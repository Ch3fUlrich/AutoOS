"""Tests for the Windows cost gate: the scheduled-task wrapper and module.

Plain unittest, no framework. PowerShell parsing and the behavioural checks
run through pwsh when it is on PATH; everything else is byte/text inspection
and runs everywhere. The behavioural checks sandbox APPDATA/LOCALAPPDATA and
mock every ScheduledTask cmdlet, so nothing real is registered.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "lib" / "windows" / "cost-gate-export.ps1"
MODULE = ROOT / "lib" / "windows" / "AutoOS.CostGate.psm1"
CATALOG = ROOT / "catalog" / "windows.json"
INSTALL = ROOT / "lib" / "windows" / "AutoOS.Install.psm1"
BOM = b"\xef\xbb\xbf"

# the scheduled-task module is Windows-only: execute its tests on Windows only (a Linux CI image may ship pwsh)
PWSH = (shutil.which("pwsh") or shutil.which("powershell")) if os.name == "nt" else None


def _text(path):
    return path.read_text(encoding="utf-8-sig")


def _run_powershell(command, env=None, cwd=None, timeout=120):
    return subprocess.run([PWSH, "-NoProfile", "-Command", command],
                          capture_output=True, text=True, env=env, cwd=cwd,
                          timeout=timeout)


def _parse_check(path):
    """Return the pwsh parse errors for a file ('' when it parses clean)."""
    command = ("$e = $null; "
               "$null = [Management.Automation.Language.Parser]::ParseFile('{p}', [ref]$null, [ref]$e); "
               "if ($e) { $e | ForEach-Object { Write-Output $_ }; exit 1 }"
               ).replace("{p}", str(path).replace("\\", "/"))
    result = _run_powershell(command)
    return result.stdout.strip()


# The mocks must accept the same parameter names the module passes; the real
# cmdlets all have switch parameters, and PowerShell binds [object] parameters
# positionally by name-matching only when their type matches.
MOCK_CMDS = r"""
function New-ScheduledTaskTrigger { param([switch]$Once, [datetime]$At, [timespan]$RepetitionInterval, [timespan]$RepetitionDuration, [switch]$AtLogOn, [string]$User) [pscustomobject]@{ Kind = 't' } }
function New-ScheduledTaskAction { param([string]$Execute, [string]$Argument) [pscustomobject]@{ Execute = $Execute; Argument = $Argument } }
function New-ScheduledTaskPrincipal { param([string]$UserId, [string]$LogonType) [pscustomobject]@{ UserId = $UserId } }
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$StartWhenAvailable, [string]$MultipleInstances) [pscustomobject]@{} }
function Register-ScheduledTask {
    param([string]$TaskName, $Action, $Trigger, $Principal, $Settings, [switch]$Force)
    $line = @{ name = $TaskName; execute = $Action.Execute; args = $Action.Argument; triggers = @($Trigger).Count } | ConvertTo-Json -Compress
    Add-Content -LiteralPath (Join-Path $PWD 'reg.jsonl') $line
    Set-Content -LiteralPath (Join-Path $PWD 'task.json') -Value ($line)
    [pscustomobject]@{ TaskName = $TaskName }
}
function Get-ScheduledTask {
    param([string]$TaskName, [switch]$ErrorAction)
    $f = Join-Path $PWD 'task.json'
    if (-not (Test-Path -LiteralPath $f)) { return $null }
    $r = Get-Content -LiteralPath $f -Raw | ConvertFrom-Json
    [pscustomobject]@{
        Actions = @([pscustomobject]@{ Execute = $r.execute; Arguments = $r.args })
        Triggers = @([pscustomobject]@{ Repetition = [pscustomobject]@{ Interval = 'PT10M' } })
    }
}
function Start-ScheduledTask { param([string]$TaskName) Set-Content -LiteralPath (Join-Path $PWD 'started-marker') -Value 'started' }
function Unregister-ScheduledTask { param([string]$TaskName, [switch]$Confirm) }
$null = $env:AUTOOS_COSTGATE_MOCK
"""


class WrapperFileTests(unittest.TestCase):
    def test_wrapper_starts_with_utf8_bom(self):
        self.assertTrue(WRAPPER.read_bytes().startswith(BOM),
                        "the wrapper must start with the UTF-8 BOM")

    @unittest.skipUnless(PWSH, "pwsh not on PATH")
    def test_wrapper_parses(self):
        errors = _parse_check(WRAPPER)
        self.assertEqual(errors, "", "wrapper has parse errors: %s" % errors)

    def test_header_carries_the_export_command(self):
        text = _text(WRAPPER)
        for needle in ("/api/usage/call-logs?limit=500&offset=0&excludeTests=1",
                       "apiFetch", "raw: true", "npm root -g",
                       "omniroute/bin/cli/api.mjs"):
            self.assertIn(needle, text, "export command text missing: %r" % needle)

    def test_wrapper_never_names_a_credential_file_or_word(self):
        # The export authenticates inside the omniroute helper; the wrapper
        # itself must not name a key/token/secret, a credential store file,
        # or an elevated scope.
        low = _text(WRAPPER).lower()
        for word in ("key", "token", "secret", "api-keys", "manage"):
            self.assertNotIn(word, low, "wrapper names %r" % word)

    def test_failure_path_writes_status_and_exits_3(self):
        # Static half of the exit-code proof: every failure site writes the
        # UNAVAILABLE status line first, and the wrapper has no exit 0 path.
        text = _text(WRAPPER)
        self.assertNotIn("exit 0", text)
        self.assertGreaterEqual(text.count("exit 3"), 3)
        self.assertGreaterEqual(text.count("Write-StatusUnavailable 'rows export failed'"), 3)
        self.assertIn("UNAVAILABLE: $Reason", text)


class ModuleFileTests(unittest.TestCase):
    def test_module_starts_with_utf8_bom(self):
        self.assertTrue(MODULE.read_bytes().startswith(BOM),
                        "the module must start with the UTF-8 BOM")

    @unittest.skipUnless(PWSH, "pwsh not on PATH")
    def test_module_parses(self):
        errors = _parse_check(MODULE)
        self.assertEqual(errors, "", "module has parse errors: %s" % errors)

    @unittest.skipUnless(PWSH, "pwsh not on PATH")
    def test_module_exports_the_installer(self):
        tmp = Path(tempfile.mkdtemp(prefix="costgate-"))
        try:
            result = _run_powershell(
                "Set-Location '{t}'; $env:APPDATA='{a}'; $env:LOCALAPPDATA='{l}'; "
                "Import-Module '{m}' -Force; "
                "(Get-Module AutoOS.CostGate).ExportedFunctions.Keys -contains 'Install-CostGateTask'"
                .format(t=tmp, a=tmp, l=tmp, m=MODULE),
                cwd=str(tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("True", result.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CatalogTests(unittest.TestCase):
    def test_catalog_is_valid_json_with_unique_ids(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        ids = [c["id"] for cat in catalog["categories"] for c in cat["components"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate component ids")

    def test_cost_gate_entry_exists_exactly_once(self):
        raw = CATALOG.read_text(encoding="utf-8")
        self.assertEqual(raw.count('"id": "cost-gate"'), 1)
        catalog = json.loads(raw)
        entries = [c for cat in catalog["categories"] for c in cat["components"]
                   if c["id"] == "cost-gate"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["provider"], "script")
        self.assertEqual(entry["package"], "cost-gate")
        self.assertEqual(entry["launcher"], "none")
        self.assertLessEqual(len(entry["description"]), 70)
        self.assertTrue(entry["homepage"].startswith("https://"))


class RegistrationTests(unittest.TestCase):
    def test_script_provider_switch_names_the_new_id(self):
        text = _text(INSTALL)
        match = re.search(r"'cost-gate'\s*\{ return Install-AutoOSCostGate \}", text)
        self.assertIsNotNone(match, "no 'cost-gate' arm in Invoke-AutoOSScriptProvider")

    def test_installer_function_registered_and_exported(self):
        text = _text(INSTALL)
        self.assertIn("function Install-AutoOSCostGate {", text)
        self.assertIn("Install-CostGateTask", text)
        self.assertRegex(text, r"Install-AutoOSClaudeAutostart,\s*Install-AutoOSCostGate,")


class InstallBehaviourTests(unittest.TestCase):
    """First call creates, second call skips, config never overwritten,
    non-numeric refused, nothing started without -Start. APPDATA and
    LOCALAPPDATA are sandboxed and every ScheduledTask cmdlet is mocked."""

    @unittest.skipUnless(PWSH, "pwsh not on PATH")
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="costgate-behaviour-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.appdata = self.tmp / "appdata"
        self.repo = self.tmp / "repo"
        self.repo.mkdir(parents=True)
        (self.repo / "tools").mkdir()
        self.sandbox = self.tmp / "sandbox"
        self.sandbox.mkdir()
        self.env = dict(os.environ)
        self.env["APPDATA"] = str(self.appdata)
        self.env["LOCALAPPDATA"] = str(self.tmp / "localappdata")

    def _ps(self, script, cwd=None):
        return _run_powershell(MOCK_CMDS + "\n" + script, env=self.env,
                               cwd=str(cwd or self.sandbox))

    def _install(self, extra=""):
        return self._ps("Import-Module '{m}' -Force\n"
                        "Install-CostGateTask -RepoRoot '{r}' {x}"
                        .format(m=MODULE, r=self.repo, x=extra))

    def _config(self):
        return self.appdata / "autoos" / "daily-gate.conf"

    def test_first_call_creates_config_and_registers_task(self):
        result = self._install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("installed", result.stdout)
        self.assertTrue(self._config().is_file(), "config file was not created")
        self.assertEqual(self._config().read_text(encoding="utf-8"), "warn=20\nblock=25\n")
        reg = (self.sandbox / "reg.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(reg), 1)
        record = json.loads(reg[0])
        self.assertEqual(record["name"], "AutoOS cost gate")
        self.assertIn("cost-gate-export.ps1", record["args"])
        self.assertIn("-RepoRoot", record["args"])
        self.assertFalse((self.sandbox / "started-marker").exists(),
                         "the task must not start without -Start")

    def test_second_call_reports_skipped_and_changes_nothing(self):
        first = self._install()
        self.assertIn("installed", first.stdout)
        reg_before = (self.sandbox / "reg.jsonl").read_text(encoding="utf-8")
        config_before = self._config().read_bytes()
        second = self._install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("skipped", second.stdout)
        self.assertEqual((self.sandbox / "reg.jsonl").read_text(encoding="utf-8"), reg_before)
        self.assertEqual(self._config().read_bytes(), config_before)

    def test_existing_config_is_never_overwritten(self):
        self._install()
        hand = "warn=99\nblock=120\n"
        self._config().write_text(hand, encoding="utf-8")
        result = self._install()
        self.assertIn("kept as is", result.stdout)
        self.assertEqual(self._config().read_text(encoding="utf-8"), hand)

    def test_non_numeric_threshold_is_refused_with_no_write(self):
        self.appdata.mkdir(parents=True, exist_ok=True)
        result = self._install(extra="-Warn abc -Block 25")
        # PowerShell refuses the binding of 'abc' to [int] before the body
        # runs; either way the call must fail without writing anything.
        self.assertNotEqual(result.returncode, 0, "non-numeric Warn must be refused")
        self.assertFalse(self._config().exists(), "a refused install must not write a config")
        self.assertFalse((self.sandbox / "reg.jsonl").exists())

    def test_start_flag_starts_the_task(self):
        result = self._install(extra="-Start")
        self.assertIn("installed", result.stdout)
        self.assertTrue((self.sandbox / "started-marker").exists(),
                        "-Start must run Start-ScheduledTask")


class WrapperExitCodeTests(unittest.TestCase):
    """The export-failure path: with node and npm removed from PATH the
    wrapper must exit 3 and write the UNAVAILABLE status line. The wrapper is
    called with `&` so its exit statement is read as $LASTEXITCODE."""

    @unittest.skipUnless(PWSH, "pwsh not on PATH")
    def test_missing_node_writes_unavailable_and_exits_3(self):
        tmp = Path(tempfile.mkdtemp(prefix="costgate-exit-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        local = tmp / "localappdata"
        repo = tmp / "repo"
        repo.mkdir(parents=True)
        empty = tmp / "empty"
        empty.mkdir()
        env = dict(os.environ)
        env["PATH"] = str(empty)
        env["APPDATA"] = str(tmp / "appdata")
        env["LOCALAPPDATA"] = str(local)
        result = _run_powershell(
            "& '{w}' -RepoRoot '{r}'; $LASTEXITCODE".format(w=WRAPPER, r=repo),
            env=env, cwd=str(tmp))
        self.assertEqual(result.stdout.strip().splitlines()[-1], "3",
                         "exit %r (stderr: %s)" % (result.stdout, result.stderr))
        status = local / "autoos" / "daily-gate.status"
        self.assertTrue(status.is_file(), "status file missing")
        line = status.read_text(encoding="utf-8").strip()
        self.assertIn("UNAVAILABLE: rows export failed", line)
        self.assertTrue(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", line),
                        "status line must start with the UTC time")
        self.assertFalse((local / "autoos" / "daily-gate.json").exists(),
                         "a failed export must not touch the gate file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
