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

Single design (arbitration ruling, choice 3; all code and tests conform):

 * links are {"rel", "id"} pairs everywhere — the (rel, id) half of
   spec:67's (a, rel, b) triple. Bare id strings are refused by name.
 * relations come from memory_facade_engine.CLOSED_RELATIONS (spec:73's
   closed list; the spec mandates it and enumerates none).
 * version_id is an int: writes return the NEW one, `prev_version_id` rides
   only on updated|superseded events (§5), `versions` keeps old snapshots
   readable (spec:79). Recall LINES render the int as `v<n>`; structured
   fields never do.
 * the store is one JSON file, snapshotted atomically after each successful
   write. The R0 fixture (tests/fixtures/memory/graph.json) is the seed
   tests copy — never the live path.

Env (one AUTOOS_MEMORY_* family):

    AUTOOS_MEMORY_STORE       graph JSON file           [logs/memory/graph.json]
    AUTOOS_MEMORY_EVENTS      schema: 2 NDJSON log      [logs/memory-events/events.ndjson]
    AUTOOS_MEMORY_SESSION     author session -> actor.id [unknown-session]
    AUTOOS_MEMORY_GEN         restart generation -> gen  [gen-0]
    AUTOOS_MEMORY_ACTOR_KIND  agent | operator | curator  [agent]

Every successful write appends its §5 `schema: 2` events to the events file;
refused writes and no-ops append nothing (an append-only log of changes,
spec:112-119). P1 runs over the R0 fixture store — the rung that proves the
facade, the events and the runbook mechanics without touching anything real
(spec:224-226) — and the P2 bake-off picks the engine behind these same
callers (D-067, spec:255-260).

Start it the way the agent-harness MCP server starts:

    uv --quiet run --no-project --with 'mcp<2' python tools/memory_facade_mcp.py

Run the tests with:

    python3 tests/test_memory_facade_mcp.py
    python3 tests/test_memory_events.py
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

import memory_facade_engine as engine_mod  # noqa: E402
from autoos_redact import Redactor  # noqa: E402

STORE_ENV = "AUTOOS_MEMORY_STORE"
EVENTS_ENV = "AUTOOS_MEMORY_EVENTS"
SESSION_ENV = "AUTOOS_MEMORY_SESSION"
GEN_ENV = "AUTOOS_MEMORY_GEN"
ACTOR_KIND_ENV = "AUTOOS_MEMORY_ACTOR_KIND"
VISIBILITIES = ("project", "global")
MAX_TITLE = 120   # §4.3: an event carries title <=120 chars (spec:112-118)
MAX_BODY = 600    # spec:74 — details live behind a source pointer
ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford, no I/L/O/U


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ulid() -> str:
    """26-char Crockford ULID: 48-bit ms timestamp + 80 random bits — the
    §5 envelope's `id` (sortable by `at`, unique without coordination)."""
    ms = int(time.time() * 1000)
    chars = []
    for _ in range(10):
        chars.append(ULID_ALPHABET[ms % 32])
        ms //= 32
    chars.extend(ULID_ALPHABET[secrets.randbelow(32)] for _ in range(16))
    return "".join(chars)


class MemoryFacade:
    """The five spec:62-68 methods, the write rules behind them, and the
    §5 event log every successful write appends to."""

    def __init__(self, engine=None, session=None, gen=None, actor_kind=None,
                 store=None, events=None):
        self.store_path = Path(
            store or os.environ.get(STORE_ENV)
            or (REPO_ROOT / engine_mod.DEFAULT_STORE_REL))
        self.events_path = Path(
            events or os.environ.get(EVENTS_ENV)
            or (REPO_ROOT / engine_mod.DEFAULT_EVENTS_REL))
        self.engine = engine if engine is not None else \
            engine_mod.MemoryEngine(self.store_path)
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
        no partial state behind — not even a scaffold hub."""
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
        hub_id = f"project:{engine_mod.slugify(project)}"
        for link in links or []:
            if not isinstance(link, dict) or "rel" not in link or "id" not in link:
                return ('refused: each link must be a {"rel", "id"} pair; '
                        "bare id strings do not satisfy the (a, rel, b) "
                        "triple (spec:67)")
            if link["rel"] not in engine_mod.CLOSED_RELATIONS:
                return (f"unknown relation {link['rel']!r}; relations come from the "
                        f"closed list {engine_mod.CLOSED_RELATIONS} (spec:73)")
            target = link["id"]
            if target == hub_id:
                continue  # this very write guarantees the project hub exists
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

    def _ensure_project_hub(self, project: str) -> tuple[dict, bool]:
        """spec:72 — `remember` provides the project hub link itself, so a
        legal write can never fail the hub rule. Returns (hub, created):
        a hub this call creates is itself a write, and gets its event."""
        hub_id = f"project:{engine_mod.slugify(project)}"
        hub = self.engine.get(hub_id)
        if hub is not None:
            return hub, False
        hub = self.engine.add_node(kind="project", title=project, body="",
                                   project=project)
        return hub, True

    # ─── §5 schema: 2 events (spec:93-121) ──────────────────────────────

    def _node_event(self, verb: str, node: dict, prev: int | None = None,
                    source: str | None = None, **extra) -> dict:
        """The node envelope (spec:100-110). `prev_version_id` is passed
        only for updated|superseded (spec:105); no body ever rides along
        (spec:117 — text is fetched by entity_id + version_id)."""
        event = {
            "id": _ulid(),
            "at": _now_iso(),
            "type": f"memory.{node['kind']}.{verb}",
            "project": node["project"],
            "entity_id": node["id"],
            "entity_kind": node["kind"],
            "version_id": node["version_id"],
            "gen": self.gen,
            "title": node["title"][:MAX_TITLE],
            "actor": {"kind": self.actor_kind, "id": self.session},
            "visibility": node["visibility"],
            "schema": 2,
        }
        if node.get("domain"):
            event["domain"] = node["domain"]
        if prev is not None:
            event["prev_version_id"] = prev
        if source:
            event["source_ref"] = source
        event.update(extra)
        return event

    def _edge_event(self, edge: dict, from_node: dict) -> dict:
        """The §4.3 edge extension (spec:116): {edge_id, rel, from_id,
        to_id, version_id} + the common envelope — no title/entity_id, the
        text lives on the nodes."""
        return {
            "id": _ulid(),
            "at": _now_iso(),
            "type": "memory.edge.linked",
            "project": from_node["project"],
            "edge_id": edge["edge_id"],
            "rel": edge["rel"],
            "from_id": edge["a"],
            "to_id": edge["b"],
            "version_id": edge["version_id"],
            "gen": self.gen,
            "actor": {"kind": self.actor_kind, "id": self.session},
            "visibility": from_node["visibility"],
            "schema": 2,
        }

    def _append(self, events: list[dict]) -> None:
        """Append-only store (spec:118-119): entries only ever go on the
        end, one JSON object per line."""
        if not events:
            return
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.events_path, "a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _recent_events(self, project: str, limit: int = 20) -> list[dict]:
        """spec:89 — recent merged/superseded events for the project; a
        merge's `redirected` twin rides along, because that is how the
        merged id stays resolvable (spec:114). A missing file is empty,
        not an error."""
        if not self.events_path.exists():
            return []
        recent = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue  # a torn line is not a failed session start
            verb = str(event.get("type", "")).rsplit(".", 1)[-1]
            if verb in ("merged", "superseded", "redirected") and (
                    event.get("project") == project
                    or event.get("visibility") == "global"):
                recent.append(event)
        return recent[-limit:]

    # ─── the five methods (spec:62-68) ───────────────────────────────────

    def recall(self, query, project=None, k=10) -> dict:
        return self.engine.recall(query, project=project, k=k)

    def context_pack(self, project, role) -> dict:
        """Session-start bundle (spec:88-91): hub facts, open
        contradictions, recent events — ≤1.5k tokens, with the ids and
        versions it served as provenance (§12)."""
        pack = self.engine.context_pack(project, role)
        pack["recent_events"] = self._recent_events(project)
        while (engine_mod.pack_tokens(pack) > engine_mod.PACK_TOKENS
               and (len(pack["hub_facts"]) > 1
                    or len(pack["recent_events"]) > 1)):
            # spec:90 — fit the budget: drop the far end of the
            # neighbourhood first, then stale events; the hub stays.
            if len(pack["hub_facts"]) > 1:
                pack["hub_facts"].pop()
            else:
                pack["recent_events"].pop(0)
        pack["served"] = [{"id": fact["id"], "version_id": fact["version_id"]}
                          for fact in pack["hub_facts"]
                          + pack["open_contradictions"]]
        pack["tokens"] = engine_mod.pack_tokens(pack)
        return pack

    def remember(self, kind, title, body, project, links=None, source=None,
                 domain=None, visibility="project") -> dict:
        links = list(links or [])
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

        pending: list[dict] = []
        if survivor is None:
            hub_id = f"project:{engine_mod.slugify(project)}"
            # the node may BE its own project hub (remember a project
            # root); only create the hub when it is someone else's
            hub = hub_created = None
            if canonical != hub_id:
                hub, hub_created = self._ensure_project_hub(project)
            if hub_created:
                pending.append(self._node_event("created", hub, source=source))
            node = self.engine.add_node(kind=kind, title=title, body=body,
                                        project=project, domain=domain,
                                        visibility=visibility)
            pending.append(self._node_event("created", node, source=source))
            if hub is not None:
                edge, created = self.engine.add_edge(node["id"], "about",
                                                     hub["id"])
                if created:
                    pending.append(self._edge_event(edge, node))
            for link in links:
                if link["id"] == node["id"]:
                    continue  # never a self-loop
                edge, created = self.engine.add_edge(node["id"], link["rel"],
                                                     link["id"])
                if created:
                    pending.append(self._edge_event(edge, node))
            result = {"status": "created", "id": node["id"],
                      "version_id": node["version_id"]}
        else:
            hub_error = self._check_hub(survivor["id"])
            if hub_error:
                return {"error": hub_error}
            # an identical rewrite is a no-op: same version, no event —
            # an append-only log of CHANGES, not of traffic
            if status == "updated":
                changed = (survivor["title"] != title
                           or survivor["body"] != body)
            else:
                changed = (survivor["body"] != body
                           or self.engine.resolve_alias(canonical)
                           != survivor["id"])
            changed = changed or any(
                not self.engine.has_edge(survivor["id"], link["rel"],
                                         link["id"]) for link in links)
            if not changed:
                result = {"status": "noop", "id": survivor["id"],
                          "version_id": survivor["version_id"]}
                if status == "merged":
                    result["merged_into"] = survivor["id"]
                    result["note"] = f"merged into {survivor['id']}"
                return self._provenance(source, **result)
            # a merge keeps the survivor's title (canonical spelling) and
            # takes the incoming body; an exact-id upsert takes both
            updates = {"title": title, "body": body} if status == "updated" \
                else {"body": body}
            prev, node = self.engine.bump(survivor["id"], **updates)
            if status == "merged":
                self.engine.set_alias(canonical, node["id"])
                # spec:114 — the merge is logged (`merged`) and the dead id
                # stays resolvable (`redirected`, carrying merged_from)
                pending.append(self._node_event(
                    "merged", node, source=source, merged_from=canonical))
                pending.append(self._node_event(
                    "redirected", node, source=source, merged_from=canonical))
            else:
                pending.append(self._node_event(
                    "updated", node, prev=prev, source=source))
            for link in links:
                if link["id"] == node["id"]:
                    continue
                edge, created = self.engine.add_edge(node["id"], link["rel"],
                                                     link["id"])
                if created:
                    pending.append(self._edge_event(edge, node))
            result = {"status": status, "id": node["id"],
                      "version_id": node["version_id"],
                      "prev_version_id": prev}
            if status == "merged":
                result["merged_into"] = node["id"]
                result["note"] = f"merged into {node['id']}"
        self.engine.save()
        self._append(pending)
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
        if created:
            self.engine.save()
            self._append([self._edge_event(edge, self.engine.get(a))])
        return self._provenance(
            source, status="linked", edge_id=edge["edge_id"], a=a, rel=rel,
            b=b, version_id=edge["version_id"], noop=not created)

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
                version_id=current["version_id"], valid_to=current["valid_to"],
                superseded_by=new)
        prev, node = self.engine.close(old, _now_iso())
        edge, created = self.engine.add_edge(new, "supersedes", old,
                                             reason=reason)
        pending = [self._node_event("superseded", node, prev=prev,
                                    source=source, supersedes=new)]
        if created:
            pending.append(self._edge_event(edge, self.engine.get(new)))
        self.engine.save()
        self._append(pending)
        return self._provenance(
            source, status="superseded", id=old, version_id=node["version_id"],
            prev_version_id=prev, valid_to=node["valid_to"],
            superseded_by=new)


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
    """Session-start bundle: hub facts, open contradictions and recent
    events, with the ids and versions it served (spec:88-91)."""
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
