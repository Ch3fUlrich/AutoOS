#!/usr/bin/env python3
"""oc_l2.py - the L2 phase lane (D-665 AO-L2-LAUNCH).

One OpenCode orchestrator per PHASE, built on the L1 launcher rather than
beside it: this module only resolves what an L2 lane IS (a lane name, the
gateway combo's model, the spawner-only MCP list, the guard's read-only role,
the brief as the first prompt), writes that as an oc_l1 lane config,
and hands the whole start/status pipeline to `tools/oc_l1.py` — the render,
the health poll, the bash-guard canary and the state file are the same code
the L1 lanes run, so a fix to the canary lands here for free.

Why it exists: an L1 that wants phase-sized work starts a lane with
`l2_start(repo, phase, brief)` instead of spawning writers itself. The L2
coordinates; it never edits code (every write permission is denied in the
rendered config, `L2_PERMISSIONS`, the bash-guard's read-only `l2` role in its
child env, and the same instruction in its first prompt) and every change goes
through a tier-2 or tier-3 run it spawns over the `autoos-agent` MCP — always in
its own clone, because profile l2 forces `--isolate` — which is the ONLY
MCP server the lane enables, so an L2 has no editor, no filesystem MCP and no
second spawner.

Reporting (L2 -> L1): the lane's child env carries `AUTOOS_L1_INBOX` = the L1
inbox the resolved lane key `inbox_file` names, and the first prompt tells the
L2 to write its `REPORT` / `DONE` lines there. The same env carries
`AUTOOS_L2_LANE` = this lane's name (the render sets it, and the spawner records
it as `parent_lane` in every run it starts), which is how `status`/`resume` know
whose children they are looking at — by identity, not by a shared cwd. Work
going the other way
(L1 -> L2) is `inbox`: one timestamped record appended to the lane's own inbox
(<$AUTOOS_RUN_DIR>/inbox/<lane>.md when set, else <state dir>/<lane>/inbox.md,
the path the append reports) and the live session nudged with the same POST
/api/session/{id}/prompt the launcher uses for its first prompt.

Steering stays with the L1 (REJECT finding 2, D-665 criteria 2/3): the same MCP
server answers at both levels, so the L2 lane marks its own level in its child
env AND in the spawner's own environment (`agent_layer: "L2"` ->
`AUTOOS_AGENT_LAYER`, rendered into the MCP server's env by `oc_l1_render`). The
server reads `L2` back and (a) registers only the spawner's tools - no
`l2_*` / `oc_*` / `cancel` / `respond`, so an L2 never sees a menu it is going to
be refused, (b) still refuses the lane-control calls at the function, so the
shared code cannot be reached around the list, and (c) refuses a `spawn` that is
not a tier-2 or tier-3 worker in its own clone - an L2 that could start tier 1
owns the L1's own seat. Each worker the lane does start is stamped
`AUTOOS_AGENT_LAYER=L3` by the spawner (never by the caller), so it runs profile
`l3`, which has no `spawn` tool at all, and its CLI refuses any run with exit
code 14: the lane's depth budget ends with its own children.

State lives under `$AUTOOS_OCL2_STATE_DIR` (default `<tmpdir>/autoos-oc-l2/`),
one directory per lane holding the generated oc_l1 config (0600: it names host
paths), the scratch dirs, the composed first prompt and the lane's inbox. A
host that wants the lanes to survive a reboot sets that variable to a
persistent directory; nothing here is ever committed.

Passwords: the lane names the env var (`AUTOOS_OCL1_PW` by default, the L1
lanes' own variable) and this module only ever passes the NAME around. A
missing one is exit 2 with the remediation, never an invented value.

Subcommands (each prints one JSON object on stdout):
  start  --repo PATH --phase NAME --brief PATH [--combo l2-orchestrator]
  status --lane l2-<repo>-<checkout-tag>-<phase>   (live | silent | stalled | dead)
  stop   --lane l2-<repo>-<checkout-tag>-<phase>
  inbox  --lane l2-<repo>-<checkout-tag>-<phase> --text LINE
  resume --lane l2-<repo>-<checkout-tag>-<phase>
     one wake prompt per stall; restarts the lane when its session is gone and
     leaves a lane that merely refused the prompt (HTTP 409 busy) alone. A
     restart whose stop was refused reports `restart_failed` and starts nothing.

Exit codes: 0 ok - 2 config/validation/refusal (an unknown combo, a lane
already running, a missing binary or password env) - 4 server not healthy -
5 UNATTENDED-REFUSED (canary not denied; the codes are oc_l1's, forwarded).
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_l1  # noqa: E402
import oc_l1_serve  # noqa: E402
import oc_l1_render  # noqa: E402
from oc_l1_http import ServerDown, _data, _read_state, _request  # noqa: E402

# The spawner resolves the run-state root with this helper; a lane's children are
# looked for under the SAME root, never under a path this module invented (F4).
import autoos_clients  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "catalog" / "ai-registry.json"

DEFAULT_COMBO = "l2-orchestrator"
LANE_PREFIX = "l2-"
# The lane name is also a directory name and the MCP tools' one argument:
# [a-z0-9][a-z0-9-]{0,31}, the same shape tools/autoos_agent_mcp.py validates.
LANE_MAX = 32

ENV_PW = "AUTOOS_OCL1_PW"
ENV_BIN = "AUTOOS_OPENCODE_BIN"
ENV_L1_INBOX = "AUTOOS_L1_INBOX"
ENV_RUN_DIR = "AUTOOS_RUN_DIR"
ENV_STATE_DIR = "AUTOOS_OCL2_STATE_DIR"
ENV_GUARD_DIR = "AUTOOS_OCL2_GUARD_DIR"
GUARD_PLUGIN_RELPATH = Path("configuration") / "opencode" / "plugins" / "bash-guard"

# The renderer's own defaults, imported not restated (one home): an L2 lane
# overrides a subset of them and everything else renders unchanged.
PERMISSION_DEFAULTS = oc_l1_render.PERMISSIONS
# What an L2 may not do, in opencode's own permission vocabulary.
#
# Sonnet final REJECT 2026-10-08 finding 1: this used to be `{"task": "deny"}`
# alone, and the renderer's default `edit: allow` therefore survived into the
# L2's config - an orchestrator that can edit files has no reason to spawn, and
# the whole tier contract (L2 coordinates, L3 writes) silently disappeared.
# `write` is the older spelling of the same key and `apply_patch` the alias of
# `patch` (opencode v2's rename map is {bash: shell, task: subagent,
# apply_patch: patch}); both spellings are emitted because a rule only binds
# under the name the build matches - see lib/agent_harness.py KEYDENY3b.
# `read` and `bash` stay allowed: an L2 inspects the tree and runs read-only
# checks, and `bash` is fenced by the guard plugin in the read-only `l2` role -
# a closed list of inspection commands, no redirection anywhere - which the
# canary proves before the lane is ever prompted.
L2_PERMISSIONS = {
    "edit": "deny", "write": "deny",
    "patch": "deny", "apply_patch": "deny",
    "task": "deny", "subagent": "deny",
    "read": "allow", "bash": "allow",
}

SKILL = "unattended-orchestration"
# AO-L2-RESUME (P1): the footer line telling the L2 how to wait. Which runs
# are its children is DISCOVERED from the spawner run records (job.json per
# run under logs/agents/) - no recorder file is ever written.
CHILD_WAIT_LINE = (
    "Wait on children via `autoos-agent status`/`result` (their run record "
    "under logs/agents/<run>/, exit.json when done) - never `pgrep -f`.")
_SILENT_WINDOW_S = 10 * 60.0
_ERROR_STATUSES = ("error", "failed")
# AO-L2-RESUME rework F2: one wake per stall. A wake is recorded and the same
# stall is not woken again until the lane shows activity after it or this window
# passes - otherwise every `resume` poll posts another prompt to a session that
# was already told, and an L1 that retries wakes the lane dozens of times.
_WAKE_COOLDOWN_S = 10 * 60.0
# F6: what a wake prompt interpolates from a run record (a run id, an error
# string) is model-visible text from another process - one line, capped.
_SAFE_TEXT_MAX = 200
# The L2's contract, appended to every phase brief: what it may not do, the
# one route to a change, and where its reports land.
ROLE_LINES = (
    "You are the L2 orchestrator of this phase (%s). Load the `%s` skill first.",
    "You NEVER edit code and you never run a write against the repository: "
    "every file-mutating and spawn permission of this session (`edit`, `write`, "
    "`patch`, `task`) is denied and the shell guard runs in `l2` role, which "
    "leaves your shell a closed read-only list (`git status|log|diff|show`, "
    "ls, cat, rg, head, tail, wc, pwd) with no redirection at all. Every "
    "change goes through the `autoos-agent` MCP, and the only spawns it answers "
    "for an L2 are workers: a tier-2 writer (`role: implement`) or a tier-3 "
    "reviewer (`read_only`, or a review card), each forced into its own isolated "
    "clone - tier 1 and a `role: orchestrate` card are refused before a run "
    "starts, and each worker you start is marked a LEAF (L3), which never spawns "
    "of its own: work you did not ask for is yours to report, not a child's to "
    "start. "
    "Spawn the work that way (writer, then a cross-family reviewer), judge "
    "their reports, and report upward.",
    "Report upward with the `l2_report` tool on the `autoos-agent` MCP - never a "
    "spawn, never your own shell. Each call appends one line to the L1 inbox at "
    "`%s`, opened by a UTC `%%Y-%%m-%%dT%%H:%%M:%%SZ` timestamp (that stamp is what "
    "the reader parses; a line without it is invisible to it) and prefixed "
    "`REPORT` at a milestone, `DONE` when the phase finishes, `BLOCKED` when it "
    "cannot. `l2_report` stamps your lane for you from the MCP's own environment; "
    "you pass only the text and the kind. Your shell cannot append anywhere and "
    "must not spend a tier-3 worker on a report: the tool is the one write path "
    "an L2 has.",
)

_KILL_WAIT_S = 5.0
_KILL_HARD_WAIT_S = 2.0


class L2Error(Exception):
    """A refusal or a config problem: exit 2, nothing started."""


def _now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_text(value, cap=_SAFE_TEXT_MAX):
    """Text from another process, fit to interpolate into a prompt: one
    printable line, capped (F6). A run id or an error string that carried a
    newline would otherwise end the wake prompt and start a new instruction."""
    kept = [ch for ch in str(value)
            if ch == " " or (0x20 <= ord(ch) and ord(ch) != 0x7F)]
    return "".join(kept).strip()[:cap]


# --- naming ------------------------------------------------------------------


def slug(text):
    """A path/name fragment lowercased to [a-z0-9-]: a repo directory called
    `AutoOS CI` and one called `autoos-ci` name the same lane."""
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower())
    return out.strip("-")


def repo_tag(repo):
    """Six hex digits of the checkout's absolute, slugified path.

    The basename alone is not an identity (Sonnet final REJECT, finding 7): two
    worktrees of two projects are both called `autoos`, and the second project's
    `start` would join the first project's running lane instead of starting its
    own. Slugifying before hashing keeps the property the name already had -
    `/a/AutoOS CI` and `/a/autoos-ci` are one project, so one lane - while
    separating the paths that are genuinely different."""
    digest = hashlib.sha256(
        slug(os.path.abspath(str(repo))).encode("utf-8")).hexdigest()
    return digest[:6]


def lane_name(repo, phase):
    """`l2-<repo>-<checkout-tag>-<phase>`, truncated to the 32-char lane shape.
    The repo basename gives way when they cannot all fit: the phase and the
    checkout tag are what keeps two lanes distinct, the basename is the part a
    human can read off the directory anyway."""
    rslug, pslug = slug(Path(repo).name), slug(phase)
    tag = repo_tag(repo)
    if not rslug or not pslug:
        raise L2Error("repo and phase must each yield a [a-z0-9-] name "
                      "(got repo=%r phase=%r)" % (repo, phase))
    tail = "-%s-%s" % (tag, pslug)
    name = "%s%s%s" % (LANE_PREFIX, rslug, tail)
    if len(name) > LANE_MAX:
        keep = len(rslug) - (len(name) - LANE_MAX)
        if keep < 1:
            raise L2Error("phase '%s' is too long to fit a %d-char lane name"
                          % (pslug, LANE_MAX))
        name = "%s%s%s" % (LANE_PREFIX, rslug[:keep].rstrip("-"), tail)
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,%d}$" % (LANE_MAX - 1), name):
        raise L2Error("lane name '%s' does not match [a-z0-9][a-z0-9-]{0,31}" % name)
    return name


def check_lane(name):
    if not isinstance(name, str) or not re.match(
            r"^[a-z0-9][a-z0-9-]{0,%d}$" % (LANE_MAX - 1), name):
        raise L2Error("lane must match [a-z0-9][a-z0-9-]{0,31} (got %r)" % (name,))
    return name


# --- the model: a gateway combo, resolved from the registry -------------------


def combo_model(combo, registry_path=REGISTRY_PATH):
    """The lane `model` block for a gateway combo: the combo id is what the
    gateway routes, and the context/output come from its own declared surface
    so a 1M orchestrator never renders as 128k (that clamp is the muse-spark
    finding this repo already fixed once for pinned lanes)."""
    try:
        doc = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise L2Error("cannot read the registry %s: %s" % (registry_path, e))
    routes = doc.get("routes") if isinstance(doc, dict) else None
    route = routes.get(combo) if isinstance(routes, dict) else None
    if not isinstance(route, dict):
        raise L2Error("unknown combo '%s': no routes row in %s" % (combo, registry_path))
    surface = (route.get("surfaces") or {}).get("omniroute")
    if not isinstance(surface, dict):
        raise L2Error("combo '%s' is not served on the omniroute surface "
                      "(an L2 lane talks to the gateway only)" % combo)
    model = {"provider": "omniroute", "key": combo, "modelID": combo}
    ctx, out = surface.get("context"), surface.get("output")
    if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0:
        limit = {"context": ctx}
        if isinstance(out, int) and not isinstance(out, bool) and out > 0:
            limit["output"] = out
        model["limit"] = limit
    return model


# --- paths -------------------------------------------------------------------


def state_root():
    return Path(os.environ.get(ENV_STATE_DIR)
                or Path(tempfile.gettempdir()) / "autoos-oc-l2")


def lane_dir(name):
    return state_root() / name


def config_path(name):
    return state_root() / ("%s.json" % name)


def guard_plugin_dir():
    """The bash-guard plugin DIRECTORY (oc_l1's `plugins` entries are dirs).
    It comes from this checkout, not the lane's cwd: an L2 lane's cwd is the
    project it coordinates, which need not carry the guard."""
    d = os.environ.get(ENV_GUARD_DIR)
    path = Path(d) if d else REPO_ROOT / GUARD_PLUGIN_RELPATH
    if not (path / "index.mjs").is_file():
        raise L2Error("bash-guard plugin not found at %s (set %s to the plugin "
                      "directory): an unguarded lane can never pass its canary"
                      % (path, ENV_GUARD_DIR))
    return path


def resolve_l1_inbox(explicit=None):
    """Where the L2 reports: --inbox, else AUTOOS_L1_INBOX, else refused.
    A report that goes nowhere is a phase that silently never finishes."""
    path = explicit or os.environ.get(ENV_L1_INBOX)
    if not path:
        raise L2Error("no L1 inbox: pass --inbox PATH or set %s - the L2's "
                      "REPORT/DONE lines must land somewhere a reader polls"
                      % ENV_L1_INBOX)
    p = Path(path)
    if not p.is_absolute():
        raise L2Error("the L1 inbox must be an absolute path (got %s)" % path)
    return p


def lane_inbox(lane_dir_):
    """The lane's own inbox (L1 -> L2): the run dir's inbox/<lane>.md when
    AUTOOS_RUN_DIR names a run, else one under the lane's own directory."""
    run = os.environ.get(ENV_RUN_DIR)
    if run:
        return Path(run) / "inbox" / ("%s.md" % lane_dir_.name)
    return lane_dir_ / "inbox.md"


def agents_root():
    """Where the spawner's run dirs live, resolved the way the spawner resolves
    it: `autoos_clients.state_dir()/agents` - honoured by AUTOOS_STATE_DIR, the
    checkout's own `logs/agents` otherwise (F4). NOT `<lane cwd>/logs`: the
    spawner that writes those records is this checkout's MCP server, whatever
    project the lane coordinates."""
    return Path(autoos_clients.state_dir()) / "agents"


# --- the first prompt --------------------------------------------------------


def first_prompt_text(brief_text, name, l1_inbox):
    """The brief verbatim plus the fixed footer; the hint line is the L1
    launcher's own (one home for how MCP is reached under opencode)."""
    role = "\n".join([
        ROLE_LINES[0] % (name, SKILL),
        ROLE_LINES[1],
        ROLE_LINES[2] % str(l1_inbox),
    ])
    return "%s\n\n---\n%s\n\n%s\n\n%s" % (brief_text.rstrip(), role,
                                          CHILD_WAIT_LINE,
                                          oc_l1_serve.HINT_LINE)


# --- the lane spec -----------------------------------------------------------


def build_lane(name, repo, phase, brief, combo, l1_inbox, *, opencode_bin=None,
               password_env=ENV_PW, port=None, guard_dir=None, model=None,
               child_env=None):
    """The oc_l1 lane dict for one phase. `handoff` is the brief because
    oc_l1 requires the file the session reads first; the prompt body itself
    travels in `first_prompt_file`, which the composed brief+footer owns."""
    b = opencode_bin or os.environ.get(ENV_BIN)
    if not b:
        raise L2Error("%s is not set and no --opencode-bin was given: the lane "
                      "names the explicit npm opencode binary, never a PATH "
                      "lookup" % ENV_BIN)
    if not os.path.isabs(b):
        raise L2Error("%s must be an absolute path (got %s)" % (ENV_BIN, b))
    gdir = str(guard_dir or guard_plugin_dir())
    scratch = lane_dir(name)
    inbox = lane_inbox(scratch)
    prompt_file = scratch / "first-prompt.md"
    lane = {
        "name": name,
        "cwd": str(repo),
        "handoff": str(brief),
        "opencode_bin": b,
        "password_env": password_env,
        "model": model or combo_model(combo),
        # The spawner and nothing else: an L2 that could edit files or reach a
        # second MCP has no reason to spawn anything, and that is how an L2
        # stops existing.
        "mcp": ["autoos-agent"],
        "instructions": ["AGENTS.md", str(brief)],
        "plugins": [gdir],
        "permission": dict(L2_PERMISSIONS),
        # AO-L2-LAUNCH merge criterion 1: not `orchestrator`, which scopes an
        # L2's writes to .oc-pilot/. An L2 has no writable scope: the guard's
        # read-only role, a closed list of inspection commands.
        "guard_role": "l2",
        # Sonnet final REJECT 2026-10-08 finding 2: one MCP server answers both
        # levels, so an L2 could start, stop and nudge lanes. The lane marks its
        # own layer in its own environment and the server refuses lane-control
        # tools when it reads L2 back.
        "agent_layer": "L2",
        "child_env": list(child_env or []),
        "inbox_file": str(l1_inbox),
        "first_prompt_file": str(prompt_file),
        "scratch_dir": str(scratch),
        "state_file": str(scratch / ("oc-l1-%s.state.json" % name)),
        "heartbeat_file": str(scratch / "heartbeat.json"),
        # L2-only metadata (oc_l1 ignores keys it does not know): stop, status
        # and inbox read the phase back out of the generated config instead of
        # re-deriving it from arguments nobody passed.
        # `agents_root` is recorded at start (F4): the `resume` CLI an MCP tool
        # launches has AUTOOS_STATE_DIR stripped from its environment, so the
        # lane carries the root its own children will be written under rather
        # than re-deriving it from an environment that is no longer the starter's.
        "l2": {"repo": str(repo), "phase": phase, "combo": combo,
               "brief": str(brief), "l1_inbox": str(l1_inbox),
               "lane_inbox": str(inbox), "agents_root": str(agents_root())},
    }
    if port:
        lane["serve_port"] = port
    return lane


def write_config(lane):
    """The one-lane oc_l1 config, 0600 (it names host paths), atomic."""
    path = config_path(lane["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({lane["name"]: lane}, f, indent=2)
        f.write("\n")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def read_config(name):
    """The stored lane for a lane name, or None when no lane was started."""
    p = config_path(check_lane(name))
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    lane = data.get(name) if isinstance(data, dict) else None
    return lane if isinstance(lane, dict) else None


def run_oc_l1(subcommand, name, cfg_path):
    """oc_l1.main in-process with its output captured (one validator, one
    launcher - this module never re-implements either)."""
    buf = io.StringIO()
    argv = [subcommand, "--name", name, "--config", str(cfg_path)]
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = oc_l1.main(argv)
    return rc, buf.getvalue()


def live_session(lane):
    """The lane's state file when it names a session the server still answers
    for - the same liveness test oc_l1's own idempotence branch runs."""
    state = _read_state(lane.get("state_file") or "")
    if not isinstance(state, dict):
        return None
    sid, port = state.get("session_id"), state.get("port")
    if not isinstance(sid, str) or not sid or not port:
        return None
    password = os.environ.get(lane.get("password_env") or ENV_PW)
    if not password:
        return None
    try:
        status, payload = _request(port, "GET", "/api/session/%s" % sid,
                                   password=password)
    except ServerDown:
        return None
    if status != 200 or not isinstance(_data(payload), dict):
        return None
    if _data(payload).get("outcome") in oc_l1_serve.DEAD_OUTCOMES:
        return None
    return state


# --- AO-L2-RESUME (P1): activity heartbeat, stall detection, resume -----------
# The canary writes heartbeat.json with turn 0 and nothing moved it, so a live
# lane read as turn 0. Every status poll merges the session's turn count and
# newest message ts forward; a lane whose last turn errored, or that sits idle
# while a child it spawned already exited, is `stalled` — one wake per stall
# (`last_wake_ts` in the heartbeat), `resume` restarts the lane when the session
# is gone and leaves a healthy lane alone when it only refused the prompt.
# Children are discovered from the spawner's own run records, under the state
# dir the spawner writes (`autoos_clients.state_dir()/agents`, recorded in the
# lane config at start), and a run belongs to the lane only when its job.json
# carries `parent_lane` == this lane's name — never merely because it shares a
# cwd. Child state is the run's own exit.json — never `pgrep -f`, whose pattern
# sits in the caller's own argv and matches itself, so it never ends.

def _session_messages(port, sid, password, limit=20):
    """Newest-first messages, [] when empty, None when unreachable (never raises)."""
    try:
        status, payload = _request(
            port, "GET", "/api/session/%s/message?order=desc&limit=%d" % (sid, limit),
            password=password)
    except ServerDown:
        return None
    if status != 200:
        return None
    items = _data(payload)
    if isinstance(items, dict):
        items = items.get("items")
    return items if isinstance(items, list) else []


def _turn_activity(items):
    """(progress-turn count, newest progress ts or None) over the messages."""
    count, newest = 0, None
    for item in items:
        if not oc_l1_serve._is_progress(item):
            continue
        count += 1
        ts = oc_l1_serve._item_ts(item)
        if ts is not None and (newest is None or ts > newest):
            newest = ts
    return count, newest


def _heartbeat_path(lane):
    return Path(lane.get("heartbeat_file")
                or str(Path(lane["scratch_dir"]) / "heartbeat.json"))


def _read_heartbeat(lane):
    """The lane's heartbeat dict; {} when it has none or it will not parse."""
    try:
        data = json.loads(_heartbeat_path(lane).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_heartbeat(lane, data):
    path = _heartbeat_path(lane)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def note_activity(lane):
    """Merge the session's turn count / newest message ts into heartbeat.json.

    The turn only moves forward; only the turn/last-activity keys are touched.
    Best-effort: returns (turn, last_message_ts) or None, never raises."""
    try:
        state = _read_state(lane.get("state_file") or "")
        if not isinstance(state, dict):
            return None
        sid, port = state.get("session_id"), state.get("port")
        if not isinstance(sid, str) or not sid or not port:
            return None
        items = _session_messages(
            port, sid, os.environ.get(lane.get("password_env") or ENV_PW))
        if items is None:
            return None
        count, newest = _turn_activity(items)
        data = _read_heartbeat(lane)
        data["turn"] = max(int(data.get("turn") or 0), count)
        if newest is not None:
            data["last_message_ts"] = newest
            data["last_activity_ts"] = _now_ts()
        _write_heartbeat(lane, data)
        return data["turn"], data.get("last_message_ts")
    except (OSError, ValueError):
        return None


def child_exited(run_dir):
    """The run's exit record: {"exited", "rc", "cancelled", "ended"}.

    `run_dir` holds the spawner's exit.json ({rc, ended}, {"cancelled": true}
    on cancel). Missing/unreadable is not exited - the child may still work.
    `pgrep -f <run-id>` is never consulted: the pattern sits in the caller's
    own argv, so it always "finds" the child and the wait never ends."""
    out = {"exited": False, "rc": None, "cancelled": False, "ended": None}
    try:
        data = json.loads((Path(run_dir) / "exit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    out["cancelled"] = data.get("cancelled") is True
    out["rc"] = data.get("rc")
    ended = data.get("ended")
    if isinstance(ended, bool):
        ended = None
    elif isinstance(ended, (int, float)):
        ended = float(ended) / 1000.0 if ended > 1e12 else float(ended)
    elif isinstance(ended, str):
        try:
            ended = datetime.fromisoformat(ended.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ended = None
    else:
        ended = None
    out["ended"] = ended
    out["exited"] = out["cancelled"] or "rc" in data or "ended" in data
    return out


def _lane_children(lane):
    """The lane's spawned workers, newest last, DISCOVERED not recorded: run
    dirs under the spawner's agents root whose job.json was written by THIS lane.

    F1 (attacker review): attribution is by identity, never by cwd. The spawner
    stamps every run it starts with the lane that owns it — the lane name the
    renderer put in the lane's own environment (`AUTOOS_L2_LANE`), recorded as
    `parent_lane` at the top of job.json — and only that match makes a run a
    lane's child. The old filter was "same cwd, started at or after the lane",
    which any run anyone starts in the lane's directory after it began matches:
    one unrelated worker exiting then read the lane as stalled and woke it for
    someone else's process. Same-cwd and the start time stay as the secondary
    filter — they narrow what is already this lane's — but they are never
    sufficient on their own."""
    name = lane.get("name") or ""
    state = _read_state(lane.get("state_file") or "") or {}
    start = 0.0
    with contextlib.suppress(ValueError, TypeError):
        start = datetime.fromisoformat(
            str(state.get("started_utc") or "").replace("Z", "+00:00")).timestamp()
    l2 = lane.get("l2") or {}
    root = Path(l2.get("agents_root") or agents_root())
    want = os.path.realpath(lane.get("cwd") or "")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    found = []
    for name_ in names:
        try:
            job = json.loads((root / name_ / "job.json").read_text(encoding="utf-8"))
            if job.get("parent_lane") != name:
                continue
            req = job.get("request") or {}
            got = req.get("cwd") if isinstance(req, dict) else None
            got = os.path.realpath(got or job.get("cwd") or "")
            begun = job.get("started")
            if got == want and isinstance(begun, (int, float)) and begun >= start:
                found.append({"run_id": job.get("run_id") or job.get("id") or name_,
                              "run_dir": str(root / name_), "started": float(begun)})
        except (OSError, ValueError, AttributeError):
            continue
    found.sort(key=lambda e: e["started"])
    return found


def _wake_key(info):
    """What one wake covered: the reason plus the child it was about, so a new
    stall of a different shape is still worth a prompt."""
    return "%s:%s" % (info.get("reason") or "", info.get("run_id") or "")


def _already_woken(heartbeat, info, newest):
    """F2: was this exact stall already woken for? Covered until the lane shows
    activity after the wake, or the wake cooldown passes. `newest` is the newest
    turn timestamp of the session (None when it has none)."""
    ts = heartbeat.get("last_wake_ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return False
    if heartbeat.get("last_wake_key") != _wake_key(info):
        return False
    if isinstance(newest, (int, float)) and not isinstance(newest, bool) \
            and newest > ts:
        return False
    return (time.time() - ts) < _WAKE_COOLDOWN_S


def _newest_progress_item(items):
    """The transcript's NEWEST progress item, or None when it holds none.

    `_session_messages` asks the server for `order=desc`, so the first progress
    item is the newest and that position is the tie-break - exactly what
    `oc_l1_serve._newest_progress_ts` assumes. Where the items do carry
    timestamps the largest wins even if the transcript came back oldest-first:
    `stalled()` reads the newest turn out of `_turn_activity` the same way, and
    the two must name one and the same turn."""
    chosen, chosen_ts = None, None
    for item in items:
        if not oc_l1_serve._is_progress(item):
            continue
        ts = oc_l1_serve._item_ts(item)
        if chosen is None:
            chosen, chosen_ts = item, ts
        elif ts is not None and (chosen_ts is None or ts > chosen_ts):
            chosen, chosen_ts = item, ts
    return chosen


def _last_turn_error(items):
    """Did the newest turn end in error? (True, detail) or (False, "").

    Exactly ONE item is judged: `_newest_progress_item`'s. A dead turn is
    finish=error, or a tool item (top-level or in parts/content) whose state is
    error - the shapes the transcript carries. An older errored turn is not a
    stall: the lane spoke again after it, and scanning back through the
    transcript would wake it for an error it already worked past."""
    item = _newest_progress_item(items)
    if item is None:
        return False, ""
    cands = [item]
    for key in ("parts", "content"):
        parts = item.get(key)
        if isinstance(parts, list):
            cands += [p for p in parts if isinstance(p, dict)]
    for cand in cands:
        state = cand.get("state")
        status = (state.get("status") if isinstance(state, dict) else None) \
            or cand.get("status")
        if status in _ERROR_STATUSES:
            err = (state.get("error") if isinstance(state, dict) else None) \
                or cand.get("error") or ""
            if isinstance(err, dict):
                err = err.get("message") or ""
            return True, str(err)[:200] or "tool %s error" % (cand.get("tool") or "?")
        if cand.get("finish") == "error" or item.get("finish") == "error":
            return True, "finish=error"
    return False, ""


def stalled(name):
    """Is the lane stuck? `last-turn-error`, `child-exited`, or not stalled.

    A dead server is `dead`, not stuck - status already says so. A stall that
    was already woken for is not stalled either (F2): it reads `already-woken`
    until the lane moves or the wake cooldown passes, so one wake covers one
    stall however often status or resume is polled in between."""
    name = check_lane(name)
    lane = read_config(name)
    if lane is None:
        return {"lane": name, "stalled": False, "reason": "absent"}
    state = live_session(lane)
    if state is None:
        return {"lane": name, "stalled": False, "reason": "not-live"}
    base = {"lane": name, "session_id": state.get("session_id"),
            "port": state.get("port")}
    items = _session_messages(state["port"], state["session_id"],
                              os.environ.get(lane.get("password_env") or ENV_PW))
    if items is None:
        items = []
    _, newest = _turn_activity(items)
    verdict = None
    is_err, detail = _last_turn_error(items)
    if is_err:
        verdict = {"reason": "last-turn-error", "detail": detail}
    else:
        kids = _lane_children(lane)
        kid = kids[-1] if kids else None
        ex = child_exited(kid["run_dir"]) if kid else {"exited": False}
        if kid and ex["exited"] and (newest is None or ex["ended"] is None
                                     or newest <= ex["ended"]):
            verdict = {"reason": "child-exited", "run_id": kid["run_id"],
                       "run_dir": kid["run_dir"], "rc": ex["rc"],
                       "cancelled": ex["cancelled"]}
    if verdict is None:
        return dict(base, stalled=False, reason="ok")
    if _already_woken(_read_heartbeat(lane), verdict, newest):
        return dict(base, stalled=False, reason="already-woken")
    verdict["next_action"] = _next_action(verdict)
    return dict(base, stalled=True, **verdict)


# What the lane-state layer can legitimately fail with on a poll: a malformed
# lane name or config, an unreadable state/heartbeat file, a server that stopped
# answering mid-request. Anything else that reaches a poll is a bug in the probe
# and is allowed to surface - the bare `except Exception` these calls carried
# swallowed it and the poll just looked like a lane that was not stalled.
_POLL_PROBE_ERRORS = (L2Error, ServerDown, OSError, ValueError)


def _poll_stalled(name, out):
    """`stalled()` for a status/inbox poll: the verdict, or None with the reason
    named in `out` when the probe failed on something the lane state can do."""
    try:
        return stalled(name)
    except _POLL_PROBE_ERRORS as e:
        out["stalled_error"] = type(e).__name__
        return None


def _poll_activity(lane, out):
    """`note_activity()` for a poll, which is decoration on the verdict: an
    expected failure is named in `out`, a bug in it is not swallowed."""
    try:
        note_activity(lane)
    except _POLL_PROBE_ERRORS as e:
        out["activity_error"] = type(e).__name__


def _next_action(verdict):
    """What the stall says to do next, in one clause.

    A wake that only names the stall leaves the lane to invent the step, and it
    invented the same filler every time. The action belongs on the verdict so
    the wake prompt, `resume`'s answer and `status`'s detail all carry the one
    wording."""
    if verdict.get("reason") == "child-exited":
        return ("read %s result via autoos-agent result and continue the phase "
                "plan" % verdict.get("run_id"))
    return ("re-read the last tool error, retry the failed step once, then "
            "continue the phase plan")


def _wake_text(info):
    """The one short wake prompt: it names the next action, not the brief.

    Everything interpolated here comes out of another process's record, so it
    goes through `_safe_text` (F6): a run id or error string carrying a newline
    would otherwise end the wake line and start an instruction of its own."""
    nxt = _safe_text(info.get("next_action") or "the next phase step")
    if info.get("reason") == "child-exited":
        return ("Wake: child %s exited rc=%s; %s. Reply with "
                "one short line of what you do next."
                % (_safe_text(info.get("run_id")), _safe_text(info.get("rc")), nxt))
    return ("Wake: your last turn ended in error (%s); %s. Reply with one short "
            "line of what you do next."
            % (_safe_text(info.get("detail") or "unknown error"), nxt))


def _record_wake(lane, info):
    """F2: mark the wake in the heartbeat - when it went out, which stall it
    covers, and the child or error it woke for."""
    hb = _read_heartbeat(lane)
    hb["last_wake_ts"] = time.time()
    hb["last_wake_key"] = _wake_key(info)
    for key in ("run_id", "detail"):
        if info.get(key) is not None:
            hb["last_wake_" + key] = _safe_text(info[key])
    with contextlib.suppress(OSError):
        _write_heartbeat(lane, hb)


def _clear_wakes(lane):
    """Drop F2's wake markers, and only those, from the heartbeat.

    A marker says "this stall was woken for" about the SESSION that wrote it. A
    restarted lane - by `resume` or by a stop and start over the same scratch
    dir - is a new session reading the heartbeat.json the canary merges, so a
    stale marker answers for a stall that has not happened yet: the first stall
    under the same key reads `already-woken` and never gets a wake. The turn
    count is left alone - it only ever merges forward, and zeroing it here would
    set the lane back."""
    hb = _read_heartbeat(lane)
    stale = [k for k in hb if k.startswith("last_wake")]
    if not stale:
        return                      # nothing to clear, and no file to create
    for key in stale:
        del hb[key]
    with contextlib.suppress(OSError):
        _write_heartbeat(lane, hb)


def _restart_lane(name, lane, why):
    """Stop the lane and start it again from its stored config, reporting the
    pair as one answer. `why` is what made a wake impossible - the restart is
    the caller's only remaining move, so it is named in the detail.

    A stop that was REFUSED (R-coord-10: an unverifiable pid, a state file that
    would not go) leaves the old server running, so `start` would only answer
    `already running` and the pair would report that as the restart's own
    failure. The refusal is the finding, so it is what gets reported and no
    start is attempted. A lane whose process is already gone is not a refusal -
    `cmd_stop` calls that `stopped`, and it restarts."""
    stopped = cmd_stop(name)
    if not stopped.get("stopped"):
        refused = "stop refused (%s)" % (stopped.get("detail") or "no reason given")
        return {"lane": name, "resumed": False, "restarted": False,
                "restart_failed": refused, "stop": stopped, "exit_code": 2,
                "detail": "%s; restart failed: %s" % (why, refused)}
    l2 = lane.get("l2") or {}
    try:
        result, rc = cmd_start(
            l2.get("repo") or lane.get("cwd"), l2.get("phase") or name,
            l2.get("brief") or lane.get("handoff"),
            combo=l2.get("combo") or DEFAULT_COMBO,
            l1_inbox=l2.get("l1_inbox"),
            opencode_bin=lane.get("opencode_bin"),
            password_env=lane.get("password_env") or ENV_PW,
            port=lane.get("serve_port"), model=lane.get("model"),
            child_env=lane.get("child_env"))
    except (L2Error, oc_l1.LaneError) as e:
        return {"lane": name, "resumed": False, "restarted": False,
                "stop": stopped, "detail": "%s; restart failed: %s" % (why, e)}
    return {"lane": name, "resumed": False, "restarted": rc == 0,
            "stop": stopped, "start": result,
            "detail": "%s; restarted (exit %s)" % (why, rc)}


def cmd_resume(name):
    """Wake a stalled lane once with its next action, or restart it.

    One prompt per stall (F2), and the answer separates the three things that
    can happen: a wake landed, the lane was refused (a healthy lane that is busy
    is left alone, F3), or the session was gone and the lane restarted from its
    stored config."""
    name = check_lane(name)
    lane = read_config(name)
    if lane is None:
        raise L2Error("no lane config at %s for '%s'" % (config_path(name), name))
    info = stalled(name)
    state = live_session(lane)
    if state is None:
        # F3: a session that is gone - whether the serve still answers for
        # nothing or is dead entirely - cannot take a wake. Restarting from the
        # stored config is the only way the phase continues.
        return _restart_lane(
            name, lane, "the lane has no live session (status %s)"
                        % info.get("reason"))
    if not info.get("stalled"):
        reason = info.get("reason")
        woken = reason == "already-woken"
        return {"lane": name, "resumed": False, "restarted": False, "noop": True,
                "already_woken": woken, "reason": reason,
                "detail": (
                    "already woken for this stall - nothing new until the lane "
                    "moves or the %d-minute wake cooldown passes"
                    % int(_WAKE_COOLDOWN_S / 60) if woken else
                    "not stalled (%s) - nothing to wake" % reason)}
    try:
        status, _ = _request(
            state["port"], "POST", "/api/session/%s/prompt" % state["session_id"],
            body={"text": _wake_text(info)},
            password=os.environ.get(lane.get("password_env") or ENV_PW))
    except ServerDown as e:
        return _restart_lane(name, lane,
                             "the wake prompt did not reach the server (%s)" % e)
    if status != 200:
        # F3: the server answered, so the lane is healthy - it merely refused
        # this prompt (409 busy is the ordinary shape). Restarting a lane that
        # is working would throw the turn it is mid-way through away.
        out = {"lane": name, "resumed": False, "restarted": False,
               "wake_rejected": True, "reason": info.get("reason"),
               "http_status": status,
               "detail": "the wake prompt returned HTTP %s - a healthy lane is "
                         "left alone, not restarted" % status}
        for key in ("run_id", "rc", "next_action"):
            if info.get(key) is not None:
                out[key] = info[key]
        return out
    _record_wake(lane, info)
    out = {"lane": name, "resumed": True, "restarted": False,
           "reason": info.get("reason"),
           "detail": "woke session %s (%s)" % (state["session_id"], info["reason"])}
    for key in ("run_id", "rc", "next_action"):
        if info.get(key) is not None:
            out[key] = info[key]
    return out


# --- stop / inbox ------------------------------------------------------------


def _identity_ok(pid, state):
    """May this PID be signalled as the lane? (True, None) or (False, why).

    Identity is the recorded command line AND the kernel start time of that
    exact PID - not a token of the command line that resembles the binary's
    name. A recycled PID can inherit the dead lane's number, and a wrapper's
    `exec` chain means the live head token is not the recorded one, so the
    comparison is made over the launcher's own arguments (everything after the
    head) plus the start time, which is the one fact a recycler cannot copy.
    A state file written before the launcher recorded either fact proves
    nothing: refusing keeps the process that is not provably the lane alive and
    keeps the state that describes it. An unreadable /proc (Windows, or already
    gone) is not evidence to refuse on.
    """
    recorded = state.get("argv")
    start = state.get("start_time")
    if not isinstance(recorded, list) or not recorded or \
            not isinstance(start, int):
        return False, ("pid %d cannot be verified: the state file records no "
                       "argv/start_time - leave it running and kill it by hand "
                       "if the lane must go" % pid)
    live = oc_l1_serve.proc_argv(pid)
    if live is None:
        return True, None
    args = recorded[1:]
    if live[len(live) - len(args):] != args:
        return False, ("pid %d is not the lane's recorded command - refused to "
                       "kill it, the state file stays" % pid)
    now = oc_l1_serve.proc_starttime(pid)
    if now is not None and now != start:
        return False, ("pid %d is the lane's command but not its process: start "
                       "time %s is not the recorded %s (a recycled pid) - "
                       "refused to kill it, the state file stays"
                       % (pid, now, start))
    return True, None


def _pgid(pid):
    try:
        return os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return None


def _pid_gone(pid):
    """Is the recorded PID finished? `kill(pid, 0)` also answers for a ZOMBIE -
    killed, not yet waited for by its parent - so reading that as alive would
    report an orphan that cannot exist. Reap it when it is our own child,
    else read /proc's state letter where it exists (the same reasoning as
    tests/_oc_l1_fakes.pid_alive, which the L1 suite already needs)."""
    if os.name == "nt":
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return True
        return False
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return True
    except (ChildProcessError, OSError):
        pass  # not our child: its own parent reaps it
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return True
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state == "Z"


def _wait_gone(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not _pid_gone(pid):
        time.sleep(0.1)
    return _pid_gone(pid)


def cmd_stop(name):
    """Clean stop (R-coord-10): the recorded PID's whole process group, then
    the state file goes - a stopped lane that leaves a `serve` bound to its
    port would make the next start's health poll answer for the orphan."""
    name = check_lane(name)
    lane = read_config(name)
    if lane is None:
        return {"lane": name, "stopped": False,
                "detail": "no lane config at %s - nothing was started" % config_path(name)}
    state = _read_state(lane.get("state_file") or "")
    pid = state.get("pid") if isinstance(state, dict) else None
    # the port the server actually took (fix 6 moves a lane off a held port),
    # with the config's number only as the fallback for a lane that never started
    port = ((state.get("port") if isinstance(state, dict) else None)
            or lane.get("serve_port") or oc_l1.derive_port(name))
    out = {"lane": name, "port": port,
           "killed_pids": [], "orphan": False, "removed_state": []}
    if not (isinstance(pid, int) and pid > 0):
        out["stopped"] = True
        out["detail"] = "no live pid recorded; nothing to kill"
        return out
    if os.name == "nt":
        # taskkill /F /T walks the tree itself (oc_l1's own kill path).
        oc_l1_serve._kill_pid(pid)
        out["killed_pids"].append(pid)
    else:
        ok, why = _identity_ok(pid, state)
        if not ok:
            out.update(stopped=False, orphan=True, detail=why)
            return out
        # start_new_session=True made the child its own group leader, so the
        # group is the tree: whatever `opencode serve` forks dies with it.
        # Anywhere else the process belongs to whoever started it - possibly
        # this very caller - and only the recorded PID may be signalled.
        group_leader = _pgid(pid) == pid
        if group_leader:
            try:
                os.killpg(pid, signal.SIGTERM)
                out["killed_pids"].append(pid)
            except (ProcessLookupError, PermissionError, OSError):
                oc_l1_serve._kill_pid(pid)
                out["killed_pids"].append(pid)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            out["killed_pids"].append(pid)
        if group_leader:
            # AO-L2-LAUNCH criterion b: the group is the whole tree, and a
            # grandchild that IGNORES SIGTERM outlives both it and the leader.
            # The lone recorded PID dying is therefore no proof the lane is gone:
            # after the graceful window, SIGKILL the ENTIRE group regardless of
            # whether the leader already died. The group's id equals the leader's
            # (now-dead) PID and the group still exists while any member does, so
            # killpg keeps reaching the survivors. A stop that signals only the
            # leader would leave that stubborn child running - the exact orphan
            # R-coord-10 forbids.
            _wait_gone(pid, _KILL_WAIT_S)
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            _wait_gone(pid, _KILL_HARD_WAIT_S)
        elif not _wait_gone(pid, _KILL_WAIT_S):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            _wait_gone(pid, _KILL_HARD_WAIT_S)
    if not _pid_gone(pid):
        out.update(stopped=False, orphan=True,
                   detail="pid %d is still alive after SIGKILL to its process group; "
                          "the state file stays so the lane is not reported as "
                          "stopped" % pid)
        return out
    # The state file goes last: while it exists the lane is still "running"
    # to anything that reads it.
    try:
        os.remove(lane["state_file"])
        out["removed_state"].append(lane["state_file"])
    except OSError as e:
        out.update(stopped=False, orphan=False,
                   detail="the server is stopped but the state file could not be "
                          "removed: %s" % e)
        return out
    out["stopped"] = True
    out["detail"] = "stopped"
    return out


def append_record(path, text, source="l1"):
    """One inbox record, appended atomically: `<UTC>Z [who] <text>`, the shape
    tools/autoos_inbox.py parses. A single O_WRONLY|O_APPEND|O_CREAT write, so
    a concurrent appender cannot interleave inside the line."""
    line = "%s [%s] %s\n" % (_now_ts(), source, str(text).rstrip("\n"))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return line


def cmd_inbox(name, text):
    """L1 -> L2: land the line in the lane's inbox, then wake the session. The
    append happens whether or not the nudge gets through - a line lost because
    the server was mid-restart would otherwise have to be re-sent by hand.

    The nudge is for a lane that proved itself guarded: the canary was DENIED
    and the pilot got its first prompt (Sonnet final REJECT, finding 3). An
    rc5 start leaves a server that answers for a live session while its pilot
    sits idle and unverified - waking that would send work to a session nobody
    has shown to be under the guard, and read to L1 as a running lane."""
    name = check_lane(name)
    if not isinstance(text, str) or not text.strip():
        raise L2Error("inbox text must not be empty")
    lane = read_config(name)
    if lane is None:
        raise L2Error("no lane config at %s for '%s'" % (config_path(name), name))
    inbox = Path((lane.get("l2") or {}).get("lane_inbox")
                 or lane_inbox(Path(lane.get("scratch_dir") or lane_dir(name))))
    line = append_record(inbox, text)
    out = {"lane": name, "inbox": str(inbox), "appended": line.rstrip("\n"),
           "nudged": False, "refused": False}
    state = live_session(lane)
    if state is None:
        out["detail"] = "the lane is not live - the line is in the inbox and will " \
                        "be read when it starts; nudge it with l2_status/l2_stop"
        return out
    canary = state.get("canary") if isinstance(state.get("canary"), dict) else {}
    if canary.get("denied") is not True or state.get("prompted") is not True:
        out["refused"] = True
        out["detail"] = ("the line is in the inbox, but the session is not nudged: "
                         "this lane never cleared its canary (canary denied=%s, "
                         "prompted=%s) - stop it and start it again to get a "
                         "guarded, running lane"
                         % (canary.get("denied"), state.get("prompted")))
        return out
    # AO-L2-RESUME: a stalled-but-alive lane cleared its canary, so it is
    # nudged like any running lane - the refusal above stays for lanes that
    # never cleared it. The flag tells the caller the nudge is a wake, and a
    # nudge precedes a turn, so the heartbeat moves with it.
    info = _poll_stalled(name, out)
    out["stalled"] = bool(info and info.get("stalled"))
    if info and out["stalled"]:
        out["stalled_reason"] = info.get("reason")
    _poll_activity(lane, out)
    password = os.environ.get(lane.get("password_env") or ENV_PW)
    try:
        status, _ = _request(state["port"], "POST",
                             "/api/session/%s/prompt" % state["session_id"],
                             body={"text": "New inbox line for %s:\n%s" % (name, line)},
                             password=password)
    except ServerDown as e:
        out["detail"] = "inbox written; the nudge did not reach the server: %s" % e
        return out
    out["nudged"] = status == 200
    if out["nudged"] and out.get("stalled"):
        out["detail"] = "stalled (%s) - nudged session %s to wake it" \
            % (out.get("stalled_reason"), state["session_id"])
    else:
        out["detail"] = "nudged session %s" % state["session_id"] if out["nudged"] \
            else "inbox written; the nudge returned HTTP %s" % status
    return out


# --- start / status ------------------------------------------------------------


def _already_running(name, lane):
    """The live lane's facts, or None. The two flags are what the duplicate-start
    message is built on: a server answering for a session is not the same thing
    as a lane running a phase."""
    state = live_session(lane)
    if state is None:
        return None
    canary = state.get("canary") if isinstance(state.get("canary"), dict) else {}
    return {"lane": name, "session_id": state.get("session_id"),
            "port": state.get("port"), "pid": state.get("pid"),
            "canary_denied": canary.get("denied") is True,
            "prompted": state.get("prompted") is True}


def cmd_start(repo, phase, brief, combo=DEFAULT_COMBO, l1_inbox=None,
              opencode_bin=None, password_env=ENV_PW, port=None, model=None,
              child_env=None):
    """Render -> serve -> canary -> first prompt for one phase, through oc_l1.
    A phase lane that is already live is REFUSED, not re-prompted: a second
    `start` would hand the same L2 a second brief and read as a fresh phase."""
    repo_p = Path(repo)
    if not repo_p.is_dir():
        raise L2Error("repo is not a directory: %s" % repo)
    brief_p = Path(brief)
    if not brief_p.is_file():
        raise L2Error("brief not found: %s (the first prompt is its contents, so "
                      "it must exist before the lane starts)" % brief)
    if not os.environ.get(password_env):
        raise L2Error("start refused: environment variable %s is not set - the "
                      "lane server password travels in it, by name only, and is "
                      "never invented here" % password_env)
    name = lane_name(repo_p, phase)
    inbox = resolve_l1_inbox(l1_inbox)
    lane = build_lane(name, repo_p, phase, brief_p, combo, inbox,
                      opencode_bin=opencode_bin, password_env=password_env,
                      port=port, model=model, child_env=child_env)
    running = _already_running(name, lane)
    if running:
        if running["canary_denied"] and running["prompted"]:
            raise L2Error("lane '%s' is already running (session %s on port %s, pid %s) "
                          "- send it work with l2_inbox, or l2_stop it first"
                          % (name, running["session_id"], running["port"],
                             running["pid"]))
        raise L2Error("lane '%s' has a server on port %s (pid %s, session %s) that is "
                      "not a running phase: it never cleared its canary (denied=%s, "
                      "prompted=%s), so its pilot sits idle and unguarded and l2_inbox "
                      "refuses to wake it - l2_stop '%s' first, then start again"
                      % (name, running["port"], running["pid"], running["session_id"],
                         running["canary_denied"], running["prompted"], name))

    scratch = Path(lane["scratch_dir"])
    scratch.mkdir(parents=True, exist_ok=True)
    prompt_file = Path(lane["first_prompt_file"])
    prompt_file.write_text(
        first_prompt_text(brief_p.read_text(encoding="utf-8", errors="replace"),
                          name, inbox), encoding="utf-8")
    cfg = write_config(lane)

    rc, output = run_oc_l1("start", name, cfg)
    # The canary MERGES heartbeat.json, so a wake marker from the lane this one
    # replaces (a restart, or a stop and start over the same scratch dir)
    # survived the launch into the new session. Nothing of this session was
    # woken for yet.
    _clear_wakes(lane)
    state = _read_state(lane["state_file"]) or {}
    canary = state.get("canary") if isinstance(state.get("canary"), dict) else {}
    result = {
        "lane": name, "phase": phase, "repo": str(repo_p), "combo": combo,
        "port": state.get("port") or lane["serve_port"],
        "session_id": state.get("session_id"), "pid": state.get("pid"),
        "exit_code": rc,
        "canary": {"denied": canary.get("denied"), "detail": canary.get("detail"),
                   "ts": canary.get("ts"), "plugin": canary.get("plugin_path")},
        "l1_inbox": str(inbox), "lane_inbox": lane["l2"]["lane_inbox"],
        "config": str(cfg), "prompt_file": str(prompt_file),
        "launcher_output": output.strip().splitlines()[-1:] or [""],
    }
    if rc == 0:
        result["detail"] = "live: the L2 has its brief; watch it with l2_status, " \
                           "send it work with l2_inbox"
    elif rc == 5:
        result["detail"] = "UNATTENDED-REFUSED: the canary was not denied, so the " \
                           "session never got the brief - fix the guard, l2_stop, " \
                           "then start again"
    else:
        result["detail"] = output.strip().splitlines()[-1] if output.strip() else "failed"
    return result, rc


def cmd_status(name):
    """live | silent | stalled | dead for the phase lane, plus the last canary
    result the state file still carries - an L2 that is silent with a denied
    canary is a different problem from one whose guard was never proved.

    The poll also advances the heartbeat past the canary (turn count / newest
    message ts, merged forward), so a live serve with recent turns reads
    `live`, not `dead`. A live session whose last turn errored, or that sits
    idle while a spawned child run already exited, reads `stalled` (exit 1):
    wake it with `resume`."""
    name = check_lane(name)
    lane = read_config(name)
    if lane is None:
        return {"lane": name, "verdict": "absent", "exit_code": 2,
                "detail": "no lane config at %s - start it with l2_start" % config_path(name)}, 2
    rc, output = run_oc_l1("status", name, config_path(name))
    verdict = {0: "live", 1: "silent", 2: "dead"}.get(rc, "error")
    state = _read_state(lane.get("state_file") or "") or {}
    out = {"lane": name, "verdict": verdict, "exit_code": rc,
           "port": state.get("port") or lane.get("serve_port"),
           "session_id": state.get("session_id"),
           "phase": (lane.get("l2") or {}).get("phase"),
           "repo": (lane.get("l2") or {}).get("repo"),
           "l1_inbox": (lane.get("l2") or {}).get("l1_inbox"),
           "lane_inbox": (lane.get("l2") or {}).get("lane_inbox")}
    canary = state.get("canary")
    if isinstance(canary, dict):
        out["canary"] = {"denied": canary.get("denied"), "detail": canary.get("detail"),
                         "ts": canary.get("ts")}
    _poll_activity(lane, out)
    if verdict in ("silent", "dead"):
        # A live serve with recent turns is live, not dead: the launcher's
        # verdict off a stale poll does not overrule the session itself.
        live_state = live_session(lane)
        if live_state is not None:
            msgs = _session_messages(
                live_state["port"], live_state["session_id"],
                os.environ.get(lane.get("password_env") or ENV_PW))
            _, newest = _turn_activity(msgs or [])
            if newest is not None and (time.time() - newest) <= _SILENT_WINDOW_S:
                verdict, rc = "live", 0
                out["verdict"], out["exit_code"] = verdict, rc
    if verdict in ("live", "silent"):
        info = _poll_stalled(name, out)
        if info and info.get("stalled"):
            out["verdict"], out["exit_code"] = "stalled", 1
            out["stalled"] = info
            out["detail"] = "stalled (%s): %s - wake it with l2_resume" \
                % (info.get("reason"), _wake_text(info))
            return out, 1
    if verdict == "live":
        out["detail"] = "working on the phase"
    elif verdict == "silent":
        out["detail"] = "no new assistant or tool activity in silent_minutes: " \
                        "read its inbox, or stop and start it"
    else:
        out["detail"] = (output.strip().splitlines() or ["server not answering"])[-1]
        out["detail"] += " - l2_stop clears the state file before a restart"
    return out, rc


# --- CLI -----------------------------------------------------------------------


def build_parser():
    ap = argparse.ArgumentParser(
        prog="oc_l2.py",
        description="L2 phase lane: one OpenCode orchestrator per phase, built on tools/oc_l1.py.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True,
                            metavar="{start,status,stop,inbox,resume}")
    p = sub.add_parser("start", help="start one phase lane")
    p.add_argument("--repo", required=True, help="the project the phase works in")
    p.add_argument("--phase", required=True, help="phase name (the lane's suffix)")
    p.add_argument("--brief", required=True, help="the phase brief - the first prompt's body")
    p.add_argument("--combo", default=DEFAULT_COMBO,
                   help="gateway combo that names the L2 model (default %s)" % DEFAULT_COMBO)
    p.add_argument("--inbox", default=None,
                   help="the L1 inbox REPORT/DONE lines land in (else %s)" % ENV_L1_INBOX)
    p.add_argument("--opencode-bin", default=None,
                   help="explicit opencode binary (else %s)" % ENV_BIN)
    p.add_argument("--password-env", default=ENV_PW,
                   help="NAME of the server password variable (default %s)" % ENV_PW)
    p.add_argument("--port", type=int, default=None,
                   help="force serve_port (default: the hash of the lane name)")
    st = sub.add_parser("status", help="live | silent | stalled | dead for a phase lane")
    st.add_argument("--lane", required=True)
    sp = sub.add_parser("stop", help="kill the lane's process group and its state file")
    sp.add_argument("--lane", required=True)
    ib = sub.add_parser("inbox", help="append a line to the lane's inbox and nudge it")
    ib.add_argument("--lane", required=True)
    ib.add_argument("--text", required=True)
    rs = sub.add_parser("resume", help="wake a stalled lane once, or restart it")
    rs.add_argument("--lane", required=True)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "start":
            result, rc = cmd_start(args.repo, args.phase, args.brief,
                                   combo=args.combo, l1_inbox=args.inbox,
                                   opencode_bin=args.opencode_bin,
                                   password_env=args.password_env, port=args.port)
        elif args.cmd == "status":
            result, rc = cmd_status(args.lane)
        elif args.cmd == "stop":
            result = cmd_stop(args.lane)
            # An orphan is not a stop: report it nonzero so a caller that only
            # reads the exit code cannot mistake a live server for a stopped one.
            rc = 0 if result.get("stopped") else 2
        elif args.cmd == "resume":
            result = cmd_resume(args.lane)
            # A wake that went out, a restart that landed, or a lane that was
            # never stuck are all success; anything else needs eyes on it.
            rc = 0 if (result.get("resumed") or result.get("restarted")
                       or result.get("noop")) else 2
        else:
            result = cmd_inbox(args.lane, args.text)
            # A refused nudge is not a delivered one: say so in the exit code as
            # well as in the JSON, so an L1 that reads neither cannot count the
            # line as handed over.
            rc = 2 if result.get("refused") else 0
    except (L2Error, oc_l1.LaneError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps(result, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
