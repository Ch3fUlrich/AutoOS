#!/usr/bin/env bash
# Apply the AutoOS router configuration to OmniRoute:
#   1. registers every provider key found in configuration/api-keys.yml
#   2. refreshes the gateway's model catalog for what it just registered
#      (omniroute models <provider>), so one run is enough on a fresh machine
#   3. (re)creates the tier combos from configuration/omniroute/combos.json
#   4. prunes the combos listed there as "retired" from the store (only those)
#
# Safe to re-run: providers are add-or-update, combos are replaced in place,
# and a retired combo that is already gone is simply not found again.
# Model refs the live catalog does not know are skipped with a warning, so a
# renamed upstream model degrades one tier leg instead of breaking the run.
#
#   ./configuration/omniroute/apply.sh [--dry-run] [--probe] [--drift]
#
# --probe sends one tiny request per combo and reports what answered
# (spends a few hundred tokens; skipped under --dry-run).
# --drift is read-only: it compares the live combos with combos.json (name +
# ordered models; retired ids ignored), prints one line per difference and
# exits 0 in sync, 1 on drift, 3 when the live store is unreadable. It skips
# the provider, resilience and combo steps below.
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
DRY=0
PROBE=0
DRIFT=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --probe)   PROBE=1 ;;
        --drift)   DRIFT=1 ;;
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

# --drift reads no provider keys and never registers, so neither a missing
# key file nor a missing CLI stops it here: its own block below reports an
# unreadable store and exits 3.
if [[ ! -f "$KEYS_FILE" && $DRIFT -ne 1 ]]; then
    echo "Missing $KEYS_FILE — copy configuration/api-keys.example.yml and fill it in."
    [[ $DRY -eq 1 ]] || exit 1
fi
if ! command -v omniroute >/dev/null && [[ $DRIFT -ne 1 ]]; then
    if [[ $DRY -eq 1 ]]; then
        echo "OmniRoute CLI not installed - dry run continues with the static plan (nothing started, registered or created)."
    else
        echo "OmniRoute CLI not installed. Run: ./setup.sh --only omniroute --yes"
        exit 1
    fi
fi

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

# Provider registry: catalog/ai-registry.json's `providers` section is the
# single source of truth for which api-keys.yml name maps to which OmniRoute
# provider id and for the provider-specific connection data (the Cloudflare
# UA quirk on groq/cerebras). catalog/providers.json is deleted (task A5e);
# catalog/ai-registry.json is the only provider source. apply.ps1 reads the
# same registry file, so the two scripts cannot drift. Only providers with an
# omniroute_id are registered: meta (unregistered 2026-09-23, openrouter-
# first, no combo leg) and the omniroute client key carry none and are
# skipped, exactly as before. A provider every one of whose route legs the
# registry marks unavailable (routes.<id>.unavailable_legs,
# providers.<id>.available: false) is skipped too - registering a connection
# nothing can ever route to proves nothing and just leaves a dead entry.
REGISTRY_FILE="${AUTOOS_REGISTRY_FILE:-$ROOT/catalog/ai-registry.json}"
[[ -f "$REGISTRY_FILE" ]] || { echo "Missing $REGISTRY_FILE" >&2; exit 1; }
provider_rows="$(python3 - "$REGISTRY_FILE" <<'PY'
import json, sys

registry = json.load(open(sys.argv[1], encoding="utf-8"))
providers = registry.get("providers") or {}
routes = registry.get("routes") or {}


def resolve_provider_id(prefix):
    """A route leg's prefix names a providers key or any provider's
    omniroute_id (mirrors tools/registry.py's resolve_leg two-step match)."""
    if prefix in providers:
        return prefix
    for pid, entry in providers.items():
        if isinstance(entry, dict) and entry.get("omniroute_id") == prefix:
            return pid
    return None


def leg_is_unavailable(leg, route):
    """Mirrors tools/registry.py's _leg_is_unavailable(): a route's own
    unavailable_legs entry, or the leg's provider carrying available: false
    registry-wide (both are spec 3.1's two operator-facing "this is down"
    flags)."""
    unavailable_legs = route.get("unavailable_legs") or {}
    entry = unavailable_legs.get(leg)
    if isinstance(entry, dict) and entry.get("available") is False:
        return True
    pid = resolve_provider_id(leg.split("/", 1)[0])
    if pid is None:
        return False
    return (providers.get(pid) or {}).get("available") is False


# Every leg any route lists, grouped by the provider id it resolves to - used
# only to find a provider none of whose legs can ever be served.
legs_by_provider = {}
# key_name -> omniroute_id, to catch two connections claiming one api-keys.yml
# entry (the guard apply.ps1 has; see below).
by_key_name = {}
for route in routes.values():
    if not isinstance(route, dict):
        continue
    for leg in route.get("legs") or []:
        pid = resolve_provider_id(leg.split("/", 1)[0])
        if pid is not None:
            legs_by_provider.setdefault(pid, []).append((leg, route))

for name, entry in providers.items():
    provider_id = entry.get("omniroute_id")
    if not provider_id:
        continue  # meta (unregistered) and the omniroute client key
    legs = legs_by_provider.get(name) or []
    # A provider with no leg anywhere is not "every leg unavailable" (it is
    # simply unused elsewhere) - only a used provider whose every leg is down
    # is skipped here.
    if legs and all(leg_is_unavailable(leg, route) for leg, route in legs):
        print("SKIP\t%s" % provider_id)
        continue
    data = entry.get("provider_data")
    # One compact JSON string per provider: the exact argv value the CLI wants.
    data_json = json.dumps(data, separators=(",", ":")) if data else ""
    # The api-keys.yml entry the value is read from: the provider's own name,
    # unless key_name shares another provider's key (MUSEAPI step 4: meta_api
    # reads the meta key). Asking api-keys.yml for a meta_api nobody has would
    # print "no key in api-keys.yml" and never register the connection.
    # api-keys.yml keys are lower-cased when parsed above, so match that.
    key_name = (entry.get("key_name") or name).lower()
    claimed = by_key_name.get(key_name)
    if claimed is not None and claimed != provider_id:
        # MUSEAPI review F1: the same guard apply.ps1 raises. Two connections
        # from one api-keys.yml name means one of them silently never gets a
        # key, so the registry - not the operator with a half-configured
        # gateway - has to say so.
        print("ERR\tapi-keys.yml name '%s' is claimed by both %s and %s - "
              "give one of them its own key entry" % (key_name, claimed, provider_id))
        continue
    by_key_name[key_name] = provider_id
    print("ROW\t%s\t%s\t%s" % (key_name, provider_id, data_json))
PY
)" || { echo "apply.sh: cannot read $REGISTRY_FILE" >&2; exit 1; }
PROVIDER_MAP=()
PROVIDER_SKIPPED=()
REGISTERED_NOW=()
declare -A PROVIDER_DATA=()
while IFS=$'\t' read -r tag a b c; do
    [[ -z "$tag" ]] && continue
    if [[ "$tag" == SKIP ]]; then
        PROVIDER_SKIPPED+=("$a")
        continue
    fi
    if [[ "$tag" == ERR ]]; then
        echo "apply.sh: $a" >&2
        exit 1
    fi
    key_name="$a" provider_id="$b" data_json="$c"
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

# ─── --drift: compare the live combos with combos.json, read-only ───────────
# Nothing else in this script says whether the live store equals the file
# (OR1b). The live list comes from `omniroute --output json combo list`:
# {"combos":[{"name","models":[{"kind":"model","providerId","model"},...]}],
#  "active":...,"error":...} — a leg ref reconstructs as providerId/model, the
# spelling combos.json already uses (the CLI splits the leading provider/
# segment off a plain "provider/model" token when a combo is created).
# A combo listed in the file's "retired" ids is ignored on the live side; an
# "omitted" id (a route the registry renders no combo for) is an extra, but the
# reason is named so the operator sees it is an orphan apply will prune. Any
# other live combo absent from the file is reported, never touched.
if [[ $DRIFT -eq 1 ]]; then
    if ! command -v omniroute >/dev/null; then
        echo "drift: live store unreadable - the omniroute CLI is not installed"
        exit 3
    fi
    if ! gateway_up; then
        echo "drift: live store unreadable - the gateway does not answer on $GATEWAY"
        exit 3
    fi
    # Same banner strip as omni_json below (the CLI prints "Loaded env" lines
    # before the JSON document). Keep the captured stdout even on a non-zero
    # exit: the CLI answers a refused management call with the document
    # {"combos":[],...,"error":"HTTP 401"} AND exit 1, so the reason must come
    # from the body, not the exit code. The body reaches python on fd 3: a
    # herestring and the program heredoc cannot share stdin (the heredoc wins,
    # and python would then parse its own text as JSON).
    drift_failed=0
    if ! drift_raw="$(cd "$HOME" && omni --output json --no-color combo list 2>/dev/null \
            | sed -n '/^[[:space:]]*[{[]/,$p')"; then
        drift_failed=1
    fi
    drift_rc=0
    python3 - "$COMBOS_FILE" "$drift_failed" 3<<<"$drift_raw" <<'PY' || drift_rc=$?
import json, sys

try:
    with open("/dev/fd/3", encoding="utf-8") as fh:
        live_doc = json.load(fh)
except (OSError, ValueError):
    if sys.argv[2] == "1":
        print("drift: live store unreadable - the omniroute CLI could not reach the gateway")
    else:
        print("drift: live store unreadable - combo list output was not JSON")
    sys.exit(3)
if not isinstance(live_doc, dict):
    # A bare JSON list is not a combo document. The old single `not isinstance
    # (...) or live_doc.get("error")` guard short-circuited the condition but
    # then called .get() on the list in the body, so --drift exited 1 with a
    # traceback instead of this reason (OR1e).
    print("drift: live store unreadable - unexpected output")
    sys.exit(3)
if live_doc.get("error"):
    # The CLI answers a refused management call as {"error": "HTTP 401"}.
    # The status is a reason, never a key, so it is safe to show.
    print("drift: live store unreadable - %s" % live_doc["error"])
    sys.exit(3)

data = json.load(open(sys.argv[1], encoding="utf-8"))
retired = set(data.get("retired", []))
omitted = set(data.get("omitted", []))
file_combos = {c["name"]: list(c["models"]) for c in data.get("combos", [])}

live_combos = {}
for c in live_doc.get("combos") or []:
    name = c.get("name")
    if not name or name in retired:
        continue
    refs = []
    for step in c.get("models") or []:
        if isinstance(step, str):
            refs.append(step)
        elif isinstance(step, dict) and step.get("kind", "model") == "model":
            provider = step.get("providerId") or step.get("provider") or ""
            model = step.get("model") or ""
            # Live 3.8.51 (measured 2026-09-27) keeps the full ref in "model"
            # ("opencode-zen/muse-..."); the upstream source splits it off.
            if provider and not model.startswith(provider + "/"):
                model = "%s/%s" % (provider, model)
            refs.append(model)
    live_combos[name] = refs

rc = 0
for name, file_refs in file_combos.items():
    if name not in live_combos:
        print("missing %s" % name)
        rc = 1
    elif live_combos[name] != file_refs:
        print("drift %s: live=[%s] file=[%s]"
              % (name, ", ".join(live_combos[name]), ", ".join(file_refs)))
        rc = 1
for name in live_combos:
    if name not in file_combos:
        if name in omitted:
            print("extra %s (omitted: no servable leg)" % name)
        else:
            print("extra %s" % name)
        rc = 1
if rc == 0:
    # No "drift:" here: that prefix marks a difference line, and an in-sync
    # store must print none.
    print("in sync: live combos match %s (%d combos)" % (sys.argv[1], len(file_combos)))
sys.exit(rc)
PY
    exit "$drift_rc"
fi

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
        # The catalog step below plans from this list too: a dry run has to say
        # it would refresh what it would just have registered.
        REGISTERED_NOW+=("$provider_id")
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
        REGISTERED_NOW+=("$provider_id")
    else
        echo "  ! $provider_id registration failed — register it in the dashboard"
    fi
}

echo "Providers:"
# A provider whose every route leg is registry-unavailable (task A5a) is
# reported here and never touched below - no key lookup, no existing-id
# check, no register_provider call.
for provider_id in "${PROVIDER_SKIPPED[@]:-}"; do
    [[ -z "$provider_id" ]] && continue
    echo "  - $provider_id: all legs unavailable (skipped)"
done
# Connections that already exist are left alone: re-adding would either fail
# or duplicate them, and neither proves the pipeline works.
# A down gateway (dry run) has no list to read; the plan then comes from the
# key file alone instead of from whatever else answers the CLI.
existing_ids=""
if gateway_up; then
    existing_ids="$(omni providers list 2>/dev/null | grep -oE '^[[:space:]]*[0-9a-f]+[[:space:]]+[a-z0-9_-]+' | grep -oE '[a-z0-9_-]+$' || true)"
fi
for entry in "${PROVIDER_MAP[@]}"; do
    if grep -qxF "${entry#*:}" <<<"$existing_ids"; then
        echo "  = ${entry#*:} already registered"
        continue
    fi
    register_provider "${entry%%:*}" "${entry#*:}"
done

# ─── Refresh the gateway's model catalog ────────────────────────────────────
# Registering a connection does not by itself put that provider's models in
# /v1/models: the gateway enumerates a provider when it is asked for its models.
# Reading the catalog before that gave a freshly registered provider no entries
# to validate against, so its leg was dropped as "catalog does not know" and
# only appeared on the SECOND apply run (L0 2026-09-27T19:07:39Z, free-ai/qwen7b
# on a fresh machine). Order is therefore register -> refresh -> read -> combos.
echo "Catalog:"
if [[ $DRY -eq 1 ]]; then
    for provider_id in "${REGISTERED_NOW[@]:-}"; do
        [[ -z "$provider_id" ]] && continue
        echo "  - would refresh $provider_id's models (omniroute models $provider_id)"
    done
elif [[ ${#REGISTERED_NOW[@]} -eq 0 ]]; then
    echo "  = nothing new registered - the catalog is already current"
else
    for provider_id in "${REGISTERED_NOW[@]:-}"; do
        if omni models "$provider_id" >/dev/null 2>&1; then
            echo "  + $provider_id enumerated"
        else
            echo "  ! $provider_id could not be enumerated - its legs may be dropped below"
        fi
    done
fi

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
done < <(python3 - "$COMBOS_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for c in data.get("combos", []):
    print("%s\t%s\t%s" % (c["name"], c.get("strategy", "priority"), ",".join(c["models"])))
PY
)

# ─── Prune: delete the retired and omitted combos, and only those ───────────
# combos.json carries two lists of ids the live store should not hold:
# "retired" (a rename or removal left them behind; 9 orphans were deleted by
# hand on 2026-09-25) and "omitted" (OR1e: every route the render produced no
# combo for, because it has no gateway-servable leg). The loop above only
# creates and replaces by name, so such an id would otherwise stay in the store
# forever. A name is deleted only when it is in one of those lists AND live (and
# not a current combo): a store combo in neither list may be one the user made
# and is never touched.
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
# Each managed orphan is printed as "<kind>\t<name>" so the message names why
# it is pruned. The two lists are disjoint by construction, but the store is
# external: a name in both is printed once, "retired" first (the older fact).
seen = set()
for kind, ids in (("retired", data.get("retired", [])),
                  ("omitted", data.get("omitted", []))):
    for name in ids:
        if name in live and name not in current and name not in seen:
            seen.add(name)
            print("%s\t%s" % (kind, name))
' "$COMBOS_FILE")"; then
    echo "  ! cannot read the retired/omitted lists from $COMBOS_FILE - nothing pruned"
elif [[ -z "$prune_names" ]]; then
    echo "  = no retired or omitted combos in the store"
else
    while IFS=$'\t' read -r prune_kind prune_name; do
        [[ -z "$prune_name" ]] && continue
        if [[ $DRY -eq 1 ]]; then
            echo "  - $prune_name: $prune_kind, would delete"
        elif omni combo delete "$prune_name" --yes </dev/null >/dev/null 2>&1; then
            echo "  - $prune_name: $prune_kind, deleted"
        else
            echo "  ! $prune_name: $prune_kind, delete failed - run: omniroute combo delete $prune_name --yes"
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
