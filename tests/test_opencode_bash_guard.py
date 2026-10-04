#!/usr/bin/env python3
"""Tests for configuration/opencode/plugins/bash-guard/index.mjs.

Drives the OpenCode bash-guard plugin through a minimal fake ctx harness
under Node.js, verifying:
- Incident-shape command denial (unquoted heredoc with backticks)
- Claude background invocation denial (--bg with $() in double quotes)
- Safe command allow paths (quoted heredoc, plain commands, echo with <<)
- Forward-compat tool naming ('shell' and 'bash' intercepted; others bypassed)
- Resilient fail-open behavior (missing guard, non-zero guard exit, timeout)
- Absence of embedded rule logic in the plugin source
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def run_plugin(event, env_overrides=None, plugin_path=None, timeout=20, keep_location=False):
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
            if env_overrides.get("AUTOOS_REPO_ROOT") is None:
                env.pop("AUTOOS_REPO_ROOT", None)

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


if __name__ == "__main__":
    unittest.main()
