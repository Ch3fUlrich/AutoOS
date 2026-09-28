"""Tests for tools/check_omnigraph_bridge.py: the D9 bridge benchmark, offline.

SPEC-OMNI A5 (docs/plans/2026-09-27-omnigraph-mcp-catalog-spec.md §B). D9 decides
whether the catalog may start the bridge with `npx`: 16 bridges in parallel must
each answer `health` in under 2 s with zero npm cache-lock errors. Measuring that
live needs npm and a server, so the *logic* is tested here against a fake HTTP
server (the stub the `--fake-server` mode starts) and a fake stdio bridge
(tests/fixtures/omnigraph_bridge_stub.py) that speaks the same MCP JSON-RPC and
hits the same paths as the real `@modernrelay/omnigraph-mcp` package. No npm, no
network, no token.

A bridge that wedges is the other half of what is tested here: `npx` leaves
`npm`-spawned grandchildren behind if only the direct child is killed, and a
Ctrl-C must stop the wave rather than wait out every remaining deadline. The
stub's `hang` mode reproduces both shapes.

Run from the repo root:

    python3 -m pytest -q tests/test_check_omnigraph_bridge.py
"""
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
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


class WhoamiQueryTests(unittest.TestCase):
    def test_whoami_query_asks_only_for_properties_the_live_schema_has(self):
        # Project has no `repository` property live: asking for it is a type error
        # (T6), which made the bridge check fail against a healthy server.
        self.assertNotIn("repository", module.WHOAMI_QUERY)
        self.assertIn("$p.slug", module.WHOAMI_QUERY)


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


def pid_exists(pid):
    """Whether *pid* is still there — a zombie counts, so 'killed' is not 'gone'.

    POSIX only: on Windows os.kill(pid, 0) *terminates* the process instead of
    probing it, so every caller of this stays behind an os.name guard.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WedgedBridgeProcessTests(unittest.TestCase):
    """What a bridge that never answers leaves behind (A5 review).

    `npx` starts `npm`, which starts `node`; killing only the direct child the
    tool spawned leaves the rest of that tree running under init, and a Ctrl-C
    during the wave waited out every remaining --timeout instead of stopping.
    The stub's `hang` mode is the reproduction: a real descendant *process*, a
    bridge that ignores SIGTERM and never replies, and one line appended to a
    temp file per bridge with both of its pids, so they can be watched after the
    tool has returned.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="autoos-bridge-hang-")
        self.pid_file = Path(self.tmp.name) / "pids"
        self.pids = []

    def tearDown(self):
        for pid in self.pids:  # a red run must not leave a sleeper behind
            try:
                os.kill(pid, 9)  # 9 = SIGKILL, written as a number: no signal.SIGKILL on Windows
            except OSError:
                pass
        self.tmp.cleanup()

    def hang_env(self, extra=None):
        env = {"FAKE_BRIDGE_MODE": "hang", "FAKE_BRIDGE_PID_FILE": str(self.pid_file)}
        env.update(extra or {})
        return env

    def wedged_pids(self, at_least=1, timeout=20):
        """One '<bridge pid> <grandchild pid>' line per bridge that wedged."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pairs = self.reported_pids()
            if len(pairs) >= at_least:
                return pairs
            time.sleep(0.05)
        self.fail(f"the wedged bridge never wrote its pids to {self.pid_file}")

    def reported_pids(self):
        """Every pid pair reported so far, tracked for teardown either way."""
        try:
            lines = self.pid_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        pairs = []
        for line in lines:
            fields = line.split()
            if len(fields) != 2:
                continue
            pair = [int(f) for f in fields]
            for pid in pair:
                if pid not in self.pids:
                    self.pids.append(pid)
            pairs.append(pair)
        return pairs

    def assert_gone(self, pid, what, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not pid_exists(pid):
                return
            time.sleep(0.1)
        self.fail(f"{what} (pid {pid}) is still alive after the tool returned")

    def interrupt_a_wedged_run(self, extra, ignore_sigint=False):
        """Run the real entry point as its own process and SIGINT it once wedged.

        The signal goes to the tool's pid alone, exactly as Ctrl-C reaches a
        terminal's foreground process — the bridges must be stopped by the tool,
        not by the signal themselves. `ignore_sigint` reproduces a child
        backgrounded by a non-interactive shell, which inherits SIGINT as
        ignored (AGENTS.md §6) and must still stop.
        """
        env = os.environ.copy()
        env.update(self.hang_env())
        kwargs = {}
        if ignore_sigint:
            kwargs["preexec_fn"] = lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)
        proc = subprocess.Popen(
            [sys.executable, str(MODULE_PATH), "--fake-server", "--bridge-cmd", stub_cmd(),
             "--graph-id", "autoos", "--timeout", "60", *extra],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
        self.wedged_pids()
        started = time.monotonic()
        os.kill(proc.pid, signal.SIGINT)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self.fail("Ctrl-C did not stop the run — it is still waiting out the 60 s "
                      f"per-bridge timeout.\n--- stdout\n{out}\n--- stderr\n{err}")
        return proc.returncode, out + err, time.monotonic() - started

    def test_a_wedged_bridge_takes_its_grandchildren_with_it(self):
        rc, out, err = run_main(
            ["--fake-server", "--bridge-cmd", stub_cmd(), "--graph-id", "autoos",
             "--parallel", "2", "--cold", "--timeout", "1", "--json"],
            env=self.hang_env())
        pairs = self.reported_pids()
        self.assertEqual(len(pairs), 2,
                         "both bridges of the wave must have reported their pids, or "
                         "the check below proves nothing about the wave")
        self.assertEqual(rc, 1, f"a wedged bridge must fail the run\n{out}{err}")
        report = json.loads(out)
        self.assertEqual(report["healthy"], 0, report)
        self.assertTrue(any("timeout" in f["reason"].lower() for f in report["failures"]),
                        "the old failure output is kept: a hang reads as a timeout, not silence")
        for bridge_pid, grandchild_pid in pairs:
            self.assert_gone(grandchild_pid, "the grandchild a wedged bridge spawned")
            self.assert_gone(bridge_pid, "the wedged bridge")

    @unittest.skipIf(os.name == "nt", "process groups are POSIX; Windows kills with taskkill")
    def test_a_wedged_bridge_is_closed_without_leaving_a_zombie(self):
        env = os.environ.copy()
        env.update(self.hang_env())
        client = module.McpStdioClient([sys.executable, str(STUB)], env,
                                       time.monotonic() + 1, None)
        self.pids.append(client.proc.pid)
        client.close()
        self.assert_gone(client.proc.pid, "the bridge close() killed")

    @unittest.skipIf(os.name == "nt", "process groups are POSIX; Windows kills with taskkill")
    def test_a_bridge_leads_its_own_process_group(self):
        # killpg only reaches the bridge's tree — and may only ever be aimed at a
        # group the bridge leads, never at the benchmark's own.
        env = os.environ.copy()
        env.update(self.hang_env())
        client = module.McpStdioClient([sys.executable, str(STUB)], env,
                                       time.monotonic() + 1, None)
        self.pids.append(client.proc.pid)
        try:
            self.assertEqual(
                os.getpgid(client.proc.pid), client.proc.pid,
                "the bridge runs in somebody else's process group, so close()'s "
                "killpg would signal that group — the benchmark's own processes "
                "included")
        finally:
            client.close()

    def assert_interrupted_cleanly(self, rc, combined, elapsed):
        self.assertNotIn("Traceback", combined, f"an interrupt must be handled\n{combined}")
        self.assertEqual(rc, 130, f"Ctrl-C must exit 130, not score a half wave\n{combined}")
        self.assertIn("interrupt", combined.lower())
        self.assertLess(elapsed, 20, "an interrupted wave must not sit out --timeout 60")
        pairs = self.reported_pids()  # every bridge that started, not just the first
        self.assertGreaterEqual(len(pairs), 1, "no bridge reported, so nothing was checked")
        for pid in self.pids:
            self.assert_gone(pid, "a process from the interrupted wave")

    @unittest.skipIf(os.name == "nt", "SIGINT semantics; Windows kills with taskkill")
    def test_ctrl_c_stops_the_wave_instead_of_waiting_it_out(self):
        rc, combined, elapsed = self.interrupt_a_wedged_run(["--parallel", "2", "--cold"])
        self.assert_interrupted_cleanly(rc, combined, elapsed)

    @unittest.skipIf(os.name == "nt", "SIGINT semantics; Windows kills with taskkill")
    def test_ctrl_c_while_priming_a_warm_cache_stops_too(self):
        rc, combined, elapsed = self.interrupt_a_wedged_run(["--parallel", "1", "--warm"])
        self.assert_interrupted_cleanly(rc, combined, elapsed)

    @unittest.skipIf(os.name == "nt", "SIGINT semantics; Windows kills with taskkill")
    def test_ctrl_c_stops_a_run_that_inherited_sigint_ignored(self):
        # A headless lane starts its children in the background with SIGINT
        # ignored, and CPython then never arms the KeyboardInterrupt handler, so
        # on such a run a Ctrl-C did nothing at all until the tool armed it.
        rc, combined, elapsed = self.interrupt_a_wedged_run(
            ["--parallel", "2", "--cold"], ignore_sigint=True)
        self.assert_interrupted_cleanly(rc, combined, elapsed)


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
