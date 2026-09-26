# Testing

```bash
bash tests/run-tests.sh                 # Linux / macOS
bash tests/run-tests.sh --wsl           # same suite, forced through WSL2 from Windows
bash tests/run-tests.sh --filter state  # only matching test names
```

`--wsl` re-runs the suite through `wslpath`, which does not exist in Git Bash.
From Git Bash, start WSL yourself:
`wsl bash -lc "cd /mnt/c/<path-to-checkout> && bash tests/run-tests.sh"`.

`--filter` / `-Filter` is a substring match on **test names** (`it` /
`Test-Case`), not on group names. A filter that matches nothing still prints a
clean run, so read the `passed` count. Name a new test so the filter for its
area (`usb`, `fetch`, `chooser`, ...) reaches it. Comma means OR:
`--filter usb,catalog` / `-Filter usb,catalog` runs the union, so shards can
be disjoint partitions executed in parallel (one worktree each — never run a
shard in the main checkout while merges land; the FINAL lane owns the merged
HEAD).

## Parallel shards

Suites are process-isolated (mktemp scratch, port-0 fixture servers,
subshell-scoped env), so shards run concurrently from separate worktrees:

```bash
bash tests/run-tests.sh --filter usb,cache,fetch,imagecache  # L1: usb + image cache
bash tests/run-tests.sh --filter catalog,image,engine,registry  # L2: catalogs
bash tests/run-tests.sh --filter openhands,litellm,zed,router,combo,opencode,tier,serena,mcp,gateway,apply,healthcheck  # L3: ai/router
bash tests/run-tests.sh --filter profile,state,undo,verify,web,menu,template,rescue,docs,shellcheck  # L4: core/web
```

```powershell
pwsh tests/run-tests.ps1 -Filter usb  # W1
pwsh tests/run-tests.ps1 -Filter litellm,zed,router,combo,opencode,tier,openhands,apply,provider,healthcheck  # W2
pwsh tests/run-tests.ps1 -Filter catalog,detect,serve,payload,menu  # W3
pwsh tests/run-tests.ps1 -Filter profile,state,verify,serena,mcp  # W4
```

Policy: run only the filters covering touched files per change; full suites
run in a FINAL guardsOnly lane, never per change. `sh` matching is
case-sensitive, `ps1` `-like` is case-insensitive.

```powershell
powershell -File tests\run-tests.ps1
powershell -File tests\run-tests.ps1 -Filter catalog
```

Use the totals reported by the harness; coverage grows with the catalog and UI.

Additional regressions run harmless child-process fixtures and temporary config
files, never real installers:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests/test-improvements.ps1
pwsh -NoProfile -File tests/test-improvements.ps1
node tests/test-web-progress.js
```

```bash
python3 tests/test-process.py
```

The process-group checks require a POSIX host. Git Bash can exercise the Bash
suite on Windows, but does not substitute for native Linux or WSL validation.
CI runs the Linux suite and process-group tests on Ubuntu.

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
- **Fake hardware comes from environment knobs**, never the live machine:
  `AUTOOS_FAKE_LSBLK` (Linux, e.g. `python3 tests/helpers/fake_usb.py good_stick`),
  `AUTOOS_FAKE_DISKS` (Windows), `AUTOOS_FAKE_UID`, `AUTOOS_FAKE_ELEVATED`,
  `AUTOOS_FAKE_ARCH`, `AUTOOS_FAKE_DISK_ID`, `AUTOOS_FAKE_RUN_ACTIVE`,
  `AUTOOS_FORCE_FAIL`, plus `AUTOOS_CACHE_DIR` and `AUTOOS_ROOT` for a scratch
  cache or catalog root. In Git Bash `/dev/sdX` names **real** disks, so a test
  is isolated by these knobs, never by checking whether a device path exists.
- **Downloads are tested against a loopback server**: `_start_test_http_server`
  (bash) and `Start-AutoOSTestHttpServer` / `Stop-AutoOSTestHttpServer`
  (PowerShell). `Invoke-AutoOSCapturedConsole { ... }` captures what
  `Write-AutoOSLine` printed. An `$env:` variable a test sets outlives the test:
  set and clear it in `try` / `finally`.
- Before trusting a new test, break the thing it guards and watch it fail.

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

`shellcheck` and `PSScriptAnalyzer` run when present. **A skip is not a pass.**

This is not a hypothetical: shellcheck was skipped locally for lack of the binary,
so a shellcheck failure reached CI unnoticed. The Linux suite therefore falls back
to the official container when the binary is missing, and only skips when neither
is available:

```bash
shellcheck -S warning setup.sh lib/linux/*.sh tests/run-tests.sh   # if installed
docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable ...  # fallback
```

Install them anyway where you can:

```bash
sudo apt-get install -y shellcheck
```

```powershell
Install-Module PSScriptAnalyzer -Scope CurrentUser -Force
```

Three rules are excluded deliberately: `PSUseSingularNouns` (these functions
return collections), `PSUseShouldProcessForStateChangingFunctions` and
`PSAvoidUsingWriteHost` (see the analyzer test in `tests/run-tests.ps1`).
Everything else, including `PSAvoidAssignmentToAutomaticVariable` and
`PSReviewUnusedParameter`, is treated as a real failure.

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

The `linux` job's test suite never touches the network — CI runs it inside a
network namespace with only loopback up, so a test that tried the real
internet would fail fast instead of silently depending on it (see the
isolation step in `ci.yml`). That means it cannot see catalog image rot: an
upstream host moving a file, changing a listing's shape, or dropping a
mirror. `tests/live-check-images.sh` is the separate, live counterpart —
it resolves every real entry in `catalog/images.json` against the real
resolver and HEAD-checks the resolved URL, checksum manifest and every
mirror. It is deliberately kept out of `tests/run-tests.sh` and run instead
by `.github/workflows/catalog-live-check.yml` on a weekly schedule, or by
hand:

```bash
bash tests/live-check-images.sh
```

A short, explicit list at the top of that script names the images known to
be currently unresolvable and why (see `catalog/images.json` for the full
catalog) — a failure there is reported as `KNOWN`, not `FAIL`, and does not
fail the run. Prune an id from that list the moment it starts resolving
again; the script itself fails the run if a listed id unexpectedly resolves,
so that's never silently missed.

## Definition of done

- [ ] Touched-area filters green on both suites (full suites: FINAL lane only)
- [ ] `shellcheck` / `Invoke-ScriptAnalyzer` clean on touched files
- [ ] Ran with `--dry-run` and read the plan
- [ ] Ran twice; the second run reports `skipped`
- [ ] No secret, no binary, no absolute path containing a username in a tracked file
- [ ] Changed a flag? Update `README.md` and the [flag table](getting-started.md#flags)
