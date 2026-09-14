#!/usr/bin/env bash
# AutoOS Linux test suite.
#
# Zero dependencies on purpose: the whole point of this repo is to run on a
# machine where nothing is installed yet, so the tests must not need bats.
#
#   bash tests/run-tests.sh            run here
#   bash tests/run-tests.sh --wsl      re-run inside WSL2 (from Windows)
#   bash tests/run-tests.sh --filter catalog
#
# No test installs anything. Providers are asserted on the PLANNED command,
# never on system state.
#
# shellcheck disable=SC2034
#   Several assignments below exist only to configure the sourced libraries
#   (AUTOOS_NO_COLOR, AUTOOS_DRY_RUN, AUTOOS_VERIFY) or to stand in for
#   detection results inside subshells; the linter sees no reader for them.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILTER=""
for arg in "$@"; do
    case "$arg" in
        --wsl)
            wslpath_root="$(wslpath -a "$ROOT" 2>/dev/null || echo "$ROOT")"
            exec wsl.exe -- bash "$wslpath_root/tests/run-tests.sh"
            ;;
        --filter) shift; FILTER="${1:-}" ;;
        --filter=*) FILTER="${arg#--filter=}" ;;
    esac
done

PASS=0; FAIL=0; SKIP=0
CURRENT=""
FAILED_NAMES=()

RED=''; GREEN=''; YELLOW=''; DIM=''; RESET=''
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    RED=$'\033[1;38;5;167m'; GREEN=$'\033[38;5;71m'
    YELLOW=$'\033[38;5;179m'; DIM=$'\033[2;38;5;245m'; RESET=$'\033[0m'
fi

describe() {
    printf '\n%s── %s%s\n' "$DIM" "$1" "$RESET"
}

it() {
    CURRENT="$1"
    if [[ -n "$FILTER" && "$CURRENT" != *"$FILTER"* ]]; then
        CURRENT=""; return 1
    fi
    return 0
}

pass() { PASS=$((PASS+1)); printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$CURRENT"; }
fail() {
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$CURRENT")
    printf '  %s✗%s %s\n' "$RED" "$RESET" "$CURRENT"
    printf '      %s%s%s\n' "$DIM" "$1" "$RESET"
}
skip() { SKIP=$((SKIP+1)); printf '  %s-%s %s %s(%s)%s\n' "$YELLOW" "$RESET" "$CURRENT" "$DIM" "$1" "$RESET"; }

assert_eq() {
    if [[ "$1" == "$2" ]]; then pass; else fail "expected [$2] but got [$1]"; fi
}
assert_contains() {
    if [[ "$1" == *"$2"* ]]; then pass; else fail "expected to contain [$2] in [${1:0:200}]"; fi
}
assert_not_contains() {
    if [[ "$1" != *"$2"* ]]; then pass; else fail "expected NOT to contain [$2]"; fi
}
assert_ok() {
    if [[ "$1" -eq 0 ]]; then pass; else fail "expected exit 0, got $1"; fi
}

# ─── Load the libraries under test ──────────────────────────────────────────
cd "$ROOT" || { echo "cannot enter $ROOT" >&2; exit 1; }
# shellcheck source=../lib/linux/ui.sh
. lib/linux/ui.sh
# shellcheck source=../lib/linux/detect.sh
. lib/linux/detect.sh
# shellcheck source=../lib/linux/catalog.sh
. lib/linux/catalog.sh
# shellcheck source=../lib/linux/install.sh
. lib/linux/install.sh

AUTOOS_NO_COLOR=1
ui_init

printf '%sAutoOS Linux test suite%s  (%s)\n' "$DIM" "$RESET" "$ROOT"

# ─── Catalog schema ─────────────────────────────────────────────────────────
describe "catalog schema"

if it "linux catalog validates"; then
    out="$(catalog_validate catalog/linux.json 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "windows catalog validates too"; then
    out="$(catalog_validate catalog/windows.json 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "a malformed catalog is rejected"; then
    tmp="$(mktemp)"
    cat >"$tmp" <<'JSON'
{"categories":[{"id":"x","name":"X","components":[
  {"id":"Bad_ID","name":"n","description":"d","provider":"nope","package":"p","requires":["ghost"]}]}]}
JSON
    out="$(catalog_validate "$tmp" 2>&1)"; rc=$?
    rm -f "$tmp"
    if [[ $rc -ne 0 && "$out" == *"unknown provider"* && "$out" == *"kebab-case"* && "$out" == *"ghost"* ]]; then
        pass
    else
        fail "expected provider/kebab/ghost problems, got rc=$rc: $out"
    fi
fi

# ─── Catalog loading (the tab-delimiter regression) ─────────────────────────
describe "catalog loading"
detect_system
catalog_load catalog/linux.json x64 0

if it "loads every component"; then
    [[ ${#CAT_ID[@]} -gt 20 ]] && pass || fail "only ${#CAT_ID[@]} components loaded"
fi

if it "fields do not shift when 'requires' is empty"; then
    # Regression: tab is an IFS whitespace char, so consecutive tabs collapsed
    # and every empty field shifted the remaining columns left.
    i="$(catalog_index_of git)"
    assert_eq "${CAT_PROFILES[i]}" "workstation,ai-coding,light,server"
fi

if it "group is populated, not swallowed into another column"; then
    i="$(catalog_index_of git)"
    assert_eq "${CAT_GROUP[i]}" "Core CLI"
fi

if it "postInstall stays in its own column"; then
    i="$(catalog_index_of monitoring)"
    assert_eq "${CAT_POST[i]}" "install_fastfetch"
fi

if it "arch filter hides x64-only entries on arm64"; then
    catalog_load catalog/linux.json arm64 0
    if catalog_index_of antigravity >/dev/null 2>&1; then
        fail "antigravity is x64-only but appeared on arm64"
    else pass; fi
    catalog_load catalog/linux.json x64 0
fi

if it "headless hides the desktop category"; then
    catalog_load catalog/linux.json x64 1
    if catalog_index_of firefox >/dev/null 2>&1; then
        fail "desktop component offered on a headless machine"
    else pass; fi
    catalog_load catalog/linux.json x64 0
fi

# ─── Profiles ───────────────────────────────────────────────────────────────
describe "profiles"

if it "light profile is the Pi set"; then
    got="$(catalog_profile_defaults light)"
    assert_contains "$got" "claude-code"
fi

if it "light profile excludes desktop tooling"; then
    got="$(catalog_profile_defaults light)"
    assert_not_contains "$got" "antigravity"
fi

if it "custom profile pre-selects nothing"; then
    assert_eq "$(catalog_profile_defaults custom)" ""
fi

if it "workstation is a superset of light"; then
    ws=" $(catalog_profile_defaults workstation) "
    missing=""
    for id in $(catalog_profile_defaults light); do
        # openssh-server is deliberately light/server only
        [[ "$id" == "openssh-server" ]] && continue
        [[ "$ws" == *" $id "* ]] || missing+="$id "
    done
    assert_eq "$missing" ""
fi

# ─── Dependency resolution ──────────────────────────────────────────────────
describe "dependency resolution"

if it "pulls in transitive requirements"; then
    catalog_resolve claude-code >/dev/null
    assert_contains "$PLAN_IDS" "nodejs"
fi

if it "orders dependencies before dependents"; then
    catalog_resolve claude-code >/dev/null
    order=""; for id in $PLAN_IDS; do order+="$id "; done
    node_pos=0; cc_pos=0; n=0
    for id in $order; do
        n=$((n+1))
        [[ "$id" == "nodejs" ]] && node_pos=$n
        [[ "$id" == "claude-code" ]] && cc_pos=$n
    done
    if (( node_pos > 0 && node_pos < cc_pos )); then pass
    else fail "nodejs at $node_pos, claude-code at $cc_pos in [$order]"; fi
fi

if it "flags auto-added dependencies"; then
    catalog_resolve claude-code >/dev/null
    assert_contains "$PLAN_AUTO" "nodejs"
fi

if it "does not flag what was explicitly requested"; then
    catalog_resolve claude-code nodejs >/dev/null
    assert_not_contains "$PLAN_AUTO" "nodejs"
fi

if it "resolves a multi-level chain"; then
    catalog_resolve powerlevel10k >/dev/null
    ok=1
    for want in zsh git oh-my-zsh powerlevel10k; do
        [[ " $PLAN_IDS " == *" $want "* ]] || ok=0
    done
    if (( ok )); then pass; else fail "chain incomplete: $PLAN_IDS"; fi
fi

# ─── Detection ──────────────────────────────────────────────────────────────
describe "detection"

if it "identifies the architecture"; then
    case "$SYS_ARCH" in x64|arm64|armhf) pass ;; *) fail "odd arch: $SYS_ARCH" ;; esac
fi

if it "suggests a valid profile"; then
    case "$(suggested_profile)" in
        workstation|ai-coding|light|server) pass ;;
        *) fail "invalid profile: $(suggested_profile)" ;;
    esac
fi

if it "a Raspberry Pi is offered the light profile"; then
    ( SYS_IS_PI=1; SYS_IS_CONTAINER=0; SYS_RAM_GB=8.0; SYS_CPU_CORES=4; SYS_IS_HEADLESS=1
      [[ "$(suggested_profile)" == "light" ]] ) && pass || fail "Pi did not map to light"
fi

if it "a big headless box is offered the server profile"; then
    ( SYS_IS_PI=0; SYS_IS_CONTAINER=0; SYS_RAM_GB=32.0; SYS_CPU_CORES=16; SYS_IS_HEADLESS=1
      [[ "$(suggested_profile)" == "server" ]] ) && pass || fail "expected server"
fi

if it "sudo is resolved into AUTOOS_SUDO exactly once"; then
    if (( SYS_IS_ROOT )); then assert_eq "$AUTOOS_SUDO" ""
    elif (( SYS_CAN_SUDO )); then assert_eq "$AUTOOS_SUDO" "sudo"
    else assert_eq "$AUTOOS_SUDO" ""; fi
fi

# "Is it already installed?" - PATH alone misses everything a user-scope
# installer, snap or flatpak drops outside it. Probed against a scratch HOME so
# the answers do not depend on what happens to be installed here.

if it "the bin directories PATH routinely omits are all probed"; then
    dirs="$( SYS_HOME=/home/nobody; _extra_bin_dirs )"
    missing=""
    for want in /home/nobody/.local/bin /snap/bin /usr/local/bin \
                /var/lib/flatpak/exports/bin /home/nobody/.local/share/flatpak/exports/bin; do
        [[ "$dirs" == *"$want"* ]] || missing="$missing $want"
    done
    if [[ -z "$missing" ]]; then pass; else fail "not probed:$missing"; fi
fi

if it "a command in ~/.local/bin is found when PATH does not list it"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/.local/bin"
    printf '#!/bin/sh\nexit 0\n' >"$tmp/.local/bin/autoos-probe"
    chmod +x "$tmp/.local/bin/autoos-probe"
    if ( SYS_HOME="$tmp"; PATH="/usr/bin:/bin"; has_bin autoos-probe ); then pass
    else fail "the home .local/bin directory was not searched"; fi
    rm -rf "$tmp"
fi

if it "a command that exists nowhere is never invented"; then
    tmp="$(mktemp -d)"
    if ( SYS_HOME="$tmp"; has_bin autoos-definitely-not-installed ); then
        fail "reported a command that does not exist"
    else pass; fi
    rm -rf "$tmp"
fi

if it "a user-scope gh install is detected without it being on PATH"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/.local/bin"
    printf '#!/bin/sh\nexit 0\n' >"$tmp/.local/bin/gh"
    chmod +x "$tmp/.local/bin/gh"
    if ( SYS_HOME="$tmp"; PATH="/usr/bin:/bin"; script_is_installed gh ); then pass
    else fail "gh in ~/.local/bin was not detected"; fi
    rm -rf "$tmp"
fi

if it "a script component that is genuinely absent stays absent"; then
    tmp="$(mktemp -d)"
    # shellcheck disable=SC2123  # narrowing PATH is the point of this probe
    if ( SYS_HOME="$tmp"; PATH="$tmp/empty:$tmp/none"; script_is_installed xpipe ); then
        fail "xpipe reported installed with nothing on disk"
    else pass; fi
    rm -rf "$tmp"
fi

if it "the workstation profile carries git and the GitHub CLI on both platforms"; then
    if python3 - <<'PY'
import json, pathlib, sys
bad = []
for name in ("linux.json", "windows.json"):
    doc = json.loads((pathlib.Path("catalog") / name).read_text(encoding="utf-8"))
    have = {c["id"]: c.get("profiles", []) for cat in doc["categories"] for c in cat["components"]}
    for want in ("git", "gh"):
        if "workstation" not in have.get(want, []):
            bad.append(f"{name}:{want}")
if bad:
    print(f"not in the workstation profile: {bad}", file=sys.stderr)
    sys.exit(1)
PY
    then pass; else fail "git/gh missing from a workstation profile"; fi
fi

if it "windows asks for the same git identity linux already asks for"; then
    if python3 - <<'PY'
import json, pathlib, sys
root = pathlib.Path("catalog")
win = json.loads((root / "windows.json").read_text(encoding="utf-8"))
lin = json.loads((root / "linux.json").read_text(encoding="utf-8"))
missing = [k for k in ("git_user_name", "git_user_email") if k not in win.get("prompts", {})]
if missing:
    print(f"windows.json never asks {missing}", file=sys.stderr)
    sys.exit(1)
for key in ("git_user_name", "git_user_email"):
    if win["prompts"][key].get("question") != lin["prompts"][key].get("question"):
        print(f"{key} asks a different question on each platform", file=sys.stderr)
        sys.exit(1)
wired = [c for cat in win["categories"] for c in cat["components"]
         if "git_user_name" in (c.get("prompt") or "")]
if not wired:
    print("no windows component consumes git_user_name", file=sys.stderr)
    sys.exit(1)
if not all(c.get("postInstall") for c in wired):
    print("the git identity prompt has no post-install step to apply it", file=sys.stderr)
    sys.exit(1)
PY
    then pass; else fail "windows git identity is asked but never applied"; fi
fi

# ─── Idempotency ────────────────────────────────────────────────────────────
describe "idempotency"

if it "append_line_once writes once, not twice"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "MARKER" "export FOO=1  # MARKER" >/dev/null
    append_line_once "$tmp" "MARKER" "export FOO=1  # MARKER" >/dev/null
    n="$(grep -c 'MARKER' "$tmp" || true)"
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
    assert_eq "$n" "1"
fi

if it "append_line_once backs the original up before touching it"; then
    tmp="$(mktemp)"
    printf 'original content\n' >"$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "M2" "line  # M2" >/dev/null
    backup="$(ls "$tmp".autoos-backup-* 2>/dev/null | head -1)"
    if [[ -f "$backup" ]] && grep -q 'original content' "$backup"; then pass
    else fail "no usable backup written"; fi
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
fi

if it "dry run never writes"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=1
    append_line_once "$tmp" "M3" "line  # M3" >/dev/null
    AUTOOS_DRY_RUN=0
    if [[ -f "$tmp" ]]; then rm -f "$tmp"; fail "dry run created the file"; else pass; fi
fi

# ─── End-to-end plan stability ──────────────────────────────────────────────
describe "end-to-end (dry run only)"

if it "a dry run exits cleanly"; then
    out="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "exit $rc: $(printf '%s' "$out" | tail -5)"; fi
fi

if it "a dry run executes no commands at all"; then
    # Asserts the property directly rather than sampling mtimes, which slid with
    # the clock and made this flaky: every action must be announced as "would
    # run:", and none may appear as an executed "run:".
    out="$(bash setup.sh --profile workstation --dry-run --yes --no-color 2>&1)"
    executed="$(printf '%s' "$out" | grep -c '^run:' || true)"
    planned="$(printf '%s' "$out" | grep -c 'would ' || true)"
    if [[ "$executed" -eq 0 && "$planned" -gt 0 ]]; then pass
    else fail "executed=$executed planned=$planned (expected 0 executed, >0 planned)"; fi
fi

if it "a dry run creates none of the files its installers would"; then
    marker="$SYS_HOME/.autoos-omnigraph.env"
    had_marker=0; [[ -e "$marker" ]] && had_marker=1
    bash setup.sh --only agent-skills --dry-run --yes --no-color >/dev/null 2>&1
    now_marker=0; [[ -e "$marker" ]] && now_marker=1
    assert_eq "$now_marker" "$had_marker"
fi

if it "two consecutive dry runs produce the same plan"; then
    a="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    b="$(bash setup.sh --profile light --dry-run --yes --no-color 2>&1 | grep -E '^\s+[0-9]+\.')"
    assert_eq "$a" "$b"
fi

if it "an unknown component id is rejected"; then
    out="$(bash setup.sh --only definitely-not-a-thing --dry-run --yes --no-color 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"Unknown component"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | tail -3)"; fi
fi

if it "--check-catalog succeeds"; then
    bash setup.sh --check-catalog >/dev/null 2>&1
    assert_ok $?
fi

if it "catalog_probe_installed identifies installed components"; then
    catalog_load catalog/linux.json x64 0
    catalog_probe_installed
    assert_ok $?
    git_idx="$(catalog_index_of git)"
    assert_eq "${CAT_INSTALLED[git_idx]}" "1"
fi

if it "--list shows installed components with a checkmark"; then
    out="$(bash setup.sh --list 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"✓"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | head -10)"; fi
fi

# ─── Run state, verification, undo ──────────────────────────────────────────
describe "state, verify and undo"

if it "macos catalog validates"; then
    out="$(catalog_validate catalog/macos.json 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "the cask column does not shift the other fields"; then
    # Each new TSV column is a chance to reintroduce the delimiter bug.
    catalog_load catalog/macos.json arm64 0
    i="$(catalog_index_of docker)"
    assert_eq "${CAT_CASK[i]}" "1"
fi

if it "a non-cask formula is flagged 0"; then
    catalog_load catalog/macos.json arm64 0
    i="$(catalog_index_of git)"
    assert_eq "${CAT_CASK[i]}" "0"
fi

if it "verify commands survive catalog loading"; then
    catalog_load catalog/linux.json x64 0
    i="$(catalog_index_of git)"
    assert_eq "${CAT_VERIFY[i]}" "git --version"
fi

if it "verification passes for something that is installed"; then
    AUTOOS_DRY_RUN=0 AUTOOS_VERIFY=1
    verify_component "bash --version" "bash" >/dev/null 2>&1
    assert_eq "$VERIFY_STATE" "verified"
fi

if it "verification reports unverified for a missing binary"; then
    AUTOOS_DRY_RUN=0 AUTOOS_VERIFY=1
    verify_component "definitely-not-a-real-binary --version" "ghost" >/dev/null 2>&1
    assert_eq "$VERIFY_STATE" "unverified"
fi

if it "--no-verify skips the check entirely"; then
    AUTOOS_VERIFY=0
    verify_component "definitely-not-a-real-binary" "ghost" >/dev/null 2>&1
    AUTOOS_VERIFY=1
    assert_eq "$VERIFY_STATE" "unchecked"
fi

if it "state survives a save/load round trip"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    AUTOOS_ANSWERS=(["omnigraph_url"]="https://example.invalid")
    autoos_state_save "$tmp" "light" "git tmux" "git" "tmux" "" >/dev/null 2>&1
    AUTOOS_ANSWERS=()
    STATE_PROFILE=""; STATE_SELECTED=""
    autoos_state_load "$tmp" >/dev/null 2>&1
    rm -f "$tmp"
    assert_eq "$STATE_PROFILE|$STATE_SELECTED|${AUTOOS_ANSWERS[omnigraph_url]:-}"               "light|git tmux|https://example.invalid"
fi

if it "a dry run saves no state"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=1
    autoos_state_save "$tmp" "light" "git" "" "" "" >/dev/null 2>&1
    AUTOOS_DRY_RUN=0
    if [[ -f "$tmp" ]]; then rm -f "$tmp"; fail "dry run wrote a state file"; else pass; fi
fi

if it "undo restores a backed-up file"; then
    scratch="$(mktemp -d)"
    target="$scratch/.zshrc"
    printf 'ORIGINAL
' >"$target"
    AUTOOS_DRY_RUN=0
    append_line_once "$target" "AutoOS:test" "export X=1  # AutoOS:test" >/dev/null 2>&1
    grep -q 'AutoOS:test' "$target" || fail "setup for this test did not modify the file"
    ( SYS_HOME="$scratch"; autoos_undo 1 >/dev/null 2>&1 )
    body="$(cat "$target")"
    rm -rf "$scratch"
    assert_eq "$body" "ORIGINAL"
fi

if it "undo never uninstalls anything"; then
    # The safety property, asserted on the source rather than by removing software.
    if grep -qE '(apt-get remove|brew uninstall|npm uninstall)' lib/linux/install.sh; then
        fail "undo path contains an uninstall command"
    else pass; fi
fi

if it "configuration survives a save/load round trip"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    declare -A AUTOOS_ANSWERS=()
    AUTOOS_ANSWERS[git_user_name]="Alice Test"
    AUTOOS_ANSWERS[git_user_email]="alice@example.com"
    AUTOOS_ANSWERS[ollama_models]="nomic-embed-text"
    AUTOOS_DRY_RUN=0
    autoos_config_save "$tmp" "ai-coding" >/dev/null 2>&1
    AUTOOS_ANSWERS=()
    CONFIG_PROFILE=""
    autoos_config_load "$tmp"
    rm -f "$tmp"
    assert_eq "$CONFIG_PROFILE|${AUTOOS_ANSWERS[git_user_name]:-}|${AUTOOS_ANSWERS[git_user_email]:-}|${AUTOOS_ANSWERS[ollama_models]:-}" \
              "ai-coding|Alice Test|alice@example.com|nomic-embed-text"
fi

if it "setup.sh reads --config file and applies profile and answers"; then
    tmp="$(mktemp)"
    printf '{\n  "version": 1,\n  "profile": "light",\n  "answers": {\n    "git_user_name": "Test User"\n  }\n}\n' >"$tmp"
    out="$(bash setup.sh --config "$tmp" --dry-run --yes --no-color 2>&1)"
    rm -f "$tmp"
    if [[ "$out" == *"Using profile: light"* ]]; then pass
    else fail "did not use profile from config: $(printf '%s' "$out" | grep -i 'profile' || true)"; fi
fi

if it "serve.py and web UI provide configuration API and card"; then
    grep -q "/api/config" lib/linux/serve.py || fail "serve.py missing /api/config"
    grep -q "cardConfig" web/index.html || fail "web/index.html missing cardConfig"
    grep -q "saveConfiguration" web/index.html || fail "web/index.html missing saveConfiguration"
    pass
fi

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

# ─── Browser UI payload ─────────────────────────────────────────────────────
describe "browser UI"

if it "classify handles edge cases correctly"; then
    failures="$(python3 - <<'PY'
import sys
sys.path.insert(0, './lib/linux')
from serve import classify

tests = [
    # Happy paths
    ("+ ok line", "ok"),
    ("! warn line", "warn"),
    ("x err line", "err"),
    ("> step line", "step"),
    ("run: cmd", "muted"),
    ("would run: cmd", "muted"),
    ("would do thing", "muted"),

    # Edge cases
    ("   + padded", "ok"),
    ("\t! tabbed", "warn"),
    ("", ""),
    ("    ", ""),
    ("unknown format", ""),
    ("x", ""),
]

bad = []
for line, expected in tests:
    res = classify(line)
    if res != expected:
        bad.append(f"classify({repr(line)}) == {repr(res)} != {repr(expected)}")
if bad:
    print("\n".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "every component has a homepage link"; then
    missing="$(python3 - <<'PY'
import json, glob
bad = []
for p in sorted(glob.glob("catalog/*.json")):
    for grp in json.load(open(p, encoding="utf-8")).get("categories", []):
        for c in grp.get("components", []):
            if not c.get("homepage"):
                bad.append(p + ":" + c["id"])
print(" ".join(bad))
PY
)"
    assert_eq "$missing" ""
fi

if it "a busy port moves the server on instead of failing"; then
    # The socket is opened in a walk, not a single bind, so an already-taken
    # 8777 costs a line of output rather than the whole run.
    if grep -q "for candidate in range(PORT, PORT + 20)" lib/linux/serve.py &&
       grep -q "already in use - serving on" lib/linux/serve.py; then pass
    else fail "serve.py does not walk past a busy port"; fi
fi

if it "the server answers a heartbeat the page can poll"; then
    if grep -q '"/api/ping"' lib/linux/serve.py &&
       grep -q "/api/ping" web/index.html; then pass
    else fail "no heartbeat endpoint, or nothing polling it"; fi
fi

if it "a page whose server has gone tears itself down"; then
    if grep -q "function serverGone" web/index.html &&
       grep -q "window.close" web/index.html; then pass
    else fail "the page would sit there looking live after its server went"; fi
fi

if it "the output can be copied without selecting it by hand"; then
    ok=1
    # innerText returns nothing while the Output card is collapsed, so the text
    # has to be read off the child elements instead.
    for marker in 'id="copyLog"' "function copyLog" "function logText" "execCommand"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "the log cannot be copied in one click"; fi
fi

if it "a verify command resolves to the file that will run"; then
    . lib/linux/detect.sh
    if launch_hint "Git" "git" "git --version" && [[ "$LAUNCH_HOW" == "run  git" && -x "$LAUNCH_PATH" ]]; then
        pass
    else fail "git resolved to how=$LAUNCH_HOW path=$LAUNCH_PATH"; fi
fi

if it "a component that is not here reports nothing rather than guessing"; then
    # A blank is honest. A plausible-looking path that does not exist is worse
    # than saying nothing, because it reads like a fact.
    . lib/linux/detect.sh
    if launch_hint "AutoOS Nonesuch XYZ" "autoos-nonesuch-xyz" "autoos-nonesuch-xyz"; then
        fail "invented how=$LAUNCH_HOW path=$LAUNCH_PATH"
    elif [[ -z "$LAUNCH_HOW" && -z "$LAUNCH_PATH" ]]; then pass
    else fail "left stale values behind: $LAUNCH_HOW / $LAUNCH_PATH"; fi
fi

if it "the report says where things landed"; then
    if grep -q "Where to find them" setup.sh && grep -q "launch_hint" setup.sh; then pass
    else fail "the report never says where anything went"; fi
fi

if it "a page whose scripts are blocked says so"; then
    # Everything on the page is driven by one inline script. Without it the
    # header would sit at "connecting..." for ever and explain nothing.
    if grep -q "<noscript>" web/index.html &&
       grep -q "JavaScript is blocked" web/index.html; then pass
    else fail "no usable noscript fallback"; fi
fi

if it "a non-URL homepage is rejected"; then
    tmp="$(mktemp)"
    cat >"$tmp" <<'JSON'
{"categories":[{"id":"x","name":"X","components":[
  {"id":"thing","name":"Thing","description":"d","provider":"apt","package":"p",
   "homepage":"not-a-url"}]}]}
JSON
    out="$(catalog_validate "$tmp" 2>&1)"; rc=$?
    rm -f "$tmp"
    if [[ $rc -ne 0 && "$out" == *"homepage"* ]]; then pass
    else fail "expected a homepage complaint, got rc=$rc: $out"; fi
fi

if it "the dependency graph the UI draws has no orphan requirements"; then
    # The browser resolves dependencies client-side, so every `requires` must
    # name a component that is actually shipped to it.
    bad="$(python3 - <<'PY'
import json, glob
bad = []
for p in sorted(glob.glob("catalog/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    ids = {c["id"] for g in d.get("categories", []) for c in g.get("components", [])}
    for g in d.get("categories", []):
        for c in g.get("components", []):
            for r in c.get("requires", []):
                if r not in ids:
                    bad.append(p + ":" + c["id"] + "->" + r)
print(" ".join(bad))
PY
)"
    assert_eq "$bad" ""
fi

if it "the web page ships the dependency visualisation"; then
    ok=1
    for marker in "Install order" "chip-req" "chip-auto" "chip-locked" "renderTiers" "lockedBy"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "web/index.html is missing dependency-UI markers"; fi
fi

if it "external links open safely"; then
    # target=_blank without rel=noopener hands the opener to the target page.
    if grep -q 'target="_blank" rel="noopener noreferrer"' web/index.html; then pass
    else fail "external links must carry rel=noopener noreferrer"; fi
fi

if it "the theme can be switched, and still starts from the OS preference"; then
    # The three-button Auto/Light/Dark group is one toggle now. What matters is
    # unchanged: the user can override, and an un-overridden page follows the OS.
    ok=1
    for marker in 'id="themeToggle"' 'id="themeIcon"' 'function applyTheme' \
                  'function currentThemeIsDark' 'prefers-color-scheme: dark'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    # "auto" must survive as the stored default, or a fresh page picks a theme
    # for the user instead of asking their OS.
    grep -q 'PREF.get("theme", "auto")' web/index.html ||
        { ok=0; echo "missing: auto as the stored default" >&2; }
    grep -q 'removeAttribute("data-theme")' web/index.html ||
        { ok=0; echo "missing: auto clears the stamp so the OS decides" >&2; }
    if (( ok )); then pass; else fail "the theme toggle is incomplete"; fi
fi

if it "every colour token is defined on bare :root, not only behind a theme"; then
    # A token defined only inside a media query or [data-theme] block is undefined
    # in the un-stamped "auto" state, which is what renders one theme on another.
    missing="$(python3 - <<'PY'
import re, io
css = io.open("web/index.html", encoding="utf-8").read()
base = css.split(":root{", 1)[1].split("}", 1)[0]
declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", base))
used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
print(" ".join(sorted(used - declared)))
PY
)"
    assert_eq "$missing" ""
fi

if it "the components view can switch between list and grid"; then
    ok=1
    for marker in 'data-layout-choice="list"' 'data-layout-choice="grid"' 'data-layout="grid"' 'id="cols"'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "layout switcher markers missing"; fi
fi

if it "the big cards are collapsible"; then
    n="$(grep -c 'details class="card"' web/index.html || true)"
    if [[ "$n" -ge 4 ]]; then pass; else fail "expected >=4 collapsible cards, found $n"; fi
fi

if it "the selected profile shows what it actually installs"; then
    # The summary used to be inline in each profile button; it is one full-width
    # detail panel below a row of chips now. Same job: the selected profile has
    # to say what you are about to install without leaving the card.
    ok=1
    for marker in "profile-chip" "profileDetail" "renderProfileSummary" "psum-grid" "psum-card"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "profile summary markers missing"; fi
fi

if it "the profile summary numbers each component with its install step"; then
    ok=1
    for marker in "step-badge" "psum-steps" "stepMap"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "install-order numbering missing from the summary"; fi
fi

if it "the summary and the install order share one numbering function"; then
    # Two independent numbering schemes would drift; there must be exactly one.
    n="$(grep -c "function stepMap" web/index.html || true)"
    assert_eq "$n" "1"
fi

if it "the component list shows the same step number"; then
    grep -q "chip-step" web/index.html && pass || fail "no step chip in the component list"
fi

if it "system, profile, components and install order share one tab"; then
    # They were three separate tabs; collapsing them into Overview is the point.
    if grep -q 'id="tab-deps"' web/index.html || grep -q 'id="tab-components"' web/index.html; then
        fail "a separate components or install-order tab is still present"
    else
        missing=""
        for card in cardSystem cardProfile cardCatalog cardOrder; do
            grep -q "id=\"$card\"" web/index.html || missing+="$card "
        done
        assert_eq "$missing" ""
    fi
fi

if it "navigation stays bounded and every section has a panel"; then
    # This began as "only two tabs remain" against a tab strip. The strip is a
    # dropdown now, and the point was never the number two: it was that sections
    # do not sprawl and that each one actually leads somewhere.
    n="$(grep -c 'role="menuitemradio"' web/index.html || true)"
    panels="$(grep -c '<section role="region" id="panel-' web/index.html || true)"
    if [ "$n" -le 5 ] && [ "$n" -eq "$panels" ]; then
        pass
    else
        fail "$n menu items and $panels panels (expected equal, and at most 5)"
    fi
fi

if it "the core stack is available on all three platforms"; then
    # These carry the same id in every catalog on purpose: a product that exists
    # everywhere but is filed under two different ids reports itself as
    # single-platform, which is exactly what docker-desktop/nerd-fonts did.
    missing="$(python3 - <<'PY'
import json, glob, collections
have = collections.defaultdict(set)
for p in glob.glob("catalog/*.json"):
    plat = p.replace("catalog", "").strip("/\\").replace(".json", "")
    for g in json.load(open(p, encoding="utf-8"))["categories"]:
        for c in g["components"]:
            have[c["id"]].add(plat)
core = ["claude-code", "git", "nodejs", "docker", "tailscale",
        "handy", "vscode", "herdr", "agent-skills", "nerd-font"]
bad = [c for c in core if have[c] != {"windows", "linux", "macos"}]
print(" ".join(bad))
PY
)"
    assert_eq "$missing" ""
fi

if it "the page labels components that are not on every platform"; then
    ok=1
    for marker in "platformChip" "chip-plat" "PLATFORM_NAME"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "platform label markers missing"; fi
fi

if it "the payload carries the platform list"; then
    grep -q "component_platforms" lib/linux/serve.py && pass || fail "serve.py does not compute platforms"
fi

if it "Handy is offered on every platform"; then
    n="$(grep -l '"id": "handy"' catalog/*.json | wc -l)"
    assert_eq "$n" "3"
fi

if it "the UI shows installed applications and allows filtering"; then
    # The header pill is gone; the inventory has its own section with its own
    # search, and the catalog keeps its installed filter. Both still have to work.
    ok=1
    for marker in "installedList" "installedSearch" "renderInstalledTab" \
                  "filterInstalled" "installedCount" "filterInstalledOnly" \
                  'data-installed' "✓ installed"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "web/index.html is missing installed-app markers"; fi
fi

if it "the payload carries installed component flags"; then
    ok=1
    for marker in '"installed":' '"installed applications"' "installed_ids"; do
        grep -q "$marker" lib/linux/serve.py || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "serve.py does not report installed components"; fi
fi

# ─── WSL detection ──────────────────────────────────────────────────────────
describe "MCP wiring"

if it "graphify is registered once, at user scope"; then
    # The command is cwd-relative, so one definition serves every repository its
    # own graph. A per-repo entry would pin one repo's graph for all of them.
    if grep -q "register_mcp_server graphify user" lib/linux/install.sh &&
       grep -q "graphify-out/graph.json" lib/linux/install.sh; then pass
    else fail "graphify is not a single cwd-relative user-scope entry"; fi
fi

if it "omnigraph is never registered at user scope"; then
    # A user-scope omnigraph silently wins over the per-repo one and answers
    # from the wrong graph, which looks identical to it working.
    if grep -q "register_mcp_server omnigraph" lib/linux/install.sh; then
        fail "omnigraph is being registered as a user server"
    elif grep -q "claude mcp remove omnigraph" lib/linux/install.sh; then pass
    else fail "the shadowing case is never called out"; fi
fi

if it "a project MCP server is approved, not just declared"; then
    # A tracked .mcp.json cannot approve itself; Claude Code skips an unapproved
    # project server silently.
    if grep -q "enabledMcpjsonServers" lib/linux/install.sh; then pass
    else fail "nothing writes the approval list"; fi
fi

if it "approving a server keeps the rest of the settings file"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.claude"
    printf '%s' '{"permissions":{"allow":["Bash(ls:*)"]},"enabledMcpjsonServers":["already"]}' \
        >"$tmp/.claude/settings.local.json"
    AUTOOS_DRY_RUN=0 enable_project_mcp_server "$tmp" omnigraph >/dev/null 2>&1
    got="$(python3 -c "
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print('perm' if d.get('permissions') else 'LOST', ','.join(d.get('enabledMcpjsonServers',[])))
" "$tmp/.claude/settings.local.json")"
    rm -rf "$tmp"
    if [[ "$got" == "perm already,omnigraph" ]]; then pass
    else fail "expected the permissions block kept and omnigraph appended, got: $got"; fi
fi

if it "approving twice adds nothing the second time"; then
    tmp="$(mktemp -d)"
    AUTOOS_DRY_RUN=0 enable_project_mcp_server "$tmp" omnigraph >/dev/null 2>&1
    AUTOOS_DRY_RUN=0 enable_project_mcp_server "$tmp" omnigraph >/dev/null 2>&1
    n="$(python3 -c "
import json,sys
print(len(json.load(open(sys.argv[1],encoding='utf-8')).get('enabledMcpjsonServers',[])))
" "$tmp/.claude/settings.local.json")"
    rm -rf "$tmp"
    if [[ "$n" == "1" ]]; then pass; else fail "expected 1 entry, got $n"; fi
fi

if it "no bearer token is ever invented"; then
    # This repository is public. A real-looking secret in it is a leak whether or
    # not it happens to work, and a guessed one fails as an unexplainable 401.
    if grep -qE "OMNIGRAPH_TOKEN=[\"']?[A-Za-z0-9]" lib/linux/install.sh; then
        fail "a token literal is present"
    elif grep -q "OMNIGRAPH_TOKEN is not set" lib/linux/install.sh; then pass
    else fail "a missing token is never reported"; fi
fi

if it "Antigravity MCP config is merged, not replaced"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.gemini/config"
    printf '%s' '{"mcpServers":{"existing":{"command":"node","args":["index.js"]}}}' \
        >"$tmp/.gemini/config/mcp_config.json"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        register_antigravity_mcp_server "playwright" '{"command":"npx","args":["-y","@playwright/mcp"]}' >/dev/null 2>&1
    )
    got="$(python3 -c "
import json,sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
servers = d.get('mcpServers', {})
print(','.join(sorted(servers.keys())))
" "$tmp/.gemini/config/mcp_config.json")"
    rm -rf "$tmp"
    if [[ "$got" == "existing,playwright" ]]; then pass
    else fail "expected existing and playwright, got: $got"; fi
fi

if it "register_antigravity_mcp_server is idempotent and creates backup"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.gemini/config"
    printf '%s' '{"mcpServers":{"existing":{"command":"node"}}}' >"$tmp/.gemini/config/mcp_config.json"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        register_antigravity_mcp_server "serena" '{"command":"uvx"}' >/dev/null 2>&1
        register_antigravity_mcp_server "serena" '{"command":"uvx"}' >/dev/null 2>&1
    )
    backups=( "$tmp"/.gemini/config/mcp_config.json.autoos-backup-* )
    has_backup=0
    [[ -f "${backups[0]}" ]] && has_backup=1
    rm -rf "$tmp"
    if (( has_backup )); then pass
    else fail "backup was not created before edit"; fi
fi

if it "agent-skills links skills to Antigravity and Claude Code"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/Documents/code/agent-skills/skills/test-skill"
    printf -- '---\nname: test-skill\ndescription: test\n---\n' >"$tmp/Documents/code/agent-skills/skills/test-skill/SKILL.md"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        clone_or_update() { :; }
        install_mcp_graphify() { :; }
        install_mcp_serena() { :; }
        install_mcp_playwright() { :; }
        install_mcp_context7() { :; }
        mcp_has_server() { return 1; }
        enable_project_mcp_server() { :; }
        register_antigravity_mcp_server() { :; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        install_agent_skills >/dev/null 2>&1
    )
    ok=1
    [[ -e "$tmp/.gemini/config/skills/test-skill/SKILL.md" ]] || ok=0
    [[ -e "$tmp/.claude/skills/test-skill/SKILL.md" ]] || ok=0
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "skills were not linked to Antigravity or Claude Code"; fi
fi

if it "custom_is_installed detects agent-skills under Documents/code or Documents/Code"; then
    tmp="$(mktemp -d)"
    (
        SYS_HOME="$tmp"
        mkdir -p "$tmp/Documents/code/agent-skills"
        custom_is_installed agent-skills
    )
    rc_code=$?
    (
        SYS_HOME="$tmp"
        rm -rf "$tmp/Documents/code"
        mkdir -p "$tmp/Documents/Code/agent-skills"
        custom_is_installed agent-skills
    )
    rc_Code=$?
    rm -rf "$tmp"
    if [[ $rc_code -eq 0 && $rc_Code -eq 0 ]]; then pass
    else fail "rc_code=$rc_code rc_Code=$rc_Code"; fi
fi

describe "claude autostart"

# A fixture transcript tree shaped exactly like ~/.claude/projects: one directory
# per working directory, one *.jsonl per session, with cwd and sessionId carried
# in the records themselves (ADR 0001). mtimes are set explicitly because the
# liveness window is the thing under test and git cannot preserve them.
cs_fixture_tree() {
    local root="$1"; shift
    local cwd uuid age slug dir
    while [ $# -gt 0 ]; do
        cwd="$1"; uuid="$2"; age="$3"; shift 3
        slug="$(printf '%s' "$cwd" | sed 's/[^A-Za-z0-9]/-/g')"
        dir="$root/projects/$slug"
        mkdir -p "$dir"
        printf '{"cwd": "%s", "uuid": "%s"}' "$cwd" "$uuid" |
            python3 "$ROOT/tests/helpers/make_transcript.py" "$dir/$uuid.jsonl" "$age"
    done
}

cs_engine() { python3 "$ROOT/lib/linux/claude_sessions.py" "$@"; }

if it "snapshot records every live session with the cwd read from its transcript"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/alpha 11111111-1111-1111-1111-111111111111 2 \
                           /home/u/beta  22222222-2222-2222-2222-222222222222 3
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=2 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" summary 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$got" = "2|/home/u/alpha|11111111-1111-1111-1111-111111111111|/home/u/beta" ]; then
        pass
    else
        fail "got [$got] from: $out"
    fi
fi

if it "a transcript older than the liveness window is not restored"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/fresh 11111111-1111-1111-1111-111111111111 5 \
                           /home/u/stale 22222222-2222-2222-2222-222222222222 900
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=1 \
           AUTOOS_CLAUDE_LIVENESS_WINDOW_MINS=240 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" cwds 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$got" = "/home/u/fresh" ]; then pass; else fail "expected only the fresh session, got [$got]"; fi
fi

if it "max_sessions keeps the most recently active, not an arbitrary slice"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/a 11111111-1111-1111-1111-111111111111 30 \
                           /home/u/b 22222222-2222-2222-2222-222222222222 2 \
                           /home/u/c 33333333-3333-3333-3333-333333333333 10
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=3 AUTOOS_CLAUDE_MAX_SESSIONS=2 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" cwds 2>/dev/null || true)"
    rm -rf "$tmp"
    if [ "$got" = "/home/u/b|/home/u/c" ]; then pass; else fail "expected the two newest, got [$got]"; fi
fi

if it "one session is recorded once even when it left transcripts in two places"; then
    # Observed on a real machine: the same session id under two project slugs (a
    # scratchpad directory alongside the repo). Restoring it twice opens two
    # terminals fighting over one conversation.
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/proj          11111111-1111-1111-1111-111111111111 4 \
                           /home/u/proj/scratch  11111111-1111-1111-1111-111111111111 2
    out="$(AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=1 \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot 2>&1)"
    got="$(printf '%s' "$out" | python3 "$ROOT/tests/helpers/read_state.py" cwds 2>/dev/null || true)"
    rm -rf "$tmp"
    # The newer of the two wins, so the surviving record is the live one.
    if [ "$got" = "/home/u/proj/scratch" ]; then pass; else fail "expected one record, got [$got]"; fi
fi

if it "a snapshot with nothing live never clobbers a good one"; then
    tmp="$(mktemp -d)"
    cs_fixture_tree "$tmp" /home/u/alpha 11111111-1111-1111-1111-111111111111 2
    AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=1 \
        AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot >/dev/null 2>&1
    # The timer fires again mid-boot, before anything is up. Overwriting here is
    # what erases the record the next restore depends on.
    AUTOOS_CLAUDE_HOME="$tmp" AUTOOS_CLAUDE_LIVE_COUNT=0 \
        AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine snapshot >/dev/null 2>&1
    got="$(python3 "$ROOT/tests/helpers/read_state.py" count "$tmp/state.json" 2>/dev/null || echo ERR)"
    rm -rf "$tmp"
    if [ "$got" = "1" ]; then pass; else fail "snapshot was clobbered: [$got] session(s) left"; fi
fi

if it "plan emits one START per restorable session and SKIPs the rest with a reason"; then
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/home/u/alpha","remote_control":true},
 {"session_uuid":"","name":"broken","cwd":"/home/u/broken","remote_control":false}]}
JSONEOF
    plan="$(AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" cs_engine plan 2>&1)"
    rm -rf "$tmp"
    starts="$(printf '%s\n' "$plan" | grep -c '^START' || true)"
    skips="$(printf '%s\n' "$plan" | grep -c '^SKIP' || true)"
    if [ "$starts" = "1" ] && [ "$skips" = "1" ] &&
       printf '%s\n' "$plan" | grep -q "START.11111111-1111-1111-1111-111111111111.alpha./home/u/alpha.1"; then
        pass
    else
        fail "unexpected plan ($starts START, $skips SKIP): $plan"
    fi
fi

if it "remote_control=never strips --rc from a session that was recorded with it"; then
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/home/u/alpha","remote_control":true}]}
JSONEOF
    plan="$(AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" AUTOOS_CLAUDE_REMOTE_CONTROL=never cs_engine plan 2>&1)"
    rm -rf "$tmp"
    if printf '%s\n' "$plan" | grep -q "alpha./home/u/alpha.0$"; then
        pass
    else
        fail "rc not stripped: $plan"
    fi
fi

if it "restore does nothing while autostart is disabled in the configuration"; then
    # `enabled` was in the shipped schema and the web card from the start, and no
    # code ever read it. It is a real pause switch now, or it should not be there.
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/tmp","remote_control":false}]}
JSONEOF
    mkdir -p "$tmp/bin"
    printf '#!/bin/sh\ntouch "%s/launched"\n' "$tmp" > "$tmp/bin/claude"
    printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/tmux"
    chmod +x "$tmp/bin/claude" "$tmp/bin/tmux"
    out="$(PATH="$tmp/bin:$PATH" AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" \
           AUTOOS_CLAUDE_ENABLED=0 AUTOOS_NO_COLOR=1 \
           bash "$ROOT/lib/linux/claude-sessions.sh" restore 2>&1)"
    launched=0; [ -f "$tmp/launched" ] && launched=1
    rm -rf "$tmp"
    if [ "$launched" -eq 0 ] && printf '%s\n' "$out" | grep -qi 'disabled'; then
        pass
    else
        fail "launched=$launched out: $out"
    fi
fi

if it "restore refuses to start a TUI when no terminal host is available"; then
    tmp="$(mktemp -d)"
    cat > "$tmp/state.json" <<'JSONEOF'
{"version":2,"captured_at":100,"sessions":[
 {"session_uuid":"11111111-1111-1111-1111-111111111111","name":"alpha","cwd":"/tmp","remote_control":false}]}
JSONEOF
    # A PATH carrying a fake `claude` but neither herdr nor tmux: the session is
    # restorable, there is simply nowhere a human could ever see it (ADR 0002).
    mkdir -p "$tmp/bin"
    printf '#!/bin/sh\ntouch "%s/launched"\n' "$tmp" > "$tmp/bin/claude"
    chmod +x "$tmp/bin/claude"
    # Host discovery runs for real (`auto`); both hosts are made unusable rather
    # than merely absent, so the result is the same on a developer box that has
    # tmux installed as on a bare one.
    printf '#!/bin/sh\nexit 1\n' > "$tmp/bin/tmux"; chmod +x "$tmp/bin/tmux"
    out="$(PATH="$tmp/bin:$PATH" HERDR_BIN="$tmp/bin/herdr-absent" \
           AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" \
           AUTOOS_CLAUDE_TERMINAL_HOST=auto AUTOOS_NO_COLOR=1 \
           bash "$ROOT/lib/linux/claude-sessions.sh" restore 2>&1)"
    launched=0; [ -f "$tmp/launched" ] && launched=1
    rm -rf "$tmp"
    if [ "$launched" -eq 0 ] && printf '%s\n' "$out" | grep -qi 'no terminal host'; then
        pass
    else
        fail "launched=$launched out: $out"
    fi
fi

if it "an inactive snapshot timer is reported once, not twice"; then
    # `systemctl is-active` PRINTS its answer and exits non-zero when the unit is
    # not running, so `|| echo inactive` appended a second line and the status
    # screen rendered "inactive" on two lines. Found by running it under WSL.
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/bin"
    printf '#!/bin/sh
echo inactive
exit 3
' > "$tmp/bin/systemctl"
    chmod +x "$tmp/bin/systemctl"
    out="$(PATH="$tmp/bin:$PATH" AUTOOS_CLAUDE_STATE_FILE="$tmp/state.json" AUTOOS_NO_COLOR=1            bash "$ROOT/lib/linux/claude-sessions.sh" status 2>&1)"
    rm -rf "$tmp"
    n="$(printf '%s
' "$out" | grep -c 'inactive' || true)"
    if [ "$n" = "1" ]; then
        pass
    else
        fail "expected one 'inactive' line, got $n: $out"
    fi
fi

if it "a snapshot that was never taken has no age"; then
    # "never (never)" - the age of something that never happened is not a second
    # fact about it.
    tmp="$(mktemp -d)"
    out="$(AUTOOS_CLAUDE_STATE_FILE="$tmp/missing.json" cs_engine summary 2>&1)"
    rm -rf "$tmp"
    if printf '%s
' "$out" | grep -q "^last_snapshot	never$"; then
        pass
    else
        fail "expected a bare 'never', got: $out"
    fi
fi

if it "the live-process probe answers on the real system without throwing"; then
    # Principle 9: the seam every test above uses must not be the only path that
    # is ever exercised. This runs the probe production actually takes.
    n="$(cs_engine probe-live 2>&1 || echo ERR)"
    if [[ "$n" =~ ^[0-9]+$ ]]; then pass; else fail "probe-live returned [$n]"; fi
fi

if it "claude-autostart is defined for linux and windows, and not for macos"; then
    ok=1
    for f in catalog/linux.json catalog/windows.json; do
        python3 "$ROOT/tests/helpers/catalog_has.py" "$f" claude-autostart || { ok=0; echo "missing in $f" >&2; }
    done
    # macOS routes through lib/linux/install.sh, which writes systemd units. On a
    # machine with no systemd that reports `installed` and does nothing.
    if python3 "$ROOT/tests/helpers/catalog_has.py" catalog/macos.json claude-autostart; then
        ok=0; echo "macos still lists claude-autostart" >&2
    fi
    if (( ok )); then pass; else fail "catalog membership wrong"; fi
fi

if it "every claude_autostart key the web UI writes is a key the scripts read"; then
    # The first implementation wrote resume_prompt_mode/interval_minutes while the
    # scripts read resume_mode/snapshot_interval_mins, so the card saved nothing.
    if python3 "$ROOT/tests/helpers/check_config_keys.py"; then
        pass
    else
        fail "web UI and config schema disagree (see above)"
    fi
fi

if it "every key the configuration form groups is a real catalog prompt"; then
    # The form used to hardcode six fields. Three of them (git_user_name,
    # ollama_models, antigravity_url) are Linux-only prompts, so on Windows it
    # rendered boxes whose answers no installer would ever read.
    if python3 "$ROOT/tests/helpers/check_config_sections.py"; then
        pass
    else
        fail "the configuration form groups a key no catalog asks (see above)"
    fi
fi

if it "each panel owns one job: overview chooses, system reports, configure sets"; then
    # Overview had grown to six cards covering three unrelated jobs. A card in the
    # wrong panel is how it grew the first time, so the split is pinned here.
    if python3 "$ROOT/tests/helpers/check_panels.py"; then
        pass
    else
        fail "a card is in the wrong panel (see above)"
    fi
fi

if it "the component list is compact until Details is asked for"; then
    ok=1
    grep -q 'id="detailToggle"' web/index.html || { ok=0; echo "missing: the Details toggle" >&2; }
    grep -q 'body:not(\[data-detail="on"\]) .item .item-meta{display:none}' web/index.html ||
        grep -q 'body:not(\[data-detail="on"\]) .item .item-meta,' web/index.html ||
        { ok=0; echo "missing: the compact-mode rule for .item-meta" >&2; }
    grep -q 'class="item-icon"' web/index.html || { ok=0; echo "missing: the component icon" >&2; }
    if (( ok )); then pass; else fail "the catalog is not compact by default"; fi
fi

if it "a quick install queues rather than racing another run"; then
    ok=1
    for marker in 'data-quick=' 'function queueQuickInstall' 'let quickQueue' 'id="headerProgress"'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    # The server rejects a second concurrent run with 409, so the client must not
    # start one; it has to wait for poll() to report the first has finished.
    grep -q 'if (running || !quickQueue.length) return;' web/index.html ||
        { ok=0; echo "missing: the drain guard against a concurrent run" >&2; }
    if (( ok )); then pass; else fail "quick install is not queued safely"; fi
fi

if it "the install order leaves out what is already installed"; then
    if grep -q 'if (BY_ID.get(id)?.installed) continue;' web/index.html; then
        pass
    else
        fail "the install order still lists components the run will skip"
    fi
fi

if it "the section menu is a real menu, not a button that looks like one"; then
    # A dropdown has to be openable, closable and walkable from the keyboard, or
    # it is navigation only a mouse can reach.
    ok=1
    for marker in 'id="tabMenuBtn"' 'aria-haspopup="menu"' 'role="menuitemradio"'                   'function openTabMenu' 'function closeTabMenu'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    grep -q 'e.key === "Escape"' web/index.html || { ok=0; echo "missing: Escape closes the menu" >&2; }
    grep -q 'e.key === "ArrowDown"' web/index.html || { ok=0; echo "missing: arrow-key navigation" >&2; }
    # With no tablist left, role="tabpanel" would be a lie.
    grep -q 'role="tabpanel"' web/index.html && { ok=0; echo "a tabpanel survives with no tablist" >&2; }
    if (( ok )); then pass; else fail "the section menu is not keyboard-operable"; fi
fi

if it "the page carries its own favicon"; then
    # The local server has no asset route, so every load was logging a 403 for
    # /favicon.ico; an inline data URI costs no request at all.
    if grep -q 'rel="icon" href="data:image/svg' web/index.html; then
        pass
    else
        fail "no inline favicon"
    fi
fi

if it "the reduced-motion guard for the indeterminate progress bar survives"; then
    if grep -q 'prefers-reduced-motion:reduce){.bar.indeterminate>div{animation:none}' web/index.html; then
        pass
    else
        fail "the card-chooser stylesheet dropped the reduced-motion rule again"
    fi
fi

if it "the claude card, chooser and configure affordance are present in the page"; then
    ok=1
    for marker in 'id="cardClaudeAutostart"' 'id="cardChooserBar"' 'id="claudeRefreshBtn"' 'data-configure-card'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "cardClaudeAutostart markers missing"; fi
fi

if it "no inline onclick handler is introduced in the web UI"; then
    # Inline handlers force HTML-escaping a value into a JS string context, which
    # is the wrong escaper; the page uses delegated listeners everywhere else.
    n="$(grep -c 'onclick="' web/index.html || true)"
    if [ "$n" = "0" ]; then pass; else fail "$n inline onclick handler(s) left"; fi
fi

describe "wsl detection"

if it "WSL is detected when running under it"; then
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
        assert_eq "$SYS_IS_WSL" "1"
    else
        skip "not running under WSL"
    fi
fi

if it "the WSL version is identified, not assumed"; then
    if (( SYS_IS_WSL )); then
        case "$SYS_WSL_VERSION" in 1|2) pass ;; *) fail "got version [$SYS_WSL_VERSION]" ;; esac
    else
        skip "not running under WSL"
    fi
fi

if it "the environment summary is always populated"; then
    [[ -n "${SYS_ENVIRONMENT:-}" ]] && pass || fail "SYS_ENVIRONMENT is empty"
fi

if it "a non-WSL machine reports no WSL version"; then
    ( SYS_IS_WSL=0; SYS_IS_CONTAINER=0; SYS_IS_PI=0
      [[ -z "${SYS_WSL_VERSION:-}" || "$SYS_IS_WSL" == "0" ]] ) && pass || fail "stale WSL version"
fi

if it "the payload the UI reads exposes the environment"; then
    ok=1
    for marker in "SYS_ENVIRONMENT" "isWsl" "environment"; do
        grep -q "$marker" lib/linux/serve.py || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "serve.py does not expose the environment"; fi
fi

# ─── Documentation ──────────────────────────────────────────────────────────
describe "documentation"

if it "check-links identifies broken links correctly and ignores valid ones"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/docs"
    touch "$tmp/README.md" "$tmp/docs/setup.md"
    cat > "$tmp/docs/index.md" <<'EOF'
[Valid local](../README.md)
[Valid peer](setup.md)
[Valid peer with fragment](setup.md#section)
[External](https://example.com/broken)
[Fragment only](#local-section)
[Mailto](mailto:test@example.com)
[Broken local](../missing.md)
[Broken peer](missing.md)
EOF

    out="$(python3 - "$tmp" <<'PY'
import sys
import os
import importlib.util

tmp_dir = sys.argv[1]
sys.argv = ["check-links.py"]
sys.path.insert(0, ".")
spec = importlib.util.spec_from_file_location("check_links", "tests/check-links.py")
check_links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_links)

problems = check_links.broken_links(tmp_dir, ["docs/index.md"])
for p in problems:
    print(p)
PY
)"

    rm -rf "$tmp"

    expected="docs/index.md: [Broken local] -> ../missing.md
docs/index.md: [Broken peer] -> missing.md"

    if [ "$out" = "$expected" ]; then
        pass
    else
        fail "expected specific broken links, got: $out"
    fi
fi

if it "every relative link in the docs resolves"; then
    out="$(python3 tests/check-links.py . 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "every docs page is linked from the index"; then
    missing=""
    for f in docs/*.md; do
        base="$(basename "$f")"
        [[ "$base" == "README.md" ]] && continue
        grep -q "$base" docs/README.md || missing+="$base "
    done
    assert_eq "$missing" ""
fi

# ─── shellcheck (optional) ──────────────────────────────────────────────────
describe "static analysis"

if it "shellcheck is clean"; then
    # Fall back to the official image when shellcheck is not installed. This
    # check being skipped locally is precisely how a shellcheck failure reached
    # CI unnoticed, so "no binary" should not silently mean "no check".
    files=(setup.sh lib/linux/*.sh tests/run-tests.sh)
    if has_cmd shellcheck; then
        out="$(shellcheck -S warning "${files[@]}" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "$(printf '%s' "$out" | head -20)"; fi
    elif has_cmd docker && docker info >/dev/null 2>&1; then
        out="$(docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable                -S warning "${files[@]}" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "(via docker) $(printf '%s' "$out" | head -20)"; fi
    else
        skip "no shellcheck binary and no usable docker"
    fi
fi

# ─── Summary ────────────────────────────────────────────────────────────────
printf '\n%s%s%s\n' "$DIM" "$(printf '─%.0s' $(seq 1 56))" "$RESET"
printf '  %spassed %d%s   %sfailed %d%s   %sskipped %d%s\n' \
    "$GREEN" "$PASS" "$RESET" \
    "$( ((FAIL)) && printf '%s' "$RED" || printf '%s' "$DIM")" "$FAIL" "$RESET" \
    "$DIM" "$SKIP" "$RESET"
if (( FAIL )); then
    printf '\n  failures:\n'
    for f in "${FAILED_NAMES[@]}"; do printf '    - %s\n' "$f"; done
    exit 1
fi
exit 0
