---
name: babysit-prs
description: Sweeps branches and CI across turns, tracking state and retrying transient faults without holding a session open.
---

# Babysit PRs

CI is slow and sessions are not. This skill defines how the `@engineer` role follows a branch across
turns — reading pipeline state, reacting to failures, and carrying a small amount of durable state —
without blocking a session open or trusting conversation memory. It is the async-CI companion to
[`no-mistakes`](../no-mistakes/SKILL.md) and a controlled input to
[`review-triage`](../review-triage/SKILL.md).

## 0. Where the numbers live

| Fact | Config key |
|---|---|
| What the babysit tracks and whether it auto-fixes | `babysit_state` |
| Retry allowance for transient faults | `evidence_bundle.max_retries_per_failure` |
| Where the evidence survives | `evidence_bundle.persist_to`, `evidence_bundle.capture_logs` |
| Review-loop bound a CI fix feeds into | `execution.max_review_loops` |
| Checkpoint fields for an interrupted sweep | `checkpointing.required_fields` |

Numbers are config; text is policy. Keep them apart.

## 1. The state record

A sweep is only useful if the next sweep can resume it. Record, per branch or pull request:

- the branch, and the head revision the record describes,
- the current pipeline status,
- the review threads awaiting the engineer,
- the retry counter for transient faults,
- a pointer to the captured failure evidence,
- the next action to take when the sweep resumes.

Persist the record where `evidence_bundle.persist_to` says, and write a checkpoint whenever a turn
ends with CI still in flight. The record — not the conversation — is what a later sweep reads. If
the two disagree, the record wins and the conversation is stale.

## 2. One sweep

Each invocation is a bounded sweep, not a wait. It reads, decides, and either acts or exits with its
state recorded.

1. Read the pipeline state for the recorded head revision.
2. **In flight** — record the state and yield. Do not poll in a tight loop and do not hold a process
   open to watch a pipeline.
3. **Green** — clear the retry counter and advance; if the review loop still has budget, hand the
   revision to the swarm review.
4. **Red** — capture the failing job's logs into the evidence bundle and produce a scoped fix, below.
5. **New head** — if the head revision moved, the previous run describes code that no longer exists.
   Reset the per-head counters and re-read.

Idempotency is required. A sweep repeated against unchanged state must change nothing; the head
revision plus the last action is the guard that stops a second identical fix commit from being
pushed.

## 3. Fixing a red pipeline

A fix is scoped to the failing job. Reproduce the failure before changing code, follow the
repository's test discipline, and validate through the local proxy
([`no-mistakes`](../no-mistakes/SKILL.md)) before the fix is pushed. The proxy exists so that a
remote pipeline is never the first place a syntax error is discovered.

A fix that would change architecture, touch a forbidden path, or grow beyond the assigned scope is
not a fix: stop and escalate to the architect with the evidence attached. Never amend a shared
branch to make a check pass.

## 4. Transient versus real

A provider timeout or a malformed response while generating a fix is not a pipeline failure. Keep
the two apart:

- **Transient** — increment the retry counter, record the state, and halt the sweep. Do not mark the
  branch blocked, and do not keep retrying inside one invocation.
- **Real** — a pipeline that genuinely fails goes down the fix path above.

The retry allowance is `evidence_bundle.max_retries_per_failure`. When it is exhausted, escalate to
the architect rather than spinning. A branch is marked blocked only for a real, unresolved failure
or an exhausted budget — never merely because a call timed out.

## 5. Threads and humans

Pending threads are part of the record. Agent-authored threads are addressed in the same sweep,
within the scope above. Threads a person has joined are not touched by this skill; the
human-participation gate owned by [`review-triage`](../review-triage/SKILL.md) applies and defers
them to that person.

## 6. Terminating

When the pipeline is green and no actionable thread or review round remains, mark the record complete
and stop sweeping. A merged or abandoned branch is not swept again. Babysitting is a loop with an
exit, not a daemon.

## 7. Anti-patterns

- Holding a session open to "watch" CI instead of taking a bounded sweep.
- Relying on chat history to remember which pipeline failed.
- Retrying a transient fault past the configured allowance, or in a tight loop.
- Marking a branch blocked over a provider timeout.
- Pushing a second fix for the same head revision.
- Amending a branch, or touching a human thread, to make a check pass.
- Sweeping a branch that is already merged or abandoned.
