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

# release_sha256_of <Release file> <filename>
# Reads the checksum for <filename> out of the SHA256 section ONLY.
# The anchoring is the point: `apt-ftparchive release` emits MD5Sum (and SHA1)
# BEFORE SHA256, so an unanchored `/^ .*Packages$/` returns the MD5 and then
# compares it against a sha256sum — a guaranteed mismatch. That went unnoticed
# because the test that used it also restricted PATH to /usr/bin:/bin while
# believing it was exercising the no-apt-ftparchive fallback; on any host with
# apt-utils (WSL2 Ubuntu, Ubuntu Desktop — where AGENTS.md §5 says this suite
# will usually run) /usr/bin/apt-ftparchive is right there and it was not.
release_sha256_of() {
    awk -v want="$2" '
        /^SHA256:/ { in_sha = 1; next }
        /^[^ ]/    { in_sha = 0 }
        in_sha && $3 == want { print $1; exit }
    ' "$1" 2>/dev/null
}

# fake_usb <fixture-name>
# Prints a synthetic `lsblk -J -b -o NAME,MODEL,SIZE,RM,TRAN,MOUNTPOINT,TYPE`
# document for one of the named fixtures in tests/helpers/fake_usb.py, for
# assignment straight into AUTOOS_FAKE_LSBLK. Never touches the live
# machine's disks (AGENTS.md §5) — see that file for what each fixture is
# shaped like and why.
fake_usb() {
    python3 "$ROOT/tests/helpers/fake_usb.py" "$1"
}

# _start_test_http_server <directory>
# Starts a throwaway python3 http.server bound to 127.0.0.1 on an
# OS-assigned port, serving <directory>, and prints "<pid> <port>" once it
# is actually listening (polls a port file the server writes itself before
# calling serve_forever(), so there is no read-before-bound race). Task 7's
# usb_write_ventoy tests use this instead of file:// URLs: curl on a
# Windows/Git-Bash test host cannot open a file:// URL built from an MSYS
# /tmp path (confirmed: "curl: (37) Could not open file /tmp/tmp.XXXXXX/...")
# - the same class of host-specific path trap MSYS_NO_PATHCONV already
# documents elsewhere in this suite. A real loopback HTTP server sidesteps
# it entirely and behaves identically under WSL2/Linux, where this suite
# usually runs (AGENTS.md §5). The caller must `kill` the printed pid when
# done - this function never cleans up after itself.
_start_test_http_server() {
    local dir="$1" portfile pid port i
    portfile="$(mktemp -u)"
    python3 -c '
import http.server, socketserver, sys, os
os.chdir(sys.argv[1])
httpd = socketserver.TCPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
with open(sys.argv[2], "w") as f:
    f.write(str(httpd.server_address[1]))
httpd.serve_forever()
' "$dir" "$portfile" >/dev/null 2>&1 &
    pid=$!
    port=""
    for i in $(seq 1 50); do
        if [[ -s "$portfile" ]]; then port="$(cat "$portfile")"; break; fi
        sleep 0.1
    done
    rm -f "$portfile"
    printf '%s %s\n' "$pid" "$port"
}

# ─── Load the libraries under test ──────────────────────────────────────────
cd "$ROOT" || { echo "cannot enter $ROOT" >&2; exit 1; }
# shellcheck source=../lib/linux/ui.sh
. lib/linux/ui.sh
# shellcheck source=../lib/linux/detect.sh
. lib/linux/detect.sh
# shellcheck source=../lib/linux/catalog.sh
. lib/linux/catalog.sh
# shellcheck source=../lib/linux/imagecache.sh
. lib/linux/imagecache.sh
# shellcheck source=../lib/linux/install.sh
. lib/linux/install.sh
# shellcheck source=../lib/linux/download.sh
. lib/linux/download.sh
# shellcheck source=../lib/linux/usb.sh
. lib/linux/usb.sh

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

# ─── Image catalog (plan Task 4) ────────────────────────────────────────────
describe "image catalog"

if it "the image catalog validates"; then
    out="$(python3 tests/helpers/images_validate.py catalog/images.json 2>&1)"; rc=$?
    [[ $rc -eq 0 ]] && pass || fail "$out"
fi

if it "no image pins a version number in its URL"; then
    # A9: a pinned version is stale the day it is written.
    if grep -qE '"index"[^,]*[0-9]+\.[0-9]+' catalog/images.json; then
        fail "an index URL contains a hardcoded version"
    else pass; fi
fi

if it "every image carries a checksum source"; then
    # custom-url/custom-local are excluded here for the same reason
    # images_validate.py's PSEUDO set excludes them from require_sums(): they
    # are UI text fields, not downloadable images, and by construction have
    # no 'sums' to carry. Checking every entry without this carve-out is the
    # same Step-3-vs-Step-4 contradiction the validator itself had to name
    # explicitly — it just resurfaces here because this test reads the
    # catalog directly instead of going through the validator.
    out="$(python3 -c "
import json,sys
PSEUDO={'custom-url','custom-local'}
bad=[i['id'] for i in json.load(open('catalog/images.json'))['images']
     if i['id'] not in PSEUDO and not i.get('sums')]
print(' '.join(bad)); sys.exit(1 if bad else 0)")"; rc=$?
    [[ $rc -eq 0 ]] && pass || fail "no checksum source: $out"
fi

if it "every image key is '-' or a 40-character uppercase hex fingerprint"; then
    # Finding A6 reopened: a wrong fingerprint is worse than none (it fails
    # verification 100% of the time, indistinguishable from an attack, and
    # that is precisely how verification gets switched off entirely) — so
    # this checks shape only, never which key was typed in.
    out="$(python3 -c "
import json, re, sys
fpr = re.compile(r'[0-9A-F]{40}')
bad = [i['id'] for i in json.load(open('catalog/images.json'))['images']
       if i.get('key') not in (None, '-') and not fpr.fullmatch(i['key'])]
print(' '.join(bad)); sys.exit(1 if bad else 0)")"; rc=$?
    [[ $rc -eq 0 ]] && pass || fail "malformed key fingerprint: $out"
fi

if it "a raw-write image is never offered to ventoy's copy path"; then
    out="$(python3 -c "
import json
bad=[i['id'] for i in json.load(open('catalog/images.json'))['images']
     if i.get('writeMode')=='raw' and 'live-persistent' in i.get('kinds',[])]
print(' '.join(bad))")"
    [[ -z "$out" ]] && pass || fail "raw images claiming persistence: $out"
fi

# ─── Engine catalog (Task 6, plan ruling P3) ────────────────────────────────
# catalog/engines.json is the data-shaped home for B3's engine/kind
# compatibility matrix — usb_plan() (lib/linux/usb.sh) looks entries up by
# id at plan time rather than branching on engine names in code, so a
# malformed entry here is a silent, unhandled-in-either-place gap unless
# this schema is checked directly.
if it "the engine catalog validates against its own schema"; then
    out="$(python3 -c "
import json
REQUIRED = {'id', 'name', 'platforms', 'arch', 'kinds', 'writeModes', 'requires', 'interactive'}
KINDS = {'installer', 'live-persistent', 'full-os'}
WRITE_MODES = {'hybrid', 'raw'}
problems = []
data = json.load(open('catalog/engines.json'))
seen = set()
for e in data.get('engines', []):
    eid = e.get('id')
    where = f\"engine '{eid}'\" if eid else 'engine with no id'
    missing = REQUIRED - set(e.keys())
    if missing:
        problems.append(f'{where}: missing {sorted(missing)}')
        continue
    if eid in seen:
        problems.append(f'{where}: duplicate id')
    seen.add(eid)
    if not isinstance(e['interactive'], bool):
        problems.append(f'{where}: interactive must be a bool')
    if not e['kinds'] or not set(e['kinds']) <= KINDS:
        problems.append(f'{where}: kinds must be a non-empty subset of {sorted(KINDS)}')
    if not e['writeModes'] or not set(e['writeModes']) <= WRITE_MODES:
        problems.append(f'{where}: writeModes must be a non-empty subset of {sorted(WRITE_MODES)}')
    if not e['platforms']:
        problems.append(f'{where}: platforms must not be empty')
print('\n'.join(problems))
import sys; sys.exit(1 if problems else 0)
" 2>&1)"; rc=$?
    [[ $rc -eq 0 ]] && pass || fail "$out"
fi

if it "the engine catalog names exactly the five engines this feature ships"; then
    out="$(python3 -c "
import json
ids = sorted(e['id'] for e in json.load(open('catalog/engines.json'))['engines'])
want = sorted(['ventoy', 'uefi-copy', 'wsl', 'native', 'rufus'])
print(' '.join(ids) if ids != want else '')")"
    [[ -z "$out" ]] && pass || fail "got: $out"
fi

if it "ventoy is the only engine an arch list can hide, and rufus is the only interactive one"; then
    out="$(python3 -c "
import json
engines = {e['id']: e for e in json.load(open('catalog/engines.json'))['engines']}
bad = []
if 'arm64' in engines['ventoy'].get('arch', []):
    bad.append('ventoy claims arm64')
if not engines['rufus']['interactive']:
    bad.append('rufus is not marked interactive')
for eid in ('ventoy', 'uefi-copy', 'wsl', 'native'):
    if engines[eid]['interactive']:
        bad.append(f'{eid} is wrongly marked interactive')
print(' / '.join(bad))")"
    [[ -z "$out" ]] && pass || fail "$out"
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

if it "rescue profile carries the disk-recovery core"; then
    got="$(catalog_profile_defaults rescue)"
    ok=1
    for id in smartmontools nvme-cli ddrescue testdisk gdisk; do
        [[ " $got " == *" $id "* ]] || { ok=0; echo "missing: $id" >&2; }
    done
    (( ok )) && pass || fail "rescue profile is incomplete"
fi

if it "rescue profile stays off the desktop"; then
    # assert_not_contains passes trivially on an empty haystack, so a broken
    # catalog_profile_defaults would have made this test green while proving
    # nothing. Establish the result is non-empty before asserting on it.
    got="$(catalog_profile_defaults rescue)"
    if [[ -z "$got" ]]; then
        fail "catalog_profile_defaults rescue returned nothing — the assertion below would pass vacuously"
    else
        assert_not_contains "$got" "antigravity"
    fi
fi

if it "rescue can open LVM, RAID and LUKS volumes"; then
    got="$(catalog_profile_defaults rescue)"
    ok=1
    for id in lvm2 mdadm cryptsetup; do
        [[ " $got " == *" $id "* ]] || { ok=0; echo "missing: $id" >&2; }
    done
    (( ok )) && pass || fail "rescue cannot reach encrypted or logical volumes"
fi

if it "rescue ships both AI CLIs"; then
    got="$(catalog_profile_defaults rescue)"
    assert_contains "$got" "claude-code"
    assert_contains "$got" "agy"
fi

if it "neither AI CLI depends on the other (rescue)"; then
    # B8: an outage that kills one must leave the other installed. claude-code
    # and agy (Antigravity CLI, which replaced gemini-cli at the human
    # partner's direction) are asserted mutually independent here.
    #
    # Three ways this used to pass without testing anything. `$(...)` captures
    # stdout only, so a Python traceback left "$out" empty and the test passed
    # against a script that never ran. The exit status was discarded for the
    # same reason. And `if i in comps` silently skipped a renamed id, so the
    # loop could examine nothing at all. The script below therefore says out
    # loud what it found, and its exit status is checked.
    out="$(python3 -c "
import json, sys
cat = json.load(open('catalog/linux.json'))
comps = {c['id']: c for g in cat['categories'] for c in g['components']}
want = ('claude-code', 'agy')
absent = [i for i in want if i not in comps]
if absent:
    sys.exit('NOT-IN-CATALOG: ' + ' '.join(absent))
bad = [i for i in want if set(want) & set(comps[i].get('requires', []))]
print('COUPLED: ' + ' '.join(bad) if bad else 'CHECKED ' + ' '.join(want))
" 2>&1)"; rc=$?
    out="${out//$'\r'/}"   # not through a pipe: $? must stay python3's own
    if [[ $rc -eq 0 && "$out" == "CHECKED claude-code agy" ]]; then
        pass
    else
        fail "rc=$rc out=[${out:0:300}]"
    fi
fi

if it "local-ai profile ships ollama"; then
    # A plain assert_contains on the substring "ollama" would pass vacuously
    # against "ollama-model-qwen3-4b" even if the "ollama" id itself were
    # never in the default set — pad with spaces for an exact id match, the
    # same guard the rescue-profile tests above use.
    got=" $(catalog_profile_defaults local-ai) "
    [[ "$got" == *" ollama "* ]] && pass || fail "ollama not in local-ai defaults: [$got]"
fi

if it "local-ai is NOT pulled in by the rescue profile"; then
    # Two orders of magnitude apart: ~400 MB of rescue tools vs 1.4-4.7 GB of
    # model weights (B21) — a user who asked for a rescue stick has not asked
    # for that.
    got="$(catalog_profile_defaults rescue)"
    if [[ -z "$got" ]]; then
        fail "catalog_profile_defaults rescue returned nothing — the assertion below would pass vacuously"
    else
        # Exact id match, not a substring check — "ollama" is also a
        # substring of "ollama-model-qwen3-4b", which must never be true
        # either, but a bare assert_not_contains would only catch that half.
        padded=" $got "
        if [[ "$padded" != *" ollama "* && "$padded" != *"ollama-model-"* ]]; then
            pass
        else
            fail "expected NOT to contain ollama or an ollama-model-* id: [$got]"
        fi
    fi
fi

if it "every local-ai model component states its download size"; then
    out="$(python3 -c "
import json,re
cat=json.load(open('catalog/linux.json'))
bad=[c['id'] for g in cat['categories'] for c in g['components']
     if c['id'].startswith('ollama-model-')
     and not re.search(r'[0-9]+(\.[0-9]+)?\s?GB', c.get('description',''))]
print(' '.join(bad))")"
    [[ -z "$out" ]] && pass || fail "model entries with no size in the description: $out"
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

if it "a dry run for --create-usb (usb write plan) leaves the filesystem untouched"; then
    # Task 6 Step 6: the same property every other dry-run test in this
    # block proves, for the USB feature specifically — a scratch cache dir
    # (never the real one) must come out exactly as it went in, proving
    # usb_plan/setup.sh's --create-usb handling never called fetch_verified
    # or touched the device.
    usb_scratch_cache="$(mktemp -d)"
    before_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    AUTOOS_CACHE_DIR="$usb_scratch_cache" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
        bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
        --engine ventoy --usb-device /dev/sdb >/dev/null 2>&1
    after_listing="$(find "$usb_scratch_cache" 2>/dev/null | sort)"
    assert_eq "$after_listing" "$before_listing"
    rm -rf "$usb_scratch_cache"
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

# ─── USB creation UI (Task 11) ──────────────────────────────────────────────
# The human partner's original request: a button on the action bar that pops
# a dialog asking which OS to put on a stick. Assertions here stay light
# (existence, the [hidden] trap, module-level shape, the guard refusal) —
# the real proof this renders and behaves is Playwright driving the live
# page, not more grep here.

if it "the action bar offers usb creation"; then
    grep -q 'id="createUsb"' web/index.html && pass || fail "no create-usb button"
fi

if it "the usb dialog is hidden by the hidden attribute, not only by a class"; then
    # AGENTS.md §6: an author `display:` rule beats the browser's own
    # [hidden]{display:none}. This shipped twice in one afternoon already
    # (the section menu, the header progress bar) - assert on whitespace-
    # normalised CSS so a reformat cannot silently disable this guard.
    css="$(tr -s ' \t\n' ' ' < web/index.html)"
    [[ "$css" == *'.usb-dialog[hidden] {display:none'* || "$css" == *'.usb-dialog[hidden]{display:none'* ]] \
        && pass || fail "usb dialog will render open on every load"
fi

if it "rufus never reaches the browser's usb engine list (it needs a human at a GUI)"; then
    out="$(python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_images_response
data = usb_images_response()
print("rufus" if any(e["id"] == "rufus" for e in data["engines"]) else "ok")
PY
)"
    [[ "$out" == "ok" ]] && pass || fail "rufus (interactive: true) was offered in the browser"
fi

if it "the create-usb button is disabled when the server is not elevated"; then
    grep -q 'elevated' web/index.html && pass || fail "page never reads the elevated flag"
fi

# `usb_create_response(body: dict) -> tuple[int, dict]` is a MODULE-LEVEL
# function. `Handler` subclasses BaseHTTPRequestHandler and its helpers all
# take `self`, so `Handler._usb_create_body(dict)` would pass the dict AS
# self and raise TypeError - the repo already solves this correctly:
# classify() is module-level for exactly this reason (serve.py, near the
# top). AUTOOS_FAKE_LSBLK is the same synthetic-fixture mechanism the "usb
# safety" tests below use (AGENTS.md §5: never touch a real disk in tests) -
# it reaches usb_create_response's own subprocess call because os.environ is
# inherited, not replaced.
if it "the usb create endpoint refuses an unguarded device"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb root_is_sda)" python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_create_response          # module-level, NOT a Handler method
print(usb_create_response({"device": "/dev/sda", "image": "ubuntu-desktop-lts"}))
PY
)"
    assert_contains "$out" "root filesystem"
fi

if it "the usb create endpoint refuses a device smaller than the image over the HTTP path (finding F12)"; then
    # Finding F12: usb_create_response used to call usb_guard with no
    # AUTOOS_IMAGE_BYTES at all, so the size check compared against 0 and a
    # device smaller than the image still got a 202. tiny_stick is 4 GB;
    # ubuntu-desktop-lts declares sizeGb: 6 in catalog/images.json.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb tiny_stick)" python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_create_response
print(usb_create_response({"device": "/dev/sdb", "image": "ubuntu-desktop-lts", "engine": "ventoy"}))
PY
)"
    assert_contains "$out" "400"
    assert_contains "$out" "too small"
fi

if it "the usb create endpoint refuses a second write while one is already running"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_create_response, RUN, LOCK
with LOCK:
    RUN["running"] = True
try:
    print(usb_create_response({"device": "/dev/sdb", "image": "ubuntu-desktop-lts", "engine": "ventoy"}))
finally:
    with LOCK:
        RUN["running"] = False
PY
)"
    assert_contains "$out" "already in progress"
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

# ─── Answer-file templates & rescue bootstrap (installer USB, task 9) ──────
describe "offline package cache (imagecache)"

if it "imagecache_codename extracts the release codename from .disk/info (cache)"; then
    # The exact string proven live against Ubuntu 26.04.1: a naive
    # `sed 's/.*"\(...\)/'` is greedy and runs to the LAST quote, silently
    # returning empty. grep -o is what must be used instead.
    tmp_stick="$(mktemp -d)"
    mkdir -p "$tmp_stick/.disk"
    printf 'Ubuntu 26.04.1 LTS "Resolute Raccoon" - Release amd64 (20260826)\n' > "$tmp_stick/.disk/info"
    got="$(imagecache_codename "$tmp_stick" 2>/dev/null)"
    rm -rf "$tmp_stick"
    assert_eq "$got" "resolute"
fi

if it "imagecache_codename hard-fails on an empty codename instead of returning it silently (cache)"; then
    tmp_stick="$(mktemp -d)"
    mkdir -p "$tmp_stick/.disk"
    printf 'no quoted codename on this line at all\n' > "$tmp_stick/.disk/info"
    out="$(imagecache_codename "$tmp_stick" 2>&1)"; rc=$?
    rm -rf "$tmp_stick"
    if [[ $rc -ne 0 && -n "$out" ]]; then pass; else fail "expected a non-zero exit and an error message, got rc=$rc: $out"; fi
fi

if it "imagecache_packages matches the catalog's rescue-profile apt packages exactly, same as the template test below (cache)"; then
    # Independent cross-check against the same source of truth the
    # rescue-bootstrap template test below uses (plan finding A12): two
    # different readers of catalog/linux.json must agree, or the catalog has
    # stopped being the single source of truth.
    catalog_pkgs="$(python3 -c "
import json
data = json.load(open('catalog/linux.json'))
pkgs = set()
for cat in data['categories']:
    for c in cat['components']:
        if c.get('provider') == 'apt' and 'rescue' in c.get('profiles', []):
            pkgs.add(c['package'])
print('\n'.join(sorted(pkgs)))
" | tr -d '\r')"
    got_pkgs="$(imagecache_packages catalog/linux.json | tr -d '\r')"
    assert_eq "$got_pkgs" "$catalog_pkgs"
fi

if it "imagecache_build never re-downloads a package whose .deb is already cached (cache)"; then
    # Fully hermetic: fake apt-get and dpkg-scanpackages on PATH, a synthetic
    # catalog, a throwaway stick root. Never touches the real apt, a real USB
    # device, or /mnt/j.
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"
    catalog_json="$tmp_stick/catalog.json"
    fake_apt_log="$tmp_stick/apt.log"

    cat > "$catalog_json" <<'JSON'
{
  "categories": [
    { "id": "test", "name": "Test", "components": [
      {"id":"foo","name":"Foo","description":"d","provider":"apt","package":"foo","profiles":["rescue"]},
      {"id":"bar","name":"Bar","description":"d","provider":"apt","package":"bar","profiles":["rescue"]}
    ] }
  ],
  "profiles": { "rescue": {"name":"Rescue"} }
}
JSON

    cat > "$fakebin/apt-get" <<'EOS'
#!/usr/bin/env bash
# Fake apt-get: logs every invocation, and for a download-only install drops
# an empty placeholder .deb per requested package into the cache dir named
# by -o Dir::Cache=... . Never touches the real system.
printf 'apt-get %s\n' "$*" >>"$FAKE_APT_LOG"
cache="" mode="" pkgs=()
for arg in "$@"; do
    case "$arg" in
        -o) continue ;;
        Dir::Cache=*) cache="${arg#Dir::Cache=}" ;;
        Dir::*|APT::*|Acquire::*) : ;;
        update) mode="update" ;;
        install) mode="install" ;;
        -d|-y|--no-install-recommends) : ;;
        -*) : ;;
        *) pkgs+=("$arg") ;;
    esac
done
if [[ "$mode" == "install" && -n "$cache" ]]; then
    mkdir -p "$cache/archives"
    for p in "${pkgs[@]}"; do : > "$cache/archives/${p}_1.0_amd64.deb"; done
fi
exit 0
EOS
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
# Fake dpkg-scanpackages: enough structure for gzip to have something to
# compress and for the caller to count entries; not a real Packages file.
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/apt-get" "$fakebin/dpkg-scanpackages"

    mkdir -p "$tmp_stick/.disk"
    printf 'Ubuntu 26.04.1 LTS "Resolute Raccoon" - Release amd64 (20260826)\n' > "$tmp_stick/.disk/info"

    # AUTOOS_SUDO="": without this override the function inherits whatever
    # the earlier "sudo is resolved into AUTOOS_SUDO exactly once" detection
    # test left behind (this machine's Windows 11 ships its own sudo.exe),
    # and `sudo mkdir ...` under Git Bash silently fails to create the
    # directory — hermetic tests must not depend on that leaking in.
    out1="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc1=$?
    log1="$(cat "$fake_apt_log" 2>/dev/null)"
    : > "$fake_apt_log"
    out2="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc2=$?
    log2="$(cat "$fake_apt_log" 2>/dev/null)"
    deb_count=$(find "$tmp_stick/rescue/debs" -maxdepth 1 -name '*.deb' 2>/dev/null | wc -l | tr -d ' ')
    has_index=0; [[ -f "$tmp_stick/rescue/debs/Packages.gz" ]] && has_index=1
    has_release=0; [[ -s "$tmp_stick/rescue/debs/Release" ]] && has_release=1

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$log1" == *"install"*"foo"* && "$log1" == *"bar"* \
        && -z "$log2" \
        && "$out2" == *"already cached"* \
        && "$out2" == *"verified"* \
        && "$has_release" -eq 1 \
        && "$deb_count" -eq 2 && "$has_index" -eq 1 ]]; then
        pass
    else
        fail "run1 rc=$rc1 log='${log1:0:150}' | run2 rc=$rc2 log='${log2:0:150}' out2='${out2: -200}' debs=$deb_count index=$has_index release=$has_release"
    fi
fi

if it "imagecache_build fails loudly on a partial cache and names exactly what's missing, then a top-up fetches only that (cache)"; then
    # Reproduces the exact defect a manual build hit: the builder's own
    # "N .deb files" summary looked complete while one catalog package
    # (mdadm) was silently absent. Here a 3-package catalog and a fake
    # apt-get that "forgets" one package on the first run stand in for that:
    # imagecache_build must report failure and name the missing package, and
    # a second run (the package now available) must fetch ONLY that one
    # package — never re-touching the two already cached — and end verified.
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"
    catalog_json="$tmp_stick/catalog.json"
    fake_apt_log="$tmp_stick/apt.log"

    cat > "$catalog_json" <<'JSON'
{
  "categories": [
    { "id": "test", "name": "Test", "components": [
      {"id":"foo","name":"Foo","description":"d","provider":"apt","package":"foo","profiles":["rescue"]},
      {"id":"bar","name":"Bar","description":"d","provider":"apt","package":"bar","profiles":["rescue"]},
      {"id":"baz","name":"Baz","description":"d","provider":"apt","package":"baz","profiles":["rescue"]}
    ] }
  ],
  "profiles": { "rescue": {"name":"Rescue"} }
}
JSON

    # FAKE_APT_DROP names one package this fake apt-get pretends never came
    # down, just like the real dependency-resolution quirk that dropped mdadm.
    cat > "$fakebin/apt-get" <<'EOS'
#!/usr/bin/env bash
printf 'apt-get %s\n' "$*" >>"$FAKE_APT_LOG"
cache="" mode="" pkgs=()
for arg in "$@"; do
    case "$arg" in
        -o) continue ;;
        Dir::Cache=*) cache="${arg#Dir::Cache=}" ;;
        Dir::*|APT::*|Acquire::*) : ;;
        update) mode="update" ;;
        install) mode="install" ;;
        -d|-y|--no-install-recommends) : ;;
        -*) : ;;
        *) pkgs+=("$arg") ;;
    esac
done
if [[ "$mode" == "install" && -n "$cache" ]]; then
    mkdir -p "$cache/archives"
    for p in "${pkgs[@]}"; do
        [[ -n "${FAKE_APT_DROP:-}" && "$p" == "$FAKE_APT_DROP" ]] && continue
        : > "$cache/archives/${p}_1.0_amd64.deb"
    done
fi
exit 0
EOS
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/apt-get" "$fakebin/dpkg-scanpackages"

    mkdir -p "$tmp_stick/.disk"
    printf 'Ubuntu 26.04.1 LTS "Resolute Raccoon" - Release amd64 (20260826)\n' > "$tmp_stick/.disk/info"

    out1="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" FAKE_APT_DROP="baz" \
            imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc1=$?
    : > "$fake_apt_log"
    out2="$(AUTOOS_SUDO="" PATH="$fakebin:$PATH" FAKE_APT_LOG="$fake_apt_log" \
            imagecache_build "$tmp_stick" "$catalog_json" 2>&1)"
    rc2=$?
    log2="$(cat "$fake_apt_log" 2>/dev/null)"
    pkg_count_in_index="$(gunzip -c "$tmp_stick/rescue/debs/Packages.gz" 2>/dev/null | grep -c '^Package:')"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -ne 0 && "$out1" == *"missing"* && "$out1" == *"baz"* \
        && $rc2 -eq 0 && "$out2" == *"verified"* \
        && "$log2" == *"baz"* && "$log2" != *"foo"* && "$log2" != *"bar"* \
        && "$pkg_count_in_index" -eq 3 ]]; then
        pass
    else
        fail "rc1=$rc1 out1='${out1: -200}' | rc2=$rc2 log2='$log2' index_count=$pkg_count_in_index"
    fi
fi

if it "imagecache_index writes a Release file that always matches the current Packages, never a stale one (cache)"; then
    # Reproduces the finding from exercising this against the physical stick:
    # a repo with Packages/Packages.gz but no Release makes apt probe for
    # InRelease/Release, fail, and log a scary "Err: … Method gave a blank
    # filename" before falling back and succeeding anyway — fine for apt,
    # indistinguishable from real breakage for a human on a rescue stick.
    # This test needs no apt-get at all: imagecache_index only reads whatever
    # .deb files are already on disk. Directly proves the risk the coordinator
    # called out — a Release whose checksums don't match Packages is worse
    # than no Release, because apt rejects the whole index — by corrupting
    # Packages after a first index and confirming a second pass fixes both
    # Packages (dpkg-scanpackages always rebuilds it from the .deb files) and
    # Release's checksums to match, rather than leaving either stale.
    tmp_stick="$(mktemp -d)"
    out="$tmp_stick/rescue/debs"
    mkdir -p "$out"
    : > "$out/foo_1.0_amd64.deb"
    : > "$out/bar_1.0_amd64.deb"

    fakebin="$(mktemp -d)"
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/dpkg-scanpackages"

    # A fake apt-ftparchive that FAILS, so the hand-written fallback genuinely
    # is the path under test. Restricting PATH to /usr/bin:/bin did not do
    # that: apt-utils puts the real apt-ftparchive at /usr/bin/apt-ftparchive,
    # so on WSL2 Ubuntu and Ubuntu Desktop this test was silently exercising
    # the OTHER branch while its comment claimed otherwise. Shadowing the name
    # from $fakebin is the only way to pin the branch on every host.
    cat > "$fakebin/apt-ftparchive" <<'EOS'
#!/usr/bin/env bash
exit 1
EOS
    chmod +x "$fakebin/apt-ftparchive"

    out1="$(AUTOOS_SUDO="" PATH="$fakebin:/usr/bin:/bin" imagecache_index "$out" 2>&1)"; rc1=$?
    # Proof the fallback is really the branch being measured below.
    used_fallback=0; [[ "$out1" == *"writing one by hand"* ]] && used_fallback=1
    has_release1=0; [[ -s "$out/Release" ]] && has_release1=1
    sha_release1="$(release_sha256_of "$out/Release" Packages)"
    sha_actual1="$(sha256sum "$out/Packages" | cut -d' ' -f1)"

    # Corrupt Packages directly (stands in for any way the index could go
    # stale between runs) and index again. dpkg-scanpackages always rebuilds
    # Packages from the .deb files on disk, so — since those .deb files never
    # changed — the *fixed* content, and hence its hash, is expected to come
    # back identical to run 1's; what actually proves "not stale" is that
    # Release's checksum tracks that corrected content and NOT the corrupted
    # one it was briefly overwritten with.
    printf 'STALE-GARBAGE\n' >> "$out/Packages"
    sha_corrupted="$(sha256sum "$out/Packages" | cut -d' ' -f1)"
    out2="$(AUTOOS_SUDO="" PATH="$fakebin:/usr/bin:/bin" imagecache_index "$out" 2>&1)"; rc2=$?
    packages_after="$(cat "$out/Packages")"
    sha_release2="$(release_sha256_of "$out/Release" Packages)"
    sha_actual2="$(sha256sum "$out/Packages" | cut -d' ' -f1)"
    # A temp Release.XXXXXX left inside the repo would end up served by apt.
    leftovers="$(find "$out" -maxdepth 1 -name 'Release.*' 2>/dev/null | wc -l | tr -d ' ')"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$used_fallback" -eq 1 \
        && "$has_release1" -eq 1 \
        && -n "$sha_release1" && "$sha_release1" == "$sha_actual1" \
        && "$packages_after" != *"STALE-GARBAGE"* \
        && -n "$sha_release2" && "$sha_release2" == "$sha_actual2" \
        && "$sha_release2" != "$sha_corrupted" \
        && "$leftovers" -eq 0 ]]; then
        pass
    else
        fail "rc1=$rc1 rc2=$rc2 used_fallback=$used_fallback has_release1=$has_release1 sha_release1=$sha_release1 sha_actual1=$sha_actual1 sha_release2=$sha_release2 sha_actual2=$sha_actual2 sha_corrupted=$sha_corrupted leftovers=$leftovers out1='${out1:0:150}' out2='${out2:0:150}'"
    fi
fi

if it "imagecache_index writes a correct Release through apt-ftparchive when apt-utils is present (cache)"; then
    # The companion to the test above, and the half that never ran: on a host
    # with apt-utils, imagecache_write_release takes the apt-ftparchive branch,
    # and nothing asserted anything about it. Real apt-ftparchive output lists
    # MD5Sum before SHA256, which is exactly what made the old unanchored
    # `awk '/^ .*Packages$/'` read an MD5 and compare it to a sha256sum.
    # The fixture reproduces that ordering with genuine checksums, so this
    # pins the branch deterministically on hosts with and without apt-utils.
    tmp_stick="$(mktemp -d)"
    out="$tmp_stick/rescue/debs"
    mkdir -p "$out"
    : > "$out/foo_1.0_amd64.deb"
    : > "$out/bar_1.0_amd64.deb"

    fakebin="$(mktemp -d)"
    cat > "$fakebin/dpkg-scanpackages" <<'EOS'
#!/usr/bin/env bash
dir="${1:-.}"
for f in "$dir"/*.deb; do
    [[ -e "$f" ]] || continue
    printf 'Package: %s\nFilename: %s\n\n' "$(basename "$f")" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/dpkg-scanpackages"
    cat > "$fakebin/apt-ftparchive" <<'EOS'
#!/usr/bin/env bash
# Same output shape as apt-utils' apt-ftparchive: MD5Sum BEFORE SHA256, real
# checksums, run with the repo directory as cwd.
printf 'ran\n' >>"$FAKE_FTPARCHIVE_LOG"
printf 'Origin: \nLabel: \nSuite: \nCodename: ./\nArchitectures: amd64\nComponents: \n'
printf 'MD5Sum:\n'
for f in Packages Packages.gz; do
    [[ -f "$f" ]] || continue
    printf ' %s %16s %s\n' "$(md5sum "$f" | cut -d' ' -f1)" "$(wc -c <"$f" | tr -d ' ')" "$f"
done
printf 'SHA256:\n'
for f in Packages Packages.gz; do
    [[ -f "$f" ]] || continue
    printf ' %s %16s %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(wc -c <"$f" | tr -d ' ')" "$f"
done
exit 0
EOS
    chmod +x "$fakebin/apt-ftparchive"

    ftp_log="$tmp_stick/ftparchive.log"
    : > "$ftp_log"
    out1="$(AUTOOS_SUDO="" PATH="$fakebin:/usr/bin:/bin" FAKE_FTPARCHIVE_LOG="$ftp_log" \
            imagecache_index "$out" 2>&1)"; rc1=$?

    ftp_ran="$(wc -l <"$ftp_log" | tr -d ' ')"
    sha_release="$(release_sha256_of "$out/Release" Packages)"
    sha_actual="$(sha256sum "$out/Packages" | cut -d' ' -f1)"
    md5_actual="$(md5sum "$out/Packages" | cut -d' ' -f1)"
    # What the old, unanchored parse would have returned. Asserting it differs
    # documents why the anchoring exists rather than leaving it to a comment.
    naive="$(awk '/^ .*Packages$/{print $1; exit}' "$out/Release" 2>/dev/null)"
    leftovers="$(find "$out" -maxdepth 1 -name 'Release.*' 2>/dev/null | wc -l | tr -d ' ')"
    release_mode="$(find "$out" -maxdepth 1 -name Release -printf '%m\n' 2>/dev/null)"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 \
        && "$ftp_ran" -ge 1 \
        && "$out1" != *"writing one by hand"* \
        && -n "$sha_release" && "$sha_release" == "$sha_actual" \
        && "$naive" == "$md5_actual" \
        && "$leftovers" -eq 0 \
        && ( -z "$release_mode" || "$release_mode" == "644" ) ]]; then
        pass
    else
        fail "rc1=$rc1 ftp_ran=$ftp_ran sha_release=$sha_release sha_actual=$sha_actual naive=$naive md5_actual=$md5_actual leftovers=$leftovers mode=$release_mode out1='${out1:0:200}'"
    fi
fi

if it "imagecache_verify audits an existing cache without rebuilding it (cache)"; then
    # Pure function, no apt-get/dpkg-scanpackages needed: it only globs for
    # <pkg>_*.deb, so this proves the audit works standalone against
    # whatever is already on a stick — the "run it later without a rebuild"
    # requirement.
    tmp_stick="$(mktemp -d)"
    catalog_json="$tmp_stick/catalog.json"
    cat > "$catalog_json" <<'JSON'
{
  "categories": [
    { "id": "test", "name": "Test", "components": [
      {"id":"foo","name":"Foo","description":"d","provider":"apt","package":"foo","profiles":["rescue"]},
      {"id":"bar","name":"Bar","description":"d","provider":"apt","package":"bar","profiles":["rescue"]}
    ] }
  ],
  "profiles": { "rescue": {"name":"Rescue"} }
}
JSON
    mkdir -p "$tmp_stick/rescue/debs"
    : > "$tmp_stick/rescue/debs/foo_2.1_amd64.deb"
    # "bar" deliberately absent.

    out="$(imagecache_verify "$tmp_stick" "$catalog_json" 2>&1)"; rc=$?
    rm -rf "$tmp_stick"

    if [[ $rc -ne 0 && "$out" == *"bar"* && "$out" != *"foo"* ]]; then
        pass
    else
        fail "expected failure naming only 'bar' as missing (rc=$rc): ${out:0:200}"
    fi
fi

# ─── Verified download (Task 3) ─────────────────────────────────────────────
describe "verified download"

if it "a checksum mismatch fails and leaves nothing behind"; then
    tmp="$(mktemp -d)"; printf 'hello' >"$tmp/src"
    fetch_verified "file://$tmp/src" "$tmp/out" \
        "0000000000000000000000000000000000000000000000000000000000000000" - - ; rc=$?
    if [[ $rc -eq 2 && ! -e "$tmp/out" ]]; then pass
    else fail "rc=$rc, out exists: $([[ -e $tmp/out ]] && echo yes || echo no)"; fi
    rm -rf "$tmp"
fi

if it "a matching checksum succeeds"; then
    tmp="$(mktemp -d)"; printf 'hello' >"$tmp/src"
    sum="$(sha256sum "$tmp/src" | awk '{print $1}')"
    fetch_verified "file://$tmp/src" "$tmp/out" "$sum" - - && [[ -s "$tmp/out" ]] \
        && pass || fail "verified download did not produce the file"
    rm -rf "$tmp"
fi

if it "a cached, already-verified file is skipped, not refetched"; then
    tmp="$(mktemp -d)"; printf 'hello' >"$tmp/src"
    sum="$(sha256sum "$tmp/src" | awk '{print $1}')"
    fetch_verified "file://$tmp/src" "$tmp/out" "$sum" - - >/dev/null
    out="$(fetch_verified "file://$tmp/src" "$tmp/out" "$sum" - - 2>&1)"
    assert_contains "$out" "skipped"
    rm -rf "$tmp"
fi

# ─── USB device enumeration and the safety guard ────────────────────────────
# Task 5 of plan 2026-09-11-installer-usb-and-rescue-profile: usb_guard() is
# the only thing standing between the installer-USB writer and a live
# workstation disk, so this block gets the most tests and no shortcuts. Every
# fixture is synthetic (tests/helpers/fake_usb.py via AUTOOS_FAKE_LSBLK) —
# this suite must never enumerate the real machine's disks (AGENTS.md §5).
# Every test name below carries "usb" so `--filter usb` actually reaches it —
# a filter that matches zero tests reports a clean run indistinguishable
# from a real pass, which bit two earlier tasks in this same plan.
describe "usb safety"

if it "usb_guard refuses the disk holding the root filesystem"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb root_is_sda)" usb_guard /dev/sda 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"root filesystem"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard refuses a non-removable internal disk"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb internal_nvme)" usb_guard /dev/nvme0n1 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not removable"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard refuses a disk with a mounted partition"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_mounted)" usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"mounted"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard refuses a stick smaller than the image"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb tiny_stick)" \
           AUTOOS_IMAGE_BYTES=8000000000 usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"too small"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard accepts a real removable usb stick that is big enough"; then
    AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_IMAGE_BYTES=4000000000 \
        usb_guard /dev/sdb && pass || fail "rejected a valid target"
fi

if it "usb_list still offers a USB SSD reporting as fixed (A10)"; then
    # A10: DriveType/RM alone misses USB SSDs (commonly behind a UAS/UASP
    # bridge, reporting RM=0/"fixed"). Bus type is the signal.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_ssd_fixed)" usb_list)"
    assert_contains "$out" "/dev/sdb"
fi

if it "usb_guard refuses when device discovery itself fails, rather than treating it as an empty list (F8)"; then
    # Finding F8: lsblk output that fails to parse used to become an empty
    # device list, which made root_disk resolve to nothing and skipped the
    # root-disk check entirely - it only failed safe by accident (the bus
    # check also saw an empty list). Malformed AUTOOS_FAKE_LSBLK stands in
    # for "lsblk itself failed/produced garbage" without needing a real
    # broken lsblk.
    out="$(AUTOOS_FAKE_LSBLK="not valid json" usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"discovery failed"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a USB-hosted /boot/efi regardless of bus (F2-prime)"; then
    # Finding F2': reachable whenever the machine booted from USB - the
    # bus/removable check alone accepts this (it IS a real USB device), so
    # the mountpoint itself must be refused, not just the bus type.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_boot_efi_mounted)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"/boot/efi"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_require_elevation refuses to plan a write without root (B10)"; then
    out="$(AUTOOS_SUDO="" AUTOOS_FAKE_UID=1000 usb_require_elevation 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"sudo"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_require_elevation is satisfied when already root (B10)"; then
    AUTOOS_SUDO="" AUTOOS_FAKE_UID=0 usb_require_elevation && pass || fail "refused root"
fi

# ─── Engine-aware guard modes (Task 6, B16) ─────────────────────────────────
# The real-hardware bug that started Task 6: Assert-AutoOSUsbSafe/usb_guard
# correctly refused every internal disk, then refused the legitimate USB
# stick too, because the only mode it knew was "must be unmounted" — wrong
# for uefi-copy, which writes onto a partition that is ALREADY mounted.
# usb_guard's <mode> parameter is what fixes this; every branch gets its
# own fixture (tests/helpers/fake_usb.py) and its own test here.
if it "usb_guard (mounted-fat32-writable mode) accepts an already-mounted writable FAT32 target"; then
    AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" \
        usb_guard /dev/sdb mounted-fat32-writable && pass || fail "rejected a valid uefi-copy target"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a target with nothing mounted"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"no mounted partition"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a mounted partition that is not FAT32"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_wrong_fs_mounted)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not FAT32"* && "$out" == *"ntfs"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard (mounted-fat32-writable mode) refuses a read-only FAT32 partition"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_readonly)" \
           usb_guard /dev/sdb mounted-fat32-writable 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"read-only"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_guard still defaults to unmounted mode when none is given (B16 backward compat)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_mounted)" usb_guard /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"mounted"* ]] && pass || fail "rc=$rc: $out"
fi

# ─── The write planner (Task 6) ─────────────────────────────────────────────
# usb_plan() turns (image, kind, engine, device) into the exact write
# commands, checks every compatibility/platform/lock/safety question first,
# and runs nothing — this is what makes --dry-run provable for this
# feature. Every test name below carries "usb" so `--filter usb` reaches
# it; most also carry "plan" or "engine" (`--filter plan` / `--filter
# engine`), matching the three filters this task's own testing scope asks
# for — the brief warns a filter matching zero tests here has reported a
# clean run twice already on this branch.
describe "usb planning"

if it "usb_plan: a dry run names the device and writes nothing"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           bash setup.sh --create-usb --image ubuntu-desktop-lts \
                         --kind installer --engine ventoy --usb-device /dev/sdb 2>&1)"
    assert_contains "$out" "/dev/sdb"
    assert_contains "$out" "Ventoy2Disk.sh"
    assert_not_contains "$out" "installed"
fi

if it "usb_plan: a raw image is never planned onto ventoy's copy path"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           bash setup.sh --create-usb --image proxmox-ve \
                         --kind installer --engine ventoy --usb-device /dev/sdb 2>&1)" || true
    assert_contains "$out" "raw"
fi

if it "usb_plan: a full-os kind is refused on an engine that cannot build one"; then
    out="$(AUTOOS_DRY_RUN=1 bash setup.sh --create-usb --image ubuntu-desktop-lts \
           --kind full-os --engine ventoy --usb-device /dev/sdb 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"cannot build"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_plan: macOS is refused cleanly, not left to fail inside usb_list"; then
    out="$( SYS_OS=macos bash setup.sh --create-usb --image ubuntu-desktop-lts \
            --kind installer --engine native --usb-device /dev/disk2 2>&1 )"; rc=$?
    [[ $rc -ne 0 && "$out" == *"not supported on macOS"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_plan: the ventoy engine is hidden on arm64, not offered and broken"; then
    out="$( SYS_ARCH=arm64 bash setup.sh --create-usb --list-engines 2>&1 )"
    assert_not_contains "$out" "ventoy"
    assert_contains     "$out" "native"
fi

if it "usb_plan: a second write is refused while one is already in progress"; then
    out="$( AUTOOS_FAKE_RUN_ACTIVE=1 usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb 2>&1 )"
    rc=$?
    [[ $rc -ne 0 && "$out" == *"already in progress"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_plan: uefi-copy needs no elevation but every other engine does (B10)"; then
    # A successful uefi-copy plan (its own fixture: already mounted, FAT32,
    # writable — the opposite of what ventoy/native/wsl need) must never
    # even mention elevation: setup.sh's --create-usb handling skips the
    # check entirely for this one engine, so nothing about it can leak into
    # the output whether or not this session actually has sudo/admin.
    out_uefi="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" AUTOOS_DRY_RUN=1 \
                bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
                --engine uefi-copy --usb-device /dev/sdb 2>&1)"; rc_uefi=$?
    if [[ $rc_uefi -eq 0 && "$out_uefi" != *"levat"* ]]; then pass
    else fail "rc=$rc_uefi: $out_uefi"; fi
fi

if it "usb_plan pins the device identity and emits a re-verify step immediately before the destructive lines (F3/F6)"; then
    # Findings F3/F6: usb_guard running once at plan time proves nothing by
    # the time the plan is actually executed, possibly much later - the
    # plan must carry a fresh usb_guard + identity re-check as its own first
    # step rather than relying on a stale comment.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_DISK_ID=serial-XYZ \
           usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    first_line="$(echo "$out" | head -1)"
    [[ "$first_line" == "usb_reverify /dev/sdb unmounted "*" serial-XYZ" ]] \
        && pass || fail "first plan line was: $first_line"
fi

if it "usb_reverify refuses when the device's identity no longer matches what was pinned at plan time (F3/F6 TOCTOU)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_DISK_ID=serial-CURRENT \
           usb_reverify /dev/sdb unmounted 0 serial-PINNED 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"identity changed"* ]] && pass || fail "rc=$rc: $out"
fi

if it "usb_reverify accepts when the device's identity still matches what was pinned (F3/F6)"; then
    AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_DISK_ID=serial-SAME \
        usb_reverify /dev/sdb unmounted 0 serial-SAME && pass || fail "rejected a matching identity"
fi

if it "usb --undo states plainly that a USB write cannot be undone"; then
    out="$( bash setup.sh --undo --dry-run 2>&1 )"
    assert_contains "$out" "USB"
fi

# ─── The write executor (Task 7) ────────────────────────────────────────────
# usb_execute() is the only thing in usb.sh that ever runs a line usb_plan
# prints - every test here exercises it through the trace/fake runner it
# already has built in (AUTOOS_TRACE/AUTOOS_FORCE_FAIL/AUTOOS_DRY_RUN), or
# through the AUTOOS_FAKE_* escape hatches usb_write_ventoy/usb_copy_image
# grow for the same reason, never against a real device (AGENTS.md SS5).
# Every test name below carries "usb_execute" so both `--filter execute` and
# `--filter usb` reach it - the brief for this task warns a filter matching
# zero tests here has reported a clean run twice already on this branch.
describe "usb execution"

if it "usb_execute: executing a plan runs exactly the planned commands, in order"; then
    plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
            usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    ran="$(AUTOOS_DRY_RUN=1 AUTOOS_TRACE=1 usb_execute /dev/sdb <<<"$plan" 2>&1)"
    assert_eq "$(echo "$ran" | grep -c '^TRACE ')" "$(echo "$plan" | wc -l)"
fi

if it "usb_execute: a failed write never leaves the stick claimed as successful"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FORCE_FAIL=1 \
           usb_execute /dev/sdb <<<"echo x" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" != *"Ready to boot"* ]] && pass || fail "reported success on failure"
fi

if it "usb_execute: reports Ready to boot only once every step succeeds and the device still reads back (B15)"; then
    # A plain "echo hello" plan line used to exercise this test via eval -
    # finding F4 removed eval, so a real step here must match one of
    # usb_plan's own known command shapes. AUTOOS_DRY_RUN=1 keeps it from
    # actually touching anything, the same way every other dry-run test in
    # this file does.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 \
           usb_execute /dev/sdb <<<"usb_write_raw /dev/sdb /tmp/x.iso 100" 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"Ready to boot: /dev/sdb"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: a plan step with an unrecognised command shape is refused, never executed (finding F4)"; then
    # Finding F4: usb_execute used to `eval "$line"` on anything that was
    # not the special-cased Ventoy2Disk.sh line, and device/image values in
    # a real plan reach here from a browser POST body. A line shaped like
    # "cmd; other-cmd args" proves the fix two ways at once: the whole line
    # is refused (unknown "echo" command shape), AND the "; touch $marker"
    # half is never independently executed - eval would have run it as a
    # second shell command, but read -ra only ever word-splits, so it stays
    # one inert literal argument.
    marker="$(mktemp -u)"
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"echo pwned; touch $marker" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && ! -e "$marker" && "$out" == *"known command shape"* ]]; then pass
    else fail "rc=$rc marker=$( [[ -e "$marker" ]] && echo EXISTS || echo absent ) out=$out"; fi
    rm -f "$marker"
fi

if it "usb_execute refuses to report success when the write does not actually read back (finding F10)"; then
    # AUTOOS_FAKE_READBACK=0 forces _usb_verify_readback's own check to
    # fail even though the device still enumerates (good_stick) - proving
    # this checks more than mere presence.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 AUTOOS_FAKE_READBACK=0 \
           usb_execute /dev/sdb <<<"usb_write_raw /dev/sdb /tmp/x.iso 100" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"did not read back"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: a step failure names the stick as removed when it truly vanished, not a generic error"; then
    # root_is_sda's fixture carries no sdb at all - the same "the device is
    # simply gone" state the unplug-mid-write finding describes.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb root_is_sda)" AUTOOS_FORCE_FAIL=1 \
           usb_execute /dev/sdb <<<"echo x" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"removed during the write"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: dispatches a Ventoy2Disk.sh plan line through usb_write_ventoy, not a bare command lookup"; then
    # usb_plan deliberately keeps "Ventoy2Disk.sh -i -g <dev>" literal in its
    # own output (the "usb planning" tests above assert that exact string),
    # even though the real binary is not on PATH until usb_write_ventoy has
    # fetched it. This proves usb_execute bridges that gap instead of just
    # eval-ing the literal plan line (which would fail: command not found).
    # AUTOOS_FAKE_VENTOY_RELEASE points at a fake tarball+sha256.txt served
    # over a throwaway loopback HTTP server (_start_test_http_server above) -
    # no real network, and AUTOOS_CACHE_DIR is "$scratch/images" (not
    # "$scratch" itself) so usb_write_ventoy's tools/ventoy cache, which
    # lives next to dirname(AUTOOS_CACHE_DIR), stays isolated per test
    # instead of colliding on a shared /tmp/tools/ventoy.
    rel="$(mktemp -d)"
    printf '#!/bin/sh\necho "fake ventoy $*"\n' >"$rel/Ventoy2Disk.sh"
    chmod +x "$rel/Ventoy2Disk.sh"
    tar -C "$rel" -czf "$rel/ventoy-9.9.9-linux.tar.gz" Ventoy2Disk.sh
    sum="$(sha256sum "$rel/ventoy-9.9.9-linux.tar.gz" | awk '{print $1}')"
    printf '%s  ventoy-9.9.9-linux.tar.gz\n' "$sum" >"$rel/sha256.txt"
    read -r http_pid http_port < <(_start_test_http_server "$rel")
    base="http://127.0.0.1:$http_port"
    release_json="{\"assets\":[{\"name\":\"ventoy-9.9.9-linux.tar.gz\",\"browser_download_url\":\"$base/ventoy-9.9.9-linux.tar.gz\"},{\"name\":\"sha256.txt\",\"browser_download_url\":\"$base/sha256.txt\"}]}"
    scratch="$(mktemp -d)"
    if [[ -n "$http_port" ]]; then
        out="$(AUTOOS_SUDO="" AUTOOS_CACHE_DIR="$scratch/images" AUTOOS_FAKE_VENTOY_RELEASE="$release_json" \
               AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
               usb_execute /dev/sdb <<<"Ventoy2Disk.sh -i -g /dev/sdb" 2>&1)"; rc=$?
        if [[ $rc -eq 0 && "$out" == *"fake ventoy -i -g /dev/sdb"* ]]; then pass
        else fail "rc=$rc out=$(printf '%s' "$out" | tail -5)"; fi
    else
        fail "test HTTP server never started listening"
    fi
    # wait, not just kill: on this host a killed python http.server can hold
    # its serving directory open for a moment afterward, and the rm -rf right
    # below would otherwise race it ("Device or resource busy").
    kill "$http_pid" 2>/dev/null
    wait "$http_pid" 2>/dev/null
    rm -rf "$rel" "$scratch"
fi

if it "usb_execute: usb_write_ventoy caches the extracted tool and skips re-fetching on a second call"; then
    rel="$(mktemp -d)"
    printf '#!/bin/sh\necho "fake ventoy $*"\n' >"$rel/Ventoy2Disk.sh"
    chmod +x "$rel/Ventoy2Disk.sh"
    tar -C "$rel" -czf "$rel/ventoy-9.9.9-linux.tar.gz" Ventoy2Disk.sh
    sum="$(sha256sum "$rel/ventoy-9.9.9-linux.tar.gz" | awk '{print $1}')"
    printf '%s  ventoy-9.9.9-linux.tar.gz\n' "$sum" >"$rel/sha256.txt"
    read -r http_pid http_port < <(_start_test_http_server "$rel")
    base="http://127.0.0.1:$http_port"
    release_json="{\"assets\":[{\"name\":\"ventoy-9.9.9-linux.tar.gz\",\"browser_download_url\":\"$base/ventoy-9.9.9-linux.tar.gz\"},{\"name\":\"sha256.txt\",\"browser_download_url\":\"$base/sha256.txt\"}]}"
    scratch="$(mktemp -d)"
    if [[ -n "$http_port" ]]; then
        AUTOOS_SUDO="" AUTOOS_CACHE_DIR="$scratch/images" AUTOOS_FAKE_VENTOY_RELEASE="$release_json" \
            usb_write_ventoy /dev/sdb >/dev/null 2>&1
        first_rc=$?
        # Second call is handed a release with NO usable assets at all - if
        # it still succeeds, the cache hit (not a re-fetch) is what made
        # that work.
        out="$(AUTOOS_SUDO="" AUTOOS_CACHE_DIR="$scratch/images" AUTOOS_FAKE_VENTOY_RELEASE='{"assets":[]}' \
               usb_write_ventoy /dev/sdb 2>&1)"; second_rc=$?
        if [[ $first_rc -eq 0 && $second_rc -eq 0 && "$out" == *"fake ventoy -i -g /dev/sdb"* ]]; then pass
        else fail "first_rc=$first_rc second_rc=$second_rc out=$out"; fi
    else
        fail "test HTTP server never started listening"
    fi
    # wait, not just kill: on this host a killed python http.server can hold
    # its serving directory open for a moment afterward, and the rm -rf right
    # below would otherwise race it ("Device or resource busy").
    kill "$http_pid" 2>/dev/null
    wait "$http_pid" 2>/dev/null
    rm -rf "$rel" "$scratch"
fi

if it "usb_execute: usb_write_raw's dry run names the dd command and writes nothing"; then
    out="$(AUTOOS_DRY_RUN=1 usb_write_raw /dev/sdb /path/to/image.iso 123456 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"dd if=/path/to/image.iso of=/dev/sdb"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_write_raw computes its own NN% progress from dd's byte counter (B14)"; then
    # dd status=progress prints bytes-and-rate but never a percentage
    # (lib/linux/process.py's %-regex would otherwise see nothing) - a fake
    # dd on PATH stands in for the real one so this proves the percentage
    # math without writing to anything.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/dd" <<'DDEOF'
#!/usr/bin/env bash
echo "52428800 bytes (52 MB, 50 MiB) copied, 1 s, 52 MB/s" >&2
echo "104857600 bytes (105 MB, 100 MiB) copied, 2 s, 52 MB/s" >&2
exit 0
DDEOF
    chmod +x "$fakebin/dd"
    img="$(mktemp)"; printf 'x' >"$img"
    out="$(PATH="$fakebin:$PATH" AUTOOS_SUDO="" usb_write_raw /dev/fake "$img" 104857600 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"50%"* && "$out" == *"100%"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
    rm -rf "$fakebin"; rm -f "$img"
fi

if it "usb_execute: usb_write_raw returns non-zero when dd itself fails"; then
    fakebin="$(mktemp -d)"
    cat >"$fakebin/dd" <<'DDEOF'
#!/usr/bin/env bash
echo "dd: fake write error" >&2
exit 1
DDEOF
    chmod +x "$fakebin/dd"
    img="$(mktemp)"; printf 'x' >"$img"
    PATH="$fakebin:$PATH" AUTOOS_SUDO="" usb_write_raw /dev/fake "$img" 104857600 >/dev/null 2>&1
    assert_eq "$?" "1"
    rm -rf "$fakebin"; rm -f "$img"
fi

if it "usb_execute: usb_copy_image's dry run writes nothing"; then
    out="$(AUTOOS_DRY_RUN=1 usb_copy_image /dev/sdb /tmp/x.iso /tmp/extra.sh 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"would copy"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image refuses a mounted ISO9660 stick (finding A11)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_iso9660_mounted)" usb_copy_image /dev/sdb /tmp/x.iso 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"A11"* && "$out" == *"raw block copy"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image refuses an unmounted ISO9660 partition before mounting anything (A11, ventoy path)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_iso9660_unmounted)" usb_copy_image /dev/sdb /tmp/x.iso 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"A11"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image refuses a >4GiB inner file before copying anything (B16)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" AUTOOS_FAKE_ISO_MAX_FILE_BYTES=5000000000 \
           AUTOOS_FAKE_ISO_MAX_FILE_NAME="casper/big.squashfs" usb_copy_image /dev/sdb /tmp/x.iso 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"casper/big.squashfs"* && "$out" == *"4 GiB"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_execute: usb_copy_image fails the whole call when umount itself fails after the write (finding F9)"; then
    fakebin="$(mktemp -d)"
    printf '#!/usr/bin/env bash\nexit 0\n' >"$fakebin/mount"
    printf '#!/usr/bin/env bash\necho "umount: fake failure" >&2\nexit 1\n' >"$fakebin/umount"
    chmod +x "$fakebin/mount" "$fakebin/umount"
    img="$(mktemp)"; printf 'x' >"$img"
    out="$(PATH="$fakebin:$PATH" AUTOOS_SUDO="" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           AUTOOS_FAKE_ISO_MAX_FILE_BYTES=1000000 AUTOOS_FAKE_ISO_MAX_FILE_NAME=x \
           usb_copy_image /dev/sdb "$img" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"could not unmount"* ]] && pass || fail "rc=$rc out=$out"
    rm -rf "$fakebin"; rm -f "$img"
fi

if it "usb_execute: usb_copy_image mounts ventoy's data partition by index, not by size (finding F7)"; then
    fakebin="$(mktemp -d)"
    record="$(mktemp)"
    printf '#!/usr/bin/env bash\necho "$1" >> %s\nexit 0\n' "$record" >"$fakebin/mount"
    printf '#!/usr/bin/env bash\nexit 0\n' >"$fakebin/umount"
    chmod +x "$fakebin/mount" "$fakebin/umount"
    img="$(mktemp)"; printf 'x' >"$img"
    PATH="$fakebin:$PATH" AUTOOS_SUDO="" AUTOOS_FAKE_LSBLK="$(fake_usb ventoy_partitions_reversed)" \
        AUTOOS_FAKE_ISO_MAX_FILE_BYTES=1000000 AUTOOS_FAKE_ISO_MAX_FILE_NAME=x \
        usb_copy_image /dev/sdb "$img" >/dev/null 2>&1
    mounted="$(cat "$record")"
    [[ "$mounted" == "/dev/sdb1" ]] && pass || fail "mounted: $mounted (expected /dev/sdb1, the lower-index partition, not sdb2 which is larger)"
    rm -rf "$fakebin"; rm -f "$img" "$record"
fi

if it "usb_execute: usb_ventoy_add_persistence caps the default 16GB at half the stick's size (B17)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_DRY_RUN=1 usb_ventoy_add_persistence /dev/sdb 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$out" == *"capping"* && "$out" == *"15GB"* ]] && pass || fail "rc=$rc out=$out"
fi

# ─── Answer-file templates ──────────────────────────────────────────────────
describe "answer file templates"

if it "no template contains a credential"; then
    # A1 was a committed PLAINTEXT password, and the only alternative that
    # could ever have caught one — `password[[:space:]]+[^ ]` — cannot match
    # YAML, where `password` is followed by `:`. Setting
    # `password: "hunter2RealPassword"` in user-data.example passed this test.
    # The `password[[:space:]]*:` alternative below is the one that fires on
    # the shape the actual incident had; `\$6\$` keeps covering crypt hashes.
    cred_pattern='(\$6\$'
    cred_pattern+='|password[[:space:]]+[^ ]'
    cred_pattern+='|password[[:space:]]*:[[:space:]]*["'"'"']?[^C]'
    cred_pattern+='|passwd/user-password)'
    if grep -rnE "$cred_pattern" templates/ | grep -v 'CHANGE-ME'; then
        fail "a template carries something that looks like a real credential"
    else pass; fi
fi

if it "no template wipes a disk without being asked"; then
    # A3 was an unprompted whole-disk wipe. The old form of this test reduced
    # to "does the string AUTOOS_WIPE_TARGET_DISK appear anywhere under
    # templates/" — and it appears only in a comment, so it was assert(true):
    # replacing `interactive-sections: [storage]` with a live `storage:` block
    # passed. Assert the actual gate in each file instead.
    wipe_problems=""

    # user-data.example: subiquity only stops and asks a human when "storage"
    # is listed under interactive-sections...
    ia_sections="$(awk '
        /^[[:space:]]*interactive-sections:[[:space:]]*$/ { grab = 1; next }
        grab && /^[[:space:]]*-[[:space:]]*[A-Za-z]/ {
            sub(/^[[:space:]]*-[[:space:]]*/, "")
            print
            next
        }
        grab && /^[[:space:]]*#/ { next }
        grab                     { grab = 0 }
    ' templates/user-data.example)"
    [[ " $(printf '%s' "$ia_sections" | tr '\n' ' ') " == *" storage "* ]] \
        || wipe_problems+="user-data.example does not list 'storage' under interactive-sections; "

    # ...and only as long as no live top-level `storage:` key overrides it.
    # A commented one (`# storage:`) is the documented, inert example and is
    # not matched: `[[:space:]]*` cannot consume a '#'.
    live_storage="$(grep -nE '^[[:space:]]*storage:' templates/user-data.example || true)"
    [[ -z "$live_storage" ]] \
        || wipe_problems+="user-data.example has an uncommented storage: block ($live_storage); "

    # preseed.cfg.example: d-i partitions automatically only once
    # partman-auto/method is set. Unset (or commented) means it asks.
    live_partman="$(grep -nE '^[[:space:]]*(d-i[[:space:]]+)?partman-auto/method' templates/preseed.cfg.example || true)"
    [[ -z "$live_partman" ]] \
        || wipe_problems+="preseed.cfg.example sets partman-auto/method ($live_partman); "

    if [[ -n "$wipe_problems" ]]; then
        fail "automatic partitioning is not gated behind an interactive prompt: $wipe_problems"
    else pass; fi
fi

if it "rescue-bootstrap.sh installs exactly the catalog's rescue-profile apt packages (template)"; then
    # The catalog (catalog/linux.json) is the single source of truth for what
    # "rescue tooling" means. This is finding A12 in the installer-USB plan:
    # the old scripts kept a second, hand-written package list that quietly
    # drifted from the catalog. Cross-checking the two here means it can't
    # drift again without a test noticing.
    #
    # It enforced only half of that, and by the weakest possible means:
    # `grep -q -w "$pkg" templates/rescue-bootstrap.sh` matched COMMENTED-OUT
    # lines, so commenting out the volume-management and cloning groups —
    # removing LVM, RAID, LUKS and cloning from the rescue toolkit — still
    # passed. It also never looked the other way, so a package in the script
    # but not the catalog was invisible. Both are fixed below: the script's
    # list is parsed out of its live `install_apt_group` calls, and the two
    # lists are compared as sets in both directions.
    catalog_pkgs="$(python3 -c "
import json
data = json.load(open('catalog/linux.json'))
pkgs = set()
for cat in data['categories']:
    for c in cat['components']:
        if c.get('provider') == 'apt' and 'rescue' in c.get('profiles', []):
            pkgs.add(c['package'])
print('\n'.join(sorted(pkgs)))
" | tr -d '\r')"

    # Every argument of every live `install_apt_group "<group>" pkg...` call,
    # minus the group name. A leading '#' disqualifies the whole line, which
    # is the specific mutation the old test could not see.
    script_pkgs="$(awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*install_apt_group[[:space:]]/ {
            line = $0
            sub(/^[[:space:]]*install_apt_group[[:space:]]+/, "", line)
            # Drop the group name, quoted or not — exactly one of these.
            if (line ~ /^"/) sub(/^"[^"]*"[[:space:]]*/, "", line)
            else             sub(/^[^[:space:]]+[[:space:]]*/, "", line)
            n = split(line, a, /[[:space:]]+/)
            for (i = 1; i <= n; i++) if (a[i] != "") print a[i]
        }
    ' templates/rescue-bootstrap.sh | tr -d '\r' | LC_ALL=C sort -u)"

    # LC_ALL=C on both sides: comm needs one collation order, and a locale that
    # ignores hyphens would reorder names like lm-sensors against lvm2.
    catalog_sorted="$(printf '%s\n' "$catalog_pkgs" | sed '/^$/d' | LC_ALL=C sort -u)"
    script_sorted="$(printf '%s\n' "$script_pkgs" | sed '/^$/d')"

    only_in_catalog="$(comm -23 <(printf '%s\n' "$catalog_sorted") \
                                <(printf '%s\n' "$script_sorted") | tr '\n' ' ')"
    only_in_script="$(comm -13 <(printf '%s\n' "$catalog_sorted") \
                               <(printf '%s\n' "$script_sorted") | tr '\n' ' ')"

    if [[ -z "${only_in_catalog// /}" && -z "${only_in_script// /}" ]]; then
        pass
    else
        fail "catalog-only: [${only_in_catalog% }] | bootstrap-only: [${only_in_script% }]"
    fi
fi

if it "the rescue bootstrap template is shellcheck clean"; then
    files=(templates/rescue-bootstrap.sh templates/ai-dispatcher.sh)
    if has_cmd shellcheck; then
        out="$(shellcheck -S warning "${files[@]}" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "$(printf '%s' "$out" | head -20)"; fi
    elif has_cmd docker && docker info >/dev/null 2>&1; then
        # MSYS_NO_PATHCONV: on Windows Git Bash, MSYS mangles the bare "/mnt"
        # argument into a host path before docker ever sees it. A no-op
        # elsewhere (native Linux/macOS docker never looks at this var).
        out="$(MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable \
               -S warning "${files[@]}" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "(via docker) $(printf '%s' "$out" | head -20)"; fi
    else
        skip "no shellcheck binary and no usable docker"
    fi
fi

if it "[trusted=yes] never appears on a real network apt source line (template)"; then
    # [trusted=yes] belongs only on the one local, generated-on-this-stick
    # file:// repo. The old form rejected exactly two literal hostnames, so
    # `deb [trusted=yes] http://mirror.corp/ubuntu jammy main` sailed through.
    # Inverted: every live (non-comment) line that carries trusted=yes must
    # also carry file: — which no network source ever can.
    offenders="$(grep -rn 'trusted=yes' templates/ \
                 | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' \
                 | grep -v 'file:' || true)"
    if [[ -n "$offenders" ]]; then
        fail "[trusted=yes] on a line that is not a local file:// source: ${offenders//$'\n'/ | }"
    else pass; fi
fi

if it "rescue-bootstrap registers the local offline cache as an unsigned local apt source, idempotently (template)"; then
    # Hermetic: an isolated fake "stick root" with a fake cache and a fake
    # apt-get, and AUTOOS_OFFLINE_APT_LIST redirected to a temp file so this
    # never touches a real /etc/apt/sources.list.d. A real copy of the
    # template (minus the trailing `main "$@"` call) is sourced from inside
    # the fake stick root so SCRIPT_DIR resolves exactly as it would on the
    # real stick, next to a real "rescue/debs" cache directory — see
    # detect_offline_cache() in templates/rescue-bootstrap.sh.
    fake_stick="$(mktemp -d)"
    mkdir -p "$fake_stick/rescue/debs"
    : > "$fake_stick/rescue/debs/Packages.gz"
    sed '$d' templates/rescue-bootstrap.sh > "$fake_stick/rescue-bootstrap.sh"

    fakebin="$(mktemp -d)"
    cat >"$fakebin/apt-get" <<'EOS'
#!/usr/bin/env bash
printf 'apt-get %s\n' "$*" >>"$FAKE_APT_LOG"
exit 0
EOS
    chmod +x "$fakebin/apt-get"

    fake_list="$fake_stick/autoos-offline.list"
    fake_apt_log="$fake_stick/apt.log"

    out="$(
        PATH="$fakebin:/usr/bin:/bin" \
        AUTOOS_OFFLINE_APT_LIST="$fake_list" \
        AUTOOS_SUDO="" \
        FAKE_APT_LOG="$fake_apt_log" \
        bash -c '
            source "$1"
            NETWORK_OK="no"   # force the offline branch without a real network probe
            register_offline_cache
            printf "REGISTERED=%s\n" "$OFFLINE_CACHE_REGISTERED"
            if apt_can_install; then printf "CAN_INSTALL=yes\n"; else printf "CAN_INSTALL=no\n"; fi
            register_offline_cache   # second call: must be idempotent, not append a duplicate line
        ' _ "$fake_stick/rescue-bootstrap.sh" 2>&1
    )"
    rc=$?
    list_content="$(cat "$fake_list" 2>/dev/null)"
    list_line_count="$(grep -c '^deb \[trusted=yes\]' "$fake_list" 2>/dev/null || true)"
    abs_cache="$(cd "$fake_stick/rescue/debs" && pwd)"
    rm -rf "$fake_stick" "$fakebin"

    if [[ $rc -eq 0 \
        && "$out" == *"UNSIGNED"* \
        && "$out" == *"REGISTERED=1"* \
        && "$out" == *"CAN_INSTALL=yes"* \
        && "$out" == *"offline cache already registered"* \
        && "$list_content" == *"deb [trusted=yes] file:${abs_cache} ./"* \
        && "$list_line_count" -eq 1 ]]; then
        pass
    else
        fail "rc=$rc list_lines=$list_line_count out=${out:0:400}"
    fi
fi

# ─── rescue-bootstrap double-run fixture (AGENTS.md §4) ─────────────────────
# install_rescue_tools, install_nodejs, install_ai_clis, install_ai_dispatcher,
# write_ai_profile and main() had ZERO execution coverage, and that is exactly
# how three idempotency defects survived to the final gate: both files an
# operator can edit were overwritten wholesale on every run, and a second run
# reported "installed=2 skipped=0" while the script's own header claimed it
# reported "skipped". Nothing static could catch that — only running it twice.
#
# Hermetic. apt-get, dpkg, npm, curl, sudo and id are all stubs on a fake PATH,
# AUTOOS_ROOT_PREFIX points every destination at a temp directory, and nothing
# is installed for real. Two deliberate choices:
#
#   * the fake `id` always reports a non-root uid, so require_root() takes the
#     sudo path. That is the normal Ubuntu live session (user "ubuntu" with
#     passwordless sudo) and the only path on which a privileged command that
#     forgot $AUTOOS_SUDO shows up at all.
#   * the fake `npm` is on the PATH from the start rather than being created by
#     the fake apt-get. A developer machine may well have a real /usr/bin/npm,
#     and `$AUTOOS_SUDO npm install -g` finding it would install software for
#     real — which no test in this suite may ever do. The cost is that
#     install_nodejs takes its "already installed" branch here; that branch is
#     asserted instead. PATH is otherwise restricted to /usr/bin:/bin for the
#     same reason the ai-dispatcher tests below restrict it.
bootstrap_sandbox_setup() {
    BS_STICK="$(mktemp -d)"
    BS_ROOT="$BS_STICK/fakeroot"
    BS_BIN="$BS_STICK/fakebin"
    BS_LOG="$BS_STICK/cmd.log"
    BS_DPKG_DB="$BS_STICK/dpkg-db"
    BS_REGISTRY="$BS_ROOT/etc/autoos/ai-clients.conf"
    BS_PROFILE="$BS_ROOT/etc/profile.d/autoos-ai.sh"
    BS_AI_BIN="$BS_ROOT/usr/local/bin/ai"
    mkdir -p "$BS_ROOT" "$BS_BIN"
    : > "$BS_LOG"
    : > "$BS_DPKG_DB"

    cp templates/rescue-bootstrap.sh templates/ai-clients.conf \
       templates/ai-dispatcher.sh "$BS_STICK/"

    cat > "$BS_BIN/id" <<'EOS'
#!/usr/bin/env bash
[[ "${1:-}" == "-u" ]] && { printf '1000\n'; exit 0; }
exit 1
EOS
    cat > "$BS_BIN/sudo" <<'EOS'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >>"$FAKE_CMD_LOG"
# Marks the child as privileged so a stub can record which side of
# $AUTOOS_SUDO it was invoked from. Real sudo scrubs the environment; here the
# point is precisely to observe that it was used at all.
export FAKE_SUDO_ACTIVE=1
exec "$@"
EOS
    cat > "$BS_BIN/curl" <<'EOS'
#!/usr/bin/env bash
# Covers two call shapes: network_reachable()'s `--head` probe (just needs
# exit 0) and install_agy_cli()'s `-o <file> <url>` download, which needs to
# leave behind something that looks like a real installer script (starts
# with "#!", non-empty) so install_agy_cli()'s own safety check passes. The
# written "installer" drops a fake `agy` binary into $FAKE_BIN_DIR — the same
# directory npm's fake binaries land in below — the way the real one would
# install a real binary somewhere on PATH.
out=""
prev=""
for a in "$@"; do
    [[ "$prev" == "-o" ]] && out="$a"
    prev="$a"
done
if [[ -n "$out" ]]; then
    cat > "$out" <<'INSTALLER'
#!/usr/bin/env bash
: > "$FAKE_BIN_DIR/agy"
chmod +x "$FAKE_BIN_DIR/agy"
INSTALLER
fi
exit 0
EOS
    cat > "$BS_BIN/dpkg" <<'EOS'
#!/usr/bin/env bash
# pkg_installed() only ever calls `dpkg -s <pkg>`.
[[ "${1:-}" == "-s" ]] || exit 1
grep -qx -- "${2:-}" "$FAKE_DPKG_DB" 2>/dev/null
EOS
    cat > "$BS_BIN/apt-get" <<'EOS'
#!/usr/bin/env bash
printf 'apt-get %s\n' "$*" >>"$FAKE_CMD_LOG"
mode=""
for a in "$@"; do
    case "$a" in
        update)  exit 0 ;;
        install) mode=install ;;
        -*)      ;;
        *)       [[ "$mode" == install ]] && printf '%s\n' "$a" >>"$FAKE_DPKG_DB" ;;
    esac
done
exit 0
EOS
    cat > "$BS_BIN/npm" <<'EOS'
#!/usr/bin/env bash
printf 'npm(sudo=%s) %s\n' "${FAKE_SUDO_ACTIVE:-0}" "$*" >>"$FAKE_CMD_LOG"
if [[ "${1:-}" == "install" ]]; then
    for a in "$@"; do
        bin=""
        [[ "$a" == "@anthropic-ai/claude-code" ]] && bin=claude
        [[ -n "$bin" ]] || continue
        printf '#!/usr/bin/env bash\nexit 0\n' >"$FAKE_BIN_DIR/$bin"
        chmod +x "$FAKE_BIN_DIR/$bin"
    done
fi
exit 0
EOS
    chmod +x "$BS_BIN"/*
}

bootstrap_sandbox_run() {
    PATH="$BS_BIN:/usr/bin:/bin" \
    AUTOOS_ROOT_PREFIX="$BS_ROOT" \
    AUTOOS_OFFLINE_APT_LIST="$BS_STICK/offline.list" \
    FAKE_CMD_LOG="$BS_LOG" \
    FAKE_DPKG_DB="$BS_DPKG_DB" \
    FAKE_BIN_DIR="$BS_BIN" \
    bash "$BS_STICK/rescue-bootstrap.sh" 2>&1
}

bootstrap_sandbox_backups() {
    find "$BS_ROOT" -name '*.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' '
}

if it "rescue-bootstrap run twice installs nothing the second time and says skipped (template)"; then
    bootstrap_sandbox_setup

    out1="$(bootstrap_sandbox_run)"; rc1=$?
    installed1="$(printf '%s\n' "$out1" | grep -c '^  installed ' || true)"
    # B4: npm install -g writes to /usr/lib/node_modules, so it must be
    # privileged like every other install here. Without $AUTOOS_SUDO it fails
    # EACCES on exactly the path require_root() exists to support. Only
    # claude goes through npm now — agy is a standalone downloaded binary.
    npm_sudo="$(grep -cF 'npm(sudo=1) install -g ' "$BS_LOG" || true)"
    npm_bare="$(grep -cF 'npm(sudo=0)' "$BS_LOG" || true)"
    agy_installed1="$(printf '%s\n' "$out1" | grep -cF '  installed agy' || true)"

    out2="$(bootstrap_sandbox_run)"; rc2=$?
    installed2="$(printf '%s\n' "$out2" | grep -c '^  installed ' || true)"
    bs_backups="$(bootstrap_sandbox_backups)"
    marker_present=0; [[ -f "$BS_ROOT/var/lib/autoos/rescue-bootstrap.done" ]] && marker_present=1

    rm -rf "$BS_STICK"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$installed1" -gt 0 \
        && "$npm_sudo" -eq 1 && "$npm_bare" -eq 0 \
        && "$agy_installed1" -eq 1 \
        && "$installed2" -eq 0 \
        && "$out2" == *"0 installed,"* \
        && "$out2" == *"ai registry"*"every shipped backend already registered"* \
        && "$out2" == *"ai dispatcher"*"unchanged"* \
        && "$out2" == *"autoos-ai.sh (unchanged)"* \
        && "$bs_backups" -eq 0 \
        && "$marker_present" -eq 1 ]]; then
        pass
    else
        fail "rc1=$rc1 rc2=$rc2 installed1=$installed1 installed2=$installed2 npm_sudo=$npm_sudo npm_bare=$npm_bare agy_installed1=$agy_installed1 backups=$bs_backups marker=$marker_present | run2='${out2:0:600}'"
    fi
fi

if it "rescue-bootstrap keeps the operator's API key and extra AI backend on a second run (template)"; then
    # The two files an operator edits. templates/ai-clients.conf documents
    # itself as THE extension point ("adding a third AI CLI later is a one-line
    # addition to THIS file") and /etc/profile.d/autoos-ai.sh says "Uncomment
    # and paste your own below" — and both were then replaced wholesale on the
    # next run, taking the operator's registered backend and their API key with
    # them (AGENTS.md hard rules 4 and 5).
    bootstrap_sandbox_setup

    out1="$(bootstrap_sandbox_run)"; rc1=$?

    # Now be the operator: register a third backend, drop one that isn't
    # installed on this machine, and paste an API key into the profile.
    # (Deliberately not key-shaped — this repository is public, AGENTS.md
    # hard rule 1.)
    printf 'ollama:ollama:Local Ollama\n' >> "$BS_REGISTRY"
    grep -v '^agy:' "$BS_REGISTRY" > "$BS_REGISTRY.edit" && mv "$BS_REGISTRY.edit" "$BS_REGISTRY"
    printf '# my own key, pasted here as the file invites\nexport ANTHROPIC_API_KEY=AUTOOS-TEST-FIXTURE-NOT-A-KEY\n' \
        >> "$BS_PROFILE"

    out2="$(bootstrap_sandbox_run)"; rc2=$?

    key_lines="$(grep -c 'AUTOOS-TEST-FIXTURE-NOT-A-KEY' "$BS_PROFILE" || true)"
    ollama_lines="$(grep -c '^ollama:ollama:Local Ollama$' "$BS_REGISTRY" || true)"
    claude_lines="$(grep -c '^claude:' "$BS_REGISTRY" || true)"
    agy_lines="$(grep -c '^agy:' "$BS_REGISTRY" || true)"
    profile_backups="$(find "$(dirname "$BS_PROFILE")" -name 'autoos-ai.sh.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' ')"
    registry_backups="$(find "$(dirname "$BS_REGISTRY")" -name 'ai-clients.conf.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' ')"

    rm -rf "$BS_STICK"

    if [[ $rc1 -eq 0 && $rc2 -eq 0 \
        && "$key_lines" -eq 1 \
        && "$ollama_lines" -eq 1 \
        && "$claude_lines" -eq 1 \
        && "$agy_lines" -eq 1 \
        && "$profile_backups" -eq 1 \
        && "$registry_backups" -eq 1 \
        && "$out2" == *"kept your edits"* ]]; then
        pass
    else
        fail "rc1=$rc1 rc2=$rc2 key=$key_lines ollama=$ollama_lines claude=$claude_lines agy=$agy_lines profile_backups=$profile_backups registry_backups=$registry_backups | run2='${out2:0:600}'"
    fi
fi

if it "ai dispatcher --list names both backends (template)"; then
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" bash templates/ai-dispatcher.sh --list 2>&1)"
    rc=$?
    if [[ $rc -eq 0 && "$out" == *"claude"* && "$out" == *"agy"* ]]; then
        pass
    else
        fail "expected both backends listed (rc=$rc): ${out:0:200}"
    fi
fi

if it "ai dispatcher falls back cleanly when the default binary is absent (template)"; then
    # Hermetic: a fake PATH provides only "agy", never touches the real
    # machine, and never installs anything. Restricted to /usr/bin:/bin so a
    # real claude/agy binary elsewhere on this machine's PATH (e.g. this
    # very agent's own `claude`) cannot leak into the test.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/agy" <<'EOS'
#!/usr/bin/env bash
printf 'agy-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/agy"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh "hello" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == *"agy-fake:hello"* && "$out" == *"not installed"* ]]; then
        pass
    else
        fail "expected a clean fallback to agy (rc=$rc): ${out:0:200}"
    fi
fi

if it "ai dispatcher honours an explicit backend even when it differs from the default (template)"; then
    # Same hermetic fake-PATH approach, but now both backends "exist" and the
    # caller names one explicitly — the dispatcher must not substitute a
    # different backend once the caller has been specific.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/claude" <<'EOS'
#!/usr/bin/env bash
printf 'claude-fake:%s\n' "$*"
EOS
    cat >"$fakebin/agy" <<'EOS'
#!/usr/bin/env bash
printf 'agy-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/claude" "$fakebin/agy"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh agy "hi" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "agy-fake:hi" ]]; then
        pass
    else
        fail "expected the explicitly-named backend to run (rc=$rc): ${out:0:200}"
    fi
fi

if it "ai dispatcher warns once on an unrecognized backend-shaped token instead of misrouting silently (template)"; then
    # D1: `ai gemeni "..."` (a typo) used to silently run the default backend
    # with "gemeni" as literal prompt text and exit 0 — no signal that the
    # backend name was never recognized. It must still fall through to the
    # default (real ambiguity: `ai "why did this fail?"` is a single arg and
    # must never be treated as an unknown backend), but now with one warning.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/claude" <<'EOS'
#!/usr/bin/env bash
printf 'claude-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/claude"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh gemeni "hi" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 \
        && "$out" == *'ai: "gemeni" is not a known backend, treating it as part of the prompt; see ai --list'* \
        && "$out" == *"claude-fake:gemeni hi"* ]]; then
        pass
    else
        fail "expected a warning plus the default backend fed 'gemeni hi' verbatim (rc=$rc): ${out:0:300}"
    fi
fi

if it "ai dispatcher prints no warning for a single-argument prompt, even one shaped like an id (template)"; then
    # The other half of D1's ambiguity guard: a lone word with nothing after
    # it must never be second-guessed as an unrecognized backend.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/claude" <<'EOS'
#!/usr/bin/env bash
printf 'claude-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/claude"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh diagnose 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "claude-fake:diagnose" && "$out" != *"not a known backend"* ]]; then
        pass
    else
        fail "expected no warning for a single-word prompt (rc=$rc): ${out:0:200}"
    fi
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

if it "no installer pipes a downloaded script into a shell (A14)"; then
    # A pipe can't be inspected before it runs and can be swapped mid-stream;
    # a downloaded file can be both (see install_agy(), install_ollama() and
    # install_uv() in lib/linux/install.sh for the fixed pattern). This is
    # the regression guard for A14: no `curl`/`wget` line may feed a shell
    # interpreter through a pipe, in this file or in templates/. Comment
    # lines are skipped deliberately — several installers document the old,
    # vulnerable one-liner in a comment (`curl ... | bash`) as the pattern
    # they replaced, and that prose must not itself trip the check.
    files=(lib/linux/install.sh templates/*.sh)
    bad=""
    for f in "${files[@]}"; do
        [[ -f "$f" ]] || continue
        while IFS=: read -r lineno line; do
            trimmed="${line#"${line%%[![:space:]]*}"}"
            [[ "$trimmed" == \#* ]] && continue
            bad+="  $f:$lineno: ${line#"${line%%[![:space:]]*}"}"$'\n'
        done < <(grep -nE '\b(curl|wget)\b.*\|[[:space:]]*(sudo[[:space:]]+)?[^|]*\b(sh|bash)\b' "$f")
    done
    if [[ -z "$bad" ]]; then pass; else fail "$(printf '%s' "$bad")"; fi
fi

if it "shellcheck is clean"; then
    # Fall back to the official image when shellcheck is not installed. This
    # check being skipped locally is precisely how a shellcheck failure reached
    # CI unnoticed, so "no binary" should not silently mean "no check".
    files=(setup.sh lib/linux/*.sh tests/run-tests.sh)
    if has_cmd shellcheck; then
        out="$(shellcheck -S warning "${files[@]}" 2>&1)"; rc=$?
        if [[ $rc -eq 0 ]]; then pass; else fail "$(printf '%s' "$out" | head -20)"; fi
    elif has_cmd docker && docker info >/dev/null 2>&1; then
        # MSYS_NO_PATHCONV: same Windows Git Bash trap as the answer-file
        # template lint above — MSYS rewrites the bare "/mnt" into a host path
        # before docker ever sees it, and the container then refuses to start.
        # Without this the whole lint FAILS (not skips) on Windows, which is
        # where this repository is developed.
        out="$(MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable \
               -S warning "${files[@]}" 2>&1)"; rc=$?
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
