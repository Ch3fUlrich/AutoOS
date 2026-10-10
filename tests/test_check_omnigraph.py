"""Tests for tools/check-omnigraph.py: the live doctor against Omnigraph 0.13.0.

D-1038 A3. 0.13.0 requires the header `omnigraph-http-api: 0.13` on every request
except /healthz (without it: HTTP 400 `api_contract_mismatch`) and omits NULL
columns from a query row, so `{"p.slug": ...}` can simply be missing. Both used to
break the doctor: it sent no contract header, and `str(r.get("p.slug"))` turned an
omitted column into the slug "None". Offline — a fake server on 127.0.0.1:0.

Run from the repo root:

    python3 -m pytest -q tests/test_check_omnigraph.py
    python3 tests/test_check_omnigraph.py
"""
import http.server
import importlib.util
import json
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "check-omnigraph.py"
HEADER = "omnigraph-http-api"


def load_module():
    spec = importlib.util.spec_from_file_location("check_omnigraph", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _contract_ok(self):
        """Every path but /healthz is gated on the 0.13 contract header."""
        fake = self.server.fake
        self.server.seen.append((self.path, self.headers.get(HEADER)))
        if self.path == "/healthz" or not fake.reject:
            return True
        self._send(400, {"error": "unsupported http api version",
                         "code": "api_contract_mismatch"})
        return False

    def do_GET(self):
        if not self._contract_ok():
            return
        if self.path == "/healthz":
            self._send(200, {"status": "ok", "version": "0.13.0", "internal_schema_version": 14})
        elif self.path == "/graphs/memory/schema":
            self._send(200, {"source": "node Project {}"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not self._contract_ok():
            return
        if self.path != "/graphs/memory/query":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {
            "query_name": "whoami",
            "target": {"branch": "main", "snapshot": "snap-1"},
            "row_count": len(self.server.rows),
            "columns": ["p.slug"],
            "rows": self.server.rows,
            "graph_commit_id": "commit-1"})


class FakeServer:
    def __init__(self, rows=None, reject=False):
        self.rows = [{"p.slug": "autoos"}] if rows is None else rows
        self.reject = reject
        self.seen = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.fake = self
        self.httpd.seen = self.seen
        self.httpd.rows = self.rows
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def live(fake):
    return module.check_live(fake.base_url, "memory", "good-token", "from env")


class ContractVersionTests(unittest.TestCase):
    def test_the_module_declares_one_contract_version(self):
        self.assertEqual(module.CONTRACT_VERSION, "0.13")


class HappyPathTests(unittest.TestCase):
    def test_a_0_13_server_with_a_project_row_has_no_problems(self):
        with FakeServer() as fake:
            problems, facts = live(fake)
        self.assertEqual(problems, [])
        self.assertIn("project autoos", facts)
        self.assertIn("server 0.13.0", facts)

    def test_every_request_but_healthz_carried_the_contract_header(self):
        with FakeServer() as fake:
            live(fake)
            paths = [(p, h) for p, h in fake.seen]
        self.assertIn("/healthz", [p for p, _h in paths])
        gated = [x for x in paths if x[0] != "/healthz"]
        self.assertTrue(gated, "the doctor never reached schema/query")
        for path, header in gated:
            self.assertEqual(header, "0.13", f"{path} is missing {HEADER}")


class ContractMismatchTests(unittest.TestCase):
    def test_a_server_on_another_contract_reports_one_contract_problem(self):
        with FakeServer(reject=True) as fake:
            problems, _facts = live(fake)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("contract", problems[0])
        self.assertIn("0.13", problems[0])
        self.assertNotIn("schema read: 400", problems[0])


class OmittedColumnTests(unittest.TestCase):
    def test_a_row_without_the_slug_column_is_never_read_as_None(self):
        with FakeServer(rows=[{}]) as fake:
            problems, facts = live(fake)
        combined = " ".join(problems + facts)
        self.assertNotIn("None", combined)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no Project hub node", problems[0])

    def test_a_real_slug_survives_a_sibling_row_that_lost_its_column(self):
        with FakeServer(rows=[{"p.slug": "a"}, {}]) as fake:
            problems, facts = live(fake)
        self.assertEqual(problems, [])
        self.assertIn("project a", facts)
        self.assertNotIn("None", " ".join(facts))


if __name__ == "__main__":
    unittest.main()
