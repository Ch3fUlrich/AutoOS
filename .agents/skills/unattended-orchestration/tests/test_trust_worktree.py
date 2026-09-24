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
