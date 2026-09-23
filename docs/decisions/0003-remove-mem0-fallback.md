# 0003. Remove the Mem0 fallback entirely

- **Status:** Accepted (2026-07-16)
- **Amends:** [0001-omnigraph-over-mem0.md](0001-omnigraph-over-mem0.md) — that ADR's
  "Fallback / switch-back criteria" section only.

## Context

[ADR 0001](0001-omnigraph-over-mem0.md) adopted Omnigraph as the default memory layer
but kept Mem0 wired alongside, off by default, behind a `mem0-fallback` Compose profile,
"in case". Nine months of practice showed the escape hatch cost more than it insured:

- **It was never exercised.** Omnigraph has run as the only memory layer since. The
  switch-back criteria in 0001 (unsustainable on the hardware, unstable bridge, failed
  recall discipline) have not been met, and per-project graph isolation has since made
  Omnigraph *more* robust, not less.
- **It kept leaking back in as a live option.** Docs, starters, and per-repo agent
  instructions kept describing Mem0 as an available fallback, so agents kept treating it
  as one. `sibling-analysis-repo/.mcp.json` wired **mem0 and no omnigraph at all** while its
  own CLAUDE.md claimed "Memory is Omnigraph now" — the fallback's existence made that
  contradiction survivable instead of obvious.
- **A fallback with a different data model is not a fallback.** Mem0 stores unstructured
  blobs keyed by `user_id`; Omnigraph stores typed nodes and edges. Nothing migrates
  between them automatically, so "fall back" meant "start over with a different model
  and lose the graph" — not something anyone would actually choose in an incident.
- **It carried real cost:** 4 containers (Postgres/pgvector, Mem0 API, MCP bridge,
  dashboard), a `POSTGRES_PASSWORD`, a DeepSeek API key, a second embedding model
  (`bge-m3`, 1024-dim — a standing dimension-mismatch trap next to the graph's 768-dim
  `nomic-embed-text`), and a permanent asterisk in every memory doc.
- **Its tooling had already rotted.** The `setup`/`start`/`stop`/`test`/`migrate` scripts
  still stood up the Mem0 stack and health-checked `localhost:8888`, and their bare
  `docker compose` calls failed with "no configuration file provided" after the
  server/client compose split. The fallback was not merely unused; it was broken, and
  nobody noticed — which is the strongest evidence it was not a real safety net.

## Decision

Remove Mem0 from the repository. Omnigraph is the memory layer, with **no fallback**.

Removed: `infra/mcp-servers/servers/_fallback/`, the `mem0-fallback` Compose profile and
its four services, the Mem0/Postgres/DeepSeek env vars, the Mem0-era orchestration
scripts, and the legacy `docs/ARCHITECTURE.md` + `docs/TOKEN_SAVINGS.md` (Mem0-era
duplicates of `docs/architecture.md`).

The stack now depends on no Postgres, no pgvector, and no LLM API key. Ollama
(`nomic-embed-text`) remains **optional** — without it, recall degrades to graph
traversal + scalar indexes rather than failing.

## Consequences

- **No escape hatch.** If Omnigraph is down, memory is unavailable for that session;
  agents fall back to reading the repo, as they would with no memory layer at all. This
  is acceptable because memory is an accelerator, not a correctness dependency — a
  session without recall is slower, not wrong.
- **Availability now rests on Omnigraph alone.** The real insurance is backups, not a
  parallel product: `.graph-backup/` NDJSON exports, the versioned `cluster/seed/*.jsonl`
  (which the boot seeder re-merges), and MinIO's bind-mounted store. Keep those working;
  they are what a fallback was pretending to be.
- **One less contradiction to explain.** "Memory is Omnigraph" is now unqualified, so a
  repo wiring mem0 is an obvious bug rather than a defensible choice.
- **ADR 0001 stays** as the record of *why* Omnigraph — only its fallback clause is void.
- Reversal is `git revert`, not a config flag. That is the intended trade: bringing Mem0
  back should be a deliberate, reviewed decision with a fresh ADR, not a profile someone
  flips at 2am.
