# Lane Changelog — one CHANGELOG bullet per wave/lane (2026-10-01)

**Lane:** `L1-backlog/ws-changelog-20260930`
**Worktree:** `C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-changelog`
**Base:** `f2d8d607` (`doc(handoff): record 2 nonce-gated reviews … for T1SECOND`)
**Date:** 2026-10-01
**Writer:** `changelog` (pinned)
**Scope:** `CHANGELOG.md` `## [Unreleased]` only — no other lane's files, no gateway.

> **REVIEW NONCE: `CHANGELOG-NONCE-9Kx2Vq7M`** — a reviewer must quote this string back,
> read from THIS file, to prove it read the evidence and not a summary.

---

## 0. Job and constraint

The operator asked that **every wave/lane of run `ws-omniroute-20260930`** carry proper
`## [Unreleased]` bullets, in the file's own style (**Keep a Changelog**; house bullet
`- NAME (date, actor): description + gates`). This worktree is pinned to the
`ws-changelog` branch (base `f2d8d607`) and **never** works or commits in the main
checkout. Sibling lanes' evidence was read **read-only** from their own worktrees
(`C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-*`); nothing there was
modified, pushed, merged, rebased or checked out.

## 1. What was added (11 bullets)

Inserted at the top of `## [Unreleased]` (newest first), existing entries untouched:

| Bullet | Lane / evidence read |
|---|---|
| T1SECOND (2026-10-01) | `AutoOS-ws-invariant` — `DONE-ws-invariant-fix.md`, commit `c1ca90e6` (on this branch) |
| Resilience defaults (2026-10-01, `51fd01d2`/`3b0135e3`) | `AutoOS-ws-resenv` — `DONE-ws-resilience-env.md` |
| Patch artifacts (2026-10-01) | `AutoOS-ws-patchbackups-3`, `AutoOS-ws-patchr1`, `AutoOS-ws-fixes` — `DONE-ws-patchbackups.md`, `DONE-ws-patch-verify.md`, `DONE-ws-patch-integrity.md` |
| Probe scripts (2026-09-30/10-01) | `AutoOS-ws-free-probe`, `AutoOS-ws-clampprobe`, `AutoOS-ws-f1`, `AutoOS-ws-fixes` |
| Admission fixes (2026-09-30) | `AutoOS-ws-admission-fix`, `AutoOS-ws-p0` — `DONE-ws-admission-fix.md`, `DONE-ws-p0-admission.md` |
| Nebius removal (2026-10-01) | `AutoOS-ws-nebius2combos` (combos half, `f6e2e5ad` on this branch) + registry half `ws-nebius2`/`ws-nebiuswave2` (`172a92bb`, `7eaf91a9`) |
| FREEWIRE + gemini retention (2026-09-30) | `AutoOS-ws-freewire` — `2026-09-30-laneFreeWire.md`, `DONE-ws-freewire.md` |
| OVH legs + registry (2026-09-30) | `AutoOS-ws-ovh`, `AutoOS-ws-ovh-finish` — `DONE-ws-ovh.md`, `DONE-ws-ovh-finish.md` |
| Vertex leg + 1M contexts (a5bcb69, 2026-09-30) | `AutoOS-ws-combos` — `DONE-ws-combos.md` (on this branch as `a5bcb69b`) |
| REVIEWGATE-2FAM (2026-09-30) | `origin/L1-backlog/reviewgate-2fam` — `d0f70f15` (already carries its own CHANGELOG entry; noted here for completeness) |
| Redactions (2026-09-30) | `AutoOS-ws-hygiene-main` (`4ba83ed0`), `AutoOS-ws-p0` (`DONE-ws-p0-admission.md` §Redaction) |

## 2. The gemini correction, recorded as directed

The operator **reversed** the gemini exclusion. The FREEWIRE bullet therefore reads:

> **Operator correction: the gemini exclusion was reversed — gemini retained; usage governed
> by the repeated-429 backoff policy (3×429/120 s → 300 s cooldown per leg; 30 min park).**

It does **not** say "gemini excluded". The `ws-freewire` evidence file
(`docs/handoff/2026-09-30-laneFreeWire.md` §3) records the earlier removal; the operator's
reversal is the governing statement and the CHANGELOG carries the corrected wording.

## 3. Verification (quoted)

**No dedicated CHANGELOG validation test exists.** `grep -rl CHANGELOG tests/` returns only
`tests/linux/33-documentation.sh`, `tests/test_keys_file.py`, `tests/test_skill_rules.py`, and
in each the only mention is a comment or an *exclusion* (e.g. `test_skill_rules.py:200`
skips `CHANGELOG.md` when checking R-id citations). The two Python tests were run anyway:

```
$ python tests/test_skill_rules.py
Ran 26 tests in 2.231s
OK
$ python tests/test_keys_file.py
Ran 14 tests in 0.274s
OK
```

`tests/linux/33-documentation.sh` was **not** run: it is a bash suite (WSL) and its only
CHANGELOG reference is the comment at `:1129` ("CHANGELOG.md is the history"), not an
assertion on CHANGELOG content.

Diff stat and keyword greps (the greps are `Select-String -Pattern` with
`-CaseSensitive:$false`, equivalent to `grep -in`):

```
$ git --no-pager diff --stat
 CHANGELOG.md | 11 +++++++++++
 1 file changed, 11 insertions(+)
```

```
7:  - T1SECOND (2026-10-01): ...
8:  - Resilience defaults (2026-10-01, commits `51fd01d2`/`3b0135e3`): ... repeated-429 ...
9:  - Patch artifacts (2026-10-01): certified-pristine revert paths ...
10: - Probe scripts (2026-09-30/10-01): ...
11: - Admission fixes (2026-09-30): `chat_admission_busy` ... ≥3 concurrent heavy ...
12: - Nebius removal (2026-10-01): every `nebius/*` leg is gone ...
13: - FREEWIRE + gemini retention (2026-09-30, commits `7eff6020`/`4cb49b43`/`2ff537a4`): ...
14: - OVH legs + registry (2026-09-30, commits `f6f5e695`/`7e329c7e`/`158818ea`): `providers.ovhcloud` ...
15: - Vertex leg + 1M contexts (a5bcb69, 2026-09-30): ...
16: - REVIEWGATE-2FAM (2026-09-30, operator): ...
17: - Redactions (2026-09-30, public repo): ...
```

Keyword coverage: `FREEWIRE` (13), `gemini` (13), `NEBREMOVAL`/`nebius` (12), `OVH`/`ovhcloud`
(14), `vertex` (15), `admission`/`chat_admission_busy` (11), `repeated-429`/`429` (8),
`probe` (10), `REVIEWGATE` (16), `T1SECOND` (7). Everything the operator listed is present.

## 4. New standing rule (operator direction)

**Every lane's DONE note must carry a "CHANGELOG bullet" line** naming the bullet that
covers it (or stating explicitly that the lane changed no user-visible behaviour). A DONE
note without that line is incomplete. This is now the third thing a DONE note records
alongside landed commits and remains.

## 5. DONE notes this branch could NOT update (for L0 to chase)

The following DONE notes live on sibling branches that are **not** reachable from
`L1-backlog/ws-changelog-20260930`, so they were read read-only and cannot be edited from
here. L0 (or the owning lane) should add the "CHANGELOG bullet" line to each:

- `DONE-ws-admission-fix.md`, `DONE-ws-p0-admission.md`, `DONE-ws-gw-admission.md`
- `DONE-ws-applyjson.md`, `DONE-ws-clamp-probe.md`, `DONE-ws-combos.md`
- `DONE-ws-dl-timeout.md`, `DONE-ws-suite-fix.md`, `DONE-ws-suite-diag.md`
- `DONE-ws-f1-vertex.md`, `DONE-ws-patch-fix.md`, `DONE-ws-patch-r1.md`, `DONE-ws-patch-verify.md`,
  `DONE-ws-patchbackups.md`, `DONE-ws-patch-integrity.md`
- `DONE-ws-failures-doc.md`, `DONE-ws-fixes.md`, `DONE-ws-free-probe.md`
- `DONE-ws-incident.md`, `DONE-ws-leghealth.md`, `DONE-ws-leghealth2.md`
- `DONE-ws-merge-map.md`, `DONE-ws-ovh.md`, `DONE-ws-providers-rescue.md`, `DONE-ws-qwenclamp.md`
- `DONE-ws-resilience-env.md`, `DONE-ws-review-wave1.md`, `DONE-ws-sweep.md`,
  `DONE-ws-sweep-review.md`, `DONE-ws-v-activate.md`

On **this** branch the DONE notes are `DONE-ws-freewire.md`, `DONE-ws-nebius-combos.md`,
`DONE-ws-invariant-fix.md`, `DONE-ws-ovh-finish.md` (+ `OPERATOR-ws-ovh-finish.md`); they are
covered by bullets 7, 6, 1 and 8 respectively.

## 6. Reviews

Two `t3-reviewer` subagents, **free model families only** (operator: no DeepSeek /
`t3-driver-clean`), read-only, nonce-gated on `CHANGELOG-NONCE-9Kx2Vq7M`. Verdicts recorded
in the DONE note and the lane's final message.
