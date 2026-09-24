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

| Combo | Legs, exact refs in priority order | Role | Recommendation |
|---|---|---|---|
| `t1-orchestrator` | `opencode-zen/muse-spark-1.3-contributor-free` → `openrouter/meta/muse-spark-1.3-contributor` | orchestrator-1M, spark-only | **keep** |
| `spark-1.3-contributor` | same two legs as t1 | pinned single-model route | **keep** (identical legs; purpose-built name) |
| `t1-orchestrator-clean` | `openrouter/meta/muse-spark-1.3-contributor` | paid-only 1M | **keep** |
| `t1-orchestrator-free-only` | `opencode-zen/muse-spark-1.3-contributor-free` | zero spend 1M (zen promo only — the agy Opus leg stays out: 200k model in a 1M-declared combo would 400 instead of degrading) | **keep** |
| `t2-worker` | `gemini/gemini-3.8-flash` → `antigravity/gemini-3.7-flash-medium` → `groq/openai/gpt-oss-120b` → `cerebras/gpt-oss-120b` → `sambanova/gpt-oss-120b` → `cheaperinference/deepseek-v4-flash` → `cheaperinference/glm-4.5-air` → `cheaperinference/kimi-k3` → `openrouter/deepseek/deepseek-v4.1-flash` → `deepseek/deepseek-flash` → `opencode-zen/deepseek-v4.1-flash` | smart-reasoning | **keep** (agy gemini added 2026-09-24, ack 1.6s) |
| `t2-worker-clean` | `deepseek/deepseek-flash` → `openrouter/deepseek/deepseek-v4.1-flash` → `opencode-zen/deepseek-v4.1-flash` → `mistral/mistral-small-latest` | paid-only smart | **keep** |
| `t2-worker-free-only` | `gemini/gemini-3.8-flash` → `antigravity/gemini-3.7-flash-medium` → `groq/openai/gpt-oss-120b` → `cerebras/gpt-oss-120b` → `sambanova/gpt-oss-120b` | zero spend smart | **keep** |
| `t3-driver` | `mistral/mistral-code-latest` → `groq/qwen/qwen3.8-27b` → `cerebras/qwen-3.8-27b` → `cheaperinference/glm-4.5-air` → `cheaperinference/minimax-m2.7` → `mistral/mistral-small-latest` → `deepseek/deepseek-flash` → `opencode-zen/deepseek-v4.1-flash` | cheap driver (keyed-head: mistral-code first, free qwen 2nd/3rd) | **keep** |
| `t3-driver-clean` | `deepseek/deepseek-flash` → `mistral/mistral-small-latest` → `opencode-zen/deepseek-v4.1-flash` | paid-only driver | **keep** |
| `t3-driver-free-only` | `groq/qwen/qwen3.8-27b` → `cerebras/qwen-3.8-27b` | zero spend driver | **keep** |
| `t4-rag` | `cohere/command-a-03-2025` → `cohere/command-r-plus-08-2024` | RAG grounded QA (trial keys) | **keep** (only non-reasoning route) |
| `gemini-3.8-flash` | `gemini/gemini-3.8-flash` → `openrouter/google/gemini-3.8-flash` | pinned, intra-family fallback | **keep** |
| `deepseek-v4.1-flash` | `openrouter/deepseek/deepseek-v4.1-flash` → `opencode-zen/deepseek-v4.1-flash` | pinned, cheapest-first paid | **keep** |
| `opus-4-6` | `antigravity/claude-opus-4-6-thinking` → `cc/claude-opus-4-6` | pinned frontier reasoning (200k) | **keep** (new 2026-09-24; t1 stays spark-only, Opus lives here) |

## B. Gateway-only combos (referenced, NOT curated)

`opencode.jsonc` `providers.omniroute.models` addresses these, but they exist
only in the gateway's own store — no leg order in this repo, no drift gate:

| Combo | Known legs | Used by | Recommendation |
|---|---|---|---|
| `auto` | gateway built-in (verified 2026-09-24: **ack 1.3s**) | opencode default bootstrap | **keep as-is** (gateway built-in, nothing to curate) |
| `auto/smart` | gateway built-in (verified 2026-09-24: **ack 4.5s**) | opencode bootstrap | **keep as-is** |
| `auto/cheap` | gateway built-in (verified 2026-09-24: **ack 4.2s**) | opencode bootstrap | **keep as-is** |

## C. LiteLLM-only groups (no gateway combo — manual fallback only)

Present in `configuration/litellm/config.yaml` AND `opencode.jsonc`
`providers.litellm.models`. Two kinds: `*-paid` groups have no gateway
combo (manual fallback only, deliberately outside `combos.json`); the
`*-free-only` groups are hand-curated mirrors of the gateway combos of the
same name (minus legs LiteLLM cannot address — see the free-only mirror
test in `tests/run-tests.sh`):

| Group | Role | Recommendation |
|---|---|---|
| `t1-orchestrator-paid` | openrouter spark first, then zen flash | **keep** (fallback when free promo dies) |
| `t2-worker-paid` | paid smart legs | **keep** |
| `t3-driver-paid` | paid driver legs | **keep** |
| `t1-orchestrator-free-only` | zen spark-free only (no paid fallback, no fallbacks entry — fails loudly) | **keep** (new 2026-09-24) |
| `t2-worker-free-only` | gemini → groq → cerebras → sambanova (agy leg dropped: no LiteLLM transport) | **keep** (new 2026-09-24) |
| `t3-driver-free-only` | groq qwen → cerebras qwen (full mirror) | **keep** (new 2026-09-24) |

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
| Antigravity CLI | `antigravity` (oauth, free) — canonical id (`agy` is alias-only; `providers auth agy` → Unknown). `oauth start --provider antigravity --import-from-system --no-browser`, code-paste flow | LIVE 2026-09-24 (account-named connection, `oauth status` active). Refs: `antigravity/claude-opus-4-6-thinking[-high\|-medium\|-low]`, `antigravity/claude-sonnet-4-6[-high\|-medium\|-low]`, `antigravity/gemini-3.7-flash-{high,medium,low,tiered}`, `antigravity/gemini-3.1-pro-low`, `antigravity/gpt-oss-120b-medium`. Probes: opus-4-6-thinking → **ack 3.4s**; gemini-3.7-flash-medium → **ack 1.6s** | t1: opus dropped (200k leg in a 1M combo would 400); t2 + t2-free-only: gemini leg curated; Opus in pinned `opus-4-6` | **done** |
| Claude Code subscription | `claude` (oauth) — `providers auth claude-code --no-browser`, code-paste flow. Ref prefix is `cc/` | LIVE 2026-09-24. Refs: `cc/claude-opus-4-6[-high\|-medium\|-low]`, `cc/claude-opus-4-7`, `cc/claude-opus-5`, `cc/claude-sonnet-4-6`, `cc/claude-fable-5`, `cc/claude-haiku-4-5-20251001`. Probe: opus-4-6 → **429 quota reset 1h** (hops by design; overflow-only, not a head) | overflow leg in pinned `opus-4-6` ($0 marginal) | **done** |
| Qoder | binary `qodercli` 1.1.62 ships in `~/.qoder/bin/qodercli` (runs) but is NOT on PATH — the dashboard error verbatim. PAT via `QODER_PERSONAL_ACCESS_TOKEN` (api-keys.yml `qoder_pat`; env wins over `/login` per Qoder docs). Gateway `qoder` entry is OAuth/dashboard-only (no CLI flow) | Qoder CLI headless/ACP use now; t2/t3 qwen overflow only after a dashboard gateway connection | PATH+PAT automated 2026-09-24 (`qodercli` catalog component: install-if-missing, PATH append, PAT export). Gateway connection via dashboard; then I list + probe |
| GitHub Copilot | `github`/`copilot` (oauth, device flow) | plan picker: GPT-5.5, GPT-5.3-Codex, Claude Sonnet/Opus 5, Gemini 3.8 Flash, Kimi K3 (docs 2026-09) | CLOSED 2026-09-23: M365-only, no GitHub seat | — |
| Devin CLI Agentic Bridge | `devin-cli-agentic` (noauth, alias `dva`) — needs the `devin` binary (`devin acp`); catalog component `devin-cli` added 2026-09-24 (winget `CognitionAI.DevinCLI` on Windows, vendor `install.sh` on Linux/macOS) | `dva/*` already in the live catalog (opus-4-7-max, glm-5-2, kimi-k3, ... — no connection needed to list) | t1 candidate (opus-4-7-max) + GLM overflow | install via catalog, then interactive `devin auth login`, then `providers add devin-cli --oauth` (`--dry-run` proven) or dashboard; then probe |
| Devin API key | `devin` (cloud-agent) — wired 2026-09-24 (`devin` registry entry; connection added, key accepted) | NONE (`models devin` empty; broken upstream, issue #6142 — key accepted but no model sync) | — | blocked upstream; use the CLI path above |
| ZCode GLM Coding Plan | `zcode` (noauth tag, key needed in practice) | GLM 5.3 Max (dashboard only) | t2/t3 GLM overflow | a ZCode/GLM plan credential (connection added, test FAILS without it); likely superseded by Z.AI below |
| Z.AI (API key) | `zai` — wired 2026-09-24 (`z_ai` registry entry; connection added, key accepted) | Refs: `zai/glm-5.3`, `zai/glm-5.2`, `zai/glm-5.1`, `zai/glm-5`, `zai/glm-5-turbo`, `zai/glm-4.7`, `zai/glm-4.7-flash`. Probe: glm-5.3 → **429 insufficient balance** (key unfunded — no curation until topped up) | t2/t3 GLM overflow once funded | fund the Z.AI key; then re-probe + curate |
| OpenCode Free | `opencode` (noauth pool) | Refs incl `oc/gemini-3.8-flash`, `oc/glm-5.3`, `oc/deepseek-v4-flash-free` | auto/* replacement or t3 tail | connection added, test FAILS key check — needs a live chat probe with client key to prove the pool serves |
| Kilo/Codex/Cursor | `kilocode`/`codex`/`cursor-cli` | subscription models | only with those subscriptions | tell me which you hold |

Rules for any addition: OAuth/subscription bridges proxy a PERSONAL
subscription (single-user proxy tolerated, resale is not — same ToS note as
`api-keys.md`); NEVER into `*-clean` (training/logging terms unknown);
one authenticated leg-probe per leg before it enters `combos.json`.
