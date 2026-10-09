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
set -euo pipefail

# ─── Fleet rule D-825: converge the live store only on an explicit GO ─────────
# This script has no read-only mode: it applies cluster/ to the live MinIO-backed
# omnigraph store and restarts the server — a shared-infra change every time it
# runs. So it refuses without --go <ref> (a judge run id YYYYMMDD-HHMMSS-…, a
# decision id D-<n> or an OS-<n> item) and --go-sha <sha> equal to this
# checkout's HEAD, so the GO names the exact code being applied. Inspect the
# diff by hand; there is no dry run here to reach.
APC_DIR="$(cd "$(dirname "$0")" && pwd)"              # .../infra/mcp-servers/scripts
APC_ROOT="$(cd "$APC_DIR/../.." && pwd)"             # repo root
GO_REF=""
GO_SHA=""
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
[[ "$GO_REF" =~ ^([0-9]{8}-[0-9]{6}-[^[:space:]]+|D-[0-9]+|OS-[0-9]+)$ ]] || {
    echo "apply-cluster.sh: --go '$GO_REF' is not a judge run id (YYYYMMDD-HHMMSS-…), D-<n> or OS-<n>." >&2
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
ogcli() { docker run --rm --network "$NET" -e OMNIGRAPH_BEARER_TOKEN="$OMNIGRAPH_TOKEN" \
  --entrypoint omnigraph "$IMAGE" "$@"; }
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
docker run --rm --network "$NET" -v "$CLUSTER_SRC:/cluster:ro" --entrypoint omnigraph \
  -e AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER" -e AWS_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD" \
  -e AWS_REGION="${AWS_REGION:-us-east-1}" -e AWS_ENDPOINT_URL_S3="$S3" \
  -e AWS_ALLOW_HTTP=true -e AWS_S3_FORCE_PATH_STYLE=true \
  "$IMAGE" cluster apply --config /cluster --yes --as default || { echo "apply failed — restarting server unchanged"; docker start omnigraph-server >/dev/null; exit 1; }

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
