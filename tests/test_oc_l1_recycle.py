"""tests/test_oc_l1_recycle.py - AO-OC-CONTEXT-RECYCLE: the token-usage recycle
supervisor of tools/oc_l1_recycle.py (`oc_l1.py recycle`). No network: the
opencode v2 API is the thread-bound fake of tests/_oc_l1_fakes.py."""

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "hooks"))
sys.path.insert(0, str(ROOT / "tests"))

import oc_l1  # noqa: E402
import oc_l1_recycle  # noqa: E402
from _oc_l1_fakes import FAKE_SESSION_ID, PW_ENV, PW_VALUE, FakeServer, make_lane, write_cfg  # noqa: E402

NEW_SESSION_ID = "ses_recycled7"
ENV_NAMES = ("AUTOOS_CTX_HANDOFF", "AUTOOS_CTX_HARD", "XDG_STATE_HOME", PW_ENV)


def assistant(input_tokens, cache_read=0, cache_write=0, output=500, seq=None):
    """An opencode v2 assistant message item (newest first in a listing). `seq` stamps an
    id and a creation time, the way the real records carry them (a bigger seq is newer)."""
    item = {"type": "assistant", "info": {"role": "assistant", "tokens": {
        "input": input_tokens, "output": output, "reasoning": 0,
        "cache": {"read": cache_read, "write": cache_write}}}}
    if seq is not None:
        item["info"].update(id="msg_%06d" % seq, time={"created": 1700000000000 + seq * 1000})
    return item


def prompts(srv):
    return [r for r in srv.requests if r["method"] == "POST" and r["path"].endswith("/prompt")]


def creates(srv):
    return [r for r in srv.requests if r["method"] == "POST" and r["path"] == "/api/session"]


class RecycleBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeServer(PW_VALUE)
        cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="oc_l1_recycle_test_")
        self.td = Path(self._td.name)
        self._env = {k: os.environ.get(k) for k in ENV_NAMES}
        os.environ[PW_ENV] = PW_VALUE
        os.environ["XDG_STATE_HOME"] = str(self.td / "xdg-state")
        os.environ.pop("AUTOOS_CTX_HANDOFF", None)
        os.environ.pop("AUTOOS_CTX_HARD", None)
        self.srv.items = []
        self.srv.session_items = {}
        self.srv.next_session_ids = [NEW_SESSION_ID]
        self.srv.requests = []
        self.lane = make_lane(self.td, self.srv.port)  # raw: the fake's port is outside the lane range
        # the on-disk config must validate (lane port range); the state file carries the real port
        self.cfg = write_cfg(self.td, dict(self.lane, serve_port=47250))
        self.write_state()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()

    def write_state(self, sid=FAKE_SESSION_ID, port=None):
        p = Path(self.lane["state_file"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "name": "l1test", "session_id": sid, "port": port or self.srv.port,
            "pid": 99999, "prompted": True, "canary": {"denied": True}}), encoding="utf-8")

    def state(self):
        return json.loads(Path(self.lane["state_file"]).read_text(encoding="utf-8"))

    def check(self):
        out, err = io.StringIO(), io.StringIO()
        args = SimpleNamespace(name="l1test", config=str(self.cfg))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = oc_l1_recycle.cmd_recycle(self.lane, args)
        text = out.getvalue().strip()
        return rc, json.loads(text.splitlines()[-1]), err.getvalue()

    def fill(self, tokens):
        self.srv.items = [assistant(tokens)]

    def git_dir(self):
        d = Path(self.lane["cwd"]) / ".git"
        d.mkdir(exist_ok=True)
        return d


class ThresholdCrossingTest(RecycleBase):
    def test_below_handoff_is_a_noop(self):
        self.fill(100000)
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "noop"))
        self.assertEqual(res["ctx"], 100000)
        self.assertEqual(prompts(self.srv), [])
        self.assertEqual(creates(self.srv), [])

    def test_handoff_prompts_once_and_a_second_check_is_skipped(self):
        self.fill(280000)
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"], res["level"]), (0, "announced", "handoff"))
        self.assertEqual(len(prompts(self.srv)), 1)
        text = prompts(self.srv)[0]["body"]["text"]
        self.assertIn(self.lane["handoff"], text)
        self.assertIn("stop", text.lower())
        self.assertEqual(prompts(self.srv)[0]["path"], "/api/session/%s/prompt" % FAKE_SESSION_ID)
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "skipped"))
        self.assertEqual(len(prompts(self.srv)), 1, "the handoff prompt is posted ONCE")
        self.assertEqual(creates(self.srv), [])

    def test_session_crossing_both_limits(self):
        self.fill(120000)
        self.assertEqual(self.check()[1]["action"], "noop")
        self.fill(275000)
        self.assertEqual(self.check()[1]["action"], "announced")
        self.assertEqual(self.check()[1]["action"], "skipped")
        self.fill(310000)
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"], res["level"]), (0, "restarted", "hard"))
        self.assertEqual(res["session_id"], NEW_SESSION_ID)
        self.assertEqual(res["previous_session_id"], FAKE_SESSION_ID)
        self.assertEqual(len(creates(self.srv)), 1)
        self.assertEqual(creates(self.srv)[0]["body"]["title"], "l1test")
        first = prompts(self.srv)[-1]
        self.assertEqual(first["path"], "/api/session/%s/prompt" % NEW_SESSION_ID)
        self.assertIn("line-01", first["body"]["text"])  # the handoff head, as `start` posts it
        self.assertNotIn("line-45", first["body"]["text"])
        st = self.state()
        self.assertEqual((st["session_id"], st["prompted"]), (NEW_SESSION_ID, True))
        self.assertEqual(st["recycled_from"], FAKE_SESSION_ID)
        self.assertEqual(st["pid"], 99999)  # the server is the same one
        self.assertEqual(st["canary"], {"denied": True})
        # the next check measures the NEW session: nothing to do, nothing re-sent
        self.srv.session_items[NEW_SESSION_ID] = [assistant(4000)]
        n = len(prompts(self.srv))
        self.assertEqual(self.check()[1]["action"], "noop")
        self.assertEqual(len(prompts(self.srv)), n)

    def test_hard_without_prior_announce_still_restarts_and_says_so(self):
        self.fill(350000)
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "restarted"))
        self.assertIn("no handoff announcement", res["warning"])
        self.assertFalse(res["handoff_fresh"])

    def test_fill_is_input_plus_both_cache_buckets_of_the_last_message(self):
        # 100k + 150k + 25k = 275k; output must not count; an older big message must not count
        self.srv.items = [assistant(100000, 150000, 25000, output=999999, seq=3),
                          {"type": "user"}, assistant(900000, seq=1)]
        rc, res, _ = self.check()
        self.assertEqual(res["ctx"], 275000)
        self.assertEqual(res["action"], "announced")

    def test_handoff_freshness_is_reported_at_restart(self):
        self.fill(280000)
        self.check()
        time.sleep(0.05)
        Path(self.lane["handoff"]).write_text("# fresh handoff\n", encoding="utf-8")
        self.fill(310000)
        rc, res, _ = self.check()
        self.assertTrue(res["handoff_fresh"])
        self.assertIn("fresh handoff", prompts(self.srv)[-1]["body"]["text"])

    def test_the_cli_subcommand_is_wired(self):
        self.fill(280000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = oc_l1.main(["recycle", "--name", "l1test", "--config", str(self.cfg)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["action"], "announced")


class ReclimbTest(RecycleBase):
    def test_a_fill_that_drops_back_re_arms_the_announcement(self):
        self.fill(280000)
        self.assertEqual(self.check()[1]["action"], "announced")
        self.fill(60000)  # a compaction
        self.assertEqual(self.check()[1]["action"], "noop")
        self.fill(285000)
        self.assertEqual(self.check()[1]["action"], "announced")
        self.assertEqual(len(prompts(self.srv)), 2, "the next climb is announced afresh")
        self.fill(60000)
        self.check()
        self.fill(310000)  # hard with the announcement cleared: the warning is back
        res = self.check()[1]
        self.assertEqual(res["action"], "restarted")
        self.assertIn("no handoff announcement", res["warning"])


class StaleSnapshotTest(RecycleBase):
    def test_the_session_is_re_read_once_the_lane_lock_is_held(self):
        self.fill(310000)  # the OLD session is over hard
        self.srv.session_items[NEW_SESSION_ID] = [assistant(4000)]
        real = oc_l1_recycle.lane_lock

        @contextlib.contextmanager
        def racing(lane):
            with real(lane) as got:  # a concurrent check finished its restart before we got the lock
                self.write_state(sid=NEW_SESSION_ID)
                yield got

        old = oc_l1_recycle.lane_lock
        oc_l1_recycle.lane_lock = racing
        try:
            rc, res, _ = self.check()
        finally:
            oc_l1_recycle.lane_lock = old
        self.assertEqual((rc, res["action"], res["session_id"]), (0, "noop", NEW_SESSION_ID))
        self.assertEqual((creates(self.srv), prompts(self.srv)), ([], []))
        self.assertEqual(self.state()["session_id"], NEW_SESSION_ID)


class RestartCooldownTest(RecycleBase):
    def test_a_session_over_hard_straight_after_a_restart_is_not_restarted_again(self):
        self.srv.next_session_ids = [NEW_SESSION_ID, "ses_recycled8"]
        self.fill(310000)
        self.assertEqual(self.check()[1]["action"], "restarted")
        self.srv.session_items[NEW_SESSION_ID] = [assistant(320000)]  # the handoff head alone is huge
        for _ in range(2):
            rc, res, _ = self.check()
            self.assertEqual((rc, res["action"], res["session_id"]), (0, "cooldown", NEW_SESSION_ID))
            self.assertIn("restarted", res["detail"])
        self.assertEqual(len(creates(self.srv)), 1)
        self.assertEqual(self.state()["session_id"], NEW_SESSION_ID)

    def test_the_restart_is_allowed_again_once_the_session_is_old_enough(self):
        self.srv.next_session_ids = [NEW_SESSION_ID, "ses_recycled8"]
        self.fill(310000)
        self.check()
        self.srv.session_items[NEW_SESSION_ID] = [assistant(320000)]
        st = self.state()
        old = time.time() - oc_l1_recycle.RESTART_MIN_AGE_S - 60
        st["recycled_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old))
        Path(self.lane["state_file"]).write_text(json.dumps(st), encoding="utf-8")
        res = self.check()[1]
        self.assertEqual((res["action"], res["session_id"]), ("restarted", "ses_recycled8"))
        self.assertEqual(self.state()["recycles"], 2)

    def test_a_handoff_crossing_during_the_cooldown_is_still_announced(self):
        self.fill(310000)
        self.check()
        self.srv.session_items[NEW_SESSION_ID] = [assistant(280000)]
        self.assertEqual(self.check()[1]["action"], "announced")


class MergeDeferTest(RecycleBase):
    def test_merge_head_defers_the_restart_until_it_clears(self):
        self.fill(310000)
        (self.git_dir() / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (3, "deferred"))
        self.assertIn("deferred: merge in progress", res["detail"])
        self.assertEqual(creates(self.srv), [])
        self.assertEqual(self.state()["session_id"], FAKE_SESSION_ID)
        (self.git_dir() / "MERGE_HEAD").unlink()
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (0, "restarted"))

    def test_worktree_gitdir_file_is_followed(self):
        self.fill(310000)
        real = self.td / "real-gitdir"
        real.mkdir()
        (real / "MERGE_HEAD").write_text("0" * 40, encoding="utf-8")
        (Path(self.lane["cwd"]) / ".git").write_text("gitdir: %s\n" % real, encoding="utf-8")
        self.assertEqual(self.check()[1]["action"], "deferred")

    def test_lane_merge_lock_file_defers(self):
        self.fill(310000)
        lock = Path(self.lane["scratch_dir"]) / oc_l1_recycle.MERGE_LOCK_NAME
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("merging\n", encoding="utf-8")
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (3, "deferred"))
        self.assertEqual(creates(self.srv), [])

    def test_a_stale_merge_lock_does_not_block_forever(self):
        self.fill(310000)
        lock = Path(self.lane["scratch_dir"]) / oc_l1_recycle.MERGE_LOCK_NAME
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("merging\n", encoding="utf-8")
        old = time.time() - oc_l1_recycle.MERGE_LOCK_MAX_AGE_S - 60
        os.utime(lock, (old, old))
        self.assertEqual(self.check()[1]["action"], "restarted")

    def test_the_handoff_announcement_is_not_deferred_by_a_merge(self):
        self.fill(280000)
        (self.git_dir() / "MERGE_HEAD").write_text("0" * 40, encoding="utf-8")
        self.assertEqual(self.check()[1]["action"], "announced")


class FailSafeTest(RecycleBase):
    def assert_nothing_done(self):
        self.assertEqual(prompts(self.srv), [])
        self.assertEqual(creates(self.srv), [])
        self.assertEqual(self.state()["session_id"], FAKE_SESSION_ID)

    def test_no_usage_record_yet_is_waiting_not_a_fault(self):
        for items in ([], [{"type": "user"}], [assistant(0, output=0)],
                      [{"type": "assistant", "info": {"role": "assistant"}}]):
            self.srv.items = items
            rc, res, err = self.check()
            self.assertEqual((rc, res["action"]), (0, "waiting"), items)
            self.assertEqual(err, "")
            self.assert_nothing_done()

    def test_non_numeric_usage_is_an_error_and_no_restart(self):
        self.srv.items = [{"type": "assistant", "info": {"tokens": {
            "input": "lots", "cache": {"read": 1, "write": 1}}}}]
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (6, "error"))
        self.assert_nothing_done()

    def test_a_bad_newest_record_is_not_papered_over_by_an_older_good_one(self):
        self.srv.items = [{"type": "assistant", "info": {
            "id": "msg_000002", "time": {"created": 1700000002000}, "tokens": {"input": True}}},
            assistant(400000, seq=1)]
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (6, "error"))
        self.assert_nothing_done()

    def test_negative_usage_is_an_error(self):
        self.srv.items = [assistant(-5)]
        self.assertEqual(self.check()[0], 6)
        self.assert_nothing_done()

    def test_an_in_flight_zero_record_falls_back_to_the_last_measured_one(self):
        self.srv.items = [assistant(0, output=0), assistant(280000)]
        self.assertEqual(self.check()[1]["ctx"], 280000)

    def test_server_down_is_an_error_not_a_restart(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        self.write_state(port=free)
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (4, "error"))

    def test_no_state_file_is_an_error(self):
        Path(self.lane["state_file"]).unlink()
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (4, "error"))
        self.assertIn("start", res["detail"])

    def test_missing_password_env_is_exit_2_and_names_the_variable_only(self):
        os.environ.pop(PW_ENV, None)
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (2, "error"))
        self.assertIn(PW_ENV, res["detail"])
        self.assertNotIn(PW_VALUE, json.dumps(res))

    def test_missing_handoff_file_blocks_the_restart_before_a_session_is_made(self):
        self.fill(310000)
        Path(self.lane["handoff"]).unlink()
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (4, "error"))
        self.assertIn("handoff", res["detail"])
        self.assertEqual(creates(self.srv), [])
        self.assertEqual(self.state()["session_id"], FAKE_SESSION_ID)

    def test_bad_threshold_environment_is_a_config_error(self):
        self.fill(310000)
        os.environ["AUTOOS_CTX_HANDOFF"] = "400000"  # >= the default hard
        rc, res, _ = self.check()
        self.assertEqual((rc, res["action"]), (2, "error"))
        self.assert_nothing_done()


if __name__ == "__main__":
    unittest.main()
