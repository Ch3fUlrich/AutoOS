"""Tests for tools/hostexec/server.py (spec status/L1-backlog.spec-host-shell.md
section 4 item 4). Two tiers:

  * plain-function tests (verify_token, host_run/host_policy/host_log_tail,
    bind-address policy) -- pure stdlib, always run, no `mcp` needed. This
    is most of the file, and is what proves server.py imports and works
    without the `mcp` package installed (brief item 4: "import lazily so
    the core tests run without it").
  * one end-to-end test that actually starts the broker on 127.0.0.1:0 and
    calls host_policy() + a denied host_run() over real HTTP, using the MCP
    SDK's own streamable-http client. Skipped (with a printed reason) if
    `mcp`/`starlette`/`uvicorn` are not importable -- run it for real via:

      uv run --no-project --with 'mcp<2' python3 tests/test_hostexec_server.py

Run from anywhere:

    python3 tests/test_hostexec_server.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from hostexec import audit, policy, runner, server  # noqa: E402

_HAVE_MCP_STACK = all(importlib.util.find_spec(m) is not None
                       for m in ("mcp", "starlette", "uvicorn"))


def _policy(bindir: str, *, storage_forbid: bool = True) -> policy.Policy:
    actors = {
        server_sha("tok-claude"): policy.Actor(name="claude", tier="default"),
        server_sha("tok-openhands"): policy.Actor(name="openhands", tier="default"),
    }
    hosts = {
        "coding-host": policy.HostEntry(alias="coding-host", kind="local"),
        "storage": policy.HostEntry(alias="storage", kind="ssh", target="storage",
                                     forbid=storage_forbid),
    }
    return policy.Policy(actors=actors, hosts=hosts, path=(bindir,), max_args=64, max_arg_length=4096)


def server_sha(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _make_bindir(tmp: str) -> str:
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir, exist_ok=True)
    for name in ("echo", "id", "sudo"):
        p = os.path.join(bindir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho ok\n")
        os.chmod(p, 0o755)
    return bindir


class VerifyTokenTests(unittest.TestCase):
    def test_correct_token_resolves_its_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = _policy(_make_bindir(tmp))
            self.assertEqual(server.verify_token(pol, "tok-claude"), "claude")
            self.assertEqual(server.verify_token(pol, "tok-openhands"), "openhands")

    def test_unknown_token_resolves_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = _policy(_make_bindir(tmp))
            self.assertIsNone(server.verify_token(pol, "not-a-real-token"))

    def test_empty_or_none_token_resolves_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = _policy(_make_bindir(tmp))
            self.assertIsNone(server.verify_token(pol, None))
            self.assertIsNone(server.verify_token(pol, ""))


class BindPolicyTests(unittest.TestCase):
    def test_default_bind_is_loopback_only(self):
        self.assertEqual(server.parse_bind(None), ["127.0.0.1"])

    def test_comma_list_is_split(self):
        self.assertEqual(server.parse_bind("127.0.0.1, ::1 ,172.17.0.1"),
                          ["127.0.0.1", "::1", "172.17.0.1"])

    def test_loopback_addresses_are_always_allowed(self):
        server.check_bind_allowed(["127.0.0.1", "::1", "localhost"], allow_lan=False)  # no raise

    def test_non_loopback_is_refused_without_allow_lan(self):
        with self.assertRaises(ValueError):
            server.check_bind_allowed(["0.0.0.0"], allow_lan=False)
        with self.assertRaises(ValueError):
            server.check_bind_allowed(["::"], allow_lan=False)
        with self.assertRaises(ValueError):
            server.check_bind_allowed(["172.17.0.1"], allow_lan=False)

    def test_non_loopback_is_allowed_with_allow_lan(self):
        server.check_bind_allowed(["0.0.0.0", "172.17.0.1"], allow_lan=True)  # no raise


class HostRunPlainFunctionTests(unittest.TestCase):
    def test_allowed_call_runs_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = _make_bindir(tmp)
            pol = _policy(bindir)
            log = audit.AuditLog(os.path.join(tmp, "state"))
            result = server.host_run(pol, log, actor="claude", host="coding-host",
                                      argv=["echo", "hi"], cwd=tmp)
            self.assertEqual(result["exit"], 0)
            log.close()

    def test_denied_call_raises_refused_and_still_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = _make_bindir(tmp)
            pol = _policy(bindir)
            state_dir = os.path.join(tmp, "state")
            log = audit.AuditLog(state_dir)
            with self.assertRaises(server.Refused) as ctx:
                server.host_run(pol, log, actor="claude", host="coding-host",
                                 argv=["sudo", "id"], cwd=tmp)
            self.assertEqual(ctx.exception.rule, "no-sudo")
            log.close()
            lines = list(audit.tail(state_dir, decision="deny"))
            self.assertTrue(any("no-sudo" in ln for ln in lines))

    def test_forbidden_host_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = _policy(_make_bindir(tmp))
            log = audit.AuditLog(os.path.join(tmp, "state"))
            with self.assertRaises(server.Refused) as ctx:
                server.host_run(pol, log, actor="claude", host="storage", argv=["echo", "hi"])
            self.assertEqual(ctx.exception.rule, "forbid-host")
            log.close()

    def test_fail_closed_when_audit_log_is_unwritable(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = _policy(_make_bindir(tmp))
            locked_parent = os.path.join(tmp, "locked")
            os.makedirs(locked_parent)
            os.chmod(locked_parent, 0o500)
            try:
                log = audit.AuditLog(os.path.join(locked_parent, "state"))
                with self.assertRaises(server.Refused):
                    # an ALLOWED argv must still be refused: the log can't be written
                    server.host_run(pol, log, actor="claude", host="coding-host",
                                     argv=["echo", "hi"])
            finally:
                os.chmod(locked_parent, 0o700)

    def test_run_fn_is_injectable_for_testing_without_a_real_process(self):
        calls = []

        def fake_run(pol, host_entry, argv, cwd):
            calls.append((host_entry.alias, argv, cwd))
            return runner.RunResult(exit_code=0, duration_ms=1, out_bytes=2,
                                     output="ok", truncated=False, timed_out=False)

        with tempfile.TemporaryDirectory() as tmp:
            pol = _policy(_make_bindir(tmp))
            log = audit.AuditLog(os.path.join(tmp, "state"))
            result = server.host_run(pol, log, actor="claude", host="coding-host",
                                      argv=["echo", "hi"], cwd=tmp, run_fn=fake_run)
            self.assertEqual(result["output"], "ok")
            self.assertEqual(calls, [("coding-host", ["echo", "hi"], tmp)])
            log.close()


class HostPolicyToolTests(unittest.TestCase):
    def test_lists_hosts_and_rule_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = _policy(_make_bindir(tmp))
            out = server.host_policy(pol)
            self.assertIn("coding-host", out["hosts"])
            self.assertIn("storage", out["hosts"])
            self.assertTrue(out["hosts"]["storage"]["forbid"])
            self.assertIn("no-sudo", out["rules"])
            self.assertIn("git-option-injection", out["rules"])


class HostLogTailScopingTests(unittest.TestCase):
    def test_only_the_callers_own_actor_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "state")
            log = audit.AuditLog(state_dir)
            log.write(actor="claude", session=None, via="http", host="coding-host",
                      argv=["echo"], cwd="/tmp", run_as="claude", decision="allow",
                      rule=None, reason=None, exit_code=0, duration_ms=1,
                      out_bytes=0, truncated=False)
            log.write(actor="openhands", session=None, via="http", host="coding-host",
                      argv=["sudo", "id"], cwd="/tmp", run_as="openhands", decision="deny",
                      rule="no-sudo", reason=None, exit_code=None, duration_ms=None,
                      out_bytes=None, truncated=False)
            log.close()
            out = server.host_log_tail(state_dir, actor="claude", n=20)
            self.assertTrue(all("actor=claude" in ln for ln in out["lines"]))
            self.assertFalse(any("openhands" in ln for ln in out["lines"]))


@unittest.skipUnless(_HAVE_MCP_STACK, "mcp/starlette/uvicorn not importable "
                                       "(run via: uv run --no-project --with 'mcp<2' "
                                       "python3 tests/test_hostexec_server.py)")
class EndToEndHttpTests(unittest.TestCase):
    """Starts the real broker on 127.0.0.1:<ephemeral>, calls host_policy()
    and a denied host_run() over actual HTTP with the MCP SDK's own client."""

    def setUp(self):
        import uvicorn

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        bindir = _make_bindir(self._tmp.name)
        self.pol = _policy(bindir)
        self.state_dir = os.path.join(self._tmp.name, "state")
        self.audit_log = audit.AuditLog(self.state_dir)
        self.app = server.build_asgi_app(self.pol, audit_log=self.audit_log)

        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning")
        self.uv_server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.uv_server.run, daemon=True)
        self.thread.start()
        deadline = __import__("time").time() + 10
        while not getattr(self.uv_server, "started", False):
            if __import__("time").time() > deadline:
                raise RuntimeError("uvicorn did not start in time")
            __import__("time").sleep(0.02)
        self.port = self.uv_server.servers[0].sockets[0].getsockname()[1]
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.uv_server.should_exit = True
        self.thread.join(timeout=10)
        self.audit_log.close()

    def _call(self, token, name, arguments):
        import asyncio

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def go():
            url = f"http://127.0.0.1:{self.port}/mcp"
            headers = {"Authorization": f"Bearer {token}"} if token else None
            async with streamablehttp_client(url, headers=headers) as (read, write, _get_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(name, arguments)

        return asyncio.run(go())

    def test_host_policy_over_http(self):
        result = self._call("tok-claude", "host_policy", {})
        self.assertFalse(result.isError)
        text = result.content[0].text
        self.assertIn("no-sudo", text)

    def test_denied_host_run_over_http_is_a_tool_error_with_the_refusal_shape(self):
        result = self._call("tok-claude", "host_run",
                             {"host": "coding-host", "argv": ["sudo", "id"]})
        self.assertTrue(result.isError)
        text = result.content[0].text
        self.assertIn("refused", text)
        self.assertIn("no-sudo", text)

    def test_missing_token_is_unauthorized(self):
        with self.assertRaises(Exception):
            self._call(None, "host_policy", {})


if __name__ == "__main__":
    if not _HAVE_MCP_STACK:
        print("NOTE: mcp/starlette/uvicorn not importable in this interpreter -- "
              "EndToEndHttpTests will be skipped. Run via:\n"
              "  uv run --no-project --with 'mcp<2' python3 tests/test_hostexec_server.py",
              file=sys.stderr)
    unittest.main()
