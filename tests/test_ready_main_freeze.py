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

    def run_ready_with_main(self, conclusion, run_id="777", error=None, extra=()):
        """Drive the real CLI with the main-CI gh read replaced.

        The fake mirrors the injectable-runner contract `ci_run_status` uses
        (tools/autoos-agent.py:3059-3075): it returns (conclusion, run_id, error)
        exactly as the parsed `gh run list --json databaseId,conclusion,headSha`
        array row would. `error` non-None is the gh-unreadable case.
        """
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        record = self.write_record(*self.READY_RECORD)
        with mock.patch.object(self.agent, "main_ci_status",
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
            "failure", run_id="777", extra=("--fixes-main",))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(inbox.splitlines()), 1, out + err + inbox)

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


if __name__ == "__main__":
    unittest.main()
