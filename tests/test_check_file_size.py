#!/usr/bin/env python3
"""tests/test_check_file_size.py — the 400-line gate's own tests (AO-400LOC).

Every case drives `tools/check-file-size.py` the way production runs it: a
subprocess whose cwd is a repository, so the checker resolves its checkout
through `git rev-parse --show-toplevel` exactly as the CI step and the pre-push
gate do (Principle 9 — never verify a branch production does not take).
Fixture repos are real git repos carrying their own identity, so the tests
also pass on CI's identity-less runners (the SBA red in tools/prepush.py).

Run directly:

    python3 tests/test_check_file_size.py
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check-file-size.py"


def blob(n):
    """n lines of text — `splitlines()` counts n whatever the last newline does."""
    return "".join("x\n" for _ in range(n))


class RepoFixture(unittest.TestCase):
    """A temp git repo the checker is pointed at through its cwd."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="check-file-size-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        # CI runners carry no git identity (the SBA red in tools/prepush.py):
        # a fixture commit must carry its own.
        self.git("config", "user.name", "file-size tests")
        self.git("config", "user.email", "filesize@example.invalid")

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=str(self.repo),
                              capture_output=True, text=True)

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def file(self, rel, n):
        """An n-line file, staged — only tracked files are measured."""
        self.write(rel, blob(n))
        self.git("add", "--", rel)
        return self.repo / rel

    def commit(self, message):
        run = self.git("commit", "-q", "-m", message)
        self.assertEqual(run.returncode, 0, run.stderr)

    def baseline(self, text):
        self.write(".filesize-baseline", text)

    def baseline_map(self):
        out = {}
        text = (self.repo / ".filesize-baseline").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                path, count = line.split()
                out[path] = int(count)
        return out

    def check(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT)] + list(args),
                              cwd=str(self.repo), capture_output=True, text=True)

    def assertPasses(self, run):
        self.assertEqual(run.returncode, 0, "expected green:\n" + run.stdout + run.stderr)

    def assertFails(self, run, needle):
        out = run.stdout + run.stderr
        self.assertEqual(run.returncode, 1, "expected refusal, got %d:\n%s" % (run.returncode, out))
        self.assertIn(needle, out, "the refusal never named it:\n" + out)


class LineLimitTests(RepoFixture):
    def test_a_new_401_line_file_fails(self):
        self.file("big_module.py", 401)
        self.assertFails(self.check(), "big_module.py")

    def test_a_400_line_file_passes(self):
        self.file("edge_module.py", 400)
        self.assertPasses(self.check())

    def test_an_untracked_file_is_not_counted(self):
        self.write("loose_module.py", blob(501))  # never `git add`-ed
        self.assertPasses(self.check())

    def test_a_non_ascii_file_name_is_still_measured(self):
        # `git ls-files` quotes non-ASCII names (core.quotePath) and newline
        # splitting mangles them: the gate reads `ls-files -z` and splits on
        # NUL, or a file like this one is silently never measured.
        self.file("\u00fc.py", 401)
        self.assertFails(self.check(), "\u00fc.py")


class BaselineTests(RepoFixture):
    def test_a_baseline_file_growing_fails(self):
        self.file("legacy.py", 500)
        self.baseline("legacy.py 500\n")
        self.assertPasses(self.check())
        self.file("legacy.py", 510)
        self.assertFails(self.check(), "legacy.py")

    def test_a_baseline_file_shrinking_passes(self):
        self.file("legacy.py", 490)
        self.baseline("legacy.py 500\n")
        self.assertPasses(self.check())

    def test_the_baseline_document_may_shrink_but_never_grow(self):
        # The document is a ratchet, not a permission slip: a hand-raised count
        # or a hand-added entry is growth, and growth is refused wherever the
        # reference (origin/main, else HEAD) can be read.
        self.file("legacy.py", 480)
        self.baseline("legacy.py 500\n")
        self.git("add", "--", ".filesize-baseline")
        self.commit("grandfather legacy.py at 500")
        self.baseline("legacy.py 520\n")
        self.assertFails(self.check(), ".filesize-baseline")
        self.baseline("legacy.py 490\n")
        self.assertPasses(self.check())
        self.baseline("legacy.py 490\nother.py 500\n")
        self.assertFails(self.check(), ".filesize-baseline")

    def test_a_malformed_baseline_is_refused(self):
        self.file("legacy.py", 500)
        self.baseline("legacy.py not-a-count\n")
        self.assertFails(self.check(), ".filesize-baseline")


class UpdateBaselineTests(RepoFixture):
    def test_update_baseline_lowers_a_shrunk_count(self):
        self.file("legacy.py", 500)
        self.baseline("legacy.py 500\n")
        self.file("legacy.py", 470)
        self.assertPasses(self.check("--update-baseline"))
        self.assertEqual(self.baseline_map(), {"legacy.py": 470})

    def test_update_baseline_never_raises_a_grown_count(self):
        self.file("legacy.py", 500)
        self.baseline("legacy.py 500\n")
        self.file("legacy.py", 520)
        self.assertFails(self.check("--update-baseline"), "legacy.py")
        self.assertEqual(self.baseline_map(), {"legacy.py": 500})

    def test_update_baseline_never_adds_an_entry(self):
        self.file("legacy.py", 500)
        self.baseline("legacy.py 500\n")
        self.file("new_giant.py", 450)
        self.assertFails(self.check("--update-baseline"), "new_giant.py")
        self.assertEqual(self.baseline_map(), {"legacy.py": 500})

    def test_update_baseline_bootstraps_a_missing_baseline_once(self):
        # "generated once from today's tree": the first --update-baseline writes
        # the amnesty list for what is already over the line; everything after
        # it is the ratchet above. A file that never needed amnesty gets no entry.
        self.file("legacy.py", 500)
        self.file("small.py", 200)
        self.assertPasses(self.check("--update-baseline"))
        self.assertEqual(self.baseline_map(), {"legacy.py": 500})

    def test_update_baseline_refuses_a_baseline_that_would_grow(self):
        # --update-baseline must not launder a hand-grown baseline: the
        # baseline-vs-reference check (no entry added, none raised) runs BEFORE
        # the write, and a refusal writes nothing at all - not even a rewritten
        # copy that drops the comment the author left in the file.
        self.file("legacy.py", 530)
        self.baseline("# grandfathered by hand\nlegacy.py 500\n")
        self.git("add", "--", ".filesize-baseline")
        self.commit("grandfather legacy.py at 500")
        path = self.repo / ".filesize-baseline"
        self.baseline("# raised by hand\nlegacy.py 520\n")
        before = path.read_bytes()
        self.assertFails(self.check("--update-baseline"), "raised")
        self.assertEqual(path.read_bytes(), before)
        self.baseline("# entry added by hand\nlegacy.py 500\nother.py 500\n")
        before = path.read_bytes()
        self.assertFails(self.check("--update-baseline"), "added")
        self.assertEqual(path.read_bytes(), before)


class ExclusionTests(RepoFixture):
    def test_excluded_patterns_are_ignored(self):
        # `third_party/` beside `vendor/`: AutoOS's vendored dir is spelled
        # third_party (AGENTS.md) and updated wholesale, which the gate must
        # never measure.
        for rel in ("a.lock", "data.json", "logo.svg", "app.min.js",
                    "vendor/vendored.py", "node_modules/dep.js",
                    "third_party/vendored.py"):
            self.file(rel, 500)
        self.assertPasses(self.check())

    def test_an_allow_entry_with_a_reason_exempts_a_file(self):
        self.file("legacy_dump.py", 500)
        self.write(".filesize-allow",
                   "legacy_dump.py kept verbatim: a recorded fixture dump\n")
        self.assertPasses(self.check())

    def test_an_allow_entry_without_a_reason_is_refused(self):
        self.file("legacy_dump.py", 500)
        self.write(".filesize-allow", "legacy_dump.py\n")
        self.assertFails(self.check(), "reason")

    def test_binary_content_is_not_measured(self):
        # A NUL byte in the first 8 KiB marks binary content: a line count
        # over an image or a packed blob is not a source-size signal, so the
        # gate skips the file and says how many it skipped.
        self.write("payload_module.py", "\0" + blob(401))
        self.git("add", "--", "payload_module.py")
        run = self.check()
        self.assertPasses(run)
        self.assertIn("1 binary file(s) skipped", run.stdout)
        self.write("payload_module.py", blob(401))  # same shape, now text
        self.assertFails(self.check(), "payload_module.py")


class GateHookTests(unittest.TestCase):
    """Spec (3): the repo's hook setup runs the checker.

    The hook the repo installs (trust_worktree.py's pre-push shim) execs
    `tools/prepush.py` and nothing else, so "the hook runs it" is true exactly
    when the gate's own command list carries the checker.
    """

    def test_the_pre_push_gate_runs_the_file_size_checker(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "prepush_gate_for_filesize", ROOT / "tools" / "prepush.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        cmds = [argv for argv, _ in gate.commands_for(str(ROOT), {},
                                                      sys.executable)]
        self.assertIn([sys.executable, "tools/check-file-size.py"], cmds)


class RealTreeTests(unittest.TestCase):
    def test_the_real_tree_passes(self):
        """The gate's own checkout passes its own gate — checked-in baseline
        entries cover today's giants, and nothing in the tree outgrew one."""
        probe = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                               cwd=str(ROOT), capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("git cannot see this checkout (%s): a Windows-"
                          "created worktree read from WSL, not a checker "
                          "result" % probe.stderr.strip())
        run = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(ROOT),
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0,
                         "the checked-in tree must pass its own gate:\n"
                         + run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
