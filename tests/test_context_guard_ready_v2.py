"""Hermetic ready-v2 CLI contracts; checker fixtures never use live state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "tools/hooks/context_guard.py"


def make_ready_fixture(tmp_path):
    handoff = tmp_path / "handoff.md"
    handoff.write_bytes(b"handoff\r\n")
    card = handoff.with_suffix(".card.md")
    card.write_text("# worker card · worker-0001 · 2026-11-05T20:00Z\n")
    checker = tmp_path / "handoff-check"
    checker.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                       "assert len(sys.argv) == 2\nPath(sys.argv[1]).read_bytes()\n")
    checker.chmod(0o755)
    env = {**{k: os.environ[k] for k in ("SystemRoot", "PATHEXT") if k in os.environ},
           "PATH": str(tmp_path), "HOME": str(tmp_path), "AUTOOS_READY_KICK": "0",
           "XDG_STATE_HOME": str(tmp_path / "state"), "CLAUDE_CODE_SESSION_ID": "session-1"}
    if os.name == "nt":
        wrapper = tmp_path / "handoff-check.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{checker}" %*\n')
    marker = tmp_path / "state/autoos/ctx-guard/session-1.ready.json"

    def run(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              cwd=tmp_path, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=10)
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


def test_refuses_without_checked_pair(ready, failure):
    handoff, card, checker, _, marker, run = ready
    if failure == "invalid":
        with checker.open("a") as f:
            f.write("print('missing heading Goal')\nsys.exit(1)\n")
    elif failure == "missing":
        checker.unlink()
        if os.name == "nt":
            checker.with_suffix(".cmd").unlink()
    elif failure == "unexecutable":
        if os.name == "nt":
            raise unittest.SkipTest("Windows has no executable permission bits")
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


def test_changed_pair_refused(ready, which):
    handoff, card, checker, _, marker, run = ready
    with checker.open("a") as f:
        f.write(f"Path({str((handoff, card)[which])!r}).write_bytes(b'changed')\n")
    p = run("ready", str(handoff))
    assert p.returncode != 0
    assert not marker.exists()


def test_reason(ready, reason):
    handoff, _, _, _, marker, run = ready
    p = run("ready", str(handoff), "--reason", reason)
    if reason == "bogus":
        assert p.returncode != 0
        assert not marker.exists()
        assert "reason" in p.stderr
    else:
        assert p.returncode == 0, p.stderr
        assert json.loads(marker.read_text())["reason"] == reason


def test_hook_ignores_broken_checker(ready):
    _, _, checker, _, marker, run = ready
    checker.unlink()
    p = run()
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "")
    assert not marker.exists()


def test_fixture_closes_stdin(ready):
    *_, run = ready
    # Pin the subprocess contract even when the test runner has a blocking TTY.
    with patch.object(subprocess, "run") as child:
        run()
    assert child.call_args.kwargs.get("stdin") == subprocess.DEVNULL


def test_no_unused_tempfile_import(ready):
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Import) and
                   any(alias.name == "tempfile" for alias in node.names)
                   for node in ast.walk(tree))


def test_ready_argument_errors(ready):
    handoff, _, _, _, marker, run = ready
    for args, diagnostic in (
            (("--reason",), "ready needs <handoff-path>"),
            (("--unknown",), "ready needs <handoff-path>"),
            ((str(handoff), "--reason"), "--reason needs a value"),
            ((str(handoff), "--reason", "--other"), "--reason needs a value")):
        p = run("ready", *args)
        assert p.returncode == 2
        assert diagnostic in p.stderr, p.stderr
        assert not marker.exists()


def test_checker_override(ready):
    handoff, _, checker, env, _, run = ready
    renamed = checker.with_name("custom-check")
    checker.rename(renamed)
    if os.name == "nt":
        wrapper = renamed.with_suffix(".cmd")
        wrapper.write_text(f'@"{sys.executable}" "{renamed}" %*\n')
        renamed = wrapper
    env["AUTOOS_HANDOFF_CHECK"] = str(renamed)
    assert run("ready", str(handoff)).returncode == 0


def test_atomic_marker_replace(ready):
    import importlib.util
    handoff, _, _, env, marker, _ = ready
    spec = importlib.util.spec_from_file_location("guard_ready_atomic", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    real_replace = guard.os.replace
    observed = []

    def replace(src, dst):
        assert not marker.exists()
        assert Path(dst) == marker
        assert json.loads(Path(src).read_text())["v"] == 2
        observed.append(dst)
        real_replace(src, dst)

    with patch.dict(os.environ, env, clear=True), patch.object(guard.os, "replace", replace):
        assert guard.cmd_ready([str(handoff)]) == 0
    assert observed == [str(marker)]


def test_real_pair(ready):
    handoff, card, checker, _, _, run = ready
    with checker.open("a") as f:
        f.write(f"assert Path(sys.argv[1]) == Path({str(card)!r})\n"
                "assert Path(sys.argv[1]).with_suffix('').with_suffix('.md').read_bytes()\n")
    assert run("ready", str(handoff)).returncode == 0


def test_guarded_errors(ready):
    handoff, card, checker, _, _, run = ready
    for path in ("", "/"):
        p = run("ready", path)
        assert p.returncode == 2 and "Traceback" not in p.stderr
    card.unlink()
    assert "card" in run("ready", str(handoff)).stderr
    card.write_bytes(b"card")
    checker.unlink()
    if os.name == "nt":
        checker.with_suffix(".cmd").unlink()
    assert "handoff-check not found" in run("ready", str(handoff)).stderr


def test_post_replace_and_cwd(ready):
    import importlib.util
    handoff, card, _, env, marker, _ = ready
    spec = importlib.util.spec_from_file_location("guard_race", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    original = guard._write_json
    def moved(path, data):
        original(path, data)
        card.write_bytes(b"moved")
    with patch.dict(os.environ, env, clear=True):
        with patch.object(guard, "_write_json", moved):
            assert guard.cmd_ready([str(handoff)]) == 2
            assert not marker.exists()
        with patch.object(guard.os, "getcwd", side_effect=FileNotFoundError("deleted cwd")):
            assert guard.cmd_ready([str(handoff)]) == 2


def test_checker_resolution(ready):
    import importlib.util
    handoff, card, checker, env, _, _ = ready
    spec = importlib.util.spec_from_file_location("guard_resolution", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    resolved = checker.with_suffix(".cmd") if os.name == "nt" else checker
    with patch.dict(os.environ, env, clear=True), patch.object(
            guard.shutil, "which", return_value=str(resolved)) as which:
        assert guard.cmd_ready([str(handoff)]) == 0
        which.assert_called_once_with("handoff-check", path=env["PATH"])


def test_deleted_cwd(ready):
    import importlib.util
    handoff, _, _, env, marker, _ = ready
    spec = importlib.util.spec_from_file_location("guard_cwd", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    with patch.dict(os.environ, env, clear=True), patch.object(
            guard.os, "getcwd", side_effect=FileNotFoundError("deleted cwd")):
        assert guard.cmd_ready([str(handoff)]) == 2
        assert not marker.exists()


def test_ready_kick(ready, outcome):
    import contextlib
    import importlib.util
    import io
    handoff, _, checker, env, marker, _ = ready
    spec = importlib.util.spec_from_file_location("guard_kick", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    env.pop("AUTOOS_READY_KICK")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] != "systemctl":
            return subprocess.CompletedProcess(argv, int(outcome == "refused"), "", "")
        assert marker.is_file(), "kick must follow the verified marker"
        assert argv == ["systemctl", "--user", "start", "--no-block", "ctx-watchdog.service"]
        assert kwargs["timeout"] == 5
        if outcome == "missing":
            raise FileNotFoundError("missing systemctl")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 5)
        if outcome == "failed":
            assert kwargs["check"] is True
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    stderr = io.StringIO()
    with patch.dict(os.environ, env, clear=True), patch.object(
            guard.subprocess, "run", side_effect=fake_run), contextlib.redirect_stderr(stderr):
        assert guard.cmd_ready([str(handoff)]) == (2 if outcome == "refused" else 0)
    assert len(calls) == (1 if outcome == "refused" else 2)
    # Refusal is an inherent negative control: validation returns before the kick
    # and emits its own stderr diagnostic, so removing the kick still passes it.
    # The other outcomes discriminate whether the kick code is present.
    assert marker.exists() == (outcome != "refused")
    if outcome in ("failed", "missing", "timeout"):
        assert len(stderr.getvalue().splitlines()) == 1
        assert "timer" in stderr.getvalue()


# The repository runners deliberately have no pytest dependency.
class ReadyContracts(unittest.TestCase):
    pass


def _case(fn, *args):
    def run(self):
        with tempfile.TemporaryDirectory() as directory:
            fn(make_ready_fixture(Path(directory)), *args)
    return run


for _name, _fn in list(globals().items()):
    if _name.startswith("test_"):
        _values = {"test_refuses_without_checked_pair": ["invalid", "missing", "unexecutable", "card", "handoff"],
                   "test_changed_pair_refused": [0, 1],
                   "test_ready_kick": ["success", "refused", "failed", "missing", "timeout"],
                   "test_reason": ["cap", "router", "operator", "planned", "bogus"]}.get(_name)
        for _index, _args in enumerate([()] if _values is None else [(v,) for v in _values]):
            setattr(ReadyContracts, f"{_name}_{_index}", _case(_fn, *_args))
        del globals()[_name]

if __name__ == "__main__":
    unittest.main()
