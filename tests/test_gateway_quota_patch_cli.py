#!/usr/bin/env python3
"""tests/test_gateway_quota_patch_cli.py — CLI tests for gateway quota patcher."""

import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH_TOOL = ROOT / "tools" / "apply-gateway-quota-patch.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "quota_cache_chunk_fixture.js"

sys.path.insert(0, str(ROOT / "tools"))
patch_tool_mod = importlib.import_module("apply-gateway-quota-patch")
CHUNK_DEFS = patch_tool_mod.CHUNK_DEFS
get_chunk_replacements = patch_tool_mod.get_chunk_replacements
ORIG_EXHAUSTION_ANCHOR = patch_tool_mod.ORIG_EXHAUSTION_ANCHOR
OLD_EXHAUSTION_PATCH = patch_tool_mod.OLD_EXHAUSTION_PATCH
NEW_EXHAUSTION_DECISION = patch_tool_mod.NEW_EXHAUSTION_DECISION


def make_synthetic_chunk(d: dict) -> str:
    return (
        "/* __omnirouteQuotaCacheState */\n"
        f"function isExhausted(p){{let s=Object.values(p);"
        f"return 0!==s.length&&s.every(e=>!1!==e.fractionReported&&e.remainingPercentage<=0)}}\n"
        f"function normalizeQuotas(p){{let s={{}};"
        f"for(let[r,n]of Object.entries(p))"
        f'n&&"object"==typeof n&&(s[r]={{remainingPercentage:'
        f"(0,c.safePercentage)({d['v1']}.remainingPercentage)??"
        f"({d['v1']}.total>0?Math.round(({d['v1']}.total-({d['v1']}.used||0))/{d['v1']}.total*100):0),"
        f"resetAt:{d['v1']}.resetAt||null,fractionReported:!1!=={d['v1']}.fractionReported&&void 0}});"
        f"return s}}\n"
        f"function setQuotaCache(p1,p2,p3){{"
        f"let r=(0,c.safePercentage)({d['v2']}.remainingPercentage)??"
        f"({d['v2']}.total>0?Math.round(({d['v2']}.total-({d['v2']}.used||0))/{d['v2']}.total*100):0);"
        f"let {d['exh_v']}={d['rem_v']}<=0;if(f({d['rem_v']},{d['exh_v']}))try{{save()}}catch(e){{}}\n"
        f"return r}}\n"
        f"function hydrate(p){{for(let e of p){{let r={{remainingPercentage:{d['reload_clamp']}(Number(e.remainingPercentage??e.remaining_percentage??0)),resetAt:null}};}}}}\n"
        f"function getQuotaWindowStatus(p){{"
        f"let {d['rem']}={d['clamp']}({d['w']}.remainingPercentage),{d['used']}={d['clamp']}(100-{d['rem']}),reset=null,{d['exp']}=!1;\n"
        f"return {{remainingPercentage:{d['rem']},usedPercentage:{d['used']},reachedThreshold:!{d['exp']}&&!1!=={d['w']}.fractionReported&&({d['rem']}<=0||{d['used']}>={d['thresh']})}}\n"
        f"}}\n"
    )


def make_old_patched_chunk(fname: str, d: dict) -> str:
    content = FIXTURE_PATH.read_text(encoding="utf-8") if fname == "_0brmz9y._.js" else make_synthetic_chunk(d)
    repls, _ = get_chunk_replacements(fname, d)
    content = content.replace(repls[0][0], repls[0][1], 1)
    content = content.replace(repls[1][0], repls[1][1], 1)
    content = content.replace(ORIG_EXHAUSTION_ANCHOR, OLD_EXHAUSTION_PATCH, 1)
    return content


def setup_chunks_dir(dest: Path, old_patched: bool = False, crlf: bool = False) -> dict[str, str]:
    hashes = {}
    for fname, d in CHUNK_DEFS.items():
        if old_patched:
            content = make_old_patched_chunk(fname, d)
        elif fname == "_0brmz9y._.js":
            content = FIXTURE_PATH.read_text(encoding="utf-8")
        else:
            content = make_synthetic_chunk(d)
        if crlf:
            content = content.replace("\n", "\r\n")
        fpath = dest / fname
        fpath.write_bytes(content.encode("utf-8"))
        hashes[fname] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return hashes


def get_file_hashes(dir_path: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dir_path.iterdir() if p.is_file()}


class GatewayQuotaPatchCliTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATCH_TOOL.is_file(), f"Patch tool missing: {PATCH_TOOL}")
        self.assertTrue(FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}")
        self.assertLessEqual(FIXTURE_PATH.stat().st_size, 40960, "Fixture must be <= 40 KB")

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(PATCH_TOOL)] + list(args), capture_output=True, text=True)

    def test_apply_once_and_all_files_changed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            before = setup_chunks_dir(tmp)
            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Done: 12 patched, 0 skipped, 0 errors", proc.stdout)
            after = get_file_hashes(tmp)
            for fname in CHUNK_DEFS:
                self.assertIn(f"PATCH {fname}: 7 replacement(s) applied", proc.stdout)
                self.assertNotEqual(before[fname], after[fname])
            backups = [p for p in tmp.iterdir() if ".autoos-backup-" in p.name]
            self.assertEqual(len(backups), 12)

    def test_second_run_skips_all_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            self.assertEqual(self.run_tool("--root", str(tmp)).returncode, 0)
            before_hashes = get_file_hashes(tmp)
            before_files = set(tmp.iterdir())
            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Done: 0 patched, 12 skipped, 0 errors", proc.stdout)
            for fname in CHUNK_DEFS:
                self.assertIn(f"SKIP  {fname}: already patched", proc.stdout)
            self.assertEqual(before_hashes, get_file_hashes(tmp))
            self.assertEqual(before_files, set(tmp.iterdir()))

    def test_check_and_dry_run_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            before = setup_chunks_dir(tmp)
            for flag in ["--check", "--dry-run"]:
                proc = self.run_tool("--root", str(tmp), flag)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("Done: 12 would be patched, 0 skipped, 0 errors", proc.stdout)
                self.assertEqual(before, get_file_hashes(tmp))
                self.assertEqual([p for p in tmp.iterdir() if ".autoos-backup-" in p.name], [])
                self.assertEqual([p for p in tmp.iterdir() if ".autoos-tmp-" in p.name], [])

    def test_backup_and_no_backup_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            proc = self.run_tool("--root", str(tmp), "--no-backup")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual([p for p in tmp.iterdir() if ".autoos-backup-" in p.name], [])

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            proc = self.run_tool("--root", str(tmp), "--no-backup", "--backup")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len([p for p in tmp.iterdir() if ".autoos-backup-" in p.name]), 12)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            proc = self.run_tool("--root", str(tmp), "--backup", "--no-backup")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual([p for p in tmp.iterdir() if ".autoos-backup-" in p.name], [])

    def test_unknown_flags_on_valid_dir_exit_2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            before = setup_chunks_dir(tmp)
            for flag in ["--bogus", "--dryrun", "--no-backups"]:
                proc = self.run_tool("--root", str(tmp), flag)
                self.assertEqual(proc.returncode, 2, f"{flag} should exit 2")
                self.assertIn("unrecognized argument", proc.stderr.lower())
                self.assertEqual(before, get_file_hashes(tmp))
                self.assertEqual([p for p in tmp.iterdir() if ".autoos-backup-" in p.name], [])

    def test_missing_empty_nonexistent_root_exit_1(self):
        proc1 = self.run_tool()
        self.assertEqual(proc1.returncode, 1)
        self.assertIn("ERROR: --root is required and must not be empty.", proc1.stderr)

        proc2 = self.run_tool("--root", "")
        self.assertEqual(proc2.returncode, 1)
        self.assertIn("ERROR: --root is required and must not be empty.", proc2.stderr)

        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent = Path(tmpdir) / "does_not_exist"
            proc3 = self.run_tool("--root", str(nonexistent))
            self.assertEqual(proc3.returncode, 1)
            self.assertIn("ERROR: chunks directory not found:", proc3.stderr)

    def test_missing_or_doubled_anchor_aborts_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            fpath = tmp / "_0brmz9y._.js"
            fpath.write_text(fpath.read_text(encoding="utf-8").replace(ORIG_EXHAUSTION_ANCHOR, ""), encoding="utf-8")
            before = get_file_hashes(tmp)
            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("inconsistent state / missing anchors", proc.stderr)
            self.assertIn("ABORTED: no files were modified due to validation errors.", proc.stderr)
            self.assertEqual(before, get_file_hashes(tmp))
            self.assertEqual([p for p in tmp.iterdir() if ".autoos-backup-" in p.name], [])

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            fpath = tmp / "_0brmz9y._.js"
            fpath.write_text(fpath.read_text(encoding="utf-8") + ORIG_EXHAUSTION_ANCHOR, encoding="utf-8")
            before = get_file_hashes(tmp)
            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("inconsistent state / missing anchors", proc.stderr)
            self.assertIn("ABORTED: no files were modified due to validation errors.", proc.stderr)
            self.assertEqual(before, get_file_hashes(tmp))
            self.assertEqual([p for p in tmp.iterdir() if ".autoos-backup-" in p.name], [])

    def test_half_patched_detection_aborts_all(self):
        d = CHUNK_DEFS["_0brmz9y._.js"]
        repls, _ = get_chunk_replacements("_0brmz9y._.js", d)
        clean = FIXTURE_PATH.read_text(encoding="utf-8")

        v1 = clean.replace(repls[0][0], repls[0][1], 1)
        v2 = clean.replace(repls[1][0], repls[1][1], 1)
        v3 = clean.replace(ORIG_EXHAUSTION_ANCHOR, OLD_EXHAUSTION_PATCH, 1)
        v4 = clean.replace(ORIG_EXHAUSTION_ANCHOR, NEW_EXHAUSTION_DECISION, 1)
        v5 = clean
        for old_s, new_s in repls[:6]:
            v5 = v5.replace(old_s, new_s, 1)

        variants = [("r1", v1), ("r2", v2), ("r3_old", v3), ("r3_new", v4), ("r1_6", v5)]
        for label, content in variants:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                setup_chunks_dir(tmp)
                (tmp / "_0brmz9y._.js").write_text(content, encoding="utf-8")
                before = get_file_hashes(tmp)
                proc = self.run_tool("--root", str(tmp))
                self.assertEqual(proc.returncode, 1, f"Variant {label} should fail")
                self.assertIn("inconsistent state / missing anchors", proc.stderr)
                self.assertIn("ABORTED: no files were modified", proc.stderr)
                self.assertEqual(before, get_file_hashes(tmp))
                self.assertEqual([p for p in tmp.iterdir() if ".autoos-backup-" in p.name], [])

    def test_one_patched_eleven_originals_converges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            chunk = tmp / "_0brmz9y._.js"
            c = chunk.read_text(encoding="utf-8")
            repls, _ = get_chunk_replacements("_0brmz9y._.js", CHUNK_DEFS["_0brmz9y._.js"])
            for old_s, new_s in repls:
                c = c.replace(old_s, new_s, 1)
            chunk.write_text(c, encoding="utf-8")
            patched_hash = hashlib.sha256(chunk.read_bytes()).hexdigest()

            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Done: 11 patched, 1 skipped, 0 errors", proc.stdout)
            self.assertIn("SKIP  _0brmz9y._.js: already patched", proc.stdout)
            self.assertEqual(hashlib.sha256(chunk.read_bytes()).hexdigest(), patched_hash)
            backups = [p for p in tmp.iterdir() if ".autoos-backup-" in p.name]
            self.assertEqual(len(backups), 11)

    def test_unknown_chunk_with_signature_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            rogue = tmp / "rogue._.js"
            rogue.write_text("/* __omnirouteQuotaCacheState */\n", encoding="utf-8")
            before = get_file_hashes(tmp)

            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("ERROR: unknown chunk(s) containing quotaCache signature found:", proc.stderr)
            self.assertIn("rogue._.js", proc.stderr)
            self.assertEqual(before, get_file_hashes(tmp))

            proc_file = self.run_tool("--root", str(tmp), "--file", "rogue._.js")
            self.assertEqual(proc_file.returncode, 1)
            self.assertIn("ERROR: unknown chunk(s) containing quotaCache signature found:", proc_file.stderr)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            proc_nofile = self.run_tool("--root", str(tmp), "--file", "not_a_chunk._.js")
            self.assertEqual(proc_nofile.returncode, 1)
            self.assertIn("ERROR: no patches registered for file: not_a_chunk._.js", proc_nofile.stderr)

    def test_renamed_set_of_chunks_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            target = tmp / "_0brmz9y._.js"
            target.rename(tmp / "renamed_chunk.txt")
            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("ERROR: missing expected chunk file(s):", proc.stderr)

    def test_restore_from_backup_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            orig_bytes = {fn: (tmp / fn).read_bytes() for fn in CHUNK_DEFS}
            proc = self.run_tool("--root", str(tmp), "--backup")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for fn in CHUNK_DEFS:
                bak_matches = [p for p in tmp.iterdir() if p.name.startswith(f"{fn}.autoos-backup-")]
                self.assertEqual(len(bak_matches), 1, f"Expected 1 backup for {fn}")
                self.assertEqual(bak_matches[0].read_bytes(), orig_bytes[fn])
                shutil.copy2(bak_matches[0], tmp / fn)
                self.assertEqual((tmp / fn).read_bytes(), orig_bytes[fn])

    def test_upgrade_from_old_patch_version(self):
        with tempfile.TemporaryDirectory() as tmp_fresh, tempfile.TemporaryDirectory() as tmp_upgrade:
            fresh_dir = Path(tmp_fresh)
            upgrade_dir = Path(tmp_upgrade)
            setup_chunks_dir(fresh_dir)
            setup_chunks_dir(upgrade_dir, old_patched=True)

            proc_fresh = self.run_tool("--root", str(fresh_dir))
            self.assertEqual(proc_fresh.returncode, 0, proc_fresh.stderr)

            proc_up = self.run_tool("--root", str(upgrade_dir))
            self.assertEqual(proc_up.returncode, 0, proc_up.stderr)
            self.assertIn("Done: 12 patched, 0 skipped, 0 errors", proc_up.stdout)
            for fn in CHUNK_DEFS:
                self.assertIn(f"UPGRADE {fn}: 5 replacement(s) applied", proc_up.stdout)
                self.assertEqual((fresh_dir / fn).read_bytes(), (upgrade_dir / fn).read_bytes())

    def test_upgrade_from_v1_git_script(self):
        proc_git = subprocess.run(
            ["git", "show", "afa6d4d4:tools/apply-gateway-quota-patch.py"],
            capture_output=True, text=True
        )
        if proc_git.returncode != 0:
            self.skipTest(f"Commit afa6d4d4 not found in git repo: {proc_git.stderr.strip()}")
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            v1_script = td / "v1-patch.py"
            v1_script.write_text(proc_git.stdout, encoding="utf-8")
            fresh_dir = td / "fresh"
            fresh_dir.mkdir()
            up_dir = td / "upgrade"
            up_dir.mkdir()
            setup_chunks_dir(fresh_dir)
            setup_chunks_dir(up_dir)

            res_v1 = subprocess.run([sys.executable, str(v1_script), "--root", str(up_dir)], capture_output=True, text=True)
            self.assertEqual(res_v1.returncode, 0, res_v1.stderr)

            res_fresh = self.run_tool("--root", str(fresh_dir))
            self.assertEqual(res_fresh.returncode, 0, res_fresh.stderr)

            res_up = self.run_tool("--root", str(up_dir))
            self.assertEqual(res_up.returncode, 0, res_up.stderr)
            for fn in CHUNK_DEFS:
                self.assertEqual((fresh_dir / fn).read_bytes(), (up_dir / fn).read_bytes())

    def test_node_check_on_patched_chunks(self):
        if not shutil.which("node"):
            self.skipTest("node binary not found on PATH")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp)
            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for fn in CHUNK_DEFS:
                res = subprocess.run(["node", "--check", str(tmp / fn)], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, f"node --check failed for {fn}: {res.stderr}")

    def test_lf_only_chunk_gets_no_cr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            setup_chunks_dir(tmp, crlf=False)
            proc = self.run_tool("--root", str(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for fn in CHUNK_DEFS:
                data = (tmp / fn).read_bytes()
                self.assertNotIn(b"\r", data, f"Chunk {fn} contains carriage return")


if __name__ == "__main__":
    unittest.main()
