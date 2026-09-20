# Verification — what was tested, and how

Dated results from the machine this configuration was built on. Everything
below was produced by the commands shown; nothing is a claim about a provider
that was not exercised.

```
Machine   HP ENVY x360 · Windows 11 · Docker Desktop 4.73
Gateway   OmniRoute 3.8.50 · :20128
Clients   opencode 2.0.11 (@opencode/cli) · OpenHands docker.openhands.dev
Date      2026-09-20
```

## Router combos — `apply --probe`

`configuration/omniroute/apply.ps1 -Probe` sends one tiny request
(`max_tokens 2048` — reasoning models need a real budget) to every combo and
reports what answered. Latest run:

| Combo | Served by | Result |
|---|---|---|
| `tier1` | `meta/muse-spark-1.3-contributor` (OpenRouter) | OK |
| `tier1-clean` | `meta/muse-spark-1.3` (OpenRouter) | OK |
| `tier2` | `openai/gpt-oss-120b` | OK |
| `tier2-clean` | `openai/gpt-oss-120b` | OK |
| `tier3` | `mistral-code-latest` | OK |
| `tier3-clean` | `qwen/qwen3.8-27b` | OK |

Notes from the runs:

- `tier1` hops over the Zen free leg (`muse-spark-1.3-contributor-free`
  answers HTTP 500 at peak) and lands on OpenRouter, as designed.
- `tier1-clean` occasionally returns 502 "empty response" when the request
  budget is small; with a realistic budget it answers. Spark spends tokens on
  hidden reasoning first — not a routing fault.
- The first leg of each chain is attempted first; failures are visible in the
  probe output rather than silently swallowed.

## Direct model refs (individual calls)

| Ref | Result |
|---|---|
| `gemini/gemini-3.8-flash` | OK |
| `sambanova/gpt-oss-120b` | OK |
| `deepseek/deepseek-flash` | OK |
| `openrouter/deepseek/deepseek-v4.1-flash` | OK |
| `mistral/mistral-code-latest` | OK (429 when the free pool is throttled) |
| `mistral/mistral-small-latest` | OK (2 RPM free tier) |
| `groq/openai/gpt-oss-120b` | OK — after the Cloudflare UA fix, see below |
| `cerebras/gpt-oss-120b` | OK — same fix |
| `opencode-zen/muse-spark-1.3-contributor-free` | 500 at peak (Zen side) |
| `opencode-zen/muse-spark-1.3`, `opencode-zen/gemini-3.1-pro` | 402 — Zen paid balance/key not set up |
| `muse-code/muse-spark-1.3` | not in the provider's live catalog (OmniRoute bug, below) |

## opencode CLI

opencode **2.0.11** (V2) with the repo's `opencode.jsonc`:

```
opencode run --model omniroute/tier1   "Reply with exactly: ack"   -> ack
opencode run --model omniroute/tier2   "Reply with exactly: ack"   -> ack
opencode run --model omniroute/tier3   "Reply with exactly: ack"   -> ack
opencode run --model omniroute/tier3-clean "Reply with exactly: ack" -> ack
```

The legacy V1 CLI (`npm opencode-ai`) silently **omits** the V2 `providers`
block ("Omitted native setting that cannot be represented in V1") and then
fails with `ProviderModelNotFoundError`. The catalog now installs
`@opencode/cli` (V2) for this reason — see docs/models.md.

## OpenHands

- Image pulled through the AutoOS pipeline: `setup.ps1 -Only openhands -Yes`
  (the catalog component, not a hand-typed docker command).
- `docker.openhands.dev/openhands/openhands:latest` runs, the web UI answers
  on <http://localhost:3000>, and the app pulls its own agent-server image on
  first conversation (currently `ghcr.io/openhands/agent-server`,
  `1.36.0-python`).
- LLM wiring: V1 configures the model in **Settings → LLM → Add LLM Profile
  → Advanced**, not from the `LLM_*` env vars alone. The profile used here is
  `openai_tier1`: model `openai/tier1`, base URL
  `http://host.docker.internal:20128/v1`, OmniRoute client key. (The `openai/`
  prefix is the LiteLLM transport selector; without it: "LLM Provider NOT
  provided".)
- **End-to-end proven 2026-09-20**: new conversation → sandbox booted
  healthy → message "Reply with exactly the two words: sandbox ok" sent via
  the chat box → agent replied "sandbox ok", served through `tier1`.
- Regression found and fixed the same day: the test container had been
  created with stale `AGENT_SERVER_IMAGE_REPOSITORY/TAG=1.26.0` pins from an
  earlier `start-stack` revision, which the app/sandbox combination rejected
  ("Sandbox entered error state"). Fix: no pins — the app resolves the
  agent-server image itself. `start-stack.*` no longer sets them.
- `configuration/openhands/config.toml` mirrors those models for the
  V0/dev path.

## Known issues found by testing (and their state)

| Issue | State |
|---|---|
| Groq + Cerebras answer Cloudflare `error 1010` to Node's default UA | **Fixed** by `apply` setting `providerSpecificData.customUserAgent` per connection |
| OpenHands sandbox "error state" on every conversation | **Fixed 2026-09-20**: stale `AGENT_SERVER_IMAGE_*=1.26.0` pins in the test container env; recreated without pins, sandbox boots healthy, message round-trip proven |
| `/v1/models` returns 401 for a normal client key in OmniRoute 3.8.50 | Open upstream; `apply` warns and skips catalog filtering, `--probe` is the real check |
| `muse-code` (Meta direct) 502s in 3.8.50 (`Cannot read properties of undefined`) | Open upstream; key registered, spark goes via OpenRouter meanwhile |
| OpenHands sandbox sometimes reports "error state" while the first agent-server image is still downloading | Retry after the pull completes; the sandbox itself comes up healthy |
| Cloudflare Workers AI needs the Account ID before it can serve | Not in any tier until configured in the dashboard |
| Zen free promo 500 at peak / Zen paid 402 without balance | Chain-hop design absorbs both |
| `omniroute --output json models` truncates at 50 and `--output json` does not lift it (its own hint is wrong) | Documented; do not treat that listing as authoritative |

## Re-running this

```bash
./configuration/omniroute/apply.sh --probe
opencode run --model omniroute/tier3 "Reply with exactly: ack"
```

```powershell
.\configuration\omniroute\apply.ps1 -Probe
.\configuration\start-stack.ps1 -App openhands   # then configure the profile once
```
