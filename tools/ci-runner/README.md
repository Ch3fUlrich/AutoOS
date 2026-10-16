# Workstation CI runners

Ephemeral self-hosted GitHub Actions runners for this repository, running on
Docker Desktop: one job per container. `runner-loop.ps1` registers a fresh
runner (`--ephemeral`), that runner executes exactly one job, the container
exits, and the loop registers the next one. `supervisor.ps1` keeps every slot
in `slots.json` alive on a 60 s tick. Design: D-1380.

## Files

- `Dockerfile` - the runner image (official runner plus the tools the Linux
  jobs expect from `ubuntu-latest`).
- `CiRunner.psm1` - pure decision logic: docker argv, slot action
  (`start`/`ok`/`wait`/`restart`), offline expiry, slot names. The repo suite
  imports it from `tests/run-tests.ps1` and tests it directly.
- `runner-loop.ps1` - one slot's register/run/reregister loop.
- `supervisor.ps1` - single instance (named mutex); starts missing loops,
  restarts unhealthy ones, deletes stale offline registrations.
- `restart-slot.ps1` - restart exactly one slot, safely.
- `register-task.ps1` - registers the logon scheduled task
  `\AutoOS CI Runners`.
- `slots.example.json` - template for the live `slots.json`.

## Install

1. Copy this folder to an install directory of your choice. Every script
   resolves its siblings through `$PSScriptRoot`, so no path is baked in.
2. Copy `slots.example.json` to `slots.json` and edit it: `labels`, and one
   entry per repository with `repo`, `prefix`, `slots`, `memory` (optional
   `image`, `netns`).
3. Build the runner image from this folder:

   ```
   docker build -t workst-runner:2.338.0-2 .
   ```

4. Register the scheduled task that starts the supervisor at logon:

   ```
   pwsh register-task.ps1
   ```

Default labels are `workst,workstation,fleet-ci` (added to the runner's own
defaults `self-hosted`, `Linux`, `X64`).

## Fork pull requests must never reach the workstation

Two guards, both required:

- Repository setting: require approval for all external contributors.
- `.github/workflows/ci.yml`: every `runs-on:` line that can name the
  self-hosted labels wraps them in
  `github.event.pull_request.head.repo.full_name == github.repository` and
  falls back to `'ubuntu-latest'`, so a fork PR is scheduled on GitHub-hosted
  runners only. The Windows job stays on `windows-latest`.

## Stop

- Stop one slot: create the file `stop-<slot>` in this folder. The loop exits
  after its current iteration.
- Stop everything: create `stop-supervisor` in this folder (the loops keep
  running; touch `stop-<slot>` for each of those too).
- Restart one slot:

  ```
  pwsh restart-slot.ps1 -Repo owner/name -Name <slot> -Memory <m>
  ```

  It refuses while that slot's runner is busy (exit code 3, `BUSY ...`) - a
  running job is never killed.

Runtime files in this folder are gitignored: `slots.json`, `*.log`, `stop-*`
and `pids.txt`.
