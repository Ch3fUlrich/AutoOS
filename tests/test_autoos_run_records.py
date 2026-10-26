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
import datetime
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
        made = []

        def _record_only(path, mode=0o777, exist_ok=False):
            # Deliberately not created: this test asserts the computed path
            # and must leave the checkout (and its logs/) exactly as it was.
            made.append(path)

        try:
            with mock.patch.object(os, "makedirs", _record_only):
                path = rec.workers_dir()
        finally:
            if saved is not None:
                os.environ["AUTOOS_WORKERS_DIR"] = saved
        # The stub created nothing, so workers_dir() only computed this dir:
        # exactly the one mkdir it would have done, and nothing exists it did.
        self.assertEqual(made, [path])
        self.assertTrue(path.endswith(os.path.join("logs", "workers")))


class WriteKillRecordTests(unittest.TestCase):
    """write_kill_record on a temp state tree: the nt refusal, the first-write
    stamps and the asymmetric merge."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "logs")          # state_dir() is logs/
        os.makedirs(os.path.join(self.state, "kill"))

    def _write(self, record, name):
        # os.name is patched because the guard is exactly what is under test:
        # the moved writer must refuse on nt and write on posix whatever host
        # this suite runs on.
        with mock.patch.object(rec.clients, "state_dir", return_value=self.state), \
                mock.patch.object(rec.os, "name", name):
            return rec.write_kill_record(RUN, record)

    def test_write_kill_record_writes_nothing_on_nt(self):
        self.assertIs(self._write({"pgid": 4242}, "nt"), False)
        self.assertIsNone(rec.read_kill_record(RUN, state=self.state))

    def test_write_kill_record_refuses_an_empty_record(self):
        self.assertIs(self._write({}, "posix"), False)
        self.assertIsNone(rec.read_kill_record(RUN, state=self.state))

    def test_write_kill_record_stamps_run_id_and_created_at_on_first_write(self):
        self.assertIs(self._write({"pgid": 4242}, "posix"), True)
        record = rec.read_kill_record(RUN, state=self.state)
        self.assertEqual(record["run_id"], RUN)
        self.assertRegex(record["created_at"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(record["pgid"], 4242)

    def test_write_kill_record_re_records_the_group_but_not_the_decided_fields(self):
        self._write({"pgid": 1, "mode": "write"}, "posix")
        first = rec.read_kill_record(RUN, state=self.state)
        self.assertIs(self._write({"pgid": 2, "mode": "review"}, "posix"), True)
        record = rec.read_kill_record(RUN, state=self.state)
        self.assertEqual(record["pgid"], 2)              # the group is re-recorded
        self.assertEqual(record["mode"], "write")        # decided-at-spawn is not
        self.assertEqual(record["created_at"], first["created_at"])
        self.assertEqual(record["run_id"], RUN)


class IsoZuluTests(unittest.TestCase):
    """_iso_zulu is the one stamp shape the record readers trust."""

    def test_an_offset_moment_is_stamped_as_utc_seconds_with_a_z(self):
        moment = datetime.datetime(2026, 10, 26, 9, 0, 5, tzinfo=datetime.timezone(
            datetime.timedelta(hours=2)))
        self.assertEqual(rec._iso_zulu(moment), "2026-10-26T07:00:05Z")


class ZenKeyTests(unittest.TestCase):
    """zen_key and ZEN_KEYS_FIELD on a temp checkout's api-keys.yml."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.keys = os.path.join(self.root, "configuration", "api-keys.yml")

    def _write_keys(self, text):
        os.makedirs(os.path.dirname(self.keys))
        with io.open(self.keys, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_the_zen_field_name_is_the_one_the_keys_reader_parses(self):
        self.assertEqual(ckeys.ZEN_KEYS_FIELD, "zen")

    def test_zen_key_reads_the_zen_field(self):
        self._write_keys("# fake values only, never a real key\nzen: fake-zen-key-123\n")
        self.assertEqual(ckeys.zen_key(self.root), "fake-zen-key-123")

    def test_zen_key_is_none_when_the_file_holds_no_zen_field(self):
        self._write_keys("gateway: some-other-field\n")
        self.assertIsNone(ckeys.zen_key(self.root))

    def test_zen_key_is_none_for_a_placeholder_value(self):
        self._write_keys("zen: REPLACE_WITH_ZEN_KEY\n")
        self.assertIsNone(ckeys.zen_key(self.root))


class AgentReExports(unittest.TestCase):
    """autoos-agent.py loaded BY PATH exposes the very callables, not copies."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "autoos_agent_cli", str(ROOT / "tools" / "autoos-agent.py"))
        cls.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.agent)

    def test_run_record_readers_are_the_moved_functions(self):
        for name in ("_iso_zulu", "kill_store_dir", "kill_store_path",
                     "write_kill_record", "read_kill_record", "workers_dir",
                     "worker_writer"):
            self.assertIs(getattr(self.agent, name), getattr(rec, name), name)

    def test_client_key_helpers_are_the_moved_functions(self):
        for name in ("ZEN_KEYS_FIELD", "key_files", "client_key", "zen_key"):
            self.assertIs(getattr(self.agent, name), getattr(ckeys, name), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
