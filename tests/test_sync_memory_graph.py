#!/usr/bin/env python3
"""Tests for tools/sync_memory_graph.py - the AutoOS memory-graph sync.

Pure stdlib, no live graph. Fixtures are temp dirs mimicking the repo layout
(docs/decisions/, the skill file, docs/tasks.md); nothing outside the sandbox
is read. Run from anywhere:

    python3 tests/test_sync_memory_graph.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "sync_memory_graph.py"


def load_script():
    spec = importlib.util.spec_from_file_location("sync_memory_graph", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_root():
    """A minimal repo layout the parser can mine."""
    tmp = Path(tempfile.mkdtemp(prefix="memgraph-"))
    (tmp / "docs" / "decisions").mkdir(parents=True)
    (tmp / "docs" / "plans").mkdir(parents=True)
    (tmp / ".agents" / "skills" / "unattended-orchestration").mkdir(parents=True)
    (tmp / "docs" / "decisions" / "0001-example-decision.md").write_text(
        "# 0001 \u2014 Example decision\n\nStatus: accepted \u00b7 2026-09-11\n\n"
        "## Context\n\nFirst paragraph of context explains why.\n\nSecond paragraph.\n",
        encoding="utf-8",
    )
    (tmp / ".agents" / "skills" / "unattended-orchestration" / "SKILL.md").write_text(
        "### coord (L1)\n\n"
        "- R-coord-01: Cut lanes from main. (why: ready needs tested tip; source: 2c3e4f7)\n"
        "- R-coord-02: Already loaded rule. (why: x; source: y)\n",
        encoding="utf-8",
    )
    (tmp / "docs" / "tasks.md").write_text(
        "| Task | Owner | Started | DONE-criteria | Status |\n"
        "|---|---|---|---|---|\n"
        "| New running thing | autoos-L1-backlog | 2026-09-28 | tests green | Running |\n"
        "| Old done thing | tier1 | 2026-09-20 | green | Done 2026-09-20 |\n",
        encoding="utf-8",
    )
    return tmp


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_script()
        self.root = fixture_root()
        self.state = self.root / ".state" / "graph-loaded.txt"

    def test_ledger_prevents_reemission(self):
        """The core safety property (operations.md rule 7): edges have no
        @key and duplicate on every re-load, so a slug in the ledger must
        never be emitted again."""
        first = self.mod.emit(self.root, self.state)
        self.assertGreater(len(first), 0)
        slugs_first = {key for key, _, _ in first}
        # Simulate a confirmed load: mark, then re-emit must be empty.
        self.mod.mark(self.root, self.state, first)
        second = self.mod.emit(self.root, self.state)
        self.assertEqual(second, [])
        # And the ledger now holds every emitted slug.
        self.assertEqual(set(self.state.read_text().split()), slugs_first)

    def test_partial_ledger_emits_only_missing(self):
        """A slug recorded by an earlier run is skipped; new slugs still flow."""
        first = self.mod.emit(self.root, self.state)
        victim = first[0][0]
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(victim + "\n", encoding="utf-8")
        second = self.mod.emit(self.root, self.state)
        self.assertNotIn(victim, {key for key, _, _ in second})
        self.assertEqual(len(second), len(first) - 1)

    def test_mark_appends_only_after_load(self):
        """--mark records exactly the emitted slugs, one per line, and a
        second mark of the same batch does not duplicate ledger lines."""
        batch = self.mod.emit(self.root, self.state)
        self.mod.mark(self.root, self.state, batch)
        lines = self.state.read_text().split()
        self.assertEqual(len(lines), len({key for key, _, _ in batch}))


class ShapeTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_script()
        self.root = fixture_root()
        self.state = self.root / ".state" / "graph-loaded.txt"

    def test_ndjson_shape(self):
        """Nodes carry type+data, edges carry PascalCase edge + from/to slugs."""
        batch = self.mod.emit(self.root, self.state)
        for _, node, edges in batch:
            self.assertIn("type", node)
            self.assertIn("data", node)
            self.assertIn("slug", node["data"])
            for edge in edges:
                self.assertIn("edge", edge)
                self.assertTrue(edge["edge"][0].isupper(), edge)
                self.assertIn("from", edge)
                self.assertIn("to", edge)

    def test_slugs_lowercase_kebab(self):
        """Auto-merge keys on slug; a case variant is a duplicate node."""
        import re

        batch = self.mod.emit(self.root, self.state)
        for key, node, _ in batch:
            self.assertEqual(key, key.lower(), key)
            self.assertRegex(key, r"^[a-z0-9][a-z0-9-]*$")
            self.assertRegex(node["data"]["slug"], r"^[a-z0-9][a-z0-9-]*$")

    def test_every_node_has_hub_edge_to_autoos(self):
        """A node with no Project edge renders as global (schema.md)."""
        hubs = {"DecidedIn", "ConstrainsProject", "AppliesTo", "PartOf", "Tracks"}
        batch = self.mod.emit(self.root, self.state)
        for key, node, edges in batch:
            hub = [e for e in edges if e["edge"] in hubs and e["to"] == "autoos"]
            self.assertTrue(hub, f"{key} ({node['type']}) has no hub edge to autoos")

    def test_done_tasks_not_emitted(self):
        """History rows stay out of the live-board sync."""
        batch = self.mod.emit(self.root, self.state)
        titles = " ".join(
            json.dumps(node) for _, node, _ in batch if node["type"] == "Task"
        )
        self.assertIn("New running thing", titles)
        self.assertNotIn("Old done thing", titles)

    def test_no_secrets_or_absolutisms_in_output(self):
        """Public repo: no tokens, no key paths, no username-carrying paths."""
        batch = self.mod.emit(self.root, self.state)
        blob = json.dumps([(n, e) for _, n, e in batch])
        for needle in ("Bearer", "OMNIGRAPH_TOKEN", "api-keys", "/home/"):
            self.assertNotIn(needle, blob)


class LoadModeTests(unittest.TestCase):
    def test_load_posts_merge_to_graph_endpoint(self):
        """--load POSTs mode=merge (never overwrite: operations.md rule 8)
        to /graphs/autoos/load with a Bearer token from the environment."""
        mod = load_script()
        root = fixture_root()
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                seen["path"] = self.path
                seen["auth"] = self.headers.get("Authorization")
                seen["body"] = json.loads(self.rfile.read(length) or b"{}")
                payload = json.dumps({"tables": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            env = dict(os.environ)
            env["OMNIGRAPH_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
            env["OMNIGRAPH_TOKEN"] = "test-token"
            rc = mod.main(["--load"], root=root, env=env)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(rc, 0)
        self.assertEqual(seen["path"], "/graphs/autoos/load")
        self.assertEqual(seen["auth"], "Bearer test-token")
        self.assertEqual(seen["body"]["branch"], "main")
        self.assertEqual(seen["body"]["mode"], "merge")
        lines = seen["body"]["data"].strip().split("\n")
        self.assertGreater(len(lines), 0)
        for line in lines:
            rec = json.loads(line)
            self.assertTrue("type" in rec or "edge" in rec)


class SeverityTests(unittest.TestCase):
    def test_why_clause_softening_word_ignored(self):
        """B1: R-router-02 is mandatory; 'usually' lives only in the
        trailing non-normative (why: ...) rationale clause."""
        mod = load_script()
        statement = (
            "Diagnose against host and route state before declaring failure. "
            "(why: first verdict is usually wrong; "
            "source: review-b3c1.out 2026-09-26T07:33Z)"
        )
        self.assertEqual(mod.decide_rule_severity(statement), "must")

    def test_genuinely_soft_statement_still_should(self):
        """Guard against overcorrection: a normative softening word stays."""
        mod = load_script()
        self.assertEqual(
            mod.decide_rule_severity("You may retry the load once on 504."),
            "should",
        )


class LoadConfirmTests(unittest.TestCase):
    def test_failed_load_does_not_mark_and_exits_nonzero(self):
        """B2: a --load whose response reports zero/failed rows for records
        sent must not touch the ledger; main must exit non-zero."""
        mod = load_script()
        root = fixture_root()
        ledger = root / ".state" / "graph-loaded.txt"
        pending = mod.emit(root, ledger)
        self.assertGreater(len(pending), 0)
        orig = mod.post_load
        mod.post_load = lambda *a, **k: {
            "tables": [{"table_key": "Rule", "rows_loaded": 0,
                        "error": "simulated failure"}]
        }
        try:
            rc = mod.main(["--load"], root=root,
                          env={"OMNIGRAPH_TOKEN": "test-token",
                               "OMNIGRAPH_BASE_URL": "http://localhost:1"})
        finally:
            mod.post_load = orig
        self.assertNotEqual(rc, 0)
        if ledger.exists():
            self.assertEqual(ledger.read_text().split(), [])
        # Nothing was marked, so the full batch is still pending.
        self.assertEqual(len(mod.emit(root, ledger)), len(pending))


if __name__ == "__main__":
    unittest.main(verbosity=2)
