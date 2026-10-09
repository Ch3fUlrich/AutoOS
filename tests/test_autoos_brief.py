#!/usr/bin/env python3
"""Tests for tools/autoos_brief.py (AO-WRITER-GUARDS P2)."""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_brief as brief  # noqa: E402


def base():
    return {"task_id": "AO-1", "one_line": "fix mail", "paths": ["a.yml", "b.yml"],
            "goal": "once-only mail", "invariants": "idempotent",
            "keys_file": "keys.yml", "reference_playbook": "ref.yml",
            "entity": "host", "state_path": "state.json", "indent": "2",
            "files": "a.yml", "playbooks": "site.yml", "test_files": "test_x.py"}


class BriefTests(unittest.TestCase):
    def test_render_ok(self):
        out = brief.render_brief("ops", "R2", base())
        self.assertIn("AO-1", out)
        self.assertIn("a.yml, b.yml", out)
        self.assertNotIn("{{", out)

    def test_code_r2_also_picks_ops(self):
        self.assertIn("AO-1", brief.render_brief("code", "R2", base()))

    def test_missing_field_raises(self):
        f = base()
        del f["goal"]
        with self.assertRaises(ValueError) as cm:
            brief.render_brief("ops", "R2", f)
        self.assertIn("goal", str(cm.exception))

    def test_empty_value_raises(self):
        f = base()
        f["goal"] = "  "
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)

    def test_unknown_field_raises(self):
        f = base()
        f["bogus"] = "x"
        with self.assertRaises(ValueError) as cm:
            brief.render_brief("ops", "R2", f)
        self.assertIn("bogus", str(cm.exception))

    def test_over_3_paths_raises(self):
        f = base()
        f["paths"] = ["a.yml", "b.yml", "c.yml", "d.yml"]
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)

    def test_r3_appends_clause(self):
        r2 = brief.render_brief("ops", "R2", base())
        r3 = brief.render_brief("ops", "R3", base())
        self.assertNotIn("auth-regression", r2)
        self.assertIn("auth-regression", r3)

    def test_non_ops_r1_has_no_template(self):
        with self.assertRaises(ValueError) as cm:
            brief.render_brief("code", "R1", base())
        self.assertIn("no template", str(cm.exception))

    def test_value_with_braces_not_reexpanded(self):
        f = base()
        f["goal"] = "{{task_id}}"
        out = brief.render_brief("ops", "R2", f)
        self.assertIn("{{task_id}}", out)
        self.assertEqual(out.count("AO-1"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
