import os
import sys
import json
import shutil
import subprocess
import threading
import time
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bash") is None
    or shutil.which("curl") is None,
    reason="Linux-only test requiring bash and curl",
)

class _AnyHandler(BaseHTTPRequestHandler):
    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self): self._ok()
    def do_POST(self): self._ok()
    def do_PUT(self): self._ok()
    def do_DELETE(self): self._ok()
    def log_message(self, *args): pass

def _start_http_server():
    srv = HTTPServer(("127.0.0.1", 0), _AnyHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, port

def _write_fake_omniroute(bin_dir: Path, combos_json_path: Path, log_path: Path):
    if os.name == "nt":
        raise unittest.SkipTest("the fake omniroute CLI is a bash script")
    script = bin_dir / "omniroute"
    script.write_text(f"""#!/usr/bin/env bash
set -e
echo "$*" >> "{log_path}"
if [[ " $* " == *" combo "* && " $* " == *" list "* ]]; then
    if [[ "${{FAKE_LIST_FAIL:-0}}" == "1" ]]; then
        echo '{{"combos":[],"error":"HTTP 401"}}'
        exit 1
    fi
    cat "{combos_json_path}"
    exit 0
fi
exit 1
""")
    script.chmod(0o755)

def _make_combos_json(path: Path, combos):
    data = {"combos": []}
    for c in combos:
        models = []
        for m in c.get("models", []):
            models.append({"kind": "model", "providerId": m["provider"], "model": f"{m['provider']}/{m['model']}"})
        data["combos"].append({"name": c["name"], "strategy": c.get("strategy", "priority"), "models": models})
    path.write_text(json.dumps(data))

def _run_apply(tmp_path: Path, flags, env, port):
    apply_sh = REPO / "configuration" / "omniroute" / "apply.sh"
    cmd = ["bash", str(apply_sh), *flags]
    full_env = os.environ.copy()
    full_env.update(env)
    full_env["AUTOOS_OMNIROUTE_URL"] = f"http://127.0.0.1:{port}"
    return subprocess.run(cmd, env=full_env, capture_output=True, text=True, timeout=60, cwd=tmp_path)

@pytest.fixture
def harness(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    keys_file = tmp_path / "keys.yml"
    keys_file.write_text("")
    combos_json = tmp_path / "combos.json"
    live_json = tmp_path / "combos.live.json"
    log_path = tmp_path / "fake.log"
    drift_gaps = REPO / "configuration" / "omniroute" / "drift-known-gaps.json"

    srv, port = _start_http_server()

    def _make_env(fake_list_fail=0):
        return {
            "HOME": str(home),
            "AUTOOS_KEYS_FILE": str(keys_file),
            "FAKE_COMBOS_JSON": str(combos_json),
            "FAKE_LOG": str(log_path),
            "FAKE_LIST_FAIL": str(fake_list_fail),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        }

    def _setup_combos(combos):
        _make_combos_json(combos_json, combos)

    def _setup_live(combos):
        _make_combos_json(live_json, combos)

    def _run(flags, fake_list_fail=0):
        _write_fake_omniroute(bin_dir, combos_json, log_path)
        env = _make_env(fake_list_fail)
        return _run_apply(tmp_path, flags, env, port)

    def _run_mutating(flags, fake_list_fail=0):
        _write_fake_omniroute(bin_dir, combos_json, log_path)
        env = _make_env(fake_list_fail)
        go_sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        full_flags = ["--go", "D-1", "--go-sha", go_sha, "--go-offline", *flags]
        return _run_apply(tmp_path, full_flags, env, port)

    def _read_log():
        return log_path.read_text() if log_path.exists() else ""

    try:
        yield {
            "tmp_path": tmp_path,
            "port": port,
            "combos_json": combos_json,
            "live_json": live_json,
            "drift_gaps": drift_gaps,
            "setup_combos": _setup_combos,
            "setup_live": _setup_live,
            "run": _run,
            "run_mutating": _run_mutating,
            "read_log": _read_log,
        }
    finally:
        srv.shutdown()
        srv.server_close()

def test_live_lines_two_combos(harness):
    harness["setup_combos"]([
        {"name": "combo-a", "models": [{"provider": "openai", "model": "gpt-4"}]},
        {"name": "combo-b", "strategy": "cost", "models": [{"provider": "anthropic", "model": "claude-3"}]},
    ])
    harness["setup_live"]([
        {"name": "combo-a", "models": [{"provider": "openai", "model": "gpt-4"}]},
        {"name": "combo-b", "strategy": "cost", "models": [{"provider": "anthropic", "model": "claude-3"}]},
    ])
    res = harness["run"](["--live-lines"])
    assert res.returncode == 0
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        parts = line.split("\x1f")
        assert len(parts) == 4
        assert parts[0] in ("combo-a", "combo-b")
        assert parts[1] in ("priority", "cost")
        assert parts[2] in ("openai/gpt-4", "anthropic/claude-3")
        assert parts[3] == "0"
    log = harness["read_log"]()
    assert "combo list" in log
    assert "create" not in log
    assert "delete" not in log
    assert "providers" not in log

def test_live_lines_list_fail(harness):
    harness["setup_combos"]([{"name": "x", "models": [{"provider": "p", "model": "m"}]}])
    res = harness["run"](["--live-lines"], fake_list_fail=1)
    assert res.returncode == 3
    assert "live store unreadable" in res.stdout

def test_mutating_guard_unknown_live_combo(harness):
    harness["setup_combos"]([{"name": "committed", "models": [{"provider": "p", "model": "m"}]}])
    harness["setup_live"]([
        {"name": "committed", "models": [{"provider": "p", "model": "m"}]},
        {"name": "hand-made-combo", "models": [{"provider": "q", "model": "n"}]},
    ])
    res = harness["run_mutating"]([])
    assert res.returncode == 4
    assert "guard:" in res.stderr
    assert "refused (exit 4)" in res.stderr
    log = harness["read_log"]()
    assert " create " not in log
    assert " delete " not in log
    assert "providers" not in log

def test_mutating_guard_list_fail(harness):
    harness["setup_combos"]([{"name": "x", "models": [{"provider": "p", "model": "m"}]}])
    res = harness["run_mutating"]([], fake_list_fail=1)
    assert res.returncode == 4
    assert "live store unreadable" in res.stderr

def test_overwrite_live_skips_guard(harness):
    harness["setup_combos"]([{"name": "x", "models": [{"provider": "p", "model": "m"}]}])
    res = harness["run_mutating"](["--overwrite-live"])
    assert "guard: skipped (--overwrite-live)" in res.stdout

def test_dry_run_and_drift_no_refused(harness):
    harness["setup_combos"]([{"name": "x", "models": [{"provider": "p", "model": "m"}]}])
    harness["setup_live"]([{"name": "hand-made", "models": [{"provider": "q", "model": "n"}]}])
    res_dry = harness["run_mutating"](["--dry-run"])
    assert "refused" not in res_dry.stdout
    assert "refused" not in res_dry.stderr
    assert "guard:" not in res_dry.stdout
    assert "guard:" not in res_dry.stderr
    res_drift = harness["run_mutating"](["--drift"])
    assert "refused" not in res_drift.stdout
    assert "refused" not in res_drift.stderr


def test_guard_runs_after_the_gateway_is_up():
    """A stopped gateway used to skip the guard ('no reachable live store'), then apply
    started it and mutated unchecked. The guard must come after the start block."""
    text = (Path(__file__).resolve().parent.parent / "configuration" / "omniroute" / "apply.sh").read_text(encoding="utf-8")
    guard = text.index('echo "guard: skipped (--overwrite-live)"')
    started = text.index('Gateway did not start')
    last_start = text.rindex('Gateway did not start')
    assert guard > last_start, "the apply guard runs before the gateway start block"
    assert started < guard


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main(["-q", __file__]))
