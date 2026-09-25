# Web services on the coding host

Every web UI and HTTP service that runs on the AI coding host, what guards
it, and whether the LAN reverse proxy publishes it. Measured on the host on
2026-09-24 with `ss -ltnp` and `curl` against each port. Hostnames are
written as `<name>.<domain>`: the real domain lives in the proxy's own
configuration, never in this public repository.

The proxy is another machine on the LAN (TLS + single sign-on in front of
every published name). A service it publishes must therefore listen on an
address that machine can reach (`0.0.0.0`), and must still keep its own
login where it has one: the SSO layer is the second lock, not the only one.

## At a glance

| Service | Port | Bind | Own auth | Proxied as | Start | Health |
|---|---|---|---|---|---|---|
| OmniRoute gateway + dashboard | 20128 | `0.0.0.0` | `/v1/*`: client key (`REQUIRE_API_KEY=true`); dashboard + `/api/*`: login session | `omniroute.<domain>` | unit `autoos-omniroute` / `omniroute --no-open --port 20128` | `GET /api/health` → 200 |
| opencode serve (web UI + API) | 4096 | `0.0.0.0` | HTTP Basic on `/api/*`, user `opencode`, password in `~/.config/autoos/opencode-serve.password` | `opencode.<domain>` | unit `autoos-opencode` / `configuration/start-stack.sh opencode-serve` | `GET /` → 200; `GET /api/session` → 401 without the password |
| OpenHands (docker app) | 3000 | `0.0.0.0` | **none** | `openhands.<domain>` | `configuration/start-stack.sh openhands` (container `--restart unless-stopped`) | `GET /` → 200 |
| LiteLLM fallback proxy | 4000 | `127.0.0.1` | `LITELLM_MASTER_KEY` | not proxied - local fallback only; clients use the gateway | unit `autoos-litellm` / `configuration/litellm/start-litellm.sh` | `GET /` → 200 |
| AutoOS browser UI | 8777 | `127.0.0.1` (default) | per-run token in the URL | not proxied - an installer with root-level effects; reach it over SSH or the tailnet (`--bind`) | `./setup.sh --serve` | `GET /api/ping?token=…` → 200 |
| Omnigraph server | 8080 | `0.0.0.0` | bearer token (`OMNIGRAPH_TOKEN`) | not proxied - agent memory API, LAN clients only | docker compose (omnigraph stack) | `GET /healthz` → 200; `/graphs` → 401 without the token |
| Omnigraph viewer | 8090 | `0.0.0.0` | none | not proxied | docker compose (omnigraph stack) | `GET /` → 200 |
| Omnigraph object store (MinIO) API | 9000 | `127.0.0.1` | MinIO access key | not proxied - storage backend of the server | docker compose (omnigraph stack) | `GET /minio/health/live` → 200 |
| Omnigraph object store (MinIO) console | 9001 | `0.0.0.0` | MinIO console login | not proxied | docker compose (omnigraph stack) | `GET /` → 200 |
| Serena MCP (streamable HTTP/SSE) | 9121 | `127.0.0.1` | none (loopback only) | not proxied - local agents only | `serena-mcp` container | `GET /sse` → 200 |
| Serena dashboard | 24282 | `127.0.0.1` | none (loopback only) | not proxied | `serena-mcp` container | `GET /` → 302 `/dashboard/` |
| Sablier front (Caddy) | 8199 | `0.0.0.0` | none - it only wakes scale-to-zero tools | it IS the upstream for the proxy's scale-to-zero names (docs, code-server) | docker compose (sablier stack, Server repo) | `GET /` → 200 |

## Per service

### OmniRoute gateway (:20128)

- **Listens** on `0.0.0.0:20128`; loopback-only helper ports `20131`/`20132`
  are internal to the process.
- **Auth**: `REQUIRE_API_KEY=true` in `~/.omniroute/.env` - every `/v1/*`
  request needs a client key (`Authorization: Bearer sk-…`), a keyless one
  gets 401. The dashboard redirects to `/login`; management routes
  (`/api/providers`, `/api/combos`, `/api/resilience`) answer 401/403 without
  a dashboard session. The local CLI (`omniroute api …`) uses a machine
  loopback token, which is why `apply.sh` sets resilience through the CLI.
- **Websockets**: `/live-ws` (dashboard live view) and the Responses API
  websocket under `/v1/`.
- **Start**: `configuration/autostart/register-autostart.sh` installs the
  `autoos-omniroute` unit (Type=notify, watchdog). By hand:
  `omniroute --no-open --port 20128` from `$HOME`.

### opencode serve (:4096)

- V2 (`@opencode/cli` 2.x) serves its web UI to anyone and guards `/api/*`
  with HTTP Basic auth: user `opencode`, password from `OPENCODE_PASSWORD`.
  Without that variable it invents a new password on every start, so
  `configuration/autostart/run-opencode-serve.sh` pins one in
  `~/.config/autoos/opencode-serve.password` (mode 600).
- **Streams**: `/api/event` (server-sent events - no response buffering)
  and websockets under `/api/pty/…` (terminal).
- The providers it offers come from the global `~/.config/opencode/opencode.json`
  (`providers.omniroute`, `providers.litellm`, projected from the repo's
  `opencode.jsonc` by `setup_opencode_config`); keys are `{env:…}` references
  the wrapper exports.

### OpenHands (:3000)

- The docker app (`docker.openhands.dev/openhands/openhands`), started with
  `SANDBOX_USER_ID=$(id -u)` so `~/.openhands` stays the user's, a memory cap
  (`AUTOOS_OPENHANDS_MEMORY`, default 2g) and `--restart unless-stopped`.
  Each conversation starts an `agent-server` sandbox container on a random
  host port; the app reaches it itself, the proxy never needs to.
- **No login of its own.** Anyone who reaches `:3000` can run commands with
  the docker socket - root-equivalent on the host. Publish it only behind
  the SSO proxy and restrict the port at the host firewall to the proxy's
  address (operator step, needs sudo).
- **Websockets**: `/socket.io/` on the app. The conversation itself runs
  over the **sandbox's** own API and websocket (`/api/conversations/…`,
  `/sockets/events/<id>`), and the UI hands the browser that sandbox's URL:
  `http://localhost:<random port>` by default, which only works in a browser
  on this host. For a remote browser, set `AUTOOS_OPENHANDS_SANDBOX_URL` to a
  pattern with `{port}` that the proxy maps back to `<coding-host>:{port}`
  (for example `https://openhands.<domain>/sbx/{port}`, with the proxy
  stripping `/sbx/<port>`), plus `AUTOOS_OPENHANDS_WEB_HOST=openhands.<domain>`
  for CORS, then recreate the container (`docker rm -f openhands-app`,
  `start-stack.sh openhands`). Sandbox ports are published on all interfaces
  and guarded by a per-sandbox session key (`X-Session-API-Key`); an
  unauthenticated call gets 401.
- Tier profiles live in the app's own settings store (it does not read
  `~/.openhands/profiles/*.json`); `start-stack.sh openhands` pushes them
  through `/api/v1/settings/profiles` (`tools/sync-openhands-profiles.py
  --push-url`). The app keeps at most 10, filled in
  `configuration/openhands/tier-profiles.json` order: the push deletes the
  `omniroute-*` / `litellm-*` / `openrouter-*` profiles the spec no longer
  lists and removes the lowest-ranked of them to make room for a
  higher-ranked tier. AutoOS owns every profile with those prefixes, so
  name your own profiles differently: any other name, and the active
  profile, is never deleted.
- On a native Linux host the `litellm-*` profiles cannot reach LiteLLM: it
  binds `127.0.0.1`, which a container cannot reach through
  `host.docker.internal`. The `omniroute-*` profiles work.

### LiteLLM (:4000)

- Loopback only, on purpose: OmniRoute is the gateway the phone and the
  proxy use; LiteLLM is the manual fallback for local tools.
  `start-litellm.sh` reads `configuration/litellm/.env` literally and restarts
  a proxy that runs with stale keys.

## What the proxy needs

| Name | Upstream | Websocket / streaming paths | May bypass SSO | Headers |
|---|---|---|---|---|
| `omniroute.<domain>` | `<coding-host>:20128` | `/live-ws`, `/v1/*` (SSE + Responses websocket) | `/v1/*` only - API clients cannot do an SSO login; the client key guards it | `Host`, `X-Forwarded-For`, `X-Forwarded-Proto` |
| `opencode.<domain>` | `<coding-host>:4096` | `/api/event` (SSE, no buffering), `/api/pty/*` (websocket) | nothing | pass `Authorization` through untouched (the app's Basic auth) |
| `openhands.<domain>` | `<coding-host>:3000` | `/socket.io/*`; plus `/sbx/<port>/*` → `<coding-host>:<port>` (sandbox API + `/sockets/*` websockets) when `AUTOOS_OPENHANDS_SANDBOX_URL` uses that pattern | nothing - the app has no login | `Host`, `X-Forwarded-Proto`; keep `X-Session-API-Key` |

Every service above is listed in the web UI's router card (live up/down,
bind, auth) with its start action: [web-ui.md](web-ui.md).
