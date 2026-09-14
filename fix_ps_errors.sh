#!/bin/bash
files=(
    "lib/windows/AutoOS.ClaudeAutostart.psm1"
    "lib/windows/AutoOS.Detect.psm1"
    "lib/windows/AutoOS.Serve.psm1"
)
for file in "${files[@]}"; do
    sed -i 's/catch { $_ | Out-Null }/catch { $null = $_ }/g' "$file"
done
