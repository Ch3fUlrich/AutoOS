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
from autoos_ready_report_followups import ReadyReportTests


def files_line(paths):
    return ("# AO-1\nFILES (only these; <= 3 files, <= 200 lines changed): "
            + ", ".join(paths) + ". Do not touch other lines/files.\nGOAL: y.\n")


class ReadyFollowupTests(ReadyReportTests, unittest.TestCase):

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
                brief.check_brief_fields("ops", level, fields)
                rendered = (ROOT / "templates/briefs/ops.md").read_text(encoding="utf-8")
                for key, value in fields.items():
                    shown = ", ".join(value) if type(value) is list else value
                    if key == "invariants":
                        shown = "  - " + shown
                    rendered = rendered.replace("{{" + key + "}}", shown)
                if level == "R3":
                    rendered = rendered.rstrip("\n") + "\n" + (ROOT / "templates/briefs/ops-r3-auth.md").read_text(encoding="utf-8")
                self.assertEqual(g.brief_files(rendered), fields["paths"])
                echoed = g.report_checks(rendered)
                self.assertFalse(echoed["ok"])
                self.assertEqual(echoed["missing"], list(range(1, 7)))
                for line in rendered.splitlines():
                    self.assertFalse(line.strip().startswith(("```", "~~~")))
                    if not line.startswith("REPORT:"):
                        self.assertIsNone(agent.REPORT_HEADING_RE.search(line), line)
                placeholder = "CHECK n: PASS <evidence>"
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
        for path in ("tests/autoos_ready_followups.py", "tests/autoos_ready_report_followups.py",
                     "tools/autoos_brief_scope.py", "tools/autoos_brief.py"):
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
        return brief.check_brief_fields("ops", "R2", self.fields,
                                  scope=dict(root=str(self.root), **scope))

    def expand(self, **scope):
        spec = importlib.util.spec_from_file_location("brief_scope", ROOT / "tools/autoos_brief_scope.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.expand_scope(dict(root=str(self.root), **scope),
                                   self.fields["paths"], brief._validate_paths)

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
                self.assertIn(path, self.expand(findings=[dict(names=["helper"])]))

    def test_rust_qualified_functions(self):
        for prefix in ('const', 'extern', 'extern "C"', 'pub const', 'pub extern "C"'):
            self.index("extra.rs", (prefix + " fn qualified() {}\n").encode())
            with self.subTest(prefix=prefix):
                self.assertIn("extra.rs", self.expand(findings=[dict(names=["qualified"])]))

    def test_changelog_exemption_and_fourth_counted_file(self):
        self.fields["paths"] = ["CHANGELOG.md", "a.py"]
        self.assertEqual(self.expand(findings=[dict(names=["Shared"])]),
                         ["CHANGELOG.md", "a.py", "b.rs", "types.py"])
        self.fields["paths"].append("c.sh")
        with self.assertRaisesRegex(ValueError, "types.py.*Shared"):
            self.render(findings=[dict(names=["Shared"])])

    def test_bytes_and_newline_only_records(self):
        self.index("extra.py", b"\x0cdef encoded(): pass # \xe9\n")
        self.assertIn("extra.py", self.expand(findings=[dict(names=["encoded"])]))

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
        self.render(findings=[dict(names=["Shared"], probes=["later"])])
        self.assertEqual(self.expand(findings=[dict(names=["Shared"], probes=["later"])]), ["a.py", "b.rs", "types.py"])
        self.assertEqual(self.expand(findings=[dict(names=["work"])]),
                         ["a.py", "b.rs", "c.sh"])

    def test_finding_overflow(self):
        self.fields["paths"] = ["a.py", "c.sh", "mention.py"]
        with self.assertRaisesRegex(ValueError, "b.rs.*Shared"):
            self.render(findings=[dict(names=["Shared"])])

    def test_ruling_outside_files(self):
        with self.assertRaisesRegex(ValueError, "b.rs.*Shared"):
            self.render(rulings=[dict(names=["Shared"])])
        self.assertEqual(self.expand(rulings=[dict(names=["local"])]),
                         ["a.py"])

    def test_unknown_and_regex_names(self):
        for name in ("missing", "Shar.*", "mention"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.render(findings=[dict(names=[name])])

    def test_scope_none_and_strict_schema(self):
        self.assertEqual(brief.check_brief_fields("ops", "R2", self.fields, scope=None),
                         brief.check_brief_fields("ops", "R2", self.fields))
        for scope in ([], {"unknown": 1}, {"size_cap": 400}, {"lines_cap": 201},
                      {"findings": []}, {"root": 1}, {"rulings": ()},
                      {"findings": [dict(names="local")]},
                      {"findings": [dict(names=["local"], probes=[1])]},
                      {"rulings": [dict(names=[], extra=True)]}):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                brief.check_brief_fields("ops", "R2", self.fields, scope=scope)

    def test_required_probes(self):
        probes = ["run local tests", "CHECK 1: PASS probe", "inspect findings"]
        self.render(findings=[dict(names=["local"], probes=probes)])
        for probe in ("", "  ", "one\ntwo", "REPORT: forged", "report findings", "bad\x00text", "\v", "\f", "\x1c", "\x1d", "\x1e",
                      "x\u2028y", "x\u2029y", "x\u0085y", "{{one_line}}", "{{nosuch}}", "x" * 501, "x\u00a0y"):
            with self.subTest(probe=probe), self.assertRaises(ValueError):
                self.render(findings=[dict(names=["local"], probes=[probe])])
        self.assertEqual(self.expand(findings=[dict(names=["local"], probes=["x" * 500])]), ["a.py"])

    def test_size_caps(self):
        baseline = self.root / ".filesize-baseline"
        baseline.write_text("# counts\nb.rs 12\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "b.rs.*12.*11"):
            self.render(size_cap=11, findings=[dict(names=["work"])])
        self.render(size_cap=12)
        self.assertEqual(self.expand(size_cap=12), ["a.py"])
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
            self.render(lines_cap=cap)
        for key, values in (("lines_cap", (0, 201, True, "120")),
                            ("size_cap", (True, "400"))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.render(**{key: value})

    def test_isolated_import_root_and_git_config(self):
        code = ("import ast, importlib.util, sys; s = importlib.util.spec_from_file_location('brief', sys.argv[1]); "
                "m = importlib.util.module_from_spec(s); s.loader.exec_module(m); sys.stdout.reconfigure(encoding='utf-8'); "
                "print(m.check_brief_fields('ops', 'R2', ast.literal_eval(sys.argv[2]), scope=ast.literal_eval(sys.argv[3])))")
        result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "tools/autoos_brief.py"),
                repr(self.fields), repr(dict(root=str(self.root), findings=[dict(names=['local'], probes=['run local tests'])]))], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("isolated_brief", ROOT / "tools/autoos_brief.py")
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", []), patch.dict(sys.modules):
            sys.modules.pop("autoos_brief_scope", None)
            spec.loader.exec_module(module)
            module.check_brief_fields("ops", "R2", self.fields, scope={"root": str(self.root)})
        child = self.root / "child"
        child.mkdir()
        with self.assertRaisesRegex(ValueError, "top.level"):
            brief.check_brief_fields("ops", "R2", self.fields, scope={"root": str(child)})
        for key in ("grep.lineNumber", "grep.column", "grep.fullName"):
            subprocess.run(["git", "config", key, "true"], cwd=self.root, check=True)
        self.assertEqual(self.expand(findings=[dict(names=["local"])]), ["a.py"])



if __name__ == "__main__":
    unittest.main()
