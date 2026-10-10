#!/bin/sh
# Apply the declared cluster to a FRESH Omnigraph 0.13 storage root, then release the admission lock that
# `cluster apply` retains. NON-DESTRUCTIVE: a root that already has a ledger is left alone (a stack recreation
# must never re-apply), exactly like the 0.8.1 `cluster import` guard it replaces.
#   usage: apply-fresh-013.sh CONFIG_DIR ROOT_URI      e.g. /tmp/cluster s3://omnigraph-013/cluster
# 0.13 prints "Admission lock: <ID>; retain until prior work is quiescent, then exact-ID force-unlock". On a fresh
# root this script is the only actor, so quiescence holds and it releases that exact ID (never a guessed one).
set -eu
cfg="${1:?config dir}"; root="${2:?storage root uri}"
if omnigraph cluster status --cluster "$root" >/dev/null 2>&1; then
  echo "existing store at $root: skipping apply (config changes are applied deliberately, see the runbook)"
  exit 0
fi
out="$(omnigraph cluster apply --config "$cfg" --yes --as default 2>&1)" || { printf '%s\n' "$out" | tail -20; echo "apply FAILED" >&2; exit 1; }
printf '%s\n' "$out" | sed -n 1,3p
line="$(printf '%s\n' "$out" | grep -m1 'Admission lock:' || true)"
if [ -z "$line" ]; then
  echo "apply reported no admission lock; if the server refuses to boot with state_lock_held, unlock the id it prints" >&2
  exit 0
fi
# the id is everything between "Admission lock: " and the ";" -- no assumption about its alphabet
id="$(printf '%s\n' "$line" | sed -n 's/^.*Admission lock: *\([^;]*\);.*$/\1/p' | head -n 1)"
if [ -z "$id" ]; then echo "cannot parse the admission lock id from: $line" >&2; exit 1; fi
omnigraph cluster force-unlock "$id" --config "$cfg"
echo "released admission lock $id"
