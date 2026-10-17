---
name: review-triage
description: Classifies review findings, maps them to a verdict, and guards what only a human may touch.
---

# Review Triage

Every finding produced by [`qa-swarm`](../qa-swarm/SKILL.md) has to become a decision: fix it now,
send it back, or leave it to a person. This skill owns that decision, the shape of anything the
automation says in a review thread, and the boundary it must never cross. It is the triage stage of
[`swarm-orchestration`](../swarm-orchestration/SKILL.md).

## 0. Where the numbers live

| Fact | Config key |
|---|---|
| The human-participation gate and its prohibitions | `triage_policy.human_participation_gate` |
| Severity vocabulary | `triage_policy.severities` |
| Verdict bands, their conditions and actions | `triage_policy.verdict_matrix` |
| How many review rounds before replanning | `execution.max_review_loops` |
| Silent versus chatty narration | `narration.mode` |
| Narrator formatting requirements | `narration.bluf_required`, `narration.attribute_findings`, `narration.prefix_autofixable` |

This file is the policy; the numbers are not its business. A threshold repeated here is a defect —
point at the key instead.

## 1. Classify before acting

Every finding is classified on three axes before anything reacts to it. Acting on an unclassified
finding is how automation ends up talking over a person.

- **Severity** — from `triage_policy.severities`. Severity sets the weight, not the owner.
- **Provenance** — was the thread opened by a human, or by an agent or bot? Provenance decides who
  is permitted to act, which the gate below governs.
- **Disposition** — resolvable without the author, requiring the author, or requiring a human
  judgement. Disposition decides whether the automation fixes it, asks, or escalates.

Classification is attached to the finding and is not re-litigated per reply.

## 2. The human-participation gate

If a person has joined a thread, that thread belongs to the person. The gate is absolute and cannot
be overridden by budget, deadline, or a clean verdict:

- never auto-fix the thread,
- never auto-resolve the thread,
- never post to the thread unless the automation is explicitly addressed.

Automation may still act on the code the thread concerns through the normal path, but it does not
touch the thread itself. When in doubt whether a participant is human, treat them as human. The
gate exists so a bot never argues with a person: failing closed costs a comment, failing open costs
trust. `triage_policy.human_participation_gate` records the prohibitions and whether the gate is
armed.

## 3. From findings to a verdict

The aggregated finding set maps to exactly one verdict through `triage_policy.verdict_matrix`. The
matrix names both the band and the action, so triage selects an action rather than inventing one:

- **blocked** — escalate to the architect.
- **request_changes** — return to the engineer for another pass.
- **approve_with_nits** — the engineer fixes or consciously ignores, within budget.
- **approve** — proceed toward merge.

Read the conditions from the config, never from memory. `execution.max_review_loops` bounds how many
times a task may cycle; when the budget is spent, the answer is to replan with a better contract,
never to loop again or lower the bar. The deterministic safety gates run before this matrix and may
be tightened by review but never loosened.

## 4. Narration

How much triage says is set by `narration.mode`, not chosen per pull request.

- **Silent** — the engineer changes the code for auto-resolvable items in a separate commit and the
  thread stays untouched. The record lives in the evidence bundle, not in a comment.
- **Chatty** — every finding is surfaced, and an item the automation can fix itself is marked with
  `narration.prefix_autofixable` so a reader knows not to start typing.

When triage does write, the shape is fixed:

- the first line is the verdict, because a reader who stops after one line must still get the
  decision (`narration.bluf_required`);
- each finding is attributed to the lens that raised it (`narration.attribute_findings`);
- each finding states the required action and who owns it;
- nothing is re-derived in the comment that the evidence bundle already settled.

Narration reports the verdict. It does not re-vote on it.

## 5. Escalation

A `blocked` verdict escalates to the architect with the finding and its evidence attached; it is not
a retry. A finding that needs a human judgement is escalated, not quietly dropped. Agent-authored
threads are the only threads triage may resolve on its own, and only for findings it actually
cleared.

## 6. Anti-patterns

- Replying to, or resolving, a thread a person is part of.
- Choosing a narration tone per comment instead of reading `narration.mode`.
- Restating the verdict matrix with conditions that differ from the config.
- Looping past the review budget instead of replanning.
- Reporting one verdict in the log and telling the thread another.
- Treating a human's silence as consent.
