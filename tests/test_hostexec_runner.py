"""Tests for tools/hostexec/runner.py (spec status/L1-backlog.spec-host-shell.md
section 4 item 3). Pure stdlib, no I/O beyond a temp dir and real /bin//usr/bin
binaries (sleep, bash) for the process-group kill test. Written before the
implementation (coding-principles 2).

Run from anywhere:

    python3 tests/test_hostexec_runner.py
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from hostexec import runner  # noqa: E402

_REAL_BIN_DIRS = ["/usr/bin", "/bin"]


def _write_script(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BasicExecTests(unittest.TestCase):
    def test_runs_and_captures_stdout_with_exit_code_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = runner.run_local(["echo", "-n", "hello"], cwd=tmp,
                                       path_dirs=_REAL_BIN_DIRS, timeout=5)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.output, "hello")
            self.assertFalse(result.timed_out)
            self.assertFalse(result.truncated)
            self.assertGreaterEqual(result.duration_ms, 0)

    def test_child_stdin_is_devnull(self):
        # K/Qoder-11: the child must not inherit the broker's stdin.
        import subprocess as _sp
        seen: dict = {}
        orig_popen = _sp.Popen

        class _FakeStdout:
            def fileno(self):
                return 999999  # invalid -> os.read raises OSError -> EOF

            def close(self):
                pass

        class _FakeProc:
            pid = 123456
            stdout = _FakeStdout()  # type: ignore[assignment]
            returncode = 0

            def wait(self, timeout=None):
                return 0

        def _capture(*a, **k):
            seen.update(k)
            return _FakeProc()  # type: ignore[return-value]

        _sp.Popen = _capture  # type: ignore[assignment]
        try:
            import os as _os
            orig_set_blocking = _os.set_blocking
            _os.set_blocking = lambda *a, **k: None  # type: ignore[assignment]
            try:
                runner.run_local(["cat"], cwd="/tmp",
                                 path_dirs=_REAL_BIN_DIRS, timeout=1)
            finally:
                _os.set_blocking = orig_set_blocking
        finally:
            _sp.Popen = orig_popen
        self.assertIs(seen.get("stdin"), _sp.DEVNULL,
                      f"child stdin must be DEVNULL, got {seen.get('stdin')!r}")

    def test_nonzero_exit_code_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = runner.run_local(["sh", "-c", "exit 7"], cwd=tmp,
                                       path_dirs=_REAL_BIN_DIRS, timeout=5)
            # NOTE: policy.py's no-inline-shell rule would refuse this argv
            # long before runner.py ever sees it -- runner is a separate,
            # lower-level unit that just executes whatever argv it is given.
            self.assertEqual(result.exit_code, 7)

    def test_shell_is_never_invoked_argv_is_passed_literally(self):
        with tempfile.TemporaryDirectory() as tmp:
            # shell=False: this whole string is ONE literal argv element to
            # /bin/echo. If a shell were involved, $HOME would expand and
            # the semicolon would start a second command.
            result = runner.run_local(
                ["echo", "-n", "$HOME; touch /tmp/hostexec-test-should-not-exist"],
                cwd=tmp, path_dirs=_REAL_BIN_DIRS, timeout=5)
            self.assertEqual(result.output, "$HOME; touch /tmp/hostexec-test-should-not-exist")
            self.assertFalse(os.path.exists("/tmp/hostexec-test-should-not-exist"))


class FixedEnvTests(unittest.TestCase):
    def test_path_comes_from_policy_dirs_not_the_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "dumpenv")
            _write_script(script, f"#!{sys.executable}\n"
                                   "import os\n"
                                   "print(os.environ.get('PATH', '<unset>'))\n")
            result = runner.run_local([script], cwd=tmp, path_dirs=["/only/this/dir"],
                                       timeout=5, base_env={"PATH": "/should/not/appear"})
            self.assertEqual(result.output.strip(), "/only/this/dir")

    def test_ld_preload_is_never_inherited(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "dumpenv")
            _write_script(script, f"#!{sys.executable}\n"
                                   "import os\n"
                                   "print(os.environ.get('LD_PRELOAD', '<unset>'))\n")
            poisoned = dict(os.environ)
            poisoned["LD_PRELOAD"] = "/tmp/evil.so"
            result = runner.run_local([script], cwd=tmp, path_dirs=_REAL_BIN_DIRS,
                                       timeout=5, base_env=poisoned)
            self.assertEqual(result.output.strip(), "<unset>")

    def test_sudo_askpass_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "dumpenv")
            _write_script(script, f"#!{sys.executable}\n"
                                   "import os\n"
                                   "print(os.environ.get('SUDO_ASKPASS', '<unset>'))\n")
            poisoned = dict(os.environ)
            poisoned["SUDO_ASKPASS"] = "/tmp/fake-askpass"
            result = runner.run_local([script], cwd=tmp, path_dirs=_REAL_BIN_DIRS,
                                       timeout=5, base_env=poisoned)
            self.assertEqual(result.output.strip(), "<unset>")


class OutputCapTests(unittest.TestCase):
    def test_output_is_capped_and_truncated_is_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "chatty")
            # a single small write (well under the OS pipe buffer) so the
            # child exits immediately regardless of whether we ever drain it
            _write_script(script, f"#!{sys.executable}\n"
                                   "import sys\n"
                                   "sys.stdout.write('A' * 5000)\n")
            result = runner.run_local([script], cwd=tmp, path_dirs=_REAL_BIN_DIRS,
                                       timeout=5, output_cap=1000)
            self.assertTrue(result.truncated)
            self.assertEqual(len(result.output), 1000)
            self.assertEqual(result.out_bytes, 1000)
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)


class TimeoutKillsTheGroupTests(unittest.TestCase):
    def test_timeout_kills_the_whole_process_group_including_grandchildren(self):
        with tempfile.TemporaryDirectory() as tmp:
            self_pidfile = os.path.join(tmp, "self.pid")
            child_pidfile = os.path.join(tmp, "child.pid")
            script = os.path.join(tmp, "spawner.sh")
            _write_script(script, "#!/bin/bash\n"
                                   f'echo $$ > "{self_pidfile}"\n'
                                   "sleep 30 &\n"
                                   f'echo $! > "{child_pidfile}"\n'
                                   "wait\n")
            result = runner.run_local([script], cwd=tmp, path_dirs=_REAL_BIN_DIRS,
                                       timeout=0.5, output_cap=4096)
            self.assertTrue(result.timed_out)
            self.assertIsNone(result.exit_code)

            # give the kernel a moment to finish reaping the killed group
            deadline = time.monotonic() + 5
            self_pid = child_pid = None
            while time.monotonic() < deadline:
                if os.path.exists(child_pidfile) and os.path.exists(self_pidfile):
                    with open(self_pidfile, encoding="utf-8") as fh:
                        self_pid = int(fh.read().strip())
                    with open(child_pidfile, encoding="utf-8") as fh:
                        child_pid = int(fh.read().strip())
                    break
                time.sleep(0.05)
            self.assertIsNotNone(child_pid, "the spawner script never wrote its child's pid")

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and (_pid_alive(self_pid) or _pid_alive(child_pid)):
                time.sleep(0.05)
            self.assertFalse(_pid_alive(self_pid), "the spawner script itself is still alive")
            self.assertFalse(_pid_alive(child_pid), "the grandchild sleep is still alive "
                                                      "-- the group kill did not reach it")


class RemoteSshTests(unittest.TestCase):
    """A fake `ssh` stub on the fixed PATH stands in for the real thing: it
    re-splits its own last argument with shlex and echoes each token back
    on its own line, so the test can confirm a metacharacter-laden argv
    element arrives on the "remote" side as one literal argument."""

    def _fake_ssh_bindir(self, tmp: str) -> str:
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir, exist_ok=True)
        _write_script(os.path.join(bindir, "ssh"), f"#!{sys.executable}\n"
                      "import sys, shlex\n"
                      "remote_cmd = sys.argv[-1]\n"
                      "for tok in shlex.split(remote_cmd):\n"
                      "    print(tok)\n")
        return bindir

    def test_metachar_arguments_round_trip_as_one_literal_arg_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = self._fake_ssh_bindir(tmp)
            argv = ["echo", "a b", "$(evil)", "; rm -rf /", 'quote"in', "back`tick`", "a|b>c<d"]
            result = runner.run_remote(argv, alias="myhost", cwd=tmp, path_dirs=[bindir], timeout=5)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(result.output.splitlines(), argv)

    def test_ssh_invoked_with_batchmode_and_dash_capital_t(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = os.path.join(tmp, "bin")
            os.makedirs(bindir)
            marker = os.path.join(tmp, "argv.txt")
            _write_script(os.path.join(bindir, "ssh"), f"#!{sys.executable}\n"
                          "import sys\n"
                          f"open({marker!r}, 'w').write(repr(sys.argv[1:]))\n")
            runner.run_remote(["id"], alias="myhost", cwd=tmp, path_dirs=[bindir], timeout=5)
            with open(marker, encoding="utf-8") as fh:
                seen = fh.read()
            self.assertIn("BatchMode=yes", seen)
            self.assertIn("-T", seen)
            self.assertIn("myhost", seen)
            self.assertIn("--", seen)


if __name__ == "__main__":
    unittest.main()
