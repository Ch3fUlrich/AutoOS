"""Tests for tools/check_omnigraph_bridge.py: the D9 bridge benchmark, offline.

SPEC-OMNI A5 (docs/plans/2026-09-27-omnigraph-mcp-catalog-spec.md §B). D9 decides
whether the catalog may start the bridge with `npx`: 16 bridges in parallel must
each answer `health` in under 2 s with zero npm cache-lock errors. Measuring that
live needs npm and a server, so the *logic* is tested here against a fake HTTP
server (the stub the `--fake-server` mode starts) and a fake stdio bridge
(tests/fixtures/omnigraph_bridge_stub.py) that speaks the same MCP JSON-RPC and
hits the same paths as the real `@modernrelay/omnigraph-mcp` package. No npm, no
network, no token.

Run from the repo root:

    python3 -m pytest -q tests/test_check_omnigraph_bridge.py
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "check_omnigraph_bridge.py"
STUB = ROOT / "tests" / "fixtures" / "omnigraph_bridge_stub.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_omnigraph_bridge", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


def run_main(args, env=None):
    """Call the real entry point and capture (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    saved = dict(os.environ)
    if env:
        os.environ.update(env)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = module.main(args)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return rc, out.getvalue(), err.getvalue()


def stub_cmd():
    return f"{sys.executable} {STUB}"


def base_args(extra=None):
    """The offline invocation: fake server, fake bridge, small wave."""
    args = ["--fake-server", "--bridge-cmd", stub_cmd(),
            "--parallel", "3", "--graph-id", "autoos", "--warm"]
    return args + (extra or [])


class FakeServerContractTests(unittest.TestCase):
    """The stub answers exactly the two paths the real bridge calls."""

    def test_healthz_is_flat_and_answers_200(self):
        with module.FakeOmnigraphServer(graph_id="autoos") as srv:
            with urllib.request.urlopen(srv.base_url + "/healthz", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(body["status"], "ok")
            self.assertIn("version", body)

    def test_query_is_graph_scoped_and_returns_a_project_row(self):
        with module.FakeOmnigraphServer(graph_id="autoos") as srv:
            req = urllib.request.Request(
                srv.base_url + "/graphs/autoos/query",
                data=json.dumps({"query": "q", "name": "whoami"}).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                rows = json.loads(resp.read().decode("utf-8"))["rows"]
            self.assertEqual(rows[0]["p.slug"], "autoos")
            self.assertIn("p.repository", rows[0])

    def test_a_path_the_bridge_never_calls_is_404(self):
        with module.FakeOmnigraphServer(graph_id="autoos") as srv:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(srv.base_url + "/graphs/autoos/schema", timeout=5)
            self.assertEqual(ctx.exception.code, 404)


class BridgeBenchmarkTests(unittest.TestCase):
    def test_all_healthy_exits_zero(self):
        rc, out, err = run_main(base_args())
        self.assertEqual(rc, 0, f"expected exit 0, got {rc}\n{out}{err}")
        self.assertIn("healthy 3/3", out)

    def test_json_reports_latency_counts_and_verdict(self):
        rc, out, _ = run_main(base_args(["--json"]))
        self.assertEqual(rc, 0, out)
        report = json.loads(out)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["parallel"], 3)
        self.assertEqual(report["healthy"], 3)
        self.assertEqual(report["whoami_ok"], 3)
        self.assertEqual(report["lock_errors"]["total"], 0)
        for key in ("min", "median", "max"):
            self.assertIsInstance(report["latency_ms"][key], (int, float))
        self.assertLessEqual(report["latency_ms"]["min"], report["latency_ms"]["max"])

    def test_a_bridge_that_exits_early_fails_and_is_counted(self):
        rc, out, _ = run_main(base_args(["--json"]),
                              env={"FAKE_BRIDGE_MODE": "exit_early"})
        report = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(report["healthy"], 0)
        self.assertEqual(len(report["failures"]), 3)

    def test_a_wrong_slug_fails_even_when_health_is_fine(self):
        rc, out, _ = run_main(base_args(["--fake-slug", "someone-else", "--json"]))
        report = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(report["healthy"], 3, "health itself must still pass")
        self.assertEqual(report["whoami_ok"], 0)
        self.assertIn("someone-else", json.dumps(report["failures"]))

    def test_npm_lock_noise_on_stderr_is_counted(self):
        rc, out, _ = run_main(base_args(["--json"]), env={"FAKE_BRIDGE_MODE": "lock_noise"})
        report = json.loads(out)
        self.assertGreaterEqual(report["lock_errors"]["total"], 3)
        self.assertGreaterEqual(report["lock_errors"]["EEXIST"], 3)
        self.assertEqual(rc, 1, "D9 requires zero cache-lock errors")
        self.assertIn("lock", out.lower() + json.dumps(report))

    def test_the_token_is_never_printed(self):
        token = "tok-" + "en" + "-censored-if-echoed"
        rc, out, err = run_main(base_args(["--json"]),
                                env={"FAKE_BRIDGE_MODE": "leak_token",
                                     "OMNIGRAPH_TOKEN": token})
        combined = out + err
        self.assertNotIn(token, combined)
        self.assertNotIn(token, json.dumps(json.loads(out)) if out.strip() else "")
        self.assertEqual(rc, 0, combined)

    def test_health_slower_than_the_d9_limit_fails(self):
        rc, out, _ = run_main(base_args(["--limit-ms", "1", "--json"]),
                              env={"FAKE_BRIDGE_MODE": "slow"})
        report = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertGreater(report["latency_ms"]["min"], 1)
        self.assertTrue(any("limit" in str(f).lower() for f in report["failures"]), report)

    def test_cold_cache_uses_a_fresh_private_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "npm-cache"
            rc, out, err = run_main(
                ["--fake-server", "--bridge-cmd", stub_cmd(), "--parallel", "1",
                 "--graph-id", "autoos", "--cold", "--npm-cache", str(cache), "--json"])
            self.assertEqual(rc, 0, f"{out}{err}")
            self.assertTrue(cache.is_dir(), "the cold cache dir must be created")
            self.assertEqual(json.loads(out)["mode"], "cold")

    def test_usage_errors_exit_two(self):
        for bad in (["--fake-server", "--bridge-cmd", stub_cmd(), "--parallel", "0"],
                    ["--fake-server", "--bridge-cmd", "   ", "--graph-id", "autoos"],
                    ["--fake-server", "--bridge-cmd", stub_cmd(), "--graph-id", ""],
                    ["--fake-server", "--bridge-cmd", stub_cmd(), "--timeout", "0"],
                    ["--root", str(ROOT / "nope"), "--bridge-cmd", stub_cmd()]):
            rc, _, err = run_main(bad)
            self.assertEqual(rc, 2, f"{bad} -> {rc}\n{err}")


class BridgeConfigTests(unittest.TestCase):
    """The command and graph id come from .mcp.json — never spelled twice."""

    def test_real_repo_mcp_json_resolves_the_pinned_bridge(self):
        cfg = module.read_bridge_config(ROOT)
        self.assertTrue(any("omnigraph-mcp" in a for a in cfg["cmd"]), cfg["cmd"])
        self.assertEqual(cfg["graph_id"], "autoos")
        self.assertTrue(cfg["base_url"], "a base url must resolve")

    def test_the_base_url_default_is_read_from_the_env_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"omnigraph": {
                "command": "node", "args": ["bridge.js"],
                "env": {"OMNIGRAPH_BASE_URL": "${OMNIGRAPH_BASE_URL:-http://localhost:8080}",
                        "OMNIGRAPH_TOKEN": "${OMNIGRAPH_TOKEN}",
                        "OMNIGRAPH_GRAPH_ID": "othergraph"}}}}), encoding="utf-8")
            cfg = module.read_bridge_config(root)
            self.assertEqual(cfg["cmd"], ["node", "bridge.js"])
            self.assertEqual(cfg["base_url"], "http://localhost:8080")
            self.assertEqual(cfg["graph_id"], "othergraph")

    def test_a_missing_entry_is_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(module.Unusable):
                module.read_bridge_config(Path(tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
