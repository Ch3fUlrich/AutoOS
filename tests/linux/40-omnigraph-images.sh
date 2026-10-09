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
describe "omnigraph cluster apply gate"

APC_SH="$ROOT/infra/mcp-servers/scripts/apply-cluster.sh"

if it "apply-cluster: refuses to converge with no --go and touches nothing"; then
    d="$(mktemp -d)"
    out="$( ( cd "$d" && bash "$APC_SH" ) 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a --go-less converge exited 0" >&2; }
    [[ "$out" == *"refusing to converge the live omnigraph store without --go"* ]] \
        || { ok=0; echo "no D-825 refusal: $out" >&2; }
    [[ "$out" != *"GO:"* ]] || { ok=0; echo "printed a GO line on a refused run: $out" >&2; }
    [[ ! -e "$d/.graph-backup" ]] || { ok=0; echo "the refused run wrote a backup dir" >&2; }
    rm -rf "$d"
    if (( ok )); then pass; else fail "apply-cluster converged the live store with no --go"; fi
fi

if it "apply-cluster: refuses a malformed --go reference"; then
    sha="$(git -C "$ROOT" rev-parse HEAD)"
    out="$(bash "$APC_SH" --go "not-a-ref" --go-sha "$sha" 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a malformed --go reference exited 0" >&2; }
    [[ "$out" == *"is not a judge run id"* ]] || { ok=0; echo "no ref-format refusal: $out" >&2; }
    if (( ok )); then pass; else fail "apply-cluster accepted a malformed --go"; fi
fi

if it "apply-cluster: refuses when --go-sha is not this checkout's HEAD"; then
    out="$(bash "$APC_SH" --go D-825 --go-sha 0000000000000000000000000000000000000000 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a wrong --go-sha exited 0" >&2; }
    [[ "$out" == *"is not this checkout's HEAD"* ]] || { ok=0; echo "no sha-mismatch refusal: $out" >&2; }
    if (( ok )); then pass; else fail "apply-cluster converged on a --go-sha that is not HEAD"; fi
fi

if it "apply-cluster: --go=OS-0 with a non-HEAD --go-sha= still refuses"; then
    out="$(bash "$APC_SH" --go=OS-0 --go-sha=abcdef 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] && pass \
        || fail "the =-form args were not gated: rc=$rc out=$out"
fi

# ─── The same D-825 gate on the Python live-store tools (dedup / populate / split) ──
# They mutate the running omnigraph store (a reset + overwrite-load, an overwrite-load,
# or a merge-load / node delete), so like apply-cluster.sh a mutating run refuses without
# --go/--go-sha. The gate sits before any docker or token access, so each case below runs
# the real script against no stack at all: a refusal never reaches docker, and a read-only
# run (--dry-run / --no-load / no --apply) passes the gate and only then fails on the
# absent stack — which is exactly the unchanged read-only behaviour.
OG_PY="$ROOT/infra/mcp-servers/scripts"
_head() { git -C "$ROOT" rev-parse HEAD; }

if it "D-825: dedup-graph refuses a live dedup with no --go and touches nothing"; then
    out="$(python3 "$OG_PY/dedup-graph.py" 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a --go-less dedup exited 0" >&2; }
    [[ "$out" == *"refusing to dedup the live omnigraph store without --go"* ]] \
        || { ok=0; echo "no D-825 refusal: $out" >&2; }
    [[ "$out" != *"GO:"* ]] || { ok=0; echo "printed a GO line on a refused run: $out" >&2; }
    if (( ok )); then pass; else fail "dedup-graph deduped with no --go"; fi
fi

if it "D-825: dedup-graph refuses a malformed --go reference" ; then
    out="$(python3 "$OG_PY/dedup-graph.py" --go not-a-ref --go-sha "$(_head)" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not a judge run id"* ]] && pass \
        || fail "a malformed --go reference was accepted: rc=$rc out=$out"
fi

if it "D-825: dedup-graph refuses when --go-sha is not this checkout's HEAD"; then
    out="$(python3 "$OG_PY/dedup-graph.py" --go D-825 --go-sha 0000000000000000000000000000000000000000 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] && pass \
        || fail "a wrong --go-sha was accepted: rc=$rc out=$out"
fi

if it "D-825: dedup-graph --dry-run needs no --go and is unchanged"; then
    out="$(python3 "$OG_PY/dedup-graph.py" --dry-run 2>&1)"; rc=$?
    [[ "$out" != *"refusing to dedup the live omnigraph store"* ]] && pass \
        || fail "the dry run hit the D-825 gate: $out"
fi

if it "D-825: dedup-graph a valid --go + --go-sha echoes GO first and proceeds"; then
    out="$(python3 "$OG_PY/dedup-graph.py" --go D-825 --go-sha "$(_head)" 2>&1)"; rc=$?
    first_line="$(printf '%s\n' "$out" | head -1)"
    [[ "$first_line" == "GO: D-825 sha=$(_head)" ]] && pass \
        || fail "the GO line was not first or the gate refused: [$first_line]"
fi

if it "D-825: populate-embeddings refuses an overwrite-load with no --go"; then
    out="$(python3 "$OG_PY/populate-embeddings.py" --seeds x.jsonl 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"refusing to overwrite-load the live omnigraph graph without --go"* ]] \
        && pass || fail "a --go-less overwrite-load was not refused: rc=$rc out=$out"
fi

if it "D-825: populate-embeddings --no-load needs no --go and is unchanged"; then
    out="$(python3 "$OG_PY/populate-embeddings.py" --seeds x.jsonl --no-load 2>&1)"; rc=$?
    [[ "$out" != *"refusing to overwrite-load the live omnigraph graph"* ]] && pass \
        || fail "--no-load hit the D-825 gate: $out"
fi

if it "D-825: split-project-graph refuses --apply with no --go"; then
    out="$(python3 "$OG_PY/split-project-graph.py" some-project --apply 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"refusing to write to the live omnigraph graph without --go"* ]] \
        && pass || fail "--apply was not refused: rc=$rc out=$out"
fi

if it "D-825: split-project-graph a dry run (no --apply) needs no --go and is unchanged"; then
    out="$(python3 "$OG_PY/split-project-graph.py" some-project 2>&1)"; rc=$?
    [[ "$out" != *"refusing to write to the live omnigraph graph"* ]] && pass \
        || fail "a dry run hit the D-825 gate: $out"
fi

if it "D-825: compact-graphs.ps1 gate is present (refuses without -Go)"; then
    # The pwsh suite owns the full battery; the bash shard only proves the gate exists and
    # fires, so a Windows host that never runs pwsh still guards against the gate vanishing.
    command -v pwsh >/dev/null || { skip "no pwsh on this host"; }
    out="$(pwsh -NoProfile -NonInteractive -File "$OG_PY/compact-graphs.ps1" 2>&1)"; rc=$?
    ok=1
    [[ $rc -ne 0 ]] || { ok=0; echo "a no-Go compact-graphs run exited 0" >&2; }
    [[ "$out" == *"refusing to compact the live omnigraph store without -Go"* ]] \
        || { ok=0; echo "no D-825 refusal: $out" >&2; }
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

if it "D-825: dedup-graph refuses a --go that names no decision, exact id only"; then
    d="$(mktemp -d)"
    printf '| D-825 | all L1s | only an explicit GO counts | router |\n' >"$d/log.md"
    ok=1
    for ref in D-9999 D-82; do
        out="$(AUTOOS_DECISIONS_LOG="$d/log.md" python3 "$OG_PY/dedup-graph.py" \
            --go "$ref" --go-sha "$(_head)" 2>&1)"; rc=$?
        [[ $rc -ne 0 ]] || { ok=0; echo "--go $ref cleared the gate: $out" >&2; }
        [[ "$out" == *"names no decision"* ]] || { ok=0; echo "$ref: no existence refusal: $out" >&2; }
        [[ "$out" != *"GO:"* ]] || { ok=0; echo "$ref: printed a GO line for nothing: $out" >&2; }
    done
    rm -rf "$d"
    if (( ok )); then pass; else fail "dedup-graph accepted a ref that names no artefact"; fi
fi

if it "D-825: dedup-graph refuses when the decisions log cannot be read"; then
    d="$(mktemp -d)"
    out="$(AUTOOS_DECISIONS_LOG="$d/no-such-dir/log.md" python3 "$OG_PY/dedup-graph.py" \
        --go D-825 --go-sha "$(_head)" 2>&1)"; rc=$?
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
    out="$(python3 "$OG_PY/dedup-graph.py" --go D-825 --go-sha 0000000000000000000000000000000000000000 \
        --go-offline 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] && pass \
        || fail "--go-offline waived the sha bar: rc=$rc out=$out"
fi

if it "D-825: apply-cluster refuses a --go that names no artefact, unknown flags still rc 2"; then
    ok=1
    out="$(bash "$APC_SH" --go D-9999 --go-sha "$(_head)" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"names no decision"* ]] \
        || { ok=0; echo "apply-cluster converged on a ref that names nothing: rc=$rc out=$out" >&2; }
    # The fixture tree (tests/fixtures/go-gate, exported by run-tests.sh) holds D-825:
    # an id that IS there must not be refused, or the suite's own mutating cases break.
    out="$(bash "$APC_SH" --go D-82 --go-sha "$(_head)" 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"names no decision"* ]] \
        || { ok=0; echo "D-82 matched the fixture's D-825 line: rc=$rc out=$out" >&2; }
    out="$(bash "$APC_SH" --bogus 2>&1)"; rc=$?
    [[ $rc -eq 2 && "$out" == *"unknown argument '--bogus'"* ]] \
        || { ok=0; echo "an unknown argument was not refused: rc=$rc out=$out" >&2; }
    out="$(bash "$APC_SH" --go D-825 --go-sha 0000000000000000000000000000000000000000 --go-offline 2>&1)"; rc=$?
    [[ $rc -ne 0 && "$out" == *"is not this checkout's HEAD"* ]] \
        || { ok=0; echo "--go-offline waived the sha bar or was an unknown flag: rc=$rc out=$out" >&2; }
    if (( ok )); then pass; else fail "apply-cluster.sh accepted an unverifiable --go"; fi
fi

