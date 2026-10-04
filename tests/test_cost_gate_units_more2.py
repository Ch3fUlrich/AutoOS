"""Additional unit tests for cost gate threshold validation and refusals.

Asserts that lib/linux/cost-gate.sh refuses --warn >= --block and
non-positive numbers with exit 1 and writes nothing.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib" / "linux"
INSTALLER = LIB / "cost-gate.sh"

BASH = shutil.which("bash")
if os.name == "nt":
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        BASH = str(git_bash)
    else:
        BASH = None

if BASH:
    try:
        if subprocess.run([BASH, "-c", "exit 0"], capture_output=True).returncode != 0:
            BASH = None
    except Exception:
        BASH = None

STUB = "#!/bin/bash\nexit 0\n"


@unittest.skipIf(BASH is None, "bash not available; POSIX installer test skipped")
class TestCostGateThresholdRefusals(unittest.TestCase):
    """The Linux installer must refuse --warn >= --block and non-positive thresholds."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.box = Path(self._tmp.name)
        self.home = self.box / "home"
        self.config = self.box / "config"
        self.bindir = self.box / "bin"
        self.home.mkdir(parents=True)
        self.bindir.mkdir(parents=True)
        stub = self.bindir / "systemctl"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)

        # Fake repo with unit templates in POSIX layout
        self.fake_repo = self.box / "repo"
        units = self.fake_repo / "lib" / "linux" / "systemd" / "user"
        units.mkdir(parents=True)
        for u in ("cost-gate.service", "cost-gate.timer"):
            src = LIB / "systemd" / "user" / u
            if src.exists():
                shutil.copyfile(src, units / u)
            else:
                (units / u).write_text("# unit\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        env = os.environ.copy()
        env["HOME"] = self.home.as_posix()
        env["XDG_CONFIG_HOME"] = self.config.as_posix()
        env["PATH"] = self.bindir.as_posix() + os.pathsep + env["PATH"]
        env["AUTOOS_ROOT"] = self.fake_repo.as_posix()
        env.pop("AUTOOS_DRY_RUN", None)
        env.pop("AUTOOS_COST_GATE_ENABLE", None)
        return subprocess.run([BASH, INSTALLER.as_posix()] + list(args),
                              env=env, capture_output=True, text=True)

    def _assert_nothing_written(self):
        conf = self.config / "autoos" / "daily-gate.conf"
        self.assertFalse(conf.exists(), "config file was written despite refusal")
        units_dir = self.config / "systemd" / "user"
        self.assertFalse((units_dir / "cost-gate.service").exists(), "service written despite refusal")
        self.assertFalse((units_dir / "cost-gate.timer").exists(), "timer written despite refusal")

    def test_warn_greater_than_block_refused(self):
        proc = self._run("--warn", "8", "--block", "5")
        self.assertEqual(proc.returncode, 1, f"expected exit 1: {proc.stdout}\n{proc.stderr}")
        self.assertIn("greater than", proc.stderr)
        self._assert_nothing_written()

    def test_warn_equal_to_block_refused(self):
        proc = self._run("--warn", "8", "--block", "8")
        self.assertEqual(proc.returncode, 1, f"expected exit 1: {proc.stdout}\n{proc.stderr}")
        self.assertIn("greater than", proc.stderr)
        self._assert_nothing_written()

    def test_warn_zero_refused(self):
        proc = self._run("--warn", "0", "--block", "5")
        self.assertEqual(proc.returncode, 1, f"expected exit 1: {proc.stdout}\n{proc.stderr}")
        self.assertIn("greater than zero", proc.stderr)
        self._assert_nothing_written()

    def test_valid_thresholds_install(self):
        proc = self._run("--warn", "8", "--block", "10")
        self.assertEqual(proc.returncode, 0, f"expected exit 0: {proc.stdout}\n{proc.stderr}")
        conf = self.config / "autoos" / "daily-gate.conf"
        self.assertTrue(conf.exists(), "daily-gate.conf was not written")
        content = conf.read_text(encoding="utf-8")
        self.assertIn("warn=8\n", content)
        self.assertIn("block=10\n", content)


if __name__ == "__main__":
    unittest.main()
