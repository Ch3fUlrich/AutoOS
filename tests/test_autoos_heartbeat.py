#!/usr/bin/env python3
"""Tests for tools/autoos_heartbeat.py, the `heartbeat` subcommand of
tools/autoos-agent.py, the MCP `heartbeat` tool, and the AUTOOS_AGENT_INBOX
launch refusal (R-heartbeat-02/03, R-pause-01, R-handoff-07 migrated into
code: briefs/common.md "Skill rules bind every spawned agent", operator
2026-09-26T13:43:33Z).

heartbeat is read-only end to end: it never pushes, commits or writes to a
repo (AGENTS.md rule 3 - a check that never writes is trivially safe to run
twice). Pure helpers (branch_prefix, pause_state) are unit tested directly;
repo_branch_state is exercised against real temporary git repositories (a
bare "origin" plus a working clone), the way test_autoos_spawner.py's
KeyFileTests builds a temp repo (inline `-c user.name=`/`-c user.email=`, no
machine config touched). The CLI and MCP surface are exercised through
subprocess/plain-function calls, the same way test_autoos_context.py and
test_autoos_spawner.py's McpRouteTests do.

Run directly, never through unittest discover:

    python3 tests/test_autoos_heartbeat.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))

import autoos_heartbeat as hb  # noqa: E402
import autoos_agent_mcp as mcp_server  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
       "-c", "init.defaultBranch=main"]


def git(*args, cwd, check=True):
    return subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True, check=check)


def write_inbox(path, *lines):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(line + "\n" for line in lines))


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTOOS_AGENT_")}
    env.update(extra)
    return env


class BranchPrefixTests(unittest.TestCase):
    """branch_prefix: groups sibling "owned" branches (R-heartbeat-02)."""

    def test_worktree_agent_style_groups_on_the_last_hyphen(self):
        self.assertEqual(hb.branch_prefix("worktree-agent-a957350031c1fae19"), "worktree-agent-")

    def test_namespaced_branch_groups_on_the_slash(self):
        self.assertEqual(hb.branch_prefix("L1-backlog/agy-tarball"), "L1-backlog/")

    def test_no_separator_is_the_whole_name(self):
        self.assertEqual(hb.branch_prefix("main"), "main")


class PauseStateTests(unittest.TestCase):
    """pause_state: the newest PAUSE wins unless a newer RESUME follows it
    (R-pause-01: "a hard stop, checked every heartbeat and before every launch")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox = os.path.join(self._tmp.name, "inbox.md")

    def test_missing_inbox_is_not_active(self):
        self.assertEqual(hb.pause_state(os.path.join(self._tmp.name, "nope.md")),
                         {"active": False, "at": None, "text": None})

    def test_no_inbox_path_is_not_active(self):
        self.assertEqual(hb.pause_state(None), {"active": False, "at": None, "text": None})

    def test_a_pause_line_with_no_resume_is_active(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW - stop everything")
        state = hb.pause_state(self.inbox)
        self.assertTrue(state["active"])
        self.assertEqual(state["at"], "2026-09-26T11:19:41Z")
        self.assertEqual(state["text"], "operator: PAUSE NOW - stop everything")

    def test_a_later_resume_clears_it(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T12:00:00Z operator: RESUME")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_an_earlier_resume_does_not_clear_a_later_pause(self):
        write_inbox(self.inbox,
                    "2026-09-26T09:00:00Z operator: RESUME",
                    "2026-09-26T11:19:41Z operator: PAUSE NOW")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_the_newest_of_several_pause_lines_is_reported(self):
        write_inbox(self.inbox,
                    "2026-09-26T08:00:00Z operator: PAUSE (operator gone for coffee)",
                    "2026-09-26T13:43:00Z operator: PAUSE (operator gone home)")
        state = hb.pause_state(self.inbox)
        self.assertTrue(state["active"])
        self.assertEqual(state["at"], "2026-09-26T13:43:00Z")
        self.assertIn("gone home", state["text"])

    def test_out_of_order_lines_are_still_read_by_timestamp(self):
        write_inbox(self.inbox,
                    "2026-09-26T13:43:00Z operator: PAUSE (later, written first)",
                    "2026-09-26T08:00:00Z operator: PAUSE (earlier, written second)")
        state = hb.pause_state(self.inbox)
        self.assertIn("written first", state["text"])

    def test_lowercase_pause_word_does_not_count(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: please pause soon")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_pause_as_part_of_another_word_does_not_count(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSED earlier, ignore")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_a_reply_line_with_done_marker_is_still_scanned_for_the_words(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T11:20:00Z L1: → done: paused every lane")
        self.assertTrue(hb.pause_state(self.inbox)["active"])

    def test_a_done_reply_carrying_resume_still_clears_it(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T12:00:00Z L1: → done: RESUME acknowledged")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_first_80_chars_of_the_text_are_kept(self):
        long_text = "PAUSE " + ("x" * 200)
        write_inbox(self.inbox, "2026-09-26T11:19:41Z " + long_text)
        state = hb.pause_state(self.inbox)
        self.assertEqual(len(state["text"]), 80)
        self.assertEqual(state["text"], long_text[:80])

    def test_a_line_with_no_parseable_timestamp_is_skipped(self):
        write_inbox(self.inbox, "not-a-timestamp PAUSE NOW")
        self.assertFalse(hb.pause_state(self.inbox)["active"])

    def test_blank_lines_are_skipped(self):
        write_inbox(self.inbox, "", "   ", "2026-09-26T11:19:41Z operator: PAUSE NOW")
        self.assertTrue(hb.pause_state(self.inbox)["active"])


class RepoBranchStateTests(unittest.TestCase):
    """repo_branch_state against real git repos: a bare origin plus a clone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.origin = os.path.join(self._tmp.name, "origin.git")
        self.work = os.path.join(self._tmp.name, "work")
        git("init", "-q", "--bare", self.origin, cwd=self._tmp.name)
        git("init", "-q", self.work, cwd=self._tmp.name)
        git("commit", "-q", "--allow-empty", "-m", "initial", cwd=self.work)
        git("remote", "add", "origin", self.origin, cwd=self.work)
        git("push", "-q", "-u", "origin", "main", cwd=self.work)

    def test_clean_repo_reports_nothing(self):
        self.assertEqual(hb.repo_branch_state(self.work), {"unpushed": [], "dirty": 0})

    def test_an_ahead_upstream_branch_is_reported(self):
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)
        state = hb.repo_branch_state(self.work)
        self.assertEqual(state["unpushed"], [("main", 1)])

    def test_a_sibling_branch_sharing_the_current_prefix_with_no_upstream_is_reported(self):
        git("checkout", "-q", "-b", "worktree-agent-current", cwd=self.work)
        git("checkout", "-q", "-b", "worktree-agent-sibling", cwd=self.work)
        git("commit", "-q", "--allow-empty", "-m", "sibling work", cwd=self.work)
        git("checkout", "-q", "worktree-agent-current", cwd=self.work)
        state = hb.repo_branch_state(self.work)
        self.assertIn(("worktree-agent-sibling", 1), state["unpushed"])
        self.assertNotIn("main", [b for b, _ in state["unpushed"]])

    def test_a_branch_with_no_upstream_and_no_matching_prefix_is_not_reported(self):
        git("checkout", "-q", "-b", "worktree-agent-current2", cwd=self.work)
        git("checkout", "-q", "-b", "unrelated-branch", cwd=self.work)
        git("commit", "-q", "--allow-empty", "-m", "unrelated work", cwd=self.work)
        git("checkout", "-q", "worktree-agent-current2", cwd=self.work)
        state = hb.repo_branch_state(self.work)
        self.assertEqual(state["unpushed"], [])

    def test_dirty_working_tree_is_counted(self):
        with io.open(os.path.join(self.work, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        state = hb.repo_branch_state(self.work)
        self.assertEqual(state["dirty"], 1)

    def test_not_a_git_repo_reports_zero_of_both(self):
        empty = os.path.join(self._tmp.name, "not-a-repo")
        os.makedirs(empty)
        self.assertEqual(hb.repo_branch_state(empty), {"unpushed": [], "dirty": 0})


def assistant_line(model, tokens):
    return json.dumps({"type": "assistant",
                       "message": {"model": model, "usage": {"input_tokens": tokens}}})


class HeartbeatCliTests(unittest.TestCase):
    """`autoos-agent.py heartbeat`: text and --json, exit-code precedence
    (3 pause > 4 over-cap > 1 unpushed/dirty > 0 clean)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.origin = os.path.join(self.tmp, "origin.git")
        self.work = os.path.join(self.tmp, "work")
        git("init", "-q", "--bare", self.origin, cwd=self.tmp)
        git("init", "-q", self.work, cwd=self.tmp)
        git("commit", "-q", "--allow-empty", "-m", "initial", cwd=self.work)
        git("remote", "add", "origin", self.origin, cwd=self.work)
        git("push", "-q", "-u", "origin", "main", cwd=self.work)
        self.inbox = os.path.join(self.tmp, "inbox.md")
        write_inbox(self.inbox, "2026-09-26T00:00:00Z operator: hello")
        self.transcript = os.path.join(self.tmp, "session.jsonl")
        with io.open(self.transcript, "w", encoding="utf-8") as fh:
            fh.write(assistant_line("claude-opus-4-6", 1000) + "\n")

    def run_heartbeat(self, *extra):
        return subprocess.run(
            [sys.executable, str(AGENT), "heartbeat", "--inbox", self.inbox,
             "--transcript", self.transcript, "--repo", self.work, *extra],
            capture_output=True, text=True, env=clean_env(), stdin=subprocess.DEVNULL)

    def test_a_clean_repo_with_no_pause_and_low_context_is_all_clear(self):
        proc = self.run_heartbeat()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pause: none", proc.stdout)
        self.assertNotIn("unpushed:", proc.stdout)
        self.assertNotIn("dirty:", proc.stdout)
        self.assertIn("context: 1000/400000 0%", proc.stdout)

    def test_json_has_the_same_facts(self):
        proc = self.run_heartbeat("--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["pause"], {"active": False, "at": None, "text": None})
        self.assertEqual(data["repos"], [{"repo": self.work, "unpushed": [], "dirty": 0}])
        self.assertEqual(data["context"]["tokens"], 1000)
        self.assertFalse(data["over_cap"])
        self.assertEqual(data["exit_code"], 0)

    def test_unpushed_and_dirty_are_reported_and_exit_1(self):
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)
        with io.open(os.path.join(self.work, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        proc = self.run_heartbeat()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("unpushed: %s main 1" % self.work, proc.stdout)
        self.assertIn("dirty: %s 1 files" % self.work, proc.stdout)

    def test_pause_active_is_exit_3_and_takes_precedence(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)  # also unpushed
        proc = self.run_heartbeat("--cap", "1")  # also over cap
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("pause: active 2026-09-26T11:19:41Z operator: PAUSE NOW", proc.stdout)

    def test_over_cap_is_exit_4_and_beats_unpushed(self):
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=self.work)  # also unpushed
        proc = self.run_heartbeat("--cap", "1")
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertIn("over-cap", proc.stdout)

    def test_cap_flag_overrides_the_model_table(self):
        proc = self.run_heartbeat("--cap", "2000", "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["context"]["cap"], 2000)
        self.assertEqual(data["context"]["pct"], 50)
        self.assertFalse(data["over_cap"])

    def test_no_repo_flag_defaults_to_cwd(self):
        proc = subprocess.run(
            [sys.executable, str(AGENT), "heartbeat", "--inbox", self.inbox,
             "--transcript", self.transcript, "--json"],
            capture_output=True, text=True, env=clean_env(), cwd=self.work,
            stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["repos"]), 1)
        self.assertEqual(os.path.realpath(data["repos"][0]["repo"]), os.path.realpath(self.work))


class RunRefusesOnPauseTests(unittest.TestCase):
    """`run` refuses to start (exit 3) when AUTOOS_AGENT_INBOX names an inbox
    with an active PAUSE; with no env set, no check happens at all."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox = os.path.join(self._tmp.name, "inbox.md")

    def test_active_pause_refuses_the_run(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        proc = subprocess.run(
            [sys.executable, str(AGENT), "run", "--tier", "2", "--dry-run", "t"],
            capture_output=True, text=True,
            env=clean_env(AUTOOS_AGENT_INBOX=self.inbox), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("PAUSE", proc.stderr)

    def test_resumed_inbox_does_not_refuse(self):
        write_inbox(self.inbox,
                    "2026-09-26T11:19:41Z operator: PAUSE NOW",
                    "2026-09-26T12:00:00Z operator: RESUME")
        proc = subprocess.run(
            [sys.executable, str(AGENT), "run", "--tier", "2", "--dry-run", "t"],
            capture_output=True, text=True,
            env=clean_env(AUTOOS_AGENT_INBOX=self.inbox), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_env_means_no_check_even_with_an_active_pause_file_present(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        proc = subprocess.run(
            [sys.executable, str(AGENT), "run", "--tier", "2", "--dry-run", "t"],
            capture_output=True, text=True, env=clean_env(), stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class McpHeartbeatTests(unittest.TestCase):
    """The `heartbeat` MCP tool and spawn()'s AUTOOS_AGENT_INBOX refusal.

    Every spawn is a dry run with state redirected to a temp dir (the same
    isolation test_autoos_spawner.py's McpToolTests uses), so a refused or
    allowed spawn here never touches this checkout's own logs/agents/.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox = os.path.join(self._tmp.name, "inbox.md")
        state_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, state_dir, True)
        self._old_env = {k: os.environ.get(k) for k in
                        ("AUTOOS_STATE_DIR", "AUTOOS_AGENT_MCP_DRY_RUN",
                         "AUTOOS_AGENT_DEPTH", "AUTOOS_AGENT_MAX_DEPTH", "AUTOOS_AGENT_INBOX")}
        os.environ.update(AUTOOS_STATE_DIR=state_dir, AUTOOS_AGENT_MCP_DRY_RUN="1")
        os.environ.pop("AUTOOS_AGENT_DEPTH", None)
        os.environ.pop("AUTOOS_AGENT_MAX_DEPTH", None)
        os.environ.pop("AUTOOS_AGENT_INBOX", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_heartbeat_tool_returns_the_json_dict(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            out = mcp_server.heartbeat_info(inbox=None, transcript=None, repos=[tmp], cap=None)
        self.assertNotIn("error", out)
        self.assertEqual(out["pause"], {"active": False, "at": None, "text": None})
        self.assertEqual(out["repos"], [{"repo": tmp, "unpushed": [], "dirty": 0}])
        self.assertIn("exit_code", out)

    def test_heartbeat_tool_never_raises_on_a_bad_repo(self):
        out = mcp_server.heartbeat_info(repos=[os.path.join(self._tmp.name, "does-not-exist")])
        self.assertNotIn("error", out)
        self.assertEqual(out["repos"][0]["unpushed"], [])

    def test_spawn_refuses_when_the_inbox_env_names_an_active_pause(self):
        write_inbox(self.inbox, "2026-09-26T11:19:41Z operator: PAUSE NOW")
        with mock.patch.dict(os.environ, {"AUTOOS_AGENT_INBOX": self.inbox}):
            out = mcp_server.spawn({"task": "t", "dry_run": True})
        self.assertIn("error", out)
        self.assertIn("PAUSE", out["error"])

    def test_spawn_with_no_inbox_env_is_unaffected(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOOS_AGENT_INBOX", None)
            out = mcp_server.spawn({"task": "t", "dry_run": True})
        self.assertNotIn("error", out)


if __name__ == "__main__":
    unittest.main()
