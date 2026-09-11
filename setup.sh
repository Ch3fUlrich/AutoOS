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
# shellcheck source=lib/linux/progress.sh
. "$LIB/progress.sh"
# shellcheck source=lib/linux/detect.sh
. "$LIB/detect.sh"
# shellcheck source=lib/linux/catalog.sh
. "$LIB/catalog.sh"
# shellcheck source=lib/linux/install.sh
. "$LIB/install.sh"
# shellcheck source=lib/linux/download.sh
. "$LIB/download.sh"
# shellcheck source=lib/linux/usb.sh
. "$LIB/usb.sh"

# Resolved properly after detect_system; this is only the fallback for the
# catalog-only modes that run before detection.
CATALOG="$AUTOOS_ROOT/catalog/linux.json"
PROFILE=""; ONLY=""; ASSUME_YES=0; DO_SERVE=0; PORT=8777; BIND="127.0.0.1"
CLAUDE_SESSIONS=""
LIST_ONLY=0; CHECK_ONLY=0; DO_UNDO=0; FROM_STATE=""
STATE_PATH="$AUTOOS_ROOT/.autoos-state.json"
STATE_PROFILE=""; STATE_SELECTED=""
# Task 6 (installer-USB planner): --create-usb and its companions.
DO_CREATE_USB=0; USB_IMAGE=""; USB_KIND="installer"; USB_ENGINE=""; USB_DEVICE=""
USB_WIPE=0; LIST_USB=0; LIST_ENGINES=0

usage() {
    cat <<'EOF'
AutoOS — post-install provisioning for Linux

  ./setup.sh [options]

  --profile <name>   workstation | ai-coding | light | server | custom
  --only <ids>       Comma-separated component ids; installs only these
  --dry-run          Print every command without changing anything
  --yes, -y          Non-interactive: take profile defaults, skip confirmation
  --no-color         Disable ANSI colour
  --claude-sessions [status|snapshot|restore|configure]
                     Report or drive the Claude session autostart
  --serve            Browser UI instead of the terminal menu (headless boxes)
  --port N           Port for --serve (default 8777)
  --bind ADDR        Bind address for --serve (default 127.0.0.1)
  --list             Print the catalog and exit
  --check-catalog    Validate the catalog and exit non-zero on any problem

  --config FILE      Load configuration (default autoos.config.json if present)
  --from-state FILE  Replay a previous run's selection and answers
  --save-state FILE  Where to write this run's state (default .autoos-state.json)
  --no-verify        Skip the post-install "does it actually work" check
  --undo             Restore files AutoOS backed up (does NOT uninstall packages)
                     (does not cover a USB write — that cannot be undone)

  --create-usb           Plan an installer/rescue USB write (--dry-run to preview only)
  --image <id>            catalog/images.json entry to write
  --kind <kind>            installer | live-persistent | full-os (default: installer)
  --engine <id>            catalog/engines.json entry to write with
  --usb-device <path>      Target device, e.g. /dev/sdb
  --wipe-target-disk       Acknowledge the target disk's current contents are lost
  --list-usb              List candidate USB devices and exit
  --list-engines          List write engines available on this machine and exit
  --help, -h         This text

Examples:
  ./setup.sh                                  interactive
  ./setup.sh --from-state .autoos-state.json  repeat a previous machine's setup
  ./setup.sh --profile light --dry-run        what a Raspberry Pi would get
  ./setup.sh --only claude-code,tailscale -y  just those two, plus dependencies
  ./setup.sh --serve                          drive it from a browser
EOF
}

CONFIG_FILE="${AUTOOS_ROOT}/autoos.config.json"
INSTALLED_ONLY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)  CONFIG_FILE="${2:-}"; shift 2 ;;
        --profile) PROFILE="${2:-}"; shift 2 ;;
        --only)    ONLY="${2:-}"; shift 2 ;;
        --dry-run) AUTOOS_DRY_RUN=1; shift ;;
        --yes|-y)  ASSUME_YES=1; shift ;;
        --no-color) AUTOOS_NO_COLOR=1; shift ;;
        --serve)   DO_SERVE=1; shift ;;
        --claude-sessions) CLAUDE_SESSIONS="${2:-status}"
                   case "$CLAUDE_SESSIONS" in
                       status|snapshot|restore|configure) shift 2 ;;
                       *) CLAUDE_SESSIONS="status"; shift ;;
                   esac ;;
        --port)    PORT="${2:-8777}"; shift 2 ;;
        --bind)    BIND="${2:-127.0.0.1}"; shift 2 ;;
        --list)    LIST_ONLY=1; shift ;;
        --installed|--list-installed) INSTALLED_ONLY=1; shift ;;
        --check-catalog) CHECK_ONLY=1; shift ;;
        --from-state) FROM_STATE="${2:-}"; shift 2 ;;
        --save-state) STATE_PATH="${2:-}"; shift 2 ;;
        --no-verify)  AUTOOS_VERIFY=0; shift ;;
        --undo)       DO_UNDO=1; shift ;;
        --create-usb)       DO_CREATE_USB=1; shift ;;
        --image)            USB_IMAGE="${2:-}"; shift 2 ;;
        --kind)              USB_KIND="${2:-installer}"; shift 2 ;;
        --engine)            USB_ENGINE="${2:-}"; shift 2 ;;
        --usb-device)        USB_DEVICE="${2:-}"; shift 2 ;;
        --wipe-target-disk)  USB_WIPE=1; shift ;;
        --list-usb)          LIST_USB=1; shift ;;
        --list-engines)      LIST_ENGINES=1; shift ;;
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
    catalog_probe_installed
    last=""
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        if [[ "${CAT_GROUP[i]}" != "$last" ]]; then ui_section "${CAT_GROUP[i]}"; last="${CAT_GROUP[i]}"; fi
        inst_mark="  "
        if (( CAT_INSTALLED[i] )); then
            inst_mark="$(_c ok)✓$(_c reset) "
        fi
        printf '  %s%-18s %-8s %s\n' "$inst_mark" "${CAT_ID[i]}" "${CAT_PROVIDER[i]}" "${CAT_DESC[i]}"
        [[ -n "${CAT_PROFILES[i]}" ]] && printf '    %-18s profiles: %s\n' "" "${CAT_PROFILES[i]}"
    done
    exit 0
fi

if (( INSTALLED_ONLY )); then
    detect_system
    if [[ "${SYS_OS:-linux}" == "macos" ]]; then CATALOG="$AUTOOS_ROOT/catalog/macos.json"; fi
    catalog_load "$CATALOG" "$SYS_ARCH" "$SYS_IS_HEADLESS"
    catalog_probe_installed
    ui_section "Installed applications"
    found=0
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        if (( CAT_INSTALLED[i] )); then
            printf '  %-26s %-8s %s\n' "${CAT_NAME[i]}" "${CAT_PROVIDER[i]}" "${CAT_DESC[i]}"
            found=$((found + 1))
        fi
    done
    printf '\n'
    if (( found > 0 )); then
        ui_ok "$found installed application(s) detected on this system."
    else
        ui_info "No catalog applications currently installed."
    fi
    exit 0
fi

# ─── USB creation (Task 6) ──────────────────────────────────────────────────
# A pure planner-and-guard flow, standalone like the catalog-only modes
# above: usb_plan "emits commands and runs nothing" (its own docstring in
# lib/linux/usb.sh), so nothing below this point writes to a disk, only to
# stdout. --list-engines and --create-usb --dry-run both have to work
# without ever reaching the main install pipeline's "Detect" banner.
if (( DO_CREATE_USB || LIST_USB || LIST_ENGINES )); then
    # detect_system is silent (sets SYS_* globals; prints nothing) and is
    # skipped whenever EITHER SYS_OS or SYS_ARCH is already set. That is
    # what lets B11/B13's tests simulate macOS and arm64 independently,
    # without a second machine (AGENTS.md §5): they export SYS_OS or
    # SYS_ARCH before invoking this script, and a fresh detect_system()
    # call would otherwise silently overwrite BOTH with this machine's real
    # `uname` — checking only one of the two globals here would still let
    # the other one get clobbered.
    if [[ -z "${SYS_OS:-}" && -z "${SYS_ARCH:-}" ]]; then detect_system; fi

    # setup.sh is the shared Linux AND macOS entry point (AGENTS.md), but
    # nothing past this line was written for `diskutil`-shaped output —
    # refuse cleanly here rather than fail unpredictably inside usb_list.
    if [[ "$(_usb_current_os)" == "macos" ]]; then
        ui_err "USB creation is not supported on macOS"
        exit 1
    fi

    if (( LIST_ENGINES )); then
        ui_section "USB write engines available on this machine"
        while IFS= read -r eid; do
            [[ -z "$eid" ]] && continue
            # mapfile, not `read < <(...)`: a process substitution that
            # produces no output makes `read` fail, which under this
            # script's `set -e` would abort the whole run rather than just
            # this one engine's row.
            eng_fields=()
            mapfile -t eng_fields < <(_engine_field "$eid" name interactive)
            ename="${eng_fields[0]:-$eid}"; einteractive="${eng_fields[1]:-0}"
            tag=""; [[ "$einteractive" == "1" ]] && tag=" (interactive)"
            printf '  %-12s %s%s\n' "$eid" "$ename" "$tag"
        done < <(_engine_list_for_platform "$(_usb_current_os)" "$(_usb_current_arch)")
        exit 0
    fi

    if (( LIST_USB )); then
        ui_section "Candidate USB devices"
        found_usb=0
        while IFS=$'\t' read -r dpath dmodel dsize _drm _dtran; do
            [[ -z "$dpath" ]] && continue
            printf '  %-14s %-24s %s bytes\n' "$dpath" "$dmodel" "$dsize"
            found_usb=$((found_usb + 1))
        done < <(usb_list)
        (( found_usb == 0 )) && ui_info "No USB devices found."
        exit 0
    fi

    # DO_CREATE_USB
    if [[ -z "$USB_IMAGE" || -z "$USB_ENGINE" || -z "$USB_DEVICE" ]]; then
        ui_err "--create-usb requires --image, --engine and --usb-device (--list-engines / --list-usb to discover values)"
        exit 2
    fi
    # AGENTS.md hard rule 3: every destructive action is opt-in and
    # announced. usb_plan below refuses nothing based on --wipe-target-disk
    # (Task 7 owns the actual write and its own confirmation), but a plan
    # for a raw-write engine already means the target's current contents
    # are lost, so the flag is acknowledged here rather than silently
    # accepted-and-ignored if a user thought passing it would gate something.
    if (( USB_WIPE )); then
        ui_muted "Acknowledged: the target device's current contents will be overwritten."
    fi
    # if/else, not `cmd; rc=$?`: under `set -e` (active for this whole
    # script) a bare failing command substitution assignment would exit the
    # script right here, before usb_plan_rc — or the plan/refusal text
    # captured with it — was ever used. A command that is an if-condition
    # is the one form set -e never treats as fatal.
    if usb_plan_out="$(usb_plan "$USB_IMAGE" "$USB_KIND" "$USB_ENGINE" "$USB_DEVICE" 2>&1)"; then
        usb_plan_rc=0
    else
        usb_plan_rc=$?
    fi
    printf '%s\n' "$usb_plan_out"
    if (( usb_plan_rc != 0 )); then
        exit 1
    fi

    # Elevation (B10): checked here, once the plan is known to be coherent,
    # not deferred to write time — the failure this prevents is a dry run
    # that looks perfect followed by "Access is denied" on the one run that
    # matters. uefi-copy writes onto an already-mounted filesystem and needs
    # no elevation; every other engine does (same split usb_require_elevation
    # documents). A dry run still shows the plan above even when unelevated
    # — that gap belongs in the preview, not hidden behind a hard failure
    # that would stop the plan from ever being shown.
    if [[ "$USB_ENGINE" != "uefi-copy" ]]; then
        if ! usb_elev_out="$(usb_require_elevation 2>&1)"; then
            if (( AUTOOS_DRY_RUN )); then
                ui_warn "$usb_elev_out"
            else
                ui_err "$usb_elev_out"
                exit 1
            fi
        fi
    fi
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

# Load catalog to identify all installed applications
catalog_load "$CATALOG" "$SYS_ARCH" "$SYS_IS_HEADLESS"
catalog_probe_installed
mapfile -t installed_apps < <(catalog_installed_names)
if (( ${#installed_apps[@]} > 0 )); then
    inst_str=""
    for app in "${installed_apps[@]}"; do
        [[ -n "$inst_str" ]] && inst_str+=", "
        inst_str+="$app"
    done
    ui_kv "Installed apps" "$(_c ok)✓$(_c reset) ${inst_str} (${#installed_apps[@]} detected)"
else
    ui_kv "Installed apps" "none detected"
fi

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
    # B13: a USB write does not participate in --save-state/--from-state/
    # --undo at all — say so plainly rather than silently leaving a user's
    # last USB write out of what "undo" covers, which they would reasonably
    # expect it to.
    ui_muted "A USB write is not tracked by AutoOS and cannot be undone."
    exit 0
fi

# ─── Browser mode ───────────────────────────────────────────────────────────
# ─── Claude session autostart ───────────────────────────────────────────────
# A first-class entry point rather than "go and run this file under lib/": the
# status of a background service is exactly the thing people need after a reboot.
if [[ -n "$CLAUDE_SESSIONS" ]]; then
    args=("$CLAUDE_SESSIONS")
    (( AUTOOS_DRY_RUN )) && args+=(--dry-run)
    AUTOOS_ROOT="$AUTOOS_ROOT" exec bash "$LIB/claude-sessions.sh" "${args[@]}"
fi

if (( DO_SERVE )); then
    # shellcheck source=lib/linux/serve.sh
    . "$LIB/serve.sh"
    serve_start "$AUTOOS_ROOT" "$PORT" "$BIND"
    exit 0
fi

# ─── 2. Profile ─────────────────────────────────────────────────────────────
if [[ -n "$CONFIG_FILE" && -f "$CONFIG_FILE" ]]; then
    autoos_config_load "$CONFIG_FILE"
    if [[ -z "$PROFILE" && -n "$CONFIG_PROFILE" ]]; then
        PROFILE="$CONFIG_PROFILE"
    fi
fi

SUGGESTED="$(suggested_profile)"
if [[ -n "$FROM_STATE" ]]; then
    autoos_state_load "$FROM_STATE" || exit 1
    PROFILE="${STATE_PROFILE:-custom}"
elif [[ -n "$ONLY" ]]; then
    PROFILE="custom"
elif [[ -z "$PROFILE" ]]; then
    ui_section "Profile"
    if (( ASSUME_YES )); then
        PROFILE="$SUGGESTED"
    else
        p_opts=()
        while IFS=$'\t' read -r pname pdesc; do
            p_badge=""
            [[ "$pname" == "$SUGGESTED" ]] && p_badge="suggested"
            p_opts+=("${pname}|${pname}|${pdesc}|${p_badge}")
        done < <(catalog_profile_list "$CATALOG")
        ui_select_radio PROFILE "Choose installation profile" "$SUGGESTED" "${p_opts[@]}"
    fi
fi
if ! catalog_has_profile "$PROFILE"; then ui_err "Unknown profile '$PROFILE'"; exit 2; fi
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
    MENU_ID=(); MENU_NAME=(); MENU_DESC=(); MENU_GROUP=(); MENU_SEL=(); MENU_INSTALLED=(); MENU_DISABLED=()
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        MENU_ID+=("${CAT_ID[i]}");   MENU_NAME+=("${CAT_NAME[i]}")
        MENU_DESC+=("${CAT_DESC[i]}"); MENU_GROUP+=("${CAT_GROUP[i]}")
        if [[ "$defaults" == *" ${CAT_ID[i]} "* ]]; then MENU_SEL+=(1); else MENU_SEL+=(0); fi
        MENU_INSTALLED+=("${CAT_INSTALLED[i]:-0}")
        if [[ "${CAT_PROVIDER[i]}" == manual ]]; then MENU_DISABLED+=(1); MENU_DESC[i]+=" (vendor setup required)"; else MENU_DISABLED+=(0); fi
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
    if (( CAT_INSTALLED[i] )); then ui_ok "✓ Already installed - package will be skipped"; fi
    if [[ "${CAT_PROVIDER[i]}" == manual ]]; then ui_warn "Action required: ${CAT_HOMEPAGE[i]}"; fi
    if [[ -n "${CAT_NOTES[i]}" ]]; then ui_muted "      ${CAT_NOTES[i]}"; fi
done
auto_count=0
for _ in $PLAN_AUTO; do auto_count=$((auto_count + 1)); done
printf '\n'
ui_info "$n component(s); $auto_count pulled in as dependencies."

# ─── 5. Questions (all of them, before anything is touched) ─────────────────
asked=" "
config_updated=0
for id in $PLAN_IDS; do
    i="$(catalog_index_of "$id")"
    raw_prompts="${CAT_PROMPT[i]}"
    [[ -z "$raw_prompts" ]] && continue
    for key in ${raw_prompts//,/ }; do
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
        elif [[ -n "${AUTOOS_ANSWERS[$key]:-}" ]]; then
            # Pre-filled from configuration file
            :
        elif (( ASSUME_YES )); then
            AUTOOS_ANSWERS["$key"]="$d"
        else
            if [[ "$asked" == " $key " ]]; then ui_section "A few questions"; fi
            # Declared here because ui_ask assigns it via printf -v (which the
            # linter cannot follow) and an unset name would trip set -u.
            reply=""
            ui_ask reply "$q" "$d" "$h"
            AUTOOS_ANSWERS["$key"]="$reply"
            config_updated=1
        fi
    done
done

if (( config_updated )) && [[ -n "$CONFIG_FILE" ]] && (( ! AUTOOS_DRY_RUN )); then
    autoos_config_save "$CONFIG_FILE" "$PROFILE"
    ui_ok "Saved answers to ${CONFIG_FILE}"
fi

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
step=0; manual=0; manual_names=""
for id in $PLAN_IDS; do
    step=$((step + 1))
    i="$(catalog_index_of "$id")"
    ui_step "[$step/$n] ${CAT_NAME[i]}"
    progress_start "$id" "${CAT_NAME[i]}" "$((step-1))" "$n"
    install_rc=0
    install_component "${CAT_PROVIDER[i]}" "${CAT_PACKAGE[i]}" "${CAT_CASK[i]:-0}" || { install_rc=$?; INSTALL_STATE="failed"; }
    case "$INSTALL_STATE" in
        manual) manual=$((manual+1)); manual_names+="$id "; ui_warn "Action required: ${CAT_HOMEPAGE[i]}" ;;
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
    progress_update "$INSTALL_STATE" 1
    if [[ "$install_rc" == 124 ]]; then ui_err "Run stopped after timeout; remaining applications were not started."; break; fi
done

# ─── 8. Report ──────────────────────────────────────────────────────────────
ui_section "Summary"
ui_kv "Installed"       "$installed" ok
ui_kv "Action required" "$manual" warn
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
        "$installed_names" "$skipped_names" "$failed_names" "$manual_names"
fi

ui_info "Some changes (PATH, shell, docker group) need a new login to take effect."
ui_muted "Repeat this setup elsewhere with:  ./setup.sh --from-state $STATE_PATH"
if (( failed )); then exit 1; fi
exit 0
