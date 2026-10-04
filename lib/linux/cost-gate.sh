#!/usr/bin/env bash
# cost-gate.sh - install the two systemd user units that keep the daily
# cost-gate verdict fresh, plus the defaults file the refresh reads.
#
# The refresh itself lives in the checkout (tools/cost-gate-refresh.py); the
# unit renders the checkout root the installer was run from into its ExecStart,
# so a moved or removed checkout fails loudly instead of running a dead path.
#
# Idempotent by construction: a second run over an untouched tree rewrites
# nothing, makes no backup and reports skipped. The unit files are compared
# after rendering, so an unchanged install is byte-identical to the previous
# one. The config file is created ONLY when it is absent - once a user edits
# warn/block, no run of this script may ever touch it again.
#
# Nothing is enabled or started here: the units are installed inert, and only
# AUTOOS_COST_GATE_ENABLE=1 (the user's opt-in, AGENTS.md hard rule 3) makes
# the installer ask systemctl for a daemon-reload and enable/start of the
# timer.
#
# Sourced by lib/linux/install.sh, which supplies the ui_* helpers, the
# has_cmd and backup_file helpers, AUTOOS_ROOT and SYS_HOME. Running this file
# directly installs for the calling user; the AUTOOS_TEST_* variables below
# are test seams that repoint the XDG paths into a scratch tree.
#
# shellcheck shell=bash

# Sourcing this file a second time is harmless: the function simply redefines.
# Running it as a script installs the units for the calling user.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    # install.sh sets this before it sources this file; a direct run must
    # default it too, or `set -u` aborts at the first `(( AUTOOS_DRY_RUN ))`.
    AUTOOS_DRY_RUN="${AUTOOS_DRY_RUN:-0}"
    AUTOOS_ROOT="${AUTOOS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
    SYS_HOME="${SYS_HOME:-$HOME}"
    export SYS_HOME
    # Minimal stand-ins so the script stands alone; setup.sh's real ui wins
    # when this file is sourced by install.sh.
    if ! declare -F ui_info >/dev/null 2>&1; then
        ui_info()  { printf '  - %s\n' "$*"; }
        ui_ok()    { printf '  + %s\n' "$*"; }
        ui_warn()  { printf '  ! %s\n' "$*"; }
        ui_err()   { printf '  x %s\n' "$*" >&2; }
        ui_muted() { printf '%s\n' "$*"; }
    fi
    if ! declare -F has_cmd >/dev/null 2>&1; then
        has_cmd() { command -v "$1" >/dev/null 2>&1; }
    fi
    if ! declare -F backup_file >/dev/null 2>&1; then
        backup_file() { cp -p -- "$1" "${1}.autoos-backup-$(date +%Y%m%d-%H%M%S)"; }
    fi
fi

install_cost_gate() {
    local warn="20" block="25"

    # Arguments: --warn N --block M. Both must be non-negative integers; a bad
    # value aborts before ANY file is written, because the config file it
    # would have written is the only place these two numbers ever land.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --warn)
                [[ $# -ge 2 ]] || { ui_err "cost-gate: --warn needs a value"; return 1; }
                warn="$2"; shift 2 ;;
            --block)
                [[ $# -ge 2 ]] || { ui_err "cost-gate: --block needs a value"; return 1; }
                block="$2"; shift 2 ;;
            *)
                ui_err "cost-gate: unknown argument '$1'"
                return 1 ;;
        esac
    done
    [[ "$warn" =~ ^[0-9]+$ ]] || { ui_err "cost-gate: --warn '$warn' is not a number"; return 1; }
    [[ "$block" =~ ^[0-9]+$ ]] || { ui_err "cost-gate: --block '$block' is not a number"; return 1; }
    (( warn > 0 )) || { ui_err "cost-gate: --warn must be greater than zero, got '$warn'"; return 1; }
    (( block > 0 )) || { ui_err "cost-gate: --block must be greater than zero, got '$block'"; return 1; }
    (( block > warn )) || { ui_err "cost-gate: --block ($block) must be greater than --warn ($warn)"; return 1; }

    # Default AUTOOS_DRY_RUN if not set (for standalone use)
    AUTOOS_DRY_RUN="${AUTOOS_DRY_RUN:-0}"

    local udest="${XDG_CONFIG_HOME:-${SYS_HOME}/.config}/systemd/user"
    local conf_dir="${XDG_CONFIG_HOME:-${SYS_HOME}/.config}/autoos"
    local conf="${conf_dir}/daily-gate.conf"
    local units=(cost-gate.service cost-gate.timer)

    if (( AUTOOS_DRY_RUN )); then
        ui_muted "would install ${#units[@]} systemd user units into $udest"
        ui_muted "would create $conf (warn=$warn, block=$block) when absent"
        return 0
    fi

    if ! has_cmd systemctl; then
        # Writing unit files nothing will ever read is how a feature reports
        # success on a machine where it could not possibly work.
        ui_err "systemd is required for the cost gate and systemctl was not found"
        return 1
    fi

    # The rendered unit's ExecStart names the checkout root directly, so the
    # root becomes part of a systemd command line. A space splits an unquoted
    # argument in two, a double quote ends it early, and a newline breaks the
    # line itself. Rather than quote each occurrence (systemd's quoting rules
    # are easy to get half right), refuse the whole install before anything is
    # written when the root carries one of those. A backslash is a legal Linux
    # path character but is refused too, so the rule is one simple test on both
    # platforms (Windows paths routinely contain them). On POSIX a path with a
    # space could be quoted and installed, but the shared rule refuses it as
    # well: one rule, testable on both platforms.
    local root="$AUTOOS_ROOT"
    # Check for problematic characters in the root path - avoid shell interpretation issues
    # by checking each one separately
    if [[ "$root" == *$'\n'* ]] || [[ "$root" == *[[:space:]]* ]] || [[ "$root" == *\"* ]] || [[ "$root" == *\\* ]]; then
        ui_err "cost-gate: refusing to install: the checkout root '$root' contains a newline, space, quote or backslash that a rendered systemd unit cannot carry safely"
        return 1
    fi
    # Special handling for ampersand and hash which can cause shell issues
    if [[ "$root" == *"&"* ]] || [[ "$root" == *"#"* ]]; then
        ui_err "cost-gate: refusing to install: the checkout root '$root' contains an ampersand or hash that a rendered systemd unit cannot carry safely"
        return 1
    fi

    # Every source is checked before anything is written: a missing template
    # is a broken checkout, not a condition of this machine, and it must not
    # be discovered half-way through replacing the installed copy.
    local u usrc
    for u in "${units[@]}"; do
        usrc="${AUTOOS_ROOT}/lib/linux/systemd/user/${u}"
        if [[ ! -f "$usrc" ]]; then
            ui_err "missing unit template: $usrc"
            return 1
        fi
    done

    mkdir -p -- "$udest" "$conf_dir"

    local changed=0 tmp root_sub
    for u in "${units[@]}"; do
        usrc="${AUTOOS_ROOT}/lib/linux/systemd/user/${u}"
        # Render to a temp file first so an unchanged unit is genuinely
        # untouched: rewriting it would restart the timer on every run of an
        # idempotent script (AGENTS.md §4). The temp file lives in the same
        # directory as the destination, so the mv below is atomic.
        tmp="$(mktemp "${udest}/.${u}.XXXXXX")"
        # Escape for the sed REPLACEMENT side, not for the path itself: &
        # would rebuild the matched text, \ would start an escape, and #
        # would end this #-delimited expression early. Backslash is escaped
        # first so a literal \ in the path is not itself mistaken for one of
        # the escapes added here. (The refusal above already drops any path
        # with a backslash; this keeps the render safe either way.)
        root_sub="${root//\\/\\\\}"
        root_sub="${root_sub//&/\\&}"
        root_sub="${root_sub//#/\\#}"
        sed -e "s#@APPROOT@#${root_sub}#g" "$usrc" > "$tmp"
        if [[ -f "${udest}/${u}" ]] && cmp -s "$tmp" "${udest}/${u}"; then
            ui_info "$u is already current"
            rm -f "$tmp"
        else
            if [[ -f "${udest}/${u}" ]] && ! backup_file "${udest}/${u}" >/dev/null; then
                ui_err "could not back up ${udest}/${u} - left as it was"
                rm -f "$tmp"
                return 1
            fi
            mv -f "$tmp" "${udest}/${u}"
            ui_ok "installed $u"
            changed=1
        fi
    done

    # The config file is created ONLY when it is absent. Once it exists the
    # user owns it: editing warn/block is the user's act, and a re-run must
    # never rewrite it, so the built-in defaults below are read from the
    # installer arguments, not from the file.
    if [[ ! -f "$conf" ]]; then
        printf 'warn=%s\nblock=%s\n' "$warn" "$block" >"$conf"
        ui_ok "created $conf (warn=$warn, block=$block)"
        changed=1
    else
        ui_info "$conf already exists, leaving it untouched"
    fi

    if (( changed )); then
        systemctl --user daemon-reload \
            && ui_ok "reloaded the user unit files" \
            || ui_warn "systemctl --user daemon-reload failed - is there a user manager on this session?"
    fi

    # Enable/start is an opt-in the user must ask for explicitly: the default
    # install lays the files down and nothing more (AGENTS.md hard rule 3).
    if [[ "${AUTOOS_COST_GATE_ENABLE:-0}" == "1" ]]; then
        if systemctl --user enable --now cost-gate.timer >/dev/null 2>&1; then
            ui_ok "enabled cost-gate.timer (refreshes the daily gate every 10 minutes)"
        else
            ui_warn "could not enable cost-gate.timer - the gate is installed but not running"
        fi
    else
        ui_info "cost-gate timer not enabled (set AUTOOS_COST_GATE_ENABLE=1 to enable it)"
    fi

    # Nothing to change = nothing happened: the next run must report skipped,
    # not installed, or an idempotent feature re-installs forever.
    if (( changed == 0 )); then
        # Public contract to install.sh (the only reader of this file's
        # outcome), set here because install.sh clears it before each run.
        # shellcheck disable=SC2034  # read by install.sh, not this file alone
        INSTALL_SCRIPT_STATE=skipped
        ui_info "cost-gate: skipped: already installed and current"
    fi
    return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    install_cost_gate "$@"
fi
