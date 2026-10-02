"""T1-CREDIT-FIX-10 (red first): real meter without the manage key (D-250),
paid deepseek local cap while unmeasured (D-240), cache-read billing and the
provider-balance paid meter (D-253).

M1: `autoos_usage.helper_fetch` is the second fetch transport (docker exec
into the gateway container, GET only, same (status, body) page shape
`fetch_window` consumes, never touching a key). `plan_credit_guards` tries
the manage-key HTTP fetch first (key non-empty + 200), then the helper when
docker and the container exist, else the unknown/D-240 fallback. Guard notes
name the source.
M2: unknown paid spend is held to the registry `policy.paid_local_cap_usd`
(default $20): at/over -> REFUSE with the D-240 reason, below -> kept with
the exact D-240 line. Measured spend governs.
M4: `cacheRead` tokens bill at `price_cache_read` (10 % of price_in on the
named models); provider-limits balances become the paid meter (decreases sum
to month spend, top-ups excluded, <$3 fresh snapshot refuses, stale ignored,
source named, no-series falls back to ledger then D-240).

All gateway/container I/O is faked (fake subprocess runner / fake fetch).
The real container is never touched here.
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools"))

import autoos_resolver as r
import autoos_usage as usage

from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "autoos_agent", _ROOT / "tools" / "autoos-agent.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

NOW = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
SINCE_MONTH = datetime.datetime(2026, 10, 1, 0, 0, tzinfo=datetime.timezone.utc)


def _reg_paid(cap=25.0, local_cap=None):
    policy = {"leg_rules": []}
    if local_cap is not None:
        policy["paid_local_cap_usd"] = local_cap
    return {
        "providers": {
            "deepseek": {"id": "deepseek", "tier": "paid",
                         "monthly_cap_usd": cap,
                         "monthly_warn_fraction": 0.8},
        },
        "models": {
            "ds-model": {"id": "ds-model", "tool_calls": "proven",
                         "context_usable": {"tokens": 100000,
                                            "source": "default"},
                         "price_in": 1e-06, "price_out": 1e-06},
        },
        "routes": {"r-paid": {"id": "r-paid",
                              "legs": ["deepseek/ds-model"]}},
        "policy": policy,
    }


def _ds_row(rid, tokens, ts="2026-10-01T10:00:00Z", provider="deepseek",
            model="ds-model"):
    return {"id": rid, "provider": provider, "model": model,
            "tokens": tokens, "timestamp": ts, "status": 200}


def _keyed_env(tmp):
    cfg = os.path.join(tmp, "ai-stack")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "manage.key"), "w") as fh:
        fh.write("test-key-not-a-secret\n")
    return {"AUTOOS_AI_STACK_CONFIG": cfg,
            "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
            "AUTOOS_STATE_DIR": os.path.join(tmp, "state")}


def _bare_env(tmp):
    state = os.path.join(tmp, "state")
    os.makedirs(state, exist_ok=True)
    return {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
            "AUTOOS_STATE_DIR": state,
            "HOME": tmp}


def _legs_for(reg, route_id, guards):
    warns = []
    kept, skipped, _notes = r.usable_legs(
        reg["routes"][route_id], {"kind": "implement", "privacy": "public"},
        {"need_tokens": 10},
        {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
        reg, {}, credit_guards=guards, credit_warns=warns)
    return kept, skipped, warns


class FakeRunner:
    """Fake subprocess runner: path-prefix -> stdout bytes (or an exception)."""

    def __init__(self):
        self.calls = []
        self.by_path = {}

    def add(self, path_prefix, stdout=None, exc=None, returncode=0):
        self.by_path[path_prefix] = (stdout, exc, returncode)

    def __call__(self, argv, **kw):
        self.calls.append({"argv": list(argv), "kw": dict(kw)})
        script = argv[-1] if argv else ""
        for prefix, (stdout, exc, returncode) in self.by_path.items():
            if prefix in script:
                if exc is not None:
                    raise exc
                return types.SimpleNamespace(returncode=returncode,
                                             stdout=stdout)
        return types.SimpleNamespace(returncode=0, stdout=b"200\n[]")


def _helper_fetch(pages=None, exc=None):
    """A helper transport backed by FakeRunner (never the real container)."""
    runner = FakeRunner()
    if exc is not None:
        runner.add("/api/usage/", exc=exc)
    for path, body in (pages or {}).items():
        runner.add(path, stdout=b"200\n" + json.dumps(body).encode())
    def fetch(url, headers, timeout):
        return usage.helper_fetch(url, headers, timeout, _run=runner)
    fetch.runner = runner
    return fetch


class M1HelperTransportTests(unittest.TestCase):
    def test_helper_runs_docker_exec_with_a_fixed_get_script(self):
        rows = [_ds_row("h1", {"in": 10, "out": 5})]
        runner = FakeRunner()
        runner.add("/api/usage/call-logs",
                   stdout=b"200\n" + json.dumps(rows).encode())
        status, body = usage.helper_fetch(
            "http://127.0.0.1:20128/api/usage/call-logs"
            "?limit=1000&offset=0&excludeTests=1",
            {"Authorization": "Bearer whatever"}, 15, _run=runner)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode()), rows)
        argv = runner.calls[0]["argv"]
        self.assertEqual(argv[:6],
                          ["docker", "exec", "-w", "/app",
                           "autoos-omniroute", "node"])
        self.assertIn("--input-type=module", argv)
        script = argv[-1]
        self.assertIn("/api/usage/call-logs?limit=1000&offset=0&excludeTests=1",
                      script)
        self.assertIn("GET", script)
        self.assertIn("apiFetch", script)

    def test_helper_never_reads_or_passes_a_key(self):
        runner = FakeRunner()
        runner.add("/api/usage/call-logs", stdout=b"200\n[]")
        usage.helper_fetch("http://x/api/usage/call-logs?limit=1&offset=0&excludeTests=1",
                           {"Authorization": "Bearer SUPER-SECRET-KEY"},
                           15, _run=runner)
        call = runner.calls[0]
        self.assertNotIn("SUPER-SECRET-KEY", " ".join(call["argv"]))
        self.assertNotIn("env", call["kw"])

    def test_helper_pages_through_fetch_window(self):
        page0 = [_ds_row("p0-%d" % i, {"in": 100, "out": 10})
                 for i in range(500)]
        page1 = [_ds_row("p1", {"in": 50, "out": 5})]
        runner = FakeRunner()
        runner.add("offset=0", stdout=b"200\n" + json.dumps(page0).encode())
        runner.add("offset=500", stdout=b"200\n" + json.dumps(page1).encode())
        fetch = lambda url, headers, timeout: usage.helper_fetch(  # noqa: E731
            url, headers, timeout, _run=runner)
        with mock.patch.object(usage, "PAGE_LIMIT", 500):
            rows, pages, truncated = usage.fetch_window(
                fetch, "http://127.0.0.1:9", "", SINCE_MONTH)
        self.assertEqual(len(rows), 501)
        self.assertFalse(truncated)

    def test_helper_docker_missing_raises_usage_error(self):
        runner = FakeRunner()
        runner.add("/api/usage/", exc=FileNotFoundError("docker"))
        with self.assertRaises(usage.UsageError):
            usage.helper_fetch("http://x/api/usage/call-logs?limit=1&offset=0&excludeTests=1",
                               None, 15, _run=runner)

    def test_helper_nonzero_exit_raises_without_key_text(self):
        runner = FakeRunner()
        runner.add("/api/usage/", stdout=b"boom",
                   returncode=1)
        try:
            usage.helper_fetch("http://x/api/usage/call-logs?limit=1&offset=0&excludeTests=1",
                               {"Authorization": "Bearer K"}, 15, _run=runner)
        except usage.UsageError as exc:
            self.assertNotIn("K", str(exc))
        else:
            self.fail("expected UsageError")

    def test_helper_rejects_non_usage_paths(self):
        runner = FakeRunner()
        with self.assertRaises(usage.UsageError):
            usage.helper_fetch("http://x/api/admin/keys", None, 15,
                               _run=runner)
        self.assertEqual(runner.calls, [])


class M1SelectionOrderTests(unittest.TestCase):
    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def test_manage_key_200_names_its_source_and_never_touches_docker(self):
        rows = [_ds_row("m1", {"in": 1_000_000, "out": 0})]
        def ok_fetch(url, headers, timeout):
            return 200, json.dumps(rows).encode()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(usage, "helper_fetch") as helper_mock:
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW, fetch=ok_fetch,
                    env=_keyed_env(tmp))
        self.assertEqual(helper_mock.call_count, 0)
        self.assertIn("measured via manage key",
                      guards["deepseek"]["note"])

    def test_no_key_falls_to_the_helper_and_names_it(self):
        rows = [_ds_row("h1", {"in": 1_000_000, "out": 0})]
        fetch = _helper_fetch(
            {"/api/usage/call-logs": rows, "/api/usage/provider-limits":
             {"caches": {}}})
        with tempfile.TemporaryDirectory() as tmp:
            guards = agent.plan_credit_guards(
                _reg_paid(), now=NOW, env=_bare_env(tmp), helper=fetch)
        self.assertIn("measured via gateway helper",
                      guards["deepseek"]["note"])

    def test_helper_non_200_falls_back_to_unknown(self):
        runner = FakeRunner()
        runner.add("/api/usage/call-logs", stdout=b"403\n[]")
        def helper(url, headers, timeout):
            return usage.helper_fetch(url, headers, timeout, _run=runner)
        with tempfile.TemporaryDirectory() as tmp:
            guards = agent.plan_credit_guards(
                _reg_paid(), now=NOW, env=_bare_env(tmp), helper=helper)
        self.assertEqual(guards["deepseek"]["state"], "unknown")
        self.assertTrue(guards["deepseek"]["spend_unknown"])

    def test_docker_missing_falls_back_to_unknown(self):
        fetch = _helper_fetch(exc=FileNotFoundError("no docker"))
        with tempfile.TemporaryDirectory() as tmp:
            guards = agent.plan_credit_guards(
                _reg_paid(), now=NOW, env=_bare_env(tmp), helper=fetch)
        self.assertEqual(guards["deepseek"]["state"], "unknown")


class M2LocalCapTests(unittest.TestCase):
    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def test_policy_cap_defaults_to_20(self):
        self.assertEqual(usage.paid_local_cap_usd({}), 20.0)
        self.assertEqual(usage.paid_local_cap_usd({"policy": {}}), 20.0)

    def test_policy_cap_is_read_when_set(self):
        self.assertEqual(
            usage.paid_local_cap_usd({"policy": {"paid_local_cap_usd": 30}}),
            30.0)

    def test_unknown_with_no_records_keeps_at_zero(self):
        fetch = _helper_fetch(exc=OSError("down"))
        with tempfile.TemporaryDirectory() as tmp:
            guards = agent.plan_credit_guards(
                _reg_paid(), now=NOW, env=_bare_env(tmp), helper=fetch)
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "unknown")
        self.assertIn("paid spend unmeasured - leg kept, "
                      "local estimate $0.00 of $20 (D-240)", guard["note"])
        kept, skipped, warns = _legs_for(_reg_paid(), "r-paid", guards)
        self.assertIn(("deepseek", "ds-model"), kept)
        self.assertNotIn("deepseek/ds-model", skipped)
        self.assertTrue(any("local estimate $0.00 of $20 (D-240)" in w
                            for w in warns), warns)

    def test_estimate_between_local_cap_and_provider_cap_refuses(self):
        # $22 of partial rows: truncated -> unknown, but >= the $20 local
        # cap while below the $25 provider cap -> D-240 refuses.
        rows = [_ds_row("t1", {"in": 11_000_000, "out": 11_000_000})]
        def fetch(url, headers, timeout):
            return 200, json.dumps(rows).encode()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                    usage, "fetch_window",
                    return_value=(rows, 20, True)):
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW, fetch=fetch,
                    env=_keyed_env(tmp))
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "refuse")
        self.assertIn("paid spend unmeasured - local estimate $22.00 "
                      ">= $20 cap (D-240)", guard["note"])
        kept, skipped, _warns = _legs_for(_reg_paid(), "r-paid", guards)
        self.assertNotIn(("deepseek", "ds-model"), kept)
        self.assertIn("deepseek/ds-model", skipped)
        self.assertTrue(any("local estimate $22.00 >= $20 cap (D-240)" in r_
                            for r_ in skipped["deepseek/ds-model"]))

    def test_estimate_below_cap_keeps_with_the_exact_line(self):
        rows = [_ds_row("t1", {"in": 1_000_000, "out": 1_000_000})]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                    usage, "fetch_window",
                    return_value=(rows, 20, True)):
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW,
                    fetch=lambda u, h, t: (200, b"[]"),
                    env=_keyed_env(tmp))
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "unknown")
        self.assertIn("paid spend unmeasured - leg kept, "
                      "local estimate $2.00 of $20 (D-240)", guard["note"])

    def test_measured_spend_governs_and_carries_no_local_line(self):
        # $22 measured COMPLETE: warn under the provider cap; the local
        # estimate is ignored.
        rows = [_ds_row("t1", {"in": 11_000_000, "out": 11_000_000})]
        def fetch(url, headers, timeout):
            return 200, json.dumps(rows).encode()
        with tempfile.TemporaryDirectory() as tmp:
            guards = agent.plan_credit_guards(
                _reg_paid(), now=NOW, fetch=fetch, env=_keyed_env(tmp))
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "warn")
        self.assertNotIn("(D-240)", guard["note"])
        kept, skipped, _warns = _legs_for(_reg_paid(), "r-paid", guards)
        self.assertIn(("deepseek", "ds-model"), kept)

    def test_tokenless_rows_add_zero_and_are_counted(self):
        est = usage.local_paid_estimate(
            [_ds_row("n1", None), _ds_row("n2", {}),
             _ds_row("p1", {"in": 1_000_000, "out": 0})],
            _reg_paid(), SINCE_MONTH, "deepseek")
        self.assertEqual(est["spend_usd"], 1.0)
        self.assertEqual(est["unpriced_runs"], 2)

    def test_unpriced_runs_named_in_the_kept_line(self):
        rows = [_ds_row("n1", None), _ds_row("n2", None),
                _ds_row("p1", {"in": 1_000_000, "out": 0})]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                    usage, "fetch_window",
                    return_value=(rows, 20, True)):
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW,
                    fetch=lambda u, h, t: (200, b"[]"),
                    env=_keyed_env(tmp))
        self.assertIn("unpriced runs: 2", guards["deepseek"]["note"])


class M4CacheBillingTests(unittest.TestCase):
    def _reg(self):
        reg = _reg_paid()
        reg["models"]["ds-model"]["price_cache_read"] = 1e-07
        return reg

    def test_cache_read_bills_at_cache_price(self):
        reg = self._reg()
        prices = usage.prices_from_registry(reg)
        rows = [_ds_row("c1", {"in": 1_000_000, "out": 0,
                               "cacheRead": 500_000})]
        spend = usage.paid_spend(rows, prices, reg, SINCE_MONTH,
                                 provider="deepseek")
        # (1M - 0.5M) x 1e-06 + 0.5M x 1e-07 = 0.50 + 0.05
        self.assertAlmostEqual(spend["spend_usd"], 0.55)

    def test_no_cache_price_keeps_old_billing(self):
        reg = _reg_paid()
        prices = usage.prices_from_registry(reg)
        rows = [_ds_row("c1", {"in": 1_000_000, "out": 0,
                               "cacheRead": 500_000})]
        spend = usage.paid_spend(rows, prices, reg, SINCE_MONTH,
                                 provider="deepseek")
        self.assertAlmostEqual(spend["spend_usd"], 1.0)

    def test_cache_price_is_10_percent_of_price_in_on_named_models(self):
        reg = json.loads((_ROOT / "catalog" / "ai-registry.json")
                         .read_text(encoding="utf-8"))
        for mid in ("deepseek-v4.1-flash", "deepseek-flash",
                    "muse-spark-1.3-contributor"):
            model = reg["models"][mid]
            self.assertAlmostEqual(model["price_cache_read"],
                                   model["price_in"] * 0.1, msg=mid)

    def test_new_price_rows_are_citable(self):
        reg = json.loads((_ROOT / "catalog" / "ai-registry.json")
                         .read_text(encoding="utf-8"))
        di = reg["models"]["anthropic/claude-sonnet-4-6"]
        self.assertEqual((di["price_in"], di["price_out"]), (3e-06, 1.5e-05))
        self.assertIn("https://deepinfra.com/anthropic/claude-sonnet-4-6",
                      di["price_source"])
        self.assertIn("2026-10-01", di["price_source"])
        vx = reg["models"]["gemini-3.1-pro-preview"]["provider_prices"][
            "vertex_ai"]
        self.assertIn("https://cloud.google.com/vertex-ai/generative-ai/pricing",
                      vx["price_source"])
        self.assertEqual(vx["price_as_of"], "2026-10-01")

    def test_policy_cap_validates(self):
        import registry as regmod
        reg = json.loads((_ROOT / "catalog" / "ai-registry.json")
                         .read_text(encoding="utf-8"))
        problems = regmod.check_registry(reg)
        self.assertEqual(
            [p for p in problems if "paid_local_cap" in p], [])
        bad = {"providers": {}, "models": {},
               "policy": {"paid_local_cap_usd": -5}}
        self.assertTrue(any("paid_local_cap" in p
                            for p in regmod.check_registry(bad)))


def _limits_payload(entries):
    return {"caches": {
        cid: {"plan": provider,
              "quotas": {"credits_usd": {
                  "remaining": remaining, "toppedUpBalance": 0,
                  "grantedBalance": remaining, "currency": "USD"}},
              "fetchedAt": fetched_at}
        for cid, provider, remaining, fetched_at in entries}}


class M4BalanceMeterTests(unittest.TestCase):
    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def _plan(self, tmp, rows, limits, seed=()):
        fetch = _helper_fetch(
            {"/api/usage/call-logs": rows,
             "/api/usage/provider-limits": limits})
        state_dir = os.path.join(tmp, "state")
        if seed:
            path = os.path.join(state_dir, "routing",
                                "provider-balances.jsonl")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                for line in seed:
                    fh.write(json.dumps(line) + "\n")
        return agent.plan_credit_guards(
            _reg_paid(), now=NOW, env=_bare_env(tmp), helper=fetch)

    def test_month_spend_is_the_sum_of_decreases(self):
        seed = [{"provider": "deepseek", "fetchedAt": "2026-10-01T08:00:00Z",
                 "remaining": 50.0, "currency": "USD"}]
        limits = _limits_payload(
            [("c1", "deepseek", 30.0, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, [], limits, seed)
        guard = guards["deepseek"]
        self.assertEqual(guard["spend_usd"], 20.0)
        self.assertEqual(guard["state"], "warn")
        self.assertIn("measured via provider balance", guard["note"])

    def test_top_up_is_excluded_and_logged(self):
        seed = [{"provider": "deepseek", "fetchedAt": "2026-10-01T08:00:00Z",
                 "remaining": 30.0, "currency": "USD"}]
        limits = _limits_payload(
            [("c1", "deepseek", 70.0, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, [], limits, seed)
        guard = guards["deepseek"]
        self.assertEqual(guard["spend_usd"], 0.0)
        self.assertIn("top-up +$40.00", guard["note"])

    def test_exhausted_balance_refuses(self):
        limits = _limits_payload(
            [("c1", "deepseek", 2.5, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, [], limits)
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "refuse")
        self.assertIn("paid deepseek balance exhausted ($2.50 left)",
                      guard["note"])
        kept, skipped, _warns = _legs_for(_reg_paid(), "r-paid", guards)
        self.assertIn("deepseek/ds-model", skipped)

    def test_stale_snapshot_ignores_the_exhausted_check(self):
        seed = [{"provider": "deepseek", "fetchedAt": "2026-10-01T05:00:00Z",
                 "remaining": 2.5, "currency": "USD"}]
        limits = _limits_payload(
            [("c1", "deepseek", 2.5, "2026-10-01T05:30:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, [], limits, seed)
        # Same $0 spend, but the snapshot is 6.5 h old: no exhausted refuse.
        self.assertNotEqual(guards["deepseek"]["state"], "refuse")

    def test_no_series_falls_back_to_ledger_then_local(self):
        limits = _limits_payload(
            [("c1", "deepseek", 44.0, "2026-10-01T10:00:00Z")])
        rows = [_ds_row("l1", {"in": 1_000_000, "out": 0})]
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, rows, limits)
        guard = guards["deepseek"]
        self.assertEqual(guard["spend_usd"], 1.0)
        self.assertIn("measured via call ledger", guard["note"])

    def test_readings_dedupe_on_provider_and_fetched_at(self):
        seed = [{"provider": "deepseek", "fetchedAt": "2026-10-01T10:00:00Z",
                 "remaining": 44.0, "currency": "USD"}]
        limits = _limits_payload(
            [("c1", "deepseek", 44.0, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            self._plan(tmp, [], limits, seed)
            path = os.path.join(tmp, "state", "routing",
                                "provider-balances.jsonl")
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 1)

    def test_balance_spend_over_cap_refuses(self):
        seed = [{"provider": "deepseek", "fetchedAt": "2026-10-01T08:00:00Z",
                 "remaining": 50.0, "currency": "USD"}]
        limits = _limits_payload(
            [("c1", "deepseek", 10.0, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, [], limits, seed)
        self.assertEqual(guards["deepseek"]["state"], "refuse")




class T1CreditFix11R2OverlayWorseTests(unittest.TestCase):
    def test_ledger_refuse_survives_flat_balance_ok(self):
        reg = _reg_paid(cap=25.0)
        guards = {"deepseek": {"provider": "deepseek", "state": "refuse", "spend_usd": 30.0, "spend_unknown": False, "cap_usd": 25.0, "warn_usd": 20.0, "models_unpriced": 0, "note": "ledger $30"}}
        balanced = {"provider": "deepseek", "state": "ok", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": 25.0, "warn_usd": 20.0, "models_unpriced": 0, "note": "balance $0 [measured via provider balance]"}
        with mock.patch.object(usage, "balance_paid_guard", return_value=balanced):
            with mock.patch.object(usage, "parse_provider_limits", return_value=[]):
                out = usage.overlay_balance_guards(reg, guards, "http://127.0.0.1:1", lambda *a, **k: (200, b"{}"), {}, SINCE_MONTH, NOW)
        self.assertEqual(out["deepseek"]["state"], "refuse")
        self.assertEqual(out["deepseek"]["spend_usd"], 30.0)
        self.assertIn("ledger", out["deepseek"]["note"].lower() if isinstance(out["deepseek"]["note"], str) else "")
        self.assertIn("balance", out["deepseek"]["note"].lower())

    def test_balance_refuse_beats_ledger_ok(self):
        reg = _reg_paid(cap=25.0)
        guards = {"deepseek": {"provider": "deepseek", "state": "ok", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": 25.0, "warn_usd": 20.0, "models_unpriced": 0, "note": "ledger ok"}}
        balanced = {"provider": "deepseek", "state": "refuse", "spend_usd": 5.0, "spend_unknown": False, "cap_usd": 25.0, "warn_usd": 20.0, "models_unpriced": 0, "note": "balance exhausted [measured via provider balance]"}
        with mock.patch.object(usage, "balance_paid_guard", return_value=balanced):
            with mock.patch.object(usage, "parse_provider_limits", return_value=[]):
                out = usage.overlay_balance_guards(reg, guards, "http://127.0.0.1:1", lambda *a, **k: (200, b"{}"), {}, SINCE_MONTH, NOW)
        self.assertEqual(out["deepseek"]["state"], "refuse")
        self.assertEqual(out["deepseek"]["spend_usd"], 5.0)

    def test_ledger_unknown_yields_to_balance_ok(self):
        reg = _reg_paid(cap=25.0)
        guards = {"deepseek": {"provider": "deepseek", "state": "unknown", "spend_usd": 0.0, "spend_unknown": True, "cap_usd": 25.0, "warn_usd": 20.0, "models_unpriced": 0, "note": "ledger unknown"}}
        balanced = {"provider": "deepseek", "state": "ok", "spend_usd": 1.0, "spend_unknown": False, "cap_usd": 25.0, "warn_usd": 20.0, "models_unpriced": 0, "note": "balance ok [measured via provider balance]"}
        with mock.patch.object(usage, "balance_paid_guard", return_value=balanced):
            with mock.patch.object(usage, "parse_provider_limits", return_value=[]):
                out = usage.overlay_balance_guards(reg, guards, "http://127.0.0.1:1", lambda *a, **k: (200, b"{}"), {}, SINCE_MONTH, NOW)
        self.assertEqual(out["deepseek"]["state"], "ok")
        self.assertEqual(out["deepseek"]["spend_usd"], 1.0)


class T1CreditFix11R3SameBatchDedupeTests(unittest.TestCase):
    def test_same_batch_duplicates_add_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.jsonl")
            r1 = {"provider": "deepseek", "fetched_at": "2026-10-01T10:00:00Z", "remaining": 44.0}
            n = usage.record_balance_readings(path, [dict(r1), dict(r1)])
            self.assertEqual(n, 1)
            self.assertEqual(len(usage.load_balance_readings(path)), 1)


class T1CreditFix11R4HelperDotDotTests(unittest.TestCase):
    def test_dotdot_path_rejected_without_url_text(self):
        for url in ("http://127.0.0.1:1/api/usage/../etc/passwd", "http://127.0.0.1:1/api/usage/%2e%2e/x", "http://127.0.0.1:1/api/usage/../api/other"):
            called = []
            def _fail(*a, **k):
                called.append(1)
                raise AssertionError("subprocess must not run")
            with self.assertRaises(usage.UsageError) as ctx:
                usage.helper_fetch(url, None, 5, _run=_fail)
            msg = str(ctx.exception)
            self.assertEqual(called, [], url)
            self.assertIn("(ValueError)", msg, url)
            self.assertNotIn("127.0.0.1", msg)
            self.assertNotIn("..", msg)
            self.assertNotIn("etc", msg)

    def test_non_usage_path_uses_type_name_only(self):
        called = []
        def _fail(*a, **k):
            called.append(1)
            raise AssertionError("subprocess must not run")
        with self.assertRaises(usage.UsageError) as ctx:
            usage.helper_fetch("http://127.0.0.1:1/api/other", None, 5, _run=_fail)
        self.assertEqual(called, [])
        self.assertIn("(ValueError)", str(ctx.exception))
        self.assertNotIn("127.0.0.1", str(ctx.exception))


class T1CreditFix11R5PrefixedCachePriceTests(unittest.TestCase):
    def test_prefixed_rows_carry_10_percent(self):
        reg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog", "ai-registry.json")))
        for mid in ("deepseek/deepseek-v4.1-flash", "deepseek-v4-flash", "meta/muse-spark-1.3-contributor", "muse-spark"):
            row = reg["models"][mid]
            self.assertIn("price_cache_read", row, mid)
            self.assertAlmostEqual(row["price_cache_read"], 0.1 * row["price_in"], places=12, msg=mid)


class T1CreditFix11R6RemainingValidationTests(unittest.TestCase):
    def test_bad_remaining_skipped_never_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.jsonl")
            lines = [
                {"provider": "deepseek", "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0},
                {"provider": "deepseek", "fetched_at": "2026-10-01T09:00:00Z", "remaining": "oops"},
                {"provider": "deepseek", "fetched_at": "2026-10-01T09:30:00Z", "remaining": float("inf")},
                {"provider": "deepseek", "fetched_at": "2026-10-01T10:00:00Z", "remaining": 40.0},
            ]
            with open(path, "w", encoding="utf-8") as fh:
                for o in lines:
                    fh.write(json.dumps(o, allow_nan=True) + "\n")
            got = usage.load_balance_readings(path)
            self.assertEqual(len(got), 2)
            reg = _reg_paid(cap=25.0)
            guards = {"deepseek": {"provider": "deepseek", "state": "ok", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": 25.0, "warn_usd": 20.0, "models_unpriced": 0, "note": "ledger ok"}}
            with mock.patch.object(usage, "parse_provider_limits", return_value=[]):
                out = usage.overlay_balance_guards(reg, guards, "http://127.0.0.1:1", lambda *a, **k: (200, b"{}"), {"AUTOOS_STATE_DIR": tmp}, SINCE_MONTH, NOW)
            # No balance series reaches the ledger path (the bad-lines file
            # is not the ledger), so the ledger guard stands exactly.
            self.assertEqual(out["deepseek"]["state"], "ok")
            self.assertEqual(out["deepseek"]["spend_usd"], 0.0)
            self.assertEqual(out["deepseek"]["note"],
                             "ledger ok [measured via call ledger]")


class T1CreditFix12N1EstimateVsMeasuredTests(unittest.TestCase):
    """N1 (D-240/D-253): a D-240 local-estimate refuse ranks as UNKNOWN in the
    overlay merge, so MEASURED balance spend governs; a REAL measured ledger
    refuse still beats a flat balance; with no balance series D-240 stands.

    Real `balance_paid_guard` path throughout (seeded ledger series via
    `record_balance_readings`/`load_balance_readings` in a temp state dir):
    `balance_paid_guard` itself is never mocked here.
    """

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def _plan(self, tmp, rows, truncated, limits, seed=()):
        state = os.path.join(tmp, "state")
        os.makedirs(os.path.join(state, "routing"), exist_ok=True)
        if seed:
            usage.record_balance_readings(
                os.path.join(state, "routing", "provider-balances.jsonl"),
                seed)
        fetch = _helper_fetch({"/api/usage/provider-limits": limits})
        with mock.patch.object(
                usage, "fetch_window", return_value=(rows, 20, truncated)):
            return agent.plan_credit_guards(
                _reg_paid(), now=NOW, env=_bare_env(tmp), helper=fetch)

    def test_estimate_refuse_yields_to_measured_balance(self):
        rows = [_ds_row("t1", {"in": 11_000_000, "out": 11_000_000})]
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0}]
        limits = _limits_payload(
            [("c1", "deepseek", 49.0, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, rows, True, limits, seed)
        guard = guards["deepseek"]
        self.assertIn(guard["state"], ("ok", "warn"))
        self.assertEqual(guard["spend_usd"], 1.0)
        self.assertIn("measured via provider balance", guard["note"])
        self.assertNotIn(">= $20 cap (D-240)", guard["note"])

    def test_measured_ledger_refuse_survives_flat_balance(self):
        rows = [_ds_row("t1", {"in": 15_000_000, "out": 15_000_000})]
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 44.0}]
        limits = _limits_payload(
            [("c1", "deepseek", 44.0, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, rows, False, limits, seed)
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "refuse")
        self.assertEqual(guard["spend_usd"], 30.0)
        self.assertIn("provider balance", guard["note"])

    def test_estimate_refuse_stands_with_no_balance_series(self):
        rows = [_ds_row("t1", {"in": 11_000_000, "out": 11_000_000})]
        limits = _limits_payload(
            [("c1", "deepseek", 44.0, "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            guards = self._plan(tmp, rows, True, limits)
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "refuse")
        self.assertIn("paid spend unmeasured - local estimate $22.00 "
                      ">= $20 cap (D-240)", guard["note"])


class T1CreditFix12N2HelperPathCharTests(unittest.TestCase):
    def test_backslash_question_hash_rejected(self):
        for url in ("http://127.0.0.1:1/api/usage/%5cevil",
                    "http://127.0.0.1:1/api/usage/%3Fevil",
                    "http://127.0.0.1:1/api/usage/%23evil",
                    "http://127.0.0.1:1/api/usage/a%5Cb"):
            called = []
            def _fail(*a, **k):
                called.append(1)
                raise AssertionError("subprocess must not run")
            with self.assertRaises(usage.UsageError) as ctx:
                usage.helper_fetch(url, None, 5, _run=_fail)
            self.assertEqual(called, [], url)
            self.assertIn("(ValueError)", str(ctx.exception), url)

    def test_nested_usage_prefix_still_rejected(self):
        called = []
        def _fail(*a, **k):
            called.append(1)
            raise AssertionError("subprocess must not run")
        with self.assertRaises(usage.UsageError) as ctx:
            usage.helper_fetch("http://127.0.0.1:1/api/usage/../api/usage/x",
                               None, 5, _run=_fail)
        self.assertEqual(called, [])
        self.assertIn("(ValueError)", str(ctx.exception))


class T1CreditFix12N3BadLinesExactTests(unittest.TestCase):
    def test_bad_lines_skipped_and_balance_spend_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.jsonl")
            lines = [
                {"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0},
                {"provider": "deepseek",
                 "fetched_at": "2026-10-01T09:00:00Z",
                 "remaining": "oops"},
                {"provider": "deepseek",
                 "fetched_at": "2026-10-01T10:00:00Z", "remaining": 40.0},
            ]
            with open(path, "w", encoding="utf-8") as fh:
                for o in lines:
                    fh.write(json.dumps(o) + "\n")
            got = usage.load_balance_readings(path)
            self.assertEqual(len(got), 2)
            spend, _topups = usage.balance_month_spend(
                got, "deepseek", SINCE_MONTH)
            self.assertEqual(spend, 10.0)
            reg = _reg_paid(cap=25.0)
            guards = {"deepseek": {"provider": "deepseek", "state": "ok",
                                   "spend_usd": 0.0, "spend_unknown": False,
                                   "cap_usd": 25.0, "warn_usd": 20.0,
                                   "models_unpriced": 0, "note": "ledger ok"}}
            with mock.patch.object(usage, "balance_ledger_path",
                                   return_value=path):
                with mock.patch.object(usage, "parse_provider_limits",
                                       return_value=[]):
                    out = usage.overlay_balance_guards(
                        reg, guards, "http://127.0.0.1:1",
                        lambda *a, **k: (200, b"{}"), {}, SINCE_MONTH, NOW)
            self.assertEqual(out["deepseek"]["state"], "ok")
            self.assertEqual(out["deepseek"]["spend_usd"], 10.0)
            self.assertIn("measured via provider balance",
                          out["deepseek"]["note"])


class HelperWhitelistTests(unittest.TestCase):
    """T1-CREDIT-FIX-13 (D-269): helper_fetch is a WHITELIST BUILD.

    Only the two fixed relative paths the code itself builds are fetched,
    and the forwarded path+query is built from validated ints, never from
    the caller string. The fake runner records calls; every rejection must
    raise UsageError BEFORE any subprocess runs.
    """

    def assert_rejected(self, url):
        def _fail(*args, **kwargs):
            raise AssertionError("subprocess must not run for %r" % (url,))
        with self.assertRaises(usage.UsageError, msg=url) as ctx:
            usage.helper_fetch(url, None, 5, _run=_fail)
        self.assertIn("(ValueError)", str(ctx.exception), url)
        self.assertNotIn("127.0.0.1", str(ctx.exception), url)

    def test_accepted_call_logs_forwards_exact(self):
        runner = FakeRunner()
        runner.add("/api/usage/call-logs?limit=500&offset=0&excludeTests=1",
                   stdout=b"200\n[]")
        status, body = usage.helper_fetch(
            "http://127.0.0.1:9/api/usage/call-logs"
            "?limit=500&offset=0&excludeTests=1", None, 5, _run=runner)
        self.assertEqual((status, body), (200, b"[]"))
        script = runner.calls[0]["argv"][-1]
        self.assertIn(
            '"/api/usage/call-logs?limit=500&offset=0&excludeTests=1"',
            script)

    def test_accepted_provider_limits_forwards_bare_path(self):
        runner = FakeRunner()
        runner.add("/api/usage/provider-limits", stdout=b"200\n{}")
        status, body = usage.helper_fetch(
            "http://127.0.0.1:9/api/usage/provider-limits", None, 5,
            _run=runner)
        self.assertEqual((status, body), (200, b"{}"))
        script = runner.calls[0]["argv"][-1]
        self.assertIn('apiFetch("/api/usage/provider-limits",', script)
        self.assertNotIn("provider-limits?", script)

    def test_scheme_and_host_ignored(self):
        # The transport is the container, not the host: any scheme/host
        # with a whitelisted path still runs (ftp chosen here).
        runner = FakeRunner()
        runner.add("/api/usage/provider-limits", stdout=b"200\n{}")
        status, _ = usage.helper_fetch(
            "ftp://x/api/usage/provider-limits", None, 5, _run=runner)
        self.assertEqual(status, 200)

    def test_reordered_query_normalized_to_canonical(self):
        runner = FakeRunner()
        runner.add("/api/usage/call-logs", stdout=b"200\n[]")
        usage.helper_fetch(
            "http://127.0.0.1:9/api/usage/call-logs"
            "?offset=7&limit=50&excludeTests=1", None, 5, _run=runner)
        script = runner.calls[0]["argv"][-1]
        self.assertIn(
            '"/api/usage/call-logs?limit=50&offset=7&excludeTests=1"',
            script)

    def test_rejects_encoded_and_dotdot_paths(self):
        for path in ("/api/usage/%252e%252e/keys",
                     "/api/usage/%2e%2e/keys",
                     "/api/usage/../keys"):
            self.assert_rejected("http://127.0.0.1:9" + path)

    def test_rejects_nul_cr_lf_raw_and_encoded(self):
        base = "http://127.0.0.1:9/api/usage/call-logs"
        good_qs = "?limit=1&offset=0&excludeTests=1"
        self.assert_rejected(base + "\x00" + good_qs)  # NUL in path
        self.assert_rejected(base + good_qs + "\x00")  # NUL in query
        self.assert_rejected(base + "\r" + good_qs)  # CR in path
        self.assert_rejected(base + good_qs + "\n")  # LF in query
        self.assert_rejected(base + "?limit=1\r\n&offset=0&excludeTests=1")
        self.assert_rejected(base + "?limit=1%0d%0a&offset=0&excludeTests=1")
        self.assert_rejected(
            "http://127.0.0.1:9/api/usage/provider-limits%0D%0A")

    def test_rejects_any_query_on_provider_limits(self):
        for url in ("http://127.0.0.1:9/api/usage/provider-limits?foo=1",
                    "http://127.0.0.1:9/api/usage/provider-limits?limit=1",
                    "http://127.0.0.1:9/api/usage/provider-limits?"):
            self.assert_rejected(url)

    def test_rejects_bad_call_logs_queries(self):
        base = "http://127.0.0.1:9/api/usage/call-logs?"
        for qs in ("limit=1&offset=0&excludeTests=1&admin=1",  # extra key
                   "limit=1&limit=2&offset=0&excludeTests=1",  # duplicate
                   "limit=-1&offset=0&excludeTests=1",
                   "limit=1e3&offset=0&excludeTests=1",
                   "limit=+5&offset=0&excludeTests=1",
                   "limit= 5&offset=0&excludeTests=1",
                   "limit=1&offset=0&excludeTests=0",
                   "limit=1&offset=0",  # missing excludeTests
                   "limit=1&excludeTests=1",  # missing offset
                   "limit=&offset=0&excludeTests=1",
                   "limit=1&offset=0&excludeTests=1&",  # trailing &
                   "limit=1&&offset=0&excludeTests=1"):
            self.assert_rejected(base + qs)

    def test_rejects_fragment_backslash_tab_case_prefix(self):
        self.assert_rejected(
            "http://127.0.0.1:9/api/usage/provider-limits#frag")
        self.assert_rejected(
            "http://127.0.0.1:9/api/usage/call-logs"
            "?limit=1&offset=0&excludeTests=1#frag")
        self.assert_rejected("http://127.0.0.1:9/api/usage\\call-logs"
                             "?limit=1&offset=0&excludeTests=1")
        self.assert_rejected("http://127.0.0.1:9/api/usage/call-logs\t"
                             "?limit=1&offset=0&excludeTests=1")
        self.assert_rejected(
            "http://127.0.0.1:9/api/usage/call-logs/"
            "?limit=1&offset=0&excludeTests=1")
        self.assert_rejected("http://127.0.0.1:9/api/usage/Call-Logs"
                             "?limit=1&offset=0&excludeTests=1")
        self.assert_rejected("http://127.0.0.1:9/api/admin/keys")
        self.assert_rejected("http://127.0.0.1:9/api/usage/other")

    def test_rejects_semicolon_and_non_ascii(self):
        self.assert_rejected(
            "http://127.0.0.1:9/api/usage/provider-limits;jsessionid=1")
        self.assert_rejected("http://127.0.0.1:9/api/usage/call-logs"
                             "?limit=1;offset=0&excludeTests=1")
        self.assert_rejected("http://127.0.0.1:9/api/usage/café")


if __name__ == "__main__":
    unittest.main()
