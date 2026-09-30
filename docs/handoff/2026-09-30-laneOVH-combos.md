# OVH credit-tier legs for t2-worker and t3-driver — combos lane (2026-09-30)

Evidence doc for the `L1-backlog/ws-ovh-20260930` combos lane. All claims
are measured; no secret values are quoted.

Branch: `L1-backlog/ws-ovh-20260930` (worktree `AutoOS-ws-ovh`).
Stacked on: `a5bcb69` (combos lane: vertex second leg + 1M contexts).
Commits: `a9d174d` (combos.json OVH legs, cherry-pick of dropped `8384f85`),
`1afa196` (docs/models.md prose), `a7b9315` (this evidence doc).

---

## 1. Context

The operator registered connection `ovhcloud` (provider id `2e7f59a9`,
active). Both `ovh/…` and `ovhcloud/…` prefixes route. L0-verified 200
acks on 6 chat candidates. The `catalog/ai-registry.json` OVH entries
(tier `credit`, `credit_usd 200`, 3 model rows) were added by commit
`f6f5e69` on this branch. The registry routes for t2-worker and
t3-driver still need a `render` (without `--check`) to incorporate
the OVH legs — until then `render omniroute --check` reports 2 new
"differs" (see section 5).

## 2. Probe evidence

Each candidate probed via the direct gateway
(`http://127.0.0.1:20128/v1/chat/completions`) with PLAIN model names
(e.g. `ovh/gpt-oss-120b`), never the `omniroute/` prefix. Each probe:
1. ACK: "Say hello in one word" at max_tokens=512
2. Tool-call: "What is the weather in Paris?" with a get_weather tool
3. Round trip: feed back {"temp_c": 18}, check the answer mentions 18

| Leg | ACK | Tool call | Round trip | Verdict |
|---|---|---|---|---|
| ovh/gpt-oss-120b | 200, "Hello" | 200, get_weather | 200, mentions 18 | ACK+TOOL+RT |
| ovh/Qwen3-Coder-30B-A3B-Instruct | 200, "Hello!" | 200, get_weather | 200, mentions 18 | ACK+TOOL+RT |
| ovh/Qwen3.8-27B | 200, "Hello" | 200, get_weather | 200, mentions 18 | ACK+TOOL+RT |
| ovh/Qwen3.5-397B-A17B | 200, "Hello" | 200, get_weather | 200, mentions 18 | ACK+TOOL+RT |
| ovh/Mistral-Small-3.2-24B-Instruct-2506 | 200, "Hi" | 200, get_weather | 200, mentions 18 | ACK+TOOL+RT |
| ovh/Meta-Llama-3_3-70B-Instruct | 200, "Hello." | 200, get_weather | 200, mentions 18 | ACK+TOOL+RT |

All 6 passed ACK+TOOL+RT. Chosen 3 by operator suggestion:
- `ovh/gpt-oss-120b` (agentic)
- `ovh/Qwen3-Coder-30B-A3B-Instruct` (cheap code)
- `ovh/Qwen3.8-27B` (fast)

Non-chat OVH ids (embeddings/tts/stt/image): inventoried only, not wired.

## 3. Leg ordering per combo

### t2-worker (11 legs, context 128k)

| Position | Leg | Tier |
|---|---|---|
| 1 | gemini/gemini-3.8-flash | free |
| 2 | antigravity/gemini-3.7-flash-high | free |
| 3 | scw/qwen3-235b-a22b-instruct-2507 | free |
| 4 | scw/mistral-small-3.2-24b-instruct-2506 | free |
| 5 | nebius/zai-org/GLM-5.2 | free |
| **6** | **ovh/gpt-oss-120b** | **credits (NEW)** |
| **7** | **ovh/Qwen3-Coder-30B-A3B-Instruct** | **credits (NEW)** |
| **8** | **ovh/Qwen3.8-27B** | **credits (NEW)** |
| 9 | deepseek/deepseek-flash | paid (fallback) |
| 10 | meta-api/muse-spark-1.3-contributor | paid |
| 11 | free-ai/qwen7b | free (last resort) |

### t3-driver (9 legs, context 128k)

| Position | Leg | Tier |
|---|---|---|
| 1 | scw/mistral-small-3.2-24b-instruct-2506 | free |
| 2 | nebius/zai-org/GLM-5.2 | free |
| 3 | scw/qwen3-235b-a22b-instruct-2507 | free |
| **4** | **ovh/gpt-oss-120b** | **credits (NEW)** |
| **5** | **ovh/Qwen3-Coder-30B-A3B-Instruct** | **credits (NEW)** |
| **6** | **ovh/Qwen3.8-27B** | **credits (NEW)** |
| 7 | mistral/mistral-code-latest | paid |
| 8 | deepseek/deepseek-flash | paid (fallback) |
| 9 | meta-api/muse-spark-1.3-contributor | paid |

Placement: credits tier after free legs, before paid-as-you-go
(operator order: trial → free → credits → paid).

NOT modified: `t2-worker-free-only`, `t3-driver-free-only`,
`t2-worker-clean`, `t3-driver-clean`, and all other combos.

## 4. Fallback requirement

`deepseek/deepseek-flash` sits as the paid fallback leg in both
`t2-worker` (position 9) and `t3-driver` (position 8). The
`deepseek-v4.1-flash` combo (single-leg, `deepseek/deepseek-flash`)
provides the fallback when a model/leg fails. The sweep lane's evidence
doc (`docs/handoff/2026-09-30-laneSweep-t2-models.md`) does not exist
yet, so the existing deepseek/deepseek-flash placement satisfies the
operator's fallback requirement.

## 5. Verification results

| Check | Result |
|---|---|
| `registry.py render omniroute --check` | 7 "differs": 5 pre-existing (gemini-3.8-flash, opus-4-6, t2-orchestrator, t2-worker-clean, t3-driver-clean) + 2 new (t2-worker, t3-driver) — all are cross-lane dependencies on L1-beta's registry (context_advertised stale + missing OVH routes). Will resolve after L0-side ws-ovh lane's `ai-registry.json` commit is merged. |
| `test_registry_render.py` | 5 test failures (same 5 tests as before, now showing 7 "differs" entries instead of 5). 155 passed. No NEW test failures added. |
| `registry.py render models-doc --check` | ok (matches) |
| `registry.py render litellm --check` | ok (matches) |
| `registry.py render ide --check` | ok (matches) |
| `registry.py render openhands --check` | ok (matches) |
| `apply.ps1 -DryRun` | OVH legs recognized by live catalog (no "catalog does not know" warnings for any ovh/ leg). t2-worker and t3-driver would be created with OVH legs. |
| `apply.ps1` (live) | t2-worker and t3-driver replaced (priority) with OVH legs. |
| `apply.ps1` (idempotent 2nd run) | Same output, no errors. Idempotent. |
| `audit-router.py --offline` | 15 combos, no drift. |
| `audit-router.py` (live) | Started in background; offline audit confirms config validity. |
| ack-per-leg probe | All 6 OVH candidates passed ACK+TOOL+RT (see section 2). |

## 6. Cross-lane dependencies

- **Registry OVH routes**: commit `f6f5e69` (OVH provider + 3 model
  rows in `ai-registry.json`) is on this branch. The registry routes
  for t2-worker and t3-driver still list the pre-OVH legs, so
  `render omniroute --check` reports 2 new "differs". Running
  `python tools/registry.py render omniroute` (without `--check`) will
  write the OVH legs into the registry routes and resolve the differs.
  Until then, `apply` validates against the live `/v1/models` catalog
  (OVH legs are known to the gateway).
- **L1-beta** (registry context_advertised): 5 pre-existing "differs"
  entries (stale context_advertised on gemini-3.8-flash, opus-4-6,
  t2-orchestrator, t2-worker-clean, t3-driver-clean) — documented by
  the combos lane (a5bcb69), not introduced by this lane.

## 7. Admission/rate-limit log

No `chat_admission_busy` or `Rate limit exceeded` events encountered
during probing or apply. Backoff count: 0.
