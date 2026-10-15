import os
import json
import re
import unittest
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import runner_pool as pool
import runner_pool_core as rpc

class TestPoolBasics(unittest.TestCase):
    # constants
    def test_constants(self):
        self.assertEqual(pool.SLOT_STATES, ('idle', 'running', 'breaker-open'))
        self.assertEqual(pool.HEARTBEAT_T, 120)

    # ensure_dirs
    def test_ensure_dirs_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # first call
            pool.ensure_dirs(root)
            expected = {root / d for d in ('queue', 'slots', 'done', 'failed', 'log', 'submitters')}
            actual = {p for p in root.iterdir() if p.is_dir()}
            self.assertEqual(expected, actual)
            # second call should not raise
            pool.ensure_dirs(root)
            self.assertTrue(all(p.is_dir() for p in expected))

    # spool_root
    def test_spool_root_localappdata(self):
        env = {'LOCALAPPDATA': r'C:\Users\Bob\AppData\Local', 'HOME': '/home/bob'}
        expected = Path(r'C:\Users\Bob\AppData\Local') / 'autoos' / 'pool'
        self.assertEqual(pool.spool_root(env), expected)

    def test_spool_root_fallback(self):
        env = {'HOME': '/home/bob'}
        expected = Path('/home/bob') / '.local' / 'state' / 'autoos' / 'pool'
        self.assertEqual(pool.spool_root(env), expected)

    # write_slot_state
    def test_write_slot_state_success(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            n, state, job, now, pid, ft = 2, 'idle', None, 1000, 1234, 555
            ok = pool.write_slot_state(root, n, state, job, now, pid, ft, since=now - 10)
            self.assertTrue(ok)
            slot_path = root / 'slots' / str(n) / 'slot.json'
            self.assertTrue(slot_path.is_file())
            data = json.loads(slot_path.read_text())
            self.assertEqual(data['slot'], n)
            self.assertEqual(data['state'], state)
            self.assertIsNone(data['job'])
            self.assertEqual(data['since'], now - 10)
            self.assertEqual(data['heartbeat'], now)
            self.assertEqual(data['pid'], pid)
            self.assertEqual(data['filetime'], ft)

    def test_write_slot_state_invalid_n(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(ValueError):
                pool.write_slot_state(root, 0, 'idle', None, 0, 0, 0)
            with self.assertRaises(ValueError):
                pool.write_slot_state(root, '1', 'idle', None, 0, 0, 0)

    def test_write_slot_state_invalid_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(ValueError):
                pool.write_slot_state(root, 1, 'unknown', None, 0, 0, 0)

    # build_status
    def test_build_status_various(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            slots_dir = root / 'slots'
            slots_dir.mkdir(parents=True)

            # helper alive and filetime maps
            alive_pids = {111}
            pid_filetime_map = {111: 100, 222: 200, 333: 300}
            pid_alive = lambda p: p in alive_pids
            filetime_of = lambda p: pid_filetime_map.get(p, None)

            now = 5000
            T = pool.HEARTBEAT_T

            # slot 1: idle fresh
            pool.write_slot_state(root, 1, 'idle', None, now, 111, 100)
            # slot 2: running, pid dead, stale => dead
            pool.write_slot_state(root, 2, 'running', 'job2', now - 200, 222, 200)
            # slot 3: running, pid dead, fresh heartbeat => stays running
            pool.write_slot_state(root, 3, 'running', 'job3', now - 30, 333, 300)
            # slot 4: corrupt JSON -> dead
            corrupt_dir = slots_dir / '4'
            corrupt_dir.mkdir()
            (corrupt_dir / 'slot.json').write_text('not a json')

            status = pool.build_status(root, now, pid_alive, filetime_of, T=T)

            # slots list sorted by slot number
            self.assertEqual([s['slot'] for s in status['slots']], [1, 2, 3, 4])

            # slot 1 idle
            s1 = status['slots'][0]
            self.assertEqual(s1['state'], 'idle')
            self.assertIsNone(s1['job'])

            # slot 2 dead
            s2 = status['slots'][1]
            self.assertEqual(s2['state'], 'slot-dead')
            self.assertEqual(s2['job'], 'job2')

            # slot 3 running
            s3 = status['slots'][2]
            self.assertEqual(s3['state'], 'running')
            self.assertEqual(s3['job'], 'job3')

            # slot 4 dead due to corrupt file
            s4 = status['slots'][3]
            self.assertEqual(s4['state'], 'slot-dead')
            self.assertIsNone(s4['job'])

            # queue depth and oldest wait
            queue_dir = root / 'queue'
            queue_dir.mkdir()
            f1 = queue_dir / 'a.json'
            f2 = queue_dir / 'b.json'
            f1.touch()
            f2.touch()
            os.utime(f1, (now - 400, now - 400))
            os.utime(f2, (now - 100, now - 100))

            status = pool.build_status(root, now, pid_alive, filetime_of, T=T)
            self.assertEqual(status['queue_depth'], 2)
            self.assertEqual(status['oldest_wait_s'], 400)

            # empty slots dir
            empty_root = Path(td) / 'empty_root'
            empty_root.mkdir()
            (empty_root / 'slots').mkdir()
            status = pool.build_status(empty_root, now, pid_alive, filetime_of, T=T)
            self.assertEqual(status['slots'], [])

    # job_argv
    def test_job_argv_ok(self):
        repo_root = Path('/repo')
        python = '/usr/bin/python3'
        job = {
            'id': 'job_123',
            'submitter': 'bob',
            'card': {'client': 'cli', 'model': 'm1', 'title': 'Test'},
            'brief': 'do something'
        }
        argv = pool.job_argv(job, repo_root, python)
        expected = [
            python,
            str(repo_root / 'tools' / 'autoos-agent.py'),
            'run',
            '--client', 'cli',
            '--model', 'm1',
            '--isolate',
            '--run-id', 'job_123',
            '--title', 'Test',
            '--',
            'do something'
        ]
        self.assertEqual(argv, expected)

    def test_job_argv_missing_client(self):
        repo_root = Path('/repo')
        python = '/usr/bin/python3'
        job = {'id': 'job1', 'submitter': 'bob', 'card': {}, 'brief': 'x'}
        with self.assertRaises(ValueError):
            pool.job_argv(job, repo_root, python)

    def test_job_argv_invalid_id(self):
        repo_root = Path('/repo')
        python = '/usr/bin/python3'
        job = {'id': 'invalid id!', 'submitter': 'bob', 'card': {'client': 'c'}, 'brief': 'x'}
        with self.assertRaises(ValueError):
            pool.job_argv(job, repo_root, python)

    def test_job_argv_too_long(self):
        repo_root = Path('/repo')
        python = '/usr/bin/python3'
        long_brief = 'x' * 30000
        job = {'id': 'j', 'submitter': 'bob', 'card': {'client': 'c'}, 'brief': long_brief}
        with self.assertRaises(ValueError) as cm:
            pool.job_argv(job, repo_root, python)
        self.assertIn('argv-too-long', str(cm.exception))

    # done_record
    def test_done_record(self):
        job = {'id': 'jid', 'submitter': 'alice', 'card': {}, 'brief': 'do'}
        rc = 1
        started = 100
        ended = 200
        deep = {'deep': True, 'reasons': ['r1', 'r2']}
        rec = pool.done_record(job, rc, started, ended, deep)
        self.assertEqual(rec['id'], 'jid')
        self.assertEqual(rec['submitter'], 'alice')
        self.assertEqual(rec['rc'], rc)
        self.assertEqual(rec['started'], started)
        self.assertEqual(rec['ended'], ended)
        self.assertEqual(rec['deep'], deep)
        self.assertEqual(rec['state'], 'failed')
        # ensure no secret fields
        self.assertNotIn('brief', rec)
        self.assertNotIn('card', rec)

if __name__ == '__main__':
    unittest.main()
