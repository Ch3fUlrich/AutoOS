# Lane: patch-backups-2 — tarball pristine-source gate

**Lane:** `patch-backups-2` under L1-alpha
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-patchbackups-2`
**Branch:** `L1-backlog/ws-patchbackups-2-20260930` (cut from `main` @ `d08f7f2`)
**Date:** 2026-09-30

## Result: STOPPED — gate (3a) FAILED, no backups installed

Step 3a requires the tarball `_18ct13i._.js` to be **identical** to the live backup
`_18ct13i._.js.autoos-backup-20260930-182908`. It is **not**. Therefore the tarball is
**not** proven to be the pristine pre-patch source for this install, and per the lane brief
("If (3a) does NOT match, STOP … instead of installing anything") **no backup files were
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
# step 1 — fetch + extract the published package
$d = "C:\Users\<user>\AppData\Local\Temp\opencode\packbackups"
New-Item -ItemType Directory -Force -Path $d | Out-Null
cd $d
npm pack omniroute@3.8.50          # -> omniroute-3.8.50.tgz (121.4 MB)
tar -xzf omniroute-3.8.50.tgz      # -> package/  (EXTRACT_OK)

# step 2 — locate target chunks
Get-ChildItem "$d\package\dist\.build\next\server\chunks\" -Filter "_0o8_5h8*"
Get-ChildItem "$d\package\dist\.build\next\server\chunks\" -Filter "_0t1t5fj*"
Get-ChildItem "$d\package\dist\.build\next\server\chunks\" -Filter "_14jycqh*"

# step 3 — SHA256 compare (Get-FileHash -Algorithm SHA256)
# step 5 — final backup listing
Get-ChildItem <live chunks> -Filter "*.autoos-backup-*"
```

`npm pack` output (abridged): `package size: 121.4 MB`, `unpacked size: 452.5 MB`,
`shasum: d7b4fce4f1b00e5e826b76855665dfae42aab97a`,
`integrity: sha512-qK6REDWQYGh8l[...]vl+az0lvyt0Mg==`, `total files: 21898`.

Live path: `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute\dist\.build\next\server\chunks\`
Tarball path: `C:\Users\<user>\AppData\Local\Temp\opencode\packbackups\package\dist\.build\next\server\chunks\`

Both `package.json` files report `name: omniroute`, `version: 3.8.50`.

## 3. Hash table

### 3a — pristine gate (MUST be identical)

| File | SHA256 | Size (B) |
|---|---|---|
| tarball `_18ct13i._.js` | `62DFB62695B7EE67296A69290EC3B84D7BCB38BB825349695848A6BBB830B81C` | 1,302,308 |
| live `_18ct13i._.js.autoos-backup-20260930-182908` | `C80549444052D0D76F37446D9581BF30646757AABE5DF5E8AFA28BD794BF725B` | 1,303,134 |

**Verdict: NOT identical.** (Sizes differ by 826 B; digests differ.)

For completeness, the live *current* `_18ct13i._.js` is a **third** distinct content:

| File | SHA256 | Size (B) |
|---|---|---|
| live current `_18ct13i._.js` | `18BC7447EE3B6852F3A0F614B471CD0A916DB986406964533B32FAEB1A29AFDE` | 1,302,588 |

So `_18ct13i._.js` exists in three mutually different forms — published 3.8.50 (tarball),
the `-182908` backup, and the live file. The `-182908` backup is **not** the published-3.8.50
file, so it cannot serve as proof that the tarball is pristine.

### 3b — target chunks vs live counterparts

| Chunk | Tarball SHA256 | Tarball size | Live (current) SHA256 | Live size | Differ? |
|---|---|---|---|---|---|
| `_0o8_5h8._.js` | `96B80E16AF41428196DD7D998099F2A621C2B753CB422EC9E64C53ADADABA57D` | 870,668 | `0EE893621B7BD591210C287F9D5CA204540932469932F53EBE337513DB87C557` | 871,423 | **differ** |
| `_0t1t5fj._.js` | `07DB3B94B8E2A74CDDED8191A76AC6626633B5A35496DBF09F7DF1A009E7583A` | 870,668 | `F03D3CD3F76BDF8A79B34E86BEB60DCFC4883A15466B4C9F50ACE6E1EBBA6035` | 871,423 | **differ** |
| `_14jycqh._.js` | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | 21,659 | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | 21,659 | identical |

`_0o8_5h8` / `_0t1t5fj` carry a live patch; `_14jycqh` is byte-identical and carries none.

## 4. Backup installation — NOT PERFORMED

Because (3a) failed, step 4 (copy tarball chunks to `<live-file>.autoos-backup-<ts>`) was
**not executed**. No live chunk content was read-modified or overwritten; no new files were
written into the live `chunks` directory. The existing backup count is unchanged at **15**.

## 5. Final backup listing (step 5)

`Get-ChildItem <live chunks> -Filter "*.autoos-backup-*"` — **15 entries, unchanged by this lane**:

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
