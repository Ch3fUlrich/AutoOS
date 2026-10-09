#!/usr/bin/env python3
"""Tests for tools/autoos_ready_guards.py (AO-WRITER-GUARDS P4a).

Hermetic (D-852): no runner, no subprocess, no git, no network — diff paths are
arguments, the tool lookup is injected (a directory name comes from this checkout's
own path, only ever stat'ed), the brief is a real render."""
import inspect
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_brief as brief  # noqa: E402
import autoos_ready_guards as g  # noqa: E402

PATHS = ["tools/a.py", "tools/b.yml", "tests/test_a.py"]
SIX = [1, 2, 3, 4, 5, 6]
ALL_OK = [(n, "PASS") for n in SIX]


def rendered(paths=PATHS):
    return brief.render_brief("ops", "R2", {
        "task_id": "AO-1", "one_line": "fix mail", "paths": list(paths),
        "goal": "once-only mail", "invariants": "idempotent", "entity": "host",
        "keys_file": "keys.yml", "reference_playbook": "ref.yml", "state_path": paths[0],
        "indent": "2", "files": paths[0], "playbooks": paths[1], "test_files": paths[2]})


def files_line(paths):
    return ("# AO-1\nFILES (only these; <= 3 files, <= 200 lines changed): "
            + ", ".join(paths) + ". Do not touch other lines/files.\nGOAL: y.\n")


def report(rows):
    return "REPORT:\n" + "\n".join("CHECK %d: %s tail output" % r for r in rows)


# Not a plain relative path: a fence violation *and* an unparsable FILES entry.
BAD = ["", "/etc/shadow", "../tools/a.py", "tools/../a.py", "tools//a.py", "/tools/a.py",
       "tools/", "tools/a.py.", "tools/CON.py", "tools/NUL.py", "a\\b.py", "C:/a.py",
       "drive:x", "tools/a.py:", "*.py", "tools/fr\u00e9d\u00e9ric.py", "\uff54ools/a.py",
       "tool\u0455/a.py", "tools/a\u200bb.py"]
# Well-formed paths no allowed entry names: sibling, nested, padded, look-alike.
OUT = ["tools/a.pyx", "tools/b.py", "docs/a.py", "tools/a.py/evil.py", "not a path",
       " tools/a.py", "tools/a.py ", "tools/a.py\u00a0", "docs", "readme.md"]
# FILES lines that are absent, reworded, or carry a forged path list.
BAD_TEXTS = ["", "GOAL: nothing here.\n", "# AO-1\n",
             "FILES (only these; <= 3 files): a.py. Do not touch other lines/files.\n",
             "FILES (only these; x): evil.py. Do not touch other lines/files.\n",
             "FILES (only these; <= 3 files, <= 200 lines changed): a.py. Do not touch.\n"]
LIST_ROWS = [["a.py", "b.py", "c.py", "d.py"], ["a.py, , b.py"], [", a.py"],
             ["a.py, A.PY"], ["a.py", "a.py"]]
# (diff paths, allowed, violations): rename halves, empty allowed fails closed.
FENCE = [(["./tools/a.py", "tools/./a.py"], ["tools/a.py"], ["tools/./a.py"]),
         (["Tools/A.PY"], ["tools/a.py"], ["Tools/A.PY"]),
         (["tools/a.py"], ["Tools/A.PY"], ["tools/a.py"]),
         (PATHS, PATHS, []), (["tools/a.py", "tests/test_a.py"], PATHS, []),
         (["tools/a.py", "tools/c.py"], PATHS, ["tools/c.py"]),
         (["tools/gone.py", "tools/a.py"], PATHS, ["tools/gone.py"]),
         (["docs/x.md"], ["docs"], ["docs/x.md"]),
         (["tools/a.py"], ["tools/a\u200b.py"], ["tools/a.py"]),
         (PATHS, [], PATHS), ([], ["tools/a.py"], [])]
# (1) Linux paths are case-sensitive: only one leading './' is normalised, never
# case, never an inner './', never a Unicode fold. (diff, allowed, violations).
CASE_FENCE = [("Tools/A.PY", ["tools/a.py"], ["Tools/A.PY"]),
              ("tools/a.Py", ["tools/a.py"], ["tools/a.Py"]),
              ("TOOLS/A.PY", ["tools/a.py"], ["TOOLS/A.PY"]),
              ("tools/a.py", ["Tools/A.PY"], ["tools/a.py"]),
              ("tools/./a.py", ["tools/a.py"], ["tools/./a.py"]),
              ("././tools/a.py", ["tools/a.py"], ["././tools/a.py"]),
              ("tools/Ａ.py", ["tools/a.py"], ["tools/Ａ.py"]),
              # Same file, two spellings: './' is the ONE allowed normalisation.
              ("./tools/a.py", ["tools/a.py"], []),
              ("tools/a.py", ["./tools/a.py"], []),
              ("./tools/a.py", ["./tools/a.py"], [])]
# (report text, required, (ok, missing, failed, input_required)).
CHECKS = [(report(ALL_OK), SIX, (True, [], [], [])),
          ("", SIX, (False, SIX, [], [])),
          ("I ran everything, trust me.\n", SIX, (False, SIX, [], [])),
          (report([(1, "PASS"), (2, "FAIL"), (3, "PASS"), (5, "INPUT_REQUIRED"),
                   (6, "PASS")]), SIX, (False, [4], [2], [5])),
          (report([(1, "INPUT_REQUIRED")]), [1], (False, [], [], [1])),
          (report([(1, "PASS")] * 2), [1], (True, [], [], [])),
          (report(ALL_OK + [(7, "FAIL"), (99, "PASS")]), SIX, (True, [], [], [])),
          ("CHECK 01: PASS tail\n", [1], (False, [1], [], [])),
          ("CHECK 03: PASS tail\n", [3], (False, [3], [], [])),
          ("check 1: PASS tail\n", [1], (False, [1], [], []))]
# (5) CHECK lines count at column 0 with an ASCII number and an evidence tail.
NOT_COUNTED = ["  CHECK 1: PASS tail\n", "   CHECK 1: PASS tail\n", "\tCHECK 1: PASS tail\n",
               "> CHECK 1: PASS tail\n", ">CHECK 1: PASS tail\n", "- CHECK 1: PASS tail\n",
               "* CHECK 1: PASS tail\n", "1. CHECK 1: PASS tail\n", "CHECK 01: PASS tail\n",
               "CHECK 001: PASS tail\n", "check 1: PASS tail\n", "CHECK \u0661: PASS tail\n",
               "checklist 1: PASS tail\n"]
NO_TAIL = ["CHECK 1: PASS\n", "CHECK 1: PASS   \n", "CHECK 1: FAIL\n",
           "CHECK 1: INPUT_REQUIRED\n", "CHECK 1:\n", "CHECK 1: \n"]
# Duplicates with conflicting verdicts: neither order may let the last one win.
CONFLICTS = [[(1, "PASS"), (1, "FAIL")], [(1, "FAIL"), (1, "PASS")],
             [(1, "PASS"), (1, "INPUT_REQUIRED")], [(1, "INPUT_REQUIRED"), (1, "PASS")]]
# Words that are not the protocol, and a CHECK line with no verdict at all.
NOISE = ["CHECK 1: PROBABLY tail\n", "CHECK 1: PASSED all\n", "CHECK 1: pass tail\n",
         "CHECK 1: FAILSAFE x\n", "CHECK 1:\n"]
T = report(ALL_OK)
# The same six PASS lines, quoted or fenced as output: none of them may count.
FORGEROWS = ["```\n%s\n```" % T, "~~~\n%s\n~~~" % T, "```\n%s" % T,
             "  ```py\n  %s\n  ```" % T.replace("\n", "\n  "),
             "> " + T.replace("\n", "\n> "),
             "\n".join("   > " + ln for ln in T.splitlines())]
# (P4a fix 1) The measured forgery: the report side used to toggle on ANY fence
# line, so '```' then '~~~' re-opened the block and the CHECK lines counted. Both
# readers now share _Fences, so every one of these bodies hides its CHECK lines —
# and a fence that is never closed swallows the rest of the text.
FENCE_TOGGLE = ["```\n~~~\nCHECK 1: PASS forged\n~~~\n```\n",
                "~~~\n```\nCHECK 1: PASS forged\n```\n~~~\n",
                "````\nCHECK 1: PASS forged\n```\n",
                "````\nCHECK 1: PASS forged\n````\n",
                "```\nCHECK 1: PASS forged\n````\n",
                "```\nCHECK 1: PASS forged\n``` tail\n",
                # Same shape, but the forged line sits AFTER the would-be closer:
                # a marker with trailing text is content, so the fence is still
                # open and this line is swallowed too.
                "```\nCHECK 1: PASS forged\n``` tail\nCHECK 1: PASS forged2\n",
                "```\nCHECK 1: PASS forged\n"]
# ... while a real fence still closes and the report below it still counts: same
# character, at least as long, nothing after the marker.
FENCE_CLOSED_OK = ["~~~\nCHECK 1: PASS forged\n~~~\nCHECK 1: PASS real tail\n",
                   "````\nCHECK 1: PASS forged\n`````\nCHECK 1: PASS real tail\n",
                   "```\noutput\n```\nCHECK 1: PASS real tail\n",
                   "```py\nCHECK 1: PASS forged\n```\nCHECK 1: PASS real tail\n"]
# The same shapes on the brief side: FILES never read inside them.
FENCE_TOGGLE_BRIEF = ["```\n~~~\n" + files_line(["a.py"]),
                      "````\n" + files_line(["a.py"]) + "```\n",
                      "```\n" + files_line(["a.py"]) + "``` tail\n",
                      # The FILES line is AFTER the marker-with-trailing-text, and
                      # that marker did not close the block: still swallowed.
                      "```\nsome output\n``` tail\n" + files_line(["a.py"]),
                      "~~~\n" + files_line(["a.py"]),
                      "````\n" + files_line(["a.py"]) + "````\n"]
# (P4a round 3) Every character str.splitlines() ends a line on besides
# '\n' and '\r' — \x0b \x0c \x1c \x1d \x1e \x85 \u2028 \u2029 — plus the other C0
# controls, none of which belongs in a brief or a report.
LINE_BREAKERS = "\x00\x01\x08\x0b\x0c\x0e\x1c\x1d\x1e\x1f\x85\u2028\u2029"
# (P4a round 4) EVERY Unicode category Zs/Zl/Zp/Cc/Cf character the body may not
# carry (besides the Cc ones above): the spaces str.strip()/str.isspace() treat as
# whitespace but that no brief or report spells — NBSP, the U+1680/U+2000-U+200A/
# U+202F/U+205F/U+3000 spaces, line/paragraph separators, and the invisible
# format characters (soft hyphen, ZWSP/ZWJ/LRM/RLM, word joiner, BOM, ...). A
# tab is NOT in here: it stays legal content, just never fence indentation.
ZS_SPACES = ("\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
             "\u2008\u2009\u200a\u202f\u205f\u3000")
CF_FORMAT = "\u00ad\u061c\u180e\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"
OTHER_Cc = "\x0b\x0c\x1c\x1d\x1e\x1f\x7f"
INVISIBLE = ZS_SPACES + "\u2028\u2029" + CF_FORMAT + OTHER_Cc
# (P4a round 4, A) CommonMark expands a leading TAB to 4 columns: a tab-indented
# ``` is fence CONTENT, never an opener or a closer. Each body here forges a
# CHECK 6 exactly like the measured defect — '```\n\t```\nCHECK 6: PASS forged'
# — which the old `[ \t]{0,3}` fence indent read as a closed fence.
TAB_FENCE_CLOSE = [
    "```\n\t```\nCHECK 1: PASS forged\n",
    "```\n \t```\nCHECK 1: PASS forged\n",
    "```\n\t\t```\nCHECK 1: PASS forged\n",
    "~~~\n\t~~~\nCHECK 1: PASS forged\n",
    # The measured report: CHECK 1-5 genuine, the fenced block 'closed' by a
    # tab-indented marker, the forged CHECK 6 tail counted.
    "\n".join("CHECK %d: PASS tail %d" % (n, n) for n in (1, 2, 3, 4, 5))
    + "\n```\noutput\n\t```\nCHECK 6: PASS forged tail\n",
]
# A tab-indented OPENER is likewise not a fence line: nothing is swallowed.
TAB_FENCE_OPEN = ["\t```\nCHECK 1: PASS real tail\n",
                  "  \t```\nCHECK 1: PASS real tail\n"]


def forged_check_body(ch):
    """The measured forgery: CHECK 1-5 genuine and NO real CHECK 6, and a fenced
    line that reads as a bare ``` closer only to a splitlines() reader, so the
    forged `CHECK 6: PASS` line below it used to count and the report said ok."""
    real = "\n".join("CHECK %d: PASS tail %d" % (n, n) for n in (1, 2, 3, 4, 5))
    return "```\nfoo" + ch + "```\n" + real + "\nCHECK 6: PASS forged\n"


def forged_files_body(ch):
    """The brief twin of the same trick: one fence 'closed' by the line break, and
    under it a canonical FILES line the brief never authorised."""
    return "```\nfoo" + ch + "```\n" + files_line(["evil.py"])


# (P4a fix 4) Size limits: a body over 1 MiB is not a brief or a report, a path
# over these limits is not a repo-relative path — and a space INSIDE a name is
# legal, so none of these rows may reject it. Each over-limit path breaks exactly
# ONE rule, which is what makes the limits observable rather than tangled.
_HEAD = "CHECK 1: PASS tail\n"
OVER_TEXT = "x" * (g.MAX_TEXT_CHARS + 1)
AT_TEXT = _HEAD + "y" * (g.MAX_TEXT_CHARS - len(_HEAD))          # exactly 1 MiB
OVER_TOTAL = "a" * 99 + "/" + "b" * 99 + "/" + "c" * 3           # 203 chars
OVER_COMPONENT = "tools/" + "b" * 101                            # 107 chars
OVER_DEEP = "/".join(["d"] * 21) + "/f.py"                       # 22 components
AT_TOTAL = "a" * 99 + "/" + "b" * 100                            # exactly 200
AT_COMPONENT = "tools/" + "b" * 100                              # exactly 100
AT_DEEP = "/".join(["d"] * 19) + "/f.py"                         # exactly 20
SPACED = ["ops/host names.yml", "not a path", "docs/readme notes.md", "a b/c d.py"]

# Arguments of the wrong type (P4a fix 2): must raise GuardError, never die with
# AttributeError/TypeError inside a line loop. A str is not one of these for a
# sequence argument — see NON_SEQ; Path is left out because whether PurePath is
# iterable changed between Python versions, and this must not depend on it.
NON_TEXT = (0, 7, -1, None, True, 3.5, b"CHECK 1: PASS tail", bytearray(b"x"),
            [], (), {}, {"report": 1}, object(), Path("tools/a.py"))
NON_SEQ = (0, 7, None, True, 3.5, b"tools/a.py", bytearray(b"tools/a.py"),
           object(), "tools/a.py", "tools")
# (P4a fix 3) `required` is a caller argument, not a hint.
BAD_REQUIRED = [(), [], None, 1, "1", b"1", 1.0, [1.0], ["1"], [True], [False],
                [0], [-1], [100], [1000], [1, 1], [1, 2, 1], {1}, {1, 2},
                range(1, 3), (1, 99, 99), (0, 1), (None,), ((1,),)]
GOOD_REQUIRED = [(1,), [1], (1, 2, 3), [99], (1, 99), tuple(SIX)]


def quoted_all(text):
    return "".join("> " + ln + "\n" for ln in text.splitlines())


def indent_files(text, mark="  "):
    return text.replace("\nFILES", "\n" + mark + "FILES")


def crlf_files(text):
    return text.replace("other lines/files.\n", "other lines/files.\r\n")


# (3) FILES-looking lines are never used silently: indented, CR-terminated,
# quoted, or inside a fence — and a second canonical line anywhere.
FILES_FORGEROWS = [
    indent_files(files_line(["a.py"])),
    indent_files(files_line(["a.py"]), "\t"),
    files_line(["a.py"]).replace("\n", "\r\n"),
    crlf_files(files_line(["a.py"])),
    files_line(["a.py"]).replace("a.py. Do", "a.py\r. Do"),
    quoted_all(files_line(["a.py"])),
    files_line(["a.py"]) + quoted_all(files_line(["b.py"])),
    files_line(["a.py"]) + indent_files(files_line(["b.py"]).lstrip("# AO-1\n"))
    + "GOAL: y.\n",
    "```\n" + files_line(["a.py"]) + "```\n",
    "~~~\n" + files_line(["a.py"]) + "~~~\n",
    "```py\n" + files_line(["a.py"]) + "```",
    # A ~~~ line does NOT close a ``` block, so the FILES line is still fenced.
    "```\n~~~\n" + files_line(["a.py"]) + "```\n",
    "~~~\n```\n" + files_line(["a.py"]) + "~~~\n",
    "  ```\n" + files_line(["a.py"]),
    files_line(["a.py"]) + "```\nFILES (only these; x): y.py\n```\n",
    files_line(["a.py"]) + "~~~\n~~~\nFILES (only these; x): y.py\n"]


class ReadyGuardsTests(unittest.TestCase):
    def test_brief_files_reads_the_rendered_ops_brief(self):
        self.assertTrue(issubclass(g.GuardError, ValueError))
        self.assertEqual(g.brief_files(rendered()), PATHS)
        # './' is the one normalisation; the case of a FILES entry is kept, so
        # the fence can still refuse 'Tools/A.PY' when the brief says 'tools/a.py'.
        self.assertEqual(g.brief_files(files_line(["./Tools/A.PY"])), ["Tools/A.PY"])

    def test_brief_files_ignores_fenced_output_it_never_counts(self):
        # Non-FILES content inside a fence is ignored; a closed fence does not
        # hide the real canonical line, before or after it.
        self.assertEqual(g.brief_files("```\nsome output\n```\n" + files_line(["a.py"])),
                         ["a.py"])
        self.assertEqual(g.brief_files(files_line(["a.py", "b.py"])
                                       + "~~~\nCHECK 1: PASS tail\n~~~\n"),
                         ["a.py", "b.py"])
        # Duplicate DETECTION stays case-insensitive (same file on one path).
        for dup in (["a.py", "A.PY"], ["./a.py", "a.py"], ["a.py", "a.py"]):
            with self.assertRaises(g.GuardError):
                g.brief_files(files_line(dup))

    def test_brief_files_refuses_non_canonical_files_lines(self):
        for text in FILES_FORGEROWS:
            with self.subTest(text=text[:32]):
                with self.assertRaises(g.GuardError):
                    g.brief_files(text)

    def test_brief_files_refuses_forged_briefs(self):
        texts = (BAD_TEXTS + [files_line(r) for r in LIST_ROWS]
                 + [files_line(["a.py"]) * 2, files_line(["a.py"]) + files_line(["b.py"])]
                 + [files_line([p]) for p in BAD])
        for text in texts:
            with self.assertRaises(g.GuardError):
                g.brief_files(text)

    def test_scope_fence_flags_every_unlisted_path(self):
        for path in BAD + OUT:
            with self.subTest(path=path):
                self.assertEqual(g.scope_fence([path], ["tools/a.py"]), [path])

    def test_scope_fence_table_and_brief_scope_round_trip(self):
        self.assertEqual([(d, a, g.scope_fence(d, a)) for d, a, _ in FENCE],
                         [(d, a, w) for d, a, w in FENCE])
        self.assertEqual(g.scope_fence(PATHS, g.brief_files(rendered())), [])

    def test_scope_fence_is_case_sensitive_and_collapses_only_dot_slash(self):
        for diff, allowed, want in CASE_FENCE:
            with self.subTest(diff=diff, allowed=allowed):
                self.assertEqual(g.scope_fence([diff], allowed), want)
        # A brief that allows 'tools/a.py' does NOT allow 'Tools/A.PY': on Linux
        # they are two files, and the fence is the only thing standing between.
        self.assertEqual(g.scope_fence(["Tools/A.PY"], g.brief_files(rendered())),
                         ["Tools/A.PY"])

    def test_scope_fence_non_str_inputs_fail_closed(self):
        self.assertEqual(g.scope_fence([None, 5, b"x"], ["tools/a.py"]),
                         ["None", "5", "b'x'"])
        self.assertEqual(g.scope_fence(["tools/a.py", None, ("tools/b.py",)],
                                       ["tools/a.py"]), ["None", "('tools/b.py',)"])
        for allowed in ([123], ["tools/a.py", None], [b"tools/a.py"], [Path("tools/a.py")],
                        [True]):
            with self.subTest(allowed=allowed):
                with self.assertRaises(g.GuardError):
                    g.scope_fence(["tools/a.py"], allowed)

    def test_report_checks_table(self):
        got = [tuple(g.report_checks(t, required=r)[k]
                    for k in ("ok", "missing", "failed", "input_required"))
               for t, r, _ in CHECKS]
        self.assertEqual(got, [w for _, _, w in CHECKS])

    def test_report_checks_column_zero_and_evidence_tail(self):
        for text in NOT_COUNTED:
            with self.subTest(text=text[:24]):
                out = g.report_checks(text, required=[1])
                self.assertEqual((out["ok"], out["missing"], out["failed"]),
                                 (False, [1], []))
        for text in NO_TAIL:
            with self.subTest(text=text[:24]):
                out = g.report_checks(text, required=[1])
                self.assertEqual((out["ok"], out["missing"], out["failed"]),
                                 (False, [], [1]))
        self.assertEqual(g.report_checks("CHECK 1: PASS tail\n", required=[1])["ok"], True)

    def test_report_checks_conflicts_and_single_fail(self):
        for rows in CONFLICTS:
            out = g.report_checks(report(rows), required=[1])
            self.assertEqual((out["ok"], out["failed"]), (False, [1]))
        for n in SIX:
            rows = [(m, "PASS" if m != n else "FAIL") for m in SIX]
            self.assertEqual(g.report_checks(report(rows))["failed"], [n])

    def test_report_checks_unparsable_verdicts_fail_closed(self):
        for text in NOISE:
            out = g.report_checks(text, required=[1])
            self.assertEqual((out["ok"], out["failed"], out["missing"]), (False, [1], []))

    def test_report_checks_forged_fenced_and_quoted_lines(self):
        for body in FORGEROWS:
            with self.subTest(body=body[:18]):
                out = g.report_checks("tail says:\n%s\ndone.\n" % body)
                self.assertEqual((out["ok"], out["missing"]), (False, SIX))

    def test_both_readers_share_one_fence_rule(self):
        # (fix 1) The toggle forgery and its relatives: no CHECK line counts.
        for body in FENCE_TOGGLE:
            with self.subTest(body=body[:26]):
                out = g.report_checks("tail says:\n%s\ndone.\n" % body, required=[1])
                self.assertEqual((out["ok"], out["missing"], out["failed"]),
                                 (False, [1], []))
        # An unclosed fence swallows the REST, not just the block.
        swallowed = g.report_checks("```\nCHECK 1: PASS forged\n"
                                    + report(ALL_OK), required=SIX)
        self.assertEqual((swallowed["ok"], swallowed["missing"]), (False, SIX))
        # A real closer still opens the text below it to count.
        for body in FENCE_CLOSED_OK:
            with self.subTest(body=body[:26]):
                out = g.report_checks(body, required=[1])
                self.assertEqual((out["ok"], out["missing"], out["failed"]), (True, [], []))
        # The same shapes on the brief side, which reads them with the same tracker.
        for body in FENCE_TOGGLE_BRIEF:
            with self.subTest(body=body[:26]):
                with self.assertRaises(g.GuardError):
                    g.brief_files(body)
        both = "```\n~~~\nCHECK 1: PASS forged\n~~~\n```\n"
        self.assertEqual(g.report_checks(both, required=[1])["missing"], [1])
        self.assertEqual(g.brief_files(both + files_line(["a.py"])), ["a.py"])

    def test_tab_indented_fence_is_content_never_markup(self):
        # (round 4 A) The measured forgery: '```\n\t```\nCHECK 6: PASS forged'
        # closed the fence because the old indent class `[ \t]{0,3}` counted a
        # TAB as one of the 0-3 blanks. Fence indentation is ' ' only now.
        for body in TAB_FENCE_CLOSE:
            with self.subTest(body=body[:30]):
                out = g.report_checks(body, required=[6])
                self.assertEqual((out["ok"], out["missing"]), (False, [6]))
        for body in TAB_FENCE_CLOSE[:4]:
            with self.subTest(body=body[:30]):
                out = g.report_checks(body, required=[1])
                self.assertEqual((out["ok"], out["missing"]), (False, [1]))
        # The brief twin: after a tab 'closer' the FILES line is still fenced,
        # so it never returns the forged path — it raises.
        for closer in ("\t```", " \t```", "\t~~~", "\t````"):
            with self.subTest(closer=closer):
                with self.assertRaises(g.GuardError):
                    g.brief_files("```\nsome output\n%s\n" % closer
                                  + files_line(["evil.py"]))
        # A tab-indented OPENER is not markup either: nothing is swallowed.
        for body in TAB_FENCE_OPEN:
            with self.subTest(body=body[:20]):
                self.assertEqual(g.report_checks(body, required=[1])["ok"], True)
        # A genuine fenced output block whose CONTENT lines are tab-indented
        # still hides the forged CHECK lines inside it — and the tab-indented
        # closer is content, so the block stays open and swallows the column-0
        # line after it too.
        hidden = g.report_checks("```\n\toutput\n\tCHECK 1: PASS forged\n\t```\n"
                                 "CHECK 1: PASS after\n", required=[1])
        self.assertEqual((hidden["ok"], hidden["missing"]), (False, [1]))
        # Up to 3 leading SPACES is still the fence indent, opener and closer.
        self.assertEqual(g.report_checks("   ```\nCHECK 1: PASS forged\n   ```\n"
                                         "CHECK 1: PASS real tail\n",
                                         required=[1])["ok"], True)

    def test_invisible_whitespace_and_format_chars_are_refused(self):
        # (round 4) Every Zs/Zl/Zp/Cc/Cf character besides ' \t\n\r' is refused
        # in a body outright: as fence indent, inside a verdict tail, and
        # leading or trailing a FILES item — where str.strip() used to eat it
        # and hand back 'tools/a.py' for the brief's 'tools/a.py\xa0'.
        for ch in INVISIBLE:
            with self.subTest(ch=repr(ch)):
                with self.assertRaises(g.GuardError):
                    g.report_checks("```\n%s```\nCHECK 1: PASS forged\n" % ch,
                                    required=[1])
                with self.assertRaises(g.GuardError):
                    g.brief_files("```\n%s```\n" % ch + files_line(["evil.py"]))
                with self.assertRaises(g.GuardError):
                    g.report_checks("CHECK 1: PASS %stail\n" % ch, required=[1])
                with self.assertRaises(g.GuardError):
                    g.brief_files(files_line(["tools/a.py%s" % ch]))
                with self.assertRaises(g.GuardError):
                    g.brief_files(files_line(["%stools/a.py" % ch]))
        # A printable non-ASCII letter is not invisible: it stays body content.
        self.assertEqual(g.report_checks("CHECK 1: PASS café tail\n",
                                         required=[1])["ok"], True)

    def test_files_items_strip_only_spaces_and_name_the_bad_item(self):
        # (round 4 B) ',' plus optional ' ' is the separator; ONLY ' ' is
        # trimmed, and any other character in an item raises naming the item.
        canon = ("FILES (only these; <= 3 files, <= 200 lines changed): %s."
                 " Do not touch other lines/files.\n")
        self.assertEqual(g.brief_files(canon % "a.py,b.py"), ["a.py", "b.py"])
        self.assertEqual(g.brief_files(canon % "a.py,  b.py "), ["a.py", "b.py"])
        # A TAB is legal body text, so only the item rule can refuse it — and
        # it names the item. The other characters die one step earlier, in
        # _guard_text (see test_invisible_whitespace_and_format_chars...).
        for raw in ("a.py,\tb.py", "a.py\t, b.py", "a.py, \tb.py", "a.py,b.py\t"):
            with self.subTest(raw=repr(raw)):
                with self.assertRaises(g.GuardError) as cm:
                    g.brief_files(canon % raw)
                self.assertIn("FILES item", str(cm.exception))

    def test_non_ascii_paths_are_violations_never_folds(self):
        # (round 4 B) The ASCII-only rule is explicit for exact_path inputs:
        # a non-ASCII path can never equal an allowed ASCII path.
        for p in ("tools/fr\u00e9d\u00e9ric.py", "tools/a.py\u00a0",
                  "\u3000tools/a.py", "\uff54ools/a.py", "tools/a\u200bb.py",
                  "tools/a.py\x7f", "tools/a.py\u2028"):
            with self.subTest(path=repr(p)[:24]):
                self.assertIsNone(g.exact_path(p))
                self.assertIsNone(g.norm_path(p))
                self.assertEqual(g.scope_fence([p], [p]), [p])
                self.assertEqual(g.scope_fence([p], ["tools/a.py"]), [p])

    def test_line_breaking_control_chars_are_refused(self):
        # (round 3) A body that would split into more lines than it has
        # '\n's is refused outright — never read, so it can never come back ok.
        for ch in LINE_BREAKERS:
            with self.subTest(ch=repr(ch)):
                with self.assertRaises(g.GuardError):
                    g.report_checks(forged_check_body(ch), required=SIX)
                with self.assertRaises(g.GuardError):
                    g.brief_files(forged_files_body(ch))
        # A tab is content, not a line break: the gate never trips on one, and a
        # genuine C0-free report with tabs still reads ok.
        self.assertEqual(g.report_checks("CHECK 1: PASS\ttail\t\n",
                                         required=[1])["ok"], True)

    def test_crlf_bodies_split_on_newline_only(self):
        # One trailing '\r' is the line ending: a CRLF report keeps counting its
        # genuine CHECK lines, and keeps hiding its fenced ones.
        self.assertEqual(g.report_checks(report(ALL_OK).replace("\n", "\r\n"))["ok"], True)
        fenced = ("```\n%s\n```\ndone\n" % report(ALL_OK)).replace("\n", "\r\n")
        self.assertEqual(g.report_checks(fenced, required=SIX)["missing"], SIX)
        # A lone '\r' is content, never a break: a CHECK line that only a
        # splitlines() reader would see at column 0 does not count.
        out = g.report_checks("junk\rCHECK 1: PASS tail\n", required=[1])
        self.assertEqual((out["ok"], out["missing"], out["failed"]), (False, [1], []))
        # The brief keeps its own rule: a CR in a FILES line is a violation — the
        # ending, or an interior one that item strip() would eat into 'a.py'.
        for text in (files_line(["a.py"]).replace("\n", "\r\n"),
                     crlf_files(files_line(["a.py"])),
                     files_line(["a.py"]).replace("a.py. Do", "a.py\r. Do"),
                     "```\nsome output\n```\n" + files_line(["a.py"]).replace("\n", "\r\n")):
            with self.subTest(text=text[:24]):
                with self.assertRaises(g.GuardError):
                    g.brief_files(text)

    def test_guards_refuse_wrong_type_inputs(self):
        # (fix 2) GuardError, never AttributeError/TypeError leaking out.
        for bad in NON_TEXT:
            with self.subTest(bad=repr(bad)[:24]):
                with self.assertRaises(g.GuardError):
                    g.report_checks(bad)
                with self.assertRaises(g.GuardError):
                    g.brief_files(bad)
        for bad in NON_SEQ:
            with self.subTest(bad=repr(bad)[:24]):
                with self.assertRaises(g.GuardError):
                    g.scope_fence(bad, ["tools/a.py"])
                with self.assertRaises(g.GuardError):
                    g.scope_fence(["tools/a.py"], bad)

    def test_report_checks_refuses_a_broken_required_argument(self):
        # (fix 3) A caller argument, not a hint: ValueError (GuardError is one).
        for bad in BAD_REQUIRED:
            with self.subTest(required=repr(bad)[:24]):
                with self.assertRaises(ValueError):
                    g.report_checks(T, required=bad)
        for good in GOOD_REQUIRED:
            with self.subTest(required=repr(good)[:24]):
                g.report_checks(T, required=good)
        self.assertEqual(g.report_checks(T), g.report_checks(T, required=tuple(SIX)))

    def test_size_limits_refuse_oversized_bodies_and_paths(self):
        # (fix 4) 1 MiB of body, 200/100/20 of path.
        for fn in (g.report_checks, g.brief_files):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(g.GuardError):
                    fn(OVER_TEXT)
        self.assertEqual(g.report_checks(AT_TEXT, required=[1])["ok"], True)
        for bad in (OVER_TOTAL, OVER_COMPONENT, OVER_DEEP):
            with self.subTest(path=bad[:18]):
                self.assertIsNone(g.exact_path(bad))
                self.assertIsNone(g.norm_path(bad))
                self.assertEqual(g.scope_fence([bad], [bad]), [bad])
        for ok in (AT_TOTAL, AT_COMPONENT, AT_DEEP):
            with self.subTest(path=ok[:18]):
                self.assertEqual(g.exact_path(ok), ok)
                self.assertEqual(g.scope_fence([ok], [ok]), [])
        # A space inside a name is legal: never a rejection.
        for spaced in SPACED:
            with self.subTest(spaced=spaced):
                self.assertEqual(g.exact_path(spaced), spaced)
                self.assertEqual(g.scope_fence([spaced], [spaced]), [])
        # ... and it survives a FILES line and the fence unchanged.
        self.assertEqual(g.brief_files(files_line(["ops/host names.yml", "a b/c d.py"])),
                         ["ops/host names.yml", "a b/c d.py"])
        self.assertEqual(g.scope_fence(["ops/host names.yml"],
                                       g.brief_files(files_line(["ops/host names.yml"]))), [])

    def test_tool_preflight_uses_the_injected_lookup(self):
        seen = []

        def which(name):
            seen.append(name)
            return "/usr/bin/" + name
        self.assertEqual(g.tool_preflight(which=which), [])
        self.assertEqual(tuple(seen), g.REQUIRED_TOOLS)
        have = {"yamllint", "gitleaks"}
        self.assertEqual(g.tool_preflight(which=lambda n: n if n in have else None),
                         ["ansible-playbook", "ansible-lint", "pre-commit"])
        self.assertEqual(g.tool_preflight(required_tools=("yamllint", "x", "nope", "nope", "y"),
                                          which=lambda n: n if n in have else None),
                         ["x", "nope", "y"])
        self.assertEqual(g.tool_preflight(which=lambda n: 1 / 0), list(g.REQUIRED_TOOLS))
        p = inspect.signature(g.tool_preflight).parameters
        self.assertIs(p["which"].default, shutil.which)
        self.assertEqual(p["required_tools"].default, g.REQUIRED_TOOLS)
        for kw in (dict(which=None), dict(which="shutil.which"),
                   dict(required_tools=["a", ""]), dict(required_tools=["a", 3])):
            with self.assertRaises(g.GuardError):
                g.tool_preflight(**kw)

    def test_tool_preflight_refuses_unusable_lookup_results(self):
        # (4) a result that is not a non-empty str, a directory, or a result with
        # a NUL/newline in it is MISSING; so is a lookup that raises.
        bad = [None, "", 1, True, ["x"], {"x": 1}, "/usr/bin/yamllint\n",
               "/usr/bin\x00yamllint", str(ROOT), str(ROOT) + "/"]
        for value in bad:
            with self.subTest(value=repr(value)[:24]):
                self.assertEqual(g.tool_preflight(required_tools=("yamllint",),
                                                  which=lambda n, v=value: v), ["yamllint"])
        self.assertEqual(g.tool_preflight(required_tools=("yamllint", "gitleaks"),
                                          which=lambda n: str(ROOT) if n == "yamllint"
                                          else "/usr/bin/gitleaks"), ["yamllint"])

        def boom(name):
            raise RuntimeError("no lookup for you")
        self.assertEqual(g.tool_preflight(required_tools=("yamllint", "gitleaks"), which=boom),
                         ["yamllint", "gitleaks"])
        self.assertEqual(g.tool_preflight(required_tools=("yamllint",),
                                          which=lambda n: "/usr/bin/yamllint"), [])

    def test_requires_ops_guards_delegates_the_writer_rule(self):
        # (6) ops always; anything at R2/R3; a mislabelled docs/code card that
        # touches ops or auth paths still gets the guards.
        self.assertTrue(g.requires_ops_guards("ops"))
        self.assertTrue(g.requires_ops_guards("ops", paths=["docs/a.md"]))
        self.assertFalse(g.requires_ops_guards("docs", paths=["docs/a.md"]))
        self.assertFalse(g.requires_ops_guards("code", paths=["tools/a.py"]))
        self.assertTrue(g.requires_ops_guards("docs", paths=["ops/deploy.yml"]))
        self.assertTrue(g.requires_ops_guards("docs", paths=["tools/auth.py"]))
        self.assertTrue(g.requires_ops_guards("code", paths=["docs/a.md"],
                                              diff_text="+    password: hunter2"))
        self.assertTrue(g.requires_ops_guards("docs", r_level="R2", paths=["docs/a.md"]))
        self.assertFalse(g.requires_ops_guards("docs", r_level="R1", paths=["docs/a.md"]))
        self.assertTrue(g.requires_ops_guards(None, paths=["inventory.yml"]))
        self.assertFalse(g.requires_ops_guards(None, paths=["docs/a.md"]))
        cases = [("ops", [], ""), ("code", ["tools/a.py"], ""),
                 ("docs", ["docs/a.md"], ""), ("docs", ["ops/x.yml"], ""),
                 ("code", ["tools/auth.py"], ""), ("infra", [], ""), (None, [], "")]
        for task_type, paths, diff in cases:
            with self.subTest(task_type=task_type, paths=paths):
                level = g.required_r_level(task_type, paths, diff)
                self.assertEqual(g.requires_ops_guards(task_type, paths=paths, diff_text=diff),
                                 task_type == "ops" or g.R_LEVELS.index(level) >= 2)
        for bad in ("ops-ish", "OPS", "", " ", 7, ["ops"]):
            with self.assertRaises(ValueError):
                g.requires_ops_guards(bad)
        for bad in ("R9", "r2", 2, ""):
            with self.assertRaises(ValueError):
                g.requires_ops_guards("docs", r_level=bad)
        self.assertEqual(g.requires_ops_guards("ops", paths=["docs/a.md"],
                                               diff_text="+ password: x"), True)

    def test_preflight_report_states(self):
        ok = g.preflight_report([])
        self.assertEqual((ok["state"], ok["missing"]), ("ok", []))
        self.assertIn("yamllint", ok["message"])
        out = g.preflight_report(["gitleaks", "yamllint"])
        self.assertEqual(out["state"], "input_required")
        self.assertEqual(out["missing"], ["gitleaks", "yamllint"])
        for name in ("gitleaks", "yamllint", "input_required", g.INSTALL_HELP):
            self.assertIn(name, out["message"])
        self.assertIn("pipx install yamllint ansible-core ansible-lint pre-commit",
                      g.INSTALL_HELP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
