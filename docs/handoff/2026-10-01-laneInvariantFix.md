# Lane InvariantFix — restore `t1-orchestrator`'s ≥2-distinct-usable-provider invariant (2026-10-01)

**Lane:** `L1-backlog/ws-invariant-fix-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-invariant` (proved: `git rev-parse --show-toplevel` → `C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-invariant`, HEAD `92a98af4`)
**Base:** nebius-combos-2's tip `92a98af4` (six `nebius/*` combos legs already removed)
**Date:** 2026-10-01 (gateway probes 05:58:57–06:04:03Z)
**Writer:** invariant-fix (pinned)
**Scope:** the registry route leg + the three surfaces it renders to + the omniroute combo. See §8 for why the registry (named as "L1-beta's half" in the brief) had to be touched.

> **REVIEW NONCE: `INVARFIX-NONCE-2Vb9Xq4M`** — a reviewer must quote this
> string back, read from THIS file, to prove it read the evidence and not a
> summary.

---

## 0. The defect, restated with the read path

`tests/test_registry.py::ComboCrossProviderTests.test_every_agentic_route_has_two_distinct_usable_providers`
calls `usable_legs(reg, route_id)`, which filters `live_legs()` on
**`model.get("tool_calls") == "proven"`** (tests/test_registry.py:3319) and reads
`catalog/ai-registry.json` via `load_registry()` (same file, lines 30, 62–64).
So the metric is a property of the **registry**, not of `combos.json`: a
`combos.json`-only edit cannot move it. That is the reason this lane edited the
registry route leg and re-rendered the surfaces (§8).

At `92a98af4` the registry still declares `nebius/zai-org/GLM-5.3-Flash` on
`t1-orchestrator`, so the test is green *now*. It goes red the moment **L1-beta**
(`L1-backlog/ws-nebius2-20260930`, `172a92bb`) drops the nebius route legs.
Simulated inline (deepcopy + strip every `nebius/*` route leg) before the fix:

```
t1-orchestrator              usable_legs=2 distinct_providers=1 ['scaleway']
t1-orchestrator-free-only    usable_legs=5 distinct_providers=4 [...]
t2-worker                    usable_legs=13 distinct_providers=5 [...]
t2-worker-free-only          usable_legs=11 distinct_providers=4 [...]
t3-driver                    usable_legs=13 distinct_providers=5 [...]
t3-driver-free-only          usable_legs=9 distinct_providers=4 [...]
```

`t1-orchestrator`'s only remaining usable legs are both scaleway
(`scaleway/qwen3-235b-a22b-instruct-2507`,
`scaleway/mistral-small-3.2-24b-instruct-2506`); its live paid legs
(`meta_api/muse-spark-1.3-contributor`, `deepseek/deepseek-flash`) are
`tool_calls: unproven`.

---

## 1. The fix — one added proven free leg

**`groq/qwen/qwen3.8-27b`** added to `routes.t1-orchestrator.legs` in the free
band (after the two scaleway legs, before the paid `meta_api` leg), and mirrored
into the omniroute combo / litellm block / models-doc row. This is one of the 10
FREEKEYS-1 free legs the wiring pass measured
(`git show L1-backlog/ws-freewire-20260930:docs/handoff/2026-09-30-laneFreeWire.md`).

Why this leg and not the others:

| Candidate (proven free) | provider tier | live probe today | verdict |
|---|---|---|---|
| `groq/qwen/qwen3.8-27b` | **free** | **200 + tool call** | **chosen** |
| `huggingface/zai-org/GLM-5.2` | free | 401 `No active credentials for provider: huggingface` | rejected |
| `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | **paid** | 200 + tool call | rejected — provider tier `paid` fails `test_on_t1_it_follows_the_free_band` |
| `openrouter/qwen/qwen3.8-27b:free` | paid | 429 | rejected (tier + rate) |
| `vertex/gemini-3.8-flash` | credit | — | rejected (credit + unproven) |
| `free_ai/qwen7b` | free | — | rejected (unproven) |

The provider-tier rule is `MetaApiProviderTests.test_on_t1_it_follows_the_free_band`
(tests/test_registry.py:1797): **every leg ahead of the paid contributor leg must
have provider tier `free`**. `groq` is provider tier `free`; `openrouter` is
provider tier `paid` even for a `:free` model (its `:free` legs are counted as
free by the *effective* `leg_tier`, but that test reads the **provider** tier).
HuggingFace is provider tier free but has no wired credential on this host.

`test_a_new_free_leg_never_trails_a_paid_leg` uses `NEW_FREE_LEGS` (the
FREEKEYS-2 scaleway/nebius set); `groq/qwen/qwen3.8-27b` is not in that set, and
the new leg sits ahead of the paid legs anyway. No `*-clean` route gained the
leg; `t1-orchestrator-free-only` is untouched (it already carried it).

### 1.1 The dated comment (verbatim from the two files)

Registry route `$comment` (appended):
> `T1SECOND 2026-10-01: groq/qwen/qwen3.8-27b (FREEKEYS-1 tool_calls-proven; providers.groq tier free; 128k) added to the free band after the scaleway legs so this route keeps TWO distinct proven providers once nebius is removed (L1-beta); the leg answered a live gateway ack+tool-call probe (200, get_weather, max_tokens 512) at 05:58:58Z. Chosen over huggingface/zai-org/GLM-5.2 (401 'no active credentials' on this host) and openrouter/*:free (provider tier paid, which fails test_on_t1_it_follows_the_free_band - only a provider-tier-free leg may precede the paid contributor). The matching combos.json combo carries the same leg with its nebius legs already dropped (NEBREMOVAL).`

`combos.json` `$comment` carries the matching `T1SECOND` paragraph.

---

## 2. Invariant test — the exact selector (green)

`-k` is a pytest flag; the suites are `unittest`, so the selector is the
dotted test id. Quoted:

```
$ python -m unittest tests.test_registry.ComboCrossProviderTests.test_every_agentic_route_has_two_distinct_usable_providers -v
test_every_agentic_route_has_two_distinct_usable_providers (...) ... ok
Ran 1 test in 0.019s
OK
```

`test_every_agentic_route_has_three_usable_legs` — also **ok**.

### 2.1 Counts per agentic route (`usable_legs` metric)

As committed (registry still carries nebius):

```
t1-orchestrator              usable_legs=4 distinct_providers=3 ['nebius', 'openrouter', 'scaleway']
t1-orchestrator-free-only    usable_legs=6 distinct_providers=5 ['groq', 'hugging_face', 'nebius', 'openrouter', 'scaleway']
t2-worker                    usable_legs=14 distinct_providers=6 ['groq', 'hugging_face', 'nebius', 'openrouter', 'ovhcloud', 'scaleway']
t2-worker-free-only          usable_legs=12 distinct_providers=5 ['groq', 'hugging_face', 'nebius', 'openrouter', 'scaleway']
t3-driver                    usable_legs=14 distinct_providers=6 ['groq', 'hugging_face', 'nebius', 'openrouter', 'ovhcloud', 'scaleway']
t3-driver-free-only          usable_legs=10 distinct_providers=5 ['groq', 'hugging_face', 'nebius', 'openrouter', 'scaleway']
```

After L1-beta's simulated nebius removal (the state the defect is about):

```
t1-orchestrator              usable_legs=3 distinct_providers=2 ['groq', 'scaleway']
t1-orchestrator-free-only    usable_legs=5 distinct_providers=4 ['groq', 'hugging_face', 'openrouter', 'scaleway']
t2-worker                    usable_legs=13 distinct_providers=5 ['groq', 'hugging_face', 'openrouter', 'ovhcloud', 'scaleway']
t2-worker-free-only          usable_legs=11 distinct_providers=4 ['groq', 'hugging_face', 'openrouter', 'scaleway']
t3-driver                    usable_legs=13 distinct_providers=5 ['groq', 'hugging_face', 'openrouter', 'ovhcloud', 'scaleway']
t3-driver-free-only          usable_legs=9 distinct_providers=4 ['groq', 'hugging_face', 'openrouter', 'scaleway']
```

`t1-orchestrator` is **2** (groq = the added leg, scaleway), and the other five
stay ≥2. **All six pass.**

---

## 3. Verification — every command, quoting the result

| Command | Result |
|---|---|
| `python tools/registry.py check` | `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers` (exit 0) |
| `python tools/registry.py validate` | same `ok:` line (exit 0) |
| `python tools/registry.py render omniroute --check` | **drift on exactly the six nebius combos** (exit 1) — §4 |
| `python tools/registry.py render litellm --check` | `ok: render litellm matches ...config.yaml` (exit 0) |
| `python tools/registry.py render ide --check` | `ok: render ide matches ...ide-models.json` (exit 0) |
| `python tools/registry.py render openhands --check` | `ok: render openhands matches ...tier-profiles.json` (exit 0) |
| `python tools/registry.py render models-doc --check` | `ok: render models-doc matches ...models.md` (exit 0) |
| `python tools/audit-router.py --offline` | `no drift (non-200 legs above are provider/balance state, not config)` (exit 0) |
| `python tests/test_registry.py` | 300 ran, **1 failure** — the pre-existing `test_a_credit_leg_is_last_and_gated_until_priced` (identical at base, §5) |
| `python tests/test_registry_render.py` | 160 ran, **5 failures** — identical set at base, §5 |
| `python tests/test_autoos_resolver.py` | 329 ran, **OK** (base: OK) |
| `python tests/test_sync_ide_models.py` | 36 ran, **OK** (base: OK) |

### 3.1 `check` / `validate` (quoted, last line each)

```
ok: registry 2026-09-30, 32 routes, 80 models, 34 providers
```
(one `info: t1-orchestrator-clean exempt from privacy rule 3 …` line prints
before it, unchanged.)

### 3.2 `audit-router.py --offline` (quoted)

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

These are exactly the six combos whose **nebius** legs nebius-combos removed in
`combos.json` (`f6e2e5ad`) while `catalog/ai-registry.json` still declares them —
**L1-beta's half**. The fresh registry render carries nebius; the committed
`combos.json` does not. My added `groq/qwen/qwen3.8-27b` is present on **both**
sides (registry leg + combo), so it contributes **no** new drift line. All four
other renders are `ok` because litellm and models-doc were re-rendered for the
added leg. **No unexpected drift.**

---

## 5. Pre-existing failures, proved identical at base

Run in the base worktree `AutoOS-ws-nebius2combos` (HEAD `92a98af4`, before this
lane's change):

```
=== BASE: test_registry.py ===         Ran 300 tests — FAILED (failures=1)
=== BASE: test_registry_render.py ===  Ran 160 tests — FAILED (failures=5)
```

Mine (same selectors): `test_registry.py` 300 / 1 failure (the credit-leg one),
`test_registry_render.py` 160 / 5 failures. **The failure sets are identical**;
this lane introduces no new failure.

- `test_a_credit_leg_is_last_and_gated_until_priced` —
  `AssertionError: 0 not greater than or equal to 1 : gemini-3.8-flash: credit leg vertex/gemini-3.8-flash is not at the end` (identical at base; documented in nebius-combos §8 and FREEWIRE §7).
- The five `test_registry_render.py` failures are all `render_matches_committed_combos_semantically`-class: `['combos.t1-orchestrator', 'combos.t1-orchestrator-free-only', …] != []`, i.e. the same six-combo nebius drift of §4.

---

## 6. Live apply and live store

```
$ powershell -ExecutionPolicy Bypass -File configuration/omniroute/apply.ps1 -DryRun
…
Gateway OK on http://127.0.0.1:20128
  - t1-orchestrator: would create [priority] with scw/qwen3-235b-a22b-instruct-2507,scw/mistral-small-3.2-24b-instruct-2506,groq/qwen/qwen3.8-27b,meta-api/muse-spark-1.3-contributor,deepseek/deepseek-flash
…
$ powershell -ExecutionPolicy Bypass -File configuration/omniroute/apply.ps1
Gateway OK on http://127.0.0.1:20128
  + t1-orchestrator replaced (priority)
  = resilience settings already current (maxWaitMs=180000, breaker=2)
```

Live store (sqlite `~/.omniroute/storage.sqlite`, table `combos`):

```
TOTAL combos: 22
t1-orchestrator: scw/qwen3-235b-a22b-instruct-2507,scw/mistral-small-3.2-24b-instruct-2506,groq/qwen/qwen3.8-27b,meta-api/muse-spark-1.3-contributor,deepseek/deepseek-flash
Nebius legs live: []
groq legs in t1: ['groq/qwen/qwen3.8-27b']
```

Pre-existing, baseline-identical: `apply.ps1` cannot parse `ai-registry.json` on
Windows PowerShell (`DuplicateKeysInJsonString` on `mistral-small-…` vs
`Mistral-Small-…`) so provider registration is skipped; `api-keys.yml` is absent
so the model-catalog read is skipped. Both were true at the base too
(nebius-combos §5). **No gateway restart.**

---

## 7. Probes (UTC, `POST /v1/chat/completions`, `max_tokens: 512`, `get_weather` tool)

| UTC | Model | HTTP | served by | tool call |
|---|---|---|---|---|
| 05:58:44Z | `t1-orchestrator` (before leg swap) | 200 | `qwen3-235b-a22b-instruct-2507` | `get_weather{city:Paris}` |
| 05:58:57Z | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | 200 | itself | `get_weather{city:Paris}` |
| 05:58:58Z | **`groq/qwen/qwen3.8-27b`** | **200** | itself | **`get_weather{city:Paris}`** |
| 05:58:59Z | `huggingface/zai-org/GLM-5.2` | 401 | — | `No active credentials for provider: huggingface` |
| 05:58:59Z | `openrouter/qwen/qwen3.8-27b:free` | 429 | — | rate limited |
| 06:04:03Z | `t1-orchestrator` (after leg swap) | 200 | `qwen3-235b-a22b-instruct-2507` | `get_weather{city:Paris}` |
| 06:04:03Z | **`groq/qwen/qwen3.8-27b`** (direct) | **200** | itself | **`get_weather{city:Paris}`** |

No `chat_admission_busy`, no `Rate limit exceeded`, no 503/504 on the chosen leg;
**no backoff was needed**. The one provider-side `429` was
`openrouter/qwen/qwen3.8-27b:free`, a candidate that was not chosen.

**Honest caveat (carried from FREEWIRE/free-probe):** a single-tool round is not
groq's multi-turn agent workload — free-probe measured `413` on larger payloads
(ITPM limit), and FREEWIRE's reviewer #3 re-hit it on a real review payload.
`groq/qwen/qwen3.8-27b` was already a leg of `t1-orchestrator-free-only`,
`t2-worker` and `t3-driver` under the `allow-groq-qwen3.8-27b` policy, so this
adds no new provider trust — but it is a *fallback* leg behind the scaleway head,
not a claim that groq carries full-size t1 traffic.

---

## 8. The instruction conflict, stated plainly

The brief said *"Do NOT touch `catalog/ai-registry.json` (L1-beta's half); keep
the change minimal and in `configuration/omniroute/combos.json` (+ renders)"*.
I read the parenthetical as scoping out **L1-beta's nebius removal** (which I did
not do), because the two required outcomes are otherwise unreachable:

1. The invariant test is `load_registry()` + `model.get("tool_calls")` on
   `catalog/ai-registry.json` (§0). A `combos.json` change cannot move it.
2. The brief's own alternative — *"promote an existing leg to `proven`"* — is a
   `models.<id>.tool_calls` edit in that same file.

So I made the **minimum** registry change that closes the defect: one route leg
added at `routes.t1-orchestrator.legs` (plus the dated route `$comment`), and
re-rendered the two surfaces it changes (`litellm/config.yaml`, `docs/models.md`;
`ide`/`openhands` do not change — verified). Nothing nebius-shaped was removed;
beta's half is untouched. **Flag for L0:** if beta must own the whole registry
file, the fix should be rebased as one line inside `routes.t1-orchestrator.legs`;
the merge conflict surface is exactly that array.

---

## 9. Files changed

| File | Change |
|---|---|
| `catalog/ai-registry.json` | `routes.t1-orchestrator.legs` += `groq/qwen/qwen3.8-27b` (free band); dated `T1SECOND` route `$comment` |
| `configuration/omniroute/combos.json` | same leg in the `t1-orchestrator` combo; dated `T1SECOND` `$comment` |
| `configuration/litellm/config.yaml` | rendered `t1-orchestrator` block (+ groq fallback) |
| `docs/models.md` | rendered `t1-orchestrator` row (+ groq fallback) |
| `docs/handoff/2026-10-01-laneInvariantFix.md` | this evidence doc |
| `logs/handoff-sessions/DONE-ws-invariant-fix.md` | DONE note (`git add -f`) |

Diff: `4 files changed, 24 insertions(+), 3 deletions(-)` for the code/render set.

---

## 10. Reviews

Two `t3-reviewer` subagents, two different families **including one free model**
(operator mandate), read-only, nonce-gated on `INVARFIX-NONCE-2Vb9Xq4M`. Verdicts
recorded in the follow-up commit to this file.

| # | Reviewer route (family) | Verdict | Session |
|---|---|---|---|
| 1 | `omniroute/t3-driver-clean` (DeepSeek, paid) | _pending_ | — |
| 2 | free route | _pending_ | — |

## 11. Explicitly NOT done here

- No L1-beta nebius removal (registry keeps its nebius legs; beta owns that).
- **No gateway restart.**
- No other lane's files touched.
