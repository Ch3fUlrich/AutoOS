#!/usr/bin/env python3
"""CAO is deprecated: no CAO path, section, or config may ship.

Red-first for the CAO-removal lane (operator 2026-09-29): CAO grew stale
beside the batch runner and its 52 files plus skill/config touch points
must go. Fails on any tree that still carries CAO; passes once the
removal lane deletes the code, cuts the SKILL.md section, and drops the
example-config block. The legacy runner retires with its skill folder;
optional tracked blobs are checked without opening that folder's files.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SKILL = '.agents/skills/unattended-orchestration/SKILL.md'
EXAMPLE = '.agents/skills/unattended-orchestration/handoff.config.example.json'

# Every CAO path on origin/main fae36a3: 18 cao/ modules + 21 tests/cao/ +
# 12 infra/mcp-servers/cao-setup/ + the runbook. Kept as exact paths (not a
# segment match) so a future docs sentence mentioning CAO cannot fail this.
CAO_PATHS = [
    '.agents/skills/unattended-orchestration/%s' % p for p in (
        ['cao/%s' % f for f in (
            '__init__.py', '__main__.py', 'budget.py', 'caoapi.py',
            'cli.py', 'config.py', 'dispatch.py', 'launch.py', 'leases.py',
            'monitor.py', 'plan.py', 'probe.py', 'profiles.py', 'routing.py',
            'verify.py', 'watchdog.py', 'wire.py', 'worktree.py',
        )] + ['references/cao-runbook.md']
    )
] + [
    '.agents/skills/unattended-orchestration/tests/cao/%s' % f for f in (
        'conftest.py', 'test_budget.py', 'test_caoapi.py', 'test_cli.py',
        'test_config.py', 'test_defaults.py', 'test_dispatch.py',
        'test_launch.py', 'test_leases.py', 'test_live_providers.py',
        'test_monitor.py', 'test_plan.py', 'test_portability.py',
        'test_probe.py', 'test_profiles.py', 'test_routing.py',
        'test_setup.py', 'test_verify.py', 'test_watchdog.py',
        'test_wire.py', 'test_worktree.py',
    )
] + [
    'infra/mcp-servers/cao-setup/%s' % f for f in (
        'README.md', 'setup_cao.py', 'setup-cao.sh',
        'patches/antigravity_cli.py',
        'patches/apply_wsl_bridge_patch.sh',
        'profiles/agy_developer.md', 'profiles/agy_developer.yaml',
        'profiles/agy_reviewer.md', 'profiles/agy_reviewer.yaml',
        'profiles/agy_supervisor.md', 'profiles/agy_supervisor.yaml',
        'workflows/three_layer_orchestration.yaml',
    )
]

def tracked_paths():
    # Staged deletions are removals: `git ls-files` still lists a path until
    # it is committed, so subtract entries staged as deleted (changed 2026-09-29:
    # the red gate uses the index, the green gate the commit — same command).
    out = subprocess.run(['git', 'ls-files'],
                         capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, 'git ls-files failed: %s' % out.stderr
    staged = subprocess.run(['git', 'diff', '--cached', '--name-only',
                             '--diff-filter=D'],
                            capture_output=True, text=True, cwd=REPO)
    assert staged.returncode == 0, 'git diff failed: %s' % staged.stderr
    return set(out.stdout.split()) - set(staged.stdout.split())


def tracked_text(path):
    """Inspect what ships in the index, not files in an optional skill folder."""
    if path not in tracked_paths():
        return None
    out = subprocess.run(['git', 'show', ':' + path], cwd=REPO,
                         capture_output=True, text=True, encoding='utf-8')
    assert out.returncode == 0, 'git show failed: %s' % out.stderr
    return out.stdout


class NoCaoTests(unittest.TestCase):
    def test_no_cao_path_is_tracked(self):
        tracked = tracked_paths()
        present = [p for p in CAO_PATHS if p in tracked]
        self.assertEqual(present, [],
                         'CAO paths still tracked: %s' % present)

    def test_skill_has_no_cao_section_or_command(self):
        text = tracked_text(SKILL) or ''
        self.assertNotIn('## CAO quickstart', text)
        self.assertNotIn('python -m cao ', text)
        self.assertNotIn('references/cao-runbook.md', text)

    def test_example_config_has_no_cao_block(self):
        text = tracked_text(EXAMPLE)
        doc = json.loads('{}' if text is None else text)
        self.assertNotIn('cao', doc)

    def test_optional_blobs_refuse_cao_and_allow_staged_folder_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            with mock.patch(__name__ + '.REPO', repo):
                self.test_skill_has_no_cao_section_or_command()
                self.test_example_config_has_no_cao_block()
                skill, example = repo / SKILL, repo / EXAMPLE
                skill.parent.mkdir(parents=True)
                example.write_text('{"cao": {}}', encoding='utf-8')
                for needle in ('## CAO quickstart', 'python -m cao ',
                               'references/cao-runbook.md'):
                    skill.write_text(needle, encoding='utf-8')
                    subprocess.run(['git', 'add', SKILL, EXAMPLE], cwd=repo, check=True)
                    with self.assertRaises(AssertionError):
                        self.test_skill_has_no_cao_section_or_command()
                with self.assertRaises(AssertionError):
                    self.test_example_config_has_no_cao_block()
                skill.write_text('# Runner', encoding='utf-8')
                example.write_text('{}', encoding='utf-8')
                subprocess.run(['git', 'add', SKILL, EXAMPLE], cwd=repo, check=True)
                self.test_skill_has_no_cao_section_or_command()
                self.test_example_config_has_no_cao_block()
                skill.unlink()
                example.unlink()
                subprocess.run(['git', 'add', '-u'], cwd=repo, check=True)
                self.test_skill_has_no_cao_section_or_command()
                self.test_example_config_has_no_cao_block()


if __name__ == '__main__':
    unittest.main()
