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

On a **server** the gateway, opencode serve and OpenHands run as one hardened
compose stack instead (same ports, same auth, containers `autoos-omniroute`,
`autoos-opencode`, `openhands-app`): [Server profile: the AI stack in Docker](#server-profile-the-ai-stack-in-docker).
The table below describes the native (workstation) layout.

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
  --push-url`). The app keeps at most 10.
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

## Server profile: the AI stack in Docker

On a server, AutoOS runs the three LAN-facing AI services as one hardened
compose stack instead of host processes: the OmniRoute gateway (`:20128`),
`opencode serve` (`:4096`) and OpenHands (`:3000`). The workstation profile
keeps the native installs (the sections above).

Files: [`configuration/docker/ai-stack/`](../configuration/docker/ai-stack/) -
`compose.yml`, `opencode.Dockerfile`, `stack.env.example` and `ai-stack.sh`,
the one entry point (`init`, `up`, `down`, `status`, `is-active`, `migrate`,
`rollback`; `--dry-run` with any of them). Catalog id: `ai-stack-docker`,
pre-ticked in `server`, requires `docker`.

### Why docker on a server

- **One firewall mechanism.** The host firewall's `DOCKER-USER` rule drops new
  LAN connections to docker-published ports except from the reverse proxy.
  Host processes are not covered by it; containers are. Moving the gateway and
  opencode into docker puts all three published names behind the same rule
  (the Server repo's firewall playbook lists `4096` and `20128` next to
  `3000`).
- **Pinned, reproducible services.** Images are pinned by digest; an upstream
  retag cannot change the gateway underneath the encrypted credentials.
- **Limits the host enforces.** Memory, process count and capabilities are
  declared per service instead of trusted.

### Layout

| Service | Image | Container | Runs as | Published | State |
|---|---|---|---|---|---|
| `omniroute` | `diegosouzapw/omniroute:3.8.50@sha256:085c…` | `autoos-omniroute` | host uid:gid | `${AUTOOS_STACK_BIND}:20128` | `~/.local/share/autoos/ai-stack/omniroute` -> `/app/data` |
| `opencode` | local `autoos/opencode:2.0.16-autoos1` (FROM `ghcr.io/anomalyco/opencode:2.0.16@sha256:1644…`) | `autoos-opencode` | host uid:gid | `${AUTOOS_STACK_BIND}:4096` | `…/ai-stack/opencode-home` -> `/home/opencode`; the code tree at the same path |
| `openhands` | `docker.openhands.dev/openhands/openhands@sha256:17d0…` | `openhands-app` | root entrypoint -> `enduser` (host uid) | `${AUTOOS_STACK_BIND}:3000` | `~/.openhands` -> `/.openhands` |

All three sit on the network `autoos-ai`, so OpenHands and opencode reach the
gateway as `http://omniroute:20128/v1`. The code tree (`AUTOOS_CODE_DIR`, the
main checkout's parent, e.g. `~/code`) is mounted **at the same path** in
opencode and, through `SANDBOX_VOLUMES=<code>:<code>:rw`, in every OpenHands
sandbox: an absolute path in a prompt, a session or a worktree means the same
file for opencode, OpenHands, the serena container and the host CLIs.

#### Why each hardening flag

| Flag | Where | Why |
|---|---|---|
| image `@sha256:` digest | all | a tag can move; the gateway holds encrypted credentials and a changed schema must be a deliberate bump |
| `restart: unless-stopped` | all | docker resumes the stack at boot; a hand `docker stop` sticks |
| `security_opt: no-new-privileges:true` | all | no setuid binary inside can raise privileges |
| `cap_drop: [ALL]` | all | none of the three needs a capability to serve HTTP |
| `cap_add: CHOWN DAC_OVERRIDE FOWNER SETUID SETGID` | openhands | measured: its entrypoint must start as root to `useradd` the sandbox user with the host uid and `su` to it; nothing else was needed |
| `user: ${AUTOOS_UID}:${AUTOOS_GID}` | omniroute, opencode | data dirs stay owned by the operator (backup, rollback, `ls` without sudo); files opencode writes in the code tree are the operator's |
| `read_only: true` + `tmpfs: /tmp` | omniroute, opencode | measured: both run with a read-only root; only their data volume and `/tmp` are writable |
| `mem_limit`, `pids_limit` | all | the host shares ~10 GB with the agents; a runaway process is killed in its own cgroup, not by the host OOM killer picking at random |
| `healthcheck` | all | `docker ps` and `healthcheck.sh` show a real verdict; OmniRoute's probe is its in-memory `/healthz` (the deep SQLite probe flipped busy gateways unhealthy upstream) |
| `REQUIRE_API_KEY: "true"` in `environment` | omniroute | process env beats OmniRoute's `.env`, so `/v1` can never be left keyless by a file edit |
| logging `max-size 10m`, `max-file 3` | all | a chatty gateway cannot fill the disk |
| one `${AUTOOS_STACK_BIND}` for every port | all | a single place decides the publish address (see below) |

#### The publish address

`AUTOOS_STACK_BIND` defaults to `0.0.0.0`, deliberately: host CLIs (Claude
Code, `autoos-agent.py`, `apply.sh`) talk to `127.0.0.1:20128`, OpenHands
sandboxes reach the gateway through the docker gateway address
(`host.docker.internal`), and the proxy uses the LAN address. Narrowing the
bind would break one of them; the LAN exposure is instead limited by the
`DOCKER-USER` firewall rule to the proxy.

#### The docker socket (OpenHands)

OpenHands starts one `agent-server` sandbox container per conversation, so it
mounts `/var/run/docker.sock`. That is **root-equivalent on the host**: anyone
who can drive the OpenHands UI can start a privileged container. OpenHands has
no login of its own. It must only ever be reachable through the SSO proxy, and
the firewall rule is what enforces that for LAN clients. Sandboxes are started
by the app, outside compose, with their own random published ports (also
covered by the firewall's `32768-60999` range).

#### Secrets

Nothing secret is in a tracked file. `ai-stack.sh init` writes, mode `600`
under a `700` directory `~/.config/autoos/ai-stack/`:

| File | Holds | Read by |
|---|---|---|
| `stack.env` | uid/gid, paths, bind, RAM - no secret | compose (`--env-file`) |
| `opencode.env` | `OPENCODE_PASSWORD` (reused from `~/.config/autoos/opencode-serve.password`, so phone logins survive), the gateway client key, `OMNIGRAPH_TOKEN`, every other `{env:…}` key the rendered config references | opencode only |
| `openhands.env` | `LLM_API_KEY`, and the remote-browser pair `OH_SANDBOX_CONTAINER_URL_PATTERN` + `WEB_HOST` (carried over from the running container) | OpenHands only |
| `manage.key` | a manage-scoped gateway key | host CLI only (`apply.sh`) - never a container |

`init` only **appends missing keys** (backup first); a value you edit stays
yours. Each container gets only its own file: an agent's shell can read its
container's environment. OmniRoute keeps `STORAGE_ENCRYPTION_KEY` in its own
data directory's `.env`, as it does natively.

**Why a manage key.** The host `omniroute` CLI authenticates management calls
with a machine token that the gateway accepts from **loopback peers only**. A
published container port sees the docker gateway address as the peer, so the
CLI would be refused. `ai-stack.sh migrate` creates a key with scope `manage`
while the native gateway still answers; `apply.sh` exports it as
`OMNIROUTE_API_KEY` whenever the stack is active. The CLI inside the image
cannot replace this: it fails to start (`tsx` is not in the image).

### opencode in the container

The config is **derived**, never kept twice:
`tools/render-opencode-container-config.py` rewrites the host's
`~/.config/opencode/opencode.json` into the container home on every `init`.

| Piece | In the container | Why |
|---|---|---|
| provider `omniroute` | `http://omniroute:20128/v1` | the compose service name |
| providers `litellm` (`:4000`), `ollama` (`:11434`) | **dropped** | they bind `127.0.0.1` on the host; no container can reach that |
| MCP `serena` | remote `http://serena-mcp:9121/sse` | the shared serena container (which mounts the code tree at the same path) instead of a per-process `uvx`; `ai-stack.sh up` attaches it to `autoos-ai` because it publishes on loopback only. The attachment is lost when serena-mcp is recreated; every `up` re-checks it |
| MCP `omnigraph` | `npx` bridge, `OMNIGRAPH_BASE_URL=http://host.docker.internal:8080` | omnigraph-server is published on the host; token from `opencode.env` |
| MCP `graphify`, `context7` | unchanged (`uvx` / `npx`) | the layer adds `uv`, `python3`, `nodejs`; packages download into the persistent home on first use |
| MCP `playwright` | **disabled** | it drives a browser the Alpine image does not ship; use it from a host client |
| `instructions` outside the code tree | **dropped** | not mounted, so not readable |
| skills link | recreated when it points into the code tree | AGENTS.md §8 wiring keeps working |

Why a local layer at all: the upstream image is V2 (`opencode --version` ->
`v2.0.16`, the same as the host) but bare Alpine - no `git` (an agent could
not commit), no `bash`, no `node`/`npx`, no `uv`. `opencode.Dockerfile` adds
exactly those plus `curl` for the healthcheck (image 225 MB -> 382 MB).

### RAM budget (this host: 9.9 GB, ~3 GB free)

Measured 2026-09-25: native OmniRoute 700 MB RSS (+130 MB launcher),
native opencode serve 120 MB, OpenHands app 366 MB, one sandbox ~240 MB.
Hardened probe containers: OmniRoute 486 MB after warm-up, opencode 100 MB.

| Service | Heap / limit | Reasoning |
|---|---|---|
| omniroute | `OMNIROUTE_MEMORY_MB=1536`, `mem_limit 2560m` | the image default heap (1024) is sized for a dashboard; coding agents on `/v1/responses` need more. Upstream sizes one coding agent at 8 GiB heap / 10 GB container - more than this host has free. 1.5 GiB heap is the ceiling this host can give without starving the agents; native buffers sit outside V8, hence the 1 GB headroom. **Documented ceiling**: two overlapping long `/v1/responses` contexts can exhaust it - the container then restarts instead of the host swapping. Raise both on a bigger host. |
| opencode | `mem_limit 1536m` | 100 MB idle; MCP children (`npx`, `uvx`) and sessions grow it |
| openhands | `mem_limit 2g` (`AUTOOS_OPENHANDS_MEMORY`) | unchanged from start-stack.sh; sandboxes are separate containers outside this limit |

Total ceilings 6 GB; typical use ~1.5 GB, about what the native processes used.

### Migration (an existing native host)

Opt-in and announced; `ai-stack.sh migrate` without `--yes` only prints the
plan:

1. `init`, then build/pull the images.
2. Create the manage key while the native gateway still answers.
3. Back up `~/.omniroute` to `~/.local/share/autoos/ai-stack/backups/omniroute-<ts>.tar.gz` (0600).
4. Stop the `autoos-omniroute` unit (a hand-started gateway: `omniroute stop`). The gateway is down from here.
5. **Copy** `~/.omniroute` (DB + `.env` with `STORAGE_ENCRYPTION_KEY` - without
   it the stored provider credentials are unreadable) into the data dir. The
   original stays untouched.
6. Start the container; it must answer `/api/health` **and** refuse a keyless
   `/v1` call with 401. If not, the container stops and the unit starts again.
7. Only then `register-autostart.sh --unregister --only autoos-omniroute`.
8. The same for `autoos-opencode`: stop, start the container, prove it
   answers, then unregister.
9. Remove the `docker run` `openhands-app` (state stays in `~/.openhands`;
   running sandboxes keep running) and start the compose one through
   `start-stack.sh openhands`, which still repairs the settings and pushes the
   tier profiles.

Afterwards `register-autostart.sh` skips the gateway and opencode units while
the stack is active, and `Start-AutoOSStack.sh`, `start-stack.sh`,
`healthcheck.sh` and `apply.sh` resume/manage the containers instead.

#### Rollback

`ai-stack.sh rollback --yes` (run it from the checkout the units should point
at):

1. `docker compose down` - containers removed, bind-mounted data kept.
2. Back up `~/.omniroute`, then copy the container's data back into it (the
   container's state is the newest: keys, combos, usage).
3. `register-autostart.sh --only autoos-omniroute,autoos-opencode`.
4. `start-stack.sh openhands` (the `docker run` container, as before).

### Bumping an image

Resolve the new digest (`docker buildx imagetools inspect <image>:<tag>`),
change the `image:` line (or the `FROM` line and the local tag for opencode),
run `ai-stack.sh up`. For OmniRoute keep a backup of the data dir first; for
OpenHands check the settings schema note in `start-stack.sh`.
