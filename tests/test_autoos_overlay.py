"""tools/autoos_overlay.py: the one machine-wide tool_calls overlay (OVERLAYHOME).

INCIDENT 2026-09-28 12:5xZ: the overlay lived at <checkout>/logs/routing/
measured.json, so a checkout that had never run a probe routed with no overlay
at all and skipped every agentic leg as "unproven". Every reader and writer now
resolves one path per machine. These tests use temp dirs and an injected
environment only - never the real state dir.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import autoos_overlay as ov  # noqa: E402


class PathTests(unittest.TestCase):
    def test_the_env_var_wins_over_everything(self):
        env = {"AUTOOS_MEASURED_OVERLAY": "/x/over.json", "XDG_STATE_HOME": "/xdg",
               "LOCALAPPDATA": "C:\\L", "HOME": "/h"}
        self.assertEqual(ov.default_path(env, "posix"), os.path.abspath("/x/over.json"))
        self.assertEqual(ov.default_path(env, "nt"), os.path.abspath("/x/over.json"))

    def test_the_env_value_expands_home_vars_and_relative_paths(self):
        # Muse review (HIGH): a raw `~/x` made a literal "~" directory and a
        # relative value a cwd-relative file.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp, "OVDIR": tmp}):
            self.assertEqual(ov.default_path({ov.ENV_VAR: "~/o.json"}),
                             os.path.join(os.path.abspath(tmp), "o.json"))
            self.assertEqual(ov.default_path({ov.ENV_VAR: "$OVDIR/v.json"}),
                             os.path.join(os.path.abspath(tmp), "v.json"))
        self.assertEqual(ov.default_path({ov.ENV_VAR: os.path.join("rel", "o.json")}),
                         os.path.join(os.getcwd(), "rel", "o.json"))

    def test_xdg_state_home_on_posix(self):
        path = ov.default_path({"XDG_STATE_HOME": "/xdg", "HOME": "/h"}, "posix")
        self.assertEqual(path, os.path.join("/xdg", "autoos", "measured.json"))

    def test_local_state_default_on_posix(self):
        path = ov.default_path({"HOME": "/h"}, "posix")
        self.assertEqual(path, os.path.join("/h", ".local", "state", "autoos", "measured.json"))

    def test_localappdata_on_windows(self):
        path = ov.default_path({"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}, "nt")
        self.assertEqual(path, os.path.join("C:\\Users\\u\\AppData\\Local", "autoos", "measured.json"))

    def test_an_empty_env_var_is_ignored(self):
        path = ov.default_path({"AUTOOS_MEASURED_OVERLAY": "", "HOME": "/h"}, "posix")
        self.assertTrue(path.endswith(os.path.join("autoos", "measured.json")))

    def test_legacy_path_is_per_checkout(self):
        self.assertEqual(ov.legacy_path("/repo"),
                         os.path.join("/repo", "logs", "routing", "measured.json"))


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "state", "measured.json")
        self.legacy = os.path.join(self.tmp, "repo", "logs", "routing", "measured.json")

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_the_new_path_is_read_and_no_note_is_printed(self):
        self._write(self.path, {"legs": {"a": 1}})
        self._write(self.legacy, {"legs": {"b": 2}})
        err = io.StringIO()
        self.assertEqual(ov.load(self.path, self.legacy, err), {"legs": {"a": 1}})
        self.assertEqual(err.getvalue(), "")

    def test_legacy_is_read_with_one_note_and_never_deleted(self):
        self._write(self.legacy, {"legs": {"b": 2}})
        err = io.StringIO()
        self.assertEqual(ov.load(self.path, self.legacy, err), {"legs": {"b": 2}})
        self.assertEqual(err.getvalue().count("\n"), 1)
        self.assertIn("overlay: using legacy %s" % self.legacy, err.getvalue())
        self.assertIn(self.path, err.getvalue())
        self.assertTrue(os.path.isfile(self.legacy))

    def test_nothing_anywhere_is_an_empty_overlay(self):
        err = io.StringIO()
        self.assertEqual(ov.load(self.path, self.legacy, err), {})
        self.assertIsNone(ov.found(self.path, self.legacy))

    def test_without_a_legacy_path_only_the_new_path_counts(self):
        self._write(self.legacy, {"legs": {"b": 2}})
        self.assertEqual(ov.load(self.path, None, io.StringIO()), {})


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_save_creates_the_directory_and_leaves_no_temp_file(self):
        path = os.path.join(self.tmp, "a", "b", "measured.json")
        ov.save(path, {"legs": {}})
        self.assertEqual(os.listdir(os.path.dirname(path)), ["measured.json"])
        with io.open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"legs": {}})

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_save_is_mode_600(self):
        path = os.path.join(self.tmp, "measured.json")
        ov.save(path, {})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class MergeSaveTests(unittest.TestCase):
    """Muse review (MEDIUM): the probes' read-modify-write was unlocked, so two
    probes at once lost one verdict even with the atomic replace."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "measured.json")

    def _read(self):
        with io.open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_two_writers_from_one_base_keep_both_legs(self):
        base = {"legs": {"old": {"tool_calls": {"value": "proven"}}}}
        ov.save(self.path, base)
        one = {"legs": dict(base["legs"], a={"tool_calls": {"value": "proven"}})}
        two = {"legs": dict(base["legs"], b={"tool_calls": {"value": "broken"}})}
        ov.merge_save(self.path, one, base)
        ov.merge_save(self.path, two, base)
        self.assertEqual(sorted(self._read()["legs"]), ["a", "b", "old"])

    def test_two_probes_writing_one_model_keep_both_fields(self):
        base = {"models": {"m": {}}}
        ov.merge_save(self.path, {"models": {"m": {"effort": {"low": 1}}}}, base)
        ov.merge_save(self.path, {"models": {"m": {"context_usable": 9}}}, base)
        self.assertEqual(self._read()["models"]["m"], {"effort": {"low": 1}, "context_usable": 9})

    def test_a_key_the_writer_removed_is_removed(self):
        base = {"legs": {"a": {"x": 1}, "b": {"x": 2}}}
        ov.save(self.path, base)
        ov.merge_save(self.path, {"legs": {"a": {"x": 1}}}, base)
        self.assertEqual(self._read(), {"legs": {"a": {"x": 1}}})

    def test_concurrent_writers_lose_nothing(self):
        base = {}
        errors = []

        def write(n):
            try:
                ov.merge_save(self.path, {"legs": {"leg%d" % n: {"v": n}}}, base)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self._read()["legs"]), ["leg%d" % n for n in range(8)])

    def test_the_legacy_file_seeds_a_first_merge_only_when_asked(self):
        legacy = os.path.join(self.tmp, "legacy.json")
        ov.save(legacy, {"legs": {"old": {"v": 0}}})
        ov.merge_save(self.path, {"legs": {"new": {"v": 1}}}, {}, legacy)
        self.assertEqual(sorted(self._read()["legs"]), ["new", "old"])
        other = os.path.join(self.tmp, "other.json")
        ov.merge_save(other, {"legs": {"new": {"v": 1}}}, {})
        with io.open(other, encoding="utf-8") as fh:
            self.assertEqual(sorted(json.load(fh)["legs"]), ["new"])


class ProbeTargetTests(unittest.TestCase):
    """Muse review (MEDIUM): an explicit --overlay was detected by comparing it
    with the import-time default, so another spelling of the same path, or an
    env change after import, flipped the legacy fallback."""

    def test_no_overlay_argument_is_the_default_with_legacy(self):
        import probe_common
        self.assertEqual(probe_common.overlay_target(None),
                         (probe_common.DEFAULT_OVERLAY, probe_common.LEGACY_OVERLAY))

    def test_an_explicit_overlay_never_reads_legacy_even_when_it_names_the_default(self):
        import probe_common
        self.assertEqual(probe_common.overlay_target(probe_common.DEFAULT_OVERLAY),
                         (probe_common.DEFAULT_OVERLAY, None))


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "measured.json")

    def test_missing(self):
        self.assertEqual(ov.status(self.path, None),
                         {"path": self.path, "present": False, "age_hours": None})

    def test_present_with_age(self):
        ov.save(self.path, {})
        old = time.time() - 3 * 3600
        os.utime(self.path, (old, old))
        got = ov.status(self.path, None)
        self.assertTrue(got["present"])
        self.assertEqual(got["path"], self.path)
        self.assertAlmostEqual(got["age_hours"], 3.0, delta=0.1)

    def test_legacy_in_use_is_reported_as_the_path(self):
        legacy = os.path.join(self.tmp, "legacy.json")
        ov.save(legacy, {})
        got = ov.status(self.path, legacy)
        self.assertEqual(got["path"], legacy)
        self.assertTrue(got["present"])

    def test_missing_reason_names_the_path_and_the_fix(self):
        reason = ov.missing_reason(self.path)
        self.assertIn("no tool_calls overlay found at %s" % self.path, reason)
        self.assertIn("tools/probe-toolcalls.py", reason)
        self.assertIn("AUTOOS_MEASURED_OVERLAY", reason)


if __name__ == "__main__":
    unittest.main()
