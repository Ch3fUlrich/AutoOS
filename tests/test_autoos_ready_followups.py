#!/usr/bin/env python3
"""Hermetic ready-guard followup regressions: no runner, git or network."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_ready_guards as g  # noqa: E402


class ReadyFollowupTests(unittest.TestCase):
    def test_decorations_do_not_hide_real_refusals(self):
        for verdict, failed, need in (("FAIL", [2], []), ("INPUT_REQUIRED", [], [2])):
            with self.subTest(verdict=verdict):
                out = g.report_checks("CHECK 2: " + verdict + " tail\n- CHECK 1: PASS", [1, 2])
                self.assertEqual(out, {"ok": False, "missing": [1], "failed": failed,
                                       "input_required": need})


if __name__ == "__main__":
    unittest.main()
