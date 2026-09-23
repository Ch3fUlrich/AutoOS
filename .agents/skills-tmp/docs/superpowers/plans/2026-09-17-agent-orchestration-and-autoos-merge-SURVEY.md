# AutoOS × agent-skills merge survey — 2026-09-17

Read-only survey. **No file in any repository was modified**; no git state-changing command was run;
no MCP write tool and no Serena tool was called.

Conventions used below:
- **MEASURED** = I read it in the file / ran a read-only command this session, with the path and line.
- **INFERENCE** = my judgement, not evidenced by a file.
- Line numbers are from the files as they stood 2026-09-17.

Repos surveyed:
- `C:\Users\<you>\Documents\Code\agent-skills` (branch `main`, HEAD `1ef5c5c`)
- `C:\Users\<you>\Documents\Code\agent-skills-canvas` (worktree of the same repo, branch
  `experiment/agent-canvas-orchestration`, HEAD `eab7905`)
- `C:\Users\<you>\Documents\Code\AutoOS`

---

## A. Inventory table

| # | Component | Location | Purpose | Providers supported **today** | State | Evidence |
|---|---|---|---|---|---|---|
| A1 | **unattended-orchestration — batch lane** | `agent-skills\skills\unattended-orchestration\run_handoff_sessions.ps1` (1392 ln), `HandoffCore.psm1` (1496 ln), `AgentAdapters.psm1` (246 ln) | Headless overnight runner: worktree per session, lanes, recovery, guard-gated `--no-ff` merge under a mutex | `claude`, `agy` (stable); `grok`, `codewhale` (experimental, refused without `-IncludeExperimental`). Aliases: `antigravity`/`gemini`→`agy`, `deepseek`→`codewhale`, `claude-code`→`claude` | **Production** (2 real multi-day runs, 7 test suites) | `AgentAdapters.psm1:8-97` registry; `:99-104` aliases; `:130-141` experimental gate; `SKILL.md:411-419` test suites |
| A2 | **unattended-orchestration — config schema** | `handoff.config.example.json` (371 ln); live copy `agent-skills\handoff.config.json` (6185 B) | Single owner of every number: sessions, lanes, subagents, guards, timeouts, stall rules, CAO block | n/a | Production | `SKILL.md:67-97` ownership table; schema dumped in §B1 below |
| A3 | **unattended-orchestration — CAO lane (Python)** | `skills\unattended-orchestration\cao\` (21 files: `cli.py` 29 KB, `launch.py`, `verify.py`, `probe.py`, `budget.py`, `profiles.py`, `routing.py`, `monitor.py`, `watchdog.py`, `wire.py`, `worktree.py`, `leases.py`, `plan.py`, `dispatch.py`, `caoapi.py`, `config.py`) | Interactive 3-layer hierarchy over CAO 2.5.0 + tmux + dashboard `:9889` | Pools: `anthropic`→`claude`, `antigravity`→`agy`, `deepseek`→`opencode` | **Prototype proven once end-to-end** — "one phase went through it live" on 2026-09-11; six defects found in that single run | `SKILL.md:739-746` pool table; `SKILL.md:867-875` "Proven end to end, once, on purpose"; `handoff.config.example.json` → `cao.pools` |
| A4 | **CAO layer policy / model gate** | `cao\profiles.py:96-127` | Refuses cheap models at spawning levels; `Fable` is level-1-only | — | Production (enforced by `python -m cao check`) | `ORCHESTRATOR_MODELS` frozenset `profiles.py:96-98`; `LEVEL_1_ONLY_MODELS:101`; `orchestrator_model_warnings():105-127` |
| A5 | **CAO routing ladders** | `cao\routing.py`; config `cao.routing.{plan,decompose,research,implement,mechanical,test,verify,review}` | Task class → ordered candidate ladder, pool = quota bucket, family = blind spots | `FAMILY_ORDER = ("anthropic","google","deepseek","oss")` | Production within the prototype | `routing.py:19`; `routing.py:31-56` `ladder()`/`select()` |
| A6 | **CAO setup/installer** | `agent-skills\infra\mcp-servers\cao-setup\setup_cao.py` (15 KB), `setup-cao.sh` (10 KB), `patches\`, `profiles\`, `workflows\` | `--check` preflight for the CAO lane; detects native-vs-shim `agy` | — | Prototype; **`cao-setup\README.md` is 0 bytes** | `SKILL.md:685-697` trap table; `ls -la` shows `README.md` size 0 |
| A7 | **swarm-orchestration — policy** | `skills\swarm-orchestration\SKILL.md` (269 ln) | Roles (@architect/@engineer/@reviewer), decomposition, wave caps, model economics, handoff contract, Best-of-N | Asserted, not executed | **Policy only.** §16 says the enforcing `.claude/` adapter is **"planned"** | `SKILL.md:256-268` enforcement table; `:266-268` "Until the generated adapter lands, §5, §6 and §9 are architect discipline rather than mechanism" |
| A8 | **swarm-orchestration — Python scaffold** | `skills\swarm-orchestration\custom_orchestration\` (`orchestrator_scaffold.py`, `provider_executor.py`, `decision_engine.py`, `verification_runner.py`, `role_router.py`, `providers\*`) | Risk scoring, capability-aware payload shaping, verification parsing | Adapters registered: `claude_code`, `antigravity`, `codex`, `openhands`, `deepseek_tui`, `local_glm`, `ollama`. **Live mode only for** `codex`, `deepseek_tui`, `local_glm`, `ollama` | **Specified-not-built for the rest**: "the scaffold has been exercised in stub/mock mode only"; `providers/openhands.py` "is still a stub" | `providers\registry.py:16-24`; `README.md` Status blockquote; `README.md` "Running the example"; ADR 0005 header note |
| A9 | **swarm-orchestration — config** | `custom_orchestration\agent_orchestration.config.yaml` (395 ln) | Declared single source for thresholds, routing, tiers | — | **Partially missing**: `SKILL.md:25,120-121,134` point at `orchestration.wave_caps` and `orchestration.forbidden_concurrent_git`; **neither key exists in the YAML** (zero grep hits; they appear only in the *spec* and *plan* as "new") | `grep wave_caps` over the repo returns only `docs\superpowers\specs\2026-07-30-…:70,152,211` and `docs\superpowers\plans\2026-07-30-…` and `SKILL.md` — never the YAML |
| A10 | **herdr-orchestration** | `skills\herdr-orchestration\SKILL.md` (98 ln) — **file only, no code** | Drive the Herdr multiplexer socket API from inside a pane | Any CLI agent Herdr can install: "codex, cursor, copilot, droid, opencode, devin, kimi…" | Production **as documentation** (verified on Herdr 0.7.4); `herdr.exe` is installed on this host | `SKILL.md:56-70` command surface; `:97` "confirmed end-to-end on 2026-07-19"; `Get-Command herdr` → `C:\Users\<you>\AppData\Local\Programs\Herdr\bin\herdr.exe` |
| A11 | **mcp-servers-setup** | `skills\mcp-servers-setup\SKILL.md` (575 ln) — **file only, no code** | Wire Serena / Omnigraph / Graphify / Superpowers / Playwright / Context7; Harbor registry; worktree rules | n/a (MCP servers, not LLM providers). Ollama appears only as an **embeddings** dependency | Production | `SKILL.md:31-492` server sections; `:573` "Ollama (optional — embeddings only)" |
| A12 | **local-ai stack** | `agent-skills\infra\local-ai\` (`docker-compose.yml`, `litellm_config.yml`, `.env.example`, `perplexity-pipefunction.py`) | LiteLLM proxy — **Perplexity Sonar models only** | perplexity/sonar{,-pro,-reasoning-pro,-deep-research} | Production, but **unrelated to agent orchestration** — it is a research-model proxy, not a worker pool | `infra\local-ai\litellm_config.yml:4-30` |
| A13 | **secrets store** | `agent-skills\secrets\api_keys.conf` (+`.example`) | Provider keys for the CAO lane, read by `cao\config.py:find_secrets/load_secrets` | Keys present in the example: `deepseek`, `muse`, `openrouter` | Production | `secrets\api_keys.conf.example` (3 lines); `cao\config.py:20-54` |
| A14 | **AutoOS — installer core** | `AutoOS\setup.ps1` (23 KB), `setup.sh` (20 KB), `lib\windows\*.psm1` (9), `lib\linux\*.sh/py` | Provision a fresh machine: detect → profile → select → plan → confirm → execute → report | n/a | Production (Windows + Linux tested; macOS untested on real hardware) | `AutoOS\AGENTS.md:34-62`; `README.md` Status section |
| A15 | **AutoOS — app catalog** | `AutoOS\catalog\{windows,linux,macos}.json` | Data-only component catalog; 10 categories on Windows | n/a | Production | `catalog\windows.json` keys `['$schema','platform','profiles','categories','prompts','windhawk_mods']` |
| A16 | **AutoOS — profiles** | same catalog files → `profiles` map + per-component `profiles: [...]` array | Starting set of ticks. Windows has 6: `workstation`, `ai-coding`, `light`, `custom`, `everyday`, `rescue` | n/a | Production but **read-only**: readers exist (`catalog.sh:143 catalog_profile_defaults`, `:221 catalog_profile_list`, `:239 catalog_has_profile`, `AutoOS.Catalog.psm1:162-171`), **no writer anywhere** | grep for `profile` in `lib/` returns only readers + a validator (`catalog.sh:67-68`, `AutoOS.Catalog.psm1:65-67`) |
| A17 | **AutoOS — LLM model catalog** | `AutoOS\catalog\llm-models.json` (v `2026-09-14`) + `llm-models.schema.json` | **Single source of truth for every model AutoOS configures in OpenCode and OpenHands on every platform** — prices, contexts, `openrouter_id`, `direct.base_url` | deepseek (chat + reasoner), muse-spark, openrouter (~14 free models), ollama | **Production** | `llm-models.schema.json` `description` field; `llm-models.json:1-90` |
| A18 | **AutoOS — OpenHands LLM profiles** | `AutoOS\openhands\profiles\*.json` (20 files) | Complete OpenHands LLM profiles minus `api_key` | `deepseek-chat`, `deepseek-reasoner`, `muse-spark-1.3(-contributor)`, `ollama-qwen2.5-coder`, `openrouter-{free,nemotron-×4,ling-×3,inkling-×2,laguna-×2,nex-×2,north-mini-code,dots3}` | Production | `openhands\README.md`; `profiles\muse-spark-1.3.json`, `profiles\ollama-qwen-coder.json` |
| A19 | **AutoOS — OpenHands agent profiles (the 3-level hierarchy, already modelled)** | `AutoOS\openhands\agent-profiles\*.json` (11 files) | `orchestrator`, `orchestrator-free`, `suborchestrator`, `suborchestrator-free`, `worker`, `worker-free`, `worker-free-high`, `claude-opus`, `claude-sonnet`, `claude-haiku`, `agy-gemini-3.8-flash` | Each references an LLM profile by `llm_profile_ref` | **Production data, no runner** — AutoOS installs them, nothing in either repo dispatches through them | `agent-profiles\orchestrator.json` (`llm_profile_ref: "muse-spark-1.3"`, `enable_sub_agents: true`); `worker-free.json` (`llm_profile_ref: "openrouter-laguna"`, `enable_sub_agents: false`) |
| A20 | **AutoOS — OpenHands/OpenCode installers** | `lib\linux\install.sh:1130 setup_opencode_config`, `:1327 setup_openhands_config`; `lib\windows\AutoOS.Install.psm1:1065 Set-AutoOSOpenCodeConfig`, `:1274 Set-AutoOSOpenHandsConfig` | Write `~/.openhands/{settings.json,profiles,agent-profiles,automation}` and `~/.config/opencode/opencode.json`; overlay fresh catalog numbers; inject `api_key` from env/secrets; **symlink `agent-skills` into `~/.openhands/skills`** | — | Production | `install.sh:1336,1349-1352,1429,1571,1619-1637`; `AutoOS.Install.psm1:1284-1536` |
| A21 | **AutoOS — web GUI** | `AutoOS\web\index.html` (2532 ln, single file), served by `lib\linux\serve.py` / `lib\windows\AutoOS.Serve.psm1` | 4 tabs: Overview, Configure, Run & log, System | n/a | Production | `web\index.html:1124` `const TABS = ["overview","configure","run","system"]`; `:1126 showTab()`; `:1816 renderProfiles()` |
| A22 | **AutoOS — HTTP API** | `lib\linux\serve.py:314-436`; mirrored `AutoOS.Serve.psm1:425-504` | `GET /api/state, /api/ping, /api/log, /api/config, /api/claude/sessions`; `POST /api/claude/snapshot, /api/config, /api/install` | n/a | Production. **No profile endpoint of any kind** | `serve.py:325,331,336,352,371,382,389,413` |
| A23 | **AutoOS — TUI** | `lib\windows\AutoOS.Ui.psm1` (radio + checkbox pickers), `lib\linux\ui.sh` | Profile radio ("Choose profile") then per-component checkboxes | n/a | Production, **selection only — no editing** | `AutoOS.Ui.psm1:242-252` "Interactive single-choice radio selector for profiles or options"; `ui.sh` has zero `profile` hits |
| A24 | **AutoOS — claude-autostart** | `lib\windows\claude-sessions.ps1`, `lib\linux\claude-sessions.sh`+`claude_sessions.py`, systemd units | Snapshot/restore Claude Code sessions across reboots, into a *visible* terminal (herdr→tmux on Linux, Windows Terminal on Windows) | claude only | Production; three ADRs behind it | `AutoOS\docs\decisions\0001..0003`; `CHANGELOG.md` `[Unreleased]` §Fixed |
| A25 | **agent-skills-canvas worktree** | `C:\Users\<you>\Documents\Code\agent-skills-canvas` | 2 commits adding an `agent-canvas`(=OpenHands) launcher adapter + `muse`/`openrouter` CAO pools | adds `agent-canvas`, `muse`, `openrouter` | **Prototype, unverified flags** | see §C |

### Installed CLIs on this host (MEASURED via `Get-Command`, 2026-09-17)

| CLI | Present | Path |
|---|---|---|
| `claude` | yes | `C:\Users\<you>\.local\bin\claude.exe` |
| `agy` | yes | `C:\Users\<you>\AppData\Local\agy\bin\agy.exe` |
| `grok` | yes | `C:\Users\<you>\.grok\bin\grok.exe` |
| `codewhale` | yes | `C:\Users\<you>\AppData\Roaming\npm\codewhale.ps1` |
| `agent-canvas` | **yes** | `C:\Users\<you>\AppData\Roaming\npm\agent-canvas.ps1` |
| `cao` | yes | `C:\Users\<you>\.local\bin\cao.exe` |
| `herdr` | yes | `C:\Users\<you>\AppData\Local\Programs\Herdr\bin\herdr.exe` |
| `gemini` | **no** | — |
| `opencode` | **no** | — (the CAO `deepseek` pool reaches DeepSeek *via* `opencode`; the binary is absent on Windows — the lane runs in WSL) |
| `ollama` | **no** on Windows PATH | — |
| `openhands` / `openhands-cli` | no | — |

---

## B. Overlap and duplication matrix between the four skills

### B1 — who owns what

| Concern | unattended-orchestration (batch) | unattended-orchestration (CAO) | swarm-orchestration | herdr-orchestration | mcp-servers-setup |
|---|---|---|---|---|---|
| **Roles / layers** | Orchestrator → Session → Subagent, 3 layers, leaf rule (`SKILL.md:252-271`) | Layer 1 → supervisor → worker (leaf) (`SKILL.md:582-601`) | @architect → @engineer → @reviewer, leaf rule (`SKILL.md:35-74`) | none (panes only) | none |
| **Depth cap** | `subagents.maxDepth` (default 3), leaf rule, enforced per-launcher by `leafEnforcement` (`--disallowedTools Agent`, `--no-subagents`) | `cao.maxDepth`; **policed reactively** by `cao\monitor.py` walking the server-stamped `caller_id` chain, *not* enforced (`SKILL.md:651-681`) | `roles.<r>.can_spawn_subagents` in YAML, enforced by nothing | n/a | n/a |
| **Wave caps / concurrency** | `subagents.maxConcurrent` (example: 2); `machineBudget.maxWorkersPerSession` (8) | not expressed | `orchestration.wave_caps.{context_sharing,isolated_candidate}` — **the key does not exist in the YAML** | "keep the fleet small and deliberate" (`SKILL.md:81`) | n/a |
| **Model tiers** | `sessions.<k>.model` + `subagents.tiers.{mechanical,judgement}` | `cao.levels[].model` + `cao.routing.<class>[].model`; gate in `profiles.py:96-127` | `model_routing.<role>.preferred_tier` (aliases, not ids) + `role_provider_routing` | n/a | n/a |
| **Model economics rationale** | `SKILL.md:340-354` (4-row "work shape" table) | `SKILL.md:595-607` ("Never put a cheap model where decisions are made") | `SKILL.md:136-162` (§6 Model economics) + `AGENT_ORCHESTRATION_RATIONALE.md` (602 ln) + ADR 0004 | n/a | n/a |
| **Launchers / providers** | `AgentAdapters.psm1` registry (4) | `cao\profiles.py PROVIDER_BY_POOL` (3) + `cao.pools` in config | `providers\registry.py` (7) + `role_provider_routing` | `herdr integration install <name>` (codex, cursor, copilot, droid, opencode, devin, kimi…) | n/a |
| **Stall / quota handling** | `Classify-HandoffFailure` (`HandoffCore.psm1:284`), `Test-HandoffStalled:886`, `Test-HandoffStallBudgetExceeded:928`; defaults `stallMinutes=60`, `stallWindow={maxStalls:5,hours:3}` | `cao\budget.py::classify_limit()` — 3 classes (capacity / billing / transient); `cao\probe.py` closes a pool only on proof; `cao\watchdog.py` sweeps `waiting_user_answer` | none | none (a pane just sits there) | n/a |
| **Merge gating** | **Guards gate the merge**: `--no-ff` under a global mutex, red guard stops that lane only (`SKILL.md:139-146`, rule 3 `:391`) | `cao verify --phase` releases a lease; **no merge at all** | `pr-approval-agent` + `qa-swarm` + `review-triage` skills, then "architect marks complete" — no mechanism | none | n/a |
| **Session persistence** | `<stateDir>/state.json` + NDJSON ledger (`Write-HandoffLedgerEvent:452`, `Reconcile-HandoffLedger:545`); `claude --bg --remote-control <name>` | CAO server sessions + `leases.py` + `verify.ndjson` | `.agent-state/checkpoints/<agent_id>.json` | tmux panes outlive the session | n/a |
| **Worktree isolation** | one worktree per **session**; `postWorktree` + `trust_worktree.py` hydration; `linkDirs`/`copyDirs`/`copyFiles` | `cao\worktree.py`, `cao.worktreeRoot` on ext4 (`SKILL.md:699-722`) | `execution.use_worktrees: true`, `paths.worktrees_dir` | `herdr worktree create` | `SKILL.md:119-219` "Worktrees and parallel agents — three non-negotiable rules" |
| **Memory** | Omnigraph, `OMNIGRAPH_GRAPH_ID` pinned per worktree | same, `--env OMNIGRAPH_GRAPH_ID` forwarded to every spawned worker (`SKILL.md:751-757`) | "managed **exclusively** by the `omnigraph` MCP server" (`SKILL.md:97-98`) | n/a | owns the Omnigraph wiring |

### B2 — facts that live in two or more places (the duplication to collapse)

| Duplicated fact | Copy 1 | Copy 2 | Copy 3 | Risk today |
|---|---|---|---|---|
| **The 3-level hierarchy and the leaf rule** | unattended `SKILL.md:252-271` + `handoff.config.example.json → subagents.{maxDepth,leafRule}` | unattended `SKILL.md:582-601` + `cao.levels` | swarm `SKILL.md:35-74` + `roles.*.can_spawn_subagents` | Three vocabularies (Orchestrator/Session/Subagent · Layer1/supervisor/worker · architect/engineer/reviewer) for one concept. A brief written in one is unreadable to the others. |
| **"Never put a cheap model where decisions are made"** | unattended `SKILL.md:340-354` | unattended `SKILL.md:595-611` + `profiles.py:96-127` (the only *executable* copy) | swarm `SKILL.md:136-162` + ADR 0004 | Prose triplication; only the CAO copy can fire. |
| **Cross-family review** | — | `cao\dispatch.py` pairs on `family`; `cao.reviewPairing: "cross-family"` | swarm `model_routing.reviewer.preferred_tier: different_family_from_engineer` + `SKILL.md:153-162` | swarm's copy is inert; `SKILL.md:158-161` already admits native Claude Code gives lens diversity only. |
| **Wave caps** | `subagents.maxConcurrent`, `machineBudget.maxWorkersPerSession` | — | `orchestration.wave_caps.*` — **a dangling pointer** | swarm `SKILL.md` §0 declares a single owner that does not exist. Anyone honouring §5 reads nothing. |
| **Repo-wide git deny list** | *implicit* (each session owns a worktree) | `plan_launch` refuses the repo root; leaf prompt names `git push`/force-push/`rm -rf` | `orchestration.forbidden_concurrent_git` — **dangling pointer**; the blocking hook `.claude\hooks\block-repo-wide-git.py` is specified in the 2026-07-30 spec and **does not exist** | A policy with no enforcement surface. |
| **Worktree hydration checklist** (venv, graph, `.env`, trust, MCP timeout, memory project name) | unattended `SKILL.md:48-65` (8-row table) | unattended `SKILL.md:714-722` (CAO version of the same) | `mcp-servers-setup SKILL.md:119-219` | Three independently-maintained copies of the same measured incidents. |
| **"CAO or X?" decision tables** | `SKILL.md:99-110` (flowchart: this vs herdr vs native) | `SKILL.md:572-580` (CAO vs batch), `:890-896` (CAO vs Herdr) | herdr `SKILL.md:31-54` (herdr vs native) | Four routing tables across two files; they agree today. |
| **Provider install commands** | `AgentAdapters.psm1:200-223` (`npm i -g @anthropic-ai/claude-code`, agy installer URL, `npm i -g @xai-official/grok`, `npm i -g codewhale`) | — | — | **AutoOS `catalog/*.json` is the other copy** — AutoOS is the repo whose whole job is "how to install a thing", and the skill hardcodes its own installers. This is the single clearest merge win. |
| **Provider/model economics data** | — | `cao.routing` model ids + `effort` (hand-written in `handoff.config.json`) | `role_provider_routing` model *aliases* | **AutoOS `catalog\llm-models.json`** is a fourth copy, and it is the only one carrying prices, context windows and a schema. |

### B3 — where each skill is honest about its own gaps

- unattended: `SKILL.md:485-497` "Known limits, stated rather than discovered"; `:231-233` `--output-format stream-json` is "the recorded future path to live progress and is **not implemented here**"; `:493-494` "Quota detection is pattern-based and fails *open*"; `:747-749` `budget.py` "fails *open* on an unknown pool".
- swarm: `README.md` "the scaffold has been exercised in stub/mock mode only… swarm review currently fails **open**"; `SKILL.md:264` generated adapter "**planned**".
- ADR 0005 header: "`providers/openhands.py` is still a stub" (it is 3.9 KB of `build_request`/`parse_response` with no transport — MEASURED by reading the head).
- ADR 0004: "Accepted (2026-07-30) — design approved, **implementation pending**".

### B4 — the "not fully in production" gaps `unattended-orchestration` itself lists

From `PROPOSALS-2026-09-05-from-a-downstream-orchestrator.md` (24.7 KB). Status table at
`:6-20` marks §1–§7, §9 **built**; the open items are:

| Ref | Open gap | Cost seen |
|---|---|---|
| §2 | automatic `successor` session start | manual re-cut |
| §3 | `archiveDirs` | operator cleans up |
| obs 15 | a session `blocked` on the trust dialog **cannot be `--resume`d** — the runner started a copy that exited at once, six times | 6 wasted launches |
| obs 16 | usage-limit stalls burn the resumes (waited 30 min once, then resumed 6× at 2-min intervals while the limit still held, then "proceeded to the guards anyway", 4 lanes) | a whole night of 4 lanes |
| obs 17 | a lane re-queued with a DONE note present **resumes the session** instead of going to guards | token cost + fresh limit exposure |
| obs 19 | queue pickup latency bounded by the parent's wait on lane children — **measured 1 h 50 min**, not `pollSeconds` | |
| obs 23 | a lane holds its `resources` through guards *and* merge; a guards-only re-queue re-acquires them (C waited ~1 h) | `guardsResources: []` proposed |
| obs 24 | a lane child **captures `dependsOn` at launch** and never re-reads it | lane19 waited forever for a dependency the owner had removed |
| obs 25 | `stop-<KEY>` is keyed by **session, not lane** — cannot retire one of two lanes on the same key | `stop-lane-<name>` proposed |
| obs 26 | an **interrupted** background session is invisible: SX sat at "What should Claude do instead?" for the full 8 h `maxSessionHours` | 8 h |
| obs 27 | **a DONE note can claim a task it did not do** — lane K's note said "K.3 in full", committed before K.3's own commits, two artefacts missing; the runner treats the note's existence as completion | silent false completion |
| obs 30 | the auto-mode classifier refuses `taskkill` on an idle lane child | needs owner in the loop |

**INFERENCE:** obs 27 is the most serious for a swarm, because it is the one failure the guard
cannot catch when the guard is scoped narrower than the brief. obs 16/17 are the most expensive
in tokens. obs 24/25 are the two that a level-2 phase orchestrator would hit immediately, because
a phase orchestrator *edits the plan while lanes run*.

---

## C. What `agent-skills-canvas` adds, and how it would merge

**Git state (MEASURED):**
```
git -C agent-skills worktree list
  …\agent-skills         1ef5c5c [main]
  …\agent-skills-canvas  eab7905 [experiment/agent-canvas-orchestration]

git -C agent-skills-canvas merge-base HEAD main  → 1ef5c5c   (== main's HEAD)
git -C agent-skills-canvas rev-list --count HEAD..main → 0   (behind: none)
git -C agent-skills-canvas rev-list --count main..HEAD → 2   (ahead: two)
git -C agent-skills-canvas status --short → (empty, clean tree)
```

**It is exactly two commits ahead of `main`, zero behind, clean. `git merge --ff-only
experiment/agent-canvas-orchestration` from `main` merges it with no conflicts.**

`git diff --stat main...HEAD`:

```
 skills/unattended-orchestration/AgentAdapters.psm1        | 30 +++++++++++++---
 skills/unattended-orchestration/cao/profiles.py           |  3 +++
 skills/unattended-orchestration/cao/routing.py            |  2 +-
 skills/unattended-orchestration/tests/AdapterContract.Tests.ps1 | 23 +++++++++---
 4 files changed, 50 insertions(+), 8 deletions(-)
```

| Commit | Adds |
|---|---|
| `e9faa3d` feat: agent-canvas adapter and muse CAO pool | `AgentAdapters.psm1`: new **stable** adapter `"agent-canvas"` (`command = "agent-canvas"`, `start = --prompt/--model/--headless/--output-format json`, `resume = --session …`, `list = sessions --json`, `allowedToolsFlag = "--tools"`, `idPattern = "session[^\r\n]*?([0-9a-f]{8})"`, `workingState = "running"`, `probeModel = "ollama/qwen2.5-coder:7b"`, `backgroundMode = "runner-managed"`, `resumeModelNote = "VERIFIED: passes model override on resume"`). New aliases `openhands` → `agent-canvas`, `canvas` → `agent-canvas`. Install command `npm install -g @openhands/agent-canvas`. `cao/profiles.py`: `PROVIDER_BY_POOL += {"muse": "mcode"}`, `PROVIDER_INIT_TIMEOUT += {"mcode": 180}`. `routing.py`: `FAMILY_ORDER += "meta"` |
| `eab7905` feat: openrouter pool and family | `PROVIDER_BY_POOL += {"openrouter": "opencode_cli"}`; `FAMILY_ORDER` final value `("anthropic","google","meta","deepseek","openrouter","oss")` |

**This is the bridge between the two repos.** `openhands → agent-canvas` is precisely the alias
that lets a `handoff.config.json` session say `"launcher": "openhands"` and reach the OpenHands
agent profiles AutoOS already installs (A19).

**Cautions to raise with the operator before merging:**

1. **The adapter's flags are not evidenced anywhere.** `--headless`, `--output-format json`,
   `--session`, `--tools` appear only in this diff and in the test that asserts the diff. The
   `agent-canvas` binary **is installed** on this host (`…\AppData\Roaming\npm\agent-canvas.ps1`,
   MEASURED), so the flags are checkable in one command — but nobody has recorded doing it.
   Contrast the `agy` adapter, whose every flag carries a measured incident comment
   (`AgentAdapters.psm1:38-45`). **INFERENCE:** `resumeModelNote = "VERIFIED: passes model
   override on resume"` is the claim most likely to be aspirational — `claude` and `grok` both
   say `UNVERIFIED` for the same property and the repo's own convention is to say so.
2. **`experimental = $false`** puts `agent-canvas` in the default stable set, so
   `Get-AdapterNames` returns it and `-IncludeExperimental` is not required. `grok` and
   `codewhale`, which are equally unproven, are `$true`. **INFERENCE:** it should ship
   `experimental = $true` until one real run.
3. **The AutoOS side calls this package unverified.** `AutoOS\docs\plans\2026-09-14-opencode-ollama-orchestration.md`
   row 7: "`openhands` (`@openhands/agent-canvas`) in `rescue` … its package/API surface is
   unverified. Defer. Phase 0 spike or drop." and §1.5 "Confirm OpenHands separately
   (`npm view @openhands/agent-canvas version`, real binary name, real `arch` limits)."
   The two repos currently disagree about whether this package is known-good.
4. **A BOM was stripped.** `AdapterContract.Tests.ps1` line 1 changes from `\ufeff<#` to `<#`.
   Harmless under `pwsh` 7; it is a whole-file-encoding change riding in a feature commit.
5. **`PROVIDER_BY_POOL["muse"] = "mcode"`** introduces a provider id `mcode` that appears in no
   other file in either repo (grep). **INFERENCE:** it is a CAO `ProviderType` member name; if
   CAO does not have it, this reproduces exactly the ADR-0008 failure ("Profiles declared
   `provider: antigravity`, not a `ProviderType` member; CAO fell back to `kiro_cli`").
   `cao/profiles.py:68-80` keeps a duplicated copy of "the full enum … so the test can catch
   drift without importing" — check `mcode` is in it.

**Conflicts with current `agent-skills` main: none.** The four touched files have no other
commits since `1ef5c5c`. The merge is mechanically free; the risk is entirely in items 1–5.

---

## D. AutoOS

### D1 — structure (MEASURED)

```
AutoOS/
  setup.ps1  setup.sh              two entry points, no cross-platform abstraction layer
  catalog/                         windows.json linux.json macos.json  (WHAT)
           llm-models.json  llm-models.schema.json    (shared LLM model catalogue)
  lib/windows/  AutoOS.{Catalog,Detect,Install,Progress,Serve,Shell,State,Ui,ClaudeAutostart}.psm1
                claude-sessions.ps1  claude-snapshot-hidden.vbs
  lib/linux/    catalog.sh detect.sh install.sh progress.sh serve.sh ui.sh
                serve.py process.py claude_sessions.py claude-sessions.sh
                systemd/user/claude-sessions-{restore,snapshot}.service|.timer
  web/index.html                   the whole browser UI, one file, 2532 lines
  openhands/profiles/*.json        20 LLM profiles
  openhands/agent-profiles/*.json  11 agent profiles  ← the 3-level hierarchy, as data
  Windows/ansible/                 fleet provisioning (NOT used by setup.ps1)
  Linux/ubuntu_autoinstall/        unattended Ubuntu install profile
  tests/  docs/  third_party/
  autoos.config.json  .autoos-state.json  .mcp.json (omnigraph, graph id "autoos")
```

Pipeline (`AGENTS.md:57`): `detect → profile → select → plan → confirm → execute → report`.
Hard rules (`AGENTS.md:16-32`): never commit a secret (this repo is **public** and leaked once,
history rewritten 2026-08-21); never commit vendor binaries; destructive actions opt-in and
announced; **never overwrite a PATH/profile/config wholesale**; never modify a user file without
a `<file>.autoos-backup-<timestamp>`.

### D2 — how AutoOS relates to agent-skills

| | AutoOS | agent-skills | Verdict |
|---|---|---|---|
| **OS provisioning** | the entire product | none | **Unique to AutoOS** |
| **App catalog** | `catalog/*.json`, 10 categories, providers `winget`/`choco`/`npm`/`script`/`manual` | hardcoded installers in `AgentAdapters.psm1:200-223` | **Duplicated** — 4 agent CLIs are installed two different ways |
| **Profiles (component sets)** | `catalog.profiles` + `component.profiles[]`, 6 on Windows | none | Unique to AutoOS |
| **LLM model catalogue (prices/contexts)** | `catalog/llm-models.json` + JSON schema + a drift test | model ids typed by hand into `handoff.config.json` → `cao.routing` | **Duplicated, and only AutoOS's copy is schema'd and tested** |
| **LLM profiles per provider** | `openhands/profiles/*.json` (20) | `cao\profiles.py` generates CAO profiles per *level* | Different shapes for the same idea |
| **Agent hierarchy as data** | `openhands/agent-profiles/{orchestrator,suborchestrator,worker}*.json` | `cao.levels[]` in `handoff.config.json` | **Duplicated concept, zero shared format** |
| **Orchestration runtime** | **none** — AutoOS installs OpenHands config and stops | the whole point | Unique to agent-skills |
| **MCP server wiring** | `setup.*` clones agent-skills, registers Graphify, approves Omnigraph per repo; links `agent-skills` → `~/.openhands/skills` (`install.sh:1349-1352`) | `mcp-servers-setup` skill + `infra/mcp-servers/` | AutoOS is the *installer*, agent-skills is the *definition* — a clean split already |
| **Secrets** | `autoos.config.json` `answers` (gitignored), prompts with blank defaults | `secrets/api_keys.conf` (gitignored) + `envFrom` | Two stores, same keys (`deepseek`, `muse`, `openrouter`) |
| **Session persistence** | `claude-autostart` restores Claude Code sessions after reboot into a visible terminal | `--remote-control` names + state.json + ledger | Complementary, not duplicated |

**INFERENCE — the clean seam:** AutoOS owns *what exists on the machine and how it is configured*
(catalog, profiles, model catalogue, OpenHands/OpenCode config files, secrets prompts).
agent-skills owns *how agents are driven* (adapters, lanes, guards, merges, routing). Today the
model catalogue and the installer list straddle that seam in both directions.

### D3 — OpenHands / CAO wiring state

| Piece | State | Evidence |
|---|---|---|
| OpenHands **config written** by AutoOS (`~/.openhands/settings.json`, `profiles/`, `agent-profiles/`, `automation/`) | **Built, both platforms** | `install.sh:1327-1637`, `AutoOS.Install.psm1:1274-1536` |
| OpenHands **binary installed** by AutoOS | **Not built** — no `openhands` component in any catalog (`grep openhands catalog/*.json` → 0 hits); the plan defers it (§4 "OpenHands / Agent Canvas — needs its own Phase-0 verification") | `docs/plans/2026-09-14-…:§4` |
| OpenHands **driven** by any orchestrator | **Not built anywhere.** `swarm-orchestration/providers/openhands.py` is a stub (ADR 0005); the canvas adapter (§C) is the first attempt and is unmerged | |
| OpenCode config written | **Built** (`setup_opencode_config`, `Set-AutoOSOpenCodeConfig`) | `install.sh:1130`, `AutoOS.Install.psm1:1065` |
| OpenCode **catalog entry** | **Planned, not shipped** — the whole of `docs/plans/2026-09-14-opencode-ollama-orchestration.md` is the plan to add it, and it is still gated behind a Phase-0 discovery spike | |
| CAO in AutoOS | **Absent.** `grep -i cao AutoOS` → nothing. CAO lives only in agent-skills (`cao/`, `infra/mcp-servers/cao-setup/`) | |
| Ollama | installed by AutoOS (`ollama_models` prompt, default `nomic-embed-text`); consumed by the OpenCode and OpenHands configs | `autoos.config.example.json` answers; `openhands/profiles/ollama-qwen2.5-coder.json` |

### D4 — provider coverage today, per component

Legend: **✔ runs** = there is code that launches it · **cfg** = configuration is written for it but
nothing here launches it · **✖** = absent.

| Component | claude CLI | gemini CLI | agy (Antigravity) | DeepSeek | Muse Spark | Ollama | OpenRouter | Evidence |
|---|---|---|---|---|---|---|---|---|
| unattended batch runner (`AgentAdapters.psm1`) | **✔** `claude` | **✔ via alias only** — `gemini` → `agy`; there is **no** Gemini-CLI adapter | **✔** `agy` | **✔ experimental** via `codewhale` | ✖ | ✖ | ✖ | `AgentAdapters.psm1:8-104` |
| unattended batch runner **+ canvas branch** | ✔ | ✔(alias) | ✔ | ✔ | ✖ (pool only, no adapter) | ✔ *as the agent-canvas probe model* | ✖ (pool only) | §C diff |
| CAO lane (`cao/`) | **✔** pool `anthropic` | ✖ (Gemini reached *as a model* through the antigravity pool) | **✔** pool `antigravity` | **✔** pool `deepseek` via `opencode` | **cfg** (canvas branch adds pool `muse`→`mcode`) | ✖ | **cfg** (canvas branch adds pool `openrouter`→`opencode_cli`) | `profiles.py:58-63` + §C |
| swarm scaffold (`custom_orchestration/`) | adapter exists, **stub mode only** | ✖ | adapter exists, stub only | **✔ live** (`deepseek_tui`) | ✖ | **✔ live** (`ollama.py`, HTTP to `/v1`) | ✖ | `README.md` "Running the example"; `providers/registry.py` |
| herdr | ✔ (any CLI) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ — but only as "start this argv in a pane"; **no model routing, no budget, no schema** | herdr `SKILL.md:44-70` |
| AutoOS → OpenCode config | ✖ | ✖ | ✖ | **cfg** | **cfg** | **cfg** | **cfg** | `install.sh:1162-1323` projects `llm-models.json` into OpenCode's shape |
| AutoOS → OpenHands config | **cfg** (`claude-opus/sonnet/haiku` agent profiles) | **cfg** (`agy-gemini-3.8-flash.json`) | **cfg** | **cfg** | **cfg** (`orchestrator.json` → `muse-spark-1.3`) | **cfg** | **cfg** (14 profiles) | `openhands/{profiles,agent-profiles}` |
| AutoOS installer catalog (installs the CLI itself) | **✔** `claude-code` component | ✖ | **✔** `Antigravity` component | ✖ | ✖ | **✔** `ollama` (script provider) | ✖ | `README.md` "Software on offer"; `catalog/windows.json` |

**The one-line reading:** *every* provider the operator listed is already **configured** somewhere
in AutoOS, and **none** of the non-Anthropic/non-Google ones can be **launched** by the
orchestration skills except through `codewhale` (DeepSeek, experimental) and the two
stub-mode swarm adapters. The canvas branch is the first code that closes any of that gap.

### D5 — UI / TUI profile management: current state

**Web GUI** (`web/index.html`):
- Tabs are a fixed array: `const TABS = ["overview","configure","run","system"]` (`:1124`),
  labels at `:1125`, switching in `showTab()` (`:1126-1138`), menu at `:702-718`.
- Profiles render as **read-only chips** — `renderProfiles()` (`:1816-1820`) emits
  `<button class="profile-chip" data-profile=… aria-pressed=…>`; selecting one applies
  `catalog_profile_defaults`-equivalent logic (`:1119`:
  `STATE.components.filter(c => canInstall(c) && (c.profiles||[]).includes(name))`).
- Profile *summary* cards (`.psum-*`, `:191-233`) show what a profile installs and the install
  order. No edit, no create, no delete, no rename.
- **No import/export anywhere.** grep for `export|import|download` in `web/index.html` returns
  exactly one hit, and it is the string `"Antigravity download URL"` (`:2222`).
- `GET/POST /api/config` exists (`serve.py:352`, `:389`) and does a **merge, not a wholesale
  replace**, with a timestamped backup — that is the pattern a profile writer must copy.
  There is **no** `/api/profiles` of any method.

**TUI**:
- Windows: `AutoOS.Ui.psm1:242-252` — "Interactive single-choice radio selector for profiles or
  options", `[string]$Title = 'Choose profile'`; then a checkbox picker for components.
- Linux: `lib/linux/ui.sh` has **zero** occurrences of `profile`; the profile list is produced by
  `catalog.sh:221 catalog_profile_list` (name + description) and consumed by `setup.sh`.
- Selection only. No editing surface at all.

**Where profile data lives, and why that matters for the feature:**
a profile today is *two* facts spread across the catalog — a name→description entry in
`catalog/<platform>.json → profiles`, and membership encoded **on each component** as
`"profiles": ["workstation","ai-coding"]`. Adding an app to a profile therefore means editing
*the component*, in a **tracked, committed** data file, on **three** platform files. There is also
a validator that refuses a component naming an unknown profile (`catalog.sh:67-68`,
`AutoOS.Catalog.psm1:65-67`).

**INFERENCE — the design decision this forces:** a user-editable profile cannot live in
`catalog/*.json` without making a committed repo file user-mutable (and multiplying it by three
platforms). It needs a **separate, gitignored user-profile store** — e.g.
`~/.autoos/profiles/<name>.json` holding `{name, description, platform, components: [ids]}` —
with the catalog's built-in profiles as read-only seeds that a user profile can be *forked from*.
That store is then trivially import/exportable (it is one JSON file per profile), and
`.autoos-state.json` / `--from-state` already proves the round-trip format works.

---

## E. Gap list against the 3-level hierarchy goal

Goal restated: **L1** = one interactive Claude Code vision/goal orchestrator · **L2** = phase
orchestrators, smart Claude sessions with task-specific knowledge · **L3** = mechanical, fast,
diverse executors and reviewers across claude/agy/deepseek/muse/ollama/openrouter/gemini —
spawned by `unattended-orchestration`. Cost-efficient, fast, swarm-safe.

| # | Gap | What is missing, concretely | Where it would live | Effort | Risk if skipped |
|---|---|---|---|---|---|
| E1 | **No launcher for 4 of the 7 providers** | `AgentAdapters.psm1` has `claude`, `agy`, `grok`, `codewhale`. No adapter reaches Muse Spark, Ollama, OpenRouter, or a real Gemini CLI. The canvas branch adds one (`agent-canvas`) that could reach *all* of them, because OpenHands is itself a multi-provider runtime | `skills\unattended-orchestration\AgentAdapters.psm1` + `tests\AdapterContract.Tests.ps1` | **M** | L3 diversity stays a slogan; "diverse executors" means claude+agy only |
| E2 | **The `agent-canvas` adapter is unverified** | One run of `agent-canvas --prompt … --headless --output-format json` and one resume would settle every flag in §C. The binary is installed | same files; record the measurement as a comment beside each flag, the way `agy`'s are | **S** | A whole provider tier that silently no-ops; exactly the ADR-0008 failure mode |
| E3 | **Level-2 has no persistent identity** | The batch runner's unit is a *session*; a phase orchestrator that spans several sessions has no record of its own. `cao.levels[1]` exists but only inside a CAO run; `state.json` keys sessions, not phases | `handoff.config.json` → a `phases` block (phase → sessions → guard), plus `Get-HandoffSessionVars` extended with a phase id; CAO's `PLAN.lock.json` is the nearest existing artifact | **L** | L2 becomes "a session that happens to be called an orchestrator" — the collapse `SKILL.md:252-256` warns about |
| E4 | **Lane config is captured at launch** (obs 24) and `stop-<KEY>` is per-session not per-lane (obs 25) | A level-2 orchestrator *edits the plan while lanes run*. Today that edit never reaches a waiting lane, and it cannot retire one | `run_handoff_sessions.ps1` lane child poll loop; `HandoffCore.psm1::Get-HandoffDependencyState:824` | **M** | L2 cannot steer; it can only queue |
| E5 | **A DONE note is trusted as completion** (obs 27) | Nothing checks the brief's `## Done means` artefact list against the tree before the guards | `HandoffCore.psm1::Test-HandoffDoneQuiet:870` + a new `Test-HandoffArtifactsPresent`; the brief already declares the list | **M** | An L3 executor's false completion propagates up two levels before a human sees it |
| E6 | **No cost accounting anywhere** | "cost-efficient" is stated as policy in three places and measured in none. `budget.spent()` is referenced in ADR 0004's *rejected* alternative. `llm-models.json` has the prices; nothing reads them for attribution | AutoOS `catalog\llm-models.json` (prices, already there) × a new `<stateDir>/cost.ndjson` written per turn by the runner | **M** | You cannot tell whether the hierarchy saved money; Cursor's 8× spread is invisible without per-role attribution (ADR 0004 §Alternatives) |
| E7 | **Swarm-safety is per-worktree, and that is only half of it** | `resources` (write/read exclusion) is production-grade for *sessions*. Subagents inside a session share the session's worktree and have **no** exclusion primitive; `subagents.maxConcurrent` bounds count, not contention | `handoff.config.json` → `subagents.resources`, or (cheaper) the swarm rule "overlapping file ownership is a consolidation signal" enforced at brief-render time | **M** | Two L3 executors in one L2 session editing one file — the exact failure worktrees exist to prevent, one level down |
| E8 | **`orchestration.wave_caps` and `orchestration.forbidden_concurrent_git` are dangling pointers** | swarm `SKILL.md:25,120-121,134` name a config owner that does not exist; the blocking hook `.claude/hooks/block-repo-wide-git.py` specified in the 2026-07-30 spec was never written | `custom_orchestration\agent_orchestration.config.yaml` (add the keys), or delete the pointers | **S** | Policy that reads as enforced and is not — the condition ADR 0004 was written to end |
| E9 | **Three role vocabularies for one hierarchy** | Orchestrator/Session/Subagent · Layer1/supervisor/worker · architect/engineer/reviewer | one merged skill (see §G phase 1) | **M** | Briefs are not portable between lanes; an L2 written for CAO cannot drive the batch runner |
| E10 | **Two model catalogues** | `cao.routing` model ids hand-typed in `handoff.config.json`; `AutoOS\catalog\llm-models.json` is schema'd, priced, drift-tested | make `llm-models.json` the single source; generate the `cao.routing` ladders from it | **M** | Model ids rot (ADR 0004 already recorded "reviewer model tiers pinned to ids two generations stale") |
| E11 | **Two installer lists for the same four CLIs** | `Get-AdapterInstallCommand` (`AgentAdapters.psm1:200-223`) vs AutoOS `catalog/*.json` | AutoOS owns installation; the adapter should *detect*, not install | **S** | Divergence between "how AutoOS installs agy" and "how the runner says to install agy" |
| E12 | **OpenHands is configured but never launched** | 11 agent profiles + 20 LLM profiles written to `~/.openhands/`, no code anywhere starts an OpenHands agent | the canvas adapter (E1/E2) is the launcher; an `openhands` pool in `cao.pools` is the CAO-side twin | **M** | The single largest piece of already-done work in either repo stays inert |
| E13 | **No provider-health preflight in the batch lane** | CAO has `probe` (closes a pool only on proof, `SKILL.md:526-538`). The batch runner has a per-launcher `probe` argv but no ladder to fall to | `HandoffCore.psm1` + a `sessions.<k>.launcherLadder` | **M** | A dead provider costs a whole lane instead of a retry |
| E14 | **Quota classification is per-launcher prose matching, and only `claude`+`agy` wordings are known** | `Classify-HandoffFailure:284` knows `usage limit reached|<epoch>` and agy's `quota reached`/`RESOURCE_EXHAUSTED`/`Resets in …`. OpenRouter, DeepSeek, Muse and Ollama wordings are unknown; `SKILL.md:493` says detection "fails *open*" | `HandoffCore.psm1::Classify-HandoffFailure` + `tests\HandoffCore.Tests.ps1` (one fixture per provider) | **M** | "a wall resumed as an early stop" — `SKILL.md:194-203` already cost 8 wasted resumes on one unknown wording |
| E15 | **AutoOS cannot create or edit a profile** | §D5 | new user-profile store + TUI/GUI tab (§G phase 4) | **M** | the operator's stated feature |
| E16 | **`cao-setup/README.md` is empty (0 bytes)** and CAO must be invoked through WSL on Windows (`SKILL.md:695`) | the whole CAO lane is undiscoverable from the repo | `infra\mcp-servers\cao-setup\README.md` | **S** | L2 phase orchestrators would be the main CAO users and cannot find the runbook |

**Risk ordering (INFERENCE):** E2 → E8 → E5 → E14 → E4 are the ones that make the system *lie*
(a provider that silently does nothing, a policy that reads as enforced, a false completion, a
wall read as an early stop, a steering command that never arrives). E1/E12/E10/E6 are the ones
that make it *expensive*. E3/E9 are the ones that make it *unbuildable at level 2*.

---

## F. OpenAI Agents API assessment

**Sourcing note:** `https://openai.com/index/introducing-the-agents-api/` returned **HTTP 403** to
WebFetch (MEASURED). The summary below is assembled from
[developers.openai.com/api/docs/guides/agents](https://developers.openai.com/api/docs/guides/agents),
[eesel.ai's Agents API pricing breakdown](https://www.eesel.ai/blog/openai-agents-api-pricing),
and the [Agents SDK handoffs](https://openai.github.io/openai-agents-python/handoffs/) /
[guardrails](https://openai.github.io/openai-agents-python/guardrails/) docs. Treat every figure
as second-hand.

### What it is

- A **managed agent runtime**: "OpenAI runs a managed Codex harness", "OpenAI manages the agent
  and saves its progress". It runs the agent loop server-side with "automatic context compaction,
  multi-agent orchestration, and an optional hosted sandbox".
- **Sessions/state**: "Saved session configuration, turns, and items" persist between tasks.
- **Handoffs**: agent→agent transfer is a first-class primitive (triage agent → specialist
  agent), transparent to the end user.
- **Guardrails**: input guardrails validate user messages before the agent sees them; output
  guardrails validate responses before the user sees them.
- **Tools**: service-connected tools, application function handlers, and **MCP servers**.
- **SDK**: the `openai-agents` SDK is MIT-licensed and **costs $0**; you pay for model calls and
  billed hosted tools.
- **Adjacent churn**: OpenAI is winding down Agent Builder and Evals — unavailable from
  **2026-11-30** — pointing users to the code-first Agents SDK or Workspace Agents.

### Cost shape (second-hand, Sept 2026)

| Item | Price |
|---|---|
| gpt-5.6-luna | $0.20–$1.20 / 1M tokens |
| gpt-5.6-terra | $2.00–$12.00 / 1M |
| gpt-5.6-sol | $4.00–$20.00 / 1M |
| gpt-6-astra | $10.00–$50.00 / 1M |
| Web search tool | $10 / 1,000 calls (+ search-content tokens at model rate) |
| File search | $2.50 / 1,000 calls; file storage $0.10/GB/day |
| Code Interpreter | $0.03–$1.92 per session |
| Long context (>272K) | ~2× standard rate |
| Cached input | ~1/10 of fresh input |

### Fit against this 3-level hierarchy

| Dimension | Verdict |
|---|---|
| **Can it execute L1/L2?** | No. L1 and L2 are explicitly Claude Code sessions holding the vision and the plan contract. The Agents API runs **OpenAI models on OpenAI's harness**; the docs make no mention of non-OpenAI models and the infrastructure "appears restricted to OpenAI's own model lineup". |
| **Can it execute L3?** | Only as *one more provider*, and one that is strictly **pay-per-token** — no subscription to amortise, unlike agy (Antigravity subscription), the Claude subscription, or free OpenRouter/local Ollama. It would be the **most expensive** L3 tier available here. |
| **Does it replace `unattended-orchestration`?** | No, and it cannot. It has no git worktree isolation, no guard-gated `--no-ff` merge, no `resources` exclusion, no DONE-note protocol, no usage-limit recovery across *other vendors'* limits, and no way to drive `claude`/`agy`/`opencode` CLIs. Those are the things this repo has actually paid for. |
| **Are its good ideas already here?** | Mostly. Handoffs ≈ `dependsOn` + DONE-note briefs. Guardrails ≈ fence hooks + `pr-approval-agent` deterministic gates. Sessions ≈ `state.json` + NDJSON ledger. Hosted sandbox ≈ disposable ext4 worktrees. Context compaction ≈ the OpenHands `condenser` block AutoOS already ships in every agent profile. |
| **What it would genuinely add** | (a) A *reviewer of a different family* that is neither Anthropic nor Google — which is the one thing `swarm-orchestration SKILL.md:153-162` says native tooling cannot give. (b) Server-side durability: an agent that survives the host rebooting, which killed ten writers on 2026-09-05 in the sibling repo. |
| **Lock-in** | High at the runtime layer (the loop, the session store and the sandbox are OpenAI's), low at the SDK layer (MIT, and it speaks MCP). Agent Builder's 2026-11-30 sunset is a live signal that the surrounding product surface is unstable. |

### Recommendation

**Do not adopt the Agents API as orchestration infrastructure. Optionally adopt an OpenAI model
as one L3 *reviewer* pool, reached the same way every other pool is.**

Concretely:
1. **Reject** it as a replacement or peer for `unattended-orchestration` / CAO. It owns the layer
   this project has already built and hardened, and it cannot run Claude or Gemini, which are
   the two providers L1/L2 must be.
2. **Accept** OpenAI as a `cao.pools` entry (`openai` → `codex` CLI — `codex` already has an
   adapter in the swarm scaffold, `providers/codex.py`, and `role_provider_routing.reviewer`
   already routes the reviewer role there) with `family: "openai"` added to
   `routing.FAMILY_ORDER`. That buys the cross-family review diversity at **zero new
   infrastructure**, using the pool/family split `routing.py` already implements.
3. **Steal one idea, cheaply:** typed input/output guardrails around an agent turn. The nearest
   local equivalent is `.claude/hooks/fences.py` in the sibling repo; an output guardrail that
   validates a DONE note against the brief's artefact list is exactly gap **E5**.
4. **Revisit** only if a hosted, reboot-proof runtime becomes a requirement — e.g. if the
   orchestrator itself must survive Windows Update. Record it as a `Decision` node so it is not
   re-litigated.

---

## G. Proposed merge plan skeleton

Four phases, ordered so each one's output is the next one's input. Every task names real files
and real functions. **Decision points are marked ◆ and are the operator's, not an agent's.**

Repository shape assumed (◆ **Decision 0**): keep **two repos**, not one. AutoOS is public and has
leaked credentials once (`AGENTS.md:20`); agent-skills carries `secrets/api_keys.conf` and a
private infra tree. Merge the *content* along the seam in §D2, not the git histories.
The alternative (one monorepo) is viable but forces a secret-handling redesign on day one.

---

### Phase 1 — collapse four orchestration skills into one, plus a setup

**Goal:** one skill, one vocabulary, one config, no dangling pointers.

| # | Task | Files | Test |
|---|---|---|---|
| 1.1 | Create `skills\agent-orchestration\SKILL.md` as the single normative policy. Fold in: unattended `SKILL.md` §1-§8 (batch), §9 (CAO), swarm `SKILL.md` §1-§16 (roles→layers), herdr `SKILL.md` §1 (when a pane beats a session). Keep the §0 "where each fact lives" pattern; **it must contain no numbers** | new `SKILL.md`; delete `skills\swarm-orchestration\SKILL.md`, `skills\herdr-orchestration\SKILL.md`, `skills\unattended-orchestration\SKILL.md` after folding | a link-check that every `SKILL.md` pointer resolves to an existing config **key**, not just an existing file — this is what would have caught E8 |
| 1.2 | **Unify the role vocabulary** to L1/L2/L3 (`orchestrator` / `phase-orchestrator` / `executor`+`reviewer`). Rewrite `@architect`/`@engineer`/`@reviewer` and `Orchestrator`/`Session`/`Subagent` onto it. Keep a one-table glossary mapping the old names, because `handoff.config.json` and `cao.levels` in the wild still use them | new `SKILL.md`; `handoff.config.example.json` `$comment_*` blocks; `cao\profiles.py::_ROLE_DUTY` | `tests\AdapterContract.Tests.ps1` unchanged; add a test that `cao.levels[].role` values are in the new vocabulary |
| 1.3 | **Fix the dangling pointers (E8).** Add `orchestration: {wave_caps: {context_sharing, isolated_candidate}, forbidden_concurrent_git: [...]}` to `custom_orchestration\agent_orchestration.config.yaml` with the values from `docs\superpowers\plans\2026-07-30-…:964-975`, **or** delete the three pointers | `agent_orchestration.config.yaml` | the plan already contains the tests verbatim: `test_wave_caps_present_and_two_tier`, `test_lens_count_fits_the_context_sharing_cap`, `test_forbidden_concurrent_git_patterns_compile` (plan `:902-940`) |
| 1.4 | Move the swarm Python scaffold under the merged skill as `custom_orchestration/` and mark it **stub-mode** in one place instead of three | `skills\agent-orchestration\custom_orchestration\**` | existing `custom_orchestration\tests\*` must stay green |
| 1.5 | Keep **both runtimes** — PowerShell batch lane and Python CAO lane — exactly as ADR 0008 §1 decided ("Two lanes, one skill, shared formats — not shared code"). Do **not** rewrite one in the other | `run_handoff_sessions.ps1`, `HandoffCore.psm1`, `cao\*` unchanged | the 7 existing suites |
| 1.6 | One setup entry point: `skills\agent-orchestration\setup.ps1` / `.sh` that runs `run_handoff_sessions.ps1 -Init`, `python -m cao check`, `setup_cao.py --check`, and reports which lanes are usable | new; wraps `infra\mcp-servers\cao-setup\setup_cao.py` | a smoke test in the style of `Runner.Smoke.Tests.ps1` |
| 1.7 | Write `infra\mcp-servers\cao-setup\README.md` (currently **0 bytes**, E16) | that file | link-check |

◆ **Decision 1a:** does `herdr-orchestration` survive as a section of the merged skill, or as a
standalone skill? It is 98 lines with no code and ADR 0007 already evaluated it as *not* the
unattended backend. **Recommendation: fold it in as one section** ("when a human-watched pane
beats a background session") and delete the skill.
◆ **Decision 1b:** does `swarm-orchestration`'s Python scaffold survive at all, given it has never
left stub mode and the CAO lane now does live multi-provider work? **Recommendation: keep only
`providers/*.py` (they are honest capability declarations and the `ollama`/`deepseek_tui` ones
are live) and `verification_runner.py`; retire `orchestrator_scaffold.py` and `role_router.py`
whose job CAO now does.**
◆ **Decision 1c:** `mcp-servers-setup` — leave alone. It is not an orchestration skill; it is the
wiring reference the other three cite. Merging it would re-create the "one giant skill" problem.

---

### Phase 2 — merge the canvas worktree

| # | Task | Files | Test |
|---|---|---|---|
| 2.1 | **Verify the adapter before merging (E2).** Run, once, on this host: `agent-canvas --prompt "Reply with exactly OK" --model <id> --headless --output-format json`, then resume it with `--session <id> --model <other>` and read the model line. Record each flag's evidence as a comment beside it, the way `AgentAdapters.psm1:38-45` does for `agy` | `AgentAdapters.psm1` (comments only) | none — this is a measurement, its output goes in the comment |
| 2.2 | Until 2.1 passes, flip `experimental = $true` for `agent-canvas` | `AgentAdapters.psm1` | `AdapterContract.Tests.ps1` "registers stable adapters…" assertion must move to the experimental list |
| 2.3 | Check `mcode` and the new pools against CAO's real `ProviderType` enum — `cao\profiles.py:68-80` keeps a duplicated copy "so the test can catch drift without importing". If `mcode` is not a member, the pool silently falls back (ADR 0008's opening failure) | `cao\profiles.py` | the existing drift test in `tests\cao\` |
| 2.4 | `git -C agent-skills merge --ff-only experiment/agent-canvas-orchestration` | — | all 7 suites + `tests\cao\` |
| 2.5 | Restore the BOM on `tests\AdapterContract.Tests.ps1` or record deliberately that the repo drops BOMs | that file | — |
| 2.6 | Remove the worktree and **drop its row from `~\.serena\serena_config.yml` → `projects`** | — | — |

◆ **Decision 2:** the alias `openhands` → `agent-canvas` asserts these are the same thing. If
`@openhands/agent-canvas` is not the OpenHands project's own CLI, the alias is a trap. AutoOS's
own plan calls the package unverified. **This must be settled before 2.4**, because every
`"launcher": "openhands"` in a future config depends on it.

---

### Phase 3 — OpenHands / CAO / LLM-profile wiring for the 3-level hierarchy

**Goal:** one model catalogue, one profile generator, every provider reachable at L3.

| # | Task | Files | Test |
|---|---|---|---|
| 3.1 | **Make `AutoOS\catalog\llm-models.json` the single model source (E10).** Add the fields the orchestration side needs but AutoOS lacks: `family` (`anthropic`/`google`/`meta`/`deepseek`/`openrouter`/`oss`), `pool`, `tier` (`orchestrator`/`leaf`), `subscription: bool` | `catalog\llm-models.json`, `catalog\llm-models.schema.json` | AutoOS's existing drift test — `tests/run-tests.sh "vendored openhands profiles match the catalog snapshot"` — extends to the new fields |
| 3.2 | **Generate the CAO ladders from it.** New `cao\catalog.py::load_model_catalog(path)` + `build_routing(catalog)` producing the `cao.routing.<class>` ladders now hand-typed in `handoff.config.json`. Keep hand-written ladders as an override | new `cao\catalog.py`; `cao\routing.py` consumes it; `handoff.config.example.json` gains `cao.modelCatalog: "<path>"` | new `tests\cao\test_catalog.py`: every generated ladder's models exist in the catalogue; every spawning level's model is in `ORCHESTRATOR_MODELS` |
| 3.3 | **Add the four missing pools** to `cao.pools` / `PROVIDER_BY_POOL`: `muse`, `openrouter`, `ollama`, `openhands`. The canvas branch does two of them | `cao\profiles.py:58-63` | drift test (2.3) |
| 3.4 | **Extend `FAMILY_ORDER`** to `("anthropic","google","meta","deepseek","openrouter","oss")` (canvas does this) and **add `openai`** if §F rec. 2 is accepted | `cao\routing.py:19` | `tests\cao\` pairing test: a review candidate never shares `family` with the implementer |
| 3.5 | **Teach `Classify-HandoffFailure` the other providers' quota wordings (E14).** One fixture per provider — OpenRouter 429 body, DeepSeek `insufficient_balance`, Ollama connection-refused, agent-canvas/OpenHands wall | `HandoffCore.psm1:284`; `tests\HandoffCore.Tests.ps1` | **two** tests per wording (pass *and* fire) — a guard must be able to fire |
| 3.6 | **Generate the OpenHands agent profiles from the level spec**, instead of hand-maintaining 11 JSON files: `orchestrator` ← `cao.levels[0]`, `suborchestrator` ← `levels[1]`, `worker*` ← `levels[2]`, `enable_sub_agents` ← `canSpawn`. AutoOS then installs generated output | new `cao\openhands_profiles.py`; consumed by `AutoOS\lib\linux\install.sh:1619-1637` and `AutoOS.Install.psm1:1536+` | AutoOS's vendored-profile drift test becomes a *generation* test |
| 3.7 | **Retire `Get-AdapterInstallCommand` in favour of the AutoOS catalog (E11):** the adapter keeps `Test-AdapterInstalled` (detection) and, when missing, prints `autoos --only <component-id>` | `AgentAdapters.psm1:200-223`; `catalog\*.json` gains ids for `codewhale`, `grok`, `agent-canvas` | `AdapterContract.Tests.ps1` install-command test becomes a catalog-id test |
| 3.8 | **Cost ledger (E6):** every turn appends `{session, level, pool, model, wall_s, in_tok, out_tok, usd}` to `<stateDir>\cost.ndjson`, priced from 3.1 | `HandoffCore.psm1` beside `Write-HandoffLedgerEvent:452`; `-Status` prints a per-level total | `tests\Ledger.Tests.ps1` gains a cost-rollup case |
| 3.9 | **Artefact check before the guards (E5):** `Test-HandoffArtifactsPresent` reads the brief's `## Done means` list and fails the session if a named file is absent | `HandoffCore.psm1` near `Test-HandoffDoneQuiet:870`; brief template in `handoff.config.example.json` | pass-and-fire pair in `HandoffCore.Tests.ps1` |
| 3.10 | **Re-read `dependsOn` per poll; add `stop-lane-<name>` (E4, obs 24/25)** | `run_handoff_sessions.ps1` lane child loop; `Get-HandoffDependencyState:824` | `Runner.Smoke.Tests.ps1` |

◆ **Decision 3a:** **which provider runs L3 by default.** The candidates differ by *budget type*,
not price: agy = Antigravity subscription (`SKILL.md:519-521` "Never decline a quota pool… Refusing
them does not get a better model — it gets a smaller budget"); OpenRouter free tier = $0 with
rate caps; Ollama = $0 and local but `ollama` is **not on this host's PATH**; DeepSeek/Muse = cheap
per-token. Recommendation (INFERENCE): ladder `antigravity → openrouter-free → deepseek → ollama`
for `mechanical`, keeping `anthropic` for `review`.
◆ **Decision 3b:** **does L3 run through OpenHands (`agent-canvas`) or through per-provider CLIs?**
OpenHands gives one adapter for every provider plus a condenser and a sandbox; per-provider CLIs
give the failure modes this repo has already measured. Recommendation: **both** — CLIs for
providers that have one (`claude`, `agy`), OpenHands for the ones that do not (muse, openrouter,
ollama).
◆ **Decision 3c:** does the OpenAI/`codex` reviewer pool go in (§F rec. 2)? Yes/no decides 3.4.
◆ **Decision 3d:** where does the generated-profile pipeline live — in agent-skills (agent-skills
generates, AutoOS installs) or in AutoOS? Recommendation: **agent-skills generates**, because the
level spec is orchestration policy and AutoOS's rule is "the catalog is data, the libraries are code".

---

### Phase 4 — AutoOS profile-management tab (GUI + TUI)

**Goal:** create / edit / delete / import / export profiles, and browse the catalog to add or
remove applications from one.

| # | Task | Files | Test |
|---|---|---|---|
| 4.1 | **User-profile store.** `~/.autoos/profiles/<id>.json` = `{schema:1, id, name, description, platform, components:[ids], derivedFrom?:"<builtin id>"}`. Built-ins in `catalog/*.json` stay **read-only seeds**; "Edit" on a built-in forks it. Gitignored by construction (it is outside the repo) | new `lib\linux\profiles.sh` + `lib\windows\AutoOS.Profiles.psm1` with `Get-AutoOSProfiles`, `New-AutoOSProfile`, `Remove-AutoOSProfile`, `Import-AutoOSProfile`, `Export-AutoOSProfile`, `Set-AutoOSProfileComponents`; Linux twins `profiles_list/new/remove/import/export/set_components` | pure-function tests only (AutoOS rule: assert on the planned command, never on system state); a round-trip test export→import→identical JSON |
| 4.2 | **Merge built-in + user profiles at read time.** `catalog_profile_list` and `Get-AutoOSComponentProperty … 'profiles'` gain a user-store overlay; a user profile's membership is stored **on the profile** (an id list), not on the component — so no committed catalog file is ever rewritten (AGENTS.md hard rule 4) | `lib\linux\catalog.sh:143,221,239`; `lib\windows\AutoOS.Catalog.psm1:162-171` | existing catalog validator tests must still pass; add "a user profile naming an unknown component id is reported, not fatal" |
| 4.3 | **HTTP API.** `GET /api/profiles` (built-in + user, flagged), `POST /api/profiles` (create/update), `DELETE /api/profiles/<id>`, `GET /api/profiles/<id>/export` (download JSON), `POST /api/profiles/import`. **Copy the `/api/config` POST discipline exactly**: read-modify-write, timestamped backup, atomic `tmp.replace()` (`serve.py:389-411`) | `lib\linux\serve.py` `do_GET:314`/`do_POST:378`; mirror in `lib\windows\AutoOS.Serve.psm1:425-504` | a test that the two implementations expose the identical route table — they have already drifted once (CHANGELOG `[Unreleased]` §Fixed — web UI) |
| 4.4 | **Web GUI tab.** Add `"profiles"` to `TABS` (`web\index.html:1124`) and `TAB_LABEL` (`:1125`), a `<section role="region" id="panel-profiles">` beside `panel-configure` (`:856`), and `renderProfilesTab()` next to `renderProfiles()` (`:1816`). Contents: profile list with add/duplicate/rename/delete; an Import button (file input) and an Export button (Blob download) — note **the page today contains zero import/export code**; and a catalog browser reusing the existing component cards + `canInstall()`/`REQUIRED_BY` dependency badges so ticking an app into a profile shows what it drags in | `web\index.html` | `tests\test-web-progress.js` is the existing JS-test precedent; add a DOM test that the tab renders and that Export produces valid profile JSON |
| 4.5 | **TUI.** Windows: a second menu level under `AutoOS.Ui.psm1:242` — profile radio gains `[+ New] [Edit] [Delete] [Import] [Export]` rows; Edit opens the existing component checkbox picker pre-ticked from the profile. Linux: `lib\linux\ui.sh` currently has **no** profile code at all — it needs the same menu built on the existing checkbox picker | `AutoOS.Ui.psm1`, `lib\linux\ui.sh`, `setup.ps1`/`setup.sh` dispatch | both suites; `shellcheck` and `PSScriptAnalyzer` clean (CI runs them) |
| 4.6 | **Flags for headless use**: `--profile-export <id> <path>`, `--profile-import <path>`, `--profile-new <name>`, so the GUI is not the only way | `setup.ps1`, `setup.sh` — **note AGENTS.md:51-53**: "Adding software must never require touching `setup.ps1` or `setup.sh`". Profile *management* is not adding software, so a flag is legitimate; adding a *profile* must still require no code change | flag-parsing tests in both suites |
| 4.7 | **Docs**: `docs\profiles.md` gains an "Editing profiles" section; `docs\web-ui.md` gains the tab; `CHANGELOG.md` `[Unreleased]` entry | those files | `tests\check-links.py` |

◆ **Decision 4a:** **user store location** — `~/.autoos/profiles/` (per user, survives a repo
re-clone, cannot be committed) vs `AutoOS/profiles.local/` (gitignored, travels with the checkout).
Recommendation: `~/.autoos/`, matching where `.openhands/` and `.config/opencode/` already live.
◆ **Decision 4b:** **can a user profile edit a built-in?** Recommendation: no — fork on edit, with
`derivedFrom` recorded, so `git pull` updating a built-in never silently changes a user's set.
◆ **Decision 4c:** **does the export format carry answers/secrets?** `.autoos-state.json` carries
`answers` including `git_user_email`. A profile export **must not**; the repo is public and
hard rule 1 is absolute. Recommendation: export components + metadata only, never `answers`.
◆ **Decision 4d:** should the profile tab also manage **LLM/agent profiles** (`openhands/profiles`,
`openhands/agent-profiles`)? They are a different kind of profile with the same word. If yes, the
tab needs two sub-sections and `llm-models.json` becomes a browsable catalog too — which is
where phases 3 and 4 meet. Recommendation: **yes, but as a second sub-tab in a later pass**;
shipping the app-profile editor first keeps the change reviewable.

---

### Cross-cutting sequencing note

Phase 2 (verify + merge canvas) is **independent** and should go first — it is two commits and a
measurement, and everything in phase 3 that touches providers builds on it. Phase 1 is a large
documentation refactor with no runtime risk and can run in parallel in its own worktree. Phase 3
depends on both. Phase 4 depends on nothing except 3.1 *if* decision 4d is taken.

**Swarm-safety while doing this work itself:** phases 1 and 4 touch disjoint repos; phases 2 and 3
both touch `AgentAdapters.psm1` and `cao\profiles.py` — they must be **one lane**, sequential, not
two. That is `resources: ["adapters:write"]` on both sessions in `handoff.config.json`.

---

## Appendix — provider mention density in `agent-skills` main

MEASURED: files containing each term (case-insensitive) across `skills/ infra/ prompts/ docs/`
of `agent-skills` at `main` (`1ef5c5c`), over `*.md *.json *.ps1 *.psm1 *.py *.yaml *.yml`:

| term | files | term | files |
|---|---|---|---|
| antigravity | 48 | opencode | 20 |
| agy | 43 | codex | 16 |
| gemini | 41 | grok | 13 |
| deepseek | 40 | openhands | 11 |
| ollama | 29 | codewhale | 10 |
| litellm | 7 | **openrouter** | **2** |
| | | **muse / spark** | **0** |

Reading: Antigravity/agy/Gemini/DeepSeek are pervasive; **Muse Spark appears nowhere in
`agent-skills` main** outside `secrets\api_keys.conf.example` (which sits outside the searched
tree) and the unmerged canvas branch, and **OpenRouter appears in two files**. Both are fully
modelled on the AutoOS side (14 OpenRouter profiles, 2 Muse profiles, priced entries in
`catalog\llm-models.json`). That asymmetry is gap **E1/E10** stated as a number: the model
knowledge lives in AutoOS, the launch machinery lives in agent-skills, and nothing connects them
today except the two unmerged canvas commits.
