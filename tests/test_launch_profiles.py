#!/usr/bin/env python3
"""ORCH-A1 phase 1: role launch-profile scope tables and the always-deny
contradiction test.

The six tracked templates ``configuration/launch-profiles/<role>.settings.
example.json`` are rendered by ``tools/launch_profiles.py`` from
``catalog/agent-harness.json`` fences plus the profile-specific always-deny
entries of spec section 3.3. These tests drive real command/path strings
through the rendered matchers and assert the decision:

- branch scope (spec 3.2): every spelling of "push to main" denies, a lane
  branch push does not;
- secret scope (spec 3.3): every secret pattern deny-matches its target, and
  a harmless look-alike does not;
- contradiction (A1-D5): a contradictory ``allow`` for an always-deny entry
  still decides ``deny``.

Matcher model (mirrors Claude Code's rule syntax; stated once, here):

- ``Bash(matcher)`` is evaluated against the whole command line. A matcher
  of the form ``<literal prefix>:*`` (no other wildcard) is the prefix
  form (``Bash(git push:*)`` allows any ``git push ...`` command, the form
  ``claude --help`` itself shows for ``--allowedTools``/``--disallowedTools``,
  e.g. ``Bash(git *)``); any other matcher is a shell-style glob over the
  full command (``fnmatchcase``, where ``*`` spans ``/`` and spaces, so
  ``*api-keys*`` matches at any depth, and main-fence entries ending in
  ``:*`` work as globs). A bare matcher with no wildcard matches one exact
  command only; Claude Code itself prefix-matches such rules, which can only
  deny *more*, never less, so the model errs allow-closed nowhere.
- ``Read/Edit/Write(glob)`` are path globs (``fnmatchcase``, ``*`` spans
  ``/``).
- ``deny`` is evaluated before ``allow`` (the precedence rule the
  contradiction test pins).

Denial here is on the *name* only: fixture paths are never opened, no real
key file is read, and no command is executed (AGENTS.md section 5; the same
rule the ``t3-reviewer`` fence case in ``tests/linux/33-documentation.sh``
follows). Placeholder roots (``/example/``, ``/home/x/``) keep the fixtures
site-free (AGENTS.md rule 1).

Run directly (``python3 tests/test_launch_profiles.py``), never via
``unittest discover`` - the suite has to be runnable on a machine where
nothing is installed (AGENTS.md section 5). Path-independent: everything is
anchored on ROOT, never on the current working directory.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
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
COORDINATOR_ROLES = tuple(r for r in ROLES if r not in LEAF_ROLES)


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
        # Prefix form only: a rule carrying any other wildcard is a glob,
        # which is what lets main-fence entries ending in ":*" (e.g.
        # `git push * main:*`) work as globs rather than literal prefixes.
        prefix = matcher[:-2]
        return (
            command == prefix
            or command.startswith(prefix + " ")
            or command.startswith(prefix + ":")
        )
    return fnmatch.fnmatchcase(command, matcher)


def path_matches(matcher, path):
    """Whether a Read/Edit/Write rule glob matches a path (model above)."""
    return fnmatch.fnmatchcase(path, matcher)


def decide(profile, tool, target):
    """The profile's decision for (tool, target): deny wins over allow.

    Returns "deny", "allow", or "none" (unlisted - denied automatically under
    ``permissionPrompts: none``, the fail-closed default of spec Q8).
    """
    allow = profile.get("allow") or []
    deny = profile.get("deny") or []
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


def decide_deny(profile, tool, target):
    return decide(profile, tool, target) == "deny"


class MatcherModelTests(unittest.TestCase):
    """Pin the documented model itself, so the tables below mean something."""

    def test_prefix_form_matches_command_with_arguments(self):
        self.assertTrue(bash_matches("git push:*", "git push origin wt-example-1"))
        self.assertTrue(bash_matches("git push:*", "git push"))
        self.assertFalse(bash_matches("git push:*", "git pushorigin"))

    def test_glob_form_matches_the_whole_command_line(self):
        # KEYDENY lesson: a fence is a decision on a real command, so the
        # glob sees the whole line, not one path argument.
        self.assertTrue(
            bash_matches("*api-keys*", "cat configuration/api-keys.yml configuration/notes.md")
        )
        self.assertFalse(bash_matches("set", "setup.sh --check"))

    def test_path_glob_star_spans_separators(self):
        self.assertTrue(path_matches("*api-keys*", "configuration/api-keys.yml"))
        self.assertTrue(path_matches("*.env.*", "stack.env.example"))
        self.assertFalse(path_matches("*.env", "stack.env.example"))

    def test_deny_wins_over_allow(self):
        profile = {"allow": ["Bash(git push:*)"], "deny": ["Bash(git push * main)"]}
        self.assertEqual(decide(profile, "Bash", "git push origin main"), "deny")
        self.assertEqual(decide(profile, "Bash", "git push origin wt-example-1"), "allow")
        self.assertEqual(decide(profile, "Bash", "git status"), "none")


# ---------------------------------------------------------------------------
# branch scope (spec 3.2)


# Every way to spell "push to main" the KEYDENY lesson requires: each must be
# denied by every rendered profile's matchers.
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
)

# Lane pushes: spelled without "main" as a ref, so the fence must let them
# through. main2 pins the no-over-deny boundary (a branch merely containing
# "main" is not main).
BRANCH_LANE_COMMANDS = (
    "git push origin wt-example-1",
    "git push origin agent/20260928-120000-example-task-a1b2c3",
    "git push origin main2",
    "git push upstream wt-example-1",
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
        # The main fence must not catch lane pushes: on profiles that may
        # push (coordinators) a lane push goes through. Leaf profiles deny
        # every push via the rendered bash_deny_leaf fence (asserted in
        # test_lane_branch_push_is_pre_granted_to_coordinators_only).
        for role in COORDINATOR_ROLES:
            profile = load_profile(role)
            for command in BRANCH_LANE_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertFalse(
                        decide_deny(profile, "Bash", command),
                        "%s is denied by %s" % (command, role),
                    )

    def test_lane_branch_push_is_pre_granted_to_coordinators_only(self):
        # Spec 3.2 grants lane-branch push to coordinators; spec Q9 leaves
        # workers/reviewers at read-only, and spec 2 makes a leaf grant
        # smaller, never larger, than its harness leaf fences - so a lane
        # push is allowed on coordinators and denied outright on leaves
        # (by the rendered bash_deny_leaf push fence, not merely unlisted).
        for role in COORDINATOR_ROLES:
            profile = load_profile(role)
            for command in BRANCH_LANE_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertEqual(decide(profile, "Bash", command), "allow")
        for role in LEAF_ROLES:
            profile = load_profile(role)
            for command in BRANCH_LANE_COMMANDS:
                with self.subTest(role=role, command=command):
                    self.assertEqual(decide(profile, "Bash", command), "deny")

    def test_explicit_main_dispatch_of_gh_workflow_run_is_denied(self):
        # Spec Q4's measured check: the coordinator `gh workflow run` grant
        # stays, but a dispatch naming main is denied.
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

    def test_lane_dispatch_of_gh_workflow_run_is_not_denied(self):
        for role in COORDINATOR_ROLES:
            profile = load_profile(role)
            command = "gh workflow run ci.yml --ref wt-example-1"
            with self.subTest(role=role, command=command):
                self.assertFalse(decide_deny(profile, "Bash", command))
                self.assertEqual(decide(profile, "Bash", command), "allow")


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
    # Bare-word rules match one exact command under this model; a longer
    # command starting with the same letters does not.
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


class SecretScopeTests(unittest.TestCase):
    def test_harness_read_patterns_are_rendered_and_match(self):
        fences = load_harness()["fences"]
        for role in ROLES:
            profile = load_profile(role)
            for pattern in fences["read_deny_all"]:
                rule = "Read(%s)" % pattern
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, profile["deny"])
                    target, lookalike = READ_TARGETS[pattern]
                    self.assertTrue(decide_deny(profile, "Read", target))
                    self.assertFalse(decide_deny(profile, "Read", lookalike))

    def test_harness_secret_bash_patterns_are_rendered_and_match(self):
        fences = load_harness()["fences"]
        selected = lp.bash_secret_patterns(fences["bash_deny_all"], fences["read_deny_all"])
        for role in ROLES:
            profile = load_profile(role)
            for pattern in selected:
                rule = "Bash(%s)" % pattern
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, profile["deny"])
                    target, lookalike = BASH_TARGETS[pattern]
                    self.assertTrue(decide_deny(profile, "Bash", target))
                    self.assertFalse(decide_deny(profile, "Bash", lookalike))

    def test_profile_specific_secret_extras_match(self):
        for role in ROLES:
            profile = load_profile(role)
            for tool, pattern, target, lookalike in EXTRA_TARGETS:
                rule = "%s(%s)" % (tool, pattern)
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, profile["deny"])
                    self.assertTrue(decide(profile, tool, target) == "deny")
                    self.assertFalse(decide_deny(profile, tool, lookalike))

    def test_claude_json_writes_are_denied(self):
        for role in ROLES:
            profile = load_profile(role)
            for tool, pattern, target, lookalike in CLAUDE_WRITE_TARGETS:
                rule = "%s(%s)" % (tool, pattern)
                with self.subTest(role=role, rule=rule):
                    self.assertIn(rule, profile["deny"])
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
# coordinator lane-push grant) cannot leak through the deny.
CONTRADICTION_TARGETS = {
    "Bash(git push * main)": "git push origin main",
    "Bash(git push * main *)": "git push origin main --force",
    "Bash(git push * main:*)": "git push origin main:refs/heads/other",
    "Bash(git push *:main*)": "git push origin :main",
    "Bash(git push *HEAD:*main*)": "git push origin HEAD:main",
    "Bash(git push *+*main*)": "git push origin +main",
    "Bash(git push *refs/heads/main*)": "git push origin refs/heads/main",
    "Bash(git push *--force*)": "git push --force origin wt-example-1",
    "Bash(git push *-f *)": "git push -f origin wt-example-1",
    "Bash(git push *-f)": "git push origin wt-example-1 -f",
    "Bash(git push *--tags*)": "git push --tags",
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
    """A1-D5: deny takes precedence over allow.

    Claude Code documents deny as taking precedence over allow. Re-checked
    for the build-host CLIs named by the lane brief (Windows 2.1.283, WSL
    2.1.267): this host's own ``claude --help`` at 2.1.283 documents
    ``--settings``, ``--allowedTools``/``--disallowedTools``,
    ``--permission-mode`` (choices include ``auto``) and
    ``--permission-prompts`` (choices ``host``/``none``), but prints no
    precedence rule (zero matches for "precedence"). This test cannot
    execute Claude Code's engine; it pins (a) every always-deny entry is
    present in the committed profile, so the documented precedence has
    something to apply to, and (b) under the documented model a
    contradictory allow for the same pattern still decides deny.
    """

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
            for rule in profile["deny"]:
                with self.subTest(role=role, rule=rule):
                    tool, target = self.contradiction_target(rule)
                    # Sanity: the target really exercises this entry.
                    self.assertTrue(
                        decide_deny({"allow": [], "deny": [rule]}, tool, target),
                        "%s does not match %s" % (rule, target),
                    )
                    contradicted = {
                        "allow": list(profile.get("allow") or []) + [rule],
                        "deny": list(profile["deny"]),
                    }
                    self.assertEqual(
                        decide(contradicted, tool, target),
                        "deny",
                        "%s loses to its own allow in %s" % (rule, role),
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
        # cannot drift: coordinators share one allow list, l1-routing extends
        # it with exactly the apply.sh grant, leaves carry no pre-grant.
        profiles = {role: load_profile(role) for role in ROLES}
        base = profiles["l1-coordinator"]["allow"]
        for role in ("l0-router", "l2-orchestrator"):
            with self.subTest(role=role):
                self.assertEqual(profiles[role]["allow"], base)
        routing = profiles["l1-routing"]["allow"]
        self.assertEqual(routing[:-1], base)
        self.assertEqual(routing[-1], "Bash(bash configuration/omniroute/apply.sh:*)")
        self.assertNotIn("apply.sh", " ".join(base))
        for role in LEAF_ROLES:
            with self.subTest(role=role):
                self.assertEqual(profiles[role]["allow"], [])

    def test_leaf_denies_are_a_superset_of_coordinator_denies(self):
        # Leaves inherit the leaf fences unchanged (spec 2): every
        # bash_deny_leaf pattern denies on leaf profiles and none of them
        # appears on a coordinator profile (which must keep lane push).
        fences = load_harness()["fences"]
        leaf_profile = load_profile("l3-worker")
        coord_profile = load_profile("l1-coordinator")
        for pattern in fences["bash_deny_leaf"]:
            rule = "Bash(%s)" % pattern
            with self.subTest(rule=rule):
                self.assertIn(rule, leaf_profile["deny"])
                self.assertNotIn(rule, coord_profile["deny"])

    def test_explicit_shell_secret_list_is_fresh_against_the_harness(self):
        # BASH_SECRET_EXTRA names shell-only disclosure vectors the read
        # fences cannot derive; each must still exist verbatim in the
        # harness, or the selector silently projects nothing.
        fences = load_harness()["fences"]
        for pattern in lp.BASH_SECRET_EXTRA:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, fences["bash_deny_all"])

    def test_profile_shape_and_safety_invariants(self):
        harness = load_harness()
        for role in ROLES:
            profile = load_profile(role)
            with self.subTest(role=role):
                self.assertEqual(profile["role"], role)
                self.assertEqual(profile["harnessRole"], lp.ROLES[role]["harness"])
                self.assertTrue(profile["mcpConfig"].startswith("configuration/mcp/"))
                self.assertEqual(profile["permissionMode"], "auto")
                self.assertEqual(profile["permissionPrompts"], "none")
                text = json.dumps(profile)
                self.assertNotIn("dangerously-skip-permissions", text)
                for rule in profile["allow"] + profile["deny"]:
                    split_rule(rule)  # every entry is Tool(matcher) shaped


if __name__ == "__main__":
    unittest.main(verbosity=2)
