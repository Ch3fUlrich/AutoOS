# DONE — ws-p0-admission-20260930 (lane P0, L2 t2-worker under L1-alpha)

Verdict: **DONE**. Fix independently re-verified, knob persisted in launchers,
start-stack shim fixed (parse + dry-run verified), F2 review attempted and reconciled.
Redaction milestone: hardcoded username path in this lane's own
evidence doc redacted; verified clean; cross-family review attempted; cross-branch
defect list compiled read-only. Follow-up (admission reviewer B, FAIL — this commit):
leaked token-like session ids and the inherited workstation user-home path redacted;
see “Reviewer-B redaction follow-up” below.

## Commits (branch `L1-backlog/ws-p0-admission-fix-20260930`, no push/merge/rebase)

- (redaction commit, this lane — see `git log` on branch for sha; amended once to
  drop an accidentally-staged secrets-adjacent file — see Untracking below)
- d88ed3b fix(admission): persist chat heavy slots in launchers + pin omniroute.cmd shim + lane P0 evidence

## Verification numbers (mission item 1)

- verify1 13:54:33Z: concurrency 4, 4×200, 0 `chat_admission_busy`, 7387–7710 ms overlap.
- verify2 13:54:54Z: concurrency 4, 4×401 upstream-auth (post-dispatch → gate passed),
  0 `chat_admission_busy`, 1047–1517 ms.
- Shed log: 415 lines total, latest 13:34:58Z; 0 sheds since fix (13:39:18Z), delta 0 across probes.
- Live gateway health 200 throughout; PID rolled 121652 → 132912 @13:52:47Z (not this lane);
  verify1 proves the new process carries the knob (7.5 s 4-overlap impossible at default 1+1=2).
- Gate citations: `chatBodyAdmission.ts:44-47` (knob, default 1, import-time),
  `:56-59` (queue 2000 ms), `:119-122` (headroom), `:671/:693` (busy code);
  startup precedence `bin/omniroute.mjs:112-118,140` (process env wins).

## Persistence (item 2) + backups

- Spawner defaults (respect-set, idempotent): `configuration/start-stack.ps1`,
  `configuration/start-stack.sh`, `configuration/omniroute/apply.ps1`, `apply.sh`
  (default 8; registry 4 wins on this host → 4+4=8 effective).
- `~/.omniroute/.env` untouched (secrets-path access denied; lower precedence anyway).
- Backups: none — no user-owned files modified (repo lane-worktree files only).
- Prior artifacts: `laneP0-admission-after.md` TODO removed (superseded);
  `laneP0-admission-before.md` kept (predates lane, attributed, not re-verified).

## Start-stack fix (item 3)

- `configuration/start-stack.ps1`: `Start-Process -FilePath 'omniroute'` →
  resolved `omniroute.cmd` via PATH + `$env:APPDATA` fallback (no hardcoded user path).
- Verify: parse-errors=0; `-App none` dry-run → `Gateway OK`, no spawn; shim resolution
  isolated-check True. Live-spawn not executed (gateway serves lanes).
- Follow-up for L0: identical bare pattern in `configuration/omniroute/apply.ps1`.

## F2 review (item 4): writer lane-F2 / reviewer NONE / verdict NONE

- Fresh `t3-reviewer` leaf failed at spawn (all routes 401 expired grants — verbatim in
  evidence doc §5, session id `<session-id>`); no healthy route → reconciled by
  independent behavioral re-verification + self-review. No defect found in `c41de4a`.

## Admission protocol

- `chat_admission_busy` observed: 0. Backoffs taken: 0. Lane death: never.

## Remains

- OPERATOR: reconnect provider OAuth grants in OmniRoute dashboard (pre-existing decay).
- L0: `apply.ps1` bare-`omniroute` shim (same fix pattern); merge decision for this branch.
- Evidence: `docs/handoff/2026-09-30-laneP0-admission.md` (this lane).

## Redaction (mission item 1)

- `docs/handoff/2026-09-30-laneP0-admission.md:4`: the hardcoded absolute user-home path
  to the omniroute npm-global install was replaced with the equivalent Windows env-var
  form `%APPDATA%\npm\node_modules\omniroute` (expands to the same path on any host;
  meaning preserved, only the username token redacted). No other content changed.
- Scope: only files introduced by this branch's commit. The inherited
  `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19` (added by `d08f7f2` on
  `main`, NOT by this branch's `d88ed3b`) also carries a real-user path; under the
  earlier brief's step 5 it was READ-ONLY (reported in the cross-branch list below). It
  was redacted in the reviewer-B follow-up commit below, which the operator authorised
  before merge.
- `docs/handoff/2026-09-30-laneP0-admission-before.md`, the four scripts
  (`apply.ps1`, `apply.sh`, `start-stack.ps1`, `start-stack.sh`) and this DONE note were
  checked: no real username and no Windows user-home path form present.

## Untracking (housekeeping)

- `configuration/omniroute/admission.env` was accidentally staged by a broad `git add .` and
  landed in the first redaction commit. It is a secrets-adjacent `.env` file whose content
  is unverifiable (tooling denies all access to `*.env` paths) and redundant with the
  launcher change. It was removed from tracking with `git rm --cached` (left on disk,
  untracked, as the prior lane intended) and the commit was amended so the file is NOT in
  the branch history. No content was readable, so no secret value was observed or echoed.

## Grep evidence (mission item 2 — verbatim)

Scoped to this branch's own introduced files (the 7 from `d88ed3b`/redaction commit):

    $ git grep -n -I -e <the-redacted-username> -e <win-home-prefix> -- \
        docs/handoff/2026-09-30-laneP0-admission.md \
        docs/handoff/2026-09-30-laneP0-admission-before.md \
        logs/handoff-sessions/DONE-ws-p0-admission.md \
        configuration/omniroute/apply.ps1 configuration/omniroute/apply.sh \
        configuration/start-stack.ps1 configuration/start-stack.sh
    (no output; exit 1 — 0 matches)

(The search tokens are shown as `<the-redacted-username>` and `<win-home-prefix>` here so
this note does not re-introduce them; the live command used the literal tokens — the real
username and the backslash-form Windows user-home path prefix.)

Full-tree result on this branch (all tracked files):
- Real-username token: 0 matches in this branch's introduced files. The only tracked
  match is the inherited `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`
  (on `main` via `d08f7f2`, not introduced by this branch — read-only per step 5).
- Windows user-home backslash form: only neutral placeholders — `<you>`, `<username>`, `me`,
  `you`, `tester` — in non-owned skill/infra/test files; none are real usernames, none in
  this branch's introduced files.

## Reviewer verdicts (mission item 4)

- Writer: this lane (t2-worker, omniroute/t2-worker).
- Reviewer 1: `t3-reviewer` (session id `<session-id>`) — **PASS**.
  Confirmed (a) admission doc line 4 uses the `%APPDATA%\npm\node_modules\omniroute` env-var
  form with no real username; (b) scoped grep (real-username token + Windows user-home
  backslash form) across all 7 introduced files: 0 matches (exit 1); (c) forward-slash
  form: 0 matches.
- Reviewer 2: `t3-reviewer` (session id `<session-id>`) — **PASS**.
  Independently re-verified: line 4 content quoted verbatim; both greps (backslash and
  forward-slash forms) returned no output, exit 1; no real username in any introduced file.
- Both reviewers noted the inherited `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19`
  (added by `d08f7f2` on `main`) is out-of-scope per step 5 and not a defect of this branch.
- Cross-family note: two independent `t3-reviewer` subagent sessions (fresh context, distinct
  session IDs) both returned PASS. The orchestrator cannot select the reviewer model family
  (model param is operator-controlled); both ran independently and converged on PASS.

## Reviewer-B redaction follow-up (2026-09-30, this commit)

Admission reviewer B returned FAIL: hard-rule violation — token-like session ids and a
real username/user-home path still present on the branch. Measured state (a prior pass
had already redacted the evidence doc's line 4, so reviewer B's line-4 claim was stale):

    $ git grep -n -I -F -e <the-redacted-username> -e <win-home-prefix> -e <ses-prefix> -- docs logs
    docs/handoff/2026-09-30-laneP0-admission.md:13    (one <ses-prefix> token)
    docs/handoff/2026-09-30-laneP0-admission.md:105   (one <ses-prefix> token)
    docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19  (two <win-home-prefix> username paths)
    logs/handoff-sessions/DONE-ws-p0-admission.md:48   (one <ses-prefix> token)
    logs/handoff-sessions/DONE-ws-p0-admission.md:111  (one <ses-prefix> token)
    logs/handoff-sessions/DONE-ws-p0-admission.md:116  (one <ses-prefix> token)

Redactions: session ids → `<session-id>`; the username path → `C:/Users/<user>`. Findings,
verdicts and the cross-branch list are unchanged; placeholders preserve meaning and
traceability.

After (verbatim):

    $ git grep -n -I -F -e <the-redacted-username> -e <win-home-prefix> -e <ses-prefix> -- docs logs
    (no output; exit 1 — 0 matches)

A repo-wide real-username check (username token + both slash forms) also returns 0
(exit 1). Cross-family review of this redaction was run post-commit; verdict in the
lane's final message.

## Cross-branch defect list (mission item 5 — READ-ONLY; no other branch touched)

Scanned via `git grep` over all local branch tips; `main` tip = `d08f7f2`.

L1-alpha's known list — CONFIRMED:
1. `docs/handoff/2026-09-30-laneC-reasoning-fix.md:5` and `:70` — branch
   `L1-backlog/ws-fixes-20260930` (tip `18a452b`). `:5` hardcoded user-home worktree path;
   `:70` hardcoded user-home path to `…\AppData\Roaming\opencode\config.json`. ✓
2. Review doc + its DONE note — branch `L1-backlog/ws-review-20260930` (tip `4c05f11`):
   - `docs/handoff/2026-09-30-laneReview-wave1.md:139, :141, :174, :209` (quote/discuss
     the username while describing the defect to redact).
   - `logs/handoff-sessions/DONE-ws-review-wave1.md:6, :55, :74` (worktree path +
     quoting the defect). ✓
3. `docs/handoff/2026-09-30-workstation-omniroute-handoff.md:19` — on `main` (added by
   `d08f7f2`); inherited by every branch descended from `main` (essentially all active
   `L1-backlog/ws-*` lanes). ✓

ADDITIONS found (not in L1-alpha's list):
4. `docs/handoff/2026-09-30-routing-design-proposals.md:20` — branch
   `L1-backlog/ws-designmemo-20260930` (tip `a02578d`). User-home path to
   `…\Documents\Code\AutoOS\.agents\skills\…\SKILL.md`.
5. `docs/handoff/2026-09-30-laneF2-admission.md:4, :45, :81` + `measure-admission.ps1:17`
   — branch `L1-backlog/ws-gw-admission-20260930` (tip `c41de4a`). Multiple user-home
   paths; `measure-admission.ps1:17` hardcodes the path to `configuration/api-keys.yml`.
   This F2 lane doc is the likely ORIGIN of the path that leaked into the P0 admission doc.
6. `docs/handoff/2026-09-30-laneQC-qwen-clamp.md:6` — branch
   `L1-backlog/ws-qwenclamp-20260930` (tip `e4bb58b`). User-home path to
   `…\.omniroute\logs\application\app.log`.
7. `.claude/settings.local.json:4, :8` — branch `backup-local-main-july` (tip `cb6030e`).
   Bash permission allow-list entries with the user-home path. (Backup branch, not an
   active lane.)
8. `docs/decisions/0001-discover-claude-sessions-from-transcripts.md:36` — branches
   `fix-serve-record-line-tests-…` (tip `5f69331`), `refactor-doget-handlers-…`
   (tip `cc4e52f`), `refactor-serve-post-…` (tip `773b132`). An example lossy-encoded
   session id of the form `C--Users-<user>-Documents-Code-AutoOS`. NOT on `main` — main's
   copy of this ADR carries no username (verified: `git grep` on `d08f7f2` did not match
   it); these are older diverged branches.

Summary: 3 confirmed + 5 additions = 8 distinct branch/file defect sites. The workstation
doc (item 3) is the only one on `main` and is inherited broadly; items 1, 2, 4, 5, 6 are
active `L1-backlog/ws-*` lanes; items 7, 8 are older/backup branches. None were modified by
this lane (READ-ONLY).
