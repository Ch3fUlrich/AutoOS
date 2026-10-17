# 0006 — Routing rules live in a launch-time resolver, not in the combos; time never overrides a hard filter

Status: accepted, trimmed · 2026-09-24 · evidence: facts re-verified below (t1-orchestrator research run +
L1 spot checks against the code)

> **Written on a pre-rename base.** This record was authored against `origin/main`
> before `58a5e70` renamed the tiers. Where it says `tier1/2/3` or `-credit` it means
> the routes now named `t1-orchestrator` / `t2-worker` / `t3-driver` (and `-clean`,
> `-free-only`); the `-credit` chains were dropped on 2026-09-23. Facts below are
> re-stated in the current ids, the decision itself is unchanged.

## Context

A proposal drafted in another session ("Orchestration mandate — save Claude usage via the
OmniRoute gateway and the 3-tier protocol") asked for:

- spawning with `model: "auto"` plus a *task card*, resolved to a combo by a tested
  `select_combo(card)` in strict order `privacy > role > ctx > cost tier > tie-break`;
- provider time windows (DeepSeek off-peak billing, busy hours of US/EU pools) as a tie-break and
  as automatic deferral of work that may wait;
- a `routing-policy.json` with defaults, private routes and promos (Qoder CLI);
- renamed combo ids (`t1-orchestrator`, `t2-worker`, `t3-driver`, `t4-rag`, `agy-thinking`,
  `opus-4-6`) and `subagent_depth: 2`.

## Facts, verified 2026-09-24

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| C1 | DeepSeek bills by time since 2026-08-16: peak Mon–Fri 01:00–04:00 + 06:00–10:00 UTC, half price otherwise and all weekend | confirmed (announced 2026-08-13, effective 2026-08-16 16:00 UTC) | api-docs.deepseek.com pricing page and news 2026-08-13 |
| C2 | LiteLLM has no time-of-day routing | confirmed; **and** it has a per-deployment `order` (lowest first) | docs.litellm.ai routing, and `router.py` "ORDER FILTERING" in the installed 1.102.1 |
| C3 | OpenRouter free models: 20 req/min, 50/day, 1000/day after $10 credit | confirmed | openrouter.ai limits page |
| C4 | `provider_windows.py` `state()` raises KeyError on `kind: "promo"` | confirmed: every non-`price` kind reads `busy_utc` | agent-skills `provider_windows.py` `state()` |
| C5 | `provider-windows.json` encodes DeepSeek's windows exactly | partly: windows and days match, the "excluding Chinese public holidays" rule is missing | same directory, `provider-windows.json` |
| C6 | OmniRoute combos carry no time or privacy metadata | confirmed: `{name, strategy, models[]}` | `omniroute combo create --help` (3.8.50) |
| — | `subagent_depth: 2` enforces depth | **refuted**: opencode 2.0.16 drops the top-level key; it is `experimental.subagent_depth` | live run, this repo's CHANGELOG |
| — | the renamed combo ids exist | **refuted then, true now**: no *pushed* repo carried them on 2026-09-24; `main` renamed them on 2026-09-23 | `git ls-tree` of AutoOS and agent-skills `origin/main`; `configuration/omniroute/combos.json` |

## Decision

1. **Build the resolver as one pure function** - `select_combo(card)` in one module that the
   spawner CLI (`tools/autoos-agent.py`) and its MCP server both import, so there is exactly one
   decision point. It returns the combo plus a reason code, and every spawn logs
   `card -> combo, reason, resolver version`. Combos keep doing what they are proven at: free-first
   order and runtime failover *within* the chosen combo. The resolver never reacts to runtime 429s;
   the gateway does.
2. **Ids are a contract; change them once, in one commit.** This record said "keep
   `tier1/2/3`" because the rename had not reached the base it was written against -
   `main` had already done it on 2026-09-23 (`t1-orchestrator`, `t2-worker`,
   `t3-driver`, `t4-rag`, with `-clean` / `-free-only` suffixes; the `-credit` chains
   dropped), in exactly the shape demanded here: one migration commit updating
   `combos.json`, every client surface and both suites, with an explicit retired-ids
   regression test. No alias table, then or now. A future rename follows the same rule.
3. **Card v1** (unknown fields are an error):

   | Field | Values | Default |
   |---|---|---|
   | `role` | orchestrate, implement, review | implement |
   | `complexity` | trivial, standard, hard | standard |
   | `ctx` | 128k, 1m | 128k |
   | `privacy` | public, sensitive | public |
   | `spend` | free-ok, credit | free-ok |

   Resolution is **filters, then preference** - not a sort:
   1. privacy filter, 2. ctx filter, 3. spend filter, 4. role/complexity preference.

   | privacy | ctx | role / complexity | spend | → |
   |---|---|---|---|---|
   | public | 1m | orchestrate, or hard | any | `t1-orchestrator` |
   | public | 128k | implement / standard | free-ok | `t2-worker` (empty card) |
   | public | 128k | review, or trivial | free-ok | `t3-driver` |
   | public | 128k | implement or review | credit | no dedicated combo: the `-credit` chains were dropped 2026-09-23, `t2-worker` / `t3-driver` already overflow to the paid legs |
   | sensitive | 128k | implement / standard / hard | any | `t2-worker-clean` |
   | sensitive | 128k | review, or trivial | any | `t3-driver-clean` |
   | sensitive | 1m | any | any | **no route**: see 4 |

   One test per row, plus one per unknown-field and per boundary case.
4. **Privacy is a hard filter, and there is no 1M private leg.** `t1-orchestrator-clean` is paid-only but
   its only leg is a *contributor* model that trains on prompts (operator block 2026-09-21,
   recorded in `configuration/omniroute/combos.json` and `docs/models.md`). So
   `sensitive + 1m` fails closed **with next steps in the message**: split the work to fit
   `t2-worker-clean` (128k), or pass an explicit `--allow-training` override, which is logged. It never
   silently picks a training leg.
5. **Time never overrides a hard filter.** Windows are real (C1, C3), but they are not in v1. When
   they come (v2: `deferrable`, `deadline`), they may only (a) order candidates that already passed
   every filter and share a *static* cost class (`free-ok` vs `credit`, evaluated before any
   off-peak discount), and (b) delay work that the caller explicitly marked `deferrable`. Blocking
   work is never delayed for price. Every window lookup is wrapped: an exception yields "no
   tie-break", and a test proves that a throwing `provider_windows` still resolves privacy and ctx
   correctly. Prerequisite: `provider_windows.py` handles unknown kinds with a warning (C4), test
   first. The missing Chinese-holiday rule (C5) is a documented limitation - it can misorder two
   equal options by at most the 50% off-peak difference - not a blocker.
6. **No `routing-policy.json` yet.** Defaults live in one `routing_defaults` module with a
   `ROUTING_VERSION` that every log line carries. A promo (the Qoder CLI) is a *client adapter*,
   and `select_combo` may return it **only** for `privacy: public`; its last probe date lives in
   the spawner's state and is stale after 7 days.
7. **LiteLLM fallback gets ordered legs**: `order` per deployment written by
   `tools/sync-router-tiers.py`, one value **per cost class** (free, partner, paid) rather than
   one per leg, so LiteLLM still spreads load across equal free legs and only steps to the next
   class when a class is cooled down (C2). This replaces the random pick across paid and free legs.
8. **Depth**: the dropped `subagent_depth` is `experimental.subagent_depth` for opencode; for
   every other client the spawner carries a depth budget (`AUTOOS_AGENT_DEPTH`) and refuses a
   spawn past the maximum.

## Known unverified facts

- The `-clean` legs (DeepSeek paid, Mistral paid, Zen paid) are "no free tier, operator-
  sanctioned", not "verified no-training". Their providers' data-retention terms are not yet
  linked here; until they are, `-clean` means *paid, not free-tier*, and the docs must say so.

## Answers to the proposal's open questions

- **Q1 promo staleness:** 7 days, re-probed before a batch.
- **Q2 deferral:** opt-in per spawn (`deferrable: true`), never global.
- **Q3 `-clean` for all review traffic:** no; only for sensitive content (cost).
- **Q4 private implementer:** `t2-worker-clean` (DeepSeek paid first) by default; local Ollama only
  on explicit opt-in (it is not installed here, and this host has no RAM to spare).

## Review record

- Writer: Claude (L1). Reviewer: `t1-orchestrator` (`meta/muse-spark-1.3-contributor`, cross-family):
  SHIP-WITH-FIXES, 12 findings; all accepted except that LiteLLM ordering is refined (7) instead of
  replaced by shuffle - concentrating on the free class is the point of free-first.

## Consequences

- One tested function decides the model; the agent's whole routing interface is "spawn with a card".
- Filters run before, and without, any time-window code, so a wrong or failing window can only
  misorder equal options.
- The work is split: the resolver and card go to the spawner lane; the LiteLLM `order` change to
  the router lane; the `provider_windows` fixes to agent-skills, test-first.
