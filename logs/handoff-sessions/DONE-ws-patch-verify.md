# DONE — ws-patch-verify (lane `L1-backlog/ws-patchr1-20260930`)

**Lane:** `patch-verify` (verification, pinned). Verifies/completes the three lanes cancelled
mid-work before the `:20128` restart.
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-patchr1`
(`git rev-parse --show-toplevel` = that path; branch `L1-backlog/ws-patchr1-20260930`,
tracking `origin/main`). Main checkout never touched.
**Doc:** `docs/handoff/2026-10-01-lanePatchVerify.md`
**Nonce:** `PV-20261001-4H7K-9Q2W-B6XD`

## Verdict

**GO** for the `:20128` restart. The live package carries a complete, verified patch set:
19 patched files, 19/19 with a certified pristine revert path, `_18ct13i._.js` fully patched
and idempotent, and both probes (reasoning + vertex) pass against one isolated gateway started
from the live package.

## What was verified

1. **R1** — both installed backups
   (`open-sse/config/providers/registry/scaleway/index.ts…pristine-3.8.50-20261001-002911`,
   `open-sse/translator/paramSupport.ts…pristine-3.8.50-20261001-002911`) are byte-identical to
   the certified tarball (SHA256 `BF39CEF1…` / `806D5302…`; tarball sha512/sha1 match npm).
2. **`_18ct13i._.js`** — reapply script (`reason-fix-reapply.ps1` @ `9cb8055`) against live:
   `0 patched, 12 skipped, 0 errors` (exit 0); 14/14 file hashes unchanged pre==post; live
   `_18ct13i._.js` = 1,302,997 B `947D1EFA…`, the full-patch composed hash.
3. **Functional** — one isolated gateway from the live package (`:20146`, DATA_DIR
   `%TEMP%\opencode\patchr1-iso`): reasoning cache-miss 200, cache-preserve 200
   (`400 … reproduced: False`); vertex trailing shapes 200, control 200. Killed by PID; no
   listener left in `201xx`; shared `:20128` untouched.

## Reviewer

Reviewer family: `t3-reviewer` (`t3 cheap-driver-128k`), different family from the writer
(`deepseek-v4.1-flash`). **Verdict: APPROVE** (attempt 2; attempt 1 disregarded as
non-independent — see doc §6). Nonce `PV-20261001-4H7K-9Q2W-B6XD` quoted back correctly.
