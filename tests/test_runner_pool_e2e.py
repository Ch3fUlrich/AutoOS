import os
import sys
import json
import time
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import runner_pool as pool
import runner_pool_core as rpc
import runner_pool_proc as proc

STUB = '''\
import os, sys
lines = [
    " ".join(sys.argv[1:]),
    "ENVOFF=" + str(os.environ.get("AUTOOS_ADMISSION_OFF")),
    "ARGV0=" + os.path.basename(sys.argv[0]),
]
out = os.environ.get("STUB_OUT")
if out:
    with open(out, "a") as f:
        for line in lines:
            f.write(line + chr(10))
else:
    for line in lines:
        print(line)
sys.exit(int(os.environ.get("STUB_RC", "0")))
'''

class TestE2E(unittest.TestCase):
    def _job(self, jid):
        return {
            'id': jid,
            'submitter': 'lane1',
            'card': {'client': 'opencode', 'model': 'm1', 'title': 't1'},
            'brief': '-dash brief',
        }

    def _setup(self, jobs=None, stub_rc='0'):
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        repo = td / 'repo'
        (repo / 'tools').mkdir(parents=True)
        (repo / 'tools' / 'autoos-agent.py').write_text(STUB, encoding='utf-8')
        root = td / 'spool'
        pool.ensure_dirs(root)
        (root / 'queue').mkdir(parents=True, exist_ok=True)
        if jobs is None:
            jobs = [self._job('jobA')]
        for job in jobs:
            (root / 'queue' / (job['id'] + '.json')).write_text(json.dumps(job), encoding='utf-8')
        deps = pool.Deps(
            spawn=lambda argv, log, env: pool.popen_spawn(argv, log, env, slot_dir=root / 'slots' / '1'),
            repo_root=repo,
            python=sys.executable,
            environ={
                'PATH': os.environ['PATH'],
                'AUTOOS_ADMISSION_OFF': '1',
                'STUB_RC': stub_rc,
            },
            mem_mb=lambda: 99999,
            floor_mb=6144,
            now=time.time,
            sleep=lambda s: None,
        )
        return td, repo, root, deps

    def _read_json(self, path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _get(self, obj, *names):
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _collect_reasons(self, obj):
        out = []
        def rec(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if 'reason' in str(k).lower():
                        out.append(v)
                    rec(v)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    rec(v)
        rec(obj)
        return out

    def _slot_state(self, status, slot_id):
        slots = status.get('slots') if isinstance(status, dict) else None
        if slots is None:
            slots = status
        if isinstance(slots, dict):
            s = slots.get(str(slot_id), slots.get(slot_id))
            if s is None:
                return None
            if isinstance(s, dict):
                return s.get('state', s.get('status'))
            return s
        if isinstance(slots, list):
            for s in slots:
                if isinstance(s, dict):
                    sid = s.get('id', s.get('slot'))
                    if sid == slot_id or str(sid) == str(slot_id):
                        return s.get('state', s.get('status'))
                else:
                    if getattr(s, 'id', None) == slot_id or getattr(s, 'slot', None) == slot_id:
                        return getattr(s, 'state', getattr(s, 'status', None))
        return None

    def test_1_success(self):
        td, repo, root, deps = self._setup()
        r = pool.run_one(root, 1, deps)
        self.assertEqual(r, 'done')
        log = (root / 'log' / 'jobA.log').read_text(encoding='utf-8')
        self.assertIn('run --client opencode --model m1 --isolate --run-id jobA --title t1 -- -dash brief', log)
        self.assertIn('ENVOFF=None', log)
        done = self._read_json(root / 'done' / 'jobA.json')
        self.assertEqual(done['state'], 'done')
        self.assertEqual(done['rc'], 0)
        self.assertNotIn('brief', json.dumps(done))
        self.assertFalse((root / 'queue' / 'jobA.json').exists())
        self.assertFalse((root / 'slots' / '1' / 'jobA.json').exists())
        self.assertTrue((root / 'slots' / '1' / 'run.private').exists())
        private = proc.read_run_private(root / 'slots' / '1')
        self.assertEqual(self._get(private, 'run_id'), 'jobA')
        self.assertIsInstance(self._get(private, 'pid'), int)

    def test_2_rc3_failed(self):
        td, repo, root, deps = self._setup(stub_rc='3')
        r = pool.run_one(root, 1, deps)
        self.assertEqual(r, 'failed')
        done = self._read_json(root / 'done' / 'jobA.json')
        self.assertEqual(done['state'], 'failed')
        self.assertEqual(done['rc'], 3)
        reasons = self._collect_reasons(done)
        self.assertTrue(any('rc' in str(x).lower() for x in reasons), reasons)

    def test_3_rc13_requeued(self):
        td, repo, root, deps = self._setup(stub_rc='13')
        r = pool.run_one(root, 1, deps)
        self.assertEqual(r, 'requeued-rc13')
        self.assertTrue((root / 'queue' / 'jobA.json').exists())
        self.assertFalse((root / 'done' / 'jobA.json').exists())
        self.assertFalse(any(p.name.endswith('.requeued') for p in root.rglob('*')))

    def test_4_order(self):
        jobs = [self._job('jobA'), self._job('jobB')]
        td, repo, root, deps = self._setup(jobs=jobs)
        t = time.time()
        os.utime(root / 'queue' / 'jobA.json', (t - 100, t - 100))
        os.utime(root / 'queue' / 'jobB.json', (t, t))
        r1 = pool.run_one(root, 1, deps)
        r2 = pool.run_one(root, 1, deps)
        self.assertEqual(r1, 'done')
        self.assertEqual(r2, 'done')
        doneA = self._read_json(root / 'done' / 'jobA.json')
        doneB = self._read_json(root / 'done' / 'jobB.json')
        self.assertLess(doneA['started'], doneB['started'])

    def test_5_real_child(self):
        td, repo, root, deps = self._setup()
        r = pool.run_one(root, 1, deps)
        self.assertEqual(r, 'done')
        log = (root / 'log' / 'jobA.log').read_text(encoding='utf-8')
        self.assertIn('ARGV0=autoos-agent.py', log)
        self.assertIn('run --client opencode --model m1 --isolate --run-id jobA --title t1 -- -dash brief', log)

    def test_6_status(self):
        td, repo, root, deps = self._setup()
        r = pool.run_one(root, 1, deps)
        self.assertEqual(r, 'done')
        status = pool.build_status(root, time.time(), proc.pid_alive, proc.filetime_of)
        self.assertEqual(self._get(status, 'queue_depth'), 0)
        self.assertEqual(self._slot_state(status, 1), 'idle')


if __name__ == '__main__':
    unittest.main()
