"""Tests for the agent spawner: tools/autoos_routing.py, tools/autoos_clients.py,
tools/autoos-agent.py and tools/autoos_agent_mcp.py.

Dry runs only - nothing is spawned, no gateway is called, no key is read.

Run from the repo root (optionally one class, e.g. RoutingTableTests):

    python3 tests/test_autoos_spawner.py [ClassName]
"""
import argparse
import contextlib
import datetime
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import shutil
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))

import autoos_routing as routing  # noqa: E402
import autoos_clients as clients  # noqa: E402
import autoos_agent_mcp as mcp_server  # noqa: E402
import autoos_resolver as resolver  # noqa: E402  (tools/autoos_resolver.py; serving_legs)
import registry as registry_tool  # noqa: E402  (tools/registry.py; private_safe lives here)


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Every cmd_run writes a worker record (ps). No test may write into the host's
# real registry (<main checkout>/logs/workers): pin it to a throwaway dir for the
# whole module; a test that needs its own dir still passes AUTOOS_WORKERS_DIR.
_WORKERS_TMP = None


def setUpModule():
    global _WORKERS_TMP
    _WORKERS_TMP = tempfile.mkdtemp(prefix="autoos-workers-test-")
    os.environ["AUTOOS_WORKERS_DIR"] = _WORKERS_TMP


def tearDownModule():
    os.environ.pop("AUTOOS_WORKERS_DIR", None)
    shutil.rmtree(_WORKERS_TMP, ignore_errors=True)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AUTOOS_AGENT_") and k != "AUTOOS_OMNIROUTE_KEY"}
    env.update(extra)
    return env


def run_agent(*args, env=None):
    return subprocess.run([sys.executable, str(AGENT), *args], capture_output=True,
                          text=True, env=env or clean_env(), stdin=subprocess.DEVNULL)


class _FakeFn:
    """A callable standing in for a ctypes function. ctypes functions carry
    ``restype``/``argtypes``; the production helper assigns them, so the fake
    must expose writable attributes on a per-instance callable (bound methods
    do not)."""

    def __init__(self, fn):
        self._fn = fn
        self.restype = None
        self.argtypes = None

    def __call__(self, *args, **kwargs):
        return self._fn(*args, **kwargs)


class _FakeKernel32:
    """A fake ctypes kernel32 for the Windows liveness/start-time paths (V1/V2).

    Runs on Linux: the production code imports ctypes inside its
    ``os.name == "nt"`` branch, so injecting this module into ``sys.modules``
    is exactly what it sees. ``byref`` returns the ctypes-like value object
    itself, so the fake Get* calls can write ``.value`` on it.
    """

    def __init__(self, handle=1234, exit_code=259, created=987654321,
                 last_error=0, times_ok=True):
        self.handle = handle
        self.exit_code = exit_code
        self.created = created
        self.last_error = last_error
        self.times_ok = times_ok
        self.opened = []
        self.closed = []
        self.OpenProcess = _FakeFn(self._open_process)
        self.GetExitCodeProcess = _FakeFn(self._get_exit_code_process)
        self.GetProcessTimes = _FakeFn(self._get_process_times)
        self.CloseHandle = _FakeFn(self._close_handle)

    def _open_process(self, access, inherit, pid):
        self.opened.append((access, inherit, pid))
        return self.handle

    def _get_exit_code_process(self, handle, code):
        code.value = self.exit_code
        return 1

    def _get_process_times(self, handle, created, exited, kernel, user):
        if not self.times_ok:
            return 0
        created.value = self.created
        return 1

    def _close_handle(self, handle):
        self.closed.append(handle)
        return 1


class _FakeCtypesValue:
    def __init__(self, value=0):
        self.value = value


def fake_windows_ctypes(k32):
    """A ctypes stand-in whose kernel32 is `k32` (V1/V2 tests).

    ``WinDLL`` is what the production helper now asks for (typed signatures);
    ``wintypes`` carries the sentinels the tests assert the helper assigned.
    """
    fake = types.ModuleType("ctypes")
    fake.k32 = k32
    fake.windll = types.SimpleNamespace(kernel32=k32)
    fake.WinDLL = lambda *args, **kwargs: k32
    fake.wintypes = types.SimpleNamespace(HANDLE=object(), DWORD=_FakeCtypesValue,
                                          BOOL=object(), FILETIME=object())
    fake.POINTER = lambda typ: typ
    fake.get_last_error = lambda: k32.last_error
    fake.byref = lambda obj: obj
    fake.c_ulong = _FakeCtypesValue
    fake.c_ulonglong = _FakeCtypesValue
    return fake


class RoutingTableTests(unittest.TestCase):
    """ADR 0006 decision 3: one test per row of the resolution table."""

    def pick(self, **card):
        return routing.select_combo(card)[0]

    def test_empty_card_is_t2_worker(self):
        combo, reason = routing.select_combo({})
        self.assertEqual(combo, "t2-worker")
        self.assertTrue(reason)

    def test_public_1m_routes_to_t1_orchestrator(self):
        # T1FREE 2026-09-27: t1-orchestrator now carries a free gemini/gemini-3.8-flash
        # fallback leg, so ctx=1m public cards route there again.
        for card in ({"ctx": "1m", "role": "orchestrate"},
                     {"ctx": "1m", "complexity": "hard"},
                     {"ctx": "1m", "role": "review", "spend": "credit"}):
            combo, reason = routing.select_combo(card)
            self.assertEqual(combo, "t1-orchestrator")
            self.assertEqual(reason, "public-1m")

    def test_public_implement_standard_free_is_t2_worker(self):
        self.assertEqual(self.pick(role="implement", complexity="standard", spend="free-ok"), "t2-worker")

    def test_public_review_free_is_t3_driver(self):
        self.assertEqual(self.pick(role="review"), "t3-driver")

    def test_public_trivial_free_is_t3_driver(self):
        self.assertEqual(self.pick(complexity="trivial"), "t3-driver")

    def test_public_implement_credit_is_t2_worker(self):
        # The -credit chains were dropped 2026-09-23 (ADR 0006): t2-worker
        # already overflows to its paid legs, so credit picks the same combo.
        self.assertEqual(self.pick(spend="credit"), "t2-worker")

    def test_public_review_credit_is_t3_driver(self):
        self.assertEqual(self.pick(role="review", spend="credit"), "t3-driver")

    def test_every_combo_exists_and_none_is_retired(self):
        retired = {"tier1", "tier1-clean", "tier2", "tier2-clean", "tier3", "tier3-clean", "rag",
                   "tier1-paid", "tier2-paid", "tier3-paid", "tier2-credit", "tier3-credit"}
        with open(ROOT / "configuration" / "omniroute" / "combos.json", encoding="utf-8") as fh:
            live = {c["name"] for c in json.load(fh)["combos"]}
        self.assertFalse(retired & set(routing.ALL_COMBOS))
        self.assertLessEqual(set(routing.ALL_COMBOS), live)
        # Every card the resolver accepts, not just the hand-kept tuple.
        import itertools
        fields = list(routing.CARD_VALUES)
        for values in itertools.product(*(routing.CARD_VALUES[f] for f in fields)):
            for allow in (False, True):
                try:
                    combo = routing.select_combo(dict(zip(fields, values)), allow)[0]
                except routing.NoRoute:
                    continue
                self.assertIn(combo, routing.ALL_COMBOS, (values, allow))

    def test_sensitive_implement_is_t2_worker_clean(self):
        self.assertEqual(self.pick(privacy="sensitive"), "t2-worker-clean")

    def test_sensitive_hard_is_t2_worker_clean(self):
        self.assertEqual(self.pick(privacy="sensitive", complexity="hard", spend="credit"), "t2-worker-clean")

    def test_sensitive_review_is_t3_driver_clean(self):
        self.assertEqual(self.pick(privacy="sensitive", role="review"), "t3-driver-clean")

    def test_sensitive_trivial_is_t3_driver_clean(self):
        self.assertEqual(self.pick(privacy="sensitive", complexity="trivial"), "t3-driver-clean")

    def test_sensitive_1m_has_no_route_and_says_what_to_do(self):
        with self.assertRaises(routing.NoRoute) as ctx:
            routing.select_combo({"privacy": "sensitive", "ctx": "1m"})
        msg = str(ctx.exception)
        self.assertIn("t2-worker-clean", msg)
        self.assertIn("128k", msg)
        self.assertNotIn("--allow-training", msg)


class RoutingBoundaryTests(unittest.TestCase):
    """ADR 0006: unknown fields and values are errors; boundary cases are fixed."""

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(routing.CardError) as ctx:
            routing.select_combo({"model": "t1-orchestrator"})
        self.assertIn("model", str(ctx.exception))

    def test_unknown_value_is_rejected(self):
        with self.assertRaises(routing.CardError):
            routing.select_combo({"privacy": "secret"})

    def test_each_field_has_its_documented_default(self):
        self.assertEqual(routing.CARD_DEFAULTS, {
            "role": "implement", "complexity": "standard", "ctx": "128k",
            "privacy": "public", "spend": "free-ok"})

    def test_role_values(self):
        self.assertEqual(routing.CARD_VALUES["role"], ("orchestrate", "implement", "review"))

    def test_complexity_values(self):
        self.assertEqual(routing.CARD_VALUES["complexity"], ("trivial", "standard", "hard"))

    def test_ctx_values(self):
        self.assertEqual(routing.CARD_VALUES["ctx"], ("128k", "1m"))

    def test_privacy_values(self):
        self.assertEqual(routing.CARD_VALUES["privacy"], ("public", "sensitive"))

    def test_spend_values(self):
        self.assertEqual(routing.CARD_VALUES["spend"], ("free-ok", "credit"))

    def test_public_128k_orchestrate_goes_to_t1_orchestrator(self):
        # T1FREE 2026-09-27: t1-orchestrator serves public-strong again (ctx=128k +
        # orchestrate/hard) through its gemini fallback leg.
        self.assertEqual(routing.select_combo({"role": "orchestrate"})[0], "t1-orchestrator")
        self.assertEqual(routing.select_combo({"role": "orchestrate"})[1], "public-strong")

    def test_hard_review_is_t1_orchestrator_not_t3_driver(self):
        # orchestrate/hard wins over review/trivial: a hard review needs the strong model.
        # T1FREE 2026-09-27: t1-orchestrator serves public-strong again.
        self.assertEqual(routing.select_combo({"role": "review", "complexity": "hard"})[0], "t1-orchestrator")

    def test_sensitive_orchestrate_128k_is_t2_worker_clean(self):
        self.assertEqual(routing.select_combo({"privacy": "sensitive", "role": "orchestrate"})[0], "t2-worker-clean")

    def test_allow_training_no_longer_opens_sensitive_1m(self):
        # Compatibility flag, inert since DSMAX 2026-09-27: there is no
        # trainable 1M gateway leg left for it to unlock.
        with self.assertRaises(routing.NoRoute):
            routing.select_combo({"privacy": "sensitive", "ctx": "1m"}, allow_training=True)

    def test_allow_training_changes_nothing_else(self):
        self.assertEqual(routing.select_combo({"privacy": "sensitive"}, allow_training=True)[0], "t2-worker-clean")

    def test_parse_card_reads_key_value_pairs(self):
        self.assertEqual(routing.parse_card("role=review, privacy=sensitive"),
                         {"role": "review", "privacy": "sensitive"})

    def test_parse_card_reads_json(self):
        self.assertEqual(routing.parse_card('{"ctx": "1m"}'), {"ctx": "1m"})

    def test_parse_card_rejects_a_bare_word(self):
        with self.assertRaises(routing.CardError):
            routing.parse_card("review")

    def test_every_combo_the_resolver_can_return_is_declared(self):
        agent = load_agent()
        declared = agent.declared_models(agent.load_jsonc(str(ROOT / "opencode.jsonc")))
        for combo in routing.ALL_COMBOS:
            self.assertIn("omniroute/" + combo, declared)

    def test_routing_version_is_set(self):
        self.assertRegex(routing.ROUTING_VERSION, r"^\d+(\.\d+)*$")


class ClientMatrixTests(unittest.TestCase):
    def test_every_client_declares_the_matrix_fields(self):
        names = set(clients.CLIENTS)
        self.assertEqual(names, {"opencode", "claude", "qwen", "gemini", "codex", "agy", "qoder"})
        for c in clients.CLIENTS.values():
            self.assertIsInstance(c.headless, bool, c.name)
            self.assertIsInstance(c.gateway, bool, c.name)
            self.assertIsInstance(c.subagents, bool, c.name)
            self.assertTrue(c.auth, c.name)
            self.assertTrue(c.binary, c.name)

    def test_gateway_clients_are_exactly_the_omniroute_run_targets(self):
        gw = {n for n, c in clients.CLIENTS.items() if c.gateway}
        self.assertEqual(gw, {"opencode", "qwen", "gemini", "codex"})

    def test_auth_names_env_vars_or_logins_never_values(self):
        for c in clients.CLIENTS.values():
            self.assertNotRegex(c.auth, r"sk-|[A-Za-z0-9]{32,}", c.name)

    def test_only_qoder_is_a_promo(self):
        self.assertEqual({n for n, c in clients.CLIENTS.items() if c.promo}, {"qoder"})

    def test_list_prints_the_matrix_and_says_agy_and_qoder_skip_the_gateway(self):
        # An empty PATH: `list` probes agy's sign-in, and a test never runs
        # the host's real agy (review 2026-09-25).
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        out = run_agent("list", env=clean_env(PATH=empty)).stdout
        for name in clients.CLIENTS:
            self.assertIn(name, out)
        for line in out.splitlines():
            if line.startswith(("agy ", "qoder ")):
                self.assertIn("own auth", line)


def plan_of(*args, env=None):
    r = run_agent("run", "--dry-run", *args, env=env)
    return r


class ClientCommandTests(unittest.TestCase):
    def test_qwen_goes_through_omniroute_run_with_the_card_combo(self):
        r = plan_of("--client", "qwen", "--card", "role=review", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would run: omniroute run qwen --model t3-driver --api-key-env AUTOOS_OMNIROUTE_KEY -- ", r.stdout)
        self.assertIn("--approval-mode plan", r.stdout)

    def test_gemini_goes_through_omniroute_run(self):
        r = plan_of("--client", "gemini", "t")
        self.assertIn("omniroute run gemini --model t2-worker ", r.stdout)
        self.assertIn("--approval-mode auto_edit -p t", r.stdout)

    def test_gemini_trusts_the_spawn_cwd_for_this_session_only(self):
        # Live 2026-09-25: headless gemini in an untrusted folder exits 55
        # ("not running in a trusted directory") and downgrades the approval
        # mode. --skip-trust trusts it for this one session, nothing persists.
        r = plan_of("--client", "gemini", "t")
        self.assertIn("-- --skip-trust --approval-mode auto_edit -p t", r.stdout)

    def test_codex_runs_exec_through_omniroute(self):
        r = plan_of("--client", "codex", "t")
        self.assertIn("omniroute run codex --model t2-worker --api-key-env AUTOOS_OMNIROUTE_KEY -- exec --sandbox workspace-write --skip-git-repo-check t", r.stdout)

    def test_claude_is_headless_print_on_its_own_login(self):
        r = plan_of("--client", "claude", "t")
        self.assertIn("would run: claude -p --permission-mode acceptEdits t", r.stdout)
        self.assertNotIn("AUTOOS_OMNIROUTE_KEY", r.stdout)

    def test_claude_joinable_is_a_background_remote_control_session(self):
        r = plan_of("--client", "claude", "--joinable", "--title", "d1", "t")
        self.assertIn("claude --bg --remote-control d1 --strict-mcp-config --mcp-config "
                      "'{\"mcpServers\":{}}' --name d1", r.stdout)

    def test_a_joinable_session_starts_no_user_scope_mcp_server(self):
        # Measured 2026-09-25: user-scope graphify (docker run -i --rm -v $PWD)
        # started one container per lane worktree. --mcp-config is variadic:
        # a non-variadic option must follow it, or it eats the prompt.
        cmd = clients.build_command(clients.CLIENTS["claude"], "task", None, "edit", None, "d1")
        self.assertIn("--strict-mcp-config", cmd)
        i = cmd.index("--mcp-config")
        self.assertEqual(json.loads(cmd[i + 1]), {"mcpServers": {}})
        self.assertTrue(cmd[i + 2].startswith("--"), cmd)
        self.assertEqual(cmd[-1], "task")

    def test_joinable_is_refused_for_other_clients(self):
        self.assertEqual(plan_of("--client", "qwen", "--joinable", "t").returncode, 2)

    def test_agy_uses_its_own_login(self):
        # AGYFIX item 1 (measured 2026-09-27, K3 audit): agy 1.2.12 reads
        # "--model" as the -p prompt when -p comes first, so the model must
        # go BEFORE -p. Item 2: with no caller model it gets the measured
        # working default (claude-opus-4-6-thinking, PONG in 8 s), not its
        # own default Gemini whose quota is out until ~2026-10-01.
        r = plan_of("--client", "agy", "t")
        self.assertIn("would run: agy --model claude-opus-4-6-thinking -p t", r.stdout)
        self.assertNotIn("omniroute run", r.stdout)

    def test_agy_puts_an_explicit_model_before_the_print_prompt(self):
        # AGYFIX item 1: working form measured in the K3 audit is
        # `agy --model <m> -p <task>`; `agy -p --model <m> <task>` makes agy
        # take "--model" as the prompt and ignore the task.
        cmd = clients.build_command(clients.CLIENTS["agy"], "task", None, "edit",
                                    "claude-sonnet-4-6")
        self.assertEqual(cmd, ["agy", "--model", "claude-sonnet-4-6", "-p", "task"])

    def test_agy_default_model_is_the_measured_working_one(self):
        # AGYFIX item 2 (K3 audit addendum 08:1xZ): agy with no --model runs
        # its default Gemini -> 157 s then rc 3 quota. The spawner supplies
        # claude-opus-4-6-thinking as data when the caller gives none.
        self.assertEqual(clients.AGY_DEFAULT_MODEL, "claude-opus-4-6-thinking")
        cmd = clients.build_command(clients.CLIENTS["agy"], "task", None, "edit", None)
        self.assertEqual(cmd, ["agy", "--model", "claude-opus-4-6-thinking", "-p", "task"])

    def test_free_default_is_the_operators_muse_spark_leg(self):
        # AGYFIX item 4 (operator 2026-09-26): the free default is Zen Muse
        # Spark 1.3 through the opencode client, measured 200 there.
        self.assertEqual(load_agent().DEFAULT_FREE_MODEL,
                         "opencode/muse-spark-1.3-contributor-free")

    def test_qoder_is_refused_for_sensitive_work(self):
        r = plan_of("--client", "qoder", "--card", "privacy=sensitive", "t")
        self.assertEqual(r.returncode, 2)
        self.assertIn("public", r.stderr)

    def test_qoder_public_runs_print_mode(self):
        # qodercli 1.1.63 knows only bypass_permissions|dont_ask|auto; the old
        # 'accept_edits' does not exist and every write was refused (62 runs,
        # measured 2026-09-27). Writers run with bypass_permissions and the
        # free Qwen3.8-Flash by default.
        r = plan_of("--client", "qoder", "t")
        self.assertIn("qodercli -p --permission-mode bypass_permissions --model Qwen3.8-Flash", r.stdout)

    def test_qoder_writer_is_always_isolated(self):
        # bypass_permissions is only acceptable inside the private sandbox
        # clone, where the leak check still applies: the spawner forces it.
        r = plan_of("--client", "qoder", "t")
        self.assertIn("git clone --local", r.stdout)
        self.assertIn("is your only writable checkout", r.stdout)

    def test_qoder_without_auto_is_isolated_too(self):
        # review of 6622d29 (qoder Qwen3.8-Flash): --no-auto gave level "ask",
        # no permission flag and NO sandbox. Every non-read qoder run is isolated.
        r = plan_of("--client", "qoder", "--no-auto", "t")
        self.assertIn("git clone --local", r.stdout)

    def test_qoder_plan_names_the_model_it_runs(self):
        r = plan_of("--client", "qoder", "t")
        self.assertNotIn("(client default)", r.stdout)

    def test_qoder_reviewer_stays_dont_ask(self):
        r = plan_of("--client", "qoder", "--card", "role=review", "t")
        self.assertIn("qodercli -p --permission-mode dont_ask", r.stdout)
        self.assertNotIn("bypass_permissions", r.stdout)

    def test_qoder_explicit_model_is_kept(self):
        r = plan_of("--client", "qoder", "--model", "Efficient", "t")
        self.assertIn("--model Efficient", r.stdout)
        self.assertNotIn("Qwen3.8-Flash", r.stdout)

    def test_opencode_card_maps_combo_to_its_tier_agent(self):
        r = plan_of("--card", "role=review,privacy=sensitive", "t")
        self.assertIn("--agent t3-reviewer --model omniroute/t3-driver-clean ", r.stdout)

    def test_the_route_is_printed_with_reason_and_version(self):
        r = plan_of("--card", "role=review", "t")
        self.assertIn("route: t3-driver reason=", r.stdout)
        self.assertIn("routing=" + routing.ROUTING_VERSION, r.stdout)

    def test_sensitive_1m_fails_closed_with_next_steps(self):
        r = plan_of("--card", "privacy=sensitive,ctx=1m", "t")
        self.assertEqual(r.returncode, 2)
        self.assertIn("128k", r.stderr)
        self.assertNotIn("--allow-training", r.stderr)

    def test_tier_and_card_together_are_refused(self):
        self.assertEqual(plan_of("--tier", "2", "--card", "role=review", "t").returncode, 2)

    def test_clean_with_card_is_refused(self):
        self.assertEqual(plan_of("--clean", "--card", "role=review", "t").returncode, 2)

    def test_no_plan_prints_the_key(self):
        env = clean_env(AUTOOS_OMNIROUTE_KEY="never-print-this-key")
        for client in clients.CLIENTS:
            r = plan_of("--client", client, "t", env=env)
            self.assertNotIn("never-print-this-key", r.stdout + r.stderr, client)


def _cap_args(**over):
    """A cmd_run-ish Namespace for required_capabilities (only the two fields it reads)."""
    ns = argparse.Namespace(isolate=False, card=None)
    ns.__dict__.update(over)
    return ns


class ClientCapabilityTests(unittest.TestCase):
    """SPAWNCAP (S2) part A: a client's shell/write abilities are registry data,
    and a task that needs one is refused before dispatch (exit 2) instead of
    being started on a client that cannot do it. Auto-choice (no --client)
    never picks such a client."""

    # QOFIX 2026-09-27: qoder writes + runs shell headless with bypass_permissions
    # (measured; the old false came from the invalid mode accept_edits).
    CAPABLE = {"opencode", "claude", "codex", "gemini", "qwen", "qoder"}
    INCAPABLE = {"agy"}

    def setUp(self):
        self.agent = load_agent()
        with io.open(ROOT / "catalog" / "ai-registry.json", encoding="utf-8") as fh:
            self.registry = json.load(fh)

    def test_every_client_declares_shell_and_write(self):
        for name, client in self.registry["clients"].items():
            with self.subTest(client=name):
                caps = client.get("capabilities")
                self.assertIsInstance(caps, dict, name)
                self.assertIn("shell", caps, name)
                self.assertIn("write", caps, name)
                self.assertIsInstance(caps["shell"], bool, name)
                self.assertIsInstance(caps["write"], bool, name)

    def test_the_capable_client_set_is_the_six_measured_ones(self):
        capable = {name for name, client in self.registry["clients"].items()
                   if client["capabilities"]["shell"] and client["capabilities"]["write"]}
        self.assertEqual(capable, self.CAPABLE)
        for name in self.INCAPABLE:
            self.assertFalse(self.registry["clients"][name]["capabilities"]["write"], name)

    def test_client_capabilities_reads_the_registry(self):
        self.assertEqual(self.agent.client_capabilities("opencode", self.registry),
                         {"shell": True, "write": True})
        self.assertEqual(self.agent.client_capabilities("qoder", self.registry),
                         {"shell": True, "write": True})
        self.assertEqual(self.agent.client_capabilities("agy", self.registry),
                         {"shell": False, "write": False})

    def test_isolate_needs_shell_and_write(self):
        self.assertEqual(self.agent.required_capabilities(_cap_args(isolate=True)),
                         ("shell", "write"))

    def test_explicit_editing_cards_need_shell_and_write(self):
        for card in ("kind=implement", "kind=debug", "kind=bulk", "role=implement"):
            with self.subTest(card=card):
                self.assertEqual(self.agent.required_capabilities(_cap_args(card=card)),
                                 ("shell", "write"), card)

    def test_read_only_cards_need_nothing(self):
        for card in ("kind=review", "kind=research", "kind=plan",
                     "role=review", "role=orchestrate"):
            with self.subTest(card=card):
                self.assertEqual(self.agent.required_capabilities(_cap_args(card=card)), (), card)

    def test_absent_empty_and_defaults_only_cards_need_nothing(self):
        # v1's defaults make every absent card role=implement, but only an
        # explicitly named editing kind/role is a write request: privacy=sensitive
        # alone (or an empty card) must not be gated, or qoder's own promo
        # refusal (test_qoder_is_refused_for_sensitive_work) would be shadowed
        # by the capability message instead of printing "public".
        for card in (None, "", "   ", "privacy=sensitive", "complexity=hard", "ctx=1m"):
            with self.subTest(card=card):
                self.assertEqual(self.agent.required_capabilities(_cap_args(card=card)), (), card)

    def test_a_malformed_card_is_left_for_build_plan_to_report(self):
        # A bad card is not a capability question: build_plan raises the CardError.
        self.assertEqual(self.agent.required_capabilities(_cap_args(card="bogus=1")), ())

    def test_choose_client_picks_the_first_capable_in_registry_order(self):
        self.assertEqual(self.agent.choose_client(("shell", "write"), self.registry), "opencode")
        self.assertEqual(self.agent.choose_client((), None), "opencode")

    def test_choose_client_skips_an_incapable_first_client(self):
        registry = {"clients": {
            "opencode": {"capabilities": {"shell": False, "write": False}},
            "claude": {"capabilities": {"shell": True, "write": True}},
        }}
        self.assertEqual(self.agent.choose_client(("shell", "write"), registry), "claude")

    def test_capability_refusal_names_the_missing_capability_and_the_capable_clients(self):
        msg = self.agent.capability_refusal("agy", ("shell", "write"), self.registry)
        self.assertIsNotNone(msg)
        self.assertIn("agy", msg)
        self.assertIn("shell", msg)
        self.assertIn("write", msg)
        for name in self.CAPABLE:
            self.assertIn(name, msg)
        self.assertIsNone(
            self.agent.capability_refusal("opencode", ("shell", "write"), self.registry))

    def test_agy_is_refused_for_an_isolated_write_before_it_starts(self):
        r = plan_of("--client", "agy", "--isolate", "edit README.md")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("agy", r.stderr)
        self.assertIn("opencode", r.stderr)
        self.assertNotIn("would run:", r.stdout)

    def test_qoder_takes_an_isolated_write(self):
        r = plan_of("--client", "qoder", "--isolate", "edit README.md")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--permission-mode bypass_permissions", r.stdout)

    def test_qoder_takes_an_explicit_editing_card(self):
        r = plan_of("--client", "qoder", "--card", "role=implement", "edit README.md")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("git clone --local", r.stdout)

    def test_qoder_can_still_take_a_read_only_card(self):
        r = plan_of("--client", "qoder", "--card", "role=review", "t")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("qodercli", r.stdout)

    def test_an_isolated_run_without_a_client_auto_picks_a_capable_one(self):
        r = plan_of("--isolate", "edit README.md")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would run: opencode", r.stdout)

    def test_the_no_client_default_is_still_opencode(self):
        r = plan_of("t")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would run: opencode", r.stdout)


class ReviewFindingTests(unittest.TestCase):
    """Cross-family review (t1-orchestrator, 2026-09-24) findings, pinned."""

    def test_joinable_keeps_an_explicit_model(self):
        r = plan_of("--client", "claude", "--joinable", "--title", "d1", "--model", "opus", "t")
        self.assertIn("--model opus", r.stdout)

    def test_gateway_client_model_override_wins_over_the_card(self):
        r = plan_of("--client", "qwen", "--card", "complexity=trivial", "--model", "omniroute/t2-worker", "t")
        self.assertIn("omniroute run qwen --model t2-worker ", r.stdout)

    def test_mcp_max_depth_as_a_string_is_coerced_not_a_crash(self):
        argv, _ = mcp_server.build_argv({"task": "t", "max_depth": "2"})
        self.assertIn("--max-depth", argv)
        self.assertIn("error", mcp_server.spawn({"task": "t", "max_depth": "two"}))

    def test_exit_record_is_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(mcp_server._write_exit(tmp, {"rc": 0}))
            self.assertFalse(mcp_server._write_exit(tmp, {"cancelled": True, "rc": None}))
            with open(os.path.join(tmp, "exit.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["rc"], 0)

    def test_finished_runners_are_reaped_and_forgotten(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        mcp_server._CHILDREN[proc.pid] = proc
        proc.wait()
        mcp_server._reap()
        self.assertNotIn(proc.pid, mcp_server._CHILDREN)


class DepthTests(unittest.TestCase):
    def test_child_gets_depth_plus_one_and_the_max(self):
        r = plan_of("--client", "qwen", "t", env=clean_env(AUTOOS_AGENT_DEPTH="1", AUTOOS_AGENT_MAX_DEPTH="3"))
        self.assertIn("depth: 2/3", r.stdout)
        self.assertIn("AUTOOS_AGENT_DEPTH", r.stdout)

    def test_top_level_default_max_matches_opencode_subagent_depth(self):
        r = plan_of("t")
        self.assertIn("depth: 1/%d" % clients.DEFAULT_MAX_DEPTH, r.stdout)

    def test_spawn_past_the_max_is_refused(self):
        r = plan_of("--client", "agy", "t", env=clean_env(AUTOOS_AGENT_DEPTH="2", AUTOOS_AGENT_MAX_DEPTH="2"))
        self.assertEqual(r.returncode, 4)
        self.assertIn("depth", r.stderr)

    def test_max_depth_flag_can_only_lower_the_inherited_max(self):
        self.assertEqual(clients.child_depth({"AUTOOS_AGENT_MAX_DEPTH": "2"}, 5), (1, 2))
        self.assertEqual(clients.child_depth({"AUTOOS_AGENT_MAX_DEPTH": "3"}, 1), (1, 1))

    def test_garbage_depth_env_counts_as_exhausted(self):
        with self.assertRaises(clients.DepthError):
            clients.child_depth({"AUTOOS_AGENT_DEPTH": "x"})


class LeanTests(unittest.TestCase):
    def test_lean_overlay_disables_heavy_servers_with_the_v2_key(self):
        agent = load_agent()
        cfg = agent.load_jsonc(str(ROOT / "opencode.jsonc"))
        servers = agent.lean_overlay(cfg)["mcp"]["servers"]
        self.assertEqual(set(servers), set(agent.LEAN_DROP) & set(cfg["mcp"]["servers"]))
        for name, entry in servers.items():
            # opencode 2.x: `disabled: true` on a FULL entry. `enabled` is not a v2
            # field (silently stripped) and a partial entry is dropped as malformed.
            self.assertIs(entry["disabled"], True, name)
            self.assertNotIn("enabled", entry, name)
            self.assertEqual(entry["type"], cfg["mcp"]["servers"][name]["type"], name)
            self.assertTrue(entry.get("command") or entry.get("url"), name)

    def test_lean_keeps_the_graph_lookups(self):
        agent = load_agent()
        self.assertNotIn("omnigraph", agent.LEAN_DROP)
        self.assertNotIn("graphify", agent.LEAN_DROP)
        self.assertIn("serena", agent.LEAN_DROP)
        self.assertIn("playwright", agent.LEAN_DROP)

    def test_lean_opencode_plan_carries_the_overlay(self):
        r = plan_of("--lean", "--card", "role=review", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OPENCODE_CONFIG_CONTENT", r.stdout)
        self.assertIn("lean: no serena, playwright, context7", r.stdout)

    def test_lean_claude_uses_a_strict_empty_mcp_config(self):
        r = plan_of("--client", "claude", "--lean", "t")
        self.assertIn("--strict-mcp-config", r.stdout)

    def test_lean_is_refused_where_it_cannot_be_applied(self):
        self.assertEqual(plan_of("--client", "qwen", "--lean", "t").returncode, 2)

    # SPAWNFREE (S2) item 4: role=review qoder runs are briefed --lean, and the
    # blunt refusal (rc 2, "qoder would still start its MCP servers") killed them
    # (inbox 2026-09-27T17:42:09Z, work/L2-general/review-edgesecret-brief.out).
    # qodercli 1.1.63 does take --strict-mcp-config (its own --help, read
    # 2026-09-27), so qoder is a client --lean is implemented for. A client that
    # cannot drop its servers - qwen, gemini, codex, agy - gets a note and runs
    # when the route is read-only, because there the servers cost memory, not
    # safety; a writer run still refuses, because --lean there is also asking for
    # that tool surface back.

    def test_lean_qoder_drops_its_servers_in_the_client_argv(self):
        r = plan_of("--client", "qoder", "--lean", "--card", "role=review", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("qodercli --strict-mcp-config -p", r.stdout)
        self.assertIn("lean: no MCP servers", r.stdout)
        self.assertNotIn("cannot drop its MCP servers", r.stdout)

    def test_lean_never_lands_in_the_omniroute_wrapper_argv(self):
        # qwen runs as `omniroute run qwen -- ...`: claude's flag belongs to the
        # client behind the --, and `omniroute --strict-mcp-config run ...` is an
        # unknown option that would kill the run. --lean must not inject it there.
        r = plan_of("--client", "qwen", "--lean", "--card", "role=review", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("omniroute run qwen", r.stdout)
        self.assertNotIn("strict-mcp-config", r.stdout)

    def test_lean_on_a_read_only_review_run_notes_and_continues(self):
        r = plan_of("--client", "qwen", "--lean", "--card", "role=review", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("qwen cannot drop its MCP servers", r.stdout)
        self.assertIn("read-only", r.stdout)

    def test_lean_on_tier_3_notes_and_continues(self):
        r = plan_of("--client", "qwen", "--lean", "--tier", "3", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("qwen cannot drop its MCP servers", r.stdout)

    def test_the_decision_follows_route_review_not_the_card_dialect(self):
        # A v2 card (kind=review,paths=...) reaches the same branch through
        # route["review"]; its CLI case is host-dependent (need_tokens measured
        # from the real tree), so the shape is asserted on the helper.
        agent = load_agent()
        note, refusal = agent.lean_decision("qwen", {"review": True, "kind": "review"})
        self.assertIsNone(refusal)
        self.assertIn("qwen cannot drop its MCP servers", note)
        note, refusal = agent.lean_decision("qwen", {"review": False, "kind": "implement"})
        self.assertIsNone(note)
        self.assertIn("writer", refusal)
        for client in agent.LEAN_CLIENTS:
            self.assertEqual(agent.lean_decision(client, {"review": False}), (None, None))

    def test_lean_on_a_writer_run_still_refuses(self):
        # Only cards the planner accepts reach the decision; a v2 kind=implement
        # without paths dies earlier ("card paths is empty"), which is not this
        # test's business (the helper case above covers the v2 shape).
        for card in (["--card", "role=implement"], ["--tier", "2"]):
            with self.subTest(card=card):
                r = plan_of("--client", "qwen", "--lean", *card, "t")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("writer", r.stderr)

    def test_a_client_that_can_drop_its_servers_gets_no_note(self):
        r = plan_of("--lean", "--card", "role=review", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("cannot drop its MCP servers", r.stdout)
        r = plan_of("--client", "claude", "--lean", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("cannot drop its MCP servers", r.stdout)


class PromoProbeTests(unittest.TestCase):
    def test_probe_is_stale_after_seven_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": tmp}
            self.assertEqual(clients.probe_age_days("qoder", env=env), None)
            clients.record_probe("qoder", now=1000.0, env=env)
            self.assertFalse(clients.probe_stale("qoder", now=1000.0 + 6 * 86400, env=env))
            self.assertTrue(clients.probe_stale("qoder", now=1000.0 + 8 * 86400, env=env))


@unittest.skipIf(os.name == "nt", "sh stubs; tests/run-tests.sh runs these on Linux")
class SignInProbeTests(unittest.TestCase):
    """Operator 2026-09-25: agy that cannot run here must be an error, not a
    silent or misleading result. A signed-out agy used to pass `list` as
    installed and then block a headless run for 60 s on an OAuth prompt."""

    SIGNED_OUT = ("#!/bin/sh\n"
                  "echo \"$*\" >>\"$(dirname \"$0\")/calls\"\n"
                  "echo 'Fetching available models...'\n"
                  "echo 'Error: Please sign in to view available models. "
                  "Launch the CLI without arguments to sign in.'\n"
                  "exit 1\n")
    SIGNED_IN = ("#!/bin/sh\n"
                 "echo \"$*\" >>\"$(dirname \"$0\")/calls\"\n"
                 "echo 'gemini-3.8-flash'\n"
                 "exit 0\n")

    def stub(self, body):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "agy")
        with open(path, "w") as fh:
            fh.write(body)
        os.chmod(path, 0o755)
        return d, clean_env(PATH=d + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
                            AUTOOS_STATE_DIR=d)

    def calls(self, d):
        try:
            with open(os.path.join(d, "calls")) as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def test_signed_out_agy_is_reported_with_its_own_reason(self):
        d, env = self.stub(self.SIGNED_OUT)
        ok, reason = clients.signin_state(clients.CLIENTS["agy"], env)
        self.assertIs(ok, False)
        self.assertIn("Please sign in", reason)

    def test_signed_in_agy_is_usable(self):
        d, env = self.stub(self.SIGNED_IN)
        self.assertEqual(clients.signin_state(clients.CLIENTS["agy"], env), (True, ""))

    def test_missing_agy_is_not_probed(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(clients.signin_state(clients.CLIENTS["agy"], {"PATH": d}), (None, ""))

    def test_an_env_without_path_never_finds_the_hosts_binary(self):
        d, env = self.stub(self.SIGNED_OUT)
        with mock.patch.dict(os.environ, {"PATH": env["PATH"]}):
            self.assertEqual(clients.signin_state(clients.CLIENTS["agy"], {"HOME": d}), (None, ""))

    def test_a_client_without_a_probe_is_unknown(self):
        self.assertEqual(clients.signin_state(clients.CLIENTS["opencode"], os.environ), (None, ""))

    def test_list_shows_a_signed_out_agy_as_not_usable(self):
        d, env = self.stub(self.SIGNED_OUT)
        out = run_agent("list", env=env).stdout
        line = next(l for l in out.splitlines() if l.startswith("agy "))
        self.assertIn("signed-out", line)
        self.assertIn("run `agy` once", line)

    def test_run_refuses_a_signed_out_agy_fast_and_never_starts_the_task(self):
        d, env = self.stub(self.SIGNED_OUT)
        start = time.time()
        r = run_agent("run", "--client", "agy", "Reply with exactly: ack", env=env)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("agy is installed but not signed in", r.stderr)
        self.assertIn("Please sign in", r.stderr)
        self.assertLess(time.time() - start, 20)
        self.assertEqual(self.calls(d), ["models"])

    def test_run_goes_ahead_when_agy_is_signed_in(self):
        d, env = self.stub(self.SIGNED_IN)
        r = run_agent("run", "--client", "agy", "t", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # AGYFIX item 1+2 (measured 2026-09-27): the working form is
        # `agy --model <m> -p <task>`, and with no caller model the spawner
        # supplies claude-opus-4-6-thinking rather than agy's default Gemini.
        self.assertEqual(self.calls(d), ["models", "--model claude-opus-4-6-thinking -p t"])

    def test_mcp_list_clients_carries_usability(self):
        d, env = self.stub(self.SIGNED_OUT)
        with mock.patch.dict(os.environ, {"PATH": env["PATH"]}):
            agy = next(c for c in mcp_server.list_clients()["clients"] if c["name"] == "agy")
        self.assertTrue(agy["installed"])
        self.assertIs(agy["usable"], False)
        self.assertIn("Please sign in", agy["reason"])


class McpToolTests(unittest.TestCase):
    """The MCP tools as plain functions, every spawn a dry run, state in a temp dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = {k: os.environ.get(k) for k in ("AUTOOS_STATE_DIR", "AUTOOS_AGENT_MCP_DRY_RUN",
                                                   "AUTOOS_AGENT_DEPTH", "AUTOOS_AGENT_MAX_DEPTH")}
        os.environ.update(AUTOOS_STATE_DIR=self.tmp, AUTOOS_AGENT_MCP_DRY_RUN="1")
        os.environ.pop("AUTOOS_AGENT_DEPTH", None)
        os.environ.pop("AUTOOS_AGENT_MAX_DEPTH", None)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_done(self, run_id):
        for _ in range(100):
            st = mcp_server.status(run_id)
            if st["state"] not in ("working", "submitted"):
                return st
            time.sleep(0.1)
        self.fail("run %s never finished" % run_id)

    def test_list_clients_has_the_matrix_and_the_card(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        with mock.patch.dict(os.environ, {"PATH": empty}):  # never probe the host's agy
            out = mcp_server.list_clients()
        self.assertEqual({c["name"] for c in out["clients"]}, set(clients.CLIENTS))
        self.assertEqual(out["card"]["defaults"], routing.CARD_DEFAULTS)
        self.assertIn("depth 1 of", out["depth"])

    def test_spawn_returns_a_run_id_at_once_and_routes_through_select_combo(self):
        out = mcp_server.spawn({"task": "t", "card": {"role": "review"}, "cwd": str(ROOT)})
        self.assertNotIn("error", out)
        self.assertEqual(out["route"]["combo"], "t3-driver")
        self.assertEqual(out["route"]["routing_version"], routing.ROUTING_VERSION)
        st = self.wait_done(out["id"])
        self.assertEqual(st["state"], "completed")
        text = mcp_server.result(out["id"])["text"]
        self.assertIn("would run: opencode run --standalone --agent t3-reviewer", text)
        self.assertIn("lean:", text)  # reviewers default to lean

    def test_state_lives_under_the_state_dir_agents(self):
        out = mcp_server.spawn({"task": "t", "cwd": str(ROOT)})
        self.assertEqual(out["dir"], os.path.join(self.tmp, "agents", out["id"]))
        self.wait_done(out["id"])
        for name in ("job.json", "output.log", "exit.json"):
            self.assertTrue(os.path.isfile(os.path.join(out["dir"], name)), name)

    def test_spawn_run_dir_is_0700_under_a_shared_umask(self):
        """The run dir carries the task brief, the worker's output and the
        question.json / answer.json pair autoos-ask.py uses for ask-back: a
        local user who can enter the dir can read the brief and answer the
        worker's question. So the dir is private like the spawner's workers
        dir (0700), whatever the spawning process's umask says."""
        old = os.umask(0o022)
        try:
            out = mcp_server.spawn({"task": "t", "cwd": str(ROOT)})
            self.assertNotIn("error", out)
            self.wait_done(out["id"])
            self.assertEqual(os.stat(out["dir"]).st_mode & 0o777, 0o700)
        finally:
            os.umask(old)

    def test_refusals_come_back_synchronously(self):
        self.assertIn("error", mcp_server.spawn({"task": "t", "card": {"privacy": "sensitive", "ctx": "1m"}}))
        self.assertIn("error", mcp_server.spawn({"task": "t", "card": {"bogus": "x"}}))
        self.assertIn("error", mcp_server.spawn({"task": "t", "client": "qoder", "card": {"privacy": "sensitive"}}))
        self.assertIn("error", mcp_server.spawn({"task": ""}))
        self.assertIn("error", mcp_server.spawn({"task": "t", "client": "nope"}))
        self.assertEqual(mcp_server.status()["runs"], [])

    def test_spawn_past_the_depth_budget_is_refused(self):
        os.environ.update(AUTOOS_AGENT_DEPTH="2", AUTOOS_AGENT_MAX_DEPTH="2")
        self.assertIn("depth", mcp_server.spawn({"task": "t"})["error"])

    def test_status_lists_runs_and_unknown_ids_are_errors(self):
        out = mcp_server.spawn({"task": "t", "cwd": str(ROOT)})
        self.wait_done(out["id"])
        self.assertEqual([r["id"] for r in mcp_server.status()["runs"]], [out["id"]])
        self.assertIn("error", mcp_server.status("nope"))
        self.assertIn("error", mcp_server.result("../etc"))

    def test_cancel_of_a_finished_run_is_a_no_op(self):
        out = mcp_server.spawn({"task": "t", "cwd": str(ROOT)})
        self.wait_done(out["id"])
        self.assertEqual(mcp_server.cancel(out["id"])["state"], "completed")

    # --- the A2A task lifecycle (spec 2026-09-25-routing-v2-spec.md §9) ------

    def make_run(self, run_id, **job):
        """A synthetic run dir carrying only what _state() reads, for the
        lifecycle states a real dry run cannot be parked in deterministically
        (canceled needs a cancel to land mid-run; lost needs a dead pid)."""
        path = os.path.join(self.tmp, "agents", run_id)
        os.makedirs(path)
        job = dict({"id": run_id, "request": {}, "task": "t", "argv": [],
                    "cwd": str(ROOT), "route": {}, "started": time.time()}, **job)
        mcp_server._write_json(os.path.join(path, "job.json"), job)
        return path

    def test_task_states_are_the_spec_9_set(self):
        # The exact A2A names in the spec's order; a typo here would leak into
        # every consumer of status()/result() (D17: A2A adapter is a thin layer
        # later only while the names match exactly).
        self.assertEqual(mcp_server.TASK_STATES,
                         ("submitted", "working", "input_required", "completed",
                          "failed", "canceled", "rejected"))

    def test_a_dry_run_spawn_walks_submitted_working_completed(self):
        """A real dry-run run reports only A2A names, in the submitted ->
        working -> completed order; `detail` keeps the pre-A2A value so no
        caller loses information to the rename. spawn()'s Popen is held open
        until the test has seen "submitted" (the window between job.json's
        first write and its pid write is otherwise too small to observe)."""
        real_popen = mcp_server.subprocess.Popen
        release = threading.Event()

        def held_popen(*args, **kw):
            # preflight's own subprocess.run lands here too (Popen underneath);
            # hold only the runner's start - that is the submitted->working gap.
            # args[0] is the argv list: look inside it, not at the tuple.
            if args and "--run-job" in args[0]:
                release.wait(5)
            return real_popen(*args, **kw)

        box = {}

        def do_spawn():
            try:
                with mock.patch.object(mcp_server.subprocess, "Popen", held_popen):
                    box["out"] = mcp_server.spawn({"task": "t", "cwd": str(ROOT)})
            except BaseException as exc:  # surfaced by the assertions below
                box["exc"] = exc

        thread = threading.Thread(target=do_spawn)
        thread.start()
        submitted = None
        try:
            deadline = time.time() + 15  # preflight alone is a full dry run
            while time.time() < deadline and "exc" not in box:
                runs = mcp_server.status()["runs"]
                if runs:
                    submitted = runs[0]
                    break
                time.sleep(0.02)
            self.assertIsNotNone(
                submitted, "the run never appeared: %r" % (box.get("exc"),))
            self.assertEqual(submitted["state"], "submitted")
            self.assertEqual(submitted["detail"], "starting")
        finally:
            release.set()
            thread.join(10)
        self.assertNotIn("exc", box)
        out = box["out"]
        self.assertEqual(out["id"], submitted["id"])
        st = mcp_server.status(out["id"])
        self.assertEqual(st["state"], "working")
        self.assertEqual(st["detail"], "running")
        working = st["state"]
        st = self.wait_done(out["id"])
        self.assertEqual(st["state"], "completed")
        self.assertEqual(st["detail"], "done")
        # every state the walk returned, in order, ack included - all A2A names
        seen = [submitted["state"], out["state"], working, st["state"]]
        for state in seen:
            self.assertIn(state, mcp_server.TASK_STATES)

    def test_cancel_of_a_working_run_reports_canceled(self):
        """Spec §9 spells it "canceled" (one l); the old value survives in
        detail. The runner is a stub sleeper in its own session so cancel
        always lands while the run is working - a real dry run may finish
        first, which is the no-op test above."""
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)

        def stop():
            if sleeper.poll() is None:  # cancel may already have killed it
                sleeper.terminate()
            sleeper.wait()
        self.addCleanup(stop)
        self.make_run("cancel-test", pid=sleeper.pid)
        self.assertEqual(mcp_server.status("cancel-test")["state"], "working")
        st = mcp_server.cancel("cancel-test")
        self.assertEqual(st["state"], "canceled")
        self.assertEqual(st["detail"], "cancelled")
        self.assertIn("canceled", mcp_server.TASK_STATES)

    def test_a_dead_pid_without_exit_json_is_failed_lost(self):
        """Spec §9: a pid that died before writing exit.json is failed, and the
        old "lost" name survives in detail (the runner was killed, the exit
        code is unknowable - not a cancel, not a success)."""
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()  # a pid that no longer exists, not merely our own
        self.make_run("lost-test", pid=gone.pid)
        st = mcp_server.status("lost-test")
        self.assertEqual(st["state"], "failed")
        self.assertEqual(st["detail"], "lost")
        self.assertIn("failed", mcp_server.TASK_STATES)

    def make_sleeper(self):
        """A live process standing in for a working runner, in its own session
        so the test never leaks it (the same trick the cancel test uses)."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)

        def stop():
            if proc.poll() is None:  # cancel may already have killed it
                proc.terminate()
            proc.wait()
        self.addCleanup(stop)
        return proc

    def make_asking_run(self, run_id, text="ship or revert?"):
        """A synthetic working run whose worker has just asked a question -
        the file tools/autoos-ask.py writes, exactly as it writes it."""
        path = self.make_run(run_id, pid=self.make_sleeper().pid)
        mcp_server._write_json(os.path.join(path, "question.json"),
                               {"text": text, "asked": "2026-09-27T00:00:00Z"})
        return path

    def test_a_pending_question_flips_a_working_run_to_input_required(self):
        """Spec §9 ask-back: question.json with no answer.json yet is the whole
        input_required signal; `detail` stays "running" and the dict carries
        the question text the orchestrator has to answer."""
        path = self.make_asking_run("ask-test")
        st = mcp_server.status("ask-test")
        self.assertEqual(st["state"], "input_required")
        self.assertEqual(st["detail"], "running")
        self.assertEqual(st["question"], "ship or revert?")
        # answered -> working again while the helper consumes the answer
        mcp_server._write_json(os.path.join(path, "answer.json"),
                               {"text": "ship", "answered": "2026-09-27T00:00:01Z"})
        st = mcp_server.status("ask-test")
        self.assertEqual(st["state"], "working")
        self.assertNotIn("question", st)
        # consumed (or withdrawn on timeout) -> working again
        for name in ("answer.json", "question.json"):
            os.remove(os.path.join(path, name))
        self.assertEqual(mcp_server.status("ask-test")["state"], "working")

    def test_respond_writes_answer_json_and_reports_the_new_state(self):
        path = self.make_asking_run("respond-test")
        out = mcp_server.respond("respond-test", "ship it")
        self.assertNotIn("error", out)
        self.assertEqual(out["state"], "working")  # answered -> no longer asking
        answer = mcp_server._read_json(os.path.join(path, "answer.json"))
        self.assertEqual(answer["text"], "ship it")
        self.assertTrue(answer["answered"].endswith("Z"))
        # after the helper has consumed both files the run is simply working
        for name in ("answer.json", "question.json"):
            os.remove(os.path.join(path, name))
        self.assertEqual(mcp_server.status("respond-test")["state"], "working")

    def test_respond_refuses_a_run_that_is_not_asking(self):
        """Only an input_required run can be answered: a working run with no
        question, a finished run, and an unknown id all come back as errors,
        and none of them writes answer.json."""
        path = self.make_run("silent-test", pid=self.make_sleeper().pid)
        out = mcp_server.respond("silent-test", "ship it")
        self.assertIn("error", out)
        self.assertIn("input_required", out["error"])
        self.assertIsNone(mcp_server._read_json(os.path.join(path, "answer.json")))
        mcp_server._write_exit(path, {"rc": 0, "ended": time.time()})
        out = mcp_server.respond("silent-test", "ship it")
        self.assertIn("error", out)
        self.assertIsNone(mcp_server._read_json(os.path.join(path, "answer.json")))
        self.assertIn("error", mcp_server.respond("nope", "ship it"))

    def test_respond_refuses_empty_text(self):
        path = self.make_asking_run("empty-text-test")
        for text in ("", "   "):
            self.assertIn("error", mcp_server.respond("empty-text-test", text))
        self.assertIsNone(mcp_server._read_json(os.path.join(path, "answer.json")))

    def test_cancel_of_an_input_required_run_reports_canceled(self):
        """A run parked in input_required is blocked, so an operator stop is
        the only way out: cancel must reach it, not report nothing-to-cancel."""
        self.make_asking_run("cancel-ask-test")
        self.assertEqual(mcp_server.status("cancel-ask-test")["state"], "input_required")
        st = mcp_server.cancel("cancel-ask-test")
        self.assertEqual(st["state"], "canceled")
        self.assertEqual(st["detail"], "cancelled")

    def test_run_job_exports_the_task_dir_to_the_child(self):
        """run_job passes AUTOOS_TASK_DIR=<run dir> so a blocked worker's brief
        can point tools/autoos-ask.py at this run; autoos-agent.py forwards
        os.environ to the client, so the worker itself sees it."""
        path = self.make_run("env-test", argv=["--version"])
        box = {}

        def fake_call(argv, **kw):
            box["argv"], box["env"] = argv, kw.get("env")
            return 0

        with mock.patch.object(mcp_server.subprocess, "call", fake_call):
            self.assertEqual(mcp_server.run_job(path), 0)
        self.assertEqual(box["argv"], [sys.executable, mcp_server.AGENT, "--version"])
        self.assertEqual(box["env"], dict(os.environ, AUTOOS_TASK_DIR=path))

    def test_a_refused_spawn_is_rejected(self):
        """Spec §9: a spawn the server refuses never started anything, so its
        answer carries state "rejected" alongside the error. One case per
        refusal path: card/route (build_argv), empty task, unknown client,
        bad cwd, and a request only the CLI's own dry run refuses."""
        refusals = (
            {"task": "t", "card": {"privacy": "sensitive", "ctx": "1m"}},
            {"task": ""},
            {"task": "t", "client": "nope"},
            {"task": "t", "cwd": os.path.join(self.tmp, "no-such-dir")},
            {"task": "t", "client": "qoder", "card": {"privacy": "sensitive"}},
        )
        for req in refusals:
            out = mcp_server.spawn(req)
            self.assertIn("error", out, req)
            self.assertEqual(out["state"], "rejected", req)
        self.assertIn("rejected", mcp_server.TASK_STATES)
        self.assertEqual(mcp_server.status()["runs"], [])

    # --- stdout channel: workers that cannot use the ask-back helper ------

    def _make_ended_run(self, run_id, output="", rc=0, answered=False):
        """Synthetic ended run with output.log and exit.json."""
        path = self.make_run(run_id)
        if output:
            with io.open(os.path.join(path, "output.log"), "w",
                         encoding="utf-8") as fh:
                fh.write(output)
        mcp_server._write_exit(path, {"rc": rc, "ended": time.time()})
        if answered:
            mcp_server._write_json(os.path.join(path, "qa-1.json"),
                                   {"question": {"text": "q"},
                                    "answer": {"text": "a"}})
        return path

    def test_a_stdout_question_flips_completed_to_input_required_ended(self):
        """A worker that cannot use the ask-back helper prints QUESTION to
        stdout.  _state() must detect this and report input_required with
        detail=ended instead of completed/done."""
        self._make_ended_run("stdout-q-test",
            output="line 1\nQUESTION k2-qoder: reply A or B?\nline 3\n",
            rc=0)
        st = mcp_server.status("stdout-q-test")
        self.assertEqual(st["state"], "input_required")
        self.assertEqual(st["detail"], "ended")
        self.assertEqual(st["question"], "reply A or B?")

    def test_a_question_line_early_in_the_log_is_not_a_question(self):
        """Only the worker's closing lines count: a QUESTION-shaped line in
        earlier tool output ('QUESTION handler: initializing') must not pin
        a finished run to input_required (Sonnet final review K2)."""
        body = "QUESTION handler: initializing\n" + "".join("work line %d\n" % i for i in range(40))
        self._make_ended_run("early-q-test", output=body + "done\n", rc=0)
        st = mcp_server.status("early-q-test")
        self.assertEqual(st["state"], "completed")
        self.assertNotIn("question", st)

    def test_a_vanished_run_dir_does_not_crash_the_channel(self):
        missing = os.path.join(self.tmp, "agents", "gone")
        self.assertEqual(mcp_server._stdout_channel(missing, {"rc": 0}), {})

    def test_a_answered_stdout_question_and_report_keeps_completed(self):
        """When a qa-1.json (answered question) exists alongside a REPORT
        block in output.log, the state stays completed and carries the
        parsed report data."""
        self._make_ended_run("report-qa-test",
            output=("REPORT my-id · completed · file1.py · test1 -> pass"
                    " · · lesson1\n"),
            rc=0, answered=True)
        st = mcp_server.status("report-qa-test")
        self.assertEqual(st["state"], "completed")
        self.assertEqual(st["detail"], "done")
        self.assertIn("report", st)
        self.assertEqual(st["report"]["id"], "my-id")
        self.assertEqual(st["report"]["status"], "completed")
        self.assertEqual(st["report"]["files"], ["file1.py"])

    def test_a_stdout_failed_report_overrides_exit_code(self):
        """When the REPORT block says 'failed' but exit code was 0,
        _state() reports failed with detail='reported-failed'."""
        self._make_ended_run("report-fail-test",
            output="REPORT my-id · failed · · · blocker1\n",
            rc=0)
        st = mcp_server.status("report-fail-test")
        self.assertEqual(st["state"], "failed")
        self.assertEqual(st["detail"], "reported-failed")
        self.assertEqual(st["rc"], 0)

    def test_respond_refuses_an_ended_run(self):
        """A run with state input_required but detail=ended (question in
        stdout from a worker that already exited) cannot be answered via
        respond() — there is no answer.json to write and the worker is
        gone."""
        self._make_ended_run("ended-respond-test",
            output="QUESTION k2-qoder: reply A or B?\n",
            rc=0)
        out = mcp_server.respond("ended-respond-test", "A")
        self.assertIn("error", out)
        self.assertIn("exited", out["error"])
        self.assertIn("follow-up", out["error"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "agents", "ended-respond-test", "answer.json")))

    def test_a_stdout_input_required_report_without_question_line(self):
        """A REPORT whose status is input_required counts as a question even
        without a QUESTION line; its blockers become the question text."""
        self._make_ended_run("report-ir-test",
            output="REPORT k2 · input_required · · ask -> no shell · need a decision · \n",
            rc=0)
        st = mcp_server.status("report-ir-test")
        self.assertEqual(st["state"], "input_required")
        self.assertEqual(st["detail"], "ended")
        self.assertEqual(st["question"], "need a decision")
        self.assertEqual(st["report"]["status"], "input_required")

    def _run_job_printing(self, run_id, printed, fallback_dir):
        path = self.make_run(run_id, argv=["--version"])

        def fake_call(argv, **kw):
            with io.open(os.path.join(path, "output.log"), "w",
                         encoding="utf-8") as fh:
                fh.write(printed)
            return 0

        with mock.patch.dict(os.environ, {"AUTOOS_FALLBACK_DIR": fallback_dir}):
            with mock.patch.object(mcp_server.subprocess, "call", fake_call):
                self.assertEqual(mcp_server.run_job(path), 0)
        return path

    def test_run_job_writes_the_question_fallback_only_when_ask_back_failed(self):
        """The primary channel failed (stdout QUESTION, no qa-*.json): run_job
        writes <id>.question.md into AUTOOS_FALLBACK_DIR with the question and
        the raw REPORT line, and a second run never overwrites it."""
        fallback_dir = os.path.join(self.tmp, "fallback")
        printed = ("QUESTION k2-qoder: reply A or B?\n"
                   "REPORT k2-qoder · input_required · · ask -> no shell · · \n")
        self._run_job_printing("fallback-test", printed, fallback_dir)
        target = os.path.join(fallback_dir, "fallback-test.question.md")
        with io.open(target, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("reply A or B?", text)
        self.assertIn("report: REPORT k2-qoder · input_required", text)
        with io.open(target, "w", encoding="utf-8") as fh:
            fh.write("parent notes\n")
        path = os.path.join(self.tmp, "agents", "fallback-test")
        with mock.patch.dict(os.environ, {"AUTOOS_FALLBACK_DIR": fallback_dir}):
            mcp_server._write_fallback(path)
        with io.open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "parent notes\n")

    def test_run_job_writes_no_fallback_when_the_primary_channel_worked(self):
        fallback_dir = os.path.join(self.tmp, "fallback")
        self._run_job_printing("primary-test",
                               "REPORT k2 · completed · · ask -> A · · \n", fallback_dir)
        self.assertFalse(os.path.exists(fallback_dir) and os.listdir(fallback_dir))

    def test_the_question_fallback_defaults_to_the_run_dir(self):
        path = self.make_run("fallback-default-test")
        with io.open(os.path.join(path, "output.log"), "w", encoding="utf-8") as fh:
            fh.write("QUESTION w: ship or revert?\n")
        mcp_server._write_exit(path, {"rc": 0, "ended": time.time()})
        env = {k: v for k, v in os.environ.items() if k != "AUTOOS_FALLBACK_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            mcp_server._write_fallback(path)
        self.assertTrue(os.path.isfile(os.path.join(path, "fallback-default-test.question.md")))

    def test_stdout_without_channel_keeps_normal_state(self):
        """A completed run whose output.log has no QUESTION or REPORT block
        keeps its normal completed/done state without any channel keys."""
        self._make_ended_run("clean-test",
            output="Just some regular output\nNothing interesting\n",
            rc=0)
        st = mcp_server.status("clean-test")
        self.assertEqual(st["state"], "completed")
        self.assertEqual(st["detail"], "done")
        self.assertNotIn("question", st)
        self.assertNotIn("report", st)


class AskHelperTests(unittest.TestCase):
    """tools/autoos-ask.py, offline: question/answer/qa files in a temp dir,
    the helper in a real subprocess, a thread playing the orchestrator."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask_argv(self, question, *opts):
        return [sys.executable, str(TOOLS / "autoos-ask.py"), question, *opts]

    def run_ask(self, question, *opts, task_dir=None):
        env = clean_env(AUTOOS_TASK_DIR=task_dir if task_dir is not None else self.tmp)
        return subprocess.run(self.ask_argv(question, *opts), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=120)

    def test_the_helper_round_trips_a_question_and_answer(self):
        """End to end: the helper parks question.json, a thread playing the
        orchestrator writes answer.json, the helper prints the answer text to
        stdout, exits 0 and archives the pair as qa-1.json (history kept)."""
        box = {}

        def answer_later():
            qpath = os.path.join(self.tmp, "question.json")
            deadline = time.time() + 10
            while not os.path.exists(qpath) and time.time() < deadline:
                time.sleep(0.02)
            with io.open(qpath, encoding="utf-8") as fh:  # it appeared: read its shape
                box["question"] = json.load(fh)
            atmp = os.path.join(self.tmp, "answer.json.tmp")
            with io.open(atmp, "w", encoding="utf-8") as fh:
                json.dump({"text": "ship it", "answered": "2026-09-27T00:00:01Z"}, fh)
            os.replace(atmp, os.path.join(self.tmp, "answer.json"))

        thread = threading.Thread(target=answer_later)
        thread.start()
        try:
            proc = self.run_ask("ship or revert?", "--poll", "0.1", "--timeout", "30")
        finally:
            thread.join()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "ship it")
        self.assertEqual(box["question"]["text"], "ship or revert?")
        self.assertTrue(box["question"]["asked"])
        with io.open(os.path.join(self.tmp, "qa-1.json"), encoding="utf-8") as fh:
            qa = json.load(fh)
        self.assertEqual(qa["question"]["text"], "ship or revert?")
        self.assertEqual(qa["answer"]["text"], "ship it")
        for name in ("question.json", "answer.json"):
            self.assertFalse(os.path.exists(os.path.join(self.tmp, name)), name)

    def test_the_helper_times_out_and_withdraws_the_question(self):
        """No answer within --timeout: the question is withdrawn (the run
        returns to working), the reason goes to stderr, the exit code is 3."""
        proc = self.run_ask("anyone?", "--poll", "0.05", "--timeout", "0.2")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("no answer within", proc.stderr)
        for name in ("question.json", "answer.json", "qa-1.json"):
            self.assertFalse(os.path.exists(os.path.join(self.tmp, name)), name)

    def test_the_helper_refuses_a_second_question_while_one_is_pending(self):
        with io.open(os.path.join(self.tmp, "question.json"), "w", encoding="utf-8") as fh:
            json.dump({"text": "first?", "asked": "2026-09-27T00:00:00Z"}, fh)
        proc = self.run_ask("second?", "--poll", "0.05", "--timeout", "0.2")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("pending", proc.stderr)
        with io.open(os.path.join(self.tmp, "question.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["text"], "first?")  # the pending one is untouched

    def test_the_helper_needs_the_task_dir_env(self):
        env = clean_env()
        env.pop("AUTOOS_TASK_DIR", None)
        proc = subprocess.run(self.ask_argv("hello?"), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("AUTOOS_TASK_DIR", proc.stderr)

    def test_a_stale_answer_json_at_start_is_archived_not_returned(self):
        """An answer.json with no question.json is stale (left by a timeout
        the previous run hit). Archive it as qa-<n>.json with a 'stale' marker
        and proceed — never silently delete an answer, never treat it as the
        answer to a question that has not been asked yet."""
        with io.open(os.path.join(self.tmp, "answer.json"), "w", encoding="utf-8") as fh:
            json.dump({"text": "late answer", "answered": "2026-09-27T00:00:02Z"}, fh)

        box = {}

        def answer_later():
            qpath = os.path.join(self.tmp, "question.json")
            deadline = time.time() + 10
            while not os.path.exists(qpath) and time.time() < deadline:
                time.sleep(0.02)
            with io.open(qpath, encoding="utf-8") as fh:
                box["question"] = json.load(fh)
            atmp = os.path.join(self.tmp, "answer.json.tmp")
            with io.open(atmp, "w", encoding="utf-8") as fh:
                json.dump({"text": "fresh", "answered": "2026-09-27T00:01:00Z"}, fh)
            os.replace(atmp, os.path.join(self.tmp, "answer.json"))

        thread = threading.Thread(target=answer_later)
        thread.start()
        try:
            proc = self.run_ask("real question?", "--poll", "0.1", "--timeout", "30")
        finally:
            thread.join()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "fresh")  # not the stale "late answer"

        def answer_second():
            qpath = os.path.join(self.tmp, "question.json")
            deadline = time.time() + 10
            while not os.path.exists(qpath) and time.time() < deadline:
                time.sleep(0.02)
            with io.open(qpath, encoding="utf-8") as fh:
                box["question"] = json.load(fh)
            atmp = os.path.join(self.tmp, "answer.json.tmp")
            with io.open(atmp, "w", encoding="utf-8") as fh:
                json.dump({"text": "second answer", "answered": "2026-09-27T00:02:00Z"}, fh)
            os.replace(atmp, os.path.join(self.tmp, "answer.json"))

        thread2 = threading.Thread(target=answer_second)
        thread2.start()
        try:
            proc2 = self.run_ask("second?", "--poll", "0.1", "--timeout", "30")
        finally:
            thread2.join()
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertEqual(proc2.stdout.strip(), "second answer")
        self.assertEqual(box["question"]["text"], "second?")
        # the stale answer went to history, never to the second question
        qa_path = os.path.join(self.tmp, "qa-1.json")
        self.assertTrue(os.path.exists(qa_path))
        with io.open(qa_path, encoding="utf-8") as fh:
            qa = json.load(fh)
        self.assertTrue(qa.get("stale"))  # the archived stale answer is marked
        self.assertEqual(qa["answer"]["text"], "late answer")
        self.assertIsNone(qa.get("question"))  # no question preceded it

    def test_a_late_answer_after_timeout_is_archived_and_the_next_ask_works(self):
        """The exact bug: timeout removes question.json, but an answer landing
        at/after the deadline left answer.json behind. The next ask used to
        see answer.json and refuse 'already pending' (exit 2) forever. Now the
        orphan answer is archived as stale and the next ask works. Sequential,
        not a thread race: clean timeout, then the late respond(), then ask."""
        proc1 = self.run_ask("first?", "--poll", "0.05", "--timeout", "0.2")
        self.assertEqual(proc1.returncode, 3, proc1.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "question.json")))
        atmp = os.path.join(self.tmp, "answer.json.tmp")
        with io.open(atmp, "w", encoding="utf-8") as fh:
            json.dump({"text": "too late", "answered": "2026-09-27T00:00:05Z"}, fh)
        os.replace(atmp, os.path.join(self.tmp, "answer.json"))

        box = {}

        def answer_second():
            qpath = os.path.join(self.tmp, "question.json")
            deadline = time.time() + 10
            while not os.path.exists(qpath) and time.time() < deadline:
                time.sleep(0.02)
            with io.open(qpath, encoding="utf-8") as fh:
                box["question"] = json.load(fh)
            tmp = os.path.join(self.tmp, "answer.json.tmp")
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"text": "second answer", "answered": "2026-09-27T00:02:00Z"}, fh)
            os.replace(tmp, os.path.join(self.tmp, "answer.json"))

        thread = threading.Thread(target=answer_second)
        thread.start()
        proc2 = self.run_ask("second?", "--poll", "0.1", "--timeout", "30")
        thread.join()
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertEqual(proc2.stdout.strip(), "second answer")
        self.assertEqual(box["question"]["text"], "second?")
        # the stale answer went to history (qa-1), never to the second question
        with io.open(os.path.join(self.tmp, "qa-1.json"), encoding="utf-8") as fh:
            qa = json.load(fh)
        self.assertTrue(qa.get("stale"))
        self.assertEqual(qa["answer"]["text"], "too late")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "answer.json")))

    # chmod 0o555 does not stop writes on Windows (only the read-only file
    # attribute exists there) nor for root: the directory stays writable.
    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "chmod cannot make a directory unwritable here")
    def test_an_unwritable_task_dir_exits_5_with_a_message_not_a_traceback(self):
        """Under --isolate the outside-path fence can deny writes into
        AUTOOS_TASK_DIR. The helper must not dump a raw traceback — it must
        exit 5 with a message that names the reason and suggests --isolate."""
        ro = os.path.join(self.tmp, "ro")
        os.mkdir(ro)
        os.chmod(ro, 0o555)
        try:
            proc = self.run_ask("anything?", "--poll", "0.05", "--timeout", "0.2",
                                task_dir=ro)
        finally:
            os.chmod(ro, 0o755)
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("autoos-ask", proc.stderr)
        self.assertIn("unavailable", proc.stderr)


def load_ask():
    """tools/autoos-ask.py loaded in-process, so a test can widen the window
    between its exists() check and its create — something a subprocess race
    can only hit by luck."""
    spec = importlib.util.spec_from_file_location("autoos_ask", str(TOOLS / "autoos-ask.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AskRaceTests(unittest.TestCase):
    """The check-then-act races in tools/autoos-ask.py: two askers sharing one
    run dir, and two archives claiming the same qa-<n> slot."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_ask(self, question, *opts):
        env = clean_env(AUTOOS_TASK_DIR=self.tmp)
        return subprocess.run([sys.executable, str(TOOLS / "autoos-ask.py"), question, *opts],
                              env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=120)

    def test_two_concurrent_askers_produce_exactly_one_question(self):
        """Two askers launched together: exactly one may become the asker (it
        writes the question and times out, exit 3), the other must refuse as
        already pending (exit 2). Two 3s is the bug - both wrote, so the loser
        replaced the winner's pending question and the orchestrator answers the
        wrong worker."""
        for _round in range(3):
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = tempfile.mkdtemp()
            results = []
            lock = threading.Lock()

            def ask(question):
                proc = self.run_ask(question, "--poll", "0.05", "--timeout", "0.2")
                with lock:
                    results.append((question, proc.returncode))

            threads = [threading.Thread(target=ask, args=("question %s?" % who,))
                       for who in "ab"]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(rc for _, rc in results), [2, 3], results)

    def test_an_asker_that_loses_the_race_never_replaces_the_pending_question(self):
        """The window itself, with no timing luck: question.json is on disk, but
        the module's own exists() reports it missing - exactly what a second
        asker sees while the first sits between its check and its write. The
        create must still fail, and the pending question must survive."""
        mod = load_ask()
        qpath = os.path.join(self.tmp, "question.json")
        with io.open(qpath, "w", encoding="utf-8") as fh:
            json.dump({"text": "first?", "asked": "2026-09-27T00:00:00Z"}, fh)

        class BlindPath:
            def __init__(self, real):
                self._real = real
                self.blinded = False

            def exists(self, path):
                if path == qpath and not self.blinded:
                    self.blinded = True  # the other asker has not written yet
                    return False
                return self._real.exists(path)

            def __getattr__(self, name):
                return getattr(self._real, name)

        class BlindOs:
            def __init__(self, real):
                self._real = real
                self.path = BlindPath(real.path)

            def __getattr__(self, name):
                return getattr(self._real, name)

        blind = BlindOs(mod.os)
        err = io.StringIO()
        with mock.patch.object(mod, "os", blind), \
                mock.patch.dict(os.environ, {"AUTOOS_TASK_DIR": self.tmp}), \
                contextlib.redirect_stderr(err):
            rc = mod.main(["second?", "--poll", "0.05", "--timeout", "0.2"])
        self.assertTrue(blind.path.blinded, "the window never opened: exists() was not consulted")
        self.assertEqual(rc, 2, err.getvalue())
        self.assertIn("already pending", err.getvalue())
        with io.open(qpath, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["text"], "first?")

    def test_two_racing_archives_never_share_a_qa_slot(self):
        """qa-<n> is a history slot. Scanning for the next free number and then
        writing lets two archivers pick the same n, and the second silently
        overwrites the first - an exchange vanishes from the record. Each
        archive has to claim its own number.

        The race is driven through _archive_stale_answer, the entry point
        main() uses when it files an answer, so the test covers the path a real
        run takes (and keeps working if the private write helper is renamed).
        """
        mod = load_ask()
        sentinel = os.path.join(self.tmp, "qa-1.json")
        with io.open(sentinel, "w", encoding="utf-8") as fh:
            json.dump({"question": None, "answer": {"text": "already filed"}, "stale": True}, fh)

        writers = 6
        barrier = threading.Barrier(writers)
        errors = []

        def archive(i):
            # Every archiver holds one orphan answer of its own — the shape
            # main() hands the helper — and files it into the shared history.
            apath = os.path.join(self.tmp, "orphan-%d.json" % i)
            with io.open(apath, "w", encoding="utf-8") as fh:
                json.dump({"text": "a%d" % i, "answered": "2026-09-27T00:00:0%dZ" % i}, fh)
            barrier.wait()
            try:
                mod._archive_stale_answer(self.tmp, apath)
            except Exception as exc:  # noqa: BLE001 - surfaced as a failure below
                errors.append("q%d: %r" % (i, exc))

        threads = [threading.Thread(target=archive, args=(i,)) for i in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        filed = sorted(n for n in os.listdir(self.tmp) if n.startswith("qa-") and n.endswith(".json"))
        self.assertEqual(len(filed), writers + 1, filed)
        with io.open(sentinel, encoding="utf-8") as fh:
            self.assertTrue(json.load(fh).get("stale"), "the existing qa-1.json was overwritten")
        answers = set()
        for name in filed:
            if name == "qa-1.json":
                continue
            with io.open(os.path.join(self.tmp, name), encoding="utf-8") as fh:
                answers.add(json.load(fh)["answer"]["text"])
        self.assertEqual(answers, {"a%d" % i for i in range(writers)}, "an exchange was lost")
        for i in range(writers):
            orphan = os.path.join(self.tmp, "orphan-%d.json" % i)
            self.assertFalse(os.path.exists(orphan), "%s was filed but left behind" % orphan)

    def test_a_qa_slot_is_never_observed_empty_or_partial(self):
        """Reserving the slot first (create qa-<n> empty, fill it in a second
        step) leaves a window in which the history file exists with no bytes in
        it. A reader polling the run dir sees an entry that is neither valid
        JSON nor any exchange, and a run killed inside the window loses the slot
        forever — the next archive steps over an empty qa-<n>. The slot must
        appear on the filesystem already complete."""
        mod = load_ask()
        apath = os.path.join(self.tmp, "answer.json")
        with io.open(apath, "w", encoding="utf-8") as fh:
            json.dump({"text": "ship it", "answered": "2026-09-27T00:00:00Z"}, fh)

        problems = []
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                for name in sorted(os.listdir(self.tmp)):
                    if not (name.startswith("qa-") and name.endswith(".json")):
                        continue
                    with io.open(os.path.join(self.tmp, name), "rb") as fh:
                        raw = fh.read()
                    if not raw.strip():
                        problems.append("%s existed with no bytes in it" % name)
                        continue
                    try:
                        json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        problems.append("%s existed half-written" % name)
                time.sleep(0.005)

        real_dump = json.dump

        def slow_dump(obj, fh, *args, **kwargs):
            # Widen the window between "the slot exists" and "the slot holds
            # its bytes" — the gap the two-step reserve used to leave open.
            time.sleep(0.2)
            return real_dump(obj, fh, *args, **kwargs)

        observer = threading.Thread(target=watch)
        observer.start()
        try:
            with mock.patch.object(mod.json, "dump", slow_dump):
                mod._archive_stale_answer(self.tmp, apath)
        finally:
            stop.set()
            observer.join()

        self.assertEqual(problems, [])
        filed = sorted(n for n in os.listdir(self.tmp)
                       if n.startswith("qa-") and n.endswith(".json"))
        self.assertEqual(filed, ["qa-1.json"], filed)
        with io.open(os.path.join(self.tmp, "qa-1.json"), encoding="utf-8") as fh:
            qa = json.load(fh)
        self.assertTrue(qa.get("stale"))
        self.assertEqual(qa["answer"]["text"], "ship it")
        self.assertFalse(os.path.exists(apath))
        leftovers = [n for n in os.listdir(self.tmp) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [], "the archive left a temp file behind")


def uv_mcp_cmd():
    """The registered server command, offline only: tests never download."""
    uv = shutil.which("uv")
    if not uv:
        return None
    cmd = [uv, "--quiet", "run", "--offline", "--no-project", "--with", "mcp<2", "python"]
    probe = subprocess.run(cmd + ["-c", "import mcp"], capture_output=True, stdin=subprocess.DEVNULL)
    return cmd if probe.returncode == 0 else None


class McpStdioTests(unittest.TestCase):
    """JSON-RPC over stdio against the real server (skipped when mcp is not in uv's cache)."""

    def test_initialize_tools_list_and_a_dry_run_spawn(self):
        cmd = uv_mcp_cmd()
        if not cmd:
            self.skipTest("uv or a cached mcp<2 is not available (offline)")
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(AUTOOS_STATE_DIR=tmp, AUTOOS_AGENT_MCP_DRY_RUN="1")
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "autoos-test", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "spawn", "arguments": {"task": "t", "card": {"role": "review"},
                                                   "cwd": str(ROOT)}}},
            ]
            proc = subprocess.Popen(cmd + [str(TOOLS / "autoos_agent_mcp.py")], env=env, text=True,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            replies = {}
            try:
                for m in msgs:
                    proc.stdin.write(json.dumps(m) + "\n")
                    proc.stdin.flush()
                    if "id" in m:  # one request at a time: EOF would cut off a pending reply
                        while m["id"] not in replies:
                            line = proc.stdout.readline()
                            self.assertTrue(line, "server closed stdout early")
                            reply = json.loads(line)
                            if "id" in reply:
                                replies[reply["id"]] = reply
            finally:
                proc.stdin.close()
                proc.wait(timeout=30)
                proc.stdout.close()
            self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "autoos-agent")
            names = {t["name"] for t in replies[2]["result"]["tools"]}
            self.assertEqual(names, {"list_clients", "spawn", "status", "result", "cancel",
                                     "respond", "route", "list_agents", "context", "heartbeat",
                                     "ps"})
            spawned = json.loads(replies[3]["result"]["content"][0]["text"])
            self.assertEqual(spawned["route"]["combo"], "t3-driver")
            run_dir = os.path.join(tmp, "agents", spawned["id"])
            self.assertTrue(os.path.isdir(run_dir))
            for _ in range(100):  # let the detached dry run finish before tmp goes away
                if os.path.exists(os.path.join(run_dir, "exit.json")):
                    break
                time.sleep(0.1)



class StatePlacementTests(unittest.TestCase):
    """Operator order 2026-09-25: run state (job/output/exit files, probes,
    sandbox clones) lives inside the repository, in a git-ignored folder -
    never scattered under ~/.local/state."""

    def _ignored(self, path):
        rel = os.path.relpath(path, ROOT)
        return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel]).returncode == 0

    def test_default_state_dir_is_inside_the_repo_and_git_ignored(self):
        env = {k: v for k, v in os.environ.items() if k != "AUTOOS_STATE_DIR"}
        base = clients.state_dir(env)
        self.assertTrue(os.path.realpath(base).startswith(os.path.realpath(str(ROOT)) + os.sep), base)
        for sub in (os.path.join(base, "agents", "x"), os.path.join(base, "sandboxes", "x")):
            self.assertTrue(self._ignored(sub), sub + " is not git-ignored")

    def test_mcp_runs_and_probes_follow_the_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("AUTOOS_STATE_DIR")
            os.environ["AUTOOS_STATE_DIR"] = tmp
            try:
                self.assertEqual(mcp_server.state_root(), os.path.join(tmp, "agents"))
                self.assertTrue(clients._probe_file().startswith(tmp))
            finally:
                if old is None:
                    os.environ.pop("AUTOOS_STATE_DIR", None)
                else:
                    os.environ["AUTOOS_STATE_DIR"] = old


class RunDirCollisionTests(unittest.TestCase):
    """Review finding 2026-09-25: a run-id collision raised FileExistsError out
    of the MCP tool call; it must retry, then fail as a JSON error."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = {k: os.environ.get(k) for k in ("AUTOOS_STATE_DIR", "AUTOOS_AGENT_MCP_DRY_RUN")}
        os.environ.update(AUTOOS_STATE_DIR=self.tmp, AUTOOS_AGENT_MCP_DRY_RUN="1")

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persistent_collision_returns_an_error_not_an_exception(self):
        real = mcp_server.os.makedirs

        def always_exists(path, *a, **kw):
            if os.path.dirname(path) == mcp_server.state_root():
                raise FileExistsError(path)
            return real(path, *a, **kw)
        mcp_server.os.makedirs = always_exists
        try:
            out = mcp_server.spawn({"task": "t", "tier": 3})
        finally:
            mcp_server.os.makedirs = real
        self.assertIn("error", out)


class TrackEntryClientTests(unittest.TestCase):
    """A run on an own-account client (qoder, agy, claude) never touches the
    gateway route its plan names, so it is no observation of that route:
    no track record and hence no re-probe proposal (measured 2026-09-26:
    agy/qoder NO-OPs were recorded as t2-worker failures)."""

    def setUp(self):
        self.cli = load_agent()

    def plan(self, client):
        return {"client": client, "route": {"combo": "t2-worker", "card": None}}

    def test_a_gateway_client_run_is_recorded(self):
        self.assertEqual(self.cli.track_entry(self.plan("opencode"), 0, 1.0)["route"],
                         "t2-worker")

    def test_an_own_account_client_run_is_not_recorded(self):
        for client in ("qoder", "agy", "claude"):
            self.assertIsNone(self.cli.track_entry(self.plan(client), 5, 1.0), client)


class ProbeProposalTests(unittest.TestCase):
    """TC2: propose a tool-calling re-probe when a real run's gate contradicts
    the recorded tool_calls status of its route's legs (spec 5.6 track_entry,
    catalog/ai-registry.json models[<model>].tool_calls, overlay
    logs/routing/measured.json legs[<leg>].tool_calls.value)."""

    REGISTRY = {
        "providers": {
            "clean": {"available": True, "trains_on_prompts": False},
            "flaky": {"available": False, "trains_on_prompts": False},
        },
        "models": {
            "big": {"tool_calls": "proven"},
            "small": {"tool_calls": "unproven"},
        },
        "routes": {
            "r-proven": {"legs": ["clean/big"]},
            "r-mixed": {"legs": ["clean/big", "clean/small"]},
            "r-with-unavailable-provider": {"legs": ["clean/big", "flaky/small"]},
            "r-with-unavailable-leg": {
                "legs": ["clean/big", "clean/small"],
                # The schema requires `available` on an unavailable_legs entry;
                # unavailable_now drops it on available: false.
                "unavailable_legs": {"clean/small": {"available": False}}},
        },
    }

    def setUp(self):
        self.cli = load_agent()

    def test_pass_on_an_unproven_leg_is_unexpected_pass(self):
        out = self.cli.probe_proposal("r-mixed", "pass", None, self.REGISTRY, {})
        self.assertEqual(out["route"], "r-mixed")
        self.assertEqual(out["kind"], "unexpected-pass")
        self.assertEqual(out["legs"], ["clean/small"])
        self.assertIn("re-probe to promote", out["reason"])
        self.assertEqual(out["command"], "python3 tools/probe-toolcalls.py --route r-mixed")

    def test_pass_on_all_proven_legs_is_none(self):
        self.assertIsNone(self.cli.probe_proposal("r-proven", "pass", None, self.REGISTRY, {}))

    def test_capability_fail_on_a_proven_first_leg_is_unexpected_fail(self):
        out = self.cli.probe_proposal("r-proven", "fail", "capability", self.REGISTRY, {})
        self.assertEqual(out["route"], "r-proven")
        self.assertEqual(out["kind"], "unexpected-fail")
        self.assertEqual(out["legs"], ["clean/big"])
        self.assertIn("regressed", out["reason"])
        self.assertEqual(out["command"], "python3 tools/probe-toolcalls.py --route r-proven")

    def test_capability_fail_on_an_unproven_first_leg_is_none(self):
        # r-mixed's first leg (clean/big) is proven, so put the unproven leg first.
        registry = json.loads(json.dumps(self.REGISTRY))
        registry["routes"]["r-mixed"]["legs"] = ["clean/small", "clean/big"]
        self.assertIsNone(self.cli.probe_proposal("r-mixed", "fail", "capability", registry, {}))

    def test_logic_fail_is_none(self):
        self.assertIsNone(self.cli.probe_proposal("r-proven", "fail", "logic", self.REGISTRY, {}))

    def test_overlay_value_wins_over_the_registry_value(self):
        # Registry says proven; the overlay's probed value says otherwise.
        overlay = {"legs": {"clean/big": {"tool_calls": {"value": "unproven"}}}}
        out = self.cli.probe_proposal("r-proven", "pass", None, self.REGISTRY, overlay)
        self.assertEqual(out["kind"], "unexpected-pass")
        self.assertEqual(out["legs"], ["clean/big"])
        # And the reverse: registry unproven, overlay promotes it to proven.
        registry = json.loads(json.dumps(self.REGISTRY))
        registry["routes"]["only"] = {"legs": ["clean/small"]}
        overlay2 = {"legs": {"clean/small": {"tool_calls": {"value": "proven"}}}}
        self.assertIsNone(self.cli.probe_proposal("only", "pass", None, registry, overlay2))

    def test_a_provider_marked_unavailable_drops_its_leg(self):
        # Only clean/big remains (proven) once flaky/small is dropped: no proposal.
        out = self.cli.probe_proposal("r-with-unavailable-provider", "pass", None, self.REGISTRY, {})
        self.assertIsNone(out)

    def test_a_leg_listed_in_unavailable_legs_is_dropped(self):
        # Only clean/big remains (proven) once clean/small is excluded: no proposal.
        out = self.cli.probe_proposal("r-with-unavailable-leg", "pass", None, self.REGISTRY, {})
        self.assertIsNone(out)

    def test_a_provider_with_a_future_until_drops_its_leg(self):
        # UNTILfix: _available_legs must read unavailable_until through
        # registry.unavailable_now, never a local `available` snapshot, so it
        # agrees with the resolver. A provider whose until is still ahead
        # drops its leg even with no available: false.
        registry = json.loads(json.dumps(self.REGISTRY))
        registry["providers"]["flaky"]["available"] = True
        registry["providers"]["flaky"]["unavailable_until"] = (
            "2999-01-01T00:00:00Z")
        self.assertEqual(
            self.cli._available_legs(
                registry["routes"]["r-with-unavailable-provider"], registry),
            ["clean/big"])

    def test_a_provider_whose_until_passed_is_available_again(self):
        # ...and the same provider with a passed until is usable again, even
        # though it still carries available: false (the self-heal rule).
        registry = json.loads(json.dumps(self.REGISTRY))
        registry["providers"]["flaky"]["unavailable_until"] = (
            "2000-01-01T00:00:00Z")
        self.assertEqual(
            self.cli._available_legs(
                registry["routes"]["r-with-unavailable-provider"], registry),
            ["clean/big", "flaky/small"])

    def test_unknown_route_is_none(self):
        self.assertIsNone(self.cli.probe_proposal("no-such-route", "pass", None, self.REGISTRY, {}))
        self.assertIsNone(self.cli.probe_proposal("no-such-route", "fail", "capability", self.REGISTRY, {}))

    def test_jsonl_append_creates_the_file_and_keeps_earlier_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "routing", "probe-proposals.jsonl")
            self.assertTrue(self.cli.record_probe_proposal(path, {"a": 1}))
            self.assertTrue(self.cli.record_probe_proposal(path, {"a": 2}))
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh]
            self.assertEqual(lines, [{"a": 1}, {"a": 2}])

    def test_a_write_error_is_printed_and_returns_false_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocked")
            with open(blocker, "w", encoding="utf-8") as fh:
                fh.write("not a directory")
            bad_path = os.path.join(blocker, "probe-proposals.jsonl")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                ok = self.cli.record_probe_proposal(bad_path, {"a": 1})
            self.assertFalse(ok)
            self.assertIn("probe proposal not written", buf.getvalue())

    def test_propose_reprobe_appends_at_and_sandbox_and_prints_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = os.path.join(tmp, "registry.json")
            with open(registry_path, "w", encoding="utf-8") as fh:
                json.dump(self.REGISTRY, fh)
            overlay_path = os.path.join(tmp, "measured.json")  # never written: absent is fine
            proposals_path = os.path.join(tmp, "routing", "probe-proposals.jsonl")
            entry = {"route": "r-mixed", "gate": "pass", "failure_class": None}
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.cli.propose_reprobe(entry, registry_path, overlay_path, proposals_path, "/tmp/sandbox-x")
            with open(proposals_path, encoding="utf-8") as fh:
                logged = json.loads(fh.readline())
            self.assertEqual(logged["route"], "r-mixed")
            self.assertEqual(logged["kind"], "unexpected-pass")
            self.assertEqual(logged["sandbox"], "/tmp/sandbox-x")
            self.assertRegex(logged["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertIn("autoos-agent: PROBE-PROPOSAL: unexpected-pass r-mixed:", buf.getvalue())

    def test_propose_reprobe_is_silent_when_there_is_no_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = os.path.join(tmp, "registry.json")
            with open(registry_path, "w", encoding="utf-8") as fh:
                json.dump(self.REGISTRY, fh)
            proposals_path = os.path.join(tmp, "probe-proposals.jsonl")
            entry = {"route": "r-proven", "gate": "pass", "failure_class": None}
            self.cli.propose_reprobe(entry, registry_path, os.path.join(tmp, "measured.json"),
                                     proposals_path, "/tmp/sandbox-x")
            self.assertFalse(os.path.exists(proposals_path))

    def test_a_missing_or_broken_registry_skips_the_proposal_with_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "probe-proposals.jsonl")
            entry = {"route": "r-mixed", "gate": "pass", "failure_class": None}
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.cli.propose_reprobe(entry, os.path.join(tmp, "no-such-registry.json"),
                                         os.path.join(tmp, "measured.json"), proposals_path, "/tmp/x")
            self.assertFalse(os.path.exists(proposals_path))
            self.assertIn("probe proposal skipped", buf.getvalue())

    def test_a_broken_overlay_skips_the_proposal_with_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = os.path.join(tmp, "registry.json")
            with open(registry_path, "w", encoding="utf-8") as fh:
                json.dump(self.REGISTRY, fh)
            overlay_path = os.path.join(tmp, "measured.json")
            with open(overlay_path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            proposals_path = os.path.join(tmp, "probe-proposals.jsonl")
            entry = {"route": "r-mixed", "gate": "pass", "failure_class": None}
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                self.cli.propose_reprobe(entry, registry_path, overlay_path, proposals_path, "/tmp/x")
            self.assertFalse(os.path.exists(proposals_path))
            self.assertIn("probe proposal skipped", buf.getvalue())

    def test_a_write_error_in_propose_reprobe_does_not_raise(self):
        # Proves the exit code path is safe: propose_reprobe must not raise
        # even when the proposals file cannot be written, so cmd_run's
        # `return rc` right after it is never reached by an exception.
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = os.path.join(tmp, "registry.json")
            with open(registry_path, "w", encoding="utf-8") as fh:
                json.dump(self.REGISTRY, fh)
            blocker = os.path.join(tmp, "blocked")
            with open(blocker, "w", encoding="utf-8") as fh:
                fh.write("not a directory")
            bad_proposals_path = os.path.join(blocker, "probe-proposals.jsonl")
            entry = {"route": "r-mixed", "gate": "pass", "failure_class": None}
            try:
                self.cli.propose_reprobe(entry, registry_path, os.path.join(tmp, "measured.json"),
                                         bad_proposals_path, "/tmp/x")
            except Exception as exc:  # pragma: no cover - the point of the test
                self.fail("propose_reprobe raised %r instead of failing closed" % exc)


class NoOpGuardTests(unittest.TestCase):
    """Measured 2026-09-25: two t2-worker workers reported "all fixed" with
    placeholder commit hashes and changed nothing. An isolated run whose job
    is to implement must leave commits or changes, or it failed."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("autoos_agent_cli", str(TOOLS / "autoos-agent.py"))
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def test_implement_run_without_changes_is_a_failure(self):
        rc, msg = self.cli.sandbox_verdict({"review": False}, changed="", ahead="")
        self.assertEqual(rc, 5)
        self.assertIn("NO-OP", msg)

    def test_implement_run_with_a_commit_or_a_change_is_fine(self):
        self.assertEqual(self.cli.sandbox_verdict({"review": False}, changed="", ahead="abc fix")[0], None)
        self.assertEqual(self.cli.sandbox_verdict({"review": False}, changed=" M a.py", ahead="")[0], None)

    def test_a_review_run_may_change_nothing(self):
        self.assertEqual(self.cli.sandbox_verdict({"review": True}, changed="", ahead="")[0], None)


@unittest.skipIf(os.name == "nt", "POSIX process groups; Windows reaps with taskkill /T")
class ProcessGroupTests(unittest.TestCase):
    """Measured 2026-09-25: leftovers (private Serena, language servers) survived
    a cancelled worker. run_client reaps the client's whole process group."""

    def gone(self, pid):
        """True once pid is reaped or a zombie (it is no longer running)."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            with open("/proc/%d/status" % pid, encoding="utf-8") as fh:
                state = next(l for l in fh if l.startswith("State:")).split()[1]
        except (OSError, StopIteration):
            return True
        return state.startswith("Z")

    def test_a_leftover_child_of_the_client_is_reaped(self):
        agent = load_agent()
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "child.pid")
            # The fake client starts `sleep 60` in the same group and exits 0;
            # only the group cleanup can stop the sleep.
            code = ("from subprocess import Popen\n"
                    "p = Popen(['sleep', '60'])\n"
                    "open(%r, 'w').write(str(p.pid))\n" % pidfile)
            rc = agent.run_client([sys.executable, "-c", code], tmp, dict(os.environ))
            self.assertEqual(rc, 0)
            with open(pidfile, encoding="utf-8") as fh:
                pid = int(fh.read())
            deadline = time.time() + 6
            while time.time() < deadline and not self.gone(pid):
                time.sleep(0.1)
            self.assertTrue(self.gone(pid), "leftover child %d survived run_client" % pid)

    def test_the_clients_exit_code_is_returned_unchanged(self):
        agent = load_agent()
        with tempfile.TemporaryDirectory() as tmp:
            rc = agent.run_client([sys.executable, "-c", "raise SystemExit(3)"],
                                  tmp, dict(os.environ))
        self.assertEqual(rc, 3)

    def test_a_joinable_session_is_not_reaped_after_a_normal_exit(self):
        agent = load_agent()
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "child.pid")
            code = ("from subprocess import Popen\n"
                    "p = Popen(['sleep', '30'])\n"
                    "open(%r, 'w').write(str(p.pid))\n" % pidfile)
            rc = agent.run_client([sys.executable, "-c", code], tmp, dict(os.environ), reap=False)
            with open(pidfile, encoding="utf-8") as fh:
                pid = int(fh.read())
            try:
                self.assertEqual(rc, 0)
                self.assertFalse(self.gone(pid), "reap=False stopped the session's child")
            finally:
                os.kill(pid, 9)

    def test_a_failed_joinable_start_is_reaped(self):
        agent = load_agent()
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "child.pid")
            code = ("from subprocess import Popen\n"
                    "p = Popen(['sleep', '30'])\n"
                    "open(%r, 'w').write(str(p.pid))\n"
                    "raise SystemExit(1)\n" % pidfile)
            rc = agent.run_client([sys.executable, "-c", code], tmp, dict(os.environ), reap=False)
            with open(pidfile, encoding="utf-8") as fh:
                pid = int(fh.read())
            deadline = time.time() + 6
            while time.time() < deadline and not self.gone(pid):
                time.sleep(0.1)
            self.assertEqual(rc, 1)
            self.assertTrue(self.gone(pid), "a failed joinable start left %d running" % pid)


class SandboxUniquenessTests(unittest.TestCase):
    """Measured 2026-09-25: two spawns in the same second whose tasks start
    with the same words named the same --isolate clone, and `git clone` failed
    with "fatal: destination path ... already exists" (rc 1). The clone dir and
    its branch must be unique while keeping the readable prefix."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("autoos_agent_unique", str(TOOLS / "autoos-agent.py"))
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def _args(self, task):
        return argparse.Namespace(
            client="opencode", tier=2, card=None, task=task, free=False,
            free_model=self.cli.DEFAULT_FREE_MODEL, lean=False, auto=True,
            joinable=False, model=None, isolate=True, clean=False,
            allow_training=False, max_depth=None, title=None)

    def _two_plans(self, task):
        fixed = datetime.datetime(2026, 9, 26, 12, 0, 0)
        frozen = types.SimpleNamespace(datetime=type(
            "FrozenDatetime", (), {"now": staticmethod(lambda: fixed)}))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"AUTOOS_STATE_DIR": tmp}):
            cfg = self.cli.load_jsonc(str(ROOT / "opencode.jsonc"))
            with mock.patch.object(self.cli, "datetime", frozen):
                return (self.cli.build_plan(self._args(task), cfg),
                        self.cli.build_plan(self._args(task), cfg))

    def test_two_spawns_in_the_same_second_get_different_sandboxes(self):
        first, second = self._two_plans("fix the spawner twice")
        self.assertNotEqual(first["sandbox"]["path"], second["sandbox"]["path"])
        self.assertNotEqual(first["sandbox"]["branch"], second["sandbox"]["branch"])

    def test_the_readable_prefix_is_kept_and_a_short_suffix_added(self):
        first, second = self._two_plans("fix the spawner twice")
        stamp, slug = "20260926-120000", "fix-the-spawner-twice"
        base = "%s-%s-%s" % (os.path.basename(self.cli.ROOT), stamp, slug)
        self.assertTrue(os.path.basename(first["sandbox"]["path"]).startswith(base + "-"),
                        first["sandbox"]["path"])
        self.assertTrue(first["sandbox"]["branch"].startswith("agent/%s-%s-" % (stamp, slug)),
                        first["sandbox"]["branch"])
        for name in (os.path.basename(first["sandbox"]["path"]),
                     first["sandbox"]["branch"].split("/", 1)[1]):
            self.assertRegex(name.rsplit("-", 1)[1], r"^[0-9a-f]{6}$", name)
        self.assertNotEqual(os.path.basename(first["sandbox"]["path"]).rsplit("-", 1)[1],
                            os.path.basename(second["sandbox"]["path"]).rsplit("-", 1)[1])


class SessionTagTests(unittest.TestCase):
    """OR3: every spawned opencode gateway request carries a lane tag in the
    `x-omniroute-session-id` header so OmniRoute call_logs.session_tag can
    attribute the call to a lane/session."""

    @classmethod
    def setUpClass(cls):
        cls.cli = load_agent()

    def _lane(self):
        # The lane part as session_tag() derives it (sanitised, capped): a
        # checkout with a long basename (every lane sandbox) is truncated, so
        # the raw os.path.basename(ROOT) is not the expected value.
        return self.cli.session_tag("t", env={}).rsplit("/", 1)[0]

    def test_fallback_tag_is_a_valid_header_value_for_any_worktree_name(self):
        # review-or3: the ROOT basename was used verbatim - a worktree name with
        # spaces or 120+ chars would emit an illegal header / an over-long tag.
        with mock.patch.object(self.cli, "ROOT", "/x/My Lane \u00e9 " + "w" * 150):
            tag = self.cli.session_tag("Some Title!", env={})
        self.assertRegex(tag, self.cli.SESSION_TAG_RE)

    def test_fallback_tag_keeps_a_plain_worktree_name(self):
        with mock.patch.object(self.cli, "ROOT", "/x/L1-routing-OR3"):
            self.assertEqual(self.cli.session_tag("OR3", env={}), "L1-routing-OR3/or3")

    def _args(self, **overrides):
        ns = argparse.Namespace(
            client="opencode", tier=2, card=None, task="do the thing",
            free=False, free_model=self.cli.DEFAULT_FREE_MODEL,
            isolate=False, auto=True, joinable=False, model=None,
            clean=False, allow_training=False, max_depth=None, lean=False,
            title=None)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def _cfg(self, providers):
        providers.setdefault("omniroute", {"models": {"t2-worker": {}}})
        return {"agents": {"t2-worker": {"model": "omniroute/t2-worker"}},
                "providers": providers}

    def _overlay(self, plan):
        return json.loads(plan["env"].get("OPENCODE_CONFIG_CONTENT", "{}"))

    def test_omniroute_model_carries_the_session_header_from_title(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOOS_SESSION_TAG", None)
            plan = self.cli.build_plan(self._args(title="Fix The Router!"), self._cfg({}))
        prov = self._overlay(plan)["providers"]["omniroute"]
        self.assertEqual(
            prov["headers"]["x-omniroute-session-id"],
            "%s/fix-the-router" % self._lane())

    def test_autoos_session_tag_overrides_the_default(self):
        with mock.patch.dict(os.environ, {"AUTOOS_SESSION_TAG": "lane/one.two_3-x"}):
            plan = self.cli.build_plan(self._args(title="ignored"), self._cfg({}))
        prov = self._overlay(plan)["providers"]["omniroute"]
        self.assertEqual(prov["headers"]["x-omniroute-session-id"], "lane/one.two_3-x")

    def test_an_invalid_tag_falls_back_with_one_warning(self):
        with mock.patch.dict(os.environ, {"AUTOOS_SESSION_TAG": "bad tag with spaces!!"}):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                plan = self.cli.build_plan(self._args(title="T"), self._cfg({}))
        prov = self._overlay(plan)["providers"]["omniroute"]
        self.assertEqual(prov["headers"]["x-omniroute-session-id"],
                         "%s/t" % self._lane())
        warns = [l for l in err.getvalue().splitlines()
                 if "AUTOOS_SESSION_TAG" in l]
        self.assertEqual(len(warns), 1, err.getvalue())

    def test_a_non_omniroute_model_gets_no_header(self):
        cfg = self._cfg({"other": {"models": {"m": {}}}})
        cfg["agents"]["t2-worker"]["model"] = "other/m"
        plan = self.cli.build_plan(self._args(title="T"), cfg)
        self.assertNotIn("providers", self._overlay(plan))

    def test_an_existing_overlay_provider_block_keeps_its_other_keys(self):
        # A pre-existing providers.omniroute block in the overlay (e.g. from a
        # future overlay helper) must be merged into, never replaced.
        cfg = self._cfg({})
        with mock.patch.object(self.cli, "lean_overlay",
                               lambda c: {"providers": {"omniroute": {
                                   "settings": {"baseURL": "http://x/v1"}}}}):
            plan = self.cli.build_plan(self._args(title="T", lean=True), cfg)
        prov = self._overlay(plan)["providers"]["omniroute"]
        self.assertEqual(prov["settings"], {"baseURL": "http://x/v1"})
        self.assertEqual(prov["headers"]["x-omniroute-session-id"],
                         "%s/t" % self._lane())

    def test_the_plan_output_prints_the_session_tag(self):
        r = run_agent("run", "--dry-run", "--tier", "2", "--title", "My Tag",
                      "t", env=clean_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("session-tag: %s/my-tag" % self._lane(),
                      r.stdout)

    def test_a_non_opencode_client_prints_no_session_tag(self):
        r = run_agent("run", "--dry-run", "--client", "gemini", "--tier", "2",
                      "t", env=clean_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("session-tag:", r.stdout)


class HeadlessRefusalTests(unittest.TestCase):
    """Measured 2026-09-25 (run 20260925-215048-85ba11): agy auto-denied a tool
    headless mode cannot prompt for, printed a refusal and still exited 0. A
    zero exit for a refused tool is not a success."""

    AGY_REFUSAL = ('jetski: no output produced - a tool required the "command" '
                   'permission that headless mode cannot prompt for, so it was '
                   'auto-denied\n')

    def setUp(self):
        self.cli = load_agent()

    def test_the_agy_refusal_is_recognised_case_insensitively(self):
        for tail in (self.AGY_REFUSAL, "JETSKI: HEADLESS MODE CANNOT PROMPT",
                     "log line\n  Jetski: No Output Produced\n"):
            msg = self.cli.headless_refusal(tail)
            self.assertIsNotNone(msg, tail)
            self.assertEqual(len(msg.splitlines()), 1, msg)

    def test_an_ordinary_run_is_not_a_refusal(self):
        self.assertIsNone(self.cli.headless_refusal("done\n"))
        self.assertIsNone(self.cli.headless_refusal(""))

    def test_a_worker_quoting_the_marker_is_not_a_refusal(self):
        # A worker whose brief or report quotes this lesson must not exit 6:
        # only agy's own "jetski:" line is the refusal.
        for tail in ('lesson: agy printed "no output produced" and exited 0\n',
                     "REPORT . the headless mode cannot prompt case is handled\n"):
            self.assertIsNone(self.cli.headless_refusal(tail), tail)

    def test_a_zero_exit_refusal_maps_to_six_and_a_message(self):
        rc, msg = self.cli.refusal_exit(0, self.AGY_REFUSAL)
        self.assertEqual(rc, 6)
        self.assertIn("no output produced", msg.lower())
        self.assertEqual(self.cli.refusal_exit(0, "done\n"), (0, None))
        self.assertEqual(self.cli.refusal_exit(1, self.AGY_REFUSAL), (1, None))

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_cmd_run_exits_six_when_the_client_refused(self):
        stub = ("#!/bin/sh\n"
                "case \"$1\" in\n"
                "  models) echo 'gemini-3.8-flash'; exit 0;;\n"
                "esac\n"
                "echo 'jetski: no output produced - a tool required the \"command\" "
                "permission that headless mode cannot prompt for, so it was auto-denied'\n"
                "exit 0\n")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "agy")
        with open(path, "w") as fh:
            fh.write(stub)
        os.chmod(path, 0o755)
        env = clean_env(PATH=d + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
                        AUTOOS_STATE_DIR=d)
        r = run_agent("run", "--client", "agy", "t", env=env)
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("autoos-agent: HEADLESS-REFUSAL:", r.stderr)
        self.assertIn("no output produced", r.stderr)
        # The child's own output still reached our stdout (streamed, not swallowed).
        self.assertIn("no output produced", r.stdout)

    def test_run_client_keeps_the_tail_and_still_prints_the_output(self):
        code = ("import sys\n"
                "sys.stdout.write(%r)\n"
                "sys.stdout.flush()\n" % self.AGY_REFUSAL)
        with tempfile.TemporaryDirectory() as tmp:
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                rc = self.cli.run_client([sys.executable, "-c", code], tmp,
                                         dict(os.environ), capture=True)
        self.assertEqual(rc, 0)
        self.assertIn("no output produced", captured.getvalue())
        self.assertIn("no output produced", rc.tail)
        self.assertIsNotNone(self.cli.headless_refusal(rc.tail))

    def test_a_refusal_before_64_kib_of_later_output_is_still_caught(self):
        # review-spfix: the refusal line scrolled out of the 64 KiB tail.
        code = ("import sys\n"
                "sys.stdout.write(%r + 'x' * 70000 + '\\n')\n"
                "sys.stdout.flush()\n" % self.AGY_REFUSAL)
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = self.cli.run_client([sys.executable, "-c", code], tmp,
                                         dict(os.environ), capture=True)
        self.assertNotIn("no output produced", rc.tail)
        self.assertEqual(self.cli.refusal_exit(0, rc.refusal or "")[0], 6)

    def test_an_uncaptured_client_keeps_the_spawner_stdout(self):
        # review-spfix: capturing hands every client a pipe instead of the
        # operator's terminal; only clients whose refusal we detect are piped.
        code = "import os; print(os.fstat(1).st_ino)"
        out = os.path.join(tempfile.mkdtemp(), "out.txt")
        with open(out, "w") as fh:
            saved = os.dup(1)
            os.dup2(fh.fileno(), 1)
            try:
                rc = self.cli.run_client([sys.executable, "-c", code], ".",
                                         dict(os.environ))
            finally:
                os.dup2(saved, 1)
                os.close(saved)
            inode = os.fstat(fh.fileno()).st_ino
        self.assertEqual(int(rc), 0)
        self.assertEqual(open(out).read().strip(), str(inode))
        self.assertEqual(rc.tail, "")

    def test_only_agy_is_captured(self):
        self.assertEqual(self.cli.CAPTURE_CLIENTS, ("agy",))

    def test_the_tail_keeps_only_the_last_64_kib(self):
        code = ("import sys\n"
                "sys.stdout.write('a' * 70000 + 'THE-END')\n"
                "sys.stdout.flush()\n")
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = self.cli.run_client([sys.executable, "-c", code], tmp,
                                         dict(os.environ), capture=True)
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(rc.tail.encode("utf-8")), 64 * 1024)
        self.assertTrue(rc.tail.endswith("THE-END"))


class KeyFileTests(unittest.TestCase):
    """A lane worktree has no git-ignored api-keys.yml (measured 2026-09-25: exit 3,
    and linking it in was refused); the spawner falls back to the main checkout."""

    def test_a_worktree_reads_the_main_checkouts_key_file(self):
        agent = load_agent()
        with tempfile.TemporaryDirectory() as tmp:
            main = os.path.join(tmp, "main")
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            subprocess.run(git + ["init", "-q", main], check=True)
            subprocess.run(git + ["-C", main, "commit", "-q", "--allow-empty", "-m", "i"], check=True)
            wt = os.path.join(tmp, "lane")
            subprocess.run(git + ["-C", main, "worktree", "add", "-q", wt], check=True)
            os.makedirs(os.path.join(main, "configuration"))
            with open(os.path.join(main, "configuration", "api-keys.yml"), "w", encoding="utf-8") as fh:
                fh.write("omniroute: sk-test-not-a-key\n")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AUTOOS_OMNIROUTE_KEY", None)
                self.assertEqual(agent.client_key(wt), "sk-test-not-a-key")
                self.assertEqual(agent.client_key(main), "sk-test-not-a-key")

    def test_no_key_file_anywhere_is_none(self):
        agent = load_agent()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOOS_OMNIROUTE_KEY", None)
            self.assertIsNone(agent.client_key(tmp))


class CardV2Tests(unittest.TestCase):
    """Task card v2 (spec docs/plans/2026-09-25-routing-v2-spec.md §4).

    normalize_v2 accepts a v1 or a v2 card and returns a v2 dict; every v1
    behaviour (parse_card/normalize/select_combo) stays byte-for-byte. Written
    before the implementation (coding-principles 2).
    """

    def norm(self, card):
        return routing.normalize_v2(card)

    def test_rooted_and_drive_paths_are_refused(self):
        for bad in ("\\\\server\\share", "\\x", "C:x", "~/x"):
            with self.assertRaises(routing.CardError, msg=bad):
                routing.normalize_v2({"paths": [bad]})

    def test_the_v2_tables_are_the_documented_ones(self):
        self.assertEqual(routing.CARD_V2_VALUES, {
            "kind": ("implement", "debug", "review", "plan", "bulk", "research"),
            "risk": ("normal", "high"),
            "spec": ("exact", "partial", "vague"),
            "privacy": ("public", "sensitive"),
            "mode": ("cost-first", "balanced", "quality-first"),
        })
        self.assertEqual(routing.CARD_V2_DEFAULTS, {
            "kind": "implement", "risk": "normal", "spec": "partial",
            "privacy": "public", "mode": "balanced", "deferrable": False,
            "deadline": None, "paths": [], "override": {},
            "author": None,
        })
        # REVROUTE (S2) item 2: author is shared by both dialects and is not a
        # combo input -- it decides who reviews, never what runs.
        self.assertEqual(routing.CARD_SHARED, frozenset({"privacy", "author"}))
        self.assertEqual(routing._CARD_NON_COMBO_SHARED, frozenset({"author"}))

    def test_an_empty_card_is_v2_with_defaults(self):
        out = self.norm({})
        self.assertEqual(out, dict(routing.CARD_V2_DEFAULTS, version="2"))
        self.assertEqual(set(out), set(routing.CARD_V2_DEFAULTS) | {"version"})
        self.assertNotIn("bucket_hint", out)
        self.assertNotIn("min_context", out)
        self.assertNotIn("spend", out)

    def test_v2_keys_are_exactly_the_defaults_plus_version(self):
        out = self.norm({"kind": "review"})
        self.assertEqual(set(out), set(routing.CARD_V2_DEFAULTS) | {"version"})

    def test_v2_defaults_fill_missing_fields(self):
        out = self.norm({"kind": "plan"})
        self.assertEqual(out["risk"], "normal")
        self.assertEqual(out["spec"], "partial")
        self.assertEqual(out["privacy"], "public")
        self.assertEqual(out["mode"], "balanced")
        self.assertIs(out["deferrable"], False)
        self.assertIsNone(out["deadline"])
        self.assertEqual(out["paths"], [])
        self.assertEqual(out["override"], {})
        self.assertEqual(out["version"], "2")

    def test_a_full_v2_card_round_trips(self):
        out = self.norm({
            "kind": "debug", "risk": "high", "spec": "vague",
            "privacy": "sensitive", "mode": "quality-first",
            "deferrable": True, "deadline": "2026-09-26T06:00Z",
            "paths": ["tools/a.py", "tests/b.py"],
            "override": {"route": "t1-orchestrator", "effort": "high"},
        })
        self.assertEqual(out["kind"], "debug")
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["spec"], "vague")
        self.assertEqual(out["privacy"], "sensitive")
        self.assertEqual(out["mode"], "quality-first")
        self.assertIs(out["deferrable"], True)
        self.assertEqual(out["deadline"], "2026-09-26T06:00Z")
        self.assertEqual(out["paths"], ["tools/a.py", "tests/b.py"])
        self.assertEqual(out["override"], {"route": "t1-orchestrator", "effort": "high"})
        self.assertEqual(out["version"], "2")

    def test_a_v2_card_does_not_alias_the_default_containers(self):
        first = self.norm({"kind": "implement"})
        first["paths"].append("a")
        first["override"]["route"] = "x"
        self.assertEqual(routing.normalize_v2({})["paths"], [])
        self.assertEqual(routing.normalize_v2({})["override"], {})

    def test_deferrable_accepts_the_string_and_bool_forms(self):
        self.assertIs(self.norm({"deferrable": True})["deferrable"], True)
        self.assertIs(self.norm({"deferrable": "true"})["deferrable"], True)
        self.assertIs(self.norm({"deferrable": False})["deferrable"], False)
        self.assertIs(self.norm({"deferrable": "false"})["deferrable"], False)

    def test_v1_role_maps_to_kind(self):
        self.assertEqual(self.norm({"role": "orchestrate"})["kind"], "plan")
        self.assertEqual(self.norm({"role": "implement"})["kind"], "implement")
        self.assertEqual(self.norm({"role": "review"})["kind"], "review")

    def test_v1_complexity_maps_to_bucket_hint(self):
        self.assertEqual(self.norm({"complexity": "trivial"})["bucket_hint"], "S0")
        self.assertEqual(self.norm({"complexity": "standard"})["bucket_hint"], "S2")
        self.assertEqual(self.norm({"complexity": "hard"})["bucket_hint"], "S3")

    def test_v1_ctx_maps_to_min_context(self):
        self.assertEqual(self.norm({"ctx": "128k"})["min_context"], 128000)
        self.assertEqual(self.norm({"ctx": "1m"})["min_context"], 1000000)

    def test_v1_defaults_fill_missing_v1_fields(self):
        out = self.norm({"role": "review"})
        self.assertEqual(out["version"], "1")
        self.assertEqual(out["kind"], "review")
        self.assertEqual(out["bucket_hint"], "S2")
        self.assertEqual(out["min_context"], 128000)
        self.assertEqual(out["spend"], "free-ok")

    def test_v1_output_carries_bucket_min_context_and_spend(self):
        out = self.norm({"role": "implement", "complexity": "hard",
                         "ctx": "1m", "privacy": "sensitive", "spend": "credit"})
        self.assertEqual(out["version"], "1")
        self.assertEqual(out["kind"], "implement")
        self.assertEqual(out["bucket_hint"], "S3")
        self.assertEqual(out["min_context"], 1000000)
        self.assertEqual(out["spend"], "credit")
        self.assertEqual(out["privacy"], "sensitive")
        self.assertEqual(set(out), set(routing.CARD_V2_DEFAULTS)
                         | {"version", "bucket_hint", "min_context", "spend"})

    def test_parse_card_keeps_its_v1_results(self):
        self.assertEqual(routing.parse_card("role=review, privacy=sensitive"),
                         {"role": "review", "privacy": "sensitive"})
        self.assertEqual(routing.parse_card('{"ctx": "1m"}'), {"ctx": "1m"})

    def test_parse_card_keeps_the_dotted_override_keys(self):
        self.assertEqual(
            routing.parse_card("override.route=t1-orchestrator,override.effort=high"),
            {"override.route": "t1-orchestrator", "override.effort": "high"})

    def test_parse_card_keeps_the_pipe_delimited_paths(self):
        self.assertEqual(routing.parse_card("paths=a/b|c/d"), {"paths": "a/b|c/d"})

    def test_key_value_paths_and_override_become_typed(self):
        out = self.norm(routing.parse_card("paths=a/b|c,override.route=t1-orchestrator"))
        self.assertEqual(out["paths"], ["a/b", "c"])
        self.assertEqual(out["override"], {"route": "t1-orchestrator"})

    def test_json_and_key_value_forms_normalize_the_same(self):
        json_out = self.norm(routing.parse_card(
            '{"kind": "debug", "risk": "high", "paths": ["a/b", "c/d"], '
            '"override": {"route": "t1-orchestrator"}}'))
        kv_out = self.norm(routing.parse_card(
            "kind=debug,risk=high,paths=a/b|c/d,override.route=t1-orchestrator"))
        self.assertEqual(json_out, kv_out)

    def test_unknown_v2_field_is_an_error(self):
        with self.assertRaises(routing.CardError) as ctx:
            self.norm({"kind": "implement", "bogus": "x"})
        self.assertIn("bogus", str(ctx.exception))

    def test_unknown_v1_field_is_an_error(self):
        with self.assertRaises(routing.CardError) as ctx:
            self.norm({"role": "implement", "bogus": "x"})
        self.assertIn("bogus", str(ctx.exception))

    def test_bad_v2_value_is_an_error(self):
        for card in ({"kind": "nope"}, {"risk": "medium"}, {"spec": "close"},
                     {"privacy": "secret"}, {"mode": "cheap"}):
            with self.assertRaises(routing.CardError):
                self.norm(card)

    def test_bad_v1_value_is_an_error(self):
        with self.assertRaises(routing.CardError):
            self.norm({"complexity": "enormous"})

    def test_mixing_v1_and_v2_fields_is_an_error(self):
        for card in ({"role": "implement", "kind": "debug"},
                     {"complexity": "hard", "spec": "exact"},
                     {"ctx": "1m", "mode": "quality-first"},
                     {"spend": "credit", "paths": ["a"]},
                     {"role": "implement", "override.route": "t1-orchestrator"}):
            with self.assertRaises(routing.CardError) as ctx:
                self.norm(card)
            self.assertIn("mixes v1 and v2 fields", str(ctx.exception))

    def test_privacy_is_shared_and_never_a_mix(self):
        # The only field both versions know: alone it is a valid (v2) card.
        out = self.norm({"privacy": "sensitive"})
        self.assertEqual(out["privacy"], "sensitive")
        self.assertEqual(out["version"], "2")

    # --- author (brief REVROUTE (S2) item 2) -------------------------------
    # Which model wrote the diff is what decides who may review it, so the
    # field belongs to both card dialects: role=review,author=qwen is how a
    # lane already spells the request.

    def test_author_is_shared_and_never_a_mix(self):
        out = self.norm({"author": "qwen"})
        self.assertEqual(out["author"], "qwen")
        self.assertEqual(out["version"], "2")
        v1 = self.norm({"role": "review", "author": "qwen"})
        self.assertEqual(v1["author"], "qwen")
        self.assertEqual(v1["version"], "1")
        self.assertEqual(v1["kind"], "review")

    def test_a_v2_card_carries_its_author(self):
        self.assertEqual(self.norm({"kind": "review",
                                    "author": "meta_api/muse-spark-1.3-contributor"})
                         ["author"], "meta_api/muse-spark-1.3-contributor")

    def test_an_absent_author_is_none_not_a_default_reviewer(self):
        # No author means the orchestrator did not say who wrote it: the
        # different-family rule cannot run, and inventing an author would let
        # the resolver claim a cross-family review nobody asked for.
        self.assertIsNone(self.norm({"kind": "review"})["author"])

    def test_key_value_form_reads_the_author(self):
        self.assertEqual(self.norm(routing.parse_card("role=review,author=qwen"))
                         ["author"], "qwen")

    def test_an_empty_author_is_an_error(self):
        for card in ({"author": ""}, {"author": "   "}, {"author": None},
                     {"author": 7}):
            with self.assertRaises(routing.CardError):
                self.norm(card)

    def test_the_author_never_changes_the_combo(self):
        # autoos_routing.select_combo is the one launch-time decision; a review
        # card must route to the same combo with or without an author, or the
        # field would be a second, undeclared routing input.
        plain = routing.select_combo({"role": "review"})
        with_author = routing.select_combo({"role": "review", "author": "qwen"})
        self.assertEqual(plain, with_author)

    def test_normalize_v1_keeps_the_author_for_the_spawner(self):
        card = routing.normalize({"role": "review", "author": "qwen"})
        self.assertEqual(card["author"], "qwen")
        self.assertEqual(card["role"], "review")

    def test_absolute_path_is_an_error(self):
        with self.assertRaises(routing.CardError):
            self.norm({"paths": ["/etc/passwd"]})
        with self.assertRaises(routing.CardError):
            self.norm(routing.parse_card("paths=/etc/passwd|a"))

    def test_parent_segment_is_an_error(self):
        with self.assertRaises(routing.CardError):
            self.norm({"paths": ["a/../b"]})
        with self.assertRaises(routing.CardError):
            self.norm({"paths": [".."]})
        with self.assertRaises(routing.CardError):
            self.norm(routing.parse_card("paths=a|../b"))

    def test_deadline_without_deferrable_is_an_error(self):
        with self.assertRaises(routing.CardError):
            self.norm({"deadline": "2026-09-26T06:00Z"})
        with self.assertRaises(routing.CardError):
            self.norm({"deferrable": False, "deadline": "2026-09-26T06:00Z"})
        with self.assertRaises(routing.CardError):
            self.norm({"deferrable": "false", "deadline": "2026-09-26T06:00Z"})

    def test_bad_deadline_is_an_error(self):
        for bad in ("next tuesday", "2026-09-26 06:00", "2026-09-26T06:00",
                    "not-a-date"):
            with self.assertRaises(routing.CardError):
                self.norm({"deferrable": True, "deadline": bad})

    def test_bad_deferrable_is_an_error(self):
        with self.assertRaises(routing.CardError):
            self.norm({"deferrable": "maybe"})
        with self.assertRaises(routing.CardError):
            self.norm({"deferrable": 1})

    def test_override_unknown_key_is_an_error(self):
        with self.assertRaises(routing.CardError):
            self.norm({"override": {"tier": "2"}})
        with self.assertRaises(routing.CardError):
            self.norm({"override.tier": "2"})

    def test_override_non_string_value_is_an_error(self):
        with self.assertRaises(routing.CardError):
            self.norm({"override": {"effort": 5}})

    def test_select_combo_v1_results_are_unchanged(self):
        cases = [
            ({}, ("t2-worker", "public-default")),
            ({"role": "review"}, ("t3-driver", "public-light")),
            ({"privacy": "sensitive"}, ("t2-worker-clean", "sensitive")),
            ({"complexity": "hard"}, ("t1-orchestrator", "public-strong")),
            ({"ctx": "1m", "role": "orchestrate"}, ("t1-orchestrator", "public-1m")),
        ]
        for card, expected in cases:
            self.assertEqual(routing.select_combo(card), expected, card)


def _small_route_registry():
    """A hand-computable registry: two survivable routes (families alpha/beta)
    for a `kind=review,paths=tools/registry.py` card, in the same style as
    tests/test_autoos_resolver.py's PlanTests fixture."""
    return {
        "providers": {"free-p": {"id": "free-p"}, "cheap-p": {"id": "cheap-p"}},
        "models": {
            "free-model": {"id": "free-model", "family": "alpha", "reasoning": False,
                          "effort_ladder": [], "tool_calls": "proven",
                          "price_in": 0.0, "price_out": 0.0, "output_max": 1000,
                          "context_usable": {"tokens": 100000, "source": "default"}},
            "cheap-model": {"id": "cheap-model", "family": "beta", "reasoning": False,
                           "effort_ladder": [], "tool_calls": "proven",
                           "price_in": 1e-6, "price_out": 2e-6, "output_max": 100000,
                           "context_usable": {"tokens": 200000, "source": "default"}},
            "orch": {"id": "orch", "price_in": 5e-6, "price_out": 1e-5,
                    "context_usable": {"tokens": 200000, "source": "default"}},
        },
        "routes": {
            "r-free": {"id": "r-free", "class": "free", "legs": ["free-p/free-model"]},
            "r-cheap": {"id": "r-cheap", "class": "cheap", "legs": ["cheap-p/cheap-model"]},
        },
        "policy": {
            "modes": {"balanced": {"theta": {"value": 0.8}, "lambda": {"value": 0.01}}},
            "verify_tokens": {"S0": {"tokens": 2000}},
            "latency_seed": {"free": {"minutes": 5}, "cheap": {"minutes": 10}},
            "seed_priors": {
                "free": {"S0": {"alpha": 8, "beta": 2}},
                "cheap": {"S0": {"alpha": 8, "beta": 2}},
            },
        },
    }


def _fake_client_state():
    return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}


class _FakeClients:
    """A clients module stand-in whose one client is always installed and
    carries no sign-in probe (`signed_in: None` never removes a route, spec
    5.3's own rule) - so `cmd_route` can be exercised without touching a real
    CLI or the network. `binary` is this interpreter's own absolute path, so
    shutil.which resolves it on every OS without depending on PATH."""

    CLIENTS = {"opencode": types.SimpleNamespace(binary=sys.executable)}

    @staticmethod
    def signin_state(client, env=None):
        return None, ""


class RouteCliTests(unittest.TestCase):
    """tools/autoos-agent.py `route` (spec 6.1) and its shared core
    route_plan_for (spec 6.1/6.2, shared with the MCP `route` tool).
    """

    def setUp(self):
        self.agent = load_agent()

    def now(self):
        return datetime.datetime(2026, 9, 29, 9, 0, tzinfo=datetime.timezone.utc)

    def isolate(self):
        """Point the freshly loaded agent module at a controlled, empty
        overlay/track-record and a fake clients module - registry stays
        whatever the caller passes to route_plan_for directly."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.agent.MEASURED_OVERLAY_PATH = os.path.join(tmp, "measured.json")
        self.agent.TRACK_RECORD = os.path.join(tmp, "track-record.jsonl")
        self.agent.clients = _FakeClients

    def test_route_plan_for_with_inline_registry_and_fake_state_returns_a_plan(self):
        result = self.agent.route_plan_for(
            "kind=review,paths=tools/registry.py", "", str(ROOT), "orch",
            self.now(), _small_route_registry(), {}, [], _fake_client_state())
        self.assertIn(result["route"], ("r-free", "r-cheap"))
        self.assertEqual(result["state"], "ready")

    def test_no_route_card_is_input_required(self):
        result = self.agent.route_plan_for(
            "kind=review,paths=tools/registry.py,override.route=zzz-nonexistent",
            "", str(ROOT), "orch", self.now(), _small_route_registry(), {}, [],
            _fake_client_state())
        self.assertIsNone(result["route"])
        self.assertEqual(result["state"], "input_required")

    def test_bad_card_raises_card_error(self):
        with self.assertRaises(routing.CardError) as cm:
            self.agent.route_plan_for("bogus=x", "", str(ROOT), "orch", self.now(),
                                      _small_route_registry(), {}, [],
                                      _fake_client_state())
        self.assertIn("bogus", str(cm.exception))

    def test_unknown_orchestrator_model_raises_naming_it(self):
        with self.assertRaises(ValueError) as cm:
            self.agent.route_plan_for(
                "kind=review,paths=tools/registry.py", "", str(ROOT),
                "no-such-model", self.now(), _small_route_registry(), {}, [],
                _fake_client_state())
        self.assertIn("no-such-model", str(cm.exception))

    def test_dict_card_is_accepted_too(self):
        result = self.agent.route_plan_for(
            {"kind": "review", "paths": ["tools/registry.py"]}, "", str(ROOT),
            "orch", self.now(), _small_route_registry(), {}, [],
            _fake_client_state())
        self.assertIn(result["route"], ("r-free", "r-cheap"))

    def test_real_registry_review_card_returns_a_route(self):
        registry = json.loads((ROOT / "catalog" / "ai-registry.json")
                              .read_text(encoding="utf-8"))
        result = self.agent.route_plan_for(
            "kind=review,paths=tools/registry.py", "", str(ROOT),
            self.agent.DEFAULT_ORCHESTRATOR_MODEL, self.now(), registry, {}, [],
            _fake_client_state())
        self.assertIsNotNone(result["route"])
        self.assertEqual(result["state"], "ready")
        self.assertIn(result["route"], registry["routes"])

    # --- cmd_route: exit codes and the --explain stderr/stdout split --------

    def run_cmd_route(self, **overrides):
        ns = argparse.Namespace(card="kind=review,paths=tools/registry.py", brief="",
                                explain=False, orchestrator_model="orch",
                                repo=str(ROOT), now=None)
        for key, value in overrides.items():
            setattr(ns, key, value)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.object(self.agent, "load_registry",
                                   lambda path: _small_route_registry()):
                rc = self.agent.cmd_route(ns)
        return rc, out.getvalue(), err.getvalue()

    def test_cmd_route_exits_0_and_prints_json_on_stdout(self):
        self.isolate()
        rc, out, err = self.run_cmd_route()
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["state"], "ready")

    def test_cmd_route_exits_5_when_no_route_survives(self):
        self.isolate()
        rc, out, err = self.run_cmd_route(
            card="kind=review,paths=tools/registry.py,override.route=zzz-nonexistent")
        self.assertEqual(rc, 5, err)
        self.assertIsNone(json.loads(out)["route"])

    def test_cmd_route_exits_2_on_a_bad_card(self):
        self.isolate()
        rc, out, err = self.run_cmd_route(card="bogus=x")
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("bogus", err)

    def test_cmd_route_exits_2_on_an_unknown_orchestrator_model(self):
        self.isolate()
        rc, out, err = self.run_cmd_route(orchestrator_model="no-such-model")
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("no-such-model", err)

    def test_explain_writes_lines_to_stderr_and_stdout_stays_json(self):
        self.isolate()
        rc, out, err = self.run_cmd_route(explain=True)
        self.assertEqual(rc, 0, err)
        data = json.loads(out)  # stdout is exactly one parseable route_plan
        self.assertTrue(err.strip())
        self.assertIn(data["reason"], err)

    def test_cli_bad_card_exits_2(self):
        r = run_agent("route", "--card", "bogus=x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bogus", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_cli_no_route_card_exits_5(self):
        # override to an id no registry has forces input_required regardless
        # of this host's own client sign-in state (spec 5.3: an override never
        # resurrects a filtered route, and an unknown one is refused the same way).
        r = run_agent("route", "--card",
                      "kind=review,paths=tools/registry.py,override.route=zzz-nonexistent")
        self.assertEqual(r.returncode, 5, r.stderr)
        data = json.loads(r.stdout)
        self.assertIsNone(data["route"])
        self.assertEqual(data["state"], "input_required")


    # --- record_probe after provider_stop (FUP 2026-09-27) -----------------

    def test_promo_client_with_provider_stop_skips_record_probe(self):
        """A promo client (qoder) whose tail contains a provider-stop marker
        must NOT record a probe: the record_probe call happens AFTER the
        provider-stop upgrade, so rc=8 never reaches rc==0."""
        # Set up overlay/track paths (like isolate()) but keep the real
        # clients module so qoder is available.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.agent.MEASURED_OVERLAY_PATH = os.path.join(tmp, "measured.json")
        self.agent.TRACK_RECORD = os.path.join(tmp, "track-record.jsonl")
        # Build a minimal fake plan so cmd_run does not need the real
        # route-resolution machinery.
        fake_plan = {
            "agent": "t2-worker",
            "client": "qoder",
            "model": "qoder-model",
            "cmd": ["qodercli", "do the thing"],
            "env": {},
            "route": {"combo": "qoder-model", "reason": "test",
                      "privacy": "public", "review": False, "tier": 2,
                      "card": None},
            "depth": (1, 3),
            "free": False,
            "sandbox": None,
            "cwd": os.getcwd(),
        }
        ns = argparse.Namespace(
            client="qoder", task="do the thing",
            free=False, dry_run=False, card=None,
            clean=False, tier=2, joinable=False,
            lean=False, isolate=False, auto=False,
            title=None, model=None, free_model=None,
            max_depth=None, plan=None, allow_training=False,
        )
        with mock.patch.object(self.agent, "build_plan", return_value=fake_plan):
            with mock.patch.object(self.agent, "run_client") as mock_run:
                mock_run.return_value = self.agent.ClientExit(
                    0, tail="working...\n"
                    "Error: your personal credits have been exhausted\n")
                with mock.patch.object(
                        self.agent.clients, "record_probe") as mock_record:
                    with mock.patch.object(
                            self.agent.clients, "signin_state",
                            return_value=(None, "")):
                        with mock.patch(
                                "shutil.which",
                                return_value="/usr/bin/qodercli"):
                            out, err = io.StringIO(), io.StringIO()
                            with contextlib.redirect_stdout(out), \
                                    contextlib.redirect_stderr(err):
                                rc = self.agent.cmd_run(ns, {})
        self.assertEqual(rc, 8, "provider stop must upgrade rc to 8")
        mock_record.assert_not_called()


class RunCardV2Tests(unittest.TestCase):
    """`run --card` v2 routes through the resolver (RUNV2, spec 6.1 "run takes
    card v2"). No network, no real clients: MEASURED_OVERLAY_PATH/TRACK_RECORD
    point at an empty tmp dir, `load_registry` is patched to the hand-computable
    _small_route_registry() fixture (same one RouteCliTests uses), and
    measure_mod.client_state is patched to _fake_client_state() so the resolver's
    own client-installed/signed-in filter never shells out to a real client.
    `clients.CLIENTS` itself is left real so `cmd_run`'s own client lookup
    (`.gateway`, `.promo`, `.name`, ...) keeps working unchanged.
    """

    def setUp(self):
        self.agent = load_agent()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.agent.MEASURED_OVERLAY_PATH = os.path.join(tmp, "measured.json")
        self.agent.TRACK_RECORD = os.path.join(tmp, "track-record.jsonl")
        # _small_route_registry()'s only orchestrator-priceable model is "orch".
        self.agent.DEFAULT_ORCHESTRATOR_MODEL = "orch"
        registry_patch = mock.patch.object(self.agent, "load_registry",
                                           lambda path: _small_route_registry())
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        state_patch = mock.patch.object(self.agent.measure_mod, "client_state",
                                        lambda *a, **k: _fake_client_state())
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def cfg(self):
        """opencode.jsonc-shaped: every v1 combo plus the small registry's own
        route ids ("r-free"/"r-cheap") declared under omniroute, so
        resolve_model never refuses a combo this fixture can actually pick."""
        names = list(routing.ALL_COMBOS) + ["r-free", "r-cheap"]
        return {"providers": {"omniroute": {"models": {n: {} for n in names}}}}

    def args(self, **overrides):
        ns = argparse.Namespace(
            tier=None, card="kind=review,paths=tools/registry.py", allow_training=False,
            client="opencode", joinable=False, max_depth=None, clean=False, model=None,
            free=False, free_model=self.agent.DEFAULT_FREE_MODEL, isolate=False, auto=True,
            lean=False, title=None, dry_run=True, task="x", no_defer=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def run_cmd_run(self, **overrides):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.agent.cmd_run(self.args(**overrides), self.cfg())
        return rc, out.getvalue(), err.getvalue()

    # --- a v2 card is routed by the resolver, not select_combo ------------

    def test_v2_card_routes_via_plan_combo_is_the_resolvers_route(self):
        rc, out, err = self.run_cmd_run()
        self.assertEqual(rc, 0, err)
        route_line = next(l for l in out.splitlines() if l.startswith("route: "))
        combo = route_line.split()[1]
        self.assertIn(combo, ("r-free", "r-cheap"))
        self.assertIn("reason=resolver-v2:", route_line)

    def test_v1_card_never_calls_the_resolver(self):
        def boom(*a, **k):
            raise AssertionError("route_plan_for must not be called for a v1 card")
        with mock.patch.object(self.agent, "route_plan_for", boom):
            rc, out, err = self.run_cmd_run(card="role=review")
        self.assertEqual(rc, 0, err)
        self.assertIn("route: t3-driver reason=public-light", out)

    def test_empty_card_never_calls_the_resolver(self):
        def boom(*a, **k):
            raise AssertionError("route_plan_for must not be called for an empty card")
        with mock.patch.object(self.agent, "route_plan_for", boom):
            rc, out, err = self.run_cmd_run(card="")
        self.assertEqual(rc, 0, err)
        self.assertIn("route: t2-worker reason=public-default", out)

    # --- tier comes from the route id's own t<1-3>- prefix, else tier 2 ----

    def test_a_route_without_a_recognised_tier_prefix_defaults_to_tier_2(self):
        self.assertEqual(self.agent._tier_for_route("r-free"), 2)
        self.assertEqual(self.agent._tier_for_route("t4-rag"), 2)
        self.assertEqual(self.agent._tier_for_route("deepseek-v4.1-flash"), 2)

    def test_a_recognised_prefix_picks_its_own_tier(self):
        self.assertEqual(self.agent._tier_for_route("t1-orchestrator-free-only"), 1)
        self.assertEqual(self.agent._tier_for_route("t2-worker-clean"), 2)
        self.assertEqual(self.agent._tier_for_route("t3-driver-clean"), 3)

    # --- input_required / deferred / --no-defer -----------------------------

    # FUP (2026-09-27): refuse empty or whitespace-only task before any
    # clone or client start (measured: $(cat missing-file) produced '' and
    # a worker chatted twice before the route planner caught it).

    def test_empty_task_is_refused_with_exit_2(self):
        for task in ("", "   ", "\t\n"):
            with self.subTest(task=repr(task)):
                rc, out, err = self.run_cmd_run(task=task)
                self.assertEqual(rc, 2, "task=%r: rc=%d err=%s" % (task, rc, err))
                self.assertIn("empty", err)

    def test_input_required_state_refuses_with_exit_2_and_the_plans_reason(self):
        fake = {"route": None, "state": "input_required",
               "reason": "no route survives the filters: boom", "bucket": "S0"}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(fake)):
            rc, out, err = self.run_cmd_run()
        self.assertEqual(rc, 2)
        self.assertIn("no route survives the filters: boom", err)
        self.assertEqual(out, "")

    def test_deferred_state_refuses_with_exit_2_and_the_defer_time(self):
        fake = {"route": "r-cheap", "state": "deferred", "bucket": "S1",
               "defer_until": "2026-10-01T00:00Z", "reason": "defer: cheap window ahead"}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(fake)):
            rc, out, err = self.run_cmd_run()
        self.assertEqual(rc, 2)
        self.assertIn("deferred until 2026-10-01T00:00Z: defer: cheap window ahead", err)
        self.assertEqual(out, "")

    def test_no_defer_ignores_the_deferral_and_runs_now(self):
        fake = {"route": "r-cheap", "state": "deferred", "bucket": "S1",
               "defer_until": "2026-10-01T00:00Z", "reason": "defer: cheap window ahead"}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(fake)):
            rc, out, err = self.run_cmd_run(no_defer=True)
        self.assertEqual(rc, 0, err)
        self.assertIn("route: r-cheap", out)
        self.assertIn("reason=resolver-v2", out)

    # --- the track entry records the resolver's own bucket ------------------

    def test_track_entry_uses_the_plans_bucket_not_unknown(self):
        plan = {"client": "opencode", "route": {"combo": "t2-worker", "card": {}, "bucket": "S2"}}
        self.assertEqual(self.agent.track_entry(plan, 0, 1.0)["bucket"], "S2")

    def test_track_entry_without_a_bucket_still_falls_back_to_unknown(self):
        plan = {"client": "opencode", "route": {"combo": "t2-worker", "card": {}}}
        self.assertEqual(self.agent.track_entry(plan, 0, 1.0)["bucket"], "unknown")

    # --- review-runv2-a4b: the class is the route's own, not its t1/t2/t3 prefix

    def test_track_entry_uses_the_routes_class_over_the_tier_prefix(self):
        plan = {"client": "opencode", "route": {"combo": "t1-orchestrator-free-only",
                                                "class": "free", "card": {}}}
        self.assertEqual(self.agent.track_entry(plan, 0, 1.0)["class"], "free")

    def test_track_entry_skips_a_keyless_free_run(self):
        # review-l1own (qoder): --free swaps in a promo model outside the gateway
        # route, so the run is no observation of that route.
        plan = {"client": "opencode", "free": True,
                "route": {"combo": "gemini-3.8-flash", "class": "cheap", "card": {}}}
        self.assertIsNone(self.agent.track_entry(plan, 0, 1.0))

    def test_build_plan_marks_a_free_run(self):
        plan = self.agent.build_plan(self.args(free=True), self.cfg())
        self.assertTrue(plan["free"])

    def test_track_entry_records_a_route_without_a_tier_prefix(self):
        plan = {"client": "opencode", "route": {"combo": "gemini-3.8-flash",
                                                "class": "cheap", "card": {}}}
        entry = self.agent.track_entry(plan, 0, 1.0)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["class"], "cheap")

    def test_v2_route_carries_the_registry_class_of_its_combo(self):
        # r-cheap has no t1/t2/t3 prefix: its class comes from the registry only
        fake = {"route": "r-cheap", "state": "ready", "reason": "stub",
                "bucket": "S1", "defer_until": None}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(fake)):
            route = self.agent._resolve_route_v2(
                self.args(), {"kind": "review", "paths": "tools/registry.py"}, self.cfg(), None)
        self.assertEqual(route["class"], "cheap")


class ModelOverridePrivacyTests(unittest.TestCase):
    """PRIV3 (review-priv, qoder 2026-09-26): an explicit --model replaced a
    sensitive card's -clean combo with no privacy re-check, so
    `--card privacy=sensitive --model omniroute/t3-driver` ran private work on
    the mistral-code free pool. Every leg the gateway serves for the chosen
    combo must be private-safe (tools/registry.py private_safe)."""

    def setUp(self):
        self.agent = load_agent()

    def cfg(self):
        names = list(routing.ALL_COMBOS)
        return {"providers": {"omniroute": {"models": {n: {} for n in names}}}}

    def run_cmd(self, **overrides):
        ns = argparse.Namespace(
            tier=None, card="privacy=sensitive", allow_training=False,
            client="opencode", joinable=False, max_depth=None, clean=False, model=None,
            free=False, free_model=self.agent.DEFAULT_FREE_MODEL, isolate=False, auto=True,
            lean=False, title=None, dry_run=True, task="x", no_defer=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.agent.cmd_run(ns, self.cfg())
        return rc, out.getvalue(), err.getvalue()

    def test_sensitive_card_with_a_free_pool_model_is_refused(self):
        rc, out, err = self.run_cmd(model="omniroute/t3-driver")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("privacy", err)

    def test_sensitive_card_with_a_clean_model_runs(self):
        rc, out, err = self.run_cmd(model="omniroute/t2-worker-clean")
        self.assertEqual(rc, 0, err)

    def test_clean_tier_maps_a_free_pool_model_to_its_clean_twin(self):
        # --tier --clean already resolves an override to its -clean twin
        # (resolve_model), so the PRIV3 check sees a private-safe combo.
        rc, out, err = self.run_cmd(card=None, tier=2, clean=True, model="omniroute/t3-driver")
        self.assertEqual(rc, 0, err)
        self.assertIn("route: t3-driver-clean", out)

    def test_v2_sensitive_card_with_a_free_pool_model_is_refused(self):
        plan = {"route": "t3-driver-clean", "state": "ready", "reason": "stub",
                "bucket": "S1", "defer_until": None}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: plan), \
                mock.patch.object(self.agent.measure_mod, "client_state", lambda *a, **k: {}):
            rc, out, err = self.run_cmd(card="kind=review,paths=tools/registry.py,privacy=sensitive",
                                        model="omniroute/t3-driver")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("privacy", err)

    def test_sensitive_card_with_free_is_refused(self):
        # close-priv 2026-09-26: --free swapped a sensitive card's -clean combo
        # for the promo model (now opencode/muse-spark-1.3-contributor-free,
        # may train on prompts).
        for card in ("privacy=sensitive", "kind=review,paths=tools/registry.py,privacy=sensitive"):
            with mock.patch.object(self.agent.measure_mod, "client_state", lambda *a, **k: {}), \
                    mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: {
                        "route": "t3-driver-clean", "state": "ready", "reason": "stub",
                        "bucket": "S1", "defer_until": None}):
                rc, out, err = self.run_cmd(card=card, free=True)
            self.assertEqual(rc, 2, card + out + err)
            self.assertIn("privacy", err)
            self.assertNotIn("muse-spark", out)

    def test_public_card_with_any_model_is_not_checked(self):
        rc, out, err = self.run_cmd(card="privacy=public", model="omniroute/t3-driver")
        self.assertEqual(rc, 0, err)

    def test_allow_training_no_longer_opens_the_sensitive_1m_route(self):
        # Compatibility flag, inert since DSMAX 2026-09-27: the card fails
        # closed before the explicit --model is even considered.
        rc, out, err = self.run_cmd(card="privacy=sensitive,ctx=1m", allow_training=True,
                                    model="omniroute/t2-worker-clean")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("128k", err)


class RunCardV2AcceptanceTests(unittest.TestCase):
    """The exact command RUNV2's done-when names, against the real repo: real
    registry, real opencode.jsonc, real clients (opencode has no sign-in probe;
    the only client with one, agy, answers in ~1-2s per autoos_clients.py's own
    measurement)."""

    def test_run_card_kind_review_dry_run_prints_a_plan_on_the_real_repo(self):
        # CI runners install no client, so every route is removed there
        # (CI 36241451890); the faked-client cmd_run tests above cover the logic.
        # the resolver plans for opencode, so only opencode on PATH counts
        # (review-l1own: another installed client did not help)
        if shutil.which(clients.CLIENTS["opencode"].binary) is None:
            self.skipTest("opencode is not installed on this host")
        r = run_agent("run", "--card", "kind=review,paths=tools/registry.py",
                      "--dry-run", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("reason=resolver-v2", r.stdout)
        self.assertIn("would run:", r.stdout)


class RunCardV2PrivacyTests(unittest.TestCase):
    """RUNV2 brief step 4: a sensitive v2 card must never resolve to a route
    with an unsafe serving leg. Originally pinned to "-clean"-suffixed combo
    names (the only privacy-safe routes that existed then); Q1 2026-09-26
    added genuinely private-safe pinned routes with no "-clean" suffix
    (cheaperinference/glm-5.2: paid tier, trains_on_prompts false, confirmed
    - see catalog/ai-registry.json providers.cheapinference's own $comment),
    so the naming-based check is replaced by the actual safety predicate
    (tools/registry.py private_safe) every serving leg must satisfy - the
    same property tests/test_autoos_resolver.py's
    test_real_registry_sensitive_implement_card_never_picks_an_unsafe_leg
    already pins at the raw resolver level. Exercises the real registry +
    overlay end to end (client state is faked so it never shells out); skips
    itself when the resolver has no route at all for this card on this
    host's data, rather than asserting a route exists (that is the other
    lane's privacy filter to prove, not RUNV2's)."""

    def test_sensitive_v2_card_only_ever_yields_a_private_safe_route(self):
        agent = load_agent()
        registry = json.loads((ROOT / "catalog" / "ai-registry.json")
                              .read_text(encoding="utf-8"))
        overlay = agent.load_overlay(agent.MEASURED_OVERLAY_PATH)
        track_record = agent.track.load(agent.TRACK_RECORD)
        result = agent.route_plan_for(
            "kind=review,paths=tools/registry.py,privacy=sensitive", "", str(ROOT),
            agent.DEFAULT_ORCHESTRATOR_MODEL,
            datetime.datetime.now(datetime.timezone.utc), registry, overlay,
            track_record, _fake_client_state())
        if result["route"] is None:
            self.skipTest("no route survives for a sensitive card on this host's "
                          "registry/overlay (%s)" % result["reason"])
        route = registry["routes"][result["route"]]
        for provider_id, model_id in resolver.serving_legs(route, registry):
            safe, reason = registry_tool.private_safe(provider_id, model_id, registry)
            self.assertTrue(
                safe, "sensitive card routed to %r, leg %s/%s is not private-safe: %s"
                     % (result["route"], provider_id, model_id, reason))


class McpRouteTests(unittest.TestCase):
    """The route/list_agents/context MCP tools as plain functions (spec 6.2)."""

    def test_route_returns_a_dict_for_the_real_registry(self):
        out = mcp_server.route_plan("kind=review,paths=tools/registry.py")
        self.assertNotIn("error", out)
        self.assertIn("route", out)
        self.assertIn("state", out)

    def test_route_error_is_a_plain_dict(self):
        out = mcp_server.route_plan("bogus=x")
        self.assertEqual(set(out), {"error"})
        self.assertIn("bogus", out["error"])

    def test_route_accepts_a_dict_card_and_explain_adds_text(self):
        out = mcp_server.route_plan({"kind": "review", "paths": ["tools/registry.py"]},
                                    explain=True)
        self.assertNotIn("error", out)
        # review-b5a4: asserted unconditionally - a no-route result must not
        # silently skip the explain check.
        self.assertIn("explain_text", out)
        self.assertIn(out["reason"], out["explain_text"])

    def test_route_never_raises_on_a_wrong_type(self):
        # review-b5a4: an int card raised TypeError through the MCP tool.
        out = mcp_server.route_plan(5)
        self.assertEqual(set(out), {"error"})

    def test_context_never_raises_when_the_transcript_vanishes(self):
        # review-b5a4: context_state's discovered-transcript branch opened
        # the file outside its OSError guard.
        original = mcp_server.agent.context_state
        def boom(*a, **k):
            raise OSError("vanished")
        mcp_server.agent.context_state = boom
        try:
            out = mcp_server.context_info(None)
        finally:
            mcp_server.agent.context_state = original
        self.assertEqual(set(out), {"error"})

    def test_list_agents_returns_clients_and_routes(self):
        out = mcp_server.list_agents()
        self.assertNotIn("error", out)
        self.assertTrue(out["clients"])
        for row in out["clients"]:
            self.assertEqual(set(row), {"id", "installed", "signed_in", "reason"})
        for row in out["routes"]:
            self.assertEqual(set(row), {"id", "class", "legs", "retired"})
            for leg in row["legs"]:
                self.assertEqual(set(leg), {"leg", "available"})

    def test_list_agents_reads_unavailable_until_at_the_current_clock(self):
        # UNTILfix: the per-leg `available` flag must come from
        # registry.unavailable_now, the one availability rule, so an operator
        # sees exactly what the resolver's hard filter applies.
        registry = {
            "clients": {},
            "providers": {
                "ahead": {"id": "ahead", "available": True,
                          "unavailable_until": "2999-01-01T00:00:00Z"},
                "healed": {"id": "healed", "available": False,
                           "unavailable_until": "2000-01-01T00:00:00Z"},
            },
            "models": {"big": {"id": "big"}},
            "routes": {"r": {"id": "r",
                             "legs": ["ahead/big", "healed/big"]}},
        }
        with mock.patch.object(mcp_server.agent, "load_registry",
                               return_value=registry), \
                mock.patch.object(mcp_server.agent.measure_mod,
                                  "client_state", return_value={}):
            out = mcp_server.list_agents()
        self.assertNotIn("error", out)
        legs = {row["leg"]: row["available"]
                for row in out["routes"][0]["legs"]}
        self.assertFalse(legs["ahead/big"])
        self.assertTrue(legs["healed/big"])

    def test_context_returns_a_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                out = mcp_server.context_info()
        self.assertEqual(out, {"context": "unknown", "reason": "no transcript"})

    def test_context_bad_transcript_path_is_an_error(self):
        out = mcp_server.context_info(
            transcript=os.path.join(tempfile.gettempdir(), "autoos-test-nope.jsonl"))
        self.assertEqual(set(out), {"error"})


_FAKE_ISOLATE_AGY_SRC = '''
import os, subprocess, sys
root = os.environ["AUTOOS_FAKE_ROOT"]
mode = os.environ.get("AUTOOS_FAKE_MODE", "noop")
def git(*a):
    subprocess.run(["git", "-C", root, *a], check=True,
                   capture_output=True, text=True)
if mode == "commit-worker":
    with open(os.path.join(root, "worker-file.txt"), "w") as fh:
        fh.write("worker\\n")
    git("add", "worker-file.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "worker change")
    print("fake: committed as worker")
elif mode == "modify":
    with open(os.path.join(root, "tracked.txt"), "a") as fh:
        fh.write("dirty\\n")
    print("fake: modified a tracked file")
elif mode == "commit-other":
    with open(os.path.join(root, "other-file.txt"), "w") as fh:
        fh.write("other\\n")
    git("add", "other-file.txt")
    git("-c", "user.name=someone-else",
        "-c", "user.email=someone@example.invalid",
        "commit", "-q", "-m", "other change")
    print("fake: committed as someone else")
elif mode == "merge-worker-lane":
    # The orchestrator merges a finished worker lane (--no-ff) while this
    # run is going: the lane's commits are autoos-worker's but not a leak.
    git("switch", "-q", "-c", "lane")
    with open(os.path.join(root, "lane-file.txt"), "w") as fh:
        fh.write("lane\\n")
    git("add", "lane-file.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "lane change")
    git("switch", "-q", "-")
    git("-c", "user.name=orch", "-c", "user.email=orch@example.invalid",
        "merge", "-q", "--no-ff", "-m", "merge lane", "lane")
    print("fake: orchestrator merged a worker lane")
elif mode == "amend-worker":
    # The worker amends the parent tip: author stays the orchestrator's, the
    # committer is the worker's (review 2026-09-26: a %ae-only scan misses it).
    with open(os.path.join(root, "tracked.txt"), "a") as fh:
        fh.write("amended\\n")
    git("add", "tracked.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "--amend", "--no-edit")
    print("fake: amended the parent tip as committer=worker")
elif mode == "commit-reset":
    # The worker commits and resets back: HEAD ends where it started and the
    # porcelain stays clean - only the branch reflog saw it.
    with open(os.path.join(root, "tracked.txt"), "a") as fh:
        fh.write("gone\\n")
    git("add", "tracked.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "worker change")
    git("reset", "-q", "--hard", "HEAD^")
    print("fake: committed as worker, then reset back")
elif mode == "fetch-lane-new-ref":
    # The orchestrator fetches a finished worker lane into a NEW ref mid-run:
    # worker-authored, but never a leak (R: test_a_merged_worker_lane...).
    git("checkout", "-q", "-b", "lane")
    with open(os.path.join(root, "lane2.txt"), "w") as fh:
        fh.write("lane2\\n")
    git("add", "lane2.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "lane2 change")
    git("checkout", "-q", "-")
    git("update-ref", "refs/heads/lane2", "lane")
    git("branch", "-q", "-D", "lane")
    print("fake: orchestrator fetched a worker lane into a new ref")
elif mode == "commit-other-branch":
    # The worker commits on `side`, an existing branch that is not checked
    # out (make_root created it before the snapshot): HEAD and the
    # checked-out tree never move.
    back = subprocess.run(["git", "-C", root, "branch", "--show-current"],
                          check=True, capture_output=True, text=True).stdout.strip()
    git("switch", "-q", "side")
    with open(os.path.join(root, "tracked.txt"), "a") as fh:
        fh.write("side\\n")
    git("add", "tracked.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "worker side change")
    git("switch", "-q", back)
    print("fake: committed as worker on another existing branch")
elif mode == "commit-sibling-worktree":
    # Another lane's worker commits on ITS OWN branch, checked out in a
    # sibling worktree of the same .git (all lanes share one repository):
    # that branch moves, but it is not a leak into this run's parent.
    wt = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                        check=True, capture_output=True, text=True).stdout
    paths = [ln[len("worktree "):] for ln in wt.splitlines() if ln.startswith("worktree ")]
    sib = [p for p in paths if os.path.realpath(p) != os.path.realpath(root)][0]
    with open(os.path.join(sib, "sib.txt"), "w") as fh:
        fh.write("sibling\\n")
    subprocess.run(["git", "-C", sib, "add", "sib.txt"], check=True)
    subprocess.run(["git", "-C", sib, "-c", "user.name=autoos-worker",
                    "-c", "user.email=autoos-worker@users.noreply.github.com",
                    "commit", "-q", "-m", "sibling lane worker change"], check=True)
    print("fake: a sibling lane's worker committed in its own worktree")
elif mode == "parent-ff-onto-lane":
    # LEAKFP A (review-fold2e.out): the orchestrator fast-forwards the parent
    # worktree's OWN branch onto another lane's worker commits while this
    # (read-only review) run is going. HEAD's first-parent chain now carries
    # autoos-worker commits it never wrote.
    git("switch", "-q", "-c", "lane-ff")
    with open(os.path.join(root, "lane-ff.txt"), "w") as fh:
        fh.write("lane\\n")
    git("add", "lane-ff.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "another lane's worker change")
    git("switch", "-q", "-")
    git("-c", "user.name=orch", "-c", "user.email=orch@example.invalid",
        "merge", "-q", "--ff-only", "lane-ff")
    print("fake: parent branch fast-forwarded onto another lane's commits")
elif mode == "ff-old-lane-commit":
    # LEAKFP A, harder shape: the lane finished BEFORE this run (its commit is
    # dated years back, made in another writer's clone so this worktree's HEAD
    # reflog never saw it) and the orchestrator deletes the lane branch after
    # the fast-forward, so no ref but the parent's own carries it -- only the
    # commit's timestamp says it cannot be this worker's.
    import shutil as _sh
    import tempfile as _tf
    old = dict(os.environ)
    old["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00Z"
    old["GIT_COMMITTER_DATE"] = "2020-01-01T00:00:00Z"
    par = _tf.mkdtemp(prefix="autoos-fake-lane-")
    other = os.path.join(par, "lane")
    git("clone", "-q", "--local", root, other)

    def ogit(*a):
        subprocess.run(["git", "-C", other, *a], check=True,
                       capture_output=True, text=True, env=old)
    ogit("switch", "-q", "-c", "lane-old")
    with open(os.path.join(other, "lane-old.txt"), "w") as fh:
        fh.write("old lane\\n")
    ogit("add", "lane-old.txt")
    ogit("-c", "user.name=autoos-worker",
         "-c", "user.email=autoos-worker@users.noreply.github.com",
         "commit", "-q", "-m", "a finished lane's commit")
    git("fetch", "-q", other, "lane-old:refs/heads/lane-old")
    git("merge", "-q", "--ff-only", "lane-old")
    git("branch", "-q", "-D", "lane-old")
    _sh.rmtree(par, True)
    print("fake: parent branch fast-forwarded onto a pre-run lane commit")
elif mode == "commit-in-another-clone":
    # LEAKFP B (MUSEAPI2.out tail): another writer's lane in the SAME
    # repository advanced an existing branch. Every worktree and clone of one
    # .git shares refs, and this worktree's HEAD never visited that branch, so
    # "checked out in another worktree" cannot excuse the moved ref.
    import shutil as _sh
    import tempfile as _tf
    par = _tf.mkdtemp(prefix="autoos-fake-sib-")
    other = os.path.join(par, "lane")
    def ogit(*a):
        subprocess.run(["git", "-C", other, *a], check=True,
                       capture_output=True, text=True)
    git("clone", "-q", "--local", root, other)
    ogit("switch", "-q", "side")
    with open(os.path.join(other, "asm.txt"), "w") as fh:
        fh.write("asm\\n")
    ogit("add", "asm.txt")
    ogit("-c", "user.name=autoos-worker",
         "-c", "user.email=autoos-worker@users.noreply.github.com",
         "commit", "-q", "-m", "another lane's worker change")
    git("fetch", "-q", other, "side:side")
    _sh.rmtree(par, True)
    print("fake: a shared-repo branch moved under another writer")
elif mode == "commit-worker-with-lane-present":
    # The boundary the two fixes above must not cross: a real leak (the worker
    # commits into the parent checkout under the worker identity) while an
    # unrelated lane ref also exists in the repository.
    git("branch", "lane-present")
    with open(os.path.join(root, "worker-file.txt"), "w") as fh:
        fh.write("worker\\n")
    git("add", "worker-file.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "worker change")
    print("fake: committed as worker with a lane ref present")
elif mode == "decoy-branch-after-commit":
    # LEAKFP2 evasion 1 (review of 75f2866): the worker commits in the parent
    # and then puts a NEW branch on its own tip. 75f2866's `_lane_work` read
    # "contained in a ref that did not exist at the snapshot" as another
    # writer's lane work and exempted the leak.
    with open(os.path.join(root, "worker-file.txt"), "w") as fh:
        fh.write("worker\\n")
    git("add", "worker-file.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "worker change")
    git("branch", "decoy")
    print("fake: worker commit in the parent, then a decoy branch at the tip")
elif mode == "backdated-worker-commit":
    # LEAKFP2 evasion 2: the same commit with GIT_COMMITTER_DATE (and the
    # author date) backdated before the run. 75f2866's run window dropped it.
    env = dict(os.environ)
    env["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00Z"
    env["GIT_COMMITTER_DATE"] = "2020-01-01T00:00:00Z"
    with open(os.path.join(root, "worker-file.txt"), "w") as fh:
        fh.write("worker\\n")
    git("add", "worker-file.txt")
    subprocess.run(["git", "-C", root, "-c", "user.name=autoos-worker",
                    "-c", "user.email=autoos-worker@users.noreply.github.com",
                    "commit", "-q", "-m", "backdated worker change"],
                   check=True, capture_output=True, text=True, env=env)
    print("fake: worker commit with a backdated committer timestamp")
elif mode == "switch-c-ff-back":
    # LEAKFP2 evasion 3: commit on a branch created during the run, then
    # fast-forward the parent's own branch onto it. 75f2866's made_on leg
    # exempted the commit as "lane work on a new branch".
    back = subprocess.run(["git", "-C", root, "branch", "--show-current"],
                          check=True, capture_output=True, text=True).stdout.strip()
    git("switch", "-q", "-c", "tmp-leak")
    with open(os.path.join(root, "worker-file.txt"), "w") as fh:
        fh.write("worker\\n")
    git("add", "worker-file.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "worker change")
    git("switch", "-q", back)
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "merge", "-q", "--ff-only", "tmp-leak")
    print("fake: worker commit through a branch created during the run, ff'd back")
elif mode == "commit-on-branch-created-in-run":
    # A branch created during the run is never exempt: the worker switches to
    # its own new branch IN THE PARENT CHECKOUT and commits there.
    git("switch", "-q", "-c", "lane-mine")
    with open(os.path.join(root, "worker-file.txt"), "w") as fh:
        fh.write("worker\\n")
    git("add", "worker-file.txt")
    git("-c", "user.name=autoos-worker",
        "-c", "user.email=autoos-worker@users.noreply.github.com",
        "commit", "-q", "-m", "worker change")
    print("fake: worker commit on a branch created during the run")
elif mode == "untracked-in-parent":
    # A worker with full write rights drops a NEW untracked file into the parent
    # checkout (review of 6622d29: git status ran with --untracked-files=no).
    with open(os.path.join(root, "stray.txt"), "w") as fh:
        fh.write("leak\\n")
    print("fake: wrote an untracked file into the parent")
elif mode == "orchestrator-commits-wip":
    # The orchestrator commits its own pre-existing WIP mid-run: the path was
    # dirty before, is clean after - the orchestrator's own cleanup, not a
    # worker leak.
    with open(os.path.join(root, "tracked.txt"), "a") as fh:
        fh.write("wip\\n")
    git("add", "tracked.txt")
    git("-c", "user.name=orch", "-c", "user.email=orch@example.invalid",
        "commit", "-q", "-m", "orch wip")
    print("fake: orchestrator committed its own WIP")
elif mode in ("sandbox-write", "sandbox-write-provider-stop"):
    # The worker edits its own cwd (the --isolate sandbox), then the provider
    # stops it before it can commit (WIPfix, 2026-09-26). It also touches the
    # two paths that must NEVER land in a WIP commit (review WIPfix4): a
    # git-ignored secrets file and the spawner's own logs/ directory.
    os.makedirs(os.path.join(os.getcwd(), "configuration"), exist_ok=True)
    with open(os.path.join(os.getcwd(), "configuration", "api-keys.yml"), "w") as fh:
        fh.write("omniroute: fake-not-a-secret\\n")
    os.makedirs(os.path.join(os.getcwd(), "logs"), exist_ok=True)
    with open(os.path.join(os.getcwd(), "logs", "x"), "w") as fh:
        fh.write("spawner state\\n")
    with open(os.path.join(os.getcwd(), "worker-new.txt"), "w") as fh:
        fh.write("work\\n")
    if mode.endswith("provider-stop"):
        print("Error: Rate limit exceeded. Please try again later.")
    else:
        print("fake: wrote worker-new.txt in its sandbox")
elif mode == "sandbox-write-conflicted":
    # The worker leaves a conflicted cherry-pick behind (WIPfix4 review): an
    # unmerged index can make the WIP `git commit` refuse outright, and that
    # failure must be printed, not silent.
    cwd = os.getcwd()
    def sgit(*a):
        subprocess.run(["git", "-C", cwd, *a], check=True,
                       capture_output=True, text=True)
    with open(os.path.join(cwd, "worker-new.txt"), "w") as fh:
        fh.write("work\\n")
    with open(os.path.join(cwd, "conflict.txt"), "w") as fh:
        fh.write("worker\\n")
    sgit("-c", "user.name=autoos-worker",
         "-c", "user.email=autoos-worker@users.noreply.github.com",
         "commit", "-q", "-am", "worker change")
    r = subprocess.run(["git", "-C", cwd, "-c", "user.name=autoos-worker",
                        "-c", "user.email=autoos-worker@users.noreply.github.com",
                        "cherry-pick", "--no-commit", "master"],
                       capture_output=True, text=True)
    assert r.returncode != 0, r.stdout + r.stderr
    # Make the unmerged path unreadable: newer gits auto-resolve a conflicted
    # path on `commit -a`, but no version can index a file it cannot open, so
    # the WIP commit refuses on every git.
    os.chmod(os.path.join(cwd, "conflict.txt"), 0)
    print("fake: left a conflicted cherry-pick in the sandbox")
elif mode == "sandbox-write-detached":
    # The worker detached HEAD in its sandbox (WIPfix4 review): the WIP commit
    # lands on no branch, so `take it: git fetch <path> <branch>` cannot fetch
    # it unless the branch is repointed at it.
    with open(os.path.join(os.getcwd(), "worker-new.txt"), "w") as fh:
        fh.write("work\\n")
    subprocess.run(["git", "-C", os.getcwd(), "checkout", "-q", "--detach", "HEAD"],
                   check=True)
    print("fake: detached HEAD in its sandbox")
elif mode == "refusal-then-provider-stop":
    # WIPfix4 review: agy auto-denied a tool (jetski refusal) AND the provider
    # stopped the run (AGY_ERROR 429) - both measured, R-gateway-12. Exit
    # precedence is 7 > 8 > 5 > 6, so the run must exit 8, failure_class
    # "provider", not 6 "logic".
    print('jetski: no output produced - a tool required the "command" '
          'permission that headless mode cannot prompt for, so it was auto-denied')
    print('AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached"')
    with open(os.path.join(os.getcwd(), "worker-new.txt"), "w") as fh:
        fh.write("work\\n")
elif mode == "sandbox-write-marker-mid-run":
    # WIPfix2: the marker appears EARLY (a brief or lesson quoting a past
    # 429), then the worker prints 20 normal lines, edits its sandbox and
    # exits 0. A real provider stop is the client's LAST output only, so this
    # must stay rc 0 with the work WIP-committed.
    print("Error: Rate limit exceeded. Please try again later.")
    for _i in range(20):
        print("normal output line %d" % _i)
    with open(os.path.join(os.getcwd(), "worker-new.txt"), "w") as fh:
        fh.write("work\\n")
elif mode == "provider-stop-only":
    print("Error: Rate limit exceeded. Please try again later.")
elif mode == "agy-quota-rc3":
    # AGYFIX item 3 (K3 audit addendum 08:1xZ, measured 2026-09-27): agy
    # without --model ran its default Gemini, printed exactly this line and
    # exited 3 after ~157 s. It is a provider stop, so the spawner must
    # report PROVIDER-STOP (exit 8), not the client's own rc 3.
    print('AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached')
    sys.exit(3)
elif mode == "parent-leak-provider-stop":
    # A leak AND a provider stop: LEAK 7 must win over PROVIDER-STOP 8.
    with open(os.path.join(root, "tracked.txt"), "a") as fh:
        fh.write("leaked\\n")
    print("Error: Rate limit exceeded. Please try again later.")
sys.exit(0)
'''


_FAKE_JOINABLE_CLAUDE_SRC = '''
# A joinable (claude --bg --remote-control) session: the launcher exits 0
# while the session it started lives on. Its last visible lines can even be
# the background session's mid-flight output (a partial tail with a provider
# marker) - the spawner must not score it.
import os
with open(os.path.join(os.getcwd(), "worker-new.txt"), "w") as fh:
    fh.write("work\\n")
print("session started in the background: d1")
print("Error: Rate limit exceeded. Please try again later.")
'''


def _init_git_root():
    """A temp git checkout with two commits and an extra `side` branch: the
    fixture the --isolate containment tests clone from, shared with the
    provider-stop fallthrough tests. The caller owns the tempdir
    (addCleanup(shutil.rmtree, ...))."""
    tmp = tempfile.mkdtemp()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-c", "init.defaultBranch=master"]
    subprocess.run(git + ["init", "-q", tmp], check=True)
    with open(os.path.join(tmp, "tracked.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    subprocess.run(git + ["-C", tmp, "add", "tracked.txt"], check=True)
    subprocess.run(git + ["-C", tmp, "commit", "-q", "-m", "init"], check=True)
    with open(os.path.join(tmp, "conflict.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    subprocess.run(git + ["-C", tmp, "add", "conflict.txt"], check=True)
    subprocess.run(git + ["-C", tmp, "commit", "-q", "-m", "conflict base"], check=True)
    # A second EXISTING branch (not checked out) for the side-ref cases;
    # refs created mid-run are new and never scanned.
    subprocess.run(git + ["-C", tmp, "branch", "side"], check=True)
    return tmp


class IsolateContainmentTests(unittest.TestCase):
    """ISOfix (rule->code: --isolate containment leak). Measured 2026-09-26:
    an --isolate worker given absolute parent paths edited and committed in
    the parent checkout; outside_fence only fenced opencode's file tools and
    sandbox_verdict only diffed the sandbox, so the spawner reported NO-OP
    (exit 5) instead of a leak."""

    WORKER_EMAIL = "autoos-worker@users.noreply.github.com"

    def setUp(self):
        self.agent = load_agent()

    def make_root(self):
        tmp = _init_git_root()
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp

    def make_fake_agy(self):
        if os.name == "nt":
            self.skipTest("shell scripts and os.chmod; POSIX only")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        py = os.path.join(d, "fake_client.py")
        with open(py, "w", encoding="utf-8") as fh:
            fh.write(_FAKE_ISOLATE_AGY_SRC)
        sh = os.path.join(d, "agy")
        with open(sh, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\nif [ "$1" = "models" ]; then echo fake; exit 0; fi\n'
                     'exec python3 "%s" "$@"\n' % py)
        os.chmod(sh, 0o755)
        return d

    def make_state(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def make_scratch(self):
        """A working directory for the run itself. cmd_run may start the client
        before a sandbox exists (a `--version`/`--help` mode probe), and such a
        probe inherits this process's cwd — without it an argv-naive fake writes
        its "sandbox" files into the checkout running the suite."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def run_isolated(self, root, stubdir, statedir, mode, card=None):
        agent = self.agent
        old_root, old_track = agent.ROOT, agent.TRACK_RECORD
        agent.ROOT, agent.TRACK_RECORD = root, os.path.join(statedir, "track-record.jsonl")
        old_cwd = os.getcwd()
        os.chdir(self.make_scratch())
        try:
            args = argparse.Namespace(
                client="agy", tier=None if card else 2, card=card, task="do the thing",
                free=False, free_model=agent.DEFAULT_FREE_MODEL,
                isolate=True, auto=True, joinable=False, model=None,
                clean=False, allow_training=False, max_depth=None, lean=False,
                title=None, dry_run=False, no_defer=False)
            cfg = {"agents": {"t2-worker": {"model": "omniroute/t2-worker"}},
                   "providers": {"omniroute": {"models": {"t2-worker": {},
                                                          "t3-driver": {}}}}}
            env = dict(os.environ)
            env["PATH"] = stubdir + os.pathsep + env.get("PATH", "")
            env["AUTOOS_STATE_DIR"] = statedir
            env["AUTOOS_FAKE_ROOT"] = root
            env["AUTOOS_FAKE_MODE"] = mode
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True):
                # SPAWNCAP (S2): the real registry declares headless agy with
                # shell=false/write=false (its headless refusal evidence), but
                # these tests exercise containment/leak/provider-stop with a fake
                # worker, not the capability gate. Neutralise the gate here so
                # the containment behaviour is still what is measured.
                with mock.patch.object(agent, "client_capabilities",
                                       lambda name, registry=None: {"shell": True, "write": True}):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        rc = agent.cmd_run(args, cfg)
        finally:
            os.chdir(old_cwd)  # before the cleanup removes the scratch dir
            agent.ROOT, agent.TRACK_RECORD = old_root, old_track
        return rc, out.getvalue(), err.getvalue()

    def lone_sandbox(self, statedir):
        base = os.path.join(statedir, "sandboxes")
        names = os.listdir(base)
        self.assertEqual(len(names), 1, names)
        return os.path.join(base, names[0])

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_worker_commit_in_the_parent_is_a_leak_exit_7_naming_the_sha(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "commit-worker")
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn(sha, out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_modified_tracked_file_in_the_parent_is_a_leak(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "modify")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn("tracked.txt", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_commit_by_another_author_is_not_a_leak(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "commit-other")
        self.assertNotIn("LEAK", out + err)
        self.assertEqual(rc, 5, out + err)  # the NO-OP verdict still applies

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_merged_worker_lane_is_not_a_leak(self):
        # Measured 2026-09-26 19:13Z: the orchestrator merged lane A5f
        # (autoos-worker commits) into the parent while ISOfix ran.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "merge-worker-lane")
        self.assertNotIn("LEAK", out + err)
        self.assertEqual(rc, 5, out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_an_amend_with_a_worker_committer_is_a_leak(self):
        # Review 2026-09-26: --amend keeps the original author, so a %ae-only
        # scan misses a worker amending the parent tip; the committer is the
        # worker's.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "amend-worker")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        # log_run records the FINAL rc (after the LEAK override), so
        # orch-*.log and the track record cannot disagree (review 2026-09-26).
        logdir = os.path.join(root, "logs")
        lines = []
        for name in os.listdir(logdir):
            if name.startswith("orch-"):
                with open(os.path.join(logdir, name), encoding="utf-8") as fh:
                    lines.extend(fh.read().splitlines())
        self.assertTrue(any(" rc=7 " in ln for ln in lines), lines)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_commit_then_reset_on_the_checked_out_branch_is_a_leak(self):
        # after_head == before_head and the porcelain is clean; only the
        # branch reflog shows the worker's commit (review 2026-09-26).
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "commit-reset")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_worker_commit_on_another_existing_branch_is_a_leak(self):
        # HEAD and the checked-out tree never move; the moved side ref does.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "commit-other-branch")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn("refs/heads/side", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_sibling_worktree_lane_committing_on_its_own_branch_is_not_a_leak(self):
        # All lanes are worktrees of one .git: a parallel lane's worker commit
        # on the branch checked out in ITS worktree moves an existing ref but
        # is not a leak (final Sonnet check of 073b8ae, 2026-09-26). The
        # side-branch leak test above stays: `side` is checked out nowhere.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        sib = tempfile.mkdtemp()
        shutil.rmtree(sib)
        self.addCleanup(shutil.rmtree, sib, True)
        subprocess.run(["git", "-C", root, "worktree", "add", "-q", "-b", "lane-sib", sib],
                       check=True)
        rc, out, err = self.run_isolated(root, stub, state, "commit-sibling-worktree")
        self.assertNotIn("LEAK", out + err)
        self.assertEqual(rc, 5, out + err)  # the NO-OP verdict still applies

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_parent_fast_forward_onto_another_lanes_commits_is_a_leak_with_the_hint(self):
        # LEAKFP A (work/L1-routing/review-fold2e.out, exit 7) is NOT exempted
        # in code (LEAKFP2 decision): nothing but "do not move a parent while
        # its child runs" (skill R-coord-01) makes this case identifiable, so a
        # moved parent stays a containment failure and the message tells the
        # orchestrator that moving the branch itself is what it just did.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "parent-ff-onto-lane")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn("worker commits in the parent checkout", out + err)
        self.assertIn("if you moved this branch yourself during the run (merge/ff)",
                      out + err)
        self.assertIn("R-coord-01", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_prerun_lane_commit_fast_forwarded_in_is_a_leak_too(self):
        # LEAKFP2: the run window is gone with the exemptions - a pre-run lane
        # commit the orchestrator fast-forwards in cannot be told apart from a
        # worker's write, so the run is blamed (conservative by design; the
        # frozen-parent rule is procedural, not code).
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "ff-old-lane-commit")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_shared_repo_branch_moving_without_a_sibling_worktree_is_a_leak(self):
        # LEAKFP B's exemption is narrow: ONLY a branch checked out in another
        # worktree at both ends of the run is excused (the test above). A ref an
        # outside clone fetched into the shared repository is checked out
        # nowhere, so the strict check keeps flagging it - 75f2866 read
        # MUSEAPI2.out too broadly and lost the side-branch leak here.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "commit-in-another-clone")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn("refs/heads/side", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_decoy_branch_at_the_tip_does_not_exempt_a_parent_commit(self):
        # Evasion 1 of 75f2866: `git branch decoy` after the worker commit made
        # the leak read as another lane's work.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "decoy-branch-after-commit")
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn(sha, out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_backdated_worker_commit_in_the_parent_is_a_leak(self):
        # Evasion 2: GIT_COMMITTER_DATE in the past fell outside 75f2866's run
        # window. The strict check reads no clock at all.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "backdated-worker-commit")
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn(sha, out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_worker_commit_via_switch_c_and_a_fast_forward_back_is_a_leak(self):
        # Evasion 3: commit on a branch created during the run, ff the parent's
        # branch onto it, delete the branch - 75f2866's made_on-leg exempted it.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "switch-c-ff-back")
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn(sha, out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_worker_commit_on_a_branch_created_during_the_run_is_a_leak(self):
        # The parent checkout moved, and the branch it moved on is brand new:
        # newness is not an excuse for a commit this worktree made.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(
            root, stub, state, "commit-on-branch-created-in-run")
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn(sha, out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_worker_commit_in_the_parent_is_still_a_leak_with_a_lane_present(self):
        # The boundary of both exemptions: a lane ref in the repository must not
        # excuse a worker commit made on the parent's own branch.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state,
                                         "commit-worker-with-lane-present")
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)
        self.assertIn(sha, out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_new_untracked_file_in_the_parent_is_a_leak(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "untracked-in-parent")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("stray.txt", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_lane_fetched_into_a_new_ref_is_not_a_leak(self):
        # The orchestrator fetches worker lanes into NEW refs during a run;
        # a ref that did not exist at the snapshot is never scanned.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "fetch-lane-new-ref")
        self.assertNotIn("LEAK", out + err)
        self.assertEqual(rc, 5, out + err)
        refs = subprocess.run(["git", "-C", root, "for-each-ref", "refs/heads",
                               "--format=%(refname)"], capture_output=True, text=True,
                              check=True).stdout.split()
        self.assertIn("refs/heads/lane2", refs)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_the_orchestrator_committing_its_own_wip_is_not_a_leak(self):
        # Pre-dirty a tracked file; the orchestrator committing it mid-run
        # leaves the path cleaner than before - only new dirt counts.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        with open(os.path.join(root, "tracked.txt"), "a", encoding="utf-8") as fh:
            fh.write("wip\n")
        rc, out, err = self.run_isolated(root, stub, state, "orchestrator-commits-wip")
        self.assertNotIn("LEAK", out + err)
        self.assertEqual(rc, 5, out + err)  # the NO-OP verdict still applies

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_push_from_the_sandbox_to_the_parent_fails(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        self.run_isolated(root, stub, state, "noop")
        sb = self.lone_sandbox(state)
        push_url = subprocess.run(["git", "-C", sb, "remote", "get-url", "--push", "origin"],
                                  capture_output=True, text=True).stdout.strip()
        self.assertIn("DISABLED-autoos-isolate", push_url)
        branch = subprocess.run(["git", "-C", sb, "branch", "--show-current"],
                                capture_output=True, text=True).stdout.strip()
        push = subprocess.run(["git", "-C", sb, "push", "origin", branch],
                              capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0, push.stdout + push.stderr)

    def test_the_isolate_prompt_line_names_sandbox_and_root(self):
        agent = self.agent
        cfg = agent.load_jsonc(str(ROOT / "opencode.jsonc"))
        state = self.make_state()

        def args(isolate, client="opencode"):
            return argparse.Namespace(
                client=client, tier=2, card=None, task="do the thing",
                free=False, free_model=agent.DEFAULT_FREE_MODEL,
                isolate=isolate, auto=True, joinable=False, model=None,
                clean=False, allow_training=False, max_depth=None, lean=False, title=None)
        with mock.patch.dict(os.environ, {"AUTOOS_STATE_DIR": state}):
            plan = agent.build_plan(args(True), cfg)
        line = ("Your working directory %s is your only writable checkout; "
                "never cd, git -C or write into %s or any other path outside it."
                % (plan["sandbox"]["path"], agent.ROOT))
        self.assertEqual(plan["cmd"][-1], line + "\n" + "do the thing")
        with mock.patch.dict(os.environ, {"AUTOOS_STATE_DIR": state}):
            plain = agent.build_plan(args(False), cfg)
        self.assertEqual(plain["cmd"][-1], "do the thing")
        self.assertNotIn("only writable checkout", plain["cmd"][-1])

    def test_every_headless_client_keeps_the_prefix_last_under_isolate(self):
        # Review 2026-09-26: the prefix rewrite assumes every build_command
        # leaves the task last; a future client appending a flag after the
        # prompt would silently drop it.
        agent = self.agent
        state = self.make_state()
        cfg = {"agents": {"t2-worker": {"model": "omniroute/t2-worker"}},
               "providers": {"omniroute": {"models": {"t2-worker": {}}}}}
        for name, c in clients.CLIENTS.items():
            if not c.headless:
                continue
            with self.subTest(client=name):
                args = argparse.Namespace(
                    client=name, tier=2, card=None, task="do the thing",
                    free=False, free_model=agent.DEFAULT_FREE_MODEL,
                    isolate=True, auto=True, joinable=False, model=None,
                    clean=False, allow_training=False, max_depth=None,
                    lean=False, title=None)
                with mock.patch.dict(os.environ, {"AUTOOS_STATE_DIR": state}):
                    plan = agent.build_plan(args, cfg)
                self.assertTrue(plan["cmd"][-1].startswith("Your working directory "),
                                "%s: cmd[-1] = %r" % (name, plan["cmd"][-1]))

    def test_filtered_parent_status_keeps_renames_with_one_side_outside_logs(self):
        # Review 2026-09-26: `any(logs/)` dropped `R  catalog/x -> logs/x`
        # (a tracked file leaving the tree); only all-logs/ entries drop, and
        # the entry keys by the non-logs side.
        agent = self.agent
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=(
                'R  catalog/x -> logs/x\n'
                'R  logs/y -> catalog/y\n'
                'R  logs/a -> logs/b\n'
                'M  catalog/z\n'
                'R  "logs/space name" -> "catalog/space name"\n'
            ), stderr="")
            with mock.patch.object(agent.subprocess, "run", return_value=proc):
                out = agent._filtered_parent_status(tmp)
        self.assertEqual(out, {
            "catalog/x -> logs/x": "R ",
            "logs/y -> catalog/y": "R ",
            "catalog/z": "M ",
            '"logs/space name" -> "catalog/space name"': "R ",
        })

    def test_track_entry_maps_exit_7_to_containment(self):
        plan = {"client": "opencode", "free": False,
                "route": {"combo": "t2-worker", "card": {}}}
        entry = self.agent.track_entry(plan, 7, 1.0)
        self.assertEqual(entry["failure_class"], "containment")
        self.assertEqual(entry["gate"], "fail")

    # WIPfix (rule->code: never lose a worker's uncommitted sandbox work).
    # Measured 2026-09-26 19:1x-19:3xZ: three --isolate workers were stopped by
    # the provider right before `git commit`; cmd_run printed 'sandbox changes
    # (uncommitted)' and exited 0, and the orchestrator WIP-committed by hand.

    def _subject(self, sandbox):
        return subprocess.run(["git", "-C", sandbox, "log", "-1", "--format=%s"],
                              capture_output=True, text=True, check=True).stdout.strip()

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_provider_stop_with_sandbox_changes_wip_commits_and_exits_8(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "sandbox-write-provider-stop")
        self.assertEqual(rc, 8, out + err)
        self.assertIn("PROVIDER-STOP", out + err)
        self.assertIn("WIP-COMMITTED:", out)
        sb = self.lone_sandbox(state)
        subject = self._subject(sb)
        self.assertTrue(subject.startswith("WIP(autoos-agent): uncommitted at exit rc=0"),
                        subject)
        self.assertIn("provider stop: Error: Rate limit exceeded", subject)
        files = subprocess.run(["git", "-C", sb, "show", "--name-only", "--format=", "HEAD"],
                               capture_output=True, text=True, check=True).stdout
        self.assertIn("worker-new.txt", files)
        branch = subprocess.run(["git", "-C", sb, "branch", "--show-current"],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.assertTrue(branch.startswith("agent/"), branch)

    # WIPfix2 (rule->code: a provider stop is the client's LAST output only).
    # A worker that merely READS or prints text containing a marker - a brief
    # quoting a past 429, a lesson - then finishes normally must not exit 8.

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_marker_quoted_mid_run_is_not_a_provider_stop(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state,
                                         "sandbox-write-marker-mid-run")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("PROVIDER-STOP", out + err)
        self.assertIn("WIP-COMMITTED:", out)
        sb = self.lone_sandbox(state)
        subject = self._subject(sb)
        self.assertTrue(subject.startswith("WIP(autoos-agent): uncommitted at exit rc=0"),
                        subject)
        self.assertNotIn("provider stop:", subject)
        files = subprocess.run(["git", "-C", sb, "show", "--name-only", "--format=", "HEAD"],
                               capture_output=True, text=True, check=True).stdout
        self.assertIn("worker-new.txt", files)

    def test_provider_stop_ignores_a_marker_outside_the_window(self):
        # The only marker is 9+ lines from the end: outside PROVIDER_STOP_WINDOW,
        # so provider_stop returns None (review WIPfix2).
        agent = self.agent
        tail = "Error: Rate limit exceeded. Please try again later.\n"
        tail += "\n".join("normal line %d" % i for i in range(9))
        self.assertIsNone(agent.provider_stop(tail))

    def test_provider_stop_returns_the_match_nearest_the_end(self):
        # Two markers inside the window: the one nearest the end is returned.
        agent = self.agent
        tail = ("Error: Rate limit exceeded.\n"
                "normal line\n"
                "Error: 429 Too Many Requests\n")
        self.assertEqual(agent.provider_stop(tail), "Error: 429 Too Many Requests")

    def test_provider_stop_still_matches_at_the_end(self):
        agent = self.agent
        self.assertEqual(
            agent.provider_stop("working\nError: Rate limit exceeded.\n"),
            "Error: Rate limit exceeded.")

    # WIPfix3 (measured 2026-09-26, work/L1-routing/WIPfix2.out): a run that
    # finished normally was reported PROVIDER-STOP because its last lines had
    # the spawner's own printed `CODE` line quoting
    # 'print("Error: Rate limit exceeded. Please try again later.")'. A real
    # provider stop is an error-prefixed LINE, so the line must both carry a
    # marker AND start (after lstrip + ANSI-strip) with an error prefix.

    def test_provider_stop_matches_the_measured_client_stop_lines(self):
        # The four shapes real clients print when the provider stops them
        # (brief WIPfix3): opencode rate limit and capacity, agy AGY_ERROR
        # 429 JSON, agy quota line. Each must match.
        # FUP (2026-09-27): qoder CLI credits-exhausted marker added.
        agent = self.agent
        cases = [
            "Error: Rate limit exceeded. Please try again later.",
            "Error: Chat admission capacity is temporarily unavailable. Retry shortly.",
            'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached',
            "error: Individual quota reached. Please upgrade your plan.",
            "error: your personal credits have been exhausted",
        ]
        for line in cases:
            with self.subTest(line=line):
                self.assertEqual(agent.provider_stop("working\n" + line + "\n"),
                                 line)

    def test_provider_stop_matches_no_active_credentials(self):
        # TOOLFIX item 3 (measured 2026-09-27): an opencode run that printed
        # "Error: No active credentials for provider: sambanova." as its error
        # line then exited 1 instead of 8 -- it is a provider stop.
        agent = self.agent
        line = "Error: No active credentials for provider: sambanova."
        self.assertEqual(agent.provider_stop("working\n" + line + "\n"), line)

    def test_provider_stop_ignores_a_marker_in_code_or_prose(self):
        # WIPfix3: the WIPfix2 false positive and its neighbours - a marker
        # quoted in a code line, a list entry, or a grep command must NOT
        # count as a provider stop.
        agent = self.agent
        cases = [
            'CODE: print("Error: Rate limit exceeded.")',
            '+    "rate limit exceeded",',
            'grep -n " 429" x',
        ]
        for line in cases:
            with self.subTest(line=line):
                self.assertIsNone(agent.provider_stop("working\n" + line + "\n"))

    def test_provider_stop_matches_through_ansi_colour(self):
        # Clients colourise stderr: the prefix check must see past the ANSI
        # colour/bold codes wrapping the prefix (the returned line is the
        # cleaned text, so the WIP subject stays readable).
        agent = self.agent
        line = "\x1b[91m\x1b[1mError: \x1b[0mRate limit exceeded"
        self.assertEqual(agent.provider_stop("working\n" + line + "\n"),
                         "Error: Rate limit exceeded")

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_sandbox_changes_without_a_stop_wip_commit_and_keep_rc_0(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "sandbox-write")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("WIP-COMMITTED:", out)
        self.assertNotIn("provider stop:", self._subject(self.lone_sandbox(state)))
        self.assertNotIn("PROVIDER-STOP", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_provider_stop_without_changes_exits_8_not_5(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "provider-stop-only")
        self.assertEqual(rc, 8, out + err)
        self.assertIn("PROVIDER-STOP", out + err)
        # Nothing changed, so nothing was committed: HEAD is still the clone's
        # initial commit and no WIP line was printed.
        self.assertNotIn("WIP-COMMITTED", out)
        self.assertNotIn("WIP(autoos-agent)", self._subject(self.lone_sandbox(state)))

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_an_agy_quota_stop_that_exits_3_is_still_a_provider_stop(self):
        # AGYFIX item 3 (K3 audit addendum 08:1xZ): agy without --model exits
        # rc 3 with "AGY_ERROR ... RESOURCE_EXHAUSTED ... quota reached" as
        # its last line. A provider stop outranks the client's own code, so
        # the run must exit 8, not 3 - unattended recovery keys on the 8.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "agy-quota-rc3")
        self.assertEqual(rc, 8, out + err)
        self.assertIn("PROVIDER-STOP", err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_parent_leak_wins_over_a_provider_stop(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "parent-leak-provider-stop")
        self.assertEqual(rc, 7, out + err)
        self.assertIn("LEAK", out + err)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_review_run_is_not_wip_committed(self):
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "sandbox-write",
                                         card="role=review")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("WIP-COMMITTED", out)
        sb = self.lone_sandbox(state)
        status = subprocess.run(["git", "-C", sb, "status", "--short"],
                                capture_output=True, text=True, check=True).stdout
        self.assertIn("worker-new.txt", status)
        self.assertNotIn("WIP(autoos-agent)", self._subject(sb))

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_refusal_then_a_provider_stop_exits_8_with_class_provider(self):
        # WIPfix4 review (agy: jetski refusal + `AGY_ERROR ... 429`, both
        # measured, R-gateway-12): the exit precedence is 7 > 8 > 5 > 6, so a
        # HEADLESS-REFUSAL run that is then provider-stopped exits 8 -
        # unattended recovery mis-keys on a 6. (agy is an own-account client,
        # so no track entry is written; the failure_class that WOULD be
        # recorded for an 8 is pinned below through track_entry itself.)
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "refusal-then-provider-stop")
        self.assertEqual(rc, 8, out + err)
        self.assertIn("HEADLESS-REFUSAL", err)
        self.assertIn("PROVIDER-STOP", err)
        entry = self.agent.track_entry(
            {"client": "opencode", "route": {"combo": "t2-worker-free",
                                             "card": {}}}, rc, 1.0)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["failure_class"], "provider")

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_conflicted_sandbox_index_names_the_wip_commit_failure(self):
        # WIPfix4 review: `commit -am` refuses on an unmerged index; the
        # refusal must be printed (one stderr line), not silent.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "sandbox-write-conflicted")
        self.assertNotIn("WIP-COMMITTED", out)
        self.assertIn("WIP-COMMIT FAILED: ", err)
        line = [ln for ln in err.splitlines() if ln.startswith("WIP-COMMIT FAILED: ")][0]
        self.assertTrue(line[len("WIP-COMMIT FAILED: "):].strip(), line)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_detached_sandbox_head_still_yields_a_fetchable_wip_commit(self):
        # WIPfix4 review: a WIP commit on a detached HEAD lands on no branch,
        # so the printed `take it: git fetch <path> <branch>` cannot fetch it.
        # The sandbox branch must be pointed at the WIP commit.
        root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
        rc, out, err = self.run_isolated(root, stub, state, "sandbox-write-detached")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("WIP-COMMITTED:", out)
        sb = self.lone_sandbox(state)
        self.assertTrue(self._subject(sb).startswith("WIP(autoos-agent): uncommitted at exit rc=0"))
        # The sandbox branch exists and points at the WIP commit even though
        # HEAD is detached (the worker left it that way).
        self.assertEqual(subprocess.run(
            ["git", "-C", sb, "branch", "--show-current"],
            capture_output=True, text=True, check=True).stdout.strip(), "")
        take_it = [ln for ln in out.splitlines()
                   if ln.startswith("take it: git fetch ")][0]
        # 'take it: git fetch <path> <branch>   (then review FETCH_HEAD)'
        branch = take_it.split("   ")[0].split()[-1]
        self.assertTrue(branch.startswith("agent/"), take_it)
        self.assertEqual(subprocess.run(
            ["git", "-C", sb, "rev-parse", "refs/heads/" + branch],
            capture_output=True, text=True, check=True).stdout.strip(),
            subprocess.run(["git", "-C", sb, "rev-parse", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.strip())
        # And the branch really fetches.
        fetch = subprocess.run(["git", "-C", root, "fetch", "-q", sb, branch],
                               capture_output=True, text=True)
        self.assertEqual(fetch.returncode, 0, fetch.stdout + fetch.stderr)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_a_joinable_isolate_run_is_left_untouched(self):
        # WIPfix4 review: a --joinable run (claude --bg) exits 0 while the
        # session still runs - no provider-stop upgrade on a partial tail and
        # no mid-flight WIP commit. The sandbox stays for the caller.
        root, stub, state = self.make_root(), self.make_fake_claude(), self.make_state()
        rc, out, err = self.run_isolated_joinable(root, stub, state)
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("PROVIDER-STOP", out + err)
        self.assertNotIn("WIP-COMMITTED", out)
        self.assertNotIn("take it: git fetch", out)
        self.assertNotIn("discard: rm -rf", out)
        sb = self.lone_sandbox(state)
        status = subprocess.run(["git", "-C", sb, "status", "--short"],
                                capture_output=True, text=True, check=True).stdout
        self.assertIn("worker-new.txt", status)
        self.assertNotIn("WIP(autoos-agent)", self._subject(sb))

    def make_fake_claude(self):
        if os.name == "nt":
            self.skipTest("shell scripts and os.chmod; POSIX only")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        py = os.path.join(d, "fake_claude.py")
        with open(py, "w", encoding="utf-8") as fh:
            fh.write(_FAKE_JOINABLE_CLAUDE_SRC)
        sh = os.path.join(d, "claude")
        with open(sh, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\nexec python3 "%s" "$@"\n' % py)
        os.chmod(sh, 0o755)
        return d

    def run_isolated_joinable(self, root, stubdir, statedir):
        agent = self.agent
        old_root, old_track = agent.ROOT, agent.TRACK_RECORD
        agent.ROOT, agent.TRACK_RECORD = root, os.path.join(statedir, "track-record.jsonl")
        old_cwd = os.getcwd()
        os.chdir(self.make_scratch())
        try:
            args = argparse.Namespace(
                client="claude", tier=2, card=None, task="do the thing",
                free=False, free_model=agent.DEFAULT_FREE_MODEL,
                isolate=True, auto=True, joinable=True, model=None,
                clean=False, allow_training=False, max_depth=None, lean=False,
                title=None, dry_run=False, no_defer=False)
            cfg = {"agents": {"t2-worker": {"model": "claude/t2-worker"}},
                   "providers": {"claude": {"models": {"t2-worker": {}}}}}
            env = dict(os.environ)
            env["PATH"] = stubdir + os.pathsep + env.get("PATH", "")
            env["AUTOOS_STATE_DIR"] = statedir
            env["AUTOOS_FAKE_ROOT"] = root
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = agent.cmd_run(args, cfg)
        finally:
            os.chdir(old_cwd)  # before the cleanup removes the scratch dir
            agent.ROOT, agent.TRACK_RECORD = old_root, old_track
        return rc, out.getvalue(), err.getvalue()

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_the_callers_own_directory_stays_clean(self):
        # SPAWNFIX2 (S1 test hygiene): cmd_run probes the client binary before
        # a sandbox exists (`--version`/`--help` through check_client_modes), and
        # that probe inherits this process's cwd. The claude fake is argv-naive,
        # so the probe ran the whole worker script and dropped worker-new.txt
        # into the checkout running the suite. Pinned for the agy fake too: it
        # writes its "sandbox" files into the cwd as well, and only `agy`
        # declaring no modes keeps it out of the probe path today.
        cases = [
            ("agy sandbox-write", lambda: self.run_isolated(
                self.make_root(), self.make_fake_agy(), self.make_state(),
                "sandbox-write")),
            ("claude joinable", lambda: self.run_isolated_joinable(
                self.make_root(), self.make_fake_claude(), self.make_state())),
        ]
        for name, run in cases:
            with self.subTest(case=name):
                cwd = os.getcwd()
                before = set(os.listdir(cwd))
                rc, out, err = run()
                self.assertEqual(rc, 0, out + err)
                self.assertEqual(os.getcwd(), cwd, "the helper restored the cwd")
                self.assertEqual(set(os.listdir(cwd)) - before, set(),
                                 "the run wrote into the directory the suite "
                                 "was started in")

    def test_provider_stop_matches_a_redrawn_line(self):
        # WIPfix4 review: a stop line redrawn in place ("\r" then erase-line)
        # carries CSI bytes in front of the prefix; an SGR-only strip misses
        # it and the run fails open as rc 0.
        agent = self.agent
        line = "\r\x1b[2KError: Rate limit exceeded"
        self.assertEqual(agent.provider_stop("working\n" + line + "\n"),
                         "Error: Rate limit exceeded")

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_the_wip_commit_never_contains_secrets_or_logs(self):
        # Review WIPfix4: a worker writing configuration/api-keys.yml or
        # logs/x into its sandbox must see neither in the WIP commit; today
        # that holds only via --exclude-standard + the cloned .gitignore, so a
        # future edit of either is silent without this pin. The sandbox-write
        # fakes create both files.
        for mode in ("sandbox-write", "sandbox-write-provider-stop"):
            with self.subTest(mode=mode):
                root, stub, state = self.make_root(), self.make_fake_agy(), self.make_state()
                rc, out, err = self.run_isolated(root, stub, state, mode)
                self.assertIn("WIP-COMMITTED:", out)
                sb = self.lone_sandbox(state)
                files = subprocess.run(
                    ["git", "-C", sb, "show", "--name-only", "--format=", "HEAD"],
                    capture_output=True, text=True, check=True).stdout
                self.assertNotIn("configuration/api-keys.yml", files)
                self.assertNotIn("logs/x", files)
                self.assertNotIn("logs", files.split())
                self.assertIn("worker-new.txt", files)


def _fallthrough_registry(route_ids, policy=None):
    """A registry whose routes are exactly `route_ids`, plus the clients the
    capability gate reads and an optional `policy` section (SPAWNFREE: the
    ordered free-model list a --free fallthrough reads from here). The resolver
    itself is replaced by `_fallthrough_plan`, so providers/models are not
    consulted."""
    return {
        "clients": {
            "opencode": {"capabilities": {"shell": True, "write": True}},
            "claude": {"capabilities": {"shell": True, "write": True}},
        },
        "routes": {rid: {"id": rid, "class": "cheap", "legs": []} for rid in route_ids},
        "policy": policy or {},
    }


def _fallthrough_plan(card, brief, repo, orchestrator_model, now, registry, overlay,
                      track_record, client_state):
    """route_plan_for stand-in: the first route id still in `registry`.

    `_resolve_route_v2` drops the excluded ids before calling, so the second
    attempt sees only the routes that have not been tried yet. No routes left
    is the resolver's own input_required."""
    routes = list((registry.get("routes") or {}).keys())
    if not routes:
        return {"route": None, "state": "input_required",
                "reason": "no route survives the filters", "bucket": "S0",
                "defer_until": None}
    return {"route": routes[0], "state": "ready", "reason": "stub", "bucket": "S1",
            "defer_until": None}


def _fallthrough_run(case, route_ids, stops, clock=None, args_over=None, policy=None,
                     stop_tail=None):
    """Run cmd_run with the resolver and the client replaced by fakes; the
    sandbox is a real temp clone so WIP commits and re-runs are real.

    `case` is the running TestCase (its `agent` attribute is the module under
    test; its addCleanup removes the temp root and state dir).

    Returns (rc, out, err, calls, sandbox_names): `calls` is
    {"n": int, "cwds": [str], "cmds": [argv], "free_models": [str],
    "track": [record]}. The run is a gateway (opencode) run so `track_entry`
    emits a record per attempt - an own-account client's run is tracked as None
    (SPAWNCAP fallthrough records every provider-stopped attempt, not just the
    final plan).

    `args_over` (SPAWNFREE) overrides run-args fields (free, free_model, card,
    isolate); `policy` is the registry's policy section.

    `stop_tail` (REVROUTE item 3) is what a stopped attempt prints; the
    provider-state file is redirected into the same temp state dir and exposed
    as `case.provider_state`.
    """
    agent = case.agent
    root = _init_git_root()
    statedir = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, root, True)
    case.addCleanup(shutil.rmtree, statedir, True)
    old_root, old_track, old_overlay = (agent.ROOT, agent.TRACK_RECORD,
                                        agent.MEASURED_OVERLAY_PATH)
    agent.ROOT = root
    # A run without --isolate works in the caller's own directory (plan["cwd"] is
    # os.getcwd()), and the fake client writes there — so the whole run happens
    # inside the throwaway root, never in the checkout running the suite.
    old_cwd = os.getcwd()
    os.chdir(root)
    agent.TRACK_RECORD = os.path.join(statedir, "track-record.jsonl")
    agent.MEASURED_OVERLAY_PATH = os.path.join(statedir, "measured.json")
    case.provider_state = os.path.join(statedir, "provider-state.json")
    old_provider_state = agent.PROVIDER_STATE_PATH
    agent.PROVIDER_STATE_PATH = case.provider_state
    cfg = {"providers": {"omniroute": {"models": {rid: {} for rid in route_ids}}}}

    calls = {"n": 0, "cwds": [], "cmds": [], "route_marks": [], "free_models": [],
             "track": []}
    real_build_plan = agent.build_plan

    def marking_build_plan(*a, **k):
        # Tag each plan's env with its route, so a re-run that kept the
        # first plan's env shows up as a stale mark.
        plan = real_build_plan(*a, **k)
        plan["env"]["AUTOOS_TEST_ROUTE_MARK"] = plan["route"]["combo"]
        return plan

    def fake_run_client(cmd, cwd, env, reap=True, capture=False):
        calls["n"] += 1
        calls["cwds"].append(cwd)
        calls["cmds"].append(cmd)
        calls["route_marks"].append(env.get("AUTOOS_TEST_ROUTE_MARK"))
        calls["free_models"].append(
            (json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}") or {}).get("model"))
        with open(os.path.join(cwd, "attempt%d.txt" % calls["n"]), "w",
                  encoding="utf-8") as fh:
            fh.write("work\n")
        if calls["n"] <= stops:
            return agent.ClientExit(0, tail=stop_tail or "Error: Rate limit exceeded\n")
        return agent.ClientExit(0, tail="done\n")

    args = argparse.Namespace(
        client="opencode", tier=None, card="kind=implement", task="edit README.md",
        free=False, free_model=agent.DEFAULT_FREE_MODEL, isolate=True, auto=True,
        joinable=False, model=None, clean=False, allow_training=False,
        max_depth=None, lean=False, title=None, dry_run=False, no_defer=False)
    args.__dict__.update(args_over or {})
    env = dict(os.environ)
    env["AUTOOS_STATE_DIR"] = statedir
    # A gateway run needs a client key and a live gateway; both are faked
    # here. The key is what makes the run track-recorded at all.
    env["AUTOOS_OMNIROUTE_KEY"] = "test-only-key"
    out, err = io.StringIO(), io.StringIO()
    old_time = agent.time.time
    if clock is not None:
        agent.time.time = clock
    try:
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(agent, "load_registry",
                                   lambda path: _fallthrough_registry(route_ids, policy)):
                with mock.patch.object(agent, "route_plan_for", _fallthrough_plan), \
                        mock.patch.object(agent, "build_plan", marking_build_plan), \
                        mock.patch.object(agent, "gateway_up", lambda: True), \
                        mock.patch.object(agent, "resolve_model",
                                          lambda cfg, tier, clean, override:
                                              override or "omniroute/r-t2"):
                    with mock.patch.object(agent, "run_client", fake_run_client):
                        with mock.patch.object(agent.measure_mod, "client_state",
                                               lambda *a, **k: {}):
                            with mock.patch.object(
                                    agent.clients, "signin_state",
                                    lambda client, env=None: (None, "")):
                                with mock.patch("shutil.which",
                                                return_value="/usr/bin/opencode"):
                                    with contextlib.redirect_stdout(out), \
                                            contextlib.redirect_stderr(err):
                                        rc = agent.cmd_run(args, cfg)
    finally:
        os.chdir(old_cwd)  # before any cleanup tries to remove the temp root
        agent.time.time = old_time
        calls["track"] = agent.track.load(agent.TRACK_RECORD)
        (agent.ROOT, agent.TRACK_RECORD, agent.MEASURED_OVERLAY_PATH,
         agent.PROVIDER_STATE_PATH) = (
            old_root, old_track, old_overlay, old_provider_state)
    base = os.path.join(statedir, "sandboxes")
    names = os.listdir(base) if os.path.isdir(base) else []
    return rc, out.getvalue(), err.getvalue(), calls, names


class LeakStrictnessTests(unittest.TestCase):
    """LEAKFP2: the detection itself is the pre-75f2866 strict one, with exactly
    ONE narrow exemption - false positive B, a ref that is another worktree's
    own checked-out branch at BOTH the snapshot and the check. These call the
    real parent_snapshot/parent_leak against real temp repositories; the
    matching cmd_run-level evasions are the IsolateContainmentTests above."""

    WORKER_EMAIL = "autoos-worker@users.noreply.github.com"
    WORKER_ID = ["-c", "user.name=autoos-worker", "-c", "user.email=" + WORKER_EMAIL]

    def setUp(self):
        self.agent = load_agent()
        self.root = _init_git_root()
        self.addCleanup(shutil.rmtree, self.root, True)

    def git(self, *args, **kw):
        return subprocess.run(
            ["git", "-C", kw.get("cwd") or self.root, *args],
            capture_output=True, text=True, check=True,
            env=kw.get("env")).stdout.strip()

    def sibling_worktree(self, branch, existing=False):
        """`git worktree add` at a fresh path: `existing` checks that branch
        out, otherwise the branch is created here."""
        path = tempfile.mkdtemp()
        shutil.rmtree(path)
        self.addCleanup(shutil.rmtree, path, True)
        if existing:
            self.git("worktree", "add", "-q", path, branch)
        else:
            self.git("worktree", "add", "-q", "-b", branch, path)
        return path

    def worker_commit(self, cwd, name, env=None):
        with open(os.path.join(cwd, name), "w", encoding="utf-8") as fh:
            fh.write("worker\n")
        self.git("add", name, cwd=cwd)
        self.git(*self.WORKER_ID, "commit", "-q", "-m", "worker change",
                 cwd=cwd, env=env)
        return self.git("rev-parse", "HEAD", cwd=cwd)

    def test_a_sibling_worktrees_own_checked_out_branch_moving_is_not_a_leak(self):
        wt = self.sibling_worktree("lane-sib")
        snap = self.agent.parent_snapshot(self.root)
        self.worker_commit(wt, "sib.txt")
        # The exemption must be doing real work: the ref did move, and the
        # commit is the worker's own.
        self.assertNotEqual(snap[3]["refs/heads/lane-sib"],
                            self.git("rev-parse", "lane-sib"))
        self.assertEqual([], self.agent.parent_leak(snap, self.root))
        # and the move was the worker's own commit, not a no-op fixture
        self.assertEqual(self.WORKER_EMAIL,
                         self.git("log", "-1", "--format=%ae", "lane-sib"))

    def test_a_worktree_added_during_the_run_does_not_exempt_its_ref(self):
        # The exemption needs BOTH ends: `side` existed at the snapshot checked
        # out nowhere, so another writer that adopts it mid-run is still a leak
        # here - newness (of a ref or of a worktree) is never an excuse.
        snap = self.agent.parent_snapshot(self.root)
        wt = self.sibling_worktree("side", existing=True)
        self.worker_commit(wt, "late.txt")
        leak = self.agent.parent_leak(snap, self.root)
        self.assertTrue(any("refs/heads/side" in ln for ln in leak), leak)

    def test_a_worktree_inside_the_sandbox_is_not_exempt(self):
        # "another worktree" means another LANE: a worktree of the parent
        # repository that lives in this run's sandbox is not one.
        sb = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, sb, True)
        wt = os.path.join(sb, "sandboxes", "run-1")
        os.makedirs(os.path.dirname(wt))
        self.git("worktree", "add", "-q", "-b", "lane-sbx", wt)
        snap = self.agent.parent_snapshot(self.root)
        self.worker_commit(wt, "sbx.txt")
        leak = self.agent.parent_leak(snap, self.root, sb)
        self.assertTrue(any("refs/heads/lane-sbx" in ln for ln in leak), leak)

    def test_a_worker_commit_on_a_branch_created_during_the_run_is_a_leak(self):
        snap = self.agent.parent_snapshot(self.root)
        self.git("switch", "-q", "-c", "lane-mine")
        sha = self.worker_commit(self.root, "mine.txt")
        leak = self.agent.parent_leak(snap, self.root)
        self.assertTrue(any(sha in ln and "parent checkout" in ln for ln in leak),
                        leak)

    def test_a_decoy_branch_at_the_tip_does_not_exempt_a_parent_commit(self):
        snap = self.agent.parent_snapshot(self.root)
        sha = self.worker_commit(self.root, "worker.txt")
        self.git("branch", "decoy")
        leak = self.agent.parent_leak(snap, self.root)
        self.assertTrue(any(sha in ln for ln in leak), leak)

    def test_a_backdated_worker_commit_is_a_leak(self):
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00Z"
        env["GIT_COMMITTER_DATE"] = "2020-01-01T00:00:00Z"
        snap = self.agent.parent_snapshot(self.root)
        sha = self.worker_commit(self.root, "old.txt", env=env)
        leak = self.agent.parent_leak(snap, self.root)
        self.assertTrue(any(sha in ln for ln in leak), leak)

    def test_a_switch_c_and_fast_forward_back_is_a_leak(self):
        # The branch stays alive: 75f2866's made_on-leg read the reflog entry as
        # "made on a branch created during the run" and exempted the commit
        # entirely (LEAKFP2 review, evasion 3). Deleting it only made the old
        # code catch the leak by accident.
        snap = self.agent.parent_snapshot(self.root)
        back = self.git("branch", "--show-current")
        self.git("switch", "-q", "-c", "tmp-leak")
        sha = self.worker_commit(self.root, "tmp.txt")
        self.git("switch", "-q", back)
        self.git("merge", "-q", "--ff-only", "tmp-leak")
        leak = self.agent.parent_leak(snap, self.root)
        self.assertTrue(any(sha in ln for ln in leak), leak)

    def test_a_new_untracked_file_in_the_parent_is_a_leak(self):
        snap = self.agent.parent_snapshot(self.root)
        with open(os.path.join(self.root, "stray.txt"), "w", encoding="utf-8") as fh:
            fh.write("leak\n")
        leak = self.agent.parent_leak(snap, self.root)
        self.assertTrue(any("stray.txt" in ln for ln in leak), leak)


class ProviderStopFallthroughTests(unittest.TestCase):
    """SPAWNCAP (S2) part B: a provider-stopped resolver-routed --isolate run
    re-runs the same task in the same sandbox on the next route, after
    WIP-committing the stopped attempt, at most MAX_FALLTHROUGH times; then it
    exits 8 exactly as WIPfix did."""

    def setUp(self):
        self.agent = load_agent()

    def test_fallthrough_line_names_both_routes(self):
        self.assertEqual(
            self.agent.fallthrough_line("r-free", "Error: Rate limit exceeded", "r-cheap"),
            "provider stop on r-free: Error: Rate limit exceeded -> falling through to r-cheap")

    def test_provider_stop_matches_all_targets_skipped(self):
        # gateway 503 ALL_TARGETS_SKIPPED, printed as an error line.
        line = "Error: all targets were skipped by pre-dispatch filters"
        self.assertEqual(self.agent.provider_stop("working\n" + line + "\n"), line)

    def test_provider_stop_matches_credits_exhausted(self):
        line = "Error: credits exhausted"
        self.assertEqual(self.agent.provider_stop("working\n" + line + "\n"), line)

    def _run(self, route_ids, stops, clock=None, args_over=None, policy=None,
             stop_tail=None):
        return _fallthrough_run(self, route_ids, stops, clock=clock,
                                args_over=args_over, policy=policy,
                                stop_tail=stop_tail)

    def test_a_provider_stop_falls_through_to_the_next_route_and_succeeds(self):
        rc, out, err, calls, sandboxes = self._run(["r-free", "r-cheap"], stops=1)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(calls["n"], 2, "one stopped attempt plus one re-run")
        self.assertEqual(calls["cwds"][0], calls["cwds"][1], "same sandbox, same cwd")
        self.assertEqual(len(sandboxes), 1, sandboxes)
        self.assertIn("provider stop on r-free: Error: Rate limit exceeded "
                      "-> falling through to r-cheap", out + err)
        self.assertIn("WIP-COMMITTED", out + err)

    def test_a_fallthrough_re_run_gets_the_next_plans_env(self):
        # qoder review 2026-09-27: env was built once from the first plan, so a
        # re-run kept the stopped route's OPENCODE_CONFIG_CONTENT/session tag.
        _, out, err, calls, _ = self._run(["r-free", "r-cheap"], stops=1)
        self.assertEqual(calls["route_marks"], ["r-free", "r-cheap"], out + err)

    def test_fallthrough_stops_after_the_cap_and_exits_8(self):
        rc, out, err, calls, _ = self._run(
            ["r-free", "r-cheap", "r-cheap2", "r-cheap3"], stops=5)
        self.assertEqual(rc, 8, out + err)
        self.assertEqual(calls["n"], 3, "the first attempt plus MAX_FALLTHROUGH re-runs")
        lines = [ln for ln in (out + err).splitlines() if ln.startswith("provider stop on ")]
        self.assertEqual(len(lines), 2, lines)

    def test_no_next_route_exits_8_without_a_fallthrough(self):
        rc, out, err, calls, _ = self._run(["r-free"], stops=5)
        self.assertEqual(rc, 8, out + err)
        self.assertEqual(calls["n"], 1, "nowhere to fall through to")
        self.assertNotIn("falling through to", out + err)

    @staticmethod
    def _records(calls):
        """The track record as (route, gate, failure_class) triples."""
        return [(r["route"], r["gate"], r["failure_class"]) for r in calls["track"]]

    def test_every_provider_stopped_attempt_is_track_recorded(self):
        # REVFIX: a provider-stopped attempt that falls through was never its
        # own observation - only the final plan's record survived, so the dead
        # route looked healthy and `route` kept picking it.
        _, out, err, calls, _ = self._run(["r-free", "r-cheap"], stops=1)
        self.assertEqual(
            self._records(calls),
            [("r-free", "fail", "provider"), ("r-cheap", "pass", None)], out + err)

    def test_a_run_with_no_fallthrough_is_track_recorded_once(self):
        # The per-attempt record must not double-count the final plan's own
        # record when the loop never falls through.
        _, out, err, calls, _ = self._run(["r-free"], stops=0)
        self.assertEqual(self._records(calls), [("r-free", "pass", None)], out + err)

    def test_the_surviving_routes_latency_excludes_the_dead_attempts(self):
        # REVFIX review 2: the final record used the cumulative `start`, so the
        # surviving route's latency sample included the dead attempt's seconds
        # (which are already their own sample). One tick per time.time() call:
        # each attempt spans exactly one tick, so the survivor's latency is 1s.
        ticks = iter(range(1000))
        _, out, err, calls, _ = self._run(["r-free", "r-cheap"], stops=1,
                                          clock=lambda: float(next(ticks)))
        by_route = {r["route"]: r["latency_s"] for r in calls["track"]}
        self.assertEqual(by_route["r-free"], 1.0, out + err)  # its own attempt
        self.assertEqual(by_route["r-cheap"], 1.0, out + err)  # not 2+ (cumulative)

    def test_the_cap_records_every_fallthrough_attempt(self):
        _, out, err, calls, _ = self._run(
            ["r-free", "r-cheap", "r-cheap2", "r-cheap3"], stops=5)
        self.assertEqual(
            self._records(calls),
            [("r-free", "fail", "provider"),
             ("r-cheap", "fail", "provider"),
             ("r-cheap2", "fail", "provider")], out + err)

    # REVROUTE (S2) item 3: the stop line often states its own reset time, and
    # that is worth more than a track record -- it takes the provider out of the
    # rotation until the window passes.
    AGY_RESET_STOP = ("AGY_ERROR: 429 RESOURCE_EXHAUSTED: Individual quota reached. "
                      "Quota resets in ~83h\n")

    def test_a_provider_stop_is_offered_to_the_reset_recorder(self):
        recorded = []
        with mock.patch.object(self.agent, "record_reset_stop",
                               lambda *a, **k: recorded.append(a) or None):
            rc, out, err, calls, _ = self._run(["r-free", "r-cheap"], stops=1,
                                               stop_tail=self.AGY_RESET_STOP)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual([call[1] for call in recorded], ["r-free"],
                         "one record for the stopped attempt, none for the survivor")
        self.assertIn("resets in ~83h", recorded[0][0])

    def test_a_recorded_reset_window_is_printed_as_the_providers_new_until(self):
        with mock.patch.object(self.agent, "record_reset_stop",
                               lambda *a, **k: ("cheap-p", "2026-10-01T17:00:00Z")):
            rc, out, err, calls, _ = self._run(["r-free", "r-cheap"], stops=1,
                                               stop_tail=self.AGY_RESET_STOP)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("provider cheap-p unavailable until 2026-10-01T17:00:00Z",
                      out + err)


FREE_MODELS = ["opencode/nemotron-3-ultra-free",
               "opencode/muse-spark-1.3-contributor-free",
               "opencode/mimo-v2.6-flash-free"]


class FreeModelFallthroughTests(unittest.TestCase):
    """SPAWNFREE (S2) item 1: a --free run's model is not a route, so the
    SPAWNCAP fallthrough never reached it — the first 'Rate limit exceeded'
    ended the run with exit 8 (inbox 2026-09-27T17:08:22Z and 17:19:45Z,
    work/L1-routing/T1FREE.r2.out). A free run now re-runs the SAME task in the
    SAME sandbox on the next model of the registry's ordered free-model list
    (policy.free_client_models), the --free-model it was given first, with the
    same WIP-preserve logic and the same MAX_FALLTHROUGH bound."""

    def setUp(self):
        self.agent = load_agent()

    def _run(self, stops, clock=None, policy=None, **args_over):
        over = {"free": True, "free_model": FREE_MODELS[1], "isolate": True}
        over.update(args_over)
        return _fallthrough_run(
            self, ["r-free"], stops, clock=clock, args_over=over,
            policy=policy if policy is not None
            else {"free_client_models": {"opencode": FREE_MODELS}})

    def test_the_chain_puts_the_given_free_model_first(self):
        chain = self.agent.free_model_chain(
            {"free_client_models": {"opencode": FREE_MODELS}}, "opencode", FREE_MODELS[1])
        self.assertEqual(chain, [FREE_MODELS[1], FREE_MODELS[0], FREE_MODELS[2]])

    def test_the_chain_never_repeats_a_model(self):
        chain = self.agent.free_model_chain(
            {"free_client_models": {"opencode": FREE_MODELS}}, "opencode", FREE_MODELS[0])
        self.assertEqual(chain, FREE_MODELS)

    def test_a_chain_with_no_policy_is_the_single_given_model(self):
        self.assertEqual(self.agent.free_model_chain(None, "opencode", "m-x"), ["m-x"])

    def test_a_free_provider_stop_re_runs_the_next_free_model_in_the_same_sandbox(self):
        rc, out, err, calls, sandboxes = self._run(stops=1)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(calls["n"], 2, "the stopped attempt plus one re-run")
        self.assertEqual(calls["cwds"][0], calls["cwds"][1], "same sandbox, same cwd")
        self.assertEqual(len(sandboxes), 1, sandboxes)
        self.assertEqual(calls["free_models"], [FREE_MODELS[1], FREE_MODELS[0]],
                         "the given model first, then the next of the chain")

    def test_the_free_rerun_carries_the_new_model_in_the_client_command(self):
        _, out, err, calls, _ = self._run(stops=1)
        for cmd, model in zip(calls["cmds"], [FREE_MODELS[1], FREE_MODELS[0]]):
            self.assertEqual(cmd[cmd.index("--model") + 1], model, out + err)

    def test_the_free_fallthrough_wip_commits_the_stopped_attempt(self):
        rc, out, err, calls, sandboxes = self._run(stops=1)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("WIP-COMMITTED", out + err)
        self.assertEqual(len(sandboxes), 1, sandboxes)
        # calls["cwds"][0] is the sandbox clone itself (the client's cwd).
        files = subprocess.run(["git", "-C", calls["cwds"][0], "ls-files"],
                               capture_output=True, text=True).stdout.split()
        self.assertIn("attempt1.txt", files, "the stopped attempt's work survives on the branch")

    def test_the_free_fallthrough_line_names_both_models(self):
        _, out, err, _, _ = self._run(stops=1)
        self.assertIn("provider stop on %s: Error: Rate limit exceeded -> falling through to %s"
                      % (FREE_MODELS[1], FREE_MODELS[0]), out + err)

    def test_a_free_run_respects_max_fallthrough_and_exits_8(self):
        rc, out, err, calls, _ = self._run(stops=9)
        self.assertEqual(rc, 8, out + err)
        self.assertEqual(calls["n"], 1 + self.agent.MAX_FALLTHROUGH,
                         "the first attempt plus MAX_FALLTHROUGH re-runs")
        self.assertEqual(calls["free_models"],
                         [FREE_MODELS[1], FREE_MODELS[0], FREE_MODELS[2]][:calls["n"]],
                         "the given model first, then the chain's order, never a repeat")

    def test_a_free_run_with_no_chain_left_exits_8_after_one_attempt(self):
        rc, out, err, calls, _ = self._run(
            stops=9, free_model="opencode/only-free-model",
            policy={"free_client_models": {"opencode": []}})
        self.assertEqual(rc, 8, out + err)
        self.assertEqual(calls["n"], 1, "no other free model to try")
        self.assertNotIn("falling through to", out + err)

    def test_a_free_fallthrough_never_unlocks_a_privacy_refusal(self):
        # PRIV3's guard (a model that trains never serves privacy=sensitive)
        # lives in build_plan, and the re-run goes through build_plan, so a
        # refused re-plan stops the loop instead of trying another model.
        agent = self.agent
        real = agent.build_plan
        seen = {"n": 0}

        def second_plan_refused(*a, **k):
            seen["n"] += 1
            if seen["n"] > 1:
                raise agent.PrivacyRefused("privacy: guard says no")
            return real(*a, **k)

        with mock.patch.object(agent, "build_plan", second_plan_refused):
            rc, out, err, calls, _ = self._run(stops=1)
        self.assertEqual(rc, 8, out + err)
        self.assertEqual(calls["n"], 1, "the refused re-plan never starts a client")

    # SPAWNFIX (S2 fix of SPAWNFREE) item 1: `--free` without `--isolate` is the
    # documented usage (unattended-orchestration.md:71), but the fall-through's
    # WIP-preserve block read plan["sandbox"]["path"] unguarded, so the first
    # provider stop of a keyless non-isolated run died on TypeError instead of
    # moving to the next free model.
    def test_a_free_fallthrough_without_a_sandbox_re_runs_the_next_free_model(self):
        rc, out, err, calls, sandboxes = self._run(stops=1, isolate=False)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(calls["n"], 2, "the stopped attempt plus one re-run")
        self.assertEqual(calls["free_models"], [FREE_MODELS[1], FREE_MODELS[0]], out + err)
        self.assertEqual(sandboxes, [], "nothing was cloned")
        self.assertNotIn("WIP-COMMITTED", out + err,
                         "no sandbox, so there is no checkout to commit into")
        self.assertIn("falling through to", out + err)

    def test_a_free_run_without_a_sandbox_exits_8_when_the_chain_is_spent(self):
        # the rc is the FINAL attempt's, not the crash's.
        rc, out, err, calls, _ = self._run(stops=9, isolate=False)
        self.assertEqual(rc, 8, out + err)
        self.assertEqual(calls["n"], 1 + self.agent.MAX_FALLTHROUGH, out + err)
        self.assertNotIn("TypeError", out + err)

    def test_a_free_run_without_a_sandbox_and_without_a_chain_exits_8(self):
        rc, out, err, calls, _ = self._run(
            stops=9, isolate=False, free_model="opencode/only-free-model",
            policy={"free_client_models": {"opencode": []}})
        self.assertEqual(rc, 8, out + err)
        self.assertEqual(calls["n"], 1, "nowhere to fall through to")


QODERCLI_1_1_63_HELP = """Usage: qodercli [options] [command] [query...]

Qoder CLI - Defaults to interactive mode. Use -p/--print for non-interactive
output.

Arguments:
  query                                Initial prompt.

Options:
  -d, --debug                          Run in debug mode (default: false)
  -m, --model <model>                  Model for the current session
  --list-models                        List available models for the current
                                       user
  --permission-mode <mode>             Set the permission mode (choices:
                                       default, accept_edits,
                                       bypass_permissions, dont_ask, auto)
  --dangerously-skip-permissions       Bypass all permission checks
  --output-format <format>             Output format (choices: text, json,
                                       stream-json)
"""


@unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
def _fake_cli(dirpath, name, version, help_text):
    """A POSIX shell stand-in for an agent CLI: answers --version and --help,
    counts every --help it is asked for (the cache test), and records its own
    path so the checker's PATH lookup is the temp dir, never the host."""
    path = os.path.join(dirpath, name)
    script = ("#!/bin/sh\n"
              "case \"$1\" in\n"
              "  --version) printf '%%s\\n' '%s' ;;\n"
              "  --help) printf x >> '%s.help-calls'\n"
              "cat <<'FAKEHELP'\n%s\nFAKEHELP\n"
              "    ;;\n"
              "esac\n" % (version, path, help_text.strip()))
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(script)
    os.chmod(path, 0o755)
    return path


@unittest.skipIf(os.name == "nt", "sh stubs; tests/run-tests.sh runs these on Linux")
class ClientModeHelpTests(unittest.TestCase):
    """SPAWNFREE (S2) item 3: '--permission-mode accept_edits' is not a mode
    qodercli 1.1.63 offers, and 62 runs refused every write before anyone
    noticed (L1-backlog qoder lanes, 2026-09-27). Each client's declared modes
    are now validated against the client's own --help, once per binary version.
    """

    def setUp(self):
        self.clients = clients
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old_state = os.environ.get("AUTOOS_STATE_DIR")
        os.environ["AUTOOS_STATE_DIR"] = self.tmp
        self.addCleanup(self._restore_state)

    def _restore_state(self):
        if self.old_state is None:
            os.environ.pop("AUTOOS_STATE_DIR", None)
        else:
            os.environ["AUTOOS_STATE_DIR"] = self.old_state

    def _env(self, *names):
        return {"PATH": os.pathsep.join([self.tmp, os.environ.get("PATH", "")]),
                "HOME": os.environ.get("HOME", "/tmp"),
                "AUTOOS_STATE_DIR": self.tmp}

    def test_the_choices_list_parses_across_wrapped_lines(self):
        parsed = self.clients.parse_mode_choices(QODERCLI_1_1_63_HELP)
        self.assertEqual(parsed["--permission-mode"],
                         ["default", "accept_edits", "bypass_permissions", "dont_ask", "auto"])
        self.assertEqual(parsed["--output-format"], ["text", "json", "stream-json"])
        self.assertNotIn("--list-models", parsed)

    def test_quoted_choices_are_read_as_the_values_they_name(self):
        # `claude --help` quotes its list (choices: "acceptEdits", "plan"); a
        # quoted choice that does not match its own unquoted spelling makes the
        # checker refuse a client that is configured correctly.
        parsed = self.clients.parse_mode_choices(
            'Options:\n'
            '  --permission-mode <mode>   Permission mode (choices: "acceptEdits",\n'
            '                             "bypassPermissions", "plan".)\n')
        self.assertEqual(parsed["--permission-mode"],
                         ["acceptEdits", "bypassPermissions", "plan"])

    def test_a_client_whose_modes_the_help_offers_passes(self):
        _fake_cli(self.tmp, "qodercli", "1.1.63", QODERCLI_1_1_63_HELP)
        ok, reason = self.clients.check_client_modes(
            self.clients.CLIENTS["qoder"], env=self._env())
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_a_mode_the_client_does_not_offer_is_refused_with_the_accepted_list(self):
        _fake_cli(self.tmp, "qodercli", "1.1.63", QODERCLI_1_1_63_HELP)
        bogus = self.clients.Client("qoder", "qodercli", True, False, True, "login",
                                    modes={"read": ["--permission-mode", "plan"],
                                           "edit": ["--permission-mode", "acceptEdits"]})
        ok, reason = self.clients.check_client_modes(bogus, env=self._env())
        self.assertFalse(ok)
        self.assertIn("acceptEdits", reason)
        self.assertIn("plan", reason)
        self.assertIn("bypass_permissions", reason)  # the accepted list, verbatim
        self.assertIn("qodercli --help", reason)

    def test_the_help_is_read_once_per_client_binary_version(self):
        exe = _fake_cli(self.tmp, "qodercli", "1.1.63", QODERCLI_1_1_63_HELP)
        client = self.clients.CLIENTS["qoder"]
        env = self._env()
        for _ in range(3):
            ok, reason = self.clients.check_client_modes(client, env=env)
            self.assertTrue(ok, reason)
        self.assertEqual(self._help_calls(exe), 1, "cached by version")
        with io.open(exe, encoding="utf-8") as fh:
            script = fh.read()
        with io.open(exe, "w", encoding="utf-8") as fh:
            fh.write(script.replace("1.1.63", "1.1.64"))
        ok, reason = self.clients.check_client_modes(client, env=env)
        self.assertTrue(ok, reason)
        self.assertEqual(self._help_calls(exe), 2, "a new version re-reads the help")

    def test_an_entry_from_an_older_parser_is_re_read(self):
        # A cached reading the checker no longer trusts must not keep deciding
        # runs: the parser version is part of the cache key.
        exe = _fake_cli(self.tmp, "qodercli", "1.1.63", QODERCLI_1_1_63_HELP)
        client = self.clients.CLIENTS["qoder"]
        env = self._env()
        self.assertTrue(self.clients.check_client_modes(client, env=env)[0])
        path = self.clients._mode_cache_file(env)
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["qodercli"]["parser"] = self.clients.MODE_PARSER_VERSION - 1
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.assertTrue(self.clients.check_client_modes(client, env=env)[0])
        self.assertEqual(self._help_calls(exe), 2)

    def _help_calls(self, exe):
        try:
            return len(io.open(exe + ".help-calls", encoding="utf-8").read())
        except OSError:
            return 0

    def test_an_absent_binary_is_not_a_refusal(self):
        ok, reason = self.clients.check_client_modes(
            self.clients.CLIENTS["agy"], env={"PATH": self.tmp, "HOME": "/tmp"})
        self.assertIsNone(ok, reason)

    def test_a_client_with_no_declared_modes_never_asks_for_help(self):
        exe = _fake_cli(self.tmp, "agy", "1.2.12", "Usage: agy\n")
        ok, reason = self.clients.check_client_modes(self.clients.CLIENTS["agy"],
                                                     env=self._env())
        self.assertIsNone(ok, reason)
        self.assertEqual(self._help_calls(exe), 0)

    @unittest.skipIf(os.name == "nt", "sh stub; POSIX only")
    def test_the_spawner_refuses_a_bad_mode_before_starting_anything(self):
        agent = load_agent()
        _fake_cli(self.tmp, "qodercli", "1.1.63", QODERCLI_1_1_63_HELP)
        bogus = self.clients.Client("qoder", "qodercli", True, False, True, "login",
                                    promo=True,
                                    modes={"read": ["--permission-mode", "plan"],
                                           "edit": ["--permission-mode", "acceptEdits"]})
        started = []
        args = argparse.Namespace(
            client="qoder", tier=2, card=None, task="do it", free=False,
            free_model=agent.DEFAULT_FREE_MODEL, isolate=True, auto=True, joinable=False,
            model=None, clean=False, allow_training=False, max_depth=None, lean=False,
            title=None, dry_run=False, no_defer=False)
        cfg = {"agents": {a: {"model": "qoder/x"} for a in
                          ("t1-orchestrator", "t2-worker", "t3-reviewer")},
               "providers": {"qoder": {"models": {"x": {}}}}}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"AUTOOS_STATE_DIR": self.tmp,
                                          "AUTOOS_WORKERS_DIR": self.tmp,
                                          "PATH": self.tmp + os.pathsep + os.environ.get("PATH", "")},
                             clear=True):
            with mock.patch.dict(self.clients.CLIENTS, {"qoder": bogus}):
                with mock.patch.object(agent, "run_client",
                                       lambda *a, **k: started.append(1)):
                    with mock.patch.object(self.clients, "signin_state",
                                           lambda client, env=None: (None, "")):
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                            rc = agent.cmd_run(args, cfg)
        self.assertEqual(rc, 2, out.getvalue() + err.getvalue())
        self.assertEqual(started, [], "refused before the client started")
        self.assertIn("acceptEdits", err.getvalue())
        self.assertIn("qodercli --help", err.getvalue())


class FreeConcurrencyCapTests(unittest.TestCase):
    """SPAWNFREE (S2) item 2: three Muse free workers started together on one
    account and all died on 'Rate limit exceeded' (L1-backlog lanes lstby2e,
    hx4m, rv3). A --free run now counts the host's live worker records that use
    the same free provider and waits for a slot (bounded) before starting."""

    def setUp(self):
        self.agent = load_agent()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workers = os.path.join(self.tmp, "workers")
        os.makedirs(self.workers)

    def record(self, wid, model, state="live", **over):
        rec = {"id": wid, "pid": os.getpid(),
               "pid_start": self.agent._proc_starttime(os.getpid()),
               "started": self.agent.utc_now_iso(), "session_tag": "", "client": "opencode",
               "model": model, "route": "r", "title": "", "cwd": "/x", "sandbox": "",
               "task_head": "t", "depth": 1}
        if state == "ended":
            rec["ended"] = self.agent.utc_now_iso()
            rec["rc"] = 0
        if state == "dead":
            rec["pid"] = 999999999
            rec["pid_start"] = 1
        rec.update(over)
        with io.open(os.path.join(self.workers, wid + ".json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)

    def test_the_free_provider_is_the_model_prefix(self):
        self.assertEqual(self.agent.free_provider("opencode/muse-spark-1.3-contributor-free"),
                         "opencode")
        self.assertEqual(self.agent.free_provider("muse"), "muse")
        self.assertEqual(self.agent.free_provider(""), "")

    def test_the_cap_is_one_for_an_unlisted_provider(self):
        agent = self.agent
        self.assertEqual(agent.free_concurrency_cap({}, "opencode"),
                         agent.DEFAULT_FREE_CONCURRENCY)
        self.assertEqual(agent.DEFAULT_FREE_CONCURRENCY, 1,
                         "zen free is one shared account (3 workers, 3 rate limits)")
        policy = {"free_concurrency": {"$comment": ["prose"], "opencode": 3, "other": 2}}
        self.assertEqual(agent.free_concurrency_cap(policy, "opencode"), 3)
        self.assertEqual(agent.free_concurrency_cap(policy, "other"), 2)
        self.assertEqual(agent.free_concurrency_cap(policy, "third"), 1)

    def test_only_live_workers_of_the_same_free_provider_count(self):
        self.record("a", "opencode/muse-spark-1.3-contributor-free")
        self.record("b", "opencode/nemotron-3-ultra-free")
        self.record("c", "other/free-model")
        self.record("d", "opencode/mimo-v2.6-flash-free", state="ended")
        self.record("e", "opencode/mimo-v2.6-flash-free", state="dead")
        self.assertEqual(self.agent.live_free_workers(self.workers, "opencode"), 2)
        self.assertEqual(self.agent.live_free_workers(self.workers, "other"), 1)
        self.assertEqual(self.agent.live_free_workers(self.workers, "none"), 0)
        self.assertEqual(self.agent.live_free_workers(os.path.join(self.tmp, "nope"), "opencode"), 0)

    def _clock(self):
        """A fake (now, sleep) pair: sleep advances the clock, so a bounded
        wait is measured in the polls it really made."""
        state = {"t": 0.0}

        def now():
            return state["t"]

        def sleep(secs):
            state["t"] += secs
            sleep.calls.append(secs)
        sleep.calls = []
        return now, sleep

    def test_a_queued_run_waits_for_a_slot_and_says_so(self):
        agent = self.agent
        self.record("a", "opencode/muse-spark-1.3-contributor-free")
        lines = []
        now, sleep = self._clock()

        def sleep_and_free(secs):
            sleep(secs)
            if len(sleep.calls) == 2:  # the other worker ends on poll 2
                os.remove(os.path.join(self.workers, "a.json"))

        with contextlib.redirect_stderr(io.StringIO()):
            live, timed_out = agent.wait_for_free_slot("opencode", 1, self.workers,
                                                       printer=lines.append,
                                                       sleep=sleep_and_free, now=now,
                                                       poll_seconds=1,
                                                       deadline_seconds=60)
        self.assertEqual((live, timed_out), (0, False), lines)
        self.assertEqual(lines, ["queued behind 1 opencode workers",
                                 "queued behind 1 opencode workers"], lines)

    def test_a_queue_outlasting_the_bound_reports_timed_out(self):
        agent = self.agent
        self.record("a", "opencode/muse-spark-1.3-contributor-free")
        now, sleep = self._clock()
        lines = []
        with contextlib.redirect_stderr(io.StringIO()):
            live, timed_out = agent.wait_for_free_slot(
                "opencode", 1, self.workers, printer=lines.append, sleep=sleep,
                now=now, poll_seconds=30, deadline_seconds=120)
        self.assertTrue(timed_out, lines)
        self.assertEqual(live, 1)
        # bounded: polls at 0/30/60/90/120 s, and the fifth refuses to sleep
        # past the deadline rather than starting a 21st minute.
        self.assertEqual(lines, ["queued behind 1 opencode workers"] * 5, lines)
        self.assertEqual(sleep.calls, [30, 30, 30, 30], sleep.calls)

    def _cmd_run_free(self, wait_result, policy=None):
        agent = self.agent
        args = argparse.Namespace(
            client="opencode", tier=2, card=None, task="do it", free=True,
            free_model="opencode/muse-spark-1.3-contributor-free", isolate=False,
            auto=True, joinable=False, model=None, clean=False, allow_training=False,
            max_depth=None, lean=False, title=None, dry_run=False, no_defer=False)
        cfg = {"providers": {"opencode": {"models": {"muse-spark-1.3-contributor-free": {}}}},
               "agents": {"t2-worker": {"model": "opencode/muse-spark-1.3-contributor-free"}}}
        out, err = io.StringIO(), io.StringIO()
        calls = []
        with mock.patch.dict(os.environ, {"AUTOOS_WORKERS_DIR": self.workers,
                                          "AUTOOS_STATE_DIR": self.tmp}, clear=True):
            with mock.patch.object(agent, "wait_for_free_slot",
                                   lambda *a, **k: wait_result):
                with mock.patch.object(agent, "run_client",
                                       lambda *a, **k: calls.append(1) or agent.ClientExit(0)):
                    with mock.patch.object(agent.clients, "signin_state",
                                           lambda client, env=None: (None, "")):
                        with mock.patch("shutil.which", return_value="/usr/bin/opencode"):
                            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                                rc = agent.cmd_run(args, cfg)
        return rc, out.getvalue(), err.getvalue(), calls

    def test_a_free_run_that_cannot_get_a_slot_exits_9_without_starting(self):
        rc, out, err, calls = self._cmd_run_free((2, True))
        self.assertEqual(rc, self.agent.EXIT_FREE_QUEUE_TIMEOUT, out + err)
        self.assertEqual(self.agent.EXIT_FREE_QUEUE_TIMEOUT, 9)
        self.assertEqual(calls, [], "the client never started (no 429, no work lost)")
        self.assertIn("opencode", err)
        self.assertIn("2", err)

    def test_a_free_run_that_gets_a_slot_starts(self):
        rc, out, err, calls = self._cmd_run_free((0, False))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(calls, [1])

    def test_a_dry_run_never_waits_for_a_slot(self):
        agent = self.agent
        args = argparse.Namespace(
            client="opencode", tier=2, card=None, task="do it", free=True,
            free_model="opencode/muse-spark-1.3-contributor-free", isolate=False,
            auto=True, joinable=False, model=None, clean=False, allow_training=False,
            max_depth=None, lean=False, title=None, dry_run=True, no_defer=False)
        cfg = {"providers": {"opencode": {"models": {"muse-spark-1.3-contributor-free": {}}}},
               "agents": {"t2-worker": {"model": "opencode/muse-spark-1.3-contributor-free"}}}
        with mock.patch.dict(os.environ, {"AUTOOS_WORKERS_DIR": self.workers,
                                          "AUTOOS_STATE_DIR": self.tmp}, clear=True):
            with mock.patch.object(agent, "wait_for_free_slot",
                                   side_effect=AssertionError("a dry run waited for a slot")):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = agent.cmd_run(args, cfg)
        self.assertEqual(rc, 0)

    def test_the_bound_defaults_to_twenty_minutes(self):
        self.assertEqual(self.agent.FREE_QUEUE_TIMEOUT_SECONDS, 20 * 60)

    # SPAWNFIX (S2 fix of SPAWNFREE) item 2: the cap was check-then-act — the
    # gate counted the worker records, and the record only lands after the
    # clone — so N spawners started together each counted 0 live and all N
    # started into the same 429 the cap exists to prevent. The count and the
    # reservation are now one critical section under a provider lock in the
    # spawner state dir.
    plan = {"model": "opencode/muse-spark-1.3-contributor-free"}

    def reservations(self):
        return sorted(n for n in os.listdir(self.workers)
                      if n.endswith(self.agent.FREE_RESERVATION_SUFFIX))

    def test_two_simultaneous_gates_reserve_only_one_slot(self):
        agent = self.agent
        real_count = agent.live_free_workers
        delay = 0.3  # the window the clone used to sit in, between count and record

        def slow_count(directory, provider):
            live = real_count(directory, provider)
            time.sleep(delay)
            return live

        results = []
        with mock.patch.object(agent, "live_free_workers", slow_count):
            with contextlib.redirect_stderr(io.StringIO()):
                threads = [
                    threading.Thread(
                        target=lambda: results.append(
                            agent.free_slot_refusal(dict(self.plan), None,
                                                    directory=self.workers,
                                                    poll_seconds=0.05,
                                                    deadline_seconds=1.0)))
                    for _ in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(20)
        self.assertEqual(len(results), 2, results)
        proceeds = [r for r in results if r[0] is None]
        refusals = [r for r in results if r[0] is not None]
        self.assertEqual(len(proceeds), 1, "exactly one spawner got the one slot")
        self.assertEqual(len(refusals), 1, refusals)
        self.assertIn("saturated", refusals[0][0])
        self.assertEqual(len(self.reservations()), 1, self.reservations())
        with contextlib.redirect_stderr(io.StringIO()):
            agent.free_reservation_release(proceeds[0][1])

    def test_a_held_reservation_blocks_the_gate_until_it_is_released(self):
        agent = self.agent
        with contextlib.redirect_stderr(io.StringIO()):
            refusal, token = agent.free_slot_refusal(
                dict(self.plan), None, directory=self.workers,
                poll_seconds=0.05, deadline_seconds=0)
            self.assertIsNone(refusal, "an empty leg takes the slot at once")
            self.assertEqual(len(self.reservations()), 1, self.reservations())
            self.assertEqual(agent.live_free_reservations(self.workers, "opencode"), 1)
            self.assertEqual(agent.live_free_reservations(self.workers, "other"), 0)
            second, token2 = agent.free_slot_refusal(
                dict(self.plan), None, directory=self.workers,
                poll_seconds=0.05, deadline_seconds=0)
            self.assertIsNotNone(second, "the live reservation is the one occupant")
            self.assertIsNone(token2)
            agent.free_reservation_release(token)
            self.assertEqual(self.reservations(), [])
            third, token3 = agent.free_slot_refusal(
                dict(self.plan), None, directory=self.workers,
                poll_seconds=0.05, deadline_seconds=0)
            self.assertIsNone(third, "releasing the reservation frees the slot")
        agent.free_reservation_release(token3)

    def test_a_reservation_from_a_dead_pid_does_not_block(self):
        agent = self.agent
        path = os.path.join(self.workers, "dead-pid" + agent.FREE_RESERVATION_SUFFIX)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump({"id": "dead-pid", "pid": 999999999, "pid_start": 1,
                       "started": agent.utc_now_iso(), "provider": "opencode",
                       "model": self.plan["model"]}, fh)
        junk = os.path.join(self.workers, "corrupt" + agent.FREE_RESERVATION_SUFFIX)
        with io.open(junk, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertEqual(agent.live_free_reservations(self.workers, "opencode"), 0,
                         "a stale or unreadable reservation never counts")
        with contextlib.redirect_stderr(io.StringIO()):
            refusal, token = agent.free_slot_refusal(
                dict(self.plan), None, directory=self.workers,
                poll_seconds=0.05, deadline_seconds=0)
        self.assertIsNone(refusal, "a run killed long ago cannot queue a live one")
        agent.free_reservation_release(token)

    def test_a_reservation_is_never_a_ps_row(self):
        # ps reads every *.json in the workers dir; a reservation is a lock-held
        # placeholder, not a worker, so it must not show up there.
        agent = self.agent
        with contextlib.redirect_stderr(io.StringIO()):
            _, token = agent.free_slot_refusal(dict(self.plan), None,
                                                directory=self.workers,
                                                poll_seconds=0.05, deadline_seconds=0)
        self.assertEqual([r["model"] for r in agent.list_workers(self.workers)], [])
        agent.free_reservation_release(token)

    def test_a_started_free_run_releases_its_reservation(self):
        # The worker record replaces the reservation as the thing that occupies
        # the slot; nothing of either kind is left live after the run.
        agent = self.agent
        rc, out, err, calls = self._cmd_run_free_real()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(calls, [1])
        self.assertEqual(self.reservations(), [], "the reservation was released")
        rows = agent.list_workers(self.workers, include_ended=True)
        self.assertEqual(len(rows), 1, rows)
        self.assertTrue(rows[0]["state"].startswith("exited"), rows)

    def _cmd_run_free_real(self):
        """cmd_run for a --free run with the real gate (nothing patched out but
        the client itself)."""
        agent = self.agent
        args = argparse.Namespace(
            client="opencode", tier=2, card=None, task="do it", free=True,
            free_model="opencode/muse-spark-1.3-contributor-free", isolate=False,
            auto=True, joinable=False, model=None, clean=False, allow_training=False,
            max_depth=None, lean=False, title=None, dry_run=False, no_defer=False)
        cfg = {"providers": {"opencode": {"models": {"muse-spark-1.3-contributor-free": {}}}},
               "agents": {"t2-worker": {"model": "opencode/muse-spark-1.3-contributor-free"}}}
        out, err = io.StringIO(), io.StringIO()
        calls = []
        with mock.patch.dict(os.environ, {"AUTOOS_WORKERS_DIR": self.workers,
                                          "AUTOOS_STATE_DIR": self.tmp}, clear=True):
            with mock.patch.object(agent, "run_client",
                                   lambda *a, **k: calls.append(1) or agent.ClientExit(0)):
                with mock.patch.object(agent.clients, "signin_state",
                                       lambda client, env=None: (None, "")):
                    with mock.patch("shutil.which", return_value="/usr/bin/opencode"):
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                            rc = agent.cmd_run(args, cfg)
        return rc, out.getvalue(), err.getvalue(), calls


class OutsideFenceTaskDirTests(unittest.TestCase):
    """FENCE (L1-backlog 2026-09-27T04:49Z): the MCP run_job exports
    AUTOOS_TASK_DIR=<root>/logs/agents/<run id>, and a blocked worker's
    tools/autoos-ask.py writes question.json there. Under --isolate the
    worker's project is its sandbox clone, so the run dir is an
    external_directory the fence denied and ask-back exited 5. The fence
    re-allows exactly that one dir (its realpath must stay under
    <root>/logs/agents/); any other value only warns, never refuses."""

    def setUp(self):
        self.agent = load_agent()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        # outside_fence measures the run dir against the spawner's own ROOT.
        self.agent.ROOT = self.root
        self.agents = os.path.join(self.root, "logs", "agents")

    def fence(self, task_dir):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rules = self.agent.outside_fence("/x/data", task_dir)
        return rules, err.getvalue()

    def todays_rules(self):
        home = os.path.expanduser("~")
        allow = [os.path.join(home, ".local", "share", "opencode", "tool-output", "*"),
                 os.path.join(home, ".local", "share", "opencode", "shell", "*", "*"),
                 "/tmp/opencode/*",
                 os.path.join("/x/data", "opencode", "*")]
        return ([{"action": "external_directory", "resource": "*", "effect": "deny"}] +
                [{"action": "external_directory", "resource": p, "effect": "allow"}
                 for p in allow])

    def test_an_unset_task_dir_keeps_todays_rules_exactly(self):
        rules, err = self.fence(None)
        self.assertEqual(rules, self.todays_rules())
        self.assertEqual(err, "")

    def test_a_run_dir_under_logs_agents_is_allowed(self):
        run_dir = os.path.join(self.agents, "20260927-063221-abcdef")
        os.makedirs(run_dir)
        rules, err = self.fence(run_dir)
        self.assertEqual(rules[0], {"action": "external_directory", "resource": "*",
                                    "effect": "deny"})  # deny-all still first
        self.assertEqual(rules, self.todays_rules() + [
            {"action": "external_directory",
             "resource": os.path.join(os.path.realpath(run_dir), "*"),
             "effect": "allow"}])
        self.assertEqual(err, "")

    def test_a_task_dir_outside_logs_agents_adds_no_rule_and_warns(self):
        os.makedirs(os.path.join(self.root, "etc"))  # the escape target exists
        for task_dir in ("/tmp/x", os.path.join(self.agents, "..", "..", "etc")):
            with self.subTest(task_dir=task_dir):
                rules, err = self.fence(task_dir)
                self.assertEqual(rules, self.todays_rules())
                lines = err.strip().splitlines()
                self.assertEqual(len(lines), 1, err)  # ONE warning line
                self.assertIn("AUTOOS_TASK_DIR", lines[0])

    @unittest.skipIf(os.name == "nt", "symlink creation needs privilege on Windows")
    def test_a_symlink_under_logs_agents_escaping_it_adds_no_rule_and_warns(self):
        os.makedirs(os.path.join(self.root, "elsewhere"))
        os.makedirs(self.agents)
        link = os.path.join(self.agents, "20260927-063221-link")
        os.symlink(os.path.join(self.root, "elsewhere"), link)
        rules, err = self.fence(link)
        self.assertEqual(rules, self.todays_rules())
        self.assertIn("AUTOOS_TASK_DIR", err)


class _WorkerRecordBase(unittest.TestCase):
    """Shared fixtures for the host-wide worker registry (`ps`)."""

    def setUp(self):
        self.agent = load_agent()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.workers = os.path.join(self.tmp, "workers")
        self.old = os.environ.get("AUTOOS_WORKERS_DIR")
        os.environ["AUTOOS_WORKERS_DIR"] = self.workers
        self.addCleanup(self._restore)
        # cmd_run refuses (rc 3) when no gateway answers; CI has none, a dev host
        # usually does - pin it so these tests never depend on the host's stack.
        # CI also has no configuration/api-keys.yml, so no client key (rc 3 before
        # the gateway check); a dev host reads the main checkout's. Pin both.
        for name, value in (("gateway_up", True), ("client_key", "sk-test-key")):
            patcher = mock.patch.object(self.agent, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _restore(self):
        if self.old is None:
            os.environ.pop("AUTOOS_WORKERS_DIR", None)
        else:
            os.environ["AUTOOS_WORKERS_DIR"] = self.old

    def write(self, wid="w1", **over):
        os.makedirs(self.workers, mode=0o700, exist_ok=True)
        rec = {"id": wid, "pid": os.getpid(), "pid_start": self.agent._proc_starttime(os.getpid()),
               "started": self.agent.utc_now_iso(), "session_tag": "lane-a", "client": "opencode",
               "model": "m", "route": "t2-worker", "title": "", "cwd": "/x", "sandbox": "",
               "task_head": "do a thing", "depth": 1}
        rec.update(over)
        path = os.path.join(self.workers, wid + ".json")
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        return path


class WorkerRecordTests(_WorkerRecordBase):
    """list_workers / workers_dir: state, the pid-reuse guard and pruning."""

    def test_workers_dir_honours_the_env_override_and_is_0700(self):
        self.assertEqual(self.agent.workers_dir(), self.workers)
        self.assertTrue(os.path.isdir(self.workers))
        self.assertEqual(os.stat(self.workers).st_mode & 0o777, 0o700)

    def test_a_dry_run_writes_no_record(self):
        plan = {"agent": "t2-worker", "client": "opencode", "model": "m",
                "cmd": [sys.executable, "-c", "pass"], "env": {},
                "route": {"combo": "t2-worker", "reason": "card", "privacy": "public",
                          "review": False, "tier": 2},
                "depth": (1, 2), "free": False, "sandbox": None, "cwd": self.tmp,
                "session_tag": "lane-a"}
        ns = argparse.Namespace(client="opencode", task="do it", free=False, dry_run=True,
                                card=None, clean=False, tier=2, joinable=False, lean=False,
                                isolate=False, auto=True, title=None, model=None,
                                free_model=self.agent.DEFAULT_FREE_MODEL, max_depth=None,
                                allow_training=False, no_defer=False)
        with mock.patch.object(self.agent, "build_plan", return_value=plan):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = self.agent.cmd_run(ns, {})
        self.assertEqual(rc, 0)
        self.assertEqual([f for f in (os.listdir(self.workers)
                                      if os.path.isdir(self.workers) else [])], [])

    def test_current_pid_and_start_time_is_running(self):
        if self.agent._proc_starttime(os.getpid()) is None:
            self.skipTest("no /proc start time on this host")
        self.write()
        rows = self.agent.list_workers(self.workers)
        self.assertEqual([r["state"] for r in rows], ["running"])

    def test_worker_record_file_is_0600_and_leaves_no_tmp(self):
        os.makedirs(self.workers, mode=0o700, exist_ok=True)
        path = os.path.join(self.workers, "w9.json")
        self.agent._write_worker_record(path, {"id": "w9", "pid": os.getpid()})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(self.workers), ["w9.json"])

    def test_a_pid_that_does_not_exist_is_died(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.write(pid=proc.pid, pid_start=None)
        rows = self.agent.list_workers(self.workers)
        self.assertEqual([r["state"] for r in rows], ["died"])

    def test_a_start_time_mismatch_is_died(self):
        start = self.agent._proc_starttime(os.getpid())
        if start is None:
            self.skipTest("no /proc start time on this host")
        self.write(pid_start=start + 1)
        rows = self.agent.list_workers(self.workers)
        self.assertEqual([r["state"] for r in rows], ["died"])

    def test_a_start_time_mismatch_is_died_with_mocked_start(self):
        # The guard fires on a genuinely different start time, host-independently.
        self.write(pid_start=12345)
        with mock.patch.object(self.agent, "_proc_starttime", return_value=54321):
            rows = self.agent.list_workers(self.workers)
        self.assertEqual([r["state"] for r in rows], ["died"])

    def test_a_live_pid_with_unreadable_start_time_is_running(self):
        # Q1: a live pid whose start time cannot be read now (permissions,
        # GetProcessTimes failure) must NOT be reported as died - the reuse
        # guard is skipped, matching list_workers' docstring.
        self.write(pid_start=12345)
        with mock.patch.object(self.agent, "_proc_starttime", return_value=None):
            rows = self.agent.list_workers(self.workers)
        self.assertEqual([r["state"] for r in rows], ["running"])

    def test_ended_is_exited_rc_and_hidden_without_include_ended(self):
        self.write(ended=self.agent.utc_now_iso(), rc=7)
        self.assertEqual(self.agent.list_workers(self.workers), [])
        rows = self.agent.list_workers(self.workers, include_ended=True)
        self.assertEqual([r["state"] for r in rows], ["exited rc=7"])

    def test_an_eight_day_old_ended_record_is_deleted(self):
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)
        path = self.write(ended=old.isoformat(timespec="seconds").replace("+00:00", "Z"), rc=0)
        rows = self.agent.list_workers(self.workers, include_ended=True)
        self.assertEqual(rows, [])
        self.assertFalse(os.path.exists(path))

    def test_a_corrupt_record_is_skipped_never_raises(self):
        self.write()
        bad = os.path.join(self.workers, "bad.json")
        with io.open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        rows = self.agent.list_workers(self.workers)
        self.assertEqual([r["id"] for r in rows], ["w1"])

    def test_cmd_run_records_during_the_run_and_ended_with_rc_after(self):
        plan = {"agent": "t2-worker", "client": "opencode", "model": "m",
                "cmd": [sys.executable, "-c", "pass"], "env": {},
                "route": {"combo": "t2-worker", "reason": "card", "privacy": "public",
                          "review": False, "tier": 2},
                "depth": (1, 2), "free": False, "sandbox": None, "cwd": self.tmp,
                "session_tag": "lane-a"}
        ns = argparse.Namespace(client="opencode", task="do it", free=False, dry_run=False,
                                card=None, clean=False, tier=2, joinable=False, lean=False,
                                isolate=False, auto=True, title="t", model=None,
                                free_model=self.agent.DEFAULT_FREE_MODEL, max_depth=None,
                                allow_training=False, no_defer=False)
        seen = {}

        def fake_run(*a, **k):
            files = [f for f in os.listdir(self.workers) if f.endswith(".json")]
            self.assertEqual(len(files), 1)
            with io.open(os.path.join(self.workers, files[0]), encoding="utf-8") as fh:
                seen["rec"] = json.load(fh)
            self.assertIsNone(seen["rec"].get("ended"))
            self.assertEqual(seen["rec"]["pid"], os.getpid())
            self.assertEqual(a[2]["AUTOOS_WORKERS_DIR"], self.workers)
            return self.agent.ClientExit(4)

        with mock.patch.object(self.agent, "build_plan", return_value=plan), \
                mock.patch.object(self.agent, "run_client", side_effect=fake_run), \
                mock.patch.object(self.agent, "log_run"), \
                mock.patch.object(self.agent.clients, "signin_state", return_value=(None, "")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = self.agent.cmd_run(ns, {})
        self.assertEqual(rc, 4)
        self.assertEqual(seen["rec"]["client"], "opencode")
        self.assertEqual(seen["rec"]["route"], "t2-worker")
        self.assertEqual(seen["rec"]["task_head"], "do it")
        with io.open(os.path.join(self.workers, seen["rec"]["id"] + ".json"), encoding="utf-8") as fh:
            after = json.load(fh)
        self.assertTrue(after["ended"])
        self.assertEqual(after["rc"], 4)


    def test_a_failed_end_record_keeps_the_client_rc(self):
        # L1-routing review note (1): the finally-block record write must never
        # replace the client's rc (disk full, permissions).
        plan = {"agent": "t2-worker", "client": "opencode", "model": "m",
                "cmd": [sys.executable, "-c", "pass"], "env": {},
                "route": {"combo": "t2-worker", "reason": "card", "privacy": "public",
                          "review": False, "tier": 2},
                "depth": (1, 2), "free": False, "sandbox": None, "cwd": self.tmp,
                "session_tag": "lane-a"}
        ns = argparse.Namespace(client="opencode", task="do it", free=False, dry_run=False,
                                card=None, clean=False, tier=2, joinable=False, lean=False,
                                isolate=False, auto=True, title="t", model=None,
                                free_model=self.agent.DEFAULT_FREE_MODEL, max_depth=None,
                                allow_training=False, no_defer=False)

        def boom(*_a, **_k):
            raise OSError(28, "No space left on device")

        err = io.StringIO()
        with mock.patch.object(self.agent, "build_plan", return_value=plan), \
                mock.patch.object(self.agent, "run_client", return_value=self.agent.ClientExit(4)), \
                mock.patch.object(self.agent, "_worker_record_end", side_effect=boom), \
                mock.patch.object(self.agent, "log_run"), \
                mock.patch.object(self.agent.clients, "signin_state", return_value=(None, "")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                rc = self.agent.cmd_run(ns, {})
        self.assertEqual(rc, 4)
        self.assertIn("could not update worker record", err.getvalue())


    def test_a_record_start_failure_still_runs_the_client(self):
        # V4 ps final review: workers_dir()/_worker_record_start() run before
        # run_client; an OSError there must not stop the client from launching.
        plan = {"agent": "t2-worker", "client": "opencode", "model": "m",
                "cmd": [sys.executable, "-c", "pass"], "env": {},
                "route": {"combo": "t2-worker", "reason": "card", "privacy": "public",
                          "review": False, "tier": 2},
                "depth": (1, 2), "free": False, "sandbox": None, "cwd": self.tmp,
                "session_tag": "lane-a"}
        ns = argparse.Namespace(client="opencode", task="do it", free=False, dry_run=False,
                                card=None, clean=False, tier=2, joinable=False, lean=False,
                                isolate=False, auto=True, title="t", model=None,
                                free_model=self.agent.DEFAULT_FREE_MODEL, max_depth=None,
                                allow_training=False, no_defer=False)
        called = {}

        def boom(*_a, **_k):
            raise OSError(28, "No space left on device")

        def fake_run(*_a, **_k):
            called["ran"] = True
            return self.agent.ClientExit(4)

        err = io.StringIO()
        with mock.patch.object(self.agent, "build_plan", return_value=plan), \
                mock.patch.object(self.agent, "_worker_record_start", side_effect=boom), \
                mock.patch.object(self.agent, "run_client", side_effect=fake_run), \
                mock.patch.object(self.agent, "log_run"), \
                mock.patch.object(self.agent.clients, "signin_state", return_value=(None, "")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                rc = self.agent.cmd_run(ns, {})
        self.assertTrue(called.get("ran"), "run_client must run without a worker record")
        self.assertEqual(rc, 4)
        self.assertIn("could not write worker record", err.getvalue())


class WindowsLivenessTests(_WorkerRecordBase):
    """V1/V2 ps final review: on Windows liveness and the pid-reuse start time
    go through ctypes (OpenProcess/GetExitCodeProcess/GetProcessTimes/
    CloseHandle), never os.kill - signal 0 there Ctrl+C's a live worker. The
    fake kernel32 lets these run on Linux."""

    def call(self, k32, fn, *args):
        fake = fake_windows_ctypes(k32)
        with mock.patch.dict(sys.modules, {"ctypes": fake}), \
                mock.patch.object(self.agent, "_WIN_KERNEL32", None), \
                mock.patch.object(self.agent.os, "name", "nt"), \
                mock.patch.object(self.agent.os, "kill",
                                  side_effect=AssertionError("os.kill must not run on Windows")):
            return fn(*args)

    def test_the_helper_sets_typed_ctypes_signatures_once(self):
        # Q2/Q3: without restype, ctypes truncates a 64-bit HANDLE to a C int.
        # The helper declares the signatures (cached, not per call); assert the
        # restype landed on the fake function objects.
        k32 = _FakeKernel32()
        fake = fake_windows_ctypes(k32)
        with mock.patch.dict(sys.modules, {"ctypes": fake}), \
                mock.patch.object(self.agent, "_WIN_KERNEL32", None):
            first = self.agent._win_kernel32()
            second = self.agent._win_kernel32()
        self.assertIs(first, k32)
        self.assertIs(second, k32, "the helper must cache, not rebuild per call")
        self.assertIs(k32.OpenProcess.restype, fake.wintypes.HANDLE)
        self.assertIs(k32.GetExitCodeProcess.restype, fake.wintypes.BOOL)
        self.assertIs(k32.GetProcessTimes.restype, fake.wintypes.BOOL)
        self.assertIs(k32.CloseHandle.restype, fake.wintypes.BOOL)
        self.assertIsNotNone(k32.OpenProcess.argtypes)
        self.assertEqual(len(k32.OpenProcess.argtypes), 3)
        self.assertEqual(len(k32.GetProcessTimes.argtypes), 5)

    def test_a_live_windows_pid_is_alive_and_never_os_kill(self):
        k32 = _FakeKernel32(handle=7, exit_code=259)
        self.assertTrue(self.call(k32, self.agent._pid_alive, 4242))
        self.assertEqual(k32.opened, [(0x1000, False, 4242)])
        self.assertEqual(k32.closed, [7])

    def test_a_null_handle_with_no_error_is_not_alive(self):
        self.assertFalse(self.call(_FakeKernel32(handle=None), self.agent._pid_alive, 4242))

    def test_access_denied_means_a_live_process(self):
        # ERROR_ACCESS_DENIED (5): the process exists, owned by someone else.
        k32 = _FakeKernel32(handle=None, last_error=5)
        self.assertTrue(self.call(k32, self.agent._pid_alive, 4242))

    def test_a_non_still_active_exit_code_is_not_alive(self):
        self.assertFalse(self.call(_FakeKernel32(exit_code=0), self.agent._pid_alive, 1))

    def test_windows_start_time_comes_from_get_process_times(self):
        self.assertEqual(self.call(_FakeKernel32(created=987654321),
                                   self.agent._proc_starttime, 1), 987654321)

    def test_windows_start_time_is_none_when_the_process_is_denied(self):
        k32 = _FakeKernel32(handle=None, last_error=5)
        self.assertIsNone(self.call(k32, self.agent._proc_starttime, 1))

    def test_windows_start_time_is_none_when_get_process_times_fails(self):
        k32 = _FakeKernel32(times_ok=False)
        self.assertIsNone(self.call(k32, self.agent._proc_starttime, 1))


class WindowsPrototypeTests(unittest.TestCase):
    """Real ctypes type-checking (no fake module): every by-reference argument
    _win_liveness passes must be accepted by the prototype _win_kernel32 declares.
    A POINTER(FILETIME) prototype rejected byref(c_ulonglong) with ArgumentError."""

    def test_declared_pointer_types_accept_the_values_passed(self):
        import ctypes
        agent = load_agent()
        exit_code_t, stamp_t = agent._win_value_types()
        ctypes.POINTER(exit_code_t).from_param(ctypes.byref(exit_code_t()))
        ctypes.POINTER(stamp_t).from_param(ctypes.byref(stamp_t()))
        self.assertEqual(ctypes.sizeof(stamp_t), 8)  # one FILETIME
        src = AGENT.read_text(encoding="utf-8")
        self.assertNotIn("POINTER(wintypes.FILETIME)", src)
        self.assertNotIn("ctypes.c_ulonglong() for", src)


class PsTests(_WorkerRecordBase):
    """The `ps` subcommand and the MCP `ps` tool read the same rows."""

    def test_ps_json_parses_and_has_the_columns(self):
        self.write()
        r = run_agent("ps", "--json", env=clean_env(AUTOOS_WORKERS_DIR=self.workers))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["dir"], self.workers)
        row = data["workers"][0]
        for key in ("id", "state", "elapsed", "client", "model", "lane", "pid", "title", "task"):
            self.assertIn(key, row)
        self.assertEqual(row["state"], "running")

    def test_ps_says_no_workers_running_when_empty(self):
        os.makedirs(self.workers, mode=0o700, exist_ok=True)
        r = run_agent("ps", env=clean_env(AUTOOS_WORKERS_DIR=self.workers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no workers running", r.stdout)

    def test_ps_all_window_is_by_end_time_not_run_length(self):
        # --all adds workers that EXITED in the last 24 h: a 1-minute run that
        # ended 3 days ago is out, a 30-hour run that ended an hour ago is in.
        now = datetime.datetime.now(datetime.timezone.utc)
        iso = lambda d: d.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.write("old", started=iso(now - datetime.timedelta(days=3, minutes=1)),
                   ended=iso(now - datetime.timedelta(days=3)), rc=0)
        self.write("long", started=iso(now - datetime.timedelta(hours=31)),
                   ended=iso(now - datetime.timedelta(hours=1)), rc=0)
        r = run_agent("ps", "--all", "--json", env=clean_env(AUTOOS_WORKERS_DIR=self.workers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([w["id"] for w in json.loads(r.stdout)["workers"]], ["long"])

    def test_mcp_ps_returns_the_same_rows(self):
        self.write()
        rows = self.agent.list_workers(self.workers)
        out = mcp_server.ps()
        self.assertEqual(out["dir"], self.workers)
        self.assertEqual([(w["id"], w["state"], w["client"]) for w in out["workers"]],
                         [(w["id"], w["state"], w["client"]) for w in rows])

    def test_an_exited_record_ended_three_days_ago_is_hidden_by_both_callers(self):
        # V3 ps final review: the CLI's --all 24 h end-time window and the MCP
        # ps(include_ended=True) must agree; the 7-day prune must not leak an
        # old exited record into the MCP tool.
        now = datetime.datetime.now(datetime.timezone.utc)
        iso = lambda d: d.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.write("old", started=iso(now - datetime.timedelta(days=3, minutes=1)),
                   ended=iso(now - datetime.timedelta(days=3)), rc=0)
        self.write("recent", started=iso(now - datetime.timedelta(hours=2)),
                   ended=iso(now - datetime.timedelta(hours=1)), rc=0)
        r = run_agent("ps", "--all", "--json", env=clean_env(AUTOOS_WORKERS_DIR=self.workers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([w["id"] for w in json.loads(r.stdout)["workers"]], ["recent"])
        out = mcp_server.ps(include_ended=True)
        self.assertEqual([w["id"] for w in out["workers"]], ["recent"])


def _reviewer_registry():
    """REVROUTE (S2) item 2 fixture: _small_route_registry() plus a
    ``policy.reviewers`` list the spawner can walk.

    Four reviewers, four families, one of them client-native (no ``leg``) and one
    ``first_pass_only``. The two legs go through providers the fixture owns:
    ``muse_api`` trains on prompts (so a privacy=sensitive card must walk past
    it) and ``gem_api`` does not. Far-future/past dates are deliberate: no test
    has to patch the clock to make a provider look down or back up.
    """
    reg = _small_route_registry()
    reg["providers"]["muse_api"] = {"id": "muse_api", "tier": "paid",
                                    "trains_on_prompts": True}
    reg["providers"]["gem_api"] = {"id": "gem_api", "tier": "paid",
                                   "trains_on_prompts": False}
    # A sensitive card must still have a route to run on: the fixture's own two
    # routes are private-safe, so only the *reviewer* list can fail privacy.
    for provider in ("free-p", "cheap-p"):
        reg["providers"][provider].update({"tier": "paid", "trains_on_prompts": False})
    reg["models"]["muse-contrib"] = {"id": "muse-contrib", "family": "meta"}
    reg["models"]["gem-flash"] = {"id": "gem-flash", "family": "google"}
    # capabilities: --isolate asks for shell+write (SPAWNCAP), and a fixture with
    # no declared client would be refused before the reviewer gate ever ran.
    reg["clients"] = {
        "opencode": {"id": "opencode", "capabilities": {"shell": True, "write": True}},
        "claude": {"id": "claude", "capabilities": {"shell": True, "write": True}},
    }
    reg["policy"]["reviewers"] = [
        {"client": "opencode", "model": "omniroute/muse", "family": "meta",
         "leg": "muse_api/muse-contrib", "paid": True, "source": "test"},
        {"client": "gemini", "model": "gem-flash", "family": "google",
         "leg": "gem_api/gem-flash", "paid": False, "source": "test"},
        {"client": "qoder", "model": "qwen3.8-flash", "family": "qwen",
         "paid": False, "source": "test"},
        {"client": "claude", "model": "haiku", "family": "anthropic",
         "paid": True, "first_pass_only": True, "source": "test"},
    ]
    return reg


class ReviewerGateTests(unittest.TestCase):
    """REVROUTE (S2) item 2: the spawner resolves WHO reviews an authored review
    card from ``policy.reviewers``, and refuses to start a run that has no
    eligible reviewer -- a same-family self-review is not an independent review,
    and waiting is better than running one.

    Same harness as RunCardV2Tests (no network, no real client probes, no key):
    the registry and the client state are fixtures, the run is a dry run unless a
    test is specifically checking that nothing was cloned or started.
    """

    DOWN = "2099-01-01T00:00:00Z"
    DOWN_LATER = "2099-06-01T00:00:00Z"

    def setUp(self):
        self.agent = load_agent()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = tmp
        self.agent.MEASURED_OVERLAY_PATH = os.path.join(tmp, "measured.json")
        self.agent.TRACK_RECORD = os.path.join(tmp, "track-record.jsonl")
        # No recorded provider stop: this fixture decides who reviews from the
        # registry and the probes, not from an outage some earlier run saw.
        self.agent.PROVIDER_STATE_PATH = os.path.join(tmp, "provider-state.json")
        self.agent.DEFAULT_ORCHESTRATOR_MODEL = "orch"
        self.registry = _reviewer_registry()
        patch = mock.patch.object(self.agent, "load_registry", lambda path: self.registry)
        patch.start()
        self.addCleanup(patch.stop)
        self.client_state = {name: {"installed": True, "signed_in": True, "reason": ""}
                             for name in ("opencode", "gemini", "qoder", "claude")}
        state = mock.patch.object(self.agent.measure_mod, "client_state",
                                  lambda *a, **k: dict(self.client_state))
        state.start()
        self.addCleanup(state.stop)

    def cfg(self):
        # "muse" is the head of this fixture's policy.reviewers list spelled as a
        # gateway model heading -- what REVROUTE item 4 makes opencode.jsonc do
        # for the real paid reviewer.
        names = list(routing.ALL_COMBOS) + ["r-free", "r-cheap", "muse"]
        return {"providers": {"omniroute": {"models": {n: {} for n in names}}}}

    def args(self, **overrides):
        ns = argparse.Namespace(
            tier=None, card="kind=review,author=qwen,paths=tools/registry.py",
            allow_training=False, client="opencode", joinable=False, max_depth=None,
            clean=False, model=None, free=False, free_model=self.agent.DEFAULT_FREE_MODEL,
            isolate=False, auto=True, lean=False, title=None, dry_run=True, task="x",
            no_defer=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def route(self, **overrides):
        """The route dict cmd_run would act on (build_plan, no clone, no start)."""
        return self.agent.build_plan(self.args(**overrides), self.cfg())["route"]

    def run_cmd_run(self, **overrides):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.agent.cmd_run(self.args(**overrides), self.cfg())
        return rc, out.getvalue(), err.getvalue()

    # --- the reviewer is picked from the registry, not from a constant ------

    def test_an_authored_v2_review_card_carries_a_review_plan(self):
        review = self.route()["review_plan"]
        self.assertIsNotNone(review, "a kind=review card with an author must know who reviews")
        self.assertEqual(review["state"], "resolved")
        self.assertEqual(review["author"], "qwen")
        self.assertEqual(review["author_family"], "qwen")
        self.assertEqual(review["reviewer"]["family"], "meta")

    def test_the_reviewer_is_cross_family_and_the_walk_stops_at_the_first_usable(self):
        review = self.route()["review_plan"]
        self.assertNotEqual(review["reviewer"]["family"], review["author_family"])
        self.assertEqual(review["skipped"], [],
                         "the head of the list was usable, nothing was passed over")

    def test_a_same_family_reviewer_is_skipped_with_the_reason_exposed(self):
        review = self.route(card="kind=review,author=meta,paths=tools/registry.py")["review_plan"]
        self.assertEqual(review["reviewer"]["family"], "google")
        self.assertEqual([s["model"] for s in review["skipped"]], ["omniroute/muse"])
        self.assertIn("same family as author (meta)", review["skipped"][0]["reasons"])

    def test_a_v1_role_review_card_with_an_author_is_resolved_too(self):
        def boom(*a, **k):
            raise AssertionError("a v1 card must not be routed by the resolver")
        with mock.patch.object(self.agent, "route_plan_for", boom):
            review = self.route(card="role=review,author=qwen")["review_plan"]
        self.assertEqual(review["state"], "resolved")
        self.assertEqual(review["reviewer"]["family"], "meta")

    def test_a_sensitive_card_walks_past_the_training_reviewer(self):
        route = self.route(card="kind=review,author=qwen,privacy=sensitive,"
                                "paths=tools/registry.py")
        self.assertEqual(route["review_plan"]["reviewer"]["family"], "google")
        skipped = route["review_plan"]["skipped"]
        self.assertEqual([s["model"] for s in skipped], ["omniroute/muse"])
        self.assertTrue([r for r in skipped[0]["reasons"] if r.startswith("privacy:")],
                        "the reason must name privacy, not availability")

    def test_the_author_never_changes_the_route_the_resolver_picks(self):
        with_author = self.route()
        without = self.route(card="kind=review,paths=tools/registry.py")
        self.assertEqual(with_author["reason"], without["reason"],
                         "who reviews is decided from the same plan, so it must not "
                         "move the route scoring")
        self.assertEqual(with_author["bucket"], without["bucket"])

    # --- the picked reviewer is the model that runs (item 2 + item 4) -------

    def test_the_resolved_reviewer_is_the_model_that_runs(self):
        route = self.route()
        self.assertEqual(route["model"], "omniroute/muse")
        self.assertEqual(route["combo"], "muse")
        self.assertEqual(route["reviewer_note"], "reviewer-model: opencode omniroute/muse")

    def test_the_reviewer_model_shows_up_in_the_command_the_run_would_use(self):
        rc, out, err = self.run_cmd_run()
        self.assertEqual(rc, 0, err)
        self.assertIn("--model omniroute/muse", out.replace("'", ""))
        self.assertIn("reviewer: opencode omniroute/muse (family meta, author qwen)", out)

    def test_the_run_prints_the_record_entry_the_lane_record_needs(self):
        # REVROUTE (S2) item 5: readiness is read off the record, so the spawn
        # that did the review has to hand over the line that goes in it --
        # otherwise the gate asks for something nobody writes.
        rc, out, err = self.run_cmd_run()
        self.assertEqual(rc, 0, err)
        self.assertIn("record-line: AutoOS-Review: kind=cross-family "
                      "author=qwen reviewer=omniroute/muse", out)

    def test_a_reviewer_model_that_opencode_json_does_not_declare_is_refused(self):
        # REVROUTE (S2) item 4: the paid Muse reviewer must be DECLARED as a
        # spawnable heading. Until it is, the run fails loudly instead of
        # quietly reviewing on the resolver's generic route.
        cfg = {"providers": {"omniroute": {"models": {"r-free": {}, "r-cheap": {}}}}}
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            rc = self.agent.cmd_run(self.args(), cfg)
        self.assertEqual(rc, 2, err.getvalue())
        self.assertIn("not declared in opencode.jsonc providers", err.getvalue())

    def test_an_explicit_model_wins_over_the_reviewer_list(self):
        route = self.route(model="omniroute/r-cheap")
        self.assertEqual(route["model"], "omniroute/r-cheap")
        self.assertNotEqual(route["combo"], "muse")
        self.assertIn("an explicit --model wins", route["reviewer_note"])

    def test_a_free_run_keeps_its_promo_model_and_says_the_reviewer_it_skipped(self):
        route = self.route(free=True, free_model="opencode/muse-spark-1.3-contributor-free")
        self.assertIsNone(route["model"], "--free carries no gateway model")
        self.assertIn("--free keeps its promo model", route["reviewer_note"])
        self.assertIn("opencode omniroute/muse", route["reviewer_note"])

    def test_a_reviewer_on_another_client_is_announced_never_faked(self):
        # Muse's provider is down, so the list picks Gemini -- which is not this
        # run's client. The run must not pretend it is that review.
        self.registry["providers"]["muse_api"].update({
            "available": False, "unavailable_until": self.DOWN_LATER})
        route = self.route()
        self.assertEqual(route["review_plan"]["reviewer"]["client"], "gemini")
        self.assertTrue(str(route["model"]).startswith("omniroute/r-"), route["model"])
        self.assertIn("policy.reviewers wants --client gemini", route["reviewer_note"])
        self.assertIn("it is NOT the review that list picked", self.run_cmd_run()[1])

    def test_a_review_plan_is_carried_through_the_route_cli_explain_output(self):
        # route --explain must show the skipped reviewers, not only the run's own
        # route scoring (brief item 2: "route --explain shows the skipped ones").
        result = self.agent.route_plan_for(
            "kind=review,author=meta,paths=tools/registry.py", "review this", str(ROOT),
            "orch", datetime.datetime(2026, 9, 29, 9, 0, tzinfo=datetime.timezone.utc),
            self.registry, {}, [], dict(self.client_state))
        explain = "\n".join(result["explain"])
        self.assertIn("reviewer skipped:", explain)
        self.assertIn("same family as author (meta)", explain)
        self.assertIsNotNone(result["review"])

    # --- gated, not started --------------------------------------------------

    def test_an_unauthored_review_card_is_not_gated(self):
        rc, out, err = self.run_cmd_run(card="kind=review,paths=tools/registry.py")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(self.route(card="kind=review,paths=tools/registry.py")["review_plan"])

    def test_a_non_review_card_with_an_author_is_not_gated(self):
        rc, out, err = self.run_cmd_run(card="kind=research,author=qwen,paths=tools/registry.py")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(
            self.route(card="kind=research,author=qwen,paths=tools/registry.py")["review_plan"])

    def everyone_down(self):
        """Every reviewer is unreachable *with a date* (brief item 2's "queue
        instead of skip when all are rate-limited"): Muse and Gemini through
        their providers, Haiku and Qwen through their own client outage."""
        self.registry["providers"]["muse_api"].update({
            "available": False, "unavailable_until": self.DOWN_LATER})
        self.registry["providers"]["gem_api"].update({
            "available": False, "unavailable_until": self.DOWN})
        self.registry["clients"]["qoder"] = {
            "id": "qoder", "available": False, "unavailable_until": self.DOWN_LATER}
        self.registry["clients"]["claude"] = {
            "id": "claude", "available": False, "unavailable_until": self.DOWN_LATER}

    def test_a_queued_review_refuses_with_exit_9_and_names_the_earliest_return(self):
        self.everyone_down()
        rc, out, err = self.run_cmd_run()
        self.assertEqual(rc, self.agent.EXIT_FREE_QUEUE_TIMEOUT, out + err)
        self.assertEqual(self.agent.EXIT_FREE_QUEUE_TIMEOUT, 9)
        self.assertIn("reviewer queued until %s" % self.DOWN, err)
        self.assertEqual(out, "", "a queued run prints nothing it did not decide")

    def test_a_queue_waits_for_the_first_reviewer_back_not_the_last(self):
        self.everyone_down()
        review = self.route()["review_plan"]
        self.assertEqual(review["state"], "queued")
        # Gemini's window is the shortest one, so the queue is over then -- even
        # though the author's own family (qoder) is also "down": that entry can
        # never review this card, waiting on it would be waiting forever.
        self.assertEqual(review["retry_at"], self.DOWN)
        self.assertIn("earliest", review["reason"])

    def test_an_unresolved_review_refuses_with_exit_2_and_never_a_retry_code(self):
        # Only a meta reviewer exists, and the author is meta: no wait fixes it.
        self.registry["policy"]["reviewers"] = self.registry["policy"]["reviewers"][:1]
        rc, out, err = self.run_cmd_run(card="kind=review,author=meta,paths=tools/registry.py")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("no reviewer is eligible for an author from family meta", err)

    def test_a_signed_out_reviewer_is_a_refusal_that_names_the_sign_in(self):
        # A dated outage is a wait; a missing sign-in is a human action. rc 9 here
        # would loop forever and never say what to do, so the refusal is exit 2
        # with the skipped reviewer's own reason on stderr. (Gemini is the
        # reviewer here, not the run's own client, so the route plan is
        # unaffected and only the reviewer walk can fail.)
        reviewers = self.registry["policy"]["reviewers"]
        self.registry["policy"]["reviewers"] = reviewers[1:3]
        self.client_state["gemini"] = {"installed": True, "signed_in": False, "reason": ""}
        rc, out, err = self.run_cmd_run()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("client: gemini not signed in", err)

    def test_the_gate_fires_before_any_clone_or_client_start(self):
        agent = self.agent
        self.registry["providers"]["muse_api"].update({"available": False,
                                                       "unavailable_until": self.DOWN})
        self.registry["providers"]["gem_api"].update({"available": False,
                                                      "unavailable_until": self.DOWN})
        self.client_state["qoder"] = {"installed": False}
        self.client_state["claude"] = {"installed": False}
        calls = []
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"AUTOOS_STATE_DIR": self.tmp,
                                          "AUTOOS_WORKERS_DIR": self.tmp}, clear=True):
            with mock.patch.object(agent, "run_client", lambda *a, **k: calls.append(1)):
                with mock.patch.object(agent.clients, "signin_state",
                                       lambda client, env=None: (None, "")):
                    with mock.patch("shutil.which", return_value="/usr/bin/opencode"):
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                            rc = agent.cmd_run(self.args(dry_run=False, isolate=True),
                                                self.cfg())
        self.assertEqual(rc, 9, out.getvalue() + err.getvalue())
        self.assertEqual(calls, [], "the client never started")
        self.assertEqual(os.listdir(os.path.join(self.tmp, "sandboxes"))
                         if os.path.isdir(os.path.join(self.tmp, "sandboxes")) else [], [],
                         "no clone was made for a run that cannot review")


class ProviderResetStateTests(unittest.TestCase):
    """REVROUTE (S2) item 3: a provider stop that states its own reset time
    ("Individual quota reached ... resets in ~83h", "cooling down (reset after
    51s)") is recorded as an ``unavailable_until`` in the spawner's own state,
    and the resolver reads it -- the provider is skipped until then instead of
    being handed the next task (measured: agy RESOURCE_EXHAUSTED 429 83h,
    L1-backlog 2026-09-27T23:22:13Z; gemini cooling 19:3xZ).

    Nothing here edits ``catalog/ai-registry.json``: the registry is the
    operator's, this file is the machine's transient observation, git-ignored
    beside ``measured.json`` and ``track-record.jsonl``.
    """

    NOW = datetime.datetime(2026, 9, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)
    AGY_STOP = ("AGY_ERROR: 429 RESOURCE_EXHAUSTED: Individual quota reached. "
                "Quota resets in ~83h")
    COOLING_STOP = "Error: 429 cooling down (reset after 51s)"
    NO_RESET_STOP = "Error: 429 Rate limit exceeded"

    def setUp(self):
        self.agent = load_agent()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state_path = os.path.join(self.tmp, "provider-state.json")
        self.registry = _reviewer_registry()

    def state(self):
        with io.open(self.state_path, encoding="utf-8") as fh:
            return json.load(fh)

    # --- reading the reset out of the client's own line ----------------------

    def test_resets_in_hours_is_that_many_seconds(self):
        self.assertEqual(self.agent.parse_reset(self.AGY_STOP), 83 * 3600)

    def test_reset_after_seconds_is_that_many_seconds(self):
        self.assertEqual(self.agent.parse_reset(self.COOLING_STOP), 51)

    def test_a_reset_spelled_out_in_minutes_is_minutes(self):
        self.assertEqual(
            self.agent.parse_reset("Error: 429 quota reached, try again in 15 minutes"),
            15 * 60)

    def test_a_day_sized_reset_is_days(self):
        self.assertEqual(self.agent.parse_reset("Error: 429 quota resets in ~2d"),
                         2 * 86400)

    def test_a_stop_that_names_no_reset_parses_to_none(self):
        self.assertIsNone(self.agent.parse_reset(self.NO_RESET_STOP))
        self.assertIsNone(self.agent.parse_reset(""))

    # --- which provider stopped ---------------------------------------------

    def test_a_provider_named_in_the_line_is_the_one_recorded(self):
        self.registry["providers"]["sambanova"] = {"id": "sambanova"}
        self.assertEqual(
            self.agent.stop_provider_id(
                "Error: No active credentials for provider: sambanova. Quota resets in ~5m",
                self.registry, ["muse_api/muse-contrib"]),
            "sambanova")

    def test_a_provider_named_by_its_gateway_alias_is_the_same_provider(self):
        # The gateway spells a provider its own way (providers.<id>.omniroute_id);
        # an error line quoting that spelling is the same provider, not a new one.
        self.registry["providers"]["sambanova"] = {"id": "sambanova"}
        self.registry["providers"]["sambanova"]["omniroute_id"] = "samba"
        self.assertEqual(
            self.agent.stop_provider_id(
                "Error: no active credentials for provider: samba, resets in ~5m",
                self.registry, ["muse_api/muse-contrib"]),
            "sambanova")

    def test_an_unnamed_stop_is_attributed_to_the_first_servable_leg(self):
        # The gateway works down a route's legs in order, so the one that took
        # the traffic is the first that is up right now.
        self.assertEqual(
            self.agent.stop_provider_id(self.AGY_STOP, self.registry,
                                        ["muse_api/muse-contrib", "free-p/free-model"]),
            "muse_api")
        self.registry["providers"]["muse_api"] = {"id": "muse_api", "available": False}
        self.assertEqual(
            self.agent.stop_provider_id(self.AGY_STOP, self.registry,
                                        ["muse_api/muse-contrib", "free-p/free-model"]),
            "free-p")

    def test_a_stop_with_no_legs_to_choose_from_is_attributed_to_nothing(self):
        self.assertIsNone(self.agent.stop_provider_id(self.AGY_STOP, self.registry, []))

    # --- the state file ------------------------------------------------------

    def test_a_stop_with_a_reset_records_the_provider_unavailable_until_then(self):
        # r-cheap's only leg is cheap-p/cheap-model, and the stop line names no
        # provider, so the leg that took the traffic is what gets the window.
        recorded = self.agent.record_reset_stop(self.AGY_STOP, "r-cheap", self.registry,
                                                 now=self.NOW, path=self.state_path)
        self.assertEqual(recorded, ("cheap-p", "2026-10-01T23:00:00Z"),
                         "83h after 2026-09-28T12:00Z -- the window the client "
                         "itself stated, kept to the second it was told")
        entry = self.state()["providers"]["cheap-p"]
        self.assertEqual(entry["unavailable_until"], "2026-10-01T23:00:00Z")
        self.assertEqual(entry["combo"], "r-cheap")
        self.assertIn("resets in ~83h", entry["reason"])

    def test_a_stop_without_a_reset_records_nothing_and_creates_no_file(self):
        self.assertIsNone(self.agent.record_reset_stop(self.NO_RESET_STOP, "r-cheap",
                                                       self.registry, now=self.NOW,
                                                       path=self.state_path))
        self.assertFalse(os.path.exists(self.state_path))

    def test_an_implausible_reset_is_not_recorded(self):
        # A client printing "resets in ~400d" must not take a provider out of
        # rotation for a year; that is an operator edit to the registry, not a
        # spawner observation.
        self.assertIsNone(self.agent.record_reset_stop(
            "Error: 429 quota resets in ~400d", "r-cheap", self.registry,
            now=self.NOW, path=self.state_path))
        self.assertFalse(os.path.exists(self.state_path))

    def test_a_later_stop_for_the_same_provider_extends_its_window(self):
        self.agent.record_reset_stop(self.COOLING_STOP, "r-cheap", self.registry,
                                      now=self.NOW, path=self.state_path)
        self.agent.record_reset_stop(self.AGY_STOP, "r-cheap", self.registry,
                                      now=self.NOW, path=self.state_path)
        self.assertEqual(self.state()["providers"]["cheap-p"]["unavailable_until"],
                          "2026-10-01T23:00:00Z")

    def test_an_expired_record_is_pruned_when_the_next_stop_is_written(self):
        self.agent.record_reset_stop(self.COOLING_STOP, "r-cheap", self.registry,
                                      now=self.NOW, path=self.state_path)
        self.assertEqual(list(self.state()["providers"]), ["cheap-p"])
        later = self.NOW + datetime.timedelta(hours=1)
        self.agent.record_reset_stop(self.AGY_STOP, "r-cheap", self.registry,
                                      now=later, path=self.state_path)
        self.assertEqual(list(self.state()["providers"]), ["cheap-p"],
                         "the 51s window expired and went; the 83h one is there")
        self.assertEqual(self.state()["providers"]["cheap-p"]["unavailable_until"],
                          "2026-10-02T00:00:00Z")

    def test_a_missing_or_corrupt_state_file_is_not_a_failure(self):
        agent = self.agent
        self.assertEqual(agent.load_provider_state(os.path.join(self.tmp, "none.json")), {})
        with io.open(self.state_path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertEqual(agent.load_provider_state(self.state_path), {})

    # --- REVFIX S1: the file's SHAPE is not trusted -------------------------
    #
    # The state is written by this tool, but it lives in logs/ where a person
    # fixes things by hand and where an older or buggier build may already have
    # written something. A record of a transient outage is never worth failing a
    # run over: a shape the reader did not expect is ignored with one warning,
    # it must never raise into `route`/`run`/the reviewer walk.

    def capture(self, fn, *a, **kw):
        """fn(...)'s return value plus everything it wrote to stderr."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = fn(*a, **kw)
        return result, err.getvalue()

    def test_providers_spelled_as_a_list_is_ignored_with_a_warning(self):
        # The exact shape REVFIX S1 reproduced: [{"providers": [...]}] used to
        # raise AttributeError out of apply_provider_state and kill the run.
        merged, err = self.capture(
            self.agent.apply_provider_state, self.registry,
            {"providers": [{"muse_api": {"unavailable_until": "2026-09-29T00:00:00Z"}}]})
        self.assertNotIn("available", merged["providers"]["muse_api"],
                         "nothing was applied: %r" % (merged["providers"]["muse_api"],))
        self.assertEqual(merged["providers"], self.registry["providers"],
                         "an unreadable file changes no provider")
        self.assertIn("provider-state", err)

    def test_a_state_that_is_not_an_object_at_all_is_ignored_with_a_warning(self):
        for bad in ({"providers": "down"}, {"providers": 7}, {"providers": [1, 2]}):
            merged, err = self.capture(self.agent.apply_provider_state, self.registry, bad)
            self.assertNotIn("available", merged["providers"]["muse_api"], str(bad))
            self.assertIn("provider-state", err, str(bad))

    def test_an_entry_that_is_not_an_object_is_ignored_with_a_warning(self):
        merged, err = self.capture(
            self.agent.apply_provider_state, self.registry,
            {"providers": {"muse_api": "down",
                           "gem_api": {"unavailable_until": "2026-09-29T00:00:00Z"}}})
        self.assertNotIn("available", merged["providers"]["muse_api"],
                         "a string entry is not a record")
        self.assertIs(merged["providers"]["gem_api"]["available"], False,
                      "the well-shaped sibling is still applied")
        self.assertEqual(err.count("provider-state"), 1,
                         "one warning for the whole file, not one per row: %r" % err)

    def test_a_record_stop_over_a_malformed_state_file_rewrites_it_cleanly(self):
        # The writer cannot be poisoned by what it is reading either: the bad
        # rows go and the file that comes back is the shape the reader expects.
        with io.open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump({"providers": [{"muse_api": "the old wrong shape"}]}, fh)
        recorded, err = self.capture(
            self.agent.record_reset_stop, self.AGY_STOP, "r-cheap", self.registry,
            now=self.NOW, path=self.state_path)
        self.assertIn("provider-state", err)
        self.assertEqual(recorded[0], "cheap-p")
        providers = self.state()["providers"]
        self.assertIsInstance(providers, dict)
        self.assertEqual(list(providers), ["cheap-p"],
                          "the malformed row was dropped, not carried forward")

    # --- REVFIX S3: the read side honours the same cap the writer does ------

    def test_a_window_beyond_the_cap_is_ignored_on_read(self):
        # The write side refuses to RECORD a 400-day reset; a file that already
        # says one (an old build, a hand edit) must not bench a provider for a
        # year either -- 7 days is the most the spawner's word can cost.
        far = (self.NOW + datetime.timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        merged, err = self.capture(
            self.agent.apply_provider_state, self.registry,
            {"providers": {"muse_api": {"unavailable_until": far}}}, now=self.NOW)
        self.assertNotIn("available", merged["providers"]["muse_api"])
        self.assertIn("provider-state", err)
        self.assertIn("7", err, "the warning names the cap it applied")

    def test_an_unparsable_window_is_ignored_on_read(self):
        merged, err = self.capture(
            self.agent.apply_provider_state, self.registry,
            {"providers": {"muse_api": {"unavailable_until": "next tuesday"}}},
            now=self.NOW)
        self.assertNotIn("available", merged["providers"]["muse_api"])
        self.assertIn("provider-state", err)

    def test_a_window_inside_the_cap_is_still_applied(self):
        inside = (self.NOW + datetime.timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        merged, err = self.capture(
            self.agent.apply_provider_state, self.registry,
            {"providers": {"muse_api": {"unavailable_until": inside}}}, now=self.NOW)
        self.assertIs(merged["providers"]["muse_api"]["available"], False)
        self.assertEqual(merged["providers"]["muse_api"]["unavailable_until"], inside)
        self.assertEqual(err, "", "a well-shaped record is not worth a warning")

    # --- the resolver reads it ----------------------------------------------

    def test_apply_marks_the_provider_down_for_the_resolver(self):
        merged = self.agent.apply_provider_state(
            self.registry, {"providers": {"muse_api": {"unavailable_until":
                                                            "2026-10-01T23:00:00Z"}}},
            now=self.NOW)
        provider = merged["providers"]["muse_api"]
        self.assertIs(provider["available"], False)
        self.assertEqual(provider["unavailable_until"], "2026-10-01T23:00:00Z")
        self.assertIsNot(merged["providers"]["muse_api"], self.registry["providers"]["muse_api"],
                         "the loaded registry is copied, never mutated in place")

    def test_apply_never_shortens_an_operators_outage(self):
        self.registry["providers"]["muse_api"] = {
            "id": "muse_api", "tier": "paid", "trains_on_prompts": True,
            "available": False, "unavailable_until": "2099-06-01T00:00:00Z"}
        merged = self.agent.apply_provider_state(
            self.registry, {"providers": {"muse_api": {"unavailable_until":
                                                           "2026-10-01T17:00:00Z"}}},
            now=self.NOW)
        # The cap is on what the STATE file may say, not on what the operator
        # wrote: a 2099 date in the registry stays exactly as long as it reads.
        self.assertEqual(merged["providers"]["muse_api"]["unavailable_until"],
                          "2099-06-01T00:00:00Z")

    def test_apply_ignores_a_provider_that_is_not_in_the_registry(self):
        merged, err = self.capture(
            self.agent.apply_provider_state, self.registry,
            {"providers": {"no-such-p": {"unavailable_until": "2026-10-01T23:00:00Z"}}},
            now=self.NOW)
        self.assertNotIn("no-such-p", merged["providers"])
        self.assertEqual(err, "", "a well-shaped row for an unknown provider is not "
                                  "a malformed file")

    def test_a_window_that_passed_leaves_the_provider_servable_again(self):
        # R-gateway-12: the record comes back on its own, no hand-edit needed.
        merged = self.agent.apply_provider_state(
            self.registry, {"providers": {"muse_api": {"unavailable_until":
                                                            "2000-01-01T00:00:00Z"}}})
        review = resolver.reviewer_for("qwen", merged, _reviewer_client_state(), self.NOW)
        self.assertEqual(review["state"], "resolved")
        self.assertEqual(review["reviewer"]["family"], "meta")

    def test_two_recorded_stops_queue_the_review_instead_of_skipping_it(self):
        # Item 3 feeding item 2: both reviewer providers stopped with a stated
        # reset, so the review waits for the earliest one -- it is NOT run by a
        # same-family model and called independent. High risk, so the
        # first-pass-only fallback is out on its own rule and the queue is what
        # the two windows leave behind.
        state = {"providers": {
            "muse_api": {"unavailable_until": "2026-10-01T23:00:00Z"},
            "gem_api": {"unavailable_until": "2026-09-28T14:00:00Z"}}}
        merged = self.agent.apply_provider_state(self.registry, state)
        review = resolver.reviewer_for("qwen", merged, _reviewer_client_state(),
                                       self.NOW, risk="high")
        self.assertEqual(review["state"], "queued")
        self.assertEqual(review["retry_at"], "2026-09-28T14:00:00Z")
        self.assertTrue([s for s in review["skipped"] if s["waiting"]], review)

    def test_load_live_registry_reads_the_registry_and_the_state_together(self):
        reg_path = os.path.join(self.tmp, "registry.json")
        with io.open(reg_path, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh)
        self.agent.record_reset_stop(self.AGY_STOP, "r-cheap", self.registry,
                                      now=self.NOW, path=self.state_path)
        live = self.agent.load_live_registry(reg_path, state_path=self.state_path,
                                              now=self.NOW)
        self.assertEqual(live["providers"]["cheap-p"]["unavailable_until"],
                          "2026-10-01T23:00:00Z")
        self.assertIs(live["providers"]["cheap-p"]["available"], False)


class GatewayCooldownStopTests(unittest.TestCase):
    """R6STOP (2026-09-28): the OmniRoute gateway's per-credential cooldown --
    the stop a t2-worker run actually gets when google_ai_studio's free tier is
    spent -- was invisible to the REVROUTE recorder, measured in
    work/L1-routing/R6RES.out section 2:

    - ``PROVIDER_STOP_MARKERS`` matched nothing in
      ``"Error: [429] All credentials for model gemini-3.8-flash are cooling
      down (reset after 37s)"``, so the run was not a provider stop at all and
      the window was never read;
    - ``_RESET_RE`` had no ``retry in`` and no fractional number, so Google's
      own ``Please retry in 59.250991496s.`` parsed to None;
    - ``stop_provider_id`` attributed an unnamed stop by splitting the leg at
      ``/`` and looking that prefix up in ``providers``, which every
      ``omniroute_id``-spelled leg (``gemini/...``, 25 such prefixes in the
      real registry) fails -- so a recorded reset benched ``antigravity``, the
      next live leg, instead of the provider that actually served the model the
      line names.

    These run against the real ``catalog/ai-registry.json`` -- the wrongness is
    a property of the real leg spellings, and a fixture whose legs all used
    provider keys would test nothing (R-worker-04).
    """

    NOW = datetime.datetime(2026, 9, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)
    # Verbatim from the call log R6RES section 1 quotes (t2-worker-free-only,
    # 12 rows, 0 tokens), with the seconds R6RES section 2 tested against.
    GEMINI_COOLDOWN = ("Error: [429] All credentials for model gemini-3.8-flash "
                       "are cooling down (reset after 37s)")
    # Verbatim from the same log: the upstream (Google) line behind the
    # gateway's wrapper.
    GOOGLE_RETRY = ("Error: [429]: You exceeded your current quota. Quota "
                    "exceeded for metric: generate_content_free_tier_requests, "
                    "limit: 20, model: gemini-3.8-flash "
                    "Please retry in 59.250991496s.")

    def setUp(self):
        self.agent = load_agent()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state_path = os.path.join(self.tmp, "provider-state.json")
        self.registry = self.agent.load_registry(self.agent.REGISTRY_PATH)

    def state(self):
        with io.open(self.state_path, encoding="utf-8") as fh:
            return json.load(fh)

    # --- 1: the cooldown line IS a provider stop -----------------------------

    def test_the_gateway_cooldown_line_is_a_provider_stop(self):
        self.assertEqual(
            self.agent.provider_stop("working\n" + self.GEMINI_COOLDOWN + "\n"),
            self.GEMINI_COOLDOWN,
            "a cooled credential cut the run off; not reading it means the next "
            "task is handed to the same provider")

    def test_the_gateway_wrapper_of_the_upstream_retry_is_a_stop(self):
        # What the client actually prints: the gateway's own cooldown line
        # wrapping Google's window.
        line = ("Error: [429] All credentials for model gemini-3.8-flash are "
                "cooling down. Please retry in 59.250991496s.")
        self.assertEqual(self.agent.provider_stop("working\n" + line + "\n"), line)
        self.assertEqual(self.agent.parse_reset(line), 59)

    def test_a_line_that_is_not_a_stop_is_still_not_a_stop(self):
        # The marker must not become so broad that ordinary output matches.
        for line in ("Done. Wrote the cooldown handler.",
                     "Error: the tests failed",
                     'print("all credentials are cooling down")'):
            with self.subTest(line=line):
                self.assertIsNone(self.agent.provider_stop("working\n" + line + "\n"))

    # --- 2: the window the cooldown states is the window it gets -------------

    def test_a_retry_in_window_with_a_fraction_is_whole_seconds(self):
        self.assertEqual(self.agent.parse_reset(self.GOOGLE_RETRY), 59,
                         "'59.250991496s' is 59 seconds; the run was told 60")

    def test_a_retry_in_window_in_seconds_is_seconds(self):
        self.assertEqual(
            self.agent.parse_reset("Error: 429 Please retry in 30s."), 30)

    def test_a_rate_limit_line_with_no_window_still_parses_to_none(self):
        self.assertIsNone(
            self.agent.parse_reset("Error: [429]: Rate limit exceeded"))

    def test_the_reset_windows_the_spawner_already_read_are_unchanged(self):
        for text, want in (
                ("AGY_ERROR: 429 RESOURCE_EXHAUSTED: Individual quota reached. "
                 "Quota resets in ~83h", 83 * 3600),
                ("Error: 429 cooling down (reset after 51s)", 51),
                ("Error: 429 quota reached, try again in 15 minutes", 15 * 60),
                ("Error: 429 quota resets in ~2d", 2 * 86400),
                ("Error: 429 retry after 45 seconds", 45),
                (self.GEMINI_COOLDOWN, 37)):
            with self.subTest(text=text):
                self.assertEqual(self.agent.parse_reset(text), want)

    # --- 3: the provider benched is the provider that served ------------------

    def test_a_leg_spelled_with_a_gateway_alias_resolves_to_its_provider(self):
        # 'gemini' is google_ai_studio's omniroute_id and how the route declares
        # the leg; 'opencode-zen' is zen's. A raw prefix lookup finds neither.
        self.assertEqual(
            self.agent.stop_provider_id(
                "Error: 429 rate limit exceeded, resets in ~5m",
                self.registry, ["gemini/gemini-3.8-flash"]),
            "google_ai_studio")
        self.assertEqual(
            self.agent.stop_provider_id(
                "Error: 429 rate limit exceeded, resets in ~5m",
                self.registry, ["opencode-zen/deepseek-v4.1-flash"]),
            "zen")

    def test_a_t2_worker_gemini_cooldown_benches_google_ai_studio(self):
        # The measured wrong answer was antigravity (the next live leg of
        # t2-worker); mistral is what a t2-worker-clean stop benched.
        recorded = self.agent.record_reset_stop(
            self.GEMINI_COOLDOWN, "t2-worker", self.registry,
            now=self.NOW, path=self.state_path)
        self.assertEqual(recorded, ("google_ai_studio", "2026-09-28T12:00:37Z"))
        self.assertEqual(list(self.state()["providers"]), ["google_ai_studio"],
                         "the cooldown benches the provider that served the model "
                         "the line names, and nobody else")
        self.assertNotIn("antigravity", self.state()["providers"])
        self.assertNotIn("mistral", self.state()["providers"])

    def test_a_t2_worker_free_only_gemini_cooldown_benches_google_ai_studio(self):
        self.assertEqual(
            self.agent.record_reset_stop(self.GOOGLE_RETRY, "t2-worker-free-only",
                                         self.registry, now=self.NOW,
                                         path=self.state_path),
            ("google_ai_studio", "2026-09-28T12:00:59Z"))
        self.assertEqual(list(self.state()["providers"]), ["google_ai_studio"])

    def test_a_model_named_by_a_leg_that_does_not_serve_it_benches_nothing_wrong(self):
        # t2-worker-clean has no gemini leg; naming a model the route does not
        # serve falls back to the first servable leg, as an unnamed stop does.
        # The fallback leg is registry data, so the expectation is read from it
        # rather than pinned to one provider's name (DSBACK 2026-09-28 moved it
        # from opencode-zen/… to deepseek/deepseek-flash).
        legs = self.registry["routes"]["t2-worker-clean"]["legs"]
        named = self.agent.stop_provider_id(
            "Error: [429] All credentials for model gemini-3.8-flash are "
            "cooling down (reset after 37s)", self.registry, legs)
        unnamed = self.agent.stop_provider_id(
            "Error: [429] All credentials are cooling down (reset after 37s)",
            self.registry, legs)
        self.assertEqual(named, "deepseek")
        self.assertEqual(named, unnamed,
                         "an unmatched model name must attribute exactly as an "
                         "unnamed stop does, to the route's first live leg")

    def test_a_cooldown_that_states_no_window_records_no_bench(self):
        # No window, no record: a permanent-looking bench on a guess is worse
        # than the retry the client will do itself.
        self.assertIsNone(self.agent.record_reset_stop(
            "Error: [429] All credentials for model gemini-3.8-flash are "
            "cooling down", "t2-worker", self.registry,
            now=self.NOW, path=self.state_path))
        self.assertFalse(os.path.exists(self.state_path))

    def test_the_recorded_cooldown_takes_the_provider_out_for_the_resolver(self):
        # The whole point of the recorder: the next `route`/`run` read merges
        # this file in and skips the leg.
        self.agent.record_reset_stop(self.GEMINI_COOLDOWN, "t2-worker",
                                     self.registry, now=self.NOW,
                                     path=self.state_path)
        merged = self.agent.apply_provider_state(
            self.registry, self.agent.load_provider_state(self.state_path),
            now=self.NOW + datetime.timedelta(seconds=1))
        cooled = merged["providers"]["google_ai_studio"]
        self.assertIs(cooled["available"], False)
        self.assertEqual(cooled["unavailable_until"], "2026-09-28T12:00:37Z")
        self.assertTrue(self.agent.unavailable_now(cooled,
                        self.NOW + datetime.timedelta(seconds=1)))
        expired = self.agent.apply_provider_state(
            self.registry, self.agent.load_provider_state(self.state_path),
            now=self.NOW + datetime.timedelta(seconds=60))
        self.assertFalse(
            self.agent.unavailable_now(expired["providers"]["google_ai_studio"],
                                       self.NOW + datetime.timedelta(seconds=60)),
            "a 37s cooldown self-heals when the 37s are up (registry.unavailable_now)")


class ReviewerSpawnabilityTests(unittest.TestCase):
    """REVROUTE (S2) item 4: the reviewer a card resolves to must be a model this
    host can actually START. A reviewer list that names an undeclared heading is
    a review that never happens, so this reads the real files -- the registry the
    operator edits and the opencode.jsonc `tools/sync-ide-models.py` renders --
    instead of a fixture that could disagree with both.

    Two spellings, two rules:
    - `omniroute/<route>` is the gateway's, and the gateway only serves a route
      the client config declares (the same check that refuses a typo'd combo).
    - `opencode/<model>` is opencode's own provider -- the identical shape a
      `--free` run already uses every day -- so the client resolves it and the
      gateway-declaration check has nothing to say about it.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def setUp(self):
        self.agent = load_agent()

    def real_cfg(self):
        return self.agent.load_jsonc(os.path.join(self.REPO, "opencode.jsonc"))

    def real_reviewers(self):
        with io.open(os.path.join(self.REPO, "catalog", "ai-registry.json"),
                     encoding="utf-8") as fh:
            return json.load(fh)["policy"]["reviewers"]

    def test_the_paid_muse_reviewer_heading_is_declared_in_the_repo_config(self):
        # The brief's requirement, on the files a real run reads: the default
        # reviewer is the paid Meta leg, reached as the gateway's own heading.
        entry = self.real_reviewers()[0]
        self.assertEqual((entry["client"], entry["model"], entry["family"],
                          entry["paid"]),
                         ("opencode", "omniroute/spark-1.3-contributor", "meta", True))
        cfg = self.real_cfg()
        self.assertIn(entry["model"], self.agent.declared_models(cfg),
                      "run tools/sync-ide-models.py: the paid Muse reviewer must be "
                      "declared for opencode to start it")
        self.assertEqual(self.agent.resolve_model(cfg, 3, False, entry["model"]),
                         "omniroute/spark-1.3-contributor")

    def test_every_gateway_spelled_reviewer_is_declared(self):
        declared = self.agent.declared_models(self.real_cfg())
        missing = [e["model"] for e in self.real_reviewers()
                   if e["client"] == "opencode"
                   and e["model"].partition("#")[0].startswith("omniroute/")
                   and e["model"] not in declared]
        self.assertEqual(missing, [],
                         "policy.reviewers names a gateway route opencode.jsonc does "
                         "not declare: the run would be refused before it started")

    def test_a_reviewer_on_the_clients_own_provider_is_not_gateway_validated(self):
        # opencode-zen's free models (the same strings --free runs) reach the
        # child as the model, not as a combo the gateway has to serve.
        client = argparse.Namespace(name="opencode", gateway=True)
        review = {"state": "resolved",
                  "reviewer": {"client": "opencode", "model": "opencode/deepseek-v4.1-flash"}}
        model, combo, note = self.agent.reviewer_run_override(
            review, client, self.real_cfg(), 3, "omniroute/t3-driver", None, False)
        self.assertEqual(model, "opencode/deepseek-v4.1-flash")
        self.assertIsNone(combo, "no gateway combo to rename: the route stands")
        self.assertEqual(note, "reviewer-model: opencode opencode/deepseek-v4.1-flash")


CROSS_FAMILY_LINE = ("AutoOS-Review: kind=cross-family author=qwen3.8-flash "
                     "reviewer=omniroute/muse verdict=PASS")
FINAL_LINE = "AutoOS-Review: kind=final reviewer=sonnet verdict=READY"


class ReviewStatusTests(unittest.TestCase):
    """REVROUTE (S2) item 5: a lane is not ready because the orchestrator says so.

    A lane record must carry two review entries before it can be called ready --
    one cross-family review (a reviewer whose model FAMILY differs from the
    author's, the rule items 1-2 made data) and the Sonnet final check (the
    operator's unchanged decision). This reads the record, not a person's
    summary of it, and says which of the two is missing and why.
    """

    def setUp(self):
        self.agent = load_agent()
        self.registry = _reviewer_registry()
        # The command reads a registry file, so the fixture has to be one: the
        # alternative is pointing the command at the real catalog and having it
        # fail on a fixture spelling for a reason the test is not about.
        fd, self.registry_path = tempfile.mkstemp(suffix=".json")
        self.addCleanup(os.unlink, self.registry_path)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh)

    def status(self, text):
        return self.agent.review_status(text, self.registry)

    def cmd(self, path):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.agent.cmd_review_status(
                argparse.Namespace(record=path, registry=self.registry_path))
        return rc, out.getvalue(), err.getvalue()

    def write_record(self, *lines):
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    # --- what counts -------------------------------------------------------

    def test_a_record_with_both_entries_is_ready(self):
        # Prose around the entries is the norm: a record is a markdown report,
        # and an entry may sit in a bullet or under a heading.
        report = self.status("# Lane x\n\n- %s\n\nSome prose.\n\n%s\n"
                             % (CROSS_FAMILY_LINE, FINAL_LINE))
        self.assertTrue(report["ready"], report)
        self.assertTrue(report["cross_family"]["ok"])
        self.assertTrue(report["final"]["ok"])

    def test_a_missing_final_entry_is_reported_and_blocks_ready(self):
        report = self.status(CROSS_FAMILY_LINE)
        self.assertFalse(report["ready"])
        self.assertTrue(report["cross_family"]["ok"])
        self.assertFalse(report["final"]["ok"])
        self.assertIn("sonnet", report["final"]["detail"].lower())

    def test_a_final_entry_that_merely_contains_sonnet_is_not_a_signoff(self):
        # REVGATE2 (HIGH): the match was a substring, so reviewer=notsonnet — a
        # model that is not the final checker — signed the lane off.
        for spelling in ("notsonnet", "sonnet-ish", "mysonnet2"):
            with self.subTest(reviewer=spelling):
                report = self.status("AutoOS-Review: kind=final reviewer=%s "
                                     "verdict=READY" % spelling)
                self.assertFalse(report["ready"], report)
                self.assertFalse(report["final"]["ok"])

    def test_a_final_entry_naming_sonnet_or_a_sonnet_model_is_a_signoff(self):
        # The NAME, case-insensitive, or the vendor's full model id — not a
        # substring of either.
        for spelling in ("Sonnet", "sonnet", "claude-sonnet-5", "claude-sonnet-4-6"):
            with self.subTest(reviewer=spelling):
                report = self.status(CROSS_FAMILY_LINE + "\nAutoOS-Review: "
                                     "kind=final reviewer=%s verdict=READY" % spelling)
                self.assertTrue(report["ready"], report)

    def test_the_final_match_ignores_surrounding_space(self):
        # A record written by hand can pad the value; padding is not a
        # different model. Fed straight to _final_review because the line
        # parser splits on whitespace and can never carry it.
        for spelling in (" Sonnet", "sonnet ", "\tCLAUDE-SONNET-5\t"):
            with self.subTest(reviewer=spelling):
                entry = {"kind": "final", "reviewer": spelling, "verdict": "READY"}
                self.assertTrue(self.agent._final_review([entry])["ok"])

    def test_a_missing_cross_family_entry_is_reported_and_blocks_ready(self):
        report = self.status(FINAL_LINE)
        self.assertFalse(report["ready"])
        self.assertFalse(report["cross_family"]["ok"])
        self.assertIn("kind=cross-family", report["cross_family"]["detail"])

    def test_a_same_family_reviewer_is_not_a_cross_family_review(self):
        # The author reviewing its own family is the exact case this gate exists
        # for, even with a PASS on the line.
        report = self.status("AutoOS-Review: kind=cross-family author=gem-flash "
                             "reviewer=gem-flash verdict=PASS\n" + FINAL_LINE)
        self.assertFalse(report["ready"])
        self.assertIn("same family", report["cross_family"]["detail"])

    def test_an_unknown_reviewer_is_not_guessed_into_a_family(self):
        # A REVIEWER must be known: an invented spelling would otherwise differ
        # from every author family and read as an independent review that never
        # happened. (REVFIX S2: an unknown AUTHOR is refused for the same reason
        # — see test_an_unknown_author_fails_the_cross_family_check.)
        report = self.status("AutoOS-Review: kind=cross-family author=qwen "
                             "reviewer=not-a-model-anywhere verdict=PASS\n" + FINAL_LINE)
        self.assertFalse(report["ready"])
        self.assertIn("not-a-model-anywhere", report["cross_family"]["detail"])

    # --- REVFIX S2: the family comparison is a comparison, not a string match -

    def test_an_author_spelled_with_the_vendors_capitalization_is_the_same_family(self):
        # Meta markets its model as "Meta Muse"; a record quoting that spelling
        # used to compare unequal to the registry's "meta" and pass as an
        # independent review of itself.
        report = self.status("AutoOS-Review: kind=cross-family author=Meta "
                             "reviewer=omniroute/muse verdict=PASS\n" + FINAL_LINE)
        self.assertFalse(report["ready"])
        self.assertIn("same family", report["cross_family"]["detail"])

    def test_a_family_capitalized_on_the_registry_side_is_the_same_family(self):
        # Normalizing both sides, not just the author's: an operator who writes
        # "Meta" in policy.reviewers means the family the models call "meta".
        for entry in self.registry["policy"]["reviewers"]:
            if entry["model"] == "omniroute/muse":
                entry["family"] = "Meta"
        report = self.status("AutoOS-Review: kind=cross-family author=muse-contrib "
                             "reviewer=omniroute/muse verdict=PASS\n" + FINAL_LINE)
        self.assertFalse(report["ready"])
        self.assertIn("same family", report["cross_family"]["detail"])

    def test_an_unknown_author_fails_the_cross_family_check(self):
        # REVFIX S2: author_family used to hand back an unresolved name as if it
        # were a family, so ANY typo ("qwen3.8-flsh", a model nobody registered)
        # differed from every reviewer and the lane went ready. Not knowing who
        # wrote the diff is not proof of independence.
        report = self.status("AutoOS-Review: kind=cross-family author=who-knows "
                             "reviewer=omniroute/muse verdict=PASS\n" + FINAL_LINE)
        self.assertFalse(report["ready"])
        self.assertFalse(report["cross_family"]["ok"])
        self.assertIn("who-knows", report["cross_family"]["detail"])

    def test_an_author_spelled_as_a_reviewer_model_uses_that_family(self):
        # The record quotes what the client was run with, not a registry id:
        # "omniroute/spark-1.3-contributor" is the operator's reviewer spelling
        # whose family the registry states.
        report = self.status("AutoOS-Review: kind=cross-family "
                             "author=omniroute/muse reviewer=gem-flash verdict=PASS\n"
                             + FINAL_LINE)
        self.assertTrue(report["ready"], report)
        self.assertEqual(report["cross_family"]["family"], "google")

    def test_a_bare_family_name_the_registry_knows_is_still_a_family(self):
        # Fail-closed on the unknown must not break the ordinary shorthand:
        # "qwen" is a family the fixture's reviewers declare, so it resolves.
        report = self.status("AutoOS-Review: kind=cross-family author=qwen "
                             "reviewer=omniroute/muse verdict=PASS\n" + FINAL_LINE)
        self.assertTrue(report["ready"], report)

    def test_a_non_ready_verdict_names_itself_rather_than_reading_missing(self):
        # "Reviewed, said FIX-FIRST" and "never reviewed" need different next
        # actions; collapsing them to "missing" would hide an open finding.
        report = self.status("AutoOS-Review: kind=cross-family author=qwen3.8-flash "
                             "reviewer=omniroute/muse verdict=FIX-FIRST\n"
                             "AutoOS-Review: kind=final reviewer=sonnet verdict=FIX-FIRST")
        self.assertFalse(report["ready"])
        self.assertIn("FIX-FIRST", report["cross_family"]["detail"])
        self.assertIn("FIX-FIRST", report["final"]["detail"])

    def test_an_entry_missing_a_reviewer_is_listed_as_malformed(self):
        report = self.status("AutoOS-Review: kind=cross-family author=qwen\n" + FINAL_LINE)
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["malformed"]), 1)

    def test_a_record_with_no_entries_says_so_with_the_line_format(self):
        report = self.status("# Lane x\n\nSTATUS: DONE. Gate green, shipped it.\n")
        self.assertFalse(report["ready"])
        self.assertEqual(report["entries"], 0)
        # The hint is the whole usability of the gate: nobody reads the source.
        self.assertIn("AutoOS-Review: kind=", report["hint"])

    # --- the command's contract -------------------------------------------

    def test_the_command_exits_0_on_a_ready_record(self):
        path = self.write_record("# Lane x", CROSS_FAMILY_LINE, FINAL_LINE)
        rc, out, _ = self.cmd(path)
        self.assertEqual(rc, 0)
        self.assertIn("ready", out)

    def test_the_command_exits_1_and_names_the_missing_review(self):
        path = self.write_record("# Lane x", FINAL_LINE)
        rc, out, _ = self.cmd(path)
        self.assertEqual(rc, 1)
        self.assertIn("cross-family", out)

    def test_an_unreadable_record_exits_2_not_1(self):
        # rc 1 is "not ready yet"; rc 2 is "the gate could not run" — a caller
        # that treats 1 as "wait" must not wait forever on a typo'd path.
        rc, _out, err = self.cmd(os.path.join(tempfile.gettempdir(), "no-such-lane-record.md"))
        self.assertEqual(rc, 2)
        self.assertIn("no-such-lane-record.md", err)

    # --- reality, not the fixture -----------------------------------------

    def test_the_real_registry_resolves_the_paid_reviewer_and_haiku(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(repo, "catalog", "ai-registry.json"),
                     encoding="utf-8") as fh:
            real = json.load(fh)
        report = self.agent.review_status(
            "AutoOS-Review: kind=cross-family author=claude-opus-4-6 "
            "reviewer=omniroute/spark-1.3-contributor verdict=PASS\n"
            "AutoOS-Review: kind=final reviewer=sonnet verdict=READY", real)
        self.assertTrue(report["ready"], report)
        self.assertEqual(report["cross_family"]["family"], "meta")
        # Haiku is the fallback first pass: it counts as a cross-family reviewer
        # for a non-anthropic author, and the same anthropic family as Sonnet's
        # final check — which is why the two entries are different requirements.
        self.assertEqual(self.agent.reviewer_family("haiku", real), "anthropic")

    def test_the_real_registry_resolves_a_gateway_spelling_and_a_vendors_case(self):
        # REVFIX S2 measured against the real catalog, not a fixture: the
        # operator's reviewer spelling resolves through the registry to the
        # family it declares, and the vendor's capitalization of that family is
        # the same family.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(repo, "catalog", "ai-registry.json"),
                     encoding="utf-8") as fh:
            real = json.load(fh)
        self.assertEqual(self.agent.resolver.author_family(
            "omniroute/spark-1.3-contributor", real)[0], "meta")
        self.assertEqual(self.agent.resolver.author_family("Meta", real)[0], "meta")
        report = self.agent.review_status(
            "AutoOS-Review: kind=cross-family author=Meta "
            "reviewer=gemini-3.8-flash verdict=PASS\n"
            "AutoOS-Review: kind=final reviewer=sonnet verdict=READY", real)
        self.assertTrue(report["ready"], report)
        # The self-review that used to pass: Meta's own model, Meta's reviewer.
        self.assertFalse(self.agent.review_status(
            "AutoOS-Review: kind=cross-family author=Meta "
            "reviewer=omniroute/spark-1.3-contributor verdict=PASS\n"
            "AutoOS-Review: kind=final reviewer=sonnet verdict=READY", real)["ready"])
        # An author the registry cannot place is not an independent review.
        self.assertFalse(self.agent.review_status(
            "AutoOS-Review: kind=cross-family author=gpt-next-week "
            "reviewer=omniroute/spark-1.3-contributor verdict=PASS\n"
            "AutoOS-Review: kind=final reviewer=sonnet verdict=READY", real)["ready"])


class ReadyCommandTests(unittest.TestCase):
    """REVGATE (S2, rule -> code): the `ready` step is code, not memory.

    Until now an orchestrator appended `ready <branch> <sha>` to autoos-L1-main's
    inbox by hand, after recalling that the record had both reviews and that the
    sha was pushed — and L1-main refused one that lacked reviews (inbox
    00:31:52Z). `ready` makes the claim itself, and only when the two facts that
    justify it hold: `review_status` says the record carries both reviews, and
    `origin/<branch>` actually points at the sha.

    Real temp git repos (a bare `origin` plus a clone, as the --isolate
    containment tests use) and the real parser / main entry: this is a CLI
    contract, so a test that called cmd_ready directly could pass a command nobody
    can type.
    """

    BRANCH = "lane/work"

    def setUp(self):
        self.agent = load_agent()
        self.registry = _reviewer_registry()
        fd, self.registry_path = tempfile.mkstemp(suffix=".json")
        self.addCleanup(os.unlink, self.registry_path)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh)

    def write_record(self, *lines):
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def make_inbox(self, content=None):
        """An inbox file (or, for content=None, a path that does not exist yet)."""
        path = os.path.join(tempfile.mkdtemp(), "L1.md")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        if content is not None:
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        return path

    def read_inbox(self, path):
        if not os.path.exists(path):
            return None
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()

    def make_repo(self, push=True):
        """A bare `origin` plus a clone with one commit on BRANCH.

        Returns (repo_dir, sha). With push=False the branch exists only locally,
        which is exactly the state `ready` must refuse as "not pushed".
        """
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        origin = os.path.join(base, "origin.git")
        repo = os.path.join(base, "work")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "init.defaultBranch=master"]
        subprocess.run(git + ["init", "-q", "--bare", origin], check=True)
        subprocess.run(git + ["clone", "-q", origin, repo], check=True,
                       stderr=subprocess.DEVNULL)
        with open(os.path.join(repo, "tracked.txt"), "w", encoding="utf-8") as fh:
            fh.write("lane work\n")
        subprocess.run(git + ["-C", repo, "add", "tracked.txt"], check=True)
        subprocess.run(git + ["-C", repo, "commit", "-q", "-m", "lane work"], check=True)
        subprocess.run(git + ["-C", repo, "switch", "-q", "-c", self.BRANCH], check=True)
        sha = subprocess.run(git + ["-C", repo, "rev-parse", "HEAD"],
                             check=True, capture_output=True,
                             text=True).stdout.strip()
        if push:
            subprocess.run(git + ["-C", repo, "push", "-q", "origin",
                                  "%s:%s" % (self.BRANCH, self.BRANCH)], check=True)
        return repo, sha

    def ready(self, record, repo, sha, inbox, extra=()):
        """Run the real CLI. `repo=None` means let it default to the cwd."""
        argv = ["ready", record, "--branch", self.BRANCH, "--sha", sha, "--inbox", inbox]
        if repo is not None:
            argv += ["--repo", repo]
        argv += ["--registry", self.registry_path, *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.agent.main(argv)
        return rc, out.getvalue(), err.getvalue()

    READY_RECORD = (CROSS_FAMILY_LINE, FINAL_LINE)
    NOT_PUSHED_SHA = "0" * 40

    # --- the review gate ----------------------------------------------------

    def test_an_unreviewed_record_is_refused_and_nothing_is_appended(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("2026-09-28T00:00:00Z handoff lane\n")
        rc, out, _ = self.ready(
            self.write_record("# Lane x", "STATUS: DONE. Gate green, shipped it."),
            repo, sha, inbox)
        self.assertEqual(rc, 1)
        self.assertIn("kind=cross-family", out)
        self.assertIn("ready: no", out)
        self.assertEqual(self.read_inbox(inbox), "2026-09-28T00:00:00Z handoff lane\n")

    def test_a_same_family_reviewer_is_refused(self):
        # The case the inbox refused at 00:31:52Z: a record that LOOKS reviewed.
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        rc, out, _ = self.ready(
            self.write_record(
                "AutoOS-Review: kind=cross-family author=muse-contrib "
                "reviewer=omniroute/muse verdict=PASS", FINAL_LINE),
            repo, sha, inbox)
        self.assertEqual(rc, 1)
        self.assertIn("same family", out)
        self.assertEqual(self.read_inbox(inbox), "")

    def test_a_final_reviewer_that_only_contains_sonnet_is_refused(self):
        # REVGATE2 (HIGH): the substring match let "notsonnet" carry the final
        # sign-off, so `ready` appended the line for a lane nobody signed off.
        for spelling in ("notsonnet", "sonnet-ish", "mysonnet2"):
            with self.subTest(reviewer=spelling):
                repo, sha = self.make_repo()
                inbox = self.make_inbox("2026-09-28T00:00:00Z handoff lane\n")
                rc, out, _ = self.ready(
                    self.write_record(CROSS_FAMILY_LINE,
                                      "AutoOS-Review: kind=final reviewer=%s "
                                      "verdict=READY" % spelling),
                    repo, sha, inbox)
                self.assertEqual(rc, 1)
                self.assertIn("not sonnet", out)
                self.assertEqual(self.read_inbox(inbox),
                                 "2026-09-28T00:00:00Z handoff lane\n")

    def test_a_final_reviewer_naming_sonnet_appends_the_line(self):
        for spelling in ("Sonnet", "claude-sonnet-5", "claude-sonnet-4-6"):
            with self.subTest(reviewer=spelling):
                repo, sha = self.make_repo()
                inbox = self.make_inbox("")
                rc, out, _ = self.ready(
                    self.write_record(CROSS_FAMILY_LINE,
                                      "AutoOS-Review: kind=final reviewer=%s "
                                      "verdict=READY" % spelling),
                    repo, sha, inbox)
                self.assertEqual(rc, 0, out)
                self.assertEqual(len(self.read_inbox(inbox).splitlines()), 1)

    def test_the_review_gate_is_checked_before_the_sha(self):
        # A record that never got its review is not "unpushed work waiting on a
        # push": the caller has to know WHICH gate it hit, so the review report
        # prints and the sha is never reached.
        repo, _sha = self.make_repo()
        inbox = self.make_inbox("")
        rc, out, _err = self.ready(
            self.write_record("STATUS: DONE."), repo, self.NOT_PUSHED_SHA, inbox)
        self.assertEqual(rc, 1)
        self.assertIn("kind=cross-family", out)
        self.assertNotIn("not pushed", out)

    # --- the pushed-sha gate ------------------------------------------------

    def test_a_sha_that_is_not_the_tip_of_origin_is_refused(self):
        repo, _sha = self.make_repo()
        inbox = self.make_inbox("")
        rc, out, _ = self.ready(self.write_record(*self.READY_RECORD),
                                repo, self.NOT_PUSHED_SHA, inbox)
        self.assertEqual(rc, 1)
        self.assertIn("not pushed", out)
        self.assertEqual(self.read_inbox(inbox), "")

    def test_a_branch_absent_from_origin_is_refused_as_not_pushed(self):
        repo, sha = self.make_repo(push=False)
        inbox = self.make_inbox("")
        rc, out, _ = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox)
        self.assertEqual(rc, 1)
        self.assertIn("not pushed", out)
        self.assertIn(self.BRANCH, out)
        self.assertEqual(self.read_inbox(inbox), "")

    def test_a_git_failure_exits_2_not_1(self):
        # rc 1 means "go do the work"; rc 2 means "the gate could not run". A repo
        # with no `origin` remote is the second, and a caller that waits on 1 must
        # not wait forever on a misconfigured checkout.
        bad_repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bad_repo, True)
        subprocess.run(["git", "-c", "init.defaultBranch=master", "init", "-q", bad_repo],
                       check=True)
        inbox = self.make_inbox("")
        rc, _out, err = self.ready(self.write_record(*self.READY_RECORD),
                                   bad_repo, self.NOT_PUSHED_SHA, inbox)
        self.assertEqual(rc, 2)
        self.assertIn("origin", err)
        self.assertEqual(self.read_inbox(inbox), "")

    # --- the append ---------------------------------------------------------

    def test_a_ready_lane_appends_exactly_one_line_to_the_inbox(self):
        repo, sha = self.make_repo()
        # No trailing newline on the existing line: an append must not join it to
        # ours, and must not rewrite it either.
        inbox = self.make_inbox("2026-09-28T00:00:00Z handoff lane")
        rc, out, _ = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox)
        self.assertEqual(rc, 0, out)
        text = self.read_inbox(inbox)
        lines = text.splitlines()
        self.assertEqual(len(lines), 2, text)
        self.assertEqual(lines[0], "2026-09-28T00:00:00Z handoff lane")
        match = re.match(
            r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ready %s %s reviews: "
            r"(.+) \| (.+)$" % (re.escape(self.BRANCH), sha), lines[1])
        self.assertIsNotNone(match, lines[1])
        self.assertEqual(match.group(2), "qwen3.8-flash reviewed by omniroute/muse (meta)")
        self.assertEqual(match.group(3), "sonnet verdict READY")
        self.assertIn(lines[1], out)

    def test_a_ready_lane_creates_a_missing_inbox(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox(None)
        rc, out, _ = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox)
        self.assertEqual(rc, 0, out)
        text = self.read_inbox(inbox)
        self.assertEqual(len(text.splitlines()), 1, text)
        self.assertIn(" ready %s %s reviews: " % (self.BRANCH, sha), text)

    def test_the_repo_defaults_to_the_cwd(self):
        # The orchestrator runs from its own lane checkout; --repo is the
        # exception, not the rule.
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        old_cwd = os.getcwd()
        os.chdir(repo)
        self.addCleanup(os.chdir, old_cwd)
        rc, out, _ = self.ready(self.write_record(*self.READY_RECORD), None, sha, inbox)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.read_inbox(inbox).splitlines()), 1)

    def test_dry_run_prints_the_line_and_appends_nothing(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("2026-09-28T00:00:00Z handoff lane\n")
        rc, out, _ = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox,
                                extra=["--dry-run"])
        self.assertEqual(rc, 0, out)
        self.assertIn(" ready %s %s reviews: " % (self.BRANCH, sha), out)
        self.assertEqual(self.read_inbox(inbox), "2026-09-28T00:00:00Z handoff lane\n")

    def test_dry_run_does_not_create_a_missing_inbox(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox(None)
        rc, out, _ = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox,
                                extra=["--dry-run"])
        self.assertEqual(rc, 0, out)
        self.assertIsNone(self.read_inbox(inbox))

    def test_an_unwritable_inbox_exits_2_not_1(self):
        # A typo'd inbox is not a lane awaiting its review either.
        repo, sha = self.make_repo()
        inbox = os.path.join(tempfile.mkdtemp(), "no-such-dir", "L1.md")
        self.addCleanup(shutil.rmtree, os.path.dirname(inbox), True)
        rc, _out, err = self.ready(self.write_record(*self.READY_RECORD), repo, sha, inbox)
        self.assertEqual(rc, 2)
        self.assertIn("L1.md", err)

    # --- the record ---------------------------------------------------------

    def test_an_unreadable_record_exits_2_not_1(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        rc, _out, err = self.ready(
            os.path.join(tempfile.gettempdir(), "no-such-lane-record.md"),
            repo, sha, inbox)
        self.assertEqual(rc, 2)
        self.assertIn("no-such-lane-record.md", err)
        self.assertEqual(self.read_inbox(inbox), "")

    def test_a_stdin_record_is_accepted_like_review_status(self):
        repo, sha = self.make_repo()
        inbox = self.make_inbox("")
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("\n".join(self.READY_RECORD) + "\n")
        try:
            rc, out, _ = self.ready("-", repo, sha, inbox)
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.read_inbox(inbox).splitlines()), 1)


class EffortRungPlumbingTests(unittest.TestCase):
    """DSBACK item 3: the effort rung the resolver picks (spec 5.5) must reach
    the client that can honour it — and nowhere it cannot.

    Measured at the DSBACK flip: deepseek-v4.1-flash answers again with the
    [none, low, high, max] ladder, but the rung only ever landed in the track
    record (track_entry stamps route["effort"]); build_plan handed the bare
    combo to `opencode run --model`, so a card the resolver scored at `max`
    sent the same request as one scored at `low`, and the low/high/max
    variants generated into opencode.jsonc were dead weight.

    The one client-side mechanism that exists is opencode's model variant:
    `--model omniroute/deepseek-v4.1-flash#high` selects the variant entry
    whose `settings.reasoningEffort` the OpenAI-compatible protocol sends as
    `reasoning_effort`. So `none` — and a non-reasoning leg's None — means NO
    `#variant` at all: the base model entry carries no reasoning settings
    (pinned in tests/test_sync_ide_models.py). An OmniRoute combo cannot carry
    a per-effort alias (pinned in tests/test_registry_render.py), so a gateway
    client takes the bare combo and the rung is not delivered there — said
    out loud rather than half-implemented.
    """

    RUNGS = ("low", "high", "max")
    COMBO = "deepseek-v4.1-flash"

    def setUp(self):
        self.agent = load_agent()
        self.cfg = self.agent.load_jsonc(str(ROOT / "opencode.jsonc"))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.agent.MEASURED_OVERLAY_PATH = os.path.join(tmp, "measured.json")
        self.agent.TRACK_RECORD = os.path.join(tmp, "track-record.jsonl")
        patch = mock.patch.object(self.agent.measure_mod, "client_state",
                                  lambda *a, **k: {})
        patch.start()
        self.addCleanup(patch.stop)

    def args(self, **overrides):
        ns = argparse.Namespace(
            tier=None, card="kind=review,paths=tools/registry.py", allow_training=False,
            client="opencode", joinable=False, max_depth=None, clean=False, model=None,
            free=False, free_model=self.agent.DEFAULT_FREE_MODEL, isolate=False, auto=True,
            lean=False, title=None, dry_run=True, task="x", no_defer=False)
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def plan_of_resolver(self, rung, combo=COMBO, **overrides):
        """route["model"] for a resolver plan whose scored effort is `rung`."""
        plan = {"route": combo, "state": "ready", "reason": "stub",
                "bucket": "S2", "effort": rung, "defer_until": None}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(plan)):
            return self.agent._resolve_route_v2(
                self.args(**overrides), {"kind": "review", "paths": "tools/registry.py"},
                self.cfg, None)

    def argv_model(self, rung, combo=COMBO, **overrides):
        """The `--model` value build_plan actually hands the client."""
        plan = {"route": combo, "state": "ready", "reason": "stub",
                "bucket": "S2", "effort": rung, "defer_until": None}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(plan)):
            built = self.agent.build_plan(self.args(**overrides), self.cfg)
        cmd = built["cmd"]
        return cmd[cmd.index("--model") + 1] if "--model" in cmd else None

    def variants_of(self, model):
        base, _, _ = model.partition("#")
        provider, _, mid = base.partition("/")
        return {v["id"]: v for v in ((self.cfg["providers"].get(provider, {})
                                        .get("models", {}).get(mid, {})
                                        or {}).get("variants") or [])}

    # --- one test per rung -------------------------------------------------

    def test_low_high_and_max_reach_deepseek_as_reasoning_effort(self):
        for rung in self.RUNGS:
            with self.subTest(rung=rung):
                self.assertEqual(self.argv_model(rung),
                                 "omniroute/%s#%s" % (self.COMBO, rung))
                # the variant the suffix selects carries exactly that rung,
                # and nothing else: settings.reasoningEffort -> reasoning_effort
                variant = self.variants_of("omniroute/" + self.COMBO)[rung]
                self.assertEqual(variant["settings"], {"reasoningEffort": rung})

    def test_a_none_rung_sends_no_reasoning_param_at_all(self):
        # "none" is a rung the resolver can return (deepseek's ladder carries
        # it; _clamp prefers the lower rung on a tie), and a non-reasoning leg
        # returns None. Both must emit a bare model: no #variant, so opencode
        # sends no reasoning_effort field.
        for rung in ("none", None):
            with self.subTest(rung=rung):
                self.assertEqual(self.argv_model(rung), "omniroute/" + self.COMBO)
                self.assertNotIn("#", self.argv_model(rung))
        self.assertNotIn("none", self.variants_of("omniroute/" + self.COMBO))

    def test_the_rung_is_also_kept_on_the_record(self):
        # stamping the argv must not cost the track record its rung
        route = self.plan_of_resolver("max")
        self.assertEqual(route["effort"], "max")
        self.assertEqual(self.agent.track_entry(
            {"client": "opencode", "route": route, "model": route["model"]}, 0, 1.0)["effort"],
            "max")

    # --- never invent a rung the answering config cannot honour -------------

    def test_a_model_without_the_rung_declared_gets_no_invented_variant(self):
        # t3-driver's served head leg has an empty ladder, so the render
        # declares no variants for it — a resolver rung must not bolt a #high
        # onto a model whose config has no such variant (PROVFIX3 finding 8 is
        # exactly this class of forwarded effort the leg rejects).
        self.assertEqual(self.plan_of_resolver("high", combo="t3-driver")["model"],
                         "omniroute/t3-driver")
        self.assertEqual(self.variants_of("omniroute/t3-driver"), {})

    def test_an_explicit_model_variant_wins_over_the_resolvers_rung(self):
        # --model omniroute/deepseek-v4.1-flash#low is an operator choice; the
        # resolver's rung describes the card, not the override. Two suffixes
        # would be an unresolvable model id.
        route = self._explicit_route("omniroute/%s#low" % self.COMBO, "high")
        self.assertEqual(route["model"], "omniroute/%s#low" % self.COMBO)

    def _explicit_route(self, model, rung):
        plan = {"route": self.COMBO, "state": "ready", "reason": "stub",
                "bucket": "S2", "effort": rung, "defer_until": None}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(plan)):
            return self.agent.resolve_route_unchecked(
                self.args(model=model), self.cfg,
                self.agent.clients.CLIENTS["opencode"])

    # --- the rung comes from the resolver, never from the spawner -----------

    def test_a_v1_card_plan_carries_no_rung_at_all(self):
        # the v1/--tier paths compute no bucket/effort (no resolver), so the
        # spawner must not fall back to some default effort of its own.
        ns = self.args(card="role=review")
        plan = self.agent.build_plan(ns, self.cfg)
        cmd = plan["cmd"]
        model = cmd[cmd.index("--model") + 1]
        self.assertEqual(model, "omniroute/t3-driver")
        self.assertNotIn("#", model)

    def test_a_gateway_client_takes_the_bare_combo(self):
        # an omniroute combo cannot carry a per-effort alias, so for the
        # gateway clients the rung is not deliverable — the argv must stay the
        # plain combo rather than a #high the gateway would read as part of
        # the combo name.
        cmd = self._gateway_plan("high")["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], self.COMBO)

    def _gateway_plan(self, rung):
        plan = {"route": self.COMBO, "state": "ready", "reason": "stub",
                "bucket": "S2", "effort": rung, "defer_until": None}
        with mock.patch.object(self.agent, "route_plan_for", lambda *a, **k: dict(plan)):
            return self.agent.build_plan(self.args(client="qwen"), self.cfg)


def _reviewer_client_state():
    return {name: {"installed": True, "signed_in": True, "reason": ""}
            for name in ("opencode", "gemini", "qoder", "claude")}


if __name__ == "__main__":
    unittest.main()
