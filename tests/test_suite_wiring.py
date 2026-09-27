#!/usr/bin/env python3
"""Guard: every tests/test_*.py is wired into at least one suite entry point.

A unit test file that no harness runs is a silent no-op: it can rot for years
and still "pass", because nothing ever executes it. This asserts that each
``tests/test_*.py`` basename is named by a Linux suite file, the Windows suite,
or a CI workflow - the three places a new unit test can be registered.

A new harness must be added to ``WIRING`` below, or its tests stay invisible.

Run directly:

    python3 tests/test_suite_wiring.py
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
WORKFLOWS = ROOT / ".github" / "workflows"

# Where a unit test can be wired. Missing entries are skipped rather than
# failing, so this stays usable on a partial checkout.
WIRING = ([p for p in sorted((TESTS / "linux").glob("*.sh"))] +
          [TESTS / "run-tests.ps1"] +
          [p for p in sorted(WORKFLOWS.glob("*.yml"))])


class SuiteWiringTests(unittest.TestCase):
    def test_every_unit_test_file_is_wired_into_a_suite(self):
        haystack = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                             for p in WIRING if p.is_file())
        unwired = [p.name for p in sorted(TESTS.glob("test_*.py"))
                   if p.name not in haystack]
        self.assertEqual(
            unwired, [],
            "unit test file(s) no harness runs: %s (wire each into %s)"
            % (", ".join(unwired), ", ".join(str(p.relative_to(ROOT)) for p in WIRING)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
