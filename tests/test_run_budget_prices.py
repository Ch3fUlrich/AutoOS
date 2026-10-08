#!/usr/bin/env python3
"""tests/test_run_budget_prices.py — pricing of unpriced Google-paid models.

A Google-paid model with no price row must never be under-counted: it is
priced at the highest known Gemini Flash rate in the prices file (highest
price_in, highest price_out and highest cache-read, each taken
independently), falling back to built-in worst-case constants when the file
has no priced Flash model at all.

Run with: python tests/test_run_budget_prices.py
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT))

import tools.run_budget as rb  # noqa: E402

PRICES_FILE = REPO_ROOT / "configuration" / "google-prices.json"
REGISTRY_NONE = str(REPO_ROOT / "no-such-registry.json")


def _row(model, provider, tin, tout, cache, ts="2026-10-03T10:00:00Z", status=None):
    row = {"timestamp": ts, "model": model, "provider": provider,
           "tokens": {"in": tin, "out": tout, "cacheRead": cache}}
    if status is not None:
        row["status"] = status
    return row


def _provider_block(price_in, price_out, cache_read, unverified=True):
    """A providers/vertex block shaped like the real price rows."""
    def _obj(per_million):
        return {"per_million": per_million, "per_token": per_million / 1e6,
                "url": "https://cloud.google.com/vertex-ai/generative-ai/pricing",
                "date": "2026-10-03", "unverified": unverified,
                "notes": "test rate"}
    return {"vertex": {"price_in": _obj(price_in), "price_out": _obj(price_out),
                       "price_cache_read": _obj(cache_read)}}


def _table(model_blocks):
    """name -> providers/vertex block, wrapped into a full price table."""
    return {"fallback": {"models": {name: {"providers": block}
                                     for name, block in model_blocks.items()}},
            "registry": {}}


def write_temp_prices(data):
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".prices.json", encoding="utf-8")
    json.dump(data, f)
    f.close()
    return Path(f.name)


class TestVertexSpellingsPriced(unittest.TestCase):
    """(a) The Vertex spellings the call log carries must now be priced from
    the real price file, not defaulted."""

    def test_vertex_flash_spellings_priced_from_real_file(self):
        table = rb.load_price_table()
        spellings = ["vertex-gemini-3.8-flash",
                     "vertex/gemini-3.8-flash",
                     "omniroute/vertex-gemini-3.8-flash"]
        # 230 rows total across the three spellings (the call log carries 230
        # on vertex-gemini-3.8-flash alone).
        rows = [_row(spellings[i % 3], "vertex", 20_000, 1_000, 5_000)
                for i in range(230)]
        res = rb.evaluate_run(rows, prices=table)
        self.assertEqual(res["calls"], 230)
        self.assertEqual(res["unpriced_models"], [])
        self.assertFalse(res["unpriced_default_used"])
        # Per row: 15000 uncached * 0.75/1M + 5000 cached * 0.1875/1M
        # + 1000 out * 3.75/1M = 0.01125 + 0.0009375 + 0.00375 = 0.0159375
        # 230 rows: 230 * 0.0159375 = 3.665625
        self.assertAlmostEqual(res["est_usd"], 3.665625, places=6)

    def test_each_spelling_resolves_at_075_375_01875(self):
        table = rb.load_price_table()
        for model in ("vertex-gemini-3.8-flash", "vertex/gemini-3.8-flash",
                      "omniroute/vertex-gemini-3.8-flash"):
            pr = rb.get_price(model, "vertex", table)
            self.assertEqual(pr["source"], "google-prices.json", model)
            self.assertEqual(pr["price_in"], 7.5e-07, model)
            self.assertEqual(pr["price_out"], 3.75e-06, model)
            self.assertEqual(pr["price_cache_read"], 1.875e-07, model)
            self.assertTrue(pr["unverified"], model)


class TestRegistryScopedProviderSpelling(unittest.TestCase):
    """(a2) LANE-PRICE-GAP (2026-10-08): the registry half of `get_price` keyed
    `models.<id>.provider_prices` by the spelling the row carried.

    A call-log row names the provider `vertex` (the gateway connection id) or
    `ovh` (the model prefix) while the price is filed under the registry id
    (`vertex_ai`, `ovhcloud`). The scoped lookup therefore missed and the row
    fell to the model-level row -- which for a two-price model id is the FREE
    provider's 0, i.e. no price at all. The lookup now goes through the one
    registry-derived normaliser tools/registry.py owns, the same one
    `autoos_usage` prices with.

    The gate's fallback policy is deliberately untouched: `configuration/
    google-prices.json` still answers what the registry cannot state, in the
    same order and with the same rates
    (test_the_prices_file_fallback_is_unchanged).
    """

    REG = {
        "providers": {
            "vertex_ai": {"id": "vertex_ai", "omniroute_id": "vertex", "tier": "credit"},
            "ovhcloud": {"id": "ovhcloud", "omniroute_id": "ovhcloud",
                         "model_prefix": "ovh", "tier": "credit"},
            "google_ai_studio": {"id": "google_ai_studio", "omniroute_id": "gemini",
                                 "tier": "free"},
        },
        "models": {
            "gemini-3.8-flash": {
                "id": "gemini-3.8-flash", "price_in": 0.0, "price_out": 0.0,
                "price_cache_read": 0.0,
                "provider_prices": {"vertex_ai": {
                    "price_in": 1e-06, "price_out": 3e-06, "price_cache_read": 1e-07,
                    "price_source": "unit-test", "price_as_of": "2026-10-08"}}},
            "gpt-oss-120b": {
                "id": "gpt-oss-120b", "price_in": 0.0, "price_out": 0.0,
                "price_cache_read": 0.0,
                "provider_prices": {"ovhcloud": {
                    "price_in": 2e-07, "price_out": 4e-07, "price_cache_read": 2e-08,
                    "price_source": "unit-test", "price_as_of": "2026-10-08"}}},
        },
    }

    def _table(self):
        return {"fallback": {}, "registry": copy.deepcopy(self.REG)}

    def test_the_gateway_connection_id_finds_the_provider_key_price(self):
        pr = rb.get_price("gemini-3.8-flash", "vertex", self._table())
        self.assertEqual(pr["source"], "registry")
        self.assertEqual((pr["price_in"], pr["price_out"], pr["price_cache_read"]),
                         (1e-06, 3e-06, 1e-07))

    def test_the_provider_key_spelling_costs_the_same_as_the_gateway_one(self):
        for spelling in ("vertex", "vertex_ai", "VERTEX"):
            pr = rb.get_price("gemini-3.8-flash", spelling, self._table())
            self.assertEqual(pr["source"], "registry", spelling)
            self.assertEqual(pr["price_in"], 1e-06, spelling)

    def test_the_model_prefix_namespace_finds_its_provider_key_price(self):
        pr = rb.get_price("gpt-oss-120b", "ovh", self._table())
        self.assertEqual(pr["source"], "registry", pr)
        self.assertEqual(pr["price_in"], 2e-07)

    def test_an_unknown_provider_never_borrows_another_providers_price(self):
        # The model-level row is the free leg's 0: an unresolvable provider reads
        # as unpriced, not as vertex_ai's money.
        pr = rb.get_price("gemini-3.8-flash", "ghost", self._table())
        self.assertEqual(pr["source"], "unpriced", pr)
        self.assertEqual(pr["price_in"], 0.0)

    def test_a_row_billed_under_the_gateway_id_is_priced_end_to_end(self):
        rows = [_row("gemini-3.8-flash", "vertex", 1_000_000, 100_000, 200_000)]
        res = rb.evaluate_run(rows, prices=self._table())
        # 800_000 uncached * 1e-06 + 200_000 cached * 1e-07 + 100_000 out * 3e-06
        # = 0.8 + 0.02 + 0.3 = 1.12
        self.assertAlmostEqual(res["est_usd"], 1.12, places=6)
        self.assertEqual(res["unpriced_models"], [])

    def test_the_prices_file_fallback_is_unchanged(self):
        # A registry row that cannot state a cache rate still loses to the
        # prices file, in the same order as before: the gate's fallback policy
        # is not this change's business.
        table = {"fallback": {"models": {"gemini-3.8-flash": {"providers":
                 _provider_block(0.75, 3.75, 0.1875)}}},
                 "registry": copy.deepcopy(self.REG)}
        del table["registry"]["models"]["gemini-3.8-flash"]["provider_prices"][
            "vertex_ai"]["price_cache_read"]
        pr = rb.get_price("gemini-3.8-flash", "vertex", table)
        self.assertEqual(pr["source"], "google-prices.json", pr)
        self.assertEqual(pr["price_in"], 7.5e-07)


class TestHighestFlashRateWins(unittest.TestCase):
    """(b) An unpriced model takes the highest price_in, highest price_out
    and highest cache-read independently from the priced Flash models."""

    def test_unpriced_takes_each_rate_independently(self):
        # A: higher price_in (1.2), lower price_out (3.0), lower cache (0.05)
        # B: lower price_in (0.9), higher price_out (4.5), higher cache (0.2)
        # C: unpriced third Flash model
        table = _table({
            "vertex-flash-a": _provider_block(1.2, 3.0, 0.05),
            "vertex-flash-b": _provider_block(0.9, 4.5, 0.2),
        })
        row = _row("vertex-flash-c", "vertex", 1_500_000, 200_000, 500_000)
        res = rb.evaluate_run([row], prices=table)
        # Uncached 1_000_000 * 1.2/1M = 1.2 (A's in)
        # Cached   500_000 * 0.2/1M = 0.1 (B's cache)
        # Out      200_000 * 4.5/1M = 0.9 (B's out)
        # Total = 2.2
        self.assertTrue(res["unpriced_default_used"])
        self.assertEqual(res["unpriced_models"], [{"model": "vertex-flash-c", "count": 1}])
        self.assertAlmostEqual(res["est_usd"], 2.2, places=6)

    def test_only_flash_models_are_considered(self):
        # A non-Flash row with a higher rate must not win the max.
        table = _table({
            "gemini-3.8-pro": _provider_block(9.0, 9.0, 9.0),
            "vertex-flash-a": _provider_block(1.2, 3.0, 0.05),
        })
        row = _row("vertex-flash-c", "vertex", 1_000_000, 0, 0)
        res = rb.evaluate_run([row], prices=table)
        # 1M uncached * 1.2/1M (the Flash max, not the Pro 9.0)
        self.assertAlmostEqual(res["est_usd"], 1.2, places=6)


class TestNoPricedFlashModel(unittest.TestCase):
    """(c) A prices file with no priced Flash model: built-in worst-case
    constants 0.75 / 3.75 / 0.1875 per 1M apply."""

    def test_builtin_constants_when_no_flash_model(self):
        data = {"models": {"gemini-3.8-pro": {"providers":
                _provider_block(0.5, 1.0, 0.05, unverified=False)}}}
        p = write_temp_prices(data)
        self.addCleanup(p.unlink)
        table = rb.load_price_table(registry_path=REGISTRY_NONE, prices_path=str(p))
        row = _row("vertex-gemini-9.0-flash-mystery", "vertex", 1_000_000, 300_000, 200_000)
        res = rb.evaluate_run([row], prices=table)
        # 800_000 uncached * 0.75/1M = 0.6
        # 200_000 cached   * 0.1875/1M = 0.0375
        # 300_000 out      * 3.75/1M   = 1.125
        # Total = 1.7625
        self.assertAlmostEqual(res["est_usd"], 1.7625, places=6)
        self.assertTrue(res["unpriced_default_used"])

    def test_empty_table_uses_builtin_constants(self):
        pr = rb.default_google_prices({"fallback": {}, "registry": {}})
        self.assertAlmostEqual(pr["price_in"], 7.5e-07, places=12)
        self.assertAlmostEqual(pr["price_out"], 3.75e-06, places=12)
        self.assertAlmostEqual(pr["price_cache_read"], 1.875e-07, places=12)
        self.assertTrue(pr["unverified"])
        self.assertEqual(pr["source"], "unpriced-default")


class TestUnpricedFlagSemantics(unittest.TestCase):
    """(d) unpriced_default_used stays true for an unpriced model and false
    when everything is priced; the unpriced_models list names the model."""

    def test_flag_true_for_unpriced_model(self):
        table = rb.load_price_table()
        row = _row("gemini-3.8-pro-mystery", "vertex", 1_000_000, 0, 0)
        res = rb.evaluate_run([row], prices=table)
        self.assertTrue(res["unpriced_default_used"])
        self.assertEqual(res["unpriced_models"], [{"model": "gemini-3.8-pro-mystery", "count": 1}])

    def test_flag_false_when_all_priced(self):
        table = rb.load_price_table()
        row = _row("gemini-3.8-flash", "vertex", 1_000_000, 0, 0)
        res = rb.evaluate_run([row], prices=table)
        self.assertFalse(res["unpriced_default_used"])
        self.assertEqual(res["unpriced_models"], [])
        self.assertAlmostEqual(res["est_usd"], 0.75, places=6)

    def test_day_unpriced_flag(self):
        table = rb.load_price_table()
        rows = [_row("vertex-gemini-3.8-flash", "vertex", 1_000_000, 0, 0)]
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=table)
        self.assertFalse(res["unpriced_default_used"])
        self.assertAlmostEqual(res["usd"], 0.75, places=6)


class TestZeroTokenRowsAreFree(unittest.TestCase):
    """(f) PRICE-GAP: a row that bills no tokens is not unpriced spend.

    Measured 2026-10-08 (workstation): 11 failed calls — vertex/claude-sonnet-5
    x5 (501), vertex/claude-opus-5 x5 (501), gemini/deep-research-max-preview-04-2026
    x1 (402), all with 0 tokens — were counted as unpriced rows priced at the
    Gemini Flash default, which is what raised the gate's PRICE-GAP flag.

    The rule: a row whose (in + out) token counts are both 0 costs $0 and is
    never registered in `unpriced` / `unpriced_default_used`, whatever its
    status. A non-2xx row that *does* carry tokens keeps the default: the
    provider may have billed partial work, and under-counting real tokens is
    the worse failure (so a bare non-2xx status is deliberately not a free
    pass). A 2xx row with tokens and no price keeps the default, unchanged.
    """

    def _table(self):
        # One priced Flash model (so the default rate is non-zero) and no row
        # for the Claude / deep-research models under test.
        return _table({"vertex-flash-a": _provider_block(1.2, 3.0, 0.05)})

    def test_failed_zero_token_unpriced_row_is_free_in_run(self):
        rows = [_row("vertex/claude-sonnet-5", "vertex", 0, 0, 0, status=501)
                for _ in range(5)]
        res = rb.evaluate_run(rows, prices=self._table())
        self.assertEqual(res["calls"], 5)
        self.assertEqual(res["est_usd"], 0.0)
        self.assertEqual(res["unpriced_models"], [])
        self.assertFalse(res["unpriced_default_used"])
        self.assertEqual(res["verdict"], "ok")

    def test_failed_zero_token_unpriced_row_is_free_in_day(self):
        rows = ([_row("vertex/claude-opus-5", "vertex", 0, 0, 0, status=501)
                 for _ in range(5)] +
                [_row("gemini/deep-research-max-preview-04-2026", "gemini",
                      0, 0, 0, status=402)])
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=self._table())
        self.assertEqual(res["usd"], 0.0)
        self.assertEqual(res["by_provider"], {"vertex": 0.0, "gemini": 0.0})
        self.assertEqual(res["unpriced_models"], [])
        self.assertFalse(res["unpriced_default_used"])
        self.assertEqual(res["verdict"], "ok")

    def test_priced_zero_token_row_is_not_counted_as_unpriced(self):
        # A successful call that reported no usage: still nothing to price.
        rows = [_row("vertex-flash-a", "vertex", 0, 0, 0, status=200)]
        res = rb.evaluate_run(rows, prices=self._table())
        self.assertEqual(res["est_usd"], 0.0)
        self.assertFalse(res["unpriced_default_used"])

    def test_ok_unpriced_row_with_tokens_still_uses_default(self):
        rows = [_row("vertex/claude-sonnet-5", "vertex", 1_000_000, 0, 0, status=200)]
        res = rb.evaluate_run(rows, prices=self._table())
        self.assertTrue(res["unpriced_default_used"])
        self.assertEqual(res["unpriced_models"], [{"model": "vertex/claude-sonnet-5", "count": 1}])
        self.assertAlmostEqual(res["est_usd"], 1.2, places=6)

    def test_failed_row_with_tokens_keeps_the_default_conservatively(self):
        # 4xx + tokens: a failed call is not proof that nothing was billed.
        rows = [_row("vertex/claude-opus-5", "vertex", 1_000_000, 0, 0, status=429)]
        res = rb.evaluate_run(rows, prices=self._table())
        self.assertTrue(res["unpriced_default_used"])
        self.assertEqual(res["unpriced_models"], [{"model": "vertex/claude-opus-5", "count": 1}])
        self.assertAlmostEqual(res["est_usd"], 1.2, places=6)

    def test_row_with_garbage_tokens_is_not_read_as_zero(self):
        # A non-numeric token field coerces to 0 but does not *prove* a free
        # call: it stays counted as unpriced spend and lands in bad_rows.
        rows = [_row("vertex/claude-opus-5", "vertex", "n/a", 0, 0, status=500)]
        res = rb.evaluate_run(rows, prices=self._table())
        self.assertEqual(res["bad_rows"], 1)
        self.assertTrue(res["unpriced_default_used"])

    def test_missing_token_block_is_free(self):
        rows = [{"timestamp": "2026-10-03T10:00:00Z", "model": "vertex/claude-opus-5",
                 "provider": "vertex", "status": 501}]
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=self._table())
        self.assertEqual(res["usd"], 0.0)
        self.assertFalse(res["unpriced_default_used"])

    def test_mixed_day_counts_only_the_rows_that_billed(self):
        rows = [_row("vertex/claude-sonnet-5", "vertex", 0, 0, 0, status=501)
                for _ in range(11)]
        rows.append(_row("vertex/claude-sonnet-5", "vertex", 100_000, 0, 0, status=200))
        res = rb.evaluate_day(rows, day_str="2026-10-03", prices=self._table())
        self.assertEqual(res["unpriced_models"], [{"model": "vertex/claude-sonnet-5", "count": 1}])
        self.assertAlmostEqual(res["usd"], 0.12, places=6)
        self.assertTrue(res["unpriced_default_used"])


class TestRealPriceFileConsistency(unittest.TestCase):
    """(e) The real configuration/google-prices.json passes the same
    consistency check the existing tests apply: it loads without raising,
    and the new rows are present with the exact rates."""

    def test_real_file_loads_and_rows_present(self):
        table = rb.load_price_table()
        models = (table.get("fallback") or {}).get("models") or {}
        for name in ("vertex-gemini-3.8-flash", "vertex/gemini-3.8-flash",
                     "omniroute/vertex-gemini-3.8-flash", "gemini-3.8-flash"):
            entry = models.get(name)
            self.assertIsInstance(entry, dict, name)
            p_entry = (entry.get("providers") or {}).get("vertex")
            self.assertIsInstance(p_entry, dict, name)
            self.assertEqual(_rate(p_entry, "price_in"), 7.5e-07, name)
            self.assertEqual(_rate(p_entry, "price_out"), 3.75e-06, name)
            self.assertEqual(_rate(p_entry, "price_cache_read"), 1.875e-07, name)


def _rate(p_entry, field):
    return rb._price_float(p_entry.get(field) or {})


if __name__ == "__main__":
    unittest.main()
