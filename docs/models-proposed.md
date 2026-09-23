# Combos evaluation (for the operator — keep / drop / merge)

Source of leg lists: `configuration/omniroute/combos.json` (12 curated combos).
Gateway truth may hold **more** (`:20128` answered 401 unauthenticated, so the
live list is unverified here — finish with
`omniroute combos list` authenticated, or `python tools/audit-router.py`
(live), which reports repo-missing and live-extra combos).

## A. Curated combos (`combos.json` — fully managed)

| Combo | Legs (priority order) | Role | Recommendation |
|---|---|---|---|
| `tier1` | zen spark-free → openrouter spark-contributor | orchestrator-1M | **keep** |
| `spark-1.3-contributor` | same as tier1 | pinned single-model route | **keep** (identical legs; single purpose-built name) |
| `tier1-clean` | openrouter spark-contributor | paid-only 1M | **keep** |
| `tier2` | gemini-3.8-flash → groq gpt-oss → cerebras → sambanova → cheapinference ×3 → openrouter deepseek → deepseek-direct → zen flash | smart-reasoning | **keep** |
| `tier2-clean` | deepseek-direct → openrouter deepseek → zen flash → mistral-small | paid-only smart | **keep** |
| `tier3` | mistral-code → groq qwen → cerebras qwen → cheapinference ×2 → mistral-small → deepseek-direct → zen flash | cheap driver | **keep** |
| `tier3-clean` | deepseek-direct → mistral-small → zen flash | paid-only driver | **keep** |
| `rag` | cohere command-a → command-r-plus | RAG specialist | **keep** (only non-reasoning route) |
| `gemini-3.8-flash` | gemini free → openrouter google twin (paid) | pinned, intra-family fallback | **keep** |
| `deepseek-v4.1-flash` | openrouter deepseek (paid) → zen flash | pinned, cheapest-first paid | **keep** |
| `tier2-credit` | cerebras → sambanova → cheapinference ×3 | credit burn, no free heads | **review** — keep only while credit balances exist; burn-in still open (handoff item 6) |
| `tier3-credit` | cerebras qwen → cheapinference ×2 | credit burn, driver tier | **review** — same condition |

## B. Gateway-only combos (referenced, NOT curated)

`opencode.jsonc` `providers.omniroute.models` addresses these, but they exist
only in the gateway's own store — no leg order in this repo, no drift gate:

| Combo | Known legs | Used by | Recommendation |
|---|---|---|---|
| `auto` | gateway built-in (unverified) | opencode default bootstrap | **decide**: pin legs into `combos.json`, or drop the refs and default to `tier2` |
| `auto/smart` | gateway built-in (unverified) | opencode bootstrap | **decide**: same as `auto` |
| `auto/cheap` | gateway built-in (unverified) | opencode bootstrap | **decide**: same as `auto` |

## C. LiteLLM-only groups (no gateway combo — manual fallback only)

Present in `configuration/litellm/config.yaml` AND `opencode.jsonc`
`providers.litellm.models`, deliberately outside `combos.json`:

| Group | Role | Recommendation |
|---|---|---|
| `tier1-paid` | openrouter spark first, then zen flash | **keep** (fallback when free promo dies) |
| `tier2-paid` | paid smart legs | **keep** |
| `tier3-paid` | paid driver legs | **keep** |

## D. How to finish this evaluation

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
