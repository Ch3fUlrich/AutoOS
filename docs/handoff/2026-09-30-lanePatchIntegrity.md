# Lane `patch-integrity` — complete revert/reapply set for the three live patch families

**Lane:** `patch-integrity` (ws-patch-integrity) under L1-backlog
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-fixes`
**Branch:** `L1-backlog/ws-fixes-20260930` (start tip `b49f9e4`)
**Date:** 2026-09-30
**Predecessor:** `patch-fix` (commit `6f93452`, branch `L1-backlog/ws-f1-vertex-20260930`) —
its `docs/handoff/2026-09-30-lanePatchFix.md` §4/§5 left two gaps for a *complete*
pre-restart revert/reapply set. This lane closes both.
**Constraint honoured throughout:** no live package file was modified, no gateway was
restarted, no push/merge/rebase/checkout. All patching was proved on throwaway copies of
the published `omniroute@3.8.50` npm tarball members.

---

## 0. Result — does each patch family now have a complete revert + reapply path?

| Family | Revert path | Reapply path | Complete? |
|---|---|---|---|
| **clamp** (scaleway qwen3-235b output cap) | `npm update omniroute`, or restore `<file>.autoos-backup-*` | **NEW** `configuration/omniroute/qwen-clamp-reapply.ps1` (this lane) — reproduces the six-chunk static-table insertion; proved run1/run2 under pwsh + PS 5.1 | **YES** for the compiled-chunk edit. The REST-route expression (`capability-overrides.json` + `apply-capability-overrides.ps1`) and the gate removal (`patch-gateway-clamp.ps1`) live on `L1-backlog/ws-qwenclamp-20260930`. |
| **deepseek** (reasoning_text 400) | `npm update omniroute`, or restore backups | `configuration/omniroute/reason-fix-reapply.ps1` — **repaired here** (`_18ct13i._.js` added; UTF-8 BOM added) | **YES** for the compiled chunks (all 5 copies). The two `.ts` source files are **NOT** reproduced byte-for-byte (see §5) — source-tree only, not loaded at runtime. |
| **vertex** (trailing model turn 400) | `npm update omniroute`, or restore backups | `tools/vertex-trailing-turn-reapply.ps1` — repaired by `patch-fix` (`6f93452`); shown idempotent + byte-complete here | **YES** (runtime + `.ts`). Artifact lives on `L1-backlog/ws-f1-vertex-20260930`, **not merged into this branch**. |

**Compositional proof** (§4): pristine tarball + clamp + deepseek + vertex reproduces the
live install byte-for-byte (CRLF-normalised) on **8 of 9** chunks and on
`openai-to-gemini.ts`; the single `_18ct13i._.js` difference is exactly the deepseek fix
that live is missing and this lane adds. The three reapply scripts are mutually
independent and jointly idempotent under both `pwsh` 7.5.8 and Windows PowerShell 5.1.

---

## 1. Cwd guard (verbatim)

```
PS> git rev-parse --show-toplevel
C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-fixes
PS> git status --short --branch
## L1-backlog/ws-fixes-20260930
PS> git log -1 --oneline
b49f9e4 docs: commit handoff doc edit + DONE note for ws-fixes lane
```

`C:\Users\<user>\Documents\Code\AutoOS` (main) was never written to; its pre-existing
uncommitted changes are other lanes' and are untouched by this lane.

### Method note (host deletes Temp extraction trees)

The host removes Temp extraction directories, so tarball members were streamed/extracted
directly into a lane-owned throwaway tree with `tar`:

```
PS> tar -xzf <...>\packbackups\omniroute-3.8.50.tgz -C <...>\patchintegrity\pristine `
      package/dist/.build/next/server/chunks/_08_y1bx._.js ` ... 9 chunks + 2 .ts + package.json
exit=0
```

Throwaway root: `C:\Users\<user>\AppData\Local\Temp\opencode\patchintegrity`
(tarball `...\packbackups\omniroute-3.8.50.tgz`, 121369534 B).

---

## 2. Task 1 — the deepseek `$findA` gap in `_18ct13i._.js`

### 2a. Measurement (pristine tarball member vs live), before any change

Anchor counts, `findA`/`findB` = this script's own anchors; `_ip` = its fix marker.

```
chunk          where      size   CRLF   LF  OLD NEW findA findB   sha16
_08_y1bx._.js  pristine 1235835     0  699    1   0     1     0   97729CF68F77CE10
_08_y1bx._.js  live     1236524     0  699    0   0     0     0   199EF4D865D85556
_18ct13i._.js  pristine 1302308     0  720    1   0     1     0   62DFB62695B7EE67
_18ct13i._.js  live     1302588     0  720    0   0     1     0   18BC7447EE3B6852
_15ose6x._.js  pristine  157968     0   51    0   0     0     1   E04039A5D67DCB68
_15ose6x._.js  live      158551     0   51    0   0     0     0   6DD65402CB51B160
```

`_18ct13i._.js` carries `$findA` in the tarball **and live** (findA=1 both), with no `_ip`
fix marker live — the DeepSeek fix was not applied to that copy. This confirms
`patch-fix`'s §4 finding.

### 2b. Changes to `configuration/omniroute/reason-fix-reapply.ps1`

1. Backed up first: `reason-fix-reapply.ps1.autoos-backup-20260930230905`
   (gitignored via `*.autoos-backup-*`; SHA256 of backup = source
   `01A25F5C766FD3BE6F6FC589434D676BAABE35499A4EC3731B50996B17BC0DC3`).
2. `$patternAFiles` now includes `_18ct13i._.js`:

```diff
-$patternAFiles = @('_08_y1bx._.js', '_1j_edf1._.js', '_1luyz1c._.js')
+$patternAFiles = @('_08_y1bx._.js', '_18ct13i._.js', '_1j_edf1._.js', '_1luyz1c._.js')
```

3. Header comment now records the six-copy layout and why `_18ct13i` is included.
4. `reason-fix-README.md`: "Five chunk files" → "Six chunk files"; pattern-A row gains
   `_18ct13i._.js`; "all 5 compiled `.js` chunks" → "all 6".

### 2c. A PS 5.1 parse defect found and fixed (not in the original scope)

The committed script declared `#Requires -Version 5.1` but had **no UTF-8 BOM** and 169
non-ASCII characters (box-drawing `─`, `§`). Windows PowerShell 5.1 decodes a non-BOM
file as ANSI, so it did not parse at all:

```
PS> powershell -NoProfile -File reason-fix-reapply.ps1 -PackageDir <copy>
At ...\reason-fix-reapply.ps1:75 char:58
+     $results.Add("ERROR $label (find string not found �?" version cha ...
Missing ')' in method call. ...  PS51_RUN1_EXIT=1
```

The `patch-fix` predecessor only proved this script under `pwsh` 7 (which reads non-BOM as
UTF-8), so the defect was invisible. Fix: wrote the file as UTF-8 **with BOM** (the
repository's own rule — `.gitattributes` comment: "*a .ps1 without a UTF-8 BOM is decoded
as ANSI by Windows PowerShell 5.1*"; every tracked `.ps1`/`.psm1` in the repo carries
`EF BB BF`). Content is otherwise byte-identical to the backup.

### 2d. Proof — throwaway copies of pristine members, both shells, run 1 then run 2

`pwsh` 7.5.8:

```
=== Compiled .js chunk patches (runtime fix) ===
  PATCH chunk-A _08_y1bx._.js (backup: _08_y1bx._.js.autoos-backup-20260930231017)
  PATCH chunk-A _18ct13i._.js (backup: _18ct13i._.js.autoos-backup-20260930231017)
  PATCH chunk-A _1j_edf1._.js ...
  PATCH chunk-A _1luyz1c._.js ...
  PATCH chunk-B _15ose6x._.js ...
  PATCH chunk-B _1xkpq2s._.js ...
  PATCH index.ts requiresReasoningContentPresence ...
  PATCH index.ts patch marker ...
  PATCH toResponses.ts import ...
  PATCH toResponses.ts helper function ...
  PATCH toResponses.ts reasoningIsPlaceholder var ...
  PATCH toResponses.ts else-if branch ...
  12 patched, 0 skipped, 0 errors          (EXIT=0)
```

run 2 (same dir):

```
  0 patched, 12 skipped, 0 errors          (EXIT=0)
```

Windows PowerShell 5.1, fresh copy — same 12 patched / 0 errors, then 0 patched /
12 skipped / 0 errors (`EXIT=0`).

End-state anchor counts:

```
--- proof-pwsh (new script) ---        --- proof-old (pre-fix script) ---
_18ct13i._.js  findA=0 _ip=1            _18ct13i._.js  findA=1 _ip=0
_08_y1bx._.js  findA=0 _ip=1            _08_y1bx._.js  findA=0 _ip=1
```

The pre-fix negative control patches only 11 files (`11 patched, 0 skipped, 0 errors`) and
leaves `_18ct13i._.js` with `findA=1, _ip=0`. The repaired script patches 12 and brings
`_18ct13i._.js` to `findA=0, _ip=1` under both shells.

---

## 3. Task 2 — a tracked, idempotent clamp reapply artifact

### 3a. Producer, read read-only

`AutoOS-ws-qwenclamp/logs/patch_dist.py` (1523 B, 2026-09-30 16:49:36 local = 14:49Z,
**untracked**), verbatim OLD/NEW:

```python
clamp_chunks = ['_08_y1bx._.js', '_0o8_5h8._.js', '_0t1t5fj._.js',
                '_18ct13i._.js', '_1j_edf1._.js', '_1luyz1c._.js']
OLD = '{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384}];'
NEW = '{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384},{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}];'
new_data = data.replace(OLD, NEW, 1)
chunk.write_text(new_data, encoding='utf-8')   # default newline=None ⇒ LF -> CRLF on Windows
```

`patch_dist.py` was **not** modified; its behaviour was ported. Two notes on it: its
`VERIFY_NEW` dropped the closing `/` of the regex (so its rerun check could never match),
and it was not idempotent (`OLD` count becomes 0 after patching ⇒ its rerun reports
`ERROR … pattern found 0 times`).

### 3b. New files under `configuration/omniroute/`

* `qwen-clamp-reapply.ps1` — pure-ASCII, UTF-8 BOM, `#Requires -Version 5.1`,
  `-PackageDir` (default `npm root -g`) + `-DryRun`. For each of the six chunks:
  `SKIP` if the scaleway entry is present; else `PATCH` only if the anchor occurs exactly
  once (else `ERROR`), backing the file up to `<file>.autoos-backup-<ts>`, then
  re-verifying the written file. Exits non-zero on any error. It preserves each file's
  existing line endings (reads and writes text without normalisation).
* `qwen-clamp-reapply-README.md` — the why, the OLD/NEW, the six chunks, provenance, the
  relationship to the other two clamp surfaces, and the **LF → CRLF** explanation.

### 3c. Proof — throwaway copies of pristine members, both shells, run 1 then run 2

`pwsh` 7.5.8:

```
=== Compiled static output-cap array (qwen3-235b clamp) ===
  PATCH _08_y1bx._.js (backup: _08_y1bx._.js.autoos-backup-20260930-231209)
  PATCH _0o8_5h8._.js ...
  PATCH _0t1t5fj._.js ...
  PATCH _18ct13i._.js ...
  PATCH _1j_edf1._.js ...
  PATCH _1luyz1c._.js ...
  6 patched, 0 skipped, 0 errors          (EXIT=0)
```

run 2: `0 patched, 6 skipped, 0 errors` (EXIT=0). `-DryRun` on a fresh copy printed
`DRYRUN` for all six and left no backup files (directory untouched: `True`).
Windows PowerShell 5.1, fresh copy: `6 patched, 0 skipped, 0 errors` then
`0 patched, 6 skipped, 0 errors` (both EXIT=0).

Presence of the entry — exactly the six chunks, in proof and live, absent in pristine and
in the other three:

```
_08_y1bx._.js  pristine=False  proof=True   live=True
_0o8_5h8._.js  pristine=False  proof=True   live=True
_0t1t5fj._.js  pristine=False  proof=True   live=True
_14jycqh._.js  pristine=False  proof=False  live=False
_15ose6x._.js  pristine=False  proof=False  live=False
_18ct13i._.js  pristine=False  proof=True   live=True
_1j_edf1._.js  pristine=False  proof=True   live=True
_1luyz1c._.js  pristine=False  proof=True   live=True
_1xkpq2s._.js  pristine=False  proof=False  live=False
```

Byte cross-check on the two clamp-only chunks (where live carries nothing else), after
CRLF normalisation:

```
_0o8_5h8._.js  norm-live==proof: True   sha16=CE4F379FE80EAEC6
_0t1t5fj._.js  norm-live==proof: True   sha16=7680F399BC3EC282
```

i.e. the 106 B insertion reproduces the live bytes exactly.

---

## 4. Compositional cross-check — the whole set rebuilds the live install

Pristine tarball members → `qwen-clamp-reapply.ps1` → `reason-fix-reapply.ps1` →
`tools/vertex-trailing-turn-reapply.ps1`, then compare (CRLF-normalised) to live:

```
chunk          match   proof_len  live_len  sha16(proof)      sha16(live)
_08_y1bx._.js  True     1236524    1236524   199EF4D865D85556  199EF4D865D85556
_0o8_5h8._.js  True      870774     870774   CE4F379FE80EAEC6  CE4F379FE80EAEC6
_0t1t5fj._.js  True      870774     870774   7680F399BC3EC282  7680F399BC3EC282
_14jycqh._.js  True       21659      21659   69D3CD37D3C7BF7F  69D3CD37D3C7BF7F
_15ose6x._.js  True      158551     158551   6DD65402CB51B160  6DD65402CB51B160
_18ct13i._.js  False    1302997    1302588   947D1EFAA6F13FE9  18BC7447EE3B6852
_1j_edf1._.js  True     1236524    1236524   643A0661B156EE24  643A0661B156EE24
_1luyz1c._.js  True     1236524    1236524   2834DAC905481C48  2834DAC905481C48
_1xkpq2s._.js  True      158551     158551   EF7137A80E09B88C  EF7137A80E09B88C

open-sse/translator/request/openai-to-gemini.ts  match=True   36778 / 36778
```

The one mismatch is the point of this lane: `_18ct13i._.js` in the composed copy is
**1302997 B = live 1302588 B + 409 B**, the size of the `$findA`→`$replaceA` DeepSeek
transform. Live lacks it; the repaired reapply adds it. (Element deltas, measured:
clamp 106 B, deepseek 409 B, vertex 174 B.)

Combined run 2 (all three scripts already applied) reports all-skipped under both shells:

```
clamp    : 0 patched, 6 skipped, 0 errors
deepseek : 0 patched, 12 skipped, 0 errors
vertex   : Done: 0 patched, 13 skipped, 0 errors
```

---

## 5. Verdict detail and what remains

* **clamp — complete.** Revert: `npm update omniroute` (or restore `.autoos-backup-*`).
  Reapply: `qwen-clamp-reapply.ps1` (new, this lane) + the qwenclamp lane's
  `apply-capability-overrides.ps1`/`capability-overrides.json` (REST route) and
  `patch-gateway-clamp.ps1` (gate removal). All three are required for the full
  behaviour; the compiled-array edit is now tracked. **Remaining:** the qwenclamp
  artifacts live on `L1-backlog/ws-qwenclamp-20260930` (not merged here).
* **deepseek — complete for the runtime chunks.** Reapply `reason-fix-reapply.ps1` now
  covers all five copies of the module (`_08_y1bx`, `_18ct13i`, `_1j_edf1`, `_1luyz1c`
  pattern A; `_15ose6x`, `_1xkpq2s` pattern B). **Remaining (recorded, not fixed — out of
  scope):** the two `.ts` source files are *not* reproduced byte-for-byte. The live
  `index.ts` is 135 B larger and the live `toResponses.ts` 803 B larger than the script's
  output (the live `index.ts` carries a revised comment describing the "DeepSeek V4 also
  400s" finding and an extra `const presenceRequired = …`, which the script does not
  emit). These `.ts` files are source-tree-only and are **not loaded at runtime**, so the
  runtime fix is complete; a maintainer re-syncing the source tree should reconcile them.
  Also note the `.ts` reapply had never been validated on Windows PowerShell 5.1 before
  this lane (§2c).
* **vertex — complete.** `tools/vertex-trailing-turn-reapply.ps1` (repaired in `6f93452`)
  rebuilds `openai-to-gemini.ts` and all six chunks byte-for-byte (verified above);
  idempotent under both shells. **Remaining:** that artifact is on
  `L1-backlog/ws-f1-vertex-20260930` and is not merged into this branch.

---

## 6. Files changed by this lane (repo only)

* `configuration/omniroute/reason-fix-reapply.ps1` — `_18ct13i._.js` added; UTF-8 BOM added.
* `configuration/omniroute/reason-fix-README.md` — six-chunk correction.
* `configuration/omniroute/qwen-clamp-reapply.ps1` — **new** tracked reapply artifact.
* `configuration/omniroute/qwen-clamp-reapply-README.md` — **new** documentation.
* `docs/handoff/2026-09-30-lanePatchIntegrity.md` — this file.
* `logs/handoff-sessions/DONE-ws-patch-integrity.md` — DONE note (`git add -f`).
* `configuration/omniroute/reason-fix-reapply.ps1.autoos-backup-20260930230905` — backup,
  gitignored, deliberately not committed.

No live package file was read-modified or overwritten; no gateway was restarted; no new
backup files were written into the live install.

`Invoke-ScriptAnalyzer` (1.25.0) on the new script reports only `PSAvoidUsingWriteHost`
warnings — identical to the pre-existing sibling patch scripts
(`reason-fix-reapply.ps1`, `patch-gateway-clamp.ps1`), which use `Write-Host` deliberately
to stay self-contained (no UI-module dependency). No new rule class introduced.

---

## 7. Refusals (verbatim, as given, all honoured)

> do NOT modify the live package; no gateway restart

Honoured: every patch was applied only to throwaway copies of the pristine tarball
members under `…\Temp\opencode\patchintegrity\`; the live install was read-only.

> Never work or commit in `C:\Users\<user>\Documents\Code\AutoOS` (main).
> Never push/merge/rebase/checkout.

Honoured: cwd guard in §1; the only git writes were commits on this branch.

---

## 8. Reviewer (different family, nonce-gated)

**Review gate nonce (read it back to prove the file was actually read):**
`64dd1013a5d04ba7d6ccaaea7b88e0ef`

The reviewer must quote that exact value. Review-integrity note: the predecessor lane
(`patch-fix`) had two reviewer spawns return **fabricated** outputs, so this lane gates the
review on the nonce above (present only in this file) and on fresh run timestamps.

### Verdict

**APPROVE** — family `t3-reviewer` (self-reported model `t3 cheap-driver-128k`; a different
provider family from this lane's `deepseek-v4.1-flash`).

**Accepted (review attempt 2 — clean-room, uniquely-named dirs `rev2c-*`).** It reported
the nonce correctly and reproduced, independently:

* clamp pristine check: `_0o8_5h8._.js` does not contain `scaleway`;
  run 1 `6 patched, 0 skipped, 0 errors` (backup ts `20261001-012615`);
  run 2 `0 patched, 6 skipped, 0 errors`.
* deepseek pristine `_18ct13i._.js` anchor count = **1**, marker = **0**;
  run 1 `12 patched, 0 skipped, 0 errors` (backup ts `20261001012659`);
  run 2 `0 patched, 12 skipped, 0 errors`; after run 1 anchor = **0**, marker = **1**.
* Windows PowerShell 5.1: run 1 `12 patched, 0 skipped, 0 errors` (backup ts
  `20261001012838`), run 2 `0 patched, 12 skipped, 0 errors`.

These match this lane's own reproducible measurements exactly.

**Review-integrity note (as demanded for every claim in this lane).** The first review
attempt also returned the correct nonce and a valid PS 5.1 run (`…011445`), but its
"clean-room" copies were leftovers of the pre-restart attempt: `$TD\rev-clamp` and
`$TD\rev-ds` already existed and were patched, so its clamp/deepseek run 1 showed `SKIP`
and it claimed a pristine `_18ct13i` anchor count of 0 — contradicted by this lane's own
measurement (`Select-String -SimpleMatch … 'e&&!(0,n.isInternalReasoningPlaceholder)(e)&&g.push('`
→ 1 in pristine; the reviewer's first-attempt count was wrong). Attempt 1 is therefore
**partially disregarded**; only its nonce read-back and PS 5.1 run are counted. Attempt 2,
whose instructions forced fresh unique directories and a pristine pre-check, is the
authoritative review and is accepted in full. Both attempts independently confirmed the
UTF-8 BOM fix makes the deepseek script parse under Windows PowerShell 5.1.


