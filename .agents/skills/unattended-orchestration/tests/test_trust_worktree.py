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
from trust_worktree import load, save


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


def test_lane_mcp_refuses_a_malformed_mcp_json(sandbox, capsys):
    # Review 2026-09-25: a swallowed parse error wrote a strict config without
    # the project's servers, and the unchanged-bytes check kept it that way.
    repo, worktree = sandbox
    (worktree / ".mcp.json").write_text("{not json", encoding="utf-8")
    assert trust_worktree.main([str(worktree), "--repo", str(repo),
                                "--lane-mcp", "--no-env"]) == 2
    assert not (worktree / LANE_MCP).exists()
    assert ".mcp.json" in capsys.readouterr().err


def test_lane_mcp_check_reports_unchanged_when_current(sandbox, capsys):
    repo, worktree = sandbox
    args = [str(worktree), "--repo", str(repo), "--lane-mcp", "--no-env"]
    assert trust_worktree.main(args) == 0
    capsys.readouterr()
    assert trust_worktree.main(args + ["--check"]) == 0
    assert "unchanged:" in capsys.readouterr().out


def test_trust_windows_style_creates_both_keys(sandbox, tmp_path, monkeypatch):
    repo, _ = sandbox
    # Use a Windows-style worktree path with backslashes
    worktree = tmp_path / "wt"
    worktree.mkdir()
    changed, msg = trust_worktree.trust(worktree, repo, mcpjson=[], force=False, check=False)
    assert changed is True
    claude_json = Path(os.environ["CLAUDE_CONFIG_PATH"])
    data = json.loads(claude_json.read_text())
    projects = data.get("projects", {})
    key1 = str(worktree)
    key2 = key1.replace("\\", "/")
    assert key1 in projects
    assert key2 in projects
    assert projects[key1].get("hasTrustDialogAccepted") is True
    assert projects[key2].get("hasTrustDialogAccepted") is True


def test_trust_preexisting_forward_slash_flipped_to_true(sandbox, tmp_path, monkeypatch):
    repo, _ = sandbox
    worktree = tmp_path / "wt"
    worktree.mkdir()
    key_forward = str(worktree).replace("\\", "/")
    claude_json = Path(os.environ["CLAUDE_CONFIG_PATH"])
    data = json.loads(claude_json.read_text())
    projects = data.setdefault("projects", {})
    # Pre-existing forward-slash entry with hasTrustDialogAccepted=False and other field
    projects[key_forward] = {"hasTrustDialogAccepted": False, "foo": "bar"}
    # Ensure repo entry exists for template (set by sandbox fixture)
    claude_json.write_text(json.dumps(data, indent=2))
    changed, msg = trust_worktree.trust(worktree, repo, mcpjson=[], force=False, check=False)
    assert changed is True
    data = json.loads(claude_json.read_text())
    projects = data.get("projects", {})
    entry_forward = projects[key_forward]
    assert entry_forward.get("hasTrustDialogAccepted") is True
    assert entry_forward.get("foo") == "bar"


def test_trust_path_without_backslashes_writes_one_key(sandbox, tmp_path):
    repo, _ = sandbox
    worktree = tmp_path / "normal_worktree"
    worktree.mkdir()
    changed, msg = trust_worktree.trust(worktree, repo, mcpjson=[], force=False, check=False)
    assert changed is True
    claude_json = Path(os.environ["CLAUDE_CONFIG_PATH"])
    data = json.loads(claude_json.read_text())
    projects = data.get("projects", {})
    key = str(worktree)
    assert key in projects
    assert projects[key].get("hasTrustDialogAccepted") is True


def test_trust_second_call_reports_already_trusted(sandbox, tmp_path, monkeypatch):
    repo, _ = sandbox
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # First trust
    changed1, msg1 = trust_worktree.trust(worktree, repo, mcpjson=[], force=False, check=False)
    assert changed1 is True
    claude_json = Path(os.environ["CLAUDE_CONFIG_PATH"])
    content1 = claude_json.read_text()
    # Second trust
    changed2, msg2 = trust_worktree.trust(worktree, repo, mcpjson=[], force=False, check=False)
    assert changed2 is False
    assert msg2.startswith("already trusted: ")
    content2 = claude_json.read_text()
    assert content1 == content2


def test_trust_both_keys_seeded_with_missing_mcpjson(sandbox, tmp_path, monkeypatch):
    """t1: both keys pre-seeded trusted (hasTrustDialogAccepted True, hasCompletedProjectOnboarding True),
    the backslash key (str(worktree)) with enabledMcpjsonServers ["X"], the forward-slash key with none;
    trust(..., mcpjson=["X"]) returns changed True and afterwards every key has "X" 
    (on POSIX the two keys are one key: then just assert "X" present)."""
    repo, _ = sandbox
    # Create a worktree directory that contains a backslash so that the two keys are different.
    worktree = tmp_path / "wt\\"   # note: this creates a directory named "wt\" (if the filesystem allows it)
    worktree.mkdir()
    key1 = str(worktree)   # e.g., "/tmp/abc/wt\\"
    key2 = str(worktree).replace("\\", "/")   # e.g., "/tmp/abc/wt//"

    # Pre-seed the config with both keys being trusted, but key1 has ["X"] and key2 has no enabledMcpjsonServers.
    claude_json = Path(os.environ["CLAUDE_CONFIG_PATH"])
    data = {}
    if claude_json.is_file():
        data = load(claude_json)
    else:
        # Create the config file if it doesn't exist? 
        # But note: the trust function expects the config file to exist? 
        # We are going to write to it via the trust function, but we are pre-seeding.
        # We'll create a minimal config.
        data = {"projects": {}}
    projects = data.setdefault("projects", {})
    projects[key1] = {
        "hasTrustDialogAccepted": True,
        "hasCompletedProjectOnboarding": True,
        "enabledMcpjsonServers": ["X"]
    }
    projects[key2] = {
        "hasTrustDialogAccepted": True,
        "hasCompletedProjectOnboarding": True
        # no enabledMcpjsonServers
    }
    save(claude_json, data)

    # Now call trust
    changed, msg = trust_worktree.trust(worktree, repo, mcpjson=["X"], force=False, check=False)
    assert changed is True
    # Reload the config to see the update
    data_after = load(claude_json)
    projects_after = data_after.get("projects", {})
    # Check key1
    assert key1 in projects_after
    entry1 = projects_after[key1]
    assert entry1.get("hasTrustDialogAccepted") is True
    assert entry1.get("hasCompletedProjectOnboarding") is True
    assert entry1.get("enabledMcpjsonServers") == ["X"]
    # Check key2
    assert key2 in projects_after
    entry2 = projects_after[key2]
    assert entry2.get("hasTrustDialogAccepted") is True
    assert entry2.get("hasCompletedProjectOnboarding") is True
    assert entry2.get("enabledMcpjsonServers") == ["X"]


def test_trust_empty_mcpjson_writes_no_key(sandbox, tmp_path, monkeypatch):
    """t2: trust(..., mcpjson=[]) on a fresh worktree writes no enabledMcpjsonServers key into the new entry."""
    repo, _ = sandbox
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # Ensure the config file exists? 
    claude_json = Path(os.environ["CLAUDE_CONFIG_PATH"])
    # If the config file doesn't exist, the trust function will return early? 
    # But we want to test writing a new entry. 
    # We'll create an empty config if needed? 
    # However, the trust function checks for the config file. 
    # Let's make sure it exists by touching it? 
    # But note: the trust function loads the config. 
    # We'll create a minimal config: {"projects": {}}
    if not claude_json.is_file():
        data = {"projects": {}}
        save(claude_json, data)
    else:
        data = load(claude_json)
        # We don't want to interfere with existing tests? 
        # But note: we are using tmp_path for the worktree, and the config file is shared? 
        # We are going to overwrite the entire config? 
        # Instead, we should only update the projects section? 
        # For simplicity, we'll load, then update the projects section, then save.
        # However, we are going to call trust which will load and save. 
        # We want to start with a clean config for the worktree? 
        # We'll remove any existing entry for our worktree keys? 
        key1 = str(worktree)
        key2 = str(worktree).replace("\\", "/")
        projects = data.setdefault("projects", {})
        # Remove the keys if they exist
        projects.pop(key1, None)
        projects.pop(key2, None)
        save(claude_json, data)

    # Now call trust with mcpjson=[]
    changed, msg = trust_worktree.trust(worktree, repo, mcpjson=[], force=False, check=False)
    assert changed is True   # because it's fresh

    # Check the config: the worktree key should be present and have the trust flags, but no enabledMcpjsonServers.
    data_after = load(claude_json)
    projects_after = data_after.get("projects", {})
    key = str(worktree)   # on Linux, key1 and key2 are the same? 
    # But note: we created worktree = tmp_path / "wt" -> no backslash, so key1==key2.
    assert key in projects_after
    entry = projects_after[key]
    assert entry.get("hasTrustDialogAccepted") is True
    assert entry.get("hasCompletedProjectOnboarding") is True
    # Check that enabledMcpjsonServers is not present
    assert "enabledMcpjsonServers" not in entry
