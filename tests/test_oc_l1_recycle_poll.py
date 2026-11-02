"""tests/test_oc_l1_recycle_poll.py - AO-OC-CONTEXT-RECYCLE: a started lane gets measured
(tools/oc_l1_recycle_poll.py): the recycle command `start` hands out, the check each supervisor poll
runs, the lock that keeps checks from overlapping, the repost of a restart's lost first prompt,
the L2 restart prompt and the committed unit / timer / task examples."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "hooks"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l1_recycle  # noqa: E402
import oc_l1_recycle_poll as poll  # noqa: E402
import oc_l1_serve  # noqa: E402
import oc_l2  # noqa: E402
from _oc_l1_fakes import PW_ENV, PW_VALUE  # noqa: E402
from test_oc_l1_recycle import NEW_SESSION_ID, RecycleBase, creates, prompts  # noqa: E402

CFG_DIR = ROOT / "configuration" / "oc-l1"


class LockTest(RecycleBase):
    def test_a_held_lock_makes_the_second_check_busy_and_it_acts_on_nothing(self):
        self.fill(310000)
        lock = Path(self.lane["scratch_dir"]) / poll.LOCK_NAME
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("x", encoding="utf-8")
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "busy"))
        self.assertEqual((prompts(self.srv), creates(self.srv)), ([], []))
        self.assertTrue(lock.exists(), "a lock this check does not own is not removed")

    def test_the_lock_is_released_after_every_outcome(self):
        lock = Path(self.lane["scratch_dir"]) / poll.LOCK_NAME
        for tokens in (1000, 280000, 310000):
            self.fill(tokens)
            self.assertEqual(self.check()[0], 0)
            self.assertFalse(lock.exists(), tokens)
        self.srv.items = [{"type": "assistant", "info": {"tokens": {"input": "x"}}}]
        self.assertEqual(self.check()[0], 6)
        self.assertFalse(lock.exists())

    def test_the_lock_is_held_while_the_check_runs(self):
        lock = Path(self.lane["scratch_dir"]) / poll.LOCK_NAME
        seen = []
        real = oc_l1_recycle._check

        def spy(*a, **k):
            seen.append(lock.exists())
            return real(*a, **k)

        self.fill(1000)
        with mock.patch.object(oc_l1_recycle, "_check", spy):
            self.check()
        self.assertEqual(seen, [True])

    def test_a_stale_lock_from_a_crashed_check_is_replaced(self):
        self.fill(280000)
        lock = Path(self.lane["scratch_dir"]) / poll.LOCK_NAME
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("x", encoding="utf-8")
        old = time.time() - poll.LOCK_STALE_S - 60
        os.utime(lock, (old, old))
        self.assertEqual(self.check()[1]["action"], "announced")


class RepostTest(RecycleBase):
    def restart_with_failing_post(self, failures):
        calls = []
        real = oc_l1_serve._post_first_prompt

        def flaky(port, pw, sid, lane):
            calls.append(sid)
            if len(calls) <= failures:
                raise oc_l1.LaneError("first prompt failed: HTTP 503")
            return real(port, pw, sid, lane)

        self.fill(310000)
        with mock.patch.object(oc_l1_serve, "_post_first_prompt", flaky):
            return self.check(), calls

    def test_one_transient_failure_is_retried_inside_the_restart(self):
        (rc, res, _), calls = self.restart_with_failing_post(1)
        self.assertEqual((rc, res["action"]), (0, "restarted"))
        self.assertEqual(calls, [NEW_SESSION_ID] * 2)
        self.assertTrue(self.state()["prompted"])

    def test_a_session_left_without_its_prompt_is_reposted_by_the_next_check(self):
        (rc, res, _), calls = self.restart_with_failing_post(2)
        self.assertEqual((rc, res["action"]), (4, "error"))
        st = self.state()
        self.assertEqual((st["session_id"], st["prompted"]), (NEW_SESSION_ID, False))
        self.assertEqual(prompts(self.srv), [])
        rc, res, _ = self.check()  # the new session is empty: re-posted, not a fault
        self.assertEqual((rc, res["action"]), (0, "reposted"))
        first = prompts(self.srv)[-1]
        self.assertEqual(first["path"], "/api/session/%s/prompt" % NEW_SESSION_ID)
        self.assertIn("line-01", first["body"]["text"])
        self.assertTrue(self.state()["prompted"])
        self.srv.session_items[NEW_SESSION_ID] = []  # the new session has not answered yet
        self.assertEqual(self.check()[1]["action"], "waiting")
        self.assertEqual(len(creates(self.srv)), 1, "no second session is created")

    def test_a_start_session_that_was_never_prompted_is_not_posted_to(self):
        p = Path(self.lane["state_file"])
        st = json.loads(p.read_text(encoding="utf-8"))
        st["prompted"] = False  # `start` left it idle (canary not denied): none of our business
        p.write_text(json.dumps(st), encoding="utf-8")
        self.srv.items = []
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "waiting"))
        self.assertEqual(prompts(self.srv), [])


class L2RestartTest(RecycleBase):
    def l2_lane(self, layer):
        brief = self.td / "brief.md"
        brief.write_text("# phase brief\nBRIEF-LINE-01\n", encoding="utf-8")
        return dict(self.lane, agent_layer=layer, handoff=str(brief), serve_port=self.srv.port)

    def test_an_l2_lane_restarts_from_its_brief(self):
        self.fill(310000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = oc_l1_recycle.cmd_recycle(self.l2_lane("L2"), SimpleNamespace(name="l1test", config=None))
        res = json.loads(out.getvalue())
        self.assertEqual((rc, res["action"]), (0, "restarted"))
        self.assertIn("BRIEF-LINE-01", prompts(self.srv)[-1]["body"]["text"])

    def test_only_the_l2_marker_gets_the_l2_report_announcement(self):
        for layer, l2 in (("L2", True), ("L3", False), (None, False)):
            text = oc_l1_recycle.announce_text(self.l2_lane(layer), 280000, 270000, 300000)
            self.assertEqual("l2_report" in text, l2, layer)
            self.assertEqual(str(self.td / "brief.md") in text, True, layer)


class StartHintTest(unittest.TestCase):
    def test_recycle_command_names_the_one_check_and_the_config(self):
        cmd = poll.recycle_command("lane-a", "/tmp/my cfg/oc-l1.json")
        for part in ("oc_l1.py", "recycle", "--name", "lane-a", "--config", "my cfg"):
            self.assertIn(part, cmd)

    def test_split_hint_separates_the_hint_from_the_launcher_output(self):
        out, cmd = poll.split_hint("started x\n%sRUN IT\n" % poll.HINT_PREFIX)
        self.assertEqual((out, cmd), ("started x", "RUN IT"))
        self.assertEqual(poll.split_hint("only this"), ("only this", None))

    def run_main(self, rc):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(oc_l1, "load_config", return_value={"l1test": {}}), \
                mock.patch.object(oc_l1, "validate_lane", return_value={"name": "l1test"}), \
                mock.patch.object(oc_l1_serve, "cmd_start", return_value=rc), \
                contextlib.redirect_stdout(buf):
            code = oc_l1.main(["start", "--name", "l1test", "--config", str(Path(td) / "c.json")])
        return code, buf.getvalue()

    def test_a_successful_start_prints_the_recycle_command_last(self):
        code, text = self.run_main(0)
        self.assertEqual(code, 0)
        last = text.strip().splitlines()[-1]
        self.assertTrue(last.startswith(poll.HINT_PREFIX), last)
        for part in ("recycle", "--name", "l1test", "c.json"):
            self.assertIn(part, last)

    def test_a_failed_start_prints_no_hint(self):
        self.assertEqual(self.run_main(5), (5, ""))


class L2StartHintTest(unittest.TestCase):
    def test_cmd_start_returns_the_recycle_command_and_keeps_the_launcher_line(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            proj = td / "proj"
            proj.mkdir()
            brief = td / "brief.md"
            brief.write_text("# brief\n", encoding="utf-8")
            env = {PW_ENV: PW_VALUE, oc_l2.ENV_STATE_DIR: str(td / "state")}
            fake = (0, "started l2 session s port 47250 pid 1\n%sCMD-TO-RUN\n" % poll.HINT_PREFIX)
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(oc_l2, "_already_running", return_value=None), \
                    mock.patch.object(oc_l2, "run_oc_l1", return_value=fake):
                result, rc = oc_l2.cmd_start(proj, "p1", brief, l1_inbox=td / "inbox.md",
                                             opencode_bin=sys.executable, password_env=PW_ENV,
                                             port=47250)
        self.assertEqual(rc, 0)
        self.assertEqual(result["recycle_command"], "CMD-TO-RUN")
        self.assertEqual(result["launcher_output"], ["started l2 session s port 47250 pid 1"])


class PollWiringTest(unittest.TestCase):
    """oc_l2 status / resume / inbox run ONE check per poll; the answer carries it."""

    LANE = "l2-p-abc-x"
    ARGV = {"status": ["status", "--lane", LANE], "resume": ["resume", "--lane", LANE],
            "inbox": ["inbox", "--lane", LANE, "--text", "hi"], "stop": ["stop", "--lane", LANE]}

    def main_with(self, sub, result, rc=0, runner_out=None):
        calls = []

        def run(cmd, name, cfg):
            calls.append((cmd, name, cfg))
            return runner_out or (0, json.dumps({"lane": name, "action": "noop", "ctx": 5}))

        value = (result, rc) if sub == "status" else result
        buf = io.StringIO()
        with mock.patch.object(oc_l2, "cmd_" + sub, return_value=value), \
                mock.patch.object(oc_l2, "run_oc_l1", run), contextlib.redirect_stdout(buf):
            code = oc_l2.main(self.ARGV[sub])
        return calls, json.loads(buf.getvalue()), code

    def test_each_poll_path_runs_one_check_and_reports_it(self):
        for sub, result in (("status", {"verdict": "live"}), ("resume", {"resumed": True}),
                            ("inbox", {"nudged": True})):
            calls, out, _ = self.main_with(sub, result)
            self.assertEqual([c[:2] for c in calls], [("recycle", self.LANE)], sub)
            self.assertEqual(calls[0][2], oc_l2.config_path(self.LANE))
            self.assertEqual((out["recycle"]["action"], out["recycle"]["ctx"]), ("noop", 5), sub)

    def test_a_poll_that_found_no_session_runs_no_check(self):
        for sub, result in (("status", {"verdict": "dead"}), ("status", {"verdict": "absent"}),
                            ("resume", {"restarted": True}), ("resume", {"restart_refused": True}),
                            ("inbox", {"refused": True})):
            calls, out, _ = self.main_with(sub, result)
            self.assertEqual(calls, [], (sub, result))
            self.assertNotIn("recycle", out)

    def test_the_summary_survives_stderr_text_after_the_json(self):
        merged = (json.dumps({"action": "error", "detail": "server unreachable: x"})
                  + "\noc_l1: recycle error: server unreachable: x\n")
        calls, out, _ = self.main_with("status", {"verdict": "live"}, runner_out=(4, merged))
        self.assertEqual((out["recycle"]["exit_code"], out["recycle"]["detail"]),
                         (4, "server unreachable: x"))

    def test_stop_runs_no_check(self):
        self.assertEqual(self.main_with("stop", {"stopped": True})[0], [])

    def test_a_failing_check_does_not_change_the_polls_exit_code(self):
        calls, out, code = self.main_with("status", {"verdict": "live"}, rc=0, runner_out=(6, "garbage"))
        self.assertEqual((out["recycle"]["exit_code"], out["recycle"]["action"]), (6, "error"))
        self.assertEqual(code, 0)

    def test_a_check_that_raises_does_not_change_the_polls_answer(self):
        for exc in (oc_l2.L2Error("no config"), oc_l1.LaneError("bad lane"),
                    subprocess.TimeoutExpired("oc_l1.py", 30), OSError("spawn failed")):
            def run(cmd, name, cfg, exc=exc):
                raise exc
            buf = io.StringIO()
            with mock.patch.object(oc_l2, "cmd_status", return_value=({"verdict": "live"}, 0)),                     mock.patch.object(oc_l2, "run_oc_l1", run), contextlib.redirect_stdout(buf):
                code = oc_l2.main(self.ARGV["status"])
            out = json.loads(buf.getvalue())
            self.assertEqual((code, out["verdict"]), (0, "live"), exc)
            self.assertEqual((out["recycle"]["exit_code"], out["recycle"]["action"]), (4, "error"), exc)
            self.assertEqual(out["recycle"]["detail"], type(exc).__name__)

    def test_a_config_path_that_raises_does_not_change_the_polls_answer(self):
        buf = io.StringIO()
        with mock.patch.object(oc_l2, "cmd_status", return_value=({"verdict": "live"}, 0)),                 mock.patch.object(oc_l2, "config_path", side_effect=oc_l2.L2Error("no state root")),                 mock.patch.object(oc_l2, "run_oc_l1", return_value=(0, "{}")),                 contextlib.redirect_stdout(buf):
            code = oc_l2.main(self.ARGV["status"])
        out = json.loads(buf.getvalue())
        self.assertEqual((code, out["verdict"]), (0, "live"))
        self.assertEqual((out["recycle"]["exit_code"], out["recycle"]["detail"]), (4, "L2Error"))


class McpWiringTest(unittest.TestCase):
    def setUp(self):
        import autoos_agent_mcp as mcp
        self.mcp = mcp
        self.calls = []

    def call(self, fn, outputs, fence=None):
        def run(argv, **kw):
            self.calls.append(argv)
            rc, out = outputs[len(self.calls) - 1]
            return subprocess.CompletedProcess(argv, rc, out, "")

        with mock.patch.object(self.mcp.subprocess, "run", run), \
                mock.patch.object(self.mcp, "lane_control_fence", return_value=fence):
            return fn("lane-a")

    def test_oc_status_runs_one_recycle_check_for_a_live_lane(self):
        rec = json.dumps({"action": "announced", "ctx": 280000, "level": "handoff"})
        out = self.call(self.mcp.oc_status, [(0, "live\n"), (0, rec + "\n")])
        self.assertEqual(out["verdict"], "live")
        self.assertEqual(out["recycle"]["action"], "announced")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][2:], ["recycle", "--name", "lane-a"])

    def test_the_poll_child_shares_the_schedulers_ctx_guard_state_dir_and_thresholds(self):
        names = ("XDG_STATE_HOME", "AUTOOS_CTX_HANDOFF", "AUTOOS_CTX_HARD")
        env = {k: "/custom/%s" % k for k in names}
        with mock.patch.dict(os.environ, env):
            child = self.mcp._oc_l1_env()
        for k in names:
            self.assertEqual(child.get(k), env[k], k)

    def test_the_windows_system_names_reach_the_launcher(self):
        with mock.patch.dict(os.environ, {"SystemDrive": "C:"}):
            child = self.mcp._oc_l1_env()
        self.assertEqual(child.get("SystemDrive"), "C:")

    def test_oc_status_runs_no_check_for_a_dead_lane_or_inside_a_marked_lane(self):
        out = self.call(self.mcp.oc_status, [(2, "dead\n")])
        self.assertEqual((len(self.calls), "recycle" in out), (1, False))
        self.calls.clear()
        out = self.call(self.mcp.oc_status, [(0, "live\n")], fence={"refused": True})
        self.assertEqual((len(self.calls), "recycle" in out), (1, False))

    def test_oc_start_returns_the_recycle_command(self):
        text = "started lane-a session s port 47250 pid 1\n%sRUN-THIS\n" % poll.HINT_PREFIX
        out = self.call(self.mcp.oc_start, [(0, text)])
        self.assertEqual(out["recycle_command"], "RUN-THIS")
        self.assertTrue(out["last_line"].startswith("started lane-a"))


class ExamplesTest(unittest.TestCase):
    def read(self, name):
        return (CFG_DIR / name).read_text(encoding="utf-8")

    def test_the_systemd_service_and_timer_sit_next_to_the_start_unit(self):
        self.assertTrue((CFG_DIR / "autoos-oc-l1.service").is_file())
        svc, tim = self.read("autoos-oc-l1-recycle.service"), self.read("autoos-oc-l1-recycle.timer")
        self.assertIn("oc_l1.py recycle --name", svc)
        self.assertIn("Type=oneshot", svc)
        self.assertNotIn("RemainAfterExit", svc.split("[Service]")[1])
        self.assertIn("OnUnitActiveSec=", tim)
        self.assertIn("Unit=autoos-oc-l1-recycle.service", tim)
        self.assertIn("[Install]", tim)

    def test_the_windows_task_and_the_exit_code_list(self):
        tail = self.read("windows-task.md").split("Token recycle")[1]
        self.assertIn("oc_l1.py recycle --name", tail)
        self.assertIn("Register-ScheduledTask", tail)
        self.assertIn("if %RC%==3 exit /b 0", tail)  # deferred (3) is a success run, as SuccessExitStatus=3
        self.assertIn("-RepetitionInterval", tail)
        self.assertIn("exit 0 ok, 2 config", tail)
        # with the wrapper the run shows 0x0; 0x3 only if the mapping is removed
        flat = " ".join(tail.split())
        self.assertIn('Task Scheduler\'s "Last Run Result" shows 0x0 for a merge window; '
                      "0x3 appears only if that mapping is removed", flat)
        self.assertNotIn('"Last Run Result" 0x3 for every', flat)

    def test_the_l2_config_path_is_documented(self):
        self.assertIn("AUTOOS_OCL2_STATE_DIR", self.read("autoos-oc-l1-recycle.service"))
        self.assertIn("AUTOOS_OCL2_STATE_DIR", self.read("windows-task.md"))


if __name__ == "__main__":
    unittest.main()
