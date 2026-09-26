"""Tests for configuration/hostexec/install.sh (TASK hostexec-2).

Standalone driver that wires the hostexec broker into agent clients and
installs the systemd USER unit file. Nothing is enabled or started.

The driver is exercised with HOME pointed at a temp dir and a stub
`systemctl` first on PATH that records its argv. The driver must never
call it with `enable`/`start` (it only prints the operator command).

Run from anywhere:

    python3 tests/test_hostexec_install.py
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / "configuration" / "hostexec" / "install.sh"
TEMPLATE = ROOT / "configuration" / "hostexec" / "autoos-hostexec.service"

TOKEN_VALUE = "test-token-value-abc123"
TEST_PORT = "19871"


def _default_port() -> str:
    """The broker default, parsed from tools/hostexec/server.py (the driver
    must use that default when AUTOOS_EXEC_PORT is unset)."""
    text = (ROOT / "tools" / "hostexec" / "server.py").read_text(encoding="utf-8")
    match = re.search(r"^DEFAULT_PORT\s*=\s*(\d+)", text, re.MULTILINE)
    assert match is not None, "DEFAULT_PORT not found in tools/hostexec/server.py"
    return match.group(1)


class _DriverCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.bindir = Path(self.tmp.name) / "bin"
        self.bindir.mkdir()
        # Stub systemctl: records argv, never does anything.
        self.syslog = Path(self.tmp.name) / "systemctl.log"
        stub = self.bindir / "systemctl"
        stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.syslog}"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env["PATH"] = str(self.bindir) + os.pathsep + self.env.get("PATH", "")
        self.env["AUTOOS_EXEC_PORT"] = TEST_PORT
        # A hermetic test must not leak the operator's real token file env.
        for key in list(self.env):
            if key.startswith("AUTOOS_EXEC_TOKEN_FILE_"):
                del self.env[key]

    def tearDown(self):
        self.tmp.cleanup()

    def run_driver(self, *args: str, env_extra: dict | None = None):
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            ["bash", str(DRIVER), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
        )
        return proc

    def run_driver_ok(self, *args: str, env_extra: dict | None = None):
        proc = self.run_driver(*args, env_extra=env_extra)
        self.assertEqual(
            proc.returncode,
            0,
            f"driver {' '.join(args)} exited {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )
        return proc

    def write_token(self, client: str, mode: int = 0o600) -> Path:
        tokdir = self.home / ".config" / "autoos" / "exec"
        tokdir.mkdir(parents=True, exist_ok=True)
        token_file = tokdir / f"{client}.token"
        token_file.write_text(TOKEN_VALUE + "\n", encoding="utf-8")
        os.chmod(token_file, mode)
        return token_file

    def systemctl_argv(self) -> list[str]:
        if not self.syslog.exists():
            return []
        return self.syslog.read_text(encoding="utf-8").splitlines()

    def assert_no_enable_or_start(self):
        for line in self.systemctl_argv():
            words = line.split()
            self.assertNotIn("enable", words, f"driver called systemctl with enable: {line!r}")
            self.assertNotIn("start", words, f"driver called systemctl with start: {line!r}")

    def assert_token_nowhere(self, proc, extra_files: list[Path] | None = None):
        self.assertNotIn(TOKEN_VALUE, proc.stdout, "token leaked into stdout")
        self.assertNotIn(TOKEN_VALUE, proc.stderr, "token leaked into stderr")
        for line in self.systemctl_argv():
            self.assertNotIn(TOKEN_VALUE, line, "token leaked into a stubbed command's argv")
        for path in extra_files or []:
            if path.name.endswith(".token"):
                continue  # the token file itself legitimately holds the token
            if path.is_file():
                self.assertNotIn(
                    TOKEN_VALUE,
                    path.read_text(encoding="utf-8"),
                    f"token leaked into {path}",
                )

    def home_files(self) -> set[str]:
        return {
            str(p.relative_to(self.home))
            for p in self.home.rglob("*")
            if p.is_file() and ".autoos-backup-" not in str(p)
        }


class UnitInstallTests(_DriverCase):
    def test_unit_install_and_second_run_already_current(self):
        proc = self.run_driver_ok("--unit")
        unit = self.home / ".config" / "systemd" / "user" / "autoos-hostexec.service"
        self.assertTrue(unit.is_file(), f"unit not installed\nstdout:\n{proc.stdout}")
        text = unit.read_text(encoding="utf-8")
        self.assertIn("tools/hostexec.py serve --policy %h/.config/autoos/exec/policy.toml", text)
        self.assertIn("EnvironmentFile=-%h/.config/autoos/exec/hostexec.env", text)
        self.assertIn("Restart=on-failure", text)
        self.assertIn("NoNewPrivileges=yes", text)
        self.assertIn("PrivateTmp=yes", text)
        # The operator command is printed, never executed here.
        self.assertIn("systemctl --user", proc.stdout)
        self.assert_no_enable_or_start()

        before = unit.read_bytes()
        again = self.run_driver_ok("--unit")
        self.assertIn("already current", again.stdout + again.stderr)
        self.assertEqual(unit.read_bytes(), before, "second run rewrote an identical unit")
        self.assert_no_enable_or_start()

    def test_differing_unit_is_backed_up_before_replace(self):
        unit = self.home / ".config" / "systemd" / "user" / "autoos-hostexec.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("# operator-edited unit\n", encoding="utf-8")
        self.run_driver_ok("--unit")
        backups = sorted(unit.parent.glob("autoos-hostexec.service.autoos-backup-*"))
        self.assertEqual(len(backups), 1, f"expected one backup, got: {backups}")
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "# operator-edited unit\n")
        self.assertIn("tools/hostexec.py", unit.read_text(encoding="utf-8"))

    def test_same_second_double_backup_keeps_both(self):
        unit = self.home / ".config" / "systemd" / "user" / "autoos-hostexec.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("# version one\n", encoding="utf-8")
        self.run_driver_ok("--unit")
        unit.write_text("# version two\n", encoding="utf-8")
        self.run_driver_ok("--unit")
        backups = sorted(unit.parent.glob("autoos-hostexec.service.autoos-backup-*"))
        self.assertEqual(len(backups), 2, f"expected two backups, got: {backups}")
        contents = sorted(b.read_text(encoding="utf-8") for b in backups)
        self.assertEqual(contents, ["# version one\n", "# version two\n"])

    def test_failed_copy_leaves_existing_unit_untouched(self):
        # A template that cannot be read must stop the install with the
        # existing unit byte-identical (failure path of install producer).
        unit = self.home / ".config" / "systemd" / "user" / "autoos-hostexec.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("# keep me\n", encoding="utf-8")
        proc = self.run_driver("--unit", env_extra={"AUTOOS_HOSTEXEC_BREAK": "no-template"})
        self.assertNotEqual(proc.returncode, 0, "broken template must fail the install")
        self.assertEqual(unit.read_text(encoding="utf-8"), "# keep me\n")

    def test_failed_backup_stops_install_and_leaves_unit_untouched(self):
        # install.sh:170 -- a failed unit backup must stop, leaving the
        # existing unit untouched (cp stub on PATH that fails only backups).
        unit = self.home / ".config" / "systemd" / "user" / "autoos-hostexec.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("# keep me\n", encoding="utf-8")
        cp_stub = self.bindir / "cp"
        cp_stub.write_text(
            "#!/bin/sh\n"
            'for a in \"$@\"; do case \"$a\" in *.autoos-backup-*) exit 1;; esac; done\n'
            'if [ -x /bin/cp ]; then exec /bin/cp \"$@\"; else exec /usr/bin/cp \"$@\"; fi\n',
            encoding="utf-8",
        )
        cp_stub.chmod(0o755)
        proc = self.run_driver("--unit")
        self.assertNotEqual(proc.returncode, 0, "failed backup must fail the install")
        self.assertEqual(unit.read_text(encoding="utf-8"), "# keep me\n")


class ClientWriterTests(_DriverCase):
    BRIDGE_BIND = "172.17.0.1"

    def test_openhands_keeps_unrelated_keys_and_writes_no_enabled(self):
        self.write_token("openhands")
        settings = self.home / ".openhands" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            '{"agent_settings": {"mcp_config": {"other": {"url": "x"}}}, "keep": 1}',
            encoding="utf-8",
        )
        proc = self.run_driver_ok(
            "--clients", "openhands", env_extra={"AUTOOS_EXEC_BIND": self.BRIDGE_BIND}
        )
        import json

        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(data["keep"], 1)
        self.assertEqual(data["agent_settings"]["mcp_config"]["other"], {"url": "x"})
        entry = data["agent_settings"]["mcp_config"]["hostexec"]
        self.assertEqual(
            entry["url"], f"http://host.docker.internal:{TEST_PORT}/mcp"
        )
        self.assertEqual(
            entry["headers"], {"Authorization": f"Bearer {TOKEN_VALUE}"}
        )
        self.assertNotIn("enabled", entry, "OpenHands rejects an `enabled` key")
        self.assert_token_nowhere(proc)
        self.assert_no_enable_or_start()
        # Second run: unchanged, reported as already current.
        before = settings.read_bytes()
        again = self.run_driver_ok(
            "--clients", "openhands", env_extra={"AUTOOS_EXEC_BIND": self.BRIDGE_BIND}
        )
        self.assertEqual(settings.read_bytes(), before)
        self.assertIn("already current", again.stdout + again.stderr)

    def test_openhands_skipped_when_bind_loopback_only(self):
        # install.sh:505 -- loopback-only bind cannot be reached from
        # containers: SKIP with a message naming AUTOOS_EXEC_BIND.
        self.write_token("openhands")
        settings = self.home / ".openhands" / "settings.json"
        proc = self.run_driver_ok("--clients", "openhands")
        combined = proc.stdout + proc.stderr
        self.assertIn("AUTOOS_EXEC_BIND", combined)
        self.assertFalse(settings.exists(), "loopback-only bind must write nothing")
        self.assert_token_nowhere(proc)

    def test_openhands_reads_bind_from_env_file(self):
        # Same as server.py: ~/.config/autoos/exec/hostexec.env provides
        # AUTOOS_EXEC_BIND when the env var is unset. Tests both ways.
        self.write_token("openhands")
        envfile = self.home / ".config" / "autoos" / "exec" / "hostexec.env"
        envfile.parent.mkdir(parents=True, exist_ok=True)
        settings = self.home / ".openhands" / "settings.json"
        envfile.write_text(f"AUTOOS_EXEC_BIND={self.BRIDGE_BIND}\n", encoding="utf-8")
        proc = self.run_driver_ok("--clients", "openhands")
        import json

        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(
            data["agent_settings"]["mcp_config"]["hostexec"]["url"],
            f"http://host.docker.internal:{TEST_PORT}/mcp",
        )
        self.assert_token_nowhere(proc)
        # Loopback-only env file -> skip again.
        settings.unlink()
        envfile.write_text("AUTOOS_EXEC_BIND=127.0.0.1\n", encoding="utf-8")
        proc2 = self.run_driver_ok("--clients", "openhands")
        self.assertIn("AUTOOS_EXEC_BIND", proc2.stdout + proc2.stderr)
        self.assertFalse(settings.exists())
        self.assert_token_nowhere(proc2)

    def test_claude_keeps_unrelated_keys(self):
        self.write_token("claude")
        claude_json = self.home / ".claude.json"
        claude_json.write_text(
            '{"mcpServers": {"other": {"type": "x"}}, "keep": true}', encoding="utf-8"
        )
        proc = self.run_driver_ok("--clients", "claude")
        import json

        data = json.loads(claude_json.read_text(encoding="utf-8"))
        self.assertIs(data["keep"], True)
        self.assertEqual(data["mcpServers"]["other"], {"type": "x"})
        entry = data["mcpServers"]["hostexec"]
        self.assertEqual(entry["type"], "http")
        self.assertEqual(entry["url"], f"http://127.0.0.1:{TEST_PORT}/mcp")
        self.assertEqual(entry["headers"], {"Authorization": f"Bearer {TOKEN_VALUE}"})
        self.assert_token_nowhere(proc)
        before = claude_json.read_bytes()
        again = self.run_driver_ok("--clients", "claude")
        self.assertEqual(claude_json.read_bytes(), before)
        self.assertIn("already current", again.stdout + again.stderr)

    def test_codex_keeps_unrelated_toml_and_points_at_env_var(self):
        self.write_token("codex")
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('[other]\nkeep = 1\n\n[mcp_servers.other]\nurl = "x"\n', encoding="utf-8")
        proc = self.run_driver_ok("--clients", "codex")
        text = config.read_text(encoding="utf-8")
        self.assertIn('[other]\nkeep = 1', text)
        self.assertIn('[mcp_servers.other]\nurl = "x"', text)
        self.assertIn("[mcp_servers.hostexec]", text)
        self.assertIn(f"http://127.0.0.1:{TEST_PORT}/mcp", text)
        self.assertIn('bearer_token_env_var = "AUTOOS_EXEC_TOKEN"', text)
        self.assertIn("AUTOOS_EXEC_TOKEN", proc.stdout + proc.stderr)
        self.assert_token_nowhere(proc, extra_files=[config])
        before = config.read_bytes()
        again = self.run_driver_ok("--clients", "codex")
        self.assertEqual(config.read_bytes(), before)
        self.assertIn("already current", again.stdout + again.stderr)

    def test_opencode_keeps_unrelated_keys(self):
        # mcp.hostexec is correct for this repo (NOT mcp.servers):
        # lib/linux/install.sh writes data['mcp'][name] for opencode,
        # so the driver must use the ["mcp"] parent key here.
        self.write_token("opencode")
        opencode_json = self.home / ".config" / "opencode" / "opencode.json"
        opencode_json.parent.mkdir(parents=True, exist_ok=True)
        opencode_json.write_text('{"mcp": {"other": {"type": "x"}}, "keep": 1}', encoding="utf-8")
        proc = self.run_driver_ok("--clients", "opencode")
        import json

        data = json.loads(opencode_json.read_text(encoding="utf-8"))
        self.assertEqual(data["keep"], 1)
        self.assertEqual(data["mcp"]["other"], {"type": "x"})
        entry = data["mcp"]["hostexec"]
        self.assertEqual(entry["type"], "remote")
        self.assertEqual(entry["url"], f"http://127.0.0.1:{TEST_PORT}/mcp")
        self.assert_token_nowhere(proc)
        before = opencode_json.read_bytes()
        again = self.run_driver_ok("--clients", "opencode")
        self.assertEqual(opencode_json.read_bytes(), before)
        self.assertIn("already current", again.stdout + again.stderr)

    def test_opencode_jsonc_with_comments_is_refused_not_rewritten(self):
        self.write_token("opencode")
        opencode_json = self.home / ".config" / "opencode" / "opencode.json"
        opencode_json.parent.mkdir(parents=True, exist_ok=True)
        original = '{\n  // operator comment\n  "mcp": {}\n}\n'
        opencode_json.write_text(original, encoding="utf-8")
        proc = self.run_driver("--clients", "opencode")
        self.assertNotEqual(proc.returncode, 0, "JSONC input must be refused")
        self.assertIn("comment", (proc.stdout + proc.stderr).lower())
        self.assertEqual(opencode_json.read_text(encoding="utf-8"), original)
        self.assert_token_nowhere(proc)

    def test_json_non_object_parent_is_refused_without_writing(self):
        # install.sh:347 -- a non-object parent (null/list/string) must be
        # refused without writing, message names the key.
        self.write_token("claude")
        claude_json = self.home / ".claude.json"
        for bad in ('{"mcpServers": null}', '{"mcpServers": []}', '{"mcpServers": "x"}'):
            claude_json.write_text(bad, encoding="utf-8")
            proc = self.run_driver("--clients", "claude")
            self.assertNotEqual(proc.returncode, 0, f"parent {bad} must be refused")
            self.assertIn("mcpServers", proc.stdout + proc.stderr)
            self.assertEqual(claude_json.read_text(encoding="utf-8"), bad)
            self.assert_token_nowhere(proc)

    def test_qoder_prints_no_token_command(self):
        # install.sh:550 -- qoder wiring is not automated: never print or
        # build a command containing the token (no $(cat ...), no token
        # value); point to the README instead.
        token_file = self.write_token("qoder")
        token_content = token_file.read_text(encoding="utf-8").strip()
        before = self.home_files()
        proc = self.run_driver_ok("--clients", "qoder")
        combined = proc.stdout + proc.stderr
        self.assertNotIn(token_content, combined)
        self.assertNotIn("$(cat", combined)
        self.assertIn("not automated", combined.lower())
        self.assertIn("README", combined)
        self.assert_token_nowhere(proc)
        after = self.home_files() - {
            ".config/systemd/user/autoos-hostexec.service",  # unit install is expected
        }
        self.assertEqual(after, before, "qoder wiring must not write client files")

    def test_default_port_comes_from_server_py(self):
        self.write_token("claude")
        proc = self.run_driver_ok(
            "--clients", "claude", env_extra={"AUTOOS_EXEC_PORT": ""}
        )
        import json

        claude_json = self.home / ".claude.json"
        data = json.loads(claude_json.read_text(encoding="utf-8"))
        self.assertEqual(
            data["mcpServers"]["hostexec"]["url"],
            f"http://127.0.0.1:{_default_port()}/mcp",
        )
        self.assert_token_nowhere(proc)


class TokenTests(_DriverCase):
    def test_group_readable_token_is_refused(self):
        self.write_token("claude", mode=0o644)
        claude_json = self.home / ".claude.json"
        proc = self.run_driver("--clients", "claude")
        self.assertNotEqual(proc.returncode, 0, "a 0644 token file must be refused")
        self.assertIn("0600", proc.stdout + proc.stderr)
        self.assertFalse(claude_json.exists(), "refused wiring must write nothing")
        self.assert_token_nowhere(proc)

    def test_missing_token_is_refused(self):
        proc = self.run_driver("--clients", "claude")
        self.assertNotEqual(proc.returncode, 0, "a missing token file must be refused")
        self.assertFalse((self.home / ".claude.json").exists())
        self.assert_token_nowhere(proc)

    def test_token_file_env_override_is_honoured(self):
        custom = Path(self.tmp.name) / "custom.token"
        custom.write_text(TOKEN_VALUE + "\n", encoding="utf-8")
        os.chmod(custom, 0o600)
        proc = self.run_driver_ok(
            "--clients", "claude", env_extra={"AUTOOS_EXEC_TOKEN_FILE_CLAUDE": str(custom)}
        )
        import json

        data = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data["mcpServers"]["hostexec"]["headers"],
            {"Authorization": f"Bearer {TOKEN_VALUE}"},
        )
        self.assert_token_nowhere(proc)


class DryRunTests(_DriverCase):
    def test_dry_run_writes_nothing(self):
        self.write_token("claude")
        self.write_token("openhands")
        before = self.home_files()
        proc = self.run_driver_ok("--dry-run", "--unit", "--clients", "claude,openhands,qoder")
        self.assertEqual(self.home_files(), before, "--dry-run wrote files")
        combined = proc.stdout + proc.stderr
        self.assertIn("dry-run", combined.lower())
        self.assert_token_nowhere(proc)


class UnregisterTests(_DriverCase):
    def _install_all(self):
        for client in ("openhands", "claude", "codex", "opencode"):
            self.write_token(client)
        self.write_token("qoder")
        # No --unit: one run installs the unit AND wires every client.
        # OpenHands needs a container-reachable bind (docker bridge gateway).
        self.run_driver_ok(
            "--clients",
            "openhands,claude,codex,opencode,qoder",
            env_extra={"AUTOOS_EXEC_BIND": "172.17.0.1"},
        )

    def test_unregister_twice(self):
        import json

        self._install_all()
        proc = self.run_driver_ok("--unregister")
        unit = self.home / ".config" / "systemd" / "user" / "autoos-hostexec.service"
        self.assertFalse(unit.exists(), "unit must be removed")
        claude_data = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertNotIn("hostexec", claude_data.get("mcpServers", {}))
        openhands_data = json.loads(
            (self.home / ".openhands" / "settings.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("hostexec", openhands_data["agent_settings"]["mcp_config"])
        codex_text = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn("hostexec", codex_text)
        opencode_data = json.loads(
            (self.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("hostexec", opencode_data.get("mcp", {}))
        self.assert_token_nowhere(proc)
        self.assert_no_enable_or_start()

        again = self.run_driver_ok("--unregister")
        self.assertIn("nothing to remove", again.stdout + again.stderr)
        self.assert_no_enable_or_start()


if __name__ == "__main__":
    unittest.main(verbosity=2)
