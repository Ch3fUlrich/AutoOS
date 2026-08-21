# PowerShell profile snippet

This directory used to hold `Microsoft.Powershell_profile.ps1`, whose entire
contents were an English sentence with a command in backticks. It was not valid
PowerShell: using it as a profile raised a parse error on every shell start.
(The filename was also mis-cased -- the real profile is
`Microsoft.PowerShell_profile.ps1`, capital S.)

You do not need to copy anything by hand. `setup.ps1` installs the theme and
adds the initialisation line to the profile of the shell you actually run:

```powershell
.\setup.ps1 -Only oh-my-posh
```

If you would rather do it yourself, the line is:

```powershell
oh-my-posh init pwsh --config "$env:LOCALAPPDATA\AutoOS\themes\powerlevel10k_rainbow_env.omp.json" | Invoke-Expression
```

Use `init powershell` instead of `init pwsh` if you are on Windows PowerShell 5.1,
whose profile lives in `Documents\WindowsPowerShell\` rather than `Documents\PowerShell\`.
