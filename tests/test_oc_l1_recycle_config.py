"""tests/test_oc_l1_recycle_config.py - AO-OC-CONTEXT-RECYCLE: thresholds, their
validation, the shared guard logic and an L2 lane through tools/oc_l1_recycle.py.
The fake server and the RecycleBase fixture live in tests/test_oc_l1_recycle.py."""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "hooks"))
sys.path.insert(0, str(ROOT / "tests"))

import context_guard  # noqa: E402
import oc_l1  # noqa: E402
import oc_l1_recycle  # noqa: E402
from _oc_l1_fakes import FAKE_SESSION_ID, PW_ENV, make_lane  # noqa: E402
from test_oc_l1_recycle import RecycleBase, assistant, prompts  # noqa: E402


class ThresholdConfigTest(RecycleBase):
    def lane_with(self, **kw):
        raw = make_lane(self.td, 47250)
        raw.update(kw)
        return dict(oc_l1.validate_lane(raw, "l1test"), serve_port=self.srv.port)

    def test_defaults_are_the_d1708_pair(self):
        self.assertEqual(oc_l1_recycle.thresholds(self.lane), (270000, 300000))

    def test_environment_beats_the_default(self):
        os.environ["AUTOOS_CTX_HANDOFF"] = "100000"
        os.environ["AUTOOS_CTX_HARD"] = "150000"
        self.assertEqual(oc_l1_recycle.thresholds(self.lane), (100000, 150000))

    def test_garbled_environment_falls_back_to_the_default(self):
        os.environ["AUTOOS_CTX_HANDOFF"] = "lots"
        self.assertEqual(oc_l1_recycle.thresholds(self.lane), (270000, 300000))

    def test_lane_keys_beat_the_environment_and_a_1m_model_can_raise_them(self):
        os.environ["AUTOOS_CTX_HANDOFF"] = "100000"
        lane = self.lane_with(ctx_handoff=700000, ctx_hard=800000)
        self.assertEqual(oc_l1_recycle.thresholds(lane), (700000, 800000))
        self.srv.items = [assistant(750000)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = oc_l1_recycle.cmd_recycle(lane, SimpleNamespace(name="l1test", config=str(self.cfg)))
        self.assertEqual(json.loads(out.getvalue())["action"], "announced")

    def test_one_lane_key_mixes_with_the_fallback_for_the_other(self):
        lane = self.lane_with(ctx_hard=900000)
        self.assertEqual(oc_l1_recycle.thresholds(lane), (270000, 900000))

    def test_validate_lane_rejects_bad_values(self):
        for bad in (0, -1, True, 1.5, "270000", [1]):
            for key in ("ctx_handoff", "ctx_hard"):
                with self.assertRaises(oc_l1.LaneError, msg="%s=%r" % (key, bad)):
                    self.lane_with(**{key: bad})

    def test_validate_lane_requires_handoff_below_hard(self):
        for pair in ((300000, 300000), (310000, 300000)):
            with self.assertRaises(oc_l1.LaneError):
                self.lane_with(ctx_handoff=pair[0], ctx_hard=pair[1])
        self.assertEqual(self.lane_with(ctx_handoff=1, ctx_hard=2)["ctx_hard"], 2)

    def test_unset_keys_resolve_to_none_in_the_validated_lane(self):
        lane = self.lane_with()
        self.assertEqual((lane["ctx_handoff"], lane["ctx_hard"]), (None, None))

    def test_window_below_the_hard_limit_is_warned_about(self):
        # the rendered default window is 128k: opencode compacts before 300k is reachable
        self.fill(310000)
        rc, res, _ = self.check()
        self.assertIn("window", res.get("warning", ""))


class SharedLogicTest(unittest.TestCase):
    def test_fill_from_usage_is_the_guard_definition(self):
        f = context_guard.fill_from_usage
        self.assertEqual(f({"input_tokens": 1, "cache_read_input_tokens": 2,
                            "cache_creation_input_tokens": 3, "output_tokens": 99}), 6)
        self.assertEqual(f({"input_tokens": 5}), 5)
        self.assertIsNone(f({"input_tokens": "5"}))
        self.assertIsNone(f({"input_tokens": True}))
        self.assertIsNone(f(None))

    def test_the_recycle_module_uses_the_guard_function_not_a_copy(self):
        self.assertIs(oc_l1_recycle.fill_from_usage, context_guard.fill_from_usage)

    def test_resolve_thresholds_prefers_given_values(self):
        self.assertEqual(context_guard.resolve_thresholds(5, 9), (5, 9))


class L2LaneTest(RecycleBase):
    def test_an_l2_lane_config_is_measured_by_the_same_command(self):
        import oc_l2
        brief = self.td / "brief.md"
        brief.write_text("# phase brief\nline-01\n", encoding="utf-8")
        guard = self.td / "guard"
        guard.mkdir()
        os.environ["AUTOOS_OCL2_STATE_DIR"] = str(self.td / "l2state")
        try:
            lane = oc_l2.build_lane(
                "l2-demo-abc-p1", self.lane["cwd"], "p1", brief, "l2-orchestrator",
                self.td / "inbox.md", opencode_bin=sys.executable,
                password_env=PW_ENV, port=47251, guard_dir=guard)
        finally:
            os.environ.pop("AUTOOS_OCL2_STATE_DIR", None)
        valid = dict(oc_l1.validate_lane(lane, lane["name"]), serve_port=self.srv.port)
        Path(valid["state_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(valid["state_file"]).write_text(json.dumps({
            "name": valid["name"], "session_id": FAKE_SESSION_ID, "port": self.srv.port,
            "prompted": True, "canary": {"denied": True}}), encoding="utf-8")
        self.fill(280000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = oc_l1_recycle.cmd_recycle(valid, SimpleNamespace(name=valid["name"], config=None))
        res = json.loads(out.getvalue())
        self.assertEqual((rc, res["action"]), (0, "announced"))
        text = prompts(self.srv)[0]["body"]["text"]
        self.assertIn("l2_report", text)  # an L2 cannot write files: it reports instead


if __name__ == "__main__":
    unittest.main()
