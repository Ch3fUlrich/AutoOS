#!/usr/bin/env bash
# AutoOS Taildrop fetcher + sorter (D-410): ONE process per host drains the
# shared Taildrop inbox and files what it finds by owner, so no agent session
# has to.
#
# Why one process: `tailscale file get` does not read "your" files, it drains an
# inbox the whole host shares. Whichever process fetches first takes every
# session's files, so a fetcher per session leaves all but one session with
# nothing and nobody can tell whose files went where. Sessions SEND with a name
# that names their owner (`autoos__notes.md`, `server__build.log`, ...); this
# script is the only thing allowed to fetch.
#
# What a run does: create the tree, fetch into $ROOT/incoming, then move each
# REGULAR FILE by NAME only:
#
#   <owner>__<rest of name>   -> $ROOT/<owner>/<rest of name>
#   anything else             -> $ROOT/unsorted/<name> + one line in sort.log
#
# The owner pattern below is a single path segment - lowercase letters, digits
# and dashes - so no name can carry a slash or a dot and no destination is ever
# built outside $ROOT. File CONTENT is never read, hashed, printed or compared:
# the bytes of a file are not this script's business, and everything it decides
# on it already fits in its name.
#
# Every failure is cheap. A missing or failing `tailscale`, or an inbox nothing
# has written yet, is one line on stderr and exit 0: a timer that reports an
# error every minute teaches people to ignore it, and the next minute's run
# starts clean anyway. Only a failure to MOVE a file is allowed to stop the
# script - the file is still in incoming/ and the next run finds it there.
set -euo pipefail

# Everything new this script creates lands private: 0700 for the directories
# (td_mkdir chmods them as well) and 0600 for sort.log, whatever umask the
# timer inherited from a login.
umask 077

# ROOT: the whole tree this script is allowed to write into. Never a path
# outside it - the owner below is the only part of any name that becomes a
# directory, and the pattern cannot express one.
ROOT="${AUTOOS_TAILDROP_ROOT:-$HOME/fleet/taildrop}"

# owner__rest-of-name -> owner/ rest-of-name. Anchored, and deliberately narrow:
# starts with a letter, then lowercase letters, digits or dashes only. No dot
# and no slash can survive it, so `..__x`, `a.b__c`, `a/b__c` and `A__x` are
# not owner names - they are ordinary files that land in unsorted/.
OWNER_RE='^([a-z][a-z0-9-]*)__(.+)$'

# td_mkdir <path>: create <path> at mode 0700, and say nothing when it is
# already there - a second run over the tree the first one made is a no-op, not
# an error. Two calls instead of `mkdir -p -m 0700` because -m alongside -p
# binds only to the deepest directory and shellcheck flags it (SC2174); -p
# creates any missing parent under the umask above, which makes it 0700 too.
td_mkdir() {
    if [[ -d "$1" ]]; then
        return 0
    fi
    mkdir -p -- "$1"
    chmod 700 -- "$1"
}

td_mkdir "$ROOT"
td_mkdir "$ROOT/incoming"
td_mkdir "$ROOT/unsorted"

if ! command -v tailscale >/dev/null 2>&1; then
    printf 'taildrop-sort: tailscale is not on PATH - nothing fetched\n' >&2
    exit 0
fi

# --wait=false: never block a timer on an idle inbox. --conflict=rename: a name
# the sender reused becomes name.1 instead of clobbering somebody's file before
# this script ever sees it.
if ! tailscale file get --wait=false --conflict=rename "$ROOT/incoming"; then
    printf 'taildrop-sort: tailscale file get failed - nothing fetched\n' >&2
    exit 0
fi

# One stamp for the whole run: every line written below describes one fetch.
stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# -print0 / read -d '': a file name may hold anything a filesystem allows -
# blanks and newlines above all - and none of it may reach the shell as syntax.
# -type f, no -L: regular files only, so a symlink left in the inbox can point
# nowhere interesting and is simply never touched.
while IFS= read -r -d '' path; do
    name="${path##*/}"
    if [[ "$name" =~ $OWNER_RE ]]; then
        dest_dir="$ROOT/${BASH_REMATCH[1]}"
        rest="${BASH_REMATCH[2]}"
        filed=0
    else
        dest_dir="$ROOT/unsorted"
        rest="$name"
        filed=1
    fi

    # A clash (the same sent name twice) appends .1, .2, ... - never overwrites.
    # `-e` also treats an entry that is a directory as taken, so `rest` can
    # never be "moved into".
    td_mkdir "$dest_dir"
    dest="$dest_dir/$rest"
    n=1
    while [[ -e "$dest" ]]; do
        dest="$dest_dir/$rest.$n"
        n=$((n + 1))
    done

    # mv keeps the file's own mode; nothing here opens it.
    mv -- "$path" "$dest"

    if (( filed )); then
        # Exactly one line per file: a name carrying a line break must not forge
        # a second log entry, so the break is written as a space.
        printf '%s unsorted %s\n' "$stamp" "${name//$'\n'/ }" >>"$ROOT/sort.log"
    fi
done < <(find "$ROOT/incoming" -mindepth 1 -maxdepth 1 -type f -print0)

exit 0