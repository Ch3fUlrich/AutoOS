"""Tests for tools/hostexec/policy.py (spec status/L1-backlog.spec-host-shell.md
section 6, decision table). Pure stdlib, no I/O beyond a temp dir and the
decision-table fixture. Written before the implementation (coding-principles 2).

Run from anywhere:

    python3 tests/test_hostexec_policy.py
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
FIXTURE = ROOT / "tests" / "fixtures" / "hostexec-decisions.tsv"
CLI = TOOLS / "hostexec.py"
sys.path.insert(0, str(TOOLS))

from hostexec import policy  # noqa: E402

# Every bare command name the fixture's argv columns reference. The test
# policy's fixed PATH resolves exactly these, in a throwaway bin dir, so
# path-hijack fires only for the rows that mean to test it.
_KNOWN_COMMANDS = [
    "sudo", "su", "doas", "pkexec", "run0",
    "sh", "bash", "zsh", "dash", "fish",
    "ksh", "mksh", "csh", "tcsh", "ash", "busybox",
    "python3", "python", "perl", "node", "ruby", "awk",
    "rm", "mkfs", "mkfs.ext4", "wipefs", "dd", "shred",
    "shutdown", "reboot", "poweroff", "halt", "init",
    "systemctl", "chmod", "chown", "git", "docker",
    "iptables", "nft", "crontab",
    "env", "nice", "nohup", "timeout", "xargs", "ionice", "stdbuf", "setsid",
    "chrt", "flock", "taskset", "time", "watch", "unbuffer", "sg", "runuser",
    "script", "systemd-run", "at", "batch", "setpriv", "chroot", "unshare",
    "nsenter", "busybox", "find", "parallel", "ssh", "scp", "sftp", "rsync",
    "podman", "ls", "make", "grep",
    "echo", "id", "uptime", "df",
    "tar", "tmux", "screen", "dtach",
    "php", "lua", "luajit", "Rscript", "R", "julia",
    "vim", "vi", "nvim", "view", "ex", "less", "more", "man",
    "sudo-rs", "gawk", "mawk", "nawk",
]


def _make_fixed_path(tmp: str) -> str:
    if os.name == "nt":
        raise unittest.SkipTest("sh stubs and chmod; POSIX only")
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir, exist_ok=True)
    for name in _KNOWN_COMMANDS:
        p = os.path.join(bindir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(p, 0o755)
    return bindir


def _test_policy(bindir: str) -> policy.Policy:
    actors = {
        "sha-claude": policy.Actor(name="claude", tier="default"),
        "sha-openhands": policy.Actor(name="openhands", tier="default"),
        "sha-opencode": policy.Actor(name="opencode", tier="default"),
    }
    hosts = {
        "coding-host": policy.HostEntry(alias="coding-host", kind="local"),
        "lab-ssh": policy.HostEntry(alias="lab-ssh", kind="ssh", target="lab-ssh"),
        "storage": policy.HostEntry(alias="storage", kind="ssh", target="storage", forbid=True),
        "firewall": policy.HostEntry(alias="firewall", kind="ssh", target="firewall", forbid=True),
    }
    return policy.Policy(actors=actors, hosts=hosts, path=(bindir,),
                          max_args=64, max_arg_length=64)


def _load_rows():
    with open(FIXTURE, encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        return list(reader)


class DecisionTableTests(unittest.TestCase):
    """Drives tests/fixtures/hostexec-decisions.tsv: every row is one call."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bindir = _make_fixed_path(cls._tmp.name)
        cls.policy = _test_policy(cls.bindir)
        cls.rows = _load_rows()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_fixture_is_not_empty(self):
        self.assertGreater(len(self.rows), 50)

    def test_dead_wrapper_helpers_are_gone(self):
        # item 4: _strip_wrappers was never called (the command-head walker
        # _command_heads replaced it) and _WRAPPERS existed only for it.
        # Keep them deleted rather than letting a future edit re-add dead
        # code that looks live.
        self.assertFalse(hasattr(policy, "_strip_wrappers"),
                         "_strip_wrappers is dead code; do not reintroduce it")
        self.assertFalse(hasattr(policy, "_WRAPPERS"),
                         "_WRAPPERS existed only for _strip_wrappers")

    def test_every_rule_id_has_at_least_one_row(self):
        rule_ids = {
            "unknown-actor", "empty-argv", "argv-caps", "forbid-host",
            "env-injection", "path-hijack", "no-sudo", "no-inline-shell",
            "destructive", "git-option-injection", "use-host-alias",
            "docker-root",
        }
        seen = {r["rule"] for r in self.rows if r["rule"]}
        self.assertEqual(seen, rule_ids, f"missing rows for: {rule_ids - seen}")

    def test_decision_table(self):
        for i, row in enumerate(self.rows):
            argv = json.loads(row["argv"])
            with self.subTest(row=i, argv=argv, expected=row["expected"]):
                decision = policy.decide(self.policy, row["actor"], row["host"], argv, cwd="/tmp")
                want_allow = row["expected"] == "allow"
                self.assertEqual(decision.allow, want_allow,
                                  f"argv={argv!r} note={row['note']!r} got rule={decision.rule!r}")
                if not want_allow:
                    self.assertEqual(decision.rule, row["rule"], f"argv={argv!r} note={row['note']!r}")
                else:
                    self.assertIsNone(decision.rule)


@unittest.skipIf(os.name == "nt", "chmod mode bits and sh stubs; POSIX only")
class PathHijackWorldWritableTests(unittest.TestCase):
    """The world-writable-directory case needs a real temp dir per test, so
    it can't live in the static TSV (paths there must be location-independent)."""

    def test_absolute_argv0_under_a_world_writable_dir_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            evil_dir = os.path.join(tmp, "evil")
            os.makedirs(evil_dir)
            os.chmod(evil_dir, 0o777)
            target = os.path.join(evil_dir, "id")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(target, 0o755)
            pol = _test_policy(_make_fixed_path(tmp))
            decision = policy.decide(pol, "claude", "coding-host", [target], cwd="/tmp")
            self.assertFalse(decision.allow)
            self.assertEqual(decision.rule, "path-hijack")

    def test_absolute_argv0_under_a_normal_dir_is_not_flagged_by_path_hijack(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = _make_fixed_path(tmp)
            # G: uid-writable counts, so the "normal" dir must not be
            # writable by anyone (0555) -- 0755 owned by us would deny.
            os.chmod(bindir, 0o555)
            target = os.path.join(bindir, "id")
            os.chmod(target, 0o555)
            pol = _test_policy(bindir)
            decision = policy.decide(pol, "claude", "coding-host", [target], cwd="/tmp")
            self.assertTrue(decision.allow, decision.reason)

    def test_absolute_argv0_under_a_uid_writable_dir_is_denied(self):
        # G: resolved file or any parent writable by the current uid denies.
        with tempfile.TemporaryDirectory() as tmp:
            evil_dir = os.path.join(tmp, "evil")
            os.makedirs(evil_dir)
            os.chmod(evil_dir, 0o755)  # owner-writable (we own tmp)
            target = os.path.join(evil_dir, "id")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(target, 0o755)
            pol = _test_policy(_make_fixed_path(tmp))
            decision = policy.decide(pol, "claude", "coding-host", [target], cwd="/tmp")
            self.assertFalse(decision.allow)
            self.assertEqual(decision.rule, "path-hijack")

    def test_absolute_symlink_to_sudo_is_denied(self):
        # G/Qoder-3: ln -s /usr/bin/sudo /home/op/bin/id then
        # [/home/op/bin/id -n id] must deny (resolved basename sudo).
        with tempfile.TemporaryDirectory() as tmp:
            bindir = _make_fixed_path(tmp)
            os.chmod(bindir, 0o555)
            for name in ("sudo", "id"):
                try:
                    os.chmod(os.path.join(bindir, name), 0o555)
                except OSError:
                    pass
            # real sudo stand-in on the fixed PATH
            sudo_target = os.path.join(bindir, "sudo")
            link_dir = os.path.join(tmp, "linkdir")
            os.makedirs(link_dir)
            link = os.path.join(link_dir, "id")
            try:
                os.symlink(sudo_target, link)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            # Make the link's parent non-writable AFTER creating the link,
            # so the denial (if any) is the symlink itself, not the parent.
            try:
                os.chmod(link_dir, 0o555)
            except OSError:
                pass
            pol = _test_policy(bindir)
            decision = policy.decide(pol, "claude", "coding-host", [link, "-n", "id"], cwd="/tmp")
            self.assertFalse(decision.allow)
            # path-hijack (symlink target differs) or no-sudo (resolved sudo)
            self.assertIn(decision.rule, ("path-hijack", "no-sudo"))


class PolicyLoaderTests(unittest.TestCase):
    """load()/loads(): TOML parsing and validation. Missing/malformed input
    must raise PolicyError -- the server must refuse to start on this
    (review F6), never fall back to allow-all."""

    VALID = """
path = ["/usr/bin", "/bin"]

[limits]
max_args = 32
max_arg_length = 2048

[actors."deadbeef"]
name = "claude"
tier = "default"

[hosts."coding-host"]
kind = "local"

[hosts."storage"]
kind = "ssh"
target = "storage"
forbid = true
"""

    def test_valid_policy_loads(self):
        pol = policy.loads(self.VALID)
        self.assertEqual(pol.actors["deadbeef"].name, "claude")
        self.assertEqual(pol.hosts["storage"].forbid, True)
        self.assertEqual(pol.hosts["coding-host"].forbid, False)
        self.assertEqual(pol.path, ("/usr/bin", "/bin"))
        self.assertEqual(pol.max_args, 32)

    def test_missing_file_raises_policy_error(self):
        with self.assertRaises(policy.PolicyError):
            policy.load("/no/such/policy.toml")

    def test_malformed_toml_raises_policy_error(self):
        with self.assertRaises(policy.PolicyError):
            policy.loads("this is not [valid toml")

    def test_missing_actors_raises_policy_error(self):
        with self.assertRaises(policy.PolicyError):
            policy.loads('path = ["/bin"]\n[hosts.h]\nkind = "local"\n')

    def test_missing_hosts_raises_policy_error(self):
        with self.assertRaises(policy.PolicyError):
            policy.loads('path = ["/bin"]\n[actors.x]\nname = "a"\ntier = "t"\n')

    def test_missing_path_raises_policy_error(self):
        with self.assertRaises(policy.PolicyError):
            policy.loads('[actors.x]\nname = "a"\ntier = "t"\n[hosts.h]\nkind = "local"\n')

    def test_bad_host_kind_raises_policy_error(self):
        with self.assertRaises(policy.PolicyError):
            policy.loads('path = ["/bin"]\n[actors.x]\nname = "a"\ntier = "t"\n'
                         '[hosts.h]\nkind = "remote-desktop"\n')

    def test_actor_missing_tier_raises_policy_error(self):
        with self.assertRaises(policy.PolicyError):
            policy.loads('path = ["/bin"]\n[actors.x]\nname = "a"\n[hosts.h]\nkind = "local"\n')

    def test_duplicate_actor_names_raise_policy_error(self):
        # H/Qoder-8: rotating a token must not silently grant two clients
        # the same name (cross-actor logTail isolation, run_as attribution).
        with self.assertRaises(policy.PolicyError):
            policy.loads('path = ["/bin"]\n[hosts.h]\nkind = "local"\n'
                         '[actors.old]\nname = "claude"\ntier = "default"\n'
                         '[actors.new]\nname = "claude"\ntier = "default"\n')


# ─── round 3 (hx3): generated wrapper-option bypass matrix ───────────────
#
# Each transparent launcher parses its leading options with one walker, so a
# value-taking option given in any spelling cannot hide a forbidden command
# behind it. The matrix is generated, not hand-listed: every wrapper x every
# value-option spelling x every forbidden child, plus the env split-string
# and flock -c/-- forms the walker exists to catch.

# (argv, rule) triples a generated case must produce.
_FORBIDDEN_CHILDREN = (
    (("sudo", "id"), "no-sudo"),
    (("sh", "-c", "x"), "no-inline-shell"),
    (("rm", "-rf", "/"), "destructive"),
)

# Per launcher: option spellings that each already have their value supplied,
# so the very next token is the wrapped command (flock's lockfile is included
# because flock requires it before the command; timeout's duration likewise).
_WRAPPER_OPTION_SPELLINGS: dict[str, list[list[str]]] = {
    "env": [
        ["-u", "x"], ["-ux"], ["--unset", "x"], ["--unset=x"],
        ["-C", "/tmp"], ["-C/tmp"], ["--chdir", "/tmp"], ["--chdir=/tmp"],
        ["-a", "M"], ["-aM"], ["--argv0", "M"], ["--argv0=M"],
        ["-i"], ["-"],
    ],
    "nice": [
        ["-n", "5"], ["-n5"], ["--adjustment", "5"], ["--adjustment=5"],
    ],
    "timeout": [
        ["-s", "TERM", "5"], ["-sTERM", "5"],
        ["--signal", "TERM", "5"], ["--signal=TERM", "5"],
        ["-k", "1", "5"], ["-k1", "5"],
        ["--kill-after", "1", "5"], ["--kill-after=1", "5"],
    ],
    "stdbuf": [
        ["-i", "L"], ["-iL"], ["--input", "L"], ["--input=L"],
        ["-o", "0"], ["-o0"], ["--output", "0"], ["--output=0"],
        ["-e", "L"], ["-eL"], ["--error", "L"], ["--error=L"],
    ],
    "ionice": [
        ["-c", "2"], ["-c2"], ["--class", "2"], ["--class=2"],
        ["-n", "3"], ["-n3"], ["--classdata", "3"], ["--classdata=3"],
    ],
    "xargs": [
        ["-n", "1"], ["-n1"], ["--max-args", "1"], ["--max-args=1"],
        ["-a", "/tmp/f"], ["-a/tmp/f"], ["--arg-file", "/tmp/f"], ["--arg-file=/tmp/f"],
        ["-s", "100"], ["-s100"], ["--max-chars", "100"], ["--max-chars=100"],
    ],
    "flock": [
        ["-w", "5", "/tmp/l"], ["-w5", "/tmp/l"],
        ["--timeout", "5", "/tmp/l"], ["--timeout=5", "/tmp/l"],
        ["-E", "1", "/tmp/l"], ["-E1", "/tmp/l"],
        ["--conflict-exit-code", "1", "/tmp/l"], ["--conflict-exit-code=1", "/tmp/l"],
    ],
    "setsid": [[], ["--wait"], ["-w"], ["--fork"]],
    "nohup": [[], ["--"]],
    # chrt takes one positional (the priority) before its command and three
    # value-taking options; taskset takes one positional (the mask, or the
    # cpu-list after the -c switch) and no values at all; watch is only
    # transparent with -x/--exec, so every spelling here carries it.
    "chrt": [
        ["1"], ["-f", "1"], ["--fifo", "1"], ["-a", "1"], ["--all-tasks", "1"],
        ["-T", "1000", "1"], ["-T1000", "1"], ["--sched-runtime", "1000", "1"],
        ["--sched-runtime=1000", "1"],
    ],
    "taskset": [
        ["0x1"], ["-c", "0"], ["--cpu-list", "0"], ["--cpu-l", "0"],
    ],
    "watch": [
        ["-x"], ["--exec"], ["-e", "-x"], ["-n", "5", "-x"],
        ["--interval=5", "-x"],
    ],
}

# env -S/--split-string after a value-taking option: the value hides the
# option region from a naive scan, so the split string survives to argv.
_ENV_SPLIT_OPTIONS = (["-S"], ["--split-string"], ["-vS"])

# flock runs `-c`/`--command` through a shell; `--` makes the next token the
# lockfile even when it starts with `-` (so `flock -- -c rm -rf /` locks on
# the file named `-c` and runs `rm -rf /`).
_FLOCK_COMMAND_FORMS = (
    (["flock", "-c", "rm -rf /"], "no-inline-shell"),
    (["flock", "--command", "rm -rf /"], "no-inline-shell"),
    (["flock", "/tmp/l", "-c", "rm -rf /"], "no-inline-shell"),
    (["flock", "/tmp/l", "--command", "rm -rf /"], "no-inline-shell"),
    (["flock", "--", "-c", "sh", "-c", "x"], "no-inline-shell"),
    (["flock", "--", "-c", "python3", "-c", "x"], "no-inline-shell"),
    (["flock", "--", "-c", "rm", "-rf", "/"], "destructive"),
    # `--` ends option parsing, not the positional region, so the `-c` one token
    # past the lockfile is still flock's shell form (measured in
    # _NON_PERMUTING_HEADS) and the denial names the shell, not an unresolvable
    # `-c` head.
    (["flock", "--", "/tmp/l", "-c", "rm -rf /"], "no-inline-shell"),
    (["flock", "--", "/tmp/l", "--command", "rm -rf /"], "no-inline-shell"),
    (["nice", "flock", "--", "/tmp/l", "-c", "rm -rf /"], "no-inline-shell"),
)

# Harmless forms that must stay allowed: proving the walker does not simply
# deny every option it meets.
_HARMLESS_ALLOWED = (
    ["env", "FOO=1", "ls"],
    ["env", "-i", "ls"],
    ["env", "-", "ls"],
    ["nice", "-n", "5", "ls"],
    ["timeout", "5", "ls", "-la"],
    ["xargs", "-0", "echo"],
    ["flock", "/tmp/l", "ls"],
    ["flock", "--", "/tmp/l", "ls"],
    ["flock", "--", "-", "ls"],
    # Options that *precede* the lockfile are still getopt's own, abbreviations
    # included (measured: `flock -s /tmp/l /bin/echo F` prints `F`). What follows
    # the lockfile is not -- see _NON_PERMUTING_HEADS.
    ["flock", "-s", "/tmp/l", "ls"],
    ["flock", "-n", "-w", "5", "/tmp/l", "ls"],
    ["setsid", "ls"],
    ["nohup", "ls"],
    # chrt: policy switch, value-taking -T/-P/-D, then the priority positional.
    ["chrt", "1", "ls"],
    ["chrt", "-f", "1", "ls"],
    ["chrt", "--fifo", "1", "ls"],
    ["chrt", "-T", "1000", "5", "ls"],
    ["chrt", "-T1000", "5", "ls"],
    ["chrt", "--sched-runtime", "1000", "1", "ls"],
    ["chrt", "-m"],
    ["chrt", "--max"],
    # `--` only terminates chrt's/taskset's options while they are still being
    # scanned, i.e. before the priority/mask (hx3): these two forms really do
    # run `ls` (`taskset -- 0x1 /bin/echo x` prints `x`). The forms with `--`
    # *after* the positional exec the literal `--` and are denied -- see
    # _NON_PERMUTING_DENIED.
    ["chrt", "--", "9", "ls"],
    ["chrt", "-f", "--", "5", "ls"],
    # taskset: the mask is positional; -c/--cpu-list is a switch, not a value.
    ["taskset", "0x1", "ls"],
    ["taskset", "-c", "0", "ls"],
    ["taskset", "--cpu-list", "0", "ls"],
    ["taskset", "--cpu-l", "0", "ls"],
    ["taskset", "-a", "0x1", "ls"],
    ["taskset", "--", "0x1", "ls"],
    # watch: transparent only in -x/--exec form.
    ["watch", "-x", "ls"],
    ["watch", "--exec", "ls"],
    ["watch", "--ex", "ls"],
    ["watch", "-n", "5", "-x", "ls"],
    ["watch", "--interval=5", "-x", "ls"],
    ["watch", "-d", "-x", "ls"],
    ["watch", "-q", "5", "-x", "ls"],
    ["watch", "-x", "uptime"],
)


def _generated_wrapper_cases():
    cases = []
    for wrapper, spellings in _WRAPPER_OPTION_SPELLINGS.items():
        for spelling in spellings:
            for child, rule in _FORBIDDEN_CHILDREN:
                cases.append(([wrapper, *spelling, *child], rule))
    # Nest one level: a wrapper wrapping another wrapper must still expose
    # the innermost command head. The outer walker must find the inner
    # wrapper as the child, and the inner walker the forbidden child -- so a
    # value-option disagreement at either layer is a bypass. One
    # representative spelling per inner wrapper keeps the matrix bounded.
    for outer, outer_spellings in _WRAPPER_OPTION_SPELLINGS.items():
        for inner, inner_spellings in _WRAPPER_OPTION_SPELLINGS.items():
            inner_option = inner_spellings[0]
            for outer_option in outer_spellings:
                for child, rule in _FORBIDDEN_CHILDREN:
                    cases.append(([outer, *outer_option, inner, *inner_option, *child], rule))
    for spelling in (["-u", "x"], ["-ux"], ["-C", "/tmp"], ["-a", "M"],
                     ["--unset", "x"], ["--unset=x"]):
        for split in _ENV_SPLIT_OPTIONS:
            cases.append((["env", *spelling, *split, "sudo id"], "no-inline-shell"))
    cases.extend(_FLOCK_COMMAND_FORMS)
    return cases


@unittest.skipIf(os.name == "nt", "sh stubs and chmod; POSIX only")
class WrapperOptionBypassMatrixTests(unittest.TestCase):
    """Round 3: one option walker per wrapper closes the value-option and
    flock `--`/`-c` bypasses; generated so a new spelling is covered without
    a new hand-written row."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bindir = _make_fixed_path(cls._tmp.name)
        cls.policy = _test_policy(cls.bindir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _decide(self, argv):
        return policy.decide(self.policy, "claude", "coding-host", argv, cwd="/tmp")

    def test_generated_matrix_denies_every_hidden_forbidden_child(self):
        cases = _generated_wrapper_cases()
        # flat spellings + one level of nesting must both be present, so the
        # bound proves the nested matrix was generated, not silently dropped.
        self.assertGreater(len(cases), 2000)
        for argv, rule in cases:
            with self.subTest(argv=argv, rule=rule):
                decision = self._decide(argv)
                self.assertFalse(decision.allow,
                                 f"{argv!r} was allowed (rule={decision.rule!r})")
                self.assertEqual(decision.rule, rule, f"{argv!r}")

    def test_harmless_option_forms_stay_allowed(self):
        for argv in _HARMLESS_ALLOWED:
            with self.subTest(argv=argv):
                decision = self._decide(argv)
                self.assertTrue(decision.allow,
                                f"{argv!r} denied as {decision.rule!r}: {decision.problems}")

    def test_flock_dashdash_next_token_is_always_the_lockfile(self):
        # The head after `--` is the command, never the `-c` that is really
        # the lockfile; the brief names these exact heads.
        self.assertEqual(
            policy._direct_child_heads(["flock", "--", "-c", "rm", "-rf", "/"]),
            [["rm", "-rf", "/"]])
        self.assertEqual(
            policy._direct_child_heads(["flock", "--", "/tmp/l", "ls"]),
            [["ls"]])
        self.assertEqual(
            policy._direct_child_heads(["flock", "--", "-", "ls"]),
            [["ls"]])


# ─── hx2 follow-up: chrt / taskset / watch on the shared option walker ─────
#
# These three launchers were the last hand-scanned ones. Each skipped any token
# starting with '-', so an unknown or abbreviated long option was never
# fail-closed and a value-taking short option left its value standing where the
# command is looked for (`chrt -T 1000 5 cmd`, `watch -q 5 cmd`,
# `taskset -pc 0,3 700`).
#
# The option tables are the real programs' own -- util-linux 2.39.3
# `chrt --help` / `taskset --help` and procps-ng 4.0.4 `watch --help` -- and the
# abbreviation behaviour was measured against those binaries rather than
# assumed: `chrt --f`, `chrt --ba`, `taskset --cpu`, `taskset --c`,
# `watch --ex` and `watch --int` each resolve to exactly one option there, while
# the programs themselves report `chrt --r` (rr | reset-on-fork), `chrt --s`
# (sched-runtime | sched-period | sched-deadline), `watch --e` (errexit |
# equexit | exec) and `watch --no` (no-color | no-rerun | no-title | no-wrap) as
# ambiguous, and `chrt --reset-fork` / `watch --compat` as unrecognized.

# (argv, the wrapped-command heads the walker must yield). [] means "no head is
# statically derivable", which is what pid mode and a stop are for.
_THREE_LAUNCHER_HEADS = (
    (["chrt", "-T", "1000", "5", "ls", "-n"], [["ls", "-n"]]),
    (["chrt", "-f", "1", "ls"], [["ls"]]),
    (["chrt", "--fifo", "1", "ls"], [["ls"]]),
    (["chrt", "--sched-runtime", "1000", "1", "ls"], [["ls"]]),
    # `chrt 9 -- ls` and `taskset 9 -- ls` used to be listed here as [["ls"]].
    # That was a fictitious reading -- both programs stop option parsing at
    # their positional and exec the literal `--`. They live, corrected, in
    # _NON_PERMUTING_HEADS below (hx3).
    (["chrt", "--", "9", "ls"], [["ls"]]),
    (["chrt", "-m"], []),
    (["chrt", "-p", "123"], []),
    (["chrt", "--pid", "123"], []),
    (["taskset", "0x1", "ls", "-l"], [["ls", "-l"]]),
    (["taskset", "-c", "0", "ls"], [["ls"]]),
    (["taskset", "--cpu-l", "0", "ls"], [["ls"]]),
    (["taskset", "--", "0x1", "ls"], [["ls"]]),
    (["taskset", "-pc", "0,3", "700"], []),
    (["taskset", "-p", "03", "700"], []),
    (["watch", "-x", "-n", "5", "ls", "-l"], [["ls", "-l"]]),
    (["watch", "-x", "--", "ls"], [["ls"]]),
    (["watch", "--exec", "uptime"], [["uptime"]]),
    # flock keeps its lockfile positional and `--` does not reset the positional
    # count: `flock -- -c …` takes `-c` as the lockfile *name* and runs `rm`.
    # What follows the lockfile is execed verbatim -- flock stops there too, so
    # those heads live in _NON_PERMUTING_HEADS (hx3 review).
    (["flock", "--", "-c", "rm", "-rf", "/"], [["rm", "-rf", "/"]]),
)

# Denied for what the option region says about the command: pid mode has no
# command to audit, an unknown/ambiguous long option makes it unknowable, and
# watch without -x/--exec joins its arguments into a `sh -c` string -- the same
# fail-closed posture as env -S and flock -c, which deny the same way.
_THREE_LAUNCHER_DENIED = (
    (["chrt", "-p", "123"], "no-inline-shell"),
    (["chrt", "--pid", "123"], "no-inline-shell"),
    (["chrt", "--pi", "123"], "no-inline-shell"),
    (["chrt", "--p", "123"], "no-inline-shell"),
    (["chrt", "-vp", "123"], "no-inline-shell"),
    (["chrt", "-p", "123", "rm", "-rf", "/"], "no-inline-shell"),
    (["taskset", "-p", "700"], "no-inline-shell"),
    (["taskset", "--pi", "700"], "no-inline-shell"),
    (["taskset", "-p", "03", "700"], "no-inline-shell"),
    (["taskset", "-pc", "0,3", "700"], "no-inline-shell"),
    (["chrt", "--zzz", "5", "ls"], "no-inline-shell"),
    (["chrt", "--r", "1", "ls"], "no-inline-shell"),
    (["chrt", "--s", "1000", "5", "ls"], "no-inline-shell"),
    (["taskset", "--zz", "0x1", "ls"], "no-inline-shell"),
    (["watch", "--zz", "ls"], "no-inline-shell"),
    (["watch", "--e", "-x", "ls"], "no-inline-shell"),
    (["watch", "--no", "-x", "ls"], "no-inline-shell"),
    (["watch", "ls"], "no-inline-shell"),
    (["watch", "-d", "ls"], "no-inline-shell"),
    (["watch", "-z", "ls"], "no-inline-shell"),
    (["watch", "-q", "5", "ls"], "no-inline-shell"),
    (["watch", "rm", "-rf", "/"], "no-inline-shell"),
    (["env", "watch", "ls"], "no-inline-shell"),
)

# The head, not a neighbouring rule, is what these denials prove: the value
# option consumed its value and the priority/mask positional was skipped, so
# the child is found and denied for its own reason.
_THREE_LAUNCHER_DENIED_BY_CHILD_RULE = (
    (["chrt", "-T", "1000", "5", "rm", "-rf", "/"], "destructive"),
    (["chrt", "--sched-runtime", "1000", "1", "rm", "-rf", "/"], "destructive"),
    (["chrt", "-T", "1000", "5", "sudo", "id"], "no-sudo"),
    (["chrt", "1", "sudo", "id"], "no-sudo"),
    (["chrt", "--fifo", "1", "sh", "-c", "x"], "no-inline-shell"),
    (["taskset", "-c", "0", "rm", "-rf", "/"], "destructive"),
    (["taskset", "--cpu-l", "0", "rm", "-rf", "/"], "destructive"),
    (["taskset", "--c", "0", "sudo", "id"], "no-sudo"),
    (["watch", "-x", "sudo", "id"], "no-sudo"),
    (["watch", "-x", "rm", "-rf", "/"], "destructive"),
    (["watch", "-q", "5", "-x", "rm", "-rf", "/"], "destructive"),
    (["watch", "-n", "5", "-x", "sh", "-c", "x"], "no-inline-shell"),
)


@unittest.skipIf(os.name == "nt", "sh stubs and chmod; POSIX only")
class ChrtTasksetWatchWalkerTests(unittest.TestCase):
    """hx2 follow-up: chrt, taskset and watch parse through
    _walk_wrapper_options like every other transparent launcher, so a
    value-taking option swallows its value, an unknown or ambiguous long option
    fails closed, pid mode yields no head and denies, and watch is refused
    unless it was given -x/--exec."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bindir = _make_fixed_path(cls._tmp.name)
        cls.policy = _test_policy(cls.bindir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _decide(self, argv):
        return policy.decide(self.policy, "claude", "coding-host", argv, cwd="/tmp")

    def test_wrapped_head_after_each_launcher(self):
        for argv, heads in _THREE_LAUNCHER_HEADS:
            with self.subTest(argv=argv):
                self.assertEqual(policy._direct_child_heads(argv), heads)

    def test_pid_mode_and_unparsable_option_regions_deny(self):
        for argv, rule in _THREE_LAUNCHER_DENIED:
            with self.subTest(argv=argv, rule=rule):
                decision = self._decide(argv)
                self.assertFalse(decision.allow, f"{argv!r} was allowed")
                self.assertEqual(decision.rule, rule, f"{argv!r}: {decision.problems}")

    def test_hidden_child_is_denied_for_its_own_reason(self):
        for argv, rule in _THREE_LAUNCHER_DENIED_BY_CHILD_RULE:
            with self.subTest(argv=argv, rule=rule):
                decision = self._decide(argv)
                self.assertFalse(decision.allow, f"{argv!r} was allowed")
                self.assertEqual(decision.rule, rule, f"{argv!r}: {decision.problems}")

    def test_the_three_launchers_reach_the_option_problem_gate(self):
        # decide() denies an unknown/ambiguous long option through
        # _wrapper_option_problem, which reads _WRAPPER_SPECS: each of these
        # names is a launcher the walker knows, and its problem is the denial.
        for argv in (["chrt", "--zzz", "1", "ls"],
                     ["taskset", "--zzz", "0x1", "ls"],
                     ["watch", "--zzz", "ls"]):
            with self.subTest(argv=argv):
                self.assertIsNotNone(policy._wrapper_option_problem(argv), argv)

    def test_the_three_launchers_have_full_option_tables(self):
        # The walker fails closed on an unknown long option, so a real option
        # missing from a table denies a harmless call. These are every long
        # option the installed program lists, in full and as its shortest
        # unambiguous prefix (measured against the binary's own errors).
        tables = {"chrt": policy._CHRT_LONGS, "taskset": policy._TASKSET_LONGS,
                  "watch": policy._WATCH_LONGS}
        expected = {
            "chrt": ("--all-tasks", "--batch", "--deadline", "--fifo", "--help",
                     "--idle", "--max", "--other", "--pid", "--reset-on-fork",
                     "--rr", "--sched-deadline", "--sched-period",
                     "--sched-runtime", "--verbose", "--version"),
            "taskset": ("--all-tasks", "--cpu-list", "--help", "--pid",
                        "--version"),
            "watch": ("--beep", "--chgexit", "--color", "--differences",
                      "--equexit", "--errexit", "--exec", "--help", "--interval",
                      "--no-color", "--no-rerun", "--no-title", "--no-wrap",
                      "--precise", "--version"),
        }
        for wrapper, names in expected.items():
            longs = tables[wrapper]
            with self.subTest(wrapper=wrapper):
                self.assertEqual(sorted(names), sorted("--" + k for k in longs))
                for full in names:
                    body = full[2:]
                    for size in range(1, len(body) + 1):
                        prefix = body[:size]
                        if sum(o.startswith(prefix) for o in longs) != 1:
                            continue  # the real program calls this ambiguous too
                        argv = [wrapper, "--" + prefix, "ls"]
                        self.assertIsNone(policy._wrapper_option_problem(argv),
                                          f"{wrapper} {argv}")


# ─── hx3 follow-up: chrt and taskset do NOT permute (POSIX `+` getopt) ─────
#
# The hx3 review found flock belongs on this path too: its own measurements are
# in _NON_PERMUTING_HEADS below. util-linux 2.39.3 stops option parsing at the
# positional for all three.
#
# What the walker used to assume: it modelled flock's permuting GNU getopt on
# every wrapper that takes a positional before its command, so it kept scanning
# options after chrt's priority and taskset's mask. util-linux 2.39.3 chrt(1)
# and taskset(1) pass a `+`-prefixed optstring to getopt(3): scanning stops at
# the first non-option, nothing after the positional is an option, and the very
# next token is handed to execvp verbatim -- `--` and `-p` included. Measured on
# this host (chrt/taskset from util-linux 2.39.3) with /bin/echo as the command:
#
#   chrt -i 0 -- /bin/echo x    -> chrt: failed to execute --: No such file or
#                                  directory                                rc 127
#   taskset 0x1 -- /bin/echo x  -> taskset: failed to execute --: No such file
#                                  directory                                rc 127
#   taskset 9 -- /bin/echo x    -> taskset: failed to execute --: No such file
#                                  directory                                rc 127
#   taskset 0x1 -c /bin/echo x  -> taskset: failed to execute -c: No such file
#                                  directory                                rc 127
#   chrt -i 0 -p 123            -> chrt: failed to execute -p: No such file or
#                                  directory (NOT pid mode)                 rc 127
#   chrt -i -- 0 /bin/echo x    -> x  (`--` before the priority is the
#                                  terminator; /bin/echo ran)               rc 0
#   taskset -- 0x1 /bin/echo x  -> x                                        rc 0
#   chrt -- -i 0 /bin/echo x    -> chrt: invalid priority argument: '-i'
#                                  (the positional region survives `--`)     rc 1
#
# The token is not merely one the program errors on later -- it is the exec
# target: with an executable literally named `--` / `-p` on the PATH (a temp dir
# handed to the call), `chrt -i 0 -p 123` printed `RAN-STUB-p 123` and
# `chrt -i 0 -- x`, `taskset 0x1 -- y` and `taskset 9 -- y` printed
# `RAN-STUB-dashdash ...`, all rc 0. hostexec's fixed PATH holds no such file,
# so nothing was reachable here -- but a head that reads `ls` for a call that
# execs `--` is an audit record that is simply false, and it lets anything after
# the separator out of the audit entirely.

# (argv, the wrapped-command heads the walker must yield). A head that starts
# with '-' is what these two programs really exec: the token verbatim.
_NON_PERMUTING_HEADS = (
    # `--` after the positional IS the command.
    (["chrt", "9", "--", "ls"], [["--", "ls"]]),
    (["chrt", "-i", "0", "--", "ls"], [["--", "ls"]]),
    (["taskset", "0x1", "--", "ls"], [["--", "ls"]]),
    (["taskset", "9", "--", "ls"], [["--", "ls"]]),
    # A dash-prefixed token after the positional is the command too, and it is
    # not pid mode: `chrt 5 -p 123` execs -p rather than touching pid 123.
    (["chrt", "5", "-p", "123"], [["-p", "123"]]),
    (["taskset", "0x1", "-c", "ls"], [["-c", "ls"]]),
    # The separator does not truncate the head: `--` and everything after it
    # land in it together, so a forbidden command parked behind it is not
    # dropped from the audit (and the head denies on the `--` it starts with).
    (["chrt", "9", "--", "rm", "-rf", "/"], [["--", "rm", "-rf", "/"]]),
    # `--` before the positional terminates the options, so the command is the
    # token after it. The third row is the positional region swallowing a
    # dash-prefixed token -- the reading chrt's own error above proves.
    (["chrt", "-f", "--", "5", "ls"], [["ls"]]),
    (["chrt", "--", "9", "ls"], [["ls"]]),
    (["chrt", "--", "-i", "0", "ls"], [["0", "ls"]]),
    (["taskset", "--", "0x1", "ls"], [["ls"]]),
    # flock stops at its lockfile too (measured, util-linux 2.39.3, /bin/echo):
    # `flock ./l -- /bin/echo A` -> `failed to execute --` rc 69 and
    # `flock ./l -s /bin/echo B` -> `failed to execute -s` rc 69. It then
    # special-cases exactly one token of that region -- a bare `-c`/`--command`,
    # which it runs via sh -c, so it is a stop and yields no head. Nothing else
    # in that spelling is honoured: `flock ./l --comm x` -> `failed to execute
    # --comm` and `flock ./l -cX` -> `failed to execute -cX`. Options *before*
    # the lockfile keep full getopt parsing, `--` included (`flock -- ./l
    # /bin/echo C` prints C; `flock -s ./l /bin/echo F` prints F), and watch
    # takes no positional at all. A `--` before the lockfile does not close the
    # region after it either: `flock -- ./l -c '/bin/echo FIVE'` prints FIVE via
    # sh -c while `flock -- ./l --comm x` and `flock -- ./l -cX` fail to execute
    # that literal token, rc 69 -- so the honoured stop reads the same here.
    (["flock", "/tmp/l", "-c", "sh", "-c", "x"], []),
    (["flock", "/tmp/l", "--command", "sh -c x"], []),
    (["flock", "/tmp/l", "--", "ls"], [["--", "ls"]]),
    (["flock", "/tmp/l", "-s", "ls"], [["-s", "ls"]]),
    (["flock", "/tmp/l", "--comm", "x"], [["--comm", "x"]]),
    (["flock", "/tmp/l", "-cX"], [["-cX"]]),
    (["flock", "--", "/tmp/l", "ls"], [["ls"]]),
    (["flock", "--", "/tmp/l", "-c", "sh -c x"], []),
    (["flock", "--", "/tmp/l", "--command", "sh -c x"], []),
    (["flock", "--", "/tmp/l", "--comm", "x"], [["--comm", "x"]]),
    (["flock", "-s", "/tmp/l", "ls"], [["ls"]]),
    (["flock", "-n", "-w", "5", "/tmp/l", "ls"], [["ls"]]),
    (["flock", "--", "-c", "rm", "-rf", "/"], [["rm", "-rf", "/"]]),
    (["watch", "-x", "--", "ls"], [["ls"]]),
)

# decide() on those heads: `--`, `-p` and `-c` resolve to no file on the fixed
# PATH, so the call denies as an unresolvable command. What matters is that `ls`
# is never audited as though it had run -- and that the denial is not pid mode,
# which is what the permuting reading claimed.
_NON_PERMUTING_DENIED = (
    (["chrt", "9", "--", "ls"], "path-hijack"),
    (["chrt", "-i", "0", "--", "ls"], "path-hijack"),
    (["taskset", "0x1", "--", "ls"], "path-hijack"),
    (["chrt", "5", "-p", "123"], "path-hijack"),
    (["taskset", "0x1", "-c", "ls"], "path-hijack"),
    (["chrt", "9", "--", "rm", "-rf", "/"], "path-hijack"),
    # The same reading for flock: `--`, `-s`, an unhonoured `--comm` and an
    # attached `-cX` are the exec targets, so `ls` is never audited as though it
    # had run -- and `rm -rf /` behind the `--` denies on the `--` it execs.
    (["flock", "/tmp/l", "--", "ls"], "path-hijack"),
    (["flock", "/tmp/l", "-s", "ls"], "path-hijack"),
    (["flock", "/tmp/l", "--comm", "x"], "path-hijack"),
    (["flock", "/tmp/l", "-cX"], "path-hijack"),
    (["flock", "/tmp/l", "--", "rm", "-rf", "/"], "path-hijack"),
)


@unittest.skipIf(os.name == "nt", "sh stubs and chmod; POSIX only")
class NonPermutingGetoptTests(unittest.TestCase):
    """hx3: chrt, taskset and flock parse with POSIX `+` getopt, so their command
    is the token right after the priority/mask/lockfile -- verbatim, even when it
    is `--` or an option-looking `-p`/`-s`/`--comm`. flock alone re-opens that
    position for a bare `-c`/`--command`, which is its own shell special case and
    a stop, not a head. Every other launcher takes no positional, so it never
    reaches the region."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bindir = _make_fixed_path(cls._tmp.name)
        cls.policy = _test_policy(cls.bindir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _decide(self, argv):
        return policy.decide(self.policy, "claude", "coding-host", argv, cwd="/tmp")

    def test_command_head_is_verbatim_after_the_positional(self):
        for argv, heads in _NON_PERMUTING_HEADS:
            with self.subTest(argv=argv):
                self.assertEqual(policy._direct_child_heads(argv), heads)

    def test_execed_verbatim_head_denies_as_an_unresolvable_command(self):
        for argv, rule in _NON_PERMUTING_DENIED:
            with self.subTest(argv=argv, rule=rule):
                decision = self._decide(argv)
                self.assertFalse(decision.allow, f"{argv!r} was allowed")
                self.assertEqual(decision.rule, rule, f"{argv!r}: {decision.problems}")

    def test_pid_mode_is_only_what_precedes_the_positional(self):
        # `chrt -p 123` really is pid mode (no command to audit); `chrt 5 -p
        # 123` execs -p, so the walker must not report a -p option and the two
        # deny for different reasons.
        self.assertIn("p", policy._walk_wrapper_options(["chrt", "-p", "123"],
                                                         policy._CHRT_SPEC)[1])
        self.assertEqual(self._decide(["chrt", "-p", "123"]).rule, "no-inline-shell")
        self.assertNotIn("p", policy._walk_wrapper_options(["chrt", "5", "-p", "123"],
                                                           policy._CHRT_SPEC)[1])
        self.assertEqual(self._decide(["chrt", "5", "-p", "123"]).rule, "path-hijack")

    def test_flock_shell_stop_survives_a_leading_dashdash(self):
        # hx3 FINAL review LOW: the post-`--` catch-up filled the lockfile slot
        # and then stopped consulting spec.post_positional_stops, so the `-c`
        # flock still runs through sh -c was reported as the execed head and
        # denied as path-hijack. It is a stop, so the audit reason is the shell.
        for argv in (["flock", "--", "/tmp/l", "-c", "id"],
                     ["flock", "--", "/tmp/l", "--command", "id"]):
            with self.subTest(argv=argv):
                self.assertEqual(policy._direct_child_heads(argv), [])
                self.assertIn(argv[3].lstrip("-"),
                              policy._walk_wrapper_options(argv, policy._FLOCK_SPEC)[1])
                decision = self._decide(argv)
                self.assertFalse(decision.allow, f"{argv!r} was allowed")
                self.assertEqual(decision.rule, "no-inline-shell", decision.problems)
                self.assertIn("shell", " ".join(decision.problems).lower(),
                              f"{argv!r}: {decision.problems}")
        # Nested one wrapper deep, the same reading must reach the inner flock.
        nested = ["nice", "flock", "--", "/tmp/l", "-c", "id"]
        decision = self._decide(nested)
        self.assertFalse(decision.allow, f"{nested!r} was allowed")
        self.assertEqual(decision.rule, "no-inline-shell", decision.problems)
        self.assertIn(["flock", "--", "/tmp/l", "-c", "id"],
                      policy._command_heads(nested))

    def test_every_positional_wrapper_stops_at_its_positional(self):
        # Nobody permutes: flock, chrt and taskset are the only launchers that
        # take a positional before their command, and util-linux 2.39.3 stops
        # their option scanning there (measured). If a future edit reopens the
        # command region to getopt, every flock/chrt/taskset row above changes
        # meaning -- this is the guard.
        self.assertEqual({label for label, spec in policy._WRAPPER_SPECS.items()
                          if spec.positionals_before_command},
                         {"flock", "chrt", "taskset"})
        # flock is the one program that re-opens the very first token of that
        # region, and only for its own shell special case, spelled in full.
        self.assertEqual({label: tuple(sorted(spec.post_positional_stops))
                          for label, spec in policy._WRAPPER_SPECS.items()
                          if spec.post_positional_stops},
                         {"flock": ("--command", "-c")})
        self.assertEqual(policy._direct_child_heads(["flock", "/tmp/l", "-c", "x"]), [])
        self.assertEqual(self._decide(["flock", "/tmp/l", "-c", "x"]).rule,
                         "no-inline-shell")


# ─── round 4 (hx4): generated long-option abbreviation matrix ────────────
#
# Every deny gate in the policy names its long options in full (the
# policy._FlaggedLongs instances), and GNU getopt_long / git parse-options
# select an option from any unambiguous abbreviation -- so `rm --recurs /`,
# `tar --to-com id` and `git push --mir origin` executed while the full
# spelling was denied. The matrix below is generated from the policy's own
# tables: it walks each flagged option from a 3-character prefix up to the
# full name, in both the bare and the `--abbrev=value` form, placed where the
# rule expects it. A new flagged option in the policy is covered by this test
# without writing a new case; a gate with no template here fails the test.

# Dangerous value per full option name -- what the `--abbrev=value` form
# carries and what a detached-value option reads from the next token, so the
# generated argv is one the rule must deny for its own reason, not merely one
# that trips a neighbouring rule.
_OPTION_VALUES = {
    "--pager": "evil-pager",
    "--git-dir": "/tmp/repo", "--work-tree": "/tmp", "--exec-path": "/tmp",
    "--output": "/tmp/out", "--upload-pack": "evil", "--receive-pack": "evil",
    "--config-env": "GIT_CONFIG", "--exec": "evil",
    "--checkpoint-action": "exec=id", "--to-command": "id",
    "--use-compress-program": "id",
    "--pid": "host", "--userns": "host", "--cap-add": "all",
    "--device": "/dev/mem", "--privileged": "true", "--volume": "/:/host",
    "--mount": "type=bind,src=/,dst=/host", "--user": "0",
    "--sshlogin": "other", "--sshloginfile": "/tmp/hosts", "--transfer": "f",
    "--return": "f", "--ssh": "other",
    "--force-with-lease": "refs/heads/main", "--force-if-includes": "true",
}

# Gate name in tools/hostexec/policy.py -> (rule id, argv shape). The shape
# places the option token where the rule reads it, followed by the detached
# value a value-taking option reads from the next token ("" for a
# presence-only flag, or when the generated form already carries `=value`).
def _opt(prefix, tok, val, suffix=()):
    return [*prefix, tok, *([val] if val else []), *suffix]


_LONG_PREFIX_GATES = {
    "_RM_DESTRUCTIVE_LONGS": ("destructive",
                              lambda tok, val: _opt(["rm"], tok, val, ["/"])),
    "_CHMOD_DESTRUCTIVE_LONGS": ("destructive",
                                 lambda tok, val: _opt(["chmod"], tok, val, ["/"])),
    "_GIT_PUSH_DESTRUCTIVE_LONGS": ("destructive",
                                    lambda tok, val: _opt(["git", "push"], tok, val, ["origin"])),
    "_IPTABLES_DESTRUCTIVE_LONGS": ("destructive",
                                    lambda tok, val: _opt(["iptables"], tok, val)),
    "_GIT_INJECT_LONGS": ("git-option-injection",
                          lambda tok, val: _opt(["git"], tok, val, ["status"])),
    "_GIT_REBASE_LONGS": ("git-option-injection",
                          lambda tok, val: _opt(["git", "rebase"], tok, val, ["origin/main"])),
    "_MAN_INLINE_LONGS": ("no-inline-shell",
                          lambda tok, val: _opt(["man"], tok, val, ["ls"])),
    "_TAR_INLINE_LONGS": ("no-inline-shell",
                          lambda tok, val: _opt(["tar", "-cf", "/tmp/x.tar"], tok, val)),
    "_DOCKER_RUN_ROOT_LONGS": ("docker-root",
                               lambda tok, val: _opt(["docker", "run"], tok, val, ["alpine"])),
    "_DOCKER_EXEC_ROOT_LONGS": ("docker-root",
                                lambda tok, val: _opt(["docker", "exec"], tok, val, ["web", "ls"])),
    # parallel's flag region is walked by _idx_after_parallel, which knows only
    # the exact long spellings; an abbreviation there mis-locates the wrapped
    # command, and the bogus head denies as path-hijack before the host-alias
    # gate is reached. Either way the call is refused -- both are accepted here
    # so the matrix says what actually stops it.
    "_PARALLEL_HOST_LONGS": (("use-host-alias", "path-hijack"),
                             lambda tok, val: _opt(["parallel"], tok, val, ["id", ":::", "x"])),
}


def _flagged_gates():
    """Every _FlaggedLongs the policy declares, by module attribute name."""
    return {name: value for name, value in vars(policy).items()
            if isinstance(value, policy._FlaggedLongs)}


def _prefixes(full: str):
    """Every abbreviation of `full` from 3 option characters up to itself."""
    body = full[2:]
    for size in range(3, len(body) + 1):
        yield "--" + body[:size]


def _generated_long_prefix_cases():
    cases = []
    for attr, (rule, shape) in _LONG_PREFIX_GATES.items():
        gate = _flagged_gates()[attr]
        for full in gate.names:
            value = _OPTION_VALUES.get(full, "x")
            for tok in _prefixes(full):
                if full in gate.takes_value:
                    # inline `--abbrev=value` and the detached form getopt_long
                    # reads the same value from the next token.
                    cases.append((shape(f"{tok}={value}", ""), rule))
                    cases.append((shape(tok, value), rule))
                else:
                    cases.append((shape(tok, ""), rule))
    return cases


@unittest.skipIf(os.name == "nt", "sh stubs and chmod; POSIX only")
class LongOptionPrefixMatrixTests(unittest.TestCase):
    """Round 4: one abbreviation-aware matcher (_long_opt_hits) behind every
    deny gate, generated over the policy's own option tables."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bindir = _make_fixed_path(cls._tmp.name)
        cls.policy = _test_policy(cls.bindir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _decide(self, argv):
        return policy.decide(self.policy, "claude", "coding-host", argv, cwd="/tmp")

    def test_every_flagged_gate_has_a_template(self):
        # A new _FlaggedLongs in the policy must be covered here, or this fails.
        gates = set(_flagged_gates())
        covered = set(_LONG_PREFIX_GATES)
        self.assertEqual(gates - covered, set(),
                         f"gates with no prefix template: {sorted(gates - covered)}")
        missing = {name for name in covered if not hasattr(policy, name)}
        self.assertEqual(missing, set(), f"templates naming a deleted gate: {sorted(missing)}")

    def test_generated_matrix_denies_every_abbreviation(self):
        cases = _generated_long_prefix_cases()
        # proves the matrix was generated from the tables, not hand-trimmed
        self.assertGreater(len(cases), 400)
        for argv, rules in cases:
            with self.subTest(argv=argv, rules=rules):
                decision = self._decide(argv)
                self.assertFalse(decision.allow,
                                 f"{argv!r} was allowed (rule={decision.rule!r})")
                self.assertIn(decision.rule, rules, f"{argv!r}")

    def test_shortest_abbreviation_is_denied_too(self):
        # 1-2 option characters: over-deny is the documented fail-closed side
        # of the rule -- a token the real program would reject as ambiguous
        # still names the dangerous option.
        for argv, rule in (
                (["rm", "--r", "/"], "destructive"),
                (["chmod", "--re", "/"], "destructive"),
                (["git", "push", "--m", "origin"], "destructive"),
                (["git", "--g", "/tmp", "status"], "git-option-injection"),
                (["iptables", "--f"], "destructive"),
                (["man", "--p", "evil"], "no-inline-shell"),
                (["tar", "--u", "id", "-f", "x"], "no-inline-shell"),
                (["docker", "run", "--p", "alpine"], "docker-root"),
                (["parallel", "--s=other", "id", ":::", "x"], "use-host-alias")):
            with self.subTest(argv=argv, rule=rule):
                decision = self._decide(argv)
                self.assertFalse(decision.allow, f"{argv!r} was allowed")
                self.assertEqual(decision.rule, rule, f"{argv!r}")

    def test_harmless_spellings_stay_allowed(self):
        # The matching is prefix-aware on the DENY side only; an ordinary
        # option that merely shares a letter with a flagged one is untouched.
        for argv in (
                ["rm", "-i", "f"],
                ["rm", "--interactive=never", "f"],
                ["rm", "--preserve-root", "f"],
                ["chmod", "--verbose", "644", "f"],
                ["chmod", "644", "/tmp/file"],
                ["git", "push", "origin", "main"],
                ["git", "log", "--oneline"],
                ["git", "status", "--ignored"],
                ["git", "diff", "--unified=3"],
                ["git", "commit", "--only", "-m", "msg"],
                ["tar", "-tf", "x.tar"],
                ["tar", "--checkpoint=1", "-cf", "/tmp/x.tar", "/tmp/f"],
                ["tar", "--to-stdout", "-xf", "/tmp/x.tar"],
                ["man", "ls"],
                ["man", "--local-file", "ls"],
                ["docker", "run", "--rm", "alpine"],
                ["docker", "run", "--detach", "alpine"],
                ["docker", "exec", "--workdir", "/tmp", "web", "ls"],
                ["iptables", "-L"],
                ["parallel", "--tag", "echo", "hi", ":::", "a"],
                ["parallel", "-I", "foo", "echo", "foo", ":::", "a"],
                ["parallel", "--replace", "foo", "echo", "foo", ":::", "a"],
                ["flock", "--", "-evil", "ls"]):
            with self.subTest(argv=argv):
                decision = self._decide(argv)
                self.assertTrue(decision.allow,
                                f"{argv!r} denied as {decision.rule!r}: {decision.problems}")

    def test_flock_dashdash_lockfile_may_start_with_a_dash(self):
        # Documented intent (hx4 item 4): after `--` the *next* token is the
        # lockfile even when it looks like an option, so `-evil` is a file
        # name and `ls` is the child -- the child is what the rules see.
        decision = self._decide(["flock", "--", "-evil", "ls"])
        self.assertTrue(decision.allow, decision.reason)
        self.assertEqual(policy._direct_child_heads(["flock", "--", "-evil", "ls"]),
                         [["ls"]])
        # and the same shape with a forbidden child still denies
        self.assertEqual(self._decide(["flock", "--", "-evil", "rm", "-rf", "/"]).rule,
                         "destructive")

    def test_docker_global_option_abbreviation_cannot_hide_the_subcommand(self):
        # The subcommand scan is prefix-aware too: `docker --log-l info run`
        # must not read "info" as the subcommand and skip the run checks.
        for argv in (
                ["docker", "--log-l", "info", "run", "-v", "/:/h", "alpine"],
                ["docker", "--ho", "tcp://127.0.0.1:2376", "run", "--priv", "alpine"],
                ["docker", "--con", "ctx", "exec", "--us", "root", "web", "ls"],
                ["docker", "--conf", "/tmp/c", "run", "--vol", "/:/h", "alpine"],
                ["podman", "--log-l", "debug", "create", "--device", "/dev/mem", "alpine"]):
            with self.subTest(argv=argv):
                decision = self._decide(argv)
                self.assertFalse(decision.allow, f"{argv!r} was allowed")
                self.assertEqual(decision.rule, "docker-root", f"{argv!r}")


class CliCheckTests(unittest.TestCase):
    """`hostexec.py check` is a dry decision printer: never runs anything."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bindir = _make_fixed_path(self._tmp.name)
        self.policy_path = os.path.join(self._tmp.name, "policy.toml")
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            fh.write(f"""
path = [{self.bindir!r}]

[limits]
max_args = 64
max_arg_length = 64

[actors."deadbeef"]
name = "claude"
tier = "default"

[hosts."coding-host"]
kind = "local"

[hosts."storage"]
kind = "ssh"
target = "storage"
forbid = true
""")

    def _check(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "check", "--policy", self.policy_path, *args],
            capture_output=True, text=True,
        )

    def test_allowed_call_prints_allow_and_exits_zero(self):
        out = self._check("--actor", "claude", "--host", "coding-host", "--", "echo", "hi")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("allow", out.stdout)

    def test_denied_call_prints_the_rule_and_exits_nonzero(self):
        out = self._check("--actor", "claude", "--host", "coding-host", "--", "sudo", "id")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("deny", out.stdout)
        self.assertIn("no-sudo", out.stdout)

    def test_check_never_runs_anything(self):
        marker = os.path.join(self._tmp.name, "marker")
        # even an "allowed" argv must never execute -- check is dry, always.
        self._check("--actor", "claude", "--host", "coding-host", "--",
                    "sh", "-c", f"touch {marker}")
        self.assertFalse(os.path.exists(marker))

    def test_unknown_actor_reported(self):
        out = self._check("--actor", "nobody", "--host", "coding-host", "--", "echo", "hi")
        self.assertIn("unknown-actor", out.stdout)

    def test_missing_policy_file_refuses_not_allow_all(self):
        out = subprocess.run(
            [sys.executable, str(CLI), "check", "--policy", "/no/such/policy.toml",
             "--actor", "claude", "--host", "coding-host", "--", "echo", "hi"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("allow\n", out.stdout)


if __name__ == "__main__":
    unittest.main()
