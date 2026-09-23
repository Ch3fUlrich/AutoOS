import json
import sys
from pathlib import Path

import pytest

# setup_cao.py bootstraps the stack the skill then drives, so it lives in the
# adopting repo's infra/ rather than inside the skill. The skill is designed to
# be COPIED into another repo, where infra/ may not exist -- so this module
# skips rather than erroring, and says where it looked. An import error here
# would take the whole suite down in every repo that adopts the skill alone.
_CANDIDATES = [
    Path(__file__).resolve().parents[4] / "infra/mcp-servers/cao-setup",
    Path(__file__).resolve().parents[2] / "cao-setup",
]
for _candidate in _CANDIDATES:
    if (_candidate / "setup_cao.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

setup_cao = pytest.importorskip(
    "setup_cao",
    reason=(
        "setup_cao.py not found in "
        + " or ".join(str(c) for c in _CANDIDATES)
        + " -- expected when the skill has been copied into a repo without infra/"
    ),
)

BRIDGE_MARKER = setup_cao.BRIDGE_MARKER
bridge_present = setup_cao.bridge_present
detect_providers = setup_cao.detect_providers
graph_id_for = setup_cao.graph_id_for
needs_bridge = setup_cao.needs_bridge
omnigraph_entry = setup_cao.omnigraph_entry
routed_pools = setup_cao.routed_pools


def test_bridge_needed_for_windows_shared_config():
    assert needs_bridge("/mnt/c/Users/x/.gemini/config/mcp_config.json") is True


def test_bridge_is_a_noop_on_a_native_linux_path():
    """Spec defect 6: the bridge must disable itself on a Linux server."""
    assert needs_bridge("/home/you/.gemini/config/mcp_config.json") is False
    assert needs_bridge("/root/.gemini/config/mcp_config.json") is False


def test_bridge_needed_for_a_wsl_unc_path():
    assert needs_bridge(r"\\wsl$\Ubuntu\home\you\.gemini\config\mcp_config.json") is True


def test_graph_id_is_the_repo_folder_name_like_trust_worktree():
    assert graph_id_for("/home/you/code/agent-skills") == "agent-skills"
    assert graph_id_for("/mnt/c/Users/x/Documents/Code/agent-skills/") == "agent-skills"
    assert graph_id_for(r"C:\Users\x\Code\agent-skills") == "agent-skills"


def test_omnigraph_entry_pins_the_repo_graph_not_memory():
    """Spec defect 1 — the highest-severity finding of the audit."""
    entry = omnigraph_entry("/home/you/code/agent-skills")
    assert entry["env"]["OMNIGRAPH_GRAPH_ID"] == "agent-skills"
    assert entry["env"]["OMNIGRAPH_GRAPH_ID"] != "memory"


def test_omnigraph_entry_carries_no_secret_value():
    entry = omnigraph_entry("/home/you/code/agent-skills")
    assert "sk-" not in repr(entry)
    assert "OMNIGRAPH_TOKEN" not in repr(entry)


def test_bridge_present_detects_the_marker(tmp_path):
    src = tmp_path / "antigravity_cli.py"
    src.write_text(f"# {BRIDGE_MARKER}\nentry['command'] = 'wsl'\n", encoding="utf-8")
    assert bridge_present(src) is True


def test_bridge_present_is_false_after_an_upstream_overwrite(tmp_path):
    """`cao update` replaces the file; this is what makes that loud."""
    src = tmp_path / "antigravity_cli.py"
    src.write_text("# pristine upstream file\n", encoding="utf-8")
    assert bridge_present(src) is False


def test_bridge_present_is_false_for_a_missing_file(tmp_path):
    assert bridge_present(tmp_path / "gone.py") is False






def test_routed_pools_reads_only_pools_the_config_dispatches_to(tmp_path):
    cfg = tmp_path / "handoff.config.json"
    cfg.write_text(
        json.dumps(
            {
                "cao": {
                    "pools": {"anthropic": {}, "antigravity": {}, "deepseek": {}},
                    "routing": {
                        "implement": [
                            {"pool": "antigravity", "family": "google", "model": "m"}
                        ],
                        "review": "cross-family",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    assert routed_pools(cfg) == {"antigravity"}


def test_routed_pools_of_a_config_without_cao_is_empty(tmp_path):
    cfg = tmp_path / "handoff.config.json"
    cfg.write_text(json.dumps({"baseBranch": "main"}), encoding="utf-8")
    assert routed_pools(cfg) == set()


def test_a_windows_cao_on_path_is_not_a_usable_install(monkeypatch):
    """D16: pip installs CAO on Windows, but it cannot import fcntl/termios."""
    import setup_cao

    monkeypatch.setattr(setup_cao.sys, "platform", "win32")
    assert setup_cao.cao_runs_here() is False


def test_linux_runs_cao_natively(monkeypatch):
    import setup_cao

    monkeypatch.setattr(setup_cao.sys, "platform", "linux")
    assert setup_cao.cao_runs_here() is True


def test_windows_reaches_cao_through_wsl(monkeypatch):
    import setup_cao

    monkeypatch.setattr(setup_cao.sys, "platform", "win32")
    assert setup_cao.cao_invocation("Ubuntu")[:3] == ["wsl", "-d", "Ubuntu"]


def test_linux_needs_no_wsl_wrapper(monkeypatch):
    import setup_cao

    monkeypatch.setattr(setup_cao.sys, "platform", "linux")
    assert "wsl" not in setup_cao.cao_invocation()


def test_graph_id_is_platform_independent():
    """Caught by running the suite under WSL: green on Windows, red on Linux.

    Path is platform-flavoured -- on Linux a Windows-style path is one long
    filename, so .name returns the whole string and the graph id is garbage.
    """
    for path in (
        "/home/you/code/agent-skills",
        "/mnt/c/Users/x/Documents/Code/agent-skills/",
        r"C:\Users\x\Code\agent-skills",
        "C:/Users/x/Code/agent-skills",
        r"\\wsl$\Ubuntu\home\you\agent-skills",
    ):
        assert graph_id_for(path) == "agent-skills", path


def test_graph_id_of_a_bare_name_is_itself():
    assert graph_id_for("agent-skills") == "agent-skills"


# --------------------------------------------------------------------------
# Agent toolchain. Measured: a worker asked to verify its own review ran
# `python3 -m pytest` and got "No module named pytest". An agent that cannot
# run the suite reports confidence instead of evidence.
# --------------------------------------------------------------------------


class FakeRun:
    """Records commands and replays canned stdout."""

    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, command, timeout=900):
        self.commands.append(command)
        for needle, out in self.responses.items():
            if needle in command:
                return type("R", (), {"stdout": out, "stderr": "", "returncode": 0})()
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 1})()


def test_providers_are_detected_where_cao_runs_not_on_the_driving_host():
    """shutil.which on Windows probes the WINDOWS PATH — wrong in both directions."""
    import setup_cao

    run = FakeRun({"command -v claude": "FOUND\n", "command -v agy": "NO\n"})
    found = setup_cao.detect_providers(run=run)
    assert found["anthropic"] is True
    assert found["antigravity"] is False
    assert all("command -v" in c for c in run.commands)


def test_agent_interpreters_looks_beyond_the_login_shells_python3():
    """A CAO terminal's PATH is not the login shell's — measured on this host."""
    import setup_cao

    run = FakeRun({"which -a python3": "/usr/bin/python3\n/opt/conda/bin/python3\n"})
    interps = setup_cao.agent_interpreters(run=run)
    assert interps == ["/usr/bin/python3", "/opt/conda/bin/python3"]
    assert "miniconda3" in run.commands[0]


def test_interpreters_are_deduplicated():
    import setup_cao

    run = FakeRun({"which -a python3": "/usr/bin/python3\n/usr/bin/python3\n"})
    assert setup_cao.agent_interpreters(run=run) == ["/usr/bin/python3"]


def test_install_reports_only_the_missing_deps():
    import setup_cao

    run = FakeRun({
        "which -a python3": "/usr/bin/python3\n",
        "import pytest": "1\n",   # missing
        "import yaml": "0\n",     # present
        "command -v uv": "0\n",   # present
    })
    actions = setup_cao.install_python_deps(run=run, dry_run=True)
    assert any("pytest" in a for a in actions)
    assert not any("pyyaml" in a for a in actions)


def test_a_complete_toolchain_reports_nothing_to_do():
    import setup_cao

    run = FakeRun({
        "which -a python3": "/usr/bin/python3\n",
        "import pytest": "0\n",
        "import yaml": "0\n",
        "command -v uv": "0\n",
    })
    assert setup_cao.install_python_deps(run=run, dry_run=True) == []


def test_uv_is_installed_when_absent():
    import setup_cao

    run = FakeRun({
        "which -a python3": "/usr/bin/python3\n",
        "import pytest": "0\n",
        "import yaml": "0\n",
        "command -v uv": "1\n",
    })
    assert any("uv" in a for a in setup_cao.install_python_deps(run=run, dry_run=True))


def test_a_toolchain_probe_that_cannot_run_does_not_take_down_the_check():
    """The provider report next to it must survive a missing shell."""
    import setup_cao

    def boom(command, timeout=900):
        raise OSError("no wsl here")

    assert setup_cao.python_deps_status(run=boom) == {}


def test_opencode_permission_config_is_described_without_writing(tmp_path):
    import setup_cao

    msg = setup_cao.configure_opencode(dry_run=True)
    assert msg.startswith("WOULD")
    assert "opencode.json" in msg


def test_opencode_permissions_cover_the_measured_blocker():
    """The worker stalled on an external-directory write prompt."""
    import setup_cao

    assert setup_cao.OPENCODE_PERMISSIONS["edit"] == "allow"
    assert setup_cao.OPENCODE_PERMISSIONS["bash"] == "allow"


# --------------------------------------------------------------------------
# agy flavour. Measured 2026-09-11: the SAME agy TUI shows the signed-in
# account with cwd under /mnt/c and "not signed in" with cwd on ext4. The
# Windows binary cannot operate with a Linux-only working directory, which
# collides with this lane putting worktrees on ext4 for the 20x win.
# --------------------------------------------------------------------------


def test_a_native_binary_is_reported_as_native():
    run = FakeRun({"command -v agy": "FOUND\n", "file -b": "ELF 64-bit LSB pie executable\n"})
    assert setup_cao.agy_flavour(run=run) == "native"


def test_a_python_shim_is_reported_as_a_windows_shim():
    run = FakeRun({"command -v agy": "FOUND\n", "file -b": "Python script, ASCII text executable\n"})
    assert setup_cao.agy_flavour(run=run) == "windows-shim"


def test_a_missing_agy_is_absent():
    assert setup_cao.agy_flavour(run=FakeRun({})) == "absent"


def test_a_shim_is_warned_about_with_the_fix():
    run = FakeRun({"command -v agy": "FOUND\n", "file -b": "Python script\n"})
    warns = setup_cao.agy_warnings(run=run)
    assert len(warns) == 1
    assert "ext4" in warns[0]
    assert "install.sh" in warns[0]


def test_a_native_agy_produces_no_warning():
    run = FakeRun({"command -v agy": "FOUND\n", "file -b": "ELF 64-bit\n"})
    assert setup_cao.agy_warnings(run=run) == []


def test_an_absent_agy_is_not_warned_about_here():
    """detect_providers already reports it missing; one message is enough."""
    assert setup_cao.agy_warnings(run=FakeRun({})) == []
