import unittest
import importlib.util
import sys
import os
import contextlib
from unittest.mock import patch, MagicMock

class TestServe(unittest.TestCase):
    def setUp(self):
        # Load lib/linux/serve.py
        file_path = os.path.join(os.path.dirname(__file__), '..', 'lib', 'linux', 'serve.py')
        spec = importlib.util.spec_from_file_location("serve", file_path)
        self.serve = importlib.util.module_from_spec(spec)

        original_argv = sys.argv
        sys.argv = ['serve.py']
        try:
            spec.loader.exec_module(self.serve)
        finally:
            sys.argv = original_argv

        # Reset state
        self.serve.LOG.clear()
        self.serve.RUN.update({"running": False, "done": 0, "total": 0, "summary": ""})

    @patch('subprocess.Popen')
    def test_run_install_success(self, mock_popen):
        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.stdout = ["+ OK", "> [1/2] git", "! Warning", "> [2/2] node"]
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        # Run function
        self.serve.run_install(['git', 'node'], {'test-answer': 'yes'}, False)

        # Verify Popen arguments
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["bash", "setup.sh", "--only", "git,node", "--yes", "--no-color"])
        self.assertIn('AUTOOS_ANSWER_TEST_ANSWER', kwargs['env'])
        self.assertEqual(kwargs['env']['AUTOOS_ANSWER_TEST_ANSWER'], 'yes')
        self.assertIn('AUTOOS_NO_COLOR', kwargs['env'])
        self.assertEqual(kwargs['env']['AUTOOS_NO_COLOR'], '1')

        # Verify LOG
        self.assertEqual(len(self.serve.LOG), 6) # 1 initial step + 4 from stdout + 1 exit
        self.assertEqual(self.serve.LOG[0]['level'], 'step')
        self.assertEqual(self.serve.LOG[0]['text'], '$ bash setup.sh --only git,node --yes --no-color')

        self.assertEqual(self.serve.LOG[1]['level'], 'ok')
        self.assertEqual(self.serve.LOG[1]['text'], '+ OK')

        self.assertEqual(self.serve.LOG[2]['level'], 'step')
        self.assertEqual(self.serve.LOG[2]['text'], '> [1/2] git')

        self.assertEqual(self.serve.LOG[5]['level'], 'ok')
        self.assertEqual(self.serve.LOG[5]['text'], '--- exit code 0 ---')

        # Verify RUN state
        self.assertFalse(self.serve.RUN['running'])
        self.assertEqual(self.serve.RUN['done'], 2)
        self.assertEqual(self.serve.RUN['total'], 2)
        self.assertEqual(self.serve.RUN['summary'], 'finished (exit 0)')

    @patch('subprocess.Popen')
    def test_run_install_dry_run(self, mock_popen):
        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.stdout = []
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        # Run function
        self.serve.run_install(['git'], None, True)

        # Verify dry-run argument was passed
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["bash", "setup.sh", "--only", "git", "--yes", "--no-color", "--dry-run"])

    @patch('subprocess.Popen')
    def test_run_install_failure(self, mock_popen):
        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.stdout = ["x Error installing"]
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        # Run function
        self.serve.run_install(['git'], {}, False)

        # Verify LOG captured the error
        self.assertEqual(self.serve.LOG[-1]['level'], 'err')
        self.assertEqual(self.serve.LOG[-1]['text'], '--- exit code 1 ---')
        self.assertEqual(self.serve.RUN['summary'], 'finished (exit 1)')

if __name__ == '__main__':
    unittest.main()
