#!/usr/bin/env python3
"""Guard: every tests/test_*.py is wired into at least one suite entry point.

A unit test file that no harness runs is a silent no-op: it can rot for years
and still "pass", because nothing ever executes it. This asserts that each
``tests/test_*.py`` basename is named by a Linux suite file, the Windows suite,
or a CI workflow - the three places a new unit test can be registered.

A new harness must be added to ``WIRING`` below, or its tests stay invisible.

Second guard (AO-ADMISSION-2-HERMETIC): no ``tests/test_*.py`` may open the live
host's memory file. A unit test that measures the machine it happens to run on is
green on CI and red on a busy workstation, for a reason no test is about.

Run directly:

    python3 tests/test_suite_wiring.py
"""
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
WORKFLOWS = ROOT / ".github" / "workflows"

# AO-ADMISSION-2-HERMETIC: the kernel's memory file, spelled in two pieces so this
# guard's own source never holds it as one quoted literal beside an `open(` — a
# matcher that named the whole path complete would trip the test it writes. The
# optional `" + "` in the argument is why the split spelling is caught too; a
# *variable* holding the path cannot be, which is what tests/_host_state.py is for.
HOST_MEMINFO = "/proc" + "/meminfo"
HOST_MEMINFO_READ_RE = re.compile(
    r"(?:\bos\.open|\bio\.open|\bopen|\bPath)\s*\(\s*[\"']/proc"
    r"(?:[\"']\s*\+\s*[\"'])?/meminfo[\"']")


def _meminfo_read_lines(text):
    """Every line number in `text` that opens the live host's memory file."""
    return [n for n, line in enumerate(text.splitlines(), 1)
            if HOST_MEMINFO_READ_RE.search(line)]


def _workflow_files(workflows=WORKFLOWS):
    """Both GitHub spellings: a `.yaml` workflow is as valid as a `.yml` one."""
    return sorted(list(workflows.glob("*.yml")) + list(workflows.glob("*.yaml")))


# Where a unit test can be wired. Missing entries are skipped rather than
# failing, so this stays usable on a partial checkout.
def _wiring(tests=TESTS, workflows=WORKFLOWS):
    return ([p for p in sorted((tests / "linux").glob("*.sh"))] +
            [tests / "run-tests.ps1"] + _workflow_files(workflows))


WIRING = _wiring()


def _run_shape(path, name):
    """True when `path` *runs* `name`, not merely mentions it.

    A mention is not a wiring: `# TODO run tests/test_foo.py` inside a suite
    must not mark a never-executed test as wired. Each harness gets its own
    run shape - `python3 tests/<name>` in a `.sh`, the quoted Join-Path form
    in run-tests.ps1 (both separators exist today), and a `python3`/`pytest`
    step for a workflow.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    escaped = re.escape(name)
    if path.suffix == ".sh":
        return re.search(r"python3\s+tests/%s\b" % escaped, text) is not None
    if path.suffix == ".ps1":
        return re.search(r"['\"]tests[\\/]%s['\"]" % escaped, text) is not None
    if path.suffix in (".yml", ".yaml"):
        return re.search(r"(?:python3|pytest)\b[^\n]*\btests/%s\b" % escaped,
                         text) is not None
    return False


def _wired_files(name, wiring=WIRING):
    return [p for p in wiring if p.is_file() and _run_shape(p, name)]


class SuiteWiringTests(unittest.TestCase):
    def test_every_unit_test_file_is_wired_into_a_suite(self):
        unwired = [p.name for p in sorted(TESTS.glob("test_*.py"))
                   if not _wired_files(p.name)]
        self.assertEqual(
            unwired, [],
            "unit test file(s) no harness runs: %s (wire each into a .sh suite "
            "with `python3 tests/<name>`, tests/run-tests.ps1's `'tests\\<name>'` "
            "Join-Path form, or a .github/workflows/*.yml|*.yaml step; a new "
            "suite directory is invisible until added to WIRING)"
            % ", ".join(unwired))

    def test_no_unit_test_opens_the_live_host_memory_file(self):
        """A test that reads the host's MemAvailable passes on an idle CI box and
        fails on a busy one. Measured 2026-10-10: a spawn in
        tests/test_autoos_heartbeat.py reached the real admission gate and came
        back "host admission: 0 live workers, cap 6; MemAvailable 4447 MB, floor
        4608 MB" four times in a day, for a reason that test was never about.
        tests/_host_state.py pins the same reads to fixtures through the seams
        tools/autoos-agent.py already honours (AUTOOS_MEMINFO_PATH,
        AUTOOS_WORKERS_DIR), so no test has a reason to open the kernel's own
        file. A *mention* is not a read — a comment, or a shell command string
        that only names the path, stays legal.
        """
        offenders = ["%s:%d" % (p.name, n)
                     for p in sorted(TESTS.glob("test_*.py"))
                     for n in _meminfo_read_lines(
                         p.read_text(encoding="utf-8", errors="replace"))]
        self.assertEqual(
            offenders, [],
            "unit test(s) opening the live host's memory file: %s (pin the read "
            "with tests/_host_state.py instead — host_state.install(self) names a "
            "fixture through AUTOOS_MEMINFO_PATH/AUTOOS_WORKERS_DIR, so the test "
            "measures a host it describes rather than the one it happens to run "
            "on)" % ", ".join(offenders))
        # The rule bites on the read shape, and only on it. The offending lines are
        # assembled at run time because this file is inside the glob the guard
        # scans — writing a read out in place would trip the rule it tests.
        whole = '"%s"' % HOST_MEMINFO
        split = "%s + %s" % ('"/proc"', '"/meminfo"')
        for arg in (whole, split):
            for opener in ("open", "io.open", "os.open", "Path"):
                self.assertEqual(_meminfo_read_lines("%s(%s)" % (opener, arg)), [1],
                                 "not caught as a live-host read: %s(%s)" % (opener, arg))
        self.assertEqual(_meminfo_read_lines("with io.open(HOST_MEMINFO) as fh:\n"), [],
                         "a variable — which no grep can resolve — is not the literal")
        self.assertEqual(_meminfo_read_lines("# the live %s back\n" % HOST_MEMINFO
                                             + 'for cmd in ("cat %s",)\n' % HOST_MEMINFO),
                         [], "a mention is not a read")

    def test_a_comment_only_mention_is_not_a_wiring(self):
        with tempfile.TemporaryDirectory() as tmp:
            sh = Path(tmp) / "suite.sh"
            sh.write_text("# TODO run tests/test_never_run.py\n", encoding="utf-8")
            self.assertEqual(_wired_files("test_never_run.py", [sh]), [])

    def test_a_run_shape_is_a_wiring(self):
        with tempfile.TemporaryDirectory() as tmp:
            sh = Path(tmp) / "suite.sh"
            sh.write_text('out="$(python3 tests/test_something.py 2>&1)"\n',
                          encoding="utf-8")
            self.assertEqual(_wired_files("test_something.py", [sh]), [sh])

    def test_both_ps1_separators_are_a_wiring(self):
        with tempfile.TemporaryDirectory() as tmp:
            back = Path(tmp) / "back.ps1"
            fwd = Path(tmp) / "fwd.ps1"
            back.write_text("& $py.Source (Join-Path $Root 'tests\\test_x.py')",
                            encoding="utf-8")
            fwd.write_text("& $py.Source (Join-Path $Root 'tests/test_x.py')",
                           encoding="utf-8")
            self.assertEqual(_wired_files("test_x.py", [back, fwd]), [back, fwd])

    def test_yaml_workflows_are_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "workflows"
            wf.mkdir()
            (wf / "a.yml").write_text("", encoding="utf-8")
            (wf / "b.yaml").write_text("", encoding="utf-8")
            self.assertEqual([p.name for p in _workflow_files(wf)],
                             ["a.yml", "b.yaml"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
