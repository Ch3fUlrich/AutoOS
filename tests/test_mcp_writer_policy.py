"""MCP must preserve the CLI's implicit writer-policy selection."""
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autoos_agent_mcp as mcp


def request(**kwargs):
    return dict(task="Fix a pure function", card={"role": "implement"},
                dry_run=True, **kwargs)


class WriterPolicyTests(unittest.TestCase):
    def start_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def setUp(self):
        self.start_patch(patch.object(mcp.agent, "leaf_spawn_refusal", lambda: None))
        self.start_patch(patch.dict(os.environ, {
            "AUTOOS_AGENT_LAYER": "L1", "AUTOOS_AGENT_DEPTH": "0"}))
        self.start_patch(patch.object(mcp.agent, "pick_policy_writer", lambda: {
            "client": "qoder", "model": "qwen3.8-flash"}))

    def registered_spawn(self):
        class App:
            def __init__(self, *args):
                self.tools = {}

            def tool(self, name):
                def register(fn):
                    self.tools[name] = fn
                    return fn
                return register

        module = types.ModuleType("mcp.server.fastmcp")
        module.FastMCP = App
        self.start_patch(patch.dict(sys.modules, {"mcp.server.fastmcp": module}))
        self.start_patch(patch.object(mcp, "_reap", lambda: None))
        self.start_patch(patch.object(mcp, "preflight", lambda *a: "test stop"))
        self.start_patch(patch.dict(os.environ))
        os.environ.pop("AUTOOS_AGENT_INBOX", None)
        self.start_patch(patch.object(mcp.agent, "claude_spawn_refusal", lambda *a, **k: (None, None)))
        self.start_patch(patch.object(mcp.agent, "host_admission_refusal", lambda **k: None))
        return mcp.build_server(profile=mcp.FULL_PROFILE).tools["spawn"]

    def test_implicit_writer_omits_client(self):
        argv, _ = mcp.build_argv(request())
        assert "--client" not in argv

    def test_explicit_writer_passes_client(self):
        argv, _ = mcp.build_argv(request(client="opencode"))
        assert argv[argv.index("--client") + 1] == "opencode"

    def test_policy_budget_and_family_use_picked_model(self):
        assert mcp.spawn_client(request()) == "qoder"
        assert mcp.planned_model_args(request())["model"] == "qwen3.8-flash"

    def check_pinned_requests_keep_client(self, pin):
        req = request(**pin)
        if "tier" in pin:
            req.pop("card")
        argv, _ = mcp.build_argv(req)
        assert argv[argv.index("--client") + 1] == "opencode"

    def test_review_keeps_opencode_and_lean(self):
        req = request()
        req["card"] = {"role": "review"}
        argv, _ = mcp.build_argv(req)
        assert argv[argv.index("--client") + 1] == "opencode"
        assert "--lean" in argv

    def test_l2_still_refuses_claude_pin(self):
        self.start_patch(patch.dict(os.environ, {"AUTOOS_AGENT_LAYER": "L2"}))
        with self.assertRaisesRegex(ValueError, "(?i)claude"):
            mcp.build_argv(request(model="anthropic/claude-sonnet-4"))

    def test_l2_checks_policy_client(self):
        self.start_patch(patch.dict(os.environ, {"AUTOOS_AGENT_LAYER": "L2"}))
        self.start_patch(patch.object(mcp.agent, "pick_policy_writer", lambda: {
            "client": "claude", "model": "sonnet"}))
        with self.assertRaisesRegex(ValueError, "(?i)claude"):
            mcp.build_argv(request())

    def check_registered_spawn_client_boundary(self, options, expected):
        spawn = self.registered_spawn()
        captured = []
        self.start_patch(patch.object(mcp, "preflight", lambda argv, *a: captured.append(argv) or "test stop"))
        args = dict(task="Fix a pure function", card={"role": "implement"}, dry_run=True)
        args.update(options)
        spawn(**args)
        argv = captured[0]
        if expected is None:
            assert "--client" not in argv
        else:
            assert argv[argv.index("--client") + 1] == expected
        if options.get("card", {}).get("role") == "review":
            assert "--lean" in argv

    def test_registered_l2_claude_pin_refused(self):
        spawn = self.registered_spawn()
        self.start_patch(patch.dict(os.environ, {"AUTOOS_AGENT_LAYER": "L2"}))
        result = spawn(task="Fix a pure function", model="anthropic/claude-sonnet-4")
        assert result["state"] == "rejected"
        assert "claude" in str(result).lower()

    def test_spawn_picks_policy_once(self):
        spawn = self.registered_spawn()
        pick = Mock(return_value={"client": "qoder", "model": "qwen3.8-flash"})
        self.start_patch(patch.object(mcp.agent, "pick_policy_writer", pick))
        self.start_patch(patch.object(mcp, "preflight", lambda *a: "test stop"))
        spawn(task="Fix a pure function", card={"role": "implement"})
        assert pick.call_count == 1

    def test_spawn_reuses_policy_for_family_and_budget(self):
        spawn = self.registered_spawn()
        pick = Mock(return_value={"client": "qoder", "model": "qwen3.8-flash"})
        self.start_patch(patch.object(mcp.agent, "pick_policy_writer", pick))
        budget = Mock(return_value=(None, None))
        self.start_patch(patch.object(mcp.agent, "claude_spawn_refusal", budget))
        original = mcp.build_argv

        def build(req, run_id, cwd=None, policy=None):
            assert mcp.spawn_client(req, policy) == "qoder"
            assert mcp.planned_model_args(req, policy)["model"] == "qwen3.8-flash"
            mcp.planned_family_fields(req, "qoder", policy)
            return original(req, run_id, cwd=cwd, policy=policy)

        self.start_patch(patch.object(mcp, "build_argv", build))
        spawn(task="Fix a pure function", card={"role": "implement"})
        assert budget.call_args.args[0] == "qoder"
        assert budget.call_args.kwargs["model"] == "qwen3.8-flash"
        assert pick.call_count == 1

    def test_spawn_policy_unavailable_is_structured(self):
        spawn = self.registered_spawn()

        def unavailable():
            raise mcp.agent.PolicyWriterUnavailable("no policy writer")
        self.start_patch(patch.object(mcp.agent, "pick_policy_writer", unavailable))
        result = spawn(task="Fix a pure function", card={"role": "implement"})
        assert result == mcp._refused("no policy writer")

    # Keep parameter cases independently counted by both stdlib and external runners.
    def test_pinned_requests_keep_client(self):
        with self.subTest(pin={"model": "example/model"}):
            self.check_pinned_requests_keep_client({"model": "example/model"})

    def test_pinned_requests_keep_client_free_model(self):
        with self.subTest(pin={"free_model": "example/model"}):
            self.check_pinned_requests_keep_client({"free_model": "example/model"})

    def test_pinned_requests_keep_client_free(self):
        with self.subTest(pin={"free": True}):
            self.check_pinned_requests_keep_client({"free": True})

    def test_pinned_requests_keep_client_tier(self):
        with self.subTest(pin={"tier": 2}):
            self.check_pinned_requests_keep_client({"tier": 2})

    def test_registered_spawn_client_boundary(self):
        with self.subTest(options={}):
            self.check_registered_spawn_client_boundary({}, None)

    def test_registered_spawn_client_boundary_explicit(self):
        with self.subTest(options={"client": "opencode"}):
            self.check_registered_spawn_client_boundary({"client": "opencode"}, "opencode")

    def test_registered_spawn_client_boundary_review(self):
        with self.subTest(options={"card": {"role": "review"}}):
            self.check_registered_spawn_client_boundary({"card": {"role": "review"}}, "opencode")


if __name__ == "__main__":
    unittest.main()
