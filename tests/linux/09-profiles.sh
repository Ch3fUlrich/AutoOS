# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

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

