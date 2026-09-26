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
import subprocess
import sys
import shutil
import tempfile
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


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AUTOOS_AGENT_") and k != "AUTOOS_OMNIROUTE_KEY"}
    env.update(extra)
    return env


def run_agent(*args, env=None):
    return subprocess.run([sys.executable, str(AGENT), *args], capture_output=True,
                          text=True, env=env or clean_env(), stdin=subprocess.DEVNULL)


class RoutingTableTests(unittest.TestCase):
    """ADR 0006 decision 3: one test per row of the resolution table."""

    def pick(self, **card):
        return routing.select_combo(card)[0]

    def test_empty_card_is_t2_worker(self):
        combo, reason = routing.select_combo({})
        self.assertEqual(combo, "t2-worker")
        self.assertTrue(reason)

    def test_public_1m_orchestrate_is_t1_orchestrator(self):
        self.assertEqual(self.pick(ctx="1m", role="orchestrate"), "t1-orchestrator")

    def test_public_1m_hard_is_t1_orchestrator(self):
        self.assertEqual(self.pick(ctx="1m", complexity="hard"), "t1-orchestrator")

    def test_public_1m_any_role_is_t1_orchestrator(self):
        self.assertEqual(self.pick(ctx="1m", role="review", spend="credit"), "t1-orchestrator")

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
        self.assertIn("--allow-training", msg)


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
        # t1-orchestrator carries 1m, a superset of 128k; the orchestrator is never demoted.
        self.assertEqual(routing.select_combo({"role": "orchestrate"})[0], "t1-orchestrator")

    def test_hard_review_is_t1_orchestrator_not_t3_driver(self):
        # orchestrate/hard wins over review/trivial: a hard review needs the strong model.
        self.assertEqual(routing.select_combo({"role": "review", "complexity": "hard"})[0], "t1-orchestrator")

    def test_sensitive_orchestrate_128k_is_t2_worker_clean(self):
        self.assertEqual(routing.select_combo({"privacy": "sensitive", "role": "orchestrate"})[0], "t2-worker-clean")

    def test_allow_training_opens_sensitive_1m_and_says_so(self):
        combo, reason = routing.select_combo({"privacy": "sensitive", "ctx": "1m"}, allow_training=True)
        self.assertEqual(combo, "t1-orchestrator-clean")
        self.assertIn("allow-training", reason)

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
        r = plan_of("--client", "agy", "t")
        self.assertIn("would run: agy -p t", r.stdout)
        self.assertNotIn("omniroute run", r.stdout)

    def test_qoder_is_refused_for_sensitive_work(self):
        r = plan_of("--client", "qoder", "--card", "privacy=sensitive", "t")
        self.assertEqual(r.returncode, 2)
        self.assertIn("public", r.stderr)

    def test_qoder_public_runs_print_mode(self):
        r = plan_of("--client", "qoder", "t")
        self.assertIn("would run: qodercli -p --permission-mode accept_edits t", r.stdout)

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
        self.assertIn("--allow-training", r.stderr)

    def test_tier_and_card_together_are_refused(self):
        self.assertEqual(plan_of("--tier", "2", "--card", "role=review", "t").returncode, 2)

    def test_clean_with_card_is_refused(self):
        self.assertEqual(plan_of("--clean", "--card", "role=review", "t").returncode, 2)

    def test_no_plan_prints_the_key(self):
        env = clean_env(AUTOOS_OMNIROUTE_KEY="never-print-this-key")
        for client in clients.CLIENTS:
            r = plan_of("--client", client, "t", env=env)
            self.assertNotIn("never-print-this-key", r.stdout + r.stderr, client)


class ReviewFindingTests(unittest.TestCase):
    """Cross-family review (t1-orchestrator, 2026-09-24) findings, pinned."""

    def test_joinable_keeps_an_explicit_model(self):
        r = plan_of("--client", "claude", "--joinable", "--title", "d1", "--model", "opus", "t")
        self.assertIn("--model opus", r.stdout)

    def test_gateway_client_model_override_wins_over_the_card(self):
        r = plan_of("--client", "qwen", "--card", "complexity=trivial", "--model", "omniroute/t1-orchestrator", "t")
        self.assertIn("omniroute run qwen --model t1-orchestrator ", r.stdout)

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
        self.assertEqual(self.calls(d), ["models", "-p t"])

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
            if st["state"] not in ("running", "starting"):
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
        self.assertEqual(st["state"], "done")
        text = mcp_server.result(out["id"])["text"]
        self.assertIn("would run: opencode run --standalone --agent t3-reviewer", text)
        self.assertIn("lean:", text)  # reviewers default to lean

    def test_state_lives_under_the_state_dir_agents(self):
        out = mcp_server.spawn({"task": "t", "cwd": str(ROOT)})
        self.assertEqual(out["dir"], os.path.join(self.tmp, "agents", out["id"]))
        self.wait_done(out["id"])
        for name in ("job.json", "output.log", "exit.json"):
            self.assertTrue(os.path.isfile(os.path.join(out["dir"], name)), name)

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
        self.assertEqual(mcp_server.cancel(out["id"])["state"], "done")


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
                                     "route", "list_agents", "context"})
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
            "r-with-unavailable-leg": {"legs": ["clean/big", "clean/small"],
                                       "unavailable_legs": {"clean/small": {}}},
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
        })

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


class RunCardV2AcceptanceTests(unittest.TestCase):
    """The exact command RUNV2's done-when names, against the real repo: real
    registry, real opencode.jsonc, real clients (opencode has no sign-in probe;
    the only client with one, agy, answers in ~1-2s per autoos_clients.py's own
    measurement)."""

    def test_run_card_kind_review_dry_run_prints_a_plan_on_the_real_repo(self):
        r = run_agent("run", "--card", "kind=review,paths=tools/registry.py",
                      "--dry-run", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("reason=resolver-v2", r.stdout)
        self.assertIn("would run:", r.stdout)


class RunCardV2PrivacyTests(unittest.TestCase):
    """RUNV2 brief step 4: a sensitive v2 card must never resolve to a non-
    "-clean" combo. Exercises the real registry + overlay end to end (client
    state is faked so it never shells out); skips itself when the resolver has
    no route at all for this card on this host's data, rather than asserting a
    route exists (that is the other lane's privacy filter to prove, not RUNV2's)."""

    def test_sensitive_v2_card_only_ever_yields_a_clean_combo_when_routed(self):
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
        self.assertTrue(result["route"].endswith("-clean"),
                        "sensitive card routed to %r, which is not a -clean combo"
                        % result["route"])


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

    def test_context_returns_a_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                out = mcp_server.context_info()
        self.assertEqual(out, {"context": "unknown", "reason": "no transcript"})

    def test_context_bad_transcript_path_is_an_error(self):
        out = mcp_server.context_info(
            transcript=os.path.join(tempfile.gettempdir(), "autoos-test-nope.jsonl"))
        self.assertEqual(set(out), {"error"})


if __name__ == "__main__":
    unittest.main()
