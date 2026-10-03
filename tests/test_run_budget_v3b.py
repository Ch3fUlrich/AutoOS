"""Tests for AutoOS cost guard budget tool refinements (part 3b)."""

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT))

import tools.run_budget as rb


def run_main(argv, fetch=None):
    """Execute main in-process capturing stdout and stderr."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = rb.main(argv, fetch=fetch)
    return rc, out_buf.getvalue(), err_buf.getvalue()


def write_temp_text(content):
    """Write text to a temporary file and return its Path."""
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".ndjson", encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


def write_temp_rows(rows):
    """Write list of row dicts as a JSON array to a temporary file and return its Path."""
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8")
    json.dump(rows, f)
    f.close()
    return Path(f.name)


class TestRunGateRotateAndLast3(unittest.TestCase):
    """Verification of rotate on last call and flag on last3 calls."""

    def test_rotate_decided_on_last_call_not_max(self):
        prices = rb.load_price_table()
        # Earlier large call, last call small -> ok, not rotate
        rows_earlier_large = [
            {
                "timestamp": "2026-10-03T10:00:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 190_000, "out": 10, "cacheRead": 0},
            },
            {
                "timestamp": "2026-10-03T10:05:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 20_000, "out": 10, "cacheRead": 0},
            },
        ]
        res_ok = rb.evaluate_run(rows_earlier_large, prices=prices)
        self.assertEqual(res_ok["verdict"], "ok")
        self.assertEqual(res_ok["last3_input"], [190_000, 20_000])

        # Earlier small call, last call large -> rotate
        rows_last_large = [
            {
                "timestamp": "2026-10-03T10:00:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 20_000, "out": 10, "cacheRead": 0},
            },
            {
                "timestamp": "2026-10-03T10:05:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 150_000, "out": 10, "cacheRead": 0},
            },
        ]
        res_rotate = rb.evaluate_run(rows_last_large, prices=prices)
        self.assertEqual(res_rotate["verdict"], "rotate")
        self.assertEqual(res_rotate["last3_input"], [20_000, 150_000])

    def test_last3_input_checks_tail_calls_not_head(self):
        prices = rb.load_price_table()
        # 5 calls: first 3 large (61,000), last 2 small (1,000)
        # Tail 3 are [61,000, 1,000, 1,000] -> not all > 60,000 -> not flag
        rows_head_large = [
            {
                "timestamp": f"2026-10-03T10:0{i}:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": toks, "out": 10, "cacheRead": 0},
            }
            for i, toks in enumerate([61_000, 61_000, 61_000, 1_000, 1_000])
        ]
        res_not_flag = rb.evaluate_run(rows_head_large, prices=prices)
        self.assertNotEqual(res_not_flag["verdict"], "flag")
        self.assertEqual(res_not_flag["verdict"], "ok")
        self.assertEqual(res_not_flag["last3_input"], [61_000, 1_000, 1_000])

        # 5 calls: first 2 small (1,000), last 3 large (61,000)
        # Tail 3 are [61,000, 61,000, 61,000] -> all > 60,000 -> flag
        rows_tail_large = [
            {
                "timestamp": f"2026-10-03T10:0{i}:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": toks, "out": 10, "cacheRead": 0},
            }
            for i, toks in enumerate([1_000, 1_000, 61_000, 61_000, 61_000])
        ]
        res_flag = rb.evaluate_run(rows_tail_large, prices=prices)
        self.assertEqual(res_flag["verdict"], "flag")
        self.assertEqual(res_flag["last3_input"], [61_000, 61_000, 61_000])


class TestDayBadRowsAccumulation(unittest.TestCase):
    """Accumulation of malformed lines and token problems in day verdict."""

    def test_day_accumulates_bad_rows_across_files_and_token_errors(self):
        # File 1: 1 malformed line and 1 valid row
        content1 = (
            "malformed line 1\n"
            + json.dumps({
                "timestamp": "2026-10-03T10:00:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 100, "out": 10, "cacheRead": 0},
            })
            + "\n"
        )
        p1 = write_temp_text(content1)
        self.addCleanup(p1.unlink)

        # File 2: 2 malformed lines and 1 row with non-numeric token field
        content2 = (
            "malformed line 2\n"
            "malformed line 3\n"
            + json.dumps({
                "timestamp": "2026-10-03T10:05:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": "not-a-number", "out": 10, "cacheRead": 0},
            })
            + "\n"
        )
        p2 = write_temp_text(content2)
        self.addCleanup(p2.unlink)

        rc, out, _err = run_main(["day", "--rows", str(p1), str(p2), "--day", "2026-10-03"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["bad_rows"], 4)


class TestGatewayHandling(unittest.TestCase):
    """Gateway handling for day and run."""

    def test_day_gateway_alone_surfaces_truncated_flag(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def mock_fetch(url, _headers, _timeout):
            now = datetime.now(timezone.utc)
            m = re.search(r"offset=(\d+)", url)
            off = int(m.group(1)) if m else 0
            page = [
                {
                    "id": f"row-{off}-{i}",
                    "timestamp": now.isoformat(),
                    "provider": "gemini",
                    "model": "gemini-3.8-flash",
                    "tokens": {"in": 10, "out": 0, "cacheRead": 0},
                }
                for i in range(500)
            ]
            return 200, json.dumps(page).encode("utf-8")

        rc, out, _err = run_main(["day", "--gateway", "--day", today], fetch=mock_fetch)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["truncated"])
        self.assertEqual(data["verdict"], "ok")
        self.assertEqual(data["day"], today)

    def test_run_with_both_rows_and_gateway_reads_rows_only(self):
        called = []

        def mock_fetch(url, _headers, _timeout):
            called.append(url)
            return 200, b"[]"

        row = {
            "timestamp": "2026-10-03T10:00:00Z",
            "provider": "gemini",
            "model": "gemini-3.8-flash",
            "tokens": {"in": 100, "out": 10, "cacheRead": 0},
        }
        p = write_temp_rows([row])
        self.addCleanup(p.unlink)

        rc, out, _err = run_main(["run", "--rows", str(p), "--gateway"], fetch=mock_fetch)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["calls"], 1)
        self.assertFalse(data["truncated"])
        self.assertEqual(len(called), 0)


class TestProviderModelAndFieldPins(unittest.TestCase):
    """Pins for provider prefixes, model lowercasing, and row field handling."""

    def test_bare_provider_prefixes_and_model_prefixes(self):
        self.assertEqual(
            rb.google_paid_provider({"provider": "", "model": "google/foo"}),
            "gemini",
        )
        self.assertEqual(
            rb.google_paid_provider({"provider": "", "model": "vertex/foo"}),
            "vertex",
        )
        self.assertIsNone(
            rb.google_paid_provider({"provider": "", "model": "vertex-foo"})
        )
        self.assertEqual(
            rb.google_paid_provider({"provider": "vertex-foo", "model": "bar"}),
            "vertex",
        )

    def test_model_name_lowercasing(self):
        self.assertEqual(
            rb.google_paid_provider({"provider": "", "model": "VERTEX/Gemini-3.8-Flash"}),
            "vertex",
        )

    def test_non_google_provider_with_gemini_model_not_counted(self):
        for prov in ("openrouter", "openrouter-gemini-x"):
            self.assertIsNone(rb.google_paid_provider({"provider": prov, "model": "gemini-3.8-flash"}))
        row = {
            "provider": "openrouter",
            "model": "gemini-3.8-flash",
            "timestamp": "2026-10-03T10:00:00Z",
            "tokens": {"in": 5000, "out": 0, "cacheRead": 0},
        }
        res = rb.evaluate_day([row], day_str="2026-10-03")
        self.assertEqual(res["usd"], 0.0)
        self.assertEqual(res["by_provider"], {})

    def test_tag_substring_does_not_match(self):
        self.assertFalse(rb.tag_matches("crax", "cra"))
        self.assertFalse(rb.tag_matches("crax/build/run-1", "cra"))
        self.assertFalse(rb.tag_matches("lane/crax/run-1", "cra"))
        self.assertFalse(rb.tag_matches("lane/run-1/crax", "cra"))
        res = rb.evaluate_run(
            [{"sessionTag": "crax/build/1", "timestamp": "2026-10-03T10:00:00Z", "tokens": {"in": 100}}],
            tag="cra",
        )
        self.assertEqual(res["calls"], 0)

    def test_boolean_token_field_counts_as_zero_and_increments_bad_rows(self):
        val_true, bad_true = rb._coerce_token(True)
        self.assertEqual(val_true, 0)
        self.assertTrue(bad_true)

        val_false, bad_false = rb._coerce_token(False)
        self.assertEqual(val_false, 0)
        self.assertTrue(bad_false)

        res = rb.evaluate_run([
            {"timestamp": "2026-10-03T10:00:00Z", "tokens": {"in": True, "out": 10, "cacheRead": 0}}
        ])
        self.assertEqual(res["input"], 0)
        self.assertEqual(res["bad_rows"], 1)

    def test_max_input_per_call_is_maximum_over_rows(self):
        res = rb.evaluate_run([
            {"timestamp": "2026-10-03T10:00:00Z", "tokens": {"in": 100, "out": 0, "cacheRead": 0}},
            {"timestamp": "2026-10-03T10:01:00Z", "tokens": {"in": 500, "out": 0, "cacheRead": 0}},
            {"timestamp": "2026-10-03T10:02:00Z", "tokens": {"in": 200, "out": 0, "cacheRead": 0}},
        ])
        self.assertEqual(res["max_input_per_call"], 500)

    def test_day_by_provider_totals_add_up_across_files(self):
        # File 1: gemini call (1M tokens in)
        p1 = write_temp_rows([
            {
                "timestamp": "2026-10-03T10:00:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 1_000_000, "out": 0, "cacheRead": 0},
            }
        ])
        self.addCleanup(p1.unlink)

        # File 2: gemini call (2M tokens in) + vertex call (1M tokens in)
        p2 = write_temp_rows([
            {
                "timestamp": "2026-10-03T10:05:00Z",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 2_000_000, "out": 0, "cacheRead": 0},
            },
            {
                "timestamp": "2026-10-03T10:10:00Z",
                "provider": "vertex",
                "model": "gemini-3.8-flash",
                "tokens": {"in": 1_000_000, "out": 0, "cacheRead": 0},
            },
        ])
        self.addCleanup(p2.unlink)

        rc, out, _err = run_main(["day", "--rows", str(p1), str(p2), "--day", "2026-10-03"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["by_provider"]["gemini"], 2.25)
        self.assertEqual(data["by_provider"]["vertex"], 0.75)
        self.assertEqual(data["usd"], 3.0)

    def test_json_file_with_rows_wrapper_object_is_read(self):
        f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8")
        json.dump({
            "rows": [
                {
                    "timestamp": "2026-10-03T10:00:00Z",
                    "provider": "gemini",
                    "model": "gemini-3.8-flash",
                    "tokens": {"in": 100, "out": 10, "cacheRead": 0},
                }
            ]
        }, f)
        f.close()
        p = Path(f.name)
        self.addCleanup(p.unlink)

        rows, bad = rb.read_rows_from_file(str(p))
        self.assertEqual(len(rows), 1)
        self.assertEqual(bad, 0)

        rc, out, _err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["calls"], 1)


class TestDayBadTimestampsAndTagAndProviderPins(unittest.TestCase):
    """Pins: day timestamp problems accumulate; run-id tags and non-Google
    providers are not counted loosely."""

    def test_day_missing_or_garbage_timestamps_count_as_bad_rows(self):
        p = write_temp_rows([
            {"provider": "gemini", "model": "gemini-3.8-flash", "tokens": {"in": 10_000, "out": 10, "cacheRead": 0}},
            {"timestamp": "not-a-date", "provider": "gemini", "model": "gemini-3.8-flash", "tokens": {"in": 20_000, "out": 10, "cacheRead": 0}},
            {"timestamp": 10 ** 30, "provider": "gemini", "model": "gemini-3.8-flash", "tokens": {"in": 30_000, "out": 10, "cacheRead": 0}},
        ])
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["day", "--rows", str(p), "--day", "2026-10-03"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["bad_rows"], 3)
        self.assertEqual(data["usd"], 0.0)
        self.assertEqual(data["by_provider"], {})

    def test_run_id_tag_is_exact_segment_not_suffix(self):
        self.assertFalse(rb.tag_matches("cra", "ra"))
        self.assertFalse(rb.tag_matches("lane/cra", "ra"))
        self.assertFalse(rb.tag_matches("lane/cra/build", "ra"))
        self.assertTrue(rb.tag_matches("lane/ra", "ra"))
        p = write_temp_rows([
            {"sessionTag": "cra", "timestamp": "2026-10-03T10:00:00Z", "tokens": {"in": 100, "out": 1, "cacheRead": 0}},
            {"sessionTag": "lane/cra", "timestamp": "2026-10-03T10:01:00Z", "tokens": {"in": 100, "out": 1, "cacheRead": 0}},
            {"sessionTag": "lane/ra", "timestamp": "2026-10-03T10:02:00Z", "tokens": {"in": 100, "out": 1, "cacheRead": 0}},
        ])
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["run", "--rows", str(p), "--tag", "ra"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["calls"], 1)

    def test_non_google_provider_gemini_model_not_counted_in_day(self):
        self.assertIsNone(rb.google_paid_provider({"provider": "openrouter", "model": "gemini-3.8-flash"}))
        self.assertIsNone(rb.google_paid_provider({"provider": "OpenRouter", "model": "gemini-3.8-flash"}))
        p = write_temp_rows([
            {"timestamp": "2026-10-03T10:00:00Z", "provider": "openrouter", "model": "gemini-3.8-flash", "tokens": {"in": 50_000, "out": 10, "cacheRead": 0}},
            {"timestamp": "2026-10-03T10:01:00Z", "provider": "openrouter", "model": "gemini-3.8-flash", "tokens": {"in": 25_000, "out": 10, "cacheRead": 0}},
        ])
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["day", "--rows", str(p), "--day", "2026-10-03"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["usd"], 0.0)
        self.assertEqual(data["by_provider"], {})
        self.assertEqual(data["bad_rows"], 0)


if __name__ == "__main__":
    unittest.main()
