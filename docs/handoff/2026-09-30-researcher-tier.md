# Researcher Tier: t4-researcher read-only wide-context route

**Date:** 2026-09-30
**Branch:** `L1-backlog/ws_researcher-20260930`
**Status:** Proposal — definitions added, not wired into default routing

## What was built

A new read-only, lean researcher/reviewer variant with a wide-context head and
small output budget, available as a combo but not yet routed by `select_combo`
or the v2 resolver. It can be selected manually via `--combo t4-researcher`.

### Route: t4-researcher (`catalog/ai-registry.json`)

| Field | Value |
|---|---|
| Class | free |
| Strategy | priority |
| Context | 131,072 (declared 128k — clamped to free\_ai/qwen7b's cap) |
| Output | 8,192 |
| Surfaces | litellm + omniroute (clients: opencode, zed) |
| OpenHands profile | none (opencode+zed only — avoids tier-profile membership churn) |

**Legs** (all free, all distinct providers):

1. `gemini/gemini-3.8-flash` — wide 1M context head
2. `scaleway/qwen3-235b-a22b-instruct-2507`
3. `nebius/zai-org/GLM-5.3-Flash`
4. `free_ai/qwen7b` — stopgap last leg

Mirrors `t3-driver-free-only` (minus dead groq/cerebras heads), leading with
gemini for wide context. Output budget 8,192 (vs 16,384 for t3-driver-free-only)
— research reports are short, and this saves the free-ai 30k/day allowance.

### Combo: t4-researcher (`configuration/omniroute/combos.json`)

```json
"t4-researcher": {
  "strategy": "priority",
  "context": "128k",
  "models": [
    "gemini/gemini-3.8-flash",
    "scw/qwen3-235b-a22b-instruct-2507",
    "nebius/zai-org/GLM-5.3-Flash",
    "free-ai/qwen7b"
  ]
}
```

### Harness role: leaf-researcher (`catalog/agent-harness.json`)

| Field | Value |
|---|---|
| spawn | false |
| leaf | true |
| opencode.mode | subagent |
| opencode.model | deepseek/deepseek-flash (template only — opencode.jsonc is hand-written) |
| openhands.profile | researcher (unique — different from leaf-reviewer's "worker") |
| openhands.llm\_profile\_ref | deepseek-flash (existing LLM profile) |
| mcp | serena, graphify |

Follows the same leaf contract as leaf-reviewer: `spawn: false`, `leaf: true`,
`mcp` subset of `{serena, graphify}`.

### OpenHands profile (`openhands/agent-profiles/researcher.json`)

Vendored by `python3 lib/agent_harness.py vendor`. Unique profile name
"researcher" (distinct from leaf-reviewer's "worker"). `enable_sub_agents:
false`, `tool_concurrency_limit: 1` — leaf constraints.

### opencode.jsonc agent block

Hand-written `t4-researcher` agent block, copying t3-reviewer's entire
read-only permissions fence verbatim (~150 lines). Only two fields differ:

- `description`: "Read-only researcher: wide-context exploration and
  fact-finding. Reads and runs checks only; never edits, commits or spawns.
  May not launch subagents."
- `model`: `omniroute/t4-researcher`

NOT wired into any spawner's allow rules — the agent exists but no spawner can
launch it yet. This is the proposal state.

## Files changed

| File | Change |
|---|---|
| `catalog/ai-registry.json` | Added t4-researcher route (+33 lines) |
| `configuration/omniroute/combos.json` | Added t4-researcher combo (+11 lines) |
| `catalog/agent-harness.json` | Added leaf-researcher role (+17 lines) |
| `opencode.jsonc` | Added t4-researcher agent block + model list update (+175 lines) |
| `openhands/agent-profiles/researcher.json` | New vendored OpenHands profile (untracked) |
| `catalog/ide-models.json` | t4-researcher entry (rendered by `registry.py render ide`) |
| `configuration/litellm/config.yaml` | t4-researcher LiteLLM block (rendered by `sync-router-tiers.py`) |
| `configuration/openhands/tier-profiles.json` | No change (t4-researcher not served to OpenHands) |
| `configuration/openhands/config.toml` | No change (t4-researcher not served to OpenHands) |
| `docs/models.md` | t4-researcher row in AUTOOS-MANAGED models-doc table |
| `tools/registry.py` | t4-researcher added to IDE\_MODEL\_ORDER tuple (+1 line) |
| `tests/fixtures/agent-harness/opencode.expected.json` | leaf-researcher fixture entry (+66 lines) |

## Validation

All render/sync checks and test suites pass:

| Check | Result |
|---|---|
| `registry.py check` | ok: 26 routes, 71 models, 33 providers |
| `registry.py render omniroute --check` | ok: combos.json matches render |
| `registry.py render ide --check` | ok: ide-models.json matches render |
| `registry.py render openhands --check` | ok: tier-profiles.json matches render |
| `registry.py render litellm --check` | ok: config.yaml matches render |
| `registry.py render models-doc --check` | ok: docs/models.md matches render |
| `sync-ide-models.py --check` | ok: opencode.jsonc, tier-profiles, config.toml match |
| `sync-router-tiers.py --check` | ok: all 14 managed tiers match (incl. t4-researcher) |
| `audit-router.py --offline` | no drift (16 combos incl. t4-researcher) |
| `agent_harness.py vendor --check` | ok |
| `tests/test_agent_harness.py` | 71 pass, 1 pre-existing fail (symlink/O\_NOFOLLOW on Windows), 4 skipped |
| `tests/test_sync_router_tiers_registry.py` | 17 pass |
| `tests/test_sync_ide_models.py` | 36 pass |
| `tests/test_autoos_resolver.py` | 292 pass |
| `tests/test_audit_router_registry.py` | 7 pass |
| `tests/test_registry_render.py` | 160 pass |

The one failure (`test_a_symlink_that_appears_after_the_check_leaves_no_backup`)
is pre-existing on main — it is a Windows-specific `O_NOFOLLOW` behaviour issue
unrelated to this change.

## Routing recommendation: do NOT wire into select\_combo

### How routing works today

**v1 `select_combo`** (`tools/autoos_routing.py`):
```
light = not strong and (role == "review" or complexity == "trivial")
```
Light cards route to `t3-driver`; non-light to `t2-worker`; strong (orchestrate/
hard) to `t1-orchestrator`. There is no `research` discrimination — a research
card is "light" only if it is also review or trivial.

**v2 resolver** (`tools/autoos_resolver.py`):
`kind=research` scores 0 points — the same as `kind=implement`, `kind=review`,
`kind=bulk`, `kind=final`. Only `kind=plan` (3 pts) and `kind=debug` (2 pts)
score higher. Research cards are bucketed by total score (files, modules,
fanout, lines, spec, tests) into S0–S4, the same as any other card.
`READ_ONLY_CARD_KINDS = ("review", "plan", "research")` already recognises
research as read-only — no edit permissions needed.

### Why not to wire t4-researcher as the default for research/review

1. **Context clamp negates the headline feature.** The combo declares 128k
   because `free_ai/qwen7b` caps at 128k, even though the gemini head has 1M.
   The wide-context advantage is lost in the combo clamp.

2. **Fallback resilience is poor.** t4-researcher has 4 legs; t3-driver has
   13+. If the first three free legs are down, the researcher falls back to a
   7B model — inadequate for research quality.

3. **Class "free" < class "cheap".** t3-driver (cheap) is higher quality than
   t4-researcher (free). Using the free route as the default for research/review
   would degrade quality.

4. **No card-kind discrimination.** `select_combo` treats `review` and
   `trivial` as "light" but has no path for `research`. Adding one would
   require new card-kind logic in the v1 router.

5. **Proposal state.** The route should be validated by manual use
   (`--combo t4-researcher`) before changing default routing.

### When to reconsider

Wire t4-researcher into routing when:
- A free-only tier is needed for research/review work (budget constraints)
- The free\_ai/qwen7b leg is replaced or the context clamp is relaxed
- The route has been validated in production via manual selection

Until then, it is available on request: `--combo t4-researcher`.
