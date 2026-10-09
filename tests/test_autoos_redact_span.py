"""AO-REDACT-SPAN: ``Redactor._one_line`` must mask only the secret it matched.

Evidence (runs 20261009-054241 / 055402 / 060115, logs/agents/*/output.log): a
worker report that merely MENTIONED ``-----BEGIN ... PRIVATE KEY-----`` — a
regex or a python list of fixture strings, with no END anywhere — set the
redactor's PEM mode and swallowed EVERY later line, so the report lost its
trailing ``VERDICT:`` line and the reviewer looked like it never answered. The
BEGIN line itself was replaced whole, erasing the prose in front of it.

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

    def test_a_short_base64_line_is_never_swallowed(self):
        out, _red = stream(BEGIN, "short", "MERGE_OK")
        self.assertIn("short", out)
        self.assertIn("MERGE_OK", out)

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
