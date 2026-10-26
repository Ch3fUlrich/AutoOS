"""Writer verdict ledger: append-only JSONL of review verdicts (P3).

Each row records one review verdict (accepted / reworked / rejected) with
the writer model that served the text, the task type, and the reviewer.
demoted() flags a model/task pair after `threshold` rejections inside
`window_days` with no clearing probe pass in between.

P6e (judge): a rejection is evidence about a FAMILY, so a 'rejected' row names both families —
the writer's and the reviewer's, computed here — and one that cannot name two different ones is
neither written nor counted.

Provenance note: this module authenticates rows only as far as the CLI
writes them (timestamps and the two family fields are stamped here, never taken from the caller).
Probe authenticity beyond the CLI -- someone hand-editing the JSONL file
to plant or erase verdicts -- is out of scope: the ledger file's integrity
is the filesystem's (ownership, permissions, backups). Anything needing
tamper evidence must add signatures or a hash chain outside this module.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import unicodedata

import autoos_clients as clients
import autoos_run_records


VERDICTS = ("accepted", "reworked", "rejected")
FAILURES = ("syntax", "semantics", "secret", "scope", "tests-missing", "alert-policy", "other")
TASKS = ("ops", "code", "docs", "infra")
RISKS = ("R0", "R1", "R2", "R3")
KEYS = ("ts", "run_id", "verdict", "failure_class", "writer_client", "writer_model_served",
        "task_type", "risk", "reviewer", "fixer_model", "probe", "ref",
        "lane", "sha", "cost", "writer_family", "reviewer_family")
_RUNID = re.compile(r"[A-Za-z0-9._-]+$")
# P6: `ref` names the evidence a seat row came from (the lane sha, or the sha256 of
# the seat's answer), so a re-run over the same answer dedupes and a changed
# answer is a new row. Optional, so every row written before P6 still loads.
_REF = re.compile(r"[0-9a-f]{7,64}$")
# P6c: a production seat row names the lane it judged (`lane`, a git branch name) and
# the pushed sha it judged at (`sha`, one 40-hex commit). Both optional in the schema
# -- every row written before P6c still loads -- and required by `record_seat`.
_SHA = re.compile(r"[0-9a-f]{40}$")
_LANE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@/+-]*$")
_FUTURE_SKEW = datetime.timedelta(minutes=5)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
# A valid row is a handful of <=200-char fields; anything past this is garbage
# (an unbounded append of junk bytes) and is skipped unread rather than
# allocating a whole line's worth of text.
_MAX_LINE_BYTES = 65536
# The one git call that resolves the fleet's ledger home, and the env it runs with:
# built FROM SCRATCH so no caller `GIT_*` can steer it (the same allowlist discipline
# as autoos-agent's `_lane_worktree_git_env`), and with the config files shut off
# because a user or system `gitconfig` is not part of what names the main checkout.
_GIT_TIMEOUT = 10
_GIT_GUARDS = (("GIT_TERMINAL_PROMPT", "0"),
               ("GIT_CONFIG_PARAMETERS", ""),
               ("GIT_CONFIG_GLOBAL", os.devnull),
               ("GIT_CONFIG_SYSTEM", os.devnull))
_fleet_home = None


class LedgerError(ValueError):
    """Non-regular or unsafe ledger path (FIFO, directory, symlink, ...)."""


def _open_ledger_ro(resolved):
    """Read-only handle for a ledger path, or None when absent.

    Raises LedgerError for anything that is not a regular file, including
    FIFOs (opened O_NONBLOCK so the open itself never blocks), directories,
    and symlinks (O_NOFOLLOW). A path that does not exist at all reads as
    an empty ledger (None) instead.

    The handle is binary: decoding happens per line in load(), so one torn
    or invalid-UTF-8 record cannot hide the rows written after it.
    """
    if not os.path.lexists(resolved):
        return None

    if NOFOLLOW == 0 and os.path.islink(resolved):
        raise LedgerError("ledger %r is a symlink" % (resolved,))

    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | NOFOLLOW

    try:
        fd = os.open(resolved, flags)
    except FileNotFoundError:
        return None
    except OSError as ex:
        raise LedgerError("ledger %r: %s" % (resolved, ex)) from None

    try:
        st = os.fstat(fd)
    except OSError as ex:
        try:
            os.close(fd)
        except OSError:
            pass

        raise LedgerError("ledger %r: %s" % (resolved, ex)) from None

    if not stat.S_ISREG(st.st_mode):
        try:
            os.close(fd)
        except OSError:
            pass

        raise LedgerError("ledger %r is not a regular file" % (resolved,))

    if NOFOLLOW == 0:
        try:
            lst = os.lstat(resolved)
        except OSError as ex:
            try:
                os.close(fd)
            except OSError:
                pass

            raise LedgerError("ledger %r: %s" % (resolved, ex)) from None

        if stat.S_ISLNK(lst.st_mode) or (lst.st_ino, lst.st_dev) != (st.st_ino, st.st_dev):
            try:
                os.close(fd)
            except OSError:
                pass

            raise LedgerError("ledger %r is a symlink" % (resolved,))

    try:
        return os.fdopen(fd, "rb")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass

        raise


def _now():
    """Current UTC time. Module-level hook so hermetic tests can pin time."""
    return datetime.datetime.now(datetime.timezone.utc)


# P6e (judge): the seat-pair rule — a reviewer of a DIFFERENT family from the writer — guarded only
# a seat COUNTED for readiness, so a same-family rejection demoted its own family's model. Both
# families ride in the row now: computed where it is WRITTEN, re-read where the clock READS it.
_FAMILY_TOKEN = re.compile(r"[a-z0-9._-]{1,40}$")
_registry_cache = None


def _family_registry():
    """The catalog the family reads are answered from (`AUTOOS_REGISTRY_PATH` overrides the
    committed one), ONE read cached per process; an unreadable catalog answers None."""
    global _registry_cache
    path = os.environ.get("AUTOOS_REGISTRY_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "catalog",
        "ai-registry.json")
    if _registry_cache is None or _registry_cache[0] != path:
        try:
            with io.open(path, encoding="utf-8") as h:
                reg = json.load(h)
        except Exception:
            reg = None
        _registry_cache = (path, reg if type(reg) is dict else None)
    return _registry_cache[1]


def _family_of(spelling):
    """The family the registry names for one writer-model or reviewer spelling, else None: the
    resolver's ONE walk (`author_family`) that `review_status` uses; an unplaced name = nothing."""
    text = spelling.strip() if type(spelling) is str else ""
    if not text:
        return None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import autoos_resolver  # lazy: `autoos-agent.py` imports THIS module
        registry = _family_registry()
        family = autoos_resolver.author_family(text, registry)[0] if registry else None
    except Exception:
        return None
    return family if type(family) is str and _FAMILY_TOKEN.fullmatch(family) else None


def _reject_counts(e):
    """(P6e) Whether a 'rejected' row may move the demotion clock: both families present and
    different. Re-read at QUERY time, so a family-less row — every row from before P6e — counts 0."""
    w, r = e.get("writer_family"), e.get("reviewer_family")
    return bool(w) and bool(r) and w != r


def _stamp_families(o):
    """(P6e) Compute both families from the row's own spellings; refuse a rejection whose two
    families are not two different ones. A caller-passed family is dropped: the registry answers."""
    o.pop("writer_family", None)
    o.pop("reviewer_family", None)
    writer, reviewer = _family_of(o.get("writer_model_served")), _family_of(o.get("reviewer"))
    for k, v in (("writer_family", writer), ("reviewer_family", reviewer)):
        if v:
            o[k] = v
    if o["verdict"] == "rejected" and not _reject_counts(o):
        raise ValueError("a rejection counts only from a reviewer of another family: writer %s/%s, "
                         "reviewer %s/%s" % (o.get("writer_model_served"), writer or "unresolved",
                         o.get("reviewer"), reviewer or "unresolved"))


def _canonical_model(model_id):
    """One canonical key per model: lowercase, provider path stripped
    (everything up to the last '/'), ':tag' stripped."""
    return str(model_id or "").strip().lower().split("/")[-1].split(":")[0].strip()


def _model_match(a, b):
    """True when two model spellings name the same model: canonical keys
    equal, or one a prefix of the other at a '-' boundary with the shorter
    side at least 6 chars (so 'nemotron-3-super' matches
    'nemotron-3-super-120b-a12b' while a stub like 'gpt-4' never matches
    by prefix alone).

    Conservative on purpose: 'gemini-3.8-flash' and 'gemini-3.8-flash-lite'
    share such a prefix, so they count as the SAME model and a reject of
    either demotes both. Over-demotion is the safe failure mode here; the
    chain allow-list in autoos_writer_rule.py stays exact and is unaffected.
    """
    x, y = _canonical_model(a), _canonical_model(b)

    if not x or not y:
        return False

    if x == y:
        return True

    short, long = (x, y) if len(x) <= len(y) else (y, x)

    return len(short) >= 6 and long.startswith(short) and long[len(short)] == "-"


def _main_checkout_logs_dir():
    """The MAIN checkout's `logs/` through ONE audited git call, or None.

    A lane worktree's own `logs/` is discarded with the worktree, so a row written
    there is a verdict nobody will ever read: `git rev-parse --git-common-dir` names
    the shared `.git` from anywhere inside the checkout, and the parent of that is
    the main working tree. Refuses to guess: a failing, hanging, empty, doubled or
    undecodable answer, and a bare repo (whose common dir IS the tree it was asked
    about), all answer None and leave the caller to the fallback.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {"PATH": os.environ.get("PATH") or "/usr/bin:/bin",
           "HOME": os.environ.get("HOME") or "/", "LANG": "C"}
    for name, value in _GIT_GUARDS:
        env[name] = value
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--git-common-dir"],
                             capture_output=True, env=env, timeout=_GIT_TIMEOUT,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if out.returncode != 0:
        return None
    try:
        common = out.stdout.decode("utf-8").strip()
    except UnicodeError:
        return None
    if not common or "\n" in common or "\x00" in common:
        return None
    if not os.path.isabs(common):
        common = os.path.normpath(os.path.join(root, common))
    if common == root:
        return None
    return os.path.join(os.path.dirname(common), "logs")


def fleet_state_dir(cache=True):
    """ONE ledger home for the whole fleet (P6c) — the directory `default_path()`
    lives in.

    `AUTOOS_LEDGER_PATH` wins outright (its own directory), then `AUTOOS_STATE_DIR`
    (the redirect every hermetic test already uses), then the main checkout's `logs/`.
    NEVER raises: what cannot be resolved falls back to this checkout's state dir,
    because a ledger written to the wrong place is a defect while no ledger at all,
    over an unreadable repo, is the same defect the old resolver had. The git answer
    is cached per process (`cache=False` re-resolves it, which is what a test of the
    resolver itself needs).
    """
    led = os.environ.get("AUTOOS_LEDGER_PATH")
    if led:
        return os.path.dirname(os.path.abspath(led))
    state = os.environ.get("AUTOOS_STATE_DIR")
    if state:
        return state
    global _fleet_home
    if cache and _fleet_home:
        return _fleet_home
    home = _main_checkout_logs_dir() or clients.state_dir()
    if cache:
        _fleet_home = home
    return home


def default_path():
    """The ledger's home: `AUTOOS_LEDGER_PATH` wins outright (P6: tests point it
    at a temp file and never touch the real logs/writer-ledger.jsonl), else the
    FLEET's state dir (P6c) — the main checkout's, not this worktree's."""
    return os.environ.get("AUTOOS_LEDGER_PATH") or os.path.join(fleet_state_dir(),
                                                                "writer-ledger.jsonl")


def _dt(v=None):
    if v is None:
        return _now()

    if isinstance(v, datetime.datetime):
        return v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)

    if type(v) in (int, float):
        return datetime.datetime.fromtimestamp(v, datetime.timezone.utc)

    try:
        o = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("bad ts %r" % (v,))

    return o if o.tzinfo else o.replace(tzinfo=datetime.timezone.utc)


def _ts_iso(t, check_future):
    """Normalize a ts to a 'Z' string.

    A naive string or naive datetime carries no stated zone and is refused;
    only an explicit offset (or 'Z') is accepted. Epoch numbers are
    inherently UTC and stay accepted. With check_future (the record path),
    a ts later than now + _FUTURE_SKEW (5-minute clock-skew tolerance) is
    refused.
    """
    if isinstance(t, datetime.datetime):
        dt = t

        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            raise ValueError("ts needs an explicit timezone offset or 'Z', got naive %r" % (t,))
    elif type(t) in (int, float):
        dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    elif isinstance(t, str):
        s = t.strip()

        if not s:
            raise ValueError("ts: non-empty str, got %r" % (t,))

        iso = s[:-1] + "+00:00" if s[-1:] in ("Z", "z") else s

        try:
            dt = datetime.datetime.fromisoformat(iso)
        except ValueError:
            raise ValueError("bad ts %r" % (t,)) from None

        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            raise ValueError("ts needs an explicit timezone offset or 'Z', got %r" % (t,))
    else:
        raise ValueError("bad ts %r" % (t,))

    if check_future and dt > _now() + _FUTURE_SKEW:
        raise ValueError("ts %r is more than 5 minutes in the future" % (t,))

    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _str(n, v):
    if type(v) is not str or not v or len(v) > 200:
        raise ValueError("%s: 1..200-char str, got %r" % (n, v))

    for c in v:
        k = unicodedata.category(c)

        if k[0] == "C" or k in ("Zl", "Zp"):
            raise ValueError("%s has control char U+%04X" % (n, ord(c)))

    return v


def _en(n, v, ok):
    if v not in ok:
        raise ValueError("%s %r: expected %s" % (n, v, "|".join(ok)))

    return v


def _lane(v):
    """A lane name is a git branch name: _str's printable <=200 chars, and none of
    the shapes git itself refuses (a path separator at either end, `..`, `//`, a
    trailing `.` or the reserved `refs/` prefix)."""
    s = _str("lane", v)

    if not _LANE.fullmatch(s) or ".." in s or "//" in s or s[-1] in "/." or s.startswith("refs/"):
        raise ValueError("lane shape %r" % (s,))

    return s


def _cost(v):
    """A stated cost: a finite, non-negative number. Nothing else is a price —
    NaN, an infinity, a bool, a string or a negative all refuse the row rather
    than roll up as a number no billing read ever reported.
    P6d: an int past the float range (`10**400`) does not answer `math.isfinite`, it
    raises OverflowError, which sails past every `except ValueError` in the gate -- a
    number too large to be a price is still just a bad price."""
    if isinstance(v, bool) or type(v) not in (int, float):
        raise ValueError("cost: a finite non-negative number, got %r" % (v,))
    try:
        priced = math.isfinite(v) and v >= 0
    except (OverflowError, ValueError, TypeError):
        priced = False
    if not priced:
        raise ValueError("cost: a finite non-negative number, got %r" % (v,))

    return v


def validate(e, fill_ts=True):
    """Strict entry check (the record path).

    fill_ts=False is the load path: ts stays required and must be
    tz-aware, but far-future screening is left to the query --
    demoted()/rollup() compare against their own reference time and
    ignore rows stamped beyond it plus the skew tolerance.
    """
    if type(e) is not dict:
        raise ValueError("entry: dict, got %s" % type(e).__name__)

    x = sorted(k for k in e if k not in KEYS)

    if x:
        raise ValueError("entry: unknown %s" % ",".join(x))

    o = {"run_id": _str("run_id", e.get("run_id")), "verdict": _en("verdict", e.get("verdict"), VERDICTS),
         "writer_client": _str("writer_client", e.get("writer_client")),
         "writer_model_served": _str("writer_model_served", e.get("writer_model_served")),
         "task_type": _en("task_type", e.get("task_type"), TASKS)}

    if not _RUNID.fullmatch(o["run_id"]):
        raise ValueError("run_id shape %r" % (o["run_id"],))

    f = e.get("failure_class")

    if f is None and o["verdict"] != "accepted":
        raise ValueError("failure_class required for %r" % (o["verdict"],))

    if f is not None:
        o["failure_class"] = _en("failure_class", f, FAILURES)

    if e.get("risk") is not None:
        o["risk"] = _en("risk", e["risk"], RISKS)

    for k in ("reviewer", "fixer_model"):
        if e.get(k) is not None:
            o[k] = _str(k, e[k])

    # P6e: optional, so a row written before it still loads; shape-checked, so no claim rides past.
    for k in ("writer_family", "reviewer_family"):
        v = e.get(k)
        if v is not None:
            if type(v) is not str or not _FAMILY_TOKEN.fullmatch(v):
                raise ValueError("%s: 1..40-char lowercase family token, got %r" % (k, v))
            o[k] = v

    if e.get("probe") is not None:
        if type(e["probe"]) is not bool:
            raise ValueError("probe: bool, got %r" % (e["probe"],))

        o["probe"] = e["probe"]

    if e.get("ref") is not None:
        r = e["ref"]

        if type(r) is not str or not _REF.fullmatch(r):
            raise ValueError("ref: 7..64 lowercase hex, got %r" % (r,))

        o["ref"] = r

    if e.get("lane") is not None:
        o["lane"] = _lane(e["lane"])

    if e.get("sha") is not None:
        s = e["sha"]

        if type(s) is not str or not _SHA.fullmatch(s):
            raise ValueError("sha: 40 lowercase hex, got %r" % (s,))

        o["sha"] = s

    if e.get("cost") is not None:
        o["cost"] = _cost(e["cost"])

    t = e.get("ts")

    if t is None and not fill_ts:
        raise ValueError("ts is required")

    o["ts"] = _ts_iso(t if t is not None else _now(), check_future=fill_ts)

    return o


def _torn_tail(resolved):
    """`b"\\n"` when the ledger ends mid-record, else `b""` (P6c: strict JSONL).

    The old write prefixed every row with a newline, which made the file littered
    with blank lines and is not JSONL. A separator is only owed where a previous
    writer never finished its line. A tail that cannot be read (a raced rename, a
    share deny on Windows, a symlink a moment after the write handle was validated)
    is answered with no separator and NO row lost: `load` skips a torn line and keeps
    reading, while a stray blank line is skipped as nothing.
    """
    try:
        h = _open_ledger_ro(resolved)

        if h is None:
            return b""

        with h:
            size = os.fstat(h.fileno()).st_size

            if not size:
                return b""

            h.seek(size - 1)

            return b"" if h.read(1) == b"\n" else b"\n"
    except (OSError, ValueError):
        return b""


def record_seat(e, path=None):
    """One seat row from the PRODUCTION path: `record()`, and a row that cannot name
    both the lane it judged and the 40-hex sha it judged at is refused before the
    file is opened.

    WHY this is a separate door and not a default: a row with `lane=null sha=null` (the
    shape `ready` wrote before P6c) says nothing about which verdict applies to which
    pushed commit, so the demotion gate reads a pile of unattributed rows and can only
    guess. Refusing is cheaper than guessing.
    """
    lane, sha = e.get("lane"), e.get("sha")

    if type(lane) is not str or not lane or type(sha) is not str or not _SHA.fullmatch(sha):
        raise ValueError("lane and sha required")

    return record(e, path=path)


def record(e, path=None):
    o = validate(e)
    _stamp_families(o)  # P6e: before the file opens, so a refusal leaves no row behind
    t = path or default_path()
    d = os.path.dirname(os.path.abspath(t))

    if d:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as ex:
            raise LedgerError("ledger %r: %s" % (t, ex)) from None

    flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND
             | getattr(os, "O_NONBLOCK", 0) | NOFOLLOW)

    if NOFOLLOW == 0 and os.path.islink(t):
        raise LedgerError("ledger %r is a symlink" % (t,))

    fd = None
    failed = False

    try:
        try:
            fd = os.open(t, flags, 0o600)
        except OSError as ex:
            raise LedgerError("ledger %r: %s" % (t, ex)) from None

        try:
            st = os.fstat(fd)
        except OSError as ex:
            raise LedgerError("ledger %r: %s" % (t, ex)) from None

        if not stat.S_ISREG(st.st_mode):
            raise LedgerError("ledger %r is not a regular file" % (t,))

        if NOFOLLOW == 0:
            try:
                lst = os.lstat(t)
            except OSError as ex:
                raise LedgerError("ledger %r: %s" % (t, ex)) from None

            if stat.S_ISLNK(lst.st_mode) or (lst.st_ino, lst.st_dev) != (st.st_ino, st.st_dev):
                raise LedgerError("ledger %r is a symlink" % (t,))

        buf = _torn_tail(t) + json.dumps(o, sort_keys=True).encode("utf-8") + b"\n"

        try:
            n = os.write(fd, buf)
        except OSError as ex:
            raise LedgerError("ledger %r: %s" % (t, ex)) from None

        if n != len(buf):
            raise LedgerError("ledger %r: short write %d of %d bytes" % (t, n, len(buf)))
    except BaseException:
        failed = True
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError as ex:
                if not failed:
                    raise LedgerError("ledger %r: %s" % (t, ex)) from None

    return o


def load(path=None):
    """Read the ledger, returning (rows, skipped).

    Bytes are read and decoded one line at a time, with errors='replace': a
    record torn mid multi-byte character, a line of binary garbage or a NUL
    byte counts as one skipped row and the parse carries on, so the verdicts
    written after the damage stay visible. Nothing that lives in the file can
    raise out of here; only LedgerError from the open path can.
    """
    rows, skip = [], 0
    resolved = path or default_path()
    h = _open_ledger_ro(resolved)

    if h is None:
        return rows, skip

    with h:
        for raw in h:
            if not raw.strip():
                continue

            if len(raw) > _MAX_LINE_BYTES:
                skip += 1
                continue

            try:
                line = raw.decode("utf-8", "replace")
                j = json.loads(line)

                if type(j) is not dict:
                    raise ValueError("nope")

                rows.append(validate(j, fill_ts=False))
            except Exception:
                skip += 1

    return rows, skip


def _latest(rows):
    """Newest entry per run_id (ties: the later line wins).

    A run reworked then rejected counts once, as rejected; two records of
    the same reject count once.
    """
    best = {}

    for e in rows:
        s = _dt(e["ts"])
        cur = best.get(e["run_id"])

        if cur is None or s >= cur[0]:
            best[e["run_id"]] = (s, e)

    return best


def _pair(latest, model, task, ref):
    """Matched (ts, entry) pairs: canonical model match, same task, and no
    row stamped beyond ref plus the skew tolerance. Hand-edited future rows
    are ignored entirely -- never counted as rejections, never counted as
    clearing probes."""
    horizon = ref + _FUTURE_SKEW

    return [(s, e) for s, e in latest.values()
            if _model_match(e.get("writer_model_served") or "", model)
            and e.get("task_type") == task and s <= horizon]


def demoted(model, task_type, now=None, window_days=7, threshold=2, path=None):
    _en("task_type", task_type, TASKS)

    if type(model) is not str or not model.strip() or not _canonical_model(model):
        return False

    ref = _dt(now)
    start = ref - datetime.timedelta(days=window_days)
    rows = _pair(_latest(load(path)[0]), model, task_type, ref)
    probe = [s for s, e in rows if e.get("probe") is True and e.get("verdict") == "accepted"]
    cut = max(probe) if probe else None

    return sum(1 for s, e in rows if e.get("verdict") == "rejected" and _reject_counts(e)
               and s >= start and (cut is None or s > cut)) >= threshold


def probe_pass(model, task_type, path=None, writer_client=None, reviewer=None):
    ref = _now()
    pre = model.split("/")[0].strip() if type(model) is str and "/" in model else ""

    return record({"run_id": "probe-%s-%d" % (ref.strftime("%Y%m%dT%H%M%S%f"), os.getpid()),
                   "verdict": "accepted", "writer_client": writer_client or pre or "probe",
                   "writer_model_served": model, "task_type": task_type,
                   "reviewer": reviewer or "probe", "probe": True,
                   "ts": ref.isoformat().replace("+00:00", "Z")}, path=path)


def rollup(path=None, now=None, window_days=None):
    """Verdict counts per canonical model per task type.

    window_days=None (default) covers all time; otherwise only rows within
    the last window_days are counted. Only the latest entry per run_id
    counts, and rows stamped beyond now plus the skew tolerance are ignored.
    """
    if window_days is not None:
        if isinstance(window_days, bool) or not isinstance(window_days, (int, float)):
            raise ValueError("window_days: non-negative days, got %r" % (window_days,))

        if window_days < 0:
            raise ValueError("window_days: non-negative days, got %r" % (window_days,))

    ref = _dt(now)
    start = ref - datetime.timedelta(days=window_days) if window_days is not None else None
    horizon = ref + _FUTURE_SKEW
    o = {}

    for s, e in _latest(load(path)[0]).values():
        if s > horizon:
            continue

        if start is not None and s < start:
            continue

        if e["verdict"] == "rejected" and not _reject_counts(e):  # P6e: the demoted() gate again
            continue

        o.setdefault(_canonical_model(e["writer_model_served"]), {}).setdefault(
            e["task_type"], {"accepted": 0, "reworked": 0, "rejected": 0})[e["verdict"]] += 1

    return o


def _agent_module():
    """The runner's own module, loaded from this file's sibling."""
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "autoos_agent_mod", os.path.join(here, "autoos-agent.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_record(run_id):
    """The runner's `workers/<run_id>.json` as a dict, or None — never raised on.

    This is the one reader of what the run said about itself, so the fields a seat
    row copies (the client, the cost) all come from here rather than from a second
    walk over the store.
    """
    try:
        if type(run_id) is not str or not _RUNID.fullmatch(run_id):
            return None

        with io.open(os.path.join(autoos_run_records.workers_dir(), run_id + ".json"),
                     encoding="utf-8") as h:
            f = json.load(h)
    except Exception:
        return None

    return f if type(f) is dict else None


def run_cost(run_id):
    """The cost the writer's run record states, or None when it states no number.

    Never invented: a row that guesses a price makes every later billing read of
    that model look paid for, and a run that recorded nothing costs nothing to say.
    """
    return _cost_or_none((run_record(run_id) or {}).get("cost"))


def _cost_or_none(v):
    try:
        return _cost(v)
    except ValueError:
        return None


def writer_for_run(run_id):
    """(writer_client, writer_model_served) from the runner-private record, else None."""
    try:
        if type(run_id) is not str or not _RUNID.fullmatch(run_id):
            return None

        w = autoos_run_records.worker_writer(run_id)
    except Exception:
        return None

    if not isinstance(w, dict):
        return None

    m = w.get("model")

    if type(m) is not str or not m.strip() or m.strip() == "unresolved":
        return None

    p = w.get("provider")
    ok = type(p) is str and p.strip() and p.strip() != "unresolved"
    f = (run_record(run_id) or {}).get("client")
    c = f.strip() if type(f) is str and f.strip() else None

    return c, ("%s/%s" % (p.strip(), m.strip()) if ok else m.strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Writer verdict ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for n in ("accept", "reject", "rework"):
        c = sub.add_parser(n)
        c.add_argument("run_id")
        c.add_argument("--class", dest="failure_class", choices=FAILURES, required=(n != "accept"))
        c.add_argument("--reviewer", required=True)
        c.add_argument("--task-type", required=True, choices=TASKS)
        c.add_argument("--fixer", dest="fixer_model", default=None)
        c.add_argument("--risk", default=None, choices=RISKS)
        c.add_argument("--writer-client", default=None)
        c.add_argument("--writer-model", default=None)

    p = sub.add_parser("probe")
    p.add_argument("model")
    p.add_argument("task_type", choices=TASKS)
    r = sub.add_parser("rollup")
    r.add_argument("--days", type=float, default=None)
    d = sub.add_parser("demoted")
    d.add_argument("model")
    d.add_argument("task_type", choices=TASKS)
    a = ap.parse_args(argv)

    try:
        if a.cmd in ("accept", "reject", "rework"):
            c, s = a.writer_client, a.writer_model

            if c is None or s is None:
                f = writer_for_run(a.run_id)

                if f is not None:
                    c, s = c if c is not None else f[0], s if s is not None else f[1]

            if c is None or s is None:
                print("no run record for %s: pass --writer-client/--writer-model" % a.run_id,
                      file=sys.stderr)
                return 2

            e = {"run_id": a.run_id, "verdict": {"accept": "accepted", "reject": "rejected",
                                                 "rework": "reworked"}[a.cmd],
                 "writer_client": c, "writer_model_served": s,
                 "task_type": a.task_type, "reviewer": a.reviewer}

            for k in ("failure_class", "fixer_model", "risk"):
                if getattr(a, k) is not None:
                    e[k] = getattr(a, k)

            print(json.dumps(record(e), sort_keys=True))
            return 0

        if a.cmd == "probe":
            print(json.dumps(probe_pass(a.model, a.task_type), sort_keys=True))
            return 0

        if a.cmd == "rollup":
            print(json.dumps(rollup(window_days=a.days), sort_keys=True, indent=1))
            return 0

        bad = demoted(a.model, a.task_type)
        print("demoted" if bad else "not-demoted")
        return 3 if bad else 0
    except LedgerError as ex:
        print("ledger: %s" % ex, file=sys.stderr)
        return 4
    except ValueError as ex:
        print("ledger: %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
