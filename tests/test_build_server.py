"""Offline regression contracts for the build-server installers."""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'lib/linux/build-server.sh').read_text()


class BuildServer(unittest.TestCase):
    def test_catalog_hooks(self):
        def entries(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from entries(child)
            elif isinstance(value, list):
                for child in value:
                    yield from entries(child)
        rows = {v.get('id'): v for v in entries(json.loads(
            (ROOT / 'catalog/linux.json').read_text())) if 'id' in v}
        for name in ('github-actions-runner', 'gitleaks'):
            self.assertEqual(rows[name].get('postInstall'), 'install_' + name.replace('-', '_'))

    def test_required_url(self):
        self.assertIn('${OMNIROUTE_BASE_URL:-}', SOURCE)
        self.assertIn('set OMNIROUTE_BASE_URL', SOURCE)
        self.assertNotRegex(SOURCE, r'https?://(?!github\.com)[^/\s"}]+')

    def test_absolute_disk_refusal(self):
        self.assertNotIn('AUTOOS_DATA_DISK_WIPE', SOURCE)
        self.assertIn('if [[ -n "$existing_fs" ]]; then', SOURCE)

    def test_pin_fails_closed(self):
        block = SOURCE.split('if [[ "$head" != "$_OMNIROUTE_FORK_SHA" ]]; then')[1].split('fi')[0]
        self.assertIn('return 1', block)

    def test_gateway_secrets(self):
        self.assertNotRegex(SOURCE, r'python3[^\n]*\$(jwt_secret|api_key_secret|initial_password)')
        self.assertNotIn('cp "$dir/.env.example"', SOURCE)
        self.assertIn('os.O_EXCL', SOURCE)
        self.assertIn('0o600', SOURCE)

    def test_loopback(self):
        self.assertIn('APP_BIND_HOST=127.0.0.1', SOURCE)
        self.assertIn('REQUIRE_API_KEY=true', SOURCE)
        self.assertIn('127.0.0.1:${DASHBOARD_PORT:-20128}:${DASHBOARD_PORT:-20128}', SOURCE)


if __name__ == '__main__':
    unittest.main()
