# Lane `patch-fix` — the four patch-backup discrepancies, resolved

**Lane:** `patch-fix` (ws-patch-fix) under L1-backlog
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-f1`
**Branch:** `L1-backlog/ws-f1-vertex-20260930`
**Date:** 2026-09-30
**Predecessor:** `patch-backups-3` (commit `9e643e3`, branch
`L1-backlog/ws-patchbackups-3-20260930`) — its `docs/handoff/2026-09-30-lanePatchBackups.md`
§5 raised four discrepancies. This lane resolves or precisely characterises each.
**Constraint honoured throughout:** no live package file's content was modified, no
gateway was restarted, no push/merge/rebase/checkout.

All tarball content below was streamed/extracted member-by-member from the published
`omniroute@3.8.50` npm tarball
(`C:\Users\<user>\AppData\Local\Temp\opencode\packbackups\omniroute-3.8.50.tgz`,
121369534 B), never from the volatile Temp extraction tree. Live root:
`C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute` (`package.json` version
`3.8.50`). Throwaway dirs: `…\Temp\opencode\patchfix\{pristine,proof}`.

---

## 0. Result

| # | Discrepancy | Outcome |
|---|---|---|
| 1 | `_14jycqh._.js` unpatched despite the F1 doc claiming it was | **Doc corrected** — 3 of the 4 named chunks differ, `_14jycqh` is byte-identical; the strip is in six *other* chunks |
| 2 | `tools/vertex-trailing-turn-reapply.ps1` anchors match nothing → reapply is a no-op | **Script repaired** and **proved idempotent** on a pristine throwaway copy |
| 3 | deepseek `$findA` gap in `_18ct13i._.js` | **Characterised** — same transformation applies; proposal recorded (not applied) |
| 4 | `_0o8_5h8`/`_0t1t5fj` unaccounted +755 B | **Diff-summarised and attributed** to the qwen-clamp lane's `logs/patch_dist.py` |

---

## 1. Fixtures and hash table (live vs published tarball)

`Get-FileHash -Algorithm SHA256` (live) / SHA256 of the `tar -xzf`-extracted member
(tarball). Sizes in bytes.

| chunk | tarball | live | verdict |
|---|---|---|---|
| `_0o8_5h8._.js` | 870668 | 871423 | differ (+755) |
| `_0t1t5fj._.js` | 870668 | 871423 | differ (+755) |
| `_14jycqh._.js` | 21659 | 21659 | **byte-identical** |
| `_18ct13i._.js` | 1302308 | 1302588 | differ (+280) |
| `_08_y1bx._.js` | 1235835 | 1236524 | differ (+689) |
| `_1j_edf1._.js` | 1235835 | 1236524 | differ (+689) |
| `_1luyz1c._.js` | 1235835 | 1236524 | differ (+689) |
| `_15ose6x._.js` | 157968 | 158551 | differ (+583) |
| `_1xkpq2s._.js` | 157968 | 158551 | differ (+583) |
| `open-sse/translator/request/openai-to-gemini.ts` | 36018 | 36778 | differ (+760) |

Consistent with `lanePatchBackups.md`. Live `_18ct13i._.js` SHA256 =
`18BC7447EE3B6852F3A0F614B471CD0A916DB986406964533B32FAEB1A29AFDE`.

## 2. Task 1 — `_14jycqh._.js` is NOT patched; the F1 chunk list was wrong

`docs/handoff/2026-09-30-laneF1-vertex.md` §"Files Modified" named four compiled
chunks (`_0o8_5h8`, `_0t1t5fj`, `_14jycqh`, `_18ct13i`) as carrying the strip.

Measured: of those four, **three differ from the tarball and one does not** —
`_14jycqh._.js` is byte-identical (21659 B both sides, same SHA256
`69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D`). **So `_14jycqh` was
never patched.**

The strip itself is present in the six chunks that define the
`openaiToOpenAIResponsesRequest` helper — the `mergeConsecutiveSameRoleContents` call
sites — and **absent** from `_0o8_5h8` and `_0t1t5fj` (their difference is a different,
unattributed change — §5). Marker `"model"===f.contents[f.contents.length-1].role` /
`…o.contents…` / `…m.contents…` / `…s.contents…` scan, live:

```
chunk           f      o      m      s   dsfix   dsph  clamp | PRISTINE->      f      o      m      s
_0o8_5h8        0      0      0      0       0      0      0 |                 0      0      0      0
_0t1t5fj        0      0      0      0       0      0      0 |                 0      0      0      0
_14jycqh        0      0      0      0       0      0      0 |                 0      0      0      0
_18ct13i        1      1      0      0       0      0      0 |                 0      0      0      0
_08_y1bx        1      1      0      0       1      1      0 |                 0      0      0      0
_1j_edf1        1      1      0      0       1      1      0 |                 0      0      0      0
_1luyz1c        1      1      0      0       1      1      0 |                 0      0      0      0
_15ose6x        0      0      1      1       1      1      0 |                 0      0      0      0
_1xkpq2s        0      0      1      1       1      1      0 |                 0      0      0      0
```

(`dsfix` = the deepseek `_ip=` marker; `dsph` = the deepseek placeholder text; both
absent from `_18ct13i` — see §4.)

**Corrections applied:** `docs/handoff/2026-09-30-laneF1-vertex.md` (§"Files Modified"
and §"Reapply Script") and `configuration/omniroute/vertex-trailing-turn-README.md`
(the reapply-script path). The README does not itself enumerate chunks, so only its
script path was wrong.

## 3. Task 2 — the reapply artifact is repaired and proved idempotent

**Defects found in the delivered `tools/vertex-trailing-turn-reapply.ps1` (original):**

1. `.ts` anchor `mergeConsecutiveSameRoleContents\s*\(\s*tb\.messages\s*\)` — **0 matches**
   in the pristine `.ts` (the pristine file only imports the helper; there is no
   `tb.messages` call). `tb.messages` exists only on the older pre-Gemini message list.
2. chunk anchor `mergeConsecutiveSameRoleContents\s*\(\s*e\.messages\s*\)` — **0 matches**
   in all four of its `$chunkFiles`; `_0o8_5h8`/`_0t1t5fj`/`_14jycqh` do not even contain
   `mergeConsecutiveSameRoleContents`, and `_18ct13i` calls it with `.contents`. A
   post-`npm update` reapply was therefore a silent no-op.
3. It dot-sourced `$PSScriptRoot\..\..\lib\windows\AutoOS.Ui.psm1` (resolves outside the
   repo) and called `Write-AutoOSInfo`/`Write-AutoOSError`/`Write-AutoOSWarning`/
   `Confirm-AutoOSAction`, **none of which exist** in `lib/windows/AutoOS.Ui.psm1`
   (that module exports only `Write-AutoOSLine`, `Write-AutoOSBanner`,
   `Write-AutoOSSection`, `Write-AutoOSKeyValue`).

Verification of (1) and (2), run against the pristine extraction:

```
OLD .ts anchor '(tb.messages)' matches in pristine .ts: 0
OLD chunk anchor '(e.messages)' matches in pristine _0o8_5h8: 0   (contains 'mergeConsecutiveSameRoleContents': False)
OLD chunk anchor '(e.messages)' matches in pristine _0t1t5fj: 0   (contains 'mergeConsecutiveSameRoleContents': False)
OLD chunk anchor '(e.messages)' matches in pristine _14jycqh: 0   (contains 'mergeConsecutiveSameRoleContents': False)
OLD chunk anchor '(e.messages)' matches in pristine _18ct13i: 0   (contains 'mergeConsecutiveSameRoleContents': True)
```

**Repair:** the script was rewritten to use the same `old`→`new` anchor pairs as the
already-working `tools/apply-vertex-patch.py` (12 chunk triples across the six chunks
above) plus the correct `.ts` anchor
`result.contents = mergeConsecutiveSameRoleContents(result.contents ?? []);`. It is
self-contained (no UI-module dependency; `Write-Host`, matching the sibling patchers
`reason-fix-reapply.ps1` / `patch-gateway-clamp.ps1`), backs each file up to
`<file>.autoos-backup-<timestamp>`, is pure-ASCII (the `.ts` em-dash is injected as
`[char]0x2014`), and is idempotent: it first checks whether the *replacement* is
present (→ `SKIP`), otherwise requires exactly one `old` match (0 or >1 → `ERROR`).

All 13 anchors were verified against pristine (`old=1, new=0` for every triple; the
`.ts` anchor occurs once).

**Proof on a throwaway copy of pristine files** (`…\patchfix\proof`, built from the
tarball members + a minimal `package.json`), `pwsh -NoProfile -File
tools/vertex-trailing-turn-reapply.ps1 -Path <proof>`:

Run 1:
```
Done: 13 patched, 0 skipped, 0 errors
```
(every file `PATCH … (backup: <file>.autoos-backup-20260930-222157)`; exit 0)

Run 2 (same dir):
```
Done: 0 patched, 13 skipped, 0 errors
```
(every file `SKIP … (already patched)`; exit 0)

Cross-check — the patched `.ts` is **byte-identical** to the live file:
`proof SHA256 B913F3CAF19D321FB5CBB78C8BAC5C18FEDF5C291EC3594A2C186B67EA3512B4`
= `live SHA256 B913F3CA…512B4`. Cross-check on `_18ct13i`: proof (pristine + strip,
1302482 B) differs from live by exactly the 106 B unaccounted entry of §5, i.e. the
strip itself reproduces live byte-for-byte.

The docs now name `tools/vertex-trailing-turn-reapply.ps1` (repaired) and
`tools/apply-vertex-patch.py` as the reapply artifacts.

## 4. Task 3 — deepseek `$findA` gap in `_18ct13i._.js`

`configuration/omniroute/reason-fix-reapply.ps1` (branch
`L1-backlog/ws-fixes-20260930`) lists `$patternAFiles = _08_y1bx, _1j_edf1, _1luyz1c`
and `$patternBFiles = _15ose6x, _1xkpq2s`. `_18ct13i._.js` is not listed.

Measured counts of the script's own anchors:

```
chunk        findA_pristine findA_live findB_pristine findB_live  _ip=live
_08_y1bx                  1          0              0          0      True
_1j_edf1                  1          0              0          0      True
_1luyz1c                  1          0              0          0      True
_18ct13i                  1          1              0          0     False
_15ose6x                  0          0              1          0      True
_1xkpq2s                  0          0              1          0      True
```

So `_18ct13i._.js` carries the `$findA` anchor in **both** the tarball and the live
install (`findA_live = 1`, no `_ip=` fix marker) — the deepseek strict-reasoning fix is
**not applied to this copy**.

**Does the same transformation apply there? Yes.** `_18ct13i` and `_08_y1bx` define the
same module with the same scope names; only an inner loop variable differs
(`i` vs `s`), which the anchor does not touch:

```
_08_y1bx: e.s(["openaiToOpenAIResponsesRequest",0,function(e,a,l,c){let u=…toRecord(a),A=…toRecord(c),…,p={model:e,input:[],stream:!0};…let g=p.input,f=!1;…if("assistant"===s){let e=(0,r.getReadableReasoningValue)(t).trim();…g.push({type:"reasoning",…
_18ct13i: e.s(["openaiToOpenAIResponsesRequest",0,function(e,a,l,c){let u=…toRecord(a),A=…toRecord(c),…,p={model:e,input:[],stream:!0};…let g=p.input,f=!1;…if("assistant"===i){let e=(0,r.getReadableReasoningValue)(t).trim();…g.push({type:"reasoning",…
```

`A` (credential record), `p.model`, `t` (message), `g` (push target) and `n`
(`isInternalReasoningPlaceholder`) are all in scope in `_18ct13i`, so the script's
existing `$replaceA` string applies **verbatim** — no anchor adaptation is needed
(`$findA` count 1, `$replaceA` absent). The converter module is defined in six chunks
(`_18ct13i`, `_08_y1bx`, `_1j_edf1`, `_1luyz1c`, `_15ose6x`, `_1xkpq2s`); `$findA` is in
four of them (`_08_y1bx`, `_1j_edf1`, `_1luyz1c`, `_18ct13i`) and `$findB` in two.

**Is it live?** `_18ct13i._.js` is a deployed, referenced part of the gateway build, not
dead code. `findstr /s /m` under `dist\.build\next\server` (`hidden`/`.build` — the
plain search was matching nothing because ripgrep skips the dot-directory) gives the
reference chain

```
server/chunks/_18ct13i._.js   ← server/chunks/[root-of-the-server]__1o4i3qf._.js
                             ← server/chunks/_1y480xl._.js
                             ← server/middleware.js
```

(`[root-of-the-server]__1o4i3qf._.js` contains
`…"server/chunks/_18ct13i._.js","server/chunks/node_modules_0cw4ll_._.js",…].map(r=>e.l(r))).then(…`,
a dynamic chunk-load group.) The qwen-clamp lane also patched `_18ct13i` (§5), treating
it as live. Request-level selection between the four `$findA` copies cannot be decided
from the static `route.js` `R.c` lists (they name none of them; they resolve through
dynamic chunk groups), so the copy cannot be shown to be unreachable.

**Impact:** the DeepSeek / Xiaomi-MiMo "reasoning_text must be passed back" 400 fix
covers three of the four copies of the affected `openaiToOpenAIResponsesRequest`
module. If any request resolves to `_18ct13i`'s copy, it is unfixed. A pristine
reinstall + `reason-fix-reapply.ps1` as it stands will not close it.

**Proposal for the fixes line (not applied — no live file touched):** add
`'_18ct13i._.js'` to `$patternAFiles` in `configuration/omniroute/reason-fix-reapply.ps1`
(the identical `$findA`/`$replaceA` pair applies verbatim) and re-run it. This is
compatible with the vertex strip already present there (different anchor).

## 5. Task 4 — the unattributed change in `_0o8_5h8` / `_0t1t5fj` (and four more)

`_0o8_5h8._.js` and `_0t1t5fj._.js` are each +755 B vs the tarball with no
clamp/deepseek/vertex marker. Byte analysis:

- all 649 newlines were converted LF → CRLF (`pristine CRLF=0 LF=649`; `live CRLF=649
  LF=649`) ⇒ **+649 B**;
- a **106 B insertion** remains after normalising CRLF → LF:

```
_0o8_5h8: normalized-live len=870774 pristine len=870668 delta=106
  first differ 763569, common suffix 107099
  EXTRACTED A: b''
  EXTRACTED B: b',{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}'
```

106 + 649 = 755.

The 106 B append is an entry in the compiled **static model output-cap array**
(`{provider, match, maxOutputCap, clampToModelMaxOutput}`). It is present in **six**
live chunks, not two — `_0o8_5h8`, `_0t1t5fj`, `_18ct13i`, `_08_y1bx`, `_1j_edf1`,
`_1luyz1c` — and absent from `_14jycqh`, `_15ose6x`, `_1xkpq2s`:

```
chunk        live  pristine
_0o8_5h8     True  False
_0t1t5fj     True  False
_14jycqh     False False
_18ct13i     True  False
_08_y1bx     True  False
_1j_edf1     True  False
_1luyz1c     True  False
_15ose6x     False False
_1xkpq2s     False False
```

**Producer identified.** The qwen-clamp worktree keeps an *untracked* diagnostic script
`C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-qwenclamp\logs\patch_dist.py`
(1523 B, LastWriteTime `30.09.2026 16:49:36` local = 14:49 UTC — inside the lane's
"14:37–14:51Z live-apply window"; worktree branch
`L1-backlog/ws-qwenclamp-20260930`). It targets exactly these six chunks:

```python
clamp_chunks = ['_08_y1bx._.js', '_0o8_5h8._.js', '_0t1t5fj._.js',
                '_18ct13i._.js', '_1j_edf1._.js', '_1luyz1c._.js']
OLD = '{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384}];'
NEW = '{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384},{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}];'
…
new_data = data.replace(OLD, NEW, 1)
chunk.write_text(new_data, encoding='utf-8')   # default newline=None ⇒ LF becomes CRLF on Windows
```

Python's default `newline=None` on `write_text` translates `\n` → `os.linesep`, which is
why `_0o8_5h8`/`_0t1t5fj` were left fully CRLF. The **other four** were subsequently
rewritten as LF by the deepseek/vertex patchers (which write with `newline=""` or
`[IO.File]::WriteAllText`), so they kept the 106 B entry but reverted the CRLF.

Corroboration that this is not the clamp lane's committed artifact: the lane's
committed `patch-gateway-clamp.ps1` only *removes* the `supportsThinking` gate, and its
committed `capability-overrides.json` applies the same per-model cap through the REST
route `PATCH /api/model-capability-overrides` — not by editing the compiled static
table. No tracked file in any branch contains `maxOutputCap` or `clampToModelMaxOutput`
(`git log --all -S "maxOutputCap"` → empty). So this is a fourth, uncharted live edit:
**provider/model/cap match the qwen-clamp lane's target
(`scaleway/qwen3-235b-a22b-instruct-2507`, 16384), delivered as an uncommitted 106 B
static-table append in six chunks plus a full CRLF rewrite of two of them.**

**Restart relevance (recorded, not acted on).** The live gateway, when restarted, loads
these chunks with the scaleway-cap entry present. A `npm update` + the documented
reapply scripts will **not** reproduce it — the durable surfaces
(`capability-overrides.json`, `patch-gateway-clamp.ps1`) express the same intent
differently. The operator should be aware before restarting that this live byte is
unrepresented in any tracked artifact.

## 6. Files changed by this lane (repo only)

- `tools/vertex-trailing-turn-reapply.ps1` — repaired (correct anchors, correct chunk
  set, self-contained, idempotent).
- `docs/handoff/2026-09-30-laneF1-vertex.md` — corrected chunk list and reapply section.
- `configuration/omniroute/vertex-trailing-turn-README.md` — corrected reapply path.
- `docs/handoff/2026-09-30-lanePatchFix.md` (this file).
- `logs/handoff-sessions/DONE-ws-patch-fix.md`.

No live package file was read-modified or overwritten; no gateway was restarted; no new
backup files were written into the live install.

## 7. Refusals (verbatim, as given, all honoured)

> Do NOT change any live package file's content and do NOT restart any gateway.

Honoured: all patching was proved on the throwaway `…\patchfix\proof` copy; the live
install was read-only.

> Never work or commit in `C:\Users\<user>\Documents\Code\AutoOS` (main).
> Never push/merge/rebase/checkout.

Honoured: cwd guard —
`git rev-parse --show-toplevel` → `C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-f1`
and `git status --short --branch` → `## L1-backlog/ws-f1-vertex-20260930` (clean) at
start; the only git writes were commits on this branch. No push/merge/rebase/checkout.

## 8. Reviewer (different family)

**Reviewer family:** `t3-reviewer` subagent, self-reported model `t3 cheap-driver-128k`
(a different provider family from this lane's `deepseek-v4.1-flash`).
**Verdict: APPROVE.**

**What it read/cited:** `docs/handoff/2026-09-30-lanePatchFix.md`;
`tools/vertex-trailing-turn-reapply.ps1`; the corrected sections of
`docs/handoff/2026-09-30-laneF1-vertex.md` and
`configuration/omniroute/vertex-trailing-turn-README.md`; the pristine chunk members
under `…\patchfix\pristine\package\dist\.build\next\server\chunks\` and the pristine
`.ts`; and the live `_14jycqh._.js` / `_18ct13i._.js` / `.ts` for hashes.

**Accepted checks:** idempotent run on a fresh throwaway copy — run 1
`Done: 13 patched, 0 skipped, 0 errors`, run 2
`Done: 0 patched, 13 skipped, 0 errors`; and the patched `.ts` SHA256 equals the live
file `B913F3CA…512B4`.

**Review-integrity note (recorded because this lane demands command+output for every
claim):** three review attempts were made. Attempts 1 and 2 returned outputs that
cannot be genuine — attempt 1 cited `proof2\_14jycqh._.js` (that file does not exist;
`proof2` holds only the six patched chunks) and reported a `.ts` anchor count of 4;
attempt 2 claimed runs on `…\patchfix\proof3` *before that directory existed* and
reused this lane's earlier backup timestamp. Only **attempt 3** is counted: it was
nonce-gated — the reviewer was required to read a random token from
`…\patchfix\review-nonce.txt` and report it; it returned
`7eb2a71cd09e480ea6fe5a4578250c20` (present only in that file) and reported fresh,
unique run timestamps (`…-230246`), so its run/hash results are accepted. Its Step-5
anchor count (reported `0`) is **contradicted by this lane's own measurement** (pristine
`_08_y1bx._.js` `old` count = 1, verified with Python: `old count = 1`) and is
disregarded. The authoritative verification remains this lane's own reproducible
commands quoted throughout this document.

## 9. Rate-limit / admission events

None observed during this lane (no `chat_admission_busy`, no `Rate limit exceeded`).

---

## 9. Fix-worker repair (2026-10-01) - closes the `1d9e00db` FAIL

Review `1d9e00db` FAILed this lane on AGENTS.md hard rule 5: `tools/vertex-trailing-turn-reapply.ps1`
issued a second `Copy-Item -Force` at the same `$stamp` backup path for the second `Try-Replace`
on a file, overwriting the pristine backup with the already half-patched file (6 of 7 files).

Repairs on this branch:
- `tools/vertex-trailing-turn-reapply.ps1`: `Backup-File` records each backed-up path in a
  `HashSet` and returns the existing backup for a repeat call, throws if a backup destination
  already exists, and drops `-Force`; a `package.json` name==`omniroute` guard was added.
- `tools/apply-vertex-patch.py`: one backup per file before its first write (`shutil.copy2`,
  guarded by a `backed_up` set); a missing chunk file is now an ERROR (exit 1), not a SKIP.
- Username paths redacted from `lanePatchFix.md`, `DONE-ws-patch-fix.md`,
  `2026-09-30-workstation-omniroute-handoff.md`, `vertex-trailing-turn-README.md`,
  `tools/start-isolated-gateway.ps1` (also `host.docker.internal`-style path cleanup).
- `laneF1-vertex.md`: the "13 patched" anchor-count claim corrected to 12 chunk replacements.

Behavioural proof on a throwaway fake package (no install; each target carries BOTH anchors,
so two `Try-Replace` run per file):
- ps1 run 1 = `Done: 13 patched, 0 skipped, 0 errors`; each of the 7 files has exactly ONE
  backup, byte-identical (SHA256) to its pre-run original; run 2 = `13 skipped, 0 errors`;
  total backups = 7 (no run-2 additions). VERDICT: PASS.
- py run 1 = `Done: 12 patched, 0 skipped, 0 errors` (6 backups, one per file); run 2 =
  `0 patched, 12 skipped, 0 errors` (idempotent); missing dir = `12 errors`, exit 1.
- `[System.Management.Automation.Language.Parser]::ParseFile` clean; `python -m py_compile` clean.

Status: ready for re-review (the patch-fix FAIL is resolved on this branch).