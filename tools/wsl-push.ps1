#requires -Version 7.0
# Push an already-merged commit through WSL's prepush gates, never bypassing hooks.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Sha,
    [string]$WindowsClone = (Split-Path -Parent $PSScriptRoot),
    [Parameter(Mandatory)][string]$WslClone,
    [string]$Base = 'origin/main',
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Sha -cnotmatch '\A[0-9a-fA-F]{40}\z') {
    throw 'Sha must be a full 40-hex commit id.'
}
$Sha = $Sha.ToLowerInvariant()
if ($WindowsClone -notmatch '\A([A-Za-z]):[\\/](.*)\z') {
    throw 'WindowsClone must be a drive-absolute Windows path.'
}
$WindowsPath = '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2].Replace('\', '/')
if (-not $WslClone.StartsWith('/')) { throw 'WslClone must be an absolute WSL path.' }
function ConvertTo-BashLiteral([string]$Value) {
    return "'" + $Value.Replace("'", "'\''") + "'"
}
$Header = @(
    'set -euo pipefail'
    'sha=' + (ConvertTo-BashLiteral $Sha)
    'clone=' + (ConvertTo-BashLiteral $WslClone)
    'windows=' + (ConvertTo-BashLiteral $WindowsPath)
    'base=' + (ConvertTo-BashLiteral $Base)
)
$Body = @'
die() { printf 'wsl-push: %s\n' "$*" >&2; exit 1; }
cd -- "$clone"
git fetch -q origin || die 'origin fetch failed'
if [ "$(git rev-parse origin/main)" = "$sha" ]; then
    printf 'skipped: origin/main already at %s\n' "$sha"
    exit 0
fi
git fetch -q -- "$windows" "$sha" || die 'Windows commit fetch failed'
git merge-base --is-ancestor origin/main "$sha" || die 'not a fast-forward of origin/main'
temp=$(mktemp -d)
wt="$temp/worktree"
cleanup() {
    cd -- "$clone" || exit 1
    if [ -e "$wt/.git" ]; then
        git worktree remove "$wt" || { printf 'worktree cleanup failed: %s\n' "$wt" >&2; exit 1; }
    fi
    rmdir -- "$temp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
git worktree add --detach "$wt" "$sha" || die 'worktree creation failed'
cd -- "$wt"
python3 tools/prepush.py --repo . --base "$base" || die 'prepush red'
ready=$(python3 tools/prepush.py --check-ready "$sha") || die 'check-ready not ready'
printf '%s\n' "$ready"
[[ "$ready" == *"is ready"* ]] || die 'check-ready did not print is ready'
git push origin "$sha":refs/heads/main || die 'push failed'
git fetch -q origin || die 'post-push fetch failed'
[ "$(git rev-parse origin/main)" = "$sha" ] || die 'origin/main != sha after push'
'@
$Bash = (($Header -join "`n") + "`n" + $Body).Replace("`r`n", "`n")
if ($DryRun) { Write-Output $Bash; exit 0 }
& wsl.exe -e bash -lc $Bash
if ($LASTEXITCODE -ne 0) { throw "WSL push route failed (exit $LASTEXITCODE)." }
