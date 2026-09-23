# Troubleshooting

> **Note:** This is a detailed/legacy reference. The authoritative current
> overview is [`../../../docs/architecture.md`](../../../docs/architecture.md);
> the memory layer is **Omnigraph**.


## Common Issues

### Docker services won't start

**Symptom**: `docker compose up -d` fails, or omnigraph-server/MinIO unreachable.

```powershell
# Check Docker is running
docker info

# Check container status
docker compose ps

# Check logs
docker compose --env-file .env.shared --env-file .env.server --env-file ~/.config/autoos/site.env -f docker-compose.server.yml logs omnigraph-server minio
docker compose logs ollama

# Full restart
docker compose down
docker compose up -d
```

**Port conflicts**: If ports 6333 or 11434 are in use:
```powershell
# Check what's using the ports
netstat -ano | findstr :6333
netstat -ano | findstr :11434
```

### nomic-embed-text model not pulled

**Symptom**: Ollama returns errors about a missing model, or `nearest()` ranking looks
random. The graph stores `Vector(768)` from `nomic-embed-text`; `bge-m3` (Mem0's old
embedder, 1024-dim) is **not** this graph's — mixing dimensions returns garbage.
Embeddings are optional: without Ollama, recall falls back to traversal + full-text.

```powershell
ollama list                  # check available models
ollama pull nomic-embed-text # pull it
```

### Serena not on PATH

**Symptom**: `serena` command not found after `uv tool install serena-agent`.

```powershell
# uv tools are installed to ~/.local/bin or ~/.cargo/bin
# Add to PATH:
$env:Path += ";$env:USERPROFILE\.local\bin"
$env:Path += ";$env:USERPROFILE\.cargo\bin"

# Or use full path in mcp.json
uv tool dir  # Shows where tools are installed
```

### Serena times out on first launch

**Symptom**: your coding agent shows "MCP server serena connection timeout".

Serena needs to download language servers on first use. Give it more time:

In `~/.codewhale/mcp.json`, increase `connect_timeout`:
```json
"serena": {
  "connect_timeout": 120,   // Was 60
  "execute_timeout": 180,   // Was 120
  ...
}
```

### Serena language server crashes on activation

**Symptom**: Activating a project succeeds, but any symbol query (e.g.
`get_symbols_overview`) fails with `LanguageServerTerminatedException` /
"language server manager is not initialized". Serena's own tool output
tells you to stop and not attempt workarounds -- that's a real instruction,
not boilerplate; report the exact error before retrying anything.

**Root cause**: a language server binary the project's `languages:` list
requires isn't installed. Some backends (Pyright for `python`, via `uvx`)
are auto-bootstrapped by Serena and just work. Others (`jedi-language-server`
for `python_jedi`) are a bare PATH lookup with no auto-install.

```powershell
# Confirm the binary is actually missing
where jedi-language-server

# Fix 1 (recommended): switch the repo's .serena/project.yml to `python`
# instead of `python_jedi` -- Pyright needs no manual install.

# Fix 2: install the missing binary directly
uv tool install jedi-language-server
```

After either fix, the current session's language server instance for that
repo is still in a failed state -- reactivate the project (or restart the
client) to pick it up.

### `activate_project` not available / session stuck on one repo

**Symptom**: `activate_project` doesn't appear in the tool list, or a call
to it fails with `Tool 'activate_project' is not active`.

**Two distinct causes**:

1. **Serena is running in `--project-from-cwd` auto-pin mode.** Check
   *both* `.mcp.json` (project scope, in the repo root) and `~/.claude.json`
   (Claude Code's user scope, under the top-level `mcpServers` key) for a
   `serena` entry with `--project-from-cwd` in `args`. Both must be fixed
   if both define the server -- whichever one is actually governing the
   session may not match naive scope-precedence assumptions. **A full
   client restart is required after editing** -- reconnecting the MCP
   server alone can reuse the args resolved at session start.

2. **The active repo's own `.serena/project.yml` excludes
   `activate_project`/`get_current_config`.** This is fixed by editing that
   repo's `excluded_tools` list, but the fix only applies on the *next*
   activation -- the current session is already stranded and needs a
   restart. See `config/serena-project.yml` and
   docs/INSTALL-GUIDE.md#two-pitfalls-that-will-strand-a-session.

### your coding agent doesn't show MCP tools

**Symptom**: After starting your coding agent, MCP tools don't appear.

```powershell
# Check MCP config location
codewhale-tui doctor

# Validate MCP config
codewhale-tui mcp validate

# List configured servers
codewhale-tui mcp list

# Force reload (inside TUI)
/mcp reload
```

If the config file doesn't exist:
```powershell
# Copy the template
copy config\mcp.json $env:USERPROFILE\.codewhale\mcp.json
```

### Ollama uses CPU instead of GPU

**Symptom**: Slow embeddings, high CPU usage during embedding operations.

On Windows with NVIDIA GPU:
```powershell
# Check if NVIDIA container toolkit is available
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi

# If GPU not detected, install NVIDIA Container Toolkit:
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

# Fallback: Remove GPU requirement from docker-compose.yml
# Delete the deploy.resources section under ollama service
```

### Superpowers MCP server not available

**Symptom**: `uvx` can't find superpowers-mcp.

```powershell
# Install explicitly
uv tool install --from git+https://github.com/erophames/superpowers-mcp superpowers-mcp

# Or run from source
git clone https://github.com/erophames/superpowers-mcp $env:TEMP\superpowers-mcp
cd $env:TEMP\superpowers-mcp
uv pip install -e .
```

### Too many MCP tools cluttering context

**Symptom**: Agent's context window fills with MCP tool descriptions.

In `~/.codewhale/mcp.json`, you can limit which tools are exposed:
```json
"serena": {
  "enabled_tools": ["find_symbol", "find_references", "get_file_structure"],
  ...
}
```

This only exposes the tools you actually use. List all available tools with:
```powershell
codewhale-tui mcp tools serena
```

## Diagnostic Commands

Run these when something seems wrong:

```powershell
# Server health
curl -fsS http://localhost:8080/healthz

# Docker health
docker compose ps
docker compose logs --tail=50

# your coding agent MCP status
codewhale-tui doctor
codewhale-tui mcp validate
codewhale-tui mcp list

# Ollama model check
curl http://localhost:11434/api/tags

# Omnigraph graphs
curl http://localhost:6333/collections | ConvertFrom-Json

# Serena check
serena --version
serena project health-check --project ${CODE_ROOT}
```

## Complete Reset

If everything is broken and you want to start fresh:

```powershell
# Stop and remove containers
cd ${AGENT_SKILLS_ROOT}/infra/mcp-servers
docker compose --env-file .env.shared --env-file .env.server `
  --env-file $HOME/.config/autoos/site.env `
  -f docker-compose.server.yml down

# Remove data (warning: deletes all memories and indices)
# NOTE: MinIO data is a bind mount (./data/minio) and survives `down -v` —
# it has to be deleted explicitly.
Remove-Item -Recurse -Force .\data

# Remove installed tools
uv tool uninstall serena-agent
uv tool uninstall superpowers-mcp

# Remove your coding agent MCP config
Remove-Item $env:USERPROFILE\.codewhale\mcp.json

# Bring the stack back up
docker compose --env-file .env.shared --env-file .env.server `
  --env-file $HOME/.config/autoos/site.env `
  -f docker-compose.server.yml up -d
curl -fsS http://localhost:8080/healthz
```
