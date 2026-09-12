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

# ─── Task 4B: catalog entry -> concrete download URL ───────────────────────
# catalog/images.json deliberately never stores a version (plan finding A9):
# each real entry gives an `index` release-listing URL and a `file` filename
# regex instead of a URL. Resolve-AutoOSImageUrl is the missing bridge
# between that and Get-AutoOSVerifiedFile above, which needs one concrete
# URI. Mirrors lib/linux/download.sh's image_resolve() function-for-function
# - see that file's own comment block for the full rationale (Ubuntu's
# directory-of-releases shape vs Debian's current/-symlink shape, the LTS
# selection rule, and why ambiguity is always an error).

# _Get-AutoOSDownloadRoot — same convention as AutoOS.Usb.psm1's
# Get-AutoOSUsbCatalogRoot: $PSScriptRoot always points at lib\windows
# regardless of the caller's cwd, so two parents up is the repo root for
# every caller, production or test. Kept private and duplicated rather than
# calling into AutoOS.Usb.psm1: that module is not guaranteed to be the one
# importing this one.
function _Get-AutoOSDownloadRoot {
    (Resolve-Path (Join-Path $PSScriptRoot '..\..')).ProviderPath
}

# _Get-AutoOSImageFetchUri <DirUri>
# <DirUri> is always directory-shaped (a catalog `index` value, or a
# subdirectory this resolver picked) - exactly what a live http(s) index
# server understands, so those pass through unchanged. file:// has no server
# behind it to invent an index.html response for a bare directory URI, so -
# only for that scheme, and only so tests can point `index` at a fixture
# tree with no network involved - this asks for that page by its
# conventional name explicitly.
function _Get-AutoOSImageFetchUri {
    param([Parameter(Mandatory)][string]$DirUri)
    if ($DirUri -match '^file://') { return $DirUri.TrimEnd('/') + '/index.html' }
    return $DirUri
}

# _Resolve-AutoOSImagePage <HtmlPath> <BaseUrl> <FileRegex> <SumsField>
#                          <SigField> <Stage:top|leaf> <LtsWanted>
# Parses one already-fetched directory-listing page. Returns a
# [pscustomobject] with .Status = 'ok' (.Url/.File/.Sums/.Sig set),
# 'recurse' (.Subdir set, Stage 'top' only) or 'error' (.Message set).
# Never guesses: ambiguous matches and unmatched patterns are always
# 'error', never a first-match - see lib/linux/download.sh's
# _image_resolve_parse_page for the full rationale, identical here.
function _Resolve-AutoOSImagePage {
    param(
        [Parameter(Mandatory)][string]$HtmlPath,
        [Parameter(Mandatory)][string]$BaseUrl,
        [Parameter(Mandatory)][string]$FileRegex,
        [string]$SumsField,
        [string]$SigField,
        [Parameter(Mandatory)][ValidateSet('top', 'leaf')][string]$Stage,
        [switch]$LtsWanted
    )
    $html = Get-Content -LiteralPath $HtmlPath -Raw

    $hrefs = New-Object System.Collections.Generic.List[string]
    foreach ($m in [regex]::Matches($html, 'href\s*=\s*["' + "'" + ']([^"' + "'" + ']+)["' + "'" + ']', 'IgnoreCase')) {
        $h = $m.Groups[1].Value
        if (-not $h) { continue }
        if ($h -eq '../' -or $h -eq './' -or $h -eq '/') { continue }
        if ($h.StartsWith('?') -or $h.StartsWith('#') -or $h.StartsWith('mailto:')) { continue }
        $hrefs.Add($h)
    }

    function ConvertTo-AutoOSHrefBasename([string]$href) {
        $trimmed = $href.TrimEnd('/')
        $seg = $trimmed.Substring($trimmed.LastIndexOf('/') + 1)
        return $seg.Split('?')[0]
    }

    function Test-AutoOSFieldIsPattern([string]$s) {
        if (-not $s -or $s -eq '-') { return $false }
        return [regex]::IsMatch($s, '[\[\]\(\)\+\\\$\^\*]')
    }

    function Resolve-AutoOSSidecar([string]$Kind, [string]$Field, [string]$Base, $Hrefs) {
        if (-not $Field -or $Field -eq '-') { return [pscustomobject]@{ Url = '-'; ErrorMessage = $null } }
        if (Test-AutoOSFieldIsPattern $Field) {
            $names = @($Hrefs | Where-Object { -not $_.EndsWith('/') -and (ConvertTo-AutoOSHrefBasename $_) -match $Field } |
                ForEach-Object { ConvertTo-AutoOSHrefBasename $_ } | Sort-Object -Unique)
            if ($names.Count -eq 0) {
                return [pscustomobject]@{ Url = $null; ErrorMessage = "$Kind pattern '$Field' matched nothing under $Base" }
            }
            if ($names.Count -gt 1) {
                return [pscustomobject]@{ Url = $null; ErrorMessage = "$Kind pattern '$Field' matched multiple files under ${Base}: $($names -join ', ')" }
            }
            return [pscustomobject]@{ Url = $Base + $names[0]; ErrorMessage = $null }
        }
        return [pscustomobject]@{ Url = $Base + $Field; ErrorMessage = $null }
    }

    $direct = @($hrefs | Where-Object { -not $_.EndsWith('/') -and (ConvertTo-AutoOSHrefBasename $_) -match $FileRegex })
    if ($direct.Count -gt 0) {
        $names = @($direct | ForEach-Object { ConvertTo-AutoOSHrefBasename $_ } | Sort-Object -Unique)
        if ($names.Count -gt 1) {
            return [pscustomobject]@{
                Status  = 'error'
                Message = "pattern '$FileRegex' matched multiple files at ${BaseUrl}: $($names -join ', ') - refusing to guess, no ordering rule is defined for files at the same directory level"
            }
        }
        $fname = $names[0]
        $sums = Resolve-AutoOSSidecar -Kind 'sums' -Field $SumsField -Base $BaseUrl -Hrefs $hrefs
        if ($sums.ErrorMessage) { return [pscustomobject]@{ Status = 'error'; Message = $sums.ErrorMessage } }
        $sig = Resolve-AutoOSSidecar -Kind 'sig' -Field $SigField -Base $BaseUrl -Hrefs $hrefs
        if ($sig.ErrorMessage) { return [pscustomobject]@{ Status = 'error'; Message = $sig.ErrorMessage } }
        return [pscustomobject]@{ Status = 'ok'; Url = $BaseUrl + $fname; File = $fname; Sums = $sums.Url; Sig = $sig.Url }
    }

    if ($Stage -eq 'leaf') {
        return [pscustomobject]@{ Status = 'error'; Message = "pattern '$FileRegex' matched nothing under $BaseUrl" }
    }

    $candidates = @()
    foreach ($h in $hrefs) {
        if (-not $h.EndsWith('/')) { continue }
        $name = ConvertTo-AutoOSHrefBasename $h
        if ($name -match '^([0-9]+)\.([0-9]+)(?:\.([0-9]+))?$') {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            $patch = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
            $candidates += [pscustomobject]@{ Major = $major; Minor = $minor; Patch = $patch; Name = $name }
        }
    }
    if ($candidates.Count -eq 0) {
        return [pscustomobject]@{
            Status  = 'error'
            Message = "pattern '$FileRegex' matched nothing under $BaseUrl (no files and no version-numbered subdirectories found either)"
        }
    }

    if ($LtsWanted) {
        $eligible = @($candidates | Where-Object { $_.Minor -eq 4 -and ($_.Major % 2) -eq 0 })
        if ($eligible.Count -eq 0) {
            $names = ($candidates | Sort-Object Major, Minor, Patch | ForEach-Object { $_.Name }) -join ', '
            return [pscustomobject]@{
                Status  = 'error'
                Message = "no LTS release directory (an even-numbered year's .04) found under $BaseUrl among: $names"
            }
        }
        $chosen = $eligible | Sort-Object Major, Minor, Patch | Select-Object -Last 1
    } else {
        $chosen = $candidates | Sort-Object Major, Minor, Patch | Select-Object -Last 1
    }

    return [pscustomobject]@{ Status = 'recurse'; Subdir = "$BaseUrl$($chosen.Name)/" }
}

function Resolve-AutoOSImageUrl {
    <#
      .SYNOPSIS
        Resolves one catalog/images.json entry to a concrete download
        record: Url, File, Sums (a SHA256SUMS URL, or '-'), Sig (a
        signature URL, or '-'). Throws a specific, clear message rather
        than guessing - see lib/linux/download.sh's image_resolve() for
        the full rationale (identical algorithm, mirrored here).

      .DESCRIPTION
        -CatalogPath defaults to <repo root>\catalog\images.json; tests pass
        a scratch catalog instead so catalog\images.json itself is never
        touched by a test run.
    #>
    param(
        [Parameter(Mandatory)][string]$ImageId,
        [string]$CatalogPath
    )

    if ($ImageId -in @('custom-url', 'custom-local')) {
        throw "Resolve-AutoOSImageUrl: '$ImageId' is a UI affordance (a user-supplied URL or local file path), not a catalog entry - there is no index to resolve it against"
    }

    if (-not $CatalogPath) {
        $CatalogPath = Join-Path (_Get-AutoOSDownloadRoot) 'catalog\images.json'
    }
    if (-not (Test-Path -LiteralPath $CatalogPath)) {
        throw "Resolve-AutoOSImageUrl: cannot read $CatalogPath"
    }
    $catalog = Get-Content -LiteralPath $CatalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $entry = @($catalog.images) | Where-Object { $_.id -eq $ImageId } | Select-Object -First 1
    if (-not $entry) {
        throw "Resolve-AutoOSImageUrl: no catalog entry for image id '$ImageId' in $CatalogPath"
    }

    $index = [string]$entry.index
    $fileRe = [string]$entry.file
    $sumsField = [string]$entry.sums
    $sigField = [string]$entry.sig
    if (-not $index -or -not $fileRe) {
        throw "Resolve-AutoOSImageUrl: catalog entry '$ImageId' has no 'index'/'file' to resolve"
    }
    if (-not $index.EndsWith('/')) { $index += '/' }

    $ltsWanted = $ImageId -like '*-lts'

    $work = Join-Path ([IO.Path]::GetTempPath()) ('aos_resolve_' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try {
        $topHtml = Join-Path $work 'top.html'
        Get-AutoOSVerifiedFile -Uri (_Get-AutoOSImageFetchUri $index) -Destination $topHtml | Out-Null
        $result = _Resolve-AutoOSImagePage -HtmlPath $topHtml -BaseUrl $index -FileRegex $fileRe `
            -SumsField $sumsField -SigField $sigField -Stage 'top' -LtsWanted:$ltsWanted

        if ($result.Status -eq 'recurse') {
            $subdir = $result.Subdir
            $leafHtml = Join-Path $work 'leaf.html'
            Get-AutoOSVerifiedFile -Uri (_Get-AutoOSImageFetchUri $subdir) -Destination $leafHtml | Out-Null
            $result = _Resolve-AutoOSImagePage -HtmlPath $leafHtml -BaseUrl $subdir -FileRegex $fileRe `
                -SumsField $sumsField -SigField $sigField -Stage 'leaf' -LtsWanted:$ltsWanted
        }

        if ($result.Status -ne 'ok') {
            throw "Resolve-AutoOSImageUrl: $($result.Message)"
        }

        [pscustomobject]@{
            Url  = $result.Url
            File = $result.File
            Sums = $result.Sums
            Sig  = $result.Sig
        }
    } finally {
        Remove-Item -Recurse -Force -LiteralPath $work -ErrorAction SilentlyContinue
    }
}

Export-ModuleMember -Function `
    Get-AutoOSDownloadCacheDir, Get-AutoOSVerifiedFile, Test-AutoOSSha256Match, Test-AutoOSGpgSignature, `
    Resolve-AutoOSImageUrl
