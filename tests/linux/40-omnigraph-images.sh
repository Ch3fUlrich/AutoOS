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
