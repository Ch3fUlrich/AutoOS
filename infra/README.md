# infra/ — Imported Infrastructure

This directory was imported from **Ch3fUlrich/agent-skills** at commit **cfb4fc6** on **2026-09-27**.
The agent-skills repository is **retired**; AutoOS is now the single home for this infrastructure.

## Top-level directories

| Directory | Purpose |
|---|---|
| `mcp-servers/` | Self-hosted MCP server stack: Serena (LSP), Graphify (code graph), Omnigraph (structured memory), Superpowers (workflow skills), Playwright (browser), Context7, Sentry, Datadog. Includes Docker Compose, config templates, cluster bootstrap, and setup scripts for server/client/offline modes. |
| `local-ai/` | Optional local LLM stack: Ollama, LiteLLM proxy (Perplexity routes), Open WebUI, Ollama agent sidecar. Docker Compose + LiteLLM config + Perplexity pipe function for Open WebUI. |
| `remote-access/` | Herdr agent multiplexer — persistent terminal sessions for running multiple agents. Helper scripts for Linux/macOS/Windows to install and start/reattach sessions. |

## Provenance

- **Source repo**: retired `agent-skills` repo (history; its content now lives in this repository)
- **Import commit**: cfb4fc6
- **Import date**: 2026-09-27
- **Import method**: `git archive` (export-ignored paths dropped)
- **Status**: agent-skills is retired; all future development happens in AutoOS

## Repo rules for infra/

- **`.env.*.example`** files are tracked as templates with placeholder values
- **Real environment files** (`.env`, `.env.shared`, `.env.server`, `.env.client`, `.env.local`) are gitignored
- **`cluster/cluster.yaml`** and **`cluster/seed/`** are gitignored (contain real graph/repo names)
- **Line endings**: LF for shell scripts (`.sh`), Python (`.py`), systemd units (`.service`, `.timer`), Dockerfiles, compose files, JSON, YAML, Markdown; CRLF with UTF-8 BOM for PowerShell (`.ps1`, `.psm1`)
- **No vendor binaries or installers** — download at runtime from vendor URLs
- **No secrets** — all real values live in gitignored files or environment variables

## Completeness note

The import used `git archive`, which drops `export-ignore` paths, so a companion
file referenced by a README or compose file may in principle be absent from the
tree. Nothing was invented to fill a gap — only tracked templates and
documentation were added. There is no separate completeness inventory to consult
(an earlier pointer to a "COMPLETENESS report" / "asm-a REPORT" named a file that
never existed and has been dropped): the authoritative checks are the CI
`check-links` and `public-scrub` gates, which fail a build on a broken relative
reference or a leaked private literal. What those gates cover, and the changes
made in the asm-a2 review round, are recorded in [`../CHANGELOG.md`](../CHANGELOG.md).