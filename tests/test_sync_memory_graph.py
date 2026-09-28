#!/usr/bin/env python3
"""Tests for tools/sync_memory_graph.py - the AutoOS memory-graph sync.

Pure stdlib, no live graph. Fixtures are temp dirs mimicking the repo layout
(docs/decisions/, the skill file, docs/tasks.md); nothing outside the sandbox
is read. Run from anywhere:

    python3 tests/test_sync_memory_graph.py
"""
import contextlib
import importlib.util
import io
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
        "| Old done thing | tier1 | 2026-09-20 | green | Done 2026-09-20 |\n"
        "| Wire router links (D-088) | autoos-L1-backlog | 2026-09-28 | "
        "Implements routing-d-100; tests green | Running |\n"
        "| Closed cite thing (D-999) | tier1 | 2026-09-20 | green | Done 2026-09-20 |\n",
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
        for key, node, edges in batch:
            if node is not None:
                self.assertIn("type", node)
                self.assertIn("data", node)
                self.assertIn("slug", node["data"])
            else:
                # An edge-only record carries no node, only the ledger key
                # that gates it (Implements/Supersedes have no node of their own).
                self.assertRegex(key, r"^[A-Z][A-Za-z]+:")
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
            if node is None:
                # Edge-only ledger key; the slugs live in the edge's from/to.
                self.assertRegex(key, r"^[A-Z][A-Za-z]+:autoos-[a-z0-9-]+->routing-d-\d+$")
                continue
            self.assertEqual(key, key.lower(), key)
            self.assertRegex(key, r"^[a-z0-9][a-z0-9-]*$")
            self.assertRegex(node["data"]["slug"], r"^[a-z0-9][a-z0-9-]*$")

    def test_every_node_has_hub_edge_to_autoos(self):
        """A node with no Project edge renders as global (schema.md)."""
        hubs = {"DecidedIn", "ConstrainsProject", "AppliesTo", "PartOf", "Tracks"}
        batch = self.mod.emit(self.root, self.state)
        for key, node, edges in batch:
            if node is None:
                continue  # edge-only records (Implements/Supersedes) have no node
            hub = [e for e in edges if e["edge"] in hubs and e["to"] == "autoos"]
            self.assertTrue(hub, f"{key} ({node['type']}) has no hub edge to autoos")

    def test_done_tasks_not_emitted(self):
        """History rows stay out of the live-board sync."""
        batch = self.mod.emit(self.root, self.state)
        titles = " ".join(
            json.dumps(node) for _, node, _ in batch
            if node is not None and node["type"] == "Task"
        )
        self.assertIn("New running thing", titles)
        self.assertNotIn("Old done thing", titles)
        self.assertNotIn("Closed cite thing", titles)

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


class CitationParserTests(unittest.TestCase):
    """Pure helpers: routing-d-NNN lookup by slug, and explicit supersession."""

    def setUp(self):
        self.mod = load_script()

    def test_routing_citations_normalise_bare_and_dedupe(self):
        text = ("Implements routing-d-100 and D-088; also D-088 again. "
                "See autoos-adr-0006 (an ADR, not a router decision).")
        self.assertEqual(self.mod.routing_citations(text),
                         ["routing-d-100", "routing-d-088"])

    def test_routing_citations_ignores_non_router_d_digits(self):
        """`routing-d-044` must not also match its own inner `d-044`."""
        self.assertEqual(self.mod.routing_citations("routing-d-044"), ["routing-d-044"])

    def test_supersedes_targets_only_after_a_supersede_verb(self):
        self.assertEqual(self.mod.supersedes_targets("superseding D-085's interim 250k"),
                         ["routing-d-085"])
        self.assertEqual(self.mod.supersedes_targets("replaces routing-d-040 in the plan"),
                         ["routing-d-040"])
        # A bare citation with no verb is an implementation, not a replacement.
        self.assertEqual(self.mod.supersedes_targets("implements routing-d-040"), [])
        self.assertEqual(self.mod.supersedes_targets("no replacement here"), [])

    def test_supersedes_targets_window_ignores_a_distant_citation(self):
        text = "replaced the interim cap; " + "x" * 80 + " D-085"
        self.assertEqual(self.mod.supersedes_targets(text), [])


class RoutingLinkTests(unittest.TestCase):
    """D-140: Task -> Decision `Implements` edges (and gated `Supersedes`)."""

    def setUp(self):
        self.mod = load_script()
        self.root = fixture_root()
        self.state = self.root / ".state" / "graph-loaded.txt"
        self.task_slug = "autoos-task-wire-router-links-d-088"

    def _implements(self, batch):
        return {(e["from"], e["to"]) for _, _, edges in batch
                for e in edges if e["edge"] == "Implements"}

    def test_task_citations_become_implements_edges(self):
        batch = self.mod.emit(self.root, self.state)
        impl = self._implements(batch)
        self.assertIn((self.task_slug, "routing-d-088"), impl)   # bare D-088 normalised
        self.assertIn((self.task_slug, "routing-d-100"), impl)   # slug form accepted

    def test_implements_is_task_to_decision_only(self):
        """Never Decision -> Decision `Implements` (no such schema edge)."""
        batch = self.mod.emit(self.root, self.state)
        for _, _, edges in batch:
            for e in edges:
                if e["edge"] == "Implements":
                    self.assertTrue(e["from"].startswith("autoos-task-"), e)
                    self.assertRegex(e["to"], r"^routing-d-\d+$")

    def test_target_is_slug_not_project(self):
        """Targets are looked up by routing-d-NNN slug, never by project."""
        batch = self.mod.emit(self.root, self.state)
        targets = {e["to"] for _, _, edges in batch
                   for e in edges if e["edge"] == "Implements"}
        self.assertTrue(targets)
        for target in targets:
            self.assertRegex(target, r"^routing-d-\d+$")

    def test_done_task_citations_are_not_emitted(self):
        batch = self.mod.emit(self.root, self.state)
        all_edges = [e for _, _, edges in batch for e in edges]
        self.assertFalse(any(e["edge"] == "Implements" and e["to"] == "routing-d-999"
                             for e in all_edges))

    def test_unknown_slug_is_skipped_with_stderr_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            batch = self.mod.records(self.root, known={"routing-d-100"})
        impl = self._implements(batch)
        self.assertIn((self.task_slug, "routing-d-100"), impl)
        self.assertNotIn((self.task_slug, "routing-d-088"), impl)
        self.assertIn("routing-d-088", err.getvalue())

    def test_known_none_emits_every_citation(self):
        batch = self.mod.records(self.root)
        self.assertIn((self.task_slug, "routing-d-088"), self._implements(batch))

    def test_supersedes_only_where_text_explicitly_replaces(self):
        """Exactly one explicit replacement in the DECISIONS text; all others none."""
        batch = self.mod.emit(self.root, self.state)
        supersedes = [(e["from"], e["to"]) for _, _, edges in batch
                      for e in edges if e["edge"] == "Supersedes"]
        self.assertEqual(supersedes, [("autoos-plan-restart-spec", "routing-d-085")])

    def test_unknown_supersedes_target_is_skipped(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            batch = self.mod.records(self.root, known={"routing-d-100"})
        supersedes = [e for _, _, edges in batch for e in edges
                      if e["edge"] == "Supersedes"]
        self.assertEqual(supersedes, [])
        self.assertIn("routing-d-085", err.getvalue())

    def test_edge_only_records_survive_a_node_slug_ledger(self):
        """Regression: a machine whose ledger already holds the Task slug (an
        older schema that stored cross-project links as text, not edges) must
        still emit the Implements edge once - node slug and edge key are
        distinct ledger entries."""
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(self.task_slug + "\n", encoding="utf-8")
        batch = self.mod.emit(self.root, self.state)
        self.assertNotIn(self.task_slug, {key for key, _, _ in batch})
        self.assertIn((self.task_slug, "routing-d-088"), self._implements(batch))
        # Marking the edge batch leaves the node slug untouched and re-emits nothing.
        self.mod.mark(self.root, self.state, batch)
        self.assertEqual(self.mod.emit(self.root, self.state), [])

    def test_edge_key_is_not_a_node_slug(self):
        batch = self.mod.emit(self.root, self.state)
        edge_only = [(k, n) for k, n, _ in batch if n is None]
        self.assertTrue(edge_only)
        for key, node in edge_only:
            self.assertIsNone(node)
            self.assertRegex(key, r"^(Implements|Supersedes):autoos-[a-z0-9-]+->routing-d-\d+$")

    def test_main_prints_no_null_nodes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.mod.main([], root=self.root, env={})
        self.assertEqual(rc, 0)
        lines = out.getvalue().splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertNotEqual(line.strip(), "null")
            rec = json.loads(line)
            self.assertTrue("type" in rec or "edge" in rec, rec)

    def test_main_known_slugs_comma_list_filters(self):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.mod.main(["--known-slugs", "routing-d-100"], root=self.root, env={})
        self.assertEqual(rc, 0)
        records = [json.loads(line) for line in out.getvalue().splitlines()]
        targets = {r["to"] for r in records if r.get("edge") in ("Implements", "Supersedes")}
        self.assertEqual(targets, {"routing-d-100"})
        self.assertIn("routing-d-088", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
