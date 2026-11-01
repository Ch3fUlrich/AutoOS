"""tests/test_oc_l1_recycle_measure.py - AO-OC-CONTEXT-RECYCLE: what tools/oc_l1_recycle_measure.py
reads - newest-record selection, in-flight vs unmapped usage, the model window, the git dir walk.
The fake server and the RecycleBase fixture live in tests/test_oc_l1_recycle.py."""

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "hooks"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l1_recycle  # noqa: E402
import oc_l1_recycle_measure as measure  # noqa: E402
from _oc_l1_fakes import make_lane  # noqa: E402
from test_oc_l1_recycle import RecycleBase, assistant, creates  # noqa: E402


class NewestRecordTest(RecycleBase):
    def test_a_server_that_ignores_order_does_not_hide_the_newest_record(self):
        old, new = assistant(100000, seq=1), assistant(310000, seq=2)
        self.assertEqual(measure.fill_of([old, new]), (measure.OK, 310000))  # oldest first
        self.assertEqual(measure.fill_of([new, old]), (measure.OK, 310000))  # newest first
        self.srv.items = [old, new]
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"], res["ctx"]), (0, "restarted", 310000))

    def test_id_orders_records_that_carry_no_time(self):
        a, b = assistant(100000), assistant(310000)
        a["info"]["id"], b["info"]["id"] = "msg_a", "msg_b"
        self.assertEqual(measure.fill_of([b, a]), (measure.OK, 310000))
        self.assertEqual(measure.fill_of([a, b]), (measure.OK, 310000))

    def test_two_records_with_nothing_to_order_them_by_are_an_error(self):
        self.srv.items = [assistant(100000), assistant(310000)]
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (6, "error"))
        self.assertEqual(creates(self.srv), [])

    def test_a_tie_at_the_top_is_an_error(self):
        self.assertEqual(measure.fill_of([assistant(1, seq=5), assistant(2, seq=5)])[0], measure.BAD)

    def test_one_record_needs_no_ordering(self):
        self.assertEqual(measure.fill_of([assistant(5)]), (measure.OK, 5))


class InFlightVersusUnmappedTest(unittest.TestCase):
    def test_a_completed_record_with_unmapped_cache_is_an_error_not_skipped(self):
        done = assistant(0, output=900, seq=2)
        done["info"]["tokens"]["cache"] = {"weird": 280000}
        self.assertEqual(measure.fill_of([done, assistant(1000, seq=1)])[0], measure.BAD)

    def test_extra_cache_spellings_are_accepted(self):
        rec = assistant(1000, seq=1)
        rec["info"]["tokens"]["cache"] = {"cacheRead": 270000, "cacheWrite": 5000}
        self.assertEqual(measure.fill_of([rec]), (measure.OK, 276000))

    def test_an_in_flight_record_is_zero_with_no_output(self):
        self.assertEqual(measure.fill_of([assistant(0, output=0, seq=2), assistant(7, seq=1)]),
                         (measure.OK, 7))
        self.assertEqual(measure.fill_of([assistant(0, output=0)])[0], measure.NONE)


class ModelWindowTest(RecycleBase):
    def lane_with(self, **model_kw):
        raw = make_lane(self.td, 47250)
        raw["model"] = dict(raw["model"], **model_kw)
        return dict(oc_l1.validate_lane(raw, "l1test"), serve_port=self.srv.port)

    def run_check(self, lane):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            oc_l1_recycle.cmd_recycle(lane, SimpleNamespace(name="l1test", config=None))
        return json.loads(out.getvalue())

    def test_a_pinned_1m_window_clears_the_warning(self):
        lane = dict(self.lane_with(limit={"context": 1000000}), ctx_handoff=700000, ctx_hard=800000)
        self.assertEqual(measure.model_window(lane), 1000000)
        self.srv.items = [assistant(100000)]
        res = self.run_check(lane)
        self.assertEqual((res.get("action"), res.get("ctx")), ("noop", 100000), res)
        self.assertNotIn("warning", res)

    def test_without_the_pin_the_same_limits_are_warned_about(self):
        lane = dict(self.lane_with(), ctx_handoff=700000, ctx_hard=800000)
        self.assertEqual(measure.model_window(lane), 131072)
        self.srv.items = [assistant(100000)]
        self.assertIn("window", self.run_check(lane)["warning"])

    def test_the_registry_context_advertised_is_the_fallback(self):
        reg = {"PlaceholderModel": {"context_advertised": 1000000}}
        with mock.patch.object(measure, "_registry_models", return_value=reg):
            self.assertEqual(measure.model_window(self.lane_with()), 1000000)
            # a pin beats the registry
            self.assertEqual(measure.model_window(self.lane_with(limit={"context": 64000})), 64000)

    def test_the_smaller_window_of_primary_and_fallback_counts(self):
        reg = {"Small": {"context_advertised": 200000}}
        with mock.patch.object(measure, "_registry_models", return_value=reg):
            lane = self.lane_with(limit={"context": 1000000}, fallback_key="fb",
                                  fallback_modelID="prov/Small")
            self.assertEqual(measure.model_window(lane), 200000)
            lane = self.lane_with(limit={"context": 1000000}, fallback_key="fb",
                                  fallback_modelID="prov/Unknown")  # unknown: the 128k default
            self.assertEqual(measure.model_window(lane), 131072)


class GitDirWalkTest(RecycleBase):
    def test_a_stray_dot_git_file_above_the_lane_does_not_hide_the_repo_root(self):
        root = self.td / "root"
        (root / ".git").mkdir(parents=True)
        (root / ".git" / "MERGE_HEAD").write_text("0" * 40, encoding="utf-8")
        mid = root / "mid"
        mid.mkdir()
        (mid / ".git").write_text("not a gitdir pointer\n", encoding="utf-8")
        cwd = mid / "lane"
        cwd.mkdir()
        self.assertEqual(measure._git_dir(cwd), root / ".git")
        reason = measure.merge_in_progress({"cwd": str(cwd), "scratch_dir": str(self.td / "scr")})
        self.assertIn("MERGE_HEAD", reason)


if __name__ == "__main__":
    unittest.main()
