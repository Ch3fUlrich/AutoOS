#!/usr/bin/env python3
"""Tests for the review-gate seat evidence tool:
tools/seat-model-evidence.py (lane SEAT-EVIDENCE, decisions D-260 / D-261).

Fixtures are throwaway SQLite files in tmp, reproducing the real opencode
schema shape (session_message rows of type 'assistant' whose `data` JSON
carries the per-turn REQUESTED / session model id — opencode stores NO
provider-response model, so the keyless check is request-side, D-261), plus a
`credential` table the tool must never read or print. Each fixture also writes
the opencode CLI start-up log and a fake autoos-agent run record so the
three-way (run record / CLI --model / db) can be exercised. The real example
dbs under logs/sandboxes are read only when present and never modified.

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

# Real example dbs are located by ABSOLUTE path so these tests run from any
# worktree / lane sandbox, and are skipped only if the path is genuinely absent.
REAL_SANDBOXES = Path("/home/s/code/AutoOS/logs/sandboxes")
REAL_LOGS = Path("/home/s/code/AutoOS/logs")
NEMOTRON = "AutoOS-20261001-205139-wsomni-4g-review-nemotro-e4fe9f"
NEMOTRON_RID = "20261001-205139-wsomni-4g-review-nemotro-e4fe9f"
MIMO_PIN = "AutoOS-20261001-160210-t0-freeze-2-review-a-pin-00a24d"
MIMO_PIN_RID = "20261001-160210-t0-freeze-2-review-a-pin-00a24d"
NEMO = "opencode/nemotron-3-ultra-free"
MIMO = "opencode/mimo-v2.6-flash-free"
OTHER = "opencode/deepseek-r1-distill-qwen-32b"
SENTINEL_TOKEN = "SENTINEL-SECRET-TOKEN-DO-NOT-LEAK"
SENTINEL_ACCOUNT = "sentinel-secret-account@example.invalid"
GOOD = {"providerID": "opencode", "id": "nemotron-3-ultra-free"}


def real_sandbox(name):
    db = REAL_SANDBOXES / (name + ".opencode-data") / "opencode" / "opencode.db"
    if db.is_file():
        return REAL_SANDBOXES / name
    return None


def write_cli_log(base, model):
    """Create <base>.opencode-data/opencode/log/opencode.log with a single
    cli-starting line whose args carry --model <model> (real opencode shape)."""
    if model is None:
        return
    log = base.parent if base.name.endswith(".opencode-data") else \
        Path(str(base) + ".opencode-data")
    d = log / "opencode" / "log"
    d.mkdir(parents=True, exist_ok=True)
    args = json.dumps(["run", "--standalone", "--agent", "t3-reviewer",
                       "--model", model, "--title", "seat"],
                      separators=(",", ":"))
    (d / "opencode.log").write_text(
        'timestamp=2026-10-01T21:09:10.621Z level=INFO run=cf3d '
        'message="cli starting" version=2.0.16 args='
        + json.dumps(args).strip('"') + ' role=cli\n')


def write_run_record(logs_root, rid, model):
    """workers/<rid>.json holding ONLY the requested model field."""
    if rid is None:
        return
    w = Path(logs_root) / "workers"
    w.mkdir(parents=True, exist_ok=True)
    (w / (rid + ".json")).write_text(json.dumps({"id": rid, "model": model}))


def _schema(con):
    con.execute(
        "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT"
        " NOT NULL, type TEXT, seq INTEGER, time_created INTEGER,"
        " time_updated INTEGER, data TEXT)")
    con.execute("CREATE TABLE credential (id TEXT PRIMARY KEY, data TEXT)")
    con.execute("INSERT INTO credential VALUES ('shhh', ?)",
                (json.dumps({"token": SENTINEL_TOKEN,
                             "account": SENTINEL_ACCOUNT}),))
    for t in ("account", "control_account"):
        con.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY, data TEXT)")
    con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")


def _assistant(con, seq, model, session_id="ses_1"):
    data = {"agent": "review", "content": [], "finish": "stop"}
    if model is not None:
        data["model"] = {"id": model.get("id"),
                         "providerID": model.get("providerID"),
                         "variant": "default"}
    con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                (f"msg{seq}", session_id, "assistant", seq, 1, 1,
                 json.dumps(data)))


def make_fixture(base, turns, cli_model=NEMO, run_model=NEMO, rid="rid",
                 logs_root=None, wal_bad=None, extra_sessions=None):
    """Create a sandbox + opencode db. `turns` is a list of model dicts
    (a dict with id=None or providerID=None models a present-but-incomplete
    model, which the tool must treat as a GAP -> exit 2, never skip).
    If `wal_bad` is a model, it is committed to the WAL only (main db
    checkpointed without it) to prove WAL rows are read. `extra_sessions`
    is a {session_id: [models]} map for other sessions in the same db."""
    data_dir = Path(str(base) + ".opencode-data")
    db_dir = data_dir / "opencode"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    _schema(con)
    seq = 0
    # Other sessions first (lower seq) so the seat's ses_1 is the newest.
    for sid, models in (extra_sessions or {}).items():
        for m in models:
            seq += 1
            _assistant(con, seq, m, session_id=sid)
    for model in turns:
        seq += 1
        _assistant(con, seq, model)
    con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("msgu", "ses_1", "user", 0, 1, 1, json.dumps({"role": "user"})))
    con.commit()
    if wal_bad is not None:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        seq += 1
        _assistant(con, seq, wal_bad)   # stays in the WAL
        con.commit()
    write_cli_log(base, cli_model)
    if logs_root:
        write_run_record(logs_root, rid, run_model)
    return con, db  # caller closes con (kept open for the WAL case)


def run_tool(sandbox_dir, expect, out=None, rid=None, logs_root=None,
             extra=None):
    argv = [sys.executable, str(TOOL), str(sandbox_dir), "--expect", expect]
    if rid:
        argv += ["--run-id", rid]
    if logs_root:
        argv += ["--logs-root", str(logs_root)]
    if out:
        argv += ["--out", str(out)]
    if extra:
        argv += extra
    return subprocess.run(argv, capture_output=True, text=True)


def parse_stdout(proc):
    # evidence <path> sha256 <hex> kind <k> all_match <bool> turns <n>
    parts = proc.stdout.split()
    assert parts[0] == "evidence" and parts[2] == "sha256" \
        and parts[4] == "kind" and parts[6] == "all_match", proc.stdout
    return {"path": parts[1], "sha": parts[3], "kind": parts[5],
            "match": parts[7], "turns": int(parts[9])}


class SeatEvidenceTests(unittest.TestCase):
    def _env(self, tmp):
        base = Path(tmp) / "AutoOS-fake-sandbox"
        base.mkdir()
        root = Path(tmp) / "logsroot"
        return base, root

    def test_all_three_sources_match_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD, GOOD], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            out = Path(tmp) / "ev.json"
            proc = run_tool(base, NEMO, out, rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            r = parse_stdout(proc)
            self.assertEqual(r["kind"], "request-side")
            self.assertEqual(r["match"], "true")
            self.assertEqual(r["turns"], 2)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["kind"], "request-side")
            self.assertEqual(doc["sources"]["run_record"]["model"], NEMO)
            self.assertEqual(doc["sources"]["cli"]["model"], NEMO)
            self.assertTrue(doc["sources"]["db"]["model_matches"])
            self.assertNotIn("provider-response", json.dumps(doc))

    def test_missing_run_record_source_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD, GOOD], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            # rid points at a record that does not exist -> source (a) absent.
            proc = run_tool(base, NEMO, Path(tmp) / "ev.json",
                            rid="nope", logs_root=root)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    # --- mutants the D-261 gate review found surviving ----------------------

    def test_one_turn_missing_model_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            # second assistant row carries no model at all -> gap, not skipped
            con, _ = make_fixture(base, [GOOD, None], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            out = Path(tmp) / "ev.json"
            proc = run_tool(base, NEMO, out, rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["gap_count"], 1)
            self.assertFalse(doc["all_match"])

    def test_model_id_none_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD, {"providerID": "opencode",
                                                "id": None}],
                                  cli_model=NEMO, run_model=NEMO, rid="rid",
                                  logs_root=root)
            con.close()
            proc = run_tool(base, NEMO, Path(tmp) / "ev.json",
                            rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_model_only_in_wal_with_other_model_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            # main db holds only GOOD; a DIFFERENT model lives only in the WAL
            con, _ = make_fixture(base, [GOOD], cli_model=NEMO, run_model=NEMO,
                                  rid="rid", logs_root=root,
                                  wal_bad={"providerID": "opencode",
                                           "id": "mimo-v2.6-flash-free"})
            try:
                proc = run_tool(base, NEMO, Path(tmp) / "ev.json",
                                rid="rid", logs_root=root)
                self.assertEqual(proc.returncode, 2,
                                 "WAL row must be read: " + proc.stdout)
                r = parse_stdout(proc)
                self.assertEqual(r["turns"], 2)
            finally:
                con.close()

    def test_cli_model_differs_from_db_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD], cli_model=OTHER,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            proc = run_tool(base, NEMO, Path(tmp) / "ev.json",
                            rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_run_record_model_differs_from_cli_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD], cli_model=NEMO,
                                  run_model=MIMO, rid="rid", logs_root=root)
            con.close()
            proc = run_tool(base, NEMO, Path(tmp) / "ev.json",
                            rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_gateway_row_other_served_model_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            rows = [
                {"timestamp": "2026-10-01T21:10:00Z", "provider": "opencode",
                 "model": "nemotron-3-ultra-free", "requestedModel": NEMO,
                 "status": "success", "correlationId": "c1",
                 "apiKeyName": "opencode"},
                {"timestamp": "2026-10-01T21:12:00Z", "provider": "opencode",
                 "model": "mimo-v2.6-flash-free", "requestedModel": NEMO,
                 "status": "success", "correlationId": "c2",
                 "apiKeyName": "opencode"},
            ]
            fake = Path(tmp) / "fakegw.py"
            fake.write_text("import json;print(json.dumps(%r))" % (rows,))
            os.environ["SEAT_EVIDENCE_GATEWAY_CMD"] = \
                f"{sys.executable} {fake}"
            try:
                out = Path(tmp) / "ev.json"
                proc = run_tool(base, NEMO, out, extra=[
                    "--gateway", "--since", "2026-10-01T21:09:00Z",
                    "--until", "2026-10-01T21:30:00Z"])
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                doc = json.loads(out.read_text())
                self.assertEqual(doc["kind"], "response-side")
                self.assertEqual(sorted(doc["correlation_ids"]), ["c1", "c2"])
            finally:
                del os.environ["SEAT_EVIDENCE_GATEWAY_CMD"]

    def test_gateway_all_rows_match_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            rows = [
                {"timestamp": "2026-10-01T21:10:00Z", "provider": "opencode",
                 "model": "nemotron-3-ultra-free", "requestedModel": NEMO,
                 "status": "success", "correlationId": "c1",
                 "apiKeyName": "opencode"},
                # out of window -> ignored
                {"timestamp": "2026-09-01T00:00:00Z", "provider": "opencode",
                 "model": "other-model", "status": "success",
                 "correlationId": "old", "apiKeyName": "opencode"},
            ]
            fake = Path(tmp) / "fakegw.py"
            fake.write_text("import json;print(json.dumps(%r))" % (rows,))
            os.environ["SEAT_EVIDENCE_GATEWAY_CMD"] = f"{sys.executable} {fake}"
            try:
                out = Path(tmp) / "ev.json"
                proc = run_tool(base, NEMO, out, extra=[
                    "--gateway", "--since", "2026-10-01T21:09:00Z",
                    "--until", "2026-10-01T21:30:00Z"])
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                r = parse_stdout(proc)
                self.assertEqual(r["kind"], "response-side")
                self.assertEqual(r["turns"], 1)
                self.assertEqual(r["match"], "true")
            finally:
                del os.environ["SEAT_EVIDENCE_GATEWAY_CMD"]

    # --- structural / privacy ------------------------------------------------

    def test_no_assistant_rows_exit_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [], cli_model=NEMO, run_model=NEMO,
                                  rid="rid", logs_root=root)
            con.close()
            proc = run_tool(base, NEMO, Path(tmp) / "ev.json",
                            rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)

    def test_missing_db_exit_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "AutoOS-fake-sandbox"
            base.mkdir()
            proc = run_tool(base, NEMO)
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)

    def test_multiple_sessions_picks_newest_reports_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            # seat session ses_1 (higher seq) is the newest; ses_0 also present
            con, _ = make_fixture(
                base, [GOOD, GOOD], cli_model=NEMO, run_model=NEMO,
                rid="rid", logs_root=root,
                extra_sessions={"ses_0": [{"providerID": "opencode",
                                           "id": "mimo-v2.6-flash-free"}]})
            con.close()
            out = Path(tmp) / "ev.json"
            proc = run_tool(base, NEMO, out, rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["turn_count"], 2)
            others = {s["session"] for s in doc["other_sessions"]}
            self.assertIn("ses_0", others)

    def test_session_flag_selects_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(
                base, [GOOD, GOOD], cli_model=NEMO, run_model=NEMO,
                rid="rid", logs_root=root,
                extra_sessions={"ses_0": [{"providerID": "opencode",
                                           "id": "mimo-v2.6-flash-free"}]})
            con.close()
            proc = run_tool(base, NEMO, Path(tmp) / "ev.json", rid="rid",
                            logs_root=root, extra=["--session", "ses_0"])
            # ses_0 records mimo, not the expected nemotron -> exit 2
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_credentials_never_read_or_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            out = Path(tmp) / "ev.json"
            proc = run_tool(base, NEMO, out, rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            blob = out.read_text() + proc.stdout + proc.stderr
            for forbidden in (SENTINEL_TOKEN, SENTINEL_ACCOUNT, "credential",
                              "shhh"):
                self.assertNotIn(forbidden, blob)

    def test_default_out_is_sibling_seat_evidence_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            proc = run_tool(base, NEMO, rid="rid", logs_root=root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            r = parse_stdout(proc)
            self.assertEqual(Path(r["path"]),
                             Path(str(base) + ".seat-evidence.json"))

    # --- real example dbs (located by absolute path; skip only if absent) ---

    def test_real_nemotron_db_three_way_match(self):
        sandbox = real_sandbox(NEMOTRON)
        if sandbox is None:
            self.skipTest(f"example db not present: {NEMOTRON}")
        # D-261 wording: this seat's CLI was started with --model nemotron and
        # its db records nemotron; nemotron was in fact the requested model, so
        # all three sources agree and the check passes.
        proc = run_tool(sandbox, NEMO, out=Path("/tmp") / "qtest_nem.json",
                        rid=NEMOTRON_RID, logs_root=REAL_LOGS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        r = parse_stdout(proc)
        self.assertEqual(r["kind"], "request-side")
        self.assertEqual(r["match"], "true")
        self.assertGreaterEqual(r["turns"], 1)

    def test_real_mimo_pin_db_flags_fence_relaunch(self):
        # D-261: autoos-agent's own cross-family fence started opencode with
        # `--model nemotron`, so the CLI and db record nemotron even though the
        # run record requested mimo. The evidence is request-side and the
        # three-way diverges -> the tool flags it (exit 2), never papers over.
        # Evidence is written to /tmp, never beside the real sandbox.
        sandbox = real_sandbox(MIMO_PIN)
        if sandbox is None:
            self.skipTest(f"example db not present: {MIMO_PIN}")
        proc = run_tool(sandbox, MIMO, out=Path("/tmp") / "qtest_mimo.json",
                        rid=MIMO_PIN_RID, logs_root=REAL_LOGS)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        doc = json.loads((Path("/tmp") / "qtest_mimo.json").read_text())
        self.assertEqual(doc["sources"]["cli"]["model"], NEMO)
        self.assertEqual(doc["sources"]["run_record"]["model"], MIMO)


if __name__ == "__main__":
    unittest.main(verbosity=2)
