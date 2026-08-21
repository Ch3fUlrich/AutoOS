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

**`Could not listen on port 8777 or the 19 ports after it`**
A busy port is not this message — the server walks forward to the next free one
and prints which it took. Seeing this means every port in the range was refused,
which for a non-loopback bind means elevation or a one-time reservation:
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
Both servers release the socket in a `finally` block, and a second instance moves
to the next free port rather than refusing to start — so this is cosmetic unless
you need 8777 specifically. A leftover process from an older build can be found
with `ss -ltnp | grep 8777` (Linux) or `Get-NetTCPConnection -LocalPort 8777`
(Windows).

**The page never gets past `connecting…`**
Its scripts are being blocked — by an extension (NoScript, uBlock, a privacy
suite) or by browser policy. Every control on the page is driven by one inline
script; without it nothing on screen is live. The page now says so instead of
sitting there, but if you are on an older build, that silent `connecting…` is
the symptom. Allow scripts for `localhost:8777` and reload.

**The page says the server has stopped**
It did. The page polls `/api/ping` and clears itself once the server is gone, so
a stale tab cannot go on showing a machine it can no longer see. Nothing is
undone by this — an install already under way runs to completion in its own
process. Start the server again for a fresh URL; tokens do not survive a restart.

**It says installed - where did it go, and how do I open it?**
The report's **Where to find them** section names both, resolved from the machine
after the run. If a component is listed there with `no launcher found yet`, the
usual cause is a PATH entry this shell predates: open a new terminal (Windows) or
log out and back in (Linux/macOS) and re-run — a second run is a no-op that
reports everything as already present.

**Where are the logs?**
`logs/autoos-<timestamp>.log`, plain text with the colour stripped.
