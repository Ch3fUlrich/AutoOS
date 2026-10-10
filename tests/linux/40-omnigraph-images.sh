# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── SPEC-OMNI A6: the GHCR viewer-image workflow ───────────────────────────
# The canonical image build moved off the LAN (operator 2026-09-28): GitHub
# Actions on a GitHub-hosted runner pushes SHA-tagged images to GHCR with the
# automatic GITHUB_TOKEN. These cases pin the properties that make that safe —
# SHA tags only (a floating tag makes a deployed image untraceable to its
# commit), a narrow path trigger (every push would be a needless registry
# write), and no operator-supplied registry secret on this path.
describe "omnigraph images workflow"

OMNI_WF="$ROOT/.github/workflows/omnigraph-images.yml"

if it "omnigraph images: the workflow file exists"; then
    [[ -f "$OMNI_WF" ]] && pass || fail "missing $OMNI_WF"
fi

if it "omnigraph images: the workflow parses as YAML"; then
    # PyYAML is deliberately not a suite dependency, so probe for a python that
    # has it, then fall back to yamllint (which CI installs and runs over
    # .github/). A parse failure must fail loudly; only a machine with neither
    # skips, and CI always has yamllint.
    omni_py=""
    for omni_candidate in python3 /usr/bin/python3; do
        command -v "$omni_candidate" >/dev/null 2>&1 || continue
        if "$omni_candidate" -c 'import yaml' 2>/dev/null; then omni_py="$omni_candidate"; break; fi
    done
    if [[ -n "$omni_py" ]]; then
        out="$("$omni_py" - "$OMNI_WF" 2>&1 <<'PY'
import sys
import yaml
with open(sys.argv[1], encoding="utf-8") as fh:
    yaml.safe_load(fh)
PY
)"; rc=$?
        [[ $rc -eq 0 ]] && pass || fail "PyYAML rejected it: $(printf '%s\n' "$out" | head -3)"
    elif has_cmd yamllint; then
        out="$(yamllint -d '{extends: relaxed, rules: {line-length: {max: 140}}}' "$OMNI_WF" 2>&1)" \
            && pass || fail "$(printf '%s\n' "$out" | head -5)"
    else
        skip "no PyYAML and no yamllint"
    fi
fi

if it "omnigraph images: the tag is the git SHA on ghcr.io, never a floating tag"; then
    problems=""
    if ! grep -qF 'ghcr.io/${{ steps.owner.outputs.lc }}/omnigraph-viewer:${{ github.sha }}' "$OMNI_WF"; then
        problems+=" no ghcr.io/<owner>/omnigraph-viewer:<sha> tag;"
    fi
    # `latest` (or any moving tag) must never be an image tag: a deployed image
    # has to stay traceable to the commit that built it. `ubuntu-latest` is the
    # runner label, not a tag, so match a tag position (`:latest`) and the
    # workflow's own `tags:` line rather than the bare word.
    if grep -qE '^[[:space:]]*tags:' "$OMNI_WF" \
        && grep -E '^[[:space:]]*tags:' "$OMNI_WF" | grep -qi 'latest'; then
        problems+=" the tags: line references a floating tag;"
    fi
    if grep -qE ':latest([^a-zA-Z0-9_-]|$)' "$OMNI_WF"; then
        problems+=" a :latest image tag is referenced;"
    fi
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "omnigraph images: the owner is lowercased before it reaches the tag (GHCR requirement)"; then
    problems=""
    grep -qE '\$\{GITHUB_REPOSITORY_OWNER,,\}' "$OMNI_WF" \
        || problems+=" no lowercase transform of GITHUB_REPOSITORY_OWNER;"
    grep -qF 'ghcr.io/${{ github.repository_owner }}' "$OMNI_WF" \
        && problems+=" the raw (possibly mixed-case) owner is still used in a tag;"
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "omnigraph images: publishing is main-only; a branch build never pushes"; then
    problems=""
    if ! grep -qF "github.ref == 'refs/heads/main'" "$OMNI_WF"; then
        problems+=" no main-only gate found;"
    fi
    # The build-push-action's own push: must be gated, not a bare 'true' -- a
    # bare true is exactly the bug (CI 36440796992) that published from a lane branch.
    if grep -qE '^[[:space:]]*push:[[:space:]]*true[[:space:]]*$' "$OMNI_WF"; then
        problems+=" push: true is unconditional;"
    fi
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "omnigraph images: it triggers only on the viewer paths and this workflow file"; then
    problems=""
    grep -qE '^[[:space:]]*push:' "$OMNI_WF" || problems+=" no push trigger;"
    grep -qF 'infra/mcp-servers/servers/omnigraph-viewer/**' "$OMNI_WF" \
        || problems+=" the viewer path filter is missing;"
    grep -qF '.github/workflows/omnigraph-images.yml' "$OMNI_WF" \
        || problems+=" the workflow's own path filter is missing;"
    # An automatic trigger outside the intended paths (PR/schedule/run) would
    # fire the build — and a registry write — where it is not wanted.
    if grep -qE '^[[:space:]]*(pull_request|schedule|workflow_run|release):' "$OMNI_WF"; then
        problems+=" an unintended trigger is present;"
    fi
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "omnigraph images: packages: write via the automatic GITHUB_TOKEN, no operator registry secret"; then
    problems=""
    grep -qE '^[[:space:]]*packages:[[:space:]]*write' "$OMNI_WF" || problems+=" packages: write is missing;"
    grep -qE '^[[:space:]]*contents:[[:space:]]*read' "$OMNI_WF" || problems+=" contents: read is missing;"
    grep -qF 'secrets.GITHUB_TOKEN' "$OMNI_WF" || problems+=" GITHUB_TOKEN is not used;"
    # Harbor/Forgejo are for local test builds; no operator registry credential
    # belongs on the tracked GHCR path.
    if grep -qE 'secrets\.(REGISTRY|HARBOR|FORGEJO)' "$OMNI_WF"; then
        problems+=" an operator registry secret is referenced;"
    fi
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

# ─── Omnigraph cluster declaration (SPEC-OMNI A6) ───────────────────────────
# The graph list names repositories, so the live cluster.yaml must never be
# tracked; the example and its schema must be, or the shape is undocumented.
describe "omnigraph cluster declaration"

if it "omnigraph cluster: cluster.yaml is gitignored while the example + schema are tracked"; then
    problems=""
    if ! git -C "$ROOT" check-ignore -q -- infra/mcp-servers/cluster/cluster.yaml; then
        problems+=" cluster.yaml is not gitignored;"
    fi
    if git -C "$ROOT" check-ignore -q -- infra/mcp-servers/cluster/cluster.yaml.example; then
        problems+=" cluster.yaml.example is gitignored too;"
    fi
    [[ -f "$ROOT/infra/mcp-servers/cluster/cluster.yaml.example" ]] \
        || problems+=" cluster.yaml.example is missing;"
    [[ -f "$ROOT/infra/mcp-servers/cluster/cluster.schema.json" ]] \
        || problems+=" cluster.schema.json is missing;"
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "omnigraph cluster: the schema is valid JSON and describes the example's shape"; then
    if ! has_cmd python3; then
        skip "no python3 to check the schema"
    else
        out="$(python3 - "$ROOT/infra/mcp-servers/cluster/cluster.schema.json" <<'PY' 2>&1
import json
import sys
schema = json.load(open(sys.argv[1], encoding="utf-8"))
required = set(schema.get("required", []))
missing = {"version", "metadata", "storage", "state", "graphs"} - required
if missing:
    print("schema is missing required keys: %s" % ", ".join(sorted(missing)))
    raise SystemExit(1)
if not isinstance(schema.get("properties"), dict) or "graphs" not in schema["properties"]:
    print("schema does not describe the graphs map")
    raise SystemExit(1)
PY
)"; rc=$?
        [[ $rc -eq 0 ]] && pass || fail "$(printf '%s\n' "$out" | head -3)"
    fi
fi

# ─── Fleet rule D-825: apply-cluster.sh converges the live store only on a GO ──
# apply-cluster.sh has no read-only mode — every run applies cluster/ to the live
# MinIO-backed omnigraph store and restarts the server — so it must refuse without
# --go <ref> and --go-sha <sha> equal to this checkout's HEAD. The refusals exit at
# the very top, before the cd/docker/snapshot, so they touch nothing; the happy path
# is exercised here only against a throwaway tree (no live store exists in the suite).
# D-852 (2026-10-09): and every case below runs with the stand-in binaries first on
# PATH, so a gate that ever stops refusing mid-run meets a recording stub and a
# closed port instead of `docker stop omnigraph-server` on the host. See
# gate_stubs_make / gate_stub_run in tests/run-tests.sh.
describe "omnigraph cluster apply gate"

APC_SH="$ROOT/infra/mcp-servers/scripts/apply-cluster.sh"

if it "apply-cluster: refuses to converge with no --go and touches nothing"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$( ( cd "$d" && gate_stub_run "$d" -- bash "$APC_SH" ) 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a --go-less converge exited 0" >&2; }
    [[ "$out" == *"refusing to converge the live omnigraph store without --go"* ]] \
        || { ok=0; echo "no D-825 refusal: $out" >&2; }
    [[ "$out" != *"GO:"* ]] || { ok=0; echo "printed a GO line on a refused run: $out" >&2; }
    [[ ! -e "$d/.graph-backup" ]] || { ok=0; echo "the refused run wrote a backup dir" >&2; }
    [[ ! -s "$d/stub/calls.log" ]] || { ok=0; echo "the refused run reached a host binary: $(cat "$d/stub/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-cluster converged the live store with no --go"; fi
fi

if it "apply-cluster: refuses a malformed --go reference"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    sha="$(git -C "$ROOT" rev-parse HEAD)"
    out="$(gate_stub_run "$d" -- bash "$APC_SH" --go "not-a-ref" --go-sha "$sha" 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a malformed --go reference exited 0" >&2; }
    [[ "$out" == *"is not a judge run id"* ]] || { ok=0; echo "no ref-format refusal: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-cluster accepted a malformed --go"; fi
fi

if it "apply-cluster: refuses when --go-sha is not this checkout's HEAD"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- bash "$APC_SH" --go D-825 --go-sha 0000000000000000000000000000000000000000 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a wrong --go-sha exited 0" >&2; }
    [[ "$out" == *"is not this checkout's HEAD"* ]] || { ok=0; echo "no sha-mismatch refusal: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-cluster converged on a --go-sha that is not HEAD"; fi
fi

if it "apply-cluster: --go=OS-0 with a non-HEAD --go-sha= still refuses"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- bash "$APC_SH" --go=OS-0 --go-sha=abcdef 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] && pass \
        || fail "the =-form args were not gated: rc=$rc out=$out"
fi

# ─── The same D-825 gate on the Python live-store tools (dedup / populate / split) ──
# They mutate the running omnigraph store (a reset + overwrite-load, an overwrite-load,
# or a merge-load / node delete), so like apply-cluster.sh a mutating run refuses without
# --go/--go-sha. D-852: the gate is no longer the only thing that keeps a case off the
# host — every case here runs against a fresh temp dir whose stand-in docker/curl/systemctl
# record the call instead of making it, and the cases that CLEAR the gate assert the
# record, because "the gate let it through" is only proven by what it then reached.
OG_PY="$ROOT/infra/mcp-servers/scripts"
_head() { git -C "$ROOT" rev-parse HEAD; }

if it "D-825: dedup-graph refuses a live dedup with no --go and touches nothing"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/dedup-graph.py" 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a --go-less dedup exited 0" >&2; }
    [[ "$out" == *"refusing to dedup the live omnigraph store without --go"* ]] \
        || { ok=0; echo "no D-825 refusal: $out" >&2; }
    [[ "$out" != *"GO:"* ]] || { ok=0; echo "printed a GO line on a refused run: $out" >&2; }
    [[ ! -s "$d/stub/calls.log" ]] || { ok=0; echo "the refused run reached a host binary: $(cat "$d/stub/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "dedup-graph deduped with no --go"; fi
fi

if it "D-825: dedup-graph refuses a malformed --go reference" ; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/dedup-graph.py" --go not-a-ref --go-sha "$(_head)" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not a judge run id"* ]] && pass \
        || fail "a malformed --go reference was accepted: rc=$rc out=$out"
fi

if it "D-825: dedup-graph refuses when --go-sha is not this checkout's HEAD"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/dedup-graph.py" --go D-825 --go-sha 0000000000000000000000000000000000000000 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] && pass \
        || fail "a wrong --go-sha was accepted: rc=$rc out=$out"
fi

if it "D-825: dedup-graph --dry-run needs no --go and is unchanged"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/dedup-graph.py" --dry-run 2>&1)"; rc=$?
    [[ "$out" != *"refusing to dedup the live omnigraph store"* ]] && pass \
        || fail "the dry run hit the D-825 gate: $out"
fi

if it "D-825: dedup-graph a valid --go + --go-sha echoes GO first and proceeds"; then
    # A CLEARED gate hands the script to the live stack next, so this is the case the
    # 2026-10-09 incident turned on: the GO line proves the gate opened, and the stand-in
    # log proves the run that followed it talked to a stub, never to docker on the host.
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/dedup-graph.py" --go D-825 --go-sha "$(_head)" 2>&1)"; rc=$?
    first_line="$(printf '%s\n' "$out" | head -1)"
    ok=1
    [[ "$first_line" == "GO: D-825 sha=$(_head)" ]] \
        || { ok=0; echo "the GO line was not first or the gate refused: [$first_line]" >&2; }
    gate_stub_saw "$d/stub" docker || { ok=0; echo "the cleared run never reached a stand-in binary: $(cat "$d/stub/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "a valid GO did not reach the stand-in stack"; fi
fi

if it "D-825: populate-embeddings refuses an overwrite-load with no --go"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/populate-embeddings.py" --seeds x.jsonl 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"refusing to overwrite-load the live omnigraph graph without --go"* ]] \
        && pass || fail "a --go-less overwrite-load was not refused: rc=$rc out=$out"
fi

if it "D-825: populate-embeddings --no-load needs no --go and is unchanged"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/populate-embeddings.py" --seeds x.jsonl --no-load 2>&1)"; rc=$?
    [[ "$out" != *"refusing to overwrite-load the live omnigraph graph"* ]] && pass \
        || fail "--no-load hit the D-825 gate: $out"
fi

if it "D-825: split-project-graph refuses --apply with no --go"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/split-project-graph.py" some-project --apply 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"refusing to write to the live omnigraph graph without --go"* ]] \
        && pass || fail "--apply was not refused: rc=$rc out=$out"
fi

if it "D-825: split-project-graph a dry run (no --apply) needs no --go and is unchanged"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/split-project-graph.py" some-project 2>&1)"; rc=$?
    [[ "$out" != *"refusing to write to the live omnigraph graph"* ]] && pass \
        || fail "a dry run hit the D-825 gate: $out"
fi

if it "D-825: compact-graphs.ps1 gate is present (refuses without -Go)"; then
    # The pwsh suite owns the full battery; the bash shard only proves the gate exists and
    # fires, so a Windows host that never runs pwsh still guards against the gate vanishing.
    command -v pwsh >/dev/null || { skip "no pwsh on this host"; }
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- pwsh -NoProfile -NonInteractive -File "$OG_PY/compact-graphs.ps1" 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a no-Go compact-graphs run exited 0" >&2; }
    [[ "$out" == *"refusing to compact the live omnigraph store without -Go"* ]] \
        || { ok=0; echo "no D-825 refusal: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "compact-graphs.ps1 ran live without a -Go: rc=$rc out=$out"; fi
fi

# ─── Fleet rule D-825 part 2: the ref must name an artefact that EXISTS ─────────
# A judge run id, a D-<n> decision, an OS-<n> item: any of them matches the regex, and
# matching the regex used to be the whole check, so `--go D-1` opened the gate. The
# lookup now lives in _go_gate.py — one implementation, imported by the Python tools and
# execed by apply.sh / apply-cluster.sh / the PowerShell twins (invoke-go-gate.ps1) — so
# these cases prove the shared verdict, and the 34-ai-services.sh battery proves the
# bash gate reaches it. Every case here stays on the REFUSAL side: passing the gate of a
# live-store tool would mean a real docker call, and there is no stack in the suite.
if it "D-825: the ref-existence gate unit tests (python3 tests/test_go_gate_ref.py)"; then
    out="$(python3 tests/test_go_gate_ref.py 2>&1)" && pass \
        || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "D-1038: agent-skills seed has no data.id, 0.13 refuses it (python3 tests/test_agent_skills_seed.py)"; then
    out="$(python3 tests/test_agent_skills_seed.py 2>&1)" && pass \
        || fail "$(printf '%s\n' "$out" | tail -n 20)"
fi

if it "D-825: dedup-graph refuses a --go that names no decision, exact id only"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    printf '| D-825 | all L1s | only an explicit GO counts | router |\n' >"$d/log.md"
    ok=1
    for ref in D-9999 D-82; do
        out="$(gate_stub_run "$d" "AUTOOS_DECISIONS_LOG=$d/log.md" -- \
            python3 "$OG_PY/dedup-graph.py" --go "$ref" --go-sha "$(_head)" 2>&1)"; rc=$?
        [[ $rc -ne 0 ]] || { ok=0; echo "--go $ref cleared the gate: $out" >&2; }
        [[ "$out" == *"names no decision"* ]] || { ok=0; echo "$ref: no existence refusal: $out" >&2; }
        [[ "$out" != *"GO:"* ]] || { ok=0; echo "$ref: printed a GO line for nothing: $out" >&2; }
    done
    [[ ! -s "$d/stub/calls.log" ]] \
        || { ok=0; echo "a refused run reached a host binary: $(cat "$d/stub/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "dedup-graph accepted a ref that names no artefact"; fi
fi

if it "D-825: dedup-graph refuses when the decisions log cannot be read"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" "AUTOOS_DECISIONS_LOG=$d/no-such-dir/log.md" -- \
        python3 "$OG_PY/dedup-graph.py" --go D-825 --go-sha "$(_head)" 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "an unreadable source cleared the gate: $out" >&2; }
    [[ "$out" == *"cannot read the routing decisions log"* ]] \
        || { ok=0; echo "no unreadable-source refusal: $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "dedup-graph deduped on an unverifiable GO"; fi
fi

if it "D-825: dedup-graph --go-offline still refuses a bad --go-sha"; then
    # The offline flag waives the artefact lookup, never the sha the GO names — and the
    # refusal on the sha is what keeps this case safe to run with no stack at all.
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- python3 "$OG_PY/dedup-graph.py" --go D-825 \
        --go-sha 0000000000000000000000000000000000000000 --go-offline 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] && pass \
        || fail "--go-offline waived the sha bar: rc=$rc out=$out"
fi

if it "D-825: apply-cluster refuses a --go that names no artefact, unknown flags still rc 2"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    ok=1
    out="$(gate_stub_run "$d" -- bash "$APC_SH" --go D-9999 --go-sha "$(_head)" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"names no decision"* ]] \
        || { ok=0; echo "apply-cluster converged on a ref that names nothing: rc=$rc out=$out" >&2; }
    # The fixture tree (tests/fixtures/go-gate, exported by run-tests.sh) holds D-825:
    # an id that IS there must not be refused, or the suite's own mutating cases break.
    out="$(gate_stub_run "$d" -- bash "$APC_SH" --go D-82 --go-sha "$(_head)" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"names no decision"* ]] \
        || { ok=0; echo "D-82 matched the fixture's D-825 line: rc=$rc out=$out" >&2; }
    out="$(gate_stub_run "$d" -- bash "$APC_SH" --bogus 2>&1)"; rc=$?
    [[ $rc -eq 2 && "$out" == *"unknown argument '--bogus'"* ]] \
        || { ok=0; echo "an unknown argument was not refused: rc=$rc out=$out" >&2; }
    out="$(gate_stub_run "$d" -- bash "$APC_SH" --go D-825 --go-sha 0000000000000000000000000000000000000000 --go-offline 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] \
        || { ok=0; echo "--go-offline waived the sha bar or was an unknown flag: rc=$rc out=$out" >&2; }
    [[ ! -s "$d/stub/calls.log" ]] \
        || { ok=0; echo "a refused run reached a host binary: $(cat "$d/stub/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-cluster.sh accepted an unverifiable --go"; fi
fi

# ─── Fleet rule D-852: the stand-in directory is what these cases really run on ──
# Every case above clears or refuses a gate, and a cleared gate goes on to exec
# docker/systemctl/curl/ssh off PATH. If the stand-in dir ever stops being first —
# a case that forgot gate_stubs_make, a PATH rebuilt from the host's own, a stub
# that lost its shebang — the SAME case would silently reach the host's live stack
# and still print a green tick, which is how omnigraph-server got restarted on
# 2026-10-09. So the shadow itself is a tested property, for every binary and both
# shells, and the call record is checked to be the stub's own.
if it "D-825: guard (D-852) every host binary a gate tool execs resolves inside the stand-in dir"; then
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    problems=""
    for b in $GATE_STUB_ALL_BINS; do
        resolved="$(PATH="$d/bin:$d/stub:$PATH"; command -v "$b")"
        [[ -n "$resolved" ]] || { problems+=" $b:nothing-on-path;"; continue; }
        [[ "$resolved" == "$d/stub/$b" ]] || problems+=" $b:$resolved;"
    done
    # docker and systemctl are the two that restarted the live server; name them
    # explicitly so a regression says which one escaped, not just that one did.
    for b in docker systemctl; do
        [[ "$(PATH="$d/bin:$d/stub:$PATH"; command -v "$b")" == "$d/stub/$b" ]] \
            || problems+=" $b-outside-the-stand-in-dir;"
    done
    rm -rf "$d"
    [[ -z "$problems" ]] && pass || fail "resolved outside the stand-in dir: $problems"
fi

if it "D-825: guard (D-852) a stand-in records the call and never runs the host binary"; then
    # The record is the whole guard: a stub that shadows PATH but stays silent
    # cannot prove a cleared gate stopped there, and one that falls through to
    # `exec` would restart the stack while still logging.
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub" "$GATE_STUB_HOST_BINS"
    problems=""
    for b in $GATE_STUB_HOST_BINS; do
        out="$(gate_stub_run "$d" -- "$b" --pretend-argument 2>&1)"; rc=$?
        [[ $rc -eq 0 ]] || problems+=" $b:exited-$rc;"
        [[ -z "$out" ]] || problems+=" $b:printed [$out];"
        gate_stub_saw "$d/stub" "$b" || problems+=" $b:not-recorded;"
    done
    grep -q -- "--pretend-argument" "$d/stub/calls.log" \
        || problems+=" the record lost the arguments;"
    # The host's real docker answers `inspect` with a StartedAt; the stub answers
    # nothing at all. Asking for the live container is the direct test of the
    # incident: it must produce no version string and no container.
    real="$(PATH="$d/bin:$d/stub:$PATH"; docker inspect -f '{{.State.StartedAt}}' omnigraph-server 2>&1)"
    [[ "$real" != *"20"* ]] || problems+=" the stub answered with a real container: $real;"
    rm -rf "$d"
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "D-825: guard (D-852) the gateway URL env points at a closed port, never a live gateway"; then
    problems=""
    for name in AUTOOS_OMNIROUTE_URL OMNIROUTE_BASE_URL AUTOOS_OMNIGRAPH_URL OMNIGRAPH_URL OMNI_S3; do
        got="$(gate_stub_run /nonexistent-standin-dir -- printenv "$name")"
        [[ "$got" == "$GATE_URL_SINK" ]] || problems+=" $name=[$got];"
    done
    # A case that passes its own stand-in overrides the sink — that is how the
    # apply.sh battery talks to its loopback gateway — but never a host name.
    got="$(gate_stub_run /nonexistent-standin-dir "AUTOOS_OMNIROUTE_URL=http://127.0.0.1:20128" -- printenv AUTOOS_OMNIROUTE_URL)"
    [[ "$got" == "http://127.0.0.1:20128" ]] || problems+=" the per-case override did not win: [$got]"
    [[ "$GATE_URL_SINK" == "http://127.0.0.1:9" ]] || problems+=" the sink is not the discard port: $GATE_URL_SINK"
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "D-825: guard (D-852) pwsh resolves the stand-in dir first too (when pwsh exists)"; then
    command -v pwsh >/dev/null || { skip "no pwsh on this host"; }
    d="$(mktemp -d)"
    gate_stubs_make "$d/stub"
    out="$(gate_stub_run "$d" -- pwsh -NoProfile -NonInteractive -Command \
        'foreach ($b in @("docker","systemctl","curl","ssh","omniroute","npx","node")) { $c = Get-Command $b -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { "$b " + $c.Source } else { "$b missing" } }' 2>&1)"
    problems=""
    for b in $GATE_STUB_ALL_BINS; do
        line="$(printf '%s\n' "$out" | grep "^$b " | head -1)"
        [[ "$line" == "$b $d/stub/$b" ]] || problems+=" $b:[$([ -n "$line" ] && echo "${line#"$b "}" || echo none)];"
    done
    rm -rf "$d"
    [[ -z "$problems" ]] && pass || fail "PowerShell resolved outside the stand-in dir: $problems"
fi

# ─── D-1038 A6 (P7a): the Omnigraph 0.13 compose-wrapper scripts ─────────────
# Measured (server-L1 rehearsal 2026-10-10): 0.13 retains its cluster admission
# lock on EVERY shutdown, clean `docker stop` included, and the next boot dies
# with state_lock_held — so `restart: unless-stopped` crash-loops. start-server-013.sh
# and apply-fresh-013.sh release that exact id. They talk to the storage root on
# every path, so every case runs against a recording stand-in `omnigraph` that
# gate_stub_run puts first on PATH (<dir>/bin), never against a live root.
describe "omnigraph 0.13 compose wrappers"

OG13_START="$ROOT/infra/mcp-servers/cluster/start-server-013.sh"
OG13_APPLY="$ROOT/infra/mcp-servers/cluster/apply-fresh-013.sh"
OG13_ROOT="s3://omnigraph-013/cluster"

# og13_stub <dir> — stand-ins for `omnigraph` and the server entrypoint. Each call
# is appended to $STUB_LOG; behaviour comes from the env: STUB_STATUS_{OUT,RC},
# STUB_APPLY_{OUT,RC}, STUB_UNLOCK_RC (every rc defaults to 0).
og13_stub() {
    local _d="$1"
    mkdir -p "$_d/bin" "$_d/cluster"
    : >"$_d/calls.log"
    cat >"$_d/bin/omnigraph" <<'OG13'
#!/bin/sh
printf 'omnigraph %s\n' "$*" >>"$STUB_LOG"
case "$1 $2" in
    "cluster status")       printf '%s\n' "${STUB_STATUS_OUT:-}"; exit "${STUB_STATUS_RC:-0}" ;;
    "cluster apply")        printf '%s\n' "${STUB_APPLY_OUT:-}";  exit "${STUB_APPLY_RC:-0}" ;;
    "cluster force-unlock") exit "${STUB_UNLOCK_RC:-0}" ;;
esac
exit 0
OG13
    cat >"$_d/bin/omnigraph-entrypoint" <<'OG13'
#!/bin/sh
printf 'ENTRY %s\n' "$*" >>"$STUB_LOG"
exit 0
OG13
    chmod +x "$_d/bin/omnigraph" "$_d/bin/omnigraph-entrypoint"
}

og13_start() {
    local _d="$1"; shift
    gate_stub_run "$_d" "OMNIGRAPH_CLUSTER=$OG13_ROOT" "OMNIGRAPH_ENTRYPOINT=$_d/bin/omnigraph-entrypoint" \
        "STUB_LOG=$_d/calls.log" -- sh "$OG13_START" "$@"
}

og13_apply() {
    gate_stub_run "$1" "STUB_LOG=$1/calls.log" -- sh "$OG13_APPLY" "$1/cluster" "$OG13_ROOT"
}

# og13_seq <log> — the unlock/exec sequence reduced to "FU" / "EN" tokens, so a
# wrapper that unlocked AFTER execing is caught by the same assertion.
og13_seq() {
    sed -n 's/^omnigraph cluster force-unlock.*/FU/p; s/^ENTRY.*/EN/p' "$1" | tr '\n' ' ' | sed 's/ *$//'
}

if it "omnigraph 0.13 start: releases the retained lock id, then execs the entrypoint"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_OUT='{"ok": true, "lock_id": "01ABC", "phase": "retained"}' \
        og13_start "$d" serve --config /srv/c.yaml 2>&1)"; rc=$?
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "the wrapper exited $rc: $out" >&2; }
    [[ "$(og13_seq "$d/calls.log")" == "FU EN" ]] \
        || { ok=0; echo "wrong sequence: [$(og13_seq "$d/calls.log")] $out" >&2; }
    grep -qF "omnigraph cluster force-unlock 01ABC --cluster $OG13_ROOT" "$d/calls.log" \
        || { ok=0; echo "no exact-id unlock against this root: $(cat "$d/calls.log")" >&2; }
    grep -qF 'ENTRY serve --config /srv/c.yaml' "$d/calls.log" \
        || { ok=0; echo "the entrypoint lost the passthrough args: $(cat "$d/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "start-server-013.sh did not release-then-exec"; fi
fi

if it "omnigraph 0.13 start: a status with no lock id means no force-unlock"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_OUT='{"ok": true, "lock_id": null, "phase": "fresh"}' og13_start "$d" serve 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$(og13_seq "$d/calls.log")" == "EN" ]] \
        && pass || fail "a fresh root was unlocked anyway or the server never started: rc=$rc [$(og13_seq "$d/calls.log")] $out"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start: an unreadable cluster status refuses to start"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_OUT='{"error": "storage unavailable"}' STUB_STATUS_RC=1 og13_start "$d" serve 2>&1)"; rc=$?
    ok=1
    [[ $rc -eq 1 ]] || { ok=0; echo "status rc 1 became exit $rc: $out" >&2; }
    [[ "$out" == *"refusing to start"* ]] || { ok=0; echo "no refusal message: $out" >&2; }
    [[ "$(og13_seq "$d/calls.log")" == "" && "$out" == *"storage unavailable"* ]] \
        || { ok=0; echo "the wrapper neither stopped at status nor showed its output: [$(cat "$d/calls.log")] $out" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "start-server-013.sh started on an unreadable status"; fi
fi

if it "omnigraph 0.13 start: a failed force-unlock never execs the entrypoint"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_OUT='{"ok": true, "lock_id": "01ABC"}' STUB_UNLOCK_RC=1 og13_start "$d" serve 2>&1)"; rc=$?
    [[ $rc -eq 1 && "$(og13_seq "$d/calls.log")" == "FU" && "$out" == *"FAILED"* ]] \
        && pass || fail "the server booted on a lock that is still held: rc=$rc [$(og13_seq "$d/calls.log")] $out"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start: no OMNIGRAPH_CLUSTER means no start at all"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(gate_stub_run "$d" "OMNIGRAPH_ENTRYPOINT=$d/bin/omnigraph-entrypoint" "STUB_LOG=$d/calls.log" \
        -- env -u OMNIGRAPH_CLUSTER sh "$OG13_START" serve 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *OMNIGRAPH_CLUSTER* && ! -s "$d/calls.log" ]] \
        && pass || fail "an unset root reached the stack: rc=$rc $out [$(cat "$d/calls.log")]"
    rm -rf "$d"
fi

if it "omnigraph 0.13 apply: an existing store is left alone, apply never called"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_RC=0 og13_apply "$d" 2>&1)"; rc=$?
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "a skipped re-apply exited $rc: $out" >&2; }
    [[ "$out" == *"existing store"* ]] || { ok=0; echo "no skip line: $out" >&2; }
    grep -q "cluster apply" "$d/calls.log" && { ok=0; echo "apply ran on an existing store: $(cat "$d/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-fresh-013.sh is not non-destructive"; fi
fi

if it "omnigraph 0.13 apply: releases the admission lock id that apply printed"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_RC=1 STUB_APPLY_OUT=$'applied cluster default\nAdmission lock: 01XYZ; retain until prior work is quiescent, then exact-ID force-unlock' \
        og13_apply "$d" 2>&1)"; rc=$?
    ok=1
    [[ $rc -eq 0 ]] || { ok=0; echo "a fresh apply exited $rc: $out" >&2; }
    [[ "$out" == *"released admission lock 01XYZ"* ]] || { ok=0; echo "no release line: $out" >&2; }
    grep -qF "omnigraph cluster force-unlock 01XYZ --config $d/cluster" "$d/calls.log" \
        || { ok=0; echo "no exact-id unlock on the config dir: $(cat "$d/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-fresh-013.sh left the fresh root locked"; fi
fi

if it "omnigraph 0.13 apply: a failed apply exits non-zero with no unlock"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_RC=1 STUB_APPLY_OUT='{"error": "schema rejected"}' STUB_APPLY_RC=1 og13_apply "$d" 2>&1)"; rc=$?
    ok=1
    [[ $rc -eq 1 ]] || { ok=0; echo "a failed apply exited $rc: $out" >&2; }
    [[ "$out" == *"apply FAILED"* && "$out" == *"schema rejected"* ]] \
        || { ok=0; echo "the apply failure was swallowed: $out" >&2; }
    grep -q "force-unlock" "$d/calls.log" && { ok=0; echo "unlocked after a failed apply: $(cat "$d/calls.log")" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-fresh-013.sh reported success on a failed apply"; fi
fi

if it "omnigraph 0.13 apply: an apply that printed no admission lock succeeds quietly"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_RC=1 STUB_APPLY_OUT='applied cluster default' og13_apply "$d" 2>&1)"; rc=$?
    [[ $rc -eq 0 && "$(og13_seq "$d/calls.log")" == "" && "$out" == *"no admission lock"* ]] \
        && pass || fail "rc=$rc seq=[$(og13_seq "$d/calls.log")] out=$out"
    rm -rf "$d"
fi

if it "omnigraph 0.13 apply: an empty lock id is a failure, never a guessed unlock"; then
    d="$(mktemp -d)"; og13_stub "$d"
    out="$(STUB_STATUS_RC=1 STUB_APPLY_OUT='Admission lock: ; retain until prior work is quiescent' og13_apply "$d" 2>&1)"; rc=$?
    [[ $rc -eq 1 && "$(og13_seq "$d/calls.log")" != *FU* ]] \
        && pass || fail "an unparsed lock id still unlocked: rc=$rc [$(og13_seq "$d/calls.log")] $out"
    rm -rf "$d"
fi

