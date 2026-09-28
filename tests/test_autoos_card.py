#!/usr/bin/env python3
"""Tests for the state-card checker: tools/autoos_card.py and the
`autoos-agent.py card check <file>` verb (RESTART spec §1, lane R2a; §3's
`Q`-thread rule cites it).

Fixtures are strings and temp files. Nothing is spawned, no gateway is called
and no live run dir is read — the checker is read-only over the one file it
names, so it is safe to run twice (AGENTS.md §4).

Run from the repo root:

    python3 tests/test_autoos_card.py [ClassName]
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

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))

import autoos_card as card  # noqa: E402
import autoos_inbox as inbox  # noqa: E402  (§0: the ONE position parser)


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AGENT_MOD = load_agent()

HEADER = ("# card autoos-L1-routing — 2026-09-28T07:00:00Z | gen=run-1 "
          "| context 41k/200k | last-event 2026-09-28T06:59:00Z#2")

# A card in the §1 shape: every section present, in order, inside its cap,
# inside the 40-line and 200-char limits.
VALID = "\n".join([
    HEADER,
    "",
    "## goal",
    "1. Land RESTART R2a into main. (operator set it 2026-09-28T06:00:00Z)",
    "",
    "## state",
    "- lane L1-routing/R2a @ dc77c8a, clean.",
    "- main = dc77c8a, pushed.",
    "",
    "## next",
    "1. `card check` this card, push the lane, wait for CI.",
    "",
    "## threads",
    "- R2a | agent/20260928-r2a | working | finish tests.",
    "- Q-008 | routing-00 | asked 06:55Z | default (a).",
    "",
    "## traps",
    "- Never run tests/run-tests.sh unfiltered — it OOMs the host "
    "(source: herdr-server.log 2026-09-26T15:25Z).",
    "",
    "## operator",
    "- Recreate the 10-min heartbeat cron after any relaunch.",
    ""]) + "\n"

OPERATOR_LINE = "- Recreate the 10-min heartbeat cron after any relaunch."


def body(*sections):
    """A card from `(name, [content lines])` pairs, header first, no blank lines
    between sections except the one after the header."""
    parts = [HEADER]
    for name, lines in sections:
        parts.append("")
        parts.append("## %s" % name)
        parts.extend(lines)
    return "\n".join(parts) + "\n"


def full_body(**over):
    """Every §1 section present, each with one content line (or the override)."""
    content = dict(goal=["1. x"], state=["- x"], next=["1. x"],
                   threads=["- R2a | here | working | next"],
                   traps=["- x (source: test)"], operator=["- x"])
    content.update(over)
    return body(*[(name, content[name]) for name, _cap in card.SECTIONS])


def replace_section(text, name, lines):
    """Swap one section's content lines for `lines`; headings and the rest stay."""
    out, keep = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = card.heading_section(line) == name
            out.append(line)
            if keep:
                out.extend(lines)
            continue
        if keep:
            if line.strip():
                continue
            keep = False          # the blank line that closes the section
        out.append(line)
    return "\n".join(out) + "\n"


def with_header(header, text=VALID):
    return header + "\n" + text.split("\n", 1)[1]


def problems(text):
    return [(p.line, p.text) for p in card.check_card(text)]


class ModuleShapeTests(unittest.TestCase):
    """§1's one table: the section names, the caps and the limits live in the module."""

    def test_sections_and_caps_are_the_spec_table(self):
        self.assertEqual(tuple(card.SECTIONS),
                         (("goal", 3), ("state", 6), ("next", 3),
                          ("threads", 12), ("traps", 8), ("operator", 4)))

    def test_the_limits_are_the_spec_numbers(self):
        self.assertEqual(card.MAX_TOTAL_LINES, 40)
        self.assertEqual(card.MAX_LINE_CHARS, 200)

    def test_section_order_names_the_header_first(self):
        self.assertEqual(card.SECTION_ORDER,
                         ("header", "goal", "state", "next", "threads", "traps", "operator"))

    def test_the_expected_order_is_named_in_the_order_and_missing_messages(self):
        got = problems(full_body().replace("\n## traps\n", "\n"))
        self.assertIn("header, goal, state, next, threads, traps, operator", got[0][1])


class ValidCardTests(unittest.TestCase):
    def test_a_valid_card_has_no_problems(self):
        self.assertEqual(problems(VALID), [])

    def test_a_minimal_card_is_valid(self):
        self.assertEqual(problems(full_body()), [])

    def test_blank_lines_are_not_content_but_count_toward_the_total(self):
        self.assertEqual(problems(VALID.replace("\n## state", "\n\n\n## state")), [])

    def test_a_capitalised_heading_is_the_same_section(self):
        self.assertEqual(problems(VALID.replace("## traps", "## Traps")), [])

    def test_a_heading_with_a_trailing_note_is_the_same_section(self):
        self.assertEqual(problems(VALID.replace("## traps", "## traps: what bit us")), [])

    def test_a_file_without_a_trailing_newline_is_still_a_card(self):
        self.assertEqual(problems(VALID.rstrip("\n")), [])


class HeaderTests(unittest.TestCase):
    def strip_field(self, header, field):
        return " | ".join(p for p in header.split(" | ") if not p.strip().startswith(field))

    def test_the_header_must_be_the_first_line(self):
        got = problems("notes on this run\n" + VALID)
        self.assertEqual(got[0][0], 1)
        self.assertIn("not a card header", got[0][1])

    def test_an_empty_file_is_a_missing_header(self):
        got = problems("")
        self.assertEqual(got[0][0], 1)
        self.assertIn("card header", got[0][1])

    def test_a_missing_gen_field_is_reported_with_its_line(self):
        got = problems(with_header(self.strip_field(HEADER, "gen")))
        self.assertEqual([line for line, _t in got], [1])
        self.assertIn("gen=", got[0][1])

    def test_a_missing_context_field_is_reported(self):
        got = problems(with_header(self.strip_field(HEADER, "context")))
        self.assertEqual(got, [(1, got[0][1])])
        self.assertIn("context <n>k/<cap>k", got[0][1])

    def test_a_missing_last_event_field_is_reported(self):
        got = problems(with_header(self.strip_field(HEADER, "last-event")))
        self.assertIn("last-event", got[0][1])

    def test_a_malformed_context_is_reported(self):
        got = problems(with_header(HEADER.replace("context 41k/200k", "context 41/200")))
        self.assertIn("context", got[0][1])

    def test_a_bad_header_utc_is_reported(self):
        got = problems(with_header(HEADER.replace("— 2026-09-28T07:00:00Z", "— 2026-09-28T07:00Z")))
        self.assertIn("UTC", got[0][1])

    def test_an_impossible_header_date_is_reported(self):
        got = problems(with_header(HEADER.replace("— 2026-09-28T07:00:00Z",
                                                  "— 2026-02-30T07:00:00Z")))
        self.assertIn("UTC", got[0][1])

    def test_an_empty_name_is_reported(self):
        got = problems(with_header(HEADER.replace("# card autoos-L1-routing", "# card")))
        self.assertIn("name", got[0][1])

    def test_a_missing_em_dash_is_reported(self):
        got = problems(with_header(HEADER.replace(" — ", " - ")))
        self.assertIn("card", got[0][1])

    def test_an_unknown_header_field_is_reported(self):
        got = problems(with_header(HEADER + " | mood=tense"))
        self.assertEqual(got, [(1, got[0][1])])
        self.assertIn("mood=tense", got[0][1])

    def test_a_duplicate_header_field_is_reported(self):
        got = problems(with_header(HEADER + " | gen=run-2"))
        self.assertIn("gen=", got[0][1])

    def test_a_bare_utc_last_event_is_a_position(self):
        # §0: a bare stamp parses as ordinal 0, which reads that whole second.
        self.assertEqual(problems(with_header(HEADER.replace(
            "last-event 2026-09-28T06:59:00Z#2", "last-event 2026-09-28T06:59:00Z"))), [])

    def test_a_bad_last_event_position_is_reported_at_its_line(self):
        got = problems(with_header(HEADER.replace("06:59:00Z#2", "yesterday")))
        self.assertEqual([line for line, _t in got], [1])
        self.assertIn("position", got[0][1])

    def test_a_zero_ordinal_position_is_reported(self):
        got = problems(with_header(HEADER.replace("06:59:00Z#2", "06:59:00Z#0")))
        self.assertIn("position", got[0][1])

    def test_the_position_is_read_by_the_shared_inbox_parser(self):
        # §0 promises one parser: the wording of the problem is autoos_inbox's own.
        try:
            inbox.parse_position("yesterday")
            self.fail("parse_position accepted 'yesterday'")
        except ValueError as exc:
            self.assertIn(str(exc), problems(with_header(
                HEADER.replace("2026-09-28T06:59:00Z#2", "yesterday")))[0][1])


class SectionTests(unittest.TestCase):
    def test_a_missing_section_is_reported_at_where_it_belongs(self):
        text = full_body().replace("\n## traps\n", "\n")
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        line, message = got[0]
        self.assertIn("missing section 'traps'", message)
        self.assertEqual(line, text.splitlines().index("## operator") + 1)

    def test_a_last_section_missing_is_reported_at_the_end_of_the_file(self):
        text = full_body().replace("\n## operator\n- x\n", "\n")
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        self.assertIn("missing section 'operator'", got[0][1])
        self.assertEqual(got[0][0], len(text.splitlines()))

    def test_a_header_only_card_reports_every_section(self):
        got = problems(HEADER + "\n")
        self.assertEqual(len(got), 6, got)
        for _line, message in got:
            self.assertIn("missing section", message)
        self.assertEqual([name for _l, name in
                          ((line, message.split("'")[1]) for line, message in got)],
                         ["goal", "state", "next", "threads", "traps", "operator"])

    def test_a_section_out_of_order_is_reported(self):
        text = body(("goal", ["1. x"]), ("state", ["- x"]), ("next", ["1. x"]),
                    ("traps", ["- x (source: test)"]),
                    ("threads", ["- R2a | here | working | next"]),
                    ("operator", ["- x"]))
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        line, message = got[0]
        self.assertIn("threads", message)
        self.assertIn("out of order", message)
        self.assertEqual(line, text.splitlines().index("## threads") + 1)

    def test_a_duplicate_section_is_reported(self):
        text = full_body().replace("\n## next\n", "\n## next\n- dupe\n\n## next\n")
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        self.assertIn("duplicate section 'next'", got[0][1])

    def test_an_unknown_section_is_reported_and_the_real_one_still_missing(self):
        text = full_body().replace("## traps", "## Decisions + why")
        got = problems(text)
        self.assertEqual(len(got), 2, got)
        self.assertTrue(any("Decisions" in message for _line, message in got), got)
        self.assertTrue(any("missing section 'traps'" in message for _line, message in got), got)

    def test_a_section_over_its_cap_names_the_first_line_over_it(self):
        over = ["- t%d | here | now | next" % n for n in range(13)]
        text = full_body(threads=over)
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        line, message = got[0]
        self.assertIn("threads", message)
        self.assertIn("12-line cap", message)
        self.assertEqual(text.splitlines()[line - 1], over[12])

    def test_a_section_exactly_at_its_cap_is_valid(self):
        text = full_body(traps=["- trap %d (source: test)" % n for n in range(8)],
                         threads=["- T%d | here | now | next" % n for n in range(12)])
        self.assertEqual(problems(text), [])

    def test_text_before_the_first_heading_is_reported(self):
        text = full_body().replace(HEADER + "\n", HEADER + "\nstray note\n")
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        self.assertIn("stray note", got[0][1])
        self.assertEqual(got[0][0], 2)


class TotalTests(unittest.TestCase):
    def card_at(self, total):
        """A card of exactly `total` lines with every section inside its cap:
        header + 6 headings + content, no blank lines (only operator shrinks)."""
        counts = [3, 6, 3, 12, 8, 4]
        drop = 1 + 6 + sum(counts) - total
        self.assertTrue(0 <= drop <= counts[-1] - 1, drop)
        counts[-1] -= drop
        out = [HEADER]
        for (name, _cap), count in zip(card.SECTIONS, counts):
            out.append("## %s" % name)
            out.extend("- %s %d" % (name, index) for index in range(count))
        text = "\n".join(out) + "\n"
        self.assertEqual(len(text.splitlines()), total)
        return text

    def test_a_41_line_card_is_over_the_total_cap(self):
        got = problems(self.card_at(41))
        self.assertEqual(len(got), 1, got)
        line, message = got[0]
        self.assertEqual(line, 41)
        self.assertIn("41 lines", message)
        self.assertIn("40", message)

    def test_a_40_line_card_is_inside_the_total_cap(self):
        self.assertEqual(problems(self.card_at(40)), [])

    def test_an_over_long_card_reports_the_total_once_not_per_section(self):
        self.assertEqual(len(problems(self.card_at(43))), 1)


class CharLimitTests(unittest.TestCase):
    def test_a_201_char_line_is_reported_at_its_line(self):
        long = "- " + ("x" * 199)
        self.assertEqual(len(long), 201)
        text = VALID.replace(OPERATOR_LINE, long)
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        line, message = got[0]
        self.assertEqual(text.splitlines()[line - 1], long)
        self.assertIn("201 characters", message)
        self.assertIn("200-char", message)

    def test_a_200_char_line_is_allowed(self):
        exact = "- " + ("x" * 198)
        self.assertEqual(len(exact), 200)
        self.assertEqual(problems(VALID.replace(OPERATOR_LINE, exact)), [])

    def test_a_long_header_is_reported_on_the_header_line(self):
        got = problems(with_header(HEADER + " | note=" + ("y" * 200)))
        self.assertTrue(any(line == 1 and "200" in message for line, message in got), got)


class QuestionThreadTests(unittest.TestCase):
    """§3: `card check` rejects a `Q` thread without an `asked <time>` field."""

    def test_a_q_thread_with_an_asked_field_is_valid(self):
        self.assertEqual(problems(VALID), [])

    def test_a_q_thread_without_an_asked_field_is_reported(self):
        text = VALID.replace("- Q-008 | routing-00 | asked 06:55Z | default (a).",
                             "- Q-008 | routing-00 | waiting on L0 | default (a).")
        got = problems(text)
        self.assertEqual(len(got), 1, got)
        line, message = got[0]
        self.assertIn("Q-008", message)
        self.assertIn("asked", message)
        self.assertIn("Q-008", text.splitlines()[line - 1])

    def test_a_non_question_thread_needs_no_asked_field(self):
        text = VALID.replace("- Q-008 | routing-00 | asked 06:55Z | default (a).",
                             "- R2b | agent/xyz | ready | merge")
        self.assertEqual(problems(text), [])

    def test_a_q_line_outside_threads_is_not_checked(self):
        text = VALID.replace("- Never run tests/run-tests.sh unfiltered — it OOMs the host "
                             "(source: herdr-server.log 2026-09-26T15:25Z).",
                             "- Q-009 says the trap is the unfiltered suite.")
        self.assertEqual(problems(text), [])


class ReadFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autoos-card-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text, name="card.md"):
        path = os.path.join(self.tmp, name)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_check_file_returns_no_problems_for_a_valid_card(self):
        self.assertEqual(card.check_file(self.write(VALID)), [])

    def test_a_missing_file_is_unreadable(self):
        with self.assertRaises(card.CardUnreadable) as caught:
            card.check_file(os.path.join(self.tmp, "nope.md"))
        self.assertIn("cannot read", str(caught.exception))

    def test_a_directory_is_unreadable(self):
        with self.assertRaises(card.CardUnreadable):
            card.check_file(self.tmp)

    def test_undecodable_bytes_are_unreadable(self):
        path = os.path.join(self.tmp, "binary.md")
        with open(path, "wb") as fh:
            fh.write(b"# card \xff\xfe \xe2 \x94 no\n")
        with self.assertRaises(card.CardUnreadable):
            card.check_file(path)

    def test_crlf_line_endings_are_checked_not_mangled(self):
        self.assertEqual(card.check_file(self.write(VALID.replace("\n", "\r\n"))), [])

    def test_a_bom_is_tolerated(self):
        path = os.path.join(self.tmp, "bom.md")
        with io.open(path, "w", encoding="utf-8-sig") as fh:
            fh.write(VALID)
        self.assertEqual(card.check_file(path), [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autoos-card-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = AGENT_MOD.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def write(self, text, name="card.md"):
        path = os.path.join(self.tmp, name)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(AGENT)] + list(args),
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env={k: v for k, v in os.environ.items()
                                   if k != "AUTOOS_RUN_DIR"})

    def test_card_check_is_a_registered_verb_with_a_handler(self):
        self.assertIn("card", AGENT_MOD.VERB_PARSERS)
        self.assertIn("card", AGENT_MOD.VERB_HANDLERS)

    def test_the_cli_checks_with_this_module_and_not_a_second_one(self):
        self.assertIs(AGENT_MOD.card_mod, card)

    def test_a_valid_card_exits_0_and_says_ok(self):
        rc, out, err = self.invoke("card", "check", self.write(VALID))
        self.assertEqual(rc, 0, err)
        self.assertIn("OK", out)

    def test_a_violation_exits_1_and_prints_the_line_number(self):
        rc, out, err = self.invoke("card", "check",
                                   self.write(full_body().replace("## traps", "## traps2")))
        self.assertEqual(rc, 1, err)
        self.assertIn("card check: line ", out)
        self.assertIn("traps", out)

    def test_every_problem_prints_on_its_own_line(self):
        broken = replace_section(VALID, "threads",
                                 ["- T%d | here | now | next" % n for n in range(14)])
        broken = broken.replace("\n## traps\n", "\n")
        rc, out, _err = self.invoke("card", "check", self.write(broken))
        self.assertEqual(rc, 1)
        listed = [line for line in out.splitlines() if line.startswith("card check: line ")]
        self.assertEqual(len(listed), 2, out)
        self.assertIn("threads", out)
        self.assertIn("missing section 'traps'", out)

    def test_an_unreadable_file_exits_2_with_the_reason_on_stderr(self):
        rc, _out, err = self.invoke("card", "check", os.path.join(self.tmp, "nope.md"))
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err)

    def test_no_action_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.invoke("card")
        self.assertEqual(caught.exception.code, 2)

    def test_the_real_cli_accepts_a_valid_card(self):
        rc = self.run_cli("card", "check", self.write(VALID))
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertIn("OK", rc.stdout)

    def test_the_real_cli_rejects_a_bad_card_with_exit_1(self):
        rc = self.run_cli("card", "check", self.write("no header here\n"))
        self.assertEqual(rc.returncode, 1)
        self.assertIn("line 1", rc.stdout)

    def test_checking_the_same_card_twice_changes_nothing(self):
        # AGENTS.md §4: read-only, safe to run twice in a row.
        path = self.write(VALID)
        before = os.stat(path)
        for _run in range(2):
            rc = self.run_cli("card", "check", path)
            self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        after = os.stat(path)
        self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))


class RealCardAcceptanceTests(unittest.TestCase):
    """The card a successor of `autoos-L1-routing` writes from today's status
    file shape — the §1 shape, checked through the CLI in a temp dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autoos-card-real-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def check(self, text, name="autoos-L1-routing.card.md"):
        path = os.path.join(self.tmp, name)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return self.tmp, path

    def test_the_real_shaped_card_passes(self):
        _run, path = self.check(REAL_CARD)
        rc = subprocess.run([sys.executable, str(AGENT), "card", "check", path],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        lines = REAL_CARD.rstrip("\n").splitlines()
        self.assertLessEqual(len(lines), 40)
        self.assertTrue(all(len(line) <= 200 for line in lines))

    def test_the_old_status_shape_does_not_pass(self):
        # §1: "a successor writes a fresh card and does not convert the old
        # status file" — `references/state-file.md`'s headings are not card sections.
        _run, path = self.check(OLD_STATUS, name="L1-backlog.md")
        rc = subprocess.run([sys.executable, str(AGENT), "card", "check", path],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("not a card header", rc.stdout)

    def test_a_card_holding_the_measured_long_status_line_is_named(self):
        # §1 measured 31 of 105 lines over the limit in one real status file.
        _run, path = self.check(OLD_STATUS.replace(
            "## Goal (priority order, who set it)",
            "## goal\n- " + ("hold this line for the successor " * 8)), name="long.md")
        rc = subprocess.run([sys.executable, str(AGENT), "card", "check", path],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("200-char", rc.stdout)


HEADER_ONLY = HEADER

OLD_STATUS = """# status L1-backlog — 2026-09-28T07:00:00Z (a1b2c3, context 41k/200k)
status: working. heartbeat cron 7f21 (every 10 minutes).

## Goal (priority order, who set it)
1. land R2

## Decisions + why
- chose (a) — cheaper (review-b3c1.out)

## Open questions
- Q-001 — asked L0 06:12Z | waiting on operator

## Lanes
| id | branch | worktree | worker | state | next |
|---|---|---|---|---|---|

## Operator steps
- rerun CI
"""

REAL_CARD = """# card autoos-L1-routing — 2026-09-28T07:12:00Z | gen=manifest-2026-09-28-r2a | context 61k/200k | last-event 2026-09-28T07:05:00Z#3

## goal
1. Merge the RESTART lanes into main in R1 → R2 → R3 order. (operator, 2026-09-28T05:40Z)
2. Keep main green: CI on the lane before any merge (AGENTS.md §7).

## state
- lane L1-routing/R2a @ dc77c8a; main = origin/main = dc77c8a, pushed.
- R1 inbox reader merged; `card check` lands here; `pack` is R3, not started.
- Held: R2b heartbeat `card: stale` — same files, needs this verb first.

## next
1. `card check` this card, push L1-routing/R2a, run CI, merge no-ff.
2. Brief R2b from the merged sha.
3. R3 pack after R2a and R2b are in main.

## threads
- R2a | agent/20260928-r2a | working | commit and report.
- R2b | lane L1-routing/R2b | blocked-on-R2a | heartbeat card key.
- Q-008 | routing-00 | asked 06:55Z | default (a): no MCP twin for card check.

## traps
- The unfiltered run-tests.sh OOMs the host — filter it (herdr-server.log 15:25Z).
- §0's marker list lives in autoos_heartbeat.ACK_MARKERS only; never restate it.
- .ps1 keeps CRLF in the working copy; the diff and the bytes differ (.gitattributes).

## operator
- Recreate the 10-min heartbeat cron after any relaunch (R-coord-07).
"""


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    sys.exit(0 if result.wasSuccessful() else 1)
