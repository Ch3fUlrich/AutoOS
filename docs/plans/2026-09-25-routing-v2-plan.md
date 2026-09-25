# Plan — routing v2, registry, skill v2

Executes [2026-09-25-routing-v2-spec.md](2026-09-25-routing-v2-spec.md). Order per D14. Every task
lists its bucket (spec §5.2, scored by hand until the resolver exists), the writer route, and the
review set (D2). Routes measured serving on 2026-09-25: `t2-worker-clean` (native DeepSeek),
`t2-worker-free-only` (GPT-OSS), `t3-driver` (Mistral Code), `t3-driver-free-only` (Qwen). Operator decision 2026-09-25 20:07Z: OpenRouter is **not** topped
up — every openrouter paid leg is permanently unavailable and leaves the routes. Free pools
(GPT-OSS, Qwen, Gemini free, Zen free Spark via opencode) first; smarter work on the `qoder` and
`agy` clients (own accounts) before paid DeepSeek; paid DeepSeek and Claude only as fallback.

## Working rules for every lane

- One lane = one branch + one sandbox clone (`autoos-agent --isolate`); briefs use the §8.2 BRIEF
  fields, name the skills to load, and pass `git -c user.name=… -c user.email=…` for commits.
- Writer route by bucket: S0–S1 → `t2-worker-free-only`, fallback `--client qoder|agy`; S2 →
  `--client qoder` or `agy`, fallback `t2-worker-clean` (DeepSeek); S3+ → decompose first; only
  undecomposable judgment work → Sonnet. Opus only for S4.
- Gate before review: NO-OP guard, diff inside the declared paths, report files == `git diff
  --name-only`, touched-part test filters green.
- Review: 1 cross-family API review (normal) or 2 + Sonnet closes (high). DeepSeek writer → Qwen or
  GPT-OSS reviewer; Sonnet writer → DeepSeek + Qwen.
- Tests: touched filters while working; both full suites + branch CI once per phase, in a separate
  guard worktree; then fast-forward `main`.
- Every bug hit becomes a rule (C2 format) in the same change.

## Phase 0 — helper lifecycle (Serena / Graphify), before any new lane

Measured 2026-09-25: `opencode.jsonc` starts a private Serena (uvx, plus the PowerShell and Bash
language servers) per opencode session; the spawner runs the client with a plain `subprocess.call`,
so leftovers survive a cancelled or timed-out worker; finished lane sessions stayed alive for 10 h and
kept a Graphify container on their worktree (stopped by hand).

| # | Task | Bucket | Writer | Review |
|---|---|---|---|---|
| Z1 | Spawner: client in its own process group (POSIX `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP`); after exit, timeout or Ctrl-C, terminate what is left of the group (TERM, then KILL after 5 s; Windows `taskkill /T`). Test with a fake client that leaves a sleeping child | S1 | DeepSeek | Qwen |
| Z2 | L1 routine: after reading a lane's DONE note, `claude stop <id>` and remove the merged, clean worktree; `l1_handoff.py` lists the candidates (built) | S0 | — (skill rule) | — |
| Z3 | Sandbox workers' Serena: measure RAM/start time of a private Serena vs `--lean`; the shared `:9121` server is not an option for parallel clones (one active project per server) | spike | DeepSeek | Qwen |
| Z4 | Graphify MCP: find where it is defined, why this session's connection closed, and stop lane sessions from starting one per worktree | spike + S1 | Sonnet (spike), DeepSeek | Qwen |

## Phase A — one registry (single source)

| # | Task | Bucket | Writer | Review |
|---|---|---|---|---|
| A1 | Field-level mapping table (old catalogs → registry paths) + `catalog/ai-registry.schema.json` | S2 | DeepSeek | Qwen |
| A2 | One-shot converter building `catalog/ai-registry.json` from `llm-models.json`, `providers.json`, `ide-models.json`, `combos.json` | S1 | GPT-OSS → DeepSeek | Qwen |
| A3 | `tools/registry.py check/validate` (rules of spec §3.1, incl. private-host rejection) + tests | S2 | DeepSeek | Qwen |
| A4 | `registry.py render` for combos.json, LiteLLM groups, opencode/Zed lists (retarget `sync-ide-models.py`), OpenHands profiles, `docs/models.md` section; semantic-equality gate vs today's files | S3 → split per target (5 × S1) | DeepSeek ×5 | Qwen |
| A5 | Switch consumers one per lane: `apply.sh`, `apply.ps1`, `lib/linux/install.sh` model reads, Windows psm1 equivalents, `mirror-litellm-env.py`, `sync-router-tiers.py`, `audit-router.py`, `sync-openhands-profiles.py`; then delete the old catalogs; CI drift check in both suites | S4 → split per consumer (8 × S1–S2) | DeepSeek | **high**: DeepSeek-family excluded → Qwen + GPT-OSS, Sonnet closes |
| A6 | Effort ladders: registry → spawner `reasoning_effort`, Zed defaults; spike first on how opencode can offer `xhigh` without a static variants block (measured to break the provider) | S2 + spike | spike: Sonnet; impl: DeepSeek | Qwen |
| A7 | Gemini routes needing > high: openrouter leg first (data only); session-id forwarding spike (what OmniRoute sends to OpenRouter) | S1 + spike | DeepSeek | Qwen |
| A8 | Upstream data PR to OmniRoute's model table (spark-1.3, deepseek-v4.1 effort ladders) | S1 | DeepSeek | Qwen; **operator approves before it is submitted** |

## Phase B — resolver v2

| # | Task | Bucket | Writer | Review |
|---|---|---|---|---|
| B1 | Card v2 parse/normalize + v1 compatibility in `tools/autoos_routing.py` | S2 | DeepSeek | Qwen |
| B2 | `measure()`: features (git, Serena → Omnigraph → grep fallback, token count) + client_state from the existing sign-in checks | S2 | DeepSeek | Qwen |
| B3a | Bucket + effort tables as pure functions + golden boundary tests | S1 | GPT-OSS → DeepSeek | Qwen |
| B3b | Filters + `no_route` + override-after-filters | S2 | DeepSeek | Qwen |
| B3c | Scoring, pick, time tie-break/defer, emit + explain | S2 | DeepSeek | Qwen |
| B3d | Fixture `tests/fixtures/routing-2026-09-25.json` from the recorded tasks + reproduction test | S2 | Sonnet (needs session history) | DeepSeek |
| B4 | Track-record writer in the spawner, overlay load, Beta `p` | S2 | DeepSeek | Qwen |
| B5 | CLI `route`, `context`; MCP `route`, `list_agents`, `context`, `respond` | S2 | DeepSeek | Qwen |
| B6 | Gate in the spawner: diff-in-paths, report-vs-diff, auto risk raise, escalation ladder + terminal `failed` | S3 → split (3 × S1–S2) | DeepSeek | **high** → Qwen + GPT-OSS, Sonnet closes |

## Phase C — orchestration skill v2

| # | Task | Bucket | Writer | Review |
|---|---|---|---|---|
| C1 | `tools/skill-rules.py check` (ids, near-duplicates, ≤200 chars, source) + CI hook | S1 | GPT-OSS → DeepSeek | Qwen |
| C2 | Rewrite the skill as rules; harvest every lesson from DONE notes and this session's error list | S3, judgment | Sonnet | DeepSeek + Qwen |
| C3 | BRIEF/REPORT templates; the spawner parses REPORT fields | S2 | DeepSeek | Qwen |
| C4 | Handoff: caps in `policy`, state-file template, L1 handoff procedure in the skill | S1 | DeepSeek | Qwen |
| C5 | Skill discovery: spike each client's native skills dir (opencode, qwen, gemini, codex, qoder, agy), then installer links (sh + ps1) + CI link check | spike + S2 | spike: Haiku/Sonnet (web); impl: DeepSeek | **high** (installers touch user dirs) → Qwen + GPT-OSS, Sonnet closes |

## Phase D — messaging

| # | Task | Bucket | Writer | Review |
|---|---|---|---|---|
| D1 | Spike: MCP Python SDK Tasks support + whether Claude Code consumes MCP Tasks | spike | Haiku/Sonnet | — |
| D2 | Task lifecycle states in the spawner + MCP server; `input_required` via question files; `respond` | S3 → split (2 × S2) | DeepSeek | Qwen |

## Phase E — probes (free legs only)

| # | Task | Bucket | Writer | Review |
|---|---|---|---|---|
| E1 | Recall probe tool (multi-needle, 32k–500k) → overlay; run on free legs (Zen Spark via opencode, Gemini free, Qwen/GPT-OSS pools) | S2 | DeepSeek | Qwen |
| E2 | Effort probe, n ≥ 5 per rung → overlay | S1 | DeepSeek | Qwen |
| E3 | RTK A/B on the same worker tasks → D19 decision | S2 | DeepSeek | Qwen |

## Operator steps (not agent work)

1. ~~Top up OpenRouter~~ — declined 2026-09-25; paid openrouter legs leave the routes.
2. Approve the `autoos-agent` MCP server in Claude Code (`claude mcp list` shows it pending).
3. Run `bash configuration/docker/ai-stack/ai-stack.sh migrate` (plan), then `--yes`; I run the
   end-to-end checks after.
4. After the migration: Semaphore template 61 for ports 4096 and 20128.
5. OmniRoute dashboard: connect Qoder and Antigravity (D13) and the Claude Code account.
6. A8 upstream PR: on hold until the operator decides; not prepared.

## Done when

Spec §11 holds; both full suites and branch CI green per phase; `main` fast-forwarded per phase; the
DONE note per phase lists what was verified live vs only tested.
