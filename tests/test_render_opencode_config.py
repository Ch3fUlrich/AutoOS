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

if __name__ == '__main__':
    unittest.main()
