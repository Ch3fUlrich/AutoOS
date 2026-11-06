#!/usr/bin/env python3
"""Report-verdict followups mixed into the wired ready-guards suite."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_ready_guards as g  # noqa: E402


class ReadyReportTests:

    def test_code_lane_report_verdicts(self):
        from test_autoos_spawner import ReadyWriterGuardsTests
        passing = "".join("CHECK %d: PASS evidence\n" % n for n in range(1, 7))
        cases = [(passing.replace("CHECK 6: PASS", "CHECK 6: FAIL"), 1,
                  "failed CHECK 6"),
                 (passing.replace("CHECK 2: PASS", "CHECK 2: INPUT_REQUIRED"), 1,
                  "input_required CHECK 2"),
                 (passing, 0, "ready: appended"),
                 ("CHECK 1: PASS evidence\nCHECK 2: PASS evidence\n", 0,
                  "ready: appended"),
                 (None, 0, "ready: appended"),
                 ("```text\nCHECK 6: FAIL evidence\n```\n"
                  "> CHECK 2: FAIL evidence\n", 0, "ready: appended")]
        for report, expected, message in cases:
            with self.subTest(report=report):
                driver = ReadyWriterGuardsTests()
                driver.setUp()
                try:
                    rc, out, err, inbox, calls = driver.ready(
                        diff_paths=["tools/example.py"], report=report)
                    self.assertEqual(rc, expected, out + err)
                    self.assertIn(message, out + err)
                    self.assertEqual(bool(inbox), expected == 0)
                    self.assertNotIn(" ops=1", inbox)
                    self.assertEqual(len(calls), 1)
                finally:
                    driver.doCleanups()

    def test_template_shaped_report_preserves_gate_refusals(self):
        report = "".join("CHECK %d: PASS evidence\n```text\noutput tail\n```\n" % n
                         for n in range(1, 7))
        result = g.report_checks(report)
        self.assertTrue(result["ok"])
        self.assertEqual(g.report_checks(report.replace("\n", "\r\n")), result)
        decorated = report.replace("CHECK 3: PASS evidence", "- **CHECK 3: PASS x**")
        with self.assertRaisesRegex(
                g.GuardError, "CHECK 3 requires CHECK 3: <PASS\\|FAIL\\|INPUT_REQUIRED> "
                "at column 0 with no list marker, bold or indentation"):
            g.report_checks(decorated)
        bare = g.report_checks(report.replace("CHECK 3: PASS evidence", "CHECK 3: PASS"))
        self.assertFalse(bare["ok"])
        self.assertIn(3, bare["failed"])

    def test_decorated_checks_raise_only_without_real_line(self):
        decorated = ["- CHECK 1: PASS tail", "* CHECK 1: PASS tail",
                     "1. CHECK 1: PASS tail", "+ CHECK 1: PASS tail",
                     "1) CHECK 1: PASS tail", "2. CHECK 1: PASS tail",
                     "12) CHECK 1: PASS tail", "   - __CHECK 1: PASS__",
                     "- **CHECK 1: PASS**", "**CHECK 1: PASS**", "__CHECK 1: PASS__"]
        for text in decorated:
            with self.subTest(text=text):
                with self.assertRaises(g.GuardError) as caught:
                    g.report_checks(text, [1])
                self.assertIn("CHECK 1 requires CHECK 1: <PASS|FAIL|INPUT_REQUIRED>",
                              str(caught.exception))
                self.assertIn("at column 0 with no list marker, bold or indentation",
                              str(caught.exception))
                for quoted in ("```\n" + text + "\n```", "> " + text, "    " + text):
                    self.assertEqual(g.report_checks(quoted, [1])["missing"], [1])
                self.assertTrue(g.report_checks(text + "\nCHECK 1: PASS real tail", [1])["ok"])
        for text in ("- CHECK 01: PASS tail", "- check 1: PASS tail"):
            self.assertEqual(g.report_checks(text, [1])["missing"], [1])

    # Real refusals take precedence over decoration diagnostics.
    def test_decorations_do_not_hide_real_refusals(self):
        for verdict, failed, need in (("FAIL", [2], []), ("INPUT_REQUIRED", [], [2])):
            with self.subTest(verdict=verdict):
                out = g.report_checks("CHECK 2: " + verdict + " tail\n- CHECK 1: PASS", [1, 2])
                self.assertEqual(out, {"ok": False, "missing": [1], "failed": failed,
                                       "input_required": need})

    def test_decorated_fail_uses_verdict_neutral_diagnostic(self):
        with self.assertRaises(g.GuardError) as caught:
            g.report_checks("- CHECK 1: FAIL", [1])
        self.assertEqual(str(caught.exception),
                         "CHECK 1 requires CHECK 1: <PASS|FAIL|INPUT_REQUIRED> "
                         "at column 0 with no list marker, bold or indentation")
