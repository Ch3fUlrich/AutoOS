# Lane B — combos.json context correction + vertex leg (2026-09-30)

**Writer:** L1-backlog/ws-combos (t2 smart-reasoning-128k)
**Branch:** `L1-backlog/ws-combos-20260930`
**Scope:** `configuration/omniroute/combos.json`, `docs/models.md` (non-managed prose),
`configuration/omniroute/apply.ps1` + `apply.sh` (resilience comment only)
**Out of scope:** `catalog/ai-registry.json` (L1-beta), `tools/probe-*.py` (P1-fixes)

---

## 1. Why

The operator flagged that several combos declared a context window **smaller** than
what their serving legs can actually use. Root cause: the registry's
`context_advertised` is stale for key models vs the live `/v1/models` catalog. The
combos.json `context` field and the combos array are semantically generated from the
registry via `render_omniroute()`, so updating combos.json without a registry change
will fail `test_render_matches_committed_combos_semantically` — **that failure is an
expected cross-lane dependency**, not a regression. The registry rows needed to close
the gap are listed in §3 for L1-beta.

The gateway does **not** receive the `context` field from `apply.ps1` (no `--context`
flag on `omniroute combo create`); it computes `computed_context_length` per-request
from the legs' live catalog context windows. So the `context` field in combos.json is
documentation/render only — it does not change gateway behaviour. The one gateway-
affecting change in this lane is adding `vertex/gemini-3.8-flash` as the second leg of
the `gemini-3.8-flash` combo.

---

## 2. Measured-vs-inferred context table

All live values measured 2026-09-30 via gateway on `:20128` — `GET /v1/models`
(5250 models) and `omniroute combo list` (`computed_context_length` per combo).

### Combos whose context was updated

| Combo | Old | New | Legs (live `context_length`) | Why |
|---|---|---|---|---|
| `gemini-3.8-flash` | 128k | **1M** | `gemini/gemini-3.8-flash` (1048576), `vertex/gemini-3.8-flash` (1048576) | Both legs 1M; vertex added as second leg (operator MUST). |
| `opus-4-6` | 200k | **1M** | `antigravity/claude-opus-4-6-thinking` (gateway `computed_context_length` 1048576) | Single leg; registry `context_advertised` 200000 is stale. |
| `t2-orchestrator` | 200k | **1M** | `antigravity/claude-opus-4-6-thinking` (1048576) | Same single leg as opus-4-6. |
| `t2-worker-clean` | 128k | **1M** | `deepseek/deepseek-flash` (1048576) | Single leg; was clamped to 128k by stale registry `context_advertised`. |
| `t3-driver-clean` | 128k | **1M** | `deepseek/deepseek-flash` (1048576) | Same single leg as t2-worker-clean. |

### Combos whose context is unchanged (honest clamps)

| Combo | Context | Clamp source | Legs at the clamp |
|---|---|---|---|
| `t1-orchestrator` | 128k | scw/nebius legs at 128000 | `scw/qwen3-235b-a22b-instruct-2507` (128000), `nebius/zai-org/GLM-5.3-Flash` (128000), `scw/mistral-small-3.2-24b-instruct-2506` (128000) |
| `t1-orchestrator-free-only` | 128k | same | same scw/nebius legs |
| `t1-orchestrator-paid` | 1M | single leg | `meta-api/muse-spark-1.3-contributor` (1048576) — already correct |
| `t2-worker` | 128k | scw/nebius/free-ai legs at 128000 | `scw/qwen3-235b-a22b-instruct-2507` (128000), `scw/mistral-small-3.2-24b-instruct-2506` (128000), `nebius/zai-org/GLM-5.2` (128000), `free-ai/qwen7b` (128000) |
| `t2-worker-free-only` | 128k | same | same scw/nebius/free-ai legs |
| `t3-driver` | 128k | scw/nebius legs at 128000 | `scw/mistral-small-3.2-24b-instruct-2506` (128000), `nebius/zai-org/GLM-5.2` (128000), `scw/qwen3-235b-a22b-instruct-2507` (128000), `mistral/mistral-code-latest` (128000) |
| `t3-driver-free-only` | 128k | same | same scw/nebius/free-ai legs |
| `t4-rag` | 128k | `cohere/command-r-plus-08-2024` at 128000 | `cohere/command-a-03-2025` (288000) is the larger leg, but the route falls to the 128k leg |
| `deepseek-v4.1-flash` | 1M | single leg | `deepseek/deepseek-flash` (1048576) — already correct |
| `spark-1.3-contributor` | 1M | single leg | `meta-api/muse-spark-1.3-contributor` (1048576) — already correct |

### Full live `/v1/models` context for every combo leg (measured 2026-09-30)

| Leg | Live `context_length` | Registry `context_advertised` | Stale? |
|---|---|---|---|
| `deepseek/deepseek-flash` | 1048576 | 1048576 | no |
| `gemini/gemini-3.8-flash` | 1048576 | 131072 | **stale** |
| `vertex/gemini-3.8-flash` | 1048576 | (no route leg) | **missing from route** |
| `meta-api/muse-spark-1.3-contributor` | 1048576 | 1048576 | no |
| `scw/qwen3-235b-a22b-instruct-2507` | 128000 | 128000 | no |
| `nebius/zai-org/GLM-5.3-Flash` | 128000 | (no model entry) | **missing model** |
| `scw/mistral-small-3.2-24b-instruct-2506` | 128000 | 128000 | no |
| `nebius/zai-org/GLM-5.2` | 128000 | 128000 | no |
| `mistral/mistral-code-latest` | 128000 | 128000 | no |
| `free-ai/qwen7b` | 128000 | 128000 | no |
| `cohere/command-a-03-2025` | 288000 | 131072 | **stale** |
| `cohere/command-r-plus-08-2024` | 128000 | 128000 | no |
| `antigravity/claude-opus-4-6-thinking` | 1048576 (gateway computed) | 200000 | **stale** |

---

## 3. Needed registry rows for L1-beta

These changes to `catalog/ai-registry.json` would make the render-gate test pass
again. Do NOT make these in the combos lane — they are L1-beta territory.

1. **`models.gemini-3.8-flash.context_advertised`**: 131072 → 1048576
   - Live `/v1/models` confirms 1048576. The 131072 was the pre-launch estimate.
   - Affects: gemini-3.8-flash combo (would render 1M), t1-orchestrator (clamp
     would shift from gemini to scw/nebius legs, still 128k).

2. **`routes.gemini-3.8-flash.legs`**: add `vertex/gemini-3.8-flash` as second leg
   - Current registry legs: `[gemini/gemini-3.8-flash, openrouter/google/gemini-3.8-flash,
     deepinfra/google/gemini-3.5-flash]`
   - Vertex provider (`providers.vertex`) must also be registered if not already.
   - The live gateway already has this leg (manually added before this lane).

3. **`models.claude-opus-4-6-thinking.context_advertised`**: 200000 → 1048576
   - Gateway `computed_context_length` is 1048576 for the antigravity leg.
   - Affects: opus-4-6 and t2-orchestrator (would render 1M).

4. **`models.command-a-03-2025.context_advertised`**: 131072 → 288000
   - Live `/v1/models` confirms 288000. Does not affect any combo's declared context
     (t4-rag clamps to command-r-plus at 128000), but the managed table in models.md
     would show 288000 instead of 128k for the command-a leg.

5. **`models.GLM-5.3-Flash`**: add a model entry
   - The model `nebius/zai-org/GLM-5.3-Flash` appears in t1-orchestrator,
     t1-orchestrator-free-only, and t3-driver-free-only but has no registry model entry.
   - Live `context_length` = 128000. Add with `context_advertised: 128000`.

6. **`providers.vertex`**: verify the vertex provider is registered
   - If not, register it (Google Vertex AI, OpenAI-compatible). The live gateway
     already serves `vertex/gemini-3.8-flash` at 1048576, so the provider is live.

---

## 4. Vertex second leg

The operator's MUST directive: `gemini-3.8-flash` models MUST become
`["gemini/gemini-3.8-flash", "vertex/gemini-3.8-flash"]`.

- `gemini/gemini-3.8-flash` = Google AI Studio free pool (first leg, free)
- `vertex/gemini-3.8-flash` = Google Vertex AI (second leg, separate quota pool)
- Both advertise 1048576 in the live `/v1/models` catalog
- The live gateway already had this leg (manually added before this lane)
- `apply.ps1` validates against `/v1/models` — if vertex is in the catalog, it
  keeps the leg; if not, it skips with a warning
- Strategy is `priority`: gemini free first, vertex free second, then (if any
  paid legs existed) paid fallback

---

## 5. Fast-failover resilience

No value changes. The current settings are already well-tuned for fast failover:

- `providerBreaker.apikey.failureThreshold = 2`: after 2 failures, the leg is
  skipped for `resetTimeoutMs` (30s). At most two cheap round-trips before hop.
- `providerBreaker.apikey.degradationThreshold = 1`: hop on first degradation
  signal (a 429 counts).
- `providerBreaker.apikey.resetTimeoutMs = 30000`: retry the leg after 30s.
- `requestQueue.maxWaitMs = 180000`: queue wait (not per-leg wait) — needed for
  Muse Spark's thinking time. A rate-limited leg returns 429 in < 1s and the
  chain hops immediately via the breaker, not via maxWaitMs.

A comment was added to both `apply.ps1` and `apply.sh` documenting this design
(§3 in the resilience comment block). No values changed — the breaker IS the
fast-skip mechanism, not maxWaitMs.

---

## 6. Render-gate test impact

`tests/test_registry_render.py::RenderMatchesTodayTests::test_render_matches_committed_combos_semantically`
will fail because:

- 5 combos have a `context` that no longer matches the registry render (which
  uses stale `context_advertised` values).
- 1 combo (`gemini-3.8-flash`) has a model (`vertex/gemini-3.8-flash`) that the
  registry route does not include.

The test is **not weakened**. The failures are expected and will be resolved
when L1-beta updates the registry rows in §3. Running the test before and
after the change confirms the expected failures and no new unrelated failures.

---

## 7. Files touched

| File | Change |
|---|---|
| `configuration/omniroute/combos.json` | 5 context fields updated; vertex leg added; $comment extended |
| `docs/models.md` | Non-managed prose: 11 edits (notes per route, fallback chains, context rule, role labels, mermaid diagram) |
| `configuration/omniroute/apply.ps1` | Resilience comment §3 added (no value change) |
| `configuration/omniroute/apply.sh` | Resilience comment matching apply.ps1 (no value change) |
