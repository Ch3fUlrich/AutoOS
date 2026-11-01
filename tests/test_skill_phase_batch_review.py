#!/usr/bin/env python3
"""Doc test: the phase-batch review rule (D-2534, amended D-3468/D-3474).

The unattended-orchestration skill and its references carry one rule, R-orch-31, and
the seat/fixer templates behind it. These assertions pin the step list (in order),
the mimo-v2.6-pro inline-only seat, "Claude reviewers never count", the single
non-Claude re-review, the 3-round cap, the templates, and every place the older
per-lane review wording is marked superseded - so a later edit cannot quietly put a
Claude seat or a per-task seat back.
"""
import importlib.util
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO / '.agents' / 'skills' / 'unattended-orchestration'
SKILL = SKILL_DIR / 'SKILL.md'
REFS = SKILL_DIR / 'references'
MAIN = REFS / 'main-orchestrator.md'
L3 = REFS / 'l3-routing.md'
RULE_MAP = REFS / 'rule-map.md'

_spec = importlib.util.spec_from_file_location('skill_rules', str(REPO / 'tools' / 'skill-rules.py'))
_sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sr)

RULE_ID = 'R-orch-31'
BLOCK_HEADING = '#### Phase-batch review'
LINE_BUDGET = 400

# One entry per step, in order: every fragment must appear in that step's text.
STEPS = [
    ('writers', ['parallel', 'phase/<key>', 'tests + CI', 'no per-task']),
    ('round-1 review', ['whole phase diff', 'mimo-v2.6-pro', 'inline-only', 'diff_ref',
                        'plus one other non-Claude family', 'Claude reviewers never count']),
    ('fixer', ['ONE fixer brief', 'ALL findings', 'same-pattern', 'verifies and commits',
               '<new-head>']),
    ('re-review', ['ONE non-Claude reviewer', 'fix delta', '<old-head>..<new-head>']),
    ('round cap', ['max 3 rounds', 'replan']),
    ('phase PR', ['phase PR', 'verdict']),
]


def read(path):
    return path.read_text(encoding='utf-8')


def section(text, heading):
    """Lines from `heading` up to the next markdown heading of any level."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    out = []
    for line in lines[start + 1:]:
        if line.startswith('#'):
            break
        out.append(line)
    return '\n'.join(out)


def numbered_steps(block):
    """{number: text} of the `N. ...` items, continuation lines folded in."""
    steps, current = {}, None
    for line in block.splitlines():
        m = re.match(r'^(\d+)\.\s+(.*)$', line)
        if m:
            current = int(m.group(1))
            steps[current] = m.group(2)
        elif current is not None and line.startswith('   ') and line.strip():
            steps[current] += ' ' + line.strip()
        elif not line.strip():
            current = None
    return steps


def superseded_after(text, anchor, window=3):
    """The `**Superseded` text on the anchor's own line (a table row) or within `window` lines after it."""
    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if anchor in line), None)
    assert idx is not None, 'anchor not found: %r' % anchor
    for line in lines[idx: idx + 1 + window]:
        if '**Superseded' in line:
            return line
    return None


class PhaseBatchRuleBlockTests(unittest.TestCase):
    def setUp(self):
        self.text = read(SKILL)
        self.block = section(self.text, BLOCK_HEADING)
        self.steps = numbered_steps(self.block)

    def test_the_rule_has_an_id_in_the_orch_scheme_and_a_why_and_source(self):
        rules = {}
        for _lineno, physical in _sr.iter_rule_blocks(self.text.splitlines()):
            parsed = _sr.parse_rule(_sr.rule_text(physical))
            if parsed:
                rules[parsed[0]] = parsed
        self.assertIn(RULE_ID, rules)
        _id, imperative, why, source = rules[RULE_ID]
        self.assertIn('D-2534', imperative)
        self.assertTrue(why and source, 'the rule lost its (why: .; source: .) tail')

    def test_the_block_names_its_decisions_in_the_heading(self):
        heading = next(line for line in self.text.splitlines() if line.startswith(BLOCK_HEADING))
        for decision in ('D-2534', 'D-3468', 'D-3474'):
            self.assertIn(decision, heading)

    def test_exactly_six_steps_in_order(self):
        self.assertEqual(sorted(self.steps), [1, 2, 3, 4, 5, 6])

    def test_each_step_carries_its_fragments(self):
        for number, (label, fragments) in enumerate(STEPS, start=1):
            step = self.steps[number]
            for fragment in fragments:
                self.assertIn(fragment, step, 'step %d (%s) lost %r' % (number, label, fragment))

    def test_the_step_fragments_appear_in_step_order(self):
        last = -1
        for number, (label, fragments) in enumerate(STEPS, start=1):
            pos = self.block.index(fragments[0])
            self.assertGreater(pos, last, 'step %d (%s) is out of order' % (number, label))
            last = pos

    def test_mimo_seat_is_named_inline_only_with_no_tools(self):
        step = self.steps[2]
        self.assertIn('no tools', step)
        self.assertIn('tests not run (no tools)', step)

    def test_both_seats_differ_from_the_writers_and_the_second_seat_cites_d_3474(self):
        step = self.steps[2]
        self.assertIn("different from the writers' families", step)
        self.assertIn('plus one other non-Claude family (D-3474)', step)

    def test_high_risk_adds_nothing_beyond_the_two_seats(self):
        self.assertNotIn('HIGH', self.block)
        self.assertNotIn('HIGH', section(read(MAIN), '## 4c. Phase-batch review'))

    def test_every_phase_is_reviewed_whatever_its_bucket(self):
        self.assertIn('Every phase', self.steps[2])
        self.assertIn('whatever its bucket', self.steps[2])

    def test_claude_never_counts_and_no_haiku_reviewer(self):
        self.assertIn('Claude reviewers never count', self.block)
        self.assertIn('never a Claude seat (Haiku included)', self.block)

    def test_mimo_is_pinned_seat_1_and_the_route_policy_picks_only_seat_2(self):
        preface = self.block.split('1. **Writers.**')[0]
        self.assertIn('Seat 1 is pinned: `mimo-v2.6-pro`', preface)
        self.assertIn('`route --explain`) chooses only seat 2', preface)
        self.assertNotIn('never a list in prose', self.block)
        self.assertNotIn('sits in it', self.block)

    def test_no_qualifying_second_seat_blocks_the_phase_fail_closed(self):
        preface = self.block.split('1. **Writers.**')[0]
        self.assertIn('no qualifying second family', preface)
        self.assertIn('BLOCKED (fail closed)', preface)
        self.assertIn('never fewer seats', preface)

    def test_mimo_is_always_one_of_the_two_round_1_seats(self):
        step = self.steps[2]
        self.assertIn('always one of the two seats', step)
        self.assertIn('never the only one', step)
        self.assertNotIn('never stands alone', step)

    def test_the_fixer_edits_are_committed_by_the_briefing_orchestrator(self):
        step = self.steps[3]
        self.assertIn('the L1/L2 that briefed the fixer verifies and commits its edits', step)
        self.assertIn('<new-head>', step)

    def test_the_templates_pointer_names_section_4c(self):
        self.assertIn('(references/main-orchestrator.md) \u00a74c', self.block)

    def test_the_skill_stays_under_the_line_budget(self):
        self.assertLessEqual(len(self.text.splitlines()), LINE_BUDGET)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.main = read(MAIN)

    def test_the_l2_brief_carries_the_phase_batch_rule_id(self):
        brief = section(self.main, '## 3. Spawning and briefing L2')
        self.assertIn(RULE_ID, brief)

    def test_section_4_names_the_rule_with_the_other_review_ids(self):
        intro = section(self.main, '## 4. L3 executors')
        for rule_id in ('R-orch-13', 'R-orch-14', RULE_ID):
            self.assertIn(rule_id, intro)

    def _block(self, heading):
        return section(self.main, heading)

    def test_seat_template_has_the_no_tools_line_and_verdict_first_line(self):
        block = self._block('### Phase review seat: prompt and card')
        self.assertIn('tests not run (no tools)', block)
        self.assertIn('VERDICT: ACCEPT|REQUEST_CHANGES', block)
        self.assertIn('first line', block)

    def test_seat_card_is_valid_json_and_carries_diff_ref(self):
        block = self._block('### Phase review seat: prompt and card')
        fenced = re.search(r'```json\n(.*?)```', block, re.S)
        self.assertIsNotNone(fenced, 'no ```json card in the seat template')
        card = json.loads(fenced.group(1))
        self.assertEqual(set(card), {'estimate_tokens', 'max_min', 'stall_min', 'diff_ref'})
        self.assertIsInstance(card.get('estimate_tokens'), int, 'CARD_SCHEMA requires estimate_tokens')
        self.assertEqual(set(card['diff_ref']), {'repo', 'base', 'head'})

    def test_seat_template_says_the_diff_is_inline_not_by_path(self):
        block = self._block('### Phase review seat: prompt and card')
        self.assertIn('full diff inline', block)

    def test_fixer_template_gets_all_findings_fails_closed_and_never_commits(self):
        block = self._block('### Phase fixer brief')
        for fragment in ('ALL open findings', 'LOW', 'MED', 'same-pattern', 'fail closed',
                         'no commit'):
            self.assertIn(fragment, block)

    def test_fixer_template_names_who_commits_new_head(self):
        block = self._block('### Phase fixer brief')
        self.assertIn('verifies and commits', block)
        self.assertIn('<new-head>', block)
        brief = re.search(r'```text\n(.*?)```', block, re.S).group(1)
        self.assertIn('no commit, no push (your briefing L1/L2 verifies and commits them)', brief)
        seat = self._block('### Phase review seat: prompt and card')
        self.assertIn('`<new-head>` is the commit the briefing L1/L2 made of the fixer', ' '.join(seat.split()))

    def test_seat_record_keeps_writer_and_provider_skip_text(self):
        block = self._block('### Phase review seat: prompt and card')
        record = block[block.index('Record per seat:'):]
        for fragment in ('writer', 'family', 'model', 'verdict', 'closing line', 'provider skip text'):
            self.assertIn(fragment, record)

    def test_4c_pins_seat_1_and_blocks_without_a_second_family(self):
        intro = section(self.main, '## 4c. Phase-batch review')
        self.assertIn('Seat 1 is pinned: `mimo-v2.6-pro`', intro)
        self.assertIn('chooses only seat 2', intro)
        self.assertIn('BLOCKED (fail closed)', intro)
        self.assertNotIn('never copied into a brief', intro)

    def test_the_main_orchestrator_stays_under_the_line_budget(self):
        self.assertLessEqual(len(self.main.splitlines()), LINE_BUDGET)

    def test_phase_batch_section_sits_after_4b_and_before_4a(self):
        heads = [l for l in self.main.splitlines() if l.startswith('## 4')]
        order = [l.split('.')[0] for l in heads]
        self.assertEqual(order, ['## 4', '## 4b', '## 4c', '## 4a'])

    def test_re_review_card_and_range_carry_the_fix_delta(self):
        block = self._block('### Phase review seat: prompt and card')
        self.assertIn('{repo, base: <old-head>, head: <new-head>}', block)
        self.assertIn('Range: <old-head>..<new-head>', block)


class SupersededMarkerTests(unittest.TestCase):
    """The older per-lane / Claude-reviewer wording stays as history, marked in place."""

    def assertMarked(self, path, anchor, *fragments):
        line = superseded_after(read(path), anchor)
        self.assertIsNotNone(line, '%s: no **Superseded line after %r' % (path.name, anchor))
        for fragment in fragments:
            self.assertIn(fragment, line)

    def test_skill_r_orch_13_per_lane_seats(self):
        self.assertMarked(SKILL, '- R-orch-13:', 'D-2534', RULE_ID)

    def test_skill_r_orch_13_timing_and_trigger_are_superseded_for_phase_work(self):
        self.assertMarked(SKILL, '- R-orch-13:', 'before execute', 'finished phase diff',
                          'before merge', 'every phase regardless of bucket')

    def test_skill_r_orch_14_per_lane_seats(self):
        self.assertMarked(SKILL, '- R-orch-14:', 'D-2534', RULE_ID)

    def test_skill_r_orch_30_fan_out_is_scoped_away_from_phase_review(self):
        self.assertMarked(SKILL, '- R-orch-30:', 'D-2534', 'D-2540', RULE_ID, 'exactly two seats', 'never a panel')

    def test_skill_r_coord_03_haiku_first_pass_is_not_a_review_seat(self):
        self.assertMarked(SKILL, '- R-coord-03:', 'D-3468', 'Haiku')

    def test_main_orchestrator_haiku_sonnet_row_is_not_a_reviewer(self):
        self.assertMarked(MAIN, '| Claude Haiku / Sonnet |', 'D-3468', 'never counts')

    def test_main_orchestrator_haiku_place_sentence_survives_only_inside_its_marker(self):
        text = read(MAIN)
        hits = [line for line in text.splitlines() if "Haiku's place is" in line]
        self.assertTrue(hits, 'the superseded marker lost the sentence it supersedes')
        for line in hits:
            self.assertIn('**Superseded by D-3468', line)
        live = section(text, '## 4. L3 executors').split('**Superseded by D-3468')[0]
        self.assertIn('seat selection is', live)
        self.assertNotIn('Haiku', live)

    def test_l3_routing_closer_and_review_role(self):
        self.assertMarked(L3, '| **Role** (added) |', 'D-3468')
        self.assertMarked(L3, '| **closer** |', 'D-3468')

    def test_rule_map_haiku_and_per_lane_rows(self):
        self.assertMarked(RULE_MAP, '| `R-coord-03` | merged |', 'D-3468')
        self.assertMarked(RULE_MAP, '| `R-orch-13` | merged |', 'D-2534')

    def test_rule_map_names_the_new_rule(self):
        self.assertIn('| `%s` | new |' % RULE_ID, read(RULE_MAP))

    def test_marker_lines_in_the_skill_are_not_parsed_as_rules(self):
        for line in read(SKILL).splitlines():
            if '**Superseded' in line:
                self.assertIsNone(_sr.parse_rule(line), line)

    def test_touched_files_stay_under_the_line_budget(self):
        for path in (SKILL, MAIN, L3, RULE_MAP, Path(__file__)):
            self.assertLessEqual(len(read(path).splitlines()), LINE_BUDGET, path.name)


if __name__ == '__main__':
    unittest.main()
