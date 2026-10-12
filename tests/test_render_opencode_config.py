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


def _model_entries_of(cfg):
    """(provider label, entry name, modelID) for every model row the rendered
    container config declares — both the {provider|providers: {name: {models}}}
    shape and the flat {models: {}} shape the renderer tolerates."""
    out = []
    for key in ('provider', 'providers'):
        block = cfg.get(key)
        if not isinstance(block, dict):
            continue
        if isinstance(block.get('models'), dict):
            for name, entry in block['models'].items():
                out.append((key, name, entry))
        for pname, prov in block.items():
            if isinstance(prov, dict) and isinstance(prov.get('models'), dict):
                for name, entry in prov['models'].items():
                    out.append(('%s.%s' % (key, pname), name, entry))
    return out


class TestRenderOpencodeConfig(unittest.TestCase):
    # D-1201 (operator 2026-10-12): Vertex AI and Google AI Studio are held off
    # everywhere, so no model id may resolve through them — and `gemini/` is the
    # native AI Studio provider spelling.
    OFF_MODEL_ID_PREFIXES = ('vertex/', 'google_ai_studio/', 'gemini/')

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
        """Operator 2026-09-30: deepseek effort ladder, the surviving non-Vertex
        fleet alias and the fleet agent models survive `init`; a second render
        adds nothing."""
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
                         # D657-D2 (AO-DENYLEGS D2, 2026-10-08): the paid
                         # deepseek combo the operator pinned on 2026-09-30
                         # renders nothing now (no probe-d657 gateway acked
                         # deepseek/deepseek-flash, so apply.sh prunes it), and
                         # an agent pointed at a pruned combo starts against an
                         # empty store. The L1 chain is the D-657 answer, and it
                         # is what tools/autoos-agent.py TIERS[1] spawns.
                         'omniroute/l1-orchestrator')
        # Operator 2026-09-30: the Vertex claude leg answered 501 on the live
        # gateway, and D-1201 (2026-10-12) holds Vertex off everywhere, so the
        # reviewer stays on a working other-family leg outside that provider.
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
        tools/autoos-agent.gemini_model_allowed() accepts. D-1201 (2026-10-12)
        left the fleet's own Gemini set empty — the renderer now adds no Gemini
        row at all — so this guard reads the render it finds rather than
        requiring one, and still fails a host source that reintroduces a refused
        id."""
        cfg = self._render_minimal()
        for s in [s for s in _iter_strings(cfg) if 'gemini' in s.lower()]:
            self.assertTrue(agent_module.gemini_model_allowed(s),
                            "D-255 refused Gemini id %r in render:\n%s" % (s, json.dumps(cfg)))

    def test_d1201_leaf_implementer_resolves_to_the_l3_implementer_combo(self):
        """D-1201: the implementer pin is a declared registry route that renders
        a live combo — not a Vertex model id."""
        cfg = self._render_minimal()
        pin = cfg['agent']['leaf-implementer']['model']
        self.assertEqual(pin, 'omniroute/l3-implementer')
        combo = pin.split('/', 1)[1]
        with open(os.path.join(ROOT, 'catalog', 'ai-registry.json'), encoding='utf-8') as fh:
            registry = json.load(fh)
        self.assertIn(combo, registry['routes'], "%s is no declared route" % combo)
        with open(os.path.join(ROOT, 'configuration', 'omniroute',
                               'combos.json'), encoding='utf-8') as fh:
            shipped = {c['name'] for c in json.load(fh)['combos']}
        self.assertIn(combo, shipped, "%s renders no combo" % combo)
        models = cfg['providers']['omniroute']['models']
        self.assertNotIn('vertex-gemini-3.8-flash', models)
        self.assertIn('nemotron-3-ultra-free', models,
                      "the non-Vertex fleet alias must keep being pinned")

    def test_d1201_rendered_container_config_declares_no_vertex_or_ai_studio_model(self):
        """D-1201 positive gate: neither the alias injection nor any fleet pin may
        put a Vertex / Google AI Studio model id into the container's opencode
        config — the second pinnable-Vertex path this closes."""
        src = {
            'agent': {name: {'mode': 'subagent'} for name in module.FLEET_AGENT_MODELS},
            'provider': {'omniroute': {'models': {}}},
            'providers': {'omniroute': {'models': {}}},
        }
        rendered, _notes = module.render(src, ROOT, 'http://omniroute:20128',
                                         'http://serena-mcp:9121/sse',
                                         'host.docker.internal')
        for cfg in (rendered, self._render_minimal()):
            offenders = []
            for label, name, entry in _model_entries_of(cfg):
                model_id = str(entry.get('modelID') if isinstance(entry, dict) else '')
                if model_id.startswith(self.OFF_MODEL_ID_PREFIXES):
                    offenders.append('%s.%s -> %s' % (label, name, model_id))
                if 'vertex' in name.lower() or 'ai_studio' in name.lower():
                    offenders.append('%s.%s names an off provider' % (label, name))
            self.assertEqual(offenders, [], json.dumps(cfg, indent=2))
        for name, model in module.FLEET_AGENT_MODELS.items():
            self.assertNotIn('vertex', model.lower(),
                             "D-1201: agent.%s pins an off-provider id %r" % (name, model))
            self.assertNotIn('ai_studio', model.lower(),
                             "D-1201: agent.%s pins an off-provider id %r" % (name, model))
        for alias, (model_id, _ctx) in module.FLEET_ALIAS_MODELS.items():
            self.assertFalse(model_id.startswith(self.OFF_MODEL_ID_PREFIXES),
                             "D-1201: alias %s -> %s" % (alias, model_id))
        self.assertEqual(module.FLEET_ALIAS_MODELS, module.FLEET_EXTRA_MODELS)
        self.assertFalse(hasattr(module, 'FLEET_VERTEX_MODELS'),
                         "D-1201: the Vertex alias table must be gone")

    def test_d255_fleet_tables_have_no_pro_gemini_ids(self):
        pro = re.compile(r'(?:^|-)pro', re.I)
        for table in (module.FLEET_ALIAS_MODELS, module.FLEET_AGENT_MODELS):
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
