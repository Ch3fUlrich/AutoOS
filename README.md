# AutoOS

Set up a freshly installed machine without clicking through twenty installers.

One command per platform. It works out what kind of machine it is running on,
suggests a sensible profile, lets you tick exactly what you want, shows you the
plan, and only then installs anything.

```powershell
.\setup.ps1          # Windows 10/11
```

```bash
./setup.sh           # Debian / Ubuntu / Raspberry Pi OS
```

No prerequisites. Not WSL, not Ansible, not Python packages, not a TUI library —
which matters, because on a machine you just installed, nothing is there yet.

---

## What it looks like

```
── Detected system ─────────────────────────────────────────────
  Operating system       Microsoft Windows 11 Home (build 26200)
  Architecture           x64
  Machine                HP ENVY x360 Convertible
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

Arrow keys to move, space to toggle, enter to confirm. Dependencies are worked
out for you — tick Claude Code and Node.js comes along automatically.

## Profiles

A profile is just a starting set of ticks; you can change anything afterwards.
The suggested one is picked from the hardware.

| Profile | For | Roughly |
|---|---|---|
| `workstation` | A machine you sit in front of | Everything that fits |
| `ai-coding` | Development box | Editors, agents, containers, shell |
| `light` | Raspberry Pi 5 and similar | Claude Code, Tailscale, Herdr |
| `server` *(Linux)* | Headless | Shell, networking, containers, no GUI |
| `custom` | You decide | Nothing pre-ticked |

## Common runs

```bash
./setup.sh --profile light --dry-run     # what a Pi would get, changes nothing
./setup.sh --only claude-code,tailscale  # just these two, plus dependencies
./setup.sh --list                        # every component id
./setup.sh --check-catalog               # validate the catalog (CI-friendly)
```

```powershell
.\setup.ps1 -Profile ai-coding -DryRun
.\setup.ps1 -Only claude-code,tailscale -Yes
.\setup.ps1 -ListComponents
```

`--dry-run` / `-DryRun` prints every command that *would* run and touches
nothing. It is the right way to see what a profile means before committing.

## Headless machines: the browser UI

For a box you reach over SSH or Tailscale, drive the install from a browser:

```bash
./setup.sh --serve                    # prints a URL with a one-time token
./setup.sh --serve --bind 0.0.0.0     # reachable from your LAN / tailnet
```

```powershell
.\setup.ps1 -Serve
```

Same checkboxes, same plan, live log streamed back. The page is served locally
and drives the real `setup.sh` / `setup.ps1`, so it cannot drift from the CLI.

**It binds `127.0.0.1` by default and always requires the token printed in the
terminal.** That endpoint installs software; a wider bind is opt-in and warned
about. The token is regenerated every run.

## What is on offer

Run `--list` for the current set. The headline items:

- **Terminal** — Windows Terminal, PowerShell 7, Oh My Posh / zsh + Powerlevel10k, Nerd Fonts
- **Coding & AI** — Claude Code CLI, Claude Desktop, Antigravity, VS Code, Docker, Herdr, Node.js
- **agent-skills + MCP** — clones [agent-skills](https://github.com/Ch3fUlrich/agent-skills) and wires up Serena / Graphify / Omnigraph / Superpowers, asking for your Omnigraph server URL rather than hardcoding one
- **Desktop (Windows)** — Windhawk with the Explorer file-size and taskbar-clock mods, PowerToys
- **Remote** — Tailscale, WireGuard, Parsec, OpenSSH
- **Science** — Miniconda plus an isolated `suite2p` environment

Adding software means adding an entry to `catalog/windows.json` or
`catalog/linux.json`. No code changes.

## Provisioning *other* machines

`Windows/ansible/` provisions machines over the network. It is separate from the
local entry points and needs the usual Ansible setup:

```bash
ansible-galaxy collection install -r Windows/ansible/requirements.yml
cp Windows/ansible/inventory.example.yml Windows/ansible/inventory.yml   # then edit
ansible-vault create Windows/ansible/group_vars/windows/vault.yml
ansible-playbook -i Windows/ansible/inventory.yml Windows/ansible/main_playbook.yml --ask-vault-pass
```

`inventory.yml` and `vault.yml` are git-ignored. **Never put a real password,
hostname or share path in a tracked file** — this repository is public and has
leaked credentials once already.

## Tests

```bash
bash tests/run-tests.sh          # Linux
bash tests/run-tests.sh --wsl    # same suite, forced through WSL2 from Windows
```

```powershell
powershell -File tests\run-tests.ps1
```

No test framework to install and **no test installs anything** — providers are
asserted on the planned command, never on system state. The suites cover catalog
validation, dependency ordering, architecture and headless filtering, the
detection heuristics, PATH-append safety, and double-run idempotency.

## Repository layout

```
setup.ps1  setup.sh     the two entry points
catalog/*.json          WHAT can be installed — data, no code
lib/windows/*.psm1      HOW it happens on Windows
lib/linux/*.sh          HOW it happens on Linux
web/index.html          browser UI, served by --serve
tests/                  both suites
Windows/ansible/        remote fleet provisioning (not used by setup.ps1)
Linux/ubuntu_autoinstall/  unattended Ubuntu install profile
third_party/            vendored code under its own licence — do not edit
```

Working on this repo with an AI agent? Read [AGENTS.md](AGENTS.md) first.

## Design rules

These are enforced by tests, not just intentions:

1. **Safe to run twice.** A second run reports `skipped`, never `installed`.
2. **Never overwrite a PATH, profile or config wholesale** — read, append, write
   back, and keep a timestamped backup. Replacing `Path` outright once wiped a
   user's entire environment.
3. **Nothing is installed before you confirm.** Detection and selection have no
   side effects, which is what makes `--dry-run` meaningful.
4. **A component that cannot work here is hidden**, not offered and then failed.

## Licence

[MIT](LICENSE). Vendored third-party code under `third_party/` keeps its own
licence.
