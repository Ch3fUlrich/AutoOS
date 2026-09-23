# 0001. Omnigraph over Mem0 for cross-project memory

- **Status:** Accepted (2026-07-09)
- **Amended (2026-07-16):** the Mem0 fallback described under
  "Fallback / switch-back criteria" was **removed** — see
  [0003-remove-mem0-fallback.md](0003-remove-mem0-fallback.md). The decision to adopt
  Omnigraph stands; only the escape hatch is gone. The rest of this record is left as
  written on 2026-07-09.

## Context

The stack needs structured, fast, efficient memory shared across agents and
projects, so that decisions and rules from earlier sessions stay integrated into
future builds. The previous default, [Mem0](https://mem0.ai), auto-extracts facts
from raw conversation into **unstructured vector blobs** and isolates by
`user_id`. That is convenient but works against the goal: the memories are
opaque, hard to review, and retrieval is pure vector similarity.

We evaluated [Omnigraph](https://github.com/ModernRelay/omnigraph) (v0.8.1): a
self-hostable, lakehouse-native graph engine with combined graph-traversal +
vector + full-text retrieval, git-style branching, and an MCP bridge
(`@modernrelay/omnigraph-mcp`, tools `schema/branches/queries/mutations/ingest`).

Key finding: **Omnigraph does not auto-extract memory** — the agent must write
typed nodes explicitly. That is more work per write, but it is exactly what makes
memory structured and reviewable.

## Decision

Adopt Omnigraph as the **default** memory layer, backed by MinIO (S3) locally.
Replace Mem0's auto-extraction with an explicit **structured-memory protocol**
(see `skills/structured-memory/SKILL.md`): typed `Decision / Rule / Preference /
Convention / Component / Task` nodes edged to a `Project`, recalled at session
start and persisted (via branch → ingest → merge) at session end.

Keep Graphify unchanged — it auto-extracts *code* structure, which Omnigraph does
not; the two are complementary (code graph vs memory graph).

## Consequences

- **Better fit for the goal:** typed, queryable, reviewable memory; fused
  retrieval beats pure vector search; versioned and reversible.
- **Heavier infra:** `omnigraph-server` + MinIO + a bearer token, vs Mem0's
  Postgres/API/bridge. Accepted.
- **Memory is no longer automatic:** it depends on the agent following the
  structured-memory protocol at session boundaries. Treated as a feature (it is
  what makes memory "structured"), reinforced by making it a first-class skill.
- Sibling repos (`homelab-server`, `finance-repo`, …) must switch their agent instructions off
  Mem0's `user_id` model onto project-scoped subgraphs.

## Fallback / switch-back criteria

> **Superseded 2026-07-16 by [ADR 0003](0003-remove-mem0-fallback.md) — the fallback
> was removed and none of the criteria below can be met any more.** Kept verbatim as
> the record of what was originally decided.

Mem0 is retained, wired alongside but **off by default**, under the
`mem0-fallback` Docker Compose profile (`docker compose --profile mem0-fallback
up -d`) with its server code under `infra/mcp-servers/servers/_fallback/`.

Re-enable the fallback if any hold: Omnigraph operation proves unsustainable on
the target hardware; the `@modernrelay/omnigraph-mcp` bridge is unstable for our
version; or structured-memory discipline fails to produce usable recall in
practice. When falling back, layer the same structured protocol on top of Mem0
(store typed statements as memory text, scope by `user_id` = project slug).
