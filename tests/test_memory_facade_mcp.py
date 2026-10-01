"""MEMSPEC P1 — the five facade methods, their write rules and their receipts.

Contract under test (docs/plans/2026-09-28-memory-facade-spec.md):

- §4 table (lines 62-68): `recall` / `context_pack` / `remember` / `link` /
  `supersede`, each with the contract the row states.
- §4 write rules (70-75): every node links to a hub, relations come from a
  closed list, a body is at most 600 characters, a secret scan runs.
- §4 returns (77-80): entity id, the NEW version_id, author session name +
  restart generation id, source; superseded versions stay readable.
- §4 read path (82-91): recall ranks by text + graph relevance (hub distance
  counts) and fits 600 tokens; `context_pack` fits 1.5k and records the ids +
  versions it served (D-042 provenance, §12).
- §11 P1 exit (line 217): the five methods round-trip.
- §13 D-067 (255-260): the engine stays swappable behind the facade.

The tests drive the module-level functions the MCP `serve()` wrappers call, so
a green run is evidence about the production path, not a test-only helper.

Run from the repo root:

    python3 tests/test_memory_facade_mcp.py
"""
import importlib.util
import inspect
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
MODULE = TOOLS / "memory_facade_mcp.py"
FIXTURE = ROOT / "tests" / "fixtures" / "memory" / "graph.json"

ENV_KEYS = ("AUTOOS_MEMORY_STORE", "AUTOOS_MEMORY_EVENTS",
            "AUTOOS_MEMORY_SESSION", "AUTOOS_MEMORY_GEN",
            "AUTOOS_MEMORY_ACTOR_KIND")


def load_module(name):
    """Load a tools/*.py module the way the suites load the other helpers: by
    path, with tools/ on sys.path so sibling imports resolve."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


facade_mod = load_module("memory_facade_mcp")
engine_mod = facade_mod.engine_mod  # tools/memory_facade_engine.py, the seam


class FacadeCase(unittest.TestCase):
    """Temp store (seeded from the R0 fixture) + temp events file + the env the
    author/gen fields come from (spec §4 returns, D-042)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore_env)
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self.store = Path(self.tmp.name) / "graph.json"
        self.events = Path(self.tmp.name) / "events.ndjson"
        os.environ["AUTOOS_MEMORY_STORE"] = str(self.store)
        os.environ["AUTOOS_MEMORY_EVENTS"] = str(self.events)
        os.environ["AUTOOS_MEMORY_SESSION"] = "test-session"
        os.environ["AUTOOS_MEMORY_GEN"] = "3"
        os.environ.pop("AUTOOS_MEMORY_ACTOR_KIND", None)
        shutil.copy(FIXTURE, self.store)
        # the module functions cache one facade per process (an MCP server
        # keeps one graph); each test needs its own.
        facade_mod._FACADE = None
        self.addCleanup(lambda: setattr(facade_mod, "_FACADE", None))

    def _restore_env(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def empty_store_facade(self):
        """A facade over a store that does not exist yet (no fixture)."""
        os.environ["AUTOOS_MEMORY_STORE"] = str(Path(self.tmp.name) / "fresh.json")
        return facade_mod.MemoryFacade()


class FiveMethodsRoundTripTests(FacadeCase):
    """The §11 P1 exit: five methods round-trip through one call chain."""

    def test_p1_exit_five_methods_round_trip(self):
        written = facade_mod.remember(
            kind="decision", title="Facade round trip",
            body="The five methods were exercised in one call chain.",
            project="acme",
            links=[{"rel": "about", "id": "project:acme"}],
            source="test-fixture")
        self.assertEqual(written["id"], "decision:facade-round-trip")
        self.assertEqual(written["status"], "created")
        self.assertEqual(written["version_id"], 1)

        hit = facade_mod.recall("round trip", project="acme")
        self.assertTrue(any("decision:facade-round-trip" in line
                            for line in hit["lines"]), hit["lines"])

        edge = facade_mod.link("decision:facade-round-trip", "about",
                               "technology:postgresql")
        self.assertFalse(edge["noop"])
        again = facade_mod.link("decision:facade-round-trip", "about",
                                "technology:postgresql")
        self.assertTrue(again["noop"])
        self.assertEqual(edge["edge_id"], again["edge_id"])

        closed = facade_mod.supersede("decision:facade-round-trip",
                                      "decision:restore-drill",
                                      "answered by the restore drill decision")
        self.assertEqual(closed["status"], "superseded")
        self.assertEqual(closed["superseded_by"], "decision:restore-drill")
        self.assertTrue(closed["valid_to"])

        pack = facade_mod.context_pack("acme", "implement")
        self.assertLessEqual(pack["tokens"], engine_mod.PACK_TOKENS)
        self.assertIn("decision:facade-round-trip",
                      [c["id"] for c in pack["open_contradictions"]])

        after = facade_mod.recall("round trip", project="acme")
        self.assertFalse(any("decision:facade-round-trip" in line
                             for line in after["lines"]), after["lines"])

    def test_the_mcp_server_advertises_exactly_the_five_spec_methods(self):
        source = MODULE.read_text(encoding="utf-8")
        names = set(re.findall(r'@app\.tool\(name="([^"]+)"\)', source))
        self.assertEqual(names, {"recall", "context_pack", "remember", "link",
                                 "supersede"})

    def test_the_module_functions_carry_the_spec_signatures(self):
        want = {
            "recall": ["query", "project", "k"],
            "context_pack": ["project", "role"],
            "remember": ["kind", "title", "body", "project", "links", "source"],
            "link": ["a", "rel", "b"],
            "supersede": ["old", "new", "reason"],
        }
        for name, params in want.items():
            got = list(inspect.signature(getattr(facade_mod, name)).parameters)
            self.assertEqual(got[:len(params)], params, name)


class RememberTests(FacadeCase):
    def test_returns_id_version_author_gen_and_source(self):
        rec = facade_mod.remember(kind="lesson", title="Idempotent imports",
                                  body="Merge-loads of the same seed duplicate it.",
                                  project="acme",
                                  links=[{"rel": "about", "id": "project:acme"}],
                                  source="test-fixture")
        self.assertEqual(rec["id"], "lesson:idempotent-imports")
        self.assertEqual(rec["version_id"], 1)
        self.assertEqual(rec["author"], "test-session")
        self.assertEqual(rec["gen"], "3")
        self.assertEqual(rec["source"], "test-fixture")

    def test_the_same_canonical_id_upserts_and_bumps_the_version(self):
        first = facade_mod.remember(kind="preference", title="Small commits",
                                    body="Commit per task.", project="acme",
                                    links=[{"rel": "about",
                                            "id": "project:acme"}],
                                    source="s")
        second = facade_mod.remember(kind="preference", title="Small commits",
                                     body="Commit per task, then log it.",
                                     project="acme",
                                     links=[{"rel": "about",
                                             "id": "project:acme"}],
                                     source="s")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["status"], "updated")
        self.assertEqual(second["version_id"], 2)
        # superseded versions stay readable (spec:79): the first version's
        # content is still there after the update.
        node = facade_mod.MemoryFacade().engine.get(second["id"])
        self.assertEqual(node["versions"][0]["body"], "Commit per task.")
        self.assertEqual(len(node["versions"]), 2)

    def test_a_bit_identical_rewrite_is_a_no_op(self):
        args = dict(kind="preference", title="Small commits",
                    body="Commit per task.", project="acme",
                    links=[{"rel": "about", "id": "project:acme"}],
                    source="s")
        facade_mod.remember(**args)
        again = facade_mod.remember(**args)
        self.assertEqual(again["status"], "noop")
        self.assertEqual(again["version_id"], 1)

    def test_title_variants_merge_into_the_survivor(self):
        facade_mod.remember(kind="decision", title="Postgres backup policy",
                            body="Back up Postgres nightly.", project="acme",
                            links=[{"rel": "about", "id": "project:acme"}],
                            source="s")
        merged = facade_mod.remember(kind="decision",
                                     title="Backup policy, postgres",
                                     body="Back up Postgres nightly.",
                                     project="acme",
                                     links=[{"rel": "about",
                                             "id": "project:acme"}],
                                     source="s")
        self.assertEqual(merged["status"], "merged")
        self.assertEqual(merged["merged_into"], "decision:postgres-backup-policy")
        self.assertEqual(merged["id"], "decision:postgres-backup-policy")
        self.assertIn("merged into decision:postgres-backup-policy",
                      merged.get("note", ""))
        # The dead id still answers with the survivor (spec:114: merged ids
        # stay resolvable).
        facade = facade_mod.MemoryFacade()
        self.assertEqual(facade.engine.resolve_alias("decision:backup-policy-postgres"),
                         "decision:postgres-backup-policy")

    def test_a_near_duplicate_title_is_found_by_similarity(self):
        facade_mod.remember(kind="decision",
                            title="Postgres nightly backup policy",
                            body="Back up Postgres nightly.", project="acme",
                            links=[{"rel": "about", "id": "project:acme"}],
                            source="s")
        merged = facade_mod.remember(kind="decision",
                                     title="Postgres nightly backup policy run",
                                     body="Back up Postgres nightly.",
                                     project="acme",
                                     links=[{"rel": "about",
                                             "id": "project:acme"}],
                                     source="s")
        self.assertEqual(merged["merged_into"],
                         "decision:postgres-nightly-backup-policy")


class WriteRuleTests(FacadeCase):
    def test_a_body_over_600_characters_is_refused(self):
        rec = facade_mod.remember(kind="lesson", title="Long body",
                                  body="x" * 601, project="acme",
                                  links=[{"rel": "about",
                                          "id": "project:acme"}],
                                  source="s")
        self.assertIn("at most 600", rec["error"])

    def test_a_title_over_120_characters_is_refused(self):
        rec = facade_mod.remember(kind="lesson", title="t" * 121, body="ok",
                                  project="acme",
                                  links=[{"rel": "about",
                                          "id": "project:acme"}],
                                  source="s")
        self.assertIn("<= 120", rec["error"])

    def test_a_secret_in_the_body_is_refused(self):
        rec = facade_mod.remember(kind="lesson", title="Deploy token",
                                  body="the deploy token = deadbeefcafe1234",
                                  project="acme",
                                  links=[{"rel": "about",
                                          "id": "project:acme"}],
                                  source="s")
        self.assertIn("secret", rec["error"])

    def test_a_remember_without_links_still_lands_on_a_hub(self):
        # The hub rule (spec:72) is enforced by construction: `remember`
        # attaches the node to its project hub, so every written node links
        # to a hub.
        rec = facade_mod.remember(kind="finding", title="No explicit links",
                                  body="The hub link is provided for me.",
                                  project="acme", links=[], source="s")
        facade = facade_mod.MemoryFacade()
        self.assertTrue(facade.engine.hub_attached(rec["id"]))
        hubs = {e["b"] for e in facade.engine.edges if e["a"] == rec["id"]}
        self.assertIn("project:acme", hubs)

    def test_a_refused_write_leaves_the_store_untouched(self):
        ok = facade_mod.remember(kind="finding", title="Persisted write",
                                 body="Proof the store is written at all.",
                                 project="acme",
                                 links=[{"rel": "about",
                                         "id": "project:acme"}],
                                 source="s")
        self.assertEqual(ok["status"], "created")
        after_ok = self.store.read_text(encoding="utf-8")
        self.assertNotEqual(after_ok,
                            Path(FIXTURE).read_text(encoding="utf-8"))
        facade_mod.remember(kind="lesson", title="Secret holder",
                            body="the secret = deadbeefcafe1234", project="acme",
                            links=[{"rel": "about", "id": "project:acme"}],
                            source="s")
        self.assertEqual(self.store.read_text(encoding="utf-8"), after_ok)

    def test_a_bare_id_link_is_refused(self):
        # links are {"rel", "id"} pairs everywhere (arbitration ruling 1): a
        # bare id does not satisfy spec:67's (a, rel, b) triple, so the write
        # is refused with an error — not coerced, not crashed into.
        rec = facade_mod.remember(kind="finding", title="Default relation",
                                  body="A bare id link.", project="acme",
                                  links=["project:acme"], source="s")
        self.assertIn("bare id strings", rec["error"])
        self.assertIn('{"rel"', rec["error"])

    def test_relation_types_come_from_a_closed_list(self):
        rec = facade_mod.link("decision:restore-drill", "enemies-with",
                              "component:ledger")
        self.assertIn("closed list", rec["error"])
        self.assertIn("enemies-with", rec["error"])

    def test_a_link_to_an_unknown_node_is_refused(self):
        rec = facade_mod.link("decision:restore-drill", "about",
                              "decision:does-not-exist")
        self.assertIn("unknown entity", rec["error"])

    def test_a_link_target_that_does_not_reach_a_hub_is_refused(self):
        # Seed the state a graph outside the facade can produce (an import
        # path, a bug): a node with no hub edge. The rule refuses it.
        facade = facade_mod.MemoryFacade()
        raw = facade.engine.add_node(kind="lesson", title="Orphan seed",
                                     body="No edges yet.", project="acme")
        rec = facade.link("decision:restore-drill", "about", raw["id"])
        self.assertIn("hub", rec["error"])

    def test_the_first_hub_boots_an_empty_store(self):
        facade = self.empty_store_facade()
        hub = facade.remember(kind="project", title="Solo", body="Root hub.",
                              project="solo", links=[], source="s")
        self.assertEqual(hub["id"], "project:solo")
        self.assertEqual(hub["version_id"], 1)
        leaf = facade.remember(kind="decision", title="Solo rule",
                               body="Rooted at the project hub.", project="solo",
                               links=[{"rel": "about", "id": "project:solo"}],
                               source="s")
        self.assertEqual(leaf["id"], "decision:solo-rule")
        self.assertTrue(facade.engine.hub_attached("decision:solo-rule"))


class LinkTests(FacadeCase):
    def test_link_is_idempotent_on_a_rel_b(self):
        first = facade_mod.link("decision:restore-drill", "about",
                                "component:ledger")
        self.assertFalse(first["noop"])
        self.assertEqual(first["version_id"], 1)
        repeat = facade_mod.link("decision:restore-drill", "about",
                                 "component:ledger")
        self.assertTrue(repeat["noop"])
        self.assertEqual(repeat["edge_id"], first["edge_id"])
        edges = [e for e in facade_mod.MemoryFacade().engine.edges
                 if (e["a"], e["rel"], e["b"]) ==
                 ("decision:restore-drill", "about", "component:ledger")]
        self.assertEqual(len(edges), 1)


class SupersedeTests(FacadeCase):
    def test_supersede_closes_the_old_fact_and_keeps_its_history(self):
        closed = facade_mod.supersede("decision:restore-drill",
                                      "decision:backup-window",
                                      "superseded by the backup window")
        self.assertEqual(closed["version_id"], 2)
        facade = facade_mod.MemoryFacade()
        node = facade.engine.get("decision:restore-drill")
        self.assertTrue(node["valid_to"])
        self.assertGreaterEqual(len(node["versions"]), 2)
        self.assertEqual(node["versions"][0]["title"],
                         "Restore drill from the nightly backup")

    def test_a_closed_fact_leaves_recall_but_stays_readable(self):
        facade_mod.supersede("decision:restore-drill", "decision:backup-window",
                             "superseded")
        hit = facade_mod.recall("restore drill", project="acme")
        self.assertFalse(any("decision:restore-drill" in line
                             for line in hit["lines"]), hit["lines"])
        node = facade_mod.MemoryFacade().engine.get("decision:restore-drill")
        self.assertEqual(node["id"], "decision:restore-drill")
        self.assertEqual(node["versions"][0]["body"],
                         "Run a restore drill every quarter against the "
                         "nightly backup.")

    def test_supersede_needs_both_facts_to_exist(self):
        missing = facade_mod.supersede("decision:restore-drill",
                                       "decision:nowhere", "reason")
        self.assertIn("unknown entity", missing["error"])
        self.assertFalse(missing.get("superseded_by"))


class RecallTests(FacadeCase):
    def test_recall_returns_compact_lines_with_ids_and_versions(self):
        hit = facade_mod.recall("backup", project="acme")
        self.assertTrue(hit["lines"])
        for line in hit["lines"]:
            self.assertRegex(
                line, r"^[a-z][a-z0-9_:-]+ \| [a-z][a-z0-9_-]* \| .+ \| v\d+$")
        self.assertLessEqual(hit["tokens"], engine_mod.RECALL_TOKENS)

    def test_recall_of_an_id_fetches_the_full_text(self):
        # spec:87 — agents fetch full text by id only when needed.
        hit = facade_mod.recall("decision:restore-drill")
        self.assertEqual(hit["mode"], "entity")
        self.assertEqual(hit["id"], "decision:restore-drill")
        self.assertIn("nightly backup", hit["body"])

    def test_recall_ranks_a_fact_one_hop_from_the_project_hub_above_a_far_one(self):
        # Both decisions match "backup"; restore-drill is 1 hop from
        # project:acme, backup-window is 3 (via technology -> domain).
        hit = facade_mod.recall("backup", project="acme")
        near = next(i for i, line in enumerate(hit["lines"])
                    if line.startswith("decision:restore-drill "))
        far = next(i for i, line in enumerate(hit["lines"])
                   if line.startswith("decision:backup-window "))
        self.assertLess(near, far)

    def test_recall_fits_the_600_token_budget(self):
        for i in range(40):
            facade_mod.remember(
                kind="finding",
                title=("Backup case %02d " % i) + " ".join(
                    "tok%02d%02d" % (i, j) for j in range(13)),
                body="Recorded while exercising the recall budget.",
                project="acme", links=[], source="s")
        hit = facade_mod.recall("backup", project="acme", k=40)
        self.assertLessEqual(hit["tokens"], engine_mod.RECALL_TOKENS)
        self.assertEqual(hit["tokens"],
                         engine_mod.estimate_tokens("\n".join(hit["lines"])))
        self.assertGreaterEqual(hit["count"], 1)
        self.assertLess(hit["count"], 40)


class ContextPackTests(FacadeCase):
    def test_the_pack_is_the_session_start_bundle_with_provenance(self):
        pack = facade_mod.context_pack("acme", "implement")
        self.assertEqual(pack["project"], "acme")
        self.assertEqual(pack["role"], "implement")
        facts = [f["id"] for f in pack["hub_facts"]]
        self.assertEqual(facts[0], "project:acme")
        # 1 hop from the hub only: technology and backup-window are 2-3 hops.
        self.assertIn("decision:restore-drill", facts)
        self.assertNotIn("decision:backup-window", facts)
        self.assertEqual([s["id"] for s in pack["served"]], facts)
        # versions are ints (arbitration choice 3); the recall LINES render
        # them as `v<n>`, the structured fields never do.
        self.assertTrue(all(isinstance(s["version_id"], int)
                            for s in pack["served"]))
        self.assertLessEqual(pack["tokens"], engine_mod.PACK_TOKENS)
        self.assertEqual(pack["tokens"], engine_mod.pack_tokens(pack))

    def test_open_contradictions_are_the_superseded_facts(self):
        facade_mod.supersede("decision:restore-drill", "decision:backup-window",
                             "the window answers it")
        pack = facade_mod.context_pack("acme", "review")
        open_ids = [c["id"] for c in pack["open_contradictions"]]
        self.assertEqual(open_ids, ["decision:restore-drill"])
        self.assertEqual(pack["open_contradictions"][0]["superseded_by"],
                         "decision:backup-window")
        facts = [f["id"] for f in pack["hub_facts"]]
        self.assertNotIn("decision:restore-drill", facts)

    def test_the_pack_fits_the_1500_token_budget(self):
        for i in range(40):
            facade_mod.remember(
                kind="finding",
                title=("Session start case %02d " % i) + " ".join(
                    "pck%02d%02d" % (i, j) for j in range(13)),
                body="Recorded while exercising the pack budget.",
                project="acme", links=[], source="s")
        pack = facade_mod.context_pack("acme", "implement")
        self.assertLessEqual(pack["tokens"], engine_mod.PACK_TOKENS)
        self.assertGreaterEqual(len(pack["hub_facts"]), 1)

    def test_recent_events_section_is_empty_before_any_write_emits_one(self):
        # tests/test_memory_events.py asserts the populated case; here the
        # section must exist and read an absent events file safely.
        pack = facade_mod.context_pack("acme", "implement")
        self.assertEqual(pack["recent_events"], [])


class DefaultsTests(FacadeCase):
    def test_the_store_file_default_is_the_repo_log_path(self):
        # File-backed store (arbitration choice 3): the R0 fixture is the
        # seed tests copy, never the live path; production defaults under
        # the git-ignored logs/ tree, symmetric with the events file.
        self.assertEqual(engine_mod.DEFAULT_STORE_REL,
                         "logs/memory/graph.json")
        os.environ.pop("AUTOOS_MEMORY_STORE", None)
        facade = facade_mod.MemoryFacade()
        self.assertTrue(str(facade.store_path).endswith(
            os.path.join("logs", "memory", "graph.json")))

    def test_the_events_file_default_is_the_repo_log_path(self):
        self.assertEqual(engine_mod.DEFAULT_EVENTS_REL,
                         "logs/memory-events/events.ndjson")
        os.environ.pop("AUTOOS_MEMORY_EVENTS", None)
        facade = facade_mod.MemoryFacade()
        self.assertTrue(str(facade.events_path).endswith(
            os.path.join("logs", "memory-events", "events.ndjson")))

    def test_reads_go_through_the_env_configured_default_facade(self):
        # Production path: the MCP wrappers call the module functions, which
        # build the facade from AUTOOS_MEMORY_STORE / AUTOOS_MEMORY_EVENTS.
        rec = facade_mod.remember(kind="finding", title="Env configured",
                                  body="Written through the default facade.",
                                  project="acme", links=[], source="s")
        self.assertEqual(rec["id"], "finding:env-configured")
        hit = facade_mod.recall("env configured", project="acme")
        self.assertTrue(any(rec["id"] in line for line in hit["lines"]))


if __name__ == "__main__":
    unittest.main()
