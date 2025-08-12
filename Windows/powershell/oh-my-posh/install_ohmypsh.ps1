# Install Oh My Posh
winget install JanDeDobbeleer.OhMyPosh --source winget --scope user --force

# Download and install Meslo Nerd Font
$fontUrls = @(
    "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.0.2/Meslo.zip"
)

$fontZip = "$env:TEMP\Meslo.zip"
$fontDir = "$env:TEMP\MesloFonts"

# Download the font zip file
Invoke-WebRequest -Uri $fontUrls[0] -OutFile $fontZip

# Extract the zip file
Expand-Archive -Path $fontZip -DestinationPath $fontDir -Force

# Install each font file
Get-ChildItem -Path $fontDir -Filter *.ttf | ForEach-Object {
    Write-Host "Installing font: $($_.Name)"
    Start-Process -FilePath $_.FullName
    Start-Sleep -Seconds 2
}

# Create PowerShell profile file if it doesn't exist
New-Item -Path $PROFILE -Type File -Force

# Add Oh My Posh initialization to profile
$initCmd = 'oh-my-posh init pwsh --eval | Invoke-Expression'
if (-not (Get-Content $PROFILE | Select-String -Pattern $initCmd)) {
    Add-Content -Path $PROFILE -Value $initCmd
}

# Set execution policy
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force

Write-Host "Setup complete. Please set MesloLGS NF as your terminal font manually."
