#Requires -Version 5.1
<#
.SYNOPSIS
    Verified, resumable, idempotent file download for AutoOS on Windows.

.DESCRIPTION
    Task 3 of plan 2026-09-11-installer-usb-and-rescue-profile: every later
    phase-2 step (fetching a multi-gigabyte installer ISO over a connection
    that may drop mid-transfer, onto a machine that might already have a
    good copy cached from a previous run) is built on Get-AutoOSVerifiedFile
    below. It is the single choke point that never hands back a file it has
    not itself verified, and never leaves a half-downloaded file sitting
    where a later run could mistake it for a complete one.

    Does NOT use Start-BitsTransfer: plan finding A8 is that BITS does not
    reliably follow mirror redirects and fails outright where the BITS
    service is disabled by policy. Uses Invoke-WebRequest -MaximumRedirection
    10 for http(s) sources, and a plain file copy for file:// sources
    (Invoke-WebRequest does not support the file:// scheme at all - confirmed
    against this module's own PowerShell 7 runtime, where it throws
    System.NotSupportedException: The 'file' scheme is not supported).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# No -Force here: forcing a re-import from inside a module REMOVES the
# caller's global copy, which silently strips these functions from the
# session (same rule AutoOS.Install.psm1 documents at its own top).
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

# Prints the resolved AutoOS download cache directory (plan ruling P6):
# $env:AUTOOS_CACHE_DIR, else $env:LOCALAPPDATA\AutoOS\images - always
# outside the repository. Pure: does not create the directory. Callers
# create it at the point they actually need it, the same convention the
# Linux counterpart (download_cache_dir in lib/linux/download.sh) uses.
function Get-AutoOSDownloadCacheDir {
    if ($env:AUTOOS_CACHE_DIR) { return $env:AUTOOS_CACHE_DIR }
    Join-Path $env:LOCALAPPDATA 'AutoOS\images'
}

# Case-insensitive SHA-256 compare via Get-FileHash - the PowerShell
# equivalent of the sha256sum/shasum pair lib/linux/download.sh uses.
function Test-AutoOSSha256Match {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Want)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $have = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    $have.ToLowerInvariant() -eq $Want.ToLowerInvariant()
}

# Fetches <Uri> to <OutFile>. file:// sources are copied directly since
# Invoke-WebRequest does not support that scheme; everything else goes
# through Invoke-WebRequest with an explicit redirect cap - never
# Start-BitsTransfer (plan finding A8).
function Get-AutoOSRawDownload {
    param([Parameter(Mandatory)][string]$Uri, [Parameter(Mandatory)][string]$OutFile)
    if ($Uri -match '^file://') {
        $localPath = ([Uri]$Uri).LocalPath
        Copy-Item -LiteralPath $localPath -Destination $OutFile -Force
        return
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -MaximumRedirection 10
}

# Verifies <Path> against the detached signature at <SignatureUri>, trusting
# ONLY <Fingerprint>. The key is imported into a throwaway GNUPGHOME under
# the download cache dir so this never reads or writes the invoking user's
# real gpg keyring - AGENTS.md hard rule 4 (read-modify-write, never
# overwrite wholesale) is framed around PATH/profile/config files, but a
# keyring is exactly that kind of thing the user owns, and a throwaway
# homedir is the only way to check a signature without touching it.
function Test-AutoOSGpgSignature {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$SignatureUri,
        [Parameter(Mandatory)][string]$Fingerprint
    )
    $gpg = Get-Command gpg -ErrorAction SilentlyContinue
    if (-not $gpg) { throw 'gpg not found - cannot verify signature' }

    $cacheDir = Get-AutoOSDownloadCacheDir
    if (-not (Test-Path -LiteralPath $cacheDir)) { New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null }
    $gnupgHome = Join-Path $cacheDir (".gnupg-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $gnupgHome -Force | Out-Null
    $sigTmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N') + '.sig')

    try {
        Get-AutoOSRawDownload -Uri $SignatureUri -OutFile $sigTmp
        $prevHome = $env:GNUPGHOME
        $env:GNUPGHOME = $gnupgHome
        try {
            & $gpg.Source --batch --quiet --keyserver hkps://keyserver.ubuntu.com --recv-keys $Fingerprint 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { return $false }
            & $gpg.Source --batch --quiet --verify $sigTmp $Path 2>$null | Out-Null
            return ($LASTEXITCODE -eq 0)
        } finally {
            if ($null -eq $prevHome) { Remove-Item Env:\GNUPGHOME -ErrorAction SilentlyContinue }
            else { $env:GNUPGHOME = $prevHome }
        }
    } finally {
        Remove-Item -Recurse -Force -LiteralPath $gnupgHome -ErrorAction SilentlyContinue
        Remove-Item -Force -LiteralPath $sigTmp -ErrorAction SilentlyContinue
    }
}

function Get-AutoOSVerifiedFile {
    <#
      .SYNOPSIS
        Downloads -Uri to -Destination and refuses to hand back the path
        unless it verifies. Throws on any verification failure.

      .DESCRIPTION
        Mirrors lib/linux/download.sh's fetch_verified(): an empty string
        (the parameter default) skips a check the same way "-" does on the
        bash side. Idempotency (AGENTS.md SS4): a cached file that still
        matches -Sha256 is a skip, not a refetch - that check runs before
        anything touches the network. Deletes the partial file on every
        failure path: a half-downloaded file left at -Destination is worse
        than throwing loudly, because a later run cannot tell it apart from
        a complete one.
    #>
    param(
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][string]$Destination,
        [string]$Sha256 = '',
        [string]$SignatureUri = '',
        [string]$GpgFingerprint = ''
    )

    $tmp = "$Destination.part"

    if ($Sha256 -and (Test-Path -LiteralPath $Destination -PathType Leaf) -and
        (Test-AutoOSSha256Match -Path $Destination -Want $Sha256)) {
        Write-AutoOSLine "skipped: $(Split-Path -Leaf $Destination) already downloaded and verified"
        return (Resolve-Path -LiteralPath $Destination).ProviderPath
    }

    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue

    try {
        Get-AutoOSRawDownload -Uri $Uri -OutFile $tmp
    } catch {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "transport failure downloading ${Uri}: $($_.Exception.Message)"
    }

    if (-not (Test-Path -LiteralPath $tmp -PathType Leaf) -or (Get-Item -LiteralPath $tmp).Length -eq 0) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "transport failure: download of $Uri produced no file"
    }

    if ($Sha256 -and -not (Test-AutoOSSha256Match -Path $tmp -Want $Sha256)) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "checksum mismatch for $Uri"
    }

    if ($SignatureUri -and -not (Test-AutoOSGpgSignature -Path $tmp -SignatureUri $SignatureUri -Fingerprint $GpgFingerprint)) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "signature verification failed for $Uri"
    }

    $destDir = Split-Path -Parent $Destination
    if ($destDir -and -not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    try {
        Move-Item -LiteralPath $tmp -Destination $Destination -Force
    } catch {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw "could not move verified file into place: $Destination"
    }

    (Resolve-Path -LiteralPath $Destination).ProviderPath
}

Export-ModuleMember -Function `
    Get-AutoOSDownloadCacheDir, Get-AutoOSVerifiedFile, Test-AutoOSSha256Match, Test-AutoOSGpgSignature
