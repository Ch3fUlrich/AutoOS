#!/usr/bin/env python3
"""Tests for the skill-rules utility script.
"""
import unittest
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'tools' / 'skill-rules.py'

class SkillRulesTests(unittest.TestCase):
    def run_script(self, args, input_text=None):
        cmd = [sys.executable, str(SCRIPT)] + args
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_missing_file_is_an_error(self):
        result = self.run_script(['check', '/nonexistent/SKILL.md'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('file not found', result.stdout)

    def test_every_near_duplicate_pair_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rules.md'
            path.write_text("R-a-01: stop the session after merge (why: x; source: t)\n"
                            "R-a-02: stop the session after merge. (why: x; source: t)\n"
                            "R-a-03: stop the session after merges (why: x; source: t)\n",
                            encoding='utf-8')
            result = self.run_script(['check', str(path)])
        self.assertEqual(result.stdout.count('near-duplicate'), 3)

    def check_text(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rules.md'
            path.write_text(text, encoding='utf-8')
            return self.run_script(['check', str(path)])

    def test_a_source_inside_the_why_does_not_hide_a_long_why(self):
        why = ' '.join(['w'] * 13)
        result = self.check_text("R-a-01: act (why: %s; source: old; source: new)\n" % why)
        self.assertIn('why too long', result.stdout)

    def test_empty_imperative(self):
        result = self.check_text("R-a-01: (why: ok; source: t)\n")
        self.assertIn('empty imperative', result.stdout)

    def test_malformed_ids_are_reported_not_skipped(self):
        result = self.check_text("R-a-01: act (why: ok; source: t)\nR-skill-rules-01: act two (why: ok; source: t)\n- R-x-001: other thing (why: ok; source: t)\n")
        self.assertEqual(result.stdout.count('malformed id'), 2)

    def test_good_file(self):
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write("R-test-01: do something (why: because it works; source: test)\n")
            tf.write("- R-another-02: do other thing (why: simple; source: other)\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertEqual(result.returncode, 0)
        self.assertIn('ok: 2 rules', result.stdout)

    def test_duplicate_id(self):
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write("R-dup-01: first (why: ok; source: a)\n")
            tf.write("R-dup-01: second (why: ok; source: b)\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate id', result.stdout)

    def test_missing_tail(self):
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write("R-missing-01: something without tail\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing tail', result.stdout)

    def test_empty_source(self):
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write("R-empty-01: act (why: ok; source: )\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('empty source', result.stdout)

    def test_why_too_long(self):
        long_why = ' '.join(['word'] * 13)
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write(f"R-why-01: act (why: {long_why}; source: src)\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('why too long', result.stdout)

    def test_line_too_long(self):
        long_text = 'x' * 201
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write(f"R-line-01: {long_text} (why: ok; source: src)\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('line too long', result.stdout)

    def test_no_rules(self):
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write("# just a comment\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no rules found', result.stdout)

    def test_near_duplicate(self):
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write("R-first-01: do the same thing (why: ok; source: a)\n")
            tf.write("R-second-01: do the same thing (why: ok; source: b)\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['check', path])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('near-duplicate with', result.stdout)

    def test_list_topic_filter(self):
        with tempfile.NamedTemporaryFile('w+', delete=False) as tf:
            tf.write("R-foo-01: something (why: ok; source: a)\n")
            tf.write("R-bar-02: other (why: ok; source: b)\n")
            tf.flush()
            path = tf.name
        result = self.run_script(['list', '--topic', 'foo', path])
        self.assertEqual(result.returncode, 0)
        self.assertIn('R-foo-01', result.stdout)
        self.assertNotIn('R-bar-02', result.stdout)

if __name__ == '__main__':
    unittest.main()
