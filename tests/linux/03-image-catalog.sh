# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

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

