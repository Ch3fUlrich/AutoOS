# API keys — how to get each one

The router (`configuration/litellm/config.yaml`) reads all keys from
`configuration/litellm/.env`. Copy `.env.example` there, fill in what you
have — chains degrade gracefully, every key you add is one more free quota
before paid fallback. Order below = most free value first.

| Env var | Where | Cost | Notes |
|---|---|---|---|
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) — sign up, create key (`gsk_…`) | Free, no card. ~30 RPM / ~1k req/day | Fastest free tier; different infra, rarely fails together with others |
| `CEREBRAS_API_KEY` | [inference.cerebras.ai](https://inference.cerebras.ai) — sign up, API keys section | Free, no card. 1M tokens/day | Lineup rotates; `gpt-oss-120b` is the stable anchor |
| `GEMINI_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) — Google account, "Create API key" | Free, no card. Flash models only (Pro left free tier Apr 2026), dynamic limits | ⚠️ Free-tier prompts may be used to improve Google models — no private code on tier2/3 free |
| `MISTRAL_API_KEY` | [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys) — sign up, create key | Free mode, rate-limited, no card | `-latest` aliases are stable; prefer them over dated IDs |
| `META_API_KEY` | [dev.meta.ai](https://dev.meta.ai) (Meta Model API) — generate key, base `https://api.meta.ai/v1` | $20 free starter credits, then pay-as-you-go | Your own credits for tier1; contributor tier (`-contributor` model IDs) trades training-data use for $0.10/$0.20 pricing |
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) — sign up, create key | Top-up; free `:free` variants metered separately | Cheapest paid spark route (`meta/muse-spark-1.3-contributor`); good last-resort pool |
| `DEEPSEEK_API_KEY` | [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys) — sign up, top up | Cheap paid direct | Backstop for tier3-paid |
| `OPENCODE_ZEN_API_KEY` | [opencode.ai/auth](https://opencode.ai/auth) — sign in, billing, copy key | $20 prepaid, at-cost tokens + 4.4% + $0.30 card fee; auto-reload $20 under $5 (changeable) | Promo free models included; ⚠️ several permit training on prompts during free period — keep private code on paid routes |
| `LITELLM_MASTER_KEY` | You invent it | Free | Random local password protecting your proxy; also the key your apps send |

## Rules

- Keys live **only** in `configuration/litellm/.env` (git-ignored) and app
  environments — never in tracked files, chat logs, or screenshots. Tracked
  templates carry `REPLACE_WITH_…` placeholders.
- Free tiers that train on prompts (Gemini free, contributor tiers, Zen promo
  models): fine for open-source work like this repo, never for private code.
  Paid routes (Meta direct non-contributor, Zen paid, DeepSeek direct) follow
  zero-retention terms — check each provider's current policy.
- Rotate a leaked key at the provider console immediately; the proxy needs no
  config change, just the new value in `.env` and a restart.
