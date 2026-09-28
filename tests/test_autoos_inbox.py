"""Tests for the shared inbox reader: tools/autoos_inbox.py and the
`autoos-agent.py inbox` verb (RESTART spec §0/§2, lane R1).

Fixtures are synthetic files in a temp dir, built from the timestamp shapes
measured in a real inbox (same-second collisions, a minute-precision line, late
records, a multi-line record). Nothing is spawned, no gateway is called.

Run from the repo root:

    python3 tests/test_autoos_inbox.py [ClassName]
"""
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))

import autoos_inbox as inbox  # noqa: E402


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AGENT_MOD = load_agent()

# The four real records the sample inbox writes in one second (2026-09-25T19:21:08Z).
COLLIDING = (
    "2026-09-25T19:21:08Z from L1-backlog: ask - F5 is in my backlog but both files are yours.\n"
    "2026-09-25T19:21:08Z lesson: OpenHands SDK 1.36.0 loads .agents/skills natively.\n"
    "2026-09-25T19:21:08Z lesson: auto-mode classifier denies the ai-stack.sh migrate plan.\n"
    "2026-09-25T19:21:08Z lesson: six unnamed playwright containers were running.\n"
)


class _Files(unittest.TestCase):
    """Writes a fixture file and hands out its path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autoos-inbox-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text, name="inbox.md"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


# ── §0: the module ──────────────────────────────────────────────────────────

class ParseTests(_Files):
    def test_a_record_is_a_timestamp_line_plus_its_continuations(self):
        path = self.write(
            "2026-09-25T20:19:14Z from L1-main: TASK, your half:\n"
            "  (a) Serena: correct per-worktree answers.\n"
            "  (b) the launch line + trust_worktree.py.\n"
            "2026-09-25T20:34:00Z main=a4eac1a\n")
        records = inbox.read_records(path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].text, "from L1-main: TASK, your half:")
        self.assertEqual(records[0].continuations,
                         ["  (a) Serena: correct per-worktree answers.",
                          "  (b) the launch line + trust_worktree.py."])
        self.assertEqual(records[1].continuations, [])

    def test_a_leading_untimestamped_block_is_not_a_record(self):
        path = self.write("# inbox L1-routing (append dated lines)\n"
                          "some prose under the header\n" + COLLIDING)
        records = inbox.read_records(path)
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0].timestamp, "2026-09-25T19:21:08Z")
        self.assertTrue(records[0].text.startswith("from L1-backlog"))

    def test_positions_number_records_sharing_a_second_in_file_order(self):
        path = self.write(COLLIDING)
        records = inbox.read_records(path)
        self.assertEqual([r.position for r in records],
                         ["2026-09-25T19:21:08Z#1", "2026-09-25T19:21:08Z#2",
                          "2026-09-25T19:21:08Z#3", "2026-09-25T19:21:08Z#4"])

    def test_a_torn_final_line_without_a_newline_is_not_read(self):
        path = self.write("2026-09-25T20:34:00Z main=a4eac1a\n"
                          "2026-09-25T20:34:19Z main=2d0c5aa (docs/tasks.o")
        records = inbox.read_records(path)
        self.assertEqual([r.position for r in records], ["2026-09-25T20:34:00Z#1"])

    def test_the_same_torn_line_is_read_whole_once_the_writer_finishes(self):
        torn = "2026-09-25T20:34:19Z main=2d0c5aa (docs/tasks.md rows only)\n"
        path = self.write("2026-09-25T20:34:00Z main=a4eac1a\n")
        self.assertEqual(len(inbox.read_records(path)), 1)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(torn)
        records = inbox.read_records(path)
        self.assertEqual([r.position for r in records],
                         ["2026-09-25T20:34:00Z#1", "2026-09-25T20:34:19Z#1"])

    def test_a_line_that_looks_like_a_timestamp_but_does_not_parse_is_malformed(self):
        # Measured shape: the sample's line 277 drops the seconds ("...T03:55Z").
        path = self.write("2026-09-27T03:54:00Z first\n"
                          "2026-09-27T03:55Z → done: answers read\n"
                          "2026-09-27T03:56:00Z third\n")
        records = inbox.read_records(path)
        self.assertEqual([r.position for r in records],
                         ["2026-09-27T03:54:00Z#1", "2026-09-27T03:56:00Z#1"])
        self.assertEqual(inbox.malformed_lines(path), [2])
        # never glued onto the previous record
        self.assertEqual(records[0].text, "first")
        self.assertEqual(records[0].continuations, [])

    def test_an_inbox_with_no_timestamped_record_is_an_error_not_an_empty_read(self):
        path = self.write("# inbox L1-routing\njust prose, no timestamps at all\n")
        with self.assertRaises(inbox.NoTimestampedRecords) as caught:
            inbox.read_records(path)
        self.assertIn("no timestamped records in %s" % path, str(caught.exception))

    def test_the_run_dir_comes_from_the_environment_only(self):
        run = os.path.join(self.tmp, "run")
        os.makedirs(os.path.join(run, "inbox"))
        with mock.patch.dict(os.environ, {"AUTOOS_RUN_DIR": run}):
            self.assertEqual(inbox.inbox_path("L1-routing"),
                             os.path.join(run, "inbox", "L1-routing.md"))
        with mock.patch.dict(os.environ, clear=True):
            os.environ.pop("AUTOOS_RUN_DIR", None)
            with self.assertRaises(inbox.InboxError) as caught:
                inbox.inbox_path("L1-routing")
        self.assertIn("AUTOOS_RUN_DIR", str(caught.exception))


class PositionTests(_Files):
    def test_a_bare_timestamp_reads_the_whole_second_it_names(self):
        # At-least-once: a bare UTC stamp has no ordinal, so it cuts *before*
        # ordinal 1 and every record of that second is returned.
        path = self.write(COLLIDING)
        got = inbox.read_since(path, inbox.parse_position("2026-09-25T19:21:08"))
        self.assertEqual([r.position for r in got],
                         ["2026-09-25T19:21:08Z#1", "2026-09-25T19:21:08Z#2",
                          "2026-09-25T19:21:08Z#3", "2026-09-25T19:21:08Z#4"])

    def test_reading_is_at_least_once_a_position_resumes_after_that_ordinal(self):
        path = self.write(COLLIDING)
        got = inbox.read_since(path, inbox.parse_position("2026-09-25T19:21:08Z#2"))
        self.assertEqual([r.position for r in got],
                         ["2026-09-25T19:21:08Z#3", "2026-09-25T19:21:08Z#4"])

    def test_a_later_position_never_returns_an_earlier_ordinal_of_the_same_second(self):
        path = self.write(COLLIDING)
        got = inbox.read_since(path, inbox.parse_position("2026-09-25T19:21:08Z#4"))
        self.assertEqual(got, [])

    def test_a_record_whose_timestamp_is_lower_than_one_before_it_is_late(self):
        # Measured shape: the sample has three such records (file order is truth).
        path = self.write("2026-09-26T07:20:00Z ahead\n"
                          "2026-09-26T07:10:24Z from previous-L1: late order\n"
                          "2026-09-26T07:30:00Z after\n")
        got = inbox.read_since(path, inbox.parse_position("2026-09-26T07:00:00"))
        self.assertEqual([r.position for r in got],
                         ["2026-09-26T07:20:00Z#1", "2026-09-26T07:10:24Z#1",
                          "2026-09-26T07:30:00Z#1"])
        self.assertFalse(got[0].late)
        self.assertTrue(got[1].late)
        self.assertFalse(got[2].late)

    def test_a_late_record_after_the_position_is_still_returned(self):
        # The measured shape: an older timestamp appended after a newer one.
        # Resuming at the newer position must not lose it (§0, file order).
        path = self.write("2026-09-26T07:20:00Z ahead\n"
                          "2026-09-26T07:10:24Z late one\n")
        got = inbox.read_since(path, inbox.parse_position("2026-09-26T07:20:00Z#1"))
        self.assertEqual([r.position for r in got], ["2026-09-26T07:10:24Z#1"])
        self.assertTrue(got[0].late)

    def test_a_position_that_is_not_in_the_file_cuts_by_timestamp(self):
        path = self.write("2026-09-26T07:20:00Z ahead\n"
                          "2026-09-26T07:10:24Z late one\n")
        got = inbox.read_since(path, inbox.parse_position("2026-09-26T07:15:00Z"))
        self.assertEqual([r.position for r in got], ["2026-09-26T07:20:00Z#1"])
        self.assertFalse(got[0].late)

    def test_a_position_that_does_not_parse_is_a_value_error(self):
        for bad in ("", "not-a-position", "2026-09-26T07:15:00Z#0",
                    "2026-09-26T07:15:00Z#x", "2026-09-26 07:15:00"):
            with self.assertRaises(ValueError, msg=bad):
                inbox.parse_position(bad)

    def test_latest_position_names_the_last_record_of_the_file(self):
        path = self.write(COLLIDING)
        self.assertEqual(inbox.latest_position(path), "2026-09-25T19:21:08Z#4")


class MalformedEntryTests(_Files):
    """R1FIX: a malformed line is text a pack must keep, not only a stderr hint.

    The measured shape is the sample's line 277, a minute-precision stamp:
    `2026-09-27T03:55Z → done: answers read (...)`. It is an acknowledgement,
    so keeping it off stdout loses a real message from the pack that embeds
    stdout verbatim — the defect this closes.
    """

    SAMPLE = ("2026-09-27T03:53:13Z E3 RTK A/B plan\n"
              "2026-09-27T03:55Z → done: answers read (BYOK opt2, LEAK opt1);\n"
              "  waiting leak-exempt PASS\n"
              "2026-09-27T04:03:09Z lesson: host omniroute CLI calls 401\n")

    def test_a_malformed_entry_keeps_its_own_text_and_the_lines_under_it(self):
        path = self.write(self.SAMPLE)
        records, malformed = inbox.parse_file(path)
        self.assertEqual([m.line for m in malformed], [2])
        self.assertEqual(
            malformed[0].text,
            "2026-09-27T03:55Z → done: answers read (BYOK opt2, LEAK opt1);")
        self.assertEqual(malformed[0].continuations, ["  waiting leak-exempt PASS"])
        self.assertEqual(inbox.malformed_lines(path), [2])
        # the record it follows keeps exactly its own text and nothing more
        self.assertEqual([r.text for r in records],
                         ["E3 RTK A/B plan", "lesson: host omniroute CLI calls 401"])
        self.assertEqual([r.continuations for r in records], [[], []])

    def test_a_malformed_entry_is_read_with_the_record_that_precedes_it(self):
        # Its place in the file is "right after the record before it", so a
        # reader resuming AT that record has not seen it yet: it must be read.
        path = self.write(self.SAMPLE)
        records, malformed = inbox.parse_file(path)
        got = inbox.window_entries(records, malformed,
                                  inbox.parse_position("2026-09-27T03:53:13Z#1"))
        self.assertEqual([type(e).__name__ for e in got], ["Malformed", "Record"])
        self.assertEqual(got[0].line, 2)

    def test_a_malformed_entry_behind_the_window_is_not_read(self):
        path = self.write(self.SAMPLE)
        records, malformed = inbox.parse_file(path)
        got = inbox.window_entries(records, malformed,
                                  inbox.parse_position("2026-09-27T04:03:09Z#1"))
        self.assertEqual(got, [])

    def test_a_malformed_line_before_the_first_record_rides_at_the_head(self):
        path = self.write("2026-09-27T03:55Z → done\n"
                          "2026-09-27T04:03:09Z first real record\n"
                          "2026-09-27T04:04:09Z second real record\n")
        records, malformed = inbox.parse_file(path)
        got = inbox.window_entries(records, malformed)
        self.assertEqual([type(e).__name__ for e in got],
                         ["Malformed", "Record", "Record"])
        cut = inbox.window_entries(records, malformed,
                                  inbox.parse_position("2026-09-27T04:03:09Z#1"))
        self.assertEqual([type(e).__name__ for e in cut], ["Record"])


class CardPositionTests(_Files):
    HEADER = ("# card L1-routing — 2026-09-28T07:00:00Z | gen=0123456789abcdef "
              "| context 210k/350k | last-event 2026-09-28T06:12:00Z#2\n")

    def test_the_card_header_last_event_is_the_position(self):
        path = self.write(self.HEADER + "goal: run the lanes\n", name="card.md")
        self.assertEqual(inbox.card_last_event(path), "2026-09-28T06:12:00Z#2")

    def test_a_card_without_a_last_event_has_no_position(self):
        path = self.write("# card L1-routing — 2026-09-28T07:00:00Z | gen=x\n"
                          "goal: run the lanes\n", name="card.md")
        self.assertIsNone(inbox.card_last_event(path))


# ── §2: the CLI verb ────────────────────────────────────────────────────────

class InboxCliTests(_Files):
    def invoke(self, *args, env=None):
        """Call main() in-process with a controlled AUTOOS_RUN_DIR.

        The env is os.environ's, patched and restored: the reader looks the RUN
        dir up at call time, so a dict handed to a subprocess would not apply.
        """
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ):
            os.environ.pop("AUTOOS_RUN_DIR", None)
            os.environ.update(env or {})
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = AGENT_MOD.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_no_since_and_no_all_is_refused(self):
        path = self.write(COLLIDING)
        rc, _out, err = self.invoke("inbox", "--file", path)
        self.assertEqual(rc, 2)
        self.assertIn("--since", err)
        self.assertEqual(_out, "")

    def test_since_reads_records_whole_with_their_continuations(self):
        path = self.write(
            "2026-09-28T05:00:00Z before the window\n"
            "2026-09-28T06:10:00Z TASK:\n"
            "  (a) the first step\n"
            "  (b) the second step\n"
            "2026-09-28T06:20:00Z after\n")
        rc, out, _err = self.invoke("inbox", "--file", path,
                                    "--since", "2026-09-28T06:00:00Z")
        self.assertEqual(rc, 0, _err)
        self.assertEqual(out.splitlines(), [
            "2026-09-28T06:10:00Z#1 TASK:",
            "  (a) the first step",
            "  (b) the second step",
            "2026-09-28T06:20:00Z#1 after"])

    def test_a_late_record_is_flagged(self):
        path = self.write("2026-09-28T07:20:00Z ahead\n"
                          "2026-09-28T07:10:00Z behind\n")
        rc, out, _err = self.invoke("inbox", "--file", path, "--all")
        self.assertEqual(rc, 0, _err)
        self.assertIn("2026-09-28T07:10:00Z#1 (late) behind", out.splitlines())

    def test_max_records_cut_the_oldest_and_says_how_many(self):
        path = self.write("".join(
            "2026-09-28T06:%02d:00Z record %d\n" % (i, i) for i in range(5)))
        rc, out, err = self.invoke("inbox", "--file", path, "--all",
                                   "--max-records", "2")
        self.assertEqual(rc, 0, err)
        self.assertEqual([l.split()[0] for l in out.splitlines()],
                         ["2026-09-28T06:03:00Z#1", "2026-09-28T06:04:00Z#1"])
        self.assertIn("cut 3 earlier records", err)

    def test_the_default_cap_is_thirty_records(self):
        path = self.write("".join(
            "2026-09-28T06:%02d:00Z record %d\n" % (i, i) for i in range(35)))
        rc, out, err = self.invoke("inbox", "--file", path, "--all")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(out.splitlines()), 30)
        self.assertIn("cut 5 earlier records", err)

    def test_a_name_resolves_under_the_run_dir_inbox(self):
        run = os.path.join(self.tmp, "run")
        os.makedirs(os.path.join(run, "inbox"))
        with open(os.path.join(run, "inbox", "L1-routing.md"), "w",
                  encoding="utf-8") as fh:
            fh.write(COLLIDING)
        rc, out, err = self.invoke("inbox", "L1-routing", "--all",
                                   env={"AUTOOS_RUN_DIR": run})
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(out.splitlines()), 4)

    def test_a_card_supplies_the_position(self):
        path = self.write(COLLIDING)
        card = self.write("# card L1-routing — 2026-09-28T07:00:00Z | gen=x "
                          "| context 1k/1k | last-event 2026-09-25T19:21:08Z#2\n",
                          name="card.md")
        rc, out, err = self.invoke("inbox", "--file", path, "--since-card", card)
        self.assertEqual(rc, 0, err)
        self.assertEqual([l.split()[0] for l in out.splitlines()],
                         ["2026-09-25T19:21:08Z#3", "2026-09-25T19:21:08Z#4"])

    def test_a_card_without_a_last_event_reads_from_the_start(self):
        path = self.write(COLLIDING)
        card = self.write("# card L1-routing — 2026-09-28T07:00:00Z | gen=x\n",
                          name="card.md")
        rc, out, err = self.invoke("inbox", "--file", path, "--since-card", card)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(out.splitlines()), 4)
        self.assertIn("no last-event", err)

    def test_an_unreadable_card_is_exit_2(self):
        path = self.write(COLLIDING)
        rc, _out, err = self.invoke("inbox", "--file", path, "--since-card",
                                    os.path.join(self.tmp, "no-such-card.md"))
        self.assertEqual(rc, 2)
        self.assertIn("no-such-card.md", err)

    def test_an_unreadable_inbox_is_exit_2(self):
        rc, _out, err = self.invoke("inbox", "--file",
                                    os.path.join(self.tmp, "nope.md"), "--all")
        self.assertEqual(rc, 2)
        self.assertIn("nope.md", err)

    def test_an_inbox_without_timestamps_exits_1_and_names_the_file(self):
        path = self.write("# inbox L1-main\nprose only\n")
        rc, _out, err = self.invoke("inbox", "--file", path, "--all")
        self.assertEqual(rc, 1)
        self.assertIn("no timestamped records in %s" % path, err)

    def test_a_malformed_line_is_reported_and_still_reaches_stdout(self):
        # R1FIX flipped this expectation: it used to require stdout to carry the
        # record only, which is exactly the acknowledgement a pack lost.
        path = self.write("2026-09-27T03:54:00Z first\n"
                          "2026-09-27T03:55Z → done: answers read\n")
        rc, out, err = self.invoke("inbox", "--file", path, "--all")
        self.assertEqual(rc, 0, err)
        self.assertIn("malformed at line 2", err)
        self.assertEqual(out.splitlines(), [
            "2026-09-27T03:54:00Z#1 first",
            "(malformed line 2) 2026-09-27T03:55Z → done: answers read"])

    def test_a_malformed_line_inside_the_window_is_printed_in_file_order(self):
        # The sample's line 277 shape, read since a record that precedes it.
        path = self.write("2026-09-27T03:53:13Z E3 RTK A/B plan\n"
                          "2026-09-27T03:55Z → done: answers read (BYOK opt2);\n"
                          "  waiting leak-exempt PASS\n"
                          "2026-09-27T04:03:09Z lesson: host omniroute CLI 401\n")
        rc, out, err = self.invoke("inbox", "--file", path,
                                  "--since", "2026-09-27T03:53:13Z#1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.splitlines(), [
            "(malformed line 2) 2026-09-27T03:55Z → done: answers read (BYOK opt2);",
            "  waiting leak-exempt PASS",
            "2026-09-27T04:03:09Z#1 lesson: host omniroute CLI 401"])

    def test_a_malformed_line_outside_the_window_is_not_printed(self):
        path = self.write("2026-09-27T03:53:13Z E3 RTK A/B plan\n"
                          "2026-09-27T03:55Z → done: answers read (BYOK opt2);\n"
                          "2026-09-27T04:03:09Z lesson: host omniroute CLI 401\n")
        rc, out, err = self.invoke("inbox", "--file", path,
                                  "--since", "2026-09-27T04:03:09Z#1")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("done: answers read", out)
        self.assertEqual(out, "")

    def test_a_malformed_line_appended_after_the_resume_point_is_still_read(self):
        # The reason the fix exists: a card stopped at the last record, and the
        # writer appended an acknowledgement that will never parse. No record is
        # new, yet this text is — so stdout carries it while stderr still says
        # the record window is empty.
        path = self.write("2026-09-27T03:53:13Z E3 RTK A/B plan\n"
                          "2026-09-27T03:55Z → done: answers read (BYOK opt2);\n")
        rc, out, err = self.invoke("inbox", "--file", path,
                                  "--since", "2026-09-27T03:53:13Z#1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.splitlines(), [
            "(malformed line 2) 2026-09-27T03:55Z → done: answers read (BYOK opt2);"])
        self.assertIn("nothing since 2026-09-27T03:53:13Z#1", err)

    def test_the_notice_counts_the_lines_a_malformed_entry_absorbed(self):
        path = self.write("2026-09-27T03:54:00Z first\n"
                          "2026-09-27T03:55Z → done: answers read\n"
                          "  waiting leak-exempt PASS\n"
                          "  and a third line\n")
        rc, out, err = self.invoke("inbox", "--file", path, "--all")
        self.assertEqual(rc, 0, err)
        self.assertIn("malformed at line 2 (3 lines)", err)
        self.assertEqual(out.splitlines(), [
            "2026-09-27T03:54:00Z#1 first",
            "(malformed line 2) 2026-09-27T03:55Z → done: answers read",
            "  waiting leak-exempt PASS",
            "  and a third line"])

    def test_records_cut_from_the_window_take_their_malformed_line_with_them(self):
        # --max-records budgets RECORDS; a malformed line is not one, but it
        # must not survive the record it rode on and print over a kept record.
        path = self.write("2026-09-27T03:50:00Z oldest\n"
                          "2026-09-27T03:55Z → done: an acknowledgement\n"
                          "2026-09-27T03:56:00Z second\n"
                          "2026-09-27T03:57:00Z third\n")
        rc, out, err = self.invoke("inbox", "--file", path, "--all",
                                   "--max-records", "2")
        self.assertEqual(rc, 0, err)
        self.assertIn("cut 1 earlier records", err)
        self.assertNotIn("an acknowledgement", out)
        self.assertEqual(out.splitlines(), [
            "2026-09-27T03:56:00Z#1 second",
            "2026-09-27T03:57:00Z#1 third"])

    def test_the_notice_speaks_only_for_a_malformed_line_inside_the_window(self):
        # R1FIX2: the notice claims "its text is on stdout", so it may only be
        # made for an entry the window actually reads.
        path = self.write("2026-09-27T03:53:13Z E3 RTK A/B plan\n"
                          "2026-09-27T03:55Z → done: answers read (BYOK opt2);\n"
                          "2026-09-27T04:03:09Z lesson: host omniroute CLI 401\n")
        rc, out, err = self.invoke("inbox", "--file", path,
                                  "--since", "2026-09-27T03:53:13Z#1")
        self.assertEqual(rc, 0, err)
        self.assertIn("malformed at line 2", err)
        self.assertIn("its text is on stdout tagged (malformed line 2)", err)
        self.assertIn(
            "(malformed line 2) 2026-09-27T03:55Z → done: answers read (BYOK opt2);",
            out.splitlines())

    def test_a_malformed_line_outside_the_window_gets_no_notice(self):
        path = self.write("2026-09-27T03:53:13Z E3 RTK A/B plan\n"
                          "2026-09-27T03:55Z → done: answers read (BYOK opt2);\n"
                          "2026-09-27T04:03:09Z lesson: host omniroute CLI 401\n")
        rc, out, err = self.invoke("inbox", "--file", path,
                                  "--since", "2026-09-27T04:03:09Z#1")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("malformed at line 2", err)
        self.assertEqual(out, "")

    def test_a_malformed_line_cut_by_max_records_is_counted_in_the_cut_line(self):
        # The sample's shape: --max-records drops the record a malformed line
        # rode on, yet stderr still promised its text was on stdout — text that
        # never reached it.
        path = self.write("2026-09-27T03:50:00Z oldest\n"
                          "2026-09-27T03:55Z → done: an acknowledgement\n"
                          "2026-09-27T03:56:00Z second\n"
                          "2026-09-27T03:57:00Z third\n")
        rc, out, err = self.invoke("inbox", "--file", path, "--all",
                                  "--max-records", "2")
        self.assertEqual(rc, 0, err)
        self.assertIn("cut 1 earlier records and 1 malformed entries", err)
        self.assertNotIn("malformed at line 2", err)
        self.assertNotIn("on stdout", err)
        self.assertEqual(out.splitlines(), [
            "2026-09-27T03:56:00Z#1 second",
            "2026-09-27T03:57:00Z#1 third"])

    def test_a_name_and_a_file_together_is_refused(self):
        path = self.write(COLLIDING)
        rc, _out, err = self.invoke("inbox", "L1-routing", "--file", path, "--all")
        self.assertEqual(rc, 2)
        self.assertIn("one source", err)

    def test_since_and_all_together_is_refused(self):
        path = self.write(COLLIDING)
        rc, _out, err = self.invoke("inbox", "--file", path, "--all",
                                    "--since", "2026-09-25T19:21:08Z#1")
        self.assertEqual(rc, 2)
        self.assertIn("--all", err)

    def test_two_named_windows_are_refused(self):
        # --since-card is the successor's resume point; a run that also names
        # --since reads one of them by precedence nobody chose.
        path = self.write(COLLIDING)
        card = self.write("# card L1-routing — 2026-09-28T07:00:00Z | gen=x "
                          "| last-event 2026-09-25T19:21:08Z#2\n", name="card.md")
        rc, _out, err = self.invoke("inbox", "--file", path, "--since-card", card,
                                    "--since", "2026-09-28T06:00:00Z")
        self.assertEqual(rc, 2)
        self.assertIn("one window", err)

    def test_a_bad_since_value_is_exit_2(self):
        path = self.write(COLLIDING)
        rc, _out, err = self.invoke("inbox", "--file", path,
                                    "--since", "yesterday")
        self.assertEqual(rc, 2)
        self.assertIn("yesterday", err)

    def test_an_empty_window_names_the_position_the_caller_wrote(self):
        # A bare UTC cut reads its whole second internally (ordinal 0); "#0" is
        # not a position a card may hold, so the notice must not invent one.
        path = self.write("2026-09-25T19:21:08Z old record\n")
        rc, out, err = self.invoke("inbox", "--file", path,
                                   "--since", "2026-09-28T06:00:00Z")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "")
        self.assertIn("nothing since 2026-09-28T06:00:00Z", err)
        self.assertNotIn("#0", err)

    def test_inbox_is_a_real_verb_of_the_cli(self):
        path = self.write(COLLIDING)
        rc = subprocess.run([sys.executable, str(AGENT), "inbox", "--file", path, "--all"],
                            capture_output=True, text=True,
                            env={k: v for k, v in os.environ.items()
                                 if k != "AUTOOS_RUN_DIR"},
                            stdin=subprocess.DEVNULL)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertEqual(len(rc.stdout.splitlines()), 4)
        self.assertEqual(rc.stdout.splitlines()[-1].split()[0],
                         "2026-09-25T19:21:08Z#4")


# ── R1: the dispatch table ──────────────────────────────────────────────────

class DispatchTests(unittest.TestCase):
    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = AGENT_MOD.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_the_table_has_a_handler_for_every_registered_verb(self):
        registered = set(AGENT_MOD.VERB_PARSERS)
        self.assertTrue(registered)
        missing = sorted(registered - set(AGENT_MOD.VERB_HANDLERS))
        self.assertEqual(missing, ["usage"],
                         "every verb except the pre-argparse `usage` delegation "
                         "needs a handler")

    def test_a_registered_verb_without_a_handler_is_refused_not_run(self):
        AGENT_MOD.VERB_PARSERS["orphan"] = lambda sub: sub.add_parser("orphan")
        self.addCleanup(AGENT_MOD.VERB_PARSERS.pop, "orphan", None)
        with mock.patch.object(AGENT_MOD, "cmd_run",
                               side_effect=AssertionError("run was reached")):
            rc, _out, err = self.invoke("orphan")
        self.assertEqual(rc, 2)
        self.assertIn("orphan", err)
        self.assertNotIn("tier", _out)

    def test_an_unregistered_verb_still_fails_in_argparse(self):
        with self.assertRaises(SystemExit) as caught:
            self.invoke("nonsense-verb")
        self.assertEqual(caught.exception.code, 2)

    def test_list_still_dispatches_through_the_table(self):
        rc = subprocess.run([sys.executable, str(AGENT), "list"],
                            capture_output=True, text=True,
                            env={k: v for k, v in os.environ.items()
                                 if k != "AUTOOS_RUN_DIR"},
                            stdin=subprocess.DEVNULL)
        self.assertEqual(rc.returncode, 0, rc.stderr)


if __name__ == "__main__":
    unittest.main()
