#!/usr/bin/env bash
# Resume the AutoOS AI stack: gateway + litellm + opencode serve + OpenHands.
#
# Run by the autoos-stack systemd user unit (register-autostart.sh), by
# healthcheck.sh --fix, or by hand after a reboot. Each service that has a
# registered unit is started THROUGH its unit (so systemd owns and restarts
# it); one without a unit falls back to a background start. Safe to run
# twice: every probe below no-ops when the service is already up, and nothing
# here ever deletes or overwrites.
#
#   ./configuration/autostart/Start-AutoOSStack.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/autoos"
GATEWAY="http://127.0.0.1:20128"

# unit_installed <name>: register-autostart.sh put the unit in place.
unit_installed() {
    command -v systemctl >/dev/null 2>&1 && systemctl --user cat "$1.service" >/dev/null 2>&1
}

# wait_for <probe-function>: up to two minutes.
wait_for() {
    local _
    for _ in $(seq 1 24); do "$1" && return 0; sleep 5; done
    "$1"
}

gateway_ok() { curl -sf -m 5 "$GATEWAY/api/health" >/dev/null 2>&1; }
litellm_ok() { curl -sf -m 5 -o /dev/null "http://127.0.0.1:4000/" 2>/dev/null; }
serve_ok() {
    local code
    code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:4096/" 2>/dev/null || true)"
    # V2 answers its UI with 200; /api/* is the part behind the password.
    [[ "$code" == "200" || "$code" == "401" ]]
}

mkdir -p "$STATE_DIR"

# 0. The docker AI stack (server profile, ai-stack.sh migrate) owns the
#    gateway, opencode serve and OpenHands: resume it (a no-op for running
#    containers) and skip the native branches for those three.
AI_STACK="$ROOT/configuration/docker/ai-stack/ai-stack.sh"
IN_DOCKER=0
if bash "$AI_STACK" is-active >/dev/null 2>&1; then
    IN_DOCKER=1
    echo "Docker AI stack owns the gateway, opencode serve and OpenHands - resuming it."
    bash "$AI_STACK" up || echo "Docker AI stack did not fully come up - see: $AI_STACK status"
fi

# 1. Gateway first: everything else routes through it.
if [[ $IN_DOCKER -eq 1 ]]; then
    :
elif gateway_ok; then
    echo "Gateway already up on 20128 - nothing to do."
elif unit_installed autoos-omniroute; then
    echo "Starting the autoos-omniroute unit..."
    systemctl --user start autoos-omniroute.service || true
    if wait_for gateway_ok; then echo "Gateway OK on 20128."
    else echo "Gateway did not answer - see: journalctl --user -u autoos-omniroute"; fi
elif ! command -v omniroute >/dev/null; then
    echo "omniroute is not installed - run setup.sh --only omniroute --yes once, then re-run this."
else
    echo "Starting OmniRoute in the background..."
    (cd "$HOME" && nohup omniroute --no-open --port 20128 >>"$STATE_DIR/omniroute.log" 2>&1 &)
    if wait_for gateway_ok; then echo "Gateway OK on 20128."
    else echo "Gateway did not answer - run: omniroute doctor"; fi
fi

# 2. LiteLLM fallback proxy on 127.0.0.1:4000. litellm does not read its own
#    .env; start-litellm.sh exports it literally (never sourced), sets
#    PYTHONUTF8 and restarts a proxy that runs with stale keys.
LITELLM_DIR="$ROOT/configuration/litellm"
if ! command -v litellm >/dev/null; then
    echo "litellm is not installed - run setup.sh --only litellm --yes once, then re-run this."
elif [[ ! -f "$LITELLM_DIR/.env" ]]; then
    echo "No litellm .env - run: python3 tools/mirror-litellm-env.py (then re-run this)."
elif ! litellm_ok && unit_installed autoos-litellm; then
    echo "Starting the autoos-litellm unit..."
    systemctl --user start autoos-litellm.service || true
    if wait_for litellm_ok; then echo "LiteLLM proxy OK on 4000."
    else echo "LiteLLM proxy did not answer - see: journalctl --user -u autoos-litellm"; fi
else
    # No-op when up with the current keys; restarts a stale-key proxy.
    bash "$LITELLM_DIR/start-litellm.sh" \
        || echo "LiteLLM proxy did not answer - see $STATE_DIR/litellm.log"
fi

# 3. OpenHands container: restart only when it exists and is not running.
#    (Created with --restart unless-stopped, so docker itself resumes it at
#    boot; this covers a container stopped by hand or an older --rm one.)
if [[ $IN_DOCKER -eq 1 ]]; then
    :
elif ! command -v docker >/dev/null; then
    echo "docker is not on PATH - skipping the OpenHands container."
else
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'openhands-app'; then
        echo "OpenHands container already running - nothing to do."
    elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'openhands-app'; then
        echo "Restarting the stopped openhands-app container..."
        docker start openhands-app >/dev/null
        echo "OpenHands UI should answer on http://localhost:3000 shortly."
    else
        echo "No openhands-app container yet - first boot: ./configuration/start-stack.sh openhands"
    fi
fi

# 4. opencode serve on :4096 (password-protected web UI for the phone).
if [[ $IN_DOCKER -eq 1 ]]; then
    :
elif serve_ok; then
    echo "opencode serve already up on :4096 - nothing to do."
elif unit_installed autoos-opencode; then
    echo "Starting the autoos-opencode unit..."
    systemctl --user start autoos-opencode.service || true
    if wait_for serve_ok; then echo "opencode serve OK on :4096."
    else echo "opencode serve did not answer - see: journalctl --user -u autoos-opencode"; fi
elif ! command -v opencode >/dev/null; then
    echo "opencode is not installed - run setup.sh --only opencode-cli --yes once, then re-run this."
else
    echo "Starting opencode serve in the background..."
    "$HERE/run-opencode-serve.sh" --detach
    if wait_for serve_ok; then echo "opencode serve OK on :4096."
    else echo "opencode serve did not answer - see $STATE_DIR/opencode-serve.log"; fi
fi
