# OVH AI Endpoints — credit-tier provider registration (2026-09-30)

Evidence doc for the `L1-backlog/ws-ovh-20260930` lane. All claims are
measured; no secret values are quoted.

Branch: `L1-backlog/ws-ovh-20260930` (worktree `AutoOS-ws-ovh`).
Commit: `f6f5e69` (registry + test), unpushed.

---

## 1. Discovery

| check | command | date | result |
|---|---|---|---|
| OVH API reachable | `GET https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/models` (Bearer key from `configuration/api-keys.yml` `ovhcloud:`) | 2026-09-30 | HTTP 200, 24 models |
| Built-in provider | `omniroute providers available --json` (alias `ovh`) | 2026-09-30 | `ovhcloud` IS built-in |
| Connection status | `omniroute connections list --json` | 2026-09-30 | **ALREADY EXISTS and ACTIVE** — id `2e7f59a9-…`, name `main` |
| Gateway model refresh | `GET http://127.0.0.1:20128/api/providers/{connId}/models?refresh=true` (mgmt token) | 2026-09-30 | 24 models (key valid) |
| Gateway /v1/models | `GET http://127.0.0.1:20128/v1/models` (omniroute key) | 2026-09-30 | both `ovh/*` and `ovhcloud/*` prefixes serve |

**Refusal verbatim:** the brief said "NO ovhcloud connection." The connection
was already registered and active before this lane started. `apply.ps1` would
SKIP it (existing connections are skipped, `configuration/omniroute/apply.ps1`
registration loop). No re-registration was performed or is needed.

---

## 2. Probe verdicts

House-style `tools/probe-toolcalls.py`-equivalent trial through the gateway
(≤1 request/3 s). Three checks per model: ack chat, single tool call, round
trip.

| model (gateway spelling) | ack | tool call | round trip | verdict |
|---|---|---|---|---|
| `ovh/gpt-oss-120b` | 200 finish=stop "ok" | 200 finish=tool_calls get_weather cityOk | 200 mentions18 noMoreCalls | **proven** 3/3 |
| `ovh/Qwen3-Coder-30B-A3B-Instruct` | 200 finish=stop "ok" | 200 finish=tool_calls get_weather cityOk | 200 mentions18 noMoreCalls | **proven** 3/3 |
| `ovh/Mistral-Small-3.2-24B-Instruct-2506` | 200 finish=stop "ok" | 200 finish=tool_calls get_weather cityOk | 200 mentions18 noMoreCalls | **proven** 3/3 |
| `ovh/Meta-Llama-3_3-70B-Instruct` | 200 finish=stop "ok" | 200 finish=tool_calls get_weather cityOk | 200 mentions18 noMoreCalls | **proven** 3/3 |

Gateway metadata (`GET /v1/models`): all 4 report `context_length`, `capabilities.tool_calling: true`, `capabilities.reasoning: true`.

---

## 3. Registry diff summary

File: `catalog/ai-registry.json` (worktree, commit `f6f5e69`).

### Provider entry (`providers.ovhcloud`)

| field | value | rationale |
|---|---|---|
| `id` | `ovhcloud` | provider key |
| `omniroute_id` | `ovhcloud` | built-in provider id (not the alias `ovh`); matches scaleway's pattern (omniroute_id = built-in id, model_prefix = alias) |
| `model_prefix` | `ovh` | the alias the gateway serves models under (`ovh/*`); same convention as scaleway → `scw` |
| `tier` | `credit` | $200 trial grant, metered, finite |
| `credit_usd` | 200 | operator's OVH trial credit |
| `monthly_cap_usd` | 200 | refuse at 100% of grant |
| `monthly_warn_fraction` | 0.8 | warn at 80% |
| `monthly_cap_source` | `operator 2026-09-30 OVH $200 trial credit` | |
| `available` | true | all 4 probed models passed |
| `trains_on_prompts` | null | UNVERIFIED — OVH terms never read; `private_safe()` fails closed |
| `api_base` | null | built-in convention (scaleway/deepinfra/nebius all null) |
| `litellm_env` | null | reached via OmniRoute connection, not LiteLLM |
| `key_name` | (omitted) | api-keys.yml entry is `ovhcloud`, same as provider id — no `key_name` needed (matches deepinfra/scaleway) |

### Model rows (3 new)

All three use OVH's exact mixed-case gateway spelling (e.g. `Mistral-Small-3.2-24B-Instruct-2506`, not the lowercase `mistral-small-3.2-24b-instruct-2506` that scaleway registered). `resolve_leg()` does an exact key match, so mixed-case and lowercase are different rows.

| model id | family | context_advertised | output_max | tool_calls | price_in (per token) | price_out (per token) | price_source |
|---|---|---|---|---|---|---|---|
| `Meta-Llama-3_3-70B-Instruct` | meta | 128000 | 32768 | proven | 7.4e-7 ($0.74/1M) | 7.4e-7 ($0.74/1M) | OVH pricing page 2026-09-30 |
| `Mistral-Small-3.2-24B-Instruct-2506` | mistral | 128000 | 32768 | proven | 1e-7 ($0.10/1M) | 3.1e-7 ($0.31/1M) | OVH pricing page 2026-09-30 |
| `Qwen3-Coder-30B-A3B-Instruct` | qwen | 128000 | 32768 | proven | 7e-8 ($0.07/1M) | 2.6e-7 ($0.26/1M) | OVH pricing page 2026-09-30 |

`context_advertised` from gateway `GET /v1/models` (`context_length` field).
`output_max` 32768 is the registry's conservative unknown (gateway reported none).
`reasoning: true`, `effort_ladder: []` (can reason, no effort parameter — matching mistral/qwen family convention).

### Shared row reuse (no edit)

`ovh/gpt-oss-120b` → reuses existing `models["gpt-oss-120b"]` (line 473 in the registry). The shared row carries `price_in: 0, price_out: 0`. The OVH gpt-oss-120b leg is therefore **gated/unpriced** — same state as deepinfra's credit-tier legs that reuse this row. Setting a non-zero price on the shared row would affect every free-tier provider that serves it (groq, cerebras, sambanova, scaleway), so it is left at 0. OVH's per-1M-token price for gpt-oss-120b is $0.09 in / $0.47 out (pricing page, 2026-09-30) — available for a future per-leg price mechanism.

### Other

- `version` bumped from `2026-09-28` to `2026-09-30`.
- `tests/test_registry.py` line 2923: added `"ovhcloud": 200.0` to `test_the_shipped_grants_are_the_operators_numbers`.
- No route entries added (scaleway and together_ai have none — providers + model rows referenced in combos suffice).
- `combos.json` NOT touched (L1-alpha's lane owns it).

---

## 4. Validation

| check | command | result |
|---|---|---|
| JSON well-formed | `python -c "json.load(open(...))"` | valid |
| `registry.py check` | `python tools/registry.py check` | ok: 25 routes, 74 models, 34 providers |
| `registry.py validate` | `python tools/registry.py validate` | ok (same) |
| `test_registry.py` | `python tests/test_registry.py` | 300 tests, OK |
| `render omniroute --check` | `python tools/registry.py render omniroute --check` | 5 pre-existing diffs (same with my changes stashed — NOT caused by this lane) |

---

## 5. Prepared combo-leg proposals (for L1-alpha's lane)

These are proposals only — `combos.json` was not touched.

### Available legs (un-gated, priced)

| leg (registry spelling) | gateway ref | family | $/1M in→out | distinct from |
|---|---|---|---|---|
| `ovhcloud/Meta-Llama-3_3-70B-Instruct` | `ovh/Meta-Llama-3_3-70B-Instruct` | meta | 0.74→0.74 | any other provider's Llama 3.3 70B |
| `ovhcloud/Mistral-Small-3.2-24B-Instruct-2506` | `ovh/Mistral-Small-3.2-24B-Instruct-2506` | mistral | 0.10→0.31 | scaleway's lowercase `scw/mistral-small-3.2-24b-instruct-2506` (same model, different casing/provider) |
| `ovhcloud/Qwen3-Coder-30B-A3B-Instruct` | `ovh/Qwen3-Coder-30B-A3B-Instruct` | qwen | 0.07→0.26 | no existing Qwen3-Coder row in the registry |

### Gated leg (unpriced)

| leg | gateway ref | reason |
|---|---|---|
| `ovhcloud/gpt-oss-120b` | `ovh/gpt-oss-120b` | shared row `price_in: 0` — resolver refuses credit-tier legs with price 0; same state as deepinfra's legs that reuse this row. OVH price is known ($0.09/$0.47 per 1M) but cannot be set on the shared row without affecting free-tier providers. |

### Placement rationale

- The 3 un-gated legs are **credit-tier** (not free). Per the FREEKEYS-2 ordering convention (free → credit → paid), they would trail the free band and precede the paid legs in any combo that adds them.
- Each leg is on a **distinct provider** (OVH) — a 429 fall-through would land on a different provider's rate limit.
- `Mistral-Small-3.2` is the same model as scaleway's free `scw/mistral-small-3.2-24b-instruct-2506`. Adding the OVH leg gives a credit fallback when the free scaleway leg is rate-limited, but it costs money ($0.10/$0.31 per 1M).
- `Qwen3-Coder-30B-A3B-Instruct` is a **new model** not served by any other registered provider — it adds a coding-specialist option.
- `Meta-Llama-3_3-70B-Instruct` is a **new model** not served by any other registered provider — it adds a general-purpose 70B option.
- `trains_on_prompts: null` (unverified) — these legs reach no `-clean` route (same as all other credit-tier legs, `private_safe()` fails closed).

### What L1-alpha would need to do

1. Add legs to the desired combo(s) in `combos.json` in the credit band (after free legs, before paid legs).
2. Each leg must use the **registry spelling** (`ovhcloud/...`), not the gateway spelling (`ovh/...`) — `gateway_ref()` applies the prefix at render time.
3. Re-run `render omniroute --check` to verify the render.
4. Run `apply.ps1` (with `$env:AUTOOS_KEYS_FILE` set to the main checkout's `configuration/api-keys.yml` if running from a worktree) — the connection is already registered, so apply will skip it; the new combos will be created.

---

## 6. Cross-references

- Gateway findings (compatible-node recipe, failure modes): `docs/handoff/2026-09-30-omniroute-gateway-findings.md` — §5 is the compatible-node recipe, NOT needed here since ovhcloud is built-in.
- Registry schema: `catalog/ai-registry.schema.json` — `price_source` (line 230-233): "A `tier: credit` provider's leg is refused by the resolver while its price is 0 or absent."
- Validator: `tools/registry.py` — `_check_credit_guards` (line 2863): enforces credit_usd/monthly_cap_usd/monthly_warn_fraction trio; `resolve_leg` (line 394): exact key match; `gateway_ref` (line 420): applies model_prefix.
- Existing credit-tier providers: deepinfra ($5, available, 6 proven legs, none in combos), together_ai ($5, unavailable, 0 proven legs), morph ($10), vertex_ai ($250).
