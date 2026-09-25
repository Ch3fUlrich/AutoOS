"""``trust_worktree.py`` — what it writes into a worktree, and what it can be told not to.

The script pre-approves a fresh worktree so a background session is not blocked. It also writes an
``.env`` carrying ``OMNIGRAPH_GRAPH_ID``, which is right for a repo with no ``.env`` convention and
WRONG for one that resolves its ``.env`` back to the main checkout: measured 2026-09-11 in
downstream project A, a worktree ``.env`` holding only that one key pre-empts the main checkout's real
``containers/services/.env`` and every service degrades to the filesystem tier in silence.

Rule 12: pass and fire — the flag absent must write it, the flag present must not.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

import trust_worktree  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A fake main checkout, a fake worktree, and a ~/.claude.json the test owns."""
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"MCP_TIMEOUT": "120000"}}), encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    claude_json = tmp_path / "claude.json"
    claude_json.write_text(json.dumps({"projects": {str(repo): {"hasTrustDialogAccepted": True}}}),
                           encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_PATH", str(claude_json))
    return repo, worktree


def test_without_the_flag_it_writes_an_env_file(sandbox):
    repo, worktree = sandbox
    assert trust_worktree.main([str(worktree), "--repo", str(repo)]) == 0
    assert (worktree / ".env").is_file()
    assert "OMNIGRAPH_GRAPH_ID=repo" in (worktree / ".env").read_text(encoding="utf-8")


def test_no_env_leaves_the_worktree_without_one(sandbox):
    repo, worktree = sandbox
    assert trust_worktree.main([str(worktree), "--repo", str(repo), "--no-env"]) == 0
    assert not (worktree / ".env").exists(), (
        "a repo that resolves .env back to the main checkout must be able to refuse this file")


def test_no_env_still_does_the_job_it_exists_for(sandbox):
    repo, worktree = sandbox
    assert trust_worktree.main([str(worktree), "--repo", str(repo), "--mcpjson", "omnigraph",
                                "--no-env"]) == 0
    local = json.loads((worktree / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert local["enableAllProjectMcpServers"] is True
    assert "omnigraph" in local["enabledMcpjsonServers"]
    assert local["env"]["MCP_TIMEOUT"] == "120000", "the copied settings must carry the timeout"
    trusted = json.loads(Path(os.environ["CLAUDE_CONFIG_PATH"]).read_text(encoding="utf-8"))
    assert trusted["projects"][str(worktree)]["hasTrustDialogAccepted"] is True


def test_the_graph_id_comes_from_the_repo_pin_not_the_folder_name(sandbox):
    """A checkout folder named ``AutoOS`` pins the graph ``autoos``.

    Deriving the id from the folder name wrote ``OMNIGRAPH_GRAPH_ID=AutoOS`` into every
    worktree ``.env``, and no such graph exists on the cluster — so a lane's memory landed
    somewhere the main checkout would never read. ``.mcp.json`` is the pin the repo already
    ships to every client, so it wins over the folder name.
    """
    repo, worktree = sandbox
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"omnigraph": {
        "env": {"OMNIGRAPH_GRAPH_ID": "autoos"}}}}), encoding="utf-8")
    assert trust_worktree.main([str(worktree), "--repo", str(repo)]) == 0
    text = (worktree / ".env").read_text(encoding="utf-8")
    assert "OMNIGRAPH_GRAPH_ID=autoos" in text
    assert "=repo" not in text, "the folder-name fallback must not win over the repo pin"


LANE_MCP = Path(".claude") / "lane-mcp.local.json"


def _write_mcp_json(worktree, servers):
    (worktree / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_lane_mcp_copies_project_servers_and_adds_a_private_serena(sandbox, capsys):
    """The config carries the worktree's own servers plus a Serena pinned to the worktree.

    Measured 2026-09-25: the shared Serena (``:9121``) holds one active project, so a
    worktree session asked it about the main checkout's files and got the main checkout's
    answers. The private ``--project`` argument is the whole point of this entry.
    """
    repo, worktree = sandbox
    _write_mcp_json(worktree, {"omnigraph": {"command": "omnigraph-mcp", "args": ["serve"]}})
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--no-env"]) == 0
    dst = worktree / LANE_MCP
    assert dst.is_file()
    data = json.loads(dst.read_text(encoding="utf-8"))
    servers = data["mcpServers"]
    assert servers["omnigraph"] == {"command": "omnigraph-mcp", "args": ["serve"]}, (
        "a project server must be copied unchanged")
    serena = servers["serena"]
    assert serena["command"] == "uvx"
    assert serena["args"][:4] == ["--from", "serena-agent==1.7.0", "serena",
                                  "start-mcp-server"]
    assert serena["args"][serena["args"].index("--project") + 1] == str(worktree.resolve())
    assert serena["args"][serena["args"].index("--context") + 1] == "claude-code"
    assert serena["args"][serena["args"].index("--open-web-dashboard") + 1] == "false"
    assert serena["args"][serena["args"].index("--enable-gui-log-window") + 1] == "false"
    assert str(dst) in capsys.readouterr().out


def test_lane_mcp_keeps_an_existing_serena_definition(sandbox):
    repo, worktree = sandbox
    pinned = {"command": "serena", "args": ["start-mcp-server", "--project", "/elsewhere"]}
    _write_mcp_json(worktree, {"serena": pinned, "omnigraph": {"command": "omnigraph-mcp"}})
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--no-env"]) == 0
    servers = json.loads((worktree / LANE_MCP).read_text(encoding="utf-8"))["mcpServers"]
    assert servers["serena"] == pinned, "one definition per name: the project's wins"
    assert servers["omnigraph"] == {"command": "omnigraph-mcp"}


def test_no_private_serena_omits_it(sandbox):
    repo, worktree = sandbox
    _write_mcp_json(worktree, {"omnigraph": {"command": "omnigraph-mcp"}})
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--no-private-serena", "--no-env"]) == 0
    servers = json.loads((worktree / LANE_MCP).read_text(encoding="utf-8"))["mcpServers"]
    assert "serena" not in servers
    assert servers["omnigraph"] == {"command": "omnigraph-mcp"}


def test_lane_mcp_second_run_reports_unchanged(sandbox, capsys):
    repo, worktree = sandbox
    _write_mcp_json(worktree, {"omnigraph": {"command": "omnigraph-mcp"}})
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--no-env"]) == 0
    before = (worktree / LANE_MCP).read_text(encoding="utf-8")
    capsys.readouterr()
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--no-env"]) == 0
    out = capsys.readouterr().out
    assert "unchanged:" in out and str(worktree / LANE_MCP) in out
    assert (worktree / LANE_MCP).read_text(encoding="utf-8") == before


def test_lane_mcp_check_writes_nothing(sandbox, capsys):
    repo, worktree = sandbox
    _write_mcp_json(worktree, {"omnigraph": {"command": "omnigraph-mcp"}})
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--check", "--no-env"]) == 0
    out = capsys.readouterr().out
    assert not (worktree / LANE_MCP).exists()
    assert "WOULD write" in out and str(worktree / LANE_MCP) in out


def test_lane_mcp_without_mcp_json_has_only_serena(sandbox):
    repo, worktree = sandbox
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--no-env"]) == 0
    servers = json.loads((worktree / LANE_MCP).read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"serena"}
