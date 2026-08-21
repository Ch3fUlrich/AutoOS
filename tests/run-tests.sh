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
cd "$ROOT"
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
    out="$(bash setup.sh --only definitely-not-a-thing --yes --no-color 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"Unknown component"* ]]; then pass
    else fail "rc=$rc out=$(printf '%s' "$out" | tail -3)"; fi
fi

if it "--check-catalog succeeds"; then
    bash setup.sh --check-catalog >/dev/null 2>&1
    assert_ok $?
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
    AUTOOS_ANSWERS=([omnigraph_url]="https://example.invalid")
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

# ─── Browser UI payload ─────────────────────────────────────────────────────
describe "browser UI"

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

# ─── Documentation ──────────────────────────────────────────────────────────
describe "documentation"

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
    if has_cmd shellcheck; then
        out="$(shellcheck -S warning setup.sh lib/linux/*.sh tests/run-tests.sh 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "$(printf '%s' "$out" | head -20)"; fi
    else
        skip "shellcheck not installed"
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
