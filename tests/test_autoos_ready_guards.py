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
