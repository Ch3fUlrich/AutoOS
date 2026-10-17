#!/usr/bin/env python3
"""tests/test_run_budget.py — Tests for AutoOS cost guard tools/run_budget.py.

Core verdict rules, exact boundaries and pricing; file handling, gateway
sources, tag matching, garbage input and classification spellings live in
tests/test_run_budget_more.py.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT))

import tools.run_budget as rb

FLASH = "gemini-3.8-flash"


def flash_row(n_in, n_out=0, n_cache=0, **extra):
    row = {"model": FLASH, "provider": "gemini", "tokens": {"in": n_in, "out": n_out, "cacheRead": n_cache}}
    row.update(extra)
    return row


def n_flash_rows(n, n_in=0, n_out=0, n_cache=0):
    return [flash_row(n_in, n_out, n_cache) for _ in range(n)]


def price_table(rate, prov="vertex", model="m1"):
    """One model priced at `rate` per input token, everything else free."""
    return {"registry": {}, "fallback": {"models": {model: {"providers": {prov: {
        "price_in": {"per_token": rate}, "price_out": {"per_token": 0.0},
        "price_cache_read": {"per_token": 0.0}}}}}}}


def make_file(data):
    """Write data as JSON to a temp file; return its Path."""
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json")
    json.dump(data, f)
    f.close()
    return Path(f.name)


class TestRunBudgetVerdicts(unittest.TestCase):
    """Test per-run verdict rules: stop, rotate, flag, ok and their exact edges."""

    def test_run_0_9m_uncached_ok(self):
        # 18 calls of 50k = 900,000 uncached tokens <= 1,000,000; cost $0.675
        res = rb.evaluate_run(n_flash_rows(18, 50_000))
        self.assertEqual(res["uncached"], 900_000)
        self.assertEqual(res["verdict"], "ok")

    def test_uncached_exactly_1m_ok_and_1m_plus_1_stop(self):
        # The ceiling is "more than 1,000,000": exactly 1M passes, 1M+1 stops.
        # 15 calls of 60k plus a final call: the total is the boundary value
        # while the last call stays under the rotate threshold and the 60k
        # calls keep the flag check (strictly > 60k) from firing.
        def rows(extra):
            out = n_flash_rows(15, 60_000)
            out.append(flash_row(extra))
            return out
        res_ok = rb.evaluate_run(rows(100_000))
        self.assertEqual(res_ok["uncached"], 1_000_000)
        self.assertEqual(res_ok["verdict"], "ok")
        res_stop = rb.evaluate_run(rows(100_001))
        self.assertEqual(res_stop["uncached"], 1_000_001)
        self.assertEqual(res_stop["verdict"], "stop")

    def test_run_1_1m_uncached_stop(self):
        # 22 calls of 50k = 1,100,000 uncached tokens > 1,000,000 (D-543 ceiling)
        res = rb.evaluate_run(n_flash_rows(22, 50_000))
        self.assertEqual(res["uncached"], 1_100_000)
        self.assertEqual(res["verdict"], "stop")

    def test_run_est_2_01_stop(self):
        # Uncached 500k ($0.375) + 436k out ($1.635) = $2.010 > $2.00
        res = rb.evaluate_run(n_flash_rows(10, 50_000, 43_600))
        self.assertLessEqual(res["uncached"], 1_000_000)
        self.assertGreater(res["est_usd"], 2.00)
        self.assertEqual(res["verdict"], "stop")

    def test_run_last_input_150k_rotate_and_149999_not(self):
        # Last call >= 150,000 triggers rotate; 149,999 does not.
        self.assertEqual(rb.evaluate_run([flash_row(150_000)])["verdict"], "rotate")
        self.assertEqual(rb.evaluate_run([flash_row(149_999)])["verdict"], "ok")

    def test_flag_at_exactly_60000_not_and_60001(self):
        # Flag needs MORE than 60,000 input on each of the last three calls.
        self.assertEqual(rb.evaluate_run(n_flash_rows(3, 60_000))["verdict"], "ok")
        self.assertEqual(rb.evaluate_run(n_flash_rows(3, 60_001))["verdict"], "flag")

    def test_run_three_calls_61k_flag_and_two_do_not(self):
        # 3 calls each > 60k input triggers flag; 2 calls do not.
        self.assertEqual(rb.evaluate_run(n_flash_rows(3, 61_000))["verdict"], "flag")
        self.assertEqual(rb.evaluate_run(n_flash_rows(2, 61_000))["verdict"], "ok")
        # 3 calls where one is <= 60k does not trigger flag.
        mixed = [flash_row(50_000)] + n_flash_rows(2, 61_000)
        self.assertEqual(rb.evaluate_run(mixed)["verdict"], "ok")

    def test_precedence_rotate_beats_flag_and_stop_beats_rotate(self):
        # Last call 150k AND all of the last three > 60k: rotate wins over flag.
        rows = n_flash_rows(2, 61_000) + [flash_row(150_000)]
        self.assertEqual(rb.evaluate_run(rows)["verdict"], "rotate")
        # 8 calls of 150k: stop (1.2M uncached) beats rotate (last call 150k).
        self.assertEqual(rb.evaluate_run(n_flash_rows(8, 150_000))["verdict"], "stop")

    def test_failed_trailing_row_does_not_mask_rotate(self):
        # A zero-input 503 after the 150k call must not hide the rotate:
        # rotate/flag look only at rows that carry input tokens (status 200),
        # but the failed row still counts as a call.
        rows = [
            flash_row(150_000, timestamp="2026-10-03T10:00:00Z", status=200),
            flash_row(0, timestamp="2026-10-03T10:05:00Z", status=503),
        ]
        res = rb.evaluate_run(rows)
        self.assertEqual(res["verdict"], "rotate")
        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["last3_input"], [150000])

    def test_run_estimate_boundaries_unrounded(self):
        # est_usd is rounded to 6 decimals for display only; the gate decides
        # on the exact value. Both estimates below display as 2.0, but one must
        # stop and the other must not.
        # Cache-read-only rows split into <= 60k calls so only the dollar
        # boundary can decide the verdict.
        def cache_rows(model, provider, total):
            sizes = [60_000] * (total // 60_000)
            if total % 60_000:
                sizes.append(total % 60_000)
            return [{"provider": provider, "model": model,
                     "tokens": {"in": t, "out": 0, "cacheRead": t}} for t in sizes]

        # AI Studio at 7.5e-08 per cache token: $1.99999995 / $2.000000025.
        res_ok = rb.evaluate_run(cache_rows(FLASH, "gemini", 26_666_666))
        self.assertEqual(res_ok["verdict"], "ok")
        res_stop = rb.evaluate_run(cache_rows(FLASH, "gemini", 26_666_667))
        self.assertEqual(res_stop["verdict"], "stop")
        self.assertAlmostEqual(res_ok["est_usd"], res_stop["est_usd"], places=6)

        # Vertex from a table priced at 6.25e-08 per cache token:
        # $1.999999875 stays ok, $2.0000000625 stops.
        prices = price_table(0.0, prov="vertex", model="m-cache")
        prices["fallback"]["models"]["m-cache"]["providers"]["vertex"]["price_cache_read"]["per_token"] = 6.25e-08
        for n, want in ((31_999_998, "ok"), (32_000_001, "stop")):
            self.assertEqual(rb.evaluate_run(cache_rows("m-cache", "vertex", n), prices=prices)["verdict"], want)

    def test_daily_estimate_boundaries_unrounded(self):
        # 1e-09 per input token: 19,999,999,999 tokens is exactly $19.999999999
        # (ok, under the default $20 warn) and 24,999,999,999 is exactly
        # $24.999999999, which must warn, not read as the rounded 24.999999.
        prices = price_table(1e-09)
        for n, want in ((19_999_999_999, "ok"), (24_999_999_999, "warn")):
            rows = [{"timestamp": "2026-10-03T10:00:00Z", "provider": "vertex", "model": "m1",
                     "tokens": {"in": n, "out": 0, "cacheRead": 0}}]
            self.assertEqual(rb.evaluate_day(rows, day_str="2026-10-03", prices=prices)["verdict"], want)


class TestDailyGate(unittest.TestCase):
    """Test daily gate spend thresholds, provider filtering, and date boundaries."""

    def test_default_thresholds_without_arguments(self):
        # Defaults are warn $20 / budget $25: called without either argument.
        # 1e-08 per input token makes the token counts exact dollars:
        # 1,999,000,000 tokens is exactly $19.99 (ok), 2B exactly $20.00
        # (warn), 2.5B exactly $25.00 (block).
        prices = price_table(1e-08)

        def day_rows(tokens):
            return [{"timestamp": "2026-10-03T10:00:00Z", "provider": "vertex", "model": "m1",
                     "tokens": {"in": tokens, "out": 0, "cacheRead": 0}}]

        for tokens, want in ((1_999_000_000, "ok"), (2_000_000_000, "warn"), (2_500_000_000, "block")):
            self.assertEqual(rb.evaluate_day(day_rows(tokens), day_str="2026-10-03", prices=prices)["verdict"], want)

    def test_antigravity_and_ovh_excluded(self):
        prices = price_table(1.0)
        prices["fallback"]["models"]["m1"]["providers"]["gemini"] = \
            {"price_in": {"per_token": 1.0}, "price_out": {"per_token": 0.0}, "price_cache_read": {"per_token": 0.0}}
        rows = [
            {"timestamp": "2026-10-03T10:00:00Z", "provider": p, "model": "m1", "tokens": {"in": 100}}
            for p in ("antigravity", "agy", "ovh", "ovhcloud")
        ]
        rows.append({"timestamp": "2026-10-03T10:00:00Z", "provider": "vertex", "model": "m1", "tokens": {"in": 5}})
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=prices)
        self.assertEqual(res["usd"], 5.0)
        self.assertEqual(list(res["by_provider"].keys()), ["vertex"])

    def test_unknown_provider_not_counted(self):
        prices = price_table(1.0)
        rows = [
            {"timestamp": "2026-10-03T10:00:00Z", "provider": "somesuch", "model": "m1", "tokens": {"in": 100}},
            {"timestamp": "2026-10-03T10:00:00Z", "provider": "gemini", "model": "unlisted-model", "tokens": {"in": 100}},
        ]
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=prices)
        # "somesuch" is not a Google-paid provider; the unlisted gemini model
        # prices at the default rates, not $1/token.
        self.assertNotIn("somesuch", res["by_provider"])
        self.assertAlmostEqual(res["by_provider"]["gemini"], 7.5e-05, places=12)

    def test_two_files_summed(self):
        p1 = make_file([{"timestamp": "2026-10-03T01:00:00Z", "provider": "vertex", "model": FLASH,
                         "tokens": {"in": 1_000_000, "out": 0, "cacheRead": 0}}])
        p2 = make_file([{"timestamp": "2026-10-03T02:00:00Z", "provider": "gemini", "model": FLASH,
                         "tokens": {"in": 1_000_000, "out": 0, "cacheRead": 0}}])
        self.addCleanup(p1.unlink)
        self.addCleanup(p2.unlink)
        rows, bad = rb.read_rows_from_files([str(p1), str(p2)])
        self.assertEqual(bad, 0)
        res = rb.evaluate_day(rows, day_str="2026-10-03")
        # 1M uncached at 0.75/1M = $0.75 each; total = $1.50
        self.assertAlmostEqual(res["usd"], 1.50, places=4)
        self.assertIn("vertex", res["by_provider"])
        self.assertIn("gemini", res["by_provider"])

    def test_utc_day_boundary(self):
        prices = price_table(1.0)
        rows = [
            {"timestamp": "2026-10-02T23:59:59Z", "provider": "vertex", "model": "m1", "tokens": {"in": 10}},
            {"timestamp": "2026-10-03T00:00:00Z", "provider": "vertex", "model": "m1", "tokens": {"in": 5}},
            {"timestamp": "2026-10-03T23:59:59Z", "provider": "vertex", "model": "m1", "tokens": {"in": 3}},
            {"timestamp": "2026-10-04T00:00:00Z", "provider": "vertex", "model": "m1", "tokens": {"in": 20}},
        ]
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=prices)
        self.assertEqual(res["usd"], 8.0)

    def test_day_output_carries_unverified(self):
        # Vertex cache-read rates are unverified, AI Studio rates are not.
        rows = [{"timestamp": "2026-10-03T10:00:00Z", "provider": "vertex", "model": FLASH,
                 "tokens": {"in": 100_000, "out": 0, "cacheRead": 100_000}}]
        self.assertTrue(rb.evaluate_day(rows, day_str="2026-10-03")["unverified"])
        rows_gem = [dict(rows[0], provider="gemini")]
        self.assertFalse(rb.evaluate_day(rows_gem, day_str="2026-10-03")["unverified"])

    def test_day_cache_split_100m_cached(self):
        # A day with 100M cached input: the cache-read rate applies to the cached
        # split, the input rate to the rest. 0.5M x 0.75/1M + 100M x 0.1875/1M
        # = $0.375 + $18.75 = $19.125 -> ok under the default $20 warn.
        rows = [{"timestamp": "2026-10-03T12:00:00Z", "provider": "vertex", "model": FLASH,
                 "tokens": {"in": 100_500_000, "out": 0, "cacheRead": 100_000_000}}]
        res = rb.evaluate_day(rows, day_str="2026-10-03")
        self.assertAlmostEqual(res["usd"], 19.125, places=4)
        self.assertEqual(res["verdict"], "ok")


class TestPriceCalculationAndVerification(unittest.TestCase):
    """Test formula for cache-read split and unverified flag tagging."""

    def test_cache_read_priced_separately_formula_and_unverified_flag(self):
        # 100M cached input and 0.5M uncached input on Vertex:
        # formula: 0.5M * $0.75/1M + 100M * $0.1875/1M = $0.375 + $18.75 = $19.125
        rows = [{"timestamp": "2026-10-03T12:00:00Z", "provider": "vertex", "model": FLASH,
                 "tokens": {"in": 100_500_000, "out": 0, "cacheRead": 100_000_000}}]
        res = rb.evaluate_run(rows)
        self.assertEqual(res["uncached"], 500_000)
        self.assertEqual(res["cache_read"], 100_000_000)
        self.assertAlmostEqual(res["est_usd"], 19.125, places=4)
        # Vertex cache-read rate is unverified
        self.assertTrue(res["unverified"])

    def test_ai_studio_cache_read_is_verified(self):
        # AI Studio uses verified rates (in 0.75, cache-read 0.075, out 3.75)
        rows = [{"timestamp": "2026-10-03T12:00:00Z", "provider": "gemini", "model": FLASH,
                 "tokens": {"in": 100_500_000, "out": 0, "cacheRead": 100_000_000}}]
        res = rb.evaluate_run(rows)
        # 0.5M * 0.75 + 100M * 0.075 = 0.375 + 7.50 = $7.875
        self.assertAlmostEqual(res["est_usd"], 7.875, places=4)
        self.assertFalse(res["unverified"])

    def test_registry_preference_when_rows_exist(self):
        # Synthetic registry with complete price rows wins over the fallback.
        table = {"registry": {"models": {"custom-model": {
            "price_in": 2e-6, "price_out": 4e-6, "price_cache_read": 1e-6}}},
            "fallback": {"models": {"custom-model": {"providers": {"vertex": {
                "price_in": {"per_token": 9e-6}, "price_out": {"per_token": 9e-6},
                "price_cache_read": {"per_token": 9e-6}}}}}}}
        pr = rb.get_price("custom-model", "vertex", table)
        self.assertEqual(pr["source"], "registry")
        self.assertEqual(pr["price_in"], 2e-6)

        # When the registry row is absent, the fallback json is used
        pr_fb = rb.get_price(FLASH, "vertex")
        self.assertEqual(pr_fb["source"], "google-prices.json")

    def test_real_price_file_is_consistent(self):
        # per_million and per_token in the shipped file agree, so it loads.
        rb.load_price_table()

    def test_price_file_is_the_source(self):
        # A temp copy of the prices file with one rate changed must change the
        # result: the file, not a hard-coded constant, is the source of truth.
        data = json.loads((REPO_ROOT / "configuration" / "google-prices.json").read_text(encoding="utf-8"))
        data["models"][FLASH]["providers"]["gemini"]["price_in"]["per_token"] = 1.5e-06
        data["models"][FLASH]["providers"]["gemini"]["price_in"]["per_million"] = 1.5
        p = make_file(data)
        self.addCleanup(p.unlink)
        table = rb.load_price_table(registry_path=str(REPO_ROOT / "no-such-registry.json"), prices_path=str(p))
        pr = rb.get_price(FLASH, "gemini", table)
        self.assertEqual(pr["price_in"], 1.5e-06)
        res = rb.evaluate_run([flash_row(1_000_000)], prices=table)
        self.assertAlmostEqual(res["est_usd"], 1.5, places=6)


class TestRowSourcesAndOfflineGateway(unittest.TestCase):
    """Test reading JSON/NDJSON files, grouping, and offline gateway fetching."""

    def test_read_json_and_ndjson(self):
        p_json = make_file([{"provider": "vertex", "sessionTag": "lane1/run1", "tokens": {"in": 10}}])
        f_nd = tempfile.NamedTemporaryFile("w", delete=False, suffix=".ndjson")
        f_nd.write('{"provider": "gemini", "sessionTag": "lane2/run2", "tokens": {"in": 20}}\n'
                   '{"provider": "vertex", "sessionTag": "lane1/run1", "tokens": {"in": 30}}')
        f_nd.close()
        p_nd = Path(f_nd.name)
        self.addCleanup(p_json.unlink)
        self.addCleanup(p_nd.unlink)

        r1, bad1 = rb.read_rows_from_file(str(p_json))
        self.assertEqual(len(r1), 1)
        self.assertEqual(bad1, 0)
        r2, bad2 = rb.read_rows_from_file(str(p_nd))
        self.assertEqual(len(r2), 2)
        self.assertEqual(bad2, 0)

        groups = rb.group_rows(r1 + r2)
        self.assertIn("vertex|lane1|lane1/run1", groups)
        self.assertEqual(len(groups["vertex|lane1|lane1/run1"]), 2)
        self.assertIn("gemini|lane2|lane2/run2", groups)

    def test_gateway_reader_offline_injected_fetch(self):
        # Offline test with mock fetch function: no network, no printed keys
        calls = []
        def mock_fetch(url, headers, timeout):
            calls.append((url, headers))
            data = [
                {"id": "call-1", "provider": "vertex", "model": FLASH, "tokens": {"in": 100, "out": 10, "cacheRead": 20}},
                {"id": "call-2", "provider": "gemini", "model": FLASH, "tokens": {"in": 200, "out": 30, "cacheRead": 40}},
            ]
            return 200, json.dumps(data).encode("utf-8")

        rows, truncated = rb.fetch_gateway_rows(fetch=mock_fetch, gateway="http://127.0.0.1:9999", key="test-offline-key")
        self.assertEqual(len(rows), 2)
        self.assertFalse(truncated)
        self.assertEqual(len(calls), 1)
        # Ensure key was passed in headers, never leaked or printed
        self.assertEqual(calls[0][1].get("Authorization"), "Bearer test-offline-key")


class TestCLIExecution(unittest.TestCase):
    """Test CLI exit codes for run (0/10/11/12) and day (0/20/21)."""

    def test_cli_run_exit_codes(self):
        def run_cli_test(tokens_data):
            p = make_file([{"model": FLASH, "provider": "gemini", "tokens": tokens_data}])
            self.addCleanup(p.unlink)
            proc = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "run_budget.py"), "run", "--rows", str(p)],
                                  capture_output=True, text=True)
            return proc.returncode, json.loads(proc.stdout)

        rc_ok, out_ok = run_cli_test({"in": 50000, "out": 0, "cacheRead": 0})
        self.assertEqual(rc_ok, 0)
        self.assertEqual(out_ok["verdict"], "ok")

        rc_rot, out_rot = run_cli_test({"in": 150000, "out": 0, "cacheRead": 0})
        self.assertEqual(rc_rot, 11)
        self.assertEqual(out_rot["verdict"], "rotate")

        rc_stop, out_stop = run_cli_test({"in": 1100000, "out": 0, "cacheRead": 0})
        self.assertEqual(rc_stop, 12)
        self.assertEqual(out_stop["verdict"], "stop")

    def test_cli_day_exit_codes(self):
        def day_cli_test(usd_target):
            # 1M tokens at 0.75e-6 per token = $0.75
            tin = int(usd_target / 0.75e-6)
            rows = [{"timestamp": "2026-10-03T12:00:00Z", "provider": "gemini", "model": FLASH,
                     "tokens": {"in": tin, "out": 0, "cacheRead": 0}}]
            p = make_file(rows)
            self.addCleanup(p.unlink)
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools" / "run_budget.py"), "day", "--rows", str(p),
                 "--day", "2026-10-03", "--warn", "20.0", "--budget", "25.0"],
                capture_output=True, text=True
            )
            return proc.returncode, json.loads(proc.stdout)

        self.assertEqual(day_cli_test(10.0)[0], 0)
        self.assertEqual(day_cli_test(21.0)[0], 20)
        self.assertEqual(day_cli_test(26.0)[0], 21)


if __name__ == "__main__":
    unittest.main()
