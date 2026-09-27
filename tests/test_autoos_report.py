#!/usr/bin/env python3
"""Unit tests for tools/autoos_report.py (routing v2 spec sections 5.7 and 8.2).

The parser extracts BRIEF and REPORT blocks from free text (workers print prose
around them), accepts both the "·"-joined single-line form and the multi-line
"field: value" form, and validates status values. check_report() compares a
report's claims against the actual diff.

Run from the repo root:

    python3 tests/test_autoos_report.py

Exit codes (tools/autoos_report.py):
    0  — parsed successfully, or check found no problems
    1  — no BRIEF/REPORT block found (parse), or problems found (check)
    2  — usage error, or unreadable/missing file
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
REPORT_TOOL = TOOLS / "autoos_report.py"


def _load_module():
    """Load tools/autoos_report.py via an absolute path (underscore is importable, but follow the probe pattern)."""
    spec = importlib.util.spec_from_file_location("autoos_report", str(REPORT_TOOL))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestParseReport(unittest.TestCase):
    """parse_report(text) -> dict or None."""

    def setUp(self):
        self.mod = _load_module()

    def test_no_block_returns_none(self):
        text = "Just some prose with no report block at all."
        self.assertIsNone(self.mod.parse_report(text))

    def test_empty_dict_returns_none(self):
        self.assertIsNone(self.mod.parse_report(""))
        self.assertIsNone(self.mod.parse_report({}))

    def test_single_line_form(self):
        text = "REPORT task-123 · completed · file1.py; file2.py · pytest -> pass; make -> ok · · lesson one"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "task-123")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["files"], ["file1.py", "file2.py"])
        self.assertEqual(result["tests"], [{"cmd": "pytest", "result": "pass"}, {"cmd": "make", "result": "ok"}])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["lessons"], ["lesson one"])
        self.assertEqual(result["missing"], [])

    def test_single_line_form_unicode_arrow(self):
        text = "REPORT task-456 · failed · src.py · test -> FAIL → timeout · blocker1 · lesson1"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["tests"], [{"cmd": "test", "result": "FAIL → timeout"}])

    # --- Defect 1: single-line form with space/colon field-name prefixes ---

    def test_single_line_form_space_field_names(self):
        """Each · segment may start with 'field value' (space, no colon)."""
        text = "REPORT c4 · status completed · files a.py; b.py · tests python3 t.py -> OK · blockers none · lessons none"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "c4")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["files"], ["a.py", "b.py"])
        self.assertEqual(result["tests"], [{"cmd": "python3 t.py", "result": "OK"}])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["lessons"], [])
        self.assertEqual(result["missing"], [])

    def test_single_line_form_colon_field_names(self):
        """Each · segment may start with 'field: value'."""
        text = "REPORT c4 · status: completed · files: a.py; b.py · tests: python3 t.py -> OK · blockers: none · lessons: none"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "c4")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["files"], ["a.py", "b.py"])
        self.assertEqual(result["tests"], [{"cmd": "python3 t.py", "result": "OK"}])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["lessons"], [])
        self.assertEqual(result["missing"], [])

    def test_mixed_field_prefix_forms(self):
        """Mix of no-prefix, space-prefix and colon-prefix segments.

        Segment positions still determine which field receives the value; the
        field-name prefix is simply stripped.
        """
        text = "REPORT task-mix · failed · blockers: need info; timeout · tests test.sh -> FAIL · lessons none · files a.py"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "task-mix")
        self.assertEqual(result["status"], "failed")
        # Position 2 = files, even though the segment says "blockers:"
        self.assertEqual(result["files"], ["need info", "timeout"])
        # Position 3 = tests
        self.assertEqual(result["tests"], [{"cmd": "test.sh", "result": "FAIL"}])
        # Position 4 = blockers, value "lessons none" → "none" → []
        self.assertEqual(result["blockers"], [])
        # Position 5 = lessons, "files a.py" → "a.py"
        self.assertEqual(result["lessons"], ["a.py"])

    def test_none_and_dash_mean_empty_list(self):
        """'none' and '-' for files/blockers/lessons produce [].

        The spec says "none" / "-" for blockers/lessons/files means an empty list.
        """
        text = "REPORT task-empty · completed · none · pytest -> pass · - · none"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["files"], [])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["lessons"], [])

    # --- end defect 1 tests ---

    def test_multi_line_form(self):
        text = """Some intro text.

REPORT my-task
status: completed
files: a.py; b.py
tests: pytest -> pass
blockers:
lessons: learned something

More prose after."""
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "my-task")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["files"], ["a.py", "b.py"])
        self.assertEqual(result["tests"], [{"cmd": "pytest", "result": "pass"}])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["lessons"], ["learned something"])

    def test_multi_line_form_case_insensitive_fields(self):
        text = """REPORT task-x
STATUS: failed
FILES: x.py
TESTS: make -> fail
BLOCKERS: need info
LESSONS: oops"""
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["files"], ["x.py"])
        self.assertEqual(result["blockers"], ["need info"])

    def test_last_block_wins(self):
        text = """REPORT first
status: failed
files: a.py
tests:
blockers:
lessons:

REPORT second
status: completed
files: b.py
tests: pytest -> pass
blockers:
lessons: done"""
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "second")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["files"], ["b.py"])

    def test_prose_around_block(self):
        text = """I did the work and here is my report.

REPORT task-789
status: completed
files: main.py
tests: pytest -> pass
blockers:
lessons: none

That's all folks."""
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "task-789")
        self.assertEqual(result["status"], "completed")

    def test_bad_status_adds_to_missing(self):
        text = "REPORT task-bad · unknown_status · f.py · t -> r · · l"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertIn("status", result["missing"])

    def test_missing_fields_tracked(self):
        text = "REPORT task-incomplete"
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertIn("status", result["missing"])
        self.assertIn("files", result["missing"])
        self.assertIn("tests", result["missing"])

    def test_input_required_status_valid(self):
        text = "REPORT task-ir · input_required · · · need clarification · "
        result = self.mod.parse_report(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "input_required")
        self.assertEqual(result["blockers"], ["need clarification"])
        self.assertEqual(result["missing"], [])


class TestParseBrief(unittest.TestCase):
    """parse_brief(text) -> dict or None."""

    def setUp(self):
        self.mod = _load_module()

    def test_no_block_returns_none(self):
        self.assertIsNone(self.mod.parse_brief("no brief here"))

    def test_single_line_form(self):
        text = "BRIEF task-1 · fix the bug · src/ · exact · tests pass · skills: coding-principles · medium · 10"
        result = self.mod.parse_brief(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "task-1")
        self.assertEqual(result["goal"], "fix the bug")
        self.assertEqual(result["paths"], ["src/"])
        self.assertEqual(result["spec"], "exact")
        self.assertEqual(result["done_when"], "tests pass")
        self.assertEqual(result["skills"], ["coding-principles"])
        self.assertEqual(result["effort"], "medium")
        self.assertEqual(result["budget"], "10")

    # --- Defect 1: BRIEF single-line with field-name prefixes ---

    def test_single_line_form_brief_space_field_names(self):
        """BRIEF with space-separated field-name prefixes, including done-when."""
        text = "BRIEF task-x · goal fix the bug · paths src/; tools/ · spec exact · done-when tests pass · skills a; b · effort medium · budget 5"
        result = self.mod.parse_brief(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "task-x")
        self.assertEqual(result["goal"], "fix the bug")
        self.assertEqual(result["paths"], ["src/", "tools/"])
        self.assertEqual(result["spec"], "exact")
        self.assertEqual(result["done_when"], "tests pass")
        self.assertEqual(result["skills"], ["a", "b"])
        self.assertEqual(result["effort"], "medium")
        self.assertEqual(result["budget"], "5")

    def test_single_line_form_brief_colon_field_names(self):
        """BRIEF with colon-separated field-name prefixes, including done-when handled as done-when."""
        text = "BRIEF task-y · goal: implement feature · paths: src/; docs/ · spec: partial · done-when: it works · skills: coding · effort: high · budget: 20"
        result = self.mod.parse_brief(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "task-y")
        self.assertEqual(result["goal"], "implement feature")
        self.assertEqual(result["paths"], ["src/", "docs/"])
        self.assertEqual(result["spec"], "partial")
        self.assertEqual(result["done_when"], "it works")
        self.assertEqual(result["skills"], ["coding"])
        self.assertEqual(result["effort"], "high")
        self.assertEqual(result["budget"], "20")

    # --- end defect 1 tests ---

    def test_multi_line_form(self):
        text = """BRIEF my-brief
goal: implement feature
paths: tools/; docs/
spec: partial
done-when: it works
skills: coding-principles; testing
effort: high
budget: 20"""
        result = self.mod.parse_brief(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "my-brief")
        self.assertEqual(result["goal"], "implement feature")
        self.assertEqual(result["paths"], ["tools/", "docs/"])
        self.assertEqual(result["skills"], ["coding-principles", "testing"])

    def test_last_block_wins(self):
        text = """BRIEF first
goal: a
paths: x
spec: exact
done-when: y
skills: s
effort: low
budget: 1

BRIEF second
goal: b
paths: y
spec: partial
done-when: z
skills: t
effort: high
budget: 2"""
        result = self.mod.parse_brief(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "second")
        self.assertEqual(result["goal"], "b")


class TestFormatFunctions(unittest.TestCase):
    """format_brief() and format_report() produce parseable output."""

    def setUp(self):
        self.mod = _load_module()

    def test_format_report_roundtrip(self):
        data = {
            "id": "task-rt",
            "status": "completed",
            "files": ["a.py", "b.py"],
            "tests": [{"cmd": "pytest", "result": "pass"}],
            "blockers": [],
            "lessons": ["done"],
        }
        text = self.mod.format_report(data)
        parsed = self.mod.parse_report(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["id"], "task-rt")
        self.assertEqual(parsed["status"], "completed")
        self.assertEqual(parsed["files"], ["a.py", "b.py"])

    def test_format_brief_roundtrip(self):
        data = {
            "id": "brief-rt",
            "goal": "do thing",
            "paths": ["src/"],
            "spec": "exact",
            "done_when": "it works",
            "skills": ["coding-principles"],
            "effort": "medium",
            "budget": "5",
        }
        text = self.mod.format_brief(data)
        parsed = self.mod.parse_brief(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["id"], "brief-rt")
        self.assertEqual(parsed["goal"], "do thing")


class TestCheckReport(unittest.TestCase):
    """check_report(report, changed_files) -> list of problems."""

    def setUp(self):
        self.mod = _load_module()

    def test_files_claimed_but_not_in_diff(self):
        report = {
            "id": "task-1",
            "status": "completed",
            "files": ["a.py", "b.py", "c.py"],
            "tests": [{"cmd": "pytest", "result": "pass"}],
            "blockers": [],
            "lessons": [],
            "missing": [],
        }
        changed = ["a.py", "b.py"]
        problems = self.mod.check_report(report, changed)
        self.assertTrue(any("c.py" in p for p in problems))

    def test_diff_files_not_claimed(self):
        report = {
            "id": "task-2",
            "status": "completed",
            "files": ["a.py"],
            "tests": [{"cmd": "pytest", "result": "pass"}],
            "blockers": [],
            "lessons": [],
            "missing": [],
        }
        changed = ["a.py", "b.py"]
        problems = self.mod.check_report(report, changed)
        self.assertTrue(any("b.py" in p for p in problems))

    def test_completed_with_no_tests(self):
        report = {
            "id": "task-3",
            "status": "completed",
            "files": ["a.py"],
            "tests": [],
            "blockers": [],
            "lessons": [],
            "missing": [],
        }
        changed = ["a.py"]
        problems = self.mod.check_report(report, changed)
        self.assertTrue(any("test" in p.lower() for p in problems))

    def test_no_problems_when_matching(self):
        report = {
            "id": "task-4",
            "status": "completed",
            "files": ["a.py", "b.py"],
            "tests": [{"cmd": "pytest", "result": "pass"}],
            "blockers": [],
            "lessons": [],
            "missing": [],
        }
        changed = ["a.py", "b.py"]
        problems = self.mod.check_report(report, changed)
        self.assertEqual(problems, [])

    def test_failed_status_no_test_requirement(self):
        report = {
            "id": "task-5",
            "status": "failed",
            "files": ["a.py"],
            "tests": [],
            "blockers": ["broken tool"],
            "lessons": [],
            "missing": [],
        }
        changed = ["a.py"]
        problems = self.mod.check_report(report, changed)
        self.assertEqual(problems, [])


class TestCLI(unittest.TestCase):
    """CLI: parse and check subcommands."""

    def test_parse_subcommand(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("REPORT task-cli · completed · f.py · t -> r · · l\n")
            f.flush()
            result = subprocess.run(
                [sys.executable, str(REPORT_TOOL), "parse", f.name],
                capture_output=True,
                text=True,
            )
            Path(f.name).unlink()
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["id"], "task-cli")

    def test_parse_stdin(self):
        result = subprocess.run(
            [sys.executable, str(REPORT_TOOL), "parse", "-"],
            input="REPORT task-stdin · failed · x.py · · blocker · lesson\n",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["id"], "task-stdin")
        self.assertEqual(data["status"], "failed")

    def test_parse_no_block_exits_1(self):
        """parse on input with no BRIEF/REPORT block exits 1."""
        result = subprocess.run(
            [sys.executable, str(REPORT_TOOL), "parse", "-"],
            input="Just some plain text with no block.\n",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no BRIEF or REPORT block found", result.stderr)

    def test_parse_missing_file_exits_2(self):
        """parse on a nonexistent file prints one-line error and exits 2."""
        result = subprocess.run(
            [sys.executable, str(REPORT_TOOL), "parse", "/nonexistent/report.txt"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        # One-line error (no traceback)
        self.assertEqual(len(result.stderr.strip().split("\n")), 1)

    # --- Defect 2: check command changes ---

    def test_check_stdin(self):
        """check - reads report from stdin."""
        result = subprocess.run(
            [sys.executable, str(REPORT_TOOL), "check", "-", "--changed", "a.py"],
            input="REPORT task-check-stdin · completed · a.py · pytest -> pass · · \n",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("ok", result.stdout)

    def test_check_missing_report_file_exits_2(self):
        """check with a missing report file prints one-line error and exits 2."""
        result = subprocess.run(
            [sys.executable, str(REPORT_TOOL), "check", "/nonexistent/report.txt", "--changed", "a.py"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        # One-line error (no traceback)
        self.assertEqual(len(result.stderr.strip().split("\n")), 1)

    def test_check_subcommand_no_problems(self):
        result = subprocess.run(
            [sys.executable, str(REPORT_TOOL), "check", "-", "--changed", "a.py"],
            input="REPORT task-check · completed · a.py · pytest -> pass · · \n",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_check_subcommand_with_problems(self):
        result = subprocess.run(
            [sys.executable, str(REPORT_TOOL), "check", "-", "--changed", "a.py", "b.py"],
            input="REPORT task-check2 · completed · a.py · pytest -> pass · · \n",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("b.py", result.stdout)

    # --- end defect 2 tests ---


if __name__ == "__main__":
    unittest.main()