"""Tests for the diff risk classifier: tools/autoos_risk.py and the
`autoos-agent.py risk` verb (RISKTIER-a, operator Q-013 / D-060 2026-09-28).

The rule the operator set: the risk class of a change is decided by CODE from
the diff, never by the writer. So these tests are about the diff, not about a
card field — every entry in `policy.risk_rules` is applied by `classify`, and
the shapes a human used to judge (a path glob, a deletion, a sudo line, a
registry policy edit) are read off `git diff`.

Fixtures are temp git repos built here (portable, no network, nothing spawned).
The injected `runner` is `(args, cwd) -> (returncode, stdout)`, so the parsing
tests never touch git; the `assess` tests use the real default runner.

Run from the repo root:

    python3 tests/test_autoos_risk.py [ClassName]
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
sys.path.insert(0, str(TOOLS))

import autoos_risk as risk  # noqa: E402


def load_agent():
    spec = importlib.util.spec_from_file_location("autoos_agent_risk", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def real_registry():
    with open(REGISTRY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def rule(type_, **kwargs):
    """One policy.risk_rules entry, in the registry's own key shape."""
    entry = {"type": type_, "reason": kwargs.pop("reason", "test rule"),
             "source": "test"}
    entry.update(kwargs)
    return entry


def registry_with(rules, percent=20):
    """A registry carrying just the policy the classifier reads."""
    policy = {"risk_rules": list(rules)}
    if percent is not None:
        policy["risk_audit_percent"] = {"value": percent, "source": "test"}
    return {"policy": policy}


def files(*paths, deleted=(), renamed=()):
    """The changed_files() shape: {"path", "old_path", "deleted"} dicts, in diff order.

    `renamed` takes (old, new) pairs. A rename carries BOTH paths because the
    glob has to see the old one too — `git mv AGENTS.md docs/AGENTS.md` keeps
    matching `AGENTS.md` only if the reader keeps the name the change moved away
    from (RISKTIER-a2 finding 1).
    """
    out = [{"path": p, "old_path": None, "deleted": False} for p in paths]
    out.extend({"path": p, "old_path": None, "deleted": True} for p in deleted)
    out.extend({"path": new, "old_path": old, "deleted": False}
               for old, new in renamed)
    return out


def nul(*fields):
    """git's `-z` byte stream: every field NUL-terminated, nothing else."""
    return "".join(f + "\0" for f in fields)


def secret_shape(kind):
    """A high-confidence secret SHAPE, assembled at runtime (RISKTIER-a2 finding 2).

    This file is tracked, and AGENTS.md rule 1 keeps a literal key shape out of a
    tracked file — so the tests never write one as a string. Every character here
    is invented; the point is that the classifier's regexes fire on the SHAPE, and
    these are the shapes `tools/autoos_redact.py` already masks.
    """
    return {
        "pem": "-----BEGIN" + " " + "RSA" + " PRIVATE KEY-----",
        "aws": "AKIA" + "ABCDEFGHIJKLMNPQRS",
        "github": "ghp_" + "a" * 36,
        "openai": "sk-" + "K" * 24,
        "slack": "xox" + "b" + "-" + "1234567890",
    }[kind]


# ── the glob matcher (one implementation, tested as a table) ────────────────

GLOB_TABLE = [
    # a plain segment matches exactly one path segment
    ("AGENTS.md", "AGENTS.md", True),
    ("AGENTS.md", "docs/AGENTS.md", False),
    ("setup.sh", "setup.sh", True),
    ("setup.sh", "lib/setup.sh", False),
    # `*` does not cross a directory boundary
    ("catalog/*.json", "catalog/ai-registry.json", True),
    ("catalog/*.json", "catalog/deep/nested.json", False),
    # `**` means any depth, INCLUDING zero segments
    ("a/**/b", "a/b", True),
    ("a/**/b", "a/x/y/b", True),
    ("a/**/b", "x/a/b", False),
    ("lib/**/*.sh", "lib/a.sh", True),
    ("lib/**/*.sh", "lib/windows/a/b.sh", True),
    ("lib/**/*.sh", "lib/windows/a.ps1", False),
    (".agents/skills/**", ".agents/skills/foo/SKILL.md", True),
    (".agents/skills/**", ".agents/skills/SKILL.md", True),
    (".agents/skills/**", ".agents/other/SKILL.md", False),
    # a leading `**/` matches at the root and at depth
    ("**/*.key", "server.key", True),
    ("**/*.key", "configuration/keys/server.key", True),
    ("**/*.key", "server.pem", False),
    ("**/install*.sh", "scripts/install-foo.sh", True),
    ("**/install*.sh", "install.sh", True),
    ("**/install*.sh", "Windows/install.ps1", False),
    ("**/migrations/**", "infra/migrations/001.sql", True),
    ("**/migrations/**", "infra/migrations/a/002.sql", True),
    ("**/migrations/**", "infra/plans/001.sql", False),
    # `*` inside a segment is not a directory wildcard
    ("**/*secret*", "configuration/db-secrets.env", True),
    # the last pattern segment is matched against the LAST path segment
    ("**/*secret*", "configuration/nested/asecretb/file", False),
    ("**/*secret*", "configuration/nested/a/secret", True),
    ("docs/plans/**", "docs/notes.md", False),
    ("docs/plans/**", "docs/plans/2026-09-25-spec.md", True),
    # RISKTIER-a2 finding 6: the globs the operator's rule table was missing.
    # `*` is case-sensitive through fnmatchcase, which is why **/Install* and
    # **/install*.sh are two rules and not one.
    ("**/*.sql", "infra/db/001.sql", True),
    ("**/*.sql", "infra/db/notes.md", False),
    ("**/setup*", "setup.py", True),
    ("**/setup*", "infra/mcp-servers/cao-setup/setup_cao.py", True),
    ("**/setup*", "docs/setup-notes.md", True),
    ("**/bootstrap*", "scripts/bootstrap.sh", True),
    ("**/bootstrap*", "scripts/debootstrap-note.md", False),
    ("**/Install*", "Windows/powershell/Install-Tools.ps1", True),
    ("**/Install*", "Windows/powershell/install-tools.ps1", False),
    ("docs/**/*spec*.md", "docs/routing/spec-v2.md", True),
    ("docs/**/*spec*.md", "docs/routing/v2-spec.md", True),
    ("docs/**/*spec*.md", "docs/routing/notes.md", False),
    # RISKTIER-a2 finding 2: the secret file shapes, and the template that must
    # stay out of them (AGENTS.md rule 1: tracked files carry `.example`).
    ("**/*.pem", "server.pem", True),
    ("**/*.pem", "infra/keys/tls/server.pem", True),
    ("**/*.pem", "infra/keys/tls/server.key.example", False),
    ("**/.env", ".env", True),
    ("**/.env", "configuration/litellm/.env", True),
    ("**/.env", "configuration/litellm/.env.local", False),
    ("**/.env.*", "configuration/litellm/.env.local", True),
    ("**/.env.*", ".env.server", True),
    ("**/.env", "configuration/litellm/env", False),
    # `exclude` is a second glob the rule reads, so it needs the same matcher:
    # `.env.example` matches `**/.env.*` and is the template, not the secret.
    ("**/*.example", ".env.example", True),
    ("**/*.example", "configuration/litellm/.env.example", True),
    ("**/*.example", "configuration/litellm/.env.local", False),
]


class GlobMatcherTests(unittest.TestCase):
    def test_the_table(self):
        for pattern, path, expected in GLOB_TABLE:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(risk.glob_match(pattern, path), expected)


# ── path_glob ───────────────────────────────────────────────────────────────

class PathGlobRuleTests(unittest.TestCase):
    def test_a_matching_path_is_high_with_a_reason_naming_it(self):
        reg = registry_with([rule("path_glob", pattern="configuration/**/*.env",
                                  reason="secrets handling")])
        out = risk.classify(files("configuration/a.env"), {}, reg)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"], ["secrets handling: configuration/a.env"])

    def test_a_non_matching_path_stays_normal(self):
        reg = registry_with([rule("path_glob", pattern="configuration/**/*.env",
                                  reason="secrets handling")])
        out = risk.classify(files("configuration/a.yml"), {}, reg)
        self.assertEqual(out["risk"], "normal")
        self.assertEqual(out["reasons"], [])

    def test_each_matching_path_of_one_rule_is_reported(self):
        reg = registry_with([rule("path_glob", pattern="**/*.key",
                                  reason="secrets handling")])
        out = risk.classify(files("a.key", "deep/b.key"), {}, reg)
        self.assertEqual(out["reasons"], ["secrets handling: a.key",
                                          "secrets handling: deep/b.key"])

    def test_a_rule_without_a_pattern_is_not_applied_to_everything(self):
        reg = registry_with([rule("path_glob", reason="secrets handling")])
        self.assertEqual(risk.classify(files("a.md"), {}, reg)["risk"], "normal")

    def test_a_rename_is_matched_on_the_path_it_moved_away_from(self):
        # Finding 1, the silent `normal`: `git mv AGENTS.md docs/AGENTS.md` reads
        # as one changed path (the new one), so a rule on `AGENTS.md` sees nothing
        # and a policy file walks out of the high-risk class inside a rename.
        reg = registry_with([rule("path_glob", pattern="AGENTS.md", reason="policy")])
        out = risk.classify(files(renamed=[("AGENTS.md", "docs/AGENTS.md")]), {}, reg)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"], ["policy: AGENTS.md -> docs/AGENTS.md"])

    def test_a_rename_that_matches_the_new_path_reports_the_new_path(self):
        reg = registry_with([rule("path_glob", pattern="docs/**", reason="plans")])
        out = risk.classify(files(renamed=[("AGENTS.md", "docs/AGENTS.md")]), {}, reg)
        self.assertEqual(out["reasons"], ["plans: docs/AGENTS.md"])

    def test_a_rename_matches_a_glob_that_only_fits_the_old_path(self):
        reg = registry_with([rule("path_glob", pattern="**/*.key",
                                  reason="secrets")])
        out = risk.classify(files(renamed=[("configuration/server.key",
                                             "configuration/server.txt")]), {}, reg)
        self.assertEqual(out["risk"], "high")

    def test_an_excluded_glob_keeps_the_template_out_of_the_rule(self):
        # Finding 2: `**/.env.*` is a secret shape and `.env.example` is the
        # tracked template AGENTS.md rule 1 tells everyone to commit. Without the
        # exclusion every honest template edit pays for high-risk review, which
        # is how a rule gets switched off; with it, only the real file raises.
        reg = registry_with([rule("path_glob", pattern="**/.env.*",
                                  exclude="**/*.example", reason="secrets")])
        secret = risk.classify(files("configuration/litellm/.env.local"), {}, reg)
        template = risk.classify(files("configuration/litellm/.env.example"), {}, reg)
        self.assertEqual(secret["risk"], "high")
        self.assertEqual(template["risk"], "normal")
        self.assertEqual(template["reasons"], [])

    def test_an_excluded_glob_still_matches_the_real_file(self):
        reg = registry_with([rule("path_glob", pattern="**/.env",
                                  exclude="**/*.example", reason="secrets")])
        self.assertEqual(risk.classify(files(".env"), {}, reg)["risk"], "high")


# ── diff_deletion ───────────────────────────────────────────────────────────

class DiffDeletionRuleTests(unittest.TestCase):
    def test_a_deleted_file_is_high(self):
        reg = registry_with([rule("diff_deletion", reason="deletion paths")])
        out = risk.classify(files("notes.md", deleted=["gone.md"]), {}, reg)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"], ["deletion paths: gone.md"])

    def test_no_deletion_is_normal(self):
        reg = registry_with([rule("diff_deletion", reason="deletion paths")])
        out = risk.classify(files("notes.md", "other.md"), {}, reg)
        self.assertEqual(out["risk"], "normal")

    def test_a_deleted_test_file_is_high(self):
        # The operator's wording is "deletes more than 200 lines, OR deletes a
        # test file": the second half is the file-level deletion this rule already
        # reads, so it stays covered when the volume threshold is added.
        reg = registry_with([rule("diff_deletion", reason="deletion paths",
                                  min_deleted_lines=200)])
        out = risk.classify(files("notes.md", deleted=["tests/test_install.py"]),
                            {}, reg, deleted_lines=3)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"], ["deletion paths: tests/test_install.py"])

    def test_more_lines_deleted_than_the_threshold_is_high(self):
        reg = registry_with([rule("diff_deletion", reason="deletion paths",
                                  min_deleted_lines=200)])
        out = risk.classify(files("notes.md"), {}, reg, deleted_lines=201)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"], ["deletion paths: 201 lines deleted"])

    def test_the_threshold_is_more_than_not_at_least(self):
        reg = registry_with([rule("diff_deletion", reason="deletion paths",
                                  min_deleted_lines=200)])
        self.assertEqual(risk.classify(files("notes.md"), {}, reg,
                                       deleted_lines=200)["risk"], "normal")

    def test_a_volume_rule_does_not_fire_without_the_count(self):
        # A threshold with no numstat behind it must not read every diff as a
        # large deletion: 0 lines is below every threshold.
        reg = registry_with([rule("diff_deletion", reason="deletion paths",
                                  min_deleted_lines=200)])
        self.assertEqual(risk.classify(files("notes.md"), {}, reg)["risk"], "normal")

    def test_a_deleted_file_is_still_named_when_the_volume_also_raises(self):
        reg = registry_with([rule("diff_deletion", reason="deletion paths",
                                  min_deleted_lines=200)])
        out = risk.classify(files(deleted=["gone.md"]), {}, reg, deleted_lines=500)
        self.assertEqual(out["reasons"], ["deletion paths: gone.md",
                                          "deletion paths: 500 lines deleted"])


# ── added_regex (NEW type) ──────────────────────────────────────────────────

class AddedRegexRuleTests(unittest.TestCase):
    def test_a_sudo_line_in_an_added_file_is_high(self):
        reg = registry_with([rule("added_regex", pattern=r"\bsudo\b",
                                  reason="root/sudo (R-orch-10)")])
        out = risk.classify(files("tests/test_x.py"),
                            {"tests/test_x.py": ['def test_it():',
                                                 '    run("sudo rm -rf /tmp/x")']},
                            reg)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"], ["root/sudo (R-orch-10): tests/test_x.py"])

    def test_a_sudo_word_inside_an_identifier_does_not_match(self):
        reg = registry_with([rule("added_regex", pattern=r"\bsudo\b",
                                  reason="root/sudo (R-orch-10)")])
        out = risk.classify(files("t.py"), {"t.py": ["autosudo = True"]}, reg)
        self.assertEqual(out["risk"], "normal")

    def test_matching_is_case_sensitive(self):
        reg = registry_with([rule("added_regex", pattern=r"\bsudo\b",
                                  reason="root/sudo (R-orch-10)")])
        out = risk.classify(files("t.sh"), {"t.sh": ["SUDO=1"]}, reg)
        self.assertEqual(out["risk"], "normal")

    def test_a_paths_glob_limits_which_files_are_scanned(self):
        reg = registry_with([rule("added_regex", pattern=r"\bsudo\b",
                                  paths="**/*.sh", reason="root/sudo (R-orch-10)")])
        in_glob = risk.classify(files("a.sh"), {"a.sh": ["sudo apt install x"]}, reg)
        out_glob = risk.classify(files("a.py"), {"a.py": ["sudo apt install x"]}, reg)
        self.assertEqual(in_glob["risk"], "high")
        self.assertEqual(out_glob["risk"], "normal")

    def test_a_file_whose_added_lines_do_not_match_is_normal(self):
        reg = registry_with([rule("added_regex", pattern=r"\bsudo\b",
                                  reason="root/sudo (R-orch-10)")])
        out = risk.classify(files("a.sh"), {"a.sh": ["plain line"]}, reg)
        self.assertEqual(out["risk"], "normal")

    def test_a_bad_pattern_is_a_risk_error_not_a_silent_normal(self):
        reg = registry_with([rule("added_regex", pattern=r"\b(", reason="broken")])
        with self.assertRaises(risk.RiskError):
            risk.classify(files("a.sh"), {"a.sh": ["x"]}, reg)


# ── registry_policy (NEW type) ──────────────────────────────────────────────

class RegistryPolicyRuleTests(unittest.TestCase):
    def test_a_policy_change_is_high(self):
        reg = registry_with([rule("registry_policy", reason="routing policy")])
        out = risk.classify(files("catalog/ai-registry.json"), {}, reg,
                            registry_policy_changed=True)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"],
                         ["routing policy: catalog/ai-registry.json"])

    def test_a_models_only_registry_change_is_normal(self):
        reg = registry_with([rule("registry_policy", reason="routing policy")])
        out = risk.classify(files("catalog/ai-registry.json"), {}, reg,
                            registry_policy_changed=False)
        self.assertEqual(out["risk"], "normal")
        self.assertEqual(out["reasons"], [])

    def test_the_flag_never_raises_risk_without_the_rule(self):
        reg = registry_with([rule("path_glob", pattern="README.md")])
        out = risk.classify(files("catalog/ai-registry.json"), {}, reg,
                            registry_policy_changed=True)
        self.assertEqual(out["risk"], "normal")


# ── classify: several rules at once ─────────────────────────────────────────

class ClassifyShapeTests(unittest.TestCase):
    def test_every_matching_rule_contributes_one_reason(self):
        reg = registry_with([
            rule("path_glob", pattern="setup.sh",
                 reason="user PATH/profile/config writers"),
            rule("diff_deletion", reason="deletion paths"),
            rule("added_regex", pattern=r"\bsudo\b", reason="root/sudo (R-orch-10)"),
        ])
        out = risk.classify(files("setup.sh", deleted=["old.sh"]),
                            {"setup.sh": ["sudo reboot"]}, reg)
        self.assertEqual(out["risk"], "high")
        self.assertEqual(out["reasons"], [
            "user PATH/profile/config writers: setup.sh",
            "deletion paths: old.sh",
            "root/sudo (R-orch-10): setup.sh",
        ])

    def test_an_unknown_rule_type_raises_risk_error(self):
        # A rule nothing applies is a rule that silently does not exist — the
        # classifier refuses to guess. registry.py validate is the gate that
        # keeps this out of the committed registry (see RiskRuleShapeTests).
        reg = registry_with([rule("mime_type", pattern="text", reason="nonsense")])
        with self.assertRaises(risk.RiskError):
            risk.classify(files("a.txt"), {}, reg)

    def test_a_registry_with_no_policy_is_normal(self):
        self.assertEqual(risk.classify(files("a.md"), {}, {})["risk"], "normal")


# ── audit: deterministic by the sha ────────────────────────────────────────

class AuditTests(unittest.TestCase):
    def test_a_sha_known_to_be_inside_the_percent(self):
        # int("28ada0a", 16) % 100 == 18, so a 20% audit samples it.
        self.assertTrue(risk.audit("28ada0a", 20))

    def test_a_sha_known_to_be_outside_the_percent(self):
        # int("59aa3a9", 16) % 100 == 21, so a 20% audit skips it.
        self.assertFalse(risk.audit("59aa3a9", 20))

    def test_zero_percent_never_audits_and_hundred_always_does(self):
        for sha in ("28ada0a", "59aa3a9", "0" * 40, "f" * 40):
            with self.subTest(sha=sha):
                self.assertFalse(risk.audit(sha, 0))
                self.assertTrue(risk.audit(sha, 100))

    def test_the_same_sha_gives_the_same_answer_every_time(self):
        self.assertEqual({risk.audit("59aa3a9", 20) for _ in range(50)}, {False})

    def test_a_full_sha_is_bucketed_on_its_first_12_hex_digits_only(self):
        # int("28ada0a00000", 16) % 100 == 68, so a 20% audit skips it and an
        # 80% one takes it — and the digits after the 12th change nothing.
        head = "28ada0a00000"
        for tail in ("", "0" * 28, "f" * 28):
            sha = head + tail
            with self.subTest(sha=sha[:16]):
                self.assertFalse(risk.audit(sha, 20))
                self.assertTrue(risk.audit(sha, 80))

    def test_a_non_hex_sha_is_a_risk_error(self):
        with self.assertRaises(risk.RiskError):
            risk.audit("not-a-sha", 20)


# ── assess: the rev is resolved to a commit before anything reads it ────────

class ShaResolutionTests(unittest.TestCase):
    """Finding 4: `risk --sha HEAD` is not hex, so the audit draw raised while the
    diff classified fine, and one commit could answer the draw twice — `28ada0a`
    buckets on 7 digits, the same commit in full buckets on 12. `assess` now
    resolves the rev with git and hashes what git answered."""

    FULL = "28ada0a" + "0" * 33     # int(FULL[:12], 16) % 100 == 68

    def setUp(self):
        self.calls = []

    def runner(self, args, cwd):
        self.calls.append(args)
        if args[0] == "rev-parse":
            return 0, self.FULL + "\n"
        if args[0] == "merge-base":
            return 0, "mb\n"
        if args[0] == "diff":
            if "--numstat" in args:
                return 0, nul("1\t1\tnotes.md")
            if "--name-status" in args:
                return 0, nul("M", "notes.md")
            return 0, ""                       # the -U0 body: nothing added
        return 1, ""                           # git show: no registry in this repo

    def assess(self, rev):
        reg = registry_with([rule("path_glob", pattern="other.md",
                                  reason="plans")], percent=20)
        return risk.assess("/repo", "origin/main", rev, reg, runner=self.runner)

    def test_the_short_rev_is_resolved_before_the_audit_draw(self):
        out = self.assess("28ada0a")
        self.assertEqual(out["sha"], self.FULL)
        # 7 digits would have been drawn in (18 < 20); the commit itself is out.
        self.assertTrue(risk.audit("28ada0a", 20))
        self.assertFalse(out["audit"])
        self.assertEqual(out["audit"], risk.audit(self.FULL, 20))

    def test_the_first_git_call_is_a_commit_lookup_and_every_later_one_uses_it(self):
        self.assess("28ada0a")
        self.assertEqual(self.calls[0], ["rev-parse", "--verify", "28ada0a^{commit}"])
        for args in self.calls[1:]:
            if args[0] in ("merge-base", "diff"):
                self.assertIn(self.FULL, args, args)

    def test_a_branch_or_head_names_the_same_commit_as_its_sha(self):
        for rev in ("HEAD", "L1-routing/RISKTIER"):
            with self.subTest(rev=rev):
                self.calls = []
                out = self.assess(rev)
                self.assertEqual(out["sha"], self.FULL)
                self.assertEqual(self.calls[0],
                                 ["rev-parse", "--verify", "%s^{commit}" % rev])

    def test_an_unresolvable_rev_is_a_risk_error_not_a_silent_normal(self):
        def runner(args, cwd):
            return (128, "fatal: not a valid object name") if args[0] == "rev-parse" \
                else (0, "")

        reg = registry_with([rule("path_glob", pattern="a.md")])
        with self.assertRaises(risk.RiskError):
            risk.assess("/repo", "origin/main", "0" * 40, reg, runner=runner)

    def test_rev_output_that_is_not_a_commit_hex_is_a_risk_error(self):
        # The resolved value is handed to git again and to the audit hash, so a
        # runner that answers with anything but 40 hex must be refused here rather
        # than raise from `audit()` after the classification already printed.
        def runner(args, cwd):
            if args[0] == "rev-parse":
                return 0, "not-a-commit\n"
            return 0, ""

        self.assertRaises(risk.RiskError, risk.resolve_sha, "/repo", "HEAD", runner)

    def test_resolve_sha_takes_the_first_line_and_no_whitespace(self):
        def runner(args, cwd):
            return 0, self.FULL + "\n\n"

        self.assertEqual(risk.resolve_sha("/repo", "HEAD", runner), self.FULL)


# ── changed_files / added_lines: the git plumbing ──────────────────────────

class GitParsingTests(unittest.TestCase):
    def test_name_status_rows_become_paths_with_deletions_flagged(self):
        seen = {}

        def runner(args, cwd):
            seen["args"] = args
            seen["cwd"] = cwd
            if args[0] == "merge-base":
                return 0, "mb-sha\n"
            return 0, nul("M", "lib/a.sh", "D", "old.md", "A", "new.md")

        out = risk.changed_files("/repo", "base-sha", "head-sha", runner)
        self.assertEqual(out, [{"path": "lib/a.sh", "old_path": None,
                                "deleted": False},
                               {"path": "old.md", "old_path": None, "deleted": True},
                               {"path": "new.md", "old_path": None,
                                "deleted": False}])
        self.assertEqual(seen["args"][:3], ["diff", "--name-status", "-z"])
        # the diff runs from the merge base, not from `base` itself
        self.assertEqual(seen["args"],
                         ["diff", "--name-status", "-z", "mb-sha", "head-sha"])
        self.assertEqual(seen["cwd"], "/repo")

    def test_a_rename_row_carries_both_paths(self):
        def runner(args, cwd):
            return 0, nul("R100", "old/name.py", "new/name.py")

        self.assertEqual(risk.changed_files("/repo", "b", "s", runner),
                         [{"path": "new/name.py", "old_path": "old/name.py",
                           "deleted": False}])

    def test_a_copy_row_carries_both_paths(self):
        def runner(args, cwd):
            return 0, nul("C75", "lib/a.sh", "lib/b.sh")

        self.assertEqual(risk.changed_files("/repo", "b", "s", runner),
                         [{"path": "lib/b.sh", "old_path": "lib/a.sh",
                           "deleted": False}])

    def test_a_path_with_a_tab_a_newline_or_an_accent_is_read_whole(self):
        # Finding 5. Without `-z` git C-quotes these: `M\t"docs/tab\\tname.md"`,
        # where the tab the parser splits on is git's own field separator and the
        # quotes are part of the bytes. Every one of those paths then fails every
        # glob in the table — the silent `normal` again, this time from a filename.
        def runner(args, cwd):
            return 0, nul("M", "docs/tab\tname.md",
                          "A", "docs/new\nline.md",
                          "M", "docs/ünïcode-résumé.md")

        self.assertEqual(risk.changed_files("/repo", "b", "s", runner),
                         [{"path": "docs/tab\tname.md", "old_path": None,
                           "deleted": False},
                          {"path": "docs/new\nline.md", "old_path": None,
                           "deleted": False},
                          {"path": "docs/ünïcode-résumé.md", "old_path": None,
                           "deleted": False}])

    def test_a_file_named_like_a_status_letter_is_not_read_as_a_status(self):
        # The `-z` stream has no record separator, so the parse walks it by the
        # arity each status letter declares: one path for M/A/D, two for R/C. A
        # file called `A100` is the path of the record before it, never a new
        # status — which is why the reader must not split on whitespace.
        def runner(args, cwd):
            return 0, nul("M", "A100", "R100", "M", "D")

        self.assertEqual(risk.changed_files("/repo", "b", "s", runner),
                         [{"path": "A100", "old_path": None, "deleted": False},
                          {"path": "D", "old_path": "M", "deleted": False}])

    def test_numstat_deletions_are_summed_and_a_rename_pair_is_not_double_counted(self):
        seen = {}

        def runner(args, cwd):
            seen["args"] = args
            if args[0] == "merge-base":
                return 0, "mb\n"
            return 0, nul("300\t0\tbig.md",          # added
                          "0\t0\t",                  # rename: empty path, pair follows
                          "docs/plans/spec-old.md",
                          "docs/plans/spec-new.md",
                          "0\t120\tnotes.md",        # deleted lines
                          "-\t-\tbin.dat",           # binary: no counts to read
                          "200\t90\t")               # second rename pair
        # the two paths of that last record are absent on purpose: the reader
        # sums the counts and never walks the paths, so it must not care.

        self.assertEqual(risk.deleted_lines("/repo", "base", "head", runner), 210)
        self.assertEqual(seen["args"], ["diff", "--numstat", "-z", "mb", "head"])

    def test_merge_base_is_asked_for_before_the_diff(self):
        calls = []

        def runner(args, cwd):
            calls.append(args)
            return 0, ("mb-sha\n" if args[0] == "merge-base" else "")

        risk.changed_files("/repo", "origin/main", "head-sha", runner)
        self.assertEqual(calls[0][:2], ["merge-base", "origin/main"])
        self.assertEqual(calls[1][-2:], ["mb-sha", "head-sha"])

    def test_added_lines_reads_only_the_plus_side_of_a_U0_diff(self):
        def runner(args, cwd):
            return 0, (
                "diff --git a/a.sh b/a.sh\n"
                "--- a/a.sh\n"
                "+++ b/a.sh\n"
                "@@ -1 +1 @@\n"
                "-sudo old\n"
                "+new line\n"
                "+sudo new\n"
                "diff --git a/b.md b/b.md\n"
                "--- /dev/null\n"
                "+++ b/b.md\n"
                "@@ -0,0 +1 @@\n"
                "+added\n"
            )

        self.assertEqual(risk.added_lines("/repo", "base", "head", runner),
                         {"a.sh": ["new line", "sudo new"], "b.md": ["added"]})

    def test_an_added_line_in_a_file_whose_name_git_quotes_is_still_read(self):
        # A path that needs quoting is written in the `+++` header as
        # `"b/tab\\tname.md"` even with core.quotepath=false — quoting is for
        # control characters, not just non-ASCII. Reading that header as a path
        # means unescaping it, and the failure to do so is the same silent
        # `normal`: the header matches nothing, `current` stays None, and every
        # added line of the file — sudo included — vanishes.
        def runner(args, cwd):
            return 0, (
                'diff --git "a/f\\tx.sh" "b/f\\tx.sh"\n'
                '--- "a/f\\tx.sh"\n'
                '+++ "b/f\\tx.sh"\n'
                "@@ -0,0 +1,2 @@\n"
                "+first line\n"
                "+sudo apt install x\n"
            )

        self.assertEqual(risk.added_lines("/repo", "base", "head", runner),
                         {"f\tx.sh": ["first line", "sudo apt install x"]})

    def test_a_quoted_rename_header_is_unescaped_to_the_new_path(self):
        def runner(args, cwd):
            return 0, (
                'diff --git "a/old\\tname.py" "b/new\\nline.py"\n'
                '--- "a/old\\tname.py"\n'
                '+++ "b/new\\nline.py"\n'
                "@@ -0,0 +1 @@\n"
                "+secret = True\n"
            )

        self.assertEqual(risk.added_lines("/repo", "base", "head", runner),
                         {"new\nline.py": ["secret = True"]})

    def test_added_lines_reads_only_the_plus_side_of_a_U0_diff(self):
        def runner(args, cwd):
            return 0, (
                "diff --git a/a.sh b/a.sh\n"
                "--- a/a.sh\n"
                "+++ b/a.sh\n"
                "@@ -1 +1 @@\n"
                "-sudo old\n"
                "+new line\n"
                "+sudo new\n"
                "diff --git a/b.md b/b.md\n"
                "--- /dev/null\n"
                "+++ b/b.md\n"
                "@@ -0,0 +1 @@\n"
                "+added\n"
            )

        self.assertEqual(risk.added_lines("/repo", "base", "head", runner),
                         {"a.sh": ["new line", "sudo new"], "b.md": ["added"]})

    def test_an_added_line_that_itself_starts_with_a_diff_plus_is_content(self):
        # A test fixture that embeds a diff, or a line of `+`s, is content, not a
        # header: git writes the added line `+++++ x` as `++++++ x`. Reading the
        # second `+` as a header would DROP the line — and a dropped `sudo` line
        # is the silent `normal` this whole module exists to prevent.
        def runner(args, cwd):
            return 0, (
                "diff --git a/t.py b/t.py\n"
                "--- a/t.py\n"
                "+++ b/t.py\n"
                "@@ -0,0 +1,3 @@\n"
                "+sudo first\n"
                "+++++ literal plus line\n"
                "+sudo last\n"
            )

        self.assertEqual(risk.added_lines("/repo", "base", "head", runner),
                         {"t.py": ["sudo first", "++++ literal plus line",
                                   "sudo last"]})

    def test_the_default_runner_pins_the_git_knobs_that_rewrite_diff_output(self):
        # A user's diff.noprefix drops the `b/` the header match needs, so every
        # added line vanishes and a sudo change reads as `normal`; color.diff
        # writes escape sequences into the same bytes; diff.external replaces the
        # diff with some script's stdout. The default runner pins all three —
        # the injected runners in these tests are the seam, not the contract
        # (R-worker-02: a fake that only ever sees the author's own git config
        # hides exactly this bug).
        seen = {}

        class FakeProc:
            returncode = 0
            stdout = ""

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return FakeProc()

        real_run = risk.subprocess.run
        risk.subprocess.run = fake_run
        try:
            risk._git(["diff", "-U0", "mb", "head"], "/repo")
        finally:
            risk.subprocess.run = real_run

        argv = " ".join(seen["argv"])
        for knob in ("core.quotepath=false", "diff.noprefix=false",
                     "color.diff=never"):
            self.assertIn("-c %s" % knob, argv, seen["argv"])
        self.assertIn("--no-ext-diff", argv, seen["argv"])
        self.assertEqual(seen["argv"][0], "git")
        # the pins come from the runner, the caller's own argv comes through last
        self.assertEqual(seen["argv"][-3:], ["-U0", "mb", "head"])
        self.assertLess(seen["argv"].index("diff"),
                        seen["argv"].index("--no-ext-diff"))
        self.assertEqual(seen["kwargs"]["cwd"], "/repo")

    def test_a_git_failure_raises_risk_error_not_a_silent_normal(self):
        def runner(args, cwd):
            return 128, "fatal: not a git repository"

        with self.assertRaises(risk.RiskError):
            risk.changed_files("/nope", "origin/main", "deadbeef", runner)


# ── assess against a real temp git repo ────────────────────────────────────

class _Repo(unittest.TestCase):
    """A throwaway commit-per-test repo, driven by the real default runner."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autoos-risk-repo-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._git(["init", "-q", "-b", "main"])
        self._git(["config", "user.name", "risk-test"])
        self._git(["config", "user.email", "risk-test@example.invalid"])
        self._git(["config", "commit.gpgsign", "false"])
        self.base = self.write_and_commit({"README.md": "hello\n"})

    def _git(self, args):
        return subprocess.run(["git"] + args, cwd=self.tmp, capture_output=True,
                              text=True, check=True).stdout

    def write_and_commit(self, changes):
        """Write (or delete, when the text is None) then commit; return the sha."""
        for name, text in changes.items():
            path = os.path.join(self.tmp, name)
            if text is None:
                os.remove(path)
                continue
            if "/" in name:
                os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        self._git(["add", "-A"])
        self._git(["commit", "-q", "-m", "risk fixture"])
        return self._git(["rev-parse", "HEAD"]).strip()

    def assess(self, sha, base=None, registry=None):
        if registry is None:
            registry = registry_with(real_registry()["policy"]["risk_rules"])
        return risk.assess(self.tmp, base or self.base, sha, registry)


class AssessTests(_Repo):
    def test_a_docs_only_diff_is_normal(self):
        sha = self.write_and_commit({"docs/handoff/notes.md": "a note\n"})
        result = self.assess(sha)
        self.assertEqual(result["risk"], "normal")
        self.assertEqual(result["reasons"], [])
        self.assertEqual([f["path"] for f in result["files"]],
                         ["docs/handoff/notes.md"])

    def test_a_skill_change_is_high(self):
        sha = self.write_and_commit(
            {".agents/skills/unattended-orchestration/SKILL.md": "# rule\n"})
        result = self.assess(sha)
        self.assertEqual(result["risk"], "high")
        self.assertIn("skill rules: "
                      ".agents/skills/unattended-orchestration/SKILL.md",
                      result["reasons"])

    def test_a_plan_or_spec_is_high(self):
        sha = self.write_and_commit({"docs/plans/2026-09-28-x.md": "# plan\n"})
        self.assertEqual(self.assess(sha)["risk"], "high")

    def test_a_deleted_file_is_high(self):
        sha = self.write_and_commit({"README.md": None})
        result = self.assess(sha)
        self.assertEqual(result["risk"], "high")
        self.assertIn("deletion paths: README.md", result["reasons"])

    def test_a_repo_with_a_hostile_diff_config_still_classifies_its_sudo_line(self):
        # End-to-end against real git bytes, with the config a developer actually
        # has set: without the pinned knobs this diff renders `+++ f.sh` (no `b/`)
        # and colored, and the added `sudo` line disappears into a silent
        # `normal`.
        self._git(["config", "diff.noprefix", "true"])
        self._git(["config", "color.diff", "always"])
        sha = self.write_and_commit({"f.sh": "set -euo pipefail\nsudo apt install x\n"})
        result = self.assess(sha)
        self.assertEqual(result["risk"], "high")
        self.assertIn("root/sudo (R-orch-10): f.sh", result["reasons"])

    def test_a_sudo_line_in_an_added_test_file_is_high(self):
        # The rule is textual, not semantic: a test that only MENTIONS sudo in
        # a string raises the class. Accepted — the cheap cross-family reviews
        # read it, the Sonnet final decides.
        sha = self.write_and_commit({"tests/test_install.py":
                                     'CMD = "sudo apt install x"\n'})
        result = self.assess(sha)
        self.assertEqual(result["risk"], "high")
        self.assertIn("root/sudo (R-orch-10): tests/test_install.py",
                      result["reasons"])

    def test_a_registry_policy_change_is_high(self):
        reg = registry_with([rule("registry_policy", reason="routing policy")],
                            percent=None)
        sha = self.write_and_commit(
            {"catalog/ai-registry.json": json.dumps({"policy": {"theta": 0.7}})})
        result = risk.assess(self.tmp, self.base, sha, reg)
        self.assertEqual(result["risk"], "high")
        self.assertEqual(result["reasons"],
                         ["routing policy: catalog/ai-registry.json"])

    def test_a_models_only_registry_change_is_normal(self):
        reg = registry_with([rule("registry_policy", reason="routing policy")],
                            percent=None)
        base = self.write_and_commit(
            {"catalog/ai-registry.json":
             json.dumps({"policy": {"theta": 0.7}, "models": {"a": {}}})})
        sha = self.write_and_commit(
            {"catalog/ai-registry.json":
             json.dumps({"policy": {"theta": 0.7}, "models": {"b": {}}})})
        result = risk.assess(self.tmp, base, sha, reg)
        self.assertEqual(result["risk"], "normal")
        self.assertEqual(result["reasons"], [])

    def test_an_unparseable_registry_is_a_risk_error(self):
        reg = registry_with([rule("registry_policy", reason="routing policy")])
        sha = self.write_and_commit({"catalog/ai-registry.json": "{not json\n"})
        with self.assertRaises(risk.RiskError):
            self.assess(sha)

    def test_assess_carries_the_audit_answer_for_the_head_sha(self):
        sha = self.write_and_commit({"docs/handoff/notes.md": "a note\n"})
        result = self.assess(sha)
        self.assertEqual(result["audit_percent"], 20)
        self.assertEqual(result["audit"], risk.audit(sha, 20))

    def test_the_percent_falls_back_to_the_declared_default(self):
        reg = registry_with([rule("path_glob", pattern="README.md")], percent=None)
        sha = self.write_and_commit({"docs/handoff/notes.md": "a note\n"})
        self.assertEqual(risk.assess(self.tmp, self.base, sha, reg)["audit_percent"],
                         risk.DEFAULT_AUDIT_PERCENT)

    def test_a_missing_sha_raises_risk_error(self):
        with self.assertRaises(risk.RiskError):
            self.assess("0" * 40)

    def test_a_short_rev_head_and_branch_answer_the_same_commit(self):
        # Finding 4, against real git: `HEAD` is not hex, so the audit draw raised
        # RiskError and the verb exited 2 on a perfectly classifiable commit.
        sha = self.write_and_commit({"docs/handoff/notes.md": "a note\n"})
        self._git(["branch", "risk-branch", sha])
        for rev in (sha, sha[:7], "HEAD", "risk-branch"):
            with self.subTest(rev=rev):
                result = self.assess(rev)
                self.assertEqual(result["sha"], sha)
                self.assertEqual(result["risk"], "normal")
                self.assertEqual(result["audit"], risk.audit(sha, 20))

    def test_a_policy_file_moved_out_of_its_glob_is_still_high(self):
        # Finding 1 on a real rename: git reports one changed path, the new one,
        # and `AGENTS.md` — the rule the operator named — never appears in the
        # diff again. The move is the evasion.
        base = self.write_and_commit({"AGENTS.md": "# working rules\n"})
        os.makedirs(os.path.join(self.tmp, "docs"), exist_ok=True)
        self._git(["mv", "AGENTS.md", "docs/AGENTS.md"])
        self._git(["add", "-A"])
        self._git(["commit", "-q", "-m", "move the policy file"])
        sha = self._git(["rev-parse", "HEAD"]).strip()
        result = self.assess(sha, base=base)
        self.assertEqual(result["risk"], "high")
        self.assertIn("policy: AGENTS.md -> docs/AGENTS.md", result["reasons"])

    @unittest.skipIf(os.name == "nt",
                     "a newline is not legal in a Windows filename")
    def test_paths_with_a_tab_a_newline_and_accents_are_read_and_matched(self):
        # Findings 5 and 1 together, through real git bytes: these three names all
        # need C-quoting in `--name-status`, which is exactly where a tab stops
        # being part of a path and becomes git's field separator.
        names = ["docs/plans/tab\tname.md", "docs/plans/new\nline.md",
                 "docs/plans/ünïcode-résumé.md"]
        sha = self.write_and_commit({name: "a plan\n" for name in names})
        result = self.assess(sha)
        self.assertEqual(sorted(f["path"] for f in result["files"]), sorted(names))
        self.assertEqual(result["risk"], "high")
        for name in names:
            self.assertIn("plans/specs (R-orch-13): " + name, result["reasons"],
                          result["reasons"])

    @unittest.skipIf(os.name == "nt",
                     "a tab in a filename is not portable, and the parsing "
                     "contract is covered by the injected-runner tests above")
    def test_a_sudo_line_in_a_file_whose_name_needs_quoting_is_high(self):
        # The `+++` header of the same awkward name is quoted too, and a quoted
        # header matches neither `b/` nor `/dev/null`: every added line of the
        # file is dropped before a rule ever sees it.
        sha = self.write_and_commit({"f\tx.sh": "sudo apt install x\n"})
        result = self.assess(sha)
        self.assertEqual(result["risk"], "high")
        self.assertIn("root/sudo (R-orch-10): f\tx.sh", result["reasons"])

    def test_a_large_deletion_is_high_and_a_small_one_is_normal(self):
        # Finding 6: `diff_deletion` only saw a whole file disappearing. A change
        # that deletes 240 lines from a file that survives — or deletes a test's
        # assertions — was `normal`, and the class is supposed to be about how
        # much the change takes away, not about whether the name is still there.
        big = "".join("line %d\n" % i for i in range(250))
        base = self.write_and_commit({"notes.md": big})
        sha = self.write_and_commit({"notes.md": "line 0\nline 1\n"})
        result = self.assess(sha, base=base)
        self.assertEqual(result["risk"], "high")
        self.assertIn("deletion paths: 248 lines deleted", result["reasons"])
        small = self.write_and_commit({"notes.md": "line 0\nline 1\nline 2\n"})
        self.assertEqual(self.assess(small, base=sha)["risk"], "normal")

    def test_a_secret_shape_in_an_ordinary_file_is_high(self):
        # Finding 2: the secret rules were all path globs, so a key added to
        # `notes.txt` — any name that is not *.key or *secret* — was `normal`.
        # The shapes are assembled by `secret_shape()` so this file carries none.
        reg = registry_with([r for r in real_registry()["policy"]["risk_rules"]
                             if r["type"] == "added_regex"])
        for kind in ("pem", "aws", "github", "openai", "slack"):
            with self.subTest(shape=kind):
                line = "token = " + secret_shape(kind)
                sha = self.write_and_commit({"notes.txt": line + "\n"})
                result = risk.assess(self.tmp, self.base, sha, reg)
                self.assertEqual(result["risk"], "high", result["reasons"])
                self.assertTrue(any(r.startswith("secret content: notes.txt")
                                    for r in result["reasons"]), result["reasons"])


# ── the committed registry itself ──────────────────────────────────────────

class RealRegistryRuleTests(unittest.TestCase):
    def test_the_operator_rules_are_present(self):
        rules = real_registry()["policy"]["risk_rules"]
        globs = {r["pattern"] for r in rules if r["type"] == "path_glob"}
        for pattern in (".agents/skills/**", "AGENTS.md", "CLAUDE.md",
                        "docs/plans/**", "tools/autoos_agent_mcp.py",
                        "catalog/*.schema.json", "docs/agent-protocol.md",
                        "tools/autoos_redact.py", "**/*.key", "**/*secret*",
                        "**/migrations/**", "**/*.pg", "Windows/ansible/**",
                        "**/install*.sh", "**/install*.ps1"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, globs)
        self.assertIn(r"\bsudo\b", {r.get("pattern") for r in rules
                                    if r["type"] == "added_regex"})
        self.assertIn("registry_policy", {r["type"] for r in rules})

    def test_the_risktier_a2_globs_are_declared(self):
        # Finding 6: the shapes the rule table was missing, and finding 2: the
        # file-level secret shapes that joined them.
        rules = real_registry()["policy"]["risk_rules"]
        globs = {r["pattern"] for r in rules if r["type"] == "path_glob"}
        for pattern in ("**/*.sql", "**/setup*", "**/bootstrap*", "**/Install*",
                        "docs/**/*spec*.md", "**/*.pem", "**/.env", "**/.env.*"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, globs)

    def test_the_env_globs_exclude_the_tracked_templates(self):
        # AGENTS.md rule 1: a tracked `.env.example` is the template and is
        # supposed to be committed. Only the exclusion keeps an honest template
        # edit out of the high-risk class — and keeps the rule believable enough
        # to survive its first false positive.
        by_pattern = {r["pattern"]: r for r in
                      real_registry()["policy"]["risk_rules"]
                      if r["type"] == "path_glob"}
        for pattern in ("**/.env.*",):
            with self.subTest(pattern=pattern):
                self.assertEqual(by_pattern[pattern].get("exclude"),
                                 "**/*.example")

    def test_the_secret_content_shapes_are_declared(self):
        # Finding 2: five high-confidence shapes, each its own added_regex rule,
        # all with the same reason so a reviewer reads one class of finding.
        # Written as the registry's own text, never as a key.
        patterns = {r.get("pattern") for r in
                    real_registry()["policy"]["risk_rules"]
                    if r["type"] == "added_regex"}
        pem = "-----BEGIN" + " [A-Z ]*PRIVATE KEY-----"
        for shape in (pem, "AKIA" + "[0-9A-Z]{16}", "ghp_" + "[A-Za-z0-9]{36}",
                      "sk-" + "[A-Za-z0-9_-]{20,}", "xox" + "[baprs]-"):
            with self.subTest(shape=shape[:12]):
                self.assertIn(shape, patterns)
        for rule in real_registry()["policy"]["risk_rules"]:
            if rule["type"] == "added_regex" and rule["reason"] == "secret content":
                self.assertNotIn("paths", rule,
                                 "a secret in any file is a secret; scoping the "
                                 "content rules to a path glob is the bug being fixed")

    def test_the_deletion_rule_carries_a_line_threshold(self):
        # Finding 6: the generic deletion rule says "more than 200 lines, or a
        # deleted file" — the second half is the file-level deletion it already
        # read, the first is the numstat it now reads.
        deletion = [r for r in real_registry()["policy"]["risk_rules"]
                    if r["type"] == "diff_deletion"]
        self.assertEqual(len(deletion), 1, deletion)
        self.assertEqual(deletion[0]["min_deleted_lines"], 200)

    def test_the_new_rules_classify_the_paths_they_name(self):
        reg = real_registry()
        for path in (".agents/skills/x/SKILL.md", "AGENTS.md", "CLAUDE.md",
                     "tools/autoos_agent_mcp.py",
                     "catalog/ai-registry.schema.json",
                     "docs/agent-protocol.md", "tools/autoos_redact.py",
                     "graph/seed.pg", "scripts/install-foo.sh",
                     "configuration/keys/server.key",
                     "configuration/keys/db-secret.txt",
                     "Windows/ansible/site.yml", "Linux/install_bar.ps1",
                     "infra/db/001.sql",
                     "setup.py", "infra/mcp-servers/cao-setup/setup_cao.py",
                     "scripts/bootstrap.sh", "Windows/powershell/Install-Tools.ps1",
                     "docs/routing/spec-v2.md",
                     "infra/tls/server.pem",
                     ".env", "configuration/litellm/.env",
                     "configuration/litellm/.env.local"):
            with self.subTest(path=path):
                out = risk.classify(files(path), {}, reg)
                self.assertEqual(out["risk"], "high", out["reasons"])
                self.assertTrue(out["reasons"])

    def test_two_rules_on_one_path_are_one_line_per_reason(self):
        # A migration's .sql file is named by two `data migrations` rules, and the
        # reason line is deduplicated: a reviewer reading the same line twice learns
        # nothing new. Two rules of DIFFERENT reasons do print twice — a setup script
        # under migrations/ is a data migration and an installer, and the class is
        # high for both.
        same = risk.classify(files("infra/migrations/001.sql"), {}, real_registry())
        self.assertEqual(same["reasons"], ["data migrations: infra/migrations/001.sql"])
        both = risk.classify(files("infra/migrations/setup_001.sql"), {},
                             real_registry())
        self.assertEqual(both["risk"], "high")
        self.assertEqual(both["reasons"], ["data migrations: infra/migrations/setup_001.sql",
                                           "installer: infra/migrations/setup_001.sql"])

    def test_the_templates_and_the_ordinary_tree_stay_normal(self):
        # The one failure mode that matters is a silent `normal`, so the real
        # danger of a widening rule table is the over-broad glob that makes
        # everything high and gets the rules switched off. These paths are what a
        # lane normally touches; each must still read `normal`.
        reg = real_registry()
        for path in (".env.example", "configuration/litellm/.env.example",
                     "infra/mcp-servers/.env.server.example",
                     "catalog/llm-models.json", "README.md",
                     "docs/handoff/notes.md", "tests/test_autoos_risk.py",
                     "infra/mcp-servers/servers/x/src/index.ts",
                     "catalog/ai-registry.json",
                     "configuration/docker/ai-stack/stack.env.example"):
            with self.subTest(path=path):
                out = risk.classify(files(path), {}, reg)
                self.assertEqual(out["risk"], "normal", out["reasons"])

    def test_a_secret_shape_in_an_ordinary_path_is_high(self):
        # The point of finding 2: the path never says `secret`, the line does.
        reg = real_registry()
        for kind in ("pem", "aws", "github", "openai", "slack"):
            with self.subTest(shape=kind):
                out = risk.classify(files("lib/linux/detect-helpers.sh"),
                                    {"lib/linux/detect-helpers.sh":
                                     ["export TOKEN=" + secret_shape(kind)]}, reg)
                self.assertEqual(out["risk"], "high", out["reasons"])
                self.assertIn("secret content: lib/linux/detect-helpers.sh",
                              out["reasons"])

    def test_a_fake_placeholder_in_a_comment_is_not_flagged_as_a_secret(self):
        # The rules are high-confidence shapes, not the word "token": a tracked
        # placeholder must not raise, or half the catalog lands in high risk and
        # the class stops meaning anything.
        reg = real_registry()
        out = risk.classify(files("docs/notes.md"),
                            {"docs/notes.md": ["# set AKIA_YOUR_KEY_HERE and "
                                               "ghp_ or sk- prefixes",
                                               "token = <your-token>"]}, reg)
        self.assertEqual(out["risk"], "normal", out["reasons"])

    def test_the_sudo_rule_is_declared_and_textual(self):
        reg = real_registry()
        out = risk.classify(files("lib/linux/install-helpers.sh"),
                            {"lib/linux/install-helpers.sh": ["  sudo chown x"]}, reg)
        self.assertEqual(out["risk"], "high")
        self.assertIn("root/sudo (R-orch-10): lib/linux/install-helpers.sh",
                      out["reasons"])

    def test_an_ordinary_source_change_is_normal(self):
        out = risk.classify(files("catalog/llm-models.json"), {}, real_registry())
        self.assertEqual(out["risk"], "normal", out["reasons"])

    def test_audit_percent_and_review_counts_are_declared(self):
        policy = real_registry()["policy"]
        self.assertEqual(policy["risk_audit_percent"]["value"], 20)
        # `source` is the one field rule 5 exempts, so the operator's dated
        # stamp is allowed here and is NOT asserted to be date-free.
        self.assertTrue(policy["risk_audit_percent"]["source"])
        counts = policy["review_counts"]
        self.assertEqual(
            {risk_class: {field: counts[risk_class][field]
                          for field in ("cross_family", "final")}
             for risk_class in ("normal", "high")},
            {"normal": {"cross_family": 2, "final": False},
             "high": {"cross_family": 2, "final": True}})
        for risk_class in ("normal", "high"):
            self.assertTrue(counts[risk_class]["source"])

    def test_high_pays_the_normal_reviews_plus_the_final(self):
        # Finding 3 (operator Q-013, common.md): high risk is not normal risk with
        # one eye removed. It is the same two diverse cheap cross-family reviews
        # PLUS the Sonnet final — the 1 + final the RISKTIER-a registry declared
        # reviewed a dangerous change less widely than a routine one.
        counts = real_registry()["policy"]["review_counts"]
        self.assertEqual(counts["high"]["cross_family"],
                         counts["normal"]["cross_family"])
        self.assertTrue(counts["high"]["final"])
        self.assertIn("Q-013/D-060", counts["high"]["source"])
        self.assertIn("(common.md)", counts["high"]["source"])


class RiskRuleShapeTests(unittest.TestCase):
    """tools/registry.py validate: unknown type / bad percent / bad counts."""

    def setUp(self):
        import registry as registry_mod
        self.registry_mod = registry_mod
        self.real = real_registry()

    def problems_for(self, mutate):
        reg = json.loads(json.dumps(self.real))
        mutate(reg["policy"])
        return [p for p in self.registry_mod.check_registry(reg)
                if p.startswith("risk_rules:") or p.startswith("risk_audit_percent:")
                or p.startswith("review_counts:")]

    def test_the_committed_registry_reports_no_risk_problems(self):
        self.assertEqual(self.problems_for(lambda p: None), [])

    def test_an_unknown_rule_type_is_a_validation_error(self):
        problems = self.problems_for(
            lambda p: p["risk_rules"].append(rule("mime_type", pattern="x",
                                                  reason="nonsense")))
        self.assertTrue(any("mime_type" in x for x in problems), problems)

    def test_an_added_regex_rule_without_a_pattern_is_an_error(self):
        problems = self.problems_for(
            lambda p: p["risk_rules"].append(rule("added_regex", reason="broken")))
        self.assertTrue(problems, problems)

    def test_a_path_glob_rule_without_a_pattern_is_an_error(self):
        problems = self.problems_for(
            lambda p: p["risk_rules"].append(rule("path_glob",
                                                  reason="no pattern")))
        self.assertTrue(problems, problems)

    def test_an_uncompilable_added_regex_is_an_error(self):
        problems = self.problems_for(
            lambda p: p["risk_rules"].append(rule("added_regex", pattern=r"\b(",
                                                  reason="broken")))
        self.assertTrue(problems, problems)

    def test_a_percent_outside_zero_to_hundred_is_an_error(self):
        for value in (-1, 101, "20"):
            with self.subTest(value=value):
                problems = self.problems_for(
                    lambda p, v=value: p["risk_audit_percent"].update({"value": v}))
                self.assertTrue(problems, problems)

    def test_a_missing_or_malformed_audit_percent_is_an_error(self):
        problems = self.problems_for(lambda p: p.pop("risk_audit_percent"))
        self.assertTrue(problems, problems)

    def test_negative_review_counts_are_an_error(self):
        problems = self.problems_for(
            lambda p: p["review_counts"]["normal"].update({"cross_family": -1}))
        self.assertTrue(any("review_counts" in x for x in problems), problems)

    def test_a_non_boolean_final_is_an_error(self):
        problems = self.problems_for(
            lambda p: p["review_counts"]["high"].update({"final": "yes"}))
        self.assertTrue(any("review_counts" in x for x in problems), problems)

    def test_an_unknown_risk_class_is_an_error(self):
        problems = self.problems_for(
            lambda p: p["review_counts"].update({"extreme": {"cross_family": 3,
                                                             "final": True,
                                                             "source": "test"}}))
        self.assertTrue(problems, problems)

    def test_zero_counts_are_allowed(self):
        problems = self.problems_for(
            lambda p: p["review_counts"]["normal"].update({"cross_family": 0}))
        self.assertEqual(problems, [])

    def test_a_field_the_rule_type_never_reads_is_an_error(self):
        # `paths` scopes added_regex only, and only path_glob/added_regex have a
        # pattern at all. An author who writes `paths` on a path_glob believes
        # they scoped it and they did not — the same "data with no reader" defect
        # this lane exists to close, one rule field at a time. classify() ignores
        # the field; validate is what says so out loud.
        inert = [
            rule("path_glob", pattern="a.md", paths="**/*.sh", reason="scoped"),
            rule("diff_deletion", pattern="a.md", reason="glob-less"),
            rule("registry_policy", pattern="x", reason="already whole-repo"),
            rule("diff_deletion", reason="deletion paths", weight=3),
            # RISKTIER-a2: the two fields the fix adds, on the wrong types.
            rule("path_glob", pattern="a.md", min_deleted_lines=200,
                 reason="no numstat here"),
            rule("added_regex", pattern="x", exclude="**/*.example",
                 reason="scoped wrong way"),
            rule("registry_policy", reason="routing policy",
                 exclude="**/*.example"),
        ]
        for bad in inert:
            with self.subTest(rule_type=bad["type"],
                              keys=sorted(set(bad) - {"type", "reason", "source"})):
                problems = self.problems_for(
                    lambda p, r=bad: p["risk_rules"].append(r))
                self.assertTrue(problems, problems)

    def test_a_legal_rule_shape_reports_nothing(self):
        problems = self.problems_for(
            lambda p: p["risk_rules"].append(
                rule("added_regex", pattern="x", paths="**/*.sh", reason="ok")))
        self.assertEqual(problems, [])

    def test_an_excluded_glob_and_a_deletion_threshold_are_legal_fields(self):
        # `exclude` is read by path_glob (the `.env.example` template) and
        # `min_deleted_lines` by diff_deletion (the 200-line volume rule), so
        # validate must know both — a rule validate refuses is a rule that never
        # reaches the committed registry, and a silent `normal` is what replaces it.
        for good in (rule("path_glob", pattern="**/.env.*",
                          exclude="**/*.example", reason="secrets"),
                     rule("diff_deletion", reason="deletion paths",
                          min_deleted_lines=200)):
            with self.subTest(rule_type=good["type"]):
                problems = self.problems_for(
                    lambda p, r=good: p["risk_rules"].append(r))
                self.assertEqual(problems, [])

    def test_a_malformed_excluded_glob_is_an_error(self):
        for bad_exclude in ("", 7, ["a"]):
            with self.subTest(exclude=bad_exclude):
                problems = self.problems_for(
                    lambda p, v=bad_exclude: p["risk_rules"].append(
                        rule("path_glob", pattern="a.md", exclude=v,
                             reason="secrets")))
                self.assertTrue(problems, problems)

    def test_a_malformed_deletion_threshold_is_an_error(self):
        for bad_value in ("200", -1, 1.5, True):
            with self.subTest(value=bad_value):
                problems = self.problems_for(
                    lambda p, v=bad_value: p["risk_rules"].append(
                        rule("diff_deletion", reason="deletion paths",
                             min_deleted_lines=v)))
                self.assertTrue(problems, problems)


# ── the `risk` verb ────────────────────────────────────────────────────────

class CliTests(_Repo):
    def invoke(self, extra):
        return subprocess.run([sys.executable, str(AGENT), "risk"] + extra,
                              capture_output=True, text=True, cwd=str(ROOT),
                              stdin=subprocess.DEVNULL)

    def test_a_high_risk_commit_prints_its_reasons_and_exits_zero(self):
        sha = self.write_and_commit({"AGENTS.md": "# policy\n"})
        proc = self.invoke(["--repo", self.tmp, "--base", self.base, "--sha", sha])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("risk: high", proc.stdout)
        self.assertIn("  reason: policy: AGENTS.md", proc.stdout)
        self.assertRegex(proc.stdout, r"audit: (yes|no) \(\d+%\)")

    def test_json_output_carries_the_whole_assessment(self):
        sha = self.write_and_commit({"docs/handoff/notes.md": "note\n"})
        proc = self.invoke(["--repo", self.tmp, "--base", self.base, "--sha", sha,
                            "--json"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["risk"], "normal")
        self.assertIn("audit", doc)
        self.assertEqual([f["path"] for f in doc["files"]],
                         ["docs/handoff/notes.md"])

    def test_a_git_failure_is_exit_two_on_stderr(self):
        proc = self.invoke(["--repo", self.tmp, "--base", self.base,
                            "--sha", "0" * 40])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("risk:", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_an_unpushed_head_and_a_short_rev_are_classifiable(self):
        # Finding 4 at the verb: a lane asks about the commit it just made, by
        # name, and got exit 2 out of a diff that classified perfectly.
        sha = self.write_and_commit({"AGENTS.md": "# policy\n"})
        for rev in ("HEAD", sha[:8], "HEAD~0"):
            with self.subTest(rev=rev):
                proc = self.invoke(["--repo", self.tmp, "--base", self.base,
                                    "--sha", rev, "--json"])
                self.assertEqual(proc.returncode, 0, proc.stderr)
                doc = json.loads(proc.stdout)
                self.assertEqual(doc["sha"], sha)
                self.assertEqual(doc["risk"], "high")

    def test_a_classified_commit_prints_the_commit_it_resolved_to(self):
        sha = self.write_and_commit({"AGENTS.md": "# policy\n"})
        proc = self.invoke(["--repo", self.tmp, "--base", self.base,
                            "--sha", sha[:8]])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("risk: high", proc.stdout)
        self.assertIn("commit: %s" % sha, proc.stdout)

    def test_the_verb_is_registered_and_defaults_to_origin_main(self):
        agent = load_agent()
        self.assertIn("risk", agent.VERB_PARSERS)
        self.assertIn("risk", agent.VERB_HANDLERS)
        ap = agent.argparse.ArgumentParser()
        agent._parser_risk(ap.add_subparsers(dest="cmd"))
        args = ap.parse_args(["risk", "--sha", "abc123"])
        self.assertEqual(args.base, "origin/main")
        self.assertEqual(args.repo, ".")


if __name__ == "__main__":
    unittest.main(verbosity=2)
