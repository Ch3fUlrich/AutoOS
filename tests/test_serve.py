import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# We need to mock sys.argv both during import AND execution of tests,
# because patch() decorators could re-import or hit code paths checking it.
_mock_argv = ['serve.py', '.', '8777']

with patch.object(sys, 'argv', _mock_argv):
    import importlib.util
    spec = importlib.util.spec_from_file_location("serve", "lib/linux/serve.py")
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)

class TestServe(unittest.TestCase):
    def setUp(self):
        # Ensure sys.argv is mocked during each test to avoid issues when
        # unittest discovery runs with ['m', 'unittest', 'discover', 'tests']
        self.argv_patcher = patch.object(sys, 'argv', _mock_argv)
        self.argv_patcher.start()

    def tearDown(self):
        self.argv_patcher.stop()

    def test_classify(self):
        self.assertEqual(serve.classify("+ something"), "ok")
        self.assertEqual(serve.classify("! something"), "warn")
        self.assertEqual(serve.classify("x something"), "err")
        self.assertEqual(serve.classify("> something"), "step")
        self.assertEqual(serve.classify("run: something"), "muted")
        self.assertEqual(serve.classify("would run: something"), "muted")
        self.assertEqual(serve.classify("would something"), "muted")
        self.assertEqual(serve.classify("unmatched line"), "")

    @patch.object(serve, 'ROOT', new_callable=MagicMock)
    def test_component_platforms(self, mock_root):
        mock_windows_path = MagicMock()
        mock_windows_path.exists.return_value = True
        mock_windows_path.read_text.return_value = json.dumps({
            "categories": [
                {"components": [{"id": "handy"}, {"id": "vscode"}]}
            ]
        })

        mock_linux_path = MagicMock()
        mock_linux_path.exists.return_value = True
        mock_linux_path.read_text.return_value = json.dumps({
            "categories": [
                {"components": [{"id": "handy"}, {"id": "git"}]}
            ]
        })

        mock_macos_path = MagicMock()
        mock_macos_path.exists.return_value = False

        def fake_div(other):
            if other == "windows.json": return mock_windows_path
            if other == "linux.json": return mock_linux_path
            if other == "macos.json": return mock_macos_path
            return MagicMock()

        mock_catalog_dir = MagicMock()
        mock_catalog_dir.__truediv__.side_effect = fake_div

        def fake_root_div(other):
            if other == "catalog": return mock_catalog_dir
            return MagicMock()

        mock_root.__truediv__.side_effect = fake_root_div

        platforms = serve.component_platforms()
        self.assertEqual(platforms.get("handy"), ["windows", "linux"])
        self.assertEqual(platforms.get("vscode"), ["windows"])
        self.assertEqual(platforms.get("git"), ["linux"])
        self.assertNotIn("unknown", platforms)

    @patch("subprocess.run")
    @patch.object(serve, 'ROOT', new_callable=MagicMock)
    def test_build_state(self, mock_root, mock_run):
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = json.dumps({
            "system": {
                "host": "test-host",
                "distribution": "Ubuntu",
                "architecture": "x86_64",
                "model": "Test Model",
                "cpu": "Test CPU",
                "cores": "4",
                "memory": "16 GB",
                "free disk": "100 GB",
                "user": "testuser",
                "display": "headless",
                "environment": "unknown"
            },
            "suggested": "server",
            "wsl": {"isWsl": False, "version": "", "distro": ""}
        }) + "\n"
        mock_run.return_value = mock_run_result

        mock_linux_json = MagicMock()
        mock_linux_json.read_text.return_value = json.dumps({
            "categories": [
                {
                    "name": "Test Category",
                    "requiresDisplay": False,
                    "components": [
                        {"id": "comp1", "name": "Comp 1", "description": "desc", "provider": "prov1", "package": "pack1"}
                    ]
                },
                {
                    "name": "Desktop Apps",
                    "requiresDisplay": True,
                    "components": [
                        {"id": "gui1", "name": "GUI App", "description": "desc", "provider": "prov1", "package": "pack1"}
                    ]
                }
            ]
        })

        mock_catalog_dir = MagicMock()
        mock_catalog_dir.__truediv__.side_effect = lambda x: mock_linux_json if x == "linux.json" else MagicMock()
        mock_root.__truediv__.side_effect = lambda x: mock_catalog_dir if x == "catalog" else MagicMock()

        with patch.object(serve, "component_platforms", return_value={"comp1": ["linux"]}):
            state = serve.build_state()
            self.assertEqual(state["platform"], "Linux")
            self.assertEqual(state["system"]["architecture"], "x86_64")

            # Check headless filtering (gui1 should be skipped because system is headless)
            comp_ids = [c["id"] for c in state["components"]]
            self.assertIn("comp1", comp_ids)
            self.assertNotIn("gui1", comp_ids)

    @patch("subprocess.Popen")
    def test_run_install(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout = ["> [1/1] Step", "+ ok line", "! warn line", "x err line", "run: log line"]
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        with patch.object(serve, "LOG", []), patch.object(serve, "RUN", {"running": False, "done": 0, "total": 0, "summary": ""}):
            serve.run_install(["comp1"], {"param1": "val1"}, dry=True)

            # verify RUN state updated
            self.assertFalse(serve.RUN["running"])
            self.assertEqual(serve.RUN["done"], 1)

            # verify log lines
            log_texts = [l["text"] for l in serve.LOG]
            self.assertIn("+ ok line", log_texts)
            self.assertIn("! warn line", log_texts)
            self.assertIn("--- exit code 0 ---", log_texts)

    def test_handler_authed(self):
        class DummyHandler:
            pass
        handler = DummyHandler()
        handler.__class__ = serve.Handler

        qs_valid = {"token": [serve.TOKEN]}
        qs_invalid = {"token": ["wrong"]}
        qs_empty = {}

        self.assertTrue(handler._authed(qs_valid))
        self.assertFalse(handler._authed(qs_invalid))
        self.assertFalse(handler._authed(qs_empty))

if __name__ == '__main__':
    unittest.main()
