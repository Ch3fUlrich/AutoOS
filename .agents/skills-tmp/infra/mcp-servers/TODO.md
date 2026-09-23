# Claude Code MCP Server Migration — TODO

> **Current state**: Claude Code uses plugins (`serena@claude-plugins-official`,
> `superpowers@claude-plugins-official`, `context7@claude-plugins-official`,
> `cq@cq`). The new self-hosted MCP servers are running for your coding agent but
> Claude Code still uses its own plugin system.

## Why Migrate?

- **Shared state**: Both agents (your coding agent + Claude Code) share the same
  Omnigraph memory and Serena indices. No duplication.
- **Self-hosted**: No dependency on Claude's plugin marketplace.
- **Consistent tools**: Same MCP tools, same behavior across agents.
- **Privacy**: All data stays on your machine.

## Migration Phases

### Phase 1: Run Both Systems Side-by-Side (NOW)

- [x] MCP servers installed and running (Serena, Omnigraph, Superpowers)
- [x] Docker services running (omnigraph-server + MinIO; Ollama optional for embeddings)
- [x] your coding agent configured with `~/.codewhale/mcp.json`
- [ ] Claude Code **still using plugins** for now
- [ ] Verify Claude Code plugins continue to work alongside the new setup
- [ ] Use your coding agent for a week — confirm MCP tools work as expected

**Goal**: Prove the new stack works before switching Claude Code.

### Phase 2: Bootstrap Knowledge Transfer

- [ ] Agent has built up knowledge in Omnigraph through your coding agent usage
- [ ] Serena indices exist for frequently-used repositories
- [ ] Superpowers skills are tested via your coding agent

**Goal**: The new system has enough context that Claude Code won't lose anything
by switching.

### Phase 3: Export Claude Code Plugin Data

- [ ] Export Claude Code conversation history for Omnigraph import
  - Location: `~/.claude/` or `%APPDATA%/Claude/`
  - Relevant conversations can be summarized and stored in Omnigraph
- [ ] Verify Serena indices under `.serena/` are identical (shared by both)
- [ ] Note any Superpowers skill customizations made in Claude Code
- [ ] Export context7 data if available locally

**Goal**: Capture any knowledge unique to the Claude Code plugin system.

### Phase 4: Switch Claude Code to MCP Servers

- [ ] Backup current Claude Code settings:
  ```
  copy %USERPROFILE%\.claude\settings.json %USERPROFILE%\.claude\settings.json.backup
  ```
- [ ] Disable plugins in `~/.claude/settings.json`:
  ```json
  "enabledPlugins": {
    "serena@claude-plugins-official": false,
    "superpowers@claude-plugins-official": false,
    "context7@claude-plugins-official": false,
    "cq@cq": false
  }
  ```
- [ ] Add MCP server config for Claude Code:
  - Option A: Copy `config/mcp-claude-code.json` to `~/.claude/mcp.json`
  - Option B: Use Claude Code's `/mcp add` commands:
    ```
    /mcp add serena uvx --from serena-agent serena start-mcp-server --context=claude-code
    /mcp add superpowers uvx --from git+https://github.com/erophames/superpowers-mcp superpowers-mcp
    ```
    (`omnigraph` needs `OMNIGRAPH_BASE_URL`/`TOKEN`/`GRAPH_ID` in its `env` —
    use Option A, or see `servers/omnigraph/README.md#register-the-mcp-bridge`.)
- [ ] Restart Claude Code
- [ ] Test: `mcp_serena_find_symbol`, `mcp_omnigraph_query`, `mcp_superpowers_*`
- [ ] Rollback if broken: restore `settings.json.backup`

**Goal**: Claude Code uses the same MCP servers as your coding agent.

### Phase 5: Cleanup (After 2+ Weeks of Stable Operation)

- [ ] Remove unused Claude Code plugin data:
  - `~/.claude/plugins/` (or wherever Claude stores plugin cache)
  - Remove plugin entries from `settings.json`
- [ ] Remove `context7` and `cq` if no longer needed
- [ ] Archive old settings backup

**Goal**: Clean system, single MCP stack for both agents.

## Rollback Plan

If anything breaks:

```powershell
# Restore Claude Code settings
copy $env:USERPROFILE\.claude\settings.json.backup $env:USERPROFILE\.claude\settings.json

# Restart Claude Code — plugins re-activate
```

The MCP servers (Docker, uv) can keep running — they don't interfere with
Claude Code's plugin system when not connected.

## Verification Checklist (After Migration)

- [ ] `mcp_serena_find_symbol` returns correct results
- [ ] `mcp_serena_find_references` finds all usages
- [ ] `mcp_omnigraph_mutate` stores new memories
- [ ] `mcp_omnigraph_query` retrieves stored memories
- [ ] `mcp_superpowers_*` skills work as before
- [ ] Same tools available in both your coding agent and Claude Code
- [ ] Token usage is measurably lower than before (track with /cost)

## Notes

- The `context7` and `cq` plugins are Claude Code-specific and don't have
  MCP equivalents yet. Keep them enabled if you use them.
- Serena's project indices are shared between the plugin and the CLI —
  no data loss when switching.
- Omnigraph is a **new capability** — Claude Code didn't have persistent memory
  before. This is an upgrade, not a replacement.
