# Structured Memory Schema (Omnigraph)

The memory graph is **typed**. Rather than storing unstructured conversation blobs,
agents write specific node types with explicit edges, so memory is queryable,
reviewable, and stays "rules-integrated".

**One graph per project** (hard isolation). Each repo's memory lives in its own graph
named after the repo folder — `agent-skills`, `sibling-analysis-repo`, `finance-repo`,
`site-repo`, … — declared in `infra/mcp-servers/cluster/cluster.yaml`. Every graph
shares this one schema (`cluster/memory.pg`). The shared **`memory`** graph holds **only**
global-scope `Preference`s. Inside a project graph, everything still edges back to that
repo's `Project` node; a node with no `Project` edge renders as "global", which is a bug
for project-specific info. See [SKILL.md](../SKILL.md) for the recall/persist protocol and
[operations.md](operations.md) for the operational rules.

> **This file must mirror `infra/mcp-servers/cluster/memory.pg`.** That file is the
> declaration the server actually enforces; this is its human-readable companion. A
> declaration is not live until `scripts/apply-cluster.sh` runs — verify with `schema_get`
> against the server, not by reading either file.

## Node types

| Type | Holds | Key fields |
|---|---|---|
| `Project` | One repository / workstream | `slug`, `name?`, `path?`, `summary?` |
| `Decision` | A choice that was made and why | `slug`, `title`, `rationale?`, `status`, `date?`, `embedding?` |
| `Rule` | A hard constraint agents must follow | `slug`, `statement`, `severity` |
| `Preference` | A soft, overridable inclination (often global) | `slug`, `statement`, `scope` |
| `Convention` | A repeatable pattern/way-of-doing | `slug`, `name`, `example?` |
| `Component` | A notable part of a system | `slug`, `name`, `kind?`, `location?` |
| `Task` | Ongoing/planned work worth remembering | `slug`, `title`, `state` |

`status` for `Decision`: `proposed | accepted | superseded | deprecated` (an enum — other
values are rejected). `severity` for `Rule`: `must | should`. `scope` for `Preference`:
`global | <project-slug>`. Every type is `@key(slug)`, so `load --mode merge` upserts by
slug and is idempotent. `Decision.embedding` is `Vector(768)` populated from `rationale`
(`nomic-embed-text`); it is optional and regenerable via `scripts/populate-embeddings.py`.

## Edge types

Edges split into **hub** edges (attach a node to its `Project` — required, or the node
reads as global) and **relational** edges (connect knowledge nodes to each other, so the
graph forms real clusters instead of a star).

| Edge | From → To | Meaning |
|---|---|---|
| **hub** | | |
| `DecidedIn` | `Decision` → `Project` | decision belongs to a project |
| `ConstrainsProject` | `Rule` → `Project` | rule applies to the project |
| `AppliesTo` | `Convention` → `Project` | pattern applies to the project |
| `PartOf` | `Component` → `Project` | component belongs to the project |
| `Tracks` | `Task` → `Project` | task belongs to the project |
| **relational** | | |
| `ConstrainsComponent` | `Rule` → `Component` | rule governs a specific component |
| `Affects` | `Decision` → `Component` | decision changes a component |
| `Addresses` | `Task` → `Component` | task works on a component |
| `Implements` | `Task` → `Decision` | task realizes a decision |
| `DependsOn` | `Component` → `Component` | component depends on another |
| `Supersedes` | `Decision` → `Decision` | replaces an earlier decision |

There is **no** generic `relates-to (any → any)` edge — edges are typed and directional,
and an undeclared type fails with `type error: T4: unknown edge type`.

**Casing is asymmetric and bites constantly:** `insert`/`delete` use the **PascalCase**
type (`insert DecidedIn { from, to }`); **traversal** uses **lowerCamelCase**
(`$d decidedIn $p`). See [operations.md](operations.md) rule 2.

## Schema declaration

The live declaration is [`infra/mcp-servers/cluster/memory.pg`](../../../infra/mcp-servers/cluster/memory.pg).
Excerpt:

```pg
node Decision {
  slug: String
  title: String
  rationale: String?
  status: enum(proposed, accepted, superseded, deprecated)
  date: String?
  embedding: Vector(768)? @embed("rationale")
  @key(slug)
  @index(status)
}

edge DecidedIn: Decision -> Project          // hub
edge Affects:   Decision -> Component        // relational
```

## JSONL ingest example

The `load` tool (**not** `ingest`) accepts newline-delimited records: nodes carry `type` +
`data`; edges carry `edge` + `from`/`to` by `slug`. Edge names are the PascalCase type.
Note each node gets its hub edge **and** at least one relational edge:

```jsonl
{"type":"Project","data":{"slug":"agent-skills","name":"Agent Skills","path":"~/code/agent-skills","summary":"Reusable agent skills + self-hosted MCP stack"}}
{"type":"Component","data":{"slug":"as-omnigraph-cluster","name":"Omnigraph cluster","kind":"infra","location":"infra/mcp-servers/cluster/"}}
{"type":"Decision","data":{"slug":"omnigraph-over-mem0","title":"Omnigraph replaces Mem0 as default memory","rationale":"Typed, queryable, reviewable memory keeps rules integrated (ADR 0001). The fallback it originally kept was removed in ADR 0003 — Omnigraph is the only memory layer.","status":"accepted","date":"2026-07-09"}}
{"type":"Rule","data":{"slug":"skill-single-source","statement":"A skill's SKILL.md is the source of truth; starters/instructions are thin pointers","severity":"must"}}
{"edge":"PartOf","from":"as-omnigraph-cluster","to":"agent-skills"}
{"edge":"DecidedIn","from":"omnigraph-over-mem0","to":"agent-skills"}
{"edge":"Affects","from":"omnigraph-over-mem0","to":"as-omnigraph-cluster"}
{"edge":"ConstrainsProject","from":"skill-single-source","to":"agent-skills"}
```

Global-scope `Preference`s are the one thing that legitimately has no `Project` edge, and
they live in the shared `memory` graph:

```jsonl
{"type":"Preference","data":{"slug":"mcp-first-nav","statement":"Prefer Serena/Graphify/Omnigraph over brute-force file reads","scope":"global"}}
```

`Date`/`DateTime` note: `load` JSONL wants a `Date` as **integer days since epoch**, while
`mutate --params` wants an **ISO string** — a common silent type error. (The types above
use `String` for `date`, so this does not bite here.)

## Slug conventions

- `Project.slug` = repository folder name (e.g. `agent-skills`, `site-repo`) — and
  the graph is named the same.
- Other slugs are **lowercase** kebab-case, unique within their project graph; a case
  variant creates a duplicate node that auto-merge cannot collapse.
- Prefix by project (`ba-*`, `inv-*`, `as-*`) so a node's owner stays legible.
- Prefer stable, human-readable slugs so edges stay legible and re-loading is idempotent
  (re-writing the same slug updates rather than duplicates).
