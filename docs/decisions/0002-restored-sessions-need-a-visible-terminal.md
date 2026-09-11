# 0002 — A restored session must land in a terminal a human can see

Status: accepted · 2026-09-11

## Context

Claude Code is an interactive TUI. It needs a terminal to draw into and a keyboard
to answer from. The first implementation ignored this on both platforms:

- **Linux.** When herdr was not reachable it fell back to
  `(cd "$cwd" && nohup $cmd >/dev/null 2>&1 &)` — no TTY, stdout to `/dev/null`.
- **Windows.** A Scheduled Task ran `powershell -WindowStyle Hidden` which called
  `Start-Process claude.cmd`, producing a console the user can never see or type
  into. If Claude asks anything at startup, that process waits forever, invisibly.

The upstream `herdr-sessions` implementation solved this by making herdr panes the
only target and refusing outright otherwise:

> `[ -n "$pane" ] || hs_die "herdr session has no panes; cannot start an agent"`

That refusal is the part the port dropped, and it is the important part.

## Decision

**Restore only into a terminal host that a human can attach to. If none is
available, skip the session and say why.**

| Platform | Preference order |
|---|---|
| Linux / macOS | herdr pane → `tmux` session → **refuse** |
| Windows | Windows Terminal tab (`wt.exe`) → **refuse** |

"Refuse" means the run reports `skipped` with the reason on the line — never
`installed`, never a silently orphaned process.

The Windows Scheduled Task keeps `-WindowStyle Hidden` for the *launcher*, because
the launcher is a script with no UI; the `wt.exe` window it starts is visible. The
snapshot task stays fully hidden — it has nothing to show.

## Consequences

- `claude-autostart` is only useful on a machine that has herdr, tmux, or Windows
  Terminal. That is a real restriction, and it is now stated at install time
  instead of being discovered as an invisible no-op after a reboot.
- The Linux unit no longer pretends it can restore without a pane owner. Where
  herdr is the chosen host, `herdr-server.service` must own it — a herdr server
  started from inside a Claude session inherits `CLAUDE_CODE_*` and silently
  disables transcript saving, which breaks resume entirely. See the comments in
  the upstream unit file; we order against it rather than re-derive it.
- Restoring several sessions takes time (each terminal starts, each session
  loads). The restore unit therefore sets `TimeoutStartSec=900` instead of
  inheriting the 90 s default that would kill it half-done.
