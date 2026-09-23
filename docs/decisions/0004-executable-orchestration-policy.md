# 0004. Orchestration policy must be executable, and the config owns every number

- **Status:** Accepted (2026-07-30) — design approved, **implementation pending**
- **Spec:** [`docs/superpowers/specs/2026-07-30-orchestration-context-and-economics-design.md`](../superpowers/specs/2026-07-30-orchestration-context-and-economics-design.md)
- **Amends:** nothing. Extends the orchestration skill set introduced alongside
  [ADR 0002](0002-herdr-multiplexer.md).

## Context

Two articles, read 2026-07-30, prompted an audit of this repository's orchestration:

- **Cursor, *Agent Swarms and Model Economics*** — identical task completion varied ~8× in cost
  depending only on which model played planner and which played worker. Workers consume 69–90% of
  tokens but a minority of cost; planners invert that. Scaling comes from *context efficiency*, not
  parallelism. Review is cheaper than the work it audits.
- **Rahul Garg (Thoughtworks), *The Orchestrator's Tax*** — the real cost of subagents is **context
  pollution**, not tokens: "tokens are spent once; context shapes every decision that follows."
  Prefer 2–4 agents per wave, never poll full transcripts, no repo-wide git during concurrent work,
  and treat overlapping file ownership as a consolidation signal.

The audit found the repository was already aligned on four of their conclusions — contract-first
handoff with hash-based drift detection, a different-family reviewer, frontier-planner plus
mid-tier-worker routing, and worktree-per-engineer isolation. **The policy was not the problem.**

Two problems were:

1. **Nothing enforced the policy on the surface actually being driven.** Day-to-day orchestration
   runs through Claude Code's native `Agent`/`Workflow` tooling, and the repository shipped zero
   `.claude/agents/`, zero `.claude/workflows/`, and zero hooks. The `skills/*/agents/*.yaml` files
   are OpenAI Codex manifests, not Claude subagent definitions. The Python scaffold — the only thing
   that *did* encode the policy — had never run outside stub mode.
2. **Several facts had more than one home, and they had already drifted.** Risk thresholds
   disagreed three ways between `SKILL.md`, the YAML, and `RiskEngine.score()`. The verdict matrix
   existed in three places, with the YAML copy read by no code at all. Reviewer model tiers were
   pinned to model ids two generations stale. Most seriously, a model tier was being passed into a
   parameter whose domain is provider ids, which — combined with an `adapter_for()` call sitting
   outside its retry block — made **swarm review fail open**, returning `APPROVE` unconditionally and
   silently.

The full defect table, with file and line evidence for each, is in the spec.

## Decision

**1. Policy is declared in Markdown, enforced by generated artifacts.**
`skills/swarm-orchestration/SKILL.md` remains the portable, normative source of truth. A generated
`.claude/` adapter binds it on the native surface: role subagents carrying `model:` tiers, workflows
carrying return schemas, and a `PreToolUse` hook blocking repo-wide git in subagent prompts. This
follows the existing repository rule — give each agent its native instruction file, keep adapters
short — rather than introducing a new pattern.

**2. `agent_orchestration.config.yaml` is the single runtime source, and the adapter is generated
from it.** Workflow scripts have no filesystem access and cannot read YAML at runtime, so generation
is the only mechanism that keeps one source. A `--check` mode gates drift in pre-commit. `SKILL.md`
holds **no numbers**; if a threshold appears there it is a bug.

**3. A response schema is the context firewall.** Where the surface supports one, subagents return
structured findings through it. A schema'd agent *cannot* return a transcript, so context hygiene
stops depending on anyone remembering to be disciplined — which matters, because Garg's incident was
a disciplined engineer forgetting once.

**4. Wave caps are two-tier, because two different things bind them.** Garg's 2–4 rule is about
*attention*: his agents returned transcripts into permanent working memory. A Best-of-N candidate in
an isolated worktree returning a schema'd result incurs none of that tax, so what bounds it is
worker-tier *cost*. A single blanket number would be wrong in both directions — too loose for
context-sharing waves, needlessly tight for isolated ones.

**5. `different_family_from_engineer` splits by surface instead of being asserted globally.** Claude
Code spawns only Claude, so native review gets **lens diversity** and nothing more; genuine family
diversity is a `herdr-orchestration` capability. Claiming otherwise produced a false sense of
coverage — three reviewers sharing a family also share their blind spots.

## Consequences

- `AGENT_ORCHESTRATION_FRAMEWORK.md` is **deleted**. It restated `SKILL.md` and the YAML almost in
  full, giving risk signals, checkpoint fields, and cache rules three homes each. Its genuinely
  unique content — decomposition criteria and the contract's minimum sections — folded into
  `SKILL.md`.
- `CUSTOM_ORCHESTRATION_VS_OPENHANDS.md` became [ADR 0005](0005-openhands-as-provider-adapter.md).
- `swarm-orchestration/README.md` is now operational only: how to run the scaffold, how to add an
  adapter. Its "Architectural Principles" section duplicated policy and is gone.
- The verdict fall-through changes from an implicit `APPROVE` to an explicit `APPROVE_WITH_NITS`, so
  1–2 MEDIUM findings stop vanishing. This makes reviews noisier, deliberately.
- Deleting `/ weight_sum` from `RiskEngine.score()` makes Best-of-3 reachable for the first time.
  Previously `very_high` required ~10 of 11 signals firing, so the expensive branches were dead code.
- Raw-sum risk scoring means **adding a signal shifts every band**, so thresholds must be re-tuned in
  the same change. This is the cost of the intuitive mapping (~2 signals → medium, ~4 → high) and is
  recorded beside the weights.

## Alternatives considered

- **Telemetry first** — log every agent spawn with role, model, and tokens to omnigraph, and measure
  the cost split before changing behaviour. Cursor's thesis is that an 8× spread is invisible without
  per-role attribution, and measurement is the first open question Garg admits he has not solved.
  Rejected as the *starting* point because the schema has no node type for a metered run, so it needs
  a cluster schema change plus `apply-cluster.sh` against the live server per
  `as-rule-declared-is-not-live` — real infra work for deferred payoff. A cheap slice survives: the
  workflow logs its own wave cost from `budget.spent()`. Full run-telemetry is deferred to its own ADR.
- **Adopt Cursor's architecture wholesale** — recursive planner→worker tree decomposition, a
  reconciler agent for conflicting design decisions, a megafile decomposer, a line-budgeted Field
  Guide, a purpose-built VCS. Rejected as YAGNI: those solve coordination failures at ~1,000
  commits/second across dozens of concurrent planners. At 2–5 agents the existing worktree plus
  TTL-lock model already prevents the collisions they reconcile, and omnigraph is a better-structured
  Field Guide than a text file with a line cap.
- **Hand-write the `.claude/` adapter.** Rejected: two hand-maintained copies of the same tier and
  lens lists is the failure this ADR exists to stop.
