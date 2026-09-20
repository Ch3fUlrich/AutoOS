# Model routing — one LiteLLM proxy for every agent

Every agent in this repo (OpenCode CLI/TUI, Zed Agent, OpenHands, Claude Code
Desktop, ad-hoc scripts) talks to the **same local LiteLLM proxy**, which
routes across three free-first tiers with paid fallbacks. One `.env` holds all
keys, one `config.yaml` holds all routing, per-model pricing is fixed in one
place so cost displays are correct everywhere.

## The tiers (cheapest capable first — cost efficiency is the point)

| Tier | Role | Chain |
|---|---|---|
| `tier1` | Orchestration: Muse Spark 1.3, xhigh effort, 1M context, tool-calling. For long unattended runs | Zen `…-contributor-free` (promo) → OpenRouter `meta/muse-spark-1.3-contributor` ($0.10/$0.20) → Meta direct on your credits ($20 starter) |
| `tier1-paid` | Paid escalation, orchestrators only | Meta-direct contributor → Zen `deepseek-v4.1-flash`. Nothing else — no opus/sonnet/fable by default |
| `tier2` | Smart second level | Cerebras `gpt-oss-120b` → Groq `gpt-oss-120b` → Gemini `3.8-flash` (all free) → paid: Zen `deepseek-v4.1-flash` |
| `tier3` | Daily driver: codegen, research, review, lint | Groq `llama-3.3-70b` → Mistral `devstral-small` → Gemini `2.5-flash` → Mistral `small` (all free) → paid: Zen `deepseek-v4.1-flash` → DeepSeek direct |

Free deployments carry `rpm` guards matching their free quotas, the router
serves least-used-first, a 429ing deployment cools down 2 minutes, and each
tier falls to its `-paid` group only after retries. `drop_params` is on so one
caller works against heterogeneous providers.

**Caveats:**
- `xhigh` is a reasoning-*effort* tier, not a model ID: aliases resolve to the
  1.3 family, effort is the caller's control (Zed: `reasoning_effort: "xhigh"`,
  Muse Code: Max level).
- Contributor / promo-free routes trade training-data use for price — fine for
  this public repo, never for private code; the paid legs are the clean ones.
  Same for Gemini free tier (may train Google models).
- Zen documents Claude/Spark under `/responses` or `/messages` while this
  config speaks `/chat/completions`. DeepSeek/Kimi/GLM legs are on the safe
  path; **run `litellm --test` after first start** and repoint anything that
  errors on path — the chain degrades gracefully meanwhile.

## Run it

```bash
pip install 'litellm[proxy]'   # or: pipx install "litellm[proxy]"
cp configuration/litellm/.env.example configuration/litellm/.env  # fill in, never commit
litellm --config configuration/litellm/config.yaml --port 4000
litellm --config configuration/litellm/config.yaml --test
```

Keys: [api-keys.md](api-keys.md). Add OpenRouter `:free` models as an extra
free layer later by appending to the tier groups.

## Connect each app (proxy: `http://127.0.0.1:4000/v1`, key = `LITELLM_MASTER_KEY`)

- **OpenCode** — repo default: root `opencode.jsonc` defines the `litellm`
  provider (`tier1/2/3`) and pins `model: litellm/tier1`. Per session:
  `/models`. Unattended runs: allow-by-default **V2** permissions in global
  `~/.config/opencode/opencode.jsonc` (note: `providers`/`permissions`, not
  the V1 `provider`/`permission` shape still floating around in blog posts):
  ```jsonc
  { "permissions": [
    { "action": "edit", "resource": "*", "effect": "allow" },
    { "action": "shell", "resource": "*", "effect": "allow" },
    { "action": "shell", "resource": "rm -rf *", "effect": "ask" },
    { "action": "shell", "resource": "sudo *", "effect": "ask" } ] }
  ```
  Keep the destructive asks — a smart model still makes dumb mistakes.
- **Zed** — AutoOS writes an `openai_compatible` provider (`autoos-litellm`,
  tiers as models, `reasoning_effort: "xhigh"` on tier1) into Zed's
  `settings.json` during setup; the key comes from env `AUTOOS_LITELLM_API_KEY`
  (set it to your master key), never from the file. Zen free models are *not*
  available through Zed's native OpenCode provider — via the proxy they are.
  Prefer the CLI driven through Zed's terminal over external-agent ACP unless
  you need ACP events.
- **Neovim + sidekick** — `install_lazyvim` enables LazyVim's
  `ai.sidekick` extra (`<leader>aa` toggles the opencode panel). opencode.nvim
  / avante.nvim are documented alternatives, not installed.
- **Claude Code Desktop** — `ANTHROPIC_BASE_URL=http://127.0.0.1:4000/v1`,
  `ANTHROPIC_API_KEY=<master key>`, model `tier1`.
- **OpenHands** — OpenAI-compatible provider, base URL the proxy, model
  `tier1`. Costs display correctly because the proxy reports real usage.
- **Headless boxes** — no GUI → the `server` profile ticks `opencode-cli`
  (and `neovim`): full terminal coding via TUI + sidekick, same tiers.

## Change the defaults

Repo default (`litellm/tier1` in `opencode.jsonc`, tiers in
`configuration/litellm/config.yaml`) is a starting point. Per-user overrides
go in `~/.config/opencode/opencode.jsonc` (global merges under project),
never by editing someone else's keys — there are no keys here to edit.
