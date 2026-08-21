# Getting started

## Requirements

Deliberately almost none — this runs on a machine you just installed, where
nothing is set up yet.

| Platform | Needs | Notes |
|---|---|---|
| Windows 10/11 | PowerShell 5.1 (built in) | `winget` recommended; Chocolatey used as fallback |
| Debian / Ubuntu / Raspberry Pi OS | `bash` 4+, `python3` | Both ship by default |
| macOS | `bash`, [Homebrew](https://brew.sh) | Untested on real hardware — see [caveat](#macos-caveat) |

`python3` on Linux is used only to read the JSON catalog; nothing else depends
on it.

## Run it

```powershell
.\setup.ps1
```

```bash
./setup.sh
```

You get, in order: what it detected, a suggested profile, a checkbox list, the
plan, and a confirmation prompt. **Nothing is installed before you confirm.**

### The menu

| Key | Does |
|---|---|
| ↑ / ↓ | Move |
| Space | Toggle the highlighted component |
| A | Select all |
| N | Select none |
| Page Up / Page Down | Jump five |
| Enter | Confirm |
| Esc / Q | Cancel — nothing is changed |

Dependencies are added for you. Tick Claude Code and Node.js comes along.

## Flags

Both entry points accept the same ideas; only the spelling differs.

| Windows | Linux / macOS | Does |
|---|---|---|
| `-Profile <name>` | `--profile <name>` | Skip the profile question |
| `-Only a,b` | `--only a,b` | Install exactly these ids (plus dependencies) |
| `-DryRun` | `--dry-run` | Print every command; change nothing |
| `-Yes` | `--yes` / `-y` | Non-interactive: take defaults, skip confirmation |
| `-NoColor` | `--no-color` | Disable ANSI colour |
| `-ListComponents` | `--list` | Print the catalog and exit |
| `-CheckCatalog` | `--check-catalog` | Validate every catalog; non-zero on any problem |
| `-Serve` | `--serve` | [Browser UI](web-ui.md) instead of the terminal menu |
| `-Port N` / `-Bind addr` | `--port N` / `--bind addr` | Where the browser UI listens |
| `-FromState <file>` | `--from-state <file>` | [Replay a previous run](state-and-undo.md) |
| `-SaveState <file>` | `--save-state <file>` | Where to write run state |
| `-NoVerify` | `--no-verify` | Skip the post-install "does it run?" check |
| `-Undo` | `--undo` | [Restore backed-up files](state-and-undo.md#undo) |

## Recipes

```bash
# See what a Raspberry Pi would get, without touching anything
./setup.sh --profile light --dry-run

# Just two things, unattended
./setup.sh --only claude-code,tailscale --yes

# Set up a second machine exactly like the first
./setup.sh --from-state .autoos-state.json
```

```powershell
# Same, on Windows
.\setup.ps1 -Profile ai-coding -DryRun
.\setup.ps1 -Only claude-code,tailscale -Yes
.\setup.ps1 -FromState .autoos-state.json
```

## Execution policy on Windows

`setup.ps1` is not code-signed, so a default Windows install will refuse to run
it. Do **not** loosen the machine-wide policy for this; scope the bypass to the
one invocation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

If you downloaded a zip rather than cloning, Windows also marks the files as
coming from the internet:

```powershell
Get-ChildItem -Recurse | Unblock-File
```

See [Security](security.md#code-signing) for why there is no signature.

## macOS caveat

macOS support is implemented — detection via `sw_vers`/`sysctl`, a Homebrew
provider with cask support, and its own `catalog/macos.json` — and its catalog
and schema are covered by both test suites. It has **not** been run on real
Apple hardware. Treat the first run as untested: use `--dry-run` and read the
plan before letting it install anything.

## What happens afterwards

- The report ends with **Where to find them**: for everything in the run, the
  path it occupies and how to start it — a command name for CLI tools, the Start
  menu entry (Windows), the Applications entry (Linux) or `open -a` (macOS).
  It is resolved from the machine, not read out of the catalog, so a blank line
  means genuinely not found rather than a guess. The usual cause of a blank is a
  PATH change that this shell has not picked up yet.
- PATH, font and shell changes need a **new terminal**; Docker and WSL need a **reboot**.
- A run state file is written (default `.autoos-state.json`) so the same
  selection can be replayed elsewhere.
- A timestamped log lands in `logs/`.
