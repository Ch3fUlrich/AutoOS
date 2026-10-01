#!/usr/bin/env python3
"""seat-model-evidence.py — deterministic per-turn served-model evidence for
a review seat run on the keyless opencode path (lane SEAT-EVIDENCE, D-260).

A seat that claims model X must be checked against what the PROVIDER
RESPONSE recorded per assistant turn, not against the requested model and
not against text the model wrote. This tool reads the opencode session data
that lives next to a sandbox — <sandbox-dir>.opencode-data/opencode/opencode.db
(passing the data directory itself is also accepted) — and extracts the
served model id for every assistant turn from the one field the runtime
writes from the provider response:

    table session_message, rows of type 'assistant', column `data` (JSON),
    path .model.providerID + .model.id

PRIVACY: the db also holds `credential`, `account`, `control_account` and
`kv` tables. This tool only ever SELECTs (type, seq, data) from
session_message where type='assistant'; the output carries index,
served_model and field — no prompt content, no tokens.

Provider-prefix normalisation is an EXPLICIT map below; model names are
compared byte-exact, never fuzzed.

Usage:
    python3 tools/seat-model-evidence.py <sandbox-dir> \
        --expect <provider/model> [--out <file>]

Output line:
    evidence <path> sha256 <hex> all_match <true|false> turns <n>

Exit codes: 0 all turns match (n>=1); 2 any mismatch; 3 no turn data found /
db unreadable. The JSON goes to --out (default <sandbox>.seat-evidence.json).
Opens the db read-only (mode=ro URI); never writes to it.
"""
import datetime
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

PROG = "seat-model-evidence"

# Explicit, documented provider-prefix spellings -> the providerID opencode
# records. Add entries here only with evidence from a real db; an unmapped
# prefix is compared as written. This is the ONLY normalisation performed.
PROVIDER_PREFIX_ALIASES = {
    "opencode": "opencode",
}

FIELD = ("session_message[type=assistant].data.model.providerID"
         " + .data.model.id")


def die(msg, code=3):
    print(f"{PROG}: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve_db(sandbox_arg):
    """Return the opencode db path for a sandbox dir (or its data dir)."""
    p = Path(sandbox_arg)
    if not p.is_dir():
        die(f"sandbox dir not found: {p}")
    if p.name.endswith(".opencode-data"):
        base = p
    else:
        base = Path(str(p) + ".opencode-data")
    db = base / "opencode" / "opencode.db"
    if not db.is_file():
        die(f"opencode db not found: {db}")
    return p, db


def sandbox_base(sandbox_arg):
    p = Path(sandbox_arg)
    if p.name.endswith(".opencode-data"):
        return Path(str(p)[: -len(".opencode-data")])
    return p


def extract_turns(db):
    """[(served_model, index)] for every assistant turn that records a
    provider-response model. Reads session_message only."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as exc:
        die(f"cannot open db: {exc}")
    try:
        rows = con.execute(
            "SELECT seq, data FROM session_message WHERE type='assistant'"
            " ORDER BY seq").fetchall()
    except sqlite3.Error as exc:
        die(f"cannot read session_message: {exc}")
    finally:
        con.close()
    turns = []
    for row in rows:
        try:
            data = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            continue
        model = data.get("model")
        if not isinstance(model, dict):
            continue
        provider = model.get("providerID")
        mid = model.get("id")
        if not isinstance(provider, str) or not isinstance(mid, str):
            continue
        turns.append(f"{provider}/{mid}")
    return turns


def normalise_expect(expected):
    if "/" not in expected:
        die(f"--expect must be provider/model, got: {expected}")
    prefix, _, mid = expected.partition("/")
    prefix = PROVIDER_PREFIX_ALIASES.get(prefix.lower(), prefix)
    return f"{prefix}/{mid}"


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.splitlines()[0])
    ap.add_argument("sandbox_dir", help="the seat sandbox dir (or its .opencode-data dir)")
    ap.add_argument("--expect", required=True, metavar="provider/model",
                    help="model that must have served every assistant turn")
    ap.add_argument("--out", default=None,
                    help="evidence json path (default <sandbox>.seat-evidence.json)")
    args = ap.parse_args(argv)

    sandbox, db = resolve_db(args.sandbox_dir)
    expected = normalise_expect(args.expect)
    served = extract_turns(db)
    if not served:
        die("no assistant-turn model data found in session_message")

    turns = [{"index": i, "served_model": m, "field": FIELD}
             for i, m in enumerate(served)]
    all_match = all(m == expected for m in served)
    doc = {
        "sandbox": str(sandbox),
        "data_path": str(db),
        "expected": expected,
        "turns": turns,
        "turn_count": len(turns),
        "all_match": all_match,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                        .isoformat(timespec="seconds"),
    }
    out = Path(args.out) if args.out else Path(str(sandbox_base(str(sandbox)))
                                               + ".seat-evidence.json")
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"evidence {out} sha256 {sha} all_match "
          f"{'true' if all_match else 'false'} turns {len(turns)}")
    if not all_match:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
