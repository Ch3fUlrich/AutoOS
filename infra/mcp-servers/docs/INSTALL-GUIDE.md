## Serena Project Management

> **Note:** This is a detailed/legacy reference. The authoritative current
> overview is [`../../../docs/architecture.md`](../../../docs/architecture.md);
> the memory layer is **Omnigraph**.


### Where Indices Are Stored

Serena stores data in two places:

| Location | Content |
|----------|---------|
| **Per-repo** `.serena/project.yml` | Project language, workspace folders, indexing settings |
| **Per-repo** `.serena/` cache | LSP language server data, symbol cache |
| **Global** `~/.serena/serena_config.yml` | Excluded tools, registered projects list, global timeouts |

Currently **9 repos** are indexed with Serena LSP support.

### Adding a New Repository

When you create a new repo under `${CODE_ROOT}`, add it to Serena:

```powershell
# Method 1: Direct command
cd ${CODE_ROOT}
("N`n" * 10) | serena project create "${CODE_ROOT}\NewRepo" --index

# Method 2: Via the initialization script (indexes ALL unindexed repos)
cd ${AGENT_SKILLS_ROOT}/infra/mcp-servers
.\windows\init-serena-projects.ps1
```

The `("N`n" * 10)` pipes "No" answers for any language detection prompts.
The `--index` flag triggers immediate LSP indexing. Indexing takes 10-60 seconds
depending on repo size.

### How the Agent Uses Serena

Once a project is indexed, any your coding agent session can use it. This assumes
Serena is running in multi-project mode (the current default -- see
`../../../docs/architecture.md`); if it's set up with
`--project-from-cwd` instead, `activate_project` isn't exposed and the
session is pinned to whichever repo it started in.

```
# Step 1: Activate the project (once per session)
mcp_serena_activate_project(project="DeepLabCut")

# Step 2: Navigate code symbolically
mcp_serena_find_symbol(name_path_pattern="train_model")
mcp_serena_find_references(name_path_pattern="train_model", relative_path="src/")
mcp_serena_get_symbols_overview(relative_path="src/models.py")

# Step 3: Store project-level notes (persistent across sessions)
mcp_serena_write_memory(memory_name="training-pipeline", content="Uses PyTorch Lightning with custom callbacks")
mcp_serena_read_memory(memory_name="training-pipeline")
```

The agent skill at `skills/mcp-servers-setup/SKILL.md` contains the full
workflow instructions that agents follow automatically.

### How Graphify Fits In

Graphify is the graph layer, not the symbol-navigation layer. It does not need
Serena-style activation, but it does need a graph build.

Use `scripts/windows/init-graphify-projects.ps1` to generate or refresh
`graphify-out/graph.json` for a repository. Once that file exists, the MCP
server can answer graph-level questions without rereading the whole codebase.

Recommended order for a new repo:

1. Activate Serena and run onboarding.
2. Build the Graphify graph.
3. Register the `graphify` server into Claude Code's own config:
   `scripts/windows/register-claude-code-mcp.ps1 -Server graphify`, then
   restart Claude Code (MCP servers only load at session start).
4. Use Serena for symbols and Graphify for broader relationship queries.

Building a graph against a local Ollama backend has several sharp edges
(missing extras, a too-short default timeout, three graphify bugs triggered
by noisy local-model JSON) that cost real debugging time to work out — see
`../README.md#graphify--local-ollama--known-gotchas` before assuming
something is broken. `init-graphify-projects.ps1` already applies every
workaround; this is background for when you need to change that script or
diagnose a new failure mode.

### Two Pitfalls That Will Strand a Session

**Don't exclude `activate_project`/`get_current_config` in a repo's own
`.serena/project.yml`.** Serena computes a session's active toolset at the
moment a project is activated. If that repo's `excluded_tools` hides those
two tools, you lose the ability to switch to another project for the rest
of the session -- editing the file afterward doesn't help, since the
toolset isn't recomputed until the *next* activation. The only way out is
restarting the client. This is fine (even intentional) in
`--project-from-cwd` auto-pin mode, since there's nothing to switch to
anyway -- but it's a footgun in multi-project mode.

**Prefer the `python` (Pyright) language backend over `python_jedi`.**
Serena bootstraps Pyright itself via `uvx` on first use. `python_jedi` does
a bare PATH lookup for a separately-installed `jedi-language-server`
binary with no auto-install -- if it's missing, the whole project's LSP
manager fails to initialize (not just Python queries), surfacing as an
opaque `LanguageServerTerminatedException`. Install it explicitly with
`uv tool install jedi-language-server` only if you have a specific reason
to avoid Pyright.
