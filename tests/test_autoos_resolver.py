#!/usr/bin/env python3
"""Golden tests for tools/autoos_resolver.py (routing v2 spec, sections 5.2 and 5.5).

Every boundary of the bucket table is pinned here, plus every effort row, the
ladder clamp and the max_tokens floors. The module is pure: it imports from any
cwd once `tools/` is on sys.path, which is the first thing this file does.
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import autoos_resolver as r  # noqa: E402

# A full canonical ladder, so a rung the rule wants is always present unless a
# test deliberately shortens the ladder.
FULL_LADDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def features(files=1, modules=1, fanout=4, lines=29, tests=True):
    """Minimum-point features: every numeric value is at its lowest band."""
    return {"files": files, "modules": modules, "fanout": fanout,
            "lines": lines, "tests": tests}


def card(spec="exact", kind="implement"):
    return {"spec": spec, "kind": kind}


class TableTests(unittest.TestCase):
    def test_table_holds_the_section_5_2_points(self):
        t = r.DEFAULT_BUCKET_TABLE
        self.assertEqual(
            [row["points"] for row in t["features"]["files"]], [0, 1, 2, 3, 4])
        self.assertEqual(
            [row["points"] for row in t["features"]["modules"]], [0, 1, 2])
        self.assertEqual(
            [row["points"] for row in t["features"]["fanout"]], [0, 1, 2])
        self.assertEqual(
            [row["points"] for row in t["features"]["lines"]], [0, 1, 2, 3])
        self.assertEqual(t["spec"], {"exact": 0, "partial": 1, "vague": 2})
        self.assertEqual(t["tests"], {True: 0, False: 1})
        self.assertEqual(t["kind"], {"implement": 0, "bulk": 0, "review": 0,
                                     "research": 0, "debug": 2, "plan": 3})
        self.assertEqual(
            [(b["name"], b["max"]) for b in t["buckets"]],
            [("S0", 1), ("S1", 3), ("S2", 6), ("S3", 9), ("S4", None)])


class PointsTests(unittest.TestCase):
    def pts(self, files=1, modules=1, fanout=4, lines=29, tests=True,
            spec="exact", kind="implement"):
        return r.points(features(files, modules, fanout, lines, tests),
                        card(spec, kind))

    def test_files_both_sides_of_every_cut(self):
        for value, expected in ((1, 0), (2, 1), (3, 2), (5, 2), (6, 3),
                                (10, 3), (11, 4)):
            with self.subTest(files=value):
                self.assertEqual(self.pts(files=value)["files"], expected)

    def test_modules_both_sides_of_every_cut(self):
        for value, expected in ((1, 0), (2, 1), (3, 2)):
            with self.subTest(modules=value):
                self.assertEqual(self.pts(modules=value)["modules"], expected)

    def test_fanout_both_sides_of_every_cut(self):
        for value, expected in ((4, 0), (5, 1), (20, 1), (21, 2)):
            with self.subTest(fanout=value):
                self.assertEqual(self.pts(fanout=value)["fanout"], expected)

    def test_lines_both_sides_of_every_cut(self):
        for value, expected in ((29, 0), (30, 1), (150, 1), (151, 2),
                                (500, 2), (501, 3)):
            with self.subTest(lines=value):
                self.assertEqual(self.pts(lines=value)["lines"], expected)

    def test_spec_all_values(self):
        for value, expected in (("exact", 0), ("partial", 1), ("vague", 2)):
            with self.subTest(spec=value):
                self.assertEqual(self.pts(spec=value)["spec"], expected)

    def test_tests_both_values(self):
        self.assertEqual(self.pts(tests=True)["tests"], 0)
        self.assertEqual(self.pts(tests=False)["tests"], 1)

    def test_kind_all_values(self):
        for value, expected in (("implement", 0), ("bulk", 0), ("review", 0),
                                ("research", 0), ("debug", 2), ("plan", 3)):
            with self.subTest(kind=value):
                self.assertEqual(self.pts(kind=value)["kind"], expected)

    def test_full_points_dict(self):
        self.assertEqual(
            self.pts(files=6, modules=3, fanout=21, lines=501,
                     spec="vague", tests=False, kind="debug"),
            {"files": 3, "modules": 2, "fanout": 2, "lines": 3,
             "spec": 2, "tests": 1, "kind": 2})

    def test_missing_features_raise_naming_the_key(self):
        for key in ("files", "modules", "fanout", "lines", "tests"):
            with self.subTest(key=key):
                f = features()
                del f[key]
                with self.assertRaises(ValueError) as cm:
                    r.points(f, card())
                self.assertIn(key, str(cm.exception))

    def test_missing_card_fields_raise_naming_the_key(self):
        for key in ("spec", "kind"):
            with self.subTest(key=key):
                c = card()
                del c[key]
                with self.assertRaises(ValueError) as cm:
                    r.points(features(), c)
                self.assertIn(key, str(cm.exception))

    def test_unknown_card_value_fails_closed(self):
        with self.assertRaises(ValueError):
            r.points(features(), card(kind="frobnicate"))
        with self.assertRaises(ValueError):
            r.points(features(), card(spec="maybe"))

    def test_the_function_uses_the_table_it_is_given(self):
        table = {
            "features": {
                "files": ({"max": 1, "points": 9}, {"max": None, "points": 9}),
                "modules": ({"max": None, "points": 0},),
                "fanout": ({"max": None, "points": 0},),
                "lines": ({"max": None, "points": 0},),
            },
            "spec": {"exact": 0, "partial": 0, "vague": 0},
            "tests": {True: 0, False: 0},
            "kind": {"implement": 0},
            "buckets": ({"name": "SX", "max": None},),
        }
        name, total = r.bucket(features(files=99), card(), table)
        self.assertEqual((name, total), ("SX", 9))


class BucketTests(unittest.TestCase):
    def cases(self):
        # Every threshold total named in the brief, reached with a real feature
        # set so the test exercises bucket(), not a private helper.
        return [
            (dict(tests=False), "S0", 1),
            (dict(kind="debug"), "S1", 2),
            (dict(kind="plan"), "S1", 3),
            (dict(files=2, kind="plan"), "S2", 4),
            (dict(files=2, kind="plan", spec="partial", tests=False), "S2", 6),
            (dict(files=3, kind="plan", spec="partial", tests=False), "S3", 7),
            (dict(files=6, modules=3, kind="plan", tests=False), "S3", 9),
            (dict(files=11, modules=3, kind="plan", tests=False), "S4", 10),
            (dict(files=11, modules=3, fanout=21, lines=501, spec="vague",
                  tests=False, kind="plan"), "S4", 17),
        ]

    def test_every_threshold_total(self):
        for kwargs, name, total in self.cases():
            with self.subTest(total=total):
                kw = dict(files=1, modules=1, fanout=4, lines=29, tests=True,
                          spec="exact", kind="implement")
                kw.update(kwargs)
                got = r.bucket(features(kw["files"], kw["modules"], kw["fanout"],
                                        kw["lines"], kw["tests"]),
                               card(kw["spec"], kw["kind"]))
                self.assertEqual(got, (name, total))

    def test_zero_is_s0(self):
        self.assertEqual(r.bucket(features(), card()), ("S0", 0))


class EffortTests(unittest.TestCase):
    def test_not_reasoning_is_none(self):
        self.assertIsNone(r.effort("S4", "plan", "frontier", FULL_LADDER, False))
        self.assertIsNone(r.effort("S0", "implement", "free", FULL_LADDER, False))

    def test_plan_is_high(self):
        self.assertEqual(r.effort("S0", "plan", "free", FULL_LADDER, True), "high")
        self.assertEqual(r.effort("S2", "plan", "mid", FULL_LADDER, True), "high")

    def test_plan_row_precedes_the_frontier_row(self):
        self.assertEqual(
            r.effort("S4", "plan", "frontier", FULL_LADDER, True), "high")

    def test_debug_is_high(self):
        self.assertEqual(r.effort("S0", "debug", "free", FULL_LADDER, True), "high")

    def test_s4_frontier_takes_the_top_rung(self):
        self.assertEqual(
            r.effort("S4", "implement", "frontier", FULL_LADDER, True), "max")

    def test_s4_frontier_top_rung_of_a_short_ladder(self):
        self.assertEqual(
            r.effort("S4", "implement", "frontier", ["none", "high"], True),
            "high")

    def test_s3_and_s4_are_high(self):
        self.assertEqual(
            r.effort("S3", "implement", "free", FULL_LADDER, True), "high")
        self.assertEqual(
            r.effort("S4", "implement", "mid", FULL_LADDER, True), "high")

    def test_s1_and_s2_are_medium(self):
        self.assertEqual(
            r.effort("S1", "implement", "free", FULL_LADDER, True), "medium")
        self.assertEqual(
            r.effort("S2", "review", "cheap", FULL_LADDER, True), "medium")

    def test_s0_is_low(self):
        self.assertEqual(
            r.effort("S0", "implement", "free", FULL_LADDER, True), "low")

    def test_bulk_follows_the_bucket_rows_before_the_low_row(self):
        # bulk is low only where the S1/S2/S3/S4 rows have not already matched.
        self.assertEqual(
            r.effort("S1", "bulk", "free", FULL_LADDER, True), "medium")
        self.assertEqual(
            r.effort("S2", "bulk", "free", FULL_LADDER, True), "medium")
        self.assertEqual(
            r.effort("S3", "bulk", "free", FULL_LADDER, True), "high")
        self.assertEqual(
            r.effort("S0", "bulk", "free", FULL_LADDER, True), "low")

    def test_no_bucket_or_kind_row_matching_is_none(self):
        self.assertIsNone(r.effort("S9", "implement", "free", FULL_LADDER, True))

    def test_clamp_down(self):
        self.assertEqual(
            r.effort("S3", "implement", "free",
                     ["none", "minimal", "low", "medium"], True),
            "medium")

    def test_clamp_up(self):
        self.assertEqual(
            r.effort("S0", "implement", "free", ["medium", "high", "max"], True),
            "medium")

    def test_clamp_tie_prefers_the_lower_rung(self):
        # wanted high (index 4); low is 2 away and max is 2 away.
        self.assertEqual(
            r.effort("S3", "implement", "free", ["low", "max"], True), "low")

    def test_rung_already_present_is_not_clamped(self):
        self.assertEqual(
            r.effort("S1", "implement", "free", ["medium"], True), "medium")

    def test_empty_ladder_is_none(self):
        self.assertIsNone(r.effort("S3", "implement", "free", [], True))
        self.assertIsNone(r.effort("S0", "plan", "free", [], True))


class MaxTokensTests(unittest.TestCase):
    def test_non_reasoning_is_none(self):
        for e in ("none", "low", "medium", "high", "max"):
            with self.subTest(effort=e):
                self.assertIsNone(r.max_tokens(e, False, 200000))

    def test_floor_is_48000_below_high(self):
        for e in ("none", "minimal", "low", "medium"):
            with self.subTest(effort=e):
                self.assertEqual(r.max_tokens(e, True, 200000), 48000)

    def test_floor_is_64000_at_high_and_above(self):
        for e in ("high", "xhigh", "max"):
            with self.subTest(effort=e):
                self.assertEqual(r.max_tokens(e, True, 200000), 64000)

    def test_capped_by_output_max(self):
        self.assertEqual(r.max_tokens("high", True, 50000), 50000)
        self.assertEqual(r.max_tokens("medium", True, 20000), 20000)
        self.assertEqual(r.max_tokens("high", True, 64000), 64000)
        self.assertEqual(r.max_tokens("medium", True, 48000), 48000)



class InvalidFeatureTests(unittest.TestCase):
    def test_negative_or_non_integer_features_fail_closed(self):
        base = {"files": 1, "modules": 1, "fanout": 0, "lines": 10, "tests": True}
        card = {"spec": "exact", "kind": "implement"}
        for key, bad in (("files", -1), ("lines", "10"), ("fanout", 1.5), ("modules", True)):
            with self.assertRaises(ValueError, msg=key):
                r.points(dict(base, **{key: bad}), card)


class FilterTests(unittest.TestCase):
    """Hard filters, override-after-filters and no_route (spec 5.3 step 1, 4).

    A small inline registry keeps every reason exact and independent of the
    real catalog; one smoke test then runs the same functions over the real
    catalog/ai-registry.json.
    """

    def setUp(self):
        self.registry = {
            "providers": {
                "clean": {"id": "clean", "trains_on_prompts": False},
                "nosy": {"id": "nosy", "trains_on_prompts": True},
            },
            "models": {
                "big": {"id": "big", "tool_calls": "proven",
                        "context_usable": {"tokens": 100000, "source": "default"}},
                "tiny": {"id": "tiny", "tool_calls": "proven",
                         "context_usable": {"tokens": 100, "source": "default"}},
                "bound": {"id": "bound", "tool_calls": "unproven",
                          "client_bound": "claude",
                          "context_usable": {"tokens": 100000, "source": "default"}},
            },
            "routes": {
                # clean/tiny is listed but unavailable, so only clean/big serves
                # and the route survives: an unavailable leg is really excluded.
                "r-ok": {"id": "r-ok", "legs": ["clean/big", "clean/tiny"],
                         "unavailable_legs": {"clean/tiny": {"available": False}}},
                "r-retired": {"id": "r-retired", "retired": True,
                              "legs": ["clean/big"]},
                "r-noleg": {"id": "r-noleg", "legs": []},
                # Serving three legs that fail privacy, context, tool_calls and
                # client_bound respectively.
                "r-mixed": {"id": "r-mixed",
                            "legs": ["nosy/big", "clean/tiny", "clean/bound"]},
            },
        }
        self.overlay = {"models": {"tiny": {
            "context_usable": {"tokens": 5000, "source": "probe"}}}}

    def card(self, kind="review", privacy="public", **extra):
        card = {"kind": kind, "privacy": privacy}
        card.update(extra)
        return card

    def feats(self, need_tokens=1000):
        return {"need_tokens": need_tokens}

    def state(self, installed=True, signed_in=True):
        return {"opencode": {"installed": installed, "signed_in": signed_in,
                             "reason": ""}}

    def run_filters(self, card=None, features=None, client_state=None,
                    overlay=None):
        return r.filter_routes(card or self.card(),
                               features or self.feats(),
                               client_state or self.state(),
                               self.registry,
                               {} if overlay is None else overlay)

    def test_agentic_kinds_are_the_three_write_kinds(self):
        self.assertEqual(r.AGENTIC_KINDS, ("implement", "debug", "bulk"))

    # --- serving_legs -----------------------------------------------------

    def test_serving_legs_skips_an_unavailable_leg(self):
        self.assertEqual(
            r.serving_legs(self.registry["routes"]["r-ok"], self.registry),
            [("clean", "big")])

    def test_serving_legs_resolves_a_provider_omniroute_id(self):
        self.registry["providers"]["clean"]["omniroute_id"] = "opencode-clean"
        route = {"id": "r-alias", "legs": ["opencode-clean/big"]}
        self.assertEqual(r.serving_legs(route, self.registry), [("clean", "big")])

    def test_serving_legs_unknown_leg_fails_closed(self):
        with self.assertRaises(ValueError) as cm:
            r.serving_legs({"id": "broken", "legs": ["clean/ghost"]}, self.registry)
        self.assertIn("clean/ghost", str(cm.exception))

    def test_serving_legs_malformed_leg_fails_closed(self):
        with self.assertRaises(ValueError) as cm:
            r.serving_legs({"id": "broken", "legs": ["nodivider"]}, self.registry)
        self.assertIn("nodivider", str(cm.exception))

    # --- usable_context ---------------------------------------------------

    def test_usable_context_falls_back_to_the_registry(self):
        self.assertEqual(r.usable_context("tiny", self.registry, {}), 100)
        self.assertEqual(r.usable_context("big", self.registry, {}), 100000)

    def test_usable_context_overlay_wins(self):
        self.assertEqual(r.usable_context("tiny", self.registry, self.overlay),
                         5000)
        self.assertEqual(r.usable_context("big", self.registry, self.overlay),
                         100000)

    # --- one test per filter reason ---------------------------------------

    def test_survivors_are_route_ids_in_registry_order(self):
        survivors, removed = self.run_filters(overlay={})
        self.assertEqual(survivors, ["r-ok"])
        self.assertEqual(sorted(removed), ["r-mixed", "r-noleg", "r-retired"])

    def test_retired_reason(self):
        _, removed = self.run_filters(overlay={})
        self.assertEqual(removed["r-retired"], ["retired"])

    def test_no_available_leg_reason(self):
        _, removed = self.run_filters(overlay={})
        self.assertEqual(removed["r-noleg"], ["no available leg"])

    def test_privacy_reason(self):
        _, removed = self.run_filters(card=self.card(privacy="sensitive"),
                                      overlay=self.overlay)
        self.assertIn("privacy: nosy/big trains on prompts", removed["r-mixed"])

    def test_context_reason(self):
        _, removed = self.run_filters(overlay={})
        self.assertIn("context: need 1000x1.3 > usable 100 on clean/tiny",
                      removed["r-mixed"])

    def test_tool_calls_reason(self):
        _, removed = self.run_filters(card=self.card(kind="implement"),
                                      overlay=self.overlay)
        self.assertIn("tool_calls: clean/bound is unproven", removed["r-mixed"])

    def test_tool_calls_filter_is_skipped_for_a_non_agentic_kind(self):
        _, removed = self.run_filters(card=self.card(kind="research"),
                                      features=self.feats(need_tokens=10),
                                      overlay={})
        self.assertEqual(removed["r-mixed"],
                         ["client_bound: clean/bound needs claude"])

    def test_client_not_installed_reason(self):
        _, removed = self.run_filters(client_state=self.state(installed=False),
                                      overlay=self.overlay)
        self.assertEqual(removed["r-ok"], ["client: opencode not installed"])

    def test_client_not_signed_in_reason(self):
        _, removed = self.run_filters(
            client_state=self.state(installed=True, signed_in=False),
            overlay=self.overlay)
        self.assertEqual(removed["r-ok"], ["client: opencode not signed in"])

    def test_unknown_sign_in_is_not_a_failure(self):
        survivors, removed = self.run_filters(
            client_state={"opencode": {"installed": True, "signed_in": None,
                                       "reason": ""}},
            overlay=self.overlay)
        self.assertIn("r-ok", survivors)

    def test_client_bound_reason(self):
        _, removed = self.run_filters(card=self.card(kind="review"),
                                      features=self.feats(need_tokens=10),
                                      overlay={})
        self.assertEqual(removed["r-mixed"],
                         ["client_bound: clean/bound needs claude"])

    def test_all_reasons_are_collected_never_stopping_at_the_first(self):
        _, removed = self.run_filters(
            card=self.card(kind="implement", privacy="sensitive"),
            features=self.feats(need_tokens=1000), overlay={})
        self.assertEqual(removed["r-mixed"], [
            "privacy: nosy/big trains on prompts",
            "context: need 1000x1.3 > usable 100 on clean/tiny",
            "tool_calls: clean/bound is unproven",
            "client_bound: clean/bound needs claude",
        ])

    def test_missing_need_tokens_fails_closed(self):
        with self.assertRaises(ValueError) as cm:
            r.filter_routes(self.card(), {}, self.state(), self.registry, {})
        self.assertIn("need_tokens", str(cm.exception))

    # --- override after the filters ---------------------------------------

    def test_override_none(self):
        self.assertEqual(r.apply_override(self.card(), ["r-ok"], {}),
                         (None, "no override"))
        self.assertEqual(r.apply_override({"override": {}}, ["r-ok"], {}),
                         (None, "no override"))

    def test_override_survivor(self):
        self.assertEqual(
            r.apply_override({"override": {"route": "r-ok"}}, ["r-ok"], {}),
            ("r-ok", "override: r-ok"))

    def test_override_removed_names_every_reason(self):
        self.assertEqual(
            r.apply_override({"override": {"route": "r-mixed"}}, [],
                             {"r-mixed": ["retired", "no available leg"]}),
            (None, "override r-mixed removed by filters: "
                   "retired; no available leg"))

    def test_override_unknown_route(self):
        self.assertEqual(
            r.apply_override({"override": {"route": "r-nope"}}, ["r-ok"], {}),
            (None, "override r-nope: unknown route"))

    def test_override_cannot_resurrect_a_filtered_route(self):
        survivors, removed = self.run_filters(overlay={})
        route, reason = r.apply_override({"override": {"route": "r-mixed"}},
                                         survivors, removed)
        self.assertIsNone(route)
        self.assertTrue(reason.startswith("override r-mixed removed by filters: "))

    # --- no_route ---------------------------------------------------------

    def test_no_route_shape(self):
        removed = {
            "a": ["retired"],
            "b": ["no available leg", "client: opencode not installed"],
        }
        self.assertEqual(r.no_route(removed), {
            "route": None,
            "state": "input_required",
            "reason": ("no route survives the filters: "
                       "a: retired | b: no available leg; "
                       "client: opencode not installed"),
            "hints": ["sign in", "narrow paths", "split the task", "override"],
        })

    # --- the real catalog -------------------------------------------------

    def test_real_registry_filters_without_error(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        card = {"kind": "implement", "privacy": "public"}
        features = {"need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        survivors, removed = r.filter_routes(card, features, client_state,
                                             registry, {})
        self.assertIsInstance(survivors, list)
        self.assertIsInstance(removed, dict)
        for route_id in survivors:
            self.assertIn(route_id, registry["routes"])
        for route_id, reasons in removed.items():
            self.assertIn(route_id, registry["routes"])
            self.assertTrue(reasons)
            for reason in reasons:
                self.assertIsInstance(reason, str)


class ToolCallsOverlayTests(unittest.TestCase):
    """The tool_calls filter's overlay wins over the registry (TC1).

    tools/probe-toolcalls.py writes verdicts to overlay["legs"][leg]
    ["tool_calls"]["value"], keyed by the leg exactly as written in a route's
    ``legs`` (an omniroute_id alias included). filter_routes must read that
    before falling back to the registry model's own ``tool_calls``.
    """

    def setUp(self):
        self.registry = {
            "providers": {
                "clean": {"id": "clean", "trains_on_prompts": False},
            },
            "models": {
                "flaky": {"id": "flaky", "tool_calls": "unproven",
                          "context_usable": {"tokens": 100000, "source": "default"}},
                "solid": {"id": "solid", "tool_calls": "proven",
                          "context_usable": {"tokens": 100000, "source": "default"}},
            },
            "routes": {
                "r-flaky": {"id": "r-flaky", "legs": ["clean/flaky"]},
                "r-solid": {"id": "r-solid", "legs": ["clean/solid"]},
            },
        }

    def card(self):
        return {"kind": "implement", "privacy": "public"}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}

    def feats(self):
        return {"need_tokens": 10}

    def filt(self, overlay):
        return r.filter_routes(self.card(), self.feats(), self.state(),
                               self.registry, overlay)

    def test_overlay_proven_keeps_a_route_the_registry_alone_removes(self):
        survivors, removed = self.filt({})
        self.assertNotIn("r-flaky", survivors)
        self.assertIn("tool_calls: clean/flaky is unproven", removed["r-flaky"])

        overlay = {"legs": {"clean/flaky": {"tool_calls": {"value": "proven",
                                                          "source": "probe"}}}}
        survivors, removed = self.filt(overlay)
        self.assertIn("r-flaky", survivors)
        self.assertNotIn("r-flaky", removed)

    def test_overlay_broken_removes_a_route_the_registry_alone_keeps(self):
        survivors, removed = self.filt({})
        self.assertIn("r-solid", survivors)

        overlay = {"legs": {"clean/solid": {"tool_calls": {"value": "broken",
                                                           "source": "probe"}}}}
        survivors, removed = self.filt(overlay)
        self.assertNotIn("r-solid", survivors)
        self.assertIn("tool_calls: clean/solid is broken", removed["r-solid"])

    def test_overlay_matches_the_leg_exactly_as_written_omniroute_alias(self):
        self.registry["providers"]["clean"]["omniroute_id"] = "opencode-clean"
        self.registry["routes"]["r-alias"] = {"id": "r-alias",
                                              "legs": ["opencode-clean/flaky"]}
        overlay = {"legs": {"opencode-clean/flaky": {"tool_calls": {"value": "proven"}}}}
        survivors, _ = self.filt(overlay)
        self.assertIn("r-alias", survivors)

    def test_overlay_without_a_value_key_falls_back_to_the_registry(self):
        # A leg the overlay only tracked an error for, never a value: the
        # registry's own tool_calls still governs.
        overlay = {"legs": {"clean/solid": {"tool_calls_last_error": {"detail": "429s"}}}}
        survivors, _ = self.filt(overlay)
        self.assertIn("r-solid", survivors)


class ScoreTests(unittest.TestCase):
    """Expected-cost scoring and theta picking (spec 5.3 steps 4-5, 5.4).

    A small inline registry keeps every number hand-computable; one smoke test
    then runs the same functions over the real catalog/ai-registry.json.
    """

    def registry(self):
        """A fresh inline registry: one free, one cheap-paid, one frontier leg.

        Prices are per token, exactly as the catalog stores them. Seed priors
        are set so free/cheap pass S1 at p=0.7 and frontier at p=0.975.
        """
        return {
            "providers": {
                "free-p": {"id": "free-p"},
                "plain-p": {"id": "plain-p"},
                "cheap-p": {"id": "cheap-p"},
                "frontier-p": {"id": "frontier-p"},
            },
            "models": {
                "free-model": {"id": "free-model", "reasoning": False,
                               "price_in": 0.0, "price_out": 0.0, "output_max": 1000},
                "plain-model": {"id": "plain-model", "reasoning": False,
                                "price_in": 1e-6, "price_out": 2e-6,
                                "output_max": 32768},
                "cheap-model": {"id": "cheap-model", "reasoning": True,
                                "price_in": 1e-6, "price_out": 2e-6,
                                "output_max": 200000,
                                "effort_ladder": ["none", "low", "medium", "high"]},
                "frontier-model": {"id": "frontier-model", "reasoning": True,
                                   "price_in": 1e-5, "price_out": 3e-5,
                                   "output_max": 200000,
                                   "effort_ladder": ["none", "low", "medium", "high",
                                                     "max"]},
                "orch": {"id": "orch", "price_in": 5e-6, "price_out": 1e-5},
            },
            "routes": {
                "r-free": {"id": "r-free", "class": "free",
                           "legs": ["free-p/free-model"]},
                "r-plain": {"id": "r-plain", "class": "cheap",
                            "legs": ["plain-p/plain-model"]},
                "r-cheap": {"id": "r-cheap", "class": "cheap",
                            "legs": ["cheap-p/cheap-model"]},
                "r-frontier": {"id": "r-frontier", "class": "frontier",
                               "legs": ["frontier-p/frontier-model"]},
            },
            "policy": {
                "modes": {
                    "cost-first": {"theta": {"value": 0.6},
                                   "lambda": {"value": 0.0}},
                    "balanced": {"theta": {"value": 0.8},
                                 "lambda": {"value": 0.01}},
                    "quality-first": {"theta": {"value": 0.95},
                                      "lambda": {"value": 0.05}},
                },
                "verify_tokens": {
                    "S0": {"tokens": 2000}, "S1": {"tokens": 2000},
                    "S2": {"tokens": 8000}, "S3": {"tokens": 16000},
                    "S4": {"tokens": 32000},
                },
                "latency_seed": {
                    "free": {"minutes": 5}, "cheap": {"minutes": 10},
                    "mid": {"minutes": 15}, "frontier": {"minutes": 30},
                },
                "seed_priors": {
                    "free": {"S1": {"alpha": 7, "beta": 3}},
                    "cheap": {"S1": {"alpha": 7, "beta": 3}},
                    "frontier": {"S1": {"alpha": 39, "beta": 1}},
                },
            },
        }

    def rec(self, route="r-cheap", cls="cheap", bucket="S1", effort="none",
            gate="pass", latency_s=600):
        return {"route": route, "class": cls, "served_leg": "x/y",
                "bucket": bucket, "effort": effort, "tokens_in": 0,
                "tokens_out": 0, "cost": 0, "latency_s": latency_s, "gate": gate,
                "failure_class": None}

    # --- expected_leg -----------------------------------------------------

    def test_expected_leg_is_the_first_serving_leg(self):
        reg = self.registry()
        reg["routes"]["r-cheap"]["legs"] = ["free-p/free-model",
                                            "cheap-p/cheap-model"]
        self.assertEqual(r.expected_leg(reg["routes"]["r-cheap"], reg),
                         ("free-p", "free-model"))

    def test_expected_leg_is_none_without_a_serving_leg(self):
        self.assertIsNone(r.expected_leg({"id": "x", "legs": []}, self.registry()))

    # --- attempt_cost -----------------------------------------------------

    def test_attempt_cost_free_is_zero_and_paid_is_arithmetic(self):
        reg = self.registry()
        self.assertEqual(
            r.attempt_cost(reg["models"]["free-model"], 1000, 1000), 0.0)
        self.assertAlmostEqual(
            r.attempt_cost(reg["models"]["cheap-model"], 1000, 64000), 0.129)

    # --- verify_cost ------------------------------------------------------

    def test_verify_cost_hand_computed(self):
        self.assertAlmostEqual(r.verify_cost("S1", self.registry(), "orch"), 0.01)

    def test_verify_cost_unknown_orchestrator_fails_closed(self):
        with self.assertRaises(ValueError) as cm:
            r.verify_cost("S1", self.registry(), "ghost")
        self.assertIn("ghost", str(cm.exception))

    # --- latency_minutes --------------------------------------------------

    def test_latency_minutes_median_of_route_records(self):
        reg = self.registry()
        records = [self.rec(route="r-cheap", latency_s=s) for s in (540, 600, 660)]
        records.append(self.rec(route="r-free", cls="free", latency_s=6))
        self.assertEqual(r.latency_minutes("r-cheap", "cheap", records, reg),
                         (10.0, "route"))

    def test_latency_minutes_even_count_median(self):
        reg = self.registry()
        records = [self.rec(route="r-cheap", latency_s=s) for s in (540, 660)]
        self.assertEqual(r.latency_minutes("r-cheap", "cheap", records, reg),
                         (10.0, "route"))

    def test_latency_minutes_falls_back_to_the_seed(self):
        reg = self.registry()
        self.assertEqual(r.latency_minutes("r-free", "free", [], reg),
                         (5.0, "seed"))
        self.assertEqual(r.latency_minutes("r-cheap", "cheap", [], reg),
                         (10.0, "seed"))
        self.assertEqual(r.latency_minutes("r-frontier", "frontier", [], reg),
                         (30.0, "seed"))

    # --- score_route ------------------------------------------------------

    def test_score_route_expected_cost_hand_computed(self):
        score = r.score_route("r-cheap", "S1", "high", {"need_tokens": 1000},
                              self.registry(), [], "orch", "balanced")
        self.assertEqual(score["route"], "r-cheap")
        self.assertEqual(score["leg"], "cheap-p/cheap-model")
        self.assertEqual(score["p_source"], "prior")
        self.assertAlmostEqual(score["p"], 0.7)
        # max_tokens("high") = 64000: 1000*1e-6 + 64000*2e-6
        self.assertAlmostEqual(score["c_attempt"], 0.129)
        # policy.verify_tokens.S1 = 2000 x orch price_in 5e-6
        self.assertAlmostEqual(score["c_verify"], 0.01)
        self.assertAlmostEqual(score["minutes"], 10.0)
        # (0.129 + 0.01)/0.7 + 0.01*10/0.7
        self.assertAlmostEqual(score["expected_cost"], 0.3414285714285714)
        self.assertEqual(
            score["reason"],
            "E=0.341429 p=0.70 (prior) T=10m on cheap-p/cheap-model")

    def test_score_route_free_leg_costs_nothing_to_attempt(self):
        score = r.score_route("r-free", "S1", None, {"need_tokens": 1000},
                              self.registry(), [], "orch", "cost-first")
        self.assertEqual(score["leg"], "free-p/free-model")
        self.assertEqual(score["c_attempt"], 0.0)
        self.assertAlmostEqual(score["c_verify"], 0.01)
        self.assertAlmostEqual(score["expected_cost"], 0.01 / 0.7)
        self.assertEqual(
            score["reason"],
            "E=0.014286 p=0.70 (prior) T=5m on free-p/free-model")

    def test_score_route_non_reasoning_uses_output_max_not_the_floor(self):
        # max_tokens("high", reasoning=True) is 64000; plain-model does not
        # reason, so out_tokens is its output_max, 32768.
        score = r.score_route("r-plain", "S1", "high", {"need_tokens": 1000},
                              self.registry(), [], "orch", "cost-first")
        self.assertAlmostEqual(score["c_attempt"], 1000e-6 + 32768 * 2e-6)

    def test_score_route_p_uses_route_observations_first(self):
        records = [self.rec(route="r-cheap", effort="high", gate="pass")
                   for _ in range(3)]
        score = r.score_route("r-cheap", "S1", "high", {"need_tokens": 1000},
                              self.registry(), records, "orch", "cost-first")
        self.assertEqual(score["p_source"], "route")
        self.assertAlmostEqual(score["p"], 10 / 13)

    def test_score_route_p_falls_back_to_the_class(self):
        records = [self.rec(route="r-twin", cls="cheap", effort="high",
                            gate="pass"),
                   self.rec(route="r-twin", cls="cheap", effort="high",
                            gate="fail")]
        score = r.score_route("r-cheap", "S1", "high", {"need_tokens": 1000},
                              self.registry(), records, "orch", "cost-first")
        self.assertEqual(score["p_source"], "class")
        self.assertAlmostEqual(score["p"], 8 / 12)

    def test_score_route_p_falls_back_to_the_prior(self):
        score = r.score_route("r-cheap", "S1", "high", {"need_tokens": 1000},
                              self.registry(), [], "orch", "cost-first")
        self.assertEqual(score["p_source"], "prior")
        self.assertAlmostEqual(score["p"], 7 / 10)

    def test_score_route_missing_need_tokens_fails_closed(self):
        with self.assertRaises(ValueError) as cm:
            r.score_route("r-cheap", "S1", "high", {}, self.registry(), [],
                          "orch", "balanced")
        self.assertIn("need_tokens", str(cm.exception))

    # --- pick -------------------------------------------------------------

    def score_set(self, reg, mode):
        return [r.score_route(rid, "S1", "high", {"need_tokens": 1000}, reg, [],
                              "orch", mode)
                for rid in ("r-free", "r-cheap", "r-frontier")]

    def test_pick_lowest_cost_above_theta(self):
        reg = self.registry()
        picked, reason = r.pick(self.score_set(reg, "cost-first"),
                                "cost-first", reg)
        self.assertEqual(picked["route"], "r-free")
        self.assertEqual(reason, "theta 0.6 met: lowest E=0.014286 on r-free")

    def test_pick_quality_first_flips_to_the_frontier(self):
        reg = self.registry()
        cost, _ = r.pick(self.score_set(reg, "cost-first"), "cost-first", reg)
        quality, reason = r.pick(self.score_set(reg, "quality-first"),
                                 "quality-first", reg)
        self.assertEqual(cost["route"], "r-free")
        self.assertEqual(quality["route"], "r-frontier")
        self.assertEqual(reason,
                         "theta 0.95 met: lowest E=3.528205 on r-frontier")

    def test_pick_prefers_the_lowest_cost_not_the_input_order(self):
        reg = self.registry()
        pricier = {"route": "a", "p": 0.9, "expected_cost": 5.0}
        cheaper = {"route": "b", "p": 0.9, "expected_cost": 1.0}
        picked, _ = r.pick([pricier, cheaper], "cost-first", reg)
        self.assertIs(picked, cheaper)

    def test_pick_ties_keep_the_input_order(self):
        reg = self.registry()
        first = {"route": "a", "p": 0.9, "expected_cost": 1.0}
        second = {"route": "b", "p": 0.9, "expected_cost": 1.0}
        picked, _ = r.pick([first, second], "cost-first", reg)
        self.assertIs(picked, first)

    def test_pick_says_theta_was_missed_when_none_reaches_it(self):
        reg = self.registry()
        scores = [r.score_route(rid, "S1", "high", {"need_tokens": 1000}, reg,
                                [], "orch", "quality-first")
                  for rid in ("r-free", "r-cheap")]
        picked, reason = r.pick(scores, "quality-first", reg)
        # Both sit at p=0.7, below theta=0.95; the tie breaks on lowest cost.
        self.assertEqual(picked["route"], "r-free")
        self.assertEqual(reason, "theta 0.95 missed: best p 0.7 on r-free")

    def test_pick_missed_theta_takes_the_highest_p(self):
        reg = self.registry()
        low = {"route": "a", "p": 0.5, "expected_cost": 1.0}
        high = {"route": "b", "p": 0.8, "expected_cost": 9.0}
        picked, reason = r.pick([low, high], "quality-first", reg)
        self.assertIs(picked, high)
        self.assertEqual(reason, "theta 0.95 missed: best p 0.8 on b")

    def test_pick_missed_theta_tie_breaks_on_lowest_cost(self):
        reg = self.registry()
        dear = {"route": "a", "p": 0.5, "expected_cost": 2.0}
        cheap = {"route": "b", "p": 0.5, "expected_cost": 1.0}
        picked, reason = r.pick([dear, cheap], "quality-first", reg)
        self.assertIs(picked, cheap)
        self.assertEqual(reason, "theta 0.95 missed: best p 0.5 on b")

    def test_pick_empty_scores_fails_closed(self):
        with self.assertRaises(ValueError):
            r.pick([], "balanced", self.registry())

    # --- missing policy keys fail closed ----------------------------------

    def test_missing_mode_fails_closed_naming_it(self):
        with self.assertRaises(ValueError) as cm:
            r.pick([{"route": "a", "p": 0.9, "expected_cost": 1.0}],
                   "turbo", self.registry())
        self.assertIn("turbo", str(cm.exception))

    def test_missing_policy_keys_fail_closed_naming_them(self):
        reg = self.registry()
        del reg["policy"]["verify_tokens"]["S1"]
        with self.assertRaises(ValueError) as cm:
            r.verify_cost("S1", reg, "orch")
        self.assertIn("verify_tokens", str(cm.exception))

        reg = self.registry()
        del reg["policy"]["latency_seed"]["cheap"]
        with self.assertRaises(ValueError) as cm:
            r.latency_minutes("r-cheap", "cheap", [], reg)
        self.assertIn("latency_seed", str(cm.exception))

        reg = self.registry()
        del reg["policy"]["seed_priors"]["cheap"]
        with self.assertRaises(ValueError) as cm:
            r.score_route("r-cheap", "S1", "high", {"need_tokens": 1000},
                          reg, [], "orch", "balanced")
        self.assertIn("cheap", str(cm.exception))

    # --- the real catalog -------------------------------------------------

    def test_real_registry_scores_and_picks(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        card = {"kind": "review", "privacy": "public"}
        features = {"need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        survivors, _ = r.filter_routes(card, features, client_state, registry, {})
        self.assertTrue(survivors)

        scores = []
        for route_id in survivors:
            route = registry["routes"][route_id]
            leg = r.expected_leg(route, registry)
            model = registry["models"][leg[1]]
            effort_name = r.effort(
                "S1", "review", route["class"],
                model.get("effort_ladder") or [], model.get("reasoning", False))
            scores.append(r.score_route(route_id, "S1", effort_name, features,
                                        registry, [], "muse-spark", "balanced"))

        picked, reason = r.pick(scores, "balanced", registry)
        self.assertIn(picked["route"], survivors)
        for score in scores:
            self.assertGreaterEqual(score["p"], 0.0)
            self.assertLessEqual(score["p"], 1.0)
            self.assertGreaterEqual(score["expected_cost"], 0.0)
            self.assertTrue(score["reason"].startswith("E="))
        self.assertIsInstance(reason, str)


class TimeTests(unittest.TestCase):
    """Time tie-break and defer by provider windows (spec 5.3 step 6, 6.4).

    A small inline registry: provider "dsk" has a weekday cheap window and a
    weekend cheap window (DeepSeek's real shape, without depending on its
    exact catalog hours); provider "flat" carries no windows at all. One
    smoke test then checks the real catalog/ai-registry.json's deepseek
    windows at a fixed Saturday.
    """

    def registry(self):
        return {
            "providers": {
                "dsk": {"id": "dsk", "windows": [
                    {"days": ["mon", "tue", "wed", "thu", "fri"],
                     "utc_from": "10:00", "utc_to": "23:59",
                     "price_factor": 0.5, "kind": "price",
                     "source": "test", "verified": "2026-09-25"},
                    {"days": ["sat", "sun"],
                     "utc_from": "00:00", "utc_to": "23:59",
                     "price_factor": 0.5, "kind": "price",
                     "source": "test", "verified": "2026-09-25"},
                ]},
                "flat": {"id": "flat"},
            },
        }

    def dt(self, y, mo, d, h, mi):
        return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)

    def score(self, route, leg, expected_cost):
        return {"route": route, "leg": leg, "expected_cost": expected_cost}

    # --- price_factor -------------------------------------------------

    def test_price_factor_inside_window(self):
        # Monday 2026-09-28 10:00 UTC is inside the weekday window.
        self.assertEqual(
            r.price_factor("dsk", self.registry(), self.dt(2026, 9, 28, 10, 0)),
            0.5)

    def test_price_factor_outside_window(self):
        self.assertEqual(
            r.price_factor("dsk", self.registry(), self.dt(2026, 9, 28, 9, 59)),
            1.0)

    def test_price_factor_at_the_2359_end_boundary(self):
        # "23:59" as utc_to means to midnight: 23:59 itself is still inside.
        self.assertEqual(
            r.price_factor("dsk", self.registry(), self.dt(2026, 9, 28, 23, 59)),
            0.5)

    def test_price_factor_weekend_window(self):
        # Saturday 2026-09-26 is covered all day.
        self.assertEqual(
            r.price_factor("dsk", self.registry(), self.dt(2026, 9, 26, 3, 0)),
            0.5)

    def test_price_factor_provider_without_windows_is_always_1(self):
        self.assertEqual(
            r.price_factor("flat", self.registry(), self.dt(2026, 9, 28, 10, 0)),
            1.0)

    # --- next_cheap_start -----------------------------------------------

    def test_next_cheap_start_from_before_the_window(self):
        self.assertEqual(
            r.next_cheap_start("dsk", self.registry(),
                               self.dt(2026, 9, 28, 9, 20)),
            self.dt(2026, 9, 28, 10, 0))

    def test_next_cheap_start_already_inside_is_none(self):
        self.assertIsNone(
            r.next_cheap_start("dsk", self.registry(),
                               self.dt(2026, 9, 28, 10, 30)))

    def test_next_cheap_start_no_windows_is_none(self):
        self.assertIsNone(
            r.next_cheap_start("flat", self.registry(),
                               self.dt(2026, 9, 28, 9, 20)))

    # --- tie_break --------------------------------------------------------

    def test_tie_break_prefers_a_cheap_window_within_10_percent(self):
        now = self.dt(2026, 9, 28, 10, 0)  # dsk is cheap now
        best = self.score("r-best", "flat/model", 1.0)
        cheap = self.score("r-cheap", "dsk/model", 1.09)  # 9% worse
        chosen, reason = r.tie_break([best, cheap], self.registry(), now)
        self.assertIs(chosen, cheap)
        self.assertEqual(
            reason, "time: r-cheap in cheap window (x0.5) within 10% of best")

    def test_tie_break_keeps_the_best_when_the_cheap_one_is_11_percent_worse(self):
        now = self.dt(2026, 9, 28, 10, 0)
        best = self.score("r-best", "flat/model", 1.0)
        cheap = self.score("r-cheap", "dsk/model", 1.11)  # outside 10%
        chosen, reason = r.tie_break([best, cheap], self.registry(), now)
        self.assertIs(chosen, best)
        self.assertEqual(reason, "time: no cheaper window within 10%")

    def test_tie_break_empty_scores_fails_closed(self):
        with self.assertRaises(ValueError):
            r.tie_break([], self.registry(), self.dt(2026, 9, 28, 10, 0))

    # --- defer_until --------------------------------------------------------

    def test_defer_until_returns_the_start_before_the_deadline(self):
        now = self.dt(2026, 9, 28, 9, 20)
        card = {"deferrable": True, "deadline": "2026-09-28T12:00Z"}
        chosen = self.score("r-x", "dsk/model", 1.0)
        t, reason = r.defer_until(card, chosen, self.registry(), now)
        self.assertEqual(t, self.dt(2026, 9, 28, 10, 0))
        self.assertTrue(
            reason.startswith("defer: dsk cheap from 2026-09-28T10:00Z"))

    def test_defer_until_none_after_the_deadline(self):
        now = self.dt(2026, 9, 28, 9, 20)
        card = {"deferrable": True, "deadline": "2026-09-28T09:30Z"}
        chosen = self.score("r-x", "dsk/model", 1.0)
        t, reason = r.defer_until(card, chosen, self.registry(), now)
        self.assertIsNone(t)
        self.assertTrue(reason.startswith("no defer"))

    def test_defer_until_none_when_not_deferrable(self):
        now = self.dt(2026, 9, 28, 9, 20)
        card = {"deferrable": False, "deadline": "2026-09-28T12:00Z"}
        chosen = self.score("r-x", "dsk/model", 1.0)
        self.assertEqual(
            r.defer_until(card, chosen, self.registry(), now),
            (None, "not deferrable"))

    def test_defer_until_none_when_already_cheap(self):
        now = self.dt(2026, 9, 28, 10, 30)  # already inside the cheap window
        card = {"deferrable": True, "deadline": "2026-09-28T12:00Z"}
        chosen = self.score("r-x", "dsk/model", 1.0)
        t, reason = r.defer_until(card, chosen, self.registry(), now)
        self.assertIsNone(t)
        self.assertTrue(reason.startswith("no defer"))

    # --- the real catalog -------------------------------------------------

    def test_a_non_utc_now_is_read_in_utc(self):
        # review-b3c2: Monday 15:00+05:00 is Monday 10:00 UTC, inside dsk's
        # weekday 10:00-23:59 window.
        from datetime import timedelta as _td
        reg = self.registry()
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone(_td(hours=5)))
        self.assertEqual(r.price_factor("dsk", reg, now), 0.5)
        start = r.next_cheap_start(
            "dsk", reg, datetime(2026, 9, 28, 14, 20, tzinfo=timezone(_td(hours=5))))
        self.assertEqual(start, datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc))

    def test_a_window_off_the_quarter_hour_is_found(self):
        # review-b3c2: 15-minute sampling missed a 09:05-09:10 window.
        reg = {"providers": {"p": {"id": "p", "windows": [
            {"days": ["mon"], "utc_from": "09:05", "utc_to": "09:10",
             "price_factor": 0.5}]}}}
        now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(r.next_cheap_start("p", reg, now),
                         datetime(2026, 9, 28, 9, 5, tzinfo=timezone.utc))

    def test_real_registry_deepseek_weekend_factor(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        saturday = self.dt(2026, 9, 26, 12, 0)
        self.assertEqual(r.price_factor("deepseek", registry, saturday), 0.5)


class PlanTests(unittest.TestCase):
    """plan(): the resolver v2 entry point, spec 5 intro, 5.3 steps 1-7, 5.5, 5.7.

    A small inline registry, in the same hand-computable style as ScoreTests
    and TimeTests: two model families ("alpha", "beta") on free/cheap/frontier,
    plus a third family ("gamma") on a mid route so the S3 case can show two
    genuinely cross-family reviewers and a capability escalation that moves up
    a class. One smoke test then runs plan() over the real
    catalog/ai-registry.json.
    """

    def registry(self):
        return {
            "providers": {
                "free-p": {"id": "free-p"},
                "cheap-p": {"id": "cheap-p", "windows": [
                    {"days": ["mon", "tue", "wed", "thu", "fri"],
                     "utc_from": "10:00", "utc_to": "23:59",
                     "price_factor": 0.5, "kind": "price",
                     "source": "test", "verified": "2026-09-25"},
                ]},
                "mid-p": {"id": "mid-p"},
                "frontier-p": {"id": "frontier-p"},
            },
            "models": {
                "free-model": {"id": "free-model", "family": "alpha",
                               "reasoning": False, "effort_ladder": [],
                               "tool_calls": "proven",
                               "price_in": 0.0, "price_out": 0.0,
                               "output_max": 1000,
                               "context_usable": {"tokens": 100000,
                                                  "source": "default"}},
                "cheap-model": {"id": "cheap-model", "family": "beta",
                                "reasoning": True,
                                "effort_ladder": ["none", "low", "medium", "high"],
                                "tool_calls": "proven",
                                "price_in": 1e-6, "price_out": 2e-6,
                                "output_max": 200000,
                                "context_usable": {"tokens": 200000,
                                                   "source": "default"}},
                "mid-model": {"id": "mid-model", "family": "gamma",
                              "reasoning": True,
                              "effort_ladder": ["none", "low", "medium", "high",
                                                "xhigh"],
                              "tool_calls": "proven",
                              "price_in": 5e-6, "price_out": 1e-5,
                              "output_max": 200000,
                              "context_usable": {"tokens": 200000,
                                                 "source": "default"}},
                "frontier-model": {"id": "frontier-model", "family": "alpha",
                                   "reasoning": True,
                                   "effort_ladder": ["none", "low", "medium",
                                                     "high", "max"],
                                   "tool_calls": "proven",
                                   "price_in": 1e-5, "price_out": 3e-5,
                                   "output_max": 200000,
                                   "context_usable": {"tokens": 200000,
                                                      "source": "default"}},
                "orch": {"id": "orch", "price_in": 5e-6, "price_out": 1e-5,
                         "context_usable": {"tokens": 200000,
                                            "source": "default"}},
            },
            "routes": {
                "r-free": {"id": "r-free", "class": "free",
                           "legs": ["free-p/free-model"]},
                "r-cheap": {"id": "r-cheap", "class": "cheap",
                            "legs": ["cheap-p/cheap-model"]},
                "r-mid": {"id": "r-mid", "class": "mid",
                          "legs": ["mid-p/mid-model"]},
                "r-frontier": {"id": "r-frontier", "class": "frontier",
                               "legs": ["frontier-p/frontier-model"]},
                "r-retired": {"id": "r-retired", "class": "cheap",
                              "retired": True, "legs": ["cheap-p/cheap-model"]},
            },
            "policy": {
                "modes": {
                    "cost-first": {"theta": {"value": 0.6},
                                   "lambda": {"value": 0.0}},
                    "balanced": {"theta": {"value": 0.8},
                                 "lambda": {"value": 0.01}},
                    "quality-first": {"theta": {"value": 0.95},
                                      "lambda": {"value": 0.05}},
                },
                "verify_tokens": {
                    "S0": {"tokens": 2000}, "S1": {"tokens": 2000},
                    "S2": {"tokens": 8000}, "S3": {"tokens": 16000},
                    "S4": {"tokens": 32000},
                },
                "latency_seed": {
                    "free": {"minutes": 5}, "cheap": {"minutes": 10},
                    "mid": {"minutes": 15}, "frontier": {"minutes": 30},
                },
                # free/cheap: reliable at S0-S1, fail S2+ (spec 5.6's real
                # measurement shape). mid: steady. frontier: always reliable.
                "seed_priors": {
                    "free": {"S0": {"alpha": 8, "beta": 2},
                             "S1": {"alpha": 8, "beta": 2},
                             "S2": {"alpha": 2, "beta": 8},
                             "S3": {"alpha": 1, "beta": 9},
                             "S4": {"alpha": 1, "beta": 9}},
                    "cheap": {"S0": {"alpha": 8, "beta": 2},
                              "S1": {"alpha": 8, "beta": 2},
                              "S2": {"alpha": 2, "beta": 8},
                              "S3": {"alpha": 1, "beta": 9},
                              "S4": {"alpha": 1, "beta": 9}},
                    "mid": {"S0": {"alpha": 8, "beta": 2},
                            "S1": {"alpha": 8, "beta": 2},
                            "S2": {"alpha": 8, "beta": 2},
                            "S3": {"alpha": 8, "beta": 2},
                            "S4": {"alpha": 8, "beta": 2}},
                    "frontier": {"S0": {"alpha": 99, "beta": 1},
                                 "S1": {"alpha": 99, "beta": 1},
                                 "S2": {"alpha": 99, "beta": 1},
                                 "S3": {"alpha": 99, "beta": 1},
                                 "S4": {"alpha": 99, "beta": 1}},
                },
            },
        }

    def card(self, **overrides):
        base = {"kind": "implement", "spec": "exact", "risk": "normal",
                "mode": "balanced", "privacy": "public"}
        base.update(overrides)
        return base

    def features(self, **overrides):
        base = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                "tests": True, "need_tokens": 1000}
        base.update(overrides)
        return base

    def state(self, installed=True, signed_in=True):
        return {"opencode": {"installed": installed, "signed_in": signed_in,
                             "reason": ""}}

    def dt(self, y, mo, d, h, mi):
        return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)

    # A fixed clock, Tuesday before cheap-p's 10:00 window: the tie-break has
    # nothing to prefer, so "ready" plans are deterministic.
    def now(self):
        return self.dt(2026, 9, 29, 9, 0)

    def s0_plan(self, **card_overrides):
        return r.plan(self.card(**card_overrides), self.features(),
                     self.state(), self.registry(), {}, [], "orch",
                     self.now())

    # --- every key, the S0/ready shape ------------------------------------

    def test_every_output_key_present(self):
        result = self.s0_plan()
        self.assertEqual(set(result.keys()), {
            "route", "class", "client", "leg", "effort", "max_tokens",
            "context_budget", "bucket", "decompose", "p", "expected_cost",
            "reviewers", "escalation", "state", "defer_until", "reason",
            "explain",
        })
        self.assertEqual(result["route"], "r-free")
        self.assertEqual(result["class"], "free")
        self.assertEqual(result["client"], "opencode")
        self.assertEqual(result["leg"], "free-p/free-model")
        self.assertIsNone(result["effort"])
        self.assertIsNone(result["max_tokens"])
        self.assertEqual(result["context_budget"], 100000)
        self.assertEqual(result["bucket"], "S0")
        self.assertFalse(result["decompose"])
        self.assertAlmostEqual(result["p"], 0.8)
        self.assertAlmostEqual(result["expected_cost"], 0.075)
        self.assertEqual(result["state"], "ready")
        self.assertIsNone(result["defer_until"])
        self.assertEqual(
            result["reason"],
            "theta 0.8 met: lowest E=0.075000 on r-free; "
            "time: no cheaper window within 10%; not deferrable")
        self.assertEqual(len(result["explain"]), 4)

    def test_reviewers_normal_risk_is_one_cross_family_route(self):
        result = self.s0_plan()
        # chosen r-free is family "alpha"; the cheapest non-alpha survivor is
        # r-cheap ("beta").
        self.assertEqual(result["reviewers"],
                         {"routes": ["r-cheap"], "closer": None,
                          "reason": "cross-family reviewer(s): r-cheap"})

    def test_escalation_none_when_effort_is_none_and_class_moves_up(self):
        result = self.s0_plan()
        self.assertEqual(result["escalation"], [
            {"on": "logic", "route": "r-free", "effort": None},
            {"on": "capability", "route": "r-cheap"},
        ])

    def test_explain_has_one_line_per_scored_route_in_registry_order(self):
        result = self.s0_plan()
        legs_in_order = ["free-p/free-model", "cheap-p/cheap-model",
                         "mid-p/mid-model", "frontier-p/frontier-model"]
        self.assertEqual(len(result["explain"]), len(legs_in_order))
        for line, leg in zip(result["explain"], legs_in_order):
            self.assertIn(leg, line)

    # --- no survivor -------------------------------------------------------

    def test_no_survivor_is_input_required_with_bucket(self):
        result = r.plan(self.card(), self.features(),
                        self.state(installed=False), self.registry(), {}, [],
                        "orch", self.now())
        self.assertEqual(result["route"], None)
        self.assertEqual(result["state"], "input_required")
        self.assertEqual(result["bucket"], "S0")
        self.assertIn("hints", result)
        self.assertIn("no route survives the filters", result["reason"])

    # --- override ------------------------------------------------------

    def test_override_to_a_survivor_scores_only_it(self):
        result = self.s0_plan(override={"route": "r-cheap"})
        self.assertEqual(result["route"], "r-cheap")
        self.assertEqual(len(result["explain"]), 1)

    def test_override_to_a_removed_route_is_input_required(self):
        result = self.s0_plan(override={"route": "r-retired"})
        self.assertEqual(result, {
            "route": None,
            "state": "input_required",
            "reason": "override r-retired removed by filters: retired",
            "bucket": "S0",
        })

    # --- S3: decompose, reviewers, escalation across classes ---------------

    def s3_plan(self, **card_overrides):
        card_overrides.setdefault("risk", "high")
        card_overrides.setdefault("spec", "partial")
        return r.plan(self.card(**card_overrides),
                     self.features(files=6, fanout=10, lines=150,
                                  tests=False),
                     self.state(), self.registry(), {}, [], "orch",
                     self.now())

    def test_bucket_s3_sets_decompose_true(self):
        result = self.s3_plan()
        self.assertEqual(result["bucket"], "S3")
        self.assertTrue(result["decompose"])
        self.assertEqual(result["route"], "r-mid")

    def test_reviewers_high_risk_is_two_cross_family_routes_plus_the_closer(self):
        result = self.s3_plan()
        # chosen r-mid is family "gamma"; the two cheapest routes of a
        # different family from "gamma" and from each other are r-free
        # ("alpha") and r-cheap ("beta") -- r-frontier is skipped, same
        # family ("alpha") as the already-picked r-free.
        self.assertEqual(result["reviewers"], {
            "routes": ["r-free", "r-cheap"],
            "closer": {"client": "claude", "model": "sonnet"},
            "reason": "cross-family reviewer(s): r-free, r-cheap",
        })

    def test_escalation_logic_raises_one_rung_capability_moves_up_a_class(self):
        result = self.s3_plan()
        self.assertEqual(result["escalation"], [
            {"on": "logic", "route": "r-mid", "effort": "xhigh"},
            {"on": "capability", "route": "r-frontier"},
        ])

    # --- time and defer ------------------------------------------------

    def test_deferrable_card_with_a_deadline_after_the_next_cheap_window(self):
        card = self.card(override={"route": "r-cheap"}, deferrable=True,
                         deadline="2026-09-29T12:00Z")
        result = r.plan(card, self.features(), self.state(), self.registry(),
                        {}, [], "orch", self.dt(2026, 9, 29, 9, 20))
        self.assertEqual(result["route"], "r-cheap")
        self.assertEqual(result["state"], "deferred")
        self.assertEqual(result["defer_until"], "2026-09-29T10:00Z")
        self.assertIn("defer: cheap-p cheap from 2026-09-29T10:00Z",
                     result["reason"])

    # --- missing card keys ------------------------------------------------

    def test_missing_card_key_raises_naming_it(self):
        for key in ("kind", "mode", "risk"):
            with self.subTest(key=key):
                card = self.card()
                del card[key]
                with self.assertRaises(ValueError) as cm:
                    r.plan(card, self.features(), self.state(),
                          self.registry(), {}, [], "orch", self.now())
                self.assertIn(key, str(cm.exception))

    # --- the real catalog ---------------------------------------------

    def test_real_registry_review_card_returns_a_route(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        # Every model is tool_calls unproven today, so an agentic kind (e.g.
        # implement) has no surviving route; review is not agentic.
        card = {"kind": "review", "spec": "exact", "risk": "normal",
               "mode": "balanced", "privacy": "public"}
        features = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                   "tests": True, "need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        result = r.plan(card, features, client_state, registry, {}, [],
                        "muse-spark", self.dt(2026, 9, 29, 9, 0))
        self.assertIsNotNone(result["route"])
        self.assertEqual(result["state"], "ready")
        self.assertIn(result["route"], registry["routes"])


if __name__ == "__main__":
    unittest.main()
