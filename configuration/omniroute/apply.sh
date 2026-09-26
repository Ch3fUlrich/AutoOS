#!/usr/bin/env bash
# Apply the AutoOS router configuration to OmniRoute:
#   1. registers every provider key found in configuration/api-keys.yml
#   2. (re)creates the tier combos from `tools/registry.py render omniroute`
#      (catalog/ai-registry.json), skipping any leg the registry marks
#      unavailable (routes.<id>.unavailable_legs, providers.<p>.available:
#      false); configuration/omniroute/combos.json stays the checked-in,
#      byte-comparable render of the same registry (`render omniroute
#      --check`) - AUTOOS_OMNIROUTE_COMBOS_FILE reads it (or another file of
#      that shape) directly instead, unfiltered, as an explicit override
#   3. prunes the combos listed there as "retired" from the store (only those)
#
# Safe to re-run: providers are add-or-update, combos are replaced in place,
# and a retired combo that is already gone is simply not found again.
# Model refs the live catalog does not know are skipped with a warning, so a
# renamed upstream model degrades one tier leg instead of breaking the run.
# A combo left with no available leg at all (registry-unavailable or catalog-
# unknown) is reported and never created - an empty combo is never pushed.
#
#   ./configuration/omniroute/apply.sh [--dry-run] [--probe]
#
# --probe sends one tiny request per combo and reports what answered
# (spends a few hundred tokens; skipped under --dry-run).
# Requires python3 for JSON parsing and the probe's HTTP calls.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# AUTOOS_OMNIROUTE_URL points the run (and the CLI) at another gateway; the
# tests aim it at a closed port so a dry run never reads the live one.
GATEWAY="${AUTOOS_OMNIROUTE_URL:-http://127.0.0.1:20128}"
if [[ -n "${AUTOOS_OMNIROUTE_URL:-}" ]]; then export OMNIROUTE_BASE_URL="$GATEWAY"; fi
KEYS_FILE="${AUTOOS_KEYS_FILE:-$ROOT/configuration/api-keys.yml}"
COMBOS_FILE="$HERE/combos.json"
# The live combo list (task A5a) comes from `tools/registry.py render
# omniroute` against catalog/ai-registry.json by default, so a leg the
# registry has since marked unavailable (routes.<id>.unavailable_legs,
# providers.<p>.available: false - e.g. the cerebras legs, zen deepseek-
# v4.1-flash, openrouter) is skipped instead of pushed to the live gateway.
# AUTOOS_REGISTRY_FILE overrides which registry render omniroute reads.
# AUTOOS_OMNIROUTE_COMBOS_FILE bypasses the render entirely and reads a
# combos.json-shaped file directly (today's pre-A5a behaviour, unfiltered) -
# an explicit escape hatch for a test or a checkout whose registry has not
# caught up. $COMBOS_FILE itself stays the "retired" source for the prune
# step below either way; it never drives the live combo list on its own.
REGISTRY_FILE="${AUTOOS_REGISTRY_FILE:-$ROOT/catalog/ai-registry.json}"
COMBOS_OVERRIDE="${AUTOOS_OMNIROUTE_COMBOS_FILE:-}"
DRY=0
PROBE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --probe)   PROBE=1 ;;
    esac
done
PROBE_COMBOS=()
# Always say so up front: with a live gateway and a key file no later line
# mentions the dry run, and the plan then reads like a real run.
[[ $DRY -eq 1 ]] && echo "This is a dry run - nothing is registered, created or started."

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
        # A copied template keeps REPLACE_WITH_* for keys you do not have;
        # registering one would also block the real key later ("already
        # registered"), so a placeholder counts as no key at all.
        [[ "$val" == REPLACE_WITH_* ]] && continue
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

# Docker AI stack (server profile): the gateway is the autoos-omniroute
# container. The CLI's machine token is accepted from loopback peers only and
# a published port sees the docker gateway as the peer, so management goes
# through the manage-scoped key ai-stack.sh migrate created (host-only file).
# The key goes to the omniroute CLI only (omni below), never into this
# script's environment: curl, python and the probe must not inherit it.
AI_STACK="$ROOT/configuration/docker/ai-stack/ai-stack.sh"
IN_DOCKER=0
MANAGE_KEY=""
if [[ -z "${AUTOOS_OMNIROUTE_URL:-}" ]] && bash "$AI_STACK" is-active >/dev/null 2>&1; then
    IN_DOCKER=1
    MANAGE_KEY_FILE="${AUTOOS_AI_STACK_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/autoos/ai-stack}/manage.key"
    if [[ -z "${OMNIROUTE_API_KEY:-}" && -s "$MANAGE_KEY_FILE" ]]; then
        MANAGE_KEY="$(tr -d '\r\n' <"$MANAGE_KEY_FILE")"
        echo "Gateway runs in docker - the CLI manages it with the manage-scoped key ($MANAGE_KEY_FILE)."
    elif [[ -z "${OMNIROUTE_API_KEY:-}" ]]; then
        echo "Gateway runs in docker but $MANAGE_KEY_FILE is missing - provider and combo changes will be refused."
        echo "  Create a key with scope 'manage' in the dashboard and save it there (mode 600)."
    fi
fi
# omni: the omniroute CLI, with the manage key in ITS environment only (an
# assignment prefix, never argv - `ps` would show argv).
omni() {
    if [[ -n "$MANAGE_KEY" ]]; then
        OMNIROUTE_API_KEY="$MANAGE_KEY" command omniroute "$@"
    else
        command omniroute "$@"
    fi
}

gateway_up() { curl -sf -m 5 "$GATEWAY/api/health" >/dev/null 2>&1; }
if ! gateway_up; then
    if [[ $DRY -eq 1 ]]; then
        echo "Gateway is down; dry run continues with the static plan (would start it with: omniroute --no-open --port 20128)."
    elif [[ $IN_DOCKER -eq 1 ]]; then
        echo "Starting the gateway container…"
        bash "$AI_STACK" up omniroute
        gateway_up || { echo "Gateway did not start — run: $AI_STACK status"; exit 1; }
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
    if ( export "$var=$value"; omni providers add "$provider_id" \
            --credential-env "$var" "${data_args[@]}" --yes ) >/dev/null 2>&1; then
        echo "  + $provider_id registered"
    else
        echo "  ! $provider_id registration failed — register it in the dashboard"
    fi
}

echo "Providers:"
# Connections that already exist are left alone: re-adding would either fail
# or duplicate them, and neither proves the pipeline works.
# A down gateway (dry run) has no list to read; the plan then comes from the
# key file alone instead of from whatever else answers the CLI.
existing_ids=""
if gateway_up; then
    existing_ids="$(omni providers list 2>/dev/null | grep -oE '^[[:space:]]*[0-9a-f]+[[:space:]]+[a-z0-9-]+' | grep -oE '[a-z0-9-]+$' || true)"
fi
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
# Through the CLI, not curl + the client key: /api/resilience is a management
# route and answers the client key with 403 "Invalid management token"
# (measured 2026-09-24); the local CLI sends the machine loopback token.
# It runs from $HOME so it never picks up a .env in the caller's cwd.
MAX_WAIT_MS=180000
BREAKER_THRESHOLD=2
omni_json() {
    # The CLI prints "Loaded env" banners on stdout before the JSON document.
    (cd "$HOME" && omni --output json --no-color "$@" 2>/dev/null) | sed -n '/^[[:space:]]*[{[]/,$p'
}
if [[ $DRY -eq 1 ]]; then
    echo "  - would set requestQueue.maxWaitMs = $MAX_WAIT_MS (omniroute api system patch-api-resilience)"
    echo "  - would set providerBreaker.apikey.failureThreshold = $BREAKER_THRESHOLD"
elif ! command -v omniroute >/dev/null; then
    echo "  - omniroute CLI missing - cannot set the resilience settings"
else
    current="$(omni_json api system get-api-resilience \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["requestQueue"]["maxWaitMs"], d["providerBreaker"]["apikey"]["failureThreshold"])' 2>/dev/null || true)"
    body="{\"requestQueue\":{\"maxWaitMs\":$MAX_WAIT_MS},\"providerBreaker\":{\"apikey\":{\"failureThreshold\":$BREAKER_THRESHOLD,\"degradationThreshold\":1,\"resetTimeoutMs\":30000}}}"
    if [[ "$current" == "$MAX_WAIT_MS $BREAKER_THRESHOLD" ]]; then
        echo "  = resilience settings already current (maxWaitMs=$MAX_WAIT_MS, breaker=$BREAKER_THRESHOLD)"
    elif (cd "$HOME" && omni api system patch-api-resilience --body "$body") >/dev/null 2>&1; then
        echo "  + resilience settings set (maxWaitMs=$MAX_WAIT_MS, breaker=$BREAKER_THRESHOLD; was ${current:-unknown})"
    else
        echo "  ! could not set resilience settings - run: omniroute api system patch-api-resilience --body '$body'"
    fi
fi

echo "Combos:"
while IFS=$'\t' read -r tag a b c; do
    [[ -z "$tag" ]] && continue
    if [[ "$tag" == SKIP ]]; then
        # a=combo name, b=leg, c=registry comment (first 60 chars) or reason.
        echo "  - skip leg $a $b: unavailable ($c)"
        continue
    fi
    # tag == COMBO: a=name, b=strategy, c=models (csv; already registry-
    # available - a SKIP line was emitted above for anything dropped there).
    name="$a" strategy="$b" models="$c"
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
    if omni combo create "$name" --strategy "$strategy" --models "$keep" >/dev/null 2>&1; then
        echo "  + $name created ($strategy)"
        PROBE_COMBOS+=("$name")
    else
        omni combo delete "$name" --yes >/dev/null 2>&1 || true
        if omni combo create "$name" --strategy "$strategy" --models "$keep" >/dev/null 2>&1; then
            echo "  + $name replaced ($strategy)"
            PROBE_COMBOS+=("$name")
        else
            echo "  ! $name creation failed (previous version, if any, is untouched)"
        fi
    fi
done < <(python3 - "$ROOT" "$REGISTRY_FILE" "$COMBOS_OVERRIDE" <<'PY'
import json, sys
from pathlib import Path

root, registry_path, override = sys.argv[1], sys.argv[2], sys.argv[3]


def emit(name, strategy, models, reason_of):
    """Print one SKIP line per unavailable leg, then one COMBO line with the
    survivors - reason_of(leg) returns None (keep) or a reason string."""
    keep = []
    for leg in models:
        reason = reason_of(leg)
        if reason is None:
            keep.append(leg)
        else:
            reason = reason.replace("\t", " ").replace("\n", " ").strip()[:60]
            print("SKIP\t%s\t%s\t%s" % (name, leg, reason))
    print("COMBO\t%s\t%s\t%s" % (name, strategy, ",".join(keep)))


if override:
    # The explicit escape hatch: read a combos.json-shaped file directly,
    # unfiltered - today's pre-A5a behaviour, no registry consulted at all.
    data = json.load(open(override, encoding="utf-8"))
    for c in data.get("combos", []):
        emit(c["name"], c.get("strategy", "priority"), c["models"], lambda leg: None)
else:
    # Default: render the live combo list from the registry (task A5a) and
    # drop a leg it marks unavailable instead of pushing it to the gateway.
    sys.path.insert(0, str(Path(root) / "tools"))
    import registry as R

    doc = R.load(registry_path)
    rendered = R.render_omniroute(doc)
    routes = doc.get("routes") or {}
    providers = doc.get("providers") or {}

    def reason_of(route):
        unavailable = route.get("unavailable_legs") or {}

        def f(leg):
            # routes.<id>.unavailable_legs.<leg>.available: false - an
            # operator decision with a comment naming why.
            entry = unavailable.get(leg)
            if isinstance(entry, dict) and entry.get("available") is False:
                return entry.get("$comment") or "unavailable"
            # providers.<p>.available: false - a provider-wide outage/close
            # with no per-leg entry (e.g. a future leg on a dead provider).
            try:
                provider_id, _ = R.resolve_leg(leg, doc)
            except ValueError:
                return None
            if not (providers.get(provider_id) or {}).get("available", True):
                return "provider %s is marked unavailable" % provider_id
            return None
        return f

    for c in rendered.get("combos", []):
        name = c["name"]
        emit(name, c.get("strategy") or "priority", c["models"],
             reason_of(routes.get(name) or {}))
PY
)

# ─── Prune: delete the retired combos, and only those ───────────────────────
# combos.json "retired" lists the ids a rename or removal left behind. The loop
# above only creates and replaces by name, so they used to stay in the store
# forever (9 orphans deleted by hand on 2026-09-25). A name is deleted only when
# it is retired AND live (and not a current combo): a store combo that is not
# in "retired" may be one the user made and is never touched.
# A down gateway is not listed at all: the CLI would fall back to reading the
# store file directly, and a dry run must not depend on that.
echo "Prune:"
list_out=""
if ! command -v omniroute >/dev/null; then
    echo "  - omniroute CLI missing - the store is not read, nothing pruned"
elif ! gateway_up; then
    echo "  - gateway down - the store is not read, nothing pruned"
elif ! list_out="$(omni combo list 2>/dev/null)"; then
    echo "  ! could not list the store's combos - nothing pruned"
elif ! prune_names="$(printf '%s\n' "$list_out" | python3 -c '
import json, re, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
current = {c["name"] for c in data.get("combos", [])}
live = set()
# "combo list" prints "  <icon> <name padded> [<strategy>] <status>" with ANSI
# colour on the icon and the status: strip it, take the name before [.
for line in sys.stdin.read().splitlines():
    m = re.match(r"\s*\S+\s+(\S+)\s+\[[^\]]*\]", re.sub(r"\x1b\[[0-9;]*m", "", line))
    if m:
        live.add(m.group(1))
for name in data.get("retired", []):
    if name in live and name not in current:
        print(name)
' "$COMBOS_FILE")"; then
    echo "  ! cannot read the retired list from $COMBOS_FILE - nothing pruned"
elif [[ -z "$prune_names" ]]; then
    echo "  = no retired combos in the store"
else
    while IFS= read -r retired_name; do
        [[ -z "$retired_name" ]] && continue
        if [[ $DRY -eq 1 ]]; then
            echo "  - $retired_name: retired, would delete"
        elif omni combo delete "$retired_name" --yes </dev/null >/dev/null 2>&1; then
            echo "  - $retired_name: retired, deleted"
        else
            echo "  ! $retired_name: retired, delete failed - run: omniroute combo delete $retired_name --yes"
        fi
    done <<<"$prune_names"
fi

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
