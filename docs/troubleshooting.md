# Troubleshooting

## Windows

**`setup.ps1 cannot be loaded because running scripts is disabled`**
Not signed. Scope the bypass to the one call:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

**Box-drawing characters render as `â€"` / a parse error**
A `.ps1` or `.psm1` lost its UTF-8 BOM. PowerShell 5.1 decodes those as ANSI.
The test suite has a check for exactly this; run it.

**`Installed but unverified`**
Normal for anything that changes PATH. Open a new terminal and re-run the
verify command yourself. It is a warning, not a failure.

**Everything is skipped and nothing installs**
Check `Elevated: no` in the detected-system panel. Machine-wide packages need
an elevated terminal.

**`Could not listen on port 8777`**
Non-loopback binding needs elevation or a one-time reservation:
```powershell
netsh http add urlacl url=http://+:8777/ user=$env:USERNAME
```

## Linux / macOS

**`bad interpreter: /bin/bash^M`**
The file has CRLF line endings. `.gitattributes` pins LF, so this means it was
edited by something that ignored it:
```bash
sed -i 's/\r$//' setup.sh
```

**`python3 is required to read the component catalog`**
```bash
sudo apt-get install -y python3
```
Only used to read JSON; nothing else depends on it.

**`No apt-get on this system`**
The Linux catalog targets Debian and Ubuntu. On macOS you should be getting the
Homebrew catalog automatically — if not, check `uname -s` reports `Darwin`.

**The script exits immediately after the banner**
Almost always a `set -e` short-circuit: a `cmd && VAR=1` as the last statement
of a function returns non-zero when `cmd` is absent, and that aborts the run.
Use an `if` block. See [architecture](architecture.md#platform-traps-already-paid-for).

**Something is offered that cannot possibly work here**
It is missing an `arch` constraint, or its category is missing
`requiresDisplay`. See [the catalog](catalog.md).

## Both

**A second run reinstalls instead of skipping**
That is a bug — idempotency is the acceptance bar. Check the component's
`Test-AutoOSInstalled` / `is_installed` branch.

**`--dry-run` seems to have changed something**
Also a bug, and a serious one. The suites assert that a dry run executes no
commands at all; please reproduce and report.

**I want the previous state back**
```bash
./setup.sh --undo --dry-run     # see what would be restored
./setup.sh --undo
```
Files only — [it does not uninstall packages](state-and-undo.md#what-it-deliberately-does-not-do).

**The browser-UI server will not stop**
`Ctrl-C` works, and so does `kill` (SIGTERM) on Linux and macOS. If a server
predating this fix is still running, close its terminal — an older build blocked
in a native accept call that could not be interrupted.

**The port is still in use after stopping**
Both servers release the socket in a `finally` block now. A leftover process from
an older build can be found with `ss -ltnp | grep 8777` (Linux) or
`Get-NetTCPConnection -LocalPort 8777` (Windows).

**Where are the logs?**
`logs/autoos-<timestamp>.log`, plain text with the colour stripped.
