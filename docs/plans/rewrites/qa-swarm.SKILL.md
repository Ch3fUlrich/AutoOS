---
name: qa-swarm
description: Independent review lenses run in parallel and converge deterministically into one verdict.
---

# QA Swarm

A review is not one opinion. This skill defines how the `@reviewer` role turns an artifact under
review into a set of independent findings and reduces them to a single, reproducible verdict. It is
the review stage of [`swarm-orchestration`](../swarm-orchestration/SKILL.md) and the upstream half
of [`review-triage`](../review-triage/SKILL.md): the swarm produces findings, triage acts on them.

## 0. Where the numbers live

This file is policy, not configuration. Every roster, tier, deadline, severity vocabulary and
matrix belongs to
[`agent_orchestration.config.yaml`](custom_orchestration/agent_orchestration.config.yaml) and is
referenced here by key.

| Fact | Config key |
|---|---|
| Which lenses run, their focus and their model tier | `swarm_review.perspectives` |
| Whether lenses run at once, and the deadline for convergence | `swarm_review.parallel_execution`, `swarm_review.timeout_seconds` |
| How a partial swarm is reconciled | `swarm_review.convergence_strategy` |
| Severity vocabulary | `triage_policy.severities` |
| Verdict bands, their conditions and actions | `triage_policy.verdict_matrix` |
| Reviewer model diversity | `model_routing.reviewer` |
| Evidence persistence and log capture | `evidence_bundle` |

Add a config key, not a number. A numeric literal in this file is a defect.

## 1. Lenses

A lens is a named perspective with a stated focus — a security lens, a performance lens, a
readability lens. The roster lives in `swarm_review.perspectives`; this skill never hard-codes it,
and adding a lens is a config change, not a code change.

- Spawn one lens agent per perspective. The architect owns that choice; the reviewer executes it.
- Lenses audit the **same** revision. A lens reviewing a different commit than its peers produces
  evidence the aggregator cannot compare.
- Lenses do not see each other's findings until convergence. Agreement between agents that have
  read one another is not corroboration.
- State which skills each lens should load. A subagent does not inherit the parent session's active
  skills, so naming them is part of the spawn, not an optional courtesy.
- A lens whose work needs no reasoning takes the cheapest tier the roster allows. A naming pass
  does not warrant a frontier model.

## 2. Findings

A lens returns findings, never a narrative. Each finding carries:

- the lens that raised it (attribution),
- a severity from `triage_policy.severities`,
- the concrete location and the revision it applies to,
- the evidence that demonstrates the problem,
- the action required to clear it, and the owner of that action.

Never accept raw terminal output, intermediate reasoning, progress narration, or a restatement of
the prompt. A claim without evidence is not a finding — it is an opinion, and it is dropped rather
than reported.

## 3. Convergence

Wait for every lens or for the configured deadline, whichever arrives first. A slow lens is an
expected condition, not an outage:

- a lens that returns is aggregated as-is;
- a lens that misses the deadline is recorded as a gap, together with whatever partial result was
  actually received;
- a missing lens is never silently treated as a clean pass, and never stalls the run forever.

When `swarm_review.parallel_execution` is on, run the lenses concurrently and join on completion.
`swarm_review.convergence_strategy` names the join rule; do not substitute one of your own.

## 4. Verdict reduction

Reduction is deterministic. Take the union of returned findings, count them by severity, and apply
`triage_policy.verdict_matrix`. The matrix is the sole authority: do not re-derive a band from
intuition, and do not let a lens vote on the outcome. The verdict is a property of the finding set,
not of the loudest agent.

- Record the finding set alongside the band it produced, so the decision is reproducible later.
- Resolve ambiguity toward the stricter band. A review gate fails closed, so an uncertain high
  finding is treated as present.
- A verdict with neither findings nor evidence is invalid. Say the review did not run; do not
  approve a void.

## 5. Role boundaries

- The architect selects the lens roster and model tiers; the reviewer executes the selected roster.
- `@reviewer` spawns no further subagents (the leaf rule) and rewrites no architecture it was not
  tasked to rewrite.
- Nothing is approved on an agent's claim alone. Only the reduction over returned findings and
  captured evidence produces a verdict.
- The verdict and its attributed findings pass to
  [`review-triage`](../review-triage/SKILL.md) for classification, narration, and enforcement of
  the human-participation gate.
- Reviewer independence (`model_routing.reviewer`) means a model family different from the
  engineer's wherever the surface can provide one. Where it cannot, vary the lens and the tier, and
  record that family diversity was unavailable.

## 6. Anti-patterns

- A single generalist "reviewer" where distinct lenses were specified.
- Lenses exchanging findings before convergence, then "confirming" one another.
- Treating a timed-out lens as approval.
- Restating the matrix in prose and letting it drift from `triage_policy.verdict_matrix`.
- Approving because the diff "looks small" rather than because the finding set supports it.
- Reporting a verdict without the evidence bundle that justifies it.
