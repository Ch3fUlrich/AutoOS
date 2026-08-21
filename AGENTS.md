# AGENTS.md — working rules for AI agents in AutoOS

AutoOS provisions a freshly installed machine. Everything here runs on **someone's real
computer, usually with administrator or root rights, usually exactly once**. A bug does not
fail a test — it leaves a person with a half-configured machine. Optimise for *safe and
resumable*, not for clever.

Read this file before changing anything. `CLAUDE.md` is a pointer to it.

---

## 1. Hard rules

These are not style preferences. Violating one is a defect regardless of what else the change achieves.

1. **Never commit a secret.** No passwords, tokens, hostnames, IPs, usernames, e-mail
   addresses or share paths — not in YAML, not in a comment, not in an example, not "just a
   placeholder that looks real". Real values live in git-ignored files (`inventory.yml`,
   `*.vault.yml`, `.env`). Tracked files carry `.example` templates with obviously fake
   values. This repository is **public** and has already leaked credentials once; the history
   was rewritten on 2026-08-21 to remove them.
2. **Never commit vendor binaries or installers.** No `.exe`, `.msi`, `.deb`, `.dmg`. Download
   at run time from the vendor's own URL. Redistributing a proprietary installer is a licence
   violation and bloats the repository permanently.
3. **Every destructive action is opt-in and announced.** No `Remove-Item -Recurse -Force`,
   `rm -rf`, disk writes or `git clean` without the user having chosen it in the menu and
   seen it named in the confirmation summary.
4. **Never overwrite a PATH, profile or config wholesale.** Read the existing value, append
   idempotently, write back. The single worst bug this repository ever shipped replaced the
   user's entire `Path` with four Miniconda directories.
5. **Never modify a file the user owns without a backup.** Shell profiles, `.zshrc`,
   `settings.json`: copy to `<file>.autoos-backup-<timestamp>` first.

## 2. Architecture

Two entry points, one shared catalog, no cross-platform abstraction layer.

```
setup.ps1              Windows entry point   — pwsh 5.1+ / 7+, no dependencies
setup.sh               Linux entry point     — bash 4+, no dependencies
catalog/*.json         WHAT can be installed (declarative, platform-specific)
lib/windows/*.psm1     HOW it happens on Windows
lib/linux/*.sh         HOW it happens on Linux
web/index.html         Browser UI, served by `--serve` for headless machines
tests/                 Pester (Windows) + bats (Linux)
Windows/ansible/       Remote fleet provisioning ONLY — not used by setup.ps1
third_party/           Vendored code with its own licence. Never edit.
```

**The catalog is data, the libraries are code.** Adding software must never require touching
`setup.ps1` or `setup.sh`. If you find yourself editing an entry point to add a package, the
catalog schema is missing something — extend the schema instead.

### The pipeline

`detect → profile → select → plan → confirm → execute → report`

Each stage is a separate function and each is independently testable. Detection never
installs. Selection never touches the disk. Execution never asks questions — everything the
user needed to answer was answered before the first package was touched. This ordering is
what makes `--dry-run` meaningful and what lets a headless run be fully non-interactive.

## 3. Conventions

### Catalog entries

```jsonc
{
  "id": "windhawk",                    // stable, kebab-case, never reused
  "name": "Windhawk",                  // shown in the menu
  "description": "One short line.",    // shown in the menu, <= 70 chars
  "provider": "winget",                // winget | choco | apt | snap | script | custom
  "package": "RamenSoftware.Windhawk", // provider-specific identifier
  "profiles": ["workstation"],         // which profiles pre-tick this
  "requires": [],                      // other component ids, resolved topologically
  "arch": ["x64", "arm64"],            // omit to mean "any"
  "postInstall": "Configure-Windhawk"  // optional function name in lib/
}
```

- `id` is a contract. Renaming one breaks saved selections and `--only` flags.
- `requires` is resolved automatically; never hand-order the catalog.
- A component that cannot run on the detected machine is **hidden**, not shown-and-failing.

### Shell

- **PowerShell**: `Verb-Noun` for functions, `PascalCase` for parameters, `$script:` scope for
  module state. `Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'` at the
  top of every module. Never use `Write-Host` for data — use the `lib/windows/AutoOS.Ui.psm1`
  helpers so `--no-color` and `--json` keep working.
- **Bash**: `set -euo pipefail` at the top of every script. `snake_case` for functions,
  `UPPER_CASE` only for exported/global constants, `local` for everything else. Quote every
  expansion. `shellcheck` clean, no exceptions without an inline justification comment.
- No `sudo` inside a function — check `AUTOOS_SUDO` (set once at startup, empty when already
  root) so the scripts work identically as root, under `sudo`, and in a container.

### Output

All user-visible output goes through the UI layer (`Write-AutoOSLine` / `ui_line`). It handles
colour, `NO_COLOR`, non-TTY, and the log file simultaneously. Direct `echo` and `Write-Host`
in an installer is a bug — it bypasses the log that a user will need when something fails.

## 4. Idempotency

Every script in this repository must be safe to run **twice in a row on the same machine**.
That is the acceptance bar, and it is the single most common source of defects here.

- Guard `git clone` with a directory check, or use pull-if-present.
- Guard appends with a grep for a marker line; never blind-append to a profile.
- Prefer the package manager's own idempotency (`winget install` is a no-op when present)
  over your own check.
- A component that is already installed reports `skipped`, not `failed` and not `installed`.

`tests/` has a dedicated double-run test. If you add an installer, add it there too.

## 5. Testing

```bash
powershell -File tests/run-tests.ps1   # Windows lib + catalog + end-to-end dry runs
bash tests/run-tests.sh                # Linux lib + catalog + end-to-end dry runs
bash tests/run-tests.sh --wsl          # same, forced through WSL2 from Windows
bash tests/run-tests.sh --filter catalog
```

Both harnesses are **written in-house and have no dependencies** — no Pester, no
bats. That is deliberate and matches the rest of the repo: these scripts have to run
on a machine where nothing is installed yet, and a test suite you must `apt install`
before you can check your work is a test suite people skip. Do not replace them with
a framework.

Rules:

- **Catalog changes require no new test** — the schema test covers every entry
  automatically. Make it fail loudly on a malformed entry rather than special-casing.
- **Never test by actually installing.** Tests assert on the *planned command* and on
  pure functions, never on system state. A test that installs software is a broken test.
- Detection heuristics are tested against **synthetic system objects**
  (`New-FakeSystem`, subshell overrides of the `SYS_*` globals), not the live machine —
  otherwise the suite only passes on the machine it was written on.
- End-to-end tests run `--dry-run` only, and one of them asserts that a dry run leaves
  the filesystem untouched.
- The Linux suite must pass under WSL2, since that is where it will usually be run from.

`shellcheck` and `PSScriptAnalyzer` are used when present and **skipped with a visible
note** when not. A skip is not a pass — install them before claiming a change is clean.

## 6. Platform gotchas that have already bitten this repo

- `lookup('env', ...)` in Ansible evaluates on the **control node**, not the target. Use
  `ansible_env`.
- `win_command` does not start a PowerShell session; a cmdlet passed to it is never
  interpreted. Use `win_shell`.
- `win_environment` with a `value:` **replaces** the variable. Read-modify-write instead.
- `exec` in bash replaces the process — nothing after it runs. Never mid-script.
- Ubuntu 24.04 uses predictable interface names; `eth0` matches nothing.
- cloud-init `runcmd` string entries run under `sh`. `<(...)` is a bashism and fails.
- PowerShell 5.1 reads `Documents\WindowsPowerShell\`, PowerShell 7 reads `Documents\PowerShell\`.
  `oh-my-posh init pwsh` in the 5.1 profile themes nothing.
- Raspberry Pi is `arm64`; a great many packages simply do not exist there. Set `arch` on the
  catalog entry rather than letting it fail at install time.

## 7. Definition of done

Before you claim a change is complete:

- [ ] `bash tests/run-tests.sh` and `pwsh tests/run-tests.ps1` both pass
- [ ] `shellcheck` clean on touched `.sh`; `Invoke-ScriptAnalyzer` clean on touched `.ps1`
- [ ] Ran with `--dry-run` and read the plan output — it says what you expected
- [ ] Ran twice; the second run reports `skipped`, not `installed`
- [ ] No secret, no binary, no absolute path containing a username in a tracked file
- [ ] `README.md` updated if you changed a flag or an entry point

Do not report work as done because the code looks right. Run it.
