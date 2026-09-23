# Remote Access & Multi-Agent Operation

Running agents you can walk away from and check on remotely.

| Need | Use | Why |
|---|---|---|
| Run/persist **multiple agents**, reattach over SSH (incl. phone), agents coordinate | **[Herdr](herdr/)** | Purpose-built agent multiplexer; sessions survive detach/restart; socket API for agent-to-agent workflows. Supersedes raw tmux here. |
| Reach a **GUI IDE** session from a phone | the IDE's own remote feature | Antigravity ships this natively now — see the note below. |
| Bare terminal persistence, no agent-awareness | tmux/screen | Fine, but Herdr gives per-agent status and the socket API for free. |

## Herdr vs tmux (why the switch)

tmux persists terminals but treats every pane as opaque text. Herdr understands
that a pane is an **agent** — surfacing blocked/working/done state, and exposing a
socket API so agents can spawn panes and wait on each other. For a
multiple-coding-agents workflow that is the difference between "a wall of
terminals" and "an orchestration surface". Keyboard bindings are tmux-style
(`ctrl+b` prefix), so muscle memory carries over.

## Removed: `antigravity-remote-ui` (2026-08-25)

This directory used to hold a workaround that captured the Antigravity IDE's chat
DOM over CDP and re-served it as a mobile web UI behind a tunnel. **Antigravity now
supports remote connections directly in the app**, so the scrape-and-tunnel hack is
obsolete and was deleted rather than left to rot — it was fragile by construction
(any IDE DOM change broke it) and carried its own Docker image and tunnel to
maintain.

Use the app's built-in remote connection instead. Nothing else in this repo
depended on it; Antigravity's **MCP wiring is unaffected** and still lives in
[`../mcp-servers/config/mcp_antigravity.json`](../mcp-servers/config/mcp_antigravity.json)
(see [`../../GEMINI.md`](../../GEMINI.md)).
