#!/usr/bin/env python3
"""ORCH-A1 phase 1: the launch-profile shape, the secret scope and precedence.

The six tracked templates ``configuration/launch-profiles/<role>.settings.
example.json`` are rendered by ``tools/launch_profiles.py`` from
``catalog/agent-harness.json`` fences plus the profile-specific always-deny
entries of spec section 3.3. These tests drive real command and path strings
through the rendered matchers and assert the decision:

- settings shape (H1): every template carries its rules under
  ``permissions.allow`` / ``permissions.deny`` - the only shape Claude Code
  reads - with no top-level ``allow``/``deny``/``permissionMode``/
  ``permissionPrompts`` keys;
- secret scope (spec 3.3): every secret pattern deny-matches its target, and
  a harmless look-alike does not;
- contradiction (A1-D5): a contradictory ``allow`` for an always-deny entry
  still decides ``deny`` - for a push or dispatch fence, the contradictory
  target is the fence's corpus witness, so this also re-checks that every
  fence decides a command that exists;
- render integrity: the committed template equals the generator's output.

The branch scope (spec 3.2: every spelling of "push to main" denies) is NOT
here. Round 13 (routing-00 D-159) removed the pre-granted lane push and
workflow dispatch, so the push and dispatch spellings - the wrapper forms
(``git -C x push origin main``), the compound lines, the ``sudo``/``env``/
``xargs`` prefixes and every main spelling - live in one home,
``tests/fixtures/push-corpus.json``, decided by ``tests/test_push_corpus.py``
against these same rendered profiles.

The matcher model those two files share, the corpus loader and the real-git
premise tests are in ``tests/helpers/launch_profile_model.py`` (principle 1:
one home per fact, and a test file is not a library another test imports).
Its documented semantics, restated only as far as this file needs them:

- ``Bash(cmd:*)`` with no other wildcard is a prefix match; a matcher with no
  wildcard at all is an exact match; anything else is a whole-line
  ``fnmatchcase`` glob, where ``*`` spans spaces and ``/`` - which is what the
  ``Bash(*git*push* main)`` fence shape relies on.
- Compound commands split on ``&&`` ``||`` ``;`` ``|`` and each part is decided
  separately: any part denied denies the whole; the whole allows only when
  every part allows.
- ``deny`` is evaluated before ``allow`` and wins regardless of order (the
  contradiction test pins that the RENDERED profiles keep every deny reachable
  under that documented rule; it cannot verify the CLI itself).

Two assumptions about the CLI are pinned ONLY in that model and must be
re-verified when the CLI changes: deny-before-allow precedence, and ``*``
crossing ``/`` in path rules. If either stops holding, the tables below prove
nothing until the model is updated.

Known divergences of the model from the real CLI (each is a gap that now fails
toward the classifier rather than toward a silent allow, because nothing is
pre-granted any more):

- The CLI also unwraps ``sudo``/``env``/``xargs`` prefixes when matching allow
  rules; this model matches the literal line. The fence compensates with its
  leading ``*``, and the corpus pins ``sudo git push origin main`` to deny on
  every profile.
- The CLI may normalize quoting and whitespace and resolve aliases before
  matching; this model matches raw text. A push through an alias or a quoted
  ref is invisible to text fencing - the classifier sees it, and the hooks lane
  (HOOKS H2) is the fence that reads the real argv (spec 3.3).

Denial here is on the *name* only: fixture paths are never opened, no real key
file is read, and no command is executed (AGENTS.md section 5; the same rule
the ``t3-reviewer`` fence case in ``tests/linux/33-documentation.sh`` follows).
Placeholder roots (``/example/``, ``/home/x/``) keep the fixtures site-free
(AGENTS.md rule 1).

Run directly (``python3 tests/test_launch_profiles.py``) or via
``python3 -m unittest discover -s tests -p "test_launch_profiles.py"`` -
stdlib only, runnable on a machine where nothing is installed (AGENTS.md
section 5). Path-independent: everything is anchored on ``ROOT``, never on the
current working directory.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "helpers"))

from launch_profile_model import (  # noqa: E402
    ROLES,
    bash_matches,
    deny_witness,
    decide,
    decide_deny,
    load_harness,
    load_profile,
    load_tool,
    path_matches,
    perms_of,
    split_rule,
)

# The generator module, imported by path: the render-integrity tests compare a
# rendered profile against it, so they need its constants, not a copy of them.
lp = load_tool()


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
        self.assertTrue(bash_matches("*git*push* main", "sudo git push origin main"))

    def test_path_glob_star_spans_separators(self):
        self.assertTrue(path_matches("*api-keys*", "configuration/api-keys.yml"))
        self.assertTrue(path_matches("*.env.*", "stack.env.example"))
        self.assertFalse(path_matches("*.env", "stack.env.example"))

    def test_compound_split_denies_when_any_part_denies(self):
        profile = {
            "permissions": {
                "allow": ["Bash(git push:*)"],
                "deny": ["Bash(*git*push* main)"],
            }
        }
        self.assertEqual(decide(profile, "Bash", "git fetch && git push origin main"), "deny")
        self.assertEqual(decide(profile, "Bash", "git status; git push origin main"), "deny")
        self.assertEqual(decide(profile, "Bash", "echo x | git push origin main"), "deny")

    def test_compound_allows_only_when_every_part_allows(self):
        # The synthetic `Bash(git push:*)` grant is the prefix form, which no
        # profile pre-grants any more (round 13); it stays here because the form
        # itself is live - APPLY_ALLOW is rendered with it.
        profile = {
            "permissions": {
                "allow": ["Bash(git push:*)"],
                "deny": ["Bash(*git*push* main)"],
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
                "deny": ["Bash(*git*push* main)"],
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
        # The scope tests exercise the rendered file through decide(), so
        # decide() must read the same `permissions` object the CLI reads. A lane
        # push answers `none` here, not `allow`: round 13 removed the grant.
        profile = load_profile("l1-coordinator")
        self.assertEqual(decide(profile, "Bash", "git push origin main"), "deny")
        self.assertEqual(decide(profile, "Bash", "git push origin L1-routing/x"), "none")
        bare_old_shape = {"allow": [], "deny": ["Bash(*git*push* main)"]}
        self.assertEqual(decide(bare_old_shape, "Bash", "git push origin main"), "none")


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

# Profile-specific ~/.claude.json write patterns (Edit, Write only -
# round 9: the shell tee/redirect/sed-i Bash forms were dead duplicates
# of the harness-rendered `Bash(*.claude.json*)` - decide() rows prove
# the generic entry matches the same commands - so they are dropped
# from the render and pinned by test_claude_json_shell_forms_still_deny
# instead): (tool, pattern, target, look-alike).
CLAUDE_WRITE_TARGETS = (
    ("Edit", "*.claude.json*", "/home/x/.claude.json", "/home/x/claude.json"),
    ("Write", "*.claude.json*", "/home/x/.claude.json", "/home/x/claude.json"),
)

# Shell forms formerly covered by the dropped duplicates: each must
# still deny on every profile via the harness-rendered
# `Bash(*.claude.json*)` generic entry.
CLAUDE_SHELL_FORM_TARGETS = (
    "echo x >> /home/x/.claude.json",
    "printf x | tee /home/x/.claude.json",
    "sed -i s/a/b/ /home/x/.claude.json",
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
# NOT rendered. Round 13 made the reason stronger, not weaker: with nothing
# pre-granted, a rendered `gh *` would be the whole decision on every gh
# command - the lane dispatch, `gh pr view`, `gh run watch` - and a deny cannot
# explain itself the way an unlisted command's classifier prompt can. A future
# harness entry that is neither selector-secret nor listed here fails the
# completeness test instead of being silently dropped (M2).
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

    def test_claude_json_shell_forms_still_deny(self):
        # Round 9: the dropped tee/redirect/sed-i Bash duplicates are
        # carried by the harness-rendered `Bash(*.claude.json*)`
        # generic entry - each shell form still denies on EVERY
        # profile, and the generic rule is present to carry it.
        for role in ROLES:
            profile = load_profile(role)
            with self.subTest(role=role):
                self.assertIn(
                    "Bash(*.claude.json*)", perms_of(profile)["deny"]
                )
            for target in CLAUDE_SHELL_FORM_TARGETS:
                with self.subTest(role=role, target=target):
                    self.assertTrue(
                        decide(load_profile(role), "Bash", target) == "deny"
                    )

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
    """A1-D5: every deny entry stays reachable against a contradictory allow.

    The precedence rule this leans on is stated once, in the module docstring.
    """

    def contradiction_target(self, rule):
        """One (tool, target) the deny rule matches, for every deny entry."""
        name, matcher = split_rule(rule)
        if rule in LEAF_CONTRADICTION_TARGETS:
            return name, LEAF_CONTRADICTION_TARGETS[rule]
        if name == "Bash" and matcher in BASH_TARGETS:
            return name, BASH_TARGETS[matcher][0]
        if name == "Read" and matcher in READ_TARGETS:
            return name, READ_TARGETS[matcher][0]
        for tool, pattern, target, _lookalike in EXTRA_TARGETS + CLAUDE_WRITE_TARGETS:
            if rule == "%s(%s)" % (tool, pattern):
                return tool, target
        if name == "Bash":
            # The push and dispatch fences: their witness is a corpus row, so
            # this test re-checks the KEYDENY property (a fence decides a real
            # command) instead of trusting a second, hand-kept table.
            witness = deny_witness(matcher)
            if witness is not None:
                return name, witness
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
        rule = "Bash(*git*push* main)"
        tool, target = self.contradiction_target(rule)
        self.assertEqual(target, "git push origin main")
        self.assertEqual(
            decide({"permissions": {"allow": [rule], "deny": []}}, tool, target),
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

    def test_only_the_routing_apply_grant_survives(self):
        # Round 13 (D-159): every push and workflow-dispatch grant is gone, so a
        # role's allow list is a shared GRANT_SETS entry, never a per-role copy -
        # and only l1-routing carries anything at all (the apply.sh grant).
        profiles = {role: perms_of(load_profile(role))["allow"] for role in ROLES}
        for role in ROLES:
            expected = [lp.APPLY_ALLOW] if role == "l1-routing" else []
            with self.subTest(role=role):
                self.assertEqual(profiles[role], expected)
        for name, grants in lp.GRANT_SETS.items():
            with self.subTest(grant_set=name):
                for rule in grants:
                    self.assertNotIn("push", rule)
                    self.assertNotIn("workflow", rule)

    def test_the_fence_core_is_rendered_on_every_profile(self):
        # The surviving guard is the fence that names main, plus the dispatch
        # shape. Rendered identically everywhere - a role that cannot push has
        # no reason to lose the fence, and a role that can has no wider one.
        profiles = {role: perms_of(load_profile(role))["deny"] for role in ROLES}
        fenced = list(lp.MAIN_FENCE) + list(lp.GH_REF_MAIN)
        for role in ROLES:
            with self.subTest(role=role):
                for rule in fenced:
                    self.assertIn(rule, profiles[role])
                self.assertEqual(profiles[role][:len(fenced)], fenced)

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
