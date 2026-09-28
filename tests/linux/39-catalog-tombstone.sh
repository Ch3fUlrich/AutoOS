# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Catalog tombstone entries (SPEC-OMNI A7a) ──────────────────────────────
# A tombstone is a component id that stays KNOWN — so saved state files and
# --only keep resolving — while installing nothing. Everything here is asserted
# against tests/fixtures/catalog-tombstone.json, never against a shipped
# catalog: retiring a real component is a catalog change with its own review,
# and a test hinged on one would break the moment that change landed.
describe "catalog tombstone"

TOMBSTONE_FIXTURE="tests/fixtures/catalog-tombstone.json"

# fixture_variant <python-source> — print the path of a copy of the fixture
# edited by <python-source> (which rewrites the file named by argv[1]). Each
# validator case needs a differently-broken catalog, and re-authoring the whole
# file per case would hide which field the case is about.
fixture_variant() {
    local path
    path="$(mktemp)"
    cp "$TOMBSTONE_FIXTURE" "$path"
    python3 -c "$1" "$path"
    printf '%s\n' "$path"
}

# tombstone_tree [<python-source>] — a scratch repo tree whose catalog is the
# fixture, for running the real setup.sh against a tombstone. Like the
# post-install tree in 14-state: the code under test is linked, never
# copied-and-edited, so a green case means setup.sh's own path produced it.
# Prints the tree path; the caller removes it.
tombstone_tree() {
    local tree
    tree="$(mktemp -d)"
    mkdir -p "$tree/home" "$tree/catalog"
    ln -s "$ROOT/setup.sh" "$tree/setup.sh"
    ln -s "$ROOT/lib" "$tree/lib"
    cp "$TOMBSTONE_FIXTURE" "$tree/catalog/linux.json"
    if [[ -n "${1:-}" ]]; then python3 -c "$1" "$tree/catalog/linux.json"; fi
    printf '%s\n' "$tree"
}

# tombstone_state_results <file> — the state file's buckets and answers on one
# line, so a case asserts the recorded outcome and not only the printed one.
tombstone_state_results() {
    python3 -c '
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
r = d["results"]
print("installed=[%s] skipped=[%s] failed=[%s] answers=%s"
      % (",".join(r["installed"]), ",".join(r["skipped"]), ",".join(r["failed"]),
         json.dumps(d["answers"], sort_keys=True)))
' "$1" 2>&1 || echo "no readable state file"
}

if it "catalog: a tombstone entry validates"; then
    out="$(catalog_validate "$TOMBSTONE_FIXTURE" 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "rc=$rc: $out"; fi
fi

if it "catalog: a tombstone flag that is not the boolean true is rejected"; then
    # A string "true" reads as retired to a shell test and as live to a `is True`
    # check, so an entry written that way is retired everywhere and nowhere.
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "retired-demo": c["tombstone"] = "yes"
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"tombstone"* ]]; then pass
    else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog: a note on an entry that is not a tombstone is rejected"; then
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "keep-demo": c["note"] = "meant to say notes"
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"note"* && "$out" == *"tombstone"* ]]; then pass
    else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog: an empty note is rejected"; then
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "retired-demo": c["note"] = "   "
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"note"* ]]; then pass
    else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog: a component that requires a tombstone is rejected"; then
    # Requiring a retired id silently loses the dependency: the tombstone
    # installs nothing, so whatever needed it gets a plan row that did no work.
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "prompted-demo": c["requires"] = ["retired-demo"]
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"tombstone"* ]]; then pass
    else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog loading: a tombstone keeps its flag and its note"; then
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    i="$(catalog_index_of retired-demo)"
    j="$(catalog_index_of keep-demo)"
    problems=""
    [[ "${CAT_TOMBSTONE[i]:-}" == "1" ]] || problems+="[tombstone flag is [${CAT_TOMBSTONE[i]:-<missing>}], expected 1] "
    [[ "${CAT_RETIRE_NOTE[i]:-}" == "wired by keep-demo now" ]] \
        || problems+="[retire note is [${CAT_RETIRE_NOTE[i]:-<missing>]}] "
    [[ "${CAT_TOMBSTONE[j]:-}" == "0" ]] || problems+="[an ordinary entry loaded as a tombstone] "
    # The tombstone keeps its own description and profile list: it is a known
    # row, not a blank one.
    [[ "${CAT_DESC[i]:-}" == "A tombstone: the id stays known, nothing installs." ]] \
        || problems+="[description lost: [${CAT_DESC[i]:-}]] "
    [[ "${CAT_PROFILES[i]:-}" == "workstation,light" ]] || problems+="[profiles lost: [${CAT_PROFILES[i]:-}]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog loading: a profile never pre-selects a tombstone"; then
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    problems=""
    for profile in workstation light; do
        defaults="$(catalog_profile_defaults "$profile")"
        # keep-demo is in both profiles, so an empty answer means the loader is
        # broken rather than the tombstone correctly dropped.
        [[ " $defaults " == *" keep-demo "* ]] || problems+="[$profile pre-selected nothing at all (got [$defaults])] "
        [[ " $defaults " != *" retired-demo "* ]] || problems+="[$profile pre-selected the tombstone (got [$defaults])] "
    done
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog loading: a tombstone pulls no dependency into the plan"; then
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "retired-demo": c["requires"] = ["pulled-demo"]
json.dump(d, open(p, "w"))
')"
    catalog_load "$f" x64 0
    plan="$(catalog_resolve retired-demo 2>&1)"
    rm -f "$f"
    assert_eq "$plan" "retired-demo"
fi

if it "catalog loading: a tombstone is never reported installed"; then
    # Detection is stubbed to "everything on this machine is here" (AGENTS.md §5:
    # synthetic system objects, never the live machine), so the only row that can
    # come back NOT installed is the one that must not claim a success AutoOS no
    # longer produces.
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    out="$(
        custom_is_installed() { return 0; }
        catalog_probe_installed
        printf 'tombstone=%s ordinary=%s\n' \
            "${CAT_INSTALLED[$(catalog_index_of retired-demo)]:-}" \
            "${CAT_INSTALLED[$(catalog_index_of keep-demo)]:-}"
    )"
    assert_eq "$out" "tombstone=0 ordinary=1"
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
fi

if it "list: --list marks a retired id (retired)"; then
    tree="$(tombstone_tree)"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --list --no-color 2>&1)"; rc=$?
    rm -rf "$tree"
    line="$(printf '%s\n' "$out" | grep -E '^ +retired-demo ' | head -1)"
    kept="$(printf '%s\n' "$out" | grep -E '^ +keep-demo ' | head -1)"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ -n "$line" && "$line" == *"(retired)"* ]] || problems+="[the tombstone row reads [$line]] "
    [[ -n "$kept" && "$kept" != *"(retired)"* ]] || problems+="[the ordinary row reads [$kept]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "end-to-end: --only a retired id is accepted and reports skipped retired"; then
    tree="$(tombstone_tree)"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --yes --no-color \
        --save-state "$tree/state.json" 2>&1)"; rc=$?
    saved="$(tombstone_state_results "$tree/state.json")"
    rm -rf "$tree"
    problems=""
    # An unknown id exits 1 on this path; a known-but-retired id must not.
    (( rc == 0 )) || problems+="[rc=$rc, expected 0: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" != *"Unknown component"* ]] || problems+="[--only refused a known id] "
    [[ "$out" == *"skipped: retired"* ]] || problems+="[no 'skipped: retired' line: $(printf '%s\n' "$out" | tail -5)] "
    [[ "$out" == *"wired by keep-demo now"* ]] || problems+="[the note is not shown] "
    [[ "$saved" == "installed=[] skipped=[retired-demo] failed=[] answers={}" ]] \
        || problems+="[the state file says: $saved] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "end-to-end: a retired id installs nothing, asks nothing, runs no postInstall"; then
    # The entry keeps the machinery it had before it was retired — a prompt and
    # a postInstall — which is how a real one arrives. The mechanism has to
    # ignore both, not rely on the author having cleaned them out first.
    tree="$(tombstone_tree '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "retired-demo":
            c["prompt"] = "demo_url"
            c["postInstall"] = "autoos_tombstone_postinstall_must_not_run"
json.dump(d, open(p, "w"))
')"
    out="$(
        autoos_tombstone_postinstall_must_not_run() { echo "TOMBSTONE POSTINSTALL RAN"; }
        export -f autoos_tombstone_postinstall_must_not_run
        HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --yes --no-color \
            --save-state "$tree/state.json" 2>&1
    )"; rc=$?
    saved="$(tombstone_state_results "$tree/state.json")"
    rm -rf "$tree"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" != *"TOMBSTONE POSTINSTALL RAN"* ]] || problems+="[its postInstall ran] "
    [[ "$out" != *"DEMO PROMPT"* ]] || problems+="[its prompt was asked] "
    [[ "$saved" == *"answers={}"* ]] || problems+="[an answer was recorded: $saved] "
    [[ "$saved" == *"failed=[]"* ]] || problems+="[the run failed: $saved] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "from-state: a retired id replays as skipped and never warns"; then
    tree="$(tombstone_tree)"
    HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo,keep-demo --yes --no-color \
        --save-state "$tree/state.json" >/dev/null 2>&1
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --from-state "$tree/state.json" --yes --no-color \
        --save-state "$tree/state2.json" 2>&1)"; rc=$?
    rm -rf "$tree"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" != *"unknown component"* ]] || problems+="[a retired id is treated as unknown] "
    [[ "$out" == *"skipped: retired"* ]] || problems+="[no skipped: retired line: $(printf '%s\n' "$out" | tail -5)] "
    [[ "$out" == *"[2/2] Kept Component"* ]] || problems+="[the run stopped at the tombstone] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "from-state: an id that is not in the catalog at all still warns"; then
    # The tombstone widens what counts as known; this is the guard that it did
    # not widen it to everything.
    tree="$(tombstone_tree)"
    HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --yes --no-color \
        --save-state "$tree/state.json" >/dev/null 2>&1
    python3 -c '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["selected"].append("never-existed-demo")
json.dump(d, open(p, "w"))
' "$tree/state.json"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --from-state "$tree/state.json" --yes --no-color \
        --save-state "$tree/state2.json" 2>&1)"; rc=$?
    rm -rf "$tree"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" == *"unknown component 'never-existed-demo'"* ]] \
        || problems+="[a genuinely unknown id was not named: $(printf '%s\n' "$out" | tail -5)] "
    [[ "$out" != *"unknown component 'retired-demo'"* ]] || problems+="[the retired id warned] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "summary: a retired id counts as already present, never installed"; then
    tree="$(tombstone_tree)"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --yes --no-color 2>&1)"; rc=$?
    rm -rf "$tree"
    # Each key is anchored to its whole line: the Detected system block prints
    # "Installed apps", which would otherwise answer for the Summary's "Installed".
    got_installed="$(printf '%s\n' "$out" | grep -E '^ +Installed +[0-9]+$' | head -1 | tr -s ' ')"
    got_present="$(printf '%s\n' "$out" | grep -E '^ +Already present +[0-9]+$' | head -1 | tr -s ' ')"
    got_failed="$(printf '%s\n' "$out" | grep -E '^ +Failed +[0-9]+$' | head -1 | tr -s ' ')"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$got_installed" == " Installed 0" ]] || problems+="[$got_installed] "
    [[ "$got_present" == " Already present 1" ]] || problems+="[$got_present] "
    [[ "$got_failed" == " Failed 0" ]] || problems+="[$got_failed] "
    # A retired row is not something the user can go and find: the landing
    # report would hunt for its launcher and answer "no launcher found yet".
    launcher="$(printf '%s\n' "$out" | grep -c 'no launcher found' || true)"
    (( launcher == 0 )) || problems+="[the landing report hunted for its launcher $launcher time(s)] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "idempotency: a retired id reports skipped on the second run too"; then
    tree="$(tombstone_tree)"
    a="$(HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --yes --no-color 2>&1)"; rc_a=$?
    b="$(HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --yes --no-color 2>&1)"; rc_b=$?
    rm -rf "$tree"
    skipped_a="$(printf '%s\n' "$a" | grep -c 'skipped: retired' || true)"
    skipped_b="$(printf '%s\n' "$b" | grep -c 'skipped: retired' || true)"
    problems=""
    (( rc_a == 0 && rc_b == 0 )) || problems+="[rc $rc_a then $rc_b] "
    [[ "$skipped_a" == "1" && "$skipped_b" == "1" ]] \
        || problems+="[run one said skipped: retired $skipped_a time(s), run two $skipped_b] "
    [[ "$b" != *"Retired Component done"* ]] || problems+="[the second run reported it installed/done] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "end-to-end: a profile dry run never plans a retired id"; then
    tree="$(tombstone_tree)"
    # The fixture puts the tombstone in the light profile beside keep-demo, so a
    # profile run is the production path that would otherwise plan it.
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --profile light --dry-run --yes --no-color 2>&1)"; rc=$?
    rm -rf "$tree"
    planned="$(printf '%s\n' "$out" | grep -E '^ +[0-9]+\.')"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$planned" == *"Kept Component"* ]] || problems+="[the profile planned nothing at all: [$planned]] "
    [[ "$planned" != *"Retired Component"* ]] || problems+="[the profile planned the tombstone: [$planned]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog tombstone: a live entry that requires a retired id is refused at resolve"; then
    # The validator is the only thing that used to catch this, and a normal run
    # never validates: the dependent would lose its requirement in the walk and
    # install green. Resolve has to refuse it out loud instead.
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "prompted-demo": c["requires"] = ["retired-demo"]
json.dump(d, open(p, "w"))
')"
    catalog_load "$f" x64 0
    catalog_resolve prompted-demo >/dev/null 2>&1
    rm -f "$f"
    problems=""
    [[ "$(catalog_resolve_blocked prompted-demo)" == *"requires retired retired-demo"* ]] \
        || problems+="[no refusal recorded for the dependent: [$(catalog_resolve_blocked prompted-demo)]] "
    # The refused row stays in the plan so the plan and the report name the same
    # component; what execution refuses is never silently absent.
    [[ " $PLAN_IDS " == *" prompted-demo "* ]] || problems+="[the refused id vanished from the plan: [$PLAN_IDS]] "
    [[ " $PLAN_IDS " == *" retired-demo "* ]] || problems+="[the tombstone row was dropped: [$PLAN_IDS]] "
    [[ " $PLAN_AUTO " == *" retired-demo "* ]] || problems+="[the tombstone is not marked a dependency: [$PLAN_AUTO]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
fi

if it "catalog tombstone: an ordinary resolve records no refusal"; then
    # The guard must not fire on the normal path — that would turn every run into
    # a false failure report.
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    catalog_resolve keep-demo retired-demo >/dev/null 2>&1
    problems=""
    [[ -z "$(catalog_resolve_blocked keep-demo)$(catalog_resolve_blocked retired-demo)" ]] \
        || problems+="[refusals recorded for a clean plan: [$(catalog_resolve_blocked keep-demo)|$(catalog_resolve_blocked retired-demo)]] "
    [[ "$PLAN_IDS" == "keep-demo retired-demo" || "$PLAN_IDS" == "retired-demo keep-demo" ]] \
        || problems+="[plan was [$PLAN_IDS]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog tombstone: a dependent of a refused component is refused too"; then
    # A's requirement cannot be installed, so installing B on top of A repeats
    # the same defect one level up: the cascade has to be refused as well.
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "prompted-demo": c["requires"] = ["retired-demo"]
        if c["id"] == "pulled-demo":  c["requires"] = ["prompted-demo"]
json.dump(d, open(p, "w"))
')"
    catalog_load "$f" x64 0
    catalog_resolve pulled-demo >/dev/null 2>&1
    rm -f "$f"
    problems=""
    [[ "$(catalog_resolve_blocked prompted-demo)" == *"requires retired retired-demo"* ]] \
        || problems+="[the direct dependent was not refused: [$(catalog_resolve_blocked prompted-demo)]] "
    [[ "$(catalog_resolve_blocked pulled-demo)" == *"requires prompted-demo"* ]] \
        || problems+="[the dependent of the refused id was not refused: [$(catalog_resolve_blocked pulled-demo)]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
fi

if it "catalog tombstone: the serve payload carries the retirement facts"; then
    # The page resolves dependencies in the browser, so it can only honour a
    # retired id if the payload says which ids are retired. state_components is
    # build_state's own projection — the same function the server calls.
    failures="$(python3 - "$TOMBSTONE_FIXTURE" <<'PY'
import json, sys
sys.path.insert(0, "lib/linux")
from serve import state_components

catalog = json.load(open(sys.argv[1], encoding="utf-8"))
comps = {c["id"]: c for c in state_components(catalog, "linux", {}, "x64", False, {})}
bad = []
tomb = comps.get("retired-demo") or {}
keep = comps.get("keep-demo") or {}
if tomb.get("tombstone") is not True:
    bad.append("the tombstone row does not say tombstone: true (%r)" % tomb.get("tombstone"))
if tomb.get("note") != "wired by keep-demo now":
    bad.append("the retire note is %r" % tomb.get("note"))
if keep.get("tombstone"):
    bad.append("an ordinary entry was marked retired")
if "note" not in keep:
    bad.append("an ordinary entry has no note key at all")
print("; ".join(bad))
PY
)"
    assert_eq "$failures" ""
fi

if it "catalog tombstone: the web page reads the retirement fields"; then
    # The cheap guard for a host without node: the page must ask the payload at
    # all. tests/test-web-progress.js runs the shipped functions and is the real
    # proof that a retired id cannot be ticked, asked or pulled in.
    ok=1
    for marker in 'c.tombstone' 'chip-retired' 'function retiredChip'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "the page ignores the retirement fields"; fi
fi

if it "end-to-end: --only an entry that requires a retired id fails loudly, not green"; then
    tree="$(tombstone_tree '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "prompted-demo": c["requires"] = ["retired-demo"]
json.dump(d, open(p, "w"))
')"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only prompted-demo --yes --no-color --dry-run \
        --save-state "$tree/state.json" 2>&1)"; rc=$?
    saved="$(tombstone_state_results "$tree/state.json")"
    rm -rf "$tree"
    problems=""
    (( rc != 0 )) || problems+="[the run exited 0 over a requirement that cannot be satisfied] "
    [[ "$out" == *"requires retired retired-demo"* ]] \
        || problems+="[nothing named the refusal: $(printf '%s\n' "$out" | tail -5)] "
    [[ "$out" != *"Asks A Question done"* ]] || problems+="[the refused component reported itself done] "
    [[ "$saved" == *"failed=[prompted-demo]"* ]] || problems+="[the state file says: $saved] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "end-to-end: a tombstone pulled in as a dependency is tagged both ways"; then
    # setup.sh and setup.ps1 print the same row for the same plan; the Windows
    # twin hid "(retired)" behind an elseif the moment a tombstone was a
    # dependency rather than hand-picked.
    tree="$(tombstone_tree '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "prompted-demo": c["requires"] = ["retired-demo"]
json.dump(d, open(p, "w"))
')"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only prompted-demo --yes --no-color --dry-run 2>&1)"
    rm -rf "$tree"
    row="$(printf '%s\n' "$out" | grep -E '^[[:space:]]+[0-9]+\. Retired Component' | head -1)"
    problems=""
    [[ "$row" == *"(dependency)"* ]] || problems+="[the row does not say (dependency): [$row]] "
    [[ "$row" == *"(retired)"* ]] || problems+="[the row does not say (retired): [$row]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "end-to-end: a retired id chosen by hand still plans one marked row"; then
    tree="$(tombstone_tree)"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --dry-run --yes --no-color 2>&1)"; rc=$?
    rm -rf "$tree"
    row="$(printf '%s\n' "$out" | grep -E '^ +1\. Retired Component' | head -1)"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$row" == *"(retired)"* ]] || problems+="[the plan row reads [$row]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi
