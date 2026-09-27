#!/usr/bin/env python3
"""Tests for tools/autoos_usage.py (the `usage` subcommand of tools/autoos-agent.py, OR4).

The fixtures mirror the documented GET /api/usage/call-logs response shape at
OmniRoute release/v3.8.51: a JSON array of row objects with camelCase fields
(src/app/api/usage/call-logs/route.ts:228,280 returning NextResponse.json(filtered);
row fields from mapSummaryRow, src/lib/usage/callLogs.ts:462-507), notably the
NESTED tokens object {in, out, ...} (callLogs.ts:475-482) and sessionTag
(callLogs.ts:505). The error predicate is status >= 400 OR a truthy error field
(route.ts:49-51). Rows are newest-first (route.ts:218-225; SQL ORDER BY
cl.timestamp DESC LIMIT/OFFSET, callLogs.ts:1016) and there is no `since`
query param, so the tool pages by offset until it sees rows older than --since.

The gateway is never contacted: every test injects a fake fetch. The manage key
used here is a fixture string, asserted NOT to leak into any output.

Run directly, never through unittest discover:

    python3 tests/test_autoos_usage.py
"""
import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
AGENT = TOOLS / "autoos-agent.py"
sys.path.insert(0, str(TOOLS))

import autoos_usage as usage  # noqa: E402

NOW = datetime.datetime(2026, 9, 27, 6, 0, 0, tzinfo=datetime.timezone.utc)
CUTOFF = NOW - datetime.timedelta(hours=1)  # --since 1h
FIXTURE_KEY = "unit-test-manage-key-9f8b7c6d"  # fixture, asserted never printed


def row(ts, rid=None, provider="openrouter", model="deepseek-v4.1-flash",
        combo="tier3", tag="or4/lane-a", status=200, error=None, tin=100, tout=50):
    """One call-log row in the documented response shape (callLogs.ts:462-507)."""
    return {
        "id": rid or "id-%s" % ts,
        "timestamp": ts,
        "method": "POST",
        "path": "/v1/chat/completions",
        "status": status,
        "model": model,
        "requestedModel": model,
        "provider": provider,
        "providerDisplay": provider,
        "account": "acct-1",
        "connectionId": "conn-1",
        "duration": 123,
        "tokens": {"in": tin, "out": tout, "cacheRead": None, "cacheWrite": None,
                   "reasoning": None, "compressed": None},
        "apiKeyId": "key-id-1",
        "apiKeyName": "autoos",
        "comboName": combo,
        "comboStepId": None,
        "comboExecutionKey": None,
        "error": error,
        "correlationId": None,
        "sessionTag": tag,
    }


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fresh_rows(n, start, step_s=10, **kw):
    """n rows, newest first, starting at `start` and going back step_s seconds."""
    return [row(iso(start - datetime.timedelta(seconds=step_s * i)),
                rid="r-%d-%d" % (int(start.timestamp()), i), **kw)
            for i in range(n)]


class FakeFetch:
    """fetch(url, headers, timeout) -> (status, body); serves pages by offset."""

    def __init__(self, pages=None):
        self.pages = pages or {}  # offset -> (status, payload)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        offset = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0])
        status, payload = self.pages.get(offset, (200, []))
        return status, json.dumps(payload).encode("utf-8")

    def offsets(self):
        return [int(parse_qs(urlparse(c["url"]).query).get("offset", ["0"])[0])
                for c in self.calls]


class UsageCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "ai-stack"
        self.cfg.mkdir()
        (self.cfg / "manage.key").write_text(FIXTURE_KEY + "\n", encoding="utf-8")
        self.env = {"AUTOOS_AI_STACK_CONFIG": str(self.cfg),
                    "AUTOOS_OMNIROUTE_URL": "http://gw.invalid:20128",
                    "HOME": self.tmp.name}

    def run_cli(self, argv, fetch):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            rc = usage.main(argv, fetch=fetch, env=self.env, now=NOW)
        return rc, out.getvalue(), err.getvalue()

    def json_report(self, argv, fetch):
        rc, out, err = self.run_cli(argv + ["--json"], fetch)
        self.assertEqual(rc, 0, err)
        return json.loads(out)


class ParseSinceTests(unittest.TestCase):
    def test_relative_minutes_hours_days(self):
        self.assertEqual(usage.parse_since("30m", NOW), NOW - datetime.timedelta(minutes=30))
        self.assertEqual(usage.parse_since("6h", NOW), NOW - datetime.timedelta(hours=6))
        self.assertEqual(usage.parse_since("2d", NOW), NOW - datetime.timedelta(days=2))

    def test_iso_8601_z_and_offset(self):
        self.assertEqual(usage.parse_since("2026-09-27T05:00:00Z", NOW),
                         datetime.datetime(2026, 9, 27, 5, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(usage.parse_since("2026-09-27T07:00:00+02:00", NOW),
                         datetime.datetime(2026, 9, 27, 5, 0, tzinfo=datetime.timezone.utc))

    def test_bad_since_raises(self):
        for bad in ("", "yesterday", "10x", "2026-13-99"):
            with self.assertRaises(ValueError, msg=bad):
                usage.parse_since(bad, NOW)


class AggregationTests(unittest.TestCase):
    def test_groups_by_each_dimension(self):
        rows = [
            row(iso(NOW - datetime.timedelta(minutes=5)), rid="a",
                provider="openrouter", combo="tier3", tag="or4/lane-a", tin=100, tout=50),
            row(iso(NOW - datetime.timedelta(minutes=6)), rid="b",
                provider="openrouter", combo="tier3", tag="or4/lane-a", tin=200, tout=60),
            row(iso(NOW - datetime.timedelta(minutes=7)), rid="c",
                provider="groq", combo="tier2", tag="or4/lane-b", model="gpt-oss-120b",
                status=500, tin=300, tout=0),
            row(iso(NOW - datetime.timedelta(minutes=8)), rid="d",
                provider="groq", combo="tier2", tag="or4/lane-b", model="gpt-oss-120b",
                tin=400, tout=70),
        ]
        by = usage.aggregate(rows, ["provider", "combo", "lane", "model"])

        prov = {g["key"]: g for g in by["provider"]}
        self.assertEqual(prov["openrouter"]["calls"], 2)
        self.assertEqual(prov["openrouter"]["ok"], 2)
        self.assertEqual(prov["openrouter"]["errors"], 0)
        self.assertEqual(prov["openrouter"]["tokens_in"], 300)
        self.assertEqual(prov["openrouter"]["tokens_out"], 110)
        self.assertEqual(prov["groq"]["calls"], 2)
        self.assertEqual(prov["groq"]["ok"], 1)
        self.assertEqual(prov["groq"]["errors"], 1)
        self.assertEqual(prov["groq"]["tokens_in"], 700)
        # sorted by calls desc, ties by key asc
        self.assertEqual([g["key"] for g in by["provider"]], ["groq", "openrouter"])

        self.assertEqual({g["key"] for g in by["combo"]}, {"tier2", "tier3"})
        self.assertEqual({g["key"] for g in by["lane"]}, {"or4/lane-a", "or4/lane-b"})
        model = {g["key"]: g for g in by["model"]}
        self.assertEqual(model["gpt-oss-120b"]["calls"], 2)
        self.assertEqual(model["deepseek-v4.1-flash"]["calls"], 2)

    def test_untagged_lane(self):
        rows = [row(iso(NOW), rid="a", tag=None),
                row(iso(NOW), rid="b", tag=""),
                row(iso(NOW), rid="c", tag="or4/lane-a")]
        by = usage.aggregate(rows, ["lane"])
        lanes = {g["key"]: g["calls"] for g in by["lane"]}
        self.assertEqual(lanes["(untagged)"], 2)
        self.assertEqual(lanes["or4/lane-a"], 1)

    def test_no_combo_grouped_as_none(self):
        rows = [row(iso(NOW), rid="a", combo=None)]
        by = usage.aggregate(rows, ["combo"])
        self.assertEqual(by["combo"][0]["key"], "(none)")

    def test_error_counting(self):
        rows = [
            row(iso(NOW), rid="a", status=500),                    # status >= 400
            row(iso(NOW), rid="b", status=200, error="boom"),      # error field per route.ts:49-50
            row(iso(NOW), rid="c", status=200),                    # ok
            row(iso(NOW), rid="d", status=0),                      # active: neither ok nor error
        ]
        g = usage.totals(rows)
        self.assertEqual(g["calls"], 4)
        self.assertEqual(g["errors"], 2)
        self.assertEqual(g["ok"], 1)

    def test_missing_tokens_object_counts_zero(self):
        r = row(iso(NOW), rid="a")
        r["tokens"] = None
        g = usage.totals([r])
        self.assertEqual(g["tokens_in"], 0)
        self.assertEqual(g["tokens_out"], 0)


class PagingTests(UsageCliTests):
    def test_since_cutoff_stops_paging(self):
        # page 0: 500 rows (full page) all inside the window; page 1: another
        # full page whose tail crosses the cutoff -> stop after page 1, drop
        # the old rows, never ask for page 2.
        old = CUTOFF - datetime.timedelta(minutes=5)
        page0 = fresh_rows(500, NOW - datetime.timedelta(minutes=1), step_s=1)
        page1 = fresh_rows(497, NOW - datetime.timedelta(minutes=29), step_s=1)
        page1 += fresh_rows(3, old, step_s=60)
        fetch = FakeFetch({0: (200, page0), 500: (200, page1)})
        rep = self.json_report(["--since", "1h"], fetch)
        self.assertEqual(fetch.offsets(), [0, 500])
        self.assertEqual(rep["totals"]["calls"], 997)
        self.assertFalse(rep["truncated"])

    def test_short_page_is_the_tail(self):
        fetch = FakeFetch({0: (200, fresh_rows(12, NOW - datetime.timedelta(minutes=1)))})
        rep = self.json_report(["--since", "1h"], fetch)
        self.assertEqual(fetch.offsets(), [0])
        self.assertEqual(rep["totals"]["calls"], 12)

    def test_page_cap_says_so(self):
        # every page full and in-window -> hard cap at MAX_PAGES, note printed
        def endless(url, headers, timeout):
            offset = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0])
            rows = [row(iso(NOW - datetime.timedelta(seconds=60 + i)),
                        rid="r-%d-%d" % (offset, i)) for i in range(500)]
            return 200, json.dumps(rows).encode("utf-8")

        calls = []

        def counting(url, headers, timeout):
            calls.append(url)
            return endless(url, headers, timeout)

        rc, out, err = self.run_cli(["--since", "1h"], counting)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(calls), usage.MAX_PAGES)
        self.assertIn("cap", out.lower())

    def test_dedupes_rows_repeated_across_pages(self):
        # the route re-merges in-memory active rows into every page
        active = row(iso(NOW), rid="active-1", status=0)
        page0 = [active] + fresh_rows(499, NOW - datetime.timedelta(minutes=2), step_s=1)
        page1 = [active] + fresh_rows(499, NOW - datetime.timedelta(minutes=40), step_s=2)
        fetch = FakeFetch({0: (200, page0), 500: (200, page1)})
        rep = self.json_report(["--since", "1h"], fetch)
        self.assertEqual(rep["totals"]["calls"], 999)  # 500 + 500 - 1 duplicated active row


class CliTests(UsageCliTests):
    def test_401_exit3_without_key_leak(self):
        fetch = FakeFetch({0: (401, {"error": "Authentication required"})})
        rc, out, err = self.run_cli(["--since", "1h"], fetch)
        self.assertEqual(rc, 3)
        self.assertIn("401", err)
        self.assertNotIn(FIXTURE_KEY, out + err)

    def test_403_exit3_without_key_leak(self):
        fetch = FakeFetch({0: (403, {"error": "API key lacks 'manage' scope."})})
        rc, out, err = self.run_cli(["--since", "1h"], fetch)
        self.assertEqual(rc, 3)
        self.assertIn("403", err)
        self.assertNotIn(FIXTURE_KEY, out + err)

    def test_any_fetch_exception_exit3_without_its_text(self):
        # review-or2: an exception outside URLError/OSError (http.client's
        # IncompleteRead, RemoteDisconnected...) must not escape as a traceback;
        # its text may carry a key or body, so only the type name is shown.
        import http.client

        def broken(url, headers, timeout):
            raise http.client.IncompleteRead(FIXTURE_KEY.encode())

        rc, out, err = self.run_cli(["--since", "1h"], broken)
        self.assertEqual(rc, 3)
        self.assertIn("IncompleteRead", err)
        self.assertNotIn(FIXTURE_KEY, out + err)

    def test_gateway_unreachable_exit3(self):
        def down(url, headers, timeout):
            raise urllib.error.URLError("connection refused")

        rc, out, err = self.run_cli(["--since", "1h"], down)
        self.assertEqual(rc, 3)
        self.assertIn("gw.invalid:20128", err)
        self.assertNotIn(FIXTURE_KEY, out + err)

    def test_missing_key_file_exit3(self):
        (self.cfg / "manage.key").unlink()
        fetch = FakeFetch({0: (200, [])})
        rc, out, err = self.run_cli(["--since", "1h"], fetch)
        self.assertEqual(rc, 3)
        self.assertIn("manage.key", err)
        self.assertEqual(fetch.calls, [])  # never contacted the gateway

    def test_sends_bearer_header_and_paging_params(self):
        fetch = FakeFetch({0: (200, [])})
        rc, out, err = self.run_cli(["--since", "1h"], fetch)
        self.assertEqual(rc, 0, err)
        self.assertEqual(fetch.calls[0]["headers"]["Authorization"], "Bearer " + FIXTURE_KEY)
        q = parse_qs(urlparse(fetch.calls[0]["url"]).query)
        self.assertEqual(q["limit"], [str(usage.PAGE_LIMIT)])
        self.assertEqual(q["excludeTests"], ["1"])

    def test_json_shape(self):
        fetch = FakeFetch({0: (200, fresh_rows(3, NOW - datetime.timedelta(minutes=1)))})
        rep = self.json_report(["--since", "1h", "--by", "provider,lane"], fetch)
        self.assertEqual(set(rep), {"since", "pages", "truncated", "totals", "by"})
        self.assertEqual(set(rep["by"]), {"provider", "lane"})
        # The default shape is the token report it always was: cost columns
        # appear only when --cost asks for them (MUSEAPI step 5), so nothing
        # that reads `usage --json` today has to change.
        self.assertEqual(set(rep["totals"]),
                         {"calls", "ok", "errors", "tokens_in", "tokens_out"})
        entry = rep["by"]["provider"][0]
        self.assertEqual(set(entry), {"key", "calls", "ok", "errors", "tokens_in", "tokens_out"})

    def test_text_tables_one_per_dimension_sorted_by_calls_desc(self):
        rows = (fresh_rows(3, NOW - datetime.timedelta(minutes=1), provider="groq", tag=None)
                + fresh_rows(1, NOW - datetime.timedelta(minutes=2), provider="openrouter",
                             tag="or4/lane-a"))
        fetch = FakeFetch({0: (200, rows)})
        rc, out, err = self.run_cli(["--since", "1h", "--by", "provider,lane"], fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("provider", out)
        self.assertIn("lane", out)
        self.assertLess(out.index("groq"), out.index("openrouter"))
        self.assertIn("(untagged)", out)
        self.assertIn("or4/lane-a", out)
        self.assertNotIn(FIXTURE_KEY, out)

    def test_bad_since_exit2(self):
        rc, out, err = self.run_cli(["--since", "whenever"], FakeFetch())
        self.assertEqual(rc, 2)
        self.assertIn("--since", err)

    def test_bad_dimension_exit2(self):
        rc, out, err = self.run_cli(["--since", "1h", "--by", "provider,bogus"], FakeFetch())
        self.assertEqual(rc, 2)
        self.assertIn("bogus", err)


class CostTests(UsageCliTests):
    """MUSEAPI step 5: the same report, priced from catalog/ai-registry.json's
    price_in/price_out. Two things the tests exist to pin:

    - the UNIT. The registry stores USD PER TOKEN (spec 3.1: the contributor's
      $0.10/1M is 1e-07), so a cost is tokens * price. Dividing the token count
      by 1M as well would report a millionth of the spend and still look
      plausible on a free tier, which is the worst kind of wrong on a spend
      guard.
    - the flag. Cost is opt-in: `usage --json` readers (the heartbeat, the
      serve payload) get exactly the shape they get today.
    """

    PRICED = "muse-spark-1.3-contributor"

    def setUp(self):
        super().setUp()
        self.registry = Path(self.tmp.name) / "ai-registry.json"
        self.registry.write_text(json.dumps({"models": {
            self.PRICED: {"id": self.PRICED, "price_in": 1e-07, "price_out": 2e-07},
            "openai/gpt-oss-120b": {"id": "openai/gpt-oss-120b",
                                    "price_in": 1e-08, "price_out": 1e-08},
        }}), encoding="utf-8")

    def rows(self, model, tin=1000, tout=500, provider="meta-api", n=1, age_min=1):
        # age_min separates two batches' row ids: the fetcher dedupes by `id`,
        # which is derived from the start timestamp, so two batches built at the
        # same minute collapse into one row.
        return fresh_rows(n, NOW - datetime.timedelta(minutes=age_min),
                          model=model, provider=provider, tin=tin, tout=tout)

    def cost_report(self, rows, extra=()):
        fetch = FakeFetch({0: (200, rows)})
        argv = ["--since", "1h", "--by", "provider", "--registry", str(self.registry),
                "--cost"] + list(extra)
        return self.json_report(argv, fetch)

    def test_cost_columns_appear_only_when_asked_for(self):
        fetch = FakeFetch({0: (200, self.rows(self.PRICED))})
        plain = self.json_report(["--since", "1h", "--by", "provider"], fetch)
        self.assertNotIn("cost_in", plain["totals"])
        self.assertNotIn("cost_in", plain["by"]["provider"][0])
        priced = self.cost_report(self.rows(self.PRICED))
        self.assertIn("cost_in", priced["totals"])
        self.assertIn("cost_in", priced["by"]["provider"][0])

    def test_a_meta_api_row_gets_a_cost(self):
        rep = self.cost_report(self.rows(self.PRICED, tin=1000, tout=500))
        entry = rep["by"]["provider"][0]
        self.assertAlmostEqual(entry["cost_in"], 1000 * 1e-07, places=12)
        self.assertAlmostEqual(entry["cost_out"], 500 * 2e-07, places=12)
        self.assertAlmostEqual(rep["totals"]["cost_in"] + rep["totals"]["cost_out"],
                               0.0002, places=12)

    def test_one_million_tokens_at_ten_cents_per_million_costs_ten_cents(self):
        rep = self.cost_report(self.rows(self.PRICED, tin=1_000_000, tout=0))
        self.assertAlmostEqual(rep["totals"]["cost_in"], 0.10, places=12)

    def test_the_gateway_spelling_of_a_model_is_priced_too(self):
        # call_logs carry the gateway's own model id, which is prefixed with the
        # connection (meta-api/muse-spark-1.3-contributor). The registry keys the
        # bare model id, so a price lookup that only tries the exact string
        # would report every real call as free.
        exact = self.cost_report(self.rows(self.PRICED))
        prefixed = self.cost_report(self.rows("meta-api/" + self.PRICED))
        self.assertEqual(prefixed["totals"]["cost_in"], exact["totals"]["cost_in"])
        self.assertEqual(prefixed["totals"]["cost_out"], exact["totals"]["cost_out"])

    def test_a_model_id_that_contains_a_slash_is_matched_exactly_first(self):
        # groq's spelling IS "openai/gpt-oss-120b" in the registry: stripping a
        # leading provider from everything would price it as a different model.
        rep = self.cost_report(self.rows("openai/gpt-oss-120b", tin=1000, tout=0,
                                         provider="groq"))
        self.assertAlmostEqual(rep["totals"]["cost_in"], 1000 * 1e-08, places=12)

    def test_an_unpriced_model_costs_nothing_but_still_counts_tokens(self):
        rep = self.cost_report(self.rows("some-model-the-registry-does-not-know"))
        self.assertEqual(rep["totals"]["tokens_in"], 1000)
        self.assertEqual(rep["totals"]["cost_in"], 0.0)
        self.assertEqual(rep["totals"]["cost_out"], 0.0)

    def test_rows_sum_across_calls(self):
        rep = self.cost_report(self.rows(self.PRICED, tin=1000, tout=500, n=4))
        self.assertEqual(rep["totals"]["calls"], 4)
        self.assertAlmostEqual(rep["totals"]["cost_in"], 4 * 1000 * 1e-07, places=12)

    def test_the_text_table_shows_cost_only_with_the_flag(self):
        fetch = FakeFetch({0: (200, self.rows(self.PRICED))})
        rc, plain, _err = self.run_cli(["--since", "1h", "--by", "provider"], fetch)
        self.assertEqual(rc, 0)
        self.assertNotIn("cost", plain)
        rc, priced, _err = self.run_cli(
            ["--since", "1h", "--by", "provider", "--cost", "--registry", str(self.registry)],
            FakeFetch({0: (200, self.rows(self.PRICED))}))
        self.assertEqual(rc, 0)
        self.assertIn("cost_in", priced)
        self.assertIn("estimated cost", priced)

    def test_the_shipped_registry_prices_the_contributor_leg(self):
        # The fixture above proves the math; this proves the fact: the number a
        # reader of the spend guard sees for the Meta contributor is the docs'
        # $0.10 in / $0.20 out per 1M, not a stale or missing price.
        prices = usage.load_registry_prices(ROOT / "catalog" / "ai-registry.json")
        self.assertEqual(prices["muse-spark-1.3-contributor"], (1e-07, 2e-07))

    def test_an_unreadable_registry_prices_nothing_and_still_reports(self):
        fetch = FakeFetch({0: (200, self.rows(self.PRICED))})
        rep = self.json_report(["--since", "1h", "--by", "provider", "--cost",
                                "--registry", str(Path(self.tmp.name) / "nope.json")], fetch)
        self.assertEqual(rep["totals"]["cost_in"], 0.0)
        self.assertEqual(rep["totals"]["tokens_in"], 1000)
        # The source says null: the report must not claim the registry priced
        # these calls when nothing was read.
        self.assertIsNone(rep["cost"]["source"])

    def test_the_cost_block_names_its_source_and_what_it_could_not_price(self):
        # A spend guard whose numbers cannot be traced is not a guard: every
        # --cost report carries which file priced it and how many models it had
        # no price for, so a 0 row is readable as "free" or "unknown".
        rows = (self.rows(self.PRICED) + self.rows("a-model-nobody-priced", age_min=2))
        rep = self.cost_report(rows)
        self.assertEqual(rep["cost"]["source"], str(self.registry))
        self.assertEqual(rep["cost"]["models_unpriced"], 1)

    def test_no_cost_block_without_the_flag(self):
        fetch = FakeFetch({0: (200, self.rows(self.PRICED))})
        rep = self.json_report(["--since", "1h", "--by", "provider"], fetch)
        self.assertNotIn("cost", rep)

    def test_the_default_price_source_is_the_repo_registry(self):
        fetch = FakeFetch({0: (200, self.rows("muse-spark-1.3-contributor"))})
        rep = self.json_report(["--since", "1h", "--by", "provider", "--cost"], fetch)
        self.assertEqual(rep["cost"]["source"], "catalog/ai-registry.json")
        # $0.10 in / $0.20 out per 1M, 1000 tokens in: 1e-4.
        self.assertAlmostEqual(rep["totals"]["cost_in"], 0.0001, places=12)


class DelegationTests(unittest.TestCase):
    """tools/autoos-agent.py `usage` delegates the remaining argv to autoos_usage.main."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("autoos_agent_usage_test", str(AGENT))
        cls.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.agent)

    def test_usage_delegates_remaining_argv(self):
        with mock.patch.object(self.agent.usage_mod, "main", return_value=0) as m:
            rc = self.agent.main(["usage", "--since", "1h", "--by", "provider,lane", "--json"])
        m.assert_called_once_with(["--since", "1h", "--by", "provider,lane", "--json"])
        self.assertEqual(rc, 0)

    def test_usage_delegates_the_cost_flag(self):
        # MUSEAPI step 5's documented call: `autoos-agent.py usage --since 24h
        # --by provider --cost`. Delegation is what makes the flag reachable
        # from the agent CLI at all.
        with mock.patch.object(self.agent.usage_mod, "main", return_value=0) as m:
            rc = self.agent.main(["usage", "--since", "24h", "--by", "provider", "--cost"])
        m.assert_called_once_with(["--since", "24h", "--by", "provider", "--cost"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
