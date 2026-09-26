#!/usr/bin/env python3
"""Tests for tools/autoos_measure.py - the I/O half of resolver v2 (spec 5.1).

Everything is measured against a throwaway git repository built in a
TemporaryDirectory, so nothing here reads the live machine, installs anything or
touches the network. The module imports from any cwd once `tools/` is on
sys.path, which is the first thing this file does.
"""
import math
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import autoos_measure as m  # noqa: E402

# The fixture must isolate each rule:
#   lib/a.sh            two lines hold `shared_func`
#   lib/b.sh            no `shared_func`
#   tools/c.py          two lines hold `shared_func`
#   tests/test_c.py     under tests/, names and content both point at tools/c.py
#   pkg/test_alpha.py   test_*.py outside tests/: name-only match for "alpha"
#   pkg/Widget.Tests.ps1  *.Tests.ps1 outside tests/: content mentions lib/b.sh
#   blob.bin            a tracked binary the token estimate must skip
#   tests/c.Tests.ps1, tests/test_c_extra.py  whole-token name match for "c"
#   tests/test_maintenance.py, tests/test_utility.py  substrings, never tokens
FIXTURE = {
    "lib/a.sh": "shared_func one\nother\nshared_func two\n",
    "lib/b.sh": "nothing here\n",
    "tools/c.py": "shared_func = 1\nprint(shared_func)\nx = 0\n",
    "tests/test_c.py": ("# covers tools/c.py and lib/a.sh\n"
                        "from tools import c\n"
                        "def test_c():\n"
                        "    assert c.shared_func == 1\n"),
    "pkg/test_alpha.py": "# name-only test file, deliberately outside tests/\n",
    "pkg/Widget.Tests.ps1": "# checks lib/b.sh\n",
    "tests/test_c_extra.py": "# extra tests for c\n",
    "tests/c.Tests.ps1": "# c suite\n",
    "tests/test_maintenance.py": "# maintenance suite\n",
    "tests/test_utility.py": "# utility suite\n",
}
BLOB = b"\x00\x01\x02\x03\x00"


class RepoTestCase(unittest.TestCase):
    """One committed fixture repo shared by every measurement test."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name)
        for rel, text in FIXTURE.items():
            path = cls.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (cls.repo / "blob.bin").write_bytes(BLOB)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false"]
        subprocess.run(git + ["init", "-q"], cwd=cls.repo, check=True)
        subprocess.run(git + ["add", "-A"], cwd=cls.repo, check=True)
        subprocess.run(git + ["commit", "-q", "-m", "fixture"], cwd=cls.repo,
                       check=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class FilesTests(RepoTestCase):
    def test_measured_from_tracked_files_under_paths(self):
        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief")
        self.assertEqual(f["files"], 2)
        self.assertEqual(f["sources"]["files"], "git ls-files")

    def test_measured_is_scoped_to_the_given_paths(self):
        f = m.measure({"paths": ["lib", "tools"]}, str(self.repo), "brief")
        self.assertEqual(f["files"], 3)

    def test_declared_files_win_over_git(self):
        f = m.measure({"paths": ["lib"], "files": ["x", "y", "z"]},
                      str(self.repo), "brief")
        self.assertEqual(f["files"], 3)
        self.assertEqual(f["sources"]["files"], "declared")

    def test_empty_paths_fail_closed(self):
        for card in ({"paths": []}, {}, {"paths": [], "files": ["x.py"]}):
            with self.subTest(card=card):
                with self.assertRaises(ValueError):
                    m.measure(card, str(self.repo), "brief")


class ModulesTests(RepoTestCase):
    def test_distinct_top_level_components(self):
        f = m.measure({"paths": ["lib", "tools"]}, str(self.repo), "brief")
        self.assertEqual(f["modules"], 2)

    def test_one_component(self):
        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief")
        self.assertEqual(f["modules"], 1)

    def test_a_root_file_is_its_own_module(self):
        # Only "." for root.py; if root files were ignored this would be 1.
        f = m.measure({"paths": ["lib"], "files": ["root.py", "lib/a.sh"]},
                      str(self.repo), "brief")
        self.assertEqual(f["modules"], 2)


class LinesTests(RepoTestCase):
    def test_measured_is_twenty_per_file(self):
        f = m.measure({"paths": ["lib", "tools"]}, str(self.repo), "brief")
        self.assertEqual(f["files"], 3)
        self.assertEqual(f["lines"], 60)
        self.assertEqual(f["sources"]["lines"], "20*files")

    def test_declared_lines_win(self):
        f = m.measure({"paths": ["lib"], "files": ["a", "b"], "lines": 7},
                      str(self.repo), "brief")
        self.assertEqual(f["lines"], 7)
        self.assertEqual(f["sources"]["lines"], "declared")


class DeclaredValuesTests(RepoTestCase):
    def test_malformed_declarations_fail_closed(self):
        # Declared files must be a list of strings and declared lines a
        # non-negative int; strings that int() could coerce and bools are
        # rejected, and the ValueError names the offending field.
        repo = str(self.repo)
        bad = [
            ({"paths": ["lib"], "files": "lib/a.sh"}, "files"),
            ({"paths": ["lib"], "files": ("lib/a.sh",)}, "files"),
            ({"paths": ["lib"], "files": ["lib/a.sh", 3]}, "files"),
            ({"paths": ["lib"], "files": [None]}, "files"),
            ({"paths": ["lib"], "files": "x", "lines": 3}, "files"),
            ({"paths": ["lib"], "lines": "7"}, "lines"),
            ({"paths": ["lib"], "lines": -1}, "lines"),
            ({"paths": ["lib"], "lines": True}, "lines"),
            ({"paths": ["lib"], "lines": 2.5}, "lines"),
        ]
        for card, field in bad:
            with self.subTest(card=card):
                with self.assertRaises(ValueError) as ctx:
                    m.measure(card, repo, "brief")
                self.assertIn(field, str(ctx.exception))


class TestsFeatureTests(RepoTestCase):
    def test_covered_by_a_test_under_tests(self):
        f = m.measure({"paths": ["tools"]}, str(self.repo), "brief")
        self.assertTrue(f["tests"])

    def test_name_match_from_a_test_file_outside_tests_dir(self):
        # pkg/test_alpha.py is a test by name only; nothing mentions lib/alpha.sh.
        f = m.measure({"paths": ["lib"], "files": ["lib/alpha.sh"]},
                      str(self.repo), "brief")
        self.assertTrue(f["tests"])

    def test_content_match_from_a_ps1_test_outside_tests_dir(self):
        # pkg/Widget.Tests.ps1 is a test by suffix; its content names lib/b.sh.
        f = m.measure({"paths": ["lib"], "files": ["lib/b.sh"]},
                      str(self.repo), "brief")
        self.assertTrue(f["tests"])

    def test_untouched_file_is_not_covered(self):
        f = m.measure({"paths": ["lib"], "files": ["lib/zzz.sh"]},
                      str(self.repo), "brief")
        self.assertFalse(f["tests"])
        self.assertEqual(f["sources"]["tests"], "none")

    def test_name_match_is_whole_token_not_substring(self):
        # stem "c" is a whole token of test_c.py, c.Tests.ps1 and
        # test_c_extra.py; "i" and "util" are mere substrings inside
        # test_maintenance.py / test_utility.py and must not count.
        repo = str(self.repo)
        for candidate in ("tests/test_c.py", "tests/c.Tests.ps1",
                          "tests/test_c_extra.py"):
            with self.subTest(covers=candidate):
                self.assertEqual(m._covered(repo, ["lib/c.py"], [candidate]),
                                 candidate)
        f = m.measure({"paths": ["lib"], "files": ["lib/i.py"]}, repo, "brief")
        self.assertFalse(f["tests"])
        self.assertEqual(f["sources"]["tests"], "none")
        self.assertIsNone(m._covered(repo, ["lib/util.py"],
                                     ["tests/test_utility.py"]))


class FanoutTests(RepoTestCase):
    def test_no_symbols_is_zero(self):
        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief", symbols=[])
        self.assertEqual(f["fanout"], 0)
        self.assertEqual(f["sources"]["fanout"], "none")

    def test_default_backend_is_grep(self):
        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief",
                      symbols=["shared_func"])
        self.assertEqual(f["fanout"], 5)
        self.assertEqual(f["sources"]["fanout"], "grep_fanout")

    def test_grep_fanout_counts_matching_lines(self):
        # lib/a.sh 2 + lib/b.sh 0 + tools/c.py 2 + tests/test_c.py 1 = 5.
        self.assertEqual(m.grep_fanout(str(self.repo), "shared_func"), 5)

    def test_grep_fanout_absent_symbol_is_zero(self):
        self.assertEqual(m.grep_fanout(str(self.repo), "no_such_symbol_zzz"), 0)

    def test_git_error_is_none_not_zero(self):
        # git grep exit 1 means "no matches"; any other non-zero exit (bad
        # repo, index lock) must read as None so measure() can fall through
        # and report "unmeasured" instead of a false zero. The tempdir root
        # that holds the fixture repo is itself not a repository.
        self.assertIsNone(m.grep_fanout(str(self.repo.parent), "shared_func"))

    def test_first_non_none_backend_wins(self):
        def first(repo, symbol):
            return None

        def second(repo, symbol):
            return 7

        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief",
                      symbols=["shared_func"], fanout_backends=[first, second])
        self.assertEqual(f["fanout"], 7)
        self.assertEqual(f["sources"]["fanout"], "second")

    def test_a_later_backend_does_not_override_an_earlier_one(self):
        def first(repo, symbol):
            return 3

        def second(repo, symbol):
            return 7

        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief",
                      symbols=["shared_func"], fanout_backends=[first, second])
        self.assertEqual(f["fanout"], 3)
        self.assertEqual(f["sources"]["fanout"], "first")

    def test_all_backends_none_is_unmeasured(self):
        def nothing(repo, symbol):
            return None

        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief",
                      symbols=["shared_func"], fanout_backends=[nothing])
        self.assertEqual(f["fanout"], 0)
        self.assertEqual(f["sources"]["fanout"], "unmeasured")

    def test_max_over_symbols(self):
        def by_symbol(repo, symbol):
            return {"shared_func": 2, "other_func": 9}[symbol]

        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief",
                      symbols=["shared_func", "other_func"],
                      fanout_backends=[by_symbol])
        self.assertEqual(f["fanout"], 9)


class NeedTokensTests(RepoTestCase):
    def test_estimate_tokens_is_ceil_quarter(self):
        self.assertEqual(m.estimate_tokens(""), 0)
        self.assertEqual(m.estimate_tokens("a"), 1)
        self.assertEqual(m.estimate_tokens("abcd"), 1)
        self.assertEqual(m.estimate_tokens("abcde"), 2)

    def test_sum_over_files_brief_and_schema(self):
        files = ["lib/a.sh", "lib/b.sh", "tools/c.py"]
        brief = "do the thing"
        f = m.measure({"paths": ["lib"], "files": files}, str(self.repo), brief,
                      schema_tokens=13)
        expected = sum(math.ceil(len((self.repo / p).read_text(errors="replace")) / 4)
                       for p in files)
        expected += math.ceil(len(brief) / 4) + 13
        self.assertEqual(f["need_tokens"], expected)

    def test_binary_file_is_skipped(self):
        f = m.measure({"paths": ["lib"], "files": ["lib/a.sh", "blob.bin"]},
                      str(self.repo), "")
        a = math.ceil(len((self.repo / "lib/a.sh").read_text(errors="replace")) / 4)
        self.assertEqual(f["need_tokens"], a)

    def test_unreadable_file_is_skipped(self):
        f = m.measure({"paths": ["lib"], "files": ["lib/a.sh", "no/such/file.py"]},
                      str(self.repo), "")
        a = math.ceil(len((self.repo / "lib/a.sh").read_text(errors="replace")) / 4)
        self.assertEqual(f["need_tokens"], a)


class ShapeTests(RepoTestCase):
    def test_feature_and_source_keys(self):
        f = m.measure({"paths": ["lib"]}, str(self.repo), "brief")
        self.assertEqual(set(f), {"files", "modules", "fanout", "lines", "tests",
                                  "need_tokens", "sources"})
        self.assertEqual(set(f["sources"]), {"files", "modules", "fanout", "lines",
                                             "tests", "need_tokens"})


class _FakeClient:
    def __init__(self, name, binary, signin_probe=()):
        self.name = name
        self.binary = binary
        self.signin_probe = signin_probe


def _fake_clients():
    """A CLIENTS map with no real binaries: python3 for installed, else missing."""
    def signin_state(client, env=None):
        return {"ok": (True, ""), "out": (False, "please sign in"),
                "noprobe": (None, "")}[client.name]

    return types.SimpleNamespace(
        CLIENTS={
            "ok": _FakeClient("ok", "python3"),
            "out": _FakeClient("out", "python3"),
            "noprobe": _FakeClient("noprobe", "python3"),
            "ghost": _FakeClient("ghost", "definitely-not-a-real-binary-xyz"),
        },
        signin_state=signin_state,
    )


class ClientStateTests(unittest.TestCase):
    def state(self):
        return m.client_state(_fake_clients(),
                              env={"PATH": os.environ.get("PATH", "")})

    def test_installed_and_signed_in(self):
        self.assertEqual(self.state()["ok"],
                         {"installed": True, "signed_in": True, "reason": ""})

    def test_installed_but_signed_out_carries_the_reason(self):
        entry = self.state()["out"]
        self.assertTrue(entry["installed"])
        self.assertIs(entry["signed_in"], False)
        self.assertEqual(entry["reason"], "please sign in")

    def test_missing_binary_is_not_installed(self):
        entry = self.state()["ghost"]
        self.assertFalse(entry["installed"])
        self.assertIsNone(entry["signed_in"])

    def test_client_without_a_probe_is_unknown(self):
        entry = self.state()["noprobe"]
        self.assertTrue(entry["installed"])
        self.assertIsNone(entry["signed_in"])


if __name__ == "__main__":
    unittest.main()
