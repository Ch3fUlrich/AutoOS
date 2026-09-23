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
| `tier1` | `meta/muse-spark-1.3-contributor` (OpenRouter) | OK 2026-09-21 |
| `tier1-clean` | `meta/muse-spark-1.3-contributor` (OpenRouter) | OK 2026-09-21 |
| `tier2` | `gemini-3.8-flash` | OK 2026-09-21 |
| `tier2-clean` | `deepseek-flash` | OK 2026-09-21 |
| `tier3` | `mistral-code-latest` | OK 2026-09-21 |
| `tier3-clean` | `deepseek-flash` | OK 2026-09-21 |
| `spark-1.3-contributor` | zen free → openrouter paid (same legs as tier1) | OK 2026-09-22 (`ack`, incl. `#low`/`#medium`/`#high`; `#minimal`/`#xhigh`/`#max` unavailable on this build) |
| `rag` | `cohere/command-a-03-2025` (trial key) | OK 2026-09-22 (`ack` via gateway and via litellm proxy) |

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
| `opencode-zen/muse-spark-1.3` | 402 — Zen paid balance/key not set up |
| `opencode-zen/gemini-3.1-pro` | REMOVED from tiers 2026-09-20 (reasons worse than 3.8-flash); 402 anyway |
| `cheaperinference/deepseek-v4-flash`, `glm-4.5-air`, `kimi-k3`, `minimax-m2.7` | refs resolve in `simulate --combo tier2/tier3 --explain` (all CLOSED, quota 100%); live ack via the tier2/tier3 probes |
| `muse-code/muse-spark-1.3` | not in the provider's live catalog; connection carries an empty outbound URL (`Invalid outbound URL`, red topology) — OmniRoute 3.8.50 defect, open upstream. Connection deactivated 2026-09-21 (by connection ID; by-name edit does not persist); spark goes via OpenRouter/Zen contributor legs |

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
| `muse-code` (Meta direct) 502s in 3.8.50 (`Cannot read properties of undefined`) | Open upstream; connection deactivated 2026-09-21, spark goes via OpenRouter/Zen contributor meanwhile |
| OpenHands sandbox sometimes reports "error state" while the first agent-server image is still downloading | Retry after the pull completes; the sandbox itself comes up healthy |
| Cloudflare Workers AI needs the Account ID before it can serve | Not in any tier until configured in the dashboard |
| Zen free promo 500 at peak / Zen paid 402 without balance | Chain-hop design absorbs both |
| `gemini-3.7-flash` cooling-down shown inside opencode | NOT a combo: call-log 2026-09-22 shows `comboName: None`, a real 1.2 MB / 1935-message body, `requestedModel: gemini/gemini-3.7-flash`, and the gemini free-tier 429 (`free_tier_input_token_count, limit: 250000`). A session had that direct model picked, bypassing every combo and its fallbacks. Repo references only `gemini-3.8-flash` in `tier2`; re-select a combo (`omniroute/tier2`) |
| `omniroute --output json models` truncates at 50 and `--output json` does not lift it (its own hint is wrong) | Documented; do not treat that listing as authoritative |

## Prefix handling (measured 2026-09-22, gateway :20128)

- Bare `tier2` from a plain OpenAI client: answers (combo resolves).
- `openai/tier2` from the same bare client: **401** (the gateway reads
  `openai/` as a provider hop, not a combo name).
- `openai/tier1` from OpenHands: round-trips (proven 2026-09-20, sandbox ok).

Conclusion: the `openai/` prefix OpenHands requires is stripped by the
OpenHands/litellm client stack before the HTTP call — the wire always
carries the bare tier name. OpenHands fallback profiles (`litellm-tier*`
on `:4000`) therefore use `openai/tierN` models against the proxy's
`(plain) tierN` model_names, the same shape as the proven gateway path.
No proxy-side alias is needed.

## The "gemini-3.7-flash is cooling down" incident — full chain (2026-09-22)

Symptom: selecting `spark-1.3-contributor` (or `tier1`) in opencode showed
*"All credentials for model gemini-3.7-flash are cooling down"* — a model no
combo references.

Chain, each link measured:

1. **`requestQueue.maxWaitMs` was 15000 ms.** Muse Spark is a reasoning model
   and spends longer than 15 s thinking, so the gateway killed its own request
   with *"Request exceeded OmniRoute's local rate-limit execution expiration"*
   → the spark leg looked dead.
2. The chain then fell through to `opencode-zen/muse-spark-1.3-contributor-free`,
   which **deterministically 403s through the gateway**: *"OpenCode's free tier
   can only be used from within OpenCode"*. It was the FIRST leg, so every
   request paid that failed round-trip.
3. With both legs exhausted the combo reported the last leg's error, and the
   gateway's rescue/reporting path surfaced an uncurated model's error text —
   the gemini free tier's `429 free_tier_input_token_count, limit: 250000`.

Falsified on the way (each checked, none was the cause): the compression
pipeline (`enabled: false`), gateway model aliases, auto-routing rules
(`routing_decisions` empty), every client config file, and opencode's session
store. The request body carried opencode's own tool set, which is why it first
looked like a hand-picked model.

Fixes, all verified:

| Fix | Evidence |
|---|---|
| `requestQueue.maxWaitMs` 15000 → **180000** (`PATCH /api/resilience`) | `tier1`, `spark-1.3-contributor`, `tier1-clean` each **3/3 `ack`** after; 502/504 before |
| **Zen free promo restored FIRST** in `tier1` + `spark-1.3-contributor`, with `providerBreaker.apikey.failureThreshold` 12 → **2** (operator call 2026-09-23) | 5 spark requests → only **3 zen attempts** (2 requests skipped it entirely) and the skipped ones were faster (2.8 s / 4.7 s vs 7.5 s / 8.1 s); all 5 served by the paid leg |
| `apply.*` sets `maxWaitMs` **and** the breaker threshold on every run | fresh machines cannot inherit the 15 s / 12-failure defaults |
| `tools/audit-router.py` flags a low `maxWaitMs`, a high breaker threshold, and any combo-bypassing ref | both suites run it with `--offline` |
| LiteLLM `*-paid` groups lead with the OpenRouter leg; `simple-shuffle` | `tier1-paid`/`tier2-paid`/`tier3-paid` answer; before, all three 402'd |

Design note on the breaker: it is one global threshold for api-key
connections, which turns out to be the right shape — every failing free leg now
hops after two attempts instead of being retried ~12 times, so *all* the
priority chains got faster, not just spark. `resetTimeoutMs` stays 30 s, so a
promo leg that recovers is picked up again within half a minute.

Also worth knowing: a bare `gemini-3.7-flash` resolves to the PAID OpenRouter
alias (200), but the provider-qualified `gemini/gemini-3.7-flash` binds to the
free tier and 504s. Provider-qualified refs bypass every combo and have no
fallback — that is why `FORBIDDEN_DIRECT_REFS` in the audit exists.

## Full leg probe — every distinct combo leg (2026-09-22)

One direct `:20128` chat per distinct leg in `combos.json` (20 legs). **No
phantom 400s remain**; every non-OK result is a real upstream state that the
priority chains hop past by design.

| Result | Legs |
|---|---|
| OK | `groq/openai/gpt-oss-120b`, `cerebras/gpt-oss-120b`, `sambanova/gpt-oss-120b`, `cheaperinference/deepseek-v4-flash`, `cheaperinference/glm-4.5-air`, `cheaperinference/minimax-m2.7`, `openrouter/deepseek/deepseek-v4.1-flash`, `deepseek/deepseek-flash`, `mistral/mistral-code-latest`, `groq/qwen/qwen3.8-27b`, `cerebras/qwen-3.8-27b`, `cohere/command-a-03-2025`, `cohere/command-r-plus-08-2024`, `openrouter/google/gemini-3.8-flash` |
| 403 zen free-tier | `opencode-zen/muse-spark-1.3-contributor-free` (promo gate) |
| 502 spark empty | `openrouter/meta/muse-spark-1.3-contributor` (needs a real output budget; chain hops) |
| 503 high demand | `gemini/gemini-3.8-flash` (transient) |
| 504 local expiry | `cheaperinference/kimi-k3` (gateway execution cap, transient) |
| 402 zen paid | `opencode-zen/deepseek-v4.1-flash` (Zen balance) |
| 429 2-RPM | `mistral/mistral-small-latest` (resets in seconds) |
| **400 phantom** | **none** |

## LiteLLM proxy hard-won notes (2026-09-22)

- The proxy does **not** read `configuration/litellm/.env` by itself: start
  it via `configuration/litellm/start-litellm.ps1`, which exports the `.env`
  into the process env first. A hand-started proxy serves every leg as
  "Missing credentials" with a complete `.env` on disk.
- The proxy serves the `.env` master key; clients must send the machine
  `LITELLM_MASTER_KEY`/`AUTOOS_LITELLM_API_KEY` value (one value, aligned
  2026-09-22). A mismatch fails at `user_api_key_auth` as "No connected db."
- `usage-based-routing-v2` needs no DB for routing here (exonerated
  2026-09-22 after the master-key fix; the outage was auth, not strategy).
- Cohere legs need `additional_drop_params: ["strict"]` — opencode sends
  OpenAI-style `strict`, which cohere rejects; global/per-model
  `drop_params` does not cover it (measured 2026-09-22).

## Re-running this

```bash
./configuration/omniroute/apply.sh --probe
opencode run --model omniroute/tier3 "Reply with exactly: ack"
```

```powershell
.\configuration\omniroute\apply.ps1 -Probe
.\configuration\start-stack.ps1 -App openhands   # then configure the profile once
```

## OpenHands Agent Canvas verdict (2026-09-20, no switch)

Agent Canvas is real and is the current upstream direction: the
`OpenHands/OpenHands` repo README is now Agent Canvas branding, with
`@openhands/agent-canvas` on npm (v1.20.0 verified) and
`ghcr.io/openhands/agent-canvas` (amd64 + arm64 verified via manifest).
It serves on port **8000** (`/canvas` for the docker image), needs
`PROJECTS_PATH` + `~/.openhands` mounts, and is labelled beta.

Verdict: **stay on `docker.openhands.dev/openhands/openhands:latest` (:3000)
for now.** The current path is verified end to end today (sandbox boots,
message round-trips through `tier1`); Canvas is a different app surface
(multi-backend control center, not a drop-in) and would need its own
LLM-profile wiring proof before it replaces anything. Re-evaluate when
Canvas leaves beta or the `:3000` image is retired.
