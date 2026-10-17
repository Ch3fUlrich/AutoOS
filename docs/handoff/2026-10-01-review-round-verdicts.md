# Review round — cross-family verdicts, 2026-10-01 (run `ws-omniroute-20260930`, under L1-beta)

Three NEW cross-family reviews of high-risk fixes that were **not** covered by two
non-deepseek families. Every reviewer below is a free, non-deepseek family. The
`omniroute/t3-driver-clean` combo was **excluded on purpose** — that combo is deepseek,
the same family as the writers, so a review from it is not cross-family (L0 corrected
this; it is not used anywhere in this round).

Method: `git show` / `git diff` on the target commits in the worktree
`AutoOS-ws-revround` (branch `L1-backlog/ws-revround-20260930`, base `2ff537a`).
Reviewers read the **committed** code. All reviews read-only: no edits, no commits, no
push/merge/rebase/checkout.

## Family table

| Seat | Model id | Family | Distinct from writer? |
|---|---|---|---|
| A | `opencode/nemotron-3-ultra-free` | nvidia | yes — writer was deepseek |
| B | `opencode/longcat-2.5-preview-free` | meituan | yes — writer was deepseek |
| C | `opencode/space-bunny-free` | (see caveat below) | yes — writer was deepseek |

**Caveat on seat C's family, measured not assumed.** `reviewer_family("opencode/space-bunny-free",
catalog/ai-registry.json)` returns `None` — measured by loading `tools/autoos-agent.py` and
calling the function against the registry. There is **no** `models` row and **no**
`policy.reviewers` row for `space-bunny-free` (grepped: no `bunny`/`space`/`pickle` key in
`models`; the 11 `policy.reviewers` models are listed in the appendix). So seat C is a real,
distinct model from a non-deepseek vendor, but **it is not a family the gate can name** —
under `d0f70f1` a record citing it would be refused as "not a known reviewer". That is a
finding about the registry, not about the reviewed code, and it is why the registry gap
below is recorded.

---

## Target 1 — Admission fix (`880ec58`, `1179e3f`, `c3e139c`)

**Reviewer:** seat A, session `ses_f09b0dbdfffethjZHisdTqEh6k`, `opencode/nemotron-3-ultra-free`
(family **nvidia**).

**Verdict (verbatim):** `VERDICT: PASS`

Findings (verbatim, condensed to one line each as the reviewer wrote them):

- `configuration/omniroute/apply.ps1:88-107` — the `%APPDATA%` fallback produces a **relative**
  `npm\omniroute.cmd` if `$env:APPDATA` is unset, so `Test-Path` would check the CWD.
  Reviewer's own assessment: *theoretical edge case, not a practical defect*.
- `configuration/autostart/Start-AutoOSStack.ps1:54-71` — `exit 1` on a missing shim **changes
  the contract**: the old `elseif (-not (Get-Command omniroute …))` branch printed a message and
  **fell through** to start LiteLLM/OpenHands/opencode; the new code aborts the whole autostart
  sequence. Intentional per `1179e3f` and correct for a gateway-dependent stack, but callers
  should know the logon task now hard-fails instead of partially succeeding.
- `tests/run-tests.ps1:9805` — the regex `(?s)Test-Path -LiteralPath \$omnirouteCmd.{0,200}?exit 1`
  is **brittle**: it can match an unrelated `Test-Path` and `exit 1` 200 chars apart in a
  different branch (false positive), or fail if legitimate code between them exceeds 200 chars
  (false negative). Passes today because they are ~50 chars apart.
- `configuration/start-stack.ps1:242` — bare `Start-Process -FilePath 'opencode'`, the same
  `.ps1`-shim class, remains unfixed (documented as a known issue).
- `configuration/autostart/Start-AutoOSStack.ps1:110` — bare `opencode`, same class, unfixed.
- `configuration/litellm/start-litellm.ps1:91` — bare `litellm`, same class, unfixed.

**My independent verification of the reviewable claims** (measured, not taken on trust):

- The three unfixed sites are real — `git grep -E "Start-Process -FilePath '(opencode|litellm)'`
  at `1179e3f` returns exactly `Start-AutoOSStack.ps1:129`, `start-litellm.ps1:91`,
  `start-stack.ps1:263`. (The reviewer cited `:110` and `:242`; the line numbers differ from my
  grep, the files and the defect class match. Line-number drift is the reviewer's, not a
  different finding.)
- The fall-through contract change is real — `git show 1179e3f^:configuration/autostart/Start-AutoOSStack.ps1`
  lines 39-51 show the old `elseif` branch printing and continuing into the `# 2. LiteLLM fallback
  proxy` section. This is the one behaviour change in the target with real user-visible blast
  radius, and the reviewer is right that it is a contract change, not a pure refactor.
- Ordering is correct: in all three files the `$env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT` guard is
  set **before** `Start-Process`, so the child inherits it.

**Real findings that escaped:** the brittleness of the `.{0,200}?exit 1` regex is a genuine test
weakness (a structural check would be `(?s)if \(-not \(Test-Path[^{]*\{.{0,400}?exit 1`, or simply
running the launcher with an empty `PATH`). The three unfixed same-class sites were **already
recorded, not fixed** in `c3e139c` — the reviewer re-found a known gap, which is a confirmation
rather than an escape.

**Prior coverage on this target:** 2 non-deepseek families already (nvidia PASS, meituan FAIL that
drove `1179e3f`, per `c3e139c`). This round adds a **third** non-deepseek family confirming the
post-fix state.

---

## Target 2 — Patch-fix (`6f93452`, `9c444e4`)

**Reviewer:** seat C, session `ses_f09b0a1f6ffex6uTI8PQVfP4aG`, `opencode/space-bunny-free`.

**Verdict (verbatim):** `VERDICT: FAIL`

This is the round's one **real escape**. The reviewer returned a substantive review, and its
headline finding contradicts the lane's own DONE note.

### The escaped finding — backups are overwritten mid-run (AGENTS.md hard rule 5)

`tools/vertex-trailing-turn-reapply.ps1:51,64-65,84-85` — the run-level `$stamp` is computed once,
`Backup-File` derives `"$p.autoos-backup-$stamp"`, and each of the **six chunk files is written by
two** `Try-Replace` entries (12 triples in `$chunkPatches`, two per file). The second call's
`Copy-Item -Force` **overwrites the pristine backup taken by the first** with the already
half-patched file. For 6 of the 7 files written, the surviving backup is *not* the user's
original; restoring it leaves one of the two strips injected. That directly contradicts the
script header, the README, and both handoff docs, which all promise "every file backed up before
its first modification".

**Independently confirmed by me:**

- `git show 6f93452:tools/vertex-trailing-turn-reapply.ps1` line 51 `$stamp = Get-Date -Format
  'yyyyMMdd-HHmmss'`; line 64 `$bak = "$p.autoos-backup-$stamp"`; line 65 `Copy-Item -LiteralPath
  $p -Destination $bak -Force`; line 84 `$bak = Backup-File $p`; line 85 the single write site.
- Lines 92-114 confirm each of `_08_y1bx` / `_18ct13i` / `_1j_edf1` / `_1luyz1c` / `_15ose6x` /
  `_1xkpq2s` appears **exactly twice** → same `$p` → same `$bak` → second copy wins.

The fix-worker pattern the finding implies: writer repairs the anchors → reviewer finds the
backup collision → fix commit makes the backup name unique per write (or takes the backup once
per file before the first write, guarded by a "already backed up this run" set) → re-review.

### Other findings from seat C

- **Tracked files carry a username path** — `docs/handoff/2026-09-30-lanePatchFix.md:4,15,17,259,315,319`
  and `logs/handoff-sessions/DONE-ws-patch-fix.md:4,84,90` hardcode a `%USERPROFILE%`-rooted path
  (the observed shim path is written in its `%APPDATA%` / `%USERPROFILE%` env-var form below), and
  `configuration/omniroute/vertex-trailing-turn-README.md:56` still does. AGENTS.md hard rule 1
  and the §7 DoD ban this in tracked files. **Confirmed by me** with a `git grep` for a
  `C:\Users\<name>` literal at `6f93452` — the hits are exactly as listed. The `.ps1` itself is
  clean; the old username-rooted `$Path` default at `6f93452^:21` really is gone.
- **Two docs disagree on which artifact produced the idempotency proof** —
  `docs/handoff/2026-09-30-laneF1-vertex.md:70-73` attributes `Done: 13 patched` to
  `tools/apply-vertex-patch.py`, but that script's `patches` list has exactly **12** entries
  (measured: `Select-String '^\s*\("_' | Measure-Object` → `12`), so it can never print 13.
  **Confirmed by me.**
- **`tools/apply-vertex-patch.py:87` takes no backup at all** (`open(fpath,"w").write(content)`),
  counts a missing chunk as `skipped` rather than `errors` and therefore exits 0 on a partial
  patch. The rewrite re-endorses it as "the equivalent chunk-only patcher" without saying it is
  anchor-equivalent but **not** safety-equivalent. **Confirmed by me**: line 103 is the bare
  `open(fpath,"w",...)` write, line 111 is `return 0 if errors == 0 else 1`, and missing files
  only increment `skipped`.
- **`vertex-trailing-turn-reapply.ps1:54-58`** — the guard tests only that `package.json` exists,
  then reports "omniroute package not found at: $Path"; no name/version is asserted, so a `-Path`
  aimed at any other package is accepted.
- **`vertex-trailing-turn-reapply.ps1:44,118,132`** — the README claims "Windows 10/11 **or
  Linux/macOS** with PowerShell 7.4+", but the default is `Join-Path $env:APPDATA …` and the
  chunk paths are backslash-built; on Linux/macOS `$env:APPDATA` is unset and the script's own
  `#Requires -Version 5.1` contradicts "7.4 or later". The documented non-Windows support does
  not exist.

**What seat C verified as correct** (recorded because a review's negatives matter as much as its
positives): `Backup-File` runs *before* the single write site and a failing `Copy-Item` is
terminating under `$ErrorActionPreference='Stop'`, so a write cannot proceed unbacked; `-NoBackup`
is a `[switch]` defaulting off and is documented as still writing; an ERROR on one entry does not
abort the others; the tail `exit 1`s whenever `$errors -gt 0`, so a partial patch does not exit 0;
a second run yields 13 SKIPs and exit 0; `List[string].Add` returns void so nothing leaks to
stdout; the only write site uses `$utf8NoBom`; `[Parser]::ParseFile` is clean; the `??`
occurrences are inside single-quoted anchor strings, so there is no 7.x-only syntax; and script,
README and both handoff docs **agree** on the six chunk names and the f/o vs m/s var mapping.

### Integrity note carried forward

`6f93452`'s own evidence doc records that **three** review attempts were made on this lane and
attempts 1 and 2 returned outputs that could not be genuine (one cited a file that does not
exist; one claimed runs in a directory that did not exist yet). Only a nonce-gated attempt 3 was
counted, and its anchor count was then contradicted by the lane's own measurement. This round
commissioned a fresh reviewer with no prior context, and that reviewer independently reached FAIL
on a defect the lane's counted review did not raise. That is the second independent signal that
this lane's review evidence is weak.

---

## Target 3 — REVIEWGATE-2FAM (`d0f70f1`)

**Reviewer:** seat B, session `ses_f09b0dbdcffew7KXPz6Vyw3Bq8`, `opencode/longcat-2.5-preview-free`
(family **meituan**).

**Verdict (verbatim):** `VERDICT: PASS` — `FINDINGS: (none — every bypass vector I could construct
holds; see evidence)`

The commissioned question was whether the two-cross-family-seat gate **can** be satisfied by
same-family seats. Seat B answered no, and did not stop at reading: it extracted the committed
tree with `git archive d0f70f1` to a temp dir and pushed **25 crafted records** through
`review_status()`. All refused as they should: same-family via policy/model-id/claude-vendor-id
spellings, the same reviewer twice, `author` == reviewer family (including `author=Meta`), unknown
reviewer, two unknown reviewers, `family=` mismatch/blank/capitalised/model-id, `verdict=FIX-FIRST`,
a second seat with unknown author, and Sonnet-as-reviewer. Accepted: two distinct families,
`family=Meta`, `family=GOOGLE`, three seats. Its summary line: `ALL HOLD`.

**The specific bypass I had it test** — whether `reviewer_family` returns early on a `None` from
`_family_of_one_spelling` (which returns bare `resolver.family_key(entry.get("family"))`, i.e.
`None` for a familyless registry row) and thereby lets a second spelling slip through as a
different family. **I verified the code path myself** at `d0f70f1:tools/autoos-agent.py:2740-2749`:
`_family_of_one_spelling` returning `None` falls through to the `_CLAUDE_MODEL_ID_RE` retry at
:2746 and only then returns `None` at :2749. There is no early return. Seat B is right.

**I also verified the test it cites exists and is load-bearing** —
`d0f70f1:tests/test_autoos_spawner.py:9123`
`test_two_seats_from_the_same_family_are_one_review_not_two` asserts
`assertFalse(report["ready"])` and the detail strings `"reviewers omniroute/muse and muse-contrib are
the same family (meta)"` and `"2 cross-family seats required; have 1"`. Remove the dedupe and that
test fails. The suite also pins the capitalisation, unknown-reviewer, unknown-author,
declared-family-mismatch and single-seat cases (`:9113`, `:9135`, `:9222`, `:9254`, `:9233`).
Seat B ran `ReviewStatusTests`+`ReadyCommandTests` on the extracted tree: `Ran 63 tests … OK`,
matching the commit message.

**Residual gap, my own (not raised by the reviewer, and not a defect):** the optional `family=`
cross-check is skipped entirely when `family=` is absent. That is safe today because the seat
family is derived from the registry rather than trusted from the record — absence skips a
redundant check, not the real one. Worth stating because "optional cross-check" reads like a
weaker gate than it is.

---

## Degenerate attempts

**None in this round.** All three reviewers returned substantive, target-specific reviews with
`file:line` findings and named commands; none was a repetition loop, a stray tool call or empty
output. No retry was needed, and no reviewer was retried on a different family because a first
attempt degenerated.

## What I could not obtain or verify

- **The gate cannot name `space-bunny-free`.** Measured: `reviewer_family("opencode/space-bunny-free",
  catalog/ai-registry.json)` → `None`; no `models` row, no `policy.reviewers` row. Seat C is a
  legitimate non-deepseek reviewer, but a `d0f70f1` record citing it would be **refused**. The
  registry has no row for it, so under the new gate it is not a countable seat. Recorded, not fixed
  (registry change, out of scope for a review round).
- **Only ONE new non-deepseek family per target.** The brief asked for 2–3 NEW reviews and 2 seats
  per target where cheap; I obtained 3 reviews across 3 targets, one fresh non-deepseek family each
  (nvidia, meituan, space-bunny), rather than 2 families on every target. Rationale: the
  cross-family rule's floor is *two families per change*, and on admission (target 1) two non-deepseek
  families already existed from `c3e139c`. On patch-fix and reviewgate I did not reach a second
  fresh family. **Not obtained: a second fresh non-deepseek family for target 2 and for target 3.**
- **`omniroute/t3-driver-clean` was not used**, per the brief and L0's correction — it is deepseek,
  the writers' family. No deepseek review appears anywhere in this round.
- **The patch-fix target is not re-reviewable end-to-end here.** Its evidence depends on a pristine
  omniroute 3.8.50 tarball and live npm install under the operator's home directory, which are
  outside this worktree. Seat C reviewed the committed script and docs only; the idempotency and
  SHA256 claims in `6f93452` were **not** independently re-run by me.
- **Line numbers in seat A's findings drift from my greps** for the three unfixed same-class sites
  (`:242`/`:110` vs my `:263`/`:129`, plus `start-litellm.ps1:91` which matches). The files and the
  defect class are correct; the individual line numbers should be re-derived before any fix commit
  cites them.

## Appendix — families used, and the `policy.reviewers` models that back them

Registry-derived (`tools/autoos_resolver.py:family_key` is strip+casefold, so every comparison is
case-insensitive):

| Model id | Family | Source |
|---|---|---|
| `opencode/nemotron-3-ultra-free` | nvidia | `policy.reviewers` + `models` |
| `opencode/longcat-2.5-preview-free` | meituan | `policy.reviewers` + `models` |
| `opencode/space-bunny-free` | **unresolvable (`None`)** | not in `models` or `policy.reviewers` |

The 11 `policy.reviewers` model spellings: `omniroute/spark-1.3-contributor` (meta),
`gemini-3.8-flash` (google), `opencode/deepseek-v4.1-flash` (**deepseek — excluded**),
`opencode/muse-spark-1.3-contributor-free` (meta), `qwen3.8-flash` (qwen),
`opencode/longcat-2.5-preview-free` (meituan), `opencode/nemotron-3-ultra-free` (nvidia),
`opencode/mimo-v2.6-flash-free` (xiaomi), `omniroute/t2-worker` (zhipu),
`omniroute/t3-driver` (qwen), `haiku` (anthropic).

Unused-but-available non-deepseek free families for a future round: `mimo-v2.6-flash-free` (xiaomi),
`muse-spark-1.3-contributor-free` (meta).
