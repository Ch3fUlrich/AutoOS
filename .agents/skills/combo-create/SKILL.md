---
name: combo-create
description: Create an OmniRoute combo (tier route) always done correctly - contexts, trial-free-credits-paid order with paid last and deepseek last, t1 >=600k 1M band, t2/t3 128k clamp, openrouter :free-only, contract gate fail-closed. Use whenever adding, reordering, or auditing a route in catalog/ai-registry.json, configuration/omniroute/combos.json, or any rendered surface.
---

# Combo Create (tier order done correctly)

This is the one home for the combo-create rule. Code, tests, and apply scripts
reference it; they do not restate it.

## Rule (TORDER 2026-10-01, D-TORDER-1b)

- **t1 band = >=600000, renders 1M.** `t1-orchestrator`, `t1-orchestrator-free-only`,
  `t1-orchestrator-paid`, `t1-orchestrator-clean`: every leg window
  `>= 600000` (explicit constant `T1_MIN_WINDOW = 600000` in
  `tools/combo-contract.py`; rendered result is still 1M). Current 1M legs still
  render 1M, so only the gate constant changed. t1-orchestrator band:
  `gemini/gemini-3.8-flash` (free head), `free_ai/google/gemini-3.8-flash`
  (second free 1M), `vertex/gemini-3.8-flash` (credit),
  `meta_api/muse-spark-1.3-contributor` (paid),
  `deepseek/deepseek-flash` (paid LAST). Free-only twin: two distinct FREE 1M
  providers (gemini + free_ai). Verify each leg in live `/v1/models` before
  adding; if impossible without inventing, keep what exists and report
  D-TORDER-2 with measured windows.
- **t2/t3 order = trial -> free -> credits -> paid, DEEPSEEK LAST, 128k clamp.**
  Free band head `gemini/gemini-3.8-flash`, then groq/huggingface/openrouter/
  free_ai legs; `scaleway/*` and `antigravity/*` kept but NEVER heads.
  Credits after all free: `ovhcloud/*` x3 + `vertex/gemini-3.8-flash` (NEW to
  t2/t3 full). Paid: `meta_api` then `deepseek` LAST. No free leg after a paid
  leg. `-free-only` twins take NO credit/paid leg. `t2/t3` render `128k`
  (lowest implementer window, do NOT raise); sub-1M legs allowed.
- **gemini context/output.** `models.gemini-3.8-flash`: `context_advertised`
  1048576, `output_max` 65536 (live), `context_usable.tokens` 1048576
  (source `default`, operator update 4). Twin
  `models.google/gemini-3.8-flash`: 1048576 / 65536 / usable 524288 (50%).
  `routes.gemini-3.8-flash` legs `[gemini, vertex, openrouter (gated/removed
  per OR rule), deepinfra (gated)]`, `surfaces.omniroute.context` 1048576,
  label `1M`. `providers.google_ai_studio.available` true with backoff note:
  "gemini retained; usage governed by the repeated-429 backoff policy
  (3x429/120 s -> 300 s cooldown per leg; 30 min park)" (gateway 08:07Z ACTIVE).
- **openrouter NO credits -> :free only.** Every servable openrouter leg MUST
  end `:free`. Every declared paid `openrouter/*` leg that survives MUST be
  gated in `routes.<id>.unavailable_legs`. `providers.openrouter.available`
  stays `True` (gating paid models, not provider, so `:free` still routes).
  `t1-orchestrator-clean` keeps declared+gated paid leg so it stays `omitted`,
  not legless. Never set provider available:false (drops `:free` too).
- **New credit singles (class credit).** `ovh-qwen3.8-27b`, `ovh-gpt-oss-120b`,
  `ovh-qwen3-coder-30b`, `vertex-gemini-3.8-flash`: single leg, strategy
  priority, `surfaces.omniroute` (clients `[opencode, zed]`, context/declared,
  display_name, output; no openhands_profile per groq/hf precedent).

## Gate (fail-closed)

`python tools/combo-contract.py` asserts for every combo: (a) client limit ==
registry context == combos context (ladder floors); (b) trial->free->credits->
paid, paid last, no free after paid, deepseek last, openrouter servable :free
+ declared-paid gated; (c) every leg resolves (live existence warn-only:
apply skips unknown with warning, exit 0, so gate must not fail on dead
huggingface/antigravity or apply -DryRun breaks); (d) t1 1M + >=600k,
t2/t3 128k.

Wired fail-closed (non-zero exit, also under -DryRun): `configuration/
omniroute/apply.ps1` + `apply.sh` (before creating anything), CI
(`.github/workflows/ci.yml` lint job), harnesses (`tests/run-tests.ps1`
registry drift case, `tests/linux/17-ai-routing.sh`), pytest
(`tests/test_registry.py` ComboContractTests via subprocess).

## Workflow

1. Edit `catalog/ai-registry.json` routes/models/providers per Rule.
2. `python tools/registry.py render omniroute --check` (must exit 0; if new
   route, add to `tools/registry.py` `IDE_MODEL_ORDER` + litellm managed block
   in `configuration/litellm/config.yaml`, then re-render).
3. Regenerate `combos`/`omitted`/`retired` keeping hand `$comment` + TORDER entry.
4. `render ide --out`, `sync-ide-models.py`, `render litellm --out` (or
   `sync-router-tiers.py`), `render models-doc` paste, then every `--check`
   + `sync-ide-models.py --check` exit 0. Prove opencode gemini
   `limit.context=1048576, output=65536`.
5. `python tools/combo-contract.py` exit 0 (per-combo PASS).
6. `python tools/registry.py check` + `validate`, `audit-router.py --offline`,
   `pytest`, `apply.ps1 -DryRun` exit 0, shellcheck/PSScriptAnalyzer clean.
