"""Tests for lib/agent_harness.py: the single agent-harness generator.

Run from the repo root:

    python3 tests/test_agent_harness.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "lib" / "agent_harness.py"
HARNESS = ROOT / "catalog" / "agent-harness.json"
FIXTURES = ROOT / "tests" / "fixtures" / "agent-harness"
REPO_ROOT = "/fake/autoos"
SKILLS_SOURCE = "/fake/skills"


def load_module():
    spec = importlib.util.spec_from_file_location("agent_harness", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(MODULE), *args], capture_output=True, text=True
    )


def read_ordered(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=OrderedDict)


def harness_data():
    with open(HARNESS, encoding="utf-8") as handle:
        return json.load(handle)


class CheckTests(unittest.TestCase):
    def test_check_passes_on_the_real_harness(self):
        result = run_cli("check", "--harness", str(HARNESS))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent-harness: ok", result.stdout)

    def test_check_fails_when_a_leaf_role_can_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = harness_data()
            data["roles"]["leaf-reviewer"]["spawn"] = True
            path = Path(tmp) / "harness.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = run_cli("check", "--harness", str(path))
            self.assertEqual(result.returncode, 1)
            self.assertIn("leaf-reviewer", result.stdout)
            self.assertNotIn("agent-harness: ok", result.stdout)

    def test_check_requires_the_bash_allow_all_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = harness_data()
            del data["fences"]["bash_allow_all"]
            path = Path(tmp) / "harness.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = run_cli("check", "--harness", str(path))
            self.assertEqual(result.returncode, 1)
            self.assertIn("fences.bash_allow_all", result.stdout)


class PinTests(unittest.TestCase):
    def test_pin_serena_prints_the_pinned_package(self):
        result = run_cli("pin", "serena", "--harness", str(HARNESS))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "serena-agent==1.7.0")

    def test_pin_unknown_server_fails(self):
        result = run_cli("pin", "not-a-server", "--harness", str(HARNESS))
        self.assertEqual(result.returncode, 1)

    def test_serena_excluded_lists_the_tools(self):
        result = run_cli("serena-excluded", "--harness", str(HARNESS))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertIn("read_file", lines)
        self.assertEqual(len(lines), len(harness_data()["mcp_servers"]["serena"]["excluded_tools"]))


class OpencodeMergeTests(unittest.TestCase):
    def merge_fixture(self, tmp):
        config = Path(tmp) / "opencode.json"
        shutil.copyfile(FIXTURES / "opencode.user.json", config)
        result = run_cli(
            "opencode",
            "--harness", str(HARNESS),
            "--config", str(config),
            "--repo-root", REPO_ROOT,
            "--skills-source", SKILLS_SOURCE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return config, result

    def test_fixture_merges_to_the_expected_document_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, _ = self.merge_fixture(tmp)
            actual = read_ordered(config)
            expected = read_ordered(FIXTURES / "opencode.expected.json")
            self.assertEqual(actual, expected)

    def test_every_global_fence_pattern_gets_its_verdict(self):
        harness = harness_data()
        with tempfile.TemporaryDirectory() as tmp:
            config, _ = self.merge_fixture(tmp)
            bash = read_ordered(config)["permission"]["bash"]
            for pattern in harness["fences"]["bash_deny_all"] + harness["fences"]["bash_deny_leaf"]:
                self.assertEqual(bash[pattern], "deny", pattern)
            for pattern in harness["fences"]["bash_allow_all"]:
                self.assertEqual(bash[pattern], "allow", pattern)

    def test_a_leaf_cannot_commit_or_push_and_cannot_spawn(self):
        harness = harness_data()
        with tempfile.TemporaryDirectory() as tmp:
            config, _ = self.merge_fixture(tmp)
            doc = read_ordered(config)
            leaves = [name for name, role in harness["roles"].items() if role["leaf"]]
            self.assertTrue(leaves)
            for name in leaves:
                block = doc["agent"][name]
                self.assertEqual(block["permission"]["task"], "deny", name)
                self.assertEqual(block["permission"]["bash"]["git commit*"], "deny", name)
                self.assertEqual(block["permission"]["bash"]["git push*"], "deny", name)

    def test_only_spawning_roles_get_task_allow(self):
        harness = harness_data()
        with tempfile.TemporaryDirectory() as tmp:
            config, _ = self.merge_fixture(tmp)
            doc = read_ordered(config)
            for name, role in harness["roles"].items():
                expected = "allow" if role["spawn"] else "deny"
                self.assertEqual(doc["agent"][name]["permission"]["task"], expected, name)

    def test_user_keys_survive_but_the_fence_beats_the_user_git_push_allow(self):
        user = read_ordered(FIXTURES / "opencode.user.json")
        with tempfile.TemporaryDirectory() as tmp:
            config, _ = self.merge_fixture(tmp)
            doc = read_ordered(config)
            for key in ("provider", "model", "mcp"):
                self.assertEqual(
                    json.dumps(doc[key], sort_keys=True),
                    json.dumps(user[key], sort_keys=True),
                    key,
                )
            self.assertEqual(
                json.dumps(doc["agent"]["build"], sort_keys=True),
                json.dumps(user["agent"]["build"], sort_keys=True),
            )
            self.assertIn("my-rules.md", doc["instructions"])
            self.assertEqual(doc["permission"]["bash"]["npm *"], "ask")
            self.assertEqual(doc["permission"]["bash"]["git push*"], "deny")

    def test_second_run_skips_and_makes_no_second_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, _ = self.merge_fixture(tmp)
            before = config.read_bytes()
            backups = sorted(Path(tmp).glob("opencode.json.autoos-backup-*"))
            self.assertEqual(len(backups), 1)
            result = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", SKILLS_SOURCE,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("skipped", result.stdout)
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(
                sorted(Path(tmp).glob("opencode.json.autoos-backup-*")), backups
            )

    def test_dry_run_on_a_missing_file_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "opencode.json"
            result = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(target),
                "--repo-root", REPO_ROOT,
                "--skills-source", SKILLS_SOURCE,
                "--dry-run",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("would install", result.stdout)
            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())

    def test_dry_run_leaves_an_existing_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "opencode.json"
            shutil.copyfile(FIXTURES / "opencode.user.json", config)
            before = config.read_bytes()
            result = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", SKILLS_SOURCE,
                "--dry-run",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("would update", result.stdout)
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(list(Path(tmp).glob("*.autoos-backup-*")), [])

    def test_a_file_with_comments_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "opencode.json"
            original = '{\n  // a user comment\n  "model": "x/y"\n}\n'
            config.write_text(original, encoding="utf-8")
            before = config.read_bytes()
            result = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", SKILLS_SOURCE,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("left alone", result.stdout)
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(list(Path(tmp).glob("*.autoos-backup-*")), [])

    def test_a_string_permission_bash_counts_as_star(self):
        module = load_module()
        harness = harness_data()
        user = {"permission": {"bash": "allow"}}
        doc = module.desired_opencode(user, harness, REPO_ROOT, SKILLS_SOURCE)
        self.assertEqual(doc["permission"]["bash"]["*"], "allow")

    def test_an_unexpected_instructions_type_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "opencode.json"
            original = '{"instructions": "not-a-list"}\n'
            config.write_text(original, encoding="utf-8")
            before = config.read_bytes()
            result = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", SKILLS_SOURCE,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("left alone", result.stdout)
            self.assertEqual(config.read_bytes(), before)


class OpencodeOrderAndEncodingTests(unittest.TestCase):
    def run_on(self, config):
        return run_cli(
            "opencode", "--harness", str(HARNESS), "--config", str(config),
            "--repo-root", REPO_ROOT, "--skills-source", SKILLS_SOURCE,
        )

    def test_a_reordered_rule_list_is_a_change_not_a_skip(self):
        # OpenCode applies the last matching rule, so order is meaning.
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "opencode.json"
            shutil.copyfile(FIXTURES / "opencode.expected.json", config)
            doc = read_ordered(config)
            bash = doc["permission"]["bash"]
            star = bash.pop("*")
            bash["*"] = star  # "*": allow now comes last and overrides every deny
            config.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            result = self.run_on(config)
            self.assertIn("updated", result.stdout)
            self.assertEqual(list(read_ordered(config)["permission"]["bash"])[0], "*")

    def test_a_config_with_a_utf8_bom_is_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "opencode.json"
            config.write_bytes(b"\xef\xbb\xbf" + (FIXTURES / "opencode.user.json").read_bytes())
            result = self.run_on(config)
            self.assertIn("updated", result.stdout, result.stdout)


class SkillsLinkTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "creating symlinks needs POSIX")
    def test_link_is_created_then_a_second_run_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills-source"
            source.mkdir()
            config = Path(tmp) / "opencode.json"
            shutil.copyfile(FIXTURES / "opencode.user.json", config)
            first = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", str(source),
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            link = Path(tmp) / "skills"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), source.resolve())
            second = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", str(source),
            )
            self.assertIn("skipped", second.stdout)

    @unittest.skipUnless(os.name == "posix", "creating symlinks needs POSIX")
    def test_link_to_another_target_is_repointed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills-source"
            source.mkdir()
            wrong = Path(tmp) / "wrong"
            wrong.mkdir()
            link = Path(tmp) / "skills"
            link.symlink_to(wrong)
            config = Path(tmp) / "opencode.json"
            shutil.copyfile(FIXTURES / "opencode.user.json", config)
            result = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", str(source),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(link.resolve(), source.resolve())

    def test_a_real_skills_directory_is_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills-source"
            source.mkdir()
            link = Path(tmp) / "skills"
            link.mkdir()
            marker = link / "keep.txt"
            marker.write_text("mine", encoding="utf-8")
            config = Path(tmp) / "opencode.json"
            shutil.copyfile(FIXTURES / "opencode.user.json", config)
            result = run_cli(
                "opencode",
                "--harness", str(HARNESS),
                "--config", str(config),
                "--repo-root", REPO_ROOT,
                "--skills-source", str(source),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("skills: left alone (not a link)", result.stdout)
            self.assertTrue(link.is_dir() and not link.is_symlink())
            self.assertEqual(marker.read_text(encoding="utf-8"), "mine")


class RenderProfileTests(unittest.TestCase):
    def render(self, name, base, contract_ref="docs/agents/leaf-contract.md"):
        module = load_module()
        role = harness_data()["roles"][name]
        return module.render_profile(name, role, base, contract_ref)

    def test_managed_keys_are_set_from_the_role_and_user_keys_kept(self):
        role = harness_data()["roles"]["orchestrator"]
        profile = self.render("orchestrator", {"custom": 1})
        self.assertEqual(profile["name"], role["openhands"]["profile"])
        self.assertEqual(profile["llm_profile_ref"], role["openhands"]["llm_profile_ref"])
        self.assertEqual(profile["enable_sub_agents"], role["spawn"])
        self.assertEqual(profile["mcp_server_refs"], role["mcp"])
        self.assertEqual(profile["custom"], 1)

    def test_base_key_order_is_kept(self):
        base = OrderedDict([("first", 1), ("name", "old"), ("last", 2)])
        profile = self.render("leaf-implementer", base)
        self.assertEqual(list(profile)[:3], ["first", "name", "last"])

    def test_a_leaf_suffix_names_the_contract_and_forbids_spawning(self):
        profile = self.render("leaf-reviewer", {})
        suffix = profile["system_message_suffix"]
        self.assertTrue(
            suffix.startswith(
                "Follow the AGENTS.md of the repository you work in and the coding-principles skill."
            )
        )
        self.assertIn(
            " You are a leaf: follow the leaf contract at docs/agents/leaf-contract.md.",
            suffix,
        )
        self.assertNotIn("sub-agents", suffix)

    def test_a_spawning_suffix_names_the_contract_and_allows_sub_agents(self):
        profile = self.render("orchestrator", {}, "/repo/docs/agents/leaf-contract.md")
        suffix = profile["system_message_suffix"]
        self.assertIn(
            " You may start sub-agents: brief each one as a leaf under the leaf contract at "
            "/repo/docs/agents/leaf-contract.md, then judge and commit its edits yourself.",
            suffix,
        )
        self.assertNotIn("You are a leaf", suffix)


class VendoredProfileTests(unittest.TestCase):
    PROFILES = ("orchestrator", "suborchestrator", "implementer", "worker")
    LEAVES = ("implementer", "worker")

    def load(self, name):
        path = ROOT / "openhands" / "agent-profiles" / (name + ".json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_spawning_profiles_enable_sub_agents_and_leaves_do_not(self):
        for name in ("orchestrator", "suborchestrator"):
            self.assertTrue(self.load(name)["enable_sub_agents"], name)
        for name in self.LEAVES:
            self.assertFalse(self.load(name)["enable_sub_agents"], name)

    def test_the_reviewer_profile_uses_the_deepseek_model(self):
        self.assertEqual(self.load("worker")["llm_profile_ref"], "deepseek-v4-flash")

    def test_every_profile_names_agents_md_and_the_leaf_contract(self):
        for name in self.PROFILES:
            suffix = self.load(name)["system_message_suffix"]
            self.assertIn("AGENTS.md", suffix, name)
            self.assertIn("leaf-contract.md", suffix, name)

    def test_leaf_profiles_do_not_get_omnigraph(self):
        for name in self.LEAVES:
            self.assertNotIn("omnigraph", self.load(name)["mcp_server_refs"], name)


class VendorTests(unittest.TestCase):
    def test_vendor_check_passes_on_the_repository(self):
        result = run_cli(
            "vendor", "--harness", str(HARNESS), "--repo-root", str(ROOT), "--check"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent-harness vendor: ok", result.stdout)

    def test_vendor_check_names_a_drifted_role_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = Path(tmp) / "openhands" / "agent-profiles"
            shutil.copytree(ROOT / "openhands" / "agent-profiles", profiles)
            drifted = profiles / "worker.json"
            doc = json.loads(drifted.read_text(encoding="utf-8"))
            doc["enable_sub_agents"] = True
            drifted.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            result = run_cli(
                "vendor", "--harness", str(HARNESS), "--repo-root", tmp, "--check"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("worker", result.stdout)


class OpenhandsTests(unittest.TestCase):
    def run_openhands(self, openhands_dir, *extra):
        return run_cli(
            "openhands",
            "--harness", str(HARNESS),
            "--openhands-dir", str(openhands_dir),
            "--repo-root", str(ROOT),
            *extra,
        )

    def test_settings_merge_keeps_every_other_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            openhands_dir = Path(tmp)
            settings = {
                "schema_version": 2,
                "agent_settings": {
                    "enable_sub_agents": False,
                    "llm": {"model": "m"},
                    "mcp_config": {"serena": {"command": "uvx"}},
                },
                "user_key": [1, 2],
            }
            (openhands_dir / "settings.json").write_text(
                json.dumps(settings), encoding="utf-8"
            )
            result = self.run_openhands(openhands_dir)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            doc = json.loads(
                (openhands_dir / "settings.json").read_text(encoding="utf-8")
            )
            self.assertTrue(doc["agent_settings"]["enable_sub_agents"])
            self.assertEqual(doc["schema_version"], 2)
            self.assertEqual(doc["agent_settings"]["llm"], {"model": "m"})
            self.assertEqual(
                doc["agent_settings"]["mcp_config"], {"serena": {"command": "uvx"}}
            )
            self.assertEqual(doc["user_key"], [1, 2])

    def test_an_installed_profile_keeps_a_user_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            openhands_dir = Path(tmp)
            profiles = openhands_dir / "agent-profiles"
            profiles.mkdir()
            (profiles / "worker.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "id": "installed",
                        "condenser": {"max_size": 99},
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_openhands(openhands_dir)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            doc = json.loads((profiles / "worker.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["condenser"], {"max_size": 99})

    def test_second_run_only_skips_and_changes_no_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            openhands_dir = Path(tmp)
            first = self.run_openhands(openhands_dir)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            before = {
                path.relative_to(openhands_dir).as_posix(): path.read_bytes()
                for path in openhands_dir.rglob("*")
                if path.is_file()
            }
            second = self.run_openhands(openhands_dir)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            lines = [line for line in second.stdout.splitlines() if line.strip()]
            self.assertTrue(lines)
            for line in lines:
                self.assertIn("skipped", line, line)
            after = {
                path.relative_to(openhands_dir).as_posix(): path.read_bytes()
                for path in openhands_dir.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_a_reformatted_but_equal_install_is_skipped(self):
        # The installer's embedded script rewrites settings.json with its own
        # formatting (indent=2, no final newline) right before the harness runs;
        # equal content must still read as skipped, with no write and no backup.
        with tempfile.TemporaryDirectory() as tmp:
            openhands_dir = Path(tmp)
            self.assertEqual(self.run_openhands(openhands_dir).returncode, 0)
            for path in openhands_dir.rglob("*.json"):
                path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))),
                                encoding="utf-8")
            before = {p.name: p.read_bytes() for p in openhands_dir.rglob("*") if p.is_file()}
            second = self.run_openhands(openhands_dir)
            for line in [l for l in second.stdout.splitlines() if l.strip()]:
                self.assertIn("skipped", line, line)
            after = {p.name: p.read_bytes() for p in openhands_dir.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_dry_run_on_an_empty_dir_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            openhands_dir = Path(tmp) / "openhands"
            result = self.run_openhands(openhands_dir, "--dry-run")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("would install", result.stdout)
            self.assertFalse(openhands_dir.exists())


if __name__ == "__main__":
    unittest.main()
