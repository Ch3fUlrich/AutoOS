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
plan, and only then installs anything.

---

## Why it works this way

Post-install automation usually fails in the same three ways. Each design
decision here is aimed at one of them.

| The usual failure | What AutoOS does instead |
|---|---|
| **The tool needs tools.** A setup script that needs WSL, Ansible, Python packages and a TUI library cannot run on the machine it is meant to set up. | **No dependencies.** PowerShell 5.1 and bash are already there. On Linux, `python3` is used only to read JSON. No test framework, no menu library. |
| **All or nothing.** Rigid blocks — "install the dev bundle" — mean taking software you do not want, or editing the script. | **A checkbox per component.** Profiles are just a starting set of ticks. Dependencies resolve themselves. |
| **It runs once, then rots.** Re-running reinstalls, half-fails, or overwrites your config. | **Safe to run twice.** A second run reports `skipped`. Every file it edits is backed up first, and `--undo` puts them back. |

Two more rules the code is built around, both learned the hard way:

- **Nothing is installed before you confirm.** Detection and selection have no
  side effects at all, which is what makes `--dry-run` worth trusting.
- **Never overwrite a PATH, profile or config wholesale** — read, append, write
  back. Replacing `Path` outright once wiped a user's entire environment; there
  is now exactly one code path for PATH edits, and a test that guards it.

## What it looks like

```
── Detected system ─────────────────────────────────────────────
  Operating system       Microsoft Windows 11 Home (build 26200)
  Architecture           x64
  CPU                    AMD Ryzen 5 4500U - 6 threads
  Memory                 7.4 GB
  Package managers       winget, choco
  Already present        git, node, docker, wsl

  Choose what to install                    14 of 36 selected

   TERMINAL & SHELL
   > [x] Windows Terminal          Tabbed terminal with true-colour support
     [x] PowerShell 7              Modern cross-platform PowerShell
     [ ] Oh My Posh                Prompt theme engine for PowerShell

   CODING & AI
     [x] Claude Code CLI           Anthropic's terminal coding agent
     [x] Docker Desktop            Containers - backs the local MCP stack

  ↑↓ move   SPACE toggle   A all   N none   ENTER confirm   ESC cancel
```

## What's inside

| Directory | Contains |
|---|---|
| `setup.ps1` / `setup.sh` | The two entry points. Everything else is called by these. |
| [`catalog/`](docs/catalog.md) | **What** can be installed — `windows.json`, `linux.json`, `macos.json`. Data only. |
| `lib/windows/` · `lib/linux/` | **How** it happens: detect, catalog, install, ui, state, serve. |
| [`web/`](docs/web-ui.md) | The browser UI served by `--serve`. |
| [`tests/`](docs/testing.md) | Both suites — 69 Linux, 62 Windows, no framework needed. |
| [`Windows/ansible/`](docs/remote-provisioning.md) | Provisioning *other* machines over the network. Not used by `setup.ps1`. |
| [`Linux/ubuntu_autoinstall/`](docs/remote-provisioning.md#unattended-ubuntu-install) | Unattended Ubuntu install profile. |
| `third_party/` | Vendored code under its own licence. Never edited. |
| [`docs/`](docs/README.md) | Everything below, in detail. |

### Software on offer

Run `--list` for the current set. The headline items:

| Area | Includes |
|---|---|
| Terminal | Windows Terminal, PowerShell 7, Oh My Posh, zsh + Powerlevel10k, Nerd Fonts |
| Coding & AI | Claude Code CLI, Claude Desktop, Antigravity, VS Code, Docker, Herdr, Node.js |
| Input | Handy — offline speech-to-text, so you can dictate prompts instead of typing them |
| MCP stack | Clones [agent-skills](https://github.com/Ch3fUlrich/agent-skills) and wires up Serena / Graphify / Omnigraph / Superpowers — asking for your Omnigraph URL rather than hardcoding one |
| Desktop (Windows) | Windhawk with the Explorer file-size and taskbar-clock mods, PowerToys |
| Remote | Tailscale, WireGuard, Parsec, OpenSSH |
| Science | Miniconda plus an isolated `suite2p` environment |

Adding software means adding a catalog entry. No code changes — see
[the catalog](docs/catalog.md).

## How to run it

### Profiles

A profile is a starting set of ticks; the suggestion comes from your hardware.

| Profile | For |
|---|---|
| `workstation` | A machine you sit in front of |
| `ai-coding` | Development box: editors, agents, containers, shell |
| `light` | Raspberry Pi 5 and similar — Claude Code, Tailscale, Herdr |
| `server` *(Linux)* | Headless: shell, networking, containers, no GUI |
| `custom` | Nothing pre-ticked |

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

`--dry-run` prints every command that *would* run and touches nothing. It is
the right way to see what a profile means before committing to it.

Full flag table: [Getting started](docs/getting-started.md#flags).

> **Windows:** `setup.ps1` is not code-signed, so run it as
> `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1` rather than
> loosening the machine-wide policy. [Why](docs/security.md#code-signing).

## Using it from a browser

For a machine with no keyboard attached — a Pi in a cupboard, a server you reach
over Tailscale — `--serve` gives you the same thing as a web page.

```bash
./setup.sh --serve                    # only this machine can reach it
./setup.sh --serve --bind 0.0.0.0     # reachable from your LAN / tailnet
```

```powershell
.\setup.ps1 -Serve
```

It prints a URL containing a one-time token. Open it and you get two tabs:
**Overview** — detected system, profile, components and install order, each in a
collapsible card — and **Run & log**. There is a light/dark/auto theme switch, a
list-or-grid layout for the components with a column selector, and a persistent
action bar showing the running total. Selecting a profile expands its card to
show exactly what it installs before you scroll into the detail.

**You can do the whole setup in the browser and install from there** — nothing
has to happen in the terminal except starting the server. Leave *Dry run*
ticked for the first pass, read the log, then untick it and confirm.

Dependencies are made visible rather than silently resolved. Every component
shows what it `needs` and what `needs` it; ticking Claude Code auto-adds Node.js
with a dashed purple `auto` badge, and a dependency something else requires is
marked `locked` with a tooltip naming what requires it. The **Install order**
tab turns the same information into numbered steps — step 1 needs nothing, and
each later step states what it is waiting on.

The page does not install anything itself: it calls a small local server that
runs the very same `setup.sh` / `setup.ps1` you would have typed, so the two
paths cannot drift apart.

Security, because this endpoint installs software:

- binds `127.0.0.1` unless you pass `--bind`, which prints a warning
- every API call needs the token, regenerated on each start
- `--serve --dry-run` locks the session to preview-only, server-side
- one run at a time

Full walkthrough and limits: [Browser UI](docs/web-ui.md).

## Repeat, verify, undo

```bash
./setup.sh --from-state .autoos-state.json
```

Every real run records what you chose and what happened. Replaying onto
different hardware drops what does not apply and says so. Components can declare
a `verify` command that must succeed after install, so "the package manager
said OK" is not mistaken for "it works". And `--undo` restores every file
AutoOS backed up — deliberately **without** uninstalling packages, which is left
to the tool that owns that record.

Details: [Replay, verification & undo](docs/state-and-undo.md).

## Tests

```bash
bash tests/run-tests.sh          # Linux / macOS
bash tests/run-tests.sh --wsl    # same suite through WSL2, from Windows
```

```powershell
powershell -File tests\run-tests.ps1
```

No framework to install, and **no test installs anything** — providers are
asserted on the planned command, never on system state. CI additionally runs
`shellcheck`, `PSScriptAnalyzer`, `ansible-lint`, and a secret scan that
rejects credential-shaped values and committed binaries.

Details: [Testing](docs/testing.md).

## Documentation

| Page | Answers |
|---|---|
| [Getting started](docs/getting-started.md) | How do I run it, and what do the flags do? |
| [Profiles & detection](docs/profiles.md) | What does it work out about my machine? |
| [The catalog](docs/catalog.md) | How do I add software? |
| [Browser UI](docs/web-ui.md) | How do I drive a headless machine from a browser? |
| [Replay, verification & undo](docs/state-and-undo.md) | How do I repeat a setup and get back? |
| [Architecture](docs/architecture.md) | How is it built, and why that way? |
| [Testing](docs/testing.md) | How do I check a change before shipping it? |
| [Remote provisioning](docs/remote-provisioning.md) | How do I set up a machine that isn't this one? |
| [Security](docs/security.md) | What must never be committed? |
| [Troubleshooting](docs/troubleshooting.md) | It broke. Now what? |

Working on this repo with an AI agent? [AGENTS.md](AGENTS.md) is the contract.

## Status

Windows and Linux are used and tested — including the Linux suite under WSL2.
**macOS is implemented but has not been run on real Apple hardware**; its
catalog and schema are covered by both suites, but treat a first run there as
untested and use `--dry-run`.

## Licence

[MIT](LICENSE). Vendored third-party code under `third_party/` keeps its own
licence.
