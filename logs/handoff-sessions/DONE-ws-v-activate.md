# DONE — ws-verify-activate (lane V-activate)

**Lane:** `ws-verify-activate` (independent verification) under L1-backlog
**Branch:** `L1-backlog/ws-verify-activate-20260930` (from `main` @ `d08f7f2`)
**Full report:** `docs/handoff/2026-09-30-laneV-activate.md`

## Outcome

Independent GO/NO-GO for the planned `:20128` restart. **Verdict: GO.**

1. **Patch inventory (independent).** Streamed every member of the certified pristine
   `omniroute@3.8.50` tarball against the live npm-global install: **21898 members compared,
   19 differing, 0 missing.** **17/19 patched files have a certified pristine revert path**
   (byte-identical to the tarball). **2 have no backup of any kind:**
   `open-sse/config/providers/registry/scaleway/index.ts` and
   `open-sse/translator/paramSupport.ts` — the scaleway-qwen clamp (residual risk R1).
2. **Coexistence + live probe proof.** One isolated gateway (`:20145`, its own `DATA_DIR`) from
   the same patched package, shared `:20128` untouched. Both probes run concurrently in the
   same window: **reasoning probe PASS** (4/4 `step2=200`,
   `400 reasoning error reproduced: False`) and **vertex trailing-model shapes + control `200`**
   on `vertex/gemini-3.8-flash`; no `400` from any probe in any round. Both probes passed in the
   same window in four consecutive rounds (rounds 2–5); the only non-200s were provider-quota
   `429`s and one transient `502`.
3. **Unaccounted clamp.** It is gated on `provider === "scaleway"` **and**
   `/^qwen3-235b-a22b-instruct-2507$/`; neither probe sends a scaleway model. Byte-exact delta
   is a purely additive 106-byte rule insertion (+649 CRLF line endings). **It cannot affect
   either probe**, confirmed empirically by the passing probes.

## Evidence / commands

- Tarball certified against the npm registry: computed
  `sha512-qK6REDWQ…yt0Mg==` == `dist.integrity`, sha1 == `dist.shasum`
  (`tools/v-activate-tarball-integrity.py`).
- `tools/verify-patch-inventory.py`, `tools/v-activate-backup-table.py`,
  `tools/v-activate-clamp-evidence.py` — in-memory tarball streaming, no Temp extraction.
- `tools/v-activate-both-probes.py` — concurrent, same-window, same-instance probe rounds.
- Verbatim outputs in `docs/handoff/2026-09-30-laneV-activate.md` §3–§5.

## Constraints honoured

Read-only package; shared `:20128` never touched or restarted; no push/merge/rebase/checkout;
`configuration/omniroute/*` inspected only; every process started by this lane killed by PID
(launcher `121344` + child `125716`); unrelated pre-existing listener `:20138` (pid `118016`)
left alone.

## Residual risks

- **R1:** the two scaleway-qwen files have no local backup — revert needs re-extraction from the
  certified tarball.
- **R2:** `_0o8_5h8._.js` / `_0t1t5fj._.js` are now attributed (the 106-byte clamp insertion);
  they do have certified backups.
- **R3:** vertex/gemini quota is tight (`429`, ~5-minute reset) — a probe-run artefact, not a
  code defect.

## Reviewer

Nonce-gated, different family. **Family:** Google Gemini (`omniroute/vertex-pro`, direct
Gemini 3.1 Pro). **Nonce returned:** `VACT-7Q3Z-9F2K-5R8M` (exact match). **Verdict:**
**GO-WITH-CAVEAT** — concurred with the GO call and with R1; flagged two wording caveats
("vertex probe passed" is broad since `gemini/gemini-2.5-flash` was `429` in every round;
claim C leans on the reasoning probe for both probes). Both are already disclosed in the
report (§4.2/§4.3) and are recorded in `docs/handoff/2026-09-30-laneV-activate.md` §8.
