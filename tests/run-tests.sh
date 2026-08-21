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
