#!/usr/bin/env python3
"""Tests for tools/unit_breaker.py (P1.2): the circuit breaker wrapped around ExecStart of every agent starter.

D-1173: a watcher restarted agents for hours with no stop. The breaker counts identical consecutive failures
(exit code + sha1 of the last stderr line, digits normalised) and after N of them disables the unit.
Everything is fake: state under a tmp XDG_STATE_HOME, an injected systemctl runner (or a fake `systemctl` on
PATH for the subprocess cases). No real unit is touched.

Run directly:  python3 tests/test_unit_breaker.py
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "unit_breaker.py"
sys.path.insert(0, str(ROOT / "tools"))

import unit_breaker as ub  # noqa: E402

PY = sys.executable


def failing(code, line):
    """argv for a command that prints `line` to stderr and exits `code`."""
    return [PY, "-c", "import sys; sys.stderr.write('noise\\n%s\\n\\n'); sys.exit(%d)" % (line, code)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.inbox = self.root / "alerts.md"
        self.calls = []
        self.env = {"XDG_STATE_HOME": str(self.state), "AUTOOS_ALERT_INBOX": str(self.inbox)}

    def runner(self, argv):
        self.calls.append(list(argv))
        return 0

    def run_breaker(self, argv, env=None, runner=None):
        env = dict(self.env if env is None else env)
        return ub.main(argv, env=env, runner=runner or self.runner)

    def wrap(self, unit, cmd, threshold=3, **kw):
        return self.run_breaker(["--unit", unit, "--threshold", str(threshold), "--"] + cmd, **kw)

    def state_file(self, unit):
        return self.state / "autoos" / "breaker" / (unit + ".json")

    def alerts(self):
        return self.inbox.read_text().splitlines() if self.inbox.exists() else []


class TestWrapper(Base):
    def test_exit_code_passes_through(self):
        self.assertEqual(self.wrap("u.service", [PY, "-c", "pass"]), 0)
        self.assertEqual(self.wrap("u.service", failing(7, "boom")), 7)

    def test_identical_failures_trip_after_threshold(self):
        for i in range(2):
            self.assertEqual(self.wrap("w.service", failing(3, "no route to host 10.0.0.%d" % i)), 3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.wrap("w.service", failing(3, "no route to host 10.0.0.9")), 3)
        self.assertEqual(self.calls, [["systemctl", "--user", "disable", "--now", "w.service"]])
        lines = self.alerts()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^\S+ BREAKER w\.service tripped after 3 x [0-9a-f]{6,12}: no route to host")

    def test_digits_are_normalised_in_the_signature(self):
        self.assertEqual(ub.signature(1, "pid 123 died at 10:22"), ub.signature(1, "pid 9 died at 3:4"))
        self.assertNotEqual(ub.signature(1, "pid 123 died"), ub.signature(2, "pid 123 died"))
        self.assertNotEqual(ub.signature(1, "pid 123 died"), ub.signature(1, "other"))

    def test_hex_ids_and_uuids_are_normalised_too(self):
        a = ub.signature(1, "crash at 0x7f3a9c10 id 0f8e6c1a-1111-2222-3333-444455556666")
        b = ub.signature(1, "crash at 0x7f3a9d99 id 9a8e6c1a-1111-2222-3333-444455550000")
        self.assertEqual(a, b)

    def test_different_signature_restarts_the_count(self):
        self.wrap("w.service", failing(3, "a"))
        self.wrap("w.service", failing(3, "a"))
        self.wrap("w.service", failing(3, "b"))
        self.wrap("w.service", failing(3, "a"))
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads(self.state_file("w.service").read_text())["count"], 1)

    def test_success_resets_the_counter(self):
        self.wrap("w.service", failing(3, "a"))
        self.wrap("w.service", failing(3, "a"))
        self.wrap("w.service", [PY, "-c", "pass"])
        self.wrap("w.service", failing(3, "a"))
        self.assertEqual(self.calls, [])

    def test_one_alert_per_trip_and_tripped_unit_refuses_to_run(self):
        for _ in range(3):
            self.wrap("w.service", failing(3, "a"))
        marker = self.root / "ran"
        rc = self.wrap("w.service", [PY, "-c", "open(%r,'w').close()" % str(marker)])
        self.assertNotEqual(rc, 0)
        self.assertFalse(marker.exists(), "a tripped unit must not run its command")
        self.assertEqual(len(self.alerts()), 1)
        self.assertEqual(len(self.calls), 1, "disable --now is issued once per trip")

    def test_reset_clears_the_trip_and_a_new_trip_alerts_again(self):
        for _ in range(3):
            self.wrap("w.service", failing(3, "a"))
        self.assertEqual(self.run_breaker(["reset", "w.service"]), 0)
        self.assertEqual(self.wrap("w.service", [PY, "-c", "pass"]), 0)
        for _ in range(3):
            self.wrap("w.service", failing(3, "a"))
        self.assertEqual(len(self.alerts()), 2)

    def test_trip_is_persisted_and_alerted_before_disable_runs(self):
        """`disable --now` kills the wrapper itself; state and alert must already be on disk."""
        seen = {}

        def runner(argv):
            seen["state"] = json.loads(self.state_file("w.service").read_text())
            seen["alerts"] = list(self.alerts())
            return 0
        for _ in range(3):
            self.wrap("w.service", failing(3, "a"), runner=runner)
        self.assertTrue(seen["state"]["tripped"])
        self.assertEqual(len(seen["alerts"]), 1)

    def test_signal_stop_is_not_a_failure(self):
        for _ in range(5):
            self.wrap("w.service", [PY, "-c", "import sys; sys.exit(143)"])
        self.assertEqual(self.calls, [])

    def test_no_systemctl_writes_a_tripped_marker_and_exits_nonzero(self):
        def missing(argv):
            raise FileNotFoundError("systemctl")
        for _ in range(3):
            rc = self.wrap("w.service", failing(4, "x"), runner=missing)
        self.assertNotEqual(rc, 0)
        self.assertTrue((self.state / "autoos" / "breaker" / "w.service.tripped").exists())
        self.assertEqual(len(self.alerts()), 1)

    def test_no_alert_inbox_goes_to_stderr_only(self):
        env = {"XDG_STATE_HOME": str(self.state)}
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                self.wrap("w.service", failing(3, "a"), env=env)
        self.assertIn("BREAKER w.service tripped after 3 x", err.getvalue())
        self.assertFalse(self.inbox.exists())

    def test_alert_line_is_one_line_capped_at_200_chars(self):
        for _ in range(3):
            self.wrap("w.service", failing(3, "z" * 500))
        line = self.alerts()[0]
        self.assertEqual(len(self.alerts()), 1)
        self.assertLessEqual(len(line.split(": ", 1)[1]), 200)

    def test_status_lines(self):
        self.wrap("ok.service", [PY, "-c", "pass"])
        self.wrap("fail.service", failing(3, "a"))
        for _ in range(3):
            self.wrap("trip.service", failing(3, "a"))
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.run_breaker(["status"]), 0)
        got = {ln.split()[0]: ln for ln in out.getvalue().splitlines()}
        self.assertIn("state=ok", got["ok.service"])
        self.assertIn("state=failing", got["fail.service"])
        self.assertIn("count=1", got["fail.service"])
        self.assertIn("state=tripped", got["trip.service"])
        self.assertIn("count=3", got["trip.service"])

    def test_state_write_is_atomic_json(self):
        self.wrap("w.service", failing(3, "a"))
        data = json.loads(self.state_file("w.service").read_text())
        self.assertEqual(data["count"], 1)
        self.assertEqual(list(self.state_file("w.service").parent.glob("*.tmp*")), [])


class TestCli(Base):
    @unittest.skipIf(os.name == "nt", "the fake systemctl is a bash script; systemd is Linux only")
    def test_real_systemctl_on_path_is_called_with_disable_now(self):
        fake = self.root / "bin"
        fake.mkdir()
        log = self.root / "sc.log"
        sc = fake / "systemctl"
        sc.write_text('#!/usr/bin/env bash\necho "$*" >> "%s"\n' % log)
        sc.chmod(sc.stat().st_mode | stat.S_IXUSR)
        env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"], **self.env)
        for _ in range(3):
            p = subprocess.run([PY, str(TOOL), "--unit", "c.service", "--threshold", "3", "--"] + failing(9, "q"),
                               env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 9)
        self.assertEqual(log.read_text().strip(), "--user disable --now c.service")


class TestNtBranch(Base):
    """Windows branch tests (run on Linux by forcing AUTOOS_OS_NAME=nt)."""
    def setUp(self):
        super().setUp()
        self.env["AUTOOS_OS_NAME"] = "nt"

    def wrap(self, unit, cmd, threshold=5, **kw):
        return self.run_breaker(["--unit", unit, "--threshold", str(threshold), "--"] + cmd, **kw)

    # --- state directory on nt ---
    def test_state_dir_uses_localappdata_when_xdg_unset(self):
        env = {"AUTOOS_OS_NAME": "nt", "LOCALAPPDATA": str(self.root / "localappdata")}
        d = ub.state_dir(env)
        self.assertEqual(d, Path(self.root / "localappdata") / "autoos" / "breaker")

    def test_state_dir_falls_back_to_appdata_local_when_localappdata_unset(self):
        env = {"AUTOOS_OS_NAME": "nt"}
        d = ub.state_dir(env)
        self.assertEqual(d, Path.home() / "AppData" / "Local" / "autoos" / "breaker")

    def test_state_dir_prefers_xdg_when_set(self):
        env = {"AUTOOS_OS_NAME": "nt", "XDG_STATE_HOME": str(self.root / "xdg")}
        d = ub.state_dir(env)
        self.assertEqual(d, Path(self.root / "xdg") / "autoos" / "breaker")

    # --- trip() on nt uses schtasks, never systemctl ---
    def test_trip_on_nt_runs_schtasks_end_then_change_disable(self):
        for _ in range(4):
            self.wrap("w.service", failing(3, "boom"))
        self.assertEqual(self.calls, [])  # not yet
        self.assertEqual(self.wrap("w.service", failing(3, "boom")), 3)
        self.assertEqual(self.calls, [
            ["schtasks", "/End", "/TN", "w"],
            ["schtasks", "/Change", "/TN", "w", "/DISABLE"],
        ])

    def test_trip_on_nt_never_calls_systemctl(self):
        for _ in range(5):
            self.wrap("w.service", failing(3, "boom"))
        for argv in self.calls:
            self.assertNotIn("systemctl", argv)

    def test_task_name_strips_trailing_service(self):
        # unit is "w.service" -> task name "w"
        self.wrap("w.service", failing(3, "boom"))
        self.assertEqual(self.calls, [])  # not yet tripped (threshold 5)

    def test_trip_on_nt_still_writes_marker_and_one_alert_when_schtasks_fails(self):
        def failing_runner(argv):
            self.calls.append(list(argv))
            raise FileNotFoundError("schtasks")
        for _ in range(4):
            self.wrap("w.service", failing(3, "boom"), runner=failing_runner)
        self.assertEqual(self.calls, [])  # not yet
        rc = self.wrap("w.service", failing(3, "boom"), runner=failing_runner)
        self.assertNotEqual(rc, 0)
        self.assertTrue((self.state / "autoos" / "breaker" / "w.service.tripped").exists())
        self.assertEqual(len(self.alerts()), 1)
        # it tried the two schtasks commands
        self.assertEqual(self.calls, [
            ["schtasks", "/End", "/TN", "w"],
            ["schtasks", "/Change", "/TN", "w", "/DISABLE"],
        ])

    # --- failure class table ---
    def test_class_startup(self):
        for rc in (2, 3):
            self.assertEqual(ub.failure_class(rc, "anything"), "startup")

    def test_class_io(self):
        for line in ("OSError: [Errno 2] No such file",
                     "WinError 5 Access is denied",
                     "PermissionError: [Errno 13] Permission denied"):
            self.assertEqual(ub.failure_class(1, line), "io")

    def test_class_memory(self):
        self.assertEqual(ub.failure_class(1, "MemoryError"), "memory")
        self.assertEqual(ub.failure_class(1, "mem floor unreadable"), "memory")

    def test_class_crash(self):
        self.assertEqual(ub.failure_class(1, "Traceback (most recent call last):"), "crash")
        self.assertEqual(ub.failure_class(1, "Unhandled exception:"), "crash")
        self.assertEqual(ub.failure_class(-11, ""), "crash")   # rc < 0
        self.assertEqual(ub.failure_class(139, ""), "crash")   # rc >= 128 and not 130/143

    def test_class_other(self):
        self.assertEqual(ub.failure_class(1, "some random error"), "other")

    def test_stop_codes_neutral(self):
        # 130/143 are stop codes, not failures -> class is "stop" (or None) and not counted
        for rc in (130, 143):
            cls = ub.failure_class(rc, "whatever")
            self.assertNotIn(cls, ("startup", "io", "memory", "crash", "other"))

    # --- 5 consecutive same-class failures trip, no time window ---
    def test_five_same_class_failures_trip(self):
        for i in range(4):
            self.assertEqual(self.wrap("w.service", failing(1, "random err %d" % i)), 1)
        self.assertEqual(self.calls, [])  # not yet
        self.assertEqual(self.wrap("w.service", failing(1, "another err")), 1)
        self.assertEqual(self.calls, [
            ["schtasks", "/End", "/TN", "w"],
            ["schtasks", "/Change", "/TN", "w", "/DISABLE"],
        ])

    def test_success_resets_on_nt(self):
        for _ in range(4):
            self.wrap("w.service", failing(1, "same class"))
        self.wrap("w.service", [PY, "-c", "pass"])  # success resets
        self.wrap("w.service", failing(1, "same class"))
        self.assertEqual(self.calls, [])  # no trip after reset

    def test_different_class_resets_on_nt(self):
        for _ in range(4):
            self.wrap("w.service", failing(2, "startup failure"))  # class startup
        self.wrap("w.service", failing(1, "random"))  # different class -> reset
        self.wrap("w.service", failing(1, "random"))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
