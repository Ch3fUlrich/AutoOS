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
        for rc in (-9, -15, 137, 143, 124, "10", "5"):
            with self.subTest(rc=rc):
                self.assertIsNotNone(self.call([self.row(state="exited", rc=rc)]))
        for rc in (0, 1, 42, "0", None, "unknown"):
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
        with tempfile.TemporaryDirectory(dir=ROOT / "logs") as tmp:
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
        rows = [self.row(90, title="", id="older"),
                self.row(45, title="", state="exited rc=5", rc=5)]
        text = self.agent.relaunch_quota_refusal(
            self.sol, "writer", rows, self.now, self.check)
        self.assertIn("previous-run", text)
        self.assertIn("45 min", text)
        future = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(hours=1)).isoformat()
        state = {"providers": {"codex": {"unavailable_until": future}}}
        with patch.object(self.agent, "load_provider_state", return_value=state):
            self.assertEqual(self.agent.relaunch_quota_ok("codex"),
                             (False, "unavailable_until " + future))
            self.assertTrue(self.agent.relaunch_quota_ok("mimo")[0])
        with patch.object(self.agent, "load_provider_state", side_effect=OSError):
            self.assertTrue(self.agent.relaunch_quota_ok("codex")[0])


if __name__ == "__main__":
    unittest.main()
