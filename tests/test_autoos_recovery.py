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
import hashlib
import io
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_recovery as r  # noqa: E402


NOW = 1800000000.0                     # a pinned epoch second, never a wall clock
RUN = "20261009-182026-task-abc123"
LEG1 = "20261009-182026-leg-one"
LEG2 = "20261009-182026-leg-two"
TASK = "Implement the widget and run the suite.\nEnd with your report.\n"
SB = "/srv/lanes/wg-p4c/worktrees/wt-1"
BRANCH = "agent/20261009-182026-task-abc123"
LANE = "lane/p4c"

SKIP = object()                        # "do not create this file"
FOOTER1 = ("CONTINUE FROM CURRENT DIFF (recovery attempt 1/2): your previous run ended "
           "without a REPORT. The sandbox at %s holds your earlier work as commits "
           "(git log); do NOT restart; finish what is missing, run every required "
           "check, and end with the REPORT." % SB)


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
    job = job_json if isinstance(job_json, str) else {
        "run_id": run_id, "task": task, "cwd": "/repo", "started": mtime,
        "pid": pid, "request": {}, "argv": [], "route": {}}
    write_text(os.path.join(run_dir, "job.json"),
               job if isinstance(job, str) else json.dumps(job, sort_keys=True), mtime)
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


def sandbox_lines(path=SB, branch=BRANCH):
    """The closing lines a real run prints: `sandbox:` on the unquoted path, the
    review/take-it pair with the path shlex-quoted as the runner quotes it."""
    return ["sandbox: %s (branch %s)\n" % (path, branch),
            "review:  git -C %s diff\n" % shlex.quote(path),
            "take it: git fetch %s %s   (then: git cherry-pick abc123..FETCH_HEAD)\n"
            % (shlex.quote(path), branch)]


def report_body():
    return "\n".join(["worker thinking", "all checks green",
                      "REPORT %s · OK · green" % RUN]) + "\n"


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
        info = classify(make_record(self.state, exit_json={"rc": 0},
                                    output=sandbox_lines() + [report_body()]),
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
            root = make_record(self.other_state(), exit_json=exit_json, mtime=mtime,
                               output=sandbox_lines() + [report_body()])
            self.assertIn(classify(root, probe=probe)["state"], r.STATES)


class SandboxParsing(TempCase):
    def classified(self, output, probe=dead, state=None):
        return classify(make_record(state or self.state, output=output), probe=probe)

    def test_the_sandbox_line_alone_names_the_worktree(self):
        """`sandbox:` is printed when the clone is stood up, so a writer killed
        before it can print anything still names its kept worktree."""
        info = self.classified(["sandbox: %s (branch %s)\n" % (SB, BRANCH), "thinking\n"])
        self.assertEqual((info["sandbox"], info["branch"]), (SB, BRANCH))

    def test_the_review_and_take_it_pair(self):
        info = self.classified(sandbox_lines())
        self.assertEqual((info["sandbox"], info["branch"]), (SB, BRANCH))

    def test_a_quoted_path_with_a_space(self):
        path = "/keep/my worktree"
        info = self.classified(sandbox_lines(path=path, branch="agent/a"))
        self.assertEqual((info["sandbox"], info["branch"]), (path, "agent/a"))

    def test_a_take_it_line_alone_carries_path_and_branch(self):
        info = self.classified(["take it: git fetch %s %s\n" % (shlex.quote(SB), BRANCH)])
        self.assertEqual((info["sandbox"], info["branch"]), (SB, BRANCH))

    def test_a_relative_path_is_refused(self):
        self.assertIsNone(self.classified(sandbox_lines(path="relative/wt"))["sandbox"])

    def test_parent_traversal_is_refused(self):
        for path in ("/keep/../../etc", "/keep/../x", "/.."):
            info = self.classified(sandbox_lines(path=path), state=self.other_state())
            self.assertIsNone(info["sandbox"], path)

    def test_a_control_character_is_refused(self):
        self.assertIsNone(self.classified(sandbox_lines(path="/keep/a\x01b"))["sandbox"])

    def test_an_unexpanded_home_is_refused(self):
        self.assertIsNone(self.classified(sandbox_lines(path="~/worktrees/wt"))["sandbox"])

    def test_quoting_that_does_not_parse_is_refused(self):
        self.assertIsNone(self.classified(["review:  git -C /keep/unclosed' diff\n"])
                          ["sandbox"])

    def test_a_review_line_that_is_not_a_diff_is_ignored(self):
        self.assertIsNone(self.classified(
            ["review:  git -C %s status\n" % shlex.quote(SB)])["sandbox"])

    def test_a_branch_that_cannot_be_pasted_is_none_and_the_worktree_kept(self):
        info = self.classified(sandbox_lines()[:-1] +
                               ["take it: git fetch %s 'not a name'\n" % shlex.quote(SB)])
        self.assertEqual((info["sandbox"], info["branch"]), (SB, None))

    def test_an_overlong_path_is_refused(self):
        self.assertIsNone(self.classified(
            sandbox_lines(path="/" + "d" * r.PATH_MAX_CHARS))["sandbox"])

    def test_the_last_worktree_line_wins(self):
        info = self.classified(sandbox_lines(path="/keep/first", branch="a")
                               + sandbox_lines(path="/keep/second", branch="b"))
        self.assertEqual((info["sandbox"], info["branch"]), ("/keep/second", "b"))

    def test_only_the_tail_is_read(self):
        """A worktree line older than the read window is simply not there."""
        filler = ["f" * 1023 + "\n"] * ((r.TAIL_BYTES // 1024) + 4)
        root = make_record(self.state,
                           output=["sandbox: %s (branch %s)\n" % (SB, BRANCH)] + filler)
        self.assertGreater(os.path.getsize(os.path.join(root, "output.log")), r.TAIL_BYTES)
        self.assertIsNone(classify(root, probe=dead)["sandbox"])

    def test_a_line_inside_the_tail_is_read(self):
        root = make_record(self.state, output=["f" * 1023 + "\n"] * 8
                           + ["sandbox: %s (branch %s)\n" % (SB, BRANCH)])
        self.assertEqual(classify(root, probe=dead)["sandbox"], SB)

    def test_no_output_log_at_all(self):
        info = classify(make_record(self.state, output=SKIP), probe=dead)
        self.assertEqual((info["sandbox"], info["branch"]), (None, None))


class ReportDetection(TempCase):
    def test_a_protocol_report_block_counts(self):
        self.assertTrue(self.classified([report_body()])["has_report"])

    def test_a_markdown_heading_counts(self):
        self.assertTrue(self.classified(["# REPORT\n", "status: done\n"])["has_report"])

    def test_prose_about_reporting_does_not_count(self):
        for line in ("I will report when done\n", "REPORTED: nothing\n",
                     "see REPORT.md for detail\n", "the contract says report\n"):
            st = self.other_state()
            self.assertFalse(self.classified([line], state=st)["has_report"], line)

    def test_an_echoed_brief_is_not_a_report(self):
        task = "do the work\nREPORT: the field list the contract prints\n"
        self.assertFalse(classify(make_record(self.state, task=task, output=[task]),
                                  probe=dead)["has_report"])
        # The same record with a heading of its own does count.
        self.assertTrue(classify(make_record(self.other_state(), task=task,
                                             output=[task, "\n**REPORT**\n"]),
                                 probe=dead)["has_report"])

    def test_empty_output_has_no_report(self):
        self.assertFalse(self.classified("")["has_report"])

    def classified(self, output, state=None):
        return classify(make_record(state or self.state, output=output,
                                    exit_json={"rc": 0}), probe=dead)


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
                         TASK.rstrip("\n") + "\n\n" + FOOTER1 + "\n")

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
        for bad in ("relative/wt", "/keep/../x", "/keep/a\x01b", "~/wt", 7, ["x"]):
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
        self.assertIn(FOOTER1, got)
        self.assertLess(len(got.encode("utf-8")), len(big.encode("utf-8")))
        self.assertLess(len(got.split("\n")[0]), len(big))

    def test_the_cap_never_splits_a_character(self):
        got = r.continuation_task("\u00e9" * r.TASK_MAX_BYTES, 1, SB)  # 2 bytes each
        got.encode("utf-8").decode("utf-8")
        self.assertIn(FOOTER1, got)

    def test_a_path_with_a_space_survives_verbatim(self):
        self.assertIn("The sandbox at /keep/my worktree holds",
                      r.continuation_task(TASK, 1, "/keep/my worktree"))


class LaneKeys(TempCase):
    def test_the_default_key_is_the_hash_of_the_task_head(self):
        self.assertEqual(r.lane_key_for_task(TASK),
                         hashlib.sha256(TASK.encode("utf-8")).hexdigest())
        head = "y" * (r.KEY_TASK_CHARS + 500)
        self.assertEqual(r.lane_key_for_task(head),
                         hashlib.sha256(head[:r.KEY_TASK_CHARS].encode("utf-8")).hexdigest())

    def test_tasks_that_differ_only_past_the_head_share_a_lane(self):
        a = "x" * r.KEY_TASK_CHARS + "\nfirst tail"
        b = "x" * r.KEY_TASK_CHARS + "\nsecond tail"
        self.assertEqual(r.lane_key_for_task(a), r.lane_key_for_task(b))
        self.assertNotEqual(r.lane_key_for_task("x" * r.KEY_TASK_CHARS),
                            r.lane_key_for_task("y" * r.KEY_TASK_CHARS))

    def test_the_task_must_be_text(self):
        for bad in (None, 7, b"x", ["x"]):
            with self.assertRaises(r.RecoveryError):
                r.lane_key_for_task(bad)

    def test_accepted_lane_key_shapes(self):
        for key in ("lane-a", "a/b/c", "wg.p4c_1", "x" * r.LANE_KEY_MAX_CHARS, "x"):
            self.assertEqual(r._check_key(key), key)

    def test_refused_lane_key_shapes(self):
        for key in ("", "x" * (r.LANE_KEY_MAX_CHARS + 1), "bad key", "bad\nkey",
                    "bad\x00key", None, 7, b"lane", "caf\u00e9", "bad;key"):
            with self.assertRaises(r.RecoveryError):
                r._check_key(key)

    def test_a_key_never_names_a_path_outside_the_recovery_dir(self):
        """A key may carry dots and slashes; only its digest becomes a file name."""
        for key in ("../escape", "..", "a/../../b", "./x"):
            path = r.path_of(key, self.state)
            self.assertEqual(os.path.dirname(path), r.recovery_dir(self.state))
            self.assertRegex(os.path.basename(path), r"^[0-9a-f]{64}\.json$")


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

    def test_no_temp_file_survives_a_write(self):
        r.record_attempt(LANE, RUN, self.state)
        self.assertEqual(sorted(os.listdir(r.recovery_dir(self.state))),
                         [os.path.basename(r.path_of(LANE, self.state))])

    def test_a_stale_temp_file_is_not_read_as_state(self):
        os.makedirs(r.recovery_dir(self.state))
        write_text(os.path.join(r.recovery_dir(self.state), ".recovery-junk.tmp"), "{")
        self.assertEqual(r.read_attempts(LANE, self.state)["attempts"], 0)

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

    @unittest.skipIf(os.name == "nt", "the mode bits mean nothing on Windows")
    def test_the_budget_file_is_not_world_readable(self):
        r.record_attempt(LANE, RUN, self.state)
        mode = stat.S_IMODE(os.stat(r.path_of(LANE, self.state)).st_mode)
        dmode = stat.S_IMODE(os.stat(r.recovery_dir(self.state)).st_mode)
        self.assertEqual((mode, dmode), (0o600, 0o700))

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


class RunIdsAndDirs(TempCase):
    def test_accepted_ids(self):
        for run in (RUN, "a", "a" * r.RUN_ID_MAX_CHARS, "task-1_2.3"):
            self.assertEqual(r._check_run_id(run), run)

    def test_refused_ids_never_traverse(self):
        for bad in ("a/b", "..", "../x", "2026..1", "", ".", ".hidden", "-",
                    "a" * (r.RUN_ID_MAX_CHARS + 1), "run name", "run\nname",
                    None, 7, b"run", "\u00e9"):
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


class Plan(TempCase):
    KEYS = {"run_id", "lane_key", "action", "state", "attempts", "rc", "has_report",
            "sandbox", "branch", "last_output_age", "state_corrupt", "continue_task",
            "spawn_hint"}

    def plan(self, state=None, **over):
        kw = {"state": state or self.state, "now": NOW, "pid_probe": dead}
        kw.update(over)
        return r.plan(RUN, **kw)

    def test_a_dead_writer_gets_one_continuation_leg(self):
        make_record(self.state, output=sandbox_lines())
        p = self.plan(lane_key=LANE)
        self.assertEqual(set(p), self.KEYS)
        self.assertEqual((p["action"], p["state"], p["attempts"]), ("rerun", "died", 0))
        self.assertEqual((p["sandbox"], p["branch"]), (SB, BRANCH))
        self.assertEqual(p["continue_task"], r.continuation_task(TASK, 1, SB))
        self.assertEqual(p["spawn_hint"]["cwd"], SB)
        self.assertIn("record_attempt", p["spawn_hint"]["note"])
        self.assertFalse(p["state_corrupt"])
        self.assertEqual(p["lane_key"], LANE)

    def test_the_default_lane_is_the_runs_own_task_hash(self):
        make_record(self.state)
        self.assertEqual(self.plan()["lane_key"], r.lane_key_for_task(TASK))

    def test_the_budget_is_shared_by_every_writer_on_the_lane(self):
        make_record(self.state, output=sandbox_lines())
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
        make_record(st, exit_json={"rc": 0}, output=sandbox_lines() + [report_body()])
        p = self.plan(state=st, pid_probe=alive)
        self.assertEqual((p["state"], p["action"], p["continue_task"]),
                         ("completed", "none", None))
        self.assertEqual(p["sandbox"], SB)

    def test_an_unknown_run_escalates(self):
        st = self.other_state()
        make_record(st, job_json="{oops")
        p = self.plan(state=st)
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
        make_record(self.state, output=sandbox_lines())
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
            r.plan("../etc", state=self.state)

    def test_a_missing_run_is_refused(self):
        with self.assertRaises(r.RecoveryError):
            self.plan()

    def test_planning_writes_nothing(self):
        """Read-only: no attempt file, no state dir, and of course no spawn."""
        make_record(self.state, output=sandbox_lines())
        self.plan(lane_key=LANE)
        self.plan(lane_key=LANE)
        self.assertEqual(os.listdir(self.state), ["agents"])
        self.assertEqual(r.read_attempts(LANE, self.state)["attempts"], 0)


class Cli(TempCase):
    def cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = r.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_rerun_exits_three(self):
        make_record(self.state, exit_json={"rc": 1}, output=sandbox_lines())
        code, text, _ = self.cli(["plan", RUN, "--lane", LANE])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(text)["action"], "rerun")
        self.assertEqual(json.loads(text)["lane_key"], LANE)

    def test_wait_and_none_exit_zero(self):
        make_record(self.state, pid=os.getpid())            # this pid is alive
        code, text, _ = self.cli(["plan", RUN])
        self.assertEqual((code, json.loads(text)["state"], json.loads(text)["action"]),
                         (0, "running", "wait"))
        st = self.other_state()
        os.environ["AUTOOS_STATE_DIR"] = st
        make_record(st, exit_json={"rc": 0}, output=sandbox_lines() + [report_body()])
        code, text, _ = self.cli(["plan", RUN])
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
        make_record(self.state, pid=os.getpid(), mtime=NOW - 60, output=sandbox_lines())
        self.assertEqual(self.cli(["plan", RUN, "--stall-secs", "30"])[0], 3)
        code, text, _ = self.cli(["plan", RUN, "--stall-secs", "120"])
        self.assertEqual((code, json.loads(text)["action"]), (0, "wait"))

    def test_bad_input_exits_two(self):
        make_record(self.state)
        for argv in (["plan", "../etc"], ["plan", "a" * 200],
                     ["plan", RUN, "--lane", "bad key"],
                     ["record", RUN, "--lane", "bad key"],
                     ["plan", RUN, "--stall-secs", "0"]):
            code, _, err = self.cli(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("autoos_recovery:", err)

    def test_usage_errors_exit_two(self):
        for argv in (["record", RUN], ["plan"], []):
            with self.assertRaises(SystemExit) as caught:
                self.cli(argv)
            self.assertEqual(caught.exception.code, 2)


class Hermetic(TempCase):
    ALLOWED_IMPORTS = {"__future__", "argparse", "errno", "hashlib", "io", "json", "os",
                       "re", "shlex", "sys", "tempfile", "time", "unicodedata", "ctypes",
                       "autoos_clients", "autoos_report"}
    FORBIDDEN_IMPORTS = {"subprocess", "socket", "http", "urllib", "ftplib", "pty",
                         "signal", "multiprocessing", "fcntl", "pwd", "grp", "asyncio",
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
        for bad in (None, 0, -1, 1.5, "7", True, b"7", []):
            self.assertIsNone(r.pid_alive(bad), bad)
        self.assertIs(r.pid_alive(os.getpid()), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
