# DONE — ws-patchbackups (patch-backups-3)

**Lane:** `patch-backups-3` under L1-alpha
**Branch:** `L1-backlog/ws-patchbackups-3-20260930` (from `main` @ `d08f7f2`)
**Full report:** `docs/handoff/2026-09-30-lanePatchBackups.md` (this lane's report appended to the `patch-backups-2` gate report)

## Outcome

Certified-pristine revert paths installed for **every patched file** in the clamp, deepseek and
vertex patch sets, sourced from the published `omniroute@3.8.50` npm tarball
(`shasum d7b4fce4f1b00e5e826b76855665dfae42aab97a`). **15 new backups** written; **1 file
(`_14jycqh._.js`) is byte-identical** to the tarball and correctly got none. No live file's
content was modified; no gateway restarted; no other worktree touched.

Backups: `<file>.autoos-backup-pristine-3.8.50-20260930-215549` next to each live file under
`C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute`.

All hashes/find-strings were taken by **streaming members straight out of the .tgz**
(`tar -xOf` → in-memory SHA256), because the host deleted files from Temp extractions
(single-member, full fresh, and the predecessor's extraction all lost members). Cross-checked:
`_04g0p_r._.js` → 257546 B / `A1D23A5B…`; single-member extracts of `_18ct13i`, `_0o8_5h8`,
`index.ts` matched; full fresh extract matched all 16.

## Hash table (tarball vs live) + installed backup

| File (set) | Tarball SHA256 | Live SHA256 | Patched? | Installed backup |
|---|---|---|---|---|
| `_04g0p_r._.js` (clamp) | `A1D23A5BA40E79615A1C3364430D27BB8093411B126FDF932E53458B986B20D2` | `1779016D5BF6BC2E4717A230586F01FF9E70E0CD2690F202D307719BFF8D2C33` | yes | `…pristine-3.8.50-20260930-215549` |
| `_0o50usg._.js` (clamp) | `29C37C4F9D80274827DD81EBF5EA2AFE0EF281B5C2A1ACC74BAC95FDD882E429` | `7462DF651797CD64C74A9C7777B5621A1E24E4186E09B9486CBFE293E07AE0B8` | yes | `…pristine-3.8.50-20260930-215549` |
| `_0wr-zm3._.js` (clamp) | `6EECBD44731825E74F4E8A1E02B790ADEB39CEA4C5F4234F9178C893681BC8F1` | `F681BE5261BF38DE3986D0AA3F5A11F1FA67970A1357A7948B6222DF4B62009C` | yes | `…pristine-3.8.50-20260930-215549` |
| `_1mq9y97._.js` (clamp) | `E00AC24377570A3C9A572EDD3396A9D5E19B1CF52747A7680587035970AA805D` | `173F8C7E1C7A26C10B3E4A2239888561A9CBC6887A7F536B12130431AC99032E` | yes | `…pristine-3.8.50-20260930-215549` |
| `_08_y1bx._.js` (deepseek) | `97729CF68F77CE10D5DBEE6498C5A601406CBA144A027E66760A2D27537FE67B` | `199EF4D865D8555633C606401CDFCFFDFA34B53E40165347700A380FFB4D2ED4` | yes | `…pristine-3.8.50-20260930-215549` |
| `_1j_edf1._.js` (deepseek) | `956F7AF17980B28468869E4CBE1D2732D762B7BB018E3877A8A0F6BE6708472D` | `643A0661B156EE24B98F8E25CBB8D69FA0D7EB370F977AFD52853CB6A184BFB9` | yes | `…pristine-3.8.50-20260930-215549` |
| `_1luyz1c._.js` (deepseek) | `9827226EDC6CFC1B3A53F8C5097AE3E482ACA65D1E085E40BEFA73A3DCA4BB54` | `2834DAC905481C48506E01BA5B49440BA5FBCC2BBE9B0EB3342A02C62864147D` | yes | `…pristine-3.8.50-20260930-215549` |
| `_15ose6x._.js` (deepseek) | `E04039A5D67DCB680BC57448665FE1D5BAA71950919A958174F099BB4E786953` | `6DD65402CB51B160D7C72BFAB77A9E2E0718FEE677FDA76B9603C9473F88EAC4` | yes | `…pristine-3.8.50-20260930-215549` |
| `_1xkpq2s._.js` (deepseek) | `F5402A53D82ABAFE6AFB3F5008043D5293AFC69B79D09354A27D47972B57268C` | `EF7137A80E09B88C672550555B10D16142509F87182225ED5556BF8F4395864A` | yes | `…pristine-3.8.50-20260930-215549` |
| `open-sse/translator/index.ts` (deepseek) | `F2E1C69CE98D1189F73EACD748D21CDACD02FEDF55A62E581EBD16C98F570F74` | `5029B12CDD32C148CAAB54BDF7A552C4B416E657FCD44BB9EA41BF678C9120DA` | yes | `…pristine-3.8.50-20260930-215549` |
| `…/openai-responses/toResponses.ts` (deepseek) | `4AE24AA23FB9765EEB7735DB0168AD616156BBBB907E3120135FEDEBC097093F` | `88C52A0B8D24805EB7AAE78DF56A02131ED56ED7B36992F9C50CC4C6056164C4` | yes | `…pristine-3.8.50-20260930-215549` |
| `_0o8_5h8._.js` (vertex) | `96B80E16AF41428196DD7D998099F2A621C2B753CB422EC9E64C53ADADABA57D` | `0EE893621B7BD591210C287F9D5CA204540932469932F53EBE337513DB87C557` | yes (unattributed) | `…pristine-3.8.50-20260930-215549` |
| `_0t1t5fj._.js` (vertex) | `07DB3B94B8E2A74CDDED8191A76AC6626633B5A35496DBF09F7DF1A009E7583A` | `F03D3CD3F76BDF8A79B34E86BEB60DCFC4883A15466B4C9F50ACE6E1EBBA6035` | yes (unattributed) | `…pristine-3.8.50-20260930-215549` |
| `_14jycqh._.js` (vertex) | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | `69D3CD37D3C7BF7F42D617FC0328D192966D2D61E238BE6FB9ACF8B1E2E6040D` | **no — identical** | *(none)* |
| `_18ct13i._.js` (vertex / deepseek anchor) | `62DFB62695B7EE67296A69290EC3B84D7BCB38BB825349695848A6BBB830B81C` | `18BC7447EE3B6852F3A0F614B471CD0A916DB986406964533B32FAEB1A29AFDE` | yes | `…pristine-3.8.50-20260930-215549` |
| `…/request/openai-to-gemini.ts` (vertex) | `0DBFA6D722C7B0F338EE8E43DF8EDFD9CFAEF3EAA3E6A5DEEFFAF357FD2A7B7F` | `B913F3CAF19D321FB5CBB78C8BAC5C18FEDF5C291EC3594A2C186B67EA3512B4` | yes | `…pristine-3.8.50-20260930-215549` |
| `dist/open-sse/mcp-server/server.js` (clamp script, **not in briefed list**) | `DEF35CCFB8481E767A492FF5927FB1932E5FD31F10330DAF86B2C5F15E3E03D9` | `A3E833ECA8725E1693464250CF2EBADE96D4D73D6CBB6964E0BFD0C8242BD386` | yes | existing `…-171528` is byte-identical to tarball (certified) |

Each installed backup was re-hashed after copy and equals its tarball hash (15/15).

## Final `Get-ChildItem … *autoos-backup-*` listing (36 entries; 15 new)

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

## Reapply find-strings vs tarball (verdicts)

- **clamp `patch-gateway-clamp.ps1` — MATCHES.** MCP `$oldGate` `MATCH=True`; MCP `$oldUnit`
  `MATCH=True`; already-patched marker absent. `$gatePattern` matches all 4 chunks, e.g.
  `let a=(0,n.getResolvedModelCapabilities)(e);if(!0!==a.supportsThinking)return null;`. A scan
  of all 11504 chunks found exactly those 4.
- **deepseek `reason-fix-reapply.ps1` — MATCHES.** `$findA` in `_08_y1bx`,`_1j_edf1`,`_1luyz1c`
  **and `_18ct13i`**; `$findB` in `_15ose6x`,`_1xkpq2s`; `index.ts` `findIdx`+`findComment`;
  `toResponses.ts` find1–find4 — all `MATCH=True`.
- **vertex `vertex-trailing-turn-reapply.ps1` — DOES NOT MATCH (defect).** `.ts`
  `mergeConsecutiveSameRoleContents(tb.messages)` `MATCH=False` (name appears only as an
  import/re-export); chunk `mergeConsecutiveSameRoleContents(e.messages)` `MATCH=False` for all
  4 of its files. The working chunk patcher is `tools/apply-vertex-patch.py`, whose `.contents`
  anchors match all 6 chunks it actually patches (`_08_y1bx`,`_1j_edf1`,`_1luyz1c`,`_15ose6x`,
  `_1xkpq2s`,`_18ct13i`).

## Discrepancies (reported, not fixed)

1. `_14jycqh._.js` is byte-identical to the tarball → **not patched**, despite
   `docs/handoff/2026-09-30-laneF1-vertex.md` listing it as patched.
2. The deepseek fix misses `_18ct13i._.js`: it carries `$findA` in the tarball and still carries
   it live; `reason-fix-reapply.ps1` omits the file.
3. The vertex `.ps1` reapply cannot reproduce the vertex patch (find-strings match nothing).
4. `_0o8_5h8._.js` / `_0t1t5fj._.js` differ from the tarball (+755 B) but carry no marker from
   any of the three patch sets — unattributed.

## Complete certified-pristine revert path

- **clamp:** complete — 4/4 chunks backed up; `mcp-server/server.js` already had a
  tarball-identical backup.
- **deepseek:** complete for the files it modifies — 5 chunks + 2 `.ts` backed up (fix itself
  misses `_18ct13i`, see discrepancy 2).
- **vertex:** complete for the files actually patched — `openai-to-gemini.ts`, `_0o8_5h8`,
  `_0t1t5fj`, `_18ct13i` backed up; `_14jycqh` needs none (identical).

Revert: copy `<file>.autoos-backup-pristine-3.8.50-20260930-215549` back over `<file>`.

## Refusals honoured

No push/merge/rebase/checkout of any branch; no live-file content modified; no gateway restart;
no other worktree touched; no existing backup deleted or overwritten.
