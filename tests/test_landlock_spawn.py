#!/usr/bin/env python3
"""AO-OPENCODE-JAIL P1c: the fence an --isolate worker cannot lift, tested LIVE.

Nothing here is mocked at the spawn. Every test puts a REAL client process
behind a REAL Landlock jail — and, where the host has a user manager, inside a
REAL `systemd-run --user --scope` unit — through `run_client`, and asks the
kernel the question the design rests on: can this worker read, write, signal or
connect to the thing it has no business touching?

The client is a tiny python program that runs the probes and writes a JSON
report INSIDE its sandbox, which the parent reads afterwards: an assertion about
what the child actually experienced, not about what the spawner planned. Two
program NAMES are exercised (`opencode`-shaped and `qodercli`-shaped), because
the name is what drives the `JAIL_RO_BY_CLIENT` lookup — the per-client read
grants differ, so the canary has to be denied on both legs, not on one.

Run from the repo root:

    python3 tests/test_landlock_spawn.py

The jail classes skip whole-file when this host is not Linux or has no Landlock
ABI (`available()` answers 0): there is no fence to measure then. The gate
class is the opposite — it drives the documented `LANDLOCK_EXEC_FORCE_ABI0`
seam, because what it measures is what happens when the fence is NOT there.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import threading
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agent = _load("autoos_agent_under_test", TOOLS / "autoos-agent.py")
landlock = _load("landlock_exec_under_test", TOOLS / "landlock_exec.py")

# Each probe answers "ok" (the operation went through), "denied" (EACCES/EPERM —
# the fence held) or the OSError's text. Expectations live in the parent, so a
# probe that could not run at all (git missing, no listener) is visible as such
# instead of quietly passing.
FAKE_CLIENT_SRC = r'''
import json, os, socket, subprocess, sys

cfg = json.load(open(sys.argv[1]))
out = {}


def probe(name, fn):
    try:
        fn()
        out[name] = "ok"
    except PermissionError:
        out[name] = "denied"
    except OSError as exc:
        out[name] = "%s: %s" % (exc.errno, exc.strerror)


def read(path):
    with open(path) as fh:
        fh.read()


def write(path):
    with open(path, "w") as fh:
        fh.write("stray\n")


probe("home_read", lambda: read(cfg["home_canary_file"]))
probe("home_list", lambda: os.listdir(cfg["home_canary_dir"]))
probe("home_write", lambda: write(os.path.join(cfg["home"], "planted.txt")))
probe("tmp_read", lambda: read(cfg["tmp_canary_file"]))
probe("tmp_write", lambda: write(cfg["tmp_canary_write"]))
probe("outside_read", lambda: read(cfg["outside_canary_file"]))
probe("sandbox_write", lambda: write(os.path.join(cfg["sandbox"], "inside.txt")))
probe("toolhome_write", lambda: write(os.path.join(cfg["toolhome"], "cache.txt")))


def git_status():
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=cfg["sandbox"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:200])
    return proc


try:
    git_status()
    out["git_status"] = "ok"
except Exception as exc:  # a jail refusal is an answer, not a crash
    out["git_status"] = "%s: %s" % (type(exc).__name__, exc)


def tcp():
    s = socket.create_connection(("127.0.0.1", cfg["tcp_port"]), timeout=5)
    s.sendall(b"ping")
    s.recv(16)
    s.close()


probe("loopback_tcp", tcp)


def unix_connect(kind, addr):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(addr)
    finally:
        s.close()


probe("abstract_unix", lambda: unix_connect(kind=0, addr=cfg["abstract_name"]))
probe("pathname_unix", lambda: unix_connect(kind=0, addr=cfg["pathname_socket"]))
probe("signal_parent", lambda: os.kill(cfg["parent_pid"], 0))

with open(os.path.join(cfg["sandbox"], "jail-report.json"), "w") as fh:
    json.dump(out, fh)
print("JAILED-CLIENT-DONE")
'''


class JailLiveTests(unittest.TestCase):
    """One fixture per test: a sandbox with its toolhome sibling, canaries outside."""

    def setUp(self):
        if not landlock._linux():
            self.skipTest("Landlock is a Linux feature; there is nothing to fence")
        if landlock.available() <= 0:
            self.skipTest("no Landlock ABI on this kernel: the jail is not offered")
        if os.environ.get("LANDLOCK_EXEC_FORCE_ABI0") == "1":
            self.skipTest("the no-Landlock seam is set; this class measures the jail")
        self.base = tempfile.mkdtemp(prefix="autoos-jail-test-")
        self.addCleanup(shutil.rmtree, self.base, True)
        # the on-disk fact `run_client` reads when it has no plan in hand: a
        # sandbox clone with its private `.toolhome` sibling beside it
        self.sandbox = os.path.join(self.base, "sandboxes", "sb-1")
        self.toolhome = self.sandbox + agent.TOOLHOME_SUFFIX
        for path in (self.sandbox, self.toolhome):
            os.makedirs(path, mode=0o700)
        subprocess.run(["git", "-c", "init.defaultBranch=test", "init", "-q",
                        self.sandbox], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        # a HOME of its own: the canaries sit under the fixture home, never the
        # operator's, and nothing here can outlive the run
        self.home = os.path.join(self.base, "home")
        self.home_canary_dir = os.path.join(self.home, "canary-dir")
        self.home_canary_file = os.path.join(self.home_canary_dir, "secret.txt")
        os.makedirs(self.home_canary_dir)
        self._write(self.home_canary_file, "not for the worker\n")
        # the one home directory the table grants to a qoder leg, read-only
        os.makedirs(os.path.join(self.home, ".qoder", "projects"))
        self._write(os.path.join(self.home, ".qoder", "projects", "journal.jsonl"),
                    '{"writer": "me"}\n')
        # the shared system temp: a directory no grant names
        self.tmp_dir = tempfile.mkdtemp(prefix="autoos-jail-tmp-", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp_dir, True)
        self.tmp_canary_file = os.path.join(self.tmp_dir, "canary.txt")
        self.tmp_canary_write = os.path.join(self.tmp_dir, "worker-may-not.txt")
        self._write(self.tmp_canary_file, "not for the worker either\n")
        # the parent-checkout analogue: outside the sandbox, outside the home
        self.outside = os.path.join(self.base, "parent-checkout")
        os.makedirs(self.outside)
        self.outside_canary = os.path.join(self.outside, "tracked.txt")
        self._write(self.outside_canary, "the leak P1b exists to stop\n")
        # the client lives INSIDE the sandbox: a jailed process can only read
        # what the jail names, and its own program must be readable
        self.client_src = os.path.join(self.sandbox, "fake_client.py")
        self._write(self.client_src, FAKE_CLIENT_SRC)
        self.bin = os.path.join(self.sandbox, "bin")
        os.makedirs(self.bin)
        self.report = os.path.join(self.sandbox, "jail-report.json")

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _stub(self, name):
        """An executable whose BASENAME is `name` — what the grant table keys on."""
        path = os.path.join(self.bin, name)
        self._write(path, "#!/bin/sh\nexec \"%s\" \"%s\" \"$@\"\n"
                    % (sys.executable, self.client_src))
        os.chmod(path, 0o755)
        return path

    def _env(self):
        env = {"HOME": self.home,
               "PATH": self.bin + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
               "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.invalid",
               "XDG_RUNTIME_DIR": os.path.join(self.base, "runtime"),
               "XDG_CONFIG_HOME": os.path.join(self.base, "config")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)
        os.makedirs(env["XDG_CONFIG_HOME"], mode=0o700, exist_ok=True)
        return env

    @staticmethod
    def _serve(listener):
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            try:
                conn.recv(8)
                conn.sendall(b"pong")
            except OSError:
                pass
            finally:
                conn.close()

    def _listeners(self):
        """The IPC endpoints a worker may (loopback TCP) or may not (unix, signal)
        reach. Sockets are closed on cleanup; the pathname one is a file in the
        fixture directory and goes with it."""
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.bind(("127.0.0.1", 0))
        tcp.listen(2)
        self.addCleanup(tcp.close)
        # a round trip, not just a SYN: an accepted connection needs someone on
        # the other side, and a worker that cannot talk to the gateway on
        # loopback is a jail that broke the run rather than fenced it
        thread = threading.Thread(target=self._serve, args=(tcp,), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.abstract_name = "\0autoos-jail-test-%d" % os.getpid()
        abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        abstract.bind(self.abstract_name)
        abstract.listen(2)
        self.addCleanup(abstract.close)
        self.pathname = os.path.join(self.base, "path.sock")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.pathname)
        sock.listen(2)
        self.addCleanup(sock.close)
        return tcp

    def _cfg(self, tcp):
        cfg = os.path.join(self.sandbox, "cfg.json")
        self._write(cfg, json.dumps({
            "home": self.home,
            "home_canary_file": self.home_canary_file,
            "home_canary_dir": self.home_canary_dir,
            "tmp_canary_file": self.tmp_canary_file,
            "tmp_canary_write": self.tmp_canary_write,
            "outside_canary_file": self.outside_canary,
            "sandbox": self.sandbox, "toolhome": self.toolhome,
            "tcp_port": tcp.getsockname()[1],
            "abstract_name": self.abstract_name,
            "pathname_socket": self.pathname,
            "parent_pid": os.getpid()}))
        return cfg

    def _spawn(self, client_name):
        """One LIVE launch: `run_client`, real Popen, real jail, real scope.

        run_id/attempt stay None: this is a test run with no canceller, and the
        runner-private kill store must not gain a record from the suite.
        """
        tcp = self._listeners()
        cmd = [self._stub(client_name), self._cfg(tcp)]
        out, err = io.StringIO(), io.StringIO()
        if os.path.exists(self.report):
            os.unlink(self.report)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = agent.run_client(cmd, self.sandbox, self._env(), reap=True,
                                  capture=True)
        self.spawn_log = out.getvalue() + err.getvalue()
        if not os.path.exists(self.report):
            self.fail("the jailed client wrote no report (rc=%s): %s"
                      % (int(rc), self.spawn_log[-3000:]))
        with open(self.report, encoding="utf-8") as fh:
            return rc, json.load(fh)

    def _assert_canaries(self, report, where):
        for probe in ("home_read", "home_list", "home_write", "tmp_read",
                      "tmp_write", "outside_read"):
            self.assertEqual(report.get(probe), "denied",
                             "%s: probe %s answered %r, expected denied"
                             % (where, probe, report.get(probe)))
        for probe in ("sandbox_write", "toolhome_write"):
            self.assertEqual(report.get(probe), "ok",
                             "%s: probe %s answered %r, expected ok"
                             % (where, probe, report.get(probe)))

    # --- the canary, on both per-client legs ----------------------------------
    def test_an_opencode_shaped_client_cannot_read_the_home_or_tmp_canary(self):
        rc, report = self._spawn("opencode")
        self.assertEqual(int(rc), 0, self.spawn_log[-2000:])
        self._assert_canaries(report, "opencode")

    def test_a_qoder_shaped_client_cannot_read_the_home_or_tmp_canary(self):
        rc, report = self._spawn("qodercli")
        self.assertEqual(int(rc), 0, self.spawn_log[-2000:])
        self._assert_canaries(report, "qodercli")
        self.assertEqual(agent.jail_client_entry([self._stub("qodercli"), "x"])
                         ["home_ro"], (".qoder",),
                         "the qoder leg no longer reads its own store")
        self.assertEqual(report.get("home_write"), "denied", report)
        planted = os.path.join(self.home, ".qoder", "projects", "planted.jsonl")
        self.assertFalse(os.path.exists(planted),
                         "a jailed qoder run wrote into the journal the FAMILYFENCE "
                         "reader trusts: it could choose who reviews it")

    def test_the_grant_table_never_names_an_operator_path(self):
        """Review item (b), stated once for every leg: a grant governs what is
        BENEATH it, so `~/.config` is as good as a grant on `~/.config/gh`, and
        an inherited XDG_ variable must not be able to smuggle either in."""
        home = os.path.expanduser("~")
        for name in sorted(agent.JAIL_RO_BY_CLIENT):
            entry = dict(agent.JAIL_RO_BY_CLIENT["default"])
            entry.update(agent.JAIL_RO_BY_CLIENT.get(name) or {})
            env = {"HOME": home,
                   "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                   "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
                   "XDG_RUNTIME_DIR": home}
            grants = agent.jail_grants("/tmp/whatever/sandbox", ["/usr/bin/" + name],
                                       env, entry)
            for kind, paths in sorted(grants.items()):
                for path in paths:
                    self.assertFalse(agent.jail_denied(path, home),
                                     "%s: grant %s=%s covers the operator's own "
                                     "files" % (name, kind, path))
                    self.assertNotEqual(os.path.realpath(path),
                                        os.path.realpath("/tmp"),
                                        "%s: grant %s=%s is the shared /tmp"
                                        % (name, kind, path))

    # --- what the worker still needs to be able to do --------------------------
    def test_a_jailed_client_writes_its_sandbox_and_runs_git_status(self):
        rc, report = self._spawn("opencode")
        self.assertEqual(report.get("sandbox_write"), "ok", report)
        self.assertEqual(report.get("toolhome_write"), "ok", report)
        self.assertEqual(report.get("git_status"), "ok",
                         "git failed inside the jail: %r" % report.get("git_status"))
        self.assertTrue(os.path.exists(os.path.join(self.sandbox, "inside.txt")))

    def test_a_jailed_client_keeps_the_loopback_it_needs(self):
        """handled_access_net is left 0 on purpose: a jailed worker still reaches
        the gateway on 127.0.0.1. A jail that broke that would break every run."""
        rc, report = self._spawn("opencode")
        self.assertEqual(report.get("loopback_tcp"), "ok", report)

    # --- the IPC scopes P1b added ---------------------------------------------
    def test_a_jailed_client_cannot_connect_an_abstract_unix_socket(self):
        """An abstract socket has no path, so no PATH_BENEATH rule can name one:
        only `scoped=ABSTRACT_UNIX_SOCKET` fences it (Landlock ABI 6+)."""
        if landlock.scope_mask(landlock.available()) == 0:
            self.skipTest("this ABI knows no IPC scopes (needs ABI 6)")
        rc, report = self._spawn("opencode")
        self.assertEqual(report.get("abstract_unix"), "denied",
                         "the abstract-socket scope did not hold: %r"
                         % report.get("abstract_unix"))

    def test_a_jailed_client_cannot_signal_a_process_outside_itself(self):
        if landlock.scope_mask(landlock.available()) == 0:
            self.skipTest("this ABI knows no IPC scopes (needs ABI 6)")
        rc, report = self._spawn("opencode")
        self.assertEqual(report.get("signal_parent"), "denied",
                         "the signal scope did not hold: %r"
                         % report.get("signal_parent"))

    def test_documented_limit_pathname_unix_socket_open(self):
        """A pathname AF_UNIX outside every grant is fenced only on a kernel that
        handles LANDLOCK_ACCESS_FS_RESOLVE_UNIX, so the answer is PROBED, not
        assumed from the ABI number: denied where the bit is handled, and open —
        named as the limit — where it is not.

        This host (ABI 8) rejects bit 16, so the pathname socket stays reachable
        from inside the jail: the hole is real, and it is what this test writes
        down rather than a passing assertion. A kernel that starts handling the
        bit fails this test on purpose, so the docstring gets revisited.
        """
        handled = bool(landlock.probe_accepted()[0] & landlock.FS_RESOLVE_UNIX)
        rc, report = self._spawn("opencode")
        answer = report.get("pathname_unix")
        if handled:
            self.assertEqual(answer, "denied",
                             "RESOLVE_UNIX is handled here and the pathname socket "
                             "still opened: %r" % answer)
        else:
            self.assertEqual(answer, "ok",
                             "the limit moved: this kernel handles RESOLVE_UNIX "
                             "and the socket was refused (%r) — update this test "
                             "and the wrapper's RESOLVE_UNIX paragraph" % answer)

    # --- the scope and the jail are one argv ----------------------------------
    def test_run_client_jails_the_client_it_launches(self):
        rc, report = self._spawn("opencode")
        self.assertEqual((rc.jail or {}).get("path"), "landlock", rc.jail)
        self.assertEqual((rc.jail or {}).get("abi"), landlock.available(), rc.jail)
        self.assertEqual(report.get("home_read"), "denied", report)

    def test_a_scoped_launch_keeps_the_jail_inside_the_scope(self):
        """SCOPECLI and P1b compose: `systemd-run --scope … -- env -u … --
        python3 landlock_exec.py … -- <client>`. Whichever scope path the host
        takes (`scoped` builds a unit, `inherited` rides the one this suite
        stands in), the fence is what governs the client's opens."""
        rc, report = self._spawn("opencode")
        scope = rc.scope or {}
        self.assertIn(scope.get("path"), ("scoped", "inherited"), scope)
        self.assertEqual((rc.jail or {}).get("path"), "landlock", rc.jail)
        self.assertEqual(report.get("outside_read"), "denied", report)

    @unittest.skipUnless(agent.scope_supported(),
                         "systemd-run --user is not usable on this host")
    def test_a_systemd_scope_unit_holds_the_jailed_client(self):
        """The composition spelled out and started for real, so at least one test
        passes through an actual `systemd-run --user --scope` unit of this suite's
        own: the cgroup is what `cancel` stops, the jail is what the worker cannot
        lift, and the client sits inside both."""
        unit = agent.cli_scope_unit("testjailcompose", 1)
        tcp = self._listeners()
        env = self._env()
        cmd = [self._stub("opencode"), self._cfg(tcp)]
        jail = agent.jail_decision(self.sandbox, cmd, env, isolated=True)
        self.assertEqual(jail["path"], "landlock", jail)
        argv, launch_env = agent.worker_scope_launch(
            unit, agent.jailed_argv(self.sandbox, cmd, env, jail), env)
        self.assertEqual(os.path.basename(argv[0]), "systemd-run", argv)
        # two `--` in the composed line: the scope's, then the jail's. The
        # wrapper is the command AFTER the scope's `env -u … --` prologue, and
        # the client is the last thing on it.
        dashes = [i for i, tok in enumerate(argv) if tok == "--"]
        self.assertEqual(len(dashes), 2, argv)
        inner = argv[dashes[0] + 1:]
        self.assertEqual(os.path.basename(inner[0]), "env", inner)
        jailed = argv[dashes[1] + 1:]
        self.assertEqual(os.path.basename(jailed[0]), sys.executable, argv)
        self.assertEqual(os.path.basename(jailed[1]), agent.JAIL_WRAPPER, argv)
        self.assertEqual(jailed[len(jailed) - len(cmd):], cmd, argv)
        out, err = io.StringIO(), io.StringIO()
        proc = subprocess.Popen(argv, cwd=self.sandbox, env=launch_env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            child_out, _ = proc.communicate(timeout=120)
        if not os.path.exists(self.report):
            self.fail("the scoped+jailed client wrote no report (rc=%s): %s"
                      % (proc.returncode,
                         child_out.decode("utf-8", "replace")[-3000:]))
        with open(self.report, encoding="utf-8") as fh:
            report = json.load(fh)
        self._assert_canaries(report, "scoped unit %s" % unit)
        self.assertEqual(report.get("git_status"), "ok", report)


class JailNoLandlockGateTests(unittest.TestCase):
    """Item 3, end to end through the CLI: with no fence to build, a PRIVATE lane
    is refused with rc 2 before anything is cloned, and a PUBLIC one runs as it
    did before P1b — loudly on stderr, and recorded on the worker record. The
    wrapper's documented `LANDLOCK_EXEC_FORCE_ABI0` seam is what puts a host with
    a real Landlock into that state without unloading an LSM."""

    def setUp(self):
        if os.name == "nt":
            self.skipTest("POSIX only: the gate and its shell stub")
        self.base = tempfile.mkdtemp(prefix="autoos-jail-gate-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.state = os.path.join(self.base, "state")
        self.workers = os.path.join(self.base, "workers")
        os.makedirs(self.state, exist_ok=True)
        os.makedirs(self.workers, exist_ok=True)
        bin_ = os.path.join(self.base, "bin")
        os.makedirs(bin_)
        stub = os.path.join(bin_, "opencode")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\n'
                     'case "$1" in\n'
                     '  --version) echo 0.0.0-fake; exit 0;;\n'
                     '  models) echo fake; exit 0;;\n'
                     'esac\n'
                     'echo "REPORT: none"\n')
        os.chmod(stub, 0o755)
        self.bin = bin_

    def _run(self, extra_args, abi0=True):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["AUTOOS_STATE_DIR"] = self.state
        env["AUTOOS_WORKERS_DIR"] = self.workers
        # a key the route does not have to mint: this run never reaches the
        # gateway, the stub client answers and the gate is what is measured
        env["AUTOOS_OMNIROUTE_KEY"] = "test-not-a-real-key"
        if abi0:
            env["LANDLOCK_EXEC_FORCE_ABI0"] = "1"
        else:
            env.pop("LANDLOCK_EXEC_FORCE_ABI0", None)
        env.pop("AUTOOS_CLAUDE_REASON", None)
        proc = subprocess.run([sys.executable, str(TOOLS / "autoos-agent.py"),
                               "run", "--isolate", "--client", "opencode"]
                              + extra_args,
                              cwd=self.base, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=300)
        return (proc.returncode,
                proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"))

    def _records(self):
        out = []
        for name in sorted(os.listdir(self.workers)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.workers, name), encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except (OSError, ValueError):
                continue
        return out

    SENSITIVE = ["--card", "privacy=sensitive,role=implement", "t"]
    PUBLIC = ["--card", "role=implement", "t"]

    def test_a_private_isolated_lane_with_no_landlock_is_refused_rc_2(self):
        rc, out, err = self._run(self.SENSITIVE)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("refusing to launch an unjailed isolated worker", out + err)
        self.assertIn("no Landlock ABI on this kernel", out + err)
        self.assertIn("--allow-unjailed", out + err)
        self.assertEqual(os.listdir(os.path.join(self.state, "sandboxes"))
                         if os.path.isdir(os.path.join(self.state, "sandboxes"))
                         else [], [], "a refusal cloned a sandbox")
        self.assertFalse([r for r in self._records()],
                         "a refusal wrote a worker record")

    def test_a_public_isolated_lane_with_no_landlock_runs_and_says_so(self):
        rc, out, err = self._run(self.PUBLIC)
        self.assertNotEqual(rc, 2, out + err)
        self.assertIn("WARNING client runs UNJAILED", err)
        self.assertIn("jail: unjailed", out)
        records = [r for r in self._records() if r.get("jail")]
        self.assertTrue(records, "no worker record carries a jail answer")
        jail = records[-1]["jail"]
        self.assertEqual(jail.get("path"), "unjailed", jail)
        self.assertEqual(jail.get("reason"), "no Landlock ABI on this kernel", jail)
        self.assertTrue(jail.get("refusable"), jail)
        self.assertNotIn("override", jail, jail)

    def test_the_allow_unjailed_override_is_recorded_for_a_private_lane(self):
        rc, out, err = self._run(self.SENSITIVE[:-1] + ["--allow-unjailed"]
                                 + self.SENSITIVE[-1:])
        self.assertNotEqual(rc, 2, out + err)
        self.assertIn("--allow-unjailed", out)
        records = [r for r in self._records() if r.get("jail")]
        self.assertTrue(records, "no worker record carries a jail answer")
        self.assertEqual(records[-1]["jail"].get("override"), "--allow-unjailed",
                         records[-1]["jail"])

    def test_a_private_isolated_lane_is_jailed_when_the_kernel_has_the_abi(self):
        """The rc-2 path is the exception, not the rule: the same card on a
        kernel that has the ABI is jailed, recorded, and never warned about."""
        if landlock.available() <= 0:
            self.skipTest("no Landlock ABI on this kernel")
        rc, out, err = self._run(self.SENSITIVE, abi0=False)
        self.assertNotEqual(rc, 2, out + err)
        self.assertNotIn("client runs UNJAILED", err)
        self.assertIn("jail: landlock", out)
        records = [r for r in self._records() if r.get("jail")]
        self.assertTrue(records, "no worker record carries a jail answer")
        self.assertEqual(records[-1]["jail"].get("path"), "landlock", records[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
