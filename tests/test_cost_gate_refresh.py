#!/usr/bin/env python3
"""Tests for tools/cost-gate-refresh.py and tools/cost-gate-status.py.

Every subprocess call is an injected `run` mock, so no gateway and no
real run_budget run happens here. Run with:

    python tests/test_cost_gate_refresh.py
"""

import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
import contextlib
from pathlib import PurePosixPath
from unittest import mock
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
for _p in (str(TOOLS), str(REPO / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


refresh = _load("cost_gate_refresh", "cost-gate-refresh.py")
status = _load("cost_gate_status", "cost-gate-status.py")

FIXED_NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
PINNED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def make_doc(**over):
    d = {"day": "2026-10-03", "usd": 1.5, "by_provider": {"vertex": 1.5},
         "verdict": "ok", "unverified": False, "budget": 25.0,
         "bad_rows": 0, "unpriced_models": [{"model": "m", "count": 2}],
         "unpriced_default_used": True, "truncated": False}
    d.update(over)
    return d


class FakeCompleted:
    def __init__(self, returncode=0, bstdout=b""):
        self.returncode, self.stdout, self.stderr = returncode, bstdout, b""


def recorder(make):
    """Injectable `run`: records argv, returns FakeCompleted. Fails the
    test if --gateway appears alongside --rows (a real gateway call)."""
    calls = []

    def fake_run(cmd, **kwargs):
        args = list(cmd)
        if "--rows" in args and "--gateway" in args:
            raise AssertionError("both --rows and --gateway in one run_budget call")
        calls.append(args)
        return make(args)

    fake_run.calls = calls
    return fake_run


def write_gate_file(td, content=b'{"old": true}\n'):
    (td / "daily-gate.json").write_bytes(content)


class TestPaths(unittest.TestCase):
    def test_state_dir_env_override(self):
        if os.name == "nt":
            env, want = {"LOCALAPPDATA": "/x"}, Path("/x/autoos")
        else:
            env, want = {"XDG_STATE_HOME": "/x"}, Path("/x/autoos")
        self.assertEqual(refresh.state_dir(env), want)
        self.assertEqual(status.state_dir(env), want)

    def test_config_path_env_override(self):
        if os.name == "nt":
            env, want = {"APPDATA": "/y"}, Path("/y/autoos/daily-gate.conf")
        else:
            env, want = {"XDG_CONFIG_HOME": "/y"}, Path("/y/autoos/daily-gate.conf")
        self.assertEqual(refresh.config_path(env), want)
        self.assertTrue(refresh.config_path({}).is_absolute())


class TestReadConfig(unittest.TestCase):
    def cfg(self, content):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "daily-gate.conf"
            if content is not None:
                p.write_bytes(content)
            return refresh.read_config(p)

    def test_absent_file_gives_defaults(self):
        self.assertEqual(self.cfg(None), (20.0, 25.0, None))

    def test_valid_file_gives_its_values(self):
        self.assertEqual(self.cfg(b"warn=20.5\nblock=26.0\n"), (20.5, 26.0, None))

    def test_valid_file_with_blank_lines(self):
        self.assertEqual(self.cfg(b"\nwarn=12\n\nblock=15\n"), (12.0, 15.0, None))

    def test_ignored_cases_fall_back_to_defaults(self):
        cases = {
            "unknown key": b"warn=10\nblock=20\nfoo=1\n",
            "garbage": b"warn=10\nblock=20\nnot a setting line\n",
            "non-numeric": b"warn=abc\nblock=25\n",
            "missing key": b"warn=10\n",
            "warn == block": b"warn=25\nblock=25\n",
            "warn > block": b"warn=30\nblock=25\n",
            "negative warn": b"warn=-1\nblock=25\n",
            "duplicate key": b"warn=10\nwarn=11\nblock=20\n",
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                w, b, note = self.cfg(content)
                self.assertEqual((w, b), (20.0, 25.0), content)
                self.assertTrue(note, content)

    def test_unreadable_never_crashes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "daily-gate.conf"
            p.write_bytes(b"warn=10\nblock=20\n")
            real = Path.read_bytes
            Path.read_bytes = lambda self: (_ for _ in ()).throw(PermissionError(13, "denied"))
            try:
                w, b, note = refresh.read_config(p)
            finally:
                Path.read_bytes = real
            self.assertEqual((w, b), (20.0, 25.0))
            self.assertTrue(note)

    def test_invalid_utf8_gives_defaults_with_note(self):
        self.assertEqual(self.cfg(b"warn=10\nblock=20\n\xff\xfe"),
                         (20.0, 25.0, "config is not valid UTF-8"))

    def test_nul_byte_config_gives_defaults_with_note(self):
        self.assertEqual(self.cfg(b"warn=10\0\nblock=20\n"),
                         (20.0, 25.0, "config contains NUL bytes"))


class TestBuildStatus(unittest.TestCase):
    LINE_RE = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z verdict=\S+ usd=\S+ budget=\S+ "
        r"truncated=(?:true|false) unpriced=\d+ unpriced_default_used=(?:true|false) "
        r"bad_rows=\d+(?: ; daily gate config ignored: .+)?$")

    def test_field_order(self):
        line = refresh.build_status(make_doc())
        self.assertIsNotNone(self.LINE_RE.match(line), line)
        self.assertIn(" verdict=ok usd=1.5 budget=25.0 truncated=false unpriced=2 "
                      "unpriced_default_used=true bad_rows=0", line)

    def test_truncated_and_bad_rows(self):
        self.assertIn("truncated=true", refresh.build_status(make_doc(truncated=True)))
        self.assertTrue(refresh.build_status(make_doc(bad_rows=4)).endswith("bad_rows=4"))

    def test_note_appended(self):
        line = refresh.build_status(make_doc(), "warn >= block")
        self.assertTrue(line.endswith(" ; daily gate config ignored: warn >= block"))

    def test_invalid_doc_is_unavailable(self):
        self.assertIn(" UNAVAILABLE: ", refresh.build_status({"verdict": "ok"}))
        self.assertIn(" UNAVAILABLE: ", refresh.build_status(make_doc(usd="x")))
        self.assertIn(" UNAVAILABLE: ", refresh.build_status([1, 2, 3]))

    def test_injected_now_stamps_the_line(self):
        ok = refresh.build_status(make_doc(), None, now=PINNED_NOW)
        bad = refresh.build_status({"no": "fields"}, None, now=PINNED_NOW)
        self.assertTrue(ok.startswith("2026-01-02T03:04:05Z "), ok)
        self.assertTrue(bad.startswith("2026-01-02T03:04:05Z UNAVAILABLE: "), bad)


class TestRefreshConfigPassing(unittest.TestCase):
    def run_refresh(self, td, make, extra=None, now=FIXED_NOW):
        argv = ["--gateway", "--state-dir", str(td),
                "--config", str(td / "absent.conf")] + (extra or [])
        run = recorder(make)
        return refresh.main(argv, run=run, now=now), run.calls

    def make_ok(self, args):
        return FakeCompleted(0, json.dumps(make_doc()).encode())

    def test_config_absent_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            rc, calls = self.run_refresh(Path(td), self.make_ok)
            self.assertEqual(rc, 0)
            cmd = calls[0]
            self.assertEqual(cmd[cmd.index("--warn") + 1], "20.0")
            self.assertEqual(cmd[cmd.index("--budget") + 1], "25.0")

    def test_config_present_uses_its_values(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "daily-gate.conf").write_bytes(b"warn=12.5\nblock=18\n")
            rc, calls = self.run_refresh(td, self.make_ok,
                                         ["--config", str(td / "daily-gate.conf")])
            self.assertEqual(rc, 0)
            cmd = calls[0]
            self.assertEqual(cmd[cmd.index("--warn") + 1], "12.5")
            self.assertEqual(cmd[cmd.index("--budget") + 1], "18.0")

    def test_garbage_config_defaults_and_note_on_status(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "daily-gate.conf").write_bytes(b"warn=10\nblock=20\nbogus=1\n")
            rc, calls = self.run_refresh(td, self.make_ok,
                                         ["--config", str(td / "daily-gate.conf")])
            self.assertEqual(rc, 0)
            cmd = calls[0]
            self.assertEqual(cmd[cmd.index("--warn") + 1], "20.0")
            self.assertEqual(cmd[cmd.index("--budget") + 1], "25.0")
            line = (td / "daily-gate.status").read_text().rstrip("\n")
            self.assertTrue(line.endswith("; daily gate config ignored: unknown key"), line)

    def test_warn_ge_block_uses_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "daily-gate.conf").write_bytes(b"warn=30\nblock=25\n")
            rc, calls = self.run_refresh(td, self.make_ok,
                                         ["--config", str(td / "daily-gate.conf")])
            self.assertEqual(rc, 0)
            cmd = calls[0]
            self.assertEqual(cmd[cmd.index("--warn") + 1], "20.0")
            line = (td / "daily-gate.status").read_text().rstrip("\n")
            self.assertIn("; daily gate config ignored: ", line)


class TestRefreshSuccessAtomic(unittest.TestCase):
    def test_success_writes_gate_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            write_gate_file(td)
            doc = make_doc()
            rc = refresh.main(["--gateway", "--state-dir", str(td),
                               "--config", str(td / "absent.conf")],
                              run=recorder(lambda a: FakeCompleted(0,
                                                                   json.dumps(doc).encode())),
                              now=FIXED_NOW)
            self.assertEqual(rc, 0)
            raw = (td / "daily-gate.json").read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "gate file has a BOM")
            self.assertEqual(json.loads(raw.decode("utf-8")), doc)
            self.assertEqual([p.name for p in td.iterdir() if p.name.endswith(".tmp")], [])
            line = (td / "daily-gate.status").read_text()
            self.assertEqual(line.count("\n"), 1)
            self.assertIn("verdict=ok", line)

    def test_replace_failure_keeps_old_gate(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            old = b'{"old": true}\n'
            write_gate_file(td, old)
            real_replace = os.replace

            def no_replace(src, dst):
                if str(dst).endswith("daily-gate.json"):
                    raise OSError("disk full")
                return real_replace(src, dst)

            os.replace = no_replace
            try:
                rc = refresh.main(["--gateway", "--state-dir", str(td),
                                   "--config", str(td / "absent.conf")],
                                  run=recorder(lambda a: FakeCompleted(
                                      0, json.dumps(make_doc()).encode())),
                                  now=FIXED_NOW)
            finally:
                os.replace = real_replace
            self.assertEqual(rc, 3)
            self.assertEqual((td / "daily-gate.json").read_bytes(), old,
                             "old gate file must survive a failed replace")
            self.assertEqual([p.name for p in td.iterdir() if p.name.endswith(".tmp")], [])
            self.assertIn(" UNAVAILABLE: ",
                          (td / "daily-gate.status").read_text().rstrip("\n"))

    def test_status_file_uses_injected_now(self):
        with tempfile.TemporaryDirectory() as td:
            rc = refresh.main(["--gateway", "--state-dir", str(td),
                               "--config", str(Path(td) / "absent.conf")],
                              run=recorder(lambda a: FakeCompleted(
                                  0, json.dumps(make_doc()).encode())),
                              now=PINNED_NOW)
            self.assertEqual(rc, 0)
            line = (Path(td) / "daily-gate.status").read_text().rstrip("\n")
            self.assertTrue(line.startswith("2026-01-02T03:04:05Z "), line)


class TestRefreshExitCodes(unittest.TestCase):
    def run_rc(self, td, make, expect):
        rc = refresh.main(["--gateway", "--state-dir", str(td),
                           "--config", str(Path(td) / "absent.conf")],
                          run=recorder(make), now=FIXED_NOW)
        self.assertEqual(rc, expect)
        return (Path(td) / "daily-gate.status").read_text()

    def test_warn_exit_20_is_success(self):
        with tempfile.TemporaryDirectory() as td:
            line = self.run_rc(td, lambda a: FakeCompleted(
                20, json.dumps(make_doc(verdict="warn")).encode()), 0)
            self.assertIn("verdict=warn", line)

    def test_block_exit_21_is_success(self):
        with tempfile.TemporaryDirectory() as td:
            line = self.run_rc(td, lambda a: FakeCompleted(
                21, json.dumps(make_doc(verdict="block")).encode()), 0)
            self.assertIn("verdict=block", line)

    def test_exit_2_keeps_old_gate(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            old = b'{"old": true}\n'
            write_gate_file(td, old)
            rc = refresh.main(["--gateway", "--state-dir", str(td),
                               "--config", str(td / "absent.conf")],
                              run=recorder(lambda a: FakeCompleted(2, b"")),
                              now=FIXED_NOW)
            self.assertEqual(rc, 3)
            self.assertEqual((td / "daily-gate.json").read_bytes(), old,
                             "old gate file must survive a failed run")
            line = (td / "daily-gate.status").read_text().rstrip("\n")
            self.assertIn(" UNAVAILABLE: ", line)
            self.assertIn("exit 2", line)

    def test_invalid_json_keeps_old_gate(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            old = b'{"old": true}\n'
            write_gate_file(td, old)
            rc = refresh.main(["--gateway", "--state-dir", str(td),
                               "--config", str(td / "absent.conf")],
                              run=recorder(lambda a: FakeCompleted(
                                  0, b"not json at all")),
                              now=FIXED_NOW)
            self.assertEqual(rc, 3)
            self.assertEqual((td / "daily-gate.json").read_bytes(), old)
            line = (td / "daily-gate.status").read_text().rstrip("\n")
            self.assertIn(" UNAVAILABLE: ", line)

    def test_status_line_field_order(self):
        with tempfile.TemporaryDirectory() as td:
            self.run_rc(td, lambda a: FakeCompleted(
                0, json.dumps(make_doc(bad_rows=2, truncated=True)).encode()), 0)
            line = (Path(td) / "daily-gate.status").read_text().rstrip("\n")
            m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) verdict=(\S+) "
                         r"usd=(\S+) budget=(\S+) truncated=(\S+) unpriced=(\S+) "
                         r"unpriced_default_used=(\S+) bad_rows=(\S+)$", line)
            self.assertIsNotNone(m, line)
            self.assertEqual(m.group(2), "ok")
            self.assertEqual(m.group(5), "true")
            self.assertEqual(m.group(8), "2")


class TestNoStateDirDefault(unittest.TestCase):
    def test_no_default_without_platform_dirs(self):
        with mock.patch.object(refresh.os, "name", "nt"):
            self.assertIsNone(refresh.state_dir({}))
            self.assertIsNone(status.state_dir({}))
        with mock.patch.object(refresh.os, "name", "posix"):
            self.assertIsNone(refresh.state_dir({}))

    def test_linux_home_only_state_dir(self):
        # HOME/.local/state/autoos rule; mirrored composition below.
        want = os.path.join("/home/t", ".local", "state") + "/autoos"
        with mock.patch.object(refresh.os, "name", "posix"), \
             mock.patch.object(refresh, "Path", PurePosixPath):
            self.assertEqual(str(refresh.state_dir({"HOME": "/home/t"})), want)
        with mock.patch.object(status.os, "name", "posix"), \
             mock.patch.object(status, "Path", PurePosixPath):
            self.assertEqual(str(status.state_dir({"HOME": "/home/t"})), want)

    def test_refresh_without_state_dir_is_unavailable(self):
        env = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
        with mock.patch.object(refresh, "state_dir", lambda env=None: None), \
             mock.patch.dict(refresh.os.environ, env, clear=True):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                rc = refresh.main(["--gateway"],
                                  run=recorder(lambda a: FakeCompleted(0, b"{}")))
            self.assertEqual(rc, 3)
            self.assertIn("no state dir", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
