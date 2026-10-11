#!/usr/bin/env python3
"""Landlock exec wrapper: an OS-level fence a worker process cannot lift.

    python3 tools/landlock_exec.py [--rw PATH]... [--ro PATH]...
                                   [--ro-file PATH]... [--keep-fd N]...
                                   [--require | --allow-unjailed] -- CMD ARGS...

Why: P0 measured that the ``systemd --user`` sandbox properties start but
enforce nothing on this host (``kernel.apparmor_restrict_unprivileged_userns=1``,
no bwrap), so an opencode/qoder worker leg can read any file the OS user can.
Landlock is enforced by the kernel for unprivileged processes, so a fence built
with it lives below the process: the jailed worker has no syscall that removes
a rule once ``landlock_restrict_self`` has run.

Platform: Linux only. **Windows has no Landlock** — a separate lane would need
a different mechanism (Job Objects / Restricted Tokens) and is not implemented
here; ``available()`` returns 0 and the module imports cleanly everywhere.

Fail closed: ``--require`` is the default. With no usable Landlock the wrapper
exits EX_CONFIG (78) and never execs, so a lane that asked for a jail cannot
silently run without one. ``--allow-unjailed`` is the only opt-out — it prints
``landlock-unavailable: running UNJAILED`` on stderr and execs anyway (P1b uses
it for non-private lanes at most).

Inherited fds: Landlock cannot revoke access an open fd already holds, so
before exec every fd >= 3 not named by ``--keep-fd`` is marked close-on-exec
and closed by the exec itself. stdin/stdout/stderr (0, 1, 2) are deliberately
left alone — the wrapper must not break the caller's pipes.

Grant semantics (PATH_BENEATH, recursive, masked to what the running kernel's
Landlock ABI supports):

* ``--rw DIR``      read + write + create/remove/truncate + exec beneath DIR
* ``--ro DIR``      read + exec beneath DIR
* ``--ro-file FILE` read of that one file
* a path that does not exist is skipped silently (templates and optional
  distro dirs are passed the same way on every host)

Everything not granted is denied for the fs rights this process handles —
including the whole of ``$HOME`` outside the grants, which is the point.

The minimal grant set a jailed ``python3`` + ``git init && git commit`` needs,
found by iteration on Ubuntu 24.04 x86_64, kernel 7.0.0-34, Landlock ABI v8
(everything else the first draft listed — ``/etc/gitconfig``, ``/etc/ssl``,
``/etc/ld.so.cache``, ``/etc/passwd``, ``/etc/nsswitch.conf``,
``/etc/alternatives``, ``/proc``, ``/sys`` — turned out *not* to be opened, so
it is not granted):

    --rw  <work dir>       the workspace itself
    --ro  /usr             git, python3, their shared libs and stdlib
    --ro  /bin /lib        the same directories on a split-usr host (/bin and
                           /lib are symlinks into /usr on a merged-usr one, so
                           granting /usr alone already works here; both are
                           named because the wrapper cannot know which host it
                           is handed)
    --ro  /lib64           the x86_64 ld-linux loader, likewise a symlink here
    --rw  /dev             git opens /dev/null *read-write* as its discard
                           target: --ro is not enough, and WRITE_FILE on a
                           device node needs the directory to be --rw. Unix
                           DAC still applies underneath, so this grants no
                           device the user could not already write

What the discovery run measured, each point asserted by a case in
``tests/test_landlock_exec.py``:

* ``/proc`` and ``/sys`` are policed by this kernel's Landlock, so leaving
  them ungranted denies ``/proc/self/environ``, ``/proc/1/cmdline`` and
  ``/sys/kernel/security/lsm`` to the worker. Older Landlock carved
  pseudo-filesystems out; check the gap tests before assuming either way.
* A ``PYTHONPATH``/``PYTHONUSERBASE`` entry outside the grants does not merely
  shadow: it breaks python's startup with ``ModuleNotFoundError: No module
  named 'encodings'`` from an interpreter that is itself inside the grants.
  Launch with a sanitized env (or grant the entries) rather than debugging the
  jail.
* A cross-directory rename/link into or out of the jail fails with **EXDEV**,
  not EACCES, because ``REFER`` is withheld from ``--rw``: code that treats
  EXDEV as "same filesystem, retry" must not read it as transient here.
  Same-directory renames — git's whole locking strategy — work unchanged.
* A symlink *planted inside* a grant that points outside it is still denied:
  PATH_BENEATH is enforced on the resolved inode, so the classic
  ``repo/config -> ~/.ssh/id_ed25519`` trick leaves nothing for a privileged
  reader to follow.

What this fence does not cover, measured (``tests/test_landlock_exec.py``
asserts each one so a kernel that changes the answer is noticed): a
**pathname or abstract AF_UNIX** socket outside the grants stays connectable,
AF_INET **loopback** stays reachable because ``handled_access_net`` is left 0
on purpose (this is a filesystem fence), and **``stat()``** on an ungranted
path still returns size, mode and mtime — Landlock has no metadata right. A
lane's sockets and its config directory therefore have to be inside the grant
set deliberately; they are not reachable around it.

Python stdlib only (ctypes); no dependency on any other tool in this repo.
"""
import argparse
import ctypes
import os
import sys

EX_CONFIG = 78
UNJAILED_WARNING = "landlock-unavailable: running UNJAILED"

# Test-only seam: a host with a real Landlock still has to be able to prove the
# fail-closed path, and forcing the probe to report "no ABI" is the one way to
# do that without unloading a kernel LSM.
FORCE_ABI0_ENV = "LANDLOCK_EXEC_FORCE_ABI0"

SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1

PR_SET_NO_NEW_PRIVS = 38

# enum landlock_access_fs, upstream bit order. Directory listing is its own
# right (READ_DIR), removing is split per target type, and TRUNCATE/REFER/
# IOCTL_DEV arrived in ABI 3/2/6 — getting these wrong is not a crash but a
# jail that silently does not cover the operation it was written to block.
FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13          # ABI 2
FS_TRUNCATE = 1 << 14       # ABI 3
FS_IOCTL_DEV = 1 << 15      # ABI 6

# {highest fs ABI generation: the rights it knows}. An ABI only ever adds
# rights, so a newer kernel supports every older mask; a right a newer kernel
# invented that is not in this table stays *unhandled*, i.e. unrestricted, and
# widening the table is what closes that.
FS_GEN_RIGHTS = [
    (6, (1 << 16) - 1),                              # + IOCTL_DEV
    (3, (1 << 15) - 1),                              # + TRUNCATE, no ioctl
    (2, (1 << 14) - 1),                              # + REFER
    (1, (1 << 13) - 1),                              # the original set
]

# Everything a --rw directory gets. REFER is deliberately absent: it is what
# lets a file be renamed or hardlinked *into another directory*, and
# same-directory renames (git's whole locking strategy) do not need it.
RW_RIGHTS = (FS_EXECUTE | FS_READ_FILE | FS_WRITE_FILE | FS_READ_DIR
             | FS_REMOVE_DIR | FS_REMOVE_FILE | FS_MAKE_CHAR | FS_MAKE_DIR
             | FS_MAKE_REG | FS_MAKE_SOCK | FS_MAKE_FIFO | FS_MAKE_BLOCK
             | FS_MAKE_SYM | FS_TRUNCATE)
# A --ro directory is opened, listed, executed and read — nothing else.
RO_RIGHTS = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
RO_FILE_RIGHTS = FS_READ_FILE


class LandlockError(Exception):
    """A Landlock syscall failed for a reason other than 'no ABI here'."""


def _libc():
    try:
        return ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        try:
            return ctypes.CDLL(None, use_errno=True)
        except OSError:
            return None


def _linux():
    return sys.platform.startswith("linux")


def abi_for_kernel():
    """The Landlock ABI the kernel reports, 0 if there is none (uncached)."""
    if not _linux():
        return 0
    libc = _libc()
    if libc is None:
        return 0
    try:
        return _syscall(libc, SYS_LANDLOCK_CREATE_RULESET,
                        ctypes.c_void_p(None), ctypes.c_size_t(0),
                        ctypes.c_uint(LANDLOCK_CREATE_RULESET_VERSION))
    except LandlockError:
        # EACCES: Landlock is not in the kernel's LSM order. Anything else is
        # the same answer for the caller — this host cannot jail.
        return 0


_cached = {}


def available():
    """The kernel's Landlock ABI version, or 0 when there is no usable jail.

    0 is also the answer off Linux and the answer with
    ``LANDLOCK_EXEC_FORCE_ABI0=1`` — the three states a caller has to treat
    identically, because none of them can enforce anything.

    Cached for the process: the answer cannot change under a running process
    (the ABI is fixed at boot), and ``jail_argv`` callers ask twice — once to
    decide, once to report. The tests clear ``_cached`` to re-measure.
    """
    if "abi" not in _cached:
        forced = os.environ.get(FORCE_ABI0_ENV) == "1"
        _cached["abi"] = 0 if forced else abi_for_kernel()
    return _cached["abi"]


def fs_mask(abi):
    """The widest fs-rights mask this table believes the ABI knows."""
    if abi <= 0:
        return 0
    for generation, rights in FS_GEN_RIGHTS:
        if abi >= generation:
            return rights
    return FS_GEN_RIGHTS[-1][1]


def fs_mask_candidates(abi):
    """Widest first, then every narrower generation.

    The table can over-claim (a patched or older-than-documented Landlock that
    rejects a bit it never learned): a ruleset that cannot be built must not
    become a jail that silently covers less than it says, so the wrapper tries
    each generation until the kernel accepts one.
    """
    if abi <= 0:
        return []
    return [rights for _, rights in FS_GEN_RIGHTS if rights <= fs_mask(abi)]


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("handled_access_block", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64),
                ("parent_fd", ctypes.c_int32)]


def _syscall(libc, number, *args):
    """libc.syscall with every argument widened to a machine word.

    argtypes are deliberately left unset: `byref()` of a struct needs to stay
    a pointer, and `syscall` is variadic so ctypes cannot infer either way.
    A bare Python int would be passed as a C int, which truncates a 64-bit
    pointer on the way, so ints become c_long and nothing else is coerced.
    """
    converted = []
    for arg in args:
        if isinstance(arg, int) and not isinstance(arg, ctypes.c_long):
            converted.append(ctypes.c_long(arg))
        else:
            converted.append(arg)
    ctypes.set_errno(0)
    libc.syscall.restype = ctypes.c_long
    result = libc.syscall(ctypes.c_long(number), *converted)
    errno = ctypes.get_errno()
    if isinstance(result, int) and result < 0:
        raise LandlockError("syscall %d failed: %s"
                            % (number, os.strerror(errno or -result)))
    return result


def _open_beneath(path, directory):
    """O_RDONLY fd on `path`, or None if it is not there (skip silently)."""
    flags = os.O_RDONLY | os.O_CLOEXEC
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return os.open(path, flags)
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return None
    except IsADirectoryError:
        return None


def _create_ruleset(libc, handled):
    attr = _RulesetAttr()
    attr.handled_access_fs = handled
    attr.handled_access_net = 0
    attr.handled_access_block = 0
    # size 8 = only handled_access_fs: valid on every ABI from 1 up, and net
    # is left unhandled so a jailed worker keeps the plain TCP it needs.
    return _syscall(libc, SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr),
                    ctypes.c_size_t(ctypes.sizeof(ctypes.c_uint64)),
                    ctypes.c_uint(0))


def build_ruleset(libc, abi, grants):
    """Create the ruleset and add one PATH_BENEATH rule per grant.

    `grants` is [(path, directory, allowed_rights)]. Returns (ruleset_fd,
    handled_mask) — the mask is what the kernel actually accepted, which is
    what every rule gets masked down to. The caller closes the fd (or execs
    and lets the close-on-exec sweep do it).
    """
    last_error = None
    fd = handled = None
    for candidate in fs_mask_candidates(abi):
        try:
            fd = _create_ruleset(libc, candidate)
            handled = candidate
            break
        except LandlockError as exc:
            last_error = exc
    if fd is None:
        raise LandlockError("no fs-rights mask accepted: %s" % last_error)
    try:
        for path, directory, rights in grants:
            rights &= handled
            if not rights:
                continue
            below = _open_beneath(path, directory)
            if below is None:
                continue
            try:
                rule = _PathBeneathAttr()
                rule.allowed_access = rights
                rule.parent_fd = below
                _syscall(libc, SYS_LANDLOCK_ADD_RULE, ctypes.c_int(fd),
                         ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
                         ctypes.byref(rule), ctypes.c_uint(0))
            finally:
                os.close(below)
        return fd, handled
    except BaseException:
        os.close(fd)
        raise


def set_no_new_privs(libc):
    """prctl(PR_SET_NO_NEW_PRIVS) — landlock_restrict_self requires it.

    libc.prctl rather than a raw syscall number: prctl is 157 on x86_64 but
    167 under the generic ABI aarch64 uses, and only the landlock trio is
    architecturally fixed at 444/445/446.
    """
    ctypes.set_errno(0)
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(ctypes.c_int(PR_SET_NO_NEW_PRIVS), ctypes.c_ulong(1),
                  ctypes.c_ulong(0), ctypes.c_ulong(0),
                  ctypes.c_ulong(0)) != 0:
        raise LandlockError("prctl(PR_SET_NO_NEW_PRIVS) failed: %s"
                            % os.strerror(ctypes.get_errno()))


def apply_jail(grants):
    """Enforce `grants` on this process. Raises if the jail cannot be built."""
    abi = available()
    if abi <= 0:
        raise LandlockError("no Landlock ABI")
    libc = _libc()
    if libc is None:
        raise LandlockError("no libc")
    fd, _handled = build_ruleset(libc, abi, grants)
    try:
        set_no_new_privs(libc)
        _syscall(libc, SYS_LANDLOCK_RESTRICT_SELF, ctypes.c_int(fd),
                 ctypes.c_uint(0))
    finally:
        os.close(fd)
    return abi


def sweep_fds(keep=()):
    """Mark every fd >= 3 outside `keep` close-on-exec, so exec drops it.

    Landlock only governs the *opening* of files; an fd inherited from a
    process that was never jailed stays usable whatever the rules say. The
    sweep is what makes "the jail covers everything the child can reach"
    true. 0/1/2 are left as they are: they are the caller's pipes.
    """
    keep = set(int(k) for k in keep)
    seen = []
    try:
        with os.scandir("/proc/self/fd") as entries:
            for entry in entries:
                try:
                    seen.append(int(entry.name))
                except ValueError:
                    continue
    except OSError:
        pass
    if not seen:
        # no procfs: sweep the conventional range and let EBADF sort it out
        seen = list(range(3, 1024))
    for number in seen:
        if number < 3 or number in keep:
            continue
        try:
            os.set_inheritable(number, False)
        except OSError:
            continue
    return [n for n in seen if n >= 3 and n not in keep]


def grants_from(rw=(), ro=(), ro_file=()):
    """[(path, is_dir, rights)] in flag order.

    A --rw/--ro path that turns out to be a file is refused by the kernel-side
    open (O_DIRECTORY), so it gets no rule: a file has no "beneath" for the
    write rights to apply to. --ro-file is the flag for that case.
    """
    grants = []
    for path in rw or ():
        grants.append((path, True, RW_RIGHTS))
    for path in ro or ():
        grants.append((path, True, RO_RIGHTS))
    for path in ro_file or ():
        grants.append((path, False, RO_FILE_RIGHTS))
    return grants


def jail_argv(rw=(), ro=(), ro_file=(), cmd=(), require=True):
    """The argv that runs `cmd` inside this jail (P1b: prefix a worker launch).

    `--keep-fd` is not a parameter on purpose — a caller that needs an fd
    survived into the child appends it to what this returns, so the flag
    stays one source of truth (this module's parser).
    """
    argv = [sys.executable, os.path.abspath(__file__)]
    argv.append("--require" if require else "--allow-unjailed")
    for path in rw or ():
        argv += ["--rw", str(path)]
    for path in ro or ():
        argv += ["--ro", str(path)]
    for path in ro_file or ():
        argv += ["--ro-file", str(path)]
    argv.append("--")
    argv += [str(c) for c in cmd or ()]
    return argv


def _split_on_double_dash(argv):
    for index, item in enumerate(argv):
        if item == "--":
            return argv[:index], argv[index + 1:]
    return argv, []


def build_parser():
    parser = argparse.ArgumentParser(
        prog="landlock_exec.py",
        description="Run CMD inside a Landlock jail (Linux only).")
    parser.add_argument("--rw", action="append", default=[], metavar="PATH",
                        help="grant read+write+create/remove+exec beneath "
                             "PATH (repeatable)")
    parser.add_argument("--ro", action="append", default=[], metavar="PATH",
                        help="grant read+exec beneath PATH (repeatable)")
    parser.add_argument("--ro-file", dest="ro_file", action="append",
                        default=[], metavar="PATH",
                        help="grant read of the single file PATH (repeatable)")
    parser.add_argument("--keep-fd", dest="keep_fd", action="append",
                        default=[], metavar="N",
                        help="do NOT close this inherited fd at exec "
                             "(repeatable; fd 0/1/2 are always kept)")
    fail = parser.add_mutually_exclusive_group()
    fail.add_argument("--require", action="store_true", default=True,
                      help="fail closed: exit %d instead of execing with no "
                           "Landlock (the default)" % EX_CONFIG)
    fail.add_argument("--allow-unjailed", dest="allow_unjailed",
                      action="store_true",
                      help="warn and exec anyway with no Landlock")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    head, cmd = _split_on_double_dash(argv)
    parser = build_parser()
    opts = parser.parse_args(head)
    if not cmd:
        parser.error("no command after --")
    require = not opts.allow_unjailed
    try:
        keep_fd = [int(k) for k in opts.keep_fd]
    except ValueError:
        parser.error("--keep-fd takes a number")  # raises SystemExit(2)

    abi = available()
    if abi > 0:
        try:
            apply_jail(grants_from(opts.rw, opts.ro, opts.ro_file))
        except LandlockError as exc:
            if require:
                sys.stderr.write("landlock-apply-failed: %s (refusing to run "
                                 "unjailed)\n" % exc)
                return EX_CONFIG
            sys.stderr.write("%s: jail failed: %s\n" % (UNJAILED_WARNING, exc))
    elif require:
        sys.stderr.write("landlock-unavailable: no usable Landlock ABI; "
                         "refusing to exec (--require). Use --allow-unjailed "
                         "to run without a jail.\n")
        return EX_CONFIG
    else:
        sys.stderr.write("%s\n" % UNJAILED_WARNING)

    sweep_fds(keep_fd)
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:
        sys.stderr.write("landlock-exec: cannot run %s: %s\n"
                         % (cmd[0], exc.strerror))
        return 127
    return 0  # not reached: execvp replaces this process


if __name__ == "__main__":
    sys.exit(main())
