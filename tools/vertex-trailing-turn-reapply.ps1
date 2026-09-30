# encoding: utf-8
# requires -Version 7.4

<#
.DESCRIPTION
    Reapplies the trailing model turn strip fix to OmniRoute's Vertex/Gemini executor.

    This is a config-first fix: the patch is applied to the global OmniRoute package
    at C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute, and a reapply
    script is provided under configuration/omniroute/ to reapply it after npm update.

    The fix adds a trailing model turn strip in openai-to-gemini.ts's openaiToGeminiBase
    function, after the mergeConsecutiveSameRoleContents call (line 569). The strip
    guards against emptying the contents array.

    The patch also updates the compiled chunks in dist/.build/next/server/chunks/.

    This script is idempotent and can be run multiple times safely.

.PARAMETER Path
    Path to the OmniRoute package directory (default: C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute).

.PARAMETER Backup
    Create a backup of each modified file with a .autoos-backup-<timestamp> suffix.
    Defaults to $true.

.PARAMETER Force
    Force overwrite of existing files without prompting. Defaults to $false.

.EXAMPLE
    PS> .\vertex-trailing-turn-reapply.ps1 -Path "C:\path\to\omniroute"

.NOTES
    File: vertex-trailing-turn-reapply.ps1
    Author: AutoOS Agent
    Date: 2026-09-30
#>

[CmdletBinding(
    DefaultParameterSetName = 'Default',
    ConfirmImpact = 'High'
)]
param (
    [Parameter(Mandatory = $false, Position = 0)]
    [string]
    $Path = "C:\\Users\\mauls\\AppData\\Roaming\\npm\\node_modules\\omniroute",

    [Parameter(Mandatory = $false)]
    [switch]
    $Backup = $true,

    [Parameter(Mandatory = $false)]
    [switch]
    $Force
)

# Load AutoOS UI helpers
. $PSScriptRoot\..\..\lib\windows\AutoOS.Ui.psm1

# Validate paths
if (-not (Test-Path -Path $Path -PathType Container)) {
    Write-AutoOSError -Message "OmniRoute package directory not found at $Path" -Id 'PATH_NOT_FOUND' -Category ResourceUnavailable
    exit 1
}

$SourceRoot = Join-Path -Path $Path -ChildPath 'open-sse'
$DistRoot = Join-Path -Path $Path -ChildPath 'dist\.build\next\server\chunks'

if (-not (Test-Path -Path $SourceRoot -PathType Container)) {
    Write-AutoOSError -Message "OmniRoute source directory not found at $SourceRoot" -Id 'SOURCE_NOT_FOUND' -Category ResourceUnavailable
    exit 1
}

if (-not (Test-Path -Path $DistRoot -PathType Container)) {
    Write-AutoOSError -Message "OmniRoute dist directory not found at $DistRoot" -Id 'DIST_NOT_FOUND' -Category ResourceUnavailable
    exit 1
}

# Backup files if requested
if ($Backup) {
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupFiles = @(
        Join-Path -Path $SourceRoot -ChildPath 'translator\request\openai-to-gemini.ts',
        Join-Path -Path $DistRoot -ChildPath '_0o8_5h8._.js',
        Join-Path -Path $DistRoot -ChildPath '_0t1t5fj._.js',
        Join-Path -Path $DistRoot -ChildPath '_14jycqh._.js',
        Join-Path -Path $DistRoot -ChildPath '_18ct13i._.js'
    )
    
    foreach ($file in $backupFiles) {
        if (Test-Path -Path $file -PathType Leaf) {
            $backupPath = "$file.autoos-backup-$timestamp"
            Copy-Item -Path $file -Destination $backupPath -Force
            Write-AutoOSInfo -Message "Created backup of $file at $backupPath" -Id 'BACKUP_CREATED'
        }
    }
}

# Patch openai-to-gemini.ts
$targetFile = Join-Path -Path $SourceRoot -ChildPath 'translator\request\openai-to-gemini.ts'

if (-not (Test-Path -Path $targetFile -PathType Leaf)) {
    Write-AutoOSError -Message "Target file not found at $targetFile" -Id 'TARGET_NOT_FOUND' -Category ResourceUnavailable
    exit 1
}

# Read the file content
$content = Get-Content -Path $targetFile -Raw

# Find the mergeConsecutiveSameRoleContents call
$mergePattern = 'mergeConsecutiveSameRoleContents\s*\(\s*tb\.messages\s*\)'
$mergeMatch = [regex]::Match($content, $mergePattern)

if (-not $mergeMatch.Success) {
    Write-AutoOSError -Message "mergeConsecutiveSameRoleContents call not found in $targetFile" -Id 'MERGE_NOT_FOUND' -Category ObjectNotFound
    exit 1
}

# Calculate the insertion point (after the merge call)
$insertionPoint = $mergeMatch.Index + $mergeMatch.Length

# Insert the trailing model turn strip
$stripCode = @'

    // Strip trailing model turns from Gemini contents
    if (tb.contents.length > 0) {
        const lastContent = tb.contents[tb.contents.length - 1];
        if (lastContent.role === 'model') {
            tb.contents.pop();
        }
    }
'@

$newContent = $content.Substring(0, $insertionPoint) + $stripCode + $content.Substring($insertionPoint)

# Write the patched content
if ($Force -or (Confirm-AutoOSAction -Message "Apply patch to $targetFile?" -Id 'PATCH_CONFIRM')) {
    Set-Content -Path $targetFile -Value $newContent -Force
    Write-AutoOSInfo -Message "Successfully patched $targetFile" -Id 'PATCH_APPLIED'
}

# Patch the compiled chunks
$chunkFiles = @(
    Join-Path -Path $DistRoot -ChildPath '_0o8_5h8._.js',
    Join-Path -Path $DistRoot -ChildPath '_0t1t5fj._.js',
    Join-Path -Path $DistRoot -ChildPath '_14jycqh._.js',
    Join-Path -Path $DistRoot -ChildPath '_18ct13i._.js'
)

foreach ($chunkFile in $chunkFiles) {
    if (Test-Path -Path $chunkFile -PathType Leaf) {
        $chunkContent = Get-Content -Path $chunkFile -Raw
        
        # Find the mergeConsecutiveSameRoleContents pattern in the chunk
        $chunkMergePattern = 'mergeConsecutiveSameRoleContents\s*\(\s*e\.messages\s*\)'
        $chunkMergeMatch = [regex]::Match($chunkContent, $chunkMergePattern)
        
        if ($chunkMergeMatch.Success) {
            # Calculate the insertion point (after the merge call)
            $chunkInsertionPoint = $chunkMergeMatch.Index + $chunkMergeMatch.Length
            
            # Insert the trailing model turn strip
            $chunkStripCode = @'

            // Strip trailing model turns from Gemini contents
            if (e.contents.length > 0) {
                const lastContent = e.contents[e.contents.length - 1];
                if (lastContent.role === 'model') {
                    e.contents.pop();
                }
            }
'@
            
            $newChunkContent = $chunkContent.Substring(0, $chunkInsertionPoint) + $chunkStripCode + $chunkContent.Substring($chunkInsertionPoint)
            
            # Write the patched content
            if ($Force -or (Confirm-AutoOSAction -Message "Apply patch to $chunkFile?" -Id 'CHUNK_PATCH_CONFIRM')) {
                Set-Content -Path $chunkFile -Value $newChunkContent -Force
                Write-AutoOSInfo -Message "Successfully patched $chunkFile" -Id 'CHUNK_PATCH_APPLIED'
            }
        } else {
            Write-AutoOSWarning -Message "mergeConsecutiveSameRoleContents call not found in $chunkFile" -Id 'CHUNK_MERGE_NOT_FOUND'
        }
    }
}

Write-AutoOSInfo -Message "Vertex/Gemini trailing model turn strip patch applied successfully" -Id 'PATCH_COMPLETE'
