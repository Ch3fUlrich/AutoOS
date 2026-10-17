"""tests/test_oc_fallback_run.py - end-to-end tests for Claude limit to OpenCode fallback."""

import http.server
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_autoos_spawner import run_agent, claude_env

if not os.path.exists(sys.executable):
    sys.executable = shutil.which("python3") or shutil.which("python") or sys.executable
os.environ["AUTOOS_SPAWNER_ISOLATED"] = "1"
LIMIT = "You've hit your limit · resets 3pm"


def _run(cli_dir, url, *extra, root=None):
    env = claude_env(PATH=cli_dir + os.pathsep + os.environ.get("PATH", ""), AUTOOS_OPENCODE_URL=url)
    env = {k: v for k, v in env.items() if not k.startswith("GIT_CONFIG_")}
    env["GIT_CONFIG_PARAMETERS"] = "'core.longpaths=true'"
    test_root = root or getattr(OcFallbackRunTests, "test_root", None)
    return run_agent("run", "--client", "claude", "--tier", "1", "--isolate", *extra, "task", env=env, root=test_root)


class OcFallbackRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/global/health" else 404)
                self.end_headers()

            def log_message(self, *a):
                pass

        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.health_server = f"http://127.0.0.1:{cls.srv.server_port}"

        cls.test_root = tempfile.mkdtemp(prefix="autoos-ocfb-root-")
        for entry in os.scandir(str(ROOT)):
            if entry.name in ("opencode.jsonc", ".git"):
                continue
            try:
                os.symlink(entry.path, os.path.join(cls.test_root, entry.name))
            except OSError:
                pass
        text = (ROOT / "opencode.jsonc").read_text(encoding="utf-8")
        idx = text.find('"models": {')
        if idx != -1:
            pos = idx + len('"models": {')
            text = text[:pos] + '\n        "muse-spark-1.3": {},\n' + text[pos:]
        (Path(cls.test_root) / "opencode.jsonc").write_text(text, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.test_root, ignore_errors=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def fake_claude(self, body, rc=0):
        tmp_path = self.tmp_path
        cnt = str(tmp_path / "cnt.txt")
        (tmp_path / "claude.py").write_text(
            f"import sys\nif sys.argv[1:2] in (['--version'], ['--help']):\n"
            f"    print('1.0'); sys.exit(0)\n"
            f"with open({repr(cnt)}, 'a') as f: f.write('1\\n')\n"
            f"print({repr(body)})\nsys.exit({rc})\n", encoding="utf-8")
        itp = "py" if shutil.which("py") else "python"
        (tmp_path / "claude.cmd").write_text(f"@{itp} \"%~dp0claude.py\" %*\n", encoding="utf-8")
        if os.name != "nt":
            sh = tmp_path / "claude"
            sh.write_text("#!" + '/bin/sh\nexec python3 "$(dirname "$0")/claude.py" "$@"\n', encoding="utf-8")
            sh.chmod(0o755)

        oc_cnt = str(tmp_path / "oc_cnt.txt")
        oc_args = str(tmp_path / "oc_args.txt")
        (tmp_path / "opencode.py").write_text(
            f"import sys\n"
            f"with open({repr(oc_cnt)}, 'a') as f: f.write('1\\n')\n"
            f"with open({repr(oc_args)}, 'a') as f: f.write('ARGS: ' + ' '.join(sys.argv) + '\\n')\n"
            f"print('done')\nsys.exit(0)\n", encoding="utf-8")
        (tmp_path / "opencode.cmd").write_text(f"@{itp} \"%~dp0opencode.py\" %*\n", encoding="utf-8")
        if os.name != "nt":
            oc_sh = tmp_path / "opencode"
            oc_sh.write_text("#!" + '/bin/sh\nexec python3 "$(dirname "$0")/opencode.py" "$@"\n', encoding="utf-8")
            oc_sh.chmod(0o755)
        return str(tmp_path), cnt

    def test_down(self):
        d, cnt = self.fake_claude(LIMIT, 1)
        r = _run(d, "http://127.0.0.1:1")
        assert r.returncode == 15
        assert "not retrying Claude" in r.stderr
        with open(cnt) as f:
            assert len(f.readlines()) == 1

    def test_up(self):
        d, cnt = self.fake_claude(LIMIT, 1)
        r = _run(d, self.health_server)
        assert "switching to opencode muse-spark-1.3" in r.stderr
        with open(cnt) as f:
            assert len(f.readlines()) == 1
        oc_cnt = Path(d) / "oc_cnt.txt"
        assert oc_cnt.exists()
        with open(oc_cnt) as f:
            assert len(f.readlines()) >= 1
        oc_args = Path(d) / "oc_args.txt"
        assert oc_args.exists()
        with open(oc_args) as f:
            assert "muse-spark-1.3" in f.read()

    def test_not_a_limit(self):
        d, _ = self.fake_claude("all done", 0)
        r = _run(d, "http://127.0.0.1:1")
        assert "switching to opencode" not in r.stderr
        assert "fallback refused" not in r.stderr

    def test_pinned(self):
        d, _ = self.fake_claude(LIMIT, 1)
        r = _run(d, "http://127.0.0.1:1", "--no-fallthrough")
        assert "switching to opencode" not in r.stderr
        assert "fallback refused" not in r.stderr


if __name__ == "__main__":
    unittest.main()
