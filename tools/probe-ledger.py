#!/usr/bin/env python3
"""probe-ledger.py - the availability ledger: what serves, what doesn't, and why.

The registry records INTENT (legs, gates, dated $comments). The gateway knows
RIGHT NOW (cooldowns, breakers - ephemeral). This ledger is the middle layer:
measured per-leg serving state with timestamps and reasons, so lanes stop
re-probing known-good and known-dead legs.

State lives in logs/routing/availability.json (git-ignored runtime state, never
committed). Stdlib only.

Usage:
  probe-ledger.py record --leg PROVIDER/MODEL --status served|failed|limited
      --reason TEXT [--latency-ms N]
  probe-ledger.py check --leg PROVIDER/MODEL [--max-age-hours N]  (exit 0 fresh,
      exit 2 stale/unknown; prints one JSON line, never secrets)
  probe-ledger.py credit --provider ID --usd X  (records a balance sighting)
  probe-ledger.py dump  (whole ledger as JSON)

Conventions (see also combo-create skill):
  - served: a real 200 with the expected shape, fresh TTL 7 days.
  - failed: a real error (4xx/5xx/skip); TTL 24h, then re-probe.
  - limited: serves but quota-capped (429/413/TPM) - usable for small work.
  - Every record carries a dated reason; a leg with no record is "unknown".
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "logs" / "routing" / "availability.json"

SERVED_TTL_H = 7 * 24
FAILED_TTL_H = 24


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path: pathlib.Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"legs": {}, "credits": {}}
    doc.setdefault("legs", {})
    doc.setdefault("credits", {})
    return doc


def _save(path: pathlib.Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_record(args, path: pathlib.Path) -> int:
    if args.status not in ("served", "failed", "limited"):
        print("status must be served|failed|limited", file=sys.stderr)
        return 2
    doc = _load(path)
    prev = doc["legs"].get(args.leg, {})
    fails = 0 if args.status in ("served", "limited") else prev.get("fails", 0) + 1
    entry = {"status": args.status, "at": _now(), "reason": args.reason,
             "fails": fails}
    if args.latency_ms is not None:
        entry["latency_ms"] = args.latency_ms
    doc["legs"][args.leg] = entry
    _save(path, doc)
    print(json.dumps({args.leg: entry}))
    return 0


def _fresh(entry: dict, max_age_h: float) -> bool:
    try:
        at = dt.datetime.strptime(entry["at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
    except (KeyError, ValueError):
        return False
    age_h = (dt.datetime.now(dt.timezone.utc) - at).total_seconds() / 3600
    return age_h <= max_age_h


def cmd_check(args, path: pathlib.Path) -> int:
    doc = _load(path)
    entry = doc["legs"].get(args.leg)
    if entry is None:
        print(json.dumps({"leg": args.leg, "status": "unknown"}))
        return 2
    ttl = SERVED_TTL_H if entry.get("status") == "served" else FAILED_TTL_H
    if args.max_age_hours is not None:
        ttl = min(ttl, args.max_age_hours)
    if not _fresh(entry, ttl):
        print(json.dumps({"leg": args.leg, "status": "stale",
                          "last": entry.get("status")}))
        return 2
    print(json.dumps({"leg": args.leg, **entry}))
    return 0


def cmd_credit(args, path: pathlib.Path) -> int:
    try:
        usd = float(args.usd)
    except ValueError:
        print("usd must be a number", file=sys.stderr)
        return 2
    doc = _load(path)
    doc["credits"][args.provider] = {"usd": usd, "as_of": _now()}
    _save(path, doc)
    print(json.dumps({args.provider: doc["credits"][args.provider]}))
    return 0


def cmd_dump(args, path: pathlib.Path) -> int:
    print(json.dumps(_load(path), indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--leg", required=True)
    r.add_argument("--status", required=True)
    r.add_argument("--reason", required=True)
    r.add_argument("--latency-ms", type=int, default=None)
    c = sub.add_parser("check")
    c.add_argument("--leg", required=True)
    c.add_argument("--max-age-hours", type=float, default=None)
    cr = sub.add_parser("credit")
    cr.add_argument("--provider", required=True)
    cr.add_argument("--usd", required=True)
    sub.add_parser("dump")
    ap.add_argument("--state", default=str(STATE))
    args = ap.parse_args(argv)
    path = pathlib.Path(args.state)
    return {"record": cmd_record, "check": cmd_check, "credit": cmd_credit,
            "dump": cmd_dump}[args.cmd](args, path)


if __name__ == "__main__":
    sys.exit(main())
