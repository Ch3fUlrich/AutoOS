# CLAUDE.md

The working rules for this repository live in **[AGENTS.md](AGENTS.md)**. Read that file first;
it is the canonical instruction set for every agent, not a summary of one.

Quick orientation:

| I want to... | Go to |
|---|---|
| Add software to the menu | `catalog/windows.json` or `catalog/linux.json` — data only, no code |
| Change how something installs | `lib/windows/*.psm1` or `lib/linux/*.sh` |
| Change the menu or colours | `lib/windows/AutoOS.Ui.psm1` / `lib/linux/ui.sh` |
| Provision a *remote* machine | `Windows/ansible/` — not used by the local entry points |
| Run the tests | `pwsh tests/run-tests.ps1` / `bash tests/run-tests.sh` |

Three rules that override anything else you might infer from the code:

1. This repository is **public**. Never commit a credential, hostname or vendor binary.
2. Never overwrite a user's PATH, shell profile or config wholesale — read, append, write back.
3. Everything must be safe to run **twice**. A second run reports `skipped`, never `installed`.

## MCP scope trap (omnigraph)

A same-named server in user scope (`~/.claude.json`) **silently overrides**
this repo's `.mcp.json` — nothing errors, the bridge just answers about the
wrong graph. This is measured for `omnigraph` only; a duplicate `serena` entry
instead spawns a second process (also wrong, differently). Rule: never define
the same server in both scopes.

Cheap detector — the graph must know itself as this repo:

```gq
query whoami() { match { $p: Project } return { $p.slug, $p.repository } }
```

`repository` must equal `git remote get-url origin`. If a recall looks empty
(`0 rows except 2 Preferences`), suspect the trap before suspecting a wipe.

## Skills

Project skills live in `.agents/skills/` (single home; see `AGENTS.md` §8).
Claude Code reads `.claude/skills/` — the installers link each skill there,
so never copy a skill into both places by hand.
