<#
.SYNOPSIS
    Merge selected servers from this stack's MCP config into Claude Code's
    global config.

.DESCRIPTION
    mcp-servers/config/mcp-claude-code.json is a reference template - by
    itself it does nothing. Claude Code reads MCP servers from
    ~/.claude.json's top-level "mcpServers" object, and nothing previously
    copied the template's entries into that file. This script does that
    merge for the server name(s) you explicitly pass via -Server: the entry
    is added or overwritten in ~/.claude.json, every other existing entry is
    left untouched, and a timestamped backup of ~/.claude.json is written
    first.

    -Server is required (not defaulted to "all") on purpose: this repo's
    MCP servers may already be wired into your Claude Code setup through a
    different path (a plugin, a project-level .mcp.json, etc.), and blanket-
    merging every template entry can silently duplicate or shadow those.
    Register one server at a time, deliberately.

    Run this after editing mcp-claude-code.json (e.g. after adding a new
    server or fixing an args typo like the missing graphifyy[mcp] extra) and
    restart Claude Code afterwards - MCP servers are only loaded at session
    start, there is no hot-reload.

.PARAMETER Server
    One or more server names from the template's mcpServers object to
    merge in, e.g. -Server graphify or -Server graphify,serena.

.PARAMETER TemplatePath
    Path to the template mcpServers config. Defaults to
    mcp-servers/config/mcp-claude-code.json relative to this script.

.PARAMETER ClaudeConfigPath
    Path to Claude Code's global config. Defaults to ~/.claude.json.

.EXAMPLE
    .\register-claude-code-mcp.ps1 -Server graphify
    Merge just the graphify entry into ~/.claude.json.

.EXAMPLE
    .\register-claude-code-mcp.ps1 -Server graphify,serena
    Merge the graphify and serena entries.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Server,
    [string]$TemplatePath = (Join-Path $PSScriptRoot '..\..\config\mcp-claude-code.json'),
    [string]$ClaudeConfigPath = (Join-Path $HOME '.claude.json')
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $TemplatePath)) {
    Write-Host "X Template not found: $TemplatePath" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $ClaudeConfigPath)) {
    Write-Host "X Claude Code config not found: $ClaudeConfigPath (run Claude Code at least once first)" -ForegroundColor Red
    exit 1
}

$template = Get-Content $TemplatePath -Raw | ConvertFrom-Json
$templateNames = @($template.mcpServers.PSObject.Properties.Name)
$unknown = @($Server | Where-Object { $_ -notin $templateNames })
if ($unknown.Count -gt 0) {
    Write-Host "X Unknown server name(s): $($unknown -join ', ')" -ForegroundColor Red
    Write-Host "  Available in template: $($templateNames -join ', ')" -ForegroundColor DarkGray
    exit 1
}

$config = Get-Content $ClaudeConfigPath -Raw | ConvertFrom-Json -AsHashtable

$backupPath = "$ClaudeConfigPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $ClaudeConfigPath $backupPath
Write-Host "Backed up $ClaudeConfigPath -> $backupPath" -ForegroundColor DarkGray

if (-not $config.ContainsKey('mcpServers')) {
    $config['mcpServers'] = @{}
}

$added = @()
$updated = @()
foreach ($name in $Server) {
    $prop = $template.mcpServers.PSObject.Properties[$name]
    $value = $prop.Value | ConvertTo-Json -Depth 10 | ConvertFrom-Json -AsHashtable
    if ($config['mcpServers'].ContainsKey($name)) {
        $updated += $name
    } else {
        $added += $name
    }
    $config['mcpServers'][$name] = $value
}

$config | ConvertTo-Json -Depth 20 | Set-Content -Path $ClaudeConfigPath -Encoding utf8

Write-Host '======================================================================' -ForegroundColor Cyan
if ($added.Count -gt 0) { Write-Host "  added:   $($added -join ', ')" -ForegroundColor Green }
if ($updated.Count -gt 0) { Write-Host "  updated: $($updated -join ', ')" -ForegroundColor Yellow }
Write-Host "  Restart Claude Code for changes to take effect (no hot-reload)." -ForegroundColor Cyan
Write-Host '======================================================================' -ForegroundColor Cyan
