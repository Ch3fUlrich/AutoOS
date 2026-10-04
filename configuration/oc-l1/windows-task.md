# OcL1: scheduled `oc_l1.py start` on Windows (O1-LITE)

Windows has no systemd; a scheduled task plays the role of the USER unit in
[autoos-oc-l1.service](autoos-oc-l1.service). The launcher command is the same
one, against a host-local config:

```
python <checkout>/tools/oc_l1.py start --name <lane>
```

(`<checkout>` = absolute path of this repo checkout, `<lane>` = the lane's
top-level config key.)

## Rule first: the password is never in the task definition

The task must not carry the server password or set the password's environment
variable itself. `oc_l1.py start` refuses to run (exit 2) unless the lane's
`password_env` variable is set in the launcher's environment, and a value
baked into a task definition is stored where Task Scheduler reads it. Instead
a small wrapper sets the variable from a **protected file** (mode 0600 on
POSIX, NTFS ACL + no sharing on Windows) each run, and the task launches the
wrapper:

```
@echo off
set /p <checkout>... (never here)
rem read the value of <password_env-name> from the protected file, e.g.:
set /p AUTOOS_OCL1_LANEX_PW=...<rem: replaced by the real read>...
python <checkout>\tools\oc_l1.py start --name <lane>
```

The wrapper above is a template: replace the `rem` line with a real single-line
read of the value (for example `for /f %%v in (<protected-file>) do set AUTOOS_OCL1_LANEX_PW=%%v`),
where `<protected-file>` is the host-local protected file holding the value of
the lane's `password_env` variable. The file path and the variable **name**
only - never a value - may appear in tracked examples.

## Create the task

`Register-ScheduledTask` (PowerShell, preferred - no quoting hazards):

```powershell
Register-ScheduledTask `
  -TaskName "OcL1-<lane>" `
  -Action (New-ScheduledTaskAction `
      -Execute "cmd.exe" `
      -Argument "/c `"<checkout>\tools\oc-l1\oc-l1-<lane>.cmd`"") `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 09:00) `
  -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries)
```

`schtasks` equivalent:

```
schtasks /Create /SC DAILY /ST 09:00 /TN "OcL1-<lane>" /TR "cmd.exe /c <checkout>\tools\oc-l1\oc-l1-<lane>.cmd"
```

Notes:

* `<lane>` is the lane key from the host-local config (`--name <lane>`).
* The task action runs the wrapper, not `oc_l1.py` directly - that is what
  keeps the password out of the task definition.
* `start` is idempotent: a re-run that finds the lane's session still live
  prints `already live` and exits 0, so the task can fire daily.
* The exit code is the launcher's: 0 ok, 2 config/validation (including a
  missing `password_env`), 4 health timeout, 5 UNATTENDED-REFUSED (the server
  stays up for supervised use). A monitoring hook can act on 4/5.
