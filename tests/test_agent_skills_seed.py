"""Shape tests for the agent-skills seed (D-1038 A4).

Omnigraph 0.13 refuses `data.id` on load, so a node record must carry the
@key `slug` only; the id is derived from it. Stdlib, offline, no graph needed.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "infra" / "mcp-servers" / "cluster" / "seed" / "agent-skills.jsonl"

EXPECTED_NODES = 47
EXPECTED_EDGES = 79


def read_lines():
    """Non-empty NDJSON lines of the seed, in file order."""
    text = SEED.read_text(encoding="utf-8")
    return [line for line in text.split("\n") if line.strip()]


class AgentSkillsSeedTests(unittest.TestCase):
    def setUp(self):
        self.lines = read_lines()
        self.records = [json.loads(line) for line in self.lines]
        self.nodes = [r for r in self.records if "type" in r]
        self.edges = [r for r in self.records if "edge" in r]

    def test_record_counts(self):
        self.assertEqual(len(self.nodes), EXPECTED_NODES, "node records")
        self.assertEqual(len(self.edges), EXPECTED_EDGES, "edge records")
        self.assertEqual(
            len(self.nodes) + len(self.edges),
            len(self.records),
            "every record is either a node or an edge",
        )

    def test_nodes_carry_no_id(self):
        offenders = [
            f"{r['type']}/{r['data'].get('slug')}"
            for r in self.nodes
            if "id" in r or "id" in r.get("data", {})
        ]
        self.assertEqual(
            offenders,
            [],
            "nodes must not carry 'id' (0.13 refuses data.id; the id is "
            f"derived from the @key slug): {offenders}",
        )

    def test_node_slugs_present_and_unique_per_type(self):
        missing = [r["type"] for r in self.nodes if not str(r["data"].get("slug") or "").strip()]
        self.assertEqual(missing, [], f"nodes with an empty data.slug: {missing}")
        seen = {}
        for node in self.nodes:
            key = (node["type"], node["data"]["slug"])
            self.assertNotIn(key, seen, f"duplicate slug within a type: {key}")
            seen[key] = True

    def test_edges_are_slug_pairs(self):
        slugs = {r["data"]["slug"] for r in self.nodes}
        for edge in self.edges:
            self.assertEqual(
                set(edge),
                {"edge", "from", "to"},
                f"edge keys must be exactly edge/from/to: {edge}",
            )
            self.assertIn(edge["from"], slugs, f"unresolved edge.from: {edge}")
            self.assertIn(edge["to"], slugs, f"unresolved edge.to: {edge}")

    def test_lines_round_trip_byte_exact(self):
        for line in self.lines:
            self.assertEqual(json.dumps(json.loads(line)), line, "NDJSON line drift")


if __name__ == "__main__":
    unittest.main()
