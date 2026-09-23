---
name: qa-swarm
description: Multi-perspective swarm review, convergence, and scoring matrix.
source: https://github.com/pauldambra/dotfiles/tree/main/ai/skills/qa-swarm
license: MIT
---

# QA Swarm (Convergence & Scoring)

This skill defines how to execute a parallel swarm of specialized sub-agents and how to aggregate their independent findings into a single, deterministic final verdict.

## 1. Multi-Perspective Review

Instead of a single "Reviewer", the orchestrator spawns multiple persona-driven agents concurrently (e.g., Security Auditor, Performance Expert, XP Practitioner). Each independently analyzes the PR diff.

## 2. Verdict Scoring Matrix

Once all sub-agents report their findings, the Triage Pipeline aggregates the severity scores.

Each finding must be rated: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, or `NIT`.

The final verdict is calculated deterministically using the following matrix:

*   **If ANY `CRITICAL` finding exists**: -> `BLOCKED` (Escalate to Architect)
*   **If `>= 2 HIGH` OR `(1 HIGH + 2 MEDIUM)`**: -> `REQUEST_CHANGES` (Return to Engineer)
*   **If `1 HIGH` OR `>= 3 MEDIUM`**: -> `APPROVE_WITH_NITS` (Engineer can auto-fix or ignore based on budget)
*   **If only `LOW` or `NIT` findings exist**: -> `APPROVE` (Proceed to merge or deployment)

## 3. Convergence Detection

The orchestrator must wait for all parallel sub-agents to complete (or hit a hard timeout) before calculating the final verdict. If a sub-agent times out, the `EvidenceBundle` records a partial failure, but the orchestrator calculates the verdict based on the *available* swarm data, ensuring progress isn't permanently stalled by one slow agent.
