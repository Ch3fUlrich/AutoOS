#!/usr/bin/env python3
"""More Windows prompt spawn tests, with real temp files and no mocks.

Complements test_win_prompt_spawn.py. Every case writes the files it talks
about into a temp tree, passes the shim's full path to
resolve_client_executable (so shutil.which needs no patching), and runs the
Windows code path with _nt=True. Covers: shims that name a shell by absolute
path, inline-code flags on the line above the real script, value flags, the
whole-line length limit with separators, Windows path tokenization, resolved
targets above the limit, PATH lookup, interpreter names without .exe,
hazards in the last argument, relative script paths, .mjs/.cjs targets, and
comment lines that carry a script name.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.append(TOOLS)

agent = importlib.import_module("autoos-agent")
resolve_client_executable = agent.resolve_client_executable
parse_shim_target = agent.parse_shim_target
ClientMissing = agent.ClientMissing

# Newlines plus cmd.exe metacharacters: an unresolvable shim must refuse this.
PROMPT = "line one\nline two with %VAR% and & symbols"


def _mk(path: str, content: str = "") -> str:
    """Write a real file, executable so POSIX which() can see it too."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    return path


class WinShimMoreTests(unittest.TestCase):
    """Shim resolution with _nt=True against real files in a temp tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        self.app = os.path.join(self.tmp, "app")
        for d in (self.bin, self.app):
            os.makedirs(d)

    def _shim(self, name: str, content: str) -> str:
        return _mk(os.path.join(self.bin, name), content)

    def _run(self, shim: str, *args: str) -> list:
        return resolve_client_executable([shim] + list(args), _nt=True)

    def _assert_missing(self, shim: str, *args: str) -> None:
        with self.assertRaises(ClientMissing, msg=shim):
            self._run(shim, *args)

    def test_shell_named_exe_skipped_and_real_target_wins(self):
        """cmd.exe/powershell.exe named by absolute path never becomes the target."""
        real_exe = _mk(os.path.join(self.bin, "real.exe"), "fake")
        for shell, call, sname in (("cmd.exe", "/c", "cmdwrap"),
                                   ("powershell.exe", "-File", "pswrap")):
            fake_shell = _mk(os.path.join(self.tmp, "sys", shell), "fake")
            shim = self._shim(f"{sname}.cmd", f'"{fake_shell}" {call} "%~dp0real.exe" %*\n')
            self.assertEqual(parse_shim_target(shim), (os.path.normpath(real_exe), []), sname)
            self.assertEqual(self._run(shim, "run", PROMPT),
                             [os.path.normpath(real_exe), "run", PROMPT], sname)

    def test_shell_only_shim_refuses_multiline_prompt(self):
        cmd_exe = _mk(os.path.join(self.tmp, "sys", "cmd.exe"), "fake")
        ps_exe = _mk(os.path.join(self.tmp, "sys", "powershell.exe"), "fake")
        _mk(os.path.join(self.bin, "script.ps1"), "fake")
        # both shims are .cmd: a bare full-path .ps1 does not survive
        # shutil.which() when .PS1 is not in PATHEXT, which would test the
        # wrong layer
        shim_cmd = self._shim("cmdonly.cmd", f'"{cmd_exe}" /c echo hello %*\n')
        shim_ps = self._shim("psonly.cmd", f'"{ps_exe}" -File "%~dp0script.ps1" %*\n')
        for shim in (shim_cmd, shim_ps):
            self.assertIsNone(parse_shim_target(shim))
            self._assert_missing(shim, "run", PROMPT)
            # a short prompt without hazards still passes through the shim
            self.assertEqual(self._run(shim, "clean"), [shim, "clean"])

    def test_inline_code_line_before_script_line_refused(self):
        """The first line's inline-code flag must refuse even when the next line has the script."""
        _mk(os.path.join(self.bin, "cli.js"), "// js")
        firsts = ['@"node" -e "1"', '@"node" --eval "1"', '@"node" -p "1"',
                  '@"node" --print "1"', '@"python" -c "1"', '@"python" -m x',
                  '@"node" -pe "1"']
        for i, first in enumerate(firsts):
            shim = self._shim(f"il{i}.cmd", first + '\n' + '@"node" "%~dp0cli.js" %*\n')
            self.assertIsNone(parse_shim_target(shim), first)
            self._assert_missing(shim, "run", PROMPT)

    def test_value_flags_keep_value_in_order_before_script(self):
        cli_js = _mk(os.path.join(self.bin, "cli.js"), "// js")
        node_exe = _mk(os.path.join(self.bin, "node.exe"), "fake")
        cases = [
            ('--import ./hook.mjs', ["--import", "./hook.mjs"]),
            ('--loader x', ["--loader", "x"]),
            ('--experimental-loader x', ["--experimental-loader", "x"]),
            ('--env-file .env', ["--env-file", ".env"]),
            ('-W ignore', ["-W", "ignore"]),
            ('-Xdev', ["-Xdev"]),
            ('-rhook.js', ["-rhook.js"]),
        ]
        for i, (flag_part, expected) in enumerate(cases):
            shim = self._shim(f"vf{i}.cmd", f'@"node" {flag_part} "%~dp0cli.js" %*\n')
            self.assertEqual(self._run(shim, "run", PROMPT),
                             [os.path.normpath(node_exe)] + expected
                             + [os.path.normpath(cli_js), "run", PROMPT], flag_part)

    def test_whole_line_limit_counts_separators(self):
        """3500+3500 plus the separating space is 7001 and must be refused."""
        shim = self._shim("bare.cmd", "@echo off\necho nothing\n")
        self._assert_missing(shim, "x" * 7001)
        self._assert_missing(shim, "a" * 3500, "b" * 3500)
        self.assertEqual(self._run(shim, "x" * 7000), [shim, "x" * 7000])
        self.assertEqual(self._run(shim, "a" * 3500, "b" * 3499),
                         [shim, "a" * 3500, "b" * 3499])

    def test_unquoted_and_quoted_paths_resolve(self):
        if os.name != "nt":
            raise unittest.SkipTest("unquoted absolute path tokenization is a Windows concern")
        node_exe = _mk(os.path.join(self.bin, "node.exe"), "fake")
        cli_js = _mk(os.path.join(self.app, "cli.js"), "// js")
        shim_uq = self._shim("uq.cmd", '@"node" %s %%*\n' % cli_js)
        self.assertEqual(self._run(shim_uq, "run", PROMPT),
                         [os.path.normpath(node_exe), os.path.normpath(cli_js), "run", PROMPT])
        spaced = _mk(os.path.join(self.tmp, "prog files", "x", "cli.js"), "// js")
        shim_q = self._shim("q.cmd", '@"node" "%s" %%*\n' % spaced)
        self.assertEqual(self._run(shim_q, "run", PROMPT),
                         [os.path.normpath(node_exe), os.path.normpath(spaced), "run", PROMPT])

    def test_unterminated_quote_fails_closed(self):
        _mk(os.path.join(self.app, "cli.js"), "// js")
        shim = self._shim("bad.cmd", '@"node" "%s %%*\n' % os.path.join(self.app, "cli.js"))
        self.assertIsNone(parse_shim_target(shim))
        self._assert_missing(shim, "run", PROMPT)

    def test_resolved_targets_exempt_from_line_limit(self):
        long_arg = "y" * 10000
        real_exe = _mk(os.path.join(self.bin, "real.exe"), "fake")
        shim_exe = self._shim("exes.cmd", '@"%~dp0real.exe" %%*\n')
        self.assertEqual(self._run(shim_exe, long_arg), [os.path.normpath(real_exe), long_arg])
        tool_py = _mk(os.path.join(self.bin, "tool.py"), "# py")
        py_exe = _mk(os.path.join(self.bin, "python.exe"), "fake")
        shim_py = self._shim("pys.cmd", '@python.exe "%~dp0tool.py" %%*\n')
        self.assertEqual(self._run(shim_py, long_arg),
                         [os.path.normpath(py_exe), os.path.normpath(tool_py), long_arg])

    def test_node_found_on_path_when_no_sibling(self):
        if os.name != "nt":
            raise unittest.SkipTest("PATHEXT lookup of node.exe is Windows-only")
        _mk(os.path.join(self.bin, "cli.js"), "// js")
        shim = self._shim("tool.cmd", '@"node" "%~dp0cli.js" %*\n')
        node_dir = os.path.join(self.tmp, "nodedir")
        os.makedirs(node_dir)
        node_exe = _mk(os.path.join(node_dir, "node.exe"), "fake")
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = self.bin + os.pathsep + node_dir
            res = self._run(shim, "run", PROMPT)
            # which() may hand back the PATHEXT spelling (node.EXE): compare
            # case-insensitively, but keep the real location
            self.assertEqual(os.path.normcase(res[0]), os.path.normcase(node_exe))
            self.assertEqual(res[1], os.path.normpath(os.path.join(self.bin, "cli.js")))
            self.assertEqual(res[2:], ["run", PROMPT])
        finally:
            os.environ["PATH"] = old

    def test_interpreter_names_without_exe_suffix(self):
        tool_py = _mk(os.path.join(self.bin, "tool.py"), "# py")
        py_bin = _mk(os.path.join(self.bin, "py"), "fake")
        shim_py = self._shim("pytool.cmd", '@"py" "%~dp0tool.py" %*\n')
        self.assertEqual(self._run(shim_py, "run", PROMPT),
                         [os.path.normpath(py_bin), os.path.normpath(tool_py), "run", PROMPT])
        cli_js = _mk(os.path.join(self.bin, "cli.js"), "// js")
        nodejs_bin = _mk(os.path.join(self.bin, "nodejs"), "fake")
        shim_nj = self._shim("nodejs.cmd", '@"nodejs" "%~dp0cli.js" %*\n')
        self.assertEqual(self._run(shim_nj, "run", PROMPT),
                         [os.path.normpath(nodejs_bin), os.path.normpath(cli_js), "run", PROMPT])
        tool2 = _mk(os.path.join(self.bin, "t2.py"), "# py")
        pw_bin = _mk(os.path.join(self.bin, "pythonw"), "fake")
        shim_pw = self._shim("pythonw.cmd", '@"pythonw" "%~dp0t2.py" %*\n')
        self.assertEqual(self._run(shim_pw, "run", PROMPT),
                         [os.path.normpath(pw_bin), os.path.normpath(tool2), "run", PROMPT])

    def test_hazard_in_last_of_three_arguments_refused(self):
        shim = self._shim("bare.cmd", "@echo off\n")
        self._assert_missing(shim, "one", "two", "third\nline")

    def test_relative_script_path_joined_to_shim_dir(self):
        node_exe = _mk(os.path.join(self.bin, "node.exe"), "fake")
        cli_js = _mk(os.path.join(self.bin, "rel.js"), "// js")
        shim = self._shim("rel.cmd", '@"node" rel.js %%*\n')
        self.assertEqual(self._run(shim, "run", PROMPT),
                         [os.path.normpath(node_exe), os.path.normpath(cli_js), "run", PROMPT])

    def test_mjs_and_cjs_scripts_resolve(self):
        node_exe = _mk(os.path.join(self.bin, "node.exe"), "fake")
        for ext in ("mjs", "cjs"):
            script = _mk(os.path.join(self.bin, "app." + ext), "// js")
            shim = self._shim(f"s{ext}.cmd", f'@"node" app.{ext} %*\n')
            self.assertEqual(self._run(shim, "run", PROMPT),
                             [os.path.normpath(node_exe), os.path.normpath(script), "run", PROMPT])

    def test_comment_lines_do_not_take_script_name_as_target(self):
        _mk(os.path.join(self.bin, "real.exe"), "fake")
        for i, comment in enumerate(("rem real.exe", ":: real.exe",
                                     "@rem real.exe", "# real.exe")):
            shim = self._shim(f"cmt{i}.cmd", comment + "\n")
            self.assertIsNone(parse_shim_target(shim), comment)
            self._assert_missing(shim, "run", PROMPT)

    def test_hazard_in_first_and_middle_arguments(self):
        bare = self._shim("bare_h.cmd", "@echo off\n")
        real_exe = _mk(os.path.join(self.bin, "real.exe"), "fake")
        resolved = self._shim("res_h.cmd", '@"%~dp0real.exe" %*\n')
        norm_real = os.path.normpath(real_exe)
        # Hazard in first argument, clean last argument
        self._assert_missing(bare, "first\nline", "two", "three")
        self.assertEqual(self._run(resolved, "first\nline", "two", "three"),
                         [norm_real, "first\nline", "two", "three"])
        # Hazard in middle argument, clean last argument
        self._assert_missing(bare, "one", "middle\nline", "three")
        self.assertEqual(self._run(resolved, "one", "middle\nline", "three"),
                         [norm_real, "one", "middle\nline", "three"])
        # Resolved target passes through hazard in last argument byte-exact
        self.assertEqual(self._run(resolved, "one", "two", "third\nline"),
                         [norm_real, "one", "two", "third\nline"])


class WinShimShellSkipTests(unittest.TestCase):
    """Verify all 10 shell executable names are skipped in shim resolution."""

    SHELL_NAMES = (
        "cmd", "powershell", "pwsh", "bash", "sh", "zsh",
        "wsl", "conhost", "wscript", "cscript",
    )
    LEGIT_EXES = (
        "cmdx.exe", "mycmd.exe", "sh2.exe", "bashful.exe", "powershell-core.exe",
    )

    @classmethod
    def setUpClass(cls):
        cls.space_dir = tempfile.mkdtemp(prefix="shim dir ")
        cls.real_exe = _mk(os.path.join(cls.space_dir, "real.exe"), "fake")
        for name in cls.SHELL_NAMES:
            for c_name in (
                name.lower(),
                name.upper(),
                "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(name)),
            ):
                _mk(os.path.join(cls.space_dir, c_name + ".exe"), "")
                _mk(os.path.join(cls.space_dir, c_name), "")
        for legit in cls.LEGIT_EXES:
            _mk(os.path.join(cls.space_dir, legit), "")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.space_dir, True)

    def test_all_ten_shells_skipped_with_real_target(self):
        norm_real = os.path.normpath(self.real_exe)
        for name in self.SHELL_NAMES:
            cases = (
                name.lower(),
                name.upper(),
                "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(name)),
            )
            for c_name in cases:
                for suffix in (".exe", ""):
                    for pos in ("first", "non_first"):
                        for line_layout in ("first_line", "second_line_after", "second_line_before"):
                            with self.subTest(shell=name, case=c_name, suffix=suffix, pos=pos, line=line_layout):
                                shell_path = os.path.join(self.space_dir, c_name + suffix)
                                prefix = "" if pos == "first" else "echo "
                                s_line = f'{prefix}"{shell_path}"\n'
                                r_line = '"%~dp0real.exe" %*\n'
                                if line_layout == "first_line":
                                    content = s_line + r_line
                                elif line_layout == "second_line_after":
                                    content = "@echo off\n" + s_line + r_line
                                else:
                                    content = r_line + s_line
                                shim = os.path.join(self.space_dir, "test_target.cmd")
                                _mk(shim, content)
                                parsed = parse_shim_target(shim)
                                self.assertIsNotNone(parsed)
                                self.assertEqual(parsed[0], norm_real)
                                res = resolve_client_executable([shim, "run", PROMPT], _nt=True)
                                self.assertEqual(res[0], norm_real)
                                self.assertNotEqual(res[0], os.path.normpath(shell_path))
                                self.assertEqual(res[1:], ["run", PROMPT])

    def test_all_ten_shells_without_real_target_refused(self):
        for name in self.SHELL_NAMES:
            cases = (
                name.lower(),
                name.upper(),
                "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(name)),
            )
            for c_name in cases:
                for suffix in (".exe", ""):
                    for pos in ("first", "non_first"):
                        for line_layout in ("first_line", "second_line"):
                            with self.subTest(shell=name, case=c_name, suffix=suffix, pos=pos, line=line_layout):
                                shell_path = os.path.join(self.space_dir, c_name + suffix)
                                prefix = "" if pos == "first" else "echo "
                                s_line = f'{prefix}"{shell_path}"\n'
                                content = s_line if line_layout == "first_line" else "@echo off\n" + s_line
                                shim = os.path.join(self.space_dir, "test_refuse.cmd")
                                _mk(shim, content)
                                self.assertIsNone(parse_shim_target(shim))
                                with self.assertRaises(ClientMissing):
                                    resolve_client_executable([shim, "run", PROMPT], _nt=True)

    def test_legitimate_similar_names_not_skipped(self):
        for legit in self.LEGIT_EXES:
            with self.subTest(legit=legit):
                legit_path = os.path.join(self.space_dir, legit)
                shim = os.path.join(self.space_dir, f"shim_{legit}.cmd")
                _mk(shim, f'"{legit_path}" %*\n')
                norm_legit = os.path.normpath(legit_path)
                parsed = parse_shim_target(shim)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed[0], norm_legit)
                res = resolve_client_executable([shim, "run", PROMPT], _nt=True)
                self.assertEqual(res[0], norm_legit)
                self.assertEqual(res[1:], ["run", PROMPT])


if __name__ == "__main__":
    unittest.main()
