#!/usr/bin/env python3
"""MCP server (stdio) over tools/autoos-agent.py: spawn agents from any MCP client.

Tools: list_clients, spawn, status, result, cancel, respond, route,
list_agents, context, heartbeat (spec 6.2; heartbeat: R-heartbeat-02/03,
R-pause-01, R-handoff-07; respond: the spec 9 ask-back). spawn is
asynchronous: it validates the request (card ->
combo through autoos_routing.select_combo, the same function the CLI uses;
the depth budget; client rules), starts a detached runner and returns a run
id at once. Each run lives in <repo>/logs/agents/<id>/ (git-ignored;
AUTOOS_STATE_DIR overrides <repo>/logs):

    job.json     the request, the autoos-agent.py argv, pid, route, start time
    output.log   the child's stdout + stderr (never contains a key)
    exit.json    {rc, ended} once the child exits; {"cancelled": true} on cancel
    question.json  a worker's ask-back question {"text", "asked"} - written by
                   tools/autoos-ask.py, which run_job points here through the
                   child's AUTOOS_TASK_DIR
    answer.json    the respond() answer {"text", "answered"}; the helper prints
                   it, archives the pair as qa-<n>.json (history kept) and the
                   run works on

A run's `state` uses the A2A task lifecycle (spec 6.2/9, TASK_STATES):
submitted (job.json has no pid yet) -> working -> completed | failed | canceled
(A2A spelling, one l); a spawn this server refuses answers "rejected" instead.
A working run with a question.json and no answer.json yet is input_required
(the spec 9 ask-back: a worker blocked on a decision) and carries the question
text; respond(run_id, text) answers it and the run works on.
A pid that died without writing exit.json is failed with detail "lost".
`detail` keeps the pre-A2A value (starting/running/done/cancelled/lost) so the
rename loses no information.

route, list_agents and context (spec 6.1/6.2) are resolver v2: they import
tools/autoos-agent.py as a module (its own hyphenated filename, loaded via
importlib the way the test suite's load_agent() does) and call its
route_plan_for/context_state directly, so this server and the CLI can never
disagree on a plan. Nothing is spawned for them; they only read the registry,
the git-ignored overlay/track-record and each client's own sign-in probe.

Start it the way the registrations do (the `mcp` pin lives in
catalog/agent-harness.json):

    uv --quiet run --no-project --with 'mcp<2' python tools/autoos_agent_mcp.py

AUTOOS_AGENT_MCP_DRY_RUN=1 turns every spawn into a dry run (the suite uses it).
The server inherits the caller's AUTOOS_AGENT_DEPTH, so an agent that spawns
through it is policed like one that runs the CLI.
"""
from __future__ import annotations

import datetime
import importlib.util
import io
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(TOOLS_DIR, "autoos-agent.py")
sys.path.insert(0, TOOLS_DIR)
import autoos_clients as clients  # noqa: E402
import autoos_report as report_parser  # noqa: E402
import autoos_routing as routing  # noqa: E402
from registry import resolve_leg, unavailable_now  # noqa: E402

TAIL_CHARS = 6000
_TAIL_BYTES = 65536
_CLOSING_LINES = 12  # non-blank lines at the end of output.log that may carry a QUESTION
_CHILDREN = {}  # pid -> Popen of runners this server started; poll() reaps them

# Spec 9 (docs/plans/2026-09-25-routing-v2-spec.md): the A2A task-state names
# this server reports. input_required is the ask-back question file (D2b);
# rejected is what spawn() answers a refused request with.
TASK_STATES = ("submitted", "working", "input_required", "completed", "failed",
               "canceled", "rejected")


def _load_agent_cli():
    """tools/autoos-agent.py as a module (its name has a hyphen, so import
    cannot name it directly - the same trick tests/test_autoos_spawner.py's
    load_agent() uses).

    `route`, `context` and `list_agents` (spec 6.1/6.2) all reuse this
    process's own logic - `route_plan_for`, `context_state`, ROOT,
    REGISTRY_PATH, MEASURED_OVERLAY_PATH, TRACK_RECORD and the `clients`
    module it probes - so the CLI and this server can never drift apart.
    Executing the module only defines functions/constants (its own CLI runs
    under ``if __name__ == "__main__"``), so loading it here has no side
    effect.
    """
    spec = importlib.util.spec_from_file_location("autoos_agent_cli", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = _load_agent_cli()


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


def _now_iso() -> str:
    # UTC "Z" form, the house style of autoos-agent.py and autoos_heartbeat.py.
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


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


def route_plan(card, brief: str = "", explain: bool = False) -> dict:
    """The resolver v2 route_plan for `card` (spec 6.1/6.2), str or dict.

    Calls tools/autoos-agent.py's own ``route_plan_for`` (same registry,
    overlay, track record and client probes the CLI's `route` subcommand
    reads) so the two can never compute a different plan for the same input.
    explain=True adds ``explain_text``: the per-route explain lines plus the
    final reason, joined - the same lines the CLI's --explain writes to
    stderr - without disturbing the route_plan's own ``explain``/``reason``
    keys. Never raises: a bad card, an unreadable registry/overlay or a
    measure()/plan() ValueError all come back as ``{"error": msg}``.
    """
    try:
        now = agent.parse_now(None)
        registry = agent.load_registry(agent.REGISTRY_PATH)
        overlay = agent.load_overlay(agent.MEASURED_OVERLAY_PATH)
        track_record = agent.track.load(agent.TRACK_RECORD)
        client_state = agent.measure_mod.client_state(agent.clients)
        result = agent.route_plan_for(card, brief, agent.ROOT,
                                      agent.DEFAULT_ORCHESTRATOR_MODEL, now,
                                      registry, overlay, track_record, client_state)
    except Exception as exc:  # noqa: BLE001 - an MCP tool returns errors, never raises
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    if explain:
        lines = list(result.get("explain") or [])
        lines.append(result.get("reason", ""))
        result = dict(result, explain_text="\n".join(lines))
    return result


def list_agents() -> dict:
    """Plain projection of the registry (spec 6.2): usable clients, routes and
    their legs' availability.

    ``clients``: ``[{id, installed, signed_in, reason}]`` from the same probes
    ``route``'s filters read. ``routes``: ``[{id, class, legs: [{leg,
    available}], retired}]`` - a leg is unavailable when its own
    ``unavailable_legs`` entry or its provider is unavailable now, read through
    ``registry.unavailable_now`` against the current UTC clock (the same rule
    the resolver's hard filter applies, so operator-facing availability cannot
    disagree with a plan during an ``unavailable_until`` window). A broken
    registry (bad leg, unreadable file) is ``{"error": msg}``, never a raise.
    """
    try:
        registry = agent.load_registry(agent.REGISTRY_PATH)
        state = agent.measure_mod.client_state(agent.clients)
        client_rows = []
        for client_id in registry.get("clients") or {}:
            entry = state.get(client_id) or {"installed": False, "signed_in": None,
                                             "reason": "not installed"}
            client_rows.append({"id": client_id, "installed": entry["installed"],
                                "signed_in": entry["signed_in"], "reason": entry["reason"]})
        providers = registry.get("providers") or {}
        now = datetime.datetime.now(datetime.timezone.utc)
        route_rows = []
        for route_id, route in (registry.get("routes") or {}).items():
            unavailable = route.get("unavailable_legs") or {}
            legs = []
            for leg in route.get("legs") or []:
                provider_id, _ = resolve_leg(leg, registry)
                available = not (
                    unavailable_now(unavailable.get(leg), now)
                    or unavailable_now(providers.get(provider_id), now))
                legs.append({"leg": leg, "available": available})
            route_rows.append({"id": route_id, "class": route.get("class"),
                               "legs": legs, "retired": bool(route.get("retired"))})
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}
    return {"clients": client_rows, "routes": route_rows}


def heartbeat_info(inbox: str | None = None, transcript: str | None = None,
                   repos: list | None = None, cap: int | None = None) -> dict:
    """The same json object `autoos-agent.py heartbeat --json` prints
    (R-heartbeat-02/03, R-pause-01, R-handoff-07 migrated into code):
    pause state, every repo's unpushed/dirty branches, this session's
    context fill, and the exit code the CLI would use.

    Calls this process's own ``heartbeat_state`` (loaded via ``agent``, same
    trick as ``route_plan``/``context_info``), so the CLI and this tool can
    never disagree. Read-only - see tools/autoos_heartbeat.py's own
    docstring. Never raises: a bad repo path or an unreadable transcript
    degrades to that field's own unknown/zero state inside ``heartbeat_state``
    already; this only guards against something unexpected.
    """
    try:
        data, _ = agent.heartbeat_state(inbox, transcript, repos, cap)
    except Exception as exc:  # noqa: BLE001 - an MCP tool returns errors, never raises
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    return data


def context_info(transcript: str | None = None) -> dict:
    """The same data `autoos-agent.py context` prints (spec 6.1), reused as data.

    A ``transcript`` path that cannot be read is ``{"error": msg}``; no
    transcript found or no usage record yet is a normal ``{"context":
    "unknown", "reason": ...}`` result - there is simply nothing to report,
    not a failure.
    """
    try:
        data, rc = agent.context_state(transcript, None)
    except Exception as exc:  # noqa: BLE001 - a vanished transcript is an error, not a crash
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    if data.get("context") == "unknown" and rc == 2:
        return {"error": data["reason"]}
    return data


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


def _refused(msg: str) -> dict:
    """A spawn answer that started nothing (spec 9): the error text callers
    already key on, plus state "rejected" so one keyed on state alone sees it."""
    return {"error": msg, "state": "rejected"}


def spawn(req: dict) -> dict:
    _reap()
    # R-pause-01/R-heartbeat-03: a hard stop, checked before every launch. Only
    # when the caller names an inbox - no AUTOOS_AGENT_INBOX means no check.
    inbox = os.environ.get("AUTOOS_AGENT_INBOX")
    if inbox:
        pause = agent.heartbeat.pause_state(inbox, since=agent.heartbeat.session_start(
            os.environ.get("AUTOOS_AGENT_TRANSCRIPT")))
        if pause["active"]:
            return _refused("PAUSE active (%s): %s" % (pause["at"], pause["text"]))
    try:
        argv, route = build_argv(req)
    except (ValueError, clients.DepthError) as exc:
        return _refused(str(exc))
    cwd = req.get("cwd") or os.getcwd()
    if not os.path.isdir(cwd):
        return _refused("cwd %s is not a directory" % cwd)
    refused = preflight(argv, cwd)
    if refused:
        return _refused(refused)
    max_attempts = 5
    for attempt in range(max_attempts):
        run_id = "%s-%s" % (datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), secrets.token_hex(3))
        path = os.path.join(state_root(), run_id)
        try:
            os.makedirs(path)
            break
        except FileExistsError:
            if attempt == max_attempts - 1:
                # Not a refusal: the request was fine, this server failed to start it.
                return {"error": "Failed to create run directory after %d attempts" % max_attempts,
                        "state": "failed"}
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
    return {"id": run_id, "state": "working", "route": route, "dir": path}


def _write_fallback(path: str) -> None:
    """The file fallback (K2): only when the primary channel failed - the
    worker could not ask back and put its question on stdout instead
    (_stdout_channel needs_input) - write <id>.question.md into
    AUTOOS_FALLBACK_DIR (the orchestrator points it at its RUN/work/<lane>/),
    else into the run dir. Never overwrites: the parent may have annotated it.
    Best-effort: a failed write never fails the runner."""
    channel = _stdout_channel(path)
    if not channel.get("needs_input"):
        return
    run_id = os.path.basename(os.path.normpath(path))
    target_dir = os.environ.get("AUTOOS_FALLBACK_DIR") or path
    target = os.path.join(target_dir, "%s.question.md" % run_id)
    if os.path.exists(target):
        return
    text = channel["question"] + "\n"
    if channel.get("report_line"):
        text += "report: %s\n" % channel["report_line"]
    try:
        os.makedirs(target_dir, exist_ok=True)
        tmp = "%s.tmp-%d" % (target, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except OSError:
        pass  # the run itself finished; the fallback copy is optional


def run_job(path: str) -> int:
    """The detached runner: one autoos-agent.py run, output and exit code on disk."""
    job = _read_json(os.path.join(path, "job.json"))
    with io.open(os.path.join(path, "output.log"), "ab") as out:
        # AUTOOS_TASK_DIR points the worker's ask-back helper (tools/autoos-ask.py)
        # at this run dir; the CLI forwards os.environ to the client, so the
        # worker sees it too.
        rc = subprocess.call([sys.executable, AGENT] + job["argv"], cwd=job["cwd"],
                             env=dict(os.environ, AUTOOS_TASK_DIR=path),
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    _write_exit(path, {"rc": rc, "ended": time.time()})  # loses to an earlier cancel
    _write_fallback(path)
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


def _stdout_channel(path: str, ex: dict | None = None) -> dict:
    """Parse the tail of output.log for QUESTION/REPORT blocks.

    Returns a dict with optional keys ``question`` (str) and ``report``
    (dict).  A worker that cannot use the ask-back helper prints
    ``QUESTION <worker-name>: <text>`` to stdout; it is detected here so
    ``_state()`` can report it as ``input_required`` with ``detail="ended"``
    and ``respond()`` can refuse an already-exited worker.
    """
    log_path = os.path.join(path, "output.log")
    try:
        with io.open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return {}

    result = {}

    # QUESTION <worker-name>: <text> - only among the closing lines (the
    # worker's last words plus the spawner's route/depth trailer): a
    # QUESTION-shaped line in earlier tool output is not the worker asking.
    closing = "\n".join([ln for ln in tail.splitlines() if ln.strip()][-_CLOSING_LINES:])
    m = re.findall(r"^QUESTION\s+\S+?:\s*(.+)$", closing, re.MULTILINE)
    if m:
        result["question"] = m[-1].strip()

    # REPORT block — the parser handles both "·"-joined and multi-line forms
    try:
        r = report_parser.parse_report(tail)
        if r is not None:
            result["report"] = r
    except Exception:  # noqa: BLE001 — best-effort, never crash _state()
        pass
    lines = re.findall(r"^REPORT\s.*$", tail, re.MULTILINE)
    if lines:
        result["report_line"] = lines[-1].strip()

    # The one decision (used by _state and _write_fallback): an rc-0 run
    # that asked on stdout, or reported input_required, and never went
    # through the ask-back helper (no qa-*.json) still needs an answer.
    if ex is None:
        ex = _read_json(os.path.join(path, "exit.json")) or {}
    asked = "question" in result or (result.get("report") or {}).get("status") == "input_required"
    try:
        used_ask_back = any(n.startswith("qa-") and n.endswith(".json") for n in os.listdir(path))
    except OSError:  # the run dir vanished mid-poll
        return {}
    if asked and ex.get("rc") == 0 and not ex.get("cancelled") and not used_ask_back:
        result["needs_input"] = True
        if "question" not in result:
            blockers = (result.get("report") or {}).get("blockers") or []
            result["question"] = "; ".join(blockers) or "(no question text)"
    return result


def _state(path: str) -> dict:
    # Spec 9: `state` is the A2A lifecycle name, `detail` the pre-A2A value
    # (starting/running/done/cancelled/lost) - the rename loses nothing. A pid
    # that died without writing exit.json is failed (the exit code is
    # unknowable); the old "lost" name survives in detail. Spec 9 ask-back: a
    # live run with a question.json and no answer.json yet is input_required
    # (the worker is blocked on a decision); answered or withdrawn, it is
    # working again.
    job = _read_json(os.path.join(path, "job.json")) or {}
    ex = _read_json(os.path.join(path, "exit.json"))
    question = None
    report = None
    if ex is not None:
        if ex.get("cancelled"):
            state, detail = "canceled", "cancelled"
        elif ex.get("rc") == 0:
            state, detail = "completed", "done"
        else:
            state, detail = "failed", "failed"
    elif not job.get("pid"):
        state, detail = "submitted", "starting"
    elif _alive(job.get("pid")):
        state, detail = "working", "running"
        asked = _read_json(os.path.join(path, "question.json"))
        if isinstance(asked, dict) and _read_json(os.path.join(path, "answer.json")) is None:
            state, question = "input_required", asked.get("text") or ""
    else:
        state, detail = "failed", "lost"

    # stdout channel: detect QUESTION/REPORT in output.log for workers
    # that cannot use the ask-back helper (e.g. qoder).
    if ex is not None and not ex.get("cancelled"):
        channel = _stdout_channel(path, ex)
        report = channel.get("report")
        if channel.get("needs_input"):
            state, detail, question = "input_required", "ended", channel["question"]
        elif report and report.get("status") == "failed" and state == "completed":
            state, detail = "failed", "reported-failed"

    out = {"id": job.get("id"), "state": state, "detail": detail,
           "client": (job.get("request") or {}).get("client") or "opencode",
           "route": job.get("route"), "started": job.get("started"), "task": (job.get("task") or "")[:120]}
    if question is not None:
        out["question"] = question
    if report is not None:
        out["report"] = report
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


def respond(run_id: str, text: str) -> dict:
    """Answer the question a worker asked through tools/autoos-ask.py (spec 6.2
    and spec 9 ask-back): writes answer.json into the run dir, the asking
    helper prints the text and the run returns to working. Only a run in
    input_required can be answered; empty text is an error."""
    try:
        path = _run_dir(run_id)
    except ValueError as exc:
        return {"error": str(exc)}
    if not isinstance(text, str) or not text.strip():
        return {"error": "text is empty"}
    st = _state(path)
    if st["state"] != "input_required":
        return dict(st, error="run is %s, not input_required" % st["state"])
    if st.get("detail") == "ended":
        return dict(st, error="worker exited; spawn a follow-up task that includes the answer")
    _write_json(os.path.join(path, "answer.json"), {"text": text, "answered": _now_iso()})
    return _state(path)


def cancel(run_id: str) -> dict:
    try:
        path = _run_dir(run_id)
    except ValueError as exc:
        return {"error": str(exc)}
    _reap()
    st = _state(path)
    if st["state"] not in ("working", "input_required"):
        return dict(st, note="not working; nothing to cancel")
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
        """One run's state (an A2A name: submitted, working, completed, failed,
        canceled; the pre-A2A value in `detail`), or the 20 newest runs."""
        return status(run_id)

    @app.tool(name="result")
    def _result(run_id: str, max_chars: int = TAIL_CHARS) -> dict:
        """A run's state plus its output (the tail when longer than max_chars)."""
        return result(run_id, max_chars)

    @app.tool(name="cancel")
    def _cancel(run_id: str) -> dict:
        """Stop a working or input_required agent (SIGTERM to its process
        group)."""
        return cancel(run_id)

    @app.tool(name="respond")
    def _respond(run_id: str, text: str) -> dict:
        """Answer a worker's pending question (spec 9 ask-back): a worker that
        runs tools/autoos-ask.py parks its run in input_required until this
        writes answer.json; the helper prints the text, archives the exchange
        and the run works on. Empty text is refused."""
        return respond(run_id, text)

    @app.tool(name="route")
    def _route(card: str | dict, brief: str = "", explain: bool = False) -> dict:
        """The resolver v2 route_plan for `card` (spec 6.1/6.2).

        card: a v1 or v2 task card, either `key=value,...`/JSON text (as `run
        --card` accepts) or an object, e.g. {"kind": "review", "paths":
        ["tools"]}. brief: the task text (counts toward need_tokens).
        explain=True adds "explain_text": the per-route reasoning the CLI's
        --explain prints to stderr. Real registry/track-record/client
        probes; no network, no key."""
        return route_plan(card, brief, explain)

    @app.tool(name="list_agents")
    def _list_agents() -> dict:
        """The registry's clients (installed/signed-in/reason) and routes
        (class, legs with availability, retired), spec 6.2."""
        return list_agents()

    @app.tool(name="context")
    def _context(transcript: str | None = None) -> dict:
        """This session's context fill: tokens, the model's cap and the
        percentage (spec 6.1/8.3) - the same data `autoos-agent.py context`
        prints."""
        return context_info(transcript)

    @app.tool(name="heartbeat")
    def _heartbeat(inbox: str | None = None, transcript: str | None = None,
                   repos: list[str] | None = None, cap: int | None = None) -> dict:
        """Read-only heartbeat (R-heartbeat-02/03, R-pause-01, R-handoff-07):
        PAUSE state from an inbox, every repo's unpushed/dirty branches, and
        this session's context fill - the same facts and exit code
        `autoos-agent.py heartbeat --json` reports. Never pushes, commits or
        writes anything."""
        return heartbeat_info(inbox, transcript, repos, cap)

    app.run()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run-job":
        sys.exit(run_job(sys.argv[2]))
    serve()
