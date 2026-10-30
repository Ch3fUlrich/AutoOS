#!/usr/bin/env python3
"""Guard: the routing-registry skill's commands name only what the tools take.

A skill is documentation with a contract: an agent runs the commands it names,
so a command that fails when it is run is the skill lying. This file asserts
the command ways the routing-registry skill can lie:

    * an apply.sh / apply.ps1 invocation names a flag the script's own
      argument handling refuses (apply.sh exits 2 on unknown arguments, so
      such a command fails when it is run);
    * a `render <target> --out <path>` claim names the file that target's own
      `render <target> --help` calls "never the real ..." — writing the render
      there drops the rest of the file (an eval agent following step 2 emptied
      config.yaml and docs/models.md), so the skill must never name that form;
      `render ide --out catalog/ide-models.json`, the one sanctioned render
      write, is pinned to stay named and allowed.

The filesystem ways — budgets, link resolution, the retired predecessor,
wrapped repo paths, script paths and required steps — live in
tests/test_skill_routing_registry.py; shared constants and helpers live in
tests/routing_registry_skill_lib.py.

Run directly:

    python3 tests/test_skill_routing_registry_commands.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from routing_registry_skill_lib import (  # noqa: E402
    APPLY_PS1, APPLY_SH, ROOT, _apply_ps1_flags, _apply_sh_flags,
    _invocation_units, _never_the_real, _out_violations, _render_help,
    _render_out_mentions, _render_targets, _skill_files, _unit_flags)

class RoutingRegistrySkillTests(unittest.TestCase):
    def test_every_apply_invocation_flag_is_handled_by_the_script(self):
        """Every flag the skill names in an apply.sh / apply.ps1 invocation
        must appear in that script's argument handling."""
        handled = {APPLY_SH: _apply_sh_flags(), APPLY_PS1: _apply_ps1_flags()}
        problems = [
            "%s: %s names %s" % (p.relative_to(ROOT), flag, script.name)
            for p in _skill_files()
            for unit in _invocation_units(p)
            for script, flag in _unit_flags(unit)
            if flag not in handled[script]]
        self.assertEqual(
            problems, [],
            "flag(s) named in the skill's apply invocations that the script's "
            "argument handling does not accept: %s" % "; ".join(problems))

    def test_the_apply_flag_check_is_not_vacuous(self):
        """If a parser stops matching the script's argument handling, every
        flag above would pass vacuously — pin the parse and the failure shape."""
        self.assertTrue(
            {"--dry-run", "--probe", "--drift", "--drift-against",
             "--allow-drift", "--go", "--go-sha", "--go-offline"}
            <= _apply_sh_flags(),
            "apply.sh argument loop not parsed (empty or partial)")
        self.assertTrue(
            {"-DryRun", "-Probe", "-Go", "-GoSha", "-GoOffline"}
            <= _apply_ps1_flags(),
            "apply.ps1 param() block not parsed (empty or partial)")
        self.assertEqual(
            _unit_flags("bash configuration/omniroute/apply.sh --dry-runn"),
            [(APPLY_SH, "--dry-runn")])
        self.assertNotIn("--dry-runn", _apply_sh_flags())
        self.assertEqual(
            _unit_flags("bash configuration/omniroute/apply.sh --go-shaX"),
            [(APPLY_SH, "--go-shaX")],
            "a malformed flag must be read as one claim, not as --go-sha")
        self.assertNotIn("--go-shaX", _apply_sh_flags())
        self.assertEqual(
            _unit_flags("configuration/omniroute/apply.ps1 -DryRunX"),
            [(APPLY_PS1, "-DryRunX")])
        self.assertNotIn("-DryRunX", _apply_ps1_flags())
        self.assertEqual(
            _unit_flags("apply.sh -DryRun"), [(APPLY_SH, "-DryRun")],
            "a PowerShell-style flag in a bash twin's invocation belongs to "
            "the bash twin, which refuses it")

    def test_no_render_out_names_a_never_the_real_file(self):
        """Every `render <target> --out <path>` the skill names — fenced or
        inline — is checked against that target's own `render --help`: a path
        the help calls "never the real ..." is the committed file the tool
        refuses to own, and a render written there drops the rest of the file
        (an eval agent following step 2 emptied config.yaml (-266 lines) and
        docs/models.md (-848 lines, markers gone)). Each fixture form is
        flagged first — a guard that cannot fail asserts nothing."""
        for name, fixture in [
                ("omniroute/combos.json",
                 "run `python tools/registry.py render omniroute --out "
                 "configuration/omniroute/combos.json`;"),
                ("litellm/config.yaml",
                 "fix drift with render litellm --out "
                 "configuration/litellm/config.yaml"),
                ("models-doc",
                 "render models-doc --out docs/models.md"),
        ]:
            self.assertTrue(
                _out_violations(_render_out_mentions(fixture)),
                "guard missed the never-the-real fixture %s: %r"
                % (name, fixture))
        problems = ["%s: render %s --out %s (help: never the real %s)"
                    % (p.relative_to(ROOT), t, path, named)
                    for p in _skill_files()
                    for t, path, named in _out_violations(
                        _render_out_mentions(p.read_text(encoding="utf-8")))]
        self.assertEqual(
            problems, [],
            "the skill names a --out onto a file that render's own --help "
            "calls 'never the real': point --out at a scratch path and bring "
            "the committed file back in line through its sanctioned updater "
            "instead: %s" % "; ".join(problems))

    def test_render_ide_out_stays_allowed(self):
        """`render ide --out catalog/ide-models.json` is the one render write
        a committed file may take — its own --help says "to update the
        committed file". The guard must keep passing it and the skill must
        keep naming it, or the sanctioned path silently disappears."""
        self.assertNotEqual(
            _never_the_real("ide"), "catalog/ide-models.json",
            "render ide's --help now refuses catalog/ide-models.json; the "
            "skill's sanctioned ide write is stale")
        self.assertEqual(
            _out_violations([("ide", "catalog/ide-models.json")]), [],
            "the guard flags the one sanctioned render write")
        named = [m for p in _skill_files()
                 for m in _render_out_mentions(
                     p.read_text(encoding="utf-8"))
                 if m == ("ide", "catalog/ide-models.json")]
        self.assertTrue(
            named,
            "the skill no longer names `render ide --out "
            "catalog/ide-models.json`, the sanctioned way to update it")
        self.assertIn(
            "to update the committed file: --out catalog/ide-models.json",
            _render_help("ide"),
            "render ide's --help no longer sanctions the committed write; "
            "the skill's ide --out is unsanctioned")

    def test_every_target_but_ide_carries_its_refusal(self):
        """The guard reads each target's refusal from its help, so a target
        whose help names no "never the real" file is one it cannot see: its
        `--out` claims would all pass. Only ide, whose write is sanctioned,
        may lack it — a new target must state its refusal (or join ide)."""
        targets = _render_targets()
        self.assertIn("ide", targets, "render --help target list not parsed")
        self.assertEqual(
            [t for t in targets if _never_the_real(t) is None], ["ide"],
            "render target(s) whose --help names no 'never the real' file: "
            "the --out guard is blind to them")

    def test_a_failing_help_fails_loudly(self):
        """A help that cannot run must not read as "no refusal"."""
        with self.assertRaises(AssertionError):
            _render_help("no-such-target")


if __name__ == "__main__":
    unittest.main(verbosity=2)
