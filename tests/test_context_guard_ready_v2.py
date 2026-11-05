"""Hermetic ready-v2 CLI contracts; checker fixtures never use live state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/hooks/context_guard.py"


@pytest.fixture
def ready(tmp_path):
    handoff = tmp_path / "handoff.md"
    handoff.write_bytes(b"handoff\r\n")
    card = handoff.with_suffix(".card.md")
    card.write_text("# worker card · worker-0001 · 2026-11-05T20:00Z\n")
    checker = tmp_path / "handoff-check"
    checker.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                       "assert len(sys.argv) == 2\nPath(sys.argv[1]).read_bytes()\n")
    checker.chmod(0o755)
    env = {"PATH": str(tmp_path), "HOME": str(tmp_path),
           "XDG_STATE_HOME": str(tmp_path / "state"), "CLAUDE_CODE_SESSION_ID": "session-1"}
    marker = tmp_path / "state/autoos/ctx-guard/session-1.ready.json"

    def run(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              cwd=tmp_path, capture_output=True, text=True, timeout=10)
    return handoff, card, checker, env, marker, run


def test_complete_marker(ready):
    handoff, card, _, _, marker, run = ready
    p = run("ready", str(handoff))
    assert p.returncode == 0, p.stderr
    m = json.loads(marker.read_text())
    expected = dict(v=2, session_id="session-1", session_name=None, role=None,
                    launched_at=None, handoff=str(handoff), card=str(card),
                    handoff_sha256=hashlib.sha256(handoff.read_bytes()).hexdigest(),
                    card_sha256=hashlib.sha256(card.read_bytes()).hexdigest(),
                    ctx_tokens=None, reason="cap", agents=None, cwd=str(handoff.parent))
    assert m == dict(expected, ts=m["ts"])
    assert isinstance(m["ts"], (int, float))
    assert list(marker.parent.iterdir()) == [marker]


@pytest.mark.parametrize("failure", ["invalid", "missing", "unexecutable", "card", "handoff"])
def test_refuses_without_checked_pair(ready, failure):
    handoff, card, checker, _, marker, run = ready
    if failure == "invalid":
        with checker.open("a") as f:
            f.write("print('missing heading Goal')\nsys.exit(1)\n")
    elif failure == "missing":
        checker.unlink()
    elif failure == "unexecutable":
        checker.chmod(0o644)
    else:
        (card if failure == "card" else handoff).unlink()
    p = run("ready", str(handoff))
    assert p.returncode != 0
    assert not marker.exists()
    assert p.stderr
    if failure == "invalid":
        assert "missing heading Goal" in p.stderr
    if failure in ("missing", "unexecutable"):
        assert "handoff-check" in p.stderr


@pytest.mark.parametrize("which", [0, 1])
def test_changed_pair_refused(ready, which):
    handoff, card, checker, _, marker, run = ready
    with checker.open("a") as f:
        f.write(f"Path({str((handoff, card)[which])!r}).write_bytes(b'changed')\n")
    p = run("ready", str(handoff))
    assert p.returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize("reason", ["cap", "router", "operator", "planned", "bogus"])
def test_reason(ready, reason):
    handoff, _, _, _, marker, run = ready
    p = run("ready", str(handoff), "--reason", reason)
    if reason == "bogus":
        assert p.returncode != 0
        assert not marker.exists()
    else:
        assert p.returncode == 0, p.stderr
        assert json.loads(marker.read_text())["reason"] == reason


def test_hook_ignores_broken_checker(ready):
    _, _, checker, _, marker, run = ready
    checker.unlink()
    p = run()
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")
    assert not marker.exists()


def test_checker_override(ready):
    handoff, _, checker, env, _, run = ready
    renamed = checker.with_name("custom-check")
    checker.rename(renamed)
    env["AUTOOS_HANDOFF_CHECK"] = str(renamed)
    assert run("ready", str(handoff)).returncode == 0


def test_atomic_marker_replace(ready, monkeypatch):
    import importlib.util
    handoff, _, _, env, marker, _ = ready
    spec = importlib.util.spec_from_file_location("guard_ready_atomic", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    real_replace = guard.os.replace
    observed = []

    def replace(src, dst):
        assert not marker.exists()
        assert Path(dst) == marker
        assert json.loads(Path(src).read_text())["v"] == 2
        observed.append(dst)
        real_replace(src, dst)

    monkeypatch.setattr(guard.os, "replace", replace)
    assert guard.cmd_ready([str(handoff)]) == 0
    assert observed == [str(marker)]
