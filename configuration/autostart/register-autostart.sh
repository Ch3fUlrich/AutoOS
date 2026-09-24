#!/usr/bin/env bash
# Opt-in autostart for the AutoOS AI stack (Linux: systemd --user units).
#
# Linux counterpart of Register-AutoOSAutostart.ps1. Renders the unit
# templates next to this script with THIS checkout's path and the PATH this
# machine needs (nvm node, ~/.local/bin), writes them to
# ~/.config/systemd/user/, enables them and starts them. Nothing happens until
# you run it; every file it writes is named first.
#
#   ./configuration/autostart/register-autostart.sh              # register + start
#   ./configuration/autostart/register-autostart.sh --dry-run    # show the plan only
#   ./configuration/autostart/register-autostart.sh --only autoos-omniroute
#   ./configuration/autostart/register-autostart.sh --takeover   # stop hand-started copies first
#   ./configuration/autostart/register-autostart.sh --unregister # disable + remove our units
#   ./configuration/autostart/register-autostart.sh --render autoos-omniroute  # print one unit
#
# Safe to run twice: an unchanged unit is reported as skipped, a changed one
# is backed up (<unit>.autoos-backup-<timestamp>) before it is replaced, and
# enable/start on an active unit are no-ops. A service that somebody started by
# hand keeps running (the unit is enabled, not started) unless --takeover.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
UNIT_DIR="${AUTOOS_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
# Tests replace both with stubs; nothing here may touch the live user manager
# from the suite (AGENTS.md §5).
SYSTEMCTL="${AUTOOS_SYSTEMCTL:-systemctl}"
LOGINCTL="${AUTOOS_LOGINCTL:-loginctl}"
MARKER="# Managed by AutoOS configuration/autostart/register-autostart.sh"

# Order matters: the gateway first, the oneshot resume unit last.
ALL_UNITS=(autoos-omniroute autoos-opencode)

DRY=0
UNREGISTER=0
TAKEOVER=0
RENDER=""
ONLY=""
while (( $# )); do
    case "$1" in
        --dry-run)    DRY=1 ;;
        --unregister) UNREGISTER=1 ;;
        --takeover)   TAKEOVER=1 ;;
        --render)     shift; RENDER="${1:-}" ;;
        --render=*)   RENDER="${1#--render=}" ;;
        --only)       shift; ONLY="${1:-}" ;;
        --only=*)     ONLY="${1#--only=}" ;;
        -h|--help)    sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 2 ;;
    esac
    shift
done

# ─── Per-unit facts ─────────────────────────────────────────────────────────
unit_port() {
    case "$1" in
        autoos-omniroute) echo 20128 ;;
        autoos-opencode)  echo 4096 ;;
        *) echo "" ;;
    esac
}
unit_binary() {
    case "$1" in
        autoos-omniroute) echo omniroute ;;
        autoos-opencode)  echo opencode ;;
        *) echo "" ;;
    esac
}

# The PATH the units run with: the directories of the tools they start (nvm
# versions node, so it is resolved here, not hard-coded), then the usual ones.
unit_path() {
    local dirs=() d seen=":" tool
    for tool in node omniroute opencode litellm docker; do
        if command -v "$tool" >/dev/null 2>&1; then
            dirs+=("$(dirname "$(command -v "$tool")")")
        fi
    done
    dirs+=("$HOME/.local/bin" /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin)
    local out=""
    for d in "${dirs[@]}"; do
        [[ "$seen" == *":$d:"* ]] && continue
        seen+="$d:"
        out+="${out:+:}$d"
    done
    printf '%s' "$out"
}

# render <unit>: the unit text for this machine, on stdout.
render() {
    local name="$1" tpl="$HERE/$1.service" bin=""
    [[ -f "$tpl" ]] || { echo "No template for $name ($tpl)" >&2; return 1; }
    local tool
    tool="$(unit_binary "$name")"
    if [[ -n "$tool" ]]; then
        bin="$(command -v "$tool" 2>/dev/null || true)"
        [[ -n "$bin" ]] || { echo "$tool is not installed" >&2; return 3; }
    fi
    # Characters that systemd or sed would reinterpret in a path.
    if [[ "$REPO$bin" =~ [[:space:]%|\\] ]]; then
        echo "Path contains a space, %, | or backslash - not supported in a unit: $REPO $bin" >&2
        return 1
    fi
    local path
    path="$(unit_path)"
    printf '%s\n' "$MARKER"
    # Template comments stay: they say why each line is there.
    sed -e "s|@REPO@|$REPO|g" -e "s|@PATH@|$path|g" \
        -e "s|@OMNIROUTE@|$bin|g" -e "s|@OPENCODE@|$bin|g" "$tpl"
}

if [[ -n "$RENDER" ]]; then
    render "$RENDER"
    exit $?
fi

selected_units() {
    local u
    for u in "${ALL_UNITS[@]}"; do
        if [[ -z "$ONLY" || ",$ONLY," == *",$u,"* ]]; then echo "$u"; fi
    done
}

listener_pid() {
    ss -ltnpH "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -n1 | cut -d= -f2
}

user_manager_ok() {
    "$SYSTEMCTL" --user show-environment >/dev/null 2>&1
}

[[ $DRY -eq 1 ]] && echo "This is a dry run - no unit is written, enabled, started or stopped."

if ! command -v "$SYSTEMCTL" >/dev/null 2>&1; then
    echo "systemctl not found - this machine has no systemd; start the stack with configuration/autostart/Start-AutoOSStack.sh."
    exit 1
fi
if [[ $DRY -eq 0 ]] && ! user_manager_ok; then
    echo "No systemd user manager is reachable (systemctl --user). Log in once with a session, or run:"
    echo "  sudo loginctl enable-linger ${USER:-$(id -un)}"
    exit 1
fi

# ─── Unregister ─────────────────────────────────────────────────────────────
if [[ $UNREGISTER -eq 1 ]]; then
    while IFS= read -r u; do
        f="$UNIT_DIR/$u.service"
        if [[ ! -f "$f" ]]; then echo "  = $u: not registered - nothing to remove"; continue; fi
        if ! grep -qF "$MARKER" "$f"; then
            echo "  ! $u: $f was not written by AutoOS - left alone"
            continue
        fi
        if [[ $DRY -eq 1 ]]; then echo "  - $u: would disable, stop and remove $f"; continue; fi
        "$SYSTEMCTL" --user disable --now "$u.service" >/dev/null 2>&1 || true
        rm -f "$f"
        echo "  - $u: disabled, stopped, removed $f"
    done < <(selected_units)
    [[ $DRY -eq 1 ]] || "$SYSTEMCTL" --user daemon-reload
    exit 0
fi

# ─── Register ───────────────────────────────────────────────────────────────
echo "Units go to $UNIT_DIR (repo: $REPO)"
CHANGED=0
TO_START=()
while IFS= read -r u; do
    f="$UNIT_DIR/$u.service"
    if [[ "$u" == autoos-omniroute && -f "$UNIT_DIR/omniroute.service" ]]; then
        # Two gateways would fight over :20128.
        echo "  ! $u: 'omniroute autostart' already installed omniroute.service - skipped."
        echo "    Use one: omniroute autostart disable   (then re-run this)"
        continue
    fi
    rc=0
    text="$(render "$u" 2>&1)" || rc=$?
    if [[ $rc -eq 3 ]]; then echo "  - $u: skipped ($text)"; continue; fi
    if [[ $rc -ne 0 ]]; then echo "  ! $u: $text"; continue; fi
    if [[ -f "$f" ]] && [[ "$(cat "$f")" == "$text" ]]; then
        echo "  = $u: unit unchanged (skipped)"
    elif [[ $DRY -eq 1 ]]; then
        if [[ -f "$f" ]]; then echo "  - $u: would back up and replace $f"
        else echo "  - $u: would write $f"; fi
    else
        mkdir -p "$UNIT_DIR"
        if [[ -f "$f" ]]; then
            backup="$f.autoos-backup-$(date +%Y%m%d-%H%M%S)"
            cp -p "$f" "$backup"
            echo "  + $u: replaced $f (backup: $backup)"
        else
            echo "  + $u: wrote $f"
        fi
        printf '%s\n' "$text" >"$f"
        CHANGED=1
    fi
    TO_START+=("$u")
done < <(selected_units)

if [[ $DRY -eq 1 ]]; then
    for u in "${TO_START[@]}"; do echo "  - $u: would enable and start"; done
else
    [[ $CHANGED -eq 1 ]] && "$SYSTEMCTL" --user daemon-reload
    for u in "${TO_START[@]}"; do
        "$SYSTEMCTL" --user enable "$u.service" >/dev/null 2>&1 \
            || { echo "  ! $u: enable failed - see: systemctl --user status $u"; continue; }
        if "$SYSTEMCTL" --user is-active --quiet "$u.service" 2>/dev/null; then
            if [[ $CHANGED -eq 1 ]]; then
                "$SYSTEMCTL" --user try-restart "$u.service" >/dev/null 2>&1 || true
                echo "  + $u: enabled, restarted with the new unit"
            else
                echo "  = $u: enabled and running (skipped)"
            fi
            continue
        fi
        port="$(unit_port "$u")"
        pid=""
        [[ -n "$port" ]] && pid="$(listener_pid "$port")"
        if [[ -n "$port" && -z "$pid" ]] && [[ -n "$(ss -ltnH "sport = :$port" 2>/dev/null)" ]]; then
            echo "  ! $u: enabled; :$port is held by another user's process - not started"
            continue
        fi
        if [[ -n "$pid" ]]; then
            if [[ $TAKEOVER -eq 0 ]]; then
                echo "  ! $u: enabled; a hand-started process (pid $pid) holds :$port - not started."
                echo "    It takes over at the next boot, or now with: $0 --takeover --only $u"
                continue
            fi
            echo "  - $u: stopping the hand-started process on :$port (pid $pid) so the unit can own it"
            if [[ "$u" == autoos-omniroute ]]; then
                (cd "$HOME" && omniroute stop) >/dev/null 2>&1 || kill "$pid" 2>/dev/null || true
            else
                kill "$pid" 2>/dev/null || true
            fi
            for _ in $(seq 1 15); do [[ -z "$(listener_pid "$port")" ]] && break; sleep 1; done
        fi
        if "$SYSTEMCTL" --user start "$u.service"; then
            echo "  + $u: enabled and started"
        else
            echo "  ! $u: start failed - see: journalctl --user -u $u"
        fi
    done
fi

# ─── The gateway must refuse keyless /v1 requests ──────────────────────────
# REQUIRE_API_KEY lives in ~/.omniroute/.env, which OmniRoute reads itself.
# Read-modify-write: only a missing line is appended, after a backup.
if [[ -z "$ONLY" || ",$ONLY," == *",autoos-omniroute,"* ]]; then
    omni_env="${AUTOOS_OMNIROUTE_ENV:-$HOME/.omniroute/.env}"
    if [[ -f "$omni_env" ]] && grep -qE '^[[:space:]]*REQUIRE_API_KEY[[:space:]]*=[[:space:]]*"?true"?[[:space:]]*$' "$omni_env"; then
        echo "REQUIRE_API_KEY=true is set in $omni_env (skipped)."
    elif [[ -f "$omni_env" ]] && grep -qE '^[[:space:]]*REQUIRE_API_KEY[[:space:]]*=' "$omni_env"; then
        echo "  ! $omni_env sets REQUIRE_API_KEY to something other than true - left alone; /v1 may accept keyless requests."
    elif [[ $DRY -eq 1 ]]; then
        echo "  - would append REQUIRE_API_KEY=true to $omni_env (backup first if it exists)"
    else
        mkdir -p "$(dirname "$omni_env")"
        if [[ -f "$omni_env" ]]; then
            cp -p "$omni_env" "$omni_env.autoos-backup-$(date +%Y%m%d-%H%M%S)"
        fi
        printf '# AutoOS: /v1 is reachable from the LAN - every request must carry a client key.\nREQUIRE_API_KEY=true\n' >>"$omni_env"
        chmod 600 "$omni_env"
        echo "  + appended REQUIRE_API_KEY=true to $omni_env (restart the gateway to apply)"
    fi
fi

# ─── Lingering: units start at boot, not at first login ─────────────────────
user_name="${USER:-$(id -un)}"
linger="$("$LOGINCTL" show-user "$user_name" -p Linger --value 2>/dev/null || true)"
if [[ "$linger" == "yes" ]]; then
    echo "Lingering is on for $user_name: the units start at boot."
elif [[ $DRY -eq 1 ]]; then
    echo "Lingering is off: would try 'loginctl enable-linger $user_name' (without sudo)."
elif "$LOGINCTL" enable-linger "$user_name" >/dev/null 2>&1; then
    echo "Lingering enabled for $user_name: the units start at boot."
else
    echo "Lingering is off and could not be enabled without sudo. Run once:"
    echo "  sudo loginctl enable-linger $user_name"
fi
echo "Remove any time with: $0 --unregister"
