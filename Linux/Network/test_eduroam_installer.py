import unittest
import importlib.util
import sys
from unittest.mock import patch, MagicMock

class TestEduroamInstaller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        script_path = os.path.join(os.path.dirname(__file__), "eduroam-linux-Universitat_Basel-UniBasel.py")
        spec = importlib.util.spec_from_file_location(
            "eduroam",
            script_path
        )
        cls.eduroam = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'argv', ['eduroam']):
            spec.loader.exec_module(cls.eduroam)

        # We need to mock methods called in __init__ of InstallerData so we can instantiate it cleanly
        cls.eduroam.InstallerData.__init__ = lambda self, silent=False, username='', password='', pfx_file='': None

    def test_byte_to_string(self):
        self.assertEqual(self.eduroam.byte_to_string([65, 66, 67]), "ABC")
        self.assertEqual(self.eduroam.byte_to_string([]), "")

    @patch('os.environ.get')
    def test_get_config_path(self, mock_env_get):
        mock_env_get.side_effect = lambda k: "/custom/path" if k == 'XDG_CONFIG_HOME' else "/home/user"
        self.assertEqual(self.eduroam.get_config_path(), "/custom/path")

        mock_env_get.side_effect = lambda k: None if k == 'XDG_CONFIG_HOME' else "/home/user"
        self.assertEqual(self.eduroam.get_config_path(), "/home/user/.config")

    @patch('subprocess.call')
    def test_installer_data_show_info_zenity(self, mock_call):
        installer = self.eduroam.InstallerData()
        installer.silent = False
        installer.graphics = 'zenity'
        installer.show_info("Test Message")
        mock_call.assert_called_once()
        args = mock_call.call_args[0][0]
        self.assertIn('zenity', args)
        self.assertIn('--info', args)
        self.assertIn('--text=Test Message', args)

    @patch('subprocess.call')
    def test_installer_data_show_info_kdialog(self, mock_call):
        installer = self.eduroam.InstallerData()
        installer.silent = False
        installer.graphics = 'kdialog'
        installer.show_info("Test Message")
        mock_call.assert_called_once()
        args = mock_call.call_args[0][0]
        self.assertIn('kdialog', args)
        self.assertIn('--msgbox', args)
        self.assertIn('Test Message', args)

    @patch('subprocess.call')
    def test_installer_data_show_info_yad(self, mock_call):
        installer = self.eduroam.InstallerData()
        installer.silent = False
        installer.graphics = 'yad'
        installer.show_info("Test Message")
        mock_call.assert_called_once()
        args = mock_call.call_args[0][0]
        self.assertIn('yad', args)
        self.assertIn('--text=Test Message', args)

    @patch('builtins.print')
    def test_installer_data_show_info_tty(self, mock_print):
        installer = self.eduroam.InstallerData()
        installer.silent = False
        installer.graphics = 'tty'
        installer.show_info("Test Message")
        mock_print.assert_called_once_with("Test Message")

    @patch('builtins.print')
    def test_installer_data_show_info_silent(self, mock_print):
        installer = self.eduroam.InstallerData()
        installer.silent = True
        installer.show_info("Test Message")
        mock_print.assert_not_called()

    @patch('subprocess.call')
    def test_installer_data_alert_zenity(self, mock_call):
        installer = self.eduroam.InstallerData()
        installer.silent = False
        installer.graphics = 'zenity'
        installer.alert("Alert Message")
        mock_call.assert_called_once()
        args = mock_call.call_args[0][0]
        self.assertIn('zenity', args)
        self.assertIn('--warning', args)
        self.assertIn('--text=Alert Message', args)

    @patch('subprocess.call')
    def test_installer_data_alert_kdialog(self, mock_call):
        installer = self.eduroam.InstallerData()
        installer.silent = False
        installer.graphics = 'kdialog'
        installer.alert("Alert Message")
        mock_call.assert_called_once()
        args = mock_call.call_args[0][0]
        self.assertIn('kdialog', args)
        self.assertIn('--sorry', args)
        self.assertIn('Alert Message', args)

    def test_prepare_network_block(self):
        # We need to test the private method __prepare_network_block of WpaConf
        user_data = MagicMock()
        user_data.username = "test_user@example.com"
        user_data.password = "secret123"

        # Test default configuration
        with patch.object(self.eduroam, 'get_config_path', return_value="/tmp/config"):
            network_block = self.eduroam.WpaConf._WpaConf__prepare_network_block("eduroam_test", user_data)

        self.assertIn('ssid="eduroam_test"', network_block)
        self.assertIn('identity="test_user@example.com"', network_block)
        self.assertIn(f'eap={self.eduroam.Config.eap_outer}', network_block)

        if self.eduroam.Config.eap_outer in ('PEAP', 'TTLS'):
            self.assertIn(f'phase2="auth={self.eduroam.Config.eap_inner}"', network_block)
            self.assertIn('password="secret123"', network_block)

if __name__ == '__main__':
    unittest.main()
