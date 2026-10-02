"""T1-CREDIT-FIX-17 (D-274): CREDIT-17 rework - present-but-unusable entries.

End-to-end tests through overlay_balance_guards (not private helpers):
- Empty ledger, payload = deepseek with fetchedAt "garbage" (and second case with
  fetchedAt missing) + a second paid provider with a good stamp ->
  deepseek refuse (STALE, reason names D-274/CREDIT-17), the other provider ok
- Credit provider in the same shape -> unchanged

MINOR-3: overlay-except wrap block test - monkeypatches refuse_paid_on_overlay_error
to raise inside that block and asserts paid refused + no crash.
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


class Credit17OverlayBalanceGuardsTests(unittest.TestCase):
    """End-to-end tests through overlay_balance_guards for CREDIT-17."""

    def test_credit17_garbage_stamp_deepseek_refused_other_paid_ok(self):
        # CREDIT-17: Empty ledger, payload has deepseek with garbage fetchedAt
        # + secondpaid with good stamp. deepseek must be refused (STALE,
        # reason names D-274/CREDIT-17), secondpaid must be ok.
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 40.0, "garbage"),
                ("c2", "secondpaid", 40.0, "2026-10-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            # deepseek: present but garbage stamp -> staled with "old reading"
            self.assertEqual(out["deepseek"]["state"], "refuse",
                             "deepseek with garbage fetchedAt must be refused")
            self.assertIn("balance stale since", out["deepseek"]["note"])
            self.assertIn("old reading for provider balance",
                          out["deepseek"]["note"])
            # secondpaid: valid stamp -> ok
            self.assertNotEqual(out["secondpaid"]["state"], "refuse",
                                "secondpaid with good stamp must not be refused")

    def test_credit17_missing_stamp_deepseek_refused_other_paid_ok(self):
        # CREDIT-17: Empty ledger, payload has deepseek with MISSING fetchedAt
        # + secondpaid with good stamp. deepseek must be refused (STALE),
        # secondpaid must be ok.
        reg = _reg_two_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            guards = {"deepseek": _ok("deepseek"),
                      "secondpaid": _ok("secondpaid")}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "deepseek", 40.0, None),  # missing fetchedAt
                ("c2", "secondpaid", 40.0, "2026-10-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            # deepseek: present but missing stamp -> staled with "old reading"
            self.assertEqual(out["deepseek"]["state"], "refuse",
                             "deepseek with missing fetchedAt must be refused")
            self.assertIn("balance stale since", out["deepseek"]["note"])
            self.assertIn("old reading for provider balance",
                          out["deepseek"]["note"])
            # secondpaid: valid stamp -> ok
            self.assertNotEqual(out["secondpaid"]["state"], "refuse",
                                "secondpaid with good stamp must not be refused")

    def test_credit17_credit_provider_garbage_stamp_unchanged(self):
        # CREDIT-17: Credit provider with garbage fetchedAt + paid provider
        # with good stamp -> credit unchanged (fail-open), paid handled normally
        reg = _reg_credit_paid()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "state")}
            path = usage.balance_ledger_path(env)
            guards = {"deepseek": _ok("deepseek"),
                      "ovhcloud": {"provider": "ovhcloud", "state": "ok",
                                   "spend_usd": 0.0, "spend_unknown": False,
                                   "cap_usd": 200.0, "warn_usd": 160.0,
                                   "models_unpriced": 0, "note": "ledger ok"}}
            fetch = lambda *a, **k: (200, json.dumps(_limits([
                ("c1", "ovhcloud", 150.0, "garbage"),
                ("c2", "deepseek", 40.0, "2026-10-15T10:00:00Z"),
            ])).encode())
            out = usage.overlay_balance_guards(
                reg, guards, "http://127.0.0.1:1", fetch, env, SINCE, NOW)
            # ovhcloud (credit): garbage stamp -> stays ok (fail-open)
            self.assertNotEqual(out["ovhcloud"]["state"], "refuse",
                                "credit provider must stay fail-open")
            # deepseek (paid): good stamp -> ok
            self.assertNotEqual(out["deepseek"]["state"], "refuse",
                                "paid provider with good stamp must not be refused")


class Minor3OverlayExceptWrapTests(unittest.TestCase):
    """MINOR-3: overlay-except wrap block test."""

    def setUp(self):
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)

    def _reg(self):
        return {
            "providers": {
                "deepseek": {"id": "deepseek", "tier": "paid",
                             "monthly_cap_usd": 25.0,
                             "monthly_warn_fraction": 0.8},
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "monthly_cap_usd": 200.0,
                             "monthly_warn_fraction": 0.8,
                             "credit_grant_usd": 200.0},
            },
            "models": {},
            "routes": {},
            "policy": {"leg_rules": []},
        }

    def test_minor3_overlay_except_wrap_refuses_paid_no_crash(self):
        # MINOR-3: The overlay-except wrap block (lines ~6103-6136 in
        # autoos-agent.py) has no test. This test monkeypatches
        # refuse_paid_on_overlay_error to raise inside that block and
        # asserts paid refused + no crash. The 'unwrap@6083' mutant must fail it.
        reg = self._reg()
        # Make the initial fetch succeed so rows_source is set and the main
        # try block doesn't trigger fallbacks. Then mock overlay_balance_guards
        # to raise in the helper branch.
        import json
        def _ok_fetch(url, headers, timeout):
            # Return valid HTTP response with empty call logs
            return (200, json.dumps([]).encode())
        def _ok_helper(*a, **k):
            raise usage.UsageError("helper down (UsageError)")
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AUTOOS_STATE_DIR": os.path.join(tmp, "s"),
                   "AUTOOS_OMNIROUTE_URL": "http://127.0.0.1:1",
                   "HOME": tmp}
            # Make overlay_balance_guards raise an exception to trigger the
            # except block in the helper branch (lines ~6106-6136)
            with mock.patch.object(usage, "read_manage_key", return_value="fake-key"), \
                 mock.patch.object(usage, "overlay_balance_guards",
                                    side_effect=RuntimeError("overlay boom")), \
                 mock.patch.object(usage, "refuse_paid_on_overlay_error",
                                    side_effect=TypeError("refuse boom")):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=_ok_fetch, env=env,
                    helper=_ok_helper)
        # Paid must be refused with the overlay exception TYPE named, no crash
        self.assertEqual(guards["deepseek"]["state"], "refuse",
                         "paid must be refused when overlay raises")
        self.assertIn("RuntimeError", guards["deepseek"]["note"],
                      "note must name the overlay exception TYPE")
        # Credit must stay fail-open
        self.assertNotEqual(guards.get("ovhcloud", {}).get("state"), "refuse",
                            "credit must stay fail-open")


if __name__ == "__main__":
    unittest.main()