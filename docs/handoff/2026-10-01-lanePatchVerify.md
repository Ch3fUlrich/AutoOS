# Lane `patch-verify` — pre-restart verification of the three cancelled patch lanes (R1 + patch-live + functional proof)

**Lane:** `patch-verify` (ws-patch-verify)
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-patchr1`
**Branch:** `L1-backlog/ws-patchr1-20260930` (base `origin/main` = `main` = `08bd972`)
**Date:** 2026-10-01
**Scope:** three lanes were **cancelled mid-work** (external), leaving state that must be
verified or completed before the `:20128` gateway restart. This lane verifies each and gives
a single **GO/NO-GO**.

**Reviewer nonce (the reviewer must echo this back verbatim to prove the file was read):**
`PV-20261001-4H7K-9Q2W-B6XD`

---

## 0. Cwd guard (verbatim)

```
PS> git rev-parse --show-toplevel
C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-patchr1
PS> git branch --show-current
L1-backlog/ws-patchr1-20260930
PS> git log -1 --oneline
e58274a Take L2-general/fallback-phase1 0615d16: FALLBACK phase-1 probe + facts (D-147 key lookup, systemd env pass, output-token cap)
```

`C:\Users\<user>\Documents\Code\AutoOS` (main) was **never** written to. No push, merge,
rebase or checkout was run.

**Live install root:** `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute`
(`package.json` version `3.8.50`).
**Certified tarball:** `C:\Users\<user>\AppData\Local\Temp\opencode\packbackups\omniroute-3.8.50.tgz`.

### 0.1 Tarball is the certified vendor artefact (re-verified here)

```
$ npm view omniroute@3.8.50 dist.integrity dist.shasum version
dist.integrity = 'sha512-qK6REDWQYGh8lwGwDgFMsBqAMXnxIePudr8cSuSYeB9iIlywNhDJxHKt6Cwa31lPci8jXE5bbvl+az0lvyt0Mg=='
dist.shasum     = 'd7b4fce4f1b00e5e826b76855665dfae42aab97a'
version         = '3.8.50'

$ python -c "… sha512/sha1 of the tgz …"
size     : 121369534
sha512   : sha512-qK6REDWQYGh8lwGwDgFMsBqAMXnxIePudr8cSuSYeB9iIlywNhDJxHKt6Cwa31lPci8jXE5bbvl+az0lvyt0Mg==
sha1     : d7b4fce4f1b00e5e826b76855665dfae42aab97a
```

`sha512` and `sha1` both match the registry → the tarball is the published 3.8.50 source.

---

## 1. Lane R1 (`patch-r1`) — the two installed pristine backups are genuine

The cancelled R1 lane installed two `*.autoos-backup-pristine-3.8.50-20261001-002911` files
for the two patched files that had no backup (`V-activate` finding R1). Both were
**streamed out of the certified tarball** and hashed here.

```
$ Get-Item <live>.autoos-backup-pristine-3.8.50-20261001-002911 ; Get-FileHash -Algorithm SHA256
open-sse/config/providers/registry/scaleway/index.ts.autoos-backup-pristine-3.8.50-20261001-002911
  length 837    sha256 BF39CEF1FD8E252850452ED8C18B760F3BCB1637B99969AA8B6055F343A0B40F
open-sse/translator/paramSupport.ts.autoos-backup-pristine-3.8.50-20261001-002911
  length 10765  sha256 806D530225FEBBFD913110B336D42AC06D7202677FF2145BBCB27B11A9B4D049

$ tar -xzf <tgz> package/open-sse/config/providers/registry/scaleway/index.ts package/open-sse/translator/paramSupport.ts ; Get-FileHash
tarball scaleway/index.ts  len=837   sha=BF39CEF1FD8E252850452ED8C18B760F3BCB1637B99969AA8B6055F343A0B40F
tarball paramSupport.ts    len=10765 sha=806D530225FEBBFD913110B336D42AC06D7202677FF2145BBCB27B11A9B4D049
```

| file | tarball SHA256 | **installed backup SHA256** | size | backup == tarball |
|---|---|---|---|---|
| `open-sse/config/providers/registry/scaleway/index.ts` | `BF39CEF1…343A0B40F` | `BF39CEF1…343A0B40F` | 837 | **YES** |
| `open-sse/translator/paramSupport.ts` | `806D5302…B27B11A9…` | `806D5302…B27B11A9…` | 10765 | **YES** |

Both installed backups are byte-identical to the certified tarball. R1's own doc
(`docs/handoff/2026-09-30-lanePatchR1.md`, untracked predecessor work) and its git-ignored
DONE note are confirmed correct by independent recomputation.

**R1 verdict: complete — the scaleway-qwen clamp now has a local file-level revert path.**
The live files remain patched (live size 861 / 11617 vs tarball 837 / 10765).

---

## 2. Lane `patch-live` — `_18ct13i._.js` is correctly and idempotently patched

The cancelled `patch-live` lane left `dist/.build/next/server/chunks/_18ct13i._.js` patched
(1,302,588 → 1,302,997 B; backups `…autoos-prelive-20261001-013028` and
`…autoos-backup-20261001013048`). Verification: re-run the deepseek reapply script
(`configuration/omniroute/reason-fix-reapply.ps1`, tip `9cb8055` of
`L1-backlog/ws-fixes-20260930`) against the live package.

### 2.1 Script provenance

```
$ Copy-Item <ws-fixes>\configuration\omniroute\reason-fix-reapply.ps1 <tmp>\reason-fix-reapply.ps1
len=13352  first3=EF-BB-BF            # UTF-8 BOM present (Windows PowerShell 5.1 parse-safe)
sha256 8EB2FBB23A772534842C70D51997C04079186AFDD30D31DD2F4102CC802ABF1B
$ git rev-parse L1-backlog/ws-fixes-20260930:configuration/omniroute/reason-fix-reapply.ps1
d94fe151b1a5add588eb97d4961f99592ab41f33
```

### 2.2 Reapply run against the live package (verbatim, `pwsh` 7)

```
$ pwsh -NoProfile -File <tmp>\reason-fix-reapply.ps1 -PackageDir C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute

=== Compiled .js chunk patches (runtime fix) ===

=== .ts source patches (source consistency) ===

=== Results ===
  SKIP  chunk-A _08_y1bx._.js (already patched)
  SKIP  chunk-A _18ct13i._.js (already patched)
  SKIP  chunk-A _1j_edf1._.js (already patched)
  SKIP  chunk-A _1luyz1c._.js (already patched)
  SKIP  chunk-B _15ose6x._.js (already patched)
  SKIP  chunk-B _1xkpq2s._.js (already patched)
  SKIP  index.ts requiresReasoningContentPresence (already patched)
  SKIP  index.ts patch marker (already present)
  SKIP  toResponses.ts import (already patched)
  SKIP  toResponses.ts helper function (already patched)
  SKIP  toResponses.ts reasoningIsPlaceholder var (already patched)
  SKIP  toResponses.ts else-if branch (already patched)

  0 patched, 12 skipped, 0 errors
EXIT=0
```

**`0 patched, 12 skipped, 0 errors`.** The live `_18ct13i._.js` is fully patched (not half-done):
its live SHA256 `947D1EFAA6F13FE9AEF0D38A039C9FDDC23069B18C22076ACBC16F0F6894780C`
(1,302,997 B) equals the composed pristine+all-patches byte result recorded by
`lanePatchIntegrity` §4 (`947D1EFAA6F13FE9`, 1,302,997 B).

### 2.3 The reapply run changed nothing (idempotent, no writes)

14 file hashes (Len + SHA256) captured **before** and **after** the run are identical:

```
pre rows=14 post rows=14
ALL 14 ROWS IDENTICAL pre==post (Len+SHA256)
```

No new `*.autoos-backup-*` was created (`Get-ChildItem … | Where LastWriteTime -gt now-30min`
returned nothing). The `_18ct13i._.js` field was verified: pre == post
`…947D…4780C`, 1,302,997 B.

---

## 3. Live package patch inventory — 19 patched, 19 with a certified revert path

Independent full-tarball inventory (`verify-patch-inventory.py`, streams each of the 21,898
members in memory):

```
members compared (excl. package/node_modules): 21898
differing (patched): 19
missing under live:  0
```

| # | live patched file | tarball B | live B | live SHA256 | certified pristine backup |
|---|---|---|---|---|---|
| 1 | `dist/.build/next/server/chunks/_04g0p_r._.js` | 257546 | 257463 | `1779016D5BF6BC2E4717A230586F01FF9E70E0CD2690F202D307719BFF8D2C33` | `…pristine-3.8.50-20260930-215549` |
| 2 | `dist/.build/next/server/chunks/_08_y1bx._.js` | 1235835 | 1236524 | `199EF4D865D8555633C606401CDFCFFDFA34B53E40165347700A380FFB4D2ED4` | `…pristine-3.8.50-20260930-215549` |
| 3 | `dist/.build/next/server/chunks/_0o50usg._.js` | 275297 | 275214 | `7462DF651797CD64C74A9C7777B5621A1E24E4186E09B9486CBFE293E07AE0B8` | `…pristine-3.8.50-20260930-215549` |
| 4 | `dist/.build/next/server/chunks/_0o8_5h8._.js` | 870668 | 871423 | `0EE893621B7BD591210C287F9D5CA204540932469932F53EBE337513DB87C557` | `…pristine-3.8.50-20260930-215549` |
| 5 | `dist/.build/next/server/chunks/_0t1t5fj._.js` | 870668 | 871423 | `F03D3CD3F76BDF8A79B34E86BEB60DCFC4883A15466B4C9F50ACE6E1EBBA6035` | `…pristine-3.8.50-20260930-215549` |
| 6 | `dist/.build/next/server/chunks/_0wr-zm3._.js` | 275297 | 275214 | `F681BE5261BF38DE3986D0AA3F5A11F1FA67970A1357A7948B6222DF4B62009C` | `…pristine-3.8.50-20260930-215549` |
| 7 | `dist/.build/next/server/chunks/_15ose6x._.js` | 157968 | 158551 | `6DD65402CB51B160D7C72BFAB77A9E2E0718FEE677FDA76B9603C9473F88EAC4` | `…pristine-3.8.50-20260930-215549` |
| 8 | `dist/.build/next/server/chunks/_18ct13i._.js` | 1302308 | **1302997** | `947D1EFAA6F13FE9AEF0D38A039C9FDDC23069B18C22076ACBC16F0F6894780C` | `…pristine-3.8.50-20260930-215549` |
| 9 | `dist/.build/next/server/chunks/_1j_edf1._.js` | 1235835 | 1236524 | `643A0661B156EE24B98F8E25CBB8D69FA0D7EB370F977AFD52853CB6A184BFB9` | `…pristine-3.8.50-20260930-215549` |
| 10 | `dist/.build/next/server/chunks/_1luyz1c._.js` | 1235835 | 1236524 | `2834DAC905481C48506E01BA5B49440BA5FBCC2BBE9B0EB3342A02C62864147D` | `…pristine-3.8.50-20260930-215549` |
| 11 | `dist/.build/next/server/chunks/_1mq9y97._.js` | 257546 | 257463 | `173F8C7E1C7A26C10B3E4A2239888561A9CBC6887A7F536B12130431AC99032E` | `…pristine-3.8.50-20260930-215549` |
| 12 | `dist/.build/next/server/chunks/_1xkpq2s._.js` | 157968 | 158551 | `EF7137A80E09B88C672550555B10D16142509F87182225ED5556BF8F4395864A` | `…pristine-3.8.50-20260930-215549` |
| 13 | `dist/open-sse/mcp-server/server.js` | 5140243 | 5140532 | `A3E833ECA8725E1693464250CF2EBADE96D4D73D6CBB6964E0BFD0C8242BD386` | `server.js.autoos-backup-20260930-171528` (non-pristine name, ==tarball) |
| 14 | `open-sse/config/providers/registry/scaleway/index.ts` | 837 | 861 | `DEF6871B286CEA70A855F0720C3E5B31CB8948CA488762256CCD5AF756E14C78` | `…pristine-3.8.50-20261001-002911` **(R1, this verify)** |
| 15 | `open-sse/services/contextManager.ts` | 38955 | 39300 | `D7A90745CCFE78A5F09A530EC55D38BD36C6776D2A2096957052467EE28EE643` | `contextManager.ts.autoos-backup-20260930144810` (non-pristine name, ==tarball) |
| 16 | `open-sse/translator/index.ts` | 37843 | 38175 | `5029B12CDD32C148CAAB54BDF7A552C4B416E657FCD44BB9EA41BF678C9120DA` | `…pristine-3.8.50-20260930-215549` |
| 17 | `open-sse/translator/paramSupport.ts` | 10765 | 11617 | `5044D9929AFD5717162015459A70ABDE6D43453846524070F0C9D51A0701A66B` | `…pristine-3.8.50-20261001-002911` **(R1, this verify)** |
| 18 | `open-sse/translator/request/openai-responses/toResponses.ts` | 17839 | 20430 | `88C52A0B8D24805EB7AAE78DF56A02131ED56ED7B36992F9C50CC4C6056164C4` | `…pristine-3.8.50-20260930-215549` |
| 19 | `open-sse/translator/request/openai-to-gemini.ts` | 36018 | 36778 | `B913F3CAF19D321FB5CBB78C8BAC5C18FEDF5C291EC3594A2C186B67EA3512B4` | `…pristine-3.8.50-20260930-215549` |

The two files the pristine-name-only inventory flags as "NO" (13 `mcp-server/server.js`,
15 `contextManager.ts`) are each covered by an older, differently-named sibling backup whose
SHA256 **equals the tarball byte-for-byte** (recomputed here):

```
mcp-server/server.js   : backupLen=5140243 tarLen=5140243 match=True  DEF35CCFB8481E767A492FF5927FB1932E5FD31F10330DAF86B2C5F15E3E03D9
services/contextManager.ts: backupLen=38955 tarLen=38955 match=True 7DE4E1C30A4AF817194D4F11950C0EBE6D8383775DB9CEF26113381AF2FDEF0B
```

**So 19 / 19 patched live files have a certified pristine revert path. There is no remaining
R1-class gap.**

---

## 4. Functional proof — both probes against ONE isolated gateway from the live package

### 4.1 Isolation and lifecycle

House pattern `tools/start-isolated-gateway.ps1` (`git show L1-backlog/ws-f1-vertex-20260930`)
with `DATA_DIR` isolation added (the shape `v-activate-start-iso.ps1` used), **spare port
`20146`**, isolated `DATA_DIR = %TEMP%\opencode\patchr1-iso` (`.env` + a sqlite **backup-API**
snapshot of the live DB, so the shared WAL is never opened). One instance, both probes.

```
$ python tools/copy-datadir.py
copied .env: 63 bytes -> C:\Users\<user>\AppData\Local\Temp\opencode\patchr1-iso\.env
sqlite backup: 117608448 B -> 117624832 B at …\patchr1-iso\storage.sqlite

$ pwsh -File tools/start-isolated-gateway.ps1 -Port 20146
launcher_pid=122900
port=20146
DATA_DIR=C:\Users\<user>\AppData\Local\Temp\opencode\patchr1-iso
logs=C:\Users\<user>\AppData\Local\Temp\opencode\patchr1-logs
listening=YES pid=131712 after=6s

$ GET /api/health  -> {"status":"ok","timestamp":"2026-10-01T01:55:05.452Z"}
```

Listener snapshot in `201xx`, **before** (left) and **after** the run (right) — the shared
gateway `126780` (20128/20131/20132) and the foreign `20138` (`118016`) are untouched:

```
before                              after (post-kill)
port=20128 pid=126780               port=20128 pid=126780
port=20131 pid=126780               port=20131 pid=126780
port=20132 pid=126780               port=20132 pid=126780
port=20138 pid=118016               port=20138 pid=118016
port=20146 pid=131712   (this lane)
```

Killed by PID: `killed pid=122900`, `killed pid=131712`; `20146 removed`. **No listener left
in `201xx`.**

### 4.2 Both probes, one instance, same window (`v-activate-run-probes.py` → 20146)

**Reasoning probe** (`probe-reasoning-repro.py`, verbatim summary):

```
  deepseek-v4.1-flash                 overwrite  rc= 79  step2=200  -> PASS
  deepseek-v4.1-flash                 preserve   rc=204  step2=200  -> PASS
  deepseek/deepseek-flash             overwrite  rc= 81  step2=200  -> PASS
  deepseek/deepseek-flash             preserve   rc=345  step2=200  -> PASS

  400 reasoning error reproduced: False
```

Cache-miss (`overwrite_call_id=True`) → **200**; cache-preserve (`preserve`) → **200**.

**Vertex probe** (`probe-vertex.py`, verbatim summary):

```
  [200] vertex/gemini-3.8-flash | Test 1: trailing model turn with tool_calls
  [200] vertex/gemini-3.8-flash | Test 2: trailing model turn plain text
  [200] vertex/gemini-3.8-flash | Test 3: normal ending with user (should pass)
  [200] vertex/gemini-3.8-flash | Test 4: complex agentic shape (7 msg, tool_calls)
  [200] gemini/gemini-2.5-flash | Test 1: trailing model turn with tool_calls
  [200] gemini/gemini-2.5-flash | Test 2: trailing model turn plain text
  [200] gemini/gemini-2.5-flash | Test 3: normal ending with user (should pass)
  [429] gemini/gemini-2.5-flash | Test 4: complex agentic shape (7 msg, tool_calls)
```

Both trailing-model shapes and the control (`Test 3`) return **200**; the single `429` is a
provider free-tier quota park on the second model, **not** the defect under test (a `400`
would be). No `400` was returned by any probe.

**Disclosure (honesty):** the combined runner printed a `UnicodeEncodeError` (`cp1252`) while
*copying the already-written probe output files to stdout* (`\u0393` in one vertex body). The
probes themselves exited `rc=0`; the `*.out.txt` files above are the complete captured
output and were read back separately. The runner's stdout copy is the only thing affected.

Runtime proof is from the **live package loaded fresh on disk**, i.e. the code the `:20128`
restart will load.

---

## 5. GO / NO-GO for the `:20128` restart

**GO.**

The live npm-global package now carries a **complete, verified patch set**:

1. **19 files patched** vs the certified 3.8.50 tarball; 0 missing; `_18ct13i._.js` is fully
   patched (`947D1EFA…`, 1,302,997 B), proven by the reapply script reporting
   `0 patched, 12 skipped, 0 errors` with **zero writes** (14/14 pre == post).
2. **19 / 19 patched files have a certified pristine revert path.** The R1 gap is closed: the
   two scaleway-qwen files now each have a `…pristine-3.8.50-20261001-002911` backup
   byte-identical to the tarball (verified here).
3. **Functional proof on one isolated gateway started from the live package:** the reasoning
   defence holds (cache-miss 200, cache-preserve 200; `400 … reproduced: False`) **and** the
   vertex trailing-model shapes return 200 (control 200) in the same instance. No `400`.
4. The isolated instance was killed by PID; the shared `:20128` gateway was never touched and
   no listener remains in `201xx`.

Residual (non-blocking): `mcp-server/server.js` and `contextManager.ts` revert via
differently-named siblings (verified byte-identical to the tarball), not
`*.autoos-backup-pristine-*`; and the deepseek `.ts` sources are source-tree-only (not loaded
at runtime) — already recorded by `lanePatchIntegrity` §5.

---

## 6. Reviewer (different family, nonce-gated)

**Family:** `t3-reviewer` (self-reported model `t3 cheap-driver-128k`) — a different provider
family from this lane's writer (`deepseek-v4.1-flash`).

**Verdict: APPROVE** (review attempt 2).

The reviewer, given the nonce above, read it back verbatim
(`PV-20261001-4H7K-9Q2W-B6XD`) and independently reproduced, with raw command output:

* R1 tarball members == installed backups:
  `BF39CEF1…343A0B40F` (scaleway/index.ts) and `806D5302…B27B11A9…` (paramSupport.ts), both
  tarball side and backup side.
* `_18ct13i._.js` = 1,302,997 B, SHA256
  `947D1EFAA6F13FE9AEF0D38A039C9FDDC23069B18C22076ACBC16F0F6894780C`.
* Reapply script: `0 patched, 12 skipped, 0 errors`, exit 0.
* Probe files: reasoning `step2=200 -> PASS` (+ `400 … reproduced: False`); vertex
  `[200]` for `vertex/gemini-3.8-flash` Tests 1–3.

These match this lane's own measurements exactly.

**Review-integrity note.** Attempt 1 (same reviewer) returned **REJECT** but was
**disregarded as unreliable**: it quoted the nonce *label* line instead of the value, marked
the correct hashes "does not match" in the same rows it copied them from, reported a bogus
`721`-byte length for a 1,302,997-byte file, and claimed the tarball members and probe files
"not found" — i.e. it did not actually perform those reads. This is the fabrication failure
mode this lane's nonce gate exists to catch (the predecessor `patch-fix` lane hit it twice).
Attempt 2, run with explicit commands, is the authoritative review and is accepted in full.

---

## 7. Files changed by this lane

* `docs/handoff/2026-10-01-lanePatchVerify.md` — this file.
* `logs/handoff-sessions/DONE-ws-patch-verify.md` — DONE note (`git add -f`).
* `docs/handoff/2026-09-30-lanePatchR1.md` — the cancelled R1 lane's untracked handoff,
  committed here so the record is preserved; its claims were independently verified above.

No live package file was written; no gateway other than the isolated `:20146` was started; no
push/merge/rebase/checkout.
