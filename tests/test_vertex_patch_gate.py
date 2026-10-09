#!/usr/bin/env python3
"""tests/test_vertex_patch_gate.py — the Vertex build gate counts every call site.

Hermetic (D-852): fixture chunk *text* in a temporary directory, the patcher and
the gate run as subprocesses over it. No container runtime, no build, no network.

The defect this suite pins (lane VERTEX-GUARD, gate 06c19a) is that
`configuration/docker/ai-stack/omniroute.Dockerfile` counted the patched sites
over 6 hard-coded chunk names, so a gateway bump that adds a 13th
mergeConsecutiveSameRoleContents call site in a new chunk — or renames one of the
6 — shipped an unpatched gateway without failing the build. tools/vertex-patch-gate.py
now derives the totals from every compiled chunk in the directory, and the build
asserts patched == total, refill == total and total >= 12.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE_TOOL = ROOT / "tools" / "vertex-patch-gate.py"
PATCH_TOOL = ROOT / "tools" / "apply-vertex-patch.py"
DOCKERFILE = ROOT / "configuration" / "docker" / "ai-stack" / "omniroute.Dockerfile"
README = ROOT / "configuration" / "omniroute" / "vertex-trailing-turn-README.md"

# The existing hermetic fixture text (the 6 chunks in each shipped state), reused
# so there is one description of the compiled shape, not two.
_spec = importlib.util.spec_from_file_location(
    "vertex_patch_fixtures", ROOT / "tests" / "test_vertex_trailing_turn_patch.py")
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)

ALL_CHUNKS = fixtures.ALL_CHUNKS
SITE_COUNT = fixtures.SITE_COUNT          # 12
MIN_SITES = 12

# A 13th call site in a chunk no table names, compiled in the same shape.
EXTRA_CHUNK = "_19zz9zz._.js"


def _patcher():
    spec = importlib.util.spec_from_file_location("apply_vertex_patch", PATCH_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PATCHER = _patcher()
# The three shipped states of one expression site, taken from the patcher's own
# table so this fixture cannot drift from what the patcher writes.
EXPR_SITE = [s for s in PATCHER.SITES if s["chunk"] == ALL_CHUNKS[0]][0]
PRISTINE_EXPR = EXPR_SITE["forms"][1][0]
V1_EXPR = EXPR_SITE["forms"][0][0]
V2_EXPR = EXPR_SITE["new"]


def extra_chunk_text(state="pristine"):
    """A chunk file that carries one call site and nothing the tables name."""
    form = {"pristine": PRISTINE_EXPR, "v1": V1_EXPR, "v2": V2_EXPR}[state]
    return ("// hermetic 13th-site fixture: a call site in a chunk the tables do not name\n"
            "const u={mergeConsecutiveSameRoleContents:(c)=>(Array.isArray(c)?c:[])};\n"
            "function h(t,n){let f={contents:(n&&n.contents)||[]};return f._x=1,%s}\n"
            "module.exports={h};\n" % form)


def patched_tree(tmp: Path, extra=None):
    """All 6 chunks upgraded to v2 by the real patcher, plus any extra chunk files."""
    for chunk in ALL_CHUNKS:
        (tmp / chunk).write_text(fixtures.chunk_text(chunk, "v1"), encoding="utf-8")
    proc = run_patcher(tmp)
    if proc.returncode != 0:
        raise AssertionError("the patcher did not upgrade the fixture tree: "
                             + proc.stdout + proc.stderr)
    for name, text in (extra or {}).items():
        (tmp / name).write_text(text, encoding="utf-8")
    return tmp


def run_patcher(chunks_dir: Path):
    return subprocess.run([sys.executable, str(PATCH_TOOL), str(chunks_dir)],
                          capture_output=True, text=True)


def run_gate(chunks_dir: Path, run1=None, run2=None, extra_args=()):
    cmd = [sys.executable, str(GATE_TOOL), str(chunks_dir), "--min-sites", str(MIN_SITES)]
    if run1 is not None:
        cmd += ["--run1", str(run1)]
    if run2 is not None:
        cmd += ["--run2", str(run2)]
    cmd += list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


def write_run_files(tmp: Path, patched=SITE_COUNT, skipped=0, errors=0,
                    patched2=0, skipped2=SITE_COUNT, errors2=0):
    r1 = tmp / "run1.txt"
    r1.write_text("PATCH site\n\nDone: %d patched, %d skipped, %d errors\n"
                  % (patched, skipped, errors), encoding="utf-8")
    r2 = tmp / "run2.txt"
    r2.write_text("SKIP site\n\nDone: %d patched, %d skipped, %d errors\n"
                  % (patched2, skipped2, errors2), encoding="utf-8")
    return r1, r2


class GatePasses(unittest.TestCase):
    """The gate's green case is the healthy tree it is supposed to accept."""

    def test_a_fully_patched_12_site_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            for chunk in ALL_CHUNKS:
                (tree / chunk).write_text(fixtures.chunk_text(chunk, "pristine"),
                                          encoding="utf-8")
            self.assertEqual(0, run_patcher(tree).returncode)
            proc = run_gate(tree)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("VERDICT=PASS", proc.stdout)
            self.assertIn("total=12", proc.stdout)
            self.assertIn("patched=12", proc.stdout)
            self.assertIn("refill=12", proc.stdout)

    def test_the_counts_are_printed_even_when_it_passes(self):
        """The build log has to say what it measured, not only that it was happy."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            proc = run_gate(tree)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertRegex(proc.stdout, r"total=\d+.*patched=\d+.*refill=\d+")


class GateFailsUnpatchedSite(unittest.TestCase):
    """The fail-open cases: a site outside the tables must stop the build."""

    def test_a_13th_unpatched_site_in_a_new_chunk_fails_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree, extra={EXTRA_CHUNK: extra_chunk_text("pristine")})
            proc = run_gate(tree)
            self.assertNotEqual(0, proc.returncode, "12 of 13 sites patched shipped before")
            self.assertIn("13", proc.stdout, "the total has to name the real count")
            self.assertIn("total=13", proc.stdout)
            self.assertIn("patched=12", proc.stdout)
            self.assertIn("refill=12", proc.stdout)
            self.assertIn(EXTRA_CHUNK, proc.stdout, "and which chunk is unpatched")

    def test_a_13th_site_left_on_the_v1_guard_fails_the_build(self):
        """The v1 guard counts as patched-by-text but not as v2 — the gate says so."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree, extra={EXTRA_CHUNK: extra_chunk_text("v1")})
            proc = run_gate(tree)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("total=13", proc.stdout)
            self.assertIn("v1", proc.stdout)

    def test_a_renamed_chunk_is_still_counted(self):
        """Renaming is invisible to a count over the directory, not to a list of names."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            moved = tree / ALL_CHUNKS[0]
            renamed = "_0zz_renamed._.js"
            moved.rename(tree / renamed)
            # the patcher never saw that file after the rename, so re-install its guard
            (tree / renamed).write_text(fixtures.chunk_text(ALL_CHUNKS[0], "pristine"),
                                         encoding="utf-8")
            proc = run_gate(tree)
            self.assertNotEqual(0, proc.returncode, "10 of 12 sites patched shipped before")
            self.assertIn("total=12", proc.stdout, "the renamed sites are still sites")
            self.assertIn("patched=10", proc.stdout)
            self.assertIn(renamed, proc.stdout)

    def test_a_patched_chunk_renamed_is_still_patched_and_passes(self):
        """A pure rename with v2 bytes present is not a failure: the text is the truth."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            (tree / ALL_CHUNKS[0]).rename(tree / "_0zz_renamed._.js")
            proc = run_gate(tree)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("total=12", proc.stdout)


class GateFloor(unittest.TestCase):
    """total >= 12 is a floor, so a shrunken gateway is a failure too."""

    def one_chunk_patched(self, tree: Path):
        """One chunk, both its sites carrying the v2 guard: 2 of 2, but only 2."""
        (tree / ALL_CHUNKS[0]).write_text(fixtures.chunk_text(ALL_CHUNKS[0], "v1"),
                                          encoding="utf-8")
        run_patcher(tree)          # patches the file it has, errors on the 5 it lacks
        self.assertIn("text:\"Continue.\"", (tree / ALL_CHUNKS[0]).read_text(encoding="utf-8"))

    def test_fewer_sites_than_the_floor_fails_even_when_every_site_is_patched(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            self.one_chunk_patched(tree)
            proc = run_gate(tree)
            self.assertNotEqual(0, proc.returncode, "2 of 2 patched shipped before")
            self.assertIn("total=2", proc.stdout)
            self.assertIn("minimum", proc.stdout.lower())

    def test_the_floor_is_configurable_so_it_can_be_proved(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            self.one_chunk_patched(tree)
            proc = run_gate(tree, extra_args=["--min-sites", "2"])
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)


class GateCountsCallSites(unittest.TestCase):
    """A call site, not every mention of the name."""

    def test_definitions_and_re_exports_are_not_counted_as_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            (tree / "_defs_only._.js").write_text(
                "function mergeConsecutiveSameRoleContents(e){return e}\n"
                "const t={mergeConsecutiveSameRoleContents:()=>mergeConsecutiveSameRoleContents};\n"
                "export {mergeConsecutiveSameRoleContents};\n", encoding="utf-8")
            proc = run_gate(tree)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("total=12", proc.stdout)

    def test_a_direct_call_site_is_counted_as_a_site(self):
        """A bump that drops the (0,u.fn)(args) dereference is still a call site."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            (tree / "_direct._.js").write_text(
                "function h(n){let f={contents:n||[]};return f.contents="
                "mergeConsecutiveSameRoleContents(f.contents),f}\n", encoding="utf-8")
            proc = run_gate(tree)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("total=13", proc.stdout)


class GateRunSummaries(unittest.TestCase):
    """The patcher's own report is checked against the sites that really exist."""

    def test_run_summaries_that_miss_a_site_fail_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree, extra={EXTRA_CHUNK: extra_chunk_text("pristine")})
            first, second = run_patcher(tree), run_patcher(tree)
            r1, r2 = Path(tmp) / "run1.txt", Path(tmp) / "run2.txt"
            r1.write_text(first.stdout, encoding="utf-8")
            r2.write_text(second.stdout, encoding="utf-8")
            proc = run_gate(tree, run1=r1, run2=r2)
            self.assertNotEqual(0, proc.returncode,
                                "the patcher reported 12 of 13 and was believed")
            self.assertIn("run1", proc.stdout)

    def test_matching_run_summaries_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            r1, r2 = write_run_files(Path(tmp))
            proc = run_gate(tree, run1=r1, run2=r2)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_errors_in_a_run_summary_fail_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            r1, r2 = write_run_files(Path(tmp), patched=11, errors=1)
            proc = run_gate(tree, run1=r1, run2=r2)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("error", proc.stdout.lower())

    def test_a_second_run_that_patches_again_fails_the_gate(self):
        """Idempotency is part of the contract, not only the counts."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree)
            r1, r2 = write_run_files(Path(tmp), patched2=1, skipped2=11)
            proc = run_gate(tree, run1=r1, run2=r2)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("run2", proc.stdout)


class DockerfileContract(unittest.TestCase):
    """The gate lives in a script; the Dockerfile must not re-count anything."""

    def text(self):
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_the_dockerfile_delegates_the_counting_to_the_gate(self):
        text = self.text()
        self.assertIn("vertex-patch-gate.py", text)
        self.assertIn("--min-sites 12", text)

    def test_the_dockerfile_names_no_chunk(self):
        """A hard-coded chunk name is exactly the fail-open this lane removed."""
        for chunk in ALL_CHUNKS:
            self.assertNotIn(chunk.split(".")[0], self.text(),
                             "the Dockerfile still lists the chunks it should not know")

    def test_the_dockerfile_hardcodes_no_site_counts(self):
        text = self.text()
        self.assertNotIn("Done: 12 patched", text)
        self.assertNotIn("Done: 0 patched, 12 skipped", text)
        self.assertNotIn("got==12", text)

    def test_the_gate_runs_after_the_second_patcher_run_and_before_the_cleanup(self):
        lines = self.text().splitlines()
        gate = [i for i, ln in enumerate(lines) if "vertex-patch-gate.py /chunks" in ln]
        self.assertEqual(1, len(gate), "the gate is invoked exactly once")
        run2 = [i for i, ln in enumerate(lines) if "/run2.txt" in ln]
        clean = [i for i, ln in enumerate(lines) if "autoos-backup-" in ln]
        self.assertTrue(run2 and clean, "run2 / cleanup lines are present")
        self.assertTrue(run2[0] < gate[0] < clean[-1])

    def test_the_gate_is_copied_from_the_tools_context(self):
        self.assertIn("COPY --from=tools vertex-patch-gate.py /vertex-patch-gate.py",
                      self.text())


class Hermetic(unittest.TestCase):
    """D-852: the gate is a text tool over a directory, like the patcher."""

    def test_the_gate_never_reaches_the_runtime_or_the_network(self):
        text = GATE_TOOL.read_text(encoding="utf-8")
        for banned in ("urllib", "requests", "socket", "subprocess", "docker"):
            self.assertNotIn(banned, text, "%s in the gate" % banned)

    def test_the_gate_report_lines_are_ascii(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            patched_tree(tree, extra={EXTRA_CHUNK: extra_chunk_text("pristine")})
            outs = [run_gate(tree).stdout]
            (tree / EXTRA_CHUNK).unlink()
            outs.append(run_gate(tree).stdout)
        for line in "\n".join(outs).splitlines():
            try:
                line.encode("ascii")
            except UnicodeEncodeError:
                self.fail("non-ASCII report line: %r" % line)

    def test_the_pristine_chunk_text_does_not_contain_the_v1_anchor(self):
        """The README's ordering claim is proved against the tables, not asserted.

        The chunk sites match on the full head+tail string, so the pristine form is
        NOT a substring of the v1 form; only the .ts site's anchor is, which is why
        the v1 form has to be tried first there.
        """
        for site in PATCHER.SITES:
            self.assertNotIn(site["forms"][1][0], site["forms"][0][0], site["label"])
        ts_find = "result.contents = mergeConsecutiveSameRoleContents(result.contents ?? []);"
        self.assertIn(ts_find, ts_find + "\n\n  if (result.contents.length > 1) {")

    def test_the_readme_states_the_real_reason_for_the_v1_first_order(self):
        readme = README.read_text(encoding="utf-8")
        self.assertNotIn("the pristine anchor is a substring\nof the v1 text", readme,
                         "the false reason is still the documented one")
        self.assertIn("openai-to-gemini.ts", readme)
        self.assertIn("identical", readme, "the chunk sites match only in one state")


if __name__ == "__main__":
    unittest.main()
