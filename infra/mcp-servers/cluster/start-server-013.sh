#!/bin/sh
# Start omnigraph-server 0.13 after releasing the admission lock the PREVIOUS run retained -- guarded.
#
# Measured (rehearsal 2026-10-10): 0.13 retains its cluster admission lock on EVERY shutdown, clean `docker stop`
# included ("v2 cluster admission retained after shutdown; establish prior graph/control I/O quiescence before
# exact-ID force-unlock"), and refuses to boot with state_lock_held until that exact id is force-unlocked. Without
# this wrapper every restart (crash, reboot, `restart: unless-stopped`, stack redeploy) crash-loops.
#
# What the lock exposes (measured, v0.13.0): `cluster status` prints ONLY `lock_id` -- no holder, host or timestamp.
# The lock object (s3://<bucket>/cluster/__cluster/lock.json) has {operation:"serve", created_at, pid:1}: no host or
# instance either, and no heartbeat (created_at never moves). Two things ARE available, and this script uses both:
#   (b) the lock id is a ULID: its first 10 chars are the creation time in ms (matches created_at to the ms);
#   (a) nothing names a live holder, so this script keeps its OWN lease: a heartbeat marker object
#       s3://<bucket>/guard/heartbeat = "<epoch> <hostname>", rewritten every OG_HEARTBEAT_SECONDS while the server
#       runs and deleted on a graceful stop (outside the cluster/ prefix, so omnigraph never sees it).
# Before force-unlocking, REFUSE (exit 1) when
#   (b) the lock is younger than OG_LOCK_GRACE_SECONDS (default 60 = stop_grace_period 35 s + margin): a fresh lock
#       means a server booted just now (or is still draining); an unparseable lock id counts as fresh;
#   (a) the marker names a DIFFERENT hostname and is younger than OG_HEARTBEAT_STALE_SECONDS (default 20 = 4 missed
#       beats): another container is alive on this root, on this host or any other that reaches the same bucket;
#       a marker that cannot be read (not 200/404) also refuses -- unverifiable is not safe.
# The marker naming OUR hostname is the previous incarnation of this very container (docker keeps the container id
# across stop/start): it is dead, we are its first process, so it does not block.
# While the server runs the wrapper keeps the lease: a failed beat is logged loudly every time, and after
# OG_HEARTBEAT_MAX_FAILS consecutive failures (default (stale-1)/beat = 3, i.e. before the lease can look stale to a
# second container) the wrapper SIGTERMs the server and exits non-zero, so docker restarts it under the guard. Chosen over
# "serve on without a lease": the server needs the same S3 endpoint for its own storage, so a 15 s outage already impairs it,
# whereas a silently stale lease lets a second container pass the guard. Residual: the drain (<= 25 s) runs after the
# decision, and a blip longer than the threshold costs one restart.
# S3 credentials never touch argv: they are written once to a 0600 curl config (`curl -K`); curl 7.88 does NOT read
# AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY by itself (measured: no Authorization header is sent without a user).
# Limits, stated plainly: a holder that never ran this wrapper (or whose S3 writes fail) leaves no marker; against it
# only (b) protects, i.e. the lock must be old. A crashed holder is "alive" for up to OG_HEARTBEAT_STALE_SECONDS.
# Do not run two servers on one root; this is a seat belt, not a license.
set -eu

root="${OMNIGRAPH_CLUSTER:?OMNIGRAPH_CLUSTER not set}"
entry="${OMNIGRAPH_ENTRYPOINT:-/usr/local/bin/omnigraph-entrypoint}"
grace="${OG_LOCK_GRACE_SECONDS:-60}"
stale="${OG_HEARTBEAT_STALE_SECONDS:-20}"
beat="${OG_HEARTBEAT_SECONDS:-5}"
me="${HOSTNAME:-$(cat /etc/hostname 2>/dev/null || hostname)}"
ep="${AWS_ENDPOINT_URL_S3:-}"
bucket="${root#s3://}"; bucket="${bucket%%/*}"
marker="$ep/$bucket/guard/heartbeat"

die() { echo "start-server-013: REFUSING TO START: $*" >&2; exit 1; }

for v in grace stale beat; do   # fail closed on a mistyped knob: a non-number would otherwise break the arithmetic below
  eval "val=\$$v"
  case "$val" in ''|*[!0-9]*) die "OG_* setting for '$v' must be a whole number of seconds, got '$val'" ;; esac
done
[ "$beat" -ge 1 ] || die "OG_HEARTBEAT_SECONDS must be >= 1"
maxfail="${OG_HEARTBEAT_MAX_FAILS:-$(( (stale - 1) / beat ))}"
case "$maxfail" in ''|*[!0-9]*) die "OG_HEARTBEAT_MAX_FAILS must be a whole number, got '$maxfail'" ;; esac
[ "$maxfail" -ge 1 ] || maxfail=1

credcfg=/tmp/og-guard-curl.cfg
if [ -n "$ep" ]; then
  [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ] || die "AWS_ENDPOINT_URL_S3 is set but AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are not"
  ( umask 077; printf 'user = "%s:%s"\n' "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" > "$credcfg" )
  trap 'rm -f "$credcfg"' EXIT       # mode 0600, on the container's /tmp (tmpfs in compose): never in the image or a bind mount
fi

s3() {
  curl -sS --max-time 10 -K "$credcfg" -H 'x-amz-content-sha256: UNSIGNED-PAYLOAD' --aws-sigv4 "aws:amz:${AWS_REGION:-us-east-1}:s3" "$@"
}

put_marker() { printf '%s %s' "$(date +%s)" "$me" | s3 -o /dev/null -f -X PUT --data-binary @- "$marker"; }

# ULID (Crockford base32) -> epoch seconds; prints nothing for a non-ULID. NO {26} interval in the awk program: the image has
# mawk 1.3.4 (2020), which lacks interval expressions and rejected every valid id (measured); the caller's grep -E checks the alphabet.
ulid_secs() {
  awk -v u="$1" 'BEGIN {
    a = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"; n = 0
    if (length(u) != 26) exit
    for (i = 1; i <= 10; i++) { p = index(a, substr(u, i, 1)); if (p == 0) exit; n = n * 32 + p - 1 }
    printf "%d", n / 1000 }'
}

out="$(omnigraph cluster status --cluster "$root" 2>&1)" || { printf '%s\n' "$out" | tail -5 >&2; die "cannot read cluster status of $root"; }

# Fail CLOSED on anything unexpected: exactly one lock_id field and exactly one outstanding_id field, each either
# null or a non-empty string. A format change, a multi-lock answer or an unparseable value must never read as "no lock"
# (that would skip the unlock and crash-loop) or as "one lock" (that would unlock the wrong thing).
flat="$(printf '%s ' "$out" | tr '\n\t\r' '   ')"   # one line, so pretty or compact JSON read the same
jval() {   # jval <name> -> prints "" for null, the string for "str"; dies on 0 or >1 fields, or on any other value
  n="$(printf '%s\n' "$out" | grep -o "\"$1\"" | wc -l | tr -d ' ')"   # occurrences, not lines
  [ "$n" = "1" ] || die "cannot interpret cluster status of $root: expected exactly one $1 field, found $n"
  tok="$(printf '%s' "$flat" | sed -n "s/.*\"$1\" *: *\(\"[^\"]*\"\|null\)[] ,}].*/\1/p")"
  case "$tok" in
    null) return 0 ;;
    '""'|'') die "$1 is present but neither null nor a non-empty string (status: $flat)" ;;
    \"*\") tok="${tok#\"}"; printf '%s' "${tok%\"}" ;;
    *) die "$1 is present but not parseable (status: $flat)" ;;
  esac
}
# (assignments, not nested substitutions: `set -e` then stops the script when die fires inside the substitution)
oid="$(jval outstanding_id)"
id="$(jval lock_id)"

# Unknown fields: the REAL 0.13.0 status carries more than lock_id (canonical_root, ledger_id, state_revision,
# result_revision, next_sequence, lookup -- measured on a fresh root, scratch rehearsal 2026-10-11), so extra fields
# must NOT stop a boot with no lock (that crash-looped a healthy fresh root). Every field outside the measured set is
# LOGGED (names only); the fail-closed refusal applies only to the unlock decision (see `unknown` below).
# exactly the 8 fields MEASURED on the real 0.13.0 status (seat 2cf013): `phase` is NOT among them, so a status that
# carries one is from a CLI this wrapper was never proven against -> logged, and the unlock is refused below.
known='lock_id outstanding_id canonical_root ledger_id state_revision result_revision next_sequence lookup'
unknown="$(printf '%s\n' "$flat" | grep -o '"[^"]*"[[:space:]]*:' | sed -e 's/"//g' -e 's/[[:space:]]*:.*//' | sort -u | while read -r f; do
  case " $known " in *" $f "*) ;; *) printf '%s ' "$f" ;; esac; done)"
[ -z "$unknown" ] || echo "start-server-013: note: cluster status carries field(s) outside the measured 0.13.0 set: $unknown" >&2

n_phase="$(printf '%s\n' "$out" | grep -o '"phase" *:' | wc -l | tr -d ' ')"
if [ "$n_phase" -gt 1 ]; then
  die "cannot interpret cluster status of $root: expected at most one phase field, found $n_phase"
elif [ "$n_phase" -eq 1 ]; then
  ptok="$(printf '%s' "$flat" | sed -n 's/.*"phase" *: *\("[^"]*"\|[^ ,}]*\)[] ,}].*/\1/p')"
  [ "$ptok" = '"retained"' ] || die "cluster status of $root has phase $ptok (must be \"retained\" to unlock)"
fi

# A deployment (apply) that was interrupted mid-flight: after force-unlock the server still refuses to serve
# (cluster_deployment_outstanding, measured), so releasing the lock cannot help and must not hide it.
[ -z "$oid" ] || die "deployment $oid is outstanding on $root (an apply was interrupted): the server will not serve until it is reconciled. With no writers running, in this order (measured: the apply refuses with state_lock_held while the interrupted run's lock is retained): 1) omnigraph cluster force-unlock ${id:-<lock_id>} --cluster $root  2) omnigraph cluster apply --cluster $root --deployment-id $oid --writers-stopped --yes --as default  3) release the new lock that step 2 retains (its id is printed; see omnigraph cluster status)"

holder_is_dead() {
  hid="$1"
  now="$(date +%s)"
  # (b) lock age, from the ULID inside the lock id
  printf '%s\n' "$hid" | grep -Eq '^[0-9A-HJKMNP-TV-Z]{26}$' || die "lock id '$hid' is not a ULID, its age is unknown (treated as fresh); confirm no server runs on $root, then: omnigraph cluster force-unlock \"$hid\" --cluster \"$root\""
  born="$(ulid_secs "$hid")"
  [ -n "$born" ] || die "lock id '$hid' is not a ULID, its age is unknown (treated as fresh); confirm no server runs on $root, then: omnigraph cluster force-unlock \"$hid\" --cluster \"$root\""
  age=$((now - born))
  [ "$age" -ge "$grace" ] || die "lock $hid is only ${age}s old (< ${grace}s grace): a server may be booting or draining right now; retry after $((grace - age))s"
  # (a) another live holder, from the heartbeat lease
  if [ -n "$ep" ]; then
    code="$(s3 -o /tmp/og-guard-marker -w '%{http_code}' "$marker" 2>/dev/null)" || code="000"
    case "$code" in
      200)
        mts=""; mhost=""
        read -r mts mhost </tmp/og-guard-marker || true
        rm -f /tmp/og-guard-marker
        # an HTTP-200 marker that is empty or garbage proves nothing about who is alive: refuse, loudly
        case "$mts" in ''|*[!0-9]*) die "heartbeat marker is malformed (timestamp '$mts'); cannot prove no other server runs on $root" ;; esac
        [ -n "$mhost" ] || die "heartbeat marker is malformed (no holder name); cannot prove no other server runs on $root"
        if [ "$mhost" != "$me" ]; then
          mage=$((now - mts))
          [ "$mage" -ge "$stale" ] || die "another holder is alive on $root: container '$mhost' heartbeat ${mage}s ago (< ${stale}s). This container is '$me'"
        fi ;;
      404) rm -f /tmp/og-guard-marker ;;
      *) die "cannot read the heartbeat marker (HTTP $code); cannot prove no other server runs on $root" ;;
    esac
  else
    echo "start-server-013: AWS_ENDPOINT_URL_S3 unset: heartbeat guard skipped, only the lock-age guard applies" >&2
  fi
  return 0
}

if [ -n "$id" ]; then
  [ -z "$unknown" ] || die "cluster status of $root has unknown field(s) ($unknown): will not force-unlock lock $id on a status this wrapper does not know; confirm no server runs on $root, then: omnigraph cluster force-unlock \"$id\" --cluster \"$root\""
  if holder_is_dead "$id"; then
    echo "releasing retained admission lock $id (age ${age}s) from the previous run"
    omnigraph cluster force-unlock "$id" --cluster "$root" >/dev/null || die "force-unlock $id FAILED"
  else
    die "cannot prove lock holder $id is dead"
  fi
fi

# take the lease, keep it fresh while the server runs, drop it on a graceful stop
if [ -n "$ep" ]; then
  put_marker || die "cannot write the heartbeat marker $marker"
  main=$$; lost=/tmp/og-lease-lost; rm -f "$lost"
  ( fails=0
    while :; do
      sleep "$beat"
      if put_marker; then fails=0; continue; fi
      fails=$((fails + 1))
      echo "start-server-013: heartbeat PUT failed ($fails/$maxfail consecutive)" >&2
      if [ "$fails" -ge "$maxfail" ]; then
        echo "start-server-013: LEASE LOST ($fails consecutive failed beats): stopping the server instead of serving without a lease" >&2
        : > "$lost"; kill -TERM "$main" 2>/dev/null; exit 0
      fi
    done ) &
  beater=$!
fi

"$entry" "$@" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true' TERM INT
rc=0
while :; do   # a trapped SIGTERM interrupts `wait`; keep waiting until the child is really gone, keep ITS exit code
  wait "$child" && rc=0 || rc=$?
  kill -0 "$child" 2>/dev/null || break
done
if [ -n "${beater:-}" ]; then
  kill "$beater" 2>/dev/null || true
  if [ -e "${lost:-/nonexistent}" ]; then rm -f "$lost"; echo "start-server-013: exiting non-zero: heartbeat lease was lost" >&2; [ "$rc" -ne 0 ] || rc=75
  else s3 -o /dev/null -X DELETE "$marker" 2>/dev/null || true; fi
fi
rm -f "$credcfg"
exit "$rc"
