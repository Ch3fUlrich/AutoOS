#!/usr/bin/env python3
"""ORCH-A1 phase 1: role launch-profile scope tables and the always-deny
contradiction test.

The six tracked templates ``configuration/launch-profiles/<role>.settings.
example.json`` are rendered by ``tools/launch_profiles.py`` from
``catalog/agent-harness.json`` fences plus the profile-specific always-deny
entries of spec section 3.3. These tests drive real command/path strings
through the rendered matchers and assert the decision:

- settings shape (H1): every template carries its rules under
  ``permissions.allow`` / ``permissions.deny`` - the only shape Claude Code
  reads - with no top-level ``allow``/``deny``/``permissionMode``/
  ``permissionPrompts`` keys;
- branch scope (spec 3.2): every spelling of "push to main" denies, a lane
  branch push does not;
- secret scope (spec 3.3): every secret pattern deny-matches its target, and
  a harmless look-alike does not;
- contradiction (A1-D5): a contradictory ``allow`` for an always-deny entry
  still decides ``deny``.

Matcher model (Claude Code's documented Bash rule semantics; stated once,
here):

- ``Bash(cmd:*)`` - a matcher of exactly ``<cmd>:*`` with no other wildcard
  - is a prefix match: the command equals ``<cmd>`` or starts with
  ``<cmd> `` (one trailing space). ``Bash(git push:*)`` is the coordinator
  lane-push grant.
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
  launch flags, the Phase 2 concern noted in the generator docstring).
- ``deny`` is evaluated before ``allow`` (the precedence rule the
  contradiction test pins).

KNOWN divergences of this model from the real CLI (each pinned by a deny
test, so the gap fails closed, never open):

- The CLI also unwraps ``sudo``/``env``/``xargs`` prefixes when matching
  allow rules; this model matches the literal line. The render compensates
  with leading-``*`` deny globs, and ``test_prefixed_main_push_is_denied``
  pins ``sudo``/``env``/``xargs`` ``git push origin main`` to deny on every
  profile.
- The CLI may normalize quoting/whitespace and resolve shell aliases and
  functions before matching; this model matches raw text. A push via an
  alias is invisible to text fencing, which is why server-side branch
  protection on ``main`` is load-bearing, not a backstop (spec 3.3).
- The CLI's compound splitting understands quoting and subshells; this
  model splits naively on the four separators. A fenced command hidden
  inside ``$(...)`` or backticks is not split out here - branch protection
  carries that case too.

Denial here is on the *name* only: fixture paths are never opened, no real
key file is read, and no command is executed (AGENTS.md section 5; the same
rule the ``t3-reviewer`` fence case in ``tests/linux/33-documentation.sh``
follows). Placeholder roots (``/example/``, ``/home/x/``) keep the fixtures
site-free (AGENTS.md rule 1).

Run directly (``python3 tests/test_launch_profiles.py``) or via
``python3 -m unittest discover -s tests -p "test_launch_profiles.py"`` -
stdlib only, runnable on a machine where nothing is installed (AGENTS.md
section 5). Path-independent: everything is anchored on ROOT, never on the
current working directory.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "launch_profiles.py"
HARNESS_PATH = ROOT / "catalog" / "agent-harness.json"
PROFILES_DIR = ROOT / "configuration" / "launch-profiles"

ROLES = (
    "l0-router",
    "l1-coordinator",
    "l1-routing",
    "l2-orchestrator",
    "l3-worker",
    "l3-reviewer",
)
LEAF_ROLES = ("l3-worker", "l3-reviewer")
# Spec 3.2 as fixed in review round 1: only the L1 coordinator roles carry
# the lane-push / workflow-dispatch pre-grants. l0-router and l2-orchestrator
# run ungranted (unlisted is fail-closed under the launch flags).
L1_ROLES = ("l1-coordinator", "l1-routing")
UNGRANTED_ROLES = ("l0-router", "l2-orchestrator")
NON_LEAF_ROLES = tuple(r for r in ROLES if r not in LEAF_ROLES)


def _load_tool():
    """Import tools/launch_profiles.py by path (a dashboard-style name the
    suite reaches the same way it reaches tools/registry.py)."""
    spec = importlib.util.spec_from_file_location("autoos_launch_profiles", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lp = _load_tool()


def load_profile(role):
    with open(PROFILES_DIR / (role + ".settings.example.json"), encoding="utf-8") as handle:
        return json.load(handle)


def load_harness():
    with open(HARNESS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


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
    shape (H1), so these tests exercise the file the CLI reads. Compound
    Bash lines are split (model above): any part denied denies the whole;
    the whole allows only when every part allows.

    Returns "deny", "allow", or "none" (unlisted - denied automatically for
    non-interactive sessions by the launch flags, a Phase 2 concern).
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


class MatcherModelTests(unittest.TestCase):
    """Pin the documented model itself, so the tables below mean something."""

    def test_prefix_form_matches_command_with_arguments(self):
        self.assertTrue(bash_matches("git push:*", "git push origin wt-example-1"))
        self.assertTrue(bash_matches("git push:*", "git push"))
        self.assertFalse(bash_matches("git push:*", "git pushorigin"))

    def test_bare_matcher_is_exact_not_prefix(self):
        self.assertTrue(bash_matches("git push", "git push"))
        self.assertFalse(bash_matches("git push", "git push origin wt-example-1"))
        self.assertFalse(bash_matches("set", "setup.sh --check"))

    def test_glob_form_matches_the_whole_command_line(self):
        # KEYDENY lesson: a fence is a decision on a real command, so the
        # glob sees the whole line, not one path argument.
        self.assertTrue(
            bash_matches("*api-keys*", "cat configuration/api-keys.yml configuration/notes.md")
        )
        self.assertTrue(bash_matches("*git push * main", "sudo git push origin main"))

    def test_path_glob_star_spans_separators(self):
        self.assertTrue(path_matches("*api-keys*", "configuration/api-keys.yml"))
        self.assertTrue(path_matches("*.env.*", "stack.env.example"))
        self.assertFalse(path_matches("*.env", "stack.env.example"))

    def test_compound_split_denies_when_any_part_denies(self):
        profile = {
            "permissions": {
                "allow": ["Bash(git push:*)"],
                "deny": ["Bash(*git push * main)"],
            }
        }
        self.assertEqual(decide(profile, "Bash", "git fetch && git push origin main"), "deny")
        self.assertEqual(decide(profile, "Bash", "git status; git push origin main"), "deny")
        self.assertEqual(decide(profile, "Bash", "echo x | git push origin main"), "deny")

    def test_compound_allows_only_when_every_part_allows(self):
        profile = {
            "permissions": {
                "allow": ["Bash(git push:*)"],
                "deny": ["Bash(*git push * main)"],
            }
        }
        self.assertEqual(
            decide(profile, "Bash", "git push origin wt-a && git push origin wt-b"), "allow"
        )
        # One unlisted part keeps the whole unlisted (fail closed).
        self.assertEqual(decide(profile, "Bash", "git fetch && git push origin wt-a"), "none")

    def test_deny_wins_over_allow(self):
        profile = {
            "permissions": {
                "allow": ["Bash(git push:*)"],
                "deny": ["Bash(*git push * main)"],
            }
        }
        self.assertEqual(decide(profile, "Bash", "git push origin main"), "deny")
        self.assertEqual(decide(profile, "Bash", "git push origin wt-example-1"), "allow")
        self.assertEqual(decide(profile, "Bash", "git status"), "none")


# ---------------------------------------------------------------------------
# settings shape (H1)


class SettingsShapeTests(unittest.TestCase):
    def test_templates_use_the_real_settings_shape(self):
        # Claude Code reads permission rules ONLY under top-level
        # `permissions`; top-level allow/deny and permissionMode /
        # permissionPrompts are ignored silently, so the render must not
        # emit them.
        for role in ROLES:
            profile = load_profile(role)
            with self.subTest(role=role):
                for banned in ("allow", "deny", "permissionMode", "permissionPrompts"):
                    self.assertNotIn(banned, profile)
                perms = profile["permissions"]
                self.assertIsInstance(perms["allow"], list)
                self.assertIsInstance(perms["deny"], list)
                self.assertTrue(perms["deny"])
                for rule in perms["allow"] + perms["deny"]:
                    split_rule(rule)  # every entry is Tool(matcher) shaped

    def test_decide_reads_the_real_shape(self):
        # The scope tables below exercise the rendered file through decide(),
        # so decide() must read the same `permissions` object the CLI reads.
        profile = load_profile("l1-coordinator")
        self.assertEqual(decide(profile, "Bash", "git push origin wt-example-1"), "allow")
        self.assertEqual(decide(profile, "Bash", "git push origin main"), "deny")
        bare_old_shape = {"allow": ["Bash(git push:*)"], "deny": ["Bash(*git push * main)"]}
        self.assertEqual(decide(bare_old_shape, "Bash", "git push origin main"), "none")


# ---------------------------------------------------------------------------
# branch scope (spec 3.2)


# Every way to spell "push to main" the KEYDENY lesson requires: each must be
# denied by every rendered profile's matchers. Includes the H2 forms without
# the word "main" as a ref (--all/--mirror/HEAD/bare): a bare `git push`
# with no refspec pushes the checked-out branch, which may be main.
BRANCH_DENY_COMMANDS = (
    "git push origin main",
    "git push upstream main",
    "git push origin main:refs/heads/other",
    "git push origin :main",
    "git push origin :refs/heads/main",
    "git push origin HEAD:main",
    "git push origin HEAD:refs/heads/main",
    "git push origin +main",
    "git push origin +refs/heads/main",
    "git push --force origin main",
    "git push origin main --force",
    "git push origin main --force-with-lease",
    "git push -f origin main",
    "git push origin main -f",
    "git push -u origin main",
    "git push origin main --tags",
    "git push --tags",
    "git push origin --tags",
    "git push origin refs/heads/main",
    "git push origin main --dry-run",
    "git push --all",
    "git push --mirror",
    "git push origin --all",
    "git push origin --mirror",
    "git push --all origin wt-example-1",
    "git push origin HEAD",
    "git push upstream HEAD",
    "git push",
)

# Lane pushes: spelled without "main" as a ref and without the H2 flag/HEAD
# forms, so the fence must let them through. main2 pins the no-over-deny
# boundary (a branch merely containing "main" is not main). The -u and
# uppercase spellings pin the coordinator grant's explicit-refspec pushes.
BRANCH_LANE_COMMANDS = (
    "git push origin wt-example-1",
    "git push origin agent/20260928-120000-example-task-a1b2c3",
    "git push origin main2",
    "git push upstream wt-example-1",
    "git push origin WS-FOO",
    "git push -u origin L1-backlog/x",
)


class BranchScopeTests(unittest.TestCase):
    def test_push_to_main_is_denied_by_every_profile(self):
        for role in ROLES:
            profile = load_profile(role)
            for command in BRANCH_DENY_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )

    def test_lane_branch_push_is_not_denied(self):
        # The main fence must not catch lane pushes on profiles that may
        # push at all. Leaf profiles deny every push via the rendered
        # bash_deny_leaf fence (asserted in
        # test_lane_branch_push_is_pre_granted_to_l1_only).
        for role in NON_LEAF_ROLES:
            profile = load_profile(role)
            for command in BRANCH_LANE_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertFalse(
                        decide_deny(profile, "Bash", command),
                        "%s is denied by %s" % (command, role),
                    )

    def test_lane_branch_push_is_pre_granted_to_l1_only(self):
        # Spec 3.2 as fixed in review round 1: the lane-push grant is L1
        # only. Spec Q9 leaves workers/reviewers at read-only, and spec 2
        # makes a leaf grant smaller, never larger, than its harness leaf
        # fences - so a lane push is allowed on L1, denied outright on
        # leaves (by the rendered bash_deny_leaf push fence, not merely
        # unlisted), and unlisted (fail closed) on the ungranted l0/l2.
        for role in L1_ROLES:
            profile = load_profile(role)
            for command in BRANCH_LANE_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertEqual(decide(profile, "Bash", command), "allow")
        for role in LEAF_ROLES:
            profile = load_profile(role)
            for command in BRANCH_LANE_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertEqual(decide(profile, "Bash", command), "deny")
        for role in UNGRANTED_ROLES:
            profile = load_profile(role)
            for command in BRANCH_LANE_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertEqual(decide(profile, "Bash", command), "none")

    def test_explicit_main_dispatch_of_gh_workflow_run_is_denied(self):
        # Spec Q4's measured check: the L1 `gh workflow run` grant stays,
        # but a dispatch naming main is denied.
        denied = (
            "gh workflow run ci.yml --ref main",
            "gh workflow run ci.yml --ref refs/heads/main",
            "gh workflow run ci.yml --ref=main",
        )
        for role in ROLES:
            profile = load_profile(role)
            for command in denied:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )

    def test_lane_dispatch_of_gh_workflow_run_is_l1_only(self):
        for role in L1_ROLES:
            profile = load_profile(role)
            command = "gh workflow run ci.yml --ref wt-example-1"
            with self.subTest(role=role, command=command):
                self.assertFalse(decide_deny(profile, "Bash", command))
                self.assertEqual(decide(profile, "Bash", command), "allow")
        for role in UNGRANTED_ROLES + LEAF_ROLES:
            profile = load_profile(role)
            command = "gh workflow run ci.yml --ref wt-example-1"
            with self.subTest(role=role, command=command):
                self.assertEqual(decide(profile, "Bash", command), "none")


# ---------------------------------------------------------------------------
# wrapper/option forms and compound commands (H3), prefix divergences (M1)


# Wrapper/option argv shapes: git reaches push without starting with the
# literal `git push`, so the anchored main fence cannot see them. Every one
# denies on every role - including lane refs, because a wrapper push is
# never pre-granted.
WRAPPER_DENY_COMMANDS = (
    "git -C /example/repo push origin main",
    "git -C /example/repo push origin wt-example-1",
    "git --git-dir=/example/repo/.git push origin main",
    "git --git-dir=/example/repo/.git push origin wt-example-1",
    "git -c foo.bar=baz push origin main",
    "git -c foo.bar=baz push origin wt-example-1",
)

# Compound lines: Claude Code splits on && || ; | and checks each part, so
# the main fence must catch the push part after the split.
COMPOUND_DENY_COMMANDS = (
    "git fetch && git push origin main",
    "git fetch || git push origin main",
    "git status; git push origin main",
    "echo x | git push origin main",
)

# M1 divergences: the CLI unwraps sudo/env/xargs prefixes for allow
# matching, so the dangerous spelling must already be in the deny set via
# the leading-* globs.
PREFIXED_DENY_COMMANDS = (
    "sudo git push origin main",
    "env git push origin main",
    "xargs git push origin main",
    "sudo git push",
)


class WrapperScopeTests(unittest.TestCase):
    def test_wrapper_push_forms_are_denied_by_every_profile(self):
        for role in ROLES:
            profile = load_profile(role)
            for command in WRAPPER_DENY_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )

    def test_compound_push_to_main_is_denied_by_every_profile(self):
        for role in ROLES:
            profile = load_profile(role)
            for command in COMPOUND_DENY_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )

    def test_compound_lane_push_is_pre_granted_to_l1_only(self):
        command = "git push origin wt-a && git push origin wt-b"
        for role in L1_ROLES:
            with self.subTest(role=role):
                self.assertEqual(decide(load_profile(role), "Bash", command), "allow")
        for role in LEAF_ROLES:
            with self.subTest(role=role):
                self.assertEqual(decide(load_profile(role), "Bash", command), "deny")

    def test_prefixed_main_push_is_denied_by_every_profile(self):
        for role in ROLES:
            profile = load_profile(role)
            for command in PREFIXED_DENY_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )


# ---------------------------------------------------------------------------
# secret scope (spec 3.3)


# Harness-derived Read targets, keyed by the exact fences.read_deny_all
# pattern: a KeyError here (not a silent skip) is the signal that the
# harness grew a pattern this table does not cover yet.
READ_TARGETS = {
    "*.env": (".env", "environment.txt"),
    "*.env.*": ("stack.env.prod", "environment.txt"),
    "*auth.json": ("/example/.config/auth.json", "/example/.config/auth.json.example"),
    "*api_keys*": ("/example/legacy/api_keys.yml", "/example/legacy/key-notes.md"),
    "*api-keys*": ("configuration/api-keys.yml", "configuration/notes.md"),
    "*client.key*": ("/example/ai-stack/client.key", "/example/ai-stack/client-cert.txt"),
    "*manage.key*": ("/example/ai-stack/manage.key", "/example/ai-stack/manage-notes.txt"),
    "*.claude.json*": ("/home/x/.claude.json", "/home/x/claude.json"),
}

# Harness-derived Bash targets, keyed by the exact fences.bash_deny_all
# pattern the generator selects as secret-relevant.
BASH_TARGETS = {
    "*auth.json*": ("cat /example/.config/auth.json", "cat /example/notes.txt"),
    "*api_keys*": ("cat /example/legacy/api_keys.yml", "cat /example/legacy/key-notes.md"),
    "*api-keys*": ("cat configuration/api-keys.yml", "cat configuration/notes.md"),
    "*client.key*": ("cat /example/ai-stack/client.key", "cat /example/ai-stack/client-cert.txt"),
    "*manage.key*": ("cat /example/ai-stack/manage.key", "cat /example/ai-stack/manage-notes.txt"),
    "*.env*": ("cat /example/stack.env", "cat /example/environment.txt"),
    "env": ("env", "envoy deploy"),
    "env *": ("env FOO=1 id", "envoy deploy"),
    "printenv*": ("printenv", "printf '%s' x"),
    "*.claude.json*": ("cat /home/x/.claude.json", "cat /home/x/claude.json"),
    "*.config/opencode*": (
        "cat /home/x/.config/opencode/opencode.json",
        "cat /home/x/.config/notes.txt",
    ),
    "*.local/share/opencode*": (
        "ls /home/x/.local/share/opencode",
        "ls /home/x/.local/share/notes.txt",
    ),
    # Bare-word rules are exact matches under this model; a longer command
    # starting with the same letters does not match.
    "set": ("set", "setup.sh --check"),
    "cat /proc/*": ("cat /proc/1/environ", "cat /example/proc-notes.txt"),
}

# Profile-specific secret extras (spec 3.3 names *.vault.yml / *.pem, which
# the harness fences do not list): (tool, pattern, target, look-alike).
EXTRA_TARGETS = (
    ("Bash", "*.vault.yml", "cat /example/ansible/group.vault.yml", "cat /example/ansible/group.yml"),
    ("Bash", "*.pem", "cat /example/certs/deploy.pem", "cat /example/certs/deploy.pem.example"),
    ("Read", "*.vault.yml", "/example/ansible/group.vault.yml", "/example/ansible/group.yml"),
    ("Read", "*.pem", "/example/certs/deploy.pem", "/example/certs/deploy.pem.example"),
)

# Profile-specific ~/.claude.json write patterns (Edit, Write and the shell
# tee / redirect / sed -i forms): (tool, pattern, target, look-alike).
CLAUDE_WRITE_TARGETS = (
    ("Edit", "*.claude.json*", "/home/x/.claude.json", "/home/x/claude.json"),
    ("Write", "*.claude.json*", "/home/x/.claude.json", "/home/x/claude.json"),
    ("Bash", "*>*.claude.json*", "echo x >> /home/x/.claude.json", "echo x >> /home/x/claude.json"),
    ("Bash", "*tee *.claude.json*", "printf x | tee /home/x/.claude.json", "printf x | tee /home/x/claude.json"),
    ("Bash", "*sed*-i*.claude.json*", "sed -i s/a/b/ /home/x/.claude.json", "sed s/a/b/ /home/x/claude.json"),
)

# The current secret/credential entries of fences.bash_deny_all, pinned so
# the selector cannot silently stop classifying one (M2). Every entry here
# must be classified secret by bash_secret_patterns() today.
CURRENT_SECRET_FENCES = (
    "*auth.json*",
    "*api_keys*",
    "*api-keys*",
    "*client.key*",
    "*manage.key*",
    "*.env*",
    "env",
    "env *",
    "printenv*",
    "*.claude.json*",
    "*.config/opencode*",
    "*.local/share/opencode*",
    "set",
    "cat /proc/*",
)

# The operational entries of fences.bash_deny_all, pinned as deliberately
# NOT rendered: under deny-precedence a rendered `gh *` would swallow the L1
# `gh workflow run` grant the spec pre-approves. A future harness entry that
# is neither selector-secret nor listed here fails the completeness test
# instead of being silently dropped (M2).
KNOWN_OPERATIONAL_FENCES = (
    "git merge*",
    "git rebase*",
    "git reset --hard*",
    "git branch -D*",
    "git clean*",
    "git worktree remove*",
    "*git *merge*",
    "*git *rebase*",
    "*git *reset --hard*",
    "*git *branch -D*",
    "*git *clean*",
    "*git *worktree remove*",
    "gh *",
    "rm -rf /*",
    "rm -rf ~*",
    "sudo *",
    "docker compose *",
    "docker rm*",
)


class SecretScopeTests(unittest.TestCase):
    def test_harness_read_patterns_are_rendered_and_match(self):
        fences = load_harness()["fences"]
        for role in ROLES:
            deny = perms_of(load_profile(role))["deny"]
            for pattern in fences["read_deny_all"]:
                rule = "Read(%s)" % pattern
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, deny)
                    target, lookalike = READ_TARGETS[pattern]
                    self.assertTrue(decide(load_profile(role), "Read", target) == "deny")
                    self.assertFalse(decide_deny(load_profile(role), "Read", lookalike))

    def test_harness_secret_bash_patterns_are_rendered_and_match(self):
        fences = load_harness()["fences"]
        selected = lp.bash_secret_patterns(fences["bash_deny_all"], fences["read_deny_all"])
        for role in ROLES:
            deny = perms_of(load_profile(role))["deny"]
            for pattern in selected:
                rule = "Bash(%s)" % pattern
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, deny)
                    target, lookalike = BASH_TARGETS[pattern]
                    self.assertTrue(decide(load_profile(role), "Bash", target) == "deny")
                    self.assertFalse(decide_deny(load_profile(role), "Bash", lookalike))

    def test_secret_subset_is_complete(self):
        # M2: "secret" is defined by the same rule the generator uses
        # (bash_secret_patterns - one home, never restated), and every entry
        # it classifies is present in every profile's deny list.
        fences = load_harness()["fences"]
        selected = lp.bash_secret_patterns(fences["bash_deny_all"], fences["read_deny_all"])
        for role in ROLES:
            deny = perms_of(load_profile(role))["deny"]
            for pattern in selected:
                with self.subTest(role=role, pattern=pattern):
                    self.assertIn("Bash(%s)" % pattern, deny)

    def test_selector_still_classifies_every_current_secret_fence(self):
        # M2: pins the selector against today's harness, so a future
        # bash-only secret fence the selector misses cannot hide behind a
        # passing subset test.
        fences = load_harness()["fences"]
        selected = lp.bash_secret_patterns(fences["bash_deny_all"], fences["read_deny_all"])
        for pattern in CURRENT_SECRET_FENCES:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, fences["bash_deny_all"])
                self.assertIn(pattern, selected)

    def test_no_bash_deny_entry_is_silently_unrendered(self):
        # M2: every fences.bash_deny_all entry is either selector-secret
        # (rendered into every profile) or a known-operational fence
        # (deliberately not rendered). A harness entry that is neither fails
        # here until a human classifies it.
        fences = load_harness()["fences"]
        selected = lp.bash_secret_patterns(fences["bash_deny_all"], fences["read_deny_all"])
        for pattern in fences["bash_deny_all"]:
            with self.subTest(pattern=pattern):
                if pattern in selected:
                    for role in ROLES:
                        self.assertIn(
                            "Bash(%s)" % pattern, perms_of(load_profile(role))["deny"]
                        )
                else:
                    self.assertIn(pattern, KNOWN_OPERATIONAL_FENCES)

    def test_profile_specific_secret_extras_match(self):
        for role in ROLES:
            profile = load_profile(role)
            deny = perms_of(profile)["deny"]
            for tool, pattern, target, lookalike in EXTRA_TARGETS:
                rule = "%s(%s)" % (tool, pattern)
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, deny)
                    self.assertTrue(decide(profile, tool, target) == "deny")
                    self.assertFalse(decide_deny(profile, tool, lookalike))

    def test_claude_json_writes_are_denied(self):
        for role in ROLES:
            profile = load_profile(role)
            deny = perms_of(profile)["deny"]
            for tool, pattern, target, lookalike in CLAUDE_WRITE_TARGETS:
                rule = "%s(%s)" % (tool, pattern)
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, deny)
                    self.assertTrue(decide(profile, tool, target) == "deny")
                    self.assertFalse(decide_deny(profile, tool, lookalike))

    def test_example_templates_stay_denied(self):
        # Claude Code evaluates deny before allow, so the harness
        # read_allow_all hole (which the opencode overlay expresses as a
        # later allow) is inexpressible here: the generator deliberately
        # emits no dead allow entries, and the example templates under a
        # denied glob stay denied. Pinned so nobody "fixes" it with an
        # allow that loses the precedence race.
        denied = (
            ("Read", "configuration/api-keys.example.yml"),
            ("Read", "stack.env.example"),
        )
        for role in ROLES:
            profile = load_profile(role)
            for tool, target in denied:
                with self.subTest(role=role, target=target):
                    self.assertTrue(decide_deny(profile, tool, target))


# ---------------------------------------------------------------------------
# contradiction (A1-D5)


# Profile-specific always-deny entries with one command each that the entry
# matches: the contradiction target proves the pre-granted allow (e.g. the
# L1 lane-push grant) cannot leak through the deny.
CONTRADICTION_TARGETS = {
    "Bash(*git push * main)": "git push origin main",
    "Bash(*git push * main *)": "git push origin main --force",
    "Bash(*git push * main:*)": "git push origin main:refs/heads/other",
    "Bash(*git push *:main*)": "git push origin :main",
    "Bash(*git push *HEAD:*main*)": "git push origin HEAD:main",
    "Bash(*git push *+*main*)": "git push origin +main",
    "Bash(*git push *refs/heads/main*)": "git push origin refs/heads/main",
    "Bash(*git push *--force*)": "git push --force origin wt-example-1",
    "Bash(*git push *-f *)": "git push -f origin wt-example-1",
    "Bash(*git push *-f)": "git push origin wt-example-1 -f",
    "Bash(*git push *--tags*)": "git push --tags",
    "Bash(*git push --all*)": "git push --all",
    "Bash(*git push * --all*)": "git push origin --all",
    "Bash(*git push --mirror*)": "git push --mirror",
    "Bash(*git push * --mirror*)": "git push origin --mirror",
    "Bash(*git push * HEAD*)": "git push origin HEAD",
    "Bash(git push)": "git push",
    "Bash(* git push)": "sudo git push",
    "Bash(git -C * push*)": "git -C /example/repo push origin wt-example-1",
    "Bash(git --git-dir* push*)": "git --git-dir=/example/repo/.git push origin wt-example-1",
    "Bash(git -c * push*)": "git -c foo.bar=baz push origin wt-example-1",
    "Bash(gh workflow run *--ref *main*)": "gh workflow run ci.yml --ref main",
    "Bash(gh workflow run *--ref=*main*)": "gh workflow run ci.yml --ref=main",
}

# Leaf-only push/commit fences (bash_deny_leaf) with one command each that
# the entry matches. The push rows reuse the lane push the leaf must never
# make; the rest name their own verb.
LEAF_CONTRADICTION_TARGETS = {
    "Bash(git push*)": "git push origin wt-example-1",
    "Bash(*git *push*)": "git push origin wt-example-1",
    "Bash(git commit*)": "git commit -m wt-example-1",
    "Bash(*git *commit*)": "git commit -m wt-example-1",
    "Bash(*git *tag*)": "git tag wt-example-1",
    "Bash(*git *update-ref*)": "git update-ref refs/heads/wt-example-1 HEAD",
    "Bash(*git *stash*)": "git stash push",
    "Bash(*git *checkout*)": "git checkout wt-example-1",
    "Bash(*git *switch*)": "git switch -c wt-example-1",
    "Bash(*git *restore*)": "git restore notes.txt",
}


class ContradictionTests(unittest.TestCase):
    """A1-D5: deny takes precedence over allow."""

    def contradiction_target(self, rule):
        """One (tool, target) the deny rule matches, for every deny entry."""
        name, matcher = split_rule(rule)
        if rule in CONTRADICTION_TARGETS:
            return name, CONTRADICTION_TARGETS[rule]
        if rule in LEAF_CONTRADICTION_TARGETS:
            return name, LEAF_CONTRADICTION_TARGETS[rule]
        if name == "Bash" and matcher in BASH_TARGETS:
            return name, BASH_TARGETS[matcher][0]
        if name == "Read" and matcher in READ_TARGETS:
            return name, READ_TARGETS[matcher][0]
        for tool, pattern, target, _lookalike in EXTRA_TARGETS + CLAUDE_WRITE_TARGETS:
            if rule == "%s(%s)" % (tool, pattern):
                return tool, target
        self.fail("no contradiction target for %s" % rule)

    def test_every_deny_entry_survives_a_contradictory_allow(self):
        for role in ROLES:
            profile = load_profile(role)
            perms = perms_of(profile)
            for rule in perms["deny"]:
                with self.subTest(role=role, rule=rule):
                    tool, target = self.contradiction_target(rule)
                    # Sanity: the target really exercises this entry.
                    self.assertTrue(
                        decide(
                            {"permissions": {"allow": [], "deny": [rule]}}, tool, target
                        )
                        == "deny",
                        "%s does not match %s" % (rule, target),
                    )
                    # The contradictory allow is placed BEFORE the deny in
                    # the decision inputs, so a precedence implementation
                    # that let a first-listed allow win would return allow
                    # here - deny must still win (order-independent).
                    contradicted = {
                        "permissions": {
                            "allow": [rule] + list(perms.get("allow") or []),
                            "deny": list(perms["deny"]),
                        }
                    }
                    self.assertEqual(
                        decide(contradicted, tool, target),
                        "deny",
                        "%s loses to its own allow in %s" % (rule, role),
                    )

    def test_contradiction_control_would_allow_without_the_deny(self):
        # Negative control: without the deny entry the same contradictory
        # allow really does allow the target, so the test above can fail -
        # it is not vacuously green on targets the allow never matches.
        tool, target = self.contradiction_target("Bash(*git push * main)")
        self.assertEqual(target, "git push origin main")
        self.assertEqual(
            decide(
                {"permissions": {"allow": ["Bash(git push:*)"], "deny": []}},
                tool,
                target,
            ),
            "allow",
        )


# ---------------------------------------------------------------------------
# render integrity: the committed templates equal the generator's render


class RenderTests(unittest.TestCase):
    def test_committed_templates_equal_the_render(self):
        harness = load_harness()
        for role in ROLES:
            with self.subTest(role=role):
                self.assertEqual(load_profile(role), lp.render_role(role, harness))

    def test_grant_sets_are_shared_not_copied(self):
        # One template per role name, rendered from shared grant sets so they
        # cannot drift: only the L1 roles carry the lane-push / workflow
        # pre-grants, l1-routing extends them with exactly the apply.sh
        # grant, and every other role carries no pre-grant.
        profiles = {role: perms_of(load_profile(role))["allow"] for role in ROLES}
        base = profiles["l1-coordinator"]
        self.assertEqual(base, list(lp.COORDINATOR_ALLOW))
        self.assertIn("Bash(gh workflow run:*)", base)
        routing = profiles["l1-routing"]
        self.assertEqual(routing[:-1], base)
        self.assertEqual(routing[-1], "Bash(bash configuration/omniroute/apply.sh:*)")
        self.assertNotIn("apply.sh", " ".join(base))
        for role in UNGRANTED_ROLES + LEAF_ROLES:
            with self.subTest(role=role):
                self.assertEqual(profiles[role], [])

    def test_leaf_denies_are_a_superset_of_coordinator_denies(self):
        # Leaves inherit the leaf fences unchanged (spec 2): every
        # bash_deny_leaf pattern denies on leaf profiles and none of them
        # appears on an L1 profile (which must keep lane push).
        fences = load_harness()["fences"]
        leaf_deny = perms_of(load_profile("l3-worker"))["deny"]
        coord_deny = perms_of(load_profile("l1-coordinator"))["deny"]
        for pattern in fences["bash_deny_leaf"]:
            rule = "Bash(%s)" % pattern
            with self.subTest(rule=rule):
                self.assertIn(rule, leaf_deny)
                self.assertNotIn(rule, coord_deny)

    def test_explicit_shell_secret_list_is_fresh_against_the_harness(self):
        # BASH_SECRET_EXTRA names shell-only disclosure vectors the read
        # fences cannot derive; each must still exist verbatim in the
        # harness, or the selector silently projects nothing.
        fences = load_harness()["fences"]
        for pattern in lp.BASH_SECRET_EXTRA:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, fences["bash_deny_all"])

    def test_profile_shape_and_safety_invariants(self):
        for role in ROLES:
            profile = load_profile(role)
            with self.subTest(role=role):
                self.assertEqual(profile["role"], role)
                self.assertEqual(profile["harnessRole"], lp.ROLES[role]["harness"])
                self.assertTrue(profile["mcpConfig"].startswith("configuration/mcp/"))
                self.assertNotIn("permissions", profile.get("allow", []))
                text = json.dumps(profile)
                self.assertNotIn("dangerously-skip-permissions", text)
                self.assertNotIn("bypassPermissions", text)
                for rule in perms_of(profile)["allow"] + perms_of(profile)["deny"]:
                    split_rule(rule)  # every entry is Tool(matcher) shaped


if __name__ == "__main__":
    unittest.main(verbosity=2)
