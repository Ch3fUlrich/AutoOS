"""T1-CREDIT-FIX-16 (D-274): never govern from old data + fail-closed fallbacks.

Red-first: old-stamped/past-month readings are NOT fresh reads; helper
fallback never crashes; stale markers with bad since fail closed.
Fake transports + tmp state dirs only; never the real container/gateway.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools"))
import autoos_usage as usage
import importlib.util
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "autoos_agent", _ROOT / "tools" / "autoos-agent.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

NOW = datetime.datetime(2026, 10, 15, 12, 0, tzinfo=datetime.timezone.utc)
SINCE = datetime.datetime(2026, 10, 1, 0, 0, tzinfo=datetime.timezone.utc)


def _reg_two_paid():
    return {
        "providers": {
            "deepseek": {"id": "deepseek", "tier": "paid",
                         "monthly_cap_usd": 25.0,
                         "monthly_warn_fraction": 0.8},
            "secondpaid": {"id": "secondpaid", "tier": "paid",
                           "monthly_cap_usd": 25.0,
                           "monthly_warn_fraction": 0.8},
        },
        "models": {},
        "routes": {},
        "policy": {"leg_rules": []},
    }


def _reg_credit_paid():
    reg = _reg_two_paid()
    reg["providers"]["ovhcloud"] = {
        "id": "ovhcloud", "tier": "credit",
        "monthly_cap_usd": 200.0, "monthly_warn_fraction": 0.8,
        "credit_grant_usd": 200.0,
    }
    return reg


def _ok(pid, note="ledger ok"):
    return {"provider": pid, "state": "ok", "spend_usd": 0.0,
            "spend_unknown": False, "cap_usd": 25.0, "warn_usd": 20.0,
            "models_unpriced": 0, "note": note}


def _limits(entries):
    return {"caches": {
        cid: {"plan": pid,
              "quotas": {"credits_usd": {"remaining": rem,
                                         "toppedUpBalance": 0,
                                         "grantedBalance": rem,
                                         "currency": "USD"}},
              "fetchedAt": stamp}
        for cid, pid, rem, stamp in entries}}


def _seed(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


class AFreshReadTests(unittest.TestCase):
    def test_old_stamped_reading_is_not_fresh_stales_that_provider(self):
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
                {"provider": "secondpaid", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
            ])
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 39.0, "2026-10-15T09:00:00Z"),
                ("c2", "secondpaid", 39.0, "2026-10-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            self.assertEqual(out["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since", out["deepseek"]["note"])
            self.assertIn("old reading", out["deepseek"]["note"])
            # other provider unaffected
            self.assertNotEqual(out["secondpaid"]["state"], "refuse")
            # appended but not latest: ledger has 3 lines, latest stays 10:00
            readings = usage.load_balance_readings(path)
            self.assertEqual(len(readings), 3)

    def test_past_month_reading_is_not_fresh(self):
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
                {"provider": "secondpaid", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
            ])
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 39.0, "2026-09-15T10:00:00Z"),
                ("c2", "secondpaid", 39.0, "2026-10-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            self.assertEqual(out["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since", out["deepseek"]["note"])
            self.assertNotEqual(out["secondpaid"]["state"], "refuse")

    def test_equal_stamp_counts_as_fresh(self):
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
            ])
            guards = {"deepseek": _ok("deepseek")}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 40.0, "2026-10-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            self.assertNotEqual(out["deepseek"]["state"], "refuse")

    def test_r1_no_series_past_month_stales(self):
        # CREDIT-16 R1 (D-274): a paid provider PRESENT in the payload but
        # with only a past-month reading stales even with no in-month
        # series; a provider absent from the payload stays untouched.
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 44.0, "2026-09-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            self.assertEqual(out["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since", out["deepseek"]["note"])
            self.assertIn("old reading", out["deepseek"]["note"])
            self.assertNotEqual(out["secondpaid"]["state"], "refuse")

    def test_r1_sep_only_ledger_newer_sep_payload_stales(self):
        # CREDIT-16 R1 (D-274): Sep-only ledger + a newer September payload
        # is still past-month (never fresh) -- the provider stales.
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-09-10T10:00:00Z",
                 "remaining": 50.0},
            ])
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 49.0, "2026-09-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            self.assertEqual(out["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since", out["deepseek"]["note"])
            self.assertNotEqual(out["secondpaid"]["state"], "refuse")


class BHelperFallbackTests(unittest.TestCase):
    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def _reg(self):
        return {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "credit_grant_usd": 200.0},
                "deepseek": {"id": "deepseek", "tier": "paid",
                             "monthly_cap_usd": 25.0,
                             "monthly_warn_fraction": 0.8},
            },
            "models": {},
            "routes": {},
            "policy": {"leg_rules": []},
        }

    def test_apply_cap_raise_refuses_paid_keeps_credit(self):
        reg = self._reg()
        def _boom_fetch(*a, **k):
            raise usage.UsageError("down (UsageError)")
        def _boom_helper(*a, **k):
            raise usage.UsageError("helper down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(usage, "apply_paid_local_cap",
                                   side_effect=RuntimeError("kaput")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_boom_fetch, env=env,
                    helper=_boom_helper)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("RuntimeError", guards["deepseek"]["note"])
        self.assertNotIn("kaput", guards["deepseek"]["note"])
        self.assertNotEqual(guards["ovhcloud"]["state"], "refuse")

    def test_credit_unreadable_raise_refuses_paid_keeps_credit(self):
        reg = self._reg()
        def _boom_fetch(*a, **k):
            raise usage.UsageError("down (UsageError)")
        def _boom_helper(*a, **k):
            raise usage.UsageError("helper down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(agent, "_credit_guards_unreadable",
                                   side_effect=ValueError("bad")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_boom_fetch, env=env,
                    helper=_boom_helper)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("ValueError", guards["deepseek"]["note"])
        self.assertNotEqual(guards.get("ovhcloud", {}).get("state"), "refuse")

    def test_r2_nonhelper_apply_cap_raise_refuses_paid_keeps_credit(self):
        # CREDIT-16 R2 (D-274): non-helper branch (fetch given, helper
        # None) -- apply_paid_local_cap raising still fail-closes paid
        # with TYPE only, credit stays fail-open, no crash.
        reg = self._reg()
        def _boom_fetch(*a, **k):
            raise usage.UsageError("down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(usage, "apply_paid_local_cap",
                                   side_effect=RuntimeError("kaput")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_boom_fetch, env=env, helper=None)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("RuntimeError", guards["deepseek"]["note"])
        self.assertNotIn("kaput", guards["deepseek"]["note"])
        self.assertNotEqual(guards["ovhcloud"]["state"], "refuse")

    def test_r2_nonhelper_unreadable_raise_refuses_paid_keeps_credit(self):
        # CREDIT-16 R2 (D-274): non-helper branch -- _credit_guards_
        # unreadable raising still fail-closes paid with TYPE only.
        reg = self._reg()
        def _boom_fetch(*a, **k):
            raise usage.UsageError("down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(agent, "_credit_guards_unreadable",
                                   side_effect=ValueError("bad")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_boom_fetch, env=env, helper=None)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("ValueError", guards["deepseek"]["note"])
        self.assertNotEqual(guards.get("ovhcloud", {}).get("state"), "refuse")

    def test_r3_guard_error_raise_still_refuses_every_paid(self):
        # CREDIT-16 R3 (D-274): _credit_guard_error raising leaves guards
        # empty -- paid refusals are built from the registry paid ids.
        reg = self._reg()
        def _boom_fetch(*a, **k):
            raise usage.UsageError("down (UsageError)")
        def _boom_helper(*a, **k):
            raise usage.UsageError("helper down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(usage, "apply_paid_local_cap",
                                   side_effect=RuntimeError("kaput")), \
                 mock.patch.object(agent, "_credit_guard_error",
                                   side_effect=OSError("no guard")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_boom_fetch, env=env,
                    helper=_boom_helper)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("RuntimeError", guards["deepseek"]["note"])
        self.assertNotIn("kaput", guards["deepseek"]["note"])

    def test_r3_refuse_raise_never_crashes(self):
        # CREDIT-16 R3 (D-274): refuse_paid_on_overlay_error raising
        # inside the handler must not crash the plan; paid still refuses.
        # Non-helper branch keeps the probe on the handler (no ledger
        # re-assert path).
        reg = self._reg()
        def _boom_fetch(*a, **k):
            raise usage.UsageError("down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(usage, "apply_paid_local_cap",
                                   side_effect=RuntimeError("kaput")), \
                 mock.patch.object(usage, "refuse_paid_on_overlay_error",
                                   side_effect=TypeError("refuse boom")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_boom_fetch, env=env, helper=None)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("RuntimeError", guards["deepseek"]["note"])

    def test_r3_helper_branch_reassert_raise_never_crashes(self):
        # CREDIT-17: helper branch -- the re-assert call (after ledger
        # consult) raising must not crash the plan; paid still refuses
        # with the fallback TYPE named. This exercises the path at
        # lines 5997-5999 where _fallback_overlay_type is re-asserted.
        reg = self._reg()
        def _boom_fetch(*a, **k):
            raise usage.UsageError("down (UsageError)")
        def _boom_helper(*a, **k):
            raise usage.UsageError("helper down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(usage, "apply_paid_local_cap",
                                   side_effect=RuntimeError("kaput")), \
                  mock.patch.object(usage, "refuse_paid_on_overlay_error",
                                    side_effect=TypeError("reassert boom")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_boom_fetch, env=env,
                    helper=_boom_helper)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("RuntimeError", guards["deepseek"]["note"])

    def test_r3_unforeseen_bug_refuse_raise_never_crashes(self):
        # CREDIT-17: unforeseen bug path -- the second
        # refuse_paid_on_overlay_error call (line 6011-6012) raising
        # must not crash the plan; paid still refuses with the TYPE
        # named. This exercises the `except Exception` block at
        # lines 6000-6012. We mock fetch_window on the agent's
        # usage_mod to raise an unforeseen exception (TypeError) since
        # fetch_window wraps all exceptions in UsageError.
        reg = self._reg()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            with mock.patch.object(agent.usage_mod, "read_manage_key",
                                    return_value="fake-key"), \
                 mock.patch.object(agent.usage_mod, "fetch_window",
                                    side_effect=TypeError("unforeseen boom")), \
                 mock.patch.object(usage, "refuse_paid_on_overlay_error",
                                    side_effect=TypeError("refuse boom")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=lambda *a, **k: None, env=env, helper=None)
        self.assertEqual(guards["deepseek"]["state"], "refuse")
        self.assertIn("TypeError", guards["deepseek"]["note"])


class CMinorTests(unittest.TestCase):
    def test_c1_bad_since_is_stale_with_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.jsonl")
            _seed(path, [
                {"stale": True, "since": None},
                {"stale": True, "since": 123, "provider": "deepseek"},
                {"stale": True, "provider": "other"},
            ])
            glob, per = usage._stale_state(path)
            self.assertIsNotNone(glob)
            self.assertIn("deepseek", per)
            self.assertIn("other", per)
            self.assertIsInstance(glob, str)
            self.assertIsInstance(per["deepseek"], str)

    def test_c2_credit_stays_fail_open(self):
        reg = _reg_credit_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
                # CREDIT-16 R5 (D-274): credit-tier ovhcloud holds the SAME
                # condition (in-month series, no reading in this payload)
                # yet stays fail-open while paid refuses.
                {"provider": "ovhcloud", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 150.0},
            ])
            guards = {"deepseek": _ok("deepseek"),
                      "ovhcloud": {"provider": "ovhcloud", "state": "unknown",
                                   "spend_usd": 0.0, "spend_unknown": True,
                                   "cap_usd": 200.0, "warn_usd": 160.0,
                                   "models_unpriced": 0, "note": "credit open"}}
            readings = [{"provider": "secondpaid",
                         "fetched_at": "2026-10-15T10:00:00Z",
                         "remaining": 5.0}]
            ledger = usage.load_balance_readings(path)
            usage._mark_missing_provider_reads(
                reg, guards, path, readings, ledger, SINCE, NOW)
            self.assertEqual(guards["deepseek"]["state"], "refuse")
            self.assertNotEqual(guards["ovhcloud"]["state"], "refuse")
            # apply_paid_local_cap keeps credit open too
            guards2 = {"deepseek": dict(guards["deepseek"]),
                       "ovhcloud": dict(guards["ovhcloud"])}
            guards2["deepseek"] = {"provider": "deepseek", "state": "unknown",
                                   "spend_usd": 0.0, "spend_unknown": True,
                                   "cap_usd": 25.0, "warn_usd": 20.0,
                                   "models_unpriced": 0, "note": "unmeasured"}
            usage.apply_paid_local_cap(reg, guards2, [], SINCE)
            self.assertNotEqual(guards2["ovhcloud"]["state"], "refuse")

    def test_c3_per_marker_keeps_global_since(self):
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            _seed(path, [{"stale": True, "since": "2026-10-10T00:00:00Z"}])
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
            ])
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            ledger = usage.load_balance_readings(path)
            usage._mark_missing_provider_reads(
                reg, guards, path, [], ledger, SINCE, NOW)
            _glob, per = usage._stale_state(path)
            self.assertEqual(per.get("deepseek"), "2026-10-10T00:00:00Z")

    def test_c4_reason_names_oldest_since(self):
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            _seed(path, [{"stale": True, "since": "2026-10-10T00:00:00Z"}])
            _seed(path, [{"stale": True, "since": "2026-10-12T00:00:00Z",
                          "provider": "deepseek"}])
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-10-15T10:00:00Z",
                 "remaining": 40.0},
            ])
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            ledger = usage.load_balance_readings(path)
            usage._mark_missing_provider_reads(
                reg, guards, path, [], ledger, SINCE, NOW)
            self.assertIn("2026-10-10T00:00:00Z",
                          guards["deepseek"]["note"])

    def test_c5_future_on_one_provider_is_per_provider(self):
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            future = (NOW + datetime.timedelta(days=2)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 40.0, future),
                ("c2", "secondpaid", 40.0, "2026-10-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            self.assertEqual(out["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since", out["deepseek"]["note"])
            self.assertNotEqual(out["secondpaid"]["state"], "refuse")
            glob, per = usage._stale_state(usage.balance_ledger_path(env))
            self.assertIsNone(glob)
            self.assertIn("deepseek", per)
            self.assertNotIn("secondpaid", per)

    def test_c5b_future_only_payload_stays_per_provider(self):
        # CREDIT-16 R4 (D-274): a payload with ONLY the future-stamped
        # provider still stales per-provider only -- global stays None.
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            future = (NOW + datetime.timedelta(days=2)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 40.0, future),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            self.assertEqual(out["deepseek"]["state"], "refuse")
            glob, per = usage._stale_state(path)
            self.assertIsNone(glob)
            self.assertIn("deepseek", per)
            self.assertIsNotNone(usage.load_balance_stale(path, "deepseek"))
            self.assertIsNone(usage.load_balance_stale(path, "secondpaid"))


class DCredit17Tests(unittest.TestCase):
    """CREDIT-17: present-but-unusable entries and month-start boundary."""

    def test_credit17_paid_present_but_unusable_no_series_stales(self):
        # CREDIT-17: a PAID provider whose only payload entry has an
        # unparseable/missing stamp and that has NO in-month series
        # must be staled (per-provider STALE), not left ok.
        reg = {
            "providers": {
                "deepseek": {"id": "deepseek", "tier": "paid",
                             "monthly_cap_usd": 25.0,
                             "monthly_warn_fraction": 0.8},
            },
            "models": {},
            "routes": {},
            "policy": {"leg_rules": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            guards = {"deepseek": _ok("deepseek")}
            # Empty ledger, payload has deepseek with garbage fetchedAt
            all_readings = [{"provider": "deepseek", "fetched_at": "garbage",
                             "remaining": 40.0}]
            readings = []  # fresh_ids empty (garbage stamp dropped)
            ledger = []  # no series
            newly = usage._mark_missing_provider_reads(
                reg, guards, path, readings, ledger, SINCE, NOW, all_readings)
            self.assertEqual(newly, ["deepseek"])
            self.assertEqual(guards["deepseek"]["state"], "refuse")
            self.assertIn("old reading for provider balance", guards["deepseek"]["note"])

    def test_credit17_credit_present_but_unusable_no_series_unchanged(self):
        # CREDIT-17: a CREDIT provider in the same situation stays
        # fail-open (unchanged).
        reg = {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "credit_grant_usd": 200.0},
            },
            "models": {},
            "routes": {},
            "policy": {"leg_rules": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            guards = {"ovhcloud": {"provider": "ovhcloud", "state": "ok",
                                   "spend_usd": 0.0, "spend_unknown": False,
                                   "cap_usd": 200.0, "warn_usd": 160.0,
                                   "models_unpriced": 0, "note": "ledger ok"}}
            all_readings = [{"provider": "ovhcloud", "fetched_at": "garbage",
                             "remaining": 150.0}]
            readings = []
            ledger = []
            newly = usage._mark_missing_provider_reads(
                reg, guards, path, readings, ledger, SINCE, NOW, all_readings)
            self.assertEqual(newly, [])
            self.assertEqual(guards["ovhcloud"]["state"], "ok")

    def test_credit17_month_start_boundary_exact_stamp_vs_one_sec_before(self):
        # CREDIT-17: month-start boundary test - stamp exactly at UTC
        # month start vs one second before. A `_ts <= since` mutant
        # should die.
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            # Seed with a reading exactly at month start
            _seed(path, [
                {"provider": "deepseek", "fetched_at": "2026-10-01T00:00:00Z",
                 "remaining": 40.0},
            ])
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            # Payload has a reading exactly at month start (should be fresh)
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 39.0, "2026-10-01T00:00:00Z"),
                ("c2", "secondpaid", 39.0, "2026-10-01T00:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            # Exactly at month start should be fresh (>= since)
            self.assertNotEqual(out["deepseek"]["state"], "refuse")
            self.assertNotEqual(out["secondpaid"]["state"], "refuse")

            # Now test one second before month start (should be past-month)
            guards2 = {"deepseek": _ok("deepseek"),
                       "secondpaid": _ok("secondpaid")}
            fetch2 = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 39.0, "2026-09-30T23:59:59Z"),
                ("c2", "secondpaid", 39.0, "2026-10-01T00:00:00Z"),
            ])).encode())
            out2 = usage.overlay_balance_guards(
                reg, guards2, "http://127.0.0.1:1", fetch2, env, SINCE, NOW)
            # One second before month start is past-month -> stale
            self.assertEqual(out2["deepseek"]["state"], "refuse")
            self.assertIn("balance stale since", out2["deepseek"]["note"])
            # secondpaid at exactly month start is fresh
            self.assertNotEqual(out2["secondpaid"]["state"], "refuse")


if __name__ == "__main__":
    unittest.main()
