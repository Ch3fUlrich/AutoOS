# OpenCode Fleet Configuration & Plugins

Fleet operational notes and configurations for OpenCode agent runners across this repository. This page currently documents the `bash-guard` plugin integration, ensuring execution-safety parity across Claude and OpenCode harnesses.

## bash-guard plugin

### Overview

`configuration/opencode/plugins/bash-guard/index.mjs` is an OpenCode v2 plugin that hooks into tool execution before execution (`execute.before`) for the `shell` and `bash` tools. It delegates inspection to the SAME guard core as the Claude hook (`tools/hooks/bash_guard.py`) with no second copy of the safety rules.

When the guard core exits with code 2, the plugin throws an error to refuse execution, presenting the guard's refusal message directly to the model. Every other outcome—such as a missing guard script, spawn error, non-zero unexpected exit code, or exceeding the 5 s process timeout—fails OPEN with a single `bash-guard:` log line on stderr, ensuring agent sessions are never blocked by harness infrastructure errors.

### OpenCode Configuration

OpenCode v2 loads a plugin from a DIRECTORY that holds `index.mjs` (measured on 2.0.12: a single file path in the config is
rejected with "configured plugin path must be a directory", and a v1-style plugin does not load). Two ways to load it:

1. Auto-discovery: put (or link) the directory at `<project>/.opencode/plugins/bash-guard/` so that
   `<project>/.opencode/plugins/bash-guard/index.mjs` exists; it is hot-reloaded within seconds when the file changes.
2. The config key `plugins` with the DIRECTORY as the entry:

```jsonc
{
  "plugins": [
    "configuration/opencode/plugins/bash-guard"
  ]
}
```

(The entry is a directory path; use an absolute path when the session's working directory is not the repo root. Check the
installed opencode version's config schema before changing the spelling.)

### Environment Variables

- `AUTOOS_REPO_ROOT`: Optional override pointing to the AutoOS repository root where `tools/hooks/bash_guard.py` resides. If unset, the repository root is derived automatically from the plugin file location.

### Mandatory Operational Rule

OpenCode L1 sessions MUST load the `bash-guard` plugin before they run unattended.

### How to Verify

1. Start a scratch session with the plugin enabled.
2. Ask the model to execute a shell command with an UNQUOTED heredoc containing a backticked command, such as:
   ```bash
   cat <<EOF
   `date`
   EOF
   ```
   The call must be refused and the refusal message displayed to the model.
3. Ask the model to execute a command with a QUOTED heredoc, such as:
   ```bash
   cat <<'EOF'
   `date`
   EOF
   ```
   The command must execute successfully.

### Known Limits

- **Fail-open design:** Missing dependencies, execution timeouts (> 5 s), or Python runtime errors fail open with a diagnostic stderr log line.
- **Tool scope:** Inspects only the `shell` and `bash` tools; calls via custom tools or external runners bypass this hook.
- **Rule set:** Currently covers unquoted heredocs with backticks/subshells and `claude --bg/-p/--print` nested calls. The kill-by-image-name rule is added to the core with FLEET-HOOKS v2.3 later.

## oc_l1.py launcher

`tools/oc_l1.py` starts ONE OpenCode orchestrator (an "L1") for ONE lane from a host-local config: one `opencode serve`
bound to 127.0.0.1, one session, relaunchable from a handoff card. It has three subcommands:

```bash
python3 tools/oc_l1.py render --name <lane> [--config PATH]   # write the scratch opencode config, nothing else
python3 tools/oc_l1.py start  --name <lane> [--config PATH]   # render, start the server, create the session, run the canary
python3 tools/oc_l1.py status --name <lane> [--config PATH]   # live | silent | dead
```

### Config

The config is host-local and never committed: `${XDG_CONFIG_HOME:-~/.config}/autoos/oc-l1.json` on POSIX,
`%LOCALAPPDATA%\autoos\oc-l1.json` on Windows (override with `--config`). Copy
`configuration/oc-l1.example.json` and fill in real values; every key is documented in its `_comment`. Important rules:

- `password_env` holds only the NAME of an environment variable. `start` refuses (exit 2, nothing started) when that variable
  is not set in the process environment; the value is never written to a file, a command line or the rendered config.
- `opencode_bin` is the explicit path of the npm `opencode` binary. It is never looked up on PATH and never the desktop app's
  background service.
- The server binds to 127.0.0.1 only (`--hostname 127.0.0.1`), on `serve_port` (default: a stable hash of the lane name in
  47200-47299).
- `render` writes a scratch config under `scratch_dir`; the user's own opencode config is never touched. Only the MCP servers
  listed in the lane's `mcp` are enabled; every other server of the repo `opencode.jsonc` is disabled.
- `plugins` lists plugin DIRECTORIES (each holding an `index.mjs`), for example the bash-guard plugin of the section above.
  `start` refuses a lane whose `plugins` list is empty (exit 2, before the server spawns): with no plugin nothing can deny the
  canary, so such a lane could only ever exit 5 (D-665). `render` alone still accepts an empty list.
- The scratch file uses opencode's OWN config schema (singular `provider`, models keyed by the gateway model id, a flat `mcp` map,
  `plugins`, `permission`, no `server` block), not the repo-file shape; `tests/test_oc_l1_render.py` pins it field by field.
- The `autoos-agent` MCP entry gets `AUTOOS_WORKERS_DIR` (lane key `workers_dir`, default `<cwd>/logs/workers`): without it the MCP
  tools run `git rev-parse` with an inherited stdin and, under opencode's stdio transport, the first `ps` call hangs.

### start, relaunch and the canary

`start` renders the config, starts the server with scratch XDG directories (so no other tool's skills load), waits for health,
creates the pilot session and writes the state file atomically (mode 0600 on POSIX). It then runs the bash-guard CANARY: a
second throwaway session on the same server is asked to run a command that the guard must refuse (an unquoted heredoc with a
backticked command). The result `{denied, ts, plugin_path, session_id}` is stored in the state file and merged into
`heartbeat.json`. Only after a DENIED canary does `start` post the pilot's first prompt (the handoff head plus a hint line about
MCP tools): a pilot that is not known to be guarded never runs.

- Canary DENIED: the first prompt is posted, exit 0, the L1 may run unattended.
- Canary NOT denied (the command ran, no tool call happened, timeout, error): NO prompt is posted, the pilot session stays idle,
  the server stays up for supervised use, `start` prints `UNATTENDED-REFUSED` and exits 5. The plugin fails open on purpose, so
  an unproven guard means no unattended run.
- A second `start` on a live session posts nothing new and prints the stored canary line; if that stored canary was not denied
  it prints `UNATTENDED-REFUSED` and exits 5 again (it never reports a refused start as live). A `start` that finds the server
  dead is a RELAUNCH: it starts a new server and runs the canary again, and `heartbeat.json` gets the new result.
- The server child inherits the launcher's FULL environment (opencode, `uv` and the MCP servers need PATH, the profile
  directories and the gateway variables); the launcher adds the XDG isolation and the Basic password. Run the launcher from a
  shell that holds only what the pilot may see.

Exit codes of `start`: 0 started or already live, 2 config or validation error (password env unset, empty `plugins`), 4 health
timeout (the child is killed by its recorded PID), 5 canary not denied.

### status

`status` asks the server (v2 API, Basic auth from the same environment variable): `live` (exit 0), `silent` (exit 1: alive, but
no new assistant message or tool item for `silent_minutes`, default 10) or `dead` (exit 2: no answer, or the session ended as
failed or interrupted).

### Templates

- `configuration/oc-l1/autoos-oc-l1.service`: example systemd user unit that runs `start`.
- `configuration/oc-l1/windows-task.md`: the Windows Scheduled Task equivalent.
