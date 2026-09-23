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
        # User settings drift newer than the image (measured 2026-09-23:
        # agent-canvas 1.20 writes agent_settings.schema_version 6 + an
        # `enabled` key on every MCP server, while the
        # docker.openhands.dev/openhands/openhands:latest image supports
        # version 4 and rejects `enabled` with extra_forbidden -> every
        # /api settings route 500s. Repair in place (with a backup, never
        # a delete): clamp the version DOWN to 4 (older payloads keep
        # theirs so the image's own migrations still run) and strip the
        # `enabled` keys. Unparseable files still move aside - OpenHands
        # regenerates. NOTE: the version lives under agent_settings, NOT
        # top-level schema_version (top stays 2-3 on both good and bad
        # files, so checking the top level misses the breakage).
        oh_settings="$HOME/.openhands/settings.json"
        if [[ -f "$oh_settings" ]]; then
            oh_repair_rc=0
            python3 - "$oh_settings" <<'PY' || oh_repair_rc=$?
import json, shutil, sys, datetime
path = sys.argv[1]
try:
    doc = json.load(open(path, encoding="utf-8"))
except Exception:
    print("Unparseable OpenHands settings.")
    sys.exit(2)
agent = doc.get("agent_settings") if isinstance(doc.get("agent_settings"), dict) else {}
ver = agent.get("schema_version")
has_enabled = any(isinstance(s, dict) and "enabled" in s for s in (agent.get("mcp_config") or {}).values())
if (isinstance(ver, int) and ver > 4) or has_enabled:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path + ".autoos-backup-" + ts)
    if isinstance(ver, int) and ver > 4:
        agent["schema_version"] = 4
    for srv in (agent.get("mcp_config") or {}).values():
        if isinstance(srv, dict):
            srv.pop("enabled", None)
    json.dump(doc, open(path, "w", encoding="utf-8"), indent=2)
    print("Repaired OpenHands settings (agent_settings.schema_version %s -> 4, stripped enabled keys; backup kept)." % ver)
sys.exit(0)
PY
            if [[ $oh_repair_rc -eq 2 ]]; then
                cp "$oh_settings" "$oh_settings.autoos-backup-$(date +%Y%m%d-%H%M%S)"
                rm -f "$oh_settings"
                echo "Unparseable OpenHands settings moved aside (OpenHands regenerates)."
            elif [[ $oh_repair_rc -ne 0 ]]; then
                echo "OpenHands settings repair reported a problem (exit $oh_repair_rc) - continuing anyway."
            fi
        fi
        # Re-project the tier profiles from the spec on every start: a rotated
        # key, a re-curated spec, or a hand edit converges back automatically.
        _ss_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
        if command -v python3 >/dev/null; then
            python3 "$_ss_root/tools/sync-openhands-profiles.py" --openhands-dir "$HOME/.openhands" \
                || echo "tier profile sync reported a problem - continuing with existing profiles"
        else
            echo "python3 not found - tier profile sync skipped (the installer covers it)"
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
