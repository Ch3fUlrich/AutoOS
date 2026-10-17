#!/usr/bin/env python3
"""Graph store and read path behind the MEMSPEC memory facade (spec §4).

Split of responsibility with tools/memory_facade_mcp.py: the facade owns
write policy (the §4 rules, provenance, `schema: 2` events), this adapter
owns the graph and how a query becomes ranked compact lines. One graph, one
central writer (D-038).

The engine itself is deliberately swappable: D-067 (spec:255-260) names
Omnigraph as the default engine and facade-first means callers never see
which one runs. P1 runs this adapter over one JSON file — the R0 rung
(spec:224-226: prove the facade, events and runbook mechanics without
touching anything real) — and the P2 bake-off (spec §9) decides what
replaces the store behind these same methods.

Data shapes (one graph, canonical `kind:slug` ids, spec:66):

    nodes {id: {id, kind, title, body, project, domain?, visibility,
                version_id (int), versions (readable history, spec:79),
                valid_to (None | ISO)}}
    edges  {(a, rel, b): {edge_id, a, rel, b, version_id, reason?}}
           idempotent on (a, rel, b) — a repeat is a no-op (spec:67)
    alias  {merged_id: survivor_id} — a merged id resolves here (spec:79-80)

Store (arbitration choice 3): file-backed, written atomically (temp +
rename) after every successful facade write. Two load shapes are accepted —
the R0 fixture seed (`nodes` as a list whose `links` materialise into edges)
and the canonical saved shape (`nodes` as a dict + `edges` + `alias`).
Default path: `logs/memory/graph.json` (git-ignored); tests copy
`tests/fixtures/memory/graph.json` to a temp file and point
`AUTOOS_MEMORY_STORE` at it.

version_id is an int: every write mints the NEW one (spec:77), the previous
rides as `prev_version_id` on `updated`/`superseded` events (§5), and
`versions` keeps every prior snapshot readable (spec:79). Recall LINES render
the int as `v<n>`; structured fields never do.

Run the tests that pin this contract with:

    python3 tests/test_memory_facade_mcp.py
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from pathlib import Path

# The id vocabulary: the four hub kinds the spec's hub rule names (spec:72)
# plus the durable-content kinds of spec §3. Closed on purpose — a new kind
# is a reviewed change, not a caller typo.
HUB_KINDS = ("project", "domain", "host", "technology")
CONTENT_KINDS = ("decision", "lesson", "component", "preference", "finding")
KINDS = HUB_KINDS + CONTENT_KINDS

# spec:73 — relation types come from a closed list. The spec mandates the
# list and enumerates none (verified across spec:73/116, the fleet console
# spec's §4.3 and PLAN §14), so this is the minimal spec-grounded set:
#
#   about      — spec:72 "every node links to a hub", and fact -> topic
#   part_of    — spec:146 "shared Technology/Component nodes connect projects"
#   supersedes — spec:68/116, the one relation the spec writes out
#
# A new relation is a reviewed change (it enters the §5 event envelope's
# `rel` field), not a caller typo.
CLOSED_RELATIONS = ("about", "part_of", "supersedes")

# Alias-ladder similarity (spec:66 "exact -> normalised -> similarity").
# 0.85 on normalised titles only when both sides have >=3 words: short
# titles like "recall v1"/"recall v2" are different facts, not aliases.
ALIAS_SIMILARITY = 0.85
MIN_ALIAS_TOKENS = 3

# Read-path budgets (spec:64-65, 85, 90).
RECALL_TOKENS = 600
PACK_TOKENS = 1500

# Defaults, symmetric with each other; logs/ is git-ignored, so the live
# paths never pollute the checkout.
DEFAULT_STORE_REL = "logs/memory/graph.json"
DEFAULT_EVENTS_REL = "logs/memory-events/events.ndjson"


def slugify(text: str) -> str:
    """Canonical-id slug. Same shape as autoos-agent.py:752's slugify,
    kept local so importing the facade never drags in the whole agent CLI
    for one regex (the duplication is one line; the coupling is not)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "fact"


def tokens(text: str) -> list[str]:
    """Word tokens for matching, alias normalisation and query scoring."""
    return re.findall(r"[a-z0-9]+", text.lower())


def normalise(title: str) -> str:
    """Order-insensitive normalised title: the ladder's rung 2 compares
    normalised titles (spec:66), and "Backup policy, postgres" has to find
    "Postgres backup policy" — sorted tokens are what make that rung more
    than dead code."""
    return " ".join(sorted(tokens(title)))


def estimate_tokens(text: str) -> int:
    """~4 chars per token, rounded up: a budget check may over-count, it
    must never under-count (spec:64/65 state the budgets in tokens)."""
    return max(1, -(-len(text) // 4))


def pack_tokens(pack: dict) -> int:
    """Token cost of a context pack, ignoring its own `tokens` field so a
    pack can honestly assert `tokens == pack_tokens(pack)`."""
    body = {key: value for key, value in pack.items() if key != "tokens"}
    return estimate_tokens(json.dumps(body, sort_keys=True))


class MemoryEngine:
    """One file-backed graph behind the facade; swap this class, not the facade."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._nodes: dict[str, dict] = {}
        self._edges: dict[tuple, dict] = {}
        self._alias: dict[str, str] = {}
        if self.path is not None and self.path.exists():
            self.load(self.path)

    # ─── store persistence ───────────────────────────────────────────────

    def load(self, path) -> None:
        """Load either shape; the fixture's `links` materialise into edges
        (the fixture README's contract), the saved shape already has them."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_nodes = data.get("nodes", {})
        if isinstance(raw_nodes, list):
            pending_links = []
            for raw in raw_nodes:
                node = dict(raw)
                links = list(node.pop("links", []))
                node.setdefault("domain", None)
                node.setdefault("visibility", "project")
                node.setdefault("valid_to", None)
                node.setdefault("version_id", 1)
                if "versions" not in node:
                    node["versions"] = [{
                        "version_id": node["version_id"],
                        "title": node["title"],
                        "body": node.get("body", ""),
                        "valid_to": node["valid_to"],
                    }]
                self._nodes[node["id"]] = node
                pending_links.append((node["id"], links))
            for node_id, links in pending_links:
                for link in links:
                    # the hand-written shape is where drift enters: a bad
                    # relation here fails the load, loudly, not at read time
                    if link.get("rel") not in CLOSED_RELATIONS:
                        raise ValueError(
                            "fixture node %s: relation %r is not in the "
                            "closed list %s (spec:73)"
                            % (node_id, link.get("rel"), CLOSED_RELATIONS))
                    self.add_edge(node_id, link["rel"], link["id"])
        else:
            self._nodes = {nid: dict(n) for nid, n in raw_nodes.items()}
            for edge in data.get("edges", []):
                self._edges[(edge["a"], edge["rel"], edge["b"])] = dict(edge)
            self._alias = dict(data.get("alias", {}))

    def save(self) -> None:
        """Atomic snapshot: temp file + rename, so a reader never sees a
        half-written graph (spec:8 — one central writer, no torn states)."""
        if self.path is None:
            return
        payload = {"nodes": self._nodes,
                   "edges": list(self._edges.values()),
                   "alias": self._alias}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # ─── store access ────────────────────────────────────────────────────

    def get(self, node_id: str) -> dict | None:
        node = self._nodes.get(node_id)
        if node is None:
            return None
        return {**node, "versions": [dict(v) for v in node["versions"]]}

    @property
    def nodes(self) -> list[dict]:
        return [self.get(node_id) for node_id in self._nodes]

    @property
    def edges(self) -> list[dict]:
        return [dict(e) for e in self._edges.values()]

    @property
    def alias(self) -> dict:
        return dict(self._alias)

    def add_node(self, kind: str, title: str, body: str, project: str,
                 domain: str | None = None, visibility: str = "project",
                 node_id: str | None = None, valid_to: str | None = None) -> dict:
        node_id = node_id or f"{kind}:{slugify(title)}"
        if node_id in self._nodes:
            raise ValueError(f"node exists: {node_id}")
        self._nodes[node_id] = {
            "id": node_id, "kind": kind, "title": title, "body": body,
            "project": project, "domain": domain, "visibility": visibility,
            "version_id": 1, "valid_to": valid_to,
            "versions": [{"version_id": 1, "title": title, "body": body,
                          "valid_to": valid_to}],
        }
        return self.get(node_id)

    def bump(self, node_id: str, **updates) -> tuple[int, dict]:
        """Write new content onto a node and mint its NEW version (spec:77);
        the previous snapshot stays in `versions` (spec:79 — superseded
        versions stay readable, nothing is ever hard-deleted)."""
        node = self._nodes[node_id]
        prev = node["version_id"]
        node["version_id"] = prev + 1
        node.update(updates)
        node["versions"].append({"version_id": node["version_id"],
                                 "title": node["title"], "body": node["body"],
                                 "valid_to": node["valid_to"]})
        return prev, self.get(node_id)

    def close(self, node_id: str, when: str) -> tuple[int, dict]:
        """Close a fact (`valid_to`) as a tombstone version — superseded
        versions stay readable, nothing is ever hard-deleted (spec:79-80)."""
        node = self._nodes[node_id]
        if node["valid_to"]:
            raise ValueError(f"already closed: {node_id}")
        return self.bump(node_id, valid_to=when)

    def add_edge(self, a: str, rel: str, b: str,
                 reason: str | None = None) -> tuple[dict, bool]:
        """Idempotent on (a, rel, b): a repeat returns the existing edge."""
        if a not in self._nodes:
            raise ValueError(f"unknown entity: {a}")
        if b not in self._nodes:
            raise ValueError(f"unknown entity: {b}")
        key = (a, rel, b)
        existing = self._edges.get(key)
        if existing:
            return dict(existing), False
        edge_id = "e-" + hashlib.sha256(
            "|".join(key).encode("utf-8")).hexdigest()[:16]
        self._edges[key] = {"edge_id": edge_id, "a": a, "rel": rel, "b": b,
                            "version_id": 1, "reason": reason}
        return dict(self._edges[key]), True

    def has_edge(self, a: str, rel: str, b: str) -> bool:
        """Whether the triple already exists — the no-op check's lookup."""
        return (a, rel, b) in self._edges

    def set_alias(self, merged_id: str, survivor_id: str) -> None:
        """A merged id resolves to its survivor (spec:79-80)."""
        self._alias[merged_id] = survivor_id

    def resolve_alias(self, node_id: str) -> str | None:
        return self._alias.get(node_id)

    def hub_attached(self, node_id: str) -> bool:
        """spec:72 — a node counts when it IS a hub or links directly to one."""
        node = self._nodes.get(node_id)
        if node is None:
            return False
        if node["kind"] in HUB_KINDS:
            return True
        for (a, _rel, b) in self._edges:
            if a != node_id and b != node_id:
                continue
            other = b if a == node_id else a
            if self._nodes[other]["kind"] in HUB_KINDS:
                return True
        return False

    # ─── alias ladder (spec:66) ──────────────────────────────────────────

    def normalised_match(self, kind: str, project: str, norm_title: str):
        for node in sorted(self._nodes.values(), key=lambda n: n["id"]):
            if (node["kind"] == kind and node["project"] == project
                    and normalise(node["title"]) == norm_title):
                return self.get(node["id"])
        return None

    def similarity_match(self, kind: str, project: str, norm_title: str):
        if len(norm_title.split()) < MIN_ALIAS_TOKENS:
            return None
        best, best_ratio = None, ALIAS_SIMILARITY
        for node in sorted(self._nodes.values(), key=lambda n: n["id"]):
            if node["kind"] != kind or node["project"] != project:
                continue
            other = normalise(node["title"])
            if len(other.split()) < MIN_ALIAS_TOKENS:
                continue
            ratio = difflib.SequenceMatcher(None, norm_title, other).ratio()
            if ratio >= best_ratio:
                best, best_ratio = self.get(node["id"]), ratio
        return best

    # ─── read path (spec:82-91) ──────────────────────────────────────────

    def recall(self, query: str, project: str | None = None, k: int = 10) -> dict:
        """Compact lines for a query — or the full entity when the query IS
        an id (spec:87: agents fetch full text by id only when needed)."""
        node_id = str(query or "").strip()
        if node_id in self._nodes:
            node = self.get(node_id)
            return {"mode": "entity", "id": node["id"], "kind": node["kind"],
                    "title": node["title"], "body": node["body"],
                    "project": node["project"], "domain": node["domain"],
                    "version_id": node["version_id"],
                    "valid_to": node["valid_to"],
                    "versions": node["versions"]}
        survivor = self._alias.get(node_id)
        if survivor:
            return {"mode": "redirect", "id": node_id, "redirects_to": survivor}
        wanted = set(tokens(query or ""))
        distances = self._hub_distances(project) if project else {}
        scored = []
        for node in self._nodes.values():
            if node["valid_to"]:
                continue
            if project and node["project"] != project:
                continue
            found = len(wanted & set(tokens(node["title"]) + tokens(node["body"])))
            if not found:
                continue
            # spec:84 — hub distance counts: a fact one hop from the project
            # hub outranks a far one. 1/(1+dist) is a small positive bonus,
            # so text relevance still leads; unreachable nodes get none.
            dist = distances.get(node["id"])
            graph = 1.0 / (1 + dist) if dist is not None else 0.0
            scored.append((-(found + graph), node["id"], node))
        scored.sort()
        lines: list[str] = []
        for _score, _nid, node in scored[:k]:
            candidate = lines + ["%s | %s | %s | v%d" % (
                node["id"], node["kind"], node["title"], node["version_id"])]
            if estimate_tokens("\n".join(candidate)) > RECALL_TOKENS:
                # ranked order: what does not fit now will not fit later
                break
            lines = candidate
        return {"mode": "lines", "lines": lines, "count": len(lines),
                "tokens": estimate_tokens("\n".join(lines))}

    def context_pack(self, project: str, role: str) -> dict:
        """The graph half of the session-start bundle (spec:88): the project
        hub + its 1-hop neighbourhood, and the open contradictions
        (superseded-but-unresolved, with the fact that superseded them).
        The facade layers `recent_events`, the served provenance and the
        1.5k budget on top."""
        hub_id = f"project:{slugify(project)}"
        hub_facts: list[dict] = []
        hub = self._nodes.get(hub_id)
        if hub is not None and not hub["valid_to"]:
            hub_facts.append(self._fact(hub))
        neighbourhood = []
        for (a, _rel, b) in self._edges:
            if a != hub_id and b != hub_id:
                continue
            other = b if a == hub_id else a
            node = self._nodes.get(other)
            if node is None or other == hub_id or node["valid_to"]:
                continue
            if node["project"] != project:
                continue
            neighbourhood.append(self._fact(node))
        hub_facts += sorted(neighbourhood, key=lambda f: f["id"])
        contradictions = []
        for node in sorted(self._nodes.values(), key=lambda n: n["id"]):
            if node["project"] != project or not node["valid_to"]:
                continue
            contradictions.append({
                "id": node["id"], "title": node["title"],
                "version_id": node["version_id"],
                "superseded_by": self._superseded_by(node["id"])})
        return {"project": project, "role": role, "hub_facts": hub_facts,
                "open_contradictions": contradictions}

    def _fact(self, node: dict) -> dict:
        return {"id": node["id"], "kind": node["kind"],
                "title": node["title"], "version_id": node["version_id"]}

    def _superseded_by(self, node_id: str) -> str | None:
        """Who superseded this fact: the source of its `supersedes` edge."""
        for (a, rel, b) in self._edges:
            if rel == "supersedes" and b == node_id:
                return a
        return None

    def _hub_distances(self, project: str) -> dict[str, int]:
        """Undirected BFS from the project hub. A missing hub yields an
        empty map — no graph bonus, never a crash (spec:84)."""
        start = f"project:{slugify(project)}"
        if start not in self._nodes:
            return {}
        found = {start: 0}
        queue = [start]
        for current in queue:  # appends during iteration: that is the BFS
            for (a, _rel, b) in self._edges:
                other = b if a == current else (a if b == current else None)
                if other is None or other in found:
                    continue
                found[other] = found[current] + 1
                queue.append(other)
        return found
