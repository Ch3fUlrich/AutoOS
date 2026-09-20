#!/usr/bin/env bash
# Start the AutoOS AI stack: OmniRoute gateway, then the app you pick.
#
#   export AUTOOS_OMNIROUTE_KEY='sk-...'   # dashboard -> api-manager
#   ./configuration/start-stack.sh opencode|zed|nvim|openhands
set -euo pipefail

GATEWAY="http://127.0.0.1:20128"
APP="${1:-none}"
if [[ -z "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then
    echo "Set AUTOOS_OMNIROUTE_KEY first (OmniRoute dashboard -> api-manager -> Create API Key)."
    exit 1
fi

gateway_ok() {
    curl -sf -m 5 -H "Authorization: Bearer $AUTOOS_OMNIROUTE_KEY" "$GATEWAY/v1/models" >/dev/null 2>&1
}

if ! gateway_ok; then
    command -v omniroute >/dev/null || { echo "omniroute is not installed. Run: ./setup.sh --only omniroute --yes"; exit 1; }
    echo "Starting OmniRoute in the background..."
    nohup omniroute --no-open --port 20128 >/tmp/omniroute.log 2>&1 &
    for _ in $(seq 1 24); do gateway_ok && break; sleep 5; done
    gateway_ok || { echo "Gateway did not answer. Run: omniroute doctor"; exit 1; }
fi
echo "Gateway OK on $GATEWAY"

case "$APP" in
    opencode)  opencode ;;
    zed)       zed . ;;
    nvim)      nvim ;;
    openhands)
        # Docker must be running; container reaches the gateway via host IP.
        docker run -it --rm --pull=always \
            -e LLM_MODEL=auto/smart \
            -e LLM_API_KEY="$AUTOOS_OMNIROUTE_KEY" \
            -e LLM_BASE_URL="http://host.docker.internal:20128/v1" \
            -e SANDBOX_RUNTIME_CONTAINER_IMAGE=docker.all-hands.dev/all-hands-ai/runtime:latest \
            -e LOG_ALL_EVENTS=true \
            -v /var/run/docker.sock:/var/run/docker.sock \
            -v "$HOME/.openhands:/.openhands" \
            --add-host host.docker.internal:host-gateway \
            --name openhands-app \
            docker.all-hands.dev/all-hands-ai/openhands:latest
        ;;
esac
