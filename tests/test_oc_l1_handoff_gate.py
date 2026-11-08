"""Hermetic checker contracts; recycle uses the existing fake transport."""
import importlib
import json
import contextlib
from pathlib import Path
from unittest.mock import patch

from test_oc_l1_recycle import RecycleBase, assistant, creates, prompts
from _oc_l1_fakes import PW_VALUE
import oc_l1
import oc_l1_recycle


class GateTest(RecycleBase):
    def setUp(self):
        super().setUp()
        self.checker = self.td / "checker"
        self.lane["handoff_check"] = str(self.checker)
        self.card = Path(self.lane["handoff"]).with_suffix(".card.md")
        self.card.write_text("card")
        self.stub("raise SystemExit(0)")
        self.srv.items = [assistant(310000)]

    def stub(self, body):
        self.checker.write_text("#!/usr/bin/env python3\n" + body + "\n")
        self.checker.chmod(0o700)

    def test_pass(self):
        self.assertEqual(self.check()[1]["action"], "restarted")
        self.assertEqual(len(creates(self.srv)), 1)

    def test_hard_level_runs_the_gate_before_restart(self):
        for body in ("raise SystemExit(1)", None, "card"):
            with self.subTest(body=body):
                self.write_state()
                self.srv.requests = []
                self.fill(310000)
                if body is None:
                    self.checker.unlink()
                elif body == "card":
                    self.card.unlink()
                else:
                    self.stub(body)
                self.assertEqual(self.check()[1]["action"], "handoff_check_failed")
                self.assertEqual(creates(self.srv), [])
                if not self.card.exists():
                    self.card.write_text("card")
                self.stub("raise SystemExit(0)")

    def test_handoff_level_announces_without_running_the_gate(self):
        # D-4876: the announce asks the lane to write its card, so the card
        # (or a failing checker) must never block the announce itself.
        for body in ("raise SystemExit(1)", None, "card"):
            with self.subTest(body=body):
                self.write_state()
                self.srv.requests = []
                with contextlib.suppress(OSError):
                    oc_l1_recycle.clear_state(self.state()["session_id"])
                self.fill(280000)
                if body is None:
                    self.checker.unlink()
                elif body == "card":
                    self.card.unlink()
                else:
                    self.stub(body)
                out = self.check()[1]
                self.assertEqual(out["action"], "announced")
                self.assertEqual(creates(self.srv), [])
                self.assertEqual(len(prompts(self.srv)), 1)
                self.assertNotIn("handoff_check_failed", self.state())
                if not self.card.exists():
                    self.card.write_text("card")
                self.stub("raise SystemExit(0)")

    def test_checker_read_atime_is_not_a_card_change(self):
        import os
        self.stub("import os, pathlib, sys; p = pathlib.Path(sys.argv[1]); "
                  "print(p.read_text()); s = p.stat(); "
                  "os.utime(p, ns=(s.st_atime_ns + 1000000000, s.st_mtime_ns))")
        before = self.card.stat()
        os.utime(self.card, ns=(1, before.st_mtime_ns))
        self.assertEqual(self.check()[1]["action"], "restarted")

    def test_checker_content_change_is_rejected(self):
        self.stub("import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('changed')")
        self.assertEqual(self.check()[1]["action"], "handoff_check_failed")

    def test_failures_persist_and_recover(self):
        for kind in ("rc1", "rc2", "missing", "permission", "card", "symlink", "timeout", "exception"):
            with self.subTest(kind=kind):
                self.write_state()
                self.srv.requests = []
                if self.card.is_symlink():
                    self.card.unlink()
                self.card.write_text("card")
                self.stub("print('repair card'); raise SystemExit(1)")
                if kind == "rc2":
                    self.stub("raise SystemExit(2)")
                if kind == "missing":
                    self.checker.unlink()
                if kind == "permission":
                    self.checker.chmod(0o600)
                if kind in ("card", "symlink"):
                    self.card.unlink()
                if kind == "symlink":
                    self.card.symlink_to(self.checker)
                with patch("oc_l1_handoff_gate.CHECK_TIMEOUT", 0.2):
                    if kind == "timeout":
                        self.stub("import time; time.sleep(1)")
                    with patch("oc_l1_handoff_gate.subprocess.run", side_effect=RuntimeError) if kind == "exception" else contextlib.nullcontext():
                        self.assertEqual(self.check()[0], 4)
                        importlib.reload(oc_l1_recycle)
                        self.assertEqual(self.check()[1]["action"], "handoff_check_failed")
                self.assertEqual(len(creates(self.srv)), 0)
                self.assertEqual(len(prompts(self.srv)), 1)
                self.assertEqual(self.state()["action"], "handoff_check_failed")
                hb = json.loads(Path(self.lane.get("heartbeat_file", str(Path(self.lane["scratch_dir"]) / "heartbeat.json"))).read_text())
                self.assertEqual(hb["action"], "handoff_check_failed")
                if self.card.is_symlink():
                    self.card.unlink()
                self.card.write_text("fixed")
                self.stub("raise SystemExit(0)")
                self.srv.next_session_ids = ["ses_recovered7"]
                self.assertEqual(self.check()[1]["action"], "restarted")

    def test_unbounded_controls_and_password(self):
        self.stub("print(%r + '\\x1b[31m' + 'x' * 10000 + '\\n' * 50); raise SystemExit(1)" % PW_VALUE)
        self.check()
        text = prompts(self.srv)[0]["body"]["text"]
        self.assertLessEqual(len(text.splitlines()), 20)
        self.assertLess(len(text), 9000)
        self.assertNotIn("\x1b", text)
        self.assertNotIn(PW_VALUE, text)

    def test_distinct_failure_once_even_after_intervening_failure(self):
        for message in ("first", "second", "first"):
            self.stub("print(%r); raise SystemExit(1)" % message)
            self.check()
        self.assertEqual(len(prompts(self.srv)), 2)
        self.assertFalse(creates(self.srv))

    def test_checker_argv(self):
        import oc_l1_handoff_gate as gate
        import subprocess
        real = subprocess.run
        with patch.object(gate.subprocess, "run", wraps=real) as run:
            self.check()
        self.assertEqual(run.call_args.args[0], [str(self.checker), str(self.card), "--log"])
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertGreater(run.call_args.kwargs["timeout"], 0)

    def test_lane_path_validation(self):
        for value in ("relative", "", 12):
            with self.subTest(value=value), self.assertRaises(oc_l1.LaneError):
                oc_l1.validate_lane(dict(self.lane, serve_port=47250, handoff_check=value), self.lane["name"])

    def test_without_key(self):
        del self.lane["handoff_check"]
        self.checker.unlink()
        self.assertEqual(self.check()[1]["action"], "restarted")
