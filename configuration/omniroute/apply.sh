#!/usr/bin/env bash
# Apply the AutoOS router configuration to OmniRoute:
#   1. registers every provider key found in configuration/api-keys.yml
#   2. (re)creates the tier combos from configuration/omniroute/combos.json
#
# Safe to re-run: providers are add-or-update, combos are replaced in place.
# Model refs the live catalog does not know are skipped with a warning, so a
# renamed upstream model degrades one tier leg instead of breaking the run.
#
#   ./configuration/omniroute/apply.sh [--dry-run]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
GATEWAY="http://127.0.0.1:20128"
KEYS_FILE="$ROOT/configuration/api-keys.yml"
COMBOS_FILE="$HERE/combos.json"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

if [[ ! -f "$KEYS_FILE" ]]; then
    echo "Missing $KEYS_FILE — copy configuration/api-keys.example.yml and fill it in."
    [[ $DRY -eq 1 ]] || exit 1
fi
command -v omniroute >/dev/null || { echo "OmniRoute CLI not installed. Run: ./setup.sh --only omniroute --yes"; exit 1; }

# ─── Parse the flat key: value map without needing PyYAML ───────────────────
declare -A KEYS=()
if [[ -f "$KEYS_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" != *:* ]] && continue
        key="$(printf '%s' "${line%%:*}" | tr -d '[:space:]')"
        val="$(printf '%s' "${line#*:}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
        [[ -n "$key" && -n "$val" ]] && KEYS["${key,,}"]="$val"
    done <"$KEYS_FILE"
fi

# key name in api-keys.yml (lower-case) -> OmniRoute provider id
PROVIDER_MAP=(
    "groq:groq"
    "google_ai_studio:gemini"
    "mistral:mistral"
    "cloudflare_workers_ai:cloudflare-ai"
    "cohere:cohere"
    "hugging_face:huggingface"
    "cerebras:cerebras"
    "sambanova:sambanova"
    "deepseek:deepseek"
    "meta:muse-code"
    "openrouter:openrouter"
    "zen:opencode-zen"
)

# Provider-specific connection data. groq and cerebras sit behind Cloudflare,
# which answers error 1010 to Node's default User-Agent; a plain client UA is
# accepted. Keyed by OmniRoute provider id.
declare -A PROVIDER_DATA=(
    [groq]='{"customUserAgent":"curl/8.7.1"}'
    [cerebras]='{"customUserAgent":"curl/8.7.1"}'
)

gateway_up() { curl -sf -m 5 "$GATEWAY/api/health" >/dev/null 2>&1; }
if ! gateway_up; then
    echo "Starting OmniRoute (background)…"
    nohup omniroute --no-open --port 20128 >/tmp/omniroute-apply.log 2>&1 &
    for _ in $(seq 1 24); do gateway_up && break; sleep 5; done
    gateway_up || { echo "Gateway did not start — run: omniroute doctor"; exit 1; }
fi
echo "Gateway OK on $GATEWAY"

# ─── Register providers ─────────────────────────────────────────────────────
register_provider() {
    local key_name="$1" provider_id="$2" value="${KEYS[$1]:-}"
    if [[ -z "$value" ]]; then
        echo "  - $provider_id: no key in api-keys.yml, skipped"
        return 0
    fi
    if [[ $DRY -eq 1 ]]; then
        echo "  - $provider_id: would register (key from $key_name)"
        return 0
    fi
    local var="AUTOOS_KEY_${key_name^^}"
    local data_args=()
    if [[ -n "${PROVIDER_DATA[$provider_id]:-}" ]]; then
        data_args=(--provider-specific-data "${PROVIDER_DATA[$provider_id]}")
    fi
    if env "$var=$value" omniroute providers add "$provider_id" --credential-env "$var" \
            "${data_args[@]}" --yes >/dev/null 2>&1; then
        echo "  + $provider_id registered"
    else
        echo "  ! $provider_id registration failed — register it in the dashboard"
    fi
}

echo "Providers:"
for entry in "${PROVIDER_MAP[@]}"; do
    register_provider "${entry%%:*}" "${entry#*:}"
done

# ─── (Re)create combos ──────────────────────────────────────────────────────
live_ids=""
if command -v python3 >/dev/null; then
    live_ids="$(curl -sf -m 10 -H "Authorization: Bearer ${KEYS[omniroute]:-}" \
        "$GATEWAY/v1/models" 2>/dev/null |
        python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for m in d.get("data", []):
    print(m.get("id", ""))' || true)"
fi
if [[ -z "$live_ids" ]]; then
    echo "  ! could not read /v1/models (check the omniroute client key in api-keys.yml)"
    echo "    combos will be created without catalog validation - verify with: omniroute simulate --combo tier1"
fi

echo "Combos:"
while IFS=$'\t' read -r name strategy models; do
    [[ -z "$name" ]] && continue
    keep=""
    dropped=""
    IFS=',' read -ra refs <<<"$models"
    for ref in "${refs[@]}"; do
        [[ -z "$ref" ]] && continue
        if [[ -z "$live_ids" || "$live_ids" == *"$ref"* ]]; then
            keep+="${keep:+,}$ref"
        else
            dropped+="${dropped:+,}$ref"
        fi
    done
    [[ -n "$dropped" ]] && echo "  - $name: catalog does not know $dropped (skipped)"
    if [[ -z "$keep" ]]; then
        echo "  ! $name: no usable models — not created"
        continue
    fi
    if [[ $DRY -eq 1 ]]; then
        echo "  - $name: would create [$strategy] with $keep"
        continue
    fi
    omniroute combo delete "$name" --yes >/dev/null 2>&1 || true
    if omniroute combo create "$name" --strategy "$strategy" --models "$keep" >/dev/null 2>&1; then
        echo "  + $name created ($strategy)"
    else
        echo "  ! $name creation failed"
    fi
done < <(python3 - "$COMBOS_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for c in data.get("combos", []):
    print("%s\t%s\t%s" % (c["name"], c.get("strategy", "priority"), ",".join(c["models"])))
PY
)

echo "Done. Apps pick this up on next start (configuration/start-stack.sh)."
