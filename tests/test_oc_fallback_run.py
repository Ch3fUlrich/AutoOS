import http.server, os, shutil, sys, threading, pytest
from tests.test_autoos_spawner import run_agent, claude_env

if not os.path.exists(sys.executable):
    sys.executable = shutil.which("python3") or shutil.which("python") or sys.executable
os.environ["AUTOOS_SPAWNER_ISOLATED"] = "1"
LIMIT = "You've hit your limit · resets 3pm"


@pytest.fixture(scope="module")
def health_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/global/health" else 404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def fake_claude(tmp_path):
    def _make(body, rc=0):
        cnt = str(tmp_path / "cnt.txt")
        (tmp_path / "claude.py").write_text(
            f"import sys\nif sys.argv[1:2] in (['--version'], ['--help']):\n"
            f"    print('1.0'); sys.exit(0)\n"
            f"with open({repr(cnt)}, 'a') as f: f.write('1\\n')\n"
            f"print({repr(body)})\nsys.exit({rc})\n", encoding="utf-8")
        itp = "py" if shutil.which("py") else "python"
        (tmp_path / "claude.cmd").write_text(f"@{itp} \"%~dp0claude.py\" %*\n", encoding="utf-8")
        sh = tmp_path / "claude"
        sh.write_text('#!/bin/sh\nexec python3 "$(dirname "$0")/claude.py" "$@"\n', encoding="utf-8")
        sh.chmod(0o755)
        return str(tmp_path), cnt
    return _make


def _run(cli_dir, url, *extra):
    env = claude_env(PATH=cli_dir + os.pathsep + os.environ.get("PATH", ""), AUTOOS_OPENCODE_URL=url)
    env = {k: v for k, v in env.items() if not k.startswith("GIT_CONFIG_")}
    env["GIT_CONFIG_PARAMETERS"] = "'core.longpaths=true'"
    return run_agent("run", "--client", "claude", "--tier", "1", "--isolate", *extra, "task", env=env)


def test_down(fake_claude):
    d, cnt = fake_claude(LIMIT, 1)
    r = _run(d, "http://127.0.0.1:1")
    assert r.returncode == 15
    assert "not retrying Claude" in r.stderr
    assert len(open(cnt).readlines()) == 1


def test_up(fake_claude, health_server):
    d, cnt = fake_claude(LIMIT, 1)
    r = _run(d, health_server)
    assert "switching to opencode muse-spark-1.3" in r.stderr
    assert len(open(cnt).readlines()) == 1


def test_not_a_limit(fake_claude):
    d, _ = fake_claude("all done", 0)
    r = _run(d, "http://127.0.0.1:1")
    assert "switching to opencode" not in r.stderr
    assert "fallback refused" not in r.stderr


def test_pinned(fake_claude):
    d, _ = fake_claude(LIMIT, 1)
    r = _run(d, "http://127.0.0.1:1", "--no-fallthrough")
    assert "switching to opencode" not in r.stderr
    assert "fallback refused" not in r.stderr
