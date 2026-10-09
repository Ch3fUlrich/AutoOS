"""Writer verdict ledger: append-only JSONL of review verdicts (P3).

Each row records one review verdict (accepted / reworked / rejected) with
the writer model that served the text, the task type, and the reviewer.
demoted() flags a model/task pair after `threshold` rejections inside
`window_days` with no clearing probe pass in between.

Provenance note: this module authenticates rows only as far as the CLI
writes them (timestamps are stamped here, never taken from the caller).
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
import os
import re
import stat
import sys
import unicodedata

import autoos_clients as clients


VERDICTS = ("accepted", "reworked", "rejected")
FAILURES = ("syntax", "semantics", "secret", "scope", "tests-missing", "alert-policy", "other")
TASKS = ("ops", "code", "docs", "infra")
RISKS = ("R0", "R1", "R2", "R3")
KEYS = ("ts", "run_id", "verdict", "failure_class", "writer_client", "writer_model_served",
        "task_type", "risk", "reviewer", "fixer_model", "probe")
_RUNID = re.compile(r"[A-Za-z0-9._-]+$")
_FUTURE_SKEW = datetime.timedelta(minutes=5)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class LedgerError(ValueError):
    """Non-regular or unsafe ledger path (FIFO, directory, symlink, ...)."""


def _open_ledger_ro(resolved):
    """Read-only handle for a ledger path, or None when absent.

    Raises LedgerError for anything that is not a regular file, including
    FIFOs (opened O_NONBLOCK so the open itself never blocks), directories,
    and symlinks (O_NOFOLLOW). A path that does not exist at all reads as
    an empty ledger (None) instead.
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
        return os.fdopen(fd, "r", encoding="utf-8")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass

        raise


def _now():
    """Current UTC time. Module-level hook so hermetic tests can pin time."""
    return datetime.datetime.now(datetime.timezone.utc)


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


def default_path():
    return os.path.join(clients.state_dir(), "writer-ledger.jsonl")


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

    if e.get("probe") is not None:
        if type(e["probe"]) is not bool:
            raise ValueError("probe: bool, got %r" % (e["probe"],))

        o["probe"] = e["probe"]

    t = e.get("ts")

    if t is None and not fill_ts:
        raise ValueError("ts is required")

    o["ts"] = _ts_iso(t if t is not None else _now(), check_future=fill_ts)

    return o


def record(e, path=None):
    o = validate(e)
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

        buf = b"\n" + json.dumps(o, sort_keys=True).encode("utf-8") + b"\n"

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
    rows, skip = [], 0
    resolved = path or default_path()
    h = _open_ledger_ro(resolved)

    if h is None:
        return rows, skip

    with h:
        for ln in h:
            if not ln.strip():
                continue

            try:
                j = json.loads(ln)

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

    return sum(1 for s, e in rows if e.get("verdict") == "rejected" and s >= start
               and (cut is None or s > cut)) >= threshold


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

        o.setdefault(_canonical_model(e["writer_model_served"]), {}).setdefault(
            e["task_type"], {"accepted": 0, "reworked": 0, "rejected": 0})[e["verdict"]] += 1

    return o


def writer_for_run(run_id):
    """(writer_client, writer_model_served) from the runner-private record, else None."""
    try:
        if type(run_id) is not str or not _RUNID.fullmatch(run_id):
            return None

        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "autoos_agent_mod", os.path.join(here, "autoos-agent.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        w = mod.worker_writer(run_id)
    except Exception:
        return None

    if not isinstance(w, dict):
        return None

    m = w.get("model")

    if type(m) is not str or not m.strip() or m.strip() == "unresolved":
        return None

    p = w.get("provider")
    ok = type(p) is str and p.strip() and p.strip() != "unresolved"

    try:
        with io.open(os.path.join(mod.workers_dir(), run_id + ".json"), encoding="utf-8") as h:
            f = json.load(h).get("client")
        c = f.strip() if type(f) is str and f.strip() else None
    except Exception:
        c = None

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
