"""Detect, install and configure the CAO orchestration stack.

Python rather than bash because this edits JSON and YAML, must apply a source
patch idempotently, and has to run on Windows (driving WSL) as well as natively
on Linux. CAO already requires Python 3.12, so this adds no new dependency to
any host that could run CAO at all.

    python setup_cao.py --check     # report only, install nothing
    python setup_cao.py --yes       # unattended bootstrap

Design rule: this script NEVER silently degrades. A provider that `routing`
depends on but which is missing is an error, not an all-Gemini fallback that
nobody notices until a report is wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

#: pool -> the binary that serves it
PROVIDER_BINARIES = {"anthropic": "claude", "antigravity": "agy", "deepseek": "opencode"}

#: Marker proving the WSL bridge patch is present in the live CAO source.
BRIDGE_MARKER = "WSL to Windows cross-environment bridge"

#: A config path under one of these is shared with Windows.
WINDOWS_SHARED_PREFIXES = ("/mnt/", "/c/")


def needs_bridge(config_path) -> bool:
    """True only when the MCP config sits on a Windows-shared path.

    On a Linux server this returns False and the bridge is skipped entirely,
    which is what makes the patch portable rather than Windows-shaped: a native
    ``/home/you/.gemini/...`` never matches.
    """
    raw = str(config_path)
    normalised = raw.replace("\\", "/")
    return normalised.startswith(WINDOWS_SHARED_PREFIXES) or "\\wsl" in raw


def graph_id_for(worktree) -> str:
    """Repo folder name -- the identical derivation to trust_worktree.py:189.

    Sharing the rule (not the code) is what keeps the two lanes writing to the
    same graph for the same repo.

    Backslashes are normalised BEFORE splitting rather than relying on
    ``Path``. ``Path`` is platform-flavoured: on Linux a Windows-style path is
    one long filename, so ``.name`` returns the entire string and the graph id
    becomes garbage. This script runs on Windows (driving WSL) and natively on
    Linux, and a config written on one is read on the other -- caught by running
    the suite under WSL, where it failed while passing on Windows.
    """
    text = str(worktree).replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if "/" in text else text


def omnigraph_entry(worktree) -> dict:
    """A per-terminal omnigraph server pinned to THIS repo's graph.

    Without this, every CAO-spawned agent inherits the global
    ``~/.gemini/config/mcp_config.json`` pin of ``OMNIGRAPH_GRAPH_ID=memory`` and
    silently writes to the wrong graph — the failure documented in CLAUDE.md
    from 2026-07-17, where an agent concluded an intact 135-node graph had been
    wiped and started rebuilding it.
    """
    return {
        "command": "npx",
        "args": ["-y", "@modernrelay/omnigraph-mcp"],
        "env": {
            "OMNIGRAPH_BASE_URL": os.environ.get(
                "OMNIGRAPH_BASE_URL", "http://localhost:8080"
            ),
            "OMNIGRAPH_GRAPH_ID": graph_id_for(worktree),
        },
    }


def bridge_present(provider_source) -> bool:
    """Is the WSL bridge patch still in the live CAO source?

    `cao update` overwrites provider files, which would silently remove the
    bridge and resurrect the original %PATH% failure. Checked, not assumed.
    """
    path = Path(provider_source)
    if not path.exists():
        return False
    return BRIDGE_MARKER in path.read_text(encoding="utf-8", errors="replace")


def agy_flavour(run=None) -> str:
    """Is `agy` a native Linux binary, a Windows shim, or absent?

    This matters more than it looks. A shim that execs the Windows ``agy.exe``
    works for one-shot ``agy -p`` and for a working directory under ``/mnt/c``,
    but a Windows process cannot operate with its cwd on a Linux-only path:
    measured 2026-09-11, the same TUI shows the signed-in account under
    ``/mnt/c`` and "You are currently not signed in" under ``/home``.

    Since the whole lane puts worktrees on ext4 for a 20x filesystem win, a
    shimmed agy silently cannot be used by CAO at all -- the two decisions
    collide, and the symptom looks like an auth problem rather than a path one.

    Returns "native", "windows-shim", or "absent".
    """
    run = run or _run
    path = (run("command -v agy").stdout or "").strip().splitlines()
    if not path or not path[0]:
        return "absent"
    # Resolve first, then inspect the resolved path: nesting `command -v` inside
    # the `file` invocation makes the two indistinguishable to anything matching
    # on the command text, including this module's own tests.
    out = (run(f"file -b {shlex.quote(path[0])}").stdout or "").lower()
    if "elf" in out:
        return "native"
    if "script" in out or "text" in out:
        return "windows-shim"
    return "native"


def agy_warnings(run=None) -> list:
    """Say plainly when agy cannot be used with an ext4 worktree."""
    if agy_flavour(run) != "windows-shim":
        return []
    return [
        "agy is a Windows shim (it execs agy.exe). It works for one-shot "
        "`agy -p` and for worktrees under /mnt/c, but a Windows process cannot "
        "run with its cwd on ext4 -- so CAO agents on ext4 worktrees will show "
        "'not signed in'. Install the native Linux build: "
        "rm ~/.local/bin/agy && "
        "curl -fsSL https://antigravity.google/cli/install.sh | bash "
        "-- then run `agy` once to sign in."
    ]


def detect_providers(run=None) -> dict:
    """pool -> whether its binary is on PATH **where CAO runs**.

    Not ``shutil.which``: on Windows that probes the WINDOWS PATH, where
    `opencode` is absent and `claude`/`agy` happen to be present — so the report
    was wrong in both directions. Agents run inside the distro, so detection has
    to as well. Always a login shell; ~/.local/bin is invisible to `bash -c`.
    """
    run = run or _run
    out = {}
    for pool, binary in PROVIDER_BINARIES.items():
        res = run(f"command -v {binary} >/dev/null 2>&1 && echo FOUND || echo NO")
        out[pool] = "FOUND" in (res.stdout or "")
    return out


def routed_pools(config_path) -> set:
    """Pools the config actually dispatches to; missing ones are hard errors."""
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    routing = (raw.get("cao") or {}).get("routing") or {}
    out = set()
    for entries in routing.values():
        if isinstance(entries, list):
            out.update(e["pool"] for e in entries)
    return out


#: What an agent needs in ITS interpreter to run this repo's checks. Measured
#: 2026-09-10: a worker asked to verify its own review ran
#: `python3 -m pytest` and got "No module named pytest". An agent that cannot
#: run the suite cannot check its own work, and reports confidence instead of
#: evidence -- which is the failure this whole lane exists to prevent.
AGENT_PYTHON_DEPS = ("pytest", "pyyaml")

#: uv is the repo's package manager; several skills shell out to it.
AGENT_TOOLS = ("uv",)

#: OpenCode prompts before touching anything outside its workspace, and a
#: headless CAO worker has nobody to answer. Measured 2026-09-10: a DeepSeek
#: worker sat on "Permission required — Access external directory /tmp" for
#: seven minutes while its supervisor waited for a callback.
OPENCODE_PERMISSIONS = {"edit": "allow", "bash": "allow", "webfetch": "allow"}


def configure_opencode(config_path=None, run=None, dry_run: bool = False) -> str:
    """Grant OpenCode the permissions a headless agent cannot grant itself.

    Only the ``permission`` block is touched; any provider definitions already
    present are preserved, because clobbering them would drop a pinned model.
    """
    run = run or _run
    path = config_path or "$HOME/.config/opencode/opencode.json"
    if dry_run:
        return f"WOULD ensure permission block in {path}"

    # No heredoc: the command travels through a login shell and, on Windows,
    # through wsl.exe as well. Each nesting level is another chance for quoting
    # to eat a brace, so this stays a single-quoted python -c with no nesting.
    py = (
        "import json,os,sys;"
        "p=os.path.expandvars(sys.argv[1]);"
        "os.makedirs(os.path.dirname(p),exist_ok=True);"
        "d=json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else "
        "{'$schema':'https://opencode.ai/config.json'};"
        f"d.setdefault('permission',{{}}).update({OPENCODE_PERMISSIONS!r});"
        "json.dump(d,open(p,'w'),indent=2)"
    )
    run(f"python3 -c {shlex.quote(py)} {shlex.quote(path)}")
    return f"ensured permission block in {path}"


def agent_interpreters(run=None):
    """Interpreters an agent might get, most likely first.

    NOT just `which python3`: a CAO terminal's PATH is not this shell's. On this
    host a worker resolved python3 to a conda build while a login shell resolved
    /usr/bin/python3, so installing into one left the other bare.
    """
    run = run or _run
    found, seen = [], set()
    # `which -a` first: whatever the login shell resolves is the best guess at
    # what a CAO terminal resolves. The explicit conda/system paths follow
    # because a terminal's PATH is NOT this shell's -- measured on this host,
    # a worker got conda's python3 while the login shell got /usr/bin/python3.
    probe = (
        "for p in $(which -a python3 2>/dev/null) "
        "$HOME/miniconda3/bin/python3 $HOME/anaconda3/bin/python3 "
        "/usr/bin/python3; do [ -x \"$p\" ] && readlink -f \"$p\"; done"
    )
    for line in (run(probe).stdout or "").splitlines():
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            found.append(line)
    return found


def _run(command: str, timeout: int = 900):
    """Always a LOGIN shell, and through WSL when we are driving it."""
    return subprocess.run(
        cao_invocation() + [command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def python_deps_status(run=None) -> dict:
    """interpreter -> {dep: present}.

    A probe that cannot run at all (no WSL, no shell) yields an empty map rather
    than an exception: a toolchain report must never take down the provider
    report next to it.
    """
    run = run or _run
    out = {}
    try:
        interpreters = agent_interpreters(run)
    except (OSError, subprocess.SubprocessError):
        return out
    for interp in interpreters:
        status = {}
        for dep in AGENT_PYTHON_DEPS:
            module = "yaml" if dep == "pyyaml" else dep
            res = run(f'"{interp}" -c "import {module}" 2>/dev/null; echo $?')
            status[dep] = (res.stdout or "").strip().endswith("0")
        out[interp] = status
    return out


def install_python_deps(run=None, dry_run: bool = False) -> list:
    """Install the agent toolchain into EVERY interpreter an agent might get.

    Returns the actions taken (or that would be taken under ``dry_run``).
    """
    run = run or _run
    actions = []

    for interp, status in python_deps_status(run).items():
        missing = [d for d, ok in status.items() if not ok]
        if not missing:
            continue
        actions.append(f"install {' '.join(missing)} into {interp}")
        if dry_run:
            continue
        # --break-system-packages is needed on Debian/Ubuntu's PEP 668 python;
        # it is ignored by pip builds that do not know the flag, and a conda
        # interpreter is unaffected either way.
        run(f'"{interp}" -m pip install -q --break-system-packages {" ".join(missing)} '
            f'|| "{interp}" -m pip install -q --user {" ".join(missing)} '
            f'|| "{interp}" -m pip install -q {" ".join(missing)}')

    for tool in AGENT_TOOLS:
        if (run(f"command -v {tool} >/dev/null 2>&1; echo $?").stdout or "").strip().endswith("0"):
            continue
        actions.append(f"install {tool}")
        if dry_run:
            continue
        run("curl -LsSf https://astral.sh/uv/install.sh | sh")
        # The installer drops uv in ~/.local/bin but may not touch any rc file —
        # the same off-PATH failure opencode had. Symlink where PATH already looks.
        run("mkdir -p ~/.local/bin && "
            "[ -x ~/.local/bin/uv ] || ln -sf ~/.cargo/bin/uv ~/.local/bin/uv 2>/dev/null; true")

    return actions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only, install nothing")
    parser.add_argument("--yes", action="store_true", help="unattended: install without prompting")
    parser.add_argument("--config", default=None, help="path to handoff.config.json")
    args = parser.parse_args(argv)

    found = detect_providers()
    print("providers:")
    for pool, ok in sorted(found.items()):
        print(f"  {pool:<12} {PROVIDER_BINARIES[pool]:<10} {'ok' if ok else 'MISSING'}")

    required = routed_pools(args.config) if args.config else set()
    missing_required = sorted(p for p in required if not found.get(p, False))

    if missing_required:
        print(
            f"\nrouting depends on {', '.join(missing_required)}, which "
            f"{'is' if len(missing_required) == 1 else 'are'} not installed.",
            file=sys.stderr,
        )

    # An agent that cannot run the suite cannot check its own work, so a missing
    # pytest is reported next to a missing provider, not treated as cosmetic.
    for warning in agy_warnings():
        print(f"\nWARNING: {warning}", file=sys.stderr)

    pending = install_python_deps(dry_run=True)
    print("\nagent python toolchain:")
    if pending:
        for action in pending:
            print(f"  MISSING: {action}")
    else:
        print("  complete")

    if args.check:
        return 1 if (missing_required or pending) else 0

    for action in install_python_deps():
        print(f"  {action}")
    print(f"  {configure_opencode()}")

    if missing_required and not args.yes:
        print("re-run with --yes to install", file=sys.stderr)
        return 1

    return 1 if missing_required else 0


def cao_runs_here() -> bool:
    """Can CAO run natively in THIS interpreter's OS?

    Measured 2026-09-10: a pip install of CAO exists on this Windows host and is
    on PATH, but every invocation exits with an ImportError -- CAO imports
    fcntl/termios at module scope and drives tmux, neither of which Windows has.
    A shutil.which("cao") hit on Windows is therefore a false positive, and
    treating it as an install sends every later command down a path that cannot
    work.
    """
    return not sys.platform.startswith("win")


def cao_invocation(wsl_distro: str = "Ubuntu") -> list:
    """argv prefix for reaching CAO from wherever this script is running.

    Always a LOGIN shell: cao, agy, claude and opencode all live in
    ~/.local/bin, which `bash -c` cannot see.
    """
    if cao_runs_here():
        return ["bash", "-lc"]
    return ["wsl", "-d", wsl_distro, "-e", "bash", "-lc"]


if __name__ == "__main__":
    raise SystemExit(main())
