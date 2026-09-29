#!/usr/bin/env python3
"""Tests for tools/autoos_gateway_key.py"""

import os
import sys
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

# Add tools to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from autoos_gateway_key import (
    is_local_gateway,
    _host_config_path,
    _normalize_hostname,
    host_name,
    client_key_field,
    resolve_client_key,
)


class TestIsLocalGateway(unittest.TestCase):
    def test_empty_url(self):
        self.assertTrue(is_local_gateway(""))
        self.assertTrue(is_local_gateway(None))

    def test_localhost(self):
        self.assertTrue(is_local_gateway("http://localhost:20128"))
        self.assertTrue(is_local_gateway("http://127.0.0.1:20128"))
        self.assertTrue(is_local_gateway("http://[::1]:20128"))
        self.assertTrue(is_local_gateway("https://localhost:20128"))
        self.assertTrue(is_local_gateway("http://localhost"))

    def test_non_local(self):
        self.assertFalse(is_local_gateway("https://gw.example.com"))
        self.assertFalse(is_local_gateway("http://[::2]:20128"))
        self.assertFalse(is_local_gateway("https://server:20128"))
        self.assertFalse(is_local_gateway("http://remote.example.com:20128"))

    def test_unparseable(self):
        # Unparseable URLs treated as non-local for safety
        self.assertFalse(is_local_gateway("not-a-url"))


class TestNormalizeHostname(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(_normalize_hostname("laptop"), "laptop")
        self.assertEqual(_normalize_hostname("LAPTOP"), "laptop")

    def test_fqdn(self):
        self.assertEqual(_normalize_hostname("laptop.corp.example.com"), "laptop")
        self.assertEqual(_normalize_hostname("SERVER.DOMAIN.LOCAL"), "server")

    def test_special_chars(self):
        self.assertEqual(_normalize_hostname("my-host"), "my_host")
        # Per spec: lowercase, cut at first '.', then replace special chars.
        # "my.host" -> "my" (first label) -> "my" (no special chars)
        self.assertEqual(_normalize_hostname("my.host"), "my")
        self.assertEqual(_normalize_hostname("my@host"), "my_host")
        self.assertEqual(_normalize_hostname("my host"), "my_host")

    def test_empty(self):
        self.assertEqual(_normalize_hostname(""), "")


class TestHostConfigPath(unittest.TestCase):
    def test_env_override(self):
        with patch.dict(os.environ, {"AUTOOS_HOST_CONFIG": "/custom/path/host.yml"}):
            self.assertEqual(_host_config_path(), Path("/custom/path/host.yml"))

    def test_windows_path(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.platform", "win32"):
                with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}):
                    path = _host_config_path()
                    expected = Path(r"C:\Users\test\AppData\Local") / "autoos" / "host.yml"
                    self.assertEqual(path, expected)

    def test_unix_path(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.platform", "linux"):
                with patch.dict(os.environ, {"HOME": "/home/test"}):
                    path = _host_config_path()
                    self.assertEqual(path, Path("/home/test") / ".config" / "autoos" / "host.yml")

    def test_xdg_config_home(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.platform", "linux"):
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/config", "HOME": "/home/test"}):
                    path = _host_config_path()
                    self.assertEqual(path, Path("/custom/config") / "autoos" / "host.yml")


class TestHostName(unittest.TestCase):
    def test_env_override(self):
        with patch.dict(os.environ, {"AUTOOS_HOST_NAME": "my-host"}):
            self.assertEqual(host_name(), "my_host")

    def test_from_host_yml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            host_file = Path(tmpdir) / "host.yml"
            host_file.write_text("host_name: my-workstation\n")
            with patch.dict(os.environ, {"AUTOOS_HOST_CONFIG": str(host_file)}):
                # An ambient AUTOOS_HOST_NAME would win over the file; drop it.
                os.environ.pop("AUTOOS_HOST_NAME", None)
                self.assertEqual(host_name(), "my_workstation")

    def test_hostname_fallback(self):
        with patch("socket.gethostname", return_value="Laptop.corp.example.com"):
            with patch.dict(os.environ, {}, clear=True):
                # Mock _host_config_path to return non-existent file
                with patch("autoos_gateway_key._host_config_path", return_value=Path("/nonexistent/host.yml")):
                    buf = StringIO()
                    with redirect_stderr(buf):
                        result = host_name()
                    self.assertEqual(result, "laptop")
                    # ONE notice line naming the field it will look up.
                    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
                    self.assertEqual(len(lines), 1, lines)
                    self.assertIn("laptop", lines[0])

    def test_server_hostname(self):
        with patch("socket.gethostname", return_value="server"):
            with patch.dict(os.environ, {}, clear=True):
                with patch("autoos_gateway_key._host_config_path", return_value=Path("/nonexistent/host.yml")):
                    result = host_name()
                    self.assertEqual(result, "server")


class TestClientKeyField(unittest.TestCase):
    def test_local_gateway(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                self.assertEqual(client_key_field(), "omniroute_workstation")

    def test_non_local_gateway(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.assertEqual(client_key_field(), "omniroute_server")

    def test_unset_url(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("autoos_gateway_key.host_name", return_value="laptop"):
                self.assertEqual(client_key_field(), "omniroute_laptop")


class TestResolveClientKey(unittest.TestCase):
    # Ambient operator env (a real AUTOOS_OMNIROUTE_KEY / AUTOOS_HOST_NAME on a
    # dev box) must never leak into fixture resolution - scrub it per test.
    _SCRUB = ("AUTOOS_OMNIROUTE_KEY", "AUTOOS_HOST_NAME", "AUTOOS_HOST_CONFIG")

    def setUp(self):
        self.keys_file = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        self.keys_file.close()
        self._saved_env = {v: os.environ.pop(v, None) for v in self._SCRUB}

    def tearDown(self):
        os.unlink(self.keys_file.name)
        for v, val in self._saved_env.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val

    def write_keys(self, content: str):
        with open(self.keys_file.name, "w") as f:
            f.write(content)

    def test_env_wins(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_KEY": "env-key"}):
            self.write_keys("omniroute: file-key\n")
            self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "env-key")

    def test_new_local_field(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("omniroute_workstation: local-key\n")
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "local-key")

    def test_new_server_field(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("omniroute_server: server-key\n")
            self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "server-key")

    def test_legacy_local_fallback(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("omniroute: legacy-local-key\n")
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                buf = StringIO()
                with redirect_stderr(buf):
                    result = resolve_client_key(os.environ, Path(self.keys_file.name))
                self.assertEqual(result, "legacy-local-key")
                # ONE deprecation line naming old + new FIELD, never a value.
                lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1, lines)
                self.assertIn("'omniroute'", lines[0])
                self.assertIn("'omniroute_workstation'", lines[0])
                self.assertNotIn("legacy-local-key", lines[0])

    def test_legacy_server_fallback(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("omniroute_client_laptop: legacy-server-key\n")
            with patch("autoos_gateway_key.host_name", return_value="laptop"):
                buf = StringIO()
                with redirect_stderr(buf):
                    result = resolve_client_key(os.environ, Path(self.keys_file.name))
                self.assertEqual(result, "legacy-server-key")
                lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1, lines)
                self.assertIn("'omniroute_client_laptop'", lines[0])
                self.assertIn("'omniroute_server'", lines[0])
                self.assertNotIn("legacy-server-key", lines[0])

    def test_missing_key_local(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("unrelated: sk-test-decoy-value\n")  # empty of the wanted field
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                with self.assertRaises(KeyError) as cm:
                    resolve_client_key(os.environ, Path(self.keys_file.name))
                msg = str(cm.exception)
                self.assertIn("omniroute_workstation", msg)
                self.assertIn("local gateway", msg)
                # Never a value, never the URL.
                self.assertNotIn("sk-test-decoy-value", msg)
                self.assertNotIn("127.0.0.1", msg)

    def test_missing_key_server(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("unrelated: sk-test-decoy-value\n")
            with self.assertRaises(KeyError) as cm:
                resolve_client_key(os.environ, Path(self.keys_file.name))
            msg = str(cm.exception)
            self.assertIn("omniroute_server", msg)
            self.assertIn("non-local gateway", msg)
            self.assertNotIn("sk-test-decoy-value", msg)
            self.assertNotIn("gw.example.com", msg)

    def test_server_host_local_url_is_omniroute_server(self):
        # On the server machine itself host_name is "server", so its local
        # lookup is omniroute_server - no special case in code.
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128",
                                     "AUTOOS_HOST_NAME": "server"}):
            self.assertEqual(client_key_field(os.environ), "omniroute_server")

    def test_placeholder_ignored(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("omniroute_workstation: REPLACE_WITH_KEY\n")
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                with self.assertRaises(KeyError):
                    resolve_client_key(os.environ, Path(self.keys_file.name))

    def test_case_insensitive_field(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("OMNIROUTE_SERVER: server-key\n")
            self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "server-key")

    def test_quoted_values(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys('omniroute_server: "server-key"\n')
            self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "server-key")

    def test_single_quoted_values(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("omniroute_server: 'server-key'\n")
            self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "server-key")


if __name__ == "__main__":
    unittest.main()