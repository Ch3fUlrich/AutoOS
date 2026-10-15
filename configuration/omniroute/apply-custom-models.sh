#!/usr/bin/env bash
# Declare custom (manual) gateway models from configuration/omniroute/custom-models.json.
#
#   ./configuration/omniroute/apply-custom-models.sh [--dry-run] \
#                                      [--go <ref> --go-sha <sha>] [--go-offline]
#
# For each row it reads GET /api/provider-models?provider=<provider> and POSTs
# /api/provider-models (source "manual") only for ids the gateway does not hold
# yet, so a re-run changes nothing ("= ... already present"). It writes nothing
# else: no provider, combo, override or resilience change.
#
# Auth is the one apply.sh uses: OMNIROUTE_API_KEY if set, else the manage-scoped
# key of the docker AI stack. The key goes to curl through a private 0600 config
# file, never argv, and is never printed. A run that MUTATES the gateway needs
# the same explicit GO as apply.sh (fleet rule D-825: --go <ref> --go-sha <sha>,
# verified by infra/mcp-servers/scripts/_go_gate.py); --dry-run needs none and
# makes no write (it still reads the list when a manage key is available).
# Requires python3 and curl.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
GATEWAY="${AUTOOS_OMNIROUTE_URL:-http://127.0.0.1:20128}"
MODELS_FILE="${AUTOOS_CUSTOM_MODELS_FILE:-$HERE/custom-models.json}"
DRY=0
GO_REF=""
GO_SHA=""
GO_OFFLINE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --go)
            [[ $# -ge 2 ]] || { echo "apply-custom-models.sh: --go needs a reference." >&2; exit 2; }
            GO_REF="$2"; shift ;;
        --go=*) GO_REF="${1#--go=}" ;;
        --go-sha)
            [[ $# -ge 2 ]] || { echo "apply-custom-models.sh: --go-sha needs this checkout's HEAD sha." >&2; exit 2; }
            GO_SHA="$2"; shift ;;
        --go-sha=*) GO_SHA="${1#--go-sha=}" ;;
        --go-offline) GO_OFFLINE=1 ;;
        *) echo "apply-custom-models.sh: unknown argument '$1' (--dry-run --go <ref> --go-sha <sha> --go-offline are the flags)." >&2
           exit 2 ;;
    esac
    shift
done

command -v python3 >/dev/null || { echo "apply-custom-models.sh: python3 is required." >&2; exit 2; }
command -v curl >/dev/null || { echo "apply-custom-models.sh: curl is required." >&2; exit 2; }
[[ -f "$MODELS_FILE" ]] || { echo "apply-custom-models.sh: $MODELS_FILE not found." >&2; exit 2; }

if [[ $DRY -ne 1 ]]; then
    [[ -n "$GO_REF" && -n "$GO_SHA" ]] || {
        echo "apply-custom-models.sh: refusing to change live gateway state without --go <ref> --go-sha <sha> (fleet rule D-825)." >&2
        echo "  Inspect without changing anything: --dry-run." >&2
        exit 2
    }
    CHECKOUT_HEAD="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
    [[ -n "$CHECKOUT_HEAD" && "$GO_SHA" == "$CHECKOUT_HEAD" ]] || {
        echo "apply-custom-models.sh: --go-sha '$GO_SHA' is not this checkout's HEAD '${CHECKOUT_HEAD:-unreadable}'." >&2
        exit 2
    }
    _go_gate="$ROOT/infra/mcp-servers/scripts/_go_gate.py"
    _go_offline_arg=()
    [[ $GO_OFFLINE -eq 1 ]] && _go_offline_arg=(--offline)
    if [[ ! -f "$_go_gate" ]]; then
        [[ $GO_OFFLINE -eq 1 ]] || { echo "apply-custom-models.sh: cannot verify --go '$GO_REF' ($_go_gate missing); pass --go-offline to log it as unverified." >&2; exit 2; }
        echo "GO-OFFLINE: $GO_REF unverified"
    elif ! _go_verified="$(python3 "$_go_gate" verify-ref --tool apply-custom-models.sh --ref "$GO_REF" \
                --root "$ROOT" ${_go_offline_arg[@]+"${_go_offline_arg[@]}"})"; then
        exit 2
    elif [[ -n "$_go_verified" ]]; then
        printf '%s\n' "$_go_verified"
    fi
    echo "GO: $GO_REF sha=$CHECKOUT_HEAD"
else
    echo "This is a dry run - nothing is written to the gateway."
fi

# The manage key, the way apply.sh finds it: an explicit OMNIROUTE_API_KEY, else
# the host-only key file the docker AI stack's migrate step created.
REST_KEY="${OMNIROUTE_API_KEY:-}"
if [[ -z "$REST_KEY" && -z "${AUTOOS_OMNIROUTE_URL:-}" ]] \
        && bash "$ROOT/configuration/docker/ai-stack/ai-stack.sh" is-active >/dev/null 2>&1; then
    MANAGE_KEY_FILE="${AUTOOS_AI_STACK_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/autoos/ai-stack}/manage.key"
    [[ -s "$MANAGE_KEY_FILE" ]] && REST_KEY="$(tr -d '\r\n' <"$MANAGE_KEY_FILE")"
fi
[[ -n "$REST_KEY" ]] || {
    if [[ $DRY -eq 1 ]]; then
        echo "  ! no manage key - cannot read the gateway's list; plan below assumes every id is missing."
    else
        echo "apply-custom-models.sh: no manage key (set OMNIROUTE_API_KEY or create the ai-stack manage.key)." >&2
        exit 2
    fi
}

REST_ERROR=""
REST_BODY=""
# omni_rest <METHOD> <path> [json-body]: the apply.sh helper, same shape - token
# in a private curl config file, body on stdin, response in REST_BODY.
omni_rest() {
    local method="$1" path="$2" body="${3:-}" cfg out code escaped
    REST_BODY=""
    cfg="$(mktemp)" || { REST_ERROR="no curl config file could be created"; return 1; }
    out="$(mktemp)" || { rm -f -- "$cfg"; REST_ERROR="no response file could be created"; return 1; }
    chmod 600 "$cfg" "$out"
    escaped="${REST_KEY//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    printf 'header = "Authorization: Bearer %s"\n' "$escaped" >"$cfg"
    trap 'rm -f -- "$cfg" "$out"' INT TERM EXIT
    if [[ -n "$body" ]]; then
        code="$(printf '%s' "$body" | curl -s -S -m 20 --config "$cfg" -X "$method" \
            -H 'Content-Type: application/json' --data @- \
            -o "$out" -w '%{http_code}' "$GATEWAY$path" 2>/dev/null)" || code=""
    else
        code="$(curl -s -S -m 20 --config "$cfg" -X "$method" -o "$out" \
            -w '%{http_code}' "$GATEWAY$path" 2>/dev/null)" || code=""
    fi
    [[ -s "$out" ]] && REST_BODY="$(cat "$out")"
    rm -f -- "$cfg" "$out"
    trap - INT TERM EXIT
    if [[ -z "$code" ]]; then
        REST_ERROR="the gateway could not be reached for $path"
        REST_BODY=""
        return 1
    fi
    if [[ "$code" != 2* ]]; then
        REST_ERROR="HTTP $code for $path: $(head -c 200 <<<"$REST_BODY")"
        REST_BODY=""
        return 1
    fi
}

# One tab-separated line per row: provider, id, request-body JSON.
rows="$(python3 - "$MODELS_FILE" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
for m in doc.get("models", []):
    body = {"provider": m["provider"], "modelId": m["id"],
            "modelName": m.get("name", m["id"]), "source": "manual"}
    for src, dst in (("max_input_tokens", "max_input_tokens"),
                     ("max_output_tokens", "max_output_tokens"),
                     ("supportsVision", "supportsVision")):
        if src in m:
            body[dst] = m[src]
    print("%s\t%s\t%s" % (m["provider"], m["id"], json.dumps(body, separators=(",", ":"))))
PY
)" || { echo "apply-custom-models.sh: $MODELS_FILE is unreadable." >&2; exit 2; }

echo "Custom models ($MODELS_FILE):"
rc=0
declare -A HELD=()   # provider -> newline-separated ids the gateway holds
while IFS=$'\t' read -r provider model_id body; do
    [[ -z "$provider" ]] && continue
    if [[ -z "${HELD[$provider]+x}" ]]; then
        HELD[$provider]=""
        if [[ -n "$REST_KEY" ]]; then
            if omni_rest GET "/api/provider-models?provider=$provider"; then
                HELD[$provider]="$(python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for m in d.get("models", []) if isinstance(d, dict) else []:
    if isinstance(m, dict) and m.get("id"):
        print(m["id"])' <<<"$REST_BODY")"
            else
                echo "  ! $provider: could not read the list (${REST_ERROR:-no answer})"
                HELD[$provider]="?"
            fi
        fi
    fi
    if [[ "${HELD[$provider]}" == "?" ]]; then rc=1; continue; fi
    if grep -qxF -- "$model_id" <<<"${HELD[$provider]}"; then
        echo "  = $provider/$model_id already present"
        continue
    fi
    if [[ $DRY -eq 1 ]]; then
        echo "  - would add $provider/$model_id (POST /api/provider-models, source manual)"
        continue
    fi
    if omni_rest POST /api/provider-models "$body"; then
        echo "  + $provider/$model_id added"
    else
        echo "  ! $provider/$model_id add failed (${REST_ERROR:-the gateway refused})"
        rc=1
    fi
done <<<"$rows"
exit $rc
