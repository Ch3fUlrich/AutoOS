# DONE — Lane C: DeepSeek Thinking-Mode 400 Fix

**Date:** 2026-09-30
**Branch:** `L1-backlog/ws-fixes-20260930`
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-fixes`
**Base HEAD:** `d08f7f2` (unchanged — no push/merge/rebase/checkout)
**Branch tip:** `361ef45`
**Gateway:** OmniRoute v3.8.50 at `http://127.0.0.1:20128`

## Problem

DeepSeek thinking-mode returns 400: "The `reasoning_text` in the thinking mode
must be passed back to the API." when sending a tool-call round trip through
the OmniRoute gateway.

## Root Cause

The gateway converts chat-completion history to the Responses API format in
`toResponses` (compiled to `dist/.build/next/server/chunks/*.js`). When the
reasoning replay cache misses (tool_call_id mismatch, >2h expiry, or DB
cleared), the reasoning text is empty or a placeholder, so no `reasoning`
input item is pushed before `function_call` items. DeepSeek rejects the
request with 400.

The empty injection (`schemaCoercion.ts:455`, `reasoning_content: ""`) is a
separate defense layer — it does not help when `toResponses` omits the
`reasoning` item from the `input[]` array. The replay cache
(`reasoningCache.ts`) is the primary defense.

## Fixes Applied

### 1. opencode Config Fix (user-owned file, backed up in-place)

`leaf-reviewer` model changed from `deepseek/deepseek-v4-flash` (unresolvable
— no deepseek provider in opencode) to `omniroute/deepseek-v4.1-flash`
(correct path through gateway).

### 2. Config Artifact

`configuration/omniroute/reasoning-defense.md` — documents the defense pipeline,
`requiresExplicitReasoningReplay` logic, cache TTL, and operator mitigations.

### 3. Source Patch (commit `3f6fd25`)

Patches the OmniRoute gateway npm package so `toResponses` injects a
`NON_ANTHROPIC_THINKING_PLACEHOLDER` reasoning item when the provider is
DeepSeek/Xiaomi-MiMo, the reasoning is a placeholder or absent, and the
message has tool calls.

**Compiled .js chunk patches** (5 files, 2 patterns — runtime fix):

| Pattern | Files | Push target | Credential record | Model |
|---------|-------|-------------|-------------------|-------|
| A | `_08_y1bx._.js`, `_1j_edf1._.js`, `_1luyz1c._.js` | `g` | `A._provider` | `p.model` |
| B | `_15ose6x._.js`, `_1xkpq2s._.js` | `d` | `p._provider` | `g.model` |

**.ts source patches** (2 files — source-tree consistency):

- `open-sse/translator/index.ts` — added `deepseek` to
  `requiresReasoningContentPresence()`
- `open-sse/translator/request/openai-responses/toResponses.ts` — added
  `NON_ANTHROPIC_THINKING_PLACEHOLDER` import,
  `responsesProviderRequiresReasoningPresence()` helper,
  `reasoningIsPlaceholder` variable, and `else if` placeholder injection branch

### Backup Files (7 total, next to their originals in the package dir)

| Original file | Backup filename |
|------|--------|
| `_08_y1bx._.js` | `.autoos-backup-20260930174401` |
| `_1j_edf1._.js` | `.autoos-backup-20260930174401` |
| `_1luyz1c._.js` | `.autoos-backup-20260930174401` |
| `_15ose6x._.js` | `.autoos-backup-20260930174401` |
| `_1xkpq2s._.js` | `.autoos-backup-20260930174401` |
| `translator/index.ts` | `.autoos-backup-20260930170532` |
| `toResponses.ts` | `.autoos-backup-20260930172918` |

### Reapply Artifact

`configuration/omniroute/reason-fix-reapply.ps1` — idempotent reapply script.
Auto-detects the global omniroute package (`npm root -g`), backs up each file,
patches all 5 compiled `.js` chunks + 2 `.ts` source files. A second run
reports "already patched" / SKIP for every file and exits 0.

**`npm update omniroute` revert caveat:** `npm update` replaces the entire
package directory, reverting all patches. Re-run the reapply script. If the
find strings no longer match (new version restructured the code), the script
reports errors — inspect the new code and update the find/replace strings.

## Evidence

### Reproduction (`tools/probe-reasoning-repro.py`)

```
deepseek-v4.1-flash        overwrite  → 400_REASONING  (cache miss)
deepseek-v4.1-flash        preserve   → 200 PASS        (cache hit)
deepseek/deepseek-flash    overwrite  → 400_REASONING
deepseek/deepseek-flash    preserve   → 200 PASS
```

### Isolated Instance Verification (port 20130, shared gateway on 20128 untouched)

| Model | Scenario | Before patch | After patch |
|-------|----------|--------|-------|
| `deepseek-v4.1-flash` | cache miss (overwrite) | 400 | **200** |
| `deepseek-v4.1-flash` | cache hit (preserve) | 200 | 200 |
| `deepseek/deepseek-flash` | cache miss (overwrite) | 400 | **200** |
| `deepseek/deepseek-flash` | cache hit (preserve) | 200 | 200 |

**4/4 test cases PASS.** The shared gateway on port 20128 was deliberately
not restarted — it is still running the unpatched chunks (see Deferred
Operator Action below).

### Cross-Family Review (`tools/probe-cross-family.py`)

| Family | Model | Round trip | Result |
|---|---|---|---|
| DeepSeek | `deepseek-v4.1-flash` | **400** | **400_REASONING** |
| Gemini | `vertex/gemini-3.8-flash` | 200 | PASS |
| Qwen | `ovh/Qwen3.8-27B` | 200 | PASS |

**Verdict: DeepSeek-specific.** The 400 reasoning error does not affect
Gemini or Qwen. Gemini returns `reasoning_content` but does not require it
to be passed back. The gateway's empty injection is harmless for
non-DeepSeek providers.

### DB Diagnostics (`tools/diag-db.py`)

- `model_capabilities` table: **empty** for ALL models (no models.dev sync data)
- `interleaved_field` = NULL for DeepSeek → `requiresExplicitReasoningReplay` = false
- `reasoning_cache` table: 1986+ entries, TTL = 7200s (2h)
- `model_capability_overrides`: does NOT support `interleaved_field` key

## Reviewer Findings (commit `4c4a534` + `361ef45`)

Three reviewers from families different from the fix authors reviewed the
patch, reapply script, and documentation:

| Reviewer | Model | Family | Findings | Verdict |
|---|---|---|---|---|
| Gemini | `omniroute/vertex-flash` | Gemini | Variable names correct, IIFE syntax valid, no conflicts | ✅ PASS |
| Space Bunny | `opencode/space-bunny-free` | Space Bunny | Reapply script idempotency defect found (insertion-type patches re-applied on second run), no secrets | ✅ PASS (defect fixed in `361ef45`) |
| DeepSeek | `omniroute/deepseek-v4.1-flash` | DeepSeek | Username in paths (rule violation), root cause analysis inaccurate, file table errors, diag tool info exposure | ✅ PASS (all fixed: `bbd12a4` scrubbed username, `4c4a534` fixed root cause + diag tool) |

**Fixes from reviewer findings:**
- `bbd12a4`: Scrubbed username from handoff doc (3 occurrences), fixed root
  cause analysis
- `4c4a534`: Added `Try-Replace-Normalized` helper (CRLF→LF normalization),
  removed top-level marker check that short-circuited partial patches,
  each `.ts` patch now has its own idempotency check, all four
  `toResponses.ts` patches report SKIP/ERROR when find string absent,
  fixed root cause in `reasoning-defense.md`, redacted error body in
  `diag-models-auth.py`
- `361ef45`: Fixed idempotency bug for insertion-type patches (patches 2
  and 4 where find string is substring of replacement) — added optional
  `$marker` parameter to `Try-Replace-Normalized`, checked FIRST before
  find string

## Commits (`8cccdca` → `361ef45`)

| Commit | Content |
|---|---|
| `8cccdca` | WIP: probe + diag tools for DeepSeek reasoning 400 investigation |
| `18a452b` | DeepSeek reasoning 400 root-caused + cross-family verified |
| `3f6fd25` | Compiled chunk patches (5) + .ts source patches (2) + reapply script + README |
| `4598176` | Handoff doc: fix branch typo, add compiled-chunk patch details |
| `bbd12a4` | Scrub username from handoff doc, fix root cause analysis |
| `4c4a534` | Address cross-family reviewer findings on reapply script and docs |
| `43764c3` | Gitignore scratch files from omniroute patch verification |
| `361ef45` | Fix idempotency bug in reapply script for insertion-type patches |

## Files Owned (this lane)

- `tools/probe-reasoning.py` — multi-model probe with reasoning_effort variants
- `tools/probe-reasoning-repro.py` — definitive before/after probe (overwrite vs preserve)
- `tools/probe-reasoning-edge.py` — edge-case probe (strip, no-effort, streaming)
- `tools/probe-reasoning-stream.py` — streaming reasoning probe
- `tools/probe-cross-family.py` — cross-family review probe
- `tools/probe-cross-family-extra.py` — extra cross-family candidates
- `tools/diag-db.py` — storage.sqlite diagnostics
- `tools/diag-interleaved.py` — interleaved_field API probe
- `tools/diag-key-source.py` — key source tracing
- `tools/diag-models.py` — gateway model listing
- `tools/diag-models-auth.py` — gateway model listing with auth
- `configuration/omniroute/reasoning-defense.md` — defense pipeline docs
- `configuration/omniroute/reason-fix-reapply.ps1` — idempotent reapply script
- `configuration/omniroute/reason-fix-README.md` — patch documentation
- `docs/handoff/2026-09-30-laneC-reasoning-fix.md` — full evidence/handoff doc
- `logs/handoff-sessions/DONE-ws-fixes.md` — this file

## Files NOT Touched (by design)

- `configuration/omniroute/combos.json` — P1-combos territory
- `configuration/omniroute/apply.ps1` — resilience hunks excluded
- `catalog/ai-registry.json` — L1-beta territory
- `tools/probe-sweep.py` — P1-sweep territory
- OmniRoute gateway source — patched in-place in the npm package (not
  committed to the repo); reapply script handles `npm update` revert

## Key Deferred Operator Action

**The shared gateway on :20128 is still running the unpatched chunks.** It
must be restarted at a safe window to activate the fix. The lanes deliberately
did not restart it — a restart would interrupt other active lanes using the
shared gateway. The fix was verified only against an isolated instance on
port 20130 with a separate `DATA_DIR`.

To activate the fix on the shared gateway:
1. Wait for a safe window (no active lanes using the gateway)
2. Run `pwsh -File configuration/omniroute/reason-fix-reapply.ps1` (idempotent)
3. Restart the gateway process

## What Still Needs Upstream Attention

If the 400 becomes frequent (cache expiry >2h, DB clearing, tool_call_id
normalization), file an issue with OmniRoute maintainers. Source patch options:

1. Don't strip reasoning from the response — let the client pass it back
2. Use DB cache as secondary lookup when in-memory cache misses
3. Add `interleaved_field` to `model_capability_overrides` key types

These are upstream gateway fixes, not config changes.
