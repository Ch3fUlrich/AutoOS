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
* Token usage is measured by a second task (below), not by this one.

## Token recycle (D-2267): the second task

`oc_l1.py start` prints `recycle-command: <command>` when it succeeds (`oc_l2.py start` returns it as
`recycle_command`, and so do the MCP `oc_start` / `l2_start`): that command is one token-usage check of one
lane, and this task runs it every few minutes. One check per run, no loop - below the handoff limit it does
nothing, past it it posts the handoff prompt once, past the hard limit it restarts the session from the
handoff, never in the middle of a merge. Same wrapper shape as the start task (the password is read from
the protected file, never put in the task), with `recycle` in place of `start`:

```
@echo off
for /f %%v in (<protected-file>) do set AUTOOS_OCL1_LANEX_PW=%%v
python <checkout>\tools\oc_l1.py recycle --name <lane> --config <config>
set RC=%errorlevel%
rem exit 3 = deferred (a merge is in progress): expected, not a failed run (systemd: SuccessExitStatus=3)
if %RC%==3 exit /b 0
exit /b %RC%
```

`<config>` is the lane's config: an L1 lane's host-local `%LOCALAPPDATA%\autoos\oc-l1.json` (the default -
drop `--config`); an L2 lane's generated config is `%AUTOOS_OCL2_STATE_DIR%\<lane>.json` (default
`%TEMP%\autoos-oc-l2\<lane>.json`; `oc_l2.py start` returns it as `config`).

`Register-ScheduledTask` (an example - nothing here registers a task for you):

```powershell
Register-ScheduledTask `
  -TaskName "OcL1Recycle-<lane>" `
  -Action (New-ScheduledTaskAction `
      -Execute "cmd.exe" `
      -Argument "/c `"<checkout>\tools\oc-l1\oc-l1-recycle-<lane>.cmd`"") `
  -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) `
      -RepetitionInterval (New-TimeSpan -Minutes 3)) `
  -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -MultipleInstances IgnoreNew)
```

`schtasks` equivalent:

```
schtasks /Create /SC MINUTE /MO 3 /TN "OcL1Recycle-<lane>" /TR "cmd.exe /c <checkout>\tools\oc-l1\oc-l1-recycle-<lane>.cmd"
```

`-MultipleInstances IgnoreNew` keeps runs from overlapping (the check also holds
`<scratch_dir>\recycle.lock` and answers `busy` if another check is running). The exit code is the check's:
exit 0 ok, 2 config error (bad thresholds, or the lane's `password_env` unset), 3 deferred (a merge is in
progress, retried next run - **expected, not a failure**: with the wrapper above, which maps 3 to 0 (the
systemd unit declares the same with `SuccessExitStatus=3`), Task Scheduler's "Last Run Result" shows 0x0 for a
merge window; 0x3 appears only if that mapping is removed), 4 server/session unreachable or a step failed, 6 token usage present but
unreadable (nothing restarted). A monitoring hook can act on 2/4/6. Details: `docs/ai/opencode-fleet.md`,
section "recycle".
