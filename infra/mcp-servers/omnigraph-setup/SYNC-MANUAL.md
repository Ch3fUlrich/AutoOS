# Omnigraph sync — operator manual

Everything you need to run, schedule, edit, and trust the local↔central memory sync.
Written 2026-07-17, after the sync was rebuilt (see "How it works" for why it is shaped
this way).

**Default cadence: every 5 minutes.** Change it in one place — see [Scheduling](#scheduling).

---

## TL;DR — set it up in one command

`setup-sync` derives what it can, writes `.env`, **proves it with a dry run, and only then**
schedules anything. Run it from `infra/mcp-servers/omnigraph-setup/`:

```powershell
# Windows (pwsh 7)                     # Linux / macOS / WSL
.\setup-sync.ps1                       # ./setup-sync.sh
```

First time on a new machine you must supply central's bearer once — it is the one value
that cannot be derived (see [Configuration](#configuration--omnigraph-setupenv)):

```powershell
.\setup-sync.ps1 -CentralUrl https://omnigraph.example.org -CentralToken <bearer>
```

| You want | Windows | Linux/macOS |
|---|---|---|
| Set up + schedule every 5 min | `.\setup-sync.ps1` | `./setup-sync.sh` |
| A different cadence | `.\setup-sync.ps1 -IntervalMinutes 15` | `./setup-sync.sh --interval 15` |
| Config + dry run, no schedule | `.\setup-sync.ps1 -NoSchedule` | `./setup-sync.sh --no-schedule` |
| See what it resolved, change nothing | `.\setup-sync.ps1 -Show` | `./setup-sync.sh --show` |
| Stop syncing | `.\setup-sync.ps1 -Unregister` | `systemctl --user disable --now omnigraph-sync.timer` |
| One sync right now | `.\sync-windows.ps1` | `./omnigraph-sync.sh` |
| Look, don't touch | `.\sync-windows.ps1 -DryRun` | `DRY_RUN=1 ./omnigraph-sync.sh` |

```bash
# check what docker actually has (never trust a doc over this)
python3 ../scripts/_omni_env.py       # -> network=… bind=…/volume=…
```

Exit code is the truth: **0 = synced**, non-zero = something failed and said so.

**What it derives for you**, so the values cannot drift apart:

| Value | Where it comes from |
|---|---|
| `LOCAL_TOKEN` | `OMNIGRAPH_TOKEN` in `infra/mcp-servers/.env.shared` — the token the local server was started with |
| `DOCKER_NET` | `docker inspect omnigraph-server` — the **live** network, not a guess |
| `LOCAL_URL` / `LOCAL_URL_CONTAINER` | the host-side and container-side views of the local server |
| `DEVICE` | the hostname |
| `CENTRAL_URL` / `CENTRAL_TOKEN` | **you**, once — then remembered |

Re-running is safe: an existing value always beats a derived one, and nothing ever writes an
empty over a non-empty. A pre-flight checks both servers answer `200` **before** your working
`.env` is touched, so a typo'd token cannot cost you a working config.

---

## Scheduling (every 5 minutes)

`setup-sync` does this for you — the sections below are what it sets up, for when you want
to inspect or hand-edit it.

### Windows — Scheduled Task

`.\setup-sync.ps1` registers a task named **`Omnigraph Sync`** that runs
`sync-windows.ps1` every 5 minutes.

```powershell
Get-ScheduledTask 'Omnigraph Sync' | Get-ScheduledTaskInfo   # last run, last result, next run
Start-ScheduledTask 'Omnigraph Sync'                         # force one now
.\setup-sync.ps1 -Unregister                                 # remove
```

- Runs **as you, only while you are logged on**. That is deliberate: running logged-off means
  storing your password, and a laptop that syncs while nobody is at it buys little here.
- `-MultipleInstances IgnoreNew` — if a run overruns 5 minutes the next is skipped, not stacked.
- `-StartWhenAvailable` — a missed run (laptop asleep) fires when you come back.
- Needs **docker running**; if Docker Desktop is down the run fails loudly and the next one
  picks up — nothing is lost.
- `LastTaskResult` `267011` means "has not run yet", not an error. `0` is success.

**Change the cadence:** `.\setup-sync.ps1 -IntervalMinutes 15` (it replaces the task), or
Task Scheduler GUI → `Omnigraph Sync` → Triggers → Repeat every.

> Hand-rolling the trigger? Do **not** pass `-RepetitionDuration ([TimeSpan]::MaxValue)` for
> "forever": it serialises to `P99999999DT23H59M59S` and Task Scheduler rejects the XML.
> **Omit `-RepetitionDuration`** — that is what means indefinitely.

### Linux — systemd timer

`./setup-sync.sh` writes `~/.config/systemd/user/omnigraph-sync.{service,timer}` and enables
the timer (falling back to a `cron` line if there is no systemd user session).

```bash
systemctl --user list-timers omnigraph-sync.timer
journalctl --user -u omnigraph-sync.service -n 50
systemctl --user disable --now omnigraph-sync.timer      # stop
loginctl enable-linger $USER                             # keep running after logout
```

**Change the cadence:** `./setup-sync.sh --interval 15`, or edit `OnUnitActiveSec=` in the
`.timer` then `systemctl --user daemon-reload && systemctl --user restart omnigraph-sync.timer`.

The tracked `omnigraph-sync.{service,timer}` in this directory are the **system-wide**
variant (`/etc/systemd/system/`) for a server host; `setup-sync.sh` installs per-user copies
with the real paths baked in.

> **Both scripts are the same logic.** `omnigraph-sync.sh` (Linux/macOS) and
> `sync-windows.ps1` (Docker Desktop) drive the same two Python helpers
> (`omnigraph_jsonl.py`, `pull_graph.py`): delta push straight to central `main`, no device
> branch, purge-then-load pull, real exit codes. Verified 2026-07-17 on the Windows box:
> `rc=0`, all 5 graphs clean and identical on both sides, zero delta on a repeat run.
>
> On Linux the defaults (`DOCKER_NET=host`, `LOCAL_URL_CONTAINER=$LOCAL_URL`) are right. On
> Docker Desktop set `DOCKER_NET=mcp-server_mcp-net` and
> `LOCAL_URL_CONTAINER=http://omnigraph-server:8080` — or just let `setup-sync` detect it.

> **Both scripts are now the same logic.** `omnigraph-sync.sh` (Linux/macOS) and
> `sync-windows.ps1` (Docker Desktop) both drive the same two Python helpers
> (`omnigraph_jsonl.py`, `pull_graph.py`): delta push straight to central `main`, no device
> branch, purge-then-load pull, real exit codes. Verified 2026-07-17 on the Windows box via
> Git Bash (`rc=0`, all 5 graphs clean and identical both sides).
>
> On Linux the defaults (`DOCKER_NET=host`, `LOCAL_URL_CONTAINER=$LOCAL_URL`) are right. On
> Docker Desktop set `DOCKER_NET=mcp-server_mcp-net` and
> `LOCAL_URL_CONTAINER=http://omnigraph-server:8080` — or just use the `.ps1`.

---

## Configuration — `omnigraph-setup/.env`

| Var | Meaning |
|---|---|
| `CENTRAL_URL` | `https://omnigraph.example.org` |
| `CENTRAL_TOKEN` | central's bearer — **different from the local one** |
| `LOCAL_TOKEN` | the local server's bearer (same as `.env.shared`'s `OMNIGRAPH_TOKEN`) |
| `LOCAL_URL` | local API **as seen from this host** (`http://127.0.0.1:8080`) |
| `LOCAL_URL_CONTAINER` | local API **as seen from inside the CLI container** (`http://omnigraph-server:8080`) |
| `GRAPHS` | optional: `"a,b"` to sync a subset. Unset = every graph central exposes |
| `GRAPH` | legacy single-graph var. Ignored unless set to something other than `memory` |
| `DEVICE` | hostname; only used to name a leftover branch to sweep |
| `VIEWER_URL` | optional: `http://coding.example.internal:8090`. Attributes each synced graph to **this device** in the viewer's Sync log (see below). Unset = no attribution; the sync is unaffected |
| `DRY_RUN` / `KEEP_DEVICE_BRANCH` | same as the `-DryRun` / `-KeepDeviceBranch` switches |

### Sync attribution — why it goes by source IP

The viewer's **Sync log** shows which device pushed each commit. That answer is not in the
data: a commit record carries only `graph_commit_id / created_at / actor_id / manifest_* /
parent ids` — **no client address** — and the server logs no request IPs either.

`actor_id` looks like the answer and is not. The server resolves it from the **bearer
token** (`OMNIGRAPH_SERVER_BEARER_TOKENS_JSON` is an actor→token map), and every client
shares one token, so it reads `default` for all of them. The CLI's `--as <ACTOR>` flag does
**not** help — its own help says *"no effect on remote writes (the server resolves the actor
from the bearer token)"*, and that was confirmed against a live server. Making it vary would
mean a bearer token per device, i.e. a secret to distribute to and rotate on every machine.

So the sync `POST`s `\<VIEWER_URL\>/api/sync-ping?graph=<g>` after each graph, and the viewer
records what it observes on the connection: the **source IP**. A commit is attributed to the
first ping that follows it within `PING_WINDOW_SEC` (default 900); anything older stays blank
rather than guessing — a wrong device name is worse than none. The ping is best-effort and
never fails a sync.

**Names are detected, not configured.** Reverse DNS answers this on any LAN whose router
registers its DHCP clients — verified here: `192.0.2.20 → coding.example.internal`, `192.0.2.10 → ollama.example.internal`.
The one address that cannot resolve is the docker bridge gateway, which is exactly what a
sync running **on the viewer's own host** arrives as; the viewer reads that gateway from its
own routing table (not a hardcoded `172.x`, which changes with the compose project) and looks
up the host instead. Lookups are cached, because `gethostbyaddr` ignores socket timeouts and
an unreachable resolver would otherwise stall a worker on every ping.

`OMNIGRAPH_DEVICE_MAP` (`ip=name,…` on the viewer) exists only as an **override** for a host
the LAN DNS cannot name. Normally leave it empty.

> Point `VIEWER_URL` at the viewer **directly** (`http://coding.example.internal:8090`). Through a reverse
> proxy every sync would arrive with the proxy's address and all devices would look alike.

**Those two local URLs are not interchangeable.** The script talks to the server both
directly (from the host) and through a throwaway CLI container (on the docker network).
`127.0.0.1` inside a container is the *container* — using it for the container-side call
gives `Connection refused` at exactly the wrong moment.

---

## `OMNIGRAPH_TOKEN` and `OMNIGRAPH_NET` (agent memory, not sync)

**Neither affects the sync** — the sync reads everything from `omnigraph-setup/.env`. They
affect the **MCP bridge**: a repo's tracked `.mcp.json` references `${OMNIGRAPH_TOKEN}` and
`${OMNIGRAPH_NET}` rather than literals, because a tracked file must never hold a bearer and
the network name differs per machine. Both fail **silently** when wrong — the bridge starts
fine, and the agent simply has no memory.

### What `OMNIGRAPH_NET` is

**It is the name of the Docker network that the `omnigraph-server` container is attached
to** — a value from `docker network ls`, not a URL, host, port, or interface.

Why it exists at all: the bridge is not a long-running service. Each agent session starts a
throwaway container (`docker run -i --rm --network ${OMNIGRAPH_NET} … omnigraph-mcp`) that
talks to `http://omnigraph-server:8080`. That hostname is **Docker's internal DNS alias**,
and Docker only resolves it for containers **on the same network**. Join the wrong network
and the name does not resolve.

Docker composes the name as `<compose-project>_<network>`, which is why it differs per host:

| Host | Compose project | `OMNIGRAPH_NET` |
|---|---|---|
| local client stack (`docker-compose.server.yml` here) | `mcp-server` | `mcp-server_mcp-net` |
| central (`coding.example.internal`, from the Server repo) | `mcp-servers` | `mcp-servers_default` |

Never copy it from a doc — **ask docker**, because only the running container knows:

```bash
docker inspect omnigraph-server --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
python3 ../scripts/_omni_env.py     # same thing, plus the MinIO store and its type
```

Proof of what a wrong value does — the name resolves only from inside the network:

```console
$ docker run --rm --network mcp-server_mcp-net curlimages/curl -fsS http://omnigraph-server:8080/healthz
{"status":"ok","version":"0.8.1","internal_schema_version":4}
$ docker run --rm --network bridge curlimages/curl -fsS http://omnigraph-server:8080/healthz
curl: (6) Could not resolve host: omnigraph-server
```

> **The trap on this machine.** `.mcp.json` falls back to `mcp-servers_default` when
> `OMNIGRAPH_NET` is unset. That network **exists here** (left over from the central-stack
> naming) but is **empty** — so `docker run` succeeds, nothing errors, and the bridge just
> cannot find the server. An error like "network not found" would be kinder than what you
> actually get, which is silence. Set the variable explicitly.

### Setting both — automated

```powershell
.\setup-agent-memory.ps1 -Check     # diagnose, change nothing   (./setup-agent-memory.sh --check)
.\setup-agent-memory.ps1            # fix
```

That script is the automated form of this whole section: it sets both variables (reading the
token from `.env.shared` and the network from `docker inspect`, so neither is typed or
guessed), builds `omnigraph-mcp:latest`, **removes any user-scope `omnigraph` override**,
audits every repo's `.mcp.json`, and verifies by driving the real bridge. `--check` exits
non-zero when something is wrong.

> **Check for a user-scope override before believing any "empty graph".** A same-named
> `omnigraph` in `~/.claude.json` silently wins over a repo's `.mcp.json`. On 2026-07-17 one
> pinned to `graph_id: memory` made every repo read `memory`; an agent saw its 2 Preferences,
> concluded `sibling-analysis-repo` (135 nodes, intact) was **wiped**, and started rebuilding it.
> `0 rows except 2 Preferences` **is** the `memory` graph — not a wipe.
> ```bash
> python -c "import json,pathlib;print(sorted((json.loads((pathlib.Path.home()/'.claude.json').read_text()).get('mcpServers') or {})))"
> ```
> There must be **no** `omnigraph` in that list.

### Setting both — by hand

The values are `OMNIGRAPH_TOKEN` from `infra/mcp-servers/.env.shared`, and the detected
network. `setup-sync` prints the exact lines for your machine at the end of a successful run.

```powershell
# Windows — persists for future sessions; restart the agent afterwards
[Environment]::SetEnvironmentVariable('OMNIGRAPH_TOKEN', '<token from .env.shared>', 'User')
[Environment]::SetEnvironmentVariable('OMNIGRAPH_NET',   'mcp-server_mcp-net', 'User')
```

```bash
# Linux/macOS — in ~/.bashrc / ~/.zshrc, or a systemd user env
export OMNIGRAPH_TOKEN=<token from .env.shared>
export OMNIGRAPH_NET=mcp-server_mcp-net
```

Verify in a **new** shell, then restart the agent:

```powershell
[Environment]::GetEnvironmentVariable('OMNIGRAPH_TOKEN','User')   # non-empty
docker network inspect $env:OMNIGRAPH_NET --format '{{range .Containers}}{{.Name}} {{end}}'
#   ^ MUST list omnigraph-server. If it prints nothing, you have the wrong network.
```

Then in a fresh agent session `graphs_list` should return 5 graphs and `schema_get` should
show 11 edge types. If you would rather not put the bearer in your OS environment, the
alternative is a user-scope `.mcp.json` under `~/.claude.json` (untracked) holding the
literal token — never in the repo's `.mcp.json`.

> `OMNIGRAPH_BASE_URL` is **not** in this list on purpose. This repo's `.mcp.json` passes it
> explicitly (`-e OMNIGRAPH_BASE_URL=http://omnigraph-server:8080`), so setting it in your
> environment has no effect on the bridge. And do not confuse `OMNIGRAPH_GRAPH_ID` (the
> **bridge's** graph) with `OMNIGRAPH_GRAPH` (the **viewer's**).

---

## How it works (and why it is shaped this way)

Per graph, every run:

1. **Back up** local `main` → `omnigraph-setup/backups/local-<graph>-<ts>.jsonl`.
2. **Verify** local for duplicates.
3. **Push the delta** to central `main` — `omnigraph_jsonl.py pushset` emits every node
   whose payload differs from central's (ignoring `id`/`embedding`) plus **only the edges
   central lacks**. If the delta is empty it pushes nothing at all (the common case).
4. **Verify** central.
5. **Pull** via `pull_graph.py`: purge local, then merge-load central's deduped export into
   the now-empty graph. Restores from the backup if the load fails.
6. **Verify** local, sweep any stale `device/<host>` branch.

Four design choices, each bought with an incident:

- **Delta push, never the whole export.** Edges have no `@key`, so merge-loading an edge
  central already has *appends a duplicate*. Pushing everything is what gave central 2×
  edges on every project graph (2026-07-17). Nodes are `@key(slug)` and upsert safely.
- **Skip identical nodes.** Pushing a node that matches central still bumps that table's
  version, and the merge then dies with `Concurrent modification: table version N already
  exists for node:<Type>`.
- **No device branch.** On v0.8.1 `branch create` can hit a Lance internal error
  (`Clone operation should not enter build_manifest`) and `branch merge` the concurrent-
  modification error above. The branch existed for "review before merge", which buys
  nothing on an unattended 5-minute timer. Safety comes from the delta + backups + verify.
- **Purge-then-load for the pull, not `overwrite`.** `load --mode overwrite` into a
  *populated* graph trips a Lance bug (`stage_create_btree_index … all columns in a record
  batch must have the same length`) — and can *land anyway while exiting 1*, so you cannot
  even trust its failure. Loading into an **empty** graph is the one reliable write path.

**The rule behind all of it: verify against the live system, never trust a component's own
report.** Every failure here was something reporting success while doing the opposite. After
a sync, check `edges` vs `distinct edges` — not the exit code alone.

---

## Verifying / troubleshooting

```bash
cd infra/mcp-servers; set -a; . ./.env.shared; . ./omnigraph-setup/.env; set +a

# duplicates on either side (the check that matters)
curl -s -X POST "http://127.0.0.1:8080/graphs/<g>/export" -H "Authorization: Bearer $OMNIGRAPH_TOKEN" \
  -H 'content-type: application/json' -d '{}' | python3 omnigraph-setup/omnigraph_jsonl.py verify
curl -s -X POST "$CENTRAL_URL/graphs/<g>/export" -H "Authorization: Bearer $CENTRAL_TOKEN" \
  -H 'content-type: application/json' -d '{}' | python3 omnigraph-setup/omnigraph_jsonl.py verify
```

| Symptom | What it means / do |
|---|---|
| `edges` > `distinct edges` | duplicates. **Do not sync into it.** Repair: export → `omnigraph_jsonl.py dedup` → compare against `cluster/seed/<g>.jsonl` → delete every node (edges cascade) → merge-load the deduped export → re-verify. Smallest graph first. |
| `all columns in a record batch must have the same length` | the Lance bug. The load failed *staged*, so the graph survives — but it can leave Lance HEAD ahead of the manifest, after which **every** load to that graph fails while reads still work. **`docker restart omnigraph-server`** recovers it; no data lost. |
| `Concurrent modification: table version N already exists` | something pushed an unchanged table. Should not happen now — if it does, `pushset` let an identical node through. |
| `Connection refused` from the CLI container | `LOCAL_URL_CONTAINER` is wrong (probably `127.0.0.1`). `pull_graph.py` pre-flights this and refuses to purge. |
| Sync exits non-zero | read the message — it names the graph and the failing command. Your backup is in `omnigraph-setup/backups/`. Central is only ever touched by a delta, so a failed run leaves it consistent. |
| A `device/<host>` branch lingers | it blocks `schema apply`. `DELETE /graphs/<g>/branches/device%2F<host>`. The sync sweeps its own. |

## Text encoding — why the helpers force UTF-8

Omnigraph exports are UTF-8 (JSON always is). Windows is not, and every hand-off in this
directory crosses that gap. Do not "simplify" these away:

| Where | Default | What it did |
|---|---|---|
| `omnigraph_jsonl.py` stdin | locale (`cp1252`) | read local as cp1252 while central was read as UTF-8 → **the same text compared unequal**, so every node with an em dash or arrow was re-pushed on *every* run (52 of sibling-analysis-repo's 135). Fixed by `_force_utf8_stdio()`. |
| `.ps1` native-command **output** | `[Console]::OutputEncoding` (OEM, cp850) | decodes docker's UTF-8 export as cp850, then writes it back as UTF-8 — **lossy, real corruption** |
| `.ps1` native-command **input** | `$OutputEncoding` (ASCII on PS 5.1) | `→` piped into docker/python becomes `?` |
| `Get-Content` / `Set-Content` | host default (ANSI on 5.1) | silent mangling |

The tell for the comparison bug: the "changed" node count equals the number of export lines
containing non-ASCII bytes, and it never drops after a successful push.

```bash
# how many records even could be affected
python3 -c "import sys;print(sum(1 for r in open(sys.argv[1],'rb') if any(b>127 for b in r)))" backups/local-<graph>-<ts>.jsonl
```

This is fixed **inside** the scripts rather than by exporting `PYTHONIOENCODING` at the call
site, because they are run three ways — by hand, by systemd, and by the Task Scheduler — and
only one of those would have carried the variable. `test_omnigraph_jsonl.py` pins it with
raw-bytes subprocess tests; note that `subprocess.run(text=True)` **cannot** catch this class
of bug, as it encodes and decodes with the same parent locale so any mismatch cancels out.

## Known limitations

- **Merged `Decision`s are invisible to semantic search.** `load --mode merge` does not
  compute the `@embed("rationale")` vector and **erases** it if the record omits one, and an
  unembedded `Decision` is *dropped* from `nearest()` — not ranked low. On v0.8.1 there is
  no working re-embed on a populated graph, so `populate-embeddings.py` only works against a
  fresh one. The nodes stay findable by traversal and full-text.
- **The Linux `omnigraph-sync.sh` is unfixed** (see the warning above).
- **Conflict resolution is last-writer-wins per node**, decided by payload difference. There
  are no timestamps in the schema, so a genuine two-sided edit of the same node cannot be
  merged intelligently — local wins on the push, then the pull makes local match central.
