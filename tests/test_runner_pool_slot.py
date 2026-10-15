import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import runner_pool as pool
import runner_pool_core as rpc

class TestSlot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        pool.ensure_dirs(self.root)
        self.n = 1
        (self.root / 'slots' / str(self.n)).mkdir(parents=True, exist_ok=True)
        self.spawn_calls = []

    def tearDown(self):
        self.td.cleanup()

    def _q(self, id, **extra):
        job = {'id': id, 'submitter': 'lane1', 'card': {'client': 'opencode'}, 'brief': 'hi'}
        job.update(extra)
        (self.root / 'queue' / f'{id}.json').write_text(json.dumps(job), encoding='utf-8')
        return job

    def _deps(self, **kw):
        d = dict(
            spawn=None,
            admission_refusal=lambda: None,
            mem_mb=lambda: 99999,
            floor_mb=6144,
            breaker_open=lambda: False,
            risk_of=lambda job: 'R0',
            diff_info=lambda job: (False, False),
            sample_every=4,
            now=lambda: 12345.0,
            pid=4242,
            filetime=0,
            repo_root=Path('/repo'),
            python='python3',
            sleep=lambda s: None,
            environ={'AUTOOS_ADMISSION_OFF': '1', 'KEEP': 'v'},
        )
        d.update(kw)
        if d['spawn'] is None:
            def spawn(argv, log_path, env):
                self.spawn_calls.append((argv, log_path, env))
                return 0
            d['spawn'] = spawn
        return pool.Deps(**d)

    def _slot(self):
        p = self.root / 'slots' / str(self.n) / 'slot.json'
        return json.loads(p.read_text(encoding='utf-8'))

    def _done(self, id):
        p = self.root / 'done' / f'{id}.json'
        return json.loads(p.read_text(encoding='utf-8'))

    def _reasons(self, done):
        out = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == 'reasons' and isinstance(v, list):
                        out.extend(v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(done)
        return out

    def test_breaker_open_claims_nothing(self):
        self._q('j1')
        deps = self._deps(breaker_open=lambda: True)
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'breaker-open')
        self.assertTrue((self.root / 'queue' / 'j1.json').exists())
        self.assertEqual(self._slot()['state'], 'breaker-open')
        self.assertEqual(self.spawn_calls, [])

    def test_memory_none(self):
        self._q('j1')
        deps = self._deps(mem_mb=lambda: None)
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'memory')
        self.assertTrue((self.root / 'queue' / 'j1.json').exists())
        self.assertEqual(self._slot()['state'], 'idle')
        self.assertEqual(self.spawn_calls, [])

    def test_memory_below_floor(self):
        self._q('j1')
        deps = self._deps(mem_mb=lambda: 100)
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'memory')
        self.assertTrue((self.root / 'queue' / 'j1.json').exists())
        self.assertEqual(self._slot()['state'], 'idle')
        self.assertEqual(self.spawn_calls, [])

    def test_admission_refusal_leaves_queue(self):
        self._q('j1')
        deps = self._deps(admission_refusal=lambda: 'no')
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'refused')
        self.assertTrue((self.root / 'queue' / 'j1.json').exists())
        self.assertEqual(self._slot()['state'], 'idle')
        self.assertEqual(self.spawn_calls, [])

    def test_empty_queue_idle_heartbeat(self):
        now = 777.0
        deps = self._deps(now=lambda: now)
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'idle')
        self.assertEqual(self._slot()['heartbeat'], now)
        self.assertEqual(self.spawn_calls, [])

    def test_happy_path_done(self):
        job = self._q('j1')
        deps = self._deps()
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'done')
        self.assertEqual(len(self.spawn_calls), 1)
        argv, log_path, env = self.spawn_calls[0]
        self.assertEqual(argv, pool.job_argv(job, deps.repo_root, deps.python))
        self.assertEqual(Path(log_path), self.root / 'log' / 'j1.log')
        self.assertNotIn('AUTOOS_ADMISSION_OFF', env)
        self.assertEqual(env, rpc.strip_env(deps.environ))
        done = self._done('j1')
        self.assertEqual(done['state'], 'done')
        self.assertIn('first3', self._reasons(done))
        self.assertFalse((self.root / 'slots' / str(self.n) / 'j1.json').exists())
        self.assertFalse((self.root / 'queue' / 'j1.json').exists())
        self.assertEqual(self._slot()['state'], 'idle')

    def test_rc1_failed(self):
        self._q('j1')
        calls = []

        def spawn(argv, log_path, env):
            calls.append((argv, log_path, env))
            return 1

        deps = self._deps(spawn=spawn)
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'failed')
        self.assertEqual(len(calls), 1)
        done = self._done('j1')
        self.assertEqual(done['state'], 'failed')
        self.assertIn('rc', self._reasons(done))
        self.assertFalse((self.root / 'slots' / str(self.n) / 'j1.json').exists())
        self.assertFalse((self.root / 'queue' / 'j1.json').exists())

    def test_rc13_requeued(self):
        self._q('j1')
        calls = []

        def spawn(argv, log_path, env):
            calls.append(1)
            return 13

        deps = self._deps(spawn=spawn)
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'requeued-rc13')
        self.assertTrue((self.root / 'queue' / 'j1.json').exists())
        self.assertFalse((self.root / 'done' / 'j1.json').exists())
        self.assertFalse((self.root / 'queue' / 'j1.json.requeued').exists())
        self.assertFalse((self.root / 'slots' / str(self.n) / 'j1.json').exists())
        self.assertEqual(len(calls), 1)

    def test_invalid_json_bad_job(self):
        (self.root / 'queue' / 'bad.json').write_text('{nope', encoding='utf-8')
        deps = self._deps()
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'bad-job')
        self.assertTrue((self.root / 'failed' / 'bad.json').exists())
        self.assertFalse((self.root / 'queue' / 'bad.json').exists())
        self.assertEqual(self.spawn_calls, [])

    def test_recovery_requeued_then_failed(self):
        job = {'id': 'j1', 'submitter': 'lane1', 'card': {'client': 'opencode'}, 'brief': 'hi'}
        claimed = self.root / 'slots' / str(self.n) / 'j1.json'
        claimed.write_text(json.dumps(job), encoding='utf-8')
        deps = self._deps()
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'recovered-requeued')
        self.assertTrue((self.root / 'queue' / 'j1.json.requeued').exists())
        self.assertTrue((self.root / 'queue' / 'j1.json').exists())
        claimed.write_text(json.dumps(job), encoding='utf-8')
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'recovered-failed')
        self.assertTrue((self.root / 'failed' / 'j1.json').exists())
        self.assertEqual(self.spawn_calls, [])

    def test_memory_recheck(self):
        self._q('j1')
        calls = {'n': 0}

        def mem():
            calls['n'] += 1
            return 99999 if calls['n'] == 1 else 100

        deps = self._deps(mem_mb=mem)
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'memory-recheck')
        self.assertTrue((self.root / 'queue' / 'j1.json').exists())
        self.assertFalse((self.root / 'queue' / 'j1.json.requeued').exists())
        self.assertFalse((self.root / 'done' / 'j1.json').exists())
        self.assertEqual(self.spawn_calls, [])

    def test_risk_reason(self):
        self._q('j1')
        deps = self._deps(risk_of=lambda job: 'R2')
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'done')
        done = self._done('j1')
        self.assertIn('risk', self._reasons(done))

    def test_two_jobs_oldest_first(self):
        self._q('a')
        self._q('b')
        old = 1000
        os.utime(self.root / 'queue' / 'a.json', (old, old))
        os.utime(self.root / 'queue' / 'b.json', (old + 10, old + 10))
        deps = self._deps()
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'done')
        self.assertEqual(pool.run_one(self.root, self.n, deps), 'done')
        self.assertEqual(len(self.spawn_calls), 2)
        self.assertEqual(Path(self.spawn_calls[0][1]).name, 'a.log')
        self.assertEqual(Path(self.spawn_calls[1][1]).name, 'b.log')
        self.assertTrue((self.root / 'done' / 'a.json').exists())
        self.assertTrue((self.root / 'done' / 'b.json').exists())

    def test_job_argv_value_error(self):
        self._q('j1')
        orig = pool.job_argv

        def bad(*a, **k):
            raise ValueError('bad job')

        pool.job_argv = bad
        try:
            deps = self._deps()
            self.assertEqual(pool.run_one(self.root, self.n, deps), 'failed')
            done = self._done('j1')
            self.assertEqual(done['state'], 'failed')
            self.assertEqual(done['rc'], 2)
            self.assertFalse((self.root / 'slots' / str(self.n) / 'j1.json').exists())
            self.assertFalse((self.root / 'queue' / 'j1.json').exists())
            self.assertEqual(self.spawn_calls, [])
        finally:
            pool.job_argv = orig

unittest.main()
