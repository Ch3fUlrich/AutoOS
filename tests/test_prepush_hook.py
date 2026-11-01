#!/usr/bin/env python3
"""Tests for the pre-push gate's push-time refusals (AO-PREPUSH-HOOK-GITENV-2).

Two failures, both measured:

  * 2026-10-12: the `risk-test` identity of tests/test_autoos_risk.py turned up in
    the REAL repo's config - a fixture identity write that inherited GIT_DIR. The gate now refuses a fixture identity in the repo-local (and
    worktree) config, and the tests no longer write one.
  * a push of 952af69d from a checkout at d3f7ab06 printed `green ... for
    d3f7ab06`: the hook gated the checkout's HEAD, not the pushed sha. With
    `--hook-stdin` the shim passes git's ref lines and each pushed sha is gated.

Hermetic: tmp repos, no network. Fixture identities are appended to the tmp
repo's own config FILE (never `git config user.*`), and the module asserts the
real repo's config is byte-identical after the suite. Run directly:

    python3 tests/test_prepush_hook.py
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_prepush import RepoFixture, ROOT, SCRIPT, run_git

sys.path.insert(0, str(ROOT / "tools"))
import prepush_hook as hook  # noqa: E402
import test_affected_tests  # noqa: E402
import test_autoos_risk  # noqa: E402

ZERO = "0" * 40
TRUST = ROOT / ".agents" / "skills" / "unattended-orchestration" / "trust_worktree.py"


def _real_config_files():
    """The real repo's config files this suite must never change."""
    files = []
    for flag in ("--git-common-dir", "--absolute-git-dir"):
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", flag],
                             capture_output=True, text=True)
        if out.returncode == 0:
            base = Path(ROOT, out.stdout.strip())
            files += [base / "config", base / "config.worktree"]
    return sorted(set(files))


def _snapshot():
    return {str(p): (p.read_bytes() if p.is_file() else None)
            for p in _real_config_files()}


_BEFORE = {}


def setUpModule():
    _BEFORE.update(_snapshot())


def tearDownModule():
    # Runs after the module's tests: the real config is what it was.
    after = _snapshot()
    changed = [name for name in _BEFORE if _BEFORE[name] != after.get(name)]
    if changed:
        raise AssertionError("the suite changed the real repo's config: %s" % changed)


class GateFixture(RepoFixture):
    """A repo that is green at HEAD (S) with a second commit X on branch `other`."""

    def setUp(self):
        super().setUp()
        for rel in ("tools/check-file-size.py", "tests/test_ci_shards.py",
                    "tests/ci-shards.py"):
            self._write(rel, "import sys\nsys.exit(0)\n")
        self.set_plan({})
        for name in ("prepush.py", "prepush_hook.py"):
            (self.repo / "tools" / name).write_bytes((SCRIPT.parent / name).read_bytes())
        run_git("add", "-A", cwd=self.repo)
        run_git("commit", "-q", "-m", "stubs", cwd=self.repo)
        self.lane = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.repo)
        self.y = self.head()
        run_git("switch", "-q", "-c", "other", cwd=self.repo)
        self._write("x.txt", "x\n")
        run_git("add", "x.txt", cwd=self.repo)
        run_git("commit", "-q", "-m", "x", cwd=self.repo)
        self.x = self.head()
        run_git("switch", "-q", self.lane, cwd=self.repo)

    def gate(self, *args, stdin=None, env=None):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo)]
                              + list(args), capture_output=True, text=True,
                              input=stdin, env=self.base_env(env), cwd=str(self.repo))
        return proc.returncode, proc.stdout + proc.stderr

    def hook(self, *lines, env=None):
        text = "".join("refs/heads/%s %s refs/heads/%s %s\n" % (ref, sha, ref, ZERO)
                       for ref, sha in lines)
        return self.gate("--hook-stdin", stdin=text, env=env)

    def certify(self, sha):
        """Run the gate on a checkout of `sha` (this repo, detached), then return."""
        run_git("switch", "-q", "--detach", sha, cwd=self.repo)
        rc, out = self.gate()
        run_git("switch", "-q", self.lane, cwd=self.repo)
        self.assertEqual(rc, 0, out)


class PushedShaTests(GateFixture):
    def test_push_of_x_from_a_checkout_at_green_y_is_refused(self):
        self.certify(self.y)
        self.assertEqual(self.head(), self.y)
        rc, out = self.hook(("other", self.x))
        self.assertEqual(rc, 1, out)
        self.assertIn(self.x, out)
        self.assertIn("run prepush on a checkout of %s first" % self.x, out)
        self.assertNotRegex(out, r"green.* for %s" % self.y[:12])

    def test_push_of_x_with_a_green_record_for_x_from_a_checkout_at_y_is_allowed(self):
        self.certify(self.x)
        rc, out = self.hook(("other", self.x))
        self.assertEqual(rc, 0, out)
        self.assertIn(self.x[:12], out)
        self.assertIn("a green gate record binds this commit and tree", out)
        self.assertNotIn("green -", out, "the HEAD gate ran for a sha that is not HEAD")

    def test_a_record_for_another_sha_does_not_certify_x(self):
        self.certify(self.y)
        rc, out = self.hook(("other", self.x), ("lane", self.y))
        self.assertEqual(rc, 1, out)
        self.assertIn("1 of 2 pushed sha(s)", out)
        self.assertIn("HEAD was not run", out)

    def test_a_pushed_head_runs_the_gate_as_today(self):
        rc, out = self.hook(("lane", self.y))
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"green\W+3 check\(s\) for %s" % self.y[:12])
        self.assertIsNotNone(self.store_record(self.y))

    def test_several_refs_each_checked_and_one_bad_refuses_the_push(self):
        self.certify(self.x)
        ok_rc, ok_out = self.hook(("other", self.x), ("lane", self.y))
        self.assertEqual(ok_rc, 0, ok_out)
        run_git("switch", "-q", "-c", "third", cwd=self.repo)
        (self.repo / "z.txt").write_text("z\n", encoding="utf-8")
        run_git("add", "z.txt", cwd=self.repo)
        run_git("commit", "-q", "-m", "z", cwd=self.repo)
        z = self.head()
        run_git("switch", "-q", self.lane, cwd=self.repo)
        rc, out = self.hook(("other", self.x), ("third", z), ("lane", self.y))
        self.assertEqual(rc, 1, out)
        self.assertIn(z, out)
        self.assertNotIn("REFUSED %s" % self.x, out)

    def test_a_zero_sha_deletion_is_skipped_and_reported(self):
        rc, out = self.gate("--hook-stdin", stdin="(delete) %s refs/heads/old %s\n"
                            % (ZERO, self.x))
        self.assertEqual(rc, 0, out)
        self.assertIn("ref deletion", out)
        self.assertIn("refs/heads/old", out)

    def test_a_deletion_beside_a_pushed_head_still_gates_the_head(self):
        text = ("(delete) %s refs/heads/old %s\nrefs/heads/lane %s refs/heads/lane %s\n"
                % (ZERO, self.x, self.y, ZERO))
        rc, out = self.gate("--hook-stdin", stdin=text)
        self.assertEqual(rc, 0, out)
        self.assertIn("ref deletion", out)
        self.assertIn("for %s" % self.y[:12], out)

    def test_an_annotated_tag_of_head_is_gated_as_head(self):
        run_git("tag", "-a", "-m", "t", "v1", cwd=self.repo)
        tag = run_git("rev-parse", "v1", cwd=self.repo)
        self.assertNotEqual(tag, self.y)
        rc, out = self.gate("--hook-stdin",
                            stdin="refs/tags/v1 %s refs/tags/v1 %s\n" % (tag, ZERO))
        self.assertEqual(rc, 0, out)
        self.assertIn("for %s" % self.y[:12], out)

    def test_zero_parsable_ref_lines_on_the_hook_path_is_could_not_run(self):
        for text in ("", "\n", "  \n\n"):
            rc, out = self.gate("--hook-stdin", stdin=text)
            self.assertEqual(rc, 2, out)
            self.assertIn("no ref line", out)
            self.assertNotIn("nothing to run", out)

    def test_a_malformed_ref_line_is_could_not_run(self):
        good = "refs/heads/lane %s refs/heads/lane %s\n" % (self.y, ZERO)
        for text in ("refs/heads/other %s\n" % self.x, good + "refs/heads/other\n"):
            rc, out = self.gate("--hook-stdin", stdin=text)
            self.assertEqual(rc, 2, out)
            self.assertNotIn("green -", out)

    def test_exit_0_without_a_run_needs_every_line_to_be_a_deletion(self):
        text = "".join("(delete) %s refs/heads/%s %s\n" % (ZERO, n, self.x) for n in "ab")
        rc, out = self.gate("--hook-stdin", stdin=text)
        self.assertEqual(rc, 0, out)
        self.assertIn("every ref being pushed is a deletion", out)

    def test_a_manual_run_without_the_flag_gates_head_and_ignores_stdin(self):
        rc, out = self.gate(stdin="refs/heads/other %s refs/heads/other %s\n"
                            % (self.x, ZERO))
        self.assertEqual(rc, 0, out)
        self.assertIn("for %s" % self.y[:12], out)

    def test_the_hook_codes_are_the_gates_codes(self):
        import test_prepush
        self.assertEqual((hook.REFUSED, hook.COULD_NOT_RUN),
                         (test_prepush.prepush.REFUSED, test_prepush.prepush.COULD_NOT_RUN))


class FixtureIdentityTests(GateFixture):
    def put(self, text, name="config"):
        with open(self.repo / ".git" / name, "a", encoding="utf-8") as fh:
            fh.write(text)

    def user(self, key, value):
        return "[user]\n\t%s = %s\n" % (key.split(".")[1], value)

    def test_a_fixture_identity_in_local_config_is_refused_whatever_its_spelling(self):
        for key, value in (("user.email", "a@example.invalid"),
                           ("user.email", "a@sub.example.invalid"),
                           ("user.email", "A@Example.Invalid"),
                           ("user.name", "risk-test"),
                           ("user.name", "Risk-Test"),
                           ("user.email", "risk-test@corp.example")):
            with self.subTest(key=key, value=value):
                path = self.repo / ".git" / "config"
                saved = path.read_bytes()
                self.put(self.user(key, value))
                try:
                    for args in ((), ("--hook-stdin",)):
                        rc, out = self.gate(*args, stdin="" if args else None)
                        self.assertEqual(rc, 1, out)
                        self.assertIn(key, out)
                        self.assertIn("local", out)
                        if value.lower() != hook.FIXTURE_TOKEN:  # the pattern is named
                            self.assertNotIn(value, out, "the refusal printed the value")
                        self.assertNotIn("[user]", out, "the refusal printed the config")
                finally:
                    path.write_bytes(saved)

    def test_a_fixture_identity_in_worktree_config_is_refused(self):
        run_git("config", "extensions.worktreeConfig", "true", cwd=self.repo)
        self.put(self.user("user.email", "w@example.invalid"), "config.worktree")
        probe = run_git("config", "--worktree", "--get", "user.email", cwd=self.repo)
        self.assertEqual(probe, "w@example.invalid", "git does not read this file")
        rc, out = self.gate()
        self.assertEqual(rc, 1, out)
        self.assertIn("worktree", out)
        self.assertIn("user.email", out)

    def test_the_override_does_not_step_over_a_fixture_identity(self):
        self.put(self.user("user.name", "risk-test"))
        rc, out = self.gate(env={"AUTOOS_PREPUSH_OVERRIDE": "orchestrator ran it"})
        self.assertEqual(rc, 1, out)

    def test_an_identity_pulled_in_by_include_or_includeif_is_refused(self):
        inc = self.tmp / "inc.cfg"
        inc.write_text(self.user("user.email", "inc@example.invalid"), encoding="utf-8")
        gitdir = (self.repo / ".git").resolve().as_posix()
        for header in ("[include]", '[includeIf "gitdir:%s"]' % gitdir):
            with self.subTest(header=header):
                path = self.repo / ".git" / "config"
                saved = path.read_bytes()
                self.put("%s\n\tpath = %s\n" % (header, inc.as_posix()))
                try:
                    self.assertEqual(hook.identity_findings(self.repo),
                                     [("local", "user.email", hook.FIXTURE_HOST)])
                    rc, out = self.gate()
                    self.assertEqual(rc, 1, out)
                    self.assertIn("user.email", out)
                finally:
                    path.write_bytes(saved)

    def test_check_ready_runs_the_identity_gate_first(self):
        self.certify(self.y)
        rc, out = self.gate("--check-ready", self.y)
        self.assertEqual(rc, 0, out)
        self.put(self.user("user.name", "risk-test"))
        rc, out = self.gate("--check-ready", self.y)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED", out)
        self.assertIn("user.name", out)
        self.assertNotIn("ready", out.lower().replace("check-ready", ""))

    def test_a_clean_identity_passes(self):
        self.put(self.user("user.name", "Ada") + self.user("user.email", "ada@example.com"))
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)
        run_git("config", "extensions.worktreeConfig", "true", cwd=self.repo)
        self.put(self.user("user.email", "ada@host.example.org"),
                 "config.worktree")
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)

    def test_lookalike_hosts_and_names_are_not_fixtures(self):
        for key, value in (("user.email", "a@notexample.invalid"),
                           ("user.email", "a@example.invalid.corp.test"),
                           ("user.name", "risk test"), ("user.name", "t")):
            self.assertIsNone(hook.fixture_pattern(key, value), (key, value))
        self.assertEqual(hook.fixture_pattern("user.email", "a@x.example.invalid"),
                         hook.FIXTURE_HOST)

    def test_an_unreadable_config_fails_closed_with_exit_2(self):
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaises(hook.ConfigUnreadable):
                hook.identity_findings(plain)
            with mock.patch("builtins.print") as printed:
                self.assertEqual(hook.identity_gate(plain), hook.COULD_NOT_RUN)
            self.assertIn("failing closed", printed.call_args[0][0])


class RealConfigIsolationTests(unittest.TestCase):
    def test_the_risk_fixture_does_not_write_through_an_inherited_git_dir(self):
        # The incident: _Repo.setUp under a hook's GIT_DIR/GIT_WORK_TREE.
        with tempfile.TemporaryDirectory() as tmp:
            victim = Path(tmp) / "victim"
            victim.mkdir()
            run_git("init", "-q", cwd=victim)
            cfg = victim / ".git" / "config"
            before = cfg.read_bytes()

            class Probe(test_autoos_risk._Repo):
                def runTest(self):
                    pass

            probe = Probe()
            leak = {"GIT_DIR": str(victim / ".git"), "GIT_WORK_TREE": str(victim)}
            with mock.patch.dict(os.environ, leak):
                probe.setUp()
            self.addCleanup(probe.doCleanups)
            self.assertEqual(cfg.read_bytes(), before, "the fixture wrote the victim")
            self.assertNotIn(b"risk-test", cfg.read_bytes())
            self.assertEqual(run_git("rev-list", "--all", "--count", cwd=victim), "0")
            local = subprocess.run(["git", "-C", probe.tmp, "config", "--local", "--list"],
                                   capture_output=True, text=True).stdout
            self.assertNotIn("user.", local, "the risk fixture wrote an identity")


    def test_the_git_helpers_ignore_an_inherited_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            victim, work = Path(tmp, "victim"), Path(tmp, "work")
            for d in (victim, work):
                d.mkdir()
                run_git("init", "-q", cwd=d)
            cfg = victim / ".git" / "config"
            before = cfg.read_bytes()
            leak = {"GIT_DIR": str(victim / ".git"), "GIT_WORK_TREE": str(victim)}
            with mock.patch.dict(os.environ, leak):
                run_git("config", "sentinel.key", "v1", cwd=work)
                test_affected_tests.git(work, "config", "sentinel.key", "v2")
            self.assertEqual(cfg.read_bytes(), before, "a helper wrote the inherited GIT_DIR")
            self.assertEqual(run_git("config", "--local", "sentinel.key", cwd=work), "v2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
