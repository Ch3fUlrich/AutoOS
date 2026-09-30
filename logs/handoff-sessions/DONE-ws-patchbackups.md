# DONE — ws-patchbackups (lane patch-backups-2)

**Status:** STOPPED at gate (3a) — no backups installed.
**Branch:** `L1-backlog/ws-patchbackups-2-20260930` (base `main` @ `d08f7f2`)
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-patchbackups-2`
**Date:** 2026-09-30

## What was done

1. `npm pack omniroute@3.8.50` in `C:\Users\mauls\AppData\Local\Temp\opencode\packbackups`
   → `omniroute-3.8.50.tgz` (121.4 MB, shasum `d7b4fce4f1b00e5e826b76855665dfae42aab97a`).
2. `tar -xzf omniroute-3.8.50.tgz` → `package/` (21898 files).
3. Located chunks under `package/dist/.build/next/server/chunks/`:
   `_0o8_5h8._.js` (870668), `_0t1t5fj._.js` (870668), `_14jycqh._.js` (21659),
   plus `_18ct13i._.js` (1302308).
4. SHA256-compared (Get-FileHash) tarball vs live. **Gate (3a) FAILED**:
   - tarball `_18ct13i._.js` = `62DFB62695B7EE67296A69290EC3B84D7BCB38BB825349695848A6BBB830B81C` (1,302,308 B)
   - live `_18ct13i._.js.autoos-backup-20260930-182908` = `C80549444052D0D76F37446D9581BF30646757AABE5DF5E8AFA28BD794BF725B` (1,303,134 B)
   - → **not identical** (live current `_18ct13i._.js` is a third form: `18BC7447…AFDE`, 1,302,588 B).

## What was NOT done (per brief)

- No `<live-file>.autoos-backup-<ts>` files were created or overwritten.
- No live chunk content was modified. Existing backup count unchanged at 15.
- No reapply script, other patch set, gateway, or other worktree was touched.
- No push / merge / rebase / checkout.

## Hash table (3b, tarball vs live current)

| Chunk | Tarball SHA256 | Live SHA256 | Differ? |
|---|---|---|---|
| `_0o8_5h8._.js` | `96B80E16…DA57D` | `0EE89362…7C557` | differ |
| `_0t1t5fj._.js` | `07DB3B94…E7583A` | `F03D3CD3…A6035` | differ |
| `_14jycqh._.js` | `69D3CD37…E6040D` | `69D3CD37…E6040D` | identical |

## Refusals honoured (verbatim from order)

- "Never push/merge/rebase/checkout."
- "Do NOT touch reapply scripts, other patch sets, gateways, or other worktrees."
- "If (3a) does NOT match, STOP and report that the tarball is not a reliable pristine source
  (with the hashes) instead of installing anything."

## Outcome

Tarball is **not proven** to be the pristine pre-patch source for the live install; the named
live backup does not match published 3.8.50. Escalated to L1-alpha — see
`docs/handoff/2026-09-30-lanePatchBackups.md` §7 for the two candidate explanations.

*Result recorded by patch-backups-2 (L1-alpha).*
