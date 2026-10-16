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

## Caches

Three named Docker volumes are mounted into every slot whose repository entry
has `"cache": true` (the `-CacheVolumes` switch on the docker run argv). They
are shared by all slots and survive container restarts (and Docker Desktop
restarts), so a cold cargo download or a Rust rebuild is paid once instead of
on every job:

- `ci-cargo-registry` -> `/home/runner/.cargo/registry` - the crates.io index
  and the downloaded crate tarballs.
- `ci-cargo-git` -> `/home/runner/.cargo/git` - git dependencies.
- `ci-sccache` -> `/home/runner/.cache/sccache` - the sccache compile cache
  (sccache v0.18.0 is baked into the image and exported as `RUSTC_WRAPPER`).

Clear a cache by stopping the slots and running
`docker volume rm ci-cargo-registry ci-cargo-git ci-sccache`; the next job
rebuilds it from scratch. The supervisor additionally runs `docker pull` for
every image in the config's `"prepull"` array (for example `postgres:16`)
once at startup, so the first job of the day does not pay for a cold pull
either. Current runner image tag: `workst-runner:2.338.0-4`.

## CPU and disk

One box runs up to 16 ephemeral containers for 9 repositories, so without a
cap one repository's pytest starved the others (measured: gen-analysis pytest
32 min under load vs ~9 min hosted, plangraph 127 s -> 360-380 s). Operator
decision 2026-10-16, D-1425:

- **Per-slot CPU cap.** The supervisor passes `-Cpus <n>` to each loop, which
  becomes `docker run --cpus <n> --cpu-shares 1024` - the shares always match
  the cap, so a capped slot cannot outweigh its peers.
- **Cap precedence.** The repository entry's `"cpus"`, else the config's
  top-level `"cpus_default"` (8 in `slots.example.json`), else 8.
  `"cpus": 0` means *no cap*: the container may use every core, exactly as
  before D-1425 (the supervisor then omits `-Cpus`).
- **Disk floor.** Every 10 minutes the supervisor reads the Docker disk's free
  space (`docker run --rm --entrypoint df <image> -B1G --output=avail /`) and,
  below the config's `"disk_floor_gb"` (50 in the example), logs
  `disk floor: <free> GB < <floor> GB, pruning` and prunes.
- **What the prune removes - and never removes.** Only dangling images
  (`docker image prune -f`) and the build cache
  (`docker builder prune -f --keep-storage 20gb`). It never touches volumes
  (the `ci-cargo-*` and `ci-sccache` caches live there) and never removes
  tagged images such as `workst-runner:*` or the pre-pulled job images.

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
