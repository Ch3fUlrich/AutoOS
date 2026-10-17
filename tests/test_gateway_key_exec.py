#!/usr/bin/env python3
"""KEYEXEC (D-370): the `exec` subcommand of tools/autoos_gateway_key.py.

`exec` resolves the OmniRoute client key with the ONE `resolve` precedence and
then runs `-- <cmd> [args...]` with AUTOOS_OMNIROUTE_KEY set in the CHILD's
environment only: the key never appears on stdout, on stderr, in argv, or in
any file.

Every case runs the CLI in a subprocess (sys.executable + the tool path) with
a temp HOME, a temp keys file passed EXPLICITLY and every other AUTOOS_*
variable scrubbed - this suite never reads configuration/api-keys.yml, never
touches ~/.config/autoos or ~/.omniroute, never uses the network. A <cmd> that
cannot be executed must report ONE `cannot execute` line and exit 127 (not
found) / 126 (not executable) - no traceback, no environment, no key.

Run directly:

    python3 tests/test_gateway_key_exec.py
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent.parent / "tools"
TOOL = TOOLS / "autoos_gateway_key.py"
sys.path.insert(0, str(TOOLS))

# Loopback gateway (AUTOOS_OMNIROUTE_URL scrubbed -> unset -> local) reads the
# field `omniroute_<host>`; HOST here fixes the host name so the field is
# deterministic and no hostname notice prints.
HOST = "execws"
FIELD = "omniroute_" + HOST
FAKE_KEY = "fake-exec-key-DO-NOT-LEAK-0123456789abcdef"

# The child prints ONLY whether AUTOOS_OMNIROUTE_KEY is set and its LENGTH -
# never the value itself, so a correct run says `set <len>` and a leak would
# show up as the fake key in the captured output.
CHILD_REPORT = (
    "import os, sys\n"
    "v = os.environ.get('AUTOOS_OMNIROUTE_KEY')\n"
    "sys.stdout.write(('set ' + str(len(v))) if v else 'unset')\n"
)


class ExecCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.keyed = root / "keyed.yml"
        self.keyed.write_text(FIELD + ": " + FAKE_KEY + "\n", encoding="utf-8")
        self.keyless = root / "keyless.yml"
        self.keyless.write_text("unrelated: not-a-key\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        """Run the CLI with a temp HOME and the keys file passed explicitly.

        Asserts on every run that the fake key never reached stdout or stderr.
        """
        env = dict(os.environ)
        for name in list(env):
            if name.startswith("AUTOOS_"):
                env.pop(name)  # no inherited key/URL/host state from this box
        tmp = self.tmp.name
        env["HOME"] = tmp
        env["USERPROFILE"] = tmp
        env["XDG_CONFIG_HOME"] = str(Path(tmp) / ".config")
        env["LOCALAPPDATA"] = str(Path(tmp) / "AppData" / "Local")
        env["AUTOOS_HOST_NAME"] = HOST
        env["AUTOOS_HOST_CONFIG"] = str(Path(tmp) / "no-such-host.yml")
        r = subprocess.run([sys.executable, str(TOOL), *args], env=env,
                           capture_output=True, text=True)
        combined = r.stdout + r.stderr
        self.assertNotIn(FAKE_KEY, combined, "the key leaked into captured output")
        return r

    def test_child_sees_the_key_set_to_its_length_and_the_key_is_never_printed(self):
        r = self.cli("exec", str(self.keyed), "--", sys.executable, "-c", CHILD_REPORT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "set %d" % len(FAKE_KEY))

    def test_the_child_exit_code_is_the_parent_exit_code(self):
        r = self.cli("exec", str(self.keyed), "--",
                     sys.executable, "-c", CHILD_REPORT + "sys.exit(7)\n")
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "set %d" % len(FAKE_KEY))

    def test_missing_key_exits_1_with_the_existing_message(self):
        r = self.cli("exec", str(self.keyless), "--",
                     sys.executable, "-c", CHILD_REPORT)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn(FIELD, r.stderr)  # the resolve subcommand's own message

    def test_optional_runs_the_child_without_the_variable(self):
        r = self.cli("exec", "--optional", str(self.keyless), "--",
                     sys.executable, "-c", CHILD_REPORT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "unset")

    def test_missing_dash_dash_is_usage_on_stderr_exit_2(self):
        r = self.cli("exec", str(self.keyed), sys.executable, "-c", CHILD_REPORT)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("usage:", r.stderr)

    def test_empty_command_after_dash_dash_is_usage_on_stderr_exit_2(self):
        r = self.cli("exec", str(self.keyed), "--")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("usage:", r.stderr)

    def test_missing_command_is_exit_127_one_cannot_execute_line_no_traceback(self):
        """A `<cmd>` that cannot run is data, not a crash (shell convention: 127)."""
        missing = "no-such-command-autoos-exec-8f21"
        r = self.cli("exec", str(self.keyed), "--", missing)
        self.assertEqual(r.returncode, 127, r.stdout + r.stderr)
        self.assertIn("cannot execute", r.stderr)
        self.assertIn(missing, r.stderr)  # names the command that failed
        self.assertNotIn("Traceback", r.stderr)  # no Python traceback
        self.assertNotIn("Traceback", r.stdout)
        self.assertEqual(len([ln for ln in r.stderr.splitlines() if ln.strip()]), 1,
                         r.stderr)  # exactly one line, not a wall of diagnostics
        self.assertEqual(r.stdout, "")
        self.assertNotIn(FAKE_KEY, r.stdout + r.stderr)  # and never the key


class PosixBranchTests(unittest.TestCase):
    """A Windows runner never reaches os.execvpe: pin that branch's contract in-process."""

    def test_execvpe_gets_the_key_in_env_only_never_in_argv(self):
        import autoos_gateway_key as gk
        seen = {}

        def fake_execvpe(file, args, env):
            seen["file"] = file
            seen["args"] = list(args)
            seen["env"] = dict(env)

        argv = ["exec", "--", sys.executable, "-c", CHILD_REPORT]
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_KEY": FAKE_KEY}):
            with patch.object(os, "execvpe", fake_execvpe):
                with patch.object(os, "name", "posix"):
                    rc = gk.main(argv)
        self.assertEqual(rc, 0)
        self.assertEqual(seen["file"], sys.executable)
        self.assertNotIn(FAKE_KEY, seen["args"])  # the key is never in argv
        self.assertEqual(seen["env"]["AUTOOS_OMNIROUTE_KEY"], FAKE_KEY)

    def test_execvpe_refusing_the_command_is_one_line_exit_126_no_env(self):
        """PermissionError on exec (found but not executable) -> 126, one stderr line."""
        import autoos_gateway_key as gk

        def denied(file, args, env):
            raise PermissionError(13, "Permission denied")

        # No keys file argument on purpose: Path() dispatches on os.name, and the
        # branch below is artificial-POSIX, so path parsing must not run here.
        argv = ["exec", "--", "unrunnable-tool", "--flag"]
        err = io.StringIO()
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_KEY": FAKE_KEY}):
            with patch.object(os, "execvpe", denied):
                with patch.object(os, "name", "posix"):
                    with contextlib.redirect_stderr(err):
                        rc = gk.main(argv)
        self.assertEqual(rc, 126, err.getvalue())
        text = err.getvalue()
        self.assertIn(
            "autoos_gateway_key: cannot execute unrunnable-tool: Permission denied", text
        )
        self.assertNotIn("Traceback", text)
        self.assertNotIn(FAKE_KEY, text)  # never the key
        self.assertNotIn("AUTOOS_OMNIROUTE_KEY", text)  # never the environment


if __name__ == "__main__":
    unittest.main(verbosity=2)