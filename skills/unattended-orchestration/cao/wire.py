"""Fixed TASK / RESULT envelopes.

With three or more layers, prose between agents becomes the dominant token cost
and the dominant source of drift: each hop paraphrases the last, and by layer
three the worker is acting on a summary of a summary.

The caps here are enforced in code rather than requested in a prompt, and
:func:`render_task` deliberately has **no parameter** that could carry plan
prose. A worker receives ``plan_ref`` and reads the contract itself; it is never
re-told the plan by an intermediary.
"""

from __future__ import annotations

import re

TASK_CAP = 3200  # characters, roughly 800 tokens
RESULT_CAP = 1600  # roughly 400 tokens
NOTES_CAP = 300

STATUSES = ("ok", "blocked", "needs_revision")


def truncate(text, cap: int) -> str:
    """Cut to ``cap`` with a VISIBLE marker. Silent truncation hides evidence."""
    text = text or ""
    if len(text) <= cap:
        return text
    dropped = len(text) - cap
    return text[:cap] + f" …[truncated {dropped} chars]"


def render_task(
    *,
    task_id,
    goal,
    plan_ref,
    files,
    guard,
    expect,
    depth,
    max_depth,
    budget_tokens,
    reply_to,
    leaf_pool=None,
    leaf_model=None,
    leaf_effort=None,
) -> str:
    """Keyword-only: ten positional fields would silently transpose.

    ``leaf_model``/``leaf_effort`` carry the routing ladder's decision to the
    agent that acts on it. Without them the ladder is computed, printed, and
    dropped: a supervisor picks whatever model it feels like, and "effort is
    determined by the task" is true only of a number nobody reads.
    """
    body = (
        f"TASK {task_id}\n"
        f"goal:       {truncate(goal, 200)}\n"
        f"plan_ref:   {plan_ref}\n"
        f"files:      {', '.join(files) if files else '(none)'}\n"
        f"acceptance: {guard} -> {expect}\n"
        f"depth:      {depth}/{max_depth}\n"
        f"budget:     {budget_tokens} tokens\n"
        f"reply:      RESULT -> {reply_to}\n"
    )
    if leaf_model:
        # Pool and model on SEPARATE lines. Measured live 2026-09-11: a single
        # "use_model: antigravity/gemini-3.8-flash-high" was passed verbatim as
        # the model id, and the worker died with "There's an issue with the
        # selected model (antigravity/gemini-3.8-flash-high). It may not exist".
        # The model id was valid; the slash was ours.
        effort = f" effort={leaf_effort}" if leaf_effort else ""
        if leaf_pool:
            body += f"use_pool:   {leaf_pool}\n"
        body += f"use_model:  {leaf_model}{effort}\n"
    return truncate(body, TASK_CAP)


#: Terminal captures are ANSI: colour, cursor moves, hyperlinks, wrap markers.
#: A pane is not a message, and the envelope has to be found INSIDE one.
#: Built from chr() rather than escapes so the pattern says what it matches.
_ESC, _BEL, _CR, _BSL = chr(27), chr(7), chr(13), chr(92)
_ANSI = re.compile(
    _ESC + r"\[[0-9;?]*[ -/]*[@-~]"                                # CSI
    + "|" + _ESC + r"\][^" + _BEL + "]*(?:" + _BEL + "|"
    + _ESC + re.escape(_BSL) + ")"                                 # OSC .. BEL/ST
    + "|" + _CR                                                    # redraw
)

#: Cursor positioning: these STAND IN for runs of spaces in a rendered pane.
_ANSI_SPACE = re.compile(_ESC + r"\[[0-9;]*[GC]")

_RESULT_HEAD = re.compile(r"^RESULT\s+\S+\s+status=", re.M)


def strip_ansi(text: str) -> str:
    """ANSI out, word boundaries KEPT.

    A tmux capture is a RENDERED view: runs of spaces are emitted as cursor-move
    escapes (``ESC[42G``, ``ESC[3C``) rather than spaces. Deleting those the way
    a naive stripper does welds words together -- measured on a live pane, the
    supervisor's line came back as "I'vedelegatedtoworkerfc1cc684". An envelope
    header would weld the same way into "RESULTp1status=ok" and stop matching,
    so the positioning escapes become a single space instead of nothing.
    """
    # Order matters: positioning escapes become spaces FIRST, or the
    # general stripper deletes them and the words weld together.
    return _ANSI.sub("", _ANSI_SPACE.sub(" ", text or ""))


def find_result(text: str) -> dict:
    """Parse the LAST RESULT envelope inside a terminal capture.

    :func:`parse_result` is strict on purpose: a *message* is an envelope, and
    accepting prose around it invites agents to bury a status in commentary.
    But the ``--terminal`` path reads a tmux PANE -- thousands of characters of
    ANSI and scrollback with the envelope somewhere in the middle. Measured
    against a live session on 2026-09-11: parse_result matched line 0, found a
    banner, and returned "no claim" for every terminal there will ever be. A
    check that can never fire is worse than no check, because its silence reads
    as evidence of honesty.

    The LAST envelope wins: an agent that reports twice has concluded twice, and
    the conclusion is the later one. Raises ValueError when there is none --
    silence is not a claim and must not be scored as one.
    """
    clean = strip_ansi(text)
    starts = [m.start() for m in _RESULT_HEAD.finditer(clean)]
    if not starts:
        raise ValueError("no RESULT envelope in this output")
    return parse_result(clean[starts[-1]:])


def render_result(*, task_id, status, evidence, files_changed, notes, cost) -> str:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    cost_s = " ".join(f"{k}={v}" for k, v in (cost or {}).items())
    body = (
        f"RESULT {task_id} status={status}\n"
        f"evidence:      {truncate(evidence, 400)}\n"
        f"files_changed: {', '.join(files_changed) if files_changed else '(none)'}\n"
        f"notes:         {truncate(notes, NOTES_CAP)}\n"
        f"cost:          {cost_s}\n"
    )
    return truncate(body, RESULT_CAP)


_HEAD = re.compile(r"^RESULT\s+(?P<id>\S+)\s+status=(?P<status>\S+)")


def parse_result(text: str) -> dict:
    lines = (text or "").strip().splitlines()
    match = _HEAD.match(lines[0]) if lines else None
    if not match:
        raise ValueError(
            "not a RESULT envelope: expected a 'RESULT <id> status=<s>' header"
        )
    status = match.group("status")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")

    out = {
        "task_id": match.group("id"),
        "status": status,
        "evidence": "",
        "files_changed": [],
        "notes": "",
        "cost": "",
    }
    for line in lines[1:]:
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "files_changed":
            out[key] = (
                [] if value in ("", "(none)") else [p.strip() for p in value.split(",")]
            )
        elif key in out:
            out[key] = value
    return out
