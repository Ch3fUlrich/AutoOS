#!/usr/bin/env python3
"""Proposal CLI tests: Python-launched fakes work without a shell on both OSes."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
PROPOSAL = ROOT / "tools/nim-free-catalog-proposal.py"
TOKEN_MARKER = "[REDACTED]"
FAKE = r'''
import json
import os
from pathlib import Path
import sys
root = Path(__file__).resolve().parent
kind = Path(__file__).stem
state = json.loads((root / "state.json").read_text(encoding="utf-8"))
argv = sys.argv[1:]
record = {"kind": kind, "argv": argv}
if kind == "check":
    record["overrides"] = [os.environ.get(name) for name in
        ("AUTOOS_NIM_CATALOG_BASE_URL", "AUTOOS_NIM_V1_BASE_URL")]
    reply = state["check"]
else:
    reply = state.get(" ".join(argv[:2]), {"code": 0, "out": ""})
    if "--body-file" in argv:
        body_path = Path(argv[argv.index("--body-file") + 1])
        record.update(body_path=str(body_path), body=body_path.read_text(encoding="utf-8"))
with (root / "calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")
if reply.get("signal"):
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
if reply.get("sleep"):
    import time
    time.sleep(reply["sleep"])
# Emit exact fixture bytes; Windows text mode would turn CRLF into CRCRLF.
sys.stdout.buffer.write(reply.get("out", "").encode("utf-8"))
print(reply.get("err", ""), end="", file=sys.stderr)
sys.exit(reply["code"])
'''


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nim proposal ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.check = self.root / "check.py"
        self.gh = self.root / "gh.py"
        for path in (self.check, self.gh):
            path.write_text(FAKE, encoding="utf-8")
        self.state = {"check": {"code": 0, "out": ""},
                      "issue list": {"code": 0, "out": "[]"}}

    def run_proposal(self, *extra, hooks=True):
        (self.root / "state.json").write_text(json.dumps(self.state), encoding="utf-8")
        argv = [sys.executable, str(PROPOSAL)]
        if hooks:
            argv += ["--test-mode", "--check-tool", str(self.check), "--gh", str(self.gh)]
        env = dict(os.environ, GH_TOKEN=TOKEN_MARKER,
                   AUTOOS_NIM_CATALOG_BASE_URL="catalog-test-override",
                   AUTOOS_NIM_V1_BASE_URL="models-test-override")
        result = subprocess.run(argv + list(extra), cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertNotIn(TOKEN_MARKER, result.stdout + result.stderr)
        return result

    def calls(self, kind="gh"):
        path = self.root / "calls.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] \
            if path.exists() else []
        return [record for record in records if record["kind"] == kind]

    def test_no_change_calls_no_gh_and_passes_snapshot_and_overrides(self):
        snapshot = str(self.root / "snapshot with spaces.json")
        result = self.run_proposal("--snapshot", snapshot)
        self.assertEqual((result.returncode, result.stdout), (0, "no change\n"), result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.calls("check")[0]["argv"], ["--check", "--snapshot", snapshot])
        self.assertEqual(self.calls("check")[0]["overrides"],
                         ["catalog-test-override", "models-test-override"])

    def test_hooks_require_explicit_test_mode_and_announce_it(self):
        result = self.run_proposal("--check-tool", str(self.check), "--gh", str(self.gh),
                                   hooks=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("require --test-mode", result.stderr)
        self.assertEqual(self.calls("check"), [])
        result = self.run_proposal()
        self.assertIn("TEST MODE", result.stderr)

    def test_failed_or_signalled_check_opens_nothing_and_does_not_echo_output(self):
        for reply, reason in (({"code": 2}, "exit 2"), ({"code": 7}, "exit 7"),
                              ({"code": 0, "signal": True}, "check failed")):
            with self.subTest(reply=reply):
                self.state["check"] = dict(reply, out=TOKEN_MARKER, err=TOKEN_MARKER)
                result = self.run_proposal()
                self.assertEqual(result.returncode, 2)
                self.assertIn(reason, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.calls(), [])

    def test_missing_check_is_an_error(self):
        self.check.unlink()
        result = self.run_proposal()
        self.assertEqual(result.returncode, 2)
        self.assertIn("check failed", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_changed_exit_without_actual_delta_is_an_error(self):
        for output in ("counts: total=1\n", "added: (none)\nremoved: (none)\n"):
            with self.subTest(output=output):
                self.state["check"] = {"code": 1, "out": output}
                result = self.run_proposal()
                self.assertEqual(result.returncode, 2)
                self.assertIn("no added/removed IDs", result.stderr)
                self.assertEqual(self.calls(), [])

    def test_malformed_delta_ids_fail_before_any_gh_call(self):
        for invalid in ("@mention", "id`text", "[link]", "a b", "", "a" * 201,
                        "caf\u00e9", "ok,", "ok, @mention", " padded", "padded\t"):
            with self.subTest(invalid=invalid):
                self.state["check"] = {"code": 1, "out": "added: valid/id\nremoved: " + invalid}
                result = self.run_proposal()
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid delta ID", result.stderr)
                self.assertEqual(self.calls(), [])

    def test_local_dry_run_prints_sorted_deduplicated_delta_and_review_instructions(self):
        self.state["check"] = {"code": 1,
                               "out": "added: org/z, org/a_1.2\nadded: org/z\nremoved: old/id\n"}
        result = self.run_proposal("--dry-run", "--no-gh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.splitlines()[0],
                         r"^NIM free catalog changed: \+2 / -1 \(\d{4}-\d{2}-\d{2}\)$")
        self.assertEqual(re.findall(r"^- (added|removed): `([^`]+)`$", result.stdout, re.M),
                         [("added", "org/a_1.2"), ("added", "org/z"), ("removed", "old/id")])
        self.assertIn("python3 tools/nim-free-catalog.py", result.stdout)
        self.assertIn("The registry is edited only by a reviewed pull request.", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_body_cap_is_shared_by_added_and_removed_and_reports_remaining_count(self):
        self.state["check"] = {"code": 1, "out": "added: " + ", ".join(
            "org/a%03d" % i for i in reversed(range(120))) + "\nremoved: " + ", ".join(
            "org/r%03d" % i for i in reversed(range(90)))}
        result = self.run_proposal("--dry-run", "--no-gh")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = re.findall(r"^- (?:added|removed): `([^`]+)`$", result.stdout, re.M)
        self.assertEqual(len(rows), 189)
        self.assertEqual(rows, sorted(rows))
        self.assertIn("... and 21 more", result.stdout)
        self.assertIn("+120 / -90", result.stdout)
        self.state["check"]["out"] += "\nremoved: @mention"
        self.assertEqual(self.run_proposal("--dry-run", "--no-gh").returncode, 2)

    def test_no_gh_requires_dry_run_before_running_children(self):
        result = self.run_proposal("--no-gh")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--no-gh requires --dry-run", result.stderr)
        self.assertEqual(self.calls("check"), [])

    def changed(self):
        self.state["check"] = {"code": 1, "out": "added: org/z, org/a\nremoved: old/id\n"}

    def test_delta_creates_exactly_one_labelled_proposal_via_temporary_body_file(self):
        self.changed()
        self.state["issue create"] = {"code": 0, "out": TOKEN_MARKER}
        result = self.run_proposal()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([c["argv"][:2] for c in calls],
                         [["issue", "list"], ["label", "create"], ["issue", "create"]])
        self.assertEqual(calls[0]["argv"], ["issue", "list", "--state", "open", "--label",
                                          "nim-free-catalog", "--json", "number", "--limit", "1"])
        self.assertEqual(calls[1]["argv"][:5],
                         ["label", "create", "nim-free-catalog", "--force", "--description"])
        self.assertTrue(calls[1]["argv"][5])
        create = calls[2]
        argv = create["argv"]
        self.assertEqual(argv[argv.index("--label") + 1], "nim-free-catalog")
        self.assertRegex(argv[argv.index("--title") + 1],
                         r"^NIM free catalog changed: \+2 / -1 \(\d{4}-\d{2}-\d{2}\)$")
        self.assertNotIn("--body", argv)
        self.assertIn("--body-file", argv)
        self.assertLess(create["body"].index("org/a"), create["body"].index("org/z"))
        self.assertIn("- removed: `old/id`", create["body"])
        self.assertIn("python3 tools/nim-free-catalog.py", create["body"])
        self.assertIn("The registry is edited only by a reviewed pull request.", create["body"])
        self.assertFalse(Path(create["body_path"]).exists())

    def test_open_proposal_means_no_create_no_label_and_no_comment(self):
        self.changed()
        self.state["issue list"]["out"] = '[{"number": 42}]'
        result = self.run_proposal()
        self.assertEqual((result.returncode, result.stdout),
                         (0, "proposal already open: #42\n"), result.stderr)
        self.assertEqual([c["argv"][:2] for c in self.calls()], [["issue", "list"]])

    def test_gh_list_failure_is_not_success_and_never_creates(self):
        self.changed()
        self.state["issue list"] = {"code": 1, "out": TOKEN_MARKER, "err": TOKEN_MARKER}
        result = self.run_proposal()
        self.assertEqual(result.returncode, 2)
        self.assertIn("gh issue list failed", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(self.calls()), 1)

    def test_gh_list_invalid_json_or_issue_number_is_an_error(self):
        self.changed()
        for output in ("broken", "{}", "null", '"text"', '[{}]', '[{"number":true}]',
                       '[{"number":-1}]', '[{"number":"@mention"}]'):
            with self.subTest(output=output):
                self.state["issue list"]["out"] = output
                result = self.run_proposal()
                self.assertEqual(result.returncode, 2)
                self.assertIn("gh issue list", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertTrue(all(c["argv"][:2] == ["issue", "list"] for c in self.calls()))

    def test_missing_gh_is_an_error_without_a_real_gh_call(self):
        self.changed()
        result = self.run_proposal("--gh", str(self.root / "missing-gh-executable"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("gh issue list", result.stderr)
        self.assertIn("executable not found", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.calls(), [])

    def test_gh_label_failure_never_creates_an_issue(self):
        self.changed()
        self.state["label create"] = {"code": 1, "err": TOKEN_MARKER}
        result = self.run_proposal()
        self.assertEqual(result.returncode, 2)
        self.assertIn("gh label create failed", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual([c["argv"][:2] for c in self.calls()],
                         [["issue", "list"], ["label", "create"]])

    def test_gh_create_failure_is_an_error_and_removes_the_body_file(self):
        self.changed()
        self.state["issue create"] = {"code": 1, "out": TOKEN_MARKER, "err": TOKEN_MARKER}
        result = self.run_proposal()
        self.assertEqual(result.returncode, 2)
        self.assertIn("gh issue create failed", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(Path(self.calls()[-1]["body_path"]).exists())

    def test_dry_run_lists_but_never_labels_or_creates(self):
        self.changed()
        result = self.run_proposal("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("NIM free catalog changed: +2 / -1 ("))
        self.assertEqual([c["argv"][:2] for c in self.calls()], [["issue", "list"]])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/nim-free-catalog.yml").read_text(encoding="utf-8")
        self.lines = [line for line in self.text.splitlines()
                      if line.strip() and not line.lstrip().startswith("#")]
        self.reference = (ROOT / ".github/workflows/catalog-live-check.yml").read_text(
            encoding="utf-8")

    def block(self, name, indent=0):
        start = self.lines.index(" " * indent + name + ":") + 1
        end = next((i for i in range(start, len(self.lines))
                    if len(self.lines[i]) - len(self.lines[i].lstrip()) <= indent),
                   len(self.lines))
        return self.lines[start:end]

    def test_only_weekly_schedule_and_manual_dispatch_with_a_distinct_off_minute(self):
        triggers = self.block("on")
        self.assertEqual([line for line in triggers if re.match(r"^  \S", line)],
                         ["  schedule:", "  workflow_dispatch:"])
        schedules = [line for line in triggers if line.lstrip().startswith("- cron:")]
        self.assertEqual(len(schedules), 1)
        cron = re.fullmatch(r"    - cron: '([0-9]+ [0-9]+ \* \* [0-6])'", schedules[0])[1]
        minute, hour, _, _, day = cron.split()
        self.assertTrue(1 <= int(minute) <= 59 and int(minute) != 30)
        self.assertTrue(0 <= int(hour) <= 23)
        self.assertEqual((minute, hour, day), ("43", "6", "4"))
        self.assertNotIn("'" + cron + "'", self.reference)

    def test_permissions_are_exact_and_no_secrets_are_referenced(self):
        self.assertEqual(self.block("permissions"), ["  contents: read", "  issues: write"])
        self.assertEqual(sum(line.lstrip().startswith("permissions:")
                             for line in self.lines), 1)
        self.assertNotIn("secrets.", self.text)

    def test_same_runner_and_checkout_then_only_the_proposal_step_with_scoped_token(self):
        runner = next(line.strip() for line in self.reference.splitlines()
                      if line.lstrip().startswith("runs-on:"))
        self.assertEqual([line.strip() for line in self.lines if "runs-on:" in line], [runner])
        steps = self.block("steps", 4)
        checkout = next(line.strip() for line in self.reference.splitlines() if "- uses:" in line)
        self.assertEqual(steps, ["      " + checkout, "        with:",
                                "          persist-credentials: false",
                                "      - name: Propose NIM free catalog changes",
                                "        run: python3 tools/nim-free-catalog-proposal.py",
                                "        env:", "          GH_TOKEN: ${{ github.token }}"])
        self.assertEqual(self.text.count("GH_TOKEN:"), 1)
        self.assertEqual(sum(line.lstrip() == "env:" for line in self.lines), 1)
        self.assertEqual(self.text.count("github.token"), 1)

    def test_overlapping_runs_are_serialized_and_safety_rationale_is_documented(self):
        self.assertEqual(self.block("concurrency"),
                         ["  group: nim-free-catalog-proposal", "  cancel-in-progress: false"])
        comments = " ".join(line.lstrip(" #") for line in self.text.splitlines()
                            if line.lstrip().startswith("#"))
        self.assertIn("pull requests", comments)
        self.assertIn("upstream", comments)
        self.assertIn("never writes repository content", comments)


if __name__ == "__main__":
    unittest.main(verbosity=2)
