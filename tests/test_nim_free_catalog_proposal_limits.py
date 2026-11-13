#!/usr/bin/env python3
"""Offline proposal body boundaries and child-process failure limits."""
import importlib.util
from pathlib import Path
import re
import subprocess
import unittest
from unittest import mock

import test_nim_free_catalog_proposal as fixtures

SPEC = importlib.util.spec_from_file_location("nim_proposal", fixtures.PROPOSAL)
PROPOSAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROPOSAL)


class ProposalLimitsTests(unittest.TestCase):
    def setUp(self):
        self.cli = fixtures.ProposalTests()
        self.cli.setUp()
        self.addCleanup(self.cli.doCleanups)

    def created_body(self, added, removed=0):
        self.cli.state["check"] = {"code": 1, "out": "added: " + ", ".join(
            f"org/a{i:03d}" for i in range(added)) + "\nremoved: " + (
                ", ".join(f"org/r{i:03d}" for i in range(removed)) or "(none)")}
        result = self.cli.run_proposal()
        self.assertEqual(result.returncode, 0, result.stderr)
        create = self.cli.calls()[-1]
        self.assertEqual(create["argv"][:2], ["issue", "create"])
        self.assertFalse(Path(create["body_path"]).exists())
        return create["body"]

    def row_budget(self):
        # Measure the actual header/footer, not a duplicate of their wording.
        return 200 - (len(self.created_body(1).splitlines()) - 1)

    def test_created_body_120_added_90_removed_caps_every_line(self):
        body = self.created_body(120, 90)
        self.assertEqual(len(body.splitlines()), 200)
        rows = re.findall(r"^- (added|removed): `([^`]+)`$", body, re.M)
        self.assertEqual(len(rows), 189)
        self.assertEqual(sum(kind == "added" for kind, _ in rows), 120)
        self.assertIn("... and 21 more\n", body)

    def test_created_body_exactly_at_budget_needs_no_summary(self):
        budget = self.row_budget()
        body = self.created_body(budget)
        self.assertEqual(len(body.splitlines()), 200)
        self.assertEqual(len(re.findall(r"^- added:", body, re.M)), budget)
        self.assertNotIn("... and", body)

    def test_created_body_one_over_budget_reserves_a_summary_line(self):
        budget = self.row_budget()
        body = self.created_body(budget + 1)
        self.assertEqual(len(body.splitlines()), 200)
        self.assertEqual(len(re.findall(r"^- added:", body, re.M)), budget - 1)
        self.assertIn("... and 2 more\n", body)

    def test_default_timeouts_cover_check_and_every_gh_child(self):
        replies = [subprocess.CompletedProcess([], 1, b"added: org/id\n", b"")]
        replies += [subprocess.CompletedProcess([], 0, b"[]", b"") for _ in range(3)]
        with mock.patch.object(PROPOSAL.subprocess, "run", side_effect=replies) as children:
            with mock.patch("sys.stdout"):
                self.assertEqual(PROPOSAL.main([]), 0)
        self.assertEqual([call.kwargs.get("timeout") for call in children.call_args_list],
                         [300, 60, 60, 60])

    def test_short_timeout_requires_test_mode_and_announces_it(self):
        result = self.cli.run_proposal("--test-timeout", "0.5", hooks=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("require --test-mode", result.stderr)
        self.assertEqual(self.cli.calls("check"), [])
        result = self.cli.run_proposal("--test-timeout", "5")
        self.assertEqual((result.returncode, result.stdout), (0, "no change\n"), result.stderr)
        self.assertIn("TEST MODE", result.stderr)

    def test_short_timeout_rejects_invalid_or_non_short_values_before_children(self):
        for value in ("0", "-1", "nan", "inf", "61"):
            with self.subTest(value=value):
                result = self.cli.run_proposal("--test-timeout", value)
                self.assertEqual(result.returncode, 2)
                self.assertIn("greater than 0 and at most 60", result.stderr)
                self.assertEqual(self.cli.calls("check"), [])

    def test_sleeping_check_times_out_and_opens_nothing(self):
        self.cli.state["check"] = {"code": 1, "out": "added: org/id\n", "sleep": 30,
                                   "err": fixtures.TOKEN_MARKER}
        result = self.cli.run_proposal("--test-timeout", "0.5")
        self.assertEqual(result.returncode, 2)
        self.assertIn("check failed: timed out after 0.5 seconds", result.stderr)
        self.assertIn("TEST MODE", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(self.cli.calls("check")), 1)
        self.assertEqual(self.cli.calls(), [])

    def assert_gh_timeout(self, operation, expected):
        self.cli.changed()
        self.cli.state[operation] = {"code": 0, "out": fixtures.TOKEN_MARKER, "sleep": 30}
        result = self.cli.run_proposal("--test-timeout", "5")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"gh {operation}: timed out after 5 seconds", result.stderr)
        self.assertIn("TEST MODE", result.stderr)
        self.assertEqual(result.stdout, "")
        calls = self.cli.calls()
        self.assertEqual([" ".join(call["argv"][:2]) for call in calls], expected)
        for call in calls:
            if "body_path" in call:
                self.assertFalse(Path(call["body_path"]).exists())

    def test_sleeping_gh_list_times_out_without_creating(self):
        self.assert_gh_timeout("issue list", ["issue list"])

    def test_sleeping_gh_label_times_out_without_creating(self):
        self.assert_gh_timeout("label create", ["issue list", "label create"])

    def test_sleeping_gh_create_times_out_and_removes_body_file(self):
        self.assert_gh_timeout("issue create", ["issue list", "label create", "issue create"])

    def assert_invalid_delta_line(self, line):
        self.cli.state["check"] = {"code": 1, "out": "added: valid/id\n" + line}
        result = self.cli.run_proposal()
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid delta ID", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.cli.calls(), [])

    def test_unicode_line_separator_cannot_hide_a_mention(self):
        self.assert_invalid_delta_line("removed: org/x\u2028@mention\n")

    def test_cr_inside_a_delta_line_cannot_hide_a_mention(self):
        self.assert_invalid_delta_line("removed: org/x\r@mention\n")

    def test_delta_prefix_requires_the_exact_separator_space(self):
        self.assert_invalid_delta_line("removed:org/x\n")

    def test_other_splitlines_controls_cannot_hide_a_mention(self):
        for separator in ("\v", "\f", "\u0085", "\u2029"):
            with self.subTest(separator=repr(separator)):
                self.assert_invalid_delta_line(f"removed: org/x{separator}@mention\n")

    def test_crlf_delta_lines_remain_valid(self):
        self.cli.state["check"] = {"code": 1, "out": "added: org/x\r\nremoved: old/id\r\n"}
        result = self.cli.run_proposal("--dry-run", "--no-gh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- added: `org/x`\n", result.stdout)
        self.assertIn("- removed: `old/id`\n", result.stdout)
        self.assertEqual(self.cli.calls(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
