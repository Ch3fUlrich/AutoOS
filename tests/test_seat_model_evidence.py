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
# Measured live 2026-10-01 (read-only call-log probe): the spawner's gateway
# traffic is keyed "autoos-local"; the paid spark seat logs provider and model
# "spark-1.3-contributor" while the run record requests "omniroute/...".
GW_KEY = "autoos-local"
SPARK = "spark-1.3-contributor"
SPARK_EXPECT = "omniroute/spark-1.3-contributor"


def epoch_for(iso):
    import datetime
    return datetime.datetime.fromisoformat(
        iso.replace("Z", "+00:00")).timestamp()


def write_job_record(root, rid, started_epoch, ended_epoch):
    """agents/<rid>/job.json with ONLY the epoch-seconds started/ended."""
    d = Path(root) / "agents" / rid
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": rid, "started": started_epoch}
    if ended_epoch is not None:
        rec["ended"] = ended_epoch
    (d / "job.json").write_text(json.dumps(rec))


def write_workers_window(root, rid, started_iso, ended_iso, session_id=None):
    """workers/<rid>.json carrying the ISO window (+ optional session id)."""
    w = Path(root) / "workers"
    w.mkdir(parents=True, exist_ok=True)
    rec = {"id": rid, "started": started_iso, "ended": ended_iso}
    if session_id:
        rec["session_id"] = session_id
    (w / (rid + ".json")).write_text(json.dumps(rec))


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
                 "apiKeyName": "autoos-local"},
                {"timestamp": "2026-10-01T21:12:00Z", "provider": "opencode",
                 "model": "mimo-v2.6-flash-free", "requestedModel": NEMO,
                 "status": "success", "correlationId": "c2",
                 "apiKeyName": "autoos-local"},
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
                 "apiKeyName": "autoos-local"},
                # out of window -> ignored
                {"timestamp": "2026-09-01T00:00:00Z", "provider": "opencode",
                 "model": "other-model", "status": "success",
                 "correlationId": "old", "apiKeyName": "autoos-local"},
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

    # --- gateway response-side (D-261 G1-G5) ---------------------------------
    # Fakes reproduce the REAL measured call-log contract (probed read-only
    # 2026-10-01 against the live omniroute container): rows carry
    # timestamp (ms + Z), provider, model, requestedModel, status,
    # correlationId, apiKeyName; the spawner's traffic is keyed
    # "autoos-local"; the paid spark seat logs provider
    # "spark-1.3-contributor".

    def _gw(self, tmp, payload, rows=None):
        fake = Path(tmp) / "fakegw.py"
        if payload is None:
            payload = rows
        fake.write_text("import json;print(json.dumps(payload))"
                        .replace("payload", json.dumps(payload)))
        os.environ["SEAT_EVIDENCE_GATEWAY_CMD"] = f"{sys.executable} {fake}"
        self.addCleanup(os.environ.pop, "SEAT_EVIDENCE_GATEWAY_CMD", None)

    def _spark_row(self, ts, model=SPARK, prov=SPARK, key=GW_KEY, **kw):
        r = {"timestamp": ts, "provider": prov, "model": model,
             "requestedModel": kw.get("req", SPARK_EXPECT), "status": 200,
             "correlationId": kw.get("cid", "c-" + ts[-9:-1]),
             "apiKeyName": key}
        if "sessionId" in kw:
            r["sessionId"] = kw["sessionId"]
        return r

    def _gw_base(self, tmp):
        base, root = self._env(tmp)
        con, _ = make_fixture(base, [GOOD], cli_model=NEMO, run_model=NEMO,
                              rid="rid", logs_root=root)
        con.close()
        return base, root, Path(tmp) / "ev.json"

    def test_gateway_fetch_accepts_logs_wrapped_rows(self):
        # G1: helper may answer {logs:[...]} / {data:[...]} / {rows:[...]}
        # or a bare array; all four shapes must parse.
        for wrap in (None, "logs", "data", "rows"):
            with self.subTest(wrap=wrap):
                with tempfile.TemporaryDirectory() as tmp:
                    base, root, out = self._gw_base(tmp)
                    rows = [self._spark_row("2026-10-01T21:10:00.000Z")]
                    payload = rows if wrap is None else {wrap: rows}
                    self._gw(tmp, payload)
                    proc = run_tool(base, SPARK_EXPECT, out, extra=[
                        "--gateway", "--since", "2026-10-01T21:09:00Z",
                        "--until", "2026-10-01T21:30:00Z"])
                    self.assertEqual(proc.returncode, 0,
                                     proc.stdout + proc.stderr)

    def test_gateway_fetch_http_failure_exit_three_status_only(self):
        # G1: a non-200 from the API must exit 3 naming the status only.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            fake = Path(tmp) / "fakegw.py"
            fake.write_text("import sys\n"
                            "sys.stderr.write('HTTP 500\\n')\n"
                            "sys.exit(3)\n")
            os.environ["SEAT_EVIDENCE_GATEWAY_CMD"] = \
                f"{sys.executable} {fake}"
            try:
                proc = run_tool(base, SPARK_EXPECT, out, extra=[
                    "--gateway", "--since", "2026-10-01T21:09:00Z",
                    "--until", "2026-10-01T21:30:00Z"])
                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertIn("500", proc.stderr)
            finally:
                del os.environ["SEAT_EVIDENCE_GATEWAY_CMD"]

    def test_gateway_fetch_unparseable_output_exit_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            fake = Path(tmp) / "fakegw.py"
            fake.write_text("print('not json at all')")
            os.environ["SEAT_EVIDENCE_GATEWAY_CMD"] = \
                f"{sys.executable} {fake}"
            try:
                proc = run_tool(base, SPARK_EXPECT, out, extra=[
                    "--gateway", "--since", "2026-10-01T21:09:00Z",
                    "--until", "2026-10-01T21:30:00Z"])
                self.assertEqual(proc.returncode, 3, proc.stderr)
            finally:
                del os.environ["SEAT_EVIDENCE_GATEWAY_CMD"]

    def test_gateway_window_mixed_formats(self):
        # G2: Z / +00:00 / ms / non-UTC-offset timestamps compared as DATETIMES.
        # String compare would keep ".500Z" <= "Z" (wrong: after end) and drop
        # the +01:00 row (wrong: it IS inside 21:09Z-21:30Z).
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            rows = [
                self._spark_row("2026-10-01T21:30:00.500Z", cid="after"),
                self._spark_row("2026-10-01T22:10:00+01:00", cid="in01"),
                self._spark_row("2026-10-01T21:10:00Z", cid="in1"),
            ]
            self._gw(tmp, rows)
            proc = run_tool(base, SPARK_EXPECT, out, extra=[
                "--gateway", "--since", "2026-10-01T21:09:00+00:00",
                "--until", "2026-10-01T21:30:00Z"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(sorted(doc["correlation_ids"]), ["in01", "in1"])

    def test_gateway_run_id_window_padded_five_seconds(self):
        # G3: --run-id alone -> [started-5s, ended+5s] from the run record.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            write_workers_window(root, "rid", "2026-10-01T21:59:58Z",
                                 "2026-10-01T22:17:44Z")
            rows = [
                self._spark_row("2026-10-01T21:59:53Z", cid="pad_in"),
                self._spark_row("2026-10-01T21:59:52Z", cid="pad_out"),
                self._spark_row("2026-10-01T22:17:49Z", cid="pad_in_end"),
                self._spark_row("2026-10-01T22:17:50Z", cid="pad_out_end"),
            ]
            self._gw(tmp, rows)
            proc = run_tool(base, SPARK_EXPECT, out, rid="rid",
                            logs_root=root, extra=["--gateway"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(sorted(doc["correlation_ids"]),
                             ["pad_in", "pad_in_end"])
            self.assertEqual(doc["window"]["padding_seconds"], 5)

    def test_gateway_run_id_no_end_exit_three(self):
        # G3: no end time = run possibly still active -> 3, never "until now".
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            write_job_record(root, "rid", epoch_for("2026-10-01T21:59:58Z"),
                             None)
            self._gw(tmp, [self._spark_row("2026-10-01T22:00:00Z")])
            proc = run_tool(base, SPARK_EXPECT, out, rid="rid",
                            logs_root=root, extra=["--gateway"])
            self.assertEqual(proc.returncode, 3, proc.stderr)
            self.assertIn("run still active or no end time", proc.stderr)

    def test_gateway_run_id_job_json_epoch_window(self):
        # G3: job.json started/ended are EPOCH SECONDS -> converted.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            write_job_record(root, "rid", epoch_for("2026-10-01T21:59:58Z"),
                             epoch_for("2026-10-01T22:17:44Z"))
            self._gw(tmp, [
                self._spark_row("2026-10-01T22:00:00Z", cid="in"),
                self._spark_row("2026-10-01T22:17:50Z", cid="out"),
            ])
            proc = run_tool(base, SPARK_EXPECT, out, rid="rid",
                            logs_root=root, extra=["--gateway"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["correlation_ids"], ["in"])

    def test_gateway_other_key_rows_not_counted(self):
        # G4: rows of other runs on a different apiKeyName must not count.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            self._gw(tmp, [
                self._spark_row("2026-10-01T21:10:00Z", cid="mine"),
                self._spark_row("2026-10-01T21:11:00Z", key="someone-else",
                                cid="theirs"),
                self._spark_row("2026-10-01T21:12:00Z", model="other-model",
                                key="someone-else", cid="theirs2"),
            ])
            proc = run_tool(base, SPARK_EXPECT, out, extra=[
                "--gateway", "--since", "2026-10-01T21:09:00Z",
                "--until", "2026-10-01T21:30:00Z"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["window_rows"], 1)

    def test_gateway_shared_window_exit_two_with_warning(self):
        # G4: same key, other models in the window -> not attributable:
        # window_rows + 'window shared with other traffic' + exit 2.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            self._gw(tmp, [
                self._spark_row("2026-10-01T21:10:00Z", cid="a"),
                self._spark_row("2026-10-01T21:11:00Z", cid="b"),
                self._spark_row("2026-10-01T21:12:00Z",
                                model="muse-spark-1.3-contributor",
                                prov="openai-compatible-chat-c125c82d",
                                cid="shared"),
            ])
            proc = run_tool(base, SPARK_EXPECT, out, extra=[
                "--gateway", "--since", "2026-10-01T21:09:00Z",
                "--until", "2026-10-01T21:30:00Z"])
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["window_rows"], 3)
            self.assertIn("window shared with other traffic",
                          json.dumps(doc["warnings"]))
            self.assertIn("window shared with other traffic", proc.stderr)

    def test_gateway_session_id_filter_attributes_shared_key(self):
        # G4: when the rows carry sessionId and the run record knows it,
        # filter by it — shared-key traffic from other sessions drops out.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            write_workers_window(root, "rid", "2026-10-01T21:59:58Z",
                                 "2026-10-01T22:17:44Z",
                                 session_id="ses_seat")
            self._gw(tmp, [
                self._spark_row("2026-10-01T22:00:00Z", cid="mine",
                                sessionId="ses_seat"),
                self._spark_row("2026-10-01T22:01:00Z", cid="theirs",
                                model="muse-spark-1.3-contributor",
                                sessionId="ses_other"),
            ])
            proc = run_tool(base, SPARK_EXPECT, out, rid="rid",
                            logs_root=root, extra=["--gateway"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["correlation_ids"], ["mine"])

    def test_gateway_measured_provider_alias(self):
        # G5: spark seat logs provider "spark-1.3-contributor"; --expect
        # omniroute/... must match ONLY the measured alias, never a fuzzy hit.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            self._gw(tmp, [self._spark_row("2026-10-01T21:10:00Z")])
            proc = run_tool(base, SPARK_EXPECT, out, extra=[
                "--gateway", "--since", "2026-10-01T21:09:00Z",
                "--until", "2026-10-01T21:30:00Z"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_gateway_uuid_provider_is_not_aliased(self):
        # G5: an openai-compatible-chat-<uuid> provider connection is other
        # traffic — no prefix-fuzzy matching against it.
        with tempfile.TemporaryDirectory() as tmp:
            base, root, out = self._gw_base(tmp)
            self._gw(tmp, [self._spark_row(
                "2026-10-01T21:10:00Z",
                prov="openai-compatible-chat-c125c82d-7b07-4e6c-spark-1.3-contributor",
                cid="uuid")])
            proc = run_tool(base, SPARK_EXPECT, out, extra=[
                "--gateway", "--since", "2026-10-01T21:09:00Z",
                "--until", "2026-10-01T21:30:00Z"])
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_keyless_requires_run_id_in_argparse(self):
        # G5: --run-id is a required source for the keyless mode.
        with tempfile.TemporaryDirectory() as tmp:
            base, root = self._env(tmp)
            con, _ = make_fixture(base, [GOOD], cli_model=NEMO,
                                  run_model=NEMO, rid="rid", logs_root=root)
            con.close()
            proc = run_tool(base, NEMO, Path(tmp) / "ev.json")
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn("--run-id", proc.stderr)

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
            proc = run_tool(base, NEMO, rid="rid")
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
