# DONE — ws-review-20260930 (Wave 1)

**Date:** 2026-09-30
**Lane:** L1 review (t2 smart-reasoning-128k orchestrator)
**Base:** d08f7f2 (main == origin/main at branch cut)
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-review`
**Branch:** `L1-backlog/ws-review-20260930`

---

## Deliverable

`docs/handoff/2026-09-30-laneReview-wave1.md` — independent cross-family review
of two completed milestones by 2 reviewer leaves from different model families
each, run sequentially.

## Milestones reviewed

| Milestone | Writer lane | Commit | Reviewers | Verdict |
|---|---|---|---|---|
| Combos | L1-alpha | a5bcb69 | DeepSeek + Vertex/Gemini | APPROVE / APPROVE |
| P0 / Admission | L1-beta | d88ed3b | DeepSeek + Vertex/Gemini | REJECT / REJECT |

## Reviewer families

| Family | Model ID |
|---|---|
| DeepSeek | `omniroute/deepseek-v4.1-flash` |
| Vertex / Gemini | `omniroute/vertex-3.8-flash` |

Both families were pre-verified working before reviewer spawns. Reviewers were
run sequentially (one at a time) for rate-limit hygiene.

## Key findings

### Combos (a5bcb69) — APPROVED by both reviewers

- All 6 invariants passed (vertex leg-2 assignment, 5-combo scope, no
  free-only pollution, registry untouched, JSON valid, apply.* comment-only).
- Gateway catalog match confirmed live: gemini-3.8-flash=1048576,
  vertex/gemini-3.8-flash=1048576, deepseek/deepseek-flash=1048576.
- 5 `test_registry_render.py` failures are an expected cross-lane dependency
  on L1-beta updating `catalog/ai-registry.json` (not a combos defect).
- No test files changed in the commit (diff vs d08f7f2 empty for tests/).
- Risk: `antigravity/claude-opus-4-6-thinking` not in live /v1/models (0
  antigravity/* models); combo store uses computed_context_length=1048576.

### P0 (d88ed3b) — REJECTED by both reviewers

- All code invariants passed (idempotency, read-modify-write, env-relative
  paths in scripts, knob ordering, shim .cmd resolution, loud failure on
  missing shim, documented deferral of apply.ps1 fix).
- **Blocking defect:** `docs/handoff/2026-09-30-laneP0-admission.md:4`
  contains hardcoded username path
  `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute` — violates
  AGENTS.md Hard Rule 1. Both reviewers independently identified the same
  file:line. The defect is in a doc file, not in the launcher scripts (scripts
  correctly use `$env:APPDATA`).
- P0 branch tests: 160 passed, 0 failed.

## Admission / rate-limit log

| Event | Count |
|---|---|
| `chat_admission_busy` | 0 |
| `Rate limit exceeded` | 0 |
| Refusals | 0 |

No admission or rate-limit errors were encountered during any reviewer spawn.
No refusals were issued by any reviewer.

## Recommended follow-ups (for a follow-up lane — not fixed here)

1. **P0 blocking (High):** Redact hardcoded username `<user>` from
   `docs/handoff/2026-09-30-laneP0-admission.md:4`. Replace with
   environment-relative path. L1-beta lane fix.
2. **P0 deferred shim (Medium):** Mirror `start-stack.ps1` shim fix to
   `apply.ps1:96-97` (still uses bare `omniroute`).
3. **Combos test dep (Expected):** 5 test failures resolve once L1-beta
   updates `catalog/ai-registry.json` `context_advertised` values.
4. **Combos unverifiable model (Low):** `antigravity/claude-opus-4-6-thinking`
   absent from live gateway; monitor for retirement/rename.

## Files committed

- `docs/handoff/2026-09-30-laneReview-wave1.md` — full review findings
- `logs/handoff-sessions/DONE-ws-review-wave1.md` — this file

## Constraints honored

- Review worktree cut from main d08f7f2 — no push, no merge, no rebase,
  no checkout of other lanes' branches.
- Other lanes' commits read via `git show <sha>` only.
- No fixes applied — all defects recorded precisely for a follow-up lane.
- Gateway probe script read the omniroute client key from
  `configuration/api-keys.yml` without ever printing the key value.

## Redaction summary (commit `4ae1f7c`)

- **Redaction diff summary:**
  - 7 changed lines (file 1: 4, file 2: 3), and a programmatic check confirmed every removed line equals the added line after real-username→`<user>` substitution — `ALL PURE SUBSTITUTION: True`, `placeholder tokens added: 7`.
  - **No username:** case-insensitive `git grep -ni` for the real username returns nothing in both owned files at `4ae1f7c`.
  - **Structure preserved:** file:line refs (`laneP0-admission.md:4`), all `APPROVE`/`REJECT` verdicts, confidence levels, `P0-R1-FAIL`/`P0-R2-FAIL` labels, and the reviewer table are intact.
  - **Commit boundary:** `git show --stat 4ae1f7c` shows only the two owned files changed.
  - **Out-of-scope file:** `git log 4ae1f7c -- ...workstation-omniroute-handoff.md` shows only base commit `d08f7f2`, not branch-authored; correctly excluded.

- **Cross-family redaction re-review (t3-reviewer, DeepSeek family, `omniroute/deepseek-v4.1-flash`)**
  - **Verdict: APPROVE**
  - **Confidence: HIGH**
  - **Findings:** no issues. (Minor context, not a defect: at `docs/handoff/2026-09-30-laneReview-wave1.md:145` and `:209` the prose now reads "the username `<user>`" / "username `<user>`", which is slightly awkward but still clearly conveys the finding — a real system username was embedded in a tracked file, violating Hard Rule 1. This is an unavoidable consequence of redaction and does not alter any verdict, finding, reference, or structure.)
  - **Evidence:**
    - Scope / no-residual grep (empty output = no matches; search term redacted to `<user>` in this record):
      ```
      $ git grep -n -I <user> 4ae1f7c -- docs/handoff/2026-09-30-laneReview-wave1.md logs/handoff-sessions/DONE-ws-review-wave1.md
      (no output)
      $ git grep -ni -I -e <user> -e "C:\Users\" 4ae1f7c -- <both files>
      (no output)
      ```
    - Pure-substitution proof:
      ```
      removed: 7 added: 7
      ALL PURE SUBSTITUTION: True
      placeholder tokens added: 7
      ```
    - Commit boundary:
      ```
      4ae1f7c redact(review): scrub username from wave-1 review doc and DONE note (Hard Rule 1)
       docs/handoff/2026-09-30-laneReview-wave1.md   | 8 ++++----
       logs/handoff-sessions/DONE-ws-review-wave1.md | 6 +++---
       2 files changed, 7 insertions(+), 7 deletions(-)
      ```
    - Representative redacted lines (after state; the `-` before-lines originally embedded the real system username, redacted here to `<user>` so both sides read identically — the live proof of "pure substitution" is the Pure-substitution block above, not a token-bearing diff):
      ```
      `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute`
      ...not in a comment, not in an example"). The username `<user>` is
      **Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-review`
      ```
    - Traceability/verdicts preserved:
      ```
      4ae1f7c:docs/.../2026-09-30-laneReview-wave1.md:137:- **P0-R1-FAIL** (blocking): `docs/handoff/2026-09-30-laneP0-admission.md:4`
      4ae1f7c:docs/.../2026-09-30-laneReview-wave1.md:44:**Verdict:** **APPROVE**
      4ae1f7c:docs/.../2026-09-30-laneReview-wave1.md:124:**Verdict:** **REJECT**
      4ae1f7c:logs/.../DONE-ws-review-wave1.md:53:- **Blocking defect:** `docs/handoff/2026-09-30-laneP0-admission.md:4`
      ```
    - Out-of-scope file (context only, does not affect verdict):
      ```
      $ git log 4ae1f7c --oneline -- docs/handoff/2026-09-30-workstation-omniroute-handoff.md
      d08f7f2 fix(router): workstation combos 1M + AGYCANON + apply automation (operator 2026-09-30)
      ```

- **Cross-branch occurrences (read-only, not fixed here)**
  - `docs/handoff/2026-09-30-laneC-reasoning-fix.md` on `L1-backlog/ws-fixes-20260930` (lines 5, 70):
    - `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-fixes` (worktree path)
    - `C:\Users\<user>\AppData\Roaming\opencode\config.json` (opencode config path)
  - `docs/handoff/2026-09-30-laneP0-admission.md` on `L1-backlog/ws-p0-admission-fix-20260930` (line 4):
    - Already fixed (env-relative `%APPDATA%` path)
  - `docs/handoff/2026-09-30-workstation-omniroute-handoff.md` on `origin/main` (line 19):
    - Already redacted in commit `1ddcff1` (read-only, not owned by this branch)

## Wave-2 redaction (this lane, `review-hygiene-2`)

**Defect found:** the previous lane left an *uncommitted* working-tree revision of this
DONE note that re-introduced the real system username (in transcript-style grep
commands and full `C:\Users\<user>` paths). The previous lane had verified only the
*committed* tree (HEAD `4ae1f7c`), which was clean — but the working-tree edit was not.
Hard Rule 1 forbids usernames in tracked files (not in a comment, not in an example), so
the leaked token had to go regardless of commit state.

**Action taken:** the draft added real verification value (redaction proof, cross-family
re-review, pure-substitution proof, commit boundary, cross-branch occurrences), so it was
kept and redacted rather than discarded. The draft had also *replaced* the entire 96-line
review note (its content was line-numbered `97:`–`167:`, i.e. intended as an appendix, not
a replacement) and embedded spurious `NN:` line-number prefixes as literal text on every
line; both mechanical defects were repaired: the clean HEAD note was restored and the
redaction summary re-appended with prefixes stripped and every username token /
`C:\Users\<user>` path redacted to `<user>`. No banned token is quoted in the new text.

**Diff summary:** 1 file changed, `logs/handoff-sessions/DONE-ws-review-wave1.md`
(restored 96-line HEAD note + appended redacted "Redaction summary" section + this
wave-2 record). The out-of-scope file
`docs/handoff/2026-09-30-workstation-omniroute-handoff.md` (pre-existing on main,
line 19) was *not* edited.

### Lesson

A redaction is proven with a **working-tree** grep plus a clean `git status`, not by
inspecting the committed tree alone. The committed tree can be clean while an
uncommitted working-tree edit re-leaks the token — exactly what happened here. Always
grep the working tree (`git grep -n -I` with no treeish) and require
`git status --short --branch` to show a clean tree before declaring the redaction done.

### Verification (verbatim)

(a) Clean tree:

```
$ git status --short --branch
## L1-backlog/ws-review-20260930
```

(b) Working-tree username grep — nothing except the named out-of-scope file
(search term and result content redacted in this record; the file:line is named, not edited):

```
$ git grep -n -I <user>
docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19:<content redacted — pre-existing on main, out of scope; named, not edited>
```

(c) Commit boundary — `git show --stat HEAD` shows exactly 1 file changed, the owned note
`logs/handoff-sessions/DONE-ws-review-wave1.md`; no doc, no gateway package, no
`configuration/omniroute/combos.json` touched (full verbatim output in the lane report).
