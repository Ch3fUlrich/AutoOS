# omnigraph-mcp-autoos — AutoOS launcher for the pinned Omnigraph MCP bridge.
#
# Twin of tools/omnigraph-mcp-autoos.sh: change both. An MCP client starts its
# server without reading a profile, so the token is read here, from
# $env:USERPROFILE\.autoos-omnigraph.env, and the bridge that the
# omnigraph-client component pre-installed is exec'd directly (npx start-up
# measured 6.7-9.3 s — spec 2026-09-27, decision D9).
#
# Written by Install-AutoOSOmnigraphClient in lib/windows/AutoOS.Install.psm1,
# which recognises its own copy of this file by the marker line below and leaves
# a file without it alone.
# AutoOS:omnigraph-mcp-autoos
#
# The bridge lives in the private npm prefix AutoOS created for it, and Windows
# npm links a global package's bin into the prefix itself (that is why
# %APPDATA%\npm is on every Node machine's PATH) — so this path is a second
# spelling of Get-AutoOSOmnigraphClientPath/Get-AutoOSOmnigraphBridgePath in the
# module, which this standalone script cannot import. tests/run-tests.ps1 binds
# the two: it runs this file and compares what it says it looked for against what
# the installer computed.
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)

$ErrorActionPreference = 'Stop'

$envFile = Join-Path $env:USERPROFILE '.autoos-omnigraph.env'
$bridge = Join-Path (Join-Path (Join-Path $env:LOCALAPPDATA 'autoos') 'omnigraph-mcp') 'omnigraph-mcp.cmd'

if (Test-Path -LiteralPath $envFile -PathType Leaf) {
    # Only the three keys the bridge uses, assigned as literal strings: a value
    # holding $(...) must not be evaluated here, and a value the caller already
    # exported wins over the file.
    $wanted = @('OMNIGRAPH_BASE_URL', 'OMNIGRAPH_TOKEN', 'OMNIGRAPH_GRAPH_ID')
    $dq = [char]34
    $sq = [char]39
    foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
        # One rule, shared by the shell twin and the rc line install.sh writes:
        # whitespace round a value is not part of it, then one layer of matching
        # quotes goes. Nothing else is stripped, so a value that merely starts
        # with a quote keeps its text and a quote inside one survives - one env
        # file yields one set of tokens whichever reader gets there first.
        $text = $line.Trim()
        # -ceq, not -eq: PowerShell's default string compare is case-insensitive
        # and `Export FOO=bar` is not something a shell reads as an export.
        if ($text.Length -gt 7 -and $text.Substring(0, 6) -ceq 'export') {
            $sep = $text[6]
            if ($sep -eq [char]32 -or $sep -eq [char]9) { $text = $text.Substring(7).Trim() }
        }
        $cut = $text.IndexOf('=')
        if ($cut -lt 1) { continue }
        $key = $text.Substring(0, $cut).Trim()
        if ($wanted -notcontains $key) { continue }
        $value = $text.Substring($cut + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            $last = $value[$value.Length - 1]
            if (($first -eq $dq -and $last -eq $dq) -or ($first -eq $sq -and $last -eq $sq)) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if (-not $value) { continue }
        if (-not (Get-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue)) {
            Set-Item -LiteralPath "Env:$key" -Value $value
        }
    }
}

if (-not (Test-Path -LiteralPath $bridge -PathType Leaf)) {
    # Never print the token or the file's contents — this line reaches a client's
    # log. Write-Error is not used: it is a terminating error under
    # $ErrorActionPreference = 'Stop', so the 127 below would never run.
    [Console]::Error.WriteLine("omnigraph-mcp-autoos: the pinned bridge is missing at $bridge - re-run .\setup.ps1 -Only omnigraph-client")
    exit 127
}

if ($Rest) { & $bridge @Rest } else { & $bridge }
exit $LASTEXITCODE
