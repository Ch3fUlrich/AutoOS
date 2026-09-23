' omnigraph-sync-hidden.vbs - launch the Omnigraph sync with NO console window.
'
' Why this exists: a Scheduled Task that runs "only when the user is logged on"
' flashes a console (conhost) window every run even with pwsh -WindowStyle Hidden,
' because the host window is created before PowerShell can hide itself. wscript.exe
' is itself windowless, and WshShell.Run with intWindowStyle = 0 starts pwsh fully
' hidden - so the 5-minute sync runs invisibly in the background.
'
' The Scheduled Task action is:  wscript.exe "<this.vbs>" "<optional pwsh path>"
' Arg 0 (optional): full path to pwsh.exe; defaults to "pwsh.exe" from PATH.
Option Explicit
Dim sh, fso, here, pwsh, sync, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
If WScript.Arguments.Count > 0 Then
    pwsh = WScript.Arguments(0)
Else
    pwsh = "pwsh.exe"
End If
sync = here & "\sync-windows.ps1"
cmd = """" & pwsh & """ -NoProfile -ExecutionPolicy Bypass -File """ & sync & """"
' 0 = hidden window; True = wait, so the sync's real exit code becomes the task's
' LastTaskResult ("exit code is the truth" - see SYNC-MANUAL.md) instead of always 0.
Dim rc
rc = sh.Run(cmd, 0, True)
WScript.Quit rc
