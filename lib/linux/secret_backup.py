#!/usr/bin/env python3
"""secret_backup.py - the copy AutoOS keeps of a file that holds a live credential.

The bytes about to be overwritten are secret: the previous bearer token stays a
live credential until the server expires it. The obvious tools do the opposite of
what that asks for:

  * ``cp -p`` and ``shutil.copy2`` create the destination at ``0666 & ~umask`` -
    0644 on a normal home, group- and world-readable - and only tighten it to the
    source's mode *after* the bytes landed. On a shared or NFS home that window is
    the leak, and a dotfile's own 0644 mode makes the copy readable forever.
  * a fixed name, or a name reused within the one-second stamp resolution,
    clobbers the previous backup - the user's original, not a stale copy.

So the backup is created ``O_CREAT | O_EXCL`` at 0600 in the same syscall that
makes it, the bytes are copied into that fd, and only the source's *times* are
carried across: copying the mode too (``shutil.copystat``) would widen it again.

The name shape is the one the whole repository uses - ``<path>.autoos-backup-<stamp>``,
then ``-1``, ``-2`` ... while a name is taken - so ``backup_newest`` and the undo
listing keep ranking these copies.

Two callers, one implementation (A3 review 4): the shell side runs this file as a
CLI from ``backup_file_before_write`` in ``install.sh``, and the omnigraph env
writer imports it *inside* the process that renames the new bytes into place,
because the backup has to land before the replace, not in a second run that could
disagree with the first about whether anything changed.
"""
import os
import shutil
import sys
import time


def secret_backup(path, stamp=None):
    """Copy <path> to a fresh 0600 backup and return the backup's name.

    <stamp> defaults to now (``YYYYmmdd-HHMMSS``); a caller - a test - may pin it.
    Raises OSError when the copy could not be made, leaving no partial file.
    """
    base = "%s.autoos-backup-%s" % (path, stamp or time.strftime("%Y%m%d-%H%M%S"))
    candidate, n, fd = base, 0, None
    while True:
        # O_EXCL is the check and the creation in one step, so a name that appears
        # between them cannot be written through, and a symlink nobody owns cannot
        # be followed into a victim file.
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            n += 1
            candidate = "%s-%d" % (base, n)
    try:
        with open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except OSError:
        try:
            os.unlink(candidate)
        except OSError:
            pass
        raise
    src_stat = os.stat(path)
    os.utime(candidate, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
    return candidate


def main(argv):
    if len(argv) < 2 or len(argv) > 3:
        sys.exit("usage: secret_backup.py <path> [stamp]")
    # The stamp is optional exactly as the usage line says - and the shell's call
    # site passes its own argument through, so an omitted one arrives as the empty
    # string here or, from a caller that leaves the argument off, as no third
    # element at all. Reading it unguarded turned the documented call into an
    # IndexError traceback and exit 1, which install.sh reads as "no backup" and
    # then refuses the whole rc-file edit.
    stamp = argv[2] if len(argv) > 2 else None
    try:
        print(secret_backup(argv[1], stamp))
    except OSError as exc:
        # A failure a shell caller has to notice, and nothing else: a traceback
        # would land in the install log a user reads when something broke.
        sys.exit("secret_backup: %s" % (exc,))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
