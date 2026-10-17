#!/usr/bin/env python3
"""tests/test_run_budget_more.py — File handling, gateway source, tag matching,
garbage input, unpriced models and price-file consistency for
tools/run_budget.py. All cases are offline: the gateway is only exercised via
an injected fetch or a refused loopback connection.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT))

import tools.run_budget as rb

PRICES_FILE = REPO_ROOT / "configuration" / "google-prices.json"


def write_text(content, suffix=".json"):
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=suffix, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


def run_cli(*args, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "run_budget.py"), *args],
                          capture_output=True, text=True, env=env)
    return proc


def good_row_file():
    return write_text(json.dumps([
        {"timestamp": "2026-10-03T10:00:00Z", "provider": "gemini",
         "model": "gemini-3.8-flash", "tokens": {"in": 100, "out": 0, "cacheRead": 0}}
    ]))


class TestRowFiles(unittest.TestCase):
    """Missing/bad input files exit 2 with a one-line message and no verdict."""

    def test_missing_file_exits_2_no_verdict(self):
        p = Path(tempfile.gettempdir()) / "autoos-run-budget-no-such-file.json"
        self.assertFalse(p.exists())
        proc = run_cli("run", "--rows", str(p))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("not found", proc.stderr)

    def test_directory_exits_2(self):
        proc = run_cli("run", "--rows", str(REPO_ROOT / "tools"))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("directory", proc.stderr)

    def test_unreadable_file_raises(self):
        p = write_text("[]")
        self.addCleanup(p.unlink)
        with unittest.mock.patch.object(Path, "open", side_effect=PermissionError("access denied")):
            with self.assertRaises(rb.RowFileError):
                rb.read_rows_from_file(str(p))

    def test_malformed_json_doc_exits_2(self):
        p = write_text("{this is not json")
        self.addCleanup(p.unlink)
        proc = run_cli("run", "--rows", str(p))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("no usable lines", proc.stderr)

    def test_all_lines_malformed_exits_2(self):
        p = write_text("garbage one\nstill not json\n")
        self.addCleanup(p.unlink)
        proc = run_cli("run", "--rows", str(p))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("no usable lines", proc.stderr)

    def test_bare_object_is_not_a_row_list(self):
        p = write_text(json.dumps({"provider": "gemini", "model": "m"}))
        self.addCleanup(p.unlink)
        with self.assertRaises(rb.RowFileError):
            rb.read_rows_from_file(str(p))
        proc = run_cli("run", "--rows", str(p))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no row list", proc.stderr)

    def test_empty_and_empty_array_are_valid_zero_rows(self):
        p1 = write_text("")
        p2 = write_text("[]")
        self.addCleanup(p1.unlink)
        self.addCleanup(p2.unlink)
        self.assertEqual(rb.read_rows_from_file(str(p1)), ([], 0))
        self.assertEqual(rb.read_rows_from_file(str(p2)), ([], 0))
        proc = run_cli("run", "--rows", str(p1))
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["verdict"], "ok")
        self.assertEqual(out["calls"], 0)

    def test_bom_accepted(self):
        # A BOM-prefixed NDJSON file must parse as if the BOM were not there:
        # without utf-8-sig handling the first line would fail to parse.
        line = json.dumps({"provider": "gemini", "model": "gemini-3.8-flash", "tokens": {"in": 100}})
        p = write_text("\ufeff" + line + "\n" + line)
        self.addCleanup(p.unlink)
        rows, bad = rb.read_rows_from_file(str(p))
        self.assertEqual(len(rows), 2)
        self.assertEqual(bad, 0)

    def test_bad_ndjson_line_counted_and_skipped(self):
        p = write_text("not json at all\n" + json.dumps({"provider": "gemini", "model": "gemini-3.8-flash", "tokens": {"in": 100}}))
        self.addCleanup(p.unlink)
        rows, bad = rb.read_rows_from_file(str(p))
        self.assertEqual(len(rows), 1)
        self.assertEqual(bad, 1)

    def test_day_bad_date_exits_2(self):
        proc = run_cli("day", "--day", "not-a-day")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("YYYY-MM-DD", proc.stderr)


class TestGarbageRows(unittest.TestCase):
    """Non-numeric token fields and bad timestamps must not crash the gate."""

    def test_garbage_token_fields_do_not_crash_run(self):
        rows = [
            # garbage string in, list out, NaN cacheRead, unparseable timestamp
            {"model": "gemini-3.8-flash", "provider": "gemini", "timestamp": "nope",
             "tokens": {"in": "garbage", "out": [1, 2], "cacheRead": float("nan")}},
            # infinite in, missing timestamp
            {"model": "gemini-3.8-flash", "provider": "gemini",
             "tokens": {"in": float("inf"), "out": 50}},
            # a good row, for scale
            {"model": "gemini-3.8-flash", "provider": "gemini", "timestamp": "2026-10-03T10:00:00Z",
             "tokens": {"in": 1000, "out": 10}},
        ]
        res = rb.evaluate_run(rows)
        self.assertEqual(res["calls"], 3)
        self.assertEqual(res["bad_rows"], 2)
        self.assertEqual(res["uncached"], 1000)
        self.assertEqual(res["output"], 60)
        self.assertEqual(res["verdict"], "ok")

    def test_garbage_day_rows_not_counted_toward_today(self):
        prices = {"registry": {}, "fallback": {"models": {"m1": {"providers": {"vertex": {
            "price_in": {"per_token": 1.0}, "price_out": {"per_token": 0.0},
            "price_cache_read": {"per_token": 0.0}}}}}}}
        rows = [
            # no usable timestamp: cannot be placed in the day, not counted
            {"timestamp": "nope", "provider": "vertex", "model": "m1", "tokens": {"in": 100}},
            # dated in the day but the token field is garbage: coerces to 0
            {"timestamp": "2026-10-03T10:00:00Z", "provider": "vertex", "model": "m1", "tokens": {"in": "nope"}},
        ]
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=prices)
        self.assertEqual(res["bad_rows"], 2)
        self.assertEqual(res["usd"], 0.0)
        self.assertEqual(res["verdict"], "ok")


class TestUnpricedModels(unittest.TestCase):
    """Google-paid rows whose model has no price row are priced at the AI
    Studio gemini-3.8-flash default rates, and said so in the output."""

    def test_unpriced_google_row_priced_at_default_and_reported(self):
        # 16 calls of 60k + one of 40k: 1M uncached without rotating the run.
        sizes = [60_000] * 16 + [40_000]
        rows = [{"model": "gemini-3.8-pro-mystery", "provider": "vertex",
                 "tokens": {"in": t, "out": 0, "cacheRead": 0}} for t in sizes]
        res = rb.evaluate_run(rows)
        self.assertTrue(res["unpriced_default_used"])
        self.assertEqual(res["unpriced_models"], [{"model": "gemini-3.8-pro-mystery", "count": len(sizes)}])
        # 1M uncached at the 0.75/1M default rate: not $0, and not stop.
        self.assertAlmostEqual(res["est_usd"], 0.75, places=6)
        self.assertEqual(res["verdict"], "ok")

    def test_unpriced_day_row(self):
        rows = [{"timestamp": "2026-10-03T10:00:00Z", "model": "gemini-9.0-turbo-mystery",
                 "provider": "gemini", "tokens": {"in": 2_000_000, "out": 0, "cacheRead": 0}}]
        res = rb.evaluate_day(rows, day_str="2026-10-03")
        self.assertTrue(res["unpriced_default_used"])
        self.assertEqual(res["unpriced_models"], [{"model": "gemini-9.0-turbo-mystery", "count": 1}])
        self.assertAlmostEqual(res["usd"], 1.5, places=6)
        self.assertAlmostEqual(res["by_provider"]["gemini"], 1.5, places=6)

    def test_non_google_unpriced_rows_stay_zero(self):
        rows = [{"model": "deepseek-mystery", "provider": "ovhcloud",
                 "tokens": {"in": 1_000_000, "out": 0, "cacheRead": 0}}]
        res = rb.evaluate_day(rows, day_str="2026-10-03")
        self.assertFalse(res["unpriced_default_used"])
        self.assertEqual(res["usd"], 0.0)

    def test_cli_notes_unpriced_on_stderr(self):
        p = write_text(json.dumps([
            {"timestamp": "2026-10-03T10:00:00Z", "model": "gemini-3.8-pro-mystery",
             "provider": "vertex", "tokens": {"in": 1_000_000, "out": 0, "cacheRead": 0}}
        ]))
        self.addCleanup(p.unlink)
        proc = run_cli("day", "--rows", str(p), "--day", "2026-10-03")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertTrue(out["unpriced_default_used"])
        self.assertIn("unpriced", proc.stderr)
        self.assertIn("default rates", proc.stderr)

    def test_default_rates_follow_the_prices_file(self):
        # Changing the AI Studio default model's rate in a temp copy changes
        # the rate an unpriced model is priced at.
        data = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
        data["models"]["gemini-3.8-flash"]["providers"]["gemini"]["price_in"]["per_token"] = 2.0e-06
        data["models"]["gemini-3.8-flash"]["providers"]["gemini"]["price_in"]["per_million"] = 2.0
        p = write_text(json.dumps(data))
        self.addCleanup(p.unlink)
        table = rb.load_price_table(registry_path=str(REPO_ROOT / "no-such-registry.json"), prices_path=str(p))
        pr = rb.default_google_prices(table)
        self.assertEqual(pr["price_in"], 2.0e-06)
        self.assertEqual(pr["source"], "unpriced-default")

    def test_default_when_file_lacks_the_model(self):
        pr = rb.default_google_prices({"fallback": {}, "registry": {}})
        self.assertEqual(pr["price_in"], 7.5e-07)
        self.assertTrue(pr["unverified"])


class TestPriceFileConsistency(unittest.TestCase):
    """per_million and per_token must agree or the file fails to load."""

    def test_disagreement_raises(self):
        data = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
        # per_token stays 7.5e-07 while per_million moves: now they disagree.
        data["models"]["gemini-3.8-flash"]["providers"]["gemini"]["price_in"]["per_million"] = 1.5
        p = write_text(json.dumps(data))
        self.addCleanup(p.unlink)
        with self.assertRaises(rb.PriceFileError):
            rb.load_price_table(prices_path=str(p))

    def test_cli_disagreement_exits_2(self):
        data = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
        data["models"]["gemini-3.8-flash"]["providers"]["vertex"]["price_out"]["per_million"] = 99.0
        bad_prices = write_text(json.dumps(data), suffix=".prices.json")
        rows = good_row_file()
        self.addCleanup(bad_prices.unlink)
        self.addCleanup(rows.unlink)
        proc = run_cli("run", "--rows", str(rows), "--prices", str(bad_prices))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("disagrees", proc.stderr)

    def test_per_token_only_is_fine(self):
        data = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
        del data["models"]["gemini-3.8-flash"]["providers"]["gemini"]["price_in"]["per_million"]
        p = write_text(json.dumps(data))
        self.addCleanup(p.unlink)
        table = rb.load_price_table(prices_path=str(p))  # must not raise
        pr = rb.get_price("gemini-3.8-flash", "gemini", table)
        self.assertEqual(pr["price_in"], 7.5e-07)


class TestGatewaySource(unittest.TestCase):
    """--gateway as a source for run and day, without any network call."""

    def test_gateway_unreachable_exits_2_one_line(self):
        # The documented command must get past the import and fail cleanly:
        # refused loopback connection -> one stderr line, exit 2, no verdict.
        proc = run_cli("run", "--gateway", extra_env={"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:9"})
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("gateway", proc.stderr)
        lines = [l for l in proc.stderr.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)

    def test_day_gateway_unreachable_exits_2(self):
        proc = run_cli("day", "--gateway", extra_env={"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:9"})
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")

    def test_truncated_flag_from_paging(self):
        # 20 full pages of fresh rows: fetch_window stops at its page cap and
        # marks the window truncated; fetch_gateway_rows must surface that.
        def mock_fetch(url, headers, timeout):
            now = datetime.now(timezone.utc)
            m = re.search(r"offset=(\d+)", url)
            off = int(m.group(1)) if m else 0
            page = [{"id": "row-%d-%d" % (off, i), "timestamp": now.isoformat(),
                     "provider": "gemini", "model": "gemini-3.8-flash",
                     "tokens": {"in": 1, "out": 0, "cacheRead": 0}} for i in range(500)]
            return 200, json.dumps(page).encode("utf-8")

        rows, truncated = rb.fetch_gateway_rows(fetch=mock_fetch, gateway="http://127.0.0.1:9999", key="k")
        self.assertTrue(truncated)
        self.assertGreaterEqual(len(rows), 500)

    def test_run_output_carries_truncated_false_without_gateway(self):
        p = good_row_file()
        self.addCleanup(p.unlink)
        proc = run_cli("run", "--rows", str(p))
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(json.loads(proc.stdout)["truncated"])


class TestProviderClassification(unittest.TestCase):
    """Every gateway spelling of a Google-paid provider is pinned."""

    def test_google_paid_provider_spellings(self):
        def classify(provider, model):
            return rb.google_paid_provider({"provider": provider, "model": model})
        # Each spelling the gateway logs:
        self.assertEqual(classify("vertex", "gemini-3.8-flash"), "vertex")
        self.assertEqual(classify("Vertex", "gemini-3.8-flash"), "vertex")
        self.assertEqual(classify("vertex-ai", "gemini-3.8-flash"), "vertex")
        self.assertEqual(classify("vertex-gemini-3.8-flash", "gemini-3.8-flash"), "vertex")
        self.assertEqual(classify("vertex_ai", "gemini-3.8-flash"), "vertex")
        self.assertEqual(classify("gemini", "gemini-3.8-flash"), "gemini")
        self.assertEqual(classify("google", "gemini-3.8-flash"), "gemini")
        self.assertEqual(classify("gemini-3.8-flash", "gemini-3.8-flash"), "gemini")
        self.assertEqual(classify("", "gemini-3.8-flash"), "gemini")
        self.assertEqual(classify("", "vertex/gemini-3.8-flash"), "vertex")
        self.assertEqual(classify("", "gemini/gemini-3.8-flash"), "gemini")
        self.assertEqual(classify("", "google/gemini-3.8-flash"), "gemini")
        # Never Google-paid:
        for p in ("antigravity", "agy", "ovh", "ovhcloud", "openrouter", "jules"):
            self.assertIsNone(classify(p, "gemini-3.8-flash"))
        self.assertIsNone(classify("vertex", "agy/gemini-3.8-flash"))


class TestTagMatching(unittest.TestCase):
    """--tag matches the full sessionTag, its first segment, or its last one."""

    def test_tag_matches_first_and_last_segments(self):
        rows = [
            {"sessionTag": "cra/Build-the-thing/run-9", "provider": "gemini",
             "model": "gemini-3.8-flash", "tokens": {"in": 100}},
            {"sessionTag": "other-lane/Some-Title/run-9", "provider": "gemini",
             "model": "gemini-3.8-flash", "tokens": {"in": 200}},
        ]
        # first segment of the first row only
        self.assertEqual(rb.evaluate_run(rows, tag="cra")["calls"], 1)
        # last segment shared by both rows
        self.assertEqual(rb.evaluate_run(rows, tag="run-9")["calls"], 2)
        # full tag
        self.assertEqual(rb.evaluate_run(rows, tag="cra/Build-the-thing/run-9")["calls"], 1)
        # near-miss: no match
        self.assertEqual(rb.evaluate_run(rows, tag="crax")["calls"], 0)

    def test_help_documents_the_tag_rule(self):
        proc = run_cli("run", "--help")
        self.assertEqual(proc.returncode, 0)
        help_text = re.sub(r"\s+", " ", proc.stdout)
        self.assertIn("first segment", help_text)
        self.assertIn("last segment", help_text)


if __name__ == "__main__":
    unittest.main()
