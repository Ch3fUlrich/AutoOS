<#
.SYNOPSIS
  Start (or reattach) a persistent Herdr agent-multiplexer session on Windows.
.DESCRIPTION
  Installs the Herdr beta if missing, then launches it in the target directory.
.PARAMETER WorkDir
  Directory to start the session in. Defaults to the current directory.
.EXAMPLE
  .\start-session.ps1 -WorkDir C:\Users\you\Documents\Code\agent-skills
#>
param(
    [string]$WorkDir = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command herdr -ErrorAction SilentlyContinue)) {
    Write-Host "herdr not found - installing Windows beta from https://herdr.dev/install.ps1 ..."
    powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 | iex"
}

if (-not (Get-Command herdr -ErrorAction SilentlyContinue)) {
    Write-Error "herdr is still not on PATH. Open a new terminal (or add it to PATH), then re-run. Tip: WSL gives a Linux-identical experience."
    exit 1
}

Write-Host "Starting/reattaching Herdr in: $WorkDir"
Write-Host "  detach with ctrl+b q - agents keep running; re-run this script to reattach."
Set-Location $WorkDir
herdr
