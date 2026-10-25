import contextlib
import hashlib
import io
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import api_client as api
import autoos_tokens as tokens

VALID_MODEL = 'deepseek-v4.1-flash'
DEFAULT_MODEL = 'deepseek-v4.1-flash'

class TestApiClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.outside_tmp = tempfile.TemporaryDirectory()
        self.outside = Path(self.outside_tmp.name).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()
        self.outside_tmp.cleanup()

    def _model_matches(self, text):
        return re.fullmatch(api.MODEL_ID_RE, text)

    def _symlink(self, target, link):
        src = str(target)
        dst = str(link)
        try:
            os.symlink(src, dst, target_is_directory=os.path.isdir(src))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'symlink unavailable: {exc}')

    def _make_prompt(self, name='prompt.md'):
        p = self.root / name
        p.write_text('hello', encoding='utf-8')
        return p

    def _make_out(self, name='out'):
        o = self.root / name
        o.mkdir(parents=True, exist_ok=True)
        return o

    def _expected_id(self, task, now_hex=None):
        ident = 'a' + hashlib.sha256(task.encode('utf-8')).hexdigest()[:12]
        if now_hex is not None:
            ident += '-' + now_hex
        return ident

    def test_api_models_contains_allowed_and_excludes_forbidden(self):
        self.assertIsInstance(api.API_MODELS, tuple)
        self.assertIn('deepseek-v4.1-flash', api.API_MODELS)
        self.assertIn('ovh/gpt-oss-120b', api.API_MODELS)
        self.assertNotIn('bzl/deepseek/deepseek-v4-flash-0731free:free', api.API_MODELS)
        for m in api.API_MODELS:
            self.assertIsInstance(m, str)
            self.assertIsNotNone(self._model_matches(m))

    def test_max_tokens_cap(self):
        self.assertEqual(api.MAX_TOKENS_CAP, 128000)

    def test_build_argv_clamps_to_model_ceiling(self):
        prompt = self._make_prompt()
        out = self._make_out()
        # ovh/gpt-oss-120b has ceiling 32768 in catalog/ai-registry.json
        argv = api.build_argv('ovh/gpt-oss-120b', str(prompt), str(out), str(self.root))
        self.assertEqual(argv[11], '32768')
        # qoder/glm-5.2 has ceiling 16384
        argv_q = api.build_argv('qoder/glm-5.2', str(prompt), str(out), str(self.root))
        self.assertEqual(argv_q[11], '16384')
        # No clamp below ceiling
        argv_low = api.build_argv('ovh/gpt-oss-120b', str(prompt), str(out), str(self.root), max_tokens=1000)
        self.assertEqual(argv_low[11], '1000')

    def test_model_id_re_charset(self):
        self.assertIsNotNone(self._model_matches('abc.DEF-123_:/'))
        self.assertIsNone(self._model_matches('bad model'))
        self.assertIsNone(self._model_matches('bad\nmodel'))
        self.assertIsNone(self._model_matches('bad$model'))

    def test_model_id_re_length(self):
        self.assertIsNotNone(self._model_matches('a' * 100))
        self.assertIsNone(self._model_matches('a' * 101))
        self.assertIsNone(self._model_matches(''))

    def test_confine_happy_and_root_equal(self):
        p = self._make_prompt()
        got = api.confine(str(p), str(self.root))
        self.assertIsInstance(got, str)
        self.assertEqual(got, os.path.realpath(str(p)))
        self.assertEqual(api.confine(str(self.root), str(self.root)), os.path.realpath(str(self.root)))

    def test_confine_rejects_parent_escape(self):
        with self.assertRaises(ValueError):
            api.confine(str(self.root / '..' / 'x'), str(self.root))
        with self.assertRaises(ValueError):
            api.confine(str(self.root / '..' / '..' / 'x'), str(self.root))

    def test_confine_rejects_absolute_elsewhere(self):
        with self.assertRaises(ValueError):
            api.confine(str(self.outside / 'x'), str(self.root))

    def test_confine_rejects_empty_and_nul(self):
        with self.assertRaises(ValueError):
            api.confine('', str(self.root))
        with self.assertRaises(ValueError):
            api.confine('bad\x00path', str(self.root))

    def test_confine_rejects_symlink_escape(self):
        outside_file = self.outside / 'secret.txt'
        outside_file.write_text('x', encoding='utf-8')
        link = self.root / 'link.txt'
        self._symlink(outside_file, link)
        with self.assertRaises(ValueError):
            api.confine(str(link), str(self.root))

    def test_confine_rejects_symlink_inside_root(self):
        target = self.root / 'target.txt'
        target.write_text('x', encoding='utf-8')
        link = self.root / 'link.txt'
        self._symlink(target, link)
        with self.assertRaises(ValueError):
            api.confine(str(link), str(self.root))

    def test_confine_rejects_symlink_component_inside_root(self):
        sub = self.root / 'sub'
        sub.mkdir()
        (sub / 'f.txt').write_text('x', encoding='utf-8')
        linkdir = self.root / 'linkdir'
        self._symlink(sub, linkdir)
        with self.assertRaises(ValueError):
            api.confine(str(linkdir / 'f.txt'), str(self.root))

    def test_build_argv_shape_defaults(self):
        prompt = self._make_prompt()
        out = self._make_out()
        argv = api.build_argv(VALID_MODEL, str(prompt), str(out), str(self.root))
        expected_script = os.path.join(os.path.dirname(os.path.realpath(api.__file__)), 'review-call.py')
        expected = [
            'python', expected_script, '--model', VALID_MODEL,
            '--prompt-file', os.path.realpath(str(prompt)),
            '--out-dir', os.path.realpath(str(out)),
            '--title', 'api', '--max-tokens', '128000',
        ]
        self.assertEqual(argv, expected)
        self.assertEqual(len(argv), 12)

    def test_build_argv_custom_script_title_tokens_paths(self):
        prompt = self._make_prompt('custom.md')
        out = self._make_out('custom_out')
        argv = api.build_argv(
            VALID_MODEL, str(prompt), str(out), str(self.root),
            title='my-title_1', max_tokens=1234, python='py3', script='/tmp/review.py',
        )
        self.assertEqual(argv[0], 'py3')
        self.assertEqual(argv[1], '/tmp/review.py')
        self.assertEqual(argv[3], VALID_MODEL)
        self.assertEqual(argv[5], os.path.realpath(str(prompt)))
        self.assertEqual(argv[7], os.path.realpath(str(out)))
        self.assertEqual(argv[9], 'my-title_1')
        self.assertEqual(argv[11], '1234')
        self.assertEqual(len(argv), 12)

    def test_build_argv_rejects_bad_models(self):
        prompt = self._make_prompt()
        out = self._make_out()
        for bad in ('-x', 'evil/model', '', 'deepseek-v4.1-flash --foo',
                    'deepseek-v4.1-flash ', 'deepseek-v4.1-flash\n'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    api.build_argv(bad, str(prompt), str(out), str(self.root))

    def test_build_argv_rejects_bad_max_tokens(self):
        prompt = self._make_prompt()
        out = self._make_out()
        for bad in (True, False, '128000', 128000.0, 0, -1, 128001):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    api.build_argv(VALID_MODEL, str(prompt), str(out), str(self.root), max_tokens=bad)

    def test_build_argv_rejects_bad_title(self):
        prompt = self._make_prompt()
        out = self._make_out()
        for bad in ('', 'bad title', 'bad/title', 'x' * 65, 'bad\n'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    api.build_argv(VALID_MODEL, str(prompt), str(out), str(self.root), title=bad)

    def test_build_argv_rejects_escapes(self):
        out = self._make_out()
        prompt = self._make_prompt()
        with self.assertRaises(ValueError):
            api.build_argv(VALID_MODEL, str(self.root / '..' / 'p.md'), str(out), str(self.root))
        with self.assertRaises(ValueError):
            api.build_argv(VALID_MODEL, str(prompt), str(self.outside / 'out'), str(self.root))

    def test_build_command_api_shape_and_files(self):
        env = {'HOME': str(self.root)}
        task = 'do the thing'
        ident = self._expected_id(task)
        pool = self.root / '.local' / 'state' / 'autoos' / 'pool'
        prompt = pool / 'api-prompts' / (ident + '.md')
        out_dir = pool / 'api-out' / ident
        argv = api.build_command_api(task, VALID_MODEL, env)
        self.assertEqual(len(argv), 12)
        self.assertEqual(argv[0], 'python')
        self.assertEqual(argv[2], '--model')
        self.assertEqual(argv[3], VALID_MODEL)
        self.assertEqual(argv[4], '--prompt-file')
        self.assertEqual(os.path.realpath(argv[5]), os.path.realpath(str(prompt)))
        self.assertEqual(argv[6], '--out-dir')
        self.assertEqual(os.path.realpath(argv[7]), os.path.realpath(str(out_dir)))
        self.assertEqual(argv[8], '--title')
        self.assertEqual(argv[9], ident)
        self.assertEqual(argv[10], '--max-tokens')
        self.assertEqual(argv[11], '128000')
        self.assertTrue(prompt.exists(), prompt)
        self.assertEqual(prompt.read_text(encoding='utf-8'), task)
        self.assertTrue(out_dir.is_dir())
        self.assertEqual(os.path.commonpath([str(pool.resolve()), str(out_dir.resolve())]), str(pool.resolve()))

    def test_build_command_api_prompt_mode_posix(self):
        env = {'HOME': str(self.root)}
        task = 'mode test'
        ident = self._expected_id(task)
        pool = self.root / '.local' / 'state' / 'autoos' / 'pool'
        prompt = pool / 'api-prompts' / (ident + '.md')
        api.build_command_api(task, VALID_MODEL, env)
        self.assertTrue(prompt.exists())
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(os.stat(prompt).st_mode), 0o600)

    def test_build_command_api_rejects_bad_model_no_prompt(self):
        env = {'HOME': str(self.root)}
        task = 'bad model task'
        ident = self._expected_id(task)
        pool = self.root / '.local' / 'state' / 'autoos' / 'pool'
        prompt = pool / 'api-prompts' / (ident + '.md')
        with self.assertRaises(ValueError):
            api.build_command_api(task, 'evil/model', env)
        self.assertFalse(prompt.exists(), prompt)

    def test_build_command_api_distinct_tasks_and_no_task_in_argv(self):
        env = {'HOME': str(self.root)}
        task1 = 'task one'
        task2 = 'task two'
        argv1 = api.build_command_api(task1, VALID_MODEL, env)
        argv2 = api.build_command_api(task2, VALID_MODEL, env)
        self.assertNotEqual(argv1[9], argv2[9])
        self.assertNotEqual(self._expected_id(task1), self._expected_id(task2))
        secret = 'UNIQUE_SECRET_TEXT_12345'
        argv3 = api.build_command_api(secret, VALID_MODEL, env)
        self.assertNotIn(secret, argv3)
        self.assertFalse(any(secret in str(x) for x in argv3))

    def test_build_command_api_now_hex_and_default_model(self):
        env = {'HOME': str(self.root)}
        task = 'hex task'
        ident = self._expected_id(task, now_hex='deadbeef')
        pool = self.root / '.local' / 'state' / 'autoos' / 'pool'
        prompt = pool / 'api-prompts' / (ident + '.md')
        argv = api.build_command_api(task, None, env, now_hex='deadbeef')
        self.assertEqual(argv[3], DEFAULT_MODEL)
        self.assertEqual(argv[9], ident)
        self.assertTrue(prompt.exists())
        self.assertEqual(prompt.read_text(encoding='utf-8'), task)

    def test_build_command_api_localappdata_precedence(self):
        local = self.root / 'localappdata'
        home = self.root / 'home'
        env = {'LOCALAPPDATA': str(local), 'HOME': str(home)}
        task = 'precedence'
        ident = self._expected_id(task)
        pool = local / 'autoos' / 'pool'
        prompt = pool / 'api-prompts' / (ident + '.md')
        argv = api.build_command_api(task, None, env)
        self.assertTrue(prompt.exists())
        self.assertEqual(os.path.realpath(argv[5]), os.path.realpath(str(prompt)))
        self.assertFalse((home / '.local' / 'state' / 'autoos' / 'pool' / 'api-prompts' / (ident + '.md')).exists())

class TestAutoosTokensRegistrySeam(unittest.TestCase):
    """autoos_tokens' registry seam: a registry dict the caller supplied is a
    TEST seam for clamp_max_tokens/resolve_output_ceiling. A dict with no (or a
    junk) models table means "output_max unknown" - never a crash and never a
    lower cap (D-2694: unknown output_max -> send the 128000 default and log)."""

    def test_registry_dict_without_a_models_table_is_unknown_not_a_crash(self):
        self.assertIsNone(tokens.resolve_output_ceiling('m/x', registry={}))
        self.assertIsNone(tokens.resolve_output_ceiling('m/x',
                                                        registry={'models': None}))
        self.assertIsNone(tokens.resolve_output_ceiling('m/x',
                                                        registry={'models': 'junk'}))

    def test_registry_models_table_still_resolves_rows(self):
        registry = {'models': {'x': {'output_max': 5}}}
        self.assertEqual(tokens.resolve_output_ceiling('m/x', registry=registry), 5)
        self.assertEqual(tokens.clamp_max_tokens('m/x', None, registry=registry), 5)

    def test_clamp_with_registry_missing_models_sends_the_default_and_logs(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = tokens.clamp_max_tokens('m/x', None, registry={})
        self.assertEqual(got, tokens.DEFAULT_MAX_TOKENS)
        self.assertEqual(got, 128000)  # D-2694: never invent a lower cap
        self.assertIn('unknown output_max for m/x', err.getvalue())

if __name__ == '__main__':
    unittest.main()
