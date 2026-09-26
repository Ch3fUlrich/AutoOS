#!/usr/bin/env bash
# install.sh — install (or refresh) the Herdr session autostart on one host.
#
# One script, two scopes, because "user units on a login host" and "system
# units as root on a headless host" used to be two forked scripts that drifted
# before being merged into this one. Idempotent: re-running just refreshes the
# unit files.
#
# Scope    Runs as   Units live in
# -------  --------  --------------------------
# user     login     ~/.config/systemd/user   (needs lingering)
# system   root      /etc/systemd/system
#
# System units are used where the sessions run as root and there is no login
# session at boot to own a user unit; user units are used where the sessions run
# as a normal login user and a root service could not reach that user's herdr
# socket or transcripts. Every profile sets HS_SCOPE explicitly (see
# profiles/example.conf) -- this script does not guess a scope from a profile's
# name, because a public profile name carries no such meaning.
#
# It deliberately does NOT start herdr-server on the system branch: a live
# Claude session already runs inside a hand-started herdr, and `herdr server`
# exits 1 when one exists — starting the unit now would only burn its restart
# budget. The unit takes over at the next reboot, which is exactly when it is
# needed. (The user branch enables herdr-server too, for the same reason: it is
# not started here either — only enabled.)
#
# Usage:  ./install.sh --profile <name> [--dry-run]
#         <name> is any profiles/<name>.conf. Start from profiles/example.conf.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROFILE=""
DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --profile) PROFILE="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

avail() { ls -1 "$HERE/profiles/" 2>/dev/null | sed 's/\.conf$//' | paste -sd, - ; }

if [ -z "$PROFILE" ]; then
    echo "usage: $0 --profile {$(avail)} [--dry-run]" >&2
    exit 2
fi

PROFILE_CONF="$HERE/profiles/$PROFILE.conf"
[ -f "$PROFILE_CONF" ] || {
    echo "FATAL: no profile at $PROFILE_CONF (have: $(avail))" >&2; exit 1; }

# A profile declares its own scope with HS_SCOPE={system,user}. There is no
# default to guess from the profile's name: that only ever worked for the
# hosts this driver was written on, and cannot generalise to an arbitrary
# profile a downstream user creates.
prof_key() { sed -n "s/^[[:space:]]*$1=\([^#]*\).*/\1/p" "$PROFILE_CONF" | tail -1 | tr -d '[:space:]'; }
SCOPE="$(prof_key HS_SCOPE)"
[ -n "$SCOPE" ] || {
    echo "FATAL: $PROFILE_CONF must set HS_SCOPE=user or HS_SCOPE=system (see profiles/example.conf)" >&2
    exit 2; }
case "$SCOPE" in
    system|user) ;;
    *) echo "FATAL: HS_SCOPE in $PROFILE_CONF must be 'system' or 'user', got '$SCOPE'" >&2; exit 2 ;;
esac

# Absolute path this checkout lives at; substituted into the unit templates so no
# unit file hard-codes an install location.
APPDIR="$HERE"
WORKDIR="$(prof_key HS_WORKDIR)"
[ -n "$WORKDIR" ] || WORKDIR="$APPDIR"
[ -x "$HERE/herdr-sessions.sh" ] || { echo "FATAL: driver missing at $HERE/herdr-sessions.sh" >&2; exit 1; }

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then echo "  would: $*"; else "$@"; fi; }

# Render a unit template into a real unit file. The checked-in units are
# templates carrying three literal tokens -- @PROFILE@ (which profile), @APPDIR@
# (where this checkout lives) and @WORKDIR@ (the cwd panes inherit). "#" is the
# sed delimiter because every replacement is a path.
render_unit() {
    sed -e "s#@PROFILE@#$PROFILE#g" -e "s#@APPDIR@#$APPDIR#g" -e "s#@WORKDIR@#$WORKDIR#g" "$1"
}

install_unit() {  # $1=src template  $2=dest path
    if [ "$DRY" = 1 ]; then
        echo "  would: install -m 0644 (rendered $1) $2"
        return
    fi
    local tmp; tmp="$(mktemp)"
    render_unit "$1" >"$tmp"
    install -m 0644 "$tmp" "$2"
    rm -f "$tmp"
}

UNITS=(herdr-sessions-update.service herdr-server.service herdr-sessions-restore.service
       herdr-sessions-snapshot.service herdr-sessions-snapshot.timer)

if [ "$SCOPE" = user ]; then
    # ── user scope: no root ─────────────────────────────────────────────────
    DEST="$HOME/.config/systemd/user"
    say "systemd user units -> $DEST"
    run mkdir -p "$DEST"
    for u in "${UNITS[@]}"; do
        install_unit "$HERE/systemd/user/$u" "$DEST/$u"
        echo "    $u"
    done

    say "checking linger (needed for start-at-boot without login)"
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
        echo "    Linger=yes"
    else
        echo "    Linger=NO — units will NOT start at boot. Fix with:"
        echo "      sudo loginctl enable-linger $USER"
    fi

    say "enabling"
    run systemctl --user daemon-reload
    run systemctl --user enable herdr-sessions-update.service
    run systemctl --user enable herdr-server.service
    run systemctl --user enable herdr-sessions-restore.service
    run systemctl --user enable --now herdr-sessions-snapshot.timer

    echo
    echo "Done. Day to day: ./herdr-sessions {update,relaunch,status,snapshot,restore} [--dry-run]"
    echo "    boot -> herdr-sessions-update -> herdr-server -> herdr-sessions-restore (full resume, never a summary)"
    echo "    every 5 min -> snapshot of the live sessions"
    echo
    echo "Deliberately NOT started here: herdr-server (enabled only — starting it now"
    echo "would race a hand-launched one holding the herdr socket)."
else
    # ── system scope: root required ──────────────────────────────────────────
    # These preconditions only gate a REAL install. --dry-run must work from any
    # checkout, as any user, with no herdr installed -- that is what makes it
    # usable as a CI smoke test (see tests/test_smoke.sh) instead of only ever
    # runnable on the live host.
    # The units are rendered with @APPDIR@ = this checkout, so any location works.
    HERDR_BIN_CHECK="$(prof_key HERDR_BIN)"
    [ -n "$HERDR_BIN_CHECK" ] || HERDR_BIN_CHECK=/root/.local/bin/herdr
    if [ "$DRY" = 1 ]; then
        say "dry-run: skipping root/herdr-binary preconditions"
        echo "    (a real install requires: root, and herdr at $HERDR_BIN_CHECK)"
    else
        [ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
        [ -x "$HERDR_BIN_CHECK" ] || {
            echo "FATAL: herdr is not installed at $HERDR_BIN_CHECK" >&2; exit 1; }
    fi

    DEST="/etc/systemd/system"
    say "systemd system units -> $DEST"
    for u in "${UNITS[@]}"; do
        install_unit "$HERE/systemd/system/$u" "$DEST/$u"
        echo "    $u"
    done

    say "systemd daemon-reload"
    run systemctl daemon-reload

    say "enable units"
    for u in herdr-sessions-update.service herdr-server.service \
             herdr-sessions-restore.service herdr-sessions-snapshot.timer; do
        run systemctl enable "$u"
    done

    # Arming the timer is safe and immediately useful: without a snapshot the
    # next reboot falls back to `claude --continue`, which guesses the
    # conversation.
    say "start the snapshot timer (safe now — it only records)"
    run systemctl start herdr-sessions-snapshot.timer

    # Seed the first snapshot by running the DRIVER, not `systemctl start
    # herdr-sessions-snapshot.service`. That unit Requires=herdr-server.service,
    # so starting it would also start herdr-server — which is exactly what must
    # not happen while a hand-started herdr holds the socket. A transitive start
    # like that can put herdr-server into a restart loop; this avoids provoking
    # it at all.
    say "seed the first snapshot"
    run env HERDR_PROFILE="$PROFILE_CONF" "$HERE/herdr-sessions.sh" snapshot

    echo
    echo "Done. Day to day: ./herdr-sessions {update,relaunch,status,snapshot,restore} [--dry-run]"
    echo "    boot -> herdr-sessions-update -> herdr-server -> herdr-sessions-restore (full resume, --rc per profile)"
    echo "    every 5 min -> snapshot of the live session(s)"
    echo
    echo "Deliberately NOT started: herdr-server (one is already running by hand;"
    echo "the unit takes over at the next reboot)."
fi
