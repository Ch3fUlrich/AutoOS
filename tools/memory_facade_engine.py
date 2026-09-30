#!/usr/bin/env python3
"""Graph store and read path behind the MEMSPEC memory facade (spec §4).

Split of responsibility with tools/memory_facade_mcp.py: the facade owns
write policy (the §4 rules, provenance, `schema: 2` events), this adapter
owns the graph and how a query becomes ranked compact lines. One graph, one
central writer (D-038).

The engine itself is deliberately swappable: D-067 (spec:255-260) names
Omnigraph as the default engine and facade-first means callers never see
which one runs. P1 runs this adapter over an in-memory graph — the R0 rung
(spec:224-226: prove the facade, events and runbook mechanics without
touching anything real) — and the P2 bake-off (spec §9) decides what
replaces the store behind these same methods.

Data shapes (one graph, canonical `kind:slug` ids, spec:66):

    nodes {id: {id, kind, title, body, project, domain?, visibility,
                version ("v1"..), valid_to (None | ISO)}}
    edges  {(a, rel, b): {edge_id, a, rel, b, version, reason?}}
           idempotent on (a, rel, b) — a repeat is a no-op (spec:67)
    alias  {merged_id: survivor_id} — a merged id resolves here (spec:79-80)

Run the tests that pin this contract with:

    python3 tests/test_memory_facade_mcp.py
"""
from __future__ import annotations

import difflib
import hashlib
import re

# The id vocabulary: the four hub kinds the spec's hub rule names (spec:72)
# plus the durable-content kinds of spec §3. Closed on purpose — a new kind
# is a reviewed change, not a caller typo.
HUB_KINDS = ("project", "domain", "host", "technology")
CONTENT_KINDS = ("decision", "lesson", "component", "preference", "finding")
KINDS = HUB_KINDS + CONTENT_KINDS

# spec:73 — relation types come from a closed list. `about` is how a node
# attaches to its hub; `supersedes` records lineage when a fact is closed.
CLOSED_RELATIONS = ("about", "depends_on", "part_of", "contradicts",
                    "supersedes", "related")

# Alias-ladder similarity (spec:66 "exact -> normalised -> similarity").
# 0.85 on normalised titles only when both sides have >=3 words: short
# titles like "recall v1"/"recall v2" are different facts, not aliases.
ALIAS_SIMILARITY = 0.85
MIN_ALIAS_TOKENS = 3


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
    return " ".join(tokens(title))


class MemoryEngine:
    """One in-memory graph behind the facade; swap this class, not the facade."""

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: dict[tuple, dict] = {}
        self._alias: dict[str, str] = {}

    # ─── store ───────────────────────────────────────────────────────────

    def get(self, node_id: str) -> dict | None:
        node = self._nodes.get(node_id)
        return dict(node) if node else None

    @property
    def nodes(self) -> list[dict]:
        return [dict(n) for n in self._nodes.values()]

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
            "version": "v1", "valid_to": valid_to,
        }
        return dict(self._nodes[node_id])

    def bump(self, node_id: str, **updates) -> tuple[str, dict]:
        """Write new content onto a node and mint its NEW version (spec:77)."""
        node = self._nodes[node_id]
        prev = node["version"]
        node["version"] = "v%d" % (int(node["version"][1:]) + 1)
        for key, value in updates.items():
            node[key] = value
        return prev, dict(node)

    def close(self, node_id: str, when: str) -> tuple[str, dict]:
        """Close a fact (`valid_to`) as a tombstone version — superseded
        versions stay readable, nothing is ever hard-deleted (spec:79-80)."""
        node = self._nodes[node_id]
        if node["valid_to"]:
            raise ValueError(f"already closed: {node_id}")
        prev, node = self.bump(node_id, valid_to=when)
        return prev, node

    def add_edge(self, a: str, rel: str, b: str, reason: str | None = None):
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
                            "version": "v1", "reason": reason}
        return dict(self._edges[key]), True

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
                return dict(node)
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
                best, best_ratio = dict(node), ratio
        return best

    # ─── read path (spec:82-91) ──────────────────────────────────────────

    def recall(self, query: str, project: str | None = None, k: int = 10) -> dict:
        """Compact lines for a query — or the full entity when the query IS
        an id (spec:87: agents fetch full text by id only when needed)."""
        node_id = str(query or "").strip()
        if node_id in self._nodes:
            node = self._nodes[node_id]
            return {"mode": "entity", "id": node["id"], "kind": node["kind"],
                    "title": node["title"], "body": node["body"],
                    "project": node["project"], "domain": node["domain"],
                    "version_id": node["version"], "valid_to": node["valid_to"]}
        survivor = self._alias.get(node_id)
        if survivor:
            return {"mode": "redirect", "id": node_id, "redirects_to": survivor}
        wanted = set(tokens(query or ""))
        hits = []
        for node in self._nodes.values():
            if node["valid_to"]:
                continue
            if project and node["project"] != project:
                continue
            found = len(wanted & set(tokens(node["title"]) + tokens(node["body"])))
            if found:
                hits.append((-found, node["id"], node))
        hits.sort(key=lambda item: (item[0], item[1]))
        lines = ["%s | %s | %s | %s" % (n["id"], n["kind"], n["title"], n["version"])
                 for _score, _nid, n in hits[:k]]
        return {"mode": "lines", "lines": lines, "count": len(lines)}

    def context_pack(self, project: str, role: str) -> dict:
        """The session-start bundle: open facts of the project hub (spec:88),
        each carrying the id + version the caller can prove provenance with
        (§12). Read-path budgets and sections are layered on next."""
        facts = sorted(
            (n for n in self._nodes.values()
             if n["project"] == project and not n["valid_to"]
             and n["kind"] not in HUB_KINDS),
            key=lambda n: n["id"])
        items = [{"id": n["id"], "kind": n["kind"], "title": n["title"],
                  "version_id": n["version"]} for n in facts]
        served = [{"id": n["id"], "version_id": n["version"]} for n in facts]
        return {"project": project, "role": role, "facts": items,
                "served": served, "count": len(items)}
