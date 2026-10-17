#!/usr/bin/env python3
"""Tests for tools/autoos_brief.py (AO-WRITER-GUARDS P2)."""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoos_brief as brief  # noqa: E402


def base():
    return {"task_id": "AO-1", "one_line": "fix mail",
            "paths": ["a.yml", "b.yml", "test_x.py"],
            "goal": "once-only mail", "invariants": "idempotent",
            "keys_file": "keys.yml", "reference_playbook": "ref.yml",
            "entity": "host", "state_path": "a.yml", "indent": "2",
            "files": "a.yml", "playbooks": "b.yml", "test_files": "test_x.py"}


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

    def test_control_chars_rejected_except_invariants_nl(self):
        bads = ["a\x0bb", "a\x0cb", "a\x85b", "a\u2028b", "a\u2029b",
                "a\rb", "a\nb", "a\tb", "a\x00b", "a\x07b"]
        for key in ("goal", "one_line", "entity"):
            for bad in bads:
                with self.subTest(key=key, bad=repr(bad)):
                    f = base()
                    f[key] = bad
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
        for bad in bads:
            with self.subTest(bad=repr(bad)):
                f = base()
                f["paths"] = ["a.yml", "b" + bad + ".yml"]
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        for bad in ("a\x0bb", "a\x0cb", "a\x85b", "a\u2028b",
                    "a\u2029b", "a\rb", "a\x00b"):
            with self.subTest(bad=repr(bad)):
                f = base()
                f["invariants"] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        f = base()
        f["invariants"] = "line one\nline two"
        out = brief.render_brief("ops", "R2", f)
        self.assertIn("line one", out)
        f = base()
        f["invariants"] = "a\tb"
        brief.render_brief("ops", "R2", f)

    def test_keyword_filter_nfkc_space_before_colon(self):
        for bad in ("\uFF33\uFF25\uFF23\uFF32\uFF25\uFF34\uFF33: x",
                    "\uFF27\uFF2F\uFF21\uFF2C: x",
                    "\uFF25\uFF24\uFF29\uFF34 \uFF2D\uFF25\uFF34\uFF28\uFF2F\uFF24: x",
                    "FILES : x", "files : x", "  GOAL : forged",
                    "\uFF26\uFF29\uFF2C\uFF25\uFF33 : x"):
            with self.subTest(bad=bad):
                f = base()
                f["goal"] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        f = base()
        f["invariants"] = "ok\n\uFF33\uFF25\uFF23\uFF32\uFF25\uFF34\uFF33: x"
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        f = base()
        f["goal"] = "doner kebab"
        self.assertIn("doner kebab", brief.render_brief("ops", "R2", f))

    def test_str_subclasses_rejected(self):
        class S(str):
            pass
        for key, bad in (("goal", S("hello")), ("one_line", S("x")),
                         ("paths", [S("a.yml")]),
                         ("paths", S("a.yml")),
                         ("files", [S("a.yml")])):
            with self.subTest(key=key, bad=repr(bad)):
                f = base()
                f[key] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        with self.assertRaises(ValueError):
            brief.render_brief(S("ops"), "R2", base())
        with self.assertRaises(ValueError):
            brief.render_brief("ops", S("R2"), base())

    def test_path_dot_empty_components_rejected(self):
        for bad in ("a//b.yml", "a/./b.yml", "./a.yml", "a/b/./c.yml",
                    "./x", "a/b//c.yml"):
            with self.subTest(bad=bad):
                f = base()
                f["paths"] = [bad]
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        f = base()
        f["paths"] = "a.yml, ./b.yml"
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        f = base()
        f["paths"] = ["./a.yml", "a.yml"]
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        f = base()
        f["paths"] = ["a.yml", " a.yml "]
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)

    def test_single_path_fields_validated(self):
        for key, bad in (("reference_playbook", "../x.yml"),
                         ("reference_playbook", "/abs.yml"),
                         ("reference_playbook", "*.yml"),
                         ("reference_playbook", "d/"),
                         ("reference_playbook", "a\\b.yml"),
                         ("reference_playbook", "C:x.yml"),
                         ("reference_playbook", "a//b.yml"),
                         ("reference_playbook", "a\x0bb.yml"),
                         ("reference_playbook", "SECRETS: x"),
                         ("state_path", "../s.json"),
                         ("state_path", "/abs.json"),
                         ("state_path", "a?.json"),
                         ("state_path", "d/"),
                         ("state_path", "./s.json"),
                         ("keys_file", "../k.yml"),
                         ("keys_file", "*.yml"),
                         ("keys_file", "a\x0bb.yml"),
                         ("keys_file", "a//b.yml"),
                         ("keys_file", "a/./b.yml"),
                         ("keys_file", ["a.yml"]),
                         ("keys_file", "a.yml, b.yml")):
            with self.subTest(key=key, bad=bad):
                f = base()
                f[key] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        for good in ("/home/u/keys.yml", "/etc/keys.yml", "~/keys.yml",
                     "keys.yml", "sub/keys.yml"):
            with self.subTest(good=good):
                f = base()
                f["keys_file"] = good
                self.assertIn(good, brief.render_brief("ops", "R2", f))
        for bad_abs in ("/home/u/../etc/keys.yml", "/home/*.yml"):
            with self.subTest(bad_abs=bad_abs):
                f = base()
                f["keys_file"] = bad_abs
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)


    def test_cf_co_cs_cn_nul_rejected_everywhere(self):
        import unicodedata
        bads = ["a\u200bb", "a\ufeffb", "a\u00adb", "a\u200eb",
                "a\u200fb", "a\u2066b", "a\ue000b", "a\ud800b",
                "a\u0378b", "a\x00b"]
        for ch in bads:
            self.assertIn(unicodedata.category(ch.strip("ab")),
                          ("Cf", "Co", "Cs", "Cn", "Cc"), repr(ch))
        scalar_keys = ("task_id", "one_line", "goal", "entity", "indent")
        for key in scalar_keys:
            for bad in bads:
                with self.subTest(key=key, bad=repr(bad)):
                    f = base()
                    f[key] = bad
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
        for key in ("paths", "files", "playbooks", "test_files"):
            for bad in bads:
                with self.subTest(key=key, bad=repr(bad)):
                    f = base()
                    f[key] = ["a.yml", "b" + bad + ".yml"]
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
        for key in ("keys_file", "reference_playbook", "state_path"):
            for bad in bads:
                with self.subTest(key=key, bad=repr(bad)):
                    f = base()
                    f[key] = "b" + bad + ".yml"
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
        for bad in bads:
            with self.subTest(bad=repr(bad)):
                f = base()
                f["invariants"] = "ok line\nbad " + bad + " line"
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        for bad in bads:
            with self.subTest(bad=repr(bad)):
                f = base()
                f["invariants"] = "x" + bad + "y"
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)

    def test_path_whitespace_trailing_dot_reserved_ascii(self):
        for key in ("paths", "files", "playbooks", "test_files"):
            for bad in (" a.yml", "a.yml ", " a.yml ", "\ta.yml"):
                with self.subTest(key=key, bad=repr(bad)):
                    f = base()
                    f[key] = [bad]
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
            for bad in (" a.yml", "a.yml "):
                with self.subTest(key=key, bad=repr(bad)):
                    f = base()
                    f[key] = bad
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
        for key in ("keys_file", "reference_playbook", "state_path"):
            for bad in (" a.yml", "a.yml ", " a.yml "):
                with self.subTest(key=key, bad=repr(bad)):
                    f = base()
                    f[key] = bad
                    with self.assertRaises(ValueError):
                        brief.render_brief("ops", "R2", f)
        for bad in ("a.yml.", "foo.", "a/b.", "a./b.yml", "a/b.yml."):
            with self.subTest(bad=bad):
                f = base()
                f["paths"] = [bad]
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        reserved = ["CON", "con", "Con.yml", "PRN", "prn.txt", "AUX",
                    "aux.yml", "NUL", "nul.txt", "COM1", "com9.yml",
                    "LPT1", "lpt9.txt", "CoM5", "LpT4.yml"]
        for bad in reserved:
            with self.subTest(bad=bad):
                f = base()
                f["paths"] = [bad]
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        for bad in ("a/CON/b.yml", "a/con.txt", "COM1/x.yml",
                    "sub/LPT9.yml", "a/AUX"):
            with self.subTest(bad=bad):
                f = base()
                f["paths"] = [bad]
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        for key in ("keys_file", "reference_playbook", "state_path"):
            with self.subTest(key=key):
                f = base()
                f[key] = "CON.yml"
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        for bad in ("a\u2044b.yml", "a\u2215b.yml", "a\uff0fb.yml",
                    "caf\u00e9.yml", "a\u00e9.yml"):
            with self.subTest(bad=repr(bad)):
                f = base()
                f["paths"] = [bad]
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)

    def test_length_limits_scalar_invariants_paths(self):
        f = base()
        f["goal"] = "x" * 500
        brief.render_brief("ops", "R2", f)
        f = base()
        f["goal"] = "x" * 501
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        f = base()
        f["invariants"] = "y" * 2000
        brief.render_brief("ops", "R2", f)
        f = base()
        f["invariants"] = "y" * 2001
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        ok_path = "a" * 196 + ".yml"
        self.assertEqual(len(ok_path), 200)
        f = base()
        f["paths"] = [ok_path]
        f["files"] = ok_path
        f["playbooks"] = ok_path
        f["test_files"] = ok_path
        f["state_path"] = ok_path
        brief.render_brief("ops", "R2", f)
        bad_path = "a" * 197 + ".yml"
        self.assertEqual(len(bad_path), 201)
        f = base()
        f["paths"] = [bad_path]
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        f = base()
        f["state_path"] = bad_path
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)

    def test_exact_container_types_dict_and_list(self):
        class D(dict):
            pass
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", D(base()))
        class L(list):
            pass
        for bad in (("a.yml", "b.yml"), {"a.yml", "b.yml"},
                    (x for x in ["a.yml"]), L(["a.yml"]),
                    ("a.yml",), frozenset(["a.yml"])):
            with self.subTest(bad=repr(bad)):
                f = base()
                f["paths"] = bad
                with self.assertRaises(ValueError):
                    brief.render_brief("ops", "R2", f)
        for bad in ((["a.yml"]), ({"a.yml"})):
            pass
        f = base()
        f["files"] = ("a.yml", "b.yml")
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)
        f = base()
        f["paths"] = L(["a.yml"])
        with self.assertRaises(ValueError):
            brief.render_brief("ops", "R2", f)

    def test_files_subset_fence_stray_refused(self):
        for key, stray in (("files", "stray.yml"),
                           ("playbooks", "stray_pb.yml"),
                           ("test_files", "stray_test.py")):
            with self.subTest(key=key, stray=stray):
                f = base()
                f[key] = stray
                with self.assertRaises(ValueError) as cm:
                    brief.render_brief("ops", "R2", f)
                self.assertIn(stray, str(cm.exception))

    def test_files_subset_fence_subset_accepted(self):
        f = base()
        f["paths"] = ["a.yml", "b.yml", "test_x.py"]
        f["files"] = ["a.yml", "b.yml"]
        f["playbooks"] = "b.yml"
        f["test_files"] = "test_x.py"
        out = brief.render_brief("ops", "R2", f)
        self.assertIn("AO-1", out)
        g = base()
        g["paths"] = ["A.yml", "b.yml", "test_x.py"]
        g["files"] = "a.yml"
        g["playbooks"] = "B.YML"
        g["test_files"] = "TEST_X.py"
        self.assertIn("AO-1", brief.render_brief("ops", "R2", g))

    def test_files_subset_fence_new_test_file(self):
        f = base()
        f["paths"] = ["a.yml", "b.yml", "test_new.py"]
        f["files"] = "a.yml"
        f["playbooks"] = "b.yml"
        f["test_files"] = "test_new.py"
        out = brief.render_brief("ops", "R2", f)
        self.assertIn("test_new.py", out)

    def test_files_subset_fence_14_distinct_refused(self):
        f = base()
        f["paths"] = ["p1.yml", "p2.yml", "p3.yml"]
        f["files"] = ["f1.yml", "f2.yml", "f3.yml"]
        f["playbooks"] = ["pb1.yml", "pb2.yml", "pb3.yml"]
        f["test_files"] = ["t1.py", "t2.py", "t3.py"]
        f["state_path"] = "p1.yml"
        with self.assertRaises(ValueError) as cm:
            brief.render_brief("ops", "R2", f)
        self.assertIn("f1.yml", str(cm.exception))

    def test_state_path_outside_paths_refused(self):
        f = base()
        f["state_path"] = "stray_state.json"
        with self.assertRaises(ValueError) as cm:
            brief.render_brief("ops", "R2", f)
        self.assertIn("stray_state.json", str(cm.exception))

    def test_state_path_inside_paths_accepted(self):
        f = base()
        f["paths"] = ["a.yml", "b.yml", "test_x.py"]
        f["state_path"] = "b.yml"
        out = brief.render_brief("ops", "R2", f)
        self.assertIn("b.yml", out)
        g = base()
        g["paths"] = ["A.yml", "b.yml", "test_x.py"]
        g["state_path"] = "a.YML"
        self.assertIn("AO-1", brief.render_brief("ops", "R2", g))

    def test_readonly_refs_marked(self):
        ops = (ROOT / "templates" / "briefs" / "ops.md").read_text(encoding="utf-8")
        self.assertIn("READ-ONLY, do not edit", ops)

    def test_rendered_lines_never_start_with_keyword_structural(self):
        import unicodedata
        f = base()
        f["invariants"] = "keep mail once\nkeep idempotent"
        out = brief.render_brief("ops", "R2", f)
        inv_lines = [ln for ln in out.splitlines() if "keep mail" in ln or "keep idempotent" in ln]
        self.assertEqual(len(inv_lines), 2)
        for ln in inv_lines:
            self.assertIn("  - keep ", ln,
                          "invariants line missing bullet: %r" % ln)
        shown_inv = "\n".join("  - " + ln.strip()
                              for ln in f["invariants"].splitlines())
        for ln in shown_inv.splitlines():
            self.assertTrue(ln.startswith("  - "))
            norm = unicodedata.normalize("NFKC", ln).strip().casefold()
            for kw in brief._KEYWORD_BASES:
                base_kw = kw.casefold()
                if norm.startswith(base_kw):
                    rest = norm[len(base_kw):]
                    self.assertFalse(
                        rest == "" or rest.startswith(":")
                        or rest[:1].isspace(),
                        "rendered invariants line starts with keyword: %r" % ln)
        for legal in ("discuss DONE later", "my SECRETS are safe here",
                      "  - GOAL: not a bypass", "goal Oriented work",
                      "doner kebab"):
            with self.subTest(legal=legal):
                g = base()
                g["goal"] = legal
                out2 = brief.render_brief("ops", "R2", g)
                self.assertIn(legal, out2)
                norm = unicodedata.normalize("NFKC", legal).strip().casefold()
                starts_kw = False
                for kw in brief._KEYWORD_BASES:
                    b = kw.casefold()
                    if norm.startswith(b):
                        rest = norm[len(b):]
                        if rest == "" or __import__("re").match(r"^\s*:", rest):
                            starts_kw = True
                self.assertFalse(starts_kw, "test value itself is a keyword line")
        for k, v in base().items():
            if k in brief._PATH_FIELDS or k in brief._SINGLE_PATH_FIELDS:
                continue
            if k == "invariants":
                rendered = "\n".join(
                    "  - " + ln.strip() for ln in v.splitlines()) \
                    if "\n" in v else "  - " + v.strip()
            else:
                rendered = v
            for ln in rendered.splitlines():
                norm = unicodedata.normalize("NFKC", ln).strip().casefold()
                for kw in brief._KEYWORD_BASES:
                    b = kw.casefold()
                    if norm.startswith(b):
                        rest = norm[len(b):]
                        self.assertFalse(
                            rest == "" or __import__("re").match(r"^\s*:", rest),
                            "value %r line starts with keyword: %r" % (k, ln))


if __name__ == "__main__":
    unittest.main(verbosity=2)
