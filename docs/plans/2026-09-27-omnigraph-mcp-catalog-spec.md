# Omnigraph + MCP clients in the AutoOS catalog — spec (2026-09-27, v2)

Status: **approved design, revised after the AutoOS review (SPEC-OMNI, 2026-09-27).**
Plan: [`2026-09-27-omnigraph-mcp-catalog-plan.md`](./2026-09-27-omnigraph-mcp-catalog-plan.md).

**This repo is public. The spec is site-free on purpose:** every host, address, domain, VM id and
path is a placeholder (`<omnigraph-url>`, `<server-vm>`, `<lan-cidr>`, `<tailnet-cidr>`,
`<registry>`, `<ci-runner>`). Real values and the deployment-side tasks live in the operator's
private infrastructure repo, never here.

## Why

`agent-skills` is retired. AutoOS already ships the skills. The import of `infra/` (the Omnigraph
server stack, viewer, MCP servers and scripts) is in flight on `L1-backlog/asm2` +
`L1-backlog/asmb`. Two things remain:
- the client side must become a clean, site-free catalog component that works in
  non-interactive shells;
- the server side needs images the operator's deployment can pull without a checkout on the host.

The operator is also moving the central Omnigraph server off the coding machine
(*"coding should be only for the projects"*). That changes only `<omnigraph-url>` for clients.

## Decisions (operator Q&A 2026-09-27; AutoOS-relevant subset)

| # | Decision |
|---|---|
| D1 | **Split ownership.** AutoOS owns the code, the images (`infra/mcp-servers/servers/*`) and the graph-list schema. The operator's infrastructure repo deploys the images. |
| D6/D7 | Clients reach the server at `<omnigraph-url>` (HTTPS through the operator's reverse proxy) with a **bearer token**. The API is limited to `<lan-cidr>` and `<tailnet-cidr>` by the proxy. There is **no browser SSO on the API**, because MCP clients can't do it. |
| D8 | Token source on each machine: the git-ignored `configuration/api-keys.yml`. The server side has its own copy in the private repo, and one rotation procedure updates both. |
| D9 | **Client bridge:** use npx only if **16 bridges started in parallel each answer `health` in < 2 s** with zero npm cache-lock errors. Otherwise use a pre-installed, pinned bridge started directly. |
| D10/D11 | Images are built by **Forgejo CI on a LAN runner** (`<ci-runner>`) and pushed to `<registry>`, project `autoos`: private, robot accounts, git-SHA tags, never `latest`. GitHub CI must pass without LAN access. |
| D12 | **Serena:** already `uvx` stdio and pinned in the catalog, so the job is to verify it. The central SSE container is dropped on the deployment side, not here. |
| D13 | **graphify:** move from pinned `uv run` to **`uv tool install 'graphifyy[mcp]==<pin>'`** (idempotent), so that `graphify-mcp` is on PATH on Linux and Windows. |
| D14 | homelab MCP is not an AutoOS component. AutoOS only removes stale entries it recognises (see §C). **Removal deferred 2026-09-28 by operator decision:** nothing removes it; a recognised leftover is only reported (§C). |
| D15 | playwright / context7: verify only. |
| D18 | Every Linux and Windows machine gets a working client set through the catalog, using uv or npm, not Docker. |

## A. `omnigraph-client` component (site-free)

- **Inputs:**
  - URL: the existing per-machine prompt `omnigraph_url`, stored as `omnigraph_base_url`.
  - Token: a **new** git-ignored `api-keys.yml` key, `omnigraph_token`.
  - If either is missing, the component is **skipped with a hint**, not failed.
- **Output:** `~/.autoos-omnigraph.env` (mode 0600 / user-only ACL on Windows) holding
  `OMNIGRAPH_BASE_URL` and `OMNIGRAPH_TOKEN`. The per-repo graph id stays in each repo's
  `.mcp.json` (`OMNIGRAPH_GRAPH_ID`).
- **Bridge wrapper:** `omnigraph-mcp-autoos` (sh + ps1) reads `~/.autoos-omnigraph.env` **itself**,
  then execs the bridge chosen by D9. That way a non-interactive `bash -c` or a Windows service gets
  the token without any rc-file line. The user-scope MCP entry calls the wrapper. The old rc-file
  token line is removed only if it has the exact form AutoOS recognises (§C).
- **Profiles:** workstation, ai-coding, light. Whether `server` is included is an open question for the operator.

## B. Bridge benchmark (runs before A, review item 16)

`tools/check-omnigraph-bridge.{sh,ps1}`:
- starts N bridges in parallel (default 16), cold and warm npm cache;
- measures spawn → `health` latency and counts lock errors;
- checks `whoami` against the pinned graph.

A **fake Omnigraph server** mode (a local stub answering `/healthz` and a canned query) lets CI test
the logic offline. The live run against `<omnigraph-url>` is an operator/agent step, and its numbers
are recorded in the component entry.

## C. Removals (review item 10)

AutoOS only removes entries it recognises: read, back up, rewrite; a second run reports "skipped".
Targets:
- the rc-file token line that reads from the retired agent-skills tree;
- the `graphify-mcp` symlink into agent-skills;
- a user-scope `homelab` MCP entry whose `PYTHONPATH` points into agent-skills;
  **not removed** (operator decision 2026-09-28 — the server-side homelab MCP is
  not finished): the entry is named by a muted report line and left in place until
  that server replaces it.
- the `agent-skills` catalog entry, which becomes a **tombstone**: the id stays known so state files
  resolve, and it installs nothing.

## D. Images and graph list (review items 3, 4, 5)

- No catalog entry for server images. The images are defined by `infra/mcp-servers/servers/*`
  (viewer; optionally a cluster-config image) and built by `.forgejo/workflows/omnigraph-images.yml`
  in its own file. The registry host and robot credentials exist **only as Forgejo secrets/vars**.
- `infra/mcp-servers/cluster/` holds **`cluster.example.yaml` + the schema only**. The live
  `cluster.yaml` (graph names, hosts, storage) is never tracked here. It reaches the deployment from
  the operator's private repo.

## E. Secret scan (review item 15)

The public-scrub scanner (arriving with asm2) learns private-IP (RFC 1918 / CGNAT), hostname and
site-domain patterns. CI fails on a hit in `docs/`, `catalog/` and `infra/`.

## F. Windows

Windows CI is off. Every Windows change needs an operator run of `tests/run-tests.ps1`, or it stays open.

## Out of scope

- The server deployment itself: the stack, reverse-proxy site, data cutover and backups belong to
  the operator's private repo.
- The `agent-skills` graph data.
- Deleting the `agent-skills` GitHub repo (operator, after the canary).

## Open (operator)

- `omnigraph-client` in the `server` profile, i.e. headless servers get an agent MCP bridge?
  Resolved 2026-09-28 04:50Z: **yes** — Linux `server` profile carries the component. A server
  with no `omnigraph_url` answer or token still reports `skipped`, so the profile costs nothing
  where no graph is configured. macOS keeps its three profiles (no `server` profile there).
