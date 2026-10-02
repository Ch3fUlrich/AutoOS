"""A malformed port in a host URL is reported, never crashes the renderer
(DeepSeek review 2026-09-25; fix written by a free gateway worker)."""
import importlib
import unittest

import importlib.util, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import json, re

spec = importlib.util.spec_from_file_location('render_mod', os.path.join(ROOT, 'tools', 'render-opencode-container-config.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
spec_agent = importlib.util.spec_from_file_location('agent_mod', os.path.join(ROOT, 'tools', 'autoos-agent.py'))
agent_module = importlib.util.module_from_spec(spec_agent)
spec_agent.loader.exec_module(agent_module)

def _iter_strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)
    elif isinstance(obj, str):
        yield obj

class TestRenderOpencodeConfig(unittest.TestCase):
    def test_with_host_malformed_port(self):
        url = 'http://127.0.0.1:abc/'
        result = module.with_host(url, 'host')
        self.assertEqual(result, url, "URL should be unchanged for malformed port")

    def test_rewrite_providers_malformed_port(self):
        # Provider block with malformed port URL
        block = {
            'badprov': {
                'options': {'baseURL': 'http://127.0.0.1:xyz/'},
                'settings': {}
            }
        }
        notes = []
        result = module.rewrite_providers(block, 'http://omniroute:20128', notes, 'V1')
        # Should leave the provider unchanged
        self.assertIn('badprov', result)
        self.assertEqual(result['badprov']['options']['baseURL'], 'http://127.0.0.1:xyz/')
        # Should have a warning note about malformed URL
        self.assertTrue(any('ignored malformed URL' in n for n in notes), "Expected a malformed URL warning")

    def test_fleet_pins_apply_and_are_idempotent(self):
        """Operator 2026-09-30: deepseek effort ladder, vertex models and the
        fleet agent models survive `init`; a second render adds nothing."""
        cfg = {
            'agent': {
                'orchestrator': {'mode': 'primary', 'model': 'meta/muse-spark-1.3-contributor'},
                'suborchestrator': {'mode': 'subagent', 'model': 'meta/muse-spark-1.3-contributor'},
                'leaf-implementer': {'mode': 'subagent', 'model': 'meta/muse-spark-1.3-contributor'},
                'leaf-reviewer': {'mode': 'subagent', 'model': 'deepseek/deepseek-v4-flash'},
            },
            'providers': {
                'omniroute': {
                    'models': {
                        'deepseek-v4.1-flash': {
                            'modelID': 'deepseek-v4.1-flash',
                            'limit': {'context': 131072, 'output': 32768},
                        }
                    }
                }
            },
        }
        notes = []
        module.pin_fleet_overrides(cfg, notes)
        models = cfg['providers']['omniroute']['models']
        self.assertEqual([v['id'] for v in models['deepseek-v4.1-flash']['variants']],
                         ['low', 'high', 'max'])
        self.assertEqual(models['deepseek-v4.1-flash']['limit']['context'], 1048576)
        for mid in module.FLEET_ALIAS_MODELS:
            self.assertIn(mid, models)
        self.assertEqual(cfg['agent']['orchestrator']['model'],
                         'omniroute/deepseek-v4.1-flash')
        # Operator 2026-09-30: vertex/claude-sonnet-4-5 answers 501 on the live
        # gateway, so the reviewer defaults to a working other-family leg.
        self.assertEqual(cfg['agent']['leaf-reviewer']['model'],
                         'omniroute/nemotron-3-ultra-free')
        self.assertEqual(cfg['agent']['suborchestrator']['mode'], 'primary')
        second = []
        module.pin_fleet_overrides(cfg, second)
        self.assertEqual(second, [], "second render must be a no-op")

    def _render_minimal(self):
        cfg = {
            'agent': {
                'leaf-implementer': {'mode': 'subagent', 'model': 'meta/muse-spark-1.3-contributor'},
            },
            'providers': {'omniroute': {'models': {}}},
        }
        module.pin_fleet_overrides(cfg, [])
        return cfg

    def test_d255_rendered_config_carries_no_refused_gemini_id(self):
        """D-255: every Gemini id in the rendered container config must be one
        tools/autoos-agent.gemini_model_allowed() accepts."""
        cfg = self._render_minimal()
        seen = [s for s in _iter_strings(cfg) if 'gemini' in s.lower()]
        self.assertTrue(seen, "expected Gemini ids in the render")
        for s in seen:
            self.assertTrue(agent_module.gemini_model_allowed(s),
                            "D-255 refused Gemini id %r in render:\n%s" % (s, json.dumps(cfg)))

    def test_d255_leaf_implementer_resolves_to_flash_leg(self):
        cfg = self._render_minimal()
        self.assertEqual(cfg['agent']['leaf-implementer']['model'],
                         'omniroute/vertex-gemini-3.8-flash')
        m = cfg['providers']['omniroute']['models']['vertex-gemini-3.8-flash']
        self.assertEqual(m['modelID'], 'vertex/gemini-3.8-flash')

    def test_d255_fleet_tables_have_no_pro_gemini_ids(self):
        pro = re.compile(r'(?:^|-)pro', re.I)
        for table in (module.FLEET_VERTEX_MODELS, module.FLEET_AGENT_MODELS):
            for key, value in table.items():
                vals = [key, value] + (list(value) if isinstance(value, tuple) else [])
                for s in vals:
                    if isinstance(s, str) and 'gemini' in s.lower():
                        tail = s.lower()[s.lower().index('gemini'):]
                        self.assertFalse(pro.search(tail),
                                         "D-255: 'pro' Gemini id %r in fleet table" % s)
                        self.assertTrue(agent_module.gemini_model_allowed(s),
                                        "D-255: refused Gemini id %r in fleet table" % s)

if __name__ == '__main__':
    unittest.main()
