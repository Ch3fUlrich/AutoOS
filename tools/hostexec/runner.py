"""subprocess execution for hostexec (spec status/L1-backlog.spec-host-shell.md
section 4 item 3): `shell=False` always, a fixed environment (PATH from the
policy, no inherited LD_*), a new process group so a timeout kills the
whole group (not just the direct child), and a bounded output capture.

Remote hosts go through
`ssh -o BatchMode=yes -T <alias> -- "cd <cwd> && <argv, quoted>"`: the
argv was already decided (policy.decide()) BEFORE this quoting step, and
shlex.join() quotes each element so the remote shell reconstructs the same
argv array -- a shell metacharacter inside one argv element arrives as one
literal argument, never as a second command (review F16/F20: no sudo, no
interactive password prompt -- BatchMode=yes fails closed instead of
hanging on one). The `cd` prefix makes the remote command run in the same
directory the audit log records (item 3); the local ssh process runs
elsewhere (see _SSH_LOCAL_CWD)."""
from __future__ import annotations

import dataclasses
import os
import queue
import shlex
import signal
import subprocess
import threading
import time
from typing import Sequence

# Wrappers must never inherit ambient job-control/pager state, and must
# never be able to summon sudo's own askpass prompt (review F20).
_CHILD_ENV_FIXED = {
    "PAGER": "cat", "SYSTEMD_PAGER": "cat", "GIT_PAGER": "cat", "LESS": "FRX",
}
_BASE_ENV_KEEP = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ")


@dataclasses.dataclass(frozen=True)
class RunResult:
    exit_code: int | None  # None means the call timed out and was killed
    duration_ms: int
    out_bytes: int          # total bytes produced by the child, even past the cap
    output: str              # captured output, up to output_cap bytes
    truncated: bool
    timed_out: bool


def build_env(path_dirs: Sequence[str], base_env: dict | None = None) -> dict:
    base_env = os.environ if base_env is None else base_env
    env = {k: base_env[k] for k in _BASE_ENV_KEEP if k in base_env}
    env["PATH"] = os.pathsep.join(path_dirs)
    env.update(_CHILD_ENV_FIXED)
    env.pop("SUDO_ASKPASS", None)
    return env


def _exec(argv: Sequence[str], *, cwd: str, path_dirs: Sequence[str],
          timeout: float, output_cap: int, base_env: dict | None) -> RunResult:
    env = build_env(path_dirs, base_env)
    start = time.monotonic()
    proc = subprocess.Popen(list(argv), cwd=cwd, env=env, shell=False,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)  # own process group: proc.pid == pgid

    os.set_blocking(proc.stdout.fileno(), False)
    q: "queue.Queue[object]" = queue.Queue()
    stop_reading = threading.Event()

    def _pump() -> None:
        # Non-blocking reads only: this loop is GUARANTEED to notice
        # stop_reading within ~10ms, so it can never block the main
        # thread's cleanup indefinitely -- not even if a grandchild that
        # os.killpg somehow missed (an escaped/re-parented process, or a
        # bug) keeps the pipe's write end open forever. A purely blocking
        # read() here once let a killed-but-not-fully-reaped tree hang
        # run_local() for the wayward process's entire remaining lifetime
        # (caught by mutation testing: killing only the direct child, not
        # the group, made close() itself block on the reader thread's
        # in-flight read -- see the report).
        #
        # Drain to EOF even past output_cap: storing stops at the cap, but
        # reading must continue or the child blocks forever on a full pipe
        # (64 KiB on Linux) and the call is misreported as timed_out. Bytes
        # past the cap are counted (out_bytes) and discarded, so memory
        # stays bounded.
        fd = proc.stdout.fileno()
        total = 0
        stored = 0
        while not stop_reading.is_set():
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            except OSError:
                break  # fd closed/invalid: nothing more to read
            if not chunk:
                break  # real EOF: every writer closed the pipe
            total += len(chunk)
            if stored < output_cap:
                room = output_cap - stored
                q.put(chunk[:room])
                stored += min(room, len(chunk))
        q.put(("__EOF__", total > output_cap, total))

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # kills the whole group, not just proc
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    # Give the pump thread a couple of seconds to reach a NATURAL EOF (the
    # common, fast case); if the pipe still has an open writer after that
    # (killpg failed to reach everyone, or this call never times out and
    # something else is holding it), force it to give up instead of
    # blocking cleanup indefinitely.
    reader.join(timeout=2)
    stop_reading.set()
    reader.join(timeout=2)  # by now the thread is guaranteed to have exited
    try:
        proc.stdout.close()
    except OSError:
        pass
    duration_ms = int((time.monotonic() - start) * 1000)

    chunks: list[bytes] = []
    truncated = False
    out_bytes = 0
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        if isinstance(item, tuple) and item and item[0] == "__EOF__":
            truncated, out_bytes = item[1], item[2]
            continue
        chunks.append(item)  # type: ignore[arg-type]

    raw = b"".join(chunks)[:output_cap]
    exit_code = None if timed_out else proc.returncode
    return RunResult(exit_code=exit_code, duration_ms=duration_ms, out_bytes=out_bytes,
                      output=raw.decode("utf-8", "replace"), truncated=truncated,
                      timed_out=timed_out)


def run_local(argv: Sequence[str], *, cwd: str, path_dirs: Sequence[str],
              timeout: float = 30.0, output_cap: int = 65536,
              base_env: dict | None = None) -> RunResult:
    return _exec(list(argv), cwd=cwd, path_dirs=path_dirs, timeout=timeout,
                 output_cap=output_cap, base_env=base_env)


# The local `ssh` process runs here, never in `cwd`. `cwd` is the REMOTE
# working directory: `ssh alias -- cmd` would otherwise run `cmd` in the
# remote login shell's $HOME while the audit line recorded `cwd`, so the
# two would disagree (item 3). `/` always exists locally and ssh does not
# care about its own cwd (its config/keys are HOME-relative).
_SSH_LOCAL_CWD = "/"


def run_remote(argv: Sequence[str], *, alias: str, cwd: str, path_dirs: Sequence[str],
               timeout: float = 30.0, output_cap: int = 65536,
               base_env: dict | None = None) -> RunResult:
    """`ssh -o BatchMode=yes -T <alias> -- "cd <cwd> && <shlex.join(argv)>"`.

    `cwd` is the remote working directory and is enforced with a leading
    `cd` (shlex.quoted, so it cannot inject a second command) rather than
    assumed: the command then runs exactly where the audit line says it
    does. If the remote `cd` fails the `&&` keeps the wrapped command from
    running. `ssh` itself is resolved on `path_dirs` like any other command
    (tests point it at a fake stub)."""
    remote_cmd = f"cd {shlex.quote(cwd)} && {shlex.join(list(argv))}"
    ssh_argv = ["ssh", "-o", "BatchMode=yes", "-T", alias, "--", remote_cmd]
    return _exec(ssh_argv, cwd=_SSH_LOCAL_CWD, path_dirs=path_dirs, timeout=timeout,
                 output_cap=output_cap, base_env=base_env)
