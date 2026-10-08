#!/usr/bin/env python3
"""Tests for configuration/opencode/plugins/bash-guard/index.mjs.

Drives the OpenCode bash-guard plugin through a minimal fake ctx harness
under Node.js, verifying:
- Incident-shape command denial (unquoted heredoc with backticks)
- Claude background invocation denial (--bg with $() in double quotes)
- Safe command allow paths (quoted heredoc, plain commands, echo with <<)
- Forward-compat tool naming ('shell' and 'bash' intercepted; others bypassed)
- Resilient fail-open behavior (missing guard, non-zero guard exit, timeout)
- Orchestrator-role denials carry the canary's "bash-guard: DENIED" marker (D-665)
- Absence of embedded rule logic in the plugin source
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
# the marker and the incident shape are the canary's contract, not a copy
from oc_l1_canary import DENIED_MARKER, INCIDENT_COMMAND  # noqa: E402
PLUGIN_PATH = ROOT / "configuration" / "opencode" / "plugins" / "bash-guard" / "index.mjs"
NODE_BIN = shutil.which("node")

HARNESS_MJS = """\
import { pathToFileURL } from 'node:url';

let inputStr = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) {
  inputStr += chunk;
}

const req = JSON.parse(inputStr);
const pluginModule = await import(req.pluginUrl);
const plugin = pluginModule.default;

let handler = null;
const fakeCtx = {
  tool: {
    hook: async (hookName, fn) => {
      if (hookName === 'execute.before') {
        handler = fn;
      }
    }
  }
};

await plugin.setup(fakeCtx);

if (!handler) {
  process.stderr.write('bash-guard: no handler registered\\n');
  process.exit(10);
}

try {
  await handler(req.event);
  process.stdout.write(JSON.stringify({ allowed: true, error: null }));
} catch (err) {
  process.stdout.write(JSON.stringify({ allowed: false, error: err?.message || String(err) }));
}
"""


def run_plugin(event, env_overrides=None, plugin_path=None, timeout=20, keep_location=False,
               cwd=None):
    if not NODE_BIN:
        raise unittest.SkipTest("node is not installed")

    target_plugin = Path(plugin_path) if plugin_path else PLUGIN_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        # import a copy named .mjs: older node (18) refuses ES-module syntax in a .js file without a package.json type
        if keep_location:
            plugin_url = target_plugin.resolve().as_uri()
        else:
            plugin_copy = Path(tmpdir) / "bash-guard.mjs"
            plugin_copy.write_text(target_plugin.read_text(encoding="utf-8"), encoding="utf-8")
            plugin_url = plugin_copy.resolve().as_uri()
        harness_file = Path(tmpdir) / "harness.mjs"
        harness_file.write_text(HARNESS_MJS, encoding="utf-8")

        env = os.environ.copy()
        env["AUTOOS_REPO_ROOT"] = str(ROOT)  # the imported copy lives in a temp dir, so point at the real checkout
        if env_overrides:
            env.update(env_overrides)
            # a None value removes the key entirely (tests that must prove
            # behaviour with the variable unset, e.g. AUTOOS_GUARD_ROLE)
            for key in [k for k, v in env_overrides.items() if v is None]:
                env.pop(key, None)

        payload = json.dumps({
            "pluginUrl": plugin_url,
            "event": event,
        })

        t0 = time.time()
        proc = subprocess.run(
            [NODE_BIN, str(harness_file)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            cwd=cwd,
        )
        elapsed = time.time() - t0

        try:
            data = json.loads(proc.stdout)
        except Exception:
            raise AssertionError(
                f"Harness did not produce valid JSON. code={proc.returncode}, "
                f"stdout={proc.stdout!r}, stderr={proc.stderr!r}"
            )

        return {
            "allowed": data["allowed"],
            "error": data.get("error"),
            "stderr": proc.stderr,
            "stdout": proc.stdout,
            "returncode": proc.returncode,
            "elapsed": elapsed,
        }


class TestOpenCodeBashGuard(unittest.TestCase):

    def test_incident_shape_unquoted_heredoc_denied(self):
        cmd = "cat <<EOF\nIf this fails, run:\n`netplan apply`\nEOF\n"
        res = run_plugin({"tool": "shell", "input": {"command": cmd}})
        self.assertFalse(res["allowed"], "Incident shape must be denied")
        self.assertIsNotNone(res["error"])
        self.assertIn("heredoc", res["error"].lower())

    def test_quoted_heredoc_allowed(self):
        cmd = "cat <<'EOF'\nIf this fails, run:\n`netplan apply`\nEOF\n"
        res = run_plugin({"tool": "shell", "input": {"command": cmd}})
        self.assertTrue(res["allowed"], f"Quoted heredoc must be allowed: {res['error']}")
        self.assertIsNone(res["error"])

    def test_plain_command_allowed(self):
        cmd = "ls -la"
        res = run_plugin({"tool": "shell", "input": {"command": cmd}})
        self.assertTrue(res["allowed"], f"Plain command must be allowed: {res['error']}")
        self.assertIsNone(res["error"])

    def test_echo_with_redirection_symbols_allowed(self):
        cmd = 'echo "a << b"'
        res = run_plugin({"tool": "shell", "input": {"command": cmd}})
        self.assertTrue(res["allowed"], f"Echo with << must be allowed: {res['error']}")
        self.assertIsNone(res["error"])

    def test_claude_bg_command_substitution_denied(self):
        cmd = 'claude --bg "$(cat f)"'
        res = run_plugin({"tool": "shell", "input": {"command": cmd}})
        self.assertFalse(res["allowed"], "claude --bg with $() must be denied")
        self.assertIsNotNone(res["error"])
        self.assertIn("claude", res["error"].lower())

    def test_repo_root_derived_from_plugin_location(self):
        """Without AUTOOS_REPO_ROOT the plugin finds tools/hooks/bash_guard.py four levels above itself."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "tools" / "hooks").mkdir(parents=True)
            (tree / "configuration" / "opencode" / "plugins" / "bash-guard").mkdir(parents=True)
            (tree / "tools" / "hooks" / "bash_guard.py").write_text(
                (ROOT / "tools" / "hooks" / "bash_guard.py").read_text(encoding="utf-8"), encoding="utf-8")
            plugin = tree / "configuration" / "opencode" / "plugins" / "bash-guard" / "index.mjs"
            plugin.write_text(PLUGIN_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            cmd = "cat <<EOF\n`netplan apply`\nEOF"
            res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                             env_overrides={"AUTOOS_REPO_ROOT": None}, plugin_path=plugin, keep_location=True)
            self.assertFalse(res["allowed"], "the incident shape must be refused via the location-derived root")

    def test_guard_missing_fail_open_and_logged(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            res = run_plugin(
                {"tool": "shell", "input": {"command": "ls -la"}},
                env_overrides={"AUTOOS_REPO_ROOT": empty_dir},
            )
            self.assertTrue(res["allowed"], "Missing guard script must fail open")
            guard_lines = [l for l in res["stderr"].splitlines() if l.startswith("bash-guard:")]
            self.assertEqual(len(guard_lines), 1, f"Expected 1 log line, got: {res['stderr']}")
            self.assertIn("missing", guard_lines[0].lower())

    def test_guard_timeout_allowed_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            guard = Path(tmp_root) / "tools" / "hooks" / "bash_guard.py"
            guard.parent.mkdir(parents=True, exist_ok=True)
            guard.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

            res = run_plugin(
                {"tool": "shell", "input": {"command": "ls -la"}},
                env_overrides={"AUTOOS_REPO_ROOT": tmp_root},
                timeout=15,
            )
            self.assertTrue(res["allowed"], "Timeout must allow")
            self.assertLess(res["elapsed"], 8.0, f"Expected under 8s elapsed, got {res['elapsed']:.2f}s")
            self.assertGreaterEqual(res["elapsed"], 4.5, f"Expected at least 4.5s elapsed, got {res['elapsed']:.2f}s")
            guard_lines = [l for l in res["stderr"].splitlines() if l.startswith("bash-guard:")]
            self.assertEqual(len(guard_lines), 1, f"Expected 1 log line, got: {res['stderr']}")
            self.assertIn("timeout", guard_lines[0].lower())

    def test_guard_exit_3_allowed_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            guard = Path(tmp_root) / "tools" / "hooks" / "bash_guard.py"
            guard.parent.mkdir(parents=True, exist_ok=True)
            guard.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")

            res = run_plugin(
                {"tool": "shell", "input": {"command": "ls -la"}},
                env_overrides={"AUTOOS_REPO_ROOT": tmp_root},
            )
            self.assertTrue(res["allowed"], "Non-zero (3) guard exit must fail open")
            guard_lines = [l for l in res["stderr"].splitlines() if l.startswith("bash-guard:")]
            self.assertEqual(len(guard_lines), 1, f"Expected 1 log line, got: {res['stderr']}")
            self.assertIn("3", guard_lines[0])

    def test_tool_names_shell_and_bash_checked_others_ignored(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            guard = Path(tmp_root) / "tools" / "hooks" / "bash_guard.py"
            guard.parent.mkdir(parents=True, exist_ok=True)
            marker = guard.parent / "marker.txt"
            guard.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[0]).parent.joinpath('marker.txt').write_text('ran', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            # 1. Unrelated tool name -> ignored without spawning
            res_other = run_plugin(
                {"tool": "python", "input": {"command": "print('hello')"}},
                env_overrides={"AUTOOS_REPO_ROOT": tmp_root},
            )
            self.assertTrue(res_other["allowed"])
            self.assertFalse(marker.exists(), "Unrelated tool must not spawn guard")

            # 2. 'shell' tool -> spawned
            res_shell = run_plugin(
                {"tool": "shell", "input": {"command": "ls"}},
                env_overrides={"AUTOOS_REPO_ROOT": tmp_root},
            )
            self.assertTrue(res_shell["allowed"])
            self.assertTrue(marker.exists(), "'shell' tool must spawn guard")

            # 3. 'bash' tool -> spawned
            marker.unlink()
            res_bash = run_plugin(
                {"tool": "bash", "input": {"command": "ls"}},
                env_overrides={"AUTOOS_REPO_ROOT": tmp_root},
            )
            self.assertTrue(res_bash["allowed"])
            self.assertTrue(marker.exists(), "'bash' tool must spawn guard")

    def test_plugin_source_contains_no_rule_logic(self):
        self.assertTrue(PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}")
        content = PLUGIN_PATH.read_text(encoding="utf-8")
        self.assertNotIn("<<", content, "Plugin must not contain heredoc operator")
        self.assertNotIn("heredoc", content.lower(), "Plugin must not contain heredoc keyword")
        self.assertNotIn("netplan", content.lower(), "Plugin must not contain netplan rule")
        self.assertNotIn("--bg", content, "Plugin must not contain --bg rule")
        self.assertIn("bash_guard.py", content, "Plugin must reference bash_guard.py")

    # ------------------------------------------------------------------
    # Orchestrator role (AUTOOS_GUARD_ROLE=orchestrator): the guard denies
    # shell writes outside .oc-pilot/ while leaving the old behaviour
    # untouched when the role is unset.
    # ------------------------------------------------------------------

    ORCH_ENV = {"AUTOOS_GUARD_ROLE": "orchestrator"}

    def _orch_denied(self, cmd, *needles):
        res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                         env_overrides=dict(self.ORCH_ENV))
        self.assertFalse(res["allowed"], f"Orchestrator must deny: {cmd!r} (error={res['error']})")
        self.assertIsNotNone(res["error"])
        for needle in needles:
            self.assertIn(needle, res["error"].lower())

    def _orch_allowed(self, cmd):
        res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                         env_overrides=dict(self.ORCH_ENV))
        self.assertTrue(res["allowed"], f"Orchestrator must allow: {cmd!r} (error={res['error']})")
        self.assertIsNone(res["error"])

    def test_orch_redirect_single_outside_denied(self):
        self._orch_denied("echo hi > outside.txt", "outside.txt")

    def test_orch_redirect_double_outside_denied(self):
        self._orch_denied("echo hi >> /tmp/out.log", "/tmp/out.log")

    def test_orch_redirect_fd2_outside_denied(self):
        self._orch_denied("false 2> /tmp/err.log", "/tmp/err.log")

    def test_orch_redirect_noclobber_outside_denied(self):
        self._orch_denied("echo hi >| /tmp/f.txt", "/tmp/f.txt")

    def test_orch_redirect_ampsingle_outside_denied(self):
        self._orch_denied("cmd &> /tmp/all.log", "/tmp/all.log")

    def test_orch_redirect_quoted_double_outside_denied(self):
        self._orch_denied('echo hi > "outside.txt"', "outside.txt")

    def test_orch_redirect_quoted_single_outside_denied(self):
        self._orch_denied("echo hi > 'outside.txt'", "outside.txt")

    def test_orch_redirect_inside_pilot_allowed(self):
        self._orch_allowed("echo hi > .oc-pilot/notes.txt")
        self._orch_allowed("echo hi >> .oc-pilot/log.txt")
        self._orch_allowed("echo hi > /dev/null")

    def test_orch_tee_outside_denied(self):
        self._orch_denied("echo hi | tee /tmp/out.txt", "/tmp/out.txt")

    def test_orch_tee_inside_pilot_allowed(self):
        self._orch_allowed("echo hi | tee .oc-pilot/out.txt")

    def test_orch_sed_i_outside_denied(self):
        self._orch_denied("sed -i 's/a/b/' /etc/hosts", "/etc/hosts")
        self._orch_denied("sed -i 's/a/b/' outside.txt", "outside.txt")

    def test_orch_sed_n_read_only_allowed(self):
        self._orch_allowed("sed -n '1,5p' /etc/hosts")

    def test_orch_python_c_write_outside_denied(self):
        self._orch_denied(
            'python3 -c "open(\'/tmp/x.txt\', \'w\').write(\'hi\')"', "/tmp/x.txt")
        self._orch_denied(
            'python -c "open(\'outside.txt\', \'a\')"', "outside.txt")

    def test_orch_python_c_write_inside_pilot_allowed(self):
        self._orch_allowed('python3 -c "open(\'.oc-pilot/x.txt\', \'w\').write(\'hi\')"')
        self._orch_allowed('python3 -c "print(open(\'.oc-pilot/x.txt\').read())"')

    def test_orch_cp_mv_outside_denied(self):
        self._orch_denied("cp a.txt /tmp/b.txt", "/tmp/b.txt")
        self._orch_denied("mv a.txt /tmp/b.txt", "/tmp/b.txt")

    def test_orch_git_mutation_denied(self):
        self._orch_denied("git apply patch.diff")
        self._orch_denied("git am patch.diff")
        self._orch_denied("git commit -m x")
        self._orch_denied("git checkout -- file.txt")
        self._orch_denied("git checkout file.txt")
        self._orch_denied("git reset HEAD~1")
        self._orch_denied("git rebase main")

    def test_orch_git_read_only_and_orchestrator_allowed(self):
        self._orch_allowed("git fetch origin")
        self._orch_allowed("git log --oneline -5")
        self._orch_allowed("git show HEAD")
        self._orch_allowed("git diff")
        self._orch_allowed("git status")
        self._orch_allowed("git branch -a")
        self._orch_allowed("git cherry-pick abc123")
        self._orch_allowed("git merge --ff-only origin/main")
        self._orch_allowed("git push")

    def test_orch_read_only_commands_allowed(self):
        self._orch_allowed("cat file.txt")
        self._orch_allowed("grep -rn pattern .")
        self._orch_allowed("ls -la")

    def test_orch_tool_commands_allowed(self):
        self._orch_allowed("python3 tools/autoos-agent.py list")
        self._orch_allowed("python3 tools/autoos_gateway_key.py status")
        self._orch_allowed("python3 tools/review-call.py")

    def test_orch_role_unset_keeps_old_behaviour(self):
        # With AUTOOS_GUARD_ROLE explicitly unset, the old flow applies:
        # a plain redirect outside .oc-pilot/ is not a legacy incident shape.
        res = run_plugin({"tool": "shell", "input": {"command": "echo test > outside.txt"}},
                         env_overrides={"AUTOOS_GUARD_ROLE": None})
        self.assertTrue(res["allowed"], f"Role unset must keep old behaviour: {res['error']}")
        self.assertIsNone(res["error"])

    # ------------------------------------------------------------------
    # D-665: the c0 orchestrator-role throw runs BEFORE the python guard, so its
    # message is the only thing the L1 canary ever sees on a role denial. The
    # canary reads a denial by DENIED_MARKER alone (tools/oc_l1_canary.py), so a
    # role denial without the marker reads as "shell call inconclusive" and the
    # lane refuses forever.
    # ------------------------------------------------------------------

    def test_orchestrator_role_throw_carries_denied_marker(self):
        res = run_plugin({"tool": "shell", "input": {"command": "echo hi > outside.txt"}},
                         env_overrides=dict(self.ORCH_ENV))
        self.assertFalse(res["allowed"], f"role must deny: {res['error']}")
        self.assertIn(DENIED_MARKER, res["error"],
                      "the canary matches DENIED_MARKER only")
        self.assertIn("outside.txt", res["error"],
                      "prefixing must keep the reason")

    def test_orchestrator_role_canary_incident_carries_denied_marker(self):
        for cmd in (INCIDENT_COMMAND,
                    INCIDENT_COMMAND + "\n",
                    "echo hi > outside.txt\n" + INCIDENT_COMMAND):
            res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                             env_overrides=dict(self.ORCH_ENV))
            self.assertFalse(res["allowed"], f"role must deny the incident shape: {cmd!r}")
            self.assertIn(DENIED_MARKER, res["error"], f"marker missing for {cmd!r}")


# ---------------------------------------------------------------------------
# AO-L2-LAUNCH merge criterion 1: the L2 read-only role (AUTOOS_GUARD_ROLE=l2).
#
# An orchestrator-L2 may inspect a tree and run read-only checks and NOTHING
# else: the shell is a closed allow list of command heads, not a write-scoped
# one. `orchestrator` scopes writes to .oc-pilot/; an L2 has no writable scope
# at all, because every change it makes is supposed to be a tier-3 spawn. So a
# redirect to .oc-pilot/ is refused here, a mutating `git` verb is refused, and
# any head that is not on the list is refused - fail closed, as the L2's whole
# contract depends on it.
# ---------------------------------------------------------------------------

class TestL2ReadOnlyRole(unittest.TestCase):

    L2_ENV = {"AUTOOS_GUARD_ROLE": "l2"}

    def _l2_denied(self, cmd, *needles):
        res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                         env_overrides=dict(self.L2_ENV))
        self.assertFalse(res["allowed"], f"l2 role must deny: {cmd!r} (error={res['error']})")
        self.assertIsNotNone(res["error"])
        self.assertTrue(res["error"].strip().startswith(DENIED_MARKER),
                        "the canary matches the marker at the start only: %r" % res["error"])
        self.assertIn("l2 read-only", res["error"], cmd)
        for needle in needles:
            self.assertIn(needle, res["error"].lower(), cmd)

    def _l2_allowed(self, cmd):
        res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                         env_overrides=dict(self.L2_ENV))
        self.assertTrue(res["allowed"], f"l2 role must allow: {cmd!r} (error={res['error']})")
        self.assertIsNone(res["error"])

    # (1) the allow list, verbatim
    def test_allow_list_git_heads(self):
        for cmd in ("git status", "git status --short", "git log --oneline -5",
                    "git diff", "git diff HEAD~1 -- tools", "git show HEAD"):
            self._l2_allowed(cmd)

    def test_allow_list_plain_heads(self):
        for cmd in ("ls -la", "cat file.txt", "rg pattern .", "head -5 f.txt",
                    "tail -n 20 f.log", "wc -l f.txt", "pwd"):
            self._l2_allowed(cmd)

    def test_allow_list_pipes_between_read_only_heads(self):
        self._l2_allowed("git log --oneline | head -20")
        self._l2_allowed("ls -la | wc -l")

    def test_wrapper_head_denied_even_around_a_read_only_head(self):
        # Sonnet round-4 finding 7: `timeout 5 ls` is still `ls` to the
        # orchestrator role, whose rule is a write TARGET. The L2 rule is a
        # closed list of HEADS, and a wrapper is a different process around the
        # read - `sudo cat` is root's `cat`. An L2 has no use for one.
        self._l2_denied("timeout 5 ls", "wrapper")
        self._l2_denied("nice ls", "wrapper")

    # (2) everything else is refused
    def test_redirect_denied_even_into_the_pilot_dir(self):
        self._l2_denied("echo x > f", "f")
        self._l2_denied("echo x >> f.log", "f.log")
        self._l2_denied("echo x > .oc-pilot/notes.txt", ".oc-pilot")
        self._l2_denied("false 2> /tmp/err.log", "/tmp/err.log")
        self._l2_denied("echo x >| /tmp/f.txt", "/tmp/f.txt")

    def test_git_mutating_verbs_denied(self):
        for cmd in ("git commit -m x", "git add -A", "git push", "git checkout -- file",
                    "git reset HEAD~1", "git rebase main", "git apply p.diff",
                    "git merge origin/main", "git branch -f x HEAD"):
            self._l2_denied(cmd, "git")

    def test_writing_and_spawning_heads_denied(self):
        for cmd in ("rm -rf /tmp/x", "python3 -c 'print(1)'", "curl https://example.com",
                    "mkdir d", "touch f", "tee /tmp/x", "cp a b", "mv a b", "sed -i s/a/b/ f",
                    "bash -c 'ls'", "npm install", "git", "echo x"):
            self._l2_denied(cmd)

    def test_pipe_into_a_writer_denied(self):
        self._l2_denied("ls | tee out.txt", "tee")

    def test_wrapper_head_still_audited(self):
        self._l2_denied("sudo rm -f /tmp/x", "rm")
        self._l2_denied("timeout 5 python3 -c 'x'", "python3")

    def test_command_substitution_denied(self):
        # `cat $(ls)` reads as `cat` on the list plus an unlisted command that
        # ran first: a substitution is not read-only, whatever its parts are.
        self._l2_denied("cat $(ls)", "substitution")
        self._l2_denied("ls `pwd`", "substitution")

    def test_stdin_redirection_denied(self):
        self._l2_denied("cat < f.txt", "redirection")

    # (3) the canary must still be denied under the new role, with the marker
    def test_canary_incident_denied_with_marker(self):
        for cmd in (INCIDENT_COMMAND, INCIDENT_COMMAND + "\n"):
            res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                             env_overrides=dict(self.L2_ENV))
            self.assertFalse(res["allowed"], f"the probe must be denied: {cmd!r}")
            self.assertIn(DENIED_MARKER, res["error"])
            self.assertIn("l2 read-only", res["error"])

    def test_role_denies_without_the_python_guard_present(self):
        # Defense in depth: the role gate is the lane's own, so a missing
        # legacy guard must not turn the read-only shell back into a writable
        # one. The orchestrator role has the same shape of hole; this one is
        # pinned because an L2 has NO writable scope to fall back on.
        with tempfile.TemporaryDirectory() as empty_dir:
            env = dict(self.L2_ENV)
            env["AUTOOS_REPO_ROOT"] = empty_dir
            res = run_plugin({"tool": "shell", "input": {"command": "git commit -m x"}},
                             env_overrides=env)
        self.assertFalse(res["allowed"], "the role alone must deny")
        self.assertIn(DENIED_MARKER, res["error"] or "")

    # (4) the other roles are untouched by this gate
    def test_l2_rules_do_not_leak_into_the_orchestrator_role(self):
        res = run_plugin({"tool": "shell", "input": {"command": "git commit -m x"}},
                         env_overrides={"AUTOOS_GUARD_ROLE": "orchestrator"})
        self.assertFalse(res["allowed"])
        self.assertIn("orchestrator role", res["error"])
        # `cat` is read-only for both, but the orchestrator may write inside
        # .oc-pilot/ while an L2 may not write anywhere.
        self.assertTrue(run_plugin(
            {"tool": "shell", "input": {"command": "echo hi > .oc-pilot/notes.txt"}},
            env_overrides={"AUTOOS_GUARD_ROLE": "orchestrator"})["allowed"])

    def test_role_unset_keeps_old_behaviour(self):
        res = run_plugin({"tool": "shell", "input": {"command": "curl https://example.com"}},
                         env_overrides={"AUTOOS_GUARD_ROLE": None})
        self.assertTrue(res["allowed"], f"role unset must keep old behaviour: {res['error']}")
        res = run_plugin({"tool": "shell", "input": {"command": "echo x > f"}},
                         env_overrides={"AUTOOS_GUARD_ROLE": None})
        self.assertTrue(res["allowed"], f"role unset must keep old behaviour: {res['error']}")

    def test_l2_role_still_runs_the_legacy_guard(self):
        # An unquoted here-document whose body expands is denied by BOTH gates;
        # the legacy python guard runs after the role check, so the incident
        # rules cannot be dropped by a role that allows the head.
        cmd = 'ls\ncat <<EOF\n`netplan apply`\nEOF'
        res = run_plugin({"tool": "shell", "input": {"command": cmd}},
                         env_overrides={"AUTOOS_GUARD_ROLE": None})
        self.assertFalse(res["allowed"], "the legacy incident rule still applies")
        self.assertIn("heredoc", (res["error"] or "").lower())

    # ------------------------------------------------------------------
    # Sonnet final REJECT (criterion b): a read-only HEAD is not a read-only
    # command. `git status` is on the list, but `git -c core.fsmonitor='touch
    # /tmp/p' status` runs that fsmonitor as a shell; `rg` is on the list, but
    # `rg --pre 'sh -c id'` pipes every file through that program; `cat` is on
    # the list, but `/proc/self/environ` holds the lane's server password. The
    # flags that turn an inspection command into a program runner are denied
    # whatever head they hang off, and so is an environment-assignment prefix,
    # which reaches the same config knobs (`GIT_PAGER=x git log`) without any
    # flag at all.
    # ------------------------------------------------------------------

    def test_git_config_injecting_global_options_denied(self):
        for cmd in ("git -c core.fsmonitor='touch /tmp/p' status",
                    "git -c core.pager=x log",
                    "git -c gpg.format=x show HEAD",
                    "git --config-env=credential.helper=ENV:CRED_HELPER status",
                    "git -C /tmp/other-repo status",
                    "git --git-dir=/tmp/evil.git log",
                    "git --work-tree=/etc status",
                    "git --exec-path=/tmp/bins log",
                    "git -p log", "git --paginate log"):
            self._l2_denied(cmd, "git")

    def test_git_output_driving_subcommand_flags_denied(self):
        for cmd in ("git diff --ext-diff", "git show --ext-diff HEAD",
                    "git show --textconv", "git diff --textconv HEAD~1",
                    "git diff --no-index a b", "git log --output=/tmp/f",
                    "git diff --output=/tmp/patch.diff"):
            self._l2_denied(cmd, "git")

    def test_git_read_only_flags_kept(self):
        for cmd in ("git log", "git log --oneline -5", "git status --short",
                    "git diff --no-ext-diff HEAD", "git show --no-textconv HEAD",
                    "git --no-pager log", "git --no-pager diff"):
            self._l2_allowed(cmd)

    def test_rg_program_running_flags_denied(self):
        for cmd in ("rg --pre 'sh -c id' x .", "rg --pre /bin/sh x .",
                    "rg --pre=unzip x .", "rg --pre-glob '*.gz' x .",
                    "rg --hostname-bin /bin/sh .", "rg --search-zip x .",
                    "rg -z x ."):
            self._l2_denied(cmd, "rg")

    def test_rg_plain_search_kept(self):
        # `--hidden` moved to the deny list (round-5 finding 1): it makes rg read
        # the gitignored secrets in the repo. See
        # test_r5_rg_flags_that_defeat_the_ignore_rules_denied.
        for cmd in ("rg foo", "rg -i TODO .", "rg --ignore-case TODO ."):
            self._l2_allowed(cmd)

    def test_proc_per_process_reads_denied(self):
        for cmd in ("cat /proc/self/environ", "cat /proc/123/environ",
                    "cat /proc/*/environ", "head -1 /proc/self/cmdline",
                    "tail -f /proc/thread-self/status", "cat /proc/self/maps",
                    "cat proc/self/environ"):
            self._l2_denied(cmd, "proc")

    def test_absolute_paths_denied_even_when_harmless(self):
        # The rule is an ALLOW rule, not a spelling list: a path operand must be
        # repo-relative, so an absolute path is denied whatever it points at.
        # /proc/./self, //proc//self, /proc/*/environ and /proc/$$/environ each
        # walked past a deny list that named one spelling at a time.
        for cmd in ("cat /proc/cpuinfo", "cat /proc/meminfo",
                    "head -3 /proc/version", "ls /etc", "cat /etc/hosts"):
            self._l2_denied(cmd, "path outside repo")

    def test_env_assignment_prefix_denied(self):
        # `FOO=x <head>` sets the environment of a command the list approved,
        # which is how GIT_PAGER / GIT_DIR / GIT_CONFIG_ENV reach git without a
        # single git flag. The prefix is the payload; the head is a disguise.
        for cmd in ("GIT_PAGER=x git log", "FOO=1 git status",
                    "env GIT_DIR=/tmp/evil git log", "LINES=1 rg foo",
                    "git log; FOO=1 ls", "export FOO=1"):
            self._l2_denied(cmd)

    def test_wrapper_of_read_only_head_denied_whatever_it_wraps(self):
        # Sonnet round-4 finding 7: the wrapper skip that the orchestrator role
        # needs (so `sudo rm /` is audited as `rm`) is what let `sudo cat` read
        # as `cat` here. The L2 list is of bare heads.
        self._l2_denied("timeout 5 ls", "wrapper")
        self._l2_denied("nice ls", "wrapper")

    # ------------------------------------------------------------------
    # Sonnet round-3 REJECT (criterion b, again): three holes left.
    #
    # (1) `skipLeading` walks past the `env` wrapper, so a bare `env` — whose
    # whole job is printing the lane's environment, server password and gateway
    # keys included — had no head at all and passed. `printenv`/`set`/`declare`/
    # `export` are the same read with different names.
    # (2) the /proc rule named the SHAPES it had seen. Every other absolute
    # path — /proc/./self/environ, //proc//self//environ, /proc/$$/environ, a
    # globbed /proc, ~/.claude.json, ~/.config/opencode/auth.json — was simply
    # not on the list. A read head may only name a path that is inside the lane
    # checkout and cannot move after the shell expands it.
    # (3) a short-flag bundle (`rg -uz`) hid the denied `-z` inside a token the
    # exact-match flag list never saw.
    # ------------------------------------------------------------------

    def test_env_dumping_heads_denied(self):
        for cmd in ("env", "printenv", "set", "declare", "export", "declare -p",
                    "env | head -1", "env cat README.md", "env -i ls",
                    "env FOO=1 cat README.md", "sudo env printenv"):
            self._l2_denied(cmd, "environment")

    def test_paths_outside_the_lane_repo_denied(self):
        for cmd in ("cat /proc/./self/environ", "cat //proc//self//environ",
                    "cat /proc/se*", "cat /proc/*",
                    "rg foo /proc/self", "cat ~/.claude.json",
                    "cat ~/.config/opencode/auth.json", "cat ../sibling/file",
                    "cat docs/../../etc/passwd", "cat .config/../auth.json",
                    "rg foo ~/", "git diff HEAD -- /etc/passwd"):
            self._l2_denied(cmd, "path outside repo")
        # The `$$` and `$HOME` spellings of these reads are denied one rule
        # earlier now: no `$` reaches an L2 command at all, so the path rule never
        # sees an expansion to argue about (L2SECRETS fix 10).
        self._l2_denied("cat /proc/$$/environ", "expansion")
        self._l2_denied("ls $HOME", "expansion")

    def test_secret_files_inside_the_repo_still_denied(self):
        for cmd in ("cat .env", "cat config/.env.local", "cat keys/lane.key",
                    "cat srv.pem", "cat api-keys.yml", "cat credentials.json",
                    "cat db-credentials.prod.json", "rg -f .env foo"):
            self._l2_denied(cmd, "secret")

    def test_short_flag_bundles_hide_a_denied_flag(self):
        for cmd in ("rg -uz x .", "rg -zi x", "rg -iz x .", "rg -iuz foo tools/",
                    "rg -C2 -z foo"):
            self._l2_denied(cmd, "rg")

    def test_repo_relative_reads_kept(self):
        for cmd in ("cat README.md", "rg foo tools/", "git log -5",
                    "git diff HEAD~1 -- tools/x.py", "ls docs",
                    "head -3 AGENTS.md", "wc -l CHANGELOG.md", "tail -n 5 README.md",
                    "cat .gitignore", "ls -la ./docs"):
            self._l2_allowed(cmd)

    # ------------------------------------------------------------------
    # Sonnet round-4 REJECT (2026-10-08), findings 1-7. Every one of them is
    # the same shape: a token the closed list read as a name on the list, while
    # the bytes the shell actually acted on were something else.
    #
    # (1) BLOCKER  `cat \<newline>/etc/passwd` — a line continuation to bash.
    #     collectSegments flattens the newline to a space (index 268) and leaves
    #     the backslash in place, so tokenizeSegment (index 408) reads `\ ` as an
    #     ESCAPED SPACE and glues it to the next word: the guard audited the word
    #     " /etc/passwd", whose leading space walked past the leading-"/" test at
    #     index 1047. The lane's `.env` is the same trick.
    # (2) HIGH     head was baseName(words[idx]) (index 1147), so ANY binary
    #     wearing an allow-listed name was that name: /tmp/evil/cat, ./cat.
    # (3) MED      l2OperandReason skipped every token starting with "-" (index
    #     1062), so the path rode in as an option's VALUE: rg --file=/etc/passwd,
    #     rg --ignore-file=/etc/shadow.
    # (4) MED      the secret rule matched the WHOLE token (index 1031), so git's
    #     rev:path spelling hid it: git show HEAD:.env, git show :.env.
    # (5) MED      the secret list was five patterns long and case-sensitive.
    # (6) LOW      a repo-relative NAME is not a repo-relative FILE: a symlink
    #     inside the checkout reads outside it, and rg -L resolves the link
    #     itself.
    # (7) LOW      a wrapper was skipped, not audited: `sudo cat` is a second
    #     user's `cat`, and an L2 needs no wrapper at all.
    # ------------------------------------------------------------------

    def test_f1_backslash_denied_in_any_form(self):
        for cmd in ("cat \\\n/etc/passwd", "cat \\\n.env", "ls \\\nconfig/.env",
                    "rg foo \\\n/etc/shadow", "git show \\\nHEAD:.env",
                    'cat "a\\\\b"', "rg foo\\\\bar .", "cat doc\\\\x"):
            self._l2_denied(cmd, "backslash")

    def test_f1_leading_whitespace_in_a_path_operand_denied(self):
        # The other half of finding 1: even with the flatten fixed, an operand is
        # checked stripped, so a quoted space is not a second escape hatch.
        self._l2_denied('cat " /etc/passwd"', "path outside repo")
        self._l2_denied('rg foo " /etc/shadow"', "path outside repo")
        self._l2_denied('cat " .env"', "secret")
        self._l2_denied('cat "/etc/passwd "', "path outside repo")

    def test_f2_head_must_be_a_bare_allow_listed_word(self):
        for cmd in ("/tmp/evil/cat x", "./cat x", "/usr/bin/ls", "./git status",
                    "../tools/cat README.md", "bin/cat README.md"):
            self._l2_denied(cmd, "bare")

    def test_f3_option_values_are_path_checked(self):
        for cmd in ("rg --ignore-file=/etc/shadow x", "rg --file=/etc/passwd foo",
                    "rg -f=/etc/passwd foo", "rg -f/etc/passwd foo",
                    "ls --block-size=/etc/passwd", "rg --ignore-file=.env x"):
            self._l2_denied(cmd, "path outside repo" if "/etc/" in cmd else "secret")

    def test_f3_path_naming_rg_options_denied_outright(self):
        for cmd in ("rg --ignore-file .gitignore x .", "rg --file patterns.txt x .",
                    "rg -f patterns.txt x .", "rg --path-separator=:: x ."):
            self._l2_denied(cmd, "reads a path")

    def test_f3_pattern_file_operand_still_reads_as_a_secret(self):
        # The value of a denied option is checked BEFORE the option is refused, so
        # the reason an L2 sees names the file, not the flag.
        self._l2_denied("rg -f .env foo", "secret")

    def test_f4_git_rev_path_specifiers_are_split_and_checked(self):
        for cmd in ("git show HEAD:.env", "git show :.env",
                    "git show HEAD:config/.env.local", "git show HEAD~1:keys/lane.key",
                    "git show refs/heads/main:.git-credentials", "git diff HEAD -- :.env"):
            self._l2_denied(cmd, "secret")
        self._l2_denied("git show HEAD:/etc/passwd", "path outside repo")

    def test_f5_secret_list_widened_and_case_insensitive(self):
        for cmd in ("cat .netrc", "cat .NETRC", "cat config/.ssh/config",
                    "cat id_ed25519", "cat id_ecdsa_sk", "cat .aws/credentials",
                    "cat credentials", "cat secrets.yml", "cat secrets.yaml",
                    "cat TLS/Server.Key", "cat srv.PEM", "cat vault.p12", "cat x.pfx",
                    "cat db_credentials.json", "cat auth.json", "cat .git-credentials",
                    "cat .npmrc", "cat .pypirc", "rg foo .ssh/"):
            self._l2_denied(cmd, "secret")

    def test_f5_secret_lookalikes_kept(self):
        for cmd in ("cat .gitignore", "cat .npmrc.example", "cat inventory.yml.example",
                    "cat .git/config", "cat README.md", "ls docs"):
            self._l2_allowed(cmd)

    def test_f6_rg_follow_flags_denied(self):
        for cmd in ("rg -L foo .", "rg --follow foo .", "rg -iL foo ."):
            self._l2_denied(cmd, "rg")

    def test_f6_symlink_operand_may_not_escape_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "inside.txt").write_text("ok", encoding="utf-8")
            (tree / "link").symlink_to("inside.txt")
            (tree / "escape").symlink_to("/etc/shadow")
            (tree / "loop_a").symlink_to("loop_b")
            (tree / "loop_b").symlink_to("loop_a")
            env = dict(self.L2_ENV)
            env["AUTOOS_REPO_ROOT"] = tmp

            def run(cmd):
                return run_plugin({"tool": "shell", "input": {"command": cmd}},
                                  env_overrides=env, cwd=tmp)

            for cmd in ("cat escape", "rg foo escape", "ls escape", "git show escape"):
                res = run(cmd)
                self.assertFalse(res["allowed"], f"an escaping symlink must be denied: {cmd!r}")
                self.assertTrue((res["error"] or "").strip().startswith(DENIED_MARKER), cmd)
                self.assertIn("outside the lane checkout", res["error"], cmd)
            # unresolvable is not "allowed": ELOOP fails closed.
            res = run("cat loop_a")
            self.assertFalse(res["allowed"], "an unresolvable operand must be denied")
            self.assertIn("cannot resolve", res["error"])
            for cmd in ("cat inside.txt", "cat link", "cat nope.txt", "ls"):
                res = run(cmd)
                self.assertTrue(res["allowed"], f"an in-checkout read must be kept: {cmd!r}")
                self.assertIsNone(res["error"])

    def test_f7_wrapper_heads_denied(self):
        for cmd in ("sudo ls", "sudo -u root cat f", "doas ls", "su - root",
                    "exec ls", "command cat f", "nice ls", "timeout 5 ls", "time ls",
                    "nohup ls", "stdbuf -o0 cat f", "sudo", "timeout"):
            self._l2_denied(cmd, "wrapper")

    # ------------------------------------------------------------------
    # Sonnet round-5 REJECT (L2GATES-r4): two holes left in the L2 role.
    #
    # (1) MED-HIGH rg DISCOVERS the files it reads, so the operand rules never
    #     see them, and the ignore rules are the only thing that kept a recursive
    #     search off the gitignored secrets sitting in the checkout. Every flag
    #     that defeats them printed ./.env: `rg --hidden KEY`, `rg -uu KEY`,
    #     `rg -. KEY`, `rg --no-ignore --hidden KEY`. A glob picks the secret in
    #     by hand, so `-g/--glob/--iglob` go too.
    # (2) LOW      L2_GIT_REST_DENY was an exact-match table and git accepts an
    #     unambiguous ABBREVIATION of a long option: `--outpu`, `--ext-d`,
    #     `--textc` each walked past it into the very behaviour the table named.
    # ------------------------------------------------------------------

    def test_r5_rg_flags_that_defeat_the_ignore_rules_denied(self):
        for cmd in ("rg --hidden KEY", "rg --hidden=true KEY", "rg -. KEY",
                    "rg -u KEY", "rg -uu KEY", "rg -uuu KEY",
                    "rg --unrestricted KEY", "rg --no-ignore KEY",
                    "rg --no-ignore --hidden KEY", "rg --no-ignore-vcs KEY",
                    "rg --no-ignore-dot KEY", "rg --no-ignore-parent --no-ignore KEY",
                    "rg -iu KEY", "rg -i. KEY", "rg --hidden KEY .env",
                    "rg -zu KEY", "rg --no-ignore-files KEY"):
            self._l2_denied(cmd, "rg")

    def test_r5_rg_glob_flags_denied(self):
        for cmd in ("rg -g .env KEY", "rg --glob .env KEY", "rg --glob=!.env KEY",
                    "rg -g=*.env KEY", "rg --iglob .env KEY", "rg --iglob=*.env KEY",
                    "rg -iAg KEY", "rg foo -g '*.env'"):
            self._l2_denied(cmd, "rg")

    def test_r5_plain_rg_searches_kept(self):
        for cmd in ("rg foo", "rg -n foo src", "rg --line-number foo",
                    "rg --context 3 foo", "rg --count-matches foo",
                    "rg --sort path foo", "rg -i foo ."):
            self._l2_allowed(cmd)

    def test_r5_git_abbreviated_denied_long_options_denied(self):
        for cmd in ("git log --outp=/tmp/f", "git log --outp /tmp/f",
                    "git log --outpu=/tmp/f", "git diff --ext-d",
                    "git diff --ext-di", "git show --textc", "git show --textco",
                    "git diff --no-i a b", "git diff --no-ind a b",
                    "git --exec-p=/tmp/bins log", "git --git-di=/tmp/evil.git log",
                    "git --work-tre=/etc status", "git --pag log",
                    "git --confi=x status", "git --names status"):
            self._l2_denied(cmd, "git")

    def test_r5_git_read_only_long_options_kept(self):
        for cmd in ("git diff --no-ext-diff HEAD", "git show --no-textconv HEAD",
                    "git --no-pager log", "git log --oneline -5 --stat",
                    "git diff --name-only", "git status --short --branch",
                    "git log --no-color", "git show --format=%h"):
            self._l2_allowed(cmd)

    # ------------------------------------------------------------------
    # AO-L2-LAUNCH merge criterion e (L2-SECRETS, 2026-10-08): the L2 lane's
    # environment CARRIES `AUTOOS_OMNIROUTE_KEY`, because opencode expands the
    # rendered config's `{env:AUTOOS_OMNIROUTE_KEY}` inside the child and the lane
    # has no other way to its model. Carrying it is only safe while the lane's own
    # shell cannot READ it back: the launcher's allowlist picks which NAMES travel,
    # this gate picks which READS the session may perform, and the key sits in the
    # child's own environment either way — in `/proc/self/environ`, in `printenv`'s
    # output, and in every expansion. An allowlist of names with a hole in the read
    # list is a naming exercise, so each spelling that reaches the value is denied
    # here and the reason names the rule, not just the rejection.
    # ------------------------------------------------------------------

    def test_l2_shell_cannot_read_the_gateway_key(self):
        for cmd, why in (
                ("printenv", "environment"),
                ("printenv AUTOOS_OMNIROUTE_KEY", "environment"),
                ("env", "environment"),
                ("cat /proc/self/environ", "live process entry"),
                ("cat /proc/$PPID/environ", "expansion"),
                ("cat /proc/1234/environ", "live process entry"),
                ("rg KEY /proc/self/environ", "live process entry"),
                ("echo $AUTOOS_OMNIROUTE_KEY", "expansion"),
                ("ls ${AUTOOS_OMNIROUTE_KEY}", "expansion"),
                ("git log --format=$AUTOOS_OMNIROUTE_KEY", "expansion")):
            self._l2_denied(cmd, why)

    def test_l2_no_expansion_hides_in_a_glued_option_value(self):
        # Sonnet seat REJECT (L2SECRETS fix 10, 2026-10-08): l2OptionOperandReason
        # path-checked a glued short value (`-XVALUE`) only when the head was rg and
        # the option was one of rg's file-opening flags, so every OTHER glued value
        # went unexamined — and a `$` in one is the lane's own environment handed to
        # a read-only command. `rg -r$AUTOOS_OMNIROUTE_KEY foo README.md` printed the
        # key as a replacement string; `head -c$AUTOOS_OMNIROUTE_KEY README.md` made
        # it a byte count. Neither token is a path, so the path rule never saw them.
        # The rule is now lexical, not per-flag: no `$` survives in an L2 command.
        for cmd in (
                "rg -r$AUTOOS_OMNIROUTE_KEY foo README.md",
                "rg -r${OPENCODE_SERVER_PASSWORD} foo README.md",
                "head -c$AUTOOS_OMNIROUTE_KEY README.md",
                "tail -n$AUTOOS_OMNIROUTE_KEY README.md",
                "rg -m$AUTOOS_OMNIROUTE_KEY foo README.md",
                "git log -G$AUTOOS_OMNIROUTE_KEY",
                'rg -r"$AUTOOS_OMNIROUTE_KEY" foo README.md',
                "rg --replace=$AUTOOS_OMNIROUTE_KEY foo README.md",
                "rg -e$AUTOOS_OMNIROUTE_KEY README.md",
                "rg -A$((1)) foo README.md",
                # fix 11: the quote is not a boundary the guard honours, so a `"`
                # glued around the value - on a flag or on a plain operand - is the
                # same read. `cat "README$KEY.md"` puts the key inside a FILE NAME,
                # which no flag rule ever looks at.
                'rg -e"$AUTOOS_OMNIROUTE_KEY" README.md',
                'rg -r"${OPENCODE_SERVER_PASSWORD}" foo README.md',
                'cat "README$AUTOOS_OMNIROUTE_KEY.md"',
                'rg foo"$AUTOOS_OMNIROUTE_KEY" README.md',
                'rg --replace="$(printenv AUTOOS_OMNIROUTE_KEY)" foo README.md',
                'head -c"$((1+1))" README.md',
                "git log --format=$AUTOOS_OMNIROUTE_KEY",
                "git log --grep=${AUTOOS_OMNIROUTE_KEY}"):
            self._l2_denied(cmd, "expansion")
        # ANSI-C quoting writes the `$` as `\x24`, so the command can carry an
        # expansion with no `$` in the text at all - and the backslash that spells
        # it is what the guard reads first. Both are refused, fail closed.
        for cmd in ("rg $'\\x41' README.md",
                    "rg -r$'\\x24AUTOOS_OMNIROUTE_KEY' foo README.md"):
            self._l2_denied(cmd, "backslash")
        # A backtick is a substitution with no `$` either; the substitution scan
        # runs before any operand rule, whatever flag it rides in on.
        self._l2_denied("rg -r`printenv AUTOOS_OMNIROUTE_KEY` foo README.md",
                        "substitution")
        # `~` is expanded by the same shell, and a leading `~` reaches a home
        # directory outside the checkout whatever flag it is glued to.
        for cmd in ("head -n~/lane.key README.md", "rg -m~/x foo README.md"):
            self._l2_denied(cmd, "path outside repo")
        # The read-only list is unchanged: a flag with a plain glued value stays
        # allowed, so the rule costs the lane nothing it was already permitted.
        for cmd in ("head -n5 README.md", "tail -c20 README.md", "rg -m10 foo README.md",
                    "rg -e foo README.md", "head -c-1 README.md", "git log -Gfoo"):
            self._l2_allowed(cmd)


if __name__ == "__main__":
    unittest.main()
