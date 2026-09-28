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
# Spec 3.2 as fixed in review round 1 and narrowed in round 3 (D-138): only
# the L1 coordinator roles carry the lane-push / workflow-dispatch pre-grants
# in full, while l2-orchestrator carries the narrower own-prefix set below.
# l0-router runs ungranted (unlisted is fail-closed under the launch flags).
L1_ROLES = ("l1-coordinator", "l1-routing")
L2_ROLES = ("l2-orchestrator",)
UNGRANTED_ROLES = ("l0-router",)
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

# L2 own-prefix pushes (round 3, D-138): the only pushes the l2-orchestrator
# grant pre-approves - the L2 lane-branch prefix, origin remote, with and
# without -u. No stated L2 branch-naming convention exists in the skill or
# docs text; the live branches are L2-<name>/... (e.g. L2-general/*), so the
# grant keys on the `L2-` prefix. L2-main pins the same no-over-deny
# boundary as main2 above: a lane merely containing "main" is not main.
BRANCH_L2_ALLOW_COMMANDS = (
    "git push origin L2-example-1",
    "git push origin L2-general/wt-example-1",
    "git push origin L2-main",
    "git push -u origin L2-example-1",
    "git push -u origin L2-general/wt-example-1",
)

# Pushes the L2 grant must leave unlisted (fail closed, never allowed): any
# branch outside the own prefix - generic lanes, L1 lanes, a lowercase
# look-alike (the glob is case-sensitive) - and any non-origin remote.
BRANCH_L2_UNLISTED_COMMANDS = (
    "git push origin wt-example-1",
    "git push origin L1-backlog/x",
    "git push origin l2-example-1",
    "git push upstream L2-example-1",
)

# L2 refspec/flag tricks that MUST deny (round 3, D-138): each matches an L2
# allow glob, so the decision proves deny beats the new allows - the same
# always-deny fence set, unchanged.
BRANCH_L2_FENCE_DENY_COMMANDS = (
    "git push origin L2-example-1:main",
    "git push origin L2-example-1:refs/heads/main",
    "git push -u origin L2-example-1:main",
    "git push origin L2-example-1 --force",
    "git push origin L2-example-1 --force-with-lease",
    "git push origin L2-example-1 --tags",
)

# L2 refspec tricks onto NON-main destinations (round 4): the round-3 allow
# `Bash(git push origin L2-*)` is a whole-line glob, so `*` spans the `:`
# and the destination - an L2 session could overwrite ANY non-main branch
# (`L2-x:some-shared-branch`, `L2-x:refs/heads/...`). Every colon refspec
# and every refs/ path must deny on l2, leaving only the same-name push
# (`git push [-u] origin L2-<name>`, destination = source). The first three
# match an L2 allow glob (deny-beats-allow, like the round-3 table); the
# refs/-spelled sources match no allow entry and deny outright.
BRANCH_L2_REFSPEC_DENY_COMMANDS = (
    "git push origin L2-x:refs/heads/L1-foo",
    "git push origin L2-x:some-shared-branch",
    "git push origin L2-x:refs/heads/l1-coordinator-lane",
    "git push -u origin L2-x:other",
    "git push origin L2-general/wt-x:other-branch",
    "git push origin refs/heads/L2-x",
    "git push origin refs/heads/L2-x:refs/heads/L1-foo",
)

# L2 branch-delete forms (round 4): deleting branches is not in the grant.
# The trailing-flag forms match the L2 allow glob (deny-beats-allow); the
# empty-source `:L2-x` form and the leading-flag forms match no allow entry
# and deny outright (fail-closed unlisted is not enough - deletion must be
# an explicit deny).
BRANCH_L2_DELETE_DENY_COMMANDS = (
    "git push origin L2-x --delete",
    "git push origin L2-x -d",
    "git push origin --delete L2-x",
    "git push origin -d L2-x",
    "git push origin :L2-x",
)

# The only pushes l2-orchestrator may make (round 4 restatement): the plain
# and -u same-name pushes. Every other push shape above denies.
BRANCH_L2_PLAIN_ALLOW_COMMANDS = (
    "git push origin L2-x",
    "git push -u origin L2-x",
)

# L2 single-ref shape (round 5): the L2 allow entries are whole-line globs
# whose trailing `*` spans the space after the ref, so `git push origin
# L2-x <anything>` - a second refspec (`L2-x L1-foo`, `L2-x L2-y`) or a
# trailing option (`L2-x --prune`, `-o x`) - matches the allow glob and
# pushes more than the one named ref. Enumerating dangerous tokens always
# misses one (round 4 caught `:`, `refs/`, `--delete`, `-d`; `L2-x L1-foo`
# and `L2-x --prune` still allowed). A space after the L2 token means a
# second argument of any kind and must deny. Every command below matches an
# L2 allow entry (checked in the test - deny beats allow) and must deny on
# l2; the pinned table after it fixes the other roles' decisions.
BRANCH_L2_TRAILING_DENY_COMMANDS = (
    "git push origin L2-x L1-foo",
    "git push origin L2-x L2-y",
    "git push origin L2-x --prune",
    "git push origin L2-x --force",
    "git push origin L2-x --force-with-lease",
    "git push origin L2-x -f",
    "git push origin L2-x --no-verify",
    "git push origin L2-x --tags",
    "git push origin L2-x -o x",
    "git push -u origin L2-x L1-foo",
    "git push -u origin L2-x --prune",
)

# L2 leading options (round 5): any option placed before the ref. Long
# options all deny via one shape; the known short force/delete flags deny
# explicitly (`-f`, `-d` - `--prune` and friends are long-only). None of
# these matches an L2 allow entry; `--force`/`-f` already deny via the
# shared fence, `--prune`/`-d` must deny via the new L2-only shape fences
# (explicit deny, not merely unlisted).
BRANCH_L2_LEADING_OPTION_DENY_COMMANDS = (
    "git push --force origin L2-x",
    "git push -f origin L2-x",
    "git push --prune origin L2-x",
)

# Round-5 commands with the other roles' decisions pinned (unchanged by this
# round - the new shape denies render ONLY into l2-orchestrator):
# (command, l1-decision, l0-decision). Leaf is deny for every row (the leaf
# push fence catches every `git push`); l2 is deny for every row (asserted
# in the l2 tests above, not here).
BRANCH_L2_ROUND5_OTHER_ROLES = (
    ("git push origin L2-x L1-foo", "allow", "none"),
    ("git push origin L2-x L2-y", "allow", "none"),
    ("git push origin L2-x --prune", "allow", "none"),
    ("git push origin L2-x --force", "deny", "deny"),
    ("git push origin L2-x --force-with-lease", "deny", "deny"),
    ("git push origin L2-x -f", "deny", "deny"),
    ("git push origin L2-x --no-verify", "allow", "none"),
    ("git push origin L2-x --tags", "deny", "deny"),
    ("git push origin L2-x -o x", "allow", "none"),
    ("git push -u origin L2-x L1-foo", "allow", "none"),
    ("git push -u origin L2-x --prune", "allow", "none"),
    ("git push --force origin L2-x", "deny", "deny"),
    ("git push -f origin L2-x", "deny", "deny"),
    ("git push --prune origin L2-x", "allow", "none"),
)

# L2 workflow dispatch (round 3, D-138): only --ref on the own prefix is
# pre-granted, in both --ref spellings. A bare run and any non-own ref stay
# unlisted; --ref main denies via the same fence as L1.
L2_WORKFLOW_ALLOW_COMMANDS = (
    "gh workflow run ci.yml --ref L2-example-1",
    "gh workflow run ci.yml --ref=L2-example-1",
    "gh workflow run ci.yml --ref L2-general/wt-example-1",
)
L2_WORKFLOW_UNLISTED_COMMANDS = (
    "gh workflow run ci.yml",
    "gh workflow run ci.yml --ref wt-example-1",
    "gh workflow run ci.yml --ref L1-backlog/x",
    "gh workflow run ci.yml --ref l2-example-1",
)

# Round 6, whitespace class: bash word-splits on TAB, CR and LF, but every
# fence matches a literal ASCII space as the argument separator - so each
# command below allowed before this round (measured with decide(): l2
# allowed `git push origin L2-x<TAB>main`, l1 allowed
# `git push origin<TAB>main`). A push never legitimately needs a
# non-space separator, so each must deny on EVERY profile now. The
# escapes below are real separator characters in the command text, the
# same characters the `Bash(*git push*<TAB/CR/LF>*)` denies match.
WHITESPACE_DENY_COMMANDS = (
    "git push origin L2-x\tmain",
    "git push -u origin L2-x\tmain",
    "git push origin L2-x\tL1-foo",
    "git push origin L2-x\nL1-foo",
    "git push origin L2-x\rL1-foo",
    "git push origin\tmain",
    "git push\torigin main",
)

# Round 6, quoting/expansion class: the fences match command text
# literally and no fence entry carries a literal ` main`, so each push
# below allowed under `Bash(git push:*)` on l1 before this round
# (measured with decide()). The ref must be literal text so the fences
# can see it - each must deny on EVERY profile now.
QUOTED_PUSH_DENY_COMMANDS = (
    'git push origin "main"',
    "git push origin 'main'",
    "git push origin $REF",
    "git push origin $(echo main)",
    "git push origin `main`",
)

# Round 6, quoting/expansion class for dispatch: same literal-text rule
# for `--ref` - a quoted, variable or substituted ref denies on EVERY
# profile now (`--ref "main"` already denied via the `*main*` fence; the
# rest allowed on l1 before this round, measured with decide()).
QUOTED_WORKFLOW_DENY_COMMANDS = (
    "gh workflow run ci.yml --ref $R",
    'gh workflow run ci.yml --ref "main"',
    "gh workflow run ci.yml --ref='main'",
    "gh workflow run ci.yml --ref `main`",
)

# Round-6 restatement: the plain same-name pushes still allow on l2, and
# the L1 lane-push / lane-dispatch shapes still allow on l1.
ROUND6_L2_ALLOW_COMMANDS = (
    "git push origin L2-x",
    "git push -u origin L2-x",
)
ROUND6_L1_ALLOW_COMMANDS = (
    "git push origin L1-routing/x",
    "git push -u origin L1-routing/x",
    "gh workflow run ci.yml --ref L1-routing/x",
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
        # Spec 3.2 as fixed in review round 1: the full lane-push grant is L1
        # only. Spec Q9 leaves workers/reviewers at read-only, and spec 2
        # makes a leaf grant smaller, never larger, than its harness leaf
        # fences - so a lane push is allowed on L1, denied outright on
        # leaves (by the rendered bash_deny_leaf push fence, not merely
        # unlisted), and unlisted (fail closed) on the ungranted l0-router.
        # Round 3 (D-138) narrows l2-orchestrator to its own prefix (below),
        # so the generic lanes here stay unlisted on L2 as well.
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
        for role in UNGRANTED_ROLES + L2_ROLES:
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
        for role in UNGRANTED_ROLES + L2_ROLES + LEAF_ROLES:
            profile = load_profile(role)
            command = "gh workflow run ci.yml --ref wt-example-1"
            with self.subTest(role=role, command=command):
                self.assertEqual(decide(profile, "Bash", command), "none")


# ---------------------------------------------------------------------------
# l2-orchestrator scope (round 3, D-138): own-prefix push and own-ref
# workflow dispatch, under the same always-deny fences


class L2ScopeTests(unittest.TestCase):
    def test_l2_own_prefix_push_is_allowed(self):
        profile = load_profile("l2-orchestrator")
        for command in BRANCH_L2_ALLOW_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(decide_deny(profile, "Bash", command))
                self.assertEqual(decide(profile, "Bash", command), "allow")

    def test_l2_other_branch_push_is_unlisted_not_allowed(self):
        # Narrower than the L1 grant: anything outside L2-* (or off origin)
        # is fail-closed unlisted, never allowed - and never denied either,
        # so the fence set stays the only denier.
        profile = load_profile("l2-orchestrator")
        for command in BRANCH_L2_UNLISTED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(decide(profile, "Bash", command), "none")

    def test_l2_refspec_onto_main_is_denied(self):
        # Each command matches an L2 allow glob (checked below), so deny
        # proves deny beats the new allows: an `L2-x:main` refspec pushes a
        # local L2 branch onto main and must deny.
        profile = load_profile("l2-orchestrator")
        allow = perms_of(profile)["allow"]
        for command in BRANCH_L2_FENCE_DENY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    any(
                        split_rule(rule)[0] == "Bash"
                        and bash_matches(split_rule(rule)[1], command)
                        for rule in allow
                    ),
                    "%s matches no L2 allow entry" % command,
                )
                self.assertTrue(
                    decide_deny(profile, "Bash", command),
                    "%s is not denied by l2-orchestrator" % command,
                )
        # The -f flag between push and origin matches no allow glob at all,
        # but the fence still denies it.
        self.assertTrue(
            decide_deny(profile, "Bash", "git push -f origin L2-example-1")
        )

    def test_l2_main_spellings_deny_like_every_profile(self):
        # The shared fence set renders into every profile unchanged: the
        # full BRANCH_DENY_COMMANDS table denies on L2 too.
        profile = load_profile("l2-orchestrator")
        for command in BRANCH_DENY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    decide_deny(profile, "Bash", command),
                    "%s is not denied by l2-orchestrator" % command,
                )

    def test_l2_wrapper_push_with_l2_ref_is_denied(self):
        # Wrapper/option argv shapes are never pre-granted (H3), even for an
        # L2 ref that the plain-spelling grant would allow.
        profile = load_profile("l2-orchestrator")
        denied = (
            "git -C /example/repo push origin L2-example-1",
            "git --git-dir=/example/repo/.git push origin L2-example-1",
            "git -c foo.bar=baz push origin L2-example-1",
        )
        for command in denied:
            with self.subTest(command=command):
                self.assertTrue(
                    decide_deny(profile, "Bash", command),
                    "%s is not denied by l2-orchestrator" % command,
                )

    def test_l2_compound_push_follows_each_part(self):
        profile = load_profile("l2-orchestrator")
        self.assertEqual(
            decide(profile, "Bash", "git push origin L2-a && git push origin L2-b"),
            "allow",
        )
        self.assertEqual(
            decide(profile, "Bash", "git fetch && git push origin L2-a"), "none"
        )
        self.assertEqual(
            decide(profile, "Bash", "git push origin L2-a && git push origin main"),
            "deny",
        )

    def test_l2_workflow_run_requires_the_own_ref(self):
        profile = load_profile("l2-orchestrator")
        for command in L2_WORKFLOW_ALLOW_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(decide(profile, "Bash", command), "allow")
        # Never a bare run, never a non-own ref: unlisted (fail closed).
        for command in L2_WORKFLOW_UNLISTED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(decide(profile, "Bash", command), "none")

    def test_l2_workflow_run_cannot_name_main(self):
        profile = load_profile("l2-orchestrator")
        for command in (
            "gh workflow run ci.yml --ref main",
            "gh workflow run ci.yml --ref=main",
        ):
            with self.subTest(command=command):
                self.assertTrue(decide_deny(profile, "Bash", command))

    def test_l2_refspec_to_non_main_branch_is_denied(self):
        # Round 4 (F1): the allow glob spans the `:` and the destination,
        # so the colon forms below overwrote any non-main branch before the
        # fix. Each colon form with an L2- source matches an L2 allow entry
        # (checked - deny beats allow); the refs/-spelled sources match no
        # allow entry and still deny.
        profile = load_profile("l2-orchestrator")
        allow = perms_of(profile)["allow"]
        allow_matching = tuple(
            command
            for command in BRANCH_L2_REFSPEC_DENY_COMMANDS
            if command.split(":")[0].rstrip().split()[-1].startswith("L2-")
        )
        self.assertTrue(allow_matching)  # the check below is not vacuous
        for command in allow_matching:
            with self.subTest(command=command):
                self.assertTrue(
                    any(
                        split_rule(rule)[0] == "Bash"
                        and bash_matches(split_rule(rule)[1], command)
                        for rule in allow
                    ),
                    "%s matches no L2 allow entry" % command,
                )
        for command in BRANCH_L2_REFSPEC_DENY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    decide_deny(profile, "Bash", command),
                    "%s is not denied by l2-orchestrator" % command,
                )

    def test_l2_delete_forms_are_denied(self):
        # Round 4 (F2): deleting branches is not in the grant - explicit
        # deny, not merely unlisted.
        profile = load_profile("l2-orchestrator")
        for command in BRANCH_L2_DELETE_DENY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    decide_deny(profile, "Bash", command),
                    "%s is not denied by l2-orchestrator" % command,
                )

    def test_l2_plain_same_name_push_is_allowed(self):
        # Round 4 restatement: only `git push [-u] origin L2-<name>`
        # (destination = source) allows on l2.
        profile = load_profile("l2-orchestrator")
        for command in BRANCH_L2_PLAIN_ALLOW_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(decide_deny(profile, "Bash", command))
                self.assertEqual(decide(profile, "Bash", command), "allow")

    def test_l2_trailing_args_are_denied(self):
        # Round 5 (shape, not tokens): a space after the L2 ref means a
        # second argument of any kind - a second refspec or a trailing
        # option - and denies. Every command here matches an L2 allow glob
        # (checked - deny beats allow), so these prove the shape fence:
        # token enumeration (`:`, `refs/`, `--delete`, `-d` in round 4)
        # missed `L2-x L1-foo` and `L2-x --prune`, both allow before this
        # round.
        profile = load_profile("l2-orchestrator")
        allow = perms_of(profile)["allow"]
        for command in BRANCH_L2_TRAILING_DENY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    any(
                        split_rule(rule)[0] == "Bash"
                        and bash_matches(split_rule(rule)[1], command)
                        for rule in allow
                    ),
                    "%s matches no L2 allow entry" % command,
                )
                self.assertTrue(
                    decide_deny(profile, "Bash", command),
                    "%s is not denied by l2-orchestrator" % command,
                )

    def test_l2_leading_options_are_denied(self):
        # Round 5: any option placed before the ref denies on l2. None of
        # these matches an L2 allow entry; `--force`/`-f` already deny via
        # the shared fence, `--prune` denies via the new L2-only long-option
        # shape (explicit deny, not merely unlisted).
        profile = load_profile("l2-orchestrator")
        for command in BRANCH_L2_LEADING_OPTION_DENY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    decide_deny(profile, "Bash", command),
                    "%s is not denied by l2-orchestrator" % command,
                )

    def test_l2_single_ref_shape_still_allows_plain_and_u(self):
        # Round 5 restatement: the grant is exactly one `L2-*` ref,
        # same-name, no options except `-u` - the two plain shapes still
        # allow with nothing after the ref.
        profile = load_profile("l2-orchestrator")
        for command in BRANCH_L2_PLAIN_ALLOW_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(decide_deny(profile, "Bash", command))
                self.assertEqual(decide(profile, "Bash", command), "allow")

    def test_l1_l0_leaf_results_unchanged_for_l2_shape_commands(self):
        # Round 5: the new shape denies render ONLY into the
        # l2-orchestrator profile, so every other role decides these
        # commands exactly as before this round - pinned per command,
        # because unlike round 4 the L1/l0 decisions are mixed here (L1
        # allows the unlisted-option shapes via its full lane-push grant
        # and denies the fenced ones; l0 mirrors that as none/deny).
        # Leaves deny every row via the leaf push fence.
        pinned = dict(
            (command, (l1, l0)) for command, l1, l0 in BRANCH_L2_ROUND5_OTHER_ROLES
        )
        commands = (
            BRANCH_L2_TRAILING_DENY_COMMANDS
            + BRANCH_L2_LEADING_OPTION_DENY_COMMANDS
        )
        self.assertEqual(set(pinned), set(commands))
        for command in commands:
            l1_expected, l0_expected = pinned[command]
            for role in L1_ROLES:
                profile = load_profile(role)
                with self.subTest(role=role, command=command):
                    self.assertEqual(
                        decide(profile, "Bash", command),
                        l1_expected,
                        "%s is not %s on %s" % (command, l1_expected, role),
                    )
            for role in UNGRANTED_ROLES:
                profile = load_profile(role)
                with self.subTest(role=role, command=command):
                    self.assertEqual(
                        decide(profile, "Bash", command),
                        l0_expected,
                        "%s is not %s on %s" % (command, l0_expected, role),
                    )
            for role in LEAF_ROLES:
                profile = load_profile(role)
                with self.subTest(role=role, command=command):
                    self.assertEqual(
                        decide(profile, "Bash", command),
                        "deny",
                        "%s is not denied by %s" % (command, role),
                    )

    def test_l1_l0_leaf_results_unchanged_for_l2_fence_commands(self):
        # Round 4: the new colon/refs/delete denies render ONLY into the
        # l2-orchestrator profile, so every other role decides these
        # commands exactly as before this round: L1 allows (the full
        # lane-push grant, no new deny), l0-router stays unlisted
        # (fail closed), leaves stay denied (the leaf push fence).
        commands = (
            BRANCH_L2_REFSPEC_DENY_COMMANDS
            + BRANCH_L2_DELETE_DENY_COMMANDS
        )
        for role in L1_ROLES:
            profile = load_profile(role)
            for command in commands:
                with self.subTest(role=role, command=command):
                    self.assertEqual(
                        decide(profile, "Bash", command),
                        "allow",
                        "%s is not allowed by %s" % (command, role),
                    )
        for role in UNGRANTED_ROLES:
            profile = load_profile(role)
            for command in commands:
                with self.subTest(role=role, command=command):
                    self.assertEqual(
                        decide(profile, "Bash", command),
                        "none",
                        "%s is not unlisted on %s" % (command, role),
                    )
        for role in LEAF_ROLES:
            profile = load_profile(role)
            for command in commands:
                with self.subTest(role=role, command=command):
                    self.assertEqual(
                        decide(profile, "Bash", command),
                        "deny",
                        "%s is not denied by %s" % (command, role),
                    )


# ---------------------------------------------------------------------------
# round 6: the fences see literal refs only - whitespace, quoting and
# expansion classes deny on every role


class Round6LiteralRefTests(unittest.TestCase):
    def test_whitespace_separator_push_is_denied_by_every_profile(self):
        for role in ROLES:
            profile = load_profile(role)
            for command in WHITESPACE_DENY_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )

    def test_quoted_push_is_denied_by_every_profile(self):
        for role in ROLES:
            profile = load_profile(role)
            for command in QUOTED_PUSH_DENY_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )

    def test_quoted_workflow_dispatch_is_denied_by_every_profile(self):
        for role in ROLES:
            profile = load_profile(role)
            for command in QUOTED_WORKFLOW_DENY_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertTrue(
                        decide_deny(profile, "Bash", command),
                        "%s is not denied by %s" % (command, role),
                    )

    def test_plain_l2_push_still_allows(self):
        profile = load_profile("l2-orchestrator")
        for command in ROUND6_L2_ALLOW_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(decide_deny(profile, "Bash", command))
                self.assertEqual(decide(profile, "Bash", command), "allow")

    def test_l1_lane_push_and_dispatch_still_allow(self):
        for role in L1_ROLES:
            profile = load_profile(role)
            for command in ROUND6_L1_ALLOW_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertFalse(decide_deny(profile, "Bash", command))
                    self.assertEqual(decide(profile, "Bash", command), "allow")

    def test_no_allow_rule_contains_tab_cr_or_lf(self):
        # The fences deny the whitespace class; no pre-grant may smuggle
        # one back in through an allow entry carrying a separator.
        for role in ROLES:
            allow = perms_of(load_profile(role))["allow"]
            for rule in allow:
                with self.subTest(role=role, rule=rule):
                    self.assertNotIn("\t", rule)
                    self.assertNotIn("\r", rule)
                    self.assertNotIn("\n", rule)


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
    # L2-only same-name-push fences (round 4, F1/F2): each target matches
    # its deny entry, so the contradiction test proves the entry fires.
    "Bash(*git push * *:*)": "git push origin L2-x:other",
    "Bash(*git push *refs/*)": "git push origin refs/heads/L2-x",
    "Bash(*git push *--delete*)": "git push origin L2-x --delete",
    "Bash(*git push * -d *)": "git push origin -d L2-x",
    "Bash(*git push * -d)": "git push origin L2-x -d",
    # L2-only single-ref shape fences (round 5): each target matches its
    # deny entry. The trailing-space targets match the L2 allow glob too,
    # so the contradiction test proves the shape fence beats the allow;
    # the `--prune`/`-d` leading-option targets match no allow entry and
    # prove the explicit deny (fail-closed unlisted is not enough where a
    # whole option class was unlisted before).
    "Bash(git push origin L2-* *)": "git push origin L2-x L1-foo",
    "Bash(git push -u origin L2-* *)": "git push -u origin L2-x L1-foo",
    "Bash(git push --* origin L2-*)": "git push --prune origin L2-x",
    "Bash(git push -f origin L2-*)": "git push -f origin L2-x",
    "Bash(git push -d origin L2-*)": "git push -d origin L2-x",
    # Round-6 whitespace class (MAIN_FENCE, every role): each target
    # carries the denied separator after a `git push` prefix, so the
    # contradiction test proves the class fence fires.
    "Bash(*git push*\t*)": "git push origin L2-x\tmain",
    "Bash(*git push*\r*)": "git push origin L2-x\rL1-foo",
    "Bash(*git push*\n*)": "git push origin L2-x\nL1-foo",
    # Round-6 quoting/expansion class (MAIN_FENCE, every role): each
    # target carries the denied character after a `git push` prefix.
    'Bash(*git push*"*)': 'git push origin "main"',
    "Bash(*git push*'*)": "git push origin 'main'",
    "Bash(*git push*$*)": "git push origin $REF",
    "Bash(*git push*`*)": "git push origin `main`",
    # Round-6 quoting/expansion class (GH_REF_MAIN, every role).
    'Bash(*gh workflow run*"*)': 'gh workflow run ci.yml --ref "main"',
    "Bash(*gh workflow run*'*)": "gh workflow run ci.yml --ref='main'",
    "Bash(*gh workflow run*$*)": "gh workflow run ci.yml --ref $R",
    "Bash(*gh workflow run*`*)": "gh workflow run ci.yml --ref `main`",
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
        # cannot drift: only the L1 roles carry the full lane-push /
        # workflow pre-grants, l1-routing extends them with exactly the
        # apply.sh grant, l2-orchestrator carries exactly the narrower
        # own-prefix set (round 3, D-138 - never the bare L1 grants), and
        # every other role carries no pre-grant.
        profiles = {role: perms_of(load_profile(role))["allow"] for role in ROLES}
        base = profiles["l1-coordinator"]
        self.assertEqual(base, list(lp.COORDINATOR_ALLOW))
        self.assertIn("Bash(gh workflow run:*)", base)
        routing = profiles["l1-routing"]
        self.assertEqual(routing[:-1], base)
        self.assertEqual(routing[-1], "Bash(bash configuration/omniroute/apply.sh:*)")
        self.assertNotIn("apply.sh", " ".join(base))
        l2 = profiles["l2-orchestrator"]
        self.assertEqual(l2, list(lp.L2_ALLOW))
        self.assertNotIn("Bash(git push:*)", l2)
        self.assertNotIn("Bash(gh workflow run:*)", l2)
        self.assertNotIn("apply.sh", " ".join(l2))
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
