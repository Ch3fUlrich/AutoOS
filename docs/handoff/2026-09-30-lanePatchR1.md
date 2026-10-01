# Lane patch-r1 — pristine revert path for the two un-backed-up patched files (2026-09-30)

Closes **R1 (material)** raised by `V-activate #5` (commit `c231618`, branch
`L1-backlog/ws-verify-activate-20260930`): two **patched** files under the live
OmniRoute install had **no backup of any kind**, so the scaleway-qwen clamp had no
fast revert path. This lane installs a certified-pristine copy of each, streamed
straight out of the signed-off `omniroute@3.8.50` tarball.

- **Host:** Windows workstation (`win32`).
- **Live install root:** `C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute`
- **Certified tarball:** `C:\Users\mauls\AppData\Local\Temp\opencode\packbackups\omniroute-3.8.50.tgz`
  (121 369 534 bytes; produced by `npm pack omniroute@3.8.50`).
- **Scope:** no live file content modified, no gateway restart, no other worktree touched.

**Nonce (reviewer must echo this back verbatim to prove the review was not fabricated):**
`NONCE-PATCHR1-20261001-7A3E9C`

---

## 1. Certification — tarball member vs live file (must differ)

Each member was streamed out with `tar -xOf <tgz> package/<member>` and hashed
(`Get-FileHash -Algorithm SHA256`) against the live file:

| member | tarball SHA256 | live SHA256 | differ |
|---|---|---|---|
| `open-sse/config/providers/registry/scaleway/index.ts` | `BF39CEF1FD8E252850452ED8C18B760F3BCB1637B99969AA8B6055F343A0B40F` | `DEF6871B286CEA70A855F0720C3E5B31CB8948CA488762256CCD5AF756E14C78` | **yes** |
| `open-sse/translator/paramSupport.ts` | `806D530225FEBBFD913110B336D42AC06D7202677FF2145BBCB27B11A9B4D049` | `5044D9929AFD5717162015459A70ABDE6D43453846524070F0C9D51A0701A66B` | **yes** |

Both differ → both live files are genuinely **patched**.

Before this lane, a `Get-ChildItem` filtered on `<file>.autoos-backup-*` in each
file's directory returned **nothing** — neither file had any sibling backup of any
kind (confirming R1).

## 2. Installed pristine backups (equal to the tarball)

Each tarball member was written directly to a sibling of its live file:
`<file>.autoos-backup-pristine-3.8.50-<timestamp>` (single run timestamp
`20261001-002911`), then re-hashed to prove the installed backup equals the tarball.

`Get-ChildItem` listing (both new backups):

```
path   : C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute\open-sse\config\providers\registry\scaleway\index.ts.autoos-backup-pristine-3.8.50-20261001-002911
length : 837
sha256 : BF39CEF1FD8E252850452ED8C18B760F3BCB1637B99969AA8B6055F343A0B40F

path   : C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute\open-sse\translator\paramSupport.ts.autoos-backup-pristine-3.8.50-20261001-002911
length : 10765
sha256 : 806D530225FEBBFD913110B336D42AC06D7202677FF2145BBCB27B11A9B4D049
```

Installed-backup hash vs tarball hash: **equal** for both files.

## 3. Live files untouched

Restated live hashes after the copy are identical to §1 — no live content changed:

```
open-sse\config\providers\registry\scaleway\index.ts  DEF6871B286CEA70A855F0720C3E5B31CB8948CA488762256CCD5AF756E14C78
open-sse\translator\paramSupport.ts                   5044D9929AFD5717162015459A70ABDE6D43453846524070F0C9D51A0701A66B
```

## 4. Verdict on R1

**All patched files now have a certified-pristine revert path** for the two files
named in R1. The same `*.autoos-backup-pristine-3.8.50-*` convention already exists
in the install for the other patched files
(`open-sse/translator/index.ts`, `open-sse/translator/request/openai-to-gemini.ts`,
`open-sse/translator/request/openai-responses/toResponses.ts`, and the `dist/.build`
chunks), so this lane brings the two stragglers into line. **Nothing remains
missing** in R1's scope.

To revert either file: copy its `.autoos-backup-pristine-3.8.50-20261001-002911`
sibling over the live path (verify the hash above first).

---

## Reviewer (facilitated — gated by the nonce)

Reviewer family: _pending_. Verdict: _pending_. The concise review request, with the
nonce, is recorded in `logs/handoff-sessions/DONE-ws-patch-r1.md`.
