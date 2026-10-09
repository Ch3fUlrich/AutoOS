#!/usr/bin/env python3
"""Tests for tools/autoos_writer_rule.py + policy.writers (AO-WRITER-GUARDS P1).

Fixtures are inline dicts plus the committed registry. Nothing is spawned.
Run from the repo root: python3 tests/test_autoos_writer_rule.py [ClassName]
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_routing as routing  # noqa: E402
import autoos_writer_ledger as ledger  # noqa: E402
import autoos_writer_rule as rule  # noqa: E402
import registry as registry_mod  # noqa: E402
NEMOTRON = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
QWEN = "qoder/qwen3.8-max"
VERTEX = "vertex/gemini-3.8-flash"
QODER = {"client": "qoder", "model": "qwen3.8-max", "available": False,
         "reason": "out of credits"}
VERTEX_LEG = {"client": "vertex", "model": "gemini-3.8-flash", "effort": "high"}
SUBS = ["nemotron-3-super", "laguna", "north-mini-code", "gpt-oss"]


def reg():
    return {"policy": {"writers": {"sub40_models": SUBS, "R2": [QODER, VERTEX_LEG],
                                   "R3": [VERTEX_LEG]}}}


class RequiredLevelTests(unittest.TestCase):
    def test_the_level_table(self):
        cases = [  # (task_type, paths, diff_text, expected)
            ("docs", ["README.md"], "", "R0"),
            ("code", ["src/main.py"], "", "R1"),
            ("code", ["src/author.py"], "", "R1"),  # author is not auth
            ("code", ["docs/authority.md"], "", "R1"),  # authority; floor holds
            ("code", ["docs/authorize-docs.md"], "", "R1"),  # authorize is not auth
            ("docs", ["playbooks/x.yml"], "", "R2"),  # mislabelled docs card
            ("code", ["docker-compose.yml"], "", "R2"),
            ("ops", ["README.md"], "", "R2"),  # explicit type only raises
            ("code", ["src/auth/login.py"], "", "R3"),
            ("code", ["a.py"], "+if authn(u):\n", "R3"),  # auth diff
            ("code", ["a.py"], "+ acl x, secrets y\n", "R3"),
            ("code", ["n.md"], "+written by the author\n", "R1"),
        ]
        for task_type, paths, diff, want in cases:
            with self.subTest(task_type=task_type, paths=paths):
                self.assertEqual(rule.required_r_level(task_type, paths, diff), want)

    def test_r3_snake_case_tokens_are_r3(self):
        for paths in (["src/auth_utils.py"], ["src/session_store.py"],
                      ["src/secrets_store.py"], ["src/oauth.py"],
                      ["login/permission_check.py"]):
            with self.subTest(paths=paths):
                self.assertEqual(rule.required_r_level("code", paths), "R3")

    def test_r3_camel_case_tokens_are_r3(self):
        for paths in (["src/userAuth.py"], ["src/SessionStore.py"],
                      ["src/PermissionCheck.ts"], ["src/apiKey.py"]):
            with self.subTest(paths=paths):
                self.assertEqual(rule.required_r_level("code", paths), "R3")
        for paths in (["src/author.py"], ["docs/authority.md"],
                      ["docs/authorize-docs.md"]):
            with self.subTest(paths=paths):
                self.assertNotEqual(rule.required_r_level("code", paths), "R3")

    def test_r3_extended_secret_tokens_are_r3(self):
        for paths in (["src/credentials.py"], ["src/api_token.py"],
                      ["src/passwd"], ["src/ssh_keys.py"],
                      ["home/user/.ssh/id_rsa"], ["certs/server.pem"],
                      ["home/user/.ssh/authorized_keys"],
                      ["src/private_key.py"], ["src/keystore.jks"]):
            with self.subTest(paths=paths):
                self.assertEqual(rule.required_r_level("code", paths), "R3")

    def test_ops_dir_hints_are_r2(self):
        for paths in (["ops/run.sh"], ["deploy/app.sh"], ["infra/main.py"],
                      ["terraform/vpc.tf"], ["modules/vpc.tf"]):
            with self.subTest(paths=paths):
                self.assertEqual(rule.required_r_level("code", paths), "R2")

    def test_r3_secret_diff_lines_are_r3(self):
        for diff in ("+SECRET_KEY=x\n", "+PASSWORD=x\n", "+API_KEY=x\n"):
            with self.subTest(diff=diff.strip()):
                self.assertEqual(rule.required_r_level("code", ["a.py"], diff), "R3")

    def test_unknown_task_types_raise(self):
        with self.assertRaises(ValueError):
            rule.required_r_level("bogus", ["a.py"])
        with self.assertRaises(ValueError):
            rule.writer_allowed(VERTEX, "R2", "bogus", reg())
        with self.assertRaises(ValueError):
            rule.writer_allowed(VERTEX, "R9", "ops", reg())

    def test_task_type_none_gets_r1_floor(self):
        self.assertEqual(rule.required_r_level(None, []), "R1")
        self.assertEqual(rule.required_r_level(None, ["README.md"]), "R0")
        self.assertEqual(rule.required_r_level(None, ["src/main.py"]), "R1")
        self.assertEqual(rule.required_r_level(None, ["playbooks/x.yml"]), "R2")
        self.assertEqual(rule.required_r_level(None, ["src/auth/login.py"]), "R3")

    def test_non_str_task_type_raises(self):
        for bad in (123, ["code"], {"t": "code"}, b"code"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    rule.required_r_level(bad, ["a.py"])

    def test_added_counts_plus_plus_plus_without_space(self):
        # Intended: a '+++b/auth.py'-style header line carries no space, so it
        # counts as an added line and its R3 token still raises the card.
        self.assertEqual(rule.required_r_level("code", ["a.py"], "+++SECRET_KEY=x\n"), "R3")
        self.assertEqual(rule.required_r_level("code", ["a.py"], "+++b/auth.py\n"), "R3")


class WriterAllowedTests(unittest.TestCase):
    def test_sub40_stays_at_r0_r1(self):
        regd = reg()
        self.assertTrue(rule.writer_allowed(NEMOTRON, "R1", "code", regd))
        self.assertFalse(rule.writer_allowed(NEMOTRON, "R2", "code", regd))
        self.assertFalse(rule.writer_allowed(NEMOTRON, "R1", "ops", regd))
        self.assertFalse(rule.writer_allowed(NEMOTRON, "R3", "docs", regd))

    def test_sub40_containment_still_conservative(self):
        regd = reg()
        self.assertTrue(rule.writer_allowed(NEMOTRON, "R1", "code", regd))
        self.assertTrue(rule.writer_allowed("my-nemotron-3-super-clone", "R1", "code", regd))
        self.assertFalse(rule.writer_allowed("my-nemotron-3-super-clone", "R2", "code", regd))

    def test_the_chain_skips_the_unavailable_leg(self):
        regd = reg()
        self.assertEqual([l["model"] for l in rule.r2_chain(regd)], ["gemini-3.8-flash"])
        self.assertFalse(rule.writer_allowed(QWEN, "R2", "ops", regd))
        self.assertTrue(rule.writer_allowed(VERTEX, "R2", "ops", regd))
        self.assertTrue(rule.writer_allowed(VERTEX, "R3", "ops", regd))
        self.assertFalse(rule.writer_allowed("some/other-model", "R2", "code", regd))
        self.assertTrue(rule.writer_allowed("some/other-model", "R1", "code", regd))

    def test_chain_model_matching_is_exact(self):
        regd = reg()
        self.assertFalse(rule.writer_allowed("vertex/gemini-3.8-flash-lite", "R2", "ops", regd))
        self.assertFalse(rule.writer_allowed("my-gemini-3.8-flash-model", "R2", "code", regd))
        self.assertTrue(rule.writer_allowed("vertex/gemini-3.8-flash:free", "R2", "ops", regd))

    def test_chain_requires_leg_provider_prefix(self):
        regd = reg()
        for bad in ("gemini-3.8-flash", "google/gemini-3.8-flash",
                    "openrouter/google/gemini-3.8-flash",
                    "gemini/gemini-3.8-flash"):
            with self.subTest(bad=bad):
                self.assertFalse(rule.writer_allowed(bad, "R2", "ops", regd))
                self.assertFalse(rule.writer_allowed(bad, "R3", "ops", regd))
        for good in ("vertex/gemini-3.8-flash", "vertex_ai/gemini-3.8-flash",
                     "vertex/gemini-3.8-flash:high", "VERTEX/gemini-3.8-flash"):
            with self.subTest(good=good):
                self.assertTrue(rule.writer_allowed(good, "R2", "ops", regd))
                self.assertTrue(rule.writer_allowed(good, "R3", "ops", regd))
        self.assertFalse(rule.writer_allowed("vertex/gemini-3.8-flash-lite", "R2", "ops", regd))
        self.assertFalse(rule.writer_allowed("vertex_ai/gemini-3.8-flash-lite", "R3", "ops", regd))
        qreg = {"policy": {"writers": {"sub40_models": SUBS,
                                       "R2": [{"client": "qoder", "model": "qwen3.8-max"}],
                                       "R3": [{"client": "qoder", "model": "qwen3.8-max"}]}}}
        self.assertTrue(rule.writer_allowed("qoder/qwen3.8-max", "R2", "ops", qreg))
        self.assertFalse(rule.writer_allowed("qwen3.8-max", "R2", "ops", qreg))
        self.assertFalse(rule.writer_allowed("other/qwen3.8-max", "R2", "ops", qreg))
        nreg = {"policy": {"writers": {"sub40_models": SUBS,
                                       "R2": [{"model": "custom-model"}]}}}
        self.assertTrue(rule.writer_allowed("custom-model", "R2", "ops", nreg))
        self.assertTrue(rule.writer_allowed("other/custom-model", "R2", "ops", nreg))

    def test_r2_chain_skips_only_exact_false(self):
        legs = [{"model": "a"}, {"model": "b", "available": True},
                {"model": "c", "available": 0}, {"model": "d", "available": None},
                {"model": "e", "available": False}]
        regd = {"policy": {"writers": {"sub40_models": SUBS, "R2": legs}}}
        self.assertEqual([str(l["model"]) for l in rule.r2_chain(regd)], ["a", "b", "c", "d"])

    def test_missing_writers_fails_closed(self):
        for regd in ({"policy": {}}, {"policy": {"writers": {}}},
                     {"policy": {"writers": {"sub40_models": []}}},
                     {}, {"policy": {"writers": {"R2": [VERTEX_LEG]}}}):
            with self.subTest(reg=regd):
                self.assertFalse(rule.writer_allowed(VERTEX, "R2", "code", regd))
                self.assertFalse(rule.writer_allowed(VERTEX, "R3", "ops", regd))
                self.assertFalse(rule.writer_allowed("some/model", "R1", "ops", regd))
                self.assertFalse(rule.writer_allowed("some/model", "R0", "ops", regd))

    def test_r3_without_r3_key_falls_back_to_r2(self):
        no_r3 = {"policy": {"writers": {"sub40_models": SUBS, "R2": [VERTEX_LEG]}}}
        self.assertTrue(rule.writer_allowed(VERTEX, "R3", "ops", no_r3))
        self.assertTrue(rule.writer_allowed(VERTEX, "R2", "ops", no_r3))
        self.assertFalse(rule.writer_allowed(QWEN, "R3", "ops", no_r3))
        self.assertTrue(rule.writer_allowed(VERTEX, "R3", "ops", reg()))
        self.assertFalse(rule.writer_allowed(QWEN, "R3", "ops", reg()))

    def test_r3_explicit_empty_list_still_denies(self):
        empty_r3 = {"policy": {"writers": {"sub40_models": SUBS, "R2": [VERTEX_LEG],
                                           "R3": []}}}
        self.assertFalse(rule.writer_allowed(VERTEX, "R3", "ops", empty_r3))
        self.assertTrue(rule.writer_allowed(VERTEX, "R2", "ops", empty_r3))


class WritersPolicyTests(unittest.TestCase):
    def test_the_committed_registry_is_clean(self):
        with open(ROOT / "catalog" / "ai-registry.json", encoding="utf-8") as fh:
            regd = json.load(fh)
        self.assertIn("writers", regd["policy"])
        self.assertEqual(registry_mod._check_writers(regd), [])

    def test_malformed_writers_are_reported(self):
        bad = [{"policy": {"writers": ["R2"]}},
               {"policy": {"writers": {"sub40_models": "nemotron", "R2": []}}},
               {"policy": {"writers": {"sub40_models": [], "R2": []}}},
               {"policy": {}},
               {"policy": {"writers": {"R2": [{"model": 7}]}}},
               {"policy": {"writers": {"R2": [{"model": "m", "available": "no"}]}}}]
        for regd in bad:
            with self.subTest(reg=regd):
                self.assertTrue(registry_mod._check_writers(regd))
        self.assertEqual(registry_mod._check_writers(reg()), [])
        self.assertTrue(registry_mod._check_writers({"policy": {}}))

    def test_task_type_is_optional_v2_with_no_default(self):
        self.assertEqual(routing.normalize_v2({"task_type": "ops"})["task_type"], "ops")
        self.assertNotIn("task_type", routing.normalize_v2({}))
        with self.assertRaises(routing.CardError):
            routing.normalize_v2({"task_type": "bogus"})

    def test_pep8_two_blank_lines_between_defs(self):
        text = (ROOT / "tools" / "autoos_writer_rule.py").read_text(encoding="utf-8")
        for name in ("_added", "_checked", "required_r_level", "_writers", "r2_chain",
                     "writer_allowed"):
            self.assertIsNotNone(
                re.search(r"\n\n\ndef %s\b" % re.escape(name), "\n\n" + text),
                "expected two blank lines before def %s" % name)


class DemotionHookTests(unittest.TestCase):
    def _ledger(self, verdicts):
        path = os.path.join(tempfile.mkdtemp(), "writer-ledger.jsonl")
        for i, verdict in enumerate(verdicts):
            item = {"run_id": "r%d" % i, "verdict": verdict, "writer_client": "vertex",
                    "writer_model_served": VERTEX, "task_type": "code", "reviewer": "t3"}
            if verdict != "accepted":
                item["failure_class"] = "syntax"
            ledger.record(item, path=path)
        return path

    def test_demoted_denied_every_level(self):
        path = self._ledger(["rejected", "rejected"])
        for level in ("R0", "R1", "R2", "R3"):
            with self.subTest(level=level):
                self.assertFalse(rule.writer_allowed(VERTEX, level, "code", reg(), path))
        self.assertTrue(rule.writer_allowed(VERTEX, "R2", "ops", reg(), path))

    def test_clean_missing_and_probe(self):
        path = self._ledger(["accepted"])
        self.assertTrue(rule.writer_allowed(VERTEX, "R2", "ops", reg(), path))
        self.assertTrue(rule.writer_allowed(
            VERTEX, "R2", "ops", reg(), os.path.join(tempfile.mkdtemp(), "no.jsonl")))
        bad = self._ledger(["rejected", "rejected"])
        self.assertFalse(rule.writer_allowed(VERTEX, "R2", "code", reg(), bad))
        ledger.probe_pass(VERTEX, "code", path=bad)
        self.assertTrue(rule.writer_allowed(VERTEX, "R2", "code", reg(), bad))

    def test_unreadable_ledger_fails_closed(self):
        self.assertFalse(rule.writer_allowed(VERTEX, "R2", "ops", reg(), tempfile.mkdtemp()))

    def test_symlink_broken_link_and_directory_deny(self):
        good = self._ledger(["rejected", "rejected"])
        room = tempfile.mkdtemp()
        link = os.path.join(room, "link.jsonl")
        os.symlink(good, link)
        self.assertFalse(rule.writer_allowed(VERTEX, "R2", "ops", reg(), link))
        broken = os.path.join(room, "broken.jsonl")
        os.symlink(os.path.join(room, "absent.jsonl"), broken)
        self.assertFalse(rule.writer_allowed(VERTEX, "R2", "ops", reg(), broken))
        self.assertFalse(rule.writer_allowed(VERTEX, "R2", "ops", reg(), tempfile.mkdtemp()))

    def test_unreadable_file_denies(self):
        import builtins

        target = self._ledger(["accepted"])
        real = builtins.open

        def boom(*args, **kwargs):
            raise PermissionError("denied")

        builtins.open = boom

        try:
            self.assertFalse(rule.writer_allowed(VERTEX, "R2", "ops", reg(), target))
        finally:
            builtins.open = real

    def test_canonical_spelling_demoted_denies(self):
        target = os.path.join(tempfile.mkdtemp(), "writer-ledger.jsonl")

        for i, served in enumerate(("openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                                    "nemotron-3-super")):
            item = {"run_id": "n%d" % i, "verdict": "rejected", "failure_class": "syntax",
                    "writer_client": "openrouter", "writer_model_served": served,
                    "task_type": "code", "reviewer": "t3"}
            ledger.record(item, path=target)

        self.assertFalse(rule.writer_allowed("NEMOTRON-3-SUPER", "R1", "code", reg(), target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
