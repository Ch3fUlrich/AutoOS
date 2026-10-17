# Slow WinGet downloads and installer progress

An observed Git 2.55.0.3 installation on 2026-09-09 spent less than a second
resolving package metadata, **14 minutes 34 seconds downloading**, and about
20 seconds running the installer. The WinGet diagnostic log identified
Delivery Optimization as the downloader. A later direct HTTPS range request
to the same GitHub release transferred 8 MiB in 3.60 seconds (about 2.22 MiB/s).

This suggests a Delivery Optimization bottleneck on that machine. It is not
proof that every WinGet download is slow: the comparison was a partial
download at a different time, and network/CDN conditions can change.
Inspect WinGet's `LocalState/DiagOutputDir` logs to distinguish source lookup,
download, installation and elevation delays.

## Proposed workaround

Microsoft documents `network.downloader` values `do` (the default) and
`wininet`. Try WinINet if repeated downloads show the same pattern. From
PowerShell 7, preview the provided settings helper:

```powershell
.\Windows\powershell\set_winget_downloader.ps1 -Downloader wininet -DryRun
```

Remove `-DryRun` to apply. The helper backs up existing settings and preserves
other JSON values; comments and formatting are rewritten. Repeat with
`-Downloader do` to restore Delivery Optimization. This setting affects future
downloads, not an already running installer. AutoOS does not change it
automatically. Microsoft's `doProgressTimeoutInSeconds` controls fallback
after missing download progress, rather than limiting total installation time.

## Progress and time limits

AutoOS shows overall completed components and a second bar for the active
component. Where the installer provides a percentage or byte counts, Windows
shows that value. Otherwise the current bar is indeterminate with elapsed time;
it does not invent a percentage. Failed or interrupted runs retain their actual
overall progress.

External installer commands have a default 30-minute limit. Override it before
starting AutoOS with `AUTOOS_INSTALL_TIMEOUT_SECONDS` (1–86400 seconds). On a
timeout, AutoOS stops its owned process tree and stops the remaining plan;
check logs and any elevation prompt before retrying. This bounds the command
runner; it cannot guarantee that a vendor installer or Windows service will
roll back work already performed. AutoOS does not kill unrelated installer
services. The same runner drains output continuously to prevent full output
pipes from appearing to hang an installation.

Source: [Microsoft WinGet settings](https://learn.microsoft.com/en-us/windows/package-manager/winget/settings).
