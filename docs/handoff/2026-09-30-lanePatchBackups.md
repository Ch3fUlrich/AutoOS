# Lane: patch-backups-2 ΓÇö tarball pristine-source gate

**Lane:** `patch-backups-2` under L1-alpha
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-patchbackups-2`
**Branch:** `L1-backlog/ws-patchbackups-2-20260930` (cut from `main` @ `d08f7f2`)
**Date:** 2026-09-30

## Result: STOPPED ΓÇö gate (3a) FAILED, no backups installed

Step 3a requires the tarball `_18ct13i._.js` to be **identical** to the live backup
`_18ct13i._.js.autoos-backup-20260930-182908`. It is **not**. Therefore the tarball is
**not** proven to be the pristine pre-patch source for this install, and per the lane brief
("If (3a) does NOT match, STOP ΓÇª instead of installing anything") **no backup files were
copied**. `_0o8_5h8._.js`, `_0t1t5fj._.js`, `_14jycqh._.js` were located and hashed but
**not** backed up.

## 1. Set-up (disclosure)

The assigned worktree/branch did not exist when this lane started (only the sibling
`AutoOS-ws-patchbackups` existed). It was created from `main` @ `d08f7f2` so this lane could
commit its report; `main` and every other branch/worktree were left untouched.

```
git -C <AutoOS> worktree add <...AutoOS-ws-patchbackups-2> -b L1-backlog/ws-patchbackups-2-20260930 d08f7f2
Preparing worktree (new branch 'L1-backlog/ws-patchbackups-2-20260930')
HEAD is now at d08f7f2 fix(router): workstation combos 1M + AGYCANON + apply automation (operator 2026-09-30)
```

## 2. Commands run

```powershell
# step 1 ΓÇö fetch + extract the published package
$d = "C:\Users\<user>\AppData\Local\Temp\opencode\packbackups"
New-Item -ItemType Directory -Force -Path $d | Out-Null
cd $d
npm pack omniroute@3.8.50          # -> omniroute-3.8.50.tgz (121.4 MB)
tar -xzf omniroute-3.8.50.tgz      # -> package/  (EXTRACT_OK)

# step 2 ΓÇö locate target chunks
Get-ChildItem "$d\package\dist\.build\next\server\chunks\" -Filter "_0o8_5h8*"
Get-ChildItem "$d\package\dist\.build\next\server\chunks\" -Filter "_0t1t5fj*"
Get-ChildItem "$d\package\dist\.build\next\server\chunks\" -Filter "_14jycqh*"

# step 3 ΓÇö SHA256 compare (Get-FileHash -Algorithm SHA256)
# step 5 ΓÇö final backup listing
Get-ChildItem <live chunks> -Filter "*.autoos-backup-*"
```

`npm pack` output (abridged): `package size: 121.4 MB`, `unpacked size: 452.5 MB`,
`shasum: d7b4fce4f1b00e5e826b76855665dfae42aab97a`,
`integrity: sha512-qK6REDWQYGh8l[...]vl+az0lvyt0Mg==`, `total files: 21898`.

Live path: `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute\dist\.build\next\server\chunks\`
Tarball path: `C:\Users\<user>\AppData\Local\Temp\opencode\packbackups\package\dist\.build\next\server\chunks\`

Both `package.json` files report `name: omniroute`, `version: 3.8.50`.

## 3. Hash table

### 3a ΓÇö pristine gate (MUST be identical)

| File | SHA256 | Size (B) |
|---|---|---|
| tarball `_18ct13i._.js` | `62DFB62695B7EE67296A69290EC3B84D7BCB38BB825349695848A6BBB830B81C` | 1,302,308 |
| live `_18ct13i._.js.autoos-backup-20260930-182908` | `C80549444052D0D76F37446D9581BF30646757AABE5DF5E8AFA28BD794BF725B` | 1,303,134 |

**Verdict: NOT identical.** (Sizes differ by 826 B; digests differ.)

For completeness, the live *current* `_18ct13i._.js` is a **third** distinct content:

| File | SHA256 | Size (B) |
|---|---|---|
| live current `_18ct13i._.js` | `18BC7447EE3B6852F3A0F614B471CD0A916DB986406964533B32FAEB1A29AFDE` | 1,302,588 |

So `_18ct13i._.js` exists in three mutually different forms ΓÇö published 3.8.50 (tarball),
the `-182908` backup, and the live file. The `-182908` backup is **not** the published-3.8.50
file, so it cannot serve as proof that the tarball is pristine.

### 3b ΓÇö target chunks vs live counterparts

| Chunk | Tarball SHA256 | Tarball size | Live (current) SHA256 | Live size | Differ? |
|---|---|---|---|---|---|
| `_0o8_5h8._.js` | `96B80E16AF41428196DD7D998099F2A621C2B753CB422EC9E64C53ADADABA57D` | 870,668 | `0EE893621B7BD591210C287F9D5CA204540932469932F53EBE337513DB87C557` | 871,423 | **differ** |
| `_0t1t5fj._.js` | `07DB3B94B8E2A74CDDED8191A76AC6626633B5A35496DBF09F7DF1A009E7583A` | 870,668 | `F03D3CD3F76BDF8A79B34E86BEB60DCFC4883A15466B4C9F50ACE6E1EBBA6035` | 871,423 | **differ** |
| `_14jycqh._.js` | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | 21,659 | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | 21,659 | identical |

`_0o8_5h8` / `_0t1t5fj` carry a live patch; `_14jycqh` is byte-identical and carries none.

## 4. Backup installation ΓÇö NOT PERFORMED

Because (3a) failed, step 4 (copy tarball chunks to `<live-file>.autoos-backup-<ts>`) was
**not executed**. No live chunk content was read-modified or overwritten; no new files were
written into the live `chunks` directory. The existing backup count is unchanged at **15**.

## 5. Final backup listing (step 5)

`Get-ChildItem <live chunks> -Filter "*.autoos-backup-*"` ΓÇö **15 entries, unchanged by this lane**:

```
_04g0p_r._.js.autoos-backup-20260930-171528   257546
_08_y1bx._.js.autoos-backup-20260930-182908  1237049
_08_y1bx._.js.autoos-backup-20260930174401   1236640
_0o50usg._.js.autoos-backup-20260930-171528   275297
_0wr-zm3._.js.autoos-backup-20260930-171528   275297
_15ose6x._.js.autoos-backup-20260930-182908   158377
_15ose6x._.js.autoos-backup-20260930174401    157968
_18ct13i._.js.autoos-backup-20260930-182908  1303134
_1j_edf1._.js.autoos-backup-20260930-182908  1237049
_1j_edf1._.js.autoos-backup-20260930174401   1236640
_1luyz1c._.js.autoos-backup-20260930-182908  1237049
_1luyz1c._.js.autoos-backup-20260930174401   1236640
_1mq9y97._.js.autoos-backup-20260930-171528   257546
_1xkpq2s._.js.autoos-backup-20260930-182908   158377
_1xkpq2s._.js.autoos-backup-20260930174401    157968
```

No backup exists for `_0o8_5h8._.js`, `_0t1t5fj._.js` or `_14jycqh._.js`.

## 6. Refusals (verbatim, as given, all honoured)

> Never push/merge/rebase/checkout.

No `git push`, `git merge`, `git rebase`, or `git checkout` of any branch was run. The only
git write was creating this lane's own worktree and committing this report.

> Do NOT touch reapply scripts, other patch sets, gateways, or other worktrees.

None were touched. `AutoOS-ws-patchbackups` (sibling lane) and all other worktrees were left
untouched.

> If (3a) does NOT match, STOP and report that the tarball is not a reliable pristine source
> (with the hashes) instead of installing anything.

Honoured: no backup was installed; this document is the report, with the hashes above.

## 7. Recommendation to L1-alpha

The `_18ct13i._.js.autoos-backup-20260930-182908` file is not the published 3.8.50 artefact,
so it cannot certify the tarball. Two possibilities need an operator decision before any
backup work resumes:

1. The live install was already carrying earlier patches when the `-182908` backup was taken
   (the `-174401` backups are 409 B smaller than the `-182908` set, consistent with at least
   two patch rounds), so no existing backup is pristine.
2. The live 3.8.50 was installed from a build that differs from the published npm tarball.

Either way, the published tarball (shasum `d7b4fce4f1b00e5e826b76855665dfae42aab97a`) is the
only verified vendor-published 3.8.50 source available here; the named live backup is not.

---

# Lane: patch-backups-3 — certified-pristine backups from the 3.8.50 tarball

**Lane:** `patch-backups-3` under L1-alpha
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-patchbackups-3`
**Branch:** `L1-backlog/ws-patchbackups-3-20260930` (cut from `main` @ `d08f7f2`)
**Date:** 2026-09-30
**Predecessor:** `patch-backups-2` (branch `L1-backlog/ws-patchbackups-2-20260930` @ `5c67a97`) — this file is that lane's report, extended.

## Result: DONE — 15 certified-pristine backups installed; all three patch sets have a complete revert path

Every briefed file was hash-compared between the published `omniroute@3.8.50` npm tarball and
the **live** npm-global install. **15 of 16 differ (patched)** and each now has a new
`<file>.autoos-backup-pristine-3.8.50-<timestamp>` copy taken from the tarball. **One file
(`_14jycqh._.js`) is byte-identical** (not patched) and correctly got **no** backup — a
discrepancy against the F1 lane doc, recorded below. No live file's content was modified.

## 1. Set-up (disclosure)

The assigned worktree/branch did **not** exist when this lane started — only the siblings
`AutoOS-ws-patchbackups` and `AutoOS-ws-patchbackups-2` did. Exactly as `patch-backups-2` did,
the lane worktree was created from `main` @ `d08f7f2` so this lane could commit its report;
`main` and every other branch/worktree were left untouched.

```
git -C <AutoOS> worktree add <...AutoOS-ws-patchbackups-3> -b L1-backlog/ws-patchbackups-3-20260930 d08f7f2
Preparing worktree (new branch 'L1-backlog/ws-patchbackups-3-20260930')
HEAD is now at d08f7f2 fix(router): workstation combos 1M + AGYCANON + apply automation (operator 2026-09-30)
```

**Extraction is not a trustworthy store.** The tarball was extracted under
`C:\Users\<user>\AppData\Local\Temp\opencode\packbackups`. Reading it back revealed the
predecessor's extraction was missing exactly one member
(`package/dist/open-sse/mcp-server/server.js`; 21897 files vs 21898 in the archive). A fresh
re-extraction produced 21898 files, but files then **disappeared again**
(`packbackups-v2` fell 21898 → 21896; mcp `server.js`, `_08_y1bx._.js`, `_04g0p_r._.js`
vanished while in use). Something on this host removes those files from Temp shortly after
they are written. Because of this, **every hash and find-string in this report was taken by
streaming the member out of `omniroute-3.8.50.tgz` with `tar -xOf` into memory** (raw bytes),
which does not touch disk. Independent cross-checks:

- `TarBytes` of `_04g0p_r._.js` → 257546 B, SHA256 `A1D23A5B…` (matches the recorded value).
- Single-member `tar -xzf` of `_18ct13i._.js`, `_0o8_5h8._.js`, `index.ts` → same hashes.
- Full fresh extraction: all 16 target hashes identical to the original extraction.

```
npm pack omniroute@3.8.50   # -> omniroute-3.8.50.tgz  (shasum d7b4fce4f1b00e5e826b76855665dfae42aab97a, 21898 entries)
```

Live path: `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute`
Tarball: `C:\Users\<user>\AppData\Local\Temp\opencode\packbackups\omniroute-3.8.50.tgz`

## 2. Hash table — tarball vs live, and the backup installed

`Get-FileHash -Algorithm SHA256` (live) / in-memory SHA256 of `tar -xOf` bytes (tarball).
New backups are `<live-file>.autoos-backup-pristine-3.8.50-20260930-215549`.

| # | File (set) | Tarball SHA256 | Live SHA256 | Patched? | Installed pristine backup |
|---|---|---|---|---|---|
| 1 | `_04g0p_r._.js` (clamp) | `A1D23A5BA40E79615A1C3364430D27BB8093411B126FDF932E53458B986B20D2` | `1779016D5BF6BC2E4717A230586F01FF9E70E0CD2690F202D307719BFF8D2C33` | yes | `_04g0p_r._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 2 | `_0o50usg._.js` (clamp) | `29C37C4F9D80274827DD81EBF5EA2AFE0EF281B5C2A1ACC74BAC95FDD882E429` | `7462DF651797CD64C74A9C7777B5621A1E24E4186E09B9486CBFE293E07AE0B8` | yes | `_0o50usg._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 3 | `_0wr-zm3._.js` (clamp) | `6EECBD44731825E74F4E8A1E02B790ADEB39CEA4C5F4234F9178C893681BC8F1` | `F681BE5261BF38DE3986D0AA3F5A11F1FA67970A1357A7948B6222DF4B62009C` | yes | `_0wr-zm3._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 4 | `_1mq9y97._.js` (clamp) | `E00AC24377570A3C9A572EDD3396A9D5E19B1CF52747A7680587035970AA805D` | `173F8C7E1C7A26C10B3E4A2239888561A9CBC6887A7F536B12130431AC99032E` | yes | `_1mq9y97._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 5 | `_08_y1bx._.js` (deepseek) | `97729CF68F77CE10D5DBEE6498C5A601406CBA144A027E66760A2D27537FE67B` | `199EF4D865D8555633C606401CDFCFFDFA34B53E40165347700A380FFB4D2ED4` | yes | `_08_y1bx._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 6 | `_1j_edf1._.js` (deepseek) | `956F7AF17980B28468869E4CBE1D2732D762B7BB018E3877A8A0F6BE6708472D` | `643A0661B156EE24B98F8E25CBB8D69FA0D7EB370F977AFD52853CB6A184BFB9` | yes | `_1j_edf1._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 7 | `_1luyz1c._.js` (deepseek) | `9827226EDC6CFC1B3A53F8C5097AE3E482ACA65D1E085E40BEFA73A3DCA4BB54` | `2834DAC905481C48506E01BA5B49440BA5FBCC2BBE9B0EB3342A02C62864147D` | yes | `_1luyz1c._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 8 | `_15ose6x._.js` (deepseek) | `E04039A5D67DCB680BC57448665FE1D5BAA71950919A958174F099BB4E786953` | `6DD65402CB51B160D7C72BFAB77A9E2E0718FEE677FDA76B9603C9473F88EAC4` | yes | `_15ose6x._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 9 | `_1xkpq2s._.js` (deepseek) | `F5402A53D82ABAFE6AFB3F5008043D5293AFC69B79D09354A27D47972B57268C` | `EF7137A80E09B88C672550555B10D16142509F87182225ED5556BF8F4395864A` | yes | `_1xkpq2s._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 10 | `open-sse/translator/index.ts` (deepseek) | `F2E1C69CE98D1189F73EACD748D21CDACD02FEDF55A62E581EBD16C98F570F74` | `5029B12CDD32C148CAAB54BDF7A552C4B416E657FCD44BB9EA41BF678C9120DA` | yes | `index.ts.autoos-backup-pristine-3.8.50-20260930-215549` |
| 11 | `open-sse/translator/request/openai-responses/toResponses.ts` (deepseek) | `4AE24AA23FB9765EEB7735DB0168AD616156BBBB907E3120135FEDEBC097093F` | `88C52A0B8D24805EB7AAE78DF56A02131ED56ED7B36992F9C50CC4C6056164C4` | yes | `toResponses.ts.autoos-backup-pristine-3.8.50-20260930-215549` |
| 12 | `_0o8_5h8._.js` (vertex) | `96B80E16AF41428196DD7D998099F2A621C2B753CB422EC9E64C53ADADABA57D` | `0EE893621B7BD591210C287F9D5CA204540932469932F53EBE337513DB87C557` | yes (unattributed) | `_0o8_5h8._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 13 | `_0t1t5fj._.js` (vertex) | `07DB3B94B8E2A74CDDED8191A76AC6626633B5A35496DBF09F7DF1A009E7583A` | `F03D3CD3F76BDF8A79B34E86BEB60DCFC4883A15466B4C9F50ACE6E1EBBA6035` | yes (unattributed) | `_0t1t5fj._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 14 | `_14jycqh._.js` (vertex) | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | **no — identical** | *(none installed, by rule)* |
| 15 | `_18ct13i._.js` (vertex, deepseek anchor) | `62DFB62695B7EE67296A69290EC3B84D7BCB38BB825349695848A6BBB830B81C` | `18BC7447EE3B6852F3A0F614B471CD0A916DB986406964533B32FAEB1A29AFDE` | yes | `_18ct13i._.js.autoos-backup-pristine-3.8.50-20260930-215549` |
| 16 | `open-sse/translator/request/openai-to-gemini.ts` (vertex) | `0DBFA6D722C7B0F338EE8E43DF8EDFD9CFAEF3EAA3E6A5DEEFFAF357FD2A7B7F` | `B913F3CAF19D321FB5CBB78C8BAC5C18FEDF5C291EC3594A2C186B67EA3512B4` | yes | `openai-to-gemini.ts.autoos-backup-pristine-3.8.50-20260930-215549` |
| — | `dist/open-sse/mcp-server/server.js` (clamp script, **not in briefed list**) | `DEF35CCFB8481E767A492FF5927FB1932E5FD31F10330DAF86B2C5F15E3E03D9` | `A3E833ECA8725E1693464250CF2EBADE96D4D73D6CBB6964E0BFD0C8242BD386` | yes | already had `server.js.autoos-backup-20260930-171528` which is **byte-identical to the tarball** — certified below; no new file needed |

Every installed backup was re-hashed after copying and equals its tarball hash (**15/15 match**).
The live files were re-hashed after copying and are **unchanged**.

### Existing backups that are also tarball-identical (now certified)

Hashing the pre-existing `*autoos-backup-*` files against the tarball shows several were
already pristine (the predecessor could not certify them because it only tested the
non-matching `_18ct13i` `-182908` file):

| Existing backup | Verdict |
|---|---|
| `_04g0p_r/_0o50usg/_0wr-zm3/_1mq9y97._.js.autoos-backup-20260930-171528` | **== tarball** (pristine) |
| `_15ose6x._.js.autoos-backup-20260930174401`, `_1xkpq2s._.js.autoos-backup-20260930174401` | **== tarball** (pristine) |
| `dist/open-sse/mcp-server/server.js.autoos-backup-20260930-171528` | **== tarball** (pristine) |
| `open-sse/services/contextManager.ts.autoos-backup-20260930144810` | **== tarball** (pristine) |
| `open-sse/translator/index.ts.autoos-backup-20260930170532` | **== tarball** (pristine) |
| `open-sse/translator/request/openai-to-gemini.ts.autoos-backup-20260930-182908` | **== tarball** (pristine) |
| `open-sse/translator/request/openai-responses/toResponses.ts.autoos-backup-20260930172918` | **== tarball** (pristine) |
| `_08_y1bx/_1j_edf1/_1luyz1c._.js.autoos-backup-20260930{,-174401}`, `_15ose6x/_1xkpq2s._.js.autoos-backup-20260930-182908`, `_18ct13i._.js.autoos-backup-20260930-182908`, `toResponses.ts.autoos-backup-20260930180542` | differ from tarball (intermediate states) |

`contextManager.ts` is patched on the live install but is **not** part of the three briefed
patch sets; its existing backup is tarball-identical, so its revert path is already certified.

## 3. Final `Get-ChildItem … *autoos-backup-*` listing (36 entries; 15 new)

```
dist\.build\next\server\chunks\_04g0p_r._.js.autoos-backup-20260930-171528  257546
dist\.build\next\server\chunks\_04g0p_r._.js.autoos-backup-pristine-3.8.50-20260930-215549  257546
dist\.build\next\server\chunks\_08_y1bx._.js.autoos-backup-20260930-182908  1237049
dist\.build\next\server\chunks\_08_y1bx._.js.autoos-backup-20260930174401  1236640
dist\.build\next\server\chunks\_08_y1bx._.js.autoos-backup-pristine-3.8.50-20260930-215549  1235835
dist\.build\next\server\chunks\_0o50usg._.js.autoos-backup-20260930-171528  275297
dist\.build\next\server\chunks\_0o50usg._.js.autoos-backup-pristine-3.8.50-20260930-215549  275297
dist\.build\next\server\chunks\_0o8_5h8._.js.autoos-backup-pristine-3.8.50-20260930-215549  870668
dist\.build\next\server\chunks\_0t1t5fj._.js.autoos-backup-pristine-3.8.50-20260930-215549  870668
dist\.build\next\server\chunks\_0wr-zm3._.js.autoos-backup-20260930-171528  275297
dist\.build\next\server\chunks\_0wr-zm3._.js.autoos-backup-pristine-3.8.50-20260930-215549  275297
dist\.build\next\server\chunks\_15ose6x._.js.autoos-backup-20260930-182908  158377
dist\.build\next\server\chunks\_15ose6x._.js.autoos-backup-20260930174401  157968
dist\.build\next\server\chunks\_15ose6x._.js.autoos-backup-pristine-3.8.50-20260930-215549  157968
dist\.build\next\server\chunks\_18ct13i._.js.autoos-backup-20260930-182908  1303134
dist\.build\next\server\chunks\_18ct13i._.js.autoos-backup-pristine-3.8.50-20260930-215549  1302308
dist\.build\next\server\chunks\_1j_edf1._.js.autoos-backup-20260930-182908  1237049
dist\.build\next\server\chunks\_1j_edf1._.js.autoos-backup-20260930174401  1236640
dist\.build\next\server\chunks\_1j_edf1._.js.autoos-backup-pristine-3.8.50-20260930-215549  1235835
dist\.build\next\server\chunks\_1luyz1c._.js.autoos-backup-20260930-182908  1237049
dist\.build\next\server\chunks\_1luyz1c._.js.autoos-backup-20260930174401  1236640
dist\.build\next\server\chunks\_1luyz1c._.js.autoos-backup-pristine-3.8.50-20260930-215549  1235835
dist\.build\next\server\chunks\_1mq9y97._.js.autoos-backup-20260930-171528  257546
dist\.build\next\server\chunks\_1mq9y97._.js.autoos-backup-pristine-3.8.50-20260930-215549  257546
dist\.build\next\server\chunks\_1xkpq2s._.js.autoos-backup-20260930-182908  158377
dist\.build\next\server\chunks\_1xkpq2s._.js.autoos-backup-20260930174401  157968
dist\.build\next\server\chunks\_1xkpq2s._.js.autoos-backup-pristine-3.8.50-20260930-215549  157968
dist\open-sse\mcp-server\server.js.autoos-backup-20260930-171528  5140243
open-sse\services\contextManager.ts.autoos-backup-20260930144810  38955
open-sse\translator\index.ts.autoos-backup-20260930170532  37843
open-sse\translator\index.ts.autoos-backup-pristine-3.8.50-20260930-215549  37843
open-sse\translator\request\openai-to-gemini.ts.autoos-backup-20260930-182908  36018
open-sse\translator\request\openai-to-gemini.ts.autoos-backup-pristine-3.8.50-20260930-215549  36018
open-sse\translator\request\openai-responses\toResponses.ts.autoos-backup-20260930172918  17839
open-sse\translator\request\openai-responses\toResponses.ts.autoos-backup-20260930180542  20430
open-sse\translator\request\openai-responses\toResponses.ts.autoos-backup-pristine-3.8.50-20260930-215549  17839
```

## 4. Reapply-script find-strings vs the tarball

Scripts: `configuration/omniroute/patch-gateway-clamp.ps1` (branch
`L1-backlog/ws-qwenclamp-20260930`), `configuration/omniroute/reason-fix-reapply.ps1`
(`L1-backlog/ws-fixes-20260930`), `tools/vertex-trailing-turn-reapply.ps1`
(`L1-backlog/ws-f1-vertex-20260930`). Matched against tarball bytes.

### 4a. clamp — `patch-gateway-clamp.ps1` — MATCHES

Two anchor kinds, both present in the tarball:

- MCP-bundle `$oldGate` (exact, `dist/open-sse/mcp-server/server.js`): `MATCH=True`
  ```
    const capabilities = getResolvedModelCapabilities(modelStr);
    if (capabilities.supportsThinking !== true) return null;
    const maxOutputTokens = toPositiveInteger(getExplicitModelOutputCap(modelStr));
  ```
- MCP-bundle `$oldUnit`: `MATCH=True`
  ```
    if (args.isModelAvailable) {
      const available = await args.isModelAvailable(args.unit.modelStr, args.unit);
      if (!available) return errorResponse(503, `Model ${args.unit.modelStr} is unavailable`);
    }
    return args.handleSingleModel(args.body, args.unit.modelStr, {
  ```
  The already-patched marker `AutoOS clamp: never forward max_tokens above` is **absent** from
  the tarball (pristine), as expected.
- Gateway `$gatePattern` (name-agnostic regex) matches the 4 tarball chunks:
  ```
  _04g0p_r: let a=(0,n.getResolvedModelCapabilities)(e);if(!0!==a.supportsThinking)return null;
  _0o50usg: let a=(0,r.getResolvedModelCapabilities)(e);if(!0!==a.supportsThinking)return null;
  _0wr-zm3: let a=(0,r.getResolvedModelCapabilities)(e);if(!0!==a.supportsThinking)return null;
  _1mq9y97: let a=(0,n.getResolvedModelCapabilities)(e);if(!0!==a.supportsThinking)return null;
  ```
  A scan of all 11504 tarball chunks found **exactly these 4** carrying the gate → the clamp
  chunk set is complete.

### 4b. deepseek — `reason-fix-reapply.ps1` — MATCHES (with one omission)

- `$findA` `MATCH=True` in `_08_y1bx`, `_1j_edf1`, `_1luyz1c` **and also `_18ct13i`**:
  ```
  e&&!(0,n.isInternalReasoningPlaceholder)(e)&&g.push({type:"reasoning",content:[{type:"reasoning_text",text:e}],summary:[]})
  ```
- `$findB` `MATCH=True` in `_15ose6x`, `_1xkpq2s`:
  ```
  e&&!(0,n.isInternalReasoningPlaceholder)(e)&&d.push({type:"reasoning",content:[{type:"reasoning_text",text:e}],summary:[]})
  ```
- `index.ts` `findIdx` `MATCH=True`: `return normalizedProvider === "xiaomi-mimo" || /(^|\/)mimo/i.test(normalizedModel);`
- `index.ts` `findComment` `MATCH=True`: ` * isInternalReasoningPlaceholder(), so it never re-poisons cache or history.`
- `toResponses.ts` find1–find4 all `MATCH=True`
  (`import { isInternalReasoningPlaceholder } from "../../../utils/reasoningPlaceholder.ts";`,
  `export function openaiToOpenAIResponsesRequest(`, the
  `const reasoning = …; if (reasoning && !isInternalReasoningPlaceholder(reasoning)) {` pair,
  and the `}); } … // Thinking blocks remain display-only here.` block).

**Omission (see §5):** `$patternAFiles` lists only 3 chunks, but `_18ct13i` also carries
`$findA`; the script therefore never patches it.

### 4c. vertex — `vertex-trailing-turn-reapply.ps1` — DOES **NOT** MATCH (script defect)

- `.ts` anchor `mergeConsecutiveSameRoleContents\s*\(\s*tb\.messages\s*\)`: `MATCH=False`.
  In the tarball `openai-to-gemini.ts`, `mergeConsecutiveSameRoleContents` appears only as an
  **import/re-export** from `./openai-to-gemini/helpers.ts`; there is no `(tb.messages)` call.
- chunk anchor `mergeConsecutiveSameRoleContents\s*\(\s*e\.messages\s*\)`: `MATCH=False` for
  all four `$chunkFiles` (`_0o8_5h8`, `_0t1t5fj`, `_14jycqh`, `_18ct13i`). In fact
  `_0o8_5h8`/`_0t1t5fj`/`_14jycqh` do not contain `mergeConsecutiveSameRoleContents` at all,
  and `_18ct13i` calls it with `.contents`, never `.messages`.

The tool that actually patched the chunks is `tools/apply-vertex-patch.py` (same lane), whose
anchors **do** match the tarball:

```
_08_y1bx: ),f.contents=(0,u.mergeConsecutiveSameRoleContents)(f.contents),f},null),e.s([]),n()}catch
_18ct13i: ),f.contents=(0,u.mergeConsecutiveSameRoleContents)(f.contents),f},null),e.s([]),n()}catch
_1j_edf1: ),f.contents=(0,u.mergeConsecutiveSameRoleContents)(f.contents),f},null),e.s([]),n()}catch
_1luyz1c: ),f.contents=(0,u.mergeConsecutiveSameRoleContents)(f.contents),f},null),e.s([]),n()}catch
_15ose6x: ),m.contents=(0,c.mergeConsecutiveSameRoleContents)(m.contents),m},null),e.s([]),n()}catch
_1xkpq2s: ),m.contents=(0,c.mergeConsecutiveSameRoleContents)(m.contents),m},null),e.s([]),n()}catch
```

## 5. Discrepancies found (reported, not fixed)

1. **`_14jycqh._.js` is NOT patched** — byte-identical to the tarball (same SHA256 and size).
   `docs/handoff/2026-09-30-laneF1-vertex.md` lists it among the 5 files the vertex fix
   "updates". Per rule it got **no** backup; the live file needs none.
2. **The deepseek fix misses `_18ct13i._.js`.** The chunk carries the deepseek `$findA` anchor
   in the tarball and still carries it **live** (unpatched; live lacks the `_ip=…` fix marker
   that the other five chunks have). `reason-fix-reapply.ps1` does not list `_18ct13i`, so a
   pristine reinstall + reapply leaves this code path unfixed.
3. **The vertex `.ps1` reapply cannot reproduce the vertex patch** — both its `.ts` and chunk
   find-strings match nothing in the tarball (§4c). Its `$chunkFiles` names
   (`_0o8_5h8`, `_0t1t5fj`, `_14jycqh`, `_18ct13i`) also differ from the chunks
   `apply-vertex-patch.py` actually patches (`_08_y1bx`, `_1j_edf1`, `_1luyz1c`, `_15ose6x`,
   `_1xkpq2s`, `_18ct13i`).
4. **`_0o8_5h8._.js` / `_0t1t5fj._.js` differences are unattributed.** Both live files are 755 B
   larger than the tarball, but contain **no** marker from any of the three patch sets (the
   clamp gate is absent in both, so is not the cause; no deepseek anchor/fix token; no vertex
   strip code). Their patch provenance is unknown; a pristine tarball backup was installed for
   each anyway.

## 6. Which patch sets now have a complete certified-pristine revert path

- **clamp — complete.** All 4 chunks (`_04g0p_r`, `_0o50usg`, `_0wr-zm3`, `_1mq9y97`) differ
  and now have `pristine-3.8.50` backups. The clamp script's other target,
  `dist/open-sse/mcp-server/server.js`, already had `…-171528`, now certified byte-identical
  to the tarball.
- **deepseek — complete for the files it touches.** All 5 chunks
  (`_08_y1bx`, `_1j_edf1`, `_1luyz1c`, `_15ose6x`, `_1xkpq2s`) and both `.ts` files
  (`index.ts`, `toResponses.ts`) differ and have backups. Caveat: the fix itself omits
  `_18ct13i` (§5.2).
- **vertex — complete for the files that are actually patched.** `openai-to-gemini.ts`,
  `_0o8_5h8`, `_0t1t5fj`, `_18ct13i` differ and have backups; `_14jycqh` is unpatched and
  therefore needs none.

**15 new certified-pristine backups** were installed, all verified equal to the tarball; **1**
patched file outside the briefed list (`mcp-server/server.js`) has a pre-existing backup now
certified. Revert procedure for any file: copy
`<file>.autoos-backup-pristine-3.8.50-20260930-215549` back over `<file>`.

## 7. Refusals (verbatim, as given, all honoured)

> Never push/merge/rebase/checkout.

No `git push`, `git merge`, `git rebase`, or `git checkout` of a branch was run. The only git
write was creating this lane's own worktree and committing this report.

> Do NOT modify any live file's content; no gateway restart; touch no other worktree.

Honoured: only **new** backup files were written next to the live files; no live file byte was
changed; no gateway was restarted; no other worktree was touched.

> never delete or overwrite an existing backup.

Honoured: the 15 new files use a fresh `-pristine-3.8.50-20260930-215549` name; the copy step
refuses if the destination already exists; the existing 21 backups are untouched.
