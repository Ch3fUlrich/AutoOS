function Test-Command($cmd) { [bool](Get-Command -Name $cmd -ErrorAction SilentlyContinue) }
