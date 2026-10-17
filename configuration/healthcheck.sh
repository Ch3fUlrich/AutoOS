#!/usr/bin/env bash
# Probe the AutoOS phone-reachable stack; log-only unless --fix is passed.
#
#   ./configuration/healthcheck.sh       # report only
#   ./configuration/healthcheck.sh --fix # resume whatever is down (gateway,
#                                        # litellm, OpenHands, opencode serve)
#
# Nothing is installed, uninstalled, or overwritten in either mode.
# Human fallback commands are printed at the end.
set -euo pipefail

FIX=0
if [[ "${1:-}" == "--fix" ]]; then FIX=1; fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/healthcheck-$(date +%Y%m%d).log"

say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

# Prints the HTTP code, or 0 when nothing listens. (curl still prints its
# -w format on connection failure, so the exit code decides, not the text.)
code_of() {
    local out rc
    out="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1$2" 2>/dev/null)"
    rc=$?
    if [[ $rc -ne 0 ]]; then printf '0'; else printf '%s' "$out"; fi
}

GW="$(code_of 20128 /api/health)"
LT="$(code_of 4000 /)"
OH="$(code_of 3000 /)"
OC="$(code_of 4096 /)"
UI="$(code_of 8777 /)"

if [[ "$GW" == "200" ]]; then GW_UP=1; else GW_UP=0; fi
if [[ "$LT" == "200" ]]; then LT_UP=1; else LT_UP=0; fi
if [[ "$OH" != "0" && "$OH" != "000" ]]; then OH_UP=1; else OH_UP=0; fi
# opencode serve (V2) answers its UI with 200 and /api/* with 401 without the
# password; an older build answered / with 401. Both mean alive.
if [[ "$OC" == "200" || "$OC" == "401" ]]; then OC_UP=1; else OC_UP=0; fi
if [[ "$UI" != "0" && "$UI" != "000" ]]; then UI_UP=1; else UI_UP=0; fi

if [[ $GW_UP -eq 1 ]]; then say "gateway :20128 -> $GW up"; else say "gateway :20128 -> $GW DOWN"; fi
if [[ $LT_UP -eq 1 ]]; then say "litellm :4000 -> $LT up"; else say "litellm :4000 -> $LT DOWN"; fi
if [[ $OH_UP -eq 1 ]]; then say "openhands :3000 -> $OH up"; else say "openhands :3000 -> $OH DOWN"; fi
if [[ $OC_UP -eq 1 ]]; then say "opencode :4096 -> $OC up"; else say "opencode :4096 -> $OC DOWN"; fi
if [[ $UI_UP -eq 1 ]]; then say "autoos-ui :8777 -> $UI up"; else say "autoos-ui :8777 -> down (expected unless setup.sh --serve runs)"; fi

# Docker AI stack (server profile): name which containers serve the ports
# above, with docker's own health verdict. Report only.
AI_STACK="$ROOT/configuration/docker/ai-stack/ai-stack.sh"
if bash "$AI_STACK" is-active >/dev/null 2>&1; then
    for c in autoos-omniroute autoos-opencode openhands-app; do
        say "docker $c -> $(docker inspect -f '{{.State.Status}}{{if .State.Health}} ({{.State.Health.Status}}){{end}}' "$c" 2>/dev/null || echo 'no container')"
    done
fi

# Omnigraph memory: clients show it "connected" even when every read fails
# (no token, stale token, missing graph), so probe it like the bridge does:
# health, an authenticated schema read, the Project hub. Report only.
if OG_OUT="$(python3 "$ROOT/tools/check-omnigraph.py" 2>&1)"; then
    say "omnigraph -> up ($(printf '%s' "$OG_OUT" | sed -n 's/^  //p' | tr '\n' ';' | sed 's/;$//'))"
else
    say "omnigraph -> FAIL"
    printf '%s\n' "$OG_OUT" | grep '^FAIL' | while IFS= read -r line; do say "  $line"; done
fi
printf '%s\n' "$OG_OUT" | { grep '^WARN' || true; } | while IFS= read -r line; do say "  $line"; done

ANY_DOWN=0
if [[ $GW_UP -eq 0 || $LT_UP -eq 0 || $OH_UP -eq 0 || $OC_UP -eq 0 ]]; then ANY_DOWN=1; fi
if [[ $FIX -eq 1 && $ANY_DOWN -eq 1 ]]; then
    # Start-AutoOSStack.sh resumes every one of them (through its unit when
    # register-autostart.sh installed one), and no-ops the ones that are up.
    say "--fix: resuming via Start-AutoOSStack.sh ..."
    bash "$ROOT/configuration/autostart/Start-AutoOSStack.sh" 2>&1 | tee -a "$LOG"
elif [[ $ANY_DOWN -eq 1 ]]; then
    say "log-only mode: nothing restarted (re-run with --fix to resume)."
fi

echo ""
echo "Human fallback:"
echo "  omniroute --no-open --port 20128   # gateway"
echo "  ./configuration/start-stack.sh openhands   # container"
echo "  ./configuration/litellm/start-litellm.sh   # litellm fallback proxy"
echo "  ./configuration/autostart/run-opencode-serve.sh --detach   # phone fallback (password)"
echo "  ./configuration/autostart/register-autostart.sh   # all of it as systemd --user units"
echo "  ./configuration/docker/ai-stack/ai-stack.sh up   # docker AI stack (server profile)"
echo "  ./setup.sh --serve --bind 0.0.0.0   # browser UI (shows a token)"
echo "  python3 tools/check-omnigraph.py    # omnigraph wiring, token and graph"
