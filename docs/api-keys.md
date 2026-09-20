# API keys — how to get each one

Primary path is **OmniRoute**: install it, open the dashboard, register keys
there. The LiteLLM `.env` (second table) is only needed for the manual
fallback router. In both cases: keys live in git-ignored files and app
environments — never in tracked files, chat logs, or screenshots.

## 1. OmniRoute (primary)

```bash
npm install -g omniroute
omniroute            # dashboard at http://localhost:20128
```

1. Dashboard → **api-manager → Create API Key** (name it e.g. `autoos`).
   Client key shape: `sk-…`. Put it in env `AUTOOS_OMNIROUTE_KEY`.
2. Dashboard → **Providers → + Add Provider** for each key below. Prefer
   free/recurring first; the `auto*` combos and your priority combos do the
   rest. Quota reality per provider is visible at `/dashboard/free-tiers`.
3. Zero-config start works with no keys at all (`auto` answers via keyless
   OpenCode Free) — then add keys to widen the free pool.

OmniRoute numbers below come from its pool-deduped catalog (re-audited
2026-09-02/03): ~1.62B documented free tokens/mo steady, +first-month signup
credits. Counts move both ways as providers change tiers — the dashboard is
the current truth, not this page.

## 2. Provider keys (register in OmniRoute, or in LiteLLM `.env`)

Ordered by free value. "Training" = free tier may train on prompts: fine for
this public repo, never for private code.

| Key | Where | Free terms (Sep 2026) | Training? |
|---|---|---|---|
| Mistral | [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys) | **Biggest documented pool: ~1B/mo per org**, 2 RPM, rate-limited free mode, no card | Check terms |
| Gemini (AI Studio) | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | Flash-family pooled, uncapped figure, dynamic limits; Pro left free tier Apr 2026; 2.0 Flash dead Jun 2026 | **Yes** |
| Groq | [console.groq.com/keys](https://console.groq.com/keys) | Per-model 200K tokens/day caps (~30M pool); llama-3.3-70b left free tier Aug 2026 — use GPT-OSS/Qwen/Llama current IDs | Check terms |
| Z.AI / GLM | [z.ai](https://z.ai) | GLM-4-Flash/4.5/4.7 **permanently free**, uncapped + 20M signup bonus | Check terms |
| Kilo gateway | Kilo Code app | Rotating "Auto Free" set (Nemotron 3, StepFun…), uncapped | Check terms |
| Nara | router.bynara.id | ~210M/mo bucket | Check terms |
| llm7 | token.llm7.io | 150M/mo, free token required | Check terms |
| xKiro | xKiro | 150M/mo | Check terms |
| OpenRouter | [openrouter.ai/keys](https://openrouter.ai/keys) | Free `:free` pool metered per request; **$10 top-up → 1000 req/day** (+24M/mo) | Per model |
| OpenCode Zen | [opencode.ai/auth](https://opencode.ai/auth) | 6 rotating free coding models, uncapped; paid: $20 prepaid, at-cost + 4.4% + $0.30 | **Promo models yes** |
| Meta Model API | [dev.meta.ai](https://dev.meta.ai) | $20 starter credits; contributor IDs (`-contributor`) = $0.10/$0.20 per 1M | **Contributor yes** |
| DeepSeek | [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys) | 5M one-time, **expires after 30 days** | Check terms |
| Cerebras | [inference.cerebras.ai](https://inference.cerebras.ai) | ⚠️ No-card 1M/day trial is **gone** — one-time $5 credit, card required | Check terms |
| Cloudflare AI | Cloudflare dashboard | ~30M/mo (10k Neurons/day) | Check terms |
| Vertex AI | Google Cloud | $300 signup credit (~300M tokens), billing account needed | Per terms |

## 3. More free legs worth registering (thin quotas, real use)

These don't carry agent loops alone — with weak autonomy, thin-but-reliable
beats strong-but-flaky. All pool through OmniRoute's `auto*` combos.

| Key | Free terms | Role it earns |
|---|---|---|
| SambaNova (account, no card) | 20 RPM but only 20 RPD / 200K TPD | Last-resort overflow only — 20 req/day caps it at commit-message / review-summary duty |
| Cloudflare Workers AI (free Workers plan) | 10k Neurons/day (~$0.11 compute), all models share it, resets 00:00 UTC | GPT-OSS-120B / Qwen coders for short prompts; heavy prompts drain it fast |
| Cohere (dashboard trial key, no card) | 1,000 calls/mo, 20 RPM chat, eval-only terms | RAG/rerank specialist, not a driver |
| GitHub Models (GitHub account + marketplace opt-in) | Free daily quotas, frontier models tens of RPD | Demos and spot-checks; limits forbid loops |
| Hugging Face (account) | $0.10/mo — demo tier, skip for agentic work | Catalog breadth only |
| xAI / Anthropic / OpenAI | ~$5 signup credits, usually card-gated, expiring | One-time boost, not a strategy |

Conflicts resolved: Llama 3.3 70B is listed free in several guides but left
Groq free 2026-08-16 — treat it as degraded, prefer GPT-OSS-120B / Qwen3.8
where both are offered. Gemini 2.5 Flash free is ~10 RPM / 250 RPD per model
(concrete beats the old "1500/day" figure still floating around).

ToS caution (single-user personal proxy is generally tolerated; resale is
not): `opencode` ToS restricts Zen to internal use (flagged avoid for
proxying), `muse-spark-web`/scraped-web routes flagged avoid, Groq/Mistral/
Cerebras prohibit reselling keys. When in doubt, paid legs only.

## 3. LiteLLM fallback `.env` (only if you use it)

Copy `configuration/litellm/.env.example` → `.env`. Same keys as above
(`GROQ/CEREBRAS/GEMINI/MISTRAL/OPENROUTER/META/DEEPSEEK/OPENCODE_ZEN_API_KEY`)
plus `LITELLM_MASTER_KEY` (a random local password you invent). Missing keys
are fine — chains skip what they cannot authenticate.

## Rules

- Rotate a leaked key at the provider console immediately; proxy needs no
  config change, just the new value + restart.
- Free tiers churn monthly (this page already needed corrections 3 weeks
  after writing). Re-check `/dashboard/free-tiers` before trusting a number.
