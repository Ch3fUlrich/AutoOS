# 0001 — Discover Claude sessions from transcripts, not process command lines

Status: accepted · 2026-09-11

## Context

The first implementation of `claude-autostart` discovered sessions by scanning the
process table and parsing `--resume <uuid>` / `-n <name>` out of each command line.
It does not work, on either platform, and the failure is silent:

- **Windows.** `Win32_Process` exposes no working directory. The code could only
  learn a session's `cwd` by looking it up in existing state *keyed by a UUID it
  did not have yet*, so the UUID lookup never ran. Measured on a developer machine
  with six live `claude.exe` processes, `Get-AutoOSClaudeSessions` returned **0**.
- **Linux.** `pgrep -x claude` matches only a process whose `comm` is exactly
  `claude`. An npm-installed Claude Code is hosted by `node`, so the scan returns
  nothing there too.
- **Both.** A command line only carries a UUID for a session that was *itself*
  started with `--resume`. A session started normally has no UUID to find.

A second discovery path existed — take the newest `*.jsonl` in the project's
transcript directory — which the upstream `herdr-sessions` implementation had
already rejected, correctly: an mtime proves a session existed, not that it was
running, and one directory routinely holds several sessions.

## Decision

**`~/.claude/projects/*/*.jsonl` is the discovery source.** One file *is* one
session. The first record in each file carries the fields we need, verbatim:

```json
{"sessionId": "57dac455-…", "cwd": "C:\\Users\\…\\AutoOS", "gitBranch": "main", …}
```

So `cwd` is read, never reconstructed — the directory-name slug
(`C--Users-<you>-Documents-Code-AutoOS`) is lossy and is never reversed.

A session is recorded when **both** hold:

1. its transcript was modified within `liveness_window_mins` (default 240), and
2. at least one Claude Code process is alive at snapshot time.

Condition 2 is what stops a timer that fires mid-boot from recording yesterday's
sessions. Condition 1 bounds how far back "before the shutdown" reaches. The list
is capped at `max_sessions` (default 8), newest first.

## Consequences

- Several sessions in one working directory are distinguished correctly — the case
  the mtime heuristic got wrong is the case this gets right, because each session
  already has its own file.
- Identical inputs on both platforms, so the PowerShell and Python implementations
  can be held to the same fixtures. `tests/fixtures/claude-projects/` is shared.
- **Accepted tradeoff:** mtime is *recent activity*, not liveness. A session idle
  longer than the window is not restored, and a session that exited within the
  window may be restored. Both are bounded and configurable; neither can lose
  conversation content, which lives in the transcript either way.
- We no longer need the process table for identity — only for the liveness gate in
  condition 2, where a plain "is anything called claude running" is sufficient and
  is the one thing both platforms can answer reliably.
