#!/usr/bin/env python3
"""MCP server (stdio) over tools/autoos-agent.py: spawn agents from any MCP client.

Tools: list_clients, spawn, status, result, cancel. spawn is asynchronous: it
validates the request (card -> combo through autoos_routing.select_combo, the
same function the CLI uses; the depth budget; client rules), starts a
detached runner and returns a run id at once. Each run lives in
<repo>/logs/agents/<id>/ (git-ignored; AUTOOS_STATE_DIR overrides <repo>/logs):

    job.json     the request, the autoos-agent.py argv, pid, route, start time
    output.log   the child's stdout + stderr (never contains a key)
    exit.json    {rc, ended} once the child exits; {"cancelled": true} on cancel

Start it the way the registrations do (the `mcp` pin lives in
catalog/agent-harness.json):

    uv --quiet run --no-project --with 'mcp<2' python tools/autoos_agent_mcp.py

AUTOOS_AGENT_MCP_DRY_RUN=1 turns every spawn into a dry run (the suite uses it).
The server inherits the caller's AUTOOS_AGENT_DEPTH, so an agent that spawns
through it is policed like one that runs the CLI.
"""
from __future__ import annotations

import datetime
import io
import json
import os
import secrets
import signal
import subprocess
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(TOOLS_DIR, "autoos-agent.py")
sys.path.insert(0, TOOLS_DIR)
import autoos_clients as clients  # noqa: E402
import autoos_routing as routing  # noqa: E402

TAIL_CHARS = 6000
_CHILDREN = {}  # pid -> Popen of runners this server started; poll() reaps them


def state_root() -> str:
    return os.path.join(clients.state_dir(), "agents")


def _read_json(path: str):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _run_dir(run_id: str) -> str:
    if not run_id or os.sep in run_id or run_id.startswith(".") or (os.altsep and os.altsep in run_id):
        raise ValueError("bad run id %r" % run_id)
    path = os.path.join(state_root(), run_id)
    if not os.path.isfile(os.path.join(path, "job.json")):
        raise ValueError("no run %r (see status() for the list)" % run_id)
    return path


def list_clients() -> dict:
    """The client matrix, the card fields and this caller's depth budget."""
    import shutil
    try:
        depth = "a spawn from here runs at depth %d of %d" % clients.child_depth(os.environ)
    except clients.DepthError as exc:
        depth = str(exc)
    rows = []
    for c in clients.CLIENTS.values():
        installed = shutil.which(c.binary) is not None
        ok, reason = clients.signin_state(c)
        rows.append({"name": c.name, "headless": c.headless, "gateway": c.gateway,
                     "native_subagents": c.subagents, "auth": c.auth, "promo": c.promo,
                     "installed": installed, "usable": installed and ok is not False,
                     "reason": reason if ok is False else ("" if installed else "not installed"),
                     "notes": c.notes})
    return {
        "clients": rows,
        "card": {"fields": {k: list(v) for k, v in routing.CARD_VALUES.items()},
                 "defaults": routing.CARD_DEFAULTS, "routing_version": routing.ROUTING_VERSION},
        "depth": depth,
    }


def build_argv(req: dict) -> tuple:
    """(autoos-agent.py run argv, route) for a spawn request; ValueError on a bad one."""
    client = req.get("client") or "opencode"
    if client not in clients.CLIENTS:
        raise ValueError("unknown client %r; one of %s" % (client, ", ".join(clients.CLIENTS)))
    task = (req.get("task") or "").strip()
    if not task:
        raise ValueError("task is empty")
    argv = ["run", "--client", client]
    tier = req.get("tier")
    card = req.get("card")
    if isinstance(card, str):
        card = routing.parse_card(card)
    if tier is not None and card:
        raise ValueError("pass tier or card, not both")
    if tier is not None:
        argv += ["--tier", str(int(tier))]
        route = {"combo": None, "reason": "explicit-tier"}
    else:
        card = routing.normalize(card or {})
        combo, reason = routing.select_combo(card, bool(req.get("allow_training")))
        route = {"combo": combo, "reason": reason}
        argv += ["--card", ",".join("%s=%s" % kv for kv in sorted(card.items()))]
        if client in ("opencode", "claude") and req.get("lean") is None and card["role"] == "review":
            req = dict(req, lean=True)  # reviewers do not need serena or a browser
    route["routing_version"] = routing.ROUTING_VERSION
    if req.get("max_depth") is not None:
        try:
            req = dict(req, max_depth=int(req["max_depth"]))  # JSON callers send "2"
        except (TypeError, ValueError):
            raise ValueError("max_depth must be an integer, got %r" % req["max_depth"])
    clients.child_depth(os.environ, req.get("max_depth"))  # raises DepthError past the budget
    for flag in ("allow_training", "isolate", "lean", "free", "clean", "joinable"):
        if req.get(flag):
            argv.append("--" + flag.replace("_", "-"))
    for opt in ("model", "title", "max_depth"):
        if req.get(opt) is not None:
            argv += ["--" + opt.replace("_", "-"), str(req[opt])]
    if req.get("dry_run") or os.environ.get("AUTOOS_AGENT_MCP_DRY_RUN") == "1":
        argv.append("--dry-run")
    argv.append(task)
    return argv, route


def preflight(argv: list, cwd: str):
    """The CLI's own dry run: every refusal (promo, flag clashes, route) comes back now."""
    dry = argv if "--dry-run" in argv else argv[:-1] + ["--dry-run", argv[-1]]
    r = subprocess.run([sys.executable, AGENT] + dry, cwd=cwd, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    return None if r.returncode == 0 else (r.stderr.strip() or r.stdout.strip() or "rc=%d" % r.returncode)


def _reap() -> None:
    """Collect finished runners so none lingers as a zombie, and forget them."""
    for pid, proc in list(_CHILDREN.items()):
        if proc.poll() is not None:
            del _CHILDREN[pid]


def _write_exit(path: str, data: dict) -> bool:
    """Write exit.json exactly once: the runner and cancel race for it, first one wins."""
    try:
        fd = os.open(os.path.join(path, "exit.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return True


def spawn(req: dict) -> dict:
    _reap()
    try:
        argv, route = build_argv(req)
    except (ValueError, clients.DepthError) as exc:
        return {"error": str(exc)}
    cwd = req.get("cwd") or os.getcwd()
    if not os.path.isdir(cwd):
        return {"error": "cwd %s is not a directory" % cwd}
    refused = preflight(argv, cwd)
    if refused:
        return {"error": refused}
    max_attempts = 5
    for attempt in range(max_attempts):
        run_id = "%s-%s" % (datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), secrets.token_hex(3))
        path = os.path.join(state_root(), run_id)
        try:
            os.makedirs(path)
            break
        except FileExistsError:
            if attempt == max_attempts - 1:
                return {"error": "Failed to create run directory after %d attempts" % max_attempts}
            continue
    job = {"id": run_id, "request": {k: v for k, v in req.items() if k != "task"},
           "task": req.get("task"), "argv": argv, "cwd": cwd, "route": route,
           "started": time.time()}
    _write_json(os.path.join(path, "job.json"), job)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--run-job", path],
                            cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    job["pid"] = proc.pid
    _CHILDREN[proc.pid] = proc
    _write_json(os.path.join(path, "job.json"), job)
    return {"id": run_id, "state": "running", "route": route, "dir": path}


def run_job(path: str) -> int:
    """The detached runner: one autoos-agent.py run, output and exit code on disk."""
    job = _read_json(os.path.join(path, "job.json"))
    with io.open(os.path.join(path, "output.log"), "ab") as out:
        rc = subprocess.call([sys.executable, AGENT] + job["argv"], cwd=job["cwd"],
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    _write_exit(path, {"rc": rc, "ended": time.time()})  # loses to an earlier cancel
    return rc


def _alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid in _CHILDREN:  # our own child: a finished one is a zombie until reaped
        return _CHILDREN[pid].poll() is None
    if os.name == "nt":
        # os.kill(pid, 0) on Windows is TerminateProcess, not a probe.
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _state(path: str) -> dict:
    job = _read_json(os.path.join(path, "job.json")) or {}
    ex = _read_json(os.path.join(path, "exit.json"))
    if ex is not None:
        state = "cancelled" if ex.get("cancelled") else ("done" if ex.get("rc") == 0 else "failed")
    elif not job.get("pid"):
        state = "starting"
    else:
        state = "running" if _alive(job.get("pid")) else "lost"
    out = {"id": job.get("id"), "state": state, "client": (job.get("request") or {}).get("client") or "opencode",
           "route": job.get("route"), "started": job.get("started"), "task": (job.get("task") or "")[:120]}
    if ex is not None:
        out["rc"] = ex.get("rc")
        out["secs"] = round((ex.get("ended") or time.time()) - (job.get("started") or 0))
    return out


def status(run_id: str | None = None) -> dict:
    _reap()
    if run_id:
        try:
            return _state(_run_dir(run_id))
        except ValueError as exc:
            return {"error": str(exc)}
    root = state_root()
    ids = sorted((d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "job.json"))),
                 reverse=True)[:20] if os.path.isdir(root) else []
    return {"runs": [_state(os.path.join(root, d)) for d in ids]}


def result(run_id: str, max_chars: int = TAIL_CHARS) -> dict:
    try:
        path = _run_dir(run_id)
    except ValueError as exc:
        return {"error": str(exc)}
    out = _state(path)
    try:
        with io.open(os.path.join(path, "output.log"), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    out["truncated"] = len(text) > max_chars
    out["text"] = text[-max_chars:] if out["truncated"] else text
    return out


def cancel(run_id: str) -> dict:
    try:
        path = _run_dir(run_id)
    except ValueError as exc:
        return {"error": str(exc)}
    _reap()
    st = _state(path)
    if st["state"] != "running":
        return dict(st, note="not running; nothing to cancel")
    job = _read_json(os.path.join(path, "job.json"))
    if not _write_exit(path, {"cancelled": True, "rc": None, "ended": time.time()}):
        return dict(_state(path), note="finished before the cancel landed")
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(job["pid"])], capture_output=True)
    else:
        try:
            os.killpg(int(job["pid"]), signal.SIGTERM)  # the runner leads its own session
        except OSError:
            pass
    return _state(path)


def serve() -> None:
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("autoos-agent")

    @app.tool(name="list_clients")
    def _list_clients() -> dict:
        """Agent clients this host can spawn (headless, gateway, sub-agents, auth,
        installed), the task-card fields with defaults, and your depth budget."""
        return list_clients()

    @app.tool(name="spawn")
    def _spawn(task: str, client: str = "opencode", card: dict | None = None,
               tier: int | None = None, model: str | None = None, isolate: bool = False,
               lean: bool | None = None, free: bool = False, allow_training: bool = False,
               joinable: bool = False, max_depth: int | None = None, title: str | None = None,
               cwd: str | None = None, dry_run: bool = False) -> dict:
        """Start one agent on `task` and return its run id at once (poll status/result).

        card: {role: orchestrate|implement|review, complexity: trivial|standard|hard,
        ctx: 128k|1m, privacy: public|sensitive, spend: free-ok|credit}; omitted fields
        take their defaults, an empty card is t2-worker. Or pass tier 1-3 instead of a card.
        isolate: private git clone on its own branch. lean: no serena/playwright
        (default on for role=review). Refused past the depth budget, and for
        privacy=sensitive + ctx=1m unless allow_training."""
        return spawn({"task": task, "client": client, "card": card, "tier": tier, "model": model,
                      "isolate": isolate, "lean": lean, "free": free,
                      "allow_training": allow_training, "joinable": joinable,
                      "max_depth": max_depth, "title": title, "cwd": cwd, "dry_run": dry_run})

    @app.tool(name="status")
    def _status(run_id: str | None = None) -> dict:
        """One run's state (running, done, failed, cancelled, lost), or the 20 newest runs."""
        return status(run_id)

    @app.tool(name="result")
    def _result(run_id: str, max_chars: int = TAIL_CHARS) -> dict:
        """A run's state plus its output (the tail when longer than max_chars)."""
        return result(run_id, max_chars)

    @app.tool(name="cancel")
    def _cancel(run_id: str) -> dict:
        """Stop a running agent (SIGTERM to its process group)."""
        return cancel(run_id)

    app.run()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run-job":
        sys.exit(run_job(sys.argv[2]))
    serve()
