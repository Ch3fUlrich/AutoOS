# DONE — ws-applyjson-20260930 (lane ApplyJson, L2 fix worker under L0)

Verdict: **DONE**. Pre-existing launcher robustness bug fixed red-first,
case-sensitive duplicate-tolerant parse working on pwsh 7 and Windows PowerShell
5.1; full suite shows zero new failures; dry run reaches `Gateway OK` with no
casing error; cross-family review PASS; no gateway restart.

## Finding closed

`configuration/omniroute/apply.ps1` parsed `catalog/ai-registry.json` with
`ConvertFrom-Json`, which is case-**insensitive** on object keys and throws
`DuplicateKeysInJsonString` (5.1) / "keys with different casing" (pwsh 7) when the
registry carries two keys differing only by case. The throw is inside
`Get-AutoOSProviderMap`, so the run's provider registration was silently skipped
while connections and combos still applied (fail-open). Pre-existing at
`c3e139c`; latent today (live registry collision count = 0).

## Commits (branch `L1-backlog/ws-applyjson-20260930`, no push/merge/rebase)

- `f3c8b0e9bc56f8adb659cd4cbc4cf3849f88ebdb` — fix(omniroute): parse the registry
  case-sensitively so a case-only key pair cannot skip provider registration.
  `configuration/omniroute/apply.ps1` +65/-2, `tests/run-tests.ps1` +72/-3.
- Docs commit (this DONE note + `docs/handoff/2026-10-01-laneApplyJson.md`) follows.

Base sha: `c3e139ca98607c2cfe04d7e207956c73959c066b` (the admission-fix tip).

## Verification numbers (quoted in `docs/handoff/2026-10-01-laneApplyJson.md`)

- Live collision count (raw-text case-insensitive scan): **0** (latent).
- Failing-first (`-Filter 'tolerates registry keys'`): pre-fix `passed 1 failed 1`
  on both shells, both with the duplicate-key error; post-fix `passed 5 failed 0`.
- Focused provider/apply filter: `passed 59 failed 0 skipped 0` on both shells.
- Full suite: pwsh `1762 passed / 5 failed / 13 skipped`; 5.1 `1757 / 6 / 14`.
  All failures reproduced at base `c3e139c` → **zero new failures**.
- Parse errors = 0 on both touched `.ps1`, both shells.
- ScriptAnalyzer parity: apply.ps1 1/1, run-tests.ps1 17/17, no new rule.
- `python tools/registry.py validate` → `ok: registry 2026-09-28, 25 routes,
  71 models, 33 providers`.
- `apply.ps1 -DryRun` (live gateway, not restarted): `Gateway OK on
  http://127.0.0.1:20128`, full plan, no `DuplicateKeysInJsonString`/casing error,
  in both shells. Pre-fix sandbox with a colliding registry showed the exact
  `DuplicateKeysInJsonString` error and an empty `Providers:` section.

## Review (nonce-gated, different family)

- Reviewer route/model: `omniroute/t3-driver-clean` (different family from the
  writer's `deepseek-v4.1-flash`), subagent session `ses_f0a3654beffempX6cVdVHWfRkp`.
- Nonce: `ws-applyjson-20261001-nonce-K7Q9Z2`.
- Nonce returned yes. Verdict: **PASS**. One finding, on the evidence doc (an
  absolute home path), fixed before the docs commit; no code defect.

## Admission protocol

- `chat_admission_busy` / `Rate limit exceeded` observed: 0. Backoffs: 0.
  Refusals: none. Lane death: never. No gateway restart.

## Remains (for L0)

- Merge decision for this branch. It is a small, self-contained fix on top of
  `c3e139c`; no other lane's files touched.
- Not changed (reported): the same case-insensitive `ConvertFrom-Json` hazard
  exists in `lib/windows/AutoOS.Install.psm1` `ConvertFrom-AutoOSJsonc` (JSONC
  parser) and at apply.ps1's CLI-output parse sites; those inputs are not the
  registry and are out of this finding's scope.

## Redaction

- No secret, username or user-home path in any file this lane introduced. The
  only resolved path shown (`%APPDATA%\npm`) is in env-var form.
- DONE note added with `git add -f` (`logs/` is git-ignored).
