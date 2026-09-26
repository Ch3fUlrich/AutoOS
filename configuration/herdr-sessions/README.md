# herdr-sessions

Bring the Claude Code sessions that were live before a shutdown back after
boot: same panes, same directories, same conversations, **never compacted**.
One driver (`herdr-sessions.sh`) plus one host-specific *profile* — no host
carries its own copy of the logic.

| Item | Value |
|---|---|
| Entrypoint | `herdr-sessions.sh {update,relaunch,snapshot,restore,status} --profile profiles/<p>.conf [--dry-run]` |
| Install | `install.sh --profile <name> [--dry-run]` — renders and installs that host's systemd units from `profiles/<name>.conf` |
| New host | copy [`profiles/example.conf`](./profiles/example.conf) to `profiles/<your-host>.conf`, edit the paths, set `HS_SCOPE` (`user` or `system`), `./install.sh --profile <your-host> --dry-run` |
| Profiles | [`profiles/example.conf`](./profiles/example.conf) — the only profile this repository ships. Site-specific profiles (host paths, display names) are kept in each site's own private configuration, never here, and are passed as `--profile <path-to-that-file>` |

## The algorithm (one, for every host)

0. **update** (at boot, before `herdr-server`) — `herdr update` then `claude
   update`, each bounded by `UPDATE_TIMEOUT_SEC`, never fatal. `herdr update`
   refuses from inside a herdr pane and `claude update` only swaps the
   versions symlink, so a long-lived session picks up neither until a boot
   does this for it. `UPDATE_ON_BOOT=0` in the profile disables it.
   Mid-uptime, run `update` by hand; when `claude update` moved the symlink
   under a live session it hands off to **relaunch**.
0b. **relaunch** — restart, *in place and without a reboot*, every session
   whose `/proc/<pid>/exe` is not the current `claude`: fresh snapshot,
   SIGTERM, wait for the pane to free, then the normal **restore** of the same
   UUID. Idempotent. Always runs detached (`systemd-run`), because the session
   being restarted is usually the one that asked for it.
1. **snapshot** (timer, every 5 min) — record each live session: pane, cwd,
   session UUID, display name, whether `--rc` was on.
2. **restore** (at boot) — for each recorded session, relaunch it in *its*
   pane with `claude --resume <uuid>`, then answer the resume prompt
   `2. Resume full session as-is` (never the "recommended" summary).
3. **fallback** — if the snapshot has nothing usable and the profile allows
   it (`FALLBACK=continue`), start one agent with `claude --continue`.

A profile declares its shape with `HS_SCOPE`. `HS_SCOPE=user` — sessions run
as a normal login user, so the units are `--user` units under
`~/.config/systemd/user` and need `loginctl enable-linger`. `HS_SCOPE=system` —
sessions run as root with no login session at boot, so the units are system
units under `/etc/systemd/system`.

`install.sh` renders the matching `systemd/{user,system}/*.{service,timer}`
template, substituting three tokens: `@PROFILE@` (the profile name), `@APPDIR@`
(wherever this checkout actually is — no unit file hard-codes an install path)
and `@WORKDIR@` (the profile's `HS_WORKDIR`, the cwd every pane inherits).

## Test

```bash
bash tests/test_smoke.sh
```

`tests/test_smoke.sh` runs `bash -n` on every script, `python3 -m
py_compile` on every `lib/*.py`, and `install.sh --profile X --dry-run` for
every profile shipped here (just `example` in this repository). This
component's tests also run from the AutoOS test harness: `bash
tests/run-tests.sh --filter=herdr` (repository root).

## Design decisions worth keeping

Never compact a resumed session; never overwrite a good snapshot with an
empty one (a timer firing mid-boot is not "the user closed everything");
systemd must own `herdr`, never a Claude session (else transcript saving
silently disables); a guard (`ExecCondition`) stops `herdr-server.service`
looping when a hand-started `herdr` already holds the socket. Full rationale
lives in the comments at the top of `herdr-sessions.sh` and each unit file.
