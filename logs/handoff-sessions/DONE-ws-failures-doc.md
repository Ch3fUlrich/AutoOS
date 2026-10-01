# DONE — ws-failures-doc-20260930

**Date:** 2026-09-30
**Lane:** L2 t2-worker (L1-alpha) — **pinned** `omniroute/deepseek-v4.1-flash`
**Base:** `d08f7f2` (main)
**Worktree:** `AutoOS-ws-failures-doc` (worktrees root) — path kept relative, no username in this note
**Branch:** `L1-backlog/ws-failures-doc-20260930`
**Commit:** this commit — see `git show --stat HEAD`

---

## Deliverable

Appended **one** section to the tracked findings doc
`docs/handoff/2026-09-30-omniroute-gateway-findings.md`:

> `## 8. Lane reliability failures — run ws-omniroute-20260930 (L1-alpha) — **mitigated in-session; spawner restart open — operator decision**`

Content: root cause (source L0), the class-by-class session-id registry, failure
shapes, impact and the hardening now standard. Strictly additive — **+61 lines,
0 deletions** (`git diff --stat` below), existing sections untouched/unnumbered.

## Evidence (command + output)

Source of the counts/narrative: orchestrator state file
`.sessions/ws-omniroute-20260930/L1a-state.md` §"Failure history" + §"Counters"
(counters quoted inline in the section). Session ids are the orchestrator's own
run records.

Fabrication check (worked example, nebius):

```
$ git rev-list --count a975d48..L1-backlog/ws-nebius-20260930
0
```

— the `ws-nebius` branch tip equals the upstream OVH-finish tip (`a975d48`), so
the fabricated nebius lanes landed no commit. The stray OVH-review worktree
reports branch `ws-ovh-finish` at the same tip.

Additive-diff check:

```
$ git diff --stat
 .../2026-09-30-omniroute-gateway-findings.md       | 61 ++++++++++++++++++++++
 1 file changed, 61 insertions(+)
```

## Constraints observed

- **Append only** — no existing section rewritten or renumbered (the `ws-incident`
  lane may co-edit this file).
- No secrets, no usernames, no absolute user paths in the tracked doc.
- Never pushed/merged/rebased/checked out.

## Deviations / notes for L0

1. The assigned worktree `AutoOS-ws-failures-doc` **did not exist** at lane start
   and was absent from `git worktree list`. Created it as lane setup:
   `git worktree add -b L1-backlog/ws-failures-doc-20260930 <worktrees>/AutoOS-ws-failures-doc d08f7f2`
   (new worktree on the assigned branch at the assigned base; no shared worktree
   disturbed).
2. `logs/` is gitignored → this DONE note is force-added (`git add -f`) per brief.

## Verification

`git status --short --branch` is clean after the commit (quoted in the lane return).

---

## Follow-up pass - admission review verdicts (this commit)

**Date:** 2026-09-30
**Base:** `c5279302d9aca3c5ab9de0f10fb4a9d694b446ac` (this branch's tip before the append)
**Commit:** this commit - `docs: record admission review verdicts + reviewer→fixer→re-review pattern`
**Branch:** `L1-backlog/ws-failures-doc-20260930`

### Deliverable

Appended **one** sub-section to section 8 of the same tracked doc -
`### Admission review verdicts — the 2-free-model mandate is complete`:
A `nemotron-3-ultra-free` = PASS; B `longcat-2.5-preview-free` = FAIL and its
findings/fixes (`880ec58`, `1179e3f`, `90cd698`, `1bb1175`); the
reviewer → L2-fixer → re-review pattern; the still-open bare-shim sites
(`Start-AutoOSStack.ps1:129`, `start-stack.ps1:263` opencode;
`configuration/litellm/start-litellm.ps1:91` litellm); and the full-suite summary
hazard (log `%TEMP%\opencode\suite-admission-fix.log`). Strictly additive.

### Evidence (command + output)

Additive-diff proof - `git diff --numstat` before the commit:

```
$ git diff --numstat -- docs/handoff/2026-09-30-omniroute-gateway-findings.md
30	0	docs/handoff/2026-09-30-omniroute-gateway-findings.md
```

Line count before → after: **211 → 241** (+30, 0 deletions; nothing before line
212 touched).

Claim checks (all quoted from `git show`/`Get-Content`, no interpolation):

```
$ git show --stat --oneline 880ec58
880ec58 fix(admission): pin omniroute.cmd shim in gateway launchers + launcher admission/shim test
 configuration/omniroute/apply.ps1 | 23 ++++++++++++++++++++++-
 configuration/start-stack.ps1     | 23 ++++++++++++++++++++++-
 tests/run-tests.ps1               | 26 ++++++++++++++++++++++++++
 3 files changed, 70 insertions(+), 2 deletions(-)

$ git show --stat --oneline 1179e3f
1179e3f fix(admission): pin omniroute.cmd shim in the logon resume helper (reviewer-B finding) + test
 configuration/autostart/Start-AutoOSStack.ps1 | 25 ++++++++++++++++++++++---
 tests/run-tests.ps1                           | 10 +++++++---
 2 files changed, 29 insertions(+), 6 deletions(-)

$ git show L1-backlog/ws-admission-fix-20260930:configuration/autostart/Start-AutoOSStack.ps1 | Select-Object -Skip 128 -First 1
    Start-Process -FilePath 'opencode' -ArgumentList 'serve', '--hostname', '0.0.0.0', '--port', '4096' -WindowStyle Hidden

$ git show L1-backlog/ws-admission-fix-20260930:configuration/start-stack.ps1 | Select-Object -Skip 262 -First 1
            Start-Process -FilePath 'opencode' -ArgumentList 'serve', '--hostname', '0.0.0.0', '--port', '4096' -WindowStyle Hidden

$ (Get-Content configuration/litellm/start-litellm.ps1)[90]
Start-Process -FilePath 'litellm' -ArgumentList '--config', 'config.yaml', '--host', $BindHost, '--port', "$Port" `

$ git ls-files configuration/start-litellm.ps1     # (empty - path in the brief does not exist)
$ git ls-files configuration/litellm/start-litellm.ps1
configuration/litellm/start-litellm.ps1
```

The brief named `configuration/start-litellm.ps1`; the tracked file is
`configuration/litellm/start-litellm.ps1`, so the doc records the real path.

Suite log check:

```
$ Get-Content "$env:TEMP\opencode\suite-admission-fix.log" -Tail 2
  + usb: Invoke-AutoOSUsbFetchImage uses a healthy mirror before the canonical source, verified against the canonical manifest (fetch mirror)
$ Select-String -Path "$env:TEMP\opencode\suite-admission-fix.log" -Pattern 'passed \d+'
# (no match - the full run never printed its summary line)
```

### Re-review (different family, operator directive)

- Reviewer: `t3-reviewer` subagent, configured model `omniroute/vertex-flash`
  (Gemini 3 Flash) - a different family from this lane's writer
  (`omniroute/deepseek-v4.1-flash`).
- It ran the git/`Get-Content` checks itself (quoted above) and confirmed:
  `git diff --numstat` = 30/0 (additive only); the four commits exist with the
  stated shape; the three still-open sites are bare `Start-Process -FilePath
  'opencode'`/`'litellm'` at the recorded lines; and `Select-String -Pattern
  '<user>|C:\Users'` on the doc returned no match.
- **Verdict: PASS** - no defect found.

### Constraints observed

- **Append only** - section 8's existing 211 lines untouched (the `ws-incident`
  lane may co-edit this file); change is +30 insertions, 0 deletions.
- Referenced reviewers by model name only; no real username, no absolute
  user-home path, no secret, no session id in the tracked doc.
- Never pushed/merged/rebased/checked out. `logs/` is git-ignored → DONE note
  force-added (`git add -f`).
