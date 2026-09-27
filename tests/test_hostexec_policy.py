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
    ["setsid", "ls"],
    ["nohup", "ls"],
)


def _generated_wrapper_cases():
    cases = []
    for wrapper, spellings in _WRAPPER_OPTION_SPELLINGS.items():
        for spelling in spellings:
            for child, rule in _FORBIDDEN_CHILDREN:
                cases.append(([wrapper, *spelling, *child], rule))
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
        self.assertGreater(len(cases), 150)
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
