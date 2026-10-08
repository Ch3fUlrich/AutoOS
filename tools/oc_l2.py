#!/usr/bin/env python3
"""oc_l2.py - the L2 phase lane (D-665 AO-L2-LAUNCH).

One OpenCode orchestrator per PHASE, built on the L1 launcher rather than
beside it: this module only resolves what an L2 lane IS (a lane name, the
gateway combo's model, the spawner-only MCP list, the guard's orchestrator
role, the brief as the first prompt), writes that as an oc_l1 lane config,
and hands the whole start/status pipeline to `tools/oc_l1.py` — the render,
the health poll, the bash-guard canary and the state file are the same code
the L1 lanes run, so a fix to the canary lands here for free.

Why it exists: an L1 that wants phase-sized work starts a lane with
`l2_start(repo, phase, brief)` instead of spawning writers itself. The L2
coordinates; it never edits code (every write permission is denied in the
rendered config, `L2_PERMISSIONS`, the bash-guard's `orchestrator` role in its
child env, and the same instruction in its first prompt) and every change goes
through a tier-3 run it spawns over the `autoos-agent` MCP — which is the ONLY
MCP server the lane enables, so an L2 has no editor, no filesystem MCP and no
second spawner.

Reporting (L2 -> L1): the lane's child env carries `AUTOOS_L1_INBOX` = the L1
inbox the resolved lane key `inbox_file` names, and the first prompt tells the
L2 to write its `REPORT` / `DONE` lines there. Work going the other way
(L1 -> L2) is `inbox`: one timestamped record appended to the lane's own inbox
(<$AUTOOS_RUN_DIR>/inbox/<lane>.md when set, else <state dir>/<lane>/inbox.md,
the path the append reports) and the live session nudged with the same POST
/api/session/{id}/prompt the launcher uses for its first prompt.

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
  status --lane l2-<repo>-<phase>
  stop   --lane l2-<repo>-<phase>
  inbox  --lane l2-<repo>-<phase> --text LINE

Exit codes: 0 ok - 2 config/validation/refusal (an unknown combo, a lane
already running, a missing binary or password env) - 4 server not healthy -
5 UNATTENDED-REFUSED (canary not denied; the codes are oc_l1's, forwarded).
"""

import argparse
import contextlib
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
# checks, and `bash` is fenced by the guard plugin in `orchestrator` role, which
# the canary proves before the lane is ever prompted.
L2_PERMISSIONS = {
    "edit": "deny", "write": "deny",
    "patch": "deny", "apply_patch": "deny",
    "task": "deny", "subagent": "deny",
    "read": "allow", "bash": "allow",
}

SKILL = "unattended-orchestration"
# The L2's contract, appended to every phase brief: what it may not do, the
# one route to a change, and where its reports land.
ROLE_LINES = (
    "You are the L2 orchestrator of this phase (%s). Load the `%s` skill first.",
    "You NEVER edit code and you never run a write against the repository: "
    "every file-mutating and spawn permission of this session (`edit`, `write`, "
    "`patch`, `task`) is denied and the shell guard runs in "
    "`orchestrator` role. Every change goes through the `autoos-agent` MCP: "
    "spawn tier-3 runs (writer, then a cross-family reviewer) and judge their "
    "reports.",
    "Report to the L1 inbox at `%s`: append one line per milestone prefixed "
    "`REPORT` and a final line prefixed `DONE` (or `BLOCKED`), each opened by "
    "a UTC `%%Y-%%m-%%dT%%H:%%M:%%SZ` timestamp - that stamp is what the reader "
    "parses, a line without it is invisible to it.",
)

_KILL_WAIT_S = 5.0
_KILL_HARD_WAIT_S = 2.0


class L2Error(Exception):
    """A refusal or a config problem: exit 2, nothing started."""


def _now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- naming ------------------------------------------------------------------


def slug(text):
    """A path/name fragment lowercased to [a-z0-9-]: a repo directory called
    `AutoOS CI` and one called `autoos-ci` name the same lane."""
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower())
    return out.strip("-")


def lane_name(repo, phase):
    """`l2-<repo>-<phase>`, truncated to the 32-char lane shape. The phase
    wins over the repo when they cannot both fit: one lane per phase is the
    unit that must stay distinct."""
    rslug, pslug = slug(Path(repo).name), slug(phase)
    if not rslug or not pslug:
        raise L2Error("repo and phase must each yield a [a-z0-9-] name "
                      "(got repo=%r phase=%r)" % (repo, phase))
    name = "%s%s-%s" % (LANE_PREFIX, rslug, pslug)
    if len(name) > LANE_MAX:
        room = LANE_MAX - len(LANE_PREFIX) - len(pslug) - 1
        if room < 4:
            raise L2Error("phase '%s' is too long to fit a %d-char lane name"
                          % (pslug, LANE_MAX))
        name = "%s%s-%s" % (LANE_PREFIX, rslug[:room], pslug)
        name = name.rstrip("-")
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


# --- the first prompt --------------------------------------------------------


def first_prompt_text(brief_text, name, l1_inbox):
    """The brief verbatim plus the fixed footer; the hint line is the L1
    launcher's own (one home for how MCP is reached under opencode)."""
    role = "\n".join([
        ROLE_LINES[0] % (name, SKILL),
        ROLE_LINES[1],
        ROLE_LINES[2] % str(l1_inbox),
    ])
    return "%s\n\n---\n%s\n\n%s" % (brief_text.rstrip(), role,
                                    oc_l1_serve.HINT_LINE)


# --- the lane spec -----------------------------------------------------------


def build_lane(name, repo, phase, brief, combo, l1_inbox, *, opencode_bin=None,
               password_env=ENV_PW, port=None, guard_dir=None, model=None):
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
        "guard_role": "orchestrator",
        "inbox_file": str(l1_inbox),
        "first_prompt_file": str(prompt_file),
        "scratch_dir": str(scratch),
        "state_file": str(scratch / ("oc-l1-%s.state.json" % name)),
        "heartbeat_file": str(scratch / "heartbeat.json"),
        # L2-only metadata (oc_l1 ignores keys it does not know): stop, status
        # and inbox read the phase back out of the generated config instead of
        # re-deriving it from arguments nobody passed.
        "l2": {"repo": str(repo), "phase": phase, "combo": combo,
               "brief": str(brief), "l1_inbox": str(l1_inbox),
               "lane_inbox": str(inbox)},
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


# --- stop / inbox ------------------------------------------------------------


def _child_argv(pid):
    """The PID's argv, token by token. POSIX only - Windows has no /proc, and
    there oc_l1's own taskkill /T /PID is the path (the state file is written
    0600 by the launcher alone)."""
    try:
        raw = Path("/proc/%d/cmdline" % pid).read_bytes()
    except (OSError, ValueError):
        return None
    return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p] or None


def _looks_like_the_lane(pid, lane):
    """Identity check before a kill: the recorded PID (never a name lookup) and
    a token of its argv that IS the lane's binary - either the exact path, or
    the same name with the extension dropped, because an `exec` chain replaces
    the wrapper's image (`fake_opencode.sh` becomes `fake_opencode.py`). A
    recycled PID only mentioning the word is refused: `grep opencode serve` and
    `tail notes-about-opencode.md` carry no such token. A heuristic against the
    common recycling case, not a proof; unreadable (Windows, already gone) is no
    evidence to refuse on."""
    argv = _child_argv(pid)
    if argv is None:
        return True
    bin_ = lane.get("opencode_bin") or ""
    stem = os.path.splitext(os.path.basename(bin_))[0]
    for token in argv:
        if token == bin_ or (stem and os.path.splitext(os.path.basename(token))[0] == stem):
            return True
    return False


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
    out = {"lane": name, "port": lane.get("serve_port") or oc_l1.derive_port(name),
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
        if not _looks_like_the_lane(pid, lane):
            out.update(stopped=False, orphan=True,
                       detail="pid %d is not the lane's opencode server - refused to kill "
                              "it, the state file stays" % pid)
            return out
        # start_new_session=True made the child its own group leader, so the
        # group is the tree: whatever `opencode serve` forks dies with it.
        pgid = _pgid(pid) or pid
        try:
            os.killpg(pgid, signal.SIGTERM)
            out["killed_pids"].append(pgid)
        except (ProcessLookupError, PermissionError, OSError):
            oc_l1_serve._kill_pid(pid)
            out["killed_pids"].append(pid)
    if not _wait_gone(pid, _KILL_WAIT_S) and os.name != "nt":
        pgid = _pgid(pid) or pid
        try:
            os.killpg(pgid, signal.SIGKILL)
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
    the server was mid-restart would otherwise have to be re-sent by hand."""
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
           "nudged": False}
    state = live_session(lane)
    if state is None:
        out["detail"] = "the lane is not live - the line is in the inbox and will " \
                        "be read when it starts; nudge it with l2_status/l2_stop"
        return out
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
    out["detail"] = "nudged session %s" % state["session_id"] if out["nudged"] \
        else "inbox written; the nudge returned HTTP %s" % status
    return out


# --- start / status ------------------------------------------------------------


def _already_running(name, lane):
    state = live_session(lane)
    if state is None:
        return None
    return {"lane": name, "session_id": state.get("session_id"),
            "port": state.get("port"), "pid": state.get("pid")}


def cmd_start(repo, phase, brief, combo=DEFAULT_COMBO, l1_inbox=None,
              opencode_bin=None, password_env=ENV_PW, port=None, model=None):
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
                      port=port, model=model)
    running = _already_running(name, lane)
    if running:
        raise L2Error("lane '%s' is already running (session %s on port %s, pid %s) "
                      "- send it work with l2_inbox, or l2_stop it first"
                      % (name, running["session_id"], running["port"], running["pid"]))

    scratch = Path(lane["scratch_dir"])
    scratch.mkdir(parents=True, exist_ok=True)
    prompt_file = Path(lane["first_prompt_file"])
    prompt_file.write_text(
        first_prompt_text(brief_p.read_text(encoding="utf-8", errors="replace"),
                          name, inbox), encoding="utf-8")
    cfg = write_config(lane)

    rc, output = run_oc_l1("start", name, cfg)
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
    """live | silent | dead for the phase lane, plus the last canary result the
    state file still carries - an L2 that is silent with a denied canary is a
    different problem from one whose guard was never proved."""
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
                            metavar="{start,status,stop,inbox}")
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
    st = sub.add_parser("status", help="live | silent | dead for a phase lane")
    st.add_argument("--lane", required=True)
    sp = sub.add_parser("stop", help="kill the lane's process group and its state file")
    sp.add_argument("--lane", required=True)
    ib = sub.add_parser("inbox", help="append a line to the lane's inbox and nudge it")
    ib.add_argument("--lane", required=True)
    ib.add_argument("--text", required=True)
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
        else:
            result, rc = cmd_inbox(args.lane, args.text), 0
    except (L2Error, oc_l1.LaneError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps(result, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
