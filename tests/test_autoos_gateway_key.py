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
from keys_file import read_keys


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
        # Uppercase hostnames are normalized to lowercase
        self.assertTrue(is_local_gateway("http://LOCALHOST:20128/"))
        self.assertTrue(is_local_gateway("http://LocalHost:20128"))
        # Userinfo (username:password@) should be stripped - matches Python's urlparse().hostname
        self.assertTrue(is_local_gateway("http://user:pass@127.0.0.1:8080"))
        self.assertTrue(is_local_gateway("http://user@127.0.0.1:8080"))
        self.assertTrue(is_local_gateway("http://user:pass@localhost:8080"))
        self.assertTrue(is_local_gateway("http://user:pass@[::1]:8080"))

    def test_non_local(self):
        self.assertFalse(is_local_gateway("https://gw.example.com"))
        self.assertFalse(is_local_gateway("http://[::2]:20128"))
        self.assertFalse(is_local_gateway("https://server:20128"))
        self.assertFalse(is_local_gateway("http://remote.example.com:20128"))

    def test_unparseable(self):
        # Unparseable URLs treated as non-local for safety
        self.assertFalse(is_local_gateway("not-a-url"))

    def test_userinfo_last_at(self):
        # Userinfo with multiple @ - should use LAST @ in authority (parity with Python urlparse)
        # http://a@b@127.0.0.1:20128 -> host is 127.0.0.1 (local)
        self.assertTrue(is_local_gateway("http://a@b@127.0.0.1:20128"))
        # http://user:p@ss@127.0.0.1:20128 -> host is 127.0.0.1 (local)
        self.assertTrue(is_local_gateway("http://user:p@ss@127.0.0.1:20128"))
        # http://a@b@gw.example.com -> host is gw.example.com (non-local)
        self.assertFalse(is_local_gateway("http://a@b@gw.example.com"))

    def test_at_sign_after_the_authority_is_not_userinfo(self):
        # Same table as the bash and PowerShell tests: path/query/fragment never decide the host.
        for url in ("http://evil.example/x@127.0.0.1", "http://evil.example#@127.0.0.1",
                    "http://evil.example?x=@127.0.0.1", "http://localhost.",
                    "http://127.0.0.2", "http://0.0.0.0"):
            self.assertFalse(is_local_gateway(url), url)
        for url in ("http://127.0.0.1/x@y", "http://localhost?x=1", "http://127.0.0.1#frag",
                    "http://127.0.0.1:20128/v1?k=a@b"):
            self.assertTrue(is_local_gateway(url), url)

    def test_whitespace_trim(self):
        # Leading/trailing whitespace should be trimmed
        self.assertTrue(is_local_gateway("  http://127.0.0.1:20128  "))
        self.assertTrue(is_local_gateway("\thttp://localhost:20128\n"))
        self.assertFalse(is_local_gateway("  https://gw.example.com  "))


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

    def test_tilde_expansion(self):
        with patch.dict(os.environ, {"AUTOOS_HOST_CONFIG": "~/custom/host.yml"}):
            with patch("os.path.expanduser", return_value="/home/user/custom/host.yml"):
                self.assertEqual(_host_config_path(), Path("/home/user/custom/host.yml"))


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

    def test_host_yml_with_bom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            host_file = Path(tmpdir) / "host.yml"
            # Write with UTF-8 BOM
            with open(host_file, "wb") as f:
                f.write(b"\xef\xbb\xbfhost_name: bom-workstation\n")
            with patch.dict(os.environ, {"AUTOOS_HOST_CONFIG": str(host_file)}):
                os.environ.pop("AUTOOS_HOST_NAME", None)
                self.assertEqual(host_name(), "bom_workstation")


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
        import autoos_gateway_key
        autoos_gateway_key._NOTICED.clear()  # notices print once per process
        self.keys_file = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        self.keys_file.close()
        self._saved_env = {v: os.environ.pop(v, None) for v in self._SCRUB}
        # not merely unset: unset means "the real ~/.config/autoos/host.yml", which a dev box may have
        os.environ["AUTOOS_HOST_CONFIG"] = os.path.join(tempfile.gettempdir(), "autoos-no-such-host-%d.yml" % os.getpid())

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


class TestKeyFileFormats(unittest.TestCase):
    """Test that both key file formats (name=value and name: value) work."""
    
    _SCRUB = ("AUTOOS_OMNIROUTE_KEY", "AUTOOS_HOST_NAME", "AUTOOS_HOST_CONFIG")

    def setUp(self):
        import autoos_gateway_key
        autoos_gateway_key._NOTICED.clear()  # notices print once per process
        self.keys_file = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
        self.keys_file.close()
        self._saved_env = {v: os.environ.pop(v, None) for v in self._SCRUB}
        # not merely unset: unset means "the real ~/.config/autoos/host.yml", which a dev box may have
        os.environ["AUTOOS_HOST_CONFIG"] = os.path.join(tempfile.gettempdir(), "autoos-no-such-host-%d.yml" % os.getpid())

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

    # --- name=value format (api_keys.conf style) ---
    
    def test_new_local_field_equals_format(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("omniroute_workstation=local-key\n")
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "local-key")

    def test_new_server_field_equals_format(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("omniroute_server=server-key\n")
            self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "server-key")

    def test_legacy_local_fallback_equals_format(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("omniroute=legacy-local-key\n")
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                buf = StringIO()
                with redirect_stderr(buf):
                    result = resolve_client_key(os.environ, Path(self.keys_file.name))
                self.assertEqual(result, "legacy-local-key")
                lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1, lines)
                self.assertIn("'omniroute'", lines[0])
                self.assertIn("'omniroute_workstation'", lines[0])

    def test_legacy_server_fallback_equals_format(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("omniroute_client_laptop=legacy-server-key\n")
            with patch("autoos_gateway_key.host_name", return_value="laptop"):
                buf = StringIO()
                with redirect_stderr(buf):
                    result = resolve_client_key(os.environ, Path(self.keys_file.name))
                self.assertEqual(result, "legacy-server-key")
                lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1, lines)
                self.assertIn("'omniroute_client_laptop'", lines[0])
                self.assertIn("'omniroute_server'", lines[0])

    # --- name: value format (api-keys.yml style) ---
    
    def test_new_local_field_colon_format(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("omniroute_workstation: local-key\n")
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "local-key")

    def test_new_server_field_colon_format(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "https://gw.example.com"}):
            self.write_keys("omniroute_server: server-key\n")
            self.assertEqual(resolve_client_key(os.environ, Path(self.keys_file.name)), "server-key")

    def test_legacy_local_fallback_colon_format(self):
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128"}):
            self.write_keys("omniroute: legacy-local-key\n")
            with patch("autoos_gateway_key.host_name", return_value="workstation"):
                buf = StringIO()
                with redirect_stderr(buf):
                    result = resolve_client_key(os.environ, Path(self.keys_file.name))
                self.assertEqual(result, "legacy-local-key")
                lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1, lines)
                self.assertIn("'omniroute'", lines[0])
                self.assertIn("'omniroute_workstation'", lines[0])

    def test_legacy_server_fallback_colon_format(self):
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

    # --- read_keys() handles both formats ---
    
    def test_read_keys_equals_format(self):
        self.write_keys("key1=value1\nkey2=value2\n")
        result = read_keys(self.keys_file.name)
        self.assertEqual(result, {"key1": "value1", "key2": "value2"})

    def test_read_keys_colon_format(self):
        self.write_keys("key1: value1\nkey2: value2\n")
        result = read_keys(self.keys_file.name)
        self.assertEqual(result, {"key1": "value1", "key2": "value2"})

    def test_read_keys_mixed_format(self):
        # First occurrence wins (per keys_file.py spec)
        self.write_keys("key1=value1\nkey1: value2\nkey2: value2\nkey2=value3\n")
        result = read_keys(self.keys_file.name)
        self.assertEqual(result, {"key1": "value1", "key2": "value2"})

    def test_read_keys_ignores_comments_and_placeholders(self):
        self.write_keys("# comment\nkey1=value1\nkey2=REPLACE_WITH_KEY\nkey3: value3\n")
        result = read_keys(self.keys_file.name)
        self.assertEqual(result, {"key1": "value1", "key3": "value3"})

    def test_read_keys_strips_quotes(self):
        self.write_keys('key1="value1"\nkey2=\'value2\'\nkey3: "value3"\nkey4: \'value4\'\n')
        result = read_keys(self.keys_file.name)
        self.assertEqual(result, {"key1": "value1", "key2": "value2", "key3": "value3", "key4": "value4"})

    def test_read_keys_handles_bom(self):
        # Write with BOM
        with open(self.keys_file.name, "wb") as f:
            f.write(b"\xef\xbb\xbfkey1=value1\nkey2: value2\n")
        result = read_keys(self.keys_file.name)
        self.assertEqual(result, {"key1": "value1", "key2": "value2"})


class TestResolveOncePerProcessAndCli(unittest.TestCase):
    """One resolver: notices print once per process, and the shell callers use the CLI."""

    def setUp(self):
        import autoos_gateway_key
        autoos_gateway_key._NOTICED.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.keys = Path(self.tmp.name) / "api-keys.yml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_resolves_in_one_process_warn_once(self):
        self.keys.write_text("omniroute: legacy-key-value\n", encoding="utf-8")
        env = {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128", "AUTOOS_HOST_NAME": "ws"}
        buf = StringIO()
        with redirect_stderr(buf):
            self.assertEqual(resolve_client_key(env, self.keys), "legacy-key-value")
            self.assertEqual(resolve_client_key(env, self.keys), "legacy-key-value")
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, lines)

    def _cli(self, *args, env=None):
        import subprocess
        tools = Path(__file__).resolve().parent.parent / "tools" / "autoos_gateway_key.py"
        full = {k: v for k, v in os.environ.items() if not k.startswith("AUTOOS_")}
        full.update(env or {})
        return subprocess.run([sys.executable, str(tools), *args], env=full,
                              capture_output=True, text=True)

    def test_cli_resolve_prints_the_key_and_one_notice(self):
        self.keys.write_text("omniroute: legacy-key-value\n", encoding="utf-8")
        r = self._cli("resolve", str(self.keys),
                      env={"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128", "AUTOOS_HOST_NAME": "ws"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "legacy-key-value\n")
        self.assertEqual(len([ln for ln in r.stderr.splitlines() if ln.strip()]), 1, r.stderr)
        self.assertNotIn("legacy-key-value", r.stderr)

    def test_cli_resolve_is_case_insensitive_like_the_library(self):
        self.keys.write_text("OmniRoute_WS: mixed-case-key\n", encoding="utf-8")
        r = self._cli("resolve", str(self.keys),
                      env={"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128", "AUTOOS_HOST_NAME": "ws"})
        self.assertEqual((r.returncode, r.stdout), (0, "mixed-case-key\n"), r.stderr)

    def test_cli_missing_key_exits_1_naming_the_field_or_is_silent_when_optional(self):
        self.keys.write_text("other: x\n", encoding="utf-8")
        env = {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128", "AUTOOS_HOST_NAME": "ws"}
        r = self._cli("resolve", str(self.keys), env=env)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("omniroute_ws", r.stderr)
        o = self._cli("resolve", "--optional", str(self.keys), env=env)
        self.assertEqual((o.returncode, o.stdout, o.stderr), (0, "", ""))

    def test_cli_no_notice_silences_the_deprecation_line(self):
        self.keys.write_text("omniroute: legacy-key-value\n", encoding="utf-8")
        r = self._cli("resolve", "--no-notice", str(self.keys),
                      env={"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128", "AUTOOS_HOST_NAME": "ws"})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "legacy-key-value\n", ""))


class TestSharedUrlTable(unittest.TestCase):
    """The table tests/fixtures/gateway-url-classification.json is also read by the bash and the
    PowerShell tests: one list of URLs, one expected answer, three implementations."""

    def test_every_row(self):
        import json
        table = Path(__file__).resolve().parent / "fixtures" / "gateway-url-classification.json"
        rows = json.loads(table.read_text(encoding="utf-8"))
        self.assertGreater(len(rows), 60)
        wrong = [(r["url"], r["local"]) for r in rows if is_local_gateway(r["url"]) != r["local"]]
        self.assertEqual(wrong, [])

    def test_cli_is_local_agrees_with_the_function(self):
        import subprocess
        tools = Path(__file__).resolve().parent.parent / "tools" / "autoos_gateway_key.py"
        for url, want in (("", 0), ("http://localhost:20128", 0), ("http://evil.example\\@127.0.0.1", 1),
                          ("http://127.1", 1), ("   ", 1)):
            r = subprocess.run([sys.executable, str(tools), "is-local", url], capture_output=True, text=True)
            self.assertEqual(r.returncode, want, url)


class TestRound4fSmallRules(unittest.TestCase):
    def test_devnull_as_the_keys_file_means_keyless_not_the_repo_file(self):
        # install.sh passes os.devnull when it has no secrets file: only the env key may answer
        with patch.dict(os.environ, {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:20128", "AUTOOS_HOST_NAME": "ws"}, clear=False):
            os.environ.pop("AUTOOS_OMNIROUTE_KEY", None)
            with self.assertRaises(KeyError):
                resolve_client_key(os.environ, Path(os.devnull))
            self.assertEqual(resolve_client_key({**os.environ, "AUTOOS_OMNIROUTE_KEY": "from-env"}, Path(os.devnull)), "from-env")

    def test_host_name_with_a_space_before_the_colon_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "host.yml"
            f.write_text("host_name :  spacey\n", encoding="utf-8")
            from autoos_gateway_key import host_name
            self.assertEqual(host_name({"AUTOOS_HOST_CONFIG": str(f)}), "spacey")


if __name__ == "__main__":
    unittest.main()