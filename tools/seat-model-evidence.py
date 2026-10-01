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
        --expect <provider/model> --gateway (--run-id <id> | --since <iso> --until <iso>) \
        [--key <apiKeyName>]

Output line:
    evidence <path> sha256 <hex> kind <request-side|response-side> \
all_match <true|false> turns <n>

Exit codes: 0 every source present and matching (turns>=1); 2 any mismatch, any
assistant row with a missing model/providerID/id (a gap), or a missing required
source; 3 the db is unreadable / the seat session has no assistant rows at all
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


def read_run_record_window(run_id, logs_root):
    """(start_iso, end_iso) from the run record — started/ended (workers) or
    started (job.json epoch). Only these fields are read."""
    root = Path(logs_root)
    workers = root / "workers" / (run_id + ".json")
    if workers.is_file():
        try:
            d = json.loads(workers.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        s, e = d.get("started"), d.get("ended")
        if isinstance(s, str) and isinstance(e, str):
            return s, e
    job = root / "agents" / run_id / "job.json"
    if job.is_file():
        try:
            d = json.loads(job.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        s = d.get("started")
        if isinstance(s, (int, float)):
            iso = datetime.datetime.fromtimestamp(
                s, datetime.timezone.utc).isoformat(timespec="seconds")
            return iso, None
    return None, None


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

def gateway_fetch(sandbox):
    """Fetch call-log rows (list of dicts) READ-ONLY. If the env
    SEAT_EVIDENCE_GATEWAY_CMD is set it is the fake runner (used by tests);
    otherwise shell out to the container helper. Never sends or prints a key."""
    fake = os.environ.get("SEAT_EVIDENCE_GATEWAY_CMD")
    if fake:
        proc = subprocess.run(shlex.split(fake), capture_output=True, text=True)
        if proc.returncode != 0:
            die(f"gateway fetch failed: {proc.stderr.strip()[:200]}")
        return json.loads(proc.stdout or "[]")
    container = os.environ.get("OMNIROUTE_CONTAINER", "autoos-omniroute")
    js = (
        "import { apiFetch } from '/app/bin/cli/api.mjs';"
        "const all=[];let off=0;"
        "for(;;){const r=await apiFetch('/api/usage/call-logs?limit=1000"
        "&offset='+off+'&excludeTests=1');const rows=(r&&(r.rows||r.data))||[];"
        "if(!rows.length)break;all.push(...rows);off+=rows.length;"
        "if(rows.length<1000)break;}"
        "process.stdout.write(JSON.stringify(all));"
    )
    proc = subprocess.run(
        ["docker", "exec", "-w", "/app", container, "node",
         "--input-type=module", "-e", js],
        capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"gateway helper unavailable (no --gateway evidence): "
            f"{proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout or "[]")


def _in_window(ts, start, end):
    if not isinstance(ts, str):
        return False
    if start and ts < start:
        return False
    if end and ts > end:
        return False
    return True


def gateway_evidence(rows, expect, start, end, key):
    """Filter to the run window (+ optional apiKeyName) and require every row's
    served model == expect. Returns (doc_turns, correlation_ids, all_match)."""
    turns = []
    cids = []
    all_match = True
    matched_any = False
    for r in rows:
        if not isinstance(r, dict):
            continue
        ts = r.get("timestamp")
        if not _in_window(ts, start, end):
            continue
        if key and r.get("apiKeyName") != key:
            continue
        matched_any = True
        provider = r.get("provider")
        mid = r.get("model")
        served = f"{provider}/{mid}" if isinstance(provider, str) \
            and isinstance(mid, str) else None
        if served != expect:
            all_match = False
        cid = r.get("correlationId")
        if isinstance(cid, str):
            cids.append(cid)
        turns.append({"index": len(turns), "served_model": served,
                      "requested_model": r.get("requestedModel"),
                      "status": r.get("status"), "correlation_id": cid,
                      "field": "call_log.model + call_log.provider"})
    if not matched_any:
        die("no gateway call-log rows in the run window")
    return turns, cids, all_match


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
    if args.run_id and not (start or end):
        start, end = read_run_record_window(args.run_id, args.logs_root)
    if not start:
        die("--gateway needs --run-id (with a start in the record) "
            "or --since/--until")
    rows = gateway_fetch(sandbox)
    turns, cids, all_match = gateway_evidence(rows, expect, start, end, args.key)
    doc = {
        "kind": "response-side",
        "sandbox": str(sandbox),
        "expected": expect,
        "window": {"since": start, "until": end, "key": args.key},
        "run_id": args.run_id,
        "turns": turns,
        "turn_count": len(turns),
        "correlation_ids": cids,
        "all_match": all_match,
        "generated_at": now_iso(),
    }
    write_output(doc, args, sandbox)
    sys.exit(0 if all_match else 2)


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
    if not sessions:
        sys.exit(3)
    sys.exit(0 if all_match else 2)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.splitlines()[0])
    ap.add_argument("sandbox_dir",
                    help="the seat sandbox dir (or its .opencode-data dir)")
    ap.add_argument("--expect", required=True, metavar="provider/model",
                    help="model that must have served / been requested")
    ap.add_argument("--run-id", default=None,
                    help="autoos-agent run id for the run-record + window")
    ap.add_argument("--logs-root", default=DEFAULT_LOGS_ROOT,
                    help="root holding agents/<id>/job.json and workers/<id>.json")
    ap.add_argument("--session", default=None,
                    help="opencode session id (default: the newest)")
    ap.add_argument("--gateway", action="store_true",
                    help="response-side check via OmniRoute call logs")
    ap.add_argument("--since", default=None, help="gateway window start (ISO)")
    ap.add_argument("--until", default=None, help="gateway window end (ISO)")
    ap.add_argument("--key", default=None,
                    help="gateway apiKeyName to filter on")
    ap.add_argument("--out", default=None,
                    help="evidence json path (default <sandbox>.seat-evidence.json)")
    args = ap.parse_args(argv)

    sandbox, base, db = resolve_db(args.sandbox_dir)
    expect = normalise_expect(args.expect)
    if args.gateway:
        run_gateway(args, sandbox, expect)
    run_keyless(args, sandbox, base, db, expect)


if __name__ == "__main__":
    main(sys.argv[1:])
