"""MCP must preserve the CLI's implicit writer-policy selection."""
import sys
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
