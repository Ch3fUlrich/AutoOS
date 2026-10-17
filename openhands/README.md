# Vendored OpenHands profiles

Structural LLM + agent profiles AutoOS installs into `~/.openhands/`
during `setup_openhands_config` / `Set-AutoOSOpenHandsConfig`.

- `profiles/*.json` — **complete profiles minus `api_key`.**
  `model`, `base_url`, `auth_type`, the reasoning/thinking flags, plus a
  committed snapshot of the catalog numbers (context windows, prices).
  `tests/run-tests.sh "vendored openhands profiles match the catalog
  snapshot"` fails on any drift, and the installers overlay fresh catalog
  numbers at setup anyway (catalog wins). `api_key` is injected at
  install time from env/secrets and never vendored.
- `agent-profiles/*.json` — agent profiles referencing LLM profiles by
  `llm_profile_ref`. Copied verbatim.
- File name (minus `.json`) is the profile id. Direct-provider profiles
  (DeepSeek flash, OpenRouter free pool, local Ollama) are the keyless and
  paid-direct legs; gateway-routed `omniroute-tier*` / `litellm-tier*`
  profiles are generated at install/start time from
  `configuration/openhands/tier-profiles.json` and published into
  `settings.json` `llm_profiles` alongside these.

## Why the explicit thinking opt-outs exist

OpenHands SDK `LLM` pydantic defaults are `reasoning_effort="high"`,
`enable_encrypted_reasoning=true`, `extended_thinking_budget=200000`.
Any sparse profile is materialized through those defaults, which makes
Ollama hard-fail non-thinking models with
`"qwen2.5-coder:7b" does not support thinking`.
Every template whose catalog entry has no `reasoning: true` therefore
pins `reasoning_effort: "none"`, `enable_encrypted_reasoning: false`,
and `extended_thinking_budget: null`.
