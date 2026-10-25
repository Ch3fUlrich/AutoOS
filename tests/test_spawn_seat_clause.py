import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("seat_clause_agent", ROOT / "tools/autoos-agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class SeatClauseTests(unittest.TestCase):
    def test_exclusions(self):
        for text, expected in [
            ("2 non-Claude seats", ["anthropic"]),
            ("two non-openai families", ["openai"]),
            ("1 non-anthropic seat", ["anthropic"]),
            ("non-Claude seats only", ["anthropic"]),
            ("both not Haiku reviewers", ["anthropic"]),
            ("no claude seat", ["anthropic"]),
            ("all non-qwen verdicts", ["qwen"]),
            ("a non-Claude seat", ["anthropic"]),
            ("Review diff: non-anthropic seat", ["anthropic"]),
            ("10 non-Claude seats", ["anthropic"]),
            ("123 non-Claude seats", ["anthropic"]),
            ("twelve not-anthropic reviewers", ["anthropic"]),
            ("eleven non claude families", ["anthropic"]),
            ("NON CLAUDE seats", ["anthropic"]),
            ("NON-CLAUDE seats", ["anthropic"]),
            ("do not use Claude seats", ["anthropic"]),
            ("do not use Claude Code for this", []),
            ("2 non-Claude reviewers, but each verdict must be independent", ["anthropic"]),
            ("2 seats: non-Claude", ["anthropic"]),
            ("seat clause: non-anthropic", ["anthropic"]),
            ("non-anthropic", []),
            ("seat. non-Claude", []),
            ("seat; non-Claude", []),
            ("seat\nnon-Claude", []),
            ("non-Claude one two seats", []),
            ("non-Claude one two three seats", []),
            ("seats one two non-Claude", []),
            ("seats one two three non-Claude", []),
            ("non-Claude. seats", []),
            ("non-Claude; seats", []),
            ("non-Claude\nseats", []),
            ("do not use Claude Code for this", []),
            ("step 2 of the non-claude runbook", []),
            ("non-Claude prose without any restriction", []),
            ("", []),
            ("Reviewer: verify non-Claude compatibility", []),
            ("Reviewer note: non-Claude tests passed", []),
            ("Summary of verdicts: non-Claude runs passed", []),
            ("There is no Claude verdict yet, please run review", []),
            ("do not use Claude Code", []),
            ("non-Claude independent seats", ["anthropic"]),
            ("seats = not Claude", ["anthropic"]),
            ("seat requirement - non-anthropic", ["anthropic"]),
            ("do not use python seats", []),
            ("do not use tabs for this seat", []),
            ("do not choose unknownvendor reviewers", []),
        ]:
            with self.subTest(text=text):
                self.assertEqual(agent.seat_clause_exclusions(text), expected)

    def test_hunk_accounting(self):
        # 1. Blank context line inside a hunk followed by a '+ # non-gemini reviewers' line (ignored)
        task_blank_ctx = (
            "Review diff\n"
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,3 +1,3 @@\n"
            " def foo():\n"
            "\n"
            "+    # non-gemini reviewers\n"
            "     return True\n"
        )
        self.assertEqual(agent.seat_clause_exclusions(task_blank_ctx), [])

        # 2. Metadata line 'rename from docs/not-use-claude-seats.md' (ignored)
        task_rename = (
            "Review diff\n"
            "diff --git a/docs/not-use-claude-seats.md b/docs/claude-seats.md\n"
            "similarity index 100%\n"
            "rename from docs/not-use-claude-seats.md\n"
            "rename to docs/claude-seats.md\n"
        )
        self.assertEqual(agent.seat_clause_exclusions(task_rename), [])

        # 3. Prose clause after the patch ('2 non-Claude seats') still honoured
        task_prose_after = (
            "Review diff\n"
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
            "\n"
            "2 non-Claude seats\n"
        )
        self.assertEqual(agent.seat_clause_exclusions(task_prose_after), ["anthropic"])

        # 4. Two files in one patch
        task_two_files = (
            "Review diff\n"
            "diff --git a/f1.py b/f1.py\n"
            "--- a/f1.py\n"
            "+++ b/f1.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-all non-qwen verdicts\n"
            "+valid code\n"
            "diff --git a/f2.py b/f2.py\n"
            "--- a/f2.py\n"
            "+++ b/f2.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+all non-gemini reviewers\n"
            "\n"
            "2 non-Claude seats\n"
        )
        self.assertEqual(agent.seat_clause_exclusions(task_two_files), ["anthropic"])

        # 5. A hunk with '\ No newline'
        task_no_newline = (
            "Review diff\n"
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-all non-qwen verdicts\n"
            "\\ No newline at end of file\n"
            "+all non-gemini reviewers\n"
            "\\ No newline at end of file\n"
            "\n"
            "2 non-Claude seats\n"
        )
        self.assertEqual(agent.seat_clause_exclusions(task_no_newline), ["anthropic"])

    def test_refusal(self):
        task = "2 non-Claude seats"
        message = agent.seat_clause_refusal(task, "anthropic", ["anthropic"])
        self.assertIn("non-Claude", message)
        self.assertIn("anthropic", message)
        self.assertIsNone(agent.seat_clause_refusal(task, "qwen", ["anthropic"]))
        self.assertIsNone(agent.seat_clause_refusal(task, "anthropic", []))

    def test_sentence_phrase_boundaries(self):
        for prefix in ("Run 3 tests.", "Review item 1;", "Review item 1\n"):
            for clause in ("non-Claude seats only", "no claude seat"):
                self.assertEqual(list(agent._seat_clause_matches(prefix + " " + clause)),
                                 [("anthropic", clause)])

    def test_item_only(self):
        for marker in ("diff --git a/test b/test", "--- a/test", "+++ b/test"):
            task = "Review this\n" + marker + "\n@@ hunk\n+all non-qwen verdicts\n-all non-qwen verdicts\n all non-qwen verdicts\n\nall non-qwen verdicts"
            self.assertEqual(agent.seat_clause_exclusions(task), ["qwen"])
            result = self.run_cli("qwen3.8-flash", task=task)
            self.assertEqual(result.returncode, 2, result.stderr)
            task = "Review this\n" + marker + "\n@@ hunk\n+all non-qwen verdicts\n\nReview only"
            self.assertEqual(agent.seat_clause_exclusions(task), [])
            result = self.run_cli("qwen3.8-flash", task=task)
            self.assertEqual(result.returncode, 0, result.stderr)
        for fence in ("```", "```python", "```diff\n--- a/app.py\n+++ b/app.py", "~~~", "~~~diff\n--- a/app.py\n+++ b/app.py"):
            task = "Review this\n" + fence + "\nall non-qwen verdicts\n" + fence[:3] + "\n2 non-Claude seats"
            self.assertEqual(agent.seat_clause_exclusions(task), ["anthropic"])
            result = self.run_cli("qwen3.8-flash", task=task)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_cli("claude-haiku-4-5", task=task)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("seat clause", result.stderr)
            self.assertIn("2 non-Claude seats", result.stderr)

    def run_cli(self, model, seat=True, task="2 non-Claude seats"):
        with tempfile.TemporaryDirectory(prefix="seat-prompt-") as tmp:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("AUTOOS_", "GIT_CONFIG_"))}
            env.update(AUTOOS_WORKERS_DIR=tmp, AUTOOS_ADMISSION_OFF="1")
            cmd = [sys.executable, str(ROOT / "tools/autoos-agent.py"), "run",
                   "--client", "qoder", "--model", model, "--dry-run", "--read-only", "--isolate"]
            if seat:
                cmd += ["--tier", "3"]
            return subprocess.run(cmd + [task], cwd=ROOT, env=env,
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_cli_clause_shapes(self):
        for task, model in [
            ("2 non-Claude reviewers, but each verdict must be independent", "claude-haiku-4-5"),
            ("both not Haiku reviewers", "claude-haiku-4-5"),
            ("all non-qwen verdicts", "qwen3.8-flash"),
        ]:
            with self.subTest(task=task):
                result = self.run_cli(model, task=task)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("seat clause", result.stderr)

    def test_claude_seat_refused(self):
        result = self.run_cli("claude-haiku-4-5")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("seat clause", result.stderr)
        self.assertIn("anthropic", result.stderr)

    def test_qwen_seat_passes(self):
        result = self.run_cli("qwen3.8-flash")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonseat_unchanged(self):
        result = self.run_cli("claude-haiku-4-5", seat=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_seat_refused(self):
        result = self.run_cli("unplaceable-seat-model")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("seat model family unknown, cannot satisfy the clause", result.stderr)


if __name__ == "__main__":
    unittest.main()
