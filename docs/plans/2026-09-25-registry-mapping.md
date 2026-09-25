# Registry field mapping — one model registry (tasks A1, A2)

Field-level mapping for [2026-09-25-routing-v2-spec.md](2026-09-25-routing-v2-spec.md) section 3
(the registry) and section 3.2 (migration, phase 1). Every field of every entry type in the six old
files is listed below with where it lands: a `catalog/ai-registry.json` path, `dropped: <why>`, or
`derived: <how>`. The converter that implements this mapping is `tools/registry-convert.py`; its own
tables (`PROVIDER_EXTRA`, `MODEL_EXTRA`, `EXTRA_MODELS`, `EXTRA_PROVIDERS`, `PROVIDER_WINDOWS`,
`CLIENT_EXTRA`, `COMBO_CLASS`, `ROUTE_COMMENT`) are the executable form of the "small explicit table"
parts of this mapping — read them alongside this document, not instead of it.

Phase 1 only (spec 3.2): this is the "add the registry, prove the mapping" step. Nothing here changes
what `apply.sh`/`apply.ps1`/`sync-*.py` read yet — that is task A4/A5.

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
    format beyond "registry content date", so this converter always writes today's date on a run.

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
`$comment` citing the decision (`tools/registry-convert.py`'s `mark_openrouter_unavailable()`).

## 9. Where every `$comment` prose piece landed

| Old `$comment` (file) | Landed in |
|---|---|
| `catalog/providers.json` (field-meaning glossary, ordering-matters note, `SambaNova` casing) | This document §2; per-provider notes stay as data comments only where they explain *that provider's* value (e.g. `omniroute`'s "not a model provider" comment). |
| `catalog/ide-models.json` (surfaces/gateway glossary, 1M-tier operator ruling, opencode `variants` warning) | This document §3, §6.4/§7.5 of the mapping table above; the `variants` warning is process guidance for a future opencode renderer (task A4/A5), not a registry fact. |
| `configuration/omniroute/combos.json` (role labels, free-first/fast-skip mechanism, per-family chain descriptions, 2026-09-21 privacy downgrade, effort-clamp warning, retry/cooldown settings, verification dates) | Split by fact: the privacy downgrade → `providers.openrouter.$comment` and `providers.zen.$comment` (Open choice §7.4); per-model chain/training facts → the relevant `models.<id>.$comment`; role labels/mechanism/retry settings → this document (no single registry field owns "how the gateway retries", since spec 3.1 has no such field — out of scope for the model registry itself). |
| `configuration/openhands/tier-profiles.json` (rename history, `openai/`-prefix measurement, push-order rationale, `retired_ids` provenance) | This document §6; the `openai/`-prefix measurement is a code comment in `tools/registry-convert.py` next to `_strip_openhands_profile()`. |
| `.agents/skills/unattended-orchestration/provider-windows.json` (re-verify-before-relying note) | This document §5 / §7.6 (process guidance for a future `registry.py recalibrate`/`probe`). |
