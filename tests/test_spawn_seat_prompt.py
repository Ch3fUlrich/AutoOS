import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "tools/autoos-agent.py"
FIRST = ("You have NO tools and no repository access. Everything you need is below. "
         "Answer in plain text only.")
spec = importlib.util.spec_from_file_location("seat_prompt_agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class SeatPromptTests(unittest.TestCase):
    def test_helper_table(self):
        for task, seat, expected, changed in [
            ("review this", True, FIRST + "\n\nreview this", True),
            (FIRST + "\n\nreview this", True, FIRST + "\n\nreview this", False),
            ("\n \t\n" + FIRST, True, "\n \t\n" + FIRST, False),
            ("\n  do x\n", False, "\n  do x\n", False),
        ]:
            with self.subTest(task=task, seat=seat):
                self.assertEqual(agent.seat_prompt(task, seat), (expected, changed))
                self.assertEqual(agent.seat_prompt(expected, seat), (expected, False))
        self.assertEqual(agent.SEAT_FIRST_LINE, FIRST)

    def test_explicit_seat_triggers(self):
        for values in ({"review_base": "HEAD"}, {"review_of": "run"}, {"tier": 3}):
            with self.subTest(values=values):
                self.assertTrue(agent.is_seat_run(argparse.Namespace(**values)))

    def test_review_card(self):
        with mock.patch.object(agent.routing, "parse_card", return_value={"role": "review"}) as parse:
            with mock.patch.object(agent, "_card_asks_review", return_value=True):
                self.assertTrue(agent.is_seat_run(argparse.Namespace(card="review card")))
            parse.assert_called_once_with("review card")

    def test_plain_run(self):
        self.assertFalse(agent.is_seat_run(argparse.Namespace()))
        self.assertFalse(agent.is_seat_run(argparse.Namespace(read_only=True)))

    def test_malformed_card(self):
        for value in (ValueError(), KeyError(), TypeError(), []):
            kwargs = {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}
            with mock.patch.object(agent.routing, "parse_card", **kwargs):
                self.assertFalse(agent.is_seat_run(argparse.Namespace(card="bad")))

    def plan(self, task, seat=True):
        with tempfile.TemporaryDirectory(prefix="seat-prompt-") as workers:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("AUTOOS_", "GIT_CONFIG_"))}
            env.update(AUTOOS_WORKERS_DIR=workers, AUTOOS_ADMISSION_OFF="1")
            flags = (["--client", "qoder", "--model", "qwen3.8-flash", "--read-only",
                      "--review-base", "HEAD"] if seat else
                     ["--model", "mimo/mimo-v2.6-flash"])
            result = subprocess.run(
                [sys.executable, str(AGENT), "run", *flags, "--dry-run", "--isolate", task],
                cwd=ROOT, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_cli_prepends(self):
        result = self.plan("review this")
        self.assertEqual(result.stdout.count(FIRST), 1)
        self.assertIn(FIRST + "\n\nreview this", result.stdout)
        self.assertEqual(result.stderr.count("seat-prompt: prepended the no-tools first line"), 1)

    def test_cli_existing_line(self):
        result = self.plan(FIRST + "\n\nreview this")
        self.assertEqual(result.stdout.count(FIRST), 1)
        self.assertNotIn("seat-prompt:", result.stderr)

    def test_cli_plain_unchanged(self):
        result = self.plan("do x", seat=False)
        self.assertNotIn(FIRST, result.stdout)
        self.assertNotIn("seat-prompt:", result.stderr)


if __name__ == "__main__":
    unittest.main()
