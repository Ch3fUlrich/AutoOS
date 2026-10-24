"""MCP must preserve the CLI's implicit writer-policy selection."""
import sys
import types
from unittest.mock import Mock
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autoos_agent_mcp as mcp


@pytest.fixture(autouse=True)
def spawn_environment(monkeypatch):
    monkeypatch.setattr(mcp.agent, "leaf_spawn_refusal", lambda: None)
    monkeypatch.setenv("AUTOOS_AGENT_LAYER", "L1")
    monkeypatch.setenv("AUTOOS_AGENT_DEPTH", "0")
    monkeypatch.setattr(mcp.agent, "pick_policy_writer", lambda: {
        "client": "qoder", "model": "qwen3.8-flash"})


def request(**kwargs):
    return dict(task="Fix a pure function", card={"role": "implement"},
                dry_run=True, **kwargs)


def test_implicit_writer_omits_client():
    argv, _ = mcp.build_argv(request())
    assert "--client" not in argv


def test_explicit_writer_passes_client():
    argv, _ = mcp.build_argv(request(client="opencode"))
    assert argv[argv.index("--client") + 1] == "opencode"


def test_policy_budget_and_family_use_picked_model():
    assert mcp.spawn_client(request()) == "qoder"
    assert mcp.planned_model_args(request())["model"] == "qwen3.8-flash"


@pytest.mark.parametrize("pin", [{"model": "example/model"},
                                  {"free_model": "example/model"},
                                  {"free": True}, {"tier": 2}])
def test_pinned_requests_keep_client(pin):
    req = request(**pin)
    if "tier" in pin:
        req.pop("card")
    argv, _ = mcp.build_argv(req)
    assert argv[argv.index("--client") + 1] == "opencode"


def test_review_keeps_opencode_and_lean():
    req = request()
    req["card"] = {"role": "review"}
    argv, _ = mcp.build_argv(req)
    assert argv[argv.index("--client") + 1] == "opencode"
    assert "--lean" in argv


def test_l2_still_refuses_claude_pin(monkeypatch):
    monkeypatch.setenv("AUTOOS_AGENT_LAYER", "L2")
    with pytest.raises(ValueError, match="(?i)claude"):
        mcp.build_argv(request(model="anthropic/claude-sonnet-4"))


def test_l2_checks_policy_client(monkeypatch):
    monkeypatch.setenv("AUTOOS_AGENT_LAYER", "L2")
    monkeypatch.setattr(mcp.agent, "pick_policy_writer", lambda: {
        "client": "claude", "model": "sonnet"})
    with pytest.raises(ValueError, match="(?i)claude"):
        mcp.build_argv(request())


@pytest.fixture
def registered_spawn(monkeypatch):
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
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", module)
    monkeypatch.setattr(mcp, "_reap", lambda: None)
    monkeypatch.setattr(mcp, "preflight", lambda *a: "test stop")
    monkeypatch.delenv("AUTOOS_AGENT_INBOX", raising=False)
    monkeypatch.setattr(mcp.agent, "claude_spawn_refusal", lambda *a, **k: (None, None))
    monkeypatch.setattr(mcp.agent, "host_admission_refusal", lambda **k: None)
    return mcp.build_server(profile=mcp.FULL_PROFILE).tools["spawn"]


@pytest.mark.parametrize("options,expected", [({}, None), ({"client": "opencode"}, "opencode"),
                                               ({"card": {"role": "review"}}, "opencode")])
def test_registered_spawn_client_boundary(registered_spawn, monkeypatch, options, expected):
    captured = []
    monkeypatch.setattr(mcp, "preflight", lambda argv, *a: captured.append(argv) or "test stop")
    args = dict(task="Fix a pure function", card={"role": "implement"}, dry_run=True)
    args.update(options)
    registered_spawn(**args)
    argv = captured[0]
    if expected is None:
        assert "--client" not in argv
    else:
        assert argv[argv.index("--client") + 1] == expected
    if options.get("card", {}).get("role") == "review":
        assert "--lean" in argv


def test_registered_l2_claude_pin_refused(registered_spawn, monkeypatch):
    monkeypatch.setenv("AUTOOS_AGENT_LAYER", "L2")
    result = registered_spawn(task="Fix a pure function", model="anthropic/claude-sonnet-4")
    assert result["state"] == "rejected"
    assert "claude" in str(result).lower()


def test_spawn_picks_policy_once(registered_spawn, monkeypatch):
    pick = Mock(return_value={"client": "qoder", "model": "qwen3.8-flash"})
    monkeypatch.setattr(mcp.agent, "pick_policy_writer", pick)
    monkeypatch.setattr(mcp, "preflight", lambda *a: "test stop")
    registered_spawn(task="Fix a pure function", card={"role": "implement"})
    assert pick.call_count == 1


def test_spawn_reuses_policy_for_family_and_budget(registered_spawn, monkeypatch):
    pick = Mock(return_value={"client": "qoder", "model": "qwen3.8-flash"})
    monkeypatch.setattr(mcp.agent, "pick_policy_writer", pick)
    budget = Mock(return_value=(None, None))
    monkeypatch.setattr(mcp.agent, "claude_spawn_refusal", budget)
    original = mcp.build_argv

    def build(req, run_id, cwd=None, policy=None):
        assert mcp.spawn_client(req, policy) == "qoder"
        assert mcp.planned_model_args(req, policy)["model"] == "qwen3.8-flash"
        mcp.planned_family_fields(req, "qoder", policy)
        return original(req, run_id, cwd=cwd, policy=policy)

    monkeypatch.setattr(mcp, "build_argv", build)
    registered_spawn(task="Fix a pure function", card={"role": "implement"})
    assert budget.call_args.args[0] == "qoder"
    assert budget.call_args.kwargs["model"] == "qwen3.8-flash"
    assert pick.call_count == 1


def test_spawn_policy_unavailable_is_structured(registered_spawn, monkeypatch):
    def unavailable():
        raise mcp.agent.PolicyWriterUnavailable("no policy writer")
    monkeypatch.setattr(mcp.agent, "pick_policy_writer", unavailable)
    result = registered_spawn(task="Fix a pure function", card={"role": "implement"})
    assert result == mcp._refused("no policy writer")
