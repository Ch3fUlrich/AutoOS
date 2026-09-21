#!/usr/bin/env bash
# Probe the AutoOS phone-reachable stack; log-only unless --fix is passed.
#
#   ./configuration/healthcheck.sh       # report only
#   ./configuration/healthcheck.sh --fix # resume gateway/container when down
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
OH="$(code_of 3000 /)"
OC="$(code_of 4096 /)"
UI="$(code_of 8777 /)"

if [[ "$GW" == "200" ]]; then GW_UP=1; else GW_UP=0; fi
if [[ "$OH" != "0" && "$OH" != "000" ]]; then OH_UP=1; else OH_UP=0; fi
# opencode serve answers 401 without credentials: alive, not down.
if [[ "$OC" == "200" || "$OC" == "401" ]]; then OC_UP=1; else OC_UP=0; fi
if [[ "$UI" != "0" && "$UI" != "000" ]]; then UI_UP=1; else UI_UP=0; fi

if [[ $GW_UP -eq 1 ]]; then say "gateway :20128 -> $GW up"; else say "gateway :20128 -> $GW DOWN"; fi
if [[ $OH_UP -eq 1 ]]; then say "openhands :3000 -> $OH up"; else say "openhands :3000 -> $OH DOWN"; fi
if [[ $OC_UP -eq 1 ]]; then say "opencode :4096 -> $OC up"; else say "opencode :4096 -> $OC DOWN"; fi
if [[ $UI_UP -eq 1 ]]; then say "autoos-ui :8777 -> $UI up"; else say "autoos-ui :8777 -> down (expected unless setup.sh --serve runs)"; fi

if [[ $FIX -eq 1 && ($GW_UP -eq 0 || $OH_UP -eq 0) ]]; then
    say "--fix: resuming via Start-AutoOSStack.sh ..."
    bash "$ROOT/configuration/autostart/Start-AutoOSStack.sh" 2>&1 | tee -a "$LOG"
elif [[ $GW_UP -eq 0 || $OH_UP -eq 0 || $OC_UP -eq 0 ]]; then
    say "log-only mode: nothing restarted (re-run with --fix to resume)."
fi

echo ""
echo "Human fallback:"
echo "  omniroute --no-open --port 20128   # gateway"
echo "  ./configuration/start-stack.sh openhands   # container"
echo "  opencode serve --hostname 0.0.0.0 --port 4096    # phone fallback"
echo "  ./setup.sh --serve --bind 0.0.0.0   # browser UI (shows a token)"
