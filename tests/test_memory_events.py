"""MEMSPEC P1 — every facade write emits a §5 `schema: 2` event (spec:93-121).

Contract under test (docs/plans/2026-09-28-memory-facade-spec.md §5, which
reproduces the envelope agreed with L2-general in
docs/plans/2026-09-28-fleet-console-spec.md §4.3):

- envelope (spec:100-110): ULID id, ISO-UTC-ms `at`, `schema: 2`,
  `version_id`, `gen`, `title` <=120, `actor {kind, id}`, `visibility`.
- type verbs (spec:102, 112-116): `memory.<entity_kind>.<created|updated|
  merged|superseded|redirected>`, edges get `memory.edge.<linked|unlinked>`;
  no `deleted` verb ever — an orphan prune is a superseded tombstone.
- `prev_version_id` rides only on `updated | superseded` (spec:105).
- a merge logs `merged` + `redirected` so merged ids stay resolvable
  (spec:114); a supersede logs the node event and the `supersedes` edge.
- no body and no secret in any event, only `title` (spec:117-118).
- append-only store: the file only grows, byte-prefix stable (spec:118).
- `context_pack.recent_events` reads the same file (spec:89).

The tests drive the module-level functions the MCP `serve()` wrappers call,
over a temp store (R0 fixture copy) + temp events file — nothing real.

Run from the repo root:

    python3 tests/test_memory_events.py
"""
import importlib.util
import json
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
    """Load a tools/*.py module the way the suites load the other helpers."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


facade_mod = load_module("memory_facade_mcp")
engine_mod = facade_mod.engine_mod

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class EventCase(unittest.TestCase):
    """Temp store (seeded from the R0 fixture) + temp events file + env."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore_env)
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self.store = Path(self.tmp.name) / "graph.json"
        self.events_path = Path(self.tmp.name) / "events.ndjson"
        os.environ["AUTOOS_MEMORY_STORE"] = str(self.store)
        os.environ["AUTOOS_MEMORY_EVENTS"] = str(self.events_path)
        os.environ["AUTOOS_MEMORY_SESSION"] = "test-session"
        os.environ["AUTOOS_MEMORY_GEN"] = "3"
        os.environ.pop("AUTOOS_MEMORY_ACTOR_KIND", None)
        shutil.copy(FIXTURE, self.store)
        facade_mod._FACADE = None
        self.addCleanup(lambda: setattr(facade_mod, "_FACADE", None))

    def _restore_env(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ─── the scripted write sequence (spec:112-116 verbs in one run) ─────

    def write_sequence(self):
        # 1. created — the auto hub link is an edge write of its own
        r1 = facade_mod.remember(kind="decision", title="Event seed fact",
                                 body="Seed body alpha.", project="acme",
                                 links=[{"rel": "about",
                                         "id": "project:acme"}],
                                 source="test-fixture")
        # 2. updated — same canonical id, changed body
        r2 = facade_mod.remember(kind="decision", title="Event seed fact",
                                 body="Seed body beta.", project="acme",
                                 links=[{"rel": "about",
                                         "id": "project:acme"}],
                                 source="test-fixture")
        # 3. merged + redirected — sorted-token title variant, other slug
        r3 = facade_mod.remember(kind="decision", title="Fact seed event",
                                 body="Seed body gamma.", project="acme",
                                 links=[{"rel": "about",
                                         "id": "project:acme"}],
                                 source="test-fixture")
        # 4. linked, then the identical link again: a no-op emits nothing
        e1 = facade_mod.link("decision:event-seed-fact", "about",
                             "technology:postgresql")
        e2 = facade_mod.link("decision:event-seed-fact", "about",
                             "technology:postgresql")
        # 5. superseded — plus the `supersedes` edge it writes
        s1 = facade_mod.supersede("decision:event-seed-fact",
                                  "decision:restore-drill", "answered by it")
        # 6. a refused write (secret scan) emits nothing
        refused = facade_mod.remember(kind="lesson", title="Refused event",
                                      body="token = deadbeefcafe1234",
                                      project="acme",
                                      links=[{"rel": "about",
                                              "id": "project:acme"}],
                                      source="test-fixture")
        return {"r1": r1, "r2": r2, "r3": r3, "e1": e1, "e2": e2,
                "s1": s1, "refused": refused}

    def read_events(self):
        self.assertTrue(self.events_path.exists(),
                        "the facade must create the events file")
        lines = [l for l in self.events_path.read_text(
            encoding="utf-8").splitlines() if l.strip()]
        return [json.loads(line) for line in lines]


class ScriptedSequenceTests(EventCase):
    """The append log must match one scripted run, entry for entry."""

    def setUp(self):
        super().setUp()
        self.seq = self.write_sequence()
        self.events = self.read_events()

    def test_the_scripted_sequence_emits_the_expected_types_in_order(self):
        types = [e["type"] for e in self.events]
        self.assertEqual(types, [
            "memory.decision.created",       # remember: the node
            "memory.edge.linked",            # remember: its auto hub link
            "memory.decision.updated",       # remember: changed body
            "memory.decision.merged",        # remember: duplicate absorbed
            "memory.decision.redirected",    # ... and its id kept resolvable
            "memory.edge.linked",            # link()
            "memory.decision.superseded",    # supersede(): the old fact
            "memory.edge.linked",            # supersede(): new --supersedes--> old
        ])

    def test_every_event_carries_the_schema_2_envelope(self):
        for e in self.events:
            self.assertRegex(e["id"], ULID_RE)
            self.assertRegex(e["at"], AT_RE)
            self.assertEqual(e["schema"], 2)
            self.assertEqual(e["project"], "acme")
            self.assertEqual(e["visibility"], "project")
            self.assertEqual(e["gen"], "3")
            self.assertEqual(e["actor"],
                             {"kind": "agent", "id": "test-session"})
            self.assertIn(e["actor"]["kind"], ("agent", "operator", "curator"))
            self.assertIsInstance(e["version_id"], int)
        ids = [e["id"] for e in self.events]
        self.assertEqual(len(ids), len(set(ids)), "ULIDs must be unique")

    def test_the_type_verbs_come_from_the_closed_lists_and_never_deleted(self):
        node_verbs = {"created", "updated", "merged", "superseded",
                      "redirected"}
        edge_verbs = {"linked", "unlinked"}
        for e in self.events:
            self.assertNotIn("deleted", e["type"])
            head, verb = e["type"].rsplit(".", 1)
            if head == "memory.edge":
                self.assertIn(verb, edge_verbs)
            else:
                self.assertEqual(head.split(".")[0], "memory")
                self.assertIn(head.split(".")[1], engine_mod.KINDS)
                self.assertIn(verb, node_verbs)

    def test_prev_version_id_rides_only_on_updated_and_superseded(self):
        with_prev = {e["type"].rsplit(".", 1)[-1] for e in self.events
                     if "prev_version_id" in e}
        self.assertEqual(with_prev, {"updated", "superseded"})
        updated = next(e for e in self.events
                       if e["type"].endswith(".updated"))
        self.assertEqual((updated["prev_version_id"],
                          updated["version_id"]), (1, 2))
        superseded = next(e for e in self.events
                          if e["type"].endswith(".superseded"))
        # created 1 -> updated 2 -> merged 3 -> superseded 4
        self.assertEqual((superseded["prev_version_id"],
                          superseded["version_id"]), (3, 4))

    def test_merged_and_redirected_record_the_absorbed_id(self):
        merged = next(e for e in self.events if e["type"].endswith(".merged"))
        redirected = next(e for e in self.events
                          if e["type"].endswith(".redirected"))
        for e in (merged, redirected):
            self.assertEqual(e["entity_id"], "decision:event-seed-fact")
            self.assertEqual(e["merged_from"], "decision:fact-seed-event")
            self.assertNotIn("prev_version_id", e)  # spec:105 — two verbs only
        self.assertEqual(merged["version_id"], 3)

    def test_the_superseded_event_names_the_fact_that_took_over(self):
        # Documented interpretation: §4.3's field list offers only
        # `supersedes`, so the closed fact's event carries the NEW id.
        superseded = next(e for e in self.events
                          if e["type"].endswith(".superseded"))
        self.assertEqual(superseded["entity_id"], "decision:event-seed-fact")
        self.assertEqual(superseded["supersedes"], "decision:restore-drill")
        self.assertEqual(superseded["entity_kind"], "decision")
        self.assertEqual(superseded["title"], "Event seed fact")

    def test_the_created_event_carries_entity_and_source_ref(self):
        created = next(e for e in self.events
                       if e["type"].endswith(".created"))
        self.assertEqual(created["entity_id"], "decision:event-seed-fact")
        self.assertEqual(created["entity_kind"], "decision")
        self.assertEqual(created["version_id"], 1)
        self.assertEqual(created["source_ref"], "test-fixture")
        self.assertLessEqual(len(created["title"]), 120)

    def test_edge_events_carry_the_edge_shape_and_no_node_text(self):
        edges = [e for e in self.events if e["type"].startswith("memory.edge.")]
        self.assertEqual(len(edges), 3)
        for e in edges:
            self.assertRegex(e["edge_id"], r"^e-[0-9a-f]{16}$")
            self.assertIn(e["rel"], engine_mod.CLOSED_RELATIONS)
            self.assertIsInstance(e["from_id"], str)
            self.assertIsInstance(e["to_id"], str)
            self.assertEqual(e["version_id"], 1)
            for absent in ("title", "entity_id", "entity_kind", "body",
                           "reason"):
                self.assertNotIn(absent, e)
        sup_edge = next(e for e in edges if e["rel"] == "supersedes")
        self.assertEqual(sup_edge["from_id"], "decision:restore-drill")
        self.assertEqual(sup_edge["to_id"], "decision:event-seed-fact")

    def test_an_event_carries_never_a_body_and_never_a_secret(self):
        text = self.events_path.read_text(encoding="utf-8")
        for body in ("Seed body alpha.", "Seed body beta.",
                     "Seed body gamma.", "deadbeefcafe1234"):
            self.assertNotIn(body, text)
        for e in self.events:
            self.assertNotIn("body", e)
        # the one pattern set: events pass it byte-identical
        self.assertEqual(facade_mod.Redactor().text(text), text)

    def test_a_refused_write_and_a_no_op_link_append_nothing(self):
        before = self.events_path.read_bytes()
        again = facade_mod.link("decision:event-seed-fact", "about",
                                "technology:postgresql")
        self.assertTrue(again["noop"])
        refused = facade_mod.remember(kind="lesson", title="Refused event",
                                      body="token = deadbeefcafe1234",
                                      project="acme",
                                      links=[{"rel": "about",
                                              "id": "project:acme"}],
                                      source="test-fixture")
        self.assertIn("secret", refused["error"])
        self.assertEqual(self.events_path.read_bytes(), before)

    def test_an_identical_remember_is_a_no_op_event(self):
        args = dict(kind="preference", title="Quiet preference",
                    body="Same every time.", project="acme",
                    links=[{"rel": "about", "id": "project:acme"}],
                    source="s")
        first = facade_mod.remember(**args)
        self.assertEqual(first["status"], "created")
        before = self.events_path.read_bytes()
        again = facade_mod.remember(**args)
        self.assertEqual(again["status"], "noop")
        self.assertEqual(self.events_path.read_bytes(), before)

    def test_the_events_store_is_append_only_with_a_byte_stable_prefix(self):
        prefix = self.events_path.read_bytes()
        facade_mod.remember(kind="finding", title="Later finding",
                            body="Written after the scripted run.",
                            project="acme",
                            links=[{"rel": "about", "id": "project:acme"}],
                            source="s")
        grown = self.events_path.read_bytes()
        self.assertTrue(grown.startswith(prefix),
                        "an append must never rewrite earlier entries")
        self.assertGreater(len(grown), len(prefix))

    def test_context_pack_recent_events_reads_the_same_file(self):
        pack = facade_mod.context_pack("acme", "review")
        types = [e["type"] for e in pack["recent_events"]]
        self.assertIn("memory.decision.merged", types)
        self.assertIn("memory.decision.redirected", types)
        self.assertIn("memory.decision.superseded", types)
        # spec:89 — the pack carries recent merged/superseded, not the
        # routine created/updated chatter.
        self.assertNotIn("memory.decision.created", types)
        self.assertNotIn("memory.decision.updated", types)


if __name__ == "__main__":
    unittest.main()
