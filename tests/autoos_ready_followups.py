#!/usr/bin/env python3
"""Hermetic ready-guard followup regressions: no runner, git or network."""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_brief as brief  # noqa: E402
import autoos_ready_guards as g  # noqa: E402
import autoos_writer_gate as writer_gate  # noqa: E402


def files_line(paths):
    return ("# AO-1\nFILES (only these; <= 3 files, <= 200 lines changed): "
            + ", ".join(paths) + ". Do not touch other lines/files.\nGOAL: y.\n")


class ReadyFollowupTests(unittest.TestCase):

    def test_rendered_ops_files_and_report_instructions(self):
        fields = {"task_id": "AO-1", "one_line": "fix mail",
                  "paths": ["a.yml", "b.yml", "test_x.py"],
                  "goal": "once-only mail", "invariants": "idempotent",
                  "keys_file": "keys.yml", "reference_playbook": "ref.yml",
                  "entity": "example", "state_path": "a.yml", "indent": "2",
                  "files": "a.yml", "playbooks": "b.yml", "test_files": "test_x.py"}
        for level in ("R2", "R3"):
            with self.subTest(level=level):
                rendered = brief.render_brief("ops", level, fields)
                self.assertEqual(g.brief_files(rendered), fields["paths"])
                for verdict in ("PASS", "FAIL", "INPUT_REQUIRED"):
                    self.assertIn("CHECK n: " + verdict + " <evidence>", rendered)
                self.assertIn("each check on its own line at column 0", rendered)
                self.assertIn("no list marker, bold or indentation", rendered)
                self.assertIn("output tail in a fenced block under that line", rendered)
                self.assertIn("bare verdict counts as FAIL", rendered)
                self.assertIn("CHECK n reports DONE item n", rendered)
                self.assertIn("give each check one verdict", rendered)
                self.assertIn("a second line with a different verdict fails", rendered)
                echoed = g.report_checks(rendered)
                self.assertFalse(echoed["ok"])
                self.assertEqual(echoed["missing"], list(range(1, 7)))
                for line in rendered.splitlines():
                    self.assertFalse(line.strip().startswith(("```", "~~~")))
                placeholder = "CHECK n: PASS <evidence>"
                self.assertIn(placeholder, rendered)
                start = rendered.index(placeholder)
                extracted = rendered[start:start + len(placeholder)]
                checks = [extracted.replace("CHECK n:", "CHECK %d:" % n)
                          .replace("<evidence>", "real output")
                          + "\n```text\noutput tail\n```\n" for n in range(1, 7)]
                report = "".join(checks)
                self.assertTrue(g.report_checks(report)["ok"])
                probe = g.report_checks(rendered + "\n" + "".join(checks[1:]))
                self.assertFalse(probe["ok"])
                self.assertEqual(probe["missing"], [1])

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

    def test_smaller_line_caps_and_pr_123(self):
        paths = ["a.py", "b.py", "CHANGELOG.md"]
        for cap in ("1", "120", "200"):
            with self.subTest(cap=cap):
                text = files_line(paths).replace("200 lines", cap + " lines")
                self.assertEqual(g.brief_files(text), paths)
        text = ("FILES (only these; <= 3 files, <= 120 lines changed): "
                "a.py, b.py, CHANGELOG.md. Do not touch other lines/files.")
        self.assertEqual(g.brief_files(text), paths)

    def test_invalid_line_caps_remain_noncanonical(self):
        for cap in ("0", "201", "0120", "+120", "1000", "", "1 20", "١٢٠"):
            with self.subTest(cap=cap):
                with self.assertRaisesRegex(g.GuardError, "non-canonical FILES line"):
                    g.brief_files(files_line(["a.py"]).replace("200 lines", cap + " lines"))

    def test_crlf_last_paragraph_and_writer_trailer(self):
        trailer = "Plan-Item: example\nWriter: omniroute/cx-gpt-6.1-sol run-example"
        for blank in ("", " ", "\t", " \t "):
            for ending in ("", "\r\n"):
                with self.subTest(blank=blank, ending=ending):
                    message = ("Body\r\n" + blank + "\r\n"
                               + trailer.replace("\n", "\r\n") + ending)
                    self.assertEqual(g.last_paragraph(message), trailer)
                    self.assertEqual(g.writer_trailer_refusal(
                        [{"sha": "abc123", "parents": 1, "message": message}]), "")
        self.assertTrue(g.writer_trailer_refusal([{
            "message": "Writer: model run\r\n \t\r\nBody without trailer"}]))

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

    def test_crlf_writer_attribution_uses_only_trailer(self):
        message = ("Body\r\nWriter: body/model run-body\r\n \t\r\n"
                   "Plan-Item: example\r\nWriter: trailer/model run-trailer\r\n")
        self.assertEqual(list(writer_gate.writers([{"parents": 1, "message": message}], g)),
                         [("trailer/model", False)])

    def test_a_change_here_runs_the_wired_module(self):
        spec = importlib.util.spec_from_file_location(
            "affected_tests_for_followups", ROOT / "tools" / "affected-tests.py")
        affected = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = affected
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(affected)
        picked = affected.map_changed_files(["tests/autoos_ready_followups.py"], ROOT)["pytest"]
        self.assertIn("tests/test_autoos_ready_guards.py", picked)


if __name__ == "__main__":
    unittest.main()
