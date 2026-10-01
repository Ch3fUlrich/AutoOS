# DONE — L1-backlog/ws-changelog-20260930 (CHANGELOG bullet per wave/lane)

**Lane:** `L1-backlog/ws-changelog-20260930`
**Worktree:** `C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-changelog`
**Base:** `f2d8d607`
**Date:** 2026-10-01
**Writer:** `changelog` (pinned)
**Evidence:** `docs/handoff/2026-10-01-laneChangelog.md`
**Nonce:** `CHANGELOG-NONCE-9Kx2Vq7M`

## CHANGELOG bullet

`CHANGELOG.md` `## [Unreleased]` — 11 new bullets: T1SECOND; Resilience defaults
(`51fd01d2`/`3b0135e3`); Patch artifacts; Probe scripts; Admission fixes; Nebius removal;
FREEWIRE + gemini retention; OVH legs + registry; Vertex leg + 1M contexts (a5bcb69);
REVIEWGATE-2FAM; Redactions. `git --no-pager diff --stat` = `CHANGELOG.md | 11 ++++++++++`,
`1 file changed, 11 insertions(+)`.

## GeminI correction recorded

The FREEWIRE bullet says **"gemini retained; usage governed by the repeated-429 backoff
policy (3×429/120 s → 300 s cooldown per leg; 30 min park)"** — the operator's reversal of
the earlier exclusion, never "gemini excluded".

## Verification

- No dedicated CHANGELOG validation test; `grep -rl CHANGELOG tests/` →
  `tests/linux/33-documentation.sh`, `tests/test_keys_file.py`, `tests/test_skill_rules.py`
  (comment / exclusion only).
- `python tests/test_skill_rules.py` → `Ran 26 tests … OK`.
- `python tests/test_keys_file.py` → `Ran 14 tests … OK`.
- Keyword greps (`grep -in`-equivalent) confirm freewire/gemini/nebius/OVH/ovhcloud/vertex/
  admission/429/probe all present (evidence §3).
- No secret, binary, real username or user-home path in any tracked file added.

## New standing rule (operator)

**Every lane DONE carries a "CHANGELOG bullet" line.** Recorded in the evidence doc §4.

## Remains (for L0)

- DONE notes on sibling branches this lane could not update (list in evidence §5) need the
  "CHANGELOG bullet" line added by their owning lane / L0.
- Merge decision for this branch.

## Refusals honoured

No push/merge/rebase/checkout; no gateway restart; no sibling worktree or other lane's file
modified; no work in the main checkout (`C:/Users/<user>/Documents/Code/AutoOS`).

## Rate-limit / admission events

None observed on the live gateway during this lane (`chat_admission_busy` / `Rate limit
exceeded`: 0; backoffs: 0). The reviewer leaf's events, if any, are recorded in the reviews
section below.

## Reviews (free families only)

| # | Reviewer route (family) | Verdict | Session |
|---|---|---|---|
| 1 | `opencode/nemotron-3-ultra-free` (NVIDIA, free) | _pending_ | _pending_ |
| 2 | `opencode/space-bunny-free` (Space Bunny, free) | _pending_ | _pending_ |

Both are nonce-gated on `CHANGELOG-NONCE-9Kx2Vq7M`, read-only. Verdicts are appended after
they return.
