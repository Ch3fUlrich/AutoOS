"""Current-user ownership checks, including an unelevated, read-only ACL proof."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import oc_l1_password


class WindowsOwnerCommandTests(unittest.TestCase):
    def mocked_read(self, returncode):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "placeholder-password"
            source.write_bytes(b"placeholder-file")
            with patch.dict(os.environ, {"AUTOOS_OCL1_PW_FILE": str(source),
                                         "TEST_PW": "placeholder-env",
                                         "PSModulePath": "placeholder-modules"}, clear=True):
                with patch.object(oc_l1_password.os, "name", "nt"), patch.object(
                        subprocess, "run", return_value=SimpleNamespace(returncode=returncode)) as run:
                    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                        if returncode:
                            with self.assertRaises(SystemExit) as exc:
                                oc_l1_password.read_lane_password("test_pw")
                            self.assertEqual(exc.exception.code, 2)
                            self.assertIsNone(exc.exception.__context__)
                        else:
                            self.assertEqual(oc_l1_password.read_lane_password("test_pw"), "placeholder-file")
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), str(source) + "\n" if returncode else "")
            return run.call_args, str(source)

    def test_windows_script_requires_current_user_owner(self):
        call, source = self.mocked_read(0)
        argv = call.args[0]
        self.assertEqual(argv[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertEqual(len(argv), 5)
        script = argv[4]
        self.assertIn("$u=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; ", script)
        self.assertIn("if (!$o -or !$u -or $o -ne $u) {exit 1}; ", script)
        self.assertEqual(script, oc_l1_password._WINDOWS_ACL_SCRIPT)
        self.assertIn(oc_l1_password._WINDOWS_ACL_OWNER_CHECK, script)
        self.assertLess(script.index(oc_l1_password._WINDOWS_ACL_OWNER_CHECK), script.index("GetAccessRules"))
        self.assertNotIn(source, repr(argv))
        self.assertNotIn("placeholder", script)
        self.assertNotIn("AUTOOS_OCL1_PW_FILE", script)
        self.assertNotIn("$args", script)
        child_env = call.kwargs["env"]
        self.assertEqual(child_env["AUTOOS_OCL1_ACL_PATH"], source)
        self.assertNotIn("TEST_PW", child_env)
        self.assertFalse(any(key.upper() == "PSMODULEPATH" for key in child_env))
        self.assertNotIn("placeholder-file", repr(call))
        self.assertNotIn("placeholder-env", repr(call))
        self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(call.kwargs["timeout"], 10)
        second, other_source = self.mocked_read(0)
        self.assertNotEqual(source, other_source)
        self.assertEqual(script, second.args[0][4])

    def test_every_nonzero_child_exit_refuses_without_disclosure(self):
        for returncode in (1, 2, 13, -1, 0xC0000005):
            with self.subTest(returncode=returncode):
                self.mocked_read(returncode)


@unittest.skipUnless(os.name == "nt", "real owner-SID checks require Windows PowerShell and Windows ACLs")
class WindowsOwnerRealTests(unittest.TestCase):
    def run_script(self, script, source=None):
        env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
        if source is not None:
            env["AUTOOS_OCL1_ACL_PATH"] = str(source)
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode

    def test_foreign_system_owner_is_refused_by_owner_rule_alone(self):
        # Method (ii): share the production statement, omit every DACL Allow rule.
        # The independent preflight proves ACL/identity reads succeeded and differ.
        prefix = (
            "$ErrorActionPreference='Stop'; "
            "$a=Get-Acl -LiteralPath $env:AUTOOS_OCL1_ACL_PATH; "
            "$o=$a.GetOwner([System.Security.Principal.SecurityIdentifier]); "
        )
        probe = prefix + (
            "$u=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
            "if (!$o -or !$u) {exit 3}; if ($o -eq $u) {exit 4}; exit 0"
        )
        owner_only = prefix + oc_l1_password._WINDOWS_ACL_OWNER_CHECK + "exit 0"
        self.assertNotIn("GetAccessRules", owner_only)
        for name in ("kernel32.dll", "ntdll.dll", "notepad.exe"):
            source = Path(os.environ["SystemRoot"]) / "System32" / name
            if not source.is_file():
                continue
            result = self.run_script(probe, source)
            if result == 4:
                continue
            self.assertEqual(result, 0, "system ACL and current-user SID must be readable")
            self.assertEqual(self.run_script(owner_only, source), 1)
            return
        self.fail("no existing system-file fixture has an owner different from the current user")

    def test_owner_statement_accepts_own_sid_and_refuses_missing_owner(self):
        for owner, expected in (("[System.Security.Principal.WindowsIdentity]::GetCurrent().User", 0),
                                ("$null", 1)):
            with self.subTest(owner=owner):
                script = ("$ErrorActionPreference='Stop'; $o=" + owner + "; "
                          + oc_l1_password._WINDOWS_ACL_OWNER_CHECK + "exit 0")
                self.assertEqual(self.run_script(script), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
