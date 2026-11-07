"""Profile terminal-safety and Antigravity policy regressions (stdlib only)."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "Windows/Terminal/oh-my-posh/profile/AutoOS.Profile.ps1"
PWSH = shutil.which("pwsh")


class ProfileTests(unittest.TestCase):
    def test_terminal_operations_follow_vt_guard(self):
        text = PROFILE.read_text(encoding="utf-8-sig")
        guard = "if (-not $Host.UI.SupportsVirtualTerminal) { return }"
        self.assertIn(guard, text)
        # Missing properties are false too; check existence before dereferencing
        # so old hosts also work under StrictMode.
        self.assertIn("$Host.UI.PSObject.Properties['SupportsVirtualTerminal']", text)
        for operation in re.finditer(
                r"Import-Module (?:PSReadLine|Terminal-Icons)|Set-PSReadLine\w+|oh-my-posh init", text):
            self.assertGreater(operation.start(), text.index(guard))

    def test_profile_encoding(self):
        self.assertTrue(PROFILE.read_bytes().startswith(b"\xef\xbb\xbf"))

    @unittest.skipUnless(PWSH, "pwsh not on PATH")
    def test_redirected_profile(self):
        self.run_profile(". '" + str(PROFILE).replace("'", "''") + "'")

    def run_profile(self, command, terminal_expected=False):
        with tempfile.TemporaryDirectory(prefix="profile-nonvt-") as tmp:
            env = dict(os.environ)
            for name in ("LOCALAPPDATA", "APPDATA", "HOME", "USERPROFILE"):
                env[name] = tmp
            result = subprocess.run([PWSH, "-NoProfile", "-Command", command],
                                    capture_output=True, text=True, env=env,
                                    cwd=tmp, timeout=30)
            if terminal_expected:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("terminal initialization reached", result.stdout + result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")

    @unittest.skipUnless(PWSH, "pwsh not on PATH")
    def test_forced_nonvt_and_missing_property(self):
        # Execute the actual suffix after the independent redirection guard,
        # with only the read-only Host reference rebound to a synthetic object.
        text = PROFILE.read_text(encoding="utf-8-sig")
        suffix = text.split("{ return }", 1)[1].replace("$Host", "$TestHost")
        for ui, terminal_expected in (
                ("[pscustomobject]@{SupportsVirtualTerminal=$false}", False),
                ("[pscustomobject]@{}", False),
                ("[pscustomobject]@{SupportsVirtualTerminal=$true}", True)):
            with self.subTest(ui=ui):
                self.run_profile(
                    "function Test-Profile($TestHost) { Set-StrictMode -Version Latest; "
                    "function Get-Module { throw 'terminal initialization reached' }; "
                    + suffix + "\n}; Test-Profile ([pscustomobject]@{UI=" + ui + "})",
                    terminal_expected=terminal_expected)


class OrchestratorRulesTests(unittest.TestCase):
    def test_each_rule_is_documented(self):
        text = (ROOT / "GEMINI.md").read_text(encoding="utf-8")
        self.assertIn("## Orchestrator rules (Antigravity)", text)
        section = text.split("## Orchestrator rules (Antigravity)", 1)[1].split("\n## ", 1)[0]
        for keyword in ("WaitMsBeforeAsync", "Never poll", "completion notice",
                        "spawner MCP", "never by hand", "role review",
                        "core.hooksPath=/dev/null", "--no-verify", "exit $LASTEXITCODE",
                        "exit 0", "parser", "backup first", "whole config file",
                        "Never echo tokens or MCP config values",
                        "every review seat has finished"):
            with self.subTest(rule=keyword):
                self.assertIn(keyword, section)


if __name__ == "__main__":
    unittest.main(verbosity=2)
