#!/usr/bin/env python3
"""Tests for tools/autoos_ready_guards.py (AO-WRITER-GUARDS P4a).

Hermetic (D-852): no runner, no filesystem, no network — diff paths are
arguments, the tool lookup is injected, the brief is a real render."""
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
FENCE = [(["Tools/A.PY", "./tools/a.py", "tools/./a.py"], ["tools/a.py"], []),
         (PATHS, PATHS, []), (["tools/a.py", "tests/test_a.py"], PATHS, []),
         (["tools/a.py", "tools/c.py"], PATHS, ["tools/c.py"]),
         (["tools/gone.py", "tools/a.py"], PATHS, ["tools/gone.py"]),
         (["docs/x.md"], ["docs"], ["docs/x.md"]),
         (["tools/a.py"], ["tools/a\u200b.py"], ["tools/a.py"]),
         (PATHS, [], PATHS), ([], ["tools/a.py"], [])]
# (report text, required, (ok, missing, failed, input_required)).
CHECKS = [(report(ALL_OK), SIX, (True, [], [], [])),
          ("", SIX, (False, SIX, [], [])),
          ("I ran everything, trust me.\n", SIX, (False, SIX, [], [])),
          (report([(1, "PASS"), (2, "FAIL"), (3, "PASS"), (5, "INPUT_REQUIRED"),
                   (6, "PASS")]), SIX, (False, [4], [2], [5])),
          (report([(1, "INPUT_REQUIRED")]), [1], (False, [], [], [1])),
          (report([(1, "PASS")] * 2), [1], (True, [], [], [])),
          (report(ALL_OK + [(7, "FAIL"), (99, "PASS")]), SIX, (True, [], [], [])),
          ("CHECK 01: PASS tail\n", [1], (True, [], [], [])),
          ("check 1: PASS tail\n", [1], (False, [1], [], []))]
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


class ReadyGuardsTests(unittest.TestCase):
    def test_brief_files_reads_the_rendered_ops_brief(self):
        self.assertTrue(issubclass(g.GuardError, ValueError))
        self.assertEqual(g.brief_files(rendered()), PATHS)
        self.assertEqual(g.brief_files(files_line(["./Tools/A.PY"])), ["tools/a.py"])

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

    def test_report_checks_table(self):
        got = [tuple(g.report_checks(t, required=r)[k]
                    for k in ("ok", "missing", "failed", "input_required"))
               for t, r, _ in CHECKS]
        self.assertEqual(got, [w for _, _, w in CHECKS])

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
