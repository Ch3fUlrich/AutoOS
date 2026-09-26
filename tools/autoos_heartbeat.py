"""Read-only heartbeat check (R-heartbeat-02/03, R-pause-01 migrated into code:
briefs/common.md "Skill rules bind every spawned agent", operator
2026-09-26T13:43:33Z).

Two pure(ish) helpers behind `autoos-agent.py heartbeat` and the MCP
`heartbeat` tool:

    pause_state(inbox_path) -> {"active", "at", "text"}
    repo_branch_state(repo) -> {"unpushed": [(branch, ahead), ...], "dirty": n}

and one grouping helper, `branch_prefix`, both use to decide which unpushed
sibling branches are "owned" by the caller.

Nothing here writes anything: `repo_branch_state` runs only read-only git
plumbing (`rev-parse`, `for-each-ref`, `rev-list --count`, `status
--porcelain`); it never pushes, commits, fetches or creates a branch. A push
or a WIP-commit, when R-heartbeat-02 calls for one, is left to the caller -
this module only reports.
"""
from __future__ import annotations

import datetime
import io
import re
import subprocess

# Case-sensitive, whole word: operator lines write "PAUSE"/"RESUME" in caps
# (e.g. "PAUSE NOW", "PAUSE (operator..."), never lowercase or as part of
# another word ("PAUSED" must not count).
_PAUSE_RE = re.compile(r"\bPAUSE\b")
_RESUME_RE = re.compile(r"\bRESUME\b")
# a line that reports on a pause is not an order to pause
_NOT_AN_ORDER_RE = re.compile(r"lesson:|→ done")
_TIMESTAMP_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')

# The first-80-chars report the CLI/MCP print for an active pause.
_TEXT_PREVIEW = 80


def branch_prefix(branch: str) -> str:
    """The grouping prefix of a branch name: up to and including its last
    "/" (a namespaced branch, e.g. "L1-backlog/agy-tarball" ->
    "L1-backlog/"), else up to and including its last "-" (e.g.
    "worktree-agent-a957350031c1fae19" -> "worktree-agent-", grouping every
    sibling worktree lane's branch), else the whole name when it has neither.
    """
    idx = branch.rfind("/")
    if idx < 0:
        idx = branch.rfind("-")
    return branch[: idx + 1] if idx >= 0 else branch


def _parse_iso(text: str) -> datetime.datetime:
    """A minimal ISO-8601 UTC parser (mirrors autoos-agent.py's own
    `parse_now`, duplicated here so this module never imports the CLI's
    hyphenated-filename module). A naive string is read as UTC."""
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def parse_inbox_line(line: str):
    """(when, text) for one inbox line (format `<ISO time> <text>`), or None
    when the line is blank, has no text after its timestamp, or its leading
    token is not a parseable ISO-8601 timestamp.

    A reply line (one that carries a "→ done:" marker somewhere in its
    text) is parsed exactly the same way - it is still scanned for PAUSE/
    RESUME below, since an operator may write either word inside a reply.
    """
    line = line.rstrip("\n").rstrip("\r")
    if not line.strip():
        return None
    parts = line.split(None, 1)
    if len(parts) < 2:
        return None
    try:
        when = _parse_iso(parts[0])
    except ValueError:
        return None
    return when, parts[1]


_NONE_PAUSE = {"active": False, "at": None, "text": None}


def session_start(transcript_path: str | None):
    """The first ``timestamp`` in a session transcript (JSONL), to the second,
    or None when the file is missing or has none. A relaunch is the resume:
    pause_state() ignores a PAUSE line older than this."""
    if not transcript_path:
        return None
    try:
        with io.open(transcript_path, encoding="utf-8") as fh:
            for raw in fh:
                found = _TIMESTAMP_RE.search(raw)
                if found:
                    return _parse_iso(found.group(1).split(".")[0] + "Z")
    except (OSError, ValueError):
        return None
    return None


def pause_state(inbox_path: str | None, since=None) -> dict:
    """The newest PAUSE/RESUME state of an inbox file (R-pause-01,
    R-heartbeat-03: "a hard stop, checked every heartbeat and before every
    launch").

    PAUSE is active when the newest line whose text contains the word PAUSE
    is newer than the newest line containing the word RESUME, or there is no
    RESUME line at all. A line older than `since` (the session start, see
    session_start()) and a `lesson:` / `→ done` line never count as a PAUSE:
    the relaunch after a pause is its resume. Lines are ordered by their own parsed timestamp, not
    file order, so an inbox is read correctly even if a line was appended
    out of order. A missing/unreadable inbox, or one with no PAUSE line
    (win or lose to a RESUME), is `{"active": False, "at": None, "text":
    None}`; an active pause also carries the winning line's own timestamp
    (`at`, ISO 8601 UTC) and the first 80 characters of its text (`text`).
    """
    if not inbox_path:
        return dict(_NONE_PAUSE)
    try:
        with io.open(inbox_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return dict(_NONE_PAUSE)
    newest_pause = None  # (when, text)
    newest_resume = None  # when
    for raw in lines:
        parsed = parse_inbox_line(raw)
        if parsed is None:
            continue
        when, text = parsed
        if _RESUME_RE.search(text) and (newest_resume is None or when > newest_resume):
            newest_resume = when
        if since is not None and when < since:
            continue
        if (_PAUSE_RE.search(text) and not _NOT_AN_ORDER_RE.search(text)
                and (newest_pause is None or when > newest_pause[0])):
            newest_pause = (when, text)
    if newest_pause is None:
        return dict(_NONE_PAUSE)
    if newest_resume is not None and newest_resume > newest_pause[0]:
        return dict(_NONE_PAUSE)
    at = newest_pause[0].isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"active": True, "at": at, "text": newest_pause[1][:_TEXT_PREVIEW]}


def _git(repo: str, *args: str):
    """One read-only git plumbing call in `repo`; (stdout.strip(), returncode).
    Never raises - a bad repo or a bad ref just gets a non-zero rc."""
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    except OSError:
        return "", 1
    return r.stdout.strip(), r.returncode


_EMPTY_REPO_STATE = {"unpushed": [], "dirty": 0}


def repo_branch_state(repo: str) -> dict:
    """{"unpushed": [(branch, ahead), ...], "dirty": n} for one repo
    (R-heartbeat-02: "push every branch you own with new commits").

    `unpushed` covers two kinds of branch: every local branch whose
    configured upstream exists and is behind it (`ahead` = the count of
    `git rev-list <upstream>..<branch>`, i.e. commits the branch has that its
    own upstream does not), plus every local branch with NO upstream at all
    whose name shares the current branch's `branch_prefix()` - a sibling
    worktree lane's branch, or the current branch itself when it has never
    been pushed (`ahead` = `git rev-list <branch> --not --remotes`, commits no
    remote-tracking ref has ever seen). A branch in either group with 0 ahead
    commits is left out. `dirty` is the number of lines `git status
    --porcelain` reports (tracked and untracked changes alike).

    Never raises: a path that is not a git checkout (or has no commits yet)
    reports `{"unpushed": [], "dirty": 0}`.
    """
    current, rc = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        return dict(_EMPTY_REPO_STATE)
    prefix = branch_prefix(current)
    refs_out, rc = _git(repo, "for-each-ref", "refs/heads",
                        "--format=%(refname:short)\t%(upstream:short)")
    unpushed = []
    if rc == 0:
        for line in refs_out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            branch = parts[0]
            upstream = parts[1] if len(parts) > 1 else ""
            if upstream:
                out, cnt_rc = _git(repo, "rev-list", "--count", "%s..%s" % (upstream, branch))
            elif branch.startswith(prefix):
                out, cnt_rc = _git(repo, "rev-list", "--count", branch, "--not", "--remotes")
            else:
                continue
            if cnt_rc != 0:
                continue
            try:
                n = int(out or "0")
            except ValueError:
                continue
            if n > 0:
                unpushed.append((branch, n))
    status_out, rc = _git(repo, "status", "--porcelain")
    dirty = len(status_out.splitlines()) if rc == 0 and status_out else 0
    return {"unpushed": unpushed, "dirty": dirty}
