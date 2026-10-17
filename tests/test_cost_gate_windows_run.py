"""Tests that EXECUTE PowerShell for the Windows cost gate.

Plain unittest. All execution runs against fakes: temporary directories
for LOCALAPPDATA and APPDATA, fake npm/node shims first on PATH, and an
api.mjs stub directory. No real gateway, no real scheduled task.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "lib" / "windows" / "cost-gate-export.ps1"
MODULE = ROOT / "lib" / "windows" / "AutoOS.CostGate.psm1"
INSTALL = ROOT / "lib" / "windows" / "AutoOS.Install.psm1"
RUN_BUDGET = ROOT / "tools" / "run_budget.py"

# these tests execute Windows PowerShell scripts with .cmd shims: run them on Windows only (a Linux CI image may ship pwsh)
PWSH = shutil.which("pwsh") if os.name == "nt" else None
POWERSHELL = shutil.which("powershell") if os.name == "nt" else None


def _make_rows(n, offset_days=0):
    now = datetime.now(timezone.utc) - timedelta(days=offset_days)
    rows = []
    for i in range(n):
        dt = now - timedelta(seconds=i)
        ts = (dt.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00" if i % 3 == 1
              else dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
        rows.append({
            "timestamp": ts,
            "model": "vertex-gemini-3.8-flash",
            "provider": "vertex",
            "tokens": {"in": 12345, "out": 678, "cacheRead": 2345}
        })
    return rows


def _setup_fakes(tmp_path, rows, node_body_override=None):
    bdir, ldir, adir = tmp_path / "bin", tmp_path / "local", tmp_path / "appdata"
    npmdir = tmp_path / "npmroot" / "omniroute" / "bin" / "cli"
    for d in (bdir, ldir, adir, npmdir):
        d.mkdir(parents=True, exist_ok=True)
    (npmdir / "api.mjs").write_text("", encoding="utf-8")
    np = str(tmp_path / "npmroot").replace("'", "''")
    (bdir / "npm.ps1").write_text(f"Write-Output '{np}'\n", encoding="utf-8")
    (bdir / "npm.cmd").write_text(f"@echo off\r\necho {tmp_path / 'npmroot'}\r\n", encoding="utf-8")
    rf = tmp_path / "rows-src.json"
    rf.write_text(json.dumps(rows), encoding="utf-8")
    nb = node_body_override or (
        "$b = [IO.File]::ReadAllText($env:FAKE_ROWS)\n"
        "if ($env:AUTOS_OFFSET -ne '0') { $b = '[]' }\nWrite-Output (\"200`n\" + $b)\nexit 0\n")
    (bdir / "node.ps1").write_text(nb, encoding="utf-8")
    (bdir / "node.cmd").write_text(
        f'@echo off\r\npwsh -NoProfile -ExecutionPolicy Bypass -File "{bdir / "node.ps1"}" %*\r\n',
        encoding="utf-8")
    env = os.environ.copy()
    env.update({"DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1", "FAKE_ROWS": str(rf),
                "LOCALAPPDATA": str(ldir), "APPDATA": str(adir),
                "PATH": str(bdir) + os.pathsep + env.get("PATH", "")})
    return {"bin_dir": bdir, "local_dir": ldir, "appdata_dir": adir, "rows_file": rf, "env": env}


def _run_wrapper(shell_exe, env, timeout=60):
    cmd = [shell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(WRAPPER), "-RepoRoot", str(ROOT)]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def _assert_e2e_run(test_case, shell_exe, n):
    with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
        tmp = Path(td)
        rows = _make_rows(n)
        fakes = _setup_fakes(tmp, rows)
        res = _run_wrapper(shell_exe, fakes["env"])
        test_case.assertEqual(res.returncode, 0, f"wrapper failed: {res.stderr}\n{res.stdout}")
        rf = fakes["local_dir"] / "autoos" / "daily-gate-rows.json"
        test_case.assertTrue(rf.exists())
        txt = rf.read_text(encoding="utf-8")
        test_case.assertTrue(txt.startswith("["))
        test_case.assertEqual(len(json.loads(txt)), n)
        gf = fakes["local_dir"] / "autoos" / "daily-gate.json"
        test_case.assertTrue(gf.exists())
        gdata = json.loads(gf.read_text(encoding="utf-8"))
        sf = fakes["local_dir"] / "autoos" / "daily-gate.status"
        test_case.assertTrue(sf.exists())
        stxt = sf.read_text(encoding="utf-8").strip()
        test_case.assertIn("verdict=ok", stxt)
        bres = subprocess.run([sys.executable, str(RUN_BUDGET), "day", "--rows", str(fakes["rows_file"])],
                              capture_output=True, text=True, check=True)
        ddata = json.loads(bres.stdout)
        test_case.assertEqual(gdata["verdict"], ddata["verdict"])
        test_case.assertEqual(gdata["usd"], ddata["usd"])
        test_case.assertIn(f"usd={ddata['usd']}", stxt)


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateWindowsRunA1(unittest.TestCase):
    """A1: wrapper with rows exits 0, writes array rows file, gate and status."""
    def test_a1_wrapper_1_row(self): _assert_e2e_run(self, PWSH, 1)
    def test_a1_wrapper_7_rows(self): _assert_e2e_run(self, PWSH, 7)


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateWindowsRunA2(unittest.TestCase):
    """A2: non-200 status, empty first page, or missing node exit 3."""
    def test_a2_fake_node_500(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            nb = '$b = [IO.File]::ReadAllText($env:FAKE_ROWS)\nWrite-Output ("500`n" + $b)\nexit 0\n'
            f = _setup_fakes(Path(td), _make_rows(1), node_body_override=nb)
            res = _run_wrapper(PWSH, f["env"])
            self.assertEqual(res.returncode, 3)
            self.assertFalse((f["local_dir"] / "autoos" / "daily-gate.json").exists())

    def test_a2_fake_node_empty_first_page(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            f = _setup_fakes(Path(td), [], node_body_override='Write-Output ("200`n[]")\nexit 0\n')
            res = _run_wrapper(PWSH, f["env"])
            self.assertEqual(res.returncode, 3)
            self.assertFalse((f["local_dir"] / "autoos" / "daily-gate.json").exists())

    def test_a2_node_missing_from_path(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            f = _setup_fakes(Path(td), _make_rows(1))
            for x in (f["bin_dir"] / "node.ps1", f["bin_dir"] / "node.cmd"):
                if x.exists():
                    x.unlink()
            sroot = os.environ.get("SystemRoot", r"C:\Windows")
            f["env"]["PATH"] = os.pathsep.join([
                str(f["bin_dir"]), str(Path(PWSH).parent), str(Path(sys.executable).parent),
                str(Path(sroot) / "System32"), sroot])
            res = _run_wrapper(PWSH, f["env"])
            self.assertEqual(res.returncode, 3)
            self.assertFalse((f["local_dir"] / "autoos" / "daily-gate.json").exists())


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateWindowsRunA3(unittest.TestCase):
    """A3: paging stops when oldest row is older than cutoff, or page is short."""
    def test_a3_stops_on_cutoff_and_short_page(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            tmp = Path(td)
            now = datetime.now(timezone.utc)
            p0 = [{"timestamp": (now - timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                   "model": "vertex-gemini-3.8-flash", "provider": "vertex", "tokens": {"in": 100, "out": 10, "cacheRead": 0}}
                  for i in range(500)]
            p1 = [{"timestamp": (now - timedelta(days=3, seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                   "model": "vertex-gemini-3.8-flash", "provider": "vertex", "tokens": {"in": 100, "out": 10, "cacheRead": 0}}
                  for i in range(500)]
            p0_f, p1_f, cf = tmp / "p0.json", tmp / "p1.json", tmp / "pages.log"
            p0_f.write_text(json.dumps(p0), encoding="utf-8")
            p1_f.write_text(json.dumps(p1), encoding="utf-8")
            nb = (f"$f = '{str(cf).replace(chr(92), '/')}'\n"
                  'Add-Content -LiteralPath $f -Value $env:AUTOS_OFFSET\n'
                  "if ($env:AUTOS_OFFSET -eq '0') {\n"
                  f"  $body = [IO.File]::ReadAllText('{str(p0_f).replace(chr(92), '/')}')\n"
                  "} elseif ($env:AUTOS_OFFSET -eq '500') {\n"
                  f"  $body = [IO.File]::ReadAllText('{str(p1_f).replace(chr(92), '/')}')\n"
                  "} else { $body = '[]' }\n"
                  'Write-Output ("200`n" + $body)\nexit 0\n')
            f = _setup_fakes(tmp, p0, node_body_override=nb)
            res = _run_wrapper(PWSH, f["env"])
            self.assertEqual(res.returncode, 0)
            offsets = [x.strip() for x in cf.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(offsets, ["0", "500"])

    def test_a3_short_page_stops(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            tmp = Path(td)
            rows = _make_rows(10, offset_days=3)
            cf = tmp / "pages.log"
            nb = (f"$f = '{str(cf).replace(chr(92), '/')}'\n"
                  'Add-Content -LiteralPath $f -Value $env:AUTOS_OFFSET\n'
                  "$body = [IO.File]::ReadAllText($env:FAKE_ROWS)\n"
                  "if ($env:AUTOS_OFFSET -ne '0') { $body = '[]' }\n"
                  'Write-Output ("200`n" + $body)\nexit 0\n')
            f = _setup_fakes(tmp, rows, node_body_override=nb)
            res = _run_wrapper(PWSH, f["env"])
            self.assertEqual(res.returncode, 0)
            offsets = [x.strip() for x in cf.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(offsets, ["0"])


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateWindowsRunA4(unittest.TestCase):
    """A4: rows file moved atomically; no *.tmp siblings left behind."""
    def test_a4_no_tmp_left_in_state_dir(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            f = _setup_fakes(Path(td), _make_rows(3))
            res = _run_wrapper(PWSH, f["env"])
            self.assertEqual(res.returncode, 0)
            adir = f["local_dir"] / "autoos"
            self.assertTrue((adir / "daily-gate-rows.json").exists())
            self.assertEqual(list(adir.glob("*.tmp")), [])


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateWindowsRunA5(unittest.TestCase):
    """A5: timestamps in both forms (...Z and ...+00:00) parse successfully."""
    def test_a5_both_timestamp_formats_parse(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            now = datetime.now(timezone.utc)
            rows = [
                {"timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                 "model": "vertex-gemini-3.8-flash", "provider": "vertex",
                 "tokens": {"in": 500, "out": 50, "cacheRead": 100}},
                {"timestamp": now.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00",
                 "model": "vertex-gemini-3.8-flash", "provider": "vertex",
                 "tokens": {"in": 500, "out": 50, "cacheRead": 100}}
            ]
            f = _setup_fakes(Path(td), rows)
            res = _run_wrapper(PWSH, f["env"])
            self.assertEqual(res.returncode, 0)
            rf = f["local_dir"] / "autoos" / "daily-gate-rows.json"
            self.assertTrue(rf.exists())
            self.assertEqual(len(json.loads(rf.read_text(encoding="utf-8"))), 2)


@unittest.skipUnless(POWERSHELL, "powershell 5.1 not on PATH or not on Windows")
class TestCostGateWindowsRunA6(unittest.TestCase):
    """A6: Windows PowerShell 5.1 variant of A1."""
    def test_a6_powershell_51_1_row(self): _assert_e2e_run(self, POWERSHELL, 1)
    def test_a6_powershell_51_7_rows(self): _assert_e2e_run(self, POWERSHELL, 7)


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateWindowsRunA7(unittest.TestCase):
    """A7: dispatch through Install-AutoOSComponent and direct Install-AutoOSCostGate."""
    def test_a7_dispatch_and_direct(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            tmp = Path(td)
            appdata, local = tmp / "appdata", tmp / "local"
            appdata.mkdir(parents=True, exist_ok=True)
            local.mkdir(parents=True, exist_ok=True)
            ps_script = tmp / "dispatch.ps1"
            ps_script.write_text(f"""
$ErrorActionPreference = 'Continue'
if (-not $env:ProgramFiles) {{ $env:ProgramFiles = 'C:\\Program Files' }}
if (-not $env:ProgramData) {{ $env:ProgramData = 'C:\\ProgramData' }}
$env:APPDATA = '{str(appdata).replace(chr(92), "/")}'
$env:LOCALAPPDATA = '{str(local).replace(chr(92), "/")}'
Import-Module '{str(INSTALL).replace(chr(92), "/")}' -Force -DisableNameChecking
$global:Registered = 0; $global:CurrentTask = $null
function global:Get-ScheduledTask {{ param($TaskName, $ErrorAction) $global:CurrentTask }}
function global:New-ScheduledTaskPrincipal {{ param($UserId, $LogonType) 'p' }}
function global:New-ScheduledTaskSettingsSet {{ param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$MultipleInstances) 's' }}
function global:New-ScheduledTaskTrigger {{ param([switch]$Once,$At,$RepetitionInterval,$RepetitionDuration,[switch]$AtLogOn,$User) [pscustomobject]@{{ Repetition = if ($RepetitionInterval) {{ [pscustomobject]@{{ Interval = [System.Xml.XmlConvert]::ToString($RepetitionInterval) }} }} else {{ $null }} }} }}
function global:New-ScheduledTaskAction {{ param($Execute,$Argument) [pscustomobject]@{{ Execute = $Execute; Arguments = $Argument }} }}
function global:Register-ScheduledTask {{ param($TaskName,[switch]$Force,$Action,$Trigger,$Principal,$Settings)
  $global:Registered++; $global:CurrentTask = [pscustomobject]@{{ Actions = @($Action); Triggers = @($Trigger) }} }}
function global:Start-ScheduledTask {{ param($TaskName) }}

$m = Get-Module AutoOS.Install
$comp = [pscustomobject]@{{ Name = 'Daily cost gate'; Provider = 'script'; Package = 'cost-gate'; Id = 'cost-gate'; Homepage = 'x' }}

$a1 = & $m {{ Install-AutoOSCostGate }}
Write-Output "d1_type=$($a1.GetType().Name);d1_count=$(@($a1).Count);d1_code=$($a1.ExitCode);d1_ok=$($a1.Success);d1_reg=$global:Registered"
$a2 = & $m {{ Install-AutoOSCostGate }}
Write-Output "d2_type=$($a2.GetType().Name);d2_count=$(@($a2).Count);d2_code=$($a2.ExitCode);d2_ok=$($a2.Success);d2_reg=$global:Registered"

$global:Registered = 0; $global:CurrentTask = $null
$dry = & $m {{ $script:DryRun = $true; Install-AutoOSCostGate }}; & $m {{ $script:DryRun = $false }}
Write-Output "dry_type=$($dry.GetType().Name);dry_count=$(@($dry).Count);dry_code=$($dry.ExitCode);dry_ok=$($dry.Success);dry_reg=$global:Registered"

function global:Register-ScheduledTask {{ throw 'boom' }}
$global:CurrentTask = $null
$a3 = & $m {{ Install-AutoOSCostGate }}
Write-Output "d3_type=$($a3.GetType().Name);d3_count=$(@($a3).Count);d3_code=$($a3.ExitCode);d3_ok=$($a3.Success)"

Remove-Item -Recurse -Force "$env:APPDATA\\*", "$env:LOCALAPPDATA\\*" -ErrorAction SilentlyContinue
$global:Registered = 0; $global:CurrentTask = $null
function global:Register-ScheduledTask {{ param($TaskName,[switch]$Force,$Action,$Trigger,$Principal,$Settings)
  $global:Registered++; $global:CurrentTask = [pscustomobject]@{{ Actions = @($Action); Triggers = @($Trigger) }} }}

$b1 = & $m {{ param($c) Install-AutoOSComponent -Component $c }} $comp
Write-Output "b1=$b1;b1_reg=$global:Registered"
$b2 = & $m {{ param($c) Install-AutoOSComponent -Component $c }} $comp
Write-Output "b2=$b2;b2_reg=$global:Registered"

function global:Register-ScheduledTask {{ throw 'boom' }}
$global:CurrentTask = $null
$b3 = & $m {{ param($c) Install-AutoOSComponent -Component $c }} $comp
Write-Output "b3=$b3"
""", encoding="utf-8")
            res = subprocess.run([PWSH, "-NoProfile", "-File", str(ps_script)],
                                 capture_output=True, text=True, check=True)
            kv = {}
            for line in res.stdout.splitlines():
                for part in line.strip().split(";"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        kv[k.strip()] = v.strip()

            for prefix, exp_type, exp_cnt, exp_code, exp_ok, exp_reg in [
                ("d1", "Hashtable", "1", "0", "True", "1"),
                ("d2", "Hashtable", "1", "-1978335189", "True", "1"),
                ("dry", "Hashtable", "1", "0", "True", "0"),
            ]:
                self.assertEqual(kv.get(f"{prefix}_type"), exp_type)
                self.assertEqual(kv.get(f"{prefix}_count"), exp_cnt)
                self.assertEqual(kv.get(f"{prefix}_code"), exp_code)
                self.assertEqual(kv.get(f"{prefix}_ok"), exp_ok)
                self.assertEqual(kv.get(f"{prefix}_reg"), exp_reg)

            self.assertEqual(kv.get("d3_type"), "Hashtable")
            self.assertEqual(kv.get("d3_count"), "1")
            self.assertEqual(kv.get("d3_code"), "1")
            self.assertEqual(kv.get("d3_ok"), "False")

            self.assertEqual(kv.get("b1"), "installed")
            self.assertEqual(kv.get("b1_reg"), "1")
            self.assertEqual(kv.get("b2"), "skipped")
            self.assertEqual(kv.get("b2_reg"), "1")
            self.assertEqual(kv.get("b3"), "failed")


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateWindowsRunA8(unittest.TestCase):
    """A8: threshold refusals, interval parsing, and pass-through to config."""
    def test_a8_threshold_refusals_and_config(self):
        with tempfile.TemporaryDirectory(prefix="cg-test-") as td:
            tmp = Path(td)
            appdata, local = tmp / "appdata", tmp / "local"
            appdata.mkdir(parents=True, exist_ok=True)
            local.mkdir(parents=True, exist_ok=True)
            ps_script = tmp / "test_a8.ps1"
            ps_script.write_text(f"""
$ErrorActionPreference = 'Continue'
$env:APPDATA = '{str(appdata).replace(chr(92), "/")}'
$env:LOCALAPPDATA = '{str(local).replace(chr(92), "/")}'
Import-Module '{str(MODULE).replace(chr(92), "/")}' -Force -DisableNameChecking
$repoRoot = '{str(ROOT).replace(chr(92), "/")}'

try {{ Install-CostGateTask -Warn 8 -Block 5 -RepoRoot $repoRoot; "w_gt=allowed" }} catch {{ "w_gt=refused:$($_.Exception.Message)" }}
try {{ Install-CostGateTask -Warn 8 -Block 8 -RepoRoot $repoRoot; "w_eq=allowed" }} catch {{ "w_eq=refused:$($_.Exception.Message)" }}
try {{ Install-CostGateTask -Warn -1 -Block 5 -RepoRoot $repoRoot; "w_neg=allowed" }} catch {{ "w_neg=refused:$($_.Exception.Message)" }}

function global:Get-ScheduledTask {{ param($TaskName, $ErrorAction) $null }}
function global:New-ScheduledTaskPrincipal {{ param($UserId, $LogonType) 'p' }}
function global:New-ScheduledTaskSettingsSet {{ param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$MultipleInstances) 's' }}
function global:New-ScheduledTaskTrigger {{ param([switch]$Once,$At,$RepetitionInterval,$RepetitionDuration,[switch]$AtLogOn,$User) [pscustomobject]@{{ Repetition = if ($RepetitionInterval) {{ [pscustomobject]@{{ Interval = [System.Xml.XmlConvert]::ToString($RepetitionInterval) }} }} else {{ $null }} }} }}
function global:New-ScheduledTaskAction {{ param($Execute,$Argument) [pscustomobject]@{{ Execute = $Execute; Arguments = $Argument }} }}
function global:Register-ScheduledTask {{ param($TaskName,[switch]$Force,$Action,$Trigger,$Principal,$Settings) [pscustomobject]@{{ TaskName = $TaskName }} }}
function global:Start-ScheduledTask {{ param($TaskName) }}

$res = @(Install-CostGateTask -Warn 12 -Block 15 -RepoRoot $repoRoot)[-1]
"install=$res"

$conf = Join-Path $env:APPDATA 'autoos\\daily-gate.conf'
if (Test-Path $conf) {{
    $raw = Get-Content $conf -Raw
    if ($raw -match 'warn=([0-9]+)') {{ "cfg_w=$($Matches[1])" }}
    if ($raw -match 'block=([0-9]+)') {{ "cfg_b=$($Matches[1])" }}
}}

function global:Get-ScheduledTask {{
    param($TaskName, $ErrorAction)
    [pscustomobject]@{{
        Actions = @([pscustomobject]@{{ Execute = 'pwsh'; Arguments = 'x' }})
        Triggers = @([pscustomobject]@{{ Repetition = [pscustomobject]@{{ Interval = 'not-a-timespan' }} }})
    }}
}}
try {{
    Test-AutoOSCostGateTaskCurrent -Arguments 'x' -Execute 'pwsh' -IntervalMinutes 10
    "bad_int=allowed"
}} catch {{
    "bad_int=refused"
}}
""", encoding="utf-8")
            res = subprocess.run([PWSH, "-NoProfile", "-File", str(ps_script)],
                                 capture_output=True, text=True, check=True)
            kv = {}
            for line in res.stdout.splitlines():
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    kv[k.strip()] = v.strip()

            self.assertTrue(kv.get("w_gt", "").startswith("refused:"), f"unexpected: {kv.get('w_gt')}")
            self.assertIn("must be greater than", kv.get("w_gt", ""))
            self.assertTrue(kv.get("w_eq", "").startswith("refused:"), f"unexpected: {kv.get('w_eq')}")
            self.assertIn("must be greater than", kv.get("w_eq", ""))
            self.assertTrue(kv.get("w_neg", "").startswith("refused:"), f"unexpected: {kv.get('w_neg')}")
            self.assertEqual(kv.get("install"), "installed")
            self.assertEqual(kv.get("cfg_w"), "12")
            self.assertEqual(kv.get("cfg_b"), "15")
            self.assertEqual(kv.get("bad_int"), "refused")


if __name__ == "__main__":
    unittest.main()
