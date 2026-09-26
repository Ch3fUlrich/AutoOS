"""Tests for configuration/herdr-sessions/lib/collect_sessions.py -- the UUID
picking bug (proposal doc `L1-backlog.herdr-home-proposal.md` section 2, table
T1-T7).

Pure stdlib, no I/O beyond a temp dir standing in for ~/.claude (CLAUDE_HOME)
and, for the registry cases, a temp dir standing in for /proc. Never touches a
real ~/.claude or /proc. Written test-first (coding-principles 2); style of
tests/test_autoos_track.py. Run from anywhere:

    python3 tests/test_herdr_sessions.py
"""
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "configuration" / "herdr-sessions" / "lib"
sys.path.insert(0, str(LIB))

import collect_sessions as cs  # noqa: E402


def write_transcript(claude_home, cwd, uuid, mtime):
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    d = os.path.join(claude_home, "projects", slug)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, uuid + ".jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"cwd": %r}\n' % cwd)
    os.utime(p, (mtime, mtime))
    return p


def write_job_state(claude_home, job_id, data_or_text):
    d = os.path.join(claude_home, "jobs", job_id)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "state.json")
    with open(p, "w", encoding="utf-8") as fh:
        if isinstance(data_or_text, str):
            fh.write(data_or_text)
        else:
            json.dump(data_or_text, fh)
    return p


def write_session_registry(claude_home, pid, data):
    d = os.path.join(claude_home, "sessions")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s.json" % pid)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return p


def write_proc_stat(proc_root, pid, start_time):
    """A minimal /proc/<pid>/stat: enough fields after the comm's closing ')'
    for registry_uuid()'s field-22 (starttime) read to find real data, since
    comm can itself contain spaces or parens and the kernel format accounts
    for that by making the LAST ')' the reliable split point."""
    d = os.path.join(proc_root, str(pid))
    os.makedirs(d, exist_ok=True)
    fields = ["0"] * 20  # state(3) .. starttime(22): 20 fields after comm
    fields[19] = str(start_time)  # field 22 overall == index 19 here
    with open(os.path.join(d, "stat"), "w", encoding="utf-8") as fh:
        fh.write("%s (claude) %s\n" % (pid, " ".join(fields)))


class HerdrSessionsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self._old_home = os.environ.get("CLAUDE_HOME")
        os.environ["CLAUDE_HOME"] = self.home
        self.now = time.time()

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("CLAUDE_HOME", None)
        else:
            os.environ["CLAUDE_HOME"] = self._old_home
        self._tmp.cleanup()


class ClaudeHomeTests(HerdrSessionsTestCase):
    def test_reads_the_env_seam(self):
        self.assertEqual(cs.claude_home(), self.home)

    def test_defaults_to_dot_claude_under_home(self):
        os.environ.pop("CLAUDE_HOME", None)
        self.assertEqual(cs.claude_home(), os.path.expanduser("~/.claude"))


class T1BgTranscriptExcludedEvenIfNewer(HerdrSessionsTestCase):
    """T1: a bg job's transcript is newer than the interactive pane's own.
    Before the fix, newest_transcript(cwd) picked purely by mtime and
    returned the bg session's uuid (B) -- see the report's pre-fix repro.
    """

    def test_bg_transcript_excluded_even_if_newer(self):
        cwd = "/tmp/w/AutoOS"
        write_transcript(self.home, cwd, "A", self.now - 600)
        write_transcript(self.home, cwd, "B", self.now)
        write_job_state(self.home, "b0000000", {
            "sessionId": "B", "resumeSessionId": "B", "cwd": cwd,
            "template": "bg", "name": "autoos-L1-main", "state": "working"})

        excluded = cs.bg_ids()
        self.assertEqual(excluded, {"B"})

        uuid, source = cs.newest_transcript(cwd, exclude=excluded)
        self.assertEqual((uuid, source), ("A", "newest-transcript"))


class T2JobStateIsNotLiveness(HerdrSessionsTestCase):
    """T2: the job's own `state` field is stopped/done, but the job directory
    is still there. bg_ids() must keep excluding it regardless -- a job's
    `state` is not liveness (it persists across reboots)."""

    def test_stopped_job_id_is_still_excluded(self):
        cwd = "/tmp/w/AutoOS"
        write_transcript(self.home, cwd, "A", self.now - 600)
        write_transcript(self.home, cwd, "B", self.now)
        for state in ("stopped", "done"):
            write_job_state(self.home, "b0000000", {
                "sessionId": "B", "resumeSessionId": "B", "cwd": cwd,
                "template": "bg", "name": "autoos-L1-main", "state": state})
            uuid, source = cs.newest_transcript(cwd, exclude=cs.bg_ids())
            self.assertEqual((uuid, source), ("A", "newest-transcript"),
                              "job state=%r must not re-admit B" % state)


class T3OnlyBgTranscriptsNeverGuess(HerdrSessionsTestCase):
    """T3: every transcript in the directory belongs to a bg session. There
    is nothing safe to guess -- before the fix this returned whichever
    transcript was newest (B); after the fix it must return None with a
    source that says so, not silently pick one."""

    def test_all_transcripts_excluded_returns_none(self):
        cwd = "/tmp/w/AutoOS"
        write_transcript(self.home, cwd, "A", self.now - 600)
        write_transcript(self.home, cwd, "B", self.now)
        write_job_state(self.home, "b0000000", {
            "sessionId": "A", "resumeSessionId": "A", "cwd": cwd,
            "template": "bg", "name": "autoos-other-bg", "state": "working"})
        write_job_state(self.home, "b1111111", {
            "sessionId": "B", "resumeSessionId": "B", "cwd": cwd,
            "template": "bg", "name": "autoos-L1-main", "state": "working"})

        excluded = cs.bg_ids()
        self.assertEqual(excluded, {"A", "B"})
        uuid, source = cs.newest_transcript(cwd, exclude=excluded)
        self.assertEqual((uuid, source), (None, "only-bg-transcripts"))


class T4MalformedStateFilesAreIgnored(HerdrSessionsTestCase):
    """T4: a malformed, empty or keyless jobs/*/state.json must never raise,
    and must not stop a WELL-formed sibling from being picked up."""

    def test_malformed_and_empty_and_keyless_are_ignored_without_exception(self):
        write_job_state(self.home, "malformed", "not json at all")
        write_job_state(self.home, "empty", "")
        write_job_state(self.home, "keyless", {"template": "bg"})
        write_job_state(self.home, "good", {
            "sessionId": "B", "resumeSessionId": "B", "cwd": "/tmp/w/AutoOS",
            "template": "bg", "name": "autoos-L1-main", "state": "working"})

        try:
            excluded = cs.bg_ids()
        except Exception as exc:  # pragma: no cover - the point of the test
            self.fail("bg_ids() raised on a malformed file: %r" % (exc,))
        self.assertEqual(excluded, {"B"})

        cwd = "/tmp/w/AutoOS"
        write_transcript(self.home, cwd, "A", self.now - 600)
        write_transcript(self.home, cwd, "B", self.now)
        uuid, source = cs.newest_transcript(cwd, exclude=excluded)
        self.assertEqual((uuid, source), ("A", "newest-transcript"))


if __name__ == "__main__":
    unittest.main()
