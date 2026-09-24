# API keys — how to get each one

**One file, one command.** Every key goes into
`configuration/api-keys.yml` (git-ignored; copy from
`configuration/api-keys.example.yml`), then:

```bash
./configuration/omniroute/apply.sh      # or apply.ps1 on Windows
```

That registers every key with OmniRoute (provider-specific quirks included)
and builds the tier combos — see [configuration/README.md](../configuration/README.md).
The client key apps send (`omniroute:`) comes from the OmniRoute dashboard →
**api-manager → Create API Key**; it is not the same as the provider keys.
The LiteLLM `.env` is only for the manual fallback router.

Keys never belong in tracked files, chat logs, or screenshots. Missing keys
are fine — routing skips that provider.

## OpenRouter first (BYOK)

Routing is openrouter-first: add every provider key you hold to your
OpenRouter account (**dashboard → Settings → Provider keys**, manual checklist
— there is no API that pushes provider credentials into OpenRouter; its
Management API manages OpenRouter keys only). Benefits: one billed/metered
surface, the full reasoning-effort ladder per model (gateway combos expose
only `low/medium/high`), and paid legs that answer when free pools 429.
Then put the same keys in `configuration/api-keys.yml` so the gateway can
route free-first with paid overflow. Note the billing shift: a BYOK leg bills
to your provider key, not to OpenRouter credit — credit-chain economics in
[models.md](models.md) assume direct keys.

## Provider key → OmniRoute provider id

`apply` maps the names in `api-keys.yml` automatically; this table is for
registering by hand in the dashboard. Source: `catalog/providers.json` — the
registry that `apply`, `tools/mirror-litellm-env.py` and
`tools/sync-router-tiers.py` all read; this table is its human-readable view.

| `api-keys.yml` name | OmniRoute provider id | Notes |
|---|---|---|
| `groq` | `groq` | Cloudflare-fronted: connection needs `customUserAgent` (apply sets `curl/8.7.1`; error 1010 otherwise) |
| `google_ai_studio` | `gemini` | |
| `mistral` | `mistral` | |
| `cloudflare_workers_ai` | `cloudflare-ai` | needs your **Account ID** in the dashboard before it can serve |
| `cohere` | `cohere` | |
| `hugging_face` | `huggingface` | |
| `z_ai` | `zai` | Z.AI (GLM family, wired 2026-09-24); legs curated only after a live leg-probe |
| `devin` | `devin` | Devin API key — connection registers, but the gateway lists no models (broken upstream, issue #6142); the working path is the Devin CLI binary + `devin auth login` → `devin-cli` OAuth connection |
| `cerebras` | `cerebras` | Cloudflare-fronted: `customUserAgent` (as groq) |
| `SambaNova` | `sambanova` | |
| `deepseek` | `deepseek` | |
| `meta` | — (unregistered since 2026-09-23) | Meta Model API (`api.meta.ai`); openrouter-first — direct access via the opencode `meta` provider only; see [models.md](models.md) |
| `openrouter` | `openrouter` | |
| `zen` | `opencode-zen` | free promo models + paid; paid legs need Zen balance |
| `cheapinference` | `cheaperinference` | paid partner gateway (`ci_live_…` key); legs sit between free and paid in tier2/tier3, never in `*-clean` |
| `omniroute` | — | the **client** key apps use; not a provider |

## Where to get them

Ordered by free value. "Training" = free tier may train on prompts: fine for
this public repo, never for private code.

| Key | Where | Free terms (Sep 2026) | Training? |
|---|---|---|---|
| Cheaper Inference (partner) | [cheaperinference.com](https://cheaperinference.com) | **Paid partner gateway, not a free tier**: 42-model resale (30% under list), own `ci_live_…` key, own billing. Sits between free and paid in tier2/tier3 | Per upstream model |
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

## More free legs worth registering (thin quotas, real use)

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

## OmniRoute CLI auth (`OMNIROUTE_API_KEY`)

Read/write CLI commands (`providers add/test`, `models`, `setup-*`) work
unauthenticated, but the **management endpoints** (`oauth start`,
`providers auth`, `setup-claude` catalog fetch) answer `401 Unauthorized`
without a server key. The key is the `omniroute:` client key from
`api-keys.yml` — the same bearer apps send to `:20128`.

**Automated (Windows):** the `omniroute` catalog component exports it as a
persistent User-scope `OMNIROUTE_API_KEY` (`Set-AutoOSOmniRouteCliKey`,
postInstall) the first time setup runs. Existing values always win
(user-managed, never overwritten); missing/placeholder keys warn and skip.
Every new terminal inherits it. Remove with:
`[Environment]::SetEnvironmentVariable('OMNIROUTE_API_KEY',$null,'User')`.
Security: localhost-only bearer key, same sensitivity as the git-ignored
`api-keys.yml` it is read from — user-scoped, never committed, never logged.

**Manual (any shell):**

```powershell
$env:OMNIROUTE_API_KEY = '<value of omniroute: in configuration/api-keys.yml>'
```

```bash
export OMNIROUTE_API_KEY='<value of omniroute: in configuration/api-keys.yml>'
```

(Linux/macOS have no automated export yet — use the snippet above.)

## OAuth connections (subscriptions, free bridges)

CLI logins and gateway connections are separate stores: logging into
`agy`/`claude`/`qoder` on your machine creates **no** gateway connection.
Each needs one interactive flow; afterwards `providers list` shows an
account-named connection and `oauth status` shows it active.

Prerequisite: `OMNIROUTE_API_KEY` set (previous section) — otherwise every
flow below 401s before showing a URL.

```powershell
omniroute oauth start --provider antigravity --import-from-system --no-browser
# reuses the local agy login, prints the authorization URL only, no browser.
# Open it, complete the Google login, copy the code from the redirect URL,
# paste it at the terminal prompt.
omniroute providers auth claude-code --no-browser   # same code-paste flow
omniroute providers test antigravity                # "No API-key probe for
omniroute providers test claude                     # oauth connections" is
                                                    # expected — proof is a chat (see below)
```

Rules that bit us (2026-09-24): the OAuth allowlist uses canonical ids —
`antigravity`, not the `agy` alias (`providers auth agy` → "Unknown OAuth
provider"); **`qoder` has no CLI OAuth flow at all** (not in `oauth
providers`) — connect it in the dashboard → Providers page instead. The
trailing `Assertion failed ... UV_HANDLE_CLOSING` after any CLI error is a
cosmetic Windows crash-on-exit, not a second failure. Proof pattern per
connection: `providers list` (account-named row) → `models <provider>` →
one `ack` chat per leg before it enters `combos.json` (phantom-leg rule).

## Qoder PAT (CLI-only key, not a gateway provider)

`qoder_pat:` in `api-keys.yml` feeds `QODER_PERSONAL_ACCESS_TOKEN`, which the
Qoder CLI reads at startup for headless/ACP use. There is no gateway
provider id for it (the gateway `qoder` entry is OAuth/dashboard-only), so it
never enters `catalog/providers.json` or the LiteLLM `.env`. **Windows:**
the `qodercli` catalog component appends `~/.qoder/bin` to User PATH (the
dashboard error was precisely this: the binary lives beside the IDE install,
not on PATH) and exports the PAT absent-only, same mechanism as
`OMNIROUTE_API_KEY` above. Both writes broadcast `WM_SETTINGCHANGE`, so new
terminals see them without sign-out — but long-running processes (including
the OmniRoute server itself) keep their stale environment until restarted;
if the dashboard still fails after setup, restart the gateway. **Linux/macOS:**
install via the component, then
`export QODER_PERSONAL_ACCESS_TOKEN='<value of qoder_pat:>'` yourself.
Warning from Qoder docs: an env PAT takes precedence over `/login`
credentials — clear it before `/logout`, or the next start signs straight
back in.

## CLI coding tools via the gateway

- **Claude Code** — pipeline equivalent landed (`Set-AutoOSClaudeGateway` /
  `route_claude_to_gateway`, `claude-code` postInstall on all platforms):
  merges `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` into
  `~/.claude/settings.json` with backup + idempotent skip. Subscription
  models additionally need the `claude` gateway OAuth connection above.
- **Qwen Code** — install `npm i -g @qwen-code/qwen-code` (done live
  2026-09-24: 0.24.4), backup `~/.qwen/settings.json` first, then
  `omniroute setup-qwen --model t2-worker --yes --api-key
  $env:OMNIROUTE_API_KEY`. Writes the `t2-worker (OmniRoute)` entry plus
  `~/.qwen/.env` (`OMNIROUTE_API_KEY` only, existing provider credentials
  untouched). Combo ids work directly as `--model` values. No `qwen`
  binary on PATH is exactly the dashboard "found but not runnable" state.
  Proven: headless `qwen -p "Reply with exactly: ack"` → ack.
- **Gemini CLI** — no `setup-*` recipe (launch-only):
  `npm i -g @google/gemini-cli`, then `omniroute run gemini`
  (`GOOGLE_GEMINI_BASE_URL` is read at the process root).
- **Antigravity CLI** — no CLI recipe either; its only integration is the
  `antigravity` gateway OAuth connection above (and it is not ACP-spawnable).
- **Devin CLI** — catalog component `devin-cli` (winget
  `CognitionAI.DevinCLI` on Windows, vendor `install.sh` on Linux/macOS).
  Then interactive `devin auth login` (cannot be automated), then gateway
  side via the dashboard Providers page (`providers add devin-cli --oauth`
  answers "Unknown OAuth provider" — the CLI OAuth allowlist holds 8
  providers; `--dry-run` misleadingly passes because it never checks the
  allowlist). The `devin` API-key provider lists no models (broken upstream,
  issue #6142) — do not route on it.

## LiteLLM fallback `.env` (only if you use it)

Copy `configuration/litellm/.env.example` → `.env`. Same keys as above
(`GROQ/CEREBRAS/GEMINI/MISTRAL/OPENROUTER/META/DEEPSEEK/OPENCODE_ZEN_API_KEY`)
plus `LITELLM_MASTER_KEY` (a random local password you invent). Missing keys
are fine — chains skip what they cannot authenticate.

## Rules

- Rotate a leaked key at the provider console immediately; proxy needs no
  config change, just the new value + restart.
- Free tiers churn monthly (this page already needed corrections 3 weeks
  after writing). Re-check `/dashboard/free-tiers` before trusting a number.
