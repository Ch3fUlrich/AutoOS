#!/usr/bin/env python3
"""Tests for the pre-push shim that hands git's ref lines to the gate
(AO-PREPUSH-HOOK-GITENV-2). Split from tests/test_prepush_hook.py (AO-400LOC); the
fixture and the real-config-unchanged module check are shared from there. Run directly:

    python3 tests/test_prepush_shim.py
"""
import os
import runpy
import shutil
import subprocess
import sys
import unittest

from test_prepush import run_git
from test_prepush_hook import GateFixture, TRUST, setUpModule, tearDownModule  # noqa: F401

class ShimTests(GateFixture):
    """The installed shim: stdin to the gate and the chained hook, and upgrades."""

    #: The v1 shim, as installed before this change (kept verbatim as test data).
    V1 = ("#!/bin/sh\n# autoos-prepush-gate \u2014 installed by .agents/skills/"
          "unattended-orchestration/trust_worktree.py\n"
          '"$py" "$gate" || exit 1\n[ -x "$chained" ] && exec "$chained" "$@"\nexit 0\n')

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.hooks = self.repo / ".git" / "hooks"

    def install(self):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        proc = subprocess.run([sys.executable, str(TRUST), str(self.repo), "--hook-only"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_the_shim_hands_git_stdin_to_the_gate_and_to_the_chained_hook(self):
        self.install()
        text = (self.hooks / "pre-push").read_text(encoding="utf-8")
        self.assertIn("autoos-prepush-gate v3", text)
        self.assertRegex(text, r'\| "\$py" "\$gate" --hook-stdin \|\| exit \$\?')
        self.assertRegex(text, r'\| "\$chained" "\$@"')
        self.assertNotIn("exec", text.replace("# ", ""), "the chained hook lost its stdin")

    def test_an_old_shim_is_upgraded_in_place_and_is_never_chained(self):
        self.hooks.mkdir(exist_ok=True)
        (self.hooks / "pre-push").write_text(self.V1, encoding="utf-8", newline="\n")
        out = self.install()
        self.assertIn("upgraded", out)
        self.assertIn("unversioned -> v3", out)
        self.assertNotIn("v1", out)
        self.assertIn("--hook-stdin", (self.hooks / "pre-push").read_text(encoding="utf-8"))
        self.assertFalse((self.hooks / "pre-push.autoos-chained").exists())
        self.assertIn("skipped", self.install())

    def test_v2_is_replaced_on_reinstall_without_chaining(self):
        self.install()
        hook = self.hooks / "pre-push"
        old = hook.read_text(encoding="utf-8").replace("gate v3", "gate v2")
        old = old.replace('--hook-stdin || exit $?', '--hook-stdin || exit 1')
        hook.write_text(old, encoding="utf-8", newline="\n")
        self.assertIn("v2 -> v3", self.install())
        text = hook.read_text(encoding="utf-8")
        self.assertNotEqual(text, old)
        self.assertIn("autoos-prepush-gate v3", text)
        self.assertIn("|| exit $?", text)
        gate_lines = [line for line in text.splitlines() if "--hook-stdin" in line]
        self.assertEqual(len(gate_lines), 1)
        self.assertNotIn("|| exit 1", gate_lines[0])
        self.assertFalse((self.hooks / "pre-push.autoos-chained").exists())

    def test_check_reports_v2_hook_without_writing(self):
        self.install()
        hook = self.hooks / "pre-push"
        old = hook.read_bytes().replace(b"gate v3", b"gate v2")
        old = old.replace(b"--hook-stdin || exit $?", b"--hook-stdin || exit 1")
        hook.write_bytes(old)
        self.assertIn(b"gate v2", hook.read_bytes())
        listing = sorted(path.name for path in self.hooks.iterdir())
        install_prepush_hook = runpy.run_path(str(TRUST))["install_prepush_hook"]
        report = install_prepush_hook(self.repo, check=True)
        self.assertIn(str(hook), report)
        self.assertNotIn("already installed", report)
        self.assertEqual(hook.read_bytes(), old)
        self.assertEqual(sorted(path.name for path in self.hooks.iterdir()), listing)

    def test_either_missing_gate_module_installs_nothing(self):
        for name in ("prepush.py", "prepush_hook.py"):
            with self.subTest(name=name):
                path = self.repo / "tools" / name
                saved = path.read_bytes()
                path.unlink()
                try:
                    self.assertIn("no tools/" + name, self.install())
                    self.assertFalse((self.hooks / "pre-push").exists())
                finally:
                    path.write_bytes(saved)
        self.assertIn("gate: installed", self.install())

    @unittest.skipUnless(shutil.which("sh"), "no POSIX sh on PATH")
    def test_installed_shim_preserves_gate_exit_codes(self):
        self.install()
        zero = "0" * 40
        for text, expected in (("malformed ref\n", 2),
                               ("refs/heads/other %s refs/heads/other %s\n" % (self.x, zero), 1),
                               ("refs/heads/lane %s refs/heads/lane %s\n" % (self.y, zero), 0)):
            with self.subTest(expected=expected):
                proc = subprocess.run([shutil.which("sh"), str(self.hooks / "pre-push")],
                                      cwd=self.repo, input=text, capture_output=True,
                                      text=True, env=self.base_env())
                self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)

    @unittest.skipIf(os.name == "nt", "the shim is an sh script; git runs it through a POSIX sh")
    def test_a_real_push_is_gated_per_sha_and_the_chained_hook_still_gets_the_refs(self):
        origin = self.tmp / "origin.git"
        run_git("init", "-q", "--bare", str(origin))
        seen = self.tmp / "chained-stdin.txt"
        self.hooks.mkdir(exist_ok=True)
        theirs = self.hooks / "pre-push"
        theirs.write_text("#!/bin/sh\ncat > %s\nexit 0\n" % seen, encoding="utf-8")
        theirs.chmod(0o755)
        self.install()

        def push(ref):
            proc = subprocess.run(["git", "-C", str(self.repo), "push", str(origin), ref],
                                  capture_output=True, text=True, env=self.base_env())
            return proc.returncode, proc.stdout + proc.stderr

        rc, out = push("other:refs/heads/other")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("run prepush on a checkout of %s first" % self.x, out)
        self.assertEqual(run_git("--git-dir", str(origin), "for-each-ref", cwd=self.repo), "")
        self.assertFalse(seen.exists(), "the chained hook ran after a refusal")
        self.certify(self.x)
        rc, out = push("other:refs/heads/other")
        self.assertEqual(rc, 0, out)
        self.assertIn("refs/heads/other %s refs/heads/other" % self.x,
                      seen.read_text(encoding="utf-8"))
        self.assertIn(self.x, run_git("--git-dir", str(origin), "for-each-ref", cwd=self.repo))


if __name__ == "__main__":
    unittest.main(verbosity=2)
