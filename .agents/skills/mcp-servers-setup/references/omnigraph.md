> Part of [mcp-servers-setup](../SKILL.md).

### Omnigraph — Structured Cross-Project Memory

The default memory layer. Instead of unstructured blobs, write **typed** nodes
(`Decision / Rule / Preference / Convention / Component / Task`) edged to a
`Project`, and recall them via fused graph + vector + full-text queries. MCP tools:
`schema / branches / queries / mutations / ingest`.

| Action | How |
|--------|-----|
| Recall project memory | `query` for rules/decisions/preferences edged to the project |
| Persist a durable fact | `mutate` (a few nodes) or `load` `mode: merge` (bulk), on a branch, then merge |
| Global preferences | `Preference` nodes with `scope: global`, in the shared `memory` graph |

Full protocol and schema: `skills/structured-memory/SKILL.md`. **Each repo has its
own graph** named after the repo folder (`OMNIGRAPH_GRAPH_ID=<repo>`); the shared
`memory` graph holds only global-scope `Preference`s. Isolation is by graph, plus
`Project` edges inside it — never a per-call `user_id`. Omnigraph is the only memory
layer; there is no fallback (ADR 0003).

**Discovering repository / project names — token-gated, and the token *is* the access model.**

Graph id == repository folder name == `Project.slug`, by construction, so "which graphs
exist" and "which projects exist" are one question. `cluster.yaml` is **not** the answer:
it is gitignored and purged from history precisely because it had become a roster of every
private project. Ask the live cluster.

| You want | MCP tool | HTTP equivalent |
|---|---|---|
| Every repository / project name | `graphs_list` | `curl -fsS -H "Authorization: Bearer $OMNIGRAPH_TOKEN" http://localhost:8080/graphs` |
| This repo's own record (slug) | `query whoami() { match { $p: Project } return { $p.slug } }` (no `repository` property in the live schema; for a scope check use the decision-keyed query in CLAUDE.md) | `POST /graphs/<id>/query` with the same header |

**A bearer token is the only key.** Measured against the live server on 2026-08-31:

| Request | Result |
|---|---|
| no `Authorization` header | **401** |
| wrong token | **401** |
| valid `OMNIGRAPH_TOKEN` | 200 |
| `GET /healthz` | 200 — the only open endpoint, and it returns liveness alone: no names, no data |

`/graphs` is closed by default. It answers at all only because the `cluster-admin` bundle
(`cluster/cluster.policy.yaml`, `applies_to: [cluster]`) grants the `graph_list` action to
the `operators` group. Without that grant the *authenticated* call returns **403
ForbiddenError** — not an empty list, so you cannot mistake "not allowed" for "nothing here".

**Scope — one token, one actor, that actor's graphs.** The token resolves to the single
actor `default` in group `operators`; `project-graphs.policy.yaml` grants it the per-project
graphs and `memory.policy.yaml` grants it `memory`. A holder therefore sees exactly the
graphs that belong to them — today that is all of them, because there is one user. **Treat
it as all-or-nothing: never hand this token to anyone whose data is not already in these
graphs.** Per-user scoping is written but switched off — `cluster/users.policy.yaml.example`
plus the commented `users-access` bundle in `cluster.yaml`. Turning it on needs one bearer
token per user and a `scripts/apply-cluster.sh` run; until then there is no way to give
someone a token that reveals only *part* of the roster.

> An unset `OMNIGRAPH_TOKEN` does **not** surface as "denied" through the MCP bridge — it
> surfaces as `missing bearer token`, and a graph that looks empty is a config bug until
> proven otherwise. Diagnose with `omnigraph-setup/setup-agent-memory.ps1 -Check` (or `.sh --check`).

