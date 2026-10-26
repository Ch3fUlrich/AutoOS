#!/usr/bin/env python3
"""Invariant I4 (AO-SPAWNER-REMOVE S1a): the historical run-record readers and
client-key helpers, moved out of tools/autoos-agent.py, still answer exactly what
the originals answered - and autoos-agent.py, loaded by path the way the by-path
callers load it, re-exports the SAME callables (identity, not a lookalike).

The fixture is a state tree (`logs/kill/<run>.json` in the current record format)
plus a workers dir holding `<run_id>.json`; every reader runs against those files.

Run directly:

    python3 tests/test_autoos_run_records.py
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_client_keys as ckeys  # noqa: E402
import autoos_run_records as rec  # noqa: E402

RUN = "20261026-090000-task-i4fixture"
MISSING = "20261026-090000-task-no-record"
# The current kill-record format (autoos-agent.py write_kill_record's keys).
KILL_RECORD = {
    "run_id": RUN,
    "created_at": "2026-10-26T09:00:00Z",
    "mode": "write",
    "scope": "scoped",
    "dry_run": False,
    "allow_mode_only": False,
    "lane": "lane/s1a",
    "pgid": 4242,
    "start": 123456,
    "writer": {"client": "claude-code", "family": "claude", "model": "test-model"},
}


class KillStoreReaders(unittest.TestCase):
    """read_kill_record / kill_store_* / worker_writer on a temp state tree."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "logs")          # state_dir() is logs/
        os.makedirs(os.path.join(self.state, "kill"))
        self.record_path = os.path.join(self.state, "kill", RUN + ".json")
        with io.open(self.record_path, "w", encoding="utf-8") as fh:
            json.dump(KILL_RECORD, fh)

    def test_read_kill_record_returns_the_stored_record(self):
        self.assertEqual(rec.read_kill_record(RUN, state=self.state), KILL_RECORD)

    def test_read_kill_record_of_a_run_without_a_record_is_none(self):
        self.assertIsNone(rec.read_kill_record(MISSING, state=self.state))

    def test_kill_store_path_names_one_file_under_the_store(self):
        self.assertEqual(rec.kill_store_dir(self.state),
                         os.path.join(self.state, "kill"))
        self.assertEqual(rec.kill_store_path(RUN, state=self.state),
                         self.record_path)

    def test_kill_store_path_rejects_a_bad_run_id(self):
        for bad in ("", None, ".hidden", "a b"):
            with self.assertRaises(ValueError):
                rec.kill_store_path(bad, state=self.state)

    def test_kill_store_path_never_lets_an_id_leave_the_store(self):
        # The originals' exact answers: an id carrying a path collapses to its
        # basename inside the store (normpath + basename) - it is not an error.
        self.assertEqual(rec.kill_store_path("a/b", state=self.state),
                         os.path.join(self.state, "kill", "b.json"))
        self.assertEqual(rec.kill_store_path("../escape", state=self.state),
                         os.path.join(self.state, "kill", "escape.json"))

    def test_worker_writer_reads_the_runner_record(self):
        with mock.patch.object(rec.clients, "state_dir", return_value=self.state):
            self.assertEqual(rec.worker_writer(RUN), KILL_RECORD["writer"])

    def test_worker_writer_without_a_record_is_none(self):
        with mock.patch.object(rec.clients, "state_dir", return_value=self.state):
            self.assertIsNone(rec.worker_writer(MISSING))
            self.assertIsNone(rec.worker_writer(""))
            self.assertIsNone(rec.worker_writer(None))


class WorkersDirTest(unittest.TestCase):
    """workers_dir(): the AUTOOS_WORKERS_DIR override and the checkout default."""

    def test_override_is_honoured_and_holds_the_worker_record(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workers = os.path.join(tmp.name, "workers")
        os.makedirs(workers)
        with io.open(os.path.join(workers, RUN + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"run_id": RUN}, fh)
        with mock.patch.dict(os.environ, {"AUTOOS_WORKERS_DIR": workers}):
            self.assertEqual(rec.workers_dir(), workers)
        self.assertIn(RUN + ".json", os.listdir(workers))

    def test_default_lands_under_the_checkout_logs_workers(self):
        saved = os.environ.pop("AUTOOS_WORKERS_DIR", None)
        try:
            path = rec.workers_dir()
        finally:
            if saved is not None:
                os.environ["AUTOOS_WORKERS_DIR"] = saved
        self.assertTrue(path.endswith(os.path.join("logs", "workers")))
        self.assertTrue(os.path.isdir(path))


class AgentReExports(unittest.TestCase):
    """autoos-agent.py loaded BY PATH exposes the very callables, not copies."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "autoos_agent_cli", str(ROOT / "tools" / "autoos-agent.py"))
        cls.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.agent)

    def test_run_record_readers_are_the_moved_functions(self):
        for name in ("kill_store_dir", "kill_store_path", "write_kill_record",
                     "read_kill_record", "workers_dir", "worker_writer"):
            self.assertIs(getattr(self.agent, name), getattr(rec, name), name)

    def test_client_key_helpers_are_the_moved_functions(self):
        for name in ("key_files", "client_key", "zen_key"):
            self.assertIs(getattr(self.agent, name), getattr(ckeys, name), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
