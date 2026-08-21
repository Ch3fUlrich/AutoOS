#!/usr/bin/env bash
# AutoOS terminal UI — colour, layout and a zero-dependency checkbox selector.
#
# Everything user-visible goes through here so colour, NO_COLOR, non-TTY output
# and the log file are handled in exactly one place. Nothing here touches the
# system.
#
# shellcheck shell=bash
# shellcheck disable=SC2034
#   The SYS_*/CAT_*/MENU_*/*_STATE globals below are this module's public
#   interface - they are read by setup.sh, serve.py's probe and the tests,
#   none of which shellcheck can see from here.


# ─── Capability detection ───────────────────────────────────────────────────
AUTOOS_LOG="${AUTOOS_LOG:-}"
AUTOOS_USE_COLOR=0

ui_init() {
    if [[ -n "${NO_COLOR:-}" || -n "${AUTOOS_NO_COLOR:-}" ]]; then
        AUTOOS_USE_COLOR=0
    elif [[ -t 1 ]] && [[ "${TERM:-dumb}" != "dumb" ]]; then
        AUTOOS_USE_COLOR=1
    fi
    if [[ -n "$AUTOOS_LOG" ]]; then
        mkdir -p "$(dirname "$AUTOOS_LOG")"
        printf '=== AutoOS run %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$AUTOOS_LOG"
    fi
}

ui_is_interactive() { [[ -t 0 && -t 1 ]]; }

# Steel blue accent; the warm ramp is reserved for severity so a warning never
# reads as decoration.
_c() {
    local style="$1"
    ((AUTOOS_USE_COLOR)) || { printf ''; return 0; }
    case "$style" in
        reset)   printf '\033[0m' ;;
        dim)     printf '\033[2;38;5;245m' ;;
        accent)  printf '\033[1;38;5;74m' ;;
        heading) printf '\033[1;38;5;252m' ;;
        muted)   printf '\033[38;5;245m' ;;
        ok)      printf '\033[38;5;71m' ;;
        warn)    printf '\033[38;5;179m' ;;
        err)     printf '\033[1;38;5;167m' ;;
        sel)     printf '\033[1;38;5;80m' ;;
    esac
}

_log() {
    [[ -n "$AUTOOS_LOG" ]] || return 0
    local level="$1"; shift
    # strip ANSI before it reaches the log
    printf '[%s] %-5s %s\n' "$(date '+%H:%M:%S')" "$level" \
        "$(printf '%s' "$*" | sed -e 's/\x1b\[[0-9;]*m//g')" >>"$AUTOOS_LOG"
}

ui_line()  { printf '%s\n' "$*"; _log PLAIN "$*"; }
ui_ok()    { printf '  %s+%s %s\n' "$(_c ok)"     "$(_c reset)" "$*"; _log OK    "$*"; }
ui_warn()  { printf '  %s!%s %s%s%s\n' "$(_c warn)" "$(_c reset)" "$(_c warn)" "$*" "$(_c reset)"; _log WARN  "$*"; }
ui_err()   { printf '  %sx%s %s%s%s\n' "$(_c err)"  "$(_c reset)" "$(_c err)"  "$*" "$(_c reset)"; _log ERROR "$*"; }
ui_step()  { printf '  %s>%s %s\n' "$(_c accent)" "$(_c reset)" "$*"; _log STEP  "$*"; }
ui_info()  { printf '  %s-%s %s\n' "$(_c muted)"  "$(_c reset)" "$*"; _log INFO  "$*"; }
ui_muted() { printf '%s%s%s\n' "$(_c muted)" "$*" "$(_c reset)"; _log MUTED "$*"; }

ui_banner() {
    local bar; bar="$(printf '─%.0s' {1..62})"
    printf '\n%s%s%s\n' "$(_c accent)" "$bar" "$(_c reset)"
    printf '  %sAutoOS%s%s  ·  post-install provisioning%s\n' \
        "$(_c accent)" "$(_c reset)" "$(_c muted)" "$(_c reset)"
    [[ -n "${1:-}" ]] && printf '  %s%s%s\n' "$(_c muted)" "$1" "$(_c reset)"
    printf '%s%s%s\n\n' "$(_c accent)" "$bar" "$(_c reset)"
}

ui_section() {
    local title="$1" pad
    pad=$(( 60 - ${#title} )); ((pad < 0)) && pad=0
    printf '\n%s── %s %s%s%s\n' \
        "$(_c heading)" "$title" "$(_c dim)" "$(printf '─%.0s' $(seq 1 $pad))" "$(_c reset)"
    _log SECT "$title"
}

ui_kv() {
    local key="$1" value="$2" style="${3:-}"
    if [[ -n "$style" ]]; then
        printf '  %s%-22s%s %s%s%s\n' "$(_c muted)" "$key" "$(_c reset)" "$(_c "$style")" "$value" "$(_c reset)"
    else
        printf '  %s%-22s%s %s\n' "$(_c muted)" "$key" "$(_c reset)" "$value"
    fi
    _log KV "$key = $value"
}

# ─── Prompts ────────────────────────────────────────────────────────────────
ui_confirm() {
    local question="$1" default="${2:-y}" hint answer
    ui_is_interactive || { [[ "$default" == "y" ]]; return; }
    if [[ "$default" == "y" ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
    while true; do
        printf '  %s?%s %s %s%s%s ' "$(_c accent)" "$(_c reset)" "$question" "$(_c muted)" "$hint" "$(_c reset)"
        read -r answer || answer=""
        answer="${answer,,}"
        if [[ -z "$answer" ]]; then answer="$default"; fi
        case "$answer" in
            y|yes|j|ja) return 0 ;;
            n|no|nein)  return 1 ;;
            *) ui_warn "Please answer y or n." ;;
        esac
    done
}

# ui_ask <var-name> <question> [default] [help]
ui_ask() {
    local __var="$1" question="$2" default="${3:-}" help="${4:-}" value shown=""
    if ! ui_is_interactive; then printf -v "$__var" '%s' "$default"; return 0; fi
    [[ -n "$help" ]] && printf '    %s%s%s\n' "$(_c muted)" "$help" "$(_c reset)"
    if [[ -n "$default" ]]; then shown=" [$default]"; fi
    printf '  %s?%s %s%s%s%s: ' "$(_c accent)" "$(_c reset)" "$question" "$(_c muted)" "$shown" "$(_c reset)"
    read -r value || value=""
    if [[ -z "$value" ]]; then value="$default"; fi
    printf -v "$__var" '%s' "$value"
}

# ─── The checkbox selector ──────────────────────────────────────────────────
# Caller fills these parallel arrays, then calls ui_menu.
# Result lands in MENU_RESULT (space-separated ids); returns 1 if cancelled.
declare -a MENU_ID MENU_NAME MENU_DESC MENU_GROUP MENU_SEL
MENU_RESULT=""

ui_menu() {
    local title="${1:-Select components}" footer="${2:-}"
    local total=${#MENU_ID[@]}
    (( total == 0 )) && { MENU_RESULT=""; return 0; }

    if ! ui_is_interactive; then
        local out=""
        for ((i = 0; i < total; i++)); do
            if (( MENU_SEL[i] )); then out+="${MENU_ID[i]} "; fi
        done
        MENU_RESULT="${out% }"
        return 0
    fi

    # Build the render order: a group header row before each group's items.
    local -a row_kind row_text row_idx
    local last_group="" i
    for ((i = 0; i < total; i++)); do
        if [[ "${MENU_GROUP[i]}" != "$last_group" ]]; then
            row_kind+=("header"); row_text+=("${MENU_GROUP[i]}"); row_idx+=(-1)
            last_group="${MENU_GROUP[i]}"
        fi
        row_kind+=("item"); row_text+=("${MENU_NAME[i]}"); row_idx+=("$i")
    done

    local nrows=${#row_kind[@]} cursor=0 top=0 rendered=0 viewport=16
    local term_h; term_h=$(tput lines 2>/dev/null || echo 30)
    viewport=$(( term_h - 12 )); (( viewport < 6 )) && viewport=6; (( viewport > 22 )) && viewport=22

    while [[ "${row_kind[cursor]}" != "item" ]] && (( cursor < nrows - 1 )); do ((cursor++)); done

    _menu_next() {  # $1 = delta ; echoes new cursor
        local d="$1" i="$cursor"
        while true; do
            i=$(( i + d ))
            (( i < 0 || i >= nrows )) && { echo "$cursor"; return; }
            [[ "${row_kind[i]}" == "item" ]] && { echo "$i"; return; }
        done
    }

    printf '\033[?25l'   # hide cursor
    trap 'printf "\033[?25h"' RETURN

    while true; do
        (( cursor < top )) && top=$cursor
        (( cursor >= top + viewport )) && top=$(( cursor - viewport + 1 ))

        local buf="" selcount=0 r
        for ((i = 0; i < total; i++)); do (( MENU_SEL[i] )) && ((selcount++)); done
        (( rendered > 0 )) && buf+=$'\033'"[${rendered}A"

        buf+=$'\033[2K'"  $(_c heading)${title}$(_c reset)$(_c muted)   ${selcount} of ${total} selected$(_c reset)"$'\n'
        buf+=$'\033[2K'$'\n'
        local lines=2

        for ((r = top; r < nrows && r < top + viewport; r++)); do
            if [[ "${row_kind[r]}" == "header" ]]; then
                buf+=$'\033[2K'"   $(_c accent)${row_text[r]^^}$(_c reset)"$'\n'
            else
                local idx=${row_idx[r]} mark box name
                (( r == cursor )) && mark="$(_c sel)>$(_c reset)" || mark=" "
                if (( MENU_SEL[idx] )); then box="$(_c ok)[x]$(_c reset)"; else box="$(_c dim)[ ]$(_c reset)"; fi
                name=$(printf '%-26s' "${MENU_NAME[idx]}")
                (( r == cursor )) && name="$(_c sel)${name}$(_c reset)"
                buf+=$'\033[2K'"  ${mark} ${box} ${name} $(_c muted)${MENU_DESC[idx]}$(_c reset)"$'\n'
            fi
            ((lines++))
        done

        local more=$(( nrows - top - viewport ))
        if (( more > 0 )); then
            buf+=$'\033[2K'"      $(_c dim)... ${more} more below$(_c reset)"$'\n'
        else
            buf+=$'\033[2K'$'\n'
        fi
        ((lines++))
        buf+=$'\033[2K'$'\n'
        buf+=$'\033[2K'"  $(_c dim)UP/DOWN move   SPACE toggle   A all   N none   ENTER confirm   ESC cancel$(_c reset)"$'\n'
        lines=$(( lines + 2 ))
        if [[ -n "$footer" ]]; then
            buf+=$'\033[2K'"  $(_c muted)${footer}$(_c reset)"$'\n'; ((lines++))
        fi

        printf '%s' "$buf"
        rendered=$lines

        local key rest
        IFS= read -rsn1 key || key=""
        case "$key" in
            $'\033')
                # Could be a bare ESC or the start of an arrow sequence.
                if IFS= read -rsn2 -t 0.05 rest; then
                    case "$rest" in
                        '[A') cursor=$(_menu_next -1) ;;
                        '[B') cursor=$(_menu_next 1) ;;
                        '[5') read -rsn1 -t 0.05 _; for _ in 1 2 3 4 5; do cursor=$(_menu_next -1); done ;;
                        '[6') read -rsn1 -t 0.05 _; for _ in 1 2 3 4 5; do cursor=$(_menu_next 1); done ;;
                    esac
                else
                    printf '\033[?25h\n'; MENU_RESULT=""; return 1
                fi
                ;;
            ' ')
                local idx=${row_idx[cursor]}
                if (( MENU_SEL[idx] )); then MENU_SEL[idx]=0; else MENU_SEL[idx]=1; fi
                ;;
            'a'|'A') for ((i = 0; i < total; i++)); do MENU_SEL[i]=1; done ;;
            'n'|'N') for ((i = 0; i < total; i++)); do MENU_SEL[i]=0; done ;;
            'q'|'Q') printf '\033[?25h\n'; MENU_RESULT=""; return 1 ;;
            ''|$'\n')
                printf '\033[?25h\n'
                local out=""
                for ((i = 0; i < total; i++)); do (( MENU_SEL[i] )) && out+="${MENU_ID[i]} "; done
                MENU_RESULT="${out% }"
                return 0
                ;;
        esac
    done
}
