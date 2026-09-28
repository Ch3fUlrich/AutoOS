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
    failures="$(python3 - "$TOMBSTONE_FIXTURE" <<'PY' 2>&1
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
    # No --dry-run here, and nothing is installed by it either: the refused
    # component never reaches install_component, so the run touches no disk but
    # the state file — which is what has to carry the failure.
    tree="$(tombstone_tree '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "prompted-demo": c["requires"] = ["retired-demo"]
json.dump(d, open(p, "w"))
')"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only prompted-demo --yes --no-color \
        --save-state "$tree/state.json" 2>&1)"; rc=$?
    saved="$(tombstone_state_results "$tree/state.json")"
    rm -rf "$tree"
    problems=""
    (( rc != 0 )) || problems+="[the run exited 0 over a requirement that cannot be satisfied] "
    [[ "$out" == *"requires retired retired-demo"* ]] \
        || problems+="[nothing named the refusal: $(printf '%s\n' "$out" | tail -5)] "
    [[ "$out" != *"Asks A Question done"* ]] || problems+="[the refused component reported itself done] "
    [[ "$out" != *"DEMO PROMPT"* ]] || problems+="[its prompt was asked, for a run that will not install it] "
    # The retired row stays in the plan and reports its own honest outcome —
    # skipped, not installed and not a failure — beside the dependent it blocked.
    [[ "$out" == *"Retired Component: skipped: retired"* ]] \
        || problems+="[the retired row reported no skip: $(printf '%s\n' "$out" | tail -5)] "
    [[ "$saved" == "installed=[] skipped=[retired-demo] failed=[prompted-demo] answers={}" ]] \
        || problems+="[the state file says: $saved] "
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

# ─── replaced_by: a replayed retirement keeps its work ─────────────────────
# A state file saved before a retirement names the retired id and none of the ids
# that inherited its work, so replaying it did the retirement's bookkeeping and
# none of the installing. `replaced_by` is what a tombstone carries so the replay
# can find them; the fixture's `replaced-demo` is the retired-with-successors
# shape and `retired-demo` stays the retired-without-successors shape.
if it "catalog: a tombstone with replaced_by validates"; then
    out="$(catalog_validate "$TOMBSTONE_FIXTURE" 2>&1)"; rc=$?
    if [[ $rc -eq 0 ]]; then pass; else fail "rc=$rc: $out"; fi
fi

if it "catalog: replaced_by naming an unknown id is rejected"; then
    # An id that does not exist can never be planned, so the expansion would
    # promise work it cannot deliver and the replay would lose it silently.
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "replaced-demo": c["replaced_by"] = ["never-existed-demo"]
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"replaced_by"* ]]; then pass
    else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog: replaced_by naming a tombstone is rejected"; then
    # Successors have to install something, or the replay lands on another row
    # that reports skipped and the work is lost one step later.
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "replaced-demo": c["replaced_by"] = ["retired-demo"]
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"replaced_by"* ]]; then pass
    else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog: replaced_by on an entry that is not a tombstone is rejected"; then
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "keep-demo": c["replaced_by"] = ["successor-demo"]
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"replaced_by"* && "$out" == *"tombstone"* ]]
    then pass; else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog: an empty replaced_by is rejected"; then
    # The field means "the work moved to these ids"; an empty list is an author
    # intent that got lost, not a statement that nothing replaced it.
    f="$(fixture_variant '
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "replaced-demo": c["replaced_by"] = []
json.dump(d, open(p, "w"))
')"
    out="$(catalog_validate "$f" 2>&1)"; rc=$?
    rm -f "$f"
    if [[ $rc -ne 0 && "$out" == *"replaced_by"* ]]; then pass
    else fail "rc=$rc out=[$out]"; fi
fi

if it "catalog loading: a tombstone keeps its replaced_by and a live entry keeps none"; then
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    i="$(catalog_index_of replaced-demo)"
    j="$(catalog_index_of retired-demo)"
    k="$(catalog_index_of keep-demo)"
    problems=""
    [[ "${CAT_REPLACED_BY[i]:-<missing>}" == "keep-demo,successor-demo" ]] \
        || problems+="[replaced_by is [${CAT_REPLACED_BY[i]:-<missing>}]] "
    [[ -z "${CAT_REPLACED_BY[j]:-}" ]] \
        || problems+="[a tombstone with no replaced_by loaded one: [${CAT_REPLACED_BY[j]}]] "
    [[ -z "${CAT_REPLACED_BY[k]:-}" ]] \
        || problems+="[an ordinary entry loaded a replaced_by: [${CAT_REPLACED_BY[k]}]] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog loading: expansion replays a tombstone as the ids that replaced it"; then
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    EXPANDED_IDS=""; EXPANDED_LINES=()
    catalog_expand_replacements replaced-demo >/dev/null
    problems=""
    [[ "$EXPANDED_IDS" == "replaced-demo keep-demo successor-demo" ]] \
        || problems+="[expanded selection [$EXPANDED_IDS]] "
    # The tombstone row stays: it is what still reports "skipped: retired", and
    # the one muted line is what tells the reader the work did not vanish.
    [[ "${EXPANDED_LINES[0]:-<none>}" == "replaced-demo is retired: replaced by keep-demo, successor-demo" ]] \
        || problems+="[announced [${EXPANDED_LINES[0]:-<none>}]] "
    [[ ${#EXPANDED_LINES[@]} -eq 1 ]] || problems+="[${#EXPANDED_LINES[@]} lines announced] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog loading: expansion names each retired id once and adds nothing twice"; then
    # A state file written after one replay already lists the replacements; a
    # second replay must not plan them twice, and untouched ids keep their place.
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    EXPANDED_IDS=""; EXPANDED_LINES=()
    catalog_expand_replacements keep-demo replaced-demo successor-demo prompted-demo >/dev/null
    problems=""
    [[ "$EXPANDED_IDS" == "keep-demo replaced-demo successor-demo prompted-demo" ]] \
        || problems+="[expanded selection [$EXPANDED_IDS]] "
    [[ ${#EXPANDED_LINES[@]} -eq 1 ]] || problems+="[${#EXPANDED_LINES[@]} lines announced] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog loading: expansion leaves an id with no replacement alone"; then
    # The guard that the new step is inert on the path every run already takes:
    # retired without successors, and ordinary ids, come back unchanged.
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    EXPANDED_IDS=""; EXPANDED_LINES=()
    catalog_expand_replacements retired-demo keep-demo prompted-demo >/dev/null
    problems=""
    [[ "$EXPANDED_IDS" == "retired-demo keep-demo prompted-demo" ]] \
        || problems+="[expanded selection [$EXPANDED_IDS]] "
    [[ ${#EXPANDED_LINES[@]} -eq 0 ]] || problems+="[announced: ${EXPANDED_LINES[*]}] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

# old_state_tree <json> — a scratch tree whose state file is what a machine
# wrote *before* the retirement: it names a retired id and none of its
# successors. Hand-authored rather than produced by a run, because the whole
# point is a file that predates the field.
old_state_tree() {
    local tree state
    tree="$(tombstone_tree)"
    state="$tree/state.json"
    printf '%s\n' "$1" > "$state"
    printf '%s\n' "$tree"
}

if it "from-state: replaying a state saved before the retirement plans the replacements"; then
    tree="$(old_state_tree '{"version":1,"platform":"linux","profile":"custom","selected":["replaced-demo"],"answers":{},"results":{}}')"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --from-state "$tree/state.json" --yes --no-color \
        --dry-run --save-state "$tree/state2.json" 2>&1)"; rc=$?
    planned="$(printf '%s\n' "$out" | grep -E '^ +[0-9]+\. ')"
    line="$(printf '%s\n' "$out" | grep 'is retired: replaced by' | head -1)"
    row="$(printf '%s\n' "$out" | grep -E '^ +[0-9]+\. Replaced Component' | head -1)"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$planned" == *"Successor Component"* ]] || problems+="[the successor is not planned: [$planned]] "
    [[ "$planned" == *"Kept Component"* ]] || problems+="[the first replacement is not planned: [$planned]] "
    [[ "$row" == *"(retired)"* ]] || problems+="[the tombstone row reads [$row]] "
    [[ "$line" == "replaced-demo is retired: replaced by keep-demo, successor-demo" ]] \
        || problems+="[announced as [$line]] "
    [[ "$out" == *"Kept Component done"* ]] || problems+="[the replacement was not installed: $(printf '%s\n' "$out" | tail -6)] "
    [[ "$out" == *"Successor Component done"* ]] || problems+="[the second replacement was not installed]"
    [[ "$out" == *"Replaced Component: skipped: retired"* ]] || problems+="[the tombstone reported no skip]"
    [[ -z "$problems" ]] && pass || fail "$problems"
    rm -rf "$tree"
fi

if it "from-state: a replay records the replacements as installed and the tombstone as skipped"; then
    # No --dry-run here: a dry run only announces the state file, and the
    # recorded buckets are what a second replay reads back. Nothing is touched
    # either way — every component the plan names is a `custom` one whose work
    # is a post-install step the fixture does not carry.
    tree="$(old_state_tree '{"version":1,"platform":"linux","profile":"custom","selected":["replaced-demo"],"answers":{},"results":{}}')"
    HOME="$tree/home" bash "$tree/setup.sh" --from-state "$tree/state.json" --yes --no-color \
        --save-state "$tree/state2.json" >/dev/null 2>&1
    saved="$(tombstone_state_results "$tree/state2.json")"
    again="$(HOME="$tree/home" bash "$tree/setup.sh" --from-state "$tree/state2.json" --yes --no-color \
        --dry-run 2>&1)"; rc=$?
    second="$(printf '%s\n' "$again" | grep -E '^ +[0-9]+\. ')"
    rm -rf "$tree"
    problems=""
    [[ "$saved" == "installed=[keep-demo,successor-demo] skipped=[replaced-demo] failed=[] answers={}" ]] \
        || problems+="[the state file says: $saved] "
    # Replaying the replay: the saved selection now lists the successors itself,
    # so the tombstone adds nothing and each row appears exactly once.
    (( rc == 0 )) || problems+="[second replay rc=$rc] "
    [[ "$(printf '%s\n' "$second" | grep -c 'Kept Component')" == "1" ]] \
        || problems+="[the second replay planned Kept Component twice: $second] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "end-to-end: --only a retired id plans the ids in its replaced_by"; then
    # --serve/-Send reaches setup.sh as --only, so the browser's saved selection
    # replays through this same expansion; profiles never name a tombstone.
    tree="$(tombstone_tree)"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only replaced-demo --yes --no-color --dry-run 2>&1)"; rc=$?
    rm -rf "$tree"
    planned="$(printf '%s\n' "$out" | grep -E '^ +[0-9]+\. ')"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$planned" == *"Successor Component"* && "$planned" == *"Kept Component"* ]] \
        || problems+="[--only planned only: [$planned]] "
    [[ "$out" == *"replaced-demo is retired: replaced by keep-demo, successor-demo"* ]] \
        || problems+="[nothing announced the substitution]"
    [[ "$out" != *"Unknown component"* ]] || problems+="[--only refused a known id]"
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog tombstone: the serve payload carries replaced_by"; then
    # The page draws the retired row from the payload; the fact of what took the
    # work over has to cross with it, the way `tombstone` and `note` do.
    failures="$(python3 - "$TOMBSTONE_FIXTURE" <<'PY' 2>&1
import json, sys
sys.path.insert(0, "lib/linux")
from serve import state_components

catalog = json.load(open(sys.argv[1], encoding="utf-8"))
comps = {c["id"]: c for c in state_components(catalog, "linux", {}, "x64", False, {})}
bad = []
rep = comps.get("replaced-demo") or {}
if rep.get("replaced_by") != ["keep-demo", "successor-demo"]:
    bad.append("replaced_by is %r" % (rep.get("replaced_by"),))
tomb = comps.get("retired-demo") or {}
if tomb.get("replaced_by") != []:
    bad.append("a tombstone with no replacements says %r" % (tomb.get("replaced_by"),))
keep = comps.get("keep-demo") or {}
if "replaced_by" not in keep:
    bad.append("an ordinary entry has no replaced_by key at all")
print("; ".join(bad))
PY
)"
    assert_eq "$failures" ""
fi

# ─── the terminal menu: a retired row is shown, locked, and explains itself ──
# The row decision has one home on each platform: catalog_menu_rows here,
# New-AutoOSMenuItem on Windows. The cases below ask it directly, then through
# the real ui_menu key handlers, then through setup.sh driving a menu on a pty —
# the path production takes, and the only one that can prove a hand-ticked
# tombstone never reaches a plan without the ids that took its work over.

# manual_tombstone_py — source for a fixture variant that retires an entry which
# also carries the manual provider. That shape is what the guard order gets
# wrong: the provider guard answers "AutoOS cannot install this" about a row that
# is retired and installs nothing by design, so the run fails where it should
# report a skip.
manual_tombstone_py='
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for g in d["categories"]:
    for c in g["components"]:
        if c["id"] == "retired-demo":
            c["provider"] = "manual"
            c["notes"] = "Fixture manual provider; nothing to download."
json.dump(d, open(p, "w"))
'

if it "catalog tombstone: a menu row for a retired id is locked and names its replacements"; then
    catalog_load "$TOMBSTONE_FIXTURE" x64 0
    catalog_menu_rows keep-demo
    r="$(catalog_index_of replaced-demo)"
    t="$(catalog_index_of retired-demo)"
    k="$(catalog_index_of keep-demo)"
    problems=""
    # A retired row is shown — the id is public and someone may remember the
    # product — but it cannot be chosen, and it says why in the same breath.
    [[ "${MENU_DISABLED[r]:-<missing>}" == 1 ]] || problems+="[a retired row can be ticked] "
    [[ "${MENU_DESC[r]:-}" == *"(retired: replaced by keep-demo, successor-demo)"* ]] \
        || problems+="[the row reads [${MENU_DESC[r]:-<missing>}]] "
    [[ "${MENU_DISABLED[t]:-<missing>}" == 1 ]] || problems+="[a retired row with no replacements can be ticked] "
    [[ "${MENU_DESC[t]:-}" == *"(retired)"* ]] || problems+="[the row reads [${MENU_DESC[t]:-<missing>}]] "
    # The guard that the new lock is not a blanket: an ordinary row stays tickable
    # and keeps the pre-tick list the caller handed in.
    [[ "${MENU_DISABLED[k]:-<missing>}" == 0 ]] || problems+="[an ordinary row was disabled] "
    [[ "${MENU_SEL[k]:-<missing>}" == 1 ]] || problems+="[the pre-tick list was not honoured] "
    [[ "${MENU_SEL[r]:-<missing>}" == 0 ]] || problems+="[a retired row was pre-ticked] "
    [[ "${#MENU_ID[@]}" == "${#CAT_ID[@]}" && "${#MENU_DISABLED[@]}" == "${#MENU_ID[@]}" \
       && "${#MENU_SEL[@]}" == "${#MENU_ID[@]}" ]] \
        || problems+="[rows and flags came apart: ${#MENU_ID[@]} ids, ${#MENU_DISABLED[@]} flags, ${#MENU_SEL[@]} selections] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog tombstone: the menu's own key handlers cannot tick a retired row"; then
    # ui_menu decides what a key does; the row arrays decide what is tickable.
    # Both are the real code, run in a subshell so the shared arrays stay clean.
    (
        ui_is_interactive() { return 0; }
        catalog_load "$TOMBSTONE_FIXTURE" x64 0
        catalog_menu_rows ""
        ui_menu "Choose what to install" "" < <(printf 'a\n') >/dev/null 2>&1 || exit 1
        [[ "$MENU_RESULT" == *keep-demo* && "$MENU_RESULT" == *prompted-demo* ]] || exit 2
        [[ "$MENU_RESULT" != *replaced-demo* ]] || exit 3
        [[ "$MENU_RESULT" != *retired-demo* ]] || exit 4
        exit 0
    ); rc=$?
    case "$rc" in
        0) pass ;;
        1) fail "ui_menu cancelled instead of returning a selection" ;;
        2) fail "select-all skipped an ordinary row: [$MENU_RESULT]" ;;
        3|4) fail "select-all handed a retired id to the selection: [$MENU_RESULT]" ;;
        *) fail "ui_menu or the row builder exited rc=$rc" ;;
    esac
fi

if it "catalog tombstone: the interactive menu never hands a retired id to the plan"; then
    # The review finding, end to end: a person who highlights the retired row and
    # presses space used to get a plan with the skip row and none of the work that
    # replaced it. Nothing about this is reachable without a terminal, so the case
    # opens one; where the host has no script(1) it is a skip, never a pass.
    #
    # Keystrokes go through a FIFO this shell holds open (fd 8), and only after the
    # render loop's own output is seen — not piped in blind up front. A pipe that
    # closes (EOF) the instant printf finishes can race setup.sh still sourcing its
    # libraries before the read loop is up, and CI does not guarantee the same pty
    # buffering a bare-metal kernel gives locally (CI run 36402165598: rc=124,
    # killed after 90s still blocked on the very first read; the footer text in the
    # capture proved the loop was reached but never got a byte).
    if ! command -v script >/dev/null 2>&1; then skip "no script(1) to open a pty"
    else
        tree="$(tombstone_tree)"
        catalog_load "$TOMBSTONE_FIXTURE" x64 0
        keys=""
        for ((j = 0; j < "$(catalog_index_of replaced-demo)"; j++)); do keys+="j"; done
        fifo="$tree/keys.fifo" capture="$tree/capture.txt"
        mkfifo "$fifo"; : > "$capture"
        timeout 90 script -qec "bash '$tree/setup.sh' --profile custom --no-color --dry-run" /dev/null \
            <"$fifo" >"$capture" 2>&1 &
        pty_pid=$!
        # Launch first, then open the write end: opening a FIFO for writing blocks
        # until a reader exists, and script is the reader — it blocks in its own
        # open of the read end until we do. fd 8 stays open either way, so script's
        # stdin cannot EOF until the exec 8>&- below.
        exec 8>"$fifo"
        ready=0
        for ((i = 0; i < 300; i++)); do
            grep -q "Choose what to install" "$capture" 2>/dev/null && { ready=1; break; }
            kill -0 "$pty_pid" 2>/dev/null || break
            sleep 0.1
        done
        if (( ready )); then printf '%s \n' "$keys" >&8; fi
        exec 8>&-        # EOF now, only once the reader is proven ready (or dead)
        wait "$pty_pid"; rc=$?
        out="$(cat "$capture")"
        plain="$(printf '%s' "$out" | tr -d '\r' | sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g')"
        rm -rf "$tree"
        if (( ! ready )) || [[ "$plain" != *"Choose what to install"* ]]; then
            # The terminal was opened but nothing drew the menu in it — a host
            # limitation, not a verdict on the code.
            skip "the pty run never reached the menu (rc=$rc): $(printf '%s\n' "$plain" | tail -2)"
        else
            planned="$(printf '%s\n' "$plain" | grep -E '^ +[0-9]+\. ' || true)"
            problems=""
            # Locked and labelled, exactly as the review asked the row to read.
            [[ "$plain" == *"[-] Replaced Component"* ]] \
                || problems+="[the retired row is not shown locked: $(printf '%s\n' "$plain" | grep 'Replaced Component' | head -1 | sed 's/^ *//')] "
            [[ "$plain" == *"(retired: replaced by keep-demo, successor-demo)"* ]] \
                || problems+="[the retired row does not name its replacements] "
            (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$plain" | tail -3)] "
            [[ "$planned" != *"Replaced Component"* ]] \
                || problems+="[the menu planned a retired id with nothing to take its work: $planned] "
            [[ -z "$problems" ]] && pass || fail "$problems"
        fi
    fi
fi

if it "catalog tombstone: a retired manual-provider id resolves rather than refusing"; then
    # The guard order the review calls LOW: resolve asks "can AutoOS install this
    # provider?" before "is this row retired?", so a retired manual entry aborts
    # the whole run over a component that installs nothing either way.
    f="$(fixture_variant "$manual_tombstone_py")"
    catalog_load "$f" x64 0
    out="$(catalog_resolve retired-demo 2>&1)"; rc=$?
    problems=""
    (( rc == 0 )) || problems+="[resolve refused a retired id: $out] "
    [[ " $PLAN_IDS " == *" retired-demo "* ]] || problems+="[the retired row is not in the plan: [$PLAN_IDS]] "
    [[ -z "$(catalog_resolve_blocked retired-demo)" ]] \
        || problems+="[it was refused instead: $(catalog_resolve_blocked retired-demo)] "
    rm -f "$f"
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog tombstone: --only a retired manual-provider id reports skipped retired"; then
    # Through the entry point, because it is the run the user sees: the retired
    # row is skipped, the vendor instruction never fires for a row that retired,
    # and the report has no failure to explain.
    tree="$(tombstone_tree "$manual_tombstone_py")"
    out="$(HOME="$tree/home" bash "$tree/setup.sh" --only retired-demo --yes --no-color --dry-run 2>&1)"; rc=$?
    rm -rf "$tree"
    problems=""
    (( rc == 0 )) || problems+="[rc=$rc: $(printf '%s\n' "$out" | tail -3)] "
    [[ "$out" == *"skipped: retired"* ]] || problems+="[no skip line: $(printf '%s\n' "$out" | tail -6)] "
    [[ "$out" != *"AutoOS cannot install"* ]] || problems+="[the provider guard fired for a retired id]"
    # The report always carries an "Action required" count; what must not appear is
    # the instruction itself, naming the vendor link of a row that retired.
    [[ "$out" != *"Action required: http"* ]] || problems+="[the plan asked the user to install the retired row by hand]"
    failed="$(printf '%s\n' "$out" | grep -E '^ +Failed' | head -1 | awk '{print $2}')"
    [[ "$failed" == "0" ]] || problems+="[the report counts ${failed:-no} failure(s): $(printf '%s\n' "$out" | tail -4)] "
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "catalog tombstone: a locked row cannot leave the menu, terminal or not"; then
    # The key handlers honour MENU_DISABLED; the selector's non-interactive
    # fallback used to read only MENU_SEL, so a row the caller marked unchosen
    # could still reach the plan when nobody was at a keyboard. Same contract,
    # both branches.
    (
        ui_is_interactive() { return 1; }
        catalog_load "$TOMBSTONE_FIXTURE" x64 0
        catalog_menu_rows keep-demo
        # Simulate any route that handed the selector a ticked retired row — the
        # lock is what has to stop it, not the tick.
        r="$(catalog_index_of replaced-demo)"
        MENU_SEL[r]=1
        ui_menu "Choose what to install" "" >/dev/null 2>&1 || exit 1
        [[ "$MENU_RESULT" == *keep-demo* ]] || exit 2
        [[ "$MENU_RESULT" != *replaced-demo* ]] || exit 3
        exit 0
    ); rc=$?
    case "$rc" in
        0) pass ;;
        1) fail "the fallback selector returned an error" ;;
        2) fail "the fallback lost the pre-ticked row: [$MENU_RESULT]" ;;
        3) fail "a locked row reached the selection: [$MENU_RESULT]" ;;
        *) fail "the fallback selector exited rc=$rc" ;;
    esac
fi
