"""Progress protocol and bounded harmless child-process regressions."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'argv', [str(ROOT / file)]):
        spec.loader.exec_module(module)
    return module


class ProgressTests(unittest.TestCase):
    def test_server_counts_completion_not_started_lines(self):
        server = load('autoos_serve', 'lib/linux/serve.py')
        server.record_line('  > [1/3] Example')
        self.assertEqual(server.RUN['done'], 0)
        server.record_line('@@AUTOOS_PROGRESS ' + json.dumps(dict(done=1, total=3, percent=None)))
        self.assertEqual(server.RUN['done'], 1)
        self.assertEqual(len(server.LOG), 1)
        server.record_line('@@AUTOOS_PROGRESS {broken')
        server.record_line('@@AUTOOS_PROGRESS {"done":4,"total":3}')
        self.assertEqual(server.RUN['done'], 1)

    def test_record_line_edge_cases(self):
        server = load('autoos_serve', 'lib/linux/serve.py')
        server.RUN.update(done=0, total=3, current=None)
        server.LOG.clear()

        cases = [
            '@@AUTOOS_PROGRESS {broken',               # json.JSONDecodeError (ValueError)
            '@@AUTOOS_PROGRESS "string"',              # TypeError (when int() fails on dict lookup)
            '@@AUTOOS_PROGRESS {"done": "x"}',         # KeyError (missing total) / ValueError (int("x"))
            '@@AUTOOS_PROGRESS {"total": 3}',          # KeyError (missing done)
            '@@AUTOOS_PROGRESS {"done": -1, "total": 3}',  # Out of bounds: done < 0
            '@@AUTOOS_PROGRESS {"done": 4, "total": 3}',   # Out of bounds: done > total
            '@@AUTOOS_PROGRESS {"done": 1}',           # KeyError (missing total)
        ]

        for case in cases:
            server.record_line(case)
            self.assertEqual(server.RUN['done'], 0)
            self.assertEqual(len(server.LOG), 0)

    def test_unknown_percentage_stays_null(self):
        runner = load('autoos_process', 'lib/linux/process.py')
        output = io.StringIO()
        with patch.dict(os.environ, AUTOOS_PROGRESS_EVENTS='1'), contextlib.redirect_stdout(output):
            runner.snapshot(dict(name='Example',done=0,total=2), time.monotonic())
        snapshot = json.loads(output.getvalue().split(' ', 1)[1])
        self.assertIsNone(snapshot['percent'])

    @unittest.skipUnless(os.name == 'posix', 'requires a native POSIX process group')
    def test_noisy_stderr_cannot_block_stdout(self):
        result = subprocess.run([sys.executable, str(ROOT/'lib/linux/process.py'), sys.executable,
                                 '-c', "import sys;sys.stderr.write('e'*262144);print('stdout-final')"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('stdout-final', result.stdout)

    @unittest.skipUnless(os.name == 'posix', 'requires a native POSIX process group')
    def test_timeout_stops_owned_child(self):
        start = time.monotonic()
        result = subprocess.run([sys.executable, str(ROOT/'lib/linux/process.py'), sys.executable,
                                 '-c', 'import time;time.sleep(30)'],
                                env=dict(os.environ, AUTOOS_INSTALL_TIMEOUT_SECONDS='1'),
                                capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 124)
        self.assertLess(time.monotonic()-start, 8)


if __name__ == '__main__':
    unittest.main()
