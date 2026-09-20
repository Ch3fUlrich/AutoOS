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

if it "Pi keeps its whole set: light profile and router stack on arm64"; then
    # Raspberry Pi 5 is headless arm64. Nothing the light profile or the
    # :20128 routing stack needs may be hidden by the arch filter there.
    catalog_load catalog/linux.json arm64 1
    missing=""
    for id in $(catalog_profile_defaults light); do
        catalog_index_of "$id" >/dev/null 2>&1 || missing+="$id "
    done
    for id in nodejs omniroute opencode-cli neovim litellm zed; do
        catalog_index_of "$id" >/dev/null 2>&1 || missing+="$id "
    done
    catalog_load catalog/linux.json x64 0
    assert_eq "$missing" ""
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

if it "the page offers an explicit light/dark/auto theme"; then
    ok=1
    for marker in 'data-theme-choice="auto"' 'data-theme-choice="light"' 'data-theme-choice="dark"'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "theme switcher markers missing"; fi
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

if it "the profile card carries an inline component summary"; then
    ok=1
    for marker in "psummary" "renderProfileSummary" "psum-grid" "psum-card"; do
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

if it "only two tabs remain"; then
    n="$(grep -c 'class="tab" role="tab"' web/index.html || true)"
    assert_eq "$n" "2"
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
        "handy", "vscode", "herdr", "agent-skills", "nerd-font",
        "zed", "litellm", "opencode-cli", "omniroute", "openhands"]
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

# ─── AI routing (omniroute / litellm / zed / opencode) ────────────────────
describe "AI routing"

if it "zed rides the script provider end to end"; then
    ok=1
    grep -q 'zed)             has_cmd zed' lib/linux/install.sh || ok=0
    grep -q 'zed)             install_zed' lib/linux/install.sh || ok=0
    # has_cmd is stubbed so the test never depends on what this machine happens
    # to have installed (Zed is present on the dev box via WSL interop).
    ( has_cmd() { return 1; }; script_is_installed zed ) >/dev/null 2>&1 && ok=0
    ( has_cmd() { return 0; }; script_is_installed zed ) >/dev/null 2>&1 || ok=0
    if (( ok )); then pass; else fail "zed dispatch or detection is broken"; fi
fi

if it "openhands pulls the current image and announces in dry run"; then
    ok=1
    grep -q 'docker.openhands.dev/openhands/openhands:latest' lib/linux/install.sh || ok=0
    grep -q 'install_openhands' lib/linux/install.sh || ok=0
    out="$( ( AUTOOS_DRY_RUN=1; install_openhands ) 2>&1)"
    [[ "$out" == *"would pull"* ]] || ok=0
    if (( ok )); then pass; else fail "openhands installer is stale or silent in dry run"; fi
fi

if it "openhands wires the LLM through OmniRoute in start-stack"; then
    ok=1
    for f in configuration/start-stack.ps1 configuration/start-stack.sh; do
        grep -q 'LLM_MODEL=openai/tier1' "$f" || { ok=0; echo "missing model in $f" >&2; }
        grep -q 'LLM_BASE_URL' "$f" || ok=0
        grep -q 'docker.openhands.dev/openhands/openhands:latest' "$f" || ok=0
        grep -q '3000:3000' "$f" || ok=0
    done
    if (( ok )); then pass; else fail "start-stack does not route OpenHands correctly"; fi
fi

if it "the OpenHands template carries the LiteLLM provider prefix"; then    # Every model line, not a sample: a new unprefixed line is the exact
    # "LLM Provider NOT provided" mismatch this guards against.
    bad="$(grep -nE '^[[:space:]]*model[[:space:]]*=' configuration/openhands/config.toml |
        grep -v 'openai/' || true)"
    ok=1
    grep -q 'model = "openai/tier1"' configuration/openhands/config.toml || ok=0
    grep -q 'model = "openai/tier3"' configuration/openhands/config.toml || ok=0
    grep -q 'openai/tier1-clean' configuration/openhands/config.toml || ok=0
    if [[ -n "$bad" ]]; then fail "model lines without the openai/ prefix: $bad"
    elif (( ok )); then pass
    else fail "template is missing the expected tier models"; fi
fi

if it "provider status never carries key values"; then
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' 2>/dev/null; then
        skip "python3 < 3.7 cannot import serve.py"
    else
        report="$(python3 - <<'PY'
import importlib.util, json, os, pathlib, sys, tempfile
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-serve-"))
(root / "configuration").mkdir()
(root / "configuration" / "api-keys.yml").write_text(
    "groq: gsk_SUPERSECRETVALUE123\ndeepseek:\nmistral: 5xLsSecretValue\n",
    encoding="utf-8")
spec = importlib.util.spec_from_file_location("autoos_serve", "lib/linux/serve.py")
mod = importlib.util.module_from_spec(spec)
sys.argv = ["serve.py", str(root), "0", "127.0.0.1", "0"]
spec.loader.exec_module(mod)
mod.ROOT = root
status = mod.provider_status()
text = json.dumps(status)
problems = []
groq = [p for p in status if p["id"] == "groq"]
deepseek = [p for p in status if p["id"] == "deepseek"]
if not groq or not groq[0]["configured"]:
    problems.append("groq-not-configured")
if deepseek and deepseek[0]["configured"]:
    problems.append("empty-value-counted-as-configured")
if "SUPERSECRET" in text or "SecretValue" in text:
    problems.append("value-leaked")
print(" ".join(problems))
PY
)"
        assert_eq "$report" ""
    fi
fi

if it "server profile ticks the headless terminal stack"; then
    catalog_load catalog/linux.json x64 1
    defaults="$(catalog_profile_defaults server)"
    ok=1
    for c in opencode-cli omniroute litellm neovim; do
        [[ " $defaults" == *" $c "* ]] || { ok=0; echo "missing: $c" >&2; }
    done
    if (( ok )); then pass; else fail "server profile is missing headless components"; fi
fi

if it "zed requires the router on every platform"; then
    bad="$(python3 - <<'PY'
import json, glob
bad = []
for p in sorted(glob.glob("catalog/*.json")):
    for g in json.load(open(p, encoding="utf-8"))["categories"]:
        for c in g["components"]:
            if c["id"] == "zed" and "litellm" not in c.get("requires", []):
                bad.append(p)
print(" ".join(bad))
PY
)"
    assert_eq "$bad" ""
fi

if it "zed routing announces in dry run and writes nothing"; then
    scratch="$(mktemp -d)"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; route_zed_to_proxy ) 2>&1)"
    if [[ "$out" == *"would route"* && ! -e "$scratch/.config/zed/settings.json" ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
    rm -rf "$scratch"
fi

if it "zed routing merges one provider and keeps the rest"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    printf '{"theme":"mine"}' >"$scratch/.config/zed/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; route_zed_to_proxy >/dev/null 2>&1 )
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; route_zed_to_proxy >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
oc = cfg.get("language_models", {}).get("openai_compatible", {}).get("autoos-omniroute", {})
models = [m["name"] for m in oc.get("available_models", [])]
print("%s|%s|%s" % (cfg.get("theme"), oc.get("api_url"), ",".join(models)))
PY
)"
    backups="$(ls "$scratch"/.config/zed/settings.json.autoos-backup-* 2>/dev/null | wc -l)"
    leaks="$(grep -cE 'sk-[A-Za-z0-9]{10,}' "$scratch/.config/zed/settings.json" || true)"
    rm -rf "$scratch"
    assert_eq "$report|backups=$backups|leaks=$leaks" \
        "mine|http://127.0.0.1:20128/v1|auto/smart,auto,auto/cheap|backups=1|leaks=0"
fi

if it "litellm installer announces in dry run and detects presence"; then
    empty="$(mktemp -d)"
    out="$( ( PATH="$empty:/usr/bin:/bin" AUTOOS_DRY_RUN=1; install_litellm_proxy ) 2>&1)"
    stub="$(mktemp -d)"; printf '#!/bin/sh\nexit 0\n' >"$stub/litellm"; chmod +x "$stub/litellm"
    out2="$( ( PATH="$stub:$PATH" AUTOOS_DRY_RUN=0; install_litellm_proxy ) 2>&1)"
    rm -rf "$empty" "$stub"
    if [[ "$out" == *"would install"* && "$out2" == *"already installed"* ]]; then pass
    else fail "dry-run or presence path broken"; fi
fi

if it "sidekick extra is created once and never duplicated"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/nvim"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; enable_sidekick_extra >/dev/null 2>&1 )
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; enable_sidekick_extra >/dev/null 2>&1 )
    n="$(grep -c 'lazyvim.plugins.extras.ai.sidekick' "$scratch/.config/nvim/lazyvim.json" || true)"
    rm -rf "$scratch"
    assert_eq "$n" "1"
fi

if it "sidekick enabling is a no-op without an nvim config"; then
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; enable_sidekick_extra >/dev/null 2>&1 )
    if [[ -e "$scratch/.config/nvim/lazyvim.json" ]]; then rm -rf "$scratch"; fail "created config unasked"
    else rm -rf "$scratch"; pass; fi
fi

if it "litellm fallback config is internally consistent"; then
    report="$(python3 - <<'PY'
import re, io
text = io.open("configuration/litellm/config.yaml", encoding="utf-8").read()
groups = set(re.findall(r"(?m)^\s*-\s*model_name:\s*(\S+)\s*$", text))
need = {"tier1", "tier1-paid", "tier2", "tier2-paid", "tier3", "tier3-paid"}
fb = text.split("fallbacks:", 1)[1]
refs = set(re.findall(r"[- ](\S+):\s*\[([^\]]*)\]", fb))
problems = sorted(list(need - groups))
for src, tgts in refs:
    if src not in groups:
        problems.append("src:" + src)
    for t in [x.strip() for x in tgts.split(",")]:
        if t not in groups:
            problems.append("tgt:" + t)
models = re.findall(r"(?m)^\s*model:\s*(\S+)\s*$", text)
if [m for m in models if "cerebras" in m or "llama-3.3-70b" in m]:
    problems.append("stale")
if "drop_params" not in text or "os.environ/LITELLM_MASTER_KEY" not in text:
    problems.append("settings")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "opencode repo config pins omniroute with litellm fallback"; then
    report="$(python3 - <<'PY'
import json, re, io
text = io.open("opencode.jsonc", encoding="utf-8").read()
text = re.sub(r"(?m)^\s*//.*$", "", text)
oc = json.loads(text)
p = oc["providers"]
print("%s|%s|%s|%s|%s" % (
    oc["model"],
    p["omniroute"]["settings"]["baseURL"],
    ",".join(sorted(p["omniroute"]["models"].keys())),
    "litellm" in p,
    ",".join(sorted(oc["mcp"]["servers"].keys()))))
PY
)"
    assert_eq "$report" \
        "omniroute/tier1|http://127.0.0.1:20128/v1|auto,auto/cheap,auto/smart,tier1,tier1-clean,tier2,tier2-clean,tier3,tier3-clean|True|graphify,playwright,serena"
fi

if it "openhands template has tiers and no secrets"; then
    ok=1
    for s in '\[llm\]' '\[llm.tier1\]' '\[llm.tier2\]' '\[llm.tier3\]' '\[llm.draft_editor\]' '\[agent.CodeActAgent\]'; do
        grep -q "$s" configuration/openhands/config.toml || { ok=0; echo "missing: $s" >&2; }
    done
    grep -q 'host.docker.internal:20128' configuration/openhands/config.toml || ok=0
    grep -qE 'sk-[A-Za-z0-9]{10,}' configuration/openhands/config.toml && ok=0
    grep -q 'api_key = ""' configuration/openhands/config.toml || ok=0
    if (( ok )); then pass; else fail "openhands template is incomplete or leaks secrets"; fi
fi

if it "start-stack.sh is valid bash and names the client key"; then
    bash -n configuration/start-stack.sh || { fail "syntax error"; }
    ok=1
    grep -q 'AUTOOS_OMNIROUTE_KEY' configuration/start-stack.sh || ok=0
    grep -q 'host.docker.internal' configuration/start-stack.sh || ok=0
    grep -q 'opencode-serve' configuration/start-stack.sh || ok=0
    if (( ok )); then pass; else fail "start script is missing wiring"; fi
fi

if it "no committed secrets in router files"; then
    # Report file:line only - a failure message must never echo the value it
    # found into logs or a terminal shared with anyone else.
    hits="$(grep -rnE 'sk-[A-Za-z0-9]{10,}' configuration/litellm/config.yaml \
        configuration/litellm/.env.example opencode.jsonc \
        configuration/openhands/config.toml 2>/dev/null | cut -d: -f1,2 || true)"
    # .env.example is the sanctioned placeholder pattern; only real-looking keys fail.
    if [[ -z "$hits" ]]; then pass; else fail "credential-shaped value at: $hits"; fi
fi

# ─── WSL detection ──────────────────────────────────────────────────────────
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

if it "install_zed announces in dry run"; then
    out="$( ( AUTOOS_DRY_RUN=1; install_zed ) 2>&1)"
    if [[ "$out" == *"would install Zed"* ]]; then pass
    else fail "no dry-run announcement"; fi
fi

if it "zed routing reports a failed merge instead of success"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    # A directory where the file belongs makes the python merge fail.
    rm -rf "$scratch/.config/zed/settings.json"
    mkdir "$scratch/.config/zed/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; route_zed_to_proxy >/dev/null 2>&1 ); rc=$?
    rm -rf "$scratch"
    assert_eq "$rc" "1"
fi

if it "existing nvim config keeps working and gains sidekick"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/nvim"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; install_lazyvim >/dev/null 2>&1 )
    n="$(grep -c 'lazyvim.plugins.extras.ai.sidekick' "$scratch/.config/nvim/lazyvim.json" 2>/dev/null || true)"
    rm -rf "$scratch"
    assert_eq "$n" "1"
fi

if it "sidekick enabling announces in dry run and writes nothing"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/nvim"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; enable_sidekick_extra ) 2>&1)"
    if [[ "$out" == *"would enable"* && ! -e "$scratch/.config/nvim/lazyvim.json" ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
    rm -rf "$scratch"
fi

if it "litellm installer uses pipx without installing anything"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\ntouch "$0.called"\n' >"$stub/pipx"; chmod +x "$stub/pipx"
    ( PATH="$stub:$PATH" AUTOOS_DRY_RUN=0; install_litellm_proxy >/dev/null 2>&1 )
    if [[ -f "$stub/pipx.called" ]]; then rm -rf "$stub"; pass
    else rm -rf "$stub"; fail "pipx stub was not invoked"; fi
fi

if it "env template carries placeholders only"; then
    bad="$(grep -vE '^(#|$|[A-Z_]+=REPLACE_WITH_[A-Z_]+$)' configuration/litellm/.env.example || true)"
    assert_eq "$bad" ""
fi

if it "api-keys example carries placeholders only"; then
    bad="$(grep -vE '^(#|$)' configuration/api-keys.example.yml |
        grep -vE '^[A-Za-z_]+:[[:space:]]*REPLACE_WITH_[A-Z_]+$' || true)"
    assert_eq "$bad" ""
fi

if it "combos.json parses and tier1 promises 1M"; then
    report="$(python3 - <<'PY'
import json
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
names = [c["name"] for c in d["combos"]]
problems = []
if names != ["tier1", "tier1-clean", "tier2", "tier2-clean", "tier3", "tier3-clean"]:
    problems.append("names")
for c in d["combos"]:
    if not c["models"]:
        problems.append(c["name"] + ":empty")
    for m in c["models"]:
        if "/" not in m:
            problems.append(c["name"] + ":" + m)
    if c["name"] == "tier1" and c.get("context") != "1M":
        problems.append("tier1-context")
by = {c["name"]: c["models"] for c in d["combos"]}
# tier1 is spark-only: gemini must never occupy a 1M slot again.
if any("gemini" in m for m in by["tier1"]):
    problems.append("tier1-gemini")
# *-clean = paid legs only: no free pool may train on private prompts.
# Free = contributor-free, -contributor (trains by contract), groq /
# cerebras / sambanova / gemini hosts, mistral-code + qwen free pools.
# Direct-key legs (mistral-small, deepseek, openrouter paid, zen paid)
# bill past the pool on the same key, so they stay.
import re
free = re.compile(r"contributor-free|-contributor$|^(groq|cerebras|sambanova|gemini)/|mistral/mistral-code|/qwen")
for n in ("tier1-clean", "tier2-clean", "tier3-clean"):
    bad = [m for m in by[n] if free.search(m)]
    if bad:
        problems.append(n + "-free:" + ",".join(bad))
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "apply handles the Cloudflare UA and meta mapping"; then
    ok=1
    grep -q 'customUserAgent' configuration/omniroute/apply.sh || ok=0
    grep -q 'muse-code' configuration/omniroute/apply.sh || ok=0
    grep -q 'provider-specific-data' configuration/omniroute/apply.sh || ok=0
    if (( ok )); then pass; else fail "apply.sh is missing the provider quirks"; fi
fi

if it "apply --dry-run registers nothing and starts nothing"; then
    # combos.json must be byte-identical afterwards; the dry run must announce.
    before="$(cat configuration/omniroute/combos.json)"
    out="$(bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    assert_contains "$out" "dry run"
    after="$(cat configuration/omniroute/combos.json)"
    assert_eq "$after" "$before"
fi

if it "tier depth is mandatory in opencode.jsonc agents"; then
    report="$(python3 - <<'PY'
import json, re, io
text = re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read())
a = json.loads(text)["agents"]
def perms(n):
    return [(p["action"], p["resource"], p["effect"]) for p in a[n]["permissions"]]
t1, t2, t3 = perms("tier1-orchestrator"), perms("tier2-worker"), perms("tier3-reviewer")
problems = []
if t1[0] != ("subagent", "*", "deny") or t1[-1] != ("subagent", "tier2-worker", "allow"):
    problems.append("tier1")
if t2[0] != ("subagent", "*", "deny") or t2[-1] != ("subagent", "tier3-reviewer", "allow"):
    problems.append("tier2")
if t3 != [("subagent", "*", "deny")]:
    problems.append("tier3-leaf")
if a["tier3-reviewer"]["mode"] != "subagent":
    problems.append("tier3-mode")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "opencode tiers declare matching context limits"; then
    report="$(python3 - <<'PY'
import json, re, io
text = re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read())
oc = json.loads(text)
m = oc["providers"]["omniroute"]["models"]
problems = []
for name, ctx in (("tier1", 1000000), ("tier1-clean", 1000000),
                  ("tier2", 131072), ("tier3", 131072),
                  ("tier2-clean", 131072), ("tier3-clean", 131072)):
    if name not in m or m[name]["modelID"] != name or m[name]["limit"]["context"] != ctx:
        problems.append(name)
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "the serve payload exposes provider status without values"; then
    ok=1
    for marker in "provider_status" "api-keys.yml"; do
        grep -q "$marker" lib/linux/serve.py || { ok=0; echo "serve.py missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "serve.py does not expose provider status"; fi
fi

if it "the providers card is in the web UI"; then
    ok=1
    for marker in "cardProviders" "renderProviders" "providersSub" "chip-missing"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "providers card markers missing"; fi
fi

if it "new components name real profiles and verify commands"; then
    bad="$(python3 - <<'PY'
import json, glob
problems = []
for p in sorted(glob.glob("catalog/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    profiles = set(d.get("profiles", {}).keys())
    for g in d.get("categories", []):
        for c in g.get("components", []):
            if c["id"] not in ("zed", "litellm", "opencode-cli", "omniroute", "openhands"):
                continue
            for prof in c.get("profiles", []):
                if prof not in profiles:
                    problems.append(p + ":" + c["id"] + ":" + prof)
            if not c.get("verify"):
                problems.append(p + ":" + c["id"] + ":no-verify")
print(" ".join(problems))
PY
)"
    assert_eq "$bad" ""
fi

if it "ai-coding dry run plans the routing stack"; then
    out="$(bash setup.sh --profile ai-coding --dry-run --yes --no-color 2>&1)"
    ok=1
    for name in "OmniRoute gateway" "LiteLLM tier router" "OpenCode CLI" "Zed"; do
        [[ "$out" == *"$name"* ]] || { ok=0; echo "missing: $name" >&2; }
    done
    if (( ok )); then pass; else fail "routing stack missing from the plan"; fi
fi

if it "autostart and healthcheck files exist and parse"; then
    ok=1
    for f in configuration/autostart/Start-AutoOSStack.sh configuration/healthcheck.sh; do
        [[ -f "$f" ]] || { ok=0; echo "missing: $f" >&2; }
        bash -n "$f" || ok=0
    done
    for f in configuration/autostart/Start-AutoOSStack.ps1 configuration/healthcheck.ps1 \
             configuration/autostart/autoos-stack.service; do
        [[ -f "$f" ]] || { ok=0; echo "missing: $f" >&2; }
    done
    if (( ok )); then pass; else fail "autostart/healthcheck files missing or invalid"; fi
fi

if it "the systemd unit is installable and opt-in"; then
    ok=1
    for key in '\[Unit\]' '\[Service\]' '\[Install\]' 'ExecStart=' 'WantedBy=default.target' 'Type=oneshot'; do
        grep -q "$key" configuration/autostart/autoos-stack.service || { ok=0; echo "missing: $key" >&2; }
    done
    grep -q 'systemctl --user enable' configuration/README.md || { ok=0; echo "not documented opt-in" >&2; }
    if (( ok )); then pass; else fail "systemd unit incomplete"; fi
fi

if it "healthcheck is log-only without --fix"; then
    ok=1
    grep -q -- '--fix' configuration/healthcheck.sh || ok=0
    # The resume call must only appear inside the --fix branch.
    body_without_fix="$(sed '/if \[\[ $FIX/,/^fi$/d' configuration/healthcheck.sh)"
    [[ "$body_without_fix" == *"Start-AutoOSStack"* ]] && ok=0
    [[ "$body_without_fix" == *"docker start"* || "$body_without_fix" == *"nohup omniroute"* ]] && ok=0
    grep -q '"401"' configuration/healthcheck.sh || ok=0
    for p in 20128 3000 4096 8777; do
        grep -q "$p" configuration/healthcheck.sh || { ok=0; echo "port $p missing" >&2; }
    done
    if (( ok )); then pass; else fail "healthcheck can start things without --fix"; fi
fi

if it "phone URLs are documented without secrets"; then
    ok=1
    for frag in "<tail-ip>:3000" "<tail-ip>:4096" "healthcheck"; do
        grep -q "$frag" docs/troubleshooting.md || { ok=0; echo "missing: $frag" >&2; }
    done
    grep -qE 'sk-[A-Za-z0-9]{10,}' docs/troubleshooting.md && ok=0
    # No machine-local IPs may be committed (repo is public).
    if grep -qE '100\.70\.|192\.168\.178\.59' docs/troubleshooting.md; then ok=0; fi
    if (( ok )); then pass; else fail "phone docs missing or leaking"; fi
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
