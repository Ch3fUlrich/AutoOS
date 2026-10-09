"""AO-REDACT-SPAN: ``Redactor._one_line`` must mask only the secret it matched.

Evidence (runs 20261009-054241 / 055402 / 060115, logs/agents/*/output.log): a
worker report that merely MENTIONED ``-----BEGIN ... PRIVATE KEY-----`` — a
regex or a python list of fixture strings, with no END anywhere — set the
redactor's PEM mode and swallowed EVERY later line, so the report lost its
trailing ``VERDICT:`` line and the reviewer looked like it never answered. The
BEGIN line itself was replaced whole, erasing the prose in front of it.

AO-REDACT-SPAN REWORK (attacker review): the same state machine leaked in the
other direction. A REAL key's body is not one unbroken run of 16+ base64
characters — it ends with a ragged short line (`YQ==`, `abc=`, `===`), and a
copy-paste carries blank and indented lines. ``_PEM_BODY_RE``'s `{16,}`
fullmatch called those "not key material", so PEM mode ended early and the body
tail, the END marker and everything after them printed in plaintext. `PemLeakTests`
pins the leak closed; `PemSurvivalTests` pins that the permissive body shape
did not re-open the original hole (an unterminated mention still lets its prose
and its VERDICT through).

Synthetic secrets only (AGENTS.md rule 1): the shapes are invented, and the
``sk-`` / PEM literals are split the way ``test_autoos_spawner.py`` splits them
so this tracked file never carries a key shape as one string.

    python3 tests/test_autoos_redact_span.py
"""
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import autoos_redact as redact  # noqa: E402

MASK = redact.TEXT_MASK
BEGIN = "-----BEGIN" + " RSA PRIVATE KEY-----"
END = "-----END" + " RSA PRIVATE KEY-----"
# A base64-shaped line: what a real key body looks like, and what the swallow
# is now allowed to consume.
BODY = "MIIBogIBAAJBALRm9DaFhwmB8QKB8CgYQK0AAAAAAAAAAAAAAAAAAAAB"


def stream(*lines):
    """One redactor, fed line by line as the spawner's pump does."""
    red = redact.Redactor()
    return "".join(red.text(line + "\n") for line in lines), red


def body_line(i: int) -> str:
    """One synthetic base64 body line (56 chars, alnum only)."""
    return "%s%016d" % (BODY[:40], i)


# A short final body line: real openssl output ends the base64 block with a
# ragged last line ("YQ==", "abc=", "==="), none of it 16 chars long. The
# attacker review's leak: the {16,} body test gave up on those lines, so PEM
# mode ended early and the END marker plus the key tail printed in plaintext.
TAIL_SHORT = "YQ" + "=="
TAIL_PAD = "abc" + "="
TAIL_EQUALS = "=" * 3


def rsa_key_lines(*extra_body):
    """A realistic RSA-2048-shaped block: body lines, the ragged last line,
    then END. `extra_body` inserts blanks/indents/padding lines."""
    return ([BEGIN, body_line(1), body_line(2)] + list(extra_body) + [END])


class PemLeakTests(unittest.TestCase):
    """The leaks an attacker review found: a real key whose body is not one
    unbroken run of 16+ base64 characters must still be masked whole."""

    def _assert_key_gone(self, out, tail_line="tail of the report"):
        for probe in (BEGIN, END, body_line(1), body_line(2),
                      TAIL_SHORT, TAIL_PAD, TAIL_EQUALS):
            self.assertNotIn(probe, out, "key material leaked:\n" + out)
        self.assertIn(tail_line, out, "the report after the key was lost:\n" + out)

    def test_a_key_with_a_short_last_body_line_is_fully_masked(self):
        out, red = stream(*rsa_key_lines(TAIL_SHORT), "tail of the report")
        self._assert_key_gone(out)
        self.assertEqual(out, MASK + "\n\n\n\n" + MASK + "\ntail of the report\n")
        self.assertEqual(red.count, 1, "one key counts once")

    def test_a_key_with_a_blank_line_in_the_body_is_fully_masked(self):
        out, _red = stream(*rsa_key_lines("", "   ", TAIL_PAD), "tail of the report")
        self._assert_key_gone(out)

    def test_a_key_with_a_whitespace_only_line_in_the_body_is_fully_masked(self):
        out, _red = stream(BEGIN, body_line(1), " \t ", body_line(2), TAIL_EQUALS,
                           END, "tail of the report")
        self._assert_key_gone(out)

    def test_an_indented_key_body_and_end_are_fully_masked(self):
        # a key pasted into a markdown list / an indented code block
        lines = ["    " + BEGIN, "        " + body_line(1), "\t" + body_line(2),
                 "    " + TAIL_SHORT, "    " + END]
        out, _red = stream(*lines, "tail of the report")
        self._assert_key_gone(out)
        self.assertIn("tail of the report", out)

    def test_a_padding_only_body_line_is_swallowed(self):
        out, _red = stream(BEGIN, body_line(1), TAIL_EQUALS, TAIL_PAD, END,
                           "tail of the report")
        self._assert_key_gone(out)

    def test_a_key_fed_with_crlf_line_endings_is_fully_masked(self):
        red = redact.Redactor()
        report = "\r\n".join(rsa_key_lines(TAIL_SHORT) + ["tail of the report"]) + "\r\n"
        out = red.text(report)
        self._assert_key_gone(out)
        self.assertIn("\r\n", out, "the line endings of the stream are kept")

    def test_line_by_line_and_whole_stream_give_identical_output(self):
        # the spawner pumps line by line; a caller may hand the whole report at
        # once. Same key, same bytes out, or one of the two paths leaks.
        lines = rsa_key_lines("", TAIL_SHORT) + [
            BEGIN, body_line(3), "Proc-Type: 4,ENCRYPTED",
            "DEK-Info: AES-256-CBC,0123456789ABCDEF", body_line(4), "  ",
            "\t" + TAIL_PAD, END, "after the keys", "VERDICT: ACCEPT"]
        whole = redact.Redactor().text("".join(ln + "\n" for ln in lines))
        stepwise, _red = stream(*lines)
        self.assertEqual(whole, stepwise)
        self._assert_key_gone(whole, "after the keys")
        self.assertIn("VERDICT: ACCEPT", whole)

    def test_an_end_marker_inside_prose_is_still_masked(self):
        out, _red = stream(BEGIN, body_line(1), "and the key closes with " + END,
                           "tail of the report")
        self._assert_key_gone(out)
        self.assertIn("and the key closes with ", out)

    def test_indented_pem_encryption_headers_are_swallowed(self):
        red = redact.Redactor()
        out = red.text("\n".join([BEGIN, "    Proc-Type: 4,ENCRYPTED",
                                  "\tDEK-Info: AES-256-" + "CBC,0123456789ABCDEF",
                                  "   ", TAIL_PAD, END, "tail of the report"]) + "\n")
        self._assert_key_gone(out)
        self.assertNotIn("Proc-Type", out)
        self.assertEqual(red.count, 1)

    def test_body_glued_to_the_begin_line_and_its_short_tail_are_masked(self):
        # a single-line log record of a key: the BEGIN keeps its prefix, the rest
        # of that line is body, and the short tail under it must not resurface.
        out, red = stream(BEGIN + " MIIEvA" + "IBAAJa" * 8, TAIL_SHORT, END,
                          "tail of the report")
        self._assert_key_gone(out)
        self.assertEqual(out, MASK + "\n\n" + MASK + "\ntail of the report\n")
        self.assertEqual(red.count, 1)

    def test_two_keys_in_one_stream_are_both_masked_and_counted(self):
        out, red = stream(*rsa_key_lines(TAIL_SHORT), "between the keys",
                          *rsa_key_lines(TAIL_EQUALS), "tail of the report")
        self._assert_key_gone(out)
        self.assertIn("between the keys", out)
        self.assertEqual(red.count, 2)


class PemSurvivalTests(unittest.TestCase):
    """What the fix must NOT cost: an unterminated mention still lets the
    report through, VERDICT included."""

    def test_an_unterminated_mention_followed_by_prose_keeps_prose_and_verdict(self):
        lines = ["the detector fixture is '" + BEGIN + "'",
                 "which is a marker, not a key",
                 "VERDICT: ACCEPT"]
        out, _red = stream(*lines)
        self.assertIn("which is a marker, not a key", out)
        self.assertIn("VERDICT: ACCEPT", out)
        self.assertNotIn(BEGIN, out)

    def test_a_prose_line_ends_the_swallow_and_later_lines_are_normal(self):
        out, _red = stream(BEGIN, body_line(1), "the run finished cleanly",
                           body_line(2))
        self.assertNotIn(body_line(1), out, "the body before the prose is swallowed")
        self.assertIn("the run finished cleanly", out)
        self.assertIn(body_line(2), out, "a body line after prose is a normal line")

    def test_a_short_alnum_line_after_a_mention_may_be_lost_but_verdict_survives(self):
        # DOCUMENTED EDGE: "short" is body-shaped, so it is swallowed with the
        # open BEGIN; the first line that is NOT body ends the mode and the
        # verdict always reaches the caller.
        out, _red = stream(BEGIN, "short", "MERGE_OK", "VERDICT: ACCEPT")
        self.assertIn("MERGE_OK", out)
        self.assertIn("VERDICT: ACCEPT", out)


class PemSpanTests(unittest.TestCase):
    def test_a_begin_and_end_on_one_line_masks_only_that_span(self):
        out, red = stream("the log record is " + BEGIN + " " + END + " and done")
        self.assertEqual(out, "the log record is " + MASK + " and done\n")
        self.assertEqual(red.count, 1, "one key counts once, not twice")

    def test_an_open_begin_keeps_its_prefix_and_masks_to_end_of_line(self):
        out, red = stream("the fixture reads ['" + BEGIN, "next line of the report",
                          "VERDICT: ACCEPT")
        self.assertTrue(out.startswith("the fixture reads ['" + MASK), out)
        self.assertNotIn(BEGIN, out)
        self.assertIn("next line of the report", out)
        self.assertIn("VERDICT: ACCEPT", out)
        self.assertEqual(red.count, 1)

    def test_key_body_under_an_open_begin_is_swallowed_until_the_end(self):
        out, _red = stream(BEGIN, BODY, "Proc-Type: 4,ENCRYPTED",
                           "DEK-Info: AES-256-CBC,0123456789ABCDEF", END, "after")
        # a swallowed line still emits its newline, so the stream keeps its shape
        self.assertEqual(out, MASK + "\n\n\n\n" + MASK + "\nafter\n")

    def test_a_non_body_line_ends_the_swallow_and_is_processed_normally(self):
        first = "%s%016d" % (BODY[:40], 1)
        second = "%s%016d" % (BODY[:40], 2)
        out, _red = stream(BEGIN, first, "the run finished cleanly", second)
        self.assertNotIn(first, out, "the body before the prose is swallowed")
        self.assertIn("the run finished cleanly", out, "prose ends the swallow")
        self.assertIn(second, out, "a body-shaped line after prose is a normal line")

    def test_a_full_pem_block_is_masked_and_the_report_around_it_survives(self):
        block = [BEGIN] + ["%s%016d" % (BODY[:40], i) for i in range(25)] + [END]
        out, red = stream(*block, "end of report", "VERDICT: ACCEPT")
        for i in range(25):
            self.assertNotIn("%s%016d" % (BODY[:40], i), out)
        self.assertNotIn(BEGIN, out)
        self.assertNotIn(END, out)
        self.assertIn("end of report", out)
        self.assertIn("VERDICT: ACCEPT", out)
        self.assertEqual(red.count, 1, "25 body lines and the END are ONE key")

    def test_the_swallow_gives_up_after_the_body_cap(self):
        lines = [BEGIN] + ["%s%016d" % (BODY[:40], i)
                            for i in range(redact.PEM_BODY_CAP + 50)]
        out, _red = stream(*lines, "the tail of the report", "VERDICT: ACCEPT")
        self.assertIn("the tail of the report", out)
        self.assertIn("VERDICT: ACCEPT", out)

    def test_a_verdict_line_ends_pem_mode_and_keeps_its_own_masking(self):
        red = redact.Redactor()
        self.assertEqual(red.text(BEGIN + "\n"), MASK + "\n")
        self.assertEqual(red.text(BODY + "\n"), "\n", "the key body is swallowed")
        self.assertEqual(red.text("VERDICT: REJECT token=" "abc123def456" + "\n"),
                         "VERDICT: REJECT token=" + MASK + "\n")
        # PEM mode is over: a body-shaped line that follows is an ordinary line.
        self.assertEqual(red.text(BODY + "\n"), BODY + "\n")

    def test_a_short_base64_line_is_body_under_an_open_begin(self):
        # AO-REDACT-SPAN rework: a short alnum-only line IS key body now (the
        # ragged last line of a real key). It is only "lost" because the BEGIN
        # it follows is never closed, and the first non-body line ends the mode.
        out, _red = stream(BEGIN, "short", "MERGE_OK", "VERDICT: ACCEPT")
        self.assertNotIn("short", out)
        self.assertIn("MERGE_OK", out)
        self.assertIn("VERDICT: ACCEPT", out)

    def test_a_report_that_mentions_a_marker_keeps_every_line_and_the_verdict(self):
        # The observed shape: a python list of detector fixtures, one of them a
        # PEM marker, with no END anywhere in the stream.
        lines = [
            "def check(pattern):",
            "    cases = [",
            "        ('MY_KEY = value', '=~ operator'),",
            "        'API_KEY=" "sk-test-abcdef123456" + "',",
            "        # PEM",
            "        '" + BEGIN + "',",
            "        'BEGIN " + "[A-Z ]*PRIVATE' in text",
            "    ]",
            "    return len(cases)",
            "VERDICT: ACCEPT",
        ]
        out, red = stream(*lines)
        for probe in ("def check(pattern):", "'=~ operator'",
                      "'API_KEY=" + MASK, "        # PEM", "'BEGIN ",
                      "    ]", "return len(cases)", "VERDICT: ACCEPT"):
            self.assertIn(probe, out, "lost a line of the report:\n" + out)
        self.assertNotIn(BEGIN, out, "the marker itself is masked")
        self.assertNotIn("sk-test-abcdef123456", out)
        self.assertGreaterEqual(red.count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
