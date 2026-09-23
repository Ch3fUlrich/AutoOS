# Third-party content

Origin and licence of every vendored or adapted component, recorded before publication
(plan Task 1.6). Each licence was **measured on 2026-09-18** from the upstream repository itself
with `gh api repos/<owner>/<repo>/license` and `gh api repos/<owner>/<repo>/contents/<path>?ref=<commit>`,
pinned to the commit named in the evidence link. Adapted skills are listed in
[`skills/SYNC.md`](skills/SYNC.md).

| Component (here) | Origin | Licence | Evidence (pinned) | Compatible with MIT publication |
|---|---|---|---|---|
| `infra/mcp-servers/servers/superpowers` (vendored) | [erophames/superpowers-mcp](https://github.com/erophames/superpowers-mcp) | MIT, **declared only**: `package.json` `"license": "MIT"` and README `## License — MIT`; the repo has **no LICENSE file** (GitHub API: 404, no detected licence) | [package.json @ 7199130](https://github.com/erophames/superpowers-mcp/blob/7199130d81d613a75dec1fc61fe18dc3cb24dbaf/package.json), [README @ 7199130](https://github.com/erophames/superpowers-mcp/blob/7199130d81d613a75dec1fc61fe18dc3cb24dbaf/README.md) | Yes by declaration; no upstream copyright line exists to reproduce |
| ↳ skills it serves at runtime (cloned, not vendored) | [obra/superpowers](https://github.com/obra/superpowers) (`src/git.ts` `SUPERPOWERS_REPO`) | MIT, Copyright (c) 2025 Jesse Vincent | [LICENSE @ b36e082](https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/LICENSE) | Yes |
| `skills/pr-approval-agent` (adapted) | [PostHog/posthog `tools/pr-approval-agent`](https://github.com/PostHog/posthog/tree/bd558c85822f19f214305a03fba9f99f8b56681c/tools/pr-approval-agent) — the path was removed from `master` on 2026-08-20 ([45e7ebd](https://github.com/PostHog/posthog/commit/45e7ebd200839628331846a9c10b20b4accfe567)); pinned to its last parent | MIT (Expat), Copyright (c) 2020-2026 PostHog Inc., for everything outside `ee/`; this path is outside `ee/` | [LICENSE @ bd558c8](https://github.com/PostHog/posthog/blob/bd558c85822f19f214305a03fba9f99f8b56681c/LICENSE) | Yes |
| `skills/qa-swarm` (adapted) | [pauldambra/dotfiles `ai/skills/qa-swarm`](https://github.com/pauldambra/dotfiles/tree/491d5ffeea995fd2fa84def405d0f0b7581e47d6/ai/skills/qa-swarm) | **None.** No LICENSE file (API 404), no licence in the README or the `SKILL.md` | [tree @ 491d5ff](https://github.com/pauldambra/dotfiles/tree/491d5ffeea995fd2fa84def405d0f0b7581e47d6) | **No** (all rights reserved by default) |
| `skills/review-triage` (adapted) | [pauldambra/dotfiles `ai/skills/review-triage`](https://github.com/pauldambra/dotfiles/tree/491d5ffeea995fd2fa84def405d0f0b7581e47d6/ai/skills/review-triage) | **None** (same repository as above) | [tree @ 491d5ff](https://github.com/pauldambra/dotfiles/tree/491d5ffeea995fd2fa84def405d0f0b7581e47d6) | **No** (all rights reserved by default) |
| `skills/babysit-prs` (adapted) | [haacked/dotfiles `ai/skills/babysit-prs`](https://github.com/haacked/dotfiles/tree/e2b47b4c72d301d48564f64b2c139ba8ce9e917e/ai/skills/babysit-prs) | **None.** No LICENSE file (API 404), no licence in the README or the `SKILL.md` | [tree @ e2b47b4](https://github.com/haacked/dotfiles/tree/e2b47b4c72d301d48564f64b2c139ba8ce9e917e) | **No** (all rights reserved by default) |
| `skills/no-mistakes` (adapted) | [kunchenguid/no-mistakes](https://github.com/kunchenguid/no-mistakes) | MIT, Copyright (c) 2026 Kun Chen | [LICENSE @ 9199313](https://github.com/kunchenguid/no-mistakes/blob/9199313f410e09215df57a89c1fd9ab8f4a3a407/LICENSE) | Yes |

The pinned commit is the upstream head measured on 2026-09-18 (for PostHog, the last commit
before the path was removed), not the commit the content was adapted from: `skills/SYNC.md` records no vendored commit for any of these sources.

The Omnigraph web UI (`infra/mcp-servers/servers/omnigraph-viewer/`) is the operator's own code
and ships under AutoOS's licence (MIT).
