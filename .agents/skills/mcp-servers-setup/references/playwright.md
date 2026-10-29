> Part of [mcp-servers-setup](../SKILL.md).

### Playwright — Browser Automation (UI validation, E2E)

Strictly for UI validation, screenshot testing, and E2E browser tasks against sites
you build — **not** a web-search tool. Exposes 24 tools (`browser_navigate`,
`browser_snapshot`, `browser_click`, `browser_take_screenshot`,
`browser_console_messages`, `browser_network_requests`, `browser_fill_form`, …);
screenshots return inline in the tool response, so no output volume is needed.

#### Wiring — ONE user-scope entry, serves every repo. Transport differs per host.

Defined **once in user scope** (like graphify) so it works from any repo Claude Code
launches in — no per-repo `.mcp.json` entry, no approval step. Which transport
depends on whether the host has node:

**Workstation (node present) — a global install, never `npx @latest`.**

```bash
npm i -g @playwright/mcp
claude mcp add -s user playwright -- \
  node "$(npm root -g)/@playwright/mcp/cli.js"
```

> **Never `npx @playwright/mcp@latest`.** The `@latest` tag forces a registry
> resolve on **every spawn**, and every Claude Code session spawns its own copy.
> Measured 2026-09-04: `npx @latest` **3521 ms** to first MCP response vs **392 ms**
> for the global install — **9× slower**, and npx additionally serializes on the
> shared npm cache, so the cost compounds with concurrency. That is what pushed
> `context7` past the 30 s connect deadline; see
> [`unattended-orchestration`](../../unattended-orchestration/SKILL.md) for why spawn
> concurrency, not CPU, is the variable that matters. Update deliberately with
> `npm i -g @playwright/mcp@latest`.

**Server (`coding.example.internal`, no node/npx) — Microsoft's official image over Docker, behind a lazy proxy.**

A container registered directly (`docker run -i …` as the `claude mcp add` command) lives
as long as its Claude Code session, browsing or not: measured 2026-09-26, 7 live sessions
held 7 idle containers (2.5–38 MiB each, no Chromium), because Claude Code starts every
stdio server at session start and does not reconnect one that exited. So sessions get
`tools/playwright_mcp_lazy.py` instead, a stdio proxy that answers the session-start
handshake from a cache, starts the container on the first real request, stops it after
`AUTOOS_PLAYWRIGHT_IDLE_SECONDS` (default 900) without traffic and starts a fresh one on
the next request (browser state does not survive a stop).

```bash
docker pull mcr.microsoft.com/playwright/mcp:latest
# from the AutoOS checkout; ./setup.sh --only mcp-playwright registers the same entry
claude mcp add -s user playwright -- python3 "$PWD/tools/playwright_mcp_lazy.py"
```

- The proxy's default backend is `docker run -i --rm --init --network host --name
  autoos-pw-<pid>-<n> mcr.microsoft.com/playwright/mcp:latest`; `AUTOOS_PLAYWRIGHT_MCP_CMD`
  replaces it. The very first session on a machine has no handshake cache and starts one
  container to fill it (`~/.cache/autoos/playwright-mcp/handshake.json`, in a directory of its
  own because `~/.cache/autoos` is shared with the image cache). The proxy reads
  that file only if it is a regular file of at most 4 MiB, owned by you and not writable
  by group or others, in a directory that is too (never through a symlink); otherwise it
  logs one line to stderr and starts the backend as on a cold cache. An existing
  group-writable directory (say from a umask of 002) is refused: `chmod go-w` it.
- **`--network host`** (Linux) lets the containerized browser reach dev servers on
  the host's `localhost:PORT`, so an agent tests the site it just built with the URL
  it would naturally type. Trade-off: less network isolation; fine on a single-user
  dev host. Portable alternative: `--add-host=host.docker.internal:host-gateway` and
  browse `http://host.docker.internal:PORT`.
- **`--rm`** → ephemeral browser profile per run; **`--init`** reaps zombie browser
  processes.
- On Linux with docker the installer registers the proxy, and replaces an existing
  entry only when it is exactly the old docker form, `npx -y @playwright/mcp…`, or this
  proxy at the path of another (moved) checkout; every registration or replacement is
  preceded by a backup of the Claude config, and a failed backup stops it. macOS and
  hosts without docker keep the npx entry. Sessions that are already running keep their
  current container until they end.
- Status: tested against a fake backend (`tests/test_playwright_mcp_lazy.py`) and observed
  2026-09-26 in a real Claude Code session against the real image: with a warm cache no
  container started, the first browsing call started one (~1.5 s cold), and with
  `AUTOOS_PLAYWRIGHT_IDLE_SECONDS=20` it stopped ~20 s after the last call (default 900 s).
  The backend advertises only `tools` (no `prompts` or `resources` to force a start); the
  proxy itself holds ~13 MB RSS.

Verify — authoritative client health check, then a real browser drive:
```bash
claude mcp list                       # → playwright … ✔ Connected
# functional: initialize → tools/call browser_navigate https://example.com → snapshot
```

With the proxy, `claude mcp list` is answered from the handshake cache and no longer
proves the container runs; the functional drive does.

> **Restart to load it.** MCP servers initialize only at session start, so a running
> session won't see a newly-added `playwright` until it restarts.

> **The tool prefix follows the wiring, and briefs depend on it.** A user-scope
> server is `mcp__playwright__*`; the *same* server installed as a plugin is
> `mcp__plugin_playwright_playwright__*`. Searching the wrong one finds nothing and
> reads as "the server is down". Anything that pre-allows tools by name — an
> `--allowedTools` list, an unattended run's config — must match the wiring actually
> in force. Check with `claude mcp list`, never from memory.

