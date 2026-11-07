"""Hermetic Linux-only checks for the print-only WSL gate route."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/wsl-push-route.sh"


@unittest.skipIf(os.name == "nt", "WSL/Linux-only bash script; run these tests in WSL")
class WslPushRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="route-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
                        GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_EMAIL="fixture@example.invalid",
                        GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
        self.origin = self.root / "origin"
        self.source = self.root / "source"
        self.verify = self.root / "verify"
        self.git("init", "--bare", "--initial-branch=main", str(self.origin))
        self.git("init", "--initial-branch=main", str(self.source))
        tools = self.source / "tools"
        tools.mkdir()
        (tools / "prepush.py").write_text(
            'import os, pathlib, sys\n'
            'state = pathlib.Path(os.environ["ROUTE_STATE"])\n'
            'if "--check-ready" in sys.argv:\n'
            '    sys.exit(0 if state.exists() else 1)\n'
            'count = pathlib.Path(os.environ["ROUTE_COUNT"])\n'
            'count.write_text(count.read_text() + "gate\\n" if count.exists() else "gate\\n")\n'
            'if os.environ.get("ROUTE_RED"): sys.exit(7)\n'
            'if not os.environ.get("ROUTE_NOT_READY"): state.touch()\n', encoding="utf-8")
        self.commit("base")
        self.git("remote", "add", "origin", str(self.origin), cwd=self.source)
        # Seed the bare fixture without ever invoking the forbidden subcommand.
        self.git("fetch", str(self.source), "main:refs/heads/main", cwd=self.origin)
        self.git("clone", str(self.origin), str(self.verify))
        (self.source / "change").write_text("candidate\n", encoding="utf-8")
        self.sha = self.commit("candidate")
        self.env.update(ROUTE_STATE=str(self.root / "state"),
                        ROUTE_COUNT=str(self.root / "count"), TMPDIR=str(self.root))
        self.before = self.git("worktree", "list", "--porcelain", cwd=self.verify)
        self.head = self.git("rev-parse", "HEAD", cwd=self.verify)
        self.origin_head = self.git("rev-parse", "main", cwd=self.origin)
        self.real_git = shutil.which("git")
        shim = self.root / "bin"
        shim.mkdir()
        self.trace = self.root / "trace"
        (shim / "git").write_text(
            '#!/usr/bin/env python3\nimport os, sys\n'
            'with open(os.environ["ROUTE_TRACE"], "a") as f: f.write(repr(sys.argv[1:]) + "\\n")\n'
            'if "push" in sys.argv[1:]: sys.exit(97)\n'
            'os.execv(os.environ["ROUTE_GIT"], ["git"] + sys.argv[1:])\n', encoding="utf-8")
        (shim / "git").chmod(0o755)
        self.env.update(PATH=str(shim) + os.pathsep + self.env["PATH"],
                        ROUTE_TRACE=str(self.trace), ROUTE_GIT=self.real_git)

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.root,
                                       env=self.env, stderr=subprocess.PIPE, text=True).strip()

    def commit(self, message):
        self.git("add", ".", cwd=self.source)
        self.git("commit", "-m", message, cwd=self.source)
        return self.git("rev-parse", "HEAD", cwd=self.source)

    def route(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *(args or
                              (self.sha, str(self.source), str(self.verify)))],
                              env=self.env, capture_output=True, text=True)

    def assert_clean(self):
        self.assertEqual(self.git("worktree", "list", "--porcelain", cwd=self.verify), self.before)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.verify), self.head)
        self.assertFalse(list(self.root.glob("wsl-route.????????")))
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin), self.origin_head)

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("push", result.stdout)
        self.assert_clean()

    def test_success_and_second_run_never_invoke_push(self):
        expected = (f"MSYS_NO_PATHCONV=1 wsl.exe -- git -C {self.verify} "
                    f"push origin {self.sha}:refs/heads/main")
        for second in (False, True):
            result = self.route()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[-1], expected)
            self.assertEqual(sum("push" in line for line in result.stdout.splitlines()), 2)
            if second:
                self.assertIn(f"skipped: {self.sha} already has a green record", result.stdout)
            self.assert_clean()
        self.assertEqual((self.root / "count").read_text(), "gate\n")
        self.assertNotIn("'push'", self.trace.read_text())

    def test_red_gate(self):
        self.env["ROUTE_RED"] = "1"
        result = self.route()
        self.assert_refused(result)
        self.assertIn("log:", result.stderr)
        log = result.stdout.strip().removeprefix("gate log: ")
        self.assertTrue(Path(log).is_file())

    def test_green_without_ready_record(self):
        self.env["ROUTE_NOT_READY"] = "1"
        self.assert_refused(self.route())
        self.assertEqual((self.root / "count").read_text(), "gate\n")

    def test_behind_and_diverged_refused_before_gate(self):
        for diverged in (False, True):
            with self.subTest(diverged=diverged):
                old = self.sha
                if diverged:
                    self.git("reset", "--hard", self.origin_head, cwd=self.source)
                (self.source / "advance").write_text(str(diverged), encoding="utf-8")
                self.commit("advance")
                self.git("fetch", str(self.source), "main:refs/heads/main", cwd=self.origin)
                self.origin_head = self.git("rev-parse", "main", cwd=self.origin)
                self.assert_refused(self.route(old, str(self.source), str(self.verify)))
                self.assertFalse((self.root / "count").exists())

    def test_invalid_arguments_and_repositories(self):
        for args in [("bad",), ("A" * 40, str(self.source), str(self.verify)),
                     (self.sha, str(self.root / "missing"), str(self.verify)),
                     (self.sha, str(self.source), str(self.root / "missing")),
                     (self.sha, str(self.source), str(self.verify), "extra")]:
            with self.subTest(args=args):
                result = self.route(*args)
                self.assert_refused(result)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(result.stderr.splitlines()), 1)
        result = subprocess.run(["bash", str(SCRIPT)], env=self.env,
                                capture_output=True, text=True)
        self.assert_refused(result)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_unservable_sha(self):
        sha = "0" * 40
        result = self.route(sha, str(self.source), str(self.verify))
        self.assert_refused(result)
        self.assertIn(sha + ": source will not serve", result.stderr)
        self.assertFalse((self.root / "count").exists())

    def test_static_no_executable_push(self):
        for line in SCRIPT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if re.search(r"\bpush\b", line):
                self.assertTrue(line.startswith(("printf ", "echo ")), line)


if __name__ == "__main__":
    unittest.main()
