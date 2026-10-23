import unittest
import io
import json
import os
import tempfile
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import runner_pool_gate as gate
import runner_pool_core as rpc  # used for strip_env in gate_env tests

class RecordingStream:
    def __init__(self):
        self.recorded = None

    def read(self, n):
        self.recorded = n
        return b'x' * n

class TestGate(unittest.TestCase):
    # ---------- parse_original_command ----------
    def test_parse_original_command_valid(self):
        self.assertEqual(gate.parse_original_command('submit'), 'submit')
        self.assertEqual(gate.parse_original_command('status --json'), 'status')

    def test_parse_original_command_invalid(self):
        bad = [
            None, '', ' submit', 'submit ', 'submit -x',
            'status', 'status --json --x', 'bash',
            'submit; id', 'submit\nid', 'scp -t /tmp',
            'sftp', 'status  --json', 'SUBMIT'
        ]
        for s in bad:
            with self.subTest(s=s):
                with self.assertRaises(gate.GateError):
                    gate.parse_original_command(s)

    # ---------- valid_id ----------
    def test_valid_id(self):
        self.assertTrue(gate.valid_id('job-1_A'))
        self.assertTrue(gate.valid_id('a' * 64))
        self.assertTrue(gate.valid_id('-x'))  # allowed by rule
        self.assertFalse(gate.valid_id(''))
        self.assertFalse(gate.valid_id('a' * 65))
        self.assertFalse(gate.valid_id('../x'))
        self.assertFalse(gate.valid_id('a/b'))
        self.assertFalse(gate.valid_id('a b'))
        self.assertFalse(gate.valid_id('x\n'))

    # ---------- read_capped ----------
    def test_read_capped_over_limit(self):
        r = RecordingStream()
        with self.assertRaises(gate.GateError):
            gate.read_capped(r, cap=65536)
        self.assertEqual(r.recorded, 65537)

    def test_read_capped_exact_limit(self):
        data = b'x' * 65536
        stream = io.BytesIO(data)
        result = gate.read_capped(stream, cap=65536)
        self.assertEqual(result, data)

    # ---------- path validation ----------
    def test_path_plain_relative_paths(self):
        for path in ('src/a.py', 'docs/x.md', './a'):
            with self.subTest(path=path):
                self.assertIsNone(gate._check_path(path))
                card = {'paths': [path]}
                self.assertEqual(gate.validate_card(card), card)

    def test_path_invalid_windows_characters(self):
        bad_paths = [
            'src/a.py:stream', 'a/b:c', 'a/b:c/d', 'a:b',
            'x\x00y', 'x\ny', 'a|b', 'a*b', 'a?b', 'a<b', 'a>b', 'a"b',
        ] + ['x' + chr(code) + 'y' for code in (*range(32), 127)]
        for path in bad_paths:
            for validate in (gate._check_path,
                             lambda value: gate.validate_card({'paths': [value]})):
                with self.subTest(path=path, validate=validate):
                    with self.assertRaisesRegex(gate.GateError, '^invalid path$'):
                        validate(path)

    # ---------- validate_card ----------
    def test_validate_card_valid(self):
        card = {
            'client': 'cli',
            'model': 'm',
            'kind': 'k',
            'task_type': 't',
            'title': 'title',
            'paths': ['path1', 'path2'],
            'spec': 'spec'
        }
        self.assertEqual(gate.validate_card(card), card)

    def test_validate_card_invalid_key(self):
        bad = {'host': 'workstation'}
        with self.assertRaises(gate.GateError):
            gate.validate_card(bad)

    def test_validate_card_unknown_key(self):
        bad = {'unknown': 'x'}
        with self.assertRaises(gate.GateError):
            gate.validate_card(bad)

    def test_validate_card_bad_value(self):
        bad = {'client': '-bad'}
        with self.assertRaises(gate.GateError):
            gate.validate_card(bad)

        bad2 = {'paths': ['-bad', 'ok']}
        with self.assertRaises(gate.GateError):
            gate.validate_card(bad2)

    def test_validate_card_oversize(self):
        big_spec = 'x' * 9000
        card = {'spec': big_spec}
        with self.assertRaises(gate.GateError):
            gate.validate_card(card)

    def test_validate_card_not_dict(self):
        with self.assertRaises(gate.GateError):
            gate.validate_card(123)

    def test_validate_card_bad_type(self):
        card = {'client': 5}
        with self.assertRaises(gate.GateError):
            gate.validate_card(card)

    # ---------- parse_submit ----------
    def test_parse_submit_success(self):
        raw = json.dumps({'card': {'client': 'c'}, 'brief': 'hello'}).encode()
        result = gate.parse_submit(
            raw,
            submitter='lane',
            now=1234567890,
            rand='abcd'
        )
        self.assertEqual(result['id'], 'j1234567890-abcd')
        self.assertTrue(gate.valid_id(result['id']))
        self.assertEqual(result['submitter'], 'lane')
        self.assertEqual(result['brief'], 'hello')
        self.assertIn('card', result)
        self.assertIn('submitted_at', result)

    def test_parse_submit_invalid_json(self):
        raw = b'{'  # invalid JSON
        with self.assertRaises(gate.GateError):
            gate.parse_submit(raw, submitter='lane')

    def test_parse_submit_extra_keys(self):
        raw = json.dumps({'card': {}, 'brief': 'b', 'extra': 1}).encode()
        with self.assertRaises(gate.GateError):
            gate.parse_submit(raw, submitter='lane')

    def test_parse_submit_invalid_brief_path(self):
        bad_briefs = ['/abs', '~user', '.', '\\win', 'C:\\temp', 'C:/temp']
        for b in bad_briefs:
            with self.subTest(b=b):
                raw = json.dumps({'card': {}, 'brief': b}).encode()
                with self.assertRaises(gate.GateError):
                    gate.parse_submit(raw, submitter='lane')

    def test_parse_submit_invalid_submitter(self):
        raw = json.dumps({'card': {}, 'brief': 'hi'}).encode()
        with self.assertRaises(gate.GateError):
            gate.parse_submit(raw, submitter='-bad')

    # ---------- authorized_keys_line ----------
    def test_authorized_keys_line(self):
        lane = 'mylane'
        pub = 'aGVsbG9wdWJrZXk='  # base64 dummy
        expected = (
            'restrict,command="C:\\\\Python\\\\python.exe C:\\\\autoos\\\\tools\\\\runner_pool.py '
            'ssh-gate --submitter mylane" ssh-ed25519 aGVsbG9wdWJrZXk= mylane@coding'
        )
        self.assertEqual(gate.authorized_keys_line(lane, pub), expected)

    def test_authorized_keys_line_invalid_lane(self):
        with self.assertRaises(ValueError):
            gate.authorized_keys_line('bad lane', 'abcd')
        with self.assertRaises(ValueError):
            gate.authorized_keys_line('bad"quote', 'abcd')

    def test_authorized_keys_line_invalid_pubkey(self):
        with self.assertRaises(ValueError):
            gate.authorized_keys_line('lane', 'invalid!')

    # ---------- gate_env ----------
    def test_gate_env_strips(self):
        env = {'AUTOOS_ADMISSION_OFF': '1', 'OTHER': 'x'}
        result = gate.gate_env(env)
        self.assertNotIn('AUTOOS_ADMISSION_OFF', result)
        self.assertIn('OTHER', result)

    # ---------- run_gate ----------
    def run_gate_helper(self, argv, env, stdin_bytes, spool_dir):
        stdin = io.BytesIO(stdin_bytes)
        stdout = io.StringIO()
        code = gate.run_gate(argv, env.copy(), stdin, stdout, spool_dir)
        return code, stdout.getvalue()

    def test_run_gate_missing_command(self):
        env = {}
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, b'', td)
        self.assertEqual(code, 2)
        self.assertIn('rejected', out)
        self.assertFalse(os.path.isdir(os.path.join(td, 'queue')))

    def test_run_gate_invalid_submitter(self):
        env = {'SSH_ORIGINAL_COMMAND': 'submit'}
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_gate_helper(['--submitter', '-x'], env, b'{}', td)
        self.assertEqual(code, 2)
        self.assertIn('rejected', out)
        self.assertFalse(os.path.isdir(os.path.join(td, 'queue')))

    def test_run_gate_invalid_original_command(self):
        env = {'SSH_ORIGINAL_COMMAND': 'bash'}
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, b'{}', td)
        self.assertEqual(code, 2)
        self.assertIn('rejected', out)

    def test_run_gate_submit_success(self):
        card = {'client': 'c'}
        raw = json.dumps({'card': card, 'brief': 'hello'}).encode()
        env = {'SSH_ORIGINAL_COMMAND': 'submit'}
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, raw, td)
            self.assertEqual(code, 0)
            sid = out.strip()
            self.assertTrue(gate.valid_id(sid))
            qpath = pathlib.Path(td) / 'queue' / f'{sid}.json'
            self.assertTrue(qpath.is_file())
            content = json.loads(qpath.read_text())
            self.assertEqual(content['submitter'], 'lane')
            self.assertEqual(content['card'], card)

    def test_run_gate_submit_oversize(self):
        env = {'SSH_ORIGINAL_COMMAND': 'submit'}
        big = b'{' + b'x' * 65536  # 65537 bytes total
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, big, td)
            self.assertEqual(code, 2)
            self.assertIn('rejected', out)
            self.assertFalse(os.path.isdir(os.path.join(td, 'queue')))

    def test_run_gate_submit_extra_key(self):
        raw = json.dumps({'card': {}, 'brief': 'hi', 'extra': 1}).encode()
        env = {'SSH_ORIGINAL_COMMAND': 'submit'}
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, raw, td)
            self.assertEqual(code, 2)
            self.assertIn('rejected', out)
            self.assertFalse(os.path.isdir(os.path.join(td, 'queue')))

    def test_run_gate_status_with_file(self):
        status = {'slots': [], 'queue_depth': 0}
        with tempfile.TemporaryDirectory() as td:
            (pathlib.Path(td) / 'status.json').write_text(json.dumps(status))
            env = {'SSH_ORIGINAL_COMMAND': 'status --json'}
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, b'', td)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), status)

    def test_run_gate_status_without_file(self):
        default = {"slots": [], "queue_depth": 0, "oldest_wait_s": 0}
        with tempfile.TemporaryDirectory() as td:
            env = {'SSH_ORIGINAL_COMMAND': 'status --json'}
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, b'', td)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), default)

    def test_run_gate_autoos_off_ignored(self):
        env = {'SSH_ORIGINAL_COMMAND': 'submit', 'AUTOOS_ADMISSION_OFF': '1'}
        raw = json.dumps({'card': {}, 'brief': 'hi'}).encode()
        with tempfile.TemporaryDirectory() as td:
            code, out = self.run_gate_helper(['--submitter', 'lane'], env, raw, td)
            self.assertEqual(code, 0)  # still processes normally

    def test_valid_id_strict_charset(self):
        for bad in ('a;b', 'x\n', 'a$b', 'a.b', 'é'):
            self.assertFalse(gate.valid_id(bad), bad)

    def test_parse_submit_brief_text_rules(self):
        ok = json.dumps({'card': {}, 'brief': 'line one\nuse C:\\dir\\x in text'}).encode()
        self.assertEqual(gate.parse_submit(ok, submitter='lane1', now=1, rand='ab')['brief'][:8], 'line one')
        for bad in ('', '   ', 'D:\\temp\\x', 'C:/temp'):
            with self.assertRaises(gate.GateError):
                gate.parse_submit(json.dumps({'card': {}, 'brief': bad}).encode(), submitter='lane1', now=1, rand='ab')

if __name__ == '__main__':
    unittest.main()
