# Orchestration update: context firewall + model economics

**Date:** 2026-07-30
**Status:** design approved, not yet implemented
**Scope:** `skills/swarm-orchestration`, `skills/qa-swarm`, new `.claude/` adapter, `custom_orchestration/` fixes

## 1. Why

Two articles, read 2026-07-30:

- **Cursor, *Agent Swarms and Model Economics*.** Identical task completion varied ~8× in cost
  ($10,565 frontier-solo vs $1,339 frontier-planner + cheap-worker). Workers consume 69–90% of
  tokens but a minority of cost; planners are the inverse. "Few moments in a large task genuinely
  require frontier intelligence" — decomposition, design decisions, trade-offs. Scaling comes from
  *context efficiency*, not parallelism. Review is cheaper than the work it audits.
- **Rahul Garg (Thoughtworks), *The Orchestrator's Tax*.** The cost of subagents is **context
  pollution**, not tokens: "tokens are spent once; context shapes every decision that follows."
  Four subagents returning full JSONL transcripts injected tens of thousands of tokens into the
  orchestrator's permanent working memory. Codified rules: 2–4 agents per wave, never poll full
  transcripts, no repo-wide git during concurrent work, overlapping file ownership is a
  consolidation signal.

This repo is already aligned on four of their conclusions and should not re-litigate them:
contract-first handoff with SHA-256 drift detection (Fowler's "duplicated orientation" fix),
different-model-family reviewer, `architect=flagship_reasoning` + `engineer=strong_mid_tier_coder`
(Cursor's planner/worker split), and worktree-per-engineer + TTL locks (stronger than Fowler's
file-ownership heuristic).

**The gap is not the policy. The gap is that nothing enforces it.** The live orchestration surface
is Claude Code's native `Agent`/`Workflow` tooling, and this repo ships zero `.claude/agents/`,
zero `.claude/workflows/`, and zero hooks. `skills/*/agents/*.yaml` are OpenAI Codex manifests, not
Claude subagent definitions. The Python scaffold has only ever run in mock mode
(`.agent-state/worktrees/mock-engineer-001`, `mock_test_dir`).

## 2. Verified defects

Each was confirmed by reading the code, not inferred.

| # | Defect | Evidence |
|---|---|---|
| **D1** | Unknown provider aborts the entire fallback chain. `adapter_for()` is the first statement in the `for` loop body, **outside** the `try:` that begins at `execution_mode = ...`. `ProviderRegistry.get()` raises `KeyError` on an unknown name, so iteration 0 propagates out and candidates 1–3 are never tried. | `provider_executor.py:95` vs `:124`; `providers/registry.py:28` |
| **D2** | `ReviewerPanel.execute_swarm` passes `preferred_provider=details.get("model_tier")` — i.e. the string `"sonnet-3.5"` into a parameter whose domain is provider ids (`claude_code`, `codex`, …). Via D1 this raises `KeyError: Unknown provider: sonnet-3.5` for **every** perspective. | `orchestrator_scaffold.py:352` |
| **D3** | `execute_swarm` returns perspective envelopes `{"perspective", "result"\|"error"}`, but `calculate_verdict` reads `finding.get("severity", "low")`. No envelope has a `severity`, so every finding counts as `low`. | `orchestrator_scaffold.py:367` vs `:386` |
| | **Net effect of D1–D3: swarm review fails *open*.** It returns `APPROVE` unconditionally, and because `narration.mode: silent` returns `None` for `APPROVE`, it does so without emitting anything. A safety gate that cannot fail closed. | `orchestrator_scaffold.py:436` |
| **D4** | The verdict matrix exists in **three** places: `qa-swarm/SKILL.md` §2 (prose), `agent_orchestration.config.yaml` → `triage_policy.verdict_matrix` (**read by no code**), and `TriagePipeline.calculate_verdict` (hardcoded Python — the only executable copy). | grep: only `swarm_review` and `triage_policy.human_participation_gate` are consumed |
| **D4a** | The declared matrix has **overlapping rules with no defined precedence**. At 1 HIGH + 2 MEDIUM both `request_changes` (`1 HIGH + 2 MEDIUM`) and `approve_with_nits` (`1 HIGH`) match. Markdown resolves by reading order; a YAML *mapping* has no order at all. | `agent_orchestration.config.yaml:210-222` |
| **D4b** | The declared matrix has a **hole**. With 0 CRITICAL, 0 HIGH, 1–2 MEDIUM: `>= 3 MEDIUM` fails and `only_low_or_nit` is false. No rule fires. Only the Python copy fills it, with `APPROVE`. | same |
| **D5** | Risk thresholds disagree **three ways**. `SKILL.md` (pre-2026-07-30) said "sum of the weighted trigger signals", bands `<0.60 / 0.60–0.85 / >=0.85`. The YAML says `low: 0.30, medium: 0.60, high: 0.85`. `RiskEngine.score()` returns `total / weight_sum` (Σweights = 1.50) — a *mean*, not a sum. At score 0.70 the docs say "1 engineer + strict review" and the code says Best-of-3: a 3× cost swing. Under normalization, `very_high` needs ~10 of 11 signals, so Best-of-5 is unreachable. | `SKILL.md` §7, `config.yaml:66-82`, `orchestrator_scaffold.py:241` |
| **D6** | `RiskSignals` fields are bare floats with no documented encoding, and the only example sets them all to `0.0`. `value * weight` silently squares the weight if a caller passes the weight instead of an indicator. | `orchestrator_scaffold.py:19-30`, `examples/run_orchestrator.py:53` |
| **D7** | `finalize_task_state()` calls `git.prune_worktrees(dry_run=False)` — repo-wide — and runs **per agent**. Exactly the operation Fowler banned after his incident. | `orchestrator_scaffold.py:576` |
| **D8** | Best-of-N is computed by `RiskEngine.execution_mode()` but never executed. (Swarm review *is* genuinely parallel via `ThreadPoolExecutor`; Best-of-N is the branch with no runtime.) | `orchestrator_scaffold.py:262`, [ADR 0005](../../decisions/0005-openhands-as-provider-adapter.md) §1 |

D2 and D4 share one root cause and it is the thing to fix structurally: **a fact with more than one
home, and a parameter with more than one domain.**

## 3. Ownership matrix — single source of responsibility

The design rule for every fact below: **one owner, everything else derives or references.** This is
Principle 1 of `coding-principles`, including its red-flag row *"I'll keep the README and the code in
sync by hand" → You won't. Generate or reference.*

| Fact | Single owner | How everything else gets it |
|---|---|---|
| Role duties, must / must-not, orchestration flow | `skills/swarm-orchestration/SKILL.md` | `.claude/agents/*.md` bodies **point at** the section; never restate |
| Model tier per role and per review lens | `config.yaml` → `model_routing.<role>.preferred_tier` | `.claude/agents/*.md` `model:` frontmatter is **generated**; the Python scaffold reads the same key at runtime |
| Tool / MCP allowlist per role | `config.yaml` → `roles.<role>.allowed_mcps` | `.claude/agents/*.md` `tools:` is **generated** via one mapping table in the generator |
| Review lenses and their focus | `config.yaml` → `swarm_review.perspectives` | both `.claude/workflows/qa-swarm.js` **and** the three `reviewer-*` agent files are **generated** from it — one lens list, not two |
| Severity vocabulary | `config.yaml` → `triage_policy.severities` (new) | generated into JS; read at runtime by Python |
| Verdict matrix | `config.yaml` → `triage_policy.verdict_matrix` (converted to an **ordered sequence** with **structured** conditions) | `TriagePipeline` **reads it at runtime**; JS is **generated**; `qa-swarm/SKILL.md` links to it |
| Wave caps | `config.yaml` → `orchestration.wave_caps` (new) | asserted in generated JS; documented by reference in `SKILL.md` |
| Risk weights and thresholds | `config.yaml` → `risk` | `RiskEngine` reads at runtime; `SKILL.md` links, does not tabulate |
| Repo-wide git deny list | `config.yaml` → `orchestration.forbidden_concurrent_git` (new) | `.claude/hooks/block-repo-wide-git.py` reads it |

Two consequences worth stating plainly:

1. **`config.yaml` becomes the single runtime source, and the `.claude/` adapter is generated from
   it.** Workflow scripts have no filesystem or `require`, so they cannot read the YAML at runtime —
   generation is the only way to keep one source. Generated files carry a `DO NOT EDIT` header naming
   their source, and the generator has a `--check` mode wired into pre-commit so drift fails loudly
   instead of rotting.
2. **`triage_policy.verdict_matrix` changes shape from a mapping to an ordered sequence, and its
   conditions become structured instead of English strings.** This is not cosmetic: the mapping's
   lack of order *is* D4a, and structured conditions are what let one rule set drive both Python and
   generated JS without a mini expression parser.

## 4. Design

### 4.1 Generator

`skills/swarm-orchestration/tools/gen_claude_adapter.py`

- Input: `custom_orchestration/agent_orchestration.config.yaml` + `SKILL.md` anchor names.
- Output: `.claude/agents/*.md`, `.claude/workflows/qa-swarm.js`, `.claude/workflows/best-of-n.js`.
- `--check` exits non-zero on drift, printing a unified diff. Wired into pre-commit.
- Holds exactly one non-derived fact: the MCP-name → Claude-tool-name mapping
  (`serena` → `mcp__serena__*`, `context7` → `mcp__context7__*`, plus base `Read`/`Grep`/`Glob`, and
  `Edit`/`Write` only where `roles.<role>.can_write_code` is true).

### 4.2 Role subagent definitions

Five generated files in `.claude/agents/`. The two orchestration roles come from `roles`; the three
reviewer lenses are **derived from `swarm_review.perspectives`**, so adding or removing a lens is a
one-line config change that regenerates both the agent files and the workflow — there is no second
list to keep in step.

| Agent | `model:` | Rationale |
|---|---|---|
| `architect` | `opus` | Owns decomposition and trade-offs. Cursor: planners are a small token fraction but ~66% of cost — the correct place to spend. `can_write_code: false` ⇒ no `Edit`/`Write` in `tools:`. |
| `engineer` | `sonnet` | 69–90% of tokens flow here; the hybrid matched frontier-solo completion at ~87% less cost. |
| `reviewer-security` | `sonnet` | Vulnerabilities, auth bypass, injection. |
| `reviewer-performance` | `sonnet` | N+1, memory, algorithmic complexity. |
| `reviewer-xp` | `haiku` | Naming, readability, DRY — a mechanical lens; cheapest tier suffices. |

Tier **aliases**, not pinned model ids, so this cannot rot the way `sonnet-3.5` / `haiku-3` did.

**Honest limitation, documented rather than papered over.** `config.yaml` → `model_routing.reviewer`
specifies `preferred_tier: different_family_from_engineer`. Claude Code's `Agent` tool spawns only
Claude, so native orchestration *cannot* deliver family diversity — `fable` is a different model but
the same family, sharing training-derived blind spots. The requirement splits in two:

- **Native** review gets **lens diversity** (three perspectives, differing tiers).
- **Family diversity is a Herdr-only capability**, which `herdr-orchestration` §1 already names as
  one of four things native tooling cannot do.

`SKILL.md` is corrected to say this instead of asserting a property the mechanism cannot provide.

### 4.3 `.claude/workflows/qa-swarm.js`

The load-bearing mechanism: `agent(prompt, {schema})` forces the subagent through a
`StructuredOutput` tool call, so it **physically cannot** return a transcript, intermediate
reasoning, or status prose. Fowler's tax is removed by construction rather than by discipline —
which matters, because his incident was a disciplined engineer forgetting once.

- `pipeline()` over the lenses, not `parallel()`: no barrier, so security findings begin verifying
  while the performance lens is still reading.
- Findings schema: `{severity, file, line, summary, failure_scenario}` with `severity` drawn from
  `triage_policy.severities`.
- Stage 2 adversarially verifies **only CRITICAL and HIGH**. Not scope creep: `SKILL.md` §1
  (@reviewer, "Must not") already forbids approving on agent claims alone, and gating to the top two
  severities bounds the cost while honouring "review is cheaper than the work it audits."
- Verdict computed in generated JS from the ordered matrix — zero tokens, cannot drift.
- One `log()` line per wave: `budget.spent()` delta plus the model each lens ran on. This is the
  whole telemetry slice — no omnigraph schema change, no hook, no cluster work.

### 4.4 `.claude/workflows/best-of-n.js`

Fixes D8. `parallel()` with `isolation: 'worktree'` per candidate, replacing the scaffold's manual
`GitManager` juggling with harness-managed isolation. The barrier is genuinely correct here —
arbitration needs all N candidates at once to rank them. Arbitration runs in JS against the existing
`arbitration_order`, verification pass rate first, with no LLM involvement unless verification ties.

### 4.5 Wave caps — two-tier, in `orchestration.wave_caps`

Fowler's 2–4 rule is about **context pollution**: his agents returned transcripts into the
orchestrator's permanent working memory. A Best-of-N candidate in an isolated worktree returning a
schema'd result does not incur that tax at all. The binding constraint differs by wave kind, so a
single blanket number would be wrong in both directions.

| Wave kind | Cap | What binds it |
|---|---|---|
| **Context-sharing** — output enters orchestrator reasoning (review lenses, exploration, orientation) | **4**, consolidate at 5+ | Fowler: attention, not throughput |
| **Isolated candidate** — worktree + schema + deterministic arbitration (Best-of-N) | **5**, gated on the existing `only_if_objective_arbitration_available` | Cursor: worker-tier cost, not context |

Enforcement is a generated assertion inside each workflow (`if (LENSES.length > cap) throw`) —
deterministic and stateless. A hook is deliberately *not* used: counting in-flight agents needs
shared mutable state incremented on `PreToolUse` and decremented on `PostToolUse`, which races across
concurrent tool calls and leaks whenever an agent dies.

### 4.6 Hook — `.claude/hooks/block-repo-wide-git.py`

`PreToolUse` on `Agent`. Fowler's third rule *is* statelessly checkable: regex the outgoing subagent
prompt against `orchestration.forbidden_concurrent_git` (`git worktree prune`, `git gc`,
`git reset --hard`, `git clean -fd`, `git checkout .`, `git stash`) and block on match. This is the
one rule where a hook is the right mechanism, and it guards the same hazard as D7.

Python, not shell, for Windows/Linux parity. `.gitattributes` already pins `*.py` to `eol=lf`.

### 4.7 Scaffold fixes

| Defect | Fix |
|---|---|
| D1 | Move `adapter_for()` **inside** the `try:` so an unknown provider records `last_error` and advances to the next candidate, restoring the 3-deep fallback design. |
| D2 | Delete `model_tier` from `swarm_review.perspectives` entirely — it is the duplicate home of a fact `model_routing` already owns (§3). `ReviewerPanel` stops passing a preferred *provider* and passes `preferred_model` resolved from `model_routing.<lens>.preferred_tier`. One domain per parameter: `preferred_provider` takes provider ids only. |
| D3 | `execute_swarm` returns a flat list of findings, each tagged with its originating perspective, matching what `calculate_verdict` consumes. Add an explicit contract comment naming the shape. |
| D4 | `TriagePipeline.calculate_verdict` reads the ordered matrix from config instead of hardcoding it. Delete the Python copy. `qa-swarm/SKILL.md` §2 links to the config instead of restating the rules. |
| D4a | Matrix becomes an ordered sequence; first match wins, and that is stated in the config comment and honoured by both the Python evaluator and the generated JS. |
| D4b | Add an explicit terminal fall-through. **Deliberate behaviour change:** the fall-through becomes `approve_with_nits`, not the current implicit `APPROVE`. Rationale: 1–2 MEDIUM findings currently vanish, because `APPROVE` under `narration.mode: silent` emits nothing. Covered by a dedicated test so the change is visible rather than incidental. |
| D5 | The **YAML becomes canonical** — thresholds `0.30 / 0.60 / 0.85` — and `/ weight_sum` is deleted from `RiskEngine.score()`, making it the raw weighted sum the docs already describe. On a raw sum those bands map to intuitive signal counts: ~2 signals → medium, ~4 → high/Best-of-3, ~6 → very_high. `SKILL.md` drops its threshold table and links to the config. Documented consequence: a raw sum means **adding a 12th signal shifts every band**, so "re-tune thresholds when adding a signal" becomes an explicit note beside the weights. |
| D6 | Document `RiskSignals` fields as 0.0/1.0 indicators, and assert it in `score()` so passing a weight fails loudly instead of squaring silently. |
| D7 | Move `prune_worktrees` out of the per-agent `finalize_task_state` into an architect-only teardown that first asserts no other agent holds a lock or worktree. |
| D8 | `RiskEngine.execution_mode()` keeps computing the mode; execution moves to `.claude/workflows/best-of-n.js`. `SKILL.md` §16 currently documents hand-execution as the interim path — that paragraph is replaced by a pointer to the workflow once it lands. |

### 4.8 Repository plumbing

- `.gitignore`: ignore `.claude/settings.local.json`; **track** `.claude/settings.json`,
  `.claude/agents/`, `.claude/workflows/`, `.claude/hooks/`. Nothing under `.claude/` is tracked
  today, so the hook and agents would otherwise be invisible to every other machine.
- `.gitattributes`: add `*.js text eol=lf`. No rule exists for `.js` today.
- `docs/agent-compatibility.md`: add a row for the Claude adapter alongside Codex and Gemini.

### 4.9 Consolidated `agent_orchestration.config.yaml` changes

Every schema change in one place, so the generator and the scaffold have an unambiguous contract.

| Key | Change | Why |
|---|---|---|
| `triage_policy.verdict_matrix` | mapping → **ordered sequence**; each entry `{verdict, when \| any_of, action}` with **structured** conditions (`{high: ">=2"}`) instead of English strings; add a terminal fall-through entry | D4, D4a, D4b — and structured conditions are what let one rule set drive both Python and generated JS without an expression parser |
| `triage_policy.severities` | **new** — ordered list `[CRITICAL, HIGH, MEDIUM, LOW, NIT]` | severity vocabulary currently lives only in `qa-swarm/SKILL.md` prose and a Python dict literal |
| `swarm_review.perspectives.*.model_tier` | **removed** | D2 — duplicate home for a fact `model_routing` owns |
| `model_routing` | add one entry per review lens (`security_auditor`, `performance_expert`, `xp_reviewer`), each with `preferred_tier` | so lens tiers have the same single home as role tiers |
| `orchestration.wave_caps` | **new** — `{context_sharing: 4, isolated_candidate: 5}` | §4.5; asserted in generated JS |
| `orchestration.forbidden_concurrent_git` | **new** — regex list | §4.6; read by the hook |
| `risk.thresholds` | unchanged values (`0.30 / 0.60 / 0.85`), now **canonical**; add a comment that they are raw-sum bands and must be re-tuned if a 12th signal is added | D5 |
| `best_of_n.default_candidates.very_high` | `5` retained, now reachable and bounded by `wave_caps.isolated_candidate` | D5, §4.5 |

## 5. Testing

Per Principle 2 (rigid TDD) — failing test first, in every case below.

| Unit | Tests |
|---|---|
| `RiskEngine.score` | raw-sum arithmetic; the 0.30/0.60/0.85 band boundaries; a weight-instead-of-indicator input raises |
| `TriagePipeline.calculate_verdict` | table test over the matrix, **including** 1 HIGH + 2 MEDIUM (precedence, D4a) and 0 CRITICAL / 0 HIGH / 2 MEDIUM (fall-through, D4b) |
| `ProviderExecutor._try_selection` | an unknown provider at index 0 falls through to index 1 and succeeds (D1) |
| `ReviewerPanel.execute_swarm` | returns flat findings carrying `severity` and `perspective`; a lens timeout yields a partial-failure record and does **not** silently become `APPROVE` (D2, D3) |
| `gen_claude_adapter.py` | `--check` fails on a hand-edited generated file; regeneration is byte-stable |
| Hook | a prompt containing `git worktree prune` is blocked; a benign `git status` prompt passes |
| Workflows | run against a trivial diff; assert the logged verdict and that the wave cost line is emitted |

## 6. Out of scope — deliberately

Not adopting from Cursor: the **reconciler agent**, **megafile decomposer**, **Field Guide
artifact**, or **custom VCS**. Those solve coordination failures at ~1,000 commits/second across
dozens of concurrent planners. At 2–5 agents the existing worktree + TTL-lock model already prevents
the collisions they reconcile, and omnigraph is a better-structured Field Guide than a line-budgeted
text file. YAGNI.

Not adopting from the telemetry option: **omnigraph run-telemetry**. The schema has
`Project/Decision/Rule/Preference/Convention/Component/Task` and no node type for a metered run, so
this needs a cluster schema change plus `apply-cluster.sh` against the live server per
`as-rule-declared-is-not-live`. Deferred to its own ADR.

Unchanged: contract-first handoff, worktrees + TTL locks, safety gates, the seven provider adapters,
and Herdr's role.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Generated files edited by hand and silently diverge | `--check` in pre-commit, `DO NOT EDIT` header naming the source |
| `.claude/` adapter drifts from the portable markdown | markdown stays canonical; the adapter is generated from the config the markdown links to |
| D4b fall-through change alters existing verdicts | explicit test; called out in CHANGELOG and ADR as a behaviour change, not a refactor |
| Raw-sum thresholds make Best-of-3 fire more often than before | intended — it was previously unreachable; wave-cap and worker-tier economics bound the cost |

## 8. Backtracking artifacts

Per Principle 5: a `CHANGELOG.md` entry, **ADR 0004** recording the executable-policy decision and
the `different_family` split, and at session end the Decision plus new Rules persisted to the
`agent-skills` omnigraph graph (never `memory`).
