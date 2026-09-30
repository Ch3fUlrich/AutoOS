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
