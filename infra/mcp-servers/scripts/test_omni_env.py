import unittest
from unittest.mock import patch
import _omni_env


class TestOmniEnv(unittest.TestCase):
    @patch("_omni_env._inspect")
    def test_detect_network_custom(self, mock_inspect):
        mock_inspect.return_value = "custom-network  "
        net = _omni_env.detect_network()
        self.assertEqual(net, "custom-network")
        mock_inspect.assert_called_once_with("omnigraph-server", "{{range $n,$_ := .NetworkSettings.Networks}}{{$n}} {{end}}")

    @patch("_omni_env._inspect")
    def test_detect_network_fallback(self, mock_inspect):
        mock_inspect.return_value = None
        net = _omni_env.detect_network(default="fallback-net")
        self.assertEqual(net, "fallback-net")

    @patch("_omni_env._inspect")
    def test_detect_minio_store_volume(self, mock_inspect):
        mock_inspect.return_value = "volume|vol_name|/var/minio|/data\n"
        kind, val = _omni_env.detect_minio_store()
        self.assertEqual(kind, "volume")
        self.assertEqual(val, "vol_name")

    @patch("_omni_env._inspect")
    def test_detect_minio_store_bind(self, mock_inspect):
        mock_inspect.return_value = "bind||/host/path/minio|/data\n"
        kind, val = _omni_env.detect_minio_store()
        self.assertEqual(kind, "bind")
        self.assertEqual(val, "/host/path/minio")

    @patch("_omni_env._inspect")
    def test_detect_minio_store_absent(self, mock_inspect):
        mock_inspect.return_value = None
        kind, val = _omni_env.detect_minio_store()
        self.assertIsNone(kind)
        self.assertIsNone(val)

    def test_describe(self):
        desc = _omni_env.describe("test-net", "volume", "my-vol")
        self.assertEqual(desc, "network=test-net volume=my-vol")

        desc_none = _omni_env.describe("test-net", None, None)
        self.assertEqual(desc_none, "network=test-net minio=<not detected>")


def test_site_env_env_wins_over_file(tmp_path, monkeypatch):
    path = tmp_path / "site.env"
    path.write_text("FOO=from-file\n")
    monkeypatch.setenv("FOO", "from-env")
    assert _omni_env.site_env("FOO", path=str(path)) == "from-env"


def test_site_env_file_wins_over_default(tmp_path, monkeypatch):
    path = tmp_path / "site.env"
    path.write_text("FOO=from-file\n")
    monkeypatch.delenv("FOO", raising=False)
    assert _omni_env.site_env("FOO", default="fallback", path=str(path)) == "from-file"


def test_site_env_handles_comments_blanks_and_quotes(tmp_path, monkeypatch):
    path = tmp_path / "site.env"
    path.write_text(
        "# a comment\n"
        "\n"
        "  QUOTED = 'quoted value'  \n"
        'DOUBLE="double value"\n'
        "BARE=bare value\n"
    )
    for name in ("QUOTED", "DOUBLE", "BARE"):
        monkeypatch.delenv(name, raising=False)
    assert _omni_env.site_env("QUOTED", path=str(path)) == "quoted value"
    assert _omni_env.site_env("DOUBLE", path=str(path)) == "double value"
    assert _omni_env.site_env("BARE", path=str(path)) == "bare value"


def test_site_env_missing_file_returns_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FOO", raising=False)
    missing = tmp_path / "does-not-exist.env"
    assert _omni_env.site_env("FOO", default="fallback", path=str(missing)) == "fallback"


if __name__ == "__main__":
    unittest.main()
