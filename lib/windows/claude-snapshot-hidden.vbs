Option Explicit
Dim sh, fso, here, ps1, cmd, rc
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\claude-sessions.ps1"
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """ -Action snapshot"
rc = sh.Run(cmd, 0, True)
WScript.Quit rc
