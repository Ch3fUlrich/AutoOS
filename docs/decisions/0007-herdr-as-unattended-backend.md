# 0007. Herdr Multiplexer as an Optional Remote-Supervision Backend for Unattended Orchestration

- **Status:** Accepted (2026-09-09)

## Context

With the upgrade of `skills/unattended-orchestration` to support heterogeneous AI agent CLIs (`claude`, `agy`, `grok`, `codewhale`), an atomic event ledger (`progress.jsonl`), and a 3-level subagent delegation hierarchy, we evaluated whether `skills/herdr-orchestration` could serve as the runtime management layer for background sessions, agent spawning, and session resumption.

`skills/herdr-orchestration` (adopted in [ADR 0002](0002-herdr-multiplexer.md)) provides a socket API to a persistent terminal multiplexer designed for:
1. Long-lived terminal panes that outlive individual SSH sessions and agent exits;
2. Remote human observation and interactive takeover (`agent attach --takeover`);
3. Driving diverse non-Claude CLIs inside managed panes.

However, unattended orchestration has distinct requirements that conflict with a pure terminal multiplexer:

| Requirement | Unattended Runner (`run_handoff_sessions.ps1`) | Herdr Multiplexer (`herdr-orchestration`) |
|---|---|---|
| **Human Presence** | **None** (runs overnight/headless for hours) | **Active** (human supervising or observing) |
| **Output Model** | Structured JSON / NDJSON streams & exit codes | Scraped terminal text (`herdr agent read`) |
| **Usage-Limit Recovery** | **Automatic** backoff & timestamped resumption | None (pane idles or errors out) |
| **Crash Recovery** | **Automatic startup reconciliation** via `progress.jsonl` | Pane persists, but session recovery is manual |
| **Merge & Isolation** | **Worktree isolation + guard commands + merge mutex** | Pane isolation only (no git merge / guard lifecycle) |
| **Memory Integration** | Pinned Omnigraph (`OMNIGRAPH_GRAPH_ID`) & Graphify links | Unaware of structured memory topology |
| **Environment Preconditions** | Standard shell (`pwsh`), Git, and agent CLI | Requires running Herdr daemon + `HERDR_ENV=1` |

## Decision

1. **Do NOT replace the unattended runner with Herdr.**
   The unattended runner's core responsibilities — automatic usage-limit backoff, crash reconciliation via `Reconcile-HandoffLedger`, guard command verification, atomic branch merging, artifact archival, and cleanup — cannot be delegated to a terminal multiplexer without loss of reliability and testability.

2. **Position Herdr as an optional remote-supervision transport, not the orchestrator.**
   Herdr is complementary to unattended orchestration:
   - When running on a remote homelab server or headless box with Herdr active (`HERDR_ENV=1`), `run_handoff_sessions.ps1` can optionally spawn worker sessions inside Herdr panes (`herdr agent start`).
   - This provides the human operator with live mobile/SSH visibility and takeover capability without compromising the runner's atomic ledger, guard gating, and automated merge loop.

3. **Keep unattended orchestration fully autonomous and self-contained by default.**
   The runner must never require a Herdr socket or daemon for standard headless runs. Default execution uses native backgrounding (`claude --bg`) or runner-managed child processes (`Start-Process` for `agy`, `grok`, `codewhale`).

## Consequences

- **Positive**: Zero external daemon dependency for unattended overnight runs on standard workstations.
- **Positive**: Complete crash resilience and cold-recovery semantics preserved via `progress.jsonl` and `state.json`.
- **Positive**: Clean architectural separation: the runner owns lifecycle, guards, and git state; Herdr owns terminal presentation and remote interactive takeover when desired.
- **Negative**: When running without Herdr, live interactive takeover is limited to CLI-native attach commands (e.g. `claude attach` or `agy --conversation`).
