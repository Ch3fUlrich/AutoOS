#!/usr/bin/env python3
"""tests/test_gateway_quota_patch.py — Tests for gateway quota null-safety patcher."""

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
PATCH_TOOL = ROOT / "tools" / "apply-gateway-quota-patch.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "quota_cache_chunk_fixture.js"

sys.path.insert(0, str(ROOT / "tools"))
patch_tool_mod = importlib.import_module("apply-gateway-quota-patch")
NEW_EXHAUSTION_DECISION = patch_tool_mod.NEW_EXHAUSTION_DECISION
CHUNK_DEFS = patch_tool_mod.CHUNK_DEFS
get_chunk_replacements = patch_tool_mod.get_chunk_replacements


def _run_node_harness(patch_script_path: Path = PATCH_TOOL) -> dict:
    """Run patch script on fixture copy in temp directory and execute scenarios in Node."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        target = tmp / "_0brmz9y._.js"
        shutil.copy2(FIXTURE_PATH, target)

        patch_cmd = [
            sys.executable,
            str(patch_script_path),
            "--root",
            str(tmp),
            "--file",
            "_0brmz9y._.js",
            "--no-backup",
        ]
        proc = subprocess.run(patch_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Patch tool failed: {proc.stderr}\n{proc.stdout}")

        driver_source = r"""
const [id, factory] = require('./_0brmz9y._.js');
const exportsMap = {};
const savedSnapshots = [];
let latestSnapshots = [];

const stubs = {
  161002: {
    safePercentage: (v) => {
      if (v === null || v === undefined) return null;
      let n = Number(v);
      return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : null;
    }
  },
  517551: {
    saveQuotaSnapshot: (snap) => { savedSnapshots.push(snap); },
    cleanupOldSnapshots: () => {},
    getLatestQuotaSnapshotsForConnection: (connId) => latestSnapshots
  },
  188693: {
    recordProviderQuotaResetEventIfChanged: () => {}
  }
};

const proxyStub = new Proxy({}, { get: () => () => {} });

const ctx = {
  i: (modId) => stubs[modId] || proxyStub,
  a: (fn) => { fn((deps) => deps, () => {}); },
  s: (arr) => {
    for (let i = 0; i < arr.length; i += 3) exportsMap[arr[i]] = arr[i + 2];
  }
};

factory(ctx);
const { setQuotaCache, isAccountQuotaExhausted, getQuotaCache, getQuotaWindowStatus } = exportsMap;

const runConn = (connId, prov, quotas) => {
  setQuotaCache(connId, prov, quotas);
  const isExhausted = isAccountQuotaExhausted(connId);
  const entry = getQuotaCache(connId);
  return { isExhausted, quotas: entry ? entry.quotas : {} };
};

const out = {};
out['A'] = runConn('connA', 'pA', { winA: { total: 100, used: 60 } });
out['B'] = runConn('connB', 'pB', { winB: { total: 100, used: 100 } });

const cInputs = {
  'null': { total: null, used: 50 },
  'zero': { total: 0, used: 50 },
  'neg': { total: -10, used: 50 },
  'nan': { total: NaN, used: 50 },
  'absent': { used: 50 }
};
out['C'] = {};
for (const [k, q] of Object.entries(cInputs)) {
  const snapLenBefore = savedSnapshots.length;
  const res = runConn('connC_' + k, 'pC', { winC: q });
  const wStat = getQuotaWindowStatus('connC_' + k, 'winC', 99);
  const snaps = savedSnapshots.slice(snapLenBefore);
  out['C'][k] = {
    isExhausted: res.isExhausted,
    remainingPercentage: res.quotas.winC ? res.quotas.winC.remainingPercentage : null,
    windowStatus: wStat,
    snapshot: snaps.length > 0 ? snaps[snaps.length - 1] : null
  };
}

out['D'] = runConn('connD', 'pD', {});
out['E'] = runConn('connE', 'pE', { winE: { total: 100, used: 0 } });
out['F'] = runConn('connF', 'pF', { winF: { total: 0, used: 25 } });
out['G'] = runConn('connG', 'pG', { winG: { used: 30 } });
out['H1_order1'] = runConn('connH1_1', 'pH', { winB: { total: 100, used: 100 }, winC: { total: null, used: 50 } });
out['H1_order2'] = runConn('connH1_2', 'pH', { winC: { total: null, used: 50 }, winB: { total: 100, used: 100 } });
out['H2_order1'] = runConn('connH2_1', 'pH', { winA: { total: 100, used: 60 }, winC: { total: null, used: 50 } });
out['H2_order2'] = runConn('connH2_2', 'pH', { winC: { total: null, used: 50 }, winA: { total: 100, used: 60 } });
out['overuse_150_100'] = runConn('conn_overuse', 'pO', { win: { total: 100, used: 150 } });
out['near_cap_996'] = runConn('conn_996', 'pN', { win: { total: 1000, used: 996 } });
out['near_cap_999'] = runConn('conn_999', 'pN', { win: { total: 1000, used: 999 } });
out['vertex_spend'] = runConn('conn_vertex', 'vertex', { spend: { used: 42.5 } });
out['multi_0_50_order1'] = runConn('conn_m050_1', 'pM', { w0: { total: 100, used: 100 }, w50: { total: 100, used: 50 } });
out['multi_0_50_order2'] = runConn('conn_m050_2', 'pM', { w50: { total: 100, used: 50 }, w0: { total: 100, used: 100 } });
out['multi_both_0'] = runConn('conn_mboth0', 'pM', { w1: { total: 100, used: 100 }, w2: { total: 200, used: 200 } });

const reloadCases = [
  { id: 'r_null_0', rem: null, exh: 0 },
  { id: 'r_null_1', rem: null, exh: 1 },
  { id: 'r_0_1', rem: 0, exh: 1 },
  { id: 'r_0_0', rem: 0, exh: 0 }
];
out['snapshot_reload'] = {};
for (const rc of reloadCases) {
  latestSnapshots = [{
    window_key: 'w',
    provider: 'pReload',
    remaining_percentage: rc.rem,
    is_exhausted: rc.exh,
    created_at: new Date().toISOString()
  }];
  const isExh = isAccountQuotaExhausted(rc.id);
  const entry = getQuotaCache(rc.id);
  out['snapshot_reload'][rc.id] = {
    isExhausted: isExh,
    remainingPercentage: entry ? entry.quotas.w.remainingPercentage : null
  };
}

console.log(JSON.stringify(out));
"""
        driver_path = tmp / "driver.js"
        driver_path.write_text(driver_source, encoding="utf-8")
        node_res = subprocess.run(
            ["node", str(driver_path)],
            cwd=str(tmp),
            capture_output=True,
            text=True,
        )
        if node_res.returncode != 0:
            raise RuntimeError(f"Node execution failed: {node_res.stderr}\n{node_res.stdout}")
        return json.loads(node_res.stdout)


class GatewayQuotaPatchScriptTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATCH_TOOL.is_file(), f"Patch tool missing: {PATCH_TOOL}")
        self.assertTrue(FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}")
        self.assertLessEqual(FIXTURE_PATH.stat().st_size, 40960, "Fixture size must be <= 40 KB")

    def test_byte_equality_and_newline_preservation(self):
        """Patched output preserves LF line endings and differs from original only in intended edits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "_0brmz9y._.js"
            orig_bytes = FIXTURE_PATH.read_bytes()
            self.assertIn(b"\n", orig_bytes)
            self.assertNotIn(b"\r\n", orig_bytes)

            shutil.copy2(FIXTURE_PATH, target)
            proc = subprocess.run(
                [sys.executable, str(PATCH_TOOL), "--root", str(tmp), "--file", "_0brmz9y._.js", "--no-backup"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"Patch failed: {proc.stderr}\n{proc.stdout}")

            patched_bytes = target.read_bytes()
            self.assertNotIn(b"\r\n", patched_bytes, "Patched file must preserve LF line endings without CRLF")

            d = CHUNK_DEFS["_0brmz9y._.js"]
            replacements, _ = get_chunk_replacements("_0brmz9y._.js", d)
            restored_bytes = patched_bytes
            for old_s, new_s in replacements:
                self.assertIn(new_s.encode("utf-8"), restored_bytes)
                restored_bytes = restored_bytes.replace(new_s.encode("utf-8"), old_s.encode("utf-8"), 1)

            self.assertEqual(restored_bytes, orig_bytes, "Reversed edits must match original fixture bytes")

    def test_text_pins_of_patched_exhaustion_expression(self):
        """Text pins of patched exhaustion expression: exact pinned form and absence of connection .some."""
        self.assertEqual(
            NEW_EXHAUSTION_DECISION,
            ".every((e,_,a)=>a.some(w=>null!=w.remainingPercentage)&&(null==e.remainingPercentage||(!1!==e.fractionReported&&e.remainingPercentage<=0)))",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "_0brmz9y._.js"
            shutil.copy2(FIXTURE_PATH, target)
            subprocess.run(
                [sys.executable, str(PATCH_TOOL), "--root", str(tmp), "--file", "_0brmz9y._.js", "--no-backup"],
                check=True,
                capture_output=True,
            )
            patched_text = target.read_text(encoding="utf-8")
            expected_expr = "t.every((e,_,a)=>a.some(w=>null!=w.remainingPercentage)&&(null==e.remainingPercentage||(!1!==e.fractionReported&&e.remainingPercentage<=0)))"
            self.assertIn(expected_expr, patched_text)
            self.assertIn(".every(", patched_text)
            self.assertIn("some(w=>null!=w.remainingPercentage)", patched_text)
            self.assertNotIn("t.some((e", patched_text)


class GatewayQuotaNodeBehaviouralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node runtime not available on PATH")
        cls.results = _run_node_harness(PATCH_TOOL)

    def test_case_a_forty_percent_left_not_exhausted(self):
        res = self.results["A"]
        self.assertFalse(res["isExhausted"])
        self.assertEqual(res["quotas"]["winA"]["remainingPercentage"], 40)

    def test_case_b_zero_percent_left_exhausted(self):
        res = self.results["B"]
        self.assertTrue(res["isExhausted"])
        self.assertEqual(res["quotas"]["winB"]["remainingPercentage"], 0)

    def test_case_c_total_less_window_not_exhausted(self):
        for subcase, c_res in self.results["C"].items():
            with self.subTest(subcase=subcase):
                self.assertFalse(c_res["isExhausted"])
                self.assertIsNone(c_res["remainingPercentage"])
                self.assertIsNone(c_res["windowStatus"]["remainingPercentage"])
                self.assertFalse(c_res["windowStatus"]["reachedThreshold"])
                self.assertIsNotNone(c_res["snapshot"])
                self.assertIsNone(c_res["snapshot"]["remaining_percentage"])
                self.assertEqual(c_res["snapshot"]["is_exhausted"], 0)

    def test_case_d_no_window_not_exhausted(self):
        self.assertFalse(self.results["D"]["isExhausted"])

    def test_case_e_hundred_percent_not_exhausted(self):
        res = self.results["E"]
        self.assertFalse(res["isExhausted"])
        self.assertEqual(res["quotas"]["winE"]["remainingPercentage"], 100)

    def test_case_f_and_g_total_less_like_c(self):
        self.assertFalse(self.results["F"]["isExhausted"])
        self.assertIsNone(self.results["F"]["quotas"]["winF"]["remainingPercentage"])
        self.assertFalse(self.results["G"]["isExhausted"])
        self.assertIsNone(self.results["G"]["quotas"]["winG"]["remainingPercentage"])

    def test_case_h1_real_zero_plus_total_less_both_orders_exhausted(self):
        res1 = self.results["H1_order1"]
        self.assertTrue(res1["isExhausted"])
        self.assertEqual(res1["quotas"]["winB"]["remainingPercentage"], 0)
        self.assertIsNone(res1["quotas"]["winC"]["remainingPercentage"])

        res2 = self.results["H1_order2"]
        self.assertTrue(res2["isExhausted"])
        self.assertEqual(res2["quotas"]["winB"]["remainingPercentage"], 0)
        self.assertIsNone(res2["quotas"]["winC"]["remainingPercentage"])

    def test_case_h2_forty_percent_plus_total_less_both_orders_not_exhausted(self):
        res1 = self.results["H2_order1"]
        self.assertFalse(res1["isExhausted"])
        self.assertEqual(res1["quotas"]["winA"]["remainingPercentage"], 40)
        self.assertIsNone(res1["quotas"]["winC"]["remainingPercentage"])

        res2 = self.results["H2_order2"]
        self.assertFalse(res2["isExhausted"])
        self.assertEqual(res2["quotas"]["winA"]["remainingPercentage"], 40)
        self.assertIsNone(res2["quotas"]["winC"]["remainingPercentage"])

    def test_case_used_150_of_100_exhausted(self):
        res = self.results["overuse_150_100"]
        self.assertTrue(res["isExhausted"])
        self.assertEqual(res["quotas"]["win"]["remainingPercentage"], -50)

    def test_case_996_of_1000_exhausted(self):
        res = self.results["near_cap_996"]
        self.assertTrue(res["isExhausted"])
        self.assertEqual(res["quotas"]["win"]["remainingPercentage"], 0)

    def test_case_999_of_1000_exhausted(self):
        res = self.results["near_cap_999"]
        self.assertTrue(res["isExhausted"])
        self.assertEqual(res["quotas"]["win"]["remainingPercentage"], 0)

    def test_vertex_total_less_only_not_exhausted(self):
        res = self.results["vertex_spend"]
        self.assertFalse(res["isExhausted"])
        self.assertIsNone(res["quotas"]["spend"]["remainingPercentage"])

    def test_case_h_real_zero_plus_total_less_exhausted(self):
        self.assertTrue(self.results["H1_order1"]["isExhausted"])
        self.assertTrue(self.results["H1_order2"]["isExhausted"])

    def test_multi_window_zero_and_fifty_not_exhausted(self):
        res1 = self.results["multi_0_50_order1"]
        self.assertFalse(res1["isExhausted"])
        self.assertEqual(res1["quotas"]["w0"]["remainingPercentage"], 0)
        self.assertEqual(res1["quotas"]["w50"]["remainingPercentage"], 50)

        res2 = self.results["multi_0_50_order2"]
        self.assertFalse(res2["isExhausted"])
        self.assertEqual(res2["quotas"]["w50"]["remainingPercentage"], 50)
        self.assertEqual(res2["quotas"]["w0"]["remainingPercentage"], 0)

    def test_multi_window_both_zero_exhausted(self):
        res = self.results["multi_both_0"]
        self.assertTrue(res["isExhausted"])
        self.assertEqual(res["quotas"]["w1"]["remainingPercentage"], 0)
        self.assertEqual(res["quotas"]["w2"]["remainingPercentage"], 0)

    def test_snapshot_reload_retains_exhaustion_states(self):
        reloads = self.results["snapshot_reload"]
        self.assertFalse(reloads["r_null_0"]["isExhausted"])
        self.assertIsNone(reloads["r_null_0"]["remainingPercentage"])

        self.assertFalse(reloads["r_null_1"]["isExhausted"])
        self.assertIsNone(reloads["r_null_1"]["remainingPercentage"])

        self.assertTrue(reloads["r_0_1"]["isExhausted"])
        self.assertEqual(reloads["r_0_1"]["remainingPercentage"], 0)

        self.assertTrue(reloads["r_0_0"]["isExhausted"])
        self.assertEqual(reloads["r_0_0"]["remainingPercentage"], 0)


if __name__ == "__main__":
    unittest.main()
