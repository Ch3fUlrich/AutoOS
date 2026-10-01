"""T1-CREDIT-FIX-7 (red first): review findings on 12d19789 (CREDIT-FIX-6).

R1: is_spend_row must accept registry id, omniroute_id, and model-prefix rows.
R2: credit_started extends the measured window to the whole grant.
R3: pins that gateway_legs does NO credit gating (D-220 follow-up T1-COMBO3).
R4: nan/inf/huge spend is unknown / total, one bad row never kills the map.
R5: SPEND UNKNOWN loud line only for legs that survive reasons.
"""
import contextlib
import datetime
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
import unittest
import unittest.mock
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools"))

import autoos_resolver as r
import autoos_usage as usage
import registry as regmod

_ROOT = Path(__file__).resolve().parent.parent

NOW = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
SINCE_MONTH = datetime.datetime(2026, 10, 1, 0, 0, tzinfo=datetime.timezone.utc)

VERTEX_PRICE = (1.5e-06, 7.5e-06)
# 100M in + 30M out at vertex price = 150 + 225 = $375
BIG = {"in": 100_000_000, "out": 30_000_000}


def _reg():
    return {
        "providers": {
            "vertex_ai": {"id": "vertex_ai", "tier": "credit",
                          "omniroute_id": "vertex",
                          "credit_usd": 250.0, "monthly_cap_usd": 250.0,
                          "monthly_warn_fraction": 0.8,
                          "trains_on_prompts": False},
            "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                         "omniroute_id": "ovhcloud",
                         "model_prefix": "ovh",
                         "credit_usd": 200.0, "monthly_cap_usd": 200.0,
                         "monthly_warn_fraction": 0.8,
                         "trains_on_prompts": False},
            "deepseek": {"id": "deepseek", "tier": "paid",
                         "omniroute_id": "deepseek",
                         "monthly_cap_usd": 25.0,
                         "monthly_warn_fraction": 0.8},
            "google_ai_studio": {"id": "google_ai_studio", "tier": "free"},
        },
        "models": {
            "gemini-3.8-flash": {
                "id": "gemini-3.8-flash", "price_in": 0.0, "price_out": 0.0,
                "tool_calls": "proven",
                "context_usable": {"tokens": 100000, "source": "default"},
                "provider_prices": {
                    "vertex_ai": {"price_in": 1.5e-06, "price_out": 7.5e-06,
                                  "price_source": "https://example.invalid",
                                  "price_as_of": "2026-10-01"}}},
        },
        "routes": {},
        "policy": {"leg_rules": []},
    }


def _row(provider, model, tokens=None, ts="2026-10-01T10:00:00Z"):
    return {"id": "r-%s-%s" % (provider, ts), "provider": provider,
            "model": model, "tokens": tokens or dict(BIG),
            "timestamp": ts, "status": 200}


def _prices(reg):
    return usage.prices_from_registry(reg)


class R1GatewaySpellingsCount(unittest.TestCase):
    def test_vertex_gateway_id_counts(self):
        reg = _reg()
        rows = [_row("vertex", "vertex/gemini-3.8-flash")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["state"], "refuse",
                         "provider 'vertex' row must bill vertex_ai, got %r"
                         % (guards["vertex_ai"],))

    def test_vertex_registry_id_counts(self):
        reg = _reg()
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["state"], "refuse")

    def test_model_prefix_only_counts(self):
        # provider field carries the gateway spelling, model carries prefix.
        # The alias is read from the registry, never hardcoded: without a
        # registry only the id itself matches (old behaviour exactly).
        reg = _reg()
        rows = [_row("vertex", "vertex/gemini-3.8-flash")]
        self.assertTrue(usage.is_spend_row(rows[0], "vertex_ai", reg))
        self.assertFalse(usage.is_spend_row(rows[0], "vertex_ai"))

    def test_similar_prefix_does_not_count(self):
        self.assertFalse(usage.is_spend_row(
            _row("vertexish", "vertexish/gemini-3.8-flash"), "vertex_ai"))
        self.assertFalse(usage.is_spend_row(
            _row("google_ai_studio", "gemini-3.8-flash"), "vertex_ai"))

    def test_ai_studio_free_stays_zero(self):
        reg = _reg()
        rows = [_row("google_ai_studio", "gemini/gemini-3.8-flash",
                     tokens={"in": 10_000_000, "out": 1_000_000})]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["spend_usd"], 0.0)
        self.assertEqual(guards["vertex_ai"]["state"], "ok")


class R2CreditStartedWindow(unittest.TestCase):
    def test_grant_window_includes_prior_month(self):
        reg = _reg()
        reg["providers"]["vertex_ai"]["credit_started"] = "2026-09-01"
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash",
                     ts="2026-09-15T10:00:00Z")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH, today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["state"], "refuse",
                         "September spend must count when credit_started "
                         "is 2026-09-01, got %r" % (guards["vertex_ai"],))

    def test_absent_credit_started_stays_month_to_date(self):
        reg = _reg()
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash",
                     ts="2026-09-15T10:00:00Z")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH, today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["spend_usd"], 0.0)
        self.assertIn("month-to-date", guards["vertex_ai"]["note"])


class R3GatewayLegsUngated(unittest.TestCase):
    def test_gateway_legs_still_lists_credit_leg(self):
        # D-220 follow-up: gateway-side credit gate (T1-COMBO3).
        # Pins CURRENT (ungated) behaviour; change only with that gate.
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        found = None
        for rid, route in (reg.get("routes") or {}).items():
            for leg in (route.get("legs") or []):
                if leg.split("/")[0] in ("vertex", "vertex_ai"):
                    found = (rid, route)
                    break
            if found:
                break
        self.assertIsNotNone(found, "shipped registry must carry a vertex leg")
        rid, route = found
        legs = regmod.gateway_legs(route, reg)
        self.assertIn(route["legs"][0].split("/", 1)[1] if "/" in route["legs"][0] else route["legs"][0],
                      [str(x).split("/", 1)[-1] for x in legs],
                      "gateway_legs must still list the vertex leg %r in %s"
                      % (route["legs"], rid))


class R4NonFiniteSpend(unittest.TestCase):
    def test_nan_spend_is_unknown(self):
        reg = _reg()
        state, _note = usage.spend_guard(reg, "vertex_ai", float("nan"))
        self.assertEqual(state, "unknown")

    def test_inf_spend_is_unknown(self):
        reg = _reg()
        state, _note = usage.spend_guard(reg, "vertex_ai", float("inf"))
        self.assertEqual(state, "unknown")

    def test_as_int_total(self):
        self.assertEqual(usage._as_int(float("inf")), 0)
        self.assertEqual(usage._as_int(float("nan")), 0)
        self.assertEqual(usage._as_int(10 ** 400), 0)

    def test_one_bad_row_degrades_only_that_row(self):
        reg = _reg()
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash",
                     tokens={"in": float("inf"), "out": 5}),
                _row("vertex_ai", "vertex/gemini-3.8-flash",
                     tokens={"in": 100, "out": 100})]
        spend = usage.paid_spend(rows, _prices(reg), reg, SINCE_MONTH,
                                 provider="vertex_ai")
        self.assertEqual(spend["calls"], 1)
        self.assertGreater(spend["spend_usd"], 0.0)


class R6RefuseReasonNamesThreshold(unittest.TestCase):
    def test_reason_prints_grant_minus_margin(self):
        reg = _reg()
        rows = [_row("vertex", "vertex/gemini-3.8-flash")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["state"], "refuse")
        reg["routes"] = {"r-v": {"id": "r-v",
                                 "legs": ["vertex/gemini-3.8-flash"]}}
        reg["models"]["gemini-3.8-flash"]["tool_calls"] = "proven"
        warns = []
        _kept, skipped, _notes = r.usable_legs(
            reg["routes"]["r-v"], {"kind": "review", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        # $375 spend; the $250 grant less the $20 default margin = $230 stop.
        self.assertEqual(skipped["vertex/gemini-3.8-flash"],
                         ["credit exhausted vertex_ai $375.00/$230.00"])

    def test_margin_zero_refuses_at_the_cap(self):
        reg = _reg()
        reg["providers"]["vertex_ai"]["credit_hard_stop_margin_usd"] = 0
        rows = [_row("vertex", "vertex/gemini-3.8-flash")]
        guards = usage.credit_guards(reg, rows, SINCE_MONTH)
        self.assertEqual(guards["vertex_ai"]["hard_stop_usd"], 250.0)


class R5LoudLineOnlyForSurvivors(unittest.TestCase):
    def _vertex_route(self):
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        for rid, route in (reg.get("routes") or {}).items():
            legs = route.get("legs") or []
            if any(l.split("/")[0] in ("vertex", "vertex_ai") for l in legs):
                return reg, rid, route
        self.fail("shipped registry must carry a vertex leg")

    def _unknown_guards(self, reg):
        return usage.credit_guards_unreadable(reg, "test: no ledger")

    def test_skipped_leg_emits_no_kept_line(self):
        reg, rid, route = self._vertex_route()
        guards = self._unknown_guards(reg)
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "implement", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        skipped_ids = [s[0] if isinstance(s, (list, tuple)) else s
                       for s in skipped]
        vertex_skipped = [str(x) for x in skipped_ids
                          if str(x).split("/")[0] in ("vertex", "vertex_ai")]
        self.assertTrue(vertex_skipped,
                        "precondition: implement card must skip a vertex leg, "
                        "skipped=%r kept=%r" % (skipped, kept))
        self.assertTrue(
            any("tool_calls" in reason
                for leg in skipped for reason in skipped[leg]
                if str(leg).split("/")[0] in ("vertex", "vertex_ai")),
            "precondition: vertex leg skipped for tool_calls, got %r"
            % (skipped,))
        self.assertFalse(any("leg kept" in w and "vertex" in w for w in warns),
                         "skipped leg must not say 'kept': %r" % (warns,))
        self.assertFalse(any("SPEND UNKNOWN" in w and "vertex" in w
                             for w in warns),
                         "skipped leg must not say 'kept': %r" % (warns,))

    def test_kept_leg_emits_loud_line(self):
        reg, rid, route = self._vertex_route()
        guards = self._unknown_guards(reg)
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "review", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        kept_ids = [k if isinstance(k, str) else k[0] for k in kept]
        vertex_kept = [str(x) for x in kept_ids
                       if str(x).split("/")[0] in ("vertex", "vertex_ai")]
        self.assertTrue(vertex_kept,
                        "precondition: review card must keep a vertex leg, "
                        "kept=%r skipped=%r" % (kept, skipped))
        self.assertTrue(any("SPEND UNKNOWN" in w for w in warns),
                        "kept unknown-spend leg must warn LOUDLY")


# --- T1-CREDIT-FIX-8 -----------------------------------------------------
# C1: the grant window must actually be fetched. C2: a row billed under a
# different provider never bills the watched grant. C3: unreadable rows make
# the grant unknown, never silent $0. C4: the quiet unknown line, like the
# loud one, is only for survivors. C5: mutants that must die.

_AGENT_MOD = None


def _agent_mod():
    global _AGENT_MOD
    if _AGENT_MOD is None:
        path = _ROOT / "tools" / "autoos-agent.py"
        spec = importlib.util.spec_from_file_location(
            "autoos_agent_fix8", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _AGENT_MOD = mod
    return _AGENT_MOD


def _small_tokens():
    # 20M in + 6M out at the vertex list price = $30 + $45 = $75.
    return {"in": 20_000_000, "out": 6_000_000}


def _c1_rows():
    def row(rid, ts):
        return {"id": rid, "provider": "vertex",
                "model": "vertex/gemini-3.8-flash",
                "tokens": dict(_small_tokens()),
                "timestamp": ts, "status": 200}
    return [row("oct-a", "2026-10-20T10:00:00Z"),
            row("oct-b", "2026-10-05T10:00:00Z"),
            row("sep-20", "2026-09-20T10:00:00Z"),
            row("sep-05", "2026-09-05T10:00:00Z")]


def _c1_reg():
    reg = _reg()
    reg["providers"]["vertex_ai"]["credit_started"] = "2026-09-01"
    return reg


def _paged_fetch(rows):
    def fetch(url, headers, timeout):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        limit = int(qs.get("limit", ["500"])[0])
        offset = int(qs.get("offset", ["0"])[0])
        return 200, json.dumps(rows[offset:offset + limit]).encode("utf-8")
    return fetch


def _keyed_env(tmpdir):
    with open(os.path.join(tmpdir, "manage.key"), "w",
              encoding="utf-8") as fh:
        fh.write("test-manage-key")
    return {"AUTOOS_AI_STACK_CONFIG": tmpdir}


class C1FetchCutoffAndPaging(unittest.TestCase):
    def test_fetch_cutoff_is_earliest_grant_start(self):
        self.assertEqual(
            usage.fetch_cutoff(_c1_reg(), NOW),
            datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc))

    def test_fetch_cutoff_without_starts_is_month_start(self):
        self.assertEqual(usage.fetch_cutoff(_reg(), NOW), SINCE_MONTH)

    def test_paged_plan_guards_count_september(self):
        agent = _agent_mod()
        agent.CREDIT_GUARD_CACHE.clear()
        with tempfile.TemporaryDirectory() as tmp:
            env = _keyed_env(tmp)
            with unittest.mock.patch.object(usage, "PAGE_LIMIT", 1):
                guards = agent.plan_credit_guards(
                    _c1_reg(), now=NOW, fetch=_paged_fetch(_c1_rows()),
                    env=env)
        self.assertEqual(guards["vertex_ai"]["spend_usd"], 300.0)
        self.assertEqual(guards["vertex_ai"]["state"], "refuse")

    def test_paged_usage_cli_counts_september(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = os.path.join(tmp, "registry.json")
            with open(reg_path, "w", encoding="utf-8") as fh:
                json.dump(_c1_reg(), fh)
            env = _keyed_env(tmp)
            buf = io.StringIO()
            with unittest.mock.patch.object(usage, "PAGE_LIMIT", 1):
                with contextlib.redirect_stdout(buf):
                    rc = usage.main(
                        ["--since", "2026-10-01T00:00:00Z", "--json",
                         "--cost", "--registry", reg_path,
                         "--spend-since", "2026-10-01T00:00:00Z"],
                        fetch=_paged_fetch(_c1_rows()), env=env, now=NOW)
        self.assertEqual(rc, 0)
        guard = json.loads(buf.getvalue())["credit_guards"]["vertex_ai"]
        self.assertEqual(guard["spend_usd"], 300.0)
        self.assertEqual(guard["state"], "refuse")


class C2SpendRowProviderGate(unittest.TestCase):
    def _ds(self):
        return {"providers": {
            "deepseek": {"id": "deepseek", "tier": "paid",
                         "omniroute_id": "deepseek",
                         "model_prefix": "deepseek"}}}

    def test_openrouter_deepseek_model_does_not_bill_deepseek(self):
        self.assertFalse(usage.is_spend_row(
            {"provider": "openrouter", "model": "deepseek/deepseek-v3-flash"},
            "deepseek", self._ds()))

    def test_zen_deepseek_model_does_not_bill_deepseek(self):
        self.assertFalse(usage.is_spend_row(
            {"provider": "opencode-zen",
             "model": "deepseek/deepseek-v3-flash"},
            "deepseek", self._ds()))

    def test_empty_provider_with_namespaced_model_counts(self):
        self.assertTrue(usage.is_spend_row(
            {"provider": "", "model": "deepseek/deepseek-v3-flash"},
            "deepseek", self._ds()))

    def test_missing_provider_with_namespaced_model_counts(self):
        self.assertTrue(usage.is_spend_row(
            {"model": "deepseek/deepseek-v3-flash"},
            "deepseek", self._ds()))

    def test_provider_match_is_case_insensitive(self):
        reg = {"providers": {"vertex_ai": {"id": "vertex_ai",
                                           "omniroute_id": "vertex"}}}
        self.assertTrue(usage.is_spend_row(
            {"provider": "VERTEX", "model": "vertex/gemini-3.8-flash"},
            "vertex_ai", reg))

    def test_namespace_match_is_exact_not_substring(self):
        reg = {"providers": {"vertex_ai": {"id": "vertex_ai",
                                           "omniroute_id": "vertex"}}}
        self.assertFalse(usage.is_spend_row(
            {"provider": "vertex_ai2", "model": "other/x"},
            "vertex_ai", reg))
        self.assertFalse(usage.is_spend_row(
            {"provider": "", "model": "vertex2/x"}, "vertex_ai", reg))
        self.assertFalse(usage.is_spend_row(
            {"provider": "vertexish", "model": "vertexish/x"},
            "vertex_ai", reg))


class C3UnreadableRowsUnknown(unittest.TestCase):
    def _bad_row(self, value, rid):
        return {"id": rid, "provider": "vertex_ai",
                "model": "vertex/gemini-3.8-flash",
                "tokens": {"in": value, "out": 0},
                "timestamp": "2026-10-05T10:00:00Z", "status": 200}

    def test_each_unreadable_kind_is_unknown(self):
        for label, value in [("inf", float("inf")),
                             ("neg-inf", float("-inf")),
                             ("nan", float("nan")),
                             ("1e30", 1e30),
                             ("10**400", 10 ** 400),
                             ("negative", -50)]:
            with self.subTest(kind=label):
                guards = usage.credit_guards(
                    _reg(), [self._bad_row(value, "bad-%s" % label)],
                    SINCE_MONTH, today=NOW.date())
                guard = guards["vertex_ai"]
                self.assertEqual(guard["state"], "unknown",
                                 "%s must read unknown, got %r"
                                 % (label, guard))
                self.assertTrue(guard["spend_unknown"])
                self.assertIn("1 unreadable", guard["note"])

    def test_negative_counts_never_subtract(self):
        spend = usage.paid_spend(
            [self._bad_row(-50, "bad-neg")], _prices(_reg()), _reg(),
            SINCE_MONTH, provider="vertex_ai")
        self.assertEqual(spend["rows_unreadable"], 1)
        self.assertEqual(spend["spend_usd"], 0.0)

    def test_measured_refuse_beats_unknown(self):
        # $120 + $180 = $300 of readable spend: over the $230 hard stop even
        # with an unreadable row beside it.
        big = {"in": 80_000_000, "out": 24_000_000}
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash",
                     tokens=dict(big)),
                self._bad_row(float("inf"), "bad-inf")]
        guards = usage.credit_guards(_reg(), rows, SINCE_MONTH,
                                     today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["state"], "refuse")
        self.assertEqual(guards["vertex_ai"]["spend_usd"], 300.0)


class C4UnknownWarnOnlyForSurvivors(unittest.TestCase):
    def _shipped_vertex(self):
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        for rid, route in (reg.get("routes") or {}).items():
            legs = route.get("legs") or []
            if any(leg.split("/")[0] in ("vertex", "vertex_ai")
                   for leg in legs):
                return reg, rid, route
        self.fail("shipped registry must carry a vertex leg")

    def _guards(self, reg):
        return usage.credit_guards(reg, None, None, failure="test: no ledger",
                                   today=NOW.date())

    def test_implement_skipped_leg_names_no_kept_line(self):
        reg, rid, route = self._shipped_vertex()
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "implement", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=self._guards(reg), credit_warns=warns)
        vertex_skipped = [leg for leg in skipped
                          if str(leg).split("/")[0] in ("vertex", "vertex_ai")]
        self.assertTrue(vertex_skipped,
                        "precondition: implement must skip a vertex leg, "
                        "skipped=%r kept=%r" % (skipped, kept))
        self.assertTrue(
            any("tool_calls" in reason
                for leg in vertex_skipped for reason in skipped[leg]),
            "precondition: vertex leg skipped for tool_calls, got %r"
            % (skipped,))
        self.assertFalse(any("leg kept" in w and "vertex" in w for w in warns),
                         "skipped leg must not say 'kept': %r" % (warns,))

    def test_review_kept_leg_names_kept_line(self):
        reg, rid, route = self._shipped_vertex()
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "review", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=self._guards(reg), credit_warns=warns)
        kept_ids = [k if isinstance(k, str) else k[0] for k in kept]
        vertex_kept = [str(x) for x in kept_ids
                       if str(x).split("/")[0] in ("vertex", "vertex_ai")]
        self.assertTrue(vertex_kept,
                        "precondition: review must keep a vertex leg, "
                        "kept=%r skipped=%r" % (kept, skipped))
        self.assertTrue(any("leg kept" in w and "vertex" in w for w in warns),
                        "kept unknown-spend leg must name it: %r" % (warns,))


class C5MutantKillers(unittest.TestCase):
    def _future_reg(self):
        return {"providers": {
            "p": {"id": "p", "tier": "credit", "credit_usd": 10.0,
                  "monthly_cap_usd": 10.0, "monthly_warn_fraction": 0.8,
                  "credit_started": "2026-10-02"}}}

    def test_future_credit_started_rejected_with_injected_today(self):
        problems = regmod.check_registry(
            self._future_reg(), today=datetime.date(2026, 10, 1))
        self.assertTrue(any("credit_started" in p and "future" in p
                            for p in problems),
                        "future credit_started must be rejected, got %r"
                        % (problems,))

    def test_past_credit_started_accepted_with_injected_today(self):
        problems = regmod.check_registry(
            self._future_reg(), today=datetime.date(2026, 10, 3))
        self.assertFalse(any("credit_started" in p and "future" in p
                             for p in problems),
                         "past credit_started must pass, got %r" % (problems,))


# --- T1-CREDIT-FIX-9 -----------------------------------------------------
# T1: a fetch cut at the page cap must not read as measured ok. T2: a
# window-limited ok/warn grant names its month-to-date limit on surviving
# legs. T3: unparseable/list/non-dict tokens are unreadable. T4: the paid
# block names unreadable rows. T5: paid mutants. T7: every kept-claiming
# line is survivor-only. T8: malformed credit_started stays loud.

def _t9_ds_reg():
    reg = _reg()
    reg["models"]["deepseek-chat"] = {
        "id": "deepseek-chat", "price_in": 1e-06, "price_out": 2e-06,
        "tool_calls": "unproven",
        "context_usable": {"tokens": 100000, "source": "default"}}
    reg["models"]["ds-proven"] = {
        "id": "ds-proven", "price_in": 1e-06, "price_out": 2e-06,
        "tool_calls": "proven",
        "context_usable": {"tokens": 100000, "source": "default"}}
    return reg


def _ds_row(rid, tokens, ts="2026-10-05T10:00:00Z", provider="deepseek",
            model="deepseek-chat"):
    return {"id": rid, "provider": provider, "model": model,
            "tokens": tokens, "timestamp": ts, "status": 200}


def _tiny_vertex_row(rid, ts="2026-10-05T10:00:00Z"):
    return {"id": rid, "provider": "vertex",
            "model": "vertex/gemini-3.8-flash",
            "tokens": {"in": 100, "out": 0},
            "timestamp": ts, "status": 200}


class T1TruncatedFetchUnknown(unittest.TestCase):
    def test_credit_truncated_is_unknown(self):
        guards = usage.credit_guards(
            _reg(), [_tiny_vertex_row("t1")], SINCE_MONTH,
            today=NOW.date(), complete=False)
        guard = guards["vertex_ai"]
        self.assertEqual(guard["state"], "unknown",
                         "truncated fetch must read unknown, got %r" % (guard,))
        self.assertTrue(guard["spend_unknown"])
        self.assertIn("fetch truncated at page cap", guard["note"])

    def test_credit_truncated_refuse_beats_unknown(self):
        big = {"in": 80_000_000, "out": 24_000_000}
        rows = [_row("vertex_ai", "vertex/gemini-3.8-flash", tokens=dict(big))]
        guards = usage.credit_guards(
            _reg(), rows, SINCE_MONTH, today=NOW.date(), complete=False)
        self.assertEqual(guards["vertex_ai"]["state"], "refuse",
                         "measured spend over stop still refuses: %r"
                         % (guards["vertex_ai"],))

    def test_plan_truncated_credit_is_unknown(self):
        agent = _agent_mod()
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)
        tiny = [_tiny_vertex_row("t%d" % i) for i in range(3)]

        def fetch(url, headers, timeout):
            return 200, json.dumps(tiny).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            env = _keyed_env(tmp)
            with unittest.mock.patch.object(
                    usage, "fetch_window",
                    return_value=(tiny, 20, True)):
                guards = agent.plan_credit_guards(
                    _reg(), now=NOW, fetch=fetch, env=env)
        guard = guards["vertex_ai"]
        self.assertEqual(guard["state"], "unknown",
                         "plan must thread truncated -> unknown, got %r"
                         % (guard,))
        self.assertIn("fetch truncated at page cap", guard["note"])

    def test_plan_truncated_paid_is_unknown_kept(self):
        agent = _agent_mod()
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)
        reg = _t9_ds_reg()
        tiny = [_ds_row("p1", {"in": 100, "out": 100})]

        def fetch(url, headers, timeout):
            return 200, json.dumps(tiny).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            env = _keyed_env(tmp)
            with unittest.mock.patch.object(
                    usage, "fetch_window",
                    return_value=(tiny, 20, True)):
                guards = agent.plan_credit_guards(
                    reg, now=NOW, fetch=fetch, env=env)
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "unknown",
                         "truncated paid fetch must read unknown, got %r"
                         % (guard,))
        self.assertIn("fetch truncated at page cap", guard["note"])

    def test_cli_truncated_credit_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = os.path.join(tmp, "registry.json")
            with open(reg_path, "w", encoding="utf-8") as fh:
                json.dump(_reg(), fh)
            env = _keyed_env(tmp)
            tiny = [_tiny_vertex_row("c%d" % i) for i in range(3)]

            def fetch(url, headers, timeout):
                return 200, json.dumps(tiny).encode("utf-8")

            buf = io.StringIO()
            with unittest.mock.patch.object(
                    usage, "fetch_window",
                    return_value=(tiny, 20, True)):
                with contextlib.redirect_stdout(buf):
                    rc = usage.main(
                        ["--since", "2026-10-01T00:00:00Z", "--json",
                         "--cost", "--registry", reg_path,
                         "--spend-since", "2026-10-01T00:00:00Z"],
                        fetch=fetch, env=env, now=NOW)
        self.assertEqual(rc, 0)
        guard = json.loads(buf.getvalue())["credit_guards"]["vertex_ai"]
        self.assertEqual(guard["state"], "unknown",
                         "CLI must thread truncated -> unknown, got %r"
                         % (guard,))
        self.assertIn("fetch truncated at page cap", guard["note"])


class T2WindowLimitedCaveat(unittest.TestCase):
    def _shipped_vertex(self):
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        for rid, route in (reg.get("routes") or {}).items():
            legs = route.get("legs") or []
            if any(leg.split("/")[0] in ("vertex", "vertex_ai")
                   for leg in legs):
                return reg, rid, route
        self.fail("shipped registry must carry a vertex leg")

    def test_shipped_registry_has_no_credit_started(self):
        with open(_ROOT / "catalog" / "ai-registry.json",
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        for pid, entry in (reg.get("providers") or {}).items():
            if isinstance(entry, dict) and entry.get("tier") == "credit":
                self.assertIsNone(entry.get("credit_started"),
                                  "%s must carry no credit_started" % pid)

    def test_ok_survivor_names_month_to_date(self):
        reg, rid, route = self._shipped_vertex()
        guards = usage.credit_guards(reg, [], SINCE_MONTH,
                                     today=NOW.date())
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "review", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        kept_ids = [k if isinstance(k, str) else k[0] for k in kept]
        self.assertTrue(
            any(str(x).split("/")[0] in ("vertex", "vertex_ai")
                for x in kept_ids),
            "precondition: review must keep a vertex leg: %r" % (kept,))
        self.assertTrue(
            any("credit spend measured month-to-date only" in w
                and "vertex" in w for w in warns),
            "ok window-limited survivor must name the limit: %r" % (warns,))

    def test_ok_skipped_leg_names_no_caveat(self):
        reg, rid, route = self._shipped_vertex()
        guards = usage.credit_guards(reg, [], SINCE_MONTH,
                                     today=NOW.date())
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": "implement", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        vertex_skipped = [leg for leg in skipped
                          if str(leg).split("/")[0] in ("vertex", "vertex_ai")]
        self.assertTrue(vertex_skipped,
                        "precondition: implement must skip a vertex leg")
        self.assertFalse(
            any("month-to-date only" in w and "vertex" in w for w in warns),
            "skipped leg must not claim a caveat: %r" % (warns,))

    def test_report_ok_names_month_to_date(self):
        guards = usage.credit_guards(_reg(), [], SINCE_MONTH,
                                     today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["state"], "ok")
        lines = usage.render_credit_guards(guards)
        self.assertTrue(
            any("vertex_ai" in line and "month-to-date only" in line
                for line in lines),
            "report ok window-limited grant must name the limit: %r"
            % (lines,))


class T3TokenFieldReadable(unittest.TestCase):
    def test_unparseable_strings_are_unreadable(self):
        for value in ("1e30", "inf", "nan", "abc"):
            with self.subTest(value=value):
                self.assertFalse(usage._token_field_readable(value),
                                 "%r must be unreadable" % (value,))

    def test_numeric_string_stays_readable(self):
        self.assertTrue(usage._token_field_readable("123"))

    def test_list_is_unreadable(self):
        self.assertFalse(usage._token_field_readable([1]))

    def test_non_dict_tokens_is_unreadable(self):
        tin, tout, readable = usage._readable_tokens([1, 2])
        self.assertFalse(readable)
        tin, tout, readable = usage._readable_tokens("abc")
        self.assertFalse(readable)

    def test_string_tokens_make_guard_unknown(self):
        row = {"id": "bad-str", "provider": "vertex_ai",
               "model": "vertex/gemini-3.8-flash",
               "tokens": {"in": "inf", "out": 0},
               "timestamp": "2026-10-05T10:00:00Z", "status": 200}
        guards = usage.credit_guards(_reg(), [row], SINCE_MONTH,
                                     today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["state"], "unknown")
        self.assertIn("1 unreadable", guards["vertex_ai"]["note"])


class T4PaidUnreadableLine(unittest.TestCase):
    def test_unreadable_rows_named(self):
        spend = {"since": "2026-10-01T00:00:00Z", "calls": 1,
                 "tokens_in": 1, "tokens_out": 1, "spend_usd": 0.0,
                 "threshold_usd": 20.0, "balance_usd": None,
                 "balance_threshold_usd": 5.0, "models_unpriced": 0,
                 "rows_unreadable": 2, "price_source": "t",
                 "complete": True, "warnings": []}
        lines = usage.render_spend_text(spend)
        self.assertTrue(any("2 unreadable row(s) skipped" in line
                            for line in lines),
                        "paid block must name unreadable rows: %r" % (lines,))

    def test_zero_unreadable_prints_no_line(self):
        spend = {"since": "2026-10-01T00:00:00Z", "calls": 1,
                 "tokens_in": 1, "tokens_out": 1, "spend_usd": 0.0,
                 "threshold_usd": 20.0, "balance_usd": None,
                 "balance_threshold_usd": 5.0, "models_unpriced": 0,
                 "rows_unreadable": 0, "price_source": "t",
                 "complete": True, "warnings": []}
        lines = usage.render_spend_text(spend)
        self.assertFalse(any("unreadable" in line for line in lines))


class T5PaidMutants(unittest.TestCase):
    def test_paid_unreadable_is_unknown_kept(self):
        agent = _agent_mod()
        agent.CREDIT_GUARD_CACHE.clear()
        self.addCleanup(agent.CREDIT_GUARD_CACHE.clear)
        reg = _t9_ds_reg()
        rows = [_ds_row("inf-ds", {"in": float("inf"), "out": 0})]

        def fetch(url, headers, timeout):
            return 200, json.dumps(rows).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            env = _keyed_env(tmp)
            guards = agent.plan_credit_guards(
                reg, now=NOW, fetch=fetch, env=env)
        guard = guards["deepseek"]
        self.assertEqual(guard["state"], "unknown",
                         "inf-token paid row must read unknown: %r" % (guard,))
        self.assertTrue(guard["spend_unknown"])
        self.assertIn("1 unreadable", guard["note"])

    def test_paid_stays_month_to_date(self):
        reg = _t9_ds_reg()
        reg["providers"]["vertex_ai"]["credit_started"] = "2026-09-01"
        sept = _ds_row("sept-ds", {"in": 100_000_000, "out": 100_000_000},
                       ts="2026-09-15T10:00:00Z")
        octo = _ds_row("oct-ds", {"in": 100, "out": 100},
                       ts="2026-10-05T10:00:00Z")
        spend = usage.paid_spend([sept, octo], usage.prices_from_registry(reg),
                                 reg, SINCE_MONTH, provider="deepseek")
        self.assertLess(spend["spend_usd"], 1.0,
                        "September $300 must not bill October: %r" % (spend,))
        vsept = {"id": "v-sept", "provider": "vertex",
                 "model": "vertex/gemini-3.8-flash",
                 "tokens": {"in": 80_000_000, "out": 24_000_000},
                 "timestamp": "2026-09-15T10:00:00Z", "status": 200}
        guards = usage.credit_guards(
            reg, [vsept, sept, octo], SINCE_MONTH, today=NOW.date())
        self.assertEqual(guards["vertex_ai"]["state"], "refuse",
                         "vertex must still see September: %r"
                         % (guards["vertex_ai"],))

    def test_future_credit_started_reads_absent(self):
        reg = {"providers": {
            "p": {"id": "p", "tier": "credit", "credit_usd": 10.0,
                  "monthly_cap_usd": 10.0, "monthly_warn_fraction": 0.8,
                  "credit_started": "2026-10-02"}}}
        self.assertIsNone(usage.credit_started_date(
            reg, "p", today=datetime.date(2026, 10, 1)))
        self.assertTrue(usage.credit_window_limited(
            reg, "p", today=datetime.date(2026, 10, 1)))


class T7KeptLinesOnlyForSurvivors(unittest.TestCase):
    def _synth(self, state, note=None):
        reg = _t9_ds_reg()
        reg["providers"]["vertex_ai"]["monthly_cap_usd"] = 250.0
        reg["models"]["v-proven"] = {
            "id": "v-proven", "tool_calls": "unproven",
            "context_usable": {"tokens": 100000, "source": "default"},
            "provider_prices": {
                "vertex_ai": {"price_in": 1.5e-06, "price_out": 7.5e-06,
                              "price_source": "https://example.invalid",
                              "price_as_of": "2026-10-01"}}}
        route = {"legs": ["vertex_ai/v-proven", "deepseek/ds-proven"]}
        guards = {
            "vertex_ai": {"provider": "vertex_ai", "state": state,
                          "spend_usd": 0.0, "spend_unknown": True,
                          "cap_usd": 250.0, "warn_usd": 200.0,
                          "models_unpriced": 0,
                          "window_limited": False,
                          "note": note or "test note"},
            "deepseek": {"provider": "deepseek", "state": "unknown",
                         "spend_usd": 0.0, "spend_unknown": True,
                         "cap_usd": 25.0, "warn_usd": 20.0,
                         "models_unpriced": 0,
                         "note": "paid spend unmeasured deepseek - leg kept "
                                 "(last resort, D-212)"},
        }
        return reg, route, guards

    def _run(self, reg, route, guards, kind):
        warns = []
        kept, skipped, _notes = r.usable_legs(
            route, {"kind": kind, "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        return kept, skipped, warns

    def test_no_guard_line_survivor_only(self):
        reg = _t9_ds_reg()
        reg["models"]["v-unproven"] = {
            "id": "v-unproven", "tool_calls": "unproven",
            "context_usable": {"tokens": 100000, "source": "default"},
            "provider_prices": {
                "vertex_ai": {"price_in": 1.5e-06, "price_out": 7.5e-06,
                              "price_source": "https://example.invalid",
                              "price_as_of": "2026-10-01"}}}
        route = {"legs": ["vertex_ai/v-unproven"]}
        _k, skipped, warns = self._run(reg, route, {}, "implement")
        self.assertTrue(any("tool_calls" in reason
                            for leg in skipped for reason in skipped[leg]))
        self.assertFalse(any("leg kept" in w for w in warns),
                         "skipped no-guard leg must not say kept: %r" % (warns,))
        _k2, _s2, warns2 = self._run(reg, route, {}, "review")
        self.assertTrue(any("leg kept" in w and "vertex_ai" in w
                            for w in warns2),
                        "surviving no-guard leg must name it: %r" % (warns2,))

    def test_unrecognised_line_survivor_only(self):
        reg, route, guards = self._synth("bogus", "bogus note")
        guards["vertex_ai"]["spend_unknown"] = False
        _k, skipped, warns = self._run(reg, route, guards, "implement")
        self.assertTrue(skipped)
        self.assertFalse(any("leg kept" in w and "vertex" in w.lower()
                             for w in warns),
                         "skipped bogus-state leg must not say kept: %r"
                         % (warns,))
        _k2, _s2, warns2 = self._run(reg, route, guards, "review")
        self.assertTrue(any("leg kept" in w for w in warns2),
                        "surviving bogus-state leg must name it: %r" % (warns2,))

    def test_paid_kept_line_survivor_only(self):
        reg = _t9_ds_reg()
        reg["models"]["ds-unproven"] = {
            "id": "ds-unproven", "price_in": 1e-06, "price_out": 2e-06,
            "tool_calls": "unproven",
            "context_usable": {"tokens": 100000, "source": "default"}}
        route = {"legs": ["deepseek/ds-unproven"]}
        guards = {"deepseek": {
            "provider": "deepseek", "state": "unknown", "spend_usd": 0.0,
            "spend_unknown": True, "cap_usd": 25.0, "warn_usd": 20.0,
            "models_unpriced": 0,
            "note": "paid spend unmeasured deepseek - leg kept "
                    "(last resort, D-212)"}}
        _k, skipped, warns = self._run(reg, route, guards, "implement")
        self.assertTrue(skipped)
        self.assertFalse(any("leg kept" in w for w in warns),
                         "skipped paid leg must not say kept: %r" % (warns,))
        _k2, _s2, warns2 = self._run(reg, route, guards, "review")
        self.assertTrue(any("leg kept" in w and "deepseek" in w
                            for w in warns2),
                        "surviving paid leg must name it: %r" % (warns2,))


class T8FetchCutoffMalformed(unittest.TestCase):
    def test_malformed_started_stays_month_start_and_loud(self):
        for bad in ("not-a-date", "2026-13-45", 12345, ""):
            with self.subTest(bad=bad):
                reg = _reg()
                reg["providers"]["vertex_ai"]["credit_started"] = bad
                self.assertEqual(usage.fetch_cutoff(reg, NOW), SINCE_MONTH)
                self.assertTrue(usage.credit_window_limited(
                    reg, "vertex_ai", today=NOW.date()))
                guards = usage.credit_guards(reg, [], SINCE_MONTH,
                                             today=NOW.date())
                self.assertIn("month-to-date",
                              guards["vertex_ai"]["note"])


if __name__ == "__main__":
    unittest.main()
