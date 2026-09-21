#!/usr/bin/env bash
# Resume the AutoOS AI stack after login: gateway + OpenHands + opencode serve.
#
# Opt-in resume helper run by the autoos-stack systemd user unit (or by hand
# after a reboot). Safe to run twice: every probe below no-ops when already
# up, and nothing here ever deletes or overwrites.
#
#   ./configuration/autostart/Start-AutoOSStack.sh
set -euo pipefail

GATEWAY="http://127.0.0.1:20128"

gateway_ok() {
    curl -sf -m 5 "$GATEWAY/api/health" >/dev/null 2>&1
}

# 1. Gateway first: everything else routes through it.
if gateway_ok; then
    echo "Gateway already up on 20128 - nothing to do."
elif ! command -v omniroute >/dev/null; then
    echo "omniroute is not installed - run setup.sh --only omniroute --yes once, then re-run this."
else
    echo "Starting OmniRoute in the background..."
    nohup omniroute --no-open --port 20128 >/tmp/omniroute.log 2>&1 &
    for _ in $(seq 1 24); do gateway_ok && break; sleep 5; done
    if gateway_ok; then
        echo "Gateway OK on 20128."
    else
        echo "Gateway did not answer - run: omniroute doctor"
    fi
fi

# 2. OpenHands container: restart only when it exists and is not running.
if ! command -v docker >/dev/null; then
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

# 3. opencode serve on :4096: start hidden only when down (401 = alive).
serve_ok() {
    local code
    code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:4096/" 2>/dev/null || true)"
    [[ "$code" == "200" || "$code" == "401" ]]
}
if serve_ok; then
    echo "opencode serve already up on :4096 - nothing to do."
elif ! command -v opencode >/dev/null; then
    echo "opencode is not installed - run setup.sh --only opencode-cli --yes once, then re-run this."
else
    echo "Starting opencode serve in the background..."
    nohup opencode serve --hostname 0.0.0.0 --port 4096 >/tmp/opencode-serve.log 2>&1 &
    for _ in $(seq 1 24); do serve_ok && break; sleep 5; done
    if serve_ok; then
        echo "opencode serve OK on :4096."
    else
        echo "opencode serve did not answer - re-run ./configuration/healthcheck.sh for detail."
    fi
fi
