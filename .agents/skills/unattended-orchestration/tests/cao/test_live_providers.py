"""Live provider smoke tests — all three model families.

Skipped unless ``CAO_LIVE=1`` so the default suite stays hermetic and fast.
These hit real CLIs and cost real tokens.

The secret is read INSIDE the WSL command from the secrets file (``find_secrets``
order: ``$AUTOOS_SECRETS``, ``~/.config/autoos/api_keys.conf``, the repo) and never
crosses back through Python, so a captured stdout can never contain it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PureWindowsPath

import pytest

from cao.caoapi import CaoClient
from cao.config import find_secrets

live = pytest.mark.skipif(os.environ.get("CAO_LIVE") != "1", reason="set CAO_LIVE=1")

WSL = ["wsl", "-d", "Ubuntu", "-e", "bash", "-lc"]
# tests/cao/ -> tests/ -> the skill -> skills/ -> the repository root
REPO = Path(__file__).resolve().parents[4]


def _wsl_path(path: Path) -> str:
    """``C:\\x\\y`` -> ``/mnt/c/x/y``; a POSIX path is returned unchanged."""
    p = PureWindowsPath(path)
    if not p.drive:
        return path.as_posix()
    return f"/mnt/{p.drive[0].lower()}/" + "/".join(p.parts[1:])


# Same lookup order as everywhere else: $AUTOOS_SECRETS, the user scope, the repo.
SECRETS = _wsl_path(find_secrets(REPO))
LOAD_KEY = (
    f'export DEEPSEEK_API_KEY=$(grep "^deepseek=" "{SECRETS}" '
    "| cut -d= -f2-); "
)


def _run(cmd: str, timeout: int = 600):
    """Run in a LOGIN shell.

    `bash -lc`, not `bash -c`: measured, `agy` and `opencode` resolve only under
    a login shell. This is the exact %PATH% bug class the CAO integration
    started from, so the tests reproduce the working invocation rather than a
    convenient one.
    """
    return subprocess.run(WSL + [cmd], capture_output=True, text=True, timeout=timeout)


@live
def test_cao_server_reports_every_routed_provider():
    components = CaoClient().health()["components"]
    assert components["cao"] == "ok"
    assert components["claude"] == "ok", (
        "claude unavailable — CAO cannot spawn Claude workers"
    )


@live
def test_gemini_worker_answers():
    r = _run("agy -p 'Reply with exactly: GEMINI_OK' --model gemini-3.8-flash-low")
    assert "GEMINI_OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]


@live
def test_claude_worker_answers():
    r = _run(
        "claude -p 'Reply with exactly: CLAUDE_OK' --model sonnet "
        "--dangerously-skip-permissions"
    )
    assert "CLAUDE_OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]


@live
def test_deepseek_worker_answers():
    r = _run(
        LOAD_KEY + "mkdir -p /tmp/oc-live && cd /tmp/oc-live && "
        "opencode run --model deepseek/deepseek-v4-flash 'Reply with exactly: DEEPSEEK_OK'"
    )
    assert "DEEPSEEK_OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]


@live
def test_claude_effort_flag_is_accepted():
    """claudeConfig.effort -> --effort is only useful if the CLI takes it."""
    r = _run(
        "claude -p 'Reply with exactly: EFFORT_OK' --model sonnet --effort low "
        "--dangerously-skip-permissions"
    )
    assert "EFFORT_OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]


@live
def test_gemini_effort_variants_are_distinct_models():
    """Gemini effort rides in the model id, so each variant must exist."""
    r = _run("agy models")
    for variant in ("gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"):
        assert variant in r.stdout, f"{variant} missing from `agy models`"


def test_wsl_path_maps_drive_letters_and_keeps_posix_paths():
    assert _wsl_path(Path(r"C:\x\y z\k.conf")) == "/mnt/c/x/y z/k.conf"
    assert _wsl_path(Path("D:/a/b")) == "/mnt/d/a/b"
    assert _wsl_path(Path("/home/u/k.conf")) == "/home/u/k.conf"
