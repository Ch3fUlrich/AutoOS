"""Tests for tools/hostexec/audit.py (spec status/L1-backlog.spec-host-shell.md
section 5). Pure stdlib, no I/O beyond a temp dir. Written before the
implementation (coding-principles 2).

Run from anywhere:

    python3 tests/test_hostexec_log.py
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
CLI = TOOLS / "hostexec.py"
sys.path.insert(0, str(TOOLS))

from hostexec import audit  # noqa: E402

_ALL_FIELDS = {
    "ts", "seq", "actor", "session", "via", "host", "argv", "argv_sha256",
    "cwd", "run_as", "decision", "rule", "reason", "exit", "duration_ms",
    "out_bytes", "truncated",
}


def _fixed_now(dt: datetime.datetime):
    return lambda: dt


def _read_lines(path) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh if ln.strip()]


class RedactionTests(unittest.TestCase):
    def test_bearer_token_is_masked(self):
        self.assertEqual(audit.redact_argv(["curl", "-H", "Bearer", "sekrit-value"]),
                          ["curl", "-H", "Bearer", "***"])

    def test_key_equals_value_is_masked(self):
        self.assertEqual(audit.redact_argv(["tool", "API_KEY=abc123"]),
                          ["tool", "API_KEY=***"])

    def test_token_flag_with_separate_value_is_masked(self):
        self.assertEqual(audit.redact_argv(["tool", "--token", "abc123"]),
                          ["tool", "--token", "***"])

    def test_non_secret_assignment_is_untouched(self):
        self.assertEqual(audit.redact_argv(["env", "FOO=bar"]), ["env", "FOO=bar"])

    def test_mysql_style_dash_p_password_is_masked(self):
        # review F12: -pSECRET (no space) is a common credential form.
        self.assertEqual(audit.redact_argv(["mysql", "-uroot", "-phunter2"]),
                          ["mysql", "-uroot", "-p***"])

    def test_password_equals_flag_is_masked(self):
        self.assertEqual(audit.redact_argv(["tool", "--password=hunter2"]),
                          ["tool", "--password=***"])

    def test_dash_u_user_colon_pass_is_masked(self):
        self.assertEqual(audit.redact_argv(["curl", "-u", "user:hunter2"]),
                          ["curl", "-u", "***"])

    def test_argv_sha256_is_over_the_raw_unredacted_form(self):
        raw = ["tool", "--token", "abc123"]
        redacted_first = audit.redact_argv(raw)
        h1 = audit.hash_argv(raw)
        # a different secret value with the same shape hashes differently:
        # the hash must come from the RAW argv, not the (identical) redacted form
        h2 = audit.hash_argv(["tool", "--token", "xyz999"])
        self.assertNotEqual(h1, h2)
        self.assertEqual(audit.redact_argv(["tool", "--token", "xyz999"]), redacted_first)


class SanitizeTests(unittest.TestCase):
    def test_control_chars_are_stripped_but_tab_kept(self):
        s = "hello\x1b[31mworld\x07\tend\x0d\x0a"
        self.assertEqual(audit.sanitize_text(s), "hello[31mworld\tend")


class AuditLogWriteTests(unittest.TestCase):
    def test_write_produces_a_line_with_every_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = audit.AuditLog(os.path.join(tmp, "state"), now=_fixed_now(
                datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.timezone.utc)))
            log.write(actor="claude", session="s1", via="http", host="coding-host",
                      argv=["echo", "hi"], cwd="/tmp", run_as="claude",
                      decision="allow", rule=None, reason=None,
                      exit_code=0, duration_ms=5, out_bytes=3, truncated=False)
            log.close()
            path = os.path.join(tmp, "state", "audit-2026-09-26.jsonl")
            lines = [json.loads(ln) for ln in _read_lines(path)]
            data_lines = [ln for ln in lines if ln.get("event") != "start"]
            self.assertEqual(len(data_lines), 1)
            self.assertEqual(set(data_lines[0]), _ALL_FIELDS)
            self.assertEqual(data_lines[0]["exit"], 0)

    def test_deny_line_has_exit_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = audit.AuditLog(os.path.join(tmp, "state"))
            log.write(actor="claude", session=None, via="http", host="coding-host",
                      argv=["sudo", "id"], cwd="/tmp", run_as="claude",
                      decision="deny", rule="no-sudo", reason=None,
                      exit_code=None, duration_ms=None, out_bytes=None, truncated=False)
            log.close()
            files = list(Path(tmp, "state").glob("audit-*.jsonl"))
            self.assertEqual(len(files), 1)
            lines = [json.loads(ln) for ln in _read_lines(files[0])]
            data_lines = [ln for ln in lines if ln.get("event") != "start"]
            self.assertEqual(data_lines[0]["exit"], None)
            self.assertEqual(data_lines[0]["decision"], "deny")
            self.assertEqual(data_lines[0]["rule"], "no-sudo")

    def test_embedded_newline_and_quote_injection_stays_one_json_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = audit.AuditLog(os.path.join(tmp, "state"))
            evil_reason = 'legit"}\n{"seq":999,"decision":"allow"\ninjected\x1b[2J'
            log.write(actor="claude", session=None, via="http", host="coding-host",
                      argv=["echo", "hi"], cwd="/tmp", run_as="claude",
                      decision="allow", rule=None, reason=evil_reason,
                      exit_code=0, duration_ms=1, out_bytes=0, truncated=False)
            log.close()
            files = list(Path(tmp, "state").glob("audit-*.jsonl"))
            raw_lines = _read_lines(files[0])
            # start record + one data record, nothing more -- the embedded
            # \n in `reason` must not have produced extra "lines" on disk.
            self.assertEqual(len(raw_lines), 2)
            for ln in raw_lines:
                json.loads(ln)  # every physical line is exactly one JSON object

    def test_seq_increments_within_one_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = audit.AuditLog(os.path.join(tmp, "state"))
            seqs = []
            for _ in range(3):
                seqs.append(log.write(actor="claude", session=None, via="http",
                                       host="coding-host", argv=["echo"], cwd="/tmp",
                                       run_as="claude", decision="allow", rule=None,
                                       reason=None, exit_code=0, duration_ms=1,
                                       out_bytes=0, truncated=False))
            log.close()
            self.assertEqual(seqs, sorted(seqs))
            self.assertEqual(len(set(seqs)), 3)

    def test_seq_resumes_across_a_restart_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "state")
            now = _fixed_now(datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.timezone.utc))
            log1 = audit.AuditLog(state_dir, now=now)
            last1 = log1.write(actor="claude", session=None, via="http", host="coding-host",
                                argv=["echo"], cwd="/tmp", run_as="claude", decision="allow",
                                rule=None, reason=None, exit_code=0, duration_ms=1,
                                out_bytes=0, truncated=False)
            log1.close()
            log2 = audit.AuditLog(state_dir, now=now)  # simulates a broker restart
            first2 = log2.write(actor="claude", session=None, via="http", host="coding-host",
                                 argv=["echo"], cwd="/tmp", run_as="claude", decision="allow",
                                 rule=None, reason=None, exit_code=0, duration_ms=1,
                                 out_bytes=0, truncated=False)
            log2.close()
            self.assertGreater(first2, last1, "seq must not restart at 1 after a restart")

    def test_start_record_has_policy_hash_pid_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = audit.AuditLog(os.path.join(tmp, "state"), policy_sha256="deadbeef", version="1.2.3")
            log.preflight()
            log.close()
            files = list(Path(tmp, "state").glob("audit-*.jsonl"))
            lines = [json.loads(ln) for ln in _read_lines(files[0])]
            starts = [ln for ln in lines if ln.get("event") == "start"]
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0]["policy_sha256"], "deadbeef")
            self.assertEqual(starts[0]["version"], "1.2.3")
            self.assertEqual(starts[0]["pid"], os.getpid())

    def test_journald_is_called_with_the_line_when_injected(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            log = audit.AuditLog(os.path.join(tmp, "state"), journald=seen.append)
            log.write(actor="claude", session=None, via="http", host="coding-host",
                      argv=["echo"], cwd="/tmp", run_as="claude", decision="allow",
                      rule=None, reason=None, exit_code=0, duration_ms=1,
                      out_bytes=0, truncated=False)
            log.close()
        self.assertGreaterEqual(len(seen), 2)  # start + the write
        self.assertIn('"decision": "allow"', seen[-1])

    def test_dir_and_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "state")
            log = audit.AuditLog(state_dir)
            log.preflight()
            log.close()
            self.assertEqual(os.stat(state_dir).st_mode & 0o777, 0o700)
            f = next(Path(state_dir).glob("audit-*.jsonl"))
            self.assertEqual(os.stat(f).st_mode & 0o777, 0o600)

    def test_fail_closed_on_unwritable_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = os.path.join(tmp, "locked")
            os.makedirs(parent)
            os.chmod(parent, 0o500)  # r-x: mkdir underneath must fail
            try:
                log = audit.AuditLog(os.path.join(parent, "state"))
                with self.assertRaises(audit.AuditWriteError):
                    log.preflight()
            finally:
                os.chmod(parent, 0o700)

    def test_concurrent_writers_never_interleave(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "state")
            n_threads, n_per_thread = 6, 20

            def worker(i):
                log = audit.AuditLog(state_dir)
                for j in range(n_per_thread):
                    log.write(actor=f"actor{i}", session=None, via="http", host="coding-host",
                               argv=["echo", str(j)], cwd="/tmp", run_as="claude",
                               decision="allow", rule=None, reason=None,
                               exit_code=0, duration_ms=1, out_bytes=0, truncated=False)
                log.close()

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            files = list(Path(state_dir).glob("audit-*.jsonl"))
            self.assertEqual(len(files), 1)
            lines = _read_lines(files[0])
            for ln in lines:
                json.loads(ln)  # every line parses -- no interleaved half-lines
            data_lines = [json.loads(ln) for ln in lines]
            data_lines = [d for d in data_lines if d.get("event") != "start"]
            self.assertEqual(len(data_lines), n_threads * n_per_thread)
            self.assertEqual(len({d["seq"] for d in data_lines}), len(data_lines),
                              "seq values collided across concurrent writers")


class SinceParseTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(audit.parse_since("30s"), 30)
        self.assertEqual(audit.parse_since("1m"), 60)
        self.assertEqual(audit.parse_since("2h"), 7200)
        self.assertEqual(audit.parse_since("1d"), 86400)

    def test_bad_value_raises(self):
        with self.assertRaises(ValueError):
            audit.parse_since("soon")


class TailFilterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = os.path.join(self._tmp.name, "state")
        self.log = audit.AuditLog(self.state_dir)
        self.log.write(actor="claude", session=None, via="http", host="coding-host",
                        argv=["echo", "1"], cwd="/tmp", run_as="claude", decision="allow",
                        rule=None, reason=None, exit_code=0, duration_ms=1,
                        out_bytes=0, truncated=False)
        self.log.write(actor="openhands", session=None, via="http", host="coding-host",
                        argv=["sudo", "id"], cwd="/tmp", run_as="openhands", decision="deny",
                        rule="no-sudo", reason=None, exit_code=None, duration_ms=None,
                        out_bytes=None, truncated=False)
        self.log.close()

    def test_filter_by_actor(self):
        lines = list(audit.tail(self.state_dir, actor="openhands"))
        self.assertTrue(all("actor=openhands" in ln for ln in lines))
        self.assertTrue(any("no-sudo" in ln for ln in lines))

    def test_filter_by_decision(self):
        lines = list(audit.tail(self.state_dir, decision="deny"))
        self.assertTrue(all("decision=deny" in ln for ln in lines))

    def test_no_filter_returns_both_plus_start(self):
        lines = list(audit.tail(self.state_dir))
        self.assertTrue(any("actor=claude" in ln for ln in lines))
        self.assertTrue(any("actor=openhands" in ln for ln in lines))


class PruneTests(unittest.TestCase):
    def test_prune_deletes_by_name_date_not_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "state")
            os.makedirs(state_dir)
            today = datetime.date(2026, 9, 26)
            old_name = os.path.join(state_dir, "audit-2026-01-01.jsonl")
            recent_name = os.path.join(state_dir, "audit-2026-09-20.jsonl")
            with open(old_name, "w", encoding="utf-8") as fh:
                fh.write("{}\n")
            with open(recent_name, "w", encoding="utf-8") as fh:
                fh.write("{}\n")
            # make the OLD-named file look freshly touched -- prune must
            # still delete it, because it keys on the filename, not mtime.
            now_ts = datetime.datetime.now().timestamp()
            os.utime(old_name, (now_ts, now_ts))
            deleted = audit.prune(state_dir, days=90, today=today)
            self.assertEqual(deleted, [old_name])
            self.assertFalse(os.path.exists(old_name))
            self.assertTrue(os.path.exists(recent_name))


class CliLogTests(unittest.TestCase):
    def test_log_subcommand_reads_the_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "state")
            log = audit.AuditLog(state_dir)
            log.write(actor="claude", session=None, via="http", host="coding-host",
                      argv=["echo", "1"], cwd="/tmp", run_as="claude", decision="allow",
                      rule=None, reason=None, exit_code=0, duration_ms=1,
                      out_bytes=0, truncated=False)
            log.close()
            out = subprocess.run([sys.executable, str(CLI), "log", "--dir", state_dir],
                                  capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("actor=claude", out.stdout)

    def test_log_subcommand_filters_by_decision(self):
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
            out = subprocess.run([sys.executable, str(CLI), "log", "--dir", state_dir,
                                   "--decision", "deny"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("no-sudo", out.stdout)
            self.assertNotIn("actor=claude ", out.stdout)


if __name__ == "__main__":
    unittest.main()
