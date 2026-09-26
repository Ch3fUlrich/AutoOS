#!/usr/bin/env bash
# AutoOS Linux test suite.
#
# Zero dependencies on purpose: the whole point of this repo is to run on a
# machine where nothing is installed yet, so the tests must not need bats.
#
#   bash tests/run-tests.sh            run here
#   bash tests/run-tests.sh --wsl      re-run inside WSL2 (from Windows)
#   bash tests/run-tests.sh --filter catalog
#   bash tests/run-tests.sh --filter usb,catalog   comma = OR (shard union)
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

if ! bash -n "${BASH_SOURCE[0]}"; then
    printf 'run-tests.sh: syntax error, no test was run\n' >&2
    exit 2
fi
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
SUMMARY_PRINTED=0
CURRENT=""
FAILED_NAMES=()

trap 'rc=$?; if [[ $rc -eq 0 && $SUMMARY_PRINTED -eq 0 ]]; then printf "run-tests.sh: ended before the summary line\n" >&2; exit 1; fi' EXIT

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
    if [[ -n "$FILTER" ]]; then
        # Comma-separated OR: --filter usb,catalog runs the union, so shards
        # can be disjoint partitions executed in parallel worktrees.
        local IFS=',' _terms _t _hit=0
        read -ra _terms <<< "$FILTER"
        for _t in "${_terms[@]}"; do
            if [[ -n "$_t" && "$CURRENT" == *"$_t"* ]]; then _hit=1; break; fi
        done
        if (( _hit == 0 )); then CURRENT=""; return 1; fi
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

# ─── Test harness self-tests ────────────────────────────────────────────────
describe "test harness"

if it "the suite exits non-zero when a syntax error stops it early"; then
    tmp="$(mktemp -d)"
    cp "$ROOT/tests/run-tests.sh" "$tmp/run-tests.sh"
    python3 -c "
import sys
with open(sys.argv[1]) as f:
    lines = f.readlines()
last = -1
for i, line in enumerate(lines):
    if line.rstrip('\n') == 'fi':
        last = i
if last >= 0:
    del lines[last]
    with open(sys.argv[1], 'w') as f:
        f.writelines(lines)
" "$tmp/run-tests.sh"
    bash "$tmp/run-tests.sh" --filter __no_such_test__ >/dev/null 2>&1
    rc=$?
    rm -rf "$tmp"
    if [[ $rc -ne 0 ]]; then pass; else fail "expected non-zero exit, got $rc"; fi
fi

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

if it "image catalog: fedora-workstation's file and sums patterns match the real upstream file names (verified 2026-09-18)"; then
    # Pure regex check against the literal upstream names - no network. The
    # 'file' pattern must match the live ISO name and must NOT match the
    # CHECKSUM sidecar, and vice versa for 'sums'.
    out="$(python3 -c "
import json, re
data = json.load(open('catalog/images.json', encoding='utf-8'))
entry = next(e for e in data['images'] if e['id'] == 'fedora-workstation')
file_re = re.compile(entry['file'])
sums_re = re.compile(entry['sums'])
iso = 'Fedora-Workstation-Live-44-1.7.x86_64.iso'
checksum = 'Fedora-Workstation-44-1.7-x86_64-CHECKSUM'
ok = (bool(file_re.search(iso)) and bool(sums_re.search(checksum))
      and not sums_re.search(iso) and not file_re.search(checksum))
print('OK' if ok else 'FAIL')
")"
    [[ "$out" == "OK" ]] && pass || fail "out=$out"
fi

if it "image catalog: images_validate.py rejects a bad 'leaf' (leading '/', missing trailing '/', or a '..' segment)"; then
    # Scratch catalog only - catalog/images.json itself is never touched by
    # a test (constraint stated at the top of this file).
    tmp="$(mktemp)"
    cat >"$tmp" <<'JSON'
{"images":[
  {"id":"bad-leaf-a","name":"n","homepage":"https://example.org","index":"https://example.org/releases/","leaf":"/Workstation/x86_64/iso/","file":"x$","sums":"SHA256SUMS","kinds":["installer"],"writeMode":"hybrid"},
  {"id":"bad-leaf-b","name":"n","homepage":"https://example.org","index":"https://example.org/releases/","leaf":"Workstation/x86_64/iso","file":"x$","sums":"SHA256SUMS","kinds":["installer"],"writeMode":"hybrid"},
  {"id":"bad-leaf-c","name":"n","homepage":"https://example.org","index":"https://example.org/releases/","leaf":"../etc/","file":"x$","sums":"SHA256SUMS","kinds":["installer"],"writeMode":"hybrid"}
]}
JSON
    out="$(python3 tests/helpers/images_validate.py "$tmp" 2>&1)"; rc=$?
    rm -f "$tmp"
    if [[ $rc -ne 0 && "$out" == *"bad-leaf-a"* && "$out" == *"bad-leaf-b"* && "$out" == *"bad-leaf-c"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
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

if it "engine_validate rejects a malformed engines.json (scratch copy)"; then
    # engine_validate is --check-catalog's dispatch target for the 'engines'
    # catalog type (setup.sh, regression fix). Mutate a scratch copy, never
    # catalog/engines.json itself: 'requires' names a component that exists
    # in no component catalog, and a required field is missing outright.
    tmp="$(mktemp)"
    cat >"$tmp" <<'JSON'
{"engines":[
  {"id":"ghost-engine","name":"Ghost","platforms":["linux"],"kinds":["installer"],"requires":["not-a-real-component"]}
]}
JSON
    out="$(engine_validate "$tmp" catalog 2>&1)"; rc=$?
    rm -f "$tmp"
    if [[ $rc -ne 0 && "$out" == *"missing"* && "$out" == *"interactive"* ]]; then
        pass
    else
        fail "expected a missing-'interactive' problem, got rc=$rc: $out"
    fi
fi

# ─── Shared LLM model catalogue (single source of truth) ──────────────────
describe "llm models"

if it "llm-models.json is valid and every id is unique"; then
    if python3 - <<'PY'
import json, sys
doc = json.load(open("catalog/llm-models.json", encoding="utf-8"))
models = doc["models"]
assert len(models) >= 18, f"expected >= 18 models, got {len(models)}"
ids = [m["id"] for m in models]
dupes = {i for i in ids if ids.count(i) > 1}
assert not dupes, f"duplicate ids: {dupes}"
for m in models:
    assert m.get("openrouter_id") or m.get("direct"), f"{m['id']}: neither openrouter_id nor direct"
    assert isinstance(m["context"], int) and isinstance(m["output"], int), f"{m['id']}: bad windows"
PY
    then pass; else fail "llm-models.json invalid"; fi
fi

if it "the installers project every shared model instead of hardcoding"; then
    # Single-source/DRY: no model id or price may appear as a literal in the
    # installers. Everything is projected from llm-models.json.
    if python3 - <<'PY'
import json, re, sys
models = json.load(open("catalog/llm-models.json", encoding="utf-8"))["models"]
bad = []
for path in ("lib/linux/install.sh", "lib/windows/AutoOS.Install.psm1"):
    src = open(path, encoding="utf-8").read()
    for m in models:
        if m.get("openrouter_id"):
            oid = m["openrouter_id"]
            # projected via _openrouter_models()/Get-OpenRouterModelEntry, never a literal key
            if re.search(r"""['"]""" + re.escape(oid) + r"""['"]\s*[:=]""", src):
                bad.append(f"{path}: hardcoded openrouter id {oid}")
        for price_key in ("paid_input_price", "paid_output_price"):
            if price_key in m and re.search(r"\b" + re.escape(repr(m[price_key])) + r"\b", src):
                bad.append(f"{path}: hardcoded price {m[price_key]} from {m['id']}")
if bad:
    print("\n".join(bad)); sys.exit(1)
PY
    then pass; else fail "installers hardcode shared model data (see above)"; fi
fi

if it "the openhands projector covers every shared model"; then
    if python3 - <<'PY'
import json, re, sys
models = json.load(open("catalog/llm-models.json", encoding="utf-8"))["models"]
for path in ("lib/linux/install.sh", "lib/windows/AutoOS.Install.psm1"):
    src = open(path, encoding="utf-8").read()
    calls = set()
    for call in re.findall(r"_profile_for\(([^)]*)\)", src):
        calls.update(re.findall(r"['\"]([^'\"]+)['\"]", call))
    missing = {m["id"] for m in models if m["id"] not in calls}
    if missing:
        print(f"{path}: unprojected models: {sorted(missing)}"); sys.exit(1)
PY
    then pass; else fail "openhands projector misses a shared model (see above)"; fi
fi

if it "the vendored openhands profiles match the catalog snapshot"; then
    # openhands/profiles/*.json carry a committed copy of the catalog numbers
    # (windows, prices) plus the model id and thinking flags. The installers
    # overlay fresh catalog numbers at setup (catalog wins), but a drifted
    # template would ship stale prices to anyone reading the repo, so any
    # mismatch fails here. api_key is injected at install time and absent.
    if python3 - <<'PY'
import json, glob, os, sys
models = {m["id"]: m for m in json.load(open("catalog/llm-models.json", encoding="utf-8"))["models"]}
# Legacy alias: muse-spark-1.3-contributor.json is the vendored template for
# the muse-spark catalog entry (only the contributor variant is kept).
models["muse-spark-1.3-contributor"] = models["muse-spark"]
price_dst = {"paid_input_price": "paid_input_cost_per_token",
             "paid_output_price": "paid_output_cost_per_token",
             "cache_read_price": "cache_read_cost_per_token"}
files = sorted(glob.glob("openhands/profiles/*.json"))
assert files, "no vendored profiles"
for path in files:
    name = os.path.basename(path)[:-5]
    assert name in models, f"{path}: unknown model {name}"
    m, p = models[name], json.load(open(path, encoding="utf-8"))
    want_model = ("openrouter/" + m["openrouter_id"]) if m.get("openrouter_id") else m["direct"]["model"]
    checks = {"model": want_model, "max_input_tokens": m["context"],
              "max_output_tokens": m["output"], "input_cost_per_token": m["input_price"],
              "output_cost_per_token": m["output_price"],
              "reasoning_effort": ("high" if m.get("reasoning") else "none")}
    if m.get("direct", {}).get("base_url"):
        checks["base_url"] = m["direct"]["base_url"]
    for opt, dst in price_dst.items():
        if m.get(opt) is not None:
            checks[dst] = m[opt]
    for k, v in checks.items():
        assert p.get(k) == v, f"{path}: {k}={p.get(k)!r} != catalog {v!r}"
    assert "api_key" not in p, f"{path}: must not vendor secrets"
    if not m.get("reasoning"):
        assert p.get("enable_encrypted_reasoning") is False, f"{path}: thinking not opted out"
        assert p.get("extended_thinking_budget") is None, f"{path}: thinking budget not nulled"
PY
    then pass; else fail "vendored openhands profiles drifted from catalog (see above)"; fi
fi

if it "resolve_ollama_base_url: OLLAMA_BASE_URL wins and is normalised to /v1"; then
    got="$(curl() { return 1; }; OLLAMA_BASE_URL="http://gpu-box:11434/" resolve_ollama_base_url)"
    assert_eq "$got" "http://gpu-box:11434/v1"
fi

if it "resolve_ollama_base_url: host.docker.internal is chosen only when Ollama answers there"; then
    got="$(unset OLLAMA_BASE_URL; curl() { [[ "$*" == *host.docker.internal:11434/api/version* ]]; }; resolve_ollama_base_url)"
    assert_eq "$got" "http://host.docker.internal:11434/v1"
fi

if it "resolve_ollama_base_url: a native Linux host (no host.docker.internal) keeps the catalog default"; then
    # Empty output = "use the catalog's 127.0.0.1", which is what a host-run
    # agent-canvas (agent-server via uvx on the host) can reach.
    got="$(unset OLLAMA_BASE_URL; curl() { return 6; }; resolve_ollama_base_url)"
    assert_eq "$got" ""
fi

if it "setup_openhands_config writes the resolved Ollama address into the profile and the keyless default"; then
    # Skipped in ambient-key environments: the repo's own api-keys.yml (read
    # as fallback when no env key exists) plus the WSL2 host env leak a
    # gateway key in, so the "keyless" default legitimately becomes the
    # gateway tier here. The hermetic key-behavior is covered by the
    # dedicated "gateway tier profiles with a key, none without" test below,
    # which unsets the gateway keys too.
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" AUTOOS_KEYS_FILE="$tmp/nonexistent-keys.yml" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
d = sys.argv[1]
p = json.load(open(os.path.join(d, "profiles", "ollama-qwen2.5-coder.json"), encoding="utf-8"))
s = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))
print(p["base_url"], p["model"], s["agent_settings"]["llm"]["base_url"], s["agent_settings"]["llm"]["model"], "api_key" in p and p["api_key"] is None, any("timeout" in v for v in s["agent_settings"]["mcp_config"].values()))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "http://ollama:11434 ollama_chat/qwen2.5-coder:7b http://ollama:11434 ollama_chat/qwen2.5-coder:7b True False"
    # NOTE: keyless expectation vs ambient machine key — under WSL2 bash the
    # surrounding env leaks in (see the --wsl suite note).
fi

if it "setup_openhands_config defaults to the gateway with a key"; then
    # Same hermetic shape as the Ollama test. Env key wins over the repo's
    # real api-keys.yml (the suite never asserts on live system state).
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" AUTOOS_OMNIROUTE_KEY="test-gw-key" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, os, sys
d = sys.argv[1]
s = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))
llm = s["agent_settings"]["llm"]
print(llm["model"], llm["base_url"], llm["api_key"], llm["reasoning_effort"])
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "openai/t1-orchestrator http://host.docker.internal:20128/v1 test-gw-key high"
fi

if it "setup_openhands_config writes gateway tier profiles with a key, none without"; then
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" AUTOOS_OMNIROUTE_KEY="test-omni-key" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, os, sys
d = sys.argv[1]
t1 = json.load(open(os.path.join(d, "profiles", "omniroute-t1-orchestrator.json"), encoding="utf-8"))
t3 = json.load(open(os.path.join(d, "profiles", "omniroute-t3-driver.json"), encoding="utf-8"))
lp = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))["llm_profiles"]
print(t1["model"], t1["base_url"], t1["api_key"], t1["reasoning_effort"],
      t3["model"], t3["reasoning_effort"], t3["enable_encrypted_reasoning"],
      lp["active"], "omniroute-t1-orchestrator" in lp["profiles"])
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "openai/t1-orchestrator http://host.docker.internal:20128/v1 test-omni-key high openai/t3-driver none False omniroute-t1-orchestrator True"
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" setup_openhands_config >/dev/null 2>&1
        test -e "$tmp/.openhands/profiles/omniroute-t1-orchestrator.json" && echo PRESENT || echo ABSENT
    )"
    rm -rf "$tmp"
    assert_eq "$out" "ABSENT"
fi

if it "setup_openhands_config writes litellm fallback tiers with a litellm key only"; then
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY AUTOOS_OMNIROUTE_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" LITELLM_MASTER_KEY="test-lit-key" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, os, sys
d = sys.argv[1]
t1 = json.load(open(os.path.join(d, "profiles", "litellm-t1-orchestrator.json"), encoding="utf-8"))
lp = json.load(open(os.path.join(d, "settings.json"), encoding="utf-8"))["llm_profiles"]
print(t1["model"], t1["base_url"], t1["api_key"], lp["active"],
      os.path.exists(os.path.join(d, "profiles", "omniroute-t1-orchestrator.json")))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "openai/t1-orchestrator http://host.docker.internal:4000/v1 test-lit-key litellm-t1-orchestrator False"
fi

if it "tier profiles come from the spec, installer and tool agree"; then
    report="$(python3 - <<'PY'
import json
spec = json.load(open("configuration/openhands/tier-profiles.json", encoding="utf-8"))
print("%s|%s|%s|%s" % (
    ",".join(t["id"] for t in spec["tiers"]),
    spec["gateway_base_url"],
    spec["litellm_base_url"],
    ",".join(t["model"] for t in spec["tiers"])))
PY
)"
    # Pinned on purpose: the order is the OpenHands push priority (the app
    # keeps 10 profiles), so a reorder must be a deliberate, reviewed edit.
    assert_eq "$report" "omniroute-t1-orchestrator,omniroute-t2-worker,omniroute-t3-driver,omniroute-t2-orchestrator,omniroute-t2-worker-clean,omniroute-t3-driver-clean,omniroute-t4-rag,omniroute-opus-4-6,omniroute-gemini-3.8-flash,omniroute-t2-worker-free-only,omniroute-deepseek-v4.1-flash,omniroute-t3-driver-free-only,omniroute-t1-orchestrator-clean,omniroute-spark-1.3-contributor,openrouter-muse-spark-1.3-contributor,litellm-t1-orchestrator,litellm-t2-worker,litellm-t3-driver,litellm-t2-worker-free-only,litellm-t3-driver-free-only,litellm-t1-orchestrator-free-only,omniroute-t1-orchestrator-free-only|http://host.docker.internal:20128/v1|http://host.docker.internal:4000/v1|openai/t1-orchestrator,openai/t2-worker,openai/t3-driver,openai/t2-orchestrator,openai/t2-worker-clean,openai/t3-driver-clean,openai/t4-rag,openai/opus-4-6,openai/gemini-3.8-flash,openai/t2-worker-free-only,openai/deepseek-v4.1-flash,openai/t3-driver-free-only,openai/t1-orchestrator-clean,openai/spark-1.3-contributor,openrouter/meta/muse-spark-1.3-contributor,openai/t1-orchestrator,openai/t2-worker,openai/t3-driver,openai/t2-worker-free-only,openai/t3-driver-free-only,openai/t1-orchestrator-free-only,openai/t1-orchestrator-free-only"
    # The embedded installer must read the spec, never inline tiers.
    grep -q 'tier-profiles.json' lib/linux/install.sh || { fail "installer does not read the tier spec"; }
    # Generator round-trip with fixture keys (env hidden: the suite never
    # asserts on live system state).
    tmp="$(mktemp -d)"; printf 'omniroute: test-omni-key\nopenrouter: test-or-key\n' >"$tmp/api-keys.yml"
    printf 'LITELLM_MASTER_KEY=test-lit-key\n' >"$tmp/.env"
    out="$( ( unset AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY OPENROUTER_API_KEY; python3 tools/sync-openhands-profiles.py --openhands-dir "$tmp" --keys-file "$tmp/api-keys.yml" --litellm-env "$tmp/.env" ) 2>&1)"
    written="$(printf '%s' "$out" | grep -c written)"
    # A direct-provider tier (gateway: openrouter) must take its OWN key and
    # endpoint, not the gateway's - that is the effort-ladder surface.
    direct="$(python3 - "$tmp/profiles/openrouter-muse-spark-1.3-contributor.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except OSError:
    print("missing")
else:
    print("%s|%s|%s" % (d.get("api_key"), d.get("base_url"), d.get("model")))
PY
)"
    rm -rf "$tmp"
    assert_eq "$written" "22"
    assert_eq "$direct" "test-or-key|https://openrouter.ai/api/v1|openrouter/meta/muse-spark-1.3-contributor"
fi

if it "start-stack regenerates tier profiles on openhands start"; then
    ok=1
    grep -q 'sync-openhands-profiles' configuration/start-stack.ps1 || { ok=0; echo "ps1 never syncs" >&2; }
    grep -q 'sync-openhands-profiles' configuration/start-stack.sh || { ok=0; echo "sh never syncs" >&2; }
    if (( ok )); then pass; else fail "tier profiles go stale between installs"; fi
fi

describe "Serena tool exclusions (serena)"

# tests/fixtures/serena/<case>.yml -> <case>.expected.yml is the ONE set of
# YAML shapes both ensure_serena_exclusions (here) and Set-AutoOSSerenaExclusions
# (tests/run-tests.ps1) are graded against, so the two implementations cannot
# quietly drift apart. Comparison is byte-for-byte (cmp), not line-based.
for f in tests/fixtures/serena/*.yml; do
    case "$f" in *.expected.yml) continue ;; esac
    base="${f%.yml}"
    name="$(basename "$base")"
    exp="${base}.expected.yml"

    if it "ensure_serena_exclusions matches fixture '$name' byte-for-byte (serena)"; then
        tmp="$(mktemp -d)"
        cp "$f" "$tmp/config.yml"
        ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/config.yml" ) >/dev/null 2>&1
        if cmp -s "$tmp/config.yml" "$exp"; then
            pass
        else
            fail "$(diff -u "$exp" "$tmp/config.yml" 2>&1 | head -20)"
        fi
        rm -rf "$tmp"
    fi

    if it "ensure_serena_exclusions second run on fixture '$name' skips and writes nothing (serena)"; then
        tmp="$(mktemp -d)"
        cp "$f" "$tmp/config.yml"
        ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/config.yml" ) >/dev/null 2>&1
        sum1="$(sha256sum "$tmp/config.yml" | awk '{print $1}')"
        out2="$( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/config.yml" 2>&1 )"
        sum2="$(sha256sum "$tmp/config.yml" | awk '{print $1}')"
        nbackups="$(find "$tmp" -maxdepth 1 -name '*.autoos-backup-*' | wc -l | tr -d ' ')"
        if [[ "$out2" == *skipped* && "$sum1" == "$sum2" && "$nbackups" == "1" ]]; then
            pass
        else
            fail "out2=[$out2] sum_changed=$([[ "$sum1" == "$sum2" ]] && echo no || echo yes) nbackups=$nbackups"
        fi
        rm -rf "$tmp"
    fi
done

if it "ensure_serena_exclusions skips on the first run when all 15 are already present, any order (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    cat >"$cfg" <<'EOF'
excluded_tools:
- delete_memory
- create_text_file
- read_file
- execute_shell_command
- list_dir
- search_for_pattern
- find_file
- replace_content
- replace_in_files
- onboarding
- write_memory
- read_memory
- list_memories
- edit_memory
- rename_memory
EOF
    orig_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    out="$( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" 2>&1 )"
    new_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    nbackups="$(find "$tmp" -maxdepth 1 -name '*.autoos-backup-*' | wc -l | tr -d ' ')"
    if [[ "$out" == *skipped* && "$orig_sum" == "$new_sum" && "$nbackups" == "0" ]]; then
        pass
    else
        fail "out=$out changed=$([[ "$orig_sum" == "$new_sum" ]] && echo no || echo yes) nbackups=$nbackups"
    fi
    rm -rf "$tmp"
fi

# Defect: an earlier version captured python's stdout with `result="$(...)"`
# and blindly printf'd it over the config, so a directory at config_path (or
# a missing python3) silently truncated it to a stray newline. Both must now
# warn, return 0, and leave the target completely untouched.
if it "ensure_serena_exclusions on a directory path warns, returns 0, and writes nothing (serena)"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/adir"
    out="$( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$tmp/adir" 2>&1 )"; rc=$?
    nbackups="$(find "$tmp" -maxdepth 1 -name '*.autoos-backup-*' | wc -l | tr -d ' ')"
    ninside="$(find "$tmp/adir" -mindepth 1 | wc -l | tr -d ' ')"
    if [[ "$rc" == "0" && -d "$tmp/adir" && "$nbackups" == "0" && "$ninside" == "0" ]]; then
        pass
    else
        fail "rc=$rc nbackups=$nbackups ninside=$ninside out=$out"
    fi
    rm -rf "$tmp"
fi

if it "ensure_serena_exclusions without python3 on PATH warns, returns 0, and writes nothing (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf 'other_key: 1\n' >"$cfg"
    orig_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    out="$( has_cmd() { [[ "$1" != "python3" ]] && command -v "$1" >/dev/null 2>&1; }
            SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
            ensure_serena_exclusions "$cfg" 2>&1 )"; rc=$?
    new_sum="$(sha256sum "$cfg" | awk '{print $1}')"
    if [[ "$rc" == "0" && "$new_sum" == "$orig_sum" ]]; then
        pass
    else
        fail "rc=$rc changed=$([[ "$orig_sum" == "$new_sum" ]] && echo no || echo yes) out=$out"
    fi
    rm -rf "$tmp"
fi

# CRLF and a raw non-UTF-8 byte can't live in a committed *.yml fixture -
# .gitattributes forces `*.yml text eol=lf`, which would silently rewrite a
# checked-in CRLF fixture to LF and defeat the point of the test. These two
# build their own bytes at run time instead.
if it "ensure_serena_exclusions preserves CRLF line endings and untouched content byte-for-byte (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf '# top comment\r\nother_key: 1\r\nexcluded_tools:\r\n- read_file\r\n- my_tool\r\nlast_key: x\r\n' >"$cfg"
    ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" ) >/dev/null 2>&1
    cr="$(tr -cd '\r' < "$cfg" | wc -c | tr -d ' ')"
    lf="$(tr -cd '\n' < "$cfg" | wc -c | tr -d ' ')"
    first_line="$(head -1 "$cfg" | tr -d '\r\n')"
    if [[ "$cr" == "$lf" && "$cr" -gt "0" && "$first_line" == "# top comment" ]]; then
        pass
    else
        fail "cr=$cr lf=$lf first_line=[$first_line]"
    fi
    rm -rf "$tmp"
fi

if it "ensure_serena_exclusions keeps each line's own ending outside the edited block (mixed CRLF/LF) (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf 'a_setting: 1\nb_setting: 2\r\nexcluded_tools:\r\n- read_file\r\ntrailer_key: keep_me\n' >"$cfg"
    ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" ) >/dev/null 2>&1
    if out="$(python3 - "$cfg" <<'PY' 2>&1
import sys
data = open(sys.argv[1], 'rb').read()
assert data.startswith(b'a_setting: 1\nb_setting: 2\r\nexcluded_tools:'), data
assert data.endswith(b'\ntrailer_key: keep_me\n') and not data.endswith(b'\r\n'), data
assert b'- delete_memory' in data, data
PY
)"; then pass; else fail "$out"; fi
    rm -rf "$tmp"
fi

if it "ensure_serena_exclusions preserves a non-UTF-8 byte outside the block untouched (serena)"; then
    tmp="$(mktemp -d)"
    cfg="$tmp/serena_config.yml"
    printf 'excluded_tools:\n- read_file\n#comment with byte: ' >"$cfg"
    printf '\xe9' >>"$cfg"
    printf '\nlast_key: x\n' >>"$cfg"
    ( SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0; ensure_serena_exclusions "$cfg" ) >/dev/null 2>&1
    if python3 -c "
data = open('$cfg', 'rb').read()
assert b'#comment with byte: \xe9\nlast_key: x\n' in data, data
"
    then pass; else fail "the non-UTF-8 byte (or its surrounding bytes) did not survive untouched"; fi
    rm -rf "$tmp"
fi

if it "check-serena-tools has a SERENA_FROM constant used by the uvx fallback (serena)"; then
    if python3 - <<'PY'
import importlib.util
import json
import unittest.mock as mock

spec = importlib.util.spec_from_file_location("check_serena_tools", "tools/check-serena-tools.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

expected = json.load(open("catalog/agent-harness.json", encoding="utf-8"))["mcp_servers"]["serena"]["package"]
assert mod.SERENA_FROM == expected, (mod.SERENA_FROM, expected)
with mock.patch("shutil.which", return_value=None):
    cmd = mod.default_command()
assert cmd[:3] == ["uvx", "--from", mod.SERENA_FROM], cmd
PY
    then pass; else fail "SERENA_FROM constant missing, or not used by the uvx fallback"; fi
fi

if it "check-serena-tools judge() rejects memory/onboarding tools and requires find_symbol (serena)"; then
    if python3 - <<'PY'
import importlib.util, sys

spec = importlib.util.spec_from_file_location("check_serena_tools", "tools/check-serena-tools.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ok, problems = mod.judge(["find_symbol", "read_file"])
assert ok, f"expected ok, got problems: {problems}"

ok, problems = mod.judge(["find_symbol", "write_memory"])
assert not ok, "write_memory should have failed judge()"

# Regression: MEMORY_MARKER = "memory" does not match "memories", so
# list_memories - one of Serena's own tool names - slipped past judge().
ok, problems = mod.judge(["find_symbol", "list_memories"])
assert not ok, "list_memories should have failed judge()"

ok, problems = mod.judge(["read_file"])
assert not ok, "missing find_symbol should have failed judge()"
sys.exit(0)
PY
    then pass; else fail "judge() did not behave as expected (see above)"; fi
fi

if it "setup_openhands_config pins every MCP server it writes"; then
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
m = json.load(open(os.path.join(sys.argv[1], "settings.json"), encoding="utf-8"))["agent_settings"]["mcp_config"]
# Expected specs come from the harness, the one source of truth for the pins.
pins = {k: v["package"] for k, v in json.load(open("catalog/agent-harness.json", encoding="utf-8"))["mcp_servers"].items()}
print(" ".join(k for k, v in pins.items() if v not in m[k]["args"]) or "all-pinned")
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "all-pinned"
fi

if it "setup_openhands_config hands omnigraph the token from the per-user env file"; then
    # The agent-server may run in a container or under systemd, neither of
    # which sees a token exported from an interactive shell rc.
    tmp="$(mktemp -d)"
    printf 'OMNIGRAPH_BASE_URL=http://localhost:8080\nOMNIGRAPH_TOKEN=file-token\n' >"$tmp/.autoos-omnigraph.env"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY OMNIGRAPH_TOKEN
        curl() { return 6; }
        setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
e = json.load(open(os.path.join(sys.argv[1], "settings.json"), encoding="utf-8"))["agent_settings"]["mcp_config"]["omnigraph"]["env"]
print(e.get("OMNIGRAPH_TOKEN"), e.get("OMNIGRAPH_GRAPH_ID"))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "file-token autoos"
fi

if it "setup_openhands_config points the omnigraph bridge at the host, not at the container itself"; then
    # OpenHands runs in a container (compose: extra_hosts host.docker.internal:host-gateway),
    # so localhost:8080 there is the container and nothing listens on it. The
    # published omnigraph-server is reached through the host alias, like the
    # gateway URL the same file already carries (llm.base_url).
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY OMNIGRAPH_TOKEN
        curl() { return 6; }
        setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, sys, os
e = json.load(open(os.path.join(sys.argv[1], "settings.json"), encoding="utf-8"))["agent_settings"]["mcp_config"]["omnigraph"]["env"]
print(e.get("OMNIGRAPH_BASE_URL"))
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "http://host.docker.internal:8080"
fi

describe "mcp pins"

if it "mcp pins: lib/ carries no floating package spec"; then
    bad=""
    for f in lib/windows/*.psm1 lib/linux/*.sh; do
        for needle in 'serena-agent' 'graphifyy[mcp]' '@playwright/mcp' '@upstash/context7-mcp' '@modernrelay/omnigraph-mcp'; do
            if grep -qF -- "$needle" "$f"; then bad="$f: $needle"; break 2; fi
        done
    done
    if [[ -z "$bad" ]]; then pass; else fail "floating package name found in $bad"; fi
fi

if it "mcp pins: no pinned version string appears under lib/"; then
    bad="$(python3 - <<'PY'
import glob, json, os, re
h = json.load(open("catalog/agent-harness.json", encoding="utf-8"))
versions = []
for server in h["mcp_servers"].values():
    match = re.search(r"@([0-9][^@]*)$", server["package"]) or re.search(r"==(.+)$", server["package"])
    if match:
        versions.append(match.group(1))
bad = []
for path in glob.glob("lib/**/*", recursive=True):
    if not os.path.isfile(path):
        continue
    text = open(path, encoding="utf-8", errors="replace").read()
    for version in versions:
        if version in text:
            bad.append("%s: %s" % (path, version))
print("; ".join(bad))
PY
)"
    if [[ -z "$bad" ]]; then pass; else fail "pinned version found: $bad"; fi
fi

if it "mcp pins: the client excludeTools list equals serena.excluded_tools"; then
    tmp="$(mktemp -d)"
    spec_file="$tmp/spec.json"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        has_cmd() { return 1; }
        register_mcp_server() { return 0; }
        ensure_serena_exclusions() { return 0; }
        register_antigravity_mcp_server() { printf '%s' "$2" >"$spec_file"; }
        install_mcp_serena >/dev/null 2>&1
    )
    out="$(python3 - "$spec_file" <<'PY'
import json, sys
spec = json.load(open(sys.argv[1], encoding="utf-8"))
want = json.load(open("catalog/agent-harness.json", encoding="utf-8"))["mcp_servers"]["serena"]["excluded_tools"]
print("match" if spec.get("excludeTools") == want else "mismatch: %r" % spec.get("excludeTools"))
PY
)"
    rm -rf "$tmp"
    assert_eq "$out" "match"
fi

if it "vendored agent profiles reference existing llm profiles"; then
    if python3 - <<'PY'
import json, glob, os, sys
llm = {os.path.basename(p)[:-5] for p in glob.glob("openhands/profiles/*.json")}
# Gateway tier ids are generated at install/start time from the tier spec,
# not vendored here - but agent profiles may reference them.
spec = json.load(open("configuration/openhands/tier-profiles.json", encoding="utf-8"))
llm |= {t["id"] for t in spec["tiers"]}
for path in sorted(glob.glob("openhands/agent-profiles/*.json")):
    ref = json.load(open(path, encoding="utf-8")).get("llm_profile_ref")
    if ref is not None:
        assert ref in llm, f"{path}: llm_profile_ref {ref!r} has no vendored profile"
PY
    then pass; else fail "agent profile references missing llm profile (see above)"; fi
fi

if it "agent harness installers: the OpenHands writer calls the generator and skips role profiles"; then
    body="$(declare -f setup_openhands_config)"
    if [[ "$body" == *'agent_harness.py" openhands'* || "$body" == *'agent_harness.py openhands'* ]] \
        && [[ "$body" == *'_role_profiles'* ]]; then
        pass
    else
        fail "setup_openhands_config does not call agent_harness.py openhands and skip role profiles"
    fi
fi

if it "opencode user config carries global gateway providers without secrets"; then
    # Tier routing must work in EVERY cwd, not just checkouts carrying the
    # repo opencode.jsonc: the user config carries omniroute/litellm
    # providers with {env:} key placeholders (never values).
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report="$(python3 - 2>&1 "$scratch/.config/opencode/config.json" <<'PY'
import io, json, re, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
p = d.get("provider", {})
problems = []
omni = p.get("omniroute", {})
if omni.get("options", {}).get("baseURL") != "http://127.0.0.1:20128/v1":
    problems.append("omni-base")
lit = p.get("litellm", {})
if lit.get("options", {}).get("baseURL") != "http://127.0.0.1:4000/v1":
    problems.append("lit-base")
for name, prov in (("omniroute", omni), ("litellm", lit)):
    key = (prov.get("options") or {}).get("apiKey", "")
    if not (key.startswith("{env:") and key.endswith("}")):
        problems.append(name + "-key-not-placeholder")
for m in ("t1-orchestrator", "t1-orchestrator-clean", "t2-worker", "t3-driver-clean", "auto/smart"):
    if m not in (omni.get("models") or {}):
        problems.append("missing:" + m)
if "t2-worker" not in (lit.get("models") or {}):
    problems.append("missing:lit-t2-worker")
if "deepseek" in p:
    problems.append("resurrected:deepseek")
meta = p.get("meta", {})
if not (meta.get("options", {}).get("apiKey", "") == "{env:META_API_KEY}"):
    problems.append("meta-key-not-placeholder")
if not (meta.get("models") or {}).keys() == {"muse-spark-1.3-contributor"}:
    problems.append("meta-models:%s" % sorted((meta.get("models") or {}).keys()))
raw = io.open(sys.argv[1], encoding="utf-8-sig").read()
if re.search(r"sk-[A-Za-z0-9]{10,}", raw):
    problems.append("secret-leak")
print(" ".join(problems))
PY
)"
    # The second run changes nothing, so it backs up nothing (a backup is
    # taken only when a run actually changes the file).
    backups="$(ls "$scratch"/.config/opencode/config.json.autoos-backup-* 2>/dev/null | wc -l)"
    rm -rf "$scratch"
    assert_eq "$report" ""
    assert_eq "$backups" "0"
    fi
fi

# AGENTS.md §4 on an EXISTING user config with the harness really applied
# (AUTOOS_ROOT set, V2 projection on). Measured 2026-09-25: run 1 merged,
# run 2 "merged" again with a fresh backup, run 3 was current - the merge
# wrote non-ASCII names as \uXXXX escapes while the harness wrote them as
# UTF-8, so the second run's byte compare always saw a change.
if it "opencode user config: the second run over an existing config writes nothing (double run)"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    printf '{"model": "anthropic/mine"}\n' >"$scratch/.config/opencode/opencode.json"
    printf '{"model": "anthropic/mine"}\n' >"$scratch/.config/opencode/config.json"
    _oc_run() {
        ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0 AUTOOS_ROOT="$ROOT"
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          opencode_is_v2() { return 0; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config 2>&1 )
    }
    # name, size, mtime_ns and sha256 of every entry: "writes nothing" means
    # not even a byte-identical rewrite, and no new backup file.
    _oc_snapshot() {
        python3 - "$scratch/.config/opencode" <<'PY'
import hashlib, os, sys
root = sys.argv[1]
for name in sorted(os.listdir(root)):
    path = os.path.join(root, name)
    st = os.lstat(path)
    digest = ""
    if os.path.isfile(path) and not os.path.islink(path):
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    print(name, st.st_size, st.st_mtime_ns, digest)
PY
    }
    first="$(_oc_run)"
    before="$(_oc_snapshot)"
    sleep 1
    second="$(_oc_run)"
    after="$(_oc_snapshot)"
    rm -rf "$scratch"
    ok=1
    [[ "$first" == *"agent-harness opencode: updated"* ]] || { ok=0; echo "harness never ran (the precondition): $first" >&2; }
    [[ "$before" == "$after" ]] || { ok=0; printf 'second run changed files:\n%s\n' "$(diff <(printf '%s\n' "$before") <(printf '%s\n' "$after"))" >&2; }
    [[ "$second" == *"merged"* || "$second" == *"written"* ]] && { ok=0; echo "second run reports a write: $second" >&2; }
    [[ "$(grep -c 'already current' <<<"$second")" == 2 ]] || { ok=0; echo "second run not current for both files: $second" >&2; }
    if (( ok )); then pass; else fail "setup_opencode_config needs two runs to converge"; fi
    fi
fi

if it "agent harness installers: the OpenCode writer calls the generator"; then
    body="$(declare -f setup_opencode_config)"
    hands="$(declare -f setup_openhands_config)"
    if { [[ "$body" == *'agent_harness.py" opencode'* ]] || [[ "$body" == *'agent_harness.py opencode'* ]]; } \
        && declare -F autoos_skills_source >/dev/null \
        && [[ "$body" == *'autoos_skills_source'* ]] \
        && [[ "$hands" == *'autoos_skills_source'* ]]; then
        pass
    else
        fail "setup_opencode_config does not call agent_harness.py opencode and use autoos_skills_source"
    fi
fi

if it "agent harness: the generator's unit tests pass"; then
    if ! has_cmd python3; then
        skip "python3 not found"
    else
        out="$(python3 tests/test_agent_harness.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
    fi
fi

if it "serena memory tools off from harness field (serena)"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
text = io.open("opencode.jsonc", encoding="utf-8").read()
text = re.sub(r"(?m)^\s*//.*$", "", text)
oc = json.loads(text)
h = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))
mem = h["mcp_servers"]["serena"]["memory_tools"]
tools = oc.get("tools", {})
want = {"serena_" + t: False for t in mem}
problems = []
if tools != want:
    problems.append("tools-mismatch:%s" % sorted(tools))
body = io.open("lib/linux/install.sh", encoding="utf-8").read()
if "memory_tools" not in body:
    problems.append("writer-no-harness-field")
for t in mem:
    if "'serena_%s'" % t in body or '"serena_%s"' % t in body:
        problems.append("writer-literal:" + t)
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
    # Functional: the writer emits the tools block from the harness field.
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report2="$(python3 - "$scratch/.config/opencode/config.json" <<'PY'
import json, io, sys
d = json.load(io.open(sys.argv[1], encoding="utf-8-sig"))
h = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))
mem = h["mcp_servers"]["serena"]["memory_tools"]
tools = d.get("tools", {})
want = {"serena_" + t: False for t in mem}
print("ok" if tools == want else "mismatch:%s" % sorted(tools))
PY
)"
    rm -rf "$scratch"
    assert_eq "$report2" "ok"
    fi
fi

if it "zed default_model converges litellm to omniroute (zed routing)"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    printf '{"agent":{"default_model":{"provider":"autoos-litellm","model":"t3-driver-paid"}}}' >"$scratch/.config/zed/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="k1" AUTOOS_LITELLM_API_KEY="k2" route_zed_to_proxy >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
dm = cfg.get("agent", {}).get("default_model", {})
print("%s|%s" % (dm.get("provider"), dm.get("model")))
PY
)"
    # A default already on omniroute must survive untouched.
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.config/zed"
    printf '{"agent":{"default_model":{"provider":"autoos-omniroute","model":"t2-worker"}}}' >"$scratch2/.config/zed/settings.json"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="k1" AUTOOS_LITELLM_API_KEY="k2" route_zed_to_proxy >/dev/null 2>&1 )
    report2="$(python3 - "$scratch2/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
dm = cfg.get("agent", {}).get("default_model", {})
print("%s|%s" % (dm.get("provider"), dm.get("model")))
PY
)"
    rm -rf "$scratch" "$scratch2"
    assert_eq "$report" "autoos-omniroute|t1-orchestrator"
    assert_eq "$report2" "autoos-omniroute|t2-worker"
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

if it "local-ai profile ships oterm, pre-ticked and requiring ollama"; then
    # Same exact-id-match guard as the "ships ollama" test above — a bare
    # substring check would pass vacuously.
    got=" $(catalog_profile_defaults local-ai) "
    if [[ "$got" != *" oterm "* ]]; then
        fail "oterm not in local-ai defaults: [$got]"
    else
        reqs="$(python3 -c "
import json
cat=json.load(open('catalog/linux.json'))
c=[c for g in cat['categories'] for c in g['components'] if c['id']=='oterm'][0]
print(' '.join(c.get('requires', [])))")"
        [[ "$reqs" == "ollama" ]] && pass || fail "oterm requires: [$reqs], expected exactly 'ollama'"
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

if it "detects Debian-like distributions"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/etc"
    cat >"$tmp/etc/os-release" <<'EOF'
ID=ubuntu
PRETTY_NAME="Ubuntu 22.04 LTS"
VERSION_ID="22.04"
VERSION_CODENAME="jammy"
ID_LIKE="debian"
EOF
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_DISTRO_ID" == "ubuntu" && "$SYS_DISTRO_NAME" == "Ubuntu 22.04 LTS" && "$SYS_IS_DEBIAN_LIKE" -eq 1 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect ubuntu as debian-like"
    rm -rf "$tmp"
fi

if it "detects WSL2 environment"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/proc/sys/kernel" "$tmp/run"
    cat >"$tmp/proc/version" <<'EOF'
Linux version 5.15.90.1-microsoft-standard-WSL2 (oe-user@oe-host) (x86_64-msft-linux-gcc (GCC) 9.3.0, GNU ld (GNU Binutils) 2.34.0.20200220) #1 SMP Fri Jan 27 02:56:13 UTC 2023
EOF
    cat >"$tmp/proc/sys/kernel/osrelease" <<'EOF'
5.15.90.1-microsoft-standard-WSL2
EOF
    mkdir -p "$tmp/run/WSL"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_IS_WSL" -eq 1 && "$SYS_WSL_VERSION" == "2" ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect WSL2"
    rm -rf "$tmp"
fi

if it "detects Raspberry Pi"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/proc/device-tree"
    echo -n "Raspberry Pi 4 Model B Rev 1.2" > "$tmp/proc/device-tree/model"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_IS_PI" -eq 1 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect Raspberry Pi"
    rm -rf "$tmp"
fi

if it "detects Docker container"; then
    tmp="$(mktemp -d)"
    touch "$tmp/.dockerenv"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        detect_system
        if [[ "$SYS_IS_CONTAINER" -eq 1 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to detect Docker container"
    rm -rf "$tmp"
fi

if it "detects non-Debian distributions"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/etc"
    cat >"$tmp/etc/os-release" <<'EOF'
ID=alpine
PRETTY_NAME="Alpine Linux v3.18"
VERSION_ID="3.18.2"
EOF
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        ID=""
        NAME=""
        ID_LIKE=""
        detect_system
        if [[ "$SYS_DISTRO_ID" == "alpine" && "$SYS_DISTRO_NAME" == "Alpine Linux v3.18" && "$SYS_IS_DEBIAN_LIKE" -eq 0 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to correctly detect alpine as non-debian-like"
    rm -rf "$tmp"
fi

if it "handles missing os-release gracefully"; then
    tmp="$(mktemp -d)"
    (
        SYS_ROOT="$tmp"
        uname() { echo "Linux"; }
        ID=""
        NAME=""
        ID_LIKE=""
        detect_system
        if [[ "$SYS_DISTRO_ID" == "unknown" && "$SYS_DISTRO_NAME" == "unknown" && "$SYS_IS_DEBIAN_LIKE" -eq 0 ]]; then
            true
        else false; fi
    ) && pass || fail "failed to gracefully handle missing os-release"
    rm -rf "$tmp"
fi

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

if it "append_line_once joins multi-word lines and adds the AutoOS header"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "M4" "export" "FOO=1" "# M4" >/dev/null
    out="$(cat "$tmp")"
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
    if [[ "$out" == $'\n# added by AutoOS\nexport FOO=1 # M4' ]]; then pass
    else fail "unexpected content: $(printf '%q' "$out")"; fi
fi

if it "append_line_once prevents duplicate lines on multiple calls"; then
    tmp="$(mktemp)"; rm -f "$tmp"
    AUTOOS_DRY_RUN=0
    append_line_once "$tmp" "M_DUP" "line 1 # M_DUP" >/dev/null
    append_line_once "$tmp" "M_DUP" "line 1 # M_DUP" >/dev/null
    append_line_once "$tmp" "M_DUP" "line 1 # M_DUP" >/dev/null
    out="$(cat "$tmp")"
    rm -f "$tmp" "$tmp".autoos-backup-* 2>/dev/null
    if [[ "$out" == $'\n# added by AutoOS\nline 1 # M_DUP' ]]; then pass
    else fail "duplicate lines found: $(printf '%q' "$out")"; fi
fi

if it "append_line_once creates missing parent directories"; then
    tmp="$(mktemp -d)"
    target="$tmp/missing/dir/file"
    AUTOOS_DRY_RUN=0
    append_line_once "$target" "M5" "line  # M5" >/dev/null
    if [[ -f "$target" ]]; then pass
    else fail "parent directories or file were not created"; fi
    rm -rf "$tmp"
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
    out="$(bash setup.sh --profile workstation --dry-run --yes --no-color 2>&1)"; rc=$?
    executed="$(printf '%s' "$out" | grep -c '^run:' || true)"
    planned="$(printf '%s' "$out" | grep -c 'would ' || true)"
    # rc too: a prompt the dry run never asks must not fail it (review 2026-09-25).
    if [[ "$rc" -eq 0 && "$executed" -eq 0 && "$planned" -gt 0 ]]; then pass
    else fail "rc=$rc executed=$executed planned=$planned (expected rc 0, 0 executed, >0 planned)"; fi
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

if it "--check-catalog validates all five catalogs by type, not just component catalogs"; then
    # Regression: setup.sh used to run every catalog/*.json through the
    # component-catalog validator, which rejects images.json and
    # engines.json outright (they have no 'categories' key). Assert every
    # file is actually reported valid, not just that the overall rc is 0 —
    # rc could go green for the wrong reason (e.g. an empty glob).
    out="$(bash setup.sh --check-catalog 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"engines.json is valid"* && "$out" == *"images.json is valid"* \
        && "$out" == *"linux.json is valid"* && "$out" == *"macos.json is valid"* && "$out" == *"windows.json is valid"*         && "$out" == *"agent-harness.json is valid"* \
        && "$out" == *"providers.json is valid"* && "$out" == *"ai-registry.json is valid"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
fi

if it "catalog_probe_installed identifies installed components"; then
    # is_installed() (lib/linux/install.sh) delegates to detect_installed_status()
    # (lib/linux/detect.sh), which for a package-manager provider runs
    # `python3 - <provider> <package> <cask>` and has THAT python3 process
    # shell out further to dpkg-query/snap/brew/npm to query the host's real
    # package database. Asserting against the real git/dpkg on this machine
    # only passes on a host with dpkg - and a stub further down that chain
    # (e.g. a fake dpkg-query) does not help either: this suite also runs on
    # Git Bash on Windows, where python3 is a native Windows build whose
    # subprocess calls cannot invoke an extension-less shebang script or a
    # .cmd stub without a real dpkg-query to fall back to. So, same style as
    # the rescue-bootstrap sandbox's stateful python3 stub below (BS_BIN):
    # stub python3 itself, on a narrowed PATH, at the exact call shape
    # detect_installed_status uses - `python3 - <provider> <package> <cask>`,
    # answering "installed"/"not-detected" straight from argv$3 (the package)
    # without touching a package database at all. That leaves
    # catalog_probe_installed's OWN mapping logic (CAT_INSTALLED[i] set from
    # the probe result) the only thing under test, deterministically on any
    # host bash can run on.
    cpi_bin="$(mktemp -d)"
    cat > "$cpi_bin/python3" <<'EOS'
#!/usr/bin/env bash
[[ "${1:-}" == "-" ]] || exit 1
case "${3:-}" in
    tmux) printf 'not-detected\n' ;;
    *)    printf 'installed\n' ;;
esac
EOS
    chmod +x "$cpi_bin/python3"

    catalog_load catalog/linux.json x64 0
    PATH="$cpi_bin:$PATH" catalog_probe_installed
    rc=$?
    git_idx="$(catalog_index_of git)"
    tmux_idx="$(catalog_index_of tmux)"
    rm -rf "$cpi_bin"
    assert_ok "$rc"
    assert_eq "${CAT_INSTALLED[git_idx]}:${CAT_INSTALLED[tmux_idx]}" "1:0"
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

# backup_file names its copy <file>.autoos-backup-<stamp> with ONE-SECOND
# resolution and cp overwrites, so two changing writes within a second used to
# destroy the user's original (the Windows side got Copy-AutoOSBackup for the
# same reason: base name, then -1, -2, ... never overwrite).
if it "backup: backup_file never overwrites a same-second backup"; then
    d="$(mktemp -d)"; f="$d/settings.json"; ok=1
    printf 'A' >"$f"; chmod 600 "$f"; p1="$(backup_file "$f" 20260101-000000)"; rc1=$?
    printf 'B' >"$f"; p2="$(backup_file "$f" 20260101-000000)"
    printf 'C' >"$f"; p3="$(backup_file "$f" 20260101-000000)"
    [[ "$p1" == "$f.autoos-backup-20260101-000000" ]]   || { ok=0; echo "first backup path: [$p1]" >&2; }
    [[ "$p2" == "$f.autoos-backup-20260101-000000-1" ]] || { ok=0; echo "second backup path: [$p2]" >&2; }
    [[ "$p3" == "$f.autoos-backup-20260101-000000-2" ]] || { ok=0; echo "third backup path: [$p3]" >&2; }
    (( rc1 == 0 )) || { ok=0; echo "first call rc=$rc1" >&2; }
    [[ "$(cat "$p1" 2>/dev/null)" == A && "$(cat "$p2" 2>/dev/null)" == B && "$(cat "$p3" 2>/dev/null)" == C ]] \
        || { ok=0; echo "bytes: [$(cat "$p1" 2>/dev/null)] [$(cat "$p2" 2>/dev/null)] [$(cat "$p3" 2>/dev/null)]" >&2; }
    [[ "$(stat -c '%a' "$p1" 2>/dev/null)" == 600 ]] || { ok=0; echo "the backup did not keep the file mode" >&2; }
    [[ "$(find "$d" -name '*.autoos-backup-*' | wc -l | tr -d ' ')" == 3 ]] || { ok=0; echo "expected exactly 3 backups" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "backup_file overwrote or misnamed a backup"; fi
fi

if it "backup: backup_file fails and prints nothing when the copy cannot be made"; then
    d="$(mktemp -d)"; ok=1
    out="$(backup_file "$d/does-not-exist" 20260101-000000 2>/dev/null)"; rc=$?
    (( rc != 0 )) || { ok=0; echo "rc=0 for a file that is not there" >&2; }
    [[ -z "$out" ]] || { ok=0; echo "printed [$out] for a backup that was not made" >&2; }
    [[ "$(find "$d" -type f | wc -l | tr -d ' ')" == 0 ]] || { ok=0; echo "left a file behind" >&2; }
    # ...and the same call works once the file is there (a missing helper also "fails").
    printf 'X' >"$d/there"
    out="$(backup_file "$d/there" 20260101-000000 2>/dev/null)"; rc=$?
    (( rc == 0 )) && [[ "$out" == "$d/there.autoos-backup-20260101-000000" && -f "$out" ]] \
        || { ok=0; echo "a valid backup did not work: rc=$rc out=[$out]" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "backup_file does not report a failed copy honestly"; fi
fi

if it "backup: two changing writes in one second keep the user's original"; then
    d="$(mktemp -d)"; f="$d/.zshrc"
    printf 'ORIGINAL\n' >"$f"
    (
        AUTOOS_DRY_RUN=0
        date() { printf '20260101-000000\n'; }   # every backup lands in the same second
        append_line_once "$f" "AutoOS:one" "export ONE=1  # AutoOS:one"
        append_line_once "$f" "AutoOS:two" "export TWO=2  # AutoOS:two"
    ) >/dev/null 2>&1
    ok=1
    [[ "$(cat "$f.autoos-backup-20260101-000000" 2>/dev/null)" == "ORIGINAL" ]] \
        || { ok=0; echo "the oldest backup is [$(cat "$f.autoos-backup-20260101-000000" 2>/dev/null)], not the seed" >&2; }
    grep -q 'AutoOS:one' "$f.autoos-backup-20260101-000000-1" 2>/dev/null \
        || { ok=0; echo "the second backup (state before the second write) is missing" >&2; }
    grep -q 'AutoOS:two' "$f" || { ok=0; echo "the second write did not land" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a second write in the same second destroyed the user's original"; fi
fi

# ...-10 sorts before ...-2 by name, so "the newest backup" cannot be the last
# name: undo picks by modification time.
if it "backup: undo restores the newest backup, not the last name (-10 sorts before -2)"; then
    ok=1
    # Written one after the other (rising mtimes), and all inside one clock tick.
    for spread in rising equal; do
        scratch="$(mktemp -d)"; target="$scratch/.zshrc"; printf 'NOW\n' >"$target"
        n=0
        for sfx in "" -1 -2 -10; do
            printf 'state%s\n' "$sfx" >"$target.autoos-backup-20260101-000000$sfx"
            n=$((n+1))
            if [[ "$spread" == rising ]]; then touch -d "2026-01-01 00:00:0$n" "$target.autoos-backup-20260101-000000$sfx"
            else touch -d "2026-01-01 00:00:00" "$target.autoos-backup-20260101-000000$sfx"; fi
        done
        ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; autoos_undo 1 >/dev/null 2>&1 )
        body="$(cat "$target")"
        [[ "$body" == "state-10" ]] || { ok=0; echo "$spread mtimes: restored [$body], expected [state-10]" >&2; }
        rm -rf "$scratch"
    done
    if (( ok )); then pass; else fail "undo restored a backup that is not the newest"; fi
fi

# backup_file returns non-zero when the copy fails (full disk, read-only
# directory). Nine call sites moved onto it and eight ignored that answer, so
# the file was then modified with NO backup - AGENTS.md hard rule 5 broken in
# exactly the case the helper detects. install_claude_autostart is the model:
# a failed backup leaves the file alone, says so, and reports failure.
#
# backup_fail_bin: prints a scratch dir holding a `cp` that refuses any
# operand named *.autoos-backup-* (creating a backup, or reading one for a
# restore) and delegates to the real cp for everything else. The copy fails
# deterministically, with no root tricks. The caller puts the dir first on PATH
# inside a subshell and removes it afterwards.
backup_fail_bin() {
    local d real; d="$(mktemp -d)"; real="$(command -v cp)"
    printf '#!/bin/sh\nfor a in "$@"; do\n    case "$a" in\n        *.autoos-backup-*) echo "cp: cannot copy $a: No space left on device (test stub)" >&2; exit 1 ;;\n    esac\ndone\nexec %s "$@"\n' "$real" >"$d/cp"
    chmod +x "$d/cp"
    printf '%s\n' "$d"
}

# backup_count <dir>: how many backups sit anywhere under <dir>.
backup_count() { find "$1" -name '*.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' '; }

# backup_holds <file> <bytes>: some backup of <file> holds exactly <bytes>.
backup_holds() {
    local b
    for b in "$1".autoos-backup-*; do
        [[ -f "$b" && "$(cat "$b")" == "$2" ]] && return 0
    done
    return 1
}

if it "backup: append_line_once leaves the file alone and fails when its backup cannot be made"; then
    d="$(mktemp -d)"; f="$d/.zshrc"; bin="$(backup_fail_bin)"; ok=1
    printf 'ORIGINAL\n' >"$f"
    out="$( ( PATH="$bin:$PATH"; AUTOOS_DRY_RUN=0; append_line_once "$f" "AutoOS:t" "export T=1  # AutoOS:t" ) 2>&1 )"; rc=$?
    [[ "$(cat "$f")" == ORIGINAL ]] || { ok=0; echo "the file was modified without a backup: [$(cat "$f")]" >&2; }
    [[ "$out" == *"could not back up $f"* ]] || { ok=0; echo "no warning naming the file: [$out]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for a write that did not happen" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: the same call with a working cp does write, and backs up first.
    out="$( ( AUTOOS_DRY_RUN=0; append_line_once "$f" "AutoOS:t" "export T=1  # AutoOS:t" ) 2>&1 )"; rc=$?
    { grep -q 'AutoOS:t' "$f" && (( rc == 0 )) && [[ "$(backup_count "$d")" == 1 ]]; } \
        || { ok=0; echo "control run: rc=$rc backups=$(backup_count "$d") out=[$out]" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "append_line_once edits a file it could not back up"; fi
fi

if it "backup: install_agy does not run the vendor installer when a profile cannot be backed up"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    printf 'original zshrc\n' >"$home/.zshrc"; printf 'original profile\n' >"$home/.profile"
    _agy_run() {   # _agy_run [stub-dir]
        (
            HOME="$home"; AUTOOS_DRY_RUN=0; [[ -z "${1:-}" ]] || PATH="$1:$PATH"
            curl() {
                local o="" p="" a
                for a in "$@"; do [[ "$p" == "-o" ]] && o="$a"; p="$a"; done
                printf '#!/usr/bin/env bash\ntouch "$HOME/vendor-ran"\necho "export PATH=x" >>"$HOME/.zshrc"\necho "export PATH=x" >>"$HOME/.profile"\n' >"$o"
            }
            install_agy
        ) 2>&1
    }
    out="$(_agy_run "$bin")"; rc=$?
    [[ "$(cat "$home/.zshrc")|$(cat "$home/.profile")" == "original zshrc|original profile" ]] \
        || { ok=0; echo "a profile was edited without a backup: [$(cat "$home/.zshrc")] [$(cat "$home/.profile")]" >&2; }
    [[ ! -e "$home/vendor-ran" ]] || { ok=0; echo "the vendor installer ran although a profile could not be backed up" >&2; }
    [[ "$out" == *"could not back up $home/"* ]] || { ok=0; echo "no warning naming the profile: [${out:0:300}]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for an installer that did not run" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp the vendor installer runs and both profiles are kept.
    out="$(_agy_run)"; rc=$?
    { (( rc == 0 )) && [[ -e "$home/vendor-ran" && "$(backup_count "$home")" == 2 ]]; } \
        || { ok=0; echo "control run: rc=$rc backups=$(backup_count "$home") out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "install_agy lets its vendor installer edit profiles it could not back up"; fi
fi

if it "backup: the Qwen Code routing does not run when settings.json cannot be backed up"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    mkdir -p "$home/.qwen"; printf '{"mine": true}\n' >"$home/.qwen/settings.json"
    _qwen_run() {   # _qwen_run [stub-dir]
        (
            SYS_HOME="$home"; AUTOOS_DRY_RUN=0; OMNIROUTE_API_KEY=k1; AUTOOS_OMNIROUTE_KEY=k2
            [[ -z "${1:-}" ]] || PATH="$1:$PATH"
            has_cmd() { [[ "$1" == qwen || "$1" == omniroute ]]; }
            omniroute() { touch "$home/omniroute-ran"; printf '{"routed": true}\n' >"$SYS_HOME/.qwen/settings.json"; }
            route_detected_clis_to_gateway
        ) 2>&1
    }
    out="$(_qwen_run "$bin")"
    [[ "$(cat "$home/.qwen/settings.json")" == '{"mine": true}' ]] || { ok=0; echo "settings.json was modified without a backup: [$(cat "$home/.qwen/settings.json")]" >&2; }
    [[ ! -e "$home/omniroute-ran" ]] || { ok=0; echo "omniroute setup-qwen ran although settings.json could not be backed up" >&2; }
    [[ "$out" == *"could not back up $home/.qwen/settings.json"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp the routing runs, after a backup of the original.
    out="$(_qwen_run)"
    { [[ -e "$home/omniroute-ran" && "$(backup_count "$home")" == 1 ]] \
        && [[ "$(cat "$home"/.qwen/settings.json.autoos-backup-*)" == '{"mine": true}' ]]; } \
        || { ok=0; echo "control run: backups=$(backup_count "$home") out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "Qwen Code routing edits a settings.json it could not back up"; fi
fi

if it "backup: route_zed_to_proxy leaves settings.json alone and fails when its backup cannot be made"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    mkdir -p "$home/.config/zed"; printf '{"theme":"mine"}' >"$home/.config/zed/settings.json"
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; route_zed_to_proxy ) 2>&1 )"; rc=$?
    [[ "$(cat "$home/.config/zed/settings.json")" == '{"theme":"mine"}' ]] || { ok=0; echo "settings.json was modified without a backup: [$(cat "$home/.config/zed/settings.json")]" >&2; }
    [[ "$out" == *"could not back up $home/.config/zed/settings.json"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for a routing that did not happen" >&2; }
    [[ "$out" != *"routed to OmniRoute"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp the merge lands and the original is kept.
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; route_zed_to_proxy ) 2>&1 )"; rc=$?
    { (( rc == 0 )) && grep -q 'autoos-omniroute' "$home/.config/zed/settings.json" \
        && [[ "$(cat "$home"/.config/zed/settings.json.autoos-backup-*)" == '{"theme":"mine"}' ]]; } \
        || { ok=0; echo "control run: rc=$rc out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "route_zed_to_proxy edits a settings.json it could not back up"; fi
fi

if it "backup: register_antigravity_mcp_server leaves the config alone when its backup cannot be made"; then
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    cfg="$home/.gemini/config/mcp_config.json"
    mkdir -p "${cfg%/*}"; printf '{"mcpServers": {"keep": {"command": "x"}}}\n' >"$cfg"
    before="$(cat "$cfg")"
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; register_antigravity_mcp_server newone '{"command":"npx"}' ) 2>&1 )"
    [[ "$(cat "$cfg")" == "$before" ]] || { ok=0; echo "the config was modified without a backup: [$(cat "$cfg")]" >&2; }
    [[ "$out" == *"could not back up $cfg"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    [[ "$out" != *"configured MCP server"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$home")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    out="$( ( SYS_HOME="$home"; AUTOOS_DRY_RUN=0; register_antigravity_mcp_server newone '{"command":"npx"}' ) 2>&1 )"
    { grep -q '"newone"' "$cfg" && [[ "$(cat "$cfg".autoos-backup-*)" == "$before" ]]; } \
        || { ok=0; echo "control run: out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "register_antigravity_mcp_server edits a config it could not back up"; fi
fi

if it "backup: enable_project_mcp_server leaves settings.local.json alone when its backup cannot be made"; then
    repo="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    f="$repo/.claude/settings.local.json"
    mkdir -p "${f%/*}"; printf '{"theme":"mine"}\n' >"$f"
    out="$( ( AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; enable_project_mcp_server "$repo" omnigraph ) 2>&1 )"
    [[ "$(cat "$f")" == '{"theme":"mine"}' ]] || { ok=0; echo "the file was modified without a backup: [$(cat "$f")]" >&2; }
    [[ "$out" == *"could not back up $f"* ]] || { ok=0; echo "no warning naming the file: [${out:0:400}]" >&2; }
    [[ "$out" != *"approved project MCP server"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$repo")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    out="$( ( AUTOOS_DRY_RUN=0; enable_project_mcp_server "$repo" omnigraph ) 2>&1 )"
    { grep -q 'omnigraph' "$f" && [[ "$(cat "$f".autoos-backup-*)" == '{"theme":"mine"}' ]]; } \
        || { ok=0; echo "control run: out=[${out:0:300}]" >&2; }
    rm -rf "$repo" "$bin"
    if (( ok )); then pass; else fail "enable_project_mcp_server edits a file it could not back up"; fi
fi

if it "backup: replace_or_append_marked_line leaves the file alone when its backup cannot be made"; then
    d="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    # Branch 1: the current line is there and a stale old-marker line must go.
    printf 'keep me\nstale line  # AutoOS:old\ncurrent line  # AutoOS:new\n' >"$d/purge"
    # Branch 2: only the old-marker line is there and it must be replaced in place.
    printf 'keep me\nstale line  # AutoOS:old\n' >"$d/replace"
    cp "$d/purge" "$d/purge.orig"; cp "$d/replace" "$d/replace.orig"
    out="$( ( PATH="$bin:$PATH"; AUTOOS_DRY_RUN=0
              replace_or_append_marked_line "$d/purge" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new"
              replace_or_append_marked_line "$d/replace" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new" ) 2>&1 )"
    cmp -s "$d/purge" "$d/purge.orig" || { ok=0; echo "the stale-line purge modified the file without a backup: [$(cat "$d/purge")]" >&2; }
    cmp -s "$d/replace" "$d/replace.orig" || { ok=0; echo "the replace modified the file without a backup: [$(cat "$d/replace")]" >&2; }
    [[ "$out" == *"could not back up $d/purge"* && "$out" == *"could not back up $d/replace"* ]] \
        || { ok=0; echo "no warning naming both files: [${out:0:500}]" >&2; }
    [[ "$out" != *"removed the stale"* && "$out" != *"replaced the"* ]] || { ok=0; echo "reported success" >&2; }
    [[ "$(backup_count "$d")" == 0 ]] || { ok=0; echo "a partial backup was left behind" >&2; }
    # Control: with a working cp both edits land, each after a backup.
    ( AUTOOS_DRY_RUN=0
      replace_or_append_marked_line "$d/purge" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new"
      replace_or_append_marked_line "$d/replace" "AutoOS:old" "AutoOS:new" "fresh line  # AutoOS:new" ) >/dev/null 2>&1
    { ! grep -q 'AutoOS:old' "$d/purge" "$d/replace" && [[ "$(backup_count "$d")" == 2 ]]; } \
        || { ok=0; echo "control run: purge=[$(cat "$d/purge")] replace=[$(cat "$d/replace")]" >&2; }
    rm -rf "$d" "$bin"
    if (( ok )); then pass; else fail "replace_or_append_marked_line edits a file it could not back up"; fi
fi

if it "backup: setup_opencode_config leaves a config alone when its backup cannot be made"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    home="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    oc="$home/.config/opencode"; mkdir -p "$oc"
    printf '{"model": "anthropic/mine"}\n' >"$oc/opencode.json"
    printf '{"model": "anthropic/mine"}\n' >"$oc/config.json"
    _oc_fail_run() {   # _oc_fail_run [stub-dir]
        ( SYS_HOME="$home" AUTOOS_DRY_RUN=0 AUTOOS_ROOT="$ROOT"
          [[ -z "${1:-}" ]] || PATH="$1:$PATH"
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          opencode_is_v2() { return 1; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config 2>&1 )
    }
    out="$(_oc_fail_run "$bin")"
    for f in opencode.json config.json; do
        [[ "$(cat "$oc/$f")" == '{"model": "anthropic/mine"}' ]] || { ok=0; echo "$f was modified without a backup: [$(head -c 120 "$oc/$f")]" >&2; }
    done
    [[ "$out" == *"could not back up $oc/config.json"* && "$out" == *"could not back up $oc/opencode.json"* ]] \
        || { ok=0; echo "no warning naming both files: [${out:0:500}]" >&2; }
    [[ "$(ls -A "$oc" | tr '\n' ' ')" == "config.json opencode.json " ]] || { ok=0; echo "stray entries: [$(ls -A "$oc" | tr '\n' ' ')]" >&2; }
    # Control: with a working cp both files are merged and the originals kept.
    out="$(_oc_fail_run)"
    { grep -q 'omniroute' "$oc/config.json" "$oc/opencode.json" \
        && backup_holds "$oc/config.json" '{"model": "anthropic/mine"}' \
        && backup_holds "$oc/opencode.json" '{"model": "anthropic/mine"}'; } \
        || { ok=0; echo "control run: backups=$(backup_count "$oc") out=[${out:0:300}]" >&2; }
    rm -rf "$home" "$bin"
    if (( ok )); then pass; else fail "setup_opencode_config edits a config it could not back up"; fi
    fi
fi

if it "backup: undo says restored only when the copy worked, and fails when it did not"; then
    scratch="$(mktemp -d)"; bin="$(backup_fail_bin)"; ok=1
    target="$scratch/.zshrc"; printf 'NOW\n' >"$target"
    printf 'BEFORE\n' >"$target.autoos-backup-20260101-000000"
    out="$( ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; PATH="$bin:$PATH"; autoos_undo 1 ) 2>&1 )"; rc=$?
    [[ "$(cat "$target")" == NOW ]] || { ok=0; echo "the file changed although the restore failed: [$(cat "$target")]" >&2; }
    [[ "$out" != *"restored $target"* ]] || { ok=0; echo "claimed a restore that did not happen: [${out:0:400}]" >&2; }
    [[ "$out" == *"could not restore $target"* ]] || { ok=0; echo "no message naming the file: [${out:0:400}]" >&2; }
    (( rc != 0 )) || { ok=0; echo "rc=0 for an undo that restored nothing" >&2; }
    # Control: with a working cp it restores, says so, and succeeds.
    out="$( ( SYS_HOME="$scratch"; AUTOOS_DRY_RUN=0; autoos_undo 1 ) 2>&1 )"; rc=$?
    { [[ "$(cat "$target")" == BEFORE && "$out" == *"restored $target"* ]] && (( rc == 0 )); } \
        || { ok=0; echo "control run: rc=$rc body=[$(cat "$target")] out=[${out:0:300}]" >&2; }
    rm -rf "$scratch" "$bin"
    if (( ok )); then pass; else fail "autoos_undo reports a restore that did not happen"; fi
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
    missing="$(python3 - 2>&1 <<'PY'
import json, glob
bad = []
# OS catalogs only: llm-models.json is a model catalogue, not components.
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
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
    bad="$(python3 - 2>&1 <<'PY'
import json, glob
bad = []
# OS catalogs only: llm-models.json is a model catalogue, not components.
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
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
    missing="$(python3 - 2>&1 <<'PY'
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
    missing="$(python3 - 2>&1 <<'PY'
import json, glob, collections
have = collections.defaultdict(set)
# OS catalogs only: llm-models.json is keyed by model id, not component id.
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
    plat = p.replace("catalog", "").strip("/\\").replace(".json", "")
    for g in json.load(open(p, encoding="utf-8"))["categories"]:
        for c in g["components"]:
            have[c["id"]].add(plat)
core = ["claude-code", "git", "nodejs", "docker", "tailscale",
        "handy", "vscode", "herdr", "agent-skills", "nerd-font",
        "zed", "litellm", "opencode-cli", "omniroute", "openhands-docker"]
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
    n="$(grep -l '"id": "handy"' catalog/windows.json catalog/linux.json catalog/macos.json | wc -l)"
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

# ─── AI routing (omniroute / litellm / zed / opencode) ────────────────────
describe "AI routing"

if it "zed rides the script provider end to end"; then
    ok=1
    grep -q 'zed)             install_zed' lib/linux/install.sh || ok=0
    grep -q 'zed)             has_bin zed' lib/linux/detect.sh || ok=0
    # has_bin is stubbed so the test never depends on what this machine happens
    # to have installed (Zed is present on the dev box via WSL interop).
    ( has_bin() { return 1; }; script_is_installed zed ) >/dev/null 2>&1 && ok=0
    ( has_bin() { return 0; }; script_is_installed zed ) >/dev/null 2>&1 || ok=0
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
        grep -q 'LLM_MODEL=openai/t1-orchestrator' "$f" || { ok=0; echo "missing model in $f" >&2; }
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
    grep -q 'model = "openai/t1-orchestrator"' configuration/openhands/config.toml || ok=0
    grep -q 'model = "openai/t3-driver"' configuration/openhands/config.toml || ok=0
    grep -q 'openai/t1-orchestrator-clean' configuration/openhands/config.toml || ok=0
    if [[ -n "$bad" ]]; then fail "model lines without the openai/ prefix: $bad"
    elif (( ok )); then pass
    else fail "template is missing the expected tier models"; fi
fi

if it "provider status never carries key values"; then
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' 2>/dev/null; then
        skip "python3 < 3.7 cannot import serve.py"
    else
        report="$(python3 - 2>&1 <<'PY'
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

if it "python heredoc checks capture stderr"; then
    # Regression gate for the 2026-09-24 finding: a crashing python heredoc
    # prints to stderr, which $() does not capture, so an empty-expected
    # assert passed on empty stdout. Every heredoc opener feeding an
    # assert_eq "" must carry 2>&1 on the command line (this test included).
    bad="$(python3 - 2>&1 <<'PY'
import re, io
lines = io.open("tests/run-tests.sh", encoding="utf-8").read().splitlines()
bare = []
for i, l in enumerate(lines):
    m = re.match(r"""\s*(\w+)="\$\(python3\b(.*)<<'PY'\s*$""", l)
    if m and "2>&1" not in m.group(2):
        var = m.group(1)
        for j in range(i + 1, min(i + 60, len(lines))):
            if re.match(r"""\s*assert_eq "\$%s" ""$""" % var, lines[j]):
                bare.append(str(i + 1))
                break
print(" ".join(bare))
PY
)"
    assert_eq "$bad" ""
fi

if it "zed requires the router on every platform"; then
    bad="$(python3 - 2>&1 <<'PY'
import json, glob
bad = []
for p in sorted(glob.glob("catalog/*.json")):
    for g in json.load(open(p, encoding="utf-8")).get("categories", []):
        for c in g["components"]:
            if c["id"] == "zed" and "litellm" not in c.get("requires", []):
                bad.append(p)
print(" ".join(bad))
PY
)"
    assert_eq "$bad" ""
fi

if it "route_detected_clis_to_gateway announces in dry run and writes nothing"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '{"theme":"mine"}' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; AUTOOS_OMNIROUTE_KEY="k1" OMNIROUTE_API_KEY="k2"; route_detected_clis_to_gateway ) 2>&1)"
    after="$(cat "$scratch/.claude/settings.json")"
    backups="$(find "$scratch" -name '*.autoos-backup-*' 2>/dev/null | wc -l)"
    rm -rf "$scratch"
    if [[ -n "$out" && "$before" == "$after" && "$backups" == "0" ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
fi

if it "route_detected_clis_to_gateway bridges keys from the keys file"; then
    tmp="$(mktemp -d)"
    printf 'omniroute: test-file-key\n' >"$tmp/api-keys.yml"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=1
        unset OMNIROUTE_API_KEY AUTOOS_OMNIROUTE_KEY
        has_cmd() { return 1; }
        AUTOOS_KEYS_FILE="$tmp/api-keys.yml" route_detected_clis_to_gateway >/dev/null 2>&1
        printf '%s|%s' "${OMNIROUTE_API_KEY:-empty}" "${AUTOOS_OMNIROUTE_KEY:-empty}"
    )"
    rm -rf "$tmp"
    assert_eq "$out" "test-file-key|test-file-key"
fi

if it "zed routing announces in dry run and writes nothing"; then
    scratch="$(mktemp -d)"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; route_zed_to_proxy ) 2>&1)"
    if [[ "$out" == *"would route"* && ! -e "$scratch/.config/zed/settings.json" ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
    rm -rf "$scratch"
fi

# Claude Code gateway routing is opt-in and reversible (catalog prompt
# claude_gateway_routing, default "login"). ANTHROPIC_BASE_URL/AUTH_TOKEN in
# ~/.claude/settings.json "env" disable the claude.ai connectors, so every answer
# other than "gateway" takes exactly those two keys out again. A backup is taken
# only by a run that changes the file, so each fixture below sees at most ONE
# modifying run and the exact backup count is safe from the per-second timestamp
# collision fixed in 0bb5952.
_claude_settings_report() {  # _claude_settings_report <settings.json>
    python3 - "$1" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
print("%s|%s|%s" % (cfg.get("theme"),
                    json.dumps(cfg.get("permissions"), sort_keys=True),
                    json.dumps(cfg.get("env"), sort_keys=True) if "env" in cfg else "no-env"))
PY
}
_claude_backups() {  # _claude_backups <home> -> number of settings.json backups
    find "$1/.claude" -maxdepth 1 -name 'settings.json.autoos-backup-*' 2>/dev/null | wc -l | tr -d ' '
}

if it "route_claude_to_gateway (default login answer) strips only the two gateway keys"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","permissions":{"allow":["Bash(ls)"]},"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:20128","ANTHROPIC_AUTH_TOKEN":"test-omni-key","KEEP_ME":"1"}}' \
        >"$scratch/.claude/settings.json"
    # A key in the environment must not matter any more: no answer means login.
    out1="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'
               AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    out2="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'
               AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    report="$(_claude_settings_report "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    backup_kept_keys="no"
    if grep -q 'ANTHROPIC_AUTH_TOKEN' "$scratch"/.claude/settings.json.autoos-backup-* 2>/dev/null; then backup_kept_keys="yes"; fi
    # "env" holding nothing but the two keys disappears entirely.
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch2/.claude/settings.json"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway >/dev/null 2>&1 )
    report2="$(_claude_settings_report "$scratch2/.claude/settings.json")"
    rm -rf "$scratch" "$scratch2"
    want='mine|{"allow": ["Bash(ls)"]}|{"KEEP_ME": "1"}'
    if [[ "$report" == "$want" && "$report2" == "mine|null|no-env" && "$backups" == 1 \
          && "$backup_kept_keys" == yes && "$out1" == *"removed"* && "$out2" == *"skipped"* \
          && "$out2" != *"removed"* ]]; then pass
    else fail "report=[$report] report2=[$report2] backups=$backups backup_kept_keys=$backup_kept_keys out1=[$out1] out2=[$out2]"; fi
fi

if it "route_claude_to_gateway (default login answer) leaves a file without the keys untouched"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"KEEP_ME":"1"}}' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway ) 2>&1)"
    after="$(cat "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    # No settings file at all: nothing is created, not even ~/.claude.
    scratch2="$(mktemp -d)"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway >/dev/null 2>&1 )
    created="no"; [[ -e "$scratch2/.claude" ]] && created="yes"
    rm -rf "$scratch" "$scratch2"
    if [[ "$before" == "$after" && "$backups" == 0 && "$created" == no && "$out" == *"skipped"* ]]; then pass
    else fail "changed=$([[ "$before" == "$after" ]] && echo no || echo yes) backups=$backups created=$created out=[$out]"; fi
fi

if it "route_claude_to_gateway (gateway answer) points Claude Code at OmniRoute, second run skipped"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"KEEP_ME":"1"}}' >"$scratch/.claude/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    out2="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
               AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    report="$(_claude_settings_report "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    # Gateway mode still needs the key: without it nothing is written.
    scratch2="$(mktemp -d)"
    nokey_out="$( ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
                    unset AUTOOS_OMNIROUTE_KEY; route_claude_to_gateway ) 2>&1)"
    nokey="no"; [[ -e "$scratch2/.claude/settings.json" ]] || nokey="yes"
    rm -rf "$scratch" "$scratch2"
    want='mine|null|{"ANTHROPIC_AUTH_TOKEN": "test-omni-key", "ANTHROPIC_BASE_URL": "http://127.0.0.1:20128", "KEEP_ME": "1"}'
    if [[ "$report" == "$want" && "$backups" == 1 && "$out2" == *"skipped"* \
          && "$nokey" == yes && "$nokey_out" == *"AUTOOS_OMNIROUTE_KEY not set"* ]]; then pass
    else fail "report=[$report] backups=$backups out2=[$out2] nokey=$nokey nokey_out=[$nokey_out]"; fi
fi

if it "route_claude_to_gateway dry run announces and writes nothing in either mode"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    login_out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=1; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway ) 2>&1)"
    after_login="$(cat "$scratch/.claude/settings.json")"
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.claude"
    printf '%s' '{"theme":"mine"}' >"$scratch2/.claude/settings.json"
    before2="$(cat "$scratch2/.claude/settings.json")"
    gw_out="$( ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=1; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
                 AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"
    after_gw="$(cat "$scratch2/.claude/settings.json")"
    scratch3="$(mktemp -d)"
    ( SYS_HOME="$scratch3" AUTOOS_DRY_RUN=1; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    created="no"; [[ -e "$scratch3/.claude" ]] && created="yes"
    backups="$(( $(_claude_backups "$scratch") + $(_claude_backups "$scratch2") ))"
    rm -rf "$scratch" "$scratch2" "$scratch3"
    if [[ "$before" == "$after_login" && "$before2" == "$after_gw" && "$backups" == 0 && "$created" == no \
          && "$login_out" == *"would remove"* && "$gw_out" == *"would point"* ]]; then pass
    else fail "backups=$backups created=$created login_out=[$login_out] gw_out=[$gw_out]"; fi
fi

if it "route_claude_to_gateway login mode works without AUTOOS_OMNIROUTE_KEY"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y","KEEP_ME":"1"}}' >"$scratch/.claude/settings.json"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=login
              unset AUTOOS_OMNIROUTE_KEY; route_claude_to_gateway ) 2>&1)"
    report="$(_claude_settings_report "$scratch/.claude/settings.json")"
    rm -rf "$scratch"
    if [[ "$report" == 'mine|null|{"KEEP_ME": "1"}' && "$out" != *"AUTOOS_OMNIROUTE_KEY"* ]]; then pass
    else fail "report=[$report] out=[$out]"; fi
fi

if it "route_claude_to_gateway leaves an unreadable settings file untouched in either mode"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme": "mine", "env": {"ANTHROPIC_BASE_URL": ' >"$scratch/.claude/settings.json"
    before="$(cat "$scratch/.claude/settings.json")"
    login_out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway ) 2>&1)"; rc1=$?
    gw_out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
                 AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway ) 2>&1)"; rc2=$?
    after="$(cat "$scratch/.claude/settings.json")"
    backups="$(_claude_backups "$scratch")"
    rm -rf "$scratch"
    if [[ "$before" == "$after" && "$backups" == 0 && "$rc1$rc2" == 00 \
          && "$login_out" == *"left untouched"* && "$gw_out" == *"left untouched"* ]]; then pass
    else fail "changed=$([[ "$before" == "$after" ]] && echo no || echo yes) backups=$backups rc=$rc1$rc2 login_out=[$login_out] gw_out=[$gw_out]"; fi
fi

_file_mode() {  # _file_mode <path> -> permission bits in octal, e.g. 600
    python3 -c 'import os, sys; print("%o" % (os.stat(sys.argv[1]).st_mode & 0o777))' "$1" 2>&1
}
_file_inode() {  # _file_inode <path>
    python3 -c 'import os, sys; print(os.stat(sys.argv[1]).st_ino)' "$1" 2>&1
}

if it "route_claude_to_gateway replaces settings.json atomically with mode 600, backups 600"; then
    # The file holds ANTHROPIC_AUTH_TOKEN, and an in-place rewrite that dies
    # mid-write (crash, ENOSPC) leaves it empty: temp file + os.replace, 0600.
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine"}' >"$scratch/.claude/settings.json"
    chmod 644 "$scratch/.claude/settings.json"
    inode_before="$(_file_inode "$scratch/.claude/settings.json")"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    inode_after="$(_file_inode "$scratch/.claude/settings.json")"
    gw_mode="$(_file_mode "$scratch/.claude/settings.json")"
    gw_backup_mode="$(_file_mode "$(find "$scratch/.claude" -name 'settings.json.autoos-backup-*' | head -1)")"
    leftovers="$(find "$scratch/.claude" -name '*autoos-tmp*' | wc -l | tr -d ' ')"
    # A settings file created from nothing is 600 as well.
    scratch2="$(mktemp -d)"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=gateway
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    new_mode="$(_file_mode "$scratch2/.claude/settings.json")"
    # The login-mode rewrite goes the same way, and its backup still holds the token.
    scratch3="$(mktemp -d)"
    mkdir -p "$scratch3/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch3/.claude/settings.json"
    chmod 644 "$scratch3/.claude/settings.json"
    ( SYS_HOME="$scratch3" AUTOOS_DRY_RUN=0; unset 'AUTOOS_ANSWERS[claude_gateway_routing]'; route_claude_to_gateway >/dev/null 2>&1 )
    login_mode="$(_file_mode "$scratch3/.claude/settings.json")"
    login_backup_mode="$(_file_mode "$(find "$scratch3/.claude" -name 'settings.json.autoos-backup-*' | head -1)")"
    rm -rf "$scratch" "$scratch2" "$scratch3"
    got="$gw_mode|$gw_backup_mode|$new_mode|$login_mode|$login_backup_mode|$leftovers"
    if [[ "$got" == "600|600|600|600|600|0" && "$inode_before" != "$inode_after" ]]; then pass
    else fail "modes|leftovers=[$got] (want 600|600|600|600|600|0) inode $inode_before -> $inode_after (must change: replaced, not rewritten in place)"; fi
fi

if it "route_claude_to_gateway trims and lower-cases the answer, like the PowerShell side"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.claude"
    printf '%s' '{"theme":"mine"}' >"$scratch/.claude/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]=" Gateway "
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    padded="$(_claude_settings_report "$scratch/.claude/settings.json")"
    # Inner whitespace is not stripped: "gate way" is not gateway, so it means login.
    scratch2="$(mktemp -d)"
    mkdir -p "$scratch2/.claude"
    printf '%s' '{"theme":"mine","env":{"ANTHROPIC_BASE_URL":"x","ANTHROPIC_AUTH_TOKEN":"y"}}' >"$scratch2/.claude/settings.json"
    ( SYS_HOME="$scratch2" AUTOOS_DRY_RUN=0; AUTOOS_ANSWERS[claude_gateway_routing]="gate way"
      AUTOOS_OMNIROUTE_KEY="test-omni-key" route_claude_to_gateway >/dev/null 2>&1 )
    split="$(_claude_settings_report "$scratch2/.claude/settings.json")"
    rm -rf "$scratch" "$scratch2"
    if [[ "$padded" == 'mine|null|{"ANTHROPIC_AUTH_TOKEN": "test-omni-key", "ANTHROPIC_BASE_URL": "http://127.0.0.1:20128"}' \
          && "$split" == "mine|null|no-env" ]]; then pass
    else fail "padded=[$padded] split=[$split]"; fi
fi

if it "claude_gateway_routing is asked by claude-code and omniroute on every platform, default login"; then
    bad="$(python3 - "$ROOT/catalog" 2>&1 <<'PY'
import json, pathlib, sys
problems = []
for name in ("linux", "macos", "windows"):
    cat = json.loads((pathlib.Path(sys.argv[1]) / f"{name}.json").read_text(encoding="utf-8"))
    spec = cat.get("prompts", {}).get("claude_gateway_routing")
    if not spec:
        problems.append(f"{name}: no claude_gateway_routing prompt"); continue
    if spec.get("default") != "login":
        problems.append(f"{name}: default is {spec.get('default')!r}, not 'login'")
    if not spec.get("question") or not spec.get("help"):
        problems.append(f"{name}: prompt needs question and help")
    comps = {c["id"]: c for g in cat["categories"] for c in g["components"]}
    for cid in ("claude-code", "omniroute"):
        keys = str(comps.get(cid, {}).get("prompt") or "").replace(",", " ").split()
        if "claude_gateway_routing" not in keys:
            problems.append(f"{name}: {cid} does not ask claude_gateway_routing")
print("; ".join(problems))
PY
)"
    assert_eq "$bad" ""
fi

if it "install_qodercli announces in dry run and writes nothing"; then
    out="$( ( AUTOOS_DRY_RUN=1; install_qodercli ) 2>&1)"
    if [[ "$out" == *"would download and run the Qoder CLI installer"* ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
fi

if it "install_devin_cli announces in dry run and writes nothing"; then
    out="$( ( AUTOOS_DRY_RUN=1; install_devin_cli ) 2>&1)"
    if [[ "$out" == *"would download and run the Devin CLI installer"* ]]; then pass
    else fail "dry run wrote or stayed silent"; fi
fi

# Operator 2026-09-25: the Antigravity app "was not available" on Linux, yet
# the run said installed - a blank pasted .deb URL returned 0, so
# install_package counted it installed. F7 removed the cause: Google publishes
# a signed apt repo (antigravity.google/download/linux), so there is no URL
# to ask for and nothing to leave blank. The repo is frozen at 1.23.2 (the 2.x
# apps are tarball-only) and the installer says so.
#
# antigravity_run <scratch> <dry 0|1> [fail-curl] [stale-answer]
# One install_antigravity run in its own subshell (a fresh APT_UPDATED, like a
# fresh process) against a scratch apt tree via the AUTOOS_APT_PREFIX test
# seam. Everything that could touch the machine is a recording stub in
# <scratch>/calls.log, so this can neither install, write to /etc nor reach
# the network (AGENTS.md section 5).
antigravity_run() {
    local sb="$1" dry="$2" mode="${3:-}" stale="${4:-}"
    (
        AUTOOS_DRY_RUN="$dry"; AUTOOS_SUDO=""; AUTOOS_APT_PREFIX="$sb"; APT_UPDATED=0
        [[ -n "$stale" ]] && AUTOOS_ANSWERS['antigravity_url']=https://example.invalid/stale.deb
        log="$sb/calls.log"
        curl() {
            printf 'curl %s\n' "$*" >>"$log"
            [[ "$mode" == fail-curl ]] && return 22
            local o="" p="" a
            for a in "$@"; do [[ "$p" == "-o" ]] && o="$a"; p="$a"; done
            if [[ -n "$o" ]]; then printf 'ARMORED-KEY\n' >"$o"; else printf 'ARMORED-KEY\n'; fi
        }
        gpg() { printf 'gpg %s\n' "$*" >>"$log"; sed 's/^/DEARMORED:/'; }
        install() {
            printf 'install %s\n' "$*" >>"$log"
            local src="${*: -2:1}" dst="${*: -1}"
            mkdir -p "$(dirname "$dst")" && cp "$src" "$dst"
        }
        tee() { printf 'tee %s\n' "$*" >>"$log"; command tee "$@"; }
        # shellcheck disable=SC2120  # stub: install_antigravity (sourced, not visible here) calls it with arguments
        run() { printf 'run %s\n' "$*" >>"$log"; }
        install_antigravity
    ) 2>&1
}

AG_KEY_URL="https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg"
AG_LIST_LINE="deb [signed-by=/etc/apt/keyrings/antigravity-repo-key.gpg] https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/ antigravity-debian main"

if it "install_antigravity dry run names Google's apt repo and package, asks for no URL, writes nothing"; then
    sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/sources.list.d"
    out="$(antigravity_run "$sb" 1 "" stale)"; rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rc=$rc: $out" >&2; }
    [[ "$out" == *"us-central1-apt.pkg.dev"* && "$out" == *"antigravity"* ]] \
        || { ok=0; echo "does not name the apt repo and package: $out" >&2; }
    for bad in ".deb" "download URL" "antigravity_url" "example.invalid"; do
        [[ "$out" != *"$bad"* ]] || { ok=0; echo "mentions '$bad': $out" >&2; }
    done
    [[ ! -s "$sb/calls.log" ]] || { ok=0; echo "a dry run ran commands: $(cat "$sb/calls.log")" >&2; }
    [[ -z "$(find "$sb/etc" -type f)" ]] || { ok=0; echo "a dry run wrote files" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the Antigravity dry run still asks for a .deb URL or touches the machine"; fi
fi

if it "install_antigravity real run adds Google's signed apt repo, installs the package, warns the repo is frozen"; then
    sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/sources.list.d"
    out="$(antigravity_run "$sb" 0 "" stale)"; rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rc=$rc: $out" >&2; }
    grep -qF -- "$AG_KEY_URL" "$sb/calls.log" || { ok=0; echo "key not fetched from $AG_KEY_URL" >&2; }
    grep -q '^gpg .*--dearmor' "$sb/calls.log" || { ok=0; echo "key not dearmored" >&2; }
    [[ "$(cat "$sb/etc/apt/keyrings/antigravity-repo-key.gpg" 2>/dev/null)" == "DEARMORED:ARMORED-KEY" ]] \
        || { ok=0; echo "dearmored key not installed at the keyring path" >&2; }
    [[ "$(cat "$sb/etc/apt/sources.list.d/antigravity.list" 2>/dev/null)" == "$AG_LIST_LINE" ]] \
        || { ok=0; echo "source line differs from Google's: $(cat "$sb/etc/apt/sources.list.d/antigravity.list" 2>&1)" >&2; }
    [[ "$(grep '^run ' "$sb/calls.log")" == $'run apt-get update -y\nrun apt-get install -y antigravity' ]] \
        || { ok=0; echo "apt commands: $(grep '^run ' "$sb/calls.log" | tr '\n' '|')" >&2; }
    [[ "$out" == *"frozen"* && "$out" == *"1.23.2"* ]] || { ok=0; echo "no frozen-repo warning: $out" >&2; }
    ! grep -q 'example.invalid' "$sb/calls.log" || { ok=0; echo "a stale antigravity_url answer was used" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the Antigravity apt repo route is not what Google's download page prescribes"; fi
fi

if it "install_antigravity is idempotent: a second run with key and source present writes nothing"; then
    sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/sources.list.d"
    antigravity_run "$sb" 0 >/dev/null
    before="$(cksum "$sb/etc/apt/sources.list.d/antigravity.list" "$sb/etc/apt/keyrings/antigravity-repo-key.gpg")"
    : >"$sb/calls.log"
    out="$(antigravity_run "$sb" 0)"; rc=$?
    after="$(cksum "$sb/etc/apt/sources.list.d/antigravity.list" "$sb/etc/apt/keyrings/antigravity-repo-key.gpg")"
    ok=1
    (( rc == 0 )) || { ok=0; echo "rc=$rc: $out" >&2; }
    writes="$(grep -E '^(curl|gpg|install|tee) ' "$sb/calls.log" || true)"
    [[ -z "$writes" ]] || { ok=0; echo "the second run wrote again: $writes" >&2; }
    [[ "$before" == "$after" ]] || { ok=0; echo "key or source list changed on the second run" >&2; }
    [[ "$(wc -l <"$sb/etc/apt/sources.list.d/antigravity.list")" == 1 ]] || { ok=0; echo "source list grew" >&2; }
    grep -qx 'run apt-get install -y antigravity' "$sb/calls.log" \
        || { ok=0; echo "apt's own no-op install was skipped: $(cat "$sb/calls.log")" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a second Antigravity run is not a no-op"; fi
fi

if it "install_antigravity writes no key and no source list when the key fetch fails"; then
    sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/sources.list.d"
    out="$(antigravity_run "$sb" 0 fail-curl)"; rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "rc=0 counts a failed key fetch as installed" >&2; }
    [[ -z "$(find "$sb/etc" -type f)" ]] || { ok=0; echo "left files behind: $(find "$sb/etc" -type f | tr '\n' ' ')" >&2; }
    grep -qF -- "$AG_KEY_URL" "$sb/calls.log" 2>/dev/null || { ok=0; echo "the key fetch was never attempted" >&2; }
    ! grep -q 'apt-get install' "$sb/calls.log" 2>/dev/null || { ok=0; echo "went on to apt-get install" >&2; }
    [[ "$out" == *"Antigravity not installed"* ]] || { ok=0; echo "no reason given: $out" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a failed Antigravity key fetch leaves a half-configured apt"; fi
fi

# VS Code, Google Chrome and the GitHub CLI share the pattern Antigravity was
# fixed for: guard on the key file, fetch, install, write the apt source line.
# install_component runs an installer with errexit OFF, so an unchecked failed
# fetch installed an EMPTY key file that the `-f` guard then trusted forever
# (apt failed on every later run). The key must exist only when it is non-empty.
#
# apt_key_case <vscode|chrome|gh>: sets AK_* for one installer.
apt_key_case() {
    case "$1" in
        vscode) AK_FN=install_vscode; AK_KEY=/etc/apt/keyrings/packages.microsoft.gpg
                AK_LIST=/etc/apt/sources.list.d/vscode.list; AK_PKG=code
                AK_URL=https://packages.microsoft.com/keys/microsoft.asc; AK_BODY="DEARMORED:ARMORED-KEY"
                AK_LINE="deb [arch=amd64,arm64,armhf signed-by=$AK_KEY] https://packages.microsoft.com/repos/code stable main" ;;
        chrome) AK_FN=install_google_chrome; AK_KEY=/etc/apt/keyrings/google-chrome.gpg
                AK_LIST=/etc/apt/sources.list.d/google-chrome.list; AK_PKG=google-chrome-stable
                AK_URL=https://dl.google.com/linux/linux_signing_key.pub; AK_BODY="DEARMORED:ARMORED-KEY"
                AK_LINE="deb [arch=amd64 signed-by=$AK_KEY] http://dl.google.com/linux/chrome/deb/ stable main" ;;
        gh)     AK_FN=install_gh; AK_KEY=/etc/apt/keyrings/githubcli-archive-keyring.gpg
                AK_LIST=/etc/apt/sources.list.d/github-cli.list; AK_PKG=gh
                AK_URL=https://cli.github.com/packages/githubcli-archive-keyring.gpg; AK_BODY="ARMORED-KEY"
                AK_LINE="deb [arch=amd64 signed-by=$AK_KEY] https://cli.github.com/packages stable main" ;;
    esac
}

# apt_key_run <scratch> <installer function> [fail-curl|empty-body]
# One installer run in its own subshell against a scratch apt tree
# (AUTOOS_APT_PREFIX). Every writing stub refuses a path outside the scratch
# tree, so even code that ignores the seam cannot touch the real /etc/apt, as
# root or not. Temp files go to <scratch>/tmp so leftovers are visible.
apt_key_run() {
    local sb="$1" fn="$2" mode="${3:-}"
    (
        AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""; AUTOOS_APT_PREFIX="$sb"; APT_UPDATED=0
        export TMPDIR="$sb/tmp"; mkdir -p "$TMPDIR"
        log="$sb/calls.log"
        inside() { [[ "$1" == "$sb"/* ]] || { printf 'REFUSED (outside the scratch tree) %s\n' "$1" >>"$log"; return 1; }; }
        curl() {
            printf 'curl %s\n' "$*" >>"$log"
            [[ "$mode" == fail-curl ]] && return 22
            [[ "$mode" == empty-body ]] && return 0
            printf 'ARMORED-KEY\n'
        }
        gpg() { printf 'gpg %s\n' "$*" >>"$log"; sed 's/^/DEARMORED:/'; }
        install() {
            printf 'install %s\n' "$*" >>"$log"
            local src="${*: -2:1}" dst="${*: -1}"
            inside "$dst" || return 1
            command mkdir -p "$(dirname "$dst")" && cp "$src" "$dst"
        }
        tee() {
            printf 'tee %s\n' "$*" >>"$log"
            inside "${*: -1}" || { cat >/dev/null; return 1; }
            command tee "$@"
        }
        mkdir() { printf 'mkdir %s\n' "$*" >>"$log"; inside "${*: -1}" || return 1; command mkdir "$@"; }
        # shellcheck disable=SC2120  # stub: the installers (sourced, not visible here) call it with arguments
        run() { printf 'run %s\n' "$*" >>"$log"; }
        has_cmd() { [[ "$1" == apt-get ]]; }
        dpkg() { printf 'amd64\n'; }
        "$fn"
    ) 2>&1
}

for _k in vscode chrome gh; do
    if it "apt keys: $_k leaves no key when the download fails"; then
        apt_key_case "$_k"
        sb="$(mktemp -d)"; ok=1
        for mode in fail-curl empty-body; do
            rm -rf "${sb:?}/etc" "${sb:?}/tmp" "${sb:?}/calls.log"; mkdir -p "$sb/etc/apt/sources.list.d"
            out="$(apt_key_run "$sb" "$AK_FN" "$mode")"; rc=$?
            (( rc != 0 )) || { ok=0; echo "$mode: rc=0 counts a failed key download as installed" >&2; }
            [[ -z "$(find "$sb/etc" -type f)" ]] || { ok=0; echo "$mode: left files behind: $(find "$sb/etc" -type f | tr '\n' ' ')" >&2; }
            [[ -z "$(find "$sb/tmp" -type f)" ]] || { ok=0; echo "$mode: temp file not removed" >&2; }
            grep -qF -- "$AK_URL" "$sb/calls.log" 2>/dev/null || { ok=0; echo "$mode: the key download was never attempted" >&2; }
            ! grep -q 'apt-get install' "$sb/calls.log" 2>/dev/null || { ok=0; echo "$mode: went on to apt-get install" >&2; }
            [[ "$out" == *"not installed"* ]] || { ok=0; echo "$mode: no reason given: ${out:0:200}" >&2; }
        done
        rm -rf "$sb"
        if (( ok )); then pass; else fail "a failed $_k key download leaves an empty key or a source line apt cannot use"; fi
    fi

    if it "apt keys: $_k an existing empty key is replaced on the next successful run"; then
        apt_key_case "$_k"
        sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/keyrings" "$sb/etc/apt/sources.list.d"
        # What the old code left behind after a failed download: an empty key
        # and the source line that names it.
        : >"$sb$AK_KEY"; printf '%s\n' "$AK_LINE" >"$sb$AK_LIST"
        out="$(apt_key_run "$sb" "$AK_FN")"; rc=$?
        ok=1
        (( rc == 0 )) || { ok=0; echo "rc=$rc: ${out:0:200}" >&2; }
        [[ "$(cat "$sb$AK_KEY" 2>/dev/null)" == "$AK_BODY" ]] || { ok=0; echo "the empty key was trusted, not replaced: [$(cat "$sb$AK_KEY" 2>/dev/null)]" >&2; }
        [[ "$(cat "$sb$AK_LIST")" == "$AK_LINE" ]] || { ok=0; echo "source line changed: $(cat "$sb$AK_LIST")" >&2; }
        grep -qx "run apt-get install -y $AK_PKG" "$sb/calls.log" 2>/dev/null || { ok=0; echo "the package was not installed: $(cat "$sb/calls.log" 2>/dev/null)" >&2; }
        rm -rf "$sb"
        if (( ok )); then pass; else fail "an empty key from an earlier bad run is trusted forever"; fi
    fi

    if it "apt keys: $_k a second successful run is unchanged"; then
        apt_key_case "$_k"
        sb="$(mktemp -d)"; mkdir -p "$sb/etc/apt/sources.list.d"
        out="$(apt_key_run "$sb" "$AK_FN")"; rc1=$?
        ok=1
        (( rc1 == 0 )) || { ok=0; echo "first run rc=$rc1: ${out:0:200}" >&2; }
        [[ "$(cat "$sb$AK_KEY" 2>/dev/null)" == "$AK_BODY" ]] || { ok=0; echo "first run: key is [$(cat "$sb$AK_KEY" 2>/dev/null)]" >&2; }
        [[ "$(cat "$sb$AK_LIST" 2>/dev/null)" == "$AK_LINE" ]] || { ok=0; echo "first run: source line is [$(cat "$sb$AK_LIST" 2>/dev/null)]" >&2; }
        [[ "$(grep '^run ' "$sb/calls.log" 2>/dev/null)" == $'run apt-get update -y\nrun apt-get install -y '"$AK_PKG" ]] \
            || { ok=0; echo "first run: apt commands: $(grep '^run ' "$sb/calls.log" 2>/dev/null | tr '\n' '|')" >&2; }
        before="$(cksum "$sb$AK_KEY" "$sb$AK_LIST" 2>/dev/null)"
        : >"$sb/calls.log"
        out="$(apt_key_run "$sb" "$AK_FN")"; rc2=$?
        after="$(cksum "$sb$AK_KEY" "$sb$AK_LIST" 2>/dev/null)"
        (( rc2 == 0 )) || { ok=0; echo "second run rc=$rc2: ${out:0:200}" >&2; }
        writes="$(grep -E '^(curl|gpg|install|tee) ' "$sb/calls.log" || true)"
        [[ -z "$writes" ]] || { ok=0; echo "the second run wrote again: $writes" >&2; }
        [[ "$before" == "$after" ]] || { ok=0; echo "key or source list changed on the second run" >&2; }
        [[ "$(wc -l <"$sb$AK_LIST" 2>/dev/null)" == 1 ]] || { ok=0; echo "source list grew" >&2; }
        grep -qx "run apt-get install -y $AK_PKG" "$sb/calls.log" || { ok=0; echo "apt's own no-op install was skipped" >&2; }
        [[ -z "$(find "$sb/tmp" -type f)" ]] || { ok=0; echo "temp file left behind" >&2; }
        rm -rf "$sb"
        if (( ok )); then pass; else fail "a second $_k run is not a no-op"; fi
    fi
done

if it "the antigravity catalog entry needs no download URL: no prompt, and no catalog asks antigravity_url"; then
    problems="$(python3 - 2>&1 <<'PY'
import json
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
    doc = json.load(open(p, encoding="utf-8"))
    if "antigravity_url" in doc.get("prompts", {}):
        print(p + ": still asks antigravity_url")
    for g in doc["categories"]:
        for c in g["components"]:
            if c["id"] == "antigravity" and c.get("prompt"):
                print(p + ": antigravity still carries prompt " + str(c["prompt"]))
PY
)"
    assert_eq "$problems" ""
fi

# Measured 2026-09-25: the vendor agy installer ends with `agy install`, which
# appends a PATH line to ~/.zshrc, ~/.zprofile and ~/.profile. AutoOS runs it,
# so AutoOS keeps the originals (AGENTS.md: back up user-owned files).
if it "install_agy backs up the shell profiles its vendor installer edits"; then
    home="$(mktemp -d)"
    printf 'original zshrc\n' >"$home/.zshrc"
    printf 'original profile\n' >"$home/.profile"
    out="$( (
        HOME="$home"; AUTOOS_DRY_RUN=0
        curl() {
            local o="" p=""
            for a in "$@"; do [[ "$p" == "-o" ]] && o="$a"; p="$a"; done
            printf '#!/usr/bin/env bash\necho "export PATH=x" >>"$HOME/.zshrc"\necho "export PATH=x" >>"$HOME/.profile"\n' >"$o"
        }
        install_agy
    ) 2>&1)"; rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rc=$rc $out" >&2; }
    zb="$(cat "$home"/.zshrc.autoos-backup-* 2>/dev/null)"
    pb="$(cat "$home"/.profile.autoos-backup-* 2>/dev/null)"
    [[ "$zb" == "original zshrc" ]] || { ok=0; echo "zshrc backup: '$zb'" >&2; }
    [[ "$pb" == "original profile" ]] || { ok=0; echo "profile backup: '$pb'" >&2; }
    compgen -G "$home/.zprofile*" >/dev/null && { ok=0; echo "backed up a file that did not exist" >&2; }
    rm -rf "$home"
    if (( ok )); then pass; else fail "agy's vendor installer edits profiles without a backup"; fi
fi

if it "script dispatch covers qodercli and devin-cli"; then
    ok=1
    grep -q 'qodercli) *install_qodercli' lib/linux/install.sh || ok=0
    grep -q 'devin-cli) *install_devin_cli' lib/linux/install.sh || ok=0
    if (( ok )); then pass; else fail "dispatch missing"; fi
fi

if it "zed routing merges one provider and keeps the rest"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    printf '{"theme":"mine"}' >"$scratch/.config/zed/settings.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="test-omni-key" AUTOOS_LITELLM_API_KEY="test-lit-key" route_zed_to_proxy >/dev/null 2>&1 )
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      AUTOOS_OMNIROUTE_API_KEY="test-omni-key" AUTOOS_LITELLM_API_KEY="test-lit-key" route_zed_to_proxy >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
oc = cfg.get("language_models", {}).get("openai_compatible", {})
omni = oc.get("autoos-omniroute", {})
lit = oc.get("autoos-litellm", {})
# The model lists are catalog/ide-models.json projected at run time (ids,
# display names, windows, membership, order); the 1M tier is 1000000.
cat = json.load(open("catalog/ide-models.json", encoding="utf-8"))["models"]
def want(gateway):
    out = []
    for m in cat:
        if "zed" in m["surfaces"].get(gateway, []):
            e = {"name": m["id"], "display_name": m["name"], "max_tokens": m["context"]}
            if m.get("reasoning_effort"):
                e["reasoning_effort"] = m["reasoning_effort"]
            out.append(e)
    return out
got_models = {g: oc.get("autoos-" + g, {}).get("available_models") for g in ("omniroute", "litellm")}
bad = [g for g in got_models if got_models[g] != want(g)]
t1 = [m.get("max_tokens") for m in (got_models["omniroute"] or []) if m.get("name") == "t1-orchestrator"]
models = "catalog-ok" if not bad and t1 == [1000000] else "MISMATCH:%s t1=%s" % (",".join(bad), t1)
# Keys never land in settings.json (Zed docs: keychain/UI or env).
# Pins come from the harness at runtime, never as literals in lib/.
h = json.load(open("catalog/agent-harness.json", encoding="utf-8"))
ctx = cfg.get("context_servers", {})
pinok = ",".join(sorted(
    "pin-ok" if h["mcp_servers"][n]["package"] in " ".join(ctx.get(n, {}).get("args", []))
    else "MISSING:" + n
    for n in ("serena", "graphify", "omnigraph", "playwright", "context7", "autoos-agent")))
print("%s|%s|%s|%s|%s|%s|%s" % (
    cfg.get("theme"), omni.get("api_url"), models,
    "api_key" in omni, lit.get("api_url"), "api_key" in lit, pinok))
bp = cfg.get("agent", {}).get("profiles", {}).get("bypass", {})
btools = bp.get("tools", {})
off = sorted(k for k, v in btools.items() if v is not True)
print("bypass=%s|off=%s|provider=%s|model=%s|allow=%s|ctx=%s" % (
    bp.get("name"), ",".join(off),
    bp.get("default_model", {}).get("provider"),
    bp.get("default_model", {}).get("model"),
    cfg.get("agent", {}).get("tool_permissions", {}).get("default"),
    ",".join(sorted(cfg.get("context_servers", {})))))
PY
)"
    backups="$(ls "$scratch"/.config/zed/settings.json.autoos-backup-* 2>/dev/null | wc -l)"
    leaks="$(grep -cE 'sk-[A-Za-z0-9]{10,}|_API_KEY|REPLACE' "$scratch/.config/zed/settings.json" || true)"
    rm -rf "$scratch"
    # Two prints = one newline inside $report; assert each line separately.
    line1="$(printf '%s' "$report" | sed -n '1p')"
    line2="$(printf '%s' "$report" | sed -n '2p')"
    assert_eq "$line1" \
        "mine|http://127.0.0.1:20128/v1|catalog-ok|False|http://127.0.0.1:4000/v1|False|pin-ok,pin-ok,pin-ok,pin-ok,pin-ok,pin-ok"
    assert_eq "$line2" \
        "bypass=bypass|off=|provider=autoos-omniroute|model=t1-orchestrator|allow=allow|ctx=autoos-agent,context7,graphify,omnigraph,playwright,serena"
    assert_eq "leaks=$leaks" "leaks=0"
    # Two runs share second-precision backup names: same second -> 1 file,
    # straddling a boundary -> 2. Either proves backup-before-edit; an exact
    # count would flake on wall-clock timing.
    if (( backups >= 1 )); then pass; else fail "no backup written"; fi
fi

if it "zed routing without key env warns and stays key-free"; then
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/zed"
    printf '{}' >"$scratch/.config/zed/settings.json"
    out="$( ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; unset AUTOOS_OMNIROUTE_API_KEY AUTOOS_LITELLM_API_KEY; route_zed_to_proxy ) 2>&1)"
    report="$(python3 - "$scratch/.config/zed/settings.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
oc = cfg.get("language_models", {}).get("openai_compatible", {})
print("%s|%s" % ("api_key" in oc.get("autoos-omniroute", {}),
                 "api_key" in oc.get("autoos-litellm", {})))
PY
)"
    rm -rf "$scratch"
    if [[ "$report" == "False|False" && "$out" == *"stays hidden"* ]]; then pass
    else fail "report=$report out=$(printf '%s' "$out" | tail -2)"; fi
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
    report="$(python3 - 2>&1 <<'PY'
import re, io
text = io.open("configuration/litellm/config.yaml", encoding="utf-8").read()
groups = set(re.findall(r"(?m)^\s*-\s*model_name:\s*(\S+)\s*$", text))
need = {"t1-orchestrator", "t1-orchestrator-paid", "t1-orchestrator-free-only", "t2-worker", "t2-worker-paid", "t2-worker-free-only", "t3-driver", "t3-driver-paid", "t3-driver-free-only"}
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
# llama-3.3-70b left groq's free tier in 2026-08 and must never come back.
# cerebras is NOT stale: combos.json re-admits it as a credit/paid-capable
# leg (2026-09-20), so the old "any cerebras routed" check no longer holds.
# Its presence in the managed blocks is asserted against combos.json by
# tools/sync-router-tiers.py --check.
if [m for m in models if "llama-3.3-70b" in m]:
    problems.append("stale")
if "drop_params" not in text or "os.environ/LITELLM_MASTER_KEY" not in text:
    problems.append("settings")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "free-only litellm groups mirror combos minus gateway-only legs"; then
    # The *-free-only groups are hand-curated (not sync-managed), so this
    # pins them to combos.json with hardcoded expectations: same legs in the
    # same order, and the ONLY permitted drops are the known OAuth-bridge
    # legs LiteLLM has no transport or key for. Anything else missing - or
    # any silently added leg - is drift. Free-only groups must also stay out
    # of fallbacks (zero spend means fail loudly, never bill silently).
    report="$(python3 - 2>&1 <<'PY'
import io, json, re
combos = {c["name"]: c["models"]
          for c in json.load(open("configuration/omniroute/combos.json",
                                   encoding="utf-8"))["combos"]}
# LiteLLM model strings: OmniRoute provider/model passes through except the
# OpenAI-compatible gateways (zen, cheaperinference -> openai/ + api_base).
# Only pre-existing, proven mappings appear here - no new inference.
# known_drops is deliberately hardcoded, NOT derived from GATEWAY_ONLY in
# tools/sync-router-tiers.py: the test must stay an independent second
# opinion - deriving it would make tool and test agree by construction.
transport = {"opencode-zen": "openai", "cheaperinference": "openai"}
def litellm_model(ref):
    prov, model = ref.split("/", 1)
    return "%s/%s" % (transport.get(prov, prov), model)
known_drops = {"antigravity/gemini-3.7-flash-medium",
               "antigravity/claude-opus-4-6-thinking"}
text = io.open("configuration/litellm/config.yaml", encoding="utf-8").read()
problems = []
for tier in ("t1-orchestrator-free-only", "t2-worker-free-only",
             "t3-driver-free-only"):
    want = [litellm_model(r) for r in combos[tier] if r not in known_drops]
    got = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^[ \t]*-[ \t]*model_name:[ \t]*(\S+)[ \t]*$", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"^[ \t]*model:[ \t]*(\S+)[ \t]*$", line)
        if m and cur == tier:
            got.append(m.group(1))
    if got != want:
        problems.append(tier + "-drift")
    # Every combos leg must be mirrored or declared-dropped: a new leg that
    # is neither fails here until known_drops explicitly acknowledges it.
    # (Checked against got, the parsed file - not against want above, which
    # would make this tautological.)
    unmirrored = [r for r in combos[tier] if litellm_model(r) not in got]
    if set(unmirrored) - known_drops:
        problems.append(tier + "-unpinned-drop")
fb = text.split("fallbacks:", 1)[1]
for tier in ("t1-orchestrator-free-only", "t2-worker-free-only",
             "t3-driver-free-only"):
    if re.search(r"(?m)^\s*-\s*" + tier + r"\s*:", fb):
        problems.append(tier + "-in-fallbacks")
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
h = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))
p = oc["providers"]
pins = sorted(
    "pin-ok" if h["mcp_servers"][name]["package"] in " ".join(spec.get("command", []))
    else "MISSING:" + name
    for name, spec in oc["mcp"]["servers"].items())
print("%s|%s|%s|%s|%s|%s" % (
    oc["model"],
    p["omniroute"]["settings"]["baseURL"],
    ",".join(sorted(p["omniroute"]["models"].keys())),
    "litellm" in p,
    ",".join(sorted(oc["mcp"]["servers"].keys())),
    ",".join(pins)))
PY
)"
    assert_eq "$report" \
        "omniroute/t1-orchestrator|http://127.0.0.1:20128/v1|auto,auto/cheap,auto/smart,deepseek-v4.1-flash,gemini-3.8-flash,opus-4-6,spark-1.3-contributor,t1-orchestrator,t1-orchestrator-clean,t1-orchestrator-free-only,t2-orchestrator,t2-worker,t2-worker-clean,t2-worker-free-only,t3-driver,t3-driver-clean,t3-driver-free-only,t4-rag|True|autoos-agent,context7,graphify,omnigraph,playwright,serena|pin-ok,pin-ok,pin-ok,pin-ok,pin-ok,pin-ok"
fi

if it "openhands template has tiers and no secrets"; then
    ok=1
    for s in '\[llm\]' '\[llm.t1-orchestrator\]' '\[llm.t2-worker\]' '\[llm.t3-driver\]' '\[llm.t1-orchestrator-clean\]' '\[llm.t2-worker-clean\]' '\[llm.t3-driver-clean\]' '\[llm.t4-rag\]' '\[llm.litellm-t1-orchestrator\]' '\[llm.litellm-t2-worker\]' '\[llm.litellm-t3-driver\]' '\[llm.draft_editor\]' '\[agent.CodeActAgent\]'; do
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

if it "openhands launch is detached, probed and stale-settings safe"; then
    # -it fails without a TTY and foreground never returns; schema_version 6
    # settings 500 the current image. Both fixed 2026-09-21 - pin the shape.
    # 2026-09-23: the guard repairs in place instead of deleting (the old
    # shape moved the whole file aside and lost the user's profiles/keys).
    # It must target agent_settings.schema_version (top-level stays 2-3 on
    # broken files too) and strip the agent-canvas `enabled` MCP keys.
    ok=1
    for f in configuration/start-stack.ps1 configuration/start-stack.sh; do
        grep -q 'docker run -d ' "$f" || { ok=0; echo "not detached: $f" >&2; }
        grep -q 'docker run -it' "$f" && { ok=0; echo "still -it: $f" >&2; }
        grep -q 'agent_settings.schema_version\|agent_settings.*schema_version' "$f" || { ok=0; echo "wrong schema_version level: $f" >&2; }
        grep -q 'autoos-backup' "$f" || { ok=0; echo "no backup: $f" >&2; }
        grep -q 'docker logs openhands-app' "$f" || { ok=0; echo "no probe: $f" >&2; }
        # Tier profiles re-project from the spec on every start (never stale).
        grep -q 'sync-openhands-profiles' "$f" || { ok=0; echo "never syncs: $f" >&2; }
        # Repair, not delete: only versions NEWER than the image (> 4) clamp
        # down; older payloads keep theirs so the image's own migrations run.
        grep -qE '> 4|-gt 4' "$f" || { ok=0; echo "wrong clamp version: $f" >&2; }
        # The agent-canvas `enabled` MCP key 500s the image (extra_forbidden).
        grep -q 'enabled' "$f" || { ok=0; echo "no enabled-key strip: $f" >&2; }
        # Repair, not delete, for parseable files: the version-mismatch path
        # must clamp in place (at most one settings.json removal remains: the
        # unparseable-file fallback, where there is nothing to preserve).
        grep -qE 'schema_version.?\s?\]?\s?= 4' "$f" || { ok=0; echo "no in-place clamp: $f" >&2; }
        [[ "$(grep -cE 'Remove-Item \$ohSettings|rm -f "\$oh_settings"' "$f")" -le 1 ]] || { ok=0; echo "deletes parseable user settings: $f" >&2; }
    done
    if (( ok )); then pass; else fail "openhands launch shape regressed"; fi
fi

if it "OpenHands mcp_config carries no enabled key (live schema forbids it)"; then
    # The live MCPServer model (additionalProperties false) rejects 'enabled'
    # with extra_forbidden and 500s /api/v1/settings (measured 2026-09-21).
    # Only the double-quoted form is asserted: the opencode writer's
    # single-quoted 'enabled': True is schema-legal and stays.
    n="$(grep -c '"enabled": True' lib/linux/install.sh || true)"
    assert_eq "$n" "0"
fi

if it "mirror-litellm-env projects keys without printing them"; then
    tmp="$(mktemp -d)"
    printf 'groq: dummy-groq-1\nmeta: dummy-meta-2\ncohere: dummy-cohere-3\nSambaNova: dummy-samba-4\nzen: REPLACE_WITH_ZEN_KEY\n' >"$tmp/api-keys.yml"
    out="$(python3 tools/mirror-litellm-env.py --keys "$tmp/api-keys.yml" --env "$tmp/.env" 2>&1)"
    rc=$?
    leaked="$(printf '%s' "$out" | grep -c 'dummy-' || true)"
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "mirror rc=$rc" >&2; }
    [[ "$leaked" == "0" ]] || { ok=0; echo "value leaked" >&2; }
    grep -q '^GROQ_API_KEY=dummy-groq-1$' "$tmp/.env" || { ok=0; echo "groq" >&2; }
    grep -q '^META_API_KEY=dummy-meta-2$' "$tmp/.env" || { ok=0; echo "meta" >&2; }
    grep -q '^COHERE_API_KEY=dummy-cohere-3$' "$tmp/.env" || { ok=0; echo "cohere" >&2; }
    grep -q '^SAMBANOVA_API_KEY=dummy-samba-4$' "$tmp/.env" || { ok=0; echo "SambaNova case" >&2; }
    grep -q '^OPENCODE_ZEN_API_KEY=REPLACE' "$tmp/.env" || { ok=0; echo "zen placeholder" >&2; }
    grep -qE '^LITELLM_MASTER_KEY=[^R]' "$tmp/.env" || { ok=0; echo "master" >&2; }
    python3 tools/mirror-litellm-env.py --check --keys "$tmp/api-keys.yml" --env "$tmp/.env" >/dev/null 2>&1
    [[ $? -eq 0 ]] || { ok=0; echo "fresh check failed" >&2; }
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "mirror tool broken"; fi
fi

if it "no committed secrets in router files"; then    # Report file:line only - a failure message must never echo the value it
    # found into logs or a terminal shared with anyone else.
    hits="$(grep -rnE 'sk-[A-Za-z0-9]{10,}' configuration/litellm/config.yaml \
        configuration/litellm/.env.example opencode.jsonc \
        configuration/openhands/config.toml 2>/dev/null | cut -d: -f1,2 || true)"
    # .env.example is the sanctioned placeholder pattern; only real-looking keys fail.
    if [[ -z "$hits" ]]; then pass; else fail "credential-shaped value at: $hits"; fi
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

# ─── Omnigraph reachability (measured 2026-09-24) ──────────────────────────
# The bridge refuses to start without OMNIGRAPH_BASE_URL and OMNIGRAPH_GRAPH_ID
# (the client only says "Connection closed"), and answers health/tools-list
# without a token, so clients show "connected" while every read fails.

if it "omnigraph config shape passes the offline probe"; then
    out="$(python3 tools/check-omnigraph.py --offline 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "exit $rc: $out"; fi
fi

if it "omnigraph probe rejects a token value and a missing graph id"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/catalog"
    cp catalog/agent-harness.json "$tmp/catalog/"
    pin="$(python3 -c 'import json;print(json.load(open("catalog/agent-harness.json"))["mcp_servers"]["omnigraph"]["package"])')"
    printf '{"mcpServers":{"omnigraph":{"command":"npx","args":["-y","%s"],"env":{"OMNIGRAPH_BASE_URL":"http://localhost:8080","OMNIGRAPH_TOKEN":"not-a-reference"}}}}' "$pin" >"$tmp/.mcp.json"
    printf '{"mcp":{"servers":{"omnigraph":{"type":"local","command":["npx","-y","%s"],"environment":{"OMNIGRAPH_BASE_URL":"http://localhost:8080","OMNIGRAPH_TOKEN":"x"}}}}}' "$pin" >"$tmp/opencode.jsonc"
    out="$(python3 tools/check-omnigraph.py --offline --root "$tmp" 2>&1)"; rc=$?
    rm -rf "$tmp"
    ok=1
    [[ $rc -eq 1 ]] || ok=0
    for frag in "OMNIGRAPH_GRAPH_ID is empty" "never a value" "not the file"; do
        [[ "$out" == *"$frag"* ]] || { ok=0; echo "missing: $frag" >&2; }
    done
    [[ "$out" != *"not-a-reference"* ]] || ok=0
    if (( ok )); then pass; else fail "rc=$rc out=$out"; fi
fi

if it "omnigraph probe warns when a local .env names a different graph id"; then
    # agent-skills' trust_worktree.py writes OMNIGRAPH_GRAPH_ID=<folder name>
    # (here "AutoOS") into worktree .env files; graph ids are case-sensitive and
    # the server only has "autoos". Nothing in this repo reads that .env, so it
    # warns instead of failing, but it must never go unnoticed.
    tmp="$(mktemp -d)"
    cp -r catalog "$tmp/" && cp .mcp.json opencode.jsonc "$tmp/"
    printf 'OMNIGRAPH_GRAPH_ID=AutoOS\n' >"$tmp/.env"
    out="$(python3 tools/check-omnigraph.py --offline --root "$tmp" 2>&1)"; rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 0 && "$out" == *"WARN"*"'AutoOS'"*"'autoos'"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "omnigraph live probe names a missing token, a rejected token and a missing graph"; then
    # A stub server on a random port stands in for omnigraph-server; nothing
    # leaves the machine and nothing is installed.
    stub="$(mktemp -d)"
    cat >"$stub/stub.py" <<'PY'
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def _auth(self):
        return self.headers.get("Authorization") == "Bearer good-token"
    def do_GET(self):
        if self.path == "/healthz": return self._send(200, {"status": "ok", "version": "stub"})
        if not self._auth(): return self._send(401, {"error": "invalid bearer token"})
        if self.path == "/graphs/autoos/schema": return self._send(200, {"source": "node Project {}"})
        return self._send(404, {"error": "graph not found"})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if not self._auth(): return self._send(401, {"error": "invalid bearer token"})
        # /query takes `query` (server 0.8.1); `query_source` is /read's field.
        if self.path != "/graphs/autoos/query" or "query" not in body:
            return self._send(422, {"error": "missing field `query`"})
        return self._send(200, {"rows": [{"p.slug": "autoos"}]})
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_address[1]))
srv.serve_forever()
PY
    python3 "$stub/stub.py" "$stub/port" & stub_pid=$!
    for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -s "$stub/port" ]] && break; sleep 0.2; done
    base="http://127.0.0.1:$(cat "$stub/port" 2>/dev/null)"
    probe() { env -u OMNIGRAPH_TOKEN AUTOOS_OMNIGRAPH_ENV_FILE="$stub/none.env" "$@" python3 tools/check-omnigraph.py --base-url "$base" 2>&1; }
    good="$(probe OMNIGRAPH_TOKEN=good-token)"; good_rc=$?
    none="$(probe)"; none_rc=$?
    bad="$(probe OMNIGRAPH_TOKEN=wrong)"; bad_rc=$?
    nograph="$(env -u OMNIGRAPH_TOKEN OMNIGRAPH_TOKEN=good-token python3 tools/check-omnigraph.py --base-url "$base" --graph nope 2>&1)"; nograph_rc=$?
    printf 'OMNIGRAPH_TOKEN=good-token\n' >"$stub/file.env"
    fromfile="$(env -u OMNIGRAPH_TOKEN AUTOOS_OMNIGRAPH_ENV_FILE="$stub/file.env" python3 tools/check-omnigraph.py --base-url "$base" 2>&1)"; file_rc=$?
    kill "$stub_pid" 2>/dev/null; wait "$stub_pid" 2>/dev/null
    rm -rf "$stub"
    unset -f probe
    got="$good_rc$none_rc$bad_rc$nograph_rc$file_rc"
    ok=1
    [[ "$got" == "01110" ]] || ok=0
    [[ "$good" == *"project autoos"* ]] || ok=0
    [[ "$none" == *"OMNIGRAPH_TOKEN is not set"* ]] || ok=0
    [[ "$bad" == *"401"* ]] || ok=0
    [[ "$nograph" == *"'nope' does not exist"* ]] || ok=0
    [[ "$fromfile" == *"token from"* ]] || ok=0
    [[ "$good$bad$fromfile" != *"good-token"* ]] || ok=0
    if (( ok )); then pass; else fail "rc=$got good=[$good] none=[$none] bad=[$bad] nograph=[$nograph] file=[$fromfile]"; fi
fi

if it "both healthchecks probe omnigraph through the live probe"; then
    ok=1
    for f in configuration/healthcheck.sh configuration/healthcheck.ps1; do
        grep -q 'check-omnigraph.py' "$f" || { ok=0; echo "$f does not run the probe" >&2; }
    done
    if (( ok )); then pass; else fail "an omnigraph probe is missing"; fi
fi

if it "zed's omnigraph context server carries the base URL and graph id the bridge requires"; then
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0 route_zed_to_proxy >/dev/null 2>&1 )
    got="$(python3 -c "
import json,sys
e=json.load(open(sys.argv[1],encoding='utf-8'))['context_servers']['omnigraph'].get('env',{})
print(bool(e.get('OMNIGRAPH_BASE_URL')), e.get('OMNIGRAPH_GRAPH_ID'), 'OMNIGRAPH_TOKEN' in e)
" "$scratch/.config/zed/settings.json" 2>&1)"
    rm -rf "$scratch"
    assert_eq "$got" "True autoos False"
fi

# The omnigraph_url answer decides where every client's bridge points.
# install_agent_skills honoured it; the Zed writer hardcoded localhost:8080, so
# a machine with a remote omnigraph got Zed pointed at nothing. One helper,
# omnigraph_base_url, now derives it for both.
# zed_omni_url <answer or empty>: the OMNIGRAPH_BASE_URL route_zed_to_proxy writes.
zed_omni_url() {
    local scratch out
    scratch="$(mktemp -d)"
    (
        AUTOOS_ANSWERS=()
        [[ -z "$1" ]] || AUTOOS_ANSWERS[omnigraph_url]="$1"
        SYS_HOME="$scratch" AUTOOS_DRY_RUN=0 route_zed_to_proxy >/dev/null 2>&1
    )
    out="$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['context_servers']['omnigraph']['env']['OMNIGRAPH_BASE_URL'])
" "$scratch/.config/zed/settings.json" 2>&1)"
    rm -rf "$scratch"
    printf '%s' "$out"
}

if it "zed: the omnigraph entry uses the omnigraph_url answer"; then
    ok=1
    got="$(zed_omni_url "https://graph.example.invalid:9000/")"
    [[ "$got" == "https://graph.example.invalid:9000" ]] || { ok=0; echo "the Zed entry says [$got], not the answer without its trailing slash" >&2; }
    helper="$( ( AUTOOS_ANSWERS=(); AUTOOS_ANSWERS[omnigraph_url]="https://graph.example.invalid:9000/"; omnigraph_base_url ) 2>&1)"
    [[ "$helper" == "https://graph.example.invalid:9000" ]] || { ok=0; echo "omnigraph_base_url says [$helper]" >&2; }
    if (( ok )); then pass; else fail "the Zed omnigraph entry ignores the omnigraph_url answer"; fi
fi

if it "zed: the omnigraph entry defaults to localhost:8080"; then
    ok=1
    got="$(zed_omni_url "")"
    [[ "$got" == "http://localhost:8080" ]] || { ok=0; echo "the Zed entry says [$got]" >&2; }
    # Both consumers take the default from the one helper, so they cannot drift.
    helper="$( ( AUTOOS_ANSWERS=(); omnigraph_base_url ) 2>&1)"
    [[ "$helper" == "http://localhost:8080" ]] || { ok=0; echo "omnigraph_base_url says [$helper]" >&2; }
    [[ "$(grep -c '\$(omnigraph_base_url)' lib/linux/install.sh)" -ge 2 ]] \
        || { ok=0; echo "the helper is not called by both install_agent_skills and route_zed_to_proxy" >&2; }
    if (( ok )); then pass; else fail "the omnigraph default differs between the Zed writer and install_agent_skills"; fi
fi

if it "the omnigraph env file is private, merged, linked for systemd, and stable on a re-run"; then
    tmp="$(mktemp -d)"
    printf 'KEEP_ME=1\nOMNIGRAPH_BASE_URL=http://old.invalid\n' >"$tmp/.autoos-omnigraph.env"
    touch "$tmp/.bashrc"
    run_env() { ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 OMNIGRAPH_TOKEN="test-token-value"; docker() { return 1; }; write_omnigraph_env "http://localhost:8080" ) 2>&1; }
    first="$(run_env)"; second="$(run_env)"
    mode="$(stat -c '%a' "$tmp/.autoos-omnigraph.env")"
    body="$(sort "$tmp/.autoos-omnigraph.env" | tr '\n' ' ')"
    link="$(readlink "$tmp/.config/environment.d/60-autoos-omnigraph.conf")"
    backups="$(ls "$tmp"/.autoos-omnigraph.env.autoos-backup-* 2>/dev/null | wc -l)"
    bmode="$(stat -c '%a' "$tmp"/.autoos-omnigraph.env.autoos-backup-* 2>/dev/null | head -1)"
    rc_blocks="$(grep -c 'AutoOS:omnigraph-env' "$tmp/.bashrc")"
    rm -rf "$tmp"
    unset -f run_env
    assert_eq "$mode|$body|${link##*/}|$backups|$bmode|$rc_blocks|$([[ "$second" == *unchanged* ]] && echo stable)|$([[ "$first$second" == *test-token-value* ]] && echo LEAK)" \
        "600|KEEP_ME=1 OMNIGRAPH_BASE_URL=http://localhost:8080 OMNIGRAPH_TOKEN=test-token-value |.autoos-omnigraph.env|1|600|1|stable|"
fi

# Review finding 2026-09-25: the rc line used to `set -a; . file`, so a value
# holding $(...) executed in every new shell. It must read the three keys
# literally, in bash AND zsh, and replace an older AutoOS line in place.
if it "the omnigraph rc line loads values literally and replaces the old sourcing line"; then
    tmp="$(mktemp -d)"
    printf 'OMNIGRAPH_BASE_URL=http://x$(touch %s/pwned)\nOMNIGRAPH_TOKEN=tok=with=equals\nOTHER=$(touch %s/pwned2)\n' "$tmp" "$tmp" >"$tmp/.autoos-omnigraph.env"
    # An rc file that already carries the v1 sourcing line, as an older run wrote it.
    printf '# mine\n[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env\n' >"$tmp/.bashrc"
    cp "$tmp/.bashrc" "$tmp/.zshrc"
    # Keep the env file as crafted: only the rc handling is under test here.
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 OMNIGRAPH_TOKEN="tok=with=equals"; docker() { return 1; }
      write_omnigraph_env 'http://x$(touch '"$tmp"'/pwned)' ) >/dev/null 2>&1
    got_b="$(env -i HOME="$tmp" bash -c ". \"$tmp/.bashrc\"; printf '%s|%s' \"\$OMNIGRAPH_BASE_URL\" \"\$OMNIGRAPH_TOKEN\"" 2>&1)"
    got_z=""
    if command -v zsh >/dev/null; then
        got_z="$(env -i HOME="$tmp" zsh -f -c ". \"$tmp/.zshrc\"; printf '%s|%s' \"\$OMNIGRAPH_BASE_URL\" \"\$OMNIGRAPH_TOKEN\"" 2>&1)"
    fi
    v1="$(grep -c 'set -a; \.' "$tmp/.bashrc" || true)"
    v2="$(grep -c 'AutoOS:omnigraph-env-v2' "$tmp/.bashrc" || true)"
    pwned="$(ls "$tmp"/pwned* 2>/dev/null | wc -l)"
    rm -rf "$tmp"
    want='http://x$(touch '"${tmp}"'/pwned)|tok=with=equals'
    ok=1
    [[ "$pwned" == 0 ]] || { ok=0; echo "a value was executed" >&2; }
    [[ "$got_b" == "$want" ]] || { ok=0; echo "bash got: $got_b" >&2; }
    [[ -z "$got_z" || "$got_z" == "$want" ]] || { ok=0; echo "zsh got: $got_z" >&2; }
    [[ "$v1" == 0 && "$v2" == 1 ]] || { ok=0; echo "v1=$v1 v2=$v2 (old line not replaced)" >&2; }
    if (( ok )); then pass; else fail "the omnigraph rc line is not a literal reader"; fi
fi

# Re-review 2026-09-25: CRLF values, a last line without a newline, and a file
# that carries BOTH the v1 sourcing line and the v2 reader (v1 must go).
if it "the omnigraph rc reader copes with CRLF and a missing last newline, and purges v1 next to v2"; then
    tmp="$(mktemp -d)"
    printf 'OMNIGRAPH_BASE_URL=http://crlf\r\nOMNIGRAPH_TOKEN=last-line-no-newline' >"$tmp/.autoos-omnigraph.env"
    v1='[ -z "${OMNIGRAPH_TOKEN:-}" ] && [ -r "$HOME/.autoos-omnigraph.env" ] && { set -a; . "$HOME/.autoos-omnigraph.env"; set +a; }  # AutoOS:omnigraph-env'
    printf '%s\n' "$v1" >"$tmp/.bashrc"
    # First write_omnigraph_env run adds v2 by replacing v1; then plant v1 again
    # next to v2 (a stale copy) and run once more: v1 must be purged.
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0 OMNIGRAPH_TOKEN=""; docker() { return 1; }
      replace_or_append_marked_line "$tmp/.bashrc" "AutoOS:omnigraph-env" "AutoOS:omnigraph-env-v2" "$(omnigraph_rc_line)" ) >/dev/null 2>&1
    printf '%s\n' "$v1" >>"$tmp/.bashrc"
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      replace_or_append_marked_line "$tmp/.bashrc" "AutoOS:omnigraph-env" "AutoOS:omnigraph-env-v2" "unused" ) >/dev/null 2>&1
    got="$(env -i HOME="$tmp" bash -c ". \"$tmp/.bashrc\"; printf '%s|%s' \"\$OMNIGRAPH_BASE_URL\" \"\$OMNIGRAPH_TOKEN\"" 2>&1)"
    v1n="$(grep -c 'set -a; \.' "$tmp/.bashrc" || true)"
    v2n="$(grep -c 'AutoOS:omnigraph-env-v2' "$tmp/.bashrc" || true)"
    rm -rf "$tmp"
    assert_eq "$got|$v1n|$v2n" "http://crlf|last-line-no-newline|0|1"
fi

if it "the omnigraph token falls back to the local server container, else is reported missing"; then
    tmp="$(mktemp -d)"
    ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      unset OMNIGRAPH_TOKEN
      has_cmd() { [[ "$1" == docker ]] || command -v "$1" >/dev/null 2>&1; }
      docker() { printf 'PATH=/bin\nOMNIGRAPH_SERVER_BEARER_TOKEN=from-container\n'; }
      write_omnigraph_env "http://localhost:8080" >/dev/null 2>&1 )
    from_container="$(grep -c '^OMNIGRAPH_TOKEN=from-container$' "$tmp/.autoos-omnigraph.env")"
    rm -rf "$tmp"; tmp="$(mktemp -d)"
    out="$( ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=0
      unset OMNIGRAPH_TOKEN
      docker() { return 1; }
      write_omnigraph_env "http://localhost:8080" ) 2>&1)"
    no_token_line="$(grep -c '^OMNIGRAPH_TOKEN=' "$tmp/.autoos-omnigraph.env")"
    rm -rf "$tmp"
    assert_eq "$from_container|$no_token_line|$([[ "$out" == *"OMNIGRAPH_TOKEN is not set"* ]] && echo warned)" "1|0|warned"
fi

if it "the omnigraph env file is announced, not written, in a dry run"; then
    tmp="$(mktemp -d)"
    out="$( ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=1; write_omnigraph_env "http://localhost:8080" ) 2>&1)"
    left="$(find "$tmp" -mindepth 1 | wc -l)"
    rm -rf "$tmp"
    assert_eq "$left|$([[ "$out" == *"would write"* ]] && echo announced)" "0|announced"
fi

if it "antigravity's omnigraph entry pins a graph id (the bridge refuses to start without one)"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/Documents/code/agent-skills"
    spec="$( (
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset OMNIGRAPH_GRAPH_ID OMNIGRAPH_TOKEN
        clone_or_update() { :; }
        install_mcp_graphify() { :; }; install_mcp_serena() { :; }
        install_mcp_playwright() { :; }; install_mcp_context7() { :; }
        mcp_has_server() { return 1; }; enable_project_mcp_server() { :; }
        write_omnigraph_env() { :; }
        register_antigravity_mcp_server() { [[ "$1" == omnigraph ]] && printf '%s\n' "$2" >&3; }
        omnigraph_readiness() { return 0; }
        answer() { echo ""; }
        install_agent_skills >/dev/null 2>&1
    ) 3>&1 )"
    rm -rf "$tmp"
    got="$(printf '%s' "$spec" | python3 -c "import json,sys;e=json.load(sys.stdin)['env'];print(e.get('OMNIGRAPH_GRAPH_ID'),'OMNIGRAPH_TOKEN' in e)" 2>&1)"
    assert_eq "$got" "autoos False"
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
        AUTOOS_ROOT="$tmp"
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

if it "repo skills link into project .claude/skills and win as skills source"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.agents/skills/demo-skill"
    printf -- '---\nname: demo-skill\ndescription: demo\n---\n' >"$tmp/.agents/skills/demo-skill/SKILL.md"
    mkdir -p "$tmp/Documents/code/agent-skills/skills/old-skill"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        AUTOOS_ROOT="$tmp"
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
    [[ -e "$tmp/.claude/skills/demo-skill/SKILL.md" ]] || ok=0
    [[ "$(AUTOOS_ROOT="$tmp" autoos_skills_source)" == "$tmp/.agents/skills" ]] || ok=0
    rm -rf "$tmp"
    if (( ok )); then pass; else fail "vendored skills did not win or were not linked"; fi
fi

# ─── OpenHands: repo skills mirrored per skill, idempotent settings writer ──
# oh_setup_run <home> <repo>: setup_openhands_config in a hermetic subshell -
# scratch HOME and AUTOOS_ROOT, no gateway or provider key, no network - with
# stdout and stderr merged. AUTOOS_KEYS_FILE points at a file that does not
# exist so the repo's own keys file is never consulted.
oh_setup_run() {
    local home="$1" repo="$2"
    mkdir -p "$home"
    (
        SYS_HOME="$home"; AUTOOS_DRY_RUN=0
        if [[ -n "$repo" ]]; then AUTOOS_ROOT="$repo"; else unset AUTOOS_ROOT; fi
        unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY OMNIGRAPH_TOKEN
        unset AUTOOS_OMNIROUTE_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY
        export AUTOOS_KEYS_FILE="$home/no-keys.yml"
        curl() { return 6; }
        setup_openhands_config
    ) 2>&1
}

# oh_skill_repo <repo>: a scratch AUTOOS_ROOT with two skills (alpha, beta) and
# one directory without a SKILL.md (nofile) that is not a skill.
oh_skill_repo() {
    local repo="$1" n
    for n in alpha beta; do
        mkdir -p "$repo/.agents/skills/$n"
        printf -- '---\nname: %s\ndescription: demo\n---\n' "$n" >"$repo/.agents/skills/$n/SKILL.md"
    done
    mkdir -p "$repo/.agents/skills/nofile"
    printf 'not a skill\n' >"$repo/.agents/skills/nofile/README.md"
}

if it "openhands: links every repo skill into ~/.openhands/skills"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    dest="$tmp/home/.openhands/skills"; problems=""
    { [[ -d "$dest" && ! -L "$dest" ]] || problems+="[$dest is not a real directory] "; }
    for n in alpha beta; do
        [[ -L "$dest/$n" && "$(readlink "$dest/$n")" == "$tmp/repo/.agents/skills/$n" ]] \
            || problems+="[$n is not a link to the repo skill (got: $(readlink "$dest/$n" 2>/dev/null || echo none))] "
        [[ -f "$dest/$n/SKILL.md" ]] || problems+="[$n/SKILL.md unreadable through the link] "
        [[ "$out" == *"linked $n"* ]] || problems+="[no 'linked $n' line] "
    done
    { [[ ! -e "$dest/nofile" && ! -L "$dest/nofile" ]] || problems+="[nofile (no SKILL.md) was linked] "; }
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a second run reports skipped and changes nothing"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    oh_setup_run "$tmp/home" "$tmp/repo" >/dev/null
    dest="$tmp/home/.openhands/skills"
    before="$(stat -c '%y' "$dest"; readlink "$dest/alpha" "$dest/beta")"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    after="$(stat -c '%y' "$dest"; readlink "$dest/alpha" "$dest/beta")"
    problems=""
    [[ "$before" == "$after" ]] || problems+="[the skills directory or a link changed: $before -> $after] "
    grep -Eq '(^|[^[:alnum:]])(linked|repointed) [a-z]' <<<"$out" && problems+="[second run printed a linked/repointed line] "
    [[ "$out" == *skipped* ]] || problems+="[second run never says skipped] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: keeps a user's own skill"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    dest="$tmp/home/.openhands/skills"
    mkdir -p "$dest/alpha" "$dest/mine"
    printf 'my own alpha\n' >"$dest/alpha/SKILL.md"
    printf 'my own skill\n'  >"$dest/mine/SKILL.md"
    ln -s /nonexistent-user-skill "$dest/foreign"
    snap() { ( cd "$dest" && find alpha mine -type f -exec sha256sum {} + | sort; readlink foreign; ls -A ) ; }
    before="$(snap)"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    after="$(snap)"
    problems=""
    [[ -d "$dest/alpha" && ! -L "$dest/alpha" ]] || problems+="[the user's own alpha is no longer a real directory] "
    [[ "$(readlink "$dest/beta" 2>/dev/null)" == "$tmp/repo/.agents/skills/beta" ]] || problems+="[beta was not linked next to the user's skills] "
    # beta is the one new entry: nothing else may differ from before.
    [[ "$(grep -v '^beta$' <<<"$after")" == "$before" ]] || problems+="[the user's skills changed: $before -> $after] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: repairs a dangling link into the repo"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    dest="$tmp/home/.openhands/skills"; mkdir -p "$dest"
    ln -s "$tmp/repo/.agents/skills/alpha-renamed" "$dest/alpha"   # ours (into the repo), dangling
    ln -s /nonexistent-elsewhere/beta "$dest/beta"                 # not ours, dangling: hands off
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    problems=""
    [[ "$(readlink "$dest/alpha")" == "$tmp/repo/.agents/skills/alpha" ]] || problems+="[alpha still points at $(readlink "$dest/alpha")] "
    [[ -f "$dest/alpha/SKILL.md" ]] || problems+="[alpha does not resolve after the repair] "
    [[ "$(readlink "$dest/beta")" == "/nonexistent-elsewhere/beta" ]] || problems+="[a foreign dangling link was rewritten to $(readlink "$dest/beta")] "
    [[ "$out" == *"repointed alpha"* ]] || problems+="[no 'repointed alpha' line] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: an existing whole-dir symlink is not written through"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    mkdir -p "$tmp/clone-skills" "$tmp/home/.openhands"
    ln -s "$tmp/clone-skills" "$tmp/home/.openhands/skills"     # the old whole-dir layout
    repo_before="$(ls -A "$tmp/repo/.agents/skills")"
    out="$(oh_setup_run "$tmp/home" "$tmp/repo")"
    problems=""
    [[ "$(readlink "$tmp/home/.openhands/skills")" == "$tmp/clone-skills" ]] || problems+="[the whole-dir link was changed] "
    [[ -z "$(ls -A "$tmp/clone-skills")" ]] || problems+="[links were written through it into the clone: $(ls -A "$tmp/clone-skills" | tr '\n' ' ')] "
    [[ "$(ls -A "$tmp/repo/.agents/skills")" == "$repo_before" ]] || problems+="[the repo's skills directory gained entries] "
    [[ "$(grep -c 'rm ' <<<"$out")" == 1 ]] || problems+="[expected one warning naming the rm command, got $(grep -c 'rm ' <<<"$out")] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a dry run links nothing"; then
    tmp="$(mktemp -d)"; oh_skill_repo "$tmp/repo"
    out="$( ( AUTOOS_DRY_RUN=1; link_skill_dirs "$tmp/repo/.agents/skills" "$tmp/home/.openhands/skills" ) 2>&1 )"
    problems=""
    [[ ! -e "$tmp/home" ]] || problems+="[a dry run created $tmp/home] "
    [[ "$out" == *"would link"* ]] || problems+="[no 'would link' line: $out] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: second run of the settings writer takes no backup and reports unchanged"; then
    tmp="$(mktemp -d)"
    oh="$tmp/home/.openhands"
    # Name, size and mtime of every backup: a bare count cannot tell a skipped
    # second run from one that overwrote a same-second backup.
    oh_backups() { find "$oh" -maxdepth 1 -name 'settings.json.autoos-backup-*' -printf '%f %s %T@\n' | sort; }
    oh_setup_run "$tmp/home" "" >/dev/null
    b1="$(oh_backups)"
    sum1="$(sha256sum "$oh/settings.json" | cut -d' ' -f1)"
    prof1="$(stat -c '%y' "$oh/profiles/openrouter-free.json")"
    out="$(oh_setup_run "$tmp/home" "")"
    b2="$(oh_backups)"
    sum2="$(sha256sum "$oh/settings.json" | cut -d' ' -f1)"
    prof2="$(stat -c '%y' "$oh/profiles/openrouter-free.json")"
    problems=""
    [[ "$b1" == "$b2" ]] || problems+="[the second run took or overwrote a settings.json backup: {$b1} -> {$b2}] "
    [[ "$sum1" == "$sum2" ]] || problems+="[the second run rewrote settings.json] "
    [[ "$prof1" == "$prof2" ]] || problems+="[the second run rewrote an identical profile] "
    [[ "$out" == *unchanged* ]] || problems+="[the second run never says unchanged] "
    [[ "$out" != *"written to"* ]] || problems+="[the second run still says 'written to'] "
    rm -rf "$tmp"
    if [[ -z "$problems" ]]; then pass; else fail "$problems"; fi
fi

if it "openhands: a BOM'd settings.json keeps the user's keys"; then
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/home/.openhands"
    printf '\xef\xbb\xbf{"custom_user_key": "keep-me", "schema_version": 2}' >"$tmp/home/.openhands/settings.json"
    oh_setup_run "$tmp/home" "" >/dev/null
    got="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8-sig")); print(d.get("custom_user_key"), "agent_settings" in d)' "$tmp/home/.openhands/settings.json" 2>&1)"
    rm -rf "$tmp"
    assert_eq "$got" "keep-me True"
fi

if it "openhands: a backup of settings.json never overwrites an earlier one"; then
    tmp="$(mktemp -d)"
    oh="$tmp/home/.openhands"; mkdir -p "$oh"
    seed1='{"user_key": "first original"}'
    seed2='{"user_key": "second original"}'
    printf '%s' "$seed1" >"$oh/settings.json"
    oh_setup_run "$tmp/home" "" >/dev/null
    printf '%s' "$seed2" >"$oh/settings.json"
    oh_setup_run "$tmp/home" "" >/dev/null
    have1=0; have2=0; b=""
    for b in "$oh"/settings.json.autoos-backup-*; do
        [[ -f "$b" ]] || continue
        [[ "$(cat "$b")" == "$seed1" ]] && have1=1
        [[ "$(cat "$b")" == "$seed2" ]] && have2=1
    done
    rm -rf "$tmp"
    if (( have1 && have2 )); then pass
    else fail "a backup holding the user's file was overwritten (first original kept: $have1, second original kept: $have2)"; fi
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

if it "custom_is_installed detects a populated native CAO home"; then
    tmp="$(mktemp -d)"
    (
        SYS_HOME="$tmp"
        mkdir -p "$tmp/.cao/db"
        custom_is_installed wsl-agent-home
    )
    rc_full=$?
    (
        SYS_HOME="$tmp"
        rm -rf "$tmp/.cao"
        custom_is_installed wsl-agent-home
    )
    rc_empty=$?
    rm -rf "$tmp"
    if [[ $rc_full -eq 0 && $rc_empty -ne 0 ]]; then pass
    else fail "rc_full=$rc_full rc_empty=$rc_empty"; fi
fi

if it "setup_wsl_agent_home is a no-op off WSL and dry-runnable on WSL"; then
    tmp="$(mktemp -d)"
    ( SYS_HOME="$tmp" SYS_IS_WSL=0 AUTOOS_DRY_RUN=0 setup_wsl_agent_home >/dev/null 2>&1 )
    rc_off=$?
    [[ -e "$tmp/.bashrc" ]] && rc_off=99
    ( SYS_HOME="$tmp" SYS_IS_WSL=1 AUTOOS_DRY_RUN=1 setup_wsl_agent_home >/dev/null 2>&1 )
    rc_dry=$?
    [[ -e "$tmp/.bashrc" ]] && rc_dry=99
    rm -rf "$tmp"
    if [[ $rc_off -eq 0 && $rc_dry -eq 0 ]]; then pass
    else fail "rc_off=$rc_off rc_dry=$rc_dry"; fi
fi

# ─── Qoder (catalog entries + MCP wiring) ───────────────────────────────────
describe "qoder"

if it "qoder catalog entries exist on linux and macos with the right shape"; then
    # catalog_validate already enforces provider/package/verify and the
    # manual-needs-homepage-and-notes rule for every entry; this pins the
    # Qoder-specific choices the task fixes: script provider + qodercli --version
    # + setup_qoder_mcp + no arch for the CLI, and manual for the desktop app
    # (no Homebrew cask and no apt/snap package exist for it).
    report="$(python3 - 2>&1 <<'PY'
import json
def comp(path, cid):
    data = json.load(open(path, encoding="utf-8"))
    for g in data["categories"]:
        for c in g["components"]:
            if c["id"] == cid:
                return c
    return None
problems = []
for path in ("catalog/linux.json", "catalog/macos.json"):
    cli = comp(path, "qodercli")
    desk = comp(path, "qoder-desktop")
    if not cli:
        problems.append(path + ":no-qodercli")
    else:
        if cli.get("provider") != "script": problems.append(path + ":cli-provider")
        if cli.get("package") != "qodercli": problems.append(path + ":cli-package")
        if cli.get("verify") != "qodercli --version": problems.append(path + ":cli-verify")
        if cli.get("postInstall") != "setup_qoder_mcp": problems.append(path + ":cli-postinstall")
        if "arch" in cli: problems.append(path + ":cli-has-arch")
    if not desk:
        problems.append(path + ":no-qoder-desktop")
    else:
        if desk.get("provider") != "manual": problems.append(path + ":desk-provider")
        if not desk.get("homepage"): problems.append(path + ":desk-homepage")
        if not desk.get("notes"): problems.append(path + ":desk-notes")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "qodercli postInstall names a shell function that exists"; then
    # postInstall is NOT schema-validated: run_post_install only warns when the
    # named function is missing, so a typo silently does nothing on a real
    # machine. Assert the catalogued name really resolves to a defined function.
    ok=1
    for path in catalog/linux.json catalog/macos.json; do
        fn="$(python3 -c "
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
for g in data['categories']:
    for c in g['components']:
        if c['id'] == 'qodercli':
            print(c.get('postInstall', ''))
" "$path")"
        [[ "$fn" == "setup_qoder_mcp" ]] || { ok=0; echo "$path postInstall=$fn" >&2; }
        declare -F "$fn" >/dev/null || { ok=0; echo "$path: $fn is not defined" >&2; }
    done
    if (( ok )); then pass; else fail "qodercli postInstall does not resolve to a defined function"; fi
fi

if it "qodercli detection rides the script provider"; then
    ok=1
    # The dispatch case itself is covered by "script dispatch covers qodercli
    # and devin-cli" above; what this pins is the detect.sh side, without which
    # an already-installed Qoder would be reinstalled instead of skipped.
    grep -q 'qodercli)        has_bin qodercli' lib/linux/detect.sh || ok=0
    # has_bin is stubbed so the test never depends on what this machine happens
    # to have installed.
    ( has_bin() { return 1; }; script_is_installed qodercli ) >/dev/null 2>&1 && ok=0
    ( has_bin() { return 0; }; script_is_installed qodercli ) >/dev/null 2>&1 || ok=0
    if (( ok )); then pass; else fail "qodercli dispatch or detection is broken"; fi
fi

if it "setup_qoder_mcp writes nothing in a dry run"; then
    tmp="$(mktemp -d)"
    out="$( ( SYS_HOME="$tmp" AUTOOS_DRY_RUN=1; setup_qoder_mcp ) 2>&1)"
    created="$(find "$tmp" -mindepth 1 2>/dev/null | wc -l | tr -d ' ')"
    rm -rf "$tmp"
    if [[ "$out" == *"would"* && "$created" == "0" ]]; then pass
    else fail "dry run created $created file(s); out=$(printf '%s' "$out" | tail -2)"; fi
fi

if it "setup_qoder_mcp registers four servers and never omnigraph"; then
    tmp="$(mktemp -d)"
    stub="$(mktemp -d)"
    log="$tmp/calls.log"
    export AUTOOS_QODER_STUB_LOG="$log"
    # Fake qodercli: record every invocation, report nothing registered yet (so
    # every server is attempted), and never write to the fake home. Asserts on
    # the PLANNED command, not on system state (AGENTS.md section 5).
    cat >"$stub/qodercli" <<'EOS'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$AUTOOS_QODER_STUB_LOG"
exit 0
EOS
    chmod +x "$stub/qodercli"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        PATH="$stub:$PATH"
        unset CONTEXT7_API_KEY
        answer() { printf ''; }
        setup_qoder_mcp >/dev/null 2>&1
    )
    added="$(grep -oE 'add-json [a-z0-9]+' "$log" 2>/dev/null | awk '{print $2}' | sort | tr '\n' ',' | sed 's/,$//')"
    # omnigraph's graph is per-repo, so it must never become a machine-global
    # user-scope entry (this repo ships it at project scope in .mcp.json).
    omni="$(grep -c 'omnigraph' "$log" 2>/dev/null || true)"
    # The pin must flow from the harness at runtime, never a literal in lib/.
    serena_pin="$(python3 -c "import json;print(json.load(open('catalog/agent-harness.json',encoding='utf-8'))['mcp_servers']['serena']['package'])")"
    pin_ok=0; grep -qF -- "$serena_pin" "$log" 2>/dev/null && pin_ok=1
    rm -rf "$tmp" "$stub"
    unset AUTOOS_QODER_STUB_LOG
    if [[ "$added" == "context7,graphify,playwright,serena" && "$omni" == "0" && "$pin_ok" == "1" ]]; then pass
    else fail "added=[$added] omnigraph_lines=$omni pin_ok=$pin_ok (expected context7,graphify,playwright,serena / 0 / 1)"; fi
fi

if it "setup_qoder_mcp skips servers that are already registered"; then
    tmp="$(mktemp -d)"
    stub="$(mktemp -d)"
    log="$tmp/calls.log"
    export AUTOOS_QODER_STUB_LOG="$log"
    # Fake qodercli whose `mcp list` reports all four as present, so a second
    # run must add nothing (idempotent - AGENTS.md section 4).
    cat >"$stub/qodercli" <<'EOS'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$AUTOOS_QODER_STUB_LOG"
if [[ "${1:-}" == "mcp" && "${2:-}" == "list" ]]; then
    printf 'serena: connected\ngraphify: connected\nplaywright: connected\ncontext7: connected\n'
fi
exit 0
EOS
    chmod +x "$stub/qodercli"
    (
        SYS_HOME="$tmp"
        AUTOOS_DRY_RUN=0
        PATH="$stub:$PATH"
        unset CONTEXT7_API_KEY
        answer() { printf ''; }
        setup_qoder_mcp >/dev/null 2>&1
    )
    adds="$(grep -c 'add-json' "$log" 2>/dev/null || true)"
    rm -rf "$tmp" "$stub"
    unset AUTOOS_QODER_STUB_LOG
    if [[ "$adds" == "0" ]]; then pass
    else fail "expected 0 add-json calls when every server already exists, got $adds"; fi
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

# install_claude_autostart renders each unit to a temp file and, when it differs
# from the installed one, moves it over it. That silently dropped a local edit
# of the unit (AGENTS.md hard rule 5: back a user-owned file up first).
#
# autostart_run <scratch>: one install_claude_autostart run against a scratch
# home. systemctl and loginctl are stubs, so nothing is enabled or started.
autostart_run() {
    local sb="$1"
    (
        AUTOOS_ROOT="$ROOT"; SYS_HOME="$sb/home"; AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""
        systemctl() { return 0; }
        loginctl() { printf 'yes\n'; }
        install_claude_autostart
    ) 2>&1
}

if it "claude-autostart: a changed unit is backed up before it is replaced"; then
    sb="$(mktemp -d)"; ud="$sb/home/.config/systemd/user"; u=claude-sessions-snapshot.service
    mkdir -p "$ud"; printf 'LOCAL EDIT: do not lose me\n' >"$ud/$u"
    out="$(autostart_run "$sb")"; rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rc=$rc: ${out:0:300}" >&2; }
    mapfile -t baks < <(find "$ud" -name '*.autoos-backup-*')
    (( ${#baks[@]} == 1 )) || { ok=0; echo "expected exactly one backup, found ${#baks[@]}" >&2; }
    [[ "${baks[0]:-}" == "$ud/$u.autoos-backup-"* ]] || { ok=0; echo "backup is named [${baks[0]:-}]" >&2; }
    [[ "$(cat "${baks[0]:-/nonexistent}" 2>/dev/null)" == "LOCAL EDIT: do not lose me" ]] \
        || { ok=0; echo "the backup does not hold the user's edit: [$(cat "${baks[0]:-/nonexistent}" 2>/dev/null)]" >&2; }
    if grep -q '^ExecStart=' "$ud/$u" && ! grep -q 'LOCAL EDIT' "$ud/$u"; then :
    else ok=0; echo "the new unit is not in place" >&2; fi
    [[ "$out" == *"installed $u"* ]] || { ok=0; echo "no 'installed' line: ${out:0:300}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "a changed autostart unit was replaced without a backup"; fi
fi

if it 'claude-autostart: an unchanged unit takes no backup and reports "already current"'; then
    sb="$(mktemp -d)"; ud="$sb/home/.config/systemd/user"; ok=1
    autostart_run "$sb" >/dev/null                   # fresh machine: three units, no backup
    [[ "$(find "$ud" -name '*.autoos-backup-*' | wc -l | tr -d ' ')" == 0 ]] || { ok=0; echo "a fresh install took a backup" >&2; }
    before="$(cksum "$ud"/claude-sessions-*)"
    out="$(autostart_run "$sb")"; rc=$?              # second run: nothing changes
    (( rc == 0 )) || { ok=0; echo "second run rc=$rc" >&2; }
    [[ "$(grep -c 'already current' <<<"$out")" == 3 ]] || { ok=0; echo "second run not current for all three: ${out:0:400}" >&2; }
    [[ "$(find "$ud" -name '*.autoos-backup-*' | wc -l | tr -d ' ')" == 0 ]] || { ok=0; echo "an unchanged run took a backup" >&2; }
    [[ "$(cksum "$ud"/claude-sessions-*)" == "$before" ]] || { ok=0; echo "an unchanged unit was rewritten" >&2; }
    # One unit edited by hand: only that one is backed up; the other two stay untouched.
    printf '# local tweak\n' >>"$ud/claude-sessions-snapshot.timer"
    edited="$(cat "$ud/claude-sessions-snapshot.timer")"
    out="$(autostart_run "$sb")"
    mapfile -t baks < <(find "$ud" -name '*.autoos-backup-*')
    (( ${#baks[@]} == 1 )) || { ok=0; echo "expected one backup for the edited unit, found ${#baks[@]}" >&2; }
    [[ "${baks[0]:-}" == "$ud/claude-sessions-snapshot.timer.autoos-backup-"* && "$(cat "${baks[0]:-/nonexistent}" 2>/dev/null)" == "$edited" ]] \
        || { ok=0; echo "the edited timer was not the one backed up, or its bytes differ: ${baks[0]:-none}" >&2; }
    [[ "$(grep -c 'already current' <<<"$out")" == 2 ]] || { ok=0; echo "the two untouched units are not reported current: ${out:0:400}" >&2; }
    rm -rf "$sb"
    if (( ok )); then pass; else fail "the autostart backup is missing for a changed unit or taken for an unchanged one"; fi
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
    # ollama_models, antigravity_url - the last since removed) were Linux-only
    # prompts, so on Windows it rendered boxes whose answers no installer
    # would ever read.
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

if it "imagecache_wheelhouse_packages reads oterm's package name off its own postInstall, from the real catalog (cache)"; then
    # Same principle as imagecache_packages' catalog cross-check above, one
    # layer up for pip: the wheelhouse builder must never hand-maintain a
    # second copy of "which package is oterm" — it has to come from the
    # catalog entries that actually install it (install_oterm /
    # Install-AutoOSOterm), or the two can drift silently.
    got="$(imagecache_wheelhouse_packages catalog/linux.json | tr -d '\r')"
    assert_eq "$got" "oterm"
fi

if it "imagecache_wheelhouse_build downloads oterm's full dependency tree and verifies it resolves fully offline (cache)"; then
    # Hermetic: python3/pip is faked so this touches no real network and
    # installs nothing — the fake still routes catalog-reading invocations
    # (the "python3 - <script>" shape imagecache_wheelhouse_packages uses)
    # through the REAL python3, since faking a JSON parser in bash would just
    # be a second, driftable copy of the parsing logic this test exists to
    # avoid.
    real_python3="$(command -v python3)"
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"
    fake_pip_log="$tmp_stick/pip.log"
    : > "$fake_pip_log"

    cat > "$fakebin/python3" <<EOS
#!/usr/bin/env bash
if [[ "\$1" == "-m" && "\$2" == "pip" ]]; then
    shift 2
    case "\$1" in
        --version) echo "pip 24.0 from fake"; exit 0 ;;
        download)
            shift
            dest=""
            while [[ \$# -gt 0 ]]; do
                case "\$1" in --dest) dest="\$2"; shift 2 ;; *) shift ;; esac
            done
            printf 'download %s\n' "\$*" >>"$fake_pip_log"
            mkdir -p "\$dest"
            # A real \`pip download\` fetches the whole dependency tree, not
            # just the leaf package — this fake drops a stand-in for a few of
            # oterm's actual transitive dependencies too, so a test asserting
            # "more than one file landed" is asserting something real.
            : > "\$dest/oterm-0.24.0-py3-none-any.whl"
            : > "\$dest/textual-8.2.8-py3-none-any.whl"
            : > "\$dest/pydantic-2.13.5-py3-none-any.whl"
            exit 0
            ;;
        install)
            printf 'install %s\n' "\$*" >>"$fake_pip_log"
            exit 0
            ;;
        *) exit 1 ;;
    esac
fi
exec "$real_python3" "\$@"
EOS
    chmod +x "$fakebin/python3"

    out1="$(PATH="$fakebin:$PATH" imagecache_wheelhouse_build "$tmp_stick" catalog/linux.json 2>&1)"; rc1=$?
    file_count=$(find "$tmp_stick/rescue/wheels" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) 2>/dev/null | wc -l | tr -d ' ')
    downloads="$(grep -c '^download ' "$fake_pip_log" || true)"

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc1 -eq 0 && "$file_count" -eq 3 && "$downloads" -eq 1 \
        && "$out1" == *"verified"*"resolve fully from"*"with no network"* ]]; then
        pass
    else
        fail "rc1=$rc1 file_count=$file_count downloads=$downloads out1='${out1:0:400}'"
    fi
fi

if it "imagecache_wheelhouse_build reports nothing new on a second run against an already-complete cache (cache)"; then
    real_python3="$(command -v python3)"
    tmp_stick="$(mktemp -d)"
    fakebin="$(mktemp -d)"

    cat > "$fakebin/python3" <<EOS
#!/usr/bin/env bash
if [[ "\$1" == "-m" && "\$2" == "pip" ]]; then
    shift 2
    case "\$1" in
        --version) echo "pip 24.0 from fake"; exit 0 ;;
        download)
            shift
            dest=""
            while [[ \$# -gt 0 ]]; do
                case "\$1" in --dest) dest="\$2"; shift 2 ;; *) shift ;; esac
            done
            mkdir -p "\$dest"
            # Idempotent stand-in for pip's own behaviour: a file already
            # sitting in --dest is left alone, nothing new appears.
            : > "\$dest/oterm-0.24.0-py3-none-any.whl"
            exit 0
            ;;
        install) exit 0 ;;
        *) exit 1 ;;
    esac
fi
exec "$real_python3" "\$@"
EOS
    chmod +x "$fakebin/python3"

    PATH="$fakebin:$PATH" imagecache_wheelhouse_build "$tmp_stick" catalog/linux.json >/dev/null 2>&1
    out2="$(PATH="$fakebin:$PATH" imagecache_wheelhouse_build "$tmp_stick" catalog/linux.json 2>&1)"; rc2=$?

    rm -rf "$tmp_stick" "$fakebin"

    if [[ $rc2 -eq 0 && "$out2" == *"unchanged"*"already cached"* ]]; then
        pass
    else
        fail "rc2=$rc2 out2='${out2:0:400}'"
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

# ─── image_resolve (Task 4B) ────────────────────────────────────────────────
# Bridges catalog/images.json's index+file-regex entries to a concrete
# download URL (the gap that left --create-usb unable to actually fetch an
# image). Every test here serves its fixtures through a real loopback HTTP
# server (tests/helpers/image_index_fixtures/) rather than a file:// URL:
# image_resolve joins relative hrefs against the page's own URL itself, and
# that join has to see a real directory-shaped base URL the same way a live
# index server would hand one back - see _start_test_http_server's own
# comment above for why file:// does not stand in for that on this suite's
# Windows/Git-Bash hosts. No test here reaches the real internet.
describe "image_resolve"

read -r _img_srv_pid _img_srv_port < <(_start_test_http_server "$ROOT/tests/helpers/image_index_fixtures")
_img_cache="$(mktemp -d)"

# _image_resolve_test_catalog <id> <fixture-subdir> <file-regex> [sums] [sig] [leaf]
# Writes a one-entry scratch images.json pointing `index` at this test run's
# loopback fixture server, and echoes its path. AUTOOS_ROOT/catalog/images.json
# itself is never touched (constraint: catalog/*.json stays off limits) -
# image_resolve's optional second argument exists for exactly this. <leaf>
# mirrors the catalog's optional `leaf` field (fedora-workstation: the ISO
# sits three directories below the chosen version dir) - omitted, it is left
# out of the entry entirely so every existing caller above is unaffected.
_image_resolve_test_catalog() {
    local id="$1" subdir="$2" file_re="$3" sums="${4:-SHA256SUMS}" sig="${5:--}" leaf="${6:-}"
    local f; f="$(mktemp)"
    python3 - "$f" "$id" "http://127.0.0.1:${_img_srv_port}/${subdir}/" "$file_re" "$sums" "$sig" "$leaf" <<'PY'
import json, sys
f, id_, index, file_re, sums, sig, leaf = sys.argv[1:8]
entry = {"id": id_, "index": index, "file": file_re, "sums": sums, "sig": sig}
if leaf:
    entry["leaf"] = leaf
with open(f, "w", encoding="utf-8") as fh:
    json.dump({"images": [entry]}, fh)
PY
    echo "$f"
}

if it "image_resolve: an LTS entry picks the current LTS release, never an interim one"; then
    cat_json="$(_image_resolve_test_catalog ubuntu-desktop-lts ubuntu_releases \
        'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' SHA256SUMS SHA256SUMS.gpg)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ubuntu-desktop-lts "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/ubuntu_releases/26.04.1"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/ubuntu-26.04.1-desktop-amd64.iso"* \
        && "$out" == *"file=ubuntu-26.04.1-desktop-amd64.iso"* \
        && "$out" == *"sums=${base}/SHA256SUMS"* && "$out" == *"sig=${base}/SHA256SUMS.gpg"* \
        && "$out" != *"25.10"* && "$out" != *"24.04.5"* && "$out" != *"22.04.5"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: Debian's current/ symlink shape resolves with no directory recursion"; then
    cat_json="$(_image_resolve_test_catalog debian-netinst-stable debian_iso_cd \
        'debian-[0-9]+\.[0-9]+\.[0-9]+-amd64-netinst\.iso$' SHA256SUMS SHA256SUMS.sign)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve debian-netinst-stable "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/debian_iso_cd"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/debian-13.1.0-amd64-netinst.iso"* \
        && "$out" == *"file=debian-13.1.0-amd64-netinst.iso"* \
        && "$out" == *"sums=${base}/SHA256SUMS"* && "$out" == *"sig=${base}/SHA256SUMS.sign"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: a pattern matching nothing fails loudly and non-zero, never guesses"; then
    cat_json="$(_image_resolve_test_catalog nothing-here no_match 'nonexistent-[0-9]+\.iso$' - -)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve nothing-here "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"matched nothing"* ]]; then pass; else fail "rc=$rc out=$out"; fi
    rm -f "$cat_json"
fi

if it "image_resolve: a pattern matching several files that differ by more than a version is an error, never a silent first-match"; then
    # Same version, different arch - a regex broader than its author meant.
    # No ordering rule applies here, unlike the point-release case below.
    cat_json="$(_image_resolve_test_catalog ambiguous multiple_match \
        'debian-[0-9]+\.[0-9]+\.[0-9]+-(amd64|arm64)-netinst\.iso$' - -)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ambiguous "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"matched multiple"* \
        && "$out" == *"debian-13.2.0-amd64-netinst.iso"* && "$out" == *"debian-13.2.0-arm64-netinst.iso"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: a point release listed beside its .0 release in one directory resolves to the highest version (live 26.04.1 shape)"; then
    # Found on the first real run, 2026-09-17: releases.ubuntu.com/26.04.1/
    # lists ubuntu-26.04-desktop-amd64.iso AND ubuntu-26.04.1-desktop-amd64.iso.
    # Names identical except for one dotted version number are the same
    # artefact at different point releases - the highest wins. Missing
    # components compare as 0, so 26.04 < 26.04.1.
    cat_json="$(_image_resolve_test_catalog ubuntu-desktop-lts ubuntu_point_release \
        'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' SHA256SUMS SHA256SUMS.gpg)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ubuntu-desktop-lts "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/ubuntu_point_release"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/ubuntu-26.04.1-desktop-amd64.iso"* \
        && "$out" == *"file=ubuntu-26.04.1-desktop-amd64.iso"* && "$out" == *"sums=${base}/SHA256SUMS"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: an -lts id with only interim directories present fails loudly instead of picking one"; then
    cat_json="$(_image_resolve_test_catalog ubuntu-desktop-lts ubuntu_only_interim \
        'ubuntu-[0-9]+\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$' SHA256SUMS SHA256SUMS.gpg)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve ubuntu-desktop-lts "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no LTS release directory"* ]]; then pass; else fail "rc=$rc out=$out"; fi
    rm -f "$cat_json"
fi

if it "image_resolve: a fedora-shaped entry resolves via 'leaf' to the ISO and CHECKSUM three directories below the chosen version dir, picking the highest of 43/44 (image_resolve)"; then
    # Real upstream shape (verified 2026-09-18): releases/ lists bare-integer
    # version dirs (44/, no dot - VERSION_DIR must accept that), and the ISO
    # is three levels below the chosen one (Workstation/x86_64/iso/) - `leaf`
    # is what lets a single recursion step land there directly.
    cat_json="$(_image_resolve_test_catalog fedora-workstation fedora_releases \
        'Fedora-Workstation-Live-[0-9]+-[0-9.]+\.x86_64\.iso$' \
        'Fedora-Workstation-[0-9]+-[0-9.]+-x86_64-CHECKSUM$' - 'Workstation/x86_64/iso/')"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve fedora-workstation "$cat_json" 2>&1)"; rc=$?
    base="http://127.0.0.1:${_img_srv_port}/fedora_releases/44/Workstation/x86_64/iso"
    if [[ $rc -eq 0 && "$out" == *"url=${base}/Fedora-Workstation-Live-44-1.7.x86_64.iso"* \
        && "$out" == *"file=Fedora-Workstation-Live-44-1.7.x86_64.iso"* \
        && "$out" == *"sums=${base}/Fedora-Workstation-44-1.7-x86_64-CHECKSUM"* \
        && "$out" != *"/43/"* && "$out" != *"/test/"* ]]; then
        pass
    else
        fail "rc=$rc out=$out"
    fi
    rm -f "$cat_json"
fi

if it "image_resolve: a bare-integer version directory (Fedora's '44/') is never LTS-eligible - it has no .04, so it must be ineligible (image_resolve)"; then
    cat_json="$(_image_resolve_test_catalog fedora-workstation-lts fedora_releases \
        'Fedora-Workstation-Live-[0-9]+-[0-9.]+\.x86_64\.iso$' \
        'Fedora-Workstation-[0-9]+-[0-9.]+-x86_64-CHECKSUM$' -)"
    out="$(AUTOOS_CACHE_DIR="$_img_cache" image_resolve fedora-workstation-lts "$cat_json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no LTS release directory"* ]]; then pass; else fail "rc=$rc out=$out"; fi
    rm -f "$cat_json"
fi

if it "image_resolve: custom-url is a specific refusal, not a crash"; then
    out="$(image_resolve custom-url "$ROOT/catalog/images.json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"UI affordance"* ]]; then pass; else fail "rc=$rc out=$out"; fi
fi

if it "image_resolve: custom-local is a specific refusal, not a crash"; then
    out="$(image_resolve custom-local "$ROOT/catalog/images.json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"UI affordance"* ]]; then pass; else fail "rc=$rc out=$out"; fi
fi

if it "image_resolve: an unknown image id is a clear error, not a crash"; then
    out="$(image_resolve totally-not-a-real-image-id "$ROOT/catalog/images.json" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no catalog entry"* ]]; then pass; else fail "rc=$rc out=$out"; fi
fi

kill "$_img_srv_pid" 2>/dev/null
rm -rf "$_img_cache"

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
    # "Immediately before" is the property, not "first": the download step
    # now precedes it (an hour-long fetch must not sit between the
    # re-verify and the write), so this locates the re-verify line and
    # checks the very next line is the first destructive one.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_DISK_ID=serial-XYZ \
           usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    reverify_no="$(printf '%s\n' "$out" | grep -n '^usb_reverify ' | head -1 | cut -d: -f1)"
    destructive_no="$(printf '%s\n' "$out" | grep -n '^Ventoy2Disk.sh ' | head -1 | cut -d: -f1)"
    reverify_line="$(printf '%s\n' "$out" | sed -n "${reverify_no:-0}p")"
    if [[ -n "$reverify_no" && -n "$destructive_no" && $((reverify_no + 1)) -eq "$destructive_no" \
          && "$reverify_line" == "usb_reverify /dev/sdb unmounted "*" serial-XYZ" ]]; then pass
    else fail "reverify at line ${reverify_no:-none}, first destructive at ${destructive_no:-none}: $out"; fi
fi

if it "usb_plan: the first plan line fetches and verifies the image, before anything touches the device (usb plan fetch)"; then
    # The seam the 2026-09-17 handoff names as the single gap stopping the
    # headline feature: image_resolve existed, fetch_verified existed, and
    # usb_plan named a cache path nobody ever filled. The plan must now
    # carry the download as its own first step so usb_execute runs it.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_CACHE_DIR=/scratch/images \
           usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    first_line="$(printf '%s\n' "$out" | head -1)"
    assert_eq "$first_line" "usb_fetch_image ubuntu-desktop-lts /scratch/images/ubuntu-desktop-lts.iso"
fi

if it "usb_plan: a live-persistent ventoy plan ends with the persistence step, an installer plan has none (usb plan persistence)"; then
    live="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_plan ubuntu-desktop-lts live-persistent ventoy /dev/sdb)"
    inst="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_plan ubuntu-desktop-lts installer ventoy /dev/sdb)"
    last_live="$(printf '%s\n' "$live" | tail -1)"
    prev_live="$(printf '%s\n' "$live" | tail -2 | head -1)"
    if [[ "$last_live" == "usb_ventoy_add_persistence /dev/sdb" && "$prev_live" == "usb_copy_image /dev/sdb "* \
          && "$inst" != *"usb_ventoy_add_persistence"* ]]; then pass
    else fail "live=$live / inst=$inst"; fi
fi

if it "usb_execute: a usb_ventoy_add_persistence line with the wrong word count is refused, never run (usb plan persistence)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"usb_ventoy_add_persistence /dev/sdb 16 extra" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"malformed usb_ventoy_add_persistence step"* ]] && pass || fail "rc=$rc out=$out"
fi

if it "usb_plan: every engine's plan starts with the fetch step and never names the cache path before it (usb plan fetch)"; then
    # uefi-copy has its own fixture (mounted FAT32); the raw/native engine
    # shares good_stick. Both must lead with the same fetch line.
    out_native="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_plan proxmox-ve installer native /dev/sdb)"
    out_uefi="$(AUTOOS_FAKE_LSBLK="$(fake_usb usb_fat32_mounted)" usb_plan ubuntu-desktop-lts installer uefi-copy /dev/sdb)"
    if [[ "$(printf '%s\n' "$out_native" | head -1)" == "usb_fetch_image proxmox-ve "* \
          && "$(printf '%s\n' "$out_uefi" | head -1)" == "usb_fetch_image ubuntu-desktop-lts "* ]]; then pass
    else fail "native: $(printf '%s\n' "$out_native" | head -1) / uefi: $(printf '%s\n' "$out_uefi" | head -1)"; fi
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

if it "usb chooser: image items name every real image and exclude the custom pseudo-entries (usb chooser)"; then
    out="$(usb_chooser_image_items)"
    if [[ "$out" == *"ubuntu-desktop-lts|Ubuntu Desktop (LTS)|installer, live-persistent|6 GB"* && "$out" != *"custom-url"* && "$out" != *"custom-local"* ]]; then pass
    else fail "$out"; fi
fi

if it "usb chooser: engine items list what this platform can build for the kind, and the terminal may offer rufus, badged interactive (usb chooser)"; then
    linux="$(SYS_OS=linux SYS_ARCH=x64 usb_chooser_engine_items installer hybrid)"
    windows="$(SYS_OS=windows SYS_ARCH=x64 usb_chooser_engine_items installer hybrid)"
    if [[ "$linux" == *"ventoy|Ventoy|"*"|default"* && "$linux" == *"uefi-copy|"* && "$linux" == *"native|"* \
          && "$linux" != *"rufus"* && "$linux" != *"wsl|"* \
          && "$windows" == *"rufus|"*"|interactive"* && "$windows" == *"wsl|"* ]]; then pass
    else fail "linux=$linux / windows=$windows"; fi
fi

if it "usb chooser: a raw image is only offered engines that write raw (usb chooser)"; then
    out="$(SYS_OS=linux SYS_ARCH=x64 usb_chooser_engine_items installer raw)"
    [[ "$out" == *"native|"* && "$out" != *"ventoy"* && "$out" != *"uefi-copy"* ]] && pass || fail "$out"
fi

if it "usb chooser: a non-interactive run takes the default at every step and never consents to the write itself (usb chooser)"; then
    # The chooser sets globals, so it must run in THIS shell (a $(...)
    # capture would be a subshell and lose them); its output goes to a file.
    chooser_log="$(mktemp)"
    fake_lsblk="$(fake_usb good_stick)"
    res="$( export AUTOOS_NONINTERACTIVE=1 AUTOOS_FAKE_LSBLK="$fake_lsblk" SYS_OS=linux SYS_ARCH=x64
            USB_IMAGE=""; USB_KIND=""; USB_ENGINE=""; USB_DEVICE=""; USB_WIPE=0
            usb_choose_interactively >"$chooser_log" 2>&1; rc=$?
            printf '%s|%s|%s|%s|%s|%s' "$USB_IMAGE" "$USB_KIND" "$USB_ENGINE" "$USB_DEVICE" "$USB_WIPE" "$rc" )"
    out="$(cat "$chooser_log")"; rm -f "$chooser_log"
    IFS='|' read -r img kind eng dev wipe rc <<<"$res"
    if [[ "$img" == "ubuntu-desktop-lts" && "$kind" == "installer" && "$eng" == "ventoy" && "$dev" == "/dev/sdb" \
          && "$wipe" == "0" && "$rc" != "0" && "$out" == *"Not confirmed"* ]]; then pass
    else fail "$res / $out"; fi
fi

if it "usb chooser: a dry run asks for no confirmation and shows the summary naming the device, model and size (usb chooser)"; then
    fake_lsblk="$(fake_usb good_stick)"
    out="$( export AUTOOS_NONINTERACTIVE=1 AUTOOS_DRY_RUN=1 AUTOOS_FAKE_LSBLK="$fake_lsblk" SYS_OS=linux SYS_ARCH=x64
            USB_IMAGE=""; USB_KIND=""; USB_ENGINE=""; USB_DEVICE=""; USB_WIPE=0
            usb_choose_interactively 2>&1 && echo RC0 )"
    if [[ "$out" == *"RC0"* && "$out" == *"/dev/sdb"* && "$out" == *"GB"* && "$out" != *"Not confirmed"* ]]; then pass
    else fail "$out"; fi
fi

if it "usb chooser: the profile menu offers Create installer USB and a non-interactive --create-usb without flags still refuses (usb chooser)"; then
    out="$(AUTOOS_NONINTERACTIVE=1 bash setup.sh --create-usb --no-color 2>&1)"; rc=$?
    if grep -q 'create-usb|Create installer USB' setup.sh && [[ $rc -eq 2 && "$out" == *"requires --image"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb --undo states plainly that a USB write cannot be undone"; then
    out="$( bash setup.sh --undo --dry-run 2>&1 )"
    assert_contains "$out" "USB"
fi

# ─── setup.sh actually executing a plan ────────────────────────────────────
# Until 2026-09-17 a --create-usb WITHOUT --dry-run printed the plan and
# exited 0 - a "create" that created nothing; usb_execute had no caller
# outside the tests. These two prove the wiring without a dry-run flag,
# which AGENTS.md §5 otherwise reserves for end-to-end tests: both are
# guaranteed to run no step - the first refuses before usb_execute is ever
# reached, the second is stopped by AUTOOS_FORCE_FAIL on the very first
# step - and both run against the synthetic lsblk fixture, and assert the
# scratch cache dir came out exactly as it went in.
if it "usb_plan: a real --create-usb without --wipe-target-disk shows the plan, then refuses before running any step (usb execute)"; then
    scratch="$(mktemp -d)"
    out="$(AUTOOS_CACHE_DIR="$scratch" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_UID=0 \
           bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
           --engine ventoy --usb-device /dev/sdb --no-color 2>&1)"; rc=$?
    listing="$(find "$scratch" | sort)"
    if [[ $rc -ne 0 && "$out" == *"--wipe-target-disk"* && "$out" == *"usb_fetch_image"* \
          && "$out" != *"TRACE"* && "$out" != *"Ready to boot"* && "$listing" == "$scratch" ]]; then pass
    else fail "rc=$rc listing=[$listing] out=$out"; fi
    rm -rf "$scratch"
fi

if it "usb_plan: a real --create-usb with --wipe-target-disk hands the printed plan to usb_execute (usb execute)"; then
    scratch="$(mktemp -d)"
    out="$(AUTOOS_CACHE_DIR="$scratch" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FAKE_UID=0 \
           AUTOOS_FORCE_FAIL=1 AUTOOS_TRACE=1 \
           bash setup.sh --create-usb --image ubuntu-desktop-lts --kind installer \
           --engine ventoy --usb-device /dev/sdb --wipe-target-disk --no-color 2>&1)"; rc=$?
    listing="$(find "$scratch" | sort)"
    # The forced failure must land on the FETCH step - proof that the first
    # thing a real run does is download, not touch the device.
    if [[ $rc -ne 0 && "$out" == *"TRACE usb_fetch_image ubuntu-desktop-lts"* \
          && "$out" == *"forced failure"* && "$out" != *"Ready to boot"* && "$listing" == "$scratch" ]]; then pass
    else fail "rc=$rc listing=[$listing] out=$out"; fi
    rm -rf "$scratch"
fi

# ─── custom-url / custom-local (an image the catalog does not describe) ────
# usb_plan used to refuse both pseudo-entries outright: they have no
# writeMode, so the engine check failed on an empty string. They are now
# driven by --image-url / --image-path / --image-sha256 / --write-mode
# (the USB_IMAGE_* / USB_WRITE_MODE globals here). Every name carries "usb"
# and "custom" so --filter usb and --filter custom both reach them.
describe "usb custom image"

_custom_img="$(mktemp)"
printf 'AUTOOS CUSTOM TEST IMAGE\n' >"$_custom_img"
_custom_sha="$(sha256sum "$_custom_img" | awk '{print $1}')"
_custom_bytes="$(_usb_file_size "$_custom_img")"

if it "usb custom: custom-local plans the file itself as the write source, with its real size and - for no digest"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$_custom_img" \
           usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc=$?
    first="$(printf '%s\n' "$out" | head -1)"
    if [[ $rc -eq 0 && "$first" == "usb_fetch_image custom-local $_custom_img - $_custom_img" \
          && "$out" == *"usb_reverify /dev/sdb unmounted $_custom_bytes "* \
          && "$out" == *"usb_copy_image /dev/sdb $_custom_img"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb custom: a custom image refuses without --write-mode, and a raw one is refused on ventoy but planned on native (A11)"; then
    no_mode="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_IMAGE_PATH="$_custom_img" \
               usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc1=$?
    raw_ventoy="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=raw USB_IMAGE_PATH="$_custom_img" \
                  usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc2=$?
    raw_native="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=raw USB_IMAGE_PATH="$_custom_img" \
                  usb_plan custom-local installer native /dev/sdb 2>&1)"; rc3=$?
    bad_mode="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=iso USB_IMAGE_PATH="$_custom_img" \
                usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc4=$?
    if [[ $rc1 -ne 0 && "$no_mode" == *"needs --write-mode"* && $rc2 -ne 0 && "$raw_ventoy" == *"cannot write a 'raw' image"* \
          && $rc3 -eq 0 && "$raw_native" == *"usb_write_raw /dev/sdb $_custom_img $_custom_bytes"* \
          && $rc4 -ne 0 && "$bad_mode" == *"must be 'hybrid' or 'raw'"* ]]; then pass
    else fail "no_mode=[$no_mode] raw_ventoy=[$raw_ventoy] raw_native=[$raw_native] bad_mode=[$bad_mode]"; fi
fi

if it "usb custom: custom-local refuses a missing file, an empty file, a block device and a path with whitespace"; then
    empty="$(mktemp)"
    missing="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH=/no/such/file.iso \
               usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc1=$?
    empty_out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$empty" \
                 usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc2=$?
    # A block device is not a regular file: -f refuses it, so a typo can
    # never make a disk the SOURCE of a write.
    device="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH=/dev/null \
              usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc3=$?
    spaced="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="/tmp/has space.iso" \
              usb_plan custom-local installer ventoy /dev/sdb 2>&1)"; rc4=$?
    rm -f "$empty"
    if [[ $rc1 -ne 0 && "$missing" == *"is not a readable file"* && $rc2 -ne 0 && "$empty_out" == *"is empty"* \
          && $rc3 -ne 0 && "$device" == *"is not a readable file"* && $rc4 -ne 0 && "$spaced" == *"must not contain whitespace"* ]]; then pass
    else fail "missing=[$missing] empty=[$empty_out] device=[$device] spaced=[$spaced]"; fi
fi

if it "usb custom: custom-url refuses without a digest, with a malformed digest and with a non-http scheme; plans a digest-keyed cache path otherwise"; then
    sha="$(printf 'ab%.0s' {1..32})"
    no_sha="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_URL=https://example.invalid/x.iso \
              usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc1=$?
    bad_sha="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_URL=https://example.invalid/x.iso \
               USB_IMAGE_SHA256=nothex usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc2=$?
    ftp="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_URL=ftp://example.invalid/x.iso \
           USB_IMAGE_SHA256="$sha" usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc3=$?
    ok="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_CACHE_DIR=/scratch/images USB_WRITE_MODE=hybrid \
          USB_IMAGE_URL=https://example.invalid/x.iso USB_IMAGE_SHA256="${sha^^}" \
          usb_plan custom-url installer ventoy /dev/sdb 2>&1)"; rc4=$?
    first="$(printf '%s\n' "$ok" | head -1)"
    if [[ $rc1 -ne 0 && "$no_sha" == *"needs --image-sha256"* && $rc2 -ne 0 && "$bad_sha" == *"64 hexadecimal"* \
          && $rc3 -ne 0 && "$ftp" == *"http:// or https://"* && $rc4 -eq 0 \
          && "$first" == "usb_fetch_image custom-url /scratch/images/custom-url-${sha:0:16}.iso $sha https://example.invalid/x.iso" ]]; then pass
    else fail "no_sha=[$no_sha] bad_sha=[$bad_sha] ftp=[$ftp] ok=[$ok]"; fi
fi

if it "usb custom: executing custom-local verifies the digest - a wrong one stops before any write, not touched"; then
    good_plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$_custom_img" \
                 USB_IMAGE_SHA256="$_custom_sha" usb_plan custom-local installer ventoy /dev/sdb 2>/dev/null | head -1)"
    bad_plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" USB_WRITE_MODE=hybrid USB_IMAGE_PATH="$_custom_img" \
                USB_IMAGE_SHA256="$(printf '%064d' 0)" usb_plan custom-local installer ventoy /dev/sdb 2>/dev/null | head -1)"
    good="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$good_plan" 2>&1)"; rc1=$?
    bad="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$bad_plan" 2>&1)"; rc2=$?
    if [[ $rc1 -eq 0 && "$good" == *"verified image"* && $rc2 -ne 0 \
          && "$bad" == *"does not match the SHA-256 you supplied"* && "$bad" == *"not touched"* ]]; then pass
    else fail "rc1=$rc1 good=[$good] rc2=$rc2 bad=[$bad]"; fi
fi

if it "usb custom: executing custom-url downloads and verifies against the supplied digest, and a second run is skipped"; then
    srv_dir="$(mktemp -d)"
    cp "$_custom_img" "$srv_dir/custom.iso"
    read -r srv_pid srv_port < <(_start_test_http_server "$srv_dir")
    cache="$(mktemp -d)"
    plan="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_CACHE_DIR="$cache" USB_WRITE_MODE=hybrid \
            USB_IMAGE_URL="http://127.0.0.1:${srv_port}/custom.iso" USB_IMAGE_SHA256="$_custom_sha" \
            usb_plan custom-url installer ventoy /dev/sdb 2>/dev/null | head -1)"
    first="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$plan" 2>&1)"; rc1=$?
    second="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" usb_execute /dev/sdb <<<"$plan" 2>&1)"; rc2=$?
    dest="$cache/custom-url-${_custom_sha:0:16}.iso"
    if [[ $rc1 -eq 0 && $rc2 -eq 0 && "$first" == *"verified image"* && "$second" == *"skipped"* ]] \
        && cmp -s "$dest" "$_custom_img"; then pass
    else fail "rc1=$rc1 rc2=$rc2 first=[$first] second=[$second]"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$srv_dir" "$cache" 2>/dev/null
fi

if it "usb custom: setup.sh --create-usb with a custom-local image and --dry-run shows the plan and writes nothing"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" bash setup.sh --create-usb --image custom-local \
           --image-path "$_custom_img" --write-mode hybrid --engine ventoy --usb-device /dev/sdb \
           --dry-run --no-color 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"usb_fetch_image custom-local $_custom_img - $_custom_img"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

rm -f "$_custom_img"

# ─── usb_fetch_image: the resolver-to-downloader bridge ─────────────────────
# image_resolve (tested above, against loopback index fixtures) and
# fetch_verified (tested above, against loopback files) were each done;
# nothing joined them, so --create-usb could never download the image it
# planned to write. Every test here serves a scratch release directory over
# _start_test_http_server - a tiny text file standing in for the ISO, plus
# a real SHA256SUMS computed from it - and points image_resolve at it
# through a scratch AUTOOS_ROOT whose catalog/images.json is the only file
# in it. No test here reaches the real internet or the real catalog. Every
# test name carries "usb_fetch_image" and "usb" so `--filter fetch` and
# `--filter usb` both reach them (lesson L2: filters match test names).
describe "usb fetch"

# _usb_fetch_fixture <good|bad|missing>
# Builds the scratch release dir and prints its path. <good> lists the
# image's real digest, <bad> lists an all-zero one, <missing> lists a
# different filename entirely.
_usb_fetch_fixture() {
    local mode="$1" rel sum
    rel="$(mktemp -d)"
    mkdir -p "$rel/1.0"
    printf 'AUTOOS TEST IMAGE - not a real ISO\n' >"$rel/1.0/testos-1.0-amd64.iso"
    sum="$(sha256sum "$rel/1.0/testos-1.0-amd64.iso" | awk '{print $1}')"
    case "$mode" in
        good)    printf '%s *testos-1.0-amd64.iso\n' "$sum" >"$rel/1.0/SHA256SUMS" ;;
        bad)     printf '%064d *testos-1.0-amd64.iso\n' 0 >"$rel/1.0/SHA256SUMS" ;;
        missing) printf '%s *something-else.iso\n' "$sum" >"$rel/1.0/SHA256SUMS" ;;
    esac
    cat >"$rel/1.0/index.html" <<'HTML'
<html><body><pre><a href="../">../</a>
<a href="testos-1.0-amd64.iso">testos-1.0-amd64.iso</a>
<a href="SHA256SUMS">SHA256SUMS</a>
</pre></body></html>
HTML
    printf '%s\n' "$rel"
}

# _usb_fetch_root <port> [sums-field]
# Writes a scratch AUTOOS_ROOT holding only catalog/images.json, with one
# entry whose index is the loopback server's 1.0/ directory. Prints the
# root. The image entry carries every field the real catalog schema has so
# usb_plan and usb_fetch_image read it exactly like a shipped entry.
_usb_fetch_root() {
    local port="$1" sums="${2:-SHA256SUMS}" mirrors="${3:-}" root
    root="$(mktemp -d)"
    mkdir -p "$root/catalog"
    python3 - "$root/catalog/images.json" "http://127.0.0.1:${port}/1.0/" "$sums" "$mirrors" <<'PY'
import json, sys
path, index, sums, mirrors = sys.argv[1:5]
entry = {"id": "testos", "name": "Test OS", "homepage": "http://127.0.0.1/",
         "index": index, "file": r"testos-[0-9.]+-amd64\.iso$", "sums": sums, "sig": "-", "key": "-",
         "kinds": ["installer"], "writeMode": "hybrid", "sizeGb": 0.001}
if mirrors:
    entry["mirrors"] = [m for m in mirrors.split(",") if m]
with open(path, "w", encoding="utf-8") as fh:
    json.dump({"images": [entry]}, fh)
PY
    printf '%s\n' "$root"
}

# _usb_fetch_mirror_of <fixture-dir> <good|corrupt>
# A second release dir with the same 1.0/ layout, served as a "mirror":
# an exact copy, or one whose image bytes differ from what the canonical
# manifest publishes. Prints its path.
_usb_fetch_mirror_of() {
    local src="$1" mode="$2" m
    m="$(mktemp -d)"
    cp -r "$src/1.0" "$m/1.0"
    if [[ "$mode" == corrupt ]]; then
        printf 'CORRUPT MIRROR COPY\n' >"$m/1.0/testos-1.0-amd64.iso"
    fi
    printf '%s\n' "$m"
}

if it "_usb_sums_digest_for reads coreutils, starred and BSD-style manifests and ignores non-SHA-256 lines (usb_fetch_image)"; then
    m="$(mktemp)"
    good="$(printf '%064d' 7)"
    cat >"$m" <<EOF
d41d8cd98f00b204e9800998ecf8427e *plain.iso
$(printf '%064d' 1)  plain.iso
$(printf '%064d' 2) *starred.iso
$(printf '%064d' 3)  ./dotslash.iso
SHA256 (bsd.iso) = $(printf '%064d' 4)
EOF
    r1="$(_usb_sums_digest_for "$m" plain.iso)"
    r2="$(_usb_sums_digest_for "$m" starred.iso)"
    r3="$(_usb_sums_digest_for "$m" dotslash.iso)"
    r4="$(_usb_sums_digest_for "$m" bsd.iso)"
    r5="$(_usb_sums_digest_for "$m" absent.iso)"
    if [[ "$r1" == "$(printf '%064d' 1)" && "$r2" == "$(printf '%064d' 2)" && "$r3" == "$(printf '%064d' 3)" \
          && "$r4" == "$(printf '%064d' 4)" && -z "$r5" ]]; then pass
    else fail "plain=$r1 starred=$r2 dotslash=$r3 bsd=$r4 absent=[$r5] (good=$good)"; fi
    rm -f "$m"
fi

if it "usb_fetch_image downloads the image, verifies it against the served SHA256SUMS and keeps the manifest beside it (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"verified image"* && -s "$cache/testos.iso" && -s "$cache/testos.iso.sums" ]] \
        && cmp -s "$cache/testos.iso" "$rel/1.0/testos-1.0-amd64.iso"; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a second run is a cache hit reported as skipped, never a refetch (usb_fetch_image idempotent)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" >/dev/null 2>&1
    out2="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out2" == *"skipped"* && "$out2" == *"testos.iso already downloaded"* ]]; then pass
    else fail "rc=$rc out=$out2"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a checksum mismatch is refused and leaves nothing at the destination (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture bad)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"checksum mismatch"* && ! -e "$cache/testos.iso" && ! -e "$cache/testos.iso.part" ]]; then pass
    else fail "rc=$rc iso=$( [[ -e "$cache/testos.iso" ]] && echo EXISTS || echo absent ) out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a manifest that does not list the image is refused before any image download (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture missing)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"does not list"* && ! -e "$cache/testos.iso" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a catalog entry with no checksum manifest is refused with nothing fetched (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port" -)"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"no checksum manifest"* && ! -e "$cache/testos.iso" && ! -e "$cache/testos.iso.sums" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a healthy mirror serves the image before the canonical source, verified against the canonical manifest (usb_fetch_image mirror)"; then
    # The canonical copy is deliberately corrupted AFTER its manifest was
    # written: if the canonical source were tried first, the digest check
    # would fail. Success therefore proves the mirror served the bytes.
    rel="$(_usb_fetch_fixture good)"
    mir="$(_usb_fetch_mirror_of "$rel" good)"
    printf 'CANONICAL COPY NOW CORRUPT\n' >"$rel/1.0/testos-1.0-amd64.iso"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    read -r mir_pid mir_port < <(_start_test_http_server "$mir")
    root="$(_usb_fetch_root "$srv_port" SHA256SUMS "http://127.0.0.1:${mir_port}/1.0/")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"from http://127.0.0.1:${mir_port}/1.0/testos-1.0-amd64.iso"* \
          && "$out" == *"verified image"* ]] && cmp -s "$cache/testos.iso" "$mir/1.0/testos-1.0-amd64.iso"; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" "$mir_pid" 2>/dev/null; wait "$srv_pid" "$mir_pid" 2>/dev/null
    rm -rf "$rel" "$mir" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: an unreachable mirror is skipped with a warning and the canonical source used (usb_fetch_image mirror)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    # Port 1 on loopback: nothing listens there, so curl fails fast.
    root="$(_usb_fetch_root "$srv_port" SHA256SUMS "http://127.0.0.1:1/1.0/")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"http://127.0.0.1:1/1.0/testos-1.0-amd64.iso failed"* \
          && "$out" == *"trying the next source"* && "$out" == *"verified image"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: a mirror serving bytes that do not match the manifest is skipped, the canonical copy wins (usb_fetch_image mirror)"; then
    rel="$(_usb_fetch_fixture good)"
    mir="$(_usb_fetch_mirror_of "$rel" corrupt)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    read -r mir_pid mir_port < <(_start_test_http_server "$mir")
    root="$(_usb_fetch_root "$srv_port" SHA256SUMS "http://127.0.0.1:${mir_port}/1.0/")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" usb_fetch_image testos "$cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"rc=2"* && "$out" == *"trying the next source"* ]] \
        && cmp -s "$cache/testos.iso" "$rel/1.0/testos-1.0-amd64.iso"; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" "$mir_pid" 2>/dev/null; wait "$srv_pid" "$mir_pid" 2>/dev/null
    rm -rf "$rel" "$mir" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: creates a cache directory that does not exist yet, as on a machine's first run (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache/images" usb_fetch_image testos "$cache/brand/new/dir/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && -s "$cache/brand/new/dir/testos.iso" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_fetch_image: custom-local without its source and digest is a specific refusal, not a fetch attempt (usb_fetch_image)"; then
    # custom-local / custom-url are real images now (see "usb custom image"),
    # but only on the 4-argument line usb_plan emits for them. Called the
    # catalog way - no source, no digest - it must refuse by name and touch
    # nothing, never fall through to image_resolve and the network.
    cache="$(mktemp -d)"
    out="$(AUTOOS_CACHE_DIR="$cache" usb_fetch_image custom-local "$cache/x.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"needs its source and digest"* && -z "$(find "$cache" -mindepth 1)" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    rm -rf "$cache"
fi

if it "usb_fetch_image: a dry run says what it would download and touches nothing (usb_fetch_image)"; then
    cache="$(mktemp -d)"
    out="$(AUTOOS_DRY_RUN=1 AUTOOS_CACHE_DIR="$cache" usb_fetch_image ubuntu-desktop-lts "$cache/x.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"would resolve ubuntu-desktop-lts"* && -z "$(find "$cache" -mindepth 1)" ]]; then pass
    else fail "rc=$rc out=$out"; fi
    rm -rf "$cache"
fi

if it "usb_execute: dispatches a usb_fetch_image plan line into the real download path (usb_fetch_image)"; then
    rel="$(_usb_fetch_fixture good)"
    read -r srv_pid srv_port < <(_start_test_http_server "$rel")
    root="$(_usb_fetch_root "$srv_port")"
    cache="$(mktemp -d)"
    out="$(AUTOOS_ROOT="$root" AUTOOS_CACHE_DIR="$cache" AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"usb_fetch_image testos $cache/testos.iso" 2>&1)"; rc=$?
    if [[ $rc -eq 0 && -s "$cache/testos.iso" && "$out" == *"Ready to boot: /dev/sdb"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
    kill "$srv_pid" 2>/dev/null; wait "$srv_pid" 2>/dev/null
    rm -rf "$rel" "$root" "$cache" 2>/dev/null
fi

if it "usb_execute: a failure in the fetch step says the device was not touched, never 'rewrite from wipefs' (usb_fetch_image)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FORCE_FAIL=1 \
           usb_execute /dev/sdb <<<"usb_fetch_image ubuntu-desktop-lts /tmp/x.iso" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"not touched"* && "$out" != *"wipefs"* && "$out" != *"Ready to boot"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb_execute: a failure in a write step still reports no-rollback, rewrite from wipefs (usb_fetch_image)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" AUTOOS_FORCE_FAIL=1 AUTOOS_TRACE=1 \
           usb_execute /dev/sdb <<<"usb_write_raw /dev/sdb /tmp/x.iso 100" 2>&1)"; rc=$?
    if [[ $rc -ne 0 && "$out" == *"wipefs"* && "$out" != *"not touched"* ]]; then pass
    else fail "rc=$rc out=$out"; fi
fi

if it "usb_execute: a usb_fetch_image line with the wrong word count is refused, never run (usb_fetch_image)"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" \
           usb_execute /dev/sdb <<<"usb_fetch_image ubuntu-desktop-lts" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"malformed usb_fetch_image step"* ]] && pass || fail "rc=$rc out=$out"
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

if it "usb_execute: _usb_copy_readback reports a same-size content change, a missing file and a size change, and nothing for an identical tree (usb copy readback)"; then
    # The second real build on 2026-09-17 finished and printed "Ready to
    # boot" while the stick had silently replaced one 16 KB cluster of
    # md5sum.txt with garbage at the same size. Only reading back catches
    # that; this is the pure dir-vs-dir function the copy path calls.
    rb="$(mktemp -d)"
    mkdir -p "$rb/src/casper" "$rb/dst/casper" "$rb/src/EFI/boot" "$rb/dst/EFI/boot"
    for rel in md5sum.txt casper/minimal.squashfs EFI/boot/bootx64.efi casper/vmlinuz; do
        head -c 40000 /dev/urandom >"$rb/src/$rel"; cp "$rb/src/$rel" "$rb/dst/$rel"
    done
    clean_out="$(_usb_copy_readback "$rb/src" "$rb/dst" 2>&1)"; clean_rc=$?
    # Same size, one "cluster" of garbage in the middle - the live shape.
    head -c 16384 /dev/urandom | dd of="$rb/dst/md5sum.txt" bs=1 seek=8192 conv=notrunc status=none
    rm -f "$rb/dst/casper/vmlinuz"
    head -c 1000 "$rb/src/EFI/boot/bootx64.efi" >"$rb/dst/EFI/boot/bootx64.efi"
    out="$(_usb_copy_readback "$rb/src" "$rb/dst" 2>&1)"; rc=$?
    if [[ $clean_rc -eq 0 && -z "$clean_out" && $rc -ne 0 && "$out" == *"content differs (same size): md5sum.txt"* \
          && "$out" == *"missing on the stick: casper/vmlinuz"* && "$out" == *"size differs: EFI/boot/bootx64.efi"* ]]; then pass
    else fail "clean_rc=$clean_rc clean_out=[$clean_out] rc=$rc out=$out"; fi
    rm -rf "$rb"
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
       templates/ai-dispatcher.sh templates/ollama-chat.sh "$BS_STICK/"

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
    # python3: a stateful stub, so install_oterm never runs a REAL
    # `pip install --user oterm` on the machine running the suite (the Linux
    # CI runner did exactly that, and installed it twice because
    # ~/.local/bin is not on the sandbox PATH). `pip install` records a
    # marker; `pip show oterm` reports it; everything else is a no-op.
    cat > "$BS_BIN/python3" <<'EOS'
#!/usr/bin/env bash
printf 'python3 %s\n' "$*" >>"$FAKE_CMD_LOG"
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
    case "${3:-}" in
        install) touch "$FAKE_DPKG_DB.pip-oterm"; exit 0 ;;
        show)    [[ -e "$FAKE_DPKG_DB.pip-oterm" ]]; exit $? ;;
    esac
fi
exit 0
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

if it "ai dispatcher --list names all three backends, including local (template)"; then
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" bash templates/ai-dispatcher.sh --list 2>&1)"
    rc=$?
    if [[ $rc -eq 0 && "$out" == *"claude"* && "$out" == *"agy"* && "$out" == *"local"* && "$out" == *"ollama-chat"* ]]; then
        pass
    else
        fail "expected all three backends listed (rc=$rc): ${out:0:300}"
    fi
fi

if it "ai dispatcher local backend execs ollama-chat, never oterm (template)"; then
    # oterm is documented interactive-only (no piped/one-shot mode), so the
    # 'local' registry record must point at ollama-chat, not oterm — this
    # drives that fact through the real dispatcher rather than just reading
    # the registry file.
    fakebin="$(mktemp -d)"
    cat >"$fakebin/ollama-chat" <<'EOS'
#!/usr/bin/env bash
printf 'ollama-chat-fake:%s\n' "$*"
EOS
    chmod +x "$fakebin/ollama-chat"
    out="$(AUTOOS_AI_REGISTRY="$PWD/templates/ai-clients.conf" \
           PATH="$fakebin:/usr/bin:/bin" \
           bash templates/ai-dispatcher.sh local "why did this drive fail?" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "ollama-chat-fake:why did this drive fail?" ]]; then
        pass
    else
        fail "expected 'local' to exec ollama-chat verbatim (rc=$rc): ${out:0:200}"
    fi
fi

if it "ollama-chat wrapper runs the pinned model non-interactively when it is pulled (template)"; then
    fakebin="$(mktemp -d)"
    cat >"$fakebin/ollama" <<'EOS'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" ]]; then
    printf 'NAME\tID\tSIZE\tMODIFIED\n'
    printf 'qwen3:4b\tabc123\t2.5 GB\tnow\n'
    exit 0
fi
printf 'ollama-run:%s\n' "$*"
EOS
    chmod +x "$fakebin/ollama"
    out="$(PATH="$fakebin:/usr/bin:/bin" bash templates/ollama-chat.sh "diagnose this" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 && "$out" == "ollama-run:run qwen3:4b diagnose this" ]]; then
        pass
    else
        fail "expected 'ollama run qwen3:4b ...' with no fallback warning (rc=$rc): ${out:0:200}"
    fi
fi

if it "ollama-chat wrapper falls back to whatever model IS pulled when the pinned default is missing (template)"; then
    fakebin="$(mktemp -d)"
    cat >"$fakebin/ollama" <<'EOS'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" ]]; then
    printf 'NAME\tID\tSIZE\tMODIFIED\n'
    printf 'qwen3:1.7b\tdef456\t1.4 GB\tnow\n'
    exit 0
fi
printf 'ollama-run:%s\n' "$*"
EOS
    chmod +x "$fakebin/ollama"
    out="$(PATH="$fakebin:/usr/bin:/bin" AUTOOS_OLLAMA_MODEL="qwen3:4b" bash templates/ollama-chat.sh "diagnose this" 2>&1)"
    rc=$?
    rm -rf "$fakebin"
    if [[ $rc -eq 0 \
        && "$out" == *'model "qwen3:4b" is not pulled — using "qwen3:1.7b" instead'* \
        && "$out" == *"ollama-run:run qwen3:1.7b diagnose this"* ]]; then
        pass
    else
        fail "expected a fallback warning plus a run against the pulled model (rc=$rc): ${out:0:300}"
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

if it "install_zed announces in dry run"; then
    out="$( ( AUTOOS_DRY_RUN=1; install_zed ) 2>&1)"
    if [[ "$out" == *"would install Zed"* ]]; then pass
    else fail "no dry-run announcement"; fi
fi

# catalog/ide-models.json is read at install time by the Zed, OpenCode and
# OpenHands writers. Missing or malformed, each must say so in ONE line that
# names the file - no traceback, no half-written config, no stray backup.
if it "a missing or malformed catalog/ide-models.json stops the IDE writers with one clear line (ide-models)"; then
    d="$(mktemp -d)"
    printf '{ "models": [ ' >"$d/truncated.json"
    printf '{"models": [{"id": "t1-orchestrator"}]}' >"$d/no-fields.json"
    ok=1
    for catfile in "$d/missing.json" "$d/truncated.json" "$d/no-fields.json"; do
        home="$d/home-$(basename "$catfile" .json)"
        mkdir -p "$home/.config/zed" "$home/.config/opencode"
        printf '{"theme":"mine"}' >"$home/.config/zed/settings.json"
        printf '{"model": "anthropic/mine"}\n' >"$home/.config/opencode/opencode.json"
        out="$( ( SYS_HOME="$home" AUTOOS_DRY_RUN=0 AUTOOS_ROOT="$ROOT" AUTOOS_IDE_MODELS_FILE="$catfile"
                  unset OPENROUTER_API_KEY META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY
                  curl() { return 6; }
                  opencode_is_v2() { return 0; }
                  route_zed_to_proxy; echo "zed_rc=$?"
                  setup_opencode_config; echo "oc_rc=$?" ) 2>&1)"
        [[ "$out" == *"zed_rc=1"* ]] || { ok=0; echo "$catfile: zed writer did not fail: $out" >&2; }
        [[ "$(grep -c "$catfile" <<<"$out")" -ge 2 ]] || { ok=0; echo "$catfile: path not named by both writers: $out" >&2; }
        [[ "$out" == *Traceback* ]] && { ok=0; echo "$catfile: traceback: $out" >&2; }
        [[ "$(cat "$home/.config/zed/settings.json")" == '{"theme":"mine"}' ]] || { ok=0; echo "$catfile: zed settings changed" >&2; }
        [[ "$(cat "$home/.config/opencode/opencode.json")" == '{"model": "anthropic/mine"}' ]] || { ok=0; echo "$catfile: opencode config changed" >&2; }
        [[ -e "$home/.config/opencode/config.json" ]] && { ok=0; echo "$catfile: config.json written" >&2; }
        n="$(find "$home" -name '*.autoos-backup-*' | wc -l)"
        [[ "$n" == 0 ]] || { ok=0; echo "$catfile: $n stray backups" >&2; }
    done
    # OpenHands: the default LLM just gets no windows; the rest still runs.
    home="$d/home-openhands"; mkdir -p "$home"
    printf 'omniroute: REPLACE_ME\n' >"$d/keys.yml"
    out="$( ( SYS_HOME="$home" AUTOOS_DRY_RUN=0 AUTOOS_KEYS_FILE="$d/keys.yml" AUTOOS_OMNIROUTE_KEY=sk-fake-gw \
              AUTOOS_IDE_MODELS_FILE="$d/truncated.json"
              unset OPENROUTER_API_KEY LITELLM_MASTER_KEY AUTOOS_LITELLM_API_KEY META_API_KEY
              curl() { return 6; }
              setup_openhands_config ) 2>&1)"
    llm="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); l=d.get("agent_settings", d).get("llm", {}); print(l.get("model"), l.get("max_input_tokens"))' "$home/.openhands/settings.json" 2>&1)"
    [[ "$out" == *"$d/truncated.json"* ]] || { ok=0; echo "openhands: path not named: $out" >&2; }
    [[ "$out" == *Traceback* ]] && { ok=0; echo "openhands: traceback: $out" >&2; }
    [[ "$llm" == "openai/t1-orchestrator None" ]] || { ok=0; echo "openhands default: $llm" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a broken model catalog is not reported cleanly"; fi
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

# The stubs sit on a PATH of only "$stub:/usr/bin:/bin" and SYS_HOME is a
# temp dir, so a real uv/pipx/litellm on the machine running the suite can
# neither satisfy nor short-circuit these cases.
if it "litellm installer prefers uv tool install (PEP 668 safe)"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\necho "$@" >"$0.called"\n' >"$stub/uv"; chmod +x "$stub/uv"
    printf '#!/bin/sh\ntouch "$0.called"\n' >"$stub/pipx"; chmod +x "$stub/pipx"
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub" AUTOOS_DRY_RUN=0; install_litellm_proxy >/dev/null 2>&1 )
    if [[ "$(cat "$stub/uv.called" 2>/dev/null)" == "tool install litellm[proxy]" && ! -f "$stub/pipx.called" ]]; then
        rm -rf "$stub"; pass
    else rm -rf "$stub"; fail "uv tool install was not the chosen path"; fi
fi

if it "litellm installer falls back to pipx without uv"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\ntouch "$0.called"\n' >"$stub/pipx"; chmod +x "$stub/pipx"
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub" AUTOOS_DRY_RUN=0; install_litellm_proxy >/dev/null 2>&1 )
    if [[ -f "$stub/pipx.called" ]]; then rm -rf "$stub"; pass
    else rm -rf "$stub"; fail "pipx stub was not invoked"; fi
fi

if it "litellm installer reports a failed install instead of success"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\nexit 1\n' >"$stub/uv"; chmod +x "$stub/uv"
    rc=0
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub" AUTOOS_DRY_RUN=0; install_litellm_proxy >/dev/null 2>&1 ) || rc=$?
    rm -rf "$stub"
    [[ $rc -ne 0 ]] && pass || fail "a failed install returned 0"
fi

if it "a present litellm is detected, so a second run skips"; then
    stub="$(mktemp -d)"
    printf '#!/bin/sh\nexit 0\n' >"$stub/litellm"; chmod +x "$stub/litellm"
    ( PATH="$stub:/usr/bin:/bin" SYS_HOME="$stub"; custom_is_installed litellm ) && ok=1 || ok=0
    rm -rf "$stub"
    [[ $ok -eq 1 ]] && pass || fail "custom_is_installed litellm said missing"
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

if it "opencode.jsonc is valid JSON once comments are stripped"; then
    # Every client loads this file, and a single unbalanced brace makes ALL of
    # them fall back to defaults while the suite's other assertions (which read
    # it as text) stay green. Measured 2026-09-23: a provider block was added
    # without its closing brace and nothing failed.
    out="$(python3 - <<'PY'
import json, re, sys
raw = open("opencode.jsonc", encoding="utf-8").read()
body = re.sub(r"(?m)^\s*//.*$", "", raw)
try:
    doc = json.loads(body)
except Exception as exc:
    sys.exit(f"parse error: {exc}")
if not doc.get("model"):
    sys.exit("no default model")
print("ok")
PY
)"
    assert_eq "$out" "ok"
fi

if it "the router declarations do not drift from each other"; then
    # tools/audit-router.py --offline compares combos.json against opencode.jsonc,
    # both Zed writers and the OpenHands tier profiles, and rejects any
    # combo-bypassing direct ref. Live gateway/proxy probes are the operator
    # path (no --offline); CI stays deterministic.
    out="$(python3 tools/audit-router.py --offline 2>&1)"; rc=$?
    assert_ok "$rc"
    assert_not_contains "$out" "DRIFT"
fi

if it "the IDE model lists match catalog/ide-models.json (sync-ide-models --check)"; then
    # catalog/ide-models.json is the single source for the gateway model list
    # (ids, names, windows, membership). opencode.jsonc and the OpenHands
    # tier spec + config.toml carry generated copies; --check exits 1 with a
    # diff when one drifted (fix: python3 tools/sync-ide-models.py).
    out="$(python3 tools/sync-ide-models.py --check 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "rc=$rc $(printf '%s\n' "$out" | tail -n 20)"; fi
fi

if it "the IDE model sync tool's unit tests pass (sync-ide-models)"; then
    out="$(python3 tests/test_sync_ide_models.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "every leg of a combo carries a provider prefix"; then
    bad="$(python3 - 2>&1 <<'PY'
import json
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
print(" ".join(f"{c['name']}:{m}" for c in d["combos"] for m in c["models"] if "/" not in m))
PY
)"
    assert_eq "$bad" ""
fi

if it "apply sets the resilience deadline and the fast-skip breaker"; then
    ok=1
    for f in configuration/omniroute/apply.sh configuration/omniroute/apply.ps1; do
        grep -q 'maxWaitMs' "$f" || { ok=0; echo "$f never sets maxWaitMs" >&2; }
        grep -q '180000' "$f" || { ok=0; echo "$f does not use the reasoning-safe value" >&2; }
        grep -q 'providerBreaker' "$f" || { ok=0; echo "$f never sets the fast-skip breaker" >&2; }
        grep -qE 'failureThreshold.?[:=].?2|BREAKER_THRESHOLD=2' "$f" \
            || { ok=0; echo "$f does not use the 2-failure threshold" >&2; }
    done
    # The free promo leg must stay FIRST in tier1/spark: free when it works,
    # fast-skipped by the breaker when it does not (operator 2026-09-23).
    head_leg="$(python3 - <<'PY'
import json
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
by = {c["name"]: c["models"] for c in d["combos"]}
print(",".join(by[n][0] for n in ("t1-orchestrator", "spark-1.3-contributor")))
PY
)"
    assert_eq "$head_leg" "opencode-zen/muse-spark-1.3-contributor-free,opencode-zen/muse-spark-1.3-contributor-free"
    if (( ok )); then pass; else fail "the resilience settings are not applied"; fi
fi

if it "combos.json carries no phantom legs (probe-falsified refs stay out)"; then
    # Regression gate for the 2026-09-22 finding: three legs shipped that the
    # gateway 400s on ("not available in the active live catalog"), which only
    # surfaces at chat time — simulate resolves them. Pin the falsified refs.
    report="$(python3 - 2>&1 <<'PY'
import json
banned = {
    "openrouter/gemini-3.8-flash": "bare openrouter gemini is an alias, not a provider ref",
    "deepseek/deepseek-v4.1-flash": "deepseek direct has no v4.1-flash in the live catalog",
    "gemini/gemini-3.7-flash": "3.7-flash is not in any combo (tiny free input quota)",
}
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
problems = []
for c in d["combos"]:
    for m in c["models"]:
        if m in banned:
            problems.append(f"{c['name']}:{m}")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "combos.json parses and t1-orchestrator promises 1M"; then
    report="$(python3 - 2>&1 <<'PY'
import json
d = json.load(open("configuration/omniroute/combos.json", encoding="utf-8"))
names = [c["name"] for c in d["combos"]]
problems = []
if names != ["t1-orchestrator", "spark-1.3-contributor", "t1-orchestrator-clean", "t1-orchestrator-free-only", "t2-worker", "t2-worker-clean", "t2-worker-free-only", "t2-orchestrator", "t3-driver", "t3-driver-clean", "t3-driver-free-only", "t4-rag", "gemini-3.8-flash", "deepseek-v4.1-flash", "opus-4-6"]:
    problems.append("names")
# "retired" is the one home of the ids a rename left behind: apply prunes
# them from the store, so a retired id must never also be a current combo.
retired = d.get("retired")
if not isinstance(retired, list) or not retired:
    problems.append("retired-empty")
else:
    for r in retired:
        if not isinstance(r, str) or not r:
            problems.append("retired-bad:" + repr(r))
        elif r in names:
            problems.append("retired:" + r)
for c in d["combos"]:
    if not c["models"]:
        problems.append(c["name"] + ":empty")
    for m in c["models"]:
        if "/" not in m:
            problems.append(c["name"] + ":" + m)
    if c["name"] == "t1-orchestrator" and c.get("context") != "1M":
        problems.append("t1-context")
by = {c["name"]: c["models"] for c in d["combos"]}
# Plain muse-spark-1.3 is BLOCKED (operator 2026-09-21): the only spark in
# any tier is the contributor.
import re as _re2
if _re2.search(r"muse-spark-1\.3(?!-contributor)", " ".join(m for c in d["combos"] for m in c["models"])):
    problems.append("plain-spark-blocked")
# t1-orchestrator is spark-only: gemini must never occupy a 1M slot again.
if any("gemini" in m for m in by["t1-orchestrator"]):
    problems.append("t1-gemini")
    problems.append("t1-gemini")
# *-clean = paid legs only: no free pool may train on private prompts.
# Free = contributor-free, groq / cerebras / sambanova / gemini hosts,
# mistral-code + qwen free pools. -contributor (trains by contract) is
# banned in t2-worker-clean/t3-driver-clean; t1-orchestrator-clean carries
# it deliberately since the 2026-09-21 contributor-only block (paid-only,
# trains).
# Direct-key legs (mistral-small, deepseek, openrouter paid, zen paid)
# bill past the pool on the same key, so they stay.
# The pinned spark-1.3-contributor single-model combo reuses t1-orchestrator's
# legs verbatim, so it is exempt from the tier-shape rules below (it is not
# a tier) but must stay byte-identical to t1.
import re
free = re.compile(r"contributor-free|^(groq|cerebras|sambanova|gemini)/|mistral/mistral-code|/qwen")
trains = re.compile(r"-contributor$")
for n in ("t1-orchestrator-clean", "t2-worker-clean", "t3-driver-clean"):
    bad = [m for m in by[n] if free.search(m)]
    if bad:
        problems.append(n + "-free:" + ",".join(bad))
for n in ("t2-worker-clean", "t3-driver-clean"):
    bad = [m for m in by[n] if trains.search(m)]
    if bad:
        problems.append(n + "-trains:" + ",".join(bad))
# *-free-only = zero paid/keyed legs (zen contributor-free counts as free).
paid = re.compile(r"cheaperinference|openrouter|^(deepseek|mistral)/|opencode-zen/(?!.*-free)")
for n in ("t1-orchestrator-free-only", "t2-worker-free-only", "t3-driver-free-only"):
    bad = [m for m in by[n] if paid.search(m)]
    if bad:
        problems.append(n + "-paid:" + ",".join(bad))
if by.get("spark-1.3-contributor") != by["t1-orchestrator"]:
    problems.append("spark-combo-drift")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "apply handles the Cloudflare UA and stays openrouter-first"; then
    ok=1
    # The UA quirk now lives once, in the registry; apply.sh only passes
    # provider_data through.
    grep -q 'providers\.json' configuration/omniroute/apply.sh || ok=0
    grep -q 'muse-code' configuration/omniroute/apply.sh && ok=0
    grep -q 'provider-specific-data' configuration/omniroute/apply.sh || ok=0
    ua="$(python3 -c "import json; p=json.load(open('catalog/providers.json', encoding='utf-8'))['providers']; print(' '.join((p[n]['provider_data'] or {}).get('customUserAgent','') for n in ('groq','cerebras')))")"
    [[ "$ua" == "curl/8.7.1 curl/8.7.1" ]] || { ok=0; echo "registry UA quirk wrong: $ua" >&2; }
    if (( ok )); then pass; else fail "apply.sh is missing the provider quirks"; fi
fi

if it "provider registry drives apply, mirror and the tier maps"; then
    # catalog/providers.json is the one map; the helper asserts every
    # consumer's in-memory map equals it, so a hand-edited copy or a half-done
    # registry edit fails here instead of routing a provider to a wrong name.
    out="$(python3 tests/helpers/check-provider-registry.py 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "$out"; fi
fi

if it "apply --dry-run registers nothing and starts nothing"; then
    # combos.json must be byte-identical afterwards; the dry run must announce.
    before="$(cat configuration/omniroute/combos.json)"
    # Hermetic: a dead gateway port and no key file - the dry run must never
    # read the live gateway or the machine's keys (it now also lists the store
    # for the retired-combo prune).
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE=/nonexistent/api-keys.yml \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    assert_contains "$out" "dry run"
    # Every registry provider with an omniroute_id is announced (meta and the
    # client key carry none and are skipped), so the map is proven live.
    for id in gemini cloudflare-ai huggingface opencode-zen cheaperinference sambanova cohere; do
        assert_contains "$out" "$id"
    done
    after="$(cat configuration/omniroute/combos.json)"
    assert_eq "$after" "$before"
fi

# api-keys.example.yml says "fill in what you have", so a copied file keeps
# REPLACE_WITH_* for the rest. Those must never be registered as keys: once
# registered, "already registered" would also shadow the real key forever.
if it "apply skips REPLACE_WITH placeholders and registers real keys"; then
    keys="$(mktemp)"
    printf 'groq: REPLACE_WITH_GROQ_KEY\nmistral: not-a-real-key-123\n' >"$keys"
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE="$keys" \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    rm -f "$keys"
    assert_contains "$out" "groq: no key in api-keys.yml, skipped"
    if grep -q "mistral: would register\|mistral already registered" <<<"$out"; then pass
    else fail "the real mistral key was not planned"; fi
fi

# start-stack.sh sits in configuration/, one level below the repo root. A
# `/../..` root pointed at the repo's parent, so the OpenHands tier-profile
# sync was never found and every start printed "reported a problem".
if it "start-stack.sh resolves the repo root for the profile sync"; then
    expr="$(grep -m1 '_ss_root=' configuration/start-stack.sh | sed -e 's/^[^=]*=//' -e 's/\${BASH_SOURCE\[0\]}/configuration\/start-stack.sh/')"
    got="$(eval "printf '%s' $expr")"
    if [[ -f "$got/tools/sync-openhands-profiles.py" ]]; then pass
    else fail "_ss_root resolved to '$got'"; fi
fi

# tools/autoos-agent.py - dry runs only: nothing is spawned or fetched.
if it "autoos-agent pairs each tier with its own model, standalone"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io, shlex, subprocess
oc = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
problems = []
for tier, agent in ((1, "t1-orchestrator"), (2, "t2-worker"), (3, "t3-reviewer")):
    out = subprocess.run(["python3", "tools/autoos-agent.py", "run", "--tier", str(tier), "--dry-run", "t"],
                         capture_output=True, text=True).stdout
    want = "--standalone --agent %s --model %s " % (agent, shlex.quote(oc["agents"][agent]["model"]))
    if want not in out:
        problems.append(agent)
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "autoos-agent: --clean picks the twin, undeclared models and --free --clean are refused"; then
    out="$(python3 tools/autoos-agent.py run --tier 3 --clean --dry-run t)"
    assert_contains "$out" "--model omniroute/t3-driver-clean "
    rc=0; python3 tools/autoos-agent.py run --tier 2 --model omniroute/not-a-combo --dry-run t >/dev/null 2>&1 || rc=$?
    assert_eq "$rc" "2"
    rc=0; python3 tools/autoos-agent.py run --tier 2 --free --clean --dry-run t >/dev/null 2>&1 || rc=$?
    assert_eq "$rc" "2"
fi

if it "autoos-agent --free is keyless and --isolate plans a fenced clone, never a worktree"; then
    out="$(AUTOOS_OMNIROUTE_KEY=never-print-this-key python3 tools/autoos-agent.py run --tier 2 --free --isolate --dry-run t)"
    assert_contains "$out" "git clone --local"
    assert_contains "$out" "env: AUTOOS_AGENT_DEPTH, AUTOOS_AGENT_MAX_DEPTH, OPENCODE_CONFIG_CONTENT, XDG_DATA_HOME"
    if grep -q "worktree add\|never-print-this-key\|AUTOOS_OMNIROUTE_KEY" <<<"$out"; then
        fail "free/isolated plan mentions a worktree or the gateway key"
    else pass; fi
fi

# ADR 0006 card resolver, the client adapters and the depth budget: one
# unittest per routing-table row, all dry runs.
if it "autoos-agent spawner unit tests: card routing, clients, depth"; then
    out="$(python3 tests/test_autoos_spawner.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# Resolver v2 (routing v2 spec section 5): pure bucket/effort tables and measure().
if it "resolver v2: bucket boundaries, effort rows, clamp (unit tests)"; then
    out="$(python3 tests/test_autoos_resolver.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "resolver v2: measure() features and client_state (unit tests)"; then
    out="$(python3 tests/test_autoos_measure.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "resolver v2: track record and Beta success estimate (unit tests)"; then
    out="$(python3 tests/test_autoos_track.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "autoos-agent context: fill from the session transcript (unit tests)"; then
    out="$(python3 tests/test_autoos_context.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# catalog/ai-registry.json (routing v2 spec section 3): converter, schema keys, idempotence.
if it "ai-registry converter: schema keys, legs resolve, idempotent (unit tests)"; then
    out="$(python3 tests/test_registry_convert.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "registry.py: check rules (unit tests) and the committed registry has no drift"; then
    out="$(python3 tests/test_registry.py 2>&1 && python3 tools/registry.py validate 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

# tools/skill-rules.py: the linter for one-line skill rules (routing v2 spec 8.1).
if it "skill-rules check: ids, length, source, near-duplicates (unit tests)"; then
    out="$(python3 tests/test_skill_rules.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "orchestration skill rules pass skill-rules check"; then out="$(python3 tools/skill-rules.py check 2>&1)" && pass || fail "$out"; fi

if it "render-opencode-container-config survives a malformed port (unit tests)"; then
    out="$(python3 tests/test_render_opencode_config.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "audit-router reads the LiteLLM master key from .env (unit tests)"; then
    out="$(python3 tests/test_audit_router_litellm_key.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "audit-router live probes retry a 503 with backoff and never a drift status (unit tests)"; then
    out="$(python3 tests/test_audit_router_probe_retry.py 2>&1)" && pass || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "autoos-agent outside-path fence denies first and re-allows only opencode scratch"; then
    report="$(python3 - 2>&1 <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("agent", "tools/autoos-agent.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rules = m.outside_fence("/x/data")
problems = []
if rules[0] != {"action": "external_directory", "resource": "*", "effect": "deny"}:
    problems.append("first-rule")
for r in rules[1:]:
    if r["effect"] != "allow" or "opencode" not in r["resource"] or r["resource"] == "*":
        problems.append(r["resource"])
if not any(r["resource"] == "/x/data/opencode/*" for r in rules):
    problems.append("private-data-dir")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "tier depth is mandatory in opencode.jsonc agents"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
text = re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read())
a = json.loads(text)["agents"]
def perms(n):
    return [(p["action"], p["resource"], p["effect"]) for p in a[n]["permissions"]]
t1, t2, t3 = perms("t1-orchestrator"), perms("t2-worker"), perms("t3-reviewer")
problems = []
if t1[0] != ("subagent", "*", "deny") or t1[-1] != ("subagent", "t2-worker", "allow"):
    problems.append("t1")
if t2[0] != ("subagent", "*", "deny") or t2[-1] != ("subagent", "t3-reviewer", "allow"):
    problems.append("t2")
# The leaf's fences run past these seven, but the first seven are the shape
# both sides agreed on; v2 names the shell action `shell` (a `bash` rule
# matches nothing) and the full fence set is asserted below.
if t3[:7] != [("subagent", "*", "deny"), ("edit", "*", "deny"), ("write", "*", "deny"), ("read", "*", "allow"), ("grep", "*", "allow"), ("glob", "*", "allow"), ("shell", "*", "allow")]:
    problems.append("t3-leaf")
if a["t3-reviewer"]["mode"] != "subagent":
    problems.append("t3-mode")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

# opencode v2 drops a top-level subagent_depth as an "unsupported legacy
# setting" and defaults to 1, so t2-worker answered "Subagent depth limit reached
# (1)" when t1-orchestrator had launched it (live, 2026-09-24).
if it "subagent depth lives under experimental, where opencode v2 reads it"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
oc = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
problems = []
if "subagent_depth" in oc:
    problems.append("top-level-key-is-ignored")
if (oc.get("experimental") or {}).get("subagent_depth") != 2:
    problems.append("experimental.subagent_depth!=2")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

# The reviewer is a leaf: v2 names the shell action `shell` (a `bash` rule
# matches nothing), MCP write tools bypass the edit/write deny, and a leaf
# never commits or pushes. Live 2026-09-24 the old stanza let t3-reviewer commit
# through the shell and overwrite a file through serena's create_text_file.
if it "t3-reviewer fences the shell, serena and omnigraph writes"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
oc = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
fences = json.load(io.open("catalog/agent-harness.json", encoding="utf-8"))["fences"]
t3 = [(p["action"], p["resource"], p["effect"]) for p in oc["agents"]["t3-reviewer"]["permissions"]]
problems = []
if any(act == "bash" for act, _, _ in t3):
    problems.append("bash-rule-matches-nothing-in-v2")
def last(action, resource):
    hits = [e for a, r, e in t3 if a == action and r == resource]
    return hits[-1] if hits else None
for pat in fences["bash_deny_all"] + fences["bash_deny_leaf"]:
    if last("shell", pat) != "deny":
        problems.append("shell:" + pat)
if last("serena_*", "*") != "deny":
    problems.append("serena-writes-open")
for tool in ("omnigraph_mutate", "omnigraph_load", "omnigraph_branches_merge", "omnigraph_branches_delete", "playwright_browser_run_code_unsafe", "autoos-agent_*"):
    if last(tool, "*") != "deny":
        problems.append(tool)
allowed = [a for a, r, e in t3 if a.startswith("serena_") and e == "allow"]
writers = ("create", "replace", "insert", "rename", "delete", "edit", "write", "execute")
problems += ["serena-writer-allowed:" + a for a in allowed if any(w in a for w in writers)]
if not allowed:
    problems.append("serena-read-tools-missing")
print(" ".join(problems))
PY
)"
    assert_eq "$report" ""
fi

if it "opencode tiers declare matching context limits"; then
    report="$(python3 - 2>&1 <<'PY'
import json, re, io
text = re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read())
oc = json.loads(text)
m = oc["providers"]["omniroute"]["models"]
problems = []
for name, ctx in (("t1-orchestrator", 1000000), ("t1-orchestrator-clean", 1000000),
                  ("t1-orchestrator-free-only", 1000000),
                  ("t2-worker", 131072), ("t3-driver", 131072),
                  ("t2-worker-clean", 131072), ("t3-driver-clean", 131072),
                  ("t2-worker-free-only", 131072), ("t3-driver-free-only", 131072),
                  ("gemini-3.8-flash", 131072), ("deepseek-v4.1-flash", 131072),
                  ("spark-1.3-contributor", 1000000)):
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
    bad="$(python3 - 2>&1 <<'PY'
import json, glob
problems = []
for p in sorted(glob.glob("catalog/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    profiles = set(d.get("profiles", {}).keys())
    for g in d.get("categories", []):
        for c in g.get("components", []):
            if c["id"] not in ("zed", "litellm", "opencode-cli", "omniroute", "openhands-docker"):
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

if it "autostart resumes the LiteLLM fallback proxy too"; then
    # litellm cannot read its own .env: the launcher must export it, and only
    # start the proxy when down (never bounce a healthy one on every logon).
    sh_launcher="configuration/autostart/Start-AutoOSStack.sh"
    ps_launcher="configuration/autostart/Start-AutoOSStack.ps1"
    starter="configuration/litellm/start-litellm.ps1"
    ok=1
    grep -q '4000' "$sh_launcher" || { ok=0; echo "sh: no :4000 probe" >&2; }
    grep -q 'start-litellm.sh' "$sh_launcher" || { ok=0; echo "sh: starter not referenced" >&2; }
    grep -q 'PYTHONUTF8' configuration/litellm/start-litellm.sh || { ok=0; echo "sh: PYTHONUTF8 missing" >&2; }
    grep -q 'already up with the current keys' configuration/litellm/start-litellm.sh || { ok=0; echo "sh: no litellm no-op path" >&2; }
    grep -q 'start-litellm.ps1' "$ps_launcher" || { ok=0; echo "ps1: starter not referenced" >&2; }
    grep -q 'already up on 4000' "$ps_launcher" || { ok=0; echo "ps1: no litellm no-op path" >&2; }
    [[ -f "$starter" ]] || { ok=0; echo "missing: $starter" >&2; }
    grep -q '\.env' "$starter" || { ok=0; echo "starter does not read .env" >&2; }
    if (( ok )); then pass; else fail "litellm autostart wiring incomplete"; fi
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

# ─── AI services (reboot-safe units, lane A) ────────────────────────────────
# Every case here asserts on a dry run or a pure decision: nothing is started,
# stopped, registered or written outside a mktemp directory.
describe "AI services"

# A fake litellm dir: config.yaml plus a .env that tries shell injection.
_svc_litellm_fixture() {
    local d
    d="$(mktemp -d)"
    : >"$d/config.yaml"
    cat >"$d/.env" <<'ENV'
# comment line
GROQ_API_KEY=gsk_fake_value_1
MISTRAL_API_KEY="quoted-fake-2"
META_API_KEY=REPLACE_WITH_META_KEY
EVIL_KEY=$(touch INJECTED)
EMPTY_KEY=
not a pair
ENV
    printf '%s' "$d"
}

# Review finding 2026-09-25: without /proc (macOS) the stale-key check can
# never run, and the old message blamed "another user". Say what is true.
if it "svc: start-litellm.sh says stale-key restart is Linux-only where /proc is missing"; then
    d="$(mktemp -d)"
    # Hermetic: a dummy .env in a temp dir, never the machine's own (CI has none).
    printf 'LITELLM_MASTER_KEY=sk-test-dummy\n' >"$d/.env"
    python3 -c 'import http.server,socketserver,sys
s=socketserver.TCPServer(("127.0.0.1",0),http.server.SimpleHTTPRequestHandler)
open(sys.argv[1],"w").write(str(s.server_address[1])); s.serve_forever()' "$d/port" >/dev/null 2>&1 &
    srv=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT="$(cat "$d/port")" AUTOOS_PROC_ROOT="$d/no-proc" \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"; rc=$?
    kill "$srv" 2>/dev/null
    rm -rf "$d"
    if [[ $rc -eq 0 && "$out" == *"Linux-only"* && "$out" != *"another user"* ]]; then pass
    else fail "rc=$rc: $out"; fi
fi

# Found in review 2026-09-25: the restart path killed whatever same-user
# process held the port, litellm or not (AGENTS.md rule 3). It must refuse.
if it "svc: start-litellm.sh never kills a program on its port that is not litellm"; then
    d="$(mktemp -d)"
    # Hermetic: a dummy .env in a temp dir, never the machine's own (CI has none).
    printf 'LITELLM_MASTER_KEY=sk-test-dummy\n' >"$d/.env"
    python3 -c 'import http.server,socketserver,sys
s=socketserver.TCPServer(("127.0.0.1",0),http.server.SimpleHTTPRequestHandler)
open(sys.argv[1],"w").write(str(s.server_address[1])); s.serve_forever()' "$d/port" >/dev/null 2>&1 &
    srv=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT="$(cat "$d/port")" bash "$ROOT/configuration/litellm/start-litellm.sh" 2>&1)"; rc=$?
    alive=0; kill -0 "$srv" 2>/dev/null && alive=1
    kill "$srv" 2>/dev/null
    rm -rf "$d"
    if [[ $rc -ne 0 && $alive -eq 1 && "$out" == *"not litellm"* ]]; then pass
    else fail "rc=$rc alive=$alive: $out"; fi
fi

# Re-review 2026-09-25: "litellm" anywhere in the command line is not proof;
# only the program name (argv[0] or the script in argv[1]) counts.
if it "svc: start-litellm.sh leaves a program alone that merely mentions litellm in its arguments"; then
    d="$(mktemp -d)"
    # Hermetic: a dummy .env in a temp dir, never the machine's own (CI has none).
    printf 'LITELLM_MASTER_KEY=sk-test-dummy\n' >"$d/.env"
    mkdir -p "$d/my-litellm-docs"
    python3 -c 'import http.server,socketserver,sys
s=socketserver.TCPServer(("127.0.0.1",0),http.server.SimpleHTTPRequestHandler)
open(sys.argv[1],"w").write(str(s.server_address[1])); s.serve_forever()' "$d/port" "$d/my-litellm-docs" >/dev/null 2>&1 &
    srv=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT="$(cat "$d/port")" bash "$ROOT/configuration/litellm/start-litellm.sh" 2>&1)"; rc=$?
    alive=0; kill -0 "$srv" 2>/dev/null && alive=1
    kill "$srv" 2>/dev/null
    rm -rf "$d"
    if [[ $rc -ne 0 && $alive -eq 1 && "$out" == *"not litellm"* ]]; then pass
    else fail "rc=$rc alive=$alive: $out"; fi
fi

if it "svc: start-litellm.sh loads .env literally, never evaluates it"; then
    d="$(_svc_litellm_fixture)"
    out="$(cd "$d" && AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    ok=1
    [[ -e "$d/INJECTED" ]] && { ok=0; echo "the .env was evaluated" >&2; }
    [[ "$out" == *"GROQ_API_KEY"* && "$out" == *"MISTRAL_API_KEY"* && "$out" == *"EVIL_KEY"* ]] \
        || { ok=0; echo "keys not reported: $out" >&2; }
    [[ "$out" == *"META_API_KEY"* || "$out" == *"EMPTY_KEY"* ]] && { ok=0; echo "placeholder/empty loaded" >&2; }
    [[ "$out" == *"fake_value"* || "$out" == *"quoted-fake"* ]] && { ok=0; echo "a value was printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "start-litellm.sh .env parsing is unsafe"; fi
fi

if it "svc: start-litellm.sh binds loopback with PYTHONUTF8 and starts nothing in a dry run"; then
    d="$(_svc_litellm_fixture)"
    out="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    rm -rf "$d"
    ok=1
    [[ "$out" == *"PYTHONUTF8=1 litellm --config config.yaml --host 127.0.0.1 --port 1"* ]] \
        || { ok=0; echo "planned command wrong: $out" >&2; }
    [[ "$out" == *"would start"* ]] || { ok=0; echo "dry run did not announce" >&2; }
    if (( ok )); then pass; else fail "start-litellm.sh plan is wrong"; fi
fi

if it "svc: start-litellm.sh restarts a proxy with stale keys and no-ops a current one"; then
    d="$(_svc_litellm_fixture)"
    env_now="$(mktemp)"; env_old="$(mktemp)"
    printf 'PATH=/usr/bin\0GROQ_API_KEY=gsk_fake_value_1\0MISTRAL_API_KEY=quoted-fake-2\0EVIL_KEY=$(touch INJECTED)\0PYTHONUTF8=1\0' >"$env_now"
    printf 'PATH=/usr/bin\0GROQ_API_KEY=gsk_old_value\0PYTHONUTF8=1\0' >"$env_old"
    cur="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 AUTOOS_FAKE_LITELLM_ENVIRON="$env_now" \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    old="$(AUTOOS_LITELLM_DIR="$d" AUTOOS_LITELLM_PORT=1 AUTOOS_FAKE_LITELLM_ENVIRON="$env_old" \
        bash "$ROOT/configuration/litellm/start-litellm.sh" --dry-run 2>&1)"
    rm -rf "$d" "$env_now" "$env_old"
    ok=1
    [[ "$cur" == *"already up with the current keys"* ]] || { ok=0; echo "current: $cur" >&2; }
    [[ "$old" == *"would restart"* && "$old" == *"GROQ_API_KEY"* && "$old" == *"MISTRAL_API_KEY"* ]] \
        || { ok=0; echo "stale: $old" >&2; }
    [[ "$old" == *"gsk_old"* || "$old" == *"fake_value"* ]] && { ok=0; echo "a value was printed" >&2; }
    if (( ok )); then pass; else fail "stale-key convergence is wrong"; fi
fi

if it "svc: the stack launcher starts litellm through start-litellm.sh"; then
    ok=1
    grep -q 'start-litellm.sh' configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "starter not used" >&2; }
    grep -qE '^[[:space:]]*\. \./\.env|set -a' configuration/autostart/Start-AutoOSStack.sh \
        && { ok=0; echo "inline .env sourcing is still there" >&2; }
    [[ -x configuration/litellm/start-litellm.sh ]] || { ok=0; echo "starter not executable" >&2; }
    if (( ok )); then pass; else fail "launcher still sources .env inline"; fi
fi

# The client key cannot PATCH /api/resilience (403 "Invalid management
# token", measured 2026-09-24); the CLI sends the machine loopback token.
if it "svc: apply sets resilience through the omniroute CLI, not curl + client key"; then
    ok=1
    for f in configuration/omniroute/apply.sh configuration/omniroute/apply.ps1; do
        grep -q 'patch-api-resilience' "$f" || { ok=0; echo "no CLI patch: $f" >&2; }
        grep -q 'get-api-resilience' "$f" || { ok=0; echo "no CLI read: $f" >&2; }
        grep -qE 'X PATCH|Method Patch' "$f" && { ok=0; echo "still PATCHes over HTTP: $f" >&2; }
    done
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE=/dev/null \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    [[ "$out" == *"would set requestQueue.maxWaitMs = 180000 (omniroute api system patch-api-resilience)"* ]] \
        || { ok=0; echo "dry run: $out" >&2; }
    if (( ok )); then pass; else fail "resilience still goes through the client key"; fi
fi

# The dry run must not read the live gateway's provider list: with a gateway
# up, every provider reads "already registered" and the placeholder test
# above became machine-dependent.
if it "svc: apply --dry-run against a down gateway plans from the key file alone"; then
    keys="$(mktemp)"
    printf 'groq: REPLACE_WITH_GROQ_KEY\nmistral: not-a-real-key-123\n' >"$keys"
    out="$(AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE="$keys" \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    rm -f "$keys"
    ok=1
    [[ "$out" == *"groq: no key in api-keys.yml, skipped"* ]] || { ok=0; echo "groq: $out" >&2; }
    [[ "$out" == *"mistral: would register"* ]] || { ok=0; echo "mistral: $out" >&2; }
    [[ "$out" == *"already registered"* ]] && { ok=0; echo "read the live gateway" >&2; }
    if (( ok )); then pass; else fail "apply dry run depends on the live gateway"; fi
fi

# combos.json "retired" is the one list of ids a rename or removal left in the
# gateway store (9 orphans were deleted by hand on 2026-09-25); apply prunes
# those and nothing else. The sandbox: a stand-in omniroute first on PATH that
# answers `combo list` from list.txt (rendered like the real CLI: ANSI icon,
# padded name, [strategy], status) and logs every other call to calls.log,
# plus a loopback stand-in gateway serving a static /api/health. Nothing here
# reaches the live gateway or its store.
_prune_sandbox() {
    local d
    d="$(mktemp -d)"
    mkdir -p "$d/bin" "$d/gw/api"
    printf 'ok\n' >"$d/gw/api/health"
    printf '# no keys: every provider is skipped\n' >"$d/keys.yml"
    : >"$d/calls.log"
    cat >"$d/bin/omniroute" <<'SH'
#!/usr/bin/env bash
d="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${1:-} ${2:-}" == "combo list" ]]; then
    printf '%s\n' "$*" >>"$d/listed"
    cat "$d/list.txt"
    exit 0
fi
printf '%s\n' "$*" >>"$d/calls.log"
exit 0
SH
    chmod +x "$d/bin/omniroute"
    printf '%s\n' "$d"
}
# _prune_list <dir> <name>... - the store as `omniroute combo list` prints it.
_prune_list() {
    local d="$1" n
    shift
    printf '\n\033[1mCombos\033[0m\n' >"$d/list.txt"
    for n in "$@"; do
        printf '  \033[2m○\033[0m %-25s [%-12s] \033[32menabled\033[0m\n' "$n" priority >>"$d/list.txt"
    done
}
# _prune_apply <dir> [apply args] - apply.sh against the two stand-ins.
_prune_apply() {
    local d="$1" pid port
    shift
    read -r pid port < <(_start_test_http_server "$d/gw")
    if [[ -z "$port" ]]; then
        kill "$pid" 2>/dev/null
        echo "no stand-in gateway"
        return 1
    fi
    PATH="$d/bin:$PATH" AUTOOS_OMNIROUTE_URL="http://127.0.0.1:$port" AUTOOS_KEYS_FILE="$d/keys.yml" \
        bash "$ROOT/configuration/omniroute/apply.sh" "$@" 2>&1
    kill "$pid" 2>/dev/null
}

if it "apply prune: deletes only the retired combos the store holds, never a user-made one"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" tier2 t2-worker my-own-combo
    out="$(_prune_apply "$d")"
    ok=1
    deletes="$(grep '^combo delete' "$d/calls.log")"
    [[ "$deletes" == "combo delete tier2 --yes" ]] || { ok=0; echo "deleted: [$deletes]" >&2; }
    grep -q 'my-own-combo' "$d/calls.log" && { ok=0; echo "the user-made combo was touched" >&2; }
    [[ -s "$d/listed" ]] || { ok=0; echo "the store was never listed" >&2; }
    [[ "$out" == *"  - tier2: retired, deleted"* ]] || { ok=0; echo "out: $out" >&2; }
    [[ "$out" == *"my-own-combo"* ]] && { ok=0; echo "the user-made combo was named" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "prune deleted something other than the retired combo"; fi
fi

if it "apply prune: --dry-run names the retired combo and deletes nothing"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" tier2 t2-worker my-own-combo
    out="$(_prune_apply "$d" --dry-run)"
    ok=1
    [[ -s "$d/listed" ]] || { ok=0; echo "the store was never listed" >&2; }
    grep -q '^combo ' "$d/calls.log" && { ok=0; echo "dry run changed combos: $(cat "$d/calls.log")" >&2; }
    [[ "$out" == *"  - tier2: retired, would delete"* ]] || { ok=0; echo "out: $out" >&2; }
    [[ "$out" == *"retired, deleted"* ]] && { ok=0; echo "dry run claims a deletion" >&2; }
    [[ "$out" == *"my-own-combo"* ]] && { ok=0; echo "the user-made combo was named" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the prune dry run is not a dry run"; fi
fi

if it "apply prune: a second run finds no retired combos and deletes nothing"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" t2-worker my-own-combo
    out="$(_prune_apply "$d")"
    ok=1
    grep -q '^combo delete' "$d/calls.log" && { ok=0; echo "deleted: $(grep '^combo delete' "$d/calls.log")" >&2; }
    [[ "$out" == *"  = no retired combos in the store"* ]] || { ok=0; echo "out: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a clean store is not reported as clean"; fi
fi

# A down gateway must not be listed: the real CLI then falls back to reading
# the store file directly, which is how a test would reach the live store.
if it "apply prune: a down gateway is never listed and nothing is pruned"; then
    d="$(_prune_sandbox)"
    _prune_list "$d" tier2
    out="$(PATH="$d/bin:$PATH" AUTOOS_OMNIROUTE_URL=http://127.0.0.1:1 AUTOOS_KEYS_FILE="$d/keys.yml" \
        bash configuration/omniroute/apply.sh --dry-run 2>&1)"
    ok=1
    [[ -e "$d/listed" ]] && { ok=0; echo "a down gateway was listed" >&2; }
    grep -q '^combo ' "$d/calls.log" && { ok=0; echo "dry run changed combos: $(cat "$d/calls.log")" >&2; }
    [[ "$out" == *"Prune:"*"gateway down - the store is not read, nothing pruned"* ]] || { ok=0; echo "out: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "prune read the store behind a down gateway"; fi
fi

# A sandbox for register-autostart.sh: fake tool binaries on PATH, a temp
# unit dir, and stubs for systemctl/loginctl that only log their arguments.
# The stub reports every unit as active, so no port probing reaches the
# live machine's listeners.
_svc_reg_sandbox() {
    local d
    d="$(mktemp -d)"
    mkdir -p "$d/bin" "$d/units"
    for t in node omniroute opencode litellm; do
        printf '#!/bin/sh\nexit 0\n' >"$d/bin/$t"; chmod +x "$d/bin/$t"
    done
    printf '#!/bin/sh\necho "$*" >>"%s/systemctl.log"\nexit 0\n' "$d" >"$d/bin/fake-systemctl"
    printf '#!/bin/sh\necho "$*" >>"%s/loginctl.log"\n[ "$1" = show-user ] && echo yes\nexit 0\n' "$d" >"$d/bin/fake-loginctl"
    chmod +x "$d/bin/fake-systemctl" "$d/bin/fake-loginctl"
    printf '%s' "$d"
}
_svc_reg() {
    local d="$1"; shift
    # The docker AI stack's ownership marker lives in the operator's config:
    # point is-active at the sandbox so a migrated host cannot change these tests.
    AUTOOS_AI_STACK_CONFIG="${AUTOOS_AI_STACK_CONFIG:-$d/ai-stack}" \
    PATH="$d/bin:$PATH" AUTOOS_SYSTEMD_USER_DIR="$d/units" AUTOOS_OMNIROUTE_ENV="$d/omniroute.env" \
        AUTOOS_SYSTEMCTL="$d/bin/fake-systemctl" AUTOOS_LOGINCTL="$d/bin/fake-loginctl" \
        bash "$ROOT/configuration/autostart/register-autostart.sh" "$@" 2>&1
}

if it "svc: the omniroute unit requires a client key and gets this machine's PATH"; then
    d="$(_svc_reg_sandbox)"
    out="$(_svc_reg "$d" --render autoos-omniroute)"
    ok=1
    # The key requirement comes from ~/.omniroute/.env (operator 2026-09-24):
    # a unit Environment= line would silently override the operator's file.
    [[ "$out" == *"Environment=REQUIRE_API_KEY"* ]] && { ok=0; echo "unit overrides REQUIRE_API_KEY" >&2; }
    [[ "$out" == *"ExecStart=$d/bin/omniroute serve --no-open --port 20128"* ]] || { ok=0; echo "ExecStart: $out" >&2; }
    [[ "$out" == *"Environment=PATH=$d/bin:"* ]] || { ok=0; echo "PATH not filled" >&2; }
    [[ "$out" == *"@"*"@"* ]] && { ok=0; echo "unfilled placeholder" >&2; }
    [[ "$out" == *"Managed by AutoOS"* ]] || { ok=0; echo "no marker" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "omniroute unit render is wrong"; fi
fi

# Review finding 2026-09-25: sed's replacement treats & as "the match", so a
# path holding & rendered @OMNIROUTE@ back into the unit.
if it "svc: a unit renders a tool path that contains an ampersand intact"; then
    d="$(_svc_reg_sandbox)"
    mkdir -p "$d/r&d"
    printf '#!/bin/sh\nexit 0\n' >"$d/r&d/omniroute"; chmod +x "$d/r&d/omniroute"
    out="$(PATH="$d/r&d:$d/bin:$PATH" AUTOOS_SYSTEMD_USER_DIR="$d/units" AUTOOS_OMNIROUTE_ENV="$d/omniroute.env" \
        AUTOOS_SYSTEMCTL="$d/bin/fake-systemctl" AUTOOS_LOGINCTL="$d/bin/fake-loginctl" \
        bash "$ROOT/configuration/autostart/register-autostart.sh" --render autoos-omniroute 2>&1)"
    ok=1
    [[ "$out" == *"ExecStart=$d/r&d/omniroute serve"* ]] || { ok=0; echo "ExecStart: $(grep ExecStart <<<"$out")" >&2; }
    [[ "$(grep '^Environment=PATH=' <<<"$out")" == *":$d/r&d:"* || "$(grep '^Environment=PATH=' <<<"$out")" == *"=$d/r&d:"* ]] \
        || { ok=0; echo "PATH: $(grep 'PATH=' <<<"$out")" >&2; }
    [[ "$out" == *"@"*"@"* ]] && { ok=0; echo "placeholder leaked back" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an & in a path corrupts the unit"; fi
fi

if it "svc: register-autostart --dry-run writes, enables and starts nothing"; then
    d="$(_svc_reg_sandbox)"
    out="$(_svc_reg "$d" --dry-run)"
    ok=1
    [[ -z "$(ls -A "$d/units")" ]] || { ok=0; echo "wrote a unit" >&2; }
    [[ -e "$d/systemctl.log" ]] && grep -qE 'enable|start|daemon-reload' "$d/systemctl.log" && { ok=0; echo "called systemctl" >&2; }
    [[ "$out" == *"would write $d/units/autoos-omniroute.service"* ]] || { ok=0; echo "not announced: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "dry run had side effects"; fi
fi

if it "svc: register-autostart twice: the second run skips, a changed unit is backed up"; then
    d="$(_svc_reg_sandbox)"
    first="$(_svc_reg "$d")"
    second="$(_svc_reg "$d")"
    echo "# hand edit" >>"$d/units/autoos-omniroute.service"
    third="$(_svc_reg "$d")"
    ok=1
    [[ "$first" == *"+ autoos-omniroute: wrote"* ]] || { ok=0; echo "first: $first" >&2; }
    [[ "$second" == *"unit unchanged (skipped)"* && "$second" == *"running (skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$second" == *"+ autoos-omniroute"* ]] && { ok=0; echo "second run changed something" >&2; }
    compgen -G "$d/units/autoos-omniroute.service.autoos-backup-*" >/dev/null || { ok=0; echo "no backup" >&2; }
    grep -q '# hand edit' "$d/units/autoos-omniroute.service" && { ok=0; echo "not converged" >&2; }
    [[ "$third" == *"replaced"* ]] || { ok=0; echo "third: $third" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "register-autostart is not idempotent"; fi
fi

if it "svc: register-autostart appends REQUIRE_API_KEY=true once, with a backup"; then
    d="$(_svc_reg_sandbox)"
    printf 'STORAGE_ENCRYPTION_KEY=keep-me\n' >"$d/omniroute.env"
    _svc_reg "$d" >/dev/null
    second="$(_svc_reg "$d")"
    ok=1
    grep -q '^STORAGE_ENCRYPTION_KEY=keep-me$' "$d/omniroute.env" || { ok=0; echo "existing line lost" >&2; }
    [[ "$(grep -c '^REQUIRE_API_KEY=true$' "$d/omniroute.env")" == 1 ]] || { ok=0; echo "not exactly one line" >&2; }
    compgen -G "$d/omniroute.env.autoos-backup-*" >/dev/null || { ok=0; echo "no backup" >&2; }
    [[ "$second" == *"REQUIRE_API_KEY=true is set"*"(skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    printf 'REQUIRE_API_KEY=false\n' >"$d/omniroute.env"
    third="$(_svc_reg "$d")"
    grep -q '^REQUIRE_API_KEY=false$' "$d/omniroute.env" || { ok=0; echo "overrode an explicit operator value" >&2; }
    [[ "$third" == *"left alone"* ]] || { ok=0; echo "third: $third" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "REQUIRE_API_KEY handling is wrong"; fi
fi

if it "svc: register-autostart never runs a second gateway next to omniroute autostart"; then
    d="$(_svc_reg_sandbox)"
    echo "[Service]" >"$d/units/omniroute.service"
    out="$(_svc_reg "$d")"
    ok=1
    [[ -e "$d/units/autoos-omniroute.service" ]] && { ok=0; echo "wrote a second gateway" >&2; }
    [[ "$out" == *"omniroute autostart disable"* ]] || { ok=0; echo "no hint: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "two gateways would fight over :20128"; fi
fi

if it "svc: register-autostart --unregister removes only units AutoOS wrote"; then
    d="$(_svc_reg_sandbox)"
    _svc_reg "$d" >/dev/null
    printf '[Service]\nExecStart=/bin/true\n' >"$d/units/autoos-foreign.service"
    out="$(_svc_reg "$d" --unregister)"
    ok=1
    [[ -e "$d/units/autoos-omniroute.service" ]] && { ok=0; echo "our unit stayed" >&2; }
    [[ -e "$d/units/autoos-foreign.service" ]] || { ok=0; echo "removed a foreign unit" >&2; }
    grep -q 'disable --now autoos-omniroute.service' "$d/systemctl.log" || { ok=0; echo "not disabled" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "unregister is wrong"; fi
fi

if it "svc: opencode-cli (V2) writes the global provider config after install"; then
    report="$(python3 - <<'PY'
import json, io
want = {"catalog/linux.json": "setup_opencode_config",
        "catalog/macos.json": "setup_opencode_config",
        "catalog/windows.json": "Set-AutoOSOpenCodeConfig"}
bad = []
for f, fn in want.items():
    d = json.load(io.open(f, encoding="utf-8-sig"))
    comps = [c for cat in d["categories"] for c in cat["components"] if c["id"] == "opencode-cli"]
    if not comps or comps[0].get("postInstall") != fn:
        bad.append(f)
print(" ".join(bad) or "ok")
PY
)"
    assert_eq "$report" "ok"
fi

# The user's own config may be JSONC (comments, trailing commas). json.load
# used to fail on it and the writer then started from {} - every provider,
# MCP server and setting the user had was replaced wholesale.
if it "svc: the opencode writer merges into a JSONC config and never writes a key value"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    cat >"$scratch/.config/opencode/config.json" <<'JSONC'
{
  // the user's own theme and provider
  "theme": "nord",
  /* block comment */
  "provider": {
    "mine": {"options": {"baseURL": "http://example.invalid/v1"}},
  },
}
JSONC
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY CONTEXT7_API_KEY
      export OPENROUTER_API_KEY="sk-or-v1-notarealkeyatall0123456789"
      curl() { return 6; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/opencode" <<'PY'
import io, json, os, sys
d = sys.argv[1]
problems = []
for name in ("config.json", "opencode.json"):
    p = os.path.join(d, name)
    if not os.path.isfile(p):
        problems.append("missing:" + name); continue
    raw = io.open(p, encoding="utf-8").read()
    doc = json.loads(raw)
    if doc.get("theme") != "nord": problems.append(name + ":theme-lost")
    if "mine" not in doc.get("provider", {}): problems.append(name + ":provider-lost")
    if "omniroute" not in doc.get("provider", {}): problems.append(name + ":no-omniroute")
    if "notarealkey" in raw: problems.append(name + ":plaintext-key")
    orr = doc.get("provider", {}).get("openrouter", {}).get("options", {}).get("apiKey")
    if orr != "{env:OPENROUTER_API_KEY}": problems.append(name + ":openrouter=%s" % orr)
print(" ".join(problems) or "ok")
PY
)"
    rm -rf "$scratch"
    assert_eq "$report" "ok"
    fi
fi

# Measured live 2026-09-24: the harness rewrote config.json with a trailing
# newline the writer did not emit, so every re-run took a backup; and the
# seeded opencode.json was backed up on its very first write.
if it "svc: the opencode writer takes no backup on a fresh machine or a re-run"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    for _ in 1 2 3; do
        ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    done
    n="$(compgen -G "$scratch/.config/opencode/*.autoos-backup-*" | wc -l)"
    rm -rf "$scratch"
    assert_eq "$n" "0"
    fi
fi

# V2 (@opencode/cli) reads `providers` (package/env/settings), not V1's
# `provider`: measured 2026-09-24, serve from $HOME listed no provider at all
# with only the V1 block written. The V2 block is projected from the repo's
# opencode.jsonc (single source) into opencode.json, never into config.json
# (V1's file).
if it "svc: the opencode writer adds the V2 providers block when the CLI is V2"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
      unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
      curl() { return 6; }
      opencode_is_v2() { return 0; }
      OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    report="$(python3 - "$scratch/.config/opencode" <<'PY2'
import io, json, os, re, sys
d = sys.argv[1]
problems = []
oc = json.load(io.open(os.path.join(d, "opencode.json"), encoding="utf-8"))
v1 = json.load(io.open(os.path.join(d, "config.json"), encoding="utf-8"))
repo = json.loads(re.sub(r"(?m)^\s*//.*$", "", io.open("opencode.jsonc", encoding="utf-8").read()))
for name in ("omniroute", "litellm"):
    got = (oc.get("providers") or {}).get(name)
    if got != repo["providers"][name]:
        problems.append("v2-" + name + "-differs")
if "providers" in v1:
    problems.append("v1-file-got-v2-block")
if oc.get("model") != repo["model"]:
    problems.append("model=%s" % oc.get("model"))
print(" ".join(problems) or "ok")
PY2
)"
    rm -rf "$scratch"
    assert_eq "$report" "ok"
    fi
fi

if it "svc: the opencode writer leaves an unparseable config alone"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    printf '{ "theme": "nord", BROKEN\n' >"$scratch/.config/opencode/config.json"
    ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0; curl() { return 6; }
      setup_opencode_config >/dev/null 2>&1 )
    got="$(cat "$scratch/.config/opencode/config.json")"
    rm -rf "$scratch"
    assert_eq "$got" '{ "theme": "nord", BROKEN'
    fi
fi

if it "svc: opencode.json is backed up before it changes and untouched on a re-run"; then
    if ! has_cmd python3; then skip "python3 not found"; else
    scratch="$(mktemp -d)"
    mkdir -p "$scratch/.config/opencode"
    printf '{"theme": "gruvbox"}\n' >"$scratch/.config/opencode/opencode.json"
    for _ in 1 2; do
        ( SYS_HOME="$scratch" AUTOOS_DRY_RUN=0
          unset META_API_KEY MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
          curl() { return 6; }
          OLLAMA_BASE_URL="http://ollama:11434" setup_opencode_config >/dev/null 2>&1 )
    done
    n="$(compgen -G "$scratch/.config/opencode/opencode.json.autoos-backup-*" | wc -l)"
    rm -rf "$scratch"
    assert_eq "$n" "1"
    fi
fi

# opencode serve (V2) answers the static UI to anyone and guards /api/* with
# HTTP Basic (user "opencode", password from OPENCODE_PASSWORD; measured on
# 2.0.16). Without the variable it invents a random password per start, so
# the phone could never log in twice. The wrapper pins one, in a 0600 file.
if it "svc: run-opencode-serve binds the LAN port with a pinned password and keys by name"; then
    d="$(mktemp -d)"
    mkdir -p "$d/lit"
    printf 'LITELLM_MASTER_KEY=sk-litellm-fake-000\nOPENROUTER_API_KEY=REPLACE_WITH_X\n' >"$d/lit/.env"
    printf 'omniroute: sk-omni-fake-111\n' >"$d/keys.yml"
    out="$(env -u AUTOOS_OMNIROUTE_KEY AUTOOS_LITELLM_DIR="$d/lit" AUTOOS_KEYS_FILE="$d/keys.yml" \
        AUTOOS_OPENCODE_PASSWORD_FILE="$d/pw" \
        bash "$ROOT/configuration/autostart/run-opencode-serve.sh" --dry-run 2>&1)"
    ok=1
    [[ "$out" == *"opencode serve --hostname 0.0.0.0 --port 4096"* ]] || { ok=0; echo "bind: $out" >&2; }
    [[ "$out" == *"LITELLM_MASTER_KEY"* && "$out" == *"AUTOOS_OMNIROUTE_KEY"* ]] || { ok=0; echo "keys: $out" >&2; }
    [[ "$out" == *"OPENROUTER_API_KEY"* ]] && { ok=0; echo "placeholder loaded" >&2; }
    [[ "$out" == *"fake-000"* || "$out" == *"fake-111"* ]] && { ok=0; echo "a value was printed" >&2; }
    [[ "$out" == *"would generate"*"$d/pw"* ]] || { ok=0; echo "password plan: $out" >&2; }
    [[ -e "$d/pw" ]] && { ok=0; echo "dry run wrote the password file" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "run-opencode-serve plan is wrong"; fi
fi

if it "svc: run-opencode-serve generates the password once, private, and reuses it"; then
    d="$(mktemp -d)"
    mkdir -p "$d/bin"
    # A fake opencode that reports whether it received a password.
    printf '#!/bin/sh\n[ -n "$OPENCODE_PASSWORD" ] && echo "pw-set $*" || echo "pw-missing $*"\n' >"$d/bin/opencode"
    chmod +x "$d/bin/opencode"
    run() { PATH="$d/bin:$PATH" AUTOOS_LITELLM_DIR="$d/none" AUTOOS_KEYS_FILE="$d/none.yml" \
        AUTOOS_OPENCODE_PASSWORD_FILE="$d/cfg/pw" bash "$ROOT/configuration/autostart/run-opencode-serve.sh" 2>&1; }
    first="$(run)"; pw1="$(cat "$d/cfg/pw" 2>/dev/null)"
    second="$(run)"; pw2="$(cat "$d/cfg/pw" 2>/dev/null)"
    mode="$(stat -c %a "$d/cfg/pw" 2>/dev/null)"
    ok=1
    [[ "$first" == *"pw-set serve --hostname 0.0.0.0 --port 4096"* ]] || { ok=0; echo "first: $first" >&2; }
    [[ ${#pw1} -ge 24 && "$pw1" == "$pw2" ]] || { ok=0; echo "password not stable" >&2; }
    [[ "$mode" == 600 ]] || { ok=0; echo "mode $mode" >&2; }
    [[ "$first$second" == *"$pw1"* ]] && { ok=0; echo "password printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "password handling is wrong"; fi
fi

if it "svc: the opencode unit runs the wrapper from this checkout"; then
    d="$(_svc_reg_sandbox)"
    out="$(_svc_reg "$d" --render autoos-opencode)"
    ok=1
    [[ "$out" == *"ExecStart=$ROOT/configuration/autostart/run-opencode-serve.sh"* ]] || { ok=0; echo "$out" >&2; }
    [[ "$out" == *"Environment=PATH=$d/bin:"* ]] || { ok=0; echo "PATH" >&2; }
    [[ "$out" == *"@"*"@"* ]] && { ok=0; echo "unfilled placeholder" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "opencode unit render is wrong"; fi
fi

# A service started by systemd never sees a token exported from ~/.zshrc, so
# opencode's omnigraph bridge "connects" and then fails every read with
# "missing bearer token" (B-omnigraph 2026-09-24, item 6). The per-user env
# file write_omnigraph_env keeps is the one source; the leading dash makes it
# optional so a machine without it still starts. Only units that start an
# omnigraph client get it: OmniRoute listens on the LAN and never needs it.
if it "svc: omnigraph units read the per-user env file (optional), the gateways do not"; then
    d="$(_svc_reg_sandbox)"
    want='EnvironmentFile=-%h/.autoos-omnigraph.env'
    ok=1
    for u in autoos-opencode autoos-stack; do
        out="$(_svc_reg "$d" --render "$u")" || { ok=0; echo "$u: render failed: $out" >&2; continue; }
        [[ "$(grep -c '^EnvironmentFile=' <<<"$out")" == 1 ]] || { ok=0; echo "$u: not exactly one EnvironmentFile= line" >&2; }
        grep -qxF "$want" <<<"$out" || { ok=0; echo "$u: missing [$want]" >&2; }
        # In [Service]: after its header, before [Install].
        svc_line="$(grep -nx '\[Service\]' <<<"$out" | cut -d: -f1)"
        env_line="$(grep -nxF "$want" <<<"$out" | cut -d: -f1)"
        inst_line="$(grep -nx '\[Install\]' <<<"$out" | cut -d: -f1)"
        [[ -n "$env_line" && "$env_line" -gt "$svc_line" && "$env_line" -lt "$inst_line" ]] \
            || { ok=0; echo "$u: EnvironmentFile= is outside [Service]" >&2; }
        # Comments may name the variable; a directive must never carry a value.
        [[ "$(grep -v '^#' <<<"$out")" == *"OMNIGRAPH_TOKEN"* ]] && { ok=0; echo "$u: a token is set in the unit text" >&2; }
    done
    for u in autoos-omniroute autoos-litellm; do
        out="$(_svc_reg "$d" --render "$u")" || { ok=0; echo "$u: render failed: $out" >&2; continue; }
        [[ "$out" == *"autoos-omnigraph.env"* ]] && { ok=0; echo "$u: the LAN-facing gateway must not get the omnigraph token" >&2; }
    done
    rm -rf "$d"
    if (( ok )); then pass; else fail "units do not read ~/.autoos-omnigraph.env as expected"; fi
fi

if it "svc: an installed opencode unit gains the omnigraph line once: backed up, then skipped"; then
    d="$(_svc_reg_sandbox)"
    # What an earlier AutoOS wrote: today's render minus the new line.
    _svc_reg "$d" --render autoos-opencode | grep -v '^EnvironmentFile=' >"$d/old.service"
    cp "$d/old.service" "$d/units/autoos-opencode.service"
    first="$(_svc_reg "$d" --only autoos-opencode)"
    second="$(_svc_reg "$d" --only autoos-opencode)"
    nbak="$(find "$d/units" -name 'autoos-opencode.service.autoos-backup-*' | wc -l)"
    ok=1
    [[ "$first" == *"+ autoos-opencode: replaced"*"(backup: "* ]] || { ok=0; echo "first: $first" >&2; }
    grep -qxF 'EnvironmentFile=-%h/.autoos-omnigraph.env' "$d/units/autoos-opencode.service" \
        || { ok=0; echo "unit did not gain the line" >&2; }
    [[ "$nbak" == 1 ]] || { ok=0; echo "expected exactly one backup, found $nbak" >&2; }
    cmp -s "$d/old.service" "$(find "$d/units" -name 'autoos-opencode.service.autoos-backup-*' | head -n1)" \
        || { ok=0; echo "the backup is not the previous unit" >&2; }
    [[ "$second" == *"= autoos-opencode: unit unchanged (skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$second" == *"+ autoos-opencode"* ]] && { ok=0; echo "second run changed something" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "adding the omnigraph line is not idempotent and backed up"; fi
fi

# systemd runs ExecStart directly: a 100644 launcher fails with 203/EXEC.
if it "svc: every script a unit or the healthcheck runs is executable in git"; then
    bad=""
    for f in configuration/autostart/Start-AutoOSStack.sh configuration/autostart/register-autostart.sh \
             configuration/autostart/run-opencode-serve.sh configuration/litellm/start-litellm.sh \
             configuration/healthcheck.sh configuration/start-stack.sh; do
        mode="$(git ls-files -s -- "$f" | cut -d' ' -f1)"
        [[ "$mode" == 100755 ]] || bad+="$f=$mode "
    done
    assert_eq "$bad" ""
fi

if it "svc: every unit template renders with this checkout and no placeholder left"; then
    d="$(_svc_reg_sandbox)"
    ok=1
    for u in autoos-omniroute autoos-litellm autoos-opencode autoos-stack; do
        out="$(_svc_reg "$d" --render "$u")" || { ok=0; echo "$u: render failed: $out" >&2; continue; }
        [[ "$out" =~ @[A-Z]+@ ]] && { ok=0; echo "$u: unfilled placeholder" >&2; }
        [[ "$out" == *"%h/AutoOS"* ]] && { ok=0; echo "$u: hard-coded ~/AutoOS" >&2; }
        [[ "$out" == *"WantedBy=default.target"* ]] || { ok=0; echo "$u: not boot-wanted" >&2; }
    done
    stack="$(_svc_reg "$d" --render autoos-stack)"
    [[ "$stack" == *"ExecStart=$ROOT/configuration/autostart/Start-AutoOSStack.sh"* ]] || { ok=0; echo "stack ExecStart" >&2; }
    [[ "$stack" == *"Type=oneshot"* ]] || { ok=0; echo "stack not oneshot" >&2; }
    lit="$(_svc_reg "$d" --render autoos-litellm)"
    [[ "$lit" == *"start-litellm.sh --foreground"* ]] || { ok=0; echo "litellm ExecStart" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "unit templates do not render cleanly"; fi
fi

if it "svc: register-autostart registers the four units in dependency order"; then
    d="$(_svc_reg_sandbox)"
    _svc_reg "$d" >/dev/null
    order="$(grep -oE '^--user enable autoos-[a-z]+' "$d/systemctl.log" | awk '{print $3}' | tr '\n' ' ')"
    rm -rf "$d"
    assert_eq "$order" "autoos-omniroute autoos-litellm autoos-opencode autoos-stack "
fi

if it "svc: the stack launcher starts each service through its unit when one is installed"; then
    ok=1
    for u in autoos-omniroute autoos-litellm autoos-opencode; do
        grep -q "unit_installed $u" configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "no unit path: $u" >&2; }
        grep -q "systemctl --user start $u.service" configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "not started via unit: $u" >&2; }
    done
    grep -q 'run-opencode-serve.sh' configuration/autostart/Start-AutoOSStack.sh || { ok=0; echo "serve fallback bypasses the password wrapper" >&2; }
    grep -q '/tmp/' configuration/autostart/Start-AutoOSStack.sh && { ok=0; echo "logs to /tmp" >&2; }
    if (( ok )); then pass; else fail "launcher bypasses the units"; fi
fi

if it "svc: healthcheck --fix resumes opencode serve and litellm too"; then
    ok=1
    grep -qE 'ANY_DOWN=1' configuration/healthcheck.sh || { ok=0; echo "no combined down flag" >&2; }
    grep -qE 'OC_UP -eq 0' configuration/healthcheck.sh || { ok=0; echo "serve not in the fix condition" >&2; }
    grep -qE 'LT_UP -eq 0' configuration/healthcheck.sh || { ok=0; echo "litellm not in the fix condition" >&2; }
    grep -q '4000' configuration/healthcheck.sh || { ok=0; echo "no :4000 probe" >&2; }
    if (( ok )); then pass; else fail "healthcheck --fix leaves a service down"; fi
fi

# The profiles in ~/.openhands are read by whichever OpenHands runs with that
# directory: the docker app (host.docker.internal via --add-host) or a native
# host process such as agent-canvas, which cannot resolve that name on a
# native Linux host. Like resolve_ollama_base_url: keep it where it resolves.
if it "svc: profile sync picks the gateway host per consumer"; then
    d="$(mktemp -d)"
    # sync_base <resolves 0|1> [--consumer X]: the omniroute-t2-worker base_url.
    sync_base() {
        local resolves="$1"; shift
        rm -rf "$d/oh"
        env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key AUTOOS_FAKE_HDI_RESOLVES="$resolves" \
            python3 "$ROOT/tools/sync-openhands-profiles.py" --openhands-dir "$d/oh" \
            --keys-file "$d/none.yml" --litellm-env "$d/none.env" "$@" >/dev/null 2>&1
        python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_url"])' \
            "$d/oh/profiles/omniroute-t2-worker.json" 2>/dev/null
    }
    got="$(sync_base 0)|$(sync_base 0 --consumer native)|$(sync_base 1 --consumer native)"
    rm -rf "$d"
    assert_eq "$got" \
        "http://host.docker.internal:20128/v1|http://127.0.0.1:20128/v1|http://host.docker.internal:20128/v1"
fi

# The image's entrypoint runs everything as root unless SANDBOX_USER_ID names
# the host user (image default 0): ~/.openhands then fills with root-owned
# files the host-side profile sync can no longer write. A missing host dir is
# created by docker as root, so it must exist before `docker run`.
if it "svc: start-stack openhands runs as the host user, survives reboots and is capped"; then
    block="$(sed -n '/^    openhands)/,/^        ;;/p' configuration/start-stack.sh)"
    ok=1
    [[ "$block" == *'SANDBOX_USER_ID="$(id -u)"'* ]] || { ok=0; echo "no SANDBOX_USER_ID" >&2; }
    [[ "$block" == *'mkdir -p "$HOME/.openhands"'* ]] || { ok=0; echo "dir not pre-created" >&2; }
    [[ "$block" == *'--restart unless-stopped'* ]] || { ok=0; echo "not reboot-safe" >&2; }
    [[ "$block" == *'docker run -d --rm'* ]] && { ok=0; echo "--rm defeats the restart policy" >&2; }
    [[ "$block" == *'--memory'* ]] || { ok=0; echo "no memory cap" >&2; }
    [[ "$block" == *'--consumer container'* ]] || { ok=0; echo "profile consumer not named" >&2; }
    # Remote browsers need the sandbox URL pattern; opt-in, never a default.
    [[ "$block" == *'OH_SANDBOX_CONTAINER_URL_PATTERN="$AUTOOS_OPENHANDS_SANDBOX_URL"'* ]] || { ok=0; echo "no sandbox URL passthrough" >&2; }
    [[ "$block" == *'if [[ -n "${AUTOOS_OPENHANDS_SANDBOX_URL:-}" ]]'* ]] || { ok=0; echo "sandbox URL not opt-in" >&2; }
    if (( ok )); then pass; else fail "OpenHands launch is not native-Linux safe"; fi
fi

# SDK-1.36 OpenHands keeps LLM profiles in its own settings store and never
# reads profiles/*.json (measured 2026-09-24: /api/v1/settings/profiles was
# empty with 16 files synced). The push saves them through the app's API.
if it "svc: profile sync pushes the tiers into a running app, idempotently and capped"; then
    d="$(mktemp -d)"
    python3 "$ROOT/tests/helpers/fake_openhands_app.py" "$d/port" "$d/req.log" >/dev/null 2>&1 &
    fake_pid=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    url="http://127.0.0.1:$(cat "$d/port")"
    push() { env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$d/oh" --keys-file "$d/none.yml" --litellm-env "$d/none.env" --push-url "$url" 2>&1; }
    first="$(push)"
    : >"$d/req.log"
    second="$(push)"
    kill "$fake_pid" 2>/dev/null
    ok=1
    [[ "$first" == *"app settings seeded with omniroute-t1-orchestrator"* ]] || { ok=0; echo "not seeded: $first" >&2; }
    # Spec order = configuration/openhands/tier-profiles.json: the three
    # hierarchy tiers (t1 -> t2 -> t3) fill the fake's cap of 3; the red-by-
    # design t1-orchestrator-free-only is last and never takes a slot.
    for _t in omniroute-t1-orchestrator omniroute-t2-worker omniroute-t3-driver; do
        [[ "$first" == *"app profile $_t saved"* ]] || { ok=0; echo "spec order ($_t): $first" >&2; }
    done
    [[ "$first" == *"app profile omniroute-t2-orchestrator saved"* ]] && { ok=0; echo "cap 3 should stop before t2-orchestrator" >&2; }
    [[ "$first" == *"app profile omniroute-t1-orchestrator-free-only saved"* ]] && { ok=0; echo "free-only took a slot" >&2; }
    [[ "$first" == *"profile cap is reached"* ]] || { ok=0; echo "cap not reported" >&2; }
    [[ "$first" == *"FAILED"* ]] && { ok=0; echo "a push failed (StrictLLM?): $first" >&2; }
    grep -q '^POST' "$d/req.log" && grep -q '^POST /api/v1/settings/profiles/omniroute-t1-orchestrator$' "$d/req.log" \
        && { ok=0; echo "second run re-posted an unchanged profile" >&2; }
    [[ "$second" == *"omniroute-t1-orchestrator skipped (up to date)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$first$second" == *"sk-fake-profile-key"* ]] && { ok=0; echo "key printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "profile push is wrong"; fi
fi

# The app keeps at most 10 profiles and never forgets one, so a renamed or
# dropped tier kept its slot forever. The push deletes the profiles AutoOS
# owns that the spec no longer lists, before it pushes. Ownership is a RECORD,
# never a name prefix (review 2026-09-25: a user's `omniroute-personal` must
# survive): the names this sync pushed (.autoos-pushed.json next to the
# profiles) plus the spec's retired_ids (the ids from before that record).
# The active profile is never deleted, and nothing is when the app does not
# say which one is active.
# _fake_app <dir> [<seed.json>]: starts the fake and sets fake_pid + url.
# Never call it inside $(...): the pid would be set in that subshell only,
# the caller's kill would miss, and the fake would outlive the test.
_fake_app() {
    python3 "$ROOT/tests/helpers/fake_openhands_app.py" "$1/port" "$1/req.log" ${2:+"$2"} >/dev/null 2>&1 &
    fake_pid=$!
    for _ in $(seq 1 50); do [[ -s "$1/port" ]] && break; sleep 0.1; done
    url="http://127.0.0.1:$(cat "$1/port")"
}
_fake_profiles() {  # _fake_profiles <url>: the app's profile names, one per line
    python3 -c 'import json,sys,urllib.request; print("\n".join(p["name"] for p in json.load(urllib.request.urlopen(sys.argv[1] + "/api/v1/settings/profiles"))["profiles"]))' "$1"
}
_push_to() {  # _push_to <dir> <url>
    env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$1/oh" --keys-file "$1/none.yml" --litellm-env "$1/none.env" --push-url "$2" 2>&1
}
_seed_pushed() {  # _seed_pushed <dir> <name>...: records <name>s as pushed by an earlier sync
    local dir="$1"; shift
    mkdir -p "$dir/oh/profiles"
    python3 -c 'import json,sys; json.dump({n: "seeded" for n in sys.argv[2:]}, open(sys.argv[1], "w"))' \
        "$dir/oh/profiles/.autoos-pushed.json" "$@"
}

if it "svc: profile push deletes retired AutoOS profiles, never a foreign one"; then
    d="$(mktemp -d)"
    cat >"$d/seed.json" <<'JSON'
{"cap": 10, "settings": {"agent_settings_diff": {}},
 "profiles": {"omniroute-tier1": {"model": "openai/tier1"},
              "litellm-tier2": {"model": "openai/tier2"},
              "openrouter-gone": {"model": "openrouter/x"},
              "omniroute-personal": {"model": "openai/mine", "api_key": "k"},
              "my-own-profile": {"model": "openai/mine", "api_key": "k"},
              "omniroute": {"model": "openai/prefix-without-dash"}}}
JSON
    _seed_pushed "$d" openrouter-gone
    _fake_app "$d" "$d/seed.json"
    first="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url")"
    first_log="$(cat "$d/req.log")"
    : >"$d/req.log"
    second="$(_push_to "$d" "$url")"
    second_deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    kill "$fake_pid" 2>/dev/null
    ok=1
    # legacy retired_ids (tier1, litellm-tier2) and a recorded push (gone) go
    for _r in omniroute-tier1 litellm-tier2 openrouter-gone; do
        [[ "$first" == *"app profile $_r deleted (retired"* ]] || { ok=0; echo "not deleted: $_r: $first" >&2; }
        grep -qx "$_r" <<<"$after" && { ok=0; echo "still in the app: $_r" >&2; }
    done
    # an AutoOS-looking prefix is not ownership
    for _k in omniroute-personal my-own-profile omniroute; do
        grep -qx "$_k" <<<"$after" || { ok=0; echo "a foreign profile was deleted: $_k" >&2; }
        grep -q "^DELETE .*/$_k\$" <<<"$first_log" && { ok=0; echo "DELETE sent for $_k" >&2; }
    done
    grep -qx omniroute-t1-orchestrator <<<"$after" || { ok=0; echo "t1 not pushed: $after" >&2; }
    [[ "$second_deletes" == 0 ]] || { ok=0; echo "second run deleted again ($second_deletes)" >&2; }
    [[ "$second" == *"deleted"* ]] && { ok=0; echo "second run reports a delete: $second" >&2; }
    [[ "$first$second" == *"sk-fake-profile-key"* ]] && { ok=0; echo "key printed" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "retired profiles are not cleaned up safely"; fi
fi

if it "svc: profile push deletes retired profiles before it saves any"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 3, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-tier1": {"model": "openai/tier1"}, "omniroute-tier2": {"model": "openai/tier2"}, "omniroute-tier3": {"model": "openai/tier3"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    kill "$fake_pid" 2>/dev/null
    last_delete="$(grep -n '^DELETE' "$d/req.log" | tail -1 | cut -d: -f1)"
    first_save="$(grep -n '^POST /api/v1/settings/profiles/' "$d/req.log" | head -1 | cut -d: -f1)"
    rm -rf "$d"
    ok=1
    [[ -n "$last_delete" && -n "$first_save" && "$last_delete" -lt "$first_save" ]] || { ok=0; echo "delete line $last_delete, first save line $first_save" >&2; }
    [[ "$out" == *"app profile omniroute-t3-driver saved"* ]] || { ok=0; echo "freed slots unused: $out" >&2; }
    if (( ok )); then pass; else fail "retired profiles still hold slots during the push"; fi
fi

if it "svc: profile push never deletes the active profile, even a retired one"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 10, "settings": {"agent_settings_diff": {}}, "active": "omniroute-tier1", "profiles": {"omniroute-tier1": {"model": "openai/tier1"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url")"
    kill "$fake_pid" 2>/dev/null
    deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$deletes" == 0 ]] || { ok=0; echo "DELETE sent ($deletes)" >&2; }
    grep -qx omniroute-tier1 <<<"$after" || { ok=0; echo "active profile gone" >&2; }
    [[ "$out" == *"omniroute-tier1 is retired but active"* ]] || { ok=0; echo "not announced: $out" >&2; }
    if (( ok )); then pass; else fail "the active profile was not protected"; fi
fi

if it "svc: profile push deletes nothing when the app does not name its active profile"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 10, "omit_active_key": true, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-tier1": {"model": "openai/tier1"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    kill "$fake_pid" 2>/dev/null
    deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    saves="$(grep -c '^POST /api/v1/settings/profiles/' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$deletes" == 0 ]] || { ok=0; echo "DELETE sent ($deletes)" >&2; }
    [[ "$out" == *"does not say which profile is active - deleting nothing"* ]] || { ok=0; echo "not warned: $out" >&2; }
    (( saves > 0 )) || { ok=0; echo "the push itself stopped" >&2; }
    if (( ok )); then pass; else fail "a delete ran without knowing the active profile"; fi
fi

if it "svc: profile push re-checks the active profile right before each delete"; then
    # The fake makes omniroute-tier1 active on its 2nd profile listing - a
    # UI switch between the push's first look and its delete.
    d="$(mktemp -d)"
    printf '%s' '{"cap": 10, "activate_on_list": {"call": 2, "name": "omniroute-tier1"}, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-tier1": {"model": "openai/tier1"}}}' >"$d/seed.json"
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url")"
    kill "$fake_pid" 2>/dev/null
    deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$deletes" == 0 ]] || { ok=0; echo "DELETE sent ($deletes)" >&2; }
    grep -qx omniroute-tier1 <<<"$after" || { ok=0; echo "the newly active profile was deleted" >&2; }
    [[ "$out" == *"omniroute-tier1 became the active profile - left in place"* ]] || { ok=0; echo "not announced: $out" >&2; }
    if (( ok )); then pass; else fail "a profile made active mid-run was deleted"; fi
fi

# Spec order decides which tiers make the cap - also in an app that already
# holds lower-ranked AutoOS profiles from an older order (measured 2026-09-25:
# the live app held 10 in-spec profiles, 0 retired, so deleting retired ids
# alone freed nothing and t3-driver/t4-rag still never fit).
if it "svc: profile push makes room for a higher-ranked tier by removing the lowest-ranked AutoOS one"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 3, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-t1-orchestrator-free-only": {"model": "openai/t1-orchestrator-free-only"}, "omniroute-spark-1.3-contributor": {"model": "openai/spark-1.3-contributor"}, "my-own-profile": {"model": "openai/mine"}}}' >"$d/seed.json"
    _seed_pushed "$d" omniroute-t1-orchestrator-free-only omniroute-spark-1.3-contributor
    _fake_app "$d" "$d/seed.json"
    first="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url" | sort | tr '\n' ' ')"
    : >"$d/req.log"
    second="$(_push_to "$d" "$url")"
    second_deletes="$(grep -c '^DELETE' "$d/req.log" || true)"
    kill "$fake_pid" 2>/dev/null
    rm -rf "$d"
    ok=1
    # One slot is the user's; the two AutoOS slots go to the spec's top two.
    [[ "$after" == "my-own-profile omniroute-t1-orchestrator omniroute-t2-worker " ]] || { ok=0; echo "app holds: $after" >&2; }
    [[ "$first" == *"omniroute-t1-orchestrator-free-only removed to make room for omniroute-t1-orchestrator"* ]] || { ok=0; echo "eviction not announced: $first" >&2; }
    [[ "$first" == *"profile cap is reached"* ]] || { ok=0; echo "cap not reported" >&2; }
    [[ "$second_deletes" == 0 ]] || { ok=0; echo "second run evicted again ($second_deletes)" >&2; }
    if (( ok )); then pass; else fail "the cap is not filled in spec order"; fi
fi

# Rank comes from the FULL spec order: an AutoOS tier the app holds but this
# run could not build (no key for its gateway) still ranks, so it can make
# room for a higher tier. A prefix-named profile nobody recorded never does.
if it "svc: profile push evicts an unbuilt AutoOS tier below the refused one, never a foreign profile"; then
    d="$(mktemp -d)"
    printf '%s' '{"cap": 3, "settings": {"agent_settings_diff": {}}, "profiles": {"omniroute-personal": {"model": "openai/mine"}, "litellm-t2-worker": {"model": "openai/t2-worker"}, "omniroute-t1-orchestrator-free-only": {"model": "openai/t1-orchestrator-free-only"}}}' >"$d/seed.json"
    _seed_pushed "$d" litellm-t2-worker omniroute-t1-orchestrator-free-only
    _fake_app "$d" "$d/seed.json"
    out="$(_push_to "$d" "$url")"
    after="$(_fake_profiles "$url" | sort | tr '\n' ' ')"
    kill "$fake_pid" 2>/dev/null
    personal_deletes="$(grep -c '^DELETE .*/omniroute-personal$' "$d/req.log" || true)"
    rm -rf "$d"
    ok=1
    [[ "$after" == "omniroute-personal omniroute-t1-orchestrator omniroute-t2-worker " ]] || { ok=0; echo "app holds: $after" >&2; }
    [[ "$out" == *"litellm-t2-worker removed to make room for omniroute-t2-worker"* ]] || { ok=0; echo "unbuilt tier not ranked: $out" >&2; }
    [[ "$personal_deletes" == 0 ]] || { ok=0; echo "omniroute-personal DELETEd" >&2; }
    if (( ok )); then pass; else fail "eviction ranks or ownership are wrong"; fi
fi

# The app never returns a profile's key (only api_key_set), so "every other
# field matches" cannot see a rotated key - it was skipped forever (review
# finding 2026-09-25). The sync records a short hash of the last pushed key.
if it "svc: profile push re-sends a profile whose key was rotated"; then
    d="$(mktemp -d)"
    python3 "$ROOT/tests/helpers/fake_openhands_app.py" "$d/port" "$d/req.log" >/dev/null 2>&1 &
    fake_pid=$!
    for _ in $(seq 1 50); do [[ -s "$d/port" ]] && break; sleep 0.1; done
    url="http://127.0.0.1:$(cat "$d/port")"
    push() { env AUTOOS_OMNIROUTE_KEY="$1" python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$d/oh" --keys-file "$d/none.yml" --litellm-env "$d/none.env" --push-url "$url" 2>&1; }
    push sk-fake-key-one >/dev/null
    : >"$d/req.log"
    same="$(push sk-fake-key-one)"
    same_posts="$(grep -c '^POST /api/v1/settings/profiles/omniroute-t1-orchestrator$' "$d/req.log" || true)"
    : >"$d/req.log"
    rotated="$(push sk-fake-key-two)"
    rotated_posts="$(grep -c '^POST /api/v1/settings/profiles/omniroute-t1-orchestrator$' "$d/req.log" || true)"
    kill "$fake_pid" 2>/dev/null
    state_mode="$(stat -c %a "$d/oh/profiles/.autoos-pushed.json" 2>/dev/null)"
    leaked="$(grep -c 'sk-fake-key' "$d/oh/profiles/.autoos-pushed.json" 2>/dev/null || true)"
    rm -rf "$d"
    ok=1
    [[ "$same_posts" == 0 ]] || { ok=0; echo "unchanged key re-posted ($same_posts)" >&2; }
    [[ "$rotated_posts" == 1 ]] || { ok=0; echo "rotated key not pushed ($rotated_posts): $rotated" >&2; }
    [[ "$state_mode" == 600 ]] || { ok=0; echo "state file mode $state_mode" >&2; }
    [[ "$leaked" == 0 ]] || { ok=0; echo "raw key in the state file" >&2; }
    if (( ok )); then pass; else fail "a rotated key never reaches the app"; fi
fi

# The push used to run as `... | grep -v skipped || true`, which swallowed a
# failing sync (exit 2) under pipefail (review finding 2026-09-25).
if it "svc: start-stack reports a failing profile push instead of hiding it"; then
    block="$(sed -n '/^    openhands)/,/^        ;;/p' configuration/start-stack.sh)"
    ok=1
    [[ "$block" == *"skipped (up to date)\$' || true"* ]] && [[ "$block" != *"push_rc"* ]] && { ok=0; echo "push failure still swallowed" >&2; }
    [[ "$block" == *'push_rc=$?'* ]] || { ok=0; echo "push exit code not kept" >&2; }
    [[ "$block" == *'tier-profile push failed'* ]] || { ok=0; echo "no warning on a failed push" >&2; }
    if (( ok )); then pass; else fail "a failed profile push is hidden"; fi
fi

if it "svc: profile push skips cleanly when no app answers"; then
    d="$(mktemp -d)"
    out="$(env AUTOOS_OMNIROUTE_KEY=sk-fake-profile-key python3 "$ROOT/tools/sync-openhands-profiles.py" \
        --openhands-dir "$d/oh" --keys-file "$d/none.yml" --litellm-env "$d/none.env" \
        --push-url http://127.0.0.1:1 2>&1)"; rc=$?
    rm -rf "$d"
    if [[ $rc -eq 0 && "$out" == *"push skipped"* ]]; then pass; else fail "rc=$rc $out"; fi
fi

# The browser UI shows the AI services live and offers the setup actions that
# already exist. Payload and action mapping are tested through an injected
# probe and runner: nothing is probed on this machine, nothing is started.
_svc_serve_py() {
    python3 - <<'PY'
import importlib.util, json, pathlib, sys, tempfile
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-serve-"))
spec = importlib.util.spec_from_file_location("autoos_serve", "lib/linux/serve.py")
mod = importlib.util.module_from_spec(spec)
sys.argv = ["serve.py", str(root), "0", "127.0.0.1", "0"]
spec.loader.exec_module(mod)
codes = {20128: 200, 4000: 0, 4096: 401, 3000: 200}
status = mod.service_status(probe=lambda port, path: codes[port])
by = {s["id"]: s for s in status}
problems = []
for sid in ("omniroute", "litellm", "opencode", "openhands"):
    s = by.get(sid)
    if not s:
        problems.append("missing:" + sid); continue
    for k in ("name", "port", "bind", "auth", "health", "up", "actions"):
        if k not in s: problems.append(sid + ":no-" + k)
up = {k: v["up"] for k, v in by.items()}
if up != {"omniroute": True, "litellm": False, "opencode": True, "openhands": True}:
    problems.append("up=%s" % up)
if by["litellm"]["bind"] != "127.0.0.1": problems.append("litellm-bind")
if by["opencode"]["bind"] != "0.0.0.0": problems.append("opencode-bind")
if "apply-dry-run" not in by["omniroute"]["actions"]: problems.append("no-apply-dry-run")
if "start-openhands" not in by["openhands"]["actions"]: problems.append("no-start-openhands")
if "start-opencode-serve" not in by["opencode"]["actions"]: problems.append("no-start-serve")
# every advertised action is in the allowlist, with a repo script behind it
for s in status:
    for a in s["actions"]:
        if a not in mod.SERVICE_ACTIONS: problems.append("unlisted:" + a)
for key, act in mod.SERVICE_ACTIONS.items():
    argv = act["argv"]
    if argv[0] != "bash" or not (pathlib.Path("lib/linux/serve.py").resolve().parents[2] / argv[1]).is_file():
        problems.append("not-a-repo-script:" + key)
if mod.SERVICE_ACTIONS["apply-dry-run"]["live"] or "--dry-run" not in mod.SERVICE_ACTIONS["apply-dry-run"]["argv"]:
    problems.append("dry-run-action-is-live")
# the request side: unknown refused, live refused under --dry-run serving
ran = []
mod.start_service_action = lambda key: ran.append(key)
code, _ = mod.service_action_response({"action": "rm -rf /"})
if code != 400: problems.append("unknown-action=%s" % code)
code, _ = mod.service_action_response({"action": "apply-dry-run"})
if code != 202 or ran != ["apply-dry-run"]: problems.append("dry-run-not-started:%s" % code)
mod.FORCE_DRY = True
code, _ = mod.service_action_response({"action": "start-openhands"})
if code != 409 or ran != ["apply-dry-run"]: problems.append("live-action-under-dry-serve:%s" % code)
print(" ".join(problems) or "ok")
PY
}

if it "svc: the web server reports every AI service and maps actions to an allowlist"; then
    assert_eq "$(_svc_serve_py 2>&1 | tail -n 1)" "ok"
fi

if it "svc: the web UI shows service status and confirms every live action"; then
    ok=1
    grep -q 'api("/api/services")' web/index.html || { ok=0; echo "no status fetch" >&2; }
    grep -q '/api/services/action' web/index.html || { ok=0; echo "no action call" >&2; }
    body="$(sed -n '/^async function runServiceAction/,/^}/p' web/index.html)"
    [[ "$body" == *"confirm("* ]] || { ok=0; echo "live action without confirm" >&2; }
    grep -q 'id="services"' web/index.html || { ok=0; echo "no services list" >&2; }
    grep -q 'backend apply/switch not yet wired' web/index.html && { ok=0; echo "stale subtitle" >&2; }
    if (( ok )); then pass; else fail "service status/actions not wired in the page"; fi
fi

# The page is shared: the Windows server must answer the same routes.
if it "svc: the Windows server answers the same service routes"; then
    ok=1
    grep -q "'/api/services'" lib/windows/AutoOS.Serve.psm1 || { ok=0; echo "no GET route" >&2; }
    grep -q "'/api/services/action'" lib/windows/AutoOS.Serve.psm1 || { ok=0; echo "no POST route" >&2; }
    for a in apply-dry-run apply start-openhands start-opencode-serve resume-stack; do
        grep -q "'$a'" lib/windows/AutoOS.Serve.psm1 || { ok=0; echo "missing action $a" >&2; }
    done
    if (( ok )); then pass; else fail "Windows server lacks the service routes"; fi
fi

if it "svc: docs/web-services.md lists every service port and no real host"; then
    ok=1
    for p in 20128 4096 3000 4000 8777 8080 8090 9000 9001 9121 24282 8199; do
        grep -q "| $p |" docs/web-services.md || { ok=0; echo "port $p missing" >&2; }
    done
    if grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' docs/web-services.md | grep -qvE '^(0\.0\.0\.0|127\.0\.0\.1)$'; then
        ok=0; echo "a real IP in the doc" >&2
    fi
    grep -qE '[a-z0-9-]\.(com|net|org|de)\b' docs/web-services.md && { ok=0; echo "a real domain in the doc" >&2; }
    if (( ok )); then pass; else fail "web-services.md incomplete or leaking"; fi
fi

# ─── Docker AI stack (server profile, lane S) ──────────────────────────────
# Every test name carries "aistack" so `--filter aistack` reaches all of them.
# Nothing here starts a container: docker and systemctl are stubs that log.
AISTACK="$ROOT/configuration/docker/ai-stack"

_aistack_sandbox() {
    local d t
    d="$(mktemp -d)"
    mkdir -p "$d/bin" "$d/home/.config/opencode" "$d/repo" "$d/code"
    # Stateful stand-ins (tests/helpers/aistack_fake.sh): docker, systemctl,
    # ss, curl, sleep, the omniroute CLI, register-autostart.sh and
    # start-stack.sh answer from files in $d and log their arguments. Nothing
    # reaches the daemon, the user manager or the network.
    for t in docker systemctl ss curl sleep omniroute register start-stack; do
        printf '#!/bin/sh\nexec bash "%s/tests/helpers/aistack_fake.sh" "%s" %s "$@"\n' "$ROOT" "$d" "$t" >"$d/bin/$t"
        chmod +x "$d/bin/$t"
    done
    mv "$d/bin/systemctl" "$d/bin/fake-systemctl"
    mv "$d/bin/register" "$d/bin/fake-register"
    mv "$d/bin/start-stack" "$d/bin/fake-start-stack"
    printf 'omniroute: sk-test-client-key\n' >"$d/repo/api-keys.yml"
    printf '%s' "$d"
}
# _aistack <sandbox> [NAME=value...] <ai-stack.sh args...>
_aistack() {
    local d="$1" extra=()
    shift
    while (( $# )) && [[ "$1" == [A-Z]*=* ]]; do extra+=("$1"); shift; done
    env -u AUTOOS_OMNIROUTE_KEY -u OMNIGRAPH_TOKEN -u AUTOOS_OPENHANDS_SANDBOX_URL -u AUTOOS_OPENHANDS_WEB_HOST \
        -u AUTOOS_AI_STACK_MIGRATING -u OMNIROUTE_API_KEY -u AUTOOS_STACK_BIND -u AUTOOS_STACK_ALLOW_LAN \
        -u AUTOOS_CURL -u AUTOOS_VERIFY_PUBLIC_URLS -u AUTOOS_VERIFY_COMBOS -u COMPOSE_PROFILES \
        HOME="$d/home" PATH="$d/bin:$PATH" AUTOOS_DOCKER="$d/bin/docker" AUTOOS_SYSTEMCTL="$d/bin/fake-systemctl" \
        AUTOOS_AI_STACK_CONFIG="$d/cfg" AUTOOS_AI_STACK_DATA="$d/data" AUTOOS_CODE_DIR="$d/code" \
        AUTOOS_KEYS_FILE="$d/repo/api-keys.yml" AUTOOS_LITELLM_DIR="$d/repo" AUTOOS_OMNIROUTE_HOME="$d/home/.omniroute" \
        AUTOOS_OPENHANDS_DIR="$d/home/.openhands" XDG_CONFIG_HOME="$d/home/.config" \
        AUTOOS_REGISTER_AUTOSTART="$d/bin/fake-register" AUTOOS_START_STACK="$d/bin/fake-start-stack" \
        "${extra[@]}" bash "${_AISTACK_SH:-$AISTACK/ai-stack.sh}" "$@" 2>&1
}
# _aistack_native <sandbox>: a host before the move - both units registered
# and active, a gateway state dir, the firewall unit up, images present.
_aistack_native() {
    local d="$1" u
    for u in autoos-omniroute autoos-opencode coding-agents-fw; do : >"$d/unit-$u"; : >"$d/active-$u"; done
    mkdir -p "$d/home/.omniroute"
    printf 'STORAGE_ENCRYPTION_KEY=test\n' >"$d/home/.omniroute/.env"
    printf 'native-db\n' >"$d/home/.omniroute/storage.sqlite"
    : >"$d/image-exists"
}
# _aistack_migrated <sandbox>: a host after a completed migrate.
_aistack_migrated() {
    local d="$1" c
    mkdir -p "$d/cfg" "$d/data/omniroute"
    printf "AUTOOS_STACK_BIND='0.0.0.0'\n" >"$d/cfg/stack.env"
    printf 'owner=docker by=migrate\n' >"$d/cfg/stack.active"
    for c in autoos-omniroute autoos-opencode openhands-app; do : >"$d/run-$c"; : >"$d/compose-$c"; done
    : >"$d/unit-coding-agents-fw"; : >"$d/active-coding-agents-fw"
    printf 'container-db\n' >"$d/data/omniroute/storage.sqlite"
}
# _aistack_seq <log> <needle...>: every needle appears, in this order.
_aistack_seq() {
    python3 - "$@" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8").read() if sys.argv[1] != "-" else sys.stdin.read()
at = 0
for needle in sys.argv[2:]:
    i = text.find(needle, at)
    if i < 0:
        print("missing (in order): %r after offset %d" % (needle, at), file=sys.stderr)
        sys.exit(1)
    at = i + len(needle)
PY
}

if it "aistack: compose template keeps the hardening contract"; then
    out="$(python3 "$ROOT/tests/helpers/check_compose.py" "$AISTACK/compose.yml" 2>&1)" && rc=0 || rc=$?
    if (( rc == 0 )); then pass; else fail "$out"; fi
fi

if it "aistack: the opencode layer builds on a digest-pinned V2 image, never V1"; then
    ok=1
    f="$AISTACK/opencode.Dockerfile"
    grep -qE '^FROM ghcr\.io/anomalyco/opencode:2\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$' "$f" || { ok=0; echo "FROM not pinned V2" >&2; }
    grep -q 'opencode-ai' "$f" && { ok=0; echo "V1 package referenced" >&2; }
    grep -qE '^RUN apk add --no-cache .*\bgit\b.*\bbash\b.*\bnodejs\b' "$f" || { ok=0; echo "agent tools missing" >&2; }
    if (( ok )); then pass; else fail "opencode layer is not pinned V2"; fi
fi

if it "aistack: the env example carries no real value"; then
    ok=1
    [[ -f "$AISTACK/stack.env.example" ]] || { ok=0; echo "missing" >&2; }
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        v="${line#*=}"
        [[ -z "$v" || "$v" == REPLACE_WITH_* || "$v" =~ ^[0-9]+[mg]?$ || "$v" == 0.0.0.0 || "$v" == /* ]] \
            || { ok=0; echo "suspicious: $line" >&2; }
        [[ "$v" == /home/* ]] && { ok=0; echo "home path: $line" >&2; }
    done <"$AISTACK/stack.env.example"
    if (( ok )); then pass; else fail "stack.env.example must stay generic"; fi
fi

if it "aistack: --dry-run init prints the plan and writes nothing"; then
    d="$(_aistack_sandbox)"
    out="$(_aistack "$d" --dry-run init)"
    ok=1
    [[ -e "$d/cfg" || -e "$d/data" ]] && { ok=0; echo "created a directory" >&2; }
    [[ "$out" == *"dry run"* ]] || { ok=0; echo "no dry-run banner" >&2; }
    [[ "$out" == *"would write $d/cfg/stack.env"* ]] || { ok=0; echo "stack.env not announced: $out" >&2; }
    [[ "$out" == *"would write $d/cfg/opencode.env"* ]] || { ok=0; echo "opencode.env not announced" >&2; }
    [[ "$out" == *"sk-test-client-key"* ]] && { ok=0; echo "printed a key" >&2; }
    grep -qE 'compose|run|pull|build' "$d/docker.log" 2>/dev/null && { ok=0; echo "called docker" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "dry run had side effects"; fi
fi

if it "aistack: init twice: 0600 env files, the second run skips, user values survive"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"
    printf '# mine\nOPENCODE_PASSWORD=operator-chosen\nMY_EXTRA=keep\n' >"$d/cfg/opencode.env"
    first="$(_aistack "$d" init)"
    second="$(_aistack "$d" init)"
    ok=1
    for f in stack.env opencode.env openhands.env; do
        [[ "$(stat -c %a "$d/cfg/$f")" == 600 ]] || { ok=0; echo "$f not 600" >&2; }
    done
    grep -q '^OPENCODE_PASSWORD=operator-chosen$' "$d/cfg/opencode.env" || { ok=0; echo "password overwritten" >&2; }
    grep -q '^MY_EXTRA=keep$' "$d/cfg/opencode.env" || { ok=0; echo "extra line lost" >&2; }
    grep -q '^# mine$' "$d/cfg/opencode.env" || { ok=0; echo "comment lost" >&2; }
    grep -q "^AUTOOS_OMNIROUTE_KEY='sk-test-client-key'$" "$d/cfg/opencode.env" || { ok=0; echo "client key not added" >&2; }
    grep -q "^LLM_API_KEY='sk-test-client-key'$" "$d/cfg/openhands.env" || { ok=0; echo "openhands key missing" >&2; }
    compgen -G "$d/cfg/opencode.env.autoos-backup-*" >/dev/null || { ok=0; echo "no backup of the user's file" >&2; }
    grep -q "^AUTOOS_CODE_DIR='$d/code'$" "$d/cfg/stack.env" || { ok=0; echo "code dir not recorded" >&2; }
    [[ -d "$d/data/omniroute" && -d "$d/data/opencode-home" ]] || { ok=0; echo "data dirs missing" >&2; }
    [[ "$second" == *"(skipped)"* ]] || { ok=0; echo "second: $second" >&2; }
    [[ "$second" == *"+ "* ]] && { ok=0; echo "second run changed something: $second" >&2; }
    [[ "$first$second" == *"sk-test-client-key"* ]] && { ok=0; echo "printed a key" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "init is not idempotent read-modify-write"; fi
fi

if it "aistack: init reuses the pinned opencode-serve password so phone logins survive"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/home/.config/autoos"
    printf 'phone-pass\n' >"$d/home/.config/autoos/opencode-serve.password"
    _aistack "$d" init >/dev/null
    if grep -q "^OPENCODE_PASSWORD='phone-pass'$" "$d/cfg/opencode.env"; then pass; else fail "password not carried over"; fi
    rm -rf "$d"
fi

if it "aistack: the container opencode config reaches the gateway by name"; then
    d="$(mktemp -d)"
    mkdir -p "$d/code/repo"
    cat >"$d/src.json" <<JSON
{"model":"omniroute/t1-orchestrator",
 "provider":{"omniroute":{"options":{"baseURL":"http://127.0.0.1:20128/v1","apiKey":"{env:AUTOOS_OMNIROUTE_KEY}"}},
             "litellm":{"options":{"baseURL":"http://127.0.0.1:4000/v1"}},
             "ollama":{"options":{"baseURL":"http://127.0.0.1:11434/v1"}},
             "meta":{"options":{"baseURL":"https://api.example.invalid/v1","apiKey":"{env:META_API_KEY}"}}},
 "providers":{"omniroute":{"settings":{"baseURL":"http://localhost:20128/v1"}},"litellm":{"settings":{"baseURL":"http://127.0.0.1:4000/v1"}}},
 "mcp":{"serena":{"type":"local","command":["uvx","serena"],"enabled":true},
        "omnigraph":{"type":"local","command":["npx","-y","x"],"enabled":true,"environment":{"OMNIGRAPH_BASE_URL":"http://localhost:8080"}},
        "playwright":{"type":"local","command":["npx","-y","@playwright/mcp"],"enabled":true},
        "graphify":{"type":"local","command":["uvx","graphify"],"enabled":true}},
 "instructions":["$d/code/repo/AGENTS.md","/elsewhere/SKILL.md"]}
JSON
    out1="$(python3 "$ROOT/tools/render-opencode-container-config.py" --source "$d/src.json" --out "$d/out.json" --code-dir "$d/code" 2>&1)"
    out2="$(python3 "$ROOT/tools/render-opencode-container-config.py" --source "$d/src.json" --out "$d/out.json" --code-dir "$d/code" 2>&1)"
    got="$(python3 - "$d/out.json" "$d/code" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
p, v2, m = c["provider"], c["providers"], c["mcp"]
print(p["omniroute"]["options"]["baseURL"], v2["omniroute"]["settings"]["baseURL"],
      sorted(p), sorted(v2), m["serena"].get("type"), m["serena"].get("url"),
      m["omnigraph"]["environment"]["OMNIGRAPH_BASE_URL"], m["playwright"]["enabled"],
      m["graphify"]["enabled"], [i.replace(sys.argv[2], "CODE") for i in c["instructions"]],
      p["omniroute"]["options"]["apiKey"])
PY
)"
    assert_eq "$got|$([[ "$out1" == *"litellm"* && "$out2" == *"unchanged"* ]] && echo reported)" \
        "http://omniroute:20128/v1 http://omniroute:20128/v1 ['meta', 'omniroute'] ['omniroute'] remote http://serena-mcp:9121/sse http://host.docker.internal:8080 False True ['CODE/repo/AGENTS.md'] {env:AUTOOS_OMNIROUTE_KEY}|reported"
    rm -rf "$d"
fi

if it "aistack: server profile runs the docker stack, workstation keeps the native installs"; then
    got="$(python3 - <<'PY'
import json
comps = {c["id"]: c for g in json.load(open("catalog/linux.json", encoding="utf-8"))["categories"] for c in g["components"]}
s = comps.get("ai-stack-docker", {})
out = [
    "server" in s.get("profiles", []),
    "workstation" not in s.get("profiles", []),
    "docker" in s.get("requires", []),
    s.get("provider") == "custom" and s.get("postInstall") == "install_ai_stack",
    # The host CLI stays: apply.sh manages the container through it. The
    # docker run OpenHands (a :latest pull) is replaced by the compose one.
    "server" in comps["omniroute"]["profiles"],
    "server" not in comps["openhands-docker"]["profiles"],
    "workstation" in comps["omniroute"]["profiles"],
    "workstation" in comps["openhands-docker"]["profiles"],
]
print(" ".join(str(x) for x in out))
PY
)"
    assert_eq "$got" "True True True True True True True True"
fi

if it "aistack: installer announces the plan in dry run and detects the running stack"; then
    d="$(_aistack_sandbox)"
    ok=1
    out="$( ( AUTOOS_DRY_RUN=1; AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; AUTOOS_AI_STACK_DATA="$d/data"
              HOME="$d/home"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG AUTOOS_AI_STACK_DATA HOME; install_ai_stack ) 2>&1)"
    [[ "$out" == *"would write"*"stack.env"* ]] || { ok=0; echo "plan not shown: $out" >&2; }
    [[ -e "$d/cfg" ]] && { ok=0; echo "dry run wrote config" >&2; }
    ( AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG
      custom_is_installed ai-stack-docker ) && { ok=0; echo "detected without a container" >&2; }
    # A leftover gateway container (a failed migrate) is not an installed stack.
    mkdir -p "$d/cfg"; : >"$d/cfg/stack.env"; : >"$d/container-exists"; : >"$d/compose-autoos-omniroute"
    ( AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG
      custom_is_installed ai-stack-docker ) && { ok=0; echo "a stopped leftover container read as installed" >&2; }
    printf 'owner=docker by=migrate\n' >"$d/cfg/stack.active"
    ( AUTOOS_DOCKER="$d/bin/docker"; AUTOOS_AI_STACK_CONFIG="$d/cfg"; export AUTOOS_DOCKER AUTOOS_AI_STACK_CONFIG
      custom_is_installed ai-stack-docker ) || { ok=0; echo "running stack not detected" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "installer dry run / detection is wrong"; fi
fi

if it "aistack: migrate without --yes is the announced plan and touches nothing"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/home/.omniroute"; printf 'STORAGE_ENCRYPTION_KEY=x\n' >"$d/home/.omniroute/.env"
    out="$(_aistack "$d" migrate)"
    ok=1
    # Order is the contract: refuse early, stop (quiescent DB), back up, copy,
    # start, prove - every service - then claim (marker) and only then
    # disable the native units.
    python3 - "$out" <<'PY' || ok=0
import sys
out = sys.argv[1]
steps = ["refuses", "stop the autoos-omniroute unit", "back up", "copy", "start the omniroute container",
         "answers", "autoos-opencode", "openhands", "stack.active", "unregister autoos-omniroute"]
pos = [out.find(s) for s in steps]
if -1 in pos or pos != sorted(pos):
    print("plan order wrong:", list(zip(steps, pos)), file=sys.stderr)
    sys.exit(1)
PY
    [[ "$out" == *"--yes"* ]] || { ok=0; echo "no hint how to run it" >&2; }
    [[ -e "$d/data" || -e "$d/cfg" ]] && { ok=0; echo "created state" >&2; }
    [[ -e "$d/systemctl.log" ]] && { ok=0; echo "called systemctl" >&2; }
    grep -qE 'compose|rm|stop' "$d/docker.log" 2>/dev/null && { ok=0; echo "called docker" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate plan is wrong or had side effects"; fi
fi

if it "aistack: rollback without --yes is the announced plan and touches nothing"; then
    d="$(_aistack_sandbox)"
    out="$(_aistack "$d" rollback)"
    ok=1
    [[ "$out" == *"register-autostart.sh"* && "$out" == *"--yes"* ]] || { ok=0; echo "plan: $out" >&2; }
    [[ -e "$d/systemctl.log" ]] && { ok=0; echo "called systemctl" >&2; }
    grep -qE 'compose' "$d/docker.log" 2>/dev/null && { ok=0; echo "called docker compose" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "rollback plan is wrong or had side effects"; fi
fi

if it "aistack: register-autostart leaves the gateway and opencode units alone once docker owns them"; then
    d="$(_svc_reg_sandbox)"
    s="$(_aistack_sandbox)"
    _aistack_migrated "$s"
    out="$(AUTOOS_DOCKER="$s/bin/docker" AUTOOS_AI_STACK_CONFIG="$s/cfg" _svc_reg "$d")"
    ok=1
    [[ -e "$d/units/autoos-omniroute.service" || -e "$d/units/autoos-opencode.service" ]] && { ok=0; echo "wrote a native unit" >&2; }
    [[ -e "$d/units/autoos-litellm.service" ]] || { ok=0; echo "litellm unit should still be written" >&2; }
    [[ "$out" == *"docker AI stack"* ]] || { ok=0; echo "not explained: $out" >&2; }
    rm -rf "$d" "$s"
    if (( ok )); then pass; else fail "register-autostart would start a second gateway"; fi
fi

if it "aistack: the resume script brings up the compose stack instead of the native units"; then
    ok=1
    f="$ROOT/configuration/autostart/Start-AutoOSStack.sh"
    grep -q 'configuration/docker/ai-stack/ai-stack.sh' "$f" || { ok=0; echo "stack script not referenced" >&2; }
    grep -q '"$AI_STACK" is-active' "$f" || { ok=0; echo "no is-active branch" >&2; }
    grep -q '"$AI_STACK" up' "$f" || { ok=0; echo "no compose resume" >&2; }
    if (( ok )); then pass; else fail "Start-AutoOSStack.sh ignores the docker stack"; fi
fi

if it "aistack: apply.sh manages a containerised gateway with the manage key, never a host restart"; then
    ok=1
    f="$ROOT/configuration/omniroute/apply.sh"
    grep -q 'manage.key' "$f" || { ok=0; echo "manage key not used" >&2; }
    grep -q '"$AI_STACK" is-active' "$f" || { ok=0; echo "no stack detection" >&2; }
    grep -q '"$AI_STACK" up omniroute' "$f" || { ok=0; echo "a down container gateway is not resumed" >&2; }
    if (( ok )); then pass; else fail "apply.sh cannot manage the container"; fi
fi

# ─── Review findings 2026-09-25 (gemini-3.8-flash + deepseek-v4.1-flash) ────
# migrate/rollback/up run for real here, against the stateful fakes above.

if it "aistack: is-active needs the completed-migration marker, not a leftover container"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    : >"$d/compose-autoos-omniroute"; : >"$d/container-exists"
    ok=1
    _aistack "$d" is-active >/dev/null && { ok=0; echo "a stopped leftover container reads as active" >&2; }
    : >"$d/run-autoos-omniroute"
    _aistack "$d" is-active >/dev/null && { ok=0; echo "a running container without the marker reads as active" >&2; }
    printf 'owner=docker by=migrate\n' >"$d/cfg/stack.active"
    _aistack "$d" is-active >/dev/null || { ok=0; echo "the marker does not make the stack active" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "is-active trusts a container instead of the marker"; fi
fi

if it "aistack: a failed gateway move removes the container and hands :20128 back"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/unhealthy-omniroute"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    grep -q 'rm -s -f omniroute' "$d/docker.log" || { ok=0; echo "failed container not removed: $(cat "$d/docker.log")" >&2; }
    [[ -e "$d/compose-autoos-omniroute" || -e "$d/run-autoos-omniroute" ]] && { ok=0; echo "container left behind" >&2; }
    [[ -e "$d/active-autoos-omniroute" && -e "$d/unit-autoos-omniroute" ]] || { ok=0; echo "native gateway not back" >&2; }
    [[ -e "$d/active-autoos-opencode" ]] || { ok=0; echo "opencode was touched" >&2; }
    grep -q 'up -d --no-deps opencode' "$d/docker.log" && { ok=0; echo "went on to opencode" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker written" >&2; }
    _aistack "$d" is-active >/dev/null && { ok=0; echo "reads as active afterwards" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a failed migrate leaves a container that reads as active"; fi
fi

if it "aistack: rollback replaces ~/.omniroute with the container state, no stale WAL survives"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    mkdir -p "$d/home/.omniroute"
    printf 'old-db\n' >"$d/home/.omniroute/storage.sqlite"
    printf 'stale-wal\n' >"$d/home/.omniroute/storage.sqlite-wal"
    printf 'stale-shm\n' >"$d/home/.omniroute/storage.sqlite-shm"
    out="$(_aistack "$d" rollback --yes)" && rc=0 || rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "rollback failed: $out" >&2; }
    [[ "$(cat "$d/home/.omniroute/storage.sqlite" 2>/dev/null)" == container-db ]] || { ok=0; echo "DB not restored" >&2; }
    [[ -e "$d/home/.omniroute/storage.sqlite-wal" || -e "$d/home/.omniroute/storage.sqlite-shm" ]] \
        && { ok=0; echo "stale WAL/SHM kept next to the restored DB" >&2; }
    backup="$(compgen -G "$d/data/backups/omniroute-native-*.tar.gz" | head -n1)"
    [[ -n "$backup" ]] && tar -tzf "$backup" | grep -q 'storage.sqlite-wal' || { ok=0; echo "no full backup" >&2; }
    aside="$(compgen -G "$d/home/.omniroute.autoos-backup-*" | head -n1)"
    [[ -n "$aside" && -e "$aside/storage.sqlite-wal" ]] || { ok=0; echo "previous dir not kept aside" >&2; }
    compgen -G "$d/home/.omniroute.autoos-staging-*" >/dev/null && { ok=0; echo "staging dir left" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker kept after rollback" >&2; }
    grep -q -- '--only autoos-omniroute,autoos-opencode' "$d/register.log" || { ok=0; echo "units not re-registered" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "rollback overlays the old gateway dir"; fi
fi

if it "aistack: migrate stops nothing when the manage key cannot be created"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/omni-key-fails"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate went on without a manage key" >&2; }
    [[ "$out" == *"manage.key"* ]] || { ok=0; echo "no hint: $out" >&2; }
    grep -q 'stop' "$d/systemctl.log" 2>/dev/null && { ok=0; echo "stopped a unit" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "started a container" >&2; }
    [[ -e "$d/active-autoos-omniroute" && -e "$d/active-autoos-opencode" ]] || { ok=0; echo "native service down" >&2; }
    [[ -e "$d/cfg/manage.key" ]] && { ok=0; echo "empty key file written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate without a manage key"; fi
fi

if it "aistack: migrate refuses a second run once migrated and points at rollback"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    mkdir -p "$d/home/.omniroute"; printf 'stale-host-db\n' >"$d/home/.omniroute/storage.sqlite"
    printf 'sk-manage\n' >"$d/cfg/manage.key"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    plan="$(_aistack "$d" migrate)"
    ok=1
    (( rc != 0 )) || { ok=0; echo "second migrate ran" >&2; }
    [[ "$out" == *"rollback --yes"* ]] || { ok=0; echo "no rollback hint: $out" >&2; }
    [[ "$plan" == *"already migrated"* ]] || { ok=0; echo "plan does not say so" >&2; }
    [[ "$(cat "$d/data/omniroute/storage.sqlite")" == container-db ]] || { ok=0; echo "container state overwritten" >&2; }
    grep -qE 'compose .*(up|stop|rm)' "$d/docker.log" 2>/dev/null && { ok=0; echo "touched containers" >&2; }
    [[ -e "$d/systemctl.log" ]] && grep -qE 'stop|start' "$d/systemctl.log" && { ok=0; echo "touched units" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate would overwrite newer container state"; fi
fi

if it "aistack: migrate disables the native units only after all three services answered"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/exists-openhands-app"; : >"$d/run-openhands-app"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "migrate failed: $out" >&2; }
    _aistack_seq "$d/events.log" "systemctl: --user stop autoos-omniroute.service" "docker: compose" "up -d --no-deps omniroute" \
        "systemctl: --user stop autoos-opencode.service" "up -d --no-deps opencode" "docker: rm -f openhands-app" \
        "start-stack: openhands" "register: --unregister --only autoos-omniroute,autoos-opencode" || ok=0
    [[ "$(cat "$d/start-stack.log")" == *"migrating=1"* ]] || { ok=0; echo "openhands not started as the compose service" >&2; }
    [[ -e "$d/cfg/stack.active" ]] || { ok=0; echo "no marker after a completed migrate" >&2; }
    [[ "$(cat "$d/data/omniroute/storage.sqlite")" == native-db ]] || { ok=0; echo "state not copied" >&2; }
    [[ "$(stat -c %a "$(compgen -G "$d/data/backups/omniroute-*.tar.gz" | head -n1)")" == 600 ]] || { ok=0; echo "backup not 0600" >&2; }
    _aistack "$d" is-active >/dev/null || { ok=0; echo "not active after migrate" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "migrate order is wrong"; fi
fi

if it "aistack: an opencode failure during migrate hands the gateway back too"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/fail-up-opencode"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    grep -q 'rm -s -f omniroute' "$d/docker.log" || { ok=0; echo "gateway container kept" >&2; }
    grep -q 'rm -s -f opencode' "$d/docker.log" || { ok=0; echo "opencode container kept" >&2; }
    for u in autoos-omniroute autoos-opencode; do
        [[ -e "$d/active-$u" && -e "$d/unit-$u" ]] || { ok=0; echo "$u not handed back" >&2; }
    done
    grep -q -- '--unregister' "$d/register.log" 2>/dev/null && { ok=0; echo "a unit was unregistered" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a late failure leaves the gateway in docker"; fi
fi

if it "aistack: an openhands failure during migrate rolls the whole stack back to native"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/exists-openhands-app"; : >"$d/run-openhands-app"
    : >"$d/fail-up-openhands"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    for s in omniroute opencode openhands; do
        grep -q "rm -s -f $s" "$d/docker.log" || { ok=0; echo "$s container kept" >&2; }
    done
    for u in autoos-omniroute autoos-opencode; do
        [[ -e "$d/active-$u" && -e "$d/unit-$u" ]] || { ok=0; echo "$u not handed back" >&2; }
    done
    # The docker run OpenHands it replaced is recreated (start-stack.sh, native mode).
    _aistack_seq "$d/start-stack.log" "migrating=1" "migrating=0" || ok=0
    [[ -e "$d/exists-openhands-app" ]] || { ok=0; echo "docker run openhands not recreated" >&2; }
    grep -q -- '--unregister' "$d/register.log" 2>/dev/null && { ok=0; echo "a unit was unregistered" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an openhands failure leaves a half-migrated host"; fi
fi

if it "aistack: up and migrate refuse a LAN bind unless the firewall unit runs or LAN is allowed"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    _aistack_reset() { rm -f "$d"/run-* "$d"/compose-* "$d/docker.log" "$d/cfg/stack.active"; }
    ok=1
    printf "AUTOOS_STACK_BIND='0.0.0.0'\n" >"$d/cfg/stack.env"
    out="$(_aistack "$d" up)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "LAN bind without firewall started" >&2; }
    [[ "$out" == *"coding-agents-fw.service"* && "$out" == *"AUTOOS_STACK_ALLOW_LAN=1"* ]] || { ok=0; echo "refusal unexplained: $out" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "started containers" >&2; }
    grep -q 'is-active coding-agents-fw.service' "$d/systemctl.log" || { ok=0; echo "firewall unit not checked" >&2; }
    _aistack_reset
    : >"$d/active-coding-agents-fw"
    _aistack "$d" up >/dev/null || { ok=0; echo "refused although the firewall unit is active" >&2; }
    grep -q 'up -d --no-deps omniroute opencode openhands' "$d/docker.log" || { ok=0; echo "not started with the firewall" >&2; }
    _aistack_reset; rm -f "$d/active-coding-agents-fw"
    printf 'AUTOOS_STACK_ALLOW_LAN=1\n' >>"$d/cfg/stack.env"
    _aistack "$d" up >/dev/null || { ok=0; echo "explicit AUTOOS_STACK_ALLOW_LAN=1 refused" >&2; }
    _aistack_reset
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    _aistack "$d" up >/dev/null || { ok=0; echo "loopback bind refused" >&2; }
    rm -rf "$d"
    # migrate: refused before anything stops.
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    rm -f "$d/active-coding-agents-fw"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "migrate ran with an open LAN bind" >&2; }
    grep -q 'stop' "$d/systemctl.log" 2>/dev/null && { ok=0; echo "migrate stopped a unit first" >&2; }
    rm -rf "$d"
    unset -f _aistack_reset
    if (( ok )); then pass; else fail "the LAN bind is not guarded"; fi
fi

if it "aistack: the opencode image is rebuilt when its Dockerfile or base image changes"; then
    d="$(_aistack_sandbox)"
    t="$d/tree/configuration"
    mkdir -p "$t/docker/ai-stack"
    cp "$AISTACK/ai-stack.sh" "$AISTACK/compose.yml" "$AISTACK/opencode.Dockerfile" "$t/docker/ai-stack/"
    cp "$ROOT/configuration/env-file.sh" "$t/"
    mkdir -p "$d/cfg"; printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    : >"$d/image-exists"; printf 'stale\n' >"$d/opencode-label"
    ok=1
    _AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack "$d" up opencode >/dev/null
    first="$(cat "$d/opencode-label")"
    grep -qE "^build --label org\.autoos\.opencode\.source=[0-9a-f]{64} -t autoos/opencode:" "$d/docker.log" \
        || { ok=0; echo "stale image not rebuilt: $(cat "$d/docker.log")" >&2; }
    rm -f "$d/docker.log" "$d/run-autoos-opencode"
    _AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack "$d" up opencode >/dev/null
    grep -q '^build' "$d/docker.log" && { ok=0; echo "rebuilt an up-to-date image" >&2; }
    rm -f "$d/docker.log" "$d/run-autoos-opencode"
    printf '# a changed layer\n' >>"$t/docker/ai-stack/opencode.Dockerfile"
    _AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack "$d" up opencode >/dev/null
    grep -q '^build' "$d/docker.log" || { ok=0; echo "a changed Dockerfile was not rebuilt" >&2; }
    [[ "$(cat "$d/opencode-label")" != "$first" ]] || { ok=0; echo "label did not change" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the opencode image never rebuilds"; fi
fi

if it "aistack: init never writes a value with a line break into an env file"; then
    d="$(_aistack_sandbox)"
    out="$(_aistack "$d" AUTOOS_OMNIROUTE_KEY=$'sk-leak\nINJECTED=1' OMNIGRAPH_TOKEN=$'tok\rEVIL=1' init)"
    ok=1
    grep -qsE '^(INJECTED|EVIL)=' "$d/cfg/opencode.env" "$d/cfg/openhands.env" && { ok=0; echo "a line break injected a key" >&2; }
    grep -qE '^(AUTOOS_OMNIROUTE_KEY|OMNIGRAPH_TOKEN)=' "$d/cfg/opencode.env" && { ok=0; echo "unsafe value written" >&2; }
    grep -q '^LLM_API_KEY=' "$d/cfg/openhands.env" 2>/dev/null && { ok=0; echo "unsafe value written for openhands" >&2; }
    grep -q $'\r' "$d/cfg/opencode.env" && { ok=0; echo "carriage return written" >&2; }
    [[ "$out" == *"AUTOOS_OMNIROUTE_KEY"*"line break"* && "$out" == *"OMNIGRAPH_TOKEN"* ]] || { ok=0; echo "not warned: $out" >&2; }
    [[ "$out" == *"sk-leak"* || "$out" == *"tok"$'\r'* ]] && { ok=0; echo "printed the value" >&2; }
    grep -q '^OPENCODE_PASSWORD=' "$d/cfg/opencode.env" || { ok=0; echo "the safe keys were not written" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "an env value with a line break reaches the file"; fi
fi

if it "aistack: apply.sh hands the manage key to the omniroute CLI only"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    printf 'sk-manage-secret\n' >"$d/cfg/manage.key"
    out="$(env -u OMNIROUTE_API_KEY -u AUTOOS_OMNIROUTE_URL HOME="$d/home" PATH="$d/bin:$PATH" \
        AUTOOS_AI_STACK_CONFIG="$d/cfg" AUTOOS_KEYS_FILE="$d/repo/api-keys.yml" \
        bash "$ROOT/configuration/omniroute/apply.sh" --dry-run 2>&1)"
    ok=1
    [[ "$out" == *"manage-scoped key"* ]] || { ok=0; echo "docker mode not detected: $out" >&2; }
    grep -qx omniroute "$d/saw-manage-key" 2>/dev/null || { ok=0; echo "the CLI did not get the key" >&2; }
    grep -qx curl "$d/saw-manage-key" 2>/dev/null && { ok=0; echo "curl inherited the manage key" >&2; }
    [[ "$out" == *"sk-manage-secret"* ]] && { ok=0; echo "printed the key" >&2; }
    grep -qE '^[[:space:]]*export OMNIROUTE_API_KEY' "$ROOT/configuration/omniroute/apply.sh" && { ok=0; echo "exported script-wide" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the manage key leaks into every child process"; fi
fi

if it "aistack: init keeps the data dir and the opencode home private (0700)"; then
    d="$(_aistack_sandbox)"
    mkdir -p "$d/data/opencode-home"; chmod 755 "$d/data" "$d/data/opencode-home"
    _aistack "$d" init >/dev/null
    got="$(stat -c %a "$d/data" "$d/data/opencode-home" "$d/data/omniroute" | tr '\n' ' ')"
    rm -rf "$d"
    assert_eq "$got" "700 700 700 "
fi

# Re-review 2026-09-25 (cross-company): marker order, recreate guard, the
# effective bind, the pinned FROM the rebuild hash relies on.
if it "aistack: migrate writes the marker before unregistering and drops it on a later failure"; then
    ok=1
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    _aistack "$d" migrate --yes >/dev/null || { ok=0; echo "migrate failed" >&2; }
    # The stand-in logs the call, then the marker state it saw during it.
    grep -A1 -- '--unregister --only autoos-omniroute,autoos-opencode' "$d/register.log" | grep -qx 'marker=yes' \
        || { ok=0; echo "unregistered before the marker existed (rollback would refuse): $(cat "$d/register.log")" >&2; }
    rm -rf "$d"
    # The unregister fails: marker removed again, everything back to native.
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    : >"$d/fail-register"
    _aistack "$d" migrate --yes >/dev/null && { ok=0; echo "migrate reported success" >&2; }
    [[ -e "$d/cfg/stack.active" ]] && { ok=0; echo "marker left after the abort" >&2; }
    for u in autoos-omniroute autoos-opencode; do
        [[ -e "$d/active-$u" && -e "$d/unit-$u" ]] || { ok=0; echo "$u not handed back" >&2; }
    done
    for s in omniroute opencode openhands; do
        grep -q "rm -s -f $s" "$d/docker.log" || { ok=0; echo "$s container kept" >&2; }
    done
    rm -rf "$d"
    # The marker cannot be written: nothing is unregistered, all back to native.
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    mkdir -p "$d/cfg/stack.active"
    _aistack "$d" migrate --yes >/dev/null && { ok=0; echo "migrate reported success" >&2; }
    grep -q -- '--unregister' "$d/register.log" 2>/dev/null && { ok=0; echo "unregistered without a marker" >&2; }
    [[ -e "$d/active-autoos-omniroute" && -e "$d/active-autoos-opencode" ]] || { ok=0; echo "not handed back" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a marker failure can orphan the host"; fi
fi

if it "aistack: up guards the bind before recreating containers that already run"; then
    d="$(_aistack_sandbox)"
    _aistack_migrated "$d"
    rm -f "$d/active-coding-agents-fw"
    : >"$d/image-exists"
    out="$(_aistack "$d" up)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "up ran compose up on a LAN bind without the firewall" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "compose up reached: $(grep 'up -d' "$d/docker.log")" >&2; }
    [[ "$out" == *"coding-agents-fw.service"* ]] || { ok=0; echo "refusal unexplained: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a running container can be recreated on an unguarded bind"; fi
fi

if it "aistack: the guard and compose agree on the bind when the shell exports AUTOOS_STACK_BIND"; then
    ok=1
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    # compose would publish on the exported 0.0.0.0, not the file's loopback.
    _aistack "$d" AUTOOS_STACK_BIND=0.0.0.0 up >/dev/null && { ok=0; echo "exported LAN bind not guarded" >&2; }
    grep -q 'up -d' "$d/docker.log" 2>/dev/null && { ok=0; echo "started containers" >&2; }
    rm -rf "$d"
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    printf "AUTOOS_STACK_BIND='0.0.0.0'\n" >"$d/cfg/stack.env"
    _aistack "$d" AUTOOS_STACK_BIND=127.0.0.1 up >/dev/null || { ok=0; echo "exported loopback refused" >&2; }
    [[ "$(sort -u "$d/compose-bind.log")" == "bind=127.0.0.1" ]] \
        || { ok=0; echo "compose saw another bind: $(sort -u "$d/compose-bind.log" | tr '\n' ' ')" >&2; }
    rm -rf "$d"
    d="$(_aistack_sandbox)"
    mkdir -p "$d/cfg"; : >"$d/image-exists"
    printf "AUTOOS_STACK_BIND='127.0.0.1'\n" >"$d/cfg/stack.env"
    _aistack "$d" up >/dev/null || { ok=0; echo "file loopback refused" >&2; }
    [[ "$(sort -u "$d/compose-bind.log")" == "bind=127.0.0.1" ]] || { ok=0; echo "compose not given the file's bind" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the guarded bind and the published bind can differ"; fi
fi

if it "aistack: every FROM in opencode.Dockerfile is digest-pinned, or the rebuild hash is blind"; then
    # The rebuild label hashes the Dockerfile text: a tag-only FROM could move
    # underneath an unchanged text and the stale layer would never rebuild.
    f="$AISTACK/opencode.Dockerfile"
    froms="$(grep -ciE '^[[:space:]]*FROM[[:space:]]' "$f")"
    pinned="$(grep -cE '^[[:space:]]*FROM[[:space:]]+[^[:space:]$]+@sha256:[0-9a-f]{64}([[:space:]]+[Aa][Ss][[:space:]]+[^[:space:]]+)?[[:space:]]*$' "$f")"
    if (( froms >= 1 && froms == pinned )); then pass; else fail "$pinned of $froms FROM lines are @sha256:-pinned in $f"; fi
fi

if it "aistack: a failed backup leaves no partial archive and hands the service back"; then
    d="$(_aistack_sandbox)"
    _aistack_native "$d"
    # tar that writes half an archive, then fails (disk full).
    cat >"$d/bin/tar" <<'STUB'
#!/bin/sh
prev=""
for a in "$@"; do
    case "$prev" in -czf|-f) printf 'partial' >"$a" ;; esac
    prev="$a"
done
exit 2
STUB
    chmod +x "$d/bin/tar"
    out="$(_aistack "$d" migrate --yes)" && rc=0 || rc=$?
    ok=1
    (( rc != 0 )) || { ok=0; echo "migrate reported success" >&2; }
    compgen -G "$d/data/backups/*.tar.gz" >/dev/null && { ok=0; echo "partial archive kept: $(ls "$d/data/backups")" >&2; }
    [[ -e "$d/active-autoos-omniroute" ]] || { ok=0; echo "gateway not handed back" >&2; }
    # rollback: the host dir is backed up before anything stops, so the same
    # failure leaves the running stack alone.
    d2="$(_aistack_sandbox)"
    _aistack_migrated "$d2"
    mkdir -p "$d2/home/.omniroute"; printf 'old-db\n' >"$d2/home/.omniroute/storage.sqlite"
    cp "$d/bin/tar" "$d2/bin/tar"
    out="$(_aistack "$d2" rollback --yes)" && rc=0 || rc=$?
    (( rc != 0 )) || { ok=0; echo "rollback reported success" >&2; }
    compgen -G "$d2/data/backups/*.tar.gz" >/dev/null && { ok=0; echo "partial rollback archive kept" >&2; }
    [[ "$(cat "$d2/home/.omniroute/storage.sqlite")" == old-db ]] || { ok=0; echo "host state changed without a backup" >&2; }
    grep -qE 'compose .*(down|stop|rm)' "$d2/docker.log" 2>/dev/null && { ok=0; echo "stopped the stack before the backup" >&2; }
    [[ -e "$d2/run-autoos-omniroute" && -e "$d2/cfg/stack.active" ]] || { ok=0; echo "stack or marker gone" >&2; }
    rm -rf "$d" "$d2"
    if (( ok )); then pass; else fail "a failed backup leaves a partial archive"; fi
fi

# ─── ai-stack.sh verify ─────────────────────────────────────────────────────
# The read-only end-to-end check. Docker is the usual stub; curl is a second
# stand-in wired through AUTOOS_CURL that answers from a table and logs the
# argv it received, so a test can prove what did (and did not) reach a curl
# command line.
_AISTACK_VERIFY_KEY="sk-verify-secret-0123456789abcdef"

# _aistack_verify_sandbox <sandbox>: a migrated host (three healthy
# containers, firewall unit up) plus what verify reads beyond the docker
# stub: the OpenHands container's SANDBOX_VOLUMES and the curl stand-in with
# an all-green route table.
_aistack_verify_sandbox() {
    local d="$1"
    _aistack_migrated "$d"
    printf 'SANDBOX_VOLUMES=%s:%s:rw\n' "$d/code" "$d/code" >"$d/env-openhands-app"
    printf '%s' "$_AISTACK_VERIFY_KEY" >"$d/curl-key"
    cat >"$d/bin/verify-curl" <<'STUB'
#!/usr/bin/env bash
# curl stand-in for `ai-stack.sh verify` (AUTOOS_CURL). Answers from
# <state>/curl-table, one "URL AUTH BODY CODE" line per route: AUTH is none |
# key | bad | any, BODY is - or a substring of the -d payload, the first match
# wins and no match means nothing answered. Logs its argv to curl-argv.log.
# The Authorization header is read from `-H @-` (stdin) or an inline -H and
# compared with <state>/curl-key; it is never logged, only the verdict is.
S="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
printf '%s\n' "$*" >>"$S/curl-argv.log"
args=("$@"); url=""; body=""; hdrs=""; want_code=0
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in
        -w) want_code=1 ;;
        -d) body="${args[i+1]:-}" ;;
        -H) h="${args[i+1]:-}"
            if [[ "$h" == "@-" ]]; then h="$(cat)"; fi
            hdrs+="$h"$'\n' ;;
        http://*|https://*) url="${args[i]}" ;;
    esac
done
auth=none
want="Authorization: Bearer $(cat "$S/curl-key" 2>/dev/null)"
while IFS= read -r h; do
    case "$h" in
        "$want") auth=key ;;
        Authorization:*) auth=bad ;;
    esac
done <<<"$hdrs"
code=000
while read -r t_url t_auth t_body t_code; do
    [[ "$t_url" == "$url" ]] || continue
    [[ "$t_auth" == any || "$t_auth" == "$auth" ]] || continue
    [[ "$t_body" == - || "$body" == *"$t_body"* ]] || continue
    code="$t_code"; break
done <"$S/curl-table"
printf 'url=%s auth=%s code=%s\n' "$url" "$auth" "$code" >>"$S/curl-seen.log"
if (( want_code )); then printf '%s' "$code"; fi
if [[ "$code" == 000 ]]; then exit 7; fi
exit 0
STUB
    chmod +x "$d/bin/verify-curl"
    printf '%s\n' \
        'http://127.0.0.1:20128/v1/models none - 401' \
        'http://127.0.0.1:20128/v1/models key - 200' \
        'http://127.0.0.1:20128/v1/chat/completions bad - 401' \
        'http://127.0.0.1:20128/v1/chat/completions key t2-worker-free-only 200' \
        'http://127.0.0.1:20128/v1/chat/completions key t3-driver-free-only 200' \
        'http://127.0.0.1:20128/v1/chat/completions key t2-worker-clean 200' \
        'http://127.0.0.1:4096/api/session none - 401' >"$d/curl-table"
}
# _aistack_verify_route <sandbox> <url> <auth> <body> <code>: one more route,
# ahead of the table (the first match wins).
_aistack_verify_route() {
    local d="$1"; shift
    { printf '%s %s %s %s\n' "$@"; cat "$d/curl-table"; } >"$d/curl-table.new" && mv "$d/curl-table.new" "$d/curl-table"
}
# _aistack_verify <sandbox> [NAME=value...] verify: the curl stand-in wired in.
_aistack_verify() {
    local d="$1"
    shift
    _aistack "$d" AUTOOS_CURL="$d/bin/verify-curl" "$@"
}
# _aistack_snapshot <sandbox>: a checksum of every file the run could have
# touched (the stubs' own logs and bin/ excluded).
_aistack_snapshot() {
    find "$1" \( -name bin -o -name '*.log' \) -prune -o -type f -print | sort | xargs -r cksum
}

if it "aistack: verify all green exits 0 and the summary says 0 failed"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    printf '%s\n' 'http://127.0.0.1:18081/ none - 302' 'http://127.0.0.1:18082/ none - 302' >>"$d/curl-table"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" \
        AUTOOS_VERIFY_PUBLIC_URLS="http://127.0.0.1:18081/ http://127.0.0.1:18082/" verify)" && rc=0 || rc=$?
    ok=1
    (( rc == 0 )) || { ok=0; echo "exit $rc, not 0" >&2; }
    grep -qx 'verify: 12 ok, 0 failed, 1 skipped' <<<"$out" || { ok=0; echo "summary: $(tail -n1 <<<"$out")" >&2; }
    grep -q '^  FAIL' <<<"$out" && { ok=0; echo "a FAIL line on a healthy stack" >&2; }
    for name in 'container autoos-omniroute' 'container autoos-opencode' 'container openhands-app' \
                'keyless /v1/models refused on :20128' 'keyless /api/session refused on :4096' \
                'combo t2-worker-free-only' 'combo t3-driver-free-only' 'combo t2-worker-clean' \
                'public URL http://127.0.0.1:18081/' 'public URL http://127.0.0.1:18082/'; do
        grep -qx "  ok    $name" <<<"$out" || { ok=0; echo "no ok line for: $name" >&2; }
    done
    grep -qE '^  ok    code dir .* visible in autoos-opencode$' <<<"$out" || { ok=0; echo "no ok for the opencode code dir" >&2; }
    grep -qE '^  ok    code dir .* in openhands-app SANDBOX_VOLUMES$' <<<"$out" || { ok=0; echo "no ok for the sandbox volumes" >&2; }
    grep -q '^  skip  healthcheck - ' <<<"$out" || { ok=0; echo "the healthcheck skip is not reported" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "verify is not green on a healthy stack"; fi
fi

if it "aistack: verify FAILs a keyless request that is answered 200"; then
    ok=1
    for probe in "20128 /v1/models" "4096 /api/session"; do
        d="$(_aistack_sandbox)"
        _aistack_verify_sandbox "$d"
        _aistack_verify_route "$d" "http://127.0.0.1:${probe%% *}${probe#* }" none - 200
        out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 1 )) || { ok=0; echo "$probe: exit $rc, not 1" >&2; }
        grep -qE "^  FAIL  keyless ${probe#* } refused on :${probe%% *} - HTTP 200" <<<"$out" || { ok=0; echo "$probe: no FAIL line" >&2; }
        [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "$probe: not exactly one FAIL" >&2; }
        grep -q '^verify: .* 1 failed' <<<"$out" || { ok=0; echo "$probe: summary does not say 1 failed" >&2; }
        rm -rf "$d"
    done
    if (( ok )); then pass; else fail "a keyless 200 is not a FAIL"; fi
fi

if it "aistack: verify FAILs a container that is not running or not healthy"; then
    ok=1
    for mode in stopped unhealthy missing; do
        d="$(_aistack_sandbox)"
        _aistack_verify_sandbox "$d"
        case "$mode" in
            stopped)   rm -f "$d/run-autoos-opencode"; want='container autoos-opencode - exited' ;;
            unhealthy) : >"$d/unhealthy-omniroute"; want='container autoos-omniroute - running \(unhealthy\)' ;;
            missing)   rm -f "$d/run-openhands-app" "$d/compose-openhands-app"; want='container openhands-app - no container' ;;
        esac
        out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 1 )) || { ok=0; echo "$mode: exit $rc, not 1" >&2; }
        # Check 1 runs first, so its FAIL leads. Later checks of a container that
        # is down may FAIL too (a stopped opencode cannot show its code dir).
        grep -E '^  FAIL' <<<"$out" | head -n1 | grep -qE "^  FAIL  $want\$" || { ok=0; echo "$mode: the first FAIL is not the container: $(grep '^  FAIL' <<<"$out" | head -n1)" >&2; }
        grep -qE '^verify: [0-9]+ ok, [1-9][0-9]* failed' <<<"$out" || { ok=0; echo "$mode: summary does not count a failure" >&2; }
        rm -rf "$d"
    done
    if (( ok )); then pass; else fail "a stopped, unhealthy or missing container is not a FAIL"; fi
fi

if it "aistack: verify skips the keyed combos without a key, and the key never reaches argv or the output"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    ok=1
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "no key: exit $rc, not 0" >&2; }
    grep -q '^  skip  keyed combos - AUTOOS_OMNIROUTE_KEY is not set' <<<"$out" || { ok=0; echo "no key: no skip line" >&2; }
    [[ "$(grep -c 'chat/completions' "$d/curl-argv.log")" == 0 ]] || { ok=0; echo "no key: a chat request was sent" >&2; }
    rm -f "$d/curl-argv.log" "$d/curl-seen.log"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "key set: exit $rc, not 0" >&2; }
    [[ "$(grep -c 'chat/completions' "$d/curl-argv.log")" == 3 ]] || { ok=0; echo "key set: not three chat requests" >&2; }
    # The stub compared the header it read from stdin: the key did arrive.
    [[ "$(grep -c 'chat/completions auth=key code=200' "$d/curl-seen.log")" == 3 ]] || { ok=0; echo "key set: the key did not reach curl" >&2; }
    [[ "$(grep -c -F -e "$_AISTACK_VERIFY_KEY" "$d/curl-argv.log" || true)" == 0 ]] || { ok=0; echo "the key is on curl's command line" >&2; }
    [[ "$(grep -c -F -e "$_AISTACK_VERIFY_KEY" "$d/docker.log" || true)" == 0 ]] || { ok=0; echo "the key reached docker" >&2; }
    [[ "$(grep -c -F -e "$_AISTACK_VERIFY_KEY" <<<"$out" || true)" == 0 ]] || { ok=0; echo "the key is in the output" >&2; }
    grep -q '^  ok    combo t2-worker-clean$' <<<"$out" || { ok=0; echo "key set: no ok for a combo" >&2; }
    # A key the gateway rejects: FAIL for every combo, and still no key printed.
    rm -f "$d/curl-argv.log" "$d/curl-seen.log"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY=sk-verify-wrong-key-987654321 verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "wrong key: exit $rc, not 1" >&2; }
    [[ "$(grep -c '^  FAIL  combo .* - HTTP 401' <<<"$out")" == 3 ]] || { ok=0; echo "wrong key: not three FAIL lines" >&2; }
    [[ "$(grep -c -F -e 'sk-verify-wrong-key-987654321' "$d/curl-argv.log" || true)" == 0 ]] || { ok=0; echo "the wrong key is on curl's command line" >&2; }
    [[ "$(grep -c -F -e 'sk-verify-wrong-key-987654321' <<<"$out" || true)" == 0 ]] || { ok=0; echo "the wrong key is in the output" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the gateway key leaks, or the keyless run is not a skip"; fi
fi

if it "aistack: verify AUTOOS_VERIFY_COMBOS replaces the combo list"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    _aistack_verify_route "$d" http://127.0.0.1:20128/v1/chat/completions key alpha-combo 200
    _aistack_verify_route "$d" http://127.0.0.1:20128/v1/chat/completions key beta-combo 404
    ok=1
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" AUTOOS_VERIFY_COMBOS="alpha-combo beta-combo" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "exit $rc, not 1" >&2; }
    grep -qx '  ok    combo alpha-combo' <<<"$out" || { ok=0; echo "alpha-combo not ok" >&2; }
    grep -qE '^  FAIL  combo beta-combo - HTTP 404' <<<"$out" || { ok=0; echo "beta-combo not FAIL" >&2; }
    [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "not exactly one FAIL" >&2; }
    grep -q 't2-worker-free-only' "$d/curl-argv.log" && { ok=0; echo "a default combo was still requested" >&2; }
    grep -q '"max_tokens":16' "$d/curl-argv.log" || { ok=0; echo "max_tokens 16 not sent" >&2; }
    # A name that could break out of the JSON body is refused, not sent.
    rm -f "$d/curl-argv.log"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" AUTOOS_VERIFY_COMBOS='bad"name' verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "bad name: exit $rc, not 1" >&2; }
    grep -q '^  FAIL  combo (invalid name)' <<<"$out" || { ok=0; echo "bad name: no FAIL" >&2; }
    [[ "$(grep -c 'chat/completions' "$d/curl-argv.log" || true)" == 0 ]] || { ok=0; echo "bad name: a request was sent" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "AUTOOS_VERIFY_COMBOS is not honoured"; fi
fi

if it "aistack: verify public URLs: skipped when unset, FAIL only for the one that is not a 302"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    ok=1
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "unset: exit $rc, not 0" >&2; }
    grep -q '^  skip  public URLs - AUTOOS_VERIFY_PUBLIC_URLS is not set' <<<"$out" || { ok=0; echo "unset: no skip line" >&2; }
    printf '%s\n' 'http://127.0.0.1:18081/ none - 302' 'http://127.0.0.1:18082/ none - 200' >>"$d/curl-table"
    out="$(_aistack_verify "$d" AUTOOS_VERIFY_PUBLIC_URLS="http://127.0.0.1:18081/ http://127.0.0.1:18082/" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "one 200: exit $rc, not 1" >&2; }
    grep -qx '  ok    public URL http://127.0.0.1:18081/' <<<"$out" || { ok=0; echo "the 302 URL is not ok" >&2; }
    grep -qE '^  FAIL  public URL http://127.0.0.1:18082/ - HTTP 200' <<<"$out" || { ok=0; echo "the 200 URL is not FAIL" >&2; }
    [[ "$(grep -c '^  FAIL' <<<"$out")" == 1 ]] || { ok=0; echo "not exactly one FAIL" >&2; }
    # No credentials go out with a public probe, and none in the URL are echoed.
    grep -qE '(^| )(-H|-u|--user|--header)( |$)' "$d/curl-argv.log" && { ok=0; echo "a public probe carried credentials" >&2; }
    out="$(_aistack_verify "$d" AUTOOS_VERIFY_PUBLIC_URLS="http://probe-user:probe-pass@127.0.0.1:18081/" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "userinfo: exit $rc, not 1" >&2; }
    grep -q '^  FAIL  public URL (rejected)' <<<"$out" || { ok=0; echo "userinfo: no FAIL" >&2; }
    grep -q 'probe-pass' <<<"$out" && { ok=0; echo "userinfo: the password is echoed" >&2; }
    grep -q 'probe-pass' "$d/curl-argv.log" && { ok=0; echo "userinfo: the URL was requested" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "public URL checks misbehave"; fi
fi

if it "aistack: verify checks the code dir in opencode and in the OpenHands sandbox volumes"; then
    ok=1
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    : >"$d/nocode-autoos-opencode"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 1 )) || { ok=0; echo "opencode without the tree: exit $rc, not 1" >&2; }
    grep -qE '^  FAIL  code dir .* visible in autoos-opencode - ' <<<"$out" || { ok=0; echo "opencode: no FAIL" >&2; }
    grep -qE '^  ok    code dir .* in openhands-app SANDBOX_VOLUMES$' <<<"$out" || { ok=0; echo "opencode case: openhands should stay ok" >&2; }
    rm -f "$d/nocode-autoos-opencode"
    # A volume list where the tree is one of several entries passes ...
    printf 'SANDBOX_VOLUMES=/elsewhere:/elsewhere:ro,%s:%s:rw\n' "$d/code" "$d/code" >"$d/env-openhands-app"
    out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "several volumes: exit $rc, not 0" >&2; }
    # ... one without it, or an unset value, does not.
    for vols in 'SANDBOX_VOLUMES=/elsewhere:/elsewhere:rw' 'SANDBOX_VOLUMES=' 'LLM_MODEL=x'; do
        printf '%s\n' "$vols" >"$d/env-openhands-app"
        out="$(_aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 1 )) || { ok=0; echo "$vols: exit $rc, not 1" >&2; }
        grep -qE '^  FAIL  code dir .* in openhands-app SANDBOX_VOLUMES - ' <<<"$out" || { ok=0; echo "$vols: no FAIL" >&2; }
    done
    # The directory is AUTOOS_CODE_DIR - the variable compose reads - from the
    # environment first, stack.env next; nothing is hard-coded.
    printf 'SANDBOX_VOLUMES=%s:%s:rw\n' "$d/other" "$d/other" >"$d/env-openhands-app"
    out="$(_aistack_verify "$d" AUTOOS_CODE_DIR="$d/other" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "env AUTOOS_CODE_DIR: exit $rc, not 0" >&2; }
    grep -qF "exec autoos-opencode test -d $d/other" "$d/docker.log" || { ok=0; echo "env: the exec did not use AUTOOS_CODE_DIR" >&2; }
    printf 'SANDBOX_VOLUMES=%s:%s:rw\n' "$d/fromfile" "$d/fromfile" >"$d/env-openhands-app"
    printf "AUTOOS_CODE_DIR='%s'\n" "$d/fromfile" >>"$d/cfg/stack.env"
    out="$(_aistack_verify "$d" AUTOOS_CODE_DIR= verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "stack.env AUTOOS_CODE_DIR: exit $rc, not 0" >&2; }
    grep -qF "exec autoos-opencode test -d $d/fromfile" "$d/docker.log" || { ok=0; echo "stack.env: the exec did not use its AUTOOS_CODE_DIR" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "the code dir check misbehaves"; fi
fi

if it "aistack: verify follows the compose profiles: a disabled openhands is skipped, an enabled one is checked"; then
    ok=1
    for style in inline block; do
        d="$(_aistack_sandbox)"
        _aistack_verify_sandbox "$d"
        t="$d/tree/configuration"
        mkdir -p "$t/docker/ai-stack"
        cp "$AISTACK/ai-stack.sh" "$AISTACK/opencode.Dockerfile" "$t/docker/ai-stack/"
        cp "$ROOT/configuration/env-file.sh" "$t/"
        if [[ "$style" == inline ]]; then
            sed 's/^    container_name: openhands-app$/&\n    profiles: ["extras", "openhands"]/' "$AISTACK/compose.yml" >"$t/docker/ai-stack/compose.yml"
        else
            sed 's/^    container_name: openhands-app$/&\n    profiles:\n      - extras\n      - openhands/' "$AISTACK/compose.yml" >"$t/docker/ai-stack/compose.yml"
        fi
        grep -q 'profiles:' "$t/docker/ai-stack/compose.yml" || { ok=0; echo "$style: the fixture has no profiles" >&2; }
        out="$(_AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack_verify "$d" verify)" && rc=0 || rc=$?
        (( rc == 0 )) || { ok=0; echo "$style, profile off: exit $rc, not 0" >&2; }
        grep -q 'container openhands-app' <<<"$out" && { ok=0; echo "$style, profile off: openhands-app is still checked" >&2; }
        grep -q '^  skip  code dir .*openhands' <<<"$out" || { ok=0; echo "$style, profile off: no skip for the sandbox volumes" >&2; }
        grep -q 'openhands-app' "$d/docker.log" && { ok=0; echo "$style, profile off: docker was asked about openhands-app" >&2; }
        out="$(_AISTACK_SH="$t/docker/ai-stack/ai-stack.sh" _aistack_verify "$d" COMPOSE_PROFILES=openhands verify)" && rc=0 || rc=$?
        (( rc == 0 )) || { ok=0; echo "$style, profile on: exit $rc, not 0" >&2; }
        grep -qx '  ok    container openhands-app' <<<"$out" || { ok=0; echo "$style, profile on: openhands-app not checked" >&2; }
        rm -rf "$d"
    done
    if (( ok )); then pass; else fail "compose profiles are not honoured"; fi
fi

if it "aistack: verify is read-only: only inspect and exec reach docker, nothing on disk changes"; then
    d="$(_aistack_sandbox)"
    _aistack_verify_sandbox "$d"
    printf '%s\n' 'http://127.0.0.1:18081/ none - 302' >>"$d/curl-table"
    ok=1
    before="$(_aistack_snapshot "$d")"
    out="$(_aistack_verify "$d" AUTOOS_OMNIROUTE_KEY="$_AISTACK_VERIFY_KEY" AUTOOS_VERIFY_PUBLIC_URLS="http://127.0.0.1:18081/" verify)" && rc=0 || rc=$?
    (( rc == 0 )) || { ok=0; echo "exit $rc, not 0: $out" >&2; }
    after="$(_aistack_snapshot "$d")"
    [[ "$before" == "$after" ]] || { ok=0; echo "a file changed: $(diff <(printf '%s' "$before") <(printf '%s' "$after") | head -5)" >&2; }
    [[ -s "$d/docker.log" ]] || { ok=0; echo "docker was never asked anything (a vacuous run)" >&2; }
    [[ "$(grep -cE '^(start|stop|restart|rm|up|down|run|create|compose|build|pull|network|kill|pause|unpause|cp|update)( |$)' "$d/docker.log" || true)" == 0 ]] \
        || { ok=0; echo "a mutating docker verb: $(grep -E '^(start|stop|restart|rm|up|down|run|create|compose)' "$d/docker.log" | head -3)" >&2; }
    grep -vE '^(inspect|exec) ' "$d/docker.log" | grep -q . && { ok=0; echo "docker verbs beyond inspect/exec: $(grep -vE '^(inspect|exec) ' "$d/docker.log" | head -3)" >&2; }
    grep '^exec ' "$d/docker.log" | grep -vE '^exec [^ ]+ test -d ' | grep -q . && { ok=0; echo "an exec that is not test -d" >&2; }
    # Nothing but docker inspect/exec: not systemctl, not ss, not the plain curl on PATH.
    grep -vE '^docker: (inspect|exec) ' "$d/events.log" | grep -q . && { ok=0; echo "another tool was called: $(grep -vE '^docker: (inspect|exec) ' "$d/events.log" | head -3)" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "verify changed something or ran a docker verb it must not"; fi
fi

if it "aistack: verify is listed in the unknown-command message and in --help"; then
    d="$(_aistack_sandbox)"
    ok=1
    out="$(_aistack "$d" bogus)" && rc=0 || rc=$?
    (( rc == 2 )) || { ok=0; echo "exit $rc, not 2" >&2; }
    grep -qE '^Unknown command: bogus \(.*\bverify\b.*\)' <<<"$out" || { ok=0; echo "not in the message: $out" >&2; }
    out="$(_aistack "$d" --help)"
    grep -q 'ai-stack.sh verify' <<<"$out" || { ok=0; echo "not in --help" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "verify is not advertised"; fi
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
SUMMARY_PRINTED=1
if (( FAIL )); then
    printf '\n  failures:\n'
    for f in "${FAILED_NAMES[@]}"; do printf '    - %s\n' "$f"; done
    exit 1
fi
exit 0
