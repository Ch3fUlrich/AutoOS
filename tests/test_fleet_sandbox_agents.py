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


def load_evidence():
    """tools/seat-model-evidence.py, imported for its attribution functions.

    D-426: the header a run stamps and its record's tag are only worth as much
    as the match the evidence tool applies to both, so the test asserts THROUGH
    `tag_matches` / `read_run_record_session_tag` rather than re-deriving the
    comparison here.
    """
    spec = importlib.util.spec_from_file_location(
        "seat_model_evidence_under_test", TOOLS / "seat-model-evidence.py")
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
        return {"providers": {
            "omniroute": {"name": "AutoOS OmniRoute gateway",
                          "env": ["AUTOOS_OMNIROUTE_KEY"],
                          "package": "@opencode/ai/providers/openai-compatible",
                          "settings": {"baseURL": "http://127.0.0.1:20128/v1"},
                          "models": {"t2-worker": {}, "t3-driver": {}}},
            "litellm": {"name": "AutoOS LiteLLM",
                        "env": ["AUTOOS_LITELLM_KEY"],
                        "settings": {"baseURL": "http://127.0.0.1:4000/v1"},
                        "models": {"lite-a": {}}}},
                "permissions": [
                    {"action": "shell", "resource": "git status *", "effect": "allow"},
                    {"action": "shell", "resource": "git push *", "effect": "deny"}],
                "mcp": {"serena": {"command": ["serena"]}},
                "experimental": {"subagent_depth": 2},
                "model": "omniroute/t1-orchestrator",
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
        # FLEET-AGENTS-2: the providers block holds env var NAMES only.
        self.assertEqual(overlay["providers"]["omniroute"]["env"],
                         ["AUTOOS_OMNIROUTE_KEY"])
        for prov in overlay["providers"].values():
            for item in prov.get("env", []):
                self.assertRegex(item, r"^[A-Z][A-Z0-9_]*$")
        for forbidden in ("mcp", "experimental", "model", "tools"):
            self.assertNotIn(forbidden, overlay)

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

    # --- FLEET-AGENTS-2: providers + permission fence ------------------------

    def test_foreign_keyed_plan_has_root_providers(self):
        overlay = self._overlay(self._build())
        omni = overlay["providers"]["omniroute"]
        self.assertIn("t2-worker", omni["models"])
        self.assertEqual(omni["env"], ["AUTOOS_OMNIROUTE_KEY"])
        self.assertEqual(omni["package"],
                         "@opencode/ai/providers/openai-compatible")
        self.assertIn("lite-a", overlay["providers"]["litellm"]["models"])

    def test_foreign_plan_keeps_per_run_headers_and_root_models(self):
        overlay = self._overlay(self._build())
        omni = overlay["providers"]["omniroute"]
        self.assertTrue(omni.get("headers"), "per-run session headers lost")
        self.assertIn("t2-worker", omni["models"])
        self.assertIn("t3-driver", omni["models"])
        self.assertEqual(omni["settings"]["baseURL"],
                         "http://127.0.0.1:20128/v1")

    def test_foreign_plan_permissions_root_first_spawn_gate_last(self):
        plan = self._build()
        perms = self._overlay(plan)["permissions"]
        root_perms = self._cfg()["permissions"]
        self.assertEqual(perms[:len(root_perms)], root_perms)
        gate = self.agent.spawn_gate_rules(2)
        self.assertEqual(perms[-len(gate):], gate)
        # none dropped: root + outside fence + spawn gate
        fence = self.agent.outside_fence(plan["env"]["XDG_DATA_HOME"],
                                         os.environ.get("AUTOOS_TASK_DIR"))
        self.assertEqual(perms, root_perms + fence + gate)

    def test_autoos_source_overlay_gets_no_providers_or_root_permissions(self):
        args = self._args()
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: self._route()):
            with mock.patch.object(self.agent, "isolate_source",
                                   return_value=os.path.abspath(self.agent.ROOT)):
                plan = self.agent.build_plan(args, self._cfg())
        overlay = self._overlay(plan)
        # only the per-run OR3 headers; nothing from ROOT's provider definition
        self.assertEqual(list(overlay["providers"]), ["omniroute"])
        self.assertEqual(list(overlay["providers"]["omniroute"]), ["headers"])
        for rule in self._cfg()["permissions"]:
            self.assertNotIn(rule, overlay["permissions"])
        gate = self.agent.spawn_gate_rules(2)
        self.assertEqual(overlay["permissions"][-len(gate):], gate)
        self.assertNotIn("agents", overlay)

    def test_foreign_keyed_plan_through_stamp_keeps_models_gets_base_url(self):
        plan = self._build()
        env = dict(plan["env"])
        with mock.patch.object(self.agent, "gateway_base_url",
                               return_value="http://gw.example.invalid:1/v1"):
            self.assertTrue(self.agent.stamp_worker_gateway(env))
        omni = json.loads(env["OPENCODE_CONFIG_CONTENT"])["providers"]["omniroute"]
        self.assertEqual(omni["settings"]["baseURL"],
                         "http://gw.example.invalid:1/v1")
        self.assertIn("t2-worker", omni["models"])
        self.assertEqual(omni["env"], ["AUTOOS_OMNIROUTE_KEY"])
        self.assertTrue(omni.get("headers"))

    def test_root_cfg_is_not_mutated_by_a_foreign_plan(self):
        cfg = self._cfg()
        before = json.dumps(cfg, sort_keys=True)
        args = self._args()
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: self._route()):
            with mock.patch.object(self.agent, "isolate_source",
                                   return_value=self.foreign_repo):
                self.agent.build_plan(args, cfg)
        self.assertEqual(json.dumps(cfg, sort_keys=True), before)

    # --- D-426: a foreign run's gateway headers carry the RECORD's tag --------

    def test_foreign_plan_headers_carry_the_record_session_tag(self):
        """The OR3 stamp must equal what the run record stores (D-426).

        The S4 rewrite retitles a foreign run's tag from the AutoOS lane name
        to the target repo's slug AFTER the OR3 block stamped the header, so
        the gateway logged `<AutoOS-lane>/<title>/<run-id>` while the record
        said `<repo>/<title>` - and the evidence gate's exact match found no
        row. The header is checked the way the evidence tool itself decides:
        `tag_matches(row, record_tag, run_id)`, with the record tag read back
        from a workers record as read_run_record_session_tag would read it.
        """
        plan = self._build(route=self._route(tier=3, review=True, read_only=True,
                                             combo="t3-driver",
                                             model="omniroute/ovh-direct-gpt-oss-120b"))
        overlay = self._overlay(plan)
        headers = overlay["providers"]["omniroute"]["headers"]
        self.assertEqual(headers[self.agent.RUN_ID_HEADER], plan["run_id"])
        # the record's tag names the foreign repo, not the AutoOS checkout
        slug = self.agent.sandbox_repo_slug(self.foreign_repo)
        self.assertEqual(plan["session_tag"], "%s/t3-do-it" % slug)
        self.assertEqual(
            headers[self.agent.SESSION_TAG_HEADER],
            self.agent.session_header_value(plan["session_tag"], plan["run_id"]),
            "gateway header and run record must name the same session tag")

        logs = tempfile.mkdtemp(prefix="autoos-evtag-logs-")
        try:
            workers = Path(logs) / "workers"
            workers.mkdir()
            (workers / ("%s.json" % plan["run_id"])).write_text(
                json.dumps({"session_tag": plan["session_tag"],
                            "run_id": plan["run_id"]}), encoding="utf-8")
            evidence = load_evidence()
            record_tag = evidence.read_run_record_session_tag(plan["run_id"], logs)
            self.assertEqual(record_tag, plan["session_tag"])
            self.assertTrue(evidence.tag_matches(
                headers[self.agent.SESSION_TAG_HEADER], record_tag, plan["run_id"]),
                "the evidence gate would refuse this run's only gateway row")
        finally:
            shutil.rmtree(logs, ignore_errors=True)

    def test_foreign_and_autoos_headers_sit_in_the_same_shape_and_place(self):
        """D-426: same key, same two headers, same value rule as an AutoOS run.

        The foreign merge (FLEET-AGENTS-2) must keep ROOT's provider
        definition AND the per-run `headers` exactly where an AutoOS-source
        overlay puts them - provider level, under `headers` (the key opencode
        v2 reads), not ROOT's `settings`, and no ROOT block may introduce a
        second, empty one.
        """
        foreign_plan = self._build()
        args = self._args()
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: self._route()):
            with mock.patch.object(self.agent, "isolate_source",
                                   return_value=os.path.abspath(self.agent.ROOT)):
                autoos_plan = self.agent.build_plan(args, self._cfg())
        foreign_omni = self._overlay(foreign_plan)["providers"]["omniroute"]
        autoos_omni = self._overlay(autoos_plan)["providers"]["omniroute"]
        self.assertEqual(list(autoos_omni), ["headers"],
                         "an AutoOS-source overlay must stay byte-identical")
        self.assertIn("headers", foreign_omni,
                      "the merged ROOT providers block lost the per-run headers")
        for key in (self.agent.SESSION_TAG_HEADER, self.agent.RUN_ID_HEADER):
            self.assertIn(key, foreign_omni["headers"])
            self.assertIn(key, autoos_omni["headers"])
        self.assertEqual(set(foreign_omni["headers"]), set(autoos_omni["headers"]))
        for plan, omni in ((foreign_plan, foreign_omni),
                           (autoos_plan, autoos_omni)):
            self.assertEqual(
                omni["headers"][self.agent.SESSION_TAG_HEADER],
                self.agent.session_header_value(plan["session_tag"],
                                                plan["run_id"]))
            self.assertEqual(omni["headers"][self.agent.RUN_ID_HEADER],
                             plan["run_id"])

    def test_foreign_headers_survive_stamp_worker_gateway(self):
        """D-426: the loopback stamp must not move or drop the session pair."""
        plan = self._build()
        env = dict(plan["env"])
        with mock.patch.object(self.agent, "gateway_base_url",
                               return_value="http://gw.example.invalid:1/v1"):
            self.assertTrue(self.agent.stamp_worker_gateway(env))
        omni = json.loads(env["OPENCODE_CONFIG_CONTENT"])["providers"]["omniroute"]
        self.assertEqual(
            omni["headers"][self.agent.SESSION_TAG_HEADER],
            self.agent.session_header_value(plan["session_tag"], plan["run_id"]))
        self.assertEqual(omni["headers"][self.agent.RUN_ID_HEADER], plan["run_id"])
        # the plan's own copy is stamped only from the worker env onward, and
        # keeps the same pair
        self.assertEqual(
            json.loads(plan["env"]["OPENCODE_CONFIG_CONTENT"])
            ["providers"]["omniroute"]["headers"],
            omni["headers"])


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


class RootOverlayBlocksTests(unittest.TestCase):
    """root_overlay_blocks copies only providers + permissions."""

    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent()

    def test_returns_only_providers_and_permissions(self):
        cfg = {"agents": {"a": {}}, "providers": {"p": {"env": ["X"]}},
               "permissions": [{"action": "shell", "effect": "deny"}],
               "mcp": {"m": {}}, "tools": {}, "experimental": {}, "model": "m"}
        self.assertEqual(sorted(self.agent.root_overlay_blocks(cfg)),
                         ["permissions", "providers"])

    def test_empty_when_absent(self):
        self.assertEqual(self.agent.root_overlay_blocks({}), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
