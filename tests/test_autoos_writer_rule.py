#!/usr/bin/env python3
"""Tests for tools/autoos_writer_rule.py + policy.writers (AO-WRITER-GUARDS P1).

Fixtures are inline dicts plus the committed registry. Nothing is spawned.
Run from the repo root: python3 tests/test_autoos_writer_rule.py [ClassName]
"""
import json
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_routing as routing  # noqa: E402
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
    return {"policy": {"writers": {"sub40_models": SUBS, "R2": [QODER, VERTEX_LEG]}}}
class RequiredLevelTests(unittest.TestCase):
    def test_the_level_table(self):
        cases = [  # (task_type, paths, diff_text, expected)
            ("docs", ["README.md"], "", "R0"),
            ("code", ["src/main.py"], "", "R1"),
            ("code", ["src/author.py"], "", "R1"),  # author is not auth
            ("code", ["docs/authority.md"], "", "R1"),  # authority; floor holds
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
    def test_unknown_task_types_raise(self):
        with self.assertRaises(ValueError):
            rule.required_r_level("bogus", ["a.py"])
        with self.assertRaises(ValueError):
            rule.writer_allowed(VERTEX, "R2", "bogus", reg())
        with self.assertRaises(ValueError):
            rule.writer_allowed(VERTEX, "R9", "ops", reg())
class WriterAllowedTests(unittest.TestCase):
    def test_sub40_stays_at_r0_r1(self):
        regd = reg()
        self.assertTrue(rule.writer_allowed(NEMOTRON, "R1", "code", regd))
        self.assertFalse(rule.writer_allowed(NEMOTRON, "R2", "code", regd))
        self.assertFalse(rule.writer_allowed(NEMOTRON, "R1", "ops", regd))
        self.assertFalse(rule.writer_allowed(NEMOTRON, "R3", "docs", regd))
    def test_the_chain_skips_the_unavailable_leg(self):
        regd = reg()
        self.assertEqual([l["model"] for l in rule.r2_chain(regd)], ["gemini-3.8-flash"])
        self.assertFalse(rule.writer_allowed(QWEN, "R2", "ops", regd))
        self.assertTrue(rule.writer_allowed(VERTEX, "R2", "ops", regd))
        self.assertTrue(rule.writer_allowed(VERTEX, "R3", "ops", regd))
        self.assertFalse(rule.writer_allowed("some/other-model", "R2", "code", regd))
        self.assertTrue(rule.writer_allowed("some/other-model", "R1", "code", regd))
class WritersPolicyTests(unittest.TestCase):
    def test_the_committed_registry_is_clean(self):
        with open(ROOT / "catalog" / "ai-registry.json", encoding="utf-8") as fh:
            regd = json.load(fh)
        self.assertIn("writers", regd["policy"])
        self.assertEqual(registry_mod._check_writers(regd), [])
    def test_malformed_writers_are_reported(self):
        bad = [{"policy": {"writers": ["R2"]}},
               {"policy": {"writers": {"sub40_models": "nemotron", "R2": []}}},
               {"policy": {"writers": {"R2": [{"model": 7}]}}},
               {"policy": {"writers": {"R2": [{"model": "m", "available": "no"}]}}}]
        for regd in bad:
            with self.subTest(reg=regd):
                self.assertTrue(registry_mod._check_writers(regd))
        self.assertEqual(registry_mod._check_writers(reg()), [])
        self.assertEqual(registry_mod._check_writers({"policy": {}}), [])
    def test_task_type_is_optional_v2_with_no_default(self):
        self.assertEqual(routing.normalize_v2({"task_type": "ops"})["task_type"], "ops")
        self.assertNotIn("task_type", routing.normalize_v2({}))
        with self.assertRaises(routing.CardError):
            routing.normalize_v2({"task_type": "bogus"})
if __name__ == "__main__":
    unittest.main(verbosity=2)
