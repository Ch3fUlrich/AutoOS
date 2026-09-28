# Fleet Console — spec (agents, spawn tree, kanban, costs, actions)

SPEC — not approved; no implementation. L2-general lane `fleetspec`. Inputs: `logs/review/fleet-inputs/fleet-r1.out` (OmniRoute architecture), `fleet-r2.out` (AutoOS spawner inventory), `fleet-r3.out` (Conductor / orchestration canvas / A2A / v4 gate), `fleetspec-outline.md` (L2-general decisions; ADDENDUM 2 overrides sections 3, 10, Q1/Q2), `fleetspec-answers.md` (routing-00 answers + operator additions A1–A5, D-042 "Context provenance" and D-043 "Project pages"). The A1 memory-event shape is **agreed with L1-backlog (2026-09-28, `schema: 2`)** — §4.3 states it; the facade spec is `docs/plans/2026-09-28-memory-facade-spec.md` (L1-backlog lane). OmniRoute paths are prefixed `omniroute:`; AutoOS paths are repo-relative `tools/...`. Anything not from those files is marked "(inference)". PUBLIC repo: "the operator's hosts", `<domain>`, "the edge proxy"; no hostname / domain / IP / username / key.

## 0. Summary

The operator needs one phone-readable view over every agent on every host: state per project, who-spawned-whom-and-why, cost, and one-tap actions (answer / steer / stop / resume / approve) — every steer logged so the parent agent sees it. AutoOS records enough lineage to build this but joins nothing (r2 §7). OmniRoute already ships a fleet board — Conductor + an orchestration canvas — and all the cost/auth/UI kit the console needs (r1, r3). Recommendation (ADDENDUM 2): a small AutoOS hub **"fleetd"** that speaks the OmniConductor hub API so existing Conductor/canvas show AutoOS agents with **zero OmniRoute change (P1)**, then a v4-shaped module, staged flag-off in the `autoos/omniroute` image, that adds the pieces the hub protocol cannot carry — spawn tree, kanban, answer/steer, cost-per-agent, provenance, memory feed, and **per-project spaces (D-043)**: a portfolio home plus a project page (Overview · Changelog · Questions · Decisions · Reports · Knowledge · Agents) fed by the router's question/decision files, each repo's changelog and ADRs, and agent-published reports (§4.1/§5.7/§8.10–8.11). Design as upstream module, ship as in-image flag-off; the memory-event envelope is **settled with L1-backlog (`schema: 2`, §4.3)**. Phases P0–P5, each with a measurable acceptance and a data ladder rung. Four open operator questions remain in section 12.

## 1. Goal and non-goals

**Goal** — the operator's 5 needs (outline §1) plus the project space added by D-043:

| # | Need | Where |
|---|---|---|
| G1 | Kanban of tasks + open questions | views 1, 4; P3–P4 |
| G2 | All agents by state, per project, across hosts | board; P1 |
| G3 | Spawn-history tree with the reason for each edge | tree; P1→P3 |
| G4 | Telemetry + cost per agent / project / model | costs; P2 |
| G5 | Actions: answer, approve, deny, open chat, steer, stop, resume — every steer logged for the parent | drawer; P3 |
| G6 | **Project spaces (D-043)** — one home per project: state, progress vs plan, changelog, its questions/decisions, agent reports, its knowledge, its agents | portfolio home 8.10, project page 8.11; P3–P4 |

**Non-goals** (outline §1): Grafana/Alloy is not the UI (may remain a metrics backend); no second agent launcher (the `autoos-agent` spawner stays the only launcher, §5); no replacement for Claude Remote Control chat (deep-link to it); no body-of-knowledge store here — memory is an external graph reached through a typed facade (A1, §4/§7); the only bodies fleetd stores are provenance packs (§4.5) and agent-published reports (§4.1/§9), neither of which is knowledge-graph text.

## 2. What exists (facts, cited)

### 2.1 AutoOS spawner (r2)

CLI `tools/autoos-agent.py` (argparse `:3589-3680`, dispatch `:3681-3694`) and MCP `tools/autoos_agent_mcp.py` (`FastMCP("autoos-agent")`, tools `:668-762`, imports the CLI as a module `:83-102` so `route`/`context` cannot drift).

| surface | what it gives the console |
|---|---|
| `autoos-agent.py ps` (`:3162-3172`, rows `:3060-3118`) | live + exited-≤24h workers: id, state, elapsed, client, model, lane, pid, title, task, cwd, sandbox, started, ended, rc, depth |
| `autoos-agent.py route` (`:1732-1766`) | resolver `route_plan` (full field set r2 §3) — spawn reason + class + legs |
| `autoos-agent.py context` (`:1529-1565`) | `{tokens, cap, pct, model, transcript, source}` — A3 context fill |
| `autoos-agent.py heartbeat` (`:1592-1668`) | `{pause, repos, context, over_cap, exit_code}` |
| MCP `spawn` (`:675-693`) → `{id, state:"working", route, dir}` | the spawn event; `route` here is the persisted minimal plan |
| MCP `respond` (`:621-638`) / `cancel` (`:641-660`) | the answer / stop back-ends |

Full CLI surface (`autoos-agent.py`, argparse `:3589-3680`, dispatch `:3681-3694`) and what each contributes:

| subcommand | console use | citation |
|---|---|---|
| `usage` | provider/combo/lane/model tables from the gateway — the only current cost source | `autoos_usage.py:418-462` |
| `list` | tier × client matrix (headless/gateway/subagents/installed/auth) + depth budget | `autoos-agent.py:1494-1526`, `TIERS :152` |
| `ps` `--all` | the live+recently-exited worker rows (see above) | `:3060-3118,:3121-3143` |
| `run` | the spawn path; stdout plan lines `route:/reviewer:/record-line:/depth:/session-tag:/lean:/sandbox:` | `:3257-3289`; exit codes `:96-118` |
| `route` | resolver `route_plan`, rc 0 route / 5 input_required / 2 bad input | `:1732-1766` |
| `review-status` / `ready` | free-text SHIP/FIX-FIRST verdict + inbox append — the store fleetd formalizes (§4.1 `decisions`) | `:1008-1036,:1135-1185` |

MCP tool surface (`autoos_agent_mcp.py`, registered `:668-762`):

| tool | returns | citation |
|---|---|---|
| `spawn` | `{id, state:"working", route, dir}`; refusals `{error, state:"rejected"}` | `:411,:356-359` |
| `status` | one `_state` dict, or `{runs:[…]}` = 20 newest | `:592-602` |
| `result` | `_state` + `{truncated, text}` tail of `output.log` (`TAIL_CHARS 6000`) | `:605-618,:71` |
| `respond` | writes `answer.json {text, answered}`, returns new state; refuses non-`input_required`/`ended` | `:621-638` |
| `cancel` | writes `exit.json {cancelled:true}` O_EXCL-once, SIGTERM to process group | `:345-353,:641-660` |
| `list_clients` / `list_agents` / `route` / `context` / `heartbeat` / `ps` | the same reads the CLI gives, delegated so they cannot drift | `:139-160,:163-244,:248-283` |

On-disk records the console reads (r2 §2): `logs/workers/<id>.json` (`autoos-agent.py:2963-3009`), `logs/agents/<run id>/` — `job.json` `:401-410`, `output.log`, `exit.json`, `question.json`/`answer.json`/`qa-<n>.json` (`autoos-ask.py:110-143`), `logs/orch-<date>.log` (`:1769-1781`), `logs/routing/track-record.jsonl` (`autoos_track.py:20-32`).

States: the MCP/A2A set `TASK_STATES = (submitted, working, input_required, completed, failed, canceled, rejected)` (`autoos_agent_mcp.py:79-80`), set in `_state()` `:540-589`. The worker set is a different vocabulary `running`/`died`/`exited rc=N` (`:3027-3041`); routing has a third (`ready`/`deferred`/`input_required`, `autoos_resolver.py:1750,:689`). Session-tag header precedent: `SESSION_TAG_HEADER = "x-omniroute-session-id"` (`autoos-agent.py:671`), value from `session_tag(title)` `:674-693`, injected as provider headers (`:1430-1433`) and read into `call_logs.session_tag` gateway-side (r1 §5).

### 2.2 Gaps a console must close (r2 §7)

| Gap | Evidence | Closes at |
|---|---|---|
| Two unlinked run ids (MCP `run_id` ≠ worker `id`; `orch-*.log` carries none) | r2 §3, `:57`; `:1774-1779` | P0 canonical run id |
| No parent/child edge — only numeric `depth` (`:3002`), no parent id/pid/session | r2 §3, `:58` | P0 parent edge |
| No host stamp — `logs/workers` host-wide but not host-stamped | r2 §7, `:84` | P0 host + P1 node agent |
| No cost/tokens per run locally — `track_entry` hard-writes 0/`served_leg:"unknown"` (`:2572-2577`) | r2 §7, `:87` | P2 run-id join |
| `route_plan` not persisted — `job.json` stores `{combo,reason,routing_version}` only | r2 §3, `:59` | P0 persist route_plan |
| No Claude/other sessions recorded | r2 §7, `:90` | P1 node agent |
| Retention inconsistent + partly destructive (workers pruned on read at 7d `:3079`; agents dir never pruned; track-record unbounded) | r2 §7, `:93` | P0/P4 retention policy |
| Verdicts (`SHIP`/`FIX-FIRST`) free-text, outside every JSON artifact | r2 §7, `:94` | P4 decisions store |

### 2.3 OmniRoute (r1) — stack, cost, auth, UI it already gives us

Stack (`omniroute:package.json`, r1 §1): Next.js 16.3.5 / React 19.2.8 (`:344,357-358`), `strict:false` (`:8`); SQLite no ORM (`omniroute:src/lib/db/adapters/driverFactory.ts:206-285`).

Migrations: `NNN_description.sql` auto-discovered, highest 193 today (`omniroute:src/lib/db/migrationRunner.ts:225,230`) — **add a table = 1 migration + 1 `db/` module** (`omniroute:src/lib/db/AGENTS.md`, pattern `src/lib/db/costLedger.ts`); column adds on hot tables also need the boot heal list (`omniroute:src/lib/db/schemaColumns.ts:111-145,168-329`).

Authz: pipeline `src/proxy.ts:23`; internal `/api/<domain>` is MANAGEMENT by default (`omniroute:src/server/authz/classify.ts`), guard with `requireManagementAuth` (`omniroute:src/lib/api/requireManagementAuth.ts:49`). Realtime: event bus (`omniroute:src/lib/events/eventBus.ts:1-16`) feeds a live WS on a separate port (loopback, default `LIVE_WS_PORT`), channels `requests/combo/credentials` (`omniroute:src/server/ws/liveServer.ts:50-54`).

Extension surface: no UI page registry — a page is a route folder + one sidebar item + nav-test updates (`omniroute:src/shared/constants/sidebarVisibility/sections.ts:820-888`). MCP server ~110 tools (`omniroute:open-sse/mcp-server/README.md:3`); names must avoid `RESERVED_MCP_NAMES` (`server.ts:804-815`) — note `pool_status`/`pool_sessions` already taken, so "pool"/"fleet" vocabulary is loaded (`omniroute:open-sse/mcp-server/tools/poolTools.ts`).

UI kit: `@xyflow/react` FlowCanvas wrapper (`omniroute:src/shared/components/flow/FlowCanvas.tsx`), `@dnd-kit` sortable only — **no kanban exists** (`omniroute:package.json:302-304`; r1 §6); Recharts; 68 locales with a new-key CI gate that demands translation in *every* locale (`omniroute:ci.yml:548`).

Cost tables: `call_logs` (per-attempt journal, `id TEXT PK`, session_tag precedent at `133:1`) and `request_cost_ledger` (`182:23-44`, `request_id TEXT`, `amount_usd REAL`) — **no per-agent rollup exists** (r1 §5). The join key between them is stated as an unverified assumption in §5.3, not as a fact from r1.

v4 gate (`omniroute:ROADMAP.md`): from 3.8.55 an external **new-feature** PR is held with the `v4-feature` label rather than merged into the active `release/v3.8.x` line, and re-targeted to the v4 `develop` modular core + SDK when that channel opens (`:42-43` the pause + label, `:89-94` the contributor table "New feature → held with `v4-feature` label → `develop` (v4)"). Fixes, docs, i18n and provider updates keep flowing into v3.8.x (`:42-43`). The v4 target is the modular core + SDK + modules (`:23`) with UI contributions in that one declarative manifest (`:73-75`) — which is why this spec writes the module A-shaped (§3).

### 2.4 Conductor + orchestration canvas + A2A (r3) — what we can reuse

Conductor (`omniroute:src/lib/conductor/`) is a **server-side proxy to an external "OmniConductor hub"** at `CONDUCTOR_HUB_URL` (default loopback:7910) / `CONDUCTOR_HUB_TOKEN` (`omniroute:src/lib/conductor/hubProxy.ts:97-101`); hub is a separate service, **not open source in this checkout** (r1 open, line 146; r3 open). It never lets the browser reach the hub — whitelisted shapes only, parsed with zod `.catch(null)` per field (`hubProxy.ts:56-91`); fail-open `{offline:true}` (`:172,187`). Bridge mirrors hub tasks into a local A2A `TaskManager` over SSE (`omniroute:src/lib/conductor/bridge.ts:1-8,21-37`), booted from instrumentation (`boot.ts:18-31`). No feature flag — env opt-in only.

The orchestration canvas (`omniroute:src/app/(dashboard)/dashboard/orchestration/`) already **merges three sources**: `useOrchestrationSnapshot` polls `/api/v1/agents/tasks`, `/api/a2a/tasks`, `/api/conductor/fleet` (`:139-143`), 5s poll / 30s when WS connected (`:18-19`), WS `agents` channel triggers debounced refetch (`:178-188`), `mergeSnapshot()` (`:201-213`). Model: `OrchState = queued|running|waiting_approval|succeeded|failed|cancelled` (`omniroute:.../model/orchestrationTypes.ts:6-7`), `OrchSource = cloud-agent|a2a|conductor|routing` (`:8`), `OrchNode{...}` (`:14-43`), `OrchEdge{id,from,to,kind:"owns"|"mirror",active}` (`:45-51`), `MAX_WORK_NODES=40` (`:98`). Per-source mappers `fromConductor.ts`/`fromA2A.ts`/`fromCloudAgent.ts`. Drawer actions Approve/Cancel/Repeat (`omniroute:.../drawer/useDrawerDetail.ts:229-234,386-404`). 4 tabs: agents|routing|overview|history (`OrchestrationPageClient.tsx:20`).

**Missing upstream** (r3 open, r1 §124): no spawn tree (canvas is flat source→work, edges `owns` only), no kanban, no per-agent cost, no `answer` action, no `steer` action, no state-grouped board. A2A tasks are in-memory (5-min TTL) with a SQLite history fallback (`omniroute:src/lib/db/a2aTasks.ts:91-120`, retention 30d via `OMNIROUTE_A2A_HISTORY_RETENTION_DAYS`).

### 2.5 Reuse vs add (the whole decision in one table)

| capability | reuse as-is | add |
|---|---|---|
| host/client list | Conductor runners panel (`omniroute:.../conductor/ConductorPageClient.tsx:134-165`) | fleetd emits `/v1/runners` (§5.5) |
| flat agent list by source | orchestration canvas + `mergeSnapshot` (`useOrchestrationSnapshot.ts:201-213`) | a fourth `OrchSource 'fleet'` (B-module, P3) |
| graph render | `@xyflow` `FlowCanvas.tsx` | `spawned` edge kind + `reason` label (canvas has only `owns`/`mirror`, `orchestrationTypes.ts:45-51`) |
| drawer + Approve/Cancel/Repeat | `useDrawerDetail.ts:229-234` | `answer` + `steer` actions (missing, r3 open) |
| cost | `call_logs`/`request_cost_ledger`/`UsageAnalytics.tsx` | `run_id` column **+ a `GET /api/usage/by-run` read route** so the join stays API-level, and a per-agent rollup cached in `run_usage` (none exists, r1 §5; §5.3) |
| authz / keys | full pipeline + scopes (r1 §3) | nothing — a new MANAGEMENT namespace only |
| realtime | event bus + WS `agents` trigger (r1 §4) | fleetd → bus bridge |
| kanban | none exists (r1 §6, "no kanban") | **build** on `@dnd-kit` (P4) |
| questions/decisions store | `question.json`/`answer.json` (r2) | **build** `questions`/`decisions` tables — filled by the §5.7 reader at P3, authoritative (inbox files become exports) at P4 |
| memory graph | external MEMSPEC (A1) | **subscribe** to its events; store refs only |
| provenance packs | RESTART persists them (answers §A4/D-042) | **ingest** + link (P4 views) |
| project space (D-043) | nothing upstream: no project entity exists in Conductor, the canvas or AutoOS records | **build** `projects`/`plan_phases` + questions/decisions readers + portfolio/project pages at P3; `changelog_entries`/`reports` + their importer, publisher and blob store at P4 (§5.7, §10) |

## 3. Options and recommendation (ADDENDUM 2 overrides)

Three homes for the console, all sharing fleetd as the backend and `call_logs.run_id` as the cost join.

| Option | Shape | Upstream acceptance | Maintenance | Auth/cost reuse | Time to first view | Risk if upstream rejects |
|---|---|---|---|---|---|---|
| **A** | v4 module (SDK manifest) extending the canvas: new `OrchSource 'fleet'`, parent-child `OrchEdge kind:"spawned"`, board/costs tabs, answer/steer drawer actions | Target v4 `develop` modular core, `omniroute:ROADMAP.md:73-75,89-94`; must pass 68-locale + coverage + v4-feature gates | Upstream-owned after merge | Native (same site, same DB) | Slow — gated on v4 channel + review | PR stalls in the v4 queue |
| **B** | Same code, carried as a flag-off patch/overlay on a pinned upstream tag in the `autoos/omniroute` image, maintained by us | None needed for shipping | We rebase each weekly-ish release (`omniroute:CHANGELOG.md` cadence, r1 §10) | Native | Fast — no upstream wait | None (it's ours) |
| **C** | Separate Next.js app reusing OmniRoute conventions, reading its DB/API for cost | N/A | Second app to secure + deploy | Two logins, cross-origin DB/API access | Medium | n/a |

**Recommendation (ADDENDUM 2, answers "shape: (a)"):**
1. **Design as A** — every module piece is written exactly as the v4 module would be (canvas source `fleet`, `spawned` edge, tabs, drawer actions) so it upstreams unchanged.
2. **Ship as B** — carried flag-off inside the `autoos/omniroute` image until upstream accepts; B is the staging ground, not a fork of core.
3. **Never C** — rejected: two websites, two auth surfaces, cross-origin cost access.
4. **P1 needs ZERO OmniRoute change**: AutoOS runs **fleetd**, a small hub that *speaks the OmniConductor hub API* (`/v1/runners` = hosts/clients, `/v1/tasks` = spawner runs, `/v1/events` SSE) so the existing Conductor panel + canvas show AutoOS agents immediately by pointing `CONDUCTOR_HUB_URL` at fleetd. fleetd is also the console's single REST+MCP backend that the spawner and node agents post to; it keeps its own small SQLite and leaves cost data in OmniRoute, reading it **through** OmniRoute's per-run cost endpoint and caching the answer — never by joining the two databases (§5.3).

**Naming** (r1 §9 collision note): "Fleet" is **taken** upstream — the hub shapes are `FleetRunner`/`FleetTask`/`FleetSnapshot` and the skills reader is `fleetSkills.ts` (r3 §1); the canvas already uses "orchestration"/"Conductor". ("fleetd" itself is **our** process name, not evidenced in r1/r2/r3 — do not read it as existing upstream vocabulary.) New user-facing surfaces are named **"Agents"** and — for D-043 — **"Projects"** (avoid `pool`/`fleet`/`orchestration` as *new* MCP tool / i18n-namespace roots; check `RESERVED_MCP_NAMES` at build time, which is also why the tools are `projects_list`/`project_get`/`publish_report` rather than `fleet_reports_*`). "fleetd" is the internal hub process name only, never a tool prefix.

**Why each option fails alone:**

| | fails because |
|---|---|
| A alone | gated on the v4 `develop` channel opening + 68-locale/coverage gates (`omniroute:ROADMAP.md:89-94`, `ci.yml:548`) — first useful view could be months out |
| B alone | a patch set that drifts from upstream each weekly-ish release (r1 §10) and never upstreams — rebase debt with no exit |
| C | two websites, two auth surfaces, cross-origin DB/API cost reads; loses the "one board" goal (§1 G2) |
| hub-only (no module) | the hub protocol has no field for a spawn tree, kanban, per-agent cost, answer, or steer (r3 open) — it can't carry the differentiators |

The recommendation is **A∩B∩hub**: hub for P1 (immediate, zero upstream), module (A-shaped, B-shipped) for the pieces the hub can't express.

## 4. Data model (fleetd SQLite; one migration per phase; all ids TEXT)

fleetd stores orchestration state; **OmniRoute stays the source of cost** (joined by `run_id`, §5.3) — fleetd's `run_usage` is a **cache table**, filled by an **API-level join**: fleetd calls OmniRoute's per-run cost endpoint and writes the answer into its own SQLite. It is not a second ledger, and it is never a *SQL-level* join: fleetd does not open OmniRoute's database file, so there is **no `ATTACH DATABASE` and no cross-file view** anywhere in this design. Retention §4.7.

### 4.1 Tables

| table | columns (type) | key / notes |
|---|---|---|
| **hosts** | `id TEXT`, `name TEXT`, `labels JSON`, `last_seen INTEGER`, `agent_version TEXT` | PK `id`; host id comes from the node agent's own config (§5.4) — r2 §7 records that a host stamp is **absent** from every current record, which is the gap P0/§5.4 closes, not something r2 §5 (states) describes |
| **agents** | `id TEXT`(=canonical run id), `kind TEXT[claude-session\|spawner-run\|external]`, `project TEXT`, `host_id TEXT`, `client TEXT`, `model TEXT`, `tier TEXT`, `title TEXT`, `role TEXT[orchestrator\|worker\|reviewer]`, `state TEXT`, `state_detail TEXT`, `waiting_reason TEXT[limit\|dependency\|review]`, `resume_at INTEGER`, `started_at INTEGER`, `ended_at INTEGER`, `rc INTEGER`, `parent_agent_id TEXT`, `session_tag TEXT`, `remote_control_url TEXT`, `transcript_ref TEXT`, `cwd TEXT`, `branch TEXT`, `archived_at INTEGER`, `state_card JSON` | PK `id`; FK `host_id→hosts`, `parent_agent_id→agents`; `idx(state,project)`, `idx(parent_agent_id)`. **Landing migration per field** (§4's rule is one migration per phase, so a column lands with the first phase that consumes it): `waiting_reason` + `resume_at` with **P1** — they are what §4.4's `waiting` row means, what §5.5 promises stays in the row through the P1 limitation, and what `POST /api/agents/:id/resume` reads (§7.1); `state_card` with **P3** — the A3 state card has no producer before the P3 detail work (§4.6). Both migrations are additive `ALTER TABLE`s, so an older fleetd row is valid in a newer schema |
| **spawn_edges** | `parent_id TEXT`, `child_id TEXT`, `at INTEGER`, `card JSON`, `resolver JSON`, `reason_text TEXT` | PK `(parent_id,child_id)`; `resolver` = `{route,class,leg,reason,p,expected_cost,bucket,effort,skipped_legs}` (r2 §3 full `route_plan` set). P0 persists that set **in the AutoOS spawner's own records** (`job.json` / the worker record — P0 is AutoOS-only, there is no fleetd yet); **fleetd copies it into this column at P1** when the node agent/outbox first posts to it (§5.1→§5.2). "who spawned whom, why" (G3) |
| **tasks** | `id TEXT`, `project TEXT`, `title TEXT`, `body TEXT`, `column TEXT[backlog\|next\|doing\|review\|done]`, `priority TEXT`, `owner_agent_id TEXT`, `created_by TEXT`, `links JSON`, `created_at INTEGER`, `updated_at INTEGER`, `archived_at INTEGER` | PK `id`; `created_by` ∈ operator \| `agent:<id>` — A5 lets **both** create; `links` = task↔agent↔question↔memory refs |
| **questions** | `id TEXT`, `project_id TEXT?`, `agent_id TEXT`, `task_id TEXT?`, `text TEXT`, `options JSON`, `default_option TEXT`, `deadline INTEGER`, `blocks TEXT?`, `asked_at INTEGER`, `answered_at INTEGER`, `answer TEXT`, `answered_by TEXT`, `channel TEXT` | PK `id`; mirrors `question.json`/`answer.json` (`autoos-ask.py:193`, `autoos_agent_mcp.py:621-638`); **D-043**: `project_id` scopes it to a project's Questions tab, `options`/`default_option`/`deadline`/`blocks` are what the tab shows and what it answers in place |
| **decisions** | `id TEXT`, `project_id TEXT?`, `question_id TEXT?`, `adr_ref TEXT?`, `text TEXT`, `why TEXT?`, `decided_by TEXT`, `at INTEGER`, `evidence JSON`, `superseded_by TEXT?`, `gen_id TEXT?` | PK `id`; **append-only**; `evidence` may carry the SHIP/FIX-FIRST verdict r2 §7 lacks a store for; `gen_id`→`generations` (A4/D-042 "why this decision", §4.5); **D-043**: `project_id` scopes it, `question_id` is the answered question that produced it, `adr_ref` points at the repo's `docs/decisions/` ADR when the decision was written down that way (§5.7) |
| **approvals** | `id TEXT`, `agent_id TEXT`, `risk_class TEXT`, `action TEXT`, `detail JSON`, `requested_at INTEGER`, `decided_at INTEGER`, `decision TEXT`, `decided_by TEXT` | PK `id`; feeds the "needs you" column (§4.4) |
| **events** | `id TEXT`(ULID), `at INTEGER`, `agent_id TEXT?`, `type TEXT`, `actor TEXT[operator\|agent:<id>\|system]`, `payload JSON` | PK `id`; **append-only log of everything, incl. every steer message** (principle: steering is never silent, G5) |
| **run_usage** (cache table) | `agent_id TEXT`, `request_id TEXT?`, `at INTEGER`, `provider TEXT`, `model TEXT`, `tokens_in INTEGER`, `tokens_out INTEGER`, `tokens_cache INTEGER`, `cost_usd REAL?`, `fetched_at INTEGER` | PK `(agent_id,request_id,at)`; **a real table that fleetd writes**, one row per cost record returned by OmniRoute's `GET /api/usage/by-run?run_id=` (§5.3) — the `call_logs ⨝ request_cost_ledger` join runs **inside OmniRoute**, never inside fleetd's SQLite (no `ATTACH DATABASE`, no cross-file view). OmniRoute-side join key as written: `request_cost_ledger.request_id = call_logs.id` — **to verify at P2; if not, add `call_logs.run_id` → ledger via the same request** (§5.3). `agent_id, provider, model, tokens_in, tokens_out, tokens_cache, cost_usd` are the columns outline §4 asks to surface; `request_id` and `at` are carried for dedup and windowing and are not rendered. A second, **unpriced** producer fills rows from a node agent's transcript token counts (§5.4). Non-gateway clients get no priced row → cost NULL, shown "unknown" (r2 §7) |
| **heartbeats** (A3) | `agent_id TEXT`, `at INTEGER`, `context_tokens INTEGER?`, `context_cap INTEGER?`, `context_pct REAL?` | PK `(agent_id,at)`; FK `agent_id→agents`; `idx(agent_id,at)`; **one row per heartbeat** — `autoos-agent.py heartbeat` (`:1592-1668`) already returns the context fill (`context`: `{tokens, cap, pct}`, `:1529-1565`), and §4.6 promises it "persisted per heartbeat", which is a *series*, so it gets its own table rather than a column that would keep only the last value. `agents.state_card` is the one-value-per-agent case (§4.1), this is the many-values-per-agent case. A row whose three context columns are NULL is the no-fill case — the heartbeat itself is still recorded (§4.6, §8.2). Lands with the **P3 migration** (no P1–P2 reader exists), retention **30 d** (§4.7) |
| **knowledge_links** | `id TEXT`, `project_id TEXT?`, `from_kind TEXT[agent\|task\|question\|decision\|report]`, `from_id TEXT`, `entity_ref TEXT`, `relation TEXT`, `created_at INTEGER`, `created_by TEXT` | PK `id`; **`from_kind`'s `report` is a spec extension** beyond the addendum's list (`agent\|task\|question\|decision`) — rationale in one line: **reports cite knowledge the way decisions do**, so they need the same ref row and the same `POST`/MCP write path (§5.7/§7.1/§7.2); nothing else in this table extends the addendum's vocabulary. **ADDENDUM + answers**: `entity_ref` is the **STABLE id** of a memory-graph entity, kept **opaque — no schema coupling**; the graph's *entity* schema is still routing-00's redesign, but its *event envelope* is now agreed (`schema: 2`, §4.3). **Write path** (row has no natural producer otherwise): `POST` / `DELETE /api/knowledge-links` (§7.1) or MCP `knowledge_link` (§7.2), authenticated as `agents:write` (the agent that owns `from_id`) or an operator session; `created_by` records which. fleetd writes only **this row** — it never writes the graph, and reading the graph is proxied (§8.7) |
| **projects** (D-043) | `id TEXT`, `name TEXT`, `repo_url TEXT?`, `state TEXT[active\|paused\|archived]`, `goal TEXT`, `plan_ref TEXT?`, `handoff_note_ref TEXT?`, `created_at INTEGER` | PK `id`; one space per project (G6). `repo_url` is a public URL or an opaque label — never a private host path (§9). `state=paused` renders `handoff_note_ref` **as** the Overview tab (§8.11) |
| **plan_phases** (D-043) | `project_id TEXT`, `phase TEXT`, `title TEXT`, `status TEXT[done\|active\|pending\|blocked]`, `updated_at INTEGER` | PK `(project_id,phase)`; FK `project_id→projects`; "progress vs plan" on Overview (§8.11). Imported from the project's plan doc (`projects.plan_ref`) — inference: answers name the field, not the importer |
| **changelog_entries** (D-043) | `id TEXT`(ULID), `project_id TEXT`, `at INTEGER`, `title TEXT`, `commit_sha TEXT?`, `pr_ref TEXT?`, `agent_id TEXT?`, `source TEXT[changelog\|merge]` | PK `id`; FKs `project_id→projects`, `agent_id→agents`; `idx(project_id,at)`; one row per changelog entry **or** per merge on `main`, both linked to commit/PR and the authoring agent when known (§5.7) |
| **reports** (D-043) | `id TEXT`, `project_id TEXT`, `title TEXT`, `author_agent_id TEXT`, `gen_id TEXT?`, `created_at INTEGER` | PK `id`; FKs `project_id→projects`, `author_agent_id→agents`; `gen_id`→`generations.gen_id` so a report traces to the pack that produced it (D-042). `POST …/view-url` mints the render URL (§7.1/§9) |
| **report_versions** (D-043) | `report_id TEXT`, `version INTEGER`, `format TEXT[html\|md]`, `blob_hash TEXT`, `created_at INTEGER` | PK `(report_id,version)`; **append-only** — publishing never overwrites; `blob_hash` addresses the body in the content-addressed store (§4.5 shape, §4.7 retention: no expiry); rendered **only** via a signed view URL whose signature covers exactly this `(report_id, version)` pair, inside the §9 sandbox (§7.1/§9) |

Three notes on the D-043 rows. (1) inference: the answers list `project_id` + `adr_ref` for *both* `questions` and `decisions`, but `adr_ref` is only read on `decisions` — an ADR records a decision, and the question behind it is reached through `decisions.question_id`. (2) inference: `changelog_entries.id` is a ULID added here as the PK because the answers name no key for that row; dedup against the source is on `(project_id, commit_sha, source)` (§5.7). (3) `report_versions` is the one place fleetd stores an agent-authored **body**. Memory-graph bodies stay external (§4.3: no body in any event); a report's HTML/markdown is console content the operator asked for, so it is addressed by `blob_hash` like the §4.5 pack blobs, append-only, **outside the 90-day pack expiry** (inference: the answers set no report retention, and a report version is cited by later decisions — §4.7), and renderable only under the §9 sandbox.

### 4.2 `spawn_edges.resolver` — the persisted `route_plan` (G3 + G4)

fleetd stores the resolver's whole plan (r2 §3, `autoos_resolver.py:1734-1757`), because today only `{combo,reason,routing_version}` survives (`job.json :303-311`) and the rest is reproducible only by re-running `route` against a *changed* track record (r2 §7). Fields written into the `resolver` JSON:

| field | use |
|---|---|
| `route` `class` `client` `leg` | which lane served the run (r2 :60 — `leg` is never persisted today) |
| `effort` `max_tokens` `context_budget` `bucket` | cost shaping (G4) |
| `p` `expected_cost` | the resolver's own cost estimate (G4) |
| `decompose` `reviewers` `review` `escalation` | orchestration shape |
| `state` `defer_until` | feeds `waiting`/`resume_at` (§4.4) |
| `reason` `explain` `skipped_legs` `re_probe_notes` | the "why" on the spawn-tree edge (G3) |

`card` is the normalized task card (r2 :59, `autoos_routing.py:134/:226`) and joins to the `track-record.jsonl` scoring dims `route|class|served_leg|bucket|effort|p_success` (`autoos_track.py:20-32,133-167`) for a "did this class work" rollup.

### 4.3 Memory events (A1 — agreed with L1-backlog, `schema: 2`)

The shared knowledge graph (one graph behind a typed **memory facade, MEMSPEC**, L1-backlog lead — its spec is `docs/plans/2026-09-28-memory-facade-spec.md`) doubles as a channel: **every memory write emits an event**. fleetd subscribes and republishes on the console's `agents`/`memory` channel; the console shows a "new decisions / lessons" feed per project/domain and delivers them to subscribed sessions. **This is the shape agreed with L1-backlog (2026-09-28, `schema: 2`); it supersedes the 06:3xZ proposal in full**, so the memory feed is no longer blocked on an envelope question (§12):

```
{ id: ULID, at: <ISO UTC>,
  type: "memory.<entity_kind>.<created|updated|merged|superseded|redirected>",
  project, domain?, entity_id: <stable id>, entity_kind,
  version_id,                       // the new node version
  prev_version_id?,                 // updated | superseded only
  gen,                              // restart generation id (D-042)
  title: "<=120 chars>", actor: {kind: agent|operator|curator, id: <author session name>},
  source_ref?, supersedes?, merged_from?,
  visibility: project|global, schema: 2 }
```

- **There is no `deleted` verb.** Nothing is hard-deleted: an orphan prune is a `superseded` by a tombstone version, which is why every prune stays undoable.
- **Merged ids stay resolvable.** The facade answers an old id with a redirect to the survivor (the `redirected` event carries `merged_from`), and **undoing a merge is an inverse split event** — never a rewind of the store.
- **Edges have their own events**: `memory.edge.<linked|unlinked> {edge_id, rel (closed list), from_id, to_id, version_id}`. The hub-neighbourhood view (§8.7) follows these instead of polling the graph.
- **No body and no secret in any event** — `title` (≤120 chars) is the only carried content; the text is fetched from the graph by `entity_id` + `version_id`.
- **Append-only store; at-least-once delivery on a per-subscriber cursor** (the same cursor discipline the Conductor bridge already persists, `omniroute:src/lib/conductor/bridge.ts:177-250`).
- **Health metrics come from the facade as `memory_health()`** — nodes, edges, duplicate rate, orphan share, mean hops to hub, curator merges. §8.6 renders them; fleetd computes none of them.
- `actor.id` is the **author session name** (e.g. a worker's run title), not a person or an account — so the §9 privacy rule still holds when an event crosses the wire.

`gen` on every event ties a memory write back to the pack that produced it (`output_links`, §4.5), so "which context produced this lesson" traces exactly like "why this decision" (§8.9).

### 4.4 State vocabulary + board-column mapping

One set = the MCP/`TASK_STATES` tuple **plus** `waiting` (`autoos_agent_mcp.py:79-80` carries `submitted, working, input_required, completed, failed, canceled, rejected` — `waiting` is **not** in it, and neither is it in the A2A `TaskState` tuple, `taskManager.ts:66`, r3 §3). So: A2A-aligned **except** `waiting`, which this spec adds for the suspended/deferred case the spawner's routing vocabulary already has (`deferred`/`defer_until`, `autoos_resolver.py:689`) and the board needs as its own lane. Worker `running/died/exited` and routing `ready/deferred` are **inputs normalized into this set at ingestion (§5.1)**, never stored raw:

| canonical state | meaning | board column | source mapping |
|---|---|---|---|
| `submitted` | registered, pid not yet seen | running | `job.json` w/o pid (`_state` `:559-560`) |
| `working` | pid alive | running | pid alive (`:561-562`); worker `running` |
| `input_required` | needs-you: pending question or approval | **needs you** | `question.json` w/o `answer.json` (`:563-565`); stdout-question fallback (`:574-575`); canvas `waiting_approval` (`orchestrationTypes.ts:6`) |
| `waiting` | suspended: `waiting_reason ∈ limit\|dependency\|review`, `resume_at INTEGER` (both `agents` columns from the P1 migration, §4.1) | waiting | `deferred`/`defer_until` from route_plan (`autoos_resolver.py:689`) |
| `completed` | rc 0 | done | (`_state :555-556`) |
| `failed` | non-zero rc, dead-pid-without-exit (`detail "lost"`), or REPORT status failed | failed | (`:557-558,566-567,576-577`); worker `died`/`exited rc≠0` |
| `canceled` | operator/agent cancel | **no lane — filtered from the board**: the five lanes (§8.1) have nothing for it, and a run that was in "needs you" when it was canceled **leaves** that lane rather than appearing in it | `exit.json.cancelled` (`:553-554`) |

`rejected` (spawn refusal, `autoos_agent_mcp.py:356-359`) is **not a board column** — it is a spawn-attempt outcome recorded in `events`, not an `agents` row. The five board lanes: **running · needs you · waiting · done · failed** (outline §8).

### 4.5 Provenance (A4 — D-042 "Context provenance")

For every agent response and restart, the operator sees **exactly which cards and context the agent was given**. Produced by others (RESTART / L1-routing); fleetd ingests and links.

| table | columns | key / retention |
|---|---|---|
| **generations** | `gen_id TEXT`, `agent_id TEXT`, `kind TEXT[spawn\|restart]`, `reason TEXT`, `pack_hash TEXT`, `manifest_ref TEXT`, `created_at INTEGER` | PK `gen_id`; content-addressed (`pack_hash`) |
| **pack_blobs** | `hash TEXT`, `bytes BLOB?`, `ref TEXT?`, `created_at INTEGER`, `expires_at INTEGER` (90d) | PK `hash`; **90-day retention** |
| **manifest_items** | `gen_id TEXT`, `item_kind TEXT[card\|fact\|event\|file]`, `item_id TEXT`, `version TEXT`, `source_ref TEXT`, `author TEXT` | PK `(gen_id,item_kind,item_id,version)`; **kept forever** |
| **output_links** | `output_kind TEXT`, `output_id TEXT`, `gen_id TEXT` | PK `(output_kind,output_id,gen_id)`; ties a ready/decision/memory-write/question/answer back to the pack that produced it; **forever** |
| **transcript_refs** | `agent_id TEXT`, `file TEXT`, `offset_start INTEGER`, `offset_end INTEGER` | PK `(agent_id,file,offset_start)`; link to full transcript by file+offset (transcript text stays on host, §9). **`file` is never an absolute path as stored**: transcripts live under `$HOME/.claude/projects/<cwd-mangled>/*.jsonl` (`autoos_context.py:186-207`), so the value is normalized to be **relative to the projects root** (`<cwd-mangled>/<session>.jsonl`) or written with a `$`-placeholder for the root. The node agent resolves the root locally from its own config. Storing the raw path would put a username in the row and break §11's "no absolute user paths in any artifact" |

**Views** (after P3, §10): agent lineage timeline (generations + restart reasons); pack diff between two `gen_id`s; "why this decision?" trace (`decision → gen_id → pack → manifest_items → sources/authors`); replay pack (exact text rebuilt from blobs + versioned facts); transcript links by file+offset. Outputs carry `gen=<id>` (answers), decisions carry `gen_id` (§4.1).

### 4.6 Agent detail extras (A3)

`agents` rows gain a **state card** + **context fill** rendered on the agent detail view. Context fill is `autoos-agent.py context` (`:1529-1565`, `{tokens, cap, pct, model, transcript, source}`) — Claude-only (`autoos_context.py:186-207`), other clients → `{context:"unknown"}`. State card = the RESTART/L1-routing card the operator uses to relaunch a session; fleetd stores its rendered form as a JSON blob on the agent.

**Where each one lives** (§4.1, so this is not left to inference):

| datum | store | why that shape | lands |
|---|---|---|---|
| state card | `agents.state_card JSON` | one current value per agent — the card a restart is minted from, overwritten when a new generation is rendered; the detail reads a column, it does not scan a log | **P3 migration** (no producer before the P3 detail work) |
| context fill | `heartbeats` — one row per heartbeat (`agent_id, at, context_tokens, context_cap, context_pct`) | A3's "persisted **per heartbeat**" is a series, not a snapshot: §8.2's `ctx 148k/200k 74%` is the newest row and its `tokens ▁▂▅█` sparkline needs the rows before it, which a single column would destroy | **P3 migration**, retention **30 d** (§4.7) |

A client that reports no fill still gets a `heartbeats` row with the context columns NULL — the heartbeat is the fact that the agent was seen; the fill is optional (§5.4's degrade path keeps posting it). `events` remains the audit log of state transitions (§4.1) and is not either store.

### 4.7 Retention

| data | policy | basis |
|---|---|---|
| `events`, `run_usage` | **fleetd runs its own daily cleanup job**, with **its own default: 30 d**, configurable (`FLEETD_EVENT_RETENTION_DAYS`). The default *mirrors* OmniRoute's `callLogs 30d` so the two stores age at a comparable rate — fleetd **does not read OmniRoute's setting**, so a dashboard edit on the OmniRoute side cannot silently change what the console retains or can replay. Trimming **bounds replay**: a per-subscriber cursor older than fleetd's own trimmed window cannot be replayed from, so a reconnect with a too-old `last_event_id` is answered with a full resync (snapshot + a `resync` marker) rather than a partial log (§7.3) | outline §4 + answers "retention: (a)"; the 30 d *number* is taken from `omniroute:src/types/databaseSettings.ts:58-81` (r1 §5) as a convention, not as a live setting |
| `agents` | archive `done` after 24h (`archived_at`), keep 30d | outline §4, answers "retention: (a)" |
| `heartbeats` | **30 d**, trimmed by the same daily cleanup job as `events`/`run_usage` and by the same configured default (`FLEETD_EVENT_RETENTION_DAYS`) | A3 (§4.6): the fill is needed to render the current detail and its recent sparkline, not as history. It is a cache of an external read — like `run_usage` — so trimming loses nothing durable; the durable forms are `agents.state_card` and the provenance pack (§4.5, kept by its own rules). Cascade with the `agents` row it belongs to, so an archived agent's series goes with it |
| memory events (`memory.<kind>.*` + `memory.edge.*`, `schema: 2`) | follow the graph's own retention; fleetd keeps only the cursor + emitted envelope | A1, §4.3 |
| `pack_blobs` | **90 days** | A4 |
| `manifest_items`, `output_links`, `transcript_refs` | **forever** (overrides the 30d default) | A4 |
| `projects`, `plan_phases`, `changelog_entries`, `decisions` | **forever** — a project's history is the thing the page exists to show; `archived` state is a flag, never a delete | D-043 (inference: answers name no retention for these; they are append-only rows) |
| `reports` / report blobs | **all versions kept, no expiry** | D-043; inference: answers set none, and a later decision cites a report by version |
| `track-record.jsonl` / `orch-*.log` | unchanged on the AutoOS side (unbounded today, r2 §7) — fleetd does not replicate them | — |

### 4.8 fleetd ↔ OmniRoute boundary

Two stores, one join key, no double-counting of cost.

| concern | owner | why |
|---|---|---|
| agents, hosts, spawn_edges, tasks, questions, decisions, approvals, events | **fleetd** SQLite (small) | orchestration truth; must exist offline (r2 §5.2) |
| provenance: generations, pack_blobs, manifest_items, output_links, transcript_refs | **fleetd** SQLite | produced by RESTART/spawn (answers §A4/D-042), read by the console |
| project spaces: projects, plan_phases, changelog_entries, reports, report_versions (+ their blobs) | **fleetd** SQLite | imported from git and the router's files (§5.7) — the repo stays the source of truth, fleetd keeps the readable index (D-043) |
| memory graph (nodes/edges/entities) | **external MEMSPEC graph** (L1-backlog) | fleetd holds only the emitted envelope + `knowledge_links.entity_ref` (stable id, opaque, §4.3). Every graph read the console makes is **proxied by fleetd through the memory facade** — the same boundary as `GET /api/memory/feed` and `/api/memory/health` (§7.1, §8.7); the browser never talks to the graph |
| per-request cost: `call_logs`, `request_cost_ledger` | **OmniRoute** SQLite (already the source, r1 §5) | single cost ledger; no second one |
| the join | **an API-level join**: `call_logs.run_id` (new column) ↔ `agents.id`, ledger side `request_cost_ledger.request_id` ↔ `call_logs.id` (**assumption, verify at P2**, §5.3) — executed **inside OmniRoute** and exposed as `GET /api/usage/by-run?run_id=` | §5.3. fleetd caches the response in its `run_usage` **table**; it never opens OmniRoute's SQLite, so no `ATTACH DATABASE` and no cross-file view |
| realtime transport | **OmniRoute** event bus + live WS (r1 §4) | the canvas already refetches on WS `agents` (`useOrchestrationSnapshot.ts:178-188`) |

## 5. Ingestion

Every field the console shows originates in an existing AutoOS record; fleetd only normalizes and links it (the §2.2 gaps are what P0 adds, not what P1 invents):

| source record (r2) | → fleetd table | gap it closes |
|---|---|---|
| `logs/agents/<run id>/job.json` (`:401-410`) | `agents`, `spawn_edges.card` | unlinked run id (§2.2) |
| `logs/workers/<id>.json` (`:2996-3009`) | `agents` state + `events` | run-id ≠ worker-id |
| `route_plan` from `route`/`spawn` (`:1734-1757`, `:411`), persisted by the spawner at P0 (§5.1) | `spawn_edges.resolver` (fleetd's copy, posted at P1 via §5.2) | route_plan not persisted (§2.2) |
| `question.json`/`answer.json`/`qa-<n>.json` (`autoos-ask.py:110-143`) | `questions`, `events` | ask-back history invisible to `ps` |
| REPORT block (`autoos_report.py:43`) + verdict lines (`:1008-1036`) | `decisions.evidence` | no structured verdict store (§2.2) |
| `context --json` (`:1529-1565`) + heartbeat (`:1592-1668`) | `agents.state_card` + one `heartbeats` row per heartbeat, posted on the §5.2 outbox (A3, §4.1/§4.6) | not persisted today |
| gateway cost API `GET /api/usage/by-run?run_id=` — OmniRoute joins `call_logs ⨝ request_cost_ledger` (`autoos_usage.py:238`) and answers per run | `run_usage` cache table | no cost per run (§2.2) |
| router `QUESTIONS.md`/`DECISIONS.md`, each repo's `CHANGELOG.md` + `docs/decisions/` ADRs, MCP `publish_report` | `projects`, `plan_phases`, `questions`, `decisions`, `changelog_entries`, `reports` (§5.7) | no project space exists anywhere (D-043) |

### 5.1 Canonical run id + parent + host (P0, AutoOS only)

`AUTOOS_AGENT_RUN_ID` is minted once per spawn and **propagated in env to children** (child's parent = the spawning process's `AUTOOS_AGENT_RUN_ID`). This unifies `logs/workers` and `logs/agents/<id>` under one id (today they are two strings for the same spawn, r2 §3/:57), closes the missing parent edge (r2 :58), and stamps host. Persist at P0 — **into the spawner's own records** (`job.json` and the worker record; P0 is AutoOS-only, there is no fleetd and no `spawn_edges` table yet): canonical `run_id`, `parent_run_id`, `host`, and the **full `route_plan`** (today only `{combo,reason,routing_version}` survives, r2 §3/:59; the `leg`, `p`, `expected_cost`, `bucket`, `skipped_legs` the console needs for G3/G4 are otherwise reproducible only by re-running `route` against a *changed* track record, r2 §7). **fleetd ingests those records into `spawn_edges.resolver` at P1**, when the outbox posts them (§5.2) — the table is where the plan lands, not where it is minted. State inputs (`running`/`died`/`ready`/`deferred`/stdout-questions) are normalized into the §4.4 vocabulary at ingestion.

### 5.2 Spawner → fleetd (outbox + retry)

`autoos-agent` posts `register` / `state-update` / `end` + `spawn_edge` + `question`/`answer` events + heartbeats (§4.6) to the fleetd console API. Writes go to a **local outbox file first and retry when the API is down — the on-disk records stay the local truth**, mirroring the two disciplines the spawner already ships and keeping them distinct: **atomic replace** is how a record that changes over time is written — `job.json` (`_write_json` → tmp + `os.replace`, `autoos_agent_mcp.py:117-121`, used at `:404,410`) and the worker record (`_write_worker_record`, `autoos-agent.py:3042-3047`); **create-once `O_EXCL`** is how a claim is written so two writers cannot both own it — `exit.json` (`autoos_agent_mcp.py:346-353`), the same discipline as `question.json` (`autoos-ask.py:74-88`). The outbox entry is a record that changes over time, so it takes the atomic-replace one. fleetd is at-least-once: dedup on the event ULID.

### 5.3 Gateway cost join — `X-AutoOS-Run-Id` → `call_logs.run_id` → `GET /api/usage/by-run` (P2)

Copy the `session_tag` precedent exactly (r1 §5): a **new header `X-AutoOS-Run-Id`** the spawner injects provider-level (as `SESSION_TAG_HEADER` is, `autoos-agent.py:1430-1433`) → read into a **new `call_logs.run_id` column** on the same path as session_tag: header read → attempt logging → migration + heal-list entry → read filter (r1 §5: `chatCore.ts:1086-1093` → `attemptLogging.ts:289` → `migrations/133:1` → `schemaColumns.ts:324` → `callLogs.ts:975`).

**The join is an API-level join, not a database-level one.** Cost per run resolves by joining `request_cost_ledger` (the only per-request cost table, r1 §5) to `call_logs` and filtering on `run_id` — and that query runs **inside OmniRoute**, which exposes the answer as a **new management endpoint `GET /api/usage/by-run?run_id=`** returning that run's cost rows (`provider`, `model`, token counts, `cost_usd`, `request_id`, `at`). fleetd calls it with its management key (§9), caches the rows in its own `run_usage` table (§4.1), dedups on `(agent_id, request_id, at)`, and serves `GET /api/usage` from the cache. The two upstream-able changes ship together as one P2 request: the `call_logs.run_id` column **and** the `by-run` read endpoint. **Rejected readings of the word "join"**: SQLite `ATTACH DATABASE` over OmniRoute's file, or a cross-file view — both would couple two stores that are not co-located, are backed up separately, and are trimmed independently; they also hand fleetd a full read path into a database that holds credentials-adjacent tables. If the endpoint is ever unavailable, cost shows as `?` (unknown) rather than the console reaching around it.

**The join key is not established by r1**: r1 §5 shows `request_cost_ledger` carries `request_id TEXT` while `call_logs` carries no `request_id` — its PK is `id TEXT`. So this spec writes the OmniRoute-side join as `request_cost_ledger.request_id = call_logs.id` **(to verify at P2; if not, add `call_logs.run_id` → ledger via the same request)** and does not claim it as fact. The P2 acceptance test is the verification: a gateway run whose `cost_usd` resolves non-zero through the endpoint, or the alternative column gets added in the same migration. **This is a field addition to a hot table plus a new read route**, so it rides the P2 upstream-able-fix question (section 12) — a `call_logs` column needs a migration + a `schemaColumns.ts` heal-list entry + an index, and the endpoint is a management-namespace read handler; both are subject to the `v4-feature`/fix gate. Non-gateway clients (`qwen`/`gemini`/`codex` behind `omniroute run`, and any direct client) get no run-id → **cost shown as "unknown"** (r2 §7/:67).

### 5.4 Node agent for Claude + non-spawner sessions (P1)

A per-host **node agent** (Python, answers "node agent: (a)"; small daemon, systemd user unit / Windows scheduled task) polls `claude agents --json` for live sessions (`state`, `name`, `cwd`) — **to verify before P1**: neither the command nor its output shape is evidenced in r1/r2/r3, so P1's first task is confirming it exists and emits those three fields. **Degrade path**: if the command is absent, errors, or its JSON does not parse, the node agent posts the session with `state="unknown"` and alerts (§11 "Claude CLI output drift") — the session is **still listed**, because it is enumerated from the transcripts below rather than from this command alone. **Fallback source**: the transcript directory (`$HOME/.claude/projects/<cwd-mangled>/*.jsonl`, newest by mtime, the same discovery `autoos_context.py:186-207` already does) plus the process list (a live `claude` pid with its cwd) — which gives id/cwd/mtime/usage but no authoritative state, hence `unknown`. It reads transcript usage (tokens from `message.usage`, the same computation as `autoos_context.py:40-41,125-156`) → posts `agents` + those token counts, which fleetd stores as **unpriced** `run_usage` rows (`cost_usd` NULL — a non-gateway session has no priced row anywhere, §4.1/§5.3). It maps a session to its spawner run by name/env (the `--joinable` sanitized title already becomes the `--bg --remote-control <name>`/`--name`, `autoos-agent.py:1451`). Host id comes from the node agent's own config, **never from the request**. Auth: a per-host API key with a narrow `agents:write` scope (§9). It does **not** upload transcript bodies — only token counts and offsets, with the `transcript_refs.file` normalization of §4.5 (§4.5/§9).

### 5.5 fleetd as an OmniConductor-compatible hub (P1 — exact shapes)

fleetd serves the shapes the existing Conductor proxy expects, so **no OmniRoute change is needed** — point `CONDUCTOR_HUB_URL` at fleetd. Contract-pinned to `omniroute:src/lib/conductor/hubProxy.ts` (the r3-exact shapes below); contract tests assert each (section 12 hub-stability risk).

| hub endpoint (fleetd serves) | shape OmniRoute consumes | citation |
|---|---|---|
| `GET /v1/runners` → `[FleetRunner]` | `FleetRunner {id, name, clis[], online, draining}` is what `hubProxy` parses — but the same endpoint also carries **OASF capabilities**, which `fleetSkills.ts:50-68` reads to derive A2A skills (one per online CLI profile + declared fleet skills, cached 60s, fail-open). `FleetRunner` does not name that field, so it is easy to omit: **fleetd must emit whatever `fleetSkills` reads** (capabilities per runner) or fleet skills silently degrade to none | `hubProxy.ts:16-22`; `fleetSkills.ts:37-38,50-68,70-105` |
| `GET /v1/tasks` → `[FleetTask]` | `FleetTask {id, status, mode, repo, runner, summary, branch, error, updated_at}` | `hubProxy.ts:24-34` |
| `GET /v1/tasks/:id` → `ConductorTaskDetail` — **inference, verify before P1 or drop this row**: r3 evidences the *type* and the OmniRoute-facing `GET /api/conductor/tasks/[id]` → `getConductorTaskDetail()`, but never observes which hub path that function calls. fleetd should serve it because the detail drawer needs it, not because the hub is known to ask | `FleetTask + {prompt, base_ref, tests, council, created_at, cli, model}` (`hubProxy.ts:42-52`); `tasks/[id]/route.ts:13-22` | `hubProxy.ts:42-52` (shape only) |
| (snapshot wrapper the proxy builds) | `FleetSnapshot {offline, runners[], tasks[]}` | `hubProxy.ts:36-40` |
| `POST /v1/tasks` `{repo:{url,base_ref}, spec:{prompt}, mode, requirements:{cli?,model?}}` | `createConductorTask` | `hubProxy.ts:229-258` |
| `POST /v1/tasks/:id/cancel` | `cancelConductorTask` | `hubProxy.ts:261-277` |
| `GET /v1/events?last_event_id=` (SSE) | mirrored into local A2A by the bridge | `omniroute:src/lib/conductor/bridge.ts:177-250` |

Statuses must map onto the hub's vocabulary, which the bridge turns into A2A (`bridge.ts:21-37`): `created→submitted`, `scheduled`/`input_required→working`, `completed→completed`, `failed→failed`, `canceled→cancelled`. **`waiting` has no home in that vocabulary** — the hub status set the bridge reads and the A2A `TaskState` it produces (`taskManager.ts:66`: `submitted|working|completed|failed|cancelled`) both lack it, so there is no value fleetd can send that arrives as "suspended". **P1 limitation, stated explicitly**: fleetd emits `scheduled` for a `waiting` run, which the canvas renders as `working`; a deferred run is therefore indistinguishable from an active one **on the P1 canvas**. The console's own `GET /api/agents` (§7.1) reads fleetd's canonical §4.4 state and is correct from P1, and `waiting_reason`/`resume_at` stay in fleetd's `agents` row regardless (§4.1, P1 migration); the accurate waiting lane arrives with the B-module `fleet` source at P3, where the state is carried directly rather than through the hub's five names. fleetd emits `runners` = hosts/clients and `tasks` = spawner runs; the spawn *tree*, kanban, answer/steer and cost-per-agent are **not expressible in the hub protocol** — those are B-module work (§3, P3). The bridge keeps a persisted cursor in `key_value` (OmniRoute side), so fleetd's SSE must honour `last_event_id` for at-least-once redelivery, and treat a cursor older than its own retention as a full resync (§4.7/§7.3).

### 5.6 Provenance + memory ingestion (A1/A4)

RESTART and spawn already **persist every context pack + manifest content-addressed, tagged `gen=<id>`** (answers §A4); MEMSPEC recalls carry node+version ids; outputs (ready, decisions, memory writes, questions/answers) carry `gen=<id>`. fleetd ingests these as `generations`/`manifest_items`/`output_links` (§4.5) and links a decision → `gen_id` for the "why this decision" trace. Memory writes reach fleetd **as events** (§4.3), not as stored bodies — `knowledge_links.entity_ref` holds only the stable graph id.

### 5.7 Project import + report ingestion (D-043)

Every project page is built from artifacts that **already exist** — the import is a reader, not a new writing convention. Four readers, one publisher. **The importer is split across two phases** (§10): the questions/decisions/projects readers land with the P3 project pages, the `changelog_entries`/`reports` readers with P4, because the blob store and the merge-walking reader are P4 work — and everything P3 renders must then have a source that exists at P3.

| source | → fleetd | how | lands at |
|---|---|---|---|
| the router's `QUESTIONS.md` / `DECISIONS.md` — each entry already carries a `project:` field | `projects` (upsert by name), `questions`, `decisions` (+ `project_id`) | same parser that P4 uses to *export* inbox files (§10): import and export are one reader, so the file format cannot drift twice | **P3** |
| each repo's `CHANGELOG.md` **or** its `changelog.d/` fragments — OmniRoute's own convention is `changelog.d/features/<PR>-<slug>.md` (r1 §10) — plus **merges on `main`** | `changelog_entries` with `source=changelog` / `source=merge` | merge rows carry `commit_sha`, `pr_ref`, and `agent_id` when the merge was agent-driven (join `agents` by branch/run id, §5.1) | **P4** |
| the repo's `docs/decisions/` ADRs | `decisions.adr_ref` | the console links out to the ADR; it never restates its text (the ADR is the durable form, the row is the index) | **P3** (with `decisions`) |
| the project's own plan doc (`projects.plan_ref`) | `plan_phases` | inference: the answers carry `plan_ref` and the Overview's "progress vs plan", but name no importer — the reader walks the plan's phase headings (this spec's own §10 table is exactly that shape) | **P3** |
| MCP `publish_report(project, title, html\|md)` (§7.2) | `reports` + `report_versions` + blob | version increments, nothing overwrites; `reports.gen_id` recorded so a report traces to the pack that produced it (§4.1/§4.5) | **P4** |

**P3's "last change" / "last merges" without the table.** The portfolio card's *last change* (§8.10) and the Overview's *last merges* (§8.11) are wanted at P3, but `changelog_entries` does not exist until P4 — so at P3 both read **`git log main` directly** (a merge-log query per configured repo: subject, sha, author, relative age; no table, no import, no dedup) and the same cells become `changelog_entries` reads at P4, when the history needs to carry `pr_ref`/`agent_id` and survive a repo being offline. That keeps every rendered P3 fact backed by a source that exists at P3, and the swap is one query → one table read.

Cadence and safety: the importer runs on the same at-least-once outbox discipline as §5.2 — a repo or a router lane can be offline, and a re-read is idempotent (`changelog_entries` dedups on `(project_id, commit_sha, source)`, falling back to `(project_id, title, source)` for a fragment that names no sha; `decisions`/`questions` upsert on the stable id the file already carries). An entry that fails to parse becomes a visible console alert plus an `events` row, never a quietly empty tab (§11 "import drift"). `projects.state` is set by the operator, or derived from the registry's project state; `state=paused` makes the handoff note the Overview (§8.11) — the same handoff artifact a lane writes for its successor session (`unattended-orchestration` `references/layers.md`: "a successor brief is written from the predecessor's DONE note").

## 6. Kanban: built-in vs Vikunja

Answered: **(a) built-in** (answers "kanban: (a)").

| criterion | built-in (chosen) | Vikunja |
|---|---|---|
| one website / one login | yes | second login + DB + API |
| same auth (§9) | yes | separate |
| agent ↔ run ↔ task linking | native (`tasks.links`, `owner_agent_id`) | none (no run ids) |
| OmniRoute-shaped UI | `@dnd-kit` already a runtime dep (`omniroute:package.json:302-304`) — a board is new work but no new dependency | not OmniRoute-shaped |
| phone apps / CalDAV | n/a | mature |

**Built-in.** `@dnd-kit/sortable` ships only a vertical sort — a multi-container board is new work (r1 §6); `droppable` is inside `@dnd-kit/core`, already installed (inference: confirm a multi-container `DndContext` works on the pinned version before P4 — flagged in section 12). Keep a **CSV/JSON export** and an optional **one-way Vikunja sync as a later option** (outline §6).

## 7. API + MCP + event channel + push

### 7.1 REST (fleetd; management-equivalent auth, §9)

| method | path | auth | body | returns |
|---|---|---|---|---|
| GET | `/api/agents` | manage / `agents:read` | query: `project`,`state`,`host`,`since` | `{agents:[…]}` (state-grouped, G2) |
| GET | `/api/agents/:id` | manage | – | agent + `children` + `events` + `usage` + `state_card` (the `agents.state_card` column) + `context_fill` (the newest `heartbeats` row **and** the series behind §8.2's sparkline) — A3, §4.1/§4.6 |
| POST | `/api/agents` | `agents:write` (node key) | agent register | `{id}` |
| PATCH | `/api/agents/:id` | `agents:write` | state update | `{ok}` |
| GET | `/api/hosts` | manage | query: `since` | `{hosts:[…]}` from the `hosts` table (§4.1): `id`, `name`, `labels`, `last_seen`, `agent_version` — the board needs it to tell "fleetd is down" from "this one host stopped reporting" and to render the `stale · <age>` pill (§8.1); without it the host list is only reachable through the hub's `/v1/runners` (§5.5) |
| POST | `/api/agents/:id/steer` | **operator session only** | `{text}` | always logged as an `events` row first (G5, never silent), **then delivered on the channel defined per agent kind** — see the steer-delivery note under this table |
| POST | `/api/agents/:id/stop` | operator session | – | cancel (SIGTERM group, `autoos_agent_mcp.py:641-660`) |
| POST | `/api/agents/:id/resume` | operator session | – | resume from `waiting` (`resume_at`) |
| GET/POST/PATCH | `/api/tasks` | operator or `tasks:write` | task fields | board columns; **A5: creatable by operator AND by `agent:<id>`**, each carrying its full response history (`tasks ⨝ questions ⨝ decisions` order) |
| GET/POST | `/api/questions` | write scope | question fields; query `?project=` | pending questions with options / default / deadline / blocks (§8.11 Questions tab) |
| POST | `/api/questions/:id/answer` | operator session **or** a signed ntfy **action token** for this one question (§7.4) | `{answer, answered_by}` | writes `answer.json`, flips `input_required→working`; a token use is single-shot, expiring and audited (§7.4) |
| GET | `/api/decisions` | manage | `?project=&since=` | decisions with why / sources / who / `superseded_by` / `adr_ref` / `gen_id` (D-043 + D-042) |
| POST | `/api/approvals/:id/decide` | operator session **or** a signed ntfy **action token** for this one approval (§7.4) | `{decision, decided_by}` | approve/deny (G5). **POST only** — a state-changing decide must not be reachable by a `GET` (a prefetched or history-replayed URL would decide an approval with no side-effect semantics and outside the CSRF/origin check that guards mutations, §9) |
| GET | `/api/spawn-tree` | manage | `?root=<agent_id>` | nested `agents`+`spawn_edges` (G3) |
| GET | `/api/projects` | manage | query: `state` | **portfolio cards** (§8.10): state pill, open-question count, running agents, last change, spend this week |
| GET | `/api/projects/:id` | manage | – | project + `plan_phases` + questions + decisions + report cards + its agents (G6, §8.11) |
| PATCH | `/api/projects/:id` | operator session | `{state?,goal?,plan_ref?,handoff_note?}` | set state / the paused project's handoff note (D-043) |
| GET | `/api/projects/:id/changelog` | manage | `?since=&source=changelog\|merge` | `changelog_entries` with commit / PR / authoring agent (§5.7) |
| GET | `/api/projects/:id/reports` | manage | – | reports + their version list, newest version first |
| POST | `/api/projects/:id/reports` | `reports:write` (agent key) or MCP | `{title, format: html\|md, body}` | `{report_id, version}`; body → blob, a prior version is **never** overwritten (§5.7) |
| GET | `/api/reports/:id/versions/:n` | manage | – | one stored body, for the operator's **own** read / download in the app session. **This is not what the report iframe loads** — see the render-path note below the table |
| POST | `/api/reports/:id/versions/:n/view-url` | **manage (operator session)** | – | `{url, expires_at}` — a **signed, short-lived render URL on the separate report origin**. HMAC-SHA256 over `(report_id, version, expiry)` with a server secret, `exp` **60 s**, single-use **optional** (a used nonce is remembered only if the operator enables it). The parent page calls this and sets the iframe `src` to the result; the report origin serves the blob **only** against a valid, unexpired signature, with a `Content-Security-Policy: sandbox` header and **no cookies** (§9) |
| GET | `/api/usage` | manage | `?group_by=agent\|project\|model&window=today\|7d\|30d` | cost rollup over fleetd's cached `run_usage` **table** — rows fetched from OmniRoute's `GET /api/usage/by-run`, never joined in fleetd's SQL (G4, §5.3) |
| GET | `/api/provenance/:gen_id` | manage | – | generation + manifest_items + pack diff vs a second `gen_id` (A4) |
| GET | `/api/memory/feed` | manage | `?project=&domain=&cursor=` | recent `memory.<kind>.*` and `memory.edge.*` events (§4.3, `schema: 2`), at-least-once via `cursor` |
| GET | `/api/memory/health` | manage | `?project=` | the facade's `memory_health()` passed through — nodes, edges, dup rate, orphan share, mean hops→hub, curator merges (§8.6); fleetd computes none of it |
| GET | `/api/memory/neighbours` | manage | `?project=&entity_id=&hop=1` | the hub-neighbourhood shape (§8.7): **fleetd proxies the memory facade's one-hop expansion** and returns ids + titles + relations — the same boundary the feed and health rows use. The browser never holds a graph credential |
| POST | `/api/knowledge-links` | `agents:write` (agent key) **or** operator session | `{from_kind, from_id, entity_ref, relation, project_id?}` | write path for `knowledge_links` (§4.1): stores the **ref row only**, never touches the graph; `created_by` records the caller; returns `{id}`. `entity_ref` stays opaque — fleetd does not resolve it (§4.3) |
| DELETE | `/api/knowledge-links/:id` | `agents:write` (rows it created) **or** operator session | – | drops the link row. **Not** a graph delete — the facade owns entity lifecycle (§4.3: no hard delete, tombstones + inverse-split only) |
| SSE/WS | `/api/agents/stream` (channel `agents`) | session | `?last_event_id=` | live updates on the existing event bus (`omniroute:src/lib/events/eventBus.ts`; canvas already refetches on WS `agents`, `useOrchestrationSnapshot.ts:178-188`) |

**Report render path (why the body endpoint is not the frame's `src`).** A cookie-authed `GET /api/reports/:id/versions/:n` can never be what the sandboxed iframe loads: the frame is deliberately **cross-origin and cookie-less** (§9), so an authenticated GET from inside it would arrive unauthenticated and fail — and the obvious "fix" (attach the session, or allow `allow-same-origin`) is what the sandbox exists to prevent. So the parent page — which *does* hold the operator session — calls `POST /api/reports/:id/versions/:n/view-url` (auth `manage`) and gets back a URL on a **separate report origin** whose signature authorizes the read by itself: `?rid=<report_id>&v=<version>&exp=<unix-seconds>&sig=<hex>`, where `sig` is **HMAC-SHA256 over `(report_id, version, expiry)` keyed with a server secret**, and `exp` is **60 s** out. The report origin — a dedicated sub-path or host the operator configures in fleetd's own git-ignored config, named there and never in this document — serves the blob **only** when the signature verifies, is not expired, and covers the exact row being asked for; it sets `Content-Security-Policy: sandbox` on the response and **issues and reads no cookies**. Single-use tracking (a seen-nonce table) is **optional**, default off: a 60-second window is the primary control, and a refresh re-mints. The parent sets the iframe's `sandbox="allow-scripts"` with **no `allow-same-origin`** (§9). The mint is an audited action (`events` row, §4.1) and the URL is never persisted in a row or a log line — only its expiry is.

**Steer delivery, per agent kind (G5).** A steer is *recorded* unconditionally — one `events` row (`agent.steer`, append-only, §4.1) attached to the steered agent and visible on the parent's lineage view (§8.3) and the agent's own timeline (§8.2), which is what "the parent sees it" means. *Delivery* is a separate thing and it depends on what kind of agent is being steered, because the evidenced write paths do not cover every case:

| target kind | state | channel | basis |
|---|---|---|---|
| spawner run | `input_required` | `respond` → `answer.json` in the run dir; the parked process reads it and continues | `autoos_agent_mcp.py:621-638` (r2 §1) — the one delivery path that is fully evidenced today |
| spawner run | `working` | **`respond` refuses this** (it accepts only `input_required`, non-`ended`: `:621-638`). fleetd creates a `steer.json` in the run dir with `question.json`'s **create-once `O_EXCL`** discipline and nothing more (`autoos-ask.py:74-88`: exclusive create, the file removed again if the write fails — there is no rewrite step in it, and none is claimed here). Atomic replace is a *different* discipline and belongs to the spawner's own mutable records — `job.json` (`autoos_agent_mcp.py:117-121,404`) and the worker record (`autoos-agent.py:3042-3047`), §5.2 — precisely the thing a steer must **not** do: overwriting `steer.json` would erase a steer the run has not read yet, so a second steer is a second file, `steer-<n>.json`, on the `qa-<n>.json` history precedent (`autoos-ask.py:110-143`, inference). The run is told **at its next park** — the UI shows the steer as `queued`, never as delivered | inference; honest about the refusal r2 records. Delivery into a *running* process would need a channel the spawner does not have (§11, §12) |
| claude-session | any | no programmatic message API exists in anything r1/r2/r3 observed — `answer` for a hub/A2A task is absent upstream (r3 open). P3 ships **copy-text + Remote Control deep-link** (`remote_control_url`, §8.2 `Chat`): the console hands the operator the steer text and opens the session, so the steer is logged and acted on by a human, not silently dropped | r3 §1/§2 (no `answer`/`steer` action), `autoos-agent.py:1451` |
| external agent | any | logged only, marked `undeliverable`; the console never claims a steer arrived | §4.1 `events` |

**A `working` run that never parks does not receive a mid-flight steer in P3.** That is a stated limitation, not an implied capability — carried as a risk (§11) and as an open question for the operator/router (§12).

### 7.2 MCP tools (any model orchestrates; scopes `read`/`write`/`admin`; names checked against `RESERVED_MCP_NAMES` at build)

| tool | args | scope |
|---|---|---|
| `agents_list` | `project?,state?,host?,since?` | read |
| `agent_get` | `id` | read |
| `agent_register` / `agent_update` | agent fields / state | write |
| `task_create` / `task_update` / `task_list` | task fields (A5: callable by agents) | write / read |
| `question_ask` | `agent_id,task_id?,text,options?,default_option?,deadline?` (blocking-aware — parks the run on `input_required`, `autoos-ask.py`) | write |
| `question_answer` | `id,answer` | write |
| `approval_request` | `agent_id,risk_class,action,detail` | write |
| `steer_send` | `id,text` (always logged as an `events` row; delivery follows the per-kind channel table, §7.1) | admin (operator-gated) |
| `spawn` | proxies to the AutoOS spawner's `route`/`spawn` so the console is the single back-end | write |
| `provenance_get` | `gen_id` | read |
| `memory_feed` | `project?,domain?,cursor?` (`memory.<kind>.*` + `memory.edge.*`, §4.3) | read |
| `memory_health` | `project?` (passes the facade's `memory_health()` through, §8.6) | read |
| `knowledge_link` | `from_kind,from_id,entity_ref,relation` — the MCP face of `POST /api/knowledge-links` (§7.1): records a console-row → graph-entity ref with `entity_ref` kept opaque. Unlinking is a REST `DELETE` by link id, so no agent tool can sweep rows it does not own | write |
| `projects_list` | `state?` | read |
| `project_get` | `id` → overview: state, goal, plan phases, open questions, running agents, last change, spend | read |
| `publish_report` | `project, title, html?\|md?` (exactly one body; new `report_versions` row, `reports.gen_id` recorded) | write |

AutoOS side (`autoos-agent` CLI/MCP) **keeps working offline**; it gains a `--console` URL/key (`api-keys.yml`) and becomes a thin client of this API only when set (outline §7). No new top-level OmniRoute dependency; MCP tool names must not collide with the shipped `pool_*` set (r1 §9).

### 7.3 Event channel

fleetd publishes one append-only stream carrying both the orchestration events and the memory envelope, delivered two ways — **two different endpoints, not one contract**: (a) **the console's own** SSE, `GET /api/agents/stream?last_event_id=` (§7.1), consumed by the console UI; (b) republished onto OmniRoute's existing event bus so the canvas refetches (`omniroute:src/lib/events/eventBus.ts:1-16`; the live WS is a separate loopback process, `omniroute:src/server/ws/liveServer.ts:50-54`, channels `requests|combo|credentials` — fleetd adds nothing to that list, it feeds the canvas's existing `agents` trigger, `useOrchestrationSnapshot.ts:178-188`). The **hub-API** SSE contract is a third thing and is *inbound-facing to OmniRoute*: `GET /v1/events?last_event_id=` (§5.5), consumed by the Conductor bridge, not by the browser. Envelope (matches the `events` table §4.1):

```
{ id: "<ULID>", at: 1759030200, type: "agent.steer|agent.state|question.asked|
                question.answered|approval.requested|
                memory.<entity_kind>.<created|updated|merged|superseded|redirected>|
                memory.edge.<linked|unlinked>",
  agent_id: "20260928-…-a1b2c3", actor: "operator|agent:<id>|system",
  payload: {…} }
```
`memory.*` payloads carry the §4.3 envelope (`schema: 2`) unchanged inside `payload` — entity/edge id and `version_id` only, **no body**; note the two `at` formats are deliberate: the fleetd envelope's `at` is an INTEGER epoch (§4.1), the memory event's is ISO UTC (§4.3). Delivery is **at-least-once, per-subscriber cursor** — same discipline the Conductor bridge persists in `key_value` (`omniroute:src/lib/conductor/bridge.ts:177-250`), so a reconnect replays from `last_event_id` and the console dedups on the event ULID. **Replay is bounded by retention** (§4.7): a cursor older than the trimmed window cannot be satisfied, so fleetd answers it with `410`-equivalent semantics — a `resync` frame carrying the current snapshot (§7.1 `GET /api/agents`) and the new head id — and the subscriber restarts from there rather than receiving a silently short log. A gap is therefore always either replayed or resynced; it is never a partial history presented as complete. Producers map to the state machine: a `spawn` emits `agent.state submitted→working`; `respond`/`answer.json` emits `question.answered` and flips `input_required→working`; the `question.asked` / `approval.requested` rows are the *same* write the ntfy publisher keys on (§7.4) — one row, both consumers, so a push and the board can never disagree about what was asked; a steer emits `agent.steer` (the parent reads it, G5); a published report emits a `report.published` row so the project's Reports tab refreshes live (D-043, inference: this console-side type is not in the answers' event list).

### 7.4 Push to the phone — ntfy (P3)

routing-00 chose **(a) ntfy** for the needs-you push (§12) and P3's acceptance promises a message on `input_required` (§10) — but a channel with no named publisher, no configuration home and no message shape is not an integration point. This section is that, in the same parts every other §7 surface has.

| part | the design |
|---|---|
| **publisher** | **fleetd**, and only fleetd. The trigger is the transition **into** `input_required` (§4.4) that produced the `question.asked` / `approval.requested` `events` row (§7.3): the same write enqueues the push, so "was the operator told" has one answer in one log. The spawner and the node agent never speak to ntfy (§5.2/§5.4) — one publisher, no second path that could double-send |
| **configuration** | fleetd's own **git-ignored config** (AGENTS.md §1 rule 1): the ntfy **base URL**, **one topic per project**, and the **access token** that authorizes the publish. None of the three values appears in this document, in a tracked file, or in an `events` payload — a topic is named here as a *shape* only. **Scope is per project**, which is how the board is already grouped (§8.1); a project with no topic configured is simply never pushed for, and its questions still render on the board |
| **message** | `title`: `<project> · <agent title>`; `body`: the **question text capped at 200 chars** (ellipsised, never wrapped onto a second screen — the push exists to say *you are needed*; the full text is answered on the board or from the drawer). Plus, via ntfy's HTTP action-button mechanism, **one button per `questions.options` entry** (§4.1) with the `default_option` first, and for an approval a pair: `Approve` / `Deny` — the same two taps §8.1's card renders as `[a][b][c]` and `[no][yes]` |
| **the buttons' credential** | Each button `POST`s to fleetd's own `POST /api/questions/:id/answer` or `POST /api/approvals/:id/decide` (§7.1) carrying a **signed action token** instead of a session: **HMAC-SHA256 over `(target_kind, target_id, expiry)`** — the same scheme, and the same server-secret family, as the report view URL (§7.1/§9). Two deliberate differences from that URL: the TTL is **minutes, not 60 s** (a push is read on a phone and answered later; inference: default **15 min**, configurable — and a token whose target was already answered is refused at check time, so the window is a ceiling, not a promise of freshness), and **single use is mandatory, not optional** (the report URL guards a read; this performs a **write**, and a re-tap must not decide twice). The token names exactly one row, so a captured button URL cannot answer another question, cannot reach `steer`/`stop`/`resume`, and dies with its target |
| **auth semantics** | The token-authed form of those two endpoints carries **no cookie and no `Authorization` session** — the push client is not a browser, so there is no ambient credential for a page to forge against; that is what makes §9's CSRF/origin check (which guards the *session* form of the same endpoints) irrelevant to this path rather than bypassed by it. The request is a `POST` for the same reason §7.1's `decide` is POST-only: a prefetched or replayed URL must not be able to act. A tap is audited as an `events` row (`actor="operator"`, the channel named in `payload`) and the question's `channel` column (§4.1) records `ntfy` as where the ask was surfaced |
| **at-most-one-send, and failure** | Publish is an outbox write first (§5.2), so an ntfy outage can neither lose the ask nor block the run — the run parks, the board shows it, the retry follows. The retry does **not** double-push: the publisher dedups on the question/approval **id**, which is what lets P3 assert *exactly one* message. A refused token (expired, already used, wrong target) is a visible failure — fleetd's own response for the button — never a silent drop; the ask stays on the board and the `events` row says the tap happened |
| **what leaves the host** | 200 characters of one question, plus a project name and an agent title. §9's privacy rule otherwise keeps task text on the host (`task_head ≤120`, counts + offsets), so this is an **explicit per-project carve-out**, named in §11's privacy row — the alternative is a push that says only "something needs you" with nothing to act on, which is precisely the outcome routing-00 chose ntfy to avoid |

**P3 acceptance probe (§10).** Ask a test question from a real agent in a project with a configured topic: **exactly one** ntfy message arrives, its body is that question truncated at 200 chars, and it carries one button per option. **Tapping a button answers it** — the `questions` row is answered, `answer.json` is written, the run leaves `input_required` (§7.1) — and the same token pointed at another question id is refused, as is a second use of it. The same question in a project with **no** topic configured produces no message and an unchanged board. A push that never arrived, or arrived twice, or answered nothing, fails the phase.

## 8. Mobile UX (ASCII sketches, ~40 cols)

Views 1–5 are the hub-and-module core; 6–9 are the A2/A4 additions (after P3); 10–11 are the D-043 project spaces.

### 8.1 Board (G1/G2) — project tabs + swipeable lanes

```
+--------------------------------------+
| AutoOS ▾   [Running|You|Wait|Done|Fail]
|  o t3-rev  Sonnet1M  2m  $0.42  [!]  |
|  o t2-wrk  qwen      8m  ?      [--] |
|  o orch     t1         1h  $3.10 [Q1] |
|  ● claude-s  Sonnet   3h  $11.20     |
|                                      |
|  needs you (2)                       |
|  ? FLEETSPEC push ok?   [a][b][c]    |
|  ! approve rm -rf?      [no][yes]    |
+--------------------------------------+
```
Card = title, client/model, elapsed, cost, badge (`[!]` approval, `[Q1]` question). Cost is `?` where the client is non-gateway — a card never shows `$0.00` for *unknown*, because $0.00 reads as "free" and a stale board reads as an idle fleet (§5.3, §8.5). Tap → agent detail (8.2).

**Stale and loading states** (fleetd unreachable, or a host's node agent down — the normal phone condition on a flaky link, so it is a first-class state, not an error page):

```
| AutoOS ▾  [stale · 4m]  [↻]         |
| ⚠ fleetd unreachable — last snapshot|
| ── cached, actions disabled ──      |
| ⚠ edge2 · no report · 22m (host?)   |
```

The board renders the **last snapshot with a `stale · <age>` pill and a retry affordance**, derived from `hosts.last_seen` (§4.1) and the stream's own liveness, and it distinguishes *fleetd is down* (nothing resolves) from *one host stopped reporting* (that host's agents go `unknown`, §5.4, others stay live). **Actions are disabled while stale** — steer/stop/resume/answer/approve against data that may be minutes old is exactly the silent wrong action §9's whole design is built to prevent; the drawer explains why. Initial load is the same pill with a skeleton rather than an empty board, so "no agents" is never ambiguous with "not yet loaded".

### 8.2 Agent detail (G5, A3) — header + actions + timeline + usage

```
+--------------------------------------+
| < t2-wrk · working · host:edge2      |
| Sonnet1M  t2  parent: o-orch ▸       |
| ctx 148k/200k 74%   tokens ▁▂▅█     |
| [Answer][Steer][Stop][Resume][Chat]  |
| ── events ──                         |
| 10:22 spawn  reason=cheap           |
| 10:31 question "branch?" [asked]    |
| 10:40 steer  "use lane X" op        |
| 11:02 ▶ running  ($0.08/last)      |
+--------------------------------------+
```
`Chat` = Claude Remote Control deep-link (`remote_control_url`); no second chat surface. `ctx …/%` = state card + context fill (A3, `autoos-agent.py context`) — the card from `agents.state_card`, the figure from the newest `heartbeats` row and the `▁▂▅█` sparkline from the rows before it (§4.1/§4.6); a non-Claude client has the context columns NULL and renders `unknown`, never a fake 0%. `Steer`/`Answer` act on the per-kind channel (§7.1): a parked run takes `answer.json`, a *working* run shows the steer as `queued`, a Claude session offers copy-text + `Chat`, an external agent shows `undeliverable` — the row never renders a delivery the console cannot make (§11 "Steer does not arrive").

### 8.3 Spawn tree (G3) — FlowCanvas, node=agent, edge label=reason

```
+--------------------------------------+
|          o-orch  [done $3.10]        |
|        /            \                |
|  "split"          "review"           |
|  /                  \                |
| t2-a[work]        t2-b[work]         |
|     \                |               |
|   "gave up"     "found bug"          |
|        \            /                |
|          t3-rev [you!]              |
+--------------------------------------+
```
Reuses `omniroute:src/shared/components/flow/FlowCanvas.tsx`; edges are `spawn_edges` with `reason_text` (the canvas today draws only `owns`/`mirror` — `spawned` kind is the B-module addition, r3).

### 8.4 Tasks kanban (G1, A5) — drag + one-tap answer

```
+--------------------------------------+
| backlog  next    doing   review done|
| ▢ auth   ▢ cost  ▢ board ▢ spec  ✓  |
| ▢ i18n   ▢ tree  ▢ steer            |
|                                    |
| ▢ cost  ← by agent:t2-a  [Q: 2]    |
|   [Yes][No]  [Open]                 |
+--------------------------------------+
```
dnd-kit board (multi-container, §6); task ↔ agent links (`tasks.links`); question list carries one-tap answer.

### 8.5 Costs (G4) — reuse UsageAnalytics

```
+--------------------------------------+
| Today | 7d | 30d        [proj][model]
| AutoOS      $14.72  ▁▂▄▆█▇▅         |
|  ├ orch      $3.10                  |
|  ├ t2-a      $0.42                  |
|  ├ claude-s  $11.20                 |
|  └ non-gw     ? (unknown, §5.3)    |
+--------------------------------------+
```
Backed by `omniroute:src/shared/components/UsageAnalytics.tsx` (presets 1d/7d/30d, r1 §5). A `?` bucket is *unpriced*, not free: non-gateway clients have no run-id and therefore no cost row at all (§5.3), so the rollup counts them separately rather than summing them into `$0.00`.

### 8.6 Memory health (A2) — graph hygiene + curator merges

```
+--------------------------------------+
| Memory health · AutoOS               |
| nodes 1204  edges 3310              |
| dup rate   6.2%  ▇▃▂                |
| orphan     11%   ▃▄▆                |
| hops→hub   2.4 avg                  |
| ── curator merges (undo) ──         |
| lesson:PROVPIN ⟶ D-011   [undo]     |
| dup node#88  ⟶ node#12   [undo]     |
+--------------------------------------+
```
Metrics come from the facade as `memory_health()` (§4.3, surfaced by `GET /api/memory/health`) — fleetd computes none of them. Curator merges are `memory.<kind>.merged` events and their inverse-split undo (§4.3), surfaced as actions, never copied as bodies.

### 8.7 Hub neighbourhood (A2) — project's graph around its hub, @xyflow

```
+--------------------------------------+
| AutoOS ▸ hub-neighbourhood           |
|        [D-011 memory]                |
|       /      |      \                |
| [rule:scrub] [spec]  [task:cost]     |
|       \       |      /               |
|        [agent:t2-a]                  |
|      hop:1 ●  hop:2 ○  hop:3 ·       |
+--------------------------------------+
```
`@xyflow` FlowCanvas again; nodes are `knowledge_links.entity_ref` stable ids expanded one-hop **through fleetd** — `GET /api/memory/neighbours` (§7.1), which proxies the memory facade and returns ids/titles/relations only. That is **the same boundary the feed and health reads use** (§4.3, §8.6): the browser holds no graph credential and never calls the graph API. The set is **maintained from the `memory.edge.<linked|unlinked>` events** (§4.3) on the same cursor the feed uses — the proxied read fills titles, the events keep it fresh without re-polling the graph. Links are created by `POST /api/knowledge-links` or MCP `knowledge_link` (§7.1/§7.2); nothing here is writable from this view.

### 8.8 Lineage / provenance (A4) — generations + restart reasons

```
+--------------------------------------+
| t2-a · lineage (generations)         |
| g-01 spawn   pack:ab12 12 items     |
|   │ reason: "split"                 |
| g-04 restart pack:cd90 14 items ▸diff|
|   │ reason: ctx 82% (RESTART)        |
| g-07 ready   out: D-042 ◂uses g-04   |
| transcript: file…offset 214-233 ▸   |
+--------------------------------------+
```
Rows = `generations`; `▸diff` compares two `pack_hash`es; `◂uses` = `output_links.gen_id`; transcript link by file+offset (`transcript_refs`).

### 8.9 "Why this decision?" (A4) — trace

```
+--------------------------------------+
| D-042 ← g-04 ← pack cd90             |
| cards: [AGENTS.md][lane rule:scrub] |
| facts: [MEMSPEC node#12 v3]         |
| events:[steer "use lane X" op]      |
| sources: r3, answers A4, r2§7        |
| authors: fleet-r3.out, operator     |
| [replay pack]  [open transcript]     |
+--------------------------------------+
```
`decision → gen_id → pack → manifest_items → sources/authors` (§4.5). `replay pack` rebuilds exact text from `pack_blobs` + versioned `manifest_items`.

### 8.10 Portfolio home (G6, D-043) — one card per project

```
+--------------------------------------+
| Projects        [active|paused|arch] |
|                                      |
| AutoOS              ● active         |
|  Q 3 · agents 2 run · $14.72 this wk |
|  last change: merge a1b2c3d 2h ago   |
|                                      |
| fleetspec           ● active         |
|  Q 1 · agents 0 run · $3.10 this wk  |
|  last change: spec pass-1 20m ago    |
|                                      |
| omnigraph           ⏸ paused         |
|  "handoff: wait for cluster apply"   |
|  last change: merge 4e5f607 3d ago   |
+--------------------------------------+
```

Exactly the five facts the operator asked for per card (answers D-043): **state pill** (`projects.state`), **open-questions count** (`questions` where `answered_at IS NULL`), **running agents** (`agents` in `working`/`input_required` for that project), **last change** — `changelog_entries` head from **P4**, read **straight from `git log main` at P3** when no changelog table exists yet (§5.7) — and **spend this week** (`run_usage` windowed by `project`, G4). A card taps through to the project page (8.11); a **paused** card shows its handoff note as its only body line, because that is what the next session needs to read first (§8.11 Overview, §5.7 import).

### 8.11 Project page (G6, D-043) — seven tabs, one per kind of answer

```
+--------------------------------------+
| AutoOS ▾ [Ovr|Chg|Q|Dec|Rpt|Kno|Agt] |
| ● active · goal: one machine → fleet |
| plan  P0✓ P1✓ P2• P3 P4 P5           |
| running 2 · needs you 1 · waiting 0  |
| last merges: a1b2c3d f1e2d3c         |
| ── open questions (3) ──             |
| ? ship hub for P1?  [a][b] dfl:a ⏰3d |
| ? run_id as fix PR? [a][b] dfl:a     |
| ── recent decisions ──               |
| kanban: built-in  ←Q4 ←g-02 adr:0007 |
| D-043 project pages   who: operator  |
+--------------------------------------+
```

| tab | shows | source |
|---|---|---|
| **Overview** | state, goal, progress vs plan phases, running / needs-you / waiting counts, last merges | registry + `fleet` events + git; **last merges reads `git log main` directly at P3** (no table — `changelog_entries` does not exist until P4, §5.7) and becomes a `changelog_entries` read at P4. A **paused** project renders `handoff_note_ref` *as* the Overview |
| **Changelog** | repo `CHANGELOG.md`/fragments + merges on `main`, each linked to its commit, PR, and the authoring agent | `changelog_entries` (**P4** — the tab is not offered before its importer runs) |
| **Questions** | open (options / default / deadline / what it blocks) then answered; **answerable in place** | `questions` (`POST …/answer`, §7.1) |
| **Decisions** | why, sources, who, superseded-by, the ADR it was written down as, and the generation/context it was made under (D-042 → 8.9) | `decisions` + `docs/decisions/` |
| **Reports** | agent-published HTML/markdown as cards, version history, external artifact links may be listed | `reports`/`report_versions` (**P4**, with the publisher + blob store), rendered **sandboxed** through a 60-second signed view URL minted by the parent (§7.1 `POST …/view-url`, §9) |
| **Knowledge** | the project's hub + clusters + recent facts | memory facade **proxied by fleetd** (§4.3, §7.1 `/api/memory/neighbours`, 8.6–8.7); links written via `/api/knowledge-links` (§7.1) |
| **Agents** | lineage, spawn tree, costs | 8.3 + 8.8 + 8.5 filtered by `project` |

## 9. Auth

- **Edge SSO + OmniRoute session, defense in depth**: console sits behind the edge proxy (Authelia-class, `<domain>`) *and* keeps OmniRoute's own `auth_token` session (`omniroute:src/app/api/auth/login/route.ts`, jose HS256 `{authenticated:true}`, httpOnly). The edge **must strip client `Authorization` before `forward_auth`** — the same lesson as the opencode path: OmniRoute carries peer-truth only in *signed internal* headers (`x-omniroute-peer-ip`, `|1` via-proxy downgrade, `omniroute:src/server/authz/peerStamp.ts`) and has **no `x-forwarded-user` external-identity handling** (r1 §3/:46), so a client-forged header reaching the proxy is exactly the failure mode to avoid.
- **Per-host narrow keys**: node agents and spawners use a per-host API key scoped `agents:write` (create/update agents + outbox posts + their own knowledge-link rows, §7.1) — **never the `manage` key** (`omniroute:src/shared/constants/managementScopes.ts`; keys are `sha256`-hashed with 12-char prefix, r1 §3). A `manage`-scoped key is what fleetd itself uses to read OmniRoute's `GET /api/usage/by-run` (§5.3) — a server-to-server call, never handed to a host.
- **Operator-only actions**: `steer` / `stop` / `resume` / `approve` / `answer` require an operator dashboard session, not a node key (outline §9; `requireManagementAuth`) — as does minting a report **view URL** (§7.1/§9). CSRF/origin check on management mutations already applies (`omniroute:src/server/authz/pipeline.ts:437-451`). **One carve-out, stated narrowly**: the ntfy action buttons (§7.4) present a **signed, single-use, expiring action token** that fleetd minted for exactly one question or one approval. It stands in for the session for *that one write and nothing else* — it cannot list agents, cannot steer, cannot stop, cannot mint a report URL, and gives the phone nothing reusable elsewhere. The CSRF/origin check keeps guarding the *session* form of those endpoints; the token form has no ambient credential for a page to forge, which is why the same path can be both delegated and un-CSRF-able (§7.4).
- **Audit**: every action → an `events` row (append-only, §4.1); MCP calls audited to `mcp_tool_audit` (`omniroute:open-sse/mcp-server/audit.ts`).
- **Agent-authored HTML is untrusted content (D-043)**: reports are *written by agents*, so the console treats a report the way a browser treats a stranger's page — Claude-artifacts-style rendering, **on a render path that carries no session**. The report body renders in an `<iframe>` with `sandbox="allow-scripts"` and **`allow-same-origin` deliberately absent** (so the document gets an opaque origin: no console DOM, no shared `localStorage`/cookies, no same-origin request that could carry the session); `allow-top-navigation`, `allow-popups` and `allow-forms` are **never** granted, so a report cannot move the operator off the dashboard or exfiltrate by form POST. **The frame's `src` is a signed view URL**, minted by the parent page — which *does* hold the operator session — via `POST /api/reports/:id/versions/:n/view-url` (auth `manage`, §7.1): **HMAC-SHA256 over `(report_id, version, expiry)`** with a server secret, **60 s expiry**, single-use optional. That is the whole auth mechanism for the frame: a cookie-less cross-origin document cannot authenticate, so the capability travels in the URL and the session never does. The blob is served by a **separate report origin**, a dedicated sub-path or host the operator configures in fleetd's own git-ignored config (named there, never in this document); **that origin serves the body only against a valid, unexpired signature for exactly the row named, sets `Content-Security-Policy: sandbox` on the response, and sets and reads no cookies**. Alongside `sandbox`, the response carries the **strict CSP** a static report needs — `default-src 'none'`, plus only what rendering requires (inference: `style-src 'unsafe-inline'` for its own styling, `img-src data:` for embedded charts). Where a deployment can only offer the report origin as a path on the console's own host rather than a distinct one, the opaque-origin sandbox + `CSP: sandbox` + the signature are the load-bearing controls: a report that reads an `auth_token` still has no origin allowed to send it, and a URL that leaks stops working within 60 s.
- **Report response + markdown**: the render request carries **no session cookie and no auth header** — the signature is its only credential — the response sets no cookie and is `Cache-Control: no-store` (a shared cache must not hold a signed body), links inside a report are `rel="noopener noreferrer"`, and markdown is sanitized server-side before it reaches the iframe, by **an allow-list sanitizer** (tags/attributes/URL-schemes enumerated, everything else dropped — not a deny-list, and not "strip `<script>`"). The dependency is **chosen at P4 under OmniRoute's `chore/` dependency gate** (r1 §10: dependency work is `chore/`-prefixed and "dependency, license, workflow, and package policies apply" — `check:lockfile`, `check:licenses`, `check:bundle-size`, `check:deps`), or hand-rolled against a fixture corpus if the gate rejects an added dep; either way the sanitizer's own tests are the XSS acceptance (§11), not a claimed property of a library.
- **Privacy**: transcript bodies and task text stay on the host; only token counts, file+offset refs, and short `task_head` leave (r2 §7, §5.4). No hostname/IP/username leaves the host in a tracked artifact — fleetd config carries host ids as opaque labels. The **push is the one place a question's own text leaves**: ≤200 characters to that project's topic, opt-in per project, and never an answer, a transcript excerpt or a path (§7.4). The ntfy base URL, its topic names and its token live only in fleetd's **git-ignored config** — the same file that names the report origin (§9 above, §7.1) and for the same reason: this repository is public, and a topic name is a credential, not a label. The credential fleetd sends is the write-only one, so a push compromise cannot read the topic's history back out of fleetd.

Who may call what:

| caller | credential | may | may not |
|---|---|---|---|
| operator (phone/desktop) | `auth_token` session (§9) | all of §7, incl. steer/stop/resume/answer/approve | — |
| node agent | per-host key `agents:write` | `POST/PATCH /api/agents`, outbox posts, its own token-count rows (§5.4), `POST/DELETE /api/knowledge-links` for the refs it owns (§7.1) | steer/stop/answer/approve; `manage` scopes — so it **cannot mint a report view URL** (§7.1) |
| spawner (`autoos-agent`) | per-host key `agents:write` + optional `tasks:write` | register/update, `spawn_edge`, `question`/`answer` events, `task_create`, knowledge-link writes | read all projects (own project only, inference: scope key per project) |
| any model via MCP | scoped MCP key (`read`/`write`/`admin`) | tools per §7.2, incl. `publish_report`, `projects_list`, `project_get`, `knowledge_link` | `steer_send` without `admin`; publishing into a **project its key is not scoped to** (inference: `reports:write` is project-scoped like `tasks:write`) |
| report viewer (the iframe document) | none — the parent minted a signed URL naming one report version (§7.1/§9) | read that one body, for ≤60 s, from the report origin | cookies, `localStorage`, the console DOM, same-origin requests, anything on the parent origin; any *other* report version (the signature covers `report_id`+`version`+`expiry`) |
| ntfy action button (the operator's own phone, one tap) | signed **action token** naming one target row (§7.4) | answer **that one** question / decide **that one** approval — inside its window, once, as a `POST` | a second use of the token; another target (the signature covers `target_kind`+`target_id`+`expiry`); `steer`/`stop`/`resume`/`report view-url` mint; any read beyond the row it acts on; a cookie or session, which it never carries |
| OmniRoute (server) → hub (fleetd) | `CONDUCTOR_HUB_TOKEN` | `/v1/*` read + task create/cancel | — (the hub is reachable **only** from OmniRoute's server-side proxy; `hubProxy.ts` whitelists the shapes and the browser never gets a hub credential or a hub route) |

## 10. Phased plan + data ladder (ADDENDUM 2 replaces the outline ladder)

| phase | scope | data ladder rung | measurable acceptance |
|---|---|---|---|
| **P0** AutoOS only, no UI | canonical run id + parent edge + host + full `route_plan` persisted in spawner records; `X-AutoOS-Run-Id` header wired (§5.1/§5.3) | **rung 0: lineage exists** | `autoos-agent ps --tree` renders a parent→child tree with reason; a worker row and its `logs/agents/<id>` share one id |
| **P1** read-only board (fleetd, **no OmniRoute change**) | **two verifications first, both cheap and both block the design**: `claude agents --json` exists and emits `state`/`name`/`cwd` (§5.4), and whether `getConductorTaskDetail` calls a hub `GET /v1/tasks/:id` at all (§5.5). Then: fleetd with OmniConductor-compatible hub API (§5.5, **incl. the OASF capabilities `fleetSkills` reads**) + Python node agent (§5.4); AutoOS agents appear in the *existing* Conductor panel + canvas via `CONDUCTOR_HUB_URL→fleetd`; `route_plan` ingested from the P0 spawner records into `spawn_edges.resolver` (§5.1→§5.2); the `agents` table is created by this phase's migration **with** `waiting_reason`/`resume_at` in it, because the §4.4 `waiting` state and §7.1's `resume` need them from day one (§4.1) | **rung 1: states + lineage** | a spawner run shows in the canvas within 5s (poll) with correct normalized state (a `waiting` run showing as `working` is the documented hub limitation, §5.5); contract tests green against `hubProxy.ts` shapes **and** against `fleetSkills` capability parsing |
| **P2** cost | **verify the ledger join first** — `request_cost_ledger.request_id = call_logs.id`, unverified (§5.3); if it does not hold, add the ledger-side join in the same migration. Then `call_logs.run_id` (+heal list+index) **and the `GET /api/usage/by-run?run_id=` endpoint** ship together (§5.3); fleetd fills its `run_usage` **cache table** from that endpoint and costs surface | **rung 2: cost per run** | `GET /api/usage?group_by=agent` returns non-zero `cost_usd` for a gateway run within one retention window — which *is* the join verification, and it is proven **through the endpoint** (no fleetd-side SQL touches OmniRoute's file); non-gateway = "unknown" (`?`, not `$0.00`, §8.1) |
| **P3** actions + project space (B-module) | canvas source `fleet` + `spawned` edges + board tab + answer/steer/approve drawer + ntfy push for "needs you" (**the push channel is specified in §7.4**: fleetd's own publisher, one topic per project from fleetd's git-ignored config, action buttons that answer through §7.1); state card + context fill on detail (A3) — `agents.state_card` and the `heartbeats` table land with **this phase's** migration (§4.1/§4.6/§4.7); **project pages open (D-043): portfolio home 8.10 + a project's Overview / Questions / Decisions tabs (8.11)** — these read tables that exist by then (`projects`, `plan_phases`, `questions`, `decisions`, §5.7's P3 readers) plus the P2 cost rollup, and **`git log main` directly** for *last change* / *last merges* until P4's `changelog_entries` exists (§5.7) | **rung 3: act** | a steer from the phone writes an `events` row AND reaches its target on the per-kind channel or is shown as `queued`/`undeliverable` (§7.1) — never silently "delivered"; the §7.4 **ntfy probe**: a test question produces **exactly one** message on its project's topic and tapping its action button answers it (a re-tap, and the same token against another row, are refused), while the same question in a project with no topic configured produces none; the portfolio card's five facts (state, open Qs, running agents, last change, spend this week) match the stores / the merge log for a real project |
| **P4** tasks/questions authoritative + provenance + changelog/reports | kanban + questions/decisions as store (inbox files become exports); memory feed (§4.3); provenance tables + views 8.6–8.9 (A2/A4); **the `changelog_entries`/`reports` importers + publisher (§5.7) and the Changelog / Reports tabs (8.11)** — the questions/decisions/projects/plan readers they sit beside already landed at P3, and P4 replaces the P3 `git log` reads with the table; with the report publisher comes **the render path**: the `…/view-url` mint, the HMAC check and the report origin's `CSP: sandbox`, cookie-less serving (§7.1/§9); the **Knowledge** tab lands only once MEMSPEC serves the agreed §4.3 envelope and `memory_health()`, and its link rows are written through `POST /api/knowledge-links` / MCP `knowledge_link` (§7.1/§7.2) | **rung 4: durable** | a task created by `agent:<id>` and one by the operator both list with full response history (A5); "why this decision" traces a real decision to its pack; an imported `CHANGELOG.md` fragment **and** a merge on `main` both render with commit/PR/authoring agent, and the card's *last change* now agrees with the `git log` value it replaces; `publish_report` twice yields v1 beside v2, rendered in the §9 sandbox **from a signed URL** — and the same URL, replayed after 60 s or for a different version, is refused |
| **P5** upstream v4 module PR | split into reviewable PRs (DB+API, UI, MCP, i18n) per `omniroute:CONTRIBUTING.md`; target v4 `develop` SDK manifest | **rung 5: upstream** | each PR passes v4-feature + 68-locale new-key + coverage + test-policy gates (r1 §7–8) |

Provenance capture (tables) can start at P0/P1 (the data already exists post-RESTART, answers §A4); its **views land after P3 (P4)** per the brief. The project pages straddle the same ladder for the same reason (D-043), and the §5.7 importer is split to match: **Overview / Questions / Decisions at P3** with the `projects`/`plan_phases`/`questions`/`decisions` readers — those rows exist as soon as the questions/decisions store does; **Changelog / Reports at P4** with the `changelog_entries`/`reports` readers and the blob store — the two changelog facts P3 wants (*last change* on the card, *last merges* on the Overview) are served from `git log main` directly in the meantime, so no P3 cell points at a table that has no importer yet (§5.7); **Knowledge after MEMSPEC** — it is a read of the external facade (§4.3), so it cannot ship before the facade serves the agreed envelope and `memory_health()`. Every phase: tests per OmniRoute gates (node:test + vitest + playwright; new `src/` file ⇒ new test, r1 §8).

Phase → upstream obligations (so the flag-off B-module stays upstreamable unchanged):

| phase | OmniRoute files it touches (r1) | obligation |
|---|---|---|
| P0/P1 | none (fleetd + AutoOS only) | hub contract tests only (§5.5) |
| P2 | `call_logs` migration + `schemaColumns.ts:324` heal/index **+ a new management read route** (`GET /api/usage/by-run`, §5.3) | fix-PR eligibility (section 12) — the column and the endpoint are **one** small upstream-able change, not two asks |
| P3 | orchestration canvas `OrchSource`/`OrchEdge` mappers + `orchestrationTypes.ts:8,45-51`, drawer `useDrawerDetail.ts:229-234`, sidebar items (`Agents`, `Projects` — D-043) + nav tests (`omniroute:src/shared/constants/sidebarVisibility/sections.ts:820-888`) | `v4-feature` label → `develop` (`ROADMAP.md:89-94`) |
| P3–P5 | `fleet`/`agents`/`projects` i18n namespace in **all 68** catalogs | `check:new-key-coverage` (`ci.yml:548`) |
| P5 | MCP `MCP_TOOLS` + `RESERVED_MCP_NAMES` + `countUniqueMcpTools` (`omniroute:open-sse/mcp-server/server.ts:804-815,111-124`) | OpenAPI + llm.txt + env docs same PR (`check:docs-sync`, r1 §8) |
| P5 | `changelog.d/features/<PR>-<slug>.md` fragment | `check:changelog-integrity` (r1 §10) |

## 11. Risks

| risk | exposure | mitigation |
|---|---|---|
| Upstream rejection / v4-feature gate | B-module never merges upstream (`omniroute:ROADMAP.md:42-43`) | ship as B (ours, flag-off); A-shaped so it upstreams *when* the channel opens |
| i18n burden | 68 locales, new-key CI gate demands translation in *every* locale (`omniroute:ci.yml:548`) | module ships with `agents` + `projects` (D-043) namespaces + `i18n:sync-ui`; upstream only at P5 |
| Conductor/canvas overlap | duplicate board next to existing Conductor panel (r1 §124) | P1 reuses the *existing* panel via the hub protocol; the module only adds the missing pieces (tree/kanban/cost/answer/steer) |
| Hub-protocol drift | fleetd must match a protocol from a **closed-source external hub** (r3 open, r1 open, line 146). Two known blind spots in the observed shapes: `waiting` has no hub name (§5.5) and the `/v1/runners` payload carries OASF capabilities that `FleetRunner` does not name but `fleetSkills` reads (§5.5) | contract tests pinned to `hubProxy.ts` shapes **plus** a `fleetSkills` capability test; treat stability as an open operator question (§12); P3's canvas source carries the states the hub cannot |
| Node-agent security | per-host daemon with write scope on the operator's hosts | narrow `agents:write` key only, loopback/or edge-only reach, no transcript bodies out (§9) |
| Claude CLI output drift | `claude agents --json` is **not evidenced** by r1/r2/r3 (its existence and its `state`/`name`/`cwd` shape are unverified), and its format can change anyway | verify before P1 (§10); tolerate unknown fields; degrade to `state="unknown"` while still listing the session from the transcript dir + process list (§5.4); alert on parse failure |
| **Steer does not arrive** (G5) | the spawner has no channel into a `working` process (`respond` refuses non-`input_required`, `autoos_agent_mcp.py:621-638`) and no message API is evidenced for a Claude session (§7.1), so a steer can be *logged* yet never *read* | per-kind channel table (§7.1) + the UI says `queued`/`undeliverable` and never "delivered"; the `events` row guarantees the parent's audit regardless; open question in §12 |
| `call_logs.run_id` gate | hot-table column add = migration + heal list + index, **and the `GET /api/usage/by-run` read route beside it** (§5.3) — both subject to the feature gate | file them as **one** small fix-PR question, not two (§12); cost can lag the board (P2 after P1), and until it lands `run_usage` is empty so every cost cell renders `?` rather than a guess (§8.1). The fallback is the endpoint being refused, **never** fleetd reading OmniRoute's SQLite (§5.3) |
| Privacy | transcripts / task text could leave the host | only counts + offsets + `task_head(≤120)` transit; §9 hard rule; no absolute user paths in any artifact — which is why `transcript_refs.file` is stored relative to the projects root / `$`-placeholder, never as the raw `$HOME/...` path (§4.5). Report bodies (D-043) are the one operator-requested exception: they are stored deliberately, reachable only through a management-authed mint of a 60-second signed URL, and sandboxed (§7.1/§9). The **push (§7.4) is the second**: ≤200 chars of one question to a per-project topic the operator configured, write-only credential, opt-in per project, and the action token it carries can address exactly one row |
| **Agent-authored HTML (XSS)** (D-043) | an agent — careless, prompted by what it read, or compromised — publishes markup that runs against the operator's dashboard session, on the origin that holds `auth_token` and every scope (§7.2) | the frame authenticates with **nothing but a signed, expiring view URL** (§7.1 `POST …/view-url`: HMAC-SHA256 over `report_id`+`version`+`expiry`, 60 s, optional single use), so the session never crosses into it; opaque-origin iframe (`sandbox="allow-scripts"`, never `allow-same-origin` / `allow-top-navigation` / `allow-popups` / `allow-forms`), `Content-Security-Policy: sandbox` plus strict CSP on the report origin's response, which **sets and reads no cookie, takes no auth header, and is `Cache-Control: no-store`**, separate report origin where the operator can configure one, allow-list server-side sanitizing for markdown (dep chosen at P4 under the `chore/` gate, §9). A report can therefore *display*, not *act* — and a leaked render URL is worthless in a minute |
| **Import drift** (D-043) | `CHANGELOG.md`, `changelog.d/` fragments, `docs/decisions/` ADRs or the router's `QUESTIONS.md`/`DECISIONS.md` change shape (a heading, a `project:` field, a date format) and the §5.7 reader silently stops matching — a project page that looks complete but is stale is worse than an empty one | per-source parsers tested against synthetic fixtures, not the live repos (AGENTS.md §5); unknown fields tolerated, parse failures counted; a failed parse raises a visible console alert + an `events` row rather than rendering an empty tab; the repo stays the source of truth, so a re-import after a fix is idempotent |

## 12. Decisions taken + open questions (routing-00)

`fleetspec-answers.md` records routing-00's answers. Written in the required question format with `answer: (x)` appended; a `Q: FLEETSPEC |` line with **no `answer:`** is a new open question needing the operator/router. The operator *additions* (A1–A5, D-042 "Context provenance", D-043 "Project pages") are direction, not questions, so they are not re-asked here — they are specified where they land (§4.3 memory envelope, §4.5 provenance, §4.6 state card, §5.7 + §8.10–8.11 project spaces). Four questions remain open below, the last one added by this review because the spawner has no evidenced channel into a *working* run (§7.1); the memory-envelope question closed on 2026-09-28.

```
Q: FLEETSPEC | home for the console UI — upstream-shaped feature shipped flag-off in-image, or a separate app | options: (a) upstream-shaped OmniRoute feature shipped first as flag-off add-on in the autoos/omniroute image (b) separate app | default: (a) because one website, auth+cost already there, no second DB | blocks: P1–P5 UI shape | reversible: no | answer: (a)

Q: FLEETSPEC | extend Conductor with a local data source, or a new "Agents" area | options: (a) extend Conductor if shapes fit (b) new area | default: (a) because r3 shows FleetRunner/FleetTask + the merging canvas already fit AutoOS; the module adds a 'fleet' OrchSource rather than a parallel namespace — this is L2-general's call, evidence in section 2.4 | blocks: P3 canvas source name | reversible: yes | answer: (a)

Q: FLEETSPEC | P1 delivery — OmniConductor hub protocol (fleetd) or a direct module | options: (a) hub protocol, zero OmniRoute change (b) direct module now | default: (a) because it shows AutoOS agents immediately via the existing Conductor/canvas and is not gated on upstream cadence | blocks: whether fleetd is a hub vs an API server | reversible: yes | answer: (a)

Q: FLEETSPEC | kanban | options: (a) built-in (b) Vikunja | default: (a) because same auth, native task↔run linking, @dnd-kit already a dep | blocks: P4 board | reversible: yes | answer: (a)

Q: FLEETSPEC | push channel for needs-you | options: (a) ntfy (b) web push | default: (a) because already in the router plan | blocks: P3 | reversible: yes | answer: (a) — the integration point this answer was missing is now specified: **§7.4** (fleetd publishes; ntfy base URL + one topic per project + the token live in fleetd's git-ignored config; the message carries the project, the agent title and the question's first 200 chars; its action buttons call §7.1's answer/decide with a short-lived signed action token). P3's acceptance is the exactly-one-message probe in §10

Q: FLEETSPEC | retention for agents | options: (a) archive done after 24h, keep 30d (b) other | default: (a) matches OmniRoute's 30d default | blocks: P0 policy | reversible: yes | answer: (a) — note A4 overrides: pack_blobs 90d, manifests/links forever; and D-043 overrides for project history: projects/plan_phases/changelog_entries/decisions and every report version are kept (§4.7)

Q: FLEETSPEC | open upstream contact before P1 | options: (a) after operator approves the spec (b) now | default: (a) because the public issue/discussion itself needs the operator's OK at that moment | blocks: P5 | reversible: yes | answer: (a)

Q: FLEETSPEC | node-agent language | options: (a) Python (b) Node | default: (a) like the spawner; reuses autoos_context.py token math | blocks: P1 node agent | reversible: yes | answer: (a)

Q: FLEETSPEC | knowledge-link entity ref | options: (a) stable id only (b) typed graph schema coupling | default: (a) because the shared memory graph is being redesigned by routing-00 — keep entity_ref opaque, no schema coupling | blocks: knowledge_links | reversible: yes | answer: (a)

Q: FLEETSPEC | memory-event envelope agreement with L1-backlog | options: (a) adopt the shape proposed in 4.3 (b) L1-backlog proposes a different one | default: (a) because the console feed depends on the envelope, so the P4 feed was blocked on agreement | blocks: memory feed + hub-neighbourhood view | reversible: yes (append-only store, schema is versioned) | answer: (a) — DECIDED, agreed with L1-backlog 2026-09-28 (schema: 2). 4.3 is rewritten to exactly that shape: verbs created|updated|merged|superseded|redirected with no 'deleted' (a prune is a superseded tombstone, undoable), version_id + prev_version_id, gen on every write, separate memory.edge.<linked|unlinked> events, merged ids answered with a redirect and undone by an inverse split event, no body/secret in any event, per-subscriber cursor, health from the facade's memory_health(). Facade spec: docs/plans/2026-09-28-memory-facade-spec.md (L1-backlog). Nothing in the console is blocked on this any more.
```

New open questions (not yet answered by routing-00):

```
Q: FLEETSPEC | is the OmniConductor hub protocol stable/public enough to implement against | options: (a) yes for P1, pin to hubProxy.ts shapes with contract tests (b) no, build a direct module now | default: (a) because the shapes are small, already zod-parsed per-field, and P1 is reversible; risk noted in section 11 (external, closed-source hub) | blocks: P1 fleetd SSE + /v1 contracts | reversible: yes

Q: FLEETSPEC | ship call_logs.run_id + GET /api/usage/by-run as an upstream fix PR | options: (a) yes, file them together as one change — a field addition to `call_logs` plus the small management read route that answers per-run cost (b) hold; join by sessionTag+time only | default: (a) because run-id-per-cost is G4 and the session_tag precedent (r1 §5) makes it a mechanical add, and because the endpoint is what keeps the join API-level so fleetd never opens OmniRoute's SQLite (§5.3); a `call_logs` column and a read handler are fixes, not features, so they should pass the 3.8.x gate | blocks: P2 cost-per-run | reversible: yes

Q: FLEETSPEC | multi-container dnd-kit board on the pinned @dnd-kit version | options: (a) verify DndContext droppables work, then build (b) fall back to single-column lists | default: (a) because @dnd-kit/core ships droppable already (no new dep); r1 §6 could not verify it on the pinned set | blocks: P4 kanban UX | reversible: yes

Q: FLEETSPEC | steer delivery into a run that is already working (G5) | options: (a) accept deferred delivery — a steer queues and arrives at the run's next park, UI says so (§7.1) (b) add a read channel to the spawner so a working process can pull a steer mid-flight (c) drop steer to a later phase | default: (a) because it is the only shape the evidenced spawner supports (respond refuses a working run, autoos_agent_mcp.py:621-638) and the events row keeps the parent's audit intact either way; (b) is a new AutoOS primitive, not a console feature | blocks: whether P3 may advertise steer for working runs and Claude sessions | reversible: yes
```
