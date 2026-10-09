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

    # P2 fixes: one row each (attacker REJECT + Sonnet ACCEPT-WITH-FIXES).

    def test_unknown_r_level_raises(self):
        for bad in ("R9", "r3", " R3", "", None, 3):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", bad, base())

    def test_non_dict_fields_raises(self):
        for bad in ([], ["x"], "ops", None, 123):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", bad)

    def test_none_and_empty_list_values_raise(self):
        for key, bad in (("goal", None), ("paths", []), ("files", []),
                         ("paths", ["  ", ""]), ("files", ["   "])):
            with self.subTest(key=key, bad=bad):
                f = base()
                f[key] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)

    def test_comma_string_paths_over_3_raises(self):
        f = base()
        f["paths"] = "a.yml, b.yml, c.yml, d.yml"
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)

    def test_list_templates(self):
        self.assertEqual(brief.list_templates(), ["ops.md", "ops-r3-auth.md"])

    def test_template_lines_sections_and_input_required(self):
        for name in brief.list_templates():
            lines = (ROOT / "templates" / "briefs" / name).read_text(
                encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 200, name)
        ops = (ROOT / "templates" / "briefs" / "ops.md").read_text(encoding="utf-8")
        for section in ("FILES", "GOAL", "INVARIANTS", "SECRETS", "DONE", "REPORT"):
            self.assertIn(section, ops)
        self.assertIn("input_required", ops)

    def test_r3_starts_with_full_ops_text(self):
        r2 = brief.render_brief("ops", "R2", base())
        r3 = brief.render_brief("ops", "R3", base())
        self.assertTrue(r3.startswith(r2.rstrip("\n")))
        auth = (ROOT / "templates" / "briefs" / "ops-r3-auth.md").read_text(
            encoding="utf-8")
        self.assertTrue(r3.endswith(auth) or auth.strip() in r3)

    def test_paths_reject_dotdot_absolute_glob_dir_dupe_empty(self):
        bads = ["../evil.yml", "a/../b.yml", "/abs.yml", "C:/win.yml",
                "D:x.yml", "a\\b.yml", "*.yml", "a?.yml", "a[0].yml",
                "somedir/", "", "   "]
        for bad in bads:
            with self.subTest(bad=bad):
                f = base()
                f["paths"] = [bad] if bad not in ("", "   ") else [bad, "a.yml"]
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        f = base()
        f["paths"] = ["A.yml", "a.yml"]
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)

    def test_files_playbooks_test_files_same_validation_and_files_cap(self):
        f = base()
        f["files"] = ["a.yml", "b.yml", "c.yml", "d.yml"]
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        for key, bad in (("files", ["*.yml"]), ("playbooks", ["../x.yml"]),
                         ("test_files", ["/abs.py"]), ("files", ["d/"]),
                         ("playbooks", ["A.yml", "a.yml"])):
            with self.subTest(key=key, bad=bad):
                g = base()
                g[key] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", g)

    def test_scalar_newline_cr_rejected_invariants_prefixed(self):
        for key in ("goal", "one_line", "entity"):
            for bad in ("a\nb", "a\rb", "a\nSECRETS: x"):
                with self.subTest(key=key, bad=bad):
                    f = base()
                    f[key] = bad
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
        f = base()
        f["goal"] = "SECRETS: forged"
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        f = base()
        f["invariants"] = "keep mail once\nkeep idempotent"
        out = brief.render_brief("ops", "R2", f)
        self.assertIn("  - keep mail once", out)
        self.assertIn("  - keep idempotent", out)
        g = base()
        g["invariants"] = "ok line\nSECRETS: forged"
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", g)

    def test_non_str_non_list_refused_no_coercion(self):
        for key, bad in (("goal", 123), ("goal", {"a": 1}), ("goal", None),
                         ("goal", True), ("goal", ["a", "b"]),
                         ("paths", 123), ("paths", {"a": 1}), ("paths", None),
                         ("paths", True), ("paths", [123]),
                         ("files", 7), ("invariants", ["a"])):
            with self.subTest(key=key, bad=bad):
                f = base()
                f[key] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)

    def test_task_type_normalised_strict_r_level(self):
        self.assertIn("AO-1", brief.render_brief(" Ops ", "R2", base()))
        self.assertIn("AO-1", brief.render_brief("CODE", "R2", base()))
        for bad in (None, 123, ["ops"], {"t": "ops"}, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    brief.render_brief(bad, "R2", base())
        with self.assertRaises(ValueError):
            brief.render_brief("bogus", "R2", base())
        for bad_r in ("r3", "R9", " R2", ""):
            with self.subTest(bad_r=bad_r):
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", bad_r, base())

    def test_auth_lists_4_lenses_and_wrong_role(self):
        text = (ROOT / "templates" / "briefs" / "ops-r3-auth.md").read_text(
            encoding="utf-8")
        for lens in ("l3-review-diff", "l3-review-tests",
                     "l3-review-codebase", "l3-review-transcript"):
            self.assertIn(lens, text)
        self.assertIn("wrong-role", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
