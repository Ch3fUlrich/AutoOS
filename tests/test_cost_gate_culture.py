"""Tests for culture invariance and export completeness in cost-gate-export.ps1.

Verifies that ISO timestamps are parsed culture-invariantly, paging break
conditions work under de-DE and en-US, and incomplete paging surfaces UNAVAILABLE.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "lib" / "windows" / "cost-gate-export.ps1"
RUN_BUDGET = ROOT / "tools" / "run_budget.py"

PWSH = shutil.which("pwsh") if os.name == "nt" else None


def _make_rows(n, offset_days=0, start_time=None, step_sec=5):
    base = start_time or (datetime.now(timezone.utc) - timedelta(days=offset_days))
    rows = []
    for i in range(n):
        dt = base - timedelta(seconds=i * step_sec)
        rows.append({
            "id": f"row-{i}",
            "timestamp": dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "model": "vertex-gemini-3.8-flash",
            "provider": "vertex",
            "tokens": {"in": 100, "out": 10, "cacheRead": 0}
        })
    return rows


def _setup_fakes(tmp_path, rows, node_script_body=None):
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
    nb = node_script_body or (
        "$all = [IO.File]::ReadAllText($env:FAKE_ROWS) | ConvertFrom-Json\n"
        "$off = [int]$env:AUTOS_OFFSET; $lim = [int]$env:AUTOS_LIMIT\n"
        "$slice = @($all | Select-Object -Skip $off -First $lim)\n"
        "$b = if ($slice.Count -gt 0) { ConvertTo-Json -InputObject $slice -Depth 5 -Compress } else { '[]' }\n"
        "Write-Output (\"200`n\" + $b)\nexit 0\n"
    )
    (bdir / "node.ps1").write_text(nb, encoding="utf-8")
    (bdir / "node.cmd").write_text(
        f'@echo off\r\npwsh -NoProfile -ExecutionPolicy Bypass -File "{bdir / "node.ps1"}" %*\r\n',
        encoding="utf-8")
    env = os.environ.copy()
    env.update({"DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "0", "FAKE_ROWS": str(rf),
                "LOCALAPPDATA": str(ldir), "APPDATA": str(adir),
                "PATH": str(bdir) + os.pathsep + env.get("PATH", "")})
    return {"bin_dir": bdir, "local_dir": ldir, "appdata_dir": adir, "rows_file": rf, "env": env}


def _run_driver(tmp_path, fakes, culture="de-DE", wrapper_path=WRAPPER):
    w_str = str(wrapper_path).replace("\\", "/")
    r_str = str(ROOT).replace("\\", "/")
    drv = tmp_path / "driver.ps1"
    drv.write_text(
        f"$c = [System.Globalization.CultureInfo]::GetCultureInfo('{culture}')\n"
        "[System.Threading.Thread]::CurrentThread.CurrentCulture = $c\n"
        "[System.Threading.Thread]::CurrentThread.CurrentUICulture = $c\n"
        f". '{w_str}' -RepoRoot '{r_str}'\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8"
    )
    cmd = [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(drv)]
    return subprocess.run(cmd, capture_output=True, text=True, env=fakes["env"], timeout=60)


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateCulture(unittest.TestCase):
    """Culture tests (de-DE, en-US) ensuring complete paging without round-trip bugs."""

    def test_a_de_de_3_pages_success(self):
        with tempfile.TemporaryDirectory(prefix="cg-cult-") as td:
            tmp = Path(td)
            fakes = _setup_fakes(tmp, _make_rows(1500))
            res = _run_driver(tmp, fakes, culture="de-DE")
            self.assertEqual(res.returncode, 0, f"wrapper failed: {res.stderr}\n{res.stdout}")
            rf = fakes["local_dir"] / "autoos" / "daily-gate-rows.json"
            self.assertTrue(rf.exists())
            rows_data = json.loads(rf.read_text(encoding="utf-8"))
            self.assertEqual(len(rows_data), 1500)
            gf = fakes["local_dir"] / "autoos" / "daily-gate.json"
            self.assertTrue(gf.exists())
            gdata = json.loads(gf.read_text(encoding="utf-8"))
            sf = fakes["local_dir"] / "autoos" / "daily-gate.status"
            self.assertTrue(sf.exists())
            stxt = sf.read_text(encoding="utf-8").strip()
            self.assertIn("verdict=ok", stxt)
            bres = subprocess.run([sys.executable, str(RUN_BUDGET), "day", "--rows", str(rf)],
                                  capture_output=True, text=True, check=True)
            ddata = json.loads(bres.stdout)
            self.assertEqual(gdata["verdict"], ddata["verdict"])
            self.assertEqual(gdata["usd"], ddata["usd"])

    def test_b_en_us_3_pages_success(self):
        with tempfile.TemporaryDirectory(prefix="cg-cult-") as td:
            tmp = Path(td)
            fakes = _setup_fakes(tmp, _make_rows(1500))
            res = _run_driver(tmp, fakes, culture="en-US")
            self.assertEqual(res.returncode, 0, f"wrapper failed: {res.stderr}\n{res.stdout}")
            rf = fakes["local_dir"] / "autoos" / "daily-gate-rows.json"
            self.assertEqual(len(json.loads(rf.read_text(encoding="utf-8"))), 1500)

    def test_c_cutoff_stops_paging_under_de_de(self):
        with tempfile.TemporaryDirectory(prefix="cg-cult-") as td:
            tmp = Path(td)
            fakes = _setup_fakes(tmp, _make_rows(500, offset_days=3))
            res = _run_driver(tmp, fakes, culture="de-DE")
            self.assertEqual(res.returncode, 0)
            rf = fakes["local_dir"] / "autoos" / "daily-gate-rows.json"
            self.assertEqual(len(json.loads(rf.read_text(encoding="utf-8"))), 500)

    def test_d_short_page_stops_paging(self):
        with tempfile.TemporaryDirectory(prefix="cg-cult-") as td:
            tmp = Path(td)
            fakes = _setup_fakes(tmp, _make_rows(10))
            res = _run_driver(tmp, fakes, culture="de-DE")
            self.assertEqual(res.returncode, 0)
            rf = fakes["local_dir"] / "autoos" / "daily-gate-rows.json"
            self.assertEqual(len(json.loads(rf.read_text(encoding="utf-8"))), 10)


@unittest.skipUnless(PWSH, "pwsh not on PATH")
class TestCostGateCompleteness(unittest.TestCase):
    """Safeguards for incomplete paging and mutation proofs."""

    def test_extra_a_page_2_500_unavailable_keeps_gate(self):
        with tempfile.TemporaryDirectory(prefix="cg-comp-") as td:
            tmp = Path(td)
            nb = ("$all = [IO.File]::ReadAllText($env:FAKE_ROWS) | ConvertFrom-Json\n"
                  "if ($env:AUTOS_OFFSET -eq '500') { Write-Output \"500`n[]\"; exit 0 }\n"
                  "$slice = @($all | Select-Object -First 500)\n"
                  "$b = ConvertTo-Json -InputObject $slice -Depth 5 -Compress\n"
                  "Write-Output (\"200`n\" + $b)\nexit 0\n")
            fakes = _setup_fakes(tmp, _make_rows(1000), node_script_body=nb)
            autoos_dir = fakes["local_dir"] / "autoos"
            autoos_dir.mkdir(parents=True, exist_ok=True)
            gf = autoos_dir / "daily-gate.json"
            prior_bytes = b'{"prior": true, "verdict": "ok"}'
            gf.write_bytes(prior_bytes)
            res = _run_driver(tmp, fakes, culture="de-DE")
            self.assertEqual(res.returncode, 3)
            self.assertEqual(gf.read_bytes(), prior_bytes)
            sf = autoos_dir / "daily-gate.status"
            self.assertTrue(sf.exists())
            stxt = sf.read_text(encoding="utf-8").strip()
            self.assertTrue(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", stxt))
            self.assertIn("UNAVAILABLE: rows export incomplete", stxt)
            self.assertIn("page 2 failed: 500", stxt)

    def test_extra_b_unparseable_timestamp_unavailable(self):
        with tempfile.TemporaryDirectory(prefix="cg-comp-") as td:
            tmp = Path(td)
            rows = _make_rows(5)
            rows[1]["timestamp"] = "not-a-time"
            fakes = _setup_fakes(tmp, rows)
            autoos_dir = fakes["local_dir"] / "autoos"
            autoos_dir.mkdir(parents=True, exist_ok=True)
            gf = autoos_dir / "daily-gate.json"
            prior_bytes = b'{"preserved": 1}'
            gf.write_bytes(prior_bytes)
            res = _run_driver(tmp, fakes, culture="de-DE")
            self.assertEqual(res.returncode, 3)
            self.assertEqual(gf.read_bytes(), prior_bytes)
            stxt = (autoos_dir / "daily-gate.status").read_text(encoding="utf-8").strip()
            self.assertIn("UNAVAILABLE: rows export incomplete", stxt)
            self.assertIn("unreadable timestamp on page 1", stxt)

    def test_extra_c_original_de_de_bug_caught_by_raw_cross_check(self):
        with tempfile.TemporaryDirectory(prefix="cg-comp-") as td:
            tmp = Path(td)
            fakes = _setup_fakes(tmp, _make_rows(1500))
            orig_txt = WRAPPER.read_text(encoding="utf-8")
            old_block = ("            if (-not $r.PSObject.Properties['timestamp']) { continue }\n"
                         "            $stamp = [DateTime]::MinValue\n"
                         "            if (-not [DateTime]::TryParse($r.timestamp, [ref]$stamp)) { continue }\n"
                         "            if ($stamp.Kind -ne [System.DateTimeKind]::Utc) { $stamp = $stamp.ToUniversalTime() }\n"
                         "            if ($null -eq $oldest -or $stamp -lt $oldest) { $oldest = $stamp }\n"
                         "            $readCount++")
            target = re.search(r"foreach \(\$r in \$pageRows\) \{.*?\$readCount\+\+\s*\}", orig_txt, re.DOTALL).group(0)
            mut_txt = orig_txt.replace(target, f"foreach ($r in $pageRows) {{\n{old_block}\n        }}")
            wrap_mut = tmp / "wrapper-buggy.ps1"
            wrap_mut.write_text(mut_txt, encoding="utf-8")
            res = _run_driver(tmp, fakes, culture="de-DE", wrapper_path=wrap_mut)
            self.assertEqual(res.returncode, 3)
            sf = fakes["local_dir"] / "autoos" / "daily-gate.status"
            self.assertTrue(sf.exists())
            stxt = sf.read_text(encoding="utf-8").strip()
            self.assertNotIn("verdict=ok", stxt)
            self.assertIn("UNAVAILABLE: rows export incomplete", stxt)
            # day of the month decides HOW the old block misreads (a day <= 12 parses as a wrong date -> raw cross-check,
            # a later day fails to parse -> unreadable timestamp): both must surface as an incomplete export
            self.assertNotIn("verdict=ok", stxt)

    def test_extra_e_page_cap_reached_unavailable(self):
        with tempfile.TemporaryDirectory(prefix="cg-comp-") as td:
            tmp = Path(td)
            nb = ("$slice = @(1..500 | ForEach-Object { [pscustomobject]@{ id = \"r$_\"; "
                  "timestamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ'); "
                  "model = 'm'; provider = 'p'; tokens = [pscustomobject]@{ in = 1; out = 1; cacheRead = 0 } } })\n"
                  "$b = ConvertTo-Json -InputObject $slice -Depth 5 -Compress\n"
                  "Write-Output (\"200`n\" + $b)\nexit 0\n")
            fakes = _setup_fakes(tmp, [], node_script_body=nb)
            res = _run_driver(tmp, fakes, culture="de-DE")
            self.assertEqual(res.returncode, 3)
            sf = fakes["local_dir"] / "autoos" / "daily-gate.status"
            self.assertTrue(sf.exists())
            stxt = sf.read_text(encoding="utf-8").strip()
            self.assertIn("UNAVAILABLE: rows export incomplete: page cap reached", stxt)


if __name__ == "__main__":
    unittest.main()
