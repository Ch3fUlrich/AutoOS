"""Tests for the agent spawner: tools/autoos_routing.py, tools/autoos_clients.py,
tools/autoos-agent.py and tools/autoos_agent_mcp.py.

Dry runs only - nothing is spawned, no gateway is called, no key is read.

Run from the repo root (optionally one class, e.g. RoutingTableTests):

    python3 tests/test_autoos_spawner.py [ClassName]
"""
import importlib.util
import json
import os
import subprocess
import sys
import shutil
import tempfile
import time
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
            self.assertEqual(names, {"list_clients", "spawn", "status", "result", "cancel"})
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


if __name__ == "__main__":
    unittest.main()
