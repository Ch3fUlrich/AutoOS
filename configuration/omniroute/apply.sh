#!/usr/bin/env bash
# Apply the AutoOS router configuration to OmniRoute:
#   1. registers every provider key found in configuration/api-keys.yml: as a
#      built-in connection where the installed CLI knows the provider id, and as
#      an OpenAI-compatible provider node plus a connection bound to it where it
#      does not (a registry endpoint such as meta-api's, which `providers add`
#      alone has nothing to attach to)
#   2. refreshes the gateway's model catalog for what it just registered
#      (omniroute models <provider>), so one run is enough on a fresh machine
#   3. (re)creates the tier combos from configuration/omniroute/combos.json
#   4. prunes the combos listed there as "retired" from the store (only those)
#
# B2-VERTEX 2026-10-01: vertex authenticates from the GCP service-account JSON
# file configuration/vertex-credentials-<gcp-project>-<keyid>.json (the real
# project id and key id are placeholders here, this repository is public; full
# content is the credential, NOT a plain API key; git-ignored), not from
# api-keys.yml. Credential-store repair (two vertex/meta connections, one
# undecryptable 401) is L0's, not apply's.
#
# Safe to re-run: providers are add-or-update, a combo the store already holds
# unchanged is left alone, and a retired combo that is already gone is simply
# not found again. Model refs the live catalog does not know are skipped with a
# warning, so a renamed upstream model degrades one tier leg instead of breaking
# the run. "Already registered" is decided from the gateway's own connection
# list wherever a manage key makes it readable — the CLI's `providers list`
# answers from its local store, which on a dockerised host is not the gateway
# (see provider_already_registered). Only a connection the gateway would route
# to counts: a disabled one is reported for what the operator has to enable, and
# gets no second one. A combo whose re-create fails after the retry's delete is
# restored from the version read before it — or, when that version was not read
# whole, reported lost rather than restored short — never reported as untouched
# (see the Combos loop).
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
OVERRIDES_FILE="$HERE/context-overrides.json"
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

# Source the shared gateway key functions from install.sh
# shellcheck source=../../lib/linux/install.sh
. "$ROOT/lib/linux/install.sh"

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
        print("SKIP\x1f%s" % provider_id)
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
        print("ERR\x1fapi-keys.yml name '%s' is claimed by both %s and %s - "
              "give one of them its own key entry" % (key_name, claimed, provider_id))
        continue
    by_key_name[key_name] = provider_id
    # The provider's own OpenAI-compatible endpoint, if it has one: apply needs
    # it to register a gateway provider node for an id the CLI does not know
    # (see ensure_provider_node below). Fields are separated by 0x1f, not a tab:
    # bash's `read` treats tab as IFS whitespace and merges consecutive
    # separators, so a provider with no provider_data would shift api_base into
    # the data slot (and --provider-specific-data would carry a URL).
    print("ROW\x1f%s\x1f%s\x1f%s\x1f%s"
          % (key_name, provider_id, data_json, entry.get("api_base") or ""))
PY
)" || { echo "apply.sh: cannot read $REGISTRY_FILE" >&2; exit 1; }
PROVIDER_MAP=()
PROVIDER_SKIPPED=()
REGISTERED_NOW=()
declare -A PROVIDER_DATA=()
declare -A PROVIDER_BASE=()
while IFS=$'\x1f' read -r tag a b c e; do
    [[ -z "$tag" ]] && continue
    if [[ "$tag" == SKIP ]]; then
        PROVIDER_SKIPPED+=("$a")
        continue
    fi
    if [[ "$tag" == ERR ]]; then
        echo "apply.sh: $a" >&2
        exit 1
    fi
    key_name="$a" provider_id="$b" data_json="$c" api_base="$e"
    PROVIDER_MAP+=("$key_name:$provider_id")
    if [[ -n "$data_json" ]]; then
        PROVIDER_DATA["$provider_id"]="$data_json"
    fi
    if [[ -n "$api_base" ]]; then
        PROVIDER_BASE["$provider_id"]="$api_base"
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

# omni_json: a management read through the CLI, with the "Loaded env" banners the
# CLI prints on stdout before the JSON document stripped off (the strip the
# resilience read below relies on). Keep the captured stdout even on a non-zero
# exit: the CLI answers a refused management call with the document
# {"combos":[],"error":"HTTP 401"} AND exit 1, so the reason must come from the
# body, not the exit code — callers pass the failure flag on to live_combo_norm.
omni_json() {
    (cd "$HOME" && omni --output json --no-color "$@" 2>/dev/null) | sed -n '/^[[:space:]]*[{[]/,$p'
}

# ─── The one live-combo normaliser ──────────────────────────────────────────
# Both readers of the live store — --drift and the Combos loop — answer the same
# question: what does the gateway hold for this name. One normaliser, never a
# second copy of the step shape.
# $1 is the raw `omniroute --output json --no-color combo list` document, whose
# shape was measured on 3.8.51 on 2026-09-27 and recorded by the --drift comment
# as it stood before this change (59aa3a9 apply.sh:252-253): {"combos":
# [{"name","models":[{"kind":"model","providerId","model"},...]}],"active":...,
# "error":...}. A leg ref reconstructs as providerId/model, the spelling
# combos.json uses, because the CLI splits the leading provider/ segment off a
# plain "provider/model" token on create.
# That measurement names no "strategy" field, although the plain `combo list`
# table prints one per row (the "<name> [<strategy>] <status>" shape recorded at
# apply.ps1:447, which the test stand-in renders the same way in _prune_list) and
# the --drift stand-in renders it in the JSON document too (_drift_json). So this
# prints the field when the document carries it and an empty column when it does
# not, and every caller treats an empty column as "the store does not report a
# strategy" — refs alone decide. A caller that assumed the field is present would
# report every combo as changed on the shape we actually measured.
# (Cited by function name, not line number: the test file grows at the bottom.)
# $1 is on fd 3 because a herestring and the program heredoc cannot share stdin
# (the heredoc wins, and python would then parse its own text as JSON).
# Prints one line per live combo:
# "<name>\x1f<strategy>\x1f<ref,ref,...>\x1f<dropped-steps>", in the store's
# order. The fourth column is how many of the store's own steps could not be
# turned into a ref, so a caller that means to write the tier back can tell a
# faithful copy from a short one. The field separator is the unit-separator
# byte, not a tab: bash's `read` treats tab as an IFS *whitespace* character, so
# a line with an empty middle field has its two separators collapsed into one
# delimiter and the refs land in the strategy variable - measured with the
# strategy-less live shape this change had to support. A non-whitespace
# separator keeps an empty field empty. Exit 0 on a readable document (possibly
# zero combos), 3 with a one-line reason on stdout when the store is unreadable —
# the caller adds its own prefix and chooses its own fallback.
live_combo_norm() {
    python3 - "${2:-0}" 3<<<"$1" <<'PY'
import json, sys

try:
    with open("/dev/fd/3", encoding="utf-8") as fh:
        live_doc = json.load(fh)
except (OSError, ValueError):
    if sys.argv[1] == "1":
        print("live store unreadable - the omniroute CLI could not reach the gateway")
    else:
        print("live store unreadable - combo list output was not JSON")
    sys.exit(3)
if not isinstance(live_doc, dict):
    # A bare JSON list is not a combo document. The old single `not isinstance
    # (...) or live_doc.get("error")` guard short-circuited the condition but
    # then called .get() on the list in the body, so --drift exited 1 with a
    # traceback instead of this reason (OR1e).
    print("live store unreadable - unexpected output")
    sys.exit(3)
if live_doc.get("error"):
    # The CLI answers a refused management call as {"error": "HTTP 401"}.
    # The status is a reason, never a key, so it is safe to show.
    print("live store unreadable - %s" % live_doc["error"])
    sys.exit(3)

for c in live_doc.get("combos") or []:
    if not isinstance(c, dict) or not c.get("name"):
        continue
    name = c["name"]
    refs = []
    dropped = 0
    steps = c.get("models") or []
    if not isinstance(steps, list):
        # A "models" field that is not a list cannot be read leg by leg. Report
        # the combo with no refs and one unreadable step, so a caller sees "I do
        # not know what this tier held" rather than "this tier was empty" — the
        # second reading would let a restore write an empty combo back.
        print("%s\x1f%s\x1f%s\x1f1" % (name, c.get("strategy") or "", ""))
        continue
    for step in steps:
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
        else:
            # A step this script has no shape for (another "kind"). It is NOT
            # silently a leg that does not exist: the fourth column counts the
            # legs a caller must not treat as the whole tier — see the Combos
            # loop's restore, which refuses to write back a short combo.
            dropped += 1
    print("%s\x1f%s\x1f%s\x1f%d" % (name, c.get("strategy") or "",
                                    ",".join(refs), dropped))
PY
}

# ─── --drift: compare the live combos with combos.json, read-only ───────────
# Nothing else in this script says whether the live store equals the file
# (OR1b). A combo listed in the file's "retired" ids is ignored on the live
# side; an "omitted" id (a route the registry renders no combo for) is an
# extra, but the reason is named so the operator sees it is an orphan apply will
# prune. Any other live combo absent from the file is reported, never touched.
if [[ $DRIFT -eq 1 ]]; then
    if ! command -v omniroute >/dev/null; then
        echo "drift: live store unreadable - the omniroute CLI is not installed"
        exit 3
    fi
    if ! gateway_up; then
        echo "drift: live store unreadable - the gateway does not answer on $GATEWAY"
        exit 3
    fi
    # The same read the Combos loop makes — one CLI call, one normaliser, so the
    # two can never disagree about what the store holds.
    drift_failed=0
    if ! drift_raw="$(omni_json combo list)"; then
        drift_failed=1
    fi
    if ! drift_live="$(live_combo_norm "$drift_raw" "$drift_failed")"; then
        echo "drift: $drift_live"
        exit 3
    fi
    drift_rc=0
    python3 - "$COMBOS_FILE" 3<<<"$drift_live" <<'PY' || drift_rc=$?
import json, sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
retired = set(data.get("retired", []))
omitted = set(data.get("omitted", []))
file_combos = {c["name"]: list(c["models"]) for c in data.get("combos", [])}

# The normalised live store, one "<name>\x1f<strategy>\x1f<refs>\x1f<dropped>"
# line per combo (unit-separated - see live_combo_norm). Neither the strategy nor
# the dropped-step count is compared here: --drift reports the leg order, which
# is what a re-run of apply can silently change, and a combo with an unreadable
# step has refs that already differ from the file's, so it is reported. Extra
# columns are tolerated - the shape the normaliser prints is its own business.
live_combos = {}
for line in open("/dev/fd/3", encoding="utf-8").read().splitlines():
    cols = line.split("\x1f")
    if len(cols) < 3:
        continue
    name, _strategy, refs = cols[0], cols[1], cols[2]
    if not name or name in retired:
        continue
    live_combos[name] = [ref for ref in refs.split(",") if ref]

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
        # Raise the chat admission heavy-in-flight limit from the default of 1.
        # Default 1 + 1 healthy-headroom = 2 max concurrent heavy requests; a 3rd
        # concurrent heavy stream gets 503 chat_admission_busy. 8 gives headroom
        # for parallel agents (swarm, multi-lane) without over-allocating heap.
        # Respect a user-set value — do not clobber.
        : "${OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT:=8}"
        export OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT
        echo "Starting OmniRoute (background)…"
        # Sane skip-on-repeated-429 policy (operator 2026-10-01): the gateway reads
        # these from its own process env at startup (open-sse/services/rotationConfig.ts:84-110;
        # provider-breaker family at open-sse/config/constants.ts:251-277), so the
        # launcher that spawns it is the only surface. Rotate a leg only after three
        # 429s inside a 120s window (the shipped default of 1 hops on the first
        # 429), then cool the leg for 300s. Respect-set: an operator value wins.
        export OMNIROUTE_ROTATION_ENABLED="${OMNIROUTE_ROTATION_ENABLED:-true}"
        export OMNIROUTE_ROTATE_ON_429="${OMNIROUTE_ROTATE_ON_429:-true}"
        export OMNIROUTE_ROTATE_429_THRESHOLD="${OMNIROUTE_ROTATE_429_THRESHOLD:-3}"
        export OMNIROUTE_ROTATE_429_WINDOW_SECONDS="${OMNIROUTE_ROTATE_429_WINDOW_SECONDS:-120}"
        export OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS="${OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS:-300}"
        export OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS="${OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS:-1800000}"
        nohup omniroute --no-open --port 20128 >/tmp/omniroute-apply.log 2>&1 &
        for _ in $(seq 1 24); do gateway_up && break; sleep 5; done
        gateway_up || { echo "Gateway did not start — run: omniroute doctor"; exit 1; }
    fi
fi
if gateway_up; then echo "Gateway OK on $GATEWAY"; fi

# ─── Register providers ─────────────────────────────────────────────────────
# The manage key for the REST calls below: the same one omni() puts into the
# CLI's environment. It never goes in argv, where `ps` can read it for the
# lifetime of the call.
REST_KEY="${OMNIROUTE_API_KEY:-$MANAGE_KEY}"

# redact_secrets - read stdin and replace every argument (a secret value) with
# [REDACTED]. A quoted bash pattern matches literally, so a key that contains
# glob characters is stripped instead of being read as a pattern.
redact_secrets() {
    local text secret
    text="$(cat)"
    for secret in "$@"; do
        [[ -z "$secret" ]] && continue
        text="${text//"$secret"/[REDACTED]}"
    done
    printf '%s\n' "$text"
}

# print_cli_error <text> [secret...] - the failure's own words on the log, with
# every secret stripped and the CLI's ANSI banner removed. Swallowing this is
# what made the meta-api failure undiagnosable (routing-00 04:5xZ): the reason
# went to /dev/null and only "register it in the dashboard" came out.
print_cli_error() {
    local text="$1"
    shift
    [[ -z "$text" ]] && return 0
    LC_ALL=C tr -d '\000-\010\013-\037' <<<"$text" |
        redact_secrets "$@" | head -n 3 | cut -c 1-200 | sed 's/^/      /' || true
}

# BUILTIN_IDS - the provider ids and aliases the installed CLI knows, read once
# per run and cached. `omniroute providers available` lists 352 of them
# (measured 2026-09-28 against omniroute 3.8.51: openai, meta-llama, free-ai,
# opencode-zen and 348 more); deciding this per provider would ask the CLI once
# per registry entry. Aliases count too, because the gateway rejects a provider
# node whose prefix collides with one (omniroute
# src/shared/constants/reservedProviderPrefixes.ts).
BUILTIN_IDS=""
BUILTIN_READ=0
builtin_provider() {
    local id="$1"
    if [[ $BUILTIN_READ -eq 0 ]]; then
        BUILTIN_READ=1
        # Same banner strip as the --drift block: the CLI prints its .env lines
        # before the JSON document.
        BUILTIN_IDS="$(omni providers available --json 2>/dev/null |
            sed -n '/^[[:space:]]*[{[]/,$p' |
            python3 -c 'import json,sys
try:
    doc = json.load(sys.stdin)
except Exception:
    sys.exit(0)
rows = doc.get("providers") if isinstance(doc, dict) else None
ids = []
for p in rows or []:
    if isinstance(p, dict):
        for v in (p.get("id"), p.get("alias")):
            if isinstance(v, str) and v:
                ids.append(v)
print("\n".join(dict.fromkeys(ids)))' 2>/dev/null || true)"
        if [[ -z "$BUILTIN_IDS" ]]; then
            echo "  ! the CLI's provider catalog is unreadable - treating every id as built-in" >&2
        fi
    fi
    # Unreadable catalog: keep the behaviour this script had before - everything
    # is a built-in. Guessing the other way would create a provider node for
    # every registry entry on a machine where the CLI simply did not answer.
    [[ -z "$BUILTIN_IDS" ]] && return 0
    grep -qxF "$id" <<<"$BUILTIN_IDS"
}

# provider_needs_node - true for a registry provider with an api_base of its own
# whose omniroute_id the CLI has no built-in connection for. Such an endpoint
# only reaches the gateway as a PROVIDER NODE: `providers add meta-api` had
# nothing to attach the key to, which is the live failure this block fixes.
provider_needs_node() {
    local provider_id="$1"
    [[ -n "${PROVIDER_BASE[$provider_id]:-}" ]] || return 1
    builtin_provider "$provider_id" && return 1
    return 0
}

# omni_rest <METHOD> <path> [json-body] [bearer-key] - one gateway call with a
# bearer token. The key defaults to the manage key (REST_KEY); the catalog read
# passes the omniroute *client* key, which authorises /v1/models. Either way the
# token goes in a private curl config file and the request body on stdin, never
# in argv - `ps` shows argv to every user on the machine for the lifetime of the
# call. The response lands in REST_BODY (empty on a failure), the reason in
# REST_ERROR, redacted by the caller before it reaches the log.
# Both are globals, not stdout, on purpose: a caller that read the body with
# $( ) would run this function in a subshell, and the two temp files would then
# belong to a shell that an interrupted run cannot reach - which is exactly how
# the key outlived the call (MUSEFIX). A caller that needs the failure's reason
# has to be in this shell too, for the same reason.
REST_ERROR=""
REST_BODY=""
omni_rest() {
    local method="$1" path="$2" body="${3:-}" key="${4:-$REST_KEY}"
    local cfg out code escaped
    REST_BODY=""
    cfg="$(mktemp)" || { REST_ERROR="no curl config file could be created"; return 1; }
    out="$(mktemp)" || { rm -f -- "$cfg"; REST_ERROR="no response file could be created"; return 1; }
    chmod 600 "$cfg" "$out"
    escaped="${key//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    printf 'header = "Authorization: Bearer %s"\n' "$escaped" >"$cfg"
    # The config file holds the manage key and the response file holds whatever
    # the gateway answered - GET /api/providers returns every provider's live
    # apiKey. Removing them after the call only covers the path that gets there:
    # a run interrupted (Ctrl-C, or killed) while the call was in flight left a
    # 0600 file carrying the key in /tmp on the user's machine. So the removal
    # is trapped for the duration of the call and the trap cleared (back to no
    # trap - this script sets none of its own) right after, the same shape
    # ai-stack.sh uses for its edge-webhook header file.
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
        REST_ERROR="HTTP $code for $path: $(head -c 300 <<<"$REST_BODY")"
        REST_BODY=""
        return 1
    fi
}

# provider_node_id <prefix> - sets NODE_ID to the id of the provider node
# carrying this prefix, or to nothing. GET /api/provider-nodes answers
# {"nodes":[…],"total":N} (measured live 2026-09-28); {"items":[…]} and a bare
# list are tolerated. A global rather than stdout, and called without $( ), for
# the reason in omni_rest's comment.
NODE_ID=""
provider_node_id() {
    NODE_ID=""
    local doc=""
    if omni_rest GET /api/provider-nodes; then
        doc="$REST_BODY"
    fi
    [[ -n "$doc" ]] || return 0
    NODE_ID="$(python3 -c 'import json,sys
try:
    doc = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(doc, dict):
    rows = doc.get("nodes") or doc.get("items") or []
else:
    rows = doc
for n in rows or []:
    if isinstance(n, dict) and n.get("prefix") == sys.argv[1]:
        print(n.get("id") or "")
        break
' "$1" <<<"$doc" || true)"
}

# omni_connections - read the gateway's connection list once and put it in
# CONNECTION_ROWS as "<usable>\x1f<provider>\x1f<name>" lines, one per
# connection, where usable is 1 for a connection the gateway would route to and
# 0 for one the dashboard has disabled (isActive: false). Both readers of the
# list — the "already registered" set and the node-bound connection check — are
# then one parse and cannot disagree about what a row means.
# Exit 1 with the reason in REST_LIST_REASON when the gateway refused, answered
# something that is not the documented document, or served a PARTIAL page:
# {"connections":[…],"total":N} with N beyond the rows it sent means the list is
# a slice, and deciding "not registered" from a slice would add a duplicate
# connection on the operator's machine. Exit 0 for a readable list, including an
# empty one — a gateway with no connections holds none, that is an answer.
# The body carries every stored apiKey, so REST_BODY is cleared the moment the
# parse has consumed it: a later failure path that prints a captured body must
# not find this one still set.
CONNECTION_ROWS=""
REST_LIST_REASON=""
omni_connections() {
    CONNECTION_ROWS=""
    REST_LIST_REASON=""
    if ! omni_rest GET '/api/providers?limit=5000'; then
        REST_LIST_REASON="the gateway's connection list is unreadable"
        return 1
    fi
    local rc=0
    CONNECTION_ROWS="$(python3 -c 'import json,sys
try:
    doc = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if isinstance(doc, dict):
    rows = doc.get("connections")
    total = doc.get("total")
else:
    rows = doc
    total = None
if not isinstance(rows, list):
    sys.exit(1)
if isinstance(total, bool) or not isinstance(total, int):
    total = len(rows)
if total > len(rows):
    # A slice, not the list.
    sys.exit(2)
for c in rows:
    if not isinstance(c, dict):
        continue
    provider = c.get("provider")
    if not isinstance(provider, str) or not provider:
        continue
    name = c.get("name") if isinstance(c.get("name"), str) else ""
    # Only an explicit false is disabled — a document that omits the field
    # says nothing about it. This is the flag the dashboard toggle writes.
    print("%s\x1f%s\x1f%s" % (0 if c.get("isActive") is False else 1,
                              provider, name))
' <<<"$REST_BODY")" || rc=$?
    REST_BODY=""
    if [[ $rc -eq 0 ]]; then
        return 0
    fi
    CONNECTION_ROWS=""
    if [[ $rc -eq 2 ]]; then
        REST_LIST_REASON="the gateway's connection list is partial"
    else
        REST_LIST_REASON="the gateway's connection list is unreadable"
    fi
    return 1
}

# provider_connection_exists <node-id> <provider-id> - is a usable key already
# bound to this node? `omniroute providers list` cannot answer it: its second
# column is the provider, and a node-bound connection's provider is the node's
# "<type>-<uuid>" id, which apply's hex-id scan never matches (omniroute
# src/lib/db/providers/nodes.ts:61-71 says a new connection carries exactly
# that concrete id).
# WHAT MAY BE ADOPTED: a connection counts only when its provider IS this node.
# A name match on its own cannot be trusted — the name apply gives a connection
# is the registry id, and the gateway can hold a connection of that name bound
# to some OTHER node (a renamed or re-created provider node leaves one behind),
# which would let apply report the provider as registered while its key sits on
# an endpoint the registry does not name. So the name is only decisive for a
# connection that carries this node's id, or the registry id itself as its
# provider.
# A row that matches but is disabled sets CONNECTION_INACTIVE and still returns
# 1: an inactive connection routes nothing, so it is not "already registered",
# and the caller has to be able to say why it is adding no second one either.
CONNECTION_INACTIVE=0
provider_connection_exists() {
    local node_id="$1" provider_id="$2" usable provider name matched
    CONNECTION_INACTIVE=0
    omni_connections || return 1
    while IFS=$'\x1f' read -r usable provider name; do
        [[ -z "$provider$usable" ]] && continue
        matched=0
        [[ "$provider" == "$node_id" ]] && matched=1
        if [[ $matched -eq 0 && "$name" == "$provider_id" &&
             "$provider" == "$provider_id" ]]; then
            matched=1
        fi
        [[ $matched -eq 1 ]] || continue
        if [[ "$usable" == 1 ]]; then
            return 0
        fi
        CONNECTION_INACTIVE=1
    done <<<"$CONNECTION_ROWS"
    return 1
}

# rest_provider_ids - the gateway's own answer to "which connections exist".
# Sets REST_CONNECTION_IDS to the provider field of every USABLE connection and
# REST_INACTIVE_IDS to the provider field of every disabled one, one per line.
# Exit 1 on anything omni_connections refuses (a refused read, a malformed
# document, a partial page) so the caller falls back to the CLI's list and says
# which of the three it is dealing with through REST_LIST_REASON.
REST_CONNECTION_IDS=""
REST_INACTIVE_IDS=""
rest_provider_ids() {
    REST_CONNECTION_IDS=""
    REST_INACTIVE_IDS=""
    omni_connections || return 1
    local usable provider name
    while IFS=$'\x1f' read -r usable provider name; do
        [[ -z "$provider$usable" ]] && continue
        if [[ "$usable" == 1 ]]; then
            REST_CONNECTION_IDS+="${provider}"$'\n'
        else
            REST_INACTIVE_IDS+="${provider}"$'\n'
        fi
    done <<<"$CONNECTION_ROWS"
    return 0
}

# ensure_provider_node <provider-id> - sets the globals NODE_NOTE ("created" or
# "existing") and NODE_ID, so the caller can say which one it was. Globals, not
# stdout, and called without $( ): the REST calls below own the two temp files
# and write the failure's reason into REST_ERROR, and both belong to the shell
# that runs them - a subshell would hide the reason from the log and put the
# files out of reach of the interrupt trap. Creates the node only when the
# gateway has no node with that prefix - GET first, or a re-run adds a second
# one.
NODE_NOTE=""
ensure_provider_node() {
    local provider_id="$1" body
    NODE_NOTE=""
    provider_node_id "$provider_id"
    if [[ -n "$NODE_ID" ]]; then
        NODE_NOTE="existing"
        return 0
    fi
    # createProviderNodeSchema, omniroute
    # src/shared/validation/schemas/provider.ts:307-385: it demands name and
    # prefix, an apiType for type "openai-compatible", and a baseUrl only for
    # the "vibeproxy-openai" preset - every other field is optional, so the
    # gateway would accept this body without it. apply sends baseUrl anyway: a
    # node without the endpoint the registry names is a node nobody can route
    # to. The CLI's own POST sends no body at all (bin/cli/api-commands/
    # provider-nodes.mjs:18-25), so this is the REST call, not `omniroute api`.
    body="$(python3 -c 'import json,sys
print(json.dumps({"name": sys.argv[1], "prefix": sys.argv[1],
                  "type": "openai-compatible", "apiType": "chat",
                  "baseUrl": sys.argv[2]}))' "$provider_id" "${PROVIDER_BASE[$provider_id]}")" || {
        REST_ERROR="the provider node request could not be built"
        return 1
    }
    if ! omni_rest POST /api/provider-nodes "$body"; then
        return 1
    fi
    # The created node is read back rather than trusted from the POST response:
    # the response shape is not the documented contract, the prefix is.
    provider_node_id "$provider_id"
    if [[ -z "$NODE_ID" ]]; then
        REST_ERROR="the gateway accepted the node but does not list it"
        return 1
    fi
    NODE_NOTE="created"
}

register_provider() {
    local key_name="$1" provider_id="$2" value="${KEYS[$1]:-}" node_exists=0
    if [[ -z "$value" ]]; then
        echo "  - $provider_id: no key in api-keys.yml, skipped"
        return 0
    fi
    if [[ $DRY -eq 1 ]]; then
        # Say how the connection would be made, not only that it would be: an
        # id with no built-in needs its provider node first. Reading the CLI's
        # catalog and the gateway's node list is a read; the dry run creates no
        # node and adds no key. A node the gateway already holds is not "would
        # create" — the plan must not promise to create what exists (APPLYADOPT).
        if gateway_up && provider_needs_node "$provider_id"; then
            if [[ -n "$REST_KEY" ]]; then
                provider_node_id "$provider_id"
                [[ -n "$NODE_ID" ]] && node_exists=1
            fi
            if [[ $node_exists -eq 1 ]]; then
                echo "  - $provider_id: would add the key to its existing provider node" \
                    "(${PROVIDER_BASE[$provider_id]})"
            else
                echo "  - $provider_id: would create its provider node (OpenAI-compatible" \
                    "endpoint ${PROVIDER_BASE[$provider_id]}) and add the key to it"
            fi
        fi
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
    local add_id="$provider_id" node_id="" err="" rc=0
    local name_args=()
    if provider_needs_node "$provider_id"; then
        if [[ -z "$REST_KEY" ]]; then
            echo "  ! $provider_id is not a built-in provider - it needs a gateway provider node"
            echo "      and no manage key is available (OMNIROUTE_API_KEY, or the ai-stack"
            echo "      manage.key): register it in the dashboard"
            return 0
        fi
        # No $( ) here: the reason has to survive in REST_ERROR.
        if ! ensure_provider_node "$provider_id"; then
            echo "  ! $provider_id provider node could not be created — register it in the dashboard"
            print_cli_error "${REST_ERROR:-the gateway refused the request}" "$value" "$REST_KEY"
            return 0
        fi
        node_id="$NODE_ID"
        if [[ "$NODE_NOTE" == "created" ]]; then
            echo "  + $provider_id: provider node created (prefix $provider_id -> ${PROVIDER_BASE[$provider_id]})"
        fi
        if provider_connection_exists "$node_id" "$provider_id"; then
            echo "  = $provider_id already registered"
            return 0
        fi
        # Reached when the loop's check could not ask the gateway (no REST list
        # this run) and this fresh read could: the key is bound to the node, but
        # disabled. Adding a second connection is not the fix.
        if [[ $CONNECTION_INACTIVE -eq 1 ]]; then
            report_inactive_connection "$provider_id"
            return 0
        fi
        # The key binds to the node, and the connection keeps the registry's
        # name so the gateway UI and --drift both read "meta-api".
        add_id="$node_id"
        name_args=(--name "$provider_id")
    fi
    # Subshell export, not `env VAR=value`: env would put the key in argv,
    # where `ps` can read it for the lifetime of the call.
    err="$( { ( export "$var=$value"; omni providers add "$add_id" \
            "${name_args[@]}" --credential-env "$var" "${data_args[@]}" --yes ) \
            2>&1 1>/dev/null; } )" || rc=$?
    if [[ $rc -eq 0 ]]; then
        echo "  + $provider_id registered"
        REGISTERED_NOW+=("$provider_id")
    else
        echo "  ! $provider_id registration failed — register it in the dashboard"
        print_cli_error "$err" "$value" "$REST_KEY"
    fi
}

# provider_already_registered <provider-id> - does the gateway hold a USABLE
# connection for this id already, so re-adding it would only duplicate it?
# WHERE the answer comes from is the whole point. `omniroute providers list`
# reads the CLI's own ~/.omniroute store, which on the dockerised host is not the
# gateway: measured 2026-09-29, the CLI listed 14 connections while the gateway's
# REST list held 32 (most of them named "main"). Deciding from the CLI therefore
# re-added scaleway, nebius, free-ai, bazaarlink, navy, arcee-ai, bluesminds,
# agentrouter, novita and together on a real operator's machine, and a dry run
# planned a provider node that already existed. With a manage key the existing
# set is therefore read from the gateway once for the whole loop
# (EXISTING_FROM_REST, set where $existing_ids is read below), and a node
# provider — whose connection's `provider` field is the node's "<type>-<uuid>"
# id, never the registry id, so an id match cannot see it — is decided by its
# node plus the connection bound to it. The CLI's list stays the fallback for a
# run that cannot ask the gateway: no manage key (a local gateway, where the
# CLI's store is the gateway's own), or a connection read the gateway refused or
# only served part of (rest_provider_ids). A connection the dashboard has
# disabled answers "not registered" and sets CONNECTION_INACTIVE, which is how
# the caller tells "add it" from "enable the one you have".
provider_already_registered() {
    local provider_id="$1"
    CONNECTION_INACTIVE=0
    if [[ $EXISTING_FROM_REST -eq 1 ]]; then
        if provider_needs_node "$provider_id"; then
            provider_node_id "$provider_id"
            [[ -n "$NODE_ID" ]] || return 1
            provider_connection_exists "$NODE_ID" "$provider_id"
        else
            if grep -qxF "$provider_id" <<<"$REST_CONNECTION_IDS"; then
                return 0
            fi
            # Not usable, but present: say so through the same flag the
            # node-bound path sets, so the caller reports one problem, not two.
            grep -qxF "$provider_id" <<<"$REST_INACTIVE_IDS" && CONNECTION_INACTIVE=1
            return 1
        fi
        return
    fi
    grep -qxF "$provider_id" <<<"$existing_ids"
}

# report_inactive_connection <provider-id> - the one place that says what an
# unusable connection is and what fixes it. Called instead of "already
# registered" (it is not: nothing routes) and instead of registering (a second
# connection would not enable the first, it would only leave the gateway with a
# row to delete).
report_inactive_connection() {
    echo "  ! $1 has an inactive connection - enable it in the dashboard"
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
EXISTING_FROM_REST=0
if gateway_up; then
    if [[ -n "$REST_KEY" ]] && rest_provider_ids; then
        EXISTING_FROM_REST=1
    else
        if [[ -n "$REST_KEY" ]]; then
            # Say which source decided, and why this one was not the gateway's:
            # the CLI's store can disagree with the gateway, and an operator
            # reading a duplicate connection afterwards needs to know this run
            # could not ask the gateway itself — or got only half an answer
            # (rest_provider_ids: a partial page is refused for that reason).
            # The gateway's own words are not printed — the refusal body of this
            # endpoint is not guaranteed to be key-free.
            # The reason goes through a variable rather than `${VAR:-default}`:
            # bash's parser reads the apostrophe in the default as an unclosed
            # quote inside a double-quoted string and the whole script fails to
            # parse. (A quoted default in an assignment is fine, hence below.)
            fallback_reason="$REST_LIST_REASON"
            [[ -n "$fallback_reason" ]] || fallback_reason="the gateway's connection list is unreadable"
            echo "  ! $fallback_reason - deciding from the CLI's local store instead" >&2
        fi
        existing_ids="$(omni providers list 2>/dev/null | grep -oE '^[[:space:]]*[0-9a-f]+[[:space:]]+[a-z0-9_-]+' | grep -oE '[a-z0-9_-]+$' || true)"
    fi
fi
for entry in "${PROVIDER_MAP[@]}"; do
    if provider_already_registered "${entry#*:}"; then
        echo "  = ${entry#*:} already registered"
        continue
    fi
    if [[ $CONNECTION_INACTIVE -eq 1 ]]; then
        report_inactive_connection "${entry#*:}"
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

# TORDER 2026-10-01: combo-contract gate (fail-closed, also under --dry-run).
if ! python3 "$ROOT/tools/combo-contract.py"; then
  echo "combo-contract failed - refusing to create combos" >&2
  exit 1
fi

# ─── (Re)create combos ──────────────────────────────────────────────────────
# The gateway's model catalog, read once. The omniroute *client* key authorises
# /v1/models, and it goes to omni_rest — which writes a 0600 curl --config file
# and removes it on every path, interrupted included — because `curl -H
# "Authorization: Bearer <key>"` puts the key in argv, where every user on the
# machine can read it out of `ps` for the lifetime of the call.
live_ids=""
# The client key is resolved ONCE per run, by the one rule in tools/autoos_gateway_key.py. Every
# later use (the combo catalog read, the --probe) reads $_client_key: each resolve is a separate
# python process, so a second one would print the legacy-field deprecation line a second time.
# stderr is let through so that line and a missing-key error are visible.
_client_key="$(autoos_resolve_client_key "$KEYS_FILE" || true)"
if command -v python3 >/dev/null; then
    if omni_rest GET /v1/models "" "$_client_key"; then
        live_ids="$(python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for m in d.get("data", []):
    print(m.get("id", ""))' <<<"$REST_BODY" || true)"
    fi
    REST_BODY=""
fi
if [[ -z "$live_ids" ]]; then
    echo "  ! could not read /v1/models (check the omniroute client key in api-keys.yml)"
    echo "    combos will be created without catalog validation - verify with: omniroute simulate --combo l1-orchestrator"
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
# Fast failover (CTXFIX 2026-09-30): the breaker IS the fast-skip mechanism,
# not maxWaitMs. degradationThreshold=1 hops on the first degradation signal
# (a 429 counts), failureThreshold=2 opens the breaker after two hard
# failures - at most two cheap round-trips before the leg is skipped for
# resetTimeoutMs. maxWaitMs=180000 is the queue wait (Spark thinking time),
# not the per-leg wait; a rate-limited leg returns 429 in < 1 s and the
# chain hops immediately. No value change needed for this lane.
# B2-LATENCY 2026-10-01 (t1 22556 ms crawl: gemini 503 -> free-ai 429 ->
# vertex error -> meta-api 401 before deepseek served): live
# get-api-resilience shows requestQueue.maxWaitMs 180000 (queue wait, NOT
# per-leg), providerBreaker.apikey 2/1/30000, oauth 8/5/60000,
# connectionCooldown apikey base 3000/maxBackoff 5 (oauth 5000/8),
# waitForCooldown 3 retries/30 s, comboCooldownWait 90 s/5 attempts/300 s budget,
# providerCooldown DISABLED. No per-leg timeout knob exists in this API surface
# (no legTimeoutMs); the 30-min hard-down park + 3x429/120 s->300 s cooldown +
# CHAT_MAX_HEAVY=8 are gateway process env (08:07Z restart), not patchable here.
# So no wiring change: current == proposed (180000 / 2 / 1 / 30000). The crawl
# stops by REMOVING dead legs (B2-HF/B2-AGY: huggingface 401 + antigravity
# rate-limited gone from bands), shortening the chain, not by retuning.
# Through the CLI, not curl + the client key: /api/resilience is a management
# route and answers the client key with 403 "Invalid management token"
# (measured 2026-09-24); the local CLI sends the machine loopback token.
# It runs from $HOME so it never picks up a .env in the caller's cwd.
MAX_WAIT_MS=180000
BREAKER_THRESHOLD=2
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

# ─── What the store already holds, read once for the whole loop ─────────────
# Creating a combo the store already has is not a no-op: `omni combo create`
# fails on an existing name, so the retry path below deleted and re-created it.
# A re-run against a gateway --drift reports in sync therefore announced
# "+ <name> replaced" for every combo (measured 2026-09-28: 14 of them) and each
# delete+create is a window in which the tier does not exist — a destructive
# action that nobody opted into (AGENTS.md hard rule 3). AGENTS.md §4 sets the
# bar: a second run reports the no-op, never a change. So compare with the store
# first and leave an unchanged combo alone. Same CLI call and the same normaliser
# as --drift, so the two can never disagree about what "in sync" means.
# A missing CLI is not read at all — the create attempts report it. A CLI that
# is there but cannot answer the list is the unreadable case: today's behaviour
# for every combo, announced once.
# --dry-run reads too. It is the same read-only call, and a plan that cannot say
# which combos it would leave alone would have to claim it plans to rewrite every
# one of them — which is the lie this section exists to stop. Nothing here writes.
declare -A LIVE_COMBO_REFS=()
declare -A LIVE_COMBO_STRATEGY=()
# How many of the store's own steps the normaliser could not read, per combo.
# A combo with a non-zero count is a tier this script cannot copy back faithfully
# — the retry path refuses to restore it rather than write a short one.
declare -A LIVE_COMBO_DROPPED=()
# Held is the set of names the store reports, kept in its own map so the test is
# a plain string compare rather than `[[ -v arr[key] ]]`, which needs bash 4.3
# while this repository's stated floor is bash 4+ — a 4.0 host would otherwise
# fail inside the Combos section, mid-run, instead of at the `declare -A` that
# already stops it at startup.
declare -A LIVE_COMBO_HELD=()
live_combo_readable=0
live_combo_reason=""
if command -v omniroute >/dev/null; then
    if ! gateway_up; then
        live_combo_reason="the gateway does not answer on $GATEWAY"
    else
        live_combo_failed=0
        if ! live_combo_raw="$(omni_json combo list)"; then
            live_combo_failed=1
        fi
        if live_combo_rows="$(live_combo_norm "$live_combo_raw" "$live_combo_failed")"; then
            live_combo_readable=1
            while IFS=$'\x1f' read -r live_name live_strategy live_refs live_dropped; do
                [[ -z "$live_name" ]] && continue
                LIVE_COMBO_REFS["$live_name"]="$live_refs"
                LIVE_COMBO_STRATEGY["$live_name"]="$live_strategy"
                LIVE_COMBO_DROPPED["$live_name"]="${live_dropped:-0}"
                LIVE_COMBO_HELD["$live_name"]=1
            done <<<"$live_combo_rows"
        else
            # The function's stdout on that path is its one-line reason; the
            # "live store unreadable" lead is this section's own headline.
            live_combo_reason="${live_combo_rows#live store unreadable - }"
        fi
    fi
fi

# A live combo is current when its legs are the same refs in the same order as
# the ones about to be written (so after the catalog filter below — a dropped
# leg is a change) and, when the store reports one, its strategy is the strategy
# the file asks for. Order matters: a reordered combo serves a different first
# leg. An empty strategy column means the live document carries no strategy
# field, so refs alone decide.
combo_is_current() {
    local name="$1" strategy="$2" keep="$3" live_refs live_strategy
    [[ "${LIVE_COMBO_HELD[$name]-}" == 1 ]] || return 1
    live_refs="${LIVE_COMBO_REFS[$name]}"
    live_strategy="${LIVE_COMBO_STRATEGY[$name]-}"
    [[ "$live_refs" == "$keep" ]] || return 1
    [[ -z "$live_strategy" || "$live_strategy" == "$strategy" ]]
}

echo "Combos:"
if [[ $live_combo_readable -eq 0 ]] && command -v omniroute >/dev/null; then
    if [[ $DRY -eq 1 ]]; then
        echo "  ! live combo list unreadable - would replace every combo (${live_combo_reason:-unknown})"
    else
        echo "  ! live combo list unreadable - replacing every combo (${live_combo_reason:-unknown})"
    fi
fi
combo_unchanged=0
combo_created=0
combo_replaced=0
combo_failed=0
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
        if [[ $live_combo_readable -eq 1 ]] && combo_is_current "$name" "$strategy" "$keep"; then
            echo "  - $name: would keep unchanged [$strategy]"
            combo_unchanged=$((combo_unchanged + 1))
        elif [[ $live_combo_readable -eq 1 ]] && [[ "${LIVE_COMBO_HELD[$name]-}" == 1 ]]; then
            echo "  - $name: would replace [$strategy] with $keep"
            combo_replaced=$((combo_replaced + 1))
        else
            echo "  - $name: would create [$strategy] with $keep"
            combo_created=$((combo_created + 1))
        fi
        continue
    fi
    if combo_is_current "$name" "$strategy" "$keep"; then
        echo "  = $name unchanged"
        combo_unchanged=$((combo_unchanged + 1))
        # Still probed: --probe asks whether every managed tier answers, and an
        # unchanged combo is managed.
        PROBE_COMBOS+=("$name")
        continue
    fi
    # Create first, delete only what it replaces: if the create fails the old
    # tier survives instead of leaving a hole. Delete-then-create can only run
    # when create reports "already exists" AND a retry still fails.
    if omni combo create "$name" --strategy "$strategy" --models "$keep" >/dev/null 2>&1; then
        echo "  + $name created ($strategy)"
        combo_created=$((combo_created + 1))
        PROBE_COMBOS+=("$name")
    else
        # The delete below is destructive and irreversible unless what it
        # removes was read first. LIVE_COMBO_REFS/LIVE_COMBO_STRATEGY hold the
        # combo as the store had it moments ago, so remember them BEFORE the
        # delete: if the re-create fails too, the tier can be put back instead
        # of being left as a hole. (The old wording claimed the previous
        # version was "untouched" — false the moment the delete ran.)
        had_previous=0
        prev_refs=""
        prev_strategy="$strategy"
        prev_dropped=0
        if [[ "${LIVE_COMBO_HELD[$name]-}" == 1 ]]; then
            had_previous=1
            prev_refs="${LIVE_COMBO_REFS[$name]}"
            prev_dropped="${LIVE_COMBO_DROPPED[$name]-0}"
            [[ "$prev_dropped" =~ ^[0-9]+$ ]] || prev_dropped=1
            # An empty strategy column means the live document reported none
            # (the shape measured on 3.8.51), so the file's strategy is the best
            # description of what was there.
            prev_strategy="${LIVE_COMBO_STRATEGY[$name]-}"
            [[ -n "$prev_strategy" ]] || prev_strategy="$strategy"
        fi
        omni combo delete "$name" --yes >/dev/null 2>&1 || true
        if omni combo create "$name" --strategy "$strategy" --models "$keep" >/dev/null 2>&1; then
            echo "  + $name replaced ($strategy)"
            combo_replaced=$((combo_replaced + 1))
            PROBE_COMBOS+=("$name")
        elif [[ $had_previous -eq 1 ]] && [[ "$prev_dropped" != 0 ]]; then
            # The refs above are a SHORT copy of what was deleted: the store
            # reported steps this script cannot read (a kind it does not know).
            # Writing them back would put a thinner tier in place of the real one
            # and print "restored" over the loss — the same false assurance this
            # whole path exists to remove. Name the state and what fixes it.
            echo "  ! $name creation failed - previous version LOST, restore refused:" \
                "the live combo reported $prev_dropped leg(s) apply cannot read," \
                "so a restore would be short - recreate $name from your own record:" \
                "omniroute combo create $name --strategy <strategy> --models <every leg>"
            combo_failed=$((combo_failed + 1))
        elif [[ $had_previous -eq 1 ]] && [[ -n "$prev_refs" ]] &&
            omni combo create "$name" --strategy "$prev_strategy" --models "$prev_refs" >/dev/null 2>&1; then
            echo "  ! $name creation failed - previous version restored"
            combo_failed=$((combo_failed + 1))
        elif [[ $had_previous -eq 1 ]] && [[ -n "$prev_refs" ]]; then
            # Nothing restores it but the words: name the exact command the
            # operator has to run to put the tier back. The one state this reads
            # as lost and that is not is a delete that failed *and* left the old
            # combo standing — the command then answers "already exists" and
            # says so. Deliberate: this line may over-warn, it may never again
            # assure the operator that nothing was touched after a delete.
            echo "  ! $name creation failed - previous version LOST, restore failed:" \
                "omniroute combo create $name --strategy $prev_strategy --models $prev_refs"
            combo_failed=$((combo_failed + 1))
        elif [[ $live_combo_readable -eq 1 ]]; then
            # The store was read for this run and did not hold the name, so
            # nothing was deleted and "no previous version" is a fact.
            echo "  ! $name creation failed (no previous version)"
            combo_failed=$((combo_failed + 1))
        else
            # The delete ran against a store apply never saw. Whether it removed
            # a tier is unknown, and "no previous version" would be a guess.
            echo "  ! $name creation failed - previous version unknown (live list unreadable)"
            combo_failed=$((combo_failed + 1))
        fi
    fi
done < <(python3 - "$COMBOS_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for c in data.get("combos", []):
    print("%s\t%s\t%s" % (c["name"], c.get("strategy", "priority"), ",".join(c["models"])))
PY
)
if [[ $DRY -eq 1 ]]; then
    echo "Combos: $combo_unchanged would keep unchanged, $combo_created would create, $combo_replaced would replace"
else
    echo "Combos: $combo_unchanged unchanged, $combo_created created, $combo_replaced replaced, $combo_failed failed"
fi

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

# ─── Model context overrides ────────────────────────────────────────────────
# The gateway resolves a model's context window from the registry, the
# models.dev sync and the provider's own discovery; where none of them knows the
# model it falls back to 128000 - and a client whose own limit is higher gets
# rejected and compacts (the 2026-09-30 deepseek-v4.1-flash and
# spark-1.3-contributor failures: live sessions compacted every ~5 minutes).
# context-overrides.json lists the known-wrong resolutions; each is applied
# through the documented management route PATCH /api/model-capability-overrides
# {target, key: "context_length", value}. Idempotent: an override already at the
# wanted value is reported, not rewritten. A local gateway with no manage key
# cannot be driven from bash here (curl cannot mint the CLI's machine token),
# so that case prints the dashboard fallback and moves on.
echo "Overrides:"
if [[ ! -f "$OVERRIDES_FILE" ]]; then
    echo "  = no context-overrides.json - nothing to do"
else
    override_rows=""
    if ! override_rows="$(python3 - "$OVERRIDES_FILE" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
for o in doc.get("overrides", []) if isinstance(doc, dict) else []:
    if isinstance(o, dict) and o.get("target") and isinstance(o.get("context"), int):
        print("%s\t%d" % (o["target"], o["context"]))
PY
    )"; then
        echo "  ! $OVERRIDES_FILE is unreadable - nothing applied"
        override_rows=""
    fi
    if [[ -z "$override_rows" ]]; then
        echo "  = context-overrides.json lists none"
    else
        while IFS=$'\t' read -r ov_target ov_context; do
            [[ -z "$ov_target" ]] && continue
            if [[ $DRY -eq 1 ]]; then
                echo "  - would ensure $ov_target context = $ov_context"
                continue
            fi
            if [[ -z "$REST_KEY" ]]; then
                echo "  ! $ov_target: no manage key for the management API - set it in the dashboard (Model Overrides)"
                continue
            fi
            if ! omni_rest GET /api/model-capability-overrides; then
                echo "  ! $ov_target: ${REST_ERROR:-the gateway did not answer}"
                continue
            fi
            override_current="$(python3 -c 'import json,sys
try:
    doc = json.load(sys.stdin)
except Exception:
    doc = {}
value = None
for row in (doc.get("overrides") if isinstance(doc, dict) else None) or []:
    if isinstance(row, dict) and row.get("target") == sys.argv[1] and row.get("key") == "context_length":
        value = row.get("value")
print("" if value is None else value)' "$ov_target" <<<"$REST_BODY")"
            if [[ "$override_current" == "$ov_context" ]]; then
                echo "  = $ov_target already $ov_context"
                continue
            fi
            override_body="$(python3 -c 'import json,sys; print(json.dumps({"target": sys.argv[1], "key": "context_length", "value": int(sys.argv[2])}))' "$ov_target" "$ov_context")"
            if ! omni_rest PATCH /api/model-capability-overrides "$override_body"; then
                echo "  ! $ov_target override failed - ${REST_ERROR:-the gateway refused}; set it in the dashboard"
                continue
            fi
            echo "  + $ov_target context -> $ov_context"
        done <<<"$override_rows"
    fi
fi

# ─── Probe: prove the combos answer, end to end ─────────────────────────────
if [[ $PROBE -eq 1 ]]; then
    key="$_client_key"  # resolved once, above: a second resolve would repeat the deprecation line
    if [[ -z "$key" || $DRY -eq 1 ]]; then
        echo "Probe skipped (dry run, or no omniroute client key in api-keys.yml)."
    elif (( ${#PROBE_COMBOS[@]} == 0 )); then
        echo "Probe skipped (no combos to probe)."
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


def first_model(resp):
    """The first chunk that names a model: SSE `data:` lines, or one JSON
    document when a gateway ignores `stream`. Stops there — the probe asks
    which leg served, not for the whole answer."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            chunk = json.loads(line)
        except ValueError:
            continue
        if chunk.get("model"):
            return chunk["model"]
    return "?"


for name in sys.argv[2:]:
    body = json.dumps({
        "model": name,
        "messages": [{"role": "user", "content": "Reply with: ok"}],
        # 2048, not 256: reasoning models (spark) spend tokens on hidden
        # thinking first and answer "empty" when the budget is tiny.
        "max_tokens": 2048,
        # Streamed: Muse's first byte outlives a whole-body wait (measured
        # 2026-09-28), and clients stream anyway.
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        gateway + "/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            served = first_model(resp)
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
