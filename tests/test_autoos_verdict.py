#!/usr/bin/env python3
"""Tests for tools/autoos_verdict.py (AO-SEAT-VERDICT-GRAMMAR) — red before the module:
`**VERDICT:** ACCEPT` parsed as no verdict, a blind seat was counted, entry `**ACCEPT**`
refused."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_verdict as v  # noqa: E402

CRITERIA = "R3: no secret leaves the sandbox; <= 200 lines changed; both suites green."
BASE = "sandbox base: %s HEAD %s" % ("a" * 40, "b" * 40)


class VerdictGrammarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "autoos_agent", str(ROOT / "tools" / "autoos-agent.py"))
        cls.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.agent)

    def entry(self, word):
        return self.agent._review_entry_verdict({"verdict": word})

    def test_markdown_on_the_first_line_is_normalised_and_countable(self):
        for text, want in (("VERDICT: ACCEPT", "ACCEPT"), ("**VERDICT:** ACCEPT", "ACCEPT"),
                           ("**VERDICT: ACCEPT**", "ACCEPT"), ("`VERDICT: accept`", "ACCEPT"),
                           ("# VERDICT: REJECT", "REJECT"), ("> VERDICT: reject", "REJECT"),
                           ("- VERDICT: HOLD", "HOLD"), ("VERDICT: ACCEPT.", "ACCEPT")):
            got = v.parse_verdict(text + "\nfindings underneath it\n")
            self.assertTrue(got["valid"], (text, got["reason"]))
            self.assertEqual(want, got["verdict"], text)
            self.assertEqual(want != "HOLD", got["countable"], text)  # a HOLD is no seat

    def test_anything_but_one_bare_word_on_line_one_is_invalid(self):
        for text, why in (("I looked at the diff.\nVERDICT: ACCEPT\n", "first"),
                          ("VERDICT: SHIP", "SHIP"), ("VERDICT: LGTM", "LGTM"),
                          ("VERDICT: ACCEPT but the ref is unread", "words after"),
                          ("VERDICT: ACCEPT\nVERDICT: REJECT\n", "two"),
                          ("VERDICT: ACCEPT: sure", "colon"), ("", "no non-empty line")):
            got = v.parse_verdict(text)
            self.assertFalse(got["valid"], text)
            self.assertIn(why, got["reason"], text)

    def test_a_blind_seat_is_no_seat_whatever_its_line_says(self):
        for phrase in v.BLIND_PHRASES:
            got = v.parse_verdict("VERDICT: ACCEPT\nnote: %s\n" % phrase)
            self.assertFalse(got["valid"], phrase)
            self.assertFalse(got["countable"], phrase)
            self.assertIn(phrase, got["reason"])
        held = v.parse_verdict("VERDICT: HOLD\nnote: could not see the diff\n")
        self.assertTrue(held["valid"] and not held["countable"], held["reason"])

    def test_the_seat_gets_its_angle_its_criteria_and_the_grammar(self):
        text = v.seat_prompt("attacker", CRITERIA)
        for needle in (CRITERIA, "attacker", "VERDICT: ACCEPT|REJECT|HOLD", "REJECT only",
                       "repro"):
            self.assertIn(needle, text)
        self.assertNotIn(BASE, text)
        self.assertIn(BASE, v.seat_prompt("final", CRITERIA, BASE))

    def test_the_entry_field_reads_the_same_grammar(self):
        for word in ("ACCEPT", "**ACCEPT**", "accept", "`ACCEPT`"):
            self.assertEqual((True, None), self.entry(word), word)
        for word in sorted(self.agent.READY_VERDICTS):   # every legacy word still works
            self.assertEqual((True, None), self.entry(word.upper()), word)
        for word in ("REJECT", "**REJECT**", "HOLD", "fix-first", "not-ready", ""):
            ok, reason = self.entry(word)
            self.assertFalse(ok, word)
            self.assertIn("verdict", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
