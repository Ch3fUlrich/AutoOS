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

# ─── D-1038 A6 (P7a2): start-server-013.sh against the REAL 0.13.0 status shapes ─
# Measured (server-L1 rehearsal 2026-10-10, seats 2cf013 / 38b65c): 0.13 retains
# its cluster admission lock on EVERY shutdown, clean `docker stop` included, and
# the next boot dies with state_lock_held — so `restart: unless-stopped`
# crash-loops. start-server-013.sh releases that exact id, but only once it can
# PROVE the holder is dead, and it refuses on any status it does not fully
# understand. It talks to the storage root on every path, so each case runs against
# a recording stand-in `omnigraph` that gate_stub_run puts first on PATH
# (<dir>/bin) — never a live root, never docker, never the network.
#
# P7a mirrored an EARLIER Server version of this wrapper and the judge held it: its
# harness asserted a release from `{"lock_id": "01ABC", "phase": "retained"}`, a
# shape the real CLI never prints, so the battery proved fail-open logic instead of
# the guard. Every case below starts from a byte-for-byte sample of real
# `omnigraph cluster status` output; the only deviations are the ones a case names
# (a lock id created seconds ago, an injected field, a duplicated key).
# AWS_ENDPOINT_URL_S3 is unset in all of them, which skips the heartbeat-lease
# branch entirely — the lease cases and the apply-fresh cases are P7a2 part 2.
describe "omnigraph 0.13 start-server-013 (real status shapes)"

OG13_START="$ROOT/infra/mcp-servers/cluster/start-server-013.sh"
OG13_ROOT="s3://b/cluster"
OG13_HELD_ID="01M4M8T9PDJ9NFYFVWAC6KAMPC"
OG13_STATUS_CALL="omnigraph cluster status --cluster $OG13_ROOT"
OG13_ENTRY_CALL="ENTRY serve --config /srv/c.yaml"

# The measured samples: each is the Server's own fixture file reproduced byte for
# byte, with its path and sha256 above it — "what this wrapper was proven against"
# is precisely what these cases are about.

# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-held.json @ d37fe8504 sha256 f2aaab21140acb115f4a436a6ee079761e070a015e6b0144af5e57f189abf8af
fx_held() { cat <<'FX'
{
  "canonical_root": "s3://omnigraph-013/cluster",
  "ledger_id": "01M4M8RTG6W31TG0MDA1QEVP3Y",
  "state_revision": 23,
  "result_revision": 1,
  "next_sequence": 2,
  "lock_id": "01M4M8T9PDJ9NFYFVWAC6KAMPC",
  "outstanding_id": null,
  "lookup": null
}
FX
}

# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-retained-clean-stop.json @ d37fe8504 sha256 f2aaab21140acb115f4a436a6ee079761e070a015e6b0144af5e57f189abf8af
# What a clean `docker stop` leaves behind is byte-identical to the held sample
# above — same sha256, and that is the measured point: a graceful stop and a
# retained lock are indistinguishable in the status, only the id's age says more.
fx_retained_clean_stop() { fx_held; }

# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-no-lock.json @ d37fe8504 sha256 309394731af650d81d7503343fb47ead119a8c58af4dc67d5cc98fc2c24fcaff
fx_no_lock() { cat <<'FX'
{
  "canonical_root": "s3://omnigraph-013/cluster",
  "ledger_id": "01M4M8RTG6W31TG0MDA1QEVP3Y",
  "state_revision": 23,
  "result_revision": 1,
  "next_sequence": 2,
  "lock_id": null,
  "outstanding_id": null,
  "lookup": null
}
FX
}

# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-after-crash.json @ d37fe8504 sha256 86498ed658bc83a25dea86ad112abc5ee18bdaec0a7a8747c9730a9a4b3a39bb
fx_after_crash() { cat <<'FX'
{
  "canonical_root": "s3://omnigraph-013/cluster",
  "ledger_id": "01M4M8RTG6W31TG0MDA1QEVP3Y",
  "state_revision": 23,
  "result_revision": 1,
  "next_sequence": 2,
  "lock_id": "01M4M8XK9BF3KEETG8SJYE6G7A",
  "outstanding_id": null,
  "lookup": null
}
FX
}

# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-after-reconcile.json @ d37fe8504 sha256 55b32d04f907180aa3f31f1cc0f85f4efeb4a7c0808096f5b7b0d8044ce15411
fx_after_reconcile() { cat <<'FX'
{
  "canonical_root": "s3://omnigraph-013/cluster",
  "ledger_id": "01M4M8RTG6W31TG0MDA1QEVP3Y",
  "state_revision": 30,
  "result_revision": 1,
  "next_sequence": 3,
  "lock_id": "01M4M9181N80TT2GSJ8M09ABPP",
  "outstanding_id": null,
  "lookup": null
}
FX
}

# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-outstanding.json @ d37fe8504 sha256 00e3412ffe0e68663392e6a86da032c24a2f8c1982ed579e2b8f56a2f6cbca45
fx_outstanding() { cat <<'FX'
{
  "canonical_root": "s3://omnigraph-013/cluster",
  "ledger_id": "01M4M8RTG6W31TG0MDA1QEVP3Y",
  "state_revision": 25,
  "result_revision": 1,
  "next_sequence": 3,
  "lock_id": "01M4M90RJ8YR1HD7BXXGQD7DDV",
  "outstanding_id": "01M4M8RTG6W31TG0MDA1QEVP3Y:2:01M4M90RMDTSTSXGS0WCFTRM5T",
  "lookup": null
}
FX
}

# The fresh-root answer: `cluster status` exits non-zero and names state_missing.
# The start wrapper has no use for it — a status it cannot read is a refusal — and
# the apply-fresh cases that consume both streams are P7a2 part 2.
# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-fresh.stderr.txt @ d37fe8504 sha256 014b8472ecfdd47bcae7224489912015759adcdcf9fc4d3ab0864ab0b61bac6b
fx_fresh_stderr() { cat <<'FX'
Error: 
   0: state_missing __cluster/state.json: cluster ledger is missing
FX
}

# Server tests/scripts/fixtures/omnigraph013/samples/cluster-status-fresh.stdout.txt @ d37fe8504 sha256 7f9ab2bf932578e19282b1fd34536dd1817d06748847a14d35470fb631cf9e58
fx_fresh_stdout() { cat <<'FX'
ERROR state_missing __cluster/state.json: cluster ledger is missing
FX
}

# og13_stub <dir> — stand-ins for `omnigraph` and for the server entrypoint. Every
# call is appended to $STUB_LOG in order; the case picks the status bytes with
# STUB_STATUS_FILE (and STUB_STATUS_RC) and the unlock verdict with STUB_UNLOCK_RC.
og13_stub() {
    local _d="$1"
    mkdir -p "$_d/bin"
    : >"$_d/calls.log"
    cat >"$_d/bin/omnigraph" <<'OG13'
#!/bin/sh
printf 'omnigraph %s\n' "$*" >>"$STUB_LOG"
case "$1 $2" in
    "cluster status")       cat "$STUB_STATUS_FILE"; exit "${STUB_STATUS_RC:-0}" ;;
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

# og13_start <dir> — run the wrapper the way the container does, with the lease
# guard off (AWS_ENDPOINT_URL_S3 unset; part 2 stands a fake S3 up for the
# heartbeat branch) and one argument pair that must survive to the entrypoint.
# The record is reset first, so the sequence a case asserts is this run's alone —
# a case that runs the wrapper over a fixture pair would otherwise read one log
# holding both runs.
og13_start() {
    local _d="$1"
    : >"$_d/calls.log"
    gate_stub_run "$_d" -u AWS_ENDPOINT_URL_S3 "OMNIGRAPH_CLUSTER=$OG13_ROOT" \
        "OMNIGRAPH_ENTRYPOINT=$_d/bin/omnigraph-entrypoint" "STUB_LOG=$_d/calls.log" \
        -- sh "$OG13_START" serve --config /srv/c.yaml
}

# og13_fx <dir> <fixture-fn> <name> [extra-field] — write a measured sample to
# <dir>/status-<name>. With an extra field (e.g. '"surprise": 1') it is injected
# straight after the opening brace, so a mutated case still carries every real
# field of the sample it grew out of.
og13_fx() {
    local _d="$1" _w="$2" _n="$3" _x="${4:-}"
    if [[ -z "$_x" ]]; then "$_w" >"$_d/status-$_n"; return; fi
    "$_w" | awk -v x="$_x" '{ print } /^\{/ { print "  " x "," }' >"$_d/status-$_n"
}

# og13_young_id — a lock id created five seconds ago. The first ten characters of a
# ULID are its creation time in milliseconds (Crockford base32), so this is a real
# id whose age the guard must read as fresh; the tail is fixed valid-alphabet filler.
og13_young_id() {
    python3 -c 'import time
A = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ms = int((time.time() - 5) * 1000); t = ""
for _ in range(10):
    t = A[ms % 32] + t; ms //= 32
print(t + "A1CDEFGHJKMNPQRS")'
}

# og13_calls [line ...] / og13_unlock <id> — the record a case expects, built from
# the same strings the stand-in writes, so an assertion cannot drift from the stub.
og13_calls() { printf '%s\n' "$@"; }
og13_unlock() { printf 'omnigraph cluster force-unlock %s --cluster %s' "$1" "$OG13_ROOT"; }

# og13_problems <dir> <rc> <want-rc> <want-log> <out> [needle ...] — the three
# things every case owes: the exit code, the WHOLE recorded call sequence (which is
# what pins an unlock ahead of the entrypoint, and pins its absence when the wrapper
# should not have called it), and the words the wrapper owes the operator. A needle
# prefixed "!" must NOT appear. Prints the failures and nothing when clean, which is
# what lets the cases that run a fixture pair accumulate over both runs.
og13_problems() {
    local _d="$1" _rc="$2" _wrc="$3" _wlog="$4" _out="$5" _bad="" _n _got
    shift 5
    [[ "$_rc" -eq "$_wrc" ]] || _bad+="exited $_rc, wanted $_wrc; "
    _got="$(cat "$_d/calls.log")"
    if [[ "$_got" != "$_wlog" ]]; then
        _bad+="calls [$(printf '%s' "$_got" | tr '\n' '|')], wanted [$(printf '%s' "$_wlog" | tr '\n' '|')]; "
    fi
    for _n in "$@"; do
        if [[ "${_n:0:1}" == "!" ]]; then
            [[ "$_out" != *"${_n:1}"* ]] || _bad+="said [${_n:1}]: ${_out//$'\n'/ }; "
        else
            [[ "$_out" == *"$_n"* ]] || _bad+="no [$_n] in: ${_out//$'\n'/ }; "
        fi
    done
    printf '%s' "$_bad"
}

og13_expect() {
    local _p; _p="$(og13_problems "$@")"
    [[ -z "$_p" ]] && pass || fail "$_p"
}

if it "omnigraph 0.13 start-server-013: a retained lock past its grace is released by its exact id, before the entrypoint"; then
    d="$(mktemp -d)"; og13_stub "$d"; og13_fx "$d" fx_held held
    out="$(STUB_STATUS_FILE="$d/status-held" og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 0 \
        "$(og13_calls "$OG13_STATUS_CALL" "$(og13_unlock "$OG13_HELD_ID")" "$OG13_ENTRY_CALL")" \
        "$out" "releasing retained admission lock" "01M4M8T9PDJ9NFYFVWAC6KAMPC" "!REFUSING TO START"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: a lock younger than the grace is a refusal, with no unlock and no boot"; then
    if ! command -v python3 >/dev/null 2>&1; then
        skip "no python3 to mint a fresh lock id"
    else
        d="$(mktemp -d)"; og13_stub "$d"
        og13_fx "$d" fx_held held
        sed "s/\"lock_id\": \"[^\"]*\"/\"lock_id\": \"$(og13_young_id)\"/" \
            "$d/status-held" >"$d/status-young"
        out="$(STUB_STATUS_FILE="$d/status-young" og13_start "$d" 2>&1)"; rc=$?
        og13_expect "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
            "$out" "REFUSING TO START" "is only" "old" "(< 60s grace)"
        rm -rf "$d"
    fi
fi

if it "omnigraph 0.13 start-server-013: the lock a clean docker stop left behind releases like any other old lock"; then
    d="$(mktemp -d)"; og13_stub "$d"; og13_fx "$d" fx_retained_clean_stop retained-clean-stop
    out="$(STUB_STATUS_FILE="$d/status-retained-clean-stop" og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 0 \
        "$(og13_calls "$OG13_STATUS_CALL" "$(og13_unlock "$OG13_HELD_ID")" "$OG13_ENTRY_CALL")" \
        "$out" "releasing retained admission lock" "!REFUSING TO START"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: a status with a null lock id boots without touching the lock API"; then
    d="$(mktemp -d)"; og13_stub "$d"; og13_fx "$d" fx_no_lock no-lock
    out="$(STUB_STATUS_FILE="$d/status-no-lock" og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 0 "$(og13_calls "$OG13_STATUS_CALL" "$OG13_ENTRY_CALL")" \
        "$out" "!REFUSING TO START" "!releasing"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: the crash and post-reconcile locks release, because their ULIDs are past the grace"; then
    # Derived from the wrapper, then measured — not guessed. Both samples carry a
    # non-null lock_id, a null outstanding_id, no phase field, and nothing but the
    # eight field names in the script's `known` set, so every guard short of the age
    # check passes and the age check is the whole decision. Decoding the first ten
    # characters puts both ids at the rehearsal on 2026-10-11 (…XK9B at 01:23:29,
    # …9181N at 01:25:29), thousands of seconds before any run of this suite, and
    # with AWS_ENDPOINT_URL_S3 unset the lease guard only reports that it was
    # skipped. So the outcome is: exit 0, force-unlock of that exact id, then the
    # entrypoint. The age only ever grows, so this holds for as long as the samples do.
    d="$(mktemp -d)"; og13_stub "$d"; problems=""
    for pair in "after_crash:01M4M8XK9BF3KEETG8SJYE6G7A" "after_reconcile:01M4M9181N80TT2GSJ8M09ABPP"; do
        fx="fx_${pair%%:*}"; id="${pair##*:}"
        og13_fx "$d" "$fx" "${fx#fx_}"
        out="$(STUB_STATUS_FILE="$d/status-${fx#fx_}" og13_start "$d" 2>&1)"; rc=$?
        problems+="$(og13_problems "$d" "$rc" 0 \
            "$(og13_calls "$OG13_STATUS_CALL" "$(og13_unlock "$id")" "$OG13_ENTRY_CALL")" \
            "$out" "releasing retained admission lock $id" "!REFUSING TO START")"
    done
    rm -rf "$d"
    [[ -z "$problems" ]] && pass || fail "$problems"
fi

if it "omnigraph 0.13 start-server-013: an outstanding deployment is refused with the ordered remedy, never unlocked away"; then
    d="$(mktemp -d)"; og13_stub "$d"; og13_fx "$d" fx_outstanding outstanding
    out="$(STUB_STATUS_FILE="$d/status-outstanding" og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
        "$out" "is outstanding on" "1) omnigraph cluster force-unlock" \
        "2) omnigraph cluster apply" "--writers-stopped" "3) release the new lock"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: an unknown field with a lock held refuses the unlock; the same field with no lock only logs"; then
    d="$(mktemp -d)"; og13_stub "$d"
    og13_fx "$d" fx_held held-surprise '"surprise": 1'
    og13_fx "$d" fx_no_lock no-lock-surprise '"surprise": 1'
    out="$(STUB_STATUS_FILE="$d/status-held-surprise" og13_start "$d" 2>&1)"; rc=$?
    held="$(og13_problems "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
        "$out" "unknown field(s)" "surprise" "will not force-unlock")"
    out="$(STUB_STATUS_FILE="$d/status-no-lock-surprise" og13_start "$d" 2>&1)"; rc=$?
    clean="$(og13_problems "$d" "$rc" 0 "$(og13_calls "$OG13_STATUS_CALL" "$OG13_ENTRY_CALL")" \
        "$out" "note: cluster status carries field(s) outside the measured 0.13.0 set: surprise" \
        "!REFUSING TO START")"
    rm -rf "$d"
    [[ -z "$held$clean" ]] && pass || fail "with a lock held: $held | with no lock: $clean"
fi

if it "omnigraph 0.13 start-server-013: a phase field is refused on both values, because the measured 0.13.0 status has none"; then
    # P7a's harness took `"phase": "retained"` as the shape that proved a release was
    # safe. The real CLI prints no phase field at all, so the hardened wrapper reads
    # one as a status from a CLI it was never proven against: an "active" phase stops
    # at the phase check itself, and a "retained" phase passes that check only to
    # stop at the unknown-field check. Neither value unlocks and neither boots.
    d="$(mktemp -d)"; og13_stub "$d"
    og13_fx "$d" fx_held phase-active '"phase": "active"'
    og13_fx "$d" fx_held phase-retained '"phase": "retained"'
    out="$(STUB_STATUS_FILE="$d/status-phase-active" og13_start "$d" 2>&1)"; rc=$?
    active="$(og13_problems "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
        "$out" "has phase \"active\"" "must be \"retained\" to unlock")"
    out="$(STUB_STATUS_FILE="$d/status-phase-retained" og13_start "$d" 2>&1)"; rc=$?
    retained="$(og13_problems "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
        "$out" "unknown field(s)" "phase" "will not force-unlock")"
    rm -rf "$d"
    [[ -z "$active$retained" ]] && pass || fail "phase active: $active | phase retained: $retained"
fi

if it "omnigraph 0.13 start-server-013: two lock_id fields in one status are an unparseable status, never a guessed unlock"; then
    # The duplicate carries a DIFFERENT id on purpose: a wrapper that read the first
    # or the last lock_id rather than refusing would unlock a lock nobody named.
    d="$(mktemp -d)"; og13_stub "$d"
    og13_fx "$d" fx_held dup-lock '"lock_id": "01M4M8XK9BF3KEETG8SJYE6G7A"'
    out="$(STUB_STATUS_FILE="$d/status-dup-lock" og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
        "$out" "expected exactly one lock_id field, found 2"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: a status command that fails refuses to start and boots nothing"; then
    d="$(mktemp -d)"; og13_stub "$d"
    printf '%s\n' '{"error": "storage unavailable"}' >"$d/status-garbage"
    out="$(STUB_STATUS_FILE="$d/status-garbage" STUB_STATUS_RC=1 og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
        "$out" "cannot read cluster status" "REFUSING TO START" "storage unavailable"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: a lock id that is not a ULID has an unknown age and counts as fresh"; then
    d="$(mktemp -d)"; og13_stub "$d"; og13_fx "$d" fx_held held
    sed 's/"lock_id": "[^"]*"/"lock_id": "NOT-A-ULID"/' "$d/status-held" >"$d/status-nonulid"
    out="$(STUB_STATUS_FILE="$d/status-nonulid" og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
        "$out" "is not a ULID" "its age is unknown"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: a failed force-unlock never boots the server on a lock still held"; then
    d="$(mktemp -d)"; og13_stub "$d"; og13_fx "$d" fx_held held
    out="$(STUB_STATUS_FILE="$d/status-held" STUB_UNLOCK_RC=1 og13_start "$d" 2>&1)"; rc=$?
    og13_expect "$d" "$rc" 1 \
        "$(og13_calls "$OG13_STATUS_CALL" "$(og13_unlock "$OG13_HELD_ID")")" \
        "$out" "force-unlock $OG13_HELD_ID FAILED"
    rm -rf "$d"
fi

if it "omnigraph 0.13 start-server-013: a mistyped grace and an unset root both stop before the first call"; then
    d="$(mktemp -d)"; og13_stub "$d"; og13_fx "$d" fx_held held
    out="$(OG_LOCK_GRACE_SECONDS=abc STUB_STATUS_FILE="$d/status-held" og13_start "$d" 2>&1)"; rc=$?
    knob="$(og13_problems "$d" "$rc" 1 "" "$out" "whole number of seconds" "'grace'")"
    # No root to look at: the wrapper dies on the parameter expansion itself, and the
    # code is the shell's (POSIX: > 0), not one the script chooses — so this asserts
    # non-zero rather than a number that would pin dash or bash rather than the guard.
    out="$(gate_stub_run "$d" -u AWS_ENDPOINT_URL_S3 -u OMNIGRAPH_CLUSTER \
        "OMNIGRAPH_ENTRYPOINT=$d/bin/omnigraph-entrypoint" "STUB_LOG=$d/calls.log" \
        -- sh "$OG13_START" serve 2>&1)"; rc=$?
    [[ $rc -ne 0 ]] || knob+="an unset OMNIGRAPH_CLUSTER exited $rc; "
    [[ "$(cat "$d/calls.log")" == "" ]] || knob+="an unset root still reached the stack; "
    [[ "$out" == *"OMNIGRAPH_CLUSTER not set"* ]] || knob+="no parameter-expansion error: $out"
    rm -rf "$d"
    [[ -z "$knob" ]] && pass || fail "$knob"
fi

if it "omnigraph 0.13 start-server-013: the age guard decodes the same under mawk and under gawk"; then
    # The 0.13 image ships mawk 1.3.4 (2020), which has no {n} regex intervals and
    # once rejected every valid lock id — so the release shape and the fresh-lock
    # shape each run again with awk resolved to every awk this host offers.
    variants=()
    for cand in mawk gawk; do
        [[ -x "/usr/bin/$cand" ]] && variants+=("$cand")
    done
    if [[ ${#variants[@]} -eq 0 ]] || ! command -v python3 >/dev/null 2>&1; then
        skip "needs at least one of /usr/bin/mawk, /usr/bin/gawk, and python3 to mint a fresh lock id"
    else
        echo "    awk variants run: ${variants[*]}" >&2
        problems=""; p=""
        for cand in "${variants[@]}"; do
            d="$(mktemp -d)"; og13_stub "$d"; ln -sf "/usr/bin/$cand" "$d/bin/awk"
            og13_fx "$d" fx_held held
            out="$(STUB_STATUS_FILE="$d/status-held" og13_start "$d" 2>&1)"; rc=$?
            p="$(og13_problems "$d" "$rc" 0 \
                "$(og13_calls "$OG13_STATUS_CALL" "$(og13_unlock "$OG13_HELD_ID")" "$OG13_ENTRY_CALL")" \
                "$out" "releasing retained admission lock")"
            [[ -n "$p" ]] && problems+="[$cand ]$p"
            sed "s/\"lock_id\": \"[^\"]*\"/\"lock_id\": \"$(og13_young_id)\"/" \
                "$d/status-held" >"$d/status-young"
            out="$(STUB_STATUS_FILE="$d/status-young" og13_start "$d" 2>&1)"; rc=$?
            p="$(og13_problems "$d" "$rc" 1 "$(og13_calls "$OG13_STATUS_CALL")" \
                "$out" "REFUSING TO START" "is only" "old")"
            [[ -n "$p" ]] && problems+="[$cand young]$p"
            rm -rf "$d"
        done
        [[ -z "$problems" ]] && pass || fail "$problems"
    fi
fi

