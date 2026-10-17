# DONE — lane `patch-integrity` (ws-patch-integrity)

**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-fixes`
**Branch:** `L1-backlog/ws-fixes-20260930` (start tip `b49f9e4`)
**Date:** 2026-09-30
**Handoff:** `docs/handoff/2026-09-30-lanePatchIntegrity.md`

## What was asked

Close the two remaining gaps in a complete pre-restart revert/reapply set, from
`patch-fix` (`6f93452`) `docs/handoff/2026-09-30-lanePatchFix.md` §4/§5:

1. DeepSeek fix misses `_18ct13i._.js` — add it to `$patternAFiles` in
   `configuration/omniroute/reason-fix-reapply.ps1`.
2. The clamp patch has no tracked artifact — create a tracked, idempotent reapply
   artifact that reproduces the 106 B six-chunk static-table insertion produced by the
   untracked `AutoOS-ws-qwenclamp\logs\patch_dist.py`.

## What was done

1. **`reason-fix-reapply.ps1`** — backed up (`*.autoos-backup-20260930230905`), then
   `_18ct13i._.js` added to `$patternAFiles`. Also found and fixed a defect outside the
   original scope: the script declared `#Requires -Version 5.1` but had no UTF-8 BOM and
   169 non-ASCII chars, so it **did not parse under Windows PowerShell 5.1** (the
   predecessor had only proved it under `pwsh`). Added the BOM per the repo's own
   `.gitattributes` rule. `reason-fix-README.md` corrected (five → six chunks).
2. **`qwen-clamp-reapply.ps1`** + **`qwen-clamp-reapply-README.md`** — new tracked
   artifacts under `configuration/omniroute/`. They port `patch_dist.py`'s behaviour
   (not its files), fix its dropped `/` in `VERIFY_NEW`, are idempotent, back up each
   changed file, and document the historical LF→CRLF normalisation.

## Evidence (all on throwaway copies of pristine `omniroute@3.8.50` tarball members)

* deepseek, `pwsh` and Windows PowerShell 5.1: run 1 `12 patched, 0 skipped, 0 errors`;
  run 2 `0 patched, 12 skipped, 0 errors`. `_18ct13i._.js` `findA 1→0`, `_ip 0→1`. The
  pre-fix script patches 11 and leaves `_18ct13i` at `findA=1`.
* clamp, `pwsh` and Windows PowerShell 5.1: run 1 `6 patched, 0 skipped, 0 errors`;
  run 2 `0 patched, 6 skipped, 0 errors`; `-DryRun` writes nothing.
* clamp byte-identity: `_0o8_5h8._.js` / `_0t1t5fj._.js` prove==live after CRLF
  normalisation (`CE4F379FE80EAEC6`, `7680F399BC3EC282`).
* composition (clamp+deepseek+vertex) reproduces live byte-for-byte (normalised) on 8/9
  chunks and on `openai-to-gemini.ts`; the lone `_18ct13i` difference is exactly the
  409 B deepseek fix live lacks.

## Verdict

* **clamp** — complete revert+reapply (chunks); companion qwenclamp artifacts unmerged.
* **deepseek** — complete for runtime chunks; the two `.ts` files are not reproduced
  byte-for-byte (source-only, not loaded at runtime) — recorded, not fixed.
* **vertex** — complete; artifact lives on `L1-backlog/ws-f1-vertex-20260930`, unmerged.

## Constraints honoured

No live package file modified; no gateway restart; no push/merge/rebase/checkout; no work
in main (`C:\Users\<user>\Documents\Code\AutoOS`). No secrets, binaries or user paths in
tracked files.

## Reviewer

Different family (`t3-reviewer`), **nonce-gated** — nonce written into the handoff doc and
required to be read back. Verdict and nonce recorded in the handoff doc §8 addendum.

## Rate-limit / admission events

None observed (no `chat_admission_busy`, no `Rate limit exceeded`).
