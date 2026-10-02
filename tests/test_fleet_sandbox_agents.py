"""FLEET-AGENTS: foreign-repo sandboxes get the AutoOS agent definitions via the config overlay.

A spawned opencode for a repo outside the AutoOS checkout dies with
``Error: Agent not found: "t2-worker"`` because the agent definitions
(t1-orchestrator, t2-worker, t3-reviewer, t4-researcher) exist only in the
AutoOS checkout's opencode.jsonc top-level "agents" block, which a foreign
clone does not have.  The fix injects those definitions into the
OPENCODE_CONFIG_CONTENT overlay for foreign repos, while leaving an
AutoOS-source plan unchanged.

No network, no real gateway: every run is a plan built in-process with a fake
HOME so ~/fleet/sandboxes is a temp dir and a tiny temp git repo as the
foreign source.
"""
import argparse
import importlib.util
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


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent_fleet", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_WORKERS_TMP = None


def setUpModule():
    global _WORKERS_TMP
    _WORKERS_TMP = tempfile.mkdtemp(prefix="autoos-fleet-agents-workers-")
    os.environ["AUTOOS_WORKERS_DIR"] = _WORKERS_TMP


def tearDownModule():
    os.environ.pop("AUTOOS_WORKERS_DIR", None)
    shutil.rmtree(_WORKERS_TMP, ignore_errors=True)


class FleetSandboxAgentsTests(unittest.TestCase):
    """Agent definitions must appear in the overlay for foreign-repo sandboxes."""

    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent()
        cls.tmp = tempfile.mkdtemp(prefix="autoos-fleet-agents-")
        # Create a tiny temp git repo as the foreign source
        cls.foreign_repo = os.path.join(cls.tmp, "foreign-repo")
        os.makedirs(cls.foreign_repo, exist_ok=True)
        subprocess.run(["git", "init", cls.foreign_repo],
                       capture_output=True, check=True)
        subprocess.run(["git", "-C", cls.foreign_repo, "-c", "user.name=autoos-test", "-c", "user.email=autoos-test@example.invalid", "-c", "commit.gpgsign=false", "commit",
                        "--allow-empty", "-m", "init"],
                       capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _args(self, **overrides):
        ns = argparse.Namespace(
            tier=2, card=None, allow_training=False, client="opencode",
            joinable=False, max_depth=None, clean=False, model="omniroute/t2-worker",
            free=False, free_model=self.agent.DEFAULT_FREE_MODEL, isolate=True,
            auto=True, lean=False, title=None, dry_run=True, task="do it",
            no_defer=False, read_only=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def _cfg(self):
        return {"providers": {"omniroute": {"models": {
            "t2-worker": {}, "t3-driver": {}}}},
                "agents": {"t1-orchestrator": {"model": "omniroute/t1-orchestrator",
                                               "description": "orchestrator"},
                           "t2-worker": {"model": "omniroute/t2-worker",
                                         "description": "worker"},
                           "t3-reviewer": {"model": "omniroute/t3-driver",
                                           "description": "reviewer"},
                           "t4-researcher": {"model": "omniroute/t4-researcher",
                                             "description": "researcher"}}}

    def _route(self, **overrides):
        route = {"tier": 2, "combo": "t2-worker", "model": "omniroute/t2-worker",
                 "reason": "stub", "privacy": "public", "review": False,
                 "resolver": False, "read_only": False, "effort": None}
        route.update(overrides)
        return route

    def _build(self, route=None, **overrides):
        args = self._args(**overrides)
        r = route or self._route()
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: r):
            with mock.patch.object(self.agent, "isolate_source",
                                   return_value=self.foreign_repo):
                return self.agent.build_plan(args, self._cfg())

    def _overlay(self, plan):
        raw = plan["env"].get("OPENCODE_CONFIG_CONTENT", "{}")
        return json.loads(raw)

    # --- (1) keyed foreign-repo plan for the t2-worker route -----------------

    def test_keyed_foreign_repo_plan_has_t2_worker_agent(self):
        plan = self._build()
        overlay = self._overlay(plan)
        self.assertIn("agents", overlay)
        self.assertIn("t2-worker", overlay["agents"])
        self.assertIn("model", overlay["agents"]["t2-worker"])

    # --- (2) same for the review route -> agents.t3-reviewer -----------------

    def test_keyed_foreign_repo_review_plan_has_t3_reviewer_agent(self):
        plan = self._build(route=self._route(tier=3, review=True,
                                             model="omniroute/t3-driver",
                                             combo="t3-driver"))
        overlay = self._overlay(plan)
        self.assertIn("agents", overlay)
        self.assertIn("t3-reviewer", overlay["agents"])

    # --- (3) --free foreign plan still carries the free model on every agent --

    def test_free_foreign_plan_carries_free_model_on_every_agent(self):
        free_model = "opencode/free-flash"
        args = self._args(free=True, free_model=free_model, model=None)
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: self._route()):
            with mock.patch.object(self.agent, "isolate_source",
                                   return_value=self.foreign_repo):
                plan = self.agent.build_plan(args, self._cfg())
        overlay = self._overlay(plan)
        self.assertIn("agents", overlay)
        # --free sets overlay["agents"] via free_overlay() with the free model
        # on every TIERS agent; the foreign-repo injection must not overwrite it.
        for agent_name in self.agent.TIERS.values():
            self.assertEqual(overlay["agents"][agent_name]["model"], free_model,
                             "%s should carry the free model" % agent_name)

    # --- (4) an AutoOS-source plan's overlay is unchanged --------------------

    def test_autoos_source_plan_has_no_new_agents_block(self):
        # When isolate_source() returns ROOT, the child IS an AutoOS checkout,
        # so we must NOT inject agents into the overlay.
        args = self._args()
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: self._route()):
            with mock.patch.object(self.agent, "isolate_source",
                                   return_value=os.path.abspath(self.agent.ROOT)):
                plan = self.agent.build_plan(args, self._cfg())
        overlay = self._overlay(plan)
        self.assertNotIn("agents", overlay,
                         "An AutoOS-source plan should not inject agents "
                         "into the overlay (the checkout's opencode.jsonc "
                         "already declares them)")

    # --- (5) no secrets in the injected JSON ---------------------------------

    def test_injected_json_contains_no_api_keys_or_tokens(self):
        plan = self._build()
        overlay = self._overlay(plan)
        raw = json.dumps(overlay)
        # Must not contain key-like values
        self.assertNotIn("Bearer", raw)
        # Walk every key in the overlay for secret-shaped names
        _SECRET_KEYS = {"apiKey", "api_key", "token", "apikey",
                        "secret", "password", "credential"}

        def _check(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower().replace("-", "_") in {s.lower().replace("-", "_")
                                                       for s in _SECRET_KEYS}:
                        # The value must not be a non-empty string
                        self.assertFalse(
                            isinstance(v, str) and v.strip(),
                            "secret-shaped key %r at %s has a non-empty value"
                            % (k, path))
                    _check(v, "%s.%s" % (path, k))
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _check(item, "%s[%d]" % (path, i))

        _check(overlay)

    # --- (6) --free foreign plan keeps definition keys -----------------------

    def test_free_foreign_plan_keeps_definition_keys(self):
        """Test that --free foreign-repo plans keep AutoOS definition keys 
        when overlay defines only model for an agent."""
        free_model = "opencode/free-flash"
        args = self._args(free=True, free_model=free_model, model=None)
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: self._route()):
            with mock.patch.object(self.agent, "isolate_source",
                                   return_value=self.foreign_repo):
                plan = self.agent.build_plan(args, self._cfg())
        overlay = self._overlay(plan)
        
        # Check that agents block exists
        self.assertIn("agents", overlay)
        
        # Check that t2-worker agent exists
        self.assertIn("t2-worker", overlay["agents"])
        
        # Check that the free model is set (from --free)
        self.assertEqual(overlay["agents"]["t2-worker"]["model"], free_model)
        
        # Check that other keys from the root definition are preserved
        # These should come from the root definition in _cfg() method
        self.assertIn("description", overlay["agents"]["t2-worker"])
        self.assertEqual(overlay["agents"]["t2-worker"]["description"], "worker")


class RootAgentsBlockTests(unittest.TestCase):
    """The root_agents_block helper extracts only agent definitions."""

    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent()

    def test_returns_only_agents(self):
        cfg = {"agents": {"t2-worker": {"model": "omniroute/t2-worker"}},
               "providers": {"omniroute": {"env": ["SECRET"]}},
               "model": "omniroute/t1-orchestrator"}
        block = self.agent.root_agents_block(cfg)
        self.assertIn("t2-worker", block)
        self.assertNotIn("providers", block)
        self.assertNotIn("model", block)

    def test_empty_when_no_agents(self):
        self.assertEqual(self.agent.root_agents_block({}), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
