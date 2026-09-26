# Registry field mapping — one model registry (tasks A1, A2, A4a)

Field-level mapping for [2026-09-25-routing-v2-spec.md](2026-09-25-routing-v2-spec.md) section 3
(the registry) and section 3.2 (migration, phase 1). Every field of every entry type in the six old
files is listed below with where it lands: a `catalog/ai-registry.json` path, `dropped: <why>`, or
`derived: <how>`. The converter that implements this mapping is `tools/registry-convert.py`; its own
tables (`PROVIDER_EXTRA`, `MODEL_EXTRA`, `EXTRA_MODELS`, `EXTRA_PROVIDERS`, `PROVIDER_WINDOWS`,
`CLIENT_EXTRA`, `COMBO_CLASS`, `ROUTE_COMMENT`) are the executable form of the "small explicit table"
parts of this mapping — read them alongside this document, not instead of it. §10 documents the
reverse direction for `combos.json` (`tools/registry.py render omniroute`, task A4a).

Phase 1 only (spec 3.2): this is the "add the registry, prove the mapping" step. Nothing here changes
what `apply.sh`/`apply.ps1`/`sync-*.py` read yet — that is task A4/A5 (rendering
`configuration/omniroute/combos.json` and proving its render equals today's file, §10, is phase 1's
own equality *proof*; actually pointing `apply.sh`/`apply.ps1` at the rendered file is still a later
phase-2 step).

## 1. `catalog/llm-models.json` → `models.<id>`

One array entry → one `models.<id>` entry (`id` unchanged, used as the registry key).

| Old field | Registry path | Notes |
|---|---|---|
| `id` | `models.<id>.id` | Unchanged; also the dict key. |
| `name` | `models.<id>.display_name` | **Additive field**, not in spec 3.1's list — kept so the human label is not lost (D9). |
| `direct` (object) | `models.<id>.direct` | Copied through as-is (`provider`, `base_url`, `model`, `npm`, `reasoning_effort`) — this *is* spec 3.1's "today's llm-models.json direct-provider block". |
| `direct.provider` | `models.<id>.direct.provider` | |
| `direct.base_url` | `models.<id>.direct.base_url` | |
| `direct.model` | `models.<id>.direct.model` | |
| `direct.npm` | `models.<id>.direct.npm` | |
| `direct.reasoning_effort` | `models.<id>.direct.reasoning_effort` | |
| `openrouter_id` (entries with no `direct`) | `models.<id>.direct = {"provider": "openrouter", "model": <openrouter_id>}` | **derived**: unified with the `direct` shape — see Open choices §7.1. |
| `context` | `models.<id>.context_advertised` | |
| `context` (derived) | `models.<id>.context_usable = {tokens: round(context*0.5), source: "default"}` | **derived**, D8: 50% of advertised until a probe measures it. |
| `output` | `models.<id>.output_max` | |
| `reasoning` | `models.<id>.reasoning` | Defaults to `false` when the old field is absent. |
| — | `models.<id>.effort_ladder` | **derived**: not in the old file at all. `MODEL_EXTRA[id]["effort_ladder"]` — `["minimal","low","medium","high","xhigh","max"]` for `muse-spark` (sourced: docs/models.md), `["low","medium","high"]` for the four other `reasoning: true` entries (conservative default, no measured ladder — see Open choices §7.2), `[]` for every non-reasoning entry. |
| — | `models.<id>.family` | **derived**: `MODEL_EXTRA[id]["family"]` — the model's vendor lineage, not its hosting provider (e.g. `ollama-qwen2.5-coder` → `qwen`, not `ollama`). See the converter table for the full 19-row assignment. |
| — | `models.<id>.tool_calls` | **derived**: always `"unproven"` (conservative unknown; nothing in this repo has measured tool-calling for any model yet). |
| `input_price` | `models.<id>.price_in` | |
| `output_price` | `models.<id>.price_out` | |
| `cache_read_price`? | `models.<id>.price_cache_read`? | |
| `paid_input_price`? | `models.<id>.paid_price_in`? | **Additive field**, not in spec 3.1 — spec's `price_in`/`price_out` is a single pair, but these free-while-capped OpenRouter entries genuinely have two commercial terms (free under a cap, priced after). Kept rather than dropped (D9) — see Open choices §7.3. |
| `paid_output_price`? | `models.<id>.paid_price_out`? | Same as above. |
| `default_for`? | `models.<id>.default_for`? | **Additive field**, not in spec 3.1 — an opencode.jsonc default-selection concern, not consumed by the resolver, kept so the fact is not lost. |

## 2. `catalog/providers.json` → `providers.<id>`

The top-level `$comment` array (order-matters note, field-meaning glossary, the `meta`/`omniroute`
`omniroute_id: null` explanation, the groq/cerebras Cloudflare User-Agent note) is **derived**: its
substance is redistributed into this document (§1–§8) and into per-entry `providers.<id>.$comment`
values in the converter's `PROVIDER_EXTRA`/`EXTRA_PROVIDERS` tables, rather than kept as one block —
see §6 "Where every `$comment` landed".

One object entry (keyed by the api-keys.yml provider name) → one `providers.<id>` entry.

| Old field | Registry path | Notes |
|---|---|---|
| (object key) | `providers.<id>.id` | Unchanged; also the dict key. |
| `omniroute_id` | `providers.<id>.omniroute_id` | |
| `litellm_env` | `providers.<id>.litellm_env` | |
| `litellm_prefix` | `providers.<id>.litellm_prefix` | |
| `api_base` | `providers.<id>.api_base` | Public vendor endpoints only in the source file already; `tests/test_registry_convert.py::PrivacyTests` asserts none look like a private host/IP. |
| `provider_data` | `providers.<id>.provider_data` | |
| — | `providers.<id>.trains_on_prompts` | **derived**: `PROVIDER_EXTRA[id]["trains_on_prompts"]`. Measured for `google_ai_studio` (docs/models.md), reasoned exactly for `meta` (its only leg trains), a documented known-exception default for `openrouter`/`zen` (mixed legs — see Open choices §7.4), an unverified `false` default for the rest. |
| — | `providers.<id>.tier` | **derived**: `PROVIDER_EXTRA[id]["tier"]`, one of `free`/`paid`/`subscription` per the converter table's per-row comment. |
| — | `providers.<id>.windows`? | **derived**, from `.agents/skills/unattended-orchestration/provider-windows.json` — see §5. |

## 3. `catalog/ide-models.json` → `routes.<id>.surfaces`

The top-level `$comment` array (surfaces/gateway glossary, the operator's 2026-09-25 "1M tier is
1000000 everywhere" ruling, the opencode `variants` warning) is **derived** — redistributed into this
document and into schema/converter descriptions; see §6.

One array entry → one `routes.<id>` entry's `surfaces` map (routes are built from the union of
`catalog/ide-models.json` ids and `configuration/omniroute/combos.json` combo names — see §4).

| Old field | Registry path | Notes |
|---|---|---|
| `id` | `routes.<id>.id` | Unchanged; also the dict key. Every ide-models id gets a route even when `combos.json` has no matching combo (`t1-orchestrator-paid`, `t2-worker-paid`, `t3-driver-paid`, `auto`, `auto/smart`, `auto/cheap` — see §4). |
| `name` | `routes.<id>.surfaces.<gateway>.display_name` | Repeated per gateway the id lists (there is one name per id, not per gateway, in the old file). |
| `context` | `routes.<id>.surfaces.<gateway>.context` | Repeated per gateway. |
| `output` | `routes.<id>.surfaces.<gateway>.output` | Repeated per gateway. |
| `reasoning_effort`? | `routes.<id>.surfaces.<gateway>.effort_default`? | Repeated per gateway, only when present. |
| `surfaces.<gateway>` (array of clients) | `routes.<id>.surfaces.<gateway>.clients` | **derived** shape choice: kept nested by gateway (`omniroute`/`litellm`) with the exposing UI surfaces (`opencode`/`zed`/`openhands`) as a `clients` array, rather than flattened to five parallel top-level keys — see Open choices §7.5. |

## 4. `configuration/omniroute/combos.json` → `routes.<id>`

The top-level `$comment` array (the largest single prose block in the old files: role-label glossary,
free-first/fast-skip mechanism, per-model free→paid chain descriptions, the 2026-09-21 privacy
downgrade, the effort-clamp warning, retry/cooldown settings, the "verified against the live catalog"
dates) is **derived** — every fact in it that a resolver or reviewer needs again is carried into a
`providers.<id>.$comment`, `models.<id>.$comment` or `routes.<id>.$comment` next to the data it
explains (§6), rather than kept as one undifferentiated block.

| Old field | Registry path | Notes |
|---|---|---|
| `retired` (array of bare id strings) | **dropped**: no registry entry | These are pre-2026-09-23-rename dead ids (`tier1`, `tier1-clean`, …) with no recoverable leg/class data — they exist only so `apply.sh`/`apply.ps1` know which stale names to prune from the live OmniRoute store. There is nothing to migrate (a deletion target, not model data). `apply.*` keeps reading `combos.json` directly for pruning (spec 3.2 phase 1: the old file stays authoritative for its own consumers until phase 2). |
| `combos[].name` | `routes.<id>.id` | Unchanged; also the dict key. |
| `combos[].strategy` | `routes.<id>.strategy` | Unchanged (`"priority"` for every existing combo). |
| `combos[].context` (display string, e.g. `"1M"`/`"128k"`/`"200k"`) | `routes.<id>.surfaces.omniroute.context_declared` | Kept as its own field, distinct from `surfaces.<gw>.context` (the ide-models numeric window) — this is deliberately the coarser picker/compaction display convention documented in docs/models.md, not the model's real window; the brief calls this field out by name (`incl. context`) so it is not silently merged away. |
| `combos[].models` (ordered leg strings) | `routes.<id>.legs` | Unchanged, in order — the exact strings `tests/test_registry_convert.py::RouteResolutionTests` checks resolve to a `models` × `providers` pair. |

## 5. `.agents/skills/unattended-orchestration/provider-windows.json` → `providers.<id>.windows`

| Old field | Registry path | Notes |
|---|---|---|
| `providers.deepseek.kind/peak_utc/peak_days/offpeak_note/source/verified` | `providers.deepseek.windows[]` | **derived**: six explicit `{days, utc_from, utc_to, price_factor, kind, source, verified}` rows — the two published peak ranges (`price_factor: 1.0`) plus this converter's hand-computed complement (three weekday off-peak ranges + the full weekend, `price_factor: 0.5`). See `PROVIDER_WINDOWS["deepseek"]` in the converter and Open choices §7.6. `providers.deepseek.$comment` also carries ADR 0006 finding C5: this source is only a *partial* match for DeepSeek's real pricing (the "excluding Chinese public holidays" rule is not captured here either) — a measured fact that would otherwise be lost. |
| `providers.meta.kind/busy_utc/busy_days/offpeak_note/source/verified` | `providers.meta.windows[]` | One row, `kind: "load"`, `price_factor: 1.0` (informational, not a price change — see Open choices §7.7), `note`/`source`/`verified` carried through. |
| `providers.anthropic.*` | `providers.cc.windows[]` | **derived**: folded into the `cc` provider (not `anthropic`), because no `anthropic` API provider exists in this registry — `audit-router.py` bans any direct Claude API reference, so `cc` (the Claude Code subscription bridge) is the only Claude-related provider entry. See Open choices §7.8. |
| top-level `_comment` | **derived**: redistributed | Its "re-verify a price entry before relying on a saving" instruction is now a standing note in this document (§7.6) rather than the registry, since it is process guidance for a future `registry.py recalibrate`/`probe`, not a fact about one entry. |

## 6. `configuration/openhands/tier-profiles.json` → `routes.<id>.surfaces.<gateway>.openhands_profile`

The top-level `$comment` array (rename history, the `openai/` LiteLLM-prefix-stripping measurement,
the push-order rationale, `retired_ids` provenance) is **derived** — the two facts a future consumer
would actually need again (the `openai/`-prefix quirk, and which ids are retired) are noted in the
converter's docstring/comments next to `_strip_openhands_profile()`; the push-order rationale is
process guidance for `tools/sync-openhands-profiles.py`, out of this registry's scope.

| Old field | Registry path | Notes |
|---|---|---|
| `gateway_base_url` | **dropped**: no registry field | Container-side constant (`http://host.docker.internal:20128/v1`) shared by every `omniroute-*` profile; a Docker Desktop DNS convention, not a per-model fact. Out of scope for `models`/`routes`/`providers` — it belongs to the (not-yet-built) OpenHands renderer in a later phase, which already knows to prefix `omniroute-*`/`litellm-*` ids with the matching constant. |
| `litellm_base_url` | **dropped**: same reasoning as `gateway_base_url` | |
| `retired_ids` | **dropped**: no registry entry | Same category as `combos.json`'s `retired` (§4) — a prune list for `tools/sync-openhands-profiles.py`, not model data. |
| `tiers[].id` | (used to look up the entry, not stored) | The registry indexes by `<gateway>-<route-id>` (e.g. `omniroute-t1-orchestrator`) computed from the route id, so this field is redundant once matched. |
| `tiers[].model` | **dropped**: redundant | Always `openai/<route-id>` (or, for the one direct-gateway profile, the full upstream ref) — already implied by the route id / `direct_profile` context. |
| `tiers[].max_input_tokens` | `routes.<id>.surfaces.<gateway>.openhands_profile.max_input_tokens` | |
| `tiers[].max_output_tokens` | `routes.<id>.surfaces.<gateway>.openhands_profile.max_output_tokens` | |
| `tiers[].reasoning` | `routes.<id>.surfaces.<gateway>.openhands_profile.reasoning` | |
| `tiers[].gateway`? (only `openrouter-muse-spark-1.3-contributor`) | `routes.spark-1.3-contributor.surfaces.openhands.direct_profile.gateway` | **derived**: this one profile has no `catalog/ide-models.json` surface counterpart (a bespoke direct-OpenRouter profile, full effort ladder, needs its own key) — it becomes a *third*, standalone `surfaces.openhands` entry on the `spark-1.3-contributor` route (spec 3.1 lists `openhands` as its own surface key alongside `omniroute`/`litellm`) rather than nested under `omniroute`/`litellm`. See Open choices §7.9. |
| `tiers[].base_url`? (same profile) | `routes.spark-1.3-contributor.surfaces.openhands.direct_profile.base_url` | |
| `tiers[].model`? (same profile only) | `routes.spark-1.3-contributor.surfaces.openhands.direct_profile.model` | Not dropped here (unlike the general case above) — it names the direct upstream ref, which is not redundant with any route id. |

## 7. Open choices

Spec-silent decisions made while building the converter, one line each:

1. `models.<id>.direct` for llm-models.json entries that only carry `openrouter_id` (no `direct`
   block) is synthesized as `{"provider": "openrouter", "model": <openrouter_id>}` — both are "reached
   outside the OmniRoute gateway" in the same sense, and spec 3.1 defines no separate field for a bare
   OpenRouter slug.
2. Reasoning-capable free OpenRouter legs with no measured effort ladder
   (`openrouter-nemotron-ultra`/`-super`/`-nano-omni`, `openrouter-dots3-note`) default to
   `["low","medium","high"]` — a conservative three-rung guess, not a measurement.
3. `paid_price_in`/`paid_price_out` are additive optional model fields (not in spec 3.1) carrying
   llm-models.json's `paid_input_price`/`paid_output_price` — spec's single `price_in`/`price_out`
   pair cannot express "free under a cap, priced after" without losing the second number.
4. `trains_on_prompts` is provider-level per spec 3.1, which cannot express a provider that hosts both
   a training and a non-training model. Two known, documented exceptions exist: `openrouter` (hosts
   the training `meta/muse-spark-1.3-contributor` leg *and* several non-training legs — the provider
   flag is `false`, matching the majority of its legs and the existing `t2-worker-clean`/
   `t3-driver-clean` usage) and `zen`/`opencode-zen` (same pattern: its free contributor-promo leg
   trains, its paid `deepseek-v4.1-flash` leg does not, and only the latter is ever used in a `-clean`
   combo). Both exceptions are recorded in the affected provider's `$comment`, and neither changes any
   current filter outcome (the training leg in each case is either never used in a `-clean` combo, or —
   for `t1-orchestrator-clean`'s openrouter contributor leg — the operator already accepted this
   exact trade-off on 2026-09-21, per `combos.json`'s own `$comment`).

   PRIV finding, 2026-09-26: provider-level `trains_on_prompts` alone was never enough — a
   privacy-sensitive card was routed onto `t3-driver-free-only` (groq/qwen, cerebras/qwen — both
   `trains_on_prompts: false`, but `tier: "free"`), because the resolver's privacy filter checked only
   `trains_on_prompts`, never `tier`. Fixed by a single `private_safe(provider_id, model_id, registry)`
   predicate (`tools/registry.py`, imported by `tools/autoos_resolver.py`) that both `registry.py
   check`'s rule 3 and the resolver's route-level privacy filter now call: a leg is private-safe only
   when its provider's `tier` is not `"free"`, its `trains_on_prompts` is exactly `false`, *and* the
   model does not carry its own `trains_on_prompts: true`. That third condition is the new, additive
   `models.<id>.trains_on_prompts` field (optional; missing means inherit the provider) — needed
   because `mistral` (paid, non-training) also serves `mistral-code-latest`, a free pool that trains
   (`docs/models.md`: "FREE 1B/mo pool ... same key bills past it"); `tests/run-tests.sh`'s own
   combos.json shell check already encodes this as a *-clean route invariant ("`mistral/mistral-code`"
   counts as a free leg that must never appear in a `-clean` combo") — the model-level field lets the
   registry express the same fact `registry.py check` can verify, without also marking
   `mistral-small-latest` (same provider, does not train) unsafe. This changes no committed `-clean`
   route's outcome: none of `t1-orchestrator-clean`/`t2-worker-clean`/`t3-driver-clean`'s *available*
   legs are a free-tier provider or carry a model-level override (`t1-orchestrator-clean`'s only leg is
   itself flagged `unavailable_legs` today; `t2-worker-clean`/`t3-driver-clean`'s zen leg is likewise
   `unavailable_legs` — see Open choice above and `UnavailableLegTests`). It does change the resolver's
   dynamic privacy filter, which applies to *any* route (not just `-clean` ones) whenever a card carries
   `privacy: sensitive`: `t1-orchestrator`/`t2-worker`/`t3-driver`/`t3-driver-free-only` (main and
   free-only variants) all now correctly lose their free-tier legs for a sensitive card, which is the
   behaviour the finding asked for.

   PRIV2 finding, 2026-09-26 (cross-family review of PRIV `d5281d1` by DeepSeek and qoder): three more
   gaps in `private_safe()`/rule 3, all fixed together:
   - **Model-level `tier` override**, mirroring the existing model-level `trains_on_prompts` override:
     `models.<id>.tier` (optional; missing means inherit `providers.<id>.tier`) lets one paid, direct-key
     leg on an otherwise-free provider be private-safe — the case `trains_on_prompts` alone cannot
     express. `zen`'s own `tier` stays `"free"` (its promo pool), but `models.'deepseek-v4.1-flash'`
     (the `opencode-zen/deepseek-v4.1-flash` leg used in `t2-worker-clean`/`t3-driver-clean`) now carries
     `"tier": "paid"`, citing `tests/run-tests.sh`'s own combos.json policy line: "Direct-key legs
     (mistral-small, deepseek, openrouter paid, zen paid) bill past the pool on the same key, so they
     stay." `private_safe()`'s "effective tier" is `models.<id>.tier` when present, else the provider's.
   - **Model-level `trains_on_prompts: true`** is now set on both contributor models —
     `models.'meta/muse-spark-1.3-contributor'` (OpenRouter, paid) and
     `models.'muse-spark-1.3-contributor-free'` (Zen, free promo) — citing the same combos.json policy
     line's other half: "-contributor (trains by contract) is banned in
     t2-worker-clean/t3-driver-clean; t1-orchestrator-clean carries it deliberately since the 2026-09-21
     contributor-only block (paid-only, trains)." Previously only the *provider*-level flags carried this
     (Open choice above); the model-level flag is what `private_safe()` and rule 3 actually key on.
   - **Fail closed, exactly**: `private_safe()` used `dict.get(...) is True` for the model-level
     `trains_on_prompts` check, which let any non-`True` truthy value (`1`, `"no"`, an explicit `null`)
     slip through as "safe". Rewritten so the *effective tier* must be exactly `"paid"` or
     `"subscription"` (an unrecognised or missing value is unsafe, not a silent pass), and a *present*
     `models.<id>.trains_on_prompts` key must be exactly `false` to be safe (only a genuinely *missing*
     key inherits the provider) — `true`, `null`, `1`, `"no"`, `0`, ... are all unsafe.
   - **Rule 3 now checks every leg of a `-clean` route**, including one flagged in
     `unavailable_legs` or reached through a provider marked `available: false` — the OmniRoute gateway
     does not consult that registry-only flag, and `combos.json`/`apply.sh` still push the leg verbatim,
     so a leg an operator flagged down is still one the gateway may actually serve. This closes the gap
     the original PRIV paragraph above relied on ("none of the committed `-clean` routes' *available*
     legs are a free-tier provider or carry a model-level override" — true only because the *unavailable*
     ones were never checked). The one deliberate exception is `tools/registry.py`'s
     `CLEAN_ROUTE_EXEMPTIONS = {"t1-orchestrator-clean": "..."}`: its only leg
     (`openrouter/meta/muse-spark-1.3-contributor`) now trains by the model-level flag above, and the
     2026-09-21 operator decision to carry it anyway (paid-only, not trains-nothing;
     `combos.json`'s own `$comment`) is preserved as a named, visible exemption — `registry.py check`/
     `validate` print it as an `"info: ..."` line (`privacy_exemption_lines()`), always, never silently,
     and never as a `check_registry()` failure. This exemption is **not** consulted by the resolver's own
     privacy filter (`tools/autoos_resolver.py filter_routes()`): it calls `private_safe()` on every
     *available* serving leg of *every* route regardless of id, and `t1-orchestrator-clean` has no
     available leg at all today, so a `privacy: sensitive` card can never reach it either way.
5. `routes.<id>.surfaces` is keyed by gateway (`omniroute`/`litellm`), matching
   `catalog/ide-models.json`'s own shape, with the UI surfaces that expose it (`opencode`/`zed`/
   `openhands`) nested as a `clients` array rather than flattened to five parallel top-level surface
   keys. This preserves the source data's actual "gateway → which clients show it" structure exactly;
   the one route that needs a genuine third, standalone `openhands` surface (`spark-1.3-contributor`,
   see §6) still gets one.
6. `providers.deepseek.windows` off-peak ranges are this converter's own hand-computed complement of
   the two published peak ranges (`00:00–01:00`, `04:00–06:00`, `10:00–23:59` on weekdays, plus the
   full weekend at `price_factor: 0.5`) — computed once by hand and hard-coded in
   `PROVIDER_WINDOWS["deepseek"]` rather than derived at run time, so the output stays simple to review
   and exactly as deterministic. `23:59` stands in for "through end of day" since the registry's
   `utc_from`/`utc_to` pattern has no `24:00`.
7. `kind: "load"` windows (`meta`, `cc`) carry `price_factor: 1.0` — spec 6.4 defines `windows` as a
   price-factor list, but `provider-windows.json` also encodes non-price "busy hours" data for
   `anthropic`/`meta` that would otherwise be lost (D9). `1.0` means "no price change"; `kind`, `note`
   and `verified` are additive optional fields on the `window` object carrying the rest of that fact.
8. `provider-windows.json`'s `anthropic` key has no matching `providers.json` id (no `anthropic` API
   provider exists — `audit-router.py` bans one), so its window is folded into the new `cc` provider
   entry instead of being dropped: `cc` is where every Claude-related fact in this registry lives.
9. `antigravity` and `cc` are new `providers.<id>` entries with no `catalog/providers.json` /
   api-keys.yml counterpart (OAuth/subscription bridges, no key to mirror) — added because
   `configuration/omniroute/combos.json` legs reference them and the "every leg resolves to a
   `models` × `providers` pair" rule (spec 3.1) requires it. `antigravity.trains_on_prompts` defaults
   conservatively to `true`: no published data-training policy was found for this OAuth consumer
   surface (checked docs/ and `.agents/skills/unattended-orchestration/`, 2026-09-25); it is never
   referenced by a `-clean` route today, so the conservative default changes no current outcome.
10. `routes.<id>.class` (`free`/`cheap`/`mid`/`frontier`) has no field in any old file. Assigned by
    role + pricing per route (`COMBO_CLASS` in the converter), deliberately orthogonal to the
    `t1`/`t2`/`t3` naming: e.g. `t1-orchestrator` (the *orchestrator* role) is `cheap` (spark pricing is
    the cheapest per-token family in the registry), while `t2-orchestrator` and `opus-4-6` are
    `frontier` (they lead with Opus). `auto`/`auto/smart`/`auto/cheap` (OmniRoute's own dynamic
    strategy, no fixed legs) are approximated as `mid`/`mid`/`cheap` respectively, since their real
    class varies per call.
11. Two pinned single-model routes are, by the existing repository's own naming convention, named
    identically to the bare model spelling of their own leading leg: `routes.gemini-3.8-flash` and
    `routes.deepseek-v4.1-flash` each share their id with a `models.<id>` entry (the native-gemini and
    opencode-zen-bare-spelling legs, respectively). Spec 3.1's "ids are unique across sections" rule is
    therefore read as scoped to `providers`/`models`/`clients` (sections whose ids are looked up from a
    bare `provider/model` leg string, where a collision would be genuinely ambiguous) — a route id may
    coincide with a model id, but never with a provider or client id.
    `tests/test_registry_convert.py::IdUniquenessTests` encodes exactly this scope.
12. `t1-orchestrator-paid`, `t2-worker-paid`, `t3-driver-paid` (from `catalog/ide-models.json`, LiteLLM
    surface only) get a `routes.<id>` entry with `legs: []` — their real legs are hand-curated in
    `configuration/litellm/config.yaml`, which is not one of the six old files in scope for this
    converter. `auto`, `auto/smart`, `auto/cheap` also get `legs: []` — they name OmniRoute's own
    built-in dynamic `auto` strategy over every registered connection, not a static combo, so there is
    nothing to enumerate. All six carry a `$comment` explaining why.
13. Every numeric `policy` value is wrapped `{"value": …, "source": …}` (`modes.*.theta`/`lambda`,
    `effort_rules.max_tokens_floor.*`) **except** `policy.bucket_table`, which instead wraps the whole
    table once — `{"source": "default", "table": {…}}` — so that `table` stays byte-identical to
    `tools/autoos_resolver.DEFAULT_BUCKET_TABLE`'s own shape (JSON-mirrored: tuples become arrays,
    the `tests` dict's boolean keys become the strings `"true"`/`"false"`) for the resolver to load
    directly. A future loader must convert `"true"`/`"false"` back to real booleans before calling
    `autoos_resolver.points()`/`bucket()` with it.
14. `policy.seed_priors[class][bucket]` uses `{alpha, beta, source}` (spec 5.6's own shape). `free` and
    `cheap` get an informed prior at `S0`/`S1` (`alpha=2, beta=1`, i.e. one observed pass over an
    uninformative `1/1` baseline — the spec text ("routes pass S0–S1") gives no exact trial count, so
    this is a deliberately modest shift, not a fabricated count) and a stronger update at `S2`–`S4`
    (`alpha=1, beta=5`, i.e. the uninformative baseline plus the four recorded multi-file failures —
    spec 5.6: "4/4 multi-file failures with fabricated reports or partial edits"). `mid` and `frontier`
    have no 2026-09-25 measurement at any bucket, so they stay at the uninformative `alpha=1, beta=1,
    source: "default"`.
15. `policy.latency_seed`, `policy.verify_tokens`, `policy.brief_tokens` have no measured values yet;
    every entry is `source: "default"` with a small, clearly-a-guess number
    (`latency_seed` 5/8/15/30 minutes for free/cheap/mid/frontier; `verify_tokens` and `brief_tokens`
    scale up by bucket, `2000..32000` and `500..3000` respectively). `policy.handoff_caps` uses spec
    8.3's own numbers verbatim with `source: "2026-09-25"` (the spec's own decision date, D16).
16. `policy.risk_rules` (spec 5.7's "path patterns") are directory-level globs
    (`lib/**/*.psm1`, `lib/**/*.sh`, `setup.sh`, `setup.ps1`, `configuration/api-keys.yml`,
    `configuration/**/*.env`, `**/*.vault.yml`, `.github/workflows/**`, `**/*settings*.json`) rather
    than function-level — the registry has no way to express "this diff touches `os.environ[PATH]`".
    Broad by design (AGENTS.md's safety-first ethos: over-flagging a review as high-risk is cheap,
    under-flagging is not). "Deletion paths" is modeled as a `diff_deletion` trigger type (any file
    removal anywhere in the diff), not a path glob — the spec's own wording ("touches ... deletion
    paths") is about what the diff *does*, not a fixed directory.
17. `clients.<id>.gateway_mode` is `"native"` for `opencode` (it talks to OmniRoute through its own
    `opencode.jsonc` provider config, built specially by `tools/autoos-agent.py`, not through the
    `omniroute run <client> --` wrapper `tools/autoos_clients.py` uses for `qwen`/`gemini`/`codex`),
    `"omniroute-run"` for those three, and `"none"` for `claude`/`agy`/`qoder` (own account/
    subscription, never the gateway).
18. `clients.<id>.skills_dir` is `.agents/skills` for `opencode` and `.claude/skills` for `claude`
    (both already true today, per AGENTS.md §8) and `null` for `qwen`/`gemini`/`codex`/`agy`/`qoder` —
    their native skills directories are unmeasured (plan.md Phase C5 is the not-yet-run spike that
    determines them).
19. `clients.<id>.signin_check` is `"env:AUTOOS_OMNIROUTE_KEY"` for the four gateway clients (a
    presence check, not a subprocess probe), the literal probe string `tools/autoos_clients.py` already
    runs for `agy` (`"agy models"`), and `null` for `claude`/`qoder` — **finding, not a choice**:
    `tools/autoos_clients.py`'s `CLIENTS` table defines a `signin_probe` for `agy` only, even though
    `qoder` also documents "own auth, not the gateway"; this is a real gap in that module, left
    unfixed here (out of scope for a registry-migration task) and reported as a lesson.
20. `registry.version` is the string `"2026-09-25"` (this migration's date) rather than copying either
    old file's own `"version"` convention (`catalog/llm-models.json` had `"2026-09-14"`;
    `catalog/ide-models.json`/`configuration/omniroute/combos.json`/
    `configuration/openhands/tier-profiles.json` had none) — spec 3.1 does not define this field's
    format beyond "registry content date", so the converter pins `REGISTRY_VERSION` (the migration date) and renders are byte-identical across runs and days.

## 8. `catalog/ai-registry.schema.json` — additive fields not in spec 3.1

For traceability, every field the schema defines beyond spec 3.1's literal list, all justified above:
`models.<id>.display_name`, `models.<id>.default_for`, `models.<id>.paid_price_in`,
`models.<id>.paid_price_out`, `providers.<id>.available`, `routes.<id>.unavailable_legs`,
`routes.<id>.surfaces.<gw>.context_declared`, `routes.<id>.surfaces.<gw>.direct_profile`, and the
`window` object's `kind`/`note`/`verified`. `providers.<id>.available` and
`routes.<id>.unavailable_legs` exist specifically for the operator's 2026-09-25 "OpenRouter is not
topped up" decision (docs/plans/2026-09-25-routing-v2-plan.md, "Operator steps"): every openrouter
provider and every openrouter leg stays in the registry, unchanged and in the same order (so the data
still mirrors today's files for the phase-1 equality gate), each marked `available: false` with a
`$comment` citing the decision (`tools/registry-convert.py`'s `mark_openrouter_unavailable()`). Single legs that are down while their provider still serves are listed in `UNAVAILABLE_LEGS` and flagged by `mark_legs_unavailable()` the same way (operator 2026-09-26: `opencode-zen/deepseek-v4.1-flash`, 402 in the tool-calling probe; L0 2026-09-26T11:44Z: `cerebras/gpt-oss-120b` 402 and `cerebras/qwen-3.8-27b` 401 credits exhausted, re-probed weekly).

## 9. Where every `$comment` prose piece landed

| Old `$comment` (file) | Landed in |
|---|---|
| `catalog/providers.json` (field-meaning glossary, ordering-matters note, `SambaNova` casing) | This document §2; per-provider notes stay as data comments only where they explain *that provider's* value (e.g. `omniroute`'s "not a model provider" comment). |
| `catalog/ide-models.json` (surfaces/gateway glossary, 1M-tier operator ruling, opencode `variants` warning) | This document §3, §6.4/§7.5 of the mapping table above; the `variants` warning is process guidance for a future opencode renderer (task A4/A5), not a registry fact. |
| `configuration/omniroute/combos.json` (role labels, free-first/fast-skip mechanism, per-family chain descriptions, 2026-09-21 privacy downgrade, effort-clamp warning, retry/cooldown settings, verification dates) | Split by fact: the privacy downgrade → `providers.openrouter.$comment` and `providers.zen.$comment` (Open choice §7.4); per-model chain/training facts → the relevant `models.<id>.$comment`; role labels/mechanism/retry settings → this document (no single registry field owns "how the gateway retries", since spec 3.1 has no such field — out of scope for the model registry itself). |
| `configuration/openhands/tier-profiles.json` (rename history, `openai/`-prefix measurement, push-order rationale, `retired_ids` provenance) | This document §6; the `openai/`-prefix measurement is a code comment in `tools/registry-convert.py` next to `_strip_openhands_profile()`. |
| `.agents/skills/unattended-orchestration/provider-windows.json` (re-verify-before-relying note) | This document §5 / §7.6 (process guidance for a future `registry.py recalibrate`/`probe`). |

## 10. Task A4a — rendering `combos.json` back from the registry (phase 1 render)

Spec 3.2 phase 1 ("render output equals today's generated files semantically") for
`configuration/omniroute/combos.json`: `tools/registry.py render omniroute` reads
`catalog/ai-registry.json` and produces the combos.json shape via the pure function
`render_omniroute(registry) -> dict`. It is the reverse of §4 above (`build_routes()` in
`tools/registry-convert.py`), restricted to the 15 routes that actually came from a
combo (`legs` non-empty — the LiteLLM-only `*-paid` routes and the dynamic `auto*`
routes carry `legs: []` per §4/Open choice 12 and have no combo). Field mapping back:

| Registry path | `combos.json` path | Notes |
|---|---|---|
| `routes.<id>.id` | `combos[].name` | Unchanged. |
| `routes.<id>.strategy` | `combos[].strategy` | Unchanged (`"priority"` for all 15). |
| `routes.<id>.surfaces.omniroute.context_declared` | `combos[].context` | Unchanged (§4's own mapping, reversed). |
| `routes.<id>.legs` | `combos[].models` | Unchanged, **in order** — a `strategy: "priority"` fallback chain, so leg order is real semantic data the render must preserve exactly (it does: `legs` is never reordered by `mark_openrouter_unavailable()`/`mark_legs_unavailable()`, only annotated via the sibling `unavailable_legs` key — see the next point). |

Two intentional, documented exceptions to strict equality (never a silently dropped
field — both are covered by `tests/test_registry_render.py` and by
`tools/registry.py`'s own `omniroute_diff()`/`_canonical_omniroute()`):

1. **`$comment` is not reproduced, not even partially.** combos.json's top-level
   `$comment` (~190 lines: role-label glossary, free-first/fast-skip mechanism,
   per-family chain descriptions, retry settings, verification dates) is pure human
   documentation — neither `apply.sh` nor `apply.ps1` reads a `"$comment"`/`"comment"`
   key anywhere (confirmed by reading both scripts, 2026-09-26: they only touch
   `.name`/`.strategy`/`.models`/`.context` per combo and top-level `.retired`). Its
   substance was already redistributed into per-entry `providers`/`models`/`routes`
   `$comment` fields during the A1/A2 migration (§4 and §9 above document exactly
   where each fact landed) — reproducing the whole block verbatim here would
   duplicate that already-migrated prose with no consumer that reads it and two
   copies to keep in sync. The render therefore emits only the spec-3.2 marker line
   (`"generated from catalog/ai-registry.json - do not edit"`), and semantic equality
   for this render ignores the entire `$comment` key, not only that one line — a
   stricter reading than spec 3.2's literal "equality ignores that one line" wording,
   justified by the "intentionally differs, already documented elsewhere" clause of
   task A4a's brief.
2. **`combos[]`/`retired` array order is not reproduced; `retired` itself is a
   hardcoded constant, not derived from the registry.** `combos.json`'s `retired`
   array (pre-2026-09-23-rename dead ids: `tier1`, `tier1-clean`, …) has no registry
   entry at all — §4 above: "there is nothing to migrate". `tools/registry.py` carries
   it as a literal constant, `OMNIROUTE_RETIRED_IDS`, the same convention
   `registry-convert.py` uses for facts no source file carries (`PROVIDER_EXTRA`,
   `MODEL_EXTRA`, `COMBO_CLASS`, …) — the values still match today's file exactly
   (verified in `tests/test_registry_render.py`), only their origin is a hand-copied
   table rather than a registry field. Separately, the order of both the `combos`
   array and the `retired` array carries no semantics: `apply.sh` iterates
   `for c in data.get("combos", [])` and builds `current = {c["name"] for c in
   ...}`; `apply.ps1` does `foreach ($combo in $combos)` and filters retired names
   with `-cnotcontains` — both look combos up by name and retired ids up by
   membership, never by position. `render_omniroute()` therefore emits `combos` in a
   canonical order (sorted by route id) rather than replicating today's hand-edited
   order, and `omniroute_diff()` compares `combos` as a name-keyed map and `retired`
   as a set, not as ordered lists — this is *not* an exception to array-order
   equality inside a single combo's `models` list, which stays a real, ordered
   fallback chain and is compared as such.

Everything else — every leg, including ones an operator has since flagged
`unavailable` (`routes.<id>.unavailable_legs`, `providers.openrouter.available:
false`, 2026-09-25/26) — renders byte-for-byte equal to today's file: those
operator decisions only add the sibling `unavailable_legs` annotation, they never
remove or reorder anything in `legs` itself, and today's committed `combos.json`
already lists those same dead legs unchanged. No new equality exception was needed
for that case, confirming the brief's own example.

## 11. Task A4b — rendering `configuration/litellm/config.yaml`'s managed blocks back from the registry (phase 1 render)

Spec 3.2 phase 1 for `configuration/litellm/config.yaml`'s "model groups": `tools/
registry.py render litellm` reads `catalog/ai-registry.json` and produces the exact
text of each AUTOOS-MANAGED tier block `tools/sync-router-tiers.py` already owns
(`# AUTOOS-MANAGED-START <tier>` … `# AUTOOS-MANAGED-END <tier>`), via the function
`render_litellm_blocks(registry, config_text) -> {tier: block_text}`. It is the
reverse of that tool's own `combos_refs()` + `provider_maps()` + `render_block()`
pipeline, restricted to `SYNCED_TIERS` (today: `t2-worker`, `t3-driver` — `t1-
orchestrator` and every `*-paid`/`*-free-only` group are hand-curated, never
sync-managed, unchanged by this task).

`render_litellm_blocks()` reuses `tools/sync-router-tiers.py`'s own `Leg`/
`render_block()`/`locate_blocks()`/`parse_block()`/`leading_indent()` by importing
that module by path (`importlib.util.spec_from_file_location`, the same technique
`tools/registry.py`'s own `_build_fresh_registry()` already uses for
`tools/registry-convert.py`) rather than copying any of it. The one piece of shared
logic that genuinely needed factoring — the (prefix, api_base, env_key) map keyed by
OmniRoute provider id, previously inlined in `sync-router-tiers.py`'s own
`provider_maps()` — is now `provider_maps_from_dict(providers)`, and `provider_maps
(path=None)` is a thin wrapper that loads `catalog/providers.json` and calls it.
`tools/registry.py` calls `provider_maps_from_dict()` with the registry's own
`providers` section instead. This is safe because the two sections share the exact
same field names for every fact this needs — `omniroute_id`, `litellm_prefix`,
`api_base`, `litellm_env` — confirmed field-for-field in §2 above (`catalog/
providers.json` → `providers.<id>`: those four fields carry over unchanged). `tests/
helpers/check-provider-registry.py`'s existing `sync.provider_maps()` call is
untouched — its return value is unchanged since the wrapper still does exactly what
the old inline code did.

Field mapping back, per tier:

| Registry path | `config.yaml` managed-block content | Notes |
|---|---|---|
| `routes.<tier>.legs` | one `- model_name: <tier>` entry per leg, in order | Unchanged, **in order** — same ordered-fallback-chain reasoning as `render_omniroute()`'s `legs` → `combos[].models` mapping (§10). A leg whose provider is gateway-only (`tools/sync-router-tiers.py`'s `GATEWAY_ONLY = {"antigravity", "cc"}` — OAuth/subscription bridges with no LiteLLM transport or key) is dropped, exactly as `combos_refs()` already drops it when reading `combos.json` today; `routes.t2-worker.legs` still carries `antigravity/gemini-3.7-flash-high` (a real leg), and the render must (and does) drop it, not silently keep it or silently omit it without a test noticing — `tests/test_registry_render.py::LitellmRenderMatchesTodayTests::test_gateway_only_leg_is_dropped_not_silently_kept_or_missing` pins exactly this leg. |
| `providers.<id>.litellm_prefix` (when the leg's provider carries one) | the leg's `model:` line's LiteLLM-transport prefix | `Leg.prefix` (`tools/sync-router-tiers.py`), keyed by the leg's OmniRoute-id first segment — unchanged from today's `provider_maps()`-sourced value, only the source dict changed. |
| `providers.<id>.api_base` (when present) | the leg's `api_base:` line | Same as above; absent for a leg whose provider passes straight through (no OpenAI-compatible gateway). |
| `providers.<id>.litellm_env` (when present; else `<PROVIDER>_API_KEY`) | the leg's `api_key: os.environ/<name>` line | Same fallback rule `Leg.__init__` already applies today. |

One documented input the render still needs that is **not** a registry fact, and is
therefore **not** an equality exception (contrast `render_omniroute()`'s `$comment`/
array-order exceptions in §10 — this render has none):

- **A leg's hand-tuned extra `litellm_params` line(s)** — an `rpm` cap and its
  trailing comment, for the two `t2-worker` legs (`gemini/gemini-3.8-flash`,
  `groq/openai/gpt-oss-120b`) and one `t3-driver` leg (`mistral/mistral-small-
  latest`) that carry one today. Grepping `catalog/ai-registry.json`, its schema,
  `tools/registry-convert.py` and this document for `rpm` finds nothing: no
  rate-limit field exists anywhere in the registry, and `tools/sync-router-tiers.py`
  has never derived this value from `combos.json` either — `_MANAGED_PARAM_KEYS =
  ("model", "api_key", "api_base")` in that tool names exactly what it regenerates,
  and anything else present in an existing block (`parse_block()`) is carried across
  a resync, never invented. `render_litellm_blocks(registry, config_text)` therefore
  takes `config_text` — today's own `configuration/litellm/config.yaml` — as a
  second argument for exactly this reason, purely to extract each leg's existing
  extra lines the same way `tools/sync-router-tiers.py`'s own `rewrite()` already
  does, and reproduces them unchanged. The function stays pure with respect to I/O
  and the clock (the same two inputs always render the same text; no file is opened
  inside it, matching `render_omniroute(registry)`'s own contract) — it simply takes
  one more input than that function does, because this generated file, unlike
  `combos.json`, carries one small piece of hand-tuned data no registry field owns.
  Because this value is reproduced exactly rather than dropped, the render is
  byte-for-byte identical to today's committed managed blocks with **zero** documented
  equality exceptions — `tests/test_registry_render.py::LitellmRenderMatchesTodayTests
  ::test_render_matches_committed_config_byte_for_byte` asserts this directly via
  `registry.litellm_diff()`, and `python3 tools/registry.py render litellm --check`
  confirms it against the real files.

`tools/registry.py render litellm --check` never writes `configuration/litellm/
config.yaml` itself (phase 1 proves equality only, exactly like `render omniroute`
above — `tools/sync-router-tiers.py` keeps owning the actual rewrite until a later
phase switches it to read the registry instead of `combos.json`). A leg changed only
in the registry (not yet reflected in `config.yaml`) makes `--check` exit 1 naming
the tier it belongs to, never silently passing — `tests/test_registry_render.py::
ChangedLegFailsLitellmCheckTests::test_changed_leg_exits_one_and_names_the_tier`
pins this for a mutated `t3-driver` leg.

## 12. Task A4c — rendering `catalog/ide-models.json` back from the registry, and retargeting `tools/sync-ide-models.py`

Spec 3.2 phase 1 for `catalog/ide-models.json` (the file §3 above maps *into* the
registry): `tools/registry.py render ide` reads `catalog/ai-registry.json` and
produces that file's own shape via the pure function `render_ide(registry) ->
dict`. It is the reverse of §3's mapping (`_build_surfaces()` /
`build_routes()` in `tools/registry-convert.py`), and covers every route — unlike
`render omniroute`, there is no legs-non-empty filter, because §3 already
established that every `ide-models.json` id gets a route even when `combos.json`
has none (the `*-paid` and `auto*` routes).

Field mapping back, per route:

| Registry path | `ide-models.json` path | Notes |
|---|---|---|
| `routes.<id>.id` | `models[].id` | Unchanged; also the dict key. |
| `routes.<id>.surfaces.<gateway>.display_name` (first of `omniroute`/`litellm` present) | `models[].name` | §3's forward mapping copied the *same* name into every gateway the id lists — reversing it just reads it back from whichever gateway is present. Verified field-for-field equal across every route that lists both gateways in today's registry (2026-09-26: zero disagreements), so the omniroute-first pick is never actually exercised as a tie-break by real data; `IDE_GATEWAYS = ("omniroute", "litellm")` names the priority order explicitly rather than leaving it to whichever key a dict happens to iterate first. |
| `routes.<id>.surfaces.<gateway>.context` (same pick) | `models[].context` | Same reasoning. |
| `routes.<id>.surfaces.<gateway>.output` (same pick) | `models[].output` | Same reasoning. |
| `routes.<id>.surfaces.<gateway>.effort_default`? (same pick) | `models[].reasoning_effort`? | Same reasoning; omitted from the render when absent, exactly like the field itself in today's file. |
| `routes.<id>.surfaces.<gateway>.clients` for every gateway present with one | `models[].surfaces.<gateway>` | Rebuilt as `{gateway: clients}` for every `omniroute`/`litellm` surface the route has — `openhands` is never a key here (see below). |

One documented equality exception (the same one `render omniroute` needed, §10
exception 1 — nothing new):

1. **`$comment` is not reproduced.** `catalog/ide-models.json`'s top-level
   `$comment` (the surfaces/gateway glossary, the operator's 2026-09-25 "1M tier
   is 1000000 everywhere" ruling, the opencode `variants` warning) was already
   redistributed into this document (§3, §9) and into schema/converter
   descriptions during the A1/A2 migration — `tools/sync-ide-models.py`'s own
   `load_catalog()` never reads a `"$comment"` key either (only `doc["models"]`).
   The render emits only the spec-3.2 marker line, and `ide_diff()` ignores the
   whole `$comment` key, mirroring `omniroute_diff()`'s own treatment exactly.

One thing that is **not** an equality exception, because the render reproduces it
exactly rather than dropping it — contrast the point above:

- **`models[]` array order.** Unlike `combos.json`'s `combos`/`retired` arrays
  (§10 exception 2, proven insignificant by reading `apply.sh`/`apply.ps1`),
  nothing in this repository shows that `ide-models.json`'s list order is
  insignificant — its own top-level comment says the opposite: *"List order =
  picker order on every surface."* No registry field carries this order (`routes`
  is a dict, and `catalog/ai-registry.json` itself stores every key
  alphabetically — `tools/registry-convert.py`'s `build_registry()` calls
  `json.dumps(..., sort_keys=True)`), so `render_ide()` carries it as a literal,
  hand-copied constant, `IDE_MODEL_ORDER` — the same convention `render_omniroute()`
  uses for `OMNIROUTE_RETIRED_IDS` (§10) and `tools/registry-convert.py` uses for
  `PROVIDER_EXTRA`/`MODEL_EXTRA`/`COMBO_CLASS`. `render_ide()` raises, naming every
  id at once, if a future route is added or removed without updating that
  constant (`tests/test_registry_render.py::MissingRouteFailsIdeRenderTests`) —
  never a silent reorder or drop. **Follow-up**: a registry field such as
  `routes.<id>.surfaces.ide_order` would let this render drop the constant; not
  added here, since spec 3.1 lists no such field and task A4c's brief is "keep
  today's value via the render" for exactly this kind of gap (the same choice
  A4b made for the litellm `rpm` lines it cannot derive either, §11). Because
  order is reproduced exactly (not just each entry's content), `render_ide()`
  achieves the same "zero exceptions beyond `$comment`" result `render_litellm_
  blocks()` achieved in §11 — `tests/test_registry_render.py::
  IdeRenderMatchesTodayTests::test_render_matches_committed_ide_models_
  semantically` asserts this against the real files.

**`surfaces.openhands` is out of scope for this render.** A route's *standalone*
`surfaces.openhands` entry (§6's last rows — today only
`spark-1.3-contributor`'s `direct_profile`) is never a gateway key in
`ide-models.json`'s own `surfaces` field (that field is `{gateway: [client,
...]}`, and `openhands` is one of the *clients*, not a gateway) — `IDE_GATEWAYS`
deliberately excludes it, so `render_ide()` neither reads nor reproduces it. It
belongs to a `tools/registry.py render tier-profiles`-shaped task (task A4d, not
this one), same as `openhands_profile` sub-fields already excluded from this
mapping.

### Retargeting `tools/sync-ide-models.py` (spec 3.2's own "existing `sync-ide-
models.py` retargeted" row)

`tools/sync-ide-models.py`'s default, unflagged run now sources its model list
from `load_from_registry()`, which calls `tools/registry.py`'s `render_ide()` on
`catalog/ai-registry.json` (default path) instead of parsing `catalog/ide-
models.json` directly — the exact retarget spec 3.2's table names for
`opencode.jsonc`'s `AUTOOS-MANAGED` blocks and the Zed lists that same tool feeds
into `configuration/openhands/tier-profiles.json`/`config.toml`. Because
`render ide --check` proves `render_ide(catalog/ai-registry.json)` is
semantically identical to `catalog/ide-models.json` today, this changes nothing
about what gets written to any of the three generated files on an unmodified
checkout — `tests/test_sync_ide_models.py::RepoTests::test_the_checkout_is_in_
sync` (unchanged, no flags) still passes, proving it.

`--catalog PATH` stays as an explicit, lower-level override that reads an
`ide-models.json`-shaped file directly via the pre-existing `load_catalog()`
(now sharing its validation with the new path through a factored-out
`_validate_models()`, so neither path can silently diverge on what counts as a
valid model list) — every test in `tests/test_sync_ide_models.py` written before
this task always passes `--catalog` explicitly and is therefore completely
unaffected by the retarget; the new coverage for the retarget itself
(`RegistrySourcedTests`) uses temp copies of `catalog/ai-registry.json` and the
new `--registry` flag instead, mirroring `render_ide()`'s own gateway-priority
behaviour (mutating only `surfaces.omniroute` on a route that also lists
`litellm` still flows through to both gateways' entries in `opencode.jsonc`,
since `models[].context`/`.output` are single shared fields — mutating a
`litellm`-only route like `t3-driver-paid` exercises the litellm-only fallback
of the same priority pick). **Follow-up**: `catalog/ide-models.json` itself is
not deleted by this task (spec 3.2's own two-phase rule — no old file is removed
before every consumer has moved off it; the Zed and V1 OpenCode writers in
`lib/` still read it directly at install time on the target machine, unaffected
by this repo-internal tool's retarget) — that is a later phase-2 step, together
with whatever also retargets those `lib/` writers.

## 13. Task A4d — rendering `configuration/openhands/tier-profiles.json` back from the registry, and retargeting `tools/sync-openhands-profiles.py`

Spec 3.2 phase 1 for `configuration/openhands/tier-profiles.json`: `tools/registry.py
render openhands` reads `catalog/ai-registry.json` and produces that file's own
shape via the pure function `render_openhands(registry) -> dict`. It is the
reverse of §6 above (the `openhands_profile`/`direct_profile` fields §6's table
already lands in the registry during the A1–A3 migration) — unlike `render
omniroute`'s legs-non-empty filter or `render ide`'s "every route" rule, this
render's membership is "every `routes.<id>.surfaces.<omniroute|litellm>.
openhands_profile`, plus every standalone `surfaces.openhands.direct_profile`" —
computed by `_openhands_tier_membership()` and checked both ways against
`OPENHANDS_TIER_ORDER`, the same bidirectional membership check `tools/sync-ide-
models.py`'s own `rewrite_tier_profiles()` already ran against `catalog/ide-
models.json`'s `surfaces.openhands` client list (that check stays; this render
gives the *shape itself* a source, where before only its `max_input_tokens`/
`max_output_tokens` fields were kept in sync from the catalog).

Field mapping back, per tier:

| Registry path | `tier-profiles.json` path | Notes |
|---|---|---|
| `<gateway>-<route id>` (computed) | `tiers[].id` | `<gateway>` is `omniroute` or `litellm`. The one direct-profile tier is the exception — see below. |
| `routes.<id>.surfaces.<gw>.openhands_profile.model`? (litellm only) / **derived** `"openai/<route id>"` (omniroute) | `tiers[].model` | The registry carries `model` explicitly on every `litellm` `openhands_profile` (redundant with the route id, kept anyway — §6's own mapping never dropped it going *forward*, only documented it as *redundant*) but never on an `omniroute` one; `render_openhands()` reads `.get("model", "openai/%s" % route_id)`, reproducing today's file exactly either way (verified against all 21 tiers, 2026-09-26: zero disagreements). |
| `routes.<id>.surfaces.<gw>.openhands_profile.max_input_tokens` | `tiers[].max_input_tokens` | Unchanged. Kept in sync with `catalog/ide-models.json`/`render_ide()` by `tools/sync-ide-models.py`'s pre-existing `rewrite_tier_profiles()` — unaffected by this task. |
| `routes.<id>.surfaces.<gw>.openhands_profile.max_output_tokens` | `tiers[].max_output_tokens` | Same as above. |
| `routes.<id>.surfaces.<gw>.openhands_profile.reasoning` | `tiers[].reasoning` | Unchanged. |
| `routes.<id>.surfaces.litellm.openhands_profile.gateway` (always `"litellm"` when present) | `tiers[].gateway`? | Present only on a `litellm` tier — an `omniroute` tier carries no `gateway` key at all, matching today's file. |
| `routes.spark-1.3-contributor.surfaces.openhands.direct_profile.{gateway,model,base_url,max_input_tokens,max_output_tokens,reasoning}` | `tiers[].{gateway,model,base_url,max_input_tokens,max_output_tokens,reasoning}` (the one `openrouter-muse-spark-1.3-contributor` tier) | §6's own mapping, read back field for field; this tier's `id` cannot be computed as `<gateway>-<route id>` (it names the OpenRouter leg's own bare model spelling, `muse-spark-1.3-contributor`, not the route id `spark-1.3-contributor`) — `OPENHANDS_DIRECT_PROFILE_IDS` names the one exception as a literal `{tier id: route id}` table, the same convention as the order/retired-ids constants below. |
| — | `tiers[]` order | **derived**: `OPENHANDS_TIER_ORDER`, a literal constant — see below. |
| — | `retired_ids` | **derived**: `OPENHANDS_RETIRED_IDS`, a literal constant — see below. |
| — | `gateway_base_url` / `litellm_base_url` | **derived**: `OPENHANDS_GATEWAY_BASE_URL` / `OPENHANDS_LITELLM_BASE_URL`, literal constants — §6's own mapping already dropped these as "container-side constant[s], not a per-model fact… out of scope for models/routes/providers". |

Three intentional, documented "keep today's value via the render" treatments —
never a silently dropped field, all covered by `tests/test_registry_render.py`
and by `openhands_diff()`/`_canonical_openhands()`:

1. **`$comment` is not reproduced**, exactly the same treatment §10/§12 give
   `combos.json`/`ide-models.json`'s own top-level comments — its substance
   (rename history, the `openai/`-prefix measurement, retired-ids provenance)
   already landed in §6 and in `tools/registry-convert.py`'s docstring during
   the A1–A3 migration. The render emits only the spec-3.2 marker line;
   `openhands_diff()` ignores the whole `$comment` key.
2. **`gateway_base_url`, `litellm_base_url` and `retired_ids` are literal
   constants, not registry fields** — §6's mapping already established this
   (container-side Docker convention; a prune list with "nothing to migrate",
   the same category as `combos.json`'s own `retired` array, §4). `retired_ids`'
   own order carries no semantics either — `tools/sync-openhands-profiles.py`'s
   `push_profiles()` immediately does `frozenset(legacy_owned)` — so
   `openhands_diff()` compares it as a set, the same treatment `omniroute_diff()`
   gives `combos.json`'s `retired` (§10, exception 2).
3. **`tiers[]` order is reproduced exactly, as a literal constant
   (`OPENHANDS_TIER_ORDER`), not treated as an exception** — unlike (2),
   because tier-profiles.json's own `$comment` states outright that order is
   semantic ("ORDER IS THE PUSH PRIORITY … a human decision") and
   `tools/sync-openhands-profiles.py`'s `push_profiles()` reads `spec_order=
   [t["id"] for t in tiers]` to rank which profiles survive the app's 10-profile
   cap — the same "order is real data, reproduce it via a hand-copied
   constant" choice `render_ide()` makes for `IDE_MODEL_ORDER` (§12) rather
   than `render_omniroute()`'s "array order is insignificant" choice for
   `combos`/`retired` (§10). `render_openhands()` raises, naming every id at
   once, if a future openhands profile is added or removed without updating
   this list — never a silent reorder or drop
   (`tests/test_registry_render.py::MissingTierFailsOpenhandsRenderTests`).
   **Follow-up**: same as `IDE_MODEL_ORDER`'s own follow-up (§12) — a future
   registry field for push rank would let this render drop the constant; not
   added here since spec 3.1 lists no such field and this is exactly the
   "keep today's value" gap task A4d's brief calls out by name.

**`catalog/ide-models.json`'s own `max_input_tokens`/`max_output_tokens` sync
into this file is out of scope for this render, and untouched by it.**
`tools/sync-ide-models.py`'s pre-existing `rewrite_tier_profiles()` (task
predates A4d; §12 does not mention it because it only rewrites two fields,
never the file's shape) already keeps those two fields converged from
`catalog/ide-models.json`/`render_ide()`'s own token windows on every
`tools/sync-ide-models.py` run, membership-checked both ways against the
catalog's `openhands` surface — this task's render only gives the *rest* of
the file's shape (`id`, `model`, `gateway`, `reasoning`, order, the base URLs,
`retired_ids`) a source in the registry too; the two overlapping fields
(`max_input_tokens`/`max_output_tokens`) are produced identically by both
paths today (verified: `render_openhands()`'s figures equal `render_ide()`'s
`context`/`output` for every shared route, 2026-09-26), so nothing regresses.

**`configuration/openhands/config.toml`'s generated part needs no new render —
it is already sourced from the registry, transitively, since task A4c.**
`tools/sync-ide-models.py`'s pre-existing `rewrite_openhands_toml()` rewrites
only the `max_input_tokens`/`max_output_tokens` lines of every `[llm]`/`[llm.*]`
table whose `model` names a catalog id, from the same `models` list every other
target in that tool now gets from `load_from_registry()` (task A4c) — i.e.
`render_ide()`'s output, not `tier-profiles.json`. `config.toml`'s token windows
and `tier-profiles.json`'s token windows are therefore two independent
consumers of the *same* `render_ide()` numbers (confirmed identical today,
above), never of each other — task A4d's own brief text ("and whatever part of
`configuration/openhands/config.toml` is generated today") is satisfied by this
fact, not by a new code path: `python3 tools/sync-ide-models.py --check` is
still the gate for `config.toml`'s generated lines, unchanged by this task.

### Retargeting `tools/sync-openhands-profiles.py`

`tools/sync-openhands-profiles.py`'s default, unflagged run now sources its
`spec` dict from a new `load_spec(spec_path, registry_path)` helper, which
calls `tools/registry.py`'s `render_openhands()` on `catalog/ai-registry.json`
(default path) instead of reading `configuration/openhands/tier-profiles.json`
directly — the exact retarget task A4d's brief calls for, mirroring task A4c's
own `--catalog` treatment for `tools/sync-ide-models.py`. Because `render
openhands --check` proves `render_openhands(catalog/ai-registry.json)` is
semantically identical to `configuration/openhands/tier-profiles.json` today,
this changes nothing about what gets written to any profile file or pushed to
a running app on an unmodified checkout — every existing regression case in
`tests/run-tests.sh` that exercises this tool without `--spec` (`"tier profiles
come from the spec, installer and tool agree"`, and every `"svc: profile
sync/push …"` case) passes unchanged, proving it; a matching in-process check
(`SyncOpenhandsProfilesSourcesFromRegistryTests`) was added to
`tests/test_registry_render.py` since no dedicated `test_sync_openhands_
profiles.py` file exists (grepped, per task A4d's own brief) — the coverage for
this tool lives entirely in `tests/run-tests.sh`/`.ps1`.

`--spec PATH` stays as an explicit, lower-level override that reads a
tier-profiles.json-shaped file directly via `json.loads` (the pre-A4d code
path, now factored into `load_spec()`'s `spec_path is not None` branch) —
every test that ever passed `--spec` explicitly would be unaffected either
way, though grepping `tests/` found none that do (the tool's own default path
is what every existing case exercises). A new `--registry PATH` flag (default
`catalog/ai-registry.json`) names the render source when `--spec` is not
given, ignored otherwise — the same pairing `tools/sync-ide-models.py`'s
`--registry`/`--catalog` flags give that tool.

## 14. Task A4e — rendering docs/models.md's "Tier mapping" table back from the registry

Spec 3.2's own row for this file — "model tables in `docs/models.md` |
generated section between markers; prose stays hand-written" — had never
been acted on: before this task, docs/models.md carried no AUTOOS-MANAGED
markers at all, and its "Tier mapping" table (the file's own heading) was
100% hand-written prose that had already drifted from the registry (its
Chain column still implied every `cerebras`/`opencode-zen deepseek-v4.1-flash`
leg was live, when `routes.<id>.unavailable_legs` already recorded several of
them as down). `tools/registry.py render models-doc` reads `catalog/
ai-registry.json` and produces that table via the pure function
`render_models_doc(registry) -> str`, and this task defines the marker pair
itself — `<!-- AUTOOS-MANAGED-START models-doc -->` / `<!-- AUTOOS-MANAGED-
END models-doc -->` — as docs/models.md's own Markdown-comment convention,
mirroring the `#` (`tools/sync-router-tiers.py`) and `//`
(`tools/sync-ide-models.py`) forms the other generated files already use.

**A rejected first attempt at this task exists in this repository's history
(commit `6a61052`, never merged onto `l1/routing`).** It defined the same
marker pair and wrapped the *existing* hand-written table unchanged, but its
`render_models_doc(registry)` never read `registry` at all — the table's text
lived as a literal Python tuple constant
(`MODELS_DOC_TIER_TABLE_LINES`) and `--check` therefore always passed,
including against a registry with a leg newly marked unavailable. That is a
second, silently-stale home for the same facts the registry already owns —
exactly the anti-pattern spec 3.2 exists to remove — not a render, and this
task's own brief named it as the thing not to repeat. The mapping below is
what this task builds instead: every cell of every row read from a registry
field at render time, with no table text held as a constant anywhere in
`tools/registry.py`.

One table row per `registry["routes"]` entry (all 21 today — unlike `render
omniroute`'s legs-non-empty filter or `render ide`'s "every route with a
clients-carrying surface" rule, this render has no membership filter at all:
every route gets a row, including the dynamic `auto`/`auto/cheap`/`auto/smart`
routes and the LiteLLM-only `*-paid` routes that carry `legs: []` and were
never in the old hand-written table), sorted by id. Unlike `render_ide()`'s
`IDE_MODEL_ORDER` or `render_openhands()`'s `OPENHANDS_TIER_ORDER`, no
hand-copied order constant is needed: this table is new with this task, so
there is no "today's hand-curated order" to preserve, and nothing in this
repository claims the doc's row order carries meaning the way `catalog/
ide-models.json`'s picker order or `tier-profiles.json`'s push-priority order
do (sections 12/13 above).

Field mapping, per column:

| Column | Registry path | Notes |
|---|---|---|
| Route | `routes.<id>.id` | Unchanged; also the dict key. |
| Class | `routes.<id>.class` | Unchanged (`free`/`cheap`/`mid`/`frontier`, spec 3.1). |
| Context | `routes.<id>.surfaces.omniroute.context_declared`, else `routes.<id>.surfaces.litellm.context_declared` | The route's own declared context promise (mapping doc section 4/10: combos.json's coarser picker/compaction display convention, e.g. `"1M"`/`"128k"`/`"200k"`, distinct from a model's real window) — `_route_context_promise()`'s first branch, `MODELS_DOC_CONTEXT_SURFACES = ("omniroute", "litellm")` naming the priority order explicitly (the same convention `IDE_GATEWAYS`/`OPENHANDS_GATEWAYS` use), verified 2026-09-26: every route that lists both surfaces carries the same declared string on both, so the pick is never actually exercised as a tie-break by real data (the same "never actually exercised" note render_ide()'s own gateway pick carries, section 12). |
| Context (fallback 1) | `routes.<id>.surfaces.omniroute.context` / `.litellm.context` | Used, comma-grouped, when the surface has no `context_declared` at all — the 7 routes with no combos.json/hand-curated counterpart (`auto`, `auto/cheap`, `auto/smart`, `t1-orchestrator-paid`, `t2-worker-paid`, `t3-driver-paid`, `t4-rag`) never had one to declare. |
| Context (fallback 2) | the route's first leg's `models.<id>.context_advertised`, else `.context_usable.tokens` | Used only when no omniroute/litellm surface on the route carries either field — not exercised by any route in today's registry (every route has at least a numeric `context` on one surface), covered instead by a synthetic-registry test (`tests/test_registry_render.py::ModelsDocCellsComeFromTheRegistryTests::test_context_column_falls_back_to_a_legs_model_when_no_surface_carries_one`) that strips both fields from a real route's surfaces to force the branch. Suffixed `(leg model)`/`(leg model, usable)` so a reader can tell the figure describes a leg's model, not the route's own declared surface. |
| Legs | `routes.<id>.legs`, in order | Unchanged, **in order** — the same "a `strategy: priority` fallback chain is real semantic data" reasoning `render_omniroute()`'s own `legs` → `combos[].models` mapping gives it (section 10). `(none)` for the 7 routes named above, whose `legs` is genuinely `[]`. |
| Legs, per-leg unavailability | `routes.<id>.unavailable_legs[leg].available` / `providers.<id>.available` | A leg is struck through and suffixed `(unavailable)` when either flag is `false` — spec 3.1's two places an operator can flag a dead leg (the same two `render_omniroute()`'s own docstring names), checked by `_leg_is_unavailable()`. Today the two flags never disagree (every `providers.openrouter.available: false` leg is also individually flagged in its own route's `unavailable_legs`), so the provider-level check is a defensive second path, not one exercised by real data yet — covered by a synthetic-registry test that clears a route's `unavailable_legs` and relies on the provider flag alone (`test_leg_whose_provider_is_globally_unavailable_is_marked`). |

The `$comment`-equivalent notice (`MODELS_DOC_GENERATED_NOTICE`) is a fixed
string, not a registry field — the same "process metadata, not table data"
treatment every other render's own generated-marker line gets (section 10/12/
13's own exception 1) — and explains how to update the committed file
(there is no `--write`; see below), since this render, unlike the other four,
has no third-party sync tool that ever calls it programmatically today.

**No `--write` flag.** Every other `render <target>` command (`omniroute`/
`litellm`/`ide`/`openhands`) only ever offers `--out PATH`/`--check`, never a
flag that rewrites the real committed file in place — phase 1 (spec 3.2)
proves equality, it does not switch a consumer over. `render models-doc`
follows the same convention for consistency, even though (unlike the other
four) nothing else in this repository writes docs/models.md today, so there
is no consumer to "switch over" in a later phase either. Updating the
committed block after a registry change is therefore a documented manual
step (this section's own opening paragraph and the CLI's own `--help`/
docstring text): run `render models-doc --check` to see it fail, run `render
models-doc` (no flags) and replace the text between the two markers in
docs/models.md with its output, then re-run `--check` to confirm.

**Dated/probe-verified prose the old Chain column carried, but no registry
field owns, moved to a new "Notes per route" list** immediately after the
`<!-- AUTOOS-MANAGED-END models-doc -->` marker (outside the generated
block, so it is never compared by `--check` and never overwritten by a
future render) — ack times, probe-falsified spellings, and operator policy
lines, one bullet per route that carried one, deduplicated (a fact repeated
across several old rows, such as "OpenRouter credits are exhausted", is
never restated here at all: the generated table's own `(unavailable)`
markers already carry it, and the registry's own `unavailable_legs.
$comment` values already carry *why* — a third home for the same fact would
recreate exactly the problem this task exists to close). Design commentary
that added no fact beyond what the generated table already shows verbatim
(`t2-worker-clean`'s old "No groq/cerebras/sambanova free legs", `t2-worker-
free-only`'s old "Same heads as t2-worker minus every paid leg") was dropped
rather than moved — the Legs column already lists exactly which legs a route
does and does not carry. Two claims were corrected against the current
registry rather than carried forward unchanged: the `gemini-3.8-flash` note's
"`antigravity/gemini-3.7-flash-medium` leg in `t2-worker`" (today's registry
has `-high` in `t2-worker` and `-medium` in `t2-worker-free-only` — the note
now attributes each variant to its real route), and the `deepseek-v4.1-flash`
note (its own old "cheapest-first" framing no longer describes a working
chain — both of its remaining legs are individually flagged unavailable in
today's registry, openrouter for exhausted credits and opencode-zen for a
failed tool-calling probe, so the note now says so explicitly instead of
implying a live cheapest-first path).

**Follow-up, same shape as `render_ide()`'s `IDE_MODEL_ORDER` and
`render_openhands()`'s `OPENHANDS_TIER_ORDER`/`OPENHANDS_RETIRED_IDS`
follow-ups (sections 12/13):** the "Notes per route" list itself is still
hand-maintained prose, not derived — a future `routes.<id>.$comment` (already
a schema-legal field on every section, per `COMMENT_KEYS`) that named which of
its lines belong in this doc's per-route notes would let a later render grow
past the four-column table above; not attempted here, since spec 3.1 lists no
such field and task A4e's own brief is "keep the *dated* prose that has no
registry field" for exactly this kind of gap, the same choice A4b/A4c/A4d made
for the litellm `rpm` lines, `catalog/ide-models.json`'s menu order and
`tier-profiles.json`'s push-priority order respectively.
