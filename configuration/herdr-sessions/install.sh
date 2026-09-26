#!/usr/bin/env bash
# install.sh — install (or refresh) the Herdr session autostart on one host.
#
# One script, two scopes, because "user units on a login host" and "system
# units as root on a headless host" used to be two forked scripts that drifted
# before being merged into this one. Idempotent: re-running with an unchanged
# profile reports "already current" for every unit; re-running with a changed
# profile backs up the differing unit before replacing it.
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
# --profile takes either a name under profiles/ (profiles/<name>.conf) or an
# absolute/relative PATH to a .conf anywhere else (any value containing a "/").
# Site-specific profiles live outside this repository (host paths, display
# names never belong in a public tracked file), so the path form is how a site
# points this installer at its own profile without copying it in here. The
# rendered units always carry the resolved absolute path, never a relative one
# a later `cd` could invalidate.
#
# --unregister disables and removes exactly the units this script would have
# installed for the given profile's scope, backing up each one first. A second
# --unregister (nothing left to remove) reports that and changes nothing.
#
# Usage:  ./install.sh --profile <name|path> [--dry-run] [--unregister]
#         A name is any profiles/<name>.conf; start from profiles/example.conf.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROFILE=""
DRY=0
UNREGISTER=0
while [ $# -gt 0 ]; do
    case "$1" in
        --profile) PROFILE="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        --unregister) UNREGISTER=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

avail() { ls -1 "$HERE/profiles/" 2>/dev/null | sed 's/\.conf$//' | paste -sd, - ; }

if [ -z "$PROFILE" ]; then
    echo "usage: $0 --profile {$(avail)|/path/to/your.conf} [--dry-run] [--unregister]" >&2
    exit 2
fi

# A profile is either a name under profiles/, or a path (anything containing a
# "/") to a .conf anywhere else -- typically a site's own private checkout.
# Resolved to an absolute path either way, since the rendered units must never
# carry a path a later `cd` could invalidate.
case "$PROFILE" in
    */*)
        [ -f "$PROFILE" ] || {
            echo "FATAL: no profile at $PROFILE (must be an existing regular file)" >&2; exit 1; }
        PROFILE_CONF="$(cd "$(dirname "$PROFILE")" && pwd)/$(basename "$PROFILE")"
        ;;
    *)
        PROFILE_CONF="$HERE/profiles/$PROFILE.conf"
        [ -f "$PROFILE_CONF" ] || {
            echo "FATAL: no profile at $PROFILE_CONF (have: $(avail))" >&2; exit 1; }
        ;;
esac

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

if [ "$SCOPE" = user ]; then
    DEST="$HOME/.config/systemd/user"
    SRC_DIR="$HERE/systemd/user"
    SYSTEMCTL_SCOPE=(--user)
else
    DEST="/etc/systemd/system"
    SRC_DIR="$HERE/systemd/system"
    SYSTEMCTL_SCOPE=()
fi

# _replace_token <line> <token> <value>: every occurrence of <token> in <line>
# replaced by the literal bytes of <value> -- plain split-and-concatenate, no
# pattern-substitution replacement field involved, so nothing in <value> is
# ever read as an escape or backreference (see render_unit below for why that
# matters here).
_replace_token() {
    local rest="$1" token="$2" value="$3" out=""
    while [[ "$rest" == *"$token"* ]]; do
        out+="${rest%%"$token"*}$value"
        rest="${rest#*"$token"}"
    done
    out+="$rest"
    printf '%s' "$out"
}

# Render a unit template into a real unit file. The checked-in units carry
# four literal tokens -- @PROFILE@ (the --profile argument as given, informational
# only), @APPDIR@ (where this checkout lives), @WORKDIR@ (the cwd panes
# inherit) and @PROFILE_PATH@ (the resolved absolute profile path, what
# HERDR_PROFILE is actually set to). Every replacement is an arbitrary
# filesystem path, so this does NOT use sed (its replacement text treats `&`
# as "the matched text" and `\` as an escape, both unescaped here) -- and,
# less obviously, does NOT use bash's own `${var/pattern/value}` either: on
# bash >= 5.2 (patsub_replacement, on by default) that construct has the exact
# same `&`-as-backreference behaviour as sed's replacement field. Plain
# split-and-concatenate (_replace_token) is immune to both.
render_unit() {
    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="$(_replace_token "$line" "@PROFILE@" "$PROFILE")"
        line="$(_replace_token "$line" "@APPDIR@" "$APPDIR")"
        line="$(_replace_token "$line" "@WORKDIR@" "$WORKDIR")"
        line="$(_replace_token "$line" "@PROFILE_PATH@" "$PROFILE_CONF")"
        printf '%s\n' "$line"
    done < "$1"
}

# unique_backup_path <path>: the next "<path>.autoos-backup-<ts>[-N]" name that
# is not already taken (file or symlink) -- the "two drifted runs in the same
# second must not overwrite the first backup" loop, shared by install_unit and
# remove_unit so the two cannot drift apart (qoder review finding 2: they once
# had two separate implementations and only one had the loop).
unique_backup_path() {
    local target="$1" backup base n=0
    backup="$target.autoos-backup-$(date +%Y%m%d%H%M%S)"
    base="$backup"
    while [ -e "$backup" ] || [ -L "$backup" ]; do
        n=$((n + 1)); backup="$base-$n"
    done
    printf '%s\n' "$backup"
}

# back_up_or_die <path>: copies <path> to unique_backup_path's next free name.
# Fail-closed, same as install_unit always was: a copy failure prints FATAL
# and exits the whole script, rather than letting a caller (remove_unit, in
# particular) carry on as though the only copy of the file had been saved.
# Success leaves the path it used in BACKUP_PATH -- a global, not a command
# substitution, so the exit above stops the real script, not just a $(...)
# subshell.
back_up_or_die() {
    local target="$1"
    BACKUP_PATH="$(unique_backup_path "$target")"
    if ! cp -p "$target" "$BACKUP_PATH"; then
        rm -f "$BACKUP_PATH"
        echo "FATAL: could not back up $target -- left it unchanged" >&2
        exit 1
    fi
}

# Idempotent: an unchanged render is left alone ("already current"); a changed
# one is backed up before being replaced, same as AGENTS.md rule 5 for any file
# this repository did not create from nothing.
install_unit() {  # $1=src template  $2=dest path
    local name; name="$(basename "$2")"
    if [ "$DRY" = 1 ]; then
        echo "  would: install -m 0644 (rendered $1) $2"
        return
    fi
    local tmp; tmp="$(mktemp)"
    render_unit "$1" >"$tmp"
    if [ -f "$2" ] && cmp -s "$tmp" "$2"; then
        echo "    $name: already current"
        rm -f "$tmp"
        return
    fi
    if [ -f "$2" ]; then
        back_up_or_die "$2"
        echo "    $name: differs from the installed copy -- backed up to $(basename "$BACKUP_PATH")"
    fi
    install -m 0644 "$tmp" "$2"
    rm -f "$tmp"
    CHANGED=$((CHANGED + 1))
    echo "    $name: installed"
}

# One line the AutoOS installer keys its "skipped" on (hard rule 3): printed
# only when every unit was already current, never after a partial change.
units_summary() {
    [ "$DRY" = 1 ] && return
    if [ "$CHANGED" = 0 ]; then
        echo "herdr-sessions: all units already current"
    else
        echo "herdr-sessions: $CHANGED unit(s) installed or replaced"
    fi
}
CHANGED=0

# --unregister: disable + back up + remove exactly the units this scope would
# install. Never errors on a unit that is not there (or not loaded) -- that is
# "nothing to remove", not a failure, and a second run must say exactly that.
remove_unit() {  # $1=dest path
    local dest="$1" name; name="$(basename "$dest")"
    [ -e "$dest" ] || return 1
    if [ "$DRY" = 1 ]; then
        echo "  would: disable $name, back it up, remove $dest"
        return 0
    fi
    # --now: stop a running service or timer (e.g. the snapshot timer, started
    # with --now at install time) before removing its unit -- disable alone
    # only stops it starting at the NEXT boot, leaving it running in memory
    # until then.
    systemctl "${SYSTEMCTL_SCOPE[@]}" disable --now "$name" >/dev/null 2>&1 || true
    back_up_or_die "$dest"
    rm -f "$dest"
    echo "    $name: disabled, backed up to $(basename "$BACKUP_PATH"), removed"
    return 0
}

UNITS=(herdr-sessions-update.service herdr-server.service herdr-sessions-restore.service
       herdr-sessions-snapshot.service herdr-sessions-snapshot.timer)

if [ "$UNREGISTER" = 1 ]; then
    if [ "$SCOPE" = system ] && [ "$DRY" != 1 ] && [ "$(id -u)" -ne 0 ]; then
        echo "must run as root" >&2; exit 1
    fi
    say "unregistering from $DEST"
    removed=0
    for u in "${UNITS[@]}"; do
        if remove_unit "$DEST/$u"; then removed=$((removed + 1)); fi
    done
    if [ "$removed" -eq 0 ]; then
        echo "nothing to remove"
    elif [ "$DRY" != 1 ]; then
        systemctl "${SYSTEMCTL_SCOPE[@]}" daemon-reload
        echo "removed $removed unit(s)"
    fi
    exit 0
fi

if [ "$SCOPE" = user ]; then
    # ── user scope: no root ─────────────────────────────────────────────────
    say "systemd user units -> $DEST"
    run mkdir -p "$DEST"
    for u in "${UNITS[@]}"; do
        install_unit "$SRC_DIR/$u" "$DEST/$u"
    done
    units_summary

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
    echo
    echo "Undo: ./install.sh --profile $PROFILE --unregister"
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

    say "systemd system units -> $DEST"
    for u in "${UNITS[@]}"; do
        install_unit "$SRC_DIR/$u" "$DEST/$u"
    done
    units_summary

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
    echo
    echo "Undo: ./install.sh --profile $PROFILE --unregister"
fi
