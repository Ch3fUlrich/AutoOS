# Model routing — one LiteLLM proxy for every agent

Every agent in this repo (OpenCode, OpenHands, Claude Code Desktop, ad-hoc
scripts) talks to the **same local LiteLLM proxy**, which routes across three
free-first tiers with paid fallbacks. One `.env` holds all keys, one
`config.yaml` holds all routing, per-model pricing is fixed in one place so
cost displays are correct everywhere.

## The tiers

| Tier | Role | Free (in order) | Paid escalation (in order) |
|---|---|---|---|
| `tier1` | Orchestrator: long-horizon, tool-calling, high context. Replaces/augments the Claude Code Desktop + Fable 5.1 setup for unattended runs | Cerebras `gpt-oss-120b` (1M tok/day) → Groq `gpt-oss-120b` → Gemini Flash (AI Studio) | Zen `claude-opus-5` → Zen `kimi-k2.7-code` → Zen `claude-fable-5-1` (flagship, last resort) |
| `tier2` | Smart second level: Muse Spark 1.3 family, xhigh effort | Zen `muse-spark-1.3-contributor-free` | Zen `muse-spark-1.3` |
| `tier3` | Daily driver: codegen, research, review, lint | Groq `llama-3.3-70b-versatile` → Mistral `devstral-small` → Mistral `small` | Zen `deepseek-v4.1-flash` → DeepSeek direct → Zen `claude-sonnet-5` |

Free deployments carry `rpm` guards matching their free quotas, the router
uses least-used-first, a 429ing deployment cools down for 2 minutes, and each
tier falls through to its `-paid` group only after retries. `drop_params` is
on so one caller works against heterogeneous providers.

**Caveats you must know:**
- `contributor-free` (tier2 free) is discounted **in exchange for training-data
  use**. Never route private code there — that is what `tier2-paid` is for.
- Zen documents Claude/Spark/Fable under `/responses` or `/messages`, while
  this config speaks OpenAI-compatible `/chat/completions`. DeepSeek/Kimi/GLM
  entries are on the documented path and safe; **run `litellm --test` after
  first start** and if a Claude/Spark/Fable entry errors on path, repoint it
  (Anthropic-direct key, or Meta Model API for Spark) — the chain degrades
  gracefully meanwhile.
- `xhigh` is a reasoning-*effort* tier, not a separate model ID: the alias
  resolves to the 1.3 family, effort is the caller's control (Muse Code "Max").

## Run it

```bash
pip install 'litellm[proxy]'
cp infra/litellm/.env.example infra/litellm/.env   # fill in keys, never commit
litellm --config infra/litellm/config.yaml --port 4000
litellm --config infra/litellm/config.yaml --test  # verify every route once
```

Free keys, no card: Groq, Cerebras, Mistral, Google AI Studio. Paid: Zen
($20 prepaid, at-cost tokens) and/or DeepSeek direct. Add OpenRouter as one
more free layer later by appending `:free` models to the tier groups.

## Connect each app (all via the proxy at `http://127.0.0.1:4000/v1`)

- **OpenCode** — already the repo default: root `opencode.json` defines the
  `litellm` provider (`tier1/2/3`) and pins `model: litellm/tier1`. Switch
  per session with `/models`, or edit the file. Server needs
  `LITELLM_MASTER_KEY` in its environment.
- **Claude Code Desktop** — settings → custom endpoint, or env:
  `ANTHROPIC_BASE_URL=http://127.0.0.1:4000/v1`,
  `ANTHROPIC_API_KEY=<LITELLM_MASTER_KEY>`, model `tier1`.
- **OpenHands** — LLM settings: provider OpenAI-compatible, base URL the
  proxy, model `tier1`. Custom per-token costs are already correct because
  the proxy reports real usage against the priced models.
- **Scripts** — any OpenAI-compatible client against the proxy with
  `model=tierN`; 429/5xx retry-across-providers happens server-side.

## Change the defaults

The repo default (`litellm/tier1` in `opencode.json`, tiers in
`infra/litellm/config.yaml`) is a starting point. Per-user overrides go in
`~/.config/opencode/opencode.jsonc` (OpenCode merges global under project),
never by editing someone else's keys — there are no keys here to edit.
