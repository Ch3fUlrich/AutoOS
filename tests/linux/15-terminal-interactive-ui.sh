# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Terminal interactive UI ────────────────────────────────────────────────
describe "terminal interactive UI"

if it "ui_select_radio returns default when non-interactive"; then
    (
        export AUTOOS_NONINTERACTIVE=1
        res=""
        ui_select_radio res "Choose profile" "light" "workstation|Workstation|Dev tools|" "light|Light|Pi tools|"
        [[ "$res" == "light" ]]
    ) && pass || fail "did not return default"
fi

if it "ui_select_radio supports arrow key and number selection"; then
    (
        ui_is_interactive() { return 0; }
        res=""
        ui_select_radio res "Choose profile" "workstation" "workstation|Workstation|Dev tools|" "light|Light|Pi tools|" < <(printf '\033[B\n') >/dev/null 2>&1
        [[ "$res" == "light" ]] || exit 1
        res=""
        ui_select_radio res "Choose profile" "light" "workstation|Workstation|Dev tools|" "light|Light|Pi tools|" < <(printf '1\n') >/dev/null 2>&1
        [[ "$res" == "workstation" ]]
    ) && pass || fail "arrow key or number selection failed"
fi

if it "ui_confirm supports interactive toggle and direct key shortcuts"; then
    (
        ui_is_interactive() { return 0; }
        ui_confirm "Proceed?" y < <(printf '\033[C\n') >/dev/null 2>&1 && exit 1
        ui_confirm "Proceed?" y < <(printf 'n') >/dev/null 2>&1 && exit 1
        ui_confirm "Proceed?" n < <(printf 'y') >/dev/null 2>&1 || exit 1
        exit 0
    ) && pass || fail "interactive confirm failed"
fi

if it "ui_menu supports group toggle and invert selection"; then
    (
        ui_is_interactive() { return 0; }
        MENU_ID=("c1" "c2" "c3")
        MENU_NAME=("C1" "C2" "C3")
        MENU_DESC=("D1" "D2" "D3")
        MENU_GROUP=("G1" "G1" "G2")
        MENU_SEL=(0 0 0)
        MENU_INSTALLED=(0 0 0)
        ui_menu "Select" "" < <(printf 'g\n') >/dev/null 2>&1
        [[ "$MENU_RESULT" == "c1 c2" ]] || exit 1
        MENU_SEL=(1 0 0)
        ui_menu "Select" "" < <(printf 'i\n') >/dev/null 2>&1
        [[ "$MENU_RESULT" == "c2 c3" ]]
    ) && pass || fail "group toggle or invert selection failed"
fi

