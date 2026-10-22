"""Portable dry-run contract for the Windows-to-WSL push route."""
import pathlib
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is absent")
class WslPushTests(unittest.TestCase):
    def run_script(self, sha="a" * 40):
        return subprocess.run(
            ["pwsh", "-NoProfile", "-File",
             str(pathlib.Path(__file__).resolve().parents[1] / "tools/wsl-push.ps1"),
             "-Sha", sha, "-WindowsClone", r"C:\x\y",
             "-WslClone", "/example/autoos", "-DryRun"],
            capture_output=True, text=True, timeout=30,
        )

    def test_short_sha_refused(self):
        self.assertNotEqual(self.run_script("abc123").returncode, 0)

    def test_non_hex_refused(self):
        self.assertNotEqual(self.run_script("z" * 40).returncode, 0)

    def test_route_and_conversion(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        for token in ("prepush.py --repo .", "--check-ready", "refs/heads/main",
                      "/mnt/c/x/y", "merge-base --is-ancestor", "trap cleanup EXIT"):
            self.assertIn(token, result.stdout)
        for token in ("--no-verify", "AUTOOS_PREPUSH_OVERRIDE", "--force"):
            self.assertNotIn(token, result.stdout)

    def test_repeat_identical(self):
        first, second = self.run_script(), self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
