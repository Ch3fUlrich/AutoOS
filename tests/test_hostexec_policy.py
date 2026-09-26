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
