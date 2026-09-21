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
assert len(models) >= 20, f"expected >= 20 models, got {len(models)}"
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
    missing = {m["id"] for m in models if m["id"] not in calls and m["id"] != "muse-spark"}
    muse_aliases = {"muse-spark-1.3", "muse-spark-1.3-contributor"}
    if "muse-spark" not in calls or not muse_aliases <= calls:
        print(f"{path}: muse-spark aliases incomplete: {sorted(calls)}"); sys.exit(1)
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
aliases = {"muse-spark-1.3": "muse-spark", "muse-spark-1.3-contributor": "muse-spark",
           "ollama-qwen-coder": "ollama-qwen2.5-coder"}
price_dst = {"paid_input_price": "paid_input_cost_per_token",
             "paid_output_price": "paid_output_cost_per_token",
             "cache_read_price": "cache_read_cost_per_token"}
files = sorted(glob.glob("openhands/profiles/*.json"))
assert files, "no vendored profiles"
for path in files:
    name = os.path.basename(path)[:-5]
    mid = aliases.get(name, name)
    assert mid in models, f"{path}: unknown model {mid}"
    m, p = models[mid], json.load(open(path, encoding="utf-8"))
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
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" setup_openhands_config >/dev/null 2>&1
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
fi

if it "setup_openhands_config writes gateway tier profiles with a key, none without"; then
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" AUTOOS_OMNIROUTE_KEY="test-omni-key" setup_openhands_config >/dev/null 2>&1
        python3 - "$tmp/.openhands" <<'PY'
import json, os, sys
d = sys.argv[1]
t1 = json.load(open(os.path.join(d, "profiles", "autoos-tier1.json"), encoding="utf-8"))
t3 = json.load(open(os.path.join(d, "profiles", "autoos-tier3.json"), encoding="utf-8"))
print(t1["model"], t1["base_url"], t1["api_key"], t1["reasoning_effort"],
      t3["model"], t3["reasoning_effort"], t3["enable_encrypted_reasoning"])
PY
    )"
    rm -rf "$tmp"
    assert_eq "$out" "openai/tier1 http://host.docker.internal:20128/v1 test-omni-key high openai/tier3 none False"
    tmp="$(mktemp -d)"
    out="$(
        SYS_HOME="$tmp"; AUTOOS_DRY_RUN=0
        unset MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY AUTOOS_OMNIROUTE_KEY
        curl() { return 6; }
        OLLAMA_BASE_URL="http://ollama:11434" setup_openhands_config >/dev/null 2>&1
        test -e "$tmp/.openhands/profiles/autoos-tier1.json" && echo PRESENT || echo ABSENT
    )"
    rm -rf "$tmp"
    assert_eq "$out" "ABSENT"
fi

if it "tier profiles come from the spec, installer and tool agree"; then
    report="$(python3 - <<'PY'
import json
spec = json.load(open("configuration/openhands/tier-profiles.json", encoding="utf-8"))
print("%s|%s|%s" % (
    ",".join(t["id"] for t in spec["tiers"]),
    spec["gateway_base_url"],
    ",".join(t["model"] for t in spec["tiers"])))
PY
)"
    assert_eq "$report" "tier1,tier2,tier3|http://host.docker.internal:20128/v1|openai/tier1,openai/tier2,openai/tier3"
    # The embedded installer must read the spec, never inline tiers.
    grep -q 'tier-profiles.json' lib/linux/install.sh || { fail "installer does not read the tier spec"; }
    # Generator round-trip with a fixture key (env hidden: the suite never
    # asserts on live system state).
    tmp="$(mktemp -d)"; printf 'omniroute: test-omni-key\n' >"$tmp/api-keys.yml"
    out="$( ( unset AUTOOS_OMNIROUTE_KEY; python3 tools/sync-openhands-profiles.py --openhands-dir "$tmp" --keys-file "$tmp/api-keys.yml" ) 2>&1)"
    rm -rf "$tmp"
    assert_eq "$(printf '%s' "$out" | grep -c written)" "3"
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
        unset MUSE_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY CONTEXT7_API_KEY
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

if it "--check-catalog validates all five catalogs by type, not just component catalogs"; then
    # Regression: setup.sh used to run every catalog/*.json through the
    # component-catalog validator, which rejects images.json and
    # engines.json outright (they have no 'categories' key). Assert every
    # file is actually reported valid, not just that the overall rc is 0 —
    # rc could go green for the wrong reason (e.g. an empty glob).
    out="$(bash setup.sh --check-catalog 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"engines.json is valid"* && "$out" == *"images.json is valid"* \
        && "$out" == *"linux.json is valid"* && "$out" == *"macos.json is valid"* && "$out" == *"windows.json is valid"*         && "$out" == *"agent-harness.json is valid"* ]]; then
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
    bad="$(python3 - <<'PY'
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
models = [m["name"] for m in omni.get("available_models", [])]
# Keys never land in settings.json (Zed docs: keychain/UI or env).
# Pins come from the harness at runtime, never as literals in lib/.
h = json.load(open("catalog/agent-harness.json", encoding="utf-8"))
ctx = cfg.get("context_servers", {})
pinok = ",".join(sorted(
    "pin-ok" if h["mcp_servers"][n]["package"] in " ".join(ctx.get(n, {}).get("args", []))
    else "MISSING:" + n
    for n in ("serena", "graphify")))
print("%s|%s|%s|%s|%s|%s|%s" % (
    cfg.get("theme"), omni.get("api_url"), ",".join(models),
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
        "mine|http://127.0.0.1:20128/v1|auto/smart,auto,auto/cheap,tier1,tier1-clean,tier2,tier2-clean,tier3,tier3-clean|False|http://127.0.0.1:4000/v1|False|pin-ok,pin-ok"
    assert_eq "$line2" \
        "bypass=bypass|off=|provider=autoos-omniroute|model=tier1|allow=allow|ctx=graphify,serena"
    assert_eq "backups=$backups|leaks=$leaks" "backups=1|leaks=0"
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
        "omniroute/tier1|http://127.0.0.1:20128/v1|auto,auto/cheap,auto/smart,tier1,tier1-clean,tier2,tier2-clean,tier3,tier3-clean|True|context7,graphify,playwright,serena|pin-ok,pin-ok,pin-ok,pin-ok"
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

if it "openhands launch is detached, probed and stale-settings safe"; then
    # -it fails without a TTY and foreground never returns; schema_version 6
    # settings 500 the current image. Both fixed 2026-09-21 - pin the shape.
    ok=1
    for f in configuration/start-stack.ps1 configuration/start-stack.sh; do
        grep -q 'docker run -d ' "$f" || { ok=0; echo "not detached: $f" >&2; }
        grep -q 'docker run -it' "$f" && { ok=0; echo "still -it: $f" >&2; }
        grep -q 'schema_version' "$f" || { ok=0; echo "no stale-settings guard: $f" >&2; }
        grep -q 'autoos-backup' "$f" || { ok=0; echo "no backup: $f" >&2; }
        grep -q 'docker logs openhands-app' "$f" || { ok=0; echo "no probe: $f" >&2; }
        # Tier profiles re-project from the spec on every start (never stale).
        grep -q 'sync-openhands-profiles' "$f" || { ok=0; echo "never syncs: $f" >&2; }
        # Only versions NEWER than the image (6+) move aside: a live v3 file
        # serves fine, so a blanket "!= 4" nuke would destroy working configs.
        grep -qE 'ge 6|>= 6|>=6' "$f" || { ok=0; echo "indiscriminate version nuke: $f" >&2; }
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
    printf 'groq: dummy-groq-1\nmeta: dummy-meta-2\nzen: REPLACE_WITH_ZEN_KEY\n' >"$tmp/api-keys.yml"
    out="$(python3 tools/mirror-litellm-env.py --keys "$tmp/api-keys.yml" --env "$tmp/.env" 2>&1)"
    rc=$?
    leaked="$(printf '%s' "$out" | grep -c 'dummy-' || true)"
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "mirror rc=$rc" >&2; }
    [[ "$leaked" == "0" ]] || { ok=0; echo "value leaked" >&2; }
    grep -q '^GROQ_API_KEY=dummy-groq-1$' "$tmp/.env" || { ok=0; echo "groq" >&2; }
    grep -q '^META_API_KEY=dummy-meta-2$' "$tmp/.env" || { ok=0; echo "meta" >&2; }
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
# Plain muse-spark-1.3 is BLOCKED (operator 2026-09-21): the only spark in
# any tier is the contributor.
import re as _re2
if _re2.search(r"muse-spark-1\.3(?!-contributor)", " ".join(m for c in d["combos"] for m in c["models"])):
    problems.append("plain-spark-blocked")
# tier1 is spark-only: gemini must never occupy a 1M slot again.
if any("gemini" in m for m in by["tier1"]):
    problems.append("tier1-gemini")
# *-clean = paid legs only: no free pool may train on private prompts.
# Free = contributor-free, groq / cerebras / sambanova / gemini hosts,
# mistral-code + qwen free pools. -contributor (trains by contract) is
# banned in tier2-clean/tier3-clean; tier1-clean carries it deliberately
# since the 2026-09-21 contributor-only block (paid-only, trains).
# Direct-key legs (mistral-small, deepseek, openrouter paid, zen paid)
# bill past the pool on the same key, so they stay.
import re
free = re.compile(r"contributor-free|^(groq|cerebras|sambanova|gemini)/|mistral/mistral-code|/qwen")
trains = re.compile(r"-contributor$")
for n in ("tier1-clean", "tier2-clean", "tier3-clean"):
    bad = [m for m in by[n] if free.search(m)]
    if bad:
        problems.append(n + "-free:" + ",".join(bad))
for n in ("tier2-clean", "tier3-clean"):
    bad = [m for m in by[n] if trains.search(m)]
    if bad:
        problems.append(n + "-trains:" + ",".join(bad))
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
if t3 != [("subagent", "*", "deny"), ("edit", "*", "deny"), ("write", "*", "deny"), ("read", "*", "allow"), ("grep", "*", "allow"), ("glob", "*", "allow"), ("bash", "*", "allow")]:
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
