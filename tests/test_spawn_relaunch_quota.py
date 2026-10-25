import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RelaunchQuotaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = load("relaunch_agent", "autoos-agent.py")

    def setUp(self):
        self.now = datetime.datetime(2026, 10, 24, tzinfo=datetime.timezone.utc)
        self.sol = "omniroute/cx-gpt-6.1-sol"
        self.check = Mock(return_value=(False, "429"))

    def row(self, minutes=45, **changes):
        row = dict(id="previous-run", title="writer task", lane="writer",
                   model=self.sol, client="opencode", state="died", rc=None,
                   started=(self.now - datetime.timedelta(minutes=minutes)).isoformat(),
                   ended=None)
        row.update(changes)
        return row

    def call(self, rows, model=None):
        return self.agent.relaunch_quota_refusal(
            model or self.sol, "writer task", rows, self.now, self.check)

    def test_died_quota_failed(self):
        text = self.call([self.row()])
        self.assertIn("previous-run", text)
        self.assertIn("quota", text)
        self.check.assert_called_once_with("codex")

    def test_quota_passed(self):
        self.check.return_value = (True, "")
        self.assertIsNone(self.call([self.row()]))

    def test_short_quiet_period(self):
        for minutes in (20, 30):
            self.assertIsNone(self.call([self.row(minutes)]))
        self.check.assert_not_called()

    def test_luna_shares_sol_quota(self):
        row = self.row(60, state="exited rc=10", rc=10)
        self.assertIsNotNone(self.call([row], "omniroute/cx-gpt-6.1-luna"))
        self.check.assert_called_once_with("codex")

    def test_different_quota(self):
        self.assertIsNone(self.call([self.row(60, model="mimo/mimo-v2.6-flash")]))
        self.check.assert_not_called()

    def test_progress(self):
        progress = self.row(90, state="exited rc=0", rc=0)
        self.assertIsNone(self.call([progress]))
        self.assertIsNone(self.call([self.row(120), progress]))
        self.check.assert_not_called()

    def test_killed_and_string_return_codes(self):
        for rc in (-9, -15, 137, 143, 124, "10", "5", 6):
            with self.subTest(rc=rc):
                self.assertIsNotNone(self.call([self.row(state="exited", rc=rc)]))
        for rc in (0, 1, 7, 42, "0", None, "unknown"):
            with self.subTest(rc=rc):
                self.assertIsNone(self.call([self.row(state="exited", rc=rc)]))

    def test_empty_identity_never_matches(self):
        for identity in ("", None):
            self.assertIsNone(self.agent.relaunch_quota_refusal(
                self.sol, identity, [self.row(title="", lane="")],
                self.now, self.check))
        self.check.assert_not_called()

    def test_registry_provider_namespace(self):
        future = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(minutes=40)).isoformat()
        for provider, group in (("cx", "codex"), ("cxa", "codex"),
                                ("xiaomi_mimo", "mimo")):
            with self.subTest(provider=provider):
                state = {"providers": {provider: {"unavailable_until": future}}}
                with patch.object(self.agent, "load_provider_state", return_value=state):
                    self.assertFalse(self.agent.relaunch_quota_ok(group)[0])
                    other = "mimo" if group == "codex" else "codex"
                    self.assertTrue(self.agent.relaunch_quota_ok(other)[0])
        for pin in ("other/not-mimo", "other/not-cx-gpt-model"):
            self.assertEqual(self.agent.quota_group(pin), "other")

    def test_cli_preview_enforcement_and_double_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            now = datetime.datetime.now(datetime.timezone.utc)
            row = self.row(90)
            row["started"] = (now - datetime.timedelta(minutes=90)).isoformat()
            (directory / "previous-run.json").write_text(json.dumps(row), encoding="utf-8")
            state = directory / "state.json"
            state.write_text(json.dumps({"providers": {"cx": {
                "unavailable_until": (now + datetime.timedelta(minutes=40)).isoformat()
            }}}), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith("AUTOOS_")}
            env.update(AUTOOS_WORKERS_DIR=tmp, AUTOOS_ADMISSION_OFF="1",
                       AUTOOS_OMNIROUTE_KEY="test-not-a-key",
                       AUTOOS_HOST_NAME="test-host",
                       AUTOOS_PROVIDER_STATE_PATH=str(state))
            cmd = [sys.executable, str(ROOT / "tools/autoos-agent.py"), "run",
                   "--model", self.sol, "--title", "writer task", "--isolate", "x"]
            sandboxes = ROOT / "logs" / "sandboxes"
            before = set(sandboxes.iterdir()) if sandboxes.exists() else set()
            for dry in (False, False, True):
                result = subprocess.run(cmd + (["--dry-run"] if dry else []),
                                        cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if dry else 15,
                                 result.stdout + result.stderr)
                if dry:
                    self.assertIn("note: relaunch quota:", result.stdout)
                else:
                    self.assertTrue(result.stderr.startswith("relaunch quota:"), result.stderr)
                after = set(sandboxes.iterdir()) if sandboxes.exists() else set()
                self.assertEqual(before, after)

    def test_different_title(self):
        self.assertIsNone(self.call([self.row(title="another task")]))
        self.check.assert_not_called()

    def test_lane_noop_and_newest(self):
        rows = [self.row(90, title="writer", id="older"),
                self.row(45, title="", state="exited rc=5", rc=5)]
        text = self.agent.relaunch_quota_refusal(
            self.sol, "writer", rows, self.now, self.check)
        self.assertIn("older", text)
        self.assertIn("90 min", text)
        future = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(hours=1)).isoformat()
        state = {"providers": {"codex": {"unavailable_until": future}}}
        with patch.object(self.agent, "load_provider_state", return_value=state):
            ok, detail = self.agent.relaunch_quota_ok("codex")
            self.assertFalse(ok)
            self.assertIn("until " + future, detail)
            self.assertIn("min left)", detail)
            self.assertTrue(self.agent.relaunch_quota_ok("mimo")[0])
        with patch.object(self.agent, "load_provider_state", side_effect=OSError):
            self.assertTrue(self.agent.relaunch_quota_ok("codex")[0])

    def test_last_activity_and_override(self):
        for age in (10, 45):
            ended = (self.now - datetime.timedelta(minutes=age)).isoformat()
            text = self.call([self.row(180, ended=ended)])
            if age == 10:
                self.assertIsNone(text)
            else:
                self.assertIn("45 min", text)
        with patch.dict(os.environ, AUTOOS_RELAUNCH_QUOTA_OFF="1"):
            self.assertIsNone(self.call([self.row()]))

    def test_state_shapes_and_timestamp_formats(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
        for value in (future.isoformat(), future.isoformat().replace("+00:00", "Z"),
                      future.replace(tzinfo=None).isoformat()):
            with self.subTest(value=value), patch.object(self.agent, "load_provider_state",
                    return_value={"providers": {"cx": {"unavailable_until": value}}}):
                ok, detail = self.agent.relaunch_quota_ok("codex")
                self.assertFalse(ok)
                self.assertIn("min left)", detail)
        for state in (None, [], 3, {"providers": []}, {"providers": [1]},
                      {"providers": {"cx": []}}):
            with self.subTest(state=state), patch.object(self.agent, "load_provider_state", return_value=state):
                self.assertTrue(self.agent.relaunch_quota_ok("codex")[0])
        for value in ("garbage", 42, None, []):
            with self.subTest(value=value), patch.object(self.agent, "load_provider_state",
                    return_value={"providers": {"cx": {"unavailable_until": value}}}):
                self.assertTrue(self.agent.relaunch_quota_ok("codex")[0])

    def test_mcp_relaunch_rejected(self):
        sys.path.insert(0, str(ROOT / "tools"))
        mcp = load("relaunch_mcp", "autoos_agent_mcp.py")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "exit.json").write_text(json.dumps({"rc": 15}), encoding="utf-8")
            with patch.object(mcp, "read_kill_record", return_value=None):
                state = mcp._state(tmp)
            self.assertEqual(state["state"], "rejected")
            self.assertEqual(state["detail"], "relaunch-quota")

    def test_pool_relaunch_requeued(self):
        spec = importlib.util.spec_from_file_location("quota_slot_fixture", ROOT / "tests/test_runner_pool_slot.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        case = fixture.TestSlot()
        case.setUp()
        self.addCleanup(case.tearDown)
        case._q("quota-job")
        self.assertEqual(fixture.pool.run_one(case.root, case.n,
                         case._deps(spawn=lambda *args: 15)), "requeued-rc15")
        self.assertTrue((case.root / "queue/quota-job.json").exists())


if __name__ == "__main__":
    unittest.main()
