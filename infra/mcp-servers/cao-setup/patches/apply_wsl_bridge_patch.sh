#!/usr/bin/env bash
# apply_wsl_bridge_patch.sh
# Copies the patched antigravity_cli.py (with WSL-to-Windows MCP bridge) over the
# stock cli-agent-orchestrator install in the active Python environment.
#
# Run this whenever cli-agent-orchestrator is updated (pip install --upgrade).
# Re-running is idempotent: the patched file is always written from the repo copy.
#
# Usage:
#   bash infra/mcp-servers/cao-setup/patches/apply_wsl_bridge_patch.sh
#
# Requirements:
#   - python3 / pip accessible in $PATH
#   - cli-agent-orchestrator already installed (pip install cli-agent-orchestrator)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="$SCRIPT_DIR/antigravity_cli.py"

# Version gate. This patch overwrites the installed provider module with a
# whole-file copy, so it can silently drop upstream changes if the installed
# cli-agent-orchestrator is not the version the copy was taken from. The exact
# "known-good" version is not recorded in this tree, so we surface the detected
# version and require an explicit confirmation before clobbering — never a bare
# overwrite on a guess. Detect it the same way ../setup-cao.sh does.
INSTALLED_VERSION="$(python3 -c \
  'import importlib.metadata as m; print(m.version("cli-agent-orchestrator"))' \
  2>/dev/null || echo "unknown")"

if [[ "$INSTALLED_VERSION" == "unknown" ]]; then
  echo "[WARN] cli-agent-orchestrator is not importable — is it installed?"
  echo "[WARN] (pip install --user cli-agent-orchestrator, then re-run.)"
fi
echo "[INFO] cli-agent-orchestrator version: $INSTALLED_VERSION"

if [[ "${AUTOOS_YES:-0}" == "1" ]]; then
  echo "[INFO] AUTOOS_YES=1 — applying patch without prompting."
elif [[ ! -t 0 ]]; then
  echo "[ERROR] Refusing to overwrite a whole module without confirmation (no TTY)."
  echo "[ERROR] Re-run interactively, or set AUTOOS_YES=1 once you have verified the version."
  exit 1
else
  read -r -p "Overwrite the installed module (version ${INSTALLED_VERSION})? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "[INFO] Aborted — nothing was changed."; exit 1; }
fi

# Resolve the installed package path
INSTALLED_PATH="$(python3 -c "
import cli_agent_orchestrator.providers.antigravity_cli as m
import inspect, os
print(os.path.abspath(inspect.getfile(m)))
")"

if [[ -z "$INSTALLED_PATH" ]]; then
  echo "[ERROR] Could not locate installed antigravity_cli.py. Is cli-agent-orchestrator installed?"
  exit 1
fi

echo "[INFO] Installed:  $INSTALLED_PATH"
echo "[INFO] Patch file: $PATCH_FILE"

# Backup the original (only if a backup does not already exist)
BACKUP="${INSTALLED_PATH}.orig"
if [[ ! -f "$BACKUP" ]]; then
  cp "$INSTALLED_PATH" "$BACKUP"
  echo "[INFO] Backup created: $BACKUP"
else
  echo "[INFO] Backup already exists: $BACKUP (skipping)"
fi

# Apply patch
cp "$PATCH_FILE" "$INSTALLED_PATH"
echo "[OK]  WSL bridge patch applied to $INSTALLED_PATH"

# Quick sanity check: confirm the bridge keyword is present
if grep -q "WSL to Windows cross-environment bridge" "$INSTALLED_PATH"; then
  echo "[OK]  Patch verified (bridge comment found)"
else
  echo "[WARN] Patch comment not found — please inspect $INSTALLED_PATH"
fi
