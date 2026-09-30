"""A malformed port in a host URL is reported, never crashes the renderer
(DeepSeek review 2026-09-25; fix written by a free gateway worker)."""
import importlib
import unittest

import importlib.util, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('render_mod', os.path.join(ROOT, 'tools', 'render-opencode-container-config.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

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
        for mid in module.FLEET_VERTEX_MODELS:
            self.assertIn(mid, models)
        self.assertEqual(cfg['agent']['orchestrator']['model'],
                         'omniroute/deepseek-v4.1-flash')
        self.assertEqual(cfg['agent']['leaf-reviewer']['model'],
                         'omniroute/vertex-claude-sonnet-4-5')
        self.assertEqual(cfg['agent']['suborchestrator']['mode'], 'primary')
        second = []
        module.pin_fleet_overrides(cfg, second)
        self.assertEqual(second, [], "second render must be a no-op")

if __name__ == '__main__':
    unittest.main()
