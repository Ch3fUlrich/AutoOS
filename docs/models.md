# Model routing — OmniRoute first, LiteLLM as fallback

Every agent in this repo (OpenCode CLI/TUI, Zed Agent, OpenHands, Claude Code
Desktop, ad-hoc scripts) talks to **OmniRoute on `:20128`**, which routes
across all connected free tiers with quota-aware auto-fallback. LiteLLM on
`:4000` stays configured as the manual fallback for anyone who prefers it.

## Why OmniRoute over hand-maintained LiteLLM chains

Our LiteLLM config rotted within 3 weeks: Cerebras killed no-card free,
llama-3.3-70b left Groq free, Gemini cut limits 50-80%. OmniRoute's catalog
re-audits this for us, and replaces static lists with live strategies:

| Need | LiteLLM (fallback) | OmniRoute (primary) |
|---|---|---|
| Fallback | Fixed ordered list per tier | 19 strategies: `priority`, `lkgp` (stick to last-good), `headroom`, `auto` (16-factor live scoring) |
| Free tracking | Hand-edited comments | Pool-deduped catalog + `/dashboard/free-tiers` with live used/remaining |
| Multi-key rotation | No | Each connection scored independently |
| Zero-config start | No (needs `.env`) | `auto` answers keyless out of the box |
| Token stretch | No | RTK→Caveman compression 15-95% (content-dependent, don't budget on it) |
| Tool coverage | Manual per-app config | `omniroute setup-opencode` / `run opencode`, 36+ tools, one client key |

Caveats: single-maintainer project shipping very fast (pin your installed
version mentally; dashboard shows it) — which is exactly why LiteLLM stays as
fallback. And OmniRoute recovers from Zen aggregate throttling gracefully; it
does not remove the throttle. ToS: proxying Zen-free carries Anomaly’s
internal-use-only clause — paid/user keys are the clean legs.

## Tier mapping (repo defaults in `opencode.jsonc`)

| Tier | OmniRoute model | Behavior |
|---|---|---|
| `tier1` orchestrator | `auto/smart` | Quality-first + explores; set caller effort to xhigh/Max for long runs |
| `tier2` smart | `auto` | Balanced 16-factor scoring |
| `tier3` driver | `auto/cheap` | Cost-weighted; codegen/review/lint |

Exact-control alternative: create a **priority combo** in
dashboard → combos (or `omniroute combo create`) that encodes the old static
chain, then send its exact name as the model:

- `tier1-strict`: `oc/…` Zen-free spark → `meta/muse-spark-1.3-contributor`
  (OpenRouter, $0.10/$0.20) → Meta-direct contributor (own credits) →
  paid: `deepseek-v4.1-flash`. Paid rule preserved: contributor, else flash.
- `tier3-strict`: Mistral free → Groq free → Gemini free → paid flash.

Your OmniRoute balance (already topped up) is the final paid leg behind these.

## Run it

```bash
npm install -g omniroute
omniroute                       # dashboard http://localhost:20128
omniroute doctor                # config, ports, runtime, liveness
omniroute providers test-all    # every connection, at once
omniroute setup-opencode        # writes opencode's own config (global;
                                # repo opencode.jsonc still wins by precedence)
omniroute run opencode --model auto/smart   # zero-config launch, nothing written
```

Keys: dashboard → Providers → + Add Provider ([guide](api-keys.md)).
Client key: dashboard → api-manager → Create API Key → env
`AUTOOS_OMNIROUTE_KEY`. Fallback router: `configuration/litellm/` +
`LITELLM_MASTER_KEY` (`litellm --test` after first start).

## Connect each app (key = `AUTOOS_OMNIROUTE_KEY`)

- **OpenCode** — repo default already points at `:20128`
  (`omniroute/tierN`, `litellm/tierN` kept as fallback). Per session:
  `/models`. Unattended: V2 allow-permissions in global config (see below).
- **Zed** — setup writes provider `autoos-omniroute` (`auto/smart` xhigh,
  `auto`, `auto/cheap`) into Zed's `settings.json`; key via env, never file.
  Zen-free is *not* available through Zed's native OpenCode provider — via
  OmniRoute (`oc/…`, `auto`) it is.
- **Neovim + sidekick** — `install_lazyvim` enables the `ai.sidekick` extra;
  `<leader>aa` runs opencode, which inherits the repo routing.
- **Claude Code / Codex / others** — `omniroute run <tool>` injects env per
  process, or `setup-*` writes the tool config. Base URL `…:20128/v1`.
- **Headless boxes** — `server` profile ticks `opencode-cli` + `neovim`.
- **Unattended permissions** (OpenCode V2 — `providers`/`permissions`, not
  the V1 `provider`/`permission` shape in old blog posts):
  ```jsonc
  { "permissions": [
    { "action": "edit", "resource": "*", "effect": "allow" },
    { "action": "shell", "resource": "*", "effect": "allow" },
    { "action": "shell", "resource": "rm -rf *", "effect": "ask" },
    { "action": "shell", "resource": "sudo *", "effect": "ask" } ] }
  ```

## Routing map

```mermaid
flowchart TB
    subgraph clients["Agents (one client key: AUTOOS_OMNIROUTE_KEY)"]
        OC["opencode CLI/TUI\nomniroute/tier1·2·3"]
        ZED["Zed agent panel\nauto/smart · auto · auto/cheap"]
        NV["Neovim + sidekick\n(<leader>aa → opencode)"]
        OH["OpenHands / Claude Code\nbase URL → :20128"]
    end
    subgraph or["OmniRoute :20128 (primary)"]
        AUTO["auto/* combos\nsmart · balanced · cheap\n16-factor scoring +\n5-30 min circuit breakers"]
        PRI["priority combos\ntier1-strict · tier3-strict\nspark → contributor → flash"]
        DASH["dashboard :20128\nproviders · free-tiers · quota"]
    end
    subgraph free["Free legs (register keys once)"]
        MI["Mistral ~1B/mo"]
        GE["Gemini Flash pooled"]
        GR["Groq per-model caps"]
        ZAI["Z.AI GLM uncapped"]
        KILO["Kilo / Nara / llm7 / xKiro"]
        ZEN["Zen oc/* rotating free"]
    end
    subgraph paid["Paid legs (last resort)"]
        BAL["Your OmniRoute balance"]
        META["Meta direct contributor"]
        ORC["OpenRouter contributor $0.10"]
        DFL["DeepSeek V4.1 Flash"]
    end
    subgraph fb["LiteLLM :4000 (manual fallback)"]
        LT["tier1·2·3 static chains\nconfiguration/litellm/"]
    end
    OC & ZED & NV & OH --> or
    AUTO & PRI --> free
    AUTO & PRI -. quota exhausted .-> paid
    OC -. "model litellm/*" .-> LT
    DASH -. live used/remaining .-> AUTO
```

## Change the defaults

Tiers in `opencode.jsonc`, combos in the OmniRoute dashboard, static chains
in `configuration/litellm/config.yaml`. Per-user overrides go in
`~/.config/opencode/opencode.jsonc` (global merges under project).
