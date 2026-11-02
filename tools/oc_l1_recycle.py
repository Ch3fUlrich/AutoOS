#!/usr/bin/env python3
"""oc_l1_recycle.py - token-usage recycle for OpenCode lanes (AO-OC-CONTEXT-RECYCLE, D-2267).

Claude sessions recycle through tools/hooks/context_guard.py; this is the same
rule for the OpenCode L1/L2 lanes `oc_l1.py start` / `oc_l2.py start` launch.
`oc_l1.py recycle --name LANE [--config PATH]` checks ONE lane ONCE and acts;
there is no loop in here - the caller schedules it (systemd timer, Scheduled
Task, the L1's own watcher). An L2 lane's generated config is an oc_l1 lane
config, so the same command measures it: --config <$AUTOOS_OCL2_STATE_DIR>/<lane>.json.

How a lane gets measured without an operator hand-building a schedule: tools/oc_l1_recycle_poll.py.
`start` prints the lane's recycle command (`recycle-command: ...`, also returned by oc_l2
start / the MCP oc_start); the examples in configuration/oc-l1/ (systemd timer, Windows task) run it;
and oc_l2 status / resume / inbox and the MCP oc_status run one check per poll. Checks of one lane
never overlap (<scratch_dir>/recycle.lock; the second one answers `busy`, exit 0).

Measure (tools/oc_l1_recycle_measure.py): GET /api/session/<id>/message?order=desc&limit=20 on the
lane's running `opencode serve`; the fill is input + cache read + cache write of the NEWEST
assistant message that carries usage - the definition context_guard owns (`fill_from_usage`,
reused, not copied; output tokens never count). Newest is decided by the records' timestamp /
id, never by list position. A turn still in flight (all-zero, no output) is passed over; a
newest usage that is present but not numeric is an error, never papered over by an older record.

Thresholds (D-1708 defaults): lane keys `ctx_handoff` / `ctx_hard` (positive ints,
handoff < hard, validated in oc_l1.validate_lane), else AUTOOS_CTX_HANDOFF /
AUTOOS_CTX_HARD, else 270000 / 300000 (context_guard.resolve_thresholds). A lane on a
1M-window model raises them in its own config; the registry
(catalog/ai-registry.json `context_advertised`) is read only for the WARNING
below, no registry field is added. opencode compacts at its window, so a hard
limit at or above it can never be reached: the result carries a `warning`.

Actions (one JSON object on stdout; the exit code is below):
  noop       fill below the handoff limit; nothing sent.
  waiting    no assistant usage record exists yet (a session `start` or a restart just created, or
             its first turn still running): nothing to measure, nothing sent, exit 0.
  reposted   the session a restart created never got its first prompt (the post failed): posted now.
  busy       another check of this lane is running: nothing done, exit 0.
  announced  fill >= handoff limit: ONE prompt posted into the session (write handoff +
             card to the lane's `handoff` path, stop taking new work). State file
             <ctx-guard state dir>/<session id>.json (context_guard.state_dir) records it.
  skipped    already announced for this session: nothing sent - a second check is silent.
             A fill that falls back below the handoff limit (compaction) clears the announcement,
             as context_guard's hook does, so the next climb is announced afresh.
  restarted  fill >= hard limit: a NEW session on the same lane server, first prompt = the
             handoff head, exactly what `start` posts (first_prompt_file is not used: a
             recycle restarts from the handoff, not the original brief). The state file
             gets the new session id (+ recycled_from); the old session is left idle, not
             deleted. `handoff_fresh` says whether the handoff file is newer than the
             announcement; a hard crossing with no prior announcement restarts anyway
             (the limit is hard) and says so in `warning`.
  cooldown   hard limit, but the session is a restart's made less than RESTART_MIN_AGE_S (15 min)
             ago: nothing done, exit 0 - the check after that restarts it (`recycles` counts them).
  deferred   hard limit but a merge is in progress: nothing done, retried next check.
             Merge markers: <scratch_dir>/merge.lock younger than 4 h (a session that
             merges by script touches it; an older one is stale and ignored), or a
             MERGE_HEAD / CHERRY_PICK_HEAD / REVERT_HEAD / rebase-merge / rebase-apply in the
             git dir of the lane cwd (worktree `.git` files are followed).
  error      see the exit code; NOTHING is restarted on any error (fail-safe).

An L2 lane (agent_layer == "L2") cannot write files (every write permission is denied), so its
announcement asks for an `l2_report` instead and its restart prompt is its brief (its `handoff`).

Exit codes: 0 ok (noop | waiting | announced | skipped | reposted | restarted | cooldown | busy) - 2 config error
(thresholds, password env unset) - 3 deferred: merge in progress (retry next check) - 4 session or
server unreachable, no session recorded, an HTTP step failed, handoff file missing, lock dir unusable -
6 usage PRESENT but not numeric / newest record not establishable (fail-safe: no prompt, no
restart). A second sequential check is safe.

Stdlib only; the tests never contact a real opencode.
"""

import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE / "hooks"))  # appended: a hook module must never shadow a tool

from oc_l1_render import LaneError  # noqa: E402
from context_guard import (  # noqa: E402,F401
    clear_state, fill_from_usage, read_state, resolve_thresholds, safe_id, write_state)
from oc_l1_recycle_measure import (  # noqa: E402,F401  (re-exported: the module's public surface)
    BAD, GIT_MARKERS, MERGE_LOCK_MAX_AGE_S, MERGE_LOCK_NAME, NONE, OK, fill_of, merge_in_progress,
    model_window)
from oc_l1_recycle_poll import lane_lock  # noqa: E402

MESSAGE_LIMIT = 20
RESTART_MIN_AGE_S = 900  # a restarted session is not restarted again sooner than this
EXIT_OK, EXIT_CONFIG, EXIT_DEFERRED, EXIT_FAILED, EXIT_USAGE = 0, 2, 3, 4, 6


def validate_ctx(lane, name):
    """The lane's own `ctx_handoff` / `ctx_hard` (None when unset), validated."""
    vals = []
    for key in ("ctx_handoff", "ctx_hard"):
        v = lane.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            raise LaneError("lane '%s': '%s' must be a positive integer (tokens)" % (name, key))
        vals.append(v)
    if None not in vals and vals[0] >= vals[1]:
        raise LaneError("lane '%s': 'ctx_handoff' must be below 'ctx_hard'" % name)
    return tuple(vals)


def thresholds(lane):
    """(handoff, hard): lane key, else AUTOOS_CTX_* environment, else 270000 / 300000."""
    return resolve_thresholds(lane.get("ctx_handoff"), lane.get("ctx_hard"))


# --- acting ---------------------------------------------------------------------


def announce_text(lane, ctx, handoff_at, hard_at):
    n, h, hard = ctx // 1000, handoff_at // 1000, hard_at // 1000
    head = ("CONTEXT-RECYCLE: your context is %dk tokens, past the %dk handoff limit. "
            "Finish the step in hand, do not start a merge you cannot finish, " % (n, h))
    if lane.get("agent_layer") == "L2":
        deliver = ("then send your handoff and card with the l2_report tool (your lane "
                   "cannot write files) and stop taking new work. At %dk this session is "
                   "restarted from your brief, %s." % (hard, lane["handoff"]))
    else:
        deliver = ("then write your handoff and card to %s, commit, and stop taking new "
                   "work. At %dk this session is restarted from that handoff."
                   % (lane["handoff"], hard))
    return head + deliver


def _now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _out(rc, lane, action, warnings=(), **fields):
    res = {"lane": lane["name"], "action": action}
    res.update(fields)
    warn = "; ".join(w for w in warnings if w)
    if warn:
        res["warning"] = warn
    print(json.dumps(res, sort_keys=True))
    if rc:
        print("oc_l1: recycle %s: %s" % (action, res.get("detail", "")), file=sys.stderr)
    return rc


def _announce(lane, port, password, sid, ctx, level, thr, http):
    """Post the handoff prompt once. The state file is written FIRST and removed when
    the post fails, so a write failure can never mean a second announcement."""
    write_state(sid, {"session_id": sid, "cwd": lane["cwd"], "ctx": ctx, "ts": time.time(),
                      "level": level, "lane": lane["name"], "handoff": lane["handoff"]})
    try:
        status, _ = http._request(port, "POST", "/api/session/%s/prompt" % sid,
                                  body={"text": announce_text(lane, ctx, *thr)}, password=password)
    except http.ServerDown:
        clear_state(sid)
        raise
    if status != 200:
        clear_state(sid)
        raise LaneError("handoff prompt failed: HTTP %d" % status)


def _post_first(lane, port, password, sid, http, serve):
    """The handoff head as the first prompt of `sid`, tried twice (a transient 5xx / reset)."""
    relaunch = dict(lane, first_prompt_file=None)  # the handoff, never the original brief
    for attempt in (1, 2):
        try:
            return serve._post_first_prompt(port, password, sid, relaunch)
        except (LaneError, http.ServerDown):
            if attempt == 2:
                raise


def _too_soon(state):
    """Why a restart is held back: the session is itself a restart's, made under RESTART_MIN_AGE_S
    ago (an oversized handoff head, or an agent that reloads a huge context at once, would
    otherwise be restarted on every tick, each restart abandoning the session mid-work)."""
    try:
        made = datetime.strptime(state["recycled_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (KeyError, TypeError, ValueError):
        return ""
    age = time.time() - made
    if 0 <= age < RESTART_MIN_AGE_S:
        return ("session %s was restarted %d s ago (minimum %d s between restarts); not restarted "
                "again yet" % (state.get("session_id"), age, RESTART_MIN_AGE_S))
    return ""


def _restart(lane, port, password, sid, state, http, serve):
    """New session on the same lane server, first prompt = the handoff head (as `start`)."""
    serve._handoff_head(lane["handoff"])  # raises LaneError BEFORE a session exists
    new_sid = serve._create_session(port, password, lane)
    st = dict(state, session_id=new_sid, prompted=False, recycled_from=sid,
              recycled_utc=_now_utc(), recycles=int(state.get("recycles") or 0) + 1)
    http._write_state(lane["state_file"], st)
    try:
        _post_first(lane, port, password, new_sid, http, serve)
    except (LaneError, http.ServerDown) as e:
        raise LaneError("new session %s was created but its first prompt failed (%s); the next "
                        "recycle check re-posts it" % (new_sid, http._scrub(str(e), password)))
    st["prompted"] = True
    http._write_state(lane["state_file"], st)
    return new_sid


def _repost(lane, port, password, sid, state, http, serve):
    """A restart's session whose first prompt never landed gets it now (state `prompted` False
    AND `recycled_from`: only a session this module made; a `start` session that was never
    prompted has not cleared its canary and is none of our business)."""
    _post_first(lane, port, password, sid, http, serve)
    http._write_state(lane["state_file"], dict(state, prompted=True))


def cmd_recycle(lane, args):
    """recycle --name X: see the module docstring. Returns the exit code."""
    import oc_l1_http as http  # lazy: oc_l1_http imports oc_l1, which imports this module
    import oc_l1_serve as serve

    thr = thresholds(lane)
    handoff_at, hard_at = thr
    if not 0 < handoff_at < hard_at:
        return _out(EXIT_CONFIG, lane, "error", detail=(
            "thresholds must satisfy 0 < handoff < hard, got %d / %d (lane keys ctx_handoff / "
            "ctx_hard, else AUTOOS_CTX_HANDOFF / AUTOOS_CTX_HARD)" % thr))
    window = model_window(lane)
    warn = ("ctx_hard %d >= the model window %d: opencode compacts first, so this limit is "
            "never reached; set ctx_handoff/ctx_hard below the window" % (hard_at, window)
            if hard_at >= window else "")
    password = os.environ.get(lane["password_env"])
    if not password:
        return _out(EXIT_CONFIG, lane, "error", [warn], detail=(
            "environment variable %s is not set; cannot authenticate" % lane["password_env"]))
    sid = None
    try:
        with lane_lock(lane) as got:
            if not got:
                return _out(EXIT_OK, lane, "busy", [warn],
                            detail="another recycle check of this lane is running")
            # read under the lock: a check that finished a restart while this one waited has
            # moved the lane to a new session, and acting on the old snapshot would make a second
            state = http._read_state(lane["state_file"])
            sid = state.get("session_id") if isinstance(state, dict) else None
            if not safe_id(sid):
                return _out(EXIT_FAILED, lane, "error", [warn], detail=(
                    "no usable session recorded in %s - run `start` first" % lane["state_file"]))
            port = state.get("port") or lane["serve_port"]
            try:
                return _check(lane, state, sid, port, password, thr, warn, http, serve)
            except OSError as e:  # the check's own failure, not the lock's
                return _out(EXIT_FAILED, lane, "error", [warn], session_id=sid,
                            detail=http._scrub("recycle check failed: %s" % e, password))
    except OSError as e:
        return _out(EXIT_FAILED, lane, "error", [warn], session_id=sid,
                    detail="recycle lock unusable: %s" % e)


def _check(lane, state, sid, port, password, thr, warn, http, serve):
    handoff_at, hard_at = thr
    if state.get("recycled_from") and state.get("prompted") is False:
        try:
            _repost(lane, port, password, sid, state, http, serve)
        except (LaneError, http.ServerDown) as e:
            return _out(EXIT_FAILED, lane, "error", [warn], session_id=sid, detail=http._scrub(
                "the restarted session still has no first prompt: %s" % e, password))
        return _out(EXIT_OK, lane, "reposted", [warn], session_id=sid)
    try:
        status, payload = http._request(
            port, "GET", "/api/session/%s/message?order=desc&limit=%d" % (sid, MESSAGE_LIMIT),
            password=password)
    except http.ServerDown as e:
        return _out(EXIT_FAILED, lane, "error", [warn], session_id=sid,
                    detail="server unreachable: %s" % e)
    if status != 200:
        return _out(EXIT_FAILED, lane, "error", [warn], session_id=sid,
                    detail="reading the session messages failed: HTTP %d" % status)
    items = http._data(payload)
    items = items.get("items") if isinstance(items, dict) else items
    kind, ctx = fill_of(items)
    if kind == NONE:
        return _out(EXIT_OK, lane, "waiting", [warn], session_id=sid, handoff_limit=handoff_at,
                    hard_limit=hard_at, detail="no assistant usage record yet - nothing to measure")
    if kind == BAD:
        return _out(EXIT_USAGE, lane, "error", [warn], session_id=sid, detail=(
            "token usage present but not numeric, or the newest assistant record cannot be "
            "established - nothing announced, nothing restarted"))
    level = "hard" if ctx >= hard_at else "handoff" if ctx >= handoff_at else None
    info = dict(session_id=sid, ctx=ctx, handoff_limit=handoff_at, hard_limit=hard_at)
    if level is None:
        with contextlib.suppress(OSError):  # back under the line (compaction): announce the next climb afresh
            clear_state(sid)
        return _out(EXIT_OK, lane, "noop", [warn], **info)

    announced = read_state(sid)
    try:
        if level == "handoff":
            if announced:
                return _out(EXIT_OK, lane, "skipped", [warn], level=level, **info)
            _announce(lane, port, password, sid, ctx, level, thr, http)
            return _out(EXIT_OK, lane, "announced", [warn], level=level, **info)

        reason = merge_in_progress(lane)  # a merge wins over the cooldown: deferred (exit 3), not cooldown
        if reason:
            return _out(EXIT_DEFERRED, lane, "deferred", [warn], level=level, detail=(
                "deferred: merge in progress (%s); retried on the next check" % reason), **info)
        held = _too_soon(state)
        if held:
            return _out(EXIT_OK, lane, "cooldown", [warn], level=level, detail=held, **info)
        fresh = False
        try:
            fresh = bool(announced) and Path(lane["handoff"]).stat().st_mtime >= announced["ts"]
        except (OSError, KeyError, TypeError):
            pass
        notes = [warn] if announced else [warn, "hard limit crossed with no handoff announcement: "
                                          "restarted from the handoff file as it stands"]
        new_sid = _restart(lane, port, password, sid, state, http, serve)
        with contextlib.suppress(OSError):  # the restart is done; a lost marker is not a failure
            write_state(sid, {"session_id": sid, "cwd": lane["cwd"], "ctx": ctx, "ts": time.time(),
                          "level": "hard", "lane": lane["name"], "restarted_as": new_sid})
        info["previous_session_id"], info["session_id"] = sid, new_sid
        return _out(EXIT_OK, lane, "restarted", notes, level=level, handoff_fresh=fresh, **info)
    except http.ServerDown as e:
        return _out(EXIT_FAILED, lane, "error", [warn], level=level, detail=(
            "server went away: %s" % e), **info)
    except (LaneError, OSError) as e:
        return _out(EXIT_FAILED, lane, "error", [warn], level=level, detail=http._scrub(
            str(e), password), **info)
