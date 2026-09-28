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


def files(*paths, deleted=()):
    """The changed_files() shape: {"path", "deleted"} dicts, in diff order."""
    out = [{"path": p, "deleted": False} for p in paths]
    out.extend({"path": p, "deleted": True} for p in deleted)
    return out


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


# ── changed_files / added_lines: the git plumbing ──────────────────────────

class GitParsingTests(unittest.TestCase):
    def test_name_status_rows_become_paths_with_deletions_flagged(self):
        seen = {}

        def runner(args, cwd):
            seen["args"] = args
            seen["cwd"] = cwd
            if args[0] == "merge-base":
                return 0, "mb-sha\n"
            return 0, "M\tlib/a.sh\nD\told.md\nA\tnew.md\n"

        out = risk.changed_files("/repo", "base-sha", "head-sha", runner)
        self.assertEqual(out, [{"path": "lib/a.sh", "deleted": False},
                               {"path": "old.md", "deleted": True},
                               {"path": "new.md", "deleted": False}])
        self.assertEqual(seen["args"][:2], ["diff", "--name-status"])
        # the diff runs from the merge base, not from `base` itself
        self.assertEqual(seen["args"], ["diff", "--name-status", "mb-sha", "head-sha"])
        self.assertEqual(seen["cwd"], "/repo")

    def test_merge_base_is_asked_for_before_the_diff(self):
        calls = []

        def runner(args, cwd):
            calls.append(args)
            return 0, ("mb-sha\n" if args[0] == "merge-base" else "")

        risk.changed_files("/repo", "origin/main", "head-sha", runner)
        self.assertEqual(calls[0][:2], ["merge-base", "origin/main"])
        self.assertEqual(calls[1][-2:], ["mb-sha", "head-sha"])

    def test_a_rename_reports_its_new_path_and_is_not_a_deletion(self):
        def runner(args, cwd):
            return 0, "R100\told/name.py\tnew/name.py\n"

        self.assertEqual(risk.changed_files("/repo", "b", "s", runner),
                         [{"path": "new/name.py", "deleted": False}])

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

    def test_the_new_rules_classify_the_paths_they_name(self):
        reg = real_registry()
        for path in (".agents/skills/x/SKILL.md", "AGENTS.md", "CLAUDE.md",
                     "docs/plans/2026-09-25-routing-v2-spec.md",
                     "tools/autoos_agent_mcp.py",
                     "catalog/ai-registry.schema.json",
                     "docs/agent-protocol.md", "tools/autoos_redact.py",
                     "infra/migrations/001.sql", "graph/seed.pg",
                     "configuration/keys/server.key",
                     "configuration/keys/db-secret.txt",
                     "Windows/ansible/site.yml", "scripts/install-foo.sh",
                     "Linux/install_bar.ps1"):
            with self.subTest(path=path):
                out = risk.classify(files(path), {}, reg)
                self.assertEqual(out["risk"], "high")
                self.assertEqual(len(out["reasons"]), 1, out["reasons"])

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
             "high": {"cross_family": 1, "final": True}})
        for risk_class in ("normal", "high"):
            self.assertTrue(counts[risk_class]["source"])


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
