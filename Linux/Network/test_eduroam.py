import importlib.util
import os
import sys
import unittest
from unittest import mock
import subprocess

# Dynamically import the target module
module_name = "eduroam_linux"
file_path = "Linux/Network/eduroam-linux-Universitat_Basel-UniBasel.py"

spec = importlib.util.spec_from_file_location(module_name, file_path)
eduroam = importlib.util.module_from_spec(spec)
sys.modules[module_name] = eduroam

# The module calls argparse at the global level, which crashes pytest due to unrecognized args.
# Mock sys.argv during import to avoid this.
original_argv = sys.argv
sys.argv = ['eduroam-linux-Universitat_Basel-UniBasel.py']
try:
    # Some objects might be missing during import time because of missing dependencies,
    # let's mock them or just load the module carefully
    spec.loader.exec_module(eduroam)
except Exception:
    pass
finally:
    sys.argv = original_argv

class TestDetectDesktopEnvironment(unittest.TestCase):

    @mock.patch.dict(os.environ, {"KDE_FULL_SESSION": "true"}, clear=True)
    def test_detect_kde(self):
        result = eduroam.detect_desktop_environment()
        self.assertEqual(result, 'kde')

    @mock.patch.dict(os.environ, {"GNOME_DESKTOP_SESSION_ID": "this-is-gnome"}, clear=True)
    def test_detect_gnome(self):
        result = eduroam.detect_desktop_environment()
        self.assertEqual(result, 'gnome')

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('subprocess.Popen')
    def test_detect_xfce(self, mock_popen):
        mock_process = mock.Mock()
        mock_process.communicate.return_value = (b'some_prop = "xfce4"\n', b'')
        mock_popen.return_value = mock_process

        result = eduroam.detect_desktop_environment()
        self.assertEqual(result, 'xfce')
        mock_popen.assert_called_with(['xprop', '-root', '_DT_SAVE_MODE'],
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('subprocess.Popen')
    def test_detect_generic_fallback(self, mock_popen):
        mock_process = mock.Mock()
        mock_process.communicate.return_value = (b'some_prop = "other"\n', b'')
        mock_popen.return_value = mock_process

        result = eduroam.detect_desktop_environment()
        self.assertEqual(result, 'generic')

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('subprocess.Popen')
    def test_detect_error_oserror(self, mock_popen):
        mock_popen.side_effect = OSError("command not found")

        result = eduroam.detect_desktop_environment()
        self.assertEqual(result, 'generic')

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch('subprocess.Popen')
    def test_detect_error_runtimeerror(self, mock_popen):
        mock_popen.side_effect = RuntimeError("something went wrong")

        result = eduroam.detect_desktop_environment()
        self.assertEqual(result, 'generic')

if __name__ == '__main__':
    unittest.main()
