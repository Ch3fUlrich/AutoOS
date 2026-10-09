"""Writer verdict ledger: append-only JSONL of review verdicts (P3)."""
from __future__ import annotations
import argparse, datetime, importlib.util, io, json, os, re, sys, unicodedata
import autoos_clients as clients
import autoos_writer_rule as rule
VERDICTS = ("accepted", "reworked", "rejected")
FAILURES = ("syntax", "semantics", "secret", "scope", "tests-missing", "alert-policy", "other")
TASKS = ("ops", "code", "docs", "infra")
RISKS = ("R0", "R1", "R2", "R3")
KEYS = ("ts", "run_id", "verdict", "failure_class", "writer_client", "writer_model_served",
        "task_type", "risk", "reviewer", "fixer_model", "probe")
_RUNID = re.compile(r"[A-Za-z0-9._-]+$")
def default_path():
    return os.path.join(clients.state_dir(), "writer-ledger.jsonl")
def _dt(v=None):
    if v is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if isinstance(v, datetime.datetime):
        return v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)
    if type(v) in (int, float):
        return datetime.datetime.fromtimestamp(v, datetime.timezone.utc)
    try:
        o = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("bad ts %r" % (v,))
    return o if o.tzinfo else o.replace(tzinfo=datetime.timezone.utc)
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
    o["ts"] = _str("ts", t if t is not None else _dt().isoformat().replace("+00:00", "Z"))
    _dt(o["ts"])
    return o
def record(e, path=None):
    o = validate(e)
    t = path or default_path()
    d = os.path.dirname(os.path.abspath(t))
    if d:
        os.makedirs(d, exist_ok=True)
    fd = os.open(t, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(o, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return o
def load(path=None):
    rows, skip = [], 0
    try:
        h = io.open(path or default_path(), encoding="utf-8")
    except OSError:
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
def _pair(rows, model, task, ref):
    c = rule._canonical(model)
    return [(s, e) for s, e in ((_dt(e["ts"]), e) for e in rows)
            if rule._canonical(e.get("writer_model_served") or "") == c
            and e.get("task_type") == task and s <= ref]
def demoted(model, task_type, now=None, window_days=7, threshold=2, path=None):
    _en("task_type", task_type, TASKS)
    if type(model) is not str or not model.strip() or not rule._canonical(model):
        return False
    ref = _dt(now)
    start = ref - datetime.timedelta(days=window_days)
    rows = _pair(load(path)[0], model, task_type, ref)
    probe = [s for s, e in rows if e.get("probe") is True and e.get("verdict") == "accepted"]
    cut = max(probe) if probe else None
    return sum(1 for s, e in rows if e.get("verdict") == "rejected" and s >= start
               and (cut is None or s > cut)) >= threshold
def probe_pass(model, task_type, path=None, now=None, writer_client=None, reviewer=None):
    ref = _dt(now)
    pre = model.split("/")[0].strip() if type(model) is str and "/" in model else ""
    return record({"run_id": "probe-%s-%d" % (ref.strftime("%Y%m%dT%H%M%S%f"), os.getpid()),
                   "verdict": "accepted", "writer_client": writer_client or pre or "probe",
                   "writer_model_served": model, "task_type": task_type,
                   "reviewer": reviewer or "probe", "probe": True,
                   "ts": ref.isoformat().replace("+00:00", "Z")}, path=path)
def rollup(path=None, now=None):
    ref, o = _dt(now), {}
    for e in load(path)[0]:
        if _dt(e["ts"]) > ref:
            continue
        o.setdefault(e["writer_model_served"], {}).setdefault(
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
        c.add_argument("--task-type", default="ops", choices=TASKS)
        c.add_argument("--fixer", dest="fixer_model", default=None)
        c.add_argument("--risk", default=None, choices=RISKS)
        c.add_argument("--writer-client", default=None)
        c.add_argument("--writer-model", default=None)
    p = sub.add_parser("probe")
    p.add_argument("model")
    p.add_argument("task_type", choices=TASKS)
    sub.add_parser("rollup")
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
            print(json.dumps(rollup(), sort_keys=True, indent=1))
            return 0
        bad = demoted(a.model, a.task_type)
        print("demoted" if bad else "not-demoted")
        return 3 if bad else 0
    except ValueError as ex:
        print("ledger: %s" % ex, file=sys.stderr)
        return 2
if __name__ == "__main__":
    sys.exit(main())
