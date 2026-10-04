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
