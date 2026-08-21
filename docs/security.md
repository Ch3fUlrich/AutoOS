# Security

## This repository is public and has leaked once

On 2026-08-21 the history was rewritten to remove credentials committed in
`Windows/ansible/inventory.yml` (and two older paths), plus a proprietary
vendor installer. The repo went from 5.1 MB to 1.7 MB.

**Rewriting history does not unpublish anything.** Values that were public must
be rotated. GitHub keeps unreachable objects fetchable by direct SHA until it
garbage-collects, and forks keep the old objects regardless.

## Never commit

| Never | Instead |
|---|---|
| Passwords, tokens, hostnames, IPs, usernames, share paths | `ansible-vault`, or a git-ignored `.env` |
| A "placeholder" that looks real | Something obviously fake: `192.0.2.10`, `you@example.org` |
| `.exe` `.msi` `.deb` `.rpm` `.dmg` | Download from the vendor at run time |

Tracked files carry `.example` templates. `.gitignore` blocks `inventory.yml`,
`vault.yml`, `.env`, `*.autoos-backup-*`, `.autoos-state.json` and every
installer extension.

CI enforces both rules on every push — see the `secrets` job in
[testing](testing.md#ci). It greps for credential shapes and rejects committed
binaries, so a mistake fails the build rather than reaching main.

## Browser UI

The `--serve` endpoint installs software, so:

- binds `127.0.0.1` unless you pass `--bind`, which prints a warning
- requires a per-run token on every API call; regenerated each start
- `--serve --dry-run` **locks the session to preview-only**, server-side
- refuses a second concurrent run

Over a tailnet a wider bind is reasonable. On anything less trusted, use SSH
port forwarding and keep the loopback default. Full detail:
[Browser UI → Security](web-ui.md#security).

## Code signing

`setup.ps1` is **not** code-signed, so Windows will not run it under the default
execution policy. There is no signature because a trusted Authenticode
certificate has to be bought and renewed, and a self-signed one only works on
machines that already trust it — which is the opposite of "fresh install".

Scope the bypass to the one invocation rather than loosening the machine:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

Downloaded a zip instead of cloning? Windows marks the files as internet-sourced:

```powershell
Get-ChildItem -Recurse | Unblock-File
```

If you do obtain a certificate, sign both entry points and every module —
PowerShell checks each file, not just the one you invoked:

```powershell
Get-ChildItem -Include *.ps1,*.psm1 -Recurse |
  Set-AuthenticodeSignature -Certificate $cert -TimestampServer http://timestamp.digicert.com
```

## Elevation

AutoOS never elevates itself. It reports whether it is elevated and warns that
machine-wide packages will be skipped if not. Deciding to run as administrator
or root is yours, and a script that silently escalates is a script you cannot
audit.

On Linux the `sudo` decision is made once at startup into `AUTOOS_SUDO`;
functions never call `sudo` directly. That keeps the scripts identical as root,
under `sudo`, and inside a container.

Homebrew is never run under sudo — it refuses, and where it does not it leaves a
root-owned prefix behind.

## Remote scripts

Several installers fetch a vendor script (`get.docker.com`,
`tailscale.com/install.sh`, the oh-my-zsh installer). That is the vendor's own
documented path, but it is still remote code:

- every one is visible in the plan before you confirm
- `--dry-run` shows the exact URL without fetching it
- none is piped straight into a shell without landing in a file first, except
  where the vendor's own instructions require it

If your threat model excludes this, use `--only` to pick components that do not
fetch remote scripts.
