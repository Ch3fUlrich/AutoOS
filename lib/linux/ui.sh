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

ui_is_interactive() {
    [[ -n "${AUTOOS_NONINTERACTIVE:-}" ]] && return 1
    [[ -t 0 && -t 1 ]]
}

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
        bold)    printf '\033[1m' ;;
        inv)     printf '\033[7m' ;;
        badge)   printf '\033[38;5;111m' ;;
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
    local question="$1" default="${2:-y}"
    if ! ui_is_interactive; then
        [[ "$default" == "y" ]] && return 0 || return 1
    fi

    # Interactive two-button toggle: [ Yes ]  [ No ]
    local choice=0
    [[ "$default" != "y" ]] && choice=1

    printf '\033[?25l'   # hide cursor
    # shellcheck disable=SC2064
    trap 'printf "\033[?25h"' RETURN

    local first_render=1
    while true; do
        local btn_yes btn_no
        if (( choice == 0 )); then
            btn_yes="$(_c sel)$(_c inv) [ Yes ] $(_c reset)"
            btn_no="$(_c dim)  No   $(_c reset)"
        else
            btn_yes="$(_c dim)  Yes   $(_c reset)"
            btn_no="$(_c sel)$(_c inv) [ No ] $(_c reset)"
        fi

        if (( first_render )); then
            printf '  %s?%s %s  %s %s' "$(_c accent)" "$(_c reset)" "$question" "$btn_yes" "$btn_no"
            first_render=0
        else
            printf '\r\033[2K  %s?%s %s  %s %s' "$(_c accent)" "$(_c reset)" "$question" "$btn_yes" "$btn_no"
        fi

        local key rest
        IFS= read -rsn1 key || key=""
        case "$key" in
            $'\033')
                if IFS= read -rsn2 -t 0.05 rest; then
                    case "$rest" in
                        '[C'|'[D'|'[A'|'[B') choice=$(( 1 - choice )) ;;
                    esac
                else
                    printf '\033[?25h\r\033[2K  %s?%s %s  %s\n' "$(_c accent)" "$(_c reset)" "$question" "$(_c warn)Cancelled$(_c reset)"
                    return 1
                fi
                ;;
            $'\t'|' '|'h'|'l'|'j'|'k'|'H'|'L'|'J'|'K') choice=$(( 1 - choice )) ;;
            'y'|'Y') choice=0; break ;;
            'n'|'N') choice=1; break ;;
            ''|$'\n') break ;;
        esac
    done

    printf '\033[?25h'
    if (( choice == 0 )); then
        printf '\r\033[2K  %s?%s %s  %s\n' "$(_c accent)" "$(_c reset)" "$question" "$(_c ok)Yes$(_c reset)"
        return 0
    else
        printf '\r\033[2K  %s?%s %s  %s\n' "$(_c accent)" "$(_c reset)" "$question" "$(_c warn)No$(_c reset)"
        return 1
    fi
}

# ui_ask <var-name> <question> [default] [help]
ui_ask() {
    local __var="$1" question="$2" default="${3:-}" help="${4:-}" value shown=""
    if ! ui_is_interactive; then printf -v "$__var" '%s' "$default"; return 0; fi
    [[ -n "$help" ]] && printf '    %s%s%s\n' "$(_c muted)" "$help" "$(_c reset)"
    if [[ -n "$default" ]]; then shown=" [$default]"; fi
    printf '  %s?%s %s%s%s%s: ' "$(_c accent)" "$(_c reset)" "$question" "$(_c muted)" "$shown" "$(_c reset)"
    if [[ -t 0 ]] && [[ -n "$default" ]]; then
        read -e -i "$default" -r value 2>/dev/null || read -r value || value=""
    else
        read -r value || value=""
    fi
    if [[ -z "$value" ]]; then value="$default"; fi
    printf -v "$__var" '%s' "$value"
}

# ─── Interactive single-choice radio selector ───────────────────────────────
# ui_select_radio <out-var> <title> <default-key> [item1] [item2] ...
# Each item format: "key|label|desc|badge"
ui_select_radio() {
    local __out="$1" title="$2" default_key="${3:-}"
    shift 3
    local -a items=("$@")
    local total=${#items[@]}

    if (( total == 0 )); then
        printf -v "$__out" '%s' "$default_key"
        return 0
    fi

    local -a keys=() labels=() descs=() badges=()
    local default_idx=0 i
    for ((i = 0; i < total; i++)); do
        local item="${items[i]}"
        local k="" l="" d="" b=""
        IFS='|' read -r k l d b <<<"$item"
        keys+=("$k")
        labels+=("${l:-$k}")
        descs+=("${d:-}")
        badges+=("${b:-}")
        if [[ "$k" == "$default_key" ]]; then
            default_idx=$i
        fi
    done

    if ! ui_is_interactive; then
        printf -v "$__out" '%s' "${keys[default_idx]}"
        return 0
    fi

    local cursor=$default_idx
    local rendered=0

    printf '\033[?25l'   # hide cursor
    # shellcheck disable=SC2064
    trap 'printf "\033[?25h"' RETURN

    while true; do
        local buf=""
        (( rendered > 0 )) && buf+=$'\033'"[${rendered}A"

        buf+=$'\033[2K'"  $(_c heading)${title}:$(_c reset)"$'\n'
        local lines=1

        for ((i = 0; i < total; i++)); do
            local mark radio badge_str desc_str label_str
            if (( i == cursor )); then
                mark="$(_c sel)❯$(_c reset)"
                radio="$(_c sel)(•)$(_c reset)"
                label_str="$(_c sel)$(_c bold)$(printf '%-14s' "${labels[i]}")$(_c reset)"
            else
                mark=" "
                radio="$(_c dim)( )$(_c reset)"
                label_str="$(printf '%-14s' "${labels[i]}")"
            fi

            if [[ -n "${badges[i]}" ]]; then
                badge_str=" $(_c badge)[${badges[i]}]$(_c reset)"
            else
                badge_str=""
            fi

            if [[ -n "${descs[i]}" ]]; then
                desc_str="  $(_c muted)${descs[i]}$(_c reset)"
            else
                desc_str=""
            fi

            buf+=$'\033[2K'"  ${mark} ${radio} ${label_str}${badge_str}${desc_str}"$'\n'
            lines=$(( lines + 1 ))
        done

        buf+=$'\033[2K'$'\n'
        buf+=$'\033[2K'"  $(_c dim)↑↓/jk move   1-$total select   ENTER confirm   ESC default$(_c reset)"$'\n'
        lines=$(( lines + 2 ))

        printf '%s' "$buf"
        rendered=$lines

        local key rest
        IFS= read -rsn1 key || key=""
        case "$key" in
            $'\033')
                if IFS= read -rsn2 -t 0.05 rest; then
                    case "$rest" in
                        '[A') if (( cursor > 0 )); then cursor=$(( cursor - 1 )); else cursor=$(( total - 1 )); fi ;;
                        '[B') if (( cursor < total - 1 )); then cursor=$(( cursor + 1 )); else cursor=0; fi ;;
                    esac
                else
                    cursor=$default_idx
                    break
                fi
                ;;
            'k'|'K') if (( cursor > 0 )); then cursor=$(( cursor - 1 )); else cursor=$(( total - 1 )); fi ;;
            'j'|'J') if (( cursor < total - 1 )); then cursor=$(( cursor + 1 )); else cursor=0; fi ;;
            [1-9])
                local num=$(( key - 1 ))
                if (( num >= 0 && num < total )); then
                    cursor=$num
                fi
                ;;
            'q'|'Q')
                cursor=$default_idx
                break
                ;;
            ' '|$'\n'|'')
                break
                ;;
        esac
    done

    # Clean up the radio menu lines
    if (( rendered > 0 )); then
        local clean_buf=""
        clean_buf+=$'\033'"[${rendered}A"
        for ((i = 0; i < rendered; i++)); do
            clean_buf+=$'\033[2K'$'\n'
        done
        clean_buf+=$'\033'"[${rendered}A"
        printf '%s' "$clean_buf"
    fi
    printf '\033[?25h'

    printf -v "$__out" '%s' "${keys[cursor]}"
    return 0
}

# ─── The checkbox selector ──────────────────────────────────────────────────
# Caller fills these parallel arrays, then calls ui_menu.
# Result lands in MENU_RESULT (space-separated ids); returns 1 if cancelled.
declare -a MENU_ID MENU_NAME MENU_DESC MENU_GROUP MENU_SEL MENU_INSTALLED
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

    while [[ "${row_kind[cursor]}" != "item" ]] && (( cursor < nrows - 1 )); do cursor=$(( cursor + 1 )); done

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
        for ((i = 0; i < total; i++)); do (( MENU_SEL[i] )) && selcount=$(( selcount + 1 )); done
        (( rendered > 0 )) && buf+=$'\033'"[${rendered}A"

        local pct=0 bar=""
        if (( total > 0 )); then
            pct=$(( selcount * 100 / total ))
            local filled=$(( selcount * 10 / total ))
            local empty=$(( 10 - filled ))
            local fill_str="" empty_str=""
            (( filled > 0 )) && fill_str="$(printf '█%.0s' $(seq 1 $filled))"
            (( empty > 0 )) && empty_str="$(printf '░%.0s' $(seq 1 $empty))"
            bar="  $(_c sel)[${fill_str}$(_c dim)${empty_str}$(_c sel)] ${pct}%$(_c reset)"
        fi

        buf+=$'\033[2K'"  $(_c heading)${title}$(_c reset)$(_c muted)   ${selcount} of ${total} selected$(_c reset)${bar}"$'\n'
        buf+=$'\033[2K'$'\n'
        local lines=2

        for ((r = top; r < nrows && r < top + viewport; r++)); do
            if [[ "${row_kind[r]}" == "header" ]]; then
                local gname="${row_text[r]}"
                local gsel=0 gtot=0 j
                for ((j = 0; j < total; j++)); do
                    if [[ "${MENU_GROUP[j]}" == "$gname" ]]; then
                        gtot=$(( gtot + 1 ))
                        (( MENU_SEL[j] )) && gsel=$(( gsel + 1 ))
                    fi
                done
                buf+=$'\033[2K'"   $(_c accent)${gname^^}$(_c reset)  $(_c dim)(${gsel}/${gtot} selected)$(_c reset)"$'\n'
            else
                local idx=${row_idx[r]} mark box name inst_badge=""
                (( r == cursor )) && mark="$(_c sel)❯$(_c reset)" || mark=" "
                if (( MENU_SEL[idx] )); then box="$(_c ok)[✓]$(_c reset)"; else box="$(_c dim)[ ]$(_c reset)"; fi
                name=$(printf '%-26s' "${MENU_NAME[idx]}")
                (( r == cursor )) && name="$(_c sel)${name}$(_c reset)"
                if (( ${#MENU_INSTALLED[@]} > idx && MENU_INSTALLED[idx] )); then
                    inst_badge="$(_c ok)✓ installed$(_c reset) "
                fi
                buf+=$'\033[2K'"  ${mark} ${box} ${name} ${inst_badge}$(_c muted)${MENU_DESC[idx]}$(_c reset)"$'\n'
            fi
            lines=$(( lines + 1 ))
        done

        local more=$(( nrows - top - viewport ))
        if (( more > 0 )); then
            buf+=$'\033[2K'"      $(_c dim)... ${more} more below$(_c reset)"$'\n'
        else
            buf+=$'\033[2K'$'\n'
        fi
        lines=$(( lines + 1 ))
        buf+=$'\033[2K'$'\n'
        buf+=$'\033[2K'"  $(_c dim)↑↓/jk move   SPACE toggle   g group   a all   n none   i invert   ENTER confirm   ESC cancel$(_c reset)"$'\n'
        lines=$(( lines + 2 ))
        if [[ -n "$footer" ]]; then
            buf+=$'\033[2K'"  $(_c muted)${footer}$(_c reset)"$'\n'; lines=$(( lines + 1 ))
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
            'k'|'K') cursor=$(_menu_next -1) ;;
            'j'|'J') cursor=$(_menu_next 1) ;;
            ' ')
                local idx=${row_idx[cursor]}
                if (( MENU_SEL[idx] )); then MENU_SEL[idx]=0; else MENU_SEL[idx]=1; fi
                ;;
            'g'|'G')
                local cur_idx=${row_idx[cursor]}
                if (( cur_idx >= 0 )); then
                    local target_group="${MENU_GROUP[cur_idx]}"
                    local all_selected=1 j
                    for ((j = 0; j < total; j++)); do
                        if [[ "${MENU_GROUP[j]}" == "$target_group" ]]; then
                            if (( ! MENU_SEL[j] )); then
                                all_selected=0
                                break
                            fi
                        fi
                    done
                    local new_val=$(( 1 - all_selected ))
                    for ((j = 0; j < total; j++)); do
                        if [[ "${MENU_GROUP[j]}" == "$target_group" ]]; then
                            MENU_SEL[j]=$new_val
                        fi
                    done
                fi
                ;;
            'i'|'I')
                for ((i = 0; i < total; i++)); do
                    MENU_SEL[i]=$(( 1 - MENU_SEL[i] ))
                done
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
