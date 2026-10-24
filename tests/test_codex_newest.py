"""Newest-only contract: synthetic stale ids deliberately assembled, not routes."""
import copy
import datetime
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import registry

# Every location _check_codex_newest walks: the two id sections, the pin list
# and the policy lists (reviewers/writers/route_order/free_client_models).
LOCATIONS = ('models', 'routes', 'pins', 'reviewers', 'writers',
             'route_order', 'free_client_models')


def _reg_with(location, value):
    """A committed-registry copy with `value` planted at one walked location."""
    reg = copy.deepcopy(registry.load(registry.DEFAULT_REGISTRY_PATH))
    if location in ('models', 'routes'):
        reg[location]['stale'] = {'id': value, 'legs': [value]}
    elif location == 'pins':
        reg['pins'] = [value]
    else:
        reg['policy'][location] = [value]
    return reg


def _newest_problems(reg):
    return [p for p in registry.check_registry(reg) if 'newest_only' in p]


def _load_newest_tool():
    """tools/codex-newest-check.py as a module (it is not importable by name)."""
    spec = importlib.util.spec_from_file_location('newest', ROOT / 'tools/codex-newest-check.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NewestOnlyTests(unittest.TestCase):
    def test_stale_each_location(self):
        # B3 round 2: the effort-suffixed stale spelling travels the same matrix
        # as the bare one - `cx/gpt-5.6-sol-high` must be as stale as `cx/gpt-5.6-sol`.
        for stale in ('cx/gpt-' + '5.6-sol', 'cx/gpt-' + '5.6-sol-high'):
            for location in LOCATIONS:
                with self.subTest(stale=stale, location=location):
                    self.assertTrue(_newest_problems(_reg_with(location, stale)))

    def test_whole_token_ids_reject_a_trailing_extra_token(self):
        """A1 (MED): the sol id is matched as ONE whole token.

        The round-1 regex searched inside a value, so `gpt-6.1-sol-max` inside
        `gpt-6.1-sol-max-extra` matched an allowed id and the extra token rode
        through. The allow-list decision is made on the whole value now.
        """
        for value in ('omniroute/cx-gpt-6.1-sol-max-extra',
                      'gpt-6.1-sol-max-extra',
                      'cx/gpt-6.1-sol-max-extra'):
            for location in LOCATIONS:
                with self.subTest(value=value, location=location):
                    self.assertTrue(_newest_problems(_reg_with(location, value)))

    def test_any_codex_prefix_id_is_current_or_an_effort_suffix(self):
        """B1 (MED): a codex-prefixed id naming another model is refused even
        though it never contains the word `sol` - `omniroute/cx-gpt-6.1-mini`
        used to walk past the check entirely."""
        for value in ('omniroute/cx-gpt-6.1-mini',
                      'cx/gpt-6.1-mini',
                      'cxa/gpt-6.1-mini'):
            for location in LOCATIONS:
                with self.subTest(value=value, location=location):
                    self.assertTrue(_newest_problems(_reg_with(location, value)))

    def test_ids_are_compared_case_insensitively(self):
        """B2 (MED): case never carries meaning - a stale id spelled in any
        case is still stale, and a current id in another case is still current."""
        for value in ('CX/GPT-5.6-SOL',
                      'GPT-5.6-SOL',
                      'OMNIROUTE/CX-GPT-5.6-SOL'):
            for location in LOCATIONS:
                with self.subTest(value=value, location=location):
                    self.assertTrue(_newest_problems(_reg_with(location, value)))
        # The mirror image: the current id in another case is not a finding.
        for value in ('CX/GPT-6.1-SOL', 'cx/GPT-6.1-SOL'):
            with self.subTest(current=value):
                self.assertFalse(_newest_problems(_reg_with('pins', value)))

    def test_policy_and_seats(self):
        reg = registry.load(registry.DEFAULT_REGISTRY_PATH)
        policy = reg['providers']['cx']['model_policy']
        leg = 'cx/' + policy['current']
        self.assertTrue(policy['newest_only'])
        self.assertEqual(reg['models'][policy['current']]['family'], 'openai')
        self.assertFalse(reg['clients']['codex']['available'])
        self.assertFalse(reg['providers']['cxa']['available'])
        self.assertEqual(reg['policy']['writers']['R2'][0]['leg'], leg)
        # Router option B: the seat list keeps the google seat first; cx is carried, not first.
        self.assertEqual(reg['policy']['reviewers'][0]['family'], 'google')
        self.assertIn(leg, [row.get('leg') for row in reg['policy']['reviewers'][1:]])

    def test_numeric_versions_and_proposals(self):
        spec = importlib.util.spec_from_file_location('newest', ROOT / 'tools/codex-newest-check.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertGreater(mod.version('cx/gpt-6.10-sol'), mod.version('cx/gpt-6.9-sol'))
        for model, expected in [('6.2', 3), ('6.1', 0), ('5.6', 0)]:
            code, text = mod.proposal(['cx/gpt-' + model + '-sol'], 'gpt-6.1-sol')
            self.assertEqual(code, expected)
            self.assertEqual(bool(text), expected == 3)

    def test_mcp_registry_availability(self):
        import autoos_agent_mcp
        reg = registry.load(registry.DEFAULT_REGISTRY_PATH)
        row = next(row for row in autoos_agent_mcp.list_clients()['clients'] if row['name'] == 'codex')
        self.assertEqual(row['available'], reg['clients']['codex']['available'])
        self.assertFalse(row['usable'])

    def test_exactly_one_codex_model_row(self):
        """B3: the codex side of the catalog is ONE model - `cx/gpt-6.1-sol`,
        family openai - so a second codex model cannot appear without both this
        count and the newest-only walk moving."""
        reg = registry.load(registry.DEFAULT_REGISTRY_PATH)
        policy = reg['providers']['cx']['model_policy']
        openai = sorted(key for key, row in reg['models'].items()
                        if row.get('family') == 'openai')
        self.assertEqual(openai, [policy['current']])
        sol_models = sorted(key for key in reg['models'] if registry.parse_sol_id(key))
        self.assertEqual(sol_models, ['gpt-6.1-sol'])
        current_row = reg['models'][policy['current']]
        self.assertEqual('cx/' + current_row['id'], 'cx/gpt-6.1-sol')

    def test_one_sol_id_grammar_helper(self):
        """B5: `registry.parse_sol_id` is the ONE sol-id grammar.

        The schema's `model_policy.current` pattern and the weekly probe point
        at it instead of carrying a second copy of the regex.
        """
        self.assertEqual(registry.parse_sol_id('gpt-6.1-sol'),
                         ('', (6, 1, 0, 0, 0, 0, 0, 0), ''))
        self.assertEqual(registry.parse_sol_id('omniroute/cx-gpt-6.10-sol-high'),
                         ('omniroute/cx-', (6, 10, 0, 0, 0, 0, 0, 0), 'high'))
        self.assertEqual(registry.parse_sol_id('CX/GPT-6.1-SOL'),
                         ('cx/', (6, 1, 0, 0, 0, 0, 0, 0), ''))
        self.assertEqual(registry.parse_sol_id('cx/gpt-6.1-sol-max-extra')[2],
                         'max-extra')
        for junk in ('gpt-6.1-mini', 'openrouter/openai/gpt-oss-120b', '', None, 7):
            self.assertIsNone(registry.parse_sol_id(junk), junk)
        probe = (ROOT / 'tools/codex-newest-check.py').read_text(encoding='utf-8')
        self.assertIn('registry.parse_sol_id', probe)
        self.assertNotIn('(?:cx/)?gpt-', probe)  # the probe's own grammar is gone
        schema = json.loads((ROOT / 'catalog/ai-registry.schema.json').read_text(encoding='utf-8'))
        current = schema['$defs']['provider']['properties']['model_policy']['properties']['current']
        self.assertEqual(current['pattern'], '^gpt-\\d+(\\.\\d+)*-sol$')
        self.assertIn('parse_sol_id', current.get('$comment', ''))

    def test_probe_docstring_documents_every_exit_code(self):
        """A2/B6: 0, 1 and 3 are all documented, and 3 stays distinct from 1 -
        3 = a human must review a proposal, 1 = the probe answered nothing."""
        doc = _load_newest_tool().__doc__
        for marker in ('Exit 0', 'Exit 1', 'Exit 3'):
            self.assertIn(marker, doc)
        self.assertIn('proposal', doc.lower())

    def test_proposal_path_is_git_ignored(self):
        """B4: the weekly proposal is review output - printed to stdout and
        written to a git-ignored path, so no `git add -A` can sweep it in."""
        mod = _load_newest_tool()
        self.assertEqual(mod.PROPOSAL_REL, 'scratch/codex-newest-proposal.md')
        rc = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', mod.PROPOSAL_REL],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        self.assertEqual(rc, 0, '%s must be git-ignored' % mod.PROPOSAL_REL)

    def test_mcp_row_available_matches_the_registry_for_every_client(self):
        """B7: every client's row carries the registry's own availability answer
        - the one the CLIENTAVAIL spawn gate and the resolver's _client_reason
        give - never a per-row default."""
        import autoos_agent_mcp
        reg = registry.load(registry.DEFAULT_REGISTRY_PATH)
        now = datetime.datetime.now(datetime.timezone.utc)
        rows = {row['name']: row for row in autoos_agent_mcp.list_clients()['clients']}
        self.assertEqual(sorted(rows), sorted(reg['clients']))
        for name, entry in reg['clients'].items():
            with self.subTest(client=name):
                expected = not (registry.unavailable_now(entry, now)
                                or entry.get('available') is False)
                self.assertEqual(rows[name]['available'], expected)

    def test_mcp_row_available_follows_the_entry_not_a_default(self):
        """B7: an `unavailable_until` that still runs is unavailable even on an
        entry carrying no `available` flag of its own, and a row may never
        claim a client the spawn gate would refuse."""
        import autoos_agent_mcp
        doc = {'clients': {'qwen': {'unavailable_until': '2099-01-01T00:00:00Z'},
                           'codex': {'available': False},
                           'agy': {}}}
        rows = {row['name']: row
                for row in autoos_agent_mcp.list_clients(registry_doc=doc)['clients']}
        self.assertFalse(rows['qwen']['available'])
        self.assertFalse(rows['codex']['available'])
        self.assertTrue(rows['agy']['available'])

    def test_providers_cx_comment_names_the_client_and_policy_home(self):
        """B9: one sentence in providers.cx.$comment saying the codex client IS
        OmniRoute provider cx and that the newest-only policy lives there."""
        reg = registry.load(registry.DEFAULT_REGISTRY_PATH)
        comment = reg['providers']['cx']['$comment']
        self.assertIn('OmniRoute provider cx', comment)
        self.assertIn('providers.cx.model_policy', comment)


if __name__ == '__main__':
    unittest.main()
