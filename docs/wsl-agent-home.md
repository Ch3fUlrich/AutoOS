# WSL native agent home (`wsl-agent-home`)

On WSL2 the Windows drives (`/mnt/c`, 9p/drvfs) do **not** support FIFOs or
AF_UNIX sockets. Anything that creates named pipes or sockets must live on
the native ext4 filesystem instead.

## Why this exists

`cli-agent-orchestrator` streams tmux output through named pipes in
`FIFO_DIR` (`os.mkfifo`). When its home directory sits under a drvfs path —
typically `~/.aws` symlinked to `/mnt/c/Users/<you>/.aws`, which makes
`~/.aws/cli-agent-orchestrator` land on 9p — every session launch fails with:

```text
Failed to create session: [Errno 95] Operation not supported
```

(Errno 95 = `EOPNOTSUPP` from `mkfifo(2)` on drvfs. Diagnosed 2026-09-14.)

## What the component does

The `wsl-agent-home` catalog component (Linux only, no-op off WSL) runs
`setup_wsl_agent_home` as its `postInstall`:

1. Probes the legacy home `~/.aws/cli-agent-orchestrator` with a real
   `mkfifo` — no guessing from mount tables.
2. If the probe fails: timestamped backup (`*.backup-<ts>`), then copies live
   state (`agent-context/`, `agent-store/`, `db/`, `workflows/`, `skills/`,
   `profiles/`, `settings.json`) to native `~/.cao`. Logs and locks stay
   behind; the legacy dir is left in place (empty) so nothing dangles.
3. Exports `CAO_HOME_DIR="$HOME/.cao"` idempotently in `~/.bashrc` and
   `~/.profile` (marker-guarded append with backup, per repo convention).
   `constants.py` reads `CAO_HOME_DIR` at import, so **one** variable relocates
   the whole tree for `cao-server`, `cao` CLI and `cao-ops-mcp-server` alike.
4. Second runs report `skipped` (`.cao` already populated).

`cao-server` must be (re)started **after** the export so it picks up the new
home; the MCP registration in `setup_openhands_config` already exports the
same variable, so both sides agree.

## Layout after migration

| Path | Filesystem | Contents |
|---|---|---|
| `~/.cao` (`$CAO_HOME_DIR`) | ext4 | live CAO state: db, fifos, agent-store, profiles |
| `~/.aws/cli-agent-orchestrator[.backup-*]` | drvfs | legacy / backups only, never used at runtime |
| `~/code/<repo>` | ext4 | native checkouts agents mutate (same reasoning) |
| Windows checkout (e.g. `Documents/Code`) | NTFS | kept for `setup.ps1` / pwsh tests, which need real Windows |

Edit native files from Windows via `\\wsl$\<distro>\home\<user>\code`
(VS Code Remote-WSL works too).
