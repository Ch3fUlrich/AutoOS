#!/usr/bin/env bash
# setup-sync.sh — configure + schedule the Omnigraph local<->central sync (Linux/macOS/WSL).
#
# One command to go from "the stack is running" to "memory syncs every 5 minutes":
#   1. derive what it can (LOCAL_TOKEN from ../.env.shared, DOCKER_NET + the two local URLs
#      from the RUNNING containers, DEVICE from the hostname)
#   2. write omnigraph-setup/.env, MERGING rather than clobbering (see below)
#   3. install a systemd --user timer (or a cron line as fallback)
#   4. prove it with a DRY RUN before anything is scheduled
#
# WHY IT MERGES INSTEAD OF WRITING FRESH: the one value that cannot be derived is
# CENTRAL_TOKEN — central's bearer is NOT the local one in .env.shared (they are different
# secrets; on this homelab they genuinely differ). An earlier setup script in this directory
# overwrote a good .env with a template and cost the operator their credentials. So: an
# existing value always wins over a derived one, and nothing here ever writes an empty over
# a non-empty. Re-running this script is safe by construction.
#
# Usage:
#   ./setup-sync.sh                                   # derive, merge, schedule, dry-run
#   ./setup-sync.sh --central-url https://… --central-token abc…
#   ./setup-sync.sh --no-schedule                     # config + dry-run only
#   ./setup-sync.sh --interval 15                     # minutes (default 5)
#   ./setup-sync.sh --show                            # print resolved config, change nothing
#
# Windows: use setup-sync.ps1 (Scheduled Task instead of systemd).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mcp_root="$(cd "$here/.." && pwd)"
shared="$mcp_root/.env.shared"
envfile="$here/.env"
sync="$here/omnigraph-sync.sh"

INTERVAL=5
SCHEDULE=1
SHOW_ONLY=0
ARG_CENTRAL_URL=""
ARG_CENTRAL_TOKEN=""

die() { printf '\033[31m[setup-sync] ERROR: %s\033[0m\n' "$*" >&2; exit 1; }
log() { printf '\033[36m[setup-sync]\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m[setup-sync] OK\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m[setup-sync] !!\033[0m %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --central-url)   ARG_CENTRAL_URL="${2:?}"; shift 2 ;;
    --central-token) ARG_CENTRAL_TOKEN="${2:?}"; shift 2 ;;
    --interval)      INTERVAL="${2:?}"; shift 2 ;;
    --no-schedule)   SCHEDULE=0; shift ;;
    --show)          SHOW_ONLY=1; shift ;;
    -h|--help)       sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

# --- 1. read an existing .env (values here WIN over anything derived) ------------------
# Parsed, not sourced: `.` would execute the file, and a stray backtick in a token would
# run as a command.
get_existing() {  # get_existing <KEY>
  [ -f "$envfile" ] || return 0
  sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*(.*)$/\1/p" "$envfile" | tail -1 \
    | sed -E 's/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# --- 2. derive from .env.shared -------------------------------------------------------
[ -f "$shared" ] || die "missing $shared
  This is the file the whole stack is keyed on. Create it first:
    cd $mcp_root && cp .env.shared.example .env.shared
    # then set OMNIGRAPH_TOKEN (openssl rand -hex 32)"

SHARED_TOKEN="$(sed -nE 's/^[[:space:]]*OMNIGRAPH_TOKEN[[:space:]]*=[[:space:]]*(.*)$/\1/p' "$shared" \
  | tail -1 | sed -E 's/[[:space:]]+$//; s/^"(.*)"$/\1/')"
[ -n "$SHARED_TOKEN" ] || die "OMNIGRAPH_TOKEN is empty in $shared"
case "$SHARED_TOKEN" in
  generate-with-*|change-me*|"") die "OMNIGRAPH_TOKEN in $shared is still the placeholder.
  Generate a real one:  openssl rand -hex 32" ;;
esac
log "read OMNIGRAPH_TOKEN from .env.shared (${#SHARED_TOKEN} chars) -> LOCAL_TOKEN"

# --- 3. derive the docker facts from what is RUNNING, never from a config file ---------
# The network name differs per host (compose project name + network), and getting it wrong
# fails QUIETLY: the container starts, DNS for `omnigraph-server` does not resolve.
DET_NET=""
if command -v docker >/dev/null 2>&1; then
  DET_NET="$(docker inspect omnigraph-server \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' 2>/dev/null \
    | sed '/^$/d' | head -1 || true)"
fi
if [ -n "$DET_NET" ]; then
  log "detected docker network from the running container: $DET_NET"
else
  DET_NET="$(get_existing DOCKER_NET)"; DET_NET="${DET_NET:-mcp-server_mcp-net}"
  warn "omnigraph-server is not running (or docker is unavailable) — falling back to '$DET_NET'.
       Start the stack and re-run to have this detected instead of guessed:
         cd $mcp_root && docker compose --env-file .env.shared --env-file .env.server --env-file ~/.config/autoos/site.env -f docker-compose.server.yml up -d"
fi

DEVICE_D="$(hostname | tr '[:upper:]' '[:lower:]')"

# --- 4. merge: existing > argument > derived ------------------------------------------
pick() {  # pick <KEY> <arg> <derived>
  local cur; cur="$(get_existing "$1")"
  if [ -n "${2:-}" ]; then printf '%s' "$2"          # explicit flag beats everything
  elif [ -n "$cur" ];  then printf '%s' "$cur"       # never clobber what is already there
  else printf '%s' "${3:-}"; fi
}
CENTRAL_URL="$(pick CENTRAL_URL "$ARG_CENTRAL_URL" "${CENTRAL_URL:-}")"
CENTRAL_TOKEN="$(pick CENTRAL_TOKEN "$ARG_CENTRAL_TOKEN" "${CENTRAL_TOKEN:-}")"
LOCAL_TOKEN="$(pick LOCAL_TOKEN "" "$SHARED_TOKEN")"
LOCAL_URL="$(pick LOCAL_URL "" "http://127.0.0.1:8080")"
LOCAL_URL_CONTAINER="$(pick LOCAL_URL_CONTAINER "" "http://omnigraph-server:8080")"
DOCKER_NET="$(pick DOCKER_NET "" "$DET_NET")"
DEVICE="$(pick DEVICE "" "$DEVICE_D")"
# Optional. Left empty unless you already set it or export it: guessing a viewer URL would
# make every sync retry a bogus host. Empty simply means "no attribution".
VIEWER_URL="$(pick VIEWER_URL "" "${VIEWER_URL:-}")"

# LOCAL_TOKEN must equal .env.shared's token — the local server was started with it.
if [ "$LOCAL_TOKEN" != "$SHARED_TOKEN" ]; then
  warn "LOCAL_TOKEN in .env differs from OMNIGRAPH_TOKEN in .env.shared.
       The local server authenticates with .env.shared's value, so the existing one is
       probably stale. Keeping yours; delete the line from .env to re-derive it."
fi

[ -n "$CENTRAL_URL" ] || die "CENTRAL_URL is not set and cannot be derived.
  Pass it once and it will be remembered:
    ./setup-sync.sh --central-url https://omnigraph.example.com --central-token <bearer>"
[ -n "$CENTRAL_TOKEN" ] || die "CENTRAL_TOKEN is not set and cannot be derived.
  Central's bearer is NOT the local token in .env.shared — it is a separate secret held by
  the server operator. Pass it once:
    ./setup-sync.sh --central-token <bearer>"

if [ "$SHOW_ONLY" = 1 ]; then
  log "resolved configuration (nothing written):"
  printf '  CENTRAL_URL=%s\n  CENTRAL_TOKEN=%s…(%s chars)\n  LOCAL_TOKEN=%s…(%s chars)\n' \
    "$CENTRAL_URL" "${CENTRAL_TOKEN:0:6}" "${#CENTRAL_TOKEN}" "${LOCAL_TOKEN:0:6}" "${#LOCAL_TOKEN}"
  printf '  LOCAL_URL=%s\n  LOCAL_URL_CONTAINER=%s\n  DOCKER_NET=%s\n  DEVICE=%s\n' \
    "$LOCAL_URL" "$LOCAL_URL_CONTAINER" "$DOCKER_NET" "$DEVICE"
  exit 0
fi

# --- 5. PRE-FLIGHT: prove the credentials work BEFORE touching a working .env ----------
# Same rule pull_graph.py learned the hard way: never destroy the old state until the thing
# that replaces it is known to work. Writing first and validating after means one typo'd
# --central-token replaces a good config with a broken one; the backup makes that
# recoverable, but only if you notice. Check first instead.
preflight() {  # preflight <label> <url> <token>
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 30 "${2%/}/graphs" \
    -H "Authorization: Bearer $3" 2>/dev/null || echo 000)"
  case "$code" in
    200) log "pre-flight OK: $1 answered 200 at $2" ;;
    401|403) die "pre-flight FAILED: $1 rejected the token (HTTP $code) at $2
  Nothing was written; your existing .env is untouched. Check the bearer and re-run." ;;
    000) die "pre-flight FAILED: $1 is unreachable at $2
  Nothing was written; your existing .env is untouched.
  Is the stack up / are you online?  curl -fsS ${2%/}/healthz" ;;
    *) die "pre-flight FAILED: $1 answered HTTP $code at $2 (expected 200)
  Nothing was written; your existing .env is untouched." ;;
  esac
}
preflight "central" "$CENTRAL_URL" "$CENTRAL_TOKEN"
preflight "local"   "$LOCAL_URL"   "$LOCAL_TOKEN"

# --- 6. write .env (0600, atomically) -------------------------------------------------
if [ -f "$envfile" ]; then
  cp -p "$envfile" "$envfile.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  log "backed up the existing .env"
fi
umask 077
tmp="$envfile.tmp.$$"
cat > "$tmp" <<EOF
# omnigraph sync config — generated by setup-sync.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# GITIGNORED: holds two bearer tokens. Never commit it.
# Re-run ./setup-sync.sh any time; it preserves whatever is already here.

# Central (authoritative) server.
CENTRAL_URL=$CENTRAL_URL
CENTRAL_TOKEN=$CENTRAL_TOKEN

# Local server — TWO views of the SAME thing, not interchangeable:
#   LOCAL_URL           reachable from THIS HOST (the published port)
#   LOCAL_URL_CONTAINER reachable from INSIDE the CLI container (compose service name)
# Mixing them up is how a pull once emptied a graph: the export worked from the host and
# the load silently had nowhere to go.
LOCAL_URL=$LOCAL_URL
LOCAL_URL_CONTAINER=$LOCAL_URL_CONTAINER
LOCAL_TOKEN=$LOCAL_TOKEN

# Docker network hosting omnigraph-server. Detected from the running container, because it
# differs per host and a wrong value fails silently (DNS just does not resolve).
DOCKER_NET=$DOCKER_NET

DEVICE=$DEVICE

# Central viewer, for the Sync log's "source" column. The viewer attributes a push to a
# device by the SOURCE IP of this ping — a commit records no client address, and actor_id
# comes from the shared bearer token (so it reads \`default\` for every device). Optional:
# empty = no attribution, sync unaffected. e.g. http://coding.example.internal:8090
VIEWER_URL=$VIEWER_URL
# GRAPHS unset => sync every graph central exposes (per-project isolation means all of them).
EOF
mv -f "$tmp" "$envfile"
chmod 600 "$envfile" 2>/dev/null || true
# Report the mode we actually GOT, not the one we asked for. chmod is a silent no-op on
# NTFS under Git Bash/WSL-interop, so claiming "0600" there would be exactly the kind of
# unearned reassurance this directory has been bitten by. On Windows use setup-sync.ps1,
# which sets a real ACL via icacls.
mode="$(stat -c '%a' "$envfile" 2>/dev/null || stat -f '%Lp' "$envfile" 2>/dev/null || echo '?')"
if [ "$mode" = "600" ]; then
  ok "wrote $envfile (mode 0600)"
else
  ok "wrote $envfile"
  warn "mode is $mode, not 0600 — chmod did not take (normal on NTFS via Git Bash).
       This file holds two bearer tokens. On Windows, run setup-sync.ps1 instead: it sets
       a real ACL. On Linux, check the filesystem's mount options."
fi

# --- 7. prove it works BEFORE scheduling ----------------------------------------------
[ -x "$sync" ] || chmod +x "$sync" 2>/dev/null || true
log "dry run (no writes) — this is the gate: nothing gets scheduled unless it passes"
if DRY_RUN=1 "$sync"; then
  ok "dry run passed"
else
  die "dry run FAILED (exit $?). Nothing was scheduled. Fix the above, then re-run.
  Config is written, so re-running is cheap. Common causes:
    - local stack down       -> docker compose … -f docker-compose.server.yml up -d
    - wrong CENTRAL_TOKEN    -> ./setup-sync.sh --central-token <bearer>
    - central unreachable    -> curl -fsS $CENTRAL_URL/healthz"
fi

[ "$SCHEDULE" = 1 ] || { ok "done (--no-schedule: nothing was scheduled)"; exit 0; }

# --- 8. schedule ----------------------------------------------------------------------
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  unitdir="$HOME/.config/systemd/user"; mkdir -p "$unitdir"
  cat > "$unitdir/omnigraph-sync.service" <<EOF
# Generated by setup-sync.sh. Re-run it to regenerate.
[Unit]
Description=Reconcile local Omnigraph memory with the central server
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=$sync
Nice=10
EOF
  cat > "$unitdir/omnigraph-sync.timer" <<EOF
# Generated by setup-sync.sh. Runs ${INTERVAL} min after boot, then every ${INTERVAL} min.
# The script exits immediately when central is unreachable, so frequent runs are cheap.
[Unit]
Description=Periodic Omnigraph memory sync

[Timer]
OnBootSec=${INTERVAL}min
OnUnitActiveSec=${INTERVAL}min
Persistent=true
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now omnigraph-sync.timer
  ok "systemd --user timer enabled — every ${INTERVAL} min"
  systemctl --user list-timers omnigraph-sync.timer --no-pager | sed 's/^/    /' || true
  cat <<EOF

  Timers for a --user unit only run while you are logged in. To keep syncing after logout:
      loginctl enable-linger $USER
  Inspect:  systemctl --user status omnigraph-sync.service
  Logs:     journalctl --user -u omnigraph-sync.service -n 50
  Stop:     systemctl --user disable --now omnigraph-sync.timer
EOF
elif command -v crontab >/dev/null 2>&1; then
  warn "no systemd --user session; falling back to cron"
  line="*/$INTERVAL * * * * $sync >> $here/backups/cron.log 2>&1"
  ( crontab -l 2>/dev/null | grep -Fv "$sync"; echo "$line" ) | crontab -
  ok "cron entry installed — every ${INTERVAL} min (log: $here/backups/cron.log)"
  echo "  Remove with: crontab -e   (delete the omnigraph-sync.sh line)"
else
  warn "neither systemd --user nor cron is available — schedule $sync every ${INTERVAL} min yourself."
fi
