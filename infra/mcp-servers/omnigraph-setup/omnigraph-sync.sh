#!/usr/bin/env bash
# omnigraph-sync.sh — reconcile this client's LOCAL Omnigraph with CENTRAL, per graph.
#
# The Linux/macOS twin of sync-windows.ps1. Both drive the same two Python helpers
# (omnigraph_jsonl.py, pull_graph.py), so the actual reconcile logic exists once.
#
# Per graph:
#   0. BACKUP local main -> backups/local-<graph>-<ts>.jsonl   (before any write)
#   1. VERIFY local for duplicates
#   2. PUSH only the DELTA to central main  (nodes that differ + edges central lacks)
#   3. VERIFY central
#   4. PULL central -> local via pull_graph.py (purge, then load into the empty graph)
#   5. VERIFY local
# One graph failing does not abort the others; the exit code is the last failure.
#
# Why it looks like this — each choice was bought with an incident (2026-07-17):
#   * DELTA push, never the whole export. Edges have no @key, so merge-loading an edge
#     central already has APPENDS a duplicate. Pushing everything gave central 2x edges on
#     every project graph. Nodes are @key(slug) and upsert safely.
#   * NO device branch. `branch create` can hit a Lance internal error ("Clone operation
#     should not enter build_manifest"); `branch merge` of a branch that touched a table
#     main is level with fails ("Concurrent modification: table version N already exists").
#     The branch bought "review before merge", which is meaningless on an unattended timer.
#   * PURGE-then-load for the pull, not `load --mode overwrite`: overwrite into a POPULATED
#     graph trips a Lance bug — and can land anyway while exiting 1, so even its failure is
#     untrustworthy. pull_graph.py handles this (and restores from backup on failure).
#   * Data is piped over STDIN, never bind-mounted. A `mktemp -d` bind mount is mangled by
#     Git Bash, which is why the old version failed on every graph under MSYS.
#   * Every docker invocation's exit code is checked. The old version discarded stderr and
#     `|| true`-d over failures, so a fully failed sync still reported success.
#
# Config via env (or a .env next to this script):
#   CENTRAL_URL, CENTRAL_TOKEN, LOCAL_TOKEN                      (required)
#   LOCAL_URL(=http://127.0.0.1:8080)        local API as seen from THIS HOST
#   LOCAL_URL_CONTAINER(=$LOCAL_URL)         local API as seen from INSIDE the CLI
#                                            container. On Docker Desktop / a compose
#                                            network this must be http://omnigraph-server:8080
#                                            — 127.0.0.1 there is the container itself.
#   GRAPHS(=all graphs central exposes)      "a,b" or "a b"
#   GRAPH(=memory; legacy single-graph)      honoured only if not 'memory'
#   VIEWER_URL(=)                            central viewer, e.g. http://coding.example.internal:8090.
#                                            Set it and each synced graph is attributed to
#                                            THIS DEVICE in the viewer's Sync log, by the
#                                            source IP the viewer sees. Unset = no
#                                            attribution (the sync itself is unaffected).
#   DEVICE(=hostname)                        only used to sweep a stale device/<host> branch
#   DOCKER_NET(=host)                        CLI-container network
#   BACKUP_DIR(=<here>/backups)  OMNIGRAPH_IMAGE(=…)  PYTHON(=python3)
#   DRY_RUN(=)                               snapshot + verify BOTH sides, no writes
#
# See SYNC-MANUAL.md for scheduling (5-minute cadence) and troubleshooting.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[ -f "$here/.env" ] && . "$here/.env"
: "${CENTRAL_URL:?set CENTRAL_URL}"; : "${CENTRAL_TOKEN:?set CENTRAL_TOKEN}"
: "${LOCAL_URL:=http://127.0.0.1:8080}"; : "${LOCAL_TOKEN:?set LOCAL_TOKEN}"
LOCAL_URL_CONTAINER="${LOCAL_URL_CONTAINER:-$LOCAL_URL}"
DEVICE="${DEVICE:-$(hostname)}"
IMAGE="${OMNIGRAPH_IMAGE:-modernrelay/omnigraph-server:v0.8.1}"
DOCKER_NET="${DOCKER_NET:-host}"
BACKUP_DIR="${BACKUP_DIR:-$here/backups}"
BRANCH="device/${DEVICE}"
JQ="$here/omnigraph_jsonl.py"; PULL="$here/pull_graph.py"; PY="${PYTHON:-python3}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
mkdir -p "$BACKUP_DIR"
log() { echo "[omnigraph-sync] $*" >&2; }

# omnigraph CLI in a throwaway container. Fails loudly: stderr is kept and the exit code
# is the verdict — never `|| true` this.
og() {  # og <token> <cli-args...>
  docker run --rm -i --network "$DOCKER_NET" -e "OMNIGRAPH_BEARER_TOKEN=$1" \
    --entrypoint omnigraph "$IMAGE" "${@:2}"
}
# Load JSONL into <graph> on <url> via stdin (no bind mount — see the header).
og_load() {  # og_load <token> <url> <graph> <mode> <file>
  docker run --rm -i --network "$DOCKER_NET" -e "OMNIGRAPH_BEARER_TOKEN=$1" \
    --entrypoint sh "$IMAGE" -c \
    "cat > /tmp/d.jsonl; omnigraph load --server $2 --graph $3 --data /tmp/d.jsonl --mode $4 --yes --json" \
    < "$5"
}
api() {  # api <token> <url> <graph> <verb: export|…>  -> stdout
  curl -fsS -m 300 -X POST "${2%/}/graphs/$3/$4" \
    -H "Authorization: Bearer $1" -H 'content-type: application/json' -d '{}'
}

# Which graphs to sync. Per-project isolation means every project graph must sync, not
# just `memory`.
if [ -n "${GRAPHS:-}" ]; then
  read -r -a GRAPH_LIST <<< "${GRAPHS//,/ }"
elif [ -n "${GRAPH:-}" ] && [ "${GRAPH}" != "memory" ]; then
  GRAPH_LIST=("$GRAPH")                       # legacy single-graph
else
  read -r -a GRAPH_LIST <<< "$(curl -fsS -m 60 "${CENTRAL_URL%/}/graphs" \
    -H "Authorization: Bearer $CENTRAL_TOKEN" \
    | grep -o '"graph_id":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ')"
fi
[ "${#GRAPH_LIST[@]}" -gt 0 ] || { log "no graphs to sync (GET /graphs empty?)"; exit 1; }
log "syncing graphs: ${GRAPH_LIST[*]}"

sync_graph() {
  local G="$1" ts backup central push n_node n_edge
  ts="$(date -u +%Y%m%dT%H%M%SZ)"; backup="$BACKUP_DIR/local-${G}-$ts.jsonl"

  # 0. back up local BEFORE anything
  if ! api "$LOCAL_TOKEN" "$LOCAL_URL" "$G" export > "$backup"; then
    log "[$G] local export/backup failed — is the local server up at $LOCAL_URL?"; return 1
  fi
  log "[$G] backed up local -> $backup"

  # 1. verify local — and HONOUR the verdict. `verify` exits non-zero on duplicates and on
  #    NO DATA (an empty body is what a failed fetch looks like: dead server, bad token,
  #    wrong graph — reporting "clean" for it is how a wiped stack read as healthy).
  #    Swallowing this with `|| true` is the exact false-success class this stack keeps
  #    producing, so a bad side aborts the graph instead of syncing garbage over the other.
  log "[$G] local verify:"
  if ! "$PY" "$JQ" verify < "$backup"; then
    log "[$G] local is dirty or empty — refusing to sync this graph. Backup: $backup"; return 1
  fi

  # 2. central export (needed for the delta, and for the dry-run report)
  central="$work/central-$G.jsonl"
  if ! api "$CENTRAL_TOKEN" "$CENTRAL_URL" "$G" export > "$central"; then
    log "[$G] central export failed"; return 1
  fi
  log "[$G] central verify:"
  if ! "$PY" "$JQ" verify < "$central"; then
    log "[$G] central is dirty or empty — NOT pushing into it, NOT pulling from it."; return 2
  fi

  if [ -n "${DRY_RUN:-}" ]; then
    "$PY" "$JQ" pushset "$central" < "$backup" > "$work/push-$G.jsonl" || return 1
    n_node=$(grep -c '"type"' "$work/push-$G.jsonl" || true)
    n_edge=$(grep -c '"edge"' "$work/push-$G.jsonl" || true)
    log "[$G] DRY_RUN — would push ${n_node:-0} node(s), ${n_edge:-0} edge(s). No writes. Backup: $backup"
    return 0
  fi

  # 3. push the DELTA straight to central main (no branch — see the header)
  push="$work/push-$G.jsonl"
  "$PY" "$JQ" pushset "$central" < "$backup" > "$push" || { log "[$G] pushset failed"; return 1; }
  n_node=$(grep -c '"type"' "$push" || true); n_edge=$(grep -c '"edge"' "$push" || true)
  if [ ! -s "$push" ]; then
    log "[$G] nothing to push (local adds nothing to central)"
  else
    log "[$G] pushing delta -> central main: ${n_node:-0} changed/new node(s), ${n_edge:-0} new edge(s)"
    if ! og_load "$CENTRAL_TOKEN" "$CENTRAL_URL" "$G" merge "$push" >/dev/null; then
      log "[$G] push failed — central untouched by this graph. Backup: $backup"; return 2
    fi
    log "[$G] pushed to central main"
  fi

  # 4. pull central -> local (purge-then-load; restores from backup on failure)
  if ! "$PY" "$PULL" "$G" \
        --source-url "$CENTRAL_URL" --source-token "$CENTRAL_TOKEN" \
        --target-url "$LOCAL_URL" --target-load-url "$LOCAL_URL_CONTAINER" \
        --target-token "$LOCAL_TOKEN" --net "$DOCKER_NET" --backup "$backup"; then
    log "[$G] pull failed — backup: $backup"; return 3
  fi

  # 4b. tell the viewer WHICH DEVICE just synced. It reads the source IP off this
  #     connection — a commit records no client address, and actor_id comes from the
  #     bearer token (one shared token => `default` for everyone), so the IP is the only
  #     free identity. Best-effort by design: attribution must never fail a sync.
  if [ -n "${VIEWER_URL:-}" ]; then
    curl -fsS -m 10 -X POST "${VIEWER_URL%/}/api/sync-ping?graph=$G" -o /dev/null \
      || log "[$G] sync-ping to $VIEWER_URL failed (attribution only — sync itself is fine)"
  fi

  # 5. sweep a stale device branch from an older version of this script (it blocks
  #    `schema apply`). Absent is the normal case, so a failure here is not an error.
  og "$CENTRAL_TOKEN" branch delete "$BRANCH" --server "$CENTRAL_URL" --graph "$G" --yes \
    >/dev/null 2>&1 && log "[$G] removed stale device branch $BRANCH"

  log "[$G] sync complete."
  return 0
}

rc=0
for g in "${GRAPH_LIST[@]}"; do
  if ! sync_graph "$g"; then
    rc=$?
    log "[$g] sync returned $rc — continuing with remaining graphs"
  fi
done
[ -n "${DRY_RUN:-}" ] && log "DRY_RUN complete — no writes made."
log "sync finished for: ${GRAPH_LIST[*]} (rc=$rc)"
exit "$rc"
