#!/usr/bin/env python3
"""Tests for tools/prepush_private.py — the private-pattern + gitleaks legs.

Hermetic by construction. Every repository, every names file and every
``gitleaks`` here is made in a temp dir by the test itself, from invented
literals that mean nothing outside this file: the names file the gate reads in
production lives in a private repository, so nothing here may quote one from it
— the entries are ``zz-fixture-*`` strings. No network, and no real gitleaks.

The host's gitleaks is deliberately pinned out of every test that does not mean
to run the fake one (``AUTOOS_GITLEAKS`` pointed at a path inside the temp dir),
because a leg that reads the ``PATH`` would answer differently on two machines —
which is how a suite goes green here and red in CI.

The fixture shape — a synthetic repo with a ``refs/remotes/origin/main`` written
without a network, and the stub ``tools/affected-tests.py`` that answers with
whatever plan the test names — is copied from ``tests/test_prepush.py``; the
leg's own behaviour is what differs.

Run directly:

    python3 tests/test_prepush_private.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "tools" / "prepush_private.py"
SCRIPT = ROOT / "tools" / "prepush.py"

#: Throwaway entries, invented for this file.
ALPHA = "zz-fixture-alpha"
BETA = "zz-fixture-beta"
GAMMA = "zz-fixture-gamma"

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]

#: The stub mapper, copied from tests/test_prepush.py: it answers with whatever
#: plan the test put in the environment, so the integration tests below decide
#: what the gate runs instead of trusting the real mapping.
AFFECTED_STUB = '''#!/usr/bin/env python3
import json
import os
import sys
sys.stdout.write(json.dumps(json.loads(os.environ.get("PREPUSH_FIXTURE_PLAN", "{}"))) + "\\n")
'''


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


private = _load("prepush_private", MODULE)


def run_git(*args, cwd=None, check=True):
    proc = subprocess.run(GIT + list(args), cwd=str(cwd),
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr))
    return proc.stdout.strip()


class PrivateGateTests(unittest.TestCase):
    """``run_private_gate`` over a repo whose base is a real commit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run_git("init", "-q", cwd=self.repo)
        # The base carries GAMMA on a line this push never touches — the control
        # for "only what the push ADDS is read".
        self.commit("tracked.txt", "first\nsecond\n")
        self.commit("pre-existing.txt", "a line with %s already in the base\n" % GAMMA,
                    append=False)
        self.base = run_git("rev-parse", "HEAD", cwd=self.repo)
        run_git("update-ref", "refs/remotes/origin/main", self.base, cwd=self.repo)

    def commit(self, rel, text, msg="lane", append=False):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if append and path.is_file():
            text = path.read_text(encoding="utf-8") + text
        path.write_text(text, encoding="utf-8")
        run_git("add", rel, cwd=self.repo)
        run_git("commit", "-q", "-m", msg, cwd=self.repo)
        return path

    def names_file(self, text, name="names.txt"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def gate(self, env=None):
        """The leg, with the host's gitleaks pinned out unless the test names one."""
        full = {"AUTOOS_GITLEAKS": str(self.tmp / "no-gitleaks-on-this-host")}
        full.update(env or {})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status, results = private.run_private_gate(self.repo, self.base, full)
        return status, results, out.getvalue()

    def patterns(self, text):
        return self.gate({"AUTOOS_PRIVATE_PATTERNS": str(self.names_file(text))})

    # --- the names-file leg ---------------------------------------------------

    def test_an_unset_names_file_skips_the_gate_and_records_nothing(self):
        status, results, out = self.gate()
        self.assertEqual((status, results), (0, []))
        self.assertIn("private-pattern gate skipped", out)
        self.assertIn("AUTOOS_PRIVATE_PATTERNS not set", out)

    def test_a_names_file_that_is_not_there_is_skipped_not_failed(self):
        status, results, out = self.gate(
            {"AUTOOS_PRIVATE_PATTERNS": str(self.tmp / "absent-names.txt")})
        self.assertEqual((status, results), (0, []))
        self.assertIn("file missing", out)

    def test_an_empty_names_file_is_skipped_saying_so(self):
        status, results, out = self.patterns("# only a comment\n\n   \n")
        self.assertEqual((status, results), (0, []))
        self.assertIn("no entries", out)

    def test_a_hit_on_an_added_line_refuses_and_names_only_the_place(self):
        self.commit("tracked.txt", "third %s third\n" % ALPHA, append=True)
        status, results, out = self.patterns(ALPHA + "\n")
        self.assertEqual(status, private.REFUSED)
        self.assertIn("tracked.txt:3: private-pattern #1", out)
        self.assertNotIn(ALPHA, out, "the matched text was printed")
        self.assertEqual(results, [{"command": "private-pattern-gate",
                                    "ok": False, "passed": None}])
        self.assertTrue(out.rstrip().endswith("1"),
                        "the output must end with the count: %r" % out)

    def test_a_literal_that_is_only_on_a_base_line_is_not_a_hit(self):
        self.commit("tracked.txt", "a clean new line\n", append=True)
        status, results, out = self.patterns(GAMMA + "\n")
        self.assertEqual(status, 0)
        self.assertEqual(results, [{"command": "private-pattern-gate",
                                    "ok": True, "passed": None}])
        self.assertNotIn(GAMMA, out)

    def test_an_entry_matches_whatever_case_the_line_uses(self):
        self.commit("tracked.txt", "SHOUTED %s\n" % ALPHA.upper(), append=True)
        status, _results, out = self.patterns(ALPHA + "\n")
        self.assertEqual(status, private.REFUSED)
        self.assertIn("tracked.txt:3: private-pattern #1", out)

    def test_comments_and_blanks_are_not_entries_and_numbering_is_their_order(self):
        self.commit("tracked.txt", "%s here\nand %s there\n" % (ALPHA, BETA),
                    append=True)
        status, _results, out = self.patterns(
            "# a comment naming %s\n\n%s\n# %s again\n%s\n" % (GAMMA, ALPHA, GAMMA, BETA))
        self.assertEqual(status, private.REFUSED)
        self.assertIn("tracked.txt:3: private-pattern #1", out)
        self.assertIn("tracked.txt:4: private-pattern #2", out)
        self.assertNotIn("#3", out, "a comment counted as an entry")
        self.assertIn("hits: 2", out)

    def test_an_unreadable_names_file_refuses_without_naming_where_it_lives(self):
        path = self.names_file(ALPHA + "\n")
        with mock.patch.object(private, "load_entries",
                               side_effect=OSError("closed to us")):
            status, results, out = self.gate({"AUTOOS_PRIVATE_PATTERNS": str(path)})
        self.assertEqual(status, private.REFUSED)
        self.assertIn("could not be read", out)
        self.assertNotIn(str(self.tmp), out, "the private location leaked")

    def test_a_gate_with_nothing_added_is_green_and_still_records_that_it_ran(self):
        status, results, out = self.patterns(ALPHA + "\n")
        self.assertEqual(status, 0)
        self.assertTrue(results[0]["ok"])

    # --- the pure helpers -----------------------------------------------------

    def test_load_entries_keeps_the_real_lines_in_order(self):
        path = self.names_file("# c\n\n%s\n  \n%s\n" % (ALPHA, BETA))
        self.assertEqual(private.load_entries(path), [ALPHA, BETA])

    def test_added_lines_reports_the_new_side_line_number_of_each_file(self):
        self.commit("tracked.txt", "alpha %s\n" % ALPHA, append=True)
        self.commit("docs/other.md", "beta\n%s\n" % BETA)
        rows = private.added_lines(self.repo, self.base)
        self.assertEqual([r[:2] for r in rows if r[0] == "tracked.txt"],
                         [("tracked.txt", 3)], rows)
        self.assertEqual([r[:2] for r in rows if r[0] == "docs/other.md"],
                         [("docs/other.md", 1), ("docs/other.md", 2)], rows)

    def test_scan_pairs_every_hit_with_its_entry_number(self):
        hits = private.scan([ALPHA, BETA], [("f.txt", 7, "saw " + BETA.lower())])
        self.assertEqual(hits, [("f.txt", 7, 2)])

    def test_an_added_line_shaped_like_a_diff_header_hides_nothing_after_it(self):
        # A content line that starts with "++ " reaches the patch as "+++ b/..." —
        # byte-identical to the next file's header — and a parser that reads it as
        # one attributes every later added line of the real file to a path that does
        # not exist: a secret gate failing open, on the shape a diff tool's own test
        # fixture writes.
        self.commit("tracked.txt", "++ b/not-a-header\nthen %s\n" % ALPHA,
                    append=True)
        self.assertEqual([(r[0], r[1]) for r in private.added_lines(self.repo, self.base)],
                         [("tracked.txt", 3), ("tracked.txt", 4)])
        status, _results, out = self.patterns(ALPHA + "\n")
        self.assertEqual(status, private.REFUSED)
        self.assertIn("tracked.txt:4: private-pattern #1", out)

    def test_two_hunks_of_one_file_report_their_own_line_numbers(self):
        # -U0 splits a push's additions into hunks with unchanged lines between
        # them; a counter that only reset at the file header would number the
        # second hunk off the first one and point a reader at the wrong line.
        self.commit("tracked.txt", "first\nsecond\nthird %s\n" % ALPHA, append=True)
        self.commit("tracked.txt", "zeroth\nfirst\nsecond\nthird %s\nfourth\n" % ALPHA)
        rows = private.added_lines(self.repo, self.base)
        self.assertEqual([(r[0], r[1]) for r in rows],
                         [("tracked.txt", 1), ("tracked.txt", 4),
                          ("tracked.txt", 5)], rows)
        status, _results, out = self.patterns(ALPHA + "\n")
        self.assertEqual(status, private.REFUSED)
        self.assertIn("tracked.txt:4: private-pattern #1", out)

    # --- the gitleaks leg -----------------------------------------------------

    @unittest.skipIf(os.name == "nt", "the fake gitleaks is a shebang script")
    def fake_gitleaks(self, exit_code=0):
        script = self.tmp / "fake-gitleaks"
        seen = self.tmp / "gitleaks-argv.txt"
        script.write_text("#!%s\n"
                          "import sys\n"
                          "open(%r, 'w').write('\\n'.join(sys.argv[1:]))\n"
                          "print('fake gitleaks ran')\n"
                          "sys.exit(%d)\n" % (sys.executable, str(seen), exit_code),
                          encoding="utf-8")
        script.chmod(0o755)
        return script, seen

    @unittest.skipIf(os.name == "nt", "the fake gitleaks is a shebang script")
    def test_a_green_gitleaks_is_recorded_as_a_command_that_ran(self):
        script, _seen = self.fake_gitleaks(0)
        status, results, out = self.gate({"AUTOOS_GITLEAKS": str(script)})
        self.assertEqual(status, 0)
        self.assertEqual(results, [{"command": "gitleaks", "ok": True, "passed": None}])
        self.assertIn("ok   gitleaks", out)

    @unittest.skipIf(os.name == "nt", "the fake gitleaks is a shebang script")
    def test_the_gitleaks_leg_is_asked_for_the_range_this_push_adds(self):
        script, seen = self.fake_gitleaks(0)
        self.gate({"AUTOOS_GITLEAKS": str(script)})
        self.assertEqual(seen.read_text(encoding="utf-8").splitlines(),
                         ["git", "--no-banner", "--redact", "--log-opts",
                          "%s..HEAD" % self.base, str(self.repo)])

    @unittest.skipIf(os.name == "nt", "the fake gitleaks is a shebang script")
    def test_a_red_gitleaks_refuses_and_its_own_redacted_output_is_shown(self):
        script, _seen = self.fake_gitleaks(1)
        status, results, out = self.gate({"AUTOOS_GITLEAKS": str(script)})
        self.assertEqual(status, private.REFUSED)
        self.assertEqual(results, [{"command": "gitleaks", "ok": False,
                                    "passed": None}])
        self.assertIn("fake gitleaks ran", out)

    def test_a_gitleaks_that_is_not_installed_skips_the_leg_never_fails(self):
        status, results, out = self.gate(
            {"AUTOOS_GITLEAKS": str(self.tmp / "not-installed")})
        self.assertEqual((status, results), (0, []))
        self.assertIn("gitleaks skipped", out)

    # --- through the gate itself ----------------------------------------------

    def install_gate(self):
        """The gate and its new leg, plus the stubs that keep the checks cheap."""
        for src in (SCRIPT, MODULE):
            (self.repo / "tools").mkdir(parents=True, exist_ok=True)
            shutil.copy(str(src), str(self.repo / "tools" / src.name))
        (self.repo / "tools" / "affected-tests.py").write_text(AFFECTED_STUB,
                                                               encoding="utf-8")
        for rel in ("tests/test_ci_shards.py", "tests/ci-shards.py"):
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("import pathlib, sys\n"
                            "pathlib.Path(%r).write_text('ran\\n')\n"
                            "sys.exit(0)\n" % str(self.tmp / "checks-ran.txt"),
                            encoding="utf-8")
        run_git("add", "-A", cwd=self.repo)
        run_git("commit", "-q", "-m", "gate stubs", cwd=self.repo)

    def run_gate(self, env=None):
        full = dict(os.environ)
        full.pop("AUTOOS_AGENT_RUN_ID", None)
        full["AUTOOS_STATE_DIR"] = str(self.tmp / "store")
        full["AUTOOS_GITLEAKS"] = str(self.tmp / "no-gitleaks-on-this-host")
        full["PREPUSH_FIXTURE_PLAN"] = "{}"
        full.update(env or {})
        proc = subprocess.run([sys.executable,
                               str(self.repo / "tools" / "prepush.py"),
                               "--repo", str(self.repo)],
                              capture_output=True, text=True, env=full,
                              cwd=str(self.repo))
        return proc.returncode, proc.stdout + proc.stderr

    def store_records(self):
        home = self.tmp / "store" / "prepush"
        if not home.is_dir():
            return []
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(home.glob("*.json"))]

    def test_a_hit_refuses_before_any_test_runs(self):
        self.commit("tracked.txt", "leaked %s line\n" % ALPHA, append=True)
        self.install_gate()
        rc, out = self.run_gate({"AUTOOS_PRIVATE_PATTERNS": str(self.names_file(ALPHA))})
        self.assertEqual(rc, 1, out)
        self.assertIn("private-pattern #1", out)
        self.assertFalse((self.tmp / "checks-ran.txt").exists(),
                         "the gate ran the suite over a tree it should refuse")
        self.assertEqual(self.store_records(), [], "a refused push left a certificate")

    def test_a_leg_that_ran_is_named_in_the_green_certificate(self):
        self.install_gate()
        rc, out = self.run_gate({"AUTOOS_PRIVATE_PATTERNS": str(self.names_file(BETA))})
        self.assertEqual(rc, 0, out)
        records = self.store_records()
        self.assertEqual(len(records), 1, records)
        commands = records[0]["commands"]
        self.assertIn("private-pattern-gate", commands)
        entry = [r for r in records[0]["results"]
                 if r["command"] == "private-pattern-gate"]
        self.assertEqual(entry, [{"command": "private-pattern-gate",
                                 "ok": True, "passed": None}])

    def test_a_leg_that_was_skipped_adds_nothing_to_the_certificate(self):
        self.install_gate()
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, out)
        self.assertIn("private-pattern gate skipped", out)
        self.assertEqual(len(self.store_records()), 1)
        self.assertNotIn("private-pattern-gate", self.store_records()[0]["commands"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
