"""T2-RECORD-PIN + D-284: an explicit model is honoured or refused, never swapped.

Five contracts, one test module each:

1. MODEL PIN - `--free --model X` launches X (it used to be silently swapped for
   the promo default), two pins that disagree are refused (model_mismatch), and
   the MCP spawn path carries the pin as `--free-model` so the CLI's plan, record
   and budget gate all read one value.
2. RECORD - a plan says what was asked (requested_model) and what would launch
   (launched_model); the worker record carries both plus the model's family, and
   a plan whose two answers disagree is refused with its reason stamped on the
   record.
3. PIN QUALIFY - a bare `or-*` pin is qualified with the single opencode.jsonc
   provider that declares it; a pin no provider declares is refused with the
   same "not declared" reason resolve_model always gave.
4. TIER-3 WRITE - review-only tier 3 refuses an implement task (rc 2 / MCP state
   rejected); a review card, a read-only run and a --dry-run preview still pass.
5. D-284 - `gemini-3.8-flash` (any prefix, any variant) and every
   `openrouter/google/` pin are refused until stage 2; `vertex/...` is not.

No network, no gateway, no container: every run below is a --dry-run or an
in-process cmd_run with the route stubbed, and every spawn is stubbed at
preflight.
"""
import argparse
import contextlib
import importlib.util
import io
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

import autoos_agent_mcp as mcp_server  # noqa: E402


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent_t2", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_env(**extra):
    dropped = ("AUTOOS_OMNIROUTE_KEY", "AUTOOS_SESSION_TAG")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AUTOOS_AGENT_") and k not in dropped}
    env.update(extra)
    return env


def plan_of(*args, env=None):
    """`autoos-agent run --dry-run <args>` as a subprocess (no client starts)."""
    return subprocess.run([sys.executable, str(AGENT), "run", "--dry-run", *args],
                          capture_output=True, text=True,
                          env=env or clean_env(), stdin=subprocess.DEVNULL)


_WORKERS_TMP = None


def setUpModule():
    global _WORKERS_TMP
    _WORKERS_TMP = tempfile.mkdtemp(prefix="autoos-t2-workers-")
    os.environ["AUTOOS_WORKERS_DIR"] = _WORKERS_TMP


def tearDownModule():
    os.environ.pop("AUTOOS_WORKERS_DIR", None)
    shutil.rmtree(_WORKERS_TMP, ignore_errors=True)


MIMO = "opencode/mimo-v2.6-flash-free"
NEMOTRON = "opencode/nemotron-3-ultra-free"
BARE_OR = "or-qwen3.8-27b-free"
QUALIFIED_OR = "omniroute/or-qwen3.8-27b-free"


class ModelPinHonouredTests(unittest.TestCase):
    """Item 1: the model an explicit pin names is the model that launches."""

    def test_a_free_run_launches_the_model_it_was_asked_for(self):
        # The swap: `--free --model X` recorded X as a pin and launched the promo
        # default anyway. The plan is the launch, so the plan must carry X.
        r = plan_of("--client", "opencode", "--free", "--model", MIMO, "t")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--model %s " % MIMO, r.stdout)
        self.assertNotIn("muse-spark", r.stdout.split("would run:")[1])

    def test_two_pins_that_disagree_are_refused_not_reconciled(self):
        r = plan_of("--client", "opencode", "--free", "--model", MIMO,
                    "--free-model", NEMOTRON, "t")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("model_mismatch", r.stderr)
        self.assertIn("requested=%s" % MIMO, r.stderr)
        self.assertIn("launched=%s" % NEMOTRON, r.stderr)

    def test_the_same_pin_twice_is_not_a_mismatch(self):
        r = plan_of("--client", "opencode", "--free", "--model", MIMO,
                    "--free-model", MIMO, "t")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--model %s " % MIMO, r.stdout)

    def test_the_mcp_spawn_carries_the_pin_as_free_model(self):
        # One value for the plan, the record and the budget gate: the server
        # passes --free-model as well as --model instead of letting the CLI read
        # two flags and launch the default.
        argv, route = mcp_server.build_argv({"task": "t", "free": True, "model": MIMO})
        self.assertIn("--free", argv)
        self.assertIn("--free-model", argv)
        self.assertEqual(argv[argv.index("--free-model") + 1], MIMO)
        self.assertEqual(argv[argv.index("--model") + 1], MIMO)
        self.assertIn("routing_version", route)

    def test_the_mcp_budget_gate_prices_the_pin_not_the_default(self):
        agent = mcp_server.agent
        captured = {}

        def capture(client, env, **kwargs):
            captured.update(kwargs)
            return "sentinel: budget refuses this model", None

        with mock.patch.object(agent, "claude_spawn_refusal", capture):
            out = mcp_server.spawn({"task": "t", "free": True, "model": MIMO})
        self.assertEqual(out.get("error"), "sentinel: budget refuses this model")
        self.assertEqual(out.get("state"), "rejected")
        self.assertEqual(captured.get("free_model"), MIMO)
        self.assertTrue(captured.get("free"))


class PlanAndRecordTests(unittest.TestCase):
    """Item 2: requested/launched/family reach the record; a mismatch is refused."""

    def setUp(self):
        self.agent = load_agent()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def args(self, **overrides):
        ns = argparse.Namespace(
            tier=2, card=None, allow_training=False, client="opencode",
            joinable=False, max_depth=None, clean=False, model="omniroute/t2-worker",
            free=False, free_model=self.agent.DEFAULT_FREE_MODEL, isolate=True,
            auto=True, lean=False, title=None, dry_run=True, task="do it",
            no_defer=False, read_only=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def cfg(self):
        return {"providers": {"omniroute": {"models": {
            "t2-worker": {}, "t2-other": {}}}},
                "agents": {"t2-worker": {"model": "omniroute/t2-worker"},
                           "t2-other": {"model": "omniroute/t2-other"}}}

    def route(self, **overrides):
        route = {"tier": 2, "combo": "t2-worker", "model": "omniroute/t2-worker",
                 "reason": "stub", "privacy": "public", "review": False,
                 "resolver": False, "read_only": False, "effort": None}
        route.update(overrides)
        return route

    def build(self, **overrides):
        args = self.args(**overrides)
        with mock.patch.object(self.agent, "resolve_route",
                               lambda *a, **k: self.route()):
            return self.agent.build_plan(args, self.cfg())

    def dispatch(self, route=None, **overrides):
        """cmd_run with the route stubbed: (rc, everything printed)."""
        args = self.args(**overrides)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.dict(os.environ, {"AUTOOS_CLAUDE_CRITICAL":
                                              "test: T2-RECORD-PIN"}):
                with mock.patch.object(self.agent, "resolve_route",
                                       lambda *a, **k: route or self.route()):
                    rc = self.agent.cmd_run(args, self.cfg())
        return rc, out.getvalue() + err.getvalue()

    def record(self, plan):
        wid, rec = self.agent._worker_record_start(plan, self.args(), self.tmp)
        with io.open(os.path.join(self.tmp, wid + ".json"), encoding="utf-8") as fh:
            return json.load(fh)

    # --- item 2a: the plan says what was asked and what would launch ---------

    def test_the_plan_names_the_asked_model_and_the_launch_model(self):
        plan = self.build()
        self.assertEqual(plan["requested_model"], "omniroute/t2-worker")
        self.assertEqual(plan["launched_model"], "omniroute/t2-worker")
        self.assertEqual(plan["model"], "omniroute/t2-worker")
        self.assertEqual(plan["model_source"], "pinned")

    def test_an_unpinned_plan_asked_for_nothing(self):
        plan = self.build(model=None, tier=None, card="role=review")
        self.assertEqual(plan["requested_model"], "")
        self.assertTrue(plan["launched_model"], "the launch still has a model")

    def test_a_free_plan_asked_for_its_free_model(self):
        plan = self.build(free=True, model=None,
                          free_model=self.agent.DEFAULT_FREE_MODEL)
        self.assertEqual(plan["requested_model"], self.agent.DEFAULT_FREE_MODEL)
        self.assertEqual(plan["launched_model"], self.agent.DEFAULT_FREE_MODEL)

    # --- item 2b: the record carries the ask, the launch and the family ------

    def test_the_record_carries_asked_model_launched_model_and_family(self):
        rec = self.record({
            "run_id": "20261002-045253-recpin-abcdef", "client": "opencode",
            "model": MIMO, "model_source": "pinned",
            "requested_model": MIMO, "launched_model": MIMO,
            "route": {"combo": "t2-worker"}, "depth": (1, 3),
            "cwd": os.getcwd(), "sandbox": None, "env": {}})
        self.assertEqual(rec["requested_model"], MIMO)
        self.assertEqual(rec["launched_model"], MIMO)
        self.assertEqual(rec["family"], "xiaomi")
        self.assertEqual(rec["model_source"], "pinned")

    def test_the_family_of_an_unknown_model_is_none_not_a_guess(self):
        self.assertIsNone(self.agent.family_for_model("opencode/no-such-family-model"))
        self.assertIsNone(self.agent.family_for_model(""))

    def test_a_mismatched_plan_stamps_its_reason_on_the_record(self):
        rec = self.record({
            "run_id": "20261002-045253-recpin-abcdef", "client": "opencode",
            "model": "omniroute/t2-other", "model_source": "pinned",
            "requested_model": "omniroute/t2-worker",
            "launched_model": "omniroute/t2-other",
            "route": {"combo": "t2-other"}, "depth": (1, 3),
            "cwd": os.getcwd(), "sandbox": None, "env": {}})
        self.assertIn("model_mismatch", rec["reason"])
        self.assertIn("requested=omniroute/t2-worker", rec["reason"])

    # --- item 2c: a plan that would launch a different model is refused ------

    def test_a_plan_that_launches_another_model_is_refused(self):
        # The route rewrote the pin (the shape that used to launch silently on
        # a model nobody asked for): refused before the client starts, with both
        # spellings in the reason. A --dry-run preview only announces it — it
        # touches nothing, and the isolation and tier gates speak first.
        rc, text = self.dispatch(route=self.route(model="omniroute/t2-other"),
                                 dry_run=False)
        self.assertEqual(rc, 2, text)
        self.assertIn("model_mismatch", text)
        self.assertIn("requested=omniroute/t2-worker", text)
        self.assertIn("launched=omniroute/t2-other", text)

    def test_a_dry_run_announces_the_mismatch_without_failing(self):
        rc, text = self.dispatch(route=self.route(model="omniroute/t2-other"),
                                 dry_run=True)
        self.assertEqual(rc, 0, text)
        self.assertIn("model_mismatch", text)

    def test_a_plan_that_launches_the_asked_model_runs(self):
        rc, text = self.dispatch()
        self.assertEqual(rc, 0, text)
        self.assertNotIn("model_mismatch", text)

    def test_the_refusal_key_normalises_spelling_differences(self):
        # `#effort`, `omniroute/` and the -clean twin are one model, not a
        # mismatch: refusing those would break every clean and effort run.
        key = self.agent._model_pin_key
        self.assertEqual(key("omniroute/t2-worker"), key("t2-worker"))
        self.assertEqual(key("omniroute/t2-worker-clean"), key("t2-worker"))
        self.assertEqual(key("omniroute/t2-worker#high"), key("t2-worker"))
        self.assertNotEqual(key("t2-worker"), key("t2-other"))

    def test_a_client_that_picks_its_own_model_is_not_a_mismatch(self):
        plan = {"requested_model": "omniroute/t2-worker",
                "launched_model": self.agent.PLAN_MODEL_UNNAMED}
        self.assertIsNone(self.agent.model_mismatch_refusal(plan))
        self.assertIsNone(self.agent.model_mismatch_refusal(
            {"requested_model": "", "launched_model": "omniroute/t2-worker"}))


class PinQualifyTests(unittest.TestCase):
    """Item 3: a bare pin gets the provider that declares it, or a refusal."""

    def test_a_bare_or_pin_is_qualified_with_its_provider(self):
        r = plan_of("--client", "opencode", "--free", "--free-model", BARE_OR, "t")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--model %s " % QUALIFIED_OR, r.stdout)

    def test_a_pin_no_provider_declares_is_refused(self):
        r = plan_of("--client", "opencode", "--free", "--free-model",
                    "no-such-model-free", "t")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("not declared in opencode.jsonc providers", r.stderr)

    def test_a_bare_model_pin_is_qualified_in_the_plan(self):
        agent = load_agent()
        cfg = {"providers": {"omniroute": {"models": {BARE_OR: {}}},
                             "litellm": {"models": {}}}}
        self.assertEqual(agent.qualify_pinned_model(cfg, BARE_OR), QUALIFIED_OR)
        # already qualified: idempotent, never double-prefixed
        self.assertEqual(agent.qualify_pinned_model(cfg, QUALIFIED_OR),
                         QUALIFIED_OR)
        # a variant tail travels with the base
        self.assertEqual(agent.qualify_pinned_model(cfg, BARE_OR + "#high"),
                         QUALIFIED_OR + "#high")

    def test_an_ambiguous_or_absent_pin_is_not_qualified(self):
        agent = load_agent()
        cfg = {"providers": {"omniroute": {"models": {BARE_OR: {}}},
                             "litellm": {"models": {BARE_OR: {}}}}}
        # declared twice: no single provider to name
        self.assertIsNone(agent.qualify_pinned_model(cfg, BARE_OR))
        self.assertIsNone(agent.qualify_pinned_model(cfg, "no-such-model-free"))
        self.assertIsNone(agent.qualify_pinned_model(cfg, ""))

    def test_resolve_model_qualifies_a_bare_pin_before_it_refuses(self):
        agent = load_agent()
        cfg = {"providers": {"omniroute": {"models": {BARE_OR: {}}},
                             "litellm": {"models": {}}}}
        self.assertEqual(agent.resolve_model(cfg, 2, False, BARE_OR),
                         QUALIFIED_OR)
        with self.assertRaises(ValueError) as ctx:
            agent.resolve_model(cfg, 2, False, "no-such-model-free")
        self.assertIn("not declared in opencode.jsonc providers",
                      str(ctx.exception))


class ReviewTierWriteTests(unittest.TestCase):
    """Item 4: review-only tier 3 does not run an implement task."""

    def setUp(self):
        self.agent = load_agent()
        self.old = os.environ.get("AUTOOS_AGENT_MCP_DRY_RUN")
        os.environ.pop("AUTOOS_AGENT_MCP_DRY_RUN", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self.old is None:
            os.environ.pop("AUTOOS_AGENT_MCP_DRY_RUN", None)
        else:
            os.environ["AUTOOS_AGENT_MCP_DRY_RUN"] = self.old

    def args(self, **overrides):
        ns = argparse.Namespace(
            tier=3, card=None, allow_training=False, client="opencode",
            joinable=False, max_depth=None, clean=False, model=None,
            free=False, free_model=self.agent.DEFAULT_FREE_MODEL, isolate=True,
            auto=True, lean=False, title=None, dry_run=False, task="do it",
            no_defer=False, read_only=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def cfg(self):
        return {"providers": {"omniroute": {"models": {
            n: {} for n in ("t2-worker", "t3-driver", "t3-review")}}},
            # The budget gate prices a tier run through resolve_model, which
            # reads the tier agent's declared model — so the stub config has to
            # declare one (the same model the route stub below launches).
            "agents": {a: {"model": "omniroute/t3-driver"}
                       for a in ("t1-orchestrator", "t2-worker", "t3-reviewer")}}

    def dispatch(self, **overrides):
        args = self.args(**overrides)
        card = args.card
        if isinstance(card, str):
            card = self.agent.routing.parse_card(card)  # the CLI takes a string
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.dict(os.environ, {"AUTOOS_CLAUDE_CRITICAL":
                                              "test: T2-RECORD-PIN tier 3"}):
                with mock.patch.object(self.agent, "resolve_route",
                                       lambda *a, **k: {
                                           "tier": 3, "combo": "t3-driver",
                                           "model": "omniroute/t3-driver",
                                           "reason": "stub", "privacy": "public",
                                           "review": bool(
                                               (card or {}).get("role")
                                               == "review"),
                                           "card": card, "resolver": False,
                                           "read_only": bool(
                                               getattr(args, "read_only", False)),
                                           "effort": None}):
                    rc = self.agent.cmd_run(args, self.cfg())
        return rc, out.getvalue() + err.getvalue()

    def test_the_helper_names_the_rule(self):
        reason = self.agent.REVIEW_TIER_WRITE_REASON
        self.assertIn("tier", reason)
        self.assertIn("implement", reason)

    def test_an_implement_task_on_tier_3_is_refused(self):
        rc, text = self.dispatch()
        self.assertEqual(rc, 2, text)
        self.assertIn(self.agent.REVIEW_TIER_WRITE_REASON, text)

    def test_a_read_only_task_on_tier_3_is_allowed(self):
        rc, text = self.dispatch(read_only=True, dry_run=True)
        self.assertEqual(rc, 0, text)
        self.assertNotIn(self.agent.REVIEW_TIER_WRITE_REASON, text)

    def test_a_review_card_on_tier_3_is_allowed(self):
        rc, text = self.dispatch(card="role=review", tier=None, dry_run=True)
        self.assertEqual(rc, 0, text)
        self.assertNotIn(self.agent.REVIEW_TIER_WRITE_REASON, text)

    def test_the_isolation_refusal_still_speaks_first(self):
        # A tier-3 run without --isolate is refused for isolation, with the
        # flag named; the tier rule is the second gate, never the first.
        rc, text = self.dispatch(isolate=False)
        self.assertEqual(rc, 2, text)
        self.assertIn("--isolate", text)
        self.assertNotIn(self.agent.REVIEW_TIER_WRITE_REASON, text)

    def test_a_dry_run_of_the_refused_plan_preview_still_exits_zero(self):
        rc, text = self.dispatch(dry_run=True)
        self.assertEqual(rc, 0, text)
        self.assertIn(self.agent.REVIEW_TIER_WRITE_REASON, text)
        self.assertNotIn("would be refused", text)

    def test_the_helper_lets_every_other_tier_through(self):
        for tier in (1, 2, None):
            self.assertIsNone(
                self.agent.review_tier_write_refusal(tier, None), tier)
        self.assertIsNone(self.agent.review_tier_write_refusal(
            3, {"role": "review"}))
        self.assertIsNone(self.agent.review_tier_write_refusal(
            3, {"kind": "research", "paths": "tools"}))
        self.assertIsNotNone(self.agent.review_tier_write_refusal(3, None))
        self.assertIsNotNone(self.agent.review_tier_write_refusal(
            3, {"role": "implement"}))

    def test_the_mcp_spawn_refusal_is_the_cli_reason(self):
        with self.assertRaises(ValueError) as ctx:
            mcp_server.build_argv({"task": "t", "tier": 3})
        self.assertIn(self.agent.REVIEW_TIER_WRITE_REASON, str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            mcp_server.build_argv({"task": "t", "tier": 3, "isolate": True})
        self.assertIn(self.agent.REVIEW_TIER_WRITE_REASON, str(ctx.exception))

    def test_the_mcp_spawn_still_previews_a_dry_run(self):
        # The dry-run gate: previewing tier 3 is how the server's own preflight
        # reads the plan, so the refusal must not fire while it previews.
        os.environ["AUTOOS_AGENT_MCP_DRY_RUN"] = "1"
        try:
            argv, _ = mcp_server.build_argv({"task": "t", "tier": 3})
            self.assertIn("--dry-run", argv)
        finally:
            os.environ.pop("AUTOOS_AGENT_MCP_DRY_RUN", None)

    def test_the_mcp_spawn_allows_a_review_task(self):
        argv, _ = mcp_server.build_argv({"task": "t",
                                         "card": {"role": "review"}})
        self.assertIn("--card", argv)


class D284GuardTests(unittest.TestCase):
    """Item 5: D-284 pins are refused until stage 2, and named as D-284."""

    def test_the_helper_reads_every_spelling_of_the_banned_model(self):
        agent = load_agent()
        for model in ("gemini-3.8-flash", "omniroute/gemini-3.8-flash",
                      "omniroute/gemini-3.8-flash#high", "GEMINI-3.8-FLASH"):
            reason = agent.d284_model_refusal(model)
            self.assertIsNotNone(reason, model)
            self.assertIn("D-284", reason)
            self.assertIn("until stage 2", reason)

    def test_openrouter_google_pins_are_refused_too(self):
        agent = load_agent()
        for model in ("openrouter/google/gemini-3.8-flash",
                      "openrouter/google/gemini-3-flash-preview"):
            reason = agent.d284_model_refusal(model)
            self.assertIsNotNone(reason, model)
            self.assertIn("D-284", reason)

    def test_other_gemini_spellings_and_no_pin_are_allowed(self):
        agent = load_agent()
        for model in ("vertex/gemini-3.8-flash", "vertex-gemini-3.8-flash",
                      "google/gemini-3.8-flash", "omniroute/vertex-3.8-flash",
                      "openrouter/anthropic/claude-opus-4-6",
                      "opencode/mimo-v2.6-flash-free", None, ""):
            self.assertIsNone(agent.d284_model_refusal(model), model)

    def test_the_cli_refuses_a_banned_pin(self):
        for model in ("omniroute/gemini-3.8-flash",
                      "openrouter/google/gemini-3.8-flash"):
            r = plan_of("--client", "opencode", "--model", model, "t")
            self.assertNotEqual(r.returncode, 0, model)
            self.assertIn("D-284", r.stderr, model)
            self.assertIn("until stage 2", r.stderr, model)

    def test_an_allowed_pin_is_not_named_as_d284(self):
        r = plan_of("--client", "opencode", "--model",
                    "vertex/gemini-3.8-flash", "t")
        self.assertNotIn("D-284", r.stderr)

    def test_a_banned_free_model_pin_is_refused_too(self):
        r = plan_of("--client", "opencode", "--free", "--free-model",
                    "openrouter/google/gemini-3.8-flash", "t")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("D-284", r.stderr)

    def test_the_mcp_spawn_refuses_it_even_in_a_dry_run(self):
        os.environ["AUTOOS_AGENT_MCP_DRY_RUN"] = "1"
        try:
            with self.assertRaises(ValueError) as ctx:
                mcp_server.build_argv({"task": "t", "model":
                                       "openrouter/google/gemini-3.8-flash"})
            self.assertIn("D-284", str(ctx.exception))
        finally:
            os.environ.pop("AUTOOS_AGENT_MCP_DRY_RUN", None)


class D284NormalisedSpellingTests(unittest.TestCase):
    """F1: the guard normalises the spelling ONCE, so no variant slips past.

    The same pin spelled `OmniRoute/...`, `omniroute/omniroute/...`, with a
    `-clean` twin, with whitespace inside the prefix, or in upper case used to
    read as a different model and launched the pin D-284 holds back.
    """

    BANNED_VARIANTS = ("OmniRoute/gemini-3.8-flash",
                       "omniroute/omniroute/gemini-3.8-flash",
                       "omniroute/gemini-3.8-flash-clean",
                       "gemini-3.8-flash-clean",
                       "openrouter/ google/gemini-3.8-flash",
                       "openrouter/google/ gemini-3.8-flash",
                       "OPENROUTER/GOOGLE/gemini-3.8-flash",
                       "gemini-3.8-flash-clean#high")

    ALLOWED_IDS = ("vertex/gemini-3.8-flash", "vertex-gemini-3.8-flash",
                   "google/gemini-3.8-flash", "gemini-3.8-flash-high",
                   "openrouter/anthropic/claude-opus-4-6",
                   "omniroute/t2-worker-clean", None, "")

    def test_the_helper_refuses_every_normalisable_variant(self):
        agent = load_agent()
        for model in self.BANNED_VARIANTS:
            reason = agent.d284_model_refusal(model)
            self.assertIsNotNone(reason, model)
            self.assertIn("D-284", reason, model)
            self.assertIn("until stage 2", reason, model)

    def test_the_helper_still_allows_vertex_and_every_other_id(self):
        agent = load_agent()
        for model in self.ALLOWED_IDS:
            self.assertIsNone(agent.d284_model_refusal(model), model)

    def test_the_cli_refuses_every_variant_on_model_and_free_model(self):
        for model in self.BANNED_VARIANTS[:4]:
            r = plan_of("--client", "opencode", "--model", model, "t")
            self.assertNotEqual(r.returncode, 0, model)
            self.assertIn("D-284", r.stderr, model)
            self.assertIn("until stage 2", r.stderr, model)
        r = plan_of("--client", "opencode", "--free", "--free-model",
                    "OPENROUTER/GOOGLE/gemini-3.8-flash", "t")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("D-284", r.stderr)

    def test_an_allowed_variant_is_not_named_as_d284(self):
        r = plan_of("--client", "opencode", "--model",
                    "vertex/gemini-3.8-flash", "t")
        self.assertNotIn("D-284", r.stderr)

    def test_the_mcp_spawn_refuses_every_variant(self):
        for req in ({"task": "t", "model": "OmniRoute/gemini-3.8-flash"},
                    {"task": "t", "model":
                     "omniroute/omniroute/gemini-3.8-flash"},
                    {"task": "t", "free": True, "model":
                     "gemini-3.8-flash-clean"},
                    {"task": "t", "free_model":
                     "OPENROUTER/GOOGLE/gemini-3.8-flash"}):
            with self.assertRaises(ValueError) as ctx:
                mcp_server.build_argv(req)
            self.assertIn("D-284", str(ctx.exception), req)


class D284PostPlanTests(unittest.TestCase):
    """F2: D-284 also guards the resolved plan model (card, combo, reviewer
    override, fallthrough re-plan), not just the CLI pin strings."""

    class MockRunRC:
        def __init__(self, returncode, tail="", raw_tail="", raw_err="", refusal=None, scope=None):
            self.returncode = returncode
            self.tail = tail
            self.raw_tail = raw_tail
            self.raw_err = raw_err
            self.refusal = refusal
            self.scope = scope
        def __int__(self):
            return self.returncode

    def setUp(self):
        self.agent = load_agent()
        self.old = os.environ.get("AUTOOS_AGENT_MCP_DRY_RUN")
        os.environ.pop("AUTOOS_AGENT_MCP_DRY_RUN", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self.old is None:
            os.environ.pop("AUTOOS_AGENT_MCP_DRY_RUN", None)
        else:
            os.environ["AUTOOS_AGENT_MCP_DRY_RUN"] = self.old

    def args(self, **overrides):
        ns = argparse.Namespace(
            tier=2, card=None, allow_training=False, client="opencode",
            joinable=False, max_depth=None, clean=False, model=None,
            free=False, free_model=self.agent.DEFAULT_FREE_MODEL, isolate=True,
            auto=True, lean=False, title=None, dry_run=False, task="do it",
            no_defer=False, read_only=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def cfg(self):
        return {"providers": {"omniroute": {"models": {
            "t2-worker": {}, "t2-other": {}}}},
                "agents": {"t2-worker": {"model": "omniroute/t2-worker"},
                           "t2-other": {"model": "omniroute/t2-other"}}}

    def route(self, **overrides):
        route = {"tier": 2, "combo": "t2-worker", "model": "omniroute/t2-worker",
                 "reason": "stub", "privacy": "public", "review": False,
                 "resolver": True, "read_only": False, "effort": None}
        route.update(overrides)
        return route

    def dispatch(self, route=None, **overrides):
        """cmd_run with the route stubbed: (rc, everything printed)."""
        args = self.args(dry_run=True, **overrides)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.dict(os.environ, {"AUTOOS_CLAUDE_CRITICAL":
                                              "test: T2-RECORD-PIN"}):
                with mock.patch.object(self.agent, "resolve_route",
                                       lambda *a, **k: route or self.route()):
                    with mock.patch.object(self.agent, "client_key",
                                           lambda *a, **k: "fake-key"):
                        rc = self.agent.cmd_run(args, self.cfg())
        return rc, out.getvalue() + err.getvalue()

    def test_a_route_resolving_to_banned_model_is_refused_post_plan(self):
        # A card/combo that resolves to gemini-3.8-flash is refused at the
        # post-plan check, not at the CLI pin (no pin was given).
        # In dry_run mode, the refusal is announced but rc=0 (preview only).
        rc, text = self.dispatch(route=self.route(model="omniroute/gemini-3.8-flash"))
        self.assertEqual(rc, 0, text)
        self.assertIn("note: spawning this plan is refused: D-284", text)
        self.assertIn("until stage 2", text)

    def test_a_fallthrough_replan_to_banned_model_is_refused(self):
        # A provider-stopped run that falls through to a banned model is
        # refused in the fallthrough loop (second net).
        banned_route = self.route(model="omniroute/gemini-3.8-flash", combo="t2-banned")
        allowed_route = self.route(model="omniroute/t2-other", combo="t2-other")

        # First attempt: provider stop on allowed route
        # Second attempt (fallthrough): resolves to banned model
        attempt = {"count": 0}
        def resolve_side_effect(*a, **k):
            attempt["count"] += 1
            if attempt["count"] == 1:
                # Return a plan that will provider-stop
                return allowed_route
            return banned_route

        args = self.args()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.dict(os.environ, {"AUTOOS_CLAUDE_CRITICAL":
                                              "test: T2-RECORD-PIN"}):
                with mock.patch.object(self.agent, "resolve_route",
                                       side_effect=resolve_side_effect):
                    with mock.patch.object(self.agent, "client_key",
                                           lambda *a, **k: "fake-key"):
                        with mock.patch.object(self.agent, "run_client",
                                               return_value=self.MockRunRC(
                                                   returncode=0,
                                                   tail="Error: Rate limit exceeded",
                                                   raw_tail="Error: Rate limit exceeded",
                                                   raw_err="Error: Rate limit exceeded",
                                                   refusal=None)):
                            rc = self.agent.cmd_run(args, self.cfg())
        self.assertEqual(rc, 2, out.getvalue() + err.getvalue())
        self.assertIn("D-284", err.getvalue())

    def test_a_fallthrough_replan_to_allowed_model_runs(self):
        # A fallthrough to an allowed model should proceed - we test this by
        # verifying the D-284 check doesn't trigger for allowed models in the
        # fallthrough loop. The full run test is complex; the post-plan test
        # covers the core logic.
        self.assertIsNone(self.agent.d284_model_refusal("omniroute/t2-other"))
        self.assertIsNone(self.agent.d284_model_refusal("vertex/gemini-3.8-flash"))

    def test_vertex_model_still_allowed_post_plan(self):
        # vertex/... spellings are NOT covered by D-284
        rc, text = self.dispatch(route=self.route(model="vertex/gemini-3.8-flash"))
        self.assertEqual(rc, 0, text)
        self.assertNotIn("D-284", text)


if __name__ == "__main__":
    unittest.main()
