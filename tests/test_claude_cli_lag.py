#!/usr/bin/env python3
"""Unit tests for tools/claude-cli-lag.py (CLIPIN / D-137: latest always, no pin).

No test here makes a network call, reads the real ``~/.claude/settings.json``,
or runs the real ``claude`` binary with a live registry: the registry fetch is
always an injected fake and the version capture runs ``sys.executable`` on a
generated script, so the file passes identically on Linux and Windows.

Run from the repo root:

    python3 tests/test_claude_cli_lag.py
"""
import http.client
import importlib.util
import io
import re
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "claude-cli-lag.py"


def _load_module():
    """Load tools/claude-cli-lag.py by path (a hyphen is not importable)."""
    spec = importlib.util.spec_from_file_location("claude_cli_lag", str(TOOL))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lag = _load_module()


class TestVersionParsing(unittest.TestCase):
    def test_parses_the_real_claude_version_line(self):
        self.assertEqual(lag.parse_version("2.1.284 (Claude Code)"), "2.1.284")

    def test_tolerates_a_leading_v_and_a_prerelease_suffix(self):
        self.assertEqual(lag.parse_version("v2.2.0"), "2.2.0")
        self.assertEqual(lag.parse_version("claude 2.2.0-beta.1 native"), "2.2.0-beta.1")

    def test_no_version_in_text_is_none_not_an_error(self):
        self.assertIsNone(lag.parse_version("command not found"))
        self.assertIsNone(lag.parse_version(""))

    def test_capture_runs_a_command_list_and_reads_stdout(self):
        script = "import sys; sys.stdout.write('2.1.284 (Claude Code)\\n')"
        out, err = lag.capture_output([sys.executable, "-c", script], timeout=20)
        self.assertIn("2.1.284", out)

    def test_capture_failure_is_reported_not_raised(self):
        out, err = lag.capture_output([sys.executable, "-c", "raise SystemExit(3)"],
                                      timeout=20)
        self.assertIsNone(out)

    def test_missing_binary_is_none(self):
        with mock.patch.object(lag, "capture_output", return_value=(None, "no such file")):
            self.assertIsNone(lag.installed_version(["claude", "--version"]))


class TestCompare(unittest.TestCase):
    def test_numeric_release_compare(self):
        self.assertEqual(lag.compare_versions("2.1.284", "2.1.284"), 0)
        self.assertEqual(lag.compare_versions("2.1.284", "2.2.1"), -1)
        self.assertEqual(lag.compare_versions("2.10.0", "2.9.9"), 1)
        self.assertEqual(lag.compare_versions("1.9.0", "1.10.0"), -1)

    def test_prerelease_is_older_than_its_release(self):
        self.assertEqual(lag.compare_versions("2.2.0-beta.1", "2.2.0"), -1)

    def test_assess_maps_the_four_states(self):
        self.assertEqual(lag.assess("2.1.0", "2.2.0"), "lags")
        self.assertEqual(lag.assess("2.2.0", "2.2.0"), "up-to-date")
        self.assertEqual(lag.assess("2.3.0", "2.2.0"), "ahead")
        self.assertEqual(lag.assess(None, "2.2.0"), "unknown")
        self.assertEqual(lag.assess("2.1.0", None), "unknown")


class TestNewestRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "registry-cache.json")

    def _fetch(self, payload=None, error=None):
        def fake(url, timeout):
            if error is not None:
                raise error
            return payload
        calls = []

        def wrapper(url, timeout):
            calls.append(url)
            return fake(url, timeout)
        return wrapper, calls

    def test_live_fetch_returns_the_registry_version(self):
        fetch, calls = self._fetch({"version": "2.2.1"})
        version, source = lag.newest_release(cache_path=self.cache, fetch=fetch,
                                             now=1_000_000.0)
        self.assertEqual(version, "2.2.1")
        self.assertIn("live", source)
        self.assertEqual(calls, [lag.REGISTRY_URL])

    def test_a_fetched_version_is_cached_and_reused_inside_the_ttl(self):
        fetch, calls = self._fetch({"version": "2.2.1"})
        lag.newest_release(cache_path=self.cache, fetch=fetch, now=1_000_000.0)
        # Second call inside an hour: the cache answers, no request is made.
        spy, spy_calls = self._fetch({"version": "9.9.9"})
        version, source = lag.newest_release(cache_path=self.cache, fetch=spy,
                                             now=1_000_000.0 + lag.CACHE_TTL_SECONDS - 10)
        self.assertEqual(version, "2.2.1")
        self.assertIn("cache", source)
        self.assertEqual(spy_calls, [])

    def test_offline_with_an_expired_cache_is_unknown_not_an_error(self):
        fetch, _ = self._fetch(error=OSError("network unreachable"))
        version, source = lag.newest_release(cache_path=self.cache, fetch=fetch,
                                             now=1_000_000.0)
        self.assertIsNone(version)
        self.assertIn("unknown", source.lower())

    def test_offline_falls_back_to_a_cache_that_is_still_fresh(self):
        store, _ = self._fetch({"version": "2.2.1"})
        lag.newest_release(cache_path=self.cache, fetch=store, now=1_000_000.0)
        fetch, _ = self._fetch(error=OSError("network unreachable"))
        version, source = lag.newest_release(cache_path=self.cache, fetch=fetch,
                                             now=1_000_000.0 + 60)
        self.assertEqual(version, "2.2.1")
        self.assertIn("cache", source)

    def test_a_registry_answer_without_a_version_is_unknown(self):
        fetch, _ = self._fetch({"name": "@anthropic-ai/claude-code"})
        version, _ = lag.newest_release(cache_path=self.cache, fetch=fetch,
                                        now=1_000_000.0)
        self.assertIsNone(version)

    def test_a_corrupt_cache_file_does_not_crash_the_check(self):
        with open(self.cache, "w") as handle:
            handle.write("{ not json")
        fetch, _ = self._fetch(error=OSError("offline"))
        version, source = lag.newest_release(cache_path=self.cache, fetch=fetch,
                                             now=1_000_000.0)
        self.assertIsNone(version)
        self.assertIn("unknown", source.lower())


class TestAutoupdater(unittest.TestCase):
    def test_unset_everywhere_is_on(self):
        state, where = lag.autoupdater_state(env={}, settings_path=None)
        self.assertEqual(state, "on")
        self.assertIsNone(where)

    def test_env_variable_turns_it_off_and_names_the_source(self):
        state, where = lag.autoupdater_state(env={"DISABLE_AUTOUPDATER": "1"},
                                             settings_path=None)
        self.assertEqual(state, "off")
        self.assertEqual(where, "env")

    def test_a_falsy_env_value_leaves_it_on(self):
        state, _ = lag.autoupdater_state(env={"DISABLE_AUTOUPDATER": "0"},
                                         settings_path=None)
        self.assertEqual(state, "on")

    def test_settings_json_env_block_turns_it_off(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "settings.json")
        with open(path, "w") as handle:
            json.dump({"env": {"DISABLE_AUTOUPDATER": "true"}}, handle)
        state, where = lag.autoupdater_state(env={}, settings_path=path)
        self.assertEqual(state, "off")
        self.assertEqual(where, path)

    def test_a_json_boolean_true_in_settings_disables_it_too(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "settings.json")
        with open(path, "w") as handle:
            json.dump({"env": {"DISABLE_AUTOUPDATER": True}}, handle)
        state, where = lag.autoupdater_state(env={}, settings_path=path)
        self.assertEqual(state, "off")
        self.assertEqual(where, path)

    def test_a_malformed_or_missing_settings_file_is_on_not_an_error(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "settings.json")
        with open(path, "w") as handle:
            handle.write("{ truncated")
        state, _ = lag.autoupdater_state(env={}, settings_path=path)
        self.assertEqual(state, "on")
        missing = os.path.join(tmp.name, "nope.json")
        self.assertEqual(lag.autoupdater_state(env={}, settings_path=missing)[0], "on")


class TestSettingsPath(unittest.TestCase):
    def test_posix_home(self):
        path = lag.settings_path(env={"HOME": "/home/user"}, os_name="posix")
        self.assertTrue(path.endswith(os.path.join(".claude", "settings.json")), path)
        self.assertTrue(path.startswith("/home/user"), path)

    def test_windows_uses_userprofile_not_home(self):
        path = lag.settings_path(env={"USERPROFILE": r"C:\Users\tester",
                                      "HOME": "/wrong/posix/home"},
                                 os_name="nt")
        self.assertTrue(path.startswith(r"C:\Users\tester"), path)
        self.assertTrue(path.endswith(".claude" + os.sep + "settings.json"), path)
        self.assertNotIn("/wrong", path)

    def test_no_home_at_all_is_none(self):
        self.assertIsNone(lag.settings_path(env={}, os_name="posix"))

    def test_explicit_override_wins(self):
        path = lag.settings_path(env={"AUTOOS_CLAUDE_SETTINGS": "/elsewhere/settings.json",
                                      "HOME": "/home/user"},
                                 os_name="posix")
        self.assertEqual(path, "/elsewhere/settings.json")

    def test_wsl_inherits_the_posix_home_not_a_guessed_windows_profile(self):
        # WSL reaches the Windows-side file through --settings / the env
        # override; the tool never guesses a %USERPROFILE% path across /mnt.
        path = lag.settings_path(env={"HOME": "/home/user",
                                      "WSL_DISTRO_NAME": "Ubuntu",
                                      "USERPROFILE": r"C:\Users\tester"},
                                 os_name="posix")
        self.assertTrue(path.startswith("/home/user"), path)

    def test_a_windows_path_uses_backslashes_and_the_dotclaude_folder(self):
        path = lag.settings_path(env={"USERPROFILE": r"C:\Users\tester"}, os_name="nt")
        self.assertIn(".claude", path)
        self.assertEqual(os.path.basename(path), "settings.json")


class TestVersionChangeRecommendation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "claude-cli-lag.json")

    def test_first_run_records_without_recommending(self):
        changed, previous = lag.note_version_change("2.1.284", self.state)
        self.assertFalse(changed)
        self.assertIsNone(previous)
        self.assertEqual(lag.read_state(self.state)["installed"], "2.1.284")

    def test_a_changed_version_recommends_the_behaviour_checks(self):
        lag.note_version_change("2.1.283", self.state)
        changed, previous = lag.note_version_change("2.1.284", self.state)
        self.assertTrue(changed)
        self.assertEqual(previous, "2.1.283")
        self.assertEqual(lag.read_state(self.state)["installed"], "2.1.284")

    def test_the_same_version_twice_is_not_a_change(self):
        lag.note_version_change("2.1.284", self.state)
        changed, _ = lag.note_version_change("2.1.284", self.state)
        self.assertFalse(changed)

    def test_an_unknown_version_neither_records_nor_recommends(self):
        lag.note_version_change("2.1.283", self.state)
        changed, previous = lag.note_version_change(None, self.state)
        self.assertFalse(changed)
        self.assertEqual(previous, "2.1.283")
        self.assertEqual(lag.read_state(self.state)["installed"], "2.1.283")

    def test_the_recommendation_names_the_doc_sections(self):
        lag.note_version_change("2.1.283", self.state)
        text = lag.recommendation("2.1.283", "2.1.284")
        self.assertIn("ORCH-A1", text)
        self.assertIn("§3.3", text)
        self.assertIn("deny-over-allow", text)
        self.assertIn("HOOKS", text)
        self.assertIn("§6", text)


class TestReportAndExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = self.tmp.name

    def _run(self, installed, newest, source="npm registry (live)", env=None):
        out = io.StringIO()
        with mock.patch.object(lag, "installed_version", return_value=installed), \
             mock.patch.object(lag, "newest_release",
                               return_value=(newest, source)):
            rc = lag.main(["--state-dir", self.state_dir],
                          env=env or {}, out=out)
        return rc, out.getvalue()

    def test_lagging_host_exits_one_and_says_restart_picks_it_up(self):
        rc, text = self._run("2.1.284", "2.2.1")
        self.assertEqual(rc, 1)
        self.assertIn("lags - restart picks it up", text)
        self.assertIn("2.1.284", text)
        self.assertIn("2.2.1", text)

    def test_up_to_date_host_exits_zero(self):
        rc, text = self._run("2.2.1", "2.2.1")
        self.assertEqual(rc, 0)
        self.assertIn("up to date", text)
        self.assertNotIn("lags", text)

    def test_unknown_newest_exits_zero(self):
        rc, text = self._run("2.1.284", None, source="unknown (offline)")
        self.assertEqual(rc, 0)
        self.assertIn("unknown", text)

    def test_no_claude_binary_is_unknown_and_exits_zero(self):
        rc, text = self._run(None, "2.2.1")
        self.assertEqual(rc, 0)
        self.assertIn("unknown", text)

    def test_autoupdater_state_is_printed_with_its_source(self):
        rc, text = self._run("2.2.1", "2.2.1", env={"DISABLE_AUTOUPDATER": "1"})
        self.assertEqual(rc, 0)
        self.assertIn("off (set in env)", text)

    def test_autoupdater_on_is_printed_when_nothing_disables_it(self):
        _, text = self._run("2.2.1", "2.2.1")
        self.assertIn("autoupdater: on", text.replace("  ", " "))

    def test_the_host_name_is_in_the_report(self):
        _, text = self._run("2.2.1", "2.2.1")
        self.assertIn(lag.hostname(), text)

    def test_a_version_change_adds_the_recommendation_line(self):
        state = os.path.join(self.state_dir, "claude-cli-lag.json")
        lag.note_version_change("2.1.283", state)
        rc, text = self._run("2.1.284", "2.2.1")
        self.assertEqual(rc, 1)
        self.assertIn("ORCH-A1", text)
        self.assertIn("HOOKS", text)

    def test_the_report_never_suggests_a_pin(self):
        # CLIPIN / D-137: every host runs the latest, auto-update stays on.
        _, text = self._run("2.1.284", "2.2.1")
        self.assertFalse(re.search(r"\bpin(?:s|ned|ning)?\b", text, re.I), text)

    def test_main_never_raises_when_the_registry_is_unreachable(self):
        out = io.StringIO()
        with mock.patch.object(lag, "installed_version", return_value="2.1.284"), \
             mock.patch.object(lag, "urlopen_json", side_effect=OSError("offline")), \
             mock.patch.object(lag, "read_cache", return_value=None):
            rc = lag.main(["--state-dir", self.state_dir], env={}, out=out)
        self.assertEqual(rc, 0)
        self.assertIn("unknown", out.getvalue())

    def test_an_http_protocol_error_reports_unknown_and_exits_zero(self):
        # http.client's HTTPExceptions (BadStatusLine, IncompleteRead) are not
        # OSError subclasses; the offline-never-errors contract needs them read
        # as "no answer" too, not as a traceback.
        out = io.StringIO()
        with mock.patch.object(lag, "installed_version", return_value="2.1.284"), \
             mock.patch.object(lag, "urlopen_json",
                               side_effect=http.client.IncompleteRead(b"")), \
             mock.patch.object(lag, "read_cache", return_value=None):
            rc = lag.main(["--state-dir", self.state_dir], env={}, out=out)
        self.assertEqual(rc, 0)
        self.assertIn("status:      unknown", out.getvalue())


class TestStateLocation(unittest.TestCase):
    def test_state_lives_under_the_git_ignored_repo_state_dir(self):
        with mock.patch.dict(os.environ, {"AUTOOS_STATE_DIR": ""}, clear=False):
            resolved = lag.default_state_dir(env={})
        self.assertTrue(resolved.endswith(os.path.join("logs")), resolved)
        self.assertIn(str(ROOT), resolved)

    def test_env_override_is_honoured(self):
        self.assertEqual(lag.default_state_dir(env={"AUTOOS_STATE_DIR": "/elsewhere/logs"}),
                         "/elsewhere/logs")

    def test_the_state_dir_is_git_ignored(self):
        lines = [line.strip() for line in (ROOT / ".gitignore").read_text().splitlines()]
        self.assertIn("logs/", lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
