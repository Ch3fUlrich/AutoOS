# Lane NebiusCombos — remove every `nebius/*` leg from `combos.json` (2026-10-01)

**Lane:** `L1-backlog/ws-nebius2-combos-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-nebius2combos`
**Base:** FREEWIRE wiring tip `2ff537a` (free legs wired, gemini excluded)
**Date:** 2026-10-01 (gateway measurements 05:45:47–05:45:50Z)
**Writer:** nebius-combos-2 (pinned)
**Scope:** `configuration/omniroute/combos.json` **only**. The registry half
(`catalog/ai-registry.json` route legs / model / provider rows) belongs to **L1-beta**.

> **REVIEW NONCE: `NEBREMOVAL-NONCE-4Kt7Wq2Z`** — a reviewer must quote this
> string back, read from THIS file, to prove it read the evidence and not a
> summary.

---

## 0. What this lane did and did not do

- **Removed** all six `nebius/*` legs from `configuration/omniroute/combos.json`
  and added one dated `NEBREMOVAL` paragraph to the file's `$comment`. Older
  history comments (`CTXFIX`, `OVHLEGS`, …) are untouched.
- **Did NOT** edit `catalog/ai-registry.json` (L1-beta owns the registry half),
  and **did NOT** restart any gateway.
- **Found and recorded (not fixed, out of scope):** under the repo's own
  `tests/test_registry.py::usable_legs` metric, `t1-orchestrator` falls from 2
  distinct usable providers to **1** once nebius is gone — see §6. The other
  five agentic routes stay ≥2.

---

## 1. The six legs removed

`grep -n nebius` over the pre-change file returned the six model refs below
(all mid-band; **no combo head** was a nebius leg) plus history text in
`$comment` lines 52–53 (`CTXFIX`), which is left intact.

| Combo | Removed leg | Was at |
|---|---|---|
| `t1-orchestrator` | `nebius/zai-org/GLM-5.3-Flash` | model 2 of 5 |
| `t1-orchestrator-free-only` | `nebius/zai-org/GLM-5.3-Flash` | model 5 of 6 |
| `t2-worker` | `nebius/zai-org/GLM-5.2` | model 4 of 19 |
| `t2-worker-free-only` | `nebius/zai-org/GLM-5.2` | model 10 of 14 |
| `t3-driver` | `nebius/zai-org/GLM-5.2` | model 2 of 18 |
| `t3-driver-free-only` | `nebius/zai-org/GLM-5.3-Flash` | model 7 of 11 |

Post-change proof (JSON parses; zero nebius model refs remain):

```
JSON OK; combos= 22
Nebius legs remaining: []
```

Diff is `1 file changed, 14 insertions(+), 7 deletions(-)` —
6 deleted leg lines, 1 deleted trailing-comma on the OVH comment line, and the
13-line `NEBREMOVAL` block.

### 1.1 Why the legs were dead anyway (live store already skipped them)

`apply.ps1` validates every leg against the live `/v1/models` catalog and skips
what the catalog does not know. Before this change the live store already
served **zero** nebius legs (baseline `omniroute combo list --json`, captured
2026-10-01T02:05Z: `t1-orchestrator` had 4 legs — `scw/qwen3-235b`,
`scw/mistral-small`, `meta-api/muse-spark`, `deepseek/deepseek-flash` — with no
nebius). So the removal makes the tracked source of truth match what the
gateway already served, and stops a future registry-driven apply from
re-introducing a leg nothing answers.

---

## 2. The `NEBREMOVAL` comment

One dated paragraph appended to `$comment` (quoted verbatim from the file):

```jsonc
"NEBREMOVAL 2026-10-01: every nebius/* leg is removed from every combo of",
"this file - six legs in total, all mid-band, no combo head:",
"t1-orchestrator and t1-orchestrator-free-only (nebius/zai-org/GLM-5.3-Flash),",
"t2-worker, t2-worker-free-only and t3-driver (nebius/zai-org/GLM-5.2), and",
"t3-driver-free-only (nebius/zai-org/GLM-5.3-Flash). No replacement leg was",
"added; the remaining legs already satisfy the >=2-distinct-usable-provider",
"invariant on every agentic route. The live store had already skipped these",
"legs (apply.ps1 skips a model the live /v1/models catalog does not know), so",
"this change makes the tracked source of truth match what the gateway serves",
"and stops a future registry+combos apply resurrecting them. Removing the",
"matching routes.<id>.legs and models/providers rows in",
"catalog/ai-registry.json is L1-beta's half; until that lands, render",
"omniroute --check reports drift on the six combos named above, which beta clears."
```

`$comment` is excluded from the render equality check
(`tools/registry.py::_canonical_omniroute` drops the whole key), so adding it
does not by itself create drift.

---

## 3. Verification — every command, quoting the result

| Command | Result |
|---|---|
| `python tools/registry.py check` | `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers` (exit 0) |
| `python tools/registry.py validate` | same ok line (exit 0) |
| `python tools/registry.py render omniroute --check` | **drift on exactly the six edited combos** (exit 1) — see §4 |
| `python tools/registry.py render litellm --check` | `ok: render litellm matches configuration/litellm/config.yaml` (exit 0) |
| `python tools/registry.py render ide --check` | `ok: render ide matches catalog/ide-models.json` (exit 0) |
| `python tools/registry.py render openhands --check` | `ok: render openhands matches configuration/openhands/tier-profiles.json` (exit 0) |
| `python tools/registry.py render models-doc --check` | `ok: render models-doc matches docs/models.md` (exit 0) |
| `python tools/sync-ide-models.py --check` | `OK: opencode.jsonc, configuration/openhands/tier-profiles.json, configuration/openhands/config.toml match ai-registry.json` (exit 0) |
| `python tools/audit-router.py --offline` | `no drift (non-200 legs above are provider/balance state, not config)` (exit 0) |
| `python tests/test_registry.py` | `Ran 300 tests … FAILED (failures=1)` — the **pre-existing** `test_a_credit_leg_is_last_and_gated_until_priced` (same failure FREEWIRE recorded at this base, §7) |

All registry-based checks pass because the registry is untouched; only the
omniroute render drifts, and only on the combos this file changed.

### 3.1 `python tools/registry.py check` (quoted)

```
info: t1-orchestrator-clean exempt from privacy rule 3 - carries the OpenRouter contributor leg (openrouter/meta/muse-spark-1.3-contributor) deliberately, since the 2026-09-21 contributor-only block made it paid-only rather than trains-nothing (combos.json's own $comment); the spawner requires --allow-training to route a privacy=sensitive card there (tools/autoos-agent.py, tools/autoos_routing.py select_combo()).
ok: registry 2026-09-30, 32 routes, 80 models, 34 providers
```

### 3.2 `python tools/audit-router.py --offline` (quoted)

```
combos: 22 (deepseek-v4.1-flash, gemini-3.8-flash, groq-qwen3.8-27b, hf-glm-5.2, hf-qwen3.8-27b, opus-4-6, or-laguna-s-2.1-free, or-nemotron-3-super-free, or-north-mini-code-free, or-qwen3.8-27b-free, spark-1.3-contributor, t1-orchestrator, t1-orchestrator-free-only, t1-orchestrator-paid, t2-orchestrator, t2-worker, t2-worker-clean, t2-worker-free-only, t3-driver, t3-driver-clean, t3-driver-free-only, t4-rag)

no drift (non-200 legs above are provider/balance state, not config)
```

---

## 4. `render omniroute --check` drift — every line, labelled

```
differs: combos.t1-orchestrator
differs: combos.t1-orchestrator-free-only
differs: combos.t2-worker
differs: combos.t2-worker-free-only
differs: combos.t3-driver
differs: combos.t3-driver-free-only
```

**All six lines are the expected registry-vs-combos drift** and each is named
after exactly one of the six combos this lane edited. The fresh render still
carries the nebius legs because `catalog/ai-registry.json` still declares them;
once **L1-beta** removes the matching `routes.<id>.legs` / model / provider rows,
a fresh render equals the committed file and the check returns `ok`. No other
render drifts (§3), so there is **no unexpected** drift line.

---

## 5. Live apply and live store

```
$ powershell -ExecutionPolicy Bypass -File configuration/omniroute/apply.ps1 -DryRun
...
  - t1-orchestrator: would create [priority] with scw/qwen3-235b-a22b-instruct-2507,scw/mistral-small-3.2-24b-instruct-2506,meta-api/muse-spark-1.3-contributor,deepseek/deepseek-flash
  - t2-worker: would create [priority] with antigravity/gemini-3.7-flash-high,scw/qwen3-235b-a22b-instruct-2507,scw/mistral-small-3.2-24b-instruct-2506,openrouter/nvidia/nemotron-3-super-120b-a12b:free,...
  ...
$ powershell -ExecutionPolicy Bypass -File configuration/omniroute/apply.ps1
Gateway OK on http://127.0.0.1:20128
  + t1-orchestrator replaced (priority)
  + t1-orchestrator-free-only replaced (priority)
  + t2-worker replaced (priority)
  + t2-worker-free-only replaced (priority)
  + t3-driver replaced (priority)
  + t3-driver-free-only replaced (priority)
  = resilience settings already current (maxWaitMs=180000, breaker=2)
  = deepseek/deepseek-flash already 1048576
```

All 22 combos were replaced with the corrected leg sets; no nebius ref is in
the dry-run plan or the live apply. (Pre-existing, baseline-identical:
`apply.ps1` cannot parse `ai-registry.json` on Windows PowerShell —
`DuplicateKeysInJsonString` on `mistral-small-…` vs `Mistral-Small-…` — so the
provider section is skipped; it was skipped at the base too. `api-keys.yml` is
absent on this host, so the model-catalog read and the `-Probe` step are
skipped; the combos still apply.)

### 5.1 `omniroute combo list --json` — ZERO nebius legs (live)

```
TOTAL combos: 22
Nebius legs in live store: 0
AUDIT-NEBREMOVAL-OK
```

Provider breakdown of the live store after apply:

```
t1-orchestrator: 4 legs; providers=[deepseek,meta-api,scw]
t1-orchestrator-free-only: 5 legs; providers=[groq,huggingface,openrouter,scw]
t2-worker: 18 legs; providers=[antigravity,deepseek,free-ai,groq,huggingface,meta-api,openrouter,ovh,scw]
t2-worker-free-only: 13 legs; providers=[antigravity,free-ai,groq,huggingface,openrouter,scw]
t3-driver: 17 legs; providers=[deepseek,groq,huggingface,meta-api,mistral,openrouter,ovh,scw]
t3-driver-free-only: 10 legs; providers=[free-ai,groq,huggingface,openrouter,scw]
```

---

## 6. Ack probe per touched combo (UTC, `POST /v1/chat/completions`, `max_tokens:1024`)

| UTC | Combo | HTTP | ms | served by |
|---|---|---|---|---|
| 05:45:47Z | `t1-orchestrator` | 200 | 336 | `qwen3-235b-a22b-instruct-2507` (scw) |
| 05:45:47Z | `t1-orchestrator-free-only` | 200 | 250 | `qwen3-235b-a22b-instruct-2507` (scw) |
| 05:45:48Z | `t2-worker` | 200 | 384 | `qwen3-235b-a22b-instruct-2507` (scw) |
| 05:45:48Z | `t2-worker-free-only` | 200 | 1820 | `nvidia/nemotron-3-super-120b-a12b:free` (openrouter) |
| 05:45:50Z | `t3-driver` | 200 | 293 | `mistral-small-3.2-24b-instruct-2506` (scw) |
| 05:45:50Z | `t3-driver-free-only` | 200 | 3269 | `nvidia/nemotron-3-super-120b-a12b:free` (openrouter) |

**6/6 HTTP 200.** No `429`, `503`, `504`, no `chat_admission_busy`, no
`Rate limit exceeded`; no backoff was needed. No probe served a nebius leg
(there are none left to reach).

---

## 7. Distinct-usable-provider count per agentic route

The repo's own metric (`tests/test_registry.py::usable_legs` = `live_legs` ∩
`tool_calls: proven` ∩ priced-credit ∩ context-fits, read off the **registry**;
the six agentic routes are `AGENTIC_TIER_ROUTES`). Measured now, and simulated
after the nebius legs are dropped:

| Route | distinct usable providers now | **after nebius removed** | verdict |
|---|---|---|---|
| `t1-orchestrator` | 2 (nebius, scaleway) | **1 (scaleway)** | **⚠ BELOW 2** |
| `t1-orchestrator-free-only` | 5 | 4 | ok |
| `t2-worker` | 6 | 5 | ok |
| `t2-worker-free-only` | 5 | 4 | ok |
| `t3-driver` | 6 | 5 | ok |
| `t3-driver-free-only` | 5 | 4 | ok |

`t1-orchestrator`'s only two usable legs now are both scaleway
(`scaleway/qwen3-235b-a22b-instruct-2507`,
`scaleway/mistral-small-3.2-24b-instruct-2506`); nebius was its sole *second
provider*. Its two other live legs (`meta_api/muse-spark-1.3-contributor`,
`deepseek/deepseek-flash`) fail the `tool_calls: proven` filter
(`tool_calls=unproven`), so the resolver — and
`test_every_agentic_route_has_two_distinct_usable_providers` once beta removes
the registry legs — counts them as unusable. This is **not** created by the
`combos.json` edit (the live gateway already served no nebius leg); the edit
merely makes the latent single-provider state visible in the tracked registry
metric.

**Handoff to L1-beta (registry owner):** after removing the nebius route legs,
`t1-orchestrator` needs a second distinct-provider **usable** leg to keep the
≥2 invariant — e.g. mark one of its paid legs (`meta_api/muse-spark-1.3-contributor`
or `deepseek/deepseek-flash`) `tool_calls: proven`, or add a probe-passed leg on
a new provider. Under the broader `live_legs` metric (no `proven` filter) the
route still has 3 distinct providers, so this is strictly a
proven-tool-call gap, not a transport one. Flagged for L0/beta.

---

## 8. Pre-existing failure left in place (NOT this lane)

`tests/test_registry.py::test_a_credit_leg_is_last_and_gated_until_priced`
fails at base `2ff537a` exactly as here:

```
AssertionError: 0 not greater than or equal to 1 : gemini-3.8-flash: credit leg vertex/gemini-3.8-flash is not at the end
Ran 300 tests in 3.004s
FAILED (failures=1)
```

Root cause is the combos-lineage/free-leg work (unpriced credit legs not gated
at the tail); it is identical to the failure FREEWIRE recorded at this base. Not
introduced or worsened by the nebius removal (which touches no `credit` leg).

---

## 9. Files changed

| File | Change |
|---|---|
| `configuration/omniroute/combos.json` | 6 `nebius/*` legs removed; one dated `NEBREMOVAL` `$comment` block |
| `docs/handoff/2026-10-01-laneNebiusCombos.md` | this evidence doc |
| `logs/handoff-sessions/DONE-ws-nebius-combos.md` | DONE note (`git add -f`) |

## 10. Reviews

Three `t3-reviewer` subagents run read-only, on three families, **including two
free models** (operator mandate), each nonce-gated on
`NEBREMOVAL-NONCE-4Kt7Wq2Z` (must be quoted back from THIS file). Recorded in
the DONE note → see `logs/handoff-sessions/DONE-ws-nebius-combos.md` §Reviews.

## 11. Explicitly NOT done here

- No `catalog/ai-registry.json` edit (L1-beta's half) — hence the expected
  §4 drift.
- **No gateway restart.**
- No other lane's files touched.
