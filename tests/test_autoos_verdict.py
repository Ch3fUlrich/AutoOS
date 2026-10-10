#!/usr/bin/env python3
"""Tests for tools/autoos_verdict.py (AO-SEAT-VERDICT-GRAMMAR) — red before the module:
`**VERDICT:** ACCEPT` parsed as no verdict, a blind seat was counted, entry `**ACCEPT**`
refused. P2 adds G2 (blindness may only VOID an ACCEPT, and reads the seat's own first-
person statements — not a substring), G3 (ONE normaliser behind both doors) and the seat
template's proof line becoming required."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_verdict as v  # noqa: E402

CRITERIA = "R3: no secret leaves the sandbox; <= 200 lines changed; both suites green."
BASE = "sandbox base: %s HEAD %s" % ("a" * 40, "b" * 40)
# What a seat says about ITS OWN view of the change voids an ACCEPT (G2); NBSP and the
# zero-width spellings are the same sentence to a reader, so they are one to the gate.
BLIND_LINES = ("I could not see the diff", "I couldn't read the patch",
               "we cannot access the change", "this seat was unable to read the diff",
               "my seat did not find the patch", "REVIEW-DIFF.patch is empty",
               "REVIEW-DIFF.patch was not present", "no access to the diff",
               "I could not see the\u00a0diff", "I \u200bcould not see the diff")
# Ordinary review prose about something that is NOT the seat's own view (G2 false-positives
# the substring check used to fire on).
NOT_BLIND_LINES = ("the fixture cannot see the row",
                   "the test double was unable to read the file",
                   "I did not find any issue in the diff",
                   "I could not see why anyone would REJECT this")


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
                           ("- VERDICT: HOLD", "HOLD"), ("VERDICT: ACCEPT.", "ACCEPT"),
                           ("VERDICT: ACCEPT!", "ACCEPT"), ("> # VERDICT: ACCEPT", "ACCEPT"),
                           ("1. VERDICT: ACCEPT", "ACCEPT"), ("1) VERDICT: REJECT", "REJECT"),
                           ("2. **VERDICT:** HOLD", "HOLD"), ("**VERDICT: ACCEPT.**", "ACCEPT")):
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

    def test_blindness_voids_an_accept_and_nothing_else(self):
        # G2, in the fail-closed direction: a seat that says IT never saw the change has
        # no ACCEPT to give; its REJECT is still a finding, and a HOLD is the honest answer.
        for line in BLIND_LINES:
            accept = v.parse_verdict("VERDICT: ACCEPT\nnote: %s\n" % line)
            self.assertFalse(accept["valid"], line)
            self.assertFalse(accept["countable"], line)
            self.assertIn("says it", accept["reason"], line)
            reject = v.parse_verdict("VERDICT: REJECT\nnote: %s\n" % line)
            self.assertTrue(reject["valid"] and reject["countable"], (line, reject["reason"]))
            held = v.parse_verdict("VERDICT: HOLD\nnote: %s\n" % line)
            self.assertTrue(held["valid"] and not held["countable"], (line, held["reason"]))

    def test_a_seat_talking_about_something_else_is_not_declaring_itself_blind(self):
        for line in NOT_BLIND_LINES:
            got = v.parse_verdict("VERDICT: ACCEPT\nnote: %s\n" % line)
            self.assertTrue(got["valid"], (line, got["reason"]))
            self.assertTrue(got["countable"], line)

    def test_the_entry_field_and_the_seat_line_agree_over_one_table(self):
        # G3: `verdict_word` reads the record's `verdict=`, `parse_verdict` reads the seat's
        # answer, and both go through ONE normaliser — so decoration can never make the two
        # doors disagree about the same word.
        for entry_form, seat_form, want in (
                ("ACCEPT", "VERDICT: ACCEPT", "ACCEPT"),
                ("ACCEPT.", "VERDICT: ACCEPT.", "ACCEPT"),
                ("ACCEPT!", "VERDICT: ACCEPT!", "ACCEPT"),
                ("**ACCEPT**", "**VERDICT:** ACCEPT", "ACCEPT"),
                ("`ACCEPT`", "`VERDICT: accept`", "ACCEPT"),
                ("- ACCEPT", "- VERDICT: ACCEPT", "ACCEPT"),
                ("1. ACCEPT", "1. VERDICT: ACCEPT", "ACCEPT"),
                ("1) accept", "1) VERDICT: accept", "ACCEPT"),
                ("> # ACCEPT", "> # VERDICT: ACCEPT", "ACCEPT"),
                ("REJECT.", "VERDICT: REJECT.", "REJECT"),
                ("**REJECT**", "**VERDICT: REJECT**", "REJECT"),
                ("HOLD.", "VERDICT: HOLD", "HOLD")):
            self.assertEqual(want, v.verdict_word(entry_form), entry_form)
            self.assertEqual(want, v.parse_verdict(seat_form + "\nfindings\n")["verdict"],
                             seat_form)
        self.assertEqual("FIX-FIRST", v.verdict_word("fix-first"))
        self.assertFalse(v.parse_verdict("VERDICT: fix-first")["valid"])
        self.assertEqual((False, "verdict fix-first"), self.entry("fix-first"))

    def test_the_seat_gets_its_angle_its_criteria_and_its_one_proof_line(self):
        text = v.seat_prompt("attacker", CRITERIA, BASE)
        for needle in (CRITERIA, "attacker", "VERDICT: ACCEPT|REJECT|HOLD", "REJECT only",
                       "repro", BASE):
            self.assertIn(needle, text)
        self.assertEqual(1, text.count("sandbox base:"), text)

    def test_the_seat_template_refuses_a_prompt_without_a_proof_line(self):
        # G1b: an empty base_line printed a template that asked for nothing, so a seat
        # could answer without ever naming what it reviewed.
        for empty in ("", None, "   ", "\n"):
            with self.assertRaises(ValueError, msg=repr(empty)):
                v.seat_prompt("attacker", CRITERIA, empty)
        with self.assertRaises(TypeError):   # and it is required, not defaulted
            v.seat_prompt("attacker", CRITERIA)

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
