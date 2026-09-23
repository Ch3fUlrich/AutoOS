# AutoOS

Set up a freshly installed machine without clicking through twenty installers.

```powershell
.\setup.ps1          # Windows 10/11
```

```bash
./setup.sh           # Debian · Ubuntu · Raspberry Pi OS · macOS
```

One command per platform. It works out what kind of machine it is running on,
suggests a sensible profile, lets you tick exactly what you want, shows you the
plan, and only then installs anything. **Nothing is installed before you
confirm**, and a second run reports `skipped` rather than reinstalling.

![AutoOS web UI — Overview: detected system, profiles, components and install order](docs/assets/webui-overview.png)

## Usage

### Profiles

A profile is a starting set of ticks; the suggestion comes from your hardware.

| Profile | For |
|---|---|
| `workstation` | A machine you sit in front of |
| `ai-coding` | Development box: editors, agents, containers, shell |
| `light` | Raspberry Pi 5 and similar — Claude Code, Tailscale, Herdr |
| `server` *(Linux)* | Headless: shell, networking, containers, no GUI |
| `custom` | Nothing pre-ticked |
| `everyday` | Browsers, media, gaming, remote control and resin-printing tools |

Details: [Profiles & detection](docs/profiles.md).

### Common runs

```bash
./setup.sh --profile light --dry-run        # what a Pi would get; changes nothing
./setup.sh --only claude-code,tailscale -y  # just those two, plus dependencies
./setup.sh --from-state .autoos-state.json  # repeat a previous machine's setup
./setup.sh --undo                           # restore the files it backed up
./setup.sh --list                           # every component id
```

```powershell
.\setup.ps1 -Profile ai-coding -DryRun
.\setup.ps1 -Only claude-code,tailscale -Yes
.\setup.ps1 -FromState .autoos-state.json
.\setup.ps1 -Undo
```

`--dry-run` prints every command that *would* run and touches nothing — the
right way to see what a profile means before committing to it. Full flag table:
[Getting started](docs/getting-started.md#flags).

> **Windows:** `setup.ps1` is not code-signed, so run it as
> `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1` rather than
> loosening the machine-wide policy. [Why](docs/security.md#code-signing).

### From a browser

For a machine with no keyboard attached — a Pi in a cupboard, a server you reach
over Tailscale — `--serve` gives you the same setup as a web page, driven by the
very same `setup.sh` / `setup.ps1`:

```bash
./setup.sh --serve                    # only this machine can reach it
./setup.sh --serve --bind 0.0.0.0     # reachable from your LAN / tailnet
```

Full walkthrough and limits: [Browser UI](docs/web-ui.md).

### AI agents and model routing

Every agent installed here — OpenCode, Zed's agent panel, Neovim + sidekick,
OpenHands — talks to one local router (OmniRoute on `127.0.0.1:20128`), with
LiteLLM on `:4000` as a manual fallback. Free legs go first; the `tierN-clean`
twins are the paid-only, no-training chains for sensitive data.

```powershell
.\configuration\omniroute\apply.ps1           # register keys, build the combos
.\configuration\start-stack.ps1 -App opencode # gateway + app, wired
```

Tiers, fallback trees, per-app start commands and provider quirks:
[Model routing](docs/models.md) · [API keys](docs/api-keys.md).

## Documentation

| Page | Answers |
|---|---|
| [Getting started](docs/getting-started.md) | How do I run it, and what do the flags do? |
| [Profiles & detection](docs/profiles.md) | What does it work out about my machine? |
| [The catalog](docs/catalog.md) | What can be installed, and how do I add software? |
| [Browser UI](docs/web-ui.md) | How do I drive a headless machine from a browser? |
| [Building a bootable USB stick](docs/usb-creator.md) | How do I make an installer or rescue stick? |
| [Replay, verification & undo](docs/state-and-undo.md) | How do I repeat a setup and get back? |
| [Desktop setup](docs/desktop-setup.md) | Everyday apps, shell design, and useful extra profiles |
| [Model routing](docs/models.md) | Which AI model answers, and how do free-first fallbacks work? |
| [Routing map](docs/routing.md) | Which script creates each routing edge? |
| [API keys](docs/api-keys.md) | Where does each key come from, and what does it cost? |
| [OpenHands Agent Canvas](docs/openhands.md) | How do I install, configure and start the web UI? |
| [Unattended orchestration](.agents/skills/unattended-orchestration/unattended-orchestration.md) | How do 3-tier agent runs stay cheap, parallel and resumable? |
| [Omnigraph memory](docs/omnigraph.md) | How do agents recall and persist durable memory? |
| [Remote provisioning](docs/remote-provisioning.md) | How do I set up a machine that isn't this one? |
| [Download troubleshooting](docs/downloads.md) | Why is WinGet slow, and how do I pick another downloader? |
| [Troubleshooting](docs/troubleshooting.md) | It broke. Now what? |
| [WSL native agent home](docs/wsl-agent-home.md) | Why do agent tools need native ext4 state on WSL? |
| [Architecture](docs/architecture.md) | How is it built, and why that way? |
| [Testing](docs/testing.md) | How do I check a change before shipping it? |
| [Security](docs/security.md) | What must never be committed? |
| [Verification](docs/verification.md) | What was tested and proven, and what is known broken? |
| [Task board](docs/tasks.md) | Which tracks are Running / Queued / Done right now? |
| [Handoff](docs/handoff.md) | Done vs open tasks for the next agent |
| [OpenHands runbook](docs/openhands-runbook.md) | How do I continue this work from inside OpenHands? |

Working on this repo with an AI agent? [AGENTS.md](AGENTS.md) is the contract.

## Status

Windows and Linux are used and tested — including the Linux suite under WSL2.
**macOS is implemented but has not been run on real Apple hardware**; its
catalog and schema are covered by both suites, but treat a first run there as
untested and use `--dry-run`.

## Licence

[MIT](LICENSE). Vendored third-party code under `third_party/` keeps its own
licence.
