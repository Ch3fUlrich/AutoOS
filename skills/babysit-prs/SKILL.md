---
name: babysit-prs
description: Babysit state management and continuous CI sweeping.
source: https://github.com/haacked/dotfiles/tree/main/ai/skills/babysit-prs
license: MIT
---

# Babysit PRs (State Management)

This skill provides state-tracking protocols for long-running workflows, ensuring that agents can "sweep" open PRs, monitor CI status, and apply feedback asynchronously without losing context.

## 1. The Babysit State

Instead of relying solely on conversational memory, the `BabysitState` object (often persisted in `omnigraph` or a local ledger) tracks:
*   The current status of the CI/CD pipeline for the branch.
*   Pending review threads that the Engineer agent needs to address.
*   Retry counters for transient LLM API failures.

## 2. CI Monitoring Loop

An agent invoking this skill will:
1.  Check the status of the test suite.
2.  If tests are running, sleep or exit gracefully (recording state) until notified.
3.  If tests failed, extract the specific failure logs into the `EvidenceBundle` and auto-generate a fix commit.

## 3. Transient Failure Recovery

If the LLM provider times out or returns a malformed response during the fix generation, the `BabysitState` simply increments a retry counter and halts. It does NOT mark the PR as `BLOCKED` until a strict retry limit (e.g., 3) is breached.
