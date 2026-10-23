import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1] / 'tools/ci-runner/measure_speed.py'
class MeasureTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('measure_speed', TOOL)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)

    def test_median_outlier(self):
        rows, summary = self.m.table({'job': [100, 100, 9999]}, {'job': [110, 110, 9999]})
        self.assertEqual((rows[0]['single'], rows[0]['parallel'], rows[0]['ratio']), (100, 110, 1.1))
        self.assertTrue(summary['passed'])
        self.assertEqual(summary['max_ratio'], 1.1)
        self.assertEqual(summary['median_ratio'], 1.1)

    def test_insufficient(self):
        for a, b in [([100], [100]*3), ([100]*3, []), ([], [100]*3)]:
            rows, summary = self.m.table({'job': a}, {'job': b})
            self.assertFalse(summary['passed'])
            self.assertIn('INSUFFICIENT', self.m.render(rows, summary))
            self.assertIn('INSUFFICIENT', self.m.render_md(rows, summary))
            self.assertEqual((rows[0]['n_single'], rows[0]['n_parallel']), (len(a), len(b)))
        self.assertFalse(self.m.table({}, {})[1]['passed'])

    def test_boundary(self):
        for seconds, passed in [(120, True), (121, False)]:
            rows, summary = self.m.table({'job': [100]*3}, {'job': [seconds]*3})
            self.assertEqual(summary['passed'], passed)
            self.assertEqual(summary['over_1_20'], int(not passed))

    def test_jobs(self):
        start, end = '2026-01-01T00:00:00Z', '2026-01-01T00:01:00+00:00'
        self.assertEqual(self.m.job_seconds(start, end), 60)
        self.assertIsNone(self.m.job_seconds(None, end))
        self.assertIsNone(self.m.job_seconds(start, ''))
        jobs = [dict(name=n, conclusion=c, startedAt=start, completedAt=e)
                for n, c, e in [('ok', 'success', end), ('skip', 'skipped', end), ('none', 'failure', None)]]
        self.assertEqual(self.m.parse_jobs({'jobs': jobs}), [('ok', 'success', 60)])

    def test_argv(self):
        self.assertEqual(self.m.dispatch_argv('OWNER/NAME', 'ci.yml', 'BRANCH'),
                         ['gh', 'workflow', 'run', 'ci.yml', '--repo', 'OWNER/NAME', '--ref', 'BRANCH'])
        for argv in [self.m.list_argv('OWNER/NAME', 'ci.yml'), self.m.view_argv('OWNER/NAME', 42)]:
            self.assertIn('OWNER/NAME', argv)
            self.assertFalse(any(c in arg for arg in argv for c in ';|&<>`$'))
        self.assertIn('ci.yml', self.m.list_argv('OWNER/NAME', 'ci.yml'))
        self.assertIn('42', self.m.view_argv('OWNER/NAME', 42))

    def test_dry_run(self):
        result = subprocess.run([sys.executable, str(TOOL), '--repo', 'OWNER/NAME', '--workflow',
                                 'ci.yml', '--ref', 'BRANCH', '--runs', '3', '--parallel', '4',
                                 '--out', 'unused', '--dry-run'], env={**os.environ, 'PATH': ''},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ['gh workflow run', 'gh run list', 'gh run view', 'gh run watch']:
            self.assertIn(command, result.stdout)


if __name__ == '__main__':
    unittest.main()
