# DONE — ws-p0-admission-20260930 (lane P0, L2 t2-worker under L1-alpha)

Verdict: **DONE**. Fix independently re-verified, knob persisted in launchers,
start-stack shim fixed (parse + dry-run verified), F2 review attempted and reconciled.

## Commits (branch `L1-backlog/ws-p0-admission-fix-20260930`, no push/merge/rebase)

- (this lane, pending at write time — see `git log` on branch for sha)

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
- Prior artifacts: `admission.env` left untracked + uncommitted (tooling blocks `*.env`
  access — cannot verify or remove; redundant with launcher change);
  `laneP0-admission-after.md` TODO removed (superseded);
  `laneP0-admission-before.md` kept (predates lane, attributed, not re-verified).

## Start-stack fix (item 3)

- `configuration/start-stack.ps1`: `Start-Process -FilePath 'omniroute'` →
  resolved `omniroute.cmd` via PATH + `$env:APPDATA` fallback (no hardcoded user path).
- Verify: parse-errors=0; `-App none` dry-run → `Gateway OK`, no spawn; shim resolution
  isolated-check True. Live-spawn not executed (gateway serves lanes).
- Follow-up for L0: identical bare pattern in `configuration/omniroute/apply.ps1`.

## F2 review (item 4): writer lane-F2 / reviewer NONE / verdict NONE

- Fresh `t3-reviewer` leaf failed at spawn (all routes 401 expired grants — verbatim in
  evidence doc §5, ses_f0d6625d7ffeRY0y2yCJTF1H0W); no healthy route → reconciled by
  independent behavioral re-verification + self-review. No defect found in `c41de4a`.

## Admission protocol

- `chat_admission_busy` observed: 0. Backoffs taken: 0. Lane death: never.

## Remains

- OPERATOR: reconnect provider OAuth grants in OmniRoute dashboard (pre-existing decay).
- L0: `apply.ps1` bare-`omniroute` shim (same fix pattern); merge decision for this branch.
- Evidence: `docs/handoff/2026-09-30-laneP0-admission.md` (this lane).
