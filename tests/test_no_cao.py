#!/usr/bin/env python3
"""CAO is deprecated: no CAO path may ship, and neither may the old runner.

CAO grew stale beside the batch runner (operator 2026-09-29). AutoOS holds
no skill and no old runner any more (D-6366): both the CAO paths and the
old runner's core files must be absent from the tracked tree.
"""
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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

# The old runner (a skill's three core files), removed with the skill.
OLD_RUNNER = [
    '.agents/skills/unattended-orchestration/HandoffCore.psm1',
    '.agents/skills/unattended-orchestration/run_handoff_sessions.ps1',
    '.agents/skills/unattended-orchestration/l1_handoff.py',
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


class NoCaoTests(unittest.TestCase):
    def test_no_cao_path_is_tracked(self):
        tracked = tracked_paths()
        present = [p for p in CAO_PATHS if p in tracked]
        self.assertEqual(present, [],
                         'CAO paths still tracked: %s' % present)

    def test_the_old_runner_is_gone(self):
        tracked = tracked_paths()
        present = [p for p in OLD_RUNNER if p in tracked]
        self.assertEqual(present, [], 'old runner still tracked: %s' % present)


if __name__ == '__main__':
    unittest.main()
