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

import importlib.util
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "autoos_agent", _ROOT / "tools" / "autoos-agent.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

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


def _reg_two_paid(cap=25.0):
    reg = _reg_paid(cap)
    reg["providers"]["acme"] = {"id": "acme", "tier": "paid",
                                "monthly_cap_usd": cap,
                                "monthly_warn_fraction": 0.8}
    return reg


def _guards_two_ok(spend=0.0, note="ledger ok"):
    out = _guards_ok(spend, note)
    out["acme"] = {"provider": "acme", "state": "ok",
                   "spend_usd": spend, "spend_unknown": False,
                   "cap_usd": 25.0, "warn_usd": 20.0,
                   "models_unpriced": 0, "note": note}
    return out


def _keyed_env(tmp):
    cfg = os.path.join(tmp, "ai-stack")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "manage.key"), "w") as fh:
        fh.write("test-key-not-a-secret\n")
    return {"AUTOOS_AI_STACK_CONFIG": cfg,
            "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
            "AUTOOS_STATE_DIR": os.path.join(tmp, "state"),
            "HOME": tmp}


def _bare_env(tmp):
    state = os.path.join(tmp, "state")
    os.makedirs(state, exist_ok=True)
    return {"AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
            "AUTOOS_STATE_DIR": state,
            "HOME": tmp}


class NonUtf8LedgerTests(unittest.TestCase):
    """M1a (D-274): a non-UTF-8 ledger file never raises, never fails open."""

    def _write_raw(self, path, chunks):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            for chunk in chunks:
                fh.write(chunk)

    def test_garbage_bytes_are_skipped_like_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _ledger_path(_env(tmp))
            good = {"provider": "deepseek",
                    "fetched_at": "2026-10-01T08:00:00Z",
                    "remaining": 50.0}
            self._write_raw(path, [b"\xff\xfe\n",
                                   json.dumps(good).encode() + b"\n"])
            readings = usage.load_balance_readings(path)  # must not raise
            self.assertEqual(len(readings), 1)
            self.assertEqual(readings[0]["provider"], "deepseek")

    def test_stale_reader_survives_garbage_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _ledger_path(_env(tmp))
            self._write_raw(path, [b"\xff\xfe\n"])
            self.assertIsNone(usage.load_balance_stale(path))  # no raise

    def test_failed_read_with_garbage_ledger_still_refuses(self):
        def fail(url, headers, timeout):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            self._write_raw(_ledger_path(env), [b"\xff\xfe\n"])
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)  # must not raise
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])


class OverlayExceptionFailsClosedTests(unittest.TestCase):
    """M1b (D-274): ANY overlay exception refuses paid, names the TYPE only."""

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def test_patched_overlay_runtimeerror_refuses_paid(self):
        import unittest.mock as mock
        ok_fetch = lambda u, h, t: (200, b"[]")  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                    usage, "overlay_balance_guards",
                    side_effect=RuntimeError("super-secret-message")):
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW, fetch=ok_fetch,
                    env=_keyed_env(tmp),
                    helper=lambda u, h, t: (200, b"{}"))
        g = guards["deepseek"]
        self.assertEqual(g["state"], "refuse")
        self.assertIn("balance overlay failed (RuntimeError) (D-274)",
                      g["note"])
        self.assertNotIn("super-secret-message", g["note"])

    def test_helper_runtimeerror_on_limits_refuses_paid(self):
        def helper(url, headers, timeout):
            if "provider-limits" in url:
                raise RuntimeError("super-secret-message")
            return 200, b"[]"
        with tempfile.TemporaryDirectory() as tmp:
            guards = agent.plan_credit_guards(
                _reg_paid(), now=NOW, env=_bare_env(tmp), helper=helper)
        g = guards["deepseek"]
        self.assertEqual(g["state"], "refuse")
        self.assertIn("balance overlay failed (RuntimeError) (D-274)",
                      g["note"])
        self.assertNotIn("super-secret-message", g["note"])


class StaleOutsideRowsGateTests(unittest.TestCase):
    """M2 (D-274): STALE is consulted outside the rows_source gate."""

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def _fresh_reg(self):
        return _reg_paid()

    def test_dead_helper_with_clean_ledger_marks_stale_and_refuses(self):
        def dead(url, headers, timeout):
            raise OSError("container down")
        with tempfile.TemporaryDirectory() as tmp:
            env = _bare_env(tmp)
            guards = agent.plan_credit_guards(
                self._fresh_reg(), now=NOW, env=env, helper=dead)
            g = guards["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])
            self.assertNotIn("(D-240)", g["note"])
            since = usage.load_balance_stale(_ledger_path(env))
            self.assertIsNotNone(since)
            self.assertIn(since, g["note"])

    def test_stale_survives_dead_helper_then_clears_on_success(self):
        calls = {"n": 0}

        def flaky(url, headers, timeout):
            if calls["n"] == 1:
                raise OSError("container down")  # run 2: helper fully dead
            if "provider-limits" in url:
                if calls["n"] == 0:
                    return 200, b"{}"  # run 1: empty limits -> stale
                return 200, _limits_body(  # run 3: healthy readings
                    [("c1", "deepseek", 50.0, "2026-10-01T08:00:00Z"),
                     ("c2", "deepseek", 49.0, "2026-10-01T11:59:00Z")])
            return 200, b"[]"
        with tempfile.TemporaryDirectory() as tmp:
            env = _bare_env(tmp)
            agent.CREDIT_GUARD_CACHE.clear()
            run1 = agent.plan_credit_guards(
                self._fresh_reg(), now=NOW, env=env, helper=flaky)
            self.assertEqual(run1["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since", run1["deepseek"]["note"])
            since1 = usage.load_balance_stale(_ledger_path(env))
            self.assertIsNotNone(since1)
            calls["n"] = 1
            agent.CREDIT_GUARD_CACHE.clear()
            later = NOW + datetime.timedelta(hours=1)
            run2 = agent.plan_credit_guards(
                self._fresh_reg(), now=later, env=env, helper=flaky)
            g2 = run2["deepseek"]
            self.assertEqual(g2["state"], "refuse")  # still refuse, m4:
            # the retry is an hour later but the ORIGINAL since is kept
            self.assertIn("balance stale since %s" % since1, g2["note"])
            self.assertNotIn("(D-240)", g2["note"])  # not the estimate
            calls["n"] = 2
            agent.CREDIT_GUARD_CACHE.clear()
            run3 = agent.plan_credit_guards(
                self._fresh_reg(), now=NOW, env=env, helper=flaky)
            g3 = run3["deepseek"]
            self.assertIsNone(usage.load_balance_stale(_ledger_path(env)))
            self.assertNotIn("balance stale since", g3.get("note") or "")
            self.assertIn("measured via provider balance", g3["note"])


class PerProviderStaleTests(unittest.TestCase):
    """M3 (D-274): STALE is per provider; other providers are unaffected."""

    def test_payload_missing_a_series_provider_stales_only_it(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T01:00:00Z",  # 11 h old
                 "remaining": 2.00}]
        body = _limits_body([("c9", "acme", 40.0,
                              "2026-10-01T11:00:00Z")])  # only the other
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            out = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            ds = out["deepseek"]
            self.assertEqual(ds["state"], "refuse")
            self.assertIn("balance stale since", ds["note"])
            self.assertIn("(D-274)", ds["note"])
            # the per-provider marker is written for deepseek only
            since1 = usage.load_balance_stale(_ledger_path(env), "deepseek")
            self.assertIsNotNone(since1)
            self.assertIsNone(usage.load_balance_stale(_ledger_path(env),
                                                       "acme"))
            ac = out["acme"]
            self.assertNotIn("balance stale since", ac.get("note") or "")
            # a repeat failure an hour later keeps the FIRST since
            later = NOW + datetime.timedelta(hours=1)
            out2 = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, later)
            self.assertEqual(out2["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since %s" % since1,
                          out2["deepseek"]["note"])
            self.assertEqual(
                since1,
                usage.load_balance_stale(_ledger_path(env), "deepseek"))

    def test_payload_with_that_reading_clears_it(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T01:00:00Z",
                 "remaining": 2.00}]
        acme_only = _limits_body([("c9", "acme", 40.0,
                                   "2026-10-01T11:00:00Z")])
        body = _limits_body([("c1", "deepseek", 50.0,
                              "2026-10-01T11:30:00Z"),
                             ("c9", "acme", 40.0,
                              "2026-10-01T11:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            # seed the per-provider marker first: it must exist before the
            # clearing payload runs
            usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, acme_only), env, SINCE_MONTH, NOW)
            self.assertIsNotNone(
                usage.load_balance_stale(_ledger_path(env), "deepseek"))
            out = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            self.assertIsNone(
                usage.load_balance_stale(_ledger_path(env)))
            ds = out["deepseek"]
            self.assertNotIn("balance stale since", ds.get("note") or "")
            self.assertIn("measured via provider balance", ds["note"])

    def test_old_global_marker_still_stales_everyone_until_a_reading(self):
        def fail(url, headers, timeout):
            raise OSError("down")
        body = _limits_body([("c9", "acme", 40.0,
                              "2026-10-01T11:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)
            self.assertIsNotNone(
                usage.load_balance_stale(_ledger_path(env)))
            out = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            self.assertIsNone(
                usage.load_balance_stale(_ledger_path(env)))
            self.assertNotIn("balance stale since",
                             out["deepseek"].get("note") or "")


class MonthRolloverKeepsPerProviderMarkerTests(unittest.TestCase):
    """MAJOR (D-274): a persisted per-provider STALE marker is honoured on
    the overlay SUCCESS path even after that provider's in-month series
    ages out (month rollover)."""

    def test_rollover_payload_without_provider_still_refuses(self):
        sept = datetime.datetime(2026, 9, 1, 0, 0,
                                 tzinfo=datetime.timezone.utc)
        mark_day = datetime.datetime(2026, 9, 29, 12, 0,
                                     tzinfo=datetime.timezone.utc)
        october = datetime.datetime(2026, 10, 1, 0, 0,
                                    tzinfo=datetime.timezone.utc)
        oct2 = datetime.datetime(2026, 10, 2, 12, 0,
                                 tzinfo=datetime.timezone.utc)
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-09-05T01:00:00Z", "remaining": 50.0},
                {"provider": "acme",
                 "fetched_at": "2026-09-05T01:00:00Z", "remaining": 9.0}]
        sept_acme_only = _limits_body([("c9", "acme", 8.0,
                                        "2026-09-29T11:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            # September: a payload holding acme only refuses deepseek and
            # writes its per-provider marker with the 09-29 stamp.
            out1 = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, sept_acme_only), env, sept, mark_day)
            self.assertEqual(out1["deepseek"]["state"], "refuse")
            since1 = usage.load_balance_stale(_ledger_path(env), "deepseek")
            self.assertEqual("2026-09-29T12:00:00Z", since1)
            # October: the September series has aged out, but the marker
            # persists -- an acme-only payload must still refuse deepseek
            # with the ORIGINAL since, never ok from the call ledger.
            oct_acme_only = _limits_body([("c9", "acme", 7.0,
                                            "2026-10-02T11:00:00Z")])
            out2 = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, oct_acme_only), env, october, oct2)
            ds = out2["deepseek"]
            self.assertEqual(ds["state"], "refuse")
            self.assertIn("balance stale since 2026-09-29T12:00:00Z",
                          ds["note"])
            self.assertEqual(
                since1,
                usage.load_balance_stale(_ledger_path(env), "deepseek"))
            ac = out2["acme"]
            self.assertNotIn("balance stale since", ac.get("note") or "")
            # Only deepseek's own usable reading clears its marker.
            both = _limits_body([("c1", "deepseek", 49.0,
                                  "2026-10-02T11:30:00Z"),
                                 ("c9", "acme", 7.0,
                                  "2026-10-02T11:30:00Z")])
            out3 = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, both), env, october, oct2)
            self.assertIsNone(
                usage.load_balance_stale(_ledger_path(env), "deepseek"))
            self.assertNotIn("balance stale since",
                             out3["deepseek"].get("note") or "")


class ClearFailureStaysClosedTests(unittest.TestCase):
    """m5: an unwritable clear marker refuses, never opens."""

    def test_clear_oserror_stays_refuse(self):
        import unittest.mock as mock
        body = _limits_body([("c1", "deepseek", 50.0,
                              "2026-10-01T08:00:00Z"),
                             ("c2", "deepseek", 49.0,
                              "2026-10-01T11:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            with mock.patch.object(
                    usage, "_clear_balance_stale",
                    side_effect=OSError("disk full")):
                out = usage.overlay_balance_guards(
                    _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                    lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("(D-274)", g["note"])


class SkewBoundaryTests(unittest.TestCase):
    """m5: fetched_at == now+SKEW is fresh; +1 s is stale."""

    def test_exact_skew_is_fresh(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0}]
        body = _limits_body([("c1", "deepseek", 49.0,
                              "2026-10-01T12:05:00Z")])  # NOW + 300 s
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertNotIn("balance stale since", g.get("note") or "")
            self.assertIn("measured via provider balance", g["note"])

    def test_one_second_past_skew_is_stale(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T08:00:00Z", "remaining": 50.0}]
        body = _limits_body([("c1", "deepseek", 49.0,
                              "2026-10-01T12:05:01Z")])  # NOW + 301 s
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


class UnstampedReadingFailsClosedTests(unittest.TestCase):
    """MAJOR-1 rework 2 (D-274): only a STAMPED reading counts as 'read'.

    A paid-provider entry with a missing or garbage fetchedAt parses to an
    unusable reading (dropped on load) -- it must NOT count as a read.
    """

    def _missing_stamp_body(self):
        return json.dumps({"caches": {
            "c1": {"plan": "deepseek",
                   "quotas": {"credits_usd": {
                       "remaining": 0.0, "toppedUpBalance": 0,
                       "grantedBalance": 2.0, "currency": "USD"}}}}}
        ).encode("utf-8")

    def _garbage_stamp_body(self):
        return json.dumps({"caches": {
            "c1": {"plan": "deepseek",
                   "quotas": {"credits_usd": {
                       "remaining": 0.0, "toppedUpBalance": 0,
                       "grantedBalance": 2.0, "currency": "USD"}},
                   "fetchedAt": "garbage"}}}
        ).encode("utf-8")

    def _seed(self, env):
        usage.record_balance_readings(_ledger_path(env), [
            {"provider": "deepseek",
             "fetched_at": "2026-10-01T01:00:00Z", "remaining": 2.0}])

    def test_missing_fetched_at_refuses(self):
        body = self._missing_stamp_body()
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            self._seed(env)
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])
            self.assertIsNotNone(
                usage.load_balance_stale(_ledger_path(env)))

    def test_garbage_fetched_at_refuses(self):
        body = self._garbage_stamp_body()
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            self._seed(env)
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])
            self.assertIsNotNone(
                usage.load_balance_stale(_ledger_path(env)))

    def test_all_unstamped_payload_keeps_global_marker(self):
        def fail(url, headers, timeout):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)
            since1 = usage.load_balance_stale(_ledger_path(env))
            self.assertIsNotNone(since1)
            # The retry runs an hour later: a 'clear then re-mark' mutant
            # would stamp the new hour, so the unchanged `since` pins it.
            later = NOW + datetime.timedelta(hours=1)
            out = usage.overlay_balance_guards(
                _reg_paid(), _guards_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, self._missing_stamp_body()),
                env, SINCE_MONTH, later)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since %s" % since1, g["note"])
            self.assertEqual(since1,
                             usage.load_balance_stale(_ledger_path(env)))

    def test_unstamped_for_one_provider_stales_only_it(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T01:00:00Z", "remaining": 2.0},
                {"provider": "acme",
                 "fetched_at": "2026-10-01T01:00:00Z", "remaining": 9.0}]
        payload = {"caches": {
            "c1": {"plan": "deepseek",
                   "quotas": {"credits_usd": {
                       "remaining": 0.0, "toppedUpBalance": 0,
                       "grantedBalance": 2.0, "currency": "USD"}}},
            "c9": {"plan": "acme",
                   "quotas": {"credits_usd": {
                       "remaining": 8.0, "toppedUpBalance": 0,
                       "grantedBalance": 9.0, "currency": "USD"}},
                   "fetchedAt": "2026-10-01T11:00:00Z"}}}
        body = json.dumps(payload).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            out = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, body), env, SINCE_MONTH, NOW)
            ds = out["deepseek"]
            self.assertEqual(ds["state"], "refuse")
            self.assertIn("balance stale since", ds["note"])
            self.assertIsNotNone(
                usage.load_balance_stale(_ledger_path(env), "deepseek"))
            self.assertIsNone(
                usage.load_balance_stale(_ledger_path(env), "acme"))
            self.assertEqual(
                usage.load_balance_stale(_ledger_path(env), "deepseek"),
                usage.load_balance_stale(_ledger_path(env)))
            ac = out["acme"]
            self.assertNotIn("balance stale since", ac.get("note") or "")

    def test_unstamped_reading_clears_no_marker(self):
        seed = [{"provider": "deepseek",
                 "fetched_at": "2026-10-01T01:00:00Z", "remaining": 2.0},
                {"provider": "acme",
                 "fetched_at": "2026-10-01T01:00:00Z", "remaining": 9.0}]
        acme_only = _limits_body([("c9", "acme", 8.0,
                                   "2026-10-01T11:00:00Z")])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.record_balance_readings(_ledger_path(env), seed)
            usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, acme_only), env, SINCE_MONTH, NOW)
            since1 = usage.load_balance_stale(_ledger_path(env), "deepseek")
            self.assertIsNotNone(since1)
            payload = {"caches": {
                "c1": {"plan": "deepseek",
                       "quotas": {"credits_usd": {
                           "remaining": 0.0, "toppedUpBalance": 0,
                           "grantedBalance": 2.0, "currency": "USD"}}},
                "c9": {"plan": "acme",
                       "quotas": {"credits_usd": {
                           "remaining": 8.0, "toppedUpBalance": 0,
                           "grantedBalance": 9.0, "currency": "USD"}},
                       "fetchedAt": "2026-10-01T11:30:00Z"}}}
            out = usage.overlay_balance_guards(
                _reg_two_paid(), _guards_two_ok(), "http://127.0.0.1:1",
                lambda u, h, t: (200, json.dumps(payload).encode("utf-8")),
                env, SINCE_MONTH, NOW)
            self.assertEqual(out["deepseek"]["state"], "refuse")
            self.assertEqual(
                since1,
                usage.load_balance_stale(_ledger_path(env), "deepseek"))


class MarkStaleRaiseFailsClosedTests(unittest.TestCase):
    """MINOR-2 rework 2 (D-274): _mark_balance_stale raising past the rows
    handler still refuses paid, never crashes plan_credit_guards."""

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def test_mark_stale_raise_refuses_paid(self):
        import unittest.mock as mock

        def dead(url, headers, timeout):
            raise OSError("container down")
        # An unpredicted raise escaping the refuse helper (RuntimeError from
        # the marker write) must refuse paid, never crash the plan.
        with tempfile.TemporaryDirectory() as tmp:
            env = _bare_env(tmp)
            with mock.patch.object(
                    usage, "_mark_balance_stale",
                    side_effect=RuntimeError("disk")):
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW, env=env, helper=dead)
            g = guards["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("(D-274)", g["note"])
            self.assertIn("RuntimeError", g["note"])
            self.assertNotIn("disk", g["note"])
        # An OSError from the refuse helper itself takes the same path.
        with tempfile.TemporaryDirectory() as tmp:
            env = _bare_env(tmp)
            with mock.patch.object(
                    usage, "refuse_paid_without_balance_read",
                    side_effect=OSError("state dir gone")):
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW, env=env, helper=dead)
            g = guards["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("(D-274)", g["note"])
            self.assertIn("OSError", g["note"])
            self.assertNotIn("state dir gone", g["note"])


class GuardErrorPathFailsClosedTests(unittest.TestCase):
    """MINOR-3 rework 2 (D-274): the unforeseen-bug guard-error path
    refuses paid too; credit guards stay fail-open exactly as today."""

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def test_guard_error_path_refuses_paid(self):
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            env = _bare_env(tmp)
            with mock.patch.object(
                    usage, "read_manage_key",
                    side_effect=TypeError("bad key")):
                guards = agent.plan_credit_guards(
                    _reg_paid(), now=NOW, env=env,
                    helper=lambda u, h, t: (200, b"[]"))
            g = guards["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("(D-274)", g["note"])
            self.assertIn("TypeError", g["note"])
            self.assertNotIn("bad key", g["note"])
            self.assertNotIn("leg kept", g["note"])


def _reg_credit_paid(cap=25.0):
    reg = _reg_paid(cap)
    reg["providers"]["vertex_ai"] = {"id": "vertex_ai", "tier": "credit",
                                     "monthly_cap_usd": 250.0}
    return reg


def _guards_credit_paid_ok(note="ledger ok"):
    out = _guards_ok(0.0, note)
    out["vertex_ai"] = {"provider": "vertex_ai", "state": "unknown",
                        "spend_usd": 0.0, "spend_unknown": True,
                        "cap_usd": 250.0, "warn_usd": 200.0,
                        "models_unpriced": 0,
                        "note": "SPEND UNKNOWN: vertex_ai credit leg kept"}
    return out


class CreditStaysFailOpenTests(unittest.TestCase):
    """MINOR-3 rework 3 (D-274): each new fail-closed path refuses paid
    only -- the credit (trial grant) guard stays fail-open, untouched."""

    def test_overlay_error_refuses_paid_only(self):
        guards = _guards_credit_paid_ok()
        before = dict(guards["vertex_ai"])
        usage.refuse_paid_on_overlay_error(
            _reg_credit_paid(), guards, "RuntimeError")
        g = guards["deepseek"]
        self.assertEqual(g["state"], "refuse")
        self.assertIn("balance overlay failed (RuntimeError) (D-274)",
                      g["note"])
        self.assertEqual(before, guards["vertex_ai"])

    def test_no_balance_read_refuses_paid_only(self):
        guards = _guards_credit_paid_ok()
        before = dict(guards["vertex_ai"])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            usage.refuse_paid_without_balance_read(
                _reg_credit_paid(), guards, env, NOW)
            g = guards["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])
            since = usage.load_balance_stale(_ledger_path(env))
            self.assertIsNotNone(since)
            self.assertIn(since, g["note"])
        self.assertEqual(before, guards["vertex_ai"])

    def test_overlay_stale_refuses_paid_only(self):
        def fail(url, headers, timeout):
            raise OSError("down")
        guards = _guards_credit_paid_ok()
        before = dict(guards["vertex_ai"])
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            out = usage.overlay_balance_guards(
                _reg_credit_paid(), guards, "http://127.0.0.1:1",
                fail, env, SINCE_MONTH, NOW)
            g = out["deepseek"]
            self.assertEqual(g["state"], "refuse")
            self.assertIn("balance stale since", g["note"])
            self.assertIn("(D-274)", g["note"])
            self.assertEqual(before, out["vertex_ai"])


if __name__ == "__main__":
    unittest.main()
