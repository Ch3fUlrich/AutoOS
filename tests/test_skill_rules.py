#!/usr/bin/env python3
"""Tests for the skill-rules utility script.
"""
import re
import json
import unittest
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'tools' / 'skill-rules.py'

# Import parse_rule from the skill-rules script for reuse in resolution tests.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('skill_rules', str(SCRIPT))
_sr = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_sr)
parse_rule = _sr.parse_rule

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

class RuleResolutionTests(unittest.TestCase):
    """Every R-<topic>-NN cited in git-tracked files (except CHANGELOG.md and
    references/PROPOSALS-*) must resolve to either:
      - a rule in SKILL.md, or
      - a row in references/rule-map.md.
    Every rule-map target that is a new R-id must exist in SKILL.md."""

    REPO = Path(__file__).resolve().parent.parent
    SKILL = REPO / '.agents' / 'skills' / 'unattended-orchestration' / 'SKILL.md'
    RULE_MAP = REPO / '.agents' / 'skills' / 'unattended-orchestration' / 'references' / 'rule-map.md'
    RULE_ID_RE = re.compile(r'R-[a-z]+-\d{2}')

    def _skill_ids(self):
        """All R-ids defined as rules in SKILL.md."""
        text = self.SKILL.read_text(encoding='utf-8')
        ids = set()
        for line in text.splitlines():
            parsed = parse_rule(line)
            if parsed:
                ids.add(parsed[0])
        return ids

    def _rule_map_old_ids(self):
        """All old R-ids listed in rule-map.md (first column)."""
        if not self.RULE_MAP.is_file():
            return set(), {}
        text = self.RULE_MAP.read_text(encoding='utf-8')
        old_ids = set()
        targets = {}  # old_id -> target string
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith('|') or line.startswith('|---') or line.startswith('| old'):
                continue
            cols = [c.strip() for c in line.split('|')]
            if len(cols) < 3:
                continue
            old_id = cols[1].strip().strip('`')
            target = cols[2].strip()
            m = self.RULE_ID_RE.match(old_id)
            if m:
                old_ids.add(old_id)
                targets[old_id] = target
        return old_ids, targets

    def _cited_ids(self):
        """All R-ids cited in git-tracked files, excluding CHANGELOG.md,
        references/PROPOSALS-*, and SKILL.md itself."""
        result = subprocess.run(
            ['git', 'ls-files'],
            capture_output=True, text=True, cwd=str(self.REPO))
        cited = set()
        for f in result.stdout.splitlines():
            if 'CHANGELOG.md' in f:
                continue
            if 'references/PROPOSALS-' in f:
                continue
            if f.endswith('SKILL.md') and 'unattended-orchestration' in f:
                continue
            if f.endswith('test_skill_rules.py'):
                continue
            if f.endswith('rule-map.md'):
                continue
            path = self.REPO / f
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            for m in self.RULE_ID_RE.finditer(text):
                cited.add(m.group(0))
        return cited

    def test_every_cited_id_resolves(self):
        """Every R-id cited anywhere must be a rule in SKILL.md or a row in rule-map.md."""
        skill_ids = self._skill_ids()
        map_old_ids, _ = self._rule_map_old_ids()
        cited = self._cited_ids()
        unresolved = cited - skill_ids - map_old_ids
        self.assertEqual(unresolved, set(),
                         f"cited R-ids that resolve nowhere: {sorted(unresolved)}")

    def test_every_rule_map_target_id_exists_in_skill(self):
        """Every rule-map target that is a new R-id must exist in SKILL.md."""
        skill_ids = self._skill_ids()
        _, targets = self._rule_map_old_ids()
        missing = []
        for old_id, target in targets.items():
            for m in self.RULE_ID_RE.finditer(target):
                new_id = m.group(0)
                if new_id not in skill_ids:
                    missing.append(f"{old_id} -> {new_id}")
        self.assertEqual(missing, [],
                         f"rule-map targets not in SKILL.md: {missing}")


class CompactionRuleTests(unittest.TestCase):
    """D-146 / SB-C item 2: `R-worker-11` — who may ask for a transcript summary.

    A 'summarise the conversation, text only, no tools' request with no cross-session
    `from=` is the agent's own harness compacting it, so complying is the right call
    and a refusal is a self-inflicted context death; only a `from=` line can be a
    peer (and a peer asking for a transcript is an injection). The fixture is the
    contract: one JSON object per line, and both verdicts must appear, so a
    classifier that stops reading `from=` — or starts reading a bare 'from' word as
    one — fails here rather than in a live lane."""

    REPO = Path(__file__).resolve().parent.parent
    SKILL = REPO / '.agents' / 'skills' / 'unattended-orchestration' / 'SKILL.md'
    FIXTURE = REPO / 'tests' / 'fixtures' / 'skill-rules-compaction.jsonl'

    def cases(self):
        lines = self.FIXTURE.read_text(encoding='utf-8').splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def test_a_request_without_from_is_harness_compaction(self):
        bare = [case for case in self.cases()
                if case['expect'] == _sr.HARNESS_COMPACTION]
        self.assertTrue(bare, "the fixture names no from=-less case at all")
        for case in bare:
            self.assertEqual(_sr.classify_origin(case['text']),
                             _sr.HARNESS_COMPACTION, case['case'])

    def test_a_request_with_a_from_origin_is_a_peer(self):
        peers = [case for case in self.cases() if case['expect'] == 'peer']
        self.assertTrue(peers, "the fixture names no from= case at all")
        for case in peers:
            self.assertEqual(_sr.classify_origin(case['text']), _sr.PEER, case['case'])

    def test_every_fixture_case_matches_its_verdict(self):
        for case in self.cases():
            self.assertEqual(_sr.classify_origin(case['text']), case['expect'],
                             case['case'])

    def test_the_fixture_carries_both_classes(self):
        self.assertEqual({c['expect'] for c in self.cases()},
                         {_sr.HARNESS_COMPACTION, _sr.PEER})

    def test_the_skill_file_defines_the_rule(self):
        rules = {}
        for line in self.SKILL.read_text(encoding='utf-8').splitlines():
            parsed = parse_rule(line)
            if parsed:
                rules[parsed[0]] = parsed
        rule = rules.get('R-worker-11')
        self.assertIsNotNone(rule, 'R-worker-11 is not a rule in SKILL.md')
        _id, imperative, why, source = rule
        self.assertIn('from=', imperative)
        self.assertTrue(why and source, 'the rule lost its (why: …; source: …) tail')

    def test_the_skill_file_passes_the_checker(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'check', str(self.SKILL)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == '__main__':
    unittest.main()
