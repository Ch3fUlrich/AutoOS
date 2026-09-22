#!/usr/bin/env bash
# Resume the AutoOS AI stack after login: gateway + litellm + OpenHands + serve.
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

# 2. LiteLLM fallback proxy on :4000. litellm does not read its own .env, so the
#    launcher exports the flat KEY=VALUE file into the process before starting.
litellm_ok() {
    curl -sf -m 5 -o /dev/null "http://127.0.0.1:4000/" 2>/dev/null
}
LITELLM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../litellm" && pwd)"
if litellm_ok; then
    echo "LiteLLM proxy already up on 4000 - nothing to do."
elif ! command -v litellm >/dev/null; then
    echo "litellm is not installed - run setup.sh --only litellm --yes once, then re-run this."
elif [[ ! -f "$LITELLM_DIR/.env" ]]; then
    echo "No litellm .env - run: python3 tools/mirror-litellm-env.py (then re-run this)."
else
    echo "Starting the LiteLLM fallback proxy on 4000..."
    (
        cd "$LITELLM_DIR" || exit 1
        set -a
        # shellcheck disable=SC1091 # generated, git-ignored, present at runtime
        . ./.env
        set +a
        # cp1252 consoles crash litellm's banner at startup.
        PYTHONUTF8=1 nohup litellm --config config.yaml --port 4000 >/tmp/litellm.log 2>&1 &
    )
    for _ in $(seq 1 24); do litellm_ok && break; sleep 5; done
    if litellm_ok; then
        echo "LiteLLM proxy OK on 4000."
    else
        echo "LiteLLM proxy did not answer - see /tmp/litellm.log"
    fi
fi

# 3. OpenHands container: restart only when it exists and is not running.
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

# 4. opencode serve on :4096: start hidden only when down (401 = alive).
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
