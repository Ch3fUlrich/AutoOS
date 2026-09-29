#!/usr/bin/env python3
"""Tests for tools/prepush.py — the gate that refuses a push the lane never tested.

Why this exists (operator order D-154, "make sure it works BEFORE you push"): three
lanes went red in CI while green on the developer's host —

  * SCOPECLI (CI 36517453134): the branch cited rule ``R-orch-17``, which existed
    only on a newer ``main``; the lane never merged main and never ran
    ``tests/test_skill_rules.py``, the check that catches a dangling R-id.
  * SBA      (CI 36493098467): the fixture's ``git commit`` returned 128 on the
    runner because CI has no git identity, while the dev host's global
    ``user.name``/``user.email`` made the same test pass at home.
  * FREEKEYS2 (CI 36506339556 shard e): 17 render tests, because the lane ran only
    the pytest files it guessed were relevant — not ``tests/test_registry_render.py``
    and not the bash render/apply filters.

Each refusal the tool owns is pinned by a test shaped like the red it prevents:
(a) the un-merged base, (b) the CI plan check, (c) the file -> test mapping,
(d) the CI-like git env — proved by a commit that really fails inside the gate and
really succeeds outside it, not by asserting on an env dict.

The fixtures are synthetic repos with stub ``tools/`` and ``tests/`` scripts, so a
test decides what "red" means. The production path — the real mapping against the
real suite — is exercised by running ``python3 tools/prepush.py`` on this branch,
which is exactly what the pre-push hook does.

Run directly:

    python3 tests/test_prepush.py
"""
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "prepush.py"
AFFECTED = ROOT / "tools" / "affected-tests.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepush = _load("prepush", SCRIPT)
at = _load("affected_tests_for_prepush", AFFECTED)

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]

#: The stub that stands in for the mapping tool: it answers with whatever plan the
#: test put in PREPUSH_FIXTURE_PLAN, so the gate's *running* is tested here and the
#: *mapping* is tested against the real tree in MappingTests.
AFFECTED_STUB = '''#!/usr/bin/env python3
import json
import os
import sys
sys.stdout.write(json.dumps(json.loads(os.environ.get("PREPUSH_FIXTURE_PLAN", "{}"))) + "\\n")
'''


def run_git(*args, cwd=None, check=True):
    proc = subprocess.run(GIT + list(args),
                          cwd=(str(cwd) if cwd is not None else None),
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr))
    return proc.stdout.strip()


class RepoFixture(unittest.TestCase):
    """A synthetic repo whose suite scripts say exactly what the test tells them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run_git("init", "-q", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        run_git("add", "tracked.txt", cwd=self.repo)
        run_git("commit", "-q", "-m", "base", cwd=self.repo)
        base_sha = run_git("rev-parse", "HEAD", cwd=self.repo)
        # The fetched copy the ancestor check reads, written without a network.
        run_git("update-ref", "refs/remotes/origin/main", base_sha, cwd=self.repo)
        self._write("tools/affected-tests.py", AFFECTED_STUB)

    def _write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def set_plan(self, plan):
        """What the (stubbed) mapping answers, and the stub files it names."""
        os.environ["PREPUSH_FIXTURE_PLAN"] = json.dumps(plan)
        self.addCleanup(os.environ.pop, "PREPUSH_FIXTURE_PLAN", None)
        for rel in plan.get("pytest", []):
            self._write(rel, "def test_stub():\n    assert 0 == 0\n")

    #: What the real runner prints on its last line, tally included — the gate reads
    #: it, so the fakes have to say it too (R-worker-04).
    SH_GREEN = ('printf "  passed 3   failed 0   skipped 0\\n"\nexit 0\n')
    SH_RED = ('printf "  passed 2   failed 1   skipped 0\\n"\n'
              "echo 'suite failed' >&2\nexit 1\n")

    # The gate invokes the bash suite as `bash tests/run-tests.sh`, so every stub that
    # stands in for it is a POSIX-only fixture (CI 36529545083 shard f:
    # RepoLintTests.test_posix_guards_are_clean refuses an unguarded shebang in a test
    # file). Guarding the *helpers* rather than each caller is what makes the 30-odd
    # tests that build a suite stub skip on Windows by themselves.
    @unittest.skipIf(os.name == "nt", "the suite stub is a bash script")
    def suite_is_green(self):
        for rel in ("tests/test_ci_shards.py", "tests/ci-shards.py"):
            self._write(rel, "import sys\nsys.exit(0)\n")
        self._write("tests/run-tests.sh", "#!/usr/bin/env bash\n" + self.SH_GREEN)

    @unittest.skipIf(os.name == "nt", "the suite stub is a bash script")
    def suite_is_red(self):
        for rel in ("tests/test_ci_shards.py", "tests/ci-shards.py"):
            self._write(rel, "import sys\nsys.stderr.write('plan check failed\\n')\n"
                             "sys.exit(1)\n")
        self._write("tests/run-tests.sh", "#!/usr/bin/env bash\n" + self.SH_RED)

    def prepush(self, *args, env=None):
        full = dict(os.environ)
        full.update(env or {})
        proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo)]
                              + list(args), capture_output=True, text=True, env=full,
                              cwd=str(self.repo))
        return proc.returncode, proc.stdout + proc.stderr

    def assert_rc(self, expected, args=(), env=None):
        rc, out = self.prepush(*args, env=env)
        self.assertEqual(rc, expected, "rc %d, output:\n%s" % (rc, out))
        return out

    def log_lines(self):
        log = prepush.log_path(self.repo)
        if not log.is_file():
            return []
        return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def move_main_ahead(self):
        """Point refs/remotes/origin/main at a commit this branch does not carry."""
        run_git("switch", "-q", "-c", "mainwork", cwd=self.repo)
        (self.repo / "main-only.txt").write_text("newer main\n", encoding="utf-8")
        run_git("add", "main-only.txt", cwd=self.repo)
        run_git("commit", "-q", "-m", "newer main", cwd=self.repo)
        newer = run_git("rev-parse", "HEAD", cwd=self.repo)
        run_git("update-ref", "refs/remotes/origin/main", newer, cwd=self.repo)
        run_git("switch", "-q", "-", cwd=self.repo)
        (self.repo / "lane.txt").write_text("lane work\n", encoding="utf-8")
        run_git("add", "lane.txt", cwd=self.repo)
        run_git("commit", "-q", "-m", "lane work", cwd=self.repo)
        return newer


class AncestorGateTests(RepoFixture):
    """(a) The SCOPECLI red: a lane that never merged main cannot push."""

    def test_an_unmerged_base_is_refused_and_names_the_rule(self):
        self.move_main_ahead()
        self.suite_is_green()
        out = self.assert_rc(1)
        self.assertIn("origin/main", out)
        self.assertIn("R-coord-01", out)
        self.assertIn("merge", out.lower())

    def test_nothing_of_the_suite_runs_when_the_base_is_unmerged(self):
        self.move_main_ahead()
        marker = self.repo / "ran.txt"
        self._write("tests/test_ci_shards.py",
                    "import pathlib\npathlib.Path(%r).write_text('ran\\n')\n" % str(marker))
        self.assert_rc(1)
        self.assertFalse(marker.exists(), "the gate ran a suite on a branch CI cannot build")

    def test_a_lane_with_no_origin_main_ref_is_refused_not_skipped(self):
        run_git("update-ref", "-d", "refs/remotes/origin/main", cwd=self.repo)
        self.suite_is_green()
        out = self.assert_rc(1)
        self.assertIn("origin/main", out)

    def test_a_merged_base_stops_refusing(self):
        # The same branch with main merged in: the ancestor gate is now silent.
        self.move_main_ahead()
        run_git("merge", "-q", "--no-edit", "origin/main", cwd=self.repo)
        self.suite_is_green()
        self.set_plan({"pytest": ["tests/test_skill_rules.py"]})
        self.assert_rc(0)


class PlanCheckTests(RepoFixture):
    """(b) CI's plan job runs first (.github/workflows/ci.yml linux-plan)."""

    def test_the_plan_scripts_run_before_anything_else(self):
        self.suite_is_green()
        marker = self.repo / "ran.txt"
        self._write("tests/test_ci_shards.py",
                    "import pathlib\npathlib.Path(%r).write_text('plan\\n')\n" % str(marker))
        self.set_plan({})
        self.assert_rc(0)
        self.assertTrue(marker.is_file(), "tests/test_ci_shards.py never ran")

    def test_a_red_plan_check_is_named_with_its_command(self):
        self.suite_is_red()
        self.set_plan({})
        out = self.assert_rc(1)
        self.assertIn("tests/test_ci_shards.py", out)

    def test_a_red_shard_map_is_caught_by_the_other_script(self):
        self.suite_is_green()
        self._write("tests/ci-shards.py", "import sys\nsys.exit(1)\n")
        self.set_plan({})
        out = self.assert_rc(1)
        self.assertIn("tests/ci-shards.py", out)


class RunListTests(RepoFixture):
    """(c) The FREEKEYS2 red: the run list is derived, never guessed by the worker."""

    @unittest.skipIf(os.name == "nt", "the suite stub is a bash script")
    def capture_suite(self):
        """A run-tests.sh stub that records every invocation it receives."""
        log = self.repo / "suite-calls.txt"
        self._write("tests/run-tests.sh",
                    "#!/usr/bin/env bash\n"
                    "printf 'ARGS=%%s|PARTS=%%s|FULL=%%s\\n' "
                    '"$*" "${AUTOOS_TEST_PARTS-}" "${AUTOOS_FULL_SUITE-}" >> "%s"\n'
                    % (str(log),) + self.SH_GREEN)
        return log

    def test_terms_run_through_the_filter(self):
        self.suite_is_green()
        calls = self.capture_suite()
        self.set_plan({"terms": "apply,render"})
        self.assert_rc(0)
        lines = calls.read_text(encoding="utf-8").splitlines()
        self.assertTrue([l for l in lines if "ARGS=--filter apply,render" in l], lines)

    def test_a_changed_part_runs_that_part_the_way_ci_runs_it(self):
        self.suite_is_green()
        calls = self.capture_suite()
        self.set_plan({"parts": ["17"]})
        self.assert_rc(0)
        lines = calls.read_text(encoding="utf-8").splitlines()
        row = [l for l in lines if "PARTS=17" in l]
        self.assertTrue(row, lines)
        self.assertIn("FULL=1", row[0])

    def test_a_bash_run_is_neither_unfiltered_nor_unselected(self):
        # R-host-08: the whole suite with no filter is the OOM shape, so every
        # run-tests.sh call must carry a filter or a part selection.
        self.suite_is_green()
        calls = self.capture_suite()
        self.set_plan({"terms": "usb", "parts": ["27"]})
        self.assert_rc(0)
        lines = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2, lines)
        for line in lines:
            args = line.split("|")[0]
            parts = line.split("|PARTS=")[1].split("|")[0]
            self.assertTrue(args.startswith("ARGS=--filter ") or parts,
                            "an unfiltered, unselected run-tests.sh call: " + line)

    def test_a_red_suite_command_is_reported_and_refuses_the_push(self):
        self.suite_is_red()
        self.set_plan({"terms": "usb"})
        out = self.assert_rc(1)
        self.assertIn("run-tests.sh", out)

    @unittest.skipIf(os.name == "nt", "the runner stub it rewrites is a bash script")
    def test_a_green_run_that_examined_no_case_is_refused(self):
        # R-worker-05, in the gate's own shape: run-tests.sh prints
        # `passed 0 failed 0 skipped 0` and exits 0 when its --filter matches no
        # case — so the exit code cannot tell "the change is fine" from "nothing
        # was looked at". A derived filter that selects nothing is a free pass.
        self.suite_is_green()
        self._write("tests/run-tests.sh",
                    '#!/usr/bin/env bash\n'
                    'printf "  passed 0   failed 0   skipped 0\\n"\nexit 0\n')
        self.set_plan({"terms": "no-such-case-name"})
        out = self.assert_rc(1)
        self.assertIn("run-tests.sh", out)
        self.assertIn("no test", out.lower(), out)

    @unittest.skipIf(os.name == "nt", "the runner stub it rewrites is a bash script")
    def test_a_part_selection_that_skips_every_case_still_ran_something(self):
        # A run whose cases were all skipped did at least examine a selection: on a
        # host where a part's cases are platform-skipped, refusing this would tell
        # the lane to fix a thing that is not broken.
        self.suite_is_green()
        self._write("tests/run-tests.sh",
                    '#!/usr/bin/env bash\n'
                    'printf "  passed 0   failed 0   skipped 7\\n"\nexit 0\n')
        self.set_plan({"parts": ["41"]})
        self.assert_rc(0)

    def test_a_red_pytest_file_refuses_the_push(self):
        self.suite_is_green()
        self.set_plan({"pytest": ["tests/test_registry_render.py"]})
        self._write("tests/test_registry_render.py",
                    "def test_stub():\n    assert 1 == 0\n")
        out = self.assert_rc(1)
        self.assertIn("tests/test_registry_render.py", out)

    def test_a_plan_naming_a_file_that_is_not_there_is_red_not_skipped(self):
        # Silence is how a case goes unrun: a mapping that points at a moved or
        # renamed test file must refuse the push, not walk past it.
        self.suite_is_green()
        self.set_plan({"pytest": ["tests/test_moved_away.py"]})
        os.remove(str(self.repo / "tests" / "test_moved_away.py"))
        self.assert_rc(1)


class GitEnvTests(RepoFixture):
    """(d) The SBA red: the child git env is CI's, not the developer's."""

    def child_env_file(self):
        """A plan-check stub that dumps the env it was really handed."""
        self.suite_is_green()
        seen = self.repo / "child-env.json"
        self._write("tests/test_ci_shards.py",
                    "import json, os\njson.dump(dict(os.environ), open(%r, 'w'))\n"
                    % str(seen))
        return seen

    def test_identity_is_scrubbed_from_every_child(self):
        seen = self.child_env_file()
        self.set_plan({})
        self.assert_rc(0, env={"GIT_AUTHOR_NAME": "dev host identity",
                               "GIT_COMMITTER_EMAIL": "dev@example.invalid"})
        child = json.loads(seen.read_text(encoding="utf-8"))
        self.assertEqual(child.get("GIT_CONFIG_GLOBAL"), "/dev/null")
        self.assertEqual(child.get("GIT_CONFIG_NOSYSTEM"), "1")
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            self.assertNotIn(key, child, "%s leaked into the child env" % key)

    COMMIT_STUB = ("import subprocess, sys\n"
                   "r = subprocess.run(['git', 'commit', '-q', '--allow-empty', '-m', "
                   "'lane'], cwd=%r, capture_output=True, text=True)\n"
                   "sys.stderr.write('fixture git commit status %%d: %%s\\n' "
                   "%% (r.returncode, r.stderr.strip()))\n"
                   "sys.exit(0 if r.returncode == 0 else 1)\n")

    def test_a_host_identity_is_not_inherited_by_the_suite(self):
        # The proof, not the assertion. The developer host's global config carries
        # a git identity, the CI runner has none, and SBA's fixture `git commit`
        # passed at home and died 128 in CI (CI 36493098467). So the same commit
        # must SUCCEED when the env names that global config and FAIL through the
        # gate, which replaces it with CI's /dev/null.
        cfg = self.tmp / "global-gitconfig"
        cfg.write_text("[user]\n\tname = Dev Host\n\temail = dev@example.invalid\n",
                       encoding="utf-8")
        env = {"GIT_CONFIG_GLOBAL": str(cfg)}
        self.suite_is_green()
        self._write("tests/test_ci_shards.py", self.COMMIT_STUB % str(self.repo))
        self.set_plan({})
        control = subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "control"],
                                 cwd=str(self.repo), env={**os.environ, **env},
                                 capture_output=True, text=True)
        self.assertEqual(control.returncode, 0,
                         "the fixture's global identity does not work: "
                         + control.stderr)
        out = self.assert_rc(1, env=env)
        self.assertIn("tests/test_ci_shards.py", out)
        self.assertIn("status 128", out)

    def test_a_repo_that_carries_its_own_identity_still_passes(self):
        # The isolation is of the HOST's config, not of the repository's: a test
        # that sets its own identity (the way CI's fixtures do) must stay green —
        # refusing that would turn the gate into a wall instead of a mirror.
        cfg = self.tmp / "global-gitconfig"
        cfg.write_text("[user]\n\tname = Dev Host\n\temail = dev@example.invalid\n",
                       encoding="utf-8")
        run_git("config", "user.name", "repo local", cwd=self.repo)
        run_git("config", "user.email", "repo@local.invalid", cwd=self.repo)
        self.suite_is_green()
        self._write("tests/test_ci_shards.py", self.COMMIT_STUB % str(self.repo))
        self.set_plan({})
        self.assert_rc(0, env={"GIT_CONFIG_GLOBAL": str(cfg)})


class LogRecordTests(RepoFixture):
    """(e) A green run leaves a record a later gate can read; a red leaves nothing."""

    def test_green_appends_sha_utc_and_commands(self):
        self.suite_is_green()
        self.set_plan({"pytest": ["tests/test_skill_rules.py"]})
        self.assert_rc(0)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1, lines)
        head = run_git("rev-parse", "HEAD", cwd=self.repo)
        fields = lines[0].split()
        self.assertEqual(fields[0], head)
        self.assertRegex(fields[1], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIn("green", lines[0])
        self.assertIn("tests/test_ci_shards.py", lines[0])

    def test_the_log_lives_in_the_git_dir_so_it_is_never_committed(self):
        self.suite_is_green()
        self.set_plan({})
        self.assert_rc(0)
        log = prepush.log_path(self.repo)
        self.assertIn(".git", log.parts)
        self.assertNotIn("autoos-prepush.log",
                         run_git("status", "--porcelain", cwd=self.repo))

    def test_the_log_lives_in_the_worktrees_own_git_dir(self):
        # A linked worktree has its own gitdir; the record belongs to the head it
        # certifies, not to the parent checkout.
        worktree = self.tmp / "wt"
        self.suite_is_green()
        self.set_plan({})
        run_git("add", "-A", cwd=self.repo)
        run_git("commit", "-q", "-m", "stub suite", cwd=self.repo)
        run_git("worktree", "add", "-q", "-b", "wt-branch", str(worktree), cwd=self.repo)
        full = dict(os.environ)
        proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(worktree)],
                              capture_output=True, text=True, env=full,
                              cwd=str(worktree))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        own = prepush.log_path(worktree)
        self.assertTrue(own.is_file(), own)
        self.assertNotEqual(str(own), str(prepush.log_path(self.repo)))

    def test_red_appends_no_green_record(self):
        self.suite_is_red()
        self.set_plan({})
        self.assert_rc(1)
        self.assertEqual(self.log_lines(), [])


class CouldNotRunTests(RepoFixture):
    """NB3: the docstring promises exit 2 when the gate itself could not run.

    The two codes are different next actions, which is why the distinction is worth
    a code: 1 means *a gate ran and said no* — fix the tree and push again; 2 means
    *the question was never asked* — the checkout has no mapper in it, or is not a
    checkout. `tools/autoos-agent.py`'s `ready` already refuses to conflate them for
    its own git failures, and the gate it wraps cannot answer with 1 for everything.
    """

    def run_outside(self, path, *args):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(path)]
                              + list(args), capture_output=True, text=True,
                              cwd=str(path))
        return proc.returncode, proc.stdout + proc.stderr

    def test_a_run_list_that_cannot_be_derived_is_not_a_refusal(self):
        os.remove(str(self.repo / "tools" / "affected-tests.py"))
        out = self.assert_rc(2)
        self.assertIn("affected-tests.py", out)
        self.assertEqual(self.log_lines(), [], "a gate that never asked left a record")

    def test_a_mapper_that_prints_no_json_is_not_a_refusal(self):
        self._write("tools/affected-tests.py", "print('not a plan')\n")
        out = self.assert_rc(2)
        self.assertIn("no JSON plan", out)

    def test_a_mapper_that_exits_red_is_not_a_refusal(self):
        # The mapper dying is not a test failing: no command of the run list was
        # even named, so there is nothing red for the lane to go and fix.
        self._write("tools/affected-tests.py", "import sys\nsys.exit(3)\n")
        self.assert_rc(2)

    def test_a_directory_that_is_not_a_checkout_is_not_a_refusal(self):
        plain = self.tmp / "not-a-checkout"
        plain.mkdir()
        rc, out = self.run_outside(plain)
        self.assertEqual(rc, 2, out)
        self.assertIn("not inside a git checkout", out)

    def test_a_ready_check_on_a_non_checkout_is_not_a_refusal(self):
        # D-110's own door: "no green record" (1, go run the gate) and "I could not
        # look" (2) send an orchestrator to different work.
        plain = self.tmp / "not-a-checkout"
        plain.mkdir()
        self.assertEqual(self.run_outside(plain, "--check-ready", "f" * 40)[0], 2)

    def test_a_checkout_with_no_commit_is_not_a_refusal(self):
        fresh = self.tmp / "fresh"
        run_git("init", "-q", str(fresh))
        rc, out = self.run_outside(fresh)
        self.assertEqual(rc, 2, out)
        self.assertIn("no HEAD commit", out)

    def test_a_red_check_is_still_a_refusal(self):
        # The other half of the contract, so the split cannot quietly become
        # "everything is 2": a gate that ran and found a red command answers 1.
        self.suite_is_red()
        self.set_plan({})
        self.assert_rc(1)


class OverrideTests(RepoFixture):
    """(f) An orchestrator may step over the gate — loudly, and on the record."""

    def test_the_override_skips_the_checks_and_logs_the_reason(self):
        self.suite_is_red()
        self.set_plan({})
        out = self.assert_rc(0, env={"AUTOOS_PREPUSH_OVERRIDE": "docs only, CI green"})
        self.assertIn("OVERRIDE", out)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1, lines)
        head = run_git("rev-parse", "HEAD", cwd=self.repo)
        self.assertEqual(lines[0].split()[0], head)
        self.assertIn("OVERRIDE", lines[0])
        self.assertIn("docs only", lines[0])

    def test_the_override_runs_no_suite(self):
        self.suite_is_green()
        marker = self.repo / "ran.txt"
        self._write("tests/test_ci_shards.py",
                    "import pathlib\npathlib.Path(%r).write_text('ran\\n')\n" % str(marker))
        self.set_plan({})
        self.assert_rc(0, env={"AUTOOS_PREPUSH_OVERRIDE": "because"})
        self.assertFalse(marker.exists(), "the override still ran the plan check")

    def test_an_empty_reason_is_not_an_override(self):
        self.suite_is_red()
        self.set_plan({})
        self.assert_rc(1, env={"AUTOOS_PREPUSH_OVERRIDE": "   "})

    def test_a_reason_with_a_newline_cannot_forge_a_record(self):
        # One line per record, and the fields that decide green-vs-overridden stay
        # the gate's own: a reason carrying a newline and a plausible sha must not
        # leave a second record behind or shift the first field off the real sha.
        self.suite_is_green()
        self.set_plan({})
        self.assert_rc(0, env={"AUTOOS_PREPUSH_OVERRIDE":
                               "x\n" + "0" * 40 + " green: faked record"})
        lines = self.log_lines()
        self.assertEqual(len(lines), 1, lines)
        fields = lines[0].split()
        self.assertEqual(fields[0], run_git("rev-parse", "HEAD", cwd=self.repo))
        self.assertEqual(fields[1], "OVERRIDE")
        self.assertFalse(prepush.local_green(fields[0], repo=self.repo))


class CheckReadyTests(RepoFixture):
    """D-110: a ready claim is only good for a sha this gate actually ran green."""

    def green_sha(self):
        self.suite_is_green()
        self.set_plan({"pytest": ["tests/test_skill_rules.py"]})
        self.assert_rc(0)
        return run_git("rev-parse", "HEAD", cwd=self.repo)

    def override_sha(self, reason="pushed unverified"):
        self.suite_is_red()
        self.set_plan({})
        self.assert_rc(0, env={"AUTOOS_PREPUSH_OVERRIDE": reason})
        return run_git("rev-parse", "HEAD", cwd=self.repo)

    def forge(self, *lines):
        """Append raw lines to the record file the way any process that can reach
        the git dir can — which is exactly why the reader must accept only the
        shape the gate itself writes."""
        for line in lines:
            prepush.record(self.repo, line)

    def test_a_forged_free_text_line_is_not_green(self):
        # B1: the reader treated *any* second field but OVERRIDE as a green record,
        # so `echo "$SHA anything" >> <git-dir>/autoos-prepush.log` certified a push
        # that was never run — measured at ee92774, where --check-ready returned 0.
        sha = run_git("rev-parse", "HEAD", cwd=self.repo)
        self.forge("%s anything-at-all" % sha)
        self.assertFalse(prepush.local_green(sha, repo=self.repo))
        out = self.assert_rc(1, ("--check-ready", sha))
        self.assertIn("NOT READY", out)

    def test_only_the_two_shapes_the_gate_writes_count_as_records(self):
        # Every half of the green shape carries meaning: the sha, the UTC stamp the
        # gate stamps itself, and the `green:` field. A line missing any of them is
        # not a record, so it cannot be green and cannot make a later line unreadable.
        sha = run_git("rev-parse", "HEAD", cwd=self.repo)
        for line in ("",
                    "prose, not a record",
                    sha,
                    "%s" % ("0" * 40),
                    "%s nope" % sha,
                    "%s green: pytest ran" % sha,
                    "%s yesterday green: pytest ran" % sha,
                    "%s 2026-01-01T00:00:00Z green pytest ran" % sha,
                    "%s 2026-01-01 00:00:00 green: pytest ran" % sha,
                    "%s 2026-01-01T00:00:00Z notgreen: pytest ran" % sha,
                    "%s 2026-01-01T00:00:00Z OVERRIDE green: pytest ran" % sha,
                    "note about %s 2026-01-01T00:00:00Z green: pytest ran" % sha,
                    "%s 2026-01-01T00:00:00Z green: pytest ran but the gate died"
                    % ("z" * 40)):
            self.assertIsNone(prepush.parse_record(line), line)
            self.forge(line)
        self.assertEqual(prepush.read_records(self.repo), [])
        self.assertFalse(prepush.local_green(sha, repo=self.repo))

    def test_the_record_the_gate_writes_is_the_record_the_gate_reads(self):
        # One writer, one parser, one predicate. If `green_line` ever changes shape
        # this test fails here instead of every lane quietly losing its ready claim.
        sha = run_git("rev-parse", "HEAD", cwd=self.repo)
        line = prepush.green_line(sha, ["python3 -m pytest -q tests/test_x.py",
                                        "bash tests/run-tests.sh --filter gate"])
        self.forge(line)
        self.assertEqual([r[0] for r in prepush.read_records(self.repo)], [sha])
        self.assertTrue(prepush.local_green(sha, repo=self.repo))
        out = self.assert_rc(0, ("--check-ready", sha))
        self.assertIn(sha[:8], out)

    def test_a_malformed_line_is_ignored_and_hides_no_real_record(self):
        # Junk in the file costs a lane nothing but a skipped line: the gate's own
        # green record below it must still read green.
        sha = self.green_sha()
        self.forge("prose, not a record", "%s anything-at-all" % sha)
        self.assertTrue(prepush.local_green(sha, repo=self.repo))
        self.assert_rc(0, ("--check-ready", sha))

    def test_a_green_sha_passes_the_check(self):
        sha = self.green_sha()
        out = self.assert_rc(0, ("--check-ready", sha))
        self.assertIn(sha[:8], out)

    def test_an_unrecorded_sha_fails_the_check(self):
        self.green_sha()
        out = self.assert_rc(1, ("--check-ready", "1" * 40))
        self.assertIn("11111111", out)

    def test_an_override_record_is_not_green(self):
        sha = self.override_sha()
        out = self.assert_rc(1, ("--check-ready", sha))
        self.assertIn("OVERRIDE", out)

    def test_a_later_green_record_beats_an_earlier_override(self):
        sha = self.override_sha()
        self.suite_is_green()
        self.set_plan({})
        self.assert_rc(0)
        self.assertEqual(run_git("rev-parse", "HEAD", cwd=self.repo), sha)
        self.assert_rc(0, ("--check-ready", sha))

    def test_an_abbreviated_sha_is_resolved_before_the_match(self):
        sha = self.green_sha()
        self.assert_rc(0, ("--check-ready", sha[:9]))

    def test_local_green_is_callable_as_a_function(self):
        sha = self.green_sha()
        self.assertTrue(prepush.local_green(sha, repo=self.repo))
        self.assertFalse(prepush.local_green("f" * 40, repo=self.repo))

    def test_the_check_is_quiet_about_success_and_loud_about_refusal(self):
        sha = self.green_sha()
        self.assertNotIn("NOT", self.assert_rc(0, ("--check-ready", sha)))
        self.assertIn("NOT READY", self.assert_rc(1, ("--check-ready", "2" * 40)))


class MappingTests(unittest.TestCase):
    """The file -> test mapping, one test per measured red (c)."""

    def plan_for(self, files, root=ROOT):
        return at.map_changed_files(files, root=root)

    def test_a_skill_change_maps_to_the_skill_rules_test(self):
        # SCOPECLI: R-orch-17 was cited and nothing ran the dangling-id check.
        for path in (".agents/skills/unattended-orchestration/SKILL.md",
                     "CHANGELOG.md", "docs/routing.md"):
            self.assertIn("tests/test_skill_rules.py", self.plan_for([path])["pytest"], path)

    def test_a_tools_change_maps_to_the_tests_that_name_it(self):
        # SBA: tools/autoos-agent.py changed; tests/test_autoos_spawner.py names it.
        plan = self.plan_for(["tools/autoos-agent.py"])
        self.assertIn("tests/test_autoos_spawner.py", plan["pytest"], plan)

    def test_a_tools_change_pulls_the_test_whose_module_constant_names_it(self):
        # The corpus attributes a mention to one *case*, because that is what a
        # --filter term is derived from. A test file that builds the tool's path in
        # a module-level constant names it in every case and in none of them, so
        # the gate would push the tool's own suite unrun.
        plan = self.plan_for(["tools/affected-tests.py"])
        self.assertIn("tests/test_affected_tests.py", plan["pytest"], plan)

    def test_a_skill_code_change_maps_to_the_skill_own_tests(self):
        # The FREEKEYS2 miss one directory over: a skill's code is covered by
        # pytest files under .agents/skills/<name>/tests/, which `discover()` does
        # not scan, so a gate that only read `tests/` would push the change unseen.
        plan = self.plan_for(
            [".agents/skills/unattended-orchestration/trust_worktree.py"])
        self.assertIn(
            ".agents/skills/unattended-orchestration/tests/test_trust_worktree.py",
            plan["pytest"], plan)

    def test_a_changed_test_file_is_run_whatever_directory_it_lives_in(self):
        plan = self.plan_for(
            [".agents/skills/unattended-orchestration/tests/test_trust_worktree.py"])
        self.assertIn(
            ".agents/skills/unattended-orchestration/tests/test_trust_worktree.py",
            plan["pytest"], plan)

    def test_a_test_file_change_pulls_the_repo_wide_lints_that_scan_the_suite(self):
        # CI 36529545083, the root cause of both reds this lane fixes: the lane added
        # `tests/test_prepush.py`, ran the file it had just written, and CI went red in
        # shard b (`SuiteWiringTests`: every `tests/test_*.py` must be *run* by some
        # harness) and shard f (`RepoLintTests`: every POSIX-only pattern in one needs a
        # Windows guard). Neither of them mentions `test_prepush.py` — each scans the
        # whole tree, so no case-level mention can derive them and the mapping named
        # neither. A new tree-wide lint over the suites belongs in REPO_META_SCANS for
        # the same reason a new harness belongs in test_suite_wiring.py's WIRING.
        wiring = ("tests/test_suite_wiring.py",)
        portability = ("tests/test_windows_portability.py",)
        both = wiring + portability
        for path, expected in (
                ("tests/test_prepush.py", both),
                # an added file is the shape that broke: nothing named it yet.
                ("tests/test_brand_new.py", both),
                # the harnesses the wiring test reads: a part that stops running a unit
                # test un-wires it.
                ("tests/linux/33-documentation.sh", wiring),
                ("tests/run-tests.ps1", both),
                (".github/workflows/ci.yml", wiring),
                # the BOM lint runs over `git ls-files *.ps1 *.psm1`, wherever they are.
                ("lib/windows/AutoOS.Catalog.psm1", portability),
                ("tests/test_windows_portability.py", ())):
            plan = self.plan_for([path])
            for meta in expected:
                self.assertIn(meta, plan["pytest"], "%s -> %s (%s)" % (path, meta, plan))
            if path.startswith("tests/") and path.endswith(".py"):
                self.assertIn(path, plan["pytest"], path)

    def test_an_ordinary_change_selects_no_repo_wide_lint(self):
        # The rule is a scan, not a sledgehammer: a catalog edit is not an input to the
        # suite lints, and selecting them on every change teaches nothing about the tree.
        for path in ("catalog/linux.json", "web/index.html", "setup.sh",
                     ".agents/skills/unattended-orchestration/SKILL.md"):
            plan = self.plan_for([path])
            self.assertNotIn("tests/test_windows_portability.py", plan["pytest"], path)
            self.assertNotIn("tests/test_suite_wiring.py", plan["pytest"], path)

    def test_a_skill_code_change_does_not_drag_in_another_skill_s_tests(self):
        # Over-inclusion is allowed, but not across the whole tree: the tests that
        # answer a skill are its own, not every skill's.
        plan = self.plan_for(
            [".agents/skills/unattended-orchestration/trust_worktree.py"])
        strays = [p for p in plan["pytest"]
                  if p.startswith(".agents/skills/")
                  and not p.startswith(".agents/skills/unattended-orchestration/")]
        self.assertEqual(strays, [], plan)

    def test_a_registry_change_maps_to_the_render_and_tier_tests_and_part_17(self):
        # FREEKEYS2: the registry changed; shard e ran none of the 17 render tests.
        plan = self.plan_for(["catalog/ai-registry.json"])
        for expected in ("tests/test_registry_render.py",
                         "tests/test_sync_router_tiers_registry.py"):
            self.assertIn(expected, plan["pytest"], plan)
        self.assertIn("17", plan["parts"], plan)
        self.assertTrue(plan["terms"], "the registry mapped to no bash filter terms")

    def test_a_route_or_combo_file_maps_the_same_way(self):
        for path in ("configuration/omniroute/profiles/t1-orchestrator.json",
                     "tools/sync-router-tiers.py",
                     "catalog/router-combo.json"):
            plan = self.plan_for([path])
            self.assertIn("17", plan["parts"], path)

    def test_the_omniroute_configuration_maps_to_the_apply_filter(self):
        plan = self.plan_for(["configuration/omniroute/apply.sh"])
        self.assertIn("apply", plan["terms"].split(","), plan)

    def test_a_changed_linux_part_maps_to_that_part(self):
        self.assertIn("34", self.plan_for(["tests/linux/34-ai-services.sh"])["parts"])

    def test_the_skill_rules_test_is_always_included(self):
        self.assertIn("tests/test_skill_rules.py", self.plan_for([])["pytest"])
        self.assertIn("tests/test_skill_rules.py", self.plan_for(["web/index.html"])["pytest"])

    def test_a_web_change_does_not_drag_in_the_routing_part(self):
        # Over-inclusion is allowed, but a UI change must not pretend to be a
        # registry change: the parts list comes from rules, not from everything.
        self.assertEqual(self.plan_for(["web/index.html"])["parts"], [])


class HookInstallTests(RepoFixture):
    """2. trust_worktree installs the gate into a lane's hooks dir — D-111.

    Rule D-111: nothing in the hook install path may write ``~/.claude.json``, so
    the step is callable on its own with ``--hook-only`` and every test here points
    HOME at an empty temp dir and asserts nothing was created in it.
    """

    TRUST = ROOT / ".agents" / "skills" / "unattended-orchestration" / "trust_worktree.py"

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

    def hook_only(self, target=None, extra=()):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["USERPROFILE"] = str(self.home)
        env.pop("CLAUDE_CONFIG_PATH", None)
        proc = subprocess.run([sys.executable, str(self.TRUST),
                               str(target or self.repo), "--hook-only"] + list(extra),
                              capture_output=True, text=True, env=env)
        return proc.returncode, proc.stdout + proc.stderr

    def place_gate_tool(self):
        """The gate's own script, so the hook the installer writes has something to run."""
        shutil.copy(str(SCRIPT), str(self.repo / "tools" / "prepush.py"))

    def install(self, *extra):
        self.place_gate_tool()
        rc, out = self.hook_only(extra=extra)
        self.assertEqual(rc, 0, out)
        return out

    def hooks_dir(self):
        return self.repo / ".git" / "hooks"

    def test_the_hook_lands_in_the_worktrees_hooks_dir(self):
        self.install()
        hook = self.hooks_dir() / "pre-push"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(str(hook), os.X_OK), "git never runs a hook it cannot exec")
        text = hook.read_text(encoding="utf-8")
        self.assertIn("tools/prepush.py", text)
        trust = _load("trust_worktree_for_prepush", self.TRUST)
        self.assertIn(trust.HOOK_MARKER, text)

    def test_installing_touches_no_claude_json(self):
        # D-111: the trust step writes ~/.claude.json; the hook step must not.
        self.install()
        self.assertFalse((self.home / ".claude.json").exists(),
                         "the hook install wrote the user's Claude config")
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [],
                         "the hook install wrote somewhere in HOME")

    def test_a_second_run_reports_skipped_and_leaves_the_hook_alone(self):
        self.install()
        hook = self.hooks_dir() / "pre-push"
        before = hook.read_text(encoding="utf-8")
        rc, out = self.hook_only()
        self.assertEqual(rc, 0, out)
        self.assertIn("skipped", out.lower())
        self.assertEqual(hook.read_text(encoding="utf-8"), before)
        self.assertFalse((self.hooks_dir() / "pre-push.autoos-chained").exists())

    @unittest.skipIf(os.name == "nt", "the foreign hook is an sh script")
    def test_a_hook_thats_not_ours_is_kept_and_renamed_not_overwritten(self):
        theirs = self.hooks_dir() / "pre-push"
        theirs.parent.mkdir(parents=True, exist_ok=True)
        theirs.write_text("#!/bin/sh\necho the operator's own hook\n", encoding="utf-8")
        theirs.chmod(0o755)
        self.install()
        chained = self.hooks_dir() / "pre-push.autoos-chained"
        self.assertIn("the operator's own hook", chained.read_text(encoding="utf-8"))
        self.assertIn("pre-push.autoos-chained", (self.hooks_dir() / "pre-push")
                      .read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "the chained hook is an sh script run by touch")
    def test_the_chained_hook_actually_runs_after_ours(self):
        # The production path: a real `git push` invokes the hook; ours passes the
        # gate and falls through to the operator's.
        origin = self.tmp / "origin.git"
        run_git("init", "-q", "--bare", str(origin))
        theirs = self.hooks_dir() / "pre-push"
        theirs.parent.mkdir(parents=True, exist_ok=True)
        marker = self.tmp / "operator-hook-ran.txt"
        theirs.write_text("#!/bin/sh\ntouch %s\nexit 0\n" % str(marker), encoding="utf-8")
        theirs.chmod(0o755)
        self.place_gate_tool()
        self.suite_is_green()
        self.set_plan({})
        run_git("add", "-A", cwd=self.repo)
        run_git("commit", "-q", "-m", "stubs, gate and operator hook", cwd=self.repo)
        self.install()
        proc = subprocess.run(["git", "-C", str(self.repo), "push", "-q", str(origin),
                               "HEAD:refs/heads/lane"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(marker.exists(), "the chained hook never ran")

    def test_the_installed_hook_refuses_a_push_the_lane_never_tested(self):
        origin = self.tmp / "origin.git"
        run_git("init", "-q", "--bare", str(origin))
        self.place_gate_tool()
        self.suite_is_green()
        self.set_plan({})
        run_git("add", "-A", cwd=self.repo)
        run_git("commit", "-q", "-m", "stubs and gate", cwd=self.repo)
        self.install()
        # main moves ahead: the push must now be refused by the hook, not by luck.
        self.move_main_ahead()
        proc = subprocess.run(["git", "-C", str(self.repo), "push", str(origin),
                               "HEAD:refs/heads/lane"], capture_output=True, text=True)
        combined = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, combined)
        self.assertIn("R-coord-01", combined)

    def test_a_repo_without_the_gate_tool_gets_no_hook_and_says_so(self):
        rc, out = self.hook_only()
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.hooks_dir() / "pre-push").exists())
        self.assertIn("prepush", out.lower())

    def push_lane(self, origin):
        proc = subprocess.run(["git", "-C", str(self.repo), "push", str(origin),
                               "HEAD:refs/heads/lane"], capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr

    def origin_refs(self, origin):
        return run_git("--git-dir", str(origin), "for-each-ref", cwd=self.repo)

    def test_a_gate_that_is_not_there_refuses_the_push(self):
        # NB2: the shim exited 0 when tools/prepush.py was missing, so a lane that
        # lost the gate — rebased onto a base without it, checked out an older
        # branch — pushed exactly the untested sha the gate exists to stop, while
        # the installer's message said it was installed. A check that cannot run is
        # not a check that passed: refuse, and let an orchestrator step over it with
        # the recorded override (which `--check-ready` then refuses to call ready).
        origin = self.tmp / "origin.git"
        run_git("init", "-q", "--bare", str(origin))
        self.install()
        os.remove(str(self.repo / "tools" / "prepush.py"))
        rc, out = self.push_lane(origin)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("nothing was checked", out)
        self.assertEqual(self.origin_refs(origin), "", "the push landed past a gate "
                         "that never ran")

    @unittest.skipIf(os.name == "nt", "the chained hook is an sh script run by touch")
    def test_a_gate_that_is_not_there_does_not_hand_over_to_the_operators_hook(self):
        # The chain is what runs *after* the gate passes. Handing over because the
        # gate could not be found would make the fail-closed refusal the operator's
        # hook's problem — and an operator hook that exits 0 would push the branch.
        origin = self.tmp / "origin.git"
        run_git("init", "-q", "--bare", str(origin))
        self.install()
        trust = _load("trust_worktree_for_chained", self.TRUST)
        chained = self.hooks_dir() / trust.CHAINED_NAME
        marker = self.tmp / "operator-hook-ran.txt"
        chained.write_text("#!/bin/sh\ntouch %s\nexit 0\n" % str(marker), encoding="utf-8")
        chained.chmod(0o755)
        os.remove(str(self.repo / "tools" / "prepush.py"))
        rc, out = self.push_lane(origin)
        self.assertNotEqual(rc, 0, out)
        self.assertFalse(marker.exists(), "the shim exec'd the chained hook with no "
                         "gate in front of it")

    def test_the_git_helper_has_one_home_and_lives_in_the_gate(self):
        # NB6: two copies of "run git, hand back stdout or None" is two places a
        # fix lands in. It matters exactly here: the hooks dir is *where git says*
        # its hooks are, and a skill-side copy that drifts reinstalls the gate into
        # a directory git never reads (NB1) — the failure the installer exists to
        # prevent. The gate's own helper is the one home; the import is lazy, so a
        # repository without `tools/prepush.py` still gets the rest of the skill.
        trust = _load("trust_worktree_for_git_helper", self.TRUST)
        self.assertFalse(hasattr(trust, "_git"),
                         "the skill kept its own copy of the gate's git helper")
        self.assertIn("gate_git()", inspect.getsource(trust.hooks_dir),
                      "hooks_dir no longer asks the gate's helper for the answer")
        self.assertIn("from prepush import _git", inspect.getsource(trust.gate_git),
                      "the helper the skill uses is not the gate's own")
        # That it still resolves the *right* directory is not proved here: the
        # hooksPath tests below install through this helper and a real `git push`
        # is what refuses.

    def test_core_hooksPath_moves_where_the_gate_installs_and_git_still_runs_it(self):
        # NB1: `installed` into `.git/hooks` is a lie when the operator set
        # core.hooksPath — git reads the other directory and the lane pushes untested
        # while believing it is gated. hooks_dir asks git itself (`rev-parse --git-path
        # hooks`), so the shim lands where the next push looks: proved by a push that
        # the gate really refuses from a directory outside the git dir.
        hooks = self.tmp / "hooks-outside-the-git-dir"
        hooks.mkdir()
        run_git("config", "core.hooksPath", str(hooks), cwd=self.repo)
        origin = self.tmp / "origin.git"
        run_git("init", "-q", "--bare", str(origin))
        self.place_gate_tool()
        self.suite_is_green()
        self.set_plan({})
        run_git("add", "-A", cwd=self.repo)
        run_git("commit", "-q", "-m", "stubs and gate", cwd=self.repo)
        out = self.install()
        self.assertIn(str(hooks), out, "the installer named some other hooks dir")
        self.assertTrue((hooks / "pre-push").is_file())
        self.assertFalse((self.repo / ".git" / "hooks" / "pre-push").exists(),
                         "the gate was written where core.hooksPath says git must not look")
        self.move_main_ahead()
        proc = subprocess.run(["git", "-C", str(self.repo), "push", str(origin),
                               "HEAD:refs/heads/lane"], capture_output=True, text=True)
        combined = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, combined)
        self.assertIn("R-coord-01", combined)

    def test_a_relative_core_hooksPath_is_resolved_against_the_checkout(self):
        # git takes a relative hooksPath against the top level, and `--git-path` hands
        # back that same relative string: joining it to anything but the checkout the
        # caller named would install somewhere git never reads.
        run_git("config", "core.hooksPath", "my-hooks", cwd=self.repo)
        self.place_gate_tool()
        out = self.install()
        hook = self.repo / "my-hooks" / "pre-push"
        self.assertTrue(hook.is_file(), out)
        self.assertIn(str(hook), out)

    def test_check_reports_without_installing(self):
        self.place_gate_tool()
        rc, out = self.hook_only(extra=("--check",))
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.hooks_dir() / "pre-push").exists())

    def test_the_full_trust_run_installs_the_hook_too(self):
        # The runner calls trust_worktree without --hook-only, so the gate has to
        # land on that path as well.
        self.place_gate_tool()
        claude_json = self.home / ".claude.json"
        claude_json.write_text(json.dumps({"projects": {}}), encoding="utf-8")
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        proc = subprocess.run([sys.executable, str(self.TRUST), str(self.repo),
                               "--repo", str(self.repo), "--no-env"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((self.hooks_dir() / "pre-push").is_file())


class ChangedFilesModeTests(unittest.TestCase):
    """`--changed-files-from REV` exists on the real tool and answers in JSON."""

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(AFFECTED)] + list(args),
                              cwd=str(ROOT), capture_output=True, text=True)

    def test_the_flag_and_plan_format_work_on_this_checkout(self):
        proc = self.run_tool("--changed-files-from", "HEAD", "--format", "plan")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(proc.stdout)
        for key in ("pytest", "parts", "terms"):
            self.assertIn(key, plan)

    def test_the_mode_needs_a_rev(self):
        proc = self.run_tool("--format", "plan")
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
