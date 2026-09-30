# Combos evaluation (for the operator — keep / drop / merge)

## 2026-09-30 refresh — status at a glance

Re-read against `configuration/omniroute/combos.json` (15 curated combos,
T2FREE → DSBACK → MISTRALFIX → CTXAUDIT → CTXFIX → FREEKEYS-2c lineage) and
today's provider state. **Nothing has been applied to the gateway — operator
approval (OS-32) is pending; approve or adjust §A/§F.**

**Fleet pins in force (D-173, 2026-09-30).** Orchestrators run
`omniroute/deepseek-v4.1-flash#max` (opencode-zen free and the spark gateway
leg were the rate-limited/502 heads). Leaves spawn on the live `vertex/`
provider: leaf-implementer `vertex-gemini-3.1-pro-preview`, leaf-reviewer
`vertex-claude-sonnet-4-5`, plus `vertex-gemini-2.5-flash` and
`vertex-deepseek-v4-flash` — pinned in
`tools/render-opencode-container-config.py` so `ai-stack.sh init` cannot
revert them.

**What changed since the 2026-09-24 revision.**

- The free band was rebuilt: `scw/` (Scaleway), `nebius/` and `free-ai/qwen7b`
  carry the free legs; `cheaperinference/*` is omitted (wallet exhausted
  2026-09-27) and `samba/*` stays omitted; `mistral-small-latest` left every
  route (0 rpm on the plan); `deepseek/deepseek-flash` heads the `-clean`
  twins again after the operator's top-up.
- Contexts were re-audited against the live catalog: the `deepseek-v4.1-flash`
  leg was fixed to `deepseek/deepseek-v4-flash` (1M), `gemini-3.8-flash` is
  1M, `opus-4-6` is 1M. One open conflict: `meta-api/muse-spark-1.3-contributor`
  (catalog 128k vs registry/briefs 1M) — one >128k probe settles it.
- Vertex is the first credit provider live in the gateway (`vertex/`,
  65 models; $250 cap; **$0 spent** so far); the rest of the FREEKEYS-2 set
  still has zero registry rows — see §F.

IDS RENAMED 2026-09-23: `tier1` → `t1-orchestrator`, `tier2` → `t2-worker`,
`tier3` → `t3-driver`, `rag` → `t4-rag` (old ids retired — delete them from
the gateway store after applying; `apply` only creates). Credit combos
dropped same day (auto-demote makes deliberate burn redundant).

Source of leg lists: `configuration/omniroute/combos.json` (15 curated
combos, refreshed 2026-09-30 — §A below). Gateway truth may hold **more** (`:20128` answered 401
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

## `t3-driver` heads the free band now.

`t3-driver` (2026-09-30 refresh) LEADS with the free band (`scw/` +
`nebius/`); `mistral/mistral-code-latest` is the first paid leg, then
`deepseek/deepseek-flash` and the `meta-api` escalation. `t3-driver-free-only`
(scw + nebius + free-ai) is the strict free answer.

## Fleet model pins (live 2026-09-30 — what the orchestrators and leaves run)

Operator order 2026-09-30: the orchestration and worker heads were being
rate-limited or answer 502 (opencode-zen free, the spark gateway leg), so the
fleet was re-pinned. The pins are enforced at render time in
`tools/render-opencode-container-config.py`, not just in the live config, so
`ai-stack.sh init` re-applies them instead of reverting them.

| Agent | Model | Why |
|---|---|---|
| `orchestrator`, `suborchestrator` | `omniroute/deepseek-v4.1-flash#max` | 1M context (catalog 1000000), paid, not rate-limited; `#max` effort restored 2026-09-30 |
| `leaf-implementer` | `omniroute/vertex-gemini-3.1-pro-preview` | fast, 1M window, no free-pool queue |
| `leaf-reviewer` | `omniroute/vertex-claude-sonnet-4-5` | cross-family to the qoder/Qwen writers |

### Vertex AI (`vertex_ai`) — working again 2026-09-30

Earlier `vertex/*` answered `429 all vertex accounts have exhausted their
quota (reset after ~5m)`; it serves again, so spawning subagents from Vertex is
possible. **Per-leg probe 2026-09-30 (gateway `/v1/chat/completions`, one
`ready` prompt each):**

| Gateway leg | Result | Fleet alias | Window | Fleet role |
|---|---|---|---|---|
| `vertex/gemini-3.1-pro-preview` | **200 OK** | `vertex-gemini-3.1-pro-preview` | 1M | leaf-implementer head |
| `vertex/gemini-2.5-flash` | **200 OK** | `vertex-gemini-2.5-flash` | 1M | cheap overflow |
| `vertex/claude-sonnet-4-5` | **501** "not implemented, or supported, or enabled" | `vertex-claude-sonnet-4-5` | 200k | unusable until Claude is enabled in the Vertex Model Garden |
| `vertex/DeepSeek-V4-Flash` | **400** "Expected input to contain field: 'messages'" | `vertex-deepseek-v4-flash` | 1M | unusable — gateway payload bug, routed for a fix |

The 501 is not quota: it is an account entitlement gap (Vertex serves only the
Gemini models for this project). The 400 is a transport bug in the gateway's
route for that model, not a client error — the request carried `messages`.

Quota semantics: the 429 is per-account and resets on a ~5-minute window, so a
Vertex leg behaves like any other chain head — the breaker hops past it, it is
never a hard failure. Budget: the `vertex_ai` credit cap is held at $250 by
L1-main (tiny probes + cost-efficient legs only). Vertex has **no
`catalog/ai-registry.json` row yet** (same gap as the 13 FREEKEYS-2 keyed
providers), and no curated combo references `vertex/*` yet, so what is live
today is the gateway connection plus the fleet *agent* models; combo changes
stay PENDING-APPROVAL (OS-32).

**Reviewer default moved off the dead leg** (same day): `leaf-reviewer` was
pinned to `vertex-claude-sonnet-4-5`, which cannot serve, so every default leaf
review would have failed. It is now `omniroute/nemotron-3-ultra-free`
(`nvidia/nemotron-3-ultra-550b-a55b:free`, 1M, free, family NVIDIA — independent
of both the Gemini implementer and the Qwen writers), with
`omniroute/deepseek-v4.1-flash` as the documented paid fallback. If the
operator enables Claude on Vertex, the reviewer can move back
(`omniroute/vertex-claude-sonnet-4-5`). Pinned in
`tools/render-opencode-container-config.py` (`FLEET_AGENT_MODELS` +
`FLEET_EXTRA_MODELS`) so `ai-stack.sh init` cannot revert it.

## A. Curated combos (`combos.json` — fully managed)

A curated combo = leg order owned by this repo (`combos.json`), validated
against the live catalog on apply, drift-gated in CI (`audit-router.py`,
`sync-router-tiers.py --check`). A gateway combo = a name that exists only
in the gateway store (e.g. `auto/*`): zero-setup bootstrap, no repo
curation, no drift gate, legs can change under you. Use gateway combos to
boot, curated combos to work.

| Combo | Legs, exact refs in priority order | Role | Recommendation |
|---|---|---|---|
| `t1-orchestrator` | `gemini/gemini-3.8-flash` → `scw/qwen3-235b-a22b-instruct-2507` → `nebius/zai-org/GLM-5.3-Flash` → `scw/mistral-small-3.2-24b-instruct-2506` → `meta-api/muse-spark-1.3-contributor` | orchestrator, 1M | **keep** (free band heads; muse-spark paid escalation last) |
| `spark-1.3-contributor` | `meta-api/muse-spark-1.3-contributor` | pinned single-model route | **keep** (Meta Model API contributor leg; trains on prompts — never in a `-clean` route) |
| `t1-orchestrator-clean` | — (dropped) | — | **dropped 2026-09-30** (contributor-only/paid; in `combos.json` `"omitted"`) |
| `t1-orchestrator-free-only` | `gemini/gemini-3.8-flash` → `scw/qwen3-235b-a22b-instruct-2507` → `nebius/zai-org/GLM-5.3-Flash` → `scw/mistral-small-3.2-24b-instruct-2506` | zero spend, 1M | **keep** |
| `t1-orchestrator-paid` | `meta-api/muse-spark-1.3-contributor` | paid-only 1M | **keep** (now curated here too; was LiteLLM-only) |
| `t2-worker` | `gemini/gemini-3.8-flash` → `agy/gemini-3.7-flash-high` → `scw/qwen3-235b-a22b-instruct-2507` → `scw/mistral-small-3.2-24b-instruct-2506` → `nebius/zai-org/GLM-5.2` → `deepseek/deepseek-flash` → `meta-api/muse-spark-1.3-contributor` → `free-ai/qwen7b` | smart-reasoning, 128k | **keep** (free band rebuilt; `free-ai/qwen7b` tail) |
| `t2-worker-clean` | `deepseek/deepseek-flash` | paid-only smart, 128k | **keep** (`mistral-small-latest` out — 0 rpm; `mistral-code-latest` barred — trains on prompts) |
| `t2-worker-free-only` | `gemini/gemini-3.8-flash` → `agy/gemini-3.7-flash-medium` → `scw/qwen3-235b-a22b-instruct-2507` → `scw/mistral-small-3.2-24b-instruct-2506` → `nebius/zai-org/GLM-5.2` → `free-ai/qwen7b` | zero spend, 128k | **keep** |
| `t2-orchestrator` | `agy/claude-opus-4-6-thinking` | small-scope orchestration, 1M | **keep** (context re-audited to 1M) |
| `t3-driver` | `scw/mistral-small-3.2-24b-instruct-2506` → `nebius/zai-org/GLM-5.2` → `scw/qwen3-235b-a22b-instruct-2507` → `mistral/mistral-code-latest` → `deepseek/deepseek-flash` → `meta-api/muse-spark-1.3-contributor` | cheap driver, 128k | **keep** (free band heads; `mistral-code-latest` first paid leg) |
| `t3-driver-clean` | `deepseek/deepseek-flash` | paid-only driver, 128k | **keep** |
| `t3-driver-free-only` | `scw/mistral-small-3.2-24b-instruct-2506` → `nebius/zai-org/GLM-5.3-Flash` → `scw/qwen3-235b-a22b-instruct-2507` → `free-ai/qwen7b` | zero spend, 128k | **keep** |
| `t4-rag` | `cohere/command-a-03-2025` → `cohere/command-r-plus-08-2024` | RAG grounded QA (trial keys) | **keep** (only non-reasoning route) |
| `gemini-3.8-flash` | `gemini/gemini-3.8-flash` | pinned, 1M | **keep** |
| `deepseek-v4.1-flash` | `deepseek/deepseek-v4-flash` | pinned, 1M | **keep** (leg fixed 2026-09-30 from `deepseek/deepseek-flash`) |
| `opus-4-6` | `agy/claude-opus-4-6-thinking` | pinned frontier reasoning, 1M | **keep** |

`combos.json` `"omitted"` today: `cheaperinference/glm-5.2`,
`cheaperinference/kimi-k3`, `samba/MiniMax-M3`, `samba/gpt-oss-120b`,
`t1-orchestrator-clean`.

## B. Gateway-only combos (referenced, NOT curated)

`opencode.jsonc` `providers.omniroute.models` addresses these, but they exist
only in the gateway's own store — no leg order in this repo, no drift gate:

| Combo | Known legs | Used by | Recommendation |
|---|---|---|---|
| `auto` | gateway built-in (verified 2026-09-24: **ack 1.3s**) | opencode default bootstrap | **keep as-is** (gateway built-in, nothing to curate) |
| `auto/smart` | gateway built-in (verified 2026-09-24: **ack 4.5s**) | opencode bootstrap | **keep as-is** |
| `auto/cheap` | gateway built-in (verified 2026-09-24: **ack 4.2s**) | opencode bootstrap | **keep as-is** |

Retired old-scheme ids (`tier1`, `tier1-clean`, `tier2`, `tier2-clean`,
`tier3`, `tier3-clean`, `rag`, `tier1-paid`, `tier2-paid`, `tier3-paid`,
`tier2-credit`, `tier3-credit` — not the new `t*-orchestrator-paid` groups,
which are live) were deleted from the live store 2026-09-24 (verified:
store holds only the new ids + built-ins). `apply.*` creates combos from
`combos.json` and prunes exactly the ids listed in its `"retired"` array,
never any other store combo — and both suites assert the exact combo name
list plus a retired-ids regression test (read from that array), so a
resurrection fails CI before it reaches any gateway.

## C. LiteLLM groups (refreshed 2026-09-30)

Present in `configuration/litellm/config.yaml` AND `opencode.jsonc`
`providers.litellm.models`. `t2-worker-paid`/`t3-driver-paid` have no
gateway combo (hand-curated fallback chains, deliberately outside
`combos.json`); `t1-orchestrator-paid` is registry-driven since MUSEAPI and
also exists as a curated combo (§A). The `-free-only` groups are
hand-curated mirrors of the gateway combos of the same name (minus legs
LiteLLM cannot address — see the free-only mirror test in
`tests/run-tests.sh`):

| Group | Legs / role | Recommendation |
|---|---|---|
| `t1-orchestrator-paid` | meta-api muse-spark (registry-driven) | **keep** |
| `t2-worker-paid`, `t3-driver-paid` | `deepseek/deepseek-flash` only; wait for a re-approved chain | **keep** (deepseek funded again) |
| `t1-orchestrator-free-only` | gemini → scw qwen → nebius GLM-5.3 → scw mistral | **keep** |
| `t2-worker-free-only` | gemini → scw qwen → scw mistral → nebius GLM-5.2 → free-ai qwen7b (agy dropped: no LiteLLM transport) | **keep** |
| `t3-driver-free-only` | scw mistral → nebius GLM-5.3 → scw qwen → free-ai qwen7b | **keep** |

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
| `z-ai/glm-5.2:free` | 32k | small jobs only | 32k window rules out 128k-class work. NOTE: OpenRouter free `z-ai/*` is a separate door from the dropped direct Z.AI connection (unfunded key) — same model family, different billing and availability |
| `cohere/north-mini-code:free` | 256k | codegen overflow | unproven |

Rule: a candidate enters a combo only after an authenticated leg-probe
(`simulate --explain` then one real chat). Until then it stays in this
table, not in `combos.json`.

## D2. CLI reachability (which CLI can address the combos, 2026-09-24)

Combos live behind `:20128`; a CLI reaches them only if it points at the
gateway. Verified live this session:

| CLI | Points at gateway? | How | Reaches |
|---|---|---|---|
| opencode CLI/TUI | yes (repo `opencode.jsonc`) | native | all combos + `#effort` variants |
| Qwen Code CLI | yes (`t2-worker` entry + `.env` key, headless ack proven) | `setup-qwen --model t2-worker` | all combos by switching `--model` |
| Claude Code | yes (`ANTHROPIC_BASE_URL/AUTH_TOKEN` merged live) | `Set-AutoOSClaudeGateway` / `route_claude_to_gateway` | all combos |
| Antigravity CLI | **no** (upstream `none`/`mitm`: cannot point at OmniRoute) | — | n/a (its models arrive via the `antigravity` gateway connection instead) |
| Qoder CLI | n/a (needs binary on PATH + PAT; gateway `qoder` entry is dashboard-only) | `qodercli` component + dashboard | pending |
| Devin CLI | n/a (binary installed 3000.11.3; `devin auth login` + dashboard connection pending) | catalog component | pending |
| Gemini CLI | not installed (redundant: agy supersedes it, AI Studio key covers API legs) | `omniroute run gemini` if ever wanted | — |

## E. Proposed free-provider additions (researched + probed 2026-09-23/24)

Correction 2026-09-24: the local `agy models` list shows Gemini 3.8-flash,
but the **gateway** `antigravity` connection exposes ONLY 3.7/3.1/GPT-OSS
plus Opus/Sonnet 4.6 (17 refs, verified against authenticated
`/v1/models` — zero `antigravity/*3.8*` refs). There is no
`antigravity/gemini-3.8-flash-medium`; curation uses 3.7-high (ack 3.6s)
where reasoning matters and 3.7-medium (ack 1.6s) in free-only.

Live gateway state (updated 2026-09-30): the 13 configured connections
(cerebras, cheaperinference, cloudflare-ai, cohere, deepseek, gemini, groq,
huggingface, mistral, muse-code, opencode-zen, openrouter, sambanova) + 2 added
in the 2026-09-24 session (`zcode`, `opencode`) **plus `vertex_ai`**, which is
live again as the `vertex/` provider (see the Vertex AI section above).
`muse-code` is registered but unreferenced — delete it with `omniroute
providers remove muse-code`
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
| Antigravity CLI | `antigravity` (oauth, free) — canonical id (`agy` is alias-only; `providers auth agy` → Unknown). `oauth start --provider antigravity --import-from-system --no-browser`, code-paste flow | LIVE 2026-09-24 (account-named connection, `oauth status` active). Refs: `antigravity/claude-opus-4-6-thinking[-high\|-medium\|-low]`, `antigravity/claude-sonnet-4-6[-high\|-medium\|-low]`, `antigravity/gemini-3.7-flash-{high,medium,low,tiered}`, `antigravity/gemini-3.1-pro-low`, `antigravity/gpt-oss-120b-medium`. Probes: opus-4-6-thinking → **ack 3.4s**; gemini-3.7-flash-medium → **ack 1.6s**; gemini-3.7-flash-high → **ack 3.6s** | t1: opus dropped (200k leg in a 1M combo would 400); t2: gemini-high curated, free-only keeps medium; Opus in pinned `opus-4-6` | **done** |
| Claude Code subscription | `claude` (oauth) — `providers auth claude-code --no-browser`, code-paste flow. Ref prefix is `cc/` | LIVE 2026-09-24. Refs: `cc/claude-opus-4-6[-high\|-medium\|-low]`, `cc/claude-opus-4-7`, `cc/claude-opus-5`, `cc/claude-sonnet-4-6`, `cc/claude-fable-5`, `cc/claude-haiku-4-5-20251001`. Probe: opus-4-6 → **429 quota reset 1h** (hops by design; overflow-only, not a head) | overflow leg in pinned `opus-4-6` ($0 marginal) | **done** |
| Qoder | binary `qodercli` 1.1.62 ships in `~/.qoder/bin/qodercli` (runs) but is NOT on PATH — the dashboard error verbatim. PAT via `QODER_PERSONAL_ACCESS_TOKEN` (api-keys.yml `qoder_pat`; env wins over `/login` per Qoder docs). Gateway `qoder` entry is OAuth/dashboard-only (no CLI flow) | Qoder CLI headless/ACP use now; t2/t3 qwen overflow only after a dashboard gateway connection | PATH+PAT automated 2026-09-24 (`qodercli` catalog component: install-if-missing, PATH append, PAT export). Gateway connection via dashboard; then I list + probe |
| GitHub Copilot | `github`/`copilot` (oauth, device flow) | plan picker: GPT-5.5, GPT-5.3-Codex, Claude Sonnet/Opus 5, Gemini 3.8 Flash, Kimi K3 (docs 2026-09) | CLOSED 2026-09-23: M365-only, no GitHub seat | — |
| Devin CLI Agentic Bridge | `devin-cli-agentic` (noauth, alias `dva`) — needs the `devin` binary (`devin acp`); catalog component `devin-cli` added 2026-09-24 (winget `CognitionAI.DevinCLI` on Windows, vendor `install.sh` on Linux/macOS) | `dva/*` already in the live catalog (opus-4-7-max, glm-5-2, kimi-k3, ... — no connection needed to list) | t1 candidate (opus-4-7-max) + GLM overflow | install via catalog, then interactive `devin auth login`, then gateway connection via dashboard (`providers add devin-cli --oauth` answers Unknown — CLI OAuth allowlist holds 8 providers; `--dry-run` misleadingly passes) |
| Devin API key | `devin` (cloud-agent) — wired 2026-09-24 (`devin` registry entry; connection added, key accepted) | NONE (`models devin` empty; broken upstream, issue #6142 — key accepted but no model sync) | — | blocked upstream; use the CLI path above |
| ZCode GLM Coding Plan | `zcode` (noauth tag, key needed in practice) | GLM 5.3 Max (dashboard only) | t2/t3 GLM overflow | a ZCode/GLM plan credential (connection added, test FAILS without it); likely superseded by Z.AI below |
| Z.AI (API key) | DROPPED 2026-09-24: key unfunded (429-insufficient-balance) and GLM needs are covered by cheaperinference + `oc/glm-*` pool legs. Connection removed (`providers remove zai`), registry/example/docs rows reverted — history kept here for re-adding when funded | — | — |
| OpenCode Free | `opencode` (noauth pool) | Refs incl `oc/gemini-3.8-flash`, `oc/glm-5.3`, `oc/deepseek-v4-flash-free` | auto/* replacement or t3 tail | connection added, test FAILS key check — needs a live chat probe with client key to prove the pool serves |
| Kilo/Codex/Cursor | `kilocode`/`codex`/`cursor-cli` | subscription models | only with those subscriptions | tell me which you hold |

## F. FREEKEYS-2: free/credit provider onboarding (2026-09-30)

Program set (operator, 2026-09-29; full probe plan in the routing repo,
`PROBE-PLAN.md`). Recon, names-only: live `api-keys.yml` entries exist for
`nscale`, `vertex_ai`, `ovhcloud`, `kilo`, `ainative`, `siliconflow`,
`sealion`, `routeway`, `requesty`, `aion_labs`, `agnes_ai`, `pollinations`;
keyless entries (`-` value): `felo`, `uncloseai`, `opencode`, `ai_horde`.
Two gaps: `duckduckgo-web` has no entry at all (its own wiring path is still
to be chosen) and `g4f-pollinations` has no entry — only `pollinations`
exists (alias-or-lane ruling pending).

| Provider | Cap | Gateway today | Repo registry row |
|---|---|---|---|
| `vertex_ai` | $250 credit | **live** — `vertex/`, 65 models | none yet |
| `nscale` | $5 credit | — | none |
| `ovhcloud` | $200 credit | — | none |
| `kilo` `ainative` `siliconflow` `sealion` `routeway` `requesty` `aion_labs` `agnes_ai` `pollinations` | trial/keyed | — | none |
| `felo` `uncloseai` `opencode` `ai_horde` | keyless | — | none |

- **Vertex is the first credit provider live** (65 models, gateway-side).
  Its four fleet picks are pinned in
  `tools/render-opencode-container-config.py` (D-173):
  `vertex/gemini-3.1-pro-preview` (1M), `vertex/gemini-2.5-flash` (1M),
  `vertex/claude-sonnet-4-5` (200k), `vertex/DeepSeek-V4-Flash` (1M).
- All program providers — vertex included — still have **zero rows** in
  `catalog/ai-registry.json`; `apply` skips what the registry cannot see, so
  onboarding starts from zero (registry row + one authenticated leg-probe
  per leg, as the rules below have always required).
- Probe budget: ≤1 request / 3 s, every call logged; **vendor spend: $0**
  so far.
- **OS-32: nothing in this file is applied to the gateway until the
  operator approves** (PENDING-APPROVAL).

Rules for any addition: OAuth/subscription bridges proxy a PERSONAL
subscription (single-user proxy tolerated, resale is not — same ToS note as
`api-keys.md`); NEVER into `*-clean` (training/logging terms unknown);
one authenticated leg-probe per leg before it enters `combos.json`.
