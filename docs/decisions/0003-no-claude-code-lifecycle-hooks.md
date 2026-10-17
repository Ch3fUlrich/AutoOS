# 0003 — Do not install Claude Code lifecycle hooks

Status: accepted · 2026-09-11

## Context

The first implementation used a "dual-layer" design: a periodic snapshot *plus*
`SessionStart` / `SessionEnd` hooks written into the user's global
`~/.claude/settings.json`, maintaining a live registry of open sessions.

Each layer failed on its own terms:

- **`SessionEnd` deletes the entry that restore depends on.** The goal is "bring
  back the sessions that were running before the shutdown", but a shutdown ends
  every session. Verified: after `hook-start` the registry held the session; after
  `hook-end` it held `"sessions": []`. The hook layer actively destroys the record
  the feature exists to keep.
- **The Windows hook writer was a no-op.** `claude-sessions.ps1 hook-start` built
  a `$sessions` array locally and then called `Save-AutoOSClaudeSnapshot`, which
  ignores its argument and re-derives from the process table. Feeding it a valid
  payload produced `"sessions": []`.
- **No locking.** Two sessions starting at once both read, both modify, both
  write; one is lost.
- **The hook command hard-codes a checkout path.** `python3 /path/to/repo/lib/
  linux/claude_sessions.py hook-start` runs on *every* Claude Code session start,
  in every project, forever — and breaks silently if AutoOS is ever moved.

## Decision

**Drop the hook layer. AutoOS does not write to `~/.claude/settings.json` at all.**

Discovery is the periodic snapshot alone, reading transcripts (see
[ADR 0001](0001-discover-claude-sessions-from-transcripts.md)).

## Consequences

- AutoOS no longer modifies a user-owned global config file, so the whole class of
  problems around it disappears: no backup file per install run accumulating in
  `~/.claude/`, no merge conflicts with hooks the user configured themselves, no
  dependency on where this repository happens to be checked out.
- The installer becomes genuinely idempotent, because the only things it writes
  are the unit files and the scheduled tasks, both of which it can compare before
  touching (`skipped`, not `installed`).
- **What we give up:** the snapshot interval (default 5 min) is now the only
  bound on losing *membership* — a session opened four minutes before a crash may
  not be in the snapshot. Conversation content is never at risk; it lives in the
  transcript regardless. This is the same tradeoff the upstream implementation
  accepted, and the reason its timer comment insists on wall-clock scheduling.
- Sessions the user deliberately closed *are* still eligible for restore if they
  were active inside the liveness window. This is the honest inverse of the
  `SessionEnd` behaviour and is bounded by `liveness_window_mins`.
