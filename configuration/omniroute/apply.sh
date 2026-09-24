#!/usr/bin/env bash
# Apply the AutoOS router configuration to OmniRoute:
#   1. registers every provider key found in configuration/api-keys.yml
#   2. (re)creates the tier combos from configuration/omniroute/combos.json
#
# Safe to re-run: providers are add-or-update, combos are replaced in place.
# Model refs the live catalog does not know are skipped with a warning, so a
# renamed upstream model degrades one tier leg instead of breaking the run.
#
#   ./configuration/omniroute/apply.sh [--dry-run] [--probe]
#
# --probe sends one tiny request per combo and reports what answered
# (spends a few hundred tokens; skipped under --dry-run).
# Requires python3 for JSON parsing and the probe's HTTP calls.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
GATEWAY="http://127.0.0.1:20128"
KEYS_FILE="$ROOT/configuration/api-keys.yml"
COMBOS_FILE="$HERE/combos.json"
DRY=0
PROBE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --probe)   PROBE=1 ;;
    esac
done
PROBE_COMBOS=()

command -v python3 >/dev/null || {
    echo "apply.sh needs python3 (JSON parsing + probe HTTP calls)."
    exit 1
}

if [[ ! -f "$KEYS_FILE" ]]; then
    echo "Missing $KEYS_FILE — copy configuration/api-keys.example.yml and fill it in."
    [[ $DRY -eq 1 ]] || exit 1
fi
command -v omniroute >/dev/null || {
    if [[ $DRY -eq 1 ]]; then
        echo "OmniRoute CLI not installed - dry run continues with the static plan (nothing started, registered or created)."
    else
        echo "OmniRoute CLI not installed. Run: ./setup.sh --only omniroute --yes"
        exit 1
    fi
}

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

# Provider registry: catalog/providers.json is the single source of truth for
# which api-keys.yml name maps to which OmniRoute provider id and for the
# provider-specific connection data (the Cloudflare UA quirk on groq/cerebras).
# apply.ps1 and the two Python tools read the same file, so the copies these
# maps used to carry cannot drift. Only providers with an omniroute_id are
# registered: meta (unregistered 2026-09-23, openrouter-first, no combo leg)
# and the omniroute client key carry none and are skipped, exactly as before.
PROVIDERS_FILE="$ROOT/catalog/providers.json"
[[ -f "$PROVIDERS_FILE" ]] || { echo "Missing $PROVIDERS_FILE" >&2; exit 1; }
provider_rows="$(python3 - "$PROVIDERS_FILE" <<'PY'
import json, sys

providers = json.load(open(sys.argv[1], encoding="utf-8"))["providers"]
for name, entry in providers.items():
    provider_id = entry.get("omniroute_id")
    if not provider_id:
        continue  # meta (unregistered) and the omniroute client key
    data = entry.get("provider_data")
    # One compact JSON string per provider: the exact argv value the CLI wants.
    data_json = json.dumps(data, separators=(",", ":")) if data else ""
    # api-keys.yml keys are lower-cased when parsed above, so match that.
    print("%s\t%s\t%s" % (name.lower(), provider_id, data_json))
PY
)" || { echo "apply.sh: cannot read $PROVIDERS_FILE" >&2; exit 1; }
PROVIDER_MAP=()
declare -A PROVIDER_DATA=()
while IFS=$'\t' read -r key_name provider_id data_json; do
    [[ -z "$key_name" ]] && continue
    PROVIDER_MAP+=("$key_name:$provider_id")
    if [[ -n "$data_json" ]]; then
        PROVIDER_DATA["$provider_id"]="$data_json"
    fi
done <<<"$provider_rows"

gateway_up() { curl -sf -m 5 "$GATEWAY/api/health" >/dev/null 2>&1; }
if ! gateway_up; then
    if [[ $DRY -eq 1 ]]; then
        echo "Gateway is down; dry run continues with the static plan (would start it with: omniroute --no-open --port 20128)."
    else
        echo "Starting OmniRoute (background)…"
        nohup omniroute --no-open --port 20128 >/tmp/omniroute-apply.log 2>&1 &
        for _ in $(seq 1 24); do gateway_up && break; sleep 5; done
        gateway_up || { echo "Gateway did not start — run: omniroute doctor"; exit 1; }
    fi
fi
if gateway_up; then echo "Gateway OK on $GATEWAY"; fi

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
    # Subshell export, not `env VAR=value`: env would put the key in argv,
    # where `ps` can read it for the lifetime of the call.
    if ( export "$var=$value"; omniroute providers add "$provider_id" \
            --credential-env "$var" "${data_args[@]}" --yes ) >/dev/null 2>&1; then
        echo "  + $provider_id registered"
    else
        echo "  ! $provider_id registration failed — register it in the dashboard"
    fi
}

echo "Providers:"
# Connections that already exist are left alone: re-adding would either fail
# or duplicate them, and neither proves the pipeline works.
existing_ids="$(omniroute providers list 2>/dev/null | grep -oE '^[[:space:]]*[0-9a-f]+[[:space:]]+[a-z0-9-]+' | grep -oE '[a-z0-9-]+$' || true)"
for entry in "${PROVIDER_MAP[@]}"; do
    if grep -qxF "${entry#*:}" <<<"$existing_ids"; then
        echo "  = ${entry#*:} already registered"
        continue
    fi
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
    echo "    combos will be created without catalog validation - verify with: omniroute simulate --combo t1-orchestrator"
fi

echo "Resilience:"
# requestQueue.maxWaitMs ships at 15000 ms — below Muse Spark's thinking time,
# so every spark request died with "Request exceeded OmniRoute's local
# rate-limit execution expiration", the chain fell through to a dead free leg,
# and the client saw the last leg's error (2026-09-22; it surfaced in opencode
# as "gemini-3.7-flash is cooling down"). 180000 sits under
# comboCooldownWait.budgetMs (300s) so a dead leg still hops.
# providerBreaker.apikey.failureThreshold 12 -> 2 (operator 2026-09-23): the
# zen free promo stays FIRST (free when it works), but a 403 is a permanent
# error, so at 12 the gateway retried the dead promo on every request. At 2 it
# is skipped for resetTimeoutMs (30s) after two failures, then retried again.
MAX_WAIT_MS=180000
BREAKER_THRESHOLD=2
CLIENT_KEY="${AUTOOS_OMNIROUTE_KEY:-}"
if [[ -z "$CLIENT_KEY" && -f "$KEYS_FILE" ]]; then
    CLIENT_KEY="$(sed -n 's/^omniroute:[[:space:]]*//p' "$KEYS_FILE" | head -n1 | tr -d '"'"'"'')"
fi
if [[ $DRY -eq 1 ]]; then
    echo "  - would set requestQueue.maxWaitMs = $MAX_WAIT_MS"
    echo "  - would set providerBreaker.apikey.failureThreshold = $BREAKER_THRESHOLD"
elif [[ -z "$CLIENT_KEY" ]]; then
    echo "  - no client key — cannot set the resilience settings (PATCH /api/resilience)"
else
    current="$(curl -sf -m 15 -H "Authorization: Bearer $CLIENT_KEY" \
        "$GATEWAY/api/resilience?include=config" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["requestQueue"]["maxWaitMs"], d["providerBreaker"]["apikey"]["failureThreshold"])' 2>/dev/null || true)"
    if [[ "$current" == "$MAX_WAIT_MS $BREAKER_THRESHOLD" ]]; then
        echo "  = resilience settings already current (maxWaitMs=$MAX_WAIT_MS, breaker=$BREAKER_THRESHOLD)"
    elif curl -sf -m 15 -X PATCH -H "Authorization: Bearer $CLIENT_KEY" \
            -H 'content-type: application/json' \
            -d "{\"requestQueue\":{\"maxWaitMs\":$MAX_WAIT_MS},\"providerBreaker\":{\"apikey\":{\"failureThreshold\":$BREAKER_THRESHOLD,\"degradationThreshold\":1,\"resetTimeoutMs\":30000}}}" \
            "$GATEWAY/api/resilience" >/dev/null; then
        echo "  + resilience settings set (maxWaitMs=$MAX_WAIT_MS, breaker=$BREAKER_THRESHOLD; was ${current:-unknown})"
    else
        echo "  ! could not set resilience settings — use the dashboard (Resilience)"
    fi
fi

echo "Combos:"
while IFS=$'\t' read -r name strategy models; do
    [[ -z "$name" ]] && continue
    keep=""
    dropped=""
    IFS=',' read -ra refs <<<"$models"
    for ref in "${refs[@]}"; do
        [[ -z "$ref" ]] && continue
        # Exact line match: a substring test would accept a ref that is merely
        # a prefix of a live id (e.g. .../muse-spark-1.3 vs ...-contributor).
        if [[ -z "$live_ids" ]] || grep -qxF "$ref" <<<"$live_ids"; then
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
    # Create first, delete only what it replaces: if the create fails the old
    # tier survives instead of leaving a hole. Delete-then-create can only run
    # when create reports "already exists" AND a retry still fails.
    if omniroute combo create "$name" --strategy "$strategy" --models "$keep" >/dev/null 2>&1; then
        echo "  + $name created ($strategy)"
        PROBE_COMBOS+=("$name")
    else
        omniroute combo delete "$name" --yes >/dev/null 2>&1 || true
        if omniroute combo create "$name" --strategy "$strategy" --models "$keep" >/dev/null 2>&1; then
            echo "  + $name replaced ($strategy)"
            PROBE_COMBOS+=("$name")
        else
            echo "  ! $name creation failed (previous version, if any, is untouched)"
        fi
    fi
done < <(python3 - "$COMBOS_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for c in data.get("combos", []):
    print("%s\t%s\t%s" % (c["name"], c.get("strategy", "priority"), ",".join(c["models"])))
PY
)

# ─── Probe: prove the combos answer, end to end ─────────────────────────────
if [[ $PROBE -eq 1 ]]; then
    key="${KEYS[omniroute]:-}"
    if [[ -z "$key" || $DRY -eq 1 ]]; then
        echo "Probe skipped (dry run, or no omniroute client key in api-keys.yml)."
    elif (( ${#PROBE_COMBOS[@]} == 0 )); then
        echo "Probe skipped (no combos were created)."
    else
        echo "Probe (one tiny request per combo):"
        # The client key goes through the environment, never argv.
        AUTOOS_PROBE_KEY="$key" python3 - "$GATEWAY" "${PROBE_COMBOS[@]}" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

gateway = sys.argv[1]
key = os.environ["AUTOOS_PROBE_KEY"]
for name in sys.argv[2:]:
    body = json.dumps({
        "model": name,
        "messages": [{"role": "user", "content": "Reply with: ok"}],
        # 2048, not 256: reasoning models (spark) spend tokens on hidden
        # thinking first and answer "empty" when the budget is tiny.
        "max_tokens": 2048,
    }).encode()
    req = urllib.request.Request(
        gateway + "/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            served = json.loads(resp.read().decode()).get("model", "?")
        print("  + %-12s served by %s" % (name, served))
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200].replace("\n", " ")
        print("  ! %-12s HTTP %s %s" % (name, e.code, detail))
    except Exception as e:
        print("  ! %-12s %s: %s" % (name, type(e).__name__, e))
PY
    fi
fi

echo "Done. Apps pick this up on next start (configuration/start-stack.sh)."
