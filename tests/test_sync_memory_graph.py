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
import urllib.error
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


def fixture_root_with_tasks(rows):
    """A fixture whose docs/tasks.md holds exactly ``rows`` (header added)."""
    tmp = fixture_root()
    body = ["| Task | Owner | Started | DONE-criteria | Status |",
            "|---|---|---|---|---|"]
    body.extend(rows)
    (tmp / "docs" / "tasks.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    return tmp


# D-1038 A3: the Omnigraph 0.13.0 load response, measured from a server-L1
# rehearsal. ``_NEW_LOAD`` is the envelope both samples share; NEW_LOAD_OK is
# the "small append" (1 JSONL line sent), NEW_LOAD_126 the 126-line sample with
# its nodes/edges abbreviated to the types quoted in the measurement. The
# rehearsal's storage uri is an environment path, not part of the contract, so
# the fixture carries an obviously fake one (AGENTS.md rule 1).
_NEW_LOAD = {
    "uri": "s3://example/cluster/graphs/autoos.omni",
    "branch": "main", "base_branch": None, "branch_created": False, "mode": "merge",
    "actor_id": "default",
    "commit": {"graph_commit_id": "hb1.01M4K", "graph_branch": None,
               "graph_manifest_version": 2, "parent_commit_id": "01M4K",
               "merged_parent_commit_id": None, "actor_id": "default",
               "created_at": 1791657392112775},
}
NEW_LOAD_OK = dict(_NEW_LOAD, total_entities=1, embedding_generation=None,
                   nodes=[{"name": "Preference", "entities_loaded": 1}], edges=[])
NEW_LOAD_126 = dict(_NEW_LOAD, total_entities=126,
                    embedding_generation="unsupported",
                    nodes=[{"name": "Component", "entities_loaded": 8},
                           {"name": "Rule", "entities_loaded": 20}],
                    edges=[{"name": "Affects", "entities_loaded": 12}])


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
                seen["contract"] = self.headers.get("omnigraph-http-api")
                seen["body"] = json.loads(self.rfile.read(length) or b"{}")
                # Detailed per-table response so the load is confirmed (an
                # empty-tables response would now refuse an edge batch).
                payload = json.dumps(
                    {"tables": [{"table_key": "Task", "rows_loaded": 5}]}).encode()
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
            rc = mod.main(["--known-slugs", "routing-d-085,routing-d-088,routing-d-100",
                           "--load"], root=root, env=env)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(rc, 0)
        self.assertEqual(seen["path"], "/graphs/autoos/load")
        self.assertEqual(seen["auth"], "Bearer test-token")
        # D-1038 A3: 0.13.0 answers 400 api_contract_mismatch without this.
        self.assertEqual(seen["contract"], "0.13")
        self.assertEqual(seen["body"]["branch"], "main")
        self.assertEqual(seen["body"]["mode"], "merge")
        lines = seen["body"]["data"].strip().split("\n")
        self.assertGreater(len(lines), 0)
        for line in lines:
            rec = json.loads(line)
            self.assertTrue("type" in rec or "edge" in rec)

    def test_load_body_lists_node_lines_before_edge_lines(self):
        """An edge's endpoints must exist before the edge is loaded."""
        mod = load_script()
        root = fixture_root()
        captured = {}
        orig = mod.post_load

        def capture(_base, _token, lines):
            captured["lines"] = list(lines)
            return {"tables": [{"table_key": "Task", "rows_loaded": 3}]}

        mod.post_load = capture
        try:
            rc = mod.main(["--known-slugs", "routing-d-085,routing-d-088,routing-d-100",
                           "--load"], root=root, env={"OMNIGRAPH_TOKEN": "t"})
        finally:
            mod.post_load = orig
        self.assertEqual(rc, 0)
        recs = [json.loads(line) for line in captured["lines"]]
        node_idx = [i for i, r in enumerate(recs) if "type" in r]
        edge_idx = [i for i, r in enumerate(recs) if "edge" in r]
        self.assertTrue(node_idx, recs)
        self.assertTrue(edge_idx, recs)
        self.assertLess(max(node_idx), min(edge_idx),
                        "an edge line was emitted before a node line")


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
            rc = mod.main(["--known-slugs", "routing-d-085,routing-d-088,routing-d-100",
                           "--load"], root=root,
                          env={"OMNIGRAPH_TOKEN": "test-token",
                               "OMNIGRAPH_BASE_URL": "http://localhost:1"})
        finally:
            mod.post_load = orig
        self.assertNotEqual(rc, 0)
        if ledger.exists():
            self.assertEqual(ledger.read_text().split(), [])
        # Nothing was marked, so the full batch is still pending.
        self.assertEqual(len(mod.emit(root, ledger)), len(pending))

    def test_empty_tables_response_with_edge_only_batch_is_not_confirmed(self):
        """No per-table detail is no evidence. A node batch may be re-sent
        idempotently; an edge-only batch would duplicate, so it is not marked."""
        mod = load_script()
        self.assertFalse(mod.load_confirmed({"tables": []}, 1, edge_only=1))

    def test_empty_tables_response_with_node_only_batch_stays_permissive(self):
        """Preserve prior behavior when the batch carries no edge-only record."""
        mod = load_script()
        self.assertTrue(mod.load_confirmed({"tables": []}, 1, edge_only=0))

    def test_edge_only_batch_with_empty_tables_response_does_not_mark(self):
        """Integration: an edge-only batch plus `{"tables": []}` must not mark."""
        mod = load_script()
        root = fixture_root()
        ledger = root / ".state" / "graph-loaded.txt"
        node_keys = [k for k, n, _ in mod.records(root) if n is not None]
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("\n".join(node_keys) + "\n", encoding="utf-8")
        before = set(ledger.read_text().split())
        pending = mod.emit(root, ledger)
        self.assertTrue(pending)
        self.assertTrue(all(node is None for _, node, _ in pending), pending)
        orig = mod.post_load
        mod.post_load = lambda *a, **k: {"tables": []}
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = mod.main(
                    ["--known-slugs", "routing-d-085,routing-d-088,routing-d-100",
                     "--load"], root=root, env={"OMNIGRAPH_TOKEN": "t"})
        finally:
            mod.post_load = orig
        self.assertNotEqual(rc, 0)
        self.assertIn("NOT confirmed", err.getvalue())
        self.assertEqual(set(ledger.read_text().split()), before)


class LoadConfirm013Tests(unittest.TestCase):
    """D-1038 A3: the 0.13.0 load response shape, and the contract header."""

    KNOWN = "routing-d-085,routing-d-088,routing-d-100"

    def _batch(self, mod, root):
        """(records, POSTed lines) exactly as --load would send them."""
        pending = mod.emit(root, root / ".state" / "graph-loaded.txt")
        return pending, (sum(1 for _, n, _ in pending if n is not None)
                         + sum(len(es) for _, _, es in pending))

    def test_total_entities_equal_sent_is_confirmed(self):
        """Every sent record landed, so the no-@key edges flowed once."""
        mod = load_script()
        self.assertTrue(mod.load_confirmed(NEW_LOAD_OK, 1, edge_only=0))
        self.assertTrue(mod.load_confirmed(NEW_LOAD_OK, 1, edge_only=1))
        self.assertTrue(mod.load_confirmed(NEW_LOAD_126, 126, edge_only=12))

    def test_total_entities_other_than_sent_is_not_confirmed(self):
        """Fewer is a partial load, more is not our batch, zero loaded nothing."""
        mod = load_script()
        cases = [(dict(NEW_LOAD_OK, total_entities=0), 1, 0),
                 (dict(NEW_LOAD_OK, total_entities=0), 1, 1),
                 (dict(NEW_LOAD_OK, total_entities=3), 4, 1),
                 (NEW_LOAD_OK, 0, 0),
                 (NEW_LOAD_126, 127, 12)]
        for res, sent, edge_only in cases:
            with self.subTest(sent=sent, total_entities=res["total_entities"]):
                self.assertFalse(mod.load_confirmed(res, sent, edge_only=edge_only))

    def test_flat_error_response_is_not_confirmed(self):
        """0.13 errors are flat HTTP 400 bodies, not a per-table list."""
        mod = load_script()
        err = {"error": "__src 'no-such-decision' not found in Decision",
               "code": "bad_request"}
        self.assertFalse(mod.load_confirmed(err, 1, edge_only=0))
        # A non-empty error outranks a matching total_entities.
        self.assertFalse(mod.load_confirmed(
            dict(NEW_LOAD_OK, error="load failed"), 1, edge_only=0))

    def test_unknown_shape_fails_closed(self):
        """Neither `tables` nor `total_entities` is no evidence: never the
        permissive node-only path (a re-sent edge batch would duplicate)."""
        mod = load_script()
        self.assertFalse(mod.load_confirmed({"unexpected": 1}, 1, edge_only=0))
        self.assertFalse(mod.load_confirmed({}, 1, edge_only=0))

    def test_log_line_for_new_shape_does_not_raise_and_marks(self):
        """Integration: --load against a 0.13 response prints per-type counts
        (never a KeyError) and marks the ledger."""
        mod = load_script()
        root = fixture_root()
        pending, sent = self._batch(mod, root)
        self.assertGreater(sent, 0)
        res = dict(NEW_LOAD_OK, total_entities=sent,
                   nodes=[{"name": "Task", "entities_loaded": sent}],
                   edges=[{"name": "Implements", "entities_loaded": 2}])
        orig = mod.post_load
        mod.post_load = lambda *a, **k: res
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = mod.main(["--known-slugs", self.KNOWN, "--load"],
                              root=root, env={"OMNIGRAPH_TOKEN": "t"})
        finally:
            mod.post_load = orig
        self.assertEqual(rc, 0)
        text = err.getvalue()
        self.assertNotIn("Traceback", text)
        self.assertIn("loaded: {'Task': %d, 'Implements': 2}" % sent, text)
        self.assertIn("marked %d records" % len(pending), text)
        ledger = root / ".state" / "graph-loaded.txt"
        self.assertEqual(set(ledger.read_text().split()),
                         {k for k, _, _ in pending})

    def test_new_shape_that_missed_records_leaves_ledger_untouched(self):
        """A partial 0.13 load must not mark, or the missing edges are lost."""
        mod = load_script()
        root = fixture_root()
        pending, sent = self._batch(mod, root)
        ledger = root / ".state" / "graph-loaded.txt"
        orig = mod.post_load
        mod.post_load = lambda *a, **k: dict(NEW_LOAD_OK, total_entities=sent - 1)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = mod.main(["--known-slugs", self.KNOWN, "--load"],
                              root=root, env={"OMNIGRAPH_TOKEN": "t"})
        finally:
            mod.post_load = orig
        self.assertNotEqual(rc, 0)
        self.assertIn("NOT confirmed", err.getvalue())
        self.assertFalse(ledger.exists() and ledger.read_text().split())
        self.assertEqual(len(mod.emit(root, ledger)), len(pending))

    def test_load_summary_never_raises_on_any_dict(self):
        mod = load_script()
        for res in (NEW_LOAD_OK, NEW_LOAD_126, {"unexpected": 1}, {},
                    {"tables": []}, {"tables": [{"rows_loaded": 1}]},
                    {"tables": ["nonsense"]}, {"nodes": [{"name": "Rule"}]},
                    {"nodes": None}, {"nodes": "x", "edges": None},
                    None, "not-a-dict"):
            with self.subTest(res=res):
                self.assertIsInstance(mod.load_summary(res), dict)

    def test_contract_header_constants(self):
        mod = load_script()
        self.assertEqual(mod.CONTRACT_HEADER, "omnigraph-http-api")
        self.assertEqual(mod.CONTRACT_VERSION, "0.13")


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

    def test_bare_decision_number_is_zero_padded(self):
        """D-88 and routing-d-88 are the same decision, keyed routing-d-088."""
        self.assertEqual(self.mod.routing_citations("D-88"), ["routing-d-088"])
        self.assertEqual(self.mod.routing_citations("routing-d-88"), ["routing-d-088"])
        self.assertEqual(self.mod.routing_citations("D-8"), ["routing-d-008"])

    def test_d_number_and_routing_slug_share_one_number_space(self):
        """Design note, not a bug: AutoOS D-NNN ids ARE the router's routing
        decision numbers, so a bare D-NNN and routing-d-NNN name the *same*
        decision and both normalise to one slug -- there are not two ledgers
        with two number spaces that could collide."""
        self.assertEqual(self.mod.routing_citations("D-088"),
                         self.mod.routing_citations("routing-d-088"))
        self.assertEqual(self.mod.routing_citations("D-088"), ["routing-d-088"])
        self.assertEqual(
            self.mod.routing_citations("superseding D-88 equals routing-d-088"),
            ["routing-d-088"])

    def test_supersedes_targets_ignores_a_later_sentence_citation(self):
        """A citation after the clause ends is not a replacement."""
        self.assertEqual(
            self.mod.supersedes_targets("superseding the interim cap. D-085 stays live"),
            [])
        self.assertEqual(
            self.mod.supersedes_targets("replaced by the new cap; D-040 is unrelated"),
            [])
        self.assertEqual(
            self.mod.supersedes_targets("superseding the cap\nD-085 is unrelated"),
            [])

    def test_supersedes_targets_data_driven_from_fixture_text(self):
        """Table of fixture texts -> expected replacements, independent of the
        module's DECISIONS constants."""
        cases = [
            ("superseding D-085's interim 250k", ["routing-d-085"]),
            ("replaces routing-d-040 in the plan", ["routing-d-040"]),
            ("superseded D-88 before the re-key", ["routing-d-088"]),
            ("implements routing-d-040", []),
            ("no replacement here", []),
            ("superseding the old cap. D-085 remains", []),
            ("replaced the interim cap; " + "x" * 80 + " D-085", []),
            ("superseding the cap\nD-085 remains", []),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.mod.supersedes_targets(text), expected)


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


class RecordDedupeTests(unittest.TestCase):
    def test_colliding_truncated_titles_emit_each_ledger_key_once(self):
        """Two board rows whose titles collide once truncated to 60 chars mint
        the same Task slug; records() emits that slug and its Implements edge
        once, not once per row."""
        mod = load_script()
        shared = "collide shared prefix " * 4  # >60 chars once normalised
        root = fixture_root_with_tasks([
            f"| {shared}alpha (D-088) | o | 2026-09-28 | green | Running |",
            f"| {shared}beta (D-088) | o | 2026-09-28 | green | Running |",
        ])
        batch = mod.records(root, known={"routing-d-088"})
        keys = [k for k, _, _ in batch]
        self.assertEqual(len(keys), len(set(keys)), "duplicate ledger key emitted")
        impl = [k for k in keys if k.startswith("Implements:")]
        self.assertEqual(len(impl), len(set(impl)))
        self.assertEqual(sum(k.endswith("->routing-d-088") for k in impl), 1)


class KnownSlugsTests(unittest.TestCase):
    """--known-slugs accepts - (stdin), a file, or =VALUE, and rejects junk."""

    def setUp(self):
        self.mod = load_script()
        self.root = fixture_root()

    def _targets(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.mod.main(list(argv), root=self.root, env={})
        self.assertEqual(rc, 0)
        recs = [json.loads(line) for line in out.getvalue().splitlines()]
        return {r["to"] for r in recs
                if r.get("edge") in ("Implements", "Supersedes")}

    def test_dash_reads_slugs_from_stdin(self):
        old = sys.stdin
        sys.stdin = io.StringIO("routing-d-100\n")
        try:
            targets = self._targets(["--known-slugs", "-"])
        finally:
            sys.stdin = old
        self.assertEqual(targets, {"routing-d-100"})

    def test_file_path_reads_slugs(self):
        path = self.root / "known-slugs.txt"
        path.write_text("routing-d-100\nrouting-d-085\n", encoding="utf-8")
        self.assertEqual(self._targets(["--known-slugs", str(path)]),
                         {"routing-d-100", "routing-d-085"})

    def test_equals_form_reads_slugs(self):
        self.assertEqual(self._targets(["--known-slugs=routing-d-100"]),
                         {"routing-d-100"})

    def test_missing_value_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.mod._take_known_slugs(["--known-slugs"])

    def test_option_as_value_is_an_error_and_does_not_swallow_load(self):
        """`--known-slugs --load` must not consume --load as a slug value."""
        with self.assertRaises(SystemExit):
            self.mod._take_known_slugs(["--known-slugs", "--load"])

    def test_empty_equals_value_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.mod._take_known_slugs(["--known-slugs="])


class LoadGuardTests(unittest.TestCase):
    """D-140 review: an unvalidated edge must never be loaded silently."""

    KNOWN = "routing-d-085,routing-d-088,routing-d-100"

    def _ledger_untouched(self, root):
        ledger = root / ".state" / "graph-loaded.txt"
        return not (ledger.exists() and ledger.read_text().split())

    def test_load_without_known_slugs_refuses_an_edge_batch(self):
        """--load with no --known-slugs must refuse (non-zero, nothing marked)
        when the emitted batch carries an edge-only record."""
        mod = load_script()
        root = fixture_root()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = mod.main(["--load"], root=root,
                          env={"OMNIGRAPH_TOKEN": "t",
                               "OMNIGRAPH_BASE_URL": "http://127.0.0.1:1"})
        self.assertNotEqual(rc, 0)
        self.assertIn("--known-slugs", err.getvalue())
        self.assertTrue(self._ledger_untouched(root))

    def test_dump_without_known_slugs_still_emits_every_citation(self):
        """Plain dump mode keeps the permissive default (no filtering)."""
        mod = load_script()
        root = fixture_root()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mod.main([], root=root, env={})
        self.assertEqual(rc, 0)
        recs = [json.loads(line) for line in out.getvalue().splitlines()]
        targets = {r["to"] for r in recs if r.get("edge") == "Implements"}
        self.assertIn("routing-d-088", targets)

    def test_http_error_becomes_a_clear_message_not_a_traceback(self):
        mod = load_script()
        root = fixture_root()
        orig = mod.post_load

        def boom(*a, **k):
            raise urllib.error.HTTPError(
                "http://127.0.0.1:1/graphs/autoos/load", 500,
                "Internal Server Error", {}, None)

        mod.post_load = boom
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = mod.main(["--known-slugs", self.KNOWN, "--load"],
                              root=root, env={"OMNIGRAPH_TOKEN": "t"})
        finally:
            mod.post_load = orig
        self.assertNotEqual(rc, 0)
        self.assertIn("500", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertTrue(self._ledger_untouched(root))

    def test_unreachable_server_becomes_a_clear_message(self):
        mod = load_script()
        root = fixture_root()
        orig = mod.post_load

        def boom(*a, **k):
            raise urllib.error.URLError("connection refused")

        mod.post_load = boom
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = mod.main(["--known-slugs", self.KNOWN, "--load"],
                              root=root, env={"OMNIGRAPH_TOKEN": "t"})
        finally:
            mod.post_load = orig
        self.assertNotEqual(rc, 0)
        self.assertIn("connection refused", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertTrue(self._ledger_untouched(root))


class MarkerTests(unittest.TestCase):
    """D-1038 P2g: the ``--load`` attempt marker. A load whose answer is lost or
    ambiguous may still have landed; nodes dedupe by @key slug but 0.13 merge does
    not dedupe edges, so a blind re-send duplicates every edge in the batch."""

    KNOWN = "routing-d-085,routing-d-088,routing-d-100"

    def setUp(self):
        self.mod = load_script()
        self.root = fixture_root()
        self.ledger = self.root / ".state" / "graph-loaded.txt"
        self.marker = self.root / ".state" / "graph-load-pending"
        pending = self.mod.emit(self.root, self.ledger)
        self.keys = [k for k, _, _ in pending]
        self.sent = (sum(1 for _, n, _ in pending if n is not None)
                     + sum(len(es) for _, _, es in pending))
        self.assertGreater(self.sent, 1, "fixture batch needs nodes and edges")

    def _run(self, argv, post_load, env=None):
        orig = self.mod.post_load
        self.mod.post_load = post_load
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = self.mod.main(["--known-slugs", self.KNOWN] + list(argv),
                                   root=self.root,
                                   env=env if env is not None else {"OMNIGRAPH_TOKEN": "t"})
        finally:
            self.mod.post_load = orig
        return rc, err.getvalue()

    def _confirmed(self, *args):
        return dict(NEW_LOAD_OK, total_entities=self.sent)

    def _never(self, *args, **kwargs):
        raise AssertionError("post_load must not be called")

    def _url_error(self, *args, **kwargs):
        raise urllib.error.URLError("the read operation timed out")

    def _http_error(self, code):
        def boom(*args, **kwargs):
            raise urllib.error.HTTPError(
                "http://127.0.0.1:1/graphs/autoos/load", code, "reason", {}, None)
        return boom

    def test_confirmed_load_leaves_no_marker_and_marks_ledger(self):
        """(a) A confirmed load is done: ledger marked, marker gone."""
        rc, err = self._run(["--load"], self._confirmed)
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.marker.exists(), err)
        self.assertEqual(set(self.ledger.read_text().split()), set(self.keys))

    def test_lost_answer_writes_marker_with_the_batch_it_sent(self):
        """(b) The answer may have landed: attempt marked, ledger untouched."""
        rc, err = self._run(["--load"], self._url_error)
        self.assertEqual(rc, 1, err)
        self.assertTrue(self.marker.exists())
        data = json.loads(self.marker.read_text())
        self.assertEqual(data["lines"], self.sent)
        self.assertEqual(data["keys"], self.keys)
        self.assertFalse(self.ledger.exists() and self.ledger.read_text().split())

    def test_marker_present_refuses_second_load_without_posting(self):
        """(c) No blind re-send: nothing is sent, the marker is untouched."""
        self._run(["--load"], self._url_error)
        rc, err = self._run(["--load"], self._never)
        self.assertEqual(rc, 1)
        self.assertIn("did not finish cleanly", err)
        self.assertIn("--clear-pending", err)
        self.assertIn(str(self.sent) + " lines", err)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.ledger.exists() and self.ledger.read_text().split())

    def test_missing_token_sends_nothing_and_writes_no_marker(self):
        """(c2) A missing token fails before the marker — no request could land,
        so a later --load must not refuse on a stale attempt."""
        rc, err = self._run(["--load"], self._never, env={})
        self.assertEqual(rc, 1, err)
        self.assertIn("OMNIGRAPH_TOKEN is not set", err)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.ledger.exists() and self.ledger.read_text().split())

    def test_client_error_clears_marker_server_error_keeps_it(self):
        """(d) 4xx rejected the whole load, so a retry is safe; 5xx says nothing."""
        rc, err = self._run(["--load"], self._http_error(400))
        self.assertEqual(rc, 1, err)
        self.assertFalse(self.marker.exists())
        rc, err = self._run(["--load"], self._http_error(503))
        self.assertEqual(rc, 1, err)
        self.assertTrue(self.marker.exists())

    def test_unconfirmed_response_keeps_the_marker(self):
        """(e) total_entities != sent is ambiguous: not marked, not re-sent."""
        rc, err = self._run(["--load"],
                            lambda *a: dict(NEW_LOAD_OK, total_entities=self.sent - 1))
        self.assertEqual(rc, 1, err)
        self.assertIn("NOT confirmed", err)
        self.assertTrue(self.marker.exists())

    def test_clear_pending_then_load_proceeds(self):
        """(f) A load that did not land: drop the marker, no network, then load."""
        self._run(["--load"], self._url_error)
        rc, err = self._run(["--clear-pending"], self._never)
        self.assertEqual(rc, 0, err)
        self.assertIn("cleared pending marker", err)
        self.assertFalse(self.marker.exists())
        rc, err = self._run(["--load"], self._confirmed)
        self.assertEqual(rc, 0, err)
        self.assertEqual(set(self.ledger.read_text().split()), set(self.keys))
        self.assertFalse(self.marker.exists())

    def test_clear_pending_without_a_marker_is_a_noop(self):
        rc, err = self._run(["--clear-pending"], self._never)
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.marker.exists())

    def test_mark_with_marker_marks_and_clears_nothing_sent(self):
        """(g) A load that did land: --mark records the batch and clears the marker."""
        self._run(["--load"], self._url_error)
        rc, err = self._run(["--mark"], self._never)
        self.assertEqual(rc, 0, err)
        self.assertIn("marked", err)
        self.assertEqual(set(self.ledger.read_text().split()), set(self.keys))
        self.assertFalse(self.marker.exists())

    def test_malformed_marker_still_blocks_a_load(self):
        """(h) A marker we cannot parse is still evidence of an attempt."""
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_bytes(b"not json")
        rc, err = self._run(["--load"], self._never)
        self.assertEqual(rc, 1)
        self.assertIn("did not finish cleanly", err)
        self.assertEqual(self.marker.read_bytes(), b"not json")

    def test_print_mode_with_marker_prints_and_succeeds(self):
        """(i) Only --load refuses; a dump run needs no reconciliation."""
        self._run(["--load"], self._url_error)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = self.mod.main([], root=self.root, env={})
        self.assertEqual(rc, 0)
        self.assertTrue([json.loads(line) for line in out.getvalue().splitlines()])
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
