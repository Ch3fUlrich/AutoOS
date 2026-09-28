"""token-rate: orchestrator tokens per merged change (RESTART spec §5 Metric).

The metric compares one orchestrator before and after the context-cap change, so
it needs both halves of the same actor's work:

    numerator   one weight per API *response* in the orchestrator's Claude Code
                transcripts, weighted input + output + cache_creation +
                0.1 * cache_read
    denominator first-parent merges into `main` whose subject names the
                orchestrator's branch prefix, by committer date

`tools/autoos_context.py` `fill_from_transcript` cannot be reused: it keeps only
the *last* usage record because a context fill is a snapshot. A rate needs the
sum over *all* records, which is what `iter_usage_records` here does.

Transcripts live where Claude Code puts them: `~/.claude/projects/<cwd slug>/*.jsonl`,
one file per session (ADR 0001). Sessions are selected by the record's `cwd`
field, so a lane sandbox deep under the orchestrator's directory still counts
while a sibling directory with a shared string prefix does not.

Router D-045 and the R5A3 answer: an `isSidechain` record (an in-session
subagent turn) is orchestrator cost and stays in the numerator; the subagent
columns report how large that part of it is. This host's client writes those
turns to `<project>/<session>/subagents/*.jsonl`, one level below the session
files, so `discover_transcripts` reads both depths — a record belongs to the
subagent view when its own `isSidechain` flag is set *or* it was read out of a
`subagents/` file. The R5A4 decision is what makes a record one *response*: the
client writes one record per content block, all repeating the same `message.id`,
the same `requestId` and the same usage, so `Totals.add` keys on
`response_identity` (the message id and request id, else the uuid), counts the
first record of a response, and lets a later duplicate only move it into the
subagent view — never add usage. A turn mirrored into a `subagents/` file is
still one turn's cost.

Stdlib only; read-only over the transcripts and `git log`. `--json` prints
`default` rather than the resolved `--projects-dir` when the user did not name
one — that path carries the operator's username, and a transcript root is not
part of the answer.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Spec 5 (restart-spec.md, D-040/D-044): cache reads are priced at a tenth of a
# fresh input token — measured on this host, 99.2 % of orchestrator tokens in
# 48 h were cache reads, so an unweighted sum is dominated by re-reads the
# provider does not bill as new input. The naive sum is still printed as a
# labelled diagnostic.
CACHE_READ_WEIGHT = 0.1

# The four usage fields that make up one turn's cost, per the transcript shape
# measured in a lane's project dir under ~/.claude/projects/.
_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                 "cache_read_input_tokens")

BIAS = ("this metric rewards shorter sessions and prices the orchestrator only "
        "(worker tokens are a gateway-side diagnostic, not in this number).")

_RELATIVE = re.compile(r"^\d+[smhdw]$")
_RELATIVE_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


@dataclass
class UsageRecord:
    """One assistant turn's cost, its session and where it ran.

    `sidechain` is the transcript's own `isSidechain` flag: the turn belongs to
    an in-session subagent rather than to the orchestrator's own reasoning.
    Router D-045 keeps those turns in the numerator — the parent session pays
    for them — and reports their share instead of filtering them; `measure` sets
    the flag on records read out of a `subagents/` file too, because there the
    directory is the client's claim and the flag is only the record's.

    `identity` is the API response the record is a piece of, as
    `response_identity` names it: the client writes one record per content
    block, so several records of one file — and one record mirrored into a
    `subagents/` file — share it and are one turn's cost. Empty means the
    transcript named no response at all, and such a record is its own.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    timestamp: datetime | None = None
    cwd: str = ""
    session: str = ""
    sidechain: bool = False
    identity: str = ""

    def weighted(self) -> float:
        return (self.input_tokens + self.output_tokens
                + self.cache_creation_input_tokens
                + CACHE_READ_WEIGHT * self.cache_read_input_tokens)

    def naive(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_creation_input_tokens + self.cache_read_input_tokens)


@dataclass
class Totals:
    """Aggregated usage over the records that survived the filters.

    The `subagent_*` fields are a *view onto* the same records the plain fields
    sum, never a subtraction from them: `weighted` already includes
    `subagent_weighted` (D-045).

    `counted` maps a record `identity` to whether the copy that got counted
    claimed a subagent, which is what lets a turn written to both a session file
    and a `subagents/` file count once.
    """

    records: int = 0
    weighted: float = 0.0
    naive: int = 0
    subagent_records: int = 0
    subagent_weighted: float = 0.0
    subagent_naive: int = 0
    session_ids: set = field(default_factory=set)
    counted: dict = field(default_factory=dict)

    def add(self, record: UsageRecord, session_fallback: str = "") -> None:
        """Count one response once, in the totals and in the subagent view.

        A record whose identity was already counted is another content block of
        the same response, or the same turn read out of a second file, so it adds
        nothing to `records`/`weighted`/`naive`; the one direction it can still
        move is *into* the subagent view, so a turn mirrored into a `subagents/`
        file is reported as one there, too.
        """
        weighted = record.weighted()
        naive = record.naive()
        session = record.session or session_fallback
        if session:
            self.session_ids.add(session)
        key = record.identity
        if key:
            claimed = self.counted.get(key)
            if claimed is not None:
                if record.sidechain and not claimed:
                    self.subagent_records += 1
                    self.subagent_weighted += weighted
                    self.subagent_naive += naive
                    self.counted[key] = True
                return
            self.counted[key] = record.sidechain
        self.records += 1
        self.weighted += weighted
        self.naive += naive
        if record.sidechain:
            self.subagent_records += 1
            self.subagent_weighted += weighted
            self.subagent_naive += naive

    def summary(self) -> "Summary":
        return Summary(records=self.records, weighted=round(self.weighted, 1),
                       naive=self.naive, sessions=len(self.session_ids),
                       subagent_records=self.subagent_records,
                       subagent_weighted=round(self.subagent_weighted, 1),
                       subagent_naive=self.subagent_naive)


@dataclass
class Summary:
    """The numerator: sessions, responses, weighted, naive, plus the subagent split.

    `records` counts API responses, not transcript lines: the client's habit of
    writing one line per content block is collapsed by `Totals.add`.
    """

    records: int = 0
    weighted: float = 0.0
    naive: int = 0
    sessions: int = 0
    subagent_records: int = 0
    subagent_weighted: float = 0.0
    subagent_naive: int = 0

    def subagent_share_pct(self):
        """Subagent weighted tokens as a percent of the weighted numerator."""
        if not self.weighted:
            return None
        return round(100.0 * self.subagent_weighted / self.weighted, 1)


def parse_moment(text):
    """An ISO UTC timestamp (`2026-09-28T07:14:19Z`, ms optional) -> aware datetime."""
    if text is None:
        return None
    value = str(text).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    if "+" not in value and "-" not in value[10:]:
        value += "+00:00"
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def resolve_window(since, until, now=None):
    """(start, end) aware datetimes. `48h`-style offsets count back from `end`.

    Absolute readings are ISO UTC; relative ones are `<N><s|m|h|d|w>` measured
    back from `--until` (or from `now` when `--until` is absent), which is what
    makes `--since 48h` mean the last 48 hours on a host with no clock argument.
    """
    end = parse_moment(until) if until else (now or datetime.now(timezone.utc))
    if str(since).strip().lower() == "now":
        start = end
    elif _RELATIVE.match(str(since).strip()):
        token = str(since).strip()
        seconds = int(token[:-1]) * _RELATIVE_UNIT[token[-1]]
        start = end - timedelta(seconds=seconds)
    else:
        start = parse_moment(since)
    return start, end


def cwd_matches(cwd, prefixes) -> bool:
    """Path-prefix match on whole segments, so `/x/AutoOS` is not `/x/AutoOS-lanes`."""
    path = str(cwd or "")
    for prefix in prefixes:
        head = str(prefix).rstrip("/")
        if path == head or path.startswith(head + "/"):
            return True
    return False


def _int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def response_identity(obj, message) -> str:
    """The identity of the API *response* a record is a piece of.

    R5A4's decision: the numerator counts each response once, so the key is the
    response's own name — `message.id`, plus the top-level `requestId` when the
    record carries one, because a retried response repeats the message id and is
    billed again. Measured 2026-09-28 over the R5a window, the L1-routing row's
    9,745 records are 4,964 responses: every one carries both keys, no message id
    ever spans two request ids, and the blocks of one response repeat its usage —
    242 of those responses carry the growing partial usage the client writes as
    blocks land, and the first record wins there too. A record with no
    `message.id` falls back to its `uuid`, so a transcript that names no response
    still counts each record once — and one that names neither is its own
    response (empty key, never deduped). The namespaces are prefixed so a uuid
    can never collide with a message id.
    """
    message_id = str(message.get("id") or "")
    if not message_id:
        uuid = str(obj.get("uuid") or "")
        return "uuid:%s" % uuid if uuid else ""
    request_id = str(obj.get("requestId") or "")
    if not request_id:
        return "msg:%s" % message_id
    return "msg:%s|%s" % (message_id, request_id)


def parse_record(line):
    """A JSONL line -> UsageRecord, or None unless it carries a usage object.

    Only `message.usage`'s own four token fields count; the real client mirrors
    the turn into `usage.iterations`, and summing both would double every record.
    A record without a parseable timestamp cannot be placed in a window and is
    dropped.
    """
    if not line:
        return None
    text = line.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    message = obj.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return None
    if not any(f in usage for f in _USAGE_FIELDS):
        return None
    try:
        moment = parse_moment(obj.get("timestamp"))
    except (ValueError, TypeError):
        moment = None
    if moment is None:
        return None
    return UsageRecord(
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_creation_input_tokens=_int(usage.get("cache_creation_input_tokens")),
        cache_read_input_tokens=_int(usage.get("cache_read_input_tokens")),
        timestamp=moment,
        cwd=str(obj.get("cwd") or ""),
        session=str(obj.get("sessionId") or obj.get("session_id") or ""),
        sidechain=bool(obj.get("isSidechain")),
        identity=response_identity(obj, message),
    )


def iter_usage_records(lines, cwd_prefixes=None, start=None, end=None):
    """Every usage record in `lines`, window- and cwd-filtered when asked.

    `fill_from_transcript` collapses a transcript to its last record; this
    iterator is the whole-session sum the rate metric needs.
    """
    for line in lines:
        record = parse_record(line)
        if record is None:
            continue
        if cwd_prefixes and not cwd_matches(record.cwd, cwd_prefixes):
            continue
        if start is not None and record.timestamp < start:
            continue
        if end is not None and record.timestamp >= end:
            continue
        yield record


def sum_records(records) -> Totals:
    """Aggregate an iterable of UsageRecord (or JSONL lines) into Totals."""
    totals = Totals()
    for item in records:
        record = item if isinstance(item, UsageRecord) else parse_record(item)
        if record is None:
            continue
        totals.add(record)
    return totals


def project_slug(path) -> str:
    """Claude Code's project-dir name for a cwd (same rule as autoos_context)."""
    return str(path).replace("/", "-").replace(".", "-")


def discover_transcripts(projects_dir, cwd_prefixes) -> list[Path]:
    """Transcript files in project dirs that could hold a matching cwd.

    The slug is a character-wise rewrite of the path, so every cwd under a
    prefix lands in a dir whose name starts with that prefix's slug — this
    prunes the scan without missing a session. The record's own `cwd` is still
    what decides membership.

    Two depths of the same project dir count: the session file
    `<project>/<session>.jsonl` and the in-session subagent files
    `<project>/<session>/subagents/*.jsonl`. Session files sort ahead of their
    own `subagents/` dir, which is what makes the dedup in `Totals.add` see the
    parent's copy first.
    """
    files = []
    try:
        dirs = sorted(p for p in Path(projects_dir).iterdir() if p.is_dir())
    except OSError:
        return files
    slugs = [project_slug(p) for p in cwd_prefixes]
    for directory in dirs:
        if not any(directory.name.startswith(slug) for slug in slugs):
            continue
        try:
            found = [p for p in directory.glob("*.jsonl") if p.is_file()]
            found += [p for p in directory.glob("*/subagents/*.jsonl") if p.is_file()]
        except OSError:
            continue
        files.extend(sorted(found, key=str))
    return files


def measure(projects_dir, cwd_prefixes, since, until, now=None) -> Summary:
    """Sum every matching record across the orchestrator's transcripts."""
    start, end = resolve_window(since, until, now=now)
    totals = Totals()
    for path in discover_transcripts(projects_dir, cwd_prefixes):
        # A subagent file is named after the agent, not after the session that
        # paid for it: its records carry the parent's `sessionId`, and for the
        # ones that carry none the session directory — never `agent-x` — is the
        # fallback, so an agent cannot invent a session.
        subagent_file = path.parent.name == "subagents"
        session = path.parent.parent.name if subagent_file else path.stem
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for record in iter_usage_records(fh, cwd_prefixes, start, end):
                    if subagent_file:
                        record.sidechain = True
                    totals.add(record, session)
        except OSError:
            continue
    return totals.summary()


def count_merges(repo, branch, branch_prefixes, since, until, now=None) -> int:
    """First-parent merges into `branch` in the window, named by a branch prefix.

    Attribution is by committer date (the moment the lane actually landed) and by
    a case-insensitive substring of the subject, which is the shape the lanes
    use: `Merge L1-routing/R6STOP 0732cf0: …`, `Merge l1/routing T2FREE …`. An
    empty `branch_prefixes` counts every merge — the whole-repo denominator.
    """
    start, end = resolve_window(since, until, now=now)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "log", "--first-parent", str(branch),
             "--merges", "--pretty=format:%ct\x1f%s"],
            capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise ValueError("git log failed for %s (%s): %s" % (repo, branch, detail.strip()))
    found = 0
    needles = [str(p).lower() for p in (branch_prefixes or [])]
    for line in proc.stdout.splitlines():
        stamp, _, subject = line.partition("\x1f")
        try:
            moment = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        except (ValueError, TypeError):
            continue
        if moment < start or moment >= end:
            continue
        if not needles or any(n in subject.lower() for n in needles):
            found += 1
    return found


def _fmt(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else "n/a"


def report(projects_dir=None, cwd_prefixes=(), repo=None, branch="main",
           branch_prefixes=(), since="48h", until=None, now=None) -> dict:
    """The §5 numbers: numerator, denominator, both rates, the subagent split,
    and the stated bias."""
    projects_dir_named = projects_dir is not None
    projects_dir = Path(projects_dir) if projects_dir_named else (
        Path.home() / ".claude" / "projects")
    start, end = resolve_window(since, until, now=now)
    totals = measure(projects_dir, list(cwd_prefixes), _fmt(start), _fmt(end), now=now)
    merges = count_merges(repo, branch, branch_prefixes, _fmt(start), _fmt(end),
                          now=now) if repo else 0
    weighted = round(totals.weighted, 1)
    return {
        "window": {"since": _fmt(start), "until": _fmt(end)},
        "sessions": totals.sessions,
        "records": totals.records,
        "weighted": weighted,
        "naive": totals.naive,
        "subagent_records": totals.subagent_records,
        "subagent_weighted": totals.subagent_weighted,
        "subagent_naive": totals.subagent_naive,
        "subagent_share_pct": totals.subagent_share_pct(),
        "merges": merges,
        "weighted_per_merge": round(weighted / merges, 1) if merges else None,
        "naive_per_merge": round(totals.naive / merges, 1) if merges else None,
        "bias": BIAS,
        "cache_read_weight": CACHE_READ_WEIGHT,
        "cwd_prefixes": list(cwd_prefixes),
        "branch_prefixes": list(branch_prefixes),
        "projects_dir": str(projects_dir) if projects_dir_named else "default",
        "repo": str(repo) if repo else None,
        "branch": branch,
    }


def _number(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return "{:,}".format(value) if isinstance(value, int) else "{:,.1f}".format(value)


def _percent(value) -> str:
    return "n/a" if value is None else "%.1f%%" % value


def format_text(data: dict) -> str:
    """The report, one labelled line per fact, greppable by an orchestrator."""
    return "\n".join([
        "window: %s .. %s" % (data["window"]["since"], data["window"]["until"]),
        "cwd-prefix: %s" % (", ".join(data["cwd_prefixes"]) or "(none)"),
        "branch-prefix: %s" % (", ".join(data["branch_prefixes"]) or "(all merges)"),
        "sessions: %d" % data["sessions"],
        "records: %d" % data["records"],
        "weighted: %s  (cache reads at %s)" % (
            _number(data["weighted"]), data["cache_read_weight"]),
        "naive: %s  (unweighted sum, diagnostic)" % _number(data["naive"]),
        "subagent-records: %d  (in-session subagent turns, counted above)"
        % data["subagent_records"],
        "subagent-weighted: %s" % _number(data["subagent_weighted"]),
        "subagent-naive: %s" % _number(data["subagent_naive"]),
        "subagent-share: %s  (subagent weighted / weighted)"
        % _percent(data["subagent_share_pct"]),
        "merges: %d" % data["merges"],
        "weighted-per-merge: %s" % _number(data["weighted_per_merge"]),
        "naive-per-merge: %s" % _number(data["naive_per_merge"]),
        "bias: %s" % data["bias"],
    ])


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(
        prog="autoos_tokenrate.py",
        description="Orchestrator tokens per merged change (RESTART spec §5). "
                    "Reads Claude Code transcripts and `git log`; writes nothing.")
    ap.add_argument("--cwd-prefix", dest="cwd_prefixes", action="append", default=[],
                    help="transcript cwd must start with this path (repeatable), "
                         "e.g. /home/<user>/code/AutoOS-lanes/L1-routing")
    ap.add_argument("--branch-prefix", dest="branch_prefixes", action="append",
                    default=[], help="merge subject must name this branch prefix "
                                     "(repeatable), e.g. 'L1-routing/' and 'l1/routing'; "
                                     "no flag means every merge counts")
    ap.add_argument("--since", required=True,
                    help="window start: ISO UTC timestamp or a relative offset (48h, 7d)")
    ap.add_argument("--until", help="window end (default: now); relative --since counts back from it")
    ap.add_argument("--repo", help="git checkout holding the branch (default: cwd)")
    ap.add_argument("--branch", default="main", help="branch the merges land on (default: main)")
    ap.add_argument("--projects-dir", help="transcript root (default: ~/.claude/projects)")
    ap.add_argument("--no-git", dest="git", action="store_false",
                    help="skip the denominator (merges 0, per-merge rates n/a)")
    ap.add_argument("--json", action="store_true", help="print one JSON object")
    args = ap.parse_args(argv)

    try:
        start, end = resolve_window(args.since, args.until)
    except (ValueError, TypeError):
        ap.error("--since/--until: not an ISO UTC timestamp or <N><s|m|h|d|w>: %r / %r"
                 % (args.since, args.until))
    if not args.cwd_prefixes:
        ap.error("--cwd-prefix is required (at least one)")
    repo = Path(args.repo) if args.repo else Path.cwd()
    if args.git and not repo.exists():
        ap.error("--repo does not exist: %s" % repo)
    try:
        data = report(projects_dir=args.projects_dir, cwd_prefixes=args.cwd_prefixes,
                      repo=repo if args.git else None, branch=args.branch,
                      branch_prefixes=args.branch_prefixes,
                      since=_fmt(start), until=_fmt(end))
    except ValueError as exc:
        print("token-rate: %s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(format_text(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
