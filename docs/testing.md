# Testing

```bash
bash tests/run-tests.sh                 # Linux / macOS
bash tests/run-tests.sh --wsl           # same suite, forced through WSL2 from Windows
bash tests/run-tests.sh --filter state  # only matching test names
```

```powershell
powershell -File tests\run-tests.ps1
powershell -File tests\run-tests.ps1 -Filter catalog
```

Current: **65 Linux**, **57 Windows**.

## No framework

Both harnesses are written in-house — no Pester, no bats. That is deliberate:
these scripts have to run on a machine where nothing is installed yet, and a
test suite you must install something to run is a test suite people skip.

Do not replace them with a framework.

## Rules

- **No test installs anything.** Tests assert on the *planned command* and on
  pure functions. A test that installs software is a broken test.
- **Detection is tested against synthetic machines** — `New-FakeSystem` on
  Windows, subshell overrides of the `SYS_*` globals on Linux — so the suite
  does not only pass on the machine it was written on.
- **Catalog changes need no new test.** The schema test walks every entry.
- **End-to-end tests are dry-run only**, and one of them asserts that a dry run
  executes no commands at all.

## What is covered

| Area | Examples |
|---|---|
| Catalog schema | All three catalogs valid; a malformed one is rejected with specific reasons |
| Catalog loading | Fields do not shift when optional ones are empty (the delimiter regression) |
| Filtering | arm64 hides x64-only entries; headless hides the desktop category |
| Profiles | `light` is the Pi set; `workstation` is a superset; `custom` pre-selects nothing |
| Dependencies | Transitive pull-in, correct ordering, auto-added flagging, cycle detection |
| Detection | Pi → `light`; big headless box → `server`; an 8 GB laptop is *not* downgraded |
| PATH | Appending preserves every existing entry; the same directory twice is a no-op |
| Idempotency | `append_line_once` writes once; a backup is taken before any edit |
| State | Save/load round trip; a dry run saves nothing |
| Verification | Passes for an installed binary, unverified for a missing one |
| Undo | Restores a backed-up file; never contains an uninstall command |
| Encoding | Every PowerShell file has a UTF-8 BOM |
| Browser UI | Every component has a homepage; a non-URL one is rejected; the serve payload carries `requires`/`homepage`/`verify` and survives JSON round-tripping |
| Dependency graph | No `requires` names a component the UI is never sent |
| Documentation | Every relative link resolves; every docs page is linked from the index |

## Linters

`shellcheck` and `PSScriptAnalyzer` run when present and are **skipped with a
visible note** when not. A skip is not a pass — install them before calling a
change clean:

```bash
sudo apt-get install -y shellcheck
```

```powershell
Install-Module PSScriptAnalyzer -Scope CurrentUser -Force
```

`PSUseSingularNouns` is excluded deliberately (these functions return
collections). Everything else, including `PSAvoidAssignmentToAutomaticVariable`
and `PSReviewUnusedParameter`, is treated as a real failure.

## CI

`.github/workflows/ci.yml` runs four jobs on every push and pull request:

| Job | Does |
|---|---|
| `linux` | Test suite, `shellcheck`, `yamllint` |
| `windows` | Test suite with PSScriptAnalyzer installed |
| `ansible` | `ansible-lint` plus a playbook syntax check |
| `secrets` | Greps for credential shapes and rejects committed installers |

The `secrets` job exists because this repository has leaked credentials once. A
cheap grep is worth more than trusting everyone to remember.

## Definition of done

- [ ] Both suites pass
- [ ] `shellcheck` / `Invoke-ScriptAnalyzer` clean on touched files
- [ ] Ran with `--dry-run` and read the plan
- [ ] Ran twice; the second run reports `skipped`
- [ ] No secret, no binary, no absolute path containing a username in a tracked file
