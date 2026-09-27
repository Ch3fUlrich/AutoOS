# CAO Setup — CLI Agent Orchestrator

This directory contains the setup scripts and patches for the **CLI Agent Orchestrator (CAO)** — an interactive hierarchy of agent terminals with a web dashboard for work you want to watch and steer across providers.

See the main [unattended-orchestration skill](../../../.agents/skills/unattended-orchestration/SKILL.md) for CAO usage, and [`references/cao-runbook.md`](../../../.agents/skills/unattended-orchestration/references/cao-runbook.md) for the full runbook.

## Contents

- `setup-cao.sh` — Linux/macOS/WSL installer for CAO server + CLI
- `setup-cao.ps1` — Windows installer for CAO server + CLI
- `patches/antigravity_cli.py` — Antigravity (agy) provider patch for CAO
- `patches/apply_wsl_bridge_patch.sh` — WSL bridge helper for cross-environment MCP

## Quick Start

```bash
# Linux/macOS/WSL
cd infra/mcp-servers/cao-setup
./setup-cao.sh

# Windows (PowerShell)
cd infra\mcp-servers\cao-setup
.\setup-cao.ps1
```

After installation:
```bash
cao-server &          # once per host; nothing below works without it
python -m cao check   # config, credentials, server, warnings
python -m cao probe   # do the pools ANSWER? closes proven-dead ones
python -m cao plan    # scaffold the plan WITH THE USER; ships invalid
python -m cao launch --phase p1  # refuses without a valid plan
python -m cao sweep --answer     # unblock waiting agents; run every few minutes
python -m cao verify --phase p1 --terminal <id>  # YOU run the guard
python -m cao resume --apply     # after a crash or usage limit
```

## Patches

The `patches/` directory contains:

1. **`antigravity_cli.py`** — CAO provider for Google's Antigravity CLI (`agy`). This is a vendored copy of the provider from the `cli-agent-orchestrator` package. See the file header for version and provenance.

2. **`apply_wsl_bridge_patch.sh`** — Helper to apply WSL-to-Windows bridge patches for MCP servers running across the WSL boundary.