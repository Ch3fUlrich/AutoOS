#!/usr/bin/env bash
# Start the AutoOS AI stack: OmniRoute gateway, then the app you pick.
#
#   export AUTOOS_OMNIROUTE_KEY='sk-...'   # dashboard -> api-manager
#   ./configuration/start-stack.sh opencode|zed|nvim|openhands|opencode-serve
set -euo pipefail

GATEWAY="http://127.0.0.1:20128"
APP="${1:-none}"
# The key file sits next to this script: configuration/api-keys.yml.
KEYS_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/api-keys.yml"
if [[ -z "${AUTOOS_OMNIROUTE_KEY:-}" && -f "$KEYS_FILE" ]]; then
    AUTOOS_OMNIROUTE_KEY="$(sed -n 's/^omniroute[[:space:]]*:[[:space:]]*//p' "$KEYS_FILE" | head -1 | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')"
    export AUTOOS_OMNIROUTE_KEY
fi
if [[ -z "${AUTOOS_OMNIROUTE_KEY:-}" ]]; then
    echo "No OmniRoute client key. Add 'omniroute: sk-...' to configuration/api-keys.yml,"
    echo "or export AUTOOS_OMNIROUTE_KEY. Then configure providers: ./configuration/omniroute/apply.sh"
    exit 1
fi

gateway_ok() {
    # /api/health, not /v1/models: the latter 401s for a normal client key in
    # this build, so probing it would call a healthy gateway "down" forever.
    curl -sf -m 5 "$GATEWAY/api/health" >/dev/null 2>&1
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
        # Docker must be running; the container reaches the gateway via host IP.
        # Image name is the current upstream one (the old docker.all-hands.dev
        # registry is gone). The sandbox/agent-server image is chosen by
        # OpenHands itself on first conversation - do not pin it.
        # The client key is exported and inherited with `-e LLM_API_KEY` (no
        # value on the command line, so `ps` never shows it).
        # Stale user settings break the current image (measured 2026-09-21:
        # a persisted schema_version 6 while the image supports 4 -> /api
        # settings 500; meanwhile a live version-3 file serves fine, so only
        # versions NEWER than the image (6+) and unparseable files move
        # aside - with a backup, never a delete. OpenHands regenerates.
        oh_settings="$HOME/.openhands/settings.json"
        if [[ -f "$oh_settings" ]]; then
            oh_ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("schema_version", ""))' "$oh_settings" 2>/dev/null || echo PARSE-FAIL)"
            if [[ "$oh_ver" == "PARSE-FAIL" ]] || { [[ "$oh_ver" =~ ^[0-9]+$ ]] && (( oh_ver >= 6 )); }; then
                cp "$oh_settings" "$oh_settings.autoos-backup-$(date +%Y%m%d-%H%M%S)"
                rm -f "$oh_settings"
                echo "Stale OpenHands settings (schema_version $oh_ver) moved aside."
            fi
        fi
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'openhands-app'; then
            docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'openhands-app' \
                || docker start openhands-app >/dev/null
        else
            export LLM_API_KEY="$AUTOOS_OMNIROUTE_KEY"
            # Detached, no -it: -it fails without a TTY (non-interactive
            # shells) and foreground -it never returns, so the URL line below
            # would lie.
            docker run -d --rm \
                -e LLM_MODEL=openai/tier1 \
                -e LLM_API_KEY \
                -e LLM_BASE_URL="http://host.docker.internal:20128/v1" \
                -e LOG_ALL_EVENTS=true \
                -p 3000:3000 \
                -v /var/run/docker.sock:/var/run/docker.sock \
                -v "$HOME/.openhands:/.openhands" \
                --add-host host.docker.internal:host-gateway \
                --name openhands-app \
                docker.openhands.dev/openhands/openhands:latest >/dev/null
            unset LLM_API_KEY
        fi
        # No URL line without a probe behind it.
        for _ in $(seq 1 36); do
            curl -s -m 5 -o /dev/null http://127.0.0.1:3000/ 2>/dev/null && break
            sleep 5
        done
        curl -s -m 5 -o /dev/null http://127.0.0.1:3000/ 2>/dev/null \
            || { echo "OpenHands did not answer on :3000 - see: docker logs openhands-app"; exit 1; }
        echo "OpenHands UI: http://localhost:3000"
        ;;
    opencode-serve)
        # Phone fallback UI (docs/openhands-runbook.md rung 2): resume when
        # down, no-op when up. 401 without pairing credentials = alive.
        if curl -s -m 5 -o /dev/null "http://127.0.0.1:4096/" 2>/dev/null; then
            echo "opencode serve already up on :4096 - nothing to do."
        elif ! command -v opencode >/dev/null; then
            echo "opencode is not installed. Run: ./setup.sh --only opencode-cli --yes"
            exit 1
        else
            echo "Starting opencode serve in the background..."
            nohup opencode serve --hostname 0.0.0.0 --port 4096 >/tmp/opencode-serve.log 2>&1 &
            for _ in $(seq 1 24); do
                curl -s -m 5 -o /dev/null "http://127.0.0.1:4096/" 2>/dev/null && break
                sleep 5
            done
            echo "opencode serve should answer on http://localhost:4096 (401 = alive, pair via: opencode pair)."
        fi
        ;;
esac
