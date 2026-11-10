#!/usr/bin/env bash
# AutoOS: Raspberry Pi 5 Moonlight Console & Coding Agents Setup
# Provisions a Raspberry Pi 5 as a low-latency Moonlight streaming console
# with AntiMicroX gamepad mapping, Kitty GPU terminal, and optional coding agents.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-rpi5-moonlight.sh [OPTIONS]

Options:
  --all               Install full stack: Moonlight, gamepad mapper, Kitty terminal,
                      Zsh environment, and all coding agents (Claude, agy, OpenCode, Pi).
  --no-agents         Install Moonlight gaming console stack only (no AI coding agents).
  --with-agents       Alias for --all.
  --dry-run           Preview actions without touching the system.
  -h, --help          Show this help text.

Examples:
  ./scripts/setup-rpi5-moonlight.sh --all
  ./scripts/setup-rpi5-moonlight.sh --no-agents
  ./scripts/setup-rpi5-moonlight.sh --dry-run
EOF
    exit 0
}

DRY_RUN=0
MODE="all"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all|--with-agents) MODE="all"; shift ;;
        --no-agents)        MODE="no-agents"; shift ;;
        --dry-run)          DRY_RUN=1; EXTRA_ARGS+=(--dry-run); shift ;;
        -h|--help)          usage ;;
        *)                  EXTRA_ARGS+=("$1"); shift ;;
    esac
done

echo "=========================================================="
echo " AutoOS: Raspberry Pi 5 Moonlight & Gaming Console Setup"
echo "=========================================================="
echo

if [[ -f /proc/device-tree/model ]]; then
    model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
    echo "Detected hardware: ${model}"
fi

if [[ "$MODE" == "no-agents" ]]; then
    echo "Profile: Moonlight streaming, CEC TV automation, gamepad support, Kitty & Zsh (no AI agents)"
    exec bash "$ROOT/setup.sh" --only moonlight,antimicrox,rpi-cec-tv,rpi-moonlight-console,kitty,jetbrains-mono-nerd-font,lsd,bat,zsh,oh-my-zsh,zsh-plugins,powerlevel10k,tailscale,gh "${EXTRA_ARGS[@]}"
else
    echo "Profile: Full stack with Moonlight console, CEC TV automation, gamepad support & coding agents (Claude, agy, OpenCode, Pi)"
    exec bash "$ROOT/setup.sh" --profile rpi-moonlight "${EXTRA_ARGS[@]}"
fi
