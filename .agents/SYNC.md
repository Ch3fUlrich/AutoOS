# Skill Vendoring Sync Ledger

This document tracks the upstream sources, commit hashes, and vendoring dates for external skills adapted into this repository.

## Upstream Sources

| Skill | Author/Source | Upstream URL / Repo | Vendored Date | Commit Hash (if known) |
|---|---|---|---|---|
| `pr-approval-agent` | PostHog / StampHog | [PostHog/posthog/tools/pr-approval-agent](https://github.com/PostHog/posthog/tree/master/tools/pr-approval-agent) | 2026-07-15 | - |
| `qa-swarm` | Paul D'Ambra | [pauldambra/dotfiles/ai/skills/qa-swarm](https://github.com/pauldambra/dotfiles/tree/main/ai/skills/qa-swarm) | 2026-07-15 | - |
| `review-triage` | Paul D'Ambra | [pauldambra/dotfiles/ai/skills/review-triage](https://github.com/pauldambra/dotfiles/tree/main/ai/skills/review-triage) | 2026-07-15 | - |
| `babysit-prs` | Phil Haack | [haacked/dotfiles/ai/skills/babysit-prs](https://github.com/haacked/dotfiles/tree/main/ai/skills/babysit-prs) | 2026-07-15 | - |
| `no-mistakes` | Kun Chen | [kunchenguid/no-mistakes](https://github.com/kunchenguid/no-mistakes) | 2026-07-15 | - |

## Sync Instructions

To check for upstream changes, you can use the following patterns (if you have cloned the upstream repo locally or via an API):

```bash
# Example for PostHog's PR Approval Agent
git fetch upstream
git diff <last-vendored-commit>..origin/master -- tools/pr-approval-agent/
```

*Note: These skills are **adapted and condensed** for this specific repository's orchestration framework, not mirrored verbatim. Upstream changes should be evaluated for conceptual updates rather than merged blindly.*
