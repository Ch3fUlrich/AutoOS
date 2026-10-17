---
name: pr-approval-agent
description: Deterministic safety gates and PR routing logic (adapted from StampHog).
source: https://github.com/PostHog/posthog/tree/master/tools/pr-approval-agent
license: MIT
---

# PR Approval Agent (StampHog Gates)

This skill provides deterministic safety gates that must be passed *before* any LLM evaluation. It prevents oversized, highly sensitive, or un-testable changes from consuming AI budget or auto-merging dangerously.

## Core Invariants

1. **Fail-Closed**: Gates are authoritative. The LLM Reviewer can tighten security (reject a PR the gates allowed), but can *never* loosen it (approve a PR the gates blocked).
2. **Transient State Recovery**: Transient errors during gate evaluation must trigger retries, not silent failures.

## Safety Gates

### 1. The Deny-List

Any PR touching files that match these categories must be immediately escalated to a human Architect (`BLOCKED`).

*   **Auth**: `(?i)(auth|login|session|jwt|oauth)`
*   **Crypto**: `(?i)(crypto|hash|encrypt|decrypt|cipher)`
*   **Migrations**: `(?i)(db/migrate|alembic|migrations)`
*   **Infra**: `(?i)(terraform|k8s|dockerfile|helm|\.github/workflows)`
*   **Billing**: `(?i)(stripe|billing|invoice|subscription|payment)`
*   **Public API**: `(?i)(api/public|openapi|swagger|routes/v1)`
*   **Dependencies**: `(?i)(package\.json|requirements\.txt|go\.mod|Cargo\.toml)`

### 2. Size Ceilings

Large PRs exhaust context windows and increase bug density.

*   **Default Ceiling**: `max_files_changed: 30`, `max_lines_changed: 800`.
*   **Exemptions**:
    *   Test fixtures/snapshots (e.g., `__snapshots__`, `testdata/`).
    *   Auto-generated files (e.g., `.min.js`, `.lock`, `zz_generated`).
    *   Pure documentation (`.md`, `.txt`).

If a PR exceeds the ceiling and is *not* exempt, return `BLOCKED: SIZE_CEILING_EXCEEDED` and instruct the Engineer to split the PR.
