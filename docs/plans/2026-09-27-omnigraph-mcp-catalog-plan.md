# Omnigraph + MCP clients in the AutoOS catalog — plan (2026-09-27, v2)

Spec: [`2026-09-27-omnigraph-mcp-catalog-spec.md`](./2026-09-27-omnigraph-mcp-catalog-spec.md).
The order follows the AutoOS review (SPEC-OMNI). This plan contains AutoOS tasks only; the
deployment-side tasks live in the operator's private repo. `catalog/` edits go through L1-routing.
Every component task runs twice: the second run must report "skipped / unchanged".

| # | Task | Verify |
|---|---|---|
| A1 | Merge `L1-backlog/asm2` (infra re-import, secrets scrubbed, `PERPLEXITY_API_KEY` from env) and `L1-backlog/asmb` (installers no longer clone agent-skills). Compare with the operator's older local import so nothing is lost. | `infra/mcp-servers/servers/omnigraph-viewer` exists on `main`; the Linux suite is green; `run-tests.ps1` has an operator run. |
| A2 | Commit this scrubbed spec and plan. Extend the public-scrub scanner with RFC 1918, CGNAT, hostname and site-domain patterns (spec §E), and run it on `docs/`, `catalog/` and `infra/` in CI. | The scanner flags a planted RFC 1918 fixture address and passes on the tree. |
| A5 | `tools/check-omnigraph-bridge.{sh,ps1}` with a fake-server mode (spec §B): unit-testable offline, then one live run against `<omnigraph-url>` (16 parallel, cold and warm). | CI runs the fake-server test. The live numbers are recorded and decide npx or pre-installed (D9). |
| A3 | `omnigraph-client` component + `omnigraph-mcp-autoos` wrapper (sh + ps1). Inputs: prompt `omnigraph_url` and the new `api-keys.yml` key `omnigraph_token`; skip with a hint if either is missing. Output: `~/.autoos-omnigraph.env`. Recognised-only removals (spec §C). Token-rotation section in `docs/omnigraph.md`. | In a **non-interactive** `bash -c 'claude mcp list'`, omnigraph is connected; `whoami` returns each repo's pinned slug. Second run: skipped. |
| A4 | `mcp-graphify`: `uv tool install 'graphifyy[mcp]==<pin>'`, idempotent; remove the recognised symlink into agent-skills; homepage points to upstream. | `command -v graphify-mcp` is in the uv tool dir; graphify is connected in three repos. |
| A4b | Verify `mcp-serena` (uvx stdio, pinned), playwright and context7; report a recognised stale user-scope `homelab` entry (removal deferred by operator decision 2026-09-28, spec §C). | No "conflicting scopes" warning; all connected. |
| A6 | `.github/workflows/omnigraph-images.yml` (GitHub Actions, GitHub-hosted runners) builds the viewer and pushes to `ghcr.io/<owner>/omnigraph-viewer:<sha>` using `GITHUB_TOKEN`; no operator prerequisite is needed to start. `infra/mcp-servers/cluster/cluster.yaml.example` + `cluster.schema.json` tracked; the live `cluster.yaml` never. | The image appears in GHCR (a workflow run pushes it); GitHub CI (`ci.yml`) still passes; the workflow only triggers on paths that touch the viewer / this workflow file (not every push) to avoid needless registry writes. |
| A7 | **Done on Linux and macOS** (2026-09-28); Windows is a later lane. `agent-skills` catalog entry becomes a tombstone (id known, installs nothing), documented in `docs/catalog.md`. | A fresh ai-coding dry run never touches the agent-skills repo. |

A6 has **no operator prerequisite**: the build uses GitHub-hosted runners and the automatic
`GITHUB_TOKEN`. Harbor and the local Forgejo runner remain available for local test builds only,
and are deliberately not the tracked deployment path (operator decision 2026-09-28).
