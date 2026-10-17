---
name: autoos-install
description: Use when installing software or dev tooling on a fresh Windows or Linux/macOS machine with AutoOS, provisioning a workstation non-interactively, replaying a saved setup, or adding a package to the AutoOS catalog.
---

# AutoOS install

AutoOS provisions a **local** machine: detect what's there, offer a checklist,
show the plan, install only after confirmation. It runs with admin/root rights,
usually exactly once per machine — a bug here leaves someone with a
half-configured computer, not a failed CI job.

## When this applies

- Installing/uninstalling-selection of software on the machine you're running on
  (Windows via `setup.ps1`, Linux/Debian/Ubuntu/Raspberry Pi OS/macOS via `setup.sh`).
- Adding a new installable component to the catalog.
- Replaying a previous machine's selection, or undoing config-file edits AutoOS made.

**Not** for: provisioning a *different* machine over the network (that's
`Windows/ansible/`, a separate mechanism `setup.ps1`/`setup.sh` never call), or
editing `setup.ps1`/`setup.sh` to add a package — that always means the catalog
schema is missing a field, not that the entry point needs code.

## The pipeline

`detect → profile → select → plan → confirm → execute → report`

Detection and selection have **no side effects**. Execution never asks
questions — every prompt is answered before the first package is touched. This
is what makes `--dry-run` trustworthy and non-interactive runs (`--yes`) safe.

## Entry points and verified flags

| Windows (`setup.ps1`) | Linux/macOS (`setup.sh`) | Does |
|---|---|---|
| `-InstallProfile <name>` (alias `-Profile`) | `--profile <name>` | Skip the profile question |
| `-Only a,b` | `--only a,b` | Install exactly these component ids (+ dependencies) |
| `-DryRun` | `--dry-run` | Print every command; touch nothing |
| `-Yes` | `--yes` / `-y` | Non-interactive: take defaults, skip confirmation |
| `-NoColor` | `--no-color` | Disable ANSI colour |
| `-ListComponents` | `--list` | Print the catalog and exit |
| `-CheckCatalog` | `--check-catalog` | Validate catalog schema, non-zero on error |
| `-Config <file>` | `--config <file>` | Config file (default `autoos.config.json`) |
| `-Serve` `-Port` `-Bind` | `--serve` `--port` `--bind` | Browser UI for headless machines |
| `-FromState <file>` | `--from-state <file>` | Replay a previous run |
| `-SaveState <file>` | `--save-state <file>` | Where to write this run's state |
| `-NoVerify` | `--no-verify` | Skip the post-install "does it run?" check |
| `-Installed` | `--installed` | List what is already present, then exit |
| `-ClaudeSessions <action>` | `--claude-sessions <action>` | status / snapshot / restore / configure the Claude session autostart |
| `-Undo` | `--undo` | Restore files AutoOS backed up (never uninstalls packages) |

Don't assume symmetry beyond the table above; when unsure, read the `param()`
block in `setup.ps1` or the `case` in `setup.sh` rather than guessing.

### Copy-pasteable commands

```bash
./setup.sh --list                              # every component id on this platform
./setup.sh --check-catalog                     # validate all three catalogs
./setup.sh --only claude-code --dry-run --yes   # dry-run a named set
./setup.sh --only claude-code,tailscale --yes   # real install, named set + deps
./setup.sh --profile ai-coding --dry-run        # dry-run a whole profile
./setup.sh --from-state .autoos-state.json      # replay a previous machine's setup
./setup.sh --undo --dry-run                     # preview what undo would restore
./setup.sh --undo --yes                         # restore backed-up files, no prompt
```

```powershell
.\setup.ps1 -ListComponents
.\setup.ps1 -CheckCatalog
.\setup.ps1 -DryRun -Only claude-code
.\setup.ps1 -Only claude-code,tailscale -Yes
.\setup.ps1 -Profile ai-coding -DryRun
.\setup.ps1 -FromState .autoos-state.json
.\setup.ps1 -Undo
```

Windows is unsigned: run `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 ...`
rather than loosening the machine-wide execution policy.

## Profiles

A profile is only a starting set of ticks — everything stays editable, and
`--only`/`-Only` overrides it entirely.

| Profile | Windows | Linux | macOS | For |
|---|:---:|:---:|:---:|---|
| `workstation` | ✓ | ✓ | ✓ | A machine you sit in front of |
| `ai-coding` | ✓ | ✓ | ✓ | Dev box: editors, agents, containers, shell |
| `light` | ✓ | ✓ | ✓ | Raspberry Pi 5-class hardware |
| `server` | — | ✓ | — | Headless: shell, networking, containers, no GUI |
| `everyday` | ✓ | ✓ | ✓ | Personal desktop — browsers, media, gaming, remote control |
| `custom` | ✓ | ✓ | ✓ | Nothing pre-ticked |

`server` doesn't exist on Windows/macOS; a headless machine there is suggested
`ai-coding` instead.

## Answering prompts non-interactively

Two mechanisms, checked in this order — a saved `autoos.config.json` answer,
then an `AUTOOS_ANSWER_<PROMPT_KEY>` env var (upper-cased, `-` → `_`):

```jsonc
// autoos.config.json — copy from autoos.config.example.json
{
  "version": 1,
  "profile": "workstation",
  "answers": { "git_user_name": "Jane Doe", "git_user_email": "jane@example.com" }
}
```

```bash
# worked example — verified: dry-runs git-config with an env-supplied answer
AUTOOS_ANSWER_GIT_USER_NAME="Test User" \
AUTOOS_ANSWER_GIT_USER_EMAIL="test@example.com" \
  ./setup.sh --only git-config --dry-run --yes
```

`--yes`/`-Yes` alone is not enough if a selected component has an unanswered
`prompt` with no default and no config/env answer — supply one of the two above.

## Hard rules (from AGENTS.md — violating any is a defect)

1. **Never commit a secret.** No passwords, tokens, hostnames, IPs, usernames,
   emails — not even "obviously fake"-looking real values. This repo is public.
2. **Never commit a vendor binary.** No `.exe`/`.msi`/`.deb`/`.dmg`; download at
   run time from the vendor's own URL.
3. **Every destructive action is opt-in and announced** in the confirmation
   summary before it runs.
4. **Never overwrite a PATH, shell profile or config wholesale.** Read the
   existing value, append idempotently, write back.
5. **Never edit a user-owned file without backing it up first**
   (`<file>.autoos-backup-<timestamp>`).
6. **Safe to run twice.** A second run must report `skipped`, never
   `installed`, for anything already present.
7. **Always `--dry-run`/`-DryRun` before a real run** — it prints every
   command and changes nothing; the test suites assert this literally.

## Adding software (catalog is data, not code)

Edit `catalog/windows.json`, `catalog/linux.json` and/or `catalog/macos.json` —
never `setup.ps1`/`setup.sh`. Put the same product under the same `id` in every
catalog it appears in.

| Field | Required | Meaning |
|---|:---:|---|
| `id` | yes | Stable, kebab-case. **A contract — never reused or renamed**; breaks `--only` and saved state. |
| `name` | yes | Menu label |
| `description` | yes | One line, ≤ 70 chars (schema test enforces it) |
| `provider` | yes | `winget` `choco` `npm` `apt` `snap` `brew` `script` `custom` |
| `package` | yes | Provider-specific identifier |
| `verify` | | Command that must exit 0 after install |
| `requires` | | Other component ids — resolved topologically, never hand-order the catalog |
| `profiles` | | Which profiles pre-tick this; omit/`[]` = manual-only |
| `arch` | | Restricts to e.g. `["x64"]`; omit = any |
| `cask` | | macOS only: `brew install --cask` |
| `source` | | winget only: e.g. `"msstore"` |
| `postInstall` | | Function name in `lib/` run after a successful install |
| `prompt` | | Key into the catalog's `prompts` object |
| `notes` | | Shown under the plan entry |
| `launcher` | | `"none"` for a background service with no command |

Minimal worked example (`catalog/linux.json`):

```jsonc
{
  "id": "htop-plus",
  "name": "htop+",
  "description": "Interactive process viewer.",
  "provider": "apt",
  "package": "htop",
  "verify": "htop --version"
}
```

Workflow:

```bash
./setup.sh --check-catalog                    # schema test covers every entry
./setup.sh --only htop-plus --dry-run          # read the planned command
./setup.sh --only htop-plus                    # run it
./setup.sh --only htop-plus                    # run again — must report skipped
```

No new test file is needed for a catalog-only change. For a winget id, confirm
it first: `winget show --id <the.id> --exact --disable-interactivity`.

## When something fails

1. Read the `--dry-run`/`-DryRun` output first — it names the exact command.
2. Check the timestamped log in `logs/`.
3. `docs/troubleshooting.md` — execution-policy errors, CRLF-on-Linux, stuck
   `--serve` ports, "installed but unverified" (usually just needs a new
   terminal — PATH was updated after the current shell started).
4. `./setup.sh --check-catalog` / `-CheckCatalog` catches a malformed entry
   before anything installs.
