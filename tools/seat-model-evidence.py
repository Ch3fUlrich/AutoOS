#!/usr/bin/env python3
"""seat-model-evidence.py — deterministic per-turn model evidence for a
review seat run (lane SEAT-EVIDENCE, decisions D-260 / D-261).

Two modes, and the KIND names what the evidence actually is:

  request-side (keyless opencode path, the default)
    opencode stores NO provider-response model in its session db; the
    `session_message.data.model` it writes is the model the CLI was started
    with, i.e. the REQUEST-side / session model. So a free seat is verified by
    a THREE-WAY match (D-261): all three must equal --expect, and each other:
      (a) the autoos-agent run record's requested free model
          (job.json argv `--free-model`, else request.model; or the workers
          record's `model`) — supply with `--run-id <id>`;
      (b) the opencode CLI `--model` from the sandbox's opencode.log
          (`message="cli starting"` line only);
      (c) the per-turn `data.model.{providerID,id}` on EVERY assistant row of
          the seat's session (session_message).
    The output lists each source so a reader can see where they diverge.

  response-side (`--gateway`: the paid/gateway path, high tier, D-261)
    reads OmniRoute call logs READ-ONLY through the container helper and
    requires every in-window row's served model (`model` + `provider`) to
    equal --expect — this is genuine provider-response evidence.

PRIVACY: the db also holds `credential`, `account`, `control_account`,
`permission`, `kv` tables and prompt/response text. This tool only ever
SELECTs session_id, seq, data from session_message WHERE type='assistant' and
reads only argv/request.model/started/ended from the run record and only the
`--model` token from the cli-starting log line. No prompt content, no key,
no token, no account ever reaches stdout or the JSON. The gateway helper
authenticates INSIDE the container; no key material is passed or printed here.

Reads are read-only on the ORIGINAL db: the db plus any -wal are copied to a
temp dir and the copy is opened, so WAL-only rows are included (immutable=1 is
never used — it hides the WAL). The original is never written.

Usage:
    python3 tools/seat-model-evidence.py <sandbox-dir> \
        --expect <provider/model> --run-id <id> [--session <id>] [--out <f>]
    python3 tools/seat-model-evidence.py <sandbox-dir> \
        --expect <provider/model> --gateway (--run-id <id> | --since <iso> \
        [--until <iso>]) [--key <apiKeyName>]
    With --gateway --run-id the window is [record start, record end] padded
    +-5 s (a record without an end time is a REFUSAL, exit 3 — never "until
    now"); rows are attributed by apiKeyName (default: the spawner key) and,
    when known to both sides, by session id.

Output line:
    evidence <path> sha256 <hex> kind <request-side|response-side> \
all_match <true|false> turns <n>

Exit codes: 0 every source present and matching (turns>=1); 2 any mismatch, any
assistant row with a missing model/providerID/id (a gap), a missing required
source, or gateway rows of other models sharing the window (cannot attribute);
3 the db is unreadable / the seat session has no assistant rows at all / the
gateway fetch failed (status only is printed) / the run record has no end time
(unverifiable). JSON goes to --out (default <sandbox>.seat-evidence.json).
"""
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PROG = "seat-model-evidence"

# Explicit, documented provider-prefix spellings -> the providerID opencode
# records. Add entries here only with evidence from a real db; an unmapped
# prefix is compared as written. This is the ONLY normalisation performed.
PROVIDER_PREFIX_ALIASES = {
    "opencode": "opencode",
}

# Gateway call-log `provider` spellings -> the canonical provider prefix,
# measured ONLY from real rows (live read-only probe 2026-10-01): the paid
# spark seat logs provider == "spark-1.3-contributor". Connection-scoped
# values like "openai-compatible-chat-<uuid>" are deliberately NOT mapped —
# fuzzy prefix matching over uuids would attribute other traffic (D-261 G5).
GW_PROVIDER_ALIASES = {
    "spark-1.3-contributor": "omniroute",
}

# apiKeyName every autoos-agent spawner gateway call measured with (2026-10-01).
DEFAULT_GATEWAY_KEY = "autoos-local"

# +-5 s window padding: absorbs run-record vs gateway-clock skew so this run's
# own rows are never lost at the boundary (stated in the output, D-261 G3).
WINDOW_PAD_SECONDS = 5

# The single field (a)/(c) key; (b) is the CLI flag. Kept explicit so the
# output can name exactly what was read.
DB_FIELD = ("session_message[type=assistant].data.model.providerID"
            " + .data.model.id")

DEFAULT_LOGS_ROOT = "/home/s/code/AutoOS/logs"


def die(msg, code=3):
    print(f"{PROG}: {msg}", file=sys.stderr)
    sys.exit(code)


def now_iso():
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds"))


def resolve_db(sandbox_arg):
    """Return (sandbox_dir, opencode_db_path) for a sandbox dir or data dir."""
    p = Path(sandbox_arg)
    if not p.is_dir():
        die(f"sandbox dir not found: {p}")
    if p.name.endswith(".opencode-data"):
        base = p
        sandbox = Path(str(p)[: -len(".opencode-data")])
    else:
        base = Path(str(p) + ".opencode-data")
        sandbox = p
    db = base / "opencode" / "opencode.db"
    if not db.is_file():
        die(f"opencode db not found: {db}")
    return sandbox, base, db


def normalise_expect(expected):
    if "/" not in expected:
        die(f"--expect must be provider/model, got: {expected}")
    prefix, _, mid = expected.partition("/")
    prefix = PROVIDER_PREFIX_ALIASES.get(prefix.lower(), prefix)
    return f"{prefix}/{mid}"


# --- source (a): the run record's requested free model ----------------------

def read_run_record_model(run_id, logs_root):
    """Return (model_str_or_None, path_str_or_None) for the requested free
    model of a run. Reads ONLY argv / request.model from job.json, else the
    `model` field from the workers record. Never returns other fields."""
    root = Path(logs_root)
    job = root / "agents" / run_id / "job.json"
    if job.is_file():
        try:
            d = json.loads(job.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        argv = d.get("argv") or []
        if isinstance(argv, list) and "--free-model" in argv:
            nxt = argv[argv.index("--free-model") + 1:][:1]
            if nxt and isinstance(nxt[0], str):
                return nxt[0], str(job)
        req = d.get("request")
        if isinstance(req, dict) and isinstance(req.get("model"), str):
            return req["model"], str(job)
    workers = root / "workers" / (run_id + ".json")
    if workers.is_file():
        try:
            d = json.loads(workers.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        if isinstance(d.get("model"), str):
            return d["model"], str(workers)
    return None, None


def _iso_from_epoch(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return datetime.datetime.fromtimestamp(
            v, datetime.timezone.utc).isoformat(timespec="seconds")
    return None


def read_run_record_window(run_id, logs_root):
    """(start_iso, end_iso) from the run record — workers/<id>.json carries
    ISO started/ended, job.json carries EPOCH SECONDS (converted; D-261 G3).
    Either may be None; a missing end means the run is not finished and the
    CALLER must refuse, never default the window end to 'now'."""
    root = Path(logs_root)
    s = e = None
    job = root / "agents" / run_id / "job.json"
    if job.is_file():
        try:
            d = json.loads(job.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        s = _iso_from_epoch(d.get("started"))
        e = _iso_from_epoch(d.get("ended"))
    workers = root / "workers" / (run_id + ".json")
    if workers.is_file():
        try:
            d = json.loads(workers.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        if isinstance(d.get("started"), str):
            s = d["started"]
        if isinstance(d.get("ended"), str):
            e = d["ended"]
    return s, e


def read_run_record_session(run_id, logs_root):
    """The opencode session id the run record knows, if any (workers first,
    then job.json request). None = unknown -> window attribution falls back
    to the every-row-model-must-match rule (D-261 G4)."""
    root = Path(logs_root)
    for path in (root / "workers" / (run_id + ".json"),
                 root / "agents" / run_id / "job.json"):
        if not path.is_file():
            continue
        try:
            d = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for cand in (d.get("session_id"), d.get("session"),
                     (d.get("request") or {}).get("session")
                     if isinstance(d.get("request"), dict) else None):
            if isinstance(cand, str) and cand:
                return cand
    return None


# --- source (b): the opencode CLI --model from opencode.log -----------------

_CLI_RE = re.compile(r"--model\\?\",\\?\"([^\"\\,]+)")


def read_cli_model(base):
    """Return (model_str_or_None, path_or_None). Reads ONLY the cli-starting
    line of <base>/opencode/log/opencode.log and only its --model token."""
    log = base / "opencode" / "log" / "opencode.log"
    if not log.is_file():
        return None, None
    try:
        with log.open("r", errors="replace") as fh:
            for line in fh:
                if 'message="cli starting"' not in line:
                    continue
                m = _CLI_RE.search(line)
                if m:
                    return m.group(1), str(log)
    except OSError:
        return None, None
    return None, str(log)


# --- source (c): per-turn db model (WAL-safe, read-only on original) --------

def _open_copy(db):
    """Copy db + -wal (never the live -shm) to a temp dir and open the copy,
    so a WAL-only row is recovered. Returns (conn, tmpdir)."""
    tmp = tempfile.mkdtemp(prefix="seatdb-")
    name = Path(db).name
    shutil.copy2(db, Path(tmp) / name)
    wal = Path(str(db) + "-wal")
    if wal.exists():
        shutil.copy2(wal, Path(tmp) / (name + "-wal"))
    con = sqlite3.connect(str(Path(tmp) / name))
    return con, tmp


def read_turns(db):
    """Return (per_session, unreadable). per_session is
    {session_id: {"turns": [model_str,...], "gaps": [seq,...], "maxseq": int}}
    for every session that has assistant rows; a gap is an assistant row whose
    model / providerID / id is missing or None. NEVER skips such a row."""
    try:
        con, tmp = _open_copy(db)
    except sqlite3.Error as exc:
        die(f"cannot open db: {exc}")
    sessions = {}
    try:
        rows = con.execute(
            "SELECT session_id, seq, data FROM session_message"
            " WHERE type='assistant' ORDER BY session_id, seq").fetchall()
    except sqlite3.Error as exc:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)
        die(f"cannot read session_message: {exc}")
    con.close()
    shutil.rmtree(tmp, ignore_errors=True)
    for sid, seq, raw in rows:
        s = sessions.setdefault(sid, {"turns": [], "gaps": [], "maxseq": -1})
        s["maxseq"] = max(s["maxseq"], seq if seq is not None else -1)
        model = None
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("model"), dict):
            provider = data["model"].get("providerID")
            mid = data["model"].get("id")
            if isinstance(provider, str) and isinstance(mid, str):
                model = f"{provider}/{mid}"
        if model is None:
            s["gaps"].append(seq)
        else:
            s["turns"].append(model)
    return sessions


def pick_session(sessions, want):
    """The seat's session: --session <id> if given, else the newest (max seq).
    Returns (session_id, {all session_ids -> summary})."""
    if not sessions:
        return None, {}
    if want:
        if want not in sessions:
            die(f"--session {want} has no assistant rows")
        chosen = want
    else:
        chosen = max(sessions, key=lambda k: sessions[k]["maxseq"])
    return chosen, sessions


# --- gateway (response-side): OmniRoute call logs ---------------------------

def _parse_rows(text):
    """Accept every shape the gateway helper can print: a bare JSON array of
    rows, or an object wrapping them in logs/data/rows (D-261 G1)."""
    try:
        b = json.loads(text or "[]")
    except json.JSONDecodeError:
        die("gateway fetch returned unparseable output")
    if isinstance(b, dict):
        b = b.get("logs") or b.get("data") or b.get("rows") or []
    if not isinstance(b, list):
        die("gateway fetch returned no row list")
    return b


def gateway_fetch(sandbox):
    """Fetch call-log rows (list of dicts) READ-ONLY. If the env
    SEAT_EVIDENCE_GATEWAY_CMD is set it is the fake runner (used by tests);
    otherwise shell out to the container helper. Never sends or prints a key;
    a failed fetch exits 3 naming only the HTTP status (D-261 G1)."""
    fake = os.environ.get("SEAT_EVIDENCE_GATEWAY_CMD")
    if fake:
        proc = subprocess.run(shlex.split(fake), capture_output=True, text=True)
        if proc.returncode != 0:
            die(f"gateway fetch failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()[:200]}")
        return _parse_rows(proc.stdout)
    container = os.environ.get("OMNIROUTE_CONTAINER", "autoos-omniroute")
    js = (
        "import { apiFetch } from '/app/bin/cli/api.mjs';"
        "const all=[];let off=0;"
        "try{for(;;){const r=await apiFetch('/api/usage/call-logs?limit=1000"
        "&offset='+off+'&excludeTests=1');"
        "if(!r.ok){console.error('HTTP '+r.status);process.exit(3);}"
        "const b=(typeof r.json==='function')?await r.json():r;"
        "const rows=(Array.isArray(b)?b:((b&&(b.logs||b.data||b.rows))||[]));"
        "if(!rows.length)break;all.push(...rows);off+=rows.length;"
        "if(rows.length<1000)break;}}"
        "catch(e){console.error('HTTP '+(e&&e.status||'fetch-error'));"
        "process.exit(3);}"
        "process.stdout.write(JSON.stringify(all));"
    )
    proc = subprocess.run(
        ["docker", "exec", "-w", "/app", container, "node",
         "--input-type=module", "-e", js],
        capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"gateway fetch failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:200]}")
    return _parse_rows(proc.stdout)


def _parse_ts(s):
    """ISO timestamp (Z or +HH:MM offset, ms optional) -> aware UTC datetime;
    None when unparseable. String comparison of mixed formats is unsound —
    '21:30:00.500Z' sorts BEFORE '21:30:00Z' (D-261 G2)."""
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _shift_iso(s, seconds):
    dt = _parse_ts(s)
    if dt is None:
        return s
    return (dt + datetime.timedelta(seconds=seconds)).isoformat(
        timespec="seconds")


def gateway_evidence(rows, expect, start, end, key, session):
    """Filter to the run window (datetimes, D-261 G2), the spawner's key name
    and, when the run record knows it and the rows carry one, the session id
    (G4). Returns (turns, cids, window_rows, mismatch_count) where
    window_rows counts everything attributed to this window+key+session."""
    start_dt, end_dt = _parse_ts(start), _parse_ts(end)
    turns = []
    cids = []
    window_rows = 0
    mismatch = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        ts = _parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        if start_dt and ts < start_dt:
            continue
        if end_dt and ts > end_dt:
            continue
        if key and r.get("apiKeyName") != key:
            continue
        row_sess = r.get("sessionId")
        if session and isinstance(row_sess, str) and row_sess != session:
            continue
        window_rows += 1
        provider = r.get("provider")
        mid = r.get("model")
        served = None
        if isinstance(provider, str) and isinstance(mid, str):
            served = f"{GW_PROVIDER_ALIASES.get(provider, provider)}/{mid}"
        if served != expect:
            mismatch += 1
        cid = r.get("correlationId")
        if isinstance(cid, str):
            cids.append(cid)
        turns.append({"index": len(turns), "served_model": served,
                      "requested_model": r.get("requestedModel"),
                      "status": r.get("status"), "correlation_id": cid,
                      "field": "call_log.model + call_log.provider"})
    if not turns:
        die("no gateway call-log rows in the run window")
    return turns, cids, window_rows, mismatch


def write_output(doc, args, sandbox):
    out = Path(args.out) if args.out else Path(
        str(sandbox) + ".seat-evidence.json")
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"evidence {out} sha256 {sha} kind {doc['kind']} "
          f"all_match {'true' if doc['all_match'] else 'false'} "
          f"turns {doc['turn_count']}")
    return out


def run_gateway(args, sandbox, expect):
    start = args.since
    end = args.until
    pad = 0
    session = None
    if args.run_id and not (start or end):
        start, end = read_run_record_window(args.run_id, args.logs_root)
        if not start:
            die(f"no start time in the run record for {args.run_id}")
        if not end:
            die("run still active or no end time")
        start = _shift_iso(start, -WINDOW_PAD_SECONDS)
        end = _shift_iso(end, WINDOW_PAD_SECONDS)
        pad = WINDOW_PAD_SECONDS
        session = read_run_record_session(args.run_id, args.logs_root)
    if not start and not end:
        die("--gateway needs --run-id (a finished record with start and end) "
            "or --since/--until")
    key = args.key if args.key is not None else DEFAULT_GATEWAY_KEY
    rows = gateway_fetch(sandbox)
    turns, cids, window_rows, mismatch = gateway_evidence(
        rows, expect, start, end, key, session)
    warnings = []
    if mismatch:
        warnings.append("window shared with other traffic")
        print(f"{PROG}: warning: window shared with other traffic "
              f"({mismatch} of {window_rows} attributed rows served another "
              f"model)", file=sys.stderr)
    doc = {
        "kind": "response-side",
        "sandbox": str(sandbox),
        "expected": expect,
        "window": {"since": start, "until": end, "key": key,
                   "padding_seconds": pad, "session": session},
        "run_id": args.run_id,
        "window_rows": window_rows,
        "turns": turns,
        "turn_count": len(turns),
        "correlation_ids": cids,
        "warnings": warnings,
        "all_match": mismatch == 0,
        "generated_at": now_iso(),
    }
    if pad:
        print(f"{PROG}: window padded +-{WINDOW_PAD_SECONDS}s around the "
              "run record start/end", file=sys.stderr)
    write_output(doc, args, sandbox)
    sys.exit(0 if mismatch == 0 else 2)


def run_keyless(args, sandbox, base, db, expect):
    sessions = read_turns(db)
    if not sessions:
        die("no assistant-turn data found in session_message")
    chosen, _ = pick_session(sessions, args.session)
    sess = sessions[chosen]
    turns_all = sess["turns"]
    gaps = sess["gaps"]

    run_model, run_path = (None, None)
    if args.run_id:
        run_model, run_path = read_run_record_model(args.run_id, args.logs_root)
    cli_model, cli_path = read_cli_model(base)

    db_all_match = bool(turns_all) and all(m == expect for m in turns_all)
    no_gaps = not gaps
    run_ok = run_model is not None and run_model == expect
    cli_ok = cli_model is not None and cli_model == expect
    all_match = db_all_match and no_gaps and run_ok and cli_ok

    turns = [{"index": i, "served_model": m, "field": DB_FIELD}
             for i, m in enumerate(turns_all)]
    gap_rows = [{"seq": s, "served_model": None, "field": DB_FIELD}
                for s in gaps]
    doc = {
        "kind": "request-side",
        "sandbox": str(sandbox),
        "data_path": str(db),
        "expected": expect,
        "run_id": args.run_id,
        "session": chosen,
        "sources": {
            "run_record": {"model": run_model, "path": run_path},
            "cli": {"model": cli_model, "path": cli_path},
            "db": {"model_matches": db_all_match,
                   "distinct": sorted(set(turns_all)),
                   "field": DB_FIELD},
        },
        "other_sessions": [
            {"session": sid, "turn_count": len(sessions[sid]["turns"]),
             "gaps": len(sessions[sid]["gaps"])}
            for sid in sorted(sessions) if sid != chosen],
        "turns": turns,
        "gaps": gap_rows,
        "turn_count": len(turns_all),
        "gap_count": len(gaps),
        "all_match": all_match,
        "generated_at": now_iso(),
    }
    write_output(doc, args, sandbox)
    sys.exit(0 if all_match else 2)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.splitlines()[0])
    ap.add_argument("sandbox_dir",
                    help="the seat sandbox dir (or its .opencode-data dir)")
    ap.add_argument("--expect", required=True, metavar="provider/model",
                    help="model that must have served / been requested")
    ap.add_argument("--run-id", default=None,
                    help="autoos-agent run id for the run-record + window "
                         "(required for the keyless request-side mode; "
                         "D-261 G5)")
    ap.add_argument("--logs-root", default=DEFAULT_LOGS_ROOT,
                    help="root holding agents/<id>/job.json and workers/<id>.json")
    ap.add_argument("--session", default=None,
                    help="opencode session id (default: the newest)")
    ap.add_argument("--gateway", action="store_true",
                    help="response-side check via OmniRoute call logs")
    ap.add_argument("--since", default=None, help="gateway window start (ISO)")
    ap.add_argument("--until", default=None, help="gateway window end (ISO)")
    ap.add_argument("--key", default=None,
                    help=f"gateway apiKeyName to filter on (default "
                         f"{DEFAULT_GATEWAY_KEY}, the spawner key)")
    ap.add_argument("--out", default=None,
                    help="evidence json path (default <sandbox>.seat-evidence.json)")
    args = ap.parse_args(argv)
    if not args.gateway and not args.run_id:
        # The keyless three-way check is meaningless without source (a);
        # refuse in argparse, not after reading the db (D-261 G5).
        ap.error("--run-id is required for the keyless (request-side) mode")

    sandbox, base, db = resolve_db(args.sandbox_dir)
    expect = normalise_expect(args.expect)
    if args.gateway:
        run_gateway(args, sandbox, expect)
    run_keyless(args, sandbox, base, db, expect)


if __name__ == "__main__":
    main(sys.argv[1:])
