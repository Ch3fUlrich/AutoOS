"""Newest-only contract: synthetic stale ids deliberately assembled, not routes."""
import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import registry


class NewestOnlyTests(unittest.TestCase):
    def test_stale_each_location(self):
        stale = 'cx/gpt-' + '5.6-sol'
        for location in ('models', 'routes', 'pins', 'reviewers', 'writers',
                         'route_order', 'free_client_models'):
            with self.subTest(location=location):
                reg = copy.deepcopy(registry.load(registry.DEFAULT_REGISTRY_PATH))
                if location in ('models', 'routes'):
                    reg[location]['stale'] = {'id': stale, 'legs': [stale]}
                elif location == 'pins':
                    reg['pins'] = [stale]
                else:
                    reg['policy'][location] = [stale]
                self.assertTrue(any('newest_only' in p for p in registry.check_registry(reg)))

    def test_policy_and_seats(self):
        reg = registry.load(registry.DEFAULT_REGISTRY_PATH)
        policy = reg['providers']['cx']['model_policy']
        leg = 'cx/' + policy['current']
        self.assertTrue(policy['newest_only'])
        self.assertEqual(reg['models'][policy['current']]['family'], 'openai')
        self.assertFalse(reg['clients']['codex']['available'])
        self.assertFalse(reg['providers']['cxa']['available'])
        self.assertEqual(reg['policy']['writers']['R2'][0]['leg'], leg)
        self.assertEqual(reg['policy']['reviewers'][0]['leg'], leg)

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


if __name__ == '__main__':
    unittest.main()
