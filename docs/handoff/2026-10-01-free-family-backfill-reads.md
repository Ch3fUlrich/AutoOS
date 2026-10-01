# Free-Family Backfill Reads (ws-omniroute-20260930)

**Date:** 2026-10-01
**Branch:** `L1-backlog/ws-freereads-20260930` (worktree `AutoOS-ws-freereads`, base `2ff537a`)
**Family:** longcat (free-family READER lane; I am the free-family reader for these items)
**Method:** data-only reads + mechanical greps. No installs, no push/merge/rebase/checkout in existing checkouts. All claims below cite a file:line or a captured command result.

---

## Section 1 — RESCUE backfill read (2nd non-deepseek family for rescue)

**Family:** longcat
**Verdict: SUPPORTED** — the registry data and the coverage doc agree on every checkable claim. Two minor narrative notes, no data mismatches.

**Evidence (captured commands):**

- `git show 6aa8208 --stat` → 3 files: `catalog/ai-registry.json` (14 deletions), `docs/handoff/2026-09-30-laneA-providers.md` (+73/-…), `tests/test_registry.py` (renames). Commit message: "drop rescue providers.ovhcloud stub (D1 reconcile)".
- Registry at `6aa8208` parsed from `git show 6aa8208:catalog/ai-registry.json`:
  - total providers: **52** (claim "52 providers" ✓)
  - `ovhcloud` present: **False** (claim "ovhcloud removed by D1 at 6aa8208" ✓)
  - the 19 keys (`api_airforce`, `llm7`, `nscale`, `siliconflow`, `sealion`, `routeway`, `requesty`, `aion_labs`, `agnes`, `pollinations`, `g4f`, `kilo_gateway`, `ainative`, `felo`, `uncloseai`, `opencode_gateway`, `ai_horde`, `z_ai`, `qoder_ai`): all present, all `available: false`, all `$comment` contains "available is false" and a date ✓
  - model rows referencing the 19 keys: **0**; routes referencing the 19 keys: **0** ✓
  - routes: **25**, models: **71** (doc: "25 routes, 71 models, 52 providers" ✓)
  - renames verified: `opencode_gateway.omniroute_id = opencode`, `qoder_ai.omniroute_id = qoder`, `kilo_gateway.omniroute_id = kilo-gateway`, `g4f.omniroute_id = g4f-pollinations`, `felo.omniroute_id = felo-web` ✓
- Test file at `6aa8208` (`tests/test_registry.py`): `MISSING_19` list contains `routeway` (line 3103); `test_each_of_the_19_missing_providers_has_a_measured_comment` asserts `"2026-09-28"` and `"available is false"` in each `$comment` — matches the registry comment text ✓

**Minor notes (not blocking):**

1. The doc's coverage-table header says "All 20 probed … on 2026-09-30" (2026-09-30-laneA-providers.md:33), but every registry `$comment` is dated **2026-09-28** (FREEKEYS-1 measurements carried into the rescue entries by `a8fa6595`). The doc's own P0-VALIDATE section documents a 2026-09-30 re-probe, so the table-header date refers to the validation re-probe, not the original measurement — imprecise wording, internally reconciled by the doc.
2. The doc objective says "register every one as `available: false`" for all 20; at `6aa8208` it is 19 registered + 1 reconciled (ovhcloud → ws-ovh `f6f5e69`). The doc states this itself ("19 remain on this branch; the 20th (ovhcloud) was reconciled", :8-9), so the "20" in the task framing = 19+1, not a mismatch.

**Degenerate-output note:** the rescue corpus itself records one degenerate review correctly — `2026-09-30-l1b-records-and-failures.md:165` marks the `opencode/nemotron-3.5-lightning-free` review "DEGENERATE — not a review" (empty verdict, leaked scratch reasoning, repeated prompt, ended mid-token). This is the correct handling per the run hard rule; not counted as a review anywhere.

---

## Section 2 — COMBINED efficiency read (two proposal docs)

**Family:** longcat
**Verdict: COHERENT — both docs are internally consistent; no proposal would change routing-contract behaviour without a review.** One caveat: the researcher branch's definition changes carry no cross-family review record (R-orch-13 would require one before merge).

### Doc A — `2026-09-30-researcher-tier.md` (ws-researcher `c60a6c8`)

- Status is honestly stated: "Proposal — definitions added, not wired into default routing" (:3). The files-changed table lists real definition edits (registry route, combo, harness role, opencode.jsonc agent block) — these are inert until wired; selectable only via `--combo t4-researcher`.
- **No routing-contract change:** the doc recommends "do NOT wire into select_combo" (:176) and the route is absent from `select_combo`/v2 resolver logic. Verified on the branch: `t4-researcher` exists as a registry route and combo only.
- Internal consistency checks (captured):
  - Route legs vs combo models differ in prefix for 2 of 4 legs (`scaleway/…`→`scw/…`, `free_ai/…`→`free-ai/…`). This is **systematic renderer behaviour, not drift**: the pre-existing `t3-driver-free-only` shows the identical mapping (route legs `scaleway/mistral-small-3.2-24b-instruct-2506`, `free_ai/qwen7b` → combo `scw/…`, `free-ai/…`). The proposal mirrors the existing convention exactly.
  - Context/output figures agree across doc, route, and combo: 131,072 (declared 128k, clamped by `free_ai/qwen7b`), output 8,192.
  - Validation table claims "26 routes, 71 models, 33 providers"; provider count 33 verified on the branch (33 + 19 rescue = 52 ✓ cross-checks Section 1).
- **Caveat:** the doc contains **no cross-family review record** (grep for review/verdict: 0 hits). Since the definitions change no routing contract, no review is required for the proposal state — but per R-orch-13 the new route/combo/harness role would need pinned cross-family review before merge.

### Doc B — `2026-09-30-routing-design-proposals.md` (ws-designmemo `a02578d`)

- All 10 items (a)–(j) are proposed-only; every item names its L1-routing-backlog territory and stays out of it.
- **The one item that would change the routing contract is explicitly flagged:** item (g) (`--lean` as `select_combo` input) states "this changes the routing contract — `select_combo` gains a new input" and remains proposed-only. No unflagged contract change found.
- Anchor spot-checks against the doc's base `d08f7f2` (captured):
  - `tools/autoos_routing.py:317` → `def select_combo(...)` ✓ (doc item (a) anchor)
  - `tools/autoos_routing.py:36` → `CARD_VALUES = {` ✓
  - `configuration/omniroute/combos.json:96-106` → `t1-orchestrator` block, `"context": "128k"` ✓ (doc item (b) anchor, verbatim)
  - `references/state-file.md:40-41` → "At half the cap…" / "At the cap from `policy.handoff_caps`…" ✓ (doc item (c) anchor range 38-44)
- Review record present: t3-reviewer, APPROVED-WITH-NOTES, with two non-blocking notes fixed before commit (:769-776). Coherent and complete.

---

## Section 3 — MECHANICAL redaction verification (raw grep results)

**Family:** longcat
**Verdict: REDACTION DID NOT HOLD on main or most lanes.** Commits `1bb1175`/`90cd698` replaced the username with `<user>` on their own branches only; the same unredacted content persists on `main` and the lane branches.

**Commands run** (per branch, tracked files): `git grep -n -I -i 'mauls' <branch>` and `git grep -n -I 'ses_f0' <branch>`. (Note: an initial run misplaced `--` before the rev, which silently invalidated it — re-run with correct `git grep <pattern> <branch>` order; all results below are from the corrected runs.)

### Required branches — hit counts

| Branch | `mauls` (username path) | `ses_f0` (session ids) |
|---|---|---|
| main | 1 | 5 |
| L1-backlog/ws-providers-rescue-20260930 | 1 | 0 |
| L1-backlog/ws-nebiuswave2-20260930 | 2 | 6 |
| L1-backlog/ws-records-20260930 | 2 | 9 |
| L1-backlog/ws-mergecheck-20260930 | 1 | 4 |
| L1-backlog/ws-revaudit-20260930 | 3 | 12 |
| L1-backlog/ws-gw-admission-20260930 | 0 | 0 |
| L1-backlog/ws-fixes-20260930 | 8 | 0 |

### Hit locations (branch:file:line)

**`mauls`:**
- main: `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`
- ws-providers-rescue: `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`
- ws-nebiuswave2: `docs/handoff/2026-09-30-laneFreeWire.md:4`; `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`
- ws-records: `docs/handoff/2026-09-30-l1b-records-and-failures.md:78`; `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`
- ws-mergecheck: `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`
- ws-revaudit: `docs/handoff/2026-09-30-laneFreeWire.md:4`; `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`; `docs/handoff/2026-10-01-review-family-audit.md:70`
- ws-fixes: `docs/handoff/2026-09-30-lanePatchIntegrity.md:4,36,43,57,329`; `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`; `logs/handoff-sessions/DONE-ws-patch-integrity.md:3,55`

**`ses_f0`:**
- main: `docs/handoff/2026-09-30-laneOVH-review.md:5,9`; `docs/handoff/2026-09-30-ws-omniroute-branch-merge-checklist.md:14,35,46`
- ws-nebiuswave2: `docs/handoff/2026-09-30-laneFreeWire.md:338,339,340`; `logs/handoff-sessions/DONE-ws-freewire.md:123,124,125`
- ws-records: `docs/handoff/2026-09-30-l1b-records-and-failures.md:84,124,128,160,161,162,163,164,165`
- ws-mergecheck: `docs/handoff/2026-09-30-ws-omniroute-branch-merge-checklist.md:14,29,52,63`
- ws-revaudit: `docs/handoff/2026-09-30-laneFreeWire.md:338,339,340`; `docs/handoff/2026-10-01-review-family-audit.md:18,23,25,26,37,39`; `logs/handoff-sessions/DONE-ws-freewire.md:123,124,125`

(Session-id strings are masked here per repo redaction policy; full values are in the grep output captured by the run.)

### Branches outside the required list (read in this run — hits found)

- `L1-backlog/ws-designmemo-20260930`: `mauls` ×2 — `docs/handoff/2026-09-30-routing-design-proposals.md:20`, `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`; `ses_f0` ×1 — `docs/handoff/2026-09-30-routing-design-proposals.md:769`. **This doc was committed 2026-09-30, after the redaction commits** — the redaction did not hold here either.
- `L1-backlog/ws-researcher-20260930`: `mauls` ×1 — `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`; `ses_f0` ×0.
- Full sweep (all 58 local branches): the single most pervasive hit is `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19` (username path) on main and 13+ lane branches — each lane carries its own unredacted copy.

### What the redaction commits actually did

- `git branch --contains 1bb1175` → only `L1-backlog/ws-gw-admission-20260930`; `git branch --contains 90cd698` → only `L1-backlog/ws-p0-admission-fix-20260930`. Both branches grep clean (0/0).
- `git show 1bb1175` diff: `C:/Users/mauls/…` → `C:/Users/<user>/…` in `docs/handoff/2026-09-30-workstation-omniroute-handoff.md`; `C:\Users\mauls\…` → `C:\Users\<user>\…` in `measure-admission.ps1`. Commit message: "no session-id leaks found".
- `git show 90cd698`: redacted `docs/handoff/2026-09-30-laneP0-admission.md`, `docs/handoff/2026-09-30-workstation-omniroute-handoff.md`, `logs/handoff-sessions/DONE-ws-p0-admission.md` — on that branch only.

**Conclusion:** the redaction held only on the two branches the commits landed on. It never propagated to `main` (1 `mauls` + 5 `ses_f0` hits) or to 6 of the 8 required lane branches. Any merge of those lanes into main without a re-redaction pass would reintroduce the username path and session ids into main's history.

---

## Review record

- **Reader:** longcat (free-family READER lane, ws-freereads-20260930)
- **Verdicts:** Task 1 SUPPORTED; Task 2 COHERENT (caveat: researcher definitions lack a review record); Task 3 REDACTION DID NOT HOLD outside the two admission branches.
- **Nothing degenerate in this read**; all commands returned cleanly. The one degenerate output encountered in the corpus (records lane, nemotron-3.5-lightning-free review) is already flagged DEGENERATE in its source doc and was not counted as a review.
