# L3 routing: choosing the leaf model for a task

**Status: PROPOSED (2026-09-18), awaiting the operator's approval.**
- Until then, L1/L2 apply it **by hand** when they pick an L3.
- The code change it describes (a scorer that generates `cao.routing` ladders) is not built yet.
- The operator's fixed rules come first and are not re-weighed by any score: routing order
  R-gateway-01 and cross-family review R-review-03 in [`../SKILL.md`](../SKILL.md) (the
  2026-09-18 "DeepSeek first" order was superseded on 2026-09-25).

Evidence: three research passes on 2026-09-18:
- the OpenRouter models API, 445 entries, measured;
- this host's GPU/RAM and `ollama list`, measured;
- a code survey of `cao/`.

Labels: **M** measured, **S** sourced, **I** inferred.

## 1. What the router decides, and from what

Every L3 task carries a **task card**. The router turns the card plus the model catalogue plus
live state into an **ordered ladder**: the existing `Candidate(pool, family, model, effort)`
list. The current `routing.select()` then takes the first open pool, and `dispatch.pick_reviewer()`
enforces family separation. **Nothing downstream changes; only where the ladder comes from.**

| Parameter | On the card as | Why it matters |
|---|---|---|
| Complexity | `trivial` / `mechanical` / `standard` / `hard` | Sets the minimum **tier**. The shape of the work decides it, not its importance (lanes.md) |
| Importance | `low` / `normal` / `critical` | Critical raises the tier floor by one and weights failure risk up: prefer smarter, bigger models |
| Urgency | `batch` / `normal` / `blocking` | Blocking weights **time**. It prefers fast paid endpoints over rate-limited free ones and over slow local offload. Never delayed for an off-peak window |
| Cost | the price table | Estimated `in_tokens × $in + out_tokens × $out`. Free and local cost $0 but spend quota or the GPU |
| Time | tok/s and queueing | `out_tokens / tok_s`, plus a queue term: free tiers are rate-limited, and local runs one model at a time |
| Availability | live state | Pool closed or cooling (`budget.is_open`), probe result, free-tier daily quota left, GPU busy |
| Context size | `in_tokens`, `out_tokens` | Hard filter: `context ≥ 1.3 × (in + out)` and `max_output ≥ out` |
| **Privacy** (added) | `public` / `private` | Content from a private repo never goes to an endpoint that may train on or publish prompts; preview and stealth models see public content only |
| **Tool calling** (added) | `agentic: true/false` | An agentic leaf needs reliable tool calls. Filter on `supported_parameters ∋ tools`, and on a local model that is known to work with Ollama's tool calls |
| **Verifiability** (added) | `guard: <command>` | A task with a guard fails loudly, so a cheaper tier is safe. With no guard, the floor rises by one |
| **Role** (added) | `write` / `review` / `close` | `review` excludes the writer's family. `close` is always Claude Sonnet (logic) or Haiku (mechanical) |
| **Track record** (added) | the ledger | The measured pass rate per (model, task class) in `verify.ndjson`. A model that failed a class twice is demoted for that class |
| **Off-peak window** (added) | `provider_windows.py` | A small bonus for a pool that is `offpeak`/`quiet` now. Price never outranks urgency |

## 2. The method

```
filter  →  floor  →  score  →  ladder  →  escalate on failure
```

1. **Filter (hard).** Remove every model that:
   - fails the context or output size;
   - lacks tool calls when `agentic`;
   - breaks privacy;
   - belongs to a closed or cooling pool, or one whose daily free quota is spent;
   - is local while the GPU is taken;
   - shares the writer's family (for `review`).
2. **Floor.**
   - `tier_min = {trivial: 0, mechanical: 1, standard: 2, hard: 3}[complexity]`.
   - Add 1 if `importance == critical`; add 1 if there is no guard.
   - Cap at 3: tier 4 (Opus) is L1/L2 only.
3. **Score** each survivor with a tier ≥ floor. Lower is better:
   `score = w_cost·norm(cost) + w_time·norm(time) + w_risk·(1 − p_pass) − w_q·(tier − floor)·[importance ≥ normal] − bonus_offpeak`.

   | Preset (from the card) | w_cost | w_time | w_risk | w_q |
   |---|---|---|---|---|
   | default | 0.5 | 0.2 | 0.3 | 0.05 |
   | `urgency: blocking` | 0.1 | 0.6 | 0.3 | 0.05 |
   | `importance: critical` | 0.1 | 0.2 | 0.7 | 0.15 |
   | `urgency: batch` | 0.7 | 0.0 | 0.3 | 0.0 |

   - `p_pass` starts at a per-tier prior (0.6, 0.7, 0.8, 0.9 for tiers 0 to 3) and moves with the
     ledger.
   - DeepSeek gets the **operator's "DeepSeek first" preference** as a fixed bonus within its
     tier. It is a rule, so a tuned weight never out-votes it.
   - **Free first** (operator, 2026-09-18) outranks that preference. When the card says
     `privacy: public`, every free public-only model at or above the floor is ranked before
     every paid one. When the card says `privacy: private`, public-only models were already
     removed at the filter step, so DeepSeek or Ollama lead.
4. **Ladder.** Sort, then emit the top 4 as `Candidate`s, including at least two pools, so a
   closed pool falls through as it does today.
5. **Escalate.** When a leaf fails the guard twice, re-route one tier up and to a **different
   family**, with the guard's output verbatim (the existing `--rework`). Every change ends with a
   Claude closer (§4 rule 2) before it counts as done.

## 3. The catalogue: tiers and routes, measured today

Prices are $/1M tokens, input/output, from OpenRouter on 2026-09-18 (M). Free `:free` ids are
limited to 20 requests/min and 50/day, or 1,000/day after ≥ $10 of lifetime credits (S:
openrouter.ai/docs/api_reference/limits).

| Tier | Use | Remote (OpenRouter id, price, ctx) | Local (Ollama tag, fit on RTX 3060 12 GB) |
|---|---|---|---|
| **0 trivial** | rename, format, summarise a grep, commit message | `liquid/lfm-2.5-2.6b:free` (66K ctx, **8K max output**); `openrouter/free` for throwaway work only (random model, not reproducible) | `qwen2.5-coder:7b` (pulled; **41.9 tok/s**, M 2026-09-17); `gemma4:e4b`, `qwen3:4b` (pulled); LFM2.5-2.6B via `hf.co/LiquidAI/LFM2.5-2.6B-GGUF` (fits fully; tools I) |
| **1 mechanical** | closed-file edits to a convention, scaffolding, doc sweeps | `inclusionai/ling-3.0-flash` $0.021/$0.063 (cheapest, 262K); `poolside/laguna-xs-2.1(:free)` $0.06/$0.12; `nvidia/nemotron-3-nano-30b-a3b` $0.06/$0.24 (no free variant); `google/gemma-4-26b-a4b-it(:free)` $0.09/$0.30 | MoE with 3–4B active, partial offload, "usable" (I): `gemma4:26b` (19 GB), `laguna-xs-2.1` (20 GB), `north-mini-code-1.0` (19 GB), `nemotron-3-nano:30b-a3b-q4_K_M` (24 GB); `gpt-oss:20b` (14 GB; tool calls need manual config, S) |
| **2 standard** | a function plus its tests, a bounded bug | **DeepSeek v4 Flash** (operator: first choice); `nvidia/nemotron-3-super-120b-a12b(:free)` $0.08/$0.45; `poolside/laguna-s-2.1(:free)` $0.09/$0.18 (1M paid / 262K free); `cohere/north-mini-code:free` (free only, no paid fallback); `google/gemma-4-31b-it(:free)`; `openai/gpt-oss-120b` $0.15/$0.60 (131K); `minimax/minimax-m2.7` $0.30/$1.20 | `qwen3-coder:30b` (pulled; ~5 tok/s, M): batch only |
| **3 hard** | multi-file, subtle, long-horizon agentic | DeepSeek's strongest model; `moonshotai/kimi-k2.6` $0.95/$4.00 (paid only); `z-ai/glm-5.2` $0.56/$1.76 (**1M paid; the free variant is 32K and unusable**); `nvidia/nemotron-3-ultra-550b-a55b:free` (**1M free**, huge diffs); `thinkingmachines/inkling` $1.00/$4.05 (1M) | none practical: `gpt-oss:120b`, `gemma4:31b` (dense), MiniMax and GLM are too slow or too big (I) |
| **closer** | the last check / final pass | Claude **Haiku** (mechanical) · Claude **Sonnet** (logic), via the Agent tool | — |

Not routable:
- **Ox Alpha** is gone from the API (M), reportedly unmasked as a GLM model (S).
- `dots-3-note-preview:free` (512K) and the stealth/preview class: public content only.
- The Ling Fin/Sante/VL variants are finance/health/vision models, not coders.
- Nemotron 3 Nano 30B and Nano Omni are two different ids: one paid-only, one free-only.

## 4. Privacy (a hard filter, not a weight)

**Operator decision, 2026-09-18.** Training on user data is allowed, so free and contributor
models are available, but no private data may reach them. Free models come first whenever the
content allows.

**Account state (M, 2026-09-18).** With training disallowed, OpenRouter refused both routes:
`Paid model training violation` for Muse contributor, and `Free model training violation` for the
`:free` ids. So these endpoints **do** train on prompts. Before that, `muse-spark-1.3-contributor`
also needed an 18+ age confirmation.

**Model classes.**
- **public-only:** everything not listed below, including every `:free` id, `openrouter/free`,
  the Muse contributor models and `opencode/*-free`.
- **private-safe:** `deepseek/*` (paid API) and `ollama/*` (local; nothing leaves the host).

**Why a prompt rule is not enough (M).** A leaf reads its whole working directory. Measured
leaks, all closed below:
- the worktree path contains the Windows username, and OpenCode sends the working directory to
  the model;
- ignored files: DONE notes with host paths, and a graphify junction full of absolute paths;
- the environment and the host (`auth.json`, `api_keys.conf`, `~/.claude.json`);
- four skills held private values: mcp-servers-setup 15 hits, repository-index 2,
  structured-memory 11, swarm-orchestration 35, unattended-orchestration 270;
- public AutoOS itself held one username in an ADR.

**The leaf gate** (`run-leaf.sh MODEL TASK_FILE`; for now an L1-local tool; moving it here is a
tracked task):
1. Classify the model. Refuse with exit 3 if the **task text** holds a literal private value, for
   any model. The generic rules (rfc1918, `C:\Users\<you>`) do not decide; they fire on
   placeholders.
2. Public-only models: refuse unless the repo's origin is on a public allow-list **and** every
   file the leaf can see scans clean.
3. Run the leaf in a **throwaway `/tmp` copy**: a `git archive` snapshot of HEAD plus the
   caller's uncommitted tracked changes. No history and no other branches, because a repo may
   keep pre-purge branches. No ignored files, no origin, a neutral path, and `env -i` with a
   clean PATH.
4. Offer only the skills that scan clean. Rebuilt on every run.
5. Patch the leaf's changes back as uncommitted edits. The L2 judges and commits. Leaves can
   never commit or push.

**Host fences (OpenCode config).**
- `external_directory: deny`;
- `read` denies for `.env*`, `auth.json`, `api_keys*`, `.claude.json*`, `patterns.txt`;
- 28 bash denies: git history changes, `rm -rf` of / and ~, sudo, docker compose/rm,
  `/mnt/c`, the opencode config and auth paths, `env`/`printenv`/`export`.
- The only permission rule that lets a Claude session start a leaf is
  `Bash(wsl -e bash -lc *run-leaf.sh *)`. The classifier refuses raw `opencode run` ("Create
  Unsafe Agents", measured).

**Verified (M, rung 1):**
- a public-only model in the private repo → refused;
- a task naming a private path, even for DeepSeek → refused;
- a public-only model on public AutoOS → ran in `/tmp`, the patch was applied back, and the test
  passed from Windows;
- an auto-mode Claude session could launch the gate.

**Residual risk (I).** A determined leaf could still reach data through a route no fence names.
The gate reduces what a leaf can see to content that is already public, and the fences shrink
the rest. Keep private-repo lanes on private-safe models, whatever the gate would allow.

## 5. What to add to OpenCode and OpenHands (AutoOS, one catalogue)

`catalog/llm-models.json` is the single source (Phase 7). Today it has 16 OpenRouter entries
(all `:free`) and one local Ollama model (M: `catalog/llm-models.json:38-220`). Proposed additions:

- **OpenRouter:**
  - `google/gemma-4-31b-it:free` and `google/gemma-4-26b-a4b-it:free`;
  - `moonshotai/kimi-k2.6`, `openai/gpt-oss-120b`, `minimax/minimax-m2.7`, `z-ai/glm-5.2`
    (paid);
  - `inclusionai/ling-3.0-flash` and `nvidia/nemotron-3-nano-30b-a3b` (paid, cheap);
  - `liquid/lfm-2.5-2.6b:free`.
- **Every entry gains the router's fields:** `tier`, `family`, `pool`, `ctx`, `max_out`,
  `price_in`, `price_out`, `free`, `tools`, `privacy`, `tok_s` (measured, else null), and `route`
  (`opencode` / `openhands` / `ollama`).
- **Local Ollama:**
  - models in the OpenCode provider (`Set-AutoOSOpenCodeConfig` / `install.sh`) and one OpenHands
    `ollama-<name>.json` profile each: `gemma4:26b`, `laguna-xs-2.1`, `north-mini-code-1.0`,
    `nemotron-3-nano:30b-a3b-q4_K_M`, `gpt-oss:20b`;
  - `reasoning_effort: "none"` where the model is non-reasoning (the existing profile's rule);
  - Ollama server settings AutoOS does not set today (M: zero hits): `OLLAMA_CONTEXT_LENGTH`
    (default 4,096; too small for agentic leaves), `OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_PARALLEL=1`,
    `OLLAMA_MAX_LOADED_MODELS=1` (one GPU model at a time).
- Pulling a model is an install: it needs the operator's go and free disk (19–24 GB each).

## 6. Building it (after approval)

- **Data ladder, rung 1:** `cao/route_score.py`, pure: `build_ladder(card, catalogue, state) ->
  list[Candidate]`, tested against a **fixture catalogue** with known expected ladders. Include
  one test per parameter above, plus the three operator rules as tests that no weight can
  break.
- **Rung 2:** the live AutoOS catalogue and today's `budget.ndjson`: the ladders for a handful of
  recorded real tasks, checked by hand.
- **Rung 3:** turn it on per task class with `"routing": {"implement": "auto"}`. Hand-written
  ladders keep working, and the existing `test_routing`/`test_dispatch`/`test_launch` tests must
  stay green unchanged.
- `budget.py` gains a per-day request counter for free pools. `provider_windows.preferred()`
  feeds the off-peak bonus (today nothing in dispatch calls it, M).
- Adapters: an OpenCode launcher adapter (none exists today, M) is the route for DeepSeek,
  OpenRouter and Ollama leaves in the batch runner.

## 7. Open questions for the operator

1. Approve the method and the tier table, or amend them.
2. Buy ≥ $10 of OpenRouter credits? That raises free ids from 50 to 1,000 requests/day.
3. Confirm the OpenRouter privacy toggles are off. Until then, private content avoids free
   endpoints.
4. Which local models to pull (19–24 GB each), and whether AutoOS should set the Ollama server
   variables.
