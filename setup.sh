#!/usr/bin/env bash
# AutoOS — post-install provisioning for Linux.
#
# One entry point. Detects the machine, suggests a profile, lets you tick exactly
# what you want, shows the plan, then installs it.
#
#   detect -> profile -> select -> plan -> confirm -> execute -> report
#
# Nothing is installed before the confirmation step.

set -euo pipefail

AUTOOS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$AUTOOS_ROOT/lib/linux"

# shellcheck source=lib/linux/ui.sh
. "$LIB/ui.sh"
# shellcheck source=lib/linux/detect.sh
. "$LIB/detect.sh"
# shellcheck source=lib/linux/catalog.sh
. "$LIB/catalog.sh"
# shellcheck source=lib/linux/install.sh
. "$LIB/install.sh"

# Resolved properly after detect_system; this is only the fallback for the
# catalog-only modes that run before detection.
CATALOG="$AUTOOS_ROOT/catalog/linux.json"
PROFILE=""; ONLY=""; ASSUME_YES=0; DO_SERVE=0; PORT=8777; BIND="127.0.0.1"
LIST_ONLY=0; CHECK_ONLY=0; DO_UNDO=0; FROM_STATE=""
STATE_PATH="$AUTOOS_ROOT/.autoos-state.json"
STATE_PROFILE=""; STATE_SELECTED=""

usage() {
    cat <<'EOF'
AutoOS — post-install provisioning for Linux

  ./setup.sh [options]

  --profile <name>   workstation | ai-coding | light | server | custom
  --only <ids>       Comma-separated component ids; installs only these
  --dry-run          Print every command without changing anything
  --yes, -y          Non-interactive: take profile defaults, skip confirmation
  --no-color         Disable ANSI colour
  --serve            Browser UI instead of the terminal menu (headless boxes)
  --port N           Port for --serve (default 8777)
  --bind ADDR        Bind address for --serve (default 127.0.0.1)
  --list             Print the catalog and exit
  --check-catalog    Validate the catalog and exit non-zero on any problem

  --from-state FILE  Replay a previous run's selection and answers
  --save-state FILE  Where to write this run's state (default .autoos-state.json)
  --no-verify        Skip the post-install "does it actually work" check
  --undo             Restore files AutoOS backed up (does NOT uninstall packages)
  --help, -h         This text

Examples:
  ./setup.sh                                  interactive
  ./setup.sh --from-state .autoos-state.json  repeat a previous machine's setup
  ./setup.sh --profile light --dry-run        what a Raspberry Pi would get
  ./setup.sh --only claude-code,tailscale -y  just those two, plus dependencies
  ./setup.sh --serve                          drive it from a browser
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile) PROFILE="${2:-}"; shift 2 ;;
        --only)    ONLY="${2:-}"; shift 2 ;;
        --dry-run) AUTOOS_DRY_RUN=1; shift ;;
        --yes|-y)  ASSUME_YES=1; shift ;;
        --no-color) AUTOOS_NO_COLOR=1; shift ;;
        --serve)   DO_SERVE=1; shift ;;
        --port)    PORT="${2:-8777}"; shift 2 ;;
        --bind)    BIND="${2:-127.0.0.1}"; shift 2 ;;
        --list)    LIST_ONLY=1; shift ;;
        --check-catalog) CHECK_ONLY=1; shift ;;
        --from-state) FROM_STATE="${2:-}"; shift 2 ;;
        --save-state) STATE_PATH="${2:-}"; shift 2 ;;
        --no-verify)  AUTOOS_VERIFY=0; shift ;;
        --undo)       DO_UNDO=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n\n' "$1"; usage; exit 2 ;;
    esac
done

ui_init

# ─── Catalog-only modes ─────────────────────────────────────────────────────
if (( CHECK_ONLY )); then
    # Validate every catalog, not just this machine's: a typo in macos.json must
    # fail CI on a Linux runner too.
    rc=0
    for cat in "$AUTOOS_ROOT"/catalog/*.json; do
        [[ -f "$cat" ]] || continue
        if catalog_validate "$cat"; then ui_ok "$(basename "$cat") is valid."
        else ui_err "$(basename "$cat") has problems."; rc=1; fi
    done
    exit $rc
fi

if (( LIST_ONLY )); then
    detect_system
    if [[ "${SYS_OS:-linux}" == "macos" ]]; then CATALOG="$AUTOOS_ROOT/catalog/macos.json"; fi
    catalog_load "$CATALOG" "$SYS_ARCH" "$SYS_IS_HEADLESS"
    last=""
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        if [[ "${CAT_GROUP[i]}" != "$last" ]]; then ui_section "${CAT_GROUP[i]}"; last="${CAT_GROUP[i]}"; fi
        printf '  %-18s %-8s %s\n' "${CAT_ID[i]}" "${CAT_PROVIDER[i]}" "${CAT_DESC[i]}"
        [[ -n "${CAT_PROFILES[i]}" ]] && printf '  %-18s profiles: %s\n' "" "${CAT_PROFILES[i]}"
    done
    exit 0
fi

# ─── 1. Detect ──────────────────────────────────────────────────────────────
AUTOOS_LOG="$AUTOOS_ROOT/logs/autoos-$(date +%Y%m%d-%H%M%S).log"
ui_init
if [[ "$(uname -s)" == "Darwin" ]]; then ui_banner "macOS"; else ui_banner "Linux"; fi

detect_system

# One entry point, two catalogs: macOS is Homebrew, everything else is apt.
if [[ "${SYS_OS:-linux}" == "macos" ]]; then
    CATALOG="$AUTOOS_ROOT/catalog/macos.json"
fi

ui_section "Detected system"
ui_kv "Distribution"    "$SYS_DISTRO_NAME"
ui_kv "Architecture"    "$SYS_ARCH"
ui_kv "Model"           "$SYS_MODEL"
ui_kv "CPU"             "$SYS_CPU_NAME — $SYS_CPU_CORES cores"
ui_kv "Memory"          "$SYS_RAM_GB GB"
ui_kv "Free disk"       "$SYS_FREE_DISK_GB GB"
ui_kv "User"            "$SYS_USER"
if (( SYS_IS_ROOT )); then ui_kv "Privileges" "root" ok
elif (( SYS_CAN_SUDO )); then ui_kv "Privileges" "sudo available" ok
else ui_kv "Privileges" "unprivileged" warn; fi
ui_kv "Display"         "$( ((SYS_IS_HEADLESS)) && echo "headless" || echo "graphical session" )"

ui_kv "Environment"     "${SYS_ENVIRONMENT:-unknown}"

present=""
if (( SYS_HAS_GIT ));    then present+="git ";    fi
if (( SYS_HAS_NODE ));   then present+="node ";   fi
if (( SYS_HAS_DOCKER )); then present+="docker "; fi
if (( SYS_HAS_ZSH ));    then present+="zsh ";    fi
ui_kv "Already present" "${present:-nothing relevant}"

detect_blockers
if (( ${#BLOCKER_MESSAGE[@]} )); then
    ui_section "Warnings"
    fatal=0
    for ((i = 0; i < ${#BLOCKER_MESSAGE[@]}; i++)); do
        if [[ "${BLOCKER_SEVERITY[i]}" == "error" ]]; then ui_err "${BLOCKER_MESSAGE[i]}"; fatal=1
        else ui_warn "${BLOCKER_MESSAGE[i]}"; fi
        ui_muted "    ${BLOCKER_FIX[i]}"
    done
    if (( fatal && ! AUTOOS_DRY_RUN )); then
        ui_err "Cannot continue until the errors above are resolved."
        exit 1
    fi
fi

# ─── Undo ───────────────────────────────────────────────────────────────────
if (( DO_UNDO )); then
    autoos_undo "$ASSUME_YES"
    exit 0
fi

# ─── Browser mode ───────────────────────────────────────────────────────────
if (( DO_SERVE )); then
    # shellcheck source=lib/linux/serve.sh
    . "$LIB/serve.sh"
    serve_start "$AUTOOS_ROOT" "$PORT" "$BIND"
    exit 0
fi

catalog_load "$CATALOG" "$SYS_ARCH" "$SYS_IS_HEADLESS"

# ─── 2. Profile ─────────────────────────────────────────────────────────────
SUGGESTED="$(suggested_profile)"
if [[ -n "$FROM_STATE" ]]; then
    autoos_state_load "$FROM_STATE" || exit 1
    PROFILE="${STATE_PROFILE:-custom}"
elif [[ -n "$ONLY" ]]; then
    PROFILE="custom"
elif [[ -z "$PROFILE" ]]; then
    ui_section "Profile"
    printf '  Suggested for this machine: %s%s%s\n\n' "$(_c accent)" "$SUGGESTED" "$(_c reset)"
    while IFS=$'\t' read -r pname pdesc; do
        mark=" "; [[ "$pname" == "$SUGGESTED" ]] && mark="*"
        printf '  %s %-13s %s\n' "$mark" "$pname" "$pdesc"
    done < <(catalog_profile_list "$CATALOG")
    printf '\n'
    if (( ASSUME_YES )); then PROFILE="$SUGGESTED"
    else ui_ask PROFILE "Profile" "$SUGGESTED"; fi
fi
case "$PROFILE" in
    workstation|ai-coding|light|server|custom) ;;
    *) ui_err "Unknown profile '$PROFILE'"; exit 2 ;;
esac
ui_ok "Using profile: $PROFILE"

# ─── 3. Select ──────────────────────────────────────────────────────────────
if [[ -n "$FROM_STATE" ]]; then
    SELECTED="$STATE_SELECTED"
    for id in $SELECTED; do
        catalog_index_of "$id" >/dev/null || { ui_warn "state names unknown component '$id' - skipping"; }
    done
    # keep only ids this machine actually offers
    kept=""
    for id in $SELECTED; do
        catalog_index_of "$id" >/dev/null 2>&1 && kept+="$id "
    done
    SELECTED="${kept% }"
elif [[ -n "$ONLY" ]]; then
    SELECTED="${ONLY//,/ }"
    for id in $SELECTED; do
        catalog_index_of "$id" >/dev/null || { ui_err "Unknown component id: $id"; exit 1; }
    done
elif (( ASSUME_YES )); then
    SELECTED="$(catalog_profile_defaults "$PROFILE")"
else
    defaults=" $(catalog_profile_defaults "$PROFILE") "
    MENU_ID=(); MENU_NAME=(); MENU_DESC=(); MENU_GROUP=(); MENU_SEL=()
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        MENU_ID+=("${CAT_ID[i]}");   MENU_NAME+=("${CAT_NAME[i]}")
        MENU_DESC+=("${CAT_DESC[i]}"); MENU_GROUP+=("${CAT_GROUP[i]}")
        if [[ "$defaults" == *" ${CAT_ID[i]} "* ]]; then MENU_SEL+=(1); else MENU_SEL+=(0); fi
    done
    if ! ui_menu "Choose what to install" "Dependencies are added automatically."; then
        ui_warn "Cancelled — nothing was changed."
        exit 0
    fi
    SELECTED="$MENU_RESULT"
fi

if [[ -z "${SELECTED// /}" ]]; then
    ui_warn "Nothing selected — nothing to do."
    exit 0
fi

# ─── 4. Plan ────────────────────────────────────────────────────────────────
# shellcheck disable=SC2086  # SELECTED is a deliberate word list
catalog_resolve $SELECTED >/dev/null

ui_section "Plan"
n=0
for id in $PLAN_IDS; do
    n=$((n + 1))
    i="$(catalog_index_of "$id")"
    tag=""
    if [[ " $PLAN_AUTO " == *" $id "* ]]; then tag="$(_c muted)(dependency)$(_c reset)"; fi
    printf '  %2d. %-22s %-8s %s %s\n' "$n" "${CAT_NAME[i]}" "${CAT_PROVIDER[i]}" "${CAT_PACKAGE[i]}" "$tag"
    if [[ -n "${CAT_NOTES[i]}" ]]; then ui_muted "      ${CAT_NOTES[i]}"; fi
done
auto_count=0
for _ in $PLAN_AUTO; do auto_count=$((auto_count + 1)); done
printf '\n'
ui_info "$n component(s); $auto_count pulled in as dependencies."

# ─── 5. Questions (all of them, before anything is touched) ─────────────────
asked=" "
for id in $PLAN_IDS; do
    i="$(catalog_index_of "$id")"
    key="${CAT_PROMPT[i]}"
    [[ -z "$key" ]] && continue
    [[ "$asked" == *" $key "* ]] && continue
    asked+="$key "
    q="$(catalog_prompt_field "$CATALOG" "$key" question)"
    d="$(catalog_prompt_field "$CATALOG" "$key" default)"
    h="$(catalog_prompt_field "$CATALOG" "$key" help)"
    # An AUTOOS_ANSWER_<KEY> environment variable pre-answers the prompt; this is
    # how --serve passes the browser's answers through to the same code path.
    env_key="AUTOOS_ANSWER_$(printf '%s' "$key" | tr '[:lower:]-' '[:upper:]_')"
    if [[ -n "${!env_key:-}" ]]; then
        AUTOOS_ANSWERS["$key"]="${!env_key}"
    elif (( ASSUME_YES )); then
        AUTOOS_ANSWERS["$key"]="$d"
    else
        if [[ "$asked" == " $key " ]]; then ui_section "A few questions"; fi
        # Declared here because ui_ask assigns it via printf -v (which the
        # linter cannot follow) and an unset name would trip set -u.
        reply=""
        ui_ask reply "$q" "$d" "$h"
        AUTOOS_ANSWERS["$key"]="$reply"
    fi
done

# ─── 6. Confirm ─────────────────────────────────────────────────────────────
if (( AUTOOS_DRY_RUN )); then
    ui_warn "DRY RUN — no changes will be made."
elif (( ! ASSUME_YES )); then
    printf '\n'
    if ! ui_confirm "Install these $n component(s)?" y; then
        ui_warn "Cancelled — nothing was changed."
        exit 0
    fi
fi

# ─── 7. Execute ─────────────────────────────────────────────────────────────
ui_section "Installing"
installed=0; skipped=0; failed=0; failed_names=""
installed_names=""; skipped_names=""; unverified=0
step=0
for id in $PLAN_IDS; do
    step=$((step + 1))
    i="$(catalog_index_of "$id")"
    ui_step "[$step/$n] ${CAT_NAME[i]}"
    install_component "${CAT_PROVIDER[i]}" "${CAT_PACKAGE[i]}" "${CAT_CASK[i]:-0}" || INSTALL_STATE="failed"
    case "$INSTALL_STATE" in
        installed)
            run_post_install "${CAT_POST[i]}"
            installed=$((installed + 1)); installed_names+="${CAT_ID[i]} "
            verify_component "${CAT_VERIFY[i]}" "${CAT_NAME[i]}"
            if [[ "$VERIFY_STATE" == "unverified" ]]; then unverified=$((unverified + 1)); fi
            ui_ok "${CAT_NAME[i]} done"
            ;;
        skipped)
            run_post_install "${CAT_POST[i]}"
            skipped=$((skipped + 1)); skipped_names+="${CAT_ID[i]} "
            ;;
        failed)
            failed=$((failed + 1)); failed_names+="${CAT_NAME[i]} "
            ui_err "${CAT_NAME[i]} failed"
            ;;
    esac
done

# ─── 8. Report ──────────────────────────────────────────────────────────────
ui_section "Summary"
ui_kv "Installed"       "$installed" ok
ui_kv "Already present" "$skipped"   muted
if (( unverified )); then ui_kv "Installed but unverified" "$unverified" warn; fi
if (( failed )); then ui_kv "Failed" "$failed" err; else ui_kv "Failed" "0" muted; fi
if (( failed )); then
    printf '\n'
    for f in $failed_names; do ui_err "$f"; done
    ui_muted "Re-run to retry only the failures; everything else reports as already present."
fi
printf '\n'
# ─── Where it landed ────────────────────────────────────────────────────────
# The counts above say how many, not where or how to start them. Resolved from
# the live machine, so a blank means genuinely not found rather than a guess.
landed="${installed_names}${skipped_names}"
if [[ -n "${landed// /}" ]]; then
    ui_section "Where to find them"
    if (( AUTOOS_DRY_RUN )); then
        ui_muted "Dry run installed nothing — these are the locations as they stand now."
    fi
    for id in $landed; do
        i="$(catalog_index_of "$id")"
        if launch_hint "${CAT_NAME[i]}" "${CAT_ID[i]}" "${CAT_VERIFY[i]}"; then
            ui_kv "${CAT_NAME[i]}" "$LAUNCH_HOW"
            [[ -n "$LAUNCH_PATH" ]] && ui_muted "                         $LAUNCH_PATH"
        else
            ui_kv "${CAT_NAME[i]}" "no launcher found yet" warn
            ui_muted "                         log out and back in, or open a new shell, then re-run"
        fi
    done
fi

# Saved last, so a replay reflects what actually happened rather than what was planned.
if [[ -n "$STATE_PATH" ]]; then
    autoos_state_save "$STATE_PATH" "$PROFILE" "$SELECTED" \
        "$installed_names" "$skipped_names" "$failed_names"
fi

ui_info "Some changes (PATH, shell, docker group) need a new login to take effect."
ui_muted "Repeat this setup elsewhere with:  ./setup.sh --from-state $STATE_PATH"
if (( failed )); then exit 1; fi
exit 0
