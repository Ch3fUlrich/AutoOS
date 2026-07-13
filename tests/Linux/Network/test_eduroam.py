import unittest
import os
import sys
import importlib.util
from unittest.mock import patch, MagicMock

class TestDetectDesktopEnvironment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module_name = 'eduroam_module'
        file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../Linux/Network/eduroam-linux-Universitat_Basel-UniBasel.py'))

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        cls.module = importlib.util.module_from_spec(spec)

        original_argv = sys.argv
        sys.argv = [file_path]
        try:
            spec.loader.exec_module(cls.module)
        finally:
            sys.argv = original_argv

    def test_kde(self):
        with patch.dict(os.environ, {'KDE_FULL_SESSION': 'true'}, clear=True):
            self.assertEqual(self.module.detect_desktop_environment(), 'kde')

    def test_gnome(self):
        with patch.dict(os.environ, {'GNOME_DESKTOP_SESSION_ID': '123'}, clear=True):
            self.assertEqual(self.module.detect_desktop_environment(), 'gnome')

    @patch('subprocess.Popen')
    def test_xfce(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b' _DT_SAVE_MODE = "xfce4"\n', b'')
        mock_popen.return_value = mock_process

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.module.detect_desktop_environment(), 'xfce')

    @patch('subprocess.Popen')
    def test_generic_other_xprop(self, mock_popen):
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b' _DT_SAVE_MODE = "other"\n', b'')
        mock_popen.return_value = mock_process

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.module.detect_desktop_environment(), 'generic')

    @patch('subprocess.Popen')
    def test_generic_oserror(self, mock_popen):
        mock_popen.side_effect = OSError("xprop not found")

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.module.detect_desktop_environment(), 'generic')

    @patch('subprocess.Popen')
    def test_generic_runtimeerror(self, mock_popen):
        mock_popen.side_effect = RuntimeError("runtime error")

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.module.detect_desktop_environment(), 'generic')

if __name__ == '__main__':
    unittest.main()
