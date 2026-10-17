#!/usr/bin/env python3
"""Additional static + shell-level checks for the Linux cost-gate installer and units.

Run:  python tests/test_cost_gate_units_more.py
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


class PathSafety(unittest.TestCase):
    """The checkout root lands in a systemd ExecStart; unsafe characters are
    refused before anything is written rather than rendered and mangled.
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

    def _build_fake_repo(self, name):
        root = self.box / name
        units = root / "lib" / "linux" / "systemd" / "user"
        units.mkdir(parents=True)
        for u in ("cost-gate.service", "cost-gate.timer"):
            shutil.copyfile(LIB / "systemd" / "user" / u, units / u)
        return root

    def _run(self, root):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.config)
        env["CG_CALLS"] = str(self.calls)
        env["PATH"] = str(self.bindir) + os.pathsep + env["PATH"]
        env["AUTOOS_ROOT"] = str(root)
        env.pop("AUTOOS_DRY_RUN", None)
        env.pop("AUTOOS_COST_GATE_ENABLE", None)
        return subprocess.run([BASH, str(INSTALLER)], env=env,
                               capture_output=True, text=True)

    def _nothing_written(self):
        d = self.config / "systemd" / "user"
        self.assertFalse((d / "cost-gate.service").exists(), "service written despite refusal")
        self.assertFalse((d / "cost-gate.timer").exists(), "timer written despite refusal")
        self.assertFalse((self.config / "autoos" / "daily-gate.conf").exists(),
                         "config written despite refusal")

    def test_spaced_checkout_root_is_refused(self):
        root = self._build_fake_repo("my checkout")
        proc = self._run(root)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("refus", (proc.stdout + proc.stderr).lower())
        self._nothing_written()

    def test_ampersand_checkout_root_is_refused(self):
        root = self._build_fake_repo("amp&x")
        proc = self._run(root)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._nothing_written()

    def test_hash_checkout_root_is_refused(self):
        root = self._build_fake_repo("hash#x")
        proc = self._run(root)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._nothing_written()

    @unittest.skipIf(os.name == "nt", "a backslash is not a legal directory name character on Windows")
    def test_backslash_checkout_root_is_refused(self):
        root = self._build_fake_repo("back" + chr(92) + "slash")
        proc = self._run(root)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("refus", (proc.stdout + proc.stderr).lower())
        self._nothing_written()


class DetectLogic(unittest.TestCase):
    """The cost-gate detection branch must require BOTH unit files.
    
    Mirrors the memguard/taildrop convention: one half installed without the
    other is a half-install a rerun should repair, so only the pair counts as
    installed (and silently skips).
    """

    def _installed(self, fake_home, create=("service", "timer")):
        d = Path(fake_home) / ".config" / "systemd" / "user"
        d.mkdir(parents=True, exist_ok=True)
        if "service" in create:
            (d / "cost-gate.service").write_text("# stub\n", encoding="utf-8")
        if "timer" in create:
            (d / "cost-gate.timer").write_text("# stub\n", encoding="utf-8")
        script = "source '" + (LIB / "detect.sh").as_posix() + "'\nhas_bin(){ return 1; }\n"
        env = os.environ.copy()
        env["SYS_HOME"] = str(fake_home)
        return subprocess.run([BASH, "-c", script + "\nscript_is_installed cost-gate"],
                               env=env, capture_output=True, text=True)

    def test_detect_requires_both_unit_files(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            home = str(Path(tmp.name) / "home")
            # only the service: a half-install, must NOT count as installed
            p = self._installed(home, create=("service",))
            self.assertNotEqual(p.returncode, 0,
                                f"service alone counted as installed: {p.stdout}")
            # only the timer: likewise a half-install
            p = self._installed(home + "-timer", create=("timer",))
            self.assertNotEqual(p.returncode, 0,
                                f"timer alone counted as installed: {p.stdout}")
            # both halves: this is the installed state
            p = self._installed(home + "-both", create=("service", "timer"))
            self.assertEqual(p.returncode, 0,
                             f"both halves should count as installed: {p.stderr}")
        finally:
            tmp.cleanup()


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