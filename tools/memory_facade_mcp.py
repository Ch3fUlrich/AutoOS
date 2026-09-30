#!/usr/bin/env python3
"""Typed memory facade MCP server — MEMSPEC P1 skeleton (spec §4).

The facade is a memory service and the ONLY writer to the one knowledge
graph (D-037 facade-first, D-038 one central writer): five methods,
model-agnostic, served as MCP —

    recall(query, project?, k)                    spec:62
    context_pack(project, role)                   spec:65
    remember(kind, title, body, project, links, source)  spec:66
    link(a, rel, b)                               spec:67
    supersede(old, new, reason)                   spec:68

Write-side rules enforced on EVERY write (spec:70-75): every node links to
a hub, relations come from a closed list, a body is at most 600 characters,
and a secret scan runs first. Every write returns the entity id, the NEW
version_id, the author session name + restart generation id (D-042) and the
source (spec:77-80); superseded versions stay readable — nothing is ever
hard-deleted.

Start it the way the agent-harness MCP server starts:

    uv --quiet run --no-project --with 'mcp<2' python tools/memory_facade_mcp.py

Env (provenance, D-042):
    AUTOOS_MEMORY_SESSION     author session name -> actor.id  [unknown-session]
    AUTOOS_MEMORY_GEN         restart generation id -> gen     [gen-0]
    AUTOOS_MEMORY_ACTOR_KIND  agent | operator | curator       [agent]

P1 keeps the graph in memory behind memory_facade_engine.MemoryEngine — the
R0 rung (spec:224-226), which proves the facade without touching anything
real — and the P2 bake-off picks the engine that replaces the store; these
callers do not change either way (D-067, spec:255-260). `schema: 2` events
on every write (spec §5) land with the event layer.

Run the tests with:

    python3 tests/test_memory_facade_mcp.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

import memory_facade_engine as engine_mod  # noqa: E402
from autoos_redact import Redactor  # noqa: E402

SESSION_ENV = "AUTOOS_MEMORY_SESSION"
GEN_ENV = "AUTOOS_MEMORY_GEN"
ACTOR_KIND_ENV = "AUTOOS_MEMORY_ACTOR_KIND"
VISIBILITIES = ("project", "global")
MAX_TITLE = 120   # §4.3: an event carries title <=120 chars (spec:112-118)
MAX_BODY = 600    # spec:74 — details live behind a source pointer


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class MemoryFacade:
    """The five spec:62-68 methods plus the write rules behind them."""

    def __init__(self, engine=None, session=None, gen=None, actor_kind=None):
        self.engine = engine if engine is not None else engine_mod.MemoryEngine()
        # provenance (D-042): author session + restart generation, env-defaulted
        self.session = session or os.environ.get(SESSION_ENV) or "unknown-session"
        self.gen = gen or os.environ.get(GEN_ENV) or "gen-0"
        self.actor_kind = actor_kind or os.environ.get(ACTOR_KIND_ENV) or "agent"

    # ─── write-side rules (spec:70-75) ───────────────────────────────────

    @staticmethod
    def _secret_hit(*texts) -> bool:
        """spec:75 — a secret scan runs on every write. Redactor is the one
        pattern set (tools/autoos_redact.py); a changed mask means a hit, so
        the refusal never has to quote what it found."""
        redactor = Redactor()
        return any(text and redactor.text(text) != text for text in texts)

    def _validate_write(self, kind, title, body, project, links, source,
                        visibility) -> str | None:
        """All validation happens BEFORE any write: a refused write leaves
        no partial state behind."""
        if kind not in engine_mod.KINDS:
            return f"unknown kind {kind!r}; kinds come from {engine_mod.KINDS}"
        if not title:
            return "title must not be empty"
        if len(title) > MAX_TITLE:
            return f"title is {len(title)} chars; the event envelope carries <= {MAX_TITLE} (spec:112)"
        if body is None or len(body) > MAX_BODY:
            size = 0 if body is None else len(body)
            return f"body is {size} chars; at most {MAX_BODY} allowed (spec:74)"
        if not project or len(project) > MAX_TITLE:
            return "project must be a non-empty name of at most 120 chars"
        if visibility not in VISIBILITIES:
            return f"visibility must be one of {VISIBILITIES} (spec:109)"
        if self._secret_hit(title, body, source):
            return "refused: content looks like it carries a secret; store details behind the source pointer (spec:75)"
        for link in links or []:
            rel = link.get("rel")
            if rel not in engine_mod.CLOSED_RELATIONS:
                return (f"unknown relation {rel!r}; relations come from the "
                        f"closed list {engine_mod.CLOSED_RELATIONS} (spec:73)")
            target = link.get("id")
            if target is None or self.engine.get(target) is None:
                return f"unknown entity for link: {target!r}"
            if not self.engine.hub_attached(target):
                return f"refused: link target {target!r} does not reach a hub (spec:72)"
        return None

    def _check_hub(self, node_id: str) -> str | None:
        if not self.engine.hub_attached(node_id):
            return (f"refused: {node_id} is not linked to a hub "
                    f"(Project, Domain, Host or Technology; spec:72)")
        return None

    def _provenance(self, source, **fields) -> dict:
        """spec:77-80 — what every write returns and records."""
        fields.setdefault("author", self.session)
        fields.setdefault("gen", self.gen)
        fields.setdefault("source", source)
        return fields

    def _ensure_project_hub(self, project: str) -> dict:
        """spec:72 — `remember` provides the project hub link itself, so a
        legal write can never fail the hub rule."""
        hub_id = f"project:{engine_mod.slugify(project)}"
        hub = self.engine.get(hub_id)
        if hub is None:
            hub = self.engine.add_node(kind="project", title=project, body="",
                                       project=project)
        return hub

    # ─── the five methods (spec:62-68) ───────────────────────────────────

    def recall(self, query, project=None, k=10) -> dict:
        return self.engine.recall(query, project=project, k=k)

    def context_pack(self, project, role) -> dict:
        return self.engine.context_pack(project, role)

    def remember(self, kind, title, body, project, links=None, source=None,
                 domain=None, visibility="project") -> dict:
        error = self._validate_write(kind, title, body, project, links,
                                     source, visibility)
        if error:
            return {"error": error}
        canonical = f"{kind}:{engine_mod.slugify(title)}"
        norm_title = engine_mod.normalise(title)

        # alias ladder: exact id -> normalised title -> similarity (spec:66)
        survivor = self.engine.get(canonical)
        status = "updated" if survivor else None
        if survivor is None:
            aliased = self.engine.resolve_alias(canonical)
            survivor = self.engine.get(aliased) if aliased else None
            status = "merged" if survivor else None
        if survivor is None:
            match = self.engine.normalised_match(kind, project, norm_title)
            survivor, status = (match, "merged") if match else (None, None)
        if survivor is None:
            match = self.engine.similarity_match(kind, project, norm_title)
            survivor, status = (match, "merged") if match else (None, None)

        if survivor is None:
            # the hub is created only now: a refused write must leave no
            # partial state behind, not even a scaffold hub (fix the condition)
            hub = self._ensure_project_hub(project)
            node = self.engine.add_node(kind=kind, title=title, body=body,
                                        project=project, domain=domain,
                                        visibility=visibility)
            self.engine.add_edge(node["id"], "about", hub["id"])
            result = {"status": "created", "id": node["id"],
                      "version_id": node["version"]}
        else:
            hub_error = self._check_hub(survivor["id"])
            if hub_error:
                return {"error": hub_error}
            # a merge keeps the survivor's title (canonical spelling) and
            # takes the incoming body; an exact-id upsert takes both
            updates = {"title": title, "body": body} if status == "updated" \
                else {"body": body}
            prev, node = self.engine.bump(survivor["id"], **updates)
            if status == "merged":
                self.engine.set_alias(canonical, node["id"])
            result = {"status": status, "id": node["id"],
                      "version_id": node["version"], "prev_version_id": prev}
            if status == "merged":
                result["merged_into"] = node["id"]
                result["note"] = f"merged into {node['id']}"
        for link in links or []:
            self.engine.add_edge(result["id"], link["rel"], link["id"])
        return self._provenance(source, **result)

    def link(self, a, rel, b, source=None) -> dict:
        if rel not in engine_mod.CLOSED_RELATIONS:
            return {"error": (f"unknown relation {rel!r}; relations come from "
                              f"the closed list {engine_mod.CLOSED_RELATIONS} (spec:73)")}
        for endpoint in (a, b):
            if self.engine.get(endpoint) is None:
                return {"error": f"unknown entity: {endpoint!r}"}
            hub_error = self._check_hub(endpoint)
            if hub_error:
                return {"error": hub_error}
        edge, created = self.engine.add_edge(a, rel, b)
        return self._provenance(
            source, status="linked", edge_id=edge["edge_id"], a=a, rel=rel,
            b=b, version_id=edge["version"], noop=not created)

    def supersede(self, old, new, reason, source=None) -> dict:
        """Closes the old fact (`valid_to`) and keeps its history: no hub
        check here on purpose — §4.3 prunes orphans by superseding them."""
        for endpoint in (old, new):
            if self.engine.get(endpoint) is None:
                return {"error": f"unknown entity: {endpoint!r}"}
        if old == new:
            return {"error": "supersede needs two different entities"}
        current = self.engine.get(old)
        if current["valid_to"]:
            return self._provenance(
                source, status="noop", note="already superseded", id=old,
                version_id=current["version"], valid_to=current["valid_to"],
                superseded_by=new)
        prev, node = self.engine.close(old, _now_iso())
        self.engine.add_edge(new, "supersedes", old, reason=reason)
        return self._provenance(
            source, status="superseded", id=old, version_id=node["version"],
            prev_version_id=prev, valid_to=node["valid_to"], superseded_by=new)


_FACADE: MemoryFacade | None = None


def _facade() -> MemoryFacade:
    global _FACADE
    if _FACADE is None:
        _FACADE = MemoryFacade()
    return _FACADE


def recall(query: str, project: str | None = None, k: int = 10) -> dict:
    """<=600 tokens of compact lines with ids (+ versions): `id | kind |
    title | version`. An id as the query fetches full text instead (spec:87)."""
    return _facade().recall(query, project=project, k=k)


def context_pack(project: str, role: str) -> dict:
    """Session-start bundle: open facts of the project hub with the ids and
    versions it served (spec:88-91)."""
    return _facade().context_pack(project, role)


def remember(kind: str, title: str, body: str, project: str, links: list | None = None,
             source: str | None = None, domain: str | None = None,
             visibility: str = "project") -> dict:
    """Upsert one fact under a canonical `kind:slug` id: exact -> normalised
    -> similarity alias checks, a duplicate reports `merged into <id>` (spec:66)."""
    return _facade().remember(kind, title, body, project, links=links,
                              source=source, domain=domain, visibility=visibility)


def link(a: str, rel: str, b: str, source: str | None = None) -> dict:
    """Idempotent on (a, rel, b): a repeat call is a no-op (spec:67)."""
    return _facade().link(a, rel, b, source=source)


def supersede(old: str, new: str, reason: str, source: str | None = None) -> dict:
    """Close the old fact (`valid_to`), keep its history readable (spec:68)."""
    return _facade().supersede(old, new, reason, source=source)


def serve() -> int:
    """MCP stdio entry point; the `mcp` package is imported only here so the
    module (and its tests) load without it."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.stderr.write(
            "memory-facade: the 'mcp' package is missing; start it with\n"
            "  uv --quiet run --no-project --with 'mcp<2' "
            "python tools/memory_facade_mcp.py\n")
        return 2
    app = FastMCP("memory-facade")

    @app.tool(name="recall")
    def _recall(query: str, project: str | None = None, k: int = 10) -> dict:
        """Compact lines with ids + versions, or full text when query is an id."""
        return recall(query, project=project, k=k)

    @app.tool(name="context_pack")
    def _context_pack(project: str, role: str) -> dict:
        """Session-start bundle for a project, <=1.5k tokens (spec:65)."""
        return context_pack(project, role)

    @app.tool(name="remember")
    def _remember(kind: str, title: str, body: str, project: str, links: list,
                  source: str, domain: str | None = None,
                  visibility: str = "project") -> dict:
        """Upsert a fact (canonical `kind:slug` id) through every write rule."""
        return remember(kind, title, body, project, links=links, source=source,
                        domain=domain, visibility=visibility)

    @app.tool(name="link")
    def _link(a: str, rel: str, b: str, source: str | None = None) -> dict:
        """Idempotent edge on the closed relation list (spec:67, 73)."""
        return link(a, rel, b, source=source)

    @app.tool(name="supersede")
    def _supersede(old: str, new: str, reason: str, source: str | None = None) -> dict:
        """Close a fact as a readable tombstone; nothing is hard-deleted (spec:68)."""
        return supersede(old, new, reason, source=source)

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
