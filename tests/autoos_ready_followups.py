#!/usr/bin/env python3
"""Hermetic followups: temporary indexed git repositories, no runner or network."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_brief as brief  # noqa: E402
import autoos_ready_guards as g  # noqa: E402
import autoos_writer_gate as writer_gate  # noqa: E402


def files_line(paths):
    return ("# AO-1\nFILES (only these; <= 3 files, <= 200 lines changed): "
            + ", ".join(paths) + ". Do not touch other lines/files.\nGOAL: y.\n")


class ReadyFollowupTests(unittest.TestCase):

    def test_rendered_ops_files_and_report_instructions(self):
        spec = importlib.util.spec_from_file_location(
            "agent_for_ready_followups", ROOT / "tools" / "autoos-agent.py")
        agent = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = agent
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(agent)
        fields = {"task_id": "AO-1", "one_line": "fix mail",
                  "paths": ["a.yml", "b.yml", "test_x.py"],
                  "goal": "once-only mail", "invariants": "idempotent",
                  "keys_file": "keys.yml", "reference_playbook": "ref.yml",
                  "entity": "example", "state_path": "a.yml", "indent": "2",
                  "files": "a.yml", "playbooks": "b.yml", "test_files": "test_x.py"}
        for level in ("R2", "R3"):
            with self.subTest(level=level):
                rendered = brief.render_brief("ops", level, fields)
                self.assertEqual(g.brief_files(rendered), fields["paths"])
                for verdict in ("PASS", "FAIL", "INPUT_REQUIRED"):
                    self.assertIn("CHECK n: " + verdict + " <evidence>", rendered)
                self.assertIn("each check on its own line at column 0", rendered)
                self.assertIn("no list marker, bold or indentation", rendered)
                self.assertIn("output tail in a fenced block under that line", rendered)
                self.assertIn("bare verdict counts as FAIL", rendered)
                self.assertIn("CHECK n reports DONE item n", rendered)
                self.assertIn("give each check one verdict", rendered)
                self.assertIn("a second line with a different verdict fails", rendered)
                echoed = g.report_checks(rendered)
                self.assertFalse(echoed["ok"])
                self.assertEqual(echoed["missing"], list(range(1, 7)))
                for line in rendered.splitlines():
                    self.assertFalse(line.strip().startswith(("```", "~~~")))
                    if not line.startswith("REPORT:"):
                        self.assertIsNone(agent.REPORT_HEADING_RE.search(line), line)
                self.assertIn("replace n with the check number and <evidence> "
                              "with the real output", rendered)
                self.assertIn("fenced block opened with at most 3 leading spaces", rendered)
                self.assertIn("a line that itself starts with `>`", rendered)
                placeholder = "CHECK n: PASS <evidence>"
                self.assertIn(placeholder, rendered)
                start = rendered.index(placeholder)
                extracted = rendered[start:start + len(placeholder)]
                checks = [extracted.replace("CHECK n:", "CHECK %d:" % n)
                          .replace("<evidence>", "real output")
                          + "\n```text\noutput tail\n```\n" for n in range(1, 7)]
                report = "".join(checks)
                self.assertTrue(g.report_checks(report)["ok"])
                probe = g.report_checks(rendered + "\n" + "".join(checks[1:]))
                self.assertFalse(probe["ok"])
                self.assertEqual(probe["missing"], [1])

    def test_template_shaped_report_preserves_gate_refusals(self):
        report = "".join("CHECK %d: PASS evidence\n```text\noutput tail\n```\n" % n
                         for n in range(1, 7))
        result = g.report_checks(report)
        self.assertTrue(result["ok"])
        self.assertEqual(g.report_checks(report.replace("\n", "\r\n")), result)
        decorated = report.replace("CHECK 3: PASS evidence", "- **CHECK 3: PASS x**")
        with self.assertRaisesRegex(
                g.GuardError, "CHECK 3 requires CHECK 3: <PASS\\|FAIL\\|INPUT_REQUIRED> "
                "at column 0 with no list marker, bold or indentation"):
            g.report_checks(decorated)
        bare = g.report_checks(report.replace("CHECK 3: PASS evidence", "CHECK 3: PASS"))
        self.assertFalse(bare["ok"])
        self.assertIn(3, bare["failed"])

    def test_smaller_line_caps_and_pr_123(self):
        paths = ["a.py", "b.py", "CHANGELOG.md"]
        for cap in ("1", "120", "200"):
            with self.subTest(cap=cap):
                text = files_line(paths).replace("200 lines", cap + " lines")
                self.assertEqual(g.brief_files(text), paths)
        text = ("FILES (only these; <= 3 files, <= 120 lines changed): "
                "a.py, b.py, CHANGELOG.md. Do not touch other lines/files.")
        self.assertEqual(g.brief_files(text), paths)

    def test_invalid_line_caps_remain_noncanonical(self):
        for cap in ("0", "201", "0120", "+120", "1000", "", "1 20", "١٢٠"):
            with self.subTest(cap=cap):
                with self.assertRaisesRegex(g.GuardError, "non-canonical FILES line"):
                    g.brief_files(files_line(["a.py"]).replace("200 lines", cap + " lines"))

    def test_crlf_last_paragraph_and_writer_trailer(self):
        trailer = "Plan-Item: example\nWriter: omniroute/cx-gpt-6.1-sol run-example"
        for blank in ("", " ", "\t", " \t "):
            for ending in ("", "\r\n"):
                with self.subTest(blank=blank, ending=ending):
                    message = ("Body\r\n" + blank + "\r\n"
                               + trailer.replace("\n", "\r\n") + ending)
                    self.assertEqual(g.last_paragraph(message), trailer)
                    self.assertEqual(g.writer_trailer_refusal(
                        [{"sha": "abc123", "parents": 1, "message": message}]), "")
        self.assertTrue(g.writer_trailer_refusal([{
            "message": "Writer: model run\r\n \t\r\nBody without trailer"}]))

    def test_decorated_checks_raise_only_without_real_line(self):
        decorated = ["- CHECK 1: PASS tail", "* CHECK 1: PASS tail",
                     "1. CHECK 1: PASS tail", "+ CHECK 1: PASS tail",
                     "1) CHECK 1: PASS tail", "2. CHECK 1: PASS tail",
                     "12) CHECK 1: PASS tail", "   - __CHECK 1: PASS__",
                     "- **CHECK 1: PASS**", "**CHECK 1: PASS**", "__CHECK 1: PASS__"]
        for text in decorated:
            with self.subTest(text=text):
                with self.assertRaises(g.GuardError) as caught:
                    g.report_checks(text, [1])
                self.assertIn("CHECK 1 requires CHECK 1: <PASS|FAIL|INPUT_REQUIRED>",
                              str(caught.exception))
                self.assertIn("at column 0 with no list marker, bold or indentation",
                              str(caught.exception))
                for quoted in ("```\n" + text + "\n```", "> " + text, "    " + text):
                    self.assertEqual(g.report_checks(quoted, [1])["missing"], [1])
                self.assertTrue(g.report_checks(text + "\nCHECK 1: PASS real tail", [1])["ok"])
        for text in ("- CHECK 01: PASS tail", "- check 1: PASS tail"):
            self.assertEqual(g.report_checks(text, [1])["missing"], [1])

    # Real refusals take precedence over decoration diagnostics.
    def test_decorations_do_not_hide_real_refusals(self):
        for verdict, failed, need in (("FAIL", [2], []), ("INPUT_REQUIRED", [], [2])):
            with self.subTest(verdict=verdict):
                out = g.report_checks("CHECK 2: " + verdict + " tail\n- CHECK 1: PASS", [1, 2])
                self.assertEqual(out, {"ok": False, "missing": [1], "failed": failed,
                                       "input_required": need})

    def test_decorated_fail_uses_verdict_neutral_diagnostic(self):
        with self.assertRaises(g.GuardError) as caught:
            g.report_checks("- CHECK 1: FAIL", [1])
        self.assertEqual(str(caught.exception),
                         "CHECK 1 requires CHECK 1: <PASS|FAIL|INPUT_REQUIRED> "
                         "at column 0 with no list marker, bold or indentation")

    def test_crlf_writer_attribution_uses_only_trailer(self):
        message = ("Body\r\nWriter: body/model run-body\r\n \t\r\n"
                   "Plan-Item: example\r\nWriter: trailer/model run-trailer\r\n")
        self.assertEqual(list(writer_gate.writers([{"parents": 1, "message": message}], g)),
                         [("trailer/model", False)])

    def test_a_change_here_runs_the_wired_module(self):
        spec = importlib.util.spec_from_file_location(
            "affected_tests_for_followups", ROOT / "tools" / "affected-tests.py")
        affected = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = affected
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(affected)
        for path in ("tests/autoos_ready_followups.py", "tools/autoos_brief_scope.py", "tools/autoos_brief.py"):
            picked = affected.map_changed_files([path], ROOT)["pytest"]
            self.assertIn("tests/test_autoos_ready_guards.py", picked)
        spec = importlib.util.spec_from_file_location(
            "guards_for_exposure", ROOT / "tests" / "test_autoos_ready_guards.py")
        guards = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guards)
        self.assertIs(guards.BriefScopeTests, BriefScopeTests)


class BriefScopeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        for name, text in {"a.py": "def local(): pass\n",
                           "b.rs": "pub struct Shared {}\nfn work() {}\n",
                           "c.sh": "work() { :; }\n",
                           "mention.py": "# Shared work\n",
                           "types.py": "class Shared: pass\n"}.items():
            (self.root / name).write_text(text, encoding="utf-8", newline="\n")
        for args in (("init", "-q"), ("add", ".")):
            subprocess.run(["git", *args], cwd=self.root, env=env, check=True,
                           capture_output=True)
        self.fields = dict(task_id="AO-1", one_line="fix scope", paths=["a.py"],
                           goal="complete scope", invariants="safe", keys_file="keys.yml",
                           reference_playbook="ref.yml", entity="example", state_path="a.py",
                           indent="2", files="a.py", playbooks="a.py", test_files="a.py")

    def render(self, **scope):
        return brief.render_brief("ops", "R2", self.fields,
                                  scope=dict(root=str(self.root), **scope))

    def index(self, path, content):
        (self.root / path).write_bytes(content)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        subprocess.run(["git", "add", "--", path], cwd=self.root, env=env,
                       check=True, capture_output=True)

    def test_shell_definition_tails(self):
        self.index("extra.sh", b"helper\n")
        with self.assertRaisesRegex(ValueError, "helper"):
            self.render(findings=[dict(names=["helper"])])
        for line in ("    helper()", "helper() is called by the lane."):
            self.index("extra.py", (line + "\n").encode())
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, "helper"):
                self.render(findings=[dict(names=["helper"])])
        for path, line in (("extra.py", "helper() { :; }"),
                           ("extra.py", "function helper { :; }"),
                           ("extra.py", "function helper() { :; }"),
                           ("extra.sh", "helper()\n{ :; }"),
                           ("extra.bash", "helper()\n{ :; }"),
                           ("extra.sh", "function helper\n{ :; }"),
                           ("extra.bash", "function helper()\n{ :; }")):
            for old in ("extra.py", "extra.sh", "extra.bash"):
                self.index(old, b"# empty\n")
            self.index(path, (line + "\n").encode())
            with self.subTest(path=path, line=line):
                self.assertIn(path, g.brief_files(self.render(findings=[dict(names=["helper"])])))

    def test_rust_qualified_functions(self):
        for prefix in ('const', 'extern', 'extern "C"', 'pub const', 'pub extern "C"'):
            self.index("extra.rs", (prefix + " fn qualified() {}\n").encode())
            with self.subTest(prefix=prefix):
                self.assertIn("extra.rs", g.brief_files(
                    self.render(findings=[dict(names=["qualified"])])))

    def test_changelog_exemption_and_fourth_counted_file(self):
        self.fields["paths"] = ["CHANGELOG.md", "a.py"]
        self.assertEqual(g.brief_files(self.render(findings=[dict(names=["Shared"])])),
                         ["CHANGELOG.md", "a.py", "b.rs", "types.py"])
        self.fields["paths"].append("c.sh")
        with self.assertRaisesRegex(ValueError, "types.py.*Shared"):
            self.render(findings=[dict(names=["Shared"])])

    def test_bytes_and_newline_only_records(self):
        self.index("extra.py", b"\x0cdef encoded(): pass # \xe9\n")
        self.assertIn("extra.py", g.brief_files(
            self.render(findings=[dict(names=["encoded"])])))

    def test_case_exact_rulings_and_findings(self):
        self.fields["paths"] = ["A.py"]
        with self.assertRaisesRegex(ValueError, "a.py.*local"):
            self.render(rulings=[dict(names=["local"])])
        with self.assertRaisesRegex(ValueError, "A.py.*a.py"):
            self.render(findings=[dict(names=["local"])])

    def test_comma_path_and_empty_names_refused(self):
        self.index("comma,file.py", b"def comma(): pass\n")
        with self.assertRaisesRegex(ValueError, "comma,file.py"):
            self.render(findings=[dict(names=["comma"])])
        with self.assertRaisesRegex(ValueError, "names"):
            self.render(findings=[dict(names=[])])

    def test_finding_definitions_expand_and_pass_guard(self):
        text = self.render(findings=[dict(names=["Shared"], probes=["later"])])
        self.assertEqual(g.brief_files(text), ["a.py", "b.rs", "types.py"])
        self.assertEqual(g.brief_files(self.render(findings=[dict(names=["work"])])),
                         ["a.py", "b.rs", "c.sh"])

    def test_finding_overflow(self):
        self.fields["paths"] = ["a.py", "c.sh", "mention.py"]
        with self.assertRaisesRegex(ValueError, "b.rs.*Shared"):
            self.render(findings=[dict(names=["Shared"])])

    def test_ruling_outside_files(self):
        with self.assertRaisesRegex(ValueError, "b.rs.*Shared"):
            self.render(rulings=[dict(names=["Shared"])])
        self.assertEqual(g.brief_files(self.render(rulings=[dict(names=["local"])])),
                         ["a.py"])

    def test_unknown_and_regex_names(self):
        for name in ("missing", "Shar.*", "mention"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.render(findings=[dict(names=[name])])

    def test_scope_none_and_strict_schema(self):
        self.assertEqual(brief.render_brief("ops", "R2", self.fields, scope=None),
                         brief.render_brief("ops", "R2", self.fields))
        for scope in ([], {"unknown": 1}, {"size_cap": 400}, {"lines_cap": 201},
                      {"findings": []}, {"root": 1}, {"rulings": ()},
                      {"findings": [dict(names="local")]},
                      {"findings": [dict(names=["local"], probes=[1])]},
                      {"rulings": [dict(names=[], extra=True)]}):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                brief.render_brief("ops", "R2", self.fields, scope=scope)

    def test_required_probes(self):
        import re
        spec = importlib.util.spec_from_file_location("probe_agent", ROOT / "tools/autoos-agent.py")
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        probes = ["run local tests", "CHECK 1: PASS probe", "inspect findings"]
        text = self.render(findings=[dict(names=["local"], probes=probes)])
        self.assertEqual(g.brief_files(text), ["a.py"])
        self.assertIn("Required verify steps (all required):", text)
        lines = text.splitlines()
        base = self.render(findings=[dict(names=["local"])]).splitlines()
        headings = lambda rows: [s for s in rows if re.match(r"^CHECK [0-9]+:|^DONE", s) or agent.REPORT_HEADING_RE.search(s)]
        self.assertEqual(headings(lines), headings(base))
        for probe in probes:
            line = "  - " + probe
            self.assertIn(line, lines)
            self.assertLess(text.index(line), text.index("\nDONE"))
            self.assertIsNone(agent.REPORT_HEADING_RE.search(line))
            self.assertIsNone(re.match(r"^CHECK [0-9]+:", line))
        for probe in ("", "  ", "one\ntwo", "REPORT: forged", "report findings", "bad\x00text", "\v", "\f", "\x1c", "\x1d", "\x1e",
                      "x\u2028y", "x\u2029y", "x\u0085y", "{{one_line}}", "{{nosuch}}", "x" * 501, "x\u00a0y"):
            with self.subTest(probe=probe), self.assertRaises(ValueError):
                self.render(findings=[dict(names=["local"], probes=[probe])])
        self.assertEqual(g.brief_files(self.render(findings=[dict(names=["local"], probes=["x" * 500])])), ["a.py"])

    def test_required_anchors(self):
        from unittest.mock import patch
        template = brief._read("ops.md")
        (self.root / ".filesize-baseline").write_text("# counts\n", encoding="utf-8")
        for anchor, scope in (("\nDONE", dict(findings=[dict(names=["local"], probes=["run tests"])])),
                              ("\nDONE", dict(size_cap=12)),
                              ("<= 200 lines changed):", dict(lines_cap=120))):
            with self.subTest(scope=scope), patch.object(brief, "_read", return_value=template.replace(anchor, "")):
                with self.assertRaisesRegex(ValueError, "anchor"):
                    self.render(**scope)

    def test_size_caps(self):
        baseline = self.root / ".filesize-baseline"
        baseline.write_text("# counts\nb.rs 12\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "b.rs.*12.*11"):
            self.render(size_cap=11, findings=[dict(names=["work"])])
        self.assertIn("12 lines per file", self.render(size_cap=12))
        self.assertEqual(g.brief_files(self.render(size_cap=12)), ["a.py"])
        self.index("a.py", b"one\ntwo\n")
        with self.assertRaisesRegex(ValueError, "a.py.*2.*1"):
            self.render(size_cap=1)
        self.fields.update(paths=["new.py"], files="new.py", playbooks="new.py",
                           test_files="new.py", state_path="new.py")
        self.render(size_cap=1)
        baseline.unlink()
        with self.assertRaisesRegex(ValueError, "baseline"):
            self.render(size_cap=1)
        baseline.mkdir()
        with self.assertRaisesRegex(ValueError, "baseline"):
            self.render(size_cap=1)

    def test_lines_caps(self):
        for cap in (1, 120, 200):
            text = self.render(lines_cap=cap)
            self.assertIn("<= %d lines changed" % cap, text)
            self.assertEqual(g.brief_files(text), ["a.py"])
        for key, values in (("lines_cap", (0, 201, True, "120")),
                            ("size_cap", (True, "400"))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.render(**{key: value})

    def test_isolated_import_root_and_git_config(self):
        code = ("import ast, importlib.util, sys; s = importlib.util.spec_from_file_location('brief', sys.argv[1]); "
                "m = importlib.util.module_from_spec(s); s.loader.exec_module(m); sys.stdout.reconfigure(encoding='utf-8'); "
                "print(m.render_brief('ops', 'R2', ast.literal_eval(sys.argv[2]), scope=ast.literal_eval(sys.argv[3])))")
        result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "tools/autoos_brief.py"),
                repr(self.fields), repr(dict(root=str(self.root), findings=[dict(names=['local'], probes=['run local tests'])]))], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("  - run local tests\n", result.stdout)
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("isolated_brief", ROOT / "tools/autoos_brief.py")
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", []), patch.dict(sys.modules):
            sys.modules.pop("autoos_brief_scope", None)
            spec.loader.exec_module(module)
            module.render_brief("ops", "R2", self.fields, scope={"root": str(self.root)})
        child = self.root / "child"
        child.mkdir()
        with self.assertRaisesRegex(ValueError, "top.level"):
            brief.render_brief("ops", "R2", self.fields, scope={"root": str(child)})
        for key in ("grep.lineNumber", "grep.column", "grep.fullName"):
            subprocess.run(["git", "config", key, "true"], cwd=self.root, check=True)
        self.assertEqual(g.brief_files(self.render(findings=[dict(names=["local"])])), ["a.py"])

    def test_scope_none_golden(self):
        import hashlib
        self.assertEqual(hashlib.sha256(brief.render_brief("ops", "R2", self.fields, scope=None).encode()).hexdigest(),
                         "236a1ae067286b402628ff9b624b803dfeea5183084f39051b6b97a936b1fb08")
        expected = (ROOT / "templates/briefs/ops.md").read_bytes()
        for key, value in self.fields.items():
            shown = ", ".join(value) if type(value) is list else value
            if key == "invariants":
                shown = "  - " + shown
            expected = expected.replace(("{{" + key + "}}").encode(), shown.encode())
        self.assertEqual(brief.render_brief("ops", "R2", self.fields, scope=None).encode(), expected)


if __name__ == "__main__":
    unittest.main()
