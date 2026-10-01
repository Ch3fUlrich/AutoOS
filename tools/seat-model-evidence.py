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

  response-side (`--gateway`: the paid/gateway path, high tier, D-261/D-266)
    reads OmniRoute call logs READ-ONLY through the container helper, keeps
    only the rows whose `sessionTag` is this run's recorded session tag, and
    requires every status-200 row's served model (`model` + `provider`) to
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
    For --gateway the window sources are EXCLUSIVE: --run-id together with
    --since/--until is a usage error; with --since/--until the tag must come
    from --session-tag. --key must be non-empty (empty would disable key
    attribution).
    With --gateway --run-id the window is [record start, record end] padded
    +-5 s (a record without an end time is a REFUSAL, exit 3 — never "until
    now"); rows are attributed by the call-log field `sessionTag`, which must
    equal the run record's `session_tag` (bare, or the `<tag>/<run-id>` header
    form) — the window and the apiKeyName are secondary bounds only (D-266 H1).
    Rows whose status is not 200 drop out iff a 200 row remains and every 200
    row served --expect (D-266 H2).

Output line:
    evidence <path> sha256 <hex> kind <request-side|response-side> \
all_match <true|false> turns <n> [n_200=<n> n_dropped=<n>]

Exit codes: 0 every source present and matching (turns>=1); 2 any mismatch, any
assistant row with a missing model/providerID/id (a gap), a missing required
source, or a status-200 gateway row that served another model;
3 the db is unreadable / the seat session has no assistant rows at all / the
gateway fetch failed (status only is printed) / the run record has no end time
(unverifiable) / the gateway rows cannot be attributed to this seat — no
session tag in the record, no `sessionTag` field on any row, no status-200
row at all (a window alone is never attribution), or a window bound
(--since/--until or the run record's) that is present but unparseable. An
attributed row with an unparseable timestamp is a gap: counted in
`n_unparseable_ts`, warned, and exits 2 (J1). JSON goes to --out
(default <sandbox>.seat-evidence.json).
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

# The one status a call-log row must carry to prove anything was served;
# rows on any other value are the H2 drop candidates (D-266).
STATUS_OK = 200

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


def read_run_record_session_tag(run_id, logs_root):
    """The spawner's lane tag for a run, from the run record (D-266 H1).

    workers/<id>.json `session_tag` is what the runner writes; job.json may
    carry it too; output.log line `session-tag: <tag>` is the spawner's own
    stdout. The ORDER is the trust order: the record the runner commits is
    authoritative over anything printed. Returns None when the run record
    knows no tag — a window alone must then refuse, never attribute.
    """
    root = Path(logs_root)
    for path in (root / "workers" / (run_id + ".json"),
                 root / "agents" / run_id / "job.json"):
        if not path.is_file():
            continue
        try:
            d = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        req = d.get("request") if isinstance(d.get("request"), dict) else {}
        for cand in (d.get("session_tag"), req.get("session_tag")):
            if isinstance(cand, str) and cand.strip():
                return cand.strip()
    out = root / "agents" / run_id / "output.log"
    if out.is_file():
        try:
            for line in out.read_text(errors="replace").splitlines():
                if line.startswith("session-tag:"):
                    val = line.split(":", 1)[1].strip()
                    if val:
                        return val
        except OSError:
            pass
    return None


def tag_matches(value, want, run_id):
    """True when a row's sessionTag IS this run's tag (D-266 H1).

    The spawner sends the header as `<tag>/<run-id>` (D-063) and the gateway
    logs the header verbatim, while the record stores the bare tag — both
    spellings are this run. Exact equality only: a prefix match would
    attribute a sibling run of the same lane (a concurrent seat) to this one.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    if value == want:
        return True
    return bool(run_id) and value == want + "/" + run_id


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


def _served_model(r):
    provider = r.get("provider")
    mid = r.get("model")
    if isinstance(provider, str) and isinstance(mid, str):
        return f"{GW_PROVIDER_ALIASES.get(provider, provider)}/{mid}"
    return None


def _is_status(r, want):
    st = r.get("status")
    if isinstance(st, bool):
        return False
    try:
        return int(st) == want
    except (TypeError, ValueError):
        return False


_ERROR_CLASSES = (
    ("timeout", ("timeout", "timed out", "etimedout", "stall", "no response")),
    ("abort", ("abort", "interrupted", "canceled", "cancelled")),
    ("rate-limit", ("rate limit", "ratelimit", "too many requests", "429")),
)


def _error_class(r):
    """One fixed class name for a non-200 row — never the error body, which
    carries vendor text (account names, model ids) that has no business in a
    public evidence file (D-266 H2)."""
    err = r.get("error")
    text = str(err).strip().lower() if err is not None else ""
    if not text:
        return "no-response"
    for name, pats in _ERROR_CLASSES:
        if any(pat in text for pat in pats):
            return name
    try:
        st = int(r.get("status"))
    except (TypeError, ValueError):
        st = 0
    return "server-error" if st >= 500 else "client-error"


def gateway_evidence(rows, expect, start, end, key, tag, run_id):
    """Attribute call-log rows to ONE seat run, then apply the non-200 rule.

    Attribution (D-266 H1) is the run's sessionTag; the datetime window and
    the spawner's key name are only secondary bounds — a window alone never
    counts. A row that carries no sessionTag field at all makes the whole
    page unattributable, which exits 3 rather than degrading to the window.

    Non-200 rows (D-266 H2: a 504 stall, an error, a missing response) are
    dropped IFF at least one 200 row remains AND every 200 row served the
    expected model exactly. Otherwise they stay in the evidence, because
    without a matching 200 row there is nothing to attribute the run to.
    Zero 200 rows -> 3 (nothing was served); a 200 row on another model -> 2.
    """
    if not tag:
        die("cannot attribute: no session tag in the run record "
            "(a window alone is not attribution)")
    start_dt, end_dt = _parse_ts(start), _parse_ts(end)
    if start and start_dt is None:
        die("gateway window start bound is unparseable: " + repr(start))
    if end and end_dt is None:
        die("gateway window end bound is unparseable: " + repr(end))
    total = 0
    window_rows = 0
    tagged = 0
    unparseable_rows = 0
    ok_rows = []
    bad_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        total += 1
        ts = _parse_ts(r.get("timestamp"))
        if ts is None:
            # J1(b): an unparseable timestamp on a row that otherwise
            # attributes to this seat (key + tag) is a GAP, never a silent
            # skip; rows failing the key/tag tests stay ignored as before.
            if key is not None and r.get("apiKeyName") != key:
                continue
            if not tag_matches(r.get("sessionTag"), tag, run_id):
                continue
            unparseable_rows += 1
            continue
        if start_dt and ts < start_dt:
            continue
        if end_dt and ts > end_dt:
            continue
        if key is not None and r.get("apiKeyName") != key:
            continue
        window_rows += 1
        row_tag = r.get("sessionTag")
        if isinstance(row_tag, str) and row_tag.strip():
            tagged += 1
        if not tag_matches(row_tag, tag, run_id):
            continue
        (ok_rows if _is_status(r, STATUS_OK) else bad_rows).append(r)
    if not window_rows:
        die("no gateway call-log rows in the run window")
    if not tagged:
        die("cannot attribute: no call-log row in the window carries a "
            "sessionTag field")
    if not ok_rows:
        die("no status-200 row carries this seat's session tag (nothing was "
            "served: cannot attribute)")
    mismatch = sum(1 for r in ok_rows if _served_model(r) != expect)
    keep = ok_rows if mismatch == 0 else sorted(
        ok_rows + bad_rows, key=lambda r: _parse_ts(r.get("timestamp")) or end_dt)
    turns = []
    cids = []
    for r in keep:
        cid = r.get("correlationId")
        if isinstance(cid, str):
            cids.append(cid)
        turns.append({"index": len(turns), "served_model": _served_model(r),
                      "requested_model": r.get("requestedModel"),
                      "status": r.get("status"), "correlation_id": cid,
                      "field": "call_log.model + call_log.provider"})
    dropped_reasons = {}
    if mismatch == 0:
        for r in bad_rows:
            cls = f"{r.get('status')} {_error_class(r)}"
            dropped_reasons[cls] = dropped_reasons.get(cls, 0) + 1
    return {
        "turns": turns, "cids": cids, "window_rows": window_rows,
        "mismatch": mismatch, "n_rows_total": total, "n_200": len(ok_rows),
        "n_dropped": 0 if mismatch else len(bad_rows),
        "n_unparseable_ts": unparseable_rows,
        "dropped_reasons": dropped_reasons,
    }


def write_output(doc, args, sandbox):
    out = Path(args.out) if args.out else Path(
        str(sandbox) + ".seat-evidence.json")
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    extra = ""
    if doc["kind"] == "response-side":
        # D-266 H3: what was served and what dropped, on one line.
        extra = f" n_200={doc['n_200']} n_dropped={doc['n_dropped']}"
    print(f"evidence {out} sha256 {sha} kind {doc['kind']} "
          f"all_match {'true' if doc['all_match'] else 'false'} "
          f"turns {doc['turn_count']}{extra}")
    return out


def run_gateway(args, sandbox, expect):
    start = args.since
    end = args.until
    # J1(a): a bound that is present but unparseable is a refusal naming it;
    # silently dropping the bound would widen the window unnoticed.
    if start and _parse_ts(start) is None:
        die(f"unparseable --since value: {start!r}")
    if end and _parse_ts(end) is None:
        die(f"unparseable --until value: {end!r}")
    pad = 0
    if args.run_id and not (start or end):
        start, end = read_run_record_window(args.run_id, args.logs_root)
        if not start:
            die(f"no start time in the run record for {args.run_id}")
        if not end:
            die("run still active or no end time")
        if _parse_ts(start) is None:
            die(f"unparseable start in the run record for {args.run_id}: "
                f"{start!r}")
        if _parse_ts(end) is None:
            die(f"unparseable end in the run record for {args.run_id}: "
                f"{end!r}")
        start = _shift_iso(start, -WINDOW_PAD_SECONDS)
        end = _shift_iso(end, WINDOW_PAD_SECONDS)
        pad = WINDOW_PAD_SECONDS
    if not start and not end:
        die("--gateway needs --run-id (a finished record with start and end) "
            "or --since/--until")
    tag = args.session_tag or None
    if not tag and args.run_id:
        tag = read_run_record_session_tag(args.run_id, args.logs_root)
    key = args.key if args.key is not None else DEFAULT_GATEWAY_KEY
    rows = gateway_fetch(sandbox)
    stats = gateway_evidence(rows, expect, start, end, key, tag,
                             args.run_id)
    turns = stats["turns"]
    cids = stats["cids"]
    mismatch = stats["mismatch"]
    warnings = []
    if mismatch:
        warnings.append("rows served another model")
        print(f"{PROG}: warning: {mismatch} of {len(turns)} attributed "
              "rows served another model", file=sys.stderr)
    ts_gaps = stats["n_unparseable_ts"]
    if ts_gaps:
        warnings.append(f"{ts_gaps} attributed row(s) with an unparseable "
                        "timestamp — no window placement, evidence is "
                        "incomplete")
        print(f"{PROG}: warning: {ts_gaps} attributed row(s) carry an "
              "unparseable timestamp", file=sys.stderr)
    doc = {
        "kind": "response-side",
        "sandbox": str(sandbox),
        "expected": expect,
        "window": {"since": start, "until": end, "key": key,
                   "padding_seconds": pad, "session_tag": tag},
        "run_id": args.run_id,
        "session_tag": tag,
        "window_rows": stats["window_rows"],
        "n_rows_total": stats["n_rows_total"],
        "n_200": stats["n_200"],
        "n_dropped": stats["n_dropped"],
        "n_unparseable_ts": ts_gaps,
        "dropped_reasons": stats["dropped_reasons"],
        "turns": turns,
        "turn_count": len(turns),
        "correlation_ids": cids,
        "warnings": warnings,
        "all_match": mismatch == 0 and ts_gaps == 0,
        "generated_at": now_iso(),
    }
    if pad:
        print(f"{PROG}: window padded +-{WINDOW_PAD_SECONDS}s around the "
              "run record start/end", file=sys.stderr)
    write_output(doc, args, sandbox)
    sys.exit(0 if doc["all_match"] else 2)


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
    ap.add_argument("--session-tag", default=None,
                    help="the spawner's session tag (default: read "
                         "session_tag from --run-id's record); the call-log "
                         "field sessionTag must equal it (D-266 H1)")
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
    if args.gateway and args.run_id and (args.since or args.until):
        # J2: one way to set the window only; with --since/--until the tag
        # must come from --session-tag.
        ap.error("--gateway: --run-id and --since/--until are exclusive — "
                 "pick one window source")
    if args.key is not None and not args.key.strip():
        # J3: an empty --key would disable key attribution.
        ap.error("empty --key would disable key attribution")

    sandbox, base, db = resolve_db(args.sandbox_dir)
    expect = normalise_expect(args.expect)
    if args.gateway:
        run_gateway(args, sandbox, expect)
    run_keyless(args, sandbox, base, db, expect)


if __name__ == "__main__":
    main(sys.argv[1:])
