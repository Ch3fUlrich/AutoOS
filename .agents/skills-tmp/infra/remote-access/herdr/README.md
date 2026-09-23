# Herdr — Agent Multiplexer (Linux · macOS · Windows)

[Herdr](https://github.com/ogulcancelik/herdr) is a single-binary terminal
multiplexer purpose-built for running multiple coding agents: see every agent's
real state at a glance (blocked / working / done), detach and let agents keep
running, and reattach from any terminal or over SSH — including from a phone.
Agents themselves can drive it through a socket API (spawn panes, read output,
wait on each other).

This is the **recommended** way to run and persist multiple agents in this stack,
superseding raw tmux. See [`../README.md`](../README.md) for how it compares to
plain tmux.

> License note: Herdr is AGPL-3.0 (plus a commercial option). Fine for local/self
> hosted developer use; review the license before redistributing a modified build.

## Install

**Linux / macOS:**

```bash
curl -fsSL https://herdr.dev/install.sh | sh    # or:
brew install herdr                               # Homebrew
mise use -g herdr                                # mise
```

**Windows (beta):**

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 | iex"
```

Or grab a binary from the [releases page](https://github.com/ogulcancelik/herdr/releases).
On Windows you can also run Herdr inside WSL for a Linux-identical experience;
use the native beta if you want it in a Windows terminal directly.

Verify: `herdr --version`.

## Use

Start it where the work lives (a repo directory):

```bash
cd ~/code/agent-skills
herdr
```

- Split panes and launch an agent in each (one per project/task).
- **Detach:** `ctrl+b q` — agents keep running.
- **Reattach:** run `herdr` again (from any terminal, or after SSH-ing back in).
- Prefix key is tmux-style `ctrl+b`; mouse click/drag/split also work.

Sessions survive restarts, so a long agent run continues while you're away.

### Reattach over SSH / from a phone

Because sessions persist server-side, remote access is just SSH + reattach:

```bash
ssh you@your-workstation
herdr                     # reattaches the running session
```

From a phone, use any SSH client (e.g. Termius, Blink) to the same host. For
tunnelling when off-LAN, reuse the homelab's existing edge (WireGuard/Tailscale)
rather than exposing SSH publicly. Full remote guidance:
<https://herdr.dev/docs/persistence-remote/>.

### Agents driving Herdr (socket API)

Herdr exposes a socket API so an agent can spawn panes, read another pane's
output, and wait on peers — the basis for multi-agent orchestration. See
<https://herdr.dev/docs/socket-api/> and the agent skill at
<https://herdr.dev/docs/agent-skill/>.

## Helper scripts

- [`scripts/start-session.sh`](scripts/start-session.sh) — Linux/macOS: install
  Herdr if missing, then start/reattach a session in a target directory.
- [`scripts/start-session.ps1`](scripts/start-session.ps1) — Windows: same, with
  the beta installer.

Advanced session/pane scripting is best done through the socket API (linked
above); these helpers cover the common "get me into a persistent session" path.
