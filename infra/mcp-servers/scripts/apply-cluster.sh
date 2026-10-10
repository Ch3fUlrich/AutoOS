#!/usr/bin/env bash
# Converge cluster/ (graphs + policies) into the live MinIO-backed store.
#
# The server's init container SKIPS `apply` on an existing store on purpose, so
# config changes (new graphs, new policies, multi-user) are applied here, on
# demand, with a safety net: snapshot the `memory` graph first, apply, restart
# the server, then VERIFY memory's node count is unchanged. If it dropped, the
# snapshot path is printed so you can restore.
#
# Run from infra/mcp-servers:  ./scripts/apply-cluster.sh --go <ref> --go-sha <sha>
#                              (add --go-offline only when this host cannot read the GO's artefact)
set -euo pipefail

# ─── Fleet rule D-825: converge the live store only on an explicit GO ─────────
# This script has no read-only mode: it applies cluster/ to the live MinIO-backed
# omnigraph store and restarts the server — a shared-infra change every time it
# runs. So it refuses without --go <ref> (a judge run id YYYYMMDD-HHMMSS-…, a
# decision id D-<n> or an OS-<n> item) and --go-sha <sha> equal to this
# checkout's HEAD, so the GO names the exact code being applied. The reference is
# then proved to EXIST through `_go_gate.py verify-ref` — the one implementation
# every gate on this rule calls — and a ref that names no artefact, or a source
# that cannot be read, refuses. --go-offline is the operator's declared exception
# and logs `GO-OFFLINE: <ref> unverified`. Inspect the diff by hand; there is no
# dry run here to reach.
APC_SH_DIR="$(cd "$(dirname "$0")" && pwd)"              # .../infra/mcp-servers/scripts
APC_ROOT="$(cd "$APC_SH_DIR/../.." && pwd)"             # repo root
GO_REF=""
GO_SHA=""
GO_OFFLINE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --go)
            [ $# -ge 2 ] || { echo "apply-cluster.sh: --go needs a reference." >&2; exit 2; }
            GO_REF="$2"; shift ;;
        --go=*)    GO_REF="${1#--go=}" ;;
        --go-sha)
            [ $# -ge 2 ] || { echo "apply-cluster.sh: --go-sha needs this checkout's HEAD sha." >&2; exit 2; }
            GO_SHA="$2"; shift ;;
        --go-sha=*) GO_SHA="${1#--go-sha=}" ;;
        --go-offline) GO_OFFLINE=1 ;;
        *) echo "apply-cluster.sh: unknown argument '$1'." >&2; exit 2 ;;
    esac
    shift
done
[ -n "$GO_REF" ] || {
    echo "apply-cluster.sh: refusing to converge the live omnigraph store without --go <ref> (fleet rule D-825)." >&2
    echo "  apply + server restart mutate shared infrastructure; pass --go <ref> --go-sha <sha>," >&2
    echo "  where <ref> is a judge run id (YYYYMMDD-HHMMSS-…), D-<n> or OS-<n>, and <sha> is" >&2
    echo "  'git rev-parse HEAD' of this checkout." >&2
    exit 2
}
[ -n "$GO_SHA" ] || {
    echo "apply-cluster.sh: refusing to converge without --go-sha <sha> (must equal this checkout's HEAD)." >&2
    exit 2
}
CHECKOUT_HEAD="$(git -C "$APC_ROOT" rev-parse HEAD 2>/dev/null || true)"
[ -n "$CHECKOUT_HEAD" ] || {
    echo "apply-cluster.sh: cannot read this checkout's HEAD (git rev-parse failed in $APC_ROOT) — refusing." >&2
    exit 2
}
[ "$GO_SHA" = "$CHECKOUT_HEAD" ] || {
    echo "apply-cluster.sh: --go-sha '$GO_SHA' is not this checkout's HEAD '$CHECKOUT_HEAD'." >&2
    exit 2
}
# The reference's SHAPE and its EXISTENCE are one implementation, not two: the
# `_go_gate.py verify-ref` command next to this script, which every gate on this rule
# calls. Checking only the shape was the bug — `--go D-1` matched the regex while no
# D-1 decision existed anywhere. The CLI refuses on a shape that is not a judge
# reference, on an id naming no artefact, and on a source it cannot read; its refusal
# text goes to stderr untouched, so only the exit code is handled here. A refusal
# prints no GO line, and nothing below it has run.
_go_gate="$APC_SH_DIR/_go_gate.py"
_go_offline_arg=""
[ "$GO_OFFLINE" = "1" ] && _go_offline_arg="--offline"
if ! command -v python3 >/dev/null 2>&1 || [ ! -f "$_go_gate" ]; then
    # No way to read the artefact tree at all — an unverifiable GO is not a GO,
    # unless the operator said so with --go-offline, which is logged as such.
    if [ "$GO_OFFLINE" != "1" ]; then
        echo "apply-cluster.sh: cannot verify --go '$GO_REF' — python3 and $_go_gate are what read the GO's artefact tree (fleet rule D-825)." >&2
        echo "  Install python3, or pass --go-offline to run with the ref logged as unverified." >&2
        exit 2
    fi
    echo "GO-OFFLINE: $GO_REF unverified"
elif ! _go_verified="$(python3 "$_go_gate" verify-ref --tool apply-cluster.sh --ref "$GO_REF" \
            --root "$APC_ROOT" ${_go_offline_arg:+"$_go_offline_arg"})"; then
    exit 2
elif [ -n "$_go_verified" ]; then
    printf '%s\n' "$_go_verified"
fi
echo "GO: $GO_REF sha=$CHECKOUT_HEAD"

here="$(cd "$(dirname "$0")/.." && pwd)"        # infra/mcp-servers
cd "$here"
set -a; . ./.env.shared; . ./.env.server; set +a # OMNIGRAPH_TOKEN/S3_BUCKET + MINIO_ROOT_USER/PASSWORD
IMAGE="modernrelay/omnigraph-server:v0.8.1"
# Ask docker which network the live server is on rather than assuming: local is
# `mcp-server_mcp-net` (compose project `mcp-server`), central/coding.example.internal is
# `mcp-servers_default` (project `mcp-servers`). A wrong network is a quiet failure —
# the CLI container simply can't resolve omnigraph-minio. OMNI_NET overrides.
NET="${OMNI_NET:-$(docker inspect omnigraph-server \
      --format '{{range $n,$_ := .NetworkSettings.Networks}}{{$n}} {{end}}' 2>/dev/null \
      | awk '{print $1}')}"
NET="${NET:-mcp-server_mcp-net}"                # fall back when the stack isn't on this host
S3="${OMNI_S3:-http://omnigraph-minio:9000}"    # must match omnigraph-server's AWS_ENDPOINT_URL_S3
BK=".graph-backup/pre-apply-$(date -u +%Y%m%d-%H%M%S).jsonl"

# Git Bash (MSYS) rewrites container-side absolute paths — `--config /cluster`
# arrives as `C:/Program Files/Git/cluster`. Pass the host side in Windows form
# and switch MSYS argument conversion off for the docker call below.
if command -v cygpath >/dev/null 2>&1; then
  CLUSTER_SRC="$(cygpath -m "$here/cluster")"
  export MSYS2_ARG_CONV_EXCL='*'
else
  CLUSTER_SRC="$here/cluster"
fi

count() { # node count of the memory graph (nodes have "type"; edges have "from")
  curl -s -X POST "http://127.0.0.1:8080/graphs/memory/export" \
    -H "Authorization: Bearer ${OMNIGRAPH_TOKEN}" -H 'content-type: application/json' -d '{}' \
    | grep -c '"type"' || true
}

mkdir -p .graph-backup
echo "› snapshotting memory graph -> $BK"
curl -s -X POST "http://127.0.0.1:8080/graphs/memory/export" \
  -H "Authorization: Bearer ${OMNIGRAPH_TOKEN}" -H 'content-type: application/json' -d '{}' -o "$BK"
BEFORE=$(count); echo "  memory nodes before: $BEFORE"

# `cluster apply` refuses while any non-main branch exists. The multi-branch sync
# keeps persistent device/<host> branches, so merge each back into main (native,
# edge-deduping — no data lost) and delete it first. The server must be UP for
# branch ops, so do this BEFORE the stop below.
echo "› reconciling non-main branches before apply…"
# AO-SECRET-ARGV (D-962): the bearer travels in the container's ENVIRONMENT, never
# in its argv. `-e NAME="$SECRET"` is expanded by the shell before docker is exec'd,
# so the value sits in `/proc/<pid>/cmdline` — world-readable to any local `ps`.
# The assignment prefix is not argv, and `-e NAME` with no `=` makes docker copy the
# value it already has in its own environment.
ogcli() { OMNIGRAPH_BEARER_TOKEN="$OMNIGRAPH_TOKEN" docker run --rm --network "$NET" \
  -e OMNIGRAPH_BEARER_TOKEN --entrypoint omnigraph "$IMAGE" "$@"; }
for g in $(curl -s http://127.0.0.1:8080/graphs -H "Authorization: Bearer ${OMNIGRAPH_TOKEN}" \
           | grep -o '"graph_id":"[^"]*"' | cut -d'"' -f4); do
  for b in $(curl -s "http://127.0.0.1:8080/graphs/$g/branches" -H "Authorization: Bearer ${OMNIGRAPH_TOKEN}" \
             | grep -o '"[^"]*"' | tr -d '"' | grep -vxE 'branches|main'); do
    echo "  [$g] merge + delete branch '$b'"
    ogcli branch merge  "$b" --into main --server http://omnigraph-server:8080 --graph "$g" --yes >/dev/null 2>&1 || true
    ogcli branch delete "$b"             --server http://omnigraph-server:8080 --graph "$g" --yes >/dev/null 2>&1 || true
  done
done

# the running server holds the cluster state lock — stop it so apply can acquire it
echo "› stopping omnigraph-server (releases the state lock)…"
docker stop omnigraph-server >/dev/null

echo "› applying cluster config…"
# AO-SECRET-ARGV (D-962): the MinIO credentials reach the container through its
# environment (`-e NAME` inherits from this shell), not through argv — an expanded
# `-e NAME="$SECRET"` is readable by any local user in `ps`. The subshell keeps the
# two renamed exports scoped to this one command.
(
  export AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER"
  export AWS_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD"
  docker run --rm --network "$NET" -v "$CLUSTER_SRC:/cluster:ro" --entrypoint omnigraph \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    -e AWS_REGION="${AWS_REGION:-us-east-1}" -e AWS_ENDPOINT_URL_S3="$S3" \
    -e AWS_ALLOW_HTTP=true -e AWS_S3_FORCE_PATH_STYLE=true \
    "$IMAGE" cluster apply --config /cluster --yes --as default
) || { echo "apply failed — restarting server unchanged"; docker start omnigraph-server >/dev/null; exit 1; }

echo "› starting omnigraph-server to pick up new graphs…"
docker start omnigraph-server >/dev/null
for _ in $(seq 1 20); do curl -sf http://127.0.0.1:8080/healthz >/dev/null 2>&1 && break || sleep 2; done

AFTER=$(count); echo "  memory nodes after:  $AFTER"
echo "› graphs now:"; curl -s http://127.0.0.1:8080/graphs -H "Authorization: Bearer ${OMNIGRAPH_TOKEN}"

if [ "$AFTER" -lt "$BEFORE" ]; then
  echo "!! memory node count DROPPED ($BEFORE -> $AFTER). Restore with:"
  echo "   python3 scripts/populate-embeddings.py  # or load $BK back into memory (overwrite)"
  exit 1
fi
echo "✓ apply complete; memory graph intact ($AFTER nodes)."
