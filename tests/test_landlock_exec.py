#!/usr/bin/env python3
"""P1a / AO-OPENCODE-JAIL: `tools/landlock_exec.py` really fences a child.

Every claim here is measured with a **real subprocess against the real kernel**
(R-orch-19 — an unmocked Landlock test is the only kind that means anything: a
mocked one proves the mock). Nothing is faked except the deliberate
`LANDLOCK_EXEC_FORCE_ABI0=1` seam, which exists so a host *with* Landlock can
still prove the fail-closed path.

Run from the repo root:

    python3 tests/test_landlock_exec.py

With no Landlock ABI (non-Linux, or a kernel without the LSM) every jail case
reports as skipped and only the pure-logic cases run.

The grant set below was found by iteration on Ubuntu 24.04 x86_64, kernel
7.0.0-34, Landlock ABI v8:

    --rw  <work dir>             the workspace
    --ro  /usr /bin /lib /lib64   git, python3, their libs and stdlib (/bin and
                                  /lib are /usr symlinks here; both are named
                                  because a split-usr host needs each)
    --rw  /dev                   git opens /dev/null read-write, so the discard
                                  target needs WRITE_FILE — --ro is not enough
    no /proc, no /sys, no /etc   turned out unnecessary for
                                  `git init && git commit`, and this kernel's
                                  Landlock does police them, so ungranted is
                                  the stricter choice
    HOME / XDG_CONFIG_HOME point at the work dir: git reads `$HOME/.gitconfig`
                                  and `$XDG_CONFIG_HOME/git/ignore` and is
                                  denied — correct behaviour, not a hole to
                                  grant around, and the reason a lane needs
                                  its config dir in the grant set explicitly

What Landlock did NOT stop, measured (asserted by the two gap tests, so a
kernel that closes one fails here instead of quietly changing the threat
model):

* AF_UNIX with a **pathname** outside every grant — a jailed child connected
  to the parent's `/tmp/….sock` without trouble; so does an **abstract**
  socket, which has no path to police.
* AF_INET **loopback** connect — `handled_access_net` is left 0 by design, so
  this is a filesystem fence and the worker keeps networking.
* **`stat()` metadata** of an ungranted file: size, mode and mtime of
  `$HOME/<anything>` stay readable. Landlock has no metadata right, so path
  probing is not blocked.
* `/proc/self/mem` is not evidence either way: reading it already fails for a
  same-uid process with no Landlock in the picture (EIO).

Everything else probed WAS blocked, including `/proc/self/environ`,
`/proc/self/maps`, `/proc/1/cmdline`, the `/proc` listing,
`/sys/kernel/security/lsm`, `/etc/passwd` and the `$HOME` listing.
"""
import errno
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "tools" / "landlock_exec.py"

# The env a jailed python3 needs. PYTHONPATH / PYTHONUSERBASE point into $HOME
# on this host, and an ungranted sys.path entry breaks python's startup
# outright ("ModuleNotFoundError: No module named 'encodings'" from a granted
# interpreter), so it is dropped rather than granted around. The rest is the
# AutoOS lane's own config, naming a private directory the jail denies.
DROPPED_ENV = ("PYTHONPATH", "PYTHONUSERBASE", "GIT_CONFIG_GLOBAL",
               "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_VALUE_0",
               "GIT_CONFIG_VALUE_1", "GIT_CONFIG_KEY_0", "GIT_CONFIG_KEY_1",
               "GIT_CONFIG_PARAMETERS", "GIT_EXEC_PATH")

# See the docstring: this is what a jailed python3 + git actually opens.
WORK_RO = ("/usr", "/bin", "/lib", "/lib64")
WORK_RW = ("/dev",)

EXIT_CONFIG = 78


def load_wrapper(name="landlock_exec_under_test"):
    spec = importlib.util.spec_from_file_location(name, str(WRAPPER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LANDLOCK = load_wrapper()
ABI = LANDLOCK.available()

READ_PROBE = """
try:
    data = open(PATH, "rb").read()
except PermissionError:
    print("PERMISSION-DENIED")
except OSError as exc:
    print("OTHER-ERROR", type(exc).__name__, exc.errno)
else:
    print("READ-LEAK", data[:16])
""".replace("PATH", "%s")

LIST_PROBE = """
try:
    names = os.listdir(PATH)
except PermissionError:
    print("PERMISSION-DENIED")
except OSError as exc:
    print("OTHER-ERROR", type(exc).__name__, exc.errno)
else:
    print("LIST-LEAK", len(names))
""".replace("PATH", "%s")


def probe_at(template, path):
    """A probe with the target inlined — argv after `-c CODE` is not the
    child's argv, so there is nothing to hand it out there."""
    return template % repr(str(path))


def _rmtree(path):
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def _fd_closed(fd):
    try:
        os.fstat(fd)
        return False
    except OSError:
        return True


class _JailFixture(unittest.TestCase):
    """Dirs, a sanitized env, and the one way to run something in a jail.

    Each fixture dir is registered with `addCleanup`, so a *failing* assertion
    still leaves /tmp and $HOME as it found them — this host's /tmp already
    carries ~150 directories from runs that did not do that.
    """

    def setUp(self):
        if ABI <= 0:
            self.skipTest("no Landlock ABI on this host (kernel without the "
                          "LSM, or not Linux) — nothing to measure")
        self.home_dir = tempfile.mkdtemp(prefix="autoos-landlock-",
                                         dir=os.environ["HOME"])
        self.addCleanup(_rmtree, self.home_dir)
        self.grant = tempfile.mkdtemp(prefix="autoos-landlock-grant-")
        self.addCleanup(_rmtree, self.grant)
        self.canary = os.path.join(self.home_dir, "canary.txt")
        self.canary_text = "SECRET-CANARY-DO-NOT-READ\n"
        with open(self.canary, "w", encoding="utf-8") as handle:
            handle.write(self.canary_text)

    def env(self, **overrides):
        env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
        env["GRANT_DIR"] = self.grant
        env.update({k: str(v) for k, v in overrides.items() if v is not None})
        return env

    def child_env(self, **overrides):
        """HOME and XDG inside the grant: the jailed tool writes no state
        outside it, and reads no config outside it."""
        return self.env(HOME=self.grant, XDG_CONFIG_HOME=self.grant,
                        **overrides)

    def run_jailed(self, cmd, rw=WORK_RW, ro=WORK_RO, ro_file=(), extra=(),
                   env=None, pass_fds=(), stdin=None, require=True):
        """One real subprocess: wrapper -> landlock_restrict_self -> execvp.

        The work dir is always the first --rw: every test here jails, and a
        jail that cannot write its own directory cannot be probed. `require`
        picks the fail-closed flag, which is the default the wrapper itself
        carries — the two are mutually exclusive, so it cannot be an extra.
        """
        argv = [sys.executable, str(WRAPPER),
                "--require" if require else "--allow-unjailed",
                "--rw", self.grant]
        for path in rw or ():
            argv += ["--rw", path]
        for path in ro or ():
            argv += ["--ro", path]
        for path in ro_file or ():
            argv += ["--ro-file", path]
        argv += list(extra or ())
        argv.append("--")
        argv += list(cmd)
        return subprocess.run(argv, capture_output=True, text=True,
                              env=self.child_env() if env is None else env,
                              pass_fds=tuple(pass_fds), input=stdin,
                              cwd=self.grant, timeout=180)

    def py(self, code, **kw):
        return self.run_jailed([sys.executable, "-c", code], **kw)

    def assertDenies(self, result, what):
        """The probe must have hit PermissionError, not escaped or crashed."""
        self.assertEqual(result.returncode, 0,
                         "the jailed probe crashed: %s\n%s"
                         % (result.stdout, result.stderr))
        self.assertIn("PERMISSION-DENIED", result.stdout,
                      "%s was not denied — Landlock did not fence it: %r"
                      % (what, result.stdout))

    def assertAllows(self, result, marker):
        self.assertEqual(result.returncode, 0,
                         "the jailed probe failed: %s\n%s"
                         % (result.stdout, result.stderr))
        self.assertIn(marker, result.stdout, result.stdout)

    def accept_now(self, listener):
        """accept() with a deadline: a child that never connected must fail the
        test, not hang the suite."""
        listener.settimeout(30)
        try:
            conn, _ = listener.accept()
        except socket.timeout:
            self.fail("the jailed child never connected — a channel this test "
                      "measured as open is now fenced; update the gap list")
        conn.close()

    def listen(self, sock):
        sock.listen(1)
        self.addCleanup(sock.close)
        return sock


class OutsideIsDeniedTests(_JailFixture):
    """The reason the fence exists: a worker cannot read the user's home."""

    def test_the_parent_reads_what_the_child_is_denied(self):
        """Control first — a canary nobody could read would deny for free."""
        with open(self.canary, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), self.canary_text)
        self.assertIn("canary.txt", os.listdir(self.home_dir))

    def test_listing_a_directory_under_home_is_denied(self):
        result = self.py("import os\n" + probe_at(LIST_PROBE, self.home_dir))
        self.assertDenies(result, "listdir(%s)" % self.home_dir)

    def test_reading_a_canary_file_under_home_is_denied(self):
        result = self.py(probe_at(READ_PROBE, self.canary))
        self.assertDenies(result, "open(%s)" % self.canary)

    def test_reading_an_ungranted_world_readable_file_is_denied(self):
        """Not a secret by design, but proof the fence is about the grant set
        and not about file modes: 0644 under /etc is still denied."""
        path = "/etc/passwd"
        if not os.path.exists(path):
            self.skipTest("no %s on this host" % path)
        result = self.py(probe_at(READ_PROBE, path))
        self.assertDenies(result, "open(%s)" % path)

    def test_exec_of_an_ungranted_binary_is_denied(self):
        """The grant covers EXECUTE, so the wrapper cannot even start the child
        it was handed — the failure is in the wrapper, before any output."""
        result = self.run_jailed([sys.executable, "-c", "print('escaped')"],
                                 ro=(), rw=())
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("escaped", result.stdout, result.stdout)
        self.assertIn("cannot run", result.stderr, result.stderr)

    def test_creating_a_file_outside_the_grant_is_denied(self):
        target = os.path.join(self.home_dir, "planted.txt")
        result = self.py(
            "try:\n"
            "    open(%r, 'w').write('x')\n"
            "except PermissionError:\n"
            "    print('PERMISSION-DENIED')\n"
            "else:\n"
            "    print('WRITE-LEAK')\n" % target)
        self.assertDenies(result, "open(%s, 'w')" % target)
        self.assertFalse(os.path.exists(target),
                         "the denied write still created the file")

    def test_a_symlink_inside_the_grant_does_not_escape_it(self):
        """PATH_BENEATH is enforced on the resolved inode, not the path the
        caller wrote: a link planted *inside* the jail that points at
        /etc/passwd is still denied. (Creating the link is allowed — MAKE_SYM
        is part of --rw — which is why this asserts on the read, not the
        symlink call.)

        This matters because the alternative is the classic container escape:
        a worker writes `repo/config -> /home/user/.ssh/id_ed25519` and gets a
        privileged reader to follow it.
        """
        link = os.path.join(self.grant, "escape-link")
        os.symlink("/etc/passwd", link)
        self.addCleanup(lambda: os.path.islink(link) and os.remove(link))
        result = self.py(probe_at(READ_PROBE, link))
        self.assertDenies(result, "open(%s) through a granted symlink" % link)

    def test_a_missing_grant_path_is_skipped_not_fatal(self):
        """Optional and distro-dependent dirs are passed blindly on every
        host: a grant that is not there must not stop the jail being built."""
        result = self.py("import os\nprint('ALIVE', os.getcwd())",
                         ro=WORK_RO + ("/lib/autoos-no-such-dir",),
                         rw=WORK_RW + ("/autoos-no-such-dir",),
                         ro_file=("/autoos-no-such-file",))
        self.assertAllows(result, "ALIVE")

    def test_an_unjailed_child_still_could_read_home(self):
        """The same probe, with no jail at all, must succeed — otherwise every
        denial above could be a broken fixture rather than a fence.

        Only run when the *test process itself* is unjailed, which it is: this
        is the parent's own view of the child's command, minus the wrapper.
        """
        result = subprocess.run([sys.executable, "-c",
                                 "import os\n" + probe_at(
                                    LIST_PROBE, self.home_dir).replace(
                                    "LIST-LEAK", "UNJAILED-OK")],
                                capture_output=True, text=True, timeout=60,
                                env=self.env())
        self.assertIn("UNJAILED-OK", result.stdout,
                      result.stdout + result.stderr)


class InsideIsAllowedTests(_JailFixture):
    """The child that cannot read $HOME does everything a worker needs."""

    def test_read_write_create_remove_truncate_inside_the_grant(self):
        code = """
import os
os.chdir(os.environ["GRANT_DIR"])
open("a.txt", "w").write("one")
assert open("a.txt").read() == "one"
os.rename("a.txt", "b.txt")
os.mkdir("d")
with open("c.txt", "w") as h:
    h.write("more" * 100)
os.truncate("c.txt", 4)
assert os.stat("c.txt").st_size == 4
os.remove("c.txt")
made_dir = os.path.isdir("d")
os.rmdir("d")
print("RW-OK", made_dir, sorted(os.listdir(".")))
"""
        self.assertAllows(self.py(code), "RW-OK True ['b.txt']")

    def test_a_same_directory_rename_needs_no_refer_right(self):
        """git's locking is rename-over-target in one directory; REFER is the
        cross-directory right and is withheld, so this is the fence *not*
        over-reaching on the exact pattern that would otherwise break."""
        code = """
import os
os.chdir(os.environ["GRANT_DIR"])
os.makedirs("repo/.git", exist_ok=True)
open("repo/lock", "w").write("new")
os.rename("repo/lock", "repo/index")
print("RENAME-OK", open("repo/index").read())
"""
        self.assertAllows(self.py(code), "RENAME-OK new")

    def test_git_init_and_commit_inside_the_jail(self):
        """The real use: a worker that has to be able to commit, in a jail."""
        if shutil.which("git") is None:
            self.skipTest("no git on this host")
        code = """
import os, subprocess
def sh(*cmd):
    p = subprocess.run(list(cmd), capture_output=True, text=True)
    print(cmd[0], cmd[1], "rc=", p.returncode,
          (p.stderr or p.stdout).strip()[:120])
    return p.returncode
os.chdir(os.environ["GRANT_DIR"])
open("f.txt", "w").write("hello\\n")
rc = sh("git", "init", "-q", ".")
rc = rc or sh("git", "add", "f.txt")
rc = rc or sh("git", "-c", "user.name=jailed",
              # AGENTS.md rule 1: no e-mail *shape* in a tracked file, not
              # even the reserved .invalid TLD the other fixtures use (those
              # are named in scan-exclude.txt, and only two files may land
              # here). git records any string it is given.
              "-c", "user.email=jailed-no-address",
              "commit", "-q", "-m", "jailed commit")
rc = rc or sh("git", "log", "--format=committed %H")
print("GIT-RC", rc)
"""
        result = self.py(code)
        self.assertAllows(result, "GIT-RC 0")
        head = subprocess.run(["git", "-C", self.grant, "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              env=self.child_env(), timeout=60)
        self.assertEqual(head.returncode, 0, head.stderr)
        self.assertEqual(len(head.stdout.strip()), 40, head.stdout)

    def test_loopback_tcp_connect_still_works(self):
        """Net rights are deliberately unhandled, so a jailed worker keeps the
        loopback a lane needs — and this names the consequence out loud."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", 0))
        except OSError as exc:
            listener.close()
            self.skipTest("no loopback here: %s" % exc)
        self.listen(listener)
        port = listener.getsockname()[1]
        result = self.py(
            "import socket\n"
            "s = socket.socket()\n"
            "s.settimeout(20)\n"
            "s.connect(('127.0.0.1', %d))\n"
            "print('TCP-CONNECTED')\n" % port)
        self.accept_now(listener)
        self.assertAllows(result, "TCP-CONNECTED")


class InheritedFdTests(_JailFixture):
    """Landlock cannot revoke an open fd, so the wrapper closes them first."""

    def _hold(self, path):
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(lambda: None if _fd_closed(fd) else os.close(fd))
        return fd

    def test_an_inherited_open_fd_to_an_outside_file_is_closed_at_exec(self):
        outside = os.path.join(self.home_dir, "inherited.txt")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("OUTSIDE-FD-SECRET\n")
        held = self._hold(outside)
        result = self.py(
            "import os\n"
            "try:\n"
            "    print('LEAK-OPEN-FD', os.fstat(%d).st_size)\n"
            "except OSError as exc:\n"
            "    print('FD-CLOSED', type(exc).__name__, exc.errno)\n" % held,
            pass_fds=(held,))
        # EBADF, not EACCES: the fd does not exist in the child at all, which
        # is the sweep's doing — Landlock would have said "permission denied".
        self.assertAllows(result, "FD-CLOSED OSError %d" % errno.EBADF)

    def test_keep_fd_preserves_exactly_the_named_fd(self):
        inside = os.path.join(self.grant, "kept.txt")
        with open(inside, "w", encoding="utf-8") as handle:
            handle.write("KEEP-ME\n")
        held = self._hold(inside)
        result = self.py(
            "import os\n"
            "print('KEEP-SIZE', os.fstat(%d).st_size)\n" % held,
            extra=["--keep-fd", str(held)], pass_fds=(held,))
        self.assertAllows(result, "KEEP-SIZE 8")

    def test_stdin_stdout_stderr_are_never_swept(self):
        """0/1/2 are the caller's pipes. Sweeping them would break the very
        spawn path (P1b) that the wrapper is meant to sit in front of."""
        result = self.py(
            "import os, sys\n"
            "os.write(1, b'WROTE-TO-1\\n')\n"
            "print('SEE-STDIN', len(sys.stdin.read()))\n",
            stdin="four\n")
        self.assertAllows(result, "WROTE-TO-1")
        self.assertAllows(result, "SEE-STDIN 5")


class FailClosedTests(_JailFixture):
    """No Landlock means no run — unless the caller opted out on purpose."""

    MARKER_CODE = ("import os\n"
                   "open(os.path.join(os.environ['GRANT_DIR'], 'executed'), 'w')"
                   ".write('ran')\n"
                   "print('CHILD-RAN')\n")

    def _forced_abi0(self):
        env = self.child_env()
        env[LANDLOCK.FORCE_ABI0_ENV] = "1"
        return env

    def test_require_is_the_default_and_refuses_to_exec(self):
        result = self.run_jailed([sys.executable, "-c", self.MARKER_CODE],
                                 env=self._forced_abi0())
        self.assertEqual(result.returncode, EXIT_CONFIG,
                         "%s\n%s" % (result.stdout, result.stderr))
        self.assertIn("landlock-unavailable", result.stderr, result.stderr)
        self.assertNotIn("CHILD-RAN", result.stdout, result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.grant, "executed")),
                         "the wrapper exited 78 and ran the worker anyway")

    def test_allow_unjailed_warns_and_runs(self):
        result = self.run_jailed([sys.executable, "-c", self.MARKER_CODE],
                                 env=self._forced_abi0(), require=False)
        self.assertEqual(result.returncode, 0,
                         "%s\n%s" % (result.stdout, result.stderr))
        self.assertIn("CHILD-RAN", result.stdout, result.stdout)
        self.assertIn("UNJAILED", result.stderr, result.stderr)
        self.assertIn(LANDLOCK.UNJAILED_WARNING, result.stderr.splitlines(),
                      result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.grant, "executed")))

    def test_a_jailed_run_never_prints_the_unjailed_warning(self):
        result = self.py("print('JOKE')")
        self.assertAllows(result, "JOKE")
        self.assertNotIn("UNJAILED", result.stderr, result.stderr)

    def test_the_two_flags_are_mutually_exclusive(self):
        """--require --allow-unjailed would otherwise let a caller turn
        fail-closed off by accident — argparse refuses it."""
        result = subprocess.run(
            [sys.executable, str(WRAPPER), "--require", "--allow-unjailed",
             "--", "true"], capture_output=True, text=True, timeout=60,
            env=self.env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed with argument", result.stderr, result.stderr)


class AvailableOffLinuxTests(unittest.TestCase):
    """`available()` must answer 0 — not raise — wherever Landlock is not."""

    def _cleared(self):
        saved = dict(LANDLOCK._cached)
        LANDLOCK._cached.clear()
        return saved

    def test_the_force_env_reports_zero(self):
        saved_env = os.environ.get(LANDLOCK.FORCE_ABI0_ENV)
        saved_cache = self._cleared()
        os.environ[LANDLOCK.FORCE_ABI0_ENV] = "1"
        try:
            self.assertEqual(LANDLOCK.available(), 0)
        finally:
            if saved_env is None:
                del os.environ[LANDLOCK.FORCE_ABI0_ENV]
            else:
                os.environ[LANDLOCK.FORCE_ABI0_ENV] = saved_env
            LANDLOCK._cached.clear()
            LANDLOCK._cached.update(saved_cache)

    def test_a_non_linux_platform_reports_zero_without_a_syscall(self):
        """The Windows lane imports this module to build an argv. A top-level
        ctypes syscall would break the *import*, so the platform check has to
        come first and answer 0."""
        saved_linux, saved_cache = LANDLOCK._linux, self._cleared()
        try:
            LANDLOCK._linux = lambda: False
            self.assertEqual(LANDLOCK.abi_for_kernel(), 0)
            self.assertEqual(LANDLOCK.available(), 0)
            self.assertEqual(LANDLOCK.fs_mask(0), 0)
            self.assertEqual(LANDLOCK.fs_mask_candidates(0), [])
            self.assertEqual(LANDLOCK.grants_from(("/x",), (), ()),
                             [("/x", True, LANDLOCK.RW_RIGHTS)])
        finally:
            LANDLOCK._linux = saved_linux
            LANDLOCK._cached.clear()
            LANDLOCK._cached.update(saved_cache)

    def test_the_module_imported_and_states_its_own_abi(self):
        self.assertTrue(WRAPPER.is_file())
        self.assertIsInstance(ABI, int)
        self.assertGreaterEqual(ABI, 0)
        self.assertIn("Windows has no Landlock",
                      WRAPPER.read_text(encoding="utf-8"))


class RightsTableTests(unittest.TestCase):
    """The bit table *is* the fence: a wrong bit is a silent hole, not a crash.

    Measured the hard way on this host — with the old (wrong) table a jailed
    `ls` of /usr failed while `cat` of a file under it worked, because
    directory listing is its own right and the draft conflated REMOVE_DIR with
    bit 3.
    """

    def test_directory_listing_has_its_own_right_and_ro_grants_it(self):
        self.assertTrue(LANDLOCK.RO_RIGHTS & LANDLOCK.FS_READ_DIR)
        self.assertTrue(LANDLOCK.RO_RIGHTS & LANDLOCK.FS_READ_FILE)
        self.assertTrue(LANDLOCK.RO_RIGHTS & LANDLOCK.FS_EXECUTE)
        self.assertFalse(LANDLOCK.RO_RIGHTS & LANDLOCK.FS_WRITE_FILE)
        self.assertEqual(LANDLOCK.RO_FILE_RIGHTS, LANDLOCK.FS_READ_FILE)

    def test_rw_grants_what_a_build_needs_and_refer_is_not_one_of_them(self):
        for right in (LANDLOCK.FS_REMOVE_DIR, LANDLOCK.FS_REMOVE_FILE,
                      LANDLOCK.FS_MAKE_DIR, LANDLOCK.FS_MAKE_REG,
                      LANDLOCK.FS_TRUNCATE, LANDLOCK.FS_READ_DIR,
                      LANDLOCK.FS_WRITE_FILE, LANDLOCK.FS_EXECUTE):
            self.assertTrue(LANDLOCK.RW_RIGHTS & right, right)
        # REFER is what lets a file be renamed or linked *into another
        # directory*: withheld, so a jailed worker cannot move a file out of
        # the jail (measured: EXDEV, not EACCES, which is why callers must not
        # read a cross-directory EXDEV as a transient failure).
        self.assertFalse(LANDLOCK.RW_RIGHTS & LANDLOCK.FS_REFER)

    def test_the_enum_matches_upstreams_bit_order(self):
        names = [LANDLOCK.FS_EXECUTE, LANDLOCK.FS_WRITE_FILE,
                 LANDLOCK.FS_READ_FILE, LANDLOCK.FS_READ_DIR,
                 LANDLOCK.FS_REMOVE_DIR, LANDLOCK.FS_REMOVE_FILE,
                 LANDLOCK.FS_MAKE_CHAR, LANDLOCK.FS_MAKE_DIR,
                 LANDLOCK.FS_MAKE_REG, LANDLOCK.FS_MAKE_SOCK,
                 LANDLOCK.FS_MAKE_FIFO, LANDLOCK.FS_MAKE_BLOCK,
                 LANDLOCK.FS_MAKE_SYM, LANDLOCK.FS_REFER,
                 LANDLOCK.FS_TRUNCATE, LANDLOCK.FS_IOCTL_DEV]
        self.assertEqual(names, [1 << bit for bit in range(16)],
                         "landlock_access_fs bits moved — every grant set in "
                         "this repo is now wrong")

    def test_masks_only_widen_with_the_abi(self):
        previous = 0
        for abi in (1, 2, 3, 4, 5, 6, 7, 8, 99):
            mask = LANDLOCK.fs_mask(abi)
            self.assertGreaterEqual(mask, previous, abi)
            previous = mask
        self.assertEqual(LANDLOCK.fs_mask(0), 0)
        # a kernel that rejects a newer bit is asked again with less, widest
        # first, down to what ABI 1 always accepted
        candidates = LANDLOCK.fs_mask_candidates(6)
        self.assertEqual(candidates, sorted(candidates, reverse=True))
        self.assertEqual(candidates[0], LANDLOCK.fs_mask(6))
        self.assertEqual(candidates[-1], LANDLOCK.fs_mask(1))

    def test_only_the_generic_landlock_syscall_numbers_are_hardcoded(self):
        """444/445/446 hold on x86_64 and on the asm-generic ABI (aarch64,
        riscv). prctl is 157 there and 167 here, so it must go through
        `libc.prctl` — and nothing else may pass a bare number to `_syscall`."""
        self.assertEqual((LANDLOCK.SYS_LANDLOCK_CREATE_RULESET,
                          LANDLOCK.SYS_LANDLOCK_ADD_RULE,
                          LANDLOCK.SYS_LANDLOCK_RESTRICT_SELF),
                         (444, 445, 446))
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("libc.prctl(ctypes.c_int(PR_SET_NO_NEW_PRIVS)", text,
                      "set_no_new_privs stopped going through libc.prctl")
        for line in text.splitlines():
            if line.lstrip().startswith(("def ", "#")):
                continue
            if "_syscall(libc," in line:
                arg = line.split("_syscall(libc,")[1].split(",")[0].strip()
                self.assertTrue(arg.startswith("SYS_") or not arg,
                                "raw syscall number at %r — only the landlock "
                                "trio is architecturally fixed" % line.strip())


class RoFileGrantTests(_JailFixture):
    """What a grant covers, at the granularity P1b will need."""

    def test_ro_file_grants_read_of_that_one_file_and_nothing_around_it(self):
        """--ro-file is the flag for a named config file: read of the inode,
        no write, and its still-ungranted parent directory cannot be listed —
        path *lookup* needs no Landlock right, so naming it is enough.

        Measured, because it is the shape P1b will use for a lane's own
        config: grant the file, never the directory that holds the rest.
        """
        named = os.path.join(self.home_dir, "granted-by-name.txt")
        with open(named, "w", encoding="utf-8") as handle:
            handle.write("NAMED-ONE\n")
        sibling = os.path.join(self.home_dir, "sibling.txt")
        code = """
import os
p = os.environ["NAMED_FILE"]
print("READ", open(p).read().strip())
for what, thunk in (
        ("APPEND", lambda: open(p, "a").write("x")),
        ("SIBLING-WRITE", lambda: open(%r, "w").write("x")),
        ("PARENT-LIST", lambda: os.listdir(os.path.dirname(p)))):
    try:
        thunk()
    except PermissionError:
        print(what, "DENIED")
    else:
        print(what, "LEAK")
""" % sibling
        result = self.py(code, ro_file=(named,),
                         env=self.child_env(NAMED_FILE=named))
        self.assertAllows(result, "READ NAMED-ONE")
        for marker in ("APPEND DENIED", "SIBLING-WRITE DENIED",
                       "PARENT-LIST DENIED"):
            self.assertIn(marker, result.stdout, result.stdout)
        self.assertFalse(os.path.exists(sibling),
                         "the denied sibling write still created a file")

    def test_a_ro_directory_grant_covers_every_file_beneath_it(self):
        """The counterpart: PATH_BENEATH is recursive, so a --ro dir is a
        grant for everything inside it — which is why the work dir is --rw and
        `$HOME` is never granted at the top."""
        nested = os.path.join(self.grant, "deep", "er", "note.txt")
        os.makedirs(os.path.dirname(nested))
        with open(nested, "w", encoding="utf-8") as handle:
            handle.write("NESTED-OK\n")
        result = self.py("import os\n" + probe_at(READ_PROBE, nested)
                         .replace("READ-LEAK", "NESTED-READ"))
        # the probe prints the first bytes it read, repr'd: the escape is the
        # probe's, not a mismatch here
        self.assertAllows(result, "NESTED-READ b'NESTED-OK")


class JailArgvTests(unittest.TestCase):
    """What P1b prefixes a worker launch with."""

    def test_the_argv_names_the_jail_and_the_command_after_double_dash(self):
        argv = LANDLOCK.jail_argv(rw=["/w"], ro=["/usr"], ro_file=["/etc/c"],
                                  cmd=["opencode", "run"])
        self.assertEqual(argv[:2], [sys.executable, str(WRAPPER)])
        self.assertEqual(argv[2], "--require")
        self.assertIn("--", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["opencode", "run"])
        self.assertEqual(argv[argv.index("--rw") + 1], "/w")
        self.assertEqual(argv[argv.index("--ro") + 1], "/usr")
        self.assertEqual(argv[argv.index("--ro-file") + 1], "/etc/c")

    def test_require_false_asks_for_the_loud_opt_out(self):
        argv = LANDLOCK.jail_argv(rw=[], ro=[], ro_file=[], cmd=["true"],
                                  require=False)
        self.assertIn("--allow-unjailed", argv)
        self.assertNotIn("--require", argv)

    def test_the_built_argv_actually_jails_the_child(self):
        """Not a string check: the argv this module hands the integration is
        run and the fence it claims is measured."""
        if ABI <= 0:
            self.skipTest("no Landlock ABI on this host")
        grant = tempfile.mkdtemp(prefix="autoos-landlock-argv-")
        self.addCleanup(_rmtree, grant)
        home = tempfile.mkdtemp(prefix="autoos-landlock-",
                                dir=os.environ["HOME"])
        self.addCleanup(_rmtree, home)
        canary = os.path.join(home, "canary")
        with open(canary, "w", encoding="utf-8") as handle:
            handle.write("x")
        env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
        env.update(HOME=grant, XDG_CONFIG_HOME=grant)
        argv = LANDLOCK.jail_argv(
            rw=[grant], ro=list(WORK_RO) + ["/dev"],
            cmd=[sys.executable, "-c", probe_at(READ_PROBE, canary)])
        result = subprocess.run(argv, capture_output=True, text=True, env=env,
                                cwd=grant, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PERMISSION-DENIED", result.stdout,
                      "an argv built by jail_argv() ran the child unjailed: %r"
                      % result.stdout)


class MeasuredGapTests(_JailFixture):
    """The fence's limits, asserted so that a change is *noticed*.

    These pass today because Landlock lets the operation through. If a kernel
    closes one, the test fails here and the threat model gets re-read instead
    of quietly improving — that is the whole point of writing the leak down.
    """

    def test_stat_of_an_ungranted_file_is_still_allowed(self):
        """Landlock has no metadata right: size, mode and mtime of anything
        under $HOME stay readable. Names and sizes leak; content does not."""
        expected = os.stat(self.canary).st_size
        result = self.py(
            "import os\nst = os.stat(%r)\n"
            "print('STAT-LEAK', st.st_size, oct(st.st_mode), st.st_mtime > 0)\n"
            % self.canary)
        self.assertAllows(result, "STAT-LEAK %d" % expected)

    def test_a_pathname_unix_socket_outside_the_grant_is_connectable(self):
        """AF_UNIX by pathname is not policed (there is no socket right in any
        ABI this wrapper handles), so a socket under /tmp is a channel out of
        the jail. Consequence for P1b: a lane's D-Bus / agent sockets must be
        inside the grants deliberately, not reached around the fence."""
        path = os.path.join(tempfile.gettempdir(),
                            "autoos-landlock-probe-%d.sock" % os.getpid())
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(path)
        except OSError as exc:
            listener.close()
            self.skipTest("no AF_UNIX here: %s" % exc)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        self.listen(listener)
        result = self.py(
            "import socket\n"
            "s = socket.socket(socket.AF_UNIX)\n"
            "s.settimeout(20)\n"
            "s.connect(%r)\n"
            "print('UNIX-SOCKET-CONNECTED')\n" % path)
        self.accept_now(listener)
        self.assertAllows(
            result, "UNIX-SOCKET-CONNECTED")

    def test_proc_is_policed_on_this_kernel_and_denied_when_ungranted(self):
        """The one assumed to leak: `/proc/self/environ` of the *same* uid. On
        this kernel Landlock does police procfs, so it is denied — and this
        test pins which side of that line the host is on. A kernel that carves
        pseudo-filesystems out again fails here and reopens the question."""
        result = self.py(probe_at(READ_PROBE, "/proc/self/environ"))
        self.assertDenies(result, "/proc/self/environ")


if __name__ == "__main__":
    if ABI <= 0:
        print("landlock-unavailable: ABI %d — jail cases skip, pure-logic "
              "cases run" % ABI, file=sys.stderr)
    # the lane's report line: how many cases actually proved the fence
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("%d passed, %d failed, %d skipped (Landlock ABI %d)"
          % (result.testsRun - len(result.failures) - len(result.errors)
             - len(result.skipped),
             len(result.failures) + len(result.errors), len(result.skipped),
             ABI))
    sys.exit(0 if result.wasSuccessful() else 1)
