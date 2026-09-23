# Agent orchestration: one skill, one model catalogue, three levels — the agent-skills × AutoOS merge plan

*Plan, 2026-09-17, written by the sibling-analysis-repo orchestrator for the L2 session that
`orchestrator3` will spawn. Plan-only; nothing here has been executed. Evidence: the read-only survey
beside this file, `2026-09-17-agent-orchestration-and-autoos-merge-SURVEY.md` (sections A–G, every
claim with a file:line, inferences marked). Decision points are the operator's (◆).*

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (this plan is
> task-level; expand each task into TDD steps before touching a file). Work in a worktree per
> phase; phases 2 and 3 share `AgentAdapters.psm1` and `cao/profiles.py` and are **one sequential
> lane** (`resources: ["adapters:write"]`).

## Goal

A three-level hierarchy that is real in code, not in prose:

| Level | Who | Runs as | Knows |
|---|---|---|---|
| **L1** `orchestrator` | one Claude (Fable/Opus) Claude Code session — the vision, the goals, research-grade fit of every result | interactive; heartbeat only when work runs | the whole project, every decision, the spec |
| **L2** `phase-orchestrator` | Claude (Opus) Claude Code sessions, one per phase | interactive `claude --bg` sessions the L1 attaches to, or `unattended-orchestration` sessions with the `judgement` tier | one phase's plan, its files, its acceptance |
| **L3** `executor` / `reviewer` | Sonnet, Haiku, `agy` (Gemini 3.8 Flash), DeepSeek (`codewhale`), Muse Spark / OpenRouter / Ollama (through OpenHands `agent-canvas` once verified), optionally OpenAI `codex` as a cross-family reviewer pool | spawned by `unattended-orchestration` (batch lane) or CAO (interactive lane); leaf rule: no spawning | one closed-file task |

Cost-efficient (subscriptions before per-token, free tiers before paid, a cost ledger that proves
it), fast (parallel lanes, small briefs), swarm-safe (one worktree per session, `resources`
exclusion, guard-gated `--no-ff` merges, artefact check before the guards).

## What is true now (from the survey — the five facts that shape the plan)

1. `unattended-orchestration` is the **only production orchestrator**; its batch lane supports
   `claude`, `agy` (stable), `grok`, `codewhale` (experimental). Its CAO lane ran end to end once.
   Its own open-gap list (`PROPOSALS-2026-09-05…`) has twelve items; the worst are: a DONE note is
   trusted as completion (obs 27), `dependsOn` is captured at launch (obs 24), `stop-<KEY>` is per
   session not per lane (obs 25), usage-limit stalls burn resumes (obs 16/17).
2. `swarm-orchestration` is **policy without enforcement**: its `SKILL.md` points at config keys
   (`orchestration.wave_caps`, `orchestration.forbidden_concurrent_git`) that do not exist; the
   blocking hook was never written; the Python scaffold has run in stub mode only.
3. **AutoOS already holds the hierarchy as data** (`openhands/agent-profiles/{orchestrator,
   suborchestrator, worker}*.json`) and the **only schema'd, priced model catalogue**
   (`catalog/llm-models.json`), but nothing launches OpenHands and no code reads the prices.
4. `agent-skills-canvas` is two clean commits ahead of `main` (an `agent-canvas` adapter aliased
   from `openhands`/`canvas`, `muse` and `openrouter` CAO pools) — mergeable with `--ff-only`, but
   its flags are evidenced nowhere and it ships `experimental=$false`.
5. Provider asymmetry: Muse Spark appears in **zero** agent-skills files, OpenRouter in two; both
   are fully modelled in AutoOS. The model knowledge lives in AutoOS, the launch machinery in
   agent-skills, and nothing connects them.

**OpenAI Agents API:** rejected as orchestration infrastructure (it cannot run Claude or Gemini,
has no worktree isolation, no guard-gated merge, no cross-vendor limit recovery, and its adjacent
product surface is sunsetting). Accepted only as an optional L3 **reviewer pool** (`openai` →
`codex`, `family: openai`) for cross-family review — a pool, not a platform (◆ 3c).

## Phases

### Phase 0 — measurements before any merge (one session, one day)

| # | Task | Evidence to record |
|---|---|---|
| 0.1 | Run `agent-canvas --prompt "Reply with exactly OK" --model <id> --headless --output-format json` once on this host (the binary is installed); resume with `--session <id> --model <other>`; read the model line | each flag's comment in `AgentAdapters.psm1`, the way `agy`'s carry incident comments |
| 0.2 | Settle ◆ **Decision 2**: is `@openhands/agent-canvas` the OpenHands project's own CLI (`npm view`, the binary's `--help`)? If not, the `openhands → agent-canvas` alias is a trap | a line in the SURVEY appendix and the adapter's comment |
| 0.3 | Check `mcode` (the Muse provider id the canvas branch adds) against CAO's real `ProviderType` enum (`cao/profiles.py:68-80` keeps the copy the drift test reads) | drift test green or a corrected id |
| 0.4 | Collect one quota-wall fixture per provider (OpenRouter 429 body, DeepSeek `insufficient_balance`, Ollama connection refused, agent-canvas wall) for phase 3.5 | fixture files under `tests/fixtures/quota/` |

### Phase 1 — collapse the four orchestration skills into one (docs lane, parallel)

`skills/agent-orchestration/SKILL.md` replaces `unattended-orchestration/SKILL.md`,
`swarm-orchestration/SKILL.md` and `herdr-orchestration/SKILL.md` (◆ 1a: herdr becomes one section
— "when a human-watched pane beats a background session"; recommended). `mcp-servers-setup` stays
separate (◆ 1c). Vocabulary: **L1 / L2 / L3** with a glossary mapping `Orchestrator/Session/Subagent`,
`Layer1/supervisor/worker` and `architect/engineer/reviewer` onto it. No number in the SKILL.md
— every value in `handoff.config.example.json` or `agent_orchestration.config.yaml`. Fix the
dangling pointers (add `orchestration.wave_caps` and `orchestration.forbidden_concurrent_git` to
the YAML with the values the 2026-07-30 plan specifies at `:964-975`, whose tests it already
contains verbatim at `:902-940` — or delete the pointers). Add a **link-check test that every
`SKILL.md` config pointer resolves to an existing key**, not merely an existing file. Keep both
runtimes (ADR 0008 §1: two lanes, one skill, shared formats, not shared code). ◆ 1b: retire the
swarm Python scaffold's `orchestrator_scaffold.py` and `role_router.py`; keep `providers/*.py`
(honest capability declarations; `ollama` and `deepseek_tui` are live) and `verification_runner.py`.
One setup entry point `skills/agent-orchestration/setup.{ps1,sh}` wrapping `-Init`,
`python -m cao check`, `setup_cao.py --check`. Write `infra/mcp-servers/cao-setup/README.md` (0 bytes
today).

### Phase 2 — merge the canvas worktree (after 0.1–0.3)

Flip `agent-canvas` to `experimental = $true` unless 0.1 passed; correct `resumeModelNote` to what
0.1 measured; `git merge --ff-only experiment/agent-canvas-orchestration`; restore or deliberately
record the stripped BOM in `tests/AdapterContract.Tests.ps1`; remove the worktree and its
`~/.serena/serena_config.yml` project row. All seven suites plus `tests/cao/` green.

### Phase 3 — one model catalogue, generated profiles, every provider reachable at L3

| # | Task |
|---|---|
| 3.1 | `AutoOS/catalog/llm-models.json` becomes the single model source: add `family`, `pool`, `tier` (`orchestrator`/`leaf`), `subscription`; extend the schema and AutoOS's drift test |
| 3.2 | `cao/catalog.py::load_model_catalog()` + `build_routing()` generate the `cao.routing.<class>` ladders from it; hand-written ladders stay as an override; `handoff.config.example.json` gains `cao.modelCatalog` |
| 3.3 | Pools `muse`, `openrouter`, `ollama`, `openhands` in `cao.pools` / `PROVIDER_BY_POOL` |
| 3.4 | `FAMILY_ORDER = (anthropic, google, meta, deepseek, openrouter, oss[, openai])`; pairing test: a reviewer never shares `family` with the implementer |
| 3.5 | `Classify-HandoffFailure` learns the phase-0 fixtures — two tests per wording (pass and fire) |
| 3.6 | Generate the eleven OpenHands agent profiles from `cao.levels` (`cao/openhands_profiles.py`); AutoOS installs the generated output (◆ 3d: agent-skills generates, AutoOS installs — recommended) |
| 3.7 | Retire `Get-AdapterInstallCommand`: the adapter detects; when missing it prints `autoos --only <component-id>`; AutoOS catalog gains `codewhale`, `grok`, `agent-canvas` |
| 3.8 | Cost ledger: `<stateDir>/cost.ndjson` per turn `{session, level, pool, model, wall_s, in_tok, out_tok, usd}` priced from 3.1; `-Status` prints per-level totals |
| 3.9 | `Test-HandoffArtifactsPresent`: the brief's `## Done means` list is checked against the tree before the guards (closes obs 27) |
| 3.10 | Re-read `dependsOn` per poll; add `stop-lane-<name>` (obs 24/25) |
| 3.11 | Level-2 identity: a `phases` block in `handoff.config.json` (phase → sessions → guard), a phase id in every session's vars and ledger events — so an L2 session spanning several L3 sessions has a record of its own |

◆ **3a** default L3 ladder for `mechanical`: recommended `antigravity → openrouter-free → deepseek
→ ollama`, `anthropic` for `review`. ◆ **3b** L3 through OpenHands or per-provider CLIs:
recommended **both** — CLIs where one exists (`claude`, `agy`), OpenHands for muse/openrouter/ollama.

### Phase 4 — AutoOS profile-management tab (GUI + TUI; depends only on 3.1 if ◆ 4d)

User-profile store `~/.autoos/profiles/<id>.json` (built-ins stay read-only seeds, fork on edit
with `derivedFrom`; ◆ 4a location, ◆ 4b no editing of built-ins — both recommended); read-time
overlay in `catalog.sh` / `AutoOS.Catalog.psm1`; `/api/profiles` CRUD + `export` + `import` copying
the `/api/config` backup-and-merge discipline in both `serve.py` and `AutoOS.Serve.psm1` (plus a
route-table parity test — they drifted once); `"profiles"` in `TABS` with add/duplicate/rename/
delete, Import (file input) and Export (Blob download), and a catalog browser reusing the component
cards and dependency badges; the same menu in both TUIs (`AutoOS.Ui.psm1`, `lib/linux/ui.sh` — the
latter has no profile code today); headless flags `--profile-{new,import,export}`. ◆ 4c: an export
never carries `answers` (public repo, hard rule 1). ◆ 4d: LLM/agent profiles as a second sub-tab in
a later pass (recommended).

## Sequencing and swarm safety

Phase 0 first (one session, measurements). Phase 2 next (two commits). Phase 1 in parallel in its
own worktree (docs only). Phase 3 after 1 and 2, as **one lane** with phase 2 (shared files).
Phase 4 after 3.1. Every phase is an L2 session; its tasks are L3 lanes under the merged skill's
own runner — the plan is also the skill's first real multi-level run.

## Decisions the operator must take before the marked steps

◆ 0 two repos (recommended) or one · ◆ 1a herdr folded in · ◆ 1b scaffold retired except providers
and verifier · ◆ 1c mcp-servers-setup untouched · ◆ 2 `openhands == agent-canvas`? · ◆ 3a default L3
ladder · ◆ 3b OpenHands vs CLIs (both) · ◆ 3c OpenAI reviewer pool · ◆ 3d who generates the OpenHands
profiles · ◆ 4a store location · ◆ 4b built-ins read-only · ◆ 4c exports carry no answers ·
◆ 4d LLM-profile sub-tab later.
