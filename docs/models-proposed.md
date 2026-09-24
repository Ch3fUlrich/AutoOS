# Combos evaluation (for the operator — keep / drop / merge)

IDS RENAMED 2026-09-23: `tier1` → `t1-orchestrator`, `tier2` → `t2-worker`,
`tier3` → `t3-driver`, `rag` → `t4-rag` (old ids retired — delete them from
the gateway store after applying; `apply` only creates). Credit combos
dropped same day (auto-demote makes deliberate burn redundant).

Source of leg lists: `configuration/omniroute/combos.json` (13 curated
combos). Gateway truth may hold **more** (`:20128` answered 401
unauthenticated, so the live list is unverified here — finish with
`omniroute combos list` authenticated, or `python tools/audit-router.py`
(live), which reports repo-missing and live-extra combos).

## Roles, plainly

- **smart-reasoning (`t2-worker`)**: the adaptive tier — free pools first,
  paid overflow after. Cost/quality decide membership, not context size.
- **paid-only smart (`t2-worker-clean`) / paid-only driver
  (`t3-driver-clean`)**: same roles minus every free/training leg. For
  sensitive data (no prompt training), not for saving money.
- **credit burn, driver tier** (retired `t3-driver-credit`): chains whose
  ONLY job was spending stored cerebras/sambanova/partner balances.
  Redundant since breaker fast-skip + cooldowns demote exhausted legs
  automatically — see Latency below.
- **RAG (`t4-rag`)**: cohere command-a → command-r-plus. Retrieval-grounded
  answering over supplied documents (quotes/citations), not reasoning or
  codegen. Use for grounded QA over docs; never as an orchestrator, worker,
  or driver substitute.

## Latency: why a long chain still answers fast

Chains are `priority` with hop-on-429/402/5xx: a healthy head answers on the
first leg (no chain walk). Dead legs cost at most two cheap round-trips
(`providerBreaker.apikey.failureThreshold = 2`, 30s skip), 402/429 hop
immediately without retries, and streaming starts tokens on the first
healthy leg. Worst case per request stays well under a 5s budget. The
breaker + cooldowns ARE the automatic move-dead-providers-last mechanism —
quota_exhausted never retries, repeated failures cool down. No manual
reordering, no separate credit combos needed.

## `t3-driver` free-first? No — keyed-head.

`t3-driver` LEADS with keyed `mistral-code-latest` (same-key free pool, then
billed), free legs (groq/cerebras qwen) second and third. So no, it is not
free-first end to end. `t3-driver-free-only` (groq + cerebras qwen) is the
strict free answer.

## A. Curated combos (`combos.json` — fully managed)

A curated combo = leg order owned by this repo (`combos.json`), validated
against the live catalog on apply, drift-gated in CI (`audit-router.py`,
`sync-router-tiers.py --check`). A gateway combo = a name that exists only
in the gateway store (e.g. `auto/*`): zero-setup bootstrap, no repo
curation, no drift gate, legs can change under you. Use gateway combos to
boot, curated combos to work.

| Combo | Legs (priority order) | Role | Recommendation |
|---|---|---|---|
| `t1-orchestrator` | zen spark-free → openrouter spark-contributor | orchestrator-1M | **keep** |
| `spark-1.3-contributor` | same as t1 | pinned single-model route | **keep** (identical legs; purpose-built name) |
| `t1-orchestrator-clean` | openrouter spark-contributor | paid-only 1M | **keep** |
| `t1-orchestrator-free-only` | zen spark-free alone | zero spend 1M | **keep** (new 2026-09-23) |
| `t2-worker` | gemini-3.8-flash → groq gpt-oss → cerebras → sambanova → cheapinference ×3 → openrouter deepseek → deepseek-direct → zen flash | smart-reasoning | **keep** |
| `t2-worker-clean` | deepseek-direct → openrouter deepseek → zen flash → mistral-small | paid-only smart | **keep** |
| `t2-worker-free-only` | gemini → groq → cerebras → sambanova | zero spend smart | **keep** (new 2026-09-23) |
| `t3-driver` | mistral-code → groq qwen → cerebras qwen → cheapinference ×2 → mistral-small → deepseek-direct → zen flash | cheap driver | **keep** |
| `t3-driver-clean` | deepseek-direct → mistral-small → zen flash | paid-only driver | **keep** |
| `t3-driver-free-only` | groq qwen → cerebras qwen | zero spend driver | **keep** (new 2026-09-23) |
| `t4-rag` | cohere command-a → command-r-plus | RAG specialist | **keep** (only non-reasoning route) |
| `gemini-3.8-flash` | gemini free → openrouter google twin (paid) | pinned, intra-family fallback | **keep** |
| `deepseek-v4.1-flash` | openrouter deepseek (paid) → zen flash | pinned, cheapest-first paid | **keep** |

## B. Gateway-only combos (referenced, NOT curated)

`opencode.jsonc` `providers.omniroute.models` addresses these, but they exist
only in the gateway's own store — no leg order in this repo, no drift gate:

| Combo | Known legs | Used by | Recommendation |
|---|---|---|---|
| `auto` | gateway built-in (unverified) | opencode default bootstrap | **decide**: pin legs into `combos.json`, or drop the refs and default to `t2-worker` |
| `auto/smart` | gateway built-in (unverified) | opencode bootstrap | **decide**: same as `auto` |
| `auto/cheap` | gateway built-in (unverified) | opencode bootstrap | **decide**: same as `auto` |

## C. LiteLLM-only groups (no gateway combo — manual fallback only)

Present in `configuration/litellm/config.yaml` AND `opencode.jsonc`
`providers.litellm.models`, deliberately outside `combos.json`:

| Group | Role | Recommendation |
|---|---|---|
| `t1-orchestrator-paid` | openrouter spark first, then zen flash | **keep** (fallback when free promo dies) |
| `t2-worker-paid` | paid smart legs | **keep** |
| `t3-driver-paid` | paid driver legs | **keep** |

## D. Free-model bench (measured 2026-09-23, OpenRouter `:free` catalog)

21 free models right now. None are proven in an agentic loop — capability
needs a leg-probe (one real task per candidate), not catalog reading. Precise
names for future legs (no more bare provider labels):

| Candidate | Context | Job it could take | Caveat |
|---|---|---|---|
| `nvidia/nemotron-3-ultra-550b-a55b:free` | 1M | t1-class reasoning standby | unproven; largest free reasoning model |
| `nvidia/nemotron-3.5-lightning:free` | 1M | fast t2/t3 overflow | unproven |
| `thinkingmachines/inkling:free`, `inkling-small:free` | 1M | t2 reasoning overflow | unproven |
| `qwen/qwen3.8-27b:free` | 262k | t3 qwen overflow beside groq/cerebras | same family, direct OpenRouter door |
| `poolside/laguna-s-2.1:free` | 262k | codegen overflow (Poolside trains code) | unproven in loop |
| `nex-agi/nex-n2.5-pro:free` | 262k | smart overflow | unproven |
| `google/gemma-4-31b-it:free`, `gemma-4-26b-a4b-it:free` | 262k | light t3 overflow | small instruction models |
| `z-ai/glm-5.2:free` | 32k | small jobs only | 32k window rules out 128k-class work |
| `cohere/north-mini-code:free` | 256k | codegen overflow | unproven |

Rule: a candidate enters a combo only after an authenticated leg-probe
(`simulate --explain` then one real chat). Until then it stays in this
table, not in `combos.json`.

## E. Proposed free-provider additions (researched + probed 2026-09-23)

Live gateway state: 13 configured connections (cerebras, cheaperinference,
cloudflare-ai, cohere, deepseek, gemini, groq, huggingface, mistral,
muse-code, opencode-zen, openrouter, sambanova) + 2 added this session
(`zcode`, `opencode` — see probe results). `muse-code` is registered
but unreferenced — delete it with `omniroute providers remove muse-code`
once no combo needs it (all green today).

### Probed 2026-09-23/24 (commands + outcomes)

- `omniroute providers add zcode/opencode --no-credential --yes` → both
  connections created. `providers test` FAILS both ("no API key
  configured"): `zcode` (GLM Coding Plan) needs a plan credential despite
  the `noauth` tag; `opencode` (OpenCode Free pool) likewise fails the
  key check. `omniroute models zcode|qoder|claude` → "No models found"
  (catalog populates per-connection only after auth).
- **Z.AI wired 2026-09-24**: `z_ai` key already in `api-keys.yml` was inert
  (no registry entry → `apply.*` skipped it, no combo could reference it).
  Added `z_ai → zai` to `catalog/providers.json` (+ example + docs table —
  registry-driven, so apply/mirror/sync pick it up with no other edits).
  `providers add zai --credential-env` → connection created; `providers
  test zai` → "Provider test not supported" (no test probe for this
  provider — NOT a credential failure, key was accepted). `models zai` →
  7 GLM models: 5.3, 5.2, 5.1, 5, 5 Turbo, 4.7 Flash, 4.7. The `models`
  command shows display names only (verified: gemini shows "Gemini 2.5
  Flash" too), so exact combo refs + one chat probe per leg still needed
  before curating (phantom-leg rule).
- `devin-cli-agentic` / alias `dva` → `providers add` rejects both
  ("Invalid provider" / "Invalid request"): the Devin bridge onboards via
  dashboard/OAuth, not CLI add. Unblocked, not installable from here.
- `omniroute models --search "opus 4"` → Opus 4.5–4.8 rows exist ONLY via
  `cinf` (CheaperInference resale = paid) and opencode-zen. No
  qwen3.8-max, no glm-5.3 in the catalog — dashboard free-ranking models
  appear only after their connections authenticate.
- `omniroute setup-claude --dry-run` → needs `--api-key` (401 without):
  run `omniroute setup-claude --dry-run --api-key <AUTOOS_OMNIROUTE_KEY>`
  yourself (key from `api-keys.yml`, never chat/paste it here).
- Local `agy models` (your login): gemini-3.8/3.7/3.6-flash
  (low/med/high), gemini-3.1-pro, claude-sonnet-4.6 (Thinking),
  claude-opus-4.6-thinking, gpt-oss-120b-medium. NO Opus 4.7 (that's the
  Devin bridge, separate connection).

### CLI Code vs CLI Agents vs ACP (OmniRoute CLI-TOOLS.md, v3.8.50)

- **CLI Code** (26 tools): coding CLIs pointed AT OmniRoute
  (`ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` → `:20128`). claude, codex,
  opencode all `full` base-URL support with `setup-*` recipes
  (`setup-claude`, `setup-codex`, `setup-opencode`, all `--dry-run`able)
  plus the zero-write launcher `omniroute run <target>`. Recipe for the
  Claude subscription: `setup-claude` writes `~/.claude/settings.json`
  env — automatable in `apply.*` style (dry-run first, backup, idempotent
  merge). Antigravity is `none`/`mitm`: it CANNOT point at OmniRoute.
- **CLI Agents** (10 tools: openclaw, goose, open-interpreter, warp...):
  same flow, broader scope — autonomous (often long-running) agents using
  OmniRoute as their model backend. Use for unattended lanes that outlive
  one CLI session.
- **ACP Agents** (reverse flow): OmniRoute SPAWNS claude/codex/opencode/
  aider/qwen/goose via stdio/ACP as backend engines
  (`acpSpawnable: true`). Antigravity is NOT spawnable — so Antigravity's
  only integration is the `agy` OAuth provider below.

### Connection matrix (what each step needs)

| Provider | OmniRoute id / connect | Models behind it | Proposed legs | Needs from you |
|---|---|---|---|---|
| Antigravity CLI | `agy` (oauth, free) — `omniroute providers auth agy` (browser popup) or `omniroute oauth start --provider antigravity`, then `providers test agy` | Opus 4.6 Thinking, Sonnet 4.6 Thinking, Gemini 3.8-flash-high (per local login) | t1: `agy/claude-opus-4.6-thinking` after zen-free; t2: `agy/gemini-3.8-flash-high` | BLOCKED 2026-09-23: CLI login ≠ gateway connection (`providers test agy` → "not found", `oauth status` empty). `--import-from-system` answers 401 without server admin context. Run `omniroute oauth start --provider antigravity --import-from-system` in YOUR terminal (reuses the agy login, no second browser flow), or the browser flow; then I test + probe + curate |
| Claude Code subscription | `claude`/`cc` (oauth) — `omniroute providers auth claude-code` (browser flow) | subscription models (Opus/Sonnet per plan) | t1 overflow after openrouter paid (subscription = $0 marginal) | OPERATOR AUTH PENDING 2026-09-23 (same CLI-login≠gateway-connection gap as agy); `setup-claude` pipeline wiring landed in AutoOS (`Set-AutoOSClaudeGateway`/`route_claude_to_gateway`, catalog postInstall) |
| Qoder | `qoder` (oauth, free) — `providers auth qoder` | Qwen3.8-Max-Preview (per dashboard ranking) | t2/t3 qwen overflow | OPERATOR AUTH PENDING 2026-09-23 (`providers test qoder` → "not found") |
| GitHub Copilot | `github`/`copilot` (oauth, device flow) | plan picker: GPT-5.5, GPT-5.3-Codex, Claude Sonnet/Opus 5, Gemini 3.8 Flash, Kimi K3 (docs 2026-09) | CLOSED 2026-09-23: M365-only, no GitHub seat | — |
| Devin CLI Agentic Bridge | `devin-cli-agentic` (noauth) | Opus 4.7 High (per dashboard) | t1 candidate — highest ceiling if it probes OK | CLI add rejected; onboard via dashboard, then I probe legs |
| ZCode GLM Coding Plan | `zcode` (noauth tag, key needed in practice) | GLM 5.3 Max | t2/t3 GLM overflow | a ZCode/GLM plan credential (connection added, test FAILS without it) |
| Z.AI (API key) | `zai` — wired 2026-09-24 (`z_ai` registry entry; connection added, key accepted) | GLM 5.3, 5.2, 5.1, 5, 5 Turbo, 4.7 Flash, 4.7 (`models zai`) | t2/t3 GLM overflow (replaces the blocked zcode row if legs probe) | exact combo refs + one chat probe per leg (display names ≠ refs); then curate |
| OpenCode Free | `opencode` (noauth pool) | rotating free set | auto/* replacement or t3 tail | connection added, test FAILS key check — needs a live chat probe with client key to prove the pool serves |
| Kilo/Codex/Cursor | `kilocode`/`codex`/`cursor-cli` | subscription models | only with those subscriptions | tell me which you hold |

Rules for any addition: OAuth/subscription bridges proxy a PERSONAL
subscription (single-user proxy tolerated, resale is not — same ToS note as
`api-keys.md`); NEVER into `*-clean` (training/logging terms unknown);
one authenticated leg-probe per leg before it enters `combos.json`.

### M365 vs GitHub Copilot (your question)

No — not the same product. **Microsoft 365 Copilot** (BizChat, ~$30/seat on
top of M365) is Graph-grounded office work (GPT-5.x + optional Claude picker
for M365 users, Word/Excel/Teams). **GitHub Copilot** (Pro $10 / Business
$19) is the dev tool with the multi-model picker above. OmniRoute's
`github` provider is the latter (device flow). With M365-only and no GitHub
seat, that row is closed — confirm which seat you hold.
## F. How to finish this evaluation

1. `omniroute combos list` (authenticated) → append any live-extra combos to
   section B with the same columns.
2. `python tools/audit-router.py` (live) → it diffs repo vs gateway store
   (missing/extra/leg-drift) — treat its output as the working list.
3. For each **review/decide** row: keep, drop (remove refs from
   `opencode.jsonc` + tier-profiles + Zed writers together — the audit fails
   partial removals), or merge (fold legs into a kept combo).
4. Re-run `sync-router-tiers.py --check` + `audit-router.py --offline` after
   every change; leg add/edit needs a live leg-probe (one direct `:20128`
   chat per leg — `simulate` is not proof).
