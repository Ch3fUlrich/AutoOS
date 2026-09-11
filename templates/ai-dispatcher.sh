#!/usr/bin/env bash
# AutoOS "ai" dispatcher.
#
# Installed as /usr/local/bin/ai by templates/rescue-bootstrap.sh. Picks which
# AI CLI to run from a small data file (/etc/autoos/ai-clients.conf, shipped
# as templates/ai-clients.conf) instead of hard-coding one tool, so adding a
# third AI client later is a one-line registry addition and never a change to
# this script — the same "catalog is data, libraries are code" split AGENTS.md
# §2 applies to the rest of AutoOS, applied here to one alias.
#
# Usage:
#   ai [args...]              run the default backend (claude, unless
#                              AUTOOS_AI_DEFAULT overrides it)
#   ai <backend> [args...]    run a specific backend by its registry id,
#                              e.g. `ai gemini "..."`
#   ai --list                 show every registered backend and whether its
#                              binary is actually on this machine
#
# claude and gemini stay callable directly under their own names — this
# dispatcher is an addition, not a replacement.
#
# Env overrides (the test suite uses these; a real install never needs to):
#   AUTOOS_AI_REGISTRY   path to the registry file (default: /etc/autoos/ai-clients.conf)
#   AUTOOS_AI_DEFAULT    backend id to use with no explicit backend (default: claude)

set -euo pipefail

REGISTRY="${AUTOOS_AI_REGISTRY:-/etc/autoos/ai-clients.conf}"
DEFAULT_ID="${AUTOOS_AI_DEFAULT:-claude}"

if [[ ! -r "$REGISTRY" ]]; then
    printf 'ai: no registry at %s — is AutoOS rescue-bootstrap installed?\n' "$REGISTRY" >&2
    exit 1
fi

# registry_records
# The single reader every other function below goes through. There used to be
# four separate ones and they did NOT agree on what a comment is: the awk pair
# skipped `/^[[:space:]]*#/`, while the `while read` pair only skipped a `#` in
# column 0 (IFS=":" contains no whitespace, so `read` keeps the indent and
# `"$id" == \#*` never matched). An indented comment therefore became a phantom
# row in `ai --list` and a "known" backend id. One reader, one rule.
registry_records() {
    awk -F: '
        {
            line = $0
            sub(/^[[:space:]]+/, "", line)
        }
        line ~ /^#/ { next }
        NF < 2      { next }
        { print line }
    ' "$REGISTRY"
}

# registry_field <id> <field-number>  (field 2 = binary, field 3 = description)
registry_field() {
    local id="$1" field="$2"
    registry_records | awk -F: -v id="$id" -v f="$field" '$1 == id { print $f; exit }'
}

registry_ids() {
    registry_records | cut -d: -f1
}

is_known_id() {
    local want="$1" id
    while IFS=: read -r id _rest; do
        [[ "$id" == "$want" ]] && return 0
    done < <(registry_records)
    return 1
}

print_list() {
    local id bin desc status marker
    # D2: the marker used to be appended to the ID column ("claude (default)"),
    # which overflowed that column's fixed width and shifted every other
    # column out of alignment on exactly that row. It gets its own column now
    # instead, so no id — however long — can ever push the row out of line.
    printf '%-10s %-16s %-14s %-7s %s\n' "ID" "BINARY" "STATUS" "DEFAULT" "DESCRIPTION"
    while IFS=: read -r id bin desc; do
        if command -v "$bin" >/dev/null 2>&1; then status="installed"; else status="not installed"; fi
        marker=""
        [[ "$id" == "$DEFAULT_ID" ]] && marker="yes"
        printf '%-10s %-16s %-14s %-7s %s\n' "$id" "$bin" "$status" "$marker" "$desc"
    done < <(registry_records)
}

case "${1:-}" in
    --list|-l)
        print_list
        exit 0
        ;;
    --help|-h)
        printf 'usage: ai [backend] [args...]\n       ai --list\n'
        exit 0
        ;;
esac

backend_id="$DEFAULT_ID"
explicit=0
if [[ $# -gt 0 ]] && is_known_id "$1"; then
    backend_id="$1"
    shift
    explicit=1
elif [[ $# -gt 1 && "$1" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    # The leading [a-z0-9] is load-bearing: `-` is inside the class, so the
    # older ^[a-z0-9_-]+$ also matched `-p` and `--model`. `claude -p "..."` is
    # the standard non-interactive invocation, so `ai -p "why did this fail"`
    # printed this warning on essentially every scripted call before doing the
    # right thing anyway. A backend id never starts with a dash.
    #
    # D1: "$1" is shaped like a backend id (and something follows it, so it
    # isn't just a one-word prompt) but isn't a registered one — e.g. a typo
    # like `ai gemeni "..."`. Real ambiguity is why this doesn't refuse to
    # run: `ai "why did this fail?"` must still fall through to the default
    # backend. So this only ever warns and continues, never blocks.
    printf 'ai: "%s" is not a known backend, treating it as part of the prompt; see ai --list\n' "$1" >&2
fi

bin="$(registry_field "$backend_id" 2)"
if [[ -z "$bin" ]]; then
    printf 'ai: unknown backend "%s" — try "ai --list"\n' "$backend_id" >&2
    exit 1
fi

if command -v "$bin" >/dev/null 2>&1; then
    exec "$bin" "$@"
fi

if [[ "$explicit" -eq 1 ]]; then
    printf 'ai: backend "%s" (%s) is not installed\n' "$backend_id" "$bin" >&2
    exit 127
fi

# The default backend's binary is missing. Fall back to the first *other*
# registered backend that is actually installed, rather than dying with a
# bare "command not found" for a tool the caller never asked for by name.
fallback_id="" fallback_bin=""
while IFS=: read -r id bin2 _desc; do
    if command -v "$bin2" >/dev/null 2>&1; then
        fallback_id="$id"
        fallback_bin="$bin2"
        break
    fi
done < <(registry_records)

if [[ -n "$fallback_bin" ]]; then
    printf 'ai: default backend "%s" (%s) is not installed — using "%s" instead\n' \
        "$backend_id" "$bin" "$fallback_id" >&2
    exec "$fallback_bin" "$@"
fi

printf 'ai: no configured AI CLI is installed (tried: %s)\n' "$(registry_ids | tr '\n' ' ')" >&2
exit 127
