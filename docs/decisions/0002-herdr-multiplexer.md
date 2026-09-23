# 0002. Herdr as the agent multiplexer (over raw tmux)

- **Status:** Accepted (2026-07-09)

## Context

Running and persisting multiple coding agents — and checking on them remotely
(including from a phone) — was previously done with tmux. tmux persists terminals
but treats each pane as opaque text; it has no notion that a pane is an agent and
no built-in way for agents to coordinate.

[Herdr](https://github.com/ogulcancelik/herdr) (14.6k★, v0.7.3, very active) is a
single Rust binary purpose-built as an "agent multiplexer": per-agent status
(blocked/working/done), detach with agents still running, reattach from any
terminal or over SSH, and a socket API for agent-to-agent orchestration.
tmux-style `ctrl+b` keybindings keep muscle memory. Windows support is beta (WSL
also works). License is AGPL-3.0 (+ commercial).

The existing `antigravity-remote-ui` solves a *different* problem — streaming the
Antigravity desktop IDE's chat DOM to a phone browser — which Herdr cannot do.

## Decision

Adopt Herdr as the **recommended** multiplexer for running/persisting multiple
agents, superseding raw tmux in this stack. Document setup for Linux, macOS, and
Windows (beta + WSL) under `infra/remote-access/herdr/`. **Keep**
`antigravity-remote-ui` for the Antigravity-IDE-on-phone case. Do not remove
tmux as an option; just stop recommending it for multi-agent work.

## Consequences

- Better multi-agent ergonomics and a socket API enabling agent orchestration.
- One more tool to install (single binary; low cost). AGPL-3.0 is fine for
  local/self-hosted developer use; review before redistributing a modified build.
- `infra/remote-access/README.md` documents when to use Herdr vs
  antigravity-remote-ui vs tmux so the choice stays clear.
