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

#### OAuth logins and the public URL

- **Google logins (Antigravity, Agy) use a loopback callback by design.** The
  dashboard asks Google to redirect to `http://127.0.0.1:20128/callback`, and
  Google's consent for the bundled client only completes when that address is
  reachable from the browser that approves it. Do the login from a browser **on
  the host**, or forward the port and browse through it:
  `ssh -L 20128:127.0.0.1:20128 <host>`, then `http://127.0.0.1:20128`. If the
  redirect fails anyway, copy the `http://127.0.0.1:20128/callback?...` address
  from the failed page's address bar and paste it into the dialog.
- **`AUTOOS_OMNIROUTE_PUBLIC_URL`** (the docker stack's `stack.env`; unset by
  default, and an empty value is the same as unset) is handed to the gateway as
  `NEXT_PUBLIC_BASE_URL` and `OMNIROUTE_PUBLIC_BASE_URL`. Use the exact origin
  you browse the dashboard from, e.g. `https://omniroute.<domain>`. Read from
  the image's source (3.8.50), it changes **server-side** behaviour only:
  - the origin the gateway accepts for browser writes to the dashboard. With a
    public URL set that origin is accepted and forwarded headers no longer
    derive one, so browsing from a different name can be refused with
    `INVALID_ORIGIN` (direct loopback and LAN-IP access keeps working);
  - the host of links the server generates (image URLs and the like);
  - the `redirect_uri` of an Antigravity/Agy login becomes `<url>/callback`
    **only if you also set your own Google OAuth client** (`ANTIGRAVITY_OAUTH_CLIENT_ID`
    and `ANTIGRAVITY_OAUTH_CLIENT_SECRET`, different from the bundled ones).
    With the bundled client the redirect stays on loopback: the variable alone
    does **not** make Google logins work from a remote browser.
- **What it does not change.** The dashboard's browser code is compiled into the
  image and cannot read the container's environment, so the OAuth dialog still
  builds its redirect from the address you browse from. Logins that need no
  browser redirect (the Qoder PAT, API keys) do not depend on it.
- **Two more things the stack does with it.** While it is set, `compose.yml`
  also pins `BASE_URL: http://localhost:20128`: without that the gateway's calls
  to itself (A2A skills, MCP tools, cloud sync) would take
  `NEXT_PUBLIC_BASE_URL` as their base and leave through the proxy. And the
  gateway **exits at startup** on a value that is not an `http(s)` URL, which
  `restart: unless-stopped` turns into a crash loop, so `ai-stack.sh up` and
  `migrate` refuse one before compose runs; `verify` prints the value as the
  app normalizes it (trailing slashes dropped) or `skip - ... is not set`.

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
  profiles it owns that the spec no longer lists, and removes the
  lowest-ranked of them to make room for a higher-ranked tier. It owns only
  what it recorded pushing (`~/.openhands/profiles/.autoos-pushed.json`)
  plus the spec's `retired_ids` - a profile you made is never deleted,
  whatever its name (`omniroute-personal` included), nor is the active one;
  and nothing is deleted when the app does not say which profile is active.
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
| `opencode.<domain>` | `<coding-host>:4096` | `/api/event` (SSE, no buffering), `/api/pty/*` (websocket) | nothing | replace `Authorization` with the app's Basic credentials after the SSO gate, so the browser shows one login (`header_up Authorization "Basic <base64 opencode:password>"`; the value exists only in the proxy's live config, never in git) |
| `openhands.<domain>` | `<coding-host>:3000` | `/socket.io/*`; plus `/sbx/<port>/*` → `<coding-host>:<port>` (sandbox API + `/sockets/*` websockets) when `AUTOOS_OPENHANDS_SANDBOX_URL` uses that pattern | nothing - the app has no login | `Host`, `X-Forwarded-Proto`; keep `X-Session-API-Key` |

Every service above is listed in the web UI's router card (live up/down,
bind, auth) with its start action: [web-ui.md](web-ui.md).

## Server profile: the AI stack in Docker

On a server, AutoOS runs the three LAN-facing AI services as one hardened
compose stack instead of host processes: the OmniRoute gateway (`:20128`),
`opencode serve` (`:4096`) and OpenHands (`:3000`). The workstation profile
keeps the native installs (the sections above).

Files: [`configuration/docker/ai-stack/`](../configuration/docker/ai-stack/) -
`compose.yml`, `omniroute.Dockerfile`, `opencode.Dockerfile`, `stack.env.example` and `ai-stack.sh`,
the one entry point (`init`, `up`, `down`, `status`, `is-active`, `migrate`,
`rollback`, `verify`; `--dry-run` with any of them). Catalog id: `ai-stack-docker`,
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
| `omniroute` | local `autoos/omniroute:3.8.50-autoos1` (FROM `diegosouzapw/omniroute:3.8.50@sha256:085c…`) | `autoos-omniroute` | host uid:gid | `${AUTOOS_STACK_BIND}:20128` | `~/.local/share/autoos/ai-stack/omniroute` -> `/app/data`; `…/ai-stack/qoder-home` -> `/home/qoder` (`HOME`) |
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
| image `@sha256:` digest (the `FROM` of the two local layers) | all | a tag can move; the gateway holds encrypted credentials and a changed schema must be a deliberate bump |
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
(`host.docker.internal`), and the proxy - on another host - uses the LAN
address. Narrowing the bind would break one of them; the LAN exposure is
instead limited by the `DOCKER-USER` firewall rule to the proxy.

A comment in `stack.env` is not a control, so `ai-stack.sh up` and `migrate`
**refuse** to (re)create a container on a non-loopback address unless one of
these holds, and say which is missing:

| Condition | Checked as | Why it is enough |
|---|---|---|
| the firewall is in place | `systemctl is-active coding-agents-fw.service` (the oneshot system unit that loads the `DOCKER-USER` rule) | its `DOCKER-USER` rule drops LAN connections to the published ports except from the proxy |
| the operator accepted the exposure | `AUTOOS_STACK_ALLOW_LAN=1` in `stack.env` | an explicit, persistent decision in the operator's own file (a host with another firewall, or none needed) |
| the stack is host-only | `AUTOOS_STACK_BIND=127.0.0.1` (any `127.*`, `::1`, `localhost`) | nothing is published to the LAN; the proxy then cannot reach it either |

The guard runs before **every** `docker compose up -d` - `up`, `migrate` and
rollback's recovery - because that call creates containers or recreates running
ones (a changed bind, a rebuilt image). It checks the **effective** address:
an exported `AUTOOS_STACK_BIND` wins over `stack.env`, exactly as it does in
compose's interpolation, and `ai-stack.sh` passes that same value to compose,
so the checked and the published address cannot differ. Only `stack.env` can
accept the exposure (`AUTOOS_STACK_ALLOW_LAN`), never an inherited variable.
Containers docker already restarted keep running when the guard refuses.

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
while the native gateway still answers, and refuses to start the move when it
cannot (no CLI, native gateway down): without the key `apply.sh` could never
manage the container gateway. `apply.sh` hands it to the `omniroute` CLI only,
as `OMNIROUTE_API_KEY` in that command's environment - never exported to the
script, so `curl`, `python3` and the probe do not inherit it. The CLI inside
the image cannot replace this: it fails to start (`tsx` is not in the image).

`init` never writes a value that holds a line break (`\n`, `\r`): an env file
is one `KEY=value` per line and docker's format has no escape for it, so the
rest of the value would become a key of its own. Such a key is skipped with a
warning that names the key, never the value. The data directory, its
`omniroute/`, `opencode-home/` and `qoder-home/` are mode `700`.

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

### OmniRoute with qodercli

The gateway's Qoder provider signs in with a personal access token (PAT), and
OmniRoute drives that through the `qodercli` binary inside its own container.
The upstream image has none, so the login failed with `spawn qodercli ENOENT`.
`omniroute.Dockerfile` adds exactly that binary on the digest-pinned upstream
image (`npm install -g @qoder-ai/qodercli@<version>`, the version the host
runs; npm fetches it at build, no binary is kept in git; image 4.14 GB ->
4.24 GB) and compose builds it as `autoos/omniroute:<upstream>-autoos<n>`.

Two measurements decide the rest of the service:

- **`qodercli` needs a writable `HOME`, even for `--version`.** It creates
  `$HOME/.qoder` on start and crashes (`ENOENT ... mkdir '/home/node/.qoder'`)
  when it cannot; the root filesystem is read-only. OmniRoute runs
  `qodercli --version` itself to find out whether the CLI is usable, so the
  gateway needs a `HOME` it can write. `compose.yml` sets `HOME=/home/qoder` and
  mounts `~/.local/share/autoos/ai-stack/qoder-home` there (a tmpfs would give
  the gateway a new machine id on every restart). `ai-stack.sh init` creates it
  (`0700`, the operator's) - and so does `up`, because on a host initialised
  before this mount existed docker would create the missing source as root.
- **The PAT login itself is not stored there.** OmniRoute passes
  `--config-dir $DATA_DIR/qoder-cli`, so the CLI's auth, cache, logs and
  sessions land in the gateway data directory, which is backed up and migrated
  with everything else. `qoder-home` holds only what the CLI writes to `HOME`
  regardless (`~/.qoder/entry`, a git ignore file, and the logs of a plain
  `qodercli --version`).

`CLI_QODER_BIN=/usr/local/bin/qodercli` names the binary by its absolute path.
The remaining hardening (`read_only`, `cap_drop`, `no-new-privileges`, the
tmpfs, the limits) is unchanged. OmniRoute resolves the config paths of the
CLI tools it manages under `HOME`, so any such config it writes now lands in
that mount instead of failing on the read-only root; nothing reaches the image.
`ai-stack.sh verify` checks `qodercli --version` in the running gateway
(`omniroute has qodercli`).

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

0. Refuse - before anything stops - when the stack already owns the services
   (a second run would copy the older host state over the containers' newer
   one; use `rollback`), when the publish address is on the LAN without the
   firewall (above), or when no manage key exists and none can be created.
1. `init`, then build/pull the images.
2. Create the manage key while the native gateway still answers.
3. Stop the `autoos-omniroute` unit (a hand-started gateway: `omniroute stop`). The gateway is down from here; the SQLite files are quiescent.
4. Back up `~/.omniroute` to `~/.local/share/autoos/ai-stack/backups/omniroute-<ts>.tar.gz` (0600; a failed `tar` leaves no partial archive).
5. **Copy** `~/.omniroute` (DB + `.env` with `STORAGE_ENCRYPTION_KEY` - without
   it the stored provider credentials are unreadable) into the data dir. The
   original stays untouched.
6. Start the container; it must answer `/api/health` **and** refuse a keyless
   `/v1` call with 401.
7. The same for `autoos-opencode`: stop the unit, start the container, prove
   it answers.
8. Remove the `docker run` `openhands-app` (state stays in `~/.openhands`;
   running sandboxes keep running) and start the compose one through
   `start-stack.sh openhands`, which still repairs the settings and pushes the
   tier profiles; it must answer on `:3000`.
9. Only when **all three** answered: write the ownership marker
   `~/.config/autoos/ai-stack/stack.active`, then `register-autostart.sh
   --unregister --only autoos-omniroute,autoos-opencode`. Marker first: no
   moment exists in which the units are gone but nothing owns the services
   (rollback needs the marker). If either step fails, the abort below removes
   the marker again.

A native unit has to *stop* before its container can take the port; it is
*unregistered* only in step 9. Any failure from step 3 on hands **every**
service moved so far back: the containers are removed
(`docker compose rm -s -f`; their data stays), the units start again
(re-registered if needed), and a replaced `openhands-app` is recreated by
`start-stack.sh openhands`. No marker remains.

**Who owns the services** is that marker, not a container: `ai-stack.sh
is-active` is true only while it exists, so a container a failed migrate left
behind never switches `register-autostart.sh`, `Start-AutoOSStack.sh`,
`start-stack.sh`, `healthcheck.sh` or `apply.sh` to docker mode. With the
marker they skip the native gateway and opencode units and resume/manage the
containers - a stopped-but-owned stack included (`up` resumes it rather than a
stale native copy starting). On a fresh server with nothing native to move,
the first `up` that gets the gateway and opencode answering from their
containers writes the marker instead; `up` never starts a service next to a
registered native unit.

#### Rollback

`ai-stack.sh rollback --yes` (run it from the checkout the units should point
at):

1. Refuse unless the marker says the stack owns the services.
2. Back up `~/.omniroute` (0600 `tar.gz`) while the stack still runs - a
   failed backup changes nothing.
3. `docker compose down` - containers removed, bind-mounted data kept.
4. **Replace** `~/.omniroute` with the container's data (the newest state:
   keys, combos, usage): copied into a fresh sibling directory, then swapped
   in, never copied over the old one - an overlay would keep a stale
   `storage.sqlite-wal`/`-shm` that SQLite replays onto the restored database.
   The previous directory stays as `~/.omniroute.autoos-backup-<ts>`.
5. Remove the marker, then `register-autostart.sh --only
   autoos-omniroute,autoos-opencode`.
6. `start-stack.sh openhands` (the `docker run` container, as before).

### Verifying the stack

`ai-stack.sh verify` is the checklist to run after `migrate --yes` (or any
`up`), as one repeatable command. It is **read-only**: docker is only asked
`inspect`, `exec <opencode> test -d` and `exec <omniroute> qodercli --version`
(the one command that writes anything: qodercli leaves its usual log files in
its `HOME`, the `qoder-home` mount), curl only probes the loopback ports (and
the public URLs you list), nothing is started, stopped, restarted or written
otherwise, and the gateway key is never printed (`--dry-run` changes nothing
either, there is nothing to change). Each check prints one line - `ok`,
`FAIL - <reason>`, or `skip - <why>` when its input is not configured - and the
command ends with `verify: N ok, M failed, K skipped`. **Exit status: 0 only
when M is 0**, 1 otherwise.

| # | Check | Passes when | Skipped when |
|---|---|---|---|
| 1 | containers | every service compose starts (those without `profiles`, plus the ones `COMPOSE_PROFILES` names) is `running`, and `healthy` where docker reports a health status | - |
| 2 | keyless refusal | `GET /v1/models` on `:20128` and `GET /api/session` on `:4096`, without an `Authorization` header, answer 401 | that service is not enabled |
| 3 | keyed combos | `POST /v1/chat/completions` with a one-word prompt and `max_tokens` 16 answers 200 for each combo | `AUTOOS_OMNIROUTE_KEY` is unset |
| 4 | code dir | `AUTOOS_CODE_DIR` (environment, then `stack.env`) is a directory inside the opencode container, and the OpenHands container's `SANDBOX_VOLUMES` has a `<dir>:<dir>` entry | that service is not enabled |
| 5 | gateway CLI | `docker exec autoos-omniroute qodercli --version` prints a version, run as the gateway runs it (same user and `HOME`): the image has the qodercli layer and its `HOME` is writable | the omniroute service is not enabled, or its container is not running (check 1 already FAILs that) |
| 6 | public URLs | each URL answers 302 (the auth proxy's redirect) without credentials | `AUTOOS_VERIFY_PUBLIC_URLS` is unset |
| 7 | gateway public URL | `AUTOOS_OMNIROUTE_PUBLIC_URL` (environment, then `stack.env`) is an `http(s)` URL without credentials or blanks; printed as the app normalizes it, **not requested** (it may be a plain LAN address; reachability is check 6's job) | it is unset or empty |
| 8 | healthcheck | - | always: `configuration/healthcheck.sh` appends to `logs/healthcheck-<date>.log` on every run and exits 0 whatever it finds, so it is neither read-only nor a verdict; its docker probes are checks 1 and 2 |

The two variables `verify` reads besides the ones above:

- `AUTOOS_VERIFY_COMBOS` - space separated combo names for check 3
  (default `t2-worker-free-only t3-driver-free-only t2-worker-clean`).
- `AUTOOS_VERIFY_PUBLIC_URLS` - space separated public URLs for check 6. Keep
  them in your shell environment, not in a file that is committed. A URL with
  credentials in it is refused, and a query string is never echoed.

The gateway key comes from `AUTOOS_OMNIROUTE_KEY` only (no key file is read).
It is handed to curl on stdin (`-H @-`), so it never appears on a command line
that `ps` shows, and no line of the output contains it.

### Bumping an image

Resolve the new digest (`docker buildx imagetools inspect <image>:<tag>`),
change the `image:` line (for opencode and OmniRoute the `FROM` line of their
Dockerfile and the local tag in `compose.yml`, which names the upstream version;
for OmniRoute the pinned qodercli version moves with them), run
`ai-stack.sh up`. For OmniRoute keep a backup of the data dir first; for
OpenHands check the settings schema note in `start-stack.sh`. `up` recreates a
running gateway whose image changed (a short gap on `:20128`).

Each locally built image (`opencode`, `omniroute`: the services with a `build:`
in `compose.yml`) carries the label `org.autoos.<service>.source`: a hash of
its Dockerfile plus the base image digest in its `FROM` line. `up` and
`migrate` rebuild it whenever that no longer matches, so an edited Dockerfile
or a bumped base never keeps serving the old layer under the unchanged local
tag; `--dry-run` says `would build` / `would rebuild`.
