#!/usr/bin/env python3
"""The launch-profile matcher model, the push corpus reader and the real-git
premise tests — one home, imported by ``tests/test_launch_profiles.py`` and
``tests/test_push_corpus.py`` (coding-principles 1: the model is one fact).

Not a test module (no ``test_`` prefix), so no suite has to run it on its own;
the two test files above carry its tests and are what CI wires in.

Matcher model (Claude Code's documented Bash rule semantics; stated once,
here):

- ``Bash(cmd:*)`` - a matcher of exactly ``<cmd>:*`` with no other wildcard
  - is a prefix match: the command equals ``<cmd>`` or starts with
  ``<cmd> `` (one trailing space).
- A rule matcher without ``:*`` and without ``*`` is an exact match: only
  that exact command matches (``env`` matches ``env``, not ``env FOO=1`` -
  hence the harness carries both ``env`` and ``env *``).
- Any other matcher is a shell-style glob over the full command line
  (``fnmatchcase``, where ``*`` spans ``/`` and spaces, so ``*api-keys*``
  matches at any depth). The main-fence entries are globs of this form.
- ``Read/Edit/Write(glob)`` are path globs (``fnmatchcase``, ``*`` spans
  ``/``).
- Compound commands are split on ``&&`` ``||`` ``;`` ``|`` and each part is
  decided separately: any part denied means the whole command is denied;
  the whole is allowed only when every part is allowed, otherwise it is
  unlisted (``none`` - denied automatically under the non-interactive
  launch flags).
- ``deny`` is evaluated before ``allow`` (the precedence rule the
  contradiction test pins). Source: the Claude Code permissions
  documentation - deny rules are evaluated before ask and allow, and a
  matching deny wins regardless of order. The contradiction test pins that
  the RENDERED profiles keep every deny reachable under that documented
  rule; it cannot verify the CLI itself.

Assumptions taken from the Claude Code permissions documentation and pinned
ONLY in this model (re-verify when the CLI changes): deny-before-allow
precedence, and ``*`` crossing ``/`` in path rules. If the CLI ever stops
evaluating deny first, or stops letting ``*`` span ``/``, the tables in the
test files prove nothing until this model is updated.

KNOWN divergences of this model from the real CLI (each pinned by a deny
test, so the gap fails closed, never open):

- The CLI also unwraps ``sudo``/``env``/``xargs`` prefixes when matching
  allow rules; this model matches the literal line. The render compensates
  with leading-``*`` deny globs.
- The CLI may normalize quoting/whitespace and resolve shell aliases and
  functions before matching; this model matches raw text. Round 13 (D-159)
  removed the character-class fences, so a rewritten or substituted ref now
  reaches the classifier rather than a glob - which is the divergence this
  model no longer has to paper over.
- The CLI's compound splitting understands quoting and subshells; this
  model splits naively on the four separators. A fenced command hidden
  inside ``$(...)`` or backticks is not split out here - the hooks lane
  (HOOKS H2) carries that case.

Denial here is on the *name* only: fixture paths are never opened, no real
key file is read, and no command is executed (AGENTS.md section 5).
Placeholder roots (``/example/``, ``/home/x/``) keep the fixtures site-free
(AGENTS.md rule 1).
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TOOL = ROOT / "tools" / "launch_profiles.py"
HARNESS_PATH = ROOT / "catalog" / "agent-harness.json"
PROFILES_DIR = ROOT / "configuration" / "launch-profiles"
CORPUS_PATH = ROOT / "tests" / "fixtures" / "push-corpus.json"

ROLES = (
    "l0-router",
    "l1-coordinator",
    "l1-routing",
    "l2-orchestrator",
    "l3-worker",
    "l3-reviewer",
)
LEAF_ROLES = ("l3-worker", "l3-reviewer")
# Spec 3.2 as of round 13 (routing-00 D-159): NO role pre-grants a push or a
# workflow dispatch. The role split below is left for the fences that still
# differ per role (the leaf push fence) and for the one non-push grant
# (l1-routing's gateway apply).
L1_ROLES = ("l1-coordinator", "l1-routing")
L2_ROLES = ("l2-orchestrator",)
UNGRANTED_ROLES = ("l0-router",)
NON_LEAF_ROLES = tuple(r for r in ROLES if r not in LEAF_ROLES)
ALL_ROLES = ROLES

CORPUS_EXPECTS = ("deny", "allow-lane", "prompt")


def load_tool():
    """Import tools/launch_profiles.py by path (a dashboard-style name the
    suite reaches the same way it reaches tools/registry.py)."""
    spec = importlib.util.spec_from_file_location("autoos_launch_profiles", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_profile(role):
    with open(PROFILES_DIR / (role + ".settings.example.json"), encoding="utf-8") as handle:
        return json.load(handle)


def load_harness():
    with open(HARNESS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def corpus_rows(path=CORPUS_PATH):
    """The push-spelling corpus, in fixture order."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise AssertionError("push-corpus.json must hold a list, got %s" % type(rows))
    return rows


def split_rule(rule):
    """Split a ``Tool(matcher)`` rule into (tool, matcher)."""
    name, sep, matcher = rule.partition("(")
    if not sep or not matcher.endswith(")"):
        raise ValueError("bad permission rule: %r" % (rule,))
    return name, matcher[:-1]


def bash_matches(matcher, command):
    """Whether a Bash rule matcher matches a command line (model above)."""
    if matcher.endswith(":*") and "*" not in matcher[:-2]:
        # Prefix form only: `Bash(cmd:*)` matches the bare command and the
        # command with arguments, nothing else.
        prefix = matcher[:-2]
        return command == prefix or command.startswith(prefix + " ")
    if "*" not in matcher:
        # No wildcard at all: one exact command only.
        return command == matcher
    return fnmatch.fnmatchcase(command, matcher)


def path_matches(matcher, path):
    """Whether a Read/Edit/Write rule glob matches a path (model above)."""
    return fnmatch.fnmatchcase(path, matcher)


_COMPOUND_SPLIT = re.compile(r"&&|\|\||;|\|")


def split_compound(command):
    """Split a shell line on && || ; | (naive: no quote/subshell sense)."""
    return [part.strip() for part in _COMPOUND_SPLIT.split(command) if part.strip()]


def _decide_single(allow, deny, tool, target):
    for rule in deny:
        name, matcher = split_rule(rule)
        if name != tool:
            continue
        hit = bash_matches if tool == "Bash" else path_matches
        if hit(matcher, target):
            return "deny"
    for rule in allow:
        name, matcher = split_rule(rule)
        if name != tool:
            continue
        hit = bash_matches if tool == "Bash" else path_matches
        if hit(matcher, target):
            return "allow"
    return "none"


def decide(profile, tool, target):
    """The profile's decision for (tool, target): deny wins over allow.

    Reads the rules from ``profile["permissions"]`` - the real settings
    shape, so these tests exercise the file the CLI reads. Compound Bash
    lines are split (model above): any part denied denies the whole; the
    whole allows only when every part allows.

    Returns "deny", "allow", or "none" (unlisted - denied automatically for
    non-interactive sessions by the launch flags).
    """
    perms = profile.get("permissions") or {}
    allow = perms.get("allow") or []
    deny = perms.get("deny") or []
    if tool == "Bash":
        parts = split_compound(target)
        if len(parts) > 1:
            results = [_decide_single(allow, deny, tool, part) for part in parts]
            if "deny" in results:
                return "deny"
            if all(result == "allow" for result in results):
                return "allow"
            return "none"
    return _decide_single(allow, deny, tool, target)


def perms_of(profile):
    return profile.get("permissions") or {}


def decide_deny(profile, tool, target):
    return decide(profile, tool, target) == "deny"


def deny_witness(matcher):
    """The first corpus ``deny`` row the Bash ``matcher`` matches, or ``None``.

    The push and dispatch fences have no hand-maintained witness table any more
    (round 13 deleted the last of them with the grants it used to contradict):
    ``tests/fixtures/push-corpus.json`` is the one home of the spellings, so
    both the witness test and the contradiction test resolve a fence to a real
    command here.
    """
    for row in corpus_rows():
        if row["expect"] == "deny" and bash_matches(matcher, row["cmd"]):
            return row["cmd"]
    return None


class RealGitPremiseTests(unittest.TestCase):
    """Prove with real git the push behaviours the fences are designed to catch.

    Builds a scratch bare repo plus a clone in a ``tempfile.TemporaryDirectory``,
    with git config forced local (``GIT_CONFIG_GLOBAL`` to an empty file,
    ``GIT_CONFIG_NOSYSTEM=1``, user.name/email set per repo, no network) and
    proves the premises:

    - ``git push origin L1-x:heads/main``, ``git push origin heads/main`` and
      ``git push origin FETCH_HEAD:heads/main`` each ADVANCE
      ``refs/heads/main`` in the bare repo - the round-11 spellings the fence
      set names as whole ref names, not substrings;
    - ``git push origin L1-x:HEAD`` does NOT advance main but creates a stray
      ``refs/heads/HEAD`` - why ``:HEAD`` is denied;
    - ``git push origin refs/heads/*:refs/heads/*`` advances main - the
      round-12 glob premise. Round 13 dropped the glob-class deny (the rule
      syntax cannot express a literal ``*``), so this row is now the evidence
      for the MED-4 residual: a glob refspec reaches only the classifier.

    Hermetic and fast (< 5 s); never touches the real checkout's remotes.
    Skipped if ``git`` is missing or on Windows, where the subprocess
    environment differs.
    """

    @classmethod
    def setUpClass(cls):
        import sys
        if sys.platform == "win32":
            raise unittest.SkipTest("Real-git premise test skipped on Windows")
        cls.git = cls._find_git()
        if cls.git is None:
            raise unittest.SkipTest("git binary not found")

    @staticmethod
    def _find_git():
        import shutil
        return shutil.which("git")

    def _run_git(self, args, cwd, env=None):
        import subprocess
        result = subprocess.run(
            [self.git] + args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.fail("git %s failed in %s: %s" % (args, cwd, result.stderr))
        return result.stdout.strip()

    def _git_env(self):
        import os
        import tempfile
        env = os.environ.copy()
        # Force git to use only local config, no global/system config.
        # Create a temporary empty file for GIT_CONFIG_GLOBAL (works on Windows too).
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.gitconfig') as f:
            f.write('# empty global config\n')
            global_config = f.name
        env["GIT_CONFIG_GLOBAL"] = global_config
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        # Store the temp file path for cleanup
        self._global_config_file = global_config
        return env

    def _cleanup_git_env(self):
        import os
        if hasattr(self, '_global_config_file'):
            try:
                os.unlink(self._global_config_file)
            except OSError:
                pass

    def _init_bare_repo(self, path, env):
        # git init --bare creates the directory, so run from parent
        import os
        parent = os.path.dirname(path)
        repo_name = os.path.basename(path)
        self._run_git(["init", "--bare", repo_name], parent, env)

    def _clone_repo(self, src, dst, env):
        # git clone creates a directory named after the repo (without .git)
        import os
        repo_name = os.path.basename(src)
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        self._run_git(["clone", src], dst, env)
        # Configure user for commits
        repo_dir = os.path.join(dst, repo_name)
        self._run_git(["config", "user.name", "Test User"], repo_dir, env)
        self._run_git(["config", "user.email", "test@example.com"], repo_dir, env)
        return repo_dir

    def _get_main_sha(self, bare_path, env):
        """Get the current SHA of refs/heads/main in the bare repo."""
        return self._run_git(["rev-parse", "refs/heads/main"], bare_path, env)

    def _seed_main(self, clone_path, env):
        """Initial commit on main in the clone, pushed to the bare repo.

        Returns the SHA main holds in the bare repo afterwards.
        """
        from pathlib import Path
        test_file = Path(clone_path) / "test.txt"
        test_file.write_text("initial\n")
        self._run_git(["add", "test.txt"], clone_path, env)
        self._run_git(["commit", "-m", "initial commit"], clone_path, env)
        # Ensure the branch is named 'main' (git default may be 'master')
        self._run_git(["branch", "-m", "main"], clone_path, env)
        self._run_git(["push", "origin", "main"], clone_path, env)
        return self._run_git(["rev-parse", "HEAD"], clone_path, env)

    def test_push_L1_x_heads_main_advances_main(self):
        """git push origin L1-x:heads/main advances refs/heads/main in bare repo."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            bare_path = Path(tmpdir) / "bare.git"
            env = self._git_env()

            # Create bare repo
            self._init_bare_repo(str(bare_path), env)

            # Clone it
            clone_path = self._clone_repo(str(bare_path), tmpdir, env)

            # Create initial commit on main in the clone
            test_file = Path(clone_path) / "test.txt"
            test_file.write_text("initial\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "initial commit"], clone_path, env)
            # Ensure the branch is named 'main' (git default may be 'master')
            self._run_git(["branch", "-m", "main"], clone_path, env)
            # Push initial main to bare
            self._run_git(["push", "origin", "main"], clone_path, env)

            # Get initial main SHA in bare
            initial_sha = self._get_main_sha(str(bare_path), env)

            # Create a branch L1-x with a new commit
            self._run_git(["checkout", "-b", "L1-x"], clone_path, env)
            test_file.write_text("L1-x commit\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "L1-x commit"], clone_path, env)
            l1_x_sha = self._run_git(["rev-parse", "HEAD"], clone_path, env)

            # Push L1-x:heads/main to bare
            self._run_git(["push", "origin", "L1-x:heads/main"], clone_path, env)

            # Assert main in bare advanced to L1-x commit
            new_sha = self._get_main_sha(str(bare_path), env)
            self.assertEqual(new_sha, l1_x_sha)
            self.assertNotEqual(new_sha, initial_sha)

    def test_push_heads_main_advances_main(self):
        """git push origin heads/main advances refs/heads/main in bare repo."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            bare_path = Path(tmpdir) / "bare.git"
            env = self._git_env()

            # Create bare repo
            self._init_bare_repo(str(bare_path), env)

            # Clone it
            clone_path = self._clone_repo(str(bare_path), tmpdir, env)

            # Create initial commit on main in the clone
            test_file = Path(clone_path) / "test.txt"
            test_file.write_text("initial\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "initial commit"], clone_path, env)
            # Ensure the branch is named 'main' (git default may be 'master')
            self._run_git(["branch", "-m", "main"], clone_path, env)
            # Push initial main to bare
            self._run_git(["push", "origin", "main"], clone_path, env)

            # Get initial main SHA in bare
            initial_sha = self._get_main_sha(str(bare_path), env)

            # Make a new commit on local main
            test_file.write_text("heads/main commit\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "heads/main commit"], clone_path, env)
            new_main_sha = self._run_git(["rev-parse", "HEAD"], clone_path, env)

            # Push heads/main to bare
            self._run_git(["push", "origin", "heads/main"], clone_path, env)

            # Assert main in bare advanced to new commit
            new_sha = self._get_main_sha(str(bare_path), env)
            self.assertEqual(new_sha, new_main_sha)
            self.assertNotEqual(new_sha, initial_sha)

    def test_push_FETCH_HEAD_heads_main_advances_main(self):
        """git push origin FETCH_HEAD:heads/main advances refs/heads/main in bare repo."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            bare_path = Path(tmpdir) / "bare.git"
            env = self._git_env()

            # Create bare repo
            self._init_bare_repo(str(bare_path), env)

            # Clone it
            clone_path = self._clone_repo(str(bare_path), tmpdir, env)

            # Create initial commit on main in the clone
            test_file = Path(clone_path) / "test.txt"
            test_file.write_text("initial\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "initial commit"], clone_path, env)
            # Ensure the branch is named 'main' (git default may be 'master')
            self._run_git(["branch", "-m", "main"], clone_path, env)
            # Push initial main to bare
            self._run_git(["push", "origin", "main"], clone_path, env)

            # Get initial main SHA in bare
            initial_sha = self._get_main_sha(str(bare_path), env)

            # Create a new commit on a branch and push it
            self._run_git(["checkout", "-b", "feature"], clone_path, env)
            test_file.write_text("feature commit\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "feature commit"], clone_path, env)
            feature_sha = self._run_git(["rev-parse", "HEAD"], clone_path, env)
            # Push feature to origin so we can fetch it
            self._run_git(["push", "origin", "feature"], clone_path, env)

            # Fetch the feature branch - FETCH_HEAD will point to it
            self._run_git(["fetch", "origin", "feature"], clone_path, env)

            # Push FETCH_HEAD:heads/main to bare (FETCH_HEAD points to fetched feature)
            self._run_git(["push", "origin", "FETCH_HEAD:heads/main"], clone_path, env)

            # Assert main in bare advanced to feature commit
            new_sha = self._get_main_sha(str(bare_path), env)
            self.assertEqual(new_sha, feature_sha)
            self.assertNotEqual(new_sha, initial_sha)

    def test_push_L1_x_HEAD_creates_stray_HEAD_branch(self):
        """git push origin L1-x:HEAD does NOT advance refs/heads/main; it creates a stray 'HEAD' branch on the remote.

        This demonstrates why the :HEAD form is denied: it does not update main as one might
        expect, but instead creates a branch literally named 'HEAD' on the remote, leaving
        main unchanged. The fence denies this because the outcome is not what a user intends.
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            bare_path = Path(tmpdir) / "bare.git"
            env = self._git_env()

            # Create bare repo with HEAD pointing to main
            self._init_bare_repo(str(bare_path), env)
            self._run_git(["symbolic-ref", "HEAD", "refs/heads/main"], str(bare_path), env)

            # Clone it
            clone_path = self._clone_repo(str(bare_path), tmpdir, env)

            # Create initial commit on main in the clone
            test_file = Path(clone_path) / "test.txt"
            test_file.write_text("initial\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "initial commit"], clone_path, env)
            # Ensure the branch is named 'main' (git default may be 'master')
            self._run_git(["branch", "-m", "main"], clone_path, env)
            # Push initial main to bare
            self._run_git(["push", "origin", "main"], clone_path, env)

            # Get initial main SHA in bare
            initial_main_sha = self._get_main_sha(str(bare_path), env)

            # Create a branch L1-x with a new commit
            self._run_git(["checkout", "-b", "L1-x"], clone_path, env)
            test_file.write_text("L1-x commit\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "L1-x commit"], clone_path, env)
            l1_x_sha = self._run_git(["rev-parse", "HEAD"], clone_path, env)

            # Push L1-x:HEAD to bare
            self._run_git(["push", "origin", "L1-x:HEAD"], clone_path, env)

            # Assert main in bare did NOT advance (it stays at initial commit)
            main_sha_after = self._get_main_sha(str(bare_path), env)
            self.assertEqual(main_sha_after, initial_main_sha,
                "refs/heads/main should NOT change after git push origin L1-x:HEAD")

            # Verify a stray 'HEAD' branch was created on the remote
            head_branch_sha = self._run_git(["rev-parse", "refs/heads/HEAD"], str(bare_path), env)
            self.assertEqual(head_branch_sha, l1_x_sha,
                "A stray refs/heads/HEAD branch should be created pointing to L1-x commit")

            # Verify remote HEAD still points to main (not to the new HEAD branch)
            remote_head_target = self._run_git(["symbolic-ref", "HEAD"], str(bare_path), env)
            self.assertEqual(remote_head_target, "refs/heads/main",
                "Remote HEAD should still point to main, not to the stray HEAD branch")

    def test_push_refspec_glob_advances_main(self):
        """git push origin 'refs/heads/*:refs/heads/*' advances refs/heads/main when local main is ahead (argv list, no shell)."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            bare_path = Path(tmpdir) / "bare.git"
            env = self._git_env()

            # Create bare repo
            self._init_bare_repo(str(bare_path), env)

            # Clone it
            clone_path = self._clone_repo(str(bare_path), tmpdir, env)

            # Create initial commit on main in the clone
            test_file = Path(clone_path) / "test.txt"
            test_file.write_text("initial\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "initial commit"], clone_path, env)
            # Ensure the branch is named 'main' (git default may be 'master')
            self._run_git(["branch", "-m", "main"], clone_path, env)
            # Push initial main to bare
            self._run_git(["push", "origin", "main"], clone_path, env)

            # Get initial main SHA in bare
            initial_sha = self._get_main_sha(str(bare_path), env)

            # Make a new commit on local main (local main is ahead of remote)
            test_file.write_text("main ahead commit\n")
            self._run_git(["add", "test.txt"], clone_path, env)
            self._run_git(["commit", "-m", "main ahead commit"], clone_path, env)
            new_main_sha = self._run_git(["rev-parse", "HEAD"], clone_path, env)

            # Push with refspec glob (argv list, no shell): refs/heads/*:refs/heads/*
            self._run_git(["push", "origin", "refs/heads/*:refs/heads/*"], clone_path, env)

            # Assert main in bare advanced to new commit
            new_sha = self._get_main_sha(str(bare_path), env)
            self.assertEqual(new_sha, new_main_sha)
            self.assertNotEqual(new_sha, initial_sha)

    def tearDown(self):
        self._cleanup_git_env()
