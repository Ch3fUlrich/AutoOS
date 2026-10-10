#!/usr/bin/env python3
"""MCP server (stdio) over tools/autoos-agent.py: spawn agents from any MCP client.

Tools: list_clients, spawn, status, result, cancel, respond, route,
list_agents, context, heartbeat, ps, oc_status/oc_start/oc_restart (L1 lane
lifecycle, c2), l2_start/l2_status/l2_stop/l2_inbox/l2_resume (L2 phase lanes,
D-665 + AO-L2-RESUME)
(spec 6.2; heartbeat: R-heartbeat-02/03, R-pause-01, R-handoff-07; respond: the
spec 9 ask-back). Which of those a client can even LIST is a profile
(MCP_TOOL_PROFILES): a server whose environment marks it as running inside an L2
lane (`AUTOOS_AGENT_LAYER=L2`, rendered by oc_l1_render) registers the spawner's
tools - spawn, status, result, ps, list_clients, route, context, heartbeat - and
nothing else, and inside that profile a `spawn` is a tier-2 or tier-3 worker,
always in its own clone (L2_SPAWN_TIER, and the isolation profile l2 forces).
A spawn made from that profile stamps its child `AUTOOS_AGENT_LAYER=L3`, and a
server marked L3 registers the same menu WITHOUT `spawn` (profile l3, the leaf:
skill rule R-worker-06) - the mark is the spawner's, never the child's.
spawn is asynchronous: it validates the request (card ->
combo through autoos_routing.select_combo, the same function the CLI uses;
the depth budget; client rules), starts a detached runner and returns a run
id at once. Each run lives in <repo>/logs/agents/<id>/ (git-ignored;
AUTOOS_STATE_DIR overrides <repo>/logs), where <id> is the spawner's own
canonical run id (tools/autoos-agent.py mint_run_id) handed to the run as
`run --run-id`, so the run dir, logs/workers/<id>.json, the branch and the
child's AUTOOS_AGENT_RUN_ID are one string (FLEETSPEC §5.1):

    job.json     the request, the canonical run id, the autoos-agent.py argv
                 (which carries that id as --run-id), pid, route, start time, and
                 the systemd scope unit the worker was launched in — as
                 information only. SB-A3 (D-103) item C: this file sits in the
                 directory the runner exports to the worker as AUTOOS_TASK_DIR,
                 and the worker is the same uid as it, so nothing in here is a
                 channel `cancel` takes orders from. `cancel` DERIVES the scope
                 name from the run id (tools/autoos-agent.py
                 `worker_scope_unit`) and reads the fallback group record from the
                 runner-private kill store (`kill_store_dir`); a job.json that
                 names another scope, another group, or another run kills nothing
    kill/<id>.json  NOT in the run dir: <state dir>/kill/, a sibling of this
                   tree. The runner's own process group and its leader start
                   time, 0600, written by the runner alone — the record the
                   fallback group kill uses where there is no user manager
    output.log   the child's stdout + stderr (never contains a key)
    exit.json    {rc, ended, family, family_source|family_reason} once the child
                 exits; {"cancelled": true} on cancel. A `family_source` naming a
                 layer was read off a model a witness saw serve; `planned-model` is
                 the plan's own claim and satisfies no gate
    question.json  a worker's ask-back question {"text", "asked"} - written by
                   tools/autoos-ask.py, which run_job points here through the
                   child's AUTOOS_TASK_DIR
    answer.json    the respond() answer {"text", "answered"}; the helper prints
                   it, archives the pair as qa-<n>.json (history kept) and the
                   run works on

A run's `state` uses the A2A task lifecycle (spec 6.2/9, TASK_STATES):
submitted (job.json has no pid yet) -> working -> completed | failed | canceled
(A2A spelling, one l); a spawn this server refuses answers "rejected" instead,
and a cancel that delivered no kill answers "cancel-failed" (SB-A4) rather than
claiming a stop it could not make.
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
disagree on a plan. spawn calls the same module's mint_run_id for the same
reason: one id, minted in one place. Nothing is spawned for the three; they only
read the registry, the git-ignored overlay/track-record and each client's own
sign-in probe.

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
import subprocess
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(TOOLS_DIR, "autoos-agent.py")
sys.path.insert(0, TOOLS_DIR)
import autoos_clients as clients  # noqa: E402
import autoos_recovery as recovery  # noqa: E402
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
               "canceled", "cancel-failed", "rejected")


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


# --- lane lifecycle (c2 2026-10-06): oc_l1.py through MCP, so a calling agent
# never has to trial-and-error shell commands to manage a lane. The lane
# password travels in the environment variable the lane config names
# (AUTOOS_OCL1_PW); these helpers never read, print or log it - a missing one
# comes back as the exact remediation instead of a stack trace.

_LANES_CONFIG = os.path.expanduser("~/.config/autoos/oc-l1.json")
# The oc_l1 children get an ALLOWLIST env, not the caller's whole environment
# (the same hygiene test_every_non_plumbing_child_call_passes_an_env enforces
# everywhere else): only what the launcher and its canary need.
_OC_L1_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
                   "AUTOOS_OCL1_PW", "SESSION_GATEWAY_URL",
                   "AUTOOS_DAILY_GATE_FILE", "AUTOOS_HOST_NAME",
                   # the gateway PAIR: the rendered lane config references both
                   # as {env:...}, and the lane child inherits this process's
                   # environment - forward only the URL and a lane starts with no
                   # key for its model
                   "AUTOOS_OMNIROUTE_URL", "AUTOOS_OMNIROUTE_KEY")


def _oc_l1_env() -> dict:
    return {k: os.environ[k] for k in _OC_L1_ENV_KEYS if k in os.environ}


# Sonnet final REJECT 2026-10-08 finding 2: one MCP server answers an L1 and an
# L2, so an L2 could start, stop and nudge lanes - relaunch its own supervisor, or
# switch off a phase it does not own. A lane marks the layer it runs at in its own
# environment (tools/oc_l2.py marks L2; an L1 lane marks nothing) and this server
# refuses the lane-CONTROL tools when it reads L2 back. Reading a lane stays
# allowed: an L2 watching its own phase is ordinary work, and it reports upward to
# the L1 inbox instead of steering.
ENV_AGENT_LAYER = "AUTOOS_AGENT_LAYER"
# AO-L2-LAUNCH merge criterion b: the L2 report tool stamps this name as the
# lane the report came from. Set by the lane render (tools/oc_l1_render.py) into
# the MCP's own environment, so it is never a tool argument a model could point
# at a peer lane. The three-sides name agreement is pinned by
# tests/test_agent_mcp_tool_profile.py.
ENV_L2_LANE = "AUTOOS_L2_LANE"
# Where an L2 report lands; the same name the renderer/serve child export. Named
# here so `l2_report` reads it from one constant, matching ENV_L2_LANE above.
ENV_L1_INBOX = "AUTOOS_L1_INBOX"
# The tools this fence covers. It is a list, not a comment, so the suite can pin
# that the set of tools it refuses is exactly the set it tests: a lane-control
# tool added later makes that test say which one is unfenced.
LANE_CONTROL_TOOLS = ("l2_start", "l2_stop", "l2_inbox", "l2_resume",
                      "oc_start", "oc_restart")


def lane_control_fence(tool: str) -> dict | None:
    """The refusal a marked lane gets for a lane-control tool; None when this
    server is not marked at all (an L1's).

    L2SPAWN-TIER fix 1: the key is "marked", not the one spelling L2 — a leaf (L3,
    the worker an L2 spawned) owns no lane either, and it has even less business
    starting, stopping or nudging one: it has no inbox to report to and no phase
    to reconcile."""
    layer = agent.agent_layer()
    if layer not in (agent.AGENT_LAYER_L2, agent.AGENT_LAYER_LEAF):
        return None
    if layer == agent.AGENT_LAYER_LEAF:
        then = ("a leaf works on the task it was given and puts what it could not "
                "do in its own REPORT; it owns no lane and spawns nothing "
                "(R-worker-06)")
    else:
        then = ("only the L1 that owns the lanes may start, stop or nudge one; "
                "an L2 works through the spawner (%s) and reports to the L1 "
                "inbox instead" % ", ".join(SPAWNER_TOOLS))
    return {"ok": False, "refused": True, "tool": tool,
            "detail": "refused: this MCP server runs inside a marked lane (%s=%s), "
                      "%s" % (ENV_AGENT_LAYER, layer, then)}


# --- the tool profile (AO-L2-LAUNCH merge criterion 2) ----------------------
#
# `lane_control_fence` refuses the lane-control tools inside an L2, but a
# refusal is still a menu entry: opencode shows an L2 every tool it is going to
# be denied, and each one it reaches for is a turn spent reading a refusal. So
# the same marker also picks the set the server REGISTERS, at registration time:
# an L2's server lists the spawner and its read-only companions and nothing
# else - no lane control, no `cancel` of someone else's run, no `respond` to a
# question this lane did not ask.
SPAWNER_TOOLS = ("spawn", "status", "result", "ps", "list_clients", "route",
                 "context", "heartbeat")
# Every tool `build_server` can register, in the order it registers them.
MCP_TOOL_NAMES = ("list_clients", "spawn", "status", "result", "cancel",
                  "respond", "route", "list_agents", "ps", "context",
                  "oc_status", "oc_start", "oc_restart", "l2_start",
                  "l2_status", "l2_stop", "l2_inbox", "l2_resume", "heartbeat")
FULL_PROFILE = "full"
NARROWEST_PROFILE = "l2"
# L2SPAWN-TIER fix 1: the layer BELOW the L2. A spawn made from profile l2 stamps
# its child `AUTOOS_AGENT_LAYER=L3` (tools/autoos-agent.py `child_agent_layer`),
# and a leaf never spawns at all (R-worker-06) — so its menu is the L2's minus the
# one tool the leaf may not use, plus nothing. `l2_report` is not on it either:
# reporting upward into a lane inbox is the L2 lane's contract, and a leaf's report
# is the output of the run its caller started.
LEAF_PROFILE = "l3"
# AO-L2-LAUNCH merge criterion b: an L2 reports its own REPORT/DONE/BLOCKED line
# through this tool, so the profile is not merely a subset of the full menu - it
# is the spawner set PLUS one tool the full profile never registers. An L1 does
# not report through it and must not see it, which is why it is absent from
# MCP_TOOL_NAMES and present only in the narrow profile.
L2_REPORT_TOOL = "l2_report"
L2_REPORT_KINDS = ("REPORT", "DONE", "BLOCKED")
L2_REPORT_MAX = 500
MCP_TOOL_PROFILES = {FULL_PROFILE: MCP_TOOL_NAMES,
                     NARROWEST_PROFILE: SPAWNER_TOOLS + (L2_REPORT_TOOL,),
                     LEAF_PROFILE: tuple(t for t in SPAWNER_TOOLS if t != "spawn")}


def mcp_tool_profile(env=None) -> str:
    """The tool profile this process serves, named by the layer its environment
    marks: `l2` inside an L2 lane, `l3` for the leaf that lane spawned, the full
    set for anything unmarked (an L1's own session)."""
    source = os.environ if env is None else env
    layer = agent.agent_layer(source)
    if layer == agent.AGENT_LAYER_L2:
        return NARROWEST_PROFILE
    if layer == agent.AGENT_LAYER_LEAF:
        return LEAF_PROFILE
    return FULL_PROFILE


def tool_names_for(profile: str) -> tuple:
    """The tool names `profile` registers. An unknown profile gets the
    NARROWEST list: a typo in a lane config must not hand back the full menu."""
    return MCP_TOOL_PROFILES.get(profile) or MCP_TOOL_PROFILES[NARROWEST_PROFILE]


# The lowest tier an L2 lane may start a worker at, and the tiers it may start at
# all (AO-L2-LAUNCH merge criterion 3, corrected by AO-L2-PRODTEST's live run: tier
# 3 is the review-only seat, so a tier-3-only lane could never start a writer).
# L2SPAWN-TIER fix 1 moved the gate itself into tools/autoos-agent.py, because the
# CLI's `run` is the SAME spawn path from a lane's bash and two copies of the rule
# drift: what the server refuses, the last mile must refuse too. These two names
# stay here as the server's own view of them — the pin the tests and the docstrings
# read — and are the CLI's objects, not a second table.
L2_SPAWN_TIER = agent.L2_SPAWN_TIER
L2_SPAWN_TIERS = agent.L2_SPAWN_TIERS
l2_spawn_refusal = agent.l2_spawn_refusal


def leaf_spawn_refusal(env=None):
    """R-worker-06 in the menu: why a spawn made by a LEAF (profile l3, the worker
    an L2 lane started) is refused whatever it asks for. The CLI's own helper —
    one rule, both entry points, and the mark that decided it is the spawner's."""
    return agent.leaf_spawn_refusal(env)


_LANE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_OC_EXIT_HINT = {
    0: "ok",
    2: "config or password error: set the lane's password env (AUTOOS_OCL1_PW) in this session's environment, then retry",
    4: "server did not become healthy in time: retry once; if it repeats, check the gateway and the throttle proxy ports",
    5: "UNATTENDED-REFUSED: the canary was not denied, so the lane did not start - do NOT run the lane unattended; fix the bash-guard plugin, delete the state file under ~/fleet/<lane>/state/, and start again, then supervise until the canary says denied=yes",
}


def _oc_l1_args(lane: str, subcommand: str) -> list:
    """oc_l1.py's argparse takes the SUBCOMMAND first: `oc_l1.py status --name
    <lane>` - measured 2026-10-06 (the --name-first order parses the lane as
    the subcommand and dies on invalid choice)."""
    if not isinstance(lane, str) or not _LANE_NAME_RE.match(lane):
        raise ValueError("lane must match [a-z0-9][a-z0-9-]{0,31}")
    if subcommand not in ("render", "start", "status"):
        raise ValueError("unsupported oc_l1.py subcommand")
    return [sys.executable, os.path.join(TOOLS_DIR, "oc_l1.py"), subcommand, "--name", lane]


def oc_status(lane: str) -> dict:
    """Lane status through tools/oc_l1.py: live (exit 0), silent (1), dead (2)."""
    argv = _oc_l1_args(lane, "status")
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL, env=_oc_l1_env())
    combined = (r.stdout + r.stderr).strip().lower()
    if r.returncode == 2 and ("password" in combined or "not set" in combined):
        # oc_l1.py exits 2 for BOTH "dead" and "config/password error"; a
        # missing password env is the caller's fixable problem, not a dead lane.
        return {"lane": lane, "verdict": "config-error", "exit_code": 2,
                "advice": "set the lane's password env (AUTOOS_OCL1_PW) in this session's environment and retry; the value is never read here"}
    verdict = {0: "live", 1: "silent", 2: "dead"}.get(r.returncode, "error")
    out = {"lane": lane, "verdict": verdict, "exit_code": r.returncode}
    if verdict == "silent":
        out["advice"] = "wait 10 minutes; still silent: force a restart with oc_restart"
    if verdict == "dead":
        out["advice"] = "the watcher restarts it within ~2 minutes; to force it now use oc_restart"
    if r.returncode not in (0, 1, 2):
        out["detail"] = (r.stdout.strip().splitlines() or [""])[-1][:200] + (r.stderr.strip().splitlines() or [""])[-1][:200]
    return out


def oc_start(lane: str) -> dict:
    """Start a lane through tools/oc_l1.py (render -> serve -> canary -> first prompt).
    Requires the lane password in this process's environment (AUTOOS_OCL1_PW);
    it is never read, printed or logged here."""
    fence = lane_control_fence("oc_start")
    if fence is not None:
        return fence
    argv = _oc_l1_args(lane, "start")
    r = subprocess.run(argv, capture_output=True, text=True, timeout=420,
                       stdin=subprocess.DEVNULL, env=_oc_l1_env())
    out = {"lane": lane, "exit_code": r.returncode,
           "outcome": _OC_EXIT_HINT.get(r.returncode, "unknown")}
    tail = (r.stdout.strip().splitlines() or [""])[-1][:200]
    if tail:
        out["last_line"] = tail
    return out


def oc_restart(lane: str) -> dict:
    """Force-restart a lane exactly as the handoff card prescribes: kill the
    pid recorded in ~/fleet/<lane>/state/, delete that state file; the lane's
    watcher then starts it fresh within ~2 minutes (never kill by process
    name). Passwords are not touched."""
    fence = lane_control_fence("oc_restart")
    if fence is not None:
        return fence
    import glob as _glob
    if not isinstance(lane, str) or not _LANE_NAME_RE.match(lane):
        raise ValueError("lane must match [a-z0-9][a-z0-9-]{0,31}")
    state_files = sorted(_glob.glob(os.path.expanduser("~/fleet/%s/state/*.json" % lane)))
    if not state_files:
        return {"lane": lane, "action": "none", "detail": "no state file - the watcher starts the lane on its own"}
    killed, removed = [], []
    try:
        st = json.load(open(state_files[-1]))
        pid = st.get("pid")
        if isinstance(pid, int) and pid > 0:
            os.kill(pid, 15)
            killed.append(pid)
    except (OSError, ValueError):
        pass
    try:
        os.remove(state_files[-1])
        removed.append(state_files[-1])
    except OSError:
        pass
    return {"lane": lane, "action": "restarted",
            "killed_pids": killed, "removed_state": removed,
            "detail": "the lane's watcher starts it within ~2 minutes; verify with oc_status"}


# --- L2 phase lanes (D-665 AO-L2-LAUNCH, AO-L2-RESUME): l2_start / l2_status /
# l2_stop / l2_inbox / l2_resume through tools/oc_l2.py, which resolves a phase
# into a lane and hands the render-serve-canary-prompt pipeline to
# tools/oc_l1.py. The shape mirrors
# the oc_* helpers above: an allowlisted environment, the server password
# present by NAME only and never read here, and the CLI's own JSON as the
# answer - this server renders no lane and kills no pid itself.

_OC_L2_SCRIPT = os.path.join(TOOLS_DIR, "oc_l2.py")
# What oc_l2.py reads. AUTOOS_OCL1_PW is already in the L1 allowlist; the rest
# are the binary path, where the L2 reports to, the run dir whose inbox the
# L2's own lines land in, and where the lane state lives.
# AUTOOS_STATE_DIR is deliberately NOT on this list: the CLI child of an MCP tool
# gets an allowlist, not this server's whole environment. So the lane records the
# agents root it will spawn into at start (oc_l2 `l2.agents_root`) and reads its
# children from there, instead of re-deriving it from an environment that no
# longer has the variable.
_OC_L2_ENV_KEYS = _OC_L1_ENV_KEYS + ("AUTOOS_OPENCODE_BIN", "AUTOOS_L1_INBOX",
                                     "AUTOOS_RUN_DIR", "AUTOOS_OCL2_STATE_DIR",
                                     "AUTOOS_OCL2_GUARD_DIR")
# What oc_l2.py pays for. `start` and `resume` are the two that can run a full
# render-serve-canary-prompt cycle (a resume restarts the lane when its session
# is gone), the others are one HTTP round trip plus a kill wait.
_L2_TIMEOUT_S = {"start": 420, "status": 90, "stop": 90, "inbox": 90,
                 "resume": 420}


def _oc_l2_env() -> dict:
    return {k: os.environ[k] for k in _OC_L2_ENV_KEYS if k in os.environ}


def _oc_l2(sub: str, argv: list) -> dict:
    """One oc_l2.py call. It prints exactly one JSON object - the verdict -
    so the answer travels as data, not as text a caller has to re-parse."""
    r = subprocess.run([sys.executable, _OC_L2_SCRIPT, sub] + list(argv),
                       capture_output=True, text=True, timeout=_L2_TIMEOUT_S[sub],
                       stdin=subprocess.DEVNULL, env=_oc_l2_env())
    text = (r.stdout or "").strip()
    try:
        out = json.loads(text)
    except ValueError:
        out = {"ok": False,
               "detail": ((text + " " + (r.stderr or "").strip()).strip()[-200:]
                          or "oc_l2.py %s printed no JSON" % sub)}
    if not isinstance(out, dict):
        out = {"ok": False, "detail": "unexpected oc_l2.py output: %s" % text[:200]}
    out.setdefault("exit_code", r.returncode)
    return out


def l2_start(repo: str, phase: str, brief_path: str,
             combo: str = "l2-orchestrator") -> dict:
    """Start the L2 lane for one phase: lane `l2-<repo>-<checkout-tag>-<phase>`, model = the
    gateway combo, the autoos-agent spawner as its only MCP, permission.task
    denied and the bash-guard in orchestrator role. The first prompt is the
    brief's own text plus the fixed role footer."""
    fence = lane_control_fence("l2_start")
    if fence is not None:
        return fence
    return _oc_l2("start", ["--repo", str(repo), "--phase", str(phase),
                            "--brief", str(brief_path), "--combo", str(combo)])


def l2_status(lane: str) -> dict:
    """live | silent | stalled | dead | absent for a phase lane, with its
    session id, port and last canary result."""
    return _oc_l2("status", ["--lane", str(lane)])


def l2_stop(lane: str) -> dict:
    """Stop a phase lane cleanly: the recorded PID's process group, then the
    state file. An orphaned `serve` still holding the port would make the next
    start's health poll answer for the wrong server (R-coord-10), so a stop
    that cannot prove the tree dead reports orphan=true and keeps the state."""
    fence = lane_control_fence("l2_stop")
    if fence is not None:
        return fence
    return _oc_l2("stop", ["--lane", str(lane)])


def l2_inbox(lane: str, text: str) -> dict:
    """Hand a line of work to a running phase lane: append one timestamped
    record to the lane's inbox and nudge its session with the launcher's own
    prompt call. The line is kept even when the lane is not live; a lane whose
    canary never denied is refused the nudge (refused=true, exit_code 2)."""
    fence = lane_control_fence("l2_inbox")
    if fence is not None:
        return fence
    return _oc_l2("inbox", ["--lane", str(lane), "--text", str(text)])


def l2_resume(lane: str) -> dict:
    """Wake a stalled phase lane: exactly one wake prompt per stall, naming the
    child that exited or the error that ended its last turn. A lane with no live
    session is stopped and started again from its stored config (restarted=true);
    a healthy lane that merely refused the prompt (HTTP 409 busy) is left alone
    (wake_rejected=true) — a working lane is never restarted for being busy. An
    already-woken stall is a no-op until the lane moves or the wake cooldown
    passes."""
    fence = lane_control_fence("l2_resume")
    if fence is not None:
        return fence
    return _oc_l2("resume", ["--lane", str(lane)])


def _l2_report_scrub(text) -> str:
    """The body of a report forced to one printable line, capped: the report is a
    single inbox record, so a newline a caller smuggled in would forge a second
    one and any control character would confuse the reader that parses the stamp."""
    kept = [ch for ch in str(text)
            if ch == " " or (0x20 <= ord(ch) and ord(ch) != 0x7F)]
    return "".join(kept).strip()[:L2_REPORT_MAX]


def l2_report(text: str, kind: str = "REPORT", env=None, now=None) -> dict:
    """Append the L2's own REPORT/DONE/BLOCKED line to the L1 inbox: one call,
    one well-formed record - no longer a tier-3 spawn whose whole task was to
    write a line (AO-L2-LAUNCH merge criterion b).

    The lane is never an argument. It is read from this MCP's own environment
    (`AUTOOS_L2_LANE`), fixed when the lane rendered, so a report cannot name a
    lane that did not write it. Refused outside profile l2, on an empty body, an
    unknown kind, or a missing lane/inbox - a report that would mis-attribute or
    land nowhere is worse than no report. `env`/`now` are the test seams; the
    server calls this with neither, so it reads the live environment and clock.
    """
    source = os.environ if env is None else env
    if mcp_tool_profile(source) != NARROWEST_PROFILE:
        return {"ok": False, "refused": True, "tool": L2_REPORT_TOOL,
                "detail": "l2_report is an L2 tool: this server does not run "
                          "inside an L2 lane (%s=L2)" % ENV_AGENT_LAYER}
    kind = str(kind or "REPORT").strip().upper()
    if kind not in L2_REPORT_KINDS:
        return {"ok": False, "refused": True, "tool": L2_REPORT_TOOL,
                "detail": "kind must be one of %s" % ", ".join(L2_REPORT_KINDS)}
    body = _l2_report_scrub(text)
    if not body:
        return {"ok": False, "refused": True, "tool": L2_REPORT_TOOL,
                "detail": "report text is empty"}
    lane = str(source.get(ENV_L2_LANE, "")).strip()
    if not lane:
        return {"ok": False, "refused": True, "tool": L2_REPORT_TOOL,
                "detail": "no lane in this MCP's environment (%s): an unattributed "
                          "report is refused" % ENV_L2_LANE}
    inbox = source.get(ENV_L1_INBOX)
    if not inbox:
        return {"ok": False, "refused": True, "tool": L2_REPORT_TOOL,
                "detail": "no L1 inbox (%s): the report would land nowhere"
                          % ENV_L1_INBOX}
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    line = "%s %s %s: %s\n" % (stamp, lane, kind, body)
    parent = os.path.dirname(str(inbox))
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(str(inbox), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError as e:
        return {"ok": False, "refused": True, "tool": L2_REPORT_TOOL,
                "detail": "cannot open the L1 inbox %s: %s" % (inbox, e)}
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return {"ok": True, "lane": lane, "kind": kind, "inbox": str(inbox),
            "appended": line.rstrip("\n")}


def kill_store_dir() -> str:
    """The runner-private store — one implementation, in the spawner.

    SB-B merge: the process that launches the client is the process that knows its
    process group, and the fallthrough loop re-launches it, so the record has to be
    writable from inside `run_client` too. Two copies of a store is two places a
    killer's answer can come from, so this server reads and writes the spawner's.
    Its rules — the directory is not `AUTOOS_TASK_DIR`, 0700 with 0600 files, the
    decided-at-spawn fields immutable and the group replaceable, and the stated
    residual that a same-uid worker can still find it — are in
    `autoos-agent.kill_store_dir`.
    """
    return agent.kill_store_dir()


def kill_store_path(run_id: str) -> str:
    return agent.kill_store_path(run_id)


def write_kill_record(run_id: str, record: dict) -> bool:
    return agent.write_kill_record(run_id, record)


def read_kill_record(run_id: str):
    return agent.read_kill_record(run_id)


def spawn_lane(env=None):
    """AO-JOB-LANE-ID: this spawn's lane id — `AUTOOS_L2_LANE`, validated with
    recovery's own key rule so what is recorded is what `plan` reads back. An
    unset or unusable value is simply no lane: a spawn is never refused over a
    label. The kill record's copy is the one recovery trusts (R-orch-17);
    `parent_lane` stays as F1 wrote it, for `oc_l2`'s child attribution."""
    raw = (os.environ if env is None else env).get(ENV_L2_LANE)
    try:
        return recovery.check_lane_key(raw)
    except ValueError:
        return None


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
        overlay, overlay_missing_at = agent.load_measured_overlay()
        track_record = agent.track.load(agent.TRACK_RECORD)
        client_state = agent.measure_mod.client_state(agent.clients)
        result = agent.route_plan_for(card, brief, agent.ROOT,
                                      agent.DEFAULT_ORCHESTRATOR_MODEL, now,
                                      registry, overlay, track_record, client_state,
                                      overlay_missing_at=overlay_missing_at,
                                      credit_guards=agent.plan_credit_guards(registry))
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


def ps(include_ended: bool = False) -> dict:
    """Every spawned worker on this host, as the CLI's `ps --json` rows.

    The same host-wide registry the CLI writes (AUTOOS_WORKERS_DIR, else the
    main checkout's logs/workers): running and died workers by default,
    exited ones too with include_ended (the same 24 h end-time window as
    ``autoos-agent.py ps --all``, V3 ps final review).
    Returns {"workers": rows, "dir": dir}.
    """
    directory = agent.workers_dir()
    return {"workers": agent.visible_workers(directory, include_ended=include_ended),
            "dir": directory}


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


def is_write_role(req: dict) -> bool:
    """Whether this request's card writes — SB-C's rule, answered by the ONE
    predicate in the spawner (`agent.is_write_role`).

    Kept as a request adapter only: this server holds a spawn request (`card`
    under a key, and it may still be the caller's text), the spawner holds the
    rule. The two entry points used to carry their own copy of that rule and
    drifted apart on the first card dialect that disagreed — a v2 `kind` card is a
    reader to one and a writer to the other, and a tier-1 write run is exactly the
    case where "which copy answered" decides whether it edits your checkout. The
    semantics are unchanged from SB-C's: any role outside the read-only pair is a
    writer, and a card that names nothing takes the registry's default
    (`implement`), because `role=implement, complexity=hard` routes UP to tier 1
    (routing.select_combo's public-strong bucket), so the tier-1 leg is not always
    the orchestrator.
    """
    return agent.is_write_role(req.get("card"))


def worktree_of(path: str) -> str:
    """The worktree `path` sits in, realpath'd — its git toplevel, or the
    directory itself when it is not a repository (so a temp dir never compares
    equal to the caller's tree).

    Deliberately not `agent.isolate_source`: that falls back to the spawner's own
    checkout for a non-repo path, which would read "somewhere else" as "my tree"."""
    try:
        top = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True,
                             stdin=subprocess.DEVNULL).stdout.strip()
    except OSError:
        return os.path.realpath(path)
    return os.path.realpath(top or path)


def build_argv(req: dict, run_id: str | None = None,
               cwd: str | None = None) -> tuple:
    """(autoos-agent.py run argv, route) for a spawn request; ValueError on a bad one.

    `run_id` is the canonical id this server minted for the run (FLEETP0b): it
    goes to the CLI as `--run-id`, so the run dir named here and the record,
    branch and child env the CLI names are one id, not two (FLEETSPEC §5.1).
    `cwd` is where the run would start: only the tier-1 write-role rule needs it."""
    # L2SPAWN-TIER fix 1 (R-worker-06): a leaf never spawns, and this is the first
    # line of the one place a spawn becomes an argv — so nothing below it runs for a
    # leaf: not the pin check, not the budget, not a dry-run preview an agent would
    # read as "the router would have let this through".
    leaf = agent.leaf_spawn_refusal()
    if leaf is not None:
        raise ValueError(leaf)
    client = req.get("client") or "opencode"
    if client not in clients.CLIENTS:
        raise ValueError("unknown client %r; one of %s" % (client, ", ".join(clients.CLIENTS)))
    task = (req.get("task") or "").strip()
    if not task:
        raise ValueError("task is empty")
    # T2-RECORD-PIN item 5 (D-284): the banned pin is refused before anything
    # else is considered, and even for a dry run -- a preview that showed a plan
    # the real launch would refuse is a preview an agent would believe.
    for pin in (req.get("model"), req.get("free_model")):
        d284 = agent.d284_model_refusal(pin)
        if d284 is not None:
            raise ValueError(d284)
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
        run_tier, gate_card = int(tier), None
    else:
        card = routing.normalize(card or {})
        combo, reason = routing.select_combo(card, bool(req.get("allow_training")))
        route = {"combo": combo, "reason": reason}
        argv += ["--card", ",".join("%s=%s" % kv for kv in sorted(card.items()))]
        run_tier, gate_card = agent._tier_for_route(combo), card
        if client in ("opencode", "claude") and req.get("lean") is None and card["role"] == "review":
            req = dict(req, lean=True)  # reviewers do not need serena or a browser
    route["routing_version"] = routing.ROUTING_VERSION
    # D-665 (AO-L2-LAUNCH criteria 3 and b): the L2 gate. A spawn that resolves
    # below tier 2 is not a worker an L2 may start, and a Claude client or a
    # Claude model pin is a spend an L2 may not make. Checked here, at the one
    # place the tier is known after routing (a flag, or whatever combo a card
    # selected), so the caller's own `--tier` cannot talk its way past a card that
    # routes to tier 1 - and a dry run is refused too, because a preview an agent
    # would believe is the same lie as the launch. The pins travel to the ONE L2
    # helper so the tier rule and the Claude rule cannot drift apart.
    l2_refusal = l2_spawn_refusal(
        run_tier, gate_card, client=client,
        models=tuple(p for p in (req.get("model"), req.get("free_model")) if p))
    if l2_refusal is not None:
        raise ValueError(l2_refusal)
    # ...and the isolation profile l2 owes every spawn it lets past that gate,
    # whatever the caller passed: the worker edits a clone of the lane's repo,
    # never the lane's checkout. The KEYDENY3 leg below already forces a clone at
    # the spawned tiers; this one does not depend on which tiers that table holds
    # or on which client lists NO_ISOLATE_CLIENTS, because "an L2 never writes in
    # a shared tree" is the L2's rule, not a tier's.
    if mcp_tool_profile() == NARROWEST_PROFILE and not req.get("isolate"):
        req = dict(req, isolate=True)
        route["forced_isolate"] = True
        route["l2_forced_isolate"] = True
    # T2-RECORD-PIN item 4: review-only tier 3 does not run an implement task.
    # The CLI's own helper, so both entry points read one rule; a dry run only
    # previews it (the server's preflight IS a dry run), and every non-dry
    # spawn of a write task into the reviewer's seat is refused here, before a
    # run dir exists.
    if not (req.get("dry_run")
            or os.environ.get("AUTOOS_AGENT_MCP_DRY_RUN") == "1"):
        tier_refusal = agent.review_tier_write_refusal(
            run_tier, gate_card, read_only=bool(req.get("read_only")))
        if tier_refusal is not None:
            raise ValueError(tier_refusal)
    # KEYDENY3g item 2: a spawned tier never runs in the caller's checkout. The
    # verdict is the CLI's own helper (leaf_isolation_refusal) — no second rule
    # table here, so the two cannot drift. A caller that asked for no isolation
    # is not refused but *forced*, because its caller is a headless agent that
    # cannot fix a flag interactively, and the alternative is a job that reports
    # as started and then exits 2.
    if not req.get("isolate") and agent.leaf_isolation_refusal(
            run_tier, False, client, leaf=agent.role_is_leaf(run_tier, gate_card)):
        # SB-B merge: the tier-1 write-role leg is NOT passed through this call.
        # `card=` would force isolation here and main's SB-C block below would
        # then see an isolated request and never record its own `default_isolate`,
        # and a request that asked to stand in the caller's worktree would never
        # reach its refusal. The two legs cannot drift on WHO is a writer, because
        # both read the one `agent.is_write_role`; this call keeps its own job —
        # the spawned tiers and the leaf roles — and the write-role leg below owns
        # the tier-1 answer, including the refusal.
        req = dict(req, isolate=True)
        route["forced_isolate"] = True
    # SB-C item 1 (SPAWNISO): KEYDENY3 keys on the spawned tiers and the leaf
    # flag, so the tier-1 WRITE role was still allowed to stand in the caller's
    # own checkout — and it edits the one tree that holds the git-ignored key
    # files (configuration/api-keys.yml, .env*). So a write role gets a clone by
    # default, and one that explicitly asked not to is refused while it points at
    # the caller's own worktree. The refusal, not a silent force, because asking
    # for no isolation is a statement the caller may have a reason for — and the
    # override flag is where it says so.
    # SB-C2 item 3 (SPAWNISO explicit): the override flag was reachable by any
    # tier-1 caller, and a card's role is the caller's own text. Only the
    # orchestrator runs shared checkouts — leaves cannot spawn at all (KEYDENY3),
    # and a tier-1 WRITE card that names the flag is refused, so the escape
    # hatch exists only for the role that has no other tree to work in. The
    # acceptance is recorded in the run's route (shared_checkout_override).
    if req.get("allow_shared_checkout"):
        if (card or {}).get("role") != "orchestrate":
            raise ValueError(
                "allow_shared_checkout is orchestrator-only: the card's role is "
                "%r, not orchestrate. A write-role spawn gets a clone "
                "(isolate, the default); leaves cannot spawn at all (KEYDENY3), "
                "so only the tier-1 orchestrator names a shared checkout."
                % ((card or {}).get("role") or "implement"))
        route["shared_checkout_override"] = True
    if run_tier == 1 and not req.get("isolate") and is_write_role(req):
        where = cwd or req.get("cwd")
        if (req.get("isolate") is False and where
                and worktree_of(where) == worktree_of(os.getcwd())):
            raise ValueError(
                "a write-role card at tier 1 cannot run in the caller's own "
                "worktree (%s): it edits the checkout this server sits in, where "
                "the git-ignored key files live. Ask for isolate (the default "
                "when you ask for nothing); the allow_shared_checkout override "
                "is orchestrator-only (SB-C2) and never releases this." % where)
        elif req.get("isolate") is not False:
            # Nothing was said for a writer: give it a clone, as a spawned tier
            # gets one. An explicit False against ANOTHER tree is honoured —
            # this rule is about the caller's own checkout, not about where a
            # run happens to start.
            req = dict(req, isolate=True)
            route["default_isolate"] = True
    if req.get("max_depth") is not None:
        try:
            req = dict(req, max_depth=int(req["max_depth"]))  # JSON callers send "2"
        except (TypeError, ValueError):
            raise ValueError("max_depth must be an integer, got %r" % req["max_depth"])
    clients.child_depth(os.environ, req.get("max_depth"))  # raises DepthError past the budget
    for flag in ("allow_training", "isolate", "lean", "free", "clean", "joinable",
                 # T2-RECORD-PIN item 4: --read-only is the second way tier 3
                 # may say yes, so the MCP path must be able to say it too.
                 "read_only",
                 # MODEFLIP opt-out (SB-B review 2): a spawn that means the chmod
                 # names it here, and the runner records the same fact in the
                 # private record below.
                 "allow_mode_only",
                 # FAMILYFENCE item 3: a stop on the pinned model ends the run.
                 "no_fallthrough"):
        if req.get(flag):
            argv.append("--" + flag.replace("_", "-"))
    # FAMILYFENCE item 1: the excluded families are a repeated flag, and a value
    # that is not a list of names would reach the CLI as one flag per dict KEY (or
    # as the argv builder's own TypeError). Refused here, where the message can
    # still name the field the caller got wrong.
    not_family = req.get("not_family")
    if not_family is not None:
        if isinstance(not_family, str) or not isinstance(not_family, (list, tuple)) \
                or not all(isinstance(family, str) and family.strip()
                           for family in not_family):
            raise ValueError("not_family must be a list of family names (e.g. "
                             "[\"nvidia\"]), got %r" % (not_family,))
        for family in not_family:
            argv += ["--not-family", family.strip()]
    for opt in ("model", "title", "max_depth"):
        if req.get(opt) is not None:
            argv += ["--" + opt.replace("_", "-"), str(req[opt])]
    # T2-RECORD-PIN item 1: `free` and a pinned model used to reach the CLI as
    # two facts the CLI reconciled by launching the promo default. One value for
    # the plan, the record and the budget gate: the pin also goes in as
    # --free-model, so `--free --model X` is X on every surface.
    if req.get("free") and req.get("model") is not None:
        argv += ["--free-model", str(req["model"])]
    if req.get("review_of") is not None:
        # FAMILYFENCE item 2: which run's WRITER this review must not copy. The
        # family is read from that run's runner-private record by the CLI, so all
        # this path carries is the id.
        argv += ["--review-of", str(req["review_of"]).strip()]
    if run_id:
        argv += ["--run-id", run_id]
    if req.get("dry_run") or os.environ.get("AUTOOS_AGENT_MCP_DRY_RUN") == "1":
        argv.append("--dry-run")
    argv.append(task)
    return argv, route


def spawn_budget_env(req: dict) -> dict:
    """The env entries to add for the CLI processes THIS spawn starts (its
    preflight and its runner), on top of the scrubbed child env.

    `claude_reason` (CLAUDEBUDGET-d item 3) is the orchestrator's declaration for
    one spawn, so it has to reach the gate the CLI re-reads on the way in -- and
    it is materialized for these two processes only, rather than exported to the
    server where every later caller inherits it. It does not go further: the
    spawner strips every `AUTOOS_CLAUDE*` key out of the worker's own env
    (`strip_claude_env`), so a worker never holds a declaration to pass down.

    FF1 (D-106) crosses it: this returns the *delta* to hand to
    `agent.spawner_child_env(extra=...)`, never a copy of `os.environ` — the
    fence is the only place a child's environment is chosen, and a wholesale
    copy would put the server's tokens back into the child.
    """
    reason = str(req.get("claude_reason") or "").strip()
    return {agent.resolver.CLAUDE_CRITICAL_ENV: reason} if reason else {}


def preflight(argv: list, cwd: str, env: dict | None = None):
    """The CLI's own dry run: every refusal (promo, flag clashes, route) comes back now."""
    dry = argv if "--dry-run" in argv else argv[:-1] + ["--dry-run", argv[-1]]
    # FF1b item 6: a child of ours, so a chosen env — the caller's GitHub,
    # provider and cloud tokens have nothing to do with resolving a plan.
    # CLAUDEBUDGET: `env` is this spawn's declaration delta, added *after* the
    # fence filtered the server's environment.
    r = subprocess.run([sys.executable, AGENT] + dry, cwd=cwd, stdin=subprocess.DEVNULL,
                       env=agent.spawner_child_env(extra=env),
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


def recorded_family_fields(run_id: str, job: dict | None) -> dict:
    """The family fields `exit.json` answers with — AO-RUN-FAMILY-RECORD (D-807).

    The runner-private kill record wins: its `writer` is what the launch actually
    served, written by the CLI, which a worker cannot edit (R-orch-17), and the CLI
    already labelled that family by its witness — a layer name when a gateway row or
    the client's own transcript named the model, `planned-model` when nothing
    answered. Where the run never reached that point — a client that is not
    installed, a run that died before the record — the spawn-time answer in job.json's
    `request` is used, and says so with `family_source: planned-model`, because "what
    I meant to run" and "what answered" are claims of different strength: a gate
    (this repo's cross-family review, the plangraph reviewer token) accepts a served
    or client-reported source and treats this one as information only. A plan that
    resolved to no family travels as null plus its reason; neither reader invents
    one."""
    writer = (read_kill_record(run_id) or {}).get("writer") or {}
    if writer.get("family"):
        fields = {"family": writer["family"]}
        for key in ("family_source", "family_reason"):
            if writer.get(key):
                fields[key] = writer[key]
        return fields
    request = (job or {}).get("request") or {}
    if "family" not in request:
        # A run dir written before this field existed: say nothing, rather than
        # saying "unresolved", which a keeper reads as a family.
        return {}
    if request.get("family_reason"):
        return {"family": None, "family_reason": request["family_reason"]}
    return {"family": request.get("family"),
            "family_source": agent.FAMILY_SOURCE_PLANNED}


def planned_model_args(req: dict) -> dict:
    """The one reading of the spawn flags that decide WHICH model this run answers
    with, as the kwargs both callers take.

    CLAUDEBUDGET-f item 3 / T2-RECORD-PIN item 1: the budget gate prices the pin,
    not the default — a spawn that carries `free` and a model launches that model
    (the argv passes it as --free-model too), so the value the gate judges is the
    value the plan will carry. AO-RUN-FAMILY-RECORD (D-807) adds the second
    reader: the family written into job.json is resolved from that same model, so
    the price and the record cannot drift onto two different runs."""
    return dict(
        model=req.get("model"),
        card=req.get("card"),
        tier=req.get("tier"),
        free=bool(req.get("free")),
        free_model=(req.get("model") if (req.get("free") and req.get("model"))
                    else agent.DEFAULT_FREE_MODEL),
        clean=bool(req.get("clean")))


def planned_family_fields(req: dict, client: str) -> dict:
    """``family`` / ``family_source`` (or ``family`` null plus ``family_reason``)
    for the model this spawn resolves to — AO-RUN-FAMILY-RECORD (D-807).

    The run's real writer family lands in the runner-private kill record when the
    launch answers; this is the spawn-time half, written before the child exists
    so a run that dies instantly still names a family. The same `run_model_family`
    layers resolve both, so a reader that only has the run dir gets the resolved
    name rather than the word "unresolved", and a plan that names nothing gets
    null with the reason — never a guess.

    `client` is the adapter's name, which is what lets a qoder run whose model no
    registry row names resolve on the client's own default model (a qwen family),
    while a gateway run resolves from the model its route served or the family every
    leg of that route declares. Nothing has answered yet, so `witnessed_family`
    labels the result `planned-model` rather than the layer that placed it: this half
    is a plan by construction, and the layer's strength claim only means something
    about a model a witness named."""
    model, _source = agent.effective_spawn_model(client, **planned_model_args(req))
    family, family_source, reason = agent.run_model_family(model, client=client)
    return agent.witnessed_family(family, family_source, reason, False)


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
    # CLAUDEBUDGET-b item 3(e): this tool is a spawn path, and the preflight
    # below was not enough to make it a gated one. A caller that never reads the
    # CLI's exit code -- an agent that only looks at this dict -- would have seen
    # a refusal arrive as a mysterious "route refused" string instead of the
    # budget's own reason, and a future launch path that skipped preflight would
    # have skipped the policy entirely. Checked here, before any run dir exists.
    # CLAUDEBUDGET-d item 2/3: the gate reads the model the request names (or the
    # client's default, or the tier/card combo), and `claude_reason` is this
    # spawn's own declaration -- so the exception is one call wide instead of an
    # AUTOOS_CLAUDE_CRITICAL the server holds for every caller that follows.
    try:
        budget_refusal, budget_note = agent.claude_spawn_refusal(
            req.get("client") or "opencode", os.environ,
            # CLAUDEBUDGET-f item 3: the same one resolution the CLI's build_plan
            # runs -- the flags go in as flags, and `free` is priced at the promo
            # model the argv carries. `planned_model_args` is the same reading the
            # run record's family is resolved from (AO-RUN-FAMILY-RECORD).
            **planned_model_args(req),
            # CLAUDEBUDGET-d item 2/3: the gate reads the model the request names (or the
            # client's default, or the tier/card combo), and `claude_reason` is this
            # spawn's own declaration -- so the exception is one call wide instead of an
            # AUTOOS_CLAUDE_CRITICAL the server holds for every caller that follows.
            reason=req.get("claude_reason"))
    except (OSError, ValueError) as exc:
        return _refused("cannot read the Claude budget: %s" % exc)
    if budget_refusal is not None:
        return _refused(budget_refusal)
    budget_env = spawn_budget_env(req)
    client = req.get("client") or "opencode"
    cwd = req.get("cwd") or os.getcwd()
    if not os.path.isdir(cwd):
        return _refused("cwd %s is not a directory" % cwd)
    # HOSTADMISSION (lane AO-ADMISSION, 2026-10-08): the same rule the CLI's
    # cmd_run applies, and it has to be read HERE. This tool starts the detached
    # runner, not the CLI's launch path, and it preflights with the CLI's own dry
    # run - which a full host must not refuse (an operator previews a route
    # before deciding where to run it). So the one gate that speaks for the machine
    # is applied before the run dir exists, and the caller gets state "rejected"
    # with the CLI's own text instead of a runner that dies on rc 13.
    # HOSTADMISSION-OFF (fix 2): this reads THIS process's environment, and the
    # process that answers with an exit code is the runner. `AUTOOS_ADMISSION_OFF`
    # used to be scrubbed on the way down, which split the answer: admitted here,
    # refused there, and the caller read "spawned" for a run that exited 13. It is
    # forwarded to our own CLI child now (autoos-agent.`spawner_child_env`), so
    # one environment decides both halves; the scrub still stops at the CLI, so a
    # client worker cannot inherit the escape. A refusal this early is the only
    # thing that can read as "rejected" from an admission: the claim of the slot
    # is the runner's (`host_admission_claim`), not this server's.
    admission = agent.host_admission_refusal()
    if admission is not None:
        return _refused(admission)
    max_attempts = 5
    for attempt in range(max_attempts):
        # FLEETP0b (FLEETSPEC §5.1): the spawner's own mint, so this run dir, the
        # CLI's record/branch/child env and the gateway header carry one id. The
        # 6-hex tail is what separates two spawns in the same second; the retry
        # stays for the id that still collides with a live run dir. The argv is
        # built with it, so the CLI's dry run checks the id it will be started
        # with, and nothing is created before that says yes.
        run_id = agent.mint_run_id(req.get("title"), req.get("task") or "")
        try:
            argv, route = build_argv(req, run_id, cwd=cwd)
        except (ValueError, clients.DepthError) as exc:
            return _refused(str(exc))
        if budget_note is not None:
            route["claude_budget"] = budget_note
        refused = preflight(argv, cwd, budget_env)
        if refused:
            # CLAUDEBUDGET-g item A: this is also where the last-mile gate reaches
            # the MCP path. `preflight` IS the CLI's own dry run, and the CLI
            # checks the final plan with the shared `claude_plan_refusal` before it
            # prints "would run:", so a model that only the reviewer resolution or
            # the resolver's route made Claude is refused here, before this server
            # has a run dir to write -- and again in the runner, on the plan it
            # launches. The dry run is re-run per attempt because main's loop mints
            # a fresh run id and rebuilds the argv each time.
            return _refused(refused)
        path = os.path.join(state_root(), run_id)
        try:
            # Private like the spawner's workers dir: the run dir holds the task
            # brief and autoos-ask.py's question/answer pair. makedirs' mode is
            # masked by the umask and never applied to an existing parent, so
            # chmod after.
            os.makedirs(path, mode=0o700)
            if os.name != "nt":
                os.chmod(path, 0o700)
            break
        except FileExistsError:
            if attempt == max_attempts - 1:
                # Not a refusal: the request was fine, this server failed to start it.
                return {"error": "Failed to create run directory after %d attempts" % max_attempts,
                        "state": "failed"}
            continue
    job = {"id": run_id, "run_id": run_id,
           "request": dict({k: v for k, v in req.items() if k != "task"},
                          **planned_family_fields(req, client)),
           "task": req.get("task"), "argv": argv, "cwd": cwd, "route": route,
           # F1 (AO-L2-RESUME): whose child this is. The L2 lane names itself in
           # its own environment (the render sets AUTOOS_L2_LANE), so the lane
           # that started a run is recorded by identity at the top of the run
           # record — the `cwd` alone says only where it ran, and any same-cwd
           # run anyone else starts would otherwise be read as the lane's own
           # child and wake it for someone else's process.
           "parent_lane": os.environ.get(ENV_L2_LANE) or None,
           "started": time.time()}
    # AO-JOB-LANE-ID: the validated id goes in beside it, and only when it IS a
    # lane — a run started outside a lane records nothing the worker can be
    # graded on. The same value goes to the kill record below, which is the copy
    # recovery reads; this one is a label on the worker's own file.
    lane = spawn_lane()
    if lane:
        job["lane"] = lane
    _write_json(os.path.join(path, "job.json"), job)
    # SB-A4: the run's decided-at-spawn record, written before the child exists so
    # a run that dies instantly still has one, and written to the private store
    # because `mode` says whether this run's output gets graded as a review. The
    # scope name is derived from the run id, exactly as `cancel` will derive it —
    # this field is a record of what was asked for, never an order to a killer.
    try:
        scope_unit = agent.worker_scope_unit(run_id)
    except ValueError:
        scope_unit = None
    write_kill_record(run_id, {
        "mode": "review" if _review_requested(req, argv) else "write",
        "scope": scope_unit,
        "dry_run": "--dry-run" in argv,
        # MODEFLIP opt-out (SB-B review 2): decided at spawn and recorded here, in
        # the runner-private record, because the run's MODEFLIP verdict reads it.
        # job.json is the worker's own directory — an opt-out a worker could write
        # into the file it is graded from is not an opt-out, it is an escape.
        "allow_mode_only": bool(req.get("allow_mode_only")) or None,
        # AO-JOB-LANE-ID: the same lane id, in the store the worker does not own,
        # because that is the only copy `autoos_recovery` may believe (R-orch-17).
        # None is not written at all: `write_kill_record` skips it, so a run
        # outside a lane keeps a record that names no lane.
        "lane": lane})
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--run-job", path],
                            # FF1b item 6: the detached runner is a child of
                            # ours, so it gets the fence — and the runner repeats
                            # it for the CLI it starts. CLAUDEBUDGET: this spawn's
                            # declaration rides along as the delta, so it survives
                            # to the runner and the gate inside its CLI.
                            cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True,
                            # SCOPEBUS: the runner launches the worker scope,
                            # so it alone gets the user bus back.
                            env=agent.spawner_child_env(extra=budget_env,
                                                        scope_bus=True))
    job["pid"] = proc.pid
    # SB-A2 (D-103) item B: the runner leads a session of its own, so its pid IS
    # its pgid, and its leader start time is only certainly THIS process the
    # instant after the fork. `cancel` refuses to signal a group whose leader has
    # a different start time — the number the kernel recycled is someone else's.
    # SB-A3 (D-103) item C: that record goes to the private store, not to
    # job.json, because the worker owns job.json's directory and can rewrite what
    # a killer reads out of it. The runner re-records its own group in run_job.
    write_kill_record(run_id, {"pgid": proc.pid,
                               "start": agent.proc_start_time(proc.pid)})
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
    """The detached runner: one autoos-agent.py run, output and exit code on disk.

    SB-A2 (D-103) item A: where the host has a user manager, the worker is
    launched inside a transient systemd SCOPE named for its run id. `cancel`
    derives that name from the run id and stops it — a cgroup is not escapable by
    `setsid()`, and a name read from a file the worker owns is an order the worker
    can give (SB-A3, D-103 item C). The unit name is still written to job.json, as
    information for whoever reads a run dir.

    Where there is no user manager (a container, a CI runner, the test suite) the
    run starts unwrapped and `cancel` falls back to the verified process-group
    kill, taken from the private kill record written below — never from job.json.
    The residual of that fallback (a worker that leaves the group, and a same-uid
    worker that finds the kill store) is stated in
    `autoos-agent.kill_verified_groups` and `kill_store_dir`, not hidden here.
    """
    run_id = os.path.basename(os.path.normpath(path))
    job = _read_json(os.path.join(path, "job.json"))
    cmd = [sys.executable, AGENT] + job["argv"]
    env = agent.spawner_child_env(extra={"AUTOOS_TASK_DIR": path})
    try:
        # WINSHIM: the same resolution every other launch site uses, applied to
        # the CLI command before any scope wrapper goes around it.
        cmd = agent.resolve_client_executable(cmd)
    except agent.ClientMissing as exc:
        # This runner is detached: no caller is left to read a raised exception, so
        # the reason goes into the log `result` reads and the run fails with the
        # spawner's own missing-program code rather than a traceback.
        message = "autoos-agent: %s" % exc
        with io.open(os.path.join(path, "output.log"), "ab") as out:
            out.write((message + "\n").encode("utf-8", "replace"))
        _write_exit(path, dict({"rc": 3, "ended": time.time()},
                               **recorded_family_fields(run_id, job)))
        print(message, file=sys.stderr)
        return 3
    if agent.scope_supported():
        # derived from the run id, and identical to what `cancel` will derive;
        # job.json keeps it only so a human reading the dir sees the unit.
        job["scope"] = agent.scope_unit_name(run_id)
        # SCOPEBUS: systemd-run needs the user bus the scrubbed env lacks.
        cmd, env = agent.worker_scope_launch(job["scope"], cmd, env)
    else:
        job.pop("scope", None)
    job.pop("group", None)  # retired channel: SB-A3 item C
    _write_json(os.path.join(path, "job.json"), job)
    write_kill_record(run_id, agent.group_record())
    with io.open(os.path.join(path, "output.log"), "ab") as out:
        # AUTOOS_TASK_DIR points the worker's ask-back helper (tools/autoos-ask.py)
        # at this run dir; the CLI forwards its own chosen env onward, so the
        # worker sees it too.
        # FF1b item 6: this used to be `dict(os.environ, ...)`. The detached
        # runner above already got a scrubbed env, so this is the same scrub run
        # a second time rather than a copy of the caller's tokens.
        # SB-A2 (D-103) item A: AUTOOS_WORKER_PGRP is gone with the file it named.
        # The runner's group and the scope unit are recorded above, by the runner.
        rc = subprocess.call(cmd, cwd=job["cwd"], env=env,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    # loses to an earlier cancel
    _write_exit(path, dict({"rc": rc, "ended": time.time()},
                           **recorded_family_fields(run_id, job)))
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


def _read_tail(path: str, limit: int = _TAIL_BYTES) -> str:
    """The last ``limit`` bytes of the run's output.log, or ""."""
    log_path = os.path.join(path, "output.log")
    try:
        with io.open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _stdout_channel(path: str, ex: dict | None = None) -> dict:
    """Parse the tail of output.log for QUESTION/REPORT blocks.

    Returns a dict with optional keys ``question`` (str) and ``report``
    (dict).  A worker that cannot use the ask-back helper prints
    ``QUESTION <worker-name>: <text>`` to stdout; it is detected here so
    ``_state()`` can report it as ``input_required`` with ``detail="ended"``
    and ``respond()`` can refuse an already-exited worker.
    """
    tail = _read_tail(path)
    if not tail:
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


# The verdict line a review run's brief asks for, anchored at the line start so a
# sentence that merely mentions a verdict does not read as one. The decoration it
# tolerates is what a markdown-speaking reviewer wraps a real decision in (a
# heading, a bullet, bold on the word `VERDICT`); the VALUE has to be the word
# alone (see `review_verdict`).
_VERDICT_LINE_RE = re.compile(
    r"(?i)^\s*(?:#{1,6}\s*|[-*+]\s+)*(?:\*\*)?VERDICT(?:\*\*)?\s*:\s*(\S.*)$")

# SB-A2 (D-103) item D: the words a verdict is. A line that opens with one and
# then says something else (`VERDICT: ready, but the ref snapshot is never read`)
# is a reviewer *talking*, and grading it as `ready` merges the very review that
# said fix-first.
_VERDICT_WORDS = ("ready", "fix-first", "not-ready")

# The verdict scan reads the transcript up to a bound — a reviewer's verdict can
# sit a hundred KB before the closing noise, so a tail is not the deliverable.
_VERDICT_SCAN_BYTES = 2 * 1024 * 1024

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# A fence opener, at markdown's own indentation: up to 3 spaces of leading
# space and then three or more ` or ~. A closing fence is the opener's OWN
# character, at least as long, and nothing but whitespace after it (CommonMark
# — `~~~` does not close a ``` block and ' ```' does not close ' ````').
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")

# A unified-diff hunk header; the b/d line counts decide how far the hunk body
# reaches. Inside that body nothing is markdown: a fence line is a fence in
# the DIFFED FILE and a VERDICT line is that file's text, not the reviewer's
# (VERDICTFENCE-R2 (b); it is what saves the measured git-diff case without
# the unsafe rescan).
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def _verdict_value(stripped: str) -> str | None:
    """The verdict word a single transcript line states, or None.

    SB-A2 (D-103) item D, the per-line half: a `>`-quoted line is someone else's
    text, a line carrying `<` or `|` is template syntax (the brief echoed back),
    and a value that is not the bare word is a reviewer *talking* — `VERDICT:
    ready, but the ref snapshot is never read` must not grade as ready. One
    trailing punctuation mark is tolerated: `VERDICT: fix-first.` means fix-first.
    """
    if stripped.startswith(">") or "<" in stripped or "|" in stripped:
        return None
    m = _VERDICT_LINE_RE.match(stripped)
    if not m:
        return None
    value = m.group(1).strip().strip("*_` ").strip()
    if not value:
        return None
    if value.lower().replace(" ", "-").rstrip(".!?:;,*_") in _VERDICT_WORDS:
        return value
    return None


def review_verdict(text: str) -> str | None:
    """The verdict a reviewer stated in its own transcript, or None.

    SB-A (D-103) items 3 and 4 (REPORTLESS, T3REVIEW): the exit code says whether
    a process ran, and a review run's deliverable is its verdict, so the transcript
    has to be read. The LAST verdict wins — a reviewer that changed its mind said
    so.

    SB-A2 (D-103) item D tightened what counts (see `_verdict_value`). VERDICTFENCE
    R2 replaced the round-1 rescan, which the cross-family review measured forging
    three verdicts: a fence still open at the END of the text now fails CLOSED —
    the transcript was cut mid-block, and a paste's own fence lines make the
    pairing ambiguous, so nothing from the first fence marker on is trusted (the
    round-1 "re-read the tail unfenced" is exactly how `VERDICT: READY` got
    harvested out of a crashed reviewer's paste). What makes the measured git-diff
    case safe deterministically is (b): a hunk body, counted from its `@@` header,
    is the diffed file's text and toggles no fence and states no verdict; fences
    follow CommonMark (c): same character, at least as long, whitespace-only
    closer; `>`-quotes and template lines stay rejected and the last verdict
    outside fences and hunks still wins (d).

    Follow-up (R-orch-16): verdicts should come only from reviewer-owned model
    turns of a structured transcript, not from scraping this raw stdout — not
    built here.
    """
    raws = [_ANSI_RE.sub("", line) for line in (text or "").splitlines()]
    found = None               # last verdict outside fences and hunks
    found_before_fence = None  # ...and before the text's first fence marker
    fence = None               # (char, length) while a block is open
    first_marker = None
    hunk = None                # (old, new) lines left in the active hunk body
    for i, raw in enumerate(raws):
        if hunk is not None:
            old, new = hunk
            if old <= 0 and new <= 0:
                hunk = None
            elif raw.startswith(" "):
                hunk = (old - 1, new - 1)
                continue
            elif raw.startswith("-"):
                hunk = (old - 1, new)
                continue
            elif raw.startswith("+"):
                hunk = (old, new - 1)
                continue
            elif raw.startswith("\\"):
                continue  # "\ No newline at end of file" counts toward neither
            elif raw == "":
                # a blank context line that lost its single leading space to a
                # trailing-whitespace strip is still hunk content
                hunk = (old - 1, new - 1)
                continue
            else:
                hunk = None
        if fence is not None:
            m = _FENCE_CLOSE_RE.match(raw)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
            continue
        if raw.startswith(("diff --git", "index ", "---", "+++")):
            continue
        m = _HUNK_RE.match(raw)
        if m:
            hunk = (int(m.group(1) or 1), int(m.group(2) or 1))
            continue
        m = _FENCE_OPEN_RE.match(raw)
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            if first_marker is None:
                first_marker = i
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        value = _verdict_value(stripped)
        if value:
            found = value
            if first_marker is None:
                found_before_fence = value
    return found_before_fence if fence is not None else found


def _review_requested(req: dict, argv: list) -> bool:
    """True when the CALLER asked for a review — decided here, at spawn, from the
    request this server was handed and the argv it built (both shapes a caller can
    send: a card dict, or the `k=v,...` text the CLI takes).

    SB-A4 (D-103, item C) moved this out of `_state`: it used to run on the way
    back out, reading `job.json`, which lives in AUTOOS_TASK_DIR and is the
    worker's own file. A writer that rewrote its job.json to read as a tier-3
    reviewer had its `VERDICT: ready` recovered into `completed`, which is a
    decision about the run's MODE taken from the run's own writable record. The
    answer is written once into the runner-private record here and read from
    there; nothing re-decides it later.
    """
    card = req.get("card")
    if isinstance(card, str):
        try:
            card = routing.parse_card(card)
        except (ValueError, KeyError, TypeError):
            card = {}
    if isinstance(card, dict) and card.get("role") == "review":
        return True
    if isinstance(card, dict) and card.get("kind") == "review":
        return True
    if str(req.get("tier") or "") == "3":
        return True
    argv = [str(a) for a in (argv or [])]
    for i, arg in enumerate(argv[:-1]):
        following = argv[i + 1]
        if arg == "--tier" and following == "3":
            return True
        if arg == "--card" and ("role=review" in following or "kind=review" in following):
            return True
    return False


def _state(path: str) -> dict:
    # Spec 9: `state` is the A2A lifecycle name, `detail` the pre-A2A value
    # (starting/running/done/cancelled/lost) - the rename loses nothing. A pid
    # that died without writing exit.json is failed (the exit code is
    # unknowable); the old "lost" name survives in detail. Spec 9 ask-back: a
    # live run with a question.json and no answer.json yet is input_required
    # (the worker is blocked on a decision); answered or withdrawn, it is
    # working again.
    job = _read_json(os.path.join(path, "job.json")) or {}
    run_id = os.path.basename(os.path.normpath(path))
    # SB-A4: the run's own record, from the store the worker was not given. The
    # directory name is the run's identity (job.json's `id` is a copy a worker can
    # rewrite), and `mode`/`dry_run` are the runner's spawn-time decisions.
    record = read_kill_record(run_id) or {}
    ex = _read_json(os.path.join(path, "exit.json"))
    question = None
    report = None
    if ex is not None:
        if ex.get("cancel-failed"):
            # SB-A4 item 3: `cancel` marked a run canceled it had not stopped.
            state, detail = "cancel-failed", "cancel-failed"
        elif ex.get("cancelled"):
            state, detail = "canceled", "cancelled"
        elif ex.get("rc") == 0:
            state, detail = "completed", "done"
        elif ex.get("rc") == agent.EXIT_HOST_ADMISSION:
            # HOSTADMISSION-OFF (lane AO-ADMISSION fix 2): the host refused this
            # run after this server had already said yes — the machine filled in
            # the gap between the pre-check and the runner's own gate. It is a
            # refusal, the same answer `spawn()` gives when it is the one that
            # sees an empty host, so it is named "rejected" here and not "failed":
            # nothing ran, nothing was lost, and a caller that retries must retry
            # against the host's room, not against a bug.
            state, detail = "rejected", "host-admission"
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
    recovered = None
    note = None
    if ex is not None and not ex.get("cancelled"):
        channel = _stdout_channel(path, ex)
        report = channel.get("report")
        if channel.get("needs_input"):
            state, detail, question = "input_required", "ended", channel["question"]
        elif report and report.get("status") == "failed" and state == "completed":
            state, detail = "failed", "reported-failed"
        if (state != "input_required" and detail != "reported-failed"
                and record.get("mode") == "review" and not record.get("dry_run")):
            # SB-A (D-103) items 3 and 4 (REPORTLESS, T3REVIEW): the exit code
            # never saw the verdict. A reviewer that stated one and then died was
            # thrown away as incomplete; a reviewer that exited 0 having said
            # nothing was counted as done and never re-routed.
            verdict = review_verdict(_read_tail(path, _VERDICT_SCAN_BYTES))
            if verdict is not None:
                recovered = verdict
                if state == "failed" and report is None:
                    state, detail = "completed", "verdict-recovered"
                    note = ("verdict recovered, no REPORT: the reviewer died (rc %s) "
                            "after it stated its verdict, so the work is kept"
                            % ex.get("rc"))
            elif state == "completed":
                state, detail = "failed", "no-verdict"
                note = "no verdict: a review run that stated none did not review"

    out = {"id": run_id, "state": state, "detail": detail,
           "client": (job.get("request") or {}).get("client") or "opencode",
           "route": job.get("route"), "started": job.get("started"), "task": (job.get("task") or "")[:120]}
    if question is not None:
        out["question"] = question
    if record.get("writer"):
        # RUNMODEL (D-103): who actually served the run, resolved by the run
        # itself and handed over in the record the worker cannot rewrite. A
        # report about a run that omits this leaves the reader to guess.
        out["writer"] = record["writer"]
    if report is not None:
        out["report"] = report
    if recovered is not None:
        out["verdict"] = recovered
    if note is not None:
        out["note"] = note
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


def _kill_landed(report: dict) -> bool:
    """Did this cancel actually stop anything?

    SB-A4 item 3 (honest state): a scope that was already inactive was not killed
    by us, and a group whose leader start time did not match was skipped, so
    neither counts. Without one of them, `cancel` must not report `canceled`.
    """
    scope = report.get("scope") or {}
    if report.get("taskkill"):
        return True  # the nt path is a direct pid kill; nothing else to consult
    if scope.get("stopped") and scope.get("was_active"):
        return True
    return any(group.get("action") == "killed" for group in report.get("groups") or [])


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
    # SB-A4 item 1: SB-A3 refused to kill anything when the record in the run dir
    # CLAIMED to be another run. That is not immunity — job.json sits in
    # AUTOOS_TASK_DIR and the worker owns it, so one rewrite of its `id` made the
    # cancel a no-op: a run could dodge being stopped by renaming itself. Nothing
    # here reads it for a target any more: the scope is derived from the run_id
    # argument and the group comes from the runner's private record, so what
    # job.json claims about its own identity changes nothing.
    if not _write_exit(path, {"cancelled": True, "rc": None, "ended": time.time()}):
        return dict(_state(path), note="finished before the cancel landed")
    if os.name == "nt":
        # RESIDUAL, stated: Windows has no private record (write_kill_record is
        # POSIX only), so this path still reads a pid out of the worker's file.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(job["pid"])], capture_output=True)
        report = {"taskkill": True}
    else:
        # SB-A2 (D-103) items A and B, as closed by SB-A3 (item C). The scope is
        # the worker's cgroup, so stopping it reaches the client and any grandchild
        # that `setsid()`ed out of a process group. Its name is DERIVED from the
        # run id — never read from job.json, which lives in AUTOOS_TASK_DIR and is
        # the worker's own file: SB-A's pgrp.json and SB-A2's job.json `scope`/
        # `group` were both orders the cancelled process could write. The group
        # kill still runs, from the private kill record: the runner itself sits
        # outside the scope (it is the one that launched systemd-run), and its
        # leader start time is verified before anything is signalled. Whatever was
        # refused is reported back, so a run this cancel could not stop says so
        # instead of looking reaped.
        report = {}
        try:
            unit = agent.worker_scope_unit(run_id)
        except ValueError as exc:
            report["scope"] = {"unit": None, "stopped": False, "refused": True,
                               "reason": str(exc)}
        else:
            report["scope"] = agent.stop_scope(unit)
        record = read_kill_record(run_id)
        if record is None:
            report["groups"] = []
            report["kill_record"] = ("no runner kill record: only the scope can be "
                                     "stopped, an unverified pgid is never signalled")
        else:
            group = {"pgid": record.get("pgid"), "start": record.get("start")}
            report["groups"] = agent.kill_verified_groups([group])
    if _kill_landed(report):
        return dict(_state(path), cancel=report)
    # We own exit.json (the O_EXCL write above won the race), so this is ours to
    # amend: the run is marked as a cancel that delivered nothing, and a later
    # `status()` reads the same honest state.
    _write_json(os.path.join(path, "exit.json"),
                {"cancelled": True, "cancel-failed": True, "rc": None,
                 "ended": time.time()})
    return dict(_state(path), cancel=report,
                error="cancel delivered no kill: %s" % json.dumps(
                    {"scope": report.get("scope"), "groups": report.get("groups"),
                     "kill_record": report.get("kill_record")}))


def build_server(profile: str | None = None):
    """The FastMCP app, registering the tools of `profile` (None: whatever this
    process's environment names - see `mcp_tool_profile`).

    Split out of `serve` so the tool set is checkable without running a server:
    criterion 2 is about what an L2 lane can LIST, and a list kept beside the
    registration is a list that drifts.
    """
    from mcp.server.fastmcp import FastMCP

    names = tool_names_for(mcp_tool_profile() if profile is None else profile)
    app = FastMCP("autoos-agent")

    def _register(name):
        """@app.tool for the profiles that list `name`.

        A tool outside the profile is defined but never advertised, so calling
        it is a protocol error rather than a refusal the model has to read -
        and `lane_control_fence` still stands behind it, because the same
        functions are reachable from the CLI."""
        def deco(fn):
            if name in names:
                return app.tool(name=name)(fn)
            return fn
        return deco

    @_register("list_clients")
    def _list_clients() -> dict:
        """Agent clients this host can spawn (headless, gateway, sub-agents, auth,
        installed), the task-card fields with defaults, and your depth budget."""
        return list_clients()

    @_register("spawn")
    def _spawn(task: str, client: str = "opencode", card: dict | None = None,
               tier: int | None = None, model: str | None = None,
               isolate: bool | None = None,
               lean: bool | None = None, free: bool = False, allow_training: bool = False,
               joinable: bool = False, max_depth: int | None = None, title: str | None = None,
               cwd: str | None = None, dry_run: bool = False,
               allow_shared_checkout: bool = False,
               allow_mode_only: bool = False,
               not_family: list[str] | None = None,
               review_of: str | None = None,
               no_fallthrough: bool = False,
               claude_reason: str | None = None) -> dict:
        """Start one agent on `task` and return its run id at once (poll status/result).

        card: {role: orchestrate|implement|review, complexity: trivial|standard|hard,
        ctx: 128k|1m, privacy: public|sensitive, spend: free-ok}; omitted fields
        take their defaults, an empty card is l2-worker. Or pass tier 1-3 instead of a card.
        isolate: private git clone on its own branch, forked from `cwd`'s repo and
        HEAD. It is FORCED for every spawned tier (2 and 3) and for a role that
        wears a leaf (`leaf: true` in catalog/agent-harness.json — role=review or
        a trivial card), because grep/glob is fenced on the search *pattern* and
        cannot see a git-ignored key file sitting in the caller's checkout; the
        clone holds committed files only. It is the DEFAULT for a write-role card
        at tier 1 too (SB-C: `role=implement, complexity=hard` routes to tier 1),
        and asking for `isolate=False` there is refused while `cwd` is the caller's
        own worktree — only `role=orchestrate` runs in place (SB-C2:
        `allow_shared_checkout=True` is orchestrator-only too — a write-role
        card passing it is refused, and an accepted override is recorded as
        `shared_checkout_override` on the route). lean: no serena/playwright
        (default on for role=review). `allow_mode_only=True` accepts a sandbox
        whose only diff is a file mode (100644 <-> 100755) — the MODEFLIP refusal
        is what a checkout artifact looks like, so say it when the chmod IS the
        task; it is recorded in the runner's private record at spawn, never in
        the worker's job.json. Refused past the depth budget, and for
        privacy=sensitive + ctx=1m (no gateway leg serves that, and `allow_training`
        does not unlock it — routing.select_combo is explicit that the flag is
        inert there; it only waives the privacy check on an explicit --model).

        L2 SPAWN GATE (D-665, AO-L2-LAUNCH criterion 3, as AO-L2-PRODTEST's live
        run corrected it): when this server's own environment marks it as running
        inside an L2 lane (`AUTOOS_AGENT_LAYER=L2` — the same marker that lists
        only the spawner's tools), a spawn is a tier-2 or tier-3 worker, always in
        its own clone: profile l2 FORCES `isolate`, whatever the caller passed, so
        a lane's worker never edits a shared tree. Tier 1 and any `role:
        orchestrate` card are refused before a run dir exists, whatever the
        request typed: the gate reads the tier the request RESOLVES to, and tier 1
        is the L1's own seat. It used to be tier-3-only, which was a dead end —
        tier 3 is the review-only seat and refuses an implement card, so an L2
        could never start a writer. An L2 that needs tier 1 reports to the L1
        inbox and the L1 starts the tier.

        LEAF (R-worker-06, the tier below that gate): the worker an L2 spawns is
        stamped `AUTOOS_AGENT_LAYER=L3` by the spawner that started it (tools/
        autoos-agent.py `child_agent_layer`), and a server marked L3 lists no
        `spawn` at all and refuses one if it is called anyway — whatever the tier,
        whatever the card, dry run included. The mark is the spawner's, never the
        child's: a caller cannot hand its child a different layer than the one the
        spawn decided, and a leaf that needs a deeper tier puts that in its report
        instead. An L1's own worker is unmarked and keeps the menu it always had.

        claude_reason: this spawn's own Claude-budget declaration, for a `model`
        that answers with Claude (CLAUDEBUDGET-d). Set it on the one call that
        needs it rather than exporting AUTOOS_CLAUDE_CRITICAL server-wide, where
        every later caller would inherit it; it is what the returned route cites.
        A card field of the same name is not one — the card is the worker's text.

        FAMILYFENCE (a review never silently runs on the writer's model family):
        not_family: model families this spawn must never run on, e.g. ["nvidia"] —
        a list of names, each one removed from the WHOLE plan (the leg chosen up
        front and every fallthrough candidate). A review of a known writer does
        not need it: the writer's family is excluded by default.
        review_of: the run id whose WRITER this review must not copy. The family
        is read from that run's runner-private kill record, never from its
        job.json, so a worker cannot pick who reviews it by editing its own file.
        That store is per-checkout: when THIS checkout's store cannot name the
        writer's family (no record, no writer, or an unresolved one), the spawn is
        refused and this answers with the error naming the store searched — spawn
        through the MCP/checkout that spawned the writer, or drop review_of and name
        the writer's family with not_family (FAMILYFENCE-4; a not_family ALONGSIDE
        review_of does not clear that refusal — the writer stays unnamed). It is
        never a warning and an unfenced run.
        no_fallthrough: a stop on the pinned model ends the run with that rc; no
        re-plan onto another model. Off by default.
        `model` is a pin for an own-account client too (FAMILYFENCE-b): a qoder or
        claude model name reaches that CLI's own --model and its family feeds the
        fence like a gateway leg's. The record's writer gains a `source`
        (gateway-log/client-reported/pinned/assumed-default); a CROSS-FAMILY verdict
        prints `yes` only on a witnessed model, never on an assumed default, while a
        collision prints `NO`, marked `(assumed)` when nothing witnessed the model
        that hit it (FAMILYFENCE-3 N5) — the same collision the run exits 12 on."""
        return spawn({"task": task, "client": client, "card": card, "tier": tier, "model": model,
                      "isolate": isolate, "lean": lean, "free": free,
                      "allow_training": allow_training, "joinable": joinable,
                      "max_depth": max_depth, "title": title, "cwd": cwd, "dry_run": dry_run,
                      "allow_shared_checkout": allow_shared_checkout,
                      "allow_mode_only": allow_mode_only,
                      "not_family": not_family, "review_of": review_of,
                      "no_fallthrough": no_fallthrough,
                      "claude_reason": claude_reason})

    @_register("status")
    def _status(run_id: str | None = None) -> dict:
        """One run's state (an A2A name: submitted, working, completed, failed,
        canceled; the pre-A2A value in `detail`), or the 20 newest runs."""
        return status(run_id)

    @_register("result")
    def _result(run_id: str, max_chars: int = TAIL_CHARS) -> dict:
        """A run's state plus its output (the tail when longer than max_chars)."""
        return result(run_id, max_chars)

    @_register("cancel")
    def _cancel(run_id: str) -> dict:
        """Stop a working or input_required agent: it stops the systemd scope the
        worker was launched in (SIGTERM, then SIGKILL to every process in the
        cgroup) and kills the runner's process group, whose leader start time is
        verified first. Where no user manager is reachable only the verified
        group kill runs. Reports what it refused to kill. The scope unit is DERIVED
        from the run id and the group record comes from the runner's private kill
        store; job.json, which the cancelled worker owns, aims nothing. A run this
        could not stop reports the state "cancel-failed", never "canceled"."""
        return cancel(run_id)

    @_register("respond")
    def _respond(run_id: str, text: str) -> dict:
        """Answer a worker's pending question (spec 9 ask-back): a worker that
        runs tools/autoos-ask.py parks its run in input_required until this
        writes answer.json; the helper prints the text, archives the exchange
        and the run works on. Empty text is refused."""
        return respond(run_id, text)

    @_register("route")
    def _route(card: str | dict, brief: str = "", explain: bool = False) -> dict:
        """The resolver v2 route_plan for `card` (spec 6.1/6.2).

        card: a v1 or v2 task card, either `key=value,...`/JSON text (as `run
        --card` accepts) or an object, e.g. {"kind": "review", "paths":
        ["tools"]}. brief: the task text (counts toward need_tokens).
        explain=True adds "explain_text": the per-route reasoning the CLI's
        --explain prints to stderr. Real registry/track-record/client
        probes; no network, no key."""
        return route_plan(card, brief, explain)

    @_register("list_agents")
    def _list_agents() -> dict:
        """The registry's clients (installed/signed-in/reason) and routes
        (class, legs with availability, retired), spec 6.2."""
        return list_agents()

    @_register("ps")
    def _ps(include_ended: bool = False) -> dict:
        """Every spawned worker on this host (all worktrees and clones): id,
        state (running / died / exited rc=N), elapsed, client, model, lane,
        pid and title/task - the same rows `autoos-agent.py ps --json` prints.
        include_ended adds workers that exited in the last 24 h (the same
        window as `autoos-agent.py ps --all`)."""
        return ps(include_ended)

    @_register("context")
    def _context(transcript: str | None = None) -> dict:
        """This session's context fill: tokens, the model's cap and the
        percentage (spec 6.1/8.3) - the same data `autoos-agent.py context`
        prints."""
        return context_info(transcript)

    @_register("oc_status")
    def _oc_status(lane: str) -> dict:
        """c2 (2026-10-06): one lane's status through tools/oc_l1.py - verdict
        live / silent / dead plus the exact next step for each. The lane
        password is never read, printed or logged; a missing password env
        surfaces as exit_code 2 with the remediation in "outcome" (oc_start).
        Reading a lane stays allowed inside an
        L2 - the fence is on control."""
        return oc_status(lane)

    @_register("oc_start")
    def _oc_start(lane: str) -> dict:
        """c2 (2026-10-06): start a lane through tools/oc_l1.py (render ->
        serve -> canary -> first prompt). Requires the lane password env
        (AUTOOS_OCL1_PW) to be set in this session's environment. Exit-code
        meanings travel in the answer's "outcome": 0 ok; 2 config/password;
        4 server not healthy; 5 UNATTENDED-REFUSED (canary not denied - fix
        the guard, delete the state file, restart, supervise). 
        Refused from inside an L2 lane
        (AUTOOS_AGENT_LAYER=L2): only the L1 that owns lanes steers them; an L2 reports to
        the L1 inbox instead."""
        return oc_start(lane)

    @_register("oc_restart")
    def _oc_restart(lane: str) -> dict:
        """c2 (2026-10-06): force-restart a lane the way the handoff card
        prescribes (kill the recorded pid, delete the state file; the watcher
        starts the lane within ~2 minutes). Never kills by process name and
        never touches passwords. Verify afterwards with oc_status. 
        Refused from inside an L2 lane
        (AUTOOS_AGENT_LAYER=L2): only the L1 that owns lanes steers them; an L2 reports to
        the L1 inbox instead."""
        return oc_restart(lane)

    @_register("l2_start")
    def _l2_start(repo: str, phase: str, brief_path: str,
                  combo: str = "l2-orchestrator") -> dict:
        """D-665 (AO-L2-LAUNCH): start the L2 lane for one phase and give it
        its brief. Lane `l2-<repo>-<checkout-tag>-<phase>`; model = the gateway combo (its own
        declared context, so no 128k clamp); MCP = the autoos-agent spawner
        ONLY, and the spawner an L2 starts lists only its own tools; OpenCode
        permission.task denied and the bash-guard plugin in its read-only `l2`
        role, so the L2 coordinates and never edits code; first
        prompt = the contents of brief_path plus the fixed footer (skill name,
        spawn tier-3 through autoos-agent, report REPORT/DONE to the L1 inbox).
        Needs AUTOOS_OCL1_PW (server password, by name only) and
        AUTOOS_OPENCODE_BIN in this session's environment, and AUTOOS_L1_INBOX
        or the lane reports nowhere. Refuses when the phase lane is already
        running - send it work with l2_inbox instead. Answers lane, port,
        session_id, pid, canary{denied,detail}; exit_code 5 is
        UNATTENDED-REFUSED (canary not denied: l2_stop, fix the guard, start
        again). 
        Refused from inside an L2 lane
        (AUTOOS_AGENT_LAYER=L2): only the L1 that owns lanes steers them; an L2 reports to
        the L1 inbox instead."""
        return l2_start(repo, phase, brief_path, combo)

    @_register("l2_status")
    def _l2_status(lane: str) -> dict:
        """D-665: a phase lane's verdict - live / silent / stalled / dead /
        absent - plus its session id, port, phase and last canary result.
        `stalled` (exit 1) is a live but stuck session: its last turn errored,
        or it sits idle while a child it spawned already exited - wake it with
        l2_resume.
        Reading a lane stays allowed inside an
        L2 - the fence is on control."""
        return l2_status(lane)

    @_register("l2_stop")
    def _l2_stop(lane: str) -> dict:
        """D-665: stop a phase lane cleanly (R-coord-10): kill the recorded
        PID's process group, verify it died, then remove the state file. A PID
        whose command line is not the lane's opencode is never killed; an
        orphan reports stopped=false. 
        Refused from inside an L2 lane
        (AUTOOS_AGENT_LAYER=L2): only the L1 that owns lanes steers them; an L2 reports to
        the L1 inbox instead."""
        return l2_stop(lane)

    @_register("l2_inbox")
    def _l2_inbox(lane: str, text: str) -> dict:
        """D-665: give a running phase lane more work - append one timestamped
        record to the lane's inbox and nudge its session with the launcher's
        own prompt call. The append happens whether or not the nudge lands;
        empty text is refused. 
        Refused from inside an L2 lane
        (AUTOOS_AGENT_LAYER=L2): only the L1 that owns lanes steers them; an L2 reports to
        the L1 inbox instead."""
        return l2_inbox(lane, text)

    @_register("l2_resume")
    def _l2_resume(lane: str) -> dict:
        """AO-L2-RESUME: wake a stalled phase lane with exactly one prompt per
        stall - the child that exited or the error that ended its last turn. A
        lane whose session is gone is stopped and started again from its stored
        config (restarted=true); a healthy lane that only refused the prompt
        (HTTP 409 busy) is left alone (wake_rejected=true); a stall already
        woken for is a no-op (already_woken=true).
        Refused from inside an L2 lane
        (AUTOOS_AGENT_LAYER=L2): only the L1 that owns lanes steers them; an L2 reports to
        the L1 inbox instead."""
        return l2_resume(lane)

    @_register(L2_REPORT_TOOL)
    def _l2_report(text: str, kind: str = "REPORT") -> dict:
        """AO-L2-LAUNCH criterion b: the L2's own report line. Appends ONE
        stamped record `<UTC> <lane> <kind>: <text>` to the L1 inbox
        (AUTOOS_L1_INBOX), so a milestone no longer costs a tier-3 spawn whose
        whole task was to write it. `kind` is REPORT, DONE or BLOCKED. The lane
        is this server's own environment (AUTOOS_L2_LANE), NEVER an argument - a
        report cannot name a lane that did not write it. The text is forced to a
        single printable line and capped at 500 chars. Present only in profile l2.
        """
        return l2_report(text, kind)

    @_register("heartbeat")
    def _heartbeat(inbox: str | None = None, transcript: str | None = None,
                   repos: list[str] | None = None, cap: int | None = None) -> dict:
        """Read-only heartbeat (R-heartbeat-02/03, R-pause-01, R-handoff-07):
        PAUSE state from an inbox, every repo's unpushed/dirty branches, and
        this session's context fill - the same facts and exit code
        `autoos-agent.py heartbeat --json` reports. Never pushes, commits or
        writes anything."""
        return heartbeat_info(inbox, transcript, repos, cap)

    return app


def serve() -> None:
    """Run the server on stdio with the profile this process's environment names."""
    build_server().run()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run-job":
        sys.exit(run_job(sys.argv[2]))
    serve()
