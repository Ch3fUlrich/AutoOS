#!/usr/bin/env python3
"""Tests for tools/autoos_heartbeat.py, the `heartbeat` subcommand of
tools/autoos-agent.py, the MCP `heartbeat` tool, and the AUTOOS_AGENT_INBOX
launch refusal (R-heartbeat-02/03, R-pause-01, R-handoff-07 migrated into
code: briefs/common.md "Skill rules bind every spawned agent", operator
2026-09-26T13:43:33Z).

heartbeat is read-only end to end: it never pushes, commits or writes to a
repo (AGENTS.md rule 3 - a check that never writes is trivially safe to run
twice). Pure helpers (branch_prefix, pause_state) are unit tested directly;
repo_branch_state is exercised against real temporary git repositories (a
bare "origin" plus a working clone), the way test_autoos_spawner.py's
KeyFileTests builds a temp repo (inline `-c user.name=`/`-c user.email=`, no
machine config touched). The CLI and MCP surface are exercised through
subprocess/plain-function calls, the same way test_autoos_context.py and
test_autoos_spawner.py's McpRouteTests do.

Run directly, never through unittest discover:

    python3 tests/test_autoos_heartbeat.py
"""
import io
import json
import os
import re
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

import autoos_heartbeat as hb  # noqa: E402
import autoos_agent_mcp as mcp_server  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
       "-c", "init.defaultBranch=main"]


def git(*args, cwd, check=True):
    return subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True, check=check)


def write_inbox(path, *lines):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(line + "\n" for line in lines))


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTOOS_AGENT_")}
    env.update(extra)
    return env


class BranchPrefixTests(unittest.TestCase):
    """branch_prefix: groups sibling "owned" branches (R-heartbeat-02)."""

    def test_worktree_agent_style_groups_on_the_last_hyphen(self):
        self.assertEqual(hb.branch_prefix("worktree-agent-a957350031c1fae19"), "worktree-agent-")

    def test_namespaced_branch_groups_on_the_slash(self):
        self.assertEqual(hb.branch_prefix("L1-backlog/agy-tarball"), "L1-backlog/")

    def test_no_separator_is_the_whole_name(self):
        self.assertEqual(hb.branch_prefix("main"), "main")


class PauseStateTests(unittest.TestCase):
    """pause_state: the newest PAUSE wins unless a newer RESUME follows it
    (R-pause-01: "a hard stop, checked every heartbeat and before every launch")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox = os.path.join(self._tmp.name, "inbox.md")

    # --- a relaunch is the resume: a PAUSE from before the session started
    # is history (2026-09-26 reboot: the 12:15Z PAUSE had no RESUME line and
    # the relaunched session read it as active).

    def test_pause_before_since_is_not_active(self):
        write_inbox(self.inbox, "2026-09-26T12:15:41Z from L0 PAUSE NOW")
        since = hb._parse_iso("2026-09-26T13:35:00Z")
        self.assertFalse(hb.pause_state(self.inbox, since=since)["active"])

    def test_pause_after_since_is_active(self):
        write_inbox(self.inbox, "2026-09-26T14:00:00Z from L0 PAUSE NOW")
        since = hb._parse_iso("2026-09-26T13:35:00Z")
        self.assertTrue(hb.pause_state(self.inbox, since=since)["active"])

    def test_lesson_and_done_lines_that_quote_pause_are_not_a_pause(self):
        write_inbox(self.inbox,
                    "2026-09-26T14:00:00Z lesson: two PAUSE lines were ignored",
                    "2026-09-26T14:01:00Z → done: PAUSE handled")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    # RESTART spec §0: the acknowledgement markers are one list, hb.ACK_MARKERS
    # (the old `_NOT_AN_ORDER_RE` held only `lesson:` and `→ done`, so every
    # other acknowledgement quoting a PAUSE read as a fresh order). One test per
    # marker in use.

    def test_marker_done_is_not_a_pause_order(self):
        write_inbox(self.inbox, "2026-09-26T14:01:00Z L1: → done: PAUSE handled")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_marker_ack_is_not_a_pause_order(self):
        write_inbox(self.inbox, "2026-09-26T14:01:00Z L1: → ack: PAUSE seen, stopping")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_marker_relaunched_is_not_a_pause_order(self):
        write_inbox(self.inbox,
                    "2026-09-26T14:01:00Z L1: → relaunched: the session the PAUSE stopped")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_marker_operator_is_not_a_pause_order(self):
        write_inbox(self.inbox,
                    "2026-09-26T14:01:00Z L1: → operator: PAUSE needs your call")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_marker_main_is_not_a_pause_order(self):
        write_inbox(self.inbox,
                    "2026-09-26T14:01:00Z L1: → main: merged before the PAUSE landed")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_a_plain_pause_with_no_marker_is_still_an_order(self):
        write_inbox(self.inbox, "2026-09-26T14:01:00Z operator: PAUSE NOW")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    # R2a2 (the Sonnet review of R2a, MEDIUM): an acknowledgement marker matched
    # *anywhere* in the line, so a real order that happened to name one was
    # swallowed. Reproduced against the shipped code:
    #
    #     pause_state(<"… operator: PAUSE all lanes; nothing merges → main
    #     until I say so">)["active"] == False
    #
    # A marker counts only at the head of the record body — the text after the
    # leading ISO timestamp and, at most, after one speaker prefix.

    def test_an_order_that_names_a_marker_mid_line_is_still_an_order(self):
        write_inbox(self.inbox,
                    "2026-09-28T10:00:00Z operator: PAUSE all lanes; nothing merges "
                    "→ main until I say so")
        state = hb.pause_state(self.inbox)
        self.assertTrue(state["active"], state)

    def test_a_marker_at_the_head_is_not_an_order(self):
        write_inbox(self.inbox, "2026-09-28T10:00:00Z → main: PAUSE acknowledged")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_a_marker_at_the_head_after_a_from_prefix_is_not_an_order(self):
        write_inbox(self.inbox, "2026-09-28T10:00:00Z from L1-main: → done: PAUSE lifted")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_a_lesson_headed_line_that_quotes_a_pause_is_not_an_order(self):
        write_inbox(self.inbox,
                    "2026-09-28T10:00:00Z lesson: PAUSE was read as an order because "
                    "→ done appeared mid-line")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    # One test per marker, head versus mid-sentence: the marker decides the line
    # only where the inbox writers actually put it — at the head, after the
    # timestamp and after at most one speaker prefix.

    def test_every_marker_at_the_head_is_not_an_order(self):
        for marker in hb.ACK_MARKERS:
            head = marker if marker.endswith(":") else marker + ":"
            for prefix in ("", "L1: ", "from L1-main: "):
                write_inbox(self.inbox,
                            "2026-09-28T10:00:00Z %s%s PAUSE acknowledged" % (prefix, head))
                state = hb.pause_state(self.inbox)
                self.assertFalse(state["active"], "%s%s: %s" % (prefix, head, state))

    def test_every_marker_mid_sentence_keeps_the_order(self):
        for marker in hb.ACK_MARKERS:
            for prefix in ("", "operator: ", "from L0 (operator) "):
                write_inbox(self.inbox,
                            "2026-09-28T10:00:00Z %sPAUSE all lanes, %s when done"
                            % (prefix, marker))
                state = hb.pause_state(self.inbox)
                self.assertTrue(state["active"], "%s…%s: %s" % (prefix, marker, state))

    # The speaker-prefix shapes are derived read-only from the real inboxes
    # (logs/handoff-sessions/20260925/inbox/*.md: `→ done:` 579x, `from L1-main`
    # 254x with no colon, `from L1-main:` 79x, `from L0 (operator) PAUSE NOW` at
    # L1-routing.md:126). Fixtures below are written by hand in those shapes.

    def test_a_pause_after_a_bare_speaker_prefix_is_an_order(self):
        write_inbox(self.inbox, "2026-09-28T10:00:00Z operator: PAUSE all lanes now")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_a_pause_after_a_from_note_prefix_is_an_order(self):
        write_inbox(self.inbox,
                    "2026-09-28T10:00:00Z from L0 (operator) PAUSE NOW (repeat of 11:19:41Z)")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_a_pause_with_no_prefix_at_all_is_an_order(self):
        write_inbox(self.inbox,
                    "2026-09-28T10:00:00Z PAUSE (operator, via L0 router): the host reboots "
                    "soon (RAM upgrade)")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_a_marker_counts_only_at_the_head_of_the_record_body(self):
        # The filter fires on a marker at the head only, never mid-sentence.
        self.assertTrue(hb._acknowledgement("lesson: PAUSE quoted"))
        self.assertTrue(hb._acknowledgement("L1: lesson: PAUSE quoted"))
        self.assertFalse(hb._acknowledgement("PAUSE, lesson: quoted"))

    def test_ack_markers_are_the_one_list_the_pause_filter_uses(self):
        # §0: one home. The head anchor is built from ACK_MARKERS, not restated.
        self.assertIn(
            "|".join(re.escape(m) for m in hb.ACK_MARKERS),
            hb._MARKER_AT_HEAD_RE.pattern)
        for marker in hb.ACK_MARKERS:
            head = marker if marker.endswith(":") else marker + ":"
            self.assertTrue(hb._acknowledgement("%s PAUSE acknowledged" % head), marker)
            self.assertTrue(hb._acknowledgement("from L1-main: %s PAUSE acknowledged"
                                                 % head), marker)

    # R2a3 (the Muse review of R2a2) — three ways the head rule still mis-read
    # a line, each reproduced against the shipped code before it was fixed:
    # a marker matched as a bare prefix, a two-word speaker prefix not stripped,
    # and a body head that was never normalised.

    def test_a_marker_that_is_the_prefix_of_a_longer_word_is_an_order(self):
        for text in ("→ mainline PAUSE all lanes",
                     "→ maintenance: PAUSE every lane",
                     "→ operators PAUSE the pack lane",
                     "→ doneX PAUSE now",
                     "→ acknowledged PAUSE now",
                     "→ relaunching PAUSE now"):
            write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
            state = hb.pause_state(self.inbox)
            self.assertTrue(state["active"], "%s: %s" % (text, state))

    def test_a_head_marker_needs_a_colon_space_or_the_end_of_the_text(self):
        # §0's boundary rule: the marker is a whole word, not a prefix.
        self.assertTrue(hb._acknowledgement("→ main: merged before the PAUSE landed"))
        self.assertTrue(hb._acknowledgement("→ done 12:00 PAUSE lifted"))
        self.assertTrue(hb._acknowledgement("→ ack PAUSE lifted"))
        self.assertTrue(hb._acknowledgement("lesson: the PAUSE was late"))
        self.assertTrue(hb._acknowledgement("→ done"))
        self.assertFalse(hb._acknowledgement("→ mainline PAUSE all lanes"))
        self.assertFalse(hb._acknowledgement("→ doneX PAUSE now"))

    def test_a_quoted_pause_after_a_two_word_speaker_is_not_an_order(self):
        for text in ("operator on duty: → done: PAUSE lifted",
                     "from L1-main relay (x): → done: PAUSE lifted",
                     "from L0 (operator): → done 12:00 PAUSE lifted"):
            write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
            state = hb.pause_state(self.inbox)
            self.assertFalse(state["active"], "%s: %s" % (text, state))

    def test_a_pause_after_a_two_word_speaker_is_still_an_order(self):
        for text in ("from L0 (operator): PAUSE NOW",
                     "operator on duty: PAUSE every lane",
                     "from L1-main relay (x): PAUSE the pack build"):
            write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
            state = hb.pause_state(self.inbox)
            self.assertTrue(state["active"], "%s: %s" % (text, state))

    def test_a_bare_first_word_without_a_colon_is_never_a_speaker(self):
        self.assertFalse(hb._acknowledgement("notes → done: PAUSE lifted"))
        write_inbox(self.inbox, "2026-09-28T10:00:00Z notes → done: PAUSE lifted")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_a_from_prefix_is_bounded_by_its_colon_note_or_one_word(self):
        # A clause after `from <name>` is the body, never more speaker words, so
        # a marker that only appears mid-sentence keeps the line an order.
        write_inbox(self.inbox,
                    "2026-09-28T10:00:00Z from L1-main PAUSE all lanes, → main when done")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_the_body_head_is_normalised_before_the_marker_check(self):
        # Leading spaces, a BOM or a CR remnant made an ack read as an order.
        for text in ("  → done: PAUSE lifted",
                     "\ufeff→ done: PAUSE lifted",
                     "\r→ done: PAUSE lifted",
                     "\ufeff  → main: PAUSE lifted",
                     "\r\n\t→ ack: PAUSE lifted"):
            self.assertTrue(hb._acknowledgement(text), repr(text))

    def test_a_bom_does_not_swallow_the_record_whole(self):
        # Stripping the head must not lose an *order* either.
        with io.open(self.inbox, "w", encoding="utf-8") as fh:
            fh.write("\ufeff2026-09-28T10:00:00Z operator: PAUSE NOW\n")
        state = hb.pause_state(self.inbox)
        self.assertTrue(state["active"], state)
        self.assertEqual(state["at"], "2026-09-28T10:00:00Z")
    def test_a_crlf_inbox_line_is_still_an_ack(self):
        with io.open(self.inbox, "w", encoding="utf-8", newline="") as fh:
            fh.write("2026-09-28T10:00:00Z → done: PAUSE lifted\r\n"
                     "2026-09-28T10:01:00Z → main: merged, PAUSE lifted\r\n")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    # R2a4 (the Muse review of R2a3, HIGH, safety): `(?:WORD\s+){0,2}WORD(\(note\))?\s*:`
    # absorbs *any* short clause that ends in a colon, so an order whose first clause
    # *is* the order became that order's own speaker prefix and its marker-headed
    # remainder read as an acknowledgement — `PAUSE all lanes: → main is held` held
    # nothing. A speaker prefix may therefore never name an order word (one list,
    # `ORDER_WORDS`, beside `ACK_MARKERS`); the line is then not stripped and is an
    # order. A lost order is the one unacceptable outcome, a spurious one is the safe
    # direction. A speaker word must also look like a name, and the whole prefix is
    # bounded, so prose that merely ends in a colon cannot pose as a speaker either.

    def test_a_pause_clause_that_ends_in_a_colon_is_still_an_order(self):
        for text in ("PAUSE all lanes: → main is held",
                     "PAUSE lanes: → main is held until I say so",
                     "PAUSE: → main is held"):
            self.assertFalse(hb._acknowledgement(text), text)
            write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
            state = hb.pause_state(self.inbox)
            self.assertTrue(state["active"], "%s: %s" % (text, state))

    def test_a_speaker_prefix_never_names_an_order_word(self):
        for word in hb.ORDER_WORDS:
            for clause in ("%s the lanes:" % word,
                           "%s every lane right now:" % word,
                           "from L0 %s:" % word):
                text = "%s → main: merged while the PAUSE holds" % clause
                self.assertFalse(hb._acknowledgement(text), text)
                write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
                state = hb.pause_state(self.inbox)
                self.assertTrue(state["active"], "%s: %s" % (text, state))

    def test_an_order_word_is_rejected_in_any_case(self):
        # ORDER_WORDS match case-insensitively: a lower-case clause is the same risk.
        for clause in ("pause the pack lanes:", "Stop every lane now:", "freeze:"):
            text = "%s → main: merged while the PAUSE holds" % clause
            self.assertFalse(hb._acknowledgement(text), text)
            write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
            self.assertTrue(hb.pause_state(self.inbox)["active"], text)

    def test_an_order_word_inside_the_note_rejects_the_prefix_too(self):
        # The guard reads the whole prefix, its `(<note>)` included — a note that
        # names an order word is the order talking, not a speaker parenthesising.
        text = "from L0 (after the PAUSE): → main: merged, hold everything"
        self.assertFalse(hb._acknowledgement(text), text)

    def test_a_named_speaker_with_a_bounded_prefix_is_still_an_ack(self):
        # The other side of the same rule: a real speaker names no order word, so
        # these acknowledgements of a finished PAUSE must keep reading as acks.
        for text in ("L1-main: → done: PAUSE lifted",
                     "operator on duty: → done 12:00 PAUSE lifted"):
            self.assertTrue(hb._acknowledgement(text), text)
            write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
            self.assertFalse(hb.pause_state(self.inbox)["active"], text)

    def test_a_speaker_clause_that_reads_like_an_order_is_a_spurious_order(self):
        # `hold on:` names HOLD, so the prefix is not trusted and the line is
        # classified as an order — deliberate, and the accepted cost of the fix:
        # ORDER_WORDS is wide on purpose because a *spurious* order only holds a
        # lane (the operator RESUMEs, one heartbeat is wasted), while a *lost*
        # PAUSE lets workers run on against a stop that was really given.
        self.assertFalse(hb._acknowledgement("hold on: → main merged"))
        write_inbox(self.inbox,
                    "2026-09-28T10:00:00Z hold on: → main merged, PAUSE lifted")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_a_speaker_word_must_look_like_a_name(self):
        # letters, digits, - _ . — and not a bare count.
        self.assertTrue(hb._acknowledgement(
            "L1-routing.coordinator_x: → done: PAUSE lifted"))
        for text in ("4 lanes: → main merged, PAUSE still on",
                     "2026: → main merged, PAUSE still on",
                     "state=held: → main merged, PAUSE still on",
                     "L1/routing: → main merged, PAUSE still on",
                     "[operator]: → main merged, PAUSE still on"):
            self.assertFalse(hb._acknowledgement(text), text)
            write_inbox(self.inbox, "2026-09-28T10:00:00Z %s" % text)
            self.assertTrue(hb.pause_state(self.inbox)["active"], text)

    def test_a_speaker_prefix_is_bounded(self):
        # The cap is what a *prefix* is; a long clause before a colon is prose.
        long_clause = "from the coordinator who owns every lane in this batch:"
        self.assertGreater(len(long_clause), hb._SPEAKER_PREFIX_MAX)
        text = "%s → main: merged while the PAUSE holds" % long_clause
        self.assertFalse(hb._acknowledgement(text), text)
        # the real corpus's longest live speaker prefix stays well inside the cap
        self.assertTrue(hb._acknowledgement(
            "from L1-backlog (relaunch #3): → done: PAUSE lifted"))

    def test_order_words_are_the_one_list_the_speaker_guard_uses(self):
        # §0: one home, the same discipline as ACK_MARKERS — the guard is built from
        # the list, never restated, and it covers the two words the filter keys on.
        self.assertIn("|".join(hb.ORDER_WORDS), hb._ORDER_WORD_RE.pattern)
        for word in ("PAUSE", "RESUME"):
            self.assertIn(word, hb.ORDER_WORDS)
        for word in hb.ORDER_WORDS:
            self.assertIsNotNone(hb._ORDER_WORD_RE.search(word), word)
            self.assertIsNotNone(hb._ORDER_WORD_RE.search(word.lower()), word)
            self.assertIsNone(hb._ORDER_WORD_RE.search(word + "D"), word)
    def test_a_body_with_no_timestamp_is_not_a_record(self):
        # §0: a record opens with its ISO timestamp; a bare line (a
        # continuation) is never scanned for an order, marker or no marker.
        self.assertIsNone(hb.parse_inbox_line("PAUSE NOW"))
        self.assertIsNone(hb.parse_inbox_line("→ done: PAUSE lifted"))
        self.assertIsNone(hb.parse_inbox_line("2026-09-28T10:00:00Z"))
        write_inbox(self.inbox, "PAUSE NOW", "→ done: PAUSE lifted")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_session_start_is_the_first_transcript_timestamp(self):
        path = os.path.join(self._tmp.name, "t.jsonl")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write('{"type":"summary"}\n')
            fh.write('{"timestamp":"2026-09-26T13:35:02.123Z","type":"user"}\n')
            fh.write('{"timestamp":"2026-09-26T14:00:00.000Z","type":"user"}\n')
        self.assertEqual(hb.session_start(path), hb._parse_iso("2026-09-26T13:35:02Z"))
        self.assertIsNone(hb.session_start(os.path.join(self._tmp.name, "nope")))

    def test_missing_inbox_is_not_active(self):
        self.assertEqual(hb.pause_state(os.path.join(self._tmp.name, "nope.md")),
                         {"active": False, "at": None, "text": None})

    def test_no_inbox_path_is_not_active(self):
        self.assertEqual(hb.pause_state(None), {"active": False, "at": None, "text": None})

    def test_a_pause_line_with_no_resume_is_active(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW - stop everything")
        state = hb.pause_state(self.inbox)
        self.assertTrue(state["active"])
        self.assertEqual(state["at"], "2026-09-26T11:19:41Z")
        self.assertEqual(state["text"], "operator: PAUSE NOW - stop everything")

    def test_a_later_resume_clears_it(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T12:00:00Z operator: RESUME")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_an_earlier_resume_does_not_clear_a_later_pause(self):
        write_inbox(self.inbox,
                    "2026-09-26T09:00:00Z operator: RESUME",
                    "2026-09-26T11:19:41Z operator: PAUSE NOW")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_the_newest_of_several_pause_lines_is_reported(self):
        write_inbox(self.inbox,
                    "2026-09-26T08:00:00Z operator: PAUSE (operator gone for coffee)",
                    "2026-09-26T13:43:00Z operator: PAUSE (operator gone home)")
        state = hb.pause_state(self.inbox)
        self.assertTrue(state["active"])
        self.assertEqual(state["at"], "2026-09-26T13:43:00Z")
        self.assertIn("gone home", state["text"])

    def test_out_of_order_lines_are_still_read_by_timestamp(self):
        write_inbox(self.inbox,
                    "2026-09-26T13:43:00Z operator: PAUSE (later, written first)",
                    "2026-09-26T08:00:00Z operator: PAUSE (earlier, written second)")
        state = hb.pause_state(self.inbox)
        self.assertIn("written first", state["text"])

    def test_lowercase_pause_word_does_not_count(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: please pause soon")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_pause_as_part_of_another_word_does_not_count(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSED earlier, ignore")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_a_reply_line_with_done_marker_is_still_scanned_for_the_words(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T11:20:00Z L1: → done: paused every lane")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_a_done_reply_carrying_resume_still_clears_it(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T12:00:00Z L1: → done: RESUME acknowledged")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_first_80_chars_of_the_text_are_kept(self):
        long_text = "PAUSE " + ("x" * 200)
        write_inbox(self.inbox, "2026-09-26T11:19:41Z " + long_text)
        state = hb.pause_state(self.inbox)
        self.assertEqual(len(state["text"]), 80)
        self.assertEqual(state["text"], long_text[:80])

    def test_a_line_with_no_parseable_timestamp_is_skipped(self):
        write_inbox(self.inbox, "not-a-timestamp PAUSE NOW")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_blank_lines_are_skipped(self):
        write_inbox(self.inbox, "", "   ", "2026-09-26T11:19:41Z operator: PAUSE NOW")
        self.assertTrue(hb.pause_state(self.inbox)["active"])


class RepoBranchStateTests(unittest.TestCase):
    """repo_branch_state against real git repos: a bare origin plus a clone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.origin = os.path.join(self._tmp.name, "origin.git")
        self.work = os.path.join(self._tmp.name, "work")
        git("init", "-q", "--bare", self.origin, cwd=self._tmp.name)
        git("init", "-q", self.work, cwd=self._tmp.name)
        git("commit", "-q", "--allow-empty", "-m", "initial", cwd=self.work)
        git("remote", "add", "origin", self.origin, cwd=self.work)
        git("push", "-q", "-u", "origin", "main", cwd=self.work)

    def test_clean_repo_reports_nothing(self):
        self.assertEqual(hb.repo_branch_state(self.work), {"unpushed": [], "dirty": 0})

    def test_an_ahead_upstream_branch_is_reported(self):
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)
        state = hb.repo_branch_state(self.work)
        self.assertEqual(state["unpushed"], [("main", 1)])

    def test_a_sibling_branch_sharing_the_current_prefix_with_no_upstream_is_reported(self):
        git("checkout", "-q", "-b", "worktree-agent-current", cwd=self.work)
        git("checkout", "-q", "-b", "worktree-agent-sibling", cwd=self.work)
        git("commit", "-q", "--allow-empty", "-m", "sibling work", cwd=self.work)
        git("checkout", "-q", "worktree-agent-current", cwd=self.work)
        state = hb.repo_branch_state(self.work)
        self.assertIn(("worktree-agent-sibling", 1), state["unpushed"])
        self.assertNotIn("main", [b for b, _ in state["unpushed"]])

    def test_a_branch_with_no_upstream_and_no_matching_prefix_is_not_reported(self):
        git("checkout", "-q", "-b", "worktree-agent-current2", cwd=self.work)
        git("checkout", "-q", "-b", "unrelated-branch", cwd=self.work)
        git("commit", "-q", "--allow-empty", "-m", "unrelated work", cwd=self.work)
        git("checkout", "-q", "worktree-agent-current2", cwd=self.work)
        state = hb.repo_branch_state(self.work)
        self.assertEqual(state["unpushed"], [])

    def test_dirty_working_tree_is_counted(self):
        with io.open(os.path.join(self.work, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        state = hb.repo_branch_state(self.work)
        self.assertEqual(state["dirty"], 1)

    def test_not_a_git_repo_reports_zero_of_both(self):
        empty = os.path.join(self._tmp.name, "not-a-repo")
        os.makedirs(empty)
        self.assertEqual(hb.repo_branch_state(empty), {"unpushed": [], "dirty": 0})


def assistant_line(model, tokens):
    return json.dumps({"type": "assistant",
                       "message": {"model": model, "usage": {"input_tokens": tokens}}})


class HeartbeatCliTests(unittest.TestCase):
    """`autoos-agent.py heartbeat`: text and --json, exit-code precedence
    (3 pause > 4 over-cap > 1 unpushed/dirty > 0 clean)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.origin = os.path.join(self.tmp, "origin.git")
        self.work = os.path.join(self.tmp, "work")
        git("init", "-q", "--bare", self.origin, cwd=self.tmp)
        git("init", "-q", self.work, cwd=self.tmp)
        git("commit", "-q", "--allow-empty", "-m", "initial", cwd=self.work)
        git("remote", "add", "origin", self.origin, cwd=self.work)
        git("push", "-q", "-u", "origin", "main", cwd=self.work)
        self.inbox = os.path.join(self.tmp, "inbox.md")
        write_inbox(self.inbox, "2026-09-26T00:00:00Z operator: hello")
        self.transcript = os.path.join(self.tmp, "session.jsonl")
        with io.open(self.transcript, "w", encoding="utf-8") as fh:
            fh.write(assistant_line("claude-opus-4-6", 1000) + "\n")

    def run_heartbeat(self, *extra):
        return subprocess.run(
            [sys.executable, str(AGENT), "heartbeat", "--inbox", self.inbox,
             "--transcript", self.transcript, "--repo", self.work, *extra],
            capture_output=True, text=True, env=clean_env(), stdin=subprocess.DEVNULL)

    def test_a_pause_older_than_the_session_is_history(self):
        write_inbox(self.inbox, "2026-09-26T12:15:41Z from L0 PAUSE NOW")
        with io.open(self.transcript, "w", encoding="utf-8") as fh:
            fh.write('{"timestamp":"2026-09-26T13:35:00.000Z","type":"user"}\n')
            fh.write(assistant_line("claude-opus-4-6", 1000) + "\n")
        proc = self.run_heartbeat()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("pause: none", proc.stdout)

    def test_a_clean_repo_with_no_pause_and_low_context_is_all_clear(self):
        proc = self.run_heartbeat()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pause: none", proc.stdout)
        self.assertNotIn("unpushed:", proc.stdout)
        self.assertNotIn("dirty:", proc.stdout)
        self.assertIn("context: 1000/600000 0%", proc.stdout)

    def test_json_has_the_same_facts(self):
        proc = self.run_heartbeat("--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["pause"], {"active": False, "at": None, "text": None})
        self.assertEqual(data["repos"], [{"repo": self.work, "unpushed": [], "dirty": 0}])
        self.assertEqual(data["context"]["tokens"], 1000)
        self.assertFalse(data["over_cap"])
        self.assertEqual(data["exit_code"], 0)

    def test_unpushed_and_dirty_are_reported_and_exit_1(self):
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)
        with io.open(os.path.join(self.work, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        proc = self.run_heartbeat()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("unpushed: %s main 1" % self.work, proc.stdout)
        self.assertIn("dirty: %s 1 files" % self.work, proc.stdout)

    def test_pause_active_is_exit_3_and_takes_precedence(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)  # also unpushed
        proc = self.run_heartbeat("--cap", "1")  # also over cap
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("pause: active 2026-09-26T11:19:41Z operator: PAUSE NOW", proc.stdout)

    def test_over_cap_is_exit_4_and_beats_unpushed(self):
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)  # also unpushed
        proc = self.run_heartbeat("--cap", "1")
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertIn("over-cap", proc.stdout)

    def test_cap_flag_overrides_the_model_table(self):
        proc = self.run_heartbeat("--cap", "2000", "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["context"]["cap"], 2000)
        self.assertEqual(data["context"]["pct"], 50)
        self.assertFalse(data["over_cap"])

    def test_no_repo_flag_defaults_to_cwd(self):
        proc = subprocess.run(
            [sys.executable, str(AGENT), "heartbeat", "--inbox", self.inbox,
             "--transcript", self.transcript, "--json"],
            capture_output=True, text=True, env=clean_env(), cwd=self.work,
            stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["repos"]), 1)
        self.assertEqual(os.path.realpath(data["repos"][0]["repo"]), os.path.realpath(self.work))


class RunRefusesOnPauseTests(unittest.TestCase):
    """`run` refuses to start (exit 3) when AUTOOS_AGENT_INBOX names an inbox
    with an active PAUSE; with no env set, no check happens at all."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox = os.path.join(self._tmp.name, "inbox.md")

    def test_a_pause_older_than_the_callers_session_does_not_refuse(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        transcript = os.path.join(self._tmp.name, "s.jsonl")
        with io.open(transcript, "w", encoding="utf-8") as fh:
            fh.write('{"timestamp":"2026-09-26T13:35:00.000Z","type":"user"}\n')
        proc = subprocess.run(
            [sys.executable, str(AGENT), "run", "--tier", "2", "--dry-run", "t"],
            capture_output=True, text=True,
            env=clean_env(AUTOOS_AGENT_INBOX=self.inbox, AUTOOS_AGENT_TRANSCRIPT=transcript),
            stdin=subprocess.DEVNULL)
        self.assertNotEqual(proc.returncode, 3, proc.stderr)
        self.assertNotIn("PAUSE active", proc.stderr)

    def test_active_pause_refuses_the_run(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        proc = subprocess.run(
            [sys.executable, str(AGENT), "run", "--tier", "2", "--dry-run", "t"],
            capture_output=True, text=True,
            env=clean_env(AUTOOS_AGENT_INBOX=self.inbox), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("PAUSE", proc.stderr)

    def test_resumed_inbox_does_not_refuse(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T12:00:00Z operator: RESUME")
        proc = subprocess.run(
            [sys.executable, str(AGENT), "run", "--tier", "2", "--dry-run", "t"],
            capture_output=True, text=True,
            env=clean_env(AUTOOS_AGENT_INBOX=self.inbox), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_env_means_no_check_even_with_an_active_pause_file_present(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        proc = subprocess.run(
            [sys.executable, str(AGENT), "run", "--tier", "2", "--dry-run", "t"],
            capture_output=True, text=True, env=clean_env(), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class McpHeartbeatTests(unittest.TestCase):
    """The `heartbeat` MCP tool and spawn()'s AUTOOS_AGENT_INBOX refusal.

    Every spawn is a dry run with state redirected to a temp dir (the same
    isolation test_autoos_spawner.py's McpToolTests uses), so a refused or
    allowed spawn here never touches this checkout's own logs/agents/.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox = os.path.join(self._tmp.name, "inbox.md")
        state_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, state_dir, True)
        self._old_env = {k: os.environ.get(k) for k in
                        ("AUTOOS_STATE_DIR", "AUTOOS_AGENT_MCP_DRY_RUN",
                         "AUTOOS_AGENT_DEPTH", "AUTOOS_AGENT_MAX_DEPTH", "AUTOOS_AGENT_INBOX")}
        os.environ.update(AUTOOS_STATE_DIR=state_dir, AUTOOS_AGENT_MCP_DRY_RUN="1")
        os.environ.pop("AUTOOS_AGENT_DEPTH", None)
        os.environ.pop("AUTOOS_AGENT_MAX_DEPTH", None)
        os.environ.pop("AUTOOS_AGENT_INBOX", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_heartbeat_tool_returns_the_json_dict(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            out = mcp_server.heartbeat_info(inbox=None, transcript=None, repos=[tmp], cap=None)
        self.assertNotIn("error", out)
        self.assertEqual(out["pause"], {"active": False, "at": None, "text": None})
        self.assertEqual(out["repos"], [{"repo": tmp, "unpushed": [], "dirty": 0}])
        self.assertIn("exit_code", out)

    def test_heartbeat_tool_never_raises_on_a_bad_repo(self):
        out = mcp_server.heartbeat_info(repos=[os.path.join(self._tmp.name, "does-not-exist")])
        self.assertNotIn("error", out)
        self.assertEqual(out["repos"][0]["unpushed"], [])

    def test_spawn_refuses_when_the_inbox_env_names_an_active_pause(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        with mock.patch.dict(os.environ, {"AUTOOS_AGENT_INBOX": self.inbox}):
            out = mcp_server.spawn({"task": "t", "dry_run": True})
        self.assertIn("error", out)
        self.assertIn("PAUSE", out["error"])

    def test_spawn_with_no_inbox_env_is_unaffected(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOOS_AGENT_INBOX", None)
            out = mcp_server.spawn({"task": "t", "dry_run": True})
        self.assertNotIn("error", out)


if __name__ == "__main__":
    unittest.main()
