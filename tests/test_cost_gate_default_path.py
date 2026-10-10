"""Default gate file path for the spawner's daily budget gate.

When AUTOOS_DAILY_GATE_FILE is unset, the gate falls back to the default
state-dir path (if it exists) instead of failing open. When it is set,
that value always wins. This file pins the fallback and the priority.

The refusal tests never rely on the host platform: they exercise whichever
of the two default-path branches (XDG on POSIX, LOCALAPPDATA on Windows)
the host takes, so the same file passes on either.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agent = _load("autoos_agent", ROOT / "tools" / "autoos-agent.py")

GATE_ENV = "AUTOOS_DAILY_GATE_FILE"
VERTEX = "omniroute/vertex-gemini-3.8-flash"


def utc_day(delta_days=0):
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def make_args(**over):
    base = dict(task="probe task", run_id=None, model=None, client="opencode",
                free=False, free_model=None, tier=None, card=None, clean=False,
                dry_run=True, isolate=True, read_only=False, joinable=False,
                not_family=None, review_of=None, lean=False, allow_training=False,
                auto=True, no_fallthrough=False, max_depth=None, title=None,
                review_base=None,
                allow_mode_only=False)
    base.update(over)
    return argparse.Namespace(**base)


class DefaultGateCase(unittest.TestCase):
    """A temp dir that plays both the default state dir and the env-var file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        self.home = os.path.join(self.tmp.name, "home")
        self.appdata = os.path.join(self.tmp.name, "appdata")
        for d in (self.state, self.home, self.appdata):
            os.makedirs(os.path.join(d, "autoos"), exist_ok=True)
        self.now = time.time()

    def _both_default_paths(self):
        e = {"XDG_STATE_HOME": self.state, "HOME": self.home,
             "LOCALAPPDATA": self.appdata}
        return (os.path.join(e["XDG_STATE_HOME"], "autoos", "daily-gate.json"),
                os.path.join(e["LOCALAPPDATA"], "autoos", "daily-gate.json"))

    def _default_path(self):
        # The default gate path the host platform actually resolves, so the
        # test passes on POSIX and Windows alike.
        return agent.default_daily_gate_path(env={
            "XDG_STATE_HOME": self.state, "HOME": self.home,
            "LOCALAPPDATA": self.appdata})

    def _write_gate(self, path, verdict="block", usd=26.50, budget=25.0,
                    day=None, raw_content=None, mtime=None):
        if raw_content is not None:
            text = raw_content
        else:
            data = {"day": day if day is not None else utc_day(), "usd": usd,
                    "verdict": verdict, "budget": budget, "by_provider": {}}
            text = json.dumps(data)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        # Pin the mtime: a few seconds before the fixed `now`, so the file is
        # fresh without depending on when the test happened to write it.
        t = mtime if mtime is not None else self.now - 10
        os.utime(path, (t, t))
        return path

    def write_default(self, **over):
        return self._write_gate(self._default_path(), **over)

    def write_named(self, name, **over):
        return self._write_gate(os.path.join(self.tmp.name, name), **over)

    def env(self, gate=None):
        """An injected env with isolated state dirs, never the real ones."""
        e = {"XDG_STATE_HOME": self.state, "HOME": self.home,
             "LOCALAPPDATA": self.appdata}
        if gate is not None:
            e[GATE_ENV] = gate
        return e

    def refused(self, args, env, now=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = agent.daily_gate_refusal(args, env=env,
                                           now=self.now if now is None else now)
        return res, err.getvalue()

    def assert_fail_open(self, env, reason, label, args=None):
        if args is None:
            args = make_args(model=VERTEX)
        res, err = self.refused(args, env)
        self.assertIsNone(res, label)
        self.assertEqual(f"daily gate unavailable: {reason}", err.strip(), label)


class DefaultPathRefusalTests(DefaultGateCase):
    def test_a_env_unset_default_block_refused(self):
        self.write_default(verdict="block", usd=26.50, budget=25.0)
        res, err = self.refused(make_args(model=VERTEX), self.env())
        self.assertIsNotNone(res, err)
        self.assertTrue(res.startswith("daily budget blocked:"), res)

    def test_b_env_unset_no_default_file_unavailable(self):
        # Neither of the two possible default paths exists.
        for p in self._both_default_paths():
            self.assertFalse(os.path.exists(p), p)
        res, err = self.refused(make_args(model=VERTEX), self.env())
        self.assertIsNone(res)
        self.assertEqual("daily gate unavailable: env var not set and no default gate file",
                         err.strip())

    def test_c_env_set_wins_over_default(self):
        # env var -> an OK file; default says block. The env file wins, so no refusal.
        ok = self.write_named("ok_gate.json", verdict="ok", usd=1.0, budget=25.0)
        self.write_default(verdict="block", usd=26.50, budget=25.0)
        res, err = self.refused(make_args(model=VERTEX), self.env(gate=ok))
        self.assertIsNone(res, err)

        # and vice versa: env var -> a block file; default says ok. Refused.
        blk = self.write_named("block_gate.json", verdict="block", usd=30.0, budget=25.0)
        self.write_default(verdict="ok", usd=1.0, budget=25.0)
        res, err = self.refused(make_args(model=VERTEX), self.env(gate=blk))
        self.assertIsNotNone(res, err)
        self.assertTrue(res.startswith("daily budget blocked:"), res)

    def test_d_env_set_missing_file_does_not_consult_default(self):
        missing = os.path.join(self.tmp.name, "does-not-exist.json")
        # The default is deliberately corrupt: if the default were ever
        # consulted, the reason would be "not a JSON object", not
        # "file unreadable".
        self.write_default(raw_content="this is not json at all")
        res, err = self.refused(make_args(model=VERTEX), self.env(gate=missing))
        self.assertIsNone(res)
        self.assertEqual("daily gate unavailable: file unreadable", err.strip())

    def test_e_default_file_stale(self):
        self.write_default(verdict="block", usd=26.50, budget=25.0,
                           mtime=self.now - 7300)
        self.assert_fail_open(self.env(), "file stale (age)", "stale default file")

    def test_f_non_google_plan_never_refused(self):
        self.write_default(verdict="block", usd=26.50, budget=25.0)
        for client in ("agy", "antigravity"):
            with self.subTest(client=client):
                args = make_args(model=VERTEX, client=client)
                res, err = self.refused(args, self.env())
                self.assertIsNone(res, err)


class DefaultGatePathHelperTests(DefaultGateCase):
    def test_xdg_state_home_set(self):
        path = agent.default_daily_gate_path(
            env={"XDG_STATE_HOME": self.state, "HOME": self.home,
                 "LOCALAPPDATA": self.appdata},
            is_windows=False)
        self.assertEqual(os.path.join(self.state, "autoos", "daily-gate.json"), path)

    def test_xdg_state_home_unset_home_based(self):
        path = agent.default_daily_gate_path(
            env={"HOME": self.home, "LOCALAPPDATA": self.appdata},
            is_windows=False)
        self.assertEqual(
            os.path.join(self.home, ".local", "state", "autoos", "daily-gate.json"),
            path)

    def test_windows_branch_uses_localappdata(self):
        path = agent.default_daily_gate_path(
            env={"XDG_STATE_HOME": self.state, "HOME": self.home,
                 "LOCALAPPDATA": self.appdata},
            is_windows=True)
        self.assertEqual(
            os.path.join(self.appdata, "autoos", "daily-gate.json"), path)


if __name__ == "__main__":
    unittest.main()
