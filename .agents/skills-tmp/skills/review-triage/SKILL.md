---
name: review-triage
description: Narration protocol, dual-mode operation, and the human participation gate.
source: https://github.com/pauldambra/dotfiles/tree/main/ai/skills/review-triage
license: MIT
---

# Review Triage (Narration & Human Gate)

This skill dictates how automated systems interact with humans inside review threads, ensuring bots do not become noisy or override explicit human intent.

## 1. The Human Participation Gate

The absolute cardinal rule of automated triage: **If ANY comment in a thread was authored by a real human being, the entire thread is classified as HUMAN_PARTICIPATING.**

If a thread is `HUMAN_PARTICIPATING`:
*   The orchestrator must NEVER auto-fix the thread.
*   The orchestrator must NEVER auto-resolve the thread.
*   The orchestrator must NEVER reply to the thread (unless explicitly `@` mentioned).

## 2. Dual-Mode Operation

The `Narrator` component operates in two distinct modes configured via the `triage_policy` YAML:

*   **Silent Mode**: Nits and low-priority fixes are resolved automatically by the Engineer agent in a separate commit without ever leaving a comment on the PR.
*   **Chatty Mode**: The Narrator leaves comments for all findings, but prefixes automated auto-resolvable issues with `[🤖 AUTO-FIXABLE]`.

## 3. Narration Protocol

When the Narrator does post to a PR/Task thread, it must follow strict formatting:
1.  **Bottom-Line Up Front (BLUF)**: The first line must state the final aggregated verdict (e.g., `🛑 BLOCKED`, `⚠️ REQUEST_CHANGES`, `✅ APPROVE_WITH_NITS`).
2.  **Attribution**: Findings must be clearly attributed to the sub-agent that found them (e.g., `*From Security Auditor:*`).
3.  **Actionability**: Every finding must clearly state the action required from the human or Engineer agent.
