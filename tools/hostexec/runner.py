"""subprocess execution for hostexec (spec status/L1-backlog.spec-host-shell.md
section 4 item 3): `shell=False` always, a fixed environment (PATH from the
policy, no inherited LD_*), a new process group so a timeout kills the
whole group (not just the direct child), and a bounded output capture.

Remote hosts go through `ssh -o BatchMode=yes -T <alias> -- <argv, quoted>`:
the argv was already decided (policy.decide()) BEFORE this quoting step, and
shlex.join() quotes each element so the remote shell reconstructs the same
argv array -- a shell metacharacter inside one argv element arrives as one
literal argument, never as a second command (review F16/F20: no sudo, no
interactive password prompt -- BatchMode=yes fails closed instead of
hanging on one)."""
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
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)  # own process group: proc.pid == pgid

    q: "queue.Queue[object]" = queue.Queue()

    def _pump() -> None:
        total = 0
        truncated = False
        try:
            while True:
                if total >= output_cap:
                    truncated = True
                    break
                chunk = proc.stdout.read(min(65536, output_cap - total))
                if not chunk:
                    break
                q.put(chunk)
                total += len(chunk)
        except (OSError, ValueError):
            pass
        finally:
            q.put(("__EOF__", truncated, total))

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

    reader.join(timeout=5)
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


def run_remote(argv: Sequence[str], *, alias: str, cwd: str, path_dirs: Sequence[str],
               timeout: float = 30.0, output_cap: int = 65536,
               base_env: dict | None = None) -> RunResult:
    """`ssh -o BatchMode=yes -T <alias> -- <shlex.join(argv)>`. `ssh` is
    resolved on `path_dirs` like any other command (tests point it at a
    fake stub)."""
    remote_cmd = shlex.join(list(argv))
    ssh_argv = ["ssh", "-o", "BatchMode=yes", "-T", alias, "--", remote_cmd]
    return _exec(ssh_argv, cwd=cwd, path_dirs=path_dirs, timeout=timeout,
                 output_cap=output_cap, base_env=base_env)
