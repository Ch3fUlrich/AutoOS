#!/bin/sh
# Start omnigraph-server 0.13 after releasing the admission lock the PREVIOUS run retained.
# Measured (rehearsal 2026-10-10): 0.13 retains its cluster admission lock on EVERY shutdown, clean `docker stop`
# included ("v2 cluster admission retained after shutdown; establish prior graph/control I/O quiescence before
# exact-ID force-unlock"), and refuses to boot with state_lock_held until that exact id is force-unlocked. Without
# this wrapper every restart (crash, reboot, `restart: unless-stopped`, stack redeploy) crash-loops.
# Quiescence is established by construction: ONE server container (fixed container_name) uses this root and
# it is not running yet -- this process IS that container's first process. Never run a second server on the
# same root; never use this wrapper for a root a different host also serves.
set -eu
root="${OMNIGRAPH_CLUSTER:?OMNIGRAPH_CLUSTER not set}"
out="$(omnigraph cluster status --cluster "$root" 2>&1)" || { printf '%s\n' "$out" | tail -5 >&2; echo "cannot read cluster status of $root; refusing to start" >&2; exit 1; }
id="$(printf '%s\n' "$out" | sed -n 's/.*"lock_id": *"\([^"]*\)".*/\1/p' | head -n 1)"
if [ -n "$id" ]; then
  echo "releasing retained admission lock $id from the previous run"
  omnigraph cluster force-unlock "$id" --cluster "$root" >/dev/null || { echo "force-unlock $id FAILED" >&2; exit 1; }
fi
exec "${OMNIGRAPH_ENTRYPOINT:-/usr/local/bin/omnigraph-entrypoint}" "$@"
