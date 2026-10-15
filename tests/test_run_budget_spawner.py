"""tests/test_run_budget_spawner.py — the spawner's daily gate.

Covers the Google-paid refusal (direct CLI, MCP starts, --free, per-model
exclusions), the fail-open behaviour for stale, future or garbage gate
files, and every call site that checks the gate (early, after the plan,
in the fallthrough re-run).
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import _host_state as host_state  # noqa: E402  (tests/_host_state.py: the host reads)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agent = _load("autoos_agent", ROOT / "tools" / "autoos-agent.py")
mcp_server = _load("autoos_agent_mcp", ROOT / "tools" / "autoos_agent_mcp.py")

# The literal name, not the module constant: a rename of the constant has to
# be caught here, where the real environment is the thing under test.
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


class GateFileCase(unittest.TestCase):
    """A temp dir plus a gate file written for today's UTC day."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gate_path = os.path.join(self.tmp.name, "daily_gate.json")

    def write_gate(self, verdict="block", usd=26.50, budget=25.0, day=None,
                   drop_budget=False, bom=False, utf16=False):
        data = {"day": day if day is not None else utc_day(), "usd": usd,
                "verdict": verdict, "by_provider": {}, "unverified": False}
        if not drop_budget:
            data["budget"] = budget
        text = json.dumps(data)
        with open(self.gate_path, "w",
                  encoding="utf-16" if utf16 else "utf-8-sig" if bom else "utf-8") as fh:
            fh.write(text)
        return self.gate_path

    def gate_env(self, path):
        return {GATE_ENV: path}

    def google_args(self, **over):
        return make_args(model=VERTEX, **over)

    def assert_fail_open(self, env, label, reason=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = agent.daily_gate_refusal(self.google_args(), env=env)
        self.assertIsNone(res, label)
        if reason is None:
            self.assertIn("daily gate unavailable", err.getvalue(), label)
        else:
            expected = f"daily gate unavailable: {reason}"
            self.assertEqual(expected.strip(), err.getvalue().strip(), label)


class RefusalTests(GateFileCase):
    def test_block_refusal_names_day_total_and_budget(self):
        path = self.write_gate(verdict="block", usd=25.50, budget=25.0)
        for m in ("vertex/gemini-3.8-flash", "gemini/gemini-2.5-pro",
                  "omniroute/gemini-2.5-flash", VERTEX):
            refusal = agent.daily_gate_refusal(make_args(model=m), env=self.gate_env(path))
            self.assertIsNotNone(refusal, m)
        self.assertIn("day total $25.50 exceeds budget $25.00", refusal)

    def test_block_without_a_budget_field_says_the_default(self):
        path = self.write_gate(drop_budget=True)
        refusal = agent.daily_gate_refusal(self.google_args(), env=self.gate_env(path))
        self.assertIn("exceeds budget $25.00", refusal)

    def test_the_free_flag_does_not_exempt_a_paid_model(self):
        # `--free --model X` launches X: the pin follows the free flag, so X
        # is the model that gets used and it is checked like any other pin.
        path = self.write_gate()
        args = self.google_args(free=True, free_model=VERTEX)
        self.assertIsNotNone(agent.daily_gate_refusal(args, env=self.gate_env(path)))

    def test_lower_mixed_and_leading_space_spellings_are_refused(self):
        path = self.write_gate()
        for m in ("  Omniroute/Vertex-Gemini-3.8-Flash ", "VERTEX/gemini-3.8-flash",
                  "OmniRoute/gemini-3.8-flash"):
            self.assertIsNotNone(
                agent.daily_gate_refusal(make_args(model=m), env=self.gate_env(path)), m)

    def test_a_google_plan_is_refused_beside_ovh_and_agy_models(self):
        # Per-model decisions: an ovh/agy string anywhere in the start must
        # not stand the check down for a Google-paid model or leg that this
        # run can use.
        path = self.write_gate()
        env = self.gate_env(path)
        ovh_legs = {"route": {"legs": [{"provider": "ovh", "model": "ovh/foo"}]}}
        self.assertIsNotNone(agent.daily_gate_refusal(
            make_args(model="ovh/foo"), {"model": VERTEX, **ovh_legs}, env=env))
        self.assertIsNotNone(agent.daily_gate_refusal(
            make_args(model="agy/x"), {"model": VERTEX}, env=env))
        self.assertIsNotNone(agent.daily_gate_refusal(
            make_args(), {"model": VERTEX, "route": {"legs": ["ovh/bar"]}}, env=env))


class VerdictTests(GateFileCase):
    def test_ok_and_warn_verdicts_allow_without_a_note(self):
        for verdict in ("ok", "warn"):
            path = self.write_gate(verdict=verdict, usd=10.0, budget=25.0)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                res = agent.daily_gate_refusal(self.google_args(),
                                               env=self.gate_env(path))
            self.assertIsNone(res, verdict)
            self.assertEqual("", err.getvalue().strip(),
                             "%s is a verdict, not an unavailable gate" % verdict)


class ExclusionTests(GateFileCase):
    def test_agy_ovh_and_free_model_runs_are_never_refused(self):
        path = self.write_gate(verdict="block", usd=30.0, budget=25.0)
        env = self.gate_env(path)
        # an agy client with a Google model: its own quota, not the gate's spend
        self.assertIsNone(agent.daily_gate_refusal(
            make_args(model="vertex/gemini-3.8-flash", client="agy"), env=env))
        for m in ("agy/gemini-3.8-flash", "antigravity/gemini-3.8-flash",
                  "ovh/deepseek-r1", "ovhcloud/x", "opencode/claude-3-5-sonnet"):
            self.assertIsNone(agent.daily_gate_refusal(
                make_args(model=m), env=env), m)
        # a plain --free run uses the free chain, not a Google model the
        # caller did not name
        self.assertIsNone(agent.daily_gate_refusal(
            make_args(free=True, free_model=agent.DEFAULT_FREE_MODEL), env=env))

    def test_the_env_var_name_is_what_the_shell_will_export(self):
        # the constant the spawner reads must be the literal name the operator
        # exports: setting only the literal has to reach the gate
        self.assertEqual(agent.DAILY_GATE_ENV_VAR, "AUTOOS_DAILY_GATE_FILE")
        path = self.write_gate()
        with mock.patch.dict(os.environ, {"AUTOOS_DAILY_GATE_FILE": path}):
            self.assertIsNotNone(agent.daily_gate_refusal(self.google_args()))


class FailOpenTests(GateFileCase):
    def test_unset_env_fail_open(self):
        self.assert_fail_open({}, "unset environment variable", reason="env var not set and no default gate file")

    def test_missing_file_fail_open(self):
        missing = os.path.join(self.tmp.name, "nope.json")
        self.assert_fail_open({GATE_ENV: missing}, "missing file", reason="file unreadable")

    def test_corrupt_json_fail_open(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        Path(bad).write_text("not json", encoding="utf-8")
        self.assert_fail_open({GATE_ENV: bad}, "corrupt json", reason="not a JSON object")

    def test_garbage_numbers_fail_open_without_a_traceback(self):
        for field, value in (("usd", "abc"), ("budget", "a lot")):
            path = self.write_gate()
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            data[field] = value
            Path(path).write_text(json.dumps(data), encoding="utf-8")
            self.assert_fail_open({GATE_ENV: path}, "garbage %s" % field, reason="garbage value")

    def test_a_block_for_another_day_is_stale(self):
        self.assert_fail_open(self.gate_env(self.write_gate(day="2001-01-01")),
                              "a block written for another UTC day", reason="file stale (not today's UTC day)")

    def test_a_future_mtime_is_stale(self):
        path = self.write_gate()
        future = time.time() + 3600
        os.utime(path, (future, future))
        self.assert_fail_open(self.gate_env(path), "a file dated in the future", reason="file stale (future mtime)")

    def test_an_old_mtime_still_fails_open(self):
        path = self.write_gate()
        stale = time.time() - 7201
        os.utime(path, (stale, stale))
        self.assert_fail_open(self.gate_env(path), "mtime older than two hours", reason="file stale (age)")

    def test_bom_and_utf16_files_are_honoured(self):
        for kwargs, label in (({"bom": True}, "utf-8 with a BOM"),
                              ({"utf16": True}, "utf-16")):
            path = self.write_gate(**kwargs)
            refusal = agent.daily_gate_refusal(
                self.google_args(), env=self.gate_env(path))
            self.assertIn("day total", refusal, label)


class McpStartGateTests(GateFileCase):
    """The MCP spawn path: preflight and spawn run the CLI as a child, so the
    gate has to reach that child, or the MCP path is wide open."""

    def setUp(self):
        super().setUp()
        # The CLI child these two paths exec measures the host it runs on before
        # it plans anything: the live worker count (`logs/workers`, which on a lane
        # host is the lanes' own records) and MemAvailable. So the class names an
        # empty records dir and the spawner's own test-only admission escape, and
        # puts the *default* spend report in an empty state home — a machine that
        # has spent nothing today, which is what CI is. Without this, "the gate
        # refused" below can be a memory refusal, and "no gate named" can be
        # today's real over-budget ledger (AO-ADMISSION-2, 2026-10-09).
        host_state.install(self, directory=os.path.join(self.tmp.name, "host-pins"),
                           gate="absent", workers=True, meminfo=None,
                           admission_off=True)

    def test_the_gate_file_reaches_the_cli_child_env(self):
        # the spawner builds the child env through one passlist; the gate
        # file has to clear it, whatever value names it
        with mock.patch.dict(os.environ, {GATE_ENV: "/tmp/daily_gate.json"}):
            env = agent.spawner_child_env()
        self.assertEqual("/tmp/daily_gate.json", env.get(GATE_ENV))

    def test_mcp_start_is_refused_with_a_fresh_block_file(self):
        path = self.write_gate(verdict="block", usd=26.50, budget=25.0)
        req = {"task": "probe task", "client": "opencode", "model": VERTEX,
               "dry_run": True}
        with mock.patch.dict(os.environ, {GATE_ENV: path}):
            out = mcp_server.spawn(req)
        self.assertEqual("rejected", out.get("state"))
        # the same message the direct CLI gives: one gate, one refusal line
        self.assertIn("autoos-agent: daily budget blocked: day total $26.50 "
                      "exceeds budget $25.00", out.get("error", ""))

    def test_mcp_start_is_unchanged_when_the_var_is_unset(self):
        # `setUp` already moved the default report out of the way: an empty state
        # home is a host that has spent nothing today. Popping the variable here is
        # the test's own premise, not the machine's state.
        old = os.environ.pop(GATE_ENV, None)
        try:
            refused = mcp_server.preflight(
                ["run", "--client", "opencode", "--model", VERTEX, "probe task"],
                cwd=str(ROOT))
        finally:
            if old is not None:
                os.environ[GATE_ENV] = old
        self.assertIsNone(refused, "a gate the caller did not point at must not refuse")


class CmdRunGateTests(GateFileCase):
    """The three call sites: before the plan, after it, and the fallthrough
    re-run. Each test fails if its call site goes away."""

    def _env(self, gate_path, extra=None):
        env = dict(os.environ, **(extra or {}))
        # HOSTADMISSION reads the host's own free memory. This file is about the
        # cost gate: a CI box below the floor would refuse every real launch here
        # for a reason none of them is about, so the test-only escape is set in
        # the one place this file builds a child env. HostAdmissionTests in
        # tests/test_autoos_spawner.py is where the rule itself is tested.
        env.setdefault("AUTOOS_ADMISSION_OFF", "1")
        if gate_path is None:
            env.pop(GATE_ENV, None)
        else:
            env[GATE_ENV] = gate_path
        # a shell that runs `git -c` exports GIT_CONFIG_KEY_n/VALUE_n pairs;
        # the empty VALUES do not survive the clear-and-repatch below on
        # Windows, where an empty value unsets the variable, and git then
        # dies on the dangling COUNT. None of the gate code under test reads
        # git config, so the pair goes.
        for k in [k for k in env if k.startswith("GIT_CONFIG_")]:
            del env[k]
        return env

    def test_cmd_run_refuses_a_google_paid_start(self):
        gate = self.write_gate(verdict="block", usd=26.00, budget=25.0)
        for over in ({}, {"free": True, "free_model": VERTEX}):
            args = make_args(model=VERTEX, **over)
            err = io.StringIO()
            with mock.patch.dict(os.environ, self._env(gate), clear=True), \
                    contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = agent.cmd_run(args, {})
            self.assertEqual(2, rc, over)
            self.assertIn("daily budget blocked", err.getvalue(), over)
            self.assertIn("$26.00", err.getvalue(), over)

    def test_post_plan_call_site_refuses_a_google_plan(self):
        # The flags carry no Google model; the plan the resolver answered
        # with does. Only the check on the plan can refuse this one.
        gate = self.write_gate(verdict="block", usd=26.0, budget=25.0)
        plan = {
            "model": VERTEX, "client": "opencode", "agent": "l2-worker",
            "route": {"combo": "vertex-combo", "reason": "test", "tier": 2,
                      "privacy": "public", "resolver": True},
            "depth": (2, 2), "run_id": "20261003-000000-post-plan-a1b2c3",
            "session_tag": None, "sandbox": None, "cmd": ["opencode", "run"],
            "cwd": self.tmp.name, "env": {}, "requested_model": "",
            "launched_model": "", "free": False,
        }
        err = io.StringIO()
        with mock.patch.dict(os.environ, self._env(gate), clear=True), \
                mock.patch.object(agent, "build_plan", return_value=plan), \
                contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = agent.cmd_run(make_args(dry_run=True), {})
        self.assertEqual(2, rc)
        self.assertIn("daily budget blocked", err.getvalue())

    def _git_repo(self, path, branch=None):
        os.makedirs(path)
        with open(os.path.join(path, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("work\n")
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-10-03T00:00:00Z",
                   GIT_COMMITTER_DATE="2026-10-03T00:00:00Z")
        for args in (("init", "-q"), ("add", "a.txt"),
                     ("-c", "user.name=t", "-c", "user.email=t@t",
                      "commit", "-q", "-m", "c")):
            subprocess.run(["git", "-C", path, *args], check=True,
                           capture_output=True, text=True, env=env)
        if branch:
            subprocess.run(["git", "-C", path, "branch", branch], check=True,
                           capture_output=True, text=True, env=env)
        return path

    def test_fallthrough_rerun_call_site_refuses_a_google_plan(self):
        # Attempt one stops on its (non-Google) route; the re-plan lands on a
        # Google-paid model. Only the check that runs on the re-run can stop
        # the second attempt before the client starts.
        gate = self.write_gate(verdict="block", usd=26.0, budget=25.0)
        src = self._git_repo(os.path.join(self.tmp.name, "src"))
        clone = self._git_repo(os.path.join(self.tmp.name, "clone"), branch="agent/t")
        state = os.path.join(self.tmp.name, "state")
        plan_a = {
            "model": "omniroute/deepseek-v4-flash", "client": "opencode",
            "agent": "l2-worker",
            "route": {"combo": "a-combo", "reason": "test", "tier": 2,
                      "privacy": "public", "resolver": True, "card": None},
            "depth": (2, 2), "run_id": "20261003-000001-fall-gate-a1b2c3",
            "session_tag": None,
            "sandbox": {"path": clone, "branch": "agent/t", "source": src},
            "cmd": ["opencode", "run", "x"], "cwd": clone, "env": {},
            "requested_model": "", "launched_model": "", "free": False,
        }
        plan_b = dict(plan_a, model=VERTEX, sandbox=None,
                      route=dict(plan_a["route"], combo="b-combo"))
        stop_line = "Error: 503 all targets were skipped by pre-dispatch filters"

        def fake_run_client(cmd, cwd, env, **kw):
            return agent.ClientExit(0, tail=stop_line, raw_tail=stop_line)

        err = io.StringIO()
        with mock.patch.dict(os.environ, self._env(
                gate, {"AUTOOS_STATE_DIR": state, "AUTOOS_WORKERS_DIR": state,
                       "AUTOOS_AI_STACK_CONFIG": state}), clear=True), \
                mock.patch.object(agent, "build_plan", side_effect=[plan_a, plan_b]), \
                mock.patch.object(agent, "run_client", fake_run_client), \
                mock.patch.object(agent, "resolve_client_executable",
                                  lambda cmd: list(cmd)), \
                mock.patch.object(agent.clients, "signin_state",
                                  lambda client, env=None: (None, "")), \
                mock.patch.object(agent, "client_key", lambda root: "test-only-key"), \
                mock.patch.object(agent, "gateway_up", lambda: True), \
                mock.patch.object(agent, "provision_worker_dirs", lambda env: True), \
                mock.patch.object(agent, "sandbox_root_prepare", lambda *a: None), \
                mock.patch.object(agent, "isolate_clone", lambda *a: None), \
                mock.patch.object(agent, "track_entry", lambda *a: None), \
                mock.patch.object(agent, "resolved_writer",
                                  lambda *a, **k: None), \
                mock.patch.object(agent, "log_run", lambda *a: None), \
                contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = agent.cmd_run(make_args(dry_run=False), {})
        self.assertEqual(2, rc)
        self.assertIn("daily budget blocked", err.getvalue())
        self.assertIn("$26.00", err.getvalue())
 

if __name__ == "__main__":
    unittest.main()
