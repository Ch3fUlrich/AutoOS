#!/usr/bin/env python3
"""Tests for Windows prompt spawn and shim resolution."""
from __future__ import annotations

import contextlib
import importlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.append(TOOLS)

agent = importlib.import_module("autoos-agent")
mcp_server = importlib.import_module("autoos_agent_mcp")

resolve_client_executable = agent.resolve_client_executable
parse_shim_target = agent.parse_shim_target
_has_shim_hazards = agent._has_shim_hazards
client_program_index = agent.client_program_index
ClientMissing = agent.ClientMissing

PROMPT = (
    "Line 1: AutoOS prompt start\nLine 2: Variables like %PATH% and ^escape characters\n"
    "Line 3: TOKEN_LINE_3_SECRET_KEY\nLine 4: Shell symbols & and | and <stdin> >stdout and \"double\" 'single' quotes\n"
    "Line 5: End of prompt task instructions"
)


@contextlib.contextmanager
def restricted_path(new_path: str):
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = new_path
    try: yield
    finally: os.environ["PATH"] = old


@contextlib.contextmanager
def prepended_path(prefix: str):
    old_p, old_ext = os.environ.get("PATH", ""), os.environ.get("PATHEXT", "")
    os.environ["PATH"] = prefix + os.pathsep + old_p
    if ".PS1" not in old_ext.upper(): os.environ["PATHEXT"] = old_ext + ";.PS1"
    try: yield
    finally:
        os.environ["PATH"], os.environ["PATHEXT"] = old_p, old_ext


# verbatim copy of the function at the base commit; update only when the base changes
def _reference_resolve_client_executable_main(cmd) -> list:
    """`cmd` with the client's program replaced by what `shutil.which()` found.

    WINSHIM (reported by Workstation-AutoOS): on Windows a client installed as a
    `.cmd`/`.ps1` shim is on PATH — so the spawner's own which() pre-check calls it
    installed — while `CreateProcess` appends only `.exe` and Popen of the bare
    name raised FileNotFoundError [WinError 2]. which() honours PATHEXT, so it
    finds the shim and Popen is handed its full path. `shell=True` is not the
    answer: it would put argv quoting and an injection surface behind every spawn.
    On POSIX which() returns the file execvp would have picked, so a client that
    works today is untouched. Every launch site goes through here — `run_client`
    (the first attempt and each fallthrough re-run) and the MCP runner's detached
    `run` — so one rule decides what starts, and a program that is not there is
    named, with the PATH that was searched, instead of traced back.
    """
    argv = list(cmd)
    index = client_program_index(argv)
    name = argv[index]
    exe = shutil.which(name)
    if exe is None:
        raise ClientMissing(
            "not installed: %s (PATH %s). Install it (catalog: ./setup.sh --only "
            "<id> -y; on Windows the directory holding the .cmd/.ps1 shim has to "
            "be on PATH); see: list" % (name, os.environ.get("PATH", "")))
    argv[index] = exe
    return argv


class TestWinPromptResolverHermetic(unittest.TestCase):
    """Hermetic unit tests for resolve_client_executable with real fake files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _file(self, rel: str, content: str = "") -> str:
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_hermetic_cmd_with_node_target_resolves_to_node_and_script(self):
        script = self._file(f"node_modules{os.sep}mytool{os.sep}index.js", "// node\n")
        shim = self._file("mytool.cmd", f'@"%_prog%" "%dp0%{os.sep}node_modules{os.sep}mytool{os.sep}index.js" %*\n')
        fake_node = self._file("node.exe", "node")
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: fake_node if "node" in n else (shim if n == "mytool" else None)):
            self.assertEqual(resolve_client_executable(["mytool", "run", PROMPT], _nt=True), [fake_node, os.path.normpath(script), "run", PROMPT])
        empty_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_dir, True)
        with restricted_path(empty_dir), mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: shim if n == "mytool" else None):
            self.assertEqual(resolve_client_executable(["mytool", "run", PROMPT], _nt=True), [fake_node, os.path.normpath(script), "run", PROMPT])

    def test_hermetic_cmd_with_python_target_resolves_to_python_and_script(self):
        script = self._file(f"scripts{os.sep}tool.py", "# py\n")
        shim = self._file("pytool.cmd", f'@"{sys.executable}" "%dp0%{os.sep}scripts{os.sep}tool.py" %*\n')
        with mock.patch.object(agent.shutil, "which", return_value=shim):
            self.assertEqual(resolve_client_executable(["pytool", "run", PROMPT], _nt=True), [sys.executable, os.path.normpath(script), "run", PROMPT])

    def test_hermetic_cmd_with_sibling_opencode_exe_resolves_to_exe(self):
        self._file("script.js", "// js\n")
        shim = self._file("opencode.cmd", f'@"node" "%dp0%{os.sep}script.js" %*\n')
        opencode_exe = self._file(f"node_modules{os.sep}@opencode{os.sep}cli{os.sep}bin{os.sep}opencode.exe", "bin")
        fake_node = self._file("node.exe", "node")
        with mock.patch.object(agent.shutil, "which", return_value=shim):
            self.assertEqual(resolve_client_executable(["opencode", "run", PROMPT], _nt=True), [os.path.normpath(opencode_exe), "run", PROMPT])
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: fake_node if "node" in n else (shim if n == "opencode" else None)):
            self.assertEqual(resolve_client_executable(["opencode", "run", PROMPT], _nt=True), [os.path.normpath(opencode_exe), "run", PROMPT])

    def test_resolution_order_sibling_node_before_path_node(self):
        script = self._file("index.js", "// js\n")
        shim = self._file("mytool.cmd", f'@"node" "%dp0%{os.sep}index.js" %*\n')
        sibling_node = self._file("node.exe", "sibling")
        path_node = self._file(f"other{os.sep}node.exe", "path")
        with restricted_path(os.path.dirname(path_node)), mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: path_node if "node" in n else (shim if n == "mytool" else None)):
            res = resolve_client_executable(["mytool", "run", PROMPT], _nt=True)
        self.assertEqual(res[0], os.path.normpath(sibling_node))
        self.assertEqual(res[1], os.path.normpath(script))

    def test_hermetic_exe_untouched_even_with_hazard_prompt(self):
        fake_exe = self._file("custom.exe", "bin")
        with mock.patch.object(agent.shutil, "which", return_value=fake_exe):
            self.assertEqual(resolve_client_executable(["custom", "run", PROMPT], _nt=True), [fake_exe, "run", PROMPT])

    def test_hermetic_cmd_without_resolvable_target_refuses_hazard_prompt(self):
        shim = self._file("unresolvable.cmd", "@echo off\necho no target\n")
        with mock.patch.object(agent.shutil, "which", return_value=shim):
            with self.assertRaises(agent.ClientMissing) as caught:
                resolve_client_executable(["unresolvable", "run", PROMPT], _nt=True)
            self.assertIn("cannot spawn unresolvable on Windows", str(caught.exception))
            self.assertIn("prompt contains newlines or special characters", str(caught.exception))
            self.assertEqual(resolve_client_executable(["unresolvable", "run", "clean task"], _nt=True), [shim, "run", "clean task"])

    def test_ps1_shim_with_resolvable_target_and_refuse_path(self):
        script = self._file("index.js", "// js\n")
        fake_node = self._file("node.exe", "node")
        ps1_target = self._file("worker.ps1", '& "node" --no-warnings "$basedir/index.js" $args\n')
        self.assertEqual(parse_shim_target(ps1_target), (os.path.normpath(script), ["--no-warnings"]))
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: fake_node if "node" in n else (ps1_target if n == "worker" else None)):
            self.assertEqual(resolve_client_executable(["worker", "run", PROMPT], _nt=True), [fake_node, "--no-warnings", os.path.normpath(script), "run", PROMPT])
        bare_ps1 = self._file("bare.ps1", 'Write-Host "bare shim"\n')
        self.assertIsNone(parse_shim_target(bare_ps1))
        with mock.patch.object(agent.shutil, "which", return_value=bare_ps1):
            with self.assertRaises(agent.ClientMissing):
                resolve_client_executable(["bare", "run", PROMPT], _nt=True)

    def test_prompt_length_boundary_7000_passes_7001_refused(self):
        bare_shim, fake_exe = self._file("bare.cmd", "@echo off\n"), self._file("custom.exe", "bin")
        self.assertFalse(_has_shim_hazards("x" * 7000))
        self.assertTrue(_has_shim_hazards("x" * 7001))
        with mock.patch.object(agent.shutil, "which", return_value=bare_shim):
            self.assertEqual(resolve_client_executable(["bare", "x" * 7000], _nt=True), [bare_shim, "x" * 7000])
            with self.assertRaises(agent.ClientMissing):
                resolve_client_executable(["bare", "x" * 7001], _nt=True)
            with self.assertRaises(agent.ClientMissing):
                resolve_client_executable(["bare"] + ["a" * 1500] * 5, _nt=True)
            self.assertEqual(resolve_client_executable(["bare"] + ["a" * 1000] * 5, _nt=True), [bare_shim] + ["a" * 1000] * 5)
        with mock.patch.object(agent.shutil, "which", return_value=fake_exe):
            self.assertEqual(resolve_client_executable(["custom", "x" * 7001], _nt=True), [fake_exe, "x" * 7001])

    def test_resolved_target_and_posix_not_subject_to_length_limit(self):
        script = self._file("index.js", "// js\n")
        fake_node = self._file("node.exe", "node")
        shim = self._file("tool.cmd", f'@"node" "%dp0%{os.sep}index.js" %*\n')
        fake_exe = self._file("custom.exe", "bin")
        bare_shim = self._file("bare.cmd", "@echo off\n")
        long_args = ["x" * 2000] * 5
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: fake_node if "node" in n else (shim if n == "tool" else None)):
            self.assertEqual(resolve_client_executable(["tool"] + long_args, _nt=True), [fake_node, os.path.normpath(script)] + long_args)
        with mock.patch.object(agent.shutil, "which", return_value=fake_exe):
            self.assertEqual(resolve_client_executable(["custom"] + long_args, _nt=True), [fake_exe] + long_args)
        with mock.patch.object(agent.shutil, "which", return_value=bare_shim):
            self.assertEqual(resolve_client_executable(["bare"] + long_args, _nt=False), [bare_shim] + long_args)

    def test_py_launcher_selector_preserved_and_resolved(self):
        script = self._file("tool.py", "# py\n")
        py_shim = self._file("pytool.cmd", f'@"py.exe" -3 -u "%dp0%{os.sep}tool.py" %*\n')
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: py_shim if n == "pytool" else "C:\\Windows\\py.exe"):
            res = resolve_client_executable(["pytool", "run", PROMPT], _nt=True)
        self.assertTrue(res[0].lower().endswith(("py.exe", "py")))
        self.assertEqual(res[1:], ["-3", "-u", os.path.normpath(script), "run", PROMPT])

    def test_interpreter_pattern_resolves_all_variants_to_sibling(self):
        for interp_name, sname, c in [("python3.12.exe", "s1.py", "# py\n"), ("pythonw.exe", "s2.py", "# py\n"),
                                      ("py.exe", "s3.py", "# py\n"), ("nodejs.exe", "s4.js", "// js\n"), ("node.exe", "s5.js", "// js\n")]:
            interp_file = self._file(interp_name, "bin")
            script_file = self._file(sname, c)
            shim = self._file(f"t_{interp_name}.cmd", f'@"{interp_name}" "%dp0%{os.sep}{sname}" %*\n')
            with mock.patch.object(agent.shutil, "which", return_value=shim):
                res = resolve_client_executable([f"t_{interp_name}", "run", PROMPT], _nt=True)
            self.assertEqual(res[0], os.path.normpath(interp_file))
            self.assertEqual(res[1], os.path.normpath(script_file))

    def test_value_taking_interpreter_flags(self):
        cli_js, hook_js = self._file("cli.js", "// js\n"), self._file("hook.js", "// hook\n")
        fake_node = self._file("node.exe", "node")
        spaced_hook = self._file(f"Program Files{os.sep}custom{os.sep}hook.js", "// h\n")
        for sname, line, exp_args in [
            ("s_req", f'@"node" --require "%dp0%{os.sep}hook.js" "%dp0%{os.sep}cli.js" %*\n', ["--require", os.path.normpath(hook_js)]),
            ("s_r", f'@"node" -r "%dp0%{os.sep}hook.js" "%dp0%{os.sep}cli.js" %*\n', ["-r", os.path.normpath(hook_js)]),
            ("s_sp", f'@"node" --require="{spaced_hook}" "%dp0%{os.sep}cli.js" %*\n', [f"--require={spaced_hook}"]),
        ]:
            shim = self._file(f"{sname}.cmd", line)
            with mock.patch.object(agent.shutil, "which", side_effect=lambda n, s=shim, sn=sname, **k: fake_node if "node" in n else (s if n == sn else None)):
                self.assertEqual(resolve_client_executable([sname, "run", PROMPT], _nt=True), [fake_node] + exp_args + [os.path.normpath(cli_js), "run", PROMPT])
        cli_py = self._file("cli.py", "# py\n")
        shim_x = self._file("s_x.cmd", f'@"{sys.executable}" -X dev "%dp0%{os.sep}cli.py" %*\n')
        with mock.patch.object(agent.shutil, "which", return_value=shim_x):
            self.assertEqual(resolve_client_executable(["s_x", "run", PROMPT], _nt=True), [sys.executable, "-X", "dev", os.path.normpath(cli_py), "run", PROMPT])

    def test_refusal_of_inline_code_and_module_shims(self):
        cases = [("node_e.cmd", '@"node" -e "1" %*\n'), ("node_eval.cmd", '@"node" --eval "1" %*\n'),
                 ("node_p.cmd", '@"node" -p "1" %*\n'), ("py_c.cmd", f'@"{sys.executable}" -c "1" %*\n'),
                 ("py_m.cmd", f'@"{sys.executable}" -m unittest %*\n')]
        for sname, content in cases:
            shim_path = self._file(sname, content)
            self.assertIsNone(parse_shim_target(shim_path))
            with mock.patch.object(agent.shutil, "which", return_value=shim_path):
                with self.assertRaises(agent.ClientMissing):
                    resolve_client_executable([sname, "run", PROMPT], _nt=True)

    def test_quoted_script_path_with_spaces_and_unquoted_fail_closed(self):
        script, fake_node = self._file(f"my tools{os.sep}index.js", "// js\n"), self._file("node.exe", "node")
        shim_q = self._file("tool_q.cmd", f'@"node" --no-warnings "%dp0%{os.sep}my tools{os.sep}index.js" %*\n')
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: fake_node if "node" in n else (shim_q if n == "tool_q" else None)):
            self.assertEqual(resolve_client_executable(["tool_q", "run", PROMPT], _nt=True), [fake_node, "--no-warnings", os.path.normpath(script), "run", PROMPT])
        shim_uq = self._file("tool_uq.cmd", f'@"node" --no-warnings %dp0%{os.sep}my tools{os.sep}index.js %*\n')
        with mock.patch.object(agent.shutil, "which", return_value=shim_uq):
            with self.assertRaises(agent.ClientMissing):
                resolve_client_executable(["tool_uq", "run", PROMPT], _nt=True)

    def test_comment_lines_in_cmd_skipped(self):
        script, fake_node = self._file("cli.js", "// js\n"), self._file("node.exe", "node")
        cmd_content = ":: Comment 1\nREM Comment 2\nrem comment 3\n@rem comment 4\n@REM comment 5\n" + f'@"node" "%dp0%{os.sep}cli.js" %*\n'
        shim = self._file("commented.cmd", cmd_content)
        self.assertEqual(parse_shim_target(shim), (os.path.normpath(script), []))
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: fake_node if "node" in n else (shim if n == "commented" else None)):
            self.assertEqual(resolve_client_executable(["commented", "run", PROMPT], _nt=True), [fake_node, os.path.normpath(script), "run", PROMPT])

    def test_each_hazard_class_alone_for_bare_shim(self):
        bare_shim = self._file("bare.cmd", "@echo off\n")
        with mock.patch.object(agent.shutil, "which", return_value=bare_shim):
            for bad in ['"', "'", "%", "&", "^", "|", "<", ">", "\n", "\r", "\n\n", "\r\n", "\r\n\r\n", "line1\r\nline2"]:
                sample = f"arg_with_{bad}_hazard" if len(bad) == 1 else bad
                self.assertTrue(_has_shim_hazards(sample))
                with self.assertRaises(agent.ClientMissing):
                    resolve_client_executable(["bare", sample], _nt=True)
            safe_sample = "safe-arg_123 without any shell metacharacters"
            self.assertFalse(_has_shim_hazards(safe_sample))
            self.assertEqual(resolve_client_executable(["bare", safe_sample], _nt=True), [bare_shim, safe_sample])

    def test_hermetic_posix_preserves_byte_identical_behavior(self):
        shim = self._file("mytool.cmd", "@echo off\n")
        with mock.patch.object(agent.shutil, "which", return_value=shim):
            self.assertEqual(resolve_client_executable(["mytool", "run", PROMPT], _nt=False), [shim, "run", PROMPT])
        with mock.patch.object(agent.shutil, "which", side_effect=lambda n, **k: f"/usr/bin/{n}"):
            self.assertEqual(resolve_client_executable(["python3", "x.cmd", "a"], _nt=False), ["/usr/bin/python3", "x.cmd", "a"])


class TestPosixEquivalenceWithMain(unittest.TestCase):
    """Assert POSIX mode leaves all inputs byte-identical to base commit behavior."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sp_dir = os.path.join(self.tmp, "dir with spaces")
        os.makedirs(self.sp_dir, exist_ok=True)
        bins = [
            (self.tmp, ["tool1.exe", "tool2.exe", "tool3.exe", "python3.exe", "git.exe", "node.exe",
                        "app.exe", "runner.exe", "opencode.cmd", "x.bat", "y.ps1", "helper.cmd",
                        "unicöde.exe", "unicode_日本語.exe"]),
            (self.sp_dir, ["spaced_tool.exe", "spaced shim.cmd"]),
        ]
        for folder, names in bins:
            for name in names:
                p = os.path.join(folder, name)
                open(p, "w", encoding="utf-8").close()
                if os.name != "nt":  # which() needs the exec bit on POSIX; Windows uses the extension
                    try:
                        pathlib.Path(p).chmod(0o755)
                    except OSError:
                        pass

    def test_posix_equivalence_60_inputs(self):
        d, sp = self.tmp, self.sp_dir
        inputs = [
            ["tool1"], ["tool1", "run"], ["tool2", "arg1", "arg2"], ["tool3", "--flag", "val"], ["git", "status"], ["node", "-v"],
            [os.path.join(d, "tool1.exe")], [os.path.join(d, "tool1.exe"), "build"], [os.path.join(d, "tool2.exe"), "--verbose"],
            [os.path.join(d, "tool3.exe"), "task", "start"], [os.path.join(d, "python3.exe"), "-c", "print(1)"], [os.path.join(d, "node.exe"), "index.js"],
            [os.path.join(sp, "spaced_tool.exe")], [os.path.join(sp, "spaced_tool.exe"), "run"], [os.path.join(sp, "spaced_tool.exe"), "arg with spaces", "another"],
            [os.path.join(sp, "spaced shim.cmd")], [os.path.join(sp, "spaced shim.cmd"), "task"], ["tool1", "path with spaces/file.txt"],
            ["tool1.exe"], ["tool1.exe", "run", "fast"], ["tool2.exe", "--opt=1"], ["tool3.exe"], ["app.exe", "start"], ["runner.exe", "job"],
            ["nonexistent_cli_123"], ["nonexistent_cli_123", "arg"], ["missing_tool_xyz", "--flag"], [os.path.join(d, "does_not_exist.exe")], ["no_such_binary"], ["bogus_cmd", "x", "y"],
            ["tool1", "run", "line1\nline2"], ["tool2", "--prompt", "Hello\r\nWorld"], ["tool1", "task", "first\nsecond\nthird\nfourth"],
            ["python3", "script.py", "A\nB\nC"], ["tool1", PROMPT], ["opencode.cmd", "run", "Prompt line 1\nPrompt line 2"],
            ["python3", "x.cmd", "a"], ["python3", "x.cmd", "a", "b"], ["python3", "y.ps1", "foo"], ["python3", "helper.cmd", "bar"],
            ["opencode.cmd"], ["opencode.cmd", "run"], ["opencode.cmd", "run", "clean prompt"], ["x.bat"], ["x.bat", "test"], ["y.ps1"], ["y.ps1", "-ExecutionPolicy", "Bypass"], ["helper.cmd", "do"],
            [], ["tool1", ""], ["tool2", "", ""], ["app.exe", ""], ["x.bat", ""], ["nonexistent_cli_123", ""],
            ["tool1", "こんにちは"], ["tool2", "arg_äöü_€_🚀"], ["tool1", "привет мир"], ["unicöde.exe", "café"], ["unicode_日本語.exe", "テスト"], ["tool1", "Unicode prompt: 🚀\nLine 2: 🌟"],
        ]
        self.assertEqual(len(inputs), 60)
        def _exec(fn, *a, **k):
            try: return (True, fn(*a, **k))
            except Exception as e: return (False, (type(e), str(e)))
        with prepended_path(self.tmp):
            for cmd in inputs:
                self.assertEqual(_exec(resolve_client_executable, cmd, _nt=False),
                                 _exec(_reference_resolve_client_executable_main, cmd), f"Mismatch for {cmd}")


class TestWinPromptSpawnUnmocked(unittest.TestCase):
    """Unmocked Windows spawn tests asserting multi-line prompt delivery through shims."""

    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("Windows-only unmocked spawn test")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "out.txt")
        self.worker_py = os.path.join(self.tmp, "worker.py")
        with open(self.worker_py, "w", encoding="utf-8") as f:
            f.write("import sys\nwith open(r'%s', 'w', encoding='utf-8') as f:\n    f.write(sys.argv[1] if len(sys.argv) > 1 else '')\n" % self.out)
        self.worker_cmd = os.path.join(self.tmp, "worker.cmd")
        with open(self.worker_cmd, "w", encoding="utf-8") as f:
            f.write(f'@"{sys.executable}" "{self.worker_py}" %*\n')
        self.agent_runner = os.path.join(self.tmp, "agent_runner.py")
        with open(self.agent_runner, "w", encoding="utf-8") as f:
            f.write("import sys, os, importlib\nsys.path.insert(0, r'%s')\nagent = importlib.import_module('autoos-agent')\n"
                    "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
                    "sys.exit(int(agent.run_client(['worker', prompt], cwd=os.getcwd(), env=dict(os.environ))))\n" % TOOLS)
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = self.tmp + os.pathsep + self.old_path

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_received_exact(self):
        with open(self.out, "r", encoding="utf-8") as f:
            received = f.read()
        self.assertEqual(received, PROMPT)
        self.assertIn("TOKEN_LINE_3_SECRET_KEY", received)
        self.assertIn('Shell symbols & and | and <stdin> >stdout and "double" \'single\' quotes', received)

    def test_run_client_first_attempt_receives_multiline_prompt_byte_exact(self):
        rc = agent.run_client(["worker", PROMPT], cwd=self.tmp, env=os.environ.copy())
        self.assertEqual(int(rc), 0)
        self._assert_received_exact()

    def test_run_client_fallthrough_rerun_receives_multiline_prompt_byte_exact(self):
        rc = agent.run_client(["worker", PROMPT], cwd=self.tmp, env=os.environ.copy(), attempt=2)
        self.assertEqual(int(rc), 0)
        self._assert_received_exact()

    def test_mcp_detached_run_receives_multiline_prompt_byte_exact(self):
        run_dir = os.path.join(self.tmp, "runs", "20261003-test-mcp-detached")
        os.makedirs(run_dir, exist_ok=True)
        mcp_server._write_json(os.path.join(run_dir, "job.json"), {"id": "20261003-test-mcp-detached", "cwd": self.tmp, "argv": [PROMPT]})
        old_agent = mcp_server.AGENT
        mcp_server.AGENT = self.agent_runner
        try:
            rc = mcp_server.run_job(run_dir)
            self.assertEqual(rc, 0)
        finally:
            mcp_server.AGENT = old_agent
        self._assert_received_exact()

    def test_control_cmd_direct_truncates_at_first_newline(self):
        p = subprocess.Popen([self.worker_cmd, PROMPT])
        p.wait()
        with open(self.out, "r", encoding="utf-8") as f:
            received = f.read()
        self.assertEqual(received, PROMPT.splitlines()[0])
        self.assertNotIn("TOKEN_LINE_3_SECRET_KEY", received)
        self.assertNotEqual(received, PROMPT)


if __name__ == "__main__":
    unittest.main()
