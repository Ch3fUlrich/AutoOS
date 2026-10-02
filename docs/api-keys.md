# API keys — how to get each one

Where each key and password lives, what it guards, and how to rotate it:
[Logins and secrets](web-services.md#logins-and-secrets).

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
registering by hand in the dashboard. Source: `catalog/ai-registry.json` — the
registry that `apply`, `tools/mirror-litellm-env.py` and
`tools/sync-router-tiers.py` all read; this table is its human-readable view.
The first column is normally the `api-keys.yml` name; a provider that shares
another's key is listed under its own registry id instead, and its Notes say
which entry to fill in (`meta_api` is that case today).

| `api-keys.yml` name | OmniRoute provider id | Notes |
|---|---|---|
| `groq` | `groq` | Cloudflare-fronted: connection needs `customUserAgent` (apply sets `curl/8.7.1`; error 1010 otherwise) |
| `google_ai_studio` | `gemini` | |
| `mistral` | `mistral` | |
| `cloudflare_workers_ai` | `cloudflare-ai` | needs your **Account ID** in the dashboard before it can serve |
| `cohere` | `cohere` | |
| `hugging_face` | `huggingface` | |
| `devin` | `devin` | Devin API key — connection registers, but the gateway lists no models (broken upstream, issue #6142); the working path is the Devin CLI binary + `devin auth login` → `devin-cli` OAuth connection |
| `cerebras` | `cerebras` | Cloudflare-fronted: `customUserAgent` (as groq) |
| `SambaNova` | `sambanova` | |
| `deepseek` | `deepseek` | |
| `meta` | — (unregistered since 2026-09-23) | Meta Model API (`api.meta.ai`); the opencode `meta` provider answers the plain `muse-spark` model directly, no gateway connection; the same value is read by `meta_api` below; see [models.md](models.md) |
| `meta_api` | `meta-api` | MUSEAPI 2026-09-27: the **same `meta` key** (`providers.meta_api.key_name: meta`) — no second entry to create. OpenAI-compatible custom registration, so the dashboard needs base URL `https://api.meta.ai/v1`. Serves `muse-spark-1.3-contributor` ($0.10/$0.20 per 1M, 100 rpm, 3M tpm), which **trains on prompts**: never a `*-clean` leg, and a `privacy: sensitive` card is refused by the spawner. `meta-api` is not a built-in OmniRoute connection — the first live `apply` creates it |
| `openrouter` | `openrouter` | |
| `zen` | `opencode-zen` | free promo models + paid; paid legs need Zen balance |
| `cheapinference` | `cheaperinference` | paid partner gateway (`ci_live_…` key); legs sit between free and paid in tier2/tier3, never in `*-clean` |
| `free_ai` | `free-ai` | Free.ai self-hosted pool; 10 rpm, 30k tokens/day, may train on prompts — **never** in a `*-clean` route |
| `morph` | `morph` | FREEKEYS-1 (D-132/D-141) 2026-09-28: **$10 vendor grant** (`tier: credit`, guard `monthly_cap_usd: 10`, warns at 80 %). Serves `morph-dsv4flash`, `morph-glm52-744b` (both tool-calling proven); `morph-v3-fast`/`-v3-large` answer but emit no tool_call, two other ids 400. No LiteLLM `.env` line — reached through the gateway |
| `bazaarlink` | `bazaarlink` | FREEKEYS-1: free tier, catalog served under prefix **`bzl`** (`model_prefix`). 87 canonical ids listed, **1** answered (`deepseek/deepseek-v4-flash-0731free:free`), the rest HTTP 400 |
| `navyai` | `navy` | FREEKEYS-1: connection registers and is active, catalog serves `navy/*`, but **no leg answered** (5x HTTP 400, 1x 403) → `available: false`. Entitlement is the operator's to fix |
| `arcee` | `arcee-ai` | FREEKEYS-1: connection registers, **the catalog lists zero models for it** → nothing to probe, `available: false`. Keys for `arcee-ai/*` only appear via `openrouter`/`free-ai`/`together` resellers |
| `bluesminds` | `bluesminds` | FREEKEYS-1: free tier, catalog prefix **`bm`**. 49 canonical ids (most are `claude-*`/`gpt-*` resale, denied by `policy.leg_rules` `deny-claude-paid-api`), **none answered** (400/410/transport) → `available: false` |
| `agentrouter` | `agentrouter` | FREEKEYS-1: free tier, 13 ids of which 12 are `claude-*` (budget-held, D-102 — never probed here); the one non-Claude id `gpt-5.6-sol` answered 400 → `available: false` |
| `novita_ai` | `novita` | FREEKEYS-1: free tier, 121 canonical ids, **every probe 403** (stopped at 3 consecutive) → `available: false`: the key authenticates at the connection level but the account has no model entitlement |
| `scaleway` | `scaleway` | FREEKEYS-1: free tier (EU "🆓" pool), catalog prefix **`scw`**. 6 ids, **3 proven** (`qwen3-235b-a22b-instruct-2507`, `gpt-oss-120b`, `mistral-small-3.2-24b-instruct-2506`) |
| `nebius` | `nebius` | FREEKEYS-1: free tier, 27 canonical ids, **3 proven** (`zai-org/GLM-5.1`, `/GLM-5.2`, `/GLM-5.3-Flash`); the Moonshot ids 404 (listed but not served) |
| `deepinfra` | `deepinfra` | FREEKEYS-1: **$5 vendor grant** (`tier: credit`, `monthly_cap_usd: 5`, warns at 80 %). 235 canonical ids, **6/6 probed legs proven** (gemini-flash family, `DeepSeek-V4-Flash-0731`, `Ling-3.0-flash`) — cheapest first, prices still not on file |
| `together_ai` | `together` | FREEKEYS-1: **$5 vendor grant** (`tier: credit`, `monthly_cap_usd: 5`, warns at 80 %). 277 canonical ids, **every probe 403** → `available: false` (the grant is not yet usable through this connection) |
| `omniroute` | — | the **client** key apps use; not a provider |

### What a `credit` grant does to routing

FREEKEYS-1b (D-132/D-141): the guard on the `tier: credit` rows above is enforced,
not just reported. `tools/autoos_usage.py credit_guards()` costs each grant from the
gateway's recorded usage rows at registry prices, and the resolver's leg filter
(`tools/autoos_resolver.py usable_legs`) drops every leg of a provider whose guard
says `refuse` — at 100 % of `monthly_cap_usd`, reason
`credit exhausted <provider> $x/$cap`. At the warn line (`monthly_warn_fraction`,
80 % by default) the leg stays, and the plan's `explain` plus the daily usage report
say `credit warn ...`.

A `credit` model with no price on file is **not usable**: `price_in`/`price_out` of
0 or missing means the grant cannot be costed at all, and an uncostable grant that
bills as $0 is a $10 drain reported as free money, so the leg is refused with
`credit leg unpriced <model>`. Neither `GET /v1/models` nor the model detail
endpoint carries a pricing block for any of these ids (measured 2026-09-28), so
today every credit leg is dropped until real prices are recorded. Free-tier models
are untouched by both rules, and `registry.py check` rejects a row that keeps
`credit_usd` while calling itself another tier.

T1-CREDIT-FIX (2026-10-01): `credit_usd` is the grant TOTAL; remaining =
`credit_usd` minus spent, so `$0 spent of $N` is intact, never exhausted. Spend
that cannot be measured (gateway call-log unreadable -- e.g. a manage key the
gateway answers with 403 -- and no dated `credit_spent_usd`/`credit_spent_as_of`
figure on the provider row) keeps the leg with a `credit spend unknown ...`
note in the plan's `explain` (fail open: a truly spent prepaid grant rejects at
the provider and the combo falls through). A 403 names itself distinctly --
`manage key rejected (403) - spend unmeasured` -- in both the plan notes and the
`usage` command output, never as a silent `$0.00`.

## Gateway keys by name (WS-OMNIREMOTE)

The OmniRoute client key field in `configuration/api-keys.yml` is now named by
**which gateway** the client targets, not by a single fixed name. This enables
multi-host setups where each machine has its own local gateway key.

### Field selection

| Gateway type | `api-keys.yml` field | When used |
|---|---|---|
| **Non-local gateway** | `omniroute_server` | `AUTOOS_OMNIROUTE_URL` is set and points to a non-loopback host (e.g. `https://gw.example.com`, `http://server:20128`) |
| **Local gateway** | `omniroute_<host>` | `AUTOOS_OMNIROUTE_URL` is unset/empty, or its host is `127.0.0.1`, `localhost`, or `::1` (any port). `<host>` is the machine's host name (see below). |

### Host name resolution

The `<host>` part of `omniroute_<host>` is resolved in this order:

1. **`AUTOOS_HOST_NAME`** environment variable — explicit override, wins always.
2. **`host_name:`** in the machine-wide host config file:
   - Windows: `%LOCALAPPDATA%\autoos\host.yml`
   - Linux/macOS: `${XDG_CONFIG_HOME:-~/.config}/autoos/host.yml`
   - Override with `AUTOOS_HOST_CONFIG` environment variable.
   - File format: flat `name: value` (same as `api-keys.yml`), e.g. `host_name: workstation`
3. **Short hostname** — the first label of the system's hostname, lower-cased, with any
   character outside `[a-z0-9_]` replaced by `_`. Prints one notice line naming the
   field it will look up.

**On the server machine itself**, if `host_name: server` is set, its local gateway
field is `omniroute_server` — no special case in code, just the natural result.

### Setup `--host-name`

```bash
./setup.sh --host-name workstation   # creates host.yml with host_name: workstation
```

If `host.yml` is missing, it is created with the given name (default = normalised
short hostname). If it exists, setup reports `skipped` and never overwrites
(AGENTS.md rule 3). This is non-secret config; it never touches `api-keys.yml`.

### Precedence

1. `AUTOOS_OMNIROUTE_KEY` environment variable — always wins, never reads the file.
2. New field from `api-keys.yml` (`omniroute_server` or `omniroute_<host>`).
3. Legacy field (one release, read-only, prints deprecation line):
   - Local: `omniroute` → prints `api-keys.yml: 'omniroute' is deprecated, rename it to 'omniroute_<host>'`
   - Server: `omniroute_client_<host>` → prints `api-keys.yml: 'omniroute_client_<host>' is deprecated, rename it to 'omniroute_server'`
4. Missing key → clear error naming the **expected field** and why (local / non-local),
   mentioning `host.yml` / `AUTOOS_HOST_NAME` if the name may be wrong.
   **Never prints a key value or the gateway URL.**

### Resolve vs exec

`tools/autoos_gateway_key.py` has two ways to hand the resolved client key to a
child process — both apply the precedence above, and **neither ever puts the key
value in argv or writes it to any file**:

- `resolve` prints the key value on **stdout** (notices and errors go to
  stderr), so the caller captures it and sets the child's environment itself —
  the value reaches stdout because that is the only channel a caller has.
- `exec` resolves once and **replaces itself with the child**, exposing the key
  only as `AUTOOS_OMNIROUTE_KEY` in the child's own environment — never in
  `argv`, never on stdout, never written to any file. This is the durable form
  for launching a worker that needs the key:

```bash
python3 tools/autoos_gateway_key.py exec -- python3 tools/autoos-agent.py run <task>
```

`exec [--optional] [--no-notice] [KEYS_FILE] -- <cmd> [args...]`: everything
before the first `--` is parsed exactly like `resolve`; everything after it is
the child command. On POSIX the child replaces this process (`execvpe`, so the
exit code *is* the child's); on Windows the child runs and its return code is
propagated. With `--optional` a missing key runs the child with no
`AUTOOS_OMNIROUTE_KEY` instead of failing (exit 1); no `--` or an empty
command is a usage error (exit 2). A `<cmd>` that cannot be executed (not found
or not executable) prints one `autoos_gateway_key: cannot execute <cmd>: <reason>`
line on stderr — never a traceback, never the environment or the key — and exits
`127` when the command was not found and `126` when it could not be run, per
shell convention.

### The sanctioned gateway review call

`tools/review-call.py` is the one sanctioned single gateway review call: one
tool-less chat completion (`stream: false`, no `tools`, no `temperature`
unless `--temperature F` is given, one user message) whose key comes **only**
from the `AUTOOS_OMNIROUTE_KEY` that `exec` puts in its environment — the tool
never reads a keys file, and a missing key is exit 2 with
`run me via autoos_gateway_key.py exec -- ...`:

```bash
python3 tools/autoos_gateway_key.py exec -- python3 tools/review-call.py --model ovh/gpt-oss-120b --prompt-file prompt.md --out-dir out
```

Optional: `--title <slug>` (the session tag becomes `review/<title>`, else
`review/<prompt-sha12>`), `--max-tokens N` (default 4096), `--temperature F`
(off by default — measured 2026-10-02 on the central gateway, a
`temperature: 0` request is cut at 64 completion tokens with an empty answer)
and `--gateway-url
URL` (otherwise `AUTOOS_OMNIROUTE_URL`, otherwise `http://127.0.0.1:20128/v1`;
a URL with no path gains `/v1`). The call is stamped with the spawner's own
`x-omniroute-session-id` / `X-AutoOS-Run-Id` headers, so the gateway call log
attributes it like a run. It writes `review.txt` and `evidence.json`
(requested/served model, status, `finish_reason`, correlation id, response
header **names** only, session tag, run id, prompt sha256, token usage, UTC
start/finish) into
`--out-dir`, or `error.json` and exit 3 on a non-200, and prints only the two
paths, the served model and the answer's last `VERDICT:` line (or `VERDICT:
missing`) — never the key, the request headers or the prompt. Exit **4** also
covers a truncated or empty answer (`finish_reason: length`, or no answer
text): `evidence.json` is still written with its `finish_reason`, and
`review-call: answer truncated/empty (finish_reason=<x>)` goes to stderr
instead of a `VERDICT: missing` success line.

### Example

```yaml
# configuration/api-keys.yml
omniroute_server: sk-server-gateway-key      # for clients pointing at the central server
omniroute_workstation: sk-workstation-key    # for this workstation's local gateway
omniroute_laptop: sk-laptop-key              # for this laptop's local gateway
# omniroute: sk-old-key                      # DEPRECATED (local fallback)
# omniroute_client_workstation: sk-old-key   # DEPRECATED (server fallback)
```

The operator renames fields by hand; AutoOS never rewrites `api-keys.yml`.

## Where to get them

Ordered by free value. "Training" = free tier may train on prompts: fine for
this public repo, never for private code.

| Key | Where | Free terms (Sep 2026) | Training? |
|---|---|---|---|
| Cheaper Inference (partner) | [cheaperinference.com](https://cheaperinference.com) | **Paid partner gateway, not a free tier**: 42-model resale (30% under list), own `ci_live_…` key, own billing. Sits between free and paid in tier2/tier3 | Per upstream model |
| Mistral | [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys) | **Biggest documented pool: ~1B/mo per org**, 2 RPM, rate-limited free mode, no card | Check terms |
| Gemini (AI Studio) | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | Flash-family pooled, uncapped figure, dynamic limits; Pro left free tier Apr 2026; 2.0 Flash dead Jun 2026 | **Yes** |
| Groq | [console.groq.com/keys](https://console.groq.com/keys) | Per-model 200K tokens/day caps (~30M pool); llama-3.3-70b left free tier Aug 2026 — use GPT-OSS/Qwen/Llama current IDs | Check terms |
| Free.ai (`free_ai`) | [free.ai](https://free.ai/?ref=46pK6GCwBJs) (referral link) → sign up, then generate an API key (`sk-free-…`) | 30,000 tokens/day on its self-hosted models only (e.g. `qwen7b`, served as Qwen3-30B-A3B-Instruct); 10 requests/min on a free account; external models (GPT, Claude, …) cost paid tokens and are not used here | Not stated: public work only |
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
never enters `catalog/ai-registry.json` or the LiteLLM `.env`. **Windows:**
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

- **Claude Code** — **opt-in** (`Set-AutoOSClaudeGateway` /
  `route_claude_to_gateway`, postInstall of `claude-code` and the `omniroute`
  routing step on all platforms), chosen by the `claude_gateway_routing`
  answer. `gateway` merges `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` into
  `~/.claude/settings.json`; while those two keys are set the claude.ai
  connectors are disabled. `login` (the default, also what `--yes` picks)
  removes exactly those two keys again and keeps everything else. Backup only
  when the file changes; a second run reports skipped. Pre-answer with
  `AUTOOS_ANSWER_CLAUDE_GATEWAY_ROUTING=gateway` or the config file's
  `answers`. Subscription models additionally need the `claude` gateway OAuth
  connection above.
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

## Claude Code version lag (no pin)

CLIPIN / D-137 (operator 2026-09-29) supersedes the pin idea: **no host pins
Claude Code.** Every install comes from the catalog's unpinned
`@anthropic-ai/claude-code` and the autoupdater stays on — nobody sets
`DISABLE_AUTOUPDATER`, neither in the environment nor in the `env` block of
`~/.claude/settings.json` (`%USERPROFILE%\.claude\settings.json` on Windows).
Do not add a version to a catalog entry and do not add that key: an update
reaches a running session only at restart, so a pin would freeze a host on a
release its launch profiles were not written for.

What replaces the pin is a read-only lag check ([tools/claude-cli-lag.py](../tools/claude-cli-lag.py),
stdlib, Linux and Windows/WSL):

    python3 tools/claude-cli-lag.py    # exit 0 up to date / ahead / unknown, 1 this host lags

On the host it runs on, it prints `claude --version`, the newest published release (the npm
registry, cached an hour in the git-ignored `logs/`, and `unknown` when offline
— an unreachable registry is never an error), whether the host lags
(`lags - restart picks it up`), and the autoupdater state with the variable or
file that set it. When the installed version differs from the last one
recorded, it adds one recommendation line — a recommendation, not a gate — to
re-run the cheap spec behaviour checks: [ORCH-A1 §3.3, deny-over-allow](plans/2026-09-28-orch-a1-role-launch-profiles-spec.md)
and [HOOKS §6, the guard contracts](plans/2026-09-28-agent-hooks-spec.md). Both
depend on Claude Code's own permission precedence and hook payload shape, which
a release can change underneath them.

A WSL host that must inspect the *Windows* settings file names it explicitly
(`--settings /mnt/c/Users/<profile>/.claude/settings.json`); the tool never
guesses a profile path across `/mnt`.

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

## Repeated names, comments and placeholders

Every reader of `configuration/api-keys.yml` (Python, the bash launchers through the resolver CLI, PowerShell) follows the
rules of `tools/keys_file.py`: when a name appears twice, the FIRST filled-in value wins (`run-opencode-serve.sh` used to take the
last line); an unquoted ` #` starts a comment (quote a value that contains one); a value containing `REPLACE` is a placeholder and
is skipped, so a later real value of the same name is used.
