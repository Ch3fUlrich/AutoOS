# DONE — ws-admission-fix-20260930 (lane AdmissionFix, L2 fix worker under L0)

Verdict: **DONE**. Both admission review findings closed; fix independently re-reviewed
cross-family = PASS; failing-first test added; parse/dry-run/analyzer verified; no gateway
restart, no secrets, no hardcoded user paths.

## Findings closed

1. `configuration/omniroute/apply.ps1` bare-shim bug (mirror of P0's start-stack fix) —
   now resolves `omniroute.cmd` via PATH with an `%APPDATA%\npm` fallback and fails loudly
   (`exit 1`) when missing.
2. Missing launcher admission/shim test — added at `tests/run-tests.ps1:9779`.

## Commits (branch `L1-backlog/ws-admission-fix-20260930`, no push/merge/rebase)

- `880ec588eb786e4c8ae7a222446b07de4a850eeb` — fix(admission): pin omniroute.cmd shim in
  gateway launchers + launcher admission/shim test.
  (`configuration/omniroute/apply.ps1` +22/-1, `configuration/start-stack.ps1` +22/-1,
  `tests/run-tests.ps1` +26; one redaction/evidence commit follows this note.)
- Docs commit (this lane's handoff evidence + this DONE note).

Base sha: `6ec0605a188586778acac6829a9263181129e949` (`origin/main` tip at worktree creation;
`origin/main` later advanced to `e58274a` — branch point unchanged).

## Verification numbers (quoted in `docs/handoff/2026-09-30-laneAdmissionFix.md`)

- Failing-first: the new test in a detached `origin/main` worktree → `failed 1`, EXIT=1
  (`configuration\omniroute\apply.ps1 still starts the bare 'omniroute' name`).
- Post-fix: `tests/run-tests.ps1 -Filter shim` → `passed 20 failed 0 skipped 0`, EXIT=0.
- Parse errors = 0 on all three touched .ps1.
- `apply.ps1 -DryRun` → `Gateway OK on http://127.0.0.1:20128`, EXIT=0, no spawn.
- ScriptAnalyzer (repo exclusions): no new findings vs `origin/main` (apply.ps1 pre=1/post=1
  — same pre-existing `KeysMissing`; start-stack pre=0/post=0; run-tests pre=17/post=17).
- Behavioral: admission guard `preset=4 -> 4`, `unset -> 8`, `second run -> 8`;
  missing-shim child run → loud message + `MISS-EXIT=1`.

## Review (item 5) — reviewer → fixer → re-review

- Writer: this lane (`deepseek-v4.1-flash`).
- Reviewer: `t3-reviewer`, session `ses_f0bf9ba21ffecmB0MYrGDZOmto`, configured model
  `omniroute/t3-driver` (`opencode.jsonc:102`) — **different family** from the writer.
- It read `git show 880ec58`, `configuration/omniroute/apply.ps1`,
  `configuration/start-stack.ps1`, the new `Test-Case` in `tests/run-tests.ps1`, and ran
  `tests/run-tests.ps1 -Filter shim` (20 passed).
- Verdict: **PASS** — found no defect; confirmed no hardcoded user path/secrets and the
  respect-set admission default.

## Admission protocol

- `chat_admission_busy` / `Rate limit exceeded` observed: 0. Backoffs taken: 0.
  Lane death: never. No gateway restart.

## Remains (for L0)

- Merge decision for this branch. Note: P0's branch `L1-backlog/ws-p0-admission-fix-20260930`
  is still unmerged; its `start-stack.ps1` shim + all four admission defaults are identical
  in spirit. This lane mirrors the `start-stack.ps1` pattern so the branch is self-contained;
  merging both is expected to be clean (same content) or a trivial conflict.
- Out-of-scope, reported not changed: `configuration/autostart/Start-AutoOSStack.ps1:47`
  (same bare `omniroute` pattern; its failure branch deliberately does not `exit`), and the
  same bare-shim class for `opencode`/`litellm` node CLIs.

## Redaction

- No secret, no real username, no absolute user-home path in any file this lane introduced
  or changed. The one observed shim path is written in its `%APPDATA%\npm\omniroute.cmd`
  env-var form.
- DONE note added with `git add -f` (`logs/` is git-ignored).
