# AutoOS PowerShell profile

`setup.ps1 -Only oh-my-posh` installs the dependencies and updates a backed-up,
managed block in both Windows PowerShell and PowerShell 7 console profiles.
The block loads `AutoOS.Profile.ps1`; no downloads occur at shell startup.

The default **Everyday** prompt shows the folder, time, command status and duration.
PSReadLine supplies history suggestions, history search and menu completion;
Terminal-Icons adds file icons. Open **AutoOS Everyday** in Windows Terminal to
use the installed Meslo font and the coordinated color scheme.

For the coding prompt in the current session, run:

```powershell
. Set-AutoOSPromptTheme coding
```

Set `AUTOOS_PROMPT_THEME=coding` in your environment for a persistent choice.
Both profiles and generated theme/configuration files are backed up before updates.
See [desktop setup](../../../../docs/desktop-setup.md).
