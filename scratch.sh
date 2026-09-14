#!/bin/bash
cat << 'EOF2' > scratch.ps1
\$dirs = @("C:\Users\runneradmin\scoop\shims", "C:\ProgramData\chocolatey\bin", "C:\Users\runneradmin\AppData\Local\Microsoft\WinGet\Links", "C:\Program Files\WinGet\Links", "C:\Users\runneradmin\AppData\Roaming\npm", "C:\Users\runneradmin\.local\bin", "C:\Users\runneradmin\.cargo\bin", "C:\Users\runneradmin\bin") | Where-Object { \$_ }
foreach (\$want in @('scoop\shims', 'chocolatey\bin', 'Microsoft\WinGet\Links')) {
    \$matching = @(\$dirs | Where-Object { \$_ -like "*\$want*" })
    if (-not \$matching) {
        Write-Error "no probe directory for \$want in: \$(\$dirs -join '; ')"
    }
}
Write-Output "OK"
EOF2
pwsh -File scratch.ps1
