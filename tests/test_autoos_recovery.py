#!/usr/bin/env python3
"""Tests for tools/autoos_recovery.py (P4c run-death recovery).

HERMETIC (D-852): every record is a file under a temp dir, time comes from the
module's own `_now` hook, and liveness comes from an injected `pid_probe` — no
git, no subprocess, no network, and no real pid ever signalled.

Run directly:

    python3 tests/test_autoos_recovery.py
"""
import ast
import contextlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_recovery as r  # noqa: E402


NOW = 1800000000.0                     # a pinned epoch second, never a wall clock
RUN = "20261009-182026-task-abc123"
LEG1 = "20261009-182026-leg-one"
LEG2 = "20261009-182026-leg-two"
OTHER_RUN = "20261009-182026-other-999"
TASK = "Implement the widget and run the suite.\nEnd with your report.\n"
SB = "/keep/worktrees/wt-1"            # a shape-only path: never a real directory
BRANCH = "agent/20261009-182026-task-abc123"
LANE = "lane/p4c"
# A run id begins `YYYYMMDD-HHMMSS`: 15 characters, unique per second, which is the
# shortest PREFIX a heading may name this run by (P4c-fixes5 (3)).
STAMP = RUN[:15]                        # "20261009-182026"
SHORT_OF_STAMP = RUN[:14]               # one character short: a minute, not a run
OTHER_STAMP = "20261009-195001-wg-p4c-fixes2"   # a DIFFERENT run's stamp + slug
# The bytes a client that believes it owns a terminal writes into its own stdout.
ESC = "\x1b"
GRN, RST, BOLD = ESC + "[32m", ESC + "[0m", ESC + "[1m"

SKIP = object()                        # "do not create this file"


def fixed_now():
    return NOW


def alive(pid):
    return True


def dead(pid):
    return False


def unprobeable(pid):
    return None


def write_text(path, text, mtime=NOW):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.utime(path, (mtime, mtime))


def make_record(state, run_id=RUN, task=TASK, pid=4242, output="worker thinking\n",
                 mtime=NOW, exit_json=SKIP, job_json=None):
    """One run record exactly as the fleet runner writes it, and its directory."""
    run_dir = os.path.join(state, "agents", run_id)
    os.makedirs(run_dir)
    if job_json is not SKIP:
        job = job_json if isinstance(job_json, str) else {
            "run_id": run_id, "task": task, "cwd": "/repo", "started": mtime,
            "pid": pid, "request": {}, "argv": [], "route": {}}
        write_text(os.path.join(run_dir, "job.json"),
                   job if isinstance(job, str) else json.dumps(job, sort_keys=True),
                   mtime)
    if output is not SKIP:
        write_text(os.path.join(run_dir, "output.log"),
                   "".join(output) if isinstance(output, list) else output, mtime)
    if exit_json is not SKIP:
        body = (exit_json if isinstance(exit_json, str) else
                exit_json if isinstance(exit_json, dict) else
                {"rc": exit_json, "ended": mtime, "family": "nvidia"})
        write_text(os.path.join(run_dir, "exit.json"),
                   body if isinstance(body, str) else json.dumps(body, sort_keys=True),
                   mtime)
    return run_dir


def footer_for(sb, attempt=1, max_attempts=2):
    return ("CONTINUE FROM CURRENT DIFF (recovery attempt %d/%d): your previous run "
            "ended without a REPORT. The sandbox at %s holds your earlier work as "
            "commits (git log); do NOT restart; finish what is missing, run every "
            "required check, and end with the REPORT." % (attempt, max_attempts, sb))


def header_lines(path=SB, branch=BRANCH):
    """The spawner's own FIRST line: printed before the child starts, so a writer
    killed before it could print anything still names its worktree."""
    return ["sandbox: %s (branch %s)\n" % (path, branch)]


def worker_lines(*texts):
    """The child's stream, which the launcher marks with a leading '> ' — the
    boundary after which NO line is the spawner's."""
    return ["> %s\n" % t for t in texts]


def trailer_lines(path=SB, branch=BRANCH):
    """The spawner's closing block, in the order tools/autoos-agent.py's cmd_run
    prints it: `writer:` / `scope:` / `sandbox changes`, then the review/take-it
    pair with the path shlex-quoted as the runner quotes it."""
    q = "'%s'" % path.replace("'", "'\\''")
    return ["writer: nvidia/muse-spark (nvidia) source=witnessed\n",
            "scope: /repo/tools/x.py (unit=file)\n",
            "sandbox changes (uncommitted):\n",
            "  M tools/x.py\n",
            "review:  git -C %s diff\n" % q,
            "take it: git fetch %s %s   (then: git cherry-pick abc123..FETCH_HEAD)\n"
            % (q, branch)]


def spawner_lines(path=SB, branch=BRANCH, report=None):
    """A whole completed run's tail: header, worker stream, REPORT, closing block.

    The REPORT is the worker's own last word, so it sits ABOVE the closing block —
    `tools/autoos-agent.py`'s cmd_run prints that trailer only after the child exits,
    and `_has_report` reads no heading under it (P4c-fixes4 rule 5). The report also
    sits behind a blank line on purpose: under the block rule `_report_view` applies
    (P4c-fixes D1), a bare line right after the '> thinking' marker is that quote
    block's lazy continuation, and a genuine REPORT in a real transcript is its own
    paragraph after the quoted stream ends.
    """
    out = header_lines(path, branch) + worker_lines("thinking")
    if report:
        out.append("\n")
        out.append(report_body())
    return out + trailer_lines(path, branch)


def report_body(run_id=RUN):
    return "\n".join(["worker thinking", "all checks green",
                      "REPORT %s · OK · green" % run_id]) + "\n"


def classify(run_dir, probe=alive, **over):
    kw = {"now": NOW, "pid_probe": probe}
    kw.update(over)
    return r.classify_run(run_dir, **kw)


class TempCase(unittest.TestCase):
    """Pins the module clock and points AUTOOS_STATE_DIR at a fresh tmp dir."""

    def setUp(self):
        self._now = r._now
        r._now = fixed_now
        self._env = os.environ.get("AUTOOS_STATE_DIR")
        self.state = tempfile.mkdtemp()
        os.environ["AUTOOS_STATE_DIR"] = self.state
        self.addCleanup(shutil.rmtree, self.state, True)

    def tearDown(self):
        r._now = self._now
        if self._env is None:
            os.environ.pop("AUTOOS_STATE_DIR", None)
        else:
            os.environ["AUTOOS_STATE_DIR"] = self._env

    def other_state(self):
        """A second temp tree, for the cases that must not share a state dir."""
        st = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, st, True)
        return st

    def sb(self, name="wg-p4c-1", state=None):
        """A REAL sandbox inside the trusted root of `state` (the record's own
        state root by default), and its path. Idempotent: a test may name the
        same sandbox in its setup and in its assertion."""
        st = state or self.state
        path = os.path.join(st, "sandboxes", name)
        os.makedirs(path, exist_ok=True)
        return path


class States(TempCase):
    def test_running_when_pid_alive_and_output_fresh(self):
        info = classify(make_record(self.state), probe=alive)
        self.assertEqual(info["state"], "running")
        self.assertIsNone(info["rc"])
        self.assertFalse(info["has_report"])
        self.assertEqual(info["last_output_age"], 0.0)
        self.assertIsNone(info["sandbox"])
        self.assertIsNone(info["branch"])

    def test_stalled_when_alive_but_quiet(self):
        info = classify(make_record(self.state, mtime=NOW - 901), probe=alive)
        self.assertEqual(info["state"], "stalled")
        self.assertEqual(info["last_output_age"], 901.0)

    def test_the_boundary_at_stall_secs_is_still_running(self):
        root = make_record(self.state, mtime=NOW - r.STALL_SECS)
        info = classify(root)
        self.assertEqual(info["last_output_age"], float(r.STALL_SECS))
        self.assertEqual(info["state"], "running")

    def test_the_boundary_honours_a_custom_stall_secs(self):
        root = make_record(self.state, mtime=NOW - 60)
        self.assertEqual(classify(root, stall_secs=60)["state"], "running")
        self.assertEqual(classify(root, stall_secs=59)["state"], "stalled")

    def test_died_when_the_pid_is_dead_even_with_fresh_output(self):
        self.assertEqual(classify(make_record(self.state), probe=dead)["state"], "died")

    def test_completed_needs_rc_zero_and_a_report(self):
        sb = self.sb()
        info = classify(make_record(self.state, exit_json={"rc": 0},
                                    output=spawner_lines(sb, report=True)),
                        probe=dead)
        self.assertEqual(info["state"], "completed")
        self.assertEqual(info["rc"], 0)
        self.assertTrue(info["has_report"])

    def test_exit_zero_without_a_report_is_died(self):
        info = classify(make_record(self.state, exit_json={"rc": 0}), probe=dead)
        self.assertEqual((info["state"], info["has_report"]), ("died", False))

    def test_nonzero_rc_with_a_report_is_died(self):
        """Decided: a REPORT alone never reads as completed — rc 0 is required."""
        info = classify(make_record(self.state, exit_json={"rc": 1},
                                    output=[report_body()]), probe=dead)
        self.assertEqual((info["state"], info["rc"], info["has_report"]),
                         ("died", 1, True))

    def test_a_cancelled_run_has_no_rc_and_reads_as_died(self):
        info = classify(make_record(self.state, exit_json={"cancelled": True,
                                                            "rc": None, "ended": NOW}),
                        probe=dead)
        self.assertEqual((info["state"], info["rc"]), ("died", None))

    def test_a_rc_that_is_not_an_int_is_never_zero(self):
        for rc in ("0", 0.0, True, None, [0], "0.0"):
            info = classify(make_record(self.other_state(), exit_json={"rc": rc},
                                        output=[report_body()]), probe=dead)
            self.assertEqual(info["state"], "died", rc)
            self.assertIsNone(info["rc"], rc)

    def test_an_unprobeable_pid_is_unknown_not_a_guess(self):
        self.assertEqual(classify(make_record(self.state), probe=unprobeable)["state"],
                         "unknown")

    def test_the_real_probe_reads_a_pid_that_is_not_a_number_as_unknown(self):
        root = make_record(self.state, job_json=json.dumps({"run_id": RUN, "task": TASK}))
        self.assertEqual(classify(root, probe=None)["state"], "unknown")

    def test_no_output_and_no_job_record_is_unknown(self):
        root = make_record(self.state, output=SKIP)
        os.unlink(os.path.join(root, "job.json"))
        self.assertIsNone(classify(root)["last_output_age"])
        self.assertEqual(classify(root)["state"], "unknown")

    def test_garbage_job_json_is_unknown_even_while_the_pid_is_alive(self):
        root = make_record(self.state, job_json="{not json at all")
        self.assertEqual(classify(root)["state"], "unknown")

    def test_garbage_exit_json_is_unknown_even_with_a_report(self):
        root = make_record(self.state, output=[report_body()], exit_json="{}{}")
        self.assertEqual(classify(root)["state"], "unknown")

    def test_a_record_that_is_not_an_object_is_unknown(self):
        for body in ("[1, 2]", '"a string"', "null", "17", ""):
            info = classify(make_record(self.other_state(), exit_json=body), probe=dead)
            self.assertEqual(info["state"], "unknown", body)
            self.assertNotEqual(info["state"], "completed", body)

    def test_the_state_is_one_of_the_five_names(self):
        for probe, exit_json, mtime in ((alive, SKIP, NOW), (alive, SKIP, NOW - 901),
                                       (dead, SKIP, NOW), (dead, {"rc": 0}, NOW),
                                       (unprobeable, SKIP, NOW)):
            st = self.other_state()
            root = make_record(st, exit_json=exit_json, mtime=mtime,
                               output=spawner_lines(self.sb(state=st), report=True))
            self.assertIn(classify(root, probe=probe)["state"], r.STATES)

    @unittest.skipIf(os.name == "nt", "symlinked record file; POSIX only")
    def test_a_run_dir_whose_record_is_a_symlink_is_unknown_not_read(self):
        """O_NOFOLLOW: a symlinked job.json is a damaged record, not a hint."""
        root = make_record(self.state)
        target = os.path.join(self.state, "elsewhere.json")
        write_text(target, json.dumps({"run_id": RUN, "task": TASK, "pid": 4242}))
        os.unlink(os.path.join(root, "job.json"))
        os.symlink(target, os.path.join(root, "job.json"))
        self.assertEqual(classify(root)["state"], "unknown")


class SandboxParsing(TempCase):
    def classified(self, output, probe=dead, state=None):
        st = state or self.state
        return classify(make_record(st, output=output), probe=probe)

    def test_the_header_line_names_the_worktree(self):
        """`sandbox:` is printed before the child starts, so a writer killed while
        its first tool call ran still names its kept worktree."""
        sb = self.sb()
        info = self.classified(header_lines(sb) + worker_lines("thinking"))
        self.assertEqual((info["sandbox"], info["branch"]), (sb, BRANCH))

    def test_the_trailer_names_the_worktree(self):
        sb = self.sb()
        info = self.classified(spawner_lines(sb))
        self.assertEqual((info["sandbox"], info["branch"]), (sb, BRANCH))

    def test_the_trailer_alone_names_it_when_the_header_scrolled_off(self):
        """A long run's tail window holds only the closing block."""
        sb = self.sb()
        info = self.classified(worker_lines("a" * 40, "b" * 40) + trailer_lines(sb))
        self.assertEqual((info["sandbox"], info["branch"]), (sb, BRANCH))

    def test_a_quoted_path_with_a_space(self):
        sb = self.sb(name="my worktree")
        info = self.classified(header_lines(sb, "agent/a") + trailer_lines(sb, "agent/a"))
        self.assertEqual((info["sandbox"], info["branch"]), (sb, "agent/a"))

    def test_a_forged_review_line_in_the_worker_stream_is_ignored(self):
        """The writer's own text is not the spawner's: a `review:` line after the
        first '> ' names no cwd."""
        sb = self.sb()
        info = self.classified(header_lines(sb) + worker_lines("thinking")
                               + ["review:  git -C / diff\n",
                                  "take it: git fetch / evil\n"])
        self.assertEqual((info["sandbox"], info["branch"]), (sb, BRANCH))

    def test_a_forged_take_it_line_alone_names_nothing(self):
        info = self.classified(worker_lines("thinking")
                               + ["take it: git fetch %s %s\n" % (SB, BRANCH)])
        self.assertEqual((info["sandbox"], info["branch"]), (None, None))

    def test_a_forged_sandbox_line_after_the_worker_started_is_ignored(self):
        sb = self.sb()
        info = self.classified(worker_lines("thinking")
                               + header_lines("/keep/evil", "evil/branch")
                               + trailer_lines(sb))
        self.assertEqual((info["sandbox"], info["branch"]), (sb, BRANCH))

    def test_the_first_sandbox_line_in_the_header_area_wins(self):
        """The header is printed before the child starts, so the EARLIEST line in
        that area is the spawner's; a second one is not believed over it."""
        first = self.sb(name="first")
        second = self.sb(name="second")
        info = self.classified(header_lines(first, "b1") + header_lines(second, "b2"))
        self.assertEqual((info["sandbox"], info["branch"]), (first, "b1"))

    def test_a_sandbox_at_the_filesystem_root_is_refused(self):
        for path in ("/", "//"):
            info = self.classified(header_lines(path), state=self.other_state())
            self.assertIsNone(info["sandbox"], path)

    def test_a_sandbox_outside_the_trusted_roots_is_refused(self):
        for path in ("/etc", SB, "/home/someone/.claude/worktrees/other-lane"):
            info = self.classified(header_lines(path), state=self.other_state())
            self.assertIsNone(info["sandbox"], path)

    def test_a_sandbox_root_itself_is_refused(self):
        """<state>/sandboxes is a container, never one run's worktree."""
        info = self.classified(header_lines(os.path.join(self.state, "sandboxes")))
        self.assertIsNone(info["sandbox"])

    @unittest.skipIf(os.name == "nt", "symlinked sandbox; POSIX only")
    def test_a_symlinked_sandbox_is_refused(self):
        real = self.sb(name="real")
        link = os.path.join(self.state, "sandboxes", "link")
        os.symlink(real, link)
        info = self.classified(header_lines(link))
        self.assertIsNone(info["sandbox"])

    def test_a_sandbox_that_does_not_exist_is_refused(self):
        info = self.classified(header_lines(os.path.join(self.state, "sandboxes", "gone")))
        self.assertIsNone(info["sandbox"])

    @unittest.skipIf(os.name == "nt", "HOME does not pick the fleet root on Windows")
    def test_a_sandbox_under_the_fleet_root_is_trusted(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        path = os.path.join(home, "fleet", "sandboxes", "repo", "run-1")
        os.makedirs(path)
        _home = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            info = self.classified(header_lines(path), state=self.other_state())
            self.assertEqual(info["sandbox"], path)
        finally:
            if _home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = _home

    def test_a_header_outside_the_roots_does_not_hide_a_trusted_trailer(self):
        sb = self.sb()
        info = self.classified(header_lines("/etc") + worker_lines("thinking")
                               + trailer_lines(sb))
        self.assertEqual((info["sandbox"], info["branch"]), (sb, BRANCH))

    def test_a_forged_trailer_path_is_still_refused(self):
        """The closing block is the spawner's, but a path in it must still be a
        real sandbox under a trusted root — `review:  git -C / diff` names no
        directory a continuation may run in."""
        lines = (worker_lines("x") + ["writer: nvidia/m (nvidia) source=witnessed\n",
                                      "sandbox changes (uncommitted):\n",
                                      "review:  git -C / diff\n",
                                      "take it: git fetch / evil\n"])
        info = self.classified(lines, state=self.other_state())
        self.assertEqual((info["sandbox"], info["branch"]), (None, None))

    def test_a_relative_path_is_refused(self):
        self.assertIsNone(self.classified(header_lines("relative/wt"))["sandbox"])

    def test_parent_traversal_is_refused(self):
        for path in ("/keep/../../etc", "/keep/../x", "/.."):
            info = self.classified(header_lines(path), state=self.other_state())
            self.assertIsNone(info["sandbox"], path)

    def test_a_control_character_is_refused(self):
        self.assertIsNone(self.classified(
            header_lines(os.path.join(self.state, "sandboxes", "a\x01b")))["sandbox"])

    def test_an_unexpanded_home_is_refused(self):
        self.assertIsNone(self.classified(header_lines("~/worktrees/wt"))["sandbox"])

    def test_quoting_that_does_not_parse_is_refused(self):
        self.assertIsNone(self.classified(
            worker_lines("x") + ["writer: w (w) source=s\n",
                                 "review:  git -C /keep/unclosed' diff\n"])["sandbox"])

    def test_a_review_line_that_is_not_a_diff_is_ignored(self):
        sb = self.sb()
        self.assertIsNone(self.classified(
            worker_lines("x") + ["writer: w (w) source=s\n",
                                 "review:  git -C %s status\n" % sb])["sandbox"])

    def test_a_branch_that_cannot_be_pasted_is_none_and_the_worktree_kept(self):
        sb = self.sb()
        lines = (worker_lines("x") + ["writer: w (w) source=s\n",
                                      "sandbox changes (uncommitted):\n",
                                      "review:  git -C %s diff\n" % sb,
                                      "take it: git fetch %s 'not a name'\n" % sb])
        info = self.classified(lines)
        self.assertEqual((info["sandbox"], info["branch"]), (sb, None))

    def test_an_overlong_path_is_refused(self):
        self.assertIsNone(self.classified(
            header_lines(os.path.join(self.state, "sandboxes", "d" * r.PATH_MAX_CHARS))
        )["sandbox"])

    def test_only_the_tail_is_read(self):
        """A worktree line older than the read window is simply not there."""
        filler = ["f" * 1023 + "\n"] * ((r.TAIL_BYTES // 1024) + 4)
        root = make_record(self.state, output=header_lines(self.sb()) + filler)
        self.assertGreater(os.path.getsize(os.path.join(root, "output.log")), r.TAIL_BYTES)
        self.assertIsNone(classify(root, probe=dead)["sandbox"])

    def test_a_line_inside_the_tail_is_read(self):
        root = make_record(self.state, output=["f" * 1023 + "\n"] * 8
                           + header_lines(self.sb()))
        self.assertEqual(classify(root, probe=dead)["sandbox"], self.sb())

    def test_no_output_log_at_all(self):
        info = classify(make_record(self.state, output=SKIP), probe=dead)
        self.assertEqual((info["sandbox"], info["branch"]), (None, None))

    def test_the_roots_are_the_state_sandboxes_and_the_fleet_sandboxes(self):
        roots = r.sandbox_roots(self.state)
        self.assertEqual(roots[0], os.path.join(self.state, "sandboxes"))
        self.assertEqual(os.path.basename(os.path.dirname(roots[1])), "fleet")
        self.assertEqual(os.path.basename(roots[1]), "sandboxes")


class TrustedSandboxUnit(TempCase):
    def test_a_trusted_real_directory_passes(self):
        sb = self.sb()
        self.assertEqual(r._trusted_sandbox(sb, self.state), sb)

    def test_the_shape_refusals_stay_refusals(self):
        for bad in (None, 7, "", "/keep/../x", "relative/x", "~/x", "/keep/a\x01b"):
            self.assertIsNone(r._trusted_sandbox(bad, self.state), bad)

    def test_a_path_of_only_separators_is_no_path(self):
        for bad in ("/", "\\", "//", os.sep):
            self.assertIsNone(r._valid_path(bad), bad)


class ReportDetection(TempCase):
    def classified(self, output, state=None, task=TASK):
        return classify(make_record(state or self.state, output=output, task=task,
                                    exit_json={"rc": 0}), probe=dead)

    def test_a_protocol_report_block_counts(self):
        self.assertTrue(self.classified([report_body()])["has_report"])

    def test_a_markdown_heading_counts(self):
        self.assertTrue(self.classified(["# REPORT\n", "status: done\n"])["has_report"])

    def test_prose_about_reporting_does_not_count(self):
        for line in ("I will report when done\n", "REPORTED: nothing\n",
                     "see REPORT.md for detail\n", "the contract says report\n"):
            self.assertFalse(self.classified([line], state=self.other_state())
                             ["has_report"], line)

    def test_an_echoed_task_report_is_not_a_report(self):
        """output.log echoes the brief: a REPORT block that is a copy of a task
        line is the task's own text, whatever run id it carries."""
        task = "do the work\n%s\nfinish up\n" % report_body().rstrip("\n")
        out = self.classified([task], task=task)
        self.assertFalse(out["has_report"])
        self.assertEqual(out["state"], "died")
        # The same record with a heading of its own does count.
        self.assertTrue(self.classified([task, "\n**REPORT**\n"], task=task,
                                       state=self.other_state())["has_report"])

    def test_an_echoed_task_heading_is_not_a_report(self):
        task = "End with the REPORT when done.\n"
        self.assertFalse(self.classified([task], task=task,
                                         state=self.other_state())["has_report"])

    def test_a_fenced_report_is_not_a_report(self):
        for fence in ("```", "~~~"):
            self.assertFalse(self.classified([fence + "\n", report_body(), fence + "\n"],
                                             state=self.other_state())["has_report"], fence)

    def test_an_unclosed_fence_swallows_the_rest(self):
        self.assertFalse(self.classified(["```python\n", report_body()],
                                         state=self.other_state())["has_report"])

    def test_a_fence_closes_only_on_the_same_char_at_least_as_long(self):
        body = report_body()
        self.assertFalse(self.classified(["````\n", body, "```\n"],
                                         state=self.other_state())["has_report"])
        self.assertFalse(self.classified(["```\n", body, "~~~\n"],
                                         state=self.other_state())["has_report"])

    def test_a_report_after_a_closed_fence_counts(self):
        self.assertTrue(self.classified(["```sh\n", "pytest -q\n", "```\n", report_body()],
                                        state=self.other_state())["has_report"])

    def test_a_report_closer_with_trailing_text_does_not_close_the_fence(self):
        self.assertFalse(self.classified(["```\n", report_body(), "``` trailing\n"],
                                         state=self.other_state())["has_report"])

    def test_a_quoted_report_is_not_a_report(self):
        for line in ("> %s" % report_body().rstrip("\n").splitlines()[-1],
                     "> REPORT: the contract's field list",
                     ">> REPORT %s · OK" % RUN):
            self.assertFalse(self.classified([line + "\n"], state=self.other_state())
                             ["has_report"], line)

    def test_a_lazy_continuation_report_under_a_quote_is_not_a_report(self):
        """The REPORT line carries no '>' of its own — CommonMark continues the
        block-quote block over every following NON-BLANK line — so it is quoted
        text inside the worker's own blockquote, not a claim (P4c-fixes D1)."""
        out = self.classified("> worker quoting a report:\n"
                              "REPORT %s · OK · green\n" % RUN,
                              task="Implement the widget.\n")
        self.assertFalse(out["has_report"])
        self.assertEqual(out["state"], "died")
        self.assertEqual(r.next_action(0, out["state"]), "rerun")

    def test_a_report_after_a_blank_line_following_a_quote_counts(self):
        """The blank line is what closes the block: a REPORT after it is the
        worker's own line again."""
        self.assertTrue(self.classified(["> worker quoting a report:\n", "\n",
                                         report_body()],
                                        state=self.other_state())["has_report"])

    def test_nested_and_tab_and_space_indented_markers_open_the_same_block(self):
        for head in (">> quoted:\n", ">\tquoted:\n", "\t> quoted:\n", "   > quoted:\n"):
            self.assertFalse(self.classified([head,
                                              "REPORT %s · OK · green\n" % RUN],
                                              state=self.other_state())["has_report"], head)

    def test_a_quote_block_directly_followed_by_a_fence_does_not_count(self):
        """No blank line between them: the fence opens inside the still-live
        quote block, and its content is quoted text twice over."""
        self.assertFalse(self.classified(["> note\n", "```\n", report_body(), "```\n"],
                                         state=self.other_state())["has_report"])

    def test_a_fence_after_a_closed_quote_still_leaves_a_report_counting(self):
        self.assertTrue(self.classified(["> note\n", "\n", "```sh\n", "pytest -q\n",
                                         "```\n", report_body()],
                                        state=self.other_state())["has_report"])

    def test_a_report_naming_another_run_is_not_this_run_s_report(self):
        self.assertFalse(self.classified([report_body(OTHER_RUN)],
                                         state=self.other_state())["has_report"])
        self.assertFalse(self.classified(["REPORT %s\n" % OTHER_RUN],
                                         state=self.other_state())["has_report"])

    def test_the_genuine_report_of_this_run_still_completes(self):
        # The blank line ends the '> thinking' quote block; the report after it
        # is the worker's own paragraph (see spawner_lines).
        info = self.classified(worker_lines("thinking") + ["\n", report_body()])
        self.assertTrue(info["has_report"])
        self.assertEqual(info["state"], "completed")

    def test_a_bare_heading_with_no_run_id_counts(self):
        self.assertTrue(self.classified(["**REPORT**\n", "done\n"],
                                        state=self.other_state())["has_report"])

    # P4c-fixes3 (1): `str.strip()` also empties U+00A0, \x0b, \x0c, U+0085,
    # U+2028, U+3000..., so a line holding only one of those separators used to
    # CLOSE the block quote and hand the REPORT under it back as a claim. A
    # blank line is ONLY ' ' and '\t', and lines are cut on '\n' only.
    LINE_BREAK_LIKES = ("\u00a0", "\x0b", "\x0c", "\x85", "\u2028", "\u2029", "\u3000")

    def test_a_separator_line_does_not_close_the_quote(self):
        for sep in self.LINE_BREAK_LIKES:
            out = self.classified("> worker quoting a report:\n%s\n"
                                  "REPORT %s \u00b7 OK \u00b7 green\n" % (sep, RUN),
                                  task="Implement the widget.\n",
                                  state=self.other_state())
            self.assertFalse(out["has_report"], repr(sep))
            self.assertEqual(out["state"], "died", repr(sep))
            self.assertEqual(r.next_action(0, out["state"]), "rerun", repr(sep))

    def test_a_space_or_tab_only_line_closes_the_quote_and_the_report_counts(self):
        for blank in ("", " ", "\t", "  \t ", "   "):
            self.assertTrue(self.classified(["> worker quoting a report:\n",
                                             blank + "\n", report_body()],
                                            state=self.other_state())["has_report"],
                            repr(blank))

    def test_a_zwsp_line_is_not_a_blank_line(self):
        """U+200B is invisible but never was blank for str.strip(); it stays a
        lazy continuation of the quote, so the REPORT under it is still quoted."""
        self.assertFalse(self.classified(["> worker quoting a report:\n",
                                          "\u200b\n", report_body()],
                                         state=self.other_state())["has_report"])

    def test_a_splitlines_break_does_not_split_a_line(self):
        """splitlines() breaks at \\x0b/\\x0c/U+0085/U+2028/U+2029: a heading and
        a forged one on the same physical line used to read as two. Cutting on
        '\\n' only keeps them one suspect line, and the whole report fails closed."""
        for bad in self.LINE_BREAK_LIKES:
            self.assertFalse(self.classified(["worker says%sdone\n" % bad,
                                              report_body()],
                                             state=self.other_state())["has_report"],
                             repr(bad))

    def test_a_separator_inside_the_report_region_fails_it_closed(self):
        """Not a quote case at all: a genuine report with a separator/control
        character somewhere in the worker's own lines is not believed — the text
        another reader could re-split or re-strip into a different verdict is no
        verdict (has_report False, fail closed).

        '\\r' is off this list since P4c-fixes5 (1): a carriage return is a terminal
        REDRAW, and `_normalise_output` gives it that meaning before the screen runs.
        What is left here is every character the normaliser may not explain away."""
        for bad in self.LINE_BREAK_LIKES:
            self.assertFalse(self.classified([report_body(), "a%sb\n" % bad],
                                              state=self.other_state())["has_report"],
                             repr(bad))

    def test_a_crlf_log_still_reads_its_report(self):
        """One trailing '\\r' per line is dropped, so a CRLF output.log is a
        normal log; a '\\r' inside a line is the terminal's in-place redraw, and only
        what a screen would have left — the text after the last one — is read."""
        self.assertTrue(self.classified("# REPORT\r\ndone\r\n",
                                        state=self.other_state())["has_report"])
        # 'REPORT <id>\ryes' redraws over the claim: the line reads 'yes', and a
        # line that says 'yes' is not a report.
        self.assertFalse(self.classified(["REPORT %s\ryes\n" % RUN],
                                         state=self.other_state())["has_report"])

    # P4c-fixes3 (2): a REPORT inside an HTML comment is not a claim.
    def test_a_commented_out_report_is_not_a_report(self):
        cases = [
            ["<!--\n", report_body(), "-->\n"],                       # spans lines
            ["<!--\n", report_body()],                                 # unclosed swallows the rest
            ["<!-- %s -->\n" % report_body().rstrip("\n")],            # closed on the same line
            ["prose <!-- start\n", report_body(), "still inside\n"],   # open, never shut
            ["worker note <!-- aside --> REPORT %s \u00b7 OK\n" % RUN],  # shares its line
        ]
        for lines in cases:
            self.assertFalse(self.classified(lines, state=self.other_state())
                             ["has_report"], lines)

    def test_a_report_after_a_closed_comment_counts(self):
        self.assertTrue(self.classified(["<!-- old work, not a claim -->\n",
                                         report_body()],
                                        state=self.other_state())["has_report"])
        self.assertTrue(self.classified(["<!--\n", "nothing here\n", "-->\n",
                                         report_body()],
                                        state=self.other_state())["has_report"])
        # A bare `-->` with no open comment is text, not markup.
        self.assertTrue(self.classified(["unmatched --> close\n", report_body()],
                                        state=self.other_state())["has_report"])

    def test_a_fence_beats_a_comment_marker(self):
        """`<!--` inside a fence is code text: it opens no comment region and
        must not swallow the genuine report that follows the closed fence."""
        self.assertTrue(self.classified(["```sh\n", "# <!-- not markup\n", "```\n",
                                         report_body()],
                                        state=self.other_state())["has_report"])
        self.assertFalse(self.classified(["```sh\n", "# <!-- not markup\n",
                                          report_body()],
                                         state=self.other_state())["has_report"])

    # P4c-fixes4 (A/B): the reader is an ALLOWLIST, not a blacklist of the markdown
    # construct this round's probe happened to find. A claim is the LAST column-0
    # heading of the worker's own lines, BEFORE the spawner's closing block — so an
    # indented line, an HTML block's payload, and anything under a forged trailer are
    # not claims at all, whatever rc the record carries.
    INDENTED = ("    ", "\t", "        ", "   \t")

    def test_an_indented_report_is_not_a_report(self):
        """The re-seat probe's exact shape: 'worker text\\n\\n    REPORT <id> · OK'
        with rc 0 used to read completed / action none / exit 0."""
        claim = "REPORT %s · OK · green\n" % RUN
        for indent in self.INDENTED:
            out = self.classified(["worker text\n", "\n", indent + claim],
                                  state=self.other_state())
            self.assertFalse(out["has_report"], repr(indent))
            self.assertEqual(out["state"], "died", repr(indent))
            self.assertEqual(r.next_action(0, out["state"]), "rerun", repr(indent))

    def test_eight_spaces_after_a_list_item_is_no_heading(self):
        """Not a code block (no blank line precedes the run) and not a heading
        either: it is indented, and rule 1 refuses it on the column alone."""
        self.assertFalse(self.classified(["- step one\n",
                                          "        REPORT %s · OK · green\n" % RUN],
                                         state=self.other_state())["has_report"])

    def test_a_report_inside_an_html_block_is_not_a_report(self):
        claim = "REPORT %s · OK · green\n" % RUN
        for opener in ("<pre>\n", "<PRE>\n", "<script>\n", "<SCRIPT>\n", "<style>\n",
                       "<textarea>\n", "<details>\n", "<div>\n", "<DIV>\n", "<p>\n",
                       "<!DOCTYPE html>\n", "<?xml version=\"1.0\"?>\n"):
            out = self.classified(["worker text\n", "\n", opener, claim],
                                  state=self.other_state())
            self.assertFalse(out["has_report"], opener)
            self.assertEqual(out["state"], "died", opener)
            self.assertEqual(r.next_action(0, out["state"]), "rerun", opener)

    def test_an_unclosed_pre_swallows_the_rest(self):
        """The raw-text tags end on their closing tag, not on a blank line: an
        unclosed `<pre>` is an inert tail, and the report under it says nothing."""
        self.assertFalse(self.classified(["<pre>\n", report_body(), "\n",
                                          report_body()],
                                         state=self.other_state())["has_report"])

    def test_a_leading_angle_bracket_is_no_heading(self):
        """'<REPORT …' is not a heading — and it opens an HTML block besides."""
        for line in ("<REPORT %s · OK · green\n" % RUN,
                     "<b>REPORT %s · OK</b>\n" % RUN):
            self.assertFalse(self.classified([line], state=self.other_state())
                             ["has_report"], line)

    def test_an_html_block_closed_by_a_blank_line_leaves_a_report_counting(self):
        self.assertTrue(self.classified(["<div>\n", "  <p>html note</p>\n", "\n",
                                         report_body()],
                                        state=self.other_state())["has_report"])

    def test_a_report_after_an_unrelated_html_block_counts(self):
        """Fail closed is not fail always: a block that ended — its own closing tag,
        or a blank line — hands the tail back to the worker's own lines."""
        self.assertTrue(self.classified(["<pre>$ pytest -q\n", "4 passed\n", "</pre>\n",
                                         report_body()],
                                        state=self.other_state())["has_report"])
        self.assertTrue(self.classified(["<div>notes</div>\n", "\n", report_body()],
                                        state=self.other_state())["has_report"])
        self.assertTrue(self.classified(["<pre>x</pre>\n", "\n", report_body()],
                                        state=self.other_state())["has_report"])

    def test_a_report_under_a_trailer_line_is_not_the_workers_claim(self):
        """cmd_run prints `writer:` / `scope:` / `sandbox changes` after the child
        exits, so nothing below them is a heading the worker printed — and a worker
        that forges one loses the claim under it (one wasted leg, no false 0)."""
        claim = "REPORT %s · OK · green\n" % RUN
        for head in ("writer: nvidia/muse-spark (nvidia) source=witnessed\n",
                     "scope: /repo/tools/x.py (unit=file)\n",
                     "sandbox changes (uncommitted):\n"):
            self.assertFalse(self.classified(["worker text\n", head, claim],
                                             state=self.other_state())["has_report"], head)

    def test_a_genuine_report_above_the_trailer_still_completes(self):
        info = self.classified(["worker text\n", "\n", report_body()] + trailer_lines())
        self.assertTrue(info["has_report"])
        self.assertEqual(info["state"], "completed")

    def test_only_the_last_column_zero_heading_decides(self):
        """A transcript whose LAST claim names another run is not this run's report,
        whichever heading came before it."""
        self.assertFalse(self.classified(["worker text\n", "\n",
                                          "REPORT %s · OK · green\n" % RUN, "\n",
                                          "REPORT %s · OK · green\n" % OTHER_RUN],
                                         state=self.other_state())["has_report"])
        self.assertTrue(self.classified(["worker text\n", "\n",
                                         "REPORT %s · OK · green\n" % OTHER_RUN, "\n",
                                         "REPORT %s · OK · green\n" % RUN],
                                         state=self.other_state())["has_report"])

    def test_the_parser_never_grants_a_claim_the_view_dropped(self):
        """`autoos_report.parse_report` alone reads the fenced block below and says
        a run reported; the module hands it only the surviving lines, and the heading
        is what makes a report at all."""
        self.assertIsNotNone(r.report_parser.parse_report(report_body()))
        fenced = ["```sh\n", report_body(), "```\n"]
        self.assertFalse(self.classified(fenced, state=self.other_state())["has_report"])
        indented = ["worker text\n", "\n",
                    "    " + report_body().replace("\n", "\n    ")]
        self.assertFalse(self.classified(indented, state=self.other_state())["has_report"])
        html = ["<pre>\n", report_body(), "</pre>\n"]
        self.assertFalse(self.classified(html, state=self.other_state())["has_report"])

    def test_the_parser_and_the_heading_must_agree_on_who_reported(self):
        """The last heading is this run's bare contract line, but the LAST block the
        parser can read names another run: two readers, two answers, no report."""
        out = self.classified(["REPORT %s · OK\n" % OTHER_RUN, "\n", "# REPORT\n",
                               "status: done\n"], state=self.other_state())
        self.assertFalse(out["has_report"])
        self.assertEqual(out["state"], "died")

    def test_empty_output_has_no_report(self):
        self.assertFalse(self.classified("")["has_report"])

    def test_a_missing_task_never_disqualifies_a_real_report(self):
        root = make_record(self.state, task=TASK, output=[report_body()],
                           exit_json={"rc": 0})
        os.unlink(os.path.join(root, "job.json"))
        self.assertTrue(classify(root, probe=dead)["has_report"])


class AnsiAndRedraws(TempCase):
    """P4c-fixes5 (1): ``output.log`` is the CLIENT's raw captured stdout. A CLI that
    believes it owns a terminal paints its progress in colour and redraws a line in
    place, and `_suspect_line` — which refuses every Cc but '\\t' — read that as a
    writer that printed control characters: WHOLE report suspect, a writer that
    finished and reported classified 'died', two wasted legs, then an escalation.
    The tail is normalised before any reader sees it, so the screen a reader with no
    colour would have seen is what the line rules are asked about — and everything
    the normaliser cannot explain (a lone ESC, an unterminated sequence, a U+2028)
    still fails closed exactly as before."""

    def classified(self, output, state=None, task=TASK):
        return classify(make_record(state or self.other_state(), output=output,
                                    task=task, exit_json={"rc": 0}), probe=dead)

    def test_colour_and_a_progress_redraw_before_a_report_complete_the_run(self):
        out = ["%srunning tests...%s\n" % (GRN, RST),
               "%s12 passed%s in 1.4s\n" % (GRN, RST),
               "resolving deps: 10%%\r25%%\r100%%\r\n",
               "\n", report_body()]
        info = self.classified(out)
        self.assertTrue(info["has_report"])
        self.assertEqual(info["state"], "completed")
        self.assertEqual(r.next_action(0, info["state"]), "none")

    def test_ansi_inside_the_report_heading_counts_after_the_strip(self):
        """The real shape is a client that BOLDS the word itself: '\x1b[1mREPORT\x1b[0m
        <id> · OK · green' is the heading once the paint is removed."""
        self.assertTrue(self.classified(["%sREPORT%s %s \u00b7 OK \u00b7 green\n"
                                         % (BOLD, RST, RUN)])["has_report"])

    def test_an_osc_title_and_a_charset_designator_are_stripped_too(self):
        for extra in (ESC + "]0;pytest \u2014 widget suite\x07",        # OSC, BEL-terminated
                      ESC + "]2;title" + ESC + "\\",                    # OSC, ST-terminated
                      ESC + "(B" + ESC + ")0",                          # nF charset sets
                      ESC + "[?25l" + ESC + "[?25h"):                   # CSI private modes
            self.assertTrue(self.classified([extra + "\n", report_body()],
                                            state=self.other_state())["has_report"],
                            repr(extra))

    def test_a_forged_line_break_under_the_colour_is_still_suspect(self):
        """The strip may remove paint and nothing else: the same coloured transcript
        with a real U+2028 (or NEL, or FF) inside a worker line fails closed."""
        for bad in ReportDetection.LINE_BREAK_LIKES:
            out = ["%srunning tests...%s\n" % (GRN, RST),
                   "worker says%sdone\n" % bad,
                   "progress: 10%%\r100%%\r\n",
                   report_body()]
            self.assertFalse(self.classified(out, state=self.other_state())
                             ["has_report"], repr(bad))

    def test_a_lone_esc_is_not_a_sequence_and_is_still_suspect(self):
        for bad in ("trailing esc " + ESC,
                    "esc then a letter " + ESC + "ept",       # reserved Fp, not paint
                    "esc then a space " + ESC + " d",
                    "unterminated csi " + ESC + "[3",
                    "unterminated osc " + ESC + "]0;title"):
            self.assertFalse(self.classified([bad + "\n", report_body()],
                                             state=self.other_state())["has_report"],
                             repr(bad))

    def test_an_unterminated_osc_cannot_swallow_a_line_break(self):
        """An OSC payload may not cross a newline, so an escape that never terminated
        leaves its ESC on line one and its BEL on line two — both Cc, both suspect —
        instead of eating the break between them and handing back a joined claim."""
        out = [ESC + "]0;evil\n", "REPORT %s \u00b7 OK \u00b7 green\x07\n" % RUN]
        self.assertFalse(self.classified(out)["has_report"])
        self.assertEqual(self.classified(out)["state"], "died")

    def test_a_trailing_carriage_return_run_is_the_screen_not_a_forgery(self):
        """One trailing '\\r' is the CRLF case; a RUN of them is a client that parked
        its cursor at the column before the newline, and a screen shows the same text
        either way. A trailing CR cannot add a character to a line, so it is not read
        as a control character against the report — the suspect rule is for what a
        re-split or re-strip COULD turn into another verdict."""
        self.assertTrue(self.classified([report_body(), "done\r\r\n"])["has_report"])
        self.assertTrue(self.classified([report_body(), "done\r\r\r\n"])["has_report"])

    # --- the normaliser itself, as a unit -------------------------------------
    def test_redraw_line_keeps_what_a_screen_would_have_shown(self):
        for src, want in (("abc", "abc"),                       # no CR at all
                          ("abc\r", "abc"),                     # the CRLF case
                          ("10%\r25%\r100%\r", "100%"),          # progress redraw
                          ("a\rb", "b"),                        # overwrite in place
                          ("\rfirst", "first"),                 # CR at the line head
                          ("a\rb\r", "b"),                      # redraw, then CRLF
                          ("abc\r\r", "abc"),                   # a trailing run shows
                          ("abc\r\r\r", "abc")):               # nothing, and erases
            self.assertEqual(r._redraw_line(src), want, repr(src))
        # No carriage return survives the normaliser, so none can reach the suspect
        # screen: what is left to reject there is a real separator or an ESC.
        for src in ("abc\r", "abc\r\r", "a\rb\r\r\r", "\r", "\r\r\r"):
            self.assertNotIn("\r", r._normalise_output(src), repr(src))

    def test_normalising_never_changes_the_physical_line_count(self):
        """The property the fail-closed rules are built on: the strip removes bytes
        INSIDE a line, so a log's line structure — the one thing `_report_view`, the
        quote logic and the heading column all count on — is invariant."""
        samples = ["%sa\x1b[0m\r\nb\n\n%s\r\nc%d\n" % (GRN, ESC, 7),
                   ESC + "]0;t\x07one\ntwo\r" + ESC + "[2Kthree\n\n",
                   "plain\nlines\nwith no escapes at all\n",
                   ""]
        for s in samples:
            once = r._normalise_output(s)
            self.assertEqual(once.count("\n"), s.count("\n"), repr(s))
            self.assertEqual(r._normalise_output(once), once, repr(s))   # idempotent
            self.assertEqual(len(r._lines(once)), len(r._lines(s)), repr(s))

    def test_normalise_output_is_the_agent_ansi_rule_extended_not_replaced(self):
        """The CSI half is the SAME alternation `tools/autoos-agent.py` strips with
        (its `_ANSI_RE`, mirrored here because that launcher is not importable), so
        what that reader calls colour this reader calls colour too."""
        for painted in ("\x1b[31mError: 429\x1b[0m", "\x1b[2K", "\x1b[1;38;5;214mx\x1b[m",
                        "\x1b[?25l", "\x1b[1A"):
            self.assertEqual(r._ANSI_ESCAPE_RE.sub("", painted),
                             re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]").sub("", painted),
                             repr(painted))


class HeadingDecorations(TempCase):
    """P4c-fixes5 (2): the allowlist of DECORATIONS around the word REPORT is exactly
    two things — an ATX ``#{1,6} `` heading prefix, and markdown bold around the word,
    which is what the real writers print (`REPORT <id> · OK · green` and
    `**REPORT** <id> · status **completed**`). The old ``[#>*-]+`` class also accepted
    a LIST MARKER, so a bullet in the writer's own summary list — `- REPORT <id> ·
    completed …` — read as the return contract's heading and granted a 'completed'."""

    ACCEPTED = ["REPORT %s \u00b7 OK \u00b7 green" % RUN,
                "**REPORT** %s \u00b7 status **completed**" % RUN,
                "**REPORT**",
                "# REPORT",
                "## REPORT %s \u00b7 OK" % RUN,
                "###### REPORT %s" % RUN,
                "**REPORT**: done"]
    REFUSED = ["- REPORT %s \u00b7 completed \u00b7 green" % RUN,
               "-- REPORT %s" % RUN,
               "* REPORT %s \u00b7 completed" % RUN,
               "** REPORT %s" % RUN,
               "*** REPORT %s" % RUN,
               "+ REPORT %s \u00b7 completed" % RUN,
               "> REPORT %s \u00b7 completed" % RUN,
               ">> REPORT %s" % RUN,
               "\u2022 REPORT %s \u00b7 completed" % RUN,        # a real bullet glyph
               "#REPORT %s" % RUN,                               # no ATX space
               "#. REPORT %s" % RUN,
               " - REPORT %s" % RUN,                              # indented anyway
               "REPORTED %s" % RUN,
               "REPORT.md"]

    def matched(self, shape):
        return r._REPORT_HEADING_RE.match(shape)

    def test_only_an_atx_prefix_and_bold_decorate_a_claim(self):
        for shape in self.ACCEPTED:
            self.assertIsNotNone(self.matched(shape), shape)

    def test_a_list_marker_or_quote_is_not_a_decoration(self):
        for shape in self.REFUSED:
            self.assertIsNone(self.matched(shape), shape)

    def test_a_bulleted_report_line_does_not_complete_the_run(self):
        """The exact false positive: rc 0, and the only REPORT-shaped line in the
        transcript is a bullet in the writer's own list of what it did."""
        for bullet in ("- ", "* ", "+ ", "\u2022 "):
            out = self.classified(["worker text\n", "\n",
                                   bullet + "REPORT %s \u00b7 completed \u00b7 green\n" % RUN],
                                  state=self.other_state())
            self.assertFalse(out["has_report"], bullet)
            self.assertEqual(out["state"], "died", bullet)
            self.assertEqual(r.next_action(0, out["state"]), "rerun", bullet)

    def test_a_quoted_report_line_does_not_complete_the_run(self):
        out = self.classified(["> REPORT %s \u00b7 completed\n" % RUN],
                              state=self.other_state())
        self.assertFalse(out["has_report"])

    def classified(self, output, state=None, task=TASK):
        return classify(make_record(state or self.other_state(), output=output,
                                    task=task, exit_json={"rc": 0}), probe=dead)

    def test_the_two_real_writer_shapes_complete(self):
        """Plain `REPORT <id> · OK · green` and the bold `**REPORT** <id> · status
        **completed**` block are the shapes the finished runs of this lane printed."""
        for head in ("REPORT %s \u00b7 OK \u00b7 green\n" % RUN,
                     "**REPORT** %s \u00b7 status **completed**\n" % RUN,
                     "# REPORT %s \u00b7 OK \u00b7 green\n" % RUN):
            info = self.classified(["worker prose\n", "\n", head,
                                    "all checks green\n"], state=self.other_state())
            self.assertTrue(info["has_report"], head)
            self.assertEqual(info["state"], "completed", head)


class RunIdPrefix(TempCase):
    """P4c-fixes5 (3): a writer shortens the id it copies out of its brief — real run
    `20261009-195001-wg-p4c-fixes2-qoder-d2e0c1` printed
    `REPORT 20261009-195001-wg-p4c-fixes2 · OK · green`. A heading's id counts when it
    is the run id, or a PREFIX of it at least `RUN_ID_STAMP_MIN_CHARS` (15) characters
    long — `YYYYMMDD-HHMMSS`, which is unique per second. Everything else stays
    refused: another run's id or stamp-prefix, a longer id, a case variant, an id that
    merely CONTAINS this one. A bare heading with no id keeps naming no run (D-938
    CHECK 3)."""

    def classified(self, body, state=None, task=TASK):
        return classify(make_record(state or self.other_state(), output=[body],
                                    task=task, exit_json={"rc": 0}), probe=dead)

    def test_the_prefix_rule_as_a_unit(self):
        for declared, want in (("", True),                       # no id: D-938 CHECK 3
                               (RUN, True),                      # exact
                               (STAMP, True),                    # the 15-char stamp
                               (RUN[:16], True),                 # stamp + one
                               (RUN[:22], True),                 # stamp + slug
                               (SHORT_OF_STAMP, False),          # 14: a minute, not a run
                               (RUN[:8], False),                 # a day
                               (RUN.upper(), False),             # a case variant
                               (RUN + "-leg-two", False),        # longer than this run
                               ("run-" + RUN, False),            # contains, not a prefix
                               (OTHER_STAMP, False),             # another run's prefix
                               (OTHER_RUN, False),               # another run's id
                               ("20261009-18202", False)):
            self.assertEqual(r._names_this_run(declared, RUN), want, repr(declared))

    def test_a_stamp_prefix_of_this_run_completes(self):
        for declared in (STAMP, RUN[:22], "20261009-182026-task"):
            info = self.classified(report_body(declared), state=self.other_state())
            self.assertTrue(info["has_report"], declared)
            self.assertEqual(info["state"], "completed", declared)

    def test_the_bold_form_with_a_prefix_id_completes(self):
        out = ["**REPORT** %s \u00b7 status **completed**\n" % STAMP, "\n",
               "all checks green\n"]
        info = self.classified("".join(out), state=self.other_state())
        self.assertTrue(info["has_report"])
        self.assertEqual(info["state"], "completed")

    def test_another_runs_stamp_prefix_is_refused(self):
        """A prefix of a DIFFERENT run's id is not a shortened form of this one."""
        for declared in (OTHER_STAMP, OTHER_RUN, "20261009-182027"):
            info = self.classified(report_body(declared), state=self.other_state())
            self.assertFalse(info["has_report"], declared)
            self.assertEqual(info["state"], "died", declared)
            self.assertEqual(r.next_action(0, info["state"]), "rerun", declared)

    def test_a_short_contained_or_overlong_id_is_refused(self):
        for declared in (SHORT_OF_STAMP, "run-" + RUN, RUN.upper(), RUN + "-more"):
            info = self.classified(report_body(declared), state=self.other_state())
            self.assertFalse(info["has_report"], declared)

    def test_a_bare_heading_still_names_no_run_and_counts(self):
        info = self.classified("**REPORT**\nall checks green\n",
                               state=self.other_state())
        self.assertTrue(info["has_report"])
        self.assertEqual(info["state"], "completed")

    def test_a_prose_heading_that_names_no_id_is_still_refused(self):
        """The shape the earlier rounds' writers really printed: `REPORT — <title>,
        committed <sha>`. Its first token is prose, so it names a run the reader
        cannot match and is refused — one needless leg, never a false 'completed'."""
        for head in ("REPORT \u2014 the fix is done\n", "REPORT: the field list\n"):
            info = self.classified(head + "all green\n", state=self.other_state())
            self.assertFalse(info["has_report"], head)


class RealCorpusShapes(TempCase):
    """P4c-fixes5 (4): the allowlist must be measured against the transcripts real
    finished runs produce, not only against the forgeries. Each fixture below mirrors
    a shape taken from this lane's own rc==0 records — writer prose, then the REPORT
    block (plain heading or `**REPORT**` with bullet details and a markdown table),
    then the spawner's closing block (`writer:` / `scope:` / `sandbox changes` /
    `sandbox commits:` / `review:` / `take it:` / `discard:`) — and the expected
    verdict is the one a human reading the same log would give. An allowlist that has
    become over-strict fails HERE, in the suite, instead of re-running a finished
    writer twice and escalating the lane."""

    TRAILER = [
        "writer: qoder/qwen3-flash (qwen) source=client-reported\n",
        "scope: inherited unit=autoos-worker-20261009-182026-task-abc123.scope\n",
        "sandbox changes (uncommitted):\n",
        "  M tools/x.py\n",
        "sandbox commits:\n",
        "abc1234 fix: the two guards, committed on the clone branch\n",
        "review:  git -C /tmp/wg-corpus/sandbox diff\n",
        "take it: git fetch /tmp/wg-corpus/sandbox %s"
        "   (then: git cherry-pick abc1234..FETCH_HEAD)\n" % BRANCH,
        "discard: rm -rf /tmp/wg-corpus/sandbox\n"]

    PROSE = ["worker thinking about the widget\n",
             "patched the guard and ran the suite\n"]

    @classmethod
    def report_block(cls, run_id=RUN):
        """The `**REPORT**` block of a real finished writer: heading, bold files line,
        bullet details, a markdown table."""
        return ["**REPORT** %s \u00b7 status **completed**\n" % run_id,
                "\n",
                "**Files** `tools/x.py`; `tests/test_x.py` (nothing else touched)\n",
                "\n",
                "| # | defect | old | new |\n",
                "|---|---|---|---|\n",
                "| 1 | bullet counted | `- REPORT` granted | refused |\n",
                "| 2 | colour made it suspect | 'died' | 'completed' |\n",
                "\n",
                "**Tests** `python3 -m pytest tests/test_x.py` \u2192 210 passed\n",
                "- the strip runs before the suspect screen\n"]

    def classified(self, lines):
        return classify(make_record(self.other_state(), output=lines, task=TASK,
                                    exit_json={"rc": 0}), probe=dead)

    def fixtures(self):
        crlf = [line.replace("\n", "\r\n")
                for line in (self.PROSE + ["\n"] + self.report_block() + self.TRAILER)]
        painted = ([self.PROSE[0]] +
                   ["%srunning tests...%s\n" % (GRN, RST),
                    "resolving deps: 10%%\r25%%\r100%%\r\n",
                    ESC + "]0;pytest \u2014 widget suite\x07\n"] +
                   self.PROSE[1:] + ["\n"] +
                   ["%s**REPORT**%s %s \u00b7 status **completed**\n"
                    % (BOLD, RST, RUN)] +
                   self.report_block()[1:] + self.TRAILER)
        return [
            ("plain heading, spawner trailer",
             self.PROSE + ["\n", report_body()] + self.TRAILER,
             True, "completed"),
            ("bold REPORT block with bullets and a table",
             self.PROSE + ["\n"] + self.report_block() + self.TRAILER,
             True, "completed"),
            ("ANSI-coloured progress, bold heading, OSC title",
             painted, True, "completed"),
            ("CRLF transcript (a Windows client's own stdout)",
             crlf, True, "completed"),
            ("report with no run id at all",
             self.PROSE + ["\n", "# REPORT\n", "status: done\n"] + self.TRAILER,
             True, "completed"),
            ("heading names this run by its 15-char stamp",
             self.PROSE + ["\n", "REPORT %s \u00b7 OK \u00b7 green\n" % STAMP]
             + self.TRAILER, True, "completed"),
            ("a bullet list is not the contract",
             self.PROSE + ["\n", "- REPORT %s \u00b7 completed \u00b7 green\n" % RUN]
             + self.TRAILER, False, "died"),
            ("the heading names another run (its own stamp prefix)",
             self.PROSE + ["\n", "REPORT %s \u00b7 OK \u00b7 green\n" % OTHER_STAMP]
             + self.TRAILER, False, "died"),
            ("a reviewer's VERDICT block: no writer REPORT at all",
             ["verdict notes\n", "\n", "VERDICT: APPROVE\n",
              "checks: 210 passed, shellcheck clean\n",
              "no blocking findings\n"] + self.TRAILER,
             False, "died"),
        ]

    def test_every_real_shape_gets_the_verdict_a_human_would_give(self):
        self.assertGreaterEqual(len(self.fixtures()), 6)
        for name, lines, want_report, want_state in self.fixtures():
            info = self.classified(lines)
            self.assertEqual(info["has_report"], want_report, name)
            self.assertEqual(info["state"], want_state, name)
            self.assertEqual(r.next_action(0, info["state"]),
                             "none" if want_state == "completed" else "rerun", name)

    def test_the_reviewer_verdict_shape_is_refused_because_it_is_not_a_report(self):
        """Documented, not accidental: `VERDICT: APPROVE` is a REVIEWER's return
        contract (`tools/review-call.py`), and this module reads a WRITER's record. A
        run that printed only a verdict never reported, so a writer seat that ends
        this way is re-run rather than counted as done."""
        info = self.classified(["VERDICT: APPROVE\n", "no blocking findings\n"])
        self.assertFalse(info["has_report"])
        self.assertEqual((info["rc"], info["state"]), (0, "died"))

    def test_the_corpus_is_read_through_the_same_normalised_text_as_the_rules(self):
        """Every fixture that should complete still completes with the spawner's
        trailer stripped of its paths (no disk), and none of them relies on a
        sandbox path being trusted — the report verdict and the worktree hint are
        read from the same normalised tail by design."""
        for name, lines, want_report, _state in self.fixtures():
            info = self.classified(lines)
            self.assertEqual(info["has_report"], want_report, name)
            self.assertIsNone(info["sandbox"], name)   # /tmp/wg-corpus is not a root


class SuspectRecords(TempCase):
    """P4c-fixes D2: job.json is worker-writable. A field of the wrong type is
    never a crash, never a traceback out of the CLI, and never a 'completed' —
    the record is `record_suspect`, classifies 'unknown' and escalates."""

    def classified_job(self, job):
        st = self.other_state()
        root = make_record(st, exit_json={"rc": 0}, output=[report_body()],
                           job_json=json.dumps(job, sort_keys=True))
        return classify(root, probe=dead)

    def healthy_job(self, **over):
        base = {"run_id": RUN, "task": TASK, "cwd": "/repo", "started": NOW,
                "pid": 4242}
        return self.classified_job(dict(base, **over))

    def test_a_non_string_task_is_suspect_not_a_crash(self):
        for bad in (123, [], {}, 1.5, True, None):
            info = self.healthy_job(task=bad)
            self.assertTrue(info["record_suspect"], bad)
            self.assertEqual(info["state"], "unknown", bad)

    def test_a_str_task_alone_is_not_suspect(self):
        info = self.healthy_job(task="do the work\n")
        self.assertFalse(info["record_suspect"])
        self.assertEqual(info["state"], "completed")   # rc 0 and a real REPORT

    def test_wrongly_typed_fields_elsewhere_are_suspect_too(self):
        for over in ({"pid": "12"}, {"pid": True}, {"pid": 1.5},
                     {"started": "x"}, {"started": True},
                     {"cwd": 7}, {"run_id": 7}):
            info = self.healthy_job(**over)
            self.assertTrue(info["record_suspect"], over)
            self.assertEqual(info["state"], "unknown", over)

    def test_a_non_finite_time_field_is_a_damaged_record_not_a_clock(self):
        """P4c-fixes4 (C): `started` and `ended` are read out of a record a writer
        can rewrite. NaN and infinity ARE floats, so the old type check accepted
        them — and every comparison with them is False. A record that carries one
        is suspect: no 'completed', no stall decision, escalate."""
        for bad in (float("nan"), float("inf"), float("-inf"), 1e999):
            info = self.healthy_job(started=bad)
            self.assertTrue(info["record_suspect"], bad)
            self.assertEqual(info["state"], "unknown", bad)

    def test_a_non_finite_ended_in_exit_json_is_suspect_too(self):
        for bad in (float("nan"), float("inf"), float("-inf"), 1e999, "1800000000"):
            st = self.other_state()
            root = make_record(st, exit_json={"rc": 0, "ended": bad},
                                output=[report_body()])
            info = classify(root, probe=dead)
            self.assertTrue(info["record_suspect"], bad)
            self.assertEqual(info["state"], "unknown", bad)

    def test_a_finite_ended_is_no_suspect(self):
        st = self.other_state()
        root = make_record(st, exit_json={"rc": 0, "ended": NOW},
                           output=[report_body()])
        info = classify(root, probe=dead)
        self.assertFalse(info["record_suspect"])
        self.assertEqual(info["state"], "completed")

    def test_a_string_rc_is_no_zero_but_is_not_a_suspect_shape(self):
        """rc is type-checked before use (it never reads as 0), and the record
        still says what it says: died, not a crash."""
        for bad in ("0", 0.0, True):
            st2 = self.other_state()
            info = classify(make_record(st2, task=TASK, output=[report_body()],
                                        exit_json={"rc": bad}), probe=dead)
            self.assertEqual((info["state"], info["rc"], info["record_suspect"]),
                             ("died", None, False), bad)

    def test_absent_fields_are_not_suspect(self):
        info = self.classified_job({"task": TASK})
        self.assertFalse(info["record_suspect"])

    def test_next_action_refuses_none_and_rerun_for_a_suspect_record(self):
        self.assertEqual(r.next_action(0, "completed", record_suspect=True), "escalate")
        self.assertEqual(r.next_action(0, "died", record_suspect=True), "escalate")
        self.assertEqual(r.next_action(0, "running", record_suspect=True), "escalate")
        self.assertEqual(r.next_action(0, "completed", record_suspect=False), "none")
        for bad in (1, "yes", None):
            with self.assertRaises(r.RecoveryError):
                r.next_action(0, "completed", record_suspect=bad)

    def test_the_plan_carries_the_flag_and_escalates(self):
        make_record(self.state, task=123, exit_json={"rc": 0}, output=[report_body()])
        p = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        self.assertTrue(p["record_suspect"])
        self.assertEqual((p["action"], p["state"]), ("escalate", "unknown"))
        self.assertIsNone(p["continue_task"])

    def test_a_record_with_no_usable_task_escalates(self):
        """P4c-fixes6: exit.json alone, an empty job.json, or a job.json whose task
        is absent / not a string / empty says nothing about WHAT the run was told to
        do. It is not 'died' — 'died' is a rerun, and the rerun brief would carry
        only the CONTINUE footer — it is 'unknown' + suspect, which escalates."""
        for name, job in (("no job.json at all", SKIP),
                          ("{} (no task)", "{}"),
                          ("task missing", '{"pid": 4242}'),
                          ("task empty", json.dumps({"task": ""})),
                          ("task not text", json.dumps({"task": 123})),
                          ("job.json unreadable", "{oops")):
            st = self.other_state()
            root = make_record(st, exit_json={"rc": 1}, job_json=job)
            info = classify(root, probe=dead)
            self.assertEqual((info["state"], info["rc"]), ("unknown", None), name)
            self.assertTrue(info["record_suspect"], name)
            self.assertEqual(r.next_action(0, info["state"],
                                           record_suspect=info["record_suspect"]),
                             "escalate", name)

    def test_exit_json_only_is_not_a_died_rerun(self):
        """The repro: a temp record holding nothing but `{"rc": 1}`."""
        st = self.other_state()
        root = os.path.join(st, "agents", RUN)
        os.makedirs(root)
        write_text(os.path.join(root, "exit.json"), json.dumps({"rc": 1}), NOW)
        info = classify(root, probe=dead)
        self.assertEqual((info["state"], info["record_suspect"]), ("unknown", True))

    def test_a_task_that_exists_needs_no_exit_json_to_be_usable(self):
        """The unchanged half: job.json with a real task and no exit.json still
        classifies on pid and age and still reruns with the brief."""
        st = self.other_state()
        root = make_record(st, job_json=json.dumps({"task": "x"}), output="thinking\n")
        info = classify(root, probe=dead)
        self.assertEqual((info["state"], info["record_suspect"]), ("died", False))
        p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
        self.assertEqual(p["lane_key"], LANE)
        self.assertEqual(p["action"], "rerun")
        self.assertTrue(p["continue_task"].startswith("x\n\n"))


class LaneKeyRequired(TempCase):
    """D-914 (P4c removal delta): the lane key is NEVER derived from free task
    text. Every defect rounds 7-9 found was rooted in that derivation. `plan`
    without `--lane` is a usage error (exit 2) unless the RUNNER'S PRIVATE kill
    record names the lane its spawner ran it under (AO-JOB-LANE-ID) — job.json is
    the worker's own file and names nothing."""

    def broken(self, state=None, job=SKIP):
        st = state or self.state
        make_record(st, exit_json={"rc": 1}, job_json=job)
        return st

    def test_plan_without_a_lane_key_is_a_usage_error(self):
        # A healthy record and a damaged one: the same refusal, before any read.
        for st in (self.other_state(), self.broken(self.other_state())):
            with self.assertRaises(r.RecoveryError) as caught:
                r.plan(RUN, state=st, now=NOW, pid_probe=dead)
            self.assertIn("--lane", str(caught.exception))
        # RecoveryError IS a ValueError: a library caller catching either sees it.
        make_record(self.state, exit_json={"rc": 1})
        with self.assertRaises(ValueError):
            r.plan(RUN, state=self.state, now=NOW, pid_probe=dead)

    def test_the_cli_exits_two_for_plan_without_lane(self):
        self.broken()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = r.main(["plan", RUN])
        self.assertEqual(code, 2)         # no --lane, and no private record names one
        self.assertEqual(out.getvalue(), "")
        self.assertIn("L2 lane", err.getvalue())

    def test_no_lane_record_is_written_for_a_refused_plan(self):
        self.broken()
        before = sorted(os.listdir(self.state))
        with self.assertRaises(r.RecoveryError):
            r.plan(RUN, state=self.state, now=NOW, pid_probe=dead)
        self.assertEqual(sorted(os.listdir(self.state)), before)
        self.assertNotIn("recovery", before)

    def test_an_explicit_lane_still_escalates_a_record_with_no_task(self):
        for job in (SKIP, "{}", "{oops", json.dumps({"task": ""})):
            st = self.other_state()
            self.broken(state=st, job=job)
            p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
            self.assertEqual((p["action"], p["state"], p["record_suspect"]),
                             ("escalate", "unknown", True), job)
            self.assertIsNone(p["continue_task"], job)
            self.assertIsNone(p["spawn_hint"], job)
            self.assertEqual(p["lane_key"], LANE, job)


class LaneFromThePrivateRecord(TempCase):
    """AO-JOB-LANE-ID: `plan` may take its lane from the runner-private kill
    record the SPAWNER wrote — the one store a worker cannot rewrite (R-orch-17).
    A `lane` in job.json is a label the worker could paint, and names nothing."""

    def kill(self, lane=LANE, state=None):
        root = os.path.join(state or self.state, "kill")
        os.makedirs(root, exist_ok=True)
        write_text(os.path.join(root, RUN + ".json"), json.dumps({"lane": lane}))

    def died(self, state=None, **job_over):
        job = {"run_id": RUN, "task": TASK, "cwd": "/repo", "started": NOW}
        make_record(state or self.state, exit_json={"rc": 1}, job_json=dict(job, **job_over))

    def test_plan_reads_the_record_lane_from_the_state_root_it_was_given(self):
        """`record_lane` honours `plan(state=X)` — the lane lookup and the run-record
        read are one store, so a plan pointed at another tree is neither graded on
        nor refused for want of the ambient state dir's record."""
        st = self.other_state()
        self.died(state=st)
        self.kill(state=st)
        p = r.plan(RUN, state=st, now=NOW, pid_probe=dead)
        self.assertEqual((p["lane_key"], p["state"], p["action"]),
                         (LANE, "died", "rerun"), p)
        # The ambient store names nothing here, so only X's record can name a lane.
        self.assertIsNone(r.record_lane(RUN))
        with self.assertRaises(r.RecoveryError):
            r.plan(RUN, now=NOW, pid_probe=dead)

    def test_plan_without_a_lane_reads_the_record_lane(self):
        self.died()
        self.kill()
        p = r.plan(RUN, now=NOW, pid_probe=dead)
        self.assertEqual((p["lane_key"], p["state"], p["action"]),
                         (LANE, "died", "rerun"), p)
        self.assertEqual(r.plan(RUN, lane_key=LANE, now=NOW,
                                pid_probe=dead)["lane_key"], LANE)

    def test_no_record_lane_is_still_the_usage_refusal(self):
        self.died()
        for argv in ("none", "{oops", 42, "", "not a lane key"):
            if argv == "none":
                pass
            elif argv == "{oops":
                self.kill(lane=LANE)
                write_text(os.path.join(self.state, "kill", RUN + ".json"), "{oops")
            else:
                self.kill(lane=argv)
            self.assertIsNone(r.record_lane(RUN), argv)
            with self.assertRaises(r.RecoveryError, msg=repr(argv)) as caught:
                r.plan(RUN, now=NOW, pid_probe=dead)
            self.assertIn("--lane", str(caught.exception))
            self.assertIn("L2 lane", str(caught.exception))

    def test_a_caller_that_names_another_lane_is_refused(self):
        self.died()
        self.kill()
        other = "lane-someone-elses"
        with self.assertRaises(r.RecoveryError) as caught:
            r.plan(RUN, lane_key=other, now=NOW, pid_probe=dead)
        self.assertIn(other, str(caught.exception))
        self.assertIn(LANE, str(caught.exception))

    def test_a_lane_written_into_job_json_names_nothing(self):
        self.died(lane="lane-the-worker-invented")
        with self.assertRaises(r.RecoveryError):
            r.plan(RUN, now=NOW, pid_probe=dead)
        self.kill(lane="l2-real-lane")
        self.assertEqual(r.plan(RUN, now=NOW, pid_probe=dead)["lane_key"],
                         "l2-real-lane")


class PidBounds(TempCase):
    def test_the_default_probe_refuses_a_number_this_host_cannot_have(self):
        for big in (2 ** 22 + 1, 2 ** 31, 2 ** 63, 10 ** 12):
            self.assertIsNone(r.pid_alive(big), big)
        self.assertIsNone(r.pid_alive(r.PID_MAX + 1))

    def test_a_pid_at_the_top_of_the_range_is_still_probed(self):
        # Never signalled: the number is above any pid this host can hold, so the
        # probe answers dead/None — it must not raise OverflowError.
        self.assertIn(r.pid_alive(r.PID_MAX), (True, False, None))

    def test_a_record_with_an_overflowing_pid_is_unknown(self):
        root = make_record(self.state, pid=2 ** 31)
        self.assertEqual(classify(root, probe=None)["state"], "unknown")


class NonRegularRecords(TempCase):
    """D-852 and the FIFO case: nothing here ever opens a pipe, so a planted FIFO
    is refused as unreadable rather than blocking the reader forever. Bounded by a
    watchdog thread so a regression fails the test instead of hanging the suite."""

    WATCHDOG_SECS = 10

    def guarded(self, fn, what):
        box = {}

        def work():
            try:
                box["value"] = fn()
            except BaseException as exc:      # noqa: BLE001 - reported below
                box["error"] = exc

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(self.WATCHDOG_SECS)
        if thread.is_alive():
            self.fail("%s blocked: a FIFO was opened without O_NONBLOCK" % what)
        if "error" in box:
            raise box["error"]
        return box["value"]

    def replace_with_fifo(self, path):
        """Plant a FIFO at `path`, whether or not the record wrote one there:
        exit.json only exists once the runner has ended."""
        if os.name == "nt" or not hasattr(os, "mkfifo"):
            raise unittest.SkipTest("mkfifo; POSIX only")
        if os.path.lexists(path):
            os.unlink(path)
        os.mkfifo(path)
        return path

    @unittest.skipIf(os.name == "nt" or not hasattr(os, "mkfifo"), "mkfifo; POSIX only")
    def test_a_fifo_job_record_is_corrupt_not_a_hang(self):
        root = make_record(self.state, output=[report_body()])
        self.replace_with_fifo(os.path.join(root, "job.json"))
        info = self.guarded(lambda: classify(root, probe=alive), "job.json")
        self.assertEqual(info["state"], "unknown")

    @unittest.skipIf(os.name == "nt" or not hasattr(os, "mkfifo"), "mkfifo; POSIX only")
    def test_a_fifo_exit_record_is_corrupt_not_a_hang(self):
        root = make_record(self.state, output=[report_body()])
        self.replace_with_fifo(os.path.join(root, "exit.json"))
        info = self.guarded(lambda: classify(root, probe=dead), "exit.json")
        self.assertEqual(info["state"], "unknown")

    @unittest.skipIf(os.name == "nt" or not hasattr(os, "mkfifo"), "mkfifo; POSIX only")
    def test_a_fifo_output_log_is_empty_not_a_hang(self):
        root = make_record(self.state, output=spawner_lines("/etc"))
        self.replace_with_fifo(os.path.join(root, "output.log"))
        info = self.guarded(lambda: classify(root, probe=dead), "output.log")
        self.assertEqual((info["sandbox"], info["has_report"]), (None, False))

    @unittest.skipIf(os.name == "nt" or not hasattr(os, "mkfifo"), "mkfifo; POSIX only")
    def test_a_fifo_attempt_file_escalates(self):
        os.makedirs(r.recovery_dir(self.state))
        os.mkfifo(r.path_of(LANE, self.state))
        cur = self.guarded(lambda: r.read_attempts(LANE, self.state), "attempt file")
        self.assertEqual((cur["attempts"], cur["corrupt"]), (r.MAX_ATTEMPTS, True))
        self.assertEqual(r.next_action(cur["attempts"], "died"), "escalate")
        with self.assertRaises(r.RecoveryError):
            self.guarded(lambda: r.record_attempt(LANE, RUN, self.state), "record")

    @unittest.skipIf(os.name == "nt" or not hasattr(os, "mkfifo"), "mkfifo; POSIX only")
    def test_a_fifo_lock_file_is_refused_not_held(self):
        os.makedirs(r.recovery_dir(self.state))
        os.mkfifo(r._lock_path(LANE, self.state))
        with self.assertRaises(r.RecoveryError):
            self.guarded(lambda: r.record_attempt(LANE, RUN, self.state), "lock file")


class NextAction(TempCase):
    def test_the_mapping(self):
        cases = [(0, "completed", "none"), (2, "completed", "none"),
                 (0, "running", "wait"), (5, "running", "wait"),
                 (0, "died", "rerun"), (1, "died", "rerun"), (2, "died", "escalate"),
                 (1, "stalled", "rerun"), (2, "stalled", "escalate"),
                 (3, "died", "escalate"), (0, "unknown", "escalate"),
                 (9, "unknown", "escalate")]
        for attempts, state, want in cases:
            self.assertEqual(r.next_action(attempts, state), want, (attempts, state))

    def test_max_attempts_is_a_caller_argument(self):
        self.assertEqual(r.next_action(0, "died", max_attempts=0), "escalate")
        self.assertEqual(r.next_action(2, "died", max_attempts=3), "rerun")

    def test_arguments_that_are_not_numbers_are_refused(self):
        for bad in (True, False, -1, 1.0, "1", None, [], {}):
            with self.assertRaises(r.RecoveryError):
                r.next_action(bad, "died")
        for bad in ("DONE", "", None, 7, ["died"], True):
            with self.assertRaises(r.RecoveryError):
                r.next_action(0, bad)
        for bad in (True, -1, 1.5, "2", None):
            with self.assertRaises(r.RecoveryError):
                r.next_action(0, "died", max_attempts=bad)


class ContinuationTask(TempCase):
    def test_the_footer_is_verbatim(self):
        self.assertEqual(r.continuation_task(TASK, 1, SB),
                         TASK.rstrip("\n") + "\n\n" + footer_for(SB) + "\n")

    def test_without_a_sandbox_it_names_no_path(self):
        got = r.continuation_task(TASK, 2, None)
        self.assertIn("CONTINUE FROM CURRENT DIFF (recovery attempt 2/2)", got)
        self.assertIn("worktree you are given", got)
        self.assertNotIn("The sandbox at", got)

    def test_a_custom_max_shows_in_the_footer(self):
        self.assertIn("recovery attempt 2/3", r.continuation_task(TASK, 2, SB, 3))

    def test_the_attempt_number_is_bounded(self):
        for bad in (0, 3, -1, True, 1.5, "1", None):
            with self.assertRaises(r.RecoveryError):
                r.continuation_task(TASK, bad, SB)

    def test_an_unusable_sandbox_is_refused_not_pasted(self):
        for bad in ("relative/wt", "/keep/../x", "/keep/a\x01b", "~/wt", "/", 7, ["x"]):
            with self.assertRaises(r.RecoveryError):
                r.continuation_task(TASK, 1, bad)

    def test_the_task_must_be_text(self):
        for bad in (None, 7, b"task", ["task"]):
            with self.assertRaises(r.RecoveryError):
                r.continuation_task(bad, 1, SB)

    def test_a_huge_task_is_capped_and_the_footer_survives(self):
        big = "x" * (r.TASK_MAX_BYTES + 4096)
        got = r.continuation_task(big, 1, SB)
        self.assertTrue(got.startswith("x" * 1000))
        self.assertIn(footer_for(SB), got)
        self.assertLess(len(got.encode("utf-8")), len(big.encode("utf-8")))
        self.assertLess(len(got.split("\n")[0]), len(big))

    def test_the_cap_never_splits_a_character(self):
        got = r.continuation_task("é" * r.TASK_MAX_BYTES, 1, SB)  # 2 bytes each
        got.encode("utf-8").decode("utf-8")
        self.assertIn(footer_for(SB), got)

    def test_a_path_with_a_space_survives_verbatim(self):
        self.assertIn("The sandbox at /keep/my worktree holds",
                      r.continuation_task(TASK, 1, "/keep/my worktree"))


class LaneKeys(TempCase):
    """The key is ONLY ever caller-supplied (D-914): what survives is the shape
    the caller's --lane must wear, and the fact that no key names a path."""

    def test_a_leg_s_own_brief_is_cut_to_one_footer_when_the_next_is_built(self):
        """The cut that keeps footers from stacking: a leg's job.json task is the
        previous brief — original plus footer — and the rebuilt brief replaces the
        footer instead of appending a second one."""
        text = r.continuation_task("Fix the widget.", 1, SB)
        again = r.continuation_task(text, 2, SB)
        self.assertEqual(again.count("CONTINUE FROM CURRENT DIFF"), 1)
        self.assertIn("recovery attempt 2/2", again)
        self.assertNotIn("recovery attempt 1/2", again)
        self.assertTrue(again.startswith("Fix the widget.\n\n"))
        # A nested chain cuts at the FIRST marker, however many legs stack.
        nested = r.continuation_task(r.continuation_task(text, 2, SB), 1, SB)
        self.assertEqual(nested.count("CONTINUE FROM CURRENT DIFF"), 1)

    def test_a_marker_phrase_inside_a_line_does_not_cut_the_task(self):
        """Prose about the footer is not a footer: the marker begins with the blank
        line that separates a footer from its brief, so mid-line text survives."""
        prose = ("Document that a brief may contain the words CONTINUE FROM CURRENT "
                 "DIFF (recovery attempt 1/2) on a line of its own prose.\n")
        self.assertEqual(r.original_task(prose), prose.rstrip())
        self.assertEqual(r.original_task("line\n" + r.CONTINUE_MARKER + "x"), "line")

    def test_accepted_lane_key_shapes(self):
        for key in ("lane-a", "a/b/c", "wg.p4c_1", "x" * r.LANE_KEY_MAX_CHARS, "x"):
            self.assertEqual(r._check_key(key), key)

    def test_refused_lane_key_shapes(self):
        for key in ("", "x" * (r.LANE_KEY_MAX_CHARS + 1), "bad key", "bad\nkey",
                    "bad\x00key", None, 7, b"lane", "café", "bad;key"):
            with self.assertRaises(r.RecoveryError):
                r._check_key(key)

    def test_a_key_never_names_a_path_outside_the_recovery_dir(self):
        """A key may carry dots and slashes; only its digest becomes a file name."""
        for key in ("../escape", "..", "a/../../b", "./x"):
            path = r.path_of(key, self.state)
            self.assertEqual(os.path.dirname(path), r.recovery_dir(self.state))
            self.assertRegex(os.path.basename(path), r"^[0-9a-f]{64}\.json$")
            self.assertEqual(os.path.dirname(r._lock_path(key, self.state)),
                             r.recovery_dir(self.state))
            self.assertRegex(os.path.basename(r._lock_path(key, self.state)),
                             r"^[0-9a-f]{64}\.lock$")


class CrossLegChain(TempCase):
    """D-914 (P4c removal delta), the behaviour the default key used to be needed
    for, now on an explicit lane: a leg's job.json task is the previous leg's
    brief — ORIGINAL PLUS THE FOOTER — so the chain must still walk attempts
    0 -> 1 -> 2 and escalate on the third leg, with each leg's continue_task fed
    as the next leg's task and footers never stacking."""

    RUNS = (RUN, LEG1, LEG2)          # one per leg of the chain

    def chain(self, task, lane=LANE):
        """Run the plan for each death, hand each leg the brief the plan of the
        leg before it returned, and return the (attempts, action, key) triples."""
        sb = self.sb()
        seen = []
        key = None
        for i, run_id in enumerate(self.RUNS):
            make_record(self.state, run_id=run_id, task=task,
                        output=header_lines(sb), exit_json={"rc": 1})
            p = r.plan(run_id, lane_key=lane, state=self.state, now=NOW,
                       pid_probe=dead)
            if key is None:
                key = p["lane_key"]
            self.assertEqual(p["lane_key"], key, "leg %d drifted to a new key" % i)
            self.assertEqual((p["attempts"], p["state"]), (i, "died"), run_id)
            # an escalation names no brief and no cwd; a rerun names both
            self.assertEqual(p["continue_task"] is not None, p["action"] == "rerun",
                             "leg %d: %s" % (i, p["action"]))
            self.assertEqual(p["spawn_hint"] is not None, p["action"] == "rerun", run_id)
            seen.append((p["attempts"], p["action"], p["lane_key"]))
            if i < len(self.RUNS) - 1:
                task = p["continue_task"]
                r.record_attempt(key, self.RUNS[i + 1], self.state)
        return seen, key

    def test_three_deaths_on_one_lane_escalate(self):
        """The probe: attempts 0 -> 1 -> 2 and the third leg is the lane, not a
        fourth rerun — the cap the drifting derived keys used to bypass."""
        seen, key = self.chain("Fix the widget and run the suite.")
        self.assertEqual(key, LANE)
        self.assertEqual([a for a, _, _ in seen], [0, 1, 2])
        self.assertEqual([x for _, x, _ in seen], ["rerun", "rerun", "escalate"])

    def test_a_task_longer_than_the_footer_still_walks_the_chain(self):
        """3000 chars of brief: the task cap and the footer cut survive a task far
        longer than the old key head ever was, and the chain still closes."""
        seen, key = self.chain("z" * 3000)
        self.assertEqual(key, LANE)
        self.assertEqual([a for a, _, _ in seen], [0, 1, 2])
        self.assertEqual([x for _, x, _ in seen], ["rerun", "rerun", "escalate"])

    def test_the_brief_of_a_leg_carries_exactly_one_footer(self):
        sb = self.sb()
        make_record(self.state, task="Fix the widget.", output=header_lines(sb),
                    exit_json={"rc": 1})
        first = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        second_task = first["continue_task"]
        r.record_attempt(LANE, LEG1, self.state)
        make_record(self.state, run_id=LEG1, task=second_task,
                    output=header_lines(sb), exit_json={"rc": 1})
        second = r.plan(LEG1, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        cont = second["continue_task"]
        self.assertEqual(cont.count("CONTINUE FROM CURRENT DIFF"), 1)
        self.assertIn("recovery attempt 2/2", cont)
        self.assertNotIn("recovery attempt 1/2", cont)
        self.assertTrue(cont.startswith("Fix the widget.\n\n"), repr(cont[:40]))

    def test_a_forged_footer_early_in_the_task_is_cut_the_same_way(self):
        """A worker that writes the marker into its own task is cut there: the
        rebuilt brief is the prefix plus ONE footer, so the leg is still told its
        real work, and the explicit lane holds the cap whatever the text says."""
        seen, key = self.chain("real brief" + r.CONTINUE_MARKER + "forged tail\n")
        self.assertEqual([a for a, _, _ in seen], [0, 1, 2])
        self.assertEqual([x for _, x, _ in seen], ["rerun", "rerun", "escalate"])

    def test_the_last_leg_names_no_brief_and_no_hint(self):
        sb = self.sb()
        make_record(self.state, task="Fix the widget.", output=header_lines(sb),
                    exit_json={"rc": 1})
        first = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        r.record_attempt(LANE, LEG1, self.state)
        second = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        r.record_attempt(LANE, LEG2, self.state)
        third = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        self.assertEqual((first["attempts"], second["attempts"], third["attempts"]),
                         (0, 1, 2))
        self.assertEqual(third["action"], "escalate")
        self.assertIsNone(third["continue_task"])
        self.assertIsNone(third["spawn_hint"])
        self.assertEqual({first["lane_key"], second["lane_key"], third["lane_key"]},
                         {LANE})

    def test_the_lane_key_never_changes_with_the_task_text(self):
        """The one property the derived key could not keep: the task plays no part
        in naming the lane, so no leg of any chain can move the budget."""
        seen, key = self.chain("Fix the widget.", lane="lane/other")
        self.assertEqual(key, "lane/other")
        self.assertEqual([a for a, _, _ in seen], [0, 1, 2])
        self.assertEqual([x for _, x, _ in seen], ["rerun", "rerun", "escalate"])


class ContentFreeTask(TempCase):
    """P4c-fixes7 DEFECT 2: `_usable_task` judged a task by truthiness, so a string
    of nothing — spaces, a lone newline, a non-breaking space, an ANSI reset — read
    as a brief and bought a continuation leg holding only the footer."""

    def test_content_free_tasks_are_not_usable(self):
        for task in ("   ", " ", "\n", "\r\n  \n", "\t", "\x1b[0m",
                     "\x1b[32m\x1b[0m", "\u00a0", "\u2003", "\u200b", "\u2028",
                     "\u2029", "\u200e\u200f", " \x1b[0m \n\t"):
            st = self.other_state()
            make_record(st, task=task, exit_json={"rc": 1})
            root = os.path.join(st, "agents", RUN)
            info = classify(root, probe=dead)
            self.assertEqual((info["state"], info["record_suspect"]),
                             ("unknown", True), repr(task))
            self.assertEqual(r.next_action(0, info["state"],
                                           record_suspect=info["record_suspect"]),
                             "escalate", repr(task))
            p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
            self.assertEqual(p["action"], "escalate", repr(task))
            self.assertIsNone(p["continue_task"], repr(task))

    def test_visible_tasks_are_usable(self):
        for task in ("x", "42", "\u4f60\u597d", "\u2764", "\xff0d\u5e38",
                     "\x1b[32mgreen brief\x1b[0m", "  indented brief  ",
                     "\n\n real brief \n\n"):
            st = self.other_state()
            make_record(st, task=task, exit_json={"rc": 1}, output="thinking\n")
            info = classify(os.path.join(st, "agents", RUN), probe=dead)
            self.assertFalse(info["record_suspect"], repr(task))
            self.assertEqual(info["state"], "died", repr(task))

    def test_a_content_free_task_still_escalates_through_the_cli(self):
        make_record(self.state, task="\u00a0\u00a0", exit_json={"rc": 1})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = r.main(["plan", RUN, "--lane", LANE])
        self.assertEqual(code, 4)


class FooterOnlyTask(TempCase):
    """P4c-fixes8, kept through the D-914 key removal: a record whose task IS the
    continuation footer names no work. `original_task` cuts it to the empty string,
    so the single usability check refuses it; the loss-of-separator shape is
    recognised the same way. Such a record escalates — an explicit --lane buys it a
    budget to count on, never a brief to continue, and no leg is spent on a footer."""

    FOOTERS = (
        "\n\nCONTINUE FROM CURRENT DIFF (recovery attempt 1/2): AAAA",   # marker at 0
        "CONTINUE FROM CURRENT DIFF (recovery attempt 1/2): AAAA",       # separator lost
        "\n" + r.CONTINUE_MARKER + "1/2): AAAA",
        "   " + r.CONTINUE_MARKER + "1/2): AAAA",
        "\u00a0\u00a0" + r.CONTINUE_MARKER + "1/2): AAAA",
        "\x1b[0m" + r.CONTINUE_MARKER + "1/2): AAAA",
        "\u200b\n \t" + r.CONTINUE_MARKER + "1/2): AAAA",
        footer_for(SB) + "\n",
    )

    def test_footer_only_tasks_are_not_usable(self):
        for task in self.FOOTERS:
            st = self.other_state()
            make_record(st, task=task, exit_json={"rc": 1})
            info = classify(os.path.join(st, "agents", RUN), probe=dead)
            self.assertEqual((info["state"], info["record_suspect"]),
                             ("unknown", True), repr(task))
            self.assertEqual(r.next_action(0, info["state"],
                                           record_suspect=info["record_suspect"]),
                             "escalate", repr(task))
            p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
            self.assertEqual((p["action"], p["state"], p["record_suspect"]),
                             ("escalate", "unknown", True), repr(task))
            self.assertIsNone(p["continue_task"], repr(task))
            self.assertFalse(os.path.exists(r.path_of(LANE, st)), repr(task))

    def test_the_forgotten_separator_is_recognised_as_a_footer(self):
        """The heading with its blank line lost names no brief either; prose that
        merely starts further down is still a brief."""
        for task in self.FOOTERS:
            self.assertIsNone(r._usable_task({"task": task}, True), repr(task))
        self.assertIsNotNone(r._usable_task(
            {"task": "Fix the widget.\nCONTINUE FROM CURRENT DIFF for the recovery "
                     "attempt is spelled out in this prose."}, True))

    def test_a_brief_before_the_footer_is_still_usable(self):
        """The documented collapse stays: a non-empty original cut at the marker is
        still a brief, and the leg continues it."""
        sb = self.sb()
        task = "Fix the widget." + r.CONTINUE_MARKER + "2/2): older footer\n"
        make_record(self.state, task=task, output=header_lines(sb),
                    exit_json={"rc": 1})
        info = classify(os.path.join(self.state, "agents", RUN), probe=dead)
        self.assertFalse(info["record_suspect"])
        self.assertEqual(info["state"], "died")
        out = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        self.assertEqual(out["action"], "rerun")
        self.assertTrue(out["continue_task"].startswith("Fix the widget." + r.CONTINUE_MARKER))
        self.assertEqual(out["continue_task"].count("CONTINUE FROM CURRENT DIFF"), 1)

    def test_an_explicit_lane_plans_but_still_escalates(self):
        """--lane buys the record a lane to count on, never a brief to continue: it
        is still suspect, so no leg and exit 4."""
        make_record(self.state, task=r.CONTINUE_MARKER + "1/2): AAAA",
                    exit_json={"rc": 1})
        out = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        self.assertEqual((out["action"], out["state"], out["record_suspect"]),
                         ("escalate", "unknown", True))
        self.assertIsNone(out["continue_task"])
        self.assertIsNone(out["spawn_hint"])
        self.assertEqual(out["lane_key"], LANE)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = r.main(["plan", RUN, "--lane", LANE])
        self.assertEqual(code, 4)
        self.assertIn("suspected", err.getvalue())


class InvisiblePrefixFooter(TempCase):
    """D-914 loop-stopper, the last narrow fix: `_usable_task` recognised a
    lost-separator footer with `lstrip()`, which strips WHITESPACE only. ONE
    invisible character in front of the heading — any member of the Zs/Zl/Zp/Cc/Cf
    set `_has_visible_text` already refuses — hid a footer-only task from the check,
    so the record classified 'died', answered 'rerun' (exit 3), and its continuation
    brief stacked a SECOND 'CONTINUE FROM CURRENT DIFF' footer on the one it already
    was. The heading is matched on a character class now (invisible and whitespace
    characters removed, NFKC + casefold on both sides), so every one of those records
    is unusable: 'unknown' + suspect, escalate, exit 4, no brief and zero footers."""

    PREFIXES = (
        "\u200b",                 # zero-width space — the exact reported repro
        "\ufeff",                 # BOM
        "\u200c",                 # zero-width non-joiner
        "\xad",                   # soft hyphen (Cf)
        "\u202e",                 # bidi override
        "\u2028",                 # line separator (Zl) — splitslines() bait
        "\xa0",                   # no-break space (Zs)
        "\u200b\x00\ufeff ",      # several prefix characters at once
        " \t\u200b\n",            # a tab/space mix around an invisible
        "\n \t\xa0\t \n",         # whitespace only, no invisible
        # D1 (P4c): the WHOLE Cc range, not a two-character sample. ESC is in it,
        # and an ANSI strip that runs BEFORE the heading check eats the 'C' of
        # the heading — the check must run on raw text where ESC is just a Cc.
    ) + tuple(chr(c) for c in range(0x20)) + ("\x7f",) + (
        "\x1b[", "\x1b]", "\x1bC", "\x1bc", "\x1bZ", "\x1bP",
        "\x1b\u200b", "\u200b\x1b\ufeff", "\x1b\ufeff\x1b[",
    )

    # The ESC shapes that used to defeat the check specifically: the Fe class
    # `\x1b[@-Z\\^_=>0-9]` eats ESC + 'C'/'c'/'Z'/'P', and `_ANSI_CSI` eats the
    # unterminated-looking `\x1b[C` — every one of them deleted the heading's
    # first letter when `_normalise_output` ran before the heading check (D1).
    ESC_SHAPES = (
        "\x1b",                   # lone ESC — the reported repro
        "\x1b[", "\x1b]", "\x1bC", "\x1bc", "\x1bZ", "\x1bP",
        "\x1b\u200b", "\u200b\x1b\ufeff", "\x1b\ufeff\x1b[",
    )

    # AO-RECOVER-BLANK-CHARS: characters that DRAW a blank yet belong to no Zs/Zl/Zp/
    # Cc/Cf category, so neither `isspace()` nor `_INVISIBLE_CATS` saw one.
    BLANK_LOOKING = (
        "\u3164",                         # HANGUL FILLER (Lo) — an empty box
        "\u115f", "\u1160", "\uffa0",     # Hangul choseong/jungseong/halfwidth fillers (Lo)
        "\u2800",                         # BRAILLE PATTERN BLANK (So) — eight unlit dots
        "\u17b4", "\u17b5",               # KHMER VOWEL INHERENT AQ/AA (Mn)
        "\u034f",                         # COMBINING GRAPHEME JOINER (Mn)
        "\u180b", "\u180c", "\u180d", "\u180f",   # MONGOLIAN FREE VARIATION SELECTOR 1-4 (Mn)
        "\ufe00", "\ufe0f",               # VARIATION SELECTOR-1/-16 (Mn): pick a form
        "\U000e0100", "\U000e01ef",       # VARIATION SELECTOR-17/-256 (Mn)
        "\U0001d159",                     # MUSICAL SYMBOL NULL NOTEHEAD (So) — a rest
    )
    # Invisible ALONE, never by association: a glyph wearing one stays content.
    ORDINARY_VISIBLE = (
        "\uac01\ubc14",                   # Hangul SYLLABLES, not fillers
        "\u2801\u2802",                   # BRAILLE cells with lit dots, not U+2800
        "a\u034f",                        # a letter wearing a combining grapheme joiner
        "\u2764\ufe0f", "\u0e01\ufe00",    # a heart / a letter wearing a variation selector
    )

    def repro(self, prefix):
        """A footer that lost the marker's blank line, with `prefix` in front."""
        return prefix + r.CONTINUE_HEADING + "1/2): AAAA"

    def test_the_exact_repro_is_unusable_for_every_prefix(self):
        for prefix in self.PREFIXES:
            task = self.repro(prefix)
            self.assertIsNone(r._usable_task({"task": task}, True), repr(task))

    def test_every_prefix_escalates_with_no_footer_at_all(self):
        for prefix in self.PREFIXES:
            task = self.repro(prefix)
            st = self.other_state()
            make_record(st, task=task, exit_json={"rc": 1})
            info = classify(os.path.join(st, "agents", RUN), probe=dead)
            self.assertEqual((info["state"], info["record_suspect"]),
                             ("unknown", True), repr(task))
            self.assertEqual(r.next_action(0, info["state"],
                                           record_suspect=info["record_suspect"]),
                             "escalate", repr(task))
            p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
            self.assertEqual((p["action"], p["state"], p["record_suspect"]),
                             ("escalate", "unknown", True), repr(task))
            self.assertIsNone(p["continue_task"], repr(task))
            # Zero stacked footers — in the plan, and in anything it says.
            self.assertEqual(json.dumps(p, sort_keys=True, default=str)
                             .count("CONTINUE FROM CURRENT DIFF"), 0, repr(task))
            self.assertFalse(os.path.exists(r.path_of(LANE, st)), repr(task))

    def test_the_exact_repro_exits_4_through_the_cli(self):
        make_record(self.state, task=self.repro("\u200b"), exit_json={"rc": 1})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = r.main(["plan", RUN, "--lane", LANE])
        self.assertEqual(code, 4)
        printed = out.getvalue() + err.getvalue()
        self.assertNotIn("CONTINUE FROM CURRENT DIFF", printed)

    def test_an_esc_that_swallows_the_first_letter_is_no_better(self):
        """D1: `_usable_task` ran `_normalise_output` BEFORE `_heading_form`, and
        the Fe pattern ate ESC + the 'C' of 'CONTINUE'. The stripped head then no
        longer CONTAINED the heading form, the footer-only task was judged usable,
        and the plan reran (exit 3) with TWO stacked footers. The heading check
        runs on the raw head now, where ESC is just another Cc: refused."""
        for prefix in self.ESC_SHAPES:
            task = self.repro(prefix)
            self.assertIsNone(r._usable_task({"task": task}, True), repr(task))
            st = self.other_state()
            make_record(st, task=task, exit_json={"rc": 1})
            p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
            self.assertEqual((p["action"], p["state"], p["record_suspect"]),
                             ("escalate", "unknown", True), repr(task))
            self.assertIsNone(p["continue_task"], repr(task))

    def test_an_esc_prefixed_footer_exits_4_through_the_cli(self):
        """The same shapes through the full CLI: exit 4, and no footer in
        anything it prints."""
        for prefix in self.ESC_SHAPES:
            shutil.rmtree(os.path.join(self.state, "agents", RUN),
                          ignore_errors=True)
            make_record(self.state, task=self.repro(prefix), exit_json={"rc": 1})
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = r.main(["plan", RUN, "--lane", LANE])
            self.assertEqual(code, 4, repr(prefix))
            printed = out.getvalue() + err.getvalue()
            self.assertNotIn("CONTINUE FROM CURRENT DIFF", printed, repr(prefix))
        shutil.rmtree(os.path.join(self.state, "agents", RUN), ignore_errors=True)

    def test_an_invisible_character_inside_the_heading_is_no_better(self):
        """The class strip is not a prefix rule: an invisible woven into the heading
        still leaves a footer-only record in front of no brief at all."""
        for task in ("CONTINUE\u200b FROM CURRENT DIFF (recovery attempt 1/2): AAAA",
                     "\ufeffCONTINUE FROM \x01CURRENT DIFF (recovery attempt 1/2): AAAA"):
            self.assertIsNone(r._usable_task({"task": task}, True), repr(task))

    def test_a_blank_looking_character_hides_the_footer_like_a_zwsp_does(self):
        """AO-RECOVER-BLANK-CHARS: one of these in front of a footer whose blank line
        was lost is the content-free record the ZWSP repro is, and alone names no work."""
        for ch in self.BLANK_LOOKING:
            with self.subTest(char=ch):
                self.assertTrue(r._is_invisible_char(ch), repr(ch))
                self.assertFalse(r._has_visible_text(ch), repr(ch))
                for prefix in (ch, ch + " ", "\n" + ch + "\t"):
                    task = self.repro(prefix)
                    self.assertIsNone(r._usable_task({"task": task}, True), repr(task))
                    st = self.other_state()
                    make_record(st, task=task, exit_json={"rc": 1})
                    p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
                    self.assertEqual((p["action"], p["state"], p["record_suspect"]),
                                     ("escalate", "unknown", True), repr(task))
                    self.assertIsNone(p["continue_task"], repr(task))

    def test_ordinary_letters_and_marks_are_never_invisible(self):
        """A list of code points, not a category: a Hangul syllable, a Braille cell
        with lit dots, a letter wearing a combining mark — all still a brief."""
        for task in self.ORDINARY_VISIBLE:
            with self.subTest(task=task):
                self.assertTrue(r._has_visible_text(task), repr(task))
                self.assertIsNotNone(r._usable_task({"task": task}, True), repr(task))
        for task in ("Fix \u3164 the widget.", "Fix \u2800 the widget."):
            self.assertIsNotNone(r._usable_task({"task": task}, True), repr(task))

    def test_real_text_before_a_genuine_footer_still_gets_exactly_one(self):
        """The cut stays literal for a brief: real task text plus the previous leg's
        footer is usable, and the rebuilt brief carries ONE footer, not two."""
        sb = self.sb()
        task = "Fix the widget." + r.CONTINUE_MARKER + "1/2): older footer\n"
        make_record(self.state, task=task, output=header_lines(sb),
                    exit_json={"rc": 1})
        out = r.plan(RUN, lane_key=LANE, state=self.state, now=NOW, pid_probe=dead)
        self.assertEqual(out["action"], "rerun")
        self.assertIsNotNone(out["continue_task"])
        self.assertEqual(out["continue_task"].count("CONTINUE FROM CURRENT DIFF"), 1)
        self.assertEqual(out["continue_task"], r.continuation_task(task, 1, sb))

    def test_real_text_before_a_line_head_footer_gets_exactly_one(self):
        """D2 (P4c): a brief whose stored footer lost its blank line is NOT
        refused — the visible text ahead of the heading says it names work — and
        the cut now finds that footer through the heading form, so the rebuilt
        brief carries ONE footer, not the stored one plus a new one."""
        task = "Fix the widget.\nCONTINUE FROM CURRENT DIFF (recovery attempt 1/2): x"
        self.assertIsNotNone(r._usable_task({"task": task}, True))
        self.assertEqual(r.original_task(task), "Fix the widget.")

    def test_prose_mentioning_the_phrase_mid_line_is_untouched(self):
        """A real brief that merely spells the heading out mid-line is usable and
        its text survives verbatim in the continuation, with exactly the new footer
        added. A heading that BEGINS a line is a footer; inside a line it is
        prose."""
        task = ("Fix the widget.\nThe docs call CONTINUE FROM CURRENT DIFF (recovery "
                "attempt 1/2) the footer — do not print one yourself.\n")
        self.assertIsNotNone(r._usable_task({"task": task}, True))
        sb = self.sb()
        cont = r.continuation_task(task, 1, sb)
        self.assertTrue(cont.startswith(task.rstrip()))
        self.assertEqual(cont.count("\n\n" + r.CONTINUE_HEADING), 1)


class StackedFooterVariants(TempCase):
    """P4c D2 (Sonnet residual): `original_task` cut only at the literal
    '\\n\\nCONTINUE FROM CURRENT DIFF (recovery attempt ' marker. A stored footer
    whose separator was broken — preceded by ZWSP/BOM/NBSP/tab, written with a CRLF
    blank line, or with the blank line lost entirely — survived the cut, and
    `continuation_task` appended a SECOND footer onto a brief that already was
    one. The cut now finds the heading through `_heading_form`, maps the match
    back through `origins` to its source index, and cuts at the FIRST match that
    begins a line after optional invisible characters; a mention with real visible
    text ahead of it on the same line stays prose."""

    FOOT = r.CONTINUE_HEADING + "1/2): older footer\n"
    TASKS = (
        "do X\n\n​" + FOOT,          # ZWSP ate the separator
        "do X\n\n\ufeff" + FOOT,        # BOM
        "do X\n\n\xa0" + FOOT,          # no-break space
        "do X\n\n\t" + FOOT,            # tab
        "do X\r\n\r\n" + FOOT,          # a CRLF blank line
        "do X\r\n\r\n​" + FOOT,   # CRLF blank line + ZWSP
        "do X\n" + FOOT,                # the blank line lost entirely
        "do X\n\t" + FOOT,              # and with a tab prefix
    )

    def test_each_variant_is_still_a_usable_brief(self):
        for task in self.TASKS:
            self.assertIsNotNone(r._usable_task({"task": task}, True), repr(task))

    def test_one_rebuilt_brief_carries_exactly_one_footer(self):
        for task in self.TASKS:
            self.assertEqual(r.original_task(task), "do X", repr(task))
            cont = r.continuation_task(task, 1, SB)
            self.assertEqual(cont.count("CONTINUE FROM CURRENT DIFF"), 1, repr(task))
            self.assertEqual(cont, r.continuation_task("do X", 1, SB), repr(task))

    def test_the_second_stacked_leg_still_carries_exactly_one_footer(self):
        for task in self.TASKS:
            cont = r.continuation_task(task, 1, SB)
            again = r.continuation_task(cont, 2, SB)
            self.assertEqual(again.count("CONTINUE FROM CURRENT DIFF"), 1, repr(task))
            self.assertIn("recovery attempt 2/2", again, repr(task))
            self.assertNotIn("recovery attempt 1/2", again, repr(task))

    def test_the_plan_rebuilds_one_footer_for_every_variant(self):
        for task in self.TASKS:
            st = self.other_state()
            make_record(st, task=task, exit_json={"rc": 1})
            p = r.plan(RUN, lane_key=LANE, state=st, now=NOW, pid_probe=dead)
            self.assertEqual(p["action"], "rerun", repr(task))
            self.assertIsNotNone(p["continue_task"], repr(task))
            self.assertEqual(p["continue_task"].count("CONTINUE FROM CURRENT DIFF"),
                             1, repr(task))

    def test_a_mid_line_mention_survives_the_cut(self):
        """Real text that merely spells the heading out inside a line keeps every
        word: one footer is appended, the mention is preserved."""
        task = ("Fix the widget. The docs call CONTINUE FROM CURRENT DIFF (recovery "
                "attempt 1/2) the footer — never print one yourself.\n")
        self.assertEqual(r.original_task(task), task.rstrip())
        cont = r.continuation_task(task, 1, SB)
        self.assertTrue(cont.startswith(task.rstrip()), repr(cont))
        self.assertIn("The docs call CONTINUE FROM CURRENT DIFF (recovery "
                      "attempt 1/2) the footer", cont)
        self.assertEqual(cont.count("\n\n" + r.CONTINUE_HEADING), 1)


class Attempts(TempCase):
    def test_no_file_is_zero_attempts(self):
        cur = r.read_attempts(LANE, self.state)
        self.assertEqual(cur, {"key": LANE, "attempts": 0, "runs": [],
                               "last_ts": None, "corrupt": False})
        self.assertFalse(os.path.exists(r.recovery_dir(self.state)))

    def test_record_writes_the_documented_shape(self):
        cur = r.record_attempt(LANE, RUN, self.state)
        self.assertEqual(cur["attempts"], 1)
        data = json.loads(io.open(r.path_of(LANE, self.state), encoding="utf-8").read())
        self.assertEqual(sorted(data), ["attempts", "key", "last_ts", "runs"])
        self.assertEqual(data, {"key": LANE, "attempts": 1, "runs": [RUN], "last_ts": NOW})

    def test_two_deaths_on_one_lane_are_two_legs(self):
        r.record_attempt(LANE, RUN, self.state)
        second = r.record_attempt(LANE, LEG1, self.state)
        self.assertEqual(second["attempts"], 2)
        self.assertEqual(second["runs"], [RUN, LEG1])

    def test_idempotent_per_run_id(self):
        r.record_attempt(LANE, RUN, self.state)
        again = r.record_attempt(LANE, RUN, self.state)
        self.assertEqual((again["attempts"], again["runs"]), (1, [RUN]))
        self.assertEqual(r.read_attempts(LANE, self.state)["attempts"], 1)

    def test_the_third_death_escalates(self):
        for run in (RUN, LEG1):
            r.record_attempt(LANE, run, self.state)
        cur = r.read_attempts(LANE, self.state)
        self.assertEqual(cur["attempts"], r.MAX_ATTEMPTS)
        self.assertEqual(r.next_action(cur["attempts"], "died"), "escalate")

    def test_lanes_are_independent(self):
        r.record_attempt("lane/a", RUN, self.state)
        self.assertEqual(r.read_attempts("lane/b", self.state)["attempts"], 0)

    def test_the_state_file_and_its_lock_are_owner_only(self):
        """The budget file and the lock that guards it are both 0600."""
        if os.name == "nt":
            self.skipTest("the mode bits mean nothing on Windows")
        r.record_attempt(LANE, RUN, self.state)
        mode = stat.S_IMODE(os.stat(r.path_of(LANE, self.state)).st_mode)
        dmode = stat.S_IMODE(os.stat(r.recovery_dir(self.state)).st_mode)
        lock_mode = stat.S_IMODE(os.stat(r._lock_path(LANE, self.state)).st_mode)
        self.assertEqual((mode, dmode, lock_mode), (0o600, 0o700, 0o600))

    def test_no_temp_file_survives_a_write(self):
        r.record_attempt(LANE, RUN, self.state)
        left = sorted(os.path.basename(p) for p in os.listdir(r.recovery_dir(self.state)))
        self.assertEqual([p for p in left if p.endswith(".tmp")], [])
        self.assertEqual(left, sorted([os.path.basename(r.path_of(LANE, self.state)),
                                       os.path.basename(r._lock_path(LANE, self.state))]))

    def test_a_stale_temp_file_is_not_read_as_state(self):
        os.makedirs(r.recovery_dir(self.state))
        write_text(os.path.join(r.recovery_dir(self.state), ".recovery-junk.tmp"), "{")
        self.assertEqual(r.read_attempts(LANE, self.state)["attempts"], 0)

    def test_two_threads_cannot_spend_the_budget_twice(self):
        """The read-modify-write is one step: two recorders of DISTINCT runs are
        two legs, never a lost update that hands the lane more legs than it was
        given."""
        def slow_now():
            # Widens the read-modify-write window on purpose, so an unlocked
            # version of this function demonstrably loses updates.
            time.sleep(0.002)
            return NOW
        r._now = slow_now
        per_thread = 100
        errors = []

        def record(prefix):
            try:
                for i in range(per_thread):
                    r.record_attempt(LANE, "%s-%03d" % (prefix, i), self.state)
            except BaseException as exc:                    # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=record, args=("a",)),
                   threading.Thread(target=record, args=("b",))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        self.assertEqual(errors, [])
        cur = r.read_attempts(LANE, self.state)
        self.assertEqual(cur["attempts"], 2 * per_thread)
        self.assertEqual(len(set(cur["runs"])), 2 * per_thread)
        self.assertEqual(len(cur["runs"]), 2 * per_thread)

    def test_refused_arguments(self):
        for bad_key in ("", "bad key", None, 7):
            with self.assertRaises(r.RecoveryError):
                r.record_attempt(bad_key, RUN, self.state)
        for bad_run in ("../x", "a/b", "", None, 7):
            with self.assertRaises(r.RecoveryError):
                r.record_attempt(LANE, bad_run, self.state)

    def corrupt_file(self, body, state=None):
        st = state or self.state
        os.makedirs(r.recovery_dir(st), exist_ok=True)
        write_text(r.path_of(LANE, st), body)
        return r.read_attempts(LANE, st)

    def test_a_record_that_cannot_be_read_is_max_attempts_and_says_so(self):
        """Fail closed: an unreadable budget is a spent budget, not a fresh one."""
        for body in ("{ not json", "", "[]", '"a string"', "null", "17"):
            cur = self.corrupt_file(body, self.other_state())
            self.assertEqual((cur["attempts"], cur["corrupt"]), (r.MAX_ATTEMPTS, True), body)
            self.assertEqual(r.next_action(cur["attempts"], "died"), "escalate", body)

    def test_a_wrongly_shaped_record_is_corrupt(self):
        for data in ({"attempts": "1", "runs": []}, {"attempts": True, "runs": []},
                     {"attempts": -1, "runs": []}, {"attempts": 1.0, "runs": []},
                     {"attempts": 1, "runs": "not-a-list"}, {"attempts": 1, "runs": [7]},
                     {"attempts": 0, "runs": ["a"]}, {"attempts": 1}, {"runs": []}, {},
                     # Found by this lane's digest, but naming another lane.
                     {"key": "lane/other", "attempts": 1, "runs": ["a"]},
                     {"key": 7, "attempts": 1, "runs": ["a"]}):
            body = json.dumps(data)
            self.assertTrue(self.corrupt_file(body, self.other_state())["corrupt"], body)

    def test_a_valid_record_with_extra_keys_is_not_corrupt(self):
        cur = self.corrupt_file(json.dumps({"key": LANE, "attempts": 2, "runs": ["a", "b"],
                                           "last_ts": NOW, "who": "l1"}))
        self.assertEqual((cur["attempts"], cur["corrupt"]), (2, False))
        self.assertEqual(cur["runs"], ["a", "b"])

    def test_recording_onto_a_corrupt_lane_is_refused(self):
        """Overwriting the file here would hand back the budget the corruption hid."""
        self.corrupt_file("{ not json")
        with self.assertRaises(r.RecoveryError):
            r.record_attempt(LANE, RUN, self.state)
        self.assertTrue(r.read_attempts(LANE, self.state)["corrupt"])

    @unittest.skipIf(os.name == "nt", "symlinked state file; POSIX only")
    def test_a_symlinked_state_file_is_refused_not_read(self):
        os.makedirs(r.recovery_dir(self.state))
        target = os.path.join(self.state, "elsewhere.json")
        write_text(target, json.dumps({"key": LANE, "attempts": 0, "runs": [],
                                       "last_ts": None}))
        os.symlink(target, r.path_of(LANE, self.state))
        with self.assertRaises(r.RecoveryError):
            r.read_attempts(LANE, self.state)
        with self.assertRaises(r.RecoveryError):
            r.record_attempt(LANE, RUN, self.state)

    @unittest.skipIf(os.name == "nt", "symlinked lock file; POSIX only")
    def test_a_symlinked_lock_file_is_refused(self):
        os.makedirs(r.recovery_dir(self.state))
        target = os.path.join(self.state, "elsewhere.lock")
        write_text(target, "x")
        os.symlink(target, r._lock_path(LANE, self.state))
        with self.assertRaises(r.RecoveryError):
            r.record_attempt(LANE, RUN, self.state)


class LaneLockTimeout(TempCase):
    """P4c-fixes D3: the POSIX flock is taken with LOCK_NB inside the same
    bounded retry loop Windows pays, so a LIVE holder delays a recorder and is
    then refused — it never hangs `record_attempt` unboundedly. POSIX only."""

    def guarded(self, fn, what):
        """Run `fn` on a watchdog thread: a blocking-flock regression fails the
        test instead of hanging the suite."""
        box = {}

        def work():
            t0 = time.monotonic()
            try:
                box["value"] = fn()
            except BaseException as exc:                # noqa: BLE001
                box["error"] = exc
            box["elapsed"] = time.monotonic() - t0

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(10)
        if thread.is_alive():
            self.fail("%s blocked: flock waits unbounded for the holder" % what)
        return box

    def small_budget(self, retries=5, secs=0.001):
        old = (r.LOCK_RETRIES, r.LOCK_RETRY_SECS)
        self.addCleanup(setattr, r, "LOCK_RETRIES", old[0])
        self.addCleanup(setattr, r, "LOCK_RETRY_SECS", old[1])
        r.LOCK_RETRIES, r.LOCK_RETRY_SECS = retries, secs

    def holder(self, path):
        """A thread that takes the lane lock and holds it until told to let go."""
        acquired, release, errors = threading.Event(), threading.Event(), []

        def hold():
            try:
                fd = r._acquire_lock(path)
                acquired.set()
                release.wait(30)
                r._release_lock(fd)
            except BaseException as exc:                # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(10), "the holder never took the lock")
        return thread, release, errors

    @unittest.skipIf(os.name == "nt", "flock; POSIX only")
    def test_a_held_lock_is_refused_within_the_retry_budget(self):
        os.makedirs(r.recovery_dir(self.state))
        self.small_budget()
        thread, release, errors = self.holder(r._lock_path(LANE, self.state))
        try:
            box = self.guarded(lambda: r.record_attempt(LANE, RUN, self.state),
                               "record_attempt")
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertIsInstance(box.get("error"), r.RecoveryError)
        self.assertLess(box["elapsed"], 1.0, "the lock wait was not bounded")
        self.assertFalse(os.path.exists(r.path_of(LANE, self.state)))

    @unittest.skipIf(os.name == "nt", "flock; POSIX only")
    def test_a_lock_released_mid_wait_is_acquired(self):
        os.makedirs(r.recovery_dir(self.state))
        self.small_budget(retries=200, secs=0.01)   # room to wait; the release ends it
        thread, release, errors = self.holder(r._lock_path(LANE, self.state))
        box = {}

        def record():
            try:
                box["value"] = r.record_attempt(LANE, RUN, self.state)
            except BaseException as exc:                # noqa: BLE001
                box["error"] = exc

        recorder = threading.Thread(target=record, daemon=True)
        recorder.start()
        time.sleep(0.05)                          # let the recorder start spinning
        release.set()
        recorder.join(10)
        thread.join(10)
        self.assertFalse(recorder.is_alive(), "the recorder never woke to the release")
        self.assertEqual(errors, [])
        self.assertNotIn("error", box, box.get("error"))
        self.assertEqual(box["value"]["attempts"], 1)
        self.assertLess(box["value"]["attempts"], r.MAX_ATTEMPTS + 1)

    @unittest.skipIf(os.name == "nt", "flock; POSIX only")
    def test_the_lock_fd_is_closed_whether_the_lock_came_or_not(self):
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("/proc/self/fd; Linux only")

        def fd_count():
            return len(os.listdir("/proc/self/fd"))

        os.makedirs(r.recovery_dir(self.state))
        before = fd_count()
        r.record_attempt(LANE, RUN, self.state)          # uncontended: take and release
        self.assertEqual(fd_count(), before)
        self.small_budget()
        thread, release, errors = self.holder(r._lock_path("lane/fd", self.state))
        try:
            box = self.guarded(lambda: r.record_attempt("lane/fd", RUN, self.state),
                               "contended record")
        finally:
            release.set()
            thread.join(10)
        self.assertIsInstance(box.get("error"), r.RecoveryError)
        self.assertEqual(fd_count(), before, "a lock fd leaked on the refusal path")


class RunIdsAndDirs(TempCase):
    def test_accepted_ids(self):
        for run in (RUN, "a", "a" * r.RUN_ID_MAX_CHARS, "task-1_2.3"):
            self.assertEqual(r._check_run_id(run), run)

    def test_refused_ids_never_traverse(self):
        for bad in ("a/b", "..", "../x", "2026..1", "", ".", ".hidden", "-",
                    "a" * (r.RUN_ID_MAX_CHARS + 1), "run name", "run\nname",
                    None, 7, b"run", "é"):
            with self.assertRaises(r.RecoveryError):
                r._check_run_id(bad)
            with self.assertRaises(r.RecoveryError):
                r.run_dir_for(bad, self.state)

    def test_a_missing_run_dir_is_refused(self):
        with self.assertRaises(r.RecoveryError):
            r.run_dir_for(RUN, self.state)

    def test_classify_refuses_a_path_that_is_not_a_record_dir(self):
        for bad in ("", None, 7, [], os.path.join(self.state, "nope")):
            with self.assertRaises(r.RecoveryError):
                r.classify_run(bad, now=NOW)

    @unittest.skipIf(os.name == "nt", "symlinked run dir; POSIX only")
    def test_a_symlinked_run_dir_is_refused(self):
        real = make_record(self.state, run_id=LEG1)
        link = os.path.join(self.state, "agents", RUN)
        os.symlink(real, link)
        with self.assertRaises(r.RecoveryError):
            r.run_dir_for(RUN, self.state)
        with self.assertRaises(r.RecoveryError):
            r.classify_run(link, now=NOW, pid_probe=alive)

    def test_a_trailing_separator_still_names_the_dir(self):
        root = make_record(self.state)
        self.assertEqual(classify(root + os.sep, probe=alive)["state"], "running")

    def test_the_state_root_default_follows_the_environment(self):
        self.assertEqual(r.state_dir(), self.state)
        self.assertEqual(r.state_dir("/explicit"), "/explicit")
        self.assertEqual(r.agents_root("/explicit"), os.path.join("/explicit", "agents"))

    def test_time_arguments_are_checked(self):
        root = make_record(self.state)
        for bad in ("now", True, "1e9", "17"):
            with self.assertRaises(r.RecoveryError):
                r.classify_run(root, now=bad, pid_probe=alive)
        for bad in (0, -5, "900", True, 0.0):
            with self.assertRaises(r.RecoveryError):
                r.classify_run(root, now=NOW, stall_secs=bad, pid_probe=alive)
        with self.assertRaises(r.RecoveryError):
            r.classify_run(root, now=NOW, pid_probe="alive")

    def test_now_defaults_to_the_module_clock_hook(self):
        root = make_record(self.state)
        self.assertEqual(r.classify_run(root, pid_probe=alive)["last_output_age"], 0.0)

    # P4c-fixes4 (C): `stall <= 0` cannot refuse NaN — and `age <= nan` is False, so
    # an unusable clock is not a harmless default, it is the verdict 'stalled' for a
    # writer that is alive and the plan 'rerun' beside it. One bound, two ends.
    UNUSABLE_CLOCKS = (float("nan"), float("inf"), float("-inf"), 1e999,
                       604800.1, 604801, 10 ** 12)

    def test_the_stall_clock_is_bounded_on_both_ends(self):
        root = make_record(self.state)
        for bad in self.UNUSABLE_CLOCKS + (0, -5, 0.5, True, "900"):
            with self.assertRaises(r.RecoveryError):
                r.classify_run(root, now=NOW, stall_secs=bad, pid_probe=alive)
        for ok in (1, 1.0, 60, 900, 604800):
            info = r.classify_run(root, now=NOW, stall_secs=ok, pid_probe=alive)
            self.assertEqual(info["state"], "running", ok)

    def test_a_non_finite_now_is_refused(self):
        root = make_record(self.state)
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(r.RecoveryError):
                r.classify_run(root, now=bad, pid_probe=alive)

    def test_a_non_finite_mtime_is_no_age_at_all(self):
        """The stall clock reads a filesystem mtime. A NaN there says nothing about
        age, and 'nothing' is 'unknown' — never `age <= stall` on a NaN, which is
        False and would answer 'stalled' for a live pid."""
        root = make_record(self.state)
        real = os.stat

        class DamagedStat(object):
            st_mode = stat.S_IFDIR | 0o700       # keep os.path.isdir working
            st_mtime = float("nan")

        os.stat = lambda *a, **k: DamagedStat()
        try:
            self.assertIsNone(r._output_age(root, NOW))
            info = classify(root, probe=alive)
            self.assertIsNone(info["last_output_age"])
            self.assertEqual(info["state"], "unknown")
            self.assertEqual(r.next_action(0, info["state"]), "escalate")
        finally:
            os.stat = real
        self.assertEqual(classify(root, probe=alive)["state"], "running")


class Plan(TempCase):
    KEYS = {"run_id", "lane_key", "action", "state", "attempts", "rc", "has_report",
            "sandbox", "branch", "last_output_age", "state_corrupt", "record_suspect",
            "continue_task", "spawn_hint"}

    def plan(self, state=None, lane_key=LANE, **over):
        kw = {"state": state or self.state, "now": NOW, "pid_probe": dead}
        kw.update(over)
        return r.plan(RUN, lane_key=lane_key, **kw)

    def test_a_dead_writer_gets_one_continuation_leg(self):
        sb = self.sb()
        make_record(self.state, output=spawner_lines(sb))
        p = self.plan(lane_key=LANE)
        self.assertEqual(set(p), self.KEYS)
        self.assertEqual((p["action"], p["state"], p["attempts"]), ("rerun", "died", 0))
        self.assertEqual((p["sandbox"], p["branch"]), (sb, BRANCH))
        self.assertEqual(p["continue_task"], r.continuation_task(TASK, 1, sb))
        self.assertEqual(p["spawn_hint"]["cwd"], sb)
        self.assertIn("record_attempt", p["spawn_hint"]["note"])
        self.assertFalse(p["state_corrupt"])
        self.assertFalse(p["record_suspect"])
        self.assertEqual(p["lane_key"], LANE)

    def test_plan_without_a_lane_key_is_refused(self):
        """D-914: the lane is never derived from the task — a plan with no named
        lane is a usage error, whatever the record says."""
        make_record(self.state)
        with self.assertRaises(r.RecoveryError):
            r.plan(RUN, state=self.state, now=NOW, pid_probe=dead)

    def test_the_budget_is_shared_by_every_writer_on_the_lane(self):
        make_record(self.state, output=spawner_lines(self.sb()))
        self.assertEqual(self.plan(lane_key=LANE)["action"], "rerun")
        r.record_attempt(LANE, LEG1, self.state)
        second = self.plan(lane_key=LANE)
        self.assertEqual((second["attempts"], second["action"]), (1, "rerun"))
        r.record_attempt(LANE, LEG2, self.state)
        third = self.plan(lane_key=LANE)
        self.assertEqual((third["attempts"], third["action"]), (2, "escalate"))
        self.assertIsNone(third["continue_task"])
        self.assertIsNone(third["spawn_hint"])

    def test_a_running_run_waits(self):
        make_record(self.state)
        p = self.plan(pid_probe=alive)
        self.assertEqual((p["state"], p["action"]), ("running", "wait"))
        self.assertIsNone(p["continue_task"])

    def test_a_completed_run_needs_nothing(self):
        st = self.other_state()
        sb = self.sb(state=st)
        make_record(st, exit_json={"rc": 0}, output=spawner_lines(sb, report=True))
        p = self.plan(state=st, pid_probe=alive)
        self.assertEqual((p["state"], p["action"], p["continue_task"]),
                         ("completed", "none", None))
        self.assertEqual(p["sandbox"], sb)

    def test_a_forged_sandbox_never_becomes_the_continuation_cwd(self):
        """The bad path is refused, so the plan re-runs with an honest hint rather
        than handing the L1 a cwd of the writer's choosing."""
        make_record(self.state, output=worker_lines("thinking")
                    + header_lines("/etc", "evil/branch"))
        p = self.plan()
        self.assertIsNone(p["sandbox"])
        self.assertEqual(p["action"], "rerun")
        self.assertIsNone(p["spawn_hint"]["cwd"])
        self.assertIn("no sandbox path", p["spawn_hint"]["note"])

    def test_an_unknown_run_escalates(self):
        st = self.other_state()
        make_record(st, job_json="{oops")
        p = self.plan(state=st, lane_key=LANE)
        self.assertEqual((p["action"], p["state"]), ("escalate", "unknown"))
        self.assertIsNone(p["continue_task"])

    def test_a_stalled_run_reruns_with_the_stall_clock(self):
        make_record(self.state, mtime=NOW - 901)
        p = self.plan(pid_probe=alive)
        self.assertEqual((p["state"], p["action"], p["last_output_age"]),
                         ("stalled", "rerun", 901.0))

    def test_no_sandbox_path_still_reruns_with_an_honest_hint(self):
        make_record(self.state, output="worker thinking\n")
        p = self.plan()
        self.assertEqual(p["action"], "rerun")
        self.assertIsNone(p["spawn_hint"]["cwd"])
        self.assertIn("no sandbox path", p["spawn_hint"]["note"])
        self.assertIn("worktree you are given", p["continue_task"])

    def test_a_corrupt_lane_is_reported_and_escalates(self):
        make_record(self.state, output=spawner_lines(self.sb()))
        os.makedirs(r.recovery_dir(self.state))
        write_text(r.path_of(LANE, self.state), "{ not json")
        p = self.plan(lane_key=LANE)
        self.assertTrue(p["state_corrupt"])
        self.assertEqual((p["action"], p["attempts"]), ("escalate", r.MAX_ATTEMPTS))

    def test_a_bad_lane_key_or_run_id_is_refused(self):
        make_record(self.state)
        with self.assertRaises(r.RecoveryError):
            self.plan(lane_key="bad key")
        with self.assertRaises(r.RecoveryError):
            r.plan("../etc", lane_key=LANE, state=self.state)
        with self.assertRaises(r.RecoveryError):
            r.plan("../etc", state=self.state)

    def test_a_missing_run_is_refused(self):
        with self.assertRaises(r.RecoveryError):
            self.plan()

    def test_planning_writes_nothing(self):
        """Read-only: no attempt file, no new directory, and of course no spawn.

        Compared against the tree the test itself built (the sandbox `plan` reads
        back is a real directory inside the trusted root), so a written file shows
        up as a new name rather than as a mismatch with a hardcoded list.
        """
        make_record(self.state, output=spawner_lines(self.sb()))
        before = sorted(os.listdir(self.state))
        self.plan(lane_key=LANE)
        self.plan(lane_key=LANE)
        self.assertEqual(sorted(os.listdir(self.state)), before)
        self.assertNotIn("recovery", before)
        self.assertEqual(r.read_attempts(LANE, self.state)["attempts"], 0)


class Cli(TempCase):
    def cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = r.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_rerun_exits_three(self):
        make_record(self.state, exit_json={"rc": 1}, output=spawner_lines(self.sb()))
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(text)["action"], "rerun")
        self.assertEqual(json.loads(text)["lane_key"], LANE)

    def test_an_echoed_report_exits_three_not_zero(self):
        """The forged-REPORT case end to end: exit 0, the brief in the log echoing
        a REPORT line — the plan must not read that as completed."""
        task = "do the work\n%s" % report_body()
        make_record(self.state, task=task, exit_json={"rc": 0}, output=[task])
        code, text, err = self.cli(["plan", RUN, "--lane", LANE])
        out = json.loads(text)
        self.assertEqual((code, out["action"], out["state"]), (3, "rerun", "died"))
        self.assertFalse(out["has_report"])
        self.assertEqual(err, "")

    def test_wait_and_none_exit_zero(self):
        make_record(self.state, pid=os.getpid())            # this pid is alive
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE])
        self.assertEqual((code, json.loads(text)["state"], json.loads(text)["action"]),
                         (0, "running", "wait"))
        st = self.other_state()
        os.environ["AUTOOS_STATE_DIR"] = st
        make_record(st, exit_json={"rc": 0},
                    output=spawner_lines(self.sb(state=st), report=True))
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE])
        self.assertEqual((code, json.loads(text)["action"]), (0, "none"))

    def test_escalate_exits_four(self):
        make_record(self.state, exit_json={"rc": 1})
        r.record_attempt(LANE, LEG1, self.state)
        r.record_attempt(LANE, LEG2, self.state)
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE])
        self.assertEqual((code, json.loads(text)["action"]), (4, "escalate"))

    def test_record_exits_zero_and_the_next_plan_sees_it(self):
        code, text, _ = self.cli(["record", RUN, "--lane", LANE])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["attempts"], 1)
        path = r.path_of(LANE)               # the environment alone picks the state root
        self.assertEqual(os.path.dirname(path), r.recovery_dir())
        self.assertTrue(os.path.exists(path))
        self.assertEqual(r.read_attempts(LANE)["attempts"], 1)

    def test_stall_secs_reaches_the_plan(self):
        make_record(self.state, pid=os.getpid(), mtime=NOW - 60,
                    output=spawner_lines(self.sb()))
        self.assertEqual(self.cli(["plan", RUN, "--lane", LANE,
                                   "--stall-secs", "30"])[0], 3)
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE,
                                  "--stall-secs", "120"])
        self.assertEqual((code, json.loads(text)["action"]), (0, "wait"))

    def test_a_stall_clock_that_is_not_a_number_exits_two(self):
        """P4c-fixes4 (C): `plan --stall-secs nan` on a LIVE fresh writer answered
        stalled / rerun / 3 — the plan that starts a second writer beside the first.
        An unusable clock is a usage error (exit 2) and the writer keeps waiting."""
        make_record(self.state, pid=os.getpid(), mtime=NOW,
                    output=spawner_lines(self.sb()))
        for arg in ("nan", "inf", "-inf", "1e999", "0", "-1", "604801", "0.5"):
            # `--opt=-1`: the space-separated form makes argparse read the leading
            # dash as another option, which is its own usage error, not this one.
            code, text, err = self.cli(
                ["plan", RUN, "--lane", LANE, "--stall-secs=%s" % arg])
            self.assertEqual(code, 2, arg)
            self.assertIn("autoos_recovery:", err)
            self.assertNotIn('"action": "rerun"', text)
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE,
                                  "--stall-secs", "120"])
        self.assertEqual((code, json.loads(text)["action"]), (0, "wait"))

    def test_a_stall_clock_that_is_not_a_float_is_argparse_s_usage_error(self):
        """The text never reaches the module: float() has no hex and no empty
        string, and argparse answers the usage exit (2) itself."""
        make_record(self.state)
        for arg in ("0x10", "", "five"):
            with self.assertRaises(SystemExit) as caught:
                self.cli(["plan", RUN, "--lane", LANE, "--stall-secs", arg])
            self.assertEqual(caught.exception.code, 2, arg)
        # ' 5' IS a float (5.0) and inside the band, so it is a usable clock.
        self.assertEqual(self.cli(["plan", RUN, "--lane", LANE,
                                   "--stall-secs", " 5"])[0], 3)

    def test_bad_input_exits_two(self):
        make_record(self.state)
        for argv in (["plan", "../etc", "--lane", LANE],
                     ["plan", "a" * 200, "--lane", LANE],
                     ["plan", RUN, "--lane", "bad key"],
                     ["record", RUN, "--lane", "bad key"],
                     ["plan", RUN, "--lane", LANE, "--stall-secs", "0"]):
            code, _, err = self.cli(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("autoos_recovery:", err)

    def test_a_forged_report_under_a_lazy_quote_exits_three_not_zero(self):
        """The blockquote-lazy-continuation case end to end (P4c-fixes D1): the
        quoted REPORT line names this run, rc is 0 — the plan must still not
        read it as completed."""
        task = "Implement the widget.\n"
        make_record(self.state, task=task, exit_json={"rc": 0},
                    output="> worker quoting a report:\n"
                           "REPORT %s · OK · green\n" % RUN)
        code, text, err = self.cli(["plan", RUN, "--lane", LANE])
        out = json.loads(text)
        self.assertEqual((code, out["action"], out["state"], out["has_report"]),
                         (3, "rerun", "died", False))

    def test_a_suspect_record_exits_four_with_a_message_not_a_traceback(self):
        """A job.json whose task is a number (P4c-fixes D2): the CLI answers
        escalate/4, never a crash and never a 0."""
        make_record(self.state, task=123, exit_json={"rc": 0}, output=[report_body()])
        code, text, err = self.cli(["plan", RUN, "--lane", LANE])
        out = json.loads(text)
        self.assertEqual((code, out["action"], out["record_suspect"]), (4, "escalate", True))
        self.assertIn("wrong type", err)
        self.assertNotIn("Traceback", err)

    def test_an_indented_claim_with_rc_zero_exits_three_not_zero(self):
        """The (A) case end to end (P4c-fixes4): rc 0 and a REPORT the worker only
        pasted, indented as markdown code — died / rerun / 3, never none / 0."""
        make_record(self.state, exit_json={"rc": 0},
                    output="worker text\n\n    REPORT %s · OK · green\n" % RUN)
        code, text, err = self.cli(["plan", RUN, "--lane", LANE])
        out = json.loads(text)
        self.assertEqual((code, out["action"], out["state"], out["has_report"]),
                         (3, "rerun", "died", False))

    def test_an_html_blocked_claim_with_rc_zero_exits_three_not_zero(self):
        """The (B) case end to end: the same claim inside a `<pre>` block."""
        for wrapper in ("<pre>\n", "<script>\n", "<details>\n", "<div>\n"):
            st = self.other_state()
            os.environ["AUTOOS_STATE_DIR"] = st
            make_record(st, exit_json={"rc": 0},
                        output="worker text\n\n%sREPORT %s · OK · green\n"
                               % (wrapper, RUN))
            code, text, _ = self.cli(["plan", RUN, "--lane", LANE])
            out = json.loads(text)
            self.assertEqual((code, out["action"], out["state"], out["has_report"]),
                             (3, "rerun", "died", False), wrapper)

    def test_a_genuine_column_zero_report_still_exits_zero(self):
        make_record(self.state, exit_json={"rc": 0},
                    output=spawner_lines(self.sb(), report=True))
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE])
        self.assertEqual((code, json.loads(text)["action"], json.loads(text)["state"]),
                         (0, "none", "completed"))

    # P4c-fixes3 (3): corrupt / contested / non-regular / unwritable lane state
    # is an ESCALATION (exit 4), not a usage error (exit 2); 2 stays only for
    # argparse failures and bad run ids / lane keys.
    def test_a_corrupt_lane_state_record_exits_four_not_two(self):
        os.makedirs(r.recovery_dir(self.state))
        write_text(r.path_of(LANE, self.state), "{ not json")
        code, _, err = self.cli(["record", RUN, "--lane", LANE])
        self.assertEqual(code, 4)
        self.assertIn("autoos_recovery:", err)
        self.assertIn("corrupt", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(err.count("\n"), 1, "the message is one line")

    @unittest.skipIf(os.name == "nt", "flock; POSIX only")
    def test_a_held_lane_lock_record_exits_four(self):
        os.makedirs(r.recovery_dir(self.state))
        old = (r.LOCK_RETRIES, r.LOCK_RETRY_SECS)
        self.addCleanup(setattr, r, "LOCK_RETRIES", old[0])
        self.addCleanup(setattr, r, "LOCK_RETRY_SECS", old[1])
        r.LOCK_RETRIES, r.LOCK_RETRY_SECS = 5, 0.001
        acquired, release, errors = threading.Event(), threading.Event(), []

        def hold():
            try:
                fd = r._acquire_lock(r._lock_path(LANE, self.state))
                acquired.set()
                release.wait(30)
                r._release_lock(fd)
            except BaseException as exc:                # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(10), "the holder never took the lock")
        try:
            code, _, err = self.cli(["record", RUN, "--lane", LANE])
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(code, 4)
        self.assertIn("held by another writer", err)
        self.assertNotIn("Traceback", err)

    @unittest.skipIf(os.name == "nt" or not hasattr(os, "mkfifo"), "mkfifo; POSIX only")
    def test_a_fifo_state_record_exits_four(self):
        os.makedirs(r.recovery_dir(self.state))
        os.mkfifo(r.path_of(LANE, self.state))
        code, _, err = self.cli(["record", RUN, "--lane", LANE])
        self.assertEqual(code, 4)
        self.assertIn("autoos_recovery:", err)

    @unittest.skipIf(os.name == "nt", "symlinked state file; POSIX only")
    def test_a_symlinked_state_record_and_plan_exit_four(self):
        os.makedirs(r.recovery_dir(self.state))
        write_text(os.path.join(self.state, "elsewhere.json"), "{}")
        os.symlink(os.path.join(self.state, "elsewhere.json"), r.path_of(LANE, self.state))
        make_record(self.state, output=spawner_lines(self.sb()))
        self.assertEqual(self.cli(["record", RUN, "--lane", LANE])[0], 4)
        self.assertEqual(self.cli(["plan", RUN, "--lane", LANE])[0], 4)

    @unittest.skipIf(os.name == "nt", "directory modes; POSIX only")
    def test_an_unwritable_state_dir_record_exits_four(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        os.chmod(self.state, 0o500)
        self.addCleanup(os.chmod, self.state, 0o700)
        code, _, err = self.cli(["record", RUN, "--lane", LANE])
        self.assertEqual(code, 4)
        self.assertIn("cannot prepare the recovery state dir", err)

    def test_a_lane_state_error_is_still_a_recovery_error(self):
        """The refuse-locally contract does not change for library callers:
        every raise stays catchable as RecoveryError; only the CLI exit splits."""
        self.assertTrue(issubclass(r.RecoveryStateError, r.RecoveryError))

    def test_an_unexpected_fault_exits_four_never_zero_or_three(self):
        make_record(self.state, exit_json={"rc": 1})
        real_plan = r.plan

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected fault")

        r.plan = boom
        try:
            code, _, err = self.cli(["plan", RUN, "--lane", LANE])
        finally:
            r.plan = real_plan
        self.assertEqual(code, 4)
        self.assertIn("internal error", err)

    def test_keyboard_interrupt_and_broken_pipe_keep_their_own_exits(self):
        make_record(self.state, exit_json={"rc": 1})
        real_plan = r.plan
        for exc in (KeyboardInterrupt, BrokenPipeError):
            def raiser(*args, _exc=exc, **kwargs):
                raise _exc

            r.plan = raiser
            try:
                with self.assertRaises(exc):
                    self.cli(["plan", RUN, "--lane", LANE])
            finally:
                r.plan = real_plan

    def test_usage_errors_exit_two(self):
        for argv in (["record", RUN], ["plan"], [], ["plan", RUN, "--lane"]):
            with self.assertRaises(SystemExit) as caught:
                self.cli(argv)
            self.assertEqual(caught.exception.code, 2)
        # AO-JOB-LANE-ID: `plan RUN` with no --lane left argparse's hands — the
        # module asks the runner-private record for the lane the spawner wrote,
        # and with none recorded it is STILL usage exit 2, now in its own words.
        code, out, err = self.cli(["plan", RUN])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--lane", err)


class Hermetic(TempCase):
    # `importlib` is the one loader, used ONLY to reach the kill store's own
    # reader in tools/autoos-agent.py for `record_lane` (AO-JOB-LANE-ID): the
    # lane recovery may plan on must come from the record the worker cannot
    # rewrite, and the reader is not copied here. Lazy, and a store that will not
    # load answers 'no lane' — never a second path implementation, never a spawn.
    ALLOWED_IMPORTS = {"__future__", "argparse", "errno", "hashlib", "io", "json", "os",
                       "re", "shlex", "sys", "tempfile", "time", "unicodedata", "ctypes",
                       "stat", "fcntl", "msvcrt", "importlib",
                       "autoos_blank", "autoos_clients", "autoos_report",
                       "autoos_ready_guards"}
    FORBIDDEN_IMPORTS = {"subprocess", "socket", "http", "urllib", "ftplib", "pty",
                         "signal", "multiprocessing", "pwd", "grp", "asyncio",
                         "threading", "autoos_agent_mcp", "requests"}
    FORBIDDEN_CALLS = {"os.system", "os.popen", "os.execv", "os.execve", "os.spawnl",
                       "os.fork", "os.killpg", "os.setsid", "os.spawn"}

    def setUp(self):
        super().setUp()
        self.source = io.open(str(ROOT / "tools" / "autoos_recovery.py"),
                              encoding="utf-8").read()

    def tree(self):
        return ast.parse(self.source)

    def test_only_harmless_modules_are_imported(self):
        imported = set()
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported - self.ALLOWED_IMPORTS,
                         "unexpected import: %s" % sorted(imported - self.ALLOWED_IMPORTS))
        self.assertFalse(imported & self.FORBIDDEN_IMPORTS)

    def test_fcntl_is_imported_only_off_windows(self):
        """The lane lock needs flock, but the module must stay importable on
        Windows: the import sits inside a platform check, never at top level.
        msvcrt is the mirror-image case — it exists only on Windows, so its own
        import must sit off POSIX."""
        tree = self.tree()

        def platform_check(test):
            for child in ast.walk(test):
                if (isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
                        and (child.value.id, child.attr) in (("os", "name"),
                                                             ("sys", "platform"))):
                    return True
            return False

        guards = [p for p in ast.walk(tree)
                  if isinstance(p, ast.If) and platform_check(p.test)]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and [a.name for a in node.names] in (
                    ["fcntl"], ["msvcrt"]):
                self.assertTrue(any(any(child is node for child in ast.walk(g))
                                    for g in guards),
                                "%s imported outside a platform check (line %d)"
                                % ([a.name for a in node.names][0], node.lineno))

    def test_nothing_shells_out(self):
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                dotted = "%s.%s" % (node.value.id, node.attr)
                self.assertFalse(dotted.startswith(tuple(self.FORBIDDEN_CALLS)), dotted)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("exec", "eval", "compile", "__import__"))

    def test_the_module_never_calls_spawn(self):
        """It hands the L2/L1 a spawn_hint; acting on it is theirs, not its own."""
        funcs = [n.func for n in ast.walk(self.tree()) if isinstance(n, ast.Call)]
        called = {f.id for f in funcs if isinstance(f, ast.Name)}
        attrs = {f.attr for f in funcs if isinstance(f, ast.Attribute)}
        self.assertFalse(({c.lower() for c in called | attrs}
                          & {"spawn", "run_job", "popen", "system", "check_output"}))
        self.assertNotIn("import autoos_agent_mcp", self.source)

    def test_the_state_root_is_the_only_place_it_makes_directories(self):
        made = []
        real = os.makedirs

        def spy(path, *a, **kw):
            made.append(path)
            return real(path, *a, **kw)

        os.makedirs = spy
        try:
            r.record_attempt(LANE, RUN, self.state)
        finally:
            os.makedirs = real
        self.assertEqual(made, [r.recovery_dir(self.state)])

    def test_the_default_probe_never_signals_a_process_it_cannot_see(self):
        for bad in (None, 0, -1, 1.5, "7", True, b"7", [], 2 ** 31, r.PID_MAX + 1):
            self.assertIsNone(r.pid_alive(bad), bad)
        self.assertIs(r.pid_alive(os.getpid()), True)

    def test_every_record_read_refuses_a_non_regular_file(self):
        """The one opener: no read of a record, log or state file follows a
        symlink or blocks on a FIFO."""
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "io" and node.func.attr == "open":
                self.fail("io.open at line %d bypasses _open_regular" % node.lineno)


if __name__ == "__main__":
    unittest.main(verbosity=2)
