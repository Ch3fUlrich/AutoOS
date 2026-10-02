"""T1-CREDIT-FIX-14 (D-274): stale balance ledger fails closed.

The provider-balance ledger must FAIL CLOSED: any failed read marks the
ledger STALE, paid guards refuse while stale, and only a successful read
clears it. A future-dated fetched_at counts as stale (clock skew tolerated).

All gateway/container I/O is faked (fake helper_fetch_fn callables, tmp
state dirs). The real container/gateway is never touched.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools"))

import autoos_resolver as r
import autoos_usage as usage

NOW = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
SINCE_MONTH = datetime.datetime(2026, 10, 1, 0, 0, tzinfo=datetime.timezone.utc)


def _reg_paid(cap=25.0):
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
        "policy": {"leg_rules": []},
    }


def _guards_ok(spend=0.0, note="ledger ok"):
    return {"deepseek": {"provider": "deepseek", "state": "ok",
                         "spend_usd": spend, "spend_unknown": False,
                         "cap_usd": 25.0, "warn_usd": 20.0,
                         "models_unpriced": 0, "note": note}}


def _limits_payload(entries):
    return {"caches": {
        cid: {"plan": provider,
              "quotas": {"credits_usd": {
                  "remaining": remaining, "toppedUpBalance": 0,
                  "grantedBalance": remaining, "currency": "USD"}},
              "fetchedAt": fetched_at}
        for cid, provider, remaining, fetched_at in entries}}


def _limits_body(entries):
    return json.dumps(_limits_payload(entries)).encode("utf-8")


def _env(tmp):
    state = os.path.join(tmp, "state")
    os.makedirs(state, exist_ok=True)
    return {"AUTOOS_STATE_DIR": state}


def _ledger_path(env):
    return usage.balance_ledger_path(env)


class StaleOnFailedReadTests(unittest.TestCase):
    def _run_failed(self, fetch):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                fetch, env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])
            since = usage.load_balance_stale(_ledger_path(env))
            self.assertIsNotNone(since)
            return since

    def test_helper_error_marks_stale(self):
        def fetch(url, headers, timeout):
            raise usage.UsageError("boom (ValueError)")
        self._run_failed(fetch)

    def test_oserror_marks_stale(self):
        def fetch(url, headers, timeout):
            raise OSError("down")
        self._run_failed(fetch)

    def test_non_200_marks_stale(self):
        self._run_failed(lambda u, h, t: (500, b"{}"))

    def test_unparseable_marks_stale(self):
        self._run_failed(lambda u, h, t: (200, b"not json{{{"))

    def test_non_dict_body_marks_stale(self):
        self._run_failed(lambda u, h, t: (200, b"[1,2]"))

    def test_empty_readings_marks_stale(self):
        self._run_failed(lambda u, h, t: (200, b"{}"))

    def test_value_error_from_helper_marks_stale(self):
        def fetch(url, headers, timeout):
            raise ValueError("bad")
        self._run_failed(fetch)


class StalePersistenceTests(unittest.TestCase):
    def test_second_failure_keeps_original_since(self):
        def fetch(url, headers, timeout):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                fetch, env, SINCE_MONTH, NOW)
            first = usage.load_balance_stale(_ledger_path(env))
            self.assertIsNotNone(first)
            later = NOW + datetime.timedelta(minutes=30)
            usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                fetch, env, SINCE_MONTH, later)
            second = usage.load_balance_stale(_ledger_path(env))
            self.assertEqual(first, second)

    def test_successful_read_clears_stale(self):
        def fail(url, headers, timeout):
            raise OSError("down")
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0}]
        body = _limits_body([("c1", "deepseek", 49.0,
                              "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)
            self.assertIsNotNone(
                usage.load_balance_stale(_ledger_path(env)))
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            self.assertIsNone(
                usage.load_balance_stale(_ledger_path(env)))
            g = out["deepseek"]
            self.assertNotIn("balance stale since", g.get("note") or "")
            self.assertIn("measured via provider balance", g["note"])
            self.assertEqual(g["spend_usd"], 1.0)

    def test_stale_marker_not_a_reading(self):
        def fail(url, headers, timeout):
            raise OSError("down")
        readings = [{"provider": "deepseek",
                     "fetched_at": "2026-10-01T08:00:00Z",
                     "remaining": 50.0}]
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), readings)
            before = usage.load_balance_readings(_ledger_path(env))
            usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)
            after = usage.load_balance_readings(_ledger_path(env))
            self.assertEqual(after, before)
            spend_before = usage.balance_month_spend(
                before, "deepseek", SINCE_MONTH)
            spend_after = usage.balance_month_spend(
                after, "deepseek", SINCE_MONTH)
            self.assertEqual(spend_after, spend_before)

    def test_successful_read_twice_idempotent(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0}]
        body = _limits_body([("c1", "deepseek", 49.0,
                              "2026-10-01T10:00:00Z")])
        ok = lambda u, h, t: (200, body)  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            out1 = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                ok, env, SINCE_MONTH, NOW)
            out2 = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                ok, env, SINCE_MONTH, NOW)
            readings = usage.load_balance_readings(_ledger_path(env))
            keys = [(x.get("provider"), x.get("fetched_at"))
                    for x in readings]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(len(readings), 2)
            self.assertEqual(out1["deepseek"]["state"],
                             out2["deepseek"]["state"])
            self.assertIsNone(
                usage.load_balance_stale(_ledger_path(env)))


class FutureDatedTests(unittest.TestCase):
    def test_future_fetched_at_is_stale(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0}]
        body = _limits_body([("c1", "deepseek", 49.0,
                              "2026-10-01T13:00:00Z")])  # NOW + 1h
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])
            self.assertIn("future-dated", g["note"])
            self.assertIsNotNone(
                usage.load_balance_stale(_ledger_path(env)))

    def test_within_skew_counts_as_fresh(self):
        # fetched_at +60 s is inside BALANCE_CLOCK_SKEW_S: normal path.
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0}]
        body = _limits_body([("c1", "deepseek", 49.0,
                              "2026-10-01T12:01:00Z")])  # NOW + 60s
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertNotIn("balance stale since", g.get("note") or "")
            self.assertIn("measured via provider balance", g["note"])

    def test_future_reading_never_latest(self):
        reg = _reg_paid()
        readings = [
            {"provider": "deepseek", "fetched_at": "2026-10-01T10:00:00Z",
             "remaining": 1.0},   # fresh + exhausted-looking
            {"provider": "deepseek", "fetched_at": "2026-10-01T13:00:00Z",
             "remaining": 500.0},  # future-dated: untrustworthy
        ]
        guard = usage.balance_paid_guard(
            reg, "deepseek", readings, SINCE_MONTH, NOW)
        # The future $500 reading must not be used as the latest reading
        # (which would read healthy): the series reads stale instead.
        self.assertIsNotNone(guard)
        self.assertEqual(guard["state"], "refuse")
        self.assertIn("balance stale since", guard["note"])
        self.assertIn("(D-274)", guard["note"])
        self.assertIn("future-dated", guard["note"])
        self.assertNotIn("500.00", guard["note"])


class StaleBeatsEstimateTests(unittest.TestCase):
    def test_stale_beats_local_estimate(self):
        guards = {"deepseek": {"provider": "deepseek", "state": "unknown",
                               "spend_usd": 5.0, "spend_unknown": True,
                               "cap_usd": 25.0, "warn_usd": 20.0,
                               "models_unpriced": 0,
                               "local_estimate": True,
                               "note": ("spend unmeasured; paid spend "
                                        "unmeasured - leg kept, local "
                                        "estimate $5.00 of $20 (D-240)")}}

        def fail(url, headers, timeout):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            out = usage.overlay_balance_guards(
                _reg_paid(), guards, "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])


class LedgerWriteFailureTests(unittest.TestCase):
    def test_write_oserror_refuses(self):
        body = _limits_body([("c1", "deepseek", 49.0,
                              "2026-10-01T10:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            import unittest.mock as mock
            with mock.patch.object(
                    usage, "record_balance_readings",
                    side_effect=OSError("disk full")):
                out = usage.overlay_balance_guards(
                    _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                    lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("(D-274)", g["note"])


class StaleResolverTests(unittest.TestCase):
    def test_stale_reason_visible_in_route_explain(self):
        # Through the real resolver call site: no fake that bypasses it.
        guards = {"deepseek": {"provider": "deepseek", "state": "unknown",
                               "spend_usd": 5.0, "spend_unknown": True,
                               "cap_usd": 25.0, "warn_usd": 20.0,
                               "models_unpriced": 0,
                               "local_estimate": True,
                               "note": ("spend unmeasured; paid spend "
                                        "unmeasured - leg kept, local "
                                        "estimate $5.00 of $20 (D-240)")}}

        def fail(url, headers, timeout):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            out = usage.overlay_balance_guards(
                _reg_paid(), guards, "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)
            reg = _reg_paid()
            warns = []
            kept, skipped, _notes = r.usable_legs(
                reg["routes"]["r-paid"],
                {"kind": "implement", "privacy": "public"},
                {"need_tokens": 10},
                {"opencode": {"installed": True, "signed_in": True,
                              "reason": ""}},
                reg, {}, credit_guards=out, credit_warns=warns)
            self.assertEqual(kept, [])
            blob_parts = []
            for v in list(skipped.values()) + warns:
                if isinstance(v, list):
                    blob_parts.extend(v)
                else:
                    blob_parts.append(v)
            blob = " ".join(blob_parts)
            self.assertIn("balance stale since", blob)
            self.assertIn("(D-274)", blob)


if __name__ == "__main__":
    unittest.main()
