"""Offline regression contracts for the build-server installers."""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = '\n'.join(p.read_text() for p in sorted((ROOT / 'lib/linux').glob('build-server*.sh')))


class BuildServer(unittest.TestCase):
    def test_round_two_contracts(self):
        self.assertIn('has_cmd blkid', SOURCE)
        self.assertIn('case "$rc" in', SOURCE)
        self.assertIn('[[ -t 0 ]]', SOURCE)
        self.assertIn('list-unit-files --no-legend ssh.socket', SOURCE)
        self.assertIn('install_github_actions_runner() (', SOURCE)
        self.assertIn('--require-hashes', SOURCE)
        self.assertIn('_PLANGRAPH_SHA=', SOURCE)
        self.assertNotIn('install -q --upgrade pip', SOURCE)
        self.assertIn('existing client configuration has no key - refusing', SOURCE)

    def test_required_steps_report_failure(self):
        import subprocess
        script = '''
AUTOOS_ROOT="$PWD"; AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""
SYS_HOME="$1"; SYS_USER=test
. lib/linux/build-server.sh
ui_err() { printf 'ERROR:%s\\n' "$*"; }
ui_ok() { printf 'SUCCESS:%s\\n' "$*"; }
ui_muted() { :; }; ui_info() { :; }
has_cmd() { return 0; }
git() { return 1; }; apt-get() { return 1; }; run() { return 1; }
if "$2"; then exit 9; fi
'''
        import tempfile
        for component in ('install_plangraph_omniroute_node',
                          'install_ssh_tailscale_access', 'install_rust_toolchain'):
            with tempfile.TemporaryDirectory() as home:
                result = subprocess.run(['bash', '-c', script, 'test', home, component],
                                        cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('ERROR:', result.stdout)
                self.assertNotIn('SUCCESS:', result.stdout)

    def test_client_preserves_keyless_file(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            env = pathlib.Path(home) / '.config/autoos/ai-stack/opencode.env'
            env.parent.mkdir(parents=True)
            env.write_text('EXTRA=1\n')
            result = subprocess.run(['bash', '-c', '''
AUTOOS_ROOT="$PWD"; SYS_HOME="$1"; AUTOOS_DRY_RUN=0
. lib/linux/build-server.sh
ui_err() { :; }; ui_ok() { :; }; ui_muted() { :; }
OMNIROUTE_BASE_URL=unused OMNIROUTE_API_KEY=unused install_omniroute_client_config
''', 'test', home], cwd=ROOT)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(env.read_text(), 'EXTRA=1\n')

    def test_runner_preserves_cwd_on_failure(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(['bash', '-c', '''
AUTOOS_ROOT="$PWD"; SYS_HOME="$1"; AUTOOS_DRY_RUN=0; AUTOOS_SUDO=""
. lib/linux/build-server.sh
ui_err() { :; }; ui_ok() { :; }; ui_muted() { :; }
has_cmd() { return 0; }; gh() { echo private; }; curl() { return 1; }
before="$PWD"
GH_RUNNER_REPO=owner/repo install_github_actions_runner && exit 9
[[ "$PWD" == "$before" ]]
''', 'test', home], cwd=ROOT)
            self.assertEqual(result.returncode, 0)

    def test_required_disk_steps_guarded(self):
        for command in ('wipefs -a', 'parted -s', 'mkfs.ext4', 'mount "$mnt"'):
            lines = [line for line in SOURCE.splitlines() if '$AUTOOS_SUDO ' + command in line]
            self.assertTrue(lines, command)
            self.assertTrue(all('|| { ui_err' in line and 'return 1' in line for line in lines))

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
