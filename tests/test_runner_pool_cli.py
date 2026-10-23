import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / 'tools'
sys.path.insert(0, str(TOOLS))

import runner_pool as pool
import runner_pool_proc as proc
import runner_pool_core as rpc
import runner_pool_gate as gate

class FakeDeps:
    def __init__(self):
        self.spawn_calls = []
    def spawn(self, *args, **kwargs):
        self.spawn_calls.append((args, kwargs))
        return 0
    def __getattr__(self, name):
        return lambda *a, **k: None

class TestCli(unittest.TestCase):
    def patch_run_one(self, result='idle'):
        calls = []
        def fake_run_one(root, k, deps):
            calls.append((root, k, deps))
            return result
        orig = pool.run_one
        pool.run_one = fake_run_one
        self.addCleanup(lambda: setattr(pool, 'run_one', orig))
        return calls

    def test_pid_alive_self(self):
        pid = os.getpid()
        self.assertTrue(proc.pid_alive(pid))
        ft = proc.filetime_of(pid)
        self.assertIsInstance(ft, int)
        self.assertGreater(ft, 0)
        self.assertEqual(ft, proc.filetime_of(pid))

    def test_pid_alive_dead(self):
        p = subprocess.Popen([sys.executable, '-c', 'pass'])
        p.wait()
        pid = p.pid
        self.assertFalse(proc.pid_alive(pid))
        self.assertIsNone(proc.filetime_of(pid))

    def test_popen_spawn_basic(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'sub' / 'out.log'
            argv = [sys.executable, '-c', 'print("hello")']
            rc = pool.popen_spawn(argv, log, os.environ.copy())
            self.assertEqual(rc, 0)
            self.assertTrue(log.exists())
            self.assertIn('hello', log.read_text())

    def test_popen_spawn_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'out.log'
            argv = [sys.executable, '-c', 'import sys; sys.exit(7)']
            rc = pool.popen_spawn(argv, log, os.environ.copy())
            self.assertEqual(rc, 7)

    def test_popen_spawn_env(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'out.log'
            env = os.environ.copy()
            env['POOL_T'] = 'yes'
            argv = [sys.executable, '-c', 'import os; print(os.environ.get("POOL_T"))']
            rc = pool.popen_spawn(argv, log, env)
            self.assertEqual(rc, 0)
            self.assertIn('yes', log.read_text())

    def test_popen_spawn_env_not_inherited(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'out.log'
            env = os.environ.copy()
            env.pop('POOL_T', None)
            argv = [sys.executable, '-c', 'import os; print(os.environ.get("POOL_T"))']
            rc = pool.popen_spawn(argv, log, env)
            self.assertEqual(rc, 0)
            self.assertIn('None', log.read_text())

    def test_popen_spawn_writes_run_private(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'job.log'
            slot = Path(td) / 'slot'
            slot.mkdir()
            argv = [sys.executable, '-c', 'import time; time.sleep(0.3)']
            data = {}
            def poll():
                deadline = time.time() + 5
                while time.time() < deadline:
                    if (slot / 'run.private').exists():
                        try:
                            val = proc.read_run_private(slot)
                            pid = val['pid']
                            data['val'] = val
                            data['alive_at_read'] = proc.pid_alive(pid)
                        except Exception as e:
                            data['err'] = e
                        return
                    time.sleep(0.02)
            t = threading.Thread(target=poll)
            t.start()
            rc = pool.popen_spawn(argv, log, os.environ.copy(), slot_dir=slot)
            t.join(timeout=6)
            self.assertEqual(rc, 0)
            self.assertIn('val', data)
            private = data['val']
            self.assertIsInstance(private, dict)
            self.assertNotEqual(private['pid'], os.getpid())
            self.assertTrue(data['alive_at_read'])
            self.assertEqual(private.get('run_id'), log.stem)

    def test_status(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td}
            pool.write_slot_state(pool.spool_root(env), 1, 'running', {'id': 'job-7'},
                                  time.time(), os.getpid(), proc.filetime_of(os.getpid()))
            out = io.StringIO()
            rc = pool.main(['status', '--json'], env, io.BytesIO(), out)
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertIn('slots', data)
            self.assertIn('queue_depth', data)
            self.assertIn('oldest_wait_s', data)
            self.assertEqual(len(data['slots']), 1)
            self.assertEqual(data['slots'][0]['state'], 'running')

    def test_argv_rejects_utf16_overflow_and_nonstring_fields(self):
        for field in ('client', 'model', 'title', 'brief'):
            with self.subTest(field=field):
                job = {'id': 'job-7', 'card': {'client': 'opencode'}, 'brief': 'x'}
                (job if field == 'brief' else job['card'])[field] = ['bad']
                with self.assertRaises(ValueError):
                    pool.job_argv(job, TOOLS.parent, sys.executable)
        with self.assertRaises(ValueError):
            pool.job_argv({'id': 'job-7', 'card': {'client': 'opencode'},
                           'brief': '\U0001f600' * 20000}, TOOLS.parent, sys.executable)

    def test_claimed_exceptions_fail_and_loop_continues(self):
        for mode in ('spawn-os', 'spawn-type', 'risk', 'memory', 'argv'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                env = {'LOCALAPPDATA': td}
                root = pool.spool_root(env)
                pool.ensure_dirs(root)
                job = {'id': 'job-7', 'card': {'client': 'opencode'}, 'brief': 'x'}
                if mode == 'argv':
                    job['card']['client'] = ['bad']
                (root / 'queue' / 'job-7.json').write_text(json.dumps(job))
                def boom(*a):
                    raise OSError('spawn failed') if mode == 'spawn-os' else TypeError('unexpected')
                deps = pool.Deps(spawn=boom if mode.startswith('spawn') else lambda *a: 0)
                if mode == 'risk':
                    deps.risk_of = boom
                if mode == 'memory':
                    calls = []
                    def mem():
                        calls.append(1)
                        return boom() if len(calls) == 2 else 99999
                    deps.mem_mb = mem
                self.assertEqual(pool.main(['slot'], env, io.BytesIO(), io.StringIO(),
                    deps={'slot_deps': deps, 'max_iterations': 2, 'sleep': lambda _: None}), 0)
                rec = json.loads((root / 'done' / 'job-7.json').read_text())
                self.assertNotEqual(rec['rc'], 0)
                self.assertTrue((root / 'failed' / 'job-7.json').exists())
                self.assertFalse(list((root / 'queue').glob('*.json')))
                self.assertFalse((root / 'slots' / '1' / 'job-7.json').exists())

    def test_kill_real_slot_and_bad_private(self):
        for private in ([], {'run_id': 'job-7'}):
            with self.subTest(private=private), tempfile.TemporaryDirectory() as td:
                env = {'LOCALAPPDATA': td}
                root = pool.spool_root(env)
                pool.write_slot_state(root, 1, 'running', {'id': 'job-7'}, 1, 1, 1)
                slot = root / 'slots' / '1'
                self.assertEqual(pool._find_slot(root, 'job-7'), slot)
                (slot / 'run.private').write_text(json.dumps(private))
                with self.assertRaises(ValueError):
                    proc.kill_job(slot, lambda _: 1, lambda _: self.fail('must not kill'), os_name='nt')
                out = io.StringIO()
                self.assertEqual(pool.main(['kill', 'job-7'], env, io.BytesIO(), out,
                                          deps={'os_name': 'nt'}), 1)
                self.assertIn('refused: bad run.private', out.getvalue())

    def test_ssh_gate_submit(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td, 'SSH_ORIGINAL_COMMAND': 'submit'}
            payload = {'card': {'client': 'opencode'}, 'brief': 'do x'}
            stdin = io.BytesIO(json.dumps(payload).encode())
            out = io.StringIO()
            rc = pool.main(['ssh-gate', '--submitter', 'lane1'], env, stdin, out)
            self.assertEqual(rc, 0)
            root = pool.spool_root(env)
            queue = root / 'queue'
            files = list(queue.glob('*.json'))
            self.assertEqual(len(files), 1)
            printed = out.getvalue().strip()
            self.assertIn(files[0].stem, printed)
            job = json.loads(files[0].read_text())
            self.assertEqual(job, gate.parse_submit(json.dumps(payload).encode(),
                submitter='lane1', now=job['submitted_at'], rand=job['id'].rsplit('-', 1)[1]))

    def test_ssh_gate_invalid_payloads(self):
        for payload in ({'brief': 'x'}, {'id': 'chosen', 'brief': 'x'},
                        {'card': {}, 'brief': 'x' * 65536}):
            with self.subTest(payload_keys=list(payload)), tempfile.TemporaryDirectory() as td:
                env = {'LOCALAPPDATA': td, 'SSH_ORIGINAL_COMMAND': 'submit'}
                rc = pool.main(['ssh-gate', '--submitter', 'lane1'], env,
                               io.BytesIO(json.dumps(payload).encode()), io.StringIO())
                self.assertEqual(rc, 2)
                self.assertFalse(list((pool.spool_root(env) / 'queue').glob('*.json')))

    def test_ssh_gate_status_and_binary_buffer(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td, 'SSH_ORIGINAL_COMMAND': 'status --json'}
            out = io.StringIO()
            self.assertEqual(pool.main(['ssh-gate', '--submitter', 'lane1'], env,
                                      io.BytesIO(), out), 0)
            self.assertEqual(json.loads(out.getvalue())['queue_depth'], 0)
            env['SSH_ORIGINAL_COMMAND'] = 'submit'
            stdin = io.TextIOWrapper(io.BytesIO(b'{"card":{"client":"api"},"brief":"x"}'))
            self.addCleanup(stdin.close)
            self.assertEqual(pool.main(['ssh-gate', '--submitter', 'lane1'], env,
                                      stdin, io.StringIO()), 0)

    def test_claim_ownership(self):
        for mode in ('none', 'fresh', 'other', 'memory', 'traversal', 'missing', 'list', 'mismatch'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                pool.ensure_dirs(root)
                a = root / 'queue' / 'a.json'
                job = {'id': 'a', 'submitter': 'lane1', 'card': {'client': 'api'}, 'brief': 'A'}
                if mode == 'traversal':
                    job['id'] = '../queue/victim'
                if mode == 'missing':
                    del job['id']
                if mode == 'mismatch':
                    job['id'] = 'wrong'
                a.write_text(json.dumps([] if mode == 'list' else job))
                victim = root / 'queue' / 'victim.json'
                victim.write_text('{"untouched":true}')
                os.utime(a, (1, 1))
                calls = []
                deps = pool.Deps(spawn=lambda *args: calls.append(args) or 0)
                real_claim = rpc.claim
                def claim(q, s):
                    if mode == 'none':
                        return None
                    if mode in ('other', 'memory'):
                        b = Path(q) / 'b.json'
                        b.write_text(json.dumps(dict(job, id='b', brief='B')))
                        dest = Path(s) / b.name
                        os.replace(b, dest)
                        return dest
                    return real_claim(q, s)
                if mode in ('other', 'memory'):
                    (root / 'slots' / '1').mkdir()
                if mode == 'memory':
                    mem = iter((99999, 0))
                    deps.mem_mb = lambda: next(mem)
                with patch.object(rpc, 'claim', claim):
                    result = pool.run_one(root, 1, deps)
                self.assertEqual(victim.read_text(), '{"untouched":true}')
                if mode == 'none':
                    self.assertEqual(result, 'claim-failed')
                    self.assertTrue(a.exists())
                    self.assertEqual(calls, [])
                elif mode in ('traversal', 'missing', 'list', 'mismatch'):
                    self.assertEqual(result, 'bad-job')
                    self.assertTrue((root / 'failed' / 'a.json').exists())
                    self.assertEqual(calls, [])
                    victim.unlink()
                    self.assertEqual(pool.run_one(root, 1, deps), 'idle')
                elif mode == 'memory':
                    self.assertEqual(result, 'memory-recheck')
                    self.assertEqual(json.loads((root / 'queue' / 'b.json').read_text())['brief'], 'B')
                    self.assertTrue(a.exists())
                else:
                    self.assertEqual(result, 'done')
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0][0][-1], 'B' if mode == 'other' else 'A')
                    self.assertEqual(a.exists(), mode == 'other')
                    if mode == 'fresh':
                        victim.unlink()
                        self.assertEqual(pool.run_one(root, 1, deps), 'idle')
                        self.assertEqual(len(calls), 1)

    def test_ssh_gate_reject(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td, 'SSH_ORIGINAL_COMMAND': 'bash'}
            stdin = io.BytesIO(b'{}')
            out = io.StringIO()
            rc = pool.main(['ssh-gate', '--submitter', 'lane1'], env, stdin, out)
            self.assertEqual(rc, 2)
            root = pool.spool_root(env)
            queue = root / 'queue'
            self.assertFalse(queue.exists() and any(queue.glob('*.json')))

    def test_kill_invalid_id(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td}
            rc = pool.main(['kill', '../x'], env, io.BytesIO(), io.StringIO())
            self.assertEqual(rc, 2)

    def test_kill_no_such_job(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td}
            root = pool.spool_root(env)
            slot = root / 'slots' / 'slot1'
            slot.mkdir(parents=True)
            (slot / 'slot.json').write_text(json.dumps({'job': {'id': 'other'}}))
            out = io.StringIO()
            rc = pool.main(['kill', 'job-123'], env, io.BytesIO(), out)
            self.assertEqual(rc, 1)
            self.assertIn('no such job', out.getvalue())

    def test_kill_calls_proc(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td}
            root = pool.spool_root(env)
            slot = root / 'slots' / 'slot1'
            slot.mkdir(parents=True)
            jobid = 'job-abc'
            (slot / 'slot.json').write_text(json.dumps({'job': {'id': jobid}}))
            (slot / 'run.private').write_text('{}')
            calls = []
            def fake_kill_job(slot_dir, filetime_of, kill_tree, os_name=None):
                calls.append((slot_dir, filetime_of, kill_tree, os_name))
                return 'killed'
            orig = proc.kill_job
            proc.kill_job = fake_kill_job
            try:
                out = io.StringIO()
                deps = {'filetime_of': lambda pid: 5678, 'kill_tree': lambda argv: None}
                rc = pool.main(['kill', jobid], env, io.BytesIO(), out, deps=deps)
            finally:
                proc.kill_job = orig
            self.assertEqual(rc, 0)
            self.assertIn('killed', out.getvalue())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], slot)
            self.assertIs(calls[0][1], deps['filetime_of'])
            self.assertIs(calls[0][2], deps['kill_tree'])
            self.assertEqual(calls[0][3], 'nt')

    def test_slot_invalid_n(self):
        for val in ('abc', '0', '-1', ''):
            with tempfile.TemporaryDirectory() as td:
                env = {'LOCALAPPDATA': td, 'AUTOOS_POOL_N': val}
                self.patch_run_one()
                out = io.StringIO()
                rc = pool.main(['slot', '--n', '1', '--once'], env, io.BytesIO(), out, deps={'slot_deps': object()})
                self.assertEqual(rc, 2)
                root = pool.spool_root(env)
                self.assertFalse(root.exists())

    def test_slot_valid_n_and_clamp(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td, 'AUTOOS_POOL_N': '3'}
            self.patch_run_one()
            out = io.StringIO()
            rc = pool.main(['slot', '--n', '3', '--once'], env, io.BytesIO(), out, deps={'slot_deps': object()})
            self.assertEqual(rc, 0)
            rc = pool.main(['slot', '--n', '4', '--once'], env, io.BytesIO(), io.StringIO(), deps={'slot_deps': object()})
            self.assertEqual(rc, 3)
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td, 'AUTOOS_POOL_N': '99'}
            self.patch_run_one()
            out = io.StringIO()
            rc = pool.main(['slot', '--n', '6', '--once'], env, io.BytesIO(), out, deps={'slot_deps': object()})
            self.assertEqual(rc, 0)
            self.assertIn('clamp requested=99 used=6', out.getvalue())
            rc = pool.main(['slot', '--n', '7', '--once'], env, io.BytesIO(), io.StringIO(), deps={'slot_deps': object()})
            self.assertEqual(rc, 3)
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td}
            self.patch_run_one()
            out = io.StringIO()
            rc = pool.main(['slot', '--n', '6', '--once'], env, io.BytesIO(), out, deps={'slot_deps': object()})
            self.assertEqual(rc, 0)
            rc = pool.main(['slot', '--n', '7', '--once'], env, io.BytesIO(), io.StringIO(), deps={'slot_deps': object()})
            self.assertEqual(rc, 3)

    def test_slot_once(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td}
            calls = []
            deps_obj = pool.Deps(spawn=lambda *a: calls.append(a) or 0, now=lambda: 1.0, pid=1)
            deps_obj.spawn_calls = calls
            out = io.StringIO()
            rc = pool.main(['slot', '--n', '1', '--once'], env, io.BytesIO(), out, deps={'slot_deps': deps_obj})
            self.assertEqual(rc, 0)
            self.assertIn('idle', out.getvalue())
            self.assertEqual(len(deps_obj.spawn_calls), 0)

    def test_slot_loop(self):
        with tempfile.TemporaryDirectory() as td:
            env = {'LOCALAPPDATA': td}
            calls = self.patch_run_one('idle')
            sleeps = []
            def fake_sleep(sec):
                sleeps.append(sec)
            deps = {'slot_deps': object(), 'max_iterations': 3, 'sleep': fake_sleep}
            out = io.StringIO()
            rc = pool.main(['slot', '--n', '1'], env, io.BytesIO(), out, deps=deps)
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 3)
            self.assertTrue(len(sleeps) in (2, 3))
            for s in sleeps:
                self.assertEqual(s, 15)

if __name__ == '__main__':
    unittest.main()

# TEST-GAMING: no - reviewed the prompt's described production interfaces and test requirements for test-detection, special-casing, or weakened assertions: 0 hits
