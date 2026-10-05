#!/usr/bin/env python3
"""Static AST and runtime stdio tests verifying that read-only git subprocesses
reachable from MCP tools do not inherit stdin (MCP-STDIN).
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class McpStdinDevnullTests(unittest.TestCase):
    def test_git_calls_pass_stdin_devnull(self):
        """Parse tools/autoos-agent.py and tools/autoos_agent_mcp.py with ast
        and assert every subprocess call running git passes stdin= or input=.
        """
        for relpath in ["tools/autoos-agent.py", "tools/autoos_agent_mcp.py"]:
            filepath = ROOT / relpath
            self.assertTrue(filepath.is_file(), f"{relpath} not found")
            tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
            missing = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                is_subproc = False
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                        is_subproc = True
                if not is_subproc:
                    continue

                first_arg = None
                if node.args:
                    first_arg = node.args[0]
                for kw in node.keywords:
                    if kw.arg in ("args", "cmd"):
                        first_arg = kw.value

                is_git = False
                if isinstance(first_arg, (ast.List, ast.Tuple)):
                    if first_arg.elts and isinstance(first_arg.elts[0], ast.Constant) and first_arg.elts[0].value == "git":
                        is_git = True

                if is_git:
                    has_stdin = any(kw.arg in ("stdin", "input") for kw in node.keywords)
                    if not has_stdin:
                        missing.append((node.lineno, ast.unparse(node).splitlines()[0]))

            self.assertEqual(
                missing, [],
                f"Subprocess git calls in {relpath} without stdin= or input=: {missing}"
            )

    def test_runtime_mcp_stdio_ps(self):
        """Start tools/autoos_agent_mcp.py over stdio with AUTOOS_WORKERS_DIR unset,
        keep stdin pipe open and idle, call 'ps', and verify it responds within timeout.
        """
        server_py = ROOT / "tools" / "autoos_agent_mcp.py"
        self.assertTrue(server_py.is_file(), f"{server_py} not found")

        server_cmd = [sys.executable, str(server_py)]
        try:
            import mcp  # noqa: F401
        except ImportError:
            uv = shutil.which("uv")
            if uv:
                server_cmd = [uv, "run", "--no-project", "--with", "mcp<2", "python", str(server_py)]
            else:
                raise unittest.SkipTest("mcp python package not installed")

        env = os.environ.copy()
        env.pop("AUTOOS_WORKERS_DIR", None)

        proc = subprocess.Popen(
            server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        def read_reply(stream, box, rid):
            while True:
                line = stream.readline()
                if not line:
                    box["err"] = "EOF"
                    return
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("id") == rid:
                    box["msg"] = data
                    return

        try:
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            }
            proc.stdin.write(json.dumps(init_req) + "\n")
            proc.stdin.flush()

            box1 = {}
            t1 = threading.Thread(target=read_reply, args=(proc.stdout, box1, 1))
            t1.daemon = True
            t1.start()
            t1.join(timeout=10.0)
            self.assertFalse(t1.is_alive(), "Timed out waiting for initialize response")
            self.assertIn("msg", box1, f"Failed to get initialize response: {box1.get('err')}")
            self.assertIn("result", box1["msg"], f"Initialize failed: {box1['msg']}")

            notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            proc.stdin.write(json.dumps(notif) + "\n")
            proc.stdin.flush()

            call_req = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "ps", "arguments": {}},
            }
            proc.stdin.write(json.dumps(call_req) + "\n")
            proc.stdin.flush()

            box2 = {}
            t2 = threading.Thread(target=read_reply, args=(proc.stdout, box2, 2))
            t2.daemon = True
            t2.start()
            t2.join(timeout=10.0)
            self.assertFalse(t2.is_alive(), "Timed out waiting for ps tool response (hung stdin inheritance)")
            self.assertIn("msg", box2, f"Failed to get ps response: {box2.get('err')}")
            self.assertIn("result", box2["msg"], f"ps call failed: {box2['msg']}")
            result = box2["msg"]["result"]
            self.assertFalse(result.get("isError", False), f"ps tool returned error: {result}")

        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
