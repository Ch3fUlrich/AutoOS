"""Tests for AutoOS cost guard budget tool refinements."""

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


class TestBadRowsCliOutput(unittest.TestCase):
    """Malformed file lines must reach the bad_rows field in CLI output."""

    def test_ndjson_malformed_lines_surfaced_in_run_cli(self):
        ndjson = (
            "not a json line\n"
            "another broken line\n"
            + json.dumps({
                "timestamp": "2026-10-03T10:00:00Z",
                "model": "gemini-3.8-flash",
                "provider": "gemini",
                "tokens": {"in": 100, "out": 10, "cacheRead": 0},
            })
            + "\n"
        )
        p = write_temp_text(ndjson)
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["bad_rows"], 2)
        self.assertEqual(data["calls"], 1)

    def test_ndjson_malformed_lines_surfaced_in_day_cli(self):
        ndjson = (
            "malformed 1\n"
            "malformed 2\n"
            + json.dumps({
                "timestamp": "2026-10-03T10:00:00Z",
                "model": "gemini-3.8-flash",
                "provider": "gemini",
                "tokens": {"in": 100, "out": 10, "cacheRead": 0},
            })
            + "\n"
        )
        p = write_temp_text(ndjson)
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["day", "--rows", str(p), "--day", "2026-10-03"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["bad_rows"], 2)

    def test_file_bad_lines_summed_with_row_level_token_errors(self):
        ndjson = (
            "{broken json line 1\n"
            "{broken json line 2\n"
            + json.dumps({
                "timestamp": "2026-10-03T10:00:00Z",
                "model": "gemini-3.8-flash",
                "provider": "gemini",
                "tokens": {"in": "not-a-number", "out": 10, "cacheRead": 0},
            })
            + "\n"
        )
        p = write_temp_text(ndjson)
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["bad_rows"], 3)


class TestMissingRowSourceCli(unittest.TestCase):
    """When neither --rows nor --gateway is supplied, the CLI must exit with code 2."""

    def test_run_without_rows_or_gateway_exits_2(self):
        rc, out, err = run_main(["run"])
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")
        lines = [line for line in err.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("run_budget:", lines[0])
        self.assertIn("neither --rows nor --gateway", lines[0])

    def test_day_without_rows_or_gateway_exits_2(self):
        rc, out, err = run_main(["day"])
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")
        lines = [line for line in err.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("run_budget:", lines[0])
        self.assertIn("neither --rows nor --gateway", lines[0])


class TestProcessExitCodesPinned(unittest.TestCase):
    """Process exit codes for all verdicts must be strictly pinned through the CLI."""

    def test_flag_verdict_exits_10(self):
        rows = [
            {"timestamp": f"2026-10-03T10:0{i}:00Z", "model": "gemini-3.8-flash",
             "provider": "gemini", "tokens": {"in": 70_000, "out": 0, "cacheRead": 0}}
            for i in range(3)
        ]
        p = write_temp_rows(rows)
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 10)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "flag")

    def test_rotate_verdict_exits_11(self):
        rows = [
            {"timestamp": "2026-10-03T10:00:00Z", "model": "gemini-3.8-flash",
             "provider": "gemini", "tokens": {"in": 150_000, "out": 0, "cacheRead": 0}}
        ]
        p = write_temp_rows(rows)
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 11)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "rotate")

    def test_stop_verdict_exits_12(self):
        rows = [
            {"timestamp": "2026-10-03T10:00:00Z", "model": "gemini-3.8-flash",
             "provider": "gemini", "tokens": {"in": 1_000_001, "out": 0, "cacheRead": 0}}
        ]
        p = write_temp_rows(rows)
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 12)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "stop")

    def test_ok_verdict_exits_0(self):
        rows = [
            {"timestamp": "2026-10-03T10:00:00Z", "model": "gemini-3.8-flash",
             "provider": "gemini", "tokens": {"in": 50_000, "out": 0, "cacheRead": 0}}
        ]
        p = write_temp_rows(rows)
        self.addCleanup(p.unlink)
        rc, out, _err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "ok")


class TestTimestampSortAndCacheReadClamp(unittest.TestCase):
    """Rows sorted by timestamp so newest is last; cacheRead clamped to input."""

    def test_timestamp_sorting_handles_gateway_reverse_order(self):
        earlier = {
            "timestamp": "2026-10-03T10:00:00Z",
            "model": "gemini-3.8-flash",
            "provider": "gemini",
            "tokens": {"in": 50_000, "out": 0, "cacheRead": 0},
        }
        later = {
            "timestamp": "2026-10-03T11:00:00Z",
            "model": "gemini-3.8-flash",
            "provider": "gemini",
            "tokens": {"in": 150_000, "out": 0, "cacheRead": 0},
        }
        res_reverse = rb.evaluate_run([later, earlier])
        self.assertEqual(res_reverse["verdict"], "rotate")
        self.assertEqual(res_reverse["last3_input"], [50_000, 150_000])

        res_forward = rb.evaluate_run([earlier, later])
        self.assertEqual(res_forward["verdict"], "rotate")
        self.assertEqual(res_forward["last3_input"], [50_000, 150_000])

    def test_cacheread_greater_than_input_clamped_and_non_negative(self):
        row = {
            "timestamp": "2026-10-03T10:00:00Z",
            "model": "gemini-3.8-flash",
            "provider": "gemini",
            "tokens": {"in": 100_000, "out": 0, "cacheRead": 150_000},
        }
        res = rb.evaluate_run([row])
        self.assertEqual(res["uncached"], 0)
        self.assertEqual(res["cache_read"], 100_000)
        self.assertEqual(res["est_usd"], 0.0075)


class TestStatusAndZeroInputConditions(unittest.TestCase):
    """Trailing non-200 and zero-input calls must not trigger rotate or flag."""

    def test_trailing_row_status_not_200_ignored_for_last_call(self):
        row1 = {
            "timestamp": "2026-10-03T10:00:00Z",
            "model": "gemini-3.8-flash",
            "provider": "gemini",
            "status": 200,
            "tokens": {"in": 50_000, "out": 0, "cacheRead": 0},
        }
        row2 = {
            "timestamp": "2026-10-03T11:00:00Z",
            "model": "gemini-3.8-flash",
            "provider": "gemini",
            "status": 503,
            "tokens": {"in": 150_000, "out": 0, "cacheRead": 0},
        }
        res = rb.evaluate_run([row1, row2])
        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["input"], 200_000)
        self.assertEqual(res["max_input_per_call"], 50_000)
        self.assertEqual(res["last3_input"], [50_000])
        self.assertEqual(res["verdict"], "ok")

    def test_trailing_row_zero_input_ignored_for_last_call(self):
        row1 = {
            "timestamp": "2026-10-03T10:00:00Z",
            "model": "gemini-3.8-flash",
            "provider": "gemini",
            "status": 200,
            "tokens": {"in": 150_000, "out": 0, "cacheRead": 0},
        }
        row2 = {
            "timestamp": "2026-10-03T11:00:00Z",
            "model": "gemini-3.8-flash",
            "provider": "gemini",
            "status": 200,
            "tokens": {"in": 0, "out": 0, "cacheRead": 0},
        }
        res = rb.evaluate_run([row1, row2])
        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["input"], 150_000)
        self.assertEqual(res["max_input_per_call"], 150_000)
        self.assertEqual(res["last3_input"], [150_000])
        self.assertEqual(res["verdict"], "rotate")


class TestUnknownProviderAndGatewayTruncated(unittest.TestCase):
    """Unknown provider normalization, gateway truncated flag, and unpriced note."""

    def test_unknown_provider_classified_for_google_models(self):
        self.assertEqual(
            rb.google_paid_provider({"provider": "unknown", "model": "gemini-3.8-flash"}),
            "gemini",
        )
        self.assertEqual(
            rb.google_paid_provider({"provider": "(unknown)", "model": "gemini-3.8-flash"}),
            "gemini",
        )
        self.assertEqual(
            rb.google_paid_provider({"provider": "unknown", "model": "vertex/gemini-3.8-flash"}),
            "vertex",
        )
        self.assertIsNone(
            rb.google_paid_provider({"provider": "unknown", "model": "deepseek-chat"})
        )

    def test_run_gateway_truncated_flag_surfaces_in_cli_output(self):
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

        rc, out, _err = run_main(["run", "--gateway"], fetch=mock_fetch)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["truncated"])
        self.assertGreaterEqual(data["calls"], 500)

    def test_run_cli_notes_unpriced_model_on_stderr(self):
        row = {
            "timestamp": "2026-10-03T10:00:00Z",
            "model": "gemini-3.8-pro-mystery",
            "provider": "vertex",
            "tokens": {"in": 1_000, "out": 0, "cacheRead": 0},
        }
        p = write_temp_rows([row])
        self.addCleanup(p.unlink)
        rc, out, err = run_main(["run", "--rows", str(p)])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["unpriced_default_used"])
        self.assertEqual(data["unpriced_models"], [{"model": "gemini-3.8-pro-mystery", "count": 1}])
        self.assertIn("run_budget: run:", err)
        self.assertIn("unpriced model(s)", err)
        self.assertIn("gemini-3.8-pro-mystery", err)
        self.assertIn("default rates", err)


try:
    from test_run_budget_v3b import *  # noqa: F401, F403
except ImportError:
    from tests.test_run_budget_v3b import *  # noqa: F401, F403


if __name__ == "__main__":
    unittest.main()
