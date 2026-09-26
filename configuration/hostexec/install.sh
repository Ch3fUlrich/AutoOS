#!/usr/bin/env bash
# configuration/hostexec/install.sh -- wire the hostexec broker into agent
# clients and install the systemd USER unit file.
#
# This driver never enables or starts anything. Enabling/starting is an
# operator step, printed (not executed) by this driver:
#
#   systemctl --user daemon-reload && systemctl --user enable --now autoos-hostexec.service
#
# Usage:
#   install.sh [--dry-run] [--clients openhands,claude,codex,opencode,qoder]
#              [--unit] [--unregister]
#
#   --dry-run    print every action, write nothing.
#   --clients    comma-separated client list to wire (default: none).
#   --unit       install the systemd USER unit file only (ignore --clients).
#   --unregister remove only the hostexec entries/unit this driver writes.
#
# Rules (AGENTS.md): read the existing file, set only our own key, write
# back; back every replaced file up first; installing twice reports
# "already current" instead of rewriting. The bearer token is read from a
# 0600 file and never appears on any argv, or in output/logs.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE="$REPO/configuration/hostexec/autoos-hostexec.service"
UNIT_NAME="autoos-hostexec.service"
KNOWN_CLIENTS="openhands claude codex opencode qoder"

DRY_RUN=0
UNIT_ONLY=0
UNREGISTER=0
CLIENTS=""
PORT=""
REQUESTED=()

TMP_FILES=()
tmp_cleanup() {
    if (( ${#TMP_FILES[@]} > 0 )); then
        rm -f "${TMP_FILES[@]}"
    fi
}
trap tmp_cleanup EXIT

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

usage() {
    cat <<'EOF'
Usage: install.sh [--dry-run] [--clients a,b,...] [--unit] [--unregister]

  --dry-run    print every action, write nothing.
  --clients    comma-separated list of clients to wire
               (any of openhands,claude,codex,opencode,qoder; default: none).
  --unit       install the systemd USER unit file only; ignore --clients.
  --unregister remove only the hostexec entries/unit this driver writes.

The driver never enables or starts the unit; it prints the operator
command instead. Tokens come from 0600 files
(~/.config/autoos/exec/<client>.token, or AUTOOS_EXEC_TOKEN_FILE_<CLIENT>)
and never appear on any argv or in output.
EOF
}

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --dry-run) DRY_RUN=1; shift ;;
            --unit) UNIT_ONLY=1; shift ;;
            --unregister) UNREGISTER=1; shift ;;
            --clients)
                if (( $# < 2 )); then
                    err "install: --clients needs a comma-separated list"
                    return 2
                fi
                CLIENTS="$2"; shift 2 ;;
            --clients=*) CLIENTS="${1#--clients=}"; shift ;;
            -h|--help) usage; exit 0 ;;
            *)
                err "install: unknown argument: $1"
                usage >&2
                return 2 ;;
        esac
    done
    return 0
}

is_known_client() {
    case " ${KNOWN_CLIENTS} " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# default_port: the broker default, read from tools/hostexec/server.py so
# the driver tracks it (AUTOOS_EXEC_PORT overrides when set).
default_port() {
    local parsed
    parsed="$(grep -E '^DEFAULT_PORT[[:space:]]*=[[:space:]]*[0-9]+' \
        "$REPO/tools/hostexec/server.py" 2>/dev/null | sed -E 's/[^0-9]//g' \
        | head -n 1 || true)"
    if [[ "$parsed" =~ ^[0-9]+$ ]]; then
        printf '%s' "$parsed"
    else
        printf '8765'
    fi
}

new_tmp() {
    local made
    made="$(mktemp)"
    TMP_FILES+=("$made")
    printf '%s' "$made"
}

# backup_file <path>: copy to <path>.autoos-backup-<stamp>, then -1, -2,
# ... while taken, so two backups in the same second keep both. Missing
# path: no-op. Dry-run: print, create nothing.
backup_file() {
    local path="$1" stamp dest idx
    if [[ ! -e "$path" && ! -L "$path" ]]; then
        return 0
    fi
    stamp="$(date +%Y%m%d%H%M%S)"
    dest="${path}.autoos-backup-${stamp}"
    if [[ -e "$dest" || -L "$dest" ]]; then
        idx=1
        while [[ -e "${dest}-${idx}" || -L "${dest}-${idx}" ]]; do
            idx=$((idx + 1))
        done
        dest="${dest}-${idx}"
    fi
    if (( DRY_RUN )); then
        say "dry-run: would back up ${path} to ${dest}"
        return 0
    fi
    cp -p "$path" "$dest"
    say "backup: ${path} -> ${dest}"
}

# install_unit: render the template with the repo path and install it to
# ~/.config/systemd/user/ only when it differs (cmp). A failed copy stops
# with the existing file untouched (stage + rename, never truncate).
install_unit() {
    local dest rendered esc stage
    dest="$HOME/.config/systemd/user/${UNIT_NAME}"
    if [[ "${AUTOOS_HOSTEXEC_BREAK:-}" == "no-template" ]]; then
        err "install: refusing: unit template is unavailable (test hook)"
        return 1
    fi
    if [[ ! -f "$TEMPLATE" ]]; then
        err "install: refusing: unit template not found: ${TEMPLATE}"
        return 1
    fi
    rendered="$(new_tmp)"
    esc="$REPO"
    esc="${esc//\\/\\\\}"
    esc="${esc//&/\\&}"
    esc="${esc//|/\\|}"
    if ! sed "s|<repo>|${esc}|g" "$TEMPLATE" > "$rendered"; then
        err "install: refusing: could not render unit template"
        return 1
    fi
    if [[ -f "$dest" ]] && cmp -s "$rendered" "$dest"; then
        say "unit: already current: ${dest}"
    else
        if (( DRY_RUN )); then
            say "dry-run: would install unit: ${dest}"
        else
            backup_file "$dest"
            mkdir -p "$(dirname "$dest")"
            stage="$(dirname "$dest")/.${UNIT_NAME}.tmp.$$"
            TMP_FILES+=("$stage")
            if ! cp "$rendered" "$stage"; then
                rm -f "$stage"
                err "install: refusing: copy failed, leaving untouched: ${dest}"
                return 1
            fi
            mv "$stage" "$dest"
            say "unit: installed: ${dest}"
        fi
    fi
    say "unit: operator step (NOT run by this driver): systemctl --user daemon-reload && systemctl --user enable --now ${UNIT_NAME}"
    return 0
}

# token_file_for <client>: the token file path -- env
# AUTOOS_EXEC_TOKEN_FILE_<CLIENT> (uppercased) wins, else the
# ~/.config/autoos/exec/<client>.token default.
token_file_for() {
    local client="$1" varname="AUTOOS_EXEC_TOKEN_FILE_${client^^}"
    if [[ -n "${!varname:-}" ]]; then
        printf '%s' "${!varname}"
    else
        printf '%s' "$HOME/.config/autoos/exec/${client}.token"
    fi
}

# token_mode <file>: numeric mode (600), portable GNU/BSD stat.
token_mode() {
    local mode
    mode="$(stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null \
        || printf 'unknown')"
    printf '%s' "$mode"
}

# token_file_ok <client>: the token file exists and is mode 0600. The
# value is never printed -- neither here nor by any caller.
token_file_ok() {
    local client="$1" path mode
    path="$(token_file_for "$client")"
    if [[ ! -f "$path" ]]; then
        err "install: refusing: token file not found for ${client}: ${path}"
        err "install: create it first, e.g.: python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ${path} && chmod 600 ${path}"
        return 1
    fi
    mode="$(token_mode "$path")"
    if [[ "$mode" != "600" ]]; then
        err "install: refusing: token file for ${client} has mode ${mode}, need 0600: ${path}"
        return 1
    fi
    return 0
}

# read_token <client> <varname>: read the token into the named shell
# variable. Callers must never print it, log it, or place it on any argv
# (pass it to helpers through the environment, never as an argument).
read_token() {
    local client="$1" outvar="$2" path tok
    token_file_ok "$client" || return 1
    path="$(token_file_for "$client")"
    tok="$(<"$path")"
    if [[ -z "$tok" ]]; then
        err "install: refusing: token file is empty: ${path}"
        return 1
    fi
    printf -v "$outvar" '%s' "$tok"
    return 0
}

# wire_client <client>: implemented under item 3 (client writers).
wire_client() {
    err "install: client wiring is not implemented yet: $1"
    return 2
}

# unregister_all: implemented under item 4.
unregister_all() {
    err "install: --unregister is not implemented yet"
    return 2
}

main() {
    local name fail=0
    parse_args "$@" || return 2
    if [[ -n "${AUTOOS_EXEC_PORT:-}" ]]; then
        PORT="${AUTOOS_EXEC_PORT}"
    else
        PORT="$(default_port)"
    fi
    if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
        err "install: refusing: bad port: ${PORT}"
        return 2
    fi
    REQUESTED=()
    if [[ -n "$CLIENTS" ]]; then
        local IFS=',' entry
        read -ra REQUESTED <<< "$CLIENTS"
        for entry in "${REQUESTED[@]}"; do
            if ! is_known_client "$entry"; then
                err "install: unknown client: ${entry} (known: ${KNOWN_CLIENTS// /, })"
                return 2
            fi
        done
    fi
    if (( UNREGISTER )); then
        unregister_all || return $?
        return 0
    fi
    if ! install_unit; then
        fail=1
    fi
    if (( UNIT_ONLY )); then
        if (( ${#REQUESTED[@]} > 0 )); then
            say "install: note: --unit given; ignoring --clients"
        fi
    else
        if (( ${#REQUESTED[@]} > 0 )); then
            for name in "${REQUESTED[@]}"; do
                if ! wire_client "$name"; then
                    fail=1
                fi
            done
        fi
    fi
    return "$fail"
}

main "$@"
