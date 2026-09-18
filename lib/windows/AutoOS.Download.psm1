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
function Get-AutoOSFileSha256 {
    <#
      .SYNOPSIS
        Lower-case SHA-256 hex of a file, streamed through .NET.
      .DESCRIPTION
        Not Get-FileHash: on the first real 6 GB download (2026-09-17,
        Windows PowerShell 5.1 launched by setup.ps1) the module's call to
        Get-FileHash failed with "not recognized as the name of a cmdlet"
        right after the download completed, although the same cmdlet
        resolves in a fresh session. Whatever the autoload state of
        Microsoft.PowerShell.Utility in that process, a hash of the file
        we are about to write to a disk must not depend on it. .NET's
        SHA256 is always present and streams, so memory stays flat for
        any file size.
    #>
    param([Parameter(Mandatory)][string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $bytes = $sha.ComputeHash($stream)
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
    ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

# ─── Uncached (cache-bypassing) hashing, for verifying what a DEVICE holds ──
# Get-AutoOSFileSha256 above reads through the Windows file cache, so right
# after a copy it can hand back the bytes that were WRITTEN rather than the
# bytes the device now stores. That is exactly the wrong answer when the
# question is "did this stick really take the image" - the 2026-09-17 build
# shipped a stick whose freshly written clusters read back as garbage, and a
# cached read would have agreed the copy was fine. The Linux side already
# avoids this with `dd iflag=direct`; FILE_FLAG_NO_BUFFERING is the Windows
# equivalent.
#
# It cannot be done with a plain FileStream: FILE_FLAG_NO_BUFFERING requires
# the read buffer to be sector-aligned and the requested count to be a
# multiple of the sector size, and a managed byte[] carries no alignment
# guarantee. Hence one small P/Invoke helper, compiled once per session, that
# reads into a manually aligned unmanaged buffer. 4096 satisfies every common
# sector size (512 and 4096 both divide it) and is itself a valid count.
$script:AutoOSUncachedReaderReady = $false
function Initialize-AutoOSUncachedReader {
    if ($script:AutoOSUncachedReaderReady) { return }
    if (-not ('AutoOSUncachedRead' -as [type])) {
        # ASCII only, and no PowerShell interpolation inside: this is C#
        # source, and a stray $ or a smart dash would break the compile
        # (handoff lesson L6's cousin).
        $source = @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;

public static class AutoOSUncachedRead
{
    const uint GENERIC_READ = 0x80000000;
    const uint FILE_SHARE_READ = 0x00000001;
    const uint FILE_SHARE_WRITE = 0x00000002;
    const uint OPEN_EXISTING = 3;
    const uint FILE_FLAG_NO_BUFFERING = 0x20000000;
    const uint FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000;
    const int ALIGN = 4096;

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern SafeFileHandle CreateFileW(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool ReadFile(
        SafeFileHandle hFile, IntPtr lpBuffer, uint nNumberOfBytesToRead,
        out uint lpNumberOfBytesRead, IntPtr lpOverlapped);

    // Hashes <path> reading straight from the device, never the cache.
    // <length> is the file's real size. Measured 2026-09-18 on NTFS: at end
    // of file ReadFile returns only the valid bytes even with no-buffering,
    // so the clamp below never changes the answer there. It stays as a guard
    // against a filesystem or driver that returns a whole final sector.
    public static string Sha256(string path, int chunkSize, long length)
    {
        if (chunkSize <= 0 || chunkSize % ALIGN != 0)
            throw new ArgumentException("chunkSize must be a positive multiple of 4096");

        using (SafeFileHandle h = CreateFileW(path, GENERIC_READ,
                   FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING,
                   FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN, IntPtr.Zero))
        {
            if (h.IsInvalid)
                throw new Win32Exception(Marshal.GetLastWin32Error());

            IntPtr raw = Marshal.AllocHGlobal(chunkSize + ALIGN);
            try
            {
                long addr = raw.ToInt64();
                IntPtr buffer = new IntPtr(((addr + ALIGN - 1) / ALIGN) * ALIGN);
                byte[] managed = new byte[chunkSize];

                using (SHA256 sha = SHA256.Create())
                {
                    long remaining = length;
                    while (remaining > 0)
                    {
                        uint got;
                        if (!ReadFile(h, buffer, (uint)chunkSize, out got, IntPtr.Zero))
                            throw new Win32Exception(Marshal.GetLastWin32Error());
                        if (got == 0)
                            throw new IOException("unexpected end of file at " + remaining + " bytes remaining");
                        int use = (int)Math.Min((long)got, remaining);
                        Marshal.Copy(buffer, managed, 0, use);
                        sha.TransformBlock(managed, 0, use, null, 0);
                        remaining -= use;
                    }
                    sha.TransformFinalBlock(new byte[0], 0, 0);
                    return BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
                }
            }
            finally
            {
                Marshal.FreeHGlobal(raw);
            }
        }
    }
}
'@
        Add-Type -TypeDefinition $source -ErrorAction Stop
    }
    $script:AutoOSUncachedReaderReady = $true
}

function Get-AutoOSUncachedFileSha256 {
    <#
      .SYNOPSIS
        Lower-case SHA-256 hex of a file, read with FILE_FLAG_NO_BUFFERING so
        the bytes come from the DEVICE and not from the Windows file cache.
        Throws when the uncached read is not possible; the caller decides
        whether to fall back and say so.
      .DESCRIPTION
        The Windows counterpart of lib/linux/usb.sh's `dd iflag=direct`
        read-back. An empty file has no sectors to read, so it short-circuits
        to the well-known SHA-256 of zero bytes rather than opening a handle.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [int]$ChunkSize = 1048576
    )
    # AUTOOS_FAKE_NO_UNCACHED=1 simulates a filesystem or driver that refuses
    # FILE_FLAG_NO_BUFFERING, so the read-back's fallback path is testable
    # without such a device (AGENTS.md SS5).
    if ($env:AUTOOS_FAKE_NO_UNCACHED -eq '1') {
        throw 'uncached reads are unavailable (AUTOOS_FAKE_NO_UNCACHED)'
    }
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($item.Length -eq 0) {
        return 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    }
    Initialize-AutoOSUncachedReader
    return [AutoOSUncachedRead]::Sha256($item.FullName, $ChunkSize, $item.Length)
}

function Test-AutoOSSha256Match {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Want)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $have = Get-AutoOSFileSha256 -Path $Path
    $have -eq $Want.ToLowerInvariant()
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

    # curl.exe first (it ships with Windows 10 1803+ / 11): it STREAMS to
    # disk. Windows PowerShell 5.1's Invoke-WebRequest -OutFile buffers the
    # whole response in memory before writing, which cannot work for a
    # multi-gigabyte ISO, and even on the part it managed the first real
    # run on 2026-09-17 crawled at ~1 MB/s where curl did 450 MB/s on the
    # same URL. -L follows the mirror redirect (plan finding A8); -f turns
    # an HTTP error page into a non-zero exit instead of a saved HTML file
    # under the ISO's name (A7) - the same flags lib/linux/download.sh uses.
    # stderr (curl's own error text) must not become a terminating error
    # under this module's $ErrorActionPreference = 'Stop' (handoff L6):
    # the exit code is the verdict, the text is only for the message.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $errText = & $curl.Source -fsSL --retry 3 --retry-delay 2 -o $OutFile -- $Uri 2>&1 | Out-String
            if ($LASTEXITCODE -ne 0) {
                throw "curl exited $LASTEXITCODE downloading ${Uri}: $($errText.Trim())"
            }
        } finally {
            $ErrorActionPreference = $prevEap
        }
        return
    }

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # Windows PowerShell 5.1's Invoke-WebRequest updates its progress bar per
    # buffer and that costs more than the transfer itself: the first real
    # 6 GB ISO download on 2026-09-17 crawled at ~1.3 MB/s with it on.
    # Progress for a USB build is reported by the caller anyway, never by
    # this helper, so the bar is pure overhead here. Scoped to this
    # function: $ProgressPreference is dynamically scoped, so callers'
    # own setting is untouched once we return.
    $ProgressPreference = 'SilentlyContinue'
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
        # gpg writes harmless notices to stderr ("gpg-agent[n]: directory
        # '...private-keys-v1.d' created" on every fresh GNUPGHOME, which is
        # EVERY call here). Under this module's $ErrorActionPreference =
        # 'Stop', a native command's stderr line redirected with 2> becomes
        # a terminating NativeCommandError (handoff lesson L6) - the first
        # real run on 2026-09-17 died on exactly that notice, before the
        # signature was ever checked. Only the exit code is the verdict;
        # stderr is noise for the duration of the two native calls.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $gpg.Source --batch --quiet --no-tty --keyserver hkps://keyserver.ubuntu.com --recv-keys $Fingerprint 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { return $false }
            & $gpg.Source --batch --quiet --no-tty --verify $sigTmp $Path 2>$null | Out-Null
            return ($LASTEXITCODE -eq 0)
        } finally {
            $ErrorActionPreference = $prevEap
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

    if ($SignatureUri) {
        # try/catch, not just the boolean: a signature check that THROWS
        # (gpg missing, signature download failed, ...) used to skip the
        # cleanup below and leave a .part behind - the same "half file
        # nobody can tell from a real one" this function exists to prevent.
        $sigOk = $false
        try {
            $sigOk = Test-AutoOSGpgSignature -Path $tmp -SignatureUri $SignatureUri -Fingerprint $GpgFingerprint
        } catch {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            throw "signature verification failed for ${Uri}: $($_.Exception.Message)"
        }
        if (-not $sigOk) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            throw "signature verification failed for $Uri"
        }
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

# Get-AutoOSDownloadRoot — same convention as AutoOS.Usb.psm1's
# Get-AutoOSUsbCatalogRoot: $PSScriptRoot always points at lib\windows
# regardless of the caller's cwd, so two parents up is the repo root for
# every caller, production or test. Kept private and duplicated rather than
# calling into AutoOS.Usb.psm1: that module is not guaranteed to be the one
# importing this one.
function Get-AutoOSDownloadRoot {
    (Resolve-Path (Join-Path $PSScriptRoot '..\..')).ProviderPath
}

# Get-AutoOSImageFetchUri <DirUri>
# <DirUri> is always directory-shaped (a catalog `index` value, or a
# subdirectory this resolver picked) - exactly what a live http(s) index
# server understands, so those pass through unchanged. file:// has no server
# behind it to invent an index.html response for a bare directory URI, so -
# only for that scheme, and only so tests can point `index` at a fixture
# tree with no network involved - this asks for that page by its
# conventional name explicitly.
function Get-AutoOSImageFetchUri {
    param([Parameter(Mandatory)][string]$DirUri)
    if ($DirUri -match '^file://') { return $DirUri.TrimEnd('/') + '/index.html' }
    return $DirUri
}

# Resolve-AutoOSImagePage <HtmlPath> <BaseUrl> <FileRegex> <SumsField>
#                          <SigField> <Stage:top|leaf> <LtsWanted>
# Parses one already-fetched directory-listing page. Returns a
# [pscustomobject] with .Status = 'ok' (.Url/.File/.Sums/.Sig set),
# 'recurse' (.Subdir set, Stage 'top' only) or 'error' (.Message set).
# Never guesses: ambiguous matches and unmatched patterns are always
# 'error', never a first-match - see lib/linux/download.sh's
# _image_resolve_parse_page for the full rationale, identical here.
function Resolve-AutoOSImagePage {
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
        $fname = $names[0]
        if ($names.Count -gt 1) {
            # The one ordering signal that IS defined at a single directory
            # level (found live on 2026-09-17: releases.ubuntu.com/26.04.1/
            # lists ubuntu-26.04-desktop-amd64.iso beside
            # ubuntu-26.04.1-desktop-amd64.iso): names identical except for
            # one dotted version number are the same artefact at different
            # point releases, and the highest version is the newest. Anything
            # else stays an error. [regex]::Match, not -match (lesson L6).
            $shapes = @{}
            $versions = @()
            $parsable = $true
            foreach ($n in $names) {
                $vm = [regex]::Match($n, '[0-9]+(?:\.[0-9]+)+')
                if (-not $vm.Success) { $parsable = $false; break }
                $shapes[$n.Substring(0, $vm.Index) + '{v}' + $n.Substring($vm.Index + $vm.Length)] = $true
                $parts = @($vm.Value.Split('.') | ForEach-Object { [int]$_ })
                while ($parts.Count -lt 4) { $parts += 0 }
                $versions += [pscustomobject]@{
                    Key  = ('{0:D6}.{1:D6}.{2:D6}.{3:D6}' -f $parts[0], $parts[1], $parts[2], $parts[3])
                    Name = $n
                }
            }
            $distinct = @($versions | ForEach-Object { $_.Key } | Sort-Object -Unique).Count
            if ($parsable -and $shapes.Count -eq 1 -and $distinct -eq $versions.Count) {
                $fname = ($versions | Sort-Object Key | Select-Object -Last 1).Name
            } else {
                return [pscustomobject]@{
                    Status  = 'error'
                    Message = "pattern '$FileRegex' matched multiple files at ${BaseUrl}: $($names -join ', ') - refusing to guess: they do not differ only by a version number, so no ordering rule applies"
                }
            }
        }
        $sums = Resolve-AutoOSSidecar -Kind 'sums' -Field $SumsField -Base $BaseUrl -Hrefs $hrefs
        if ($sums.ErrorMessage) { return [pscustomobject]@{ Status = 'error'; Message = $sums.ErrorMessage } }
        $sig = Resolve-AutoOSSidecar -Kind 'sig' -Field $SigField -Base $BaseUrl -Hrefs $hrefs
        if ($sig.ErrorMessage) { return [pscustomobject]@{ Status = 'error'; Message = $sig.ErrorMessage } }
        return [pscustomobject]@{ Status = 'ok'; Url = $BaseUrl + $fname; File = $fname; Sums = $sums.Url; Sig = $sig.Url }
    }

    if ($Stage -eq 'leaf') {
        return [pscustomobject]@{ Status = 'error'; Message = "pattern '$FileRegex' matched nothing under $BaseUrl" }
    }

    # The minor (and patch) group is OPTIONAL: Fedora's releases/ lists bare
    # integers (44/, no dot at all) - a dir name with no dot is still a
    # version, just one whose missing parts compare as 0. That 0 default is
    # also what keeps a bare integer out of the LTS rule below without any
    # extra logic: LTS requires Minor -eq 4 (an actual ".04"), and a missing
    # minor is 0, never 4.
    $candidates = @()
    foreach ($h in $hrefs) {
        if (-not $h.EndsWith('/')) { continue }
        $name = ConvertTo-AutoOSHrefBasename $h
        if ($name -match '^([0-9]+)(?:\.([0-9]+)(?:\.([0-9]+))?)?$') {
            $major = [int]$Matches[1]
            $minor = if ($Matches[2]) { [int]$Matches[2] } else { 0 }
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
        $CatalogPath = Join-Path (Get-AutoOSDownloadRoot) 'catalog\images.json'
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
    # 'leaf' is optional and absent on every entry but fedora-workstation;
    # under Set-StrictMode Latest, $entry.leaf on an object that lacks the
    # property throws, so it must be read property-safely.
    $leafProp = $entry.PSObject.Properties['leaf']
    $leafField = if ($leafProp) { [string]$leafProp.Value } else { '' }
    if (-not $index -or -not $fileRe) {
        throw "Resolve-AutoOSImageUrl: catalog entry '$ImageId' has no 'index'/'file' to resolve"
    }
    if (-not $index.EndsWith('/')) { $index += '/' }

    $ltsWanted = $ImageId -like '*-lts'

    $work = Join-Path ([IO.Path]::GetTempPath()) ('aos_resolve_' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try {
        $topHtml = Join-Path $work 'top.html'
        Get-AutoOSVerifiedFile -Uri (Get-AutoOSImageFetchUri $index) -Destination $topHtml | Out-Null
        $result = Resolve-AutoOSImagePage -HtmlPath $topHtml -BaseUrl $index -FileRegex $fileRe `
            -SumsField $sumsField -SigField $sigField -Stage 'top' -LtsWanted:$ltsWanted

        if ($result.Status -eq 'recurse') {
            $subdir = $result.Subdir
            # 'leaf' only ever applies here, after the top stage has
            # actually recursed - an entry that resolves directly at the
            # top (Debian's current/ shape) never sees it, so every
            # existing entry is unaffected by its mere presence.
            if ($leafField) { $subdir = $subdir + $leafField }
            $leafHtml = Join-Path $work 'leaf.html'
            Get-AutoOSVerifiedFile -Uri (Get-AutoOSImageFetchUri $subdir) -Destination $leafHtml | Out-Null
            $result = Resolve-AutoOSImagePage -HtmlPath $leafHtml -BaseUrl $subdir -FileRegex $fileRe `
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
    Get-AutoOSDownloadCacheDir, Get-AutoOSVerifiedFile, Test-AutoOSSha256Match, Get-AutoOSFileSha256, `
    Get-AutoOSUncachedFileSha256, Test-AutoOSGpgSignature, Resolve-AutoOSImageUrl
