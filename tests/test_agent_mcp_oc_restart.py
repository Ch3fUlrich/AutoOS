"""MCP restart delegates safe stopping to the configured L1 launcher."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_agent_mcp as mcp  # noqa: E402


class OcRestartTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "PATH": "fake-path", "HOME": "fake-home",
            "AUTOOS_OCL1_PW": "test-only", "CALLER_ONLY": "excluded",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.kill = mock.patch.object(mcp.os, "kill", side_effect=AssertionError("no direct kill"))
        self.kill_mock = self.kill.start()
        self.addCleanup(self.kill.stop)

    def restart(self, code, stdout="", stderr=""):
        with mock.patch.object(mcp.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], code, stdout, stderr)) as run:
            result = mcp.oc_restart("review-lane")
        run.assert_called_once_with(
            mcp._oc_l1_args("review-lane", "stop"), capture_output=True,
            text=True, timeout=90, stdin=subprocess.DEVNULL, env=mcp._oc_l1_env())
        self.assertNotIn("CALLER_ONLY", run.call_args.kwargs["env"])
        self.kill_mock.assert_not_called()
        return result

    def test_success(self):
        self.assertEqual(self.restart(0, "earlier\n" + "s" * 250 + "\n"), {
            "lane": "review-lane", "action": "restarted", "exit_code": 0,
            "last_line": "s" * 200,
            "detail": "the lane's watcher starts it within ~2 minutes; verify with oc_status",
        })

    def test_refused_stop(self):
        self.assertEqual(self.restart(2, "ignored", "earlier\n" + "r" * 250 + "\n"), {
            "lane": "review-lane", "action": "not-stopped", "exit_code": 2,
            "detail": "r" * 200,
            "advice": "nothing was restarted; the state file was kept - read the detail, fix it, retry",
        })

    def test_other_exit_is_error(self):
        self.assertEqual(self.restart(1, "ignored", "earlier\n" + "e" * 250 + "\n"), {
            "lane": "review-lane", "action": "error", "exit_code": 1,
            "detail": "e" * 200,
        })

    def test_empty_output(self):
        self.assertEqual(self.restart(0)["last_line"], "")
        self.assertEqual(self.restart(1)["detail"], "")

    def test_fence_precedes_validation_and_subprocess(self):
        with mock.patch.dict(os.environ, {mcp.ENV_AGENT_LAYER: "L2"}), \
                mock.patch.object(mcp.subprocess, "run") as run:
            self.assertTrue(mcp.oc_restart("INVALID LANE")["refused"])
        run.assert_not_called()
        self.kill_mock.assert_not_called()

    def test_invalid_lane_never_runs_subprocess(self):
        with mock.patch.object(mcp.subprocess, "run") as run:
            for lane in ("../other", "", "INVALID", None):
                with self.subTest(lane=lane), self.assertRaises(ValueError):
                    mcp.oc_restart(lane)
        run.assert_not_called()
        self.kill_mock.assert_not_called()

    def test_windows_env_keys_are_in_both_allowlists(self):
        for key in mcp.LANE_ENV_WINDOWS:
            with self.subTest(key=key):
                self.assertIn(key, mcp._OC_L1_ENV_KEYS)
                self.assertIn(key, mcp._OC_L2_ENV_KEYS)


if __name__ == "__main__":
    unittest.main()
