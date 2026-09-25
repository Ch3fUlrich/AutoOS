r"""Pre-approve a git worktree so an unattended Claude Code session is not blocked.

WHY THIS EXISTS
---------------
A freshly created worktree is a directory Claude Code has never seen, so the
first session in it asks before it will do anything: *do you trust the files in
this folder?* and, when ``.mcp.json`` declares servers, *do you approve them?*
An interactive session answers once. A **background** session cannot: it goes
to the ``blocked`` state and stays there until somebody attaches. Measured on
the first real launch of ``run_handoff_sessions.ps1`` (2026-09-03): both lanes'
sessions went ``blocked`` within seconds, in two brand-new worktrees.

So the runner calls this from ``postWorktree`` before starting a session. It
records the worktree in ``~/.claude.json`` with the trust flags the main
checkout already carries, optionally enables named ``.mcp.json`` servers, and
copies the gitignored ``.claude/settings.local.json`` (MCP enablement, tokens)
into the worktree - which is exactly what an interactive session would have
after answering those prompts.

It is **idempotent** and **additive**: an entry that already exists is left
alone unless ``--force``, nothing else in the config is touched, and the file is
written atomically beside a one-per-day backup. It grants no permission a
session would not have had after the operator clicked "trust" once.

Note the MCP half: the ``~/.claude.json`` entry alone does NOT suppress the
"New MCP server found in this project" dialog (measured 2026-09-05, three lanes
blocked in three seconds). The worktree's ``.claude/settings.local.json`` with
``enableAllProjectMcpServers: true`` does, so this script always writes one -
copied from the main checkout when it has one, minimal otherwise. Keep that
file gitignored in the adopting repository.

``--lane-mcp`` closes one more scope trap. Measured 2026-09-25: the shared Serena
on ``localhost:9121`` holds a single active project, so a worktree session that
connected to it got the *main checkout's* answers - the wrong files, and silently
so. A private Serena per session costs roughly 160-200 MB and about 2 s to start,
and answered for the worktree. The file it writes is loaded with
``claude --strict-mcp-config --mcp-config <file>``, so no user-scope server with
the same name can override it (the ``CLAUDE.md`` scope trap). Servers declared in
the worktree's own ``.mcp.json`` are copied unchanged; a ``serena`` already
defined there is kept as-is rather than duplicated. The copy carries their ``env``
blocks, so keep ``.claude/lane-mcp.local.json`` gitignored like settings.local.json.

This file is part of the ``unattended-orchestration`` skill and carries no
repository-specific knowledge; reference it from ``postWorktree`` as
``'{{skillDir}}/trust_worktree.py'``.

Usage::

    python trust_worktree.py <worktree> [--repo <main checkout>] [--mcpjson NAME ...]
    python trust_worktree.py <worktree> --lane-mcp [--no-private-serena]
    python trust_worktree.py <worktree> --check      # report, change nothing
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

#: The flags a project entry carries once its trust dialog has been answered.
TRUST_FLAGS = {
    "hasTrustDialogAccepted": True,
    "hasCompletedProjectOnboarding": True,
}

#: Copied into the worktree when the main checkout has it. Gitignored in most
#: repositories, so a worktree never has it by construction.
LOCAL_SETTINGS = Path(".claude") / "settings.local.json"

#: Written by ``--lane-mcp``; consumed as
#: ``claude --strict-mcp-config --mcp-config <file>``.
LANE_MCP = Path(".claude") / "lane-mcp.local.json"

#: The private Serena's pin. The shared instance serves one project at a time, so
#: each lane runs its own; the version is pinned so lanes start within a minute of
#: each other.
SERENA_SERVER_ID = "serena"
SERENA_FROM = "serena-agent==1.7.0"

#: Per-session bookkeeping that must not be inherited by a new entry.
VOLATILE_KEYS = ("history", "lastCost", "lastAPIDuration", "lastSessionId",
                 "lastSessionMetrics", "lastModelUsage", "lastDuration",
                 "lastToolDuration", "lastStartTime")


def config_path() -> Path:
    """``~/.claude.json`` - the user-scope config the trust flags live in."""
    return Path(os.environ.get("CLAUDE_CONFIG_PATH") or (Path.home() / ".claude.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    """Atomic write, with one backup per day kept beside it."""
    backup = path.with_suffix(f".bak-{date.today():%Y%m%d}")
    if not backup.exists():
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".claude-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2)
        # Several lane children trust their worktrees at the same second and
        # os.replace on ~/.claude.json then fails with PermissionError (WinError 5)
        # while another process holds the file (measured 2026-09-10: one of three
        # lanes lost the race and its session started untrusted -> blocked).
        import random, time
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25 + random.random() * 0.5)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _find_entry(projects: dict, repo: Path) -> dict:
    """The main checkout's entry, whichever separator style it was stored under."""
    wanted = str(repo).replace("\\", "/").rstrip("/").lower()
    best: dict = {}
    for key, entry in projects.items():
        if str(key).replace("\\", "/").rstrip("/").lower() == wanted:
            # Prefer the entry that already carries the trust flag.
            if not best or (entry or {}).get("hasTrustDialogAccepted"):
                best = dict(entry or {})
    return best


def template(data: dict, repo: Path) -> dict:
    """The main checkout's entry as the shape a new one should have."""
    entry = _find_entry(data.get("projects") or {}, repo)
    for key in VOLATILE_KEYS:
        entry.pop(key, None)
    return entry


def trust(worktree: Path, repo: Path, *, mcpjson: list[str], force: bool = False,
          check: bool = False) -> tuple[bool, str]:
    """``(changed, message)`` - record ``worktree`` as trusted."""
    path = config_path()
    if not path.is_file():
        return False, f"no config at {path}"
    data = load(path)
    projects = data.setdefault("projects", {})
    key = str(worktree)
    entry = projects.get(key)
    already = bool(entry) and all(entry.get(k) == v for k, v in TRUST_FLAGS.items())
    enabled = list((entry or {}).get("enabledMcpjsonServers") or [])
    missing_mcp = [m for m in mcpjson if m not in enabled]
    if already and not missing_mcp and not force:
        return False, f"already trusted: {key}"
    if check:
        return False, f"WOULD trust: {key}" + (f" (+mcpjson {missing_mcp})" if missing_mcp else "")
    merged = template(data, repo)
    merged.update(entry or {})
    merged.update(TRUST_FLAGS)
    if mcpjson:
        merged["enabledMcpjsonServers"] = sorted(set(enabled) | set(mcpjson))
    projects[key] = merged
    save(path, data)
    return True, f"trusted: {key}" + (f" (mcpjson {sorted(set(enabled) | set(mcpjson))})" if mcpjson else "")


def copy_local_settings(worktree: Path, repo: Path, *, mcpjson: list[str],
                        check: bool = False) -> str:
    """Give the worktree a local settings file that approves its MCP servers.

    Measured 2026-09-05: a project entry in ``~/.claude.json`` carrying
    ``enabledMcpjsonServers`` does NOT suppress the "New MCP server found in
    this project" dialog - three background sessions blocked on it within
    three seconds of launch. What does suppress it is
    ``.claude/settings.local.json`` in the worktree with
    ``enableAllProjectMcpServers: true`` (and the names in
    ``enabledMcpjsonServers``). So: copy the main checkout's file when it has
    one (it also carries tokens and permission allow-lists), otherwise write a
    minimal one; either way merge the approval keys in.
    """
    src, dst = repo / LOCAL_SETTINGS, worktree / LOCAL_SETTINGS
    if check:
        return f"WOULD write {dst} (from {src if src.is_file() else 'scratch'})"
    dst.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    origin = "scratch"
    if dst.is_file():
        try:
            data = json.loads(dst.read_text(encoding="utf-8"))
            origin = "the worktree's existing file"
        except json.JSONDecodeError:
            data = {}
    elif src.is_file():
        shutil.copy2(src, dst)
        data = json.loads(dst.read_text(encoding="utf-8"))
        origin = f"a copy of {LOCAL_SETTINGS}"
    changed = False
    if data.get("enableAllProjectMcpServers") is not True:
        data["enableAllProjectMcpServers"] = True
        changed = True
    enabled = list(data.get("enabledMcpjsonServers") or [])
    for name in mcpjson:
        if name not in enabled:
            enabled.append(name)
            changed = True
    if enabled:
        data["enabledMcpjsonServers"] = enabled
    if changed or not dst.is_file():
        dst.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return f"wrote {dst} from {origin} (MCP servers approved: {enabled or 'all project servers'})"
    return f"{dst} already approves the MCP servers"


def _project_servers(worktree: Path) -> dict:
    """The worktree's own ``.mcp.json`` servers, or ``{}`` when it has none."""
    mcp_json = worktree / ".mcp.json"
    if not mcp_json.is_file():
        return {}
    # A parse error raises: a strict config silently missing the project's
    # servers would stay wrong, because an unchanged file is never rewritten.
    data = json.loads(mcp_json.read_text(encoding="utf-8"))
    return dict(data.get("mcpServers") or {})


def lane_mcp_content(worktree: Path, *, private_serena: bool = True) -> dict:
    """The ``--strict-mcp-config`` document a lane session loads.

    Every server the worktree's ``.mcp.json`` declares is copied unchanged, then -
    unless the project already defines ``serena`` - a private Serena bound to this
    worktree is added. One definition per name, so a project ``serena`` always wins.
    """
    servers = _project_servers(worktree)
    if private_serena and SERENA_SERVER_ID not in servers:
        servers[SERENA_SERVER_ID] = {
            "command": "uvx",
            "args": [
                "--from", SERENA_FROM, "serena", "start-mcp-server",
                "--context", "claude-code",
                "--project", str(worktree.resolve()),
                "--open-web-dashboard", "false",
                "--enable-gui-log-window", "false",
            ],
        }
    return {"mcpServers": servers}


def write_lane_mcp(worktree: Path, *, private_serena: bool = True,
                   check: bool = False) -> str:
    """Write the strict per-worktree MCP config; ``(changed, message)`` style.

    Loaded with ``claude --strict-mcp-config --mcp-config <path>``, so a user-scope
    server of the same name cannot override the private Serena. Written only when
    the content differs, so a second run reports ``unchanged``.
    """
    dst = worktree / LANE_MCP
    text = json.dumps(lane_mcp_content(worktree, private_serena=private_serena),
                      indent=2) + "\n"
    if dst.is_file() and dst.read_text(encoding="utf-8") == text:
        return f"unchanged: {dst}"
    if check:
        return f"WOULD write {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8", newline="\n")
    return f"wrote {dst}"


def _repo_graph_id(repo: Path) -> str:
    """The graph id the repository pins, falling back to its folder name.

    The folder name alone gets the case wrong: this checkout is `AutoOS` but its
    cluster graph is `autoos`, and an agent pinned to a graph that does not exist
    writes its memory where nobody will ever read it. `.mcp.json` is the pin the
    repo ships to every client, so it is the source of truth here too.
    """
    try:
        servers = json.loads((repo / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers") or {}
        pinned = (servers.get("omnigraph") or {}).get("env", {}).get("OMNIGRAPH_GRAPH_ID")
        if isinstance(pinned, str) and pinned.strip():
            return pinned.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return repo.name


def configure_omnigraph_env(worktree: Path, repo: Path, check: bool = False) -> str:
    """Ensure worktree .env defines OMNIGRAPH_GRAPH_ID matching the repository's pin."""
    repo_folder = _repo_graph_id(repo)
    env_file = worktree / ".env"
    repo_env = repo / ".env"

    if check:
        return f"WOULD ensure OMNIGRAPH_GRAPH_ID={repo_folder} in {env_file}"

    if not env_file.is_file() and repo_env.is_file():
        shutil.copy2(repo_env, env_file)

    if env_file.is_file():
        content = env_file.read_text(encoding="utf-8")
        if "OMNIGRAPH_GRAPH_ID" not in content:
            with env_file.open("a", encoding="utf-8", newline="\n") as fh:
                if not content.endswith("\n") and content:
                    fh.write("\n")
                fh.write(f"OMNIGRAPH_GRAPH_ID={repo_folder}\n")
            return f"configured OMNIGRAPH_GRAPH_ID={repo_folder} in {env_file}"
        return f"{env_file} already configures OMNIGRAPH_GRAPH_ID"
    else:
        env_file.write_text(f"OMNIGRAPH_GRAPH_ID={repo_folder}\n", encoding="utf-8")
        return f"created {env_file} with OMNIGRAPH_GRAPH_ID={repo_folder}"


def trust_agy(worktree: Path, repo: Path, check: bool = False) -> str:
    """Copy the repo's .gemini config into the worktree, if it has one.

    This does NOT pre-approve agy's workspace-trust dialog. Measured on
    2026-09-11 with Antigravity CLI 1.2.1: a fresh worktree still asks "Do you
    trust the contents of this project?", and answering it left no file changed
    anywhere under ~/.gemini -- so there is nothing this function could write to
    pre-empt it.

    The name is kept for symmetry with trust_claude/trust_grok, but the honest
    description is "copy config". A headless agy worker's trust prompt is
    handled reactively instead, by cao/watchdog.py, which recognises that exact
    wording and answers it. If a future agy version grows a persistable trust
    store, pre-approving here is strictly better than answering later.
    """
    src = repo / ".gemini"
    dst = worktree / ".gemini"
    if not src.is_dir():
        return ("AGY: no .gemini directory in repo to copy (note: agy's trust "
                "dialog is answered at runtime by cao/watchdog.py, not here)")
    if check:
        return f"AGY: WOULD copy {src} to {dst}"
    if not dst.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return f"AGY: copied configuration from {src} to {dst}"
    return f"AGY: {dst} already exists"


def trust_grok(worktree: Path, repo: Path, check: bool = False) -> str:
    """Pre-approve worktree for Grok CLI."""
    src = repo / ".grok"
    dst = worktree / ".grok"
    if not src.is_dir():
        return "Grok: no .grok directory in repo to copy"
    if check:
        return f"Grok: WOULD copy {src} to {dst}"
    if not dst.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return f"Grok: copied configuration from {src} to {dst}"
    return f"Grok: {dst} already exists"


def trust_codewhale(worktree: Path, repo: Path, check: bool = False) -> str:
    """Pre-approve worktree for CodeWhale CLI."""
    src = repo / ".codewhale"
    dst = worktree / ".codewhale"
    if not src.is_dir():
        return "CodeWhale: no .codewhale directory in repo to copy"
    if check:
        return f"CodeWhale: WOULD copy {src} to {dst}"
    if not dst.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return f"CodeWhale: copied configuration from {src} to {dst}"
    return f"CodeWhale: {dst} already exists"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("worktree", type=Path)
    ap.add_argument("--repo", type=Path, default=None,
                    help="the main checkout whose trust entry is the template "
                         "(default: the worktree's own git common dir parent)")
    ap.add_argument("--mcpjson", action="append", default=[],
                    help="a .mcp.json server name to enable for the worktree (repeatable)")
    ap.add_argument("--agent", choices=["claude", "agy", "grok", "codewhale", "all"], default="all",
                    help="agent CLI to pre-approve (default: all)")
    ap.add_argument("--no-env", action="store_true",
                    help="do not create or touch the worktree's .env. Use it in any repository "
                         "that resolves .env back to the main checkout: a worktree .env holding "
                         "only OMNIGRAPH_GRAPH_ID pre-empts that resolution and every service "
                         "degrades in silence (measured 2026-09-11, downstream project A).")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--lane-mcp", action="store_true",
                    help="write <worktree>/.claude/lane-mcp.local.json for "
                         "`claude --strict-mcp-config --mcp-config <file>`")
    ap.add_argument("--no-private-serena", action="store_true",
                    help="with --lane-mcp, omit the per-worktree Serena server")
    ap.add_argument("--check", action="store_true", help="report, change nothing")
    args = ap.parse_args(argv)

    worktree = args.worktree.resolve()
    if not worktree.is_dir():
        print(f"not a directory: {worktree}", file=sys.stderr)
        return 2
    repo = args.repo.resolve() if args.repo else _main_checkout(worktree)
    if repo is None:
        print("could not determine the main checkout; pass --repo", file=sys.stderr)
        return 2

    agent = args.agent.lower()
    if agent in ("all", "claude"):
        changed, msg = trust(worktree, repo, mcpjson=args.mcpjson, force=args.force, check=args.check)
        print(msg)
        print(copy_local_settings(worktree, repo, mcpjson=args.mcpjson, check=args.check))

    if agent in ("all", "agy"):
        print(trust_agy(worktree, repo, check=args.check))

    if agent in ("all", "grok"):
        print(trust_grok(worktree, repo, check=args.check))

    if agent in ("all", "codewhale"):
        print(trust_codewhale(worktree, repo, check=args.check))

    if args.lane_mcp:
        try:
            print(write_lane_mcp(worktree, private_serena=not args.no_private_serena,
                                 check=args.check))
        except (OSError, ValueError) as exc:
            print(f"cannot read {worktree / '.mcp.json'}: {exc}", file=sys.stderr)
            return 2

    if not args.no_env:
        print(configure_omnigraph_env(worktree, repo, check=args.check))
    else:
        print(f"OMNIGRAPH_GRAPH_ID: not written (--no-env); {worktree}\\.env left alone")
    return 0


def _main_checkout(worktree: Path) -> Path | None:
    """The main checkout behind a linked worktree, or the worktree itself."""
    dot_git = worktree / ".git"
    if dot_git.is_dir():
        return worktree
    if not dot_git.is_file():
        return None
    pointer = dot_git.read_text(encoding="utf-8").strip()
    if not pointer.startswith("gitdir:"):
        return None
    gitdir = Path(pointer.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = worktree / gitdir
    try:
        commondir = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    common = (gitdir / commondir).resolve()
    return common.parent if common.name == ".git" else None


if __name__ == "__main__":
    raise SystemExit(main())
