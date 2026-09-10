# Everyday desktop and shell setup

From the repository directory, preview the Windows profile:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Profile everyday -Yes -DryRun
```

Remove `-DryRun` to install. For the browser selector, run
`powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Serve` and open
the URL printed in the terminal. On Linux/macOS use
`bash setup.sh --profile everyday --yes --dry-run`, or `bash setup.sh --serve`.
Administrator/root permissions may be needed for selected installers.

The Windows Everyday profile offers Firefox, Chrome, Spotify, VLC, 7-Zip,
HWiNFO, Steam, Discord, Parsec, Moonlight, Jellyfin Media Player and Unified
Remote. Unified Remote installs the computer's server; install its companion
phone app separately. Jellyfin Media Player is the Jellyfin client requested.

FiiO K3 and CHITUBOX are listed with official vendor links. Where a catalog
entry has no supported automated installer, its checkbox is gray and disabled;
profile selection and Select all exclude it. Direct command-line requests for
such entries are rejected before execution. This also applies to manual-only
Linux entries. Available applications differ by platform and architecture.
For FiiO, choose the driver appropriate for the K3 from the vendor's support
page; Linux and macOS normally use USB audio class support.

Green checks mean a local installation was detected. Detection cannot prove
every application is absent: an unknown status is not an installation failure.
Already installed packages report `skipped`. Selected configuration steps can
still run to repair or update their settings.

## Oh My Posh

Preview a focused shell setup with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Only oh-my-posh -Yes -DryRun
```

Remove `-DryRun` to apply. Dependencies include PowerShell 7, Meslo Nerd Font,
PSReadLine and Terminal-Icons. The default Everyday prompt shows the folder,
clock, success/error status and long-command duration. History suggestions,
history search and menu completion make it useful without coding-specific
segments. An optional coding theme remains available:

```powershell
. Set-AutoOSPromptTheme coding
. Set-AutoOSPromptTheme everyday
```

Restart the shell after setup. The AutoOS Everyday Windows Terminal profile
uses the configured font. Existing Terminal defaults are preserved. Both
PowerShell 5.1 and PowerShell 7 profiles receive a managed initialization block;
existing customizations are retained and changed files are backed up with an
`.autoos-backup-<timestamp>` suffix. Repeating setup does not duplicate blocks.

## Further profile ideas

These are proposals, not additional presets implemented by this change:

| Profile | Useful catalog applications |
| --- | --- |
| Gaming and streaming | Steam, Discord, Parsec, Moonlight, HWiNFO, Unified Remote |
| Home media | Spotify, VLC, Jellyfin Media Player, Unified Remote |
| Research | Zotero, Obsidian, Miniconda, Suite2p |
| Remote administration | Tailscale, WireGuard, XPipe, SSH tools |
| Comfortable terminal | Windows Terminal, PowerShell 7, Oh My Posh, Nerd Font, PSReadLine, Terminal-Icons |

Consult the current platform catalog before creating a preset; some tools are
available on only one platform. New profiles belong in catalog data.

Sources: [Oh My Posh prompt setup](https://ohmyposh.dev/docs/installation/prompt),
[Terminal-Icons](https://github.com/devblackops/Terminal-Icons),
[Unified Remote downloads](https://www.unifiedremote.com/download),
[FiiO drivers](https://www.fiio.com/Driver_Download),
[CHITUBOX downloads](https://www.chitubox.com/en/download/chitubox-free).
