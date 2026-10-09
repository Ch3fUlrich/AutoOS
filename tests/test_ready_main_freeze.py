"""T0-FREEZE (plan v3): `ready` refuses while main CI is red.

Four gates on top of ReadyCommandTests' fixtures (tests/test_autoos_spawner.py,
class ReadyCommandTests): red main blocks a normal lane, a fixes-main lane is
allowed, green main is allowed, an unreadable gh fails closed with exit 2.

Fakes reproduce the real gh JSON contract (R-worker-04). The real reader is
`ci_run_status` in tools/autoos-agent.py: it runs
`gh run view --json conclusion,headSha` (line 3059), parses stdout with
`json.loads` (line 3068), and reads `data.get("headSha")` / `data.get("conclusion")`
(lines 3071-3075) off the returned OBJECT. The main gate reads the same two
fields off `gh run list --branch main ... --json databaseId,conclusion,headSha`,
whose stdout is a JSON ARRAY of such objects (one per run); the fake below feeds
that array shape through the same injectable-runner pattern, and `ready` is
driven through the real `agent.main` CLI, never `cmd_ready` directly.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import prepush as prepush_tool  # noqa: E402  (the D-110 gate record, as in ReadyCommandTests)


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent", TOOLS / "autoos-agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CROSS_FAMILY_LINE = ("AutoOS-Review: kind=cross-family author=qwen3.8-flash "
                     "reviewer=omniroute/muse verdict=PASS")
CROSS_FAMILY_LINE_2 = ("AutoOS-Review: kind=cross-family author=qwen3.8-flash "
                       "reviewer=gem-flash verdict=PASS")
FINAL_LINE = "AutoOS-Review: kind=final reviewer=sonnet verdict=READY"


def _freeze_registry():
    """Minimal registry the review gate can place: qwen author, meta+google seats.

    Same shape ReadyCommandTests uses (policy.reviewers with families); the
    review half is not what this file tests, it only has to be green so the
    main-CI gate is the one under test.
    """
    return {
        "models": {},
        "routes": {},
        "providers": {},
        "policy": {"reviewers": [
            {"client": "opencode", "model": "omniroute/muse", "family": "meta",
             "paid": True, "source": "test"},
            {"client": "gemini", "model": "gem-flash", "family": "google",
             "paid": False, "source": "test"},
            {"client": "qoder", "model": "qwen3.8-flash", "family": "qwen",
             "paid": False, "source": "test"},
        ]},
    }


class MainCiFreezeTests(unittest.TestCase):
    """The sixth `ready` gate: main red freezes normal lanes, fixes-main flows."""

    BRANCH = "lane/work"

    def setUp(self):
        self.agent = load_agent()
        self.registry = _freeze_registry()
        fd, self.registry_path = tempfile.mkstemp(suffix=".json")
        self.addCleanup(os.unlink, self.registry_path)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh)
        self._old_state = os.environ.get("AUTOOS_STATE_DIR")
        self.store_dir = tempfile.mkdtemp()
        os.environ["AUTOOS_STATE_DIR"] = self.store_dir
        self.addCleanup(shutil.rmtree, self.store_dir, True)
        self.addCleanup(self._restore_state)
        # AO-WRITER-GUARDS P4b (HERMETIC, D-852): the writer-guards gate reads the
        # lane's diff through `lane_diff_paths` on EVERY ready — the same shared
        # patch tests/test_autoos_spawner.py's ReadyCommandTests.setUp installs
        # (stub_lane_diff there, stub_lane_diff here, one per class) — so no ready
        # test in this file or its subclasses shells out to git.
        self.stub_lane_diff()

    def stub_lane_diff(self, paths=(), added="", error=None):
        """Install the recording stub for `lane_diff_paths` (see setUp); the
        sibling of ReadyCommandTests.stub_lane_diff, marked so `ready()` below
        refuses a run whose stub is missing."""
        calls = []

        def stub(repo, base, sha):
            calls.append((repo, base, sha))
            return list(paths), added, error

        stub.lane_diff_stub = True
        patch = mock.patch.object(self.agent, "lane_diff_paths", stub)
        patch.start()
        self.addCleanup(patch.stop)
        self.lane_diff_calls = calls
        return stub

    def _restore_state(self):
        if self._old_state is None:
            os.environ.pop("AUTOOS_STATE_DIR", None)
        else:
            os.environ["AUTOOS_STATE_DIR"] = self._old_state

    def write_record(self, *lines):
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def make_inbox(self, content=""):
        path = os.path.join(tempfile.mkdtemp(), "L1.md")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def read_inbox(self, path):
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()

    def make_repo(self, push=True, green=True):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        origin = os.path.join(base, "origin.git")
        repo = os.path.join(base, "work")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "init.defaultBranch=master"]
        subprocess.run(git + ["init", "-q", "--bare", origin], check=True)
        subprocess.run(git + ["clone", "-q", origin, repo], check=True,
                       stderr=subprocess.DEVNULL)
        with open(os.path.join(repo, "tracked.txt"), "w", encoding="utf-8") as fh:
            fh.write("lane work\n")
        subprocess.run(git + ["-C", repo, "add", "tracked.txt"], check=True)
        subprocess.run(git + ["-C", repo, "commit", "-q", "-m", "lane work"], check=True)
        subprocess.run(git + ["-C", repo, "switch", "-q", "-c", self.BRANCH], check=True)
        sha = subprocess.run(git + ["-C", repo, "rev-parse", "HEAD"],
                             check=True, capture_output=True,
                             text=True).stdout.strip()
        if push:
            subprocess.run(git + ["-C", repo, "push", "-q", "origin",
                                  "%s:%s" % (self.BRANCH, self.BRANCH)], check=True)
        if green:
            tree = subprocess.run(git + ["-C", repo, "rev-parse", "HEAD^{tree}"],
                                  check=True, capture_output=True,
                                  text=True).stdout.strip()
            prepush_tool.write_store_record(prepush_tool.green_record(
                sha, tree, [{"command": "pytest -q staged", "ok": True,
                             "passed": 1}]))
        return repo, sha

    def ready(self, record, repo, sha, inbox, extra=()):
        if not getattr(self.agent.lane_diff_paths, "lane_diff_stub", False):
            raise AssertionError(
                "ready test would run real git: the writer-guards gate reads "
                "lane_diff_paths, so patch it with stub_lane_diff() (setUp does "
                "it for every test in this class)")
        argv = ["ready", record, "--branch", self.BRANCH, "--sha", sha,
                "--inbox", inbox, "--repo", repo,
                "--registry", self.registry_path, *extra]
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = self.agent.main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
        return rc, out.getvalue(), err.getvalue()

    READY_RECORD = (CROSS_FAMILY_LINE, CROSS_FAMILY_LINE_2, FINAL_LINE)

    def run_ready_with_main(self, conclusion, run_id="777", error=None, extra=(),
                            declare_waiver=False):
        """Drive the real CLI with the main-CI gh read replaced.

        The fake mirrors the injectable-runner contract `ci_run_status` uses
        (tools/autoos-agent.py:3059-3075): it returns (conclusion, run_id, error)
        exactly as the parsed `gh run list --json databaseId,conclusion,headSha`
        array row would. `error` non-None is the gh-unreadable case.
        declare_waiver=True exports AUTOOS_FIXES_MAIN=<branch>@<sha> for the
        lane under test (T0-FREEZE-2: --fixes-main alone waives nothing).
        The env is scrubbed of any outer declaration first, so the tests mean
        what they say even if the harness exports one.
        """
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        outer = dict(os.environ)
        outer.pop("AUTOOS_FIXES_MAIN", None)
        if declare_waiver:
            outer["AUTOOS_FIXES_MAIN"] = "%s@%s" % (self.BRANCH, sha)
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status",
                                  lambda runner=None: (conclusion, run_id, error)):
            rc, out, err = self.ready(record, repo, sha, inbox, extra=extra)
        return rc, out, err, self.read_inbox(inbox)

    def test_red_main_blocks_a_normal_lane_and_names_the_gate_and_run(self):
        rc, out, err, inbox = self.run_ready_with_main("failure", run_id="777")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("main-ci-red", out + err)
        self.assertIn("777", out + err)
        self.assertEqual(inbox, "")

    def test_red_main_with_fixes_main_is_allowed(self):
        rc, out, err, inbox = self.run_ready_with_main(
            "failure", run_id="777", extra=("--fixes-main",),
            declare_waiver=True)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(inbox.splitlines()), 1, out + err + inbox)
        self.assertIn(' fixes_main="', inbox.rstrip("\n"))

    def test_green_main_is_allowed(self):
        rc, out, err, inbox = self.run_ready_with_main("success", run_id="778")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(inbox.splitlines()), 1, out + err + inbox)

    def test_unreadable_main_ci_fails_closed_with_exit_2(self):
        rc, out, err, inbox = self.run_ready_with_main(
            None, run_id=None, error="gh: command not found")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("gh", err)
        self.assertEqual(inbox, "")


class FixMainWaiverTests(MainCiFreezeTests):
    """F1: --fixes-main is an env-declared waiver, not a bare flag.

    The waiver is honoured ONLY when the environment declares
    AUTOOS_FIXES_MAIN=<lane>@<sha> whose sha equals the --sha being readied;
    a bare flag, or a mismatching env, REFUSES with exit 1 naming waiver-not-declared
    and saying the waiver was not declared and the CI was not consulted. The
    ready line logs the waiver use (lane@sha in stdout and as
    fixes_main="lane@sha" on the inbox line).
    """

    def run_ready_env(self, env_value, conclusion="failure", run_id="777",
                      extra=("--fixes-main",)):
        """Drive the real CLI with a controlled AUTOOS_FIXES_MAIN value.

        env_value=None is the bare flag (any outer declaration scrubbed, so
        the test means "no declaration" even if the harness exports one).
        """
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        outer = dict(os.environ)
        outer.pop("AUTOOS_FIXES_MAIN", None)
        if env_value is not None:
            outer["AUTOOS_FIXES_MAIN"] = env_value
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status",
                                  lambda runner=None: (conclusion, run_id, None)):
            rc, out, err = self.ready(record, repo, sha, inbox, extra=extra)
        return rc, out, err, self.read_inbox(inbox), sha

    def test_bare_fixes_main_flag_is_refused(self):
        rc, out, err, inbox, _sha = self.run_ready_env(None)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertIn("waiver was not declared", out + err)
        self.assertIn("CI was not consulted", out + err)
        self.assertEqual(inbox, "")

    def test_bare_flag_is_refused_even_when_main_is_green(self):
        rc, out, err, inbox, _sha = self.run_ready_env(None, conclusion="success")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertIn("waiver was not declared", out + err)
        self.assertIn("CI was not consulted", out + err)
        self.assertEqual(inbox, "")

    def test_mismatching_env_sha_is_refused(self):
        rc, out, err, inbox, _sha = self.run_ready_env(
            "lane/work@" + "0" * 40)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertIn("waiver was not declared", out + err)
        self.assertIn("CI was not consulted", out + err)
        self.assertEqual(inbox, "")

    def test_malformed_env_without_lane_at_sha_is_refused(self):
        for bad in ("justalane", "", "@", "lane/work@"):
            with self.subTest(env=bad):
                rc, out, err, inbox, _sha = self.run_ready_env(bad)
                self.assertEqual(rc, 1, out + err)
                self.assertIn("waiver-not-declared", out + err)
                self.assertNotIn("main-ci-red", out + err)
                self.assertIn("waiver was not declared", out + err)
                self.assertIn("CI was not consulted", out + err)
                self.assertEqual(inbox, "")

    def test_matching_env_waiver_is_honoured_and_logs_lane_at_sha(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        declared = "%s@%s" % (self.BRANCH, sha)
        outer = dict(os.environ)
        outer["AUTOOS_FIXES_MAIN"] = declared
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status",
                                  lambda runner=None: ("failure", "777", None)):
            rc, out, err = self.ready(record, repo, sha, inbox,
                                      extra=("--fixes-main",))
        self.assertEqual(rc, 0, out + err)
        self.assertIn(declared, out)
        self.assertEqual(len(self.read_inbox(inbox).splitlines()), 1,
                         out + err + self.read_inbox(inbox))
        self.assertIn(' fixes_main="%s"' % declared,
                      self.read_inbox(inbox).rstrip("\n"))

    def test_matching_env_without_the_flag_still_freezes(self):
        """The env alone waives nothing: red main blocks a lane that never
        passed --fixes-main, even when the declaration names its sha."""
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        outer = dict(os.environ)
        outer["AUTOOS_FIXES_MAIN"] = "%s@%s" % (self.BRANCH, sha)
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status",
                                  lambda runner=None: ("failure", "777", None)):
            rc, out, err = self.ready(record, repo, sha, inbox)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("main-ci-red", out + err)
        self.assertEqual(self.read_inbox(inbox), "")


class MainCiStatusParserTests(unittest.TestCase):
    """F2: main_ci_status parses the real `gh run list` array contract.

    Fakes mirror tests/test_autoos_spawner.py CIRunStatusTests (line 10031):
    its proc() wraps stdout in subprocess.CompletedProcess and its read()
    injects the runner, asserting argv and the timeout kwarg (lines ~10036-10060).
    Same shape here, against the ARRAY stdout `gh run list --json
    databaseId,conclusion,headSha` prints. Parser-level contract: a non-None
    error is the gh-unreadable case (the ready gate turns it into exit 2);
    a returned conclusion is what the gate compares (non-success -> exit 1
    main-ci-red, success -> allowed).
    """

    def setUp(self):
        self.agent = load_agent()

    def parse(self, stdout, rc=0, stderr="", exc=None):
        seen = {}

        def runner(argv, **kw):
            seen["argv"] = list(argv)
            seen["kw"] = kw
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(["gh"], rc,
                                               stdout=stdout, stderr=stderr)

        return self.agent.main_ci_status(runner=runner), seen

    def test_it_asks_gh_for_main_completed_runs_and_three_fields(self):
        (_conclusion, _run_id, err), seen = self.parse(
            json.dumps([{"databaseId": 1, "conclusion": "success",
                         "headSha": "abc"}]))
        self.assertIsNone(err)
        self.assertEqual(seen["argv"],
                         ["gh", "run", "list", "--branch", "main",
                          "--status", "completed", "--limit", "1",
                          "--workflow", "ci.yml", "--event", "push",
                          "--json", "databaseId,conclusion,headSha"])

    def test_it_times_out_rather_than_hanging_the_gate(self):
        (_c, _r, _e), seen = self.parse(
            json.dumps([{"databaseId": 1, "conclusion": "success",
                         "headSha": "a"}]))
        self.assertLessEqual(seen["kw"].get("timeout", 10 ** 9), 120)

    def test_success_returns_conclusion_and_run_id(self):
        (conclusion, run_id, err), _seen = self.parse(
            json.dumps([{"databaseId": 777, "conclusion": "success",
                         "headSha": "deadbeef"}]))
        self.assertIsNone(err)
        self.assertEqual((conclusion, run_id), ("success", "777"))

    def test_red_conclusions_are_returned_for_the_gate_to_refuse(self):
        for conclusion in ("failure", "cancelled", "skipped", "timed_out",
                           "action_required", "stale"):
            with self.subTest(conclusion=conclusion):
                (got, run_id, err), _seen = self.parse(
                    json.dumps([{"databaseId": 42, "conclusion": conclusion,
                                 "headSha": "deadbeef"}]))
                self.assertIsNone(err)
                self.assertEqual((got, run_id), (conclusion, "42"))

    def test_empty_array_is_unreadable(self):
        (conclusion, run_id, err), _seen = self.parse("[]")
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertTrue(err)

    def test_non_dict_row_is_unreadable(self):
        (conclusion, run_id, err), _seen = self.parse('["nope"]')
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertTrue(err)

    def test_missing_database_id_is_unreadable(self):
        (conclusion, run_id, err), _seen = self.parse(
            json.dumps([{"conclusion": "failure", "headSha": "deadbeef"}]))
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertTrue(err)

    def test_missing_conclusion_is_unreadable(self):
        (conclusion, run_id, err), _seen = self.parse(
            json.dumps([{"databaseId": 7, "headSha": "deadbeef"}]))
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertTrue(err)

    def test_null_or_empty_conclusion_is_unreadable(self):
        for bad in (None, ""):
            with self.subTest(conclusion=bad):
                (conclusion, run_id, err), _seen = self.parse(
                    json.dumps([{"databaseId": 7, "conclusion": bad,
                                 "headSha": "deadbeef"}]))
                self.assertIsNone(conclusion)
                self.assertIsNone(run_id)
                self.assertTrue(err)

    def test_invalid_json_is_unreadable(self):
        (conclusion, run_id, err), _seen = self.parse("not json at all")
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertIn("json", err.lower())

    def test_nonzero_gh_is_unreadable_naming_its_output(self):
        (conclusion, run_id, err), _seen = self.parse(
            "", rc=1, stderr="gh: no runs found")
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertIn("no runs found", err)

    def test_a_gh_timeout_is_unreadable(self):
        (conclusion, run_id, err), _seen = self.parse(
            "", exc=subprocess.TimeoutExpired(["gh"], 60))
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertTrue(err)

    def test_a_gh_that_cannot_start_is_unreadable(self):
        (conclusion, run_id, err), _seen = self.parse(
            "", exc=OSError("No such file or directory"))
        self.assertIsNone(conclusion)
        self.assertIsNone(run_id)
        self.assertIn("No such file", err)


class Freeze3WaiverLineTests(FixMainWaiverTests):
    """T0-FREEZE-3 G1-G4: waiver rides the inbox line; lane is checked."""

    def ready_with_env(self, env_value, extra=("--fixes-main",)):
        """Drive the real CLI with an env computed from the real sha.

        run_ready_env cannot name the sha before make_repo runs, so lane
        tests build the repo first and declare the env from its sha.
        """
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        outer = dict(os.environ)
        outer.pop("AUTOOS_FIXES_MAIN", None)
        if env_value is not None:
            outer["AUTOOS_FIXES_MAIN"] = env_value(sha) if callable(env_value) else env_value
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status",
                                  lambda runner=None: ("failure", "777", None)):
            rc, out, err = self.ready(record, repo, sha, inbox, extra=extra)
        return rc, out, err, self.read_inbox(inbox), sha

    def test_waiver_is_written_on_the_inbox_line(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        declared = "%s@%s" % (self.BRANCH, sha)
        outer = dict(os.environ)
        outer["AUTOOS_FIXES_MAIN"] = declared
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status",
                                  lambda runner=None: ("failure", "777", None)):
            rc, out, err = self.ready(record, repo, sha, inbox,
                                      extra=("--fixes-main",))
        self.assertEqual(rc, 0, out + err)
        self.assertIn(declared, out)
        line = self.read_inbox(inbox).rstrip("\n")
        self.assertEqual(len(self.read_inbox(inbox).splitlines()), 1)
        self.assertIn(' fixes_main="%s"' % declared, line)

    def test_basename_lane_is_honoured(self):
        base = self.BRANCH.rsplit("/", 1)[-1]
        rc, out, err, inbox, sha = self.ready_with_env(
            lambda s: "%s@%s" % (base, s))
        self.assertEqual(rc, 0, out + err)
        self.assertIn(' fixes_main="%s@%s"' % (base, sha),
                      inbox.rstrip("\n"))

    def test_waiver_lane_mismatch_is_refused(self):
        rc, out, err, inbox, sha = self.ready_with_env(
            lambda s: "other@%s" % s)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertIn("waiver lane mismatch", out + err)
        self.assertIn("other", out + err)
        self.assertEqual(inbox, "")

    def test_whitespace_only_lane_is_refused(self):
        rc, out, err, inbox, sha = self.ready_with_env(
            lambda s: " @%s" % s)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertIn("waiver was not declared", out + err)
        self.assertEqual(inbox, "")

    def test_surrounding_whitespace_is_trimmed_then_honoured(self):
        rc, out, err, inbox, sha = self.ready_with_env(
            lambda s: " %s@%s " % (self.BRANCH, s))
        self.assertEqual(rc, 0, out + err)
        line = inbox.rstrip("\n")
        self.assertIn(' fixes_main="%s@%s"' % (self.BRANCH, sha), line)

    def test_second_at_sign_stays_in_the_sha_half(self):
        # PINNED: the split partitions at the FIRST '@', so 'a@b@sha' means
        # lane 'a' and sha 'b@sha' -- which mismatches and is refused as an
        # undeclared waiver, not as a lane mismatch.
        rc, out, err, inbox, sha = self.ready_with_env(
            lambda s: "%s@extra@%s" % (self.BRANCH, s))
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertIn("waiver was not declared", out + err)
        self.assertNotIn("waiver lane mismatch", out + err)
        self.assertEqual(inbox, "")

    def test_abbreviated_sha_is_refused(self):
        # PINNED: the sha must equal --sha exactly; a 12-char prefix waives
        # nothing.
        rc, out, err, inbox, sha = self.ready_with_env(
            lambda s: "%s@%s" % (self.BRANCH, s[:12]))
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertIn("waiver was not declared", out + err)
        self.assertEqual(inbox, "")

    def test_bare_flag_says_ci_was_not_consulted(self):
        rc, out, err, inbox, _sha = self.run_ready_env(None)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver was not declared", out + err)
        self.assertIn("CI was not consulted", out + err)

    def test_red_main_names_both_waiver_parts(self):
        rc, out, err, inbox = self.run_ready_with_main("failure", run_id="777")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("--fixes-main", out + err)
        self.assertIn("AUTOOS_FIXES_MAIN=<lane>@", out + err)

    def test_waived_lane_does_not_consult_ci(self):
        # BY DESIGN: a waived lane never asks gh about main, so an unreadable
        # gh is still exit 0 -- the waiver replaces the read, it does not add
        # to it.
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        outer = dict(os.environ)
        outer["AUTOOS_FIXES_MAIN"] = "%s@%s" % (self.BRANCH, sha)
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status",
                                  side_effect=AssertionError("must not consult CI")):
            rc, out, err = self.ready(record, repo, sha, inbox,
                                      extra=("--fixes-main",))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.read_inbox(inbox).splitlines()), 1)


class Freeze4ScopedCiTests(MainCiStatusParserTests):
    """T0-FREEZE-4 H2: main_ci_status scopes the gh read to push CI."""

    def test_argv_scopes_to_ci_workflow_push_events(self):
        (_c, _r, err), seen = self.parse(
            json.dumps([{"databaseId": 1, "conclusion": "success",
                         "headSha": "abc"}]))
        self.assertIsNone(err)
        self.assertEqual(seen["argv"],
                         ["gh", "run", "list", "--branch", "main",
                          "--status", "completed", "--limit", "1",
                          "--workflow", "ci.yml", "--event", "push",
                          "--json", "databaseId,conclusion,headSha"])


class Freeze4WaiverLaneCharsTests(Freeze3WaiverLineTests):
    """T0-FREEZE-4 H3: the waiver lane is safe for the durable line."""

    @unittest.skipIf(os.name == "nt", "a double quote is not a legal Windows ref name; the quote check is covered on POSIX")
    def test_quote_in_waiver_lane_is_refused(self):
        self.BRANCH = 'lane/a"b'
        rc, out, err, inbox, sha = self.ready_with_env(
            lambda s: '%s@%s' % (self.BRANCH, s))
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertIn("cannot be written", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertEqual(inbox, "")

    def test_backslash_and_space_in_waiver_lane_are_refused(self):
        for bad_lane in ("la\\ne", "la ne", "la\tne"):
            with self.subTest(lane=bad_lane):
                rc, out, err, inbox, sha = self.ready_with_env(
                    lambda s, b=bad_lane: '%s@%s' % (b, s))
                self.assertEqual(rc, 1, out + err)
                self.assertIn("waiver-not-declared", out + err)
                self.assertIn("cannot be written", out + err)
                self.assertEqual(inbox, "")


class Freeze4WaiverGateNameTests(FixMainWaiverTests):
    """T0-FREEZE-4 H4: an undeclared waiver names its own gate."""

    def test_bare_flag_names_waiver_not_declared_gate(self):
        rc, out, err, inbox, _sha = self.run_ready_env(None)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("waiver-not-declared", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertEqual(inbox, "")


class Freeze5RepoScopeTests(MainCiFreezeTests):
    """T0-FREEZE-5 H6: the main-CI gh read runs in the lane's repo.

    WHY: `gh run list` resolves the repository from the PROCESS working
    directory, while every git gate in `cmd_ready` uses
    `args.repo or os.getcwd()` (tools/autoos-agent.py ~3170:
    `remote_branch_tip` and the D-110 `local_green` read). With `--repo`
    naming another checkout the sixth gate evaluated the WRONG repo's main
    CI. The gate takes the same repo directory and runs gh with cwd=<that
    dir>; an unreadable gh (a dir that is not a git repo included) stays
    fail-closed (exit 2 at the CLI).

    Fakes extend the injectable-runner contract `MainCiStatusParserTests.parse`
    uses (tests/test_ready_main_freeze.py ~340: a `def runner(argv, **kw)`
    fake): the new `cwd` kwarg rides in `kw`, so existing `**kw` fakes keep
    working. `main_ci_status` is repo-first/runner-second so a positional repo
    from `cmd_ready` still binds the pre-H6 `lambda runner=None` stubs in
    `MainCiFreezeTests.run_ready_with_main` (~180).
    """

    def parse_scoped(self, stdout, repo, rc=0, stderr="", exc=None):
        """Call the gate with an explicit repo; the fake records gh's cwd."""
        seen = {}

        def runner(argv, **kw):
            seen["argv"] = list(argv)
            seen["kw"] = dict(kw)
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(["gh"], rc,
                                               stdout=stdout, stderr=stderr)

        return self.agent.main_ci_status(repo, runner=runner), seen

    def test_explicit_repo_is_gh_cwd(self):
        (conclusion, run_id, err), seen = self.parse_scoped(
            json.dumps([{"databaseId": 7, "conclusion": "success",
                         "headSha": "deadbeef"}]), "/some/dir")
        self.assertIsNone(err, seen)
        self.assertEqual((conclusion, run_id), ("success", "7"))
        self.assertEqual(seen["kw"].get("cwd"), "/some/dir")

    def test_omitted_repo_defaults_to_process_cwd(self):
        seen = {}

        def runner(argv, **kw):
            seen["kw"] = dict(kw)
            return subprocess.CompletedProcess(
                ["gh"], 0,
                stdout=json.dumps([{"databaseId": 7,
                                    "conclusion": "success",
                                    "headSha": "deadbeef"}]),
                stderr="")

        (conclusion, _run_id, err) = self.agent.main_ci_status(runner=runner)
        self.assertIsNone(err, seen)
        self.assertEqual(conclusion, "success")
        self.assertEqual(seen["kw"].get("cwd"), os.getcwd())

    def test_ready_with_repo_runs_gh_in_that_repo(self):
        """End to end: `--repo DIR` reaches gh as cwd=DIR (not the process cwd)."""
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        seen = {}
        real_run = self.agent.subprocess.run

        def fake_run(argv, **kw):
            if list(argv[:3]) == ["gh", "run", "list"]:
                seen["cwd"] = kw.get("cwd")
                return subprocess.CompletedProcess(
                    argv, 0,
                    stdout=json.dumps([{"databaseId": 777,
                                        "conclusion": "success",
                                        "headSha": "deadbeef"}]),
                    stderr="")
            return real_run(argv, **kw)

        outer = dict(os.environ)
        outer.pop("AUTOOS_FIXES_MAIN", None)
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent.subprocess, "run", fake_run):
            rc, _out, _err = self.ready(record, repo, sha, inbox)
        self.assertEqual(rc, 0, seen)
        self.assertEqual(seen.get("cwd"), repo)

    def test_gh_failure_in_the_lane_repo_fails_closed_with_exit_2(self):
        """A gh the gate cannot read -- a dir that is not a git repo included --
        is exit 2, never an allow (fail closed like every other unreadable gate)."""
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        real_run = self.agent.subprocess.run

        def fake_run(argv, **kw):
            if list(argv[:3]) == ["gh", "run", "list"]:
                return subprocess.CompletedProcess(
                    argv, 1, stdout="",
                    stderr="none of the git remotes configured")
            return real_run(argv, **kw)

        outer = dict(os.environ)
        outer.pop("AUTOOS_FIXES_MAIN", None)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent.subprocess, "run", fake_run):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = self.agent.main(
                    ["ready", record, "--branch", self.BRANCH, "--sha", sha,
                     "--inbox", inbox, "--repo", repo,
                     "--registry", self.registry_path])
        self.assertEqual(rc, 2, out.getvalue() + err.getvalue())
        self.assertEqual(self.read_inbox(inbox), "")


class Freeze5GateOrderTests(MainCiFreezeTests):
    """T0-FREEZE-5 H7: the main-CI gate runs LAST -- every earlier refusal wins.

    WHY: each `ready` refusal must name the gate the caller actually hit, so
    the fix it suggests (push; run the pre-push gate) is the fix that unblocks.
    A main gate that ran first would blame a red main for a lane that was never
    pushed or never tested. Fixtures are MainCiFreezeTests' own
    (tests/test_ready_main_freeze.py: `make_repo` ~115 with its push/green
    switches, `ready` ~145 driving the real CLI, `READY_RECORD` ~157), the same
    real-temp-repo shape ReadyCommandTests uses
    (tests/test_autoos_spawner.py ~9596), and the no-jump precedent is
    `test_the_record_gate_does_not_jump_the_push_gate` (~10037: an unpushed
    lane hears "not pushed", not "D-110"). The order pinned here:
    reviews -> pushed -> (--ci-run) -> D-110 pre-push record -> main-ci-red.
    The AssertionError stubs prove the earlier gate returned BEFORE gh was
    ever consulted; the NotIn assertions prove the refusal names the right gate.
    """

    def run_ready_red_main(self, conclusion="failure", run_id="777",
                           push=True, green=True, sha_override=None,
                           extra=()):
        """Drive the real CLI with main CI stubbed red (or green).

        `main_ci_status` raising AssertionError means "must not consult main":
        any test passing that stub fails if the main gate runs before its gate.
        """
        repo, sha = self.make_repo(push=push, green=green)
        if sha_override is not None:
            sha = sha_override
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        outer = dict(os.environ)
        outer.pop("AUTOOS_FIXES_MAIN", None)
        stub = conclusion
        if isinstance(stub, type) and issubclass(stub, BaseException):
            main_fake = mock.Mock(side_effect=stub("must not consult main"))
        else:
            main_fake = mock.Mock(return_value=(conclusion, run_id, None))
        with mock.patch.dict(os.environ, outer, clear=True), \
                mock.patch.object(self.agent, "main_ci_status", main_fake):
            rc, out, err = self.ready(record, repo, sha, inbox, extra=extra)
        return rc, out, err, self.read_inbox(inbox)

    def test_missing_prepush_record_with_red_main_names_d110(self):
        rc, out, err, inbox = self.run_ready_red_main(
            conclusion=AssertionError, push=True, green=False)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("D-110", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertEqual(inbox, "")

    def test_unpushed_branch_with_red_main_names_not_pushed(self):
        rc, out, err, inbox = self.run_ready_red_main(
            conclusion=AssertionError, push=False, green=False)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("not pushed", out + err)
        self.assertNotIn("D-110", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertEqual(inbox, "")

    def test_wrong_sha_with_red_main_names_not_pushed(self):
        rc, out, err, inbox = self.run_ready_red_main(
            conclusion=AssertionError, push=True, green=True,
            sha_override="0" * 40)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("not pushed", out + err)
        self.assertNotIn("main-ci-red", out + err)
        self.assertEqual(inbox, "")

    def test_red_main_with_everything_else_green_names_main_ci_red(self):
        rc, out, err, inbox = self.run_ready_red_main(
            conclusion="failure", run_id="777", push=True, green=True)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("main-ci-red", out + err)
        self.assertIn("777", out + err)
        self.assertEqual(inbox, "")

    def test_green_main_with_everything_else_green_is_allowed(self):
        rc, out, err, inbox = self.run_ready_red_main(
            conclusion="success", run_id="778", push=True, green=True)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(inbox.splitlines()), 1, out + err + inbox)


if __name__ == "__main__":
    unittest.main()
