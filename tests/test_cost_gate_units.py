#!/usr/bin/env python3
"""Static + shell-level checks for the Linux cost-gate installer and units.

Run:  python tests/test_cost_gate_units.py

The static part inspects the two unit templates, the catalog entry and the
installer/detect registration. The shell-level part drives
lib/linux/cost-gate.sh in a scratch HOME/XDG tree with a stub `systemctl` on
PATH that records every call it is given; it is skipped when bash is not
available (e.g. Windows CI without a POSIX shell).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib" / "linux"
CATALOG = REPO / "catalog" / "linux.json"
SERVICE = LIB / "systemd" / "user" / "cost-gate.service"
TIMER = LIB / "systemd" / "user" / "cost-gate.timer"
INSTALLER = LIB / "cost-gate.sh"
REFRESH = "tools/cost-gate-refresh.py"
BASH = shutil.which("bash")
SHELLCHECK = shutil.which("shellcheck")

# The stub systemctl records every argv it is called with and always succeeds:
# the installer never runs a real systemctl, so a call here is a recorded
# observation, not a side effect. The shebang is #!/bin/bash (not an env form)
# so the Windows-portability lint does not flag it as a POSIX-only stub.
STUB = (
    "#!/bin/bash\n"
    "printf '%s\\n' \"$*\" >> \"$CG_CALLS\"\n"
    "exit 0\n"
)


def _components():
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    for group in data["categories"]:
        for comp in group["components"]:
            yield comp


def _cost_gate():
    return [c for c in _components() if c.get("id") == "cost-gate"]


class UnitStructure(unittest.TestCase):
    """Shape guarantees a systemd user unit must keep to stay enable-safe."""

    def test_service_has_no_install_section(self):
        # Only the timer is ever enabled; a service [Install] would let an
        # operator enable the oneshot directly and bypass the timer cadence.
        lines = SERVICE.read_text(encoding="utf-8").splitlines()
        self.assertNotIn("[Install]", lines, "service must not carry an [Install] section")

    def test_timer_persistent_false_and_boot_delay(self):
        lines = TIMER.read_text(encoding="utf-8").splitlines()
        self.assertIn("Persistent=false", lines)
        self.assertIn("OnBootSec=2min", lines)

    def test_timer_comment_matches_onbootsec(self):
        # OnBootSec is two minutes; the header comment must say so, not a
        # stale "ten minutes after boot".
        text = TIMER.read_text(encoding="utf-8")
        self.assertIn("OnBootSec=2min", text)
        self.assertNotIn("ten minutes after boot", text,
                         "timer comment claims a 10-minute boot delay but OnBootSec=2min")


class CatalogEntry(unittest.TestCase):
    def test_cost_gate_entry_present_exactly_once(self):
        self.assertEqual(len(_cost_gate()), 1)

    def test_cost_gate_entry_fields(self):
        entries = _cost_gate()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["name"], "Cost gate refresh")
        self.assertEqual(e["provider"], "script")
        self.assertEqual(e["package"], "cost-gate")
        self.assertEqual(e["launcher"], "none")
        self.assertEqual(e["profiles"], ["workstation", "server"])
        self.assertTrue(e["description"])
        self.assertIn("http", e["homepage"])
        self.assertTrue(e["notes"])

    def test_catalog_still_valid_json_with_unique_ids(self):
        json.loads(CATALOG.read_text(encoding="utf-8"))  # raises on invalid JSON
        ids = [c["id"] for c in _components()]
        self.assertEqual(len(ids), len(set(ids)), "duplicate component ids")


class Registration(unittest.TestCase):
    def test_install_sh_registers_cost_gate(self):
        text = (LIB / "install.sh").read_text(encoding="utf-8")
        self.assertRegex(text, r"cost-gate\)\s*install_cost_gate\b")
        self.assertRegex(text, r"install_cost_gate\(\)\s*\{")

    def test_detect_sh_registers_cost_gate(self):
        text = (LIB / "detect.sh").read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"cost-gate\)\s*\[\[ -f \"\$SYS_HOME/\.config/systemd/user/cost-gate\.service\"")


@unittest.skipIf(BASH is None, "bash not available; POSIX installer test skipped")
class InstallerBehaviour(unittest.TestCase):
    """Run install_cost_gate against a scratch tree with a stub systemctl."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.box = Path(self._tmp.name)
        self.home = self.box / "home"
        self.config = self.box / "config"
        self.state = self.box / "state"
        self.bindir = self.box / "bin"
        self.calls = self.box / "calls"
        self.home.mkdir(parents=True)
        self.bindir.mkdir(parents=True)
        stub = self.bindir / "systemctl"
        stub.write_text(STUB, encoding="utf-8")
        # `command -v` needs the exec bit on POSIX; on Windows the filesystem
        # already reports the file as executable, so this is a harmless no-op.
        stub.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, args=(), extra_env=None):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.config)
        env["XDG_STATE_HOME"] = str(self.state)
        env["AUTOOS_DRY_RUN"] = "0"
        env["CG_CALLS"] = str(self.calls)
        env["PATH"] = str(self.bindir) + os.pathsep + env["PATH"]
        env.pop("AUTOOS_COST_GATE_ENABLE", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run([BASH, str(INSTALLER)] + list(args), env=env,
                              capture_output=True, text=True)

    def _svc_tmr(self):
        d = self.config / "systemd" / "user"
        return d / "cost-gate.service", d / "cost-gate.timer"

    def _conf(self):
        return self.config / "autoos" / "daily-gate.conf"

    def _calls(self):
        if not self.calls.exists():
            return []
        return [l for l in self.calls.read_text(encoding="utf-8").splitlines() if l]

    def _snapshot(self):
        snap = {}
        for p in self._svc_tmr() + (self._conf(),):
            if p.exists():
                snap[str(p)] = (p.read_bytes(), p.stat().st_mtime_ns)
        return snap

    def test_first_run_creates_units_and_config(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        svc, tmr = self._svc_tmr()
        self.assertTrue(svc.is_file())
        self.assertTrue(tmr.is_file())
        conf = self._conf()
        self.assertTrue(conf.is_file())
        self.assertEqual(conf.read_text(encoding="utf-8"), "warn=20\nblock=25\n")
        rendered = svc.read_text(encoding="utf-8")
        self.assertNotIn("@APPROOT@", rendered, "placeholder left unrendered")
        self.assertIn(REFRESH, rendered)
        self.assertIn("--gateway", rendered)

    def test_second_run_reports_skipped_and_changes_nothing(self):
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        snap = self._snapshot()
        self.assertTrue(snap, "first run created nothing")
        self.calls.write_text("", encoding="utf-8")  # drop run-one's calls
        second = self._run()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("skipped", second.stdout)
        self.assertEqual(snap, self._snapshot(), "second run changed a file")
        backups = list((self.config / "systemd" / "user").glob("*.autoos-backup-*"))
        self.assertEqual(backups, [], "second run churned a backup")
        calls2 = self._calls()
        self.assertFalse(any("daemon-reload" in c for c in calls2), calls2)
        self.assertFalse(any("enable" in c for c in calls2), calls2)

    def test_existing_config_never_overwritten(self):
        conf = self._conf()
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("warn=99\nblock=100\n", encoding="utf-8")
        before = conf.read_bytes()
        mt = conf.stat().st_mtime_ns
        proc = self._run(["--warn", "1", "--block", "2"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(conf.read_bytes(), before, "existing config was rewritten")
        self.assertEqual(conf.stat().st_mtime_ns, mt, "existing config mtime changed")
        self.assertEqual(conf.read_text(encoding="utf-8"), "warn=99\nblock=100\n")

    def test_warn_block_arguments_are_written_when_config_absent(self):
        proc = self._run(["--warn", "12", "--block", "15"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._conf().read_text(encoding="utf-8"), "warn=12\nblock=15\n")

    def test_non_numeric_arguments_write_nothing(self):
        for args in (["--warn", "abc", "--block", "15"],
                     ["--warn", "12", "--block", "1.5"],
                     ["--warn"]):
            proc = self._run(args)
            self.assertNotEqual(proc.returncode, 0,
                                f"args={args} should fail: {proc.stdout}")
            self.assertFalse(self._conf().exists(), f"config created for args={args}")
            svc, tmr = self._svc_tmr()
            self.assertFalse(svc.exists(), f"service created for args={args}")
            self.assertFalse(tmr.exists(), f"timer created for args={args}")

    def test_enable_only_when_env_set(self):
        self._run()
        default_calls = self._calls()
        self.assertFalse(any("enable" in c or "start" in c for c in default_calls),
                         f"enable/start without opt-in: {default_calls}")
        self.calls.write_text("", encoding="utf-8")
        proc = self._run(extra_env={"AUTOOS_COST_GATE_ENABLE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        enable_calls = self._calls()
        self.assertTrue(any("enable" in c and "cost-gate.timer" in c
                            for c in enable_calls),
                        f"expected an enable call, got {enable_calls}")


@unittest.skipIf(BASH is None, "bash not available; POSIX installer test skipped")
class StandaloneDryRunDefault(unittest.TestCase):
    """Running cost-gate.sh directly (not via install.sh) must not crash.

    install.sh sets AUTOOS_DRY_RUN before sourcing this file; a direct run
    must default it too, or `set -u` aborts at the first `(( AUTOOS_DRY_RUN ))`.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.box = Path(self._tmp.name)
        self.home = self.box / "home"
        self.config = self.box / "config"
        self.bindir = self.box / "bin"
        self.calls = self.box / "calls"
        self.home.mkdir(parents=True)
        self.bindir.mkdir(parents=True)
        stub = self.bindir / "systemctl"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, args=(), extra_env=None):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.config)
        env["CG_CALLS"] = str(self.calls)
        env["PATH"] = str(self.bindir) + os.pathsep + env["PATH"]
        # The point of this test: AUTOOS_DRY_RUN is NOT pre-set, so the
        # installer's own default must supply it.
        env.pop("AUTOOS_DRY_RUN", None)
        env.pop("AUTOOS_COST_GATE_ENABLE", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run([BASH, str(INSTALLER)] + list(args), env=env,
                              capture_output=True, text=True)

    def test_standalone_with_dry_run_unset_creates_config(self):
        proc = self._run(["--warn", "8", "--block", "10"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        conf = self.config / "autoos" / "daily-gate.conf"
        self.assertTrue(conf.is_file(), "standalone run did not create the config")
        self.assertEqual(conf.read_text(encoding="utf-8"), "warn=8\nblock=10\n")
        svc = self.config / "systemd" / "user" / "cost-gate.service"
        tmr = self.config / "systemd" / "user" / "cost-gate.timer"
        self.assertTrue(svc.is_file())
        self.assertTrue(tmr.is_file())


class Shellcheck(unittest.TestCase):
    def test_cost_gate_sh_lints_clean_at_warning(self):
        if SHELLCHECK is None:
            self.skipTest("shellcheck not installed")
        proc = subprocess.run(
            [SHELLCHECK, "-S", "warning", str(INSTALLER)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "shellcheck -S warning found issues:\n" + proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
