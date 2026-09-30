# DONE — lane `patch-fix` (ws-patch-fix) — 2026-09-30

**Lane:** `patch-fix`
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-f1`
**Branch:** `L1-backlog/ws-f1-vertex-20260930`
**Predecessor:** `patch-backups-3` (`9e643e3`, `docs/handoff/2026-09-30-lanePatchBackups.md`)
**Status:** DONE. Worktree clean at end (quoted below). No live package file changed; no
gateway restarted; no push/merge/rebase/checkout.

## Landed (this branch)

- `tools/vertex-trailing-turn-reapply.ps1` — **repaired**. The old anchors
  (`mergeConsecutiveSameRoleContents\s*\(\s*tb\.messages\s*\)` and
  `…\(\s*e\.messages\s*\)`) matched **nothing** in pristine 3.8.50, so a post-`npm update`
  reapply was a silent no-op; it also dot-sourced a non-existent UI module and called
  four functions that do not exist in `lib/windows/AutoOS.Ui.psm1`. Now self-contained,
  pure-ASCII, uses the six real chunk anchors (as `tools/apply-vertex-patch.py`) plus the
  real `.ts` anchor, backs up each file, and is idempotent.
- `docs/handoff/2026-09-30-laneF1-vertex.md` — corrected: of the four chunks it named,
  three differ from the tarball and **`_14jycqh._.js` is byte-identical (unpatched)**;
  the strip actually lives in six other chunks (`_08_y1bx`, `_18ct13i`, `_1j_edf1`,
  `_1luyz1c`, `_15ose6x`, `_1xkpq2s`). Reapply section rewritten.
- `configuration/omniroute/vertex-trailing-turn-README.md` — corrected reapply path
  (`configuration/omniroute/…` → `tools/vertex-trailing-turn-reapply.ps1`) + chunk list.
- `docs/handoff/2026-09-30-lanePatchFix.md` — new evidence doc for all four discrepancies.
- `logs/handoff-sessions/DONE-ws-patch-fix.md` — this file.

**Commit:** `(this commit)` on `L1-backlog/ws-f1-vertex-20260930`.

## The four discrepancies — disposition

1. **`_14jycqh` unpatched** — confirmed byte-identical (21659 B, SHA256
   `69D3CD37…6040D` both sides). Docs corrected to "3 of the 4 named chunks differ; 1 is
   unpatched", and to the real six-chunk strip set.
2. **Broken reapply artifact** — `.ps1` repaired; **proved** on a throwaway pristine copy
   under both `pwsh` (run1 `Done: 13 patched, 0 skipped, 0 errors`; run2
   `Done: 0 patched, 13 skipped, 0 errors`) and Windows PowerShell 5.1 (same two lines).
   The patched `.ts` is byte-identical to live
   (`B913F3CAF19D321FB5CBB78C8BAC5C18FEDF5C291EC3594A2C186B67EA3512B4`).
3. **DeepSeek `$findA` gap in `_18ct13i._.js`** — the anchor is present once in the
   tarball and **still present live** (`findA_live = 1`, no `_ip=` marker), and the module
   is the same function with identical scope names (`A`, `p`, `t`, `g`, `n`), so the
   existing `$replaceA` applies verbatim. `_18ct13i` is a deployed, referenced chunk
   (dynamic chunk group `[root-of-the-server]__1o4i3qf` ← `_1y480xl` ← `middleware.js`)
   and the qwen-clamp lane patched it. **Proposal (not applied):** add `_18ct13i._.js`
   to `$patternAFiles` in `configuration/omniroute/reason-fix-reapply.ps1`.
4. **Unaccounted `_0o8_5h8`/`_0t1t5fj` +755 B** — diff-summarised: **649 B** full
   LF→CRLF rewrite + **106 B** append
   `,{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}`
   to the static output-cap array. Producer identified: the **qwen-clamp** worktree's
   untracked `logs/patch_dist.py`
   (`…\AutoOS-worktrees\AutoOS-ws-qwenclamp\logs\patch_dist.py`, 14:49 UTC), which appends
   that entry to six chunks and (via Python default newline translation) CRLF-rewrites
   `_0o8_5h8`/`_0t1t5fj`; the other four were later rewritten LF by the deepseek/vertex
   patchers. **Restart relevance:** the live gateway loads this uncharted entry; a
   `npm update` + the documented reapply scripts will not reproduce it (the durable
   surfaces express the same intent via REST/gate-un-gate instead).

## Remains / open items (hand-off)

- Fixes line may add `_18ct13i._.js` to `reason-fix-reapply.ps1` (task 3 proposal).
- Operator awareness: the scaleway static-cap entry live in six chunks has no tracked
  artifact (task 4). Decide whether to keep it, fold it into `capability-overrides.json`,
  or let a restart/reinstall drop it.
- `reason-fix-reapply.ps1` lives on `L1-backlog/ws-fixes-20260930`, not this branch.

## Reviewer (different family)

- Reviewer: `t3-reviewer` subagent, self-reported model `t3 cheap-driver-128k` (different
  provider family from this lane's `deepseek-v4.1-flash`).
- **Verdict: APPROVE.** Cited reading: the evidence doc, the repaired `.ps1`, the pristine
  chunk members + `.ts`, and the live `_14jycqh`/`_18ct13i`/`.ts` for hashes.
- Accepted: nonce-gated idempotency run (nonce `7eb2a71c…0c20` read back from this lane's
  file; run1 `13 patched, 0 skipped, 0 errors`; run2 `0 patched, 13 skipped, 0 errors`)
  and the `.ts` SHA256 match.
- Integrity caveat recorded: two earlier review attempts returned fabricated outputs
  (one cited a non-existent path; one "ran" on a directory that did not yet exist). Only
  the nonce-gated third attempt is counted; its Step-5 anchor count (0) contradicts this
  lane's own measurement (pristine `old` = 1) and is disregarded. The authoritative
  verification is this lane's own quoted commands.

## Refusals (verbatim, as given, all honoured)

> Never work or commit in `C:\Users\mauls\Documents\Code\AutoOS` (main).
> Never push/merge/rebase/checkout.

Start guard:
```
$ git rev-parse --show-toplevel
C:/Users/mauls/Documents/Code/AutoOS-worktrees/AutoOS-ws-f1
$ git status --short --branch
## L1-backlog/ws-f1-vertex-20260930
```

> Do NOT change any live package file's content and do NOT restart any gateway.

Honoured: every patch ran against `…\Temp\opencode\patchfix\{proof,proof2,proof3}` copies;
the live install was read-only. No gateway process was touched.

## Rate-limit / admission events

None. No `chat_admission_busy`, no `Rate limit exceeded` observed during this lane
(times logged elsewhere if they occur; none did).

## Model spend / rung

Not tracked by this lane (no per-call telemetry emitted). Rung: L1-backlog sub-lane.
