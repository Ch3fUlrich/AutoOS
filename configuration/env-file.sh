#!/usr/bin/env bash
# Sourced helper: read a flat KEY=VALUE file WITHOUT shell evaluation.
#
#   . configuration/env-file.sh
#   autoos_read_env_file path/to/.env   # fills ENV_NAMES / ENV_VALUES
#
# `set -a; . ./.env` would run any $(...) or backtick a value contains; this
# keeps every value literal. Blank lines, comments, lines without '=', names
# that are not shell identifiers, empty values and REPLACE_WITH_* placeholders
# are skipped. One pair of surrounding quotes is removed. Values are never
# printed by this helper.

# shellcheck disable=SC2034 # ENV_NAMES / ENV_VALUES are read by the caller.
autoos_read_env_file() {
    ENV_NAMES=()
    ENV_VALUES=()
    local line trimmed name value
    [[ -f "$1" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        trimmed="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$trimmed" || "$trimmed" == \#* || "$trimmed" != *=* ]] && continue
        name="${trimmed%%=*}"
        name="${name%"${name##*[![:space:]]}"}"
        value="${trimmed#*=}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ ${#value} -ge 2 && ( "$value" == \"*\" || "$value" == \'*\' ) ]]; then
            value="${value:1:${#value}-2}"
        fi
        [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        [[ -z "$value" || "$value" == REPLACE_WITH_* ]] && continue
        ENV_NAMES+=("$name")
        ENV_VALUES+=("$value")
    done <"$1"
    return 0
}
