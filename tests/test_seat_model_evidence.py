#!/usr/bin/env python3
"""Tests for the review-gate seat evidence tool:
tools/seat-model-evidence.py (lane SEAT-EVIDENCE, decision D-260).

Fixtures are throwaway SQLite files in tmp, reproducing the real opencode
schema shape (session_message rows of type 'assistant' whose `data` JSON
carries the per-turn provider-response model id, plus a `credential` table
that the tool must never read or print). The real example dbs under
logs/sandboxes are read only when present and never modified.

Run from the repo root:

    python3 tests/test_seat_model_evidence.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "seat-model-evidence.py"

MIMO_PIN = "AutoOS-20261001-160210-t0-freeze-2-review-a-pin-00a24d"


def example_dbs():
    """Candidate dirs holding the real example dbs: a normal checkout keeps
    them under logs/sandboxes; a lane sandbox sits beside them."""
    return [ROOT / "logs" / "sandboxes", ROOT.parent]


def real_db(name):
    for base in example_dbs():
        db = base / (name + ".opencode-data") / "opencode" / "opencode.db"
        if db.is_file():
            return base / name, db
    return None, None

NEMOTRON = "AutoOS-20261001-205139-wsomni-4g-review-nemotro-e4fe9f"
MIMO_PIN = "AutoOS-20261001-160210-t0-freeze-2-review-a-pin-00a24d"
SENTINEL_TOKEN = "SENTINEL-SECRET-TOKEN-DO-NOT-LEAK"
SENTINEL_ACCOUNT = "sentinel-secret-account@example.invalid"


def make_fixture(base, turns, with_credentials=True):
    """Create <base> sandbox dir + <base>.opencode-data/opencode/opencode.db.

    `turns` is a list of model dicts ({'providerID':..,'id':..}) or None for
    an assistant row that carries no model data (the tool must skip it).
    """
    data_dir = Path(str(base) + ".opencode-data")
    db_dir = data_dir / "opencode"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
        " type TEXT, seq INTEGER, time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    con.execute("CREATE TABLE event (id TEXT, aggregate_id TEXT, seq INTEGER,"
                " created INTEGER, type TEXT, data TEXT)")
    con.execute("CREATE TABLE event_sequence (aggregate_id TEXT PRIMARY KEY,"
                " seq INTEGER, owner_id TEXT)")
    if with_credentials:
        con.execute("CREATE TABLE credential (id TEXT PRIMARY KEY, data TEXT)")
        con.execute("INSERT INTO credential VALUES ('shhh', ?)",
                    (json.dumps({"token": SENTINEL_TOKEN,
                                 "account": SENTINEL_ACCOUNT}),))
        con.execute("CREATE TABLE account (id TEXT PRIMARY KEY, data TEXT)")
        con.execute("CREATE TABLE control_account (id TEXT PRIMARY KEY, data TEXT)")
        con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    seq = 0
    for model in turns:
        seq += 1
        data = {"agent": "review", "content": [], "cost": 0,
                "finish": "stop", "time": {"created": 1}}
        if model is not None:
            data["model"] = {"id": model["id"], "providerID": model["providerID"],
                             "variant": "default"}
        con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                    (f"msg{seq}", "ses_1", "assistant", seq, 1, 1,
                     json.dumps(data)))
    con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("msgu", "ses_1", "user", 0, 1, 1, json.dumps({"role": "user"})))
    con.commit()
    con.close()
    return db


def run_tool(sandbox_dir, expect, out=None):
    argv = [sys.executable, str(TOOL), str(sandbox_dir), "--expect", expect]
    if out:
        argv += ["--out", str(out)]
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc


def parse_stdout(proc):
    # evidence <path> sha256 <hex> all_match <true|false> turns <n>
    parts = proc.stdout.split()
    assert parts[0] == "evidence" and parts[2] == "sha256" and parts[4] == "all_match", proc.stdout
    return parts[1], parts[3], parts[5], int(parts[7])


class SeatEvidenceTests(unittest.TestCase):
    def test_all_match_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "AutoOS-fake-sandbox"
            base.mkdir()
            make_fixture(base, [
                {"providerID": "opencode", "id": "nemotron-3-ultra-free"},
                {"providerID": "opencode", "id": "nemotron-3-ultra-free"},
            ])
            out = Path(tmp) / "ev.json"
            proc = run_tool(base, "opencode/nemotron-3-ultra-free", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            path, sha, match, turns = parse_stdout(proc)
            self.assertEqual(match, "true")
            self.assertEqual(turns, 2)
            self.assertTrue(os.path.isfile(path))
            doc = json.loads(out.read_text())
            self.assertEqual(doc["turn_count"], 2)
            self.assertTrue(doc["all_match"])
            self.assertEqual(doc["expected"], "opencode/nemotron-3-ultra-free")
            self.assertEqual(doc["turns"][0]["served_model"],
                             "opencode/nemotron-3-ultra-free")
            self.assertIn("session_message", doc["turns"][0]["field"])
            import hashlib
            self.assertEqual(sha, hashlib.sha256(out.read_bytes()).hexdigest())

    def test_one_turn_on_other_model_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "AutoOS-fake-sandbox"
            base.mkdir()
            make_fixture(base, [
                {"providerID": "opencode", "id": "nemotron-3-ultra-free"},
                {"providerID": "opencode", "id": "mimo-v2.6-flash-free"},
            ])
            out = Path(tmp) / "ev.json"
            proc = run_tool(base, "opencode/nemotron-3-ultra-free", out)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            _, _, match, turns = parse_stdout(proc)
            self.assertEqual(match, "false")
            self.assertEqual(turns, 2)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["turns"][1]["served_model"],
                             "opencode/mimo-v2.6-flash-free")

    def test_no_turn_data_exit_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "AutoOS-fake-sandbox"
            base.mkdir()
            make_fixture(base, [None, None])  # assistant rows without model data
            proc = run_tool(base, "opencode/nemotron-3-ultra-free",
                             Path(tmp) / "ev.json")
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)

    def test_missing_db_exit_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "AutoOS-fake-sandbox"
            base.mkdir()
            proc = run_tool(base, "opencode/nemotron-3-ultra-free")
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)

    def test_credentials_never_read_or_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "AutoOS-fake-sandbox"
            base.mkdir()
            make_fixture(base, [
                {"providerID": "opencode", "id": "nemotron-3-ultra-free"},
            ])
            out = Path(tmp) / "ev.json"
            proc = run_tool(base, "opencode/nemotron-3-ultra-free", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            blob = out.read_text() + proc.stdout + proc.stderr
            for forbidden in (SENTINEL_TOKEN, SENTINEL_ACCOUNT, "credential",
                              "shhh"):
                self.assertNotIn(forbidden, blob)

    def test_default_out_is_sibling_seat_evidence_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "AutoOS-fake-sandbox"
            base.mkdir()
            make_fixture(base, [
                {"providerID": "opencode", "id": "nemotron-3-ultra-free"},
            ])
            proc = run_tool(base, "opencode/nemotron-3-ultra-free")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            path, _, _, _ = parse_stdout(proc)
            self.assertEqual(Path(path),
                             Path(str(base) + ".seat-evidence.json"))
            self.assertTrue(os.path.isfile(path))

    def test_real_nemotron_db_all_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox, db = real_db(NEMOTRON)
            if db is None:
                self.skipTest(f"example db not present: {NEMOTRON}")
            proc = run_tool(sandbox, "opencode/nemotron-3-ultra-free",
                            Path(tmp) / "ev.json")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            _, _, match, turns = parse_stdout(proc)
            self.assertEqual(match, "true")
            self.assertGreaterEqual(turns, 1)

    def test_real_mimo_pin_db_flags_fence(self):
        # D-260: the fence served nemotron although mimo was requested —
        # the tool must flag the mismatch, never paper over it. Evidence is
        # written to tmp, never beside the real sandbox.
        with tempfile.TemporaryDirectory() as tmp:
            sandbox, db = real_db(MIMO_PIN)
            if db is None:
                self.skipTest(f"example db not present: {MIMO_PIN}")
            proc = run_tool(sandbox, "opencode/mimo-v2.6-flash-free",
                            Path(tmp) / "ev.json")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            _, _, match, turns = parse_stdout(proc)
            self.assertEqual(match, "false")
            self.assertGreaterEqual(turns, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
