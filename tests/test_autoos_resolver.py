#!/usr/bin/env python3
"""Golden tests for tools/autoos_resolver.py (routing v2 spec, sections 5.2 and 5.5).

Every boundary of the bucket table is pinned here, plus every effort row, the
ladder clamp and the max_tokens floors. The module is pure: it imports from any
cwd once `tools/` is on sys.path, which is the first thing this file does.
"""
import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import autoos_resolver as r  # noqa: E402
import autoos_usage as usage  # noqa: E402  (the spend guard's one reader of the cap trio)
import registry as registry_tool  # noqa: E402  (tools/registry.py; private_safe lives here)

# A full canonical ladder, so a rung the rule wants is always present unless a
# test deliberately shortens the ladder.
FULL_LADDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def features(files=1, modules=1, fanout=4, lines=29, tests=True):
    """Minimum-point features: every numeric value is at its lowest band."""
    return {"files": files, "modules": modules, "fanout": fanout,
            "lines": lines, "tests": tests}


def card(spec="exact", kind="implement"):
    return {"spec": spec, "kind": kind}


def clean_head_leg(registry):
    """The leg `t2-worker-clean` heads with, read from the registry.

    Three cases below need "a private-safe leg that a probe has proven
    tool_calls on" without reading the git-ignored probe overlay, so they prove
    this leg in an inline overlay instead of naming a model. MISTRALFIX
    (2026-09-28) moved them off the old name: mistral/mistral-small-latest is
    no longer a leg of any route (0 rpm on the measured plan), and since DSBACK
    the native DeepSeek leg heads both -clean twins.
    """
    return registry["routes"]["t2-worker-clean"]["legs"][0]


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
        # "final" is the D-102 CLAUDEBUDGET kind: the reserved Claude review.
        self.assertEqual(t["kind"], {"implement": 0, "bulk": 0, "review": 0,
                                     "research": 0, "debug": 2, "plan": 3,
                                     "final": 0})
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
                "clean": {"id": "clean", "tier": "paid", "trains_on_prompts": False},
                "nosy": {"id": "nosy", "tier": "paid", "trains_on_prompts": True},
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
        # FT: r-mixed now survives -- its nosy/big leg is usable (review is
        # not agentic, need 1000x1.3 <= big's 100000, no client_bound), even
        # though its other two legs (clean/tiny, clean/bound) are not.
        survivors, removed = self.run_filters(overlay={})
        self.assertEqual(survivors, ["r-ok", "r-mixed"])
        self.assertEqual(sorted(removed), ["r-noleg", "r-retired"])

    def test_retired_reason(self):
        _, removed = self.run_filters(overlay={})
        self.assertEqual(removed["r-retired"], ["retired"])

    def test_no_available_leg_reason(self):
        # FT folds the old "no available leg" route-level check into the new
        # "no usable leg" one: zero serving legs means usable_legs() has
        # nothing to try and nothing to list after the colon.
        _, removed = self.run_filters(overlay={})
        self.assertEqual(removed["r-noleg"], ["no usable leg: "])

    def test_privacy_reason(self):
        # Unchanged by FT: privacy stays route-level, checked regardless of
        # any leg's own usability (the overlay here even makes clean/tiny
        # usable too, so both legs *would* be usable -- privacy still wins).
        _, removed = self.run_filters(card=self.card(privacy="sensitive"),
                                      overlay=self.overlay)
        self.assertIn("privacy: nosy/big trains on prompts", removed["r-mixed"])

    def test_privacy_checks_an_unavailable_leg_the_gateway_still_serves(self):
        # close-priv 2026-09-26: unavailable_legs is registry-only; the gateway
        # combo still lists the leg, so a sensitive card must not pass a route
        # whose hidden leg is unsafe.
        self.registry["providers"]["freepool"] = {
            "id": "freepool", "tier": "free", "trains_on_prompts": False}
        self.registry["models"]["free-model"] = {
            "id": "free-model", "tool_calls": "proven",
            "context_usable": {"tokens": 100000, "source": "default"}}
        self.registry["routes"]["r-hidden"] = {
            "id": "r-hidden", "legs": ["clean/big", "freepool/free-model"],
            "unavailable_legs": {"freepool/free-model": {"available": False}}}
        survivors, removed = self.run_filters(card=self.card(privacy="sensitive"))
        self.assertNotIn("r-hidden", survivors)
        self.assertTrue(any("freepool/free-model" in r for r in removed["r-hidden"]))

    def test_privacy_removes_route_with_a_free_pool_leg_even_after_a_clean_leg(self):
        # PRIV brief 2026-09-26 ("Free first, private never"): a free-tier
        # leg is never private-safe, even one whose own trains_on_prompts is
        # false, and even when it comes after an otherwise-clean first leg --
        # the whole route is removed for a sensitive card (route-level, D-
        # style, not per-leg fall-through).
        self.registry["providers"]["freepool"] = {
            "id": "freepool", "tier": "free", "trains_on_prompts": False}
        self.registry["models"]["free-model"] = {
            "id": "free-model", "tool_calls": "proven",
            "context_usable": {"tokens": 100000, "source": "default"}}
        self.registry["routes"]["r-freepool"] = {
            "id": "r-freepool", "legs": ["clean/big", "freepool/free-model"]}
        survivors, removed = self.run_filters(card=self.card(privacy="sensitive"))
        self.assertNotIn("r-freepool", survivors)
        self.assertTrue(
            any("freepool/free-model" in reason for reason in removed["r-freepool"]),
            removed.get("r-freepool"))

    # --- per-leg filters (FT): usable_legs(), not a route removal ---------

    def test_context_reason(self):
        # FT: a too-small-context leg no longer removes the whole route (see
        # test_survivors_are_route_ids_in_registry_order) -- it is only
        # skipped, in usable_legs()'s own per-leg reasons.
        _, skipped, _ = r.usable_legs(self.registry["routes"]["r-mixed"],
                                   self.card(), self.feats(), self.state(),
                                   self.registry, {})
        self.assertIn("context: need 1000x1.3 > usable 100 on clean/tiny",
                      skipped["clean/tiny"])

    def test_tool_calls_reason(self):
        _, skipped, _ = r.usable_legs(self.registry["routes"]["r-mixed"],
                                   self.card(kind="implement"), self.feats(),
                                   self.state(), self.registry, self.overlay)
        self.assertIn("tool_calls: clean/bound is unproven",
                      skipped["clean/bound"])

    def test_tool_calls_filter_is_skipped_for_a_non_agentic_kind(self):
        _, skipped, _ = r.usable_legs(self.registry["routes"]["r-mixed"],
                                   self.card(kind="research"),
                                   self.feats(need_tokens=10), self.state(),
                                   self.registry, {})
        self.assertEqual(skipped["clean/bound"],
                         ["client_bound: clean/bound needs claude"])

    def test_client_bound_reason(self):
        _, skipped, _ = r.usable_legs(self.registry["routes"]["r-mixed"],
                                   self.card(kind="review"),
                                   self.feats(need_tokens=10), self.state(),
                                   self.registry, {})
        self.assertEqual(skipped["clean/bound"],
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

    def test_all_reasons_are_collected_never_stopping_at_the_first(self):
        # A privacy-sensitive card whose every leg is *also* unusable (need
        # is huge, so context fails everywhere): both the route-level
        # privacy reason and the aggregate "no usable leg" reason show up,
        # and the per-leg collection itself never stops at the first
        # reason either -- clean/bound fails all three per-leg checks.
        _, removed = self.run_filters(
            card=self.card(kind="implement", privacy="sensitive"),
            features=self.feats(need_tokens=1_000_000), overlay={})
        reasons = removed["r-mixed"]
        self.assertEqual(reasons[0], "privacy: nosy/big trains on prompts")
        self.assertEqual(len(reasons), 2)
        no_usable = reasons[1]
        self.assertTrue(no_usable.startswith("no usable leg: "))
        self.assertIn("clean/bound: context:", no_usable)
        self.assertIn("tool_calls: clean/bound is unproven", no_usable)
        self.assertIn("client_bound: clean/bound needs claude", no_usable)

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
        # FT: r-mixed itself now survives under the default card/overlay
        # (its nosy/big leg is usable) -- r-retired is still an actual
        # route-level removal, so it is what exercises "an override never
        # resurrects a filtered route" here.
        survivors, removed = self.run_filters(overlay={})
        route, reason = r.apply_override({"override": {"route": "r-retired"}},
                                         survivors, removed)
        self.assertIsNone(route)
        self.assertTrue(reason.startswith("override r-retired removed by filters: "))

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
        # FT: r-flaky has only the one leg, so an unusable leg is the whole
        # route's only leg -- it still shows up removed, wrapped in the new
        # "no usable leg" aggregate reason rather than as its own list item.
        survivors, removed = self.filt({})
        self.assertNotIn("r-flaky", survivors)
        self.assertEqual(len(removed["r-flaky"]), 1)
        self.assertIn("tool_calls: clean/flaky is unproven", removed["r-flaky"][0])

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
        self.assertEqual(len(removed["r-solid"]), 1)
        self.assertIn("tool_calls: clean/solid is broken", removed["r-solid"][0])

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


class FallThroughTests(unittest.TestCase):
    """FT (2026-09-26 operator decision, replacing the earlier "every leg
    strict" answer): "a leg that is rate-limited or unproven makes the combo
    fall through to the next proven leg; it does not block the route. A
    route is blocked only when NO leg is available." OmniRoute's combos use
    strategy "priority" -- legs are tried in order and an error falls
    through to the next -- so the resolver's own per-leg filters must not be
    stricter than that and block a whole route over one bad leg.
    """

    def two_leg_registry(self, leg_a=None, leg_b=None, route_class="cheap"):
        """A route "r-ft" with two legs, "p/model-a" and "p/model-b", every
        key ``plan()``'s scoring path reads present with a harmless default
        (proven, ample context, no client_bound); `leg_a`/`leg_b` override
        just the fields a test cares about.
        """
        model_a = {"id": "model-a", "family": "a", "reasoning": False,
                  "effort_ladder": [], "tool_calls": "proven",
                  "price_in": 1e-6, "price_out": 2e-6, "output_max": 32768,
                  "context_usable": {"tokens": 100000, "source": "default"}}
        model_b = {"id": "model-b", "family": "b", "reasoning": False,
                  "effort_ladder": [], "tool_calls": "proven",
                  "price_in": 2e-6, "price_out": 4e-6, "output_max": 32768,
                  "context_usable": {"tokens": 100000, "source": "default"}}
        model_a.update(leg_a or {})
        model_b.update(leg_b or {})
        return {
            "providers": {"p": {"id": "p", "tier": "paid", "trains_on_prompts": False}},
            "models": {
                "model-a": model_a, "model-b": model_b,
                "orch": {"id": "orch", "price_in": 5e-6, "price_out": 1e-5,
                        "context_usable": {"tokens": 200000,
                                           "source": "default"}},
            },
            "routes": {
                "r-ft": {"id": "r-ft", "class": route_class,
                        "legs": ["p/model-a", "p/model-b"]},
            },
            "policy": {
                "modes": {"balanced": {"theta": {"value": 0.6},
                                       "lambda": {"value": 0.01}}},
                "verify_tokens": {"S0": {"tokens": 100}},
                "latency_seed": {route_class: {"minutes": 5}},
                "seed_priors": {route_class: {"S0": {"alpha": 8, "beta": 2}}},
            },
        }

    def card(self, **overrides):
        base = {"kind": "implement", "spec": "exact", "risk": "normal",
               "mode": "balanced", "privacy": "public"}
        base.update(overrides)
        return base

    def features(self, **overrides):
        base = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
               "tests": True, "need_tokens": 10}
        base.update(overrides)
        return base

    def state(self, installed=True, signed_in=True):
        return {"opencode": {"installed": installed, "signed_in": signed_in,
                             "reason": ""}}

    def now(self):
        return datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

    # --- an unproven/too-small/bound first leg falls through --------------

    def test_unproven_first_leg_falls_through_to_proven_second_leg(self):
        registry = self.two_leg_registry(leg_a={"tool_calls": "unproven"})
        card, features, client_state = self.card(), self.features(), self.state()

        survivors, removed = r.filter_routes(card, features, client_state,
                                             registry, {})
        self.assertEqual(survivors, ["r-ft"])
        self.assertEqual(removed, {})

        result = r.plan(card, features, client_state, registry, {}, [],
                        "orch", self.now())
        self.assertEqual(result["route"], "r-ft")
        self.assertEqual(result["leg"], "p/model-b")
        self.assertEqual(result["skipped_legs"],
                         {"p/model-a": ["tool_calls: p/model-a is unproven"]})
        self.assertIn("falls through 1 skipped leg(s)", result["reason"])

    def test_too_small_context_first_leg_falls_through_to_bigger_second_leg(self):
        registry = self.two_leg_registry(
            leg_a={"context_usable": {"tokens": 5, "source": "default"}})
        card = self.card()
        features = self.features(need_tokens=1000)  # x1.3 = 1300 > 5, <= 100000
        client_state = self.state()

        survivors, _ = r.filter_routes(card, features, client_state, registry, {})
        self.assertEqual(survivors, ["r-ft"])

        result = r.plan(card, features, client_state, registry, {}, [],
                        "orch", self.now())
        self.assertEqual(result["leg"], "p/model-b")
        self.assertIn("p/model-a", result["skipped_legs"])
        self.assertIn("context: need 1000x1.3 > usable 5 on p/model-a",
                     result["skipped_legs"]["p/model-a"])
        self.assertIn("falls through 1 skipped leg(s)", result["reason"])

    def test_client_bound_leg_is_skipped_even_for_its_own_client(self):
        # A route is served through the gateway (omniroute/<route>), never from
        # inside the bound client: run 20260926-142228 got 403 "OpenCode's free
        # tier can only be used from within OpenCode" on client=opencode.
        registry = self.two_leg_registry(leg_a={"client_bound": "opencode"})
        legs, skipped, _ = r.usable_legs(registry["routes"]["r-ft"], self.card(),
                                      self.features(), self.state(), registry, {},
                                      client="opencode")
        self.assertEqual(legs, [("p", "model-b")])
        self.assertEqual(skipped,
                         {"p/model-a": ["client_bound: p/model-a needs opencode"]})

    def test_client_bound_leg_skipped_falls_through_to_open_leg(self):
        registry = self.two_leg_registry(leg_a={"client_bound": "claude"})
        card, features, client_state = self.card(), self.features(), self.state()

        survivors, _ = r.filter_routes(card, features, client_state, registry, {})
        self.assertEqual(survivors, ["r-ft"])

        legs, skipped, _ = r.usable_legs(registry["routes"]["r-ft"], card,
                                      features, client_state, registry, {})
        self.assertEqual(legs, [("p", "model-b")])
        self.assertEqual(skipped,
                         {"p/model-a": ["client_bound: p/model-a needs claude"]})

    # --- all legs unusable: the route IS blocked ---------------------------

    def test_all_legs_unproven_removed_with_no_usable_leg(self):
        registry = self.two_leg_registry(leg_a={"tool_calls": "unproven"},
                                         leg_b={"tool_calls": "unproven"})
        card, features, client_state = self.card(), self.features(), self.state()

        survivors, removed = r.filter_routes(card, features, client_state,
                                             registry, {})
        self.assertEqual(survivors, [])
        reason = removed["r-ft"][0]
        self.assertTrue(reason.startswith("no usable leg: "))
        self.assertIn("p/model-a: tool_calls: p/model-a is unproven", reason)
        self.assertIn("p/model-b: tool_calls: p/model-b is unproven", reason)

    # --- privacy stays route-level, even with an otherwise-usable leg -----

    def test_sensitive_card_removed_even_with_a_usable_leg(self):
        registry = self.two_leg_registry()
        registry["providers"]["p"]["trains_on_prompts"] = True
        card = self.card(privacy="sensitive")
        features, client_state = self.features(), self.state()

        survivors, removed = r.filter_routes(card, features, client_state,
                                             registry, {})
        self.assertNotIn("r-ft", survivors)
        self.assertEqual(removed["r-ft"], [
            "privacy: p/model-a trains on prompts",
            "privacy: p/model-b trains on prompts",
        ])

    # --- the overlay rate limit: unproven skipped, proven kept -------------

    def test_rate_limited_unproven_leg_skipped_but_proven_leg_kept(self):
        trials_429 = [{"status": 429, "single": "error", "round": "skipped",
                      "note": "rate limited"}]
        registry = self.two_leg_registry(leg_a={"tool_calls": "unproven"})
        card, features, client_state = self.card(), self.features(), self.state()
        overlay = {"legs": {
            "p/model-a": {"tool_calls_last_error": {"trials": trials_429}},
            "p/model-b": {"tool_calls_last_error": {"trials": trials_429}},
        }}

        legs, skipped, _ = r.usable_legs(registry["routes"]["r-ft"], card,
                                      features, client_state, registry, overlay)
        # model-a: unproven AND rate-limited -- skipped, both reasons kept.
        self.assertEqual(legs, [("p", "model-b")])
        self.assertIn("rate_limited: p/model-a (429)", skipped["p/model-a"])
        self.assertIn("tool_calls: p/model-a is unproven", skipped["p/model-a"])
        # model-b: proven, so the *same* all-429 last error does not skip it
        # -- OmniRoute itself falls through past a 429 at run time.
        self.assertNotIn("p/model-b", skipped)

    # --- the real catalog ---------------------------------------------

    def test_real_registry_overlay_proven_leg_survives_for_implement(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        card = {"kind": "implement", "privacy": "public"}
        features = {"need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        # Every model in the real catalog is tool_calls unproven today (no
        # probe has proven one yet); marking the -clean routes' head leg
        # proven in the overlay is enough to keep t2-worker-clean alive, even
        # though its other legs (openrouter/deepseek, gated by its provider,
        # and opencode-zen/deepseek-v4.1-flash, gated on the route) stay
        # unproven. MISTRALFIX 2026-09-28: this used to prove
        # mistral/mistral-small-latest, which is no longer a leg of the route.
        overlay = {"legs": {clean_head_leg(registry): {
            "tool_calls": {"value": "proven", "source": "test"}}}}
        survivors, removed = r.filter_routes(card, features, client_state,
                                             registry, overlay)
        self.assertIn("t2-worker-clean", survivors)
        self.assertNotIn("t2-worker-clean", removed)


class ProviderLimitsFilterTests(unittest.TestCase):
    """The per-leg tpm filter (brief R4, 2026-09-27): a leg whose provider
    limits for that model carry `tpm` is skipped when need_tokens * 1.3 > tpm,
    counted as a skipped leg exactly like the context filter. No clock, no
    counters: rpm/rpd/tpd are data only for now.

    A small inline registry with a groq leg carrying tpm 8000 (the operator's
    measured Groq free-tier cap); no policy.leg_rules here, so the deny-groq
    rule never gates it -- the test isolates the tpm filter.
    """

    def setUp(self):
        self.registry = {
            "providers": {
                "groq": {"id": "groq", "tier": "free", "trains_on_prompts": False,
                         "limits": {
                             "openai/gpt-oss-120b": {
                                 "rpm": 30, "rpd": 1000, "tpm": 8000,
                                 "tpd": 200000,
                                 "source": "operator Groq console screenshot 2026-09-27"}}},
                "clean": {"id": "clean", "tier": "paid", "trains_on_prompts": False},
            },
            "models": {
                "openai/gpt-oss-120b": {
                    "id": "openai/gpt-oss-120b", "tool_calls": "proven",
                    "context_usable": {"tokens": 131072, "source": "default"}},
                "big": {"id": "big", "tool_calls": "proven",
                        "context_usable": {"tokens": 100000, "source": "default"}},
            },
            "routes": {
                "r-groq": {"id": "r-groq",
                           "legs": ["groq/openai/gpt-oss-120b", "clean/big"]},
            },
        }

    def card(self, kind="review", privacy="public"):
        return {"kind": kind, "privacy": privacy}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}

    def test_a_2k_token_need_keeps_a_groq_leg(self):
        # 2000 * 1.3 = 2600 <= tpm 8000: the groq leg survives.
        legs, skipped, _ = r.usable_legs(
            self.registry["routes"]["r-groq"], self.card(),
            {"need_tokens": 2000}, self.state(), self.registry, {})
        self.assertIn(("groq", "openai/gpt-oss-120b"), legs)
        self.assertNotIn("groq/openai/gpt-oss-120b", skipped)

    def test_a_9k_need_skips_the_groq_leg_with_the_reason(self):
        # 9000 * 1.3 = 11700 > tpm 8000: the groq leg is skipped, named as a
        # limit reason, and the clean/big fallback leg still serves.
        legs, skipped, _ = r.usable_legs(
            self.registry["routes"]["r-groq"], self.card(),
            {"need_tokens": 9000}, self.state(), self.registry, {})
        self.assertNotIn(("groq", "openai/gpt-oss-120b"), legs)
        self.assertIn("groq/openai/gpt-oss-120b", skipped)
        self.assertTrue(
            any("limit: groq/openai/gpt-oss-120b tpm 8000 < need 9000" in rsn
                for rsn in skipped["groq/openai/gpt-oss-120b"]),
            skipped["groq/openai/gpt-oss-120b"])

    def test_a_leg_without_limits_is_unaffected(self):
        # clean/big has no limits entry: a 9k need skips the groq leg but keeps
        # clean/big (context 100000 * 1.3 covers it).
        legs, skipped, _ = r.usable_legs(
            self.registry["routes"]["r-groq"], self.card(),
            {"need_tokens": 9000}, self.state(), self.registry, {})
        self.assertIn(("clean", "big"), legs)
        self.assertNotIn("clean/big", skipped)

    def test_tpm_boundary_equal_keeps_the_leg(self):
        """need * 1.3 == tpm keeps the leg (review R4FIX): the filter skips only
        on strictly greater, so the boundary leg survives and one token over
        drops it."""
        reg = self.registry
        reg["providers"]["groq"]["limits"]["openai/gpt-oss-120b"]["tpm"] = 1300
        legs, skipped, _ = r.usable_legs(
            reg["routes"]["r-groq"], self.card(),
            {"need_tokens": 1000}, self.state(), reg, {})
        self.assertIn(("groq", "openai/gpt-oss-120b"), legs)
        self.assertNotIn("groq/openai/gpt-oss-120b", skipped)

        legs, skipped, _ = r.usable_legs(
            reg["routes"]["r-groq"], self.card(),
            {"need_tokens": 1001}, self.state(), reg, {})
        self.assertNotIn(("groq", "openai/gpt-oss-120b"), legs)
        self.assertIn("groq/openai/gpt-oss-120b", skipped)

    def test_a_route_dropped_by_tpm_is_input_required_naming_tpm(self):
        """Review R4FIX: when the tpm filter drops every leg of a route, the
        route comes back with a 'no usable leg' reason naming the tpm limit and
        no_route reports input_required."""
        reg = self.registry
        reg["routes"]["r-groq"]["legs"] = ["groq/openai/gpt-oss-120b"]
        survivors, removed = r.filter_routes(
            self.card(), {"need_tokens": 9000}, self.state(), reg, {})
        self.assertNotIn("r-groq", survivors)
        self.assertIn("r-groq", removed)
        joined = " ".join(removed["r-groq"])
        self.assertIn("no usable leg", joined)
        self.assertIn("tpm", joined)
        self.assertEqual(r.no_route(removed)["state"], "input_required")


class LegRulesFilterTests(unittest.TestCase):
    """The per-leg policy.leg_rules filter (brief OR1f, 2026-09-27).

    The gateway renders only ``registry.gateway_legs``, which drops every leg
    ``policy.leg_rules`` denies; the resolver's ``usable_legs`` must drop the
    same legs, or it plans a combo (e.g. ``groq/openai/gpt-oss-120b``) the
    gateway never serves. A denied leg is skipped with a reason naming the
    rule, and a route whose only leg is denied comes back as the resolver's
    existing no-usable-leg outcome. No clock, no availability here: the inline
    registry isolates the deny rule from the other per-leg filters.
    """

    def registry(self):
        return {
            "providers": {
                "groq": {"id": "groq", "tier": "free",
                         "trains_on_prompts": False},
                "clean": {"id": "clean", "tier": "paid",
                          "trains_on_prompts": False},
            },
            "models": {
                "openai/gpt-oss-120b": {
                    "id": "openai/gpt-oss-120b", "tool_calls": "proven",
                    "context_usable": {"tokens": 131072, "source": "default"}},
                "big": {"id": "big", "tool_calls": "proven",
                        "context_usable": {"tokens": 100000,
                                           "source": "default"}},
                "small": {"id": "small", "tool_calls": "proven",
                          "context_usable": {"tokens": 100000,
                                             "source": "default"}},
            },
            "routes": {
                "r-rules": {"id": "r-rules",
                            "legs": ["groq/openai/gpt-oss-120b", "clean/big"]},
                "r-only-denied": {"id": "r-only-denied",
                                  "legs": ["groq/openai/gpt-oss-120b"]},
                "r-order": {"id": "r-order",
                            "legs": ["clean/big", "groq/openai/gpt-oss-120b",
                                     "clean/small"]},
            },
            "policy": {
                "leg_rules": [
                    {"id": "deny-groq", "match": "groq/*", "allow": False,
                     "reason": "test: groq denied"},
                    {"id": "allow-clean", "match": "clean/*", "allow": True,
                     "reason": "test: clean allowed"},
                ],
            },
        }

    def card(self):
        return {"kind": "review", "privacy": "public"}

    def features(self):
        return {"need_tokens": 1000}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}

    def test_denied_leg_skipped_with_reason_naming_the_rule(self):
        registry = self.registry()
        legs, skipped, _ = r.usable_legs(registry["routes"]["r-rules"],
                                       self.card(), self.features(),
                                       self.state(), registry, {})
        self.assertEqual(legs, [("clean", "big")])
        self.assertEqual(
            skipped["groq/openai/gpt-oss-120b"],
            ["leg_rules: groq/openai/gpt-oss-120b denied by deny-groq"])

    def test_route_whose_only_leg_is_denied_is_input_required(self):
        registry = self.registry()
        survivors, removed = r.filter_routes(self.card(), self.features(),
                                             self.state(), registry, {})
        self.assertNotIn("r-only-denied", survivors)
        joined = " ".join(removed["r-only-denied"])
        self.assertIn("no usable leg", joined)
        self.assertIn("leg_rules: groq/openai/gpt-oss-120b denied by deny-groq",
                      joined)
        self.assertEqual(r.no_route(removed)["state"], "input_required")

    def test_an_allowed_leg_next_to_a_denied_one_is_kept_in_order(self):
        registry = self.registry()
        legs, skipped, _ = r.usable_legs(registry["routes"]["r-order"],
                                       self.card(), self.features(),
                                       self.state(), registry, {})
        # Allowed legs keep route order; the denied leg in the middle is the
        # only one skipped.
        self.assertEqual(legs, [("clean", "big"), ("clean", "small")])
        self.assertEqual(sorted(skipped), ["groq/openai/gpt-oss-120b"])

    def test_real_registry_usable_legs_are_gateway_servable(self):
        """Every leg ``usable_legs`` keeps is also served by
        ``registry.gateway_legs`` for the same route: the resolver must plan
        only legs a gateway combo serves. Client-bound legs are dropped by both
        (the gateway 403s them; the resolver's client_bound filter skips them).
        Legs are compared in resolved ``(provider_id, model_id)`` form because
        gateway_legs returns the raw leg strings, whose provider half can be an
        alias (``cheapinference/...`` resolves to provider
        ``cheaperinference``)."""
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        card = {"kind": "review", "privacy": "public"}
        features = {"need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        for route_id, route in registry["routes"].items():
            if not (route.get("legs") or []):
                continue  # a legless route renders no combo at all
            gateway = set()
            for leg in registry_tool.gateway_legs(route, registry):
                if isinstance(leg, str):
                    gateway.add(registry_tool.resolve_leg(leg, registry))
            kept, _, _ = r.usable_legs(route, card, features, client_state,
                                       registry, {})
            for leg in kept:
                self.assertIn(leg, gateway,
                              "%s: usable leg %s is not in gateway_legs"
                              % (route_id, leg))


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
            "explain", "skipped_legs", "re_probe_notes", "review",
        })
        # Every model in this fixture is tool_calls "proven" with plenty of
        # context: nothing is skipped, so FT's skipped_legs is empty here.
        self.assertEqual(result["skipped_legs"], {})
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

    # --- the operator-pinned effort rung (spec 4: route/client/effort is
    #     "pinned by the operator (optional; logged)") ----------------------
    # DSBACK item 3 measured the pin being parsed and validated by
    # normalize_v2 and then dropped: only override.route reached apply_override,
    # so a card pinning effort=max silently ran the bucket's rung.

    def test_a_pinned_effort_rung_replaces_the_buckets_one(self):
        # S0/implement on the frontier leg scores "low"; the pin says "max".
        unpinned = self.s0_plan(override={"route": "r-frontier"})
        self.assertEqual(unpinned["effort"], "low")
        pinned = self.s0_plan(override={"route": "r-frontier", "effort": "max"})
        self.assertEqual(pinned["effort"], "max")
        # and the pin is what the output cap follows, not the bucket
        self.assertEqual(pinned["max_tokens"], 64000)
        self.assertNotEqual(unpinned["max_tokens"], pinned["max_tokens"])

    def test_a_pinned_rung_is_clamped_to_the_legs_ladder_never_invented(self):
        # r-mid's ladder tops out at xhigh: pinning max asks for a rung the
        # answering leg does not have.
        result = self.s0_plan(override={"route": "r-mid", "effort": "max"})
        self.assertEqual(result["effort"], "xhigh")

    def test_a_pinned_rung_cannot_make_a_non_reasoning_leg_reason(self):
        # free-model has an empty ladder and reasoning False: effort() returns
        # None for it, and a pin must not fabricate a rung out of nothing.
        result = self.s0_plan(override={"route": "r-free", "effort": "high"})
        self.assertIsNone(result["effort"])
        self.assertIsNone(result["max_tokens"])

    def test_a_pinned_none_rung_is_a_rung_not_an_absence(self):
        # "none" is a real rung on these ladders: pinning it must land on
        # "none" (the client then sends no reasoning param at all) rather than
        # falling back to the bucket's rung.
        result = self.s0_plan(override={"route": "r-frontier", "effort": "none"})
        self.assertEqual(result["effort"], "none")

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

    # FUP (2026-09-27): re-probe notes must not inflate "falls through"
    # count -- a leg whose provider's unavailable_until just passed is
    # usable again, and the informational note is separate from skipped.

    def test_re_probe_note_does_not_say_falls_through(self):
        reg = self.registry()
        reg["providers"]["free-p"]["available"] = False
        reg["providers"]["free-p"]["unavailable_until"] = "2026-10-01T09:05:00Z"
        now = self.dt(2026, 10, 1, 9, 5)
        result = r.plan(self.card(), self.features(),
                        self.state(), reg, {}, [],
                        "orch", now=now)
        self.assertIsNotNone(result["route"], "a self-healed route must be picked")
        self.assertEqual(result["skipped_legs"], {},
                         "a leg whose only note is re-probe must not be in skipped")
        notes = result.get("re_probe_notes", {}).get("free-p/free-model", [])
        self.assertTrue(notes and "re-probe" in notes[0],
                        "%s: %s" % (result.get("route", "?"), notes))
        self.assertNotIn("falls through", result["reason"],
                         "re-probe notes must not inflate the falls-through count")

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

    def test_review_counts_come_from_the_policy_when_the_registry_declares_them(self):
        # RISKTIER-a: what a class costs is registry data, not a constant here.
        registry = self.registry()
        registry["policy"]["review_counts"] = {
            "normal": {"cross_family": 2, "final": False, "source": "test"},
            "high": {"cross_family": 1, "final": True, "source": "test"},
        }
        plan = r.plan(self.card(), self.features(), self.state(), registry,
                      {}, [], "orch", self.now())
        # r-free (alpha) chosen: the two cheapest other families are r-cheap
        # (beta) and r-mid (gamma); r-frontier is alpha again, so it stays out.
        self.assertEqual(plan["reviewers"]["routes"], ["r-cheap", "r-mid"])
        self.assertIsNone(plan["reviewers"]["closer"])

        high = r.plan(self.card(risk="high"), self.features(), self.state(),
                      registry, {}, [], "orch", self.now())
        self.assertEqual(high["reviewers"]["routes"], ["r-cheap"])
        self.assertEqual(high["reviewers"]["closer"],
                         {"client": "claude", "model": "sonnet"})

    def test_review_counts_declaring_no_final_leaves_the_closer_out(self):
        registry = self.registry()
        registry["policy"]["review_counts"] = {
            "high": {"cross_family": 2, "final": False, "source": "test"},
        }
        high = r.plan(self.card(risk="high"), self.features(), self.state(),
                      registry, {}, [], "orch", self.now())
        self.assertEqual(high["reviewers"]["closer"], None)

    def test_a_policy_asking_for_no_cross_family_reviewer_gets_none(self):
        # zero means zero. The pick loop breaks on the cap BEFORE appending, so
        # a 0-count policy routes no reviewer at all; an append-then-check loop
        # (what the hard-coded table made harmless) would always return one.
        registry = self.registry()
        registry["policy"]["review_counts"] = {
            "normal": {"cross_family": 0, "final": False, "source": "test"},
        }
        plan = r.plan(self.card(), self.features(), self.state(), registry,
                      {}, [], "orch", self.now())
        self.assertEqual(plan["reviewers"]["routes"], [])
        self.assertIsNone(plan["reviewers"]["closer"])
        self.assertIn("cross-family reviewer(s):", plan["reviewers"]["reason"])

    def test_an_absent_review_counts_key_falls_back_to_d2s_numbers(self):
        # The constants stay as the fallback, so a registry that predates
        # policy.review_counts routes exactly as it did before the field.
        registry = self.registry()
        registry["policy"].pop("review_counts", None)
        normal = r.plan(self.card(), self.features(), self.state(), registry,
                        {}, [], "orch", self.now())
        self.assertEqual(normal["reviewers"]["routes"], ["r-cheap"])
        self.assertIsNone(normal["reviewers"]["closer"])

        high = r.plan(self.card(risk="high"), self.features(), self.state(),
                      registry, {}, [], "orch", self.now())
        self.assertEqual(len(high["reviewers"]["routes"]), 2)
        self.assertEqual(high["reviewers"]["closer"],
                         {"client": "claude", "model": "sonnet"})

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
        # Review is not an agentic kind, so the strict tool_calls filter
        # does not apply here (agentic kinds do have surviving routes today:
        # glm-5.2 and MiniMax-M3 are model-level proven).
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

    @staticmethod
    def _inline_toolcalls_overlay(registry):
        """Build a tool_calls overlay in-process (PRIV2, 2026-09-26): the
        sensitive-routing regression below needs an agentic kind's
        tool_calls proven for at least one private-safe leg, but
        the real probe's overlay (tools/autoos_overlay.py) is never in
        git and absent on a fresh clone or in CI. Marks the -clean routes'
        head leg (clean_head_leg: private-safe — paid tier,
        trains_on_prompts false, no model-level override) proven and every
        other leg in the registry explicitly unproven -- same shape
        tools/probe-toolcalls.py writes (``overlay["legs"][leg]["tool_calls"]
        ["value"]``) -- so this test never depends on that file."""
        overlay = {"legs": {}}
        for route in registry["routes"].values():
            for leg in route.get("legs") or []:
                overlay["legs"].setdefault(leg, {"tool_calls": {"value": "unproven"}})
        overlay["legs"][clean_head_leg(registry)] = {"tool_calls": {"value": "proven"}}
        return overlay

    def test_real_registry_sensitive_implement_card_never_picks_an_unsafe_leg(self):
        # CIGREEN: expectation moved by 35148c5c (CLEAN put the unproven
        # ovhcloud/gpt-oss-120b head on -clean) and ffe384a0 (credit spend
        # guard refuses it as unpriced). With only that head proven the plan
        # fail-closes instead of picking an unsafe leg -- pinned below by the
        # None route plus the named credit-unpriced reason -- and with a priced
        # private-safe leg proven the plan must route to it with every serving
        # leg private-safe. Either half fails if an unsafe leg were ever picked.
        # PRIV brief 2026-09-26, the found bug: `route --card
        # kind=implement,paths=...,privacy=sensitive` chose t3-driver-free-
        # only via groq/qwen/qwen3.8-27b, a free pool -- "Free first, private
        # never" was violated. PRIV2 (2026-09-26): this must run in CI, so
        # the tool_calls overlay is built inline (_inline_toolcalls_overlay)
        # instead of reading the machine-wide overlay --
        # see
        # test_real_registry_sensitive_implement_card_never_picks_an_unsafe_leg_with_measured_overlay
        # below for the real-probe-overlay variant, which may still skip.
        registry_path = (Path(__file__).resolve().parent.parent
                         / "catalog" / "ai-registry.json")
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        overlay = self._inline_toolcalls_overlay(registry)
        card = {"kind": "implement", "spec": "exact", "risk": "normal",
               "mode": "balanced", "privacy": "sensitive"}
        features = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                   "tests": True, "need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        starved = r.plan(card, features, client_state, registry, overlay, [],
                         "muse-spark", self.dt(2026, 9, 29, 9, 0))
        self.assertIsNone(starved["route"], starved)
        self.assertEqual(starved["state"], "input_required", starved)
        self.assertIn("credit leg unpriced gpt-oss-120b",
                      starved["reason"], starved["reason"])
        overlay["legs"]["ovhcloud/Qwen3.8-27B"] = {
            "tool_calls": {"value": "proven"}}
        result = r.plan(card, features, client_state, registry, overlay, [],
                        "muse-spark", self.dt(2026, 9, 29, 9, 0))
        self.assertIsNotNone(result["route"], result)
        route = registry["routes"][result["route"]]
        for provider_id, model_id in r.serving_legs(route, registry):
            safe, reason = registry_tool.private_safe(provider_id, model_id, registry)
            self.assertTrue(
                safe, "%s/%s on route %s is not private-safe: %s"
                     % (provider_id, model_id, result["route"], reason))
        # CIGREEN-REWORK1 (Sonnet seat; expectation moved by 35148c5c): the two
        # variants above prove only the clean/private-safe head, so the only
        # usable route is private-safe either way -- a plan with the privacy
        # route filter removed, or with private_safe forced True, still passes
        # them. This third variant proves EVERY registry leg, so a filter-less
        # plan falls onto a free pool while the correct plan must still route
        # to a route whose every serving leg is private-safe.
        all_proven = {"legs": {leg: {"tool_calls": {"value": "proven"}}
                               for leg in overlay["legs"]}}
        result = r.plan(card, features, client_state, registry, all_proven, [],
                        "muse-spark", self.dt(2026, 9, 29, 9, 0))
        self.assertIsNotNone(result["route"], result)
        route = registry["routes"][result["route"]]
        for provider_id, model_id in r.serving_legs(route, registry):
            safe, reason = registry_tool.private_safe(provider_id, model_id, registry)
            self.assertTrue(
                safe, "%s/%s on route %s is not private-safe: %s"
                     % (provider_id, model_id, result["route"], reason))

    def test_real_registry_sensitive_implement_card_never_picks_an_unsafe_leg_with_measured_overlay(self):
        # Extra (PRIV2): the same regression against the real probe's
        # overlay, when one happens to be on disk. The machine-wide overlay
        # (autoos_overlay, OVERLAYHOME) is never in git, so this skips on a
        # fresh clone or in CI rather than failing -- the inline-overlay test
        # above is the one that must run. Read-only: it never writes the file.
        import autoos_overlay
        found = autoos_overlay.found(
            autoos_overlay.default_path(),
            autoos_overlay.legacy_path(str(Path(__file__).resolve().parent.parent)))
        if found is None:
            self.skipTest("no tool_calls overlay on this machine to probe with")
        overlay_path = Path(found)
        registry_path = (Path(__file__).resolve().parent.parent
                         / "catalog" / "ai-registry.json")
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        card = {"kind": "implement", "spec": "exact", "risk": "normal",
               "mode": "balanced", "privacy": "sensitive"}
        features = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                   "tests": True, "need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        result = r.plan(card, features, client_state, registry, overlay, [],
                        "muse-spark", self.dt(2026, 9, 29, 9, 0))
        # What is under test is the RULE, not the state of the market. The
        # overlay is a snapshot of probes, so "a route exists" is a claim about
        # the day the file was written: with a real measured overlay on disk,
        # every private-safe route can legitimately be down, and the correct
        # answer to a sensitive card then is input_required. Asserting a non-None
        # route here made the suite machine-dependent — and pushed towards
        # keeping an unsafe route alive just to satisfy it.
        if result["route"] is None:
            self.assertEqual(result["state"], "input_required", result)
            # ... but only with a reason. A plan that gives up naming nothing is
            # a resolver hole, not a private-safe refusal.
            self.assertTrue(result.get("reason", "").strip(), result)
            return
        route = registry["routes"][result["route"]]
        for provider_id, model_id in r.serving_legs(route, registry):
            safe, reason = registry_tool.private_safe(provider_id, model_id, registry)
            self.assertTrue(
                safe, "%s/%s on route %s is not private-safe: %s"
                     % (provider_id, model_id, result["route"], reason))


class GatewayOrderTests(unittest.TestCase):
    """R-gateway-01 evolved order, twice-revised 2026-09-26 (briefs/common.md
    'Model mix' then 'Claude budget'): the 15:44Z/15:49Z Qwen-3.8-via-
    OpenRouter-credits directive was SUPERSEDED at 16:4xZ once the operator
    found OpenRouter has no shared credit (BYOK only, a probe there spends the
    operator's own key -> 402). Final, current order: free pools -> the
    "credits" tier (samba, cheaperinference; qoder/agy at the client level,
    never a registry leg) -> paid native DeepSeek (V4.1 Flash only - no
    v4-pro/v4-flash/V3.x anywhere, including samba/cheaperinference rows).
    `qoder`/`agy` client-level fallbacks have no registry leg at all (agy
    reaches the pre-existing `antigravity/...` legs, untouched here). The part
    this module can pin is leg ORDER within a route (OmniRoute's own priority
    strategy tries legs in this order) and which providers/models are
    actually referenced.
    """

    def _legs(self, registry, route_id):
        return registry["routes"][route_id]["legs"]

    def registry(self):
        path = (Path(__file__).resolve().parent.parent
               / "catalog" / "ai-registry.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_t3_driver_credit_legs_rank_behind_free_ahead_of_older_fallbacks(self):
        legs = self._legs(self.registry(), "t3-driver")
        free_leg = legs.index("groq/qwen/qwen3.8-27b")  # true free tier
        # cerebras/qwen-3.8-27b is qwen-3.8-27b's own PAID overflow leg (an
        # older, pre-existing fallback) - the new credit tier ranks ahead of it.
        older_fallback = legs.index("cerebras/qwen-3.8-27b")
        for credit_leg in ("samba/gpt-oss-120b", "cheaperinference/glm-5.2",
                          "cheaperinference/kimi-k3", "samba/MiniMax-M3",
                          "deepseek/deepseek-flash"):
            with self.subTest(leg=credit_leg):
                idx = legs.index(credit_leg)
                self.assertGreater(idx, free_leg,
                                   "%s must rank behind the free groq leg" % credit_leg)
                self.assertLess(idx, older_fallback,
                               "%s must rank ahead of the older cerebras "
                               "paid-overflow fallback" % credit_leg)

    def test_openrouter_qwen_legs_are_unreferenced_by_any_route(self):
        # 16:4xZ revision: OpenRouter has no shared credit (BYOK only) - the
        # four openrouter/qwen legs added for the withdrawn 15:44Z SPEED
        # directive keep their model entries (operator instruction) but are
        # "unavailable and in no route order". FREEWIRE 2026-09-30: the $0
        # ':free' qwen leg is now referenced (the operator's free-provider
        # wiring); every PAID openrouter/qwen leg stays unreferenced.
        registry = self.registry()
        for mid in ("qwen/qwen3.8-flash", "qwen/qwen3-coder-flash",
                   "qwen/qwen3.8-max-0902"):
            self.assertIn(mid, registry["models"])
        seen_free = False
        for route in registry["routes"].values():
            for leg in route.get("legs") or []:
                if not leg.startswith("openrouter/qwen/"):
                    continue
                self.assertTrue(leg.endswith(":free"), leg)
                seen_free = True
        self.assertTrue(seen_free, "the :free openrouter qwen leg should be wired")

    def test_only_deepseek_v41_flash_survives_of_the_deepseek_family(self):
        # 16:4xZ revision: "DeepSeek = ONLY V4.1 Flash ... no v4-pro, v4-flash,
        # V3.x anywhere (incl. samba/cheaperinference DeepSeek rows)". Every
        # leg naming a non-V4.1 DeepSeek model is either absent or flagged
        # unavailable; deepseek/deepseek-flash (native) and
        # openrouter/deepseek/deepseek-v4.1-flash / opencode-zen/deepseek-
        # v4.1-flash (the two BYOK/zen V4.1 paths) are the only ones left
        # servable.
        registry = self.registry()
        rejected_models = ("DeepSeek-V3.2", "deepseek/deepseek-v4-pro")
        for mid in rejected_models:
            self.assertNotIn(mid, registry["models"])
        # DSAMEND2 (review 2): the third entry used to be the *leg* string
        # "deepseek/deepseek-v4-flash", and `models` is keyed by model id, not by
        # leg, so that limb could never fail. What DSAMEND actually left of that
        # snapshot is a reseller row with no native `direct` block — assert that.
        self.assertNotIn("direct", registry["models"].get("deepseek-v4-flash") or {})
        self.assertIn("deepseek-v4-flash", registry["models"],
                      "the reseller row keeps its model entry, only not a native one")
        for route_id, route in registry["routes"].items():
            for leg in route.get("legs") or []:
                if leg == "cheaperinference/deepseek-v4-flash":
                    unavailable = route.get("unavailable_legs") or {}
                    with self.subTest(route=route_id, leg=leg):
                        self.assertIn(leg, unavailable)
                        self.assertIs(unavailable[leg].get("available"), False)
                elif "deepseek" in leg and leg not in (
                    "deepseek/deepseek-flash",
                    "openrouter/deepseek/deepseek-v4.1-flash",
                    "opencode-zen/deepseek-v4.1-flash",
                ):
                    self.fail("unexpected non-V4.1 DeepSeek leg %s in %s"
                             % (leg, route_id))

    def test_no_claude_or_gpt_leg_through_cheaperinference(self):
        # "Claude budget" 2026-09-26 16:2xZ/16:4xZ: "NEVER Claude or GPT
        # models through paid APIs (no cheaperinference/claude-*, no
        # cheaperinference/gpt-*): too expensive" - "DROP the review combo on
        # cheaperinference/claude-sonnet-5".
        #
        # AUTHORS (S1) 2026-09-28 rewrote the two models-table limbs as the leg
        # test they were standing in for. "claude-sonnet-5 is not in models" was
        # never the rule; "nothing routes to it" is, and registering the
        # orchestrator's own model as an AUTHOR id -- so `review-status` can read
        # a record the Sonnet final wrote instead of refusing it -- says nothing
        # about legs. An author row carries no provider and no route names it;
        # what keeps Claude and GPT off a paid API is policy.leg_rules
        # deny-claude-paid-api / deny-gpt-paid-api, checked by registry rule 9.
        registry = self.registry()
        legs = {leg for route in registry["routes"].values()
                for leg in (route.get("legs") or [])
                + list(route.get("unavailable_legs") or {})}
        for mid in ("claude-sonnet-5", "gpt-5.6-terra"):
            self.assertEqual([leg for leg in sorted(legs)
                              if leg.split("/", 1)[1:2] == [mid]], [],
                             "%s is an author id, never a routable leg" % mid)
        for route in registry["routes"].values():
            for leg in route.get("legs") or []:
                self.assertFalse(leg.startswith("cheaperinference/claude"), leg)
                self.assertFalse(leg.startswith("cheaperinference/gpt"), leg)

    def test_credit_legs_are_opencode_addressable_pinned_routes(self):
        # "still wanted" (16:4xZ): declared through the registry render into
        # the opencode provider list, so `--model omniroute/<leg>` resolves -
        # see tools/registry.py IDE_MODEL_ORDER and this lane's REPORT for the
        # `--dry-run` proof.
        registry = self.registry()
        for route_id in ("cheaperinference/kimi-k3", "cheaperinference/glm-5.2",
                        "samba/gpt-oss-120b", "samba/MiniMax-M3"):
            self.assertEqual(registry["routes"][route_id]["legs"], [route_id])

    def test_no_qoder_leg_exists_in_any_route(self):
        # qoder is a client-level fallback (R-spawn-09), never resolvable as
        # a registry leg - confirms the "ahead of qoder" half of the order is
        # not (and cannot be) expressed as route leg data.
        registry = self.registry()
        for route in registry["routes"].values():
            for leg in route.get("legs") or []:
                self.assertFalse(leg.startswith("qoder/"), leg)

    def test_agentic_card_never_resolves_to_a_leg_without_proven_tool_calls(self):
        # CIGREEN: expectation moved by ffe384a0 (credit spend guard: the only
        # overlay-proven clean head, ovhcloud/gpt-oss-120b, is credit-unpriced
        # and refused, so proving just it fail-closes to None) and 35148c5c
        # (CLEAN put that ovh head on -clean). Prove the priced private-safe
        # leg ovhcloud/Qwen3.8-27B instead: the plan must then land on it, never
        # on an unproven leg.
        # Brief item 5: "an agentic card never picks a leg without
        # tool_calls." New legs default to tool_calls: unproven (D20 - no
        # value enters without evidence) until promoted from a probe
        # verdict; this proves the pre-existing per-leg filter
        # (tools/autoos_resolver.py usable_legs) still holds it for an
        # implement (agentic) card over the now-larger real registry, using
        # the same inline overlay shape PlanTests._inline_toolcalls_overlay
        # builds (every other real leg explicitly unproven).
        registry = self.registry()
        overlay = PlanTests._inline_toolcalls_overlay(registry)
        overlay["legs"]["ovhcloud/Qwen3.8-27B"] = {
            "tool_calls": {"value": "proven"}}
        card = {"kind": "implement", "spec": "exact", "risk": "normal",
               "mode": "balanced", "privacy": "public"}
        features = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                   "tests": True, "need_tokens": 1000}
        client_state = {"opencode": {"installed": True, "signed_in": True,
                                     "reason": ""}}
        result = r.plan(card, features, client_state, registry, overlay, [],
                        "muse-spark", datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc))
        self.assertIsNotNone(result["route"], result)
        # result["leg"] is the route's first *usable* leg (per-leg tool_calls
        # filter already applied by usable_legs/score_route): the priced
        # private-safe leg is the only overlay-proven, credit-priced leg, so
        # an agentic (implement) card must land on exactly it.
        self.assertEqual(result["leg"], "ovhcloud/Qwen3.8-27B", result)
        provider_id, model_id = registry_tool.resolve_leg(
            result["leg"], registry)
        effective = (overlay["legs"].get(result["leg"], {})
                     .get("tool_calls", {}).get("value")
                     or registry["models"][model_id].get("tool_calls"))
        self.assertEqual(effective, "proven", result["leg"])


class DecomposeTests(unittest.TestCase):
    """decompose(): spec 5.3 step 3 / D7 -- S3/S4 only, one level deep.

    A tiny inline registry (just the two things decompose() reads: the
    orchestrator's price_in and policy.brief_tokens per bucket) keeps every
    number hand-computable. `whole_plan` and `subtask_plans` are plain dicts
    carrying only the keys decompose() reads (route, expected_cost, reason) --
    exactly what plan()'s output shape provides.
    """

    def registry(self):
        return {
            "models": {"orch": {"price_in": 5e-6}},
            "policy": {
                "brief_tokens": {
                    "S3": {"tokens": 2000, "source": "default"},
                    "S4": {"tokens": 3000, "source": "default"},
                },
            },
        }

    def subtask(self, route="r1", expected_cost=0.02, reason="ok"):
        return {"route": route, "expected_cost": expected_cost, "reason": reason}

    # --- bucket filter: S3/S4 only -----------------------------------------

    def test_s2_never_decomposes(self):
        result = r.decompose({"route": "whole", "expected_cost": 1.0}, [],
                             "S2", self.registry(), "orch")
        self.assertEqual(result, {
            "split": False,
            "reason": "bucket S2: no decompose (S3/S4 only)",
        })

    def test_s0_and_s1_never_decompose_either(self):
        for bucket_name in ("S0", "S1"):
            with self.subTest(bucket=bucket_name):
                result = r.decompose({"route": "whole", "expected_cost": 1.0},
                                     [], bucket_name, self.registry(), "orch")
                self.assertFalse(result["split"])
                self.assertEqual(
                    result["reason"],
                    "bucket %s: no decompose (S3/S4 only)" % bucket_name)

    # --- zero subtask_plans: never split into nothing (review-b5a4) ---------

    def test_s3_with_no_subtasks_never_splits(self):
        whole = {"route": "whole", "expected_cost": 1.0}
        result = r.decompose(whole, [], "S3", self.registry(), "orch")
        self.assertEqual(result, {
            "split": False,
            "reason": "no subtasks proposed",
        })

    def test_s4_with_no_subtasks_never_splits(self):
        whole = {"route": "whole", "expected_cost": 1.0}
        result = r.decompose(whole, [], "S4", self.registry(), "orch")
        self.assertEqual(result, {
            "split": False,
            "reason": "no subtasks proposed",
        })

    # --- S3: split vs keep whole --------------------------------------------

    def test_s3_splits_when_cheaper(self):
        # overhead = 2 * 2000 * 5e-6 = 0.02; subtasks_cost = 0.02 + 0.02 = 0.04
        # total = 0.06 < whole 0.5 -> split.
        subtasks = [self.subtask("r1", 0.02), self.subtask("r2", 0.02)]
        whole = {"route": "whole", "expected_cost": 0.5}
        result = r.decompose(whole, subtasks, "S3", self.registry(), "orch")
        self.assertTrue(result["split"])
        self.assertAlmostEqual(result["overhead"], 0.02)
        self.assertAlmostEqual(result["subtasks_cost"], 0.04)
        self.assertAlmostEqual(result["whole_cost"], 0.5)
        self.assertEqual(result["reason"], "split: 0.060000 < 0.500000")
        self.assertEqual(result["subtasks"], ["r1", "r2"])

    def test_s3_keeps_whole_when_not_cheaper(self):
        # Same 0.06 total, whole is only 0.05 -> keep whole.
        subtasks = [self.subtask("r1", 0.02), self.subtask("r2", 0.02)]
        whole = {"route": "whole", "expected_cost": 0.05}
        result = r.decompose(whole, subtasks, "S3", self.registry(), "orch")
        self.assertFalse(result["split"])
        self.assertAlmostEqual(result["overhead"], 0.02)
        self.assertAlmostEqual(result["subtasks_cost"], 0.04)
        self.assertAlmostEqual(result["whole_cost"], 0.05)
        self.assertEqual(result["reason"], "keep whole: 0.060000 >= 0.050000")
        self.assertEqual(result["subtasks"], ["r1", "r2"])

    # --- whole plan has no route: infinite cost, D7 ------------------------

    def test_whole_route_none_counts_as_infinite_cost(self):
        # no_route()'s shape: no "expected_cost" key at all when route is None.
        whole = {"route": None,
                "reason": "no route survives the filters: sign in"}
        subtasks = [self.subtask("r1", 0.02), self.subtask("r2", 0.02)]
        result = r.decompose(whole, subtasks, "S3", self.registry(), "orch")
        self.assertTrue(result["split"])
        self.assertIsNone(result["whole_cost"])
        self.assertEqual(result["reason"], "split: 0.060000 < inf")

    # --- a subtask with no route fails the split closed ---------------------

    def test_subtask_with_no_route_fails_closed_naming_it(self):
        subtasks = [self.subtask("r1", 0.01),
                   {"route": None,
                    "reason": "no route survives the filters: sign in"}]
        whole = {"route": "whole", "expected_cost": 1.0}
        result = r.decompose(whole, subtasks, "S3", self.registry(), "orch")
        self.assertEqual(result, {
            "split": False,
            "reason": "subtask 2 has no route: "
                     "no route survives the filters: sign in",
        })

    def test_first_subtask_with_no_route_is_named_subtask_1(self):
        subtasks = [{"route": None, "reason": "no route: narrow paths"},
                   self.subtask("r2", 0.01)]
        whole = {"route": "whole", "expected_cost": 1.0}
        result = r.decompose(whole, subtasks, "S3", self.registry(), "orch")
        self.assertEqual(result["reason"],
                        "subtask 1 has no route: no route: narrow paths")

    # --- overhead arithmetic, S4 ---------------------------------------------

    def test_overhead_arithmetic_scales_with_subtask_count(self):
        # overhead = 3 * 3000 * 5e-6 = 0.045; subtasks_cost = 3 * 0.001 = 0.003
        subtasks = [self.subtask("r%d" % i, 0.001) for i in range(1, 4)]
        whole = {"route": "whole", "expected_cost": 10.0}
        result = r.decompose(whole, subtasks, "S4", self.registry(), "orch")
        self.assertAlmostEqual(result["overhead"], 0.045)
        self.assertAlmostEqual(result["subtasks_cost"], 0.003)
        self.assertTrue(result["split"])

    # --- fail closed on registry gaps ---------------------------------------

    def test_missing_brief_tokens_bucket_fails_closed_naming_it(self):
        reg = self.registry()
        del reg["policy"]["brief_tokens"]["S3"]
        subtasks = [self.subtask("r1", 0.01)]
        whole = {"route": "whole", "expected_cost": 1.0}
        with self.assertRaises(ValueError) as cm:
            r.decompose(whole, subtasks, "S3", reg, "orch")
        self.assertIn("brief_tokens", str(cm.exception))
        self.assertIn("S3", str(cm.exception))

    def test_unknown_orchestrator_fails_closed_naming_it(self):
        subtasks = [self.subtask("r1", 0.01)]
        whole = {"route": "whole", "expected_cost": 1.0}
        with self.assertRaises(ValueError) as cm:
            r.decompose(whole, subtasks, "S3", self.registry(), "ghost")
        self.assertIn("ghost", str(cm.exception))


class UnavailableUntilResolverTests(unittest.TestCase):
    """The resolver honours ``unavailable_until`` (brief UNTIL, 2026-09-26;
    R-gateway-12: a 429 with retryable:true but a multi-day reset is not
    soon-retryable -- mark the entry unavailable till the reset). ``now`` is
    injectable into filter_routes/_serving_legs_raw/_client_reason so these
    tests never depend on the wall clock; an entry whose until has passed is
    available again and the reason says "re-probe". The renders
    (tools/registry.py render_*) never read the field at all -- that
    time-independence is pinned in tests/test_registry_render.py.
    """

    def dt(self, *args):
        return datetime(*args, tzinfo=timezone.utc)

    def setUp(self):
        # One leg per provider so dropping a provider/leg/client is visible
        # as a whole removed route.
        self.registry = {
            "providers": {
                "cheap": {"id": "cheap", "tier": "paid",
                          "trains_on_prompts": False},
                "quota": {"id": "quota", "tier": "paid",
                          "trains_on_prompts": False},
            },
            "models": {
                "fast": {"id": "fast", "tool_calls": "proven",
                         "context_usable": {"tokens": 100000,
                                            "source": "default"}},
                "slow": {"id": "slow", "tool_calls": "proven",
                         "context_usable": {"tokens": 100000,
                                            "source": "default"}},
            },
            "routes": {
                "r-quota": {"id": "r-quota", "legs": ["quota/slow"]},
                "r-plain": {"id": "r-plain", "legs": ["cheap/fast"]},
            },
            "clients": {
                "agy": {"id": "agy"},
            },
        }
        self.card = {"kind": "review", "privacy": "public"}
        self.features = {"need_tokens": 1000}
        self.state = {"opencode": {"installed": True, "signed_in": True,
                                   "reason": ""},
                      "agy": {"installed": True, "signed_in": True,
                              "reason": ""}}

    def filter(self, client="opencode", now=None):
        return r.filter_routes(self.card, self.features, self.state,
                               self.registry, {}, client=client, now=now)

    # --- providers.<id> / unavailable_legs ---------------------------------

    def test_a_provider_with_a_future_until_drops_its_legs(self):
        self.registry["providers"]["quota"]["unavailable_until"] = (
            "2026-10-01T09:05:00Z")
        survivors, removed = self.filter(now=self.dt(2026, 9, 26, 19, 17))
        self.assertEqual(survivors, ["r-plain"])
        self.assertEqual(
            removed["r-quota"],
            ["no usable leg: quota/slow: unavailable: quota until "
             "2026-10-01T09:05:00Z"])

    def test_a_provider_whose_until_passed_serves_again_and_says_re_probe(self):
        self.registry["providers"]["quota"]["available"] = False
        self.registry["providers"]["quota"]["unavailable_until"] = (
            "2026-10-01T09:05:00Z")
        now = self.dt(2026, 10, 1, 9, 5, 0)
        survivors, removed = self.filter(now=now)
        self.assertIn("r-quota", survivors)
        _, skipped, re_probe_notes = r.usable_legs(
            self.registry["routes"]["r-quota"],
            self.card, self.features, self.state,
            self.registry, {}, now=now)
        # The re-probe note is no longer in skipped for a usable leg; it
        # lives in re_probe_notes instead (FUP 2026-09-27).
        self.assertNotIn("quota/slow", skipped)
        self.assertEqual(re_probe_notes["quota/slow"],
                         ["re-probe: quota unavailable_until passed"])

    def test_an_unavailable_leg_with_a_future_until_names_the_date(self):
        self.registry["routes"]["r-plain"]["unavailable_legs"] = {
            "cheap/fast": {"available": False,
                           "unavailable_until": "2026-10-01T09:05:00Z"}}
        survivors, removed = self.filter(now=self.dt(2026, 9, 30))
        self.assertNotIn("r-plain", survivors)
        self.assertIn("unavailable: cheap/fast until 2026-10-01T09:05:00Z",
                      removed["r-plain"][0])

    def test_an_unparsable_provider_until_does_not_say_passed(self):
        # UNTILfix: the "unavailable_until passed" note needs a *parsable*
        # until at or before now. An unparsable value falls through to the
        # available flag in unavailable_now, so nothing "passed" and the
        # reason line must not claim it did (rule 7 reports the value).
        self.registry["providers"]["quota"]["unavailable_until"] = (
            "next tuesday")
        _, skipped, _ = r.usable_legs(self.registry["routes"]["r-quota"],
                                   self.card, self.features, self.state,
                                   self.registry, {}, now=self.dt(2026, 9, 26))
        self.assertNotIn("quota/slow", skipped)

    def test_an_unparsable_leg_until_does_not_say_passed(self):
        # Same on the leg's own unavailable_legs entry.
        self.registry["routes"]["r-plain"]["unavailable_legs"] = {
            "cheap/fast": {"unavailable_until": "next tuesday"}}
        _, skipped, _ = r.usable_legs(self.registry["routes"]["r-plain"],
                                   self.card, self.features, self.state,
                                   self.registry, {}, now=self.dt(2026, 9, 26))
        self.assertNotIn("cheap/fast", skipped)

    def test_an_unavailable_leg_with_no_until_stays_unavailable_forever(self):
        self.registry["routes"]["r-plain"]["unavailable_legs"] = {
            "cheap/fast": {"available": False}}
        for now in (self.dt(2026, 9, 26), self.dt(2030, 1, 1)):
            survivors, removed = self.filter(now=now)
            self.assertNotIn("r-plain", survivors)
            self.assertEqual(removed["r-plain"],
                             ["no usable leg: cheap/fast: unavailable"])
            _, skipped, _ = r.usable_legs(self.registry["routes"]["r-plain"],
                                       self.card, self.features, self.state,
                                       self.registry, {}, now=now)
            self.assertEqual(skipped["cheap/fast"], ["unavailable"])

    # --- clients.<id> -------------------------------------------------------

    def test_a_client_with_a_future_until_is_filtered_with_the_date(self):
        self.registry["clients"]["agy"]["unavailable_until"] = (
            "2026-10-01T09:05:00Z")
        survivors, removed = self.filter(client="agy",
                                         now=self.dt(2026, 9, 26, 19, 17))
        self.assertEqual(survivors, [])
        for route_id in ("r-quota", "r-plain"):
            self.assertIn("client: agy unavailable until 2026-10-01T09:05:00Z",
                          removed[route_id])

    def test_a_client_whose_until_passed_is_filtered_again_says_re_probe(self):
        self.registry["clients"]["agy"]["available"] = False
        self.registry["clients"]["agy"]["unavailable_until"] = (
            "2026-10-01T09:05:00Z")
        survivors, removed = self.filter(client="agy",
                                         now=self.dt(2026, 10, 1, 9, 5, 0))
        self.assertNotIn("r-plain", survivors)
        self.assertIn("re-probe: agy unavailable_until passed",
                      removed["r-plain"])
        self.assertIn("re-probe: agy unavailable_until passed",
                      removed["r-quota"])

    def test_a_client_available_false_without_an_until_stays_filtered(self):
        self.registry["clients"]["agy"]["available"] = False
        survivors, removed = self.filter(client="agy", now=self.dt(2030, 1, 1))
        self.assertEqual(survivors, [])
        self.assertIn("client: agy unavailable", removed["r-plain"])

    def test_no_until_keys_changes_nothing_for_a_passing_client(self):
        survivors, removed = self.filter(client="agy",
                                         now=self.dt(2026, 9, 26))
        self.assertEqual(sorted(survivors), ["r-plain", "r-quota"])

    # --- injectability -------------------------------------------------------

    def test_the_same_registry_is_available_or_not_by_now_alone(self):
        self.registry["providers"]["quota"]["unavailable_until"] = (
            "2026-10-01T09:05:00Z")
        before, _ = self.filter(now=self.dt(2026, 10, 1, 9, 4, 59))
        after, _ = self.filter(now=self.dt(2026, 10, 1, 9, 5, 0))
        self.assertNotIn("r-quota", before)
        self.assertIn("r-quota", after)

    # --- the real registry, cooled (R6STOP, 2026-09-28) ----------------------
    #
    # The writer side of this is in tests/test_autoos_spawner.py: a gateway
    # "all credentials ... are cooling down" line now records
    # google_ai_studio.unavailable_until. What has to follow is that the
    # t2-worker combos the run was routed to -- whose only live legs are that
    # provider and its two siblings (R6RES section 1) -- stop being answers, and
    # that the caller is told WHEN, not just that nothing served.

    REAL_UNTILS = {
        # FREEWIRE 2026-09-30 cooled every provider a t2-worker combo could
        # still be served by. CIGREEN (aced9915, B2-AGY): antigravity is kept
        # cooled too. GLM55/AINATIVE 2026-10-05: the two providers the
        # operator's order put at t2-worker's head join the fixture, so the
        # earliest cooled still-leggable provider a reason can name is
        # ainative (the oc glm leg is gated unavailable, GLM55 gate); antigravity
        # backs legs again (CLAUDE55, t1-orchestrator/opus-5-5) and is cooled
        # after it, so the reason's earliest return stays ainative.
        "antigravity": "2026-09-28T12:25:00Z",
        "opencode_gateway": "2026-09-28T12:10:00Z",
        "ainative": "2026-09-28T12:20:00Z",
        "google_ai_studio": "2026-09-28T12:30:00Z",
        "meta_api": "2026-09-28T14:00:00Z",
        "scaleway": "2026-09-28T15:00:00Z",
        "nebius": "2026-09-28T16:00:00Z",
        "hugging_face": "2026-09-28T17:00:00Z",
        "groq": "2026-09-28T18:00:00Z",
        "openrouter": "2026-09-28T19:00:00Z",
        "ovhcloud": "2026-09-28T20:00:00Z",
        "deepseek": "2026-09-28T21:00:00Z",
    }

    def real_registry(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        for provider_id, until in self.REAL_UNTILS.items():
            registry["providers"][provider_id]["unavailable_until"] = until
        return registry

    def real_plan(self, card):
        return r.plan(card,
                      {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                       "tests": True, "need_tokens": 1000},
                      {"opencode": {"installed": True, "signed_in": True,
                                    "reason": ""}},
                      self.real_registry(), {}, [], "muse-spark",
                      self.dt(2026, 9, 28, 12, 0, 0))

    def test_a_free_band_cooldown_sends_the_t2_worker_routes_away(self):
        cooled = {"t2-worker", "t2-worker-free-only", "t2-worker-clean"}
        card = {"kind": "implement", "spec": "exact", "risk": "normal",
                "mode": "balanced", "privacy": "public"}
        result = self.real_plan(card)
        self.assertNotIn(result["route"], cooled,
                         "a plan that answers a combo whose every leg is cooling "
                         "is how the next task gets the same 429: %s"
                         % result["reason"])
        # The reason names the cooldown, and names the EARLIEST return as the
        # retry -- a caller reading it must not wait for the last one.
        # CIGREEN: expectation moved by aced9915 (B2-AGY removed the
        # antigravity legs, so no reason can name an antigravity cooldown).
        # Moved again by GLM55/AINATIVE 2026-10-05 + the GLM55 gate: the
        # earliest cooled provider still legged on t2-worker is ainative
        # (the oc glm leg is gated unavailable, not cooling).
        self.assertIn("unavailable: ainative until 2026-09-28T12:20:00Z",
                      result["reason"], result["reason"])
        dates = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                           result["reason"])
        self.assertEqual(min(dates), "2026-09-28T12:20:00Z", result["reason"])

    def test_a_cooldown_refuses_a_card_that_insists_on_t2_worker(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY removed the
        # antigravity legs, and the provider is available=false, so cooling it
        # changes nothing). GLM55/AINATIVE 2026-10-05: the earliest
        # still-legged cooled provider on t2-worker is ainative (the oc glm
        # leg is gated unavailable, GLM55 gate); google_ai_studio and meta_api
        # are still legged too.
        card = {"kind": "implement", "spec": "exact", "risk": "normal",
                "mode": "balanced", "privacy": "public",
                "override": {"route": "t2-worker"}}
        result = self.real_plan(card)
        self.assertIsNone(result["route"], result["reason"])
        self.assertEqual(result["state"], "input_required")
        self.assertIn("t2-worker", result["reason"])
        self.assertIn("unavailable: ainative until 2026-09-28T12:20:00Z",
                      result["reason"])
        self.assertIn("unavailable: google_ai_studio until 2026-09-28T12:30:00Z",
                      result["reason"])
        self.assertIn("unavailable: meta_api until 2026-09-28T14:00:00Z",
                      result["reason"])


class MetaApiResolverTests(unittest.TestCase):
    """BRIEF MUSEAPI (2026-09-27): meta_api/muse-spark-1.3-contributor is the
    main writer for normal work, but the contributor contract trains on prompts
    (evidence: dev.meta.ai/docs/pricing-rate-limits), so a privacy=sensitive
    card must never reach it. filter_routes() is where that is enforced: one
    non-private-safe serving leg disqualifies the whole route (spec: privacy is
    a hard filter, only -clean routes serve sensitive work)."""

    @classmethod
    def setUpClass(cls):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        cls.registry = json.loads(path.read_text(encoding="utf-8"))

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True,
                             "reason": ""}}

    def test_the_contributor_leg_is_never_private_safe(self):
        safe, reason = registry_tool.private_safe(
            "meta_api", "muse-spark-1.3-contributor", self.registry)
        self.assertFalse(safe)
        self.assertIn("train", reason.lower())

    def test_a_sensitive_card_removes_every_route_the_leg_heads(self):
        card = {"kind": "review", "privacy": "sensitive"}
        survivors, removed = r.filter_routes(
            card, {"need_tokens": 1000}, self.state(), self.registry, {})
        for route_id in ("t1-orchestrator", "t1-orchestrator-paid",
                         "spark-1.3-contributor"):
            self.assertNotIn(route_id, survivors, route_id)
            self.assertIn(route_id, removed, route_id)
            joined = " ".join(removed[route_id])
            self.assertIn("privacy: meta_api/muse-spark-1.3-contributor",
                          joined, route_id)

    def test_a_public_card_routes_through_the_contributor_leg(self):
        card = {"kind": "review", "privacy": "public"}
        survivors, _ = r.filter_routes(
            card, {"need_tokens": 1000}, self.state(), self.registry, {})
        for route_id in ("t1-orchestrator", "t1-orchestrator-paid",
                         "spark-1.3-contributor"):
            self.assertIn(route_id, survivors, route_id)
        legs, skipped, _notes = r.usable_legs(
            self.registry["routes"]["t1-orchestrator"], card,
            {"need_tokens": 1000}, self.state(), self.registry, {})
        # R4a (D-212, supersedes D-141 ordering cited below): the paid
        # contributor leg is last resort, not merely "not first" -- while a
        # free leg of the route is healthy it is not selected at all. It stays
        # in skipped with its held-back reason, and everything usable is free.
        # FREEKEYS-2 (D-141 item 3) ordered the band free -> credit -> paid, so the
        # paid contributor leg is no longer the first leg a public card sees — it is
        # still the leg the route *serves* the writer on, and everything ahead of it
        # must be free, which is the whole point of the reorder.
        self.assertNotIn(("meta_api", "muse-spark-1.3-contributor"), legs)
        self.assertIn("meta_api/muse-spark-1.3-contributor", skipped)
        self.assertTrue(any(reason.startswith("paid held back")
                            for reason in
                            skipped["meta_api/muse-spark-1.3-contributor"]))
        for leg in legs:
            self.assertIn(self.registry["providers"][leg[0]]["tier"],
                          ("free", "trial", "credit"), leg)
        # T1-CREDIT-FIX-6 (D-220): the vertex leg of gemini-3.8-flash is
        # priced per provider now, so it correctly survives while the paid
        # leg is held back -- the band order is free -> credit -> paid. The
        # paid leg stays held back while a freeish leg is healthy (R4a), so
        # the band check runs over the surviving legs, all below paid.
        band = {"free": 0, "credit": 1, "paid": 2, "subscription": 2}
        ranks = [band[self.registry["providers"][leg[0]]["tier"]]
                 for leg in legs]
        self.assertEqual(ranks, sorted(ranks), legs)
        self.assertTrue(all(rank < band["paid"] for rank in ranks), legs)
        for paid_route in ("t1-orchestrator-paid", "spark-1.3-contributor"):
            paid, _s, _n = r.usable_legs(
                self.registry["routes"][paid_route], card,
                {"need_tokens": 1000}, self.state(), self.registry, {})
            self.assertEqual(paid[0], ("meta_api", "muse-spark-1.3-contributor"),
                             paid_route)

    def test_no_clean_route_serves_the_contributor_leg(self):
        card = {"kind": "review", "privacy": "sensitive"}
        survivors, _ = r.filter_routes(
            card, {"need_tokens": 1000}, self.state(), self.registry, {})
        for route_id in survivors:
            if not route_id.endswith("-clean"):
                continue
            legs, _skipped, _notes = r.usable_legs(
                self.registry["routes"][route_id], card,
                {"need_tokens": 1000}, self.state(), self.registry, {})
            self.assertFalse([leg for leg in legs
                              if leg[0] == "meta_api"], route_id)


class FreeAiResolverTests(unittest.TestCase):
    """BRIEF FREEAI (2026-09-27): free_ai declares only rpm/tpd (no tpm), so
    the resolver's request-size filter never skips its qwen7b leg - a
    need_tokens*1.3 that exceeds the daily 30k cap must NOT be mistaken for a
    tpm skip. And because free_ai is a free, may-train public pool, a
    privacy=sensitive card disqualifies every route carrying it."""

    @classmethod
    def setUpClass(cls):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        cls.registry = json.loads(path.read_text(encoding="utf-8"))

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True,
                             "reason": ""}}

    def test_free_ai_declares_only_rpm_and_tpd(self):
        entry = self.registry["providers"]["free_ai"]["limits"]["qwen7b"]
        self.assertEqual(entry["rpm"], 10)
        self.assertEqual(entry["tpd"], 30000)
        self.assertNotIn("tpm", entry)

    def test_provider_tpm_is_none_for_free_ai(self):
        self.assertIsNone(
            r.provider_tpm("free_ai", "qwen7b", self.registry))

    def test_a_need_above_the_daily_cap_is_not_skipped_by_a_tpm_filter(self):
        # 30000 * 1.3 = 39000 > tpd 30000, but tpd is data-only today: the leg
        # stays because provider_tpm() reads only tpm and free_ai has none.
        route = self.registry["routes"]["t3-driver-free-only"]
        card = {"kind": "review", "privacy": "public"}
        legs, skipped, _ = r.usable_legs(
            route, card, {"need_tokens": 30000}, self.state(),
            self.registry, {})
        self.assertIn(("free_ai", "qwen7b"), legs)
        self.assertNotIn("free_ai/qwen7b", skipped)

    def test_a_sensitive_card_removes_routes_carrying_free_ai(self):
        card = {"kind": "review", "privacy": "sensitive"}
        survivors, removed = r.filter_routes(
            card, {"need_tokens": 1000}, self.state(), self.registry, {})
        self.assertNotIn("t3-driver-free-only", survivors)
        self.assertIn("t3-driver-free-only", removed)
        joined = " ".join(removed["t3-driver-free-only"])
        self.assertIn("privacy: free_ai/qwen7b", joined)


class ReviewerSelectionTests(unittest.TestCase):
    """Brief REVROUTE (S2) item 2: a review card carrying ``author`` resolves to
    the first entry of ``policy.reviewers`` whose family differs from the
    author's, whose provider/client is available at ``now``, and which privacy
    allows -- paid entries LAST (T0-PAID-4 Q1, operator rule D-212/D-219: the
    walk tries every NON-paid entry first in registry order, then the paid
    ones, and a paid choice says ``last resort`` in its reason). Everything the walk rejected is returned with its reason, because a
    reviewer list that silently narrows is indistinguishable from a config
    mistake -- and "silently no review" is exactly the failure this brief
    exists to close.

    A card where every remaining candidate is only *temporarily* down is
    ``queued`` (the spawner waits, SPAWNFREE's rc 9), never ``unresolved``: a
    rate limit is a delay, not an absence.
    """

    NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
    FUTURE = "2026-09-30T09:00:00Z"
    FUTURE_LATER = "2026-09-30T12:00:00Z"

    def registry(self):
        return {
            "providers": {
                "meta_api": {"id": "meta_api", "tier": "paid",
                             "trains_on_prompts": True},
                "gemini": {"id": "gemini", "tier": "paid",
                           "trains_on_prompts": False},
            },
            "models": {
                "muse-spark-1.3-contributor": {
                    "id": "muse-spark-1.3-contributor", "family": "meta"},
                "gemini-3.8-flash": {"id": "gemini-3.8-flash", "family": "google"},
            },
            "clients": {
                "opencode": {"id": "opencode"},
                "gemini": {"id": "gemini"},
                "qoder": {"id": "qoder"},
                "claude": {"id": "claude"},
            },
            "policy": {"reviewers": [
                {"client": "opencode", "model": "omniroute/spark-1.3-contributor",
                 "family": "meta", "paid": True, "leg": "meta_api/muse-spark-1.3-contributor"},
                {"client": "gemini", "model": "gemini-3.8-flash",
                 "family": "google", "paid": False, "leg": "gemini/gemini-3.8-flash"},
                {"client": "qoder", "model": "qwen3.8-flash",
                 "family": "qwen", "paid": False},
                {"client": "claude", "model": "haiku",
                 "family": "anthropic", "paid": True, "first_pass_only": True},
            ]},
        }

    def state(self, *names):
        """client_state: every client installed and signed in but `names`."""
        out = {c: {"installed": True, "signed_in": True, "reason": ""}
               for c in ("opencode", "gemini", "qoder", "claude")}
        for name in names:
            out[name] = {"installed": True, "signed_in": False,
                         "reason": "test: signed out"}
        return out

    def pick(self, author, **kwargs):
        kwargs.setdefault("client_state", self.state())
        kwargs.setdefault("risk", "normal")
        kwargs.setdefault("privacy", "public")
        return r.reviewer_for(author, self.registry(), kwargs.pop("client_state"),
                              self.NOW, **kwargs)

    # --- the author half ---------------------------------------------------

    def test_author_spelled_as_a_family_works(self):
        result = self.pick("qwen")
        self.assertEqual(result["author_family"], "qwen")
        self.assertEqual(result["reviewer"]["family"], "meta")

    def test_author_spelled_as_a_registry_model_works(self):
        result = self.pick("muse-spark-1.3-contributor")
        self.assertEqual(result["author_family"], "meta")
        # A meta author cannot be reviewed by the meta reviewer. The free
        # google entry rides a paid-tier leg in this fixture, so it serves
        # only as last resort -- the legless free qwen entry reviews.
        self.assertEqual(result["reviewer"]["family"], "qwen")

    def test_author_spelled_as_a_leg_works(self):
        self.assertEqual(self.pick("meta_api/muse-spark-1.3-contributor")
                         ["author_family"], "meta")

    def test_an_author_spelled_as_a_reviewer_model_uses_that_family(self):
        # A client's own model string (qoder's qwen3.8-flash, claude's haiku) is
        # not a registry model id, but the operator's reviewer list states its
        # family. Without that lookup the name is read as a bare family, and
        # "haiku" then looks cross-family to every anthropic reviewer.
        self.assertEqual(self.pick("qwen3.8-flash")["author_family"], "qwen")
        self.assertEqual(self.pick("haiku")["author_family"], "anthropic")

    def test_an_unknown_author_fails_closed_instead_of_being_a_family(self):
        # REVFIX S2. The old reading was "a family nobody registered is
        # cross-family to every reviewer, so the list's head wins". That made
        # the rule unenforceable: a typo'd or unregistered author ("who-knows",
        # "qwen3.8-flsh") always cleared it, so the gate passed exactly the
        # records nobody had checked. Not knowing who wrote the diff is not
        # proof that the reviewer is someone else.
        result = self.pick("who-knows")
        self.assertIsNone(result["reviewer"])
        self.assertIsNone(result["author_family"])
        self.assertEqual(result["state"], "unresolved")
        self.assertIn("who-knows", result["reason"])
        self.assertIn("family", result["reason"])

    def test_a_family_name_the_registry_knows_is_still_accepted(self):
        # Failing closed on the unknown must not break the ordinary shorthand --
        # "qwen" and "meta" are families this registry's own models and
        # reviewers declare, so they name an author just as well as an id.
        self.assertEqual(self.pick("qwen")["author_family"], "qwen")
        self.assertEqual(self.pick("meta")["author_family"], "meta")

    # --- REVFIX S2: case and spelling are not the family --------------------

    def test_the_author_family_is_compared_without_case(self):
        # Meta markets the model as "Meta Muse"; a card authored by it and
        # reviewed by the registry's "meta" reviewer is a self-review, and used
        # to read as an independent one.
        result = self.pick("Meta")
        self.assertEqual(result["author_family"], "meta")
        self.assertEqual(result["reviewer"]["family"], "qwen")
        self.assertIn("same family as author (meta)", result["skipped"][0]["reasons"][0])

    def test_a_reviewer_entry_family_is_compared_without_case(self):
        reg = self.registry()
        reg["policy"]["reviewers"][0]["family"] = "Meta"
        result = r.reviewer_for("muse-spark-1.3-contributor", reg, self.state(),
                                self.NOW, risk="normal", privacy="public")
        self.assertEqual(result["reviewer"]["family"], "qwen",
                         "an operator's capitalization is the same family")

    def test_an_author_spelled_as_a_model_id_ignores_case(self):
        self.assertEqual(self.pick("MUSE-SPARK-1.3-CONTRIBUTOR")["author_family"],
                         "meta")

    def test_an_author_spelled_as_a_leg_ignores_case(self):
        self.assertEqual(self.pick("META_API/muse-spark-1.3-contributor")
                         ["author_family"], "meta")

    def test_an_author_spelled_as_a_route_id_uses_its_first_leg(self):
        # A card often names the ROUTE it ran ("spark-1.3-contributor"), not a
        # model. The legs are a priority order, so the head is the one that took
        # the traffic -- the same reading stop_provider_id gives a 429 line.
        reg = self.registry()
        reg["routes"] = {"spark-1.3-contributor": {
            "id": "spark-1.3-contributor",
            "legs": ["meta_api/muse-spark-1.3-contributor",
                     "gemini/gemini-3.8-flash"]}}
        self.assertEqual(r.author_family("spark-1.3-contributor", reg),
                         ("meta", None))

    def test_a_route_author_fails_closed_when_no_leg_resolves(self):
        reg = self.registry()
        reg["routes"] = {"nowhere": {"id": "nowhere", "legs": ["no_p/no_m"]}}
        family, why = r.author_family("nowhere", reg)
        self.assertIsNone(family)
        self.assertIn("nowhere", why)


    def test_a_missing_family_on_the_author_model_is_not_guessed(self):
        # A model with no family cannot be checked against the rule; claiming
        # "meta" anyway would let an author review itself.
        reg = self.registry()
        del reg["models"]["muse-spark-1.3-contributor"]["family"]
        result = r.reviewer_for("muse-spark-1.3-contributor", reg, self.state(),
                               self.NOW, risk="normal", privacy="public")
        self.assertIsNone(result["reviewer"])
        self.assertIn("family", result["reason"])

    # --- the same-family rule ----------------------------------------------

    def test_the_walk_stops_at_the_first_usable_reviewer(self):
        # Two passes, each an ORDERED preference (T0-PAID-4 Q1, D-212/D-219):
        # every NON-paid entry first in registry order, then the paid ones.
        # Once one entry passes, nothing after it in its pass is examined.
        # Here the qwen author fences its own free entry, so the first pass
        # finds nobody and the paid meta head takes the second pass as last
        # resort -- the skipped list holds exactly the fenced free entry.
        result = self.pick("qwen")
        self.assertEqual(result["reviewer"]["family"], "meta")
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["family"], "qwen")
        self.assertIn("last resort", result["reason"])

    def test_the_author_family_is_skipped_with_a_reason(self):
        result = self.pick("meta")
        self.assertEqual(result["reviewer"]["family"], "qwen")
        first = result["skipped"][0]
        self.assertEqual(first["family"], "meta")
        self.assertIn("same family as author (meta)", first["reasons"][0])
        self.assertFalse(first["waiting"])

    # --- availability ------------------------------------------------------

    def test_a_down_provider_picks_the_next_reviewer(self):
        reg = self.registry()
        reg["providers"]["meta_api"].update(available=False,
                                            unavailable_until=self.FUTURE)
        result = r.reviewer_for("qwen", reg, self.state(), self.NOW,
                                risk="normal", privacy="public")
        self.assertEqual(result["reviewer"]["family"], "google")
        down = result["skipped"][0]
        self.assertEqual(down["family"], "meta")
        self.assertTrue(down["waiting"])
        self.assertIn(self.FUTURE, down["reasons"][0])

    def test_a_past_unavailable_until_does_not_skip(self):
        reg = self.registry()
        reg["providers"]["meta_api"].update(
            available=False, unavailable_until="2026-09-28T09:00:00Z")
        result = r.reviewer_for("qwen", reg, self.state(), self.NOW,
                                risk="normal", privacy="public")
        self.assertEqual(result["reviewer"]["family"], "meta")

    def test_a_signed_out_client_skips_its_reviewer(self):
        result = self.pick("qwen", client_state=self.state("opencode"))
        self.assertEqual(result["reviewer"]["family"], "google")
        self.assertIn("signed in", result["skipped"][0]["reasons"][0])

    def test_a_legless_reviewer_uses_its_client_outage_date(self):
        reg = self.registry()
        reg["clients"]["qoder"].update(available=False,
                                       unavailable_until=self.FUTURE)
        result = r.reviewer_for("meta", reg, self.state("opencode", "gemini"),
                                self.NOW, risk="high", privacy="public")
        # meta and google are out (signed out), anthropic is first-pass-only at
        # high risk -- so qoder's dated outage is what the walk reports.
        qoder = [s for s in result["skipped"] if s["client"] == "qoder"][0]
        self.assertIn(self.FUTURE, qoder["reasons"][0])
        self.assertTrue(qoder["waiting"])

    # --- privacy -----------------------------------------------------------

    def test_a_training_reviewer_never_takes_sensitive_work(self):
        # Muse trains by contributor contract: privacy=sensitive must skip it
        # even though it is the head of the list and cross-family.
        result = self.pick("qwen", privacy="sensitive")
        self.assertEqual(result["reviewer"]["family"], "google")
        meta = result["skipped"][0]
        self.assertIn("privacy", meta["reasons"][0])
        self.assertFalse(meta["waiting"])

    def test_a_legless_reviewer_fails_closed_on_sensitive_work(self):
        # No leg means nothing to check training against, and "unproven" is not
        # "safe" for a private prompt.
        result = self.pick("meta", privacy="sensitive",
                           client_state=self.state("opencode", "gemini"))
        qoder = [s for s in result["skipped"] if s["client"] == "qoder"][0]
        self.assertIn("no registry leg", qoder["reasons"][0])

    # --- first-pass-only ---------------------------------------------------

    def test_haiku_takes_a_normal_first_pass(self):
        result = self.pick("qwen", client_state=self.state("opencode", "gemini"))
        self.assertEqual(result["reviewer"]["client"], "claude")

    def test_haiku_never_takes_the_high_risk_second_review(self):
        result = self.pick("qwen", risk="high",
                           client_state=self.state("opencode", "gemini"))
        self.assertIsNone(result["reviewer"])
        anthropic = [s for s in result["skipped"] if s["family"] == "anthropic"][0]
        self.assertIn("first-pass", anthropic["reasons"][0])

    # --- queue vs unresolved -----------------------------------------------

    def test_everything_temporarily_down_queues_with_the_earliest_reset(self):
        reg = self.registry()
        reg["providers"]["meta_api"].update(available=False,
                                            unavailable_until=self.FUTURE_LATER)
        reg["providers"]["gemini"].update(available=False,
                                          unavailable_until=self.FUTURE)
        reg["clients"]["qoder"].update(available=False,
                                       unavailable_until=self.FUTURE_LATER)
        result = r.reviewer_for("meta", reg, self.state(), self.NOW,
                                risk="high", privacy="public")
        self.assertIsNone(result["reviewer"])
        self.assertEqual(result["state"], "queued")
        # The wait ends at the FIRST reviewer that comes back, not the last.
        self.assertEqual(result["retry_at"], self.FUTURE)

    def test_an_all_structural_walk_is_unresolved_not_queued(self):
        # family/privacy/first-pass rejects never become a wait: nothing is
        # coming back.
        result = self.pick("qwen", risk="high",
                           client_state=self.state("opencode", "gemini"))
        self.assertEqual(result["state"], "unresolved")
        self.assertIsNone(result["retry_at"])

    def test_nothing_at_all_is_unresolved(self):
        reg = self.registry()
        reg["policy"]["reviewers"] = [e for e in reg["policy"]["reviewers"]
                                      if e["family"] == "qwen"]
        result = r.reviewer_for("qwen", reg, self.state(), self.NOW,
                                risk="normal", privacy="public")
        self.assertEqual(result["state"], "unresolved")

    def test_a_missing_reviewers_list_is_reported_not_crashed(self):
        reg = self.registry()
        del reg["policy"]["reviewers"]
        # A model id, not a bare family: with the reviewer list gone "qwen" is
        # nowhere the registry can place either, and this test is about the
        # missing LIST, not about author resolution (REVFIX S2).
        result = r.reviewer_for("muse-spark-1.3-contributor", reg, self.state(),
                                self.NOW, risk="normal", privacy="public")
        self.assertIsNone(result["reviewer"])
        self.assertIn("policy.reviewers", result["reason"])

    # --- explain lines -----------------------------------------------------

    def test_explain_names_every_skipped_reviewer_in_order(self):
        lines = r.reviewer_explain_lines(self.pick("meta"))
        self.assertTrue(lines, "no explain line for a skipped reviewer")
        self.assertIn("omniroute/spark-1.3-contributor", lines[0])

    def test_explain_names_the_last_resort_when_paid_reviews(self):
        # T0-PAID-4 Q1: the qwen author fences its own free entry and the
        # paid-tier google entry reviews as last resort -- the explain block
        # names the skipped free entry and the reason says paid was last
        # resort, never a silent paid review.
        result = self.pick("qwen")
        lines = r.reviewer_explain_lines(result)
        self.assertEqual(len(lines), 1)
        self.assertIn("qwen3.8-flash", lines[0])
        self.assertIn("last resort", result["reason"])

    # --- REVFREE: the free Zen reviewers, on the real registry --------------

    FREE_ZEN_MODELS = ["opencode/longcat-2.5-preview-free",
                       "opencode/nemotron-3-ultra-free",
                       "opencode/mimo-v2.6-flash-free"]

    def real_registry(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def all_clients_up(self):
        return {c: {"installed": True, "signed_in": True, "reason": ""}
                for c in ("opencode", "gemini", "qoder", "claude")}

    def test_a_free_zen_reviewer_never_stands_alone_at_high_risk(self):
        # Their quality is unmeasured (L2-general 2026-09-28T08:12:44Z), so they
        # may take a normal-risk first pass but must never close a high-risk
        # review: with ONLY them on the list, a high-risk card resolves to
        # nothing and says why, while the same card at normal risk is served.
        reg = self.real_registry()
        reg["policy"]["reviewers"] = [e for e in reg["policy"]["reviewers"]
                                      if e["model"] in self.FREE_ZEN_MODELS]
        self.assertEqual([e["model"] for e in reg["policy"]["reviewers"]],
                         self.FREE_ZEN_MODELS)
        # The author is a registry model id, not the reviewer-list spelling
        # "qwen3.8-flash": with the list sliced down to the three, only a model
        # id still places the author's family (REVFIX S2 fails closed on an
        # unplaceable author, and that is not what this test is about).
        author = "qwen/qwen3.8-flash"

        normal = r.reviewer_for(author, reg, self.all_clients_up(), self.NOW,
                                risk="normal", privacy="public")
        self.assertEqual(normal["reviewer"]["model"], self.FREE_ZEN_MODELS[0], normal)

        high = r.reviewer_for(author, reg, self.all_clients_up(), self.NOW,
                              risk="high", privacy="public")
        self.assertIsNone(high["reviewer"], high)
        self.assertEqual(high["state"], "unresolved", high)
        for skipped in high["skipped"]:
            self.assertIn("first-pass", skipped["reasons"][0], skipped)


class PlanReviewCardTests(unittest.TestCase):
    """plan() wires the walk into the route_plan: a review card with an author
    gets a ``review`` decision beside its reviewer *routes*, and the explain
    block carries why each candidate was not picked. Run against the real
    catalog, because the point of item 1 is that this answer is data -- a
    fixture that invented its own reviewer list would prove nothing about the
    shipped one."""

    NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

    @classmethod
    def setUpClass(cls):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        cls.registry = json.loads(path.read_text(encoding="utf-8"))

    def state(self):
        return {name: {"installed": True, "signed_in": True, "reason": ""}
                for name in self.registry["clients"]}

    def plan(self, **card_extra):
        card = {"kind": "review", "mode": "balanced", "risk": "normal",
                "spec": "exact", "privacy": "public"}
        card.update(card_extra)
        return r.plan(card, {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                             "tests": True, "need_tokens": 1000},
                      self.state(), self.registry, {}, [], "claude-opus-4-6",
                      self.NOW)

    def test_a_review_card_with_an_author_carries_the_review_decision(self):
        # T0-PAID-4 Q1 (D-212/D-219): the paid meta head must not review a
        # qwen card while a free entry is usable -- the free google reviewer
        # takes it.
        review = self.plan(author="qwen")["review"]
        self.assertEqual(review["author_family"], "qwen")
        self.assertEqual(review["reviewer"]["family"], "google")
        self.assertEqual(review["reviewer"]["client"], "gemini")
        self.assertIn("gemini-3.8-flash", review["reviewer"]["model"])
        self.assertIsNot(review["reviewer"].get("paid"), True)
        self.assertEqual(review["state"], "resolved")

    def test_the_reason_says_who_authored_it(self):
        self.assertIn("author qwen", self.plan(author="qwen")["review"]["reason"])

    def test_a_meta_authored_card_skips_its_own_family_and_says_so(self):
        plan = self.plan(author="meta")
        review = plan["review"]
        self.assertNotEqual(review["reviewer"]["family"], "meta")
        self.assertIn("meta", [s["family"] for s in review["skipped"]])
        # --explain's job: the skipped candidate is visible, not gone.
        self.assertTrue([l for l in plan["explain"] if "omniroute/spark-1.3" in l])

    def test_a_card_without_an_author_has_no_review_decision(self):
        self.assertIsNone(self.plan()["review"])

    def test_a_non_review_card_never_resolves_a_reviewer(self):
        # An implement card has no review to route; author alone must not start
        # one. research is another non-agentic kind, so the route itself
        # survives and only the `review` guard is under test.
        self.assertIsNone(self.plan(kind="research", author="qwen")["review"])

    def test_sensitive_work_never_lands_on_the_training_reviewer(self):
        # The operator's fixed rule: Muse trains, so a privacy=sensitive card
        # must be reviewed by something that does not.
        result = r.reviewer_for("qwen", self.registry, self.state(), self.NOW,
                                risk="normal", privacy="sensitive")
        if result["reviewer"] is not None:
            self.assertNotEqual(result["reviewer"]["family"], "meta")

    def ghost_review_plan(self, registry):
        card = {"kind": "review", "mode": "balanced", "risk": "normal",
                "spec": "exact", "privacy": "public", "author": "qwen"}
        return r.plan(card, {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                             "tests": True, "need_tokens": 1000},
                      self.state(), registry, {}, [], "claude-opus-4-6",
                      self.NOW)

    def test_a_ghost_leg_after_the_head_does_not_crash_the_review_card(self):
        # T0-PAID-5 P1: one ghost leg in policy.reviewers (after the usable
        # head) must not crash every authored review card -- it reads as
        # unknown (a rejection naming the leg), never a crash.
        import copy
        reg = copy.deepcopy(self.registry)
        reg["policy"]["reviewers"].append(
            {"client": "opencode", "model": "ghost-model",
             "family": "ghostfam", "leg": "ghost-p/ghost-model"})
        plan = self.ghost_review_plan(reg)
        review = plan["review"]
        self.assertIsNotNone(review["reviewer"])
        self.assertEqual(review["reviewer"]["family"], "google")
        ghost_rows = [s for s in review["skipped"]
                      if s["model"] == "ghost-model"]
        self.assertTrue(ghost_rows, review["skipped"])
        self.assertTrue(any("leg ghost-p/ghost-model unresolvable" in reason
                            for reason in ghost_rows[0]["reasons"]),
                        ghost_rows[0]["reasons"])
        self.assertTrue(any("ghost-model" in line for line in plan["explain"]))

    def test_a_ghost_leg_before_the_head_does_not_crash_the_review_card(self):
        # T0-PAID-5 P1: the same ghost leg placed BEFORE the usable head --
        # the two-pass walk pre-computes rejections for every entry, so the
        # crash fired before any reviewer was even considered.
        import copy
        reg = copy.deepcopy(self.registry)
        reg["policy"]["reviewers"].insert(
            0, {"client": "opencode", "model": "ghost-model",
                "family": "ghostfam", "leg": "ghost-p/ghost-model"})
        plan = self.ghost_review_plan(reg)
        review = plan["review"]
        self.assertIsNotNone(review["reviewer"])
        self.assertEqual(review["reviewer"]["family"], "google")
        ghost_rows = [s for s in review["skipped"]
                      if s["model"] == "ghost-model"]
        self.assertTrue(ghost_rows, review["skipped"])
        self.assertTrue(any("leg ghost-p/ghost-model unresolvable" in reason
                            for reason in ghost_rows[0]["reasons"]),
                        ghost_rows[0]["reasons"])


class PlanLimitsGateTests(unittest.TestCase):
    """MISTRALFIX (S1) 2026-09-28: a plan limit that says the model cannot serve
    at all is a per-leg skip, not a fall-through onto a dead leg.

    Measured on api.mistral.ai with the operator's key (x-ratelimit headers):
    `mistral-small-latest` returns 429 at **0 requests/minute** on this plan,
    and a 403 model is simply not on the plan. Before this rule the resolver
    read only `tpm` from providers.<id>.limits, so an `rpm: 0` leg was planned
    as if it served -- the gateway burned the call and fell through.

    Inline registry only, so the gate is isolated: no policy.leg_rules, no
    unavailable_legs, one 0-rpm leg, one plan_available:false leg, one healthy
    fallback.
    """

    def setUp(self):
        self.registry = {
            "providers": {
                "mistral": {
                    "id": "mistral", "tier": "paid", "trains_on_prompts": False,
                    "limits": {
                        "mistral-small-latest": {
                            "rpm": 0,
                            "source": "L1-routing direct probe 2026-09-28"},
                        "mistral-large-latest": {
                            "plan_available": False,
                            "source": "L1-routing direct probe 2026-09-28"},
                        "codestral-latest": {
                            "rpm": 125, "tpm": 625000,
                            "source": "L1-routing direct probe 2026-09-28"},
                    }},
            },
            "models": {
                "mistral-small-latest": {
                    "id": "mistral-small-latest", "tool_calls": "proven",
                    "context_usable": {"tokens": 131072, "source": "default"}},
                "mistral-large-latest": {
                    "id": "mistral-large-latest", "tool_calls": "proven",
                    "context_usable": {"tokens": 131072, "source": "default"}},
                "codestral-latest": {
                    "id": "codestral-latest", "tool_calls": "proven",
                    "context_usable": {"tokens": 131072, "source": "default"}},
            },
            "routes": {
                "r-mixed": {"id": "r-mixed", "legs": [
                    "mistral/mistral-small-latest",
                    "mistral/mistral-large-latest",
                    "mistral/codestral-latest"]},
                "r-all-dead": {"id": "r-all-dead", "legs": [
                    "mistral/mistral-small-latest",
                    "mistral/mistral-large-latest"]},
            },
        }

    def card(self):
        return {"kind": "review", "privacy": "public"}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}

    def legs(self, route_id):
        return r.usable_legs(self.registry["routes"][route_id], self.card(),
                             {"need_tokens": 1000}, self.state(),
                             self.registry, {})

    def test_an_rpm_0_leg_is_skipped_with_reason_plan_0_rpm(self):
        legs, skipped, _ = self.legs("r-mixed")
        self.assertNotIn(("mistral", "mistral-small-latest"), legs)
        self.assertIn("mistral/mistral-small-latest", skipped)
        self.assertIn("plan: 0 rpm", skipped["mistral/mistral-small-latest"])

    def test_a_plan_available_false_leg_is_skipped_naming_the_flag(self):
        legs, skipped, _ = self.legs("r-mixed")
        self.assertNotIn(("mistral", "mistral-large-latest"), legs)
        self.assertIn("mistral/mistral-large-latest", skipped)
        self.assertIn("plan: plan_available false",
                      skipped["mistral/mistral-large-latest"])

    def test_an_rpm_positive_leg_is_kept(self):
        # The gate is about *no* capacity, not about a small one: 125 rpm is a
        # working leg and must stay plannable.
        legs, skipped, _ = self.legs("r-mixed")
        self.assertIn(("mistral", "codestral-latest"), legs)
        self.assertNotIn("mistral/codestral-latest", skipped)

    def test_a_leg_with_both_dead_flags_reports_both_reasons(self):
        # Never stop at the first reason: usable_legs collects every reason that
        # applies, same contract as the context/tpm/leg_rules filters.
        entry = self.registry["providers"]["mistral"]["limits"]["codestral-latest"]
        entry["rpm"] = 0
        entry["plan_available"] = False
        _, skipped, _ = self.legs("r-mixed")
        self.assertEqual(
            skipped["mistral/codestral-latest"],
            ["plan: 0 rpm", "plan: plan_available false"])

    def test_a_route_whose_every_leg_is_plan_dead_is_dropped_naming_plan(self):
        survivors, removed = r.filter_routes(
            self.card(), {"need_tokens": 1000}, self.state(), self.registry, {})
        self.assertNotIn("r-all-dead", survivors)
        self.assertIn("r-all-dead", removed)
        joined = " ".join(removed["r-all-dead"])
        self.assertIn("no usable leg", joined)
        self.assertIn("plan", joined)
        self.assertEqual(r.no_route(removed)["state"], "input_required")

    def test_a_leg_with_no_limits_row_is_never_gated(self):
        # No measurement is not a deny: the same leg with its row removed (and a
        # provider with no limits table at all) stays plannable.
        del self.registry["providers"]["mistral"]["limits"]["mistral-small-latest"]
        legs, skipped, _ = self.legs("r-mixed")
        self.assertIn(("mistral", "mistral-small-latest"), legs)
        self.assertNotIn("mistral/mistral-small-latest", skipped)


class ClaudeBudgetLegTests(unittest.TestCase):
    """D-102 CLAUDEBUDGET (S2) item 2, per-leg: in budget mode a Claude leg is
    held for finals, and nothing else about the route changes.

    The operator's rule is "Claude only orchestrates and gives finals": writers,
    researchers and first reviewers use free/cheap legs. The hold is a *per-leg*
    filter (same shape as policy.leg_rules), so a mixed route keeps its cheap
    legs and only loses the Claude one -- removing the whole route would drop
    work that free capacity can do.
    """

    def registry(self, **budget):
        entry = {"mode": "budget", "weekly_share_left": 0.10,
                 "budget_below": 0.25, "source": "test D-102"}
        entry.update(budget)
        return {
            "providers": {"cc": {"id": "cc"},
                          "antigravity": {"id": "antigravity"},
                          "groq": {"id": "groq"}},
            "models": {
                "claude-opus-4-6": {"id": "claude-opus-4-6",
                                    "family": "anthropic",
                                    "tool_calls": "proven",
                                    "context_usable": {"tokens": 200000,
                                                       "source": "default"}},
                "gemini-3-flash": {"id": "gemini-3-flash", "family": "google",
                                   "tool_calls": "proven",
                                   "context_usable": {"tokens": 200000,
                                                      "source": "default"}},
                "openai/gpt-oss-120b": {"id": "openai/gpt-oss-120b",
                                        "family": "openai-oss",
                                        "tool_calls": "proven",
                                        "context_usable": {"tokens": 200000,
                                                           "source": "default"}},
            },
            "routes": {
                "r-claude-only": {"id": "r-claude-only", "class": "frontier",
                                  "legs": ["cc/claude-opus-4-6"]},
                "r-mixed": {"id": "r-mixed", "class": "frontier",
                            "legs": ["antigravity/claude-opus-4-6",
                                     "groq/openai/gpt-oss-120b"]},
                "r-clean": {"id": "r-clean", "class": "free",
                            "legs": ["groq/openai/gpt-oss-120b",
                                     "antigravity/gemini-3-flash"]},
            },
            "clients": {"claude": {"id": "claude"},
                        "opencode": {"id": "opencode"}},
            "policy": {"claude_budget": entry},
        }

    def features(self):
        return {"need_tokens": 1000}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""},
                "claude": {"installed": True, "signed_in": True, "reason": ""}}

    def legs(self, card, route_id="r-mixed", registry=None, client="opencode",
             env=None):
        registry = registry or self.registry()
        kept, skipped, _ = r.usable_legs(registry["routes"][route_id], card,
                                        self.features(), self.state(),
                                        registry, {}, client, None, env)
        return kept, skipped

    def card(self, **over):
        base = {"kind": "implement", "privacy": "public"}
        base.update(over)
        return base

    # --- what counts as a Claude leg --------------------------------------

    def test_a_claude_leg_is_recognised_by_family_provider_and_name(self):
        registry = self.registry()
        self.assertTrue(r.is_claude_leg("cc/claude-opus-4-6", registry))
        self.assertTrue(r.is_claude_leg("antigravity/claude-opus-4-6", registry))
        self.assertFalse(r.is_claude_leg("groq/openai/gpt-oss-120b", registry))
        self.assertFalse(r.is_claude_leg("antigravity/gemini-3-flash", registry))

    # --- the predicate is the model's family/name, not the provider id -------

    def proxy_registry(self):
        """Claude reached through an unrelated provider: a proxy leg whose model
        row says nothing about Claude, and one whose family does. The old
        predicate read `provider in (cc, anthropic)` and let both through."""
        registry = self.registry()
        registry["providers"]["relay"] = {"id": "relay"}
        registry["models"]["fable-5"] = {
            "id": "fable-5", "tool_calls": "proven",
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["models"]["masked-1"] = {
            "id": "masked-1", "family": "anthropic", "tool_calls": "proven",
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["models"]["opus-4-6"] = {
            "id": "opus-4-6", "tool_calls": "proven",
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["models"]["sonnet-5"] = {
            "id": "sonnet-5", "tool_calls": "proven",
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["models"]["haiku-4-5"] = {
            "id": "haiku-4-5", "tool_calls": "proven",
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["models"]["nemotron-3"] = {
            "id": "nemotron-3", "family": "nvidia", "tool_calls": "proven",
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["routes"]["r-relay"] = {"id": "r-relay", "class": "frontier",
                                         "legs": ["relay/fable-5"]}
        return registry

    def test_a_proxy_or_openrouter_spelling_is_a_claude_leg(self):
        registry = self.proxy_registry()
        for leg in ("relay/fable-5", "relay/masked-1", "relay/opus-4-6",
                    "relay/sonnet-5", "relay/haiku-4-5",
                    "openrouter/anthropic/claude-opus-4-6"):
            self.assertTrue(r.is_claude_leg(leg, registry), leg)
        self.assertFalse(r.is_claude_leg("relay/nemotron-3", registry))

    def test_the_claude_name_check_is_case_insensitive(self):
        registry = self.proxy_registry()
        registry["models"]["Sonnet-5"] = dict(registry["models"]["sonnet-5"],
                                              id="Sonnet-5")
        self.assertTrue(r.is_claude_leg("relay/Sonnet-5", registry))

    def test_a_proxy_claude_leg_is_held_like_any_other(self):
        kept, skipped = self.legs(self.card(), "r-relay",
                                  registry=self.proxy_registry())
        self.assertEqual(kept, [])
        self.assertIn("claude_budget: relay/fable-5 held for finals",
                      skipped["relay/fable-5"])

    def test_a_claude_provider_is_never_a_wait(self):
        registry = self.proxy_registry()
        self.assertTrue(r.is_claude_provider("cc", registry))
        self.assertTrue(r.is_claude_provider("anthropic", registry))
        self.assertTrue(r.is_claude_provider("relay", registry))
        self.assertFalse(r.is_claude_provider("groq", registry))
        self.assertFalse(r.is_claude_provider("antigravity", registry))

    def test_the_real_registry_has_exactly_the_known_claude_legs(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY removed the
        # antigravity leg); the post-AGY registry carried only the cc leg.
        # CLAUDE55 2026-10-05 (operator): the opus-4-6 route (and its cc leg)
        # was renamed opus-5-5 with an antigravity Opus 5.5 leg, and the
        # t1-orchestrator free band gained the antigravity Sonnet 5.5 seat -
        # so the registry now carries exactly those two antigravity legs
        # (cc/claude-opus-4-6 is gone with the renamed route).
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        claude = set()
        for route in registry["routes"].values():
            for leg in route.get("legs") or []:
                if r.is_claude_leg(leg, registry):
                    claude.add(leg)
        self.assertEqual(claude, {"antigravity/claude-opus-5-5-medium",
                                  "antigravity/claude-sonnet-5-5-medium"})

    # --- the hold ----------------------------------------------------------

    def test_budget_mode_holds_the_claude_leg_of_a_mixed_route(self):
        for kind in ("implement", "review", "research", "debug", "bulk", "plan"):
            kept, skipped = self.legs(self.card(kind=kind))
            self.assertEqual(kept, [("groq", "openai/gpt-oss-120b")], kind)
            self.assertEqual(
                skipped["antigravity/claude-opus-4-6"],
                ["claude_budget: antigravity/claude-opus-4-6 held for finals"],
                kind)

    def test_a_final_card_keeps_the_claude_leg_when_the_orchestrator_declares_it(self):
        # The finals are what the operator reserved Claude for -- and the final
        # is the ORCHESTRATOR's declaration (CLAUDEBUDGET-d item 1), not a word
        # the card can say for itself.
        kept, skipped = self.legs(self.card(kind="final"),
                                  env={r.CLAUDE_FINAL_ENV: "L1-routing@deadbeef"})
        self.assertIn(("antigravity", "claude-opus-4-6"), kept)
        self.assertNotIn("antigravity/claude-opus-4-6", skipped)

    def test_a_card_forging_kind_final_does_not_keep_the_claude_leg(self):
        # CLAUDEBUDGET-d item 1 (HIGH, rev-claudebudget2): `kind=final` is a card
        # field, and a worker writes its own card, so a forged final must get the
        # same verdict as any other card while the budget is on and nobody
        # declared: no Claude leg.
        kept, skipped = self.legs(self.card(kind="final"), env={})
        self.assertNotIn(("antigravity", "claude-opus-4-6"), kept)
        self.assertIn("antigravity/claude-opus-4-6", skipped)

    def test_a_v1_role_review_card_is_still_held(self):
        # normalize_v2 maps role=review onto kind=review before plan() sees it,
        # so the hold reads the v2 kind -- pin that the mapped card is held.
        kept, skipped = self.legs(self.card(kind="review"))
        self.assertIn("antigravity/claude-opus-4-6", skipped)

    def test_a_critical_card_alone_is_still_held_and_names_what_it_needs(self):
        # CLAUDEBUDGET-b item 1: `critical` was self-grantable -- any worker could
        # write critical=true on its own card and unlock Claude. Only the
        # orchestrator's env declares a critical path now.
        kept, skipped = self.legs(self.card(critical=True), env={})
        self.assertNotIn(("antigravity", "claude-opus-4-6"), kept)
        self.assertIn(
            "claude_budget: critical needs the orchestrator's "
            "AUTOOS_CLAUDE_CRITICAL", skipped["antigravity/claude-opus-4-6"])

    def test_the_orchestrator_env_unlocks_the_claude_leg(self):
        env = {r.CLAUDE_CRITICAL_ENV: "CI is red on main, nothing else can close it"}
        kept, skipped = self.legs(self.card(critical=True), env=env)
        self.assertIn(("antigravity", "claude-opus-4-6"), kept)
        self.assertNotIn("antigravity/claude-opus-4-6", skipped)

    def test_an_empty_critical_reason_is_not_a_declaration(self):
        kept, _ = self.legs(self.card(critical=True),
                            env={r.CLAUDE_CRITICAL_ENV: "   "})
        self.assertNotIn(("antigravity", "claude-opus-4-6"), kept)

    def test_the_env_unlocks_a_card_that_never_claimed_critical(self):
        # The declaration is the orchestrator's, not the card's: a card that says
        # nothing still gets Claude when its spawner set the env.
        kept, _ = self.legs(self.card(),
                            env={r.CLAUDE_CRITICAL_ENV: "critical path"})
        self.assertIn(("antigravity", "claude-opus-4-6"), kept)

    def test_budget_off_changes_nothing(self):
        registry = self.registry(mode="normal", weekly_share_left=0.3)
        kept, skipped = self.legs(self.card(), registry=registry)
        self.assertEqual(kept, [("antigravity", "claude-opus-4-6"),
                               ("groq", "openai/gpt-oss-120b")])
        self.assertEqual(skipped, {})

    def test_no_claude_budget_key_at_all_changes_nothing(self):
        registry = self.registry()
        del registry["policy"]["claude_budget"]
        kept, skipped = self.legs(self.card(), registry=registry)
        self.assertEqual(skipped, {})
        self.assertIn(("antigravity", "claude-opus-4-6"), kept)

    def test_a_non_claude_route_is_untouched_by_budget_mode(self):
        kept, skipped = self.legs(self.card(), route_id="r-clean")
        self.assertEqual(skipped, {})
        self.assertEqual(len(kept), 2)

    # --- the client half ---------------------------------------------------

    def test_the_claude_client_is_held_for_every_route_except_finals(self):
        registry = self.registry()
        survivors, removed = r.filter_routes(
            self.card(kind="implement"), self.features(), self.state(),
            registry, {}, "claude")
        self.assertEqual(survivors, [])
        for route_id, reasons in removed.items():
            self.assertIn("claude_budget", " ".join(reasons), route_id)

    def test_the_claude_client_still_serves_a_declared_final(self):
        registry = self.registry()
        survivors, _ = r.filter_routes(
            self.card(kind="final"), self.features(), self.state(),
            registry, {}, "claude",
            env={r.CLAUDE_FINAL_ENV: "L1-routing@deadbeef"})
        self.assertEqual(survivors, ["r-claude-only", "r-mixed", "r-clean"])

    def test_a_forged_final_does_not_unlock_the_claude_client(self):
        # The client half of the same rule: the card names the final, the
        # orchestrator's env permits it.
        registry = self.registry()
        survivors, removed = r.filter_routes(
            self.card(kind="final"), self.features(), self.state(),
            registry, {}, "claude")
        self.assertEqual(survivors, [])
        for route_id, reasons in removed.items():
            self.assertIn("claude_budget", " ".join(reasons), route_id)

    def test_the_claude_client_is_untouched_when_budget_is_off(self):
        registry = self.registry(mode="normal", weekly_share_left=1.0)
        survivors, _ = r.filter_routes(
            self.card(kind="implement"), self.features(), self.state(),
            registry, {}, "claude")
        self.assertEqual(len(survivors), 3)

    # --- explain -----------------------------------------------------------

    def test_explain_names_the_budget_state_in_one_line(self):
        lines = r.claude_budget_explain(self.registry())
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("claude_budget: ON"), lines[0])
        self.assertIn("0.1", lines[0])

    def test_explain_says_off_when_budget_is_off(self):
        lines = r.claude_budget_explain(self.registry(mode="normal",
                                                     weekly_share_left=0.9))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("claude_budget: off"), lines[0])

    def test_explain_says_off_when_the_key_is_absent(self):
        registry = self.registry()
        del registry["policy"]["claude_budget"]
        self.assertTrue(r.claude_budget_explain(registry)[0]
                        .startswith("claude_budget: off"))


class ClaudeBudgetPlanTests(unittest.TestCase):
    """plan(): the deferred path and the critical-path override line.

    A deferrable card that loses its last leg to the budget hold does not get a
    Claude fallback -- it waits. `wait_until` is the next cheap/off-peak start
    the registry knows about, and when no window is recorded it says the only
    honest thing: "free capacity".
    """

    def registry(self, **budget):
        # PlanTests' inline registry plus a Claude-only frontier route, so one
        # knob (policy.claude_budget) changes the answer and nothing else does.
        registry = PlanTests.registry(None)
        registry["providers"]["cc"] = {"id": "cc"}
        registry["models"]["claude-opus-4-6"] = {
            "id": "claude-opus-4-6", "family": "anthropic", "reasoning": True,
            "effort_ladder": ["none", "low", "medium", "high", "max"],
            "tool_calls": "proven", "price_in": 1.5e-5, "price_out": 7.5e-5,
            "output_max": 32000,
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["routes"]["r-claude"] = {"id": "r-claude",
                                          "class": "frontier",
                                          "legs": ["cc/claude-opus-4-6"]}
        registry["clients"] = {"opencode": {"id": "opencode"},
                               "claude": {"id": "claude"}}
        registry["policy"]["claude_budget"] = {
            "mode": "budget", "weekly_share_left": 0.10,
            "budget_below": 0.25, "source": "test D-102"}
        registry["policy"]["claude_budget"].update(budget)
        return registry

    def plan(self, registry=None, client="opencode", now=None, env=None,
             **card_overrides):
        registry = registry or self.registry()
        card = {"kind": "implement", "spec": "exact", "risk": "normal",
                "mode": "balanced", "privacy": "public"}
        card.update(card_overrides)
        features = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                    "tests": True, "need_tokens": 1000}
        state = {"opencode": {"installed": True, "signed_in": True, "reason": ""},
                 "claude": {"installed": True, "signed_in": True, "reason": ""}}
        return r.plan(card, features, state, registry, {}, [], "orch",
                      now or datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc),
                      client, env)

    # --- the deferred path (Claude-only card) ------------------------------

    def claude_only(self, **budget):
        """A registry in which the only routes left are Claude ones."""
        registry = self.registry(**budget)
        for route_id in ("r-free", "r-cheap", "r-mid", "r-frontier"):
            del registry["routes"][route_id]
        return registry

    def test_a_deferrable_claude_only_card_is_deferred_not_falled_back(self):
        plan = self.plan(self.claude_only(), deferrable=True)
        self.assertIsNone(plan["route"])
        self.assertEqual(plan["state"], "deferred")
        self.assertIn("claude_budget", plan["reason"])
        self.assertIn("wait_until", plan)
        self.assertIsNotNone(plan["wait_until"])

    def test_wait_until_is_the_next_cheap_window_the_registry_knows(self):
        # cheap-p turns cheap at 10:00 UTC on weekdays and the clock is 09:00
        # Tuesday, so that is the window this card waits for -- the same
        # providers.<id>.windows data defer_until() already reads.
        registry = self.claude_only()
        plan = self.plan(registry, kind="review", deferrable=True)
        self.assertEqual(plan["state"], "deferred")
        self.assertEqual(plan["wait_until"], "2026-09-29T10:00Z")

    def test_wait_until_says_free_capacity_when_no_window_is_known(self):
        registry = self.claude_only()
        del registry["providers"]["cheap-p"]["windows"]
        plan = self.plan(registry, deferrable=True)
        self.assertEqual(plan["wait_until"], "free capacity")
        self.assertEqual(plan["reason"], "claude_budget")

    def test_a_claude_offpeak_window_is_never_a_wait(self):
        # CLAUDEBUDGET-b item 2: cc's 21:00 window is a *Claude* window. Waiting
        # for it means the card runs on Claude as soon as the price drops -- the
        # opposite of holding Claude for finals. With no non-Claude window on
        # file the answer is the honest one: free capacity.
        registry = self.claude_only()
        del registry["providers"]["cheap-p"]["windows"]
        registry["providers"]["cc"]["windows"] = [
            {"days": ["mon", "tue", "wed", "thu", "fri"], "utc_from": "21:00",
             "utc_to": "23:59", "price_factor": 0.5, "kind": "load",
             "source": "test"}]
        plan = self.plan(registry, deferrable=True,
                         now=datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(plan["state"], "deferred")
        self.assertEqual(plan["wait_until"], "free capacity")
        self.assertEqual(plan["reason"], "claude_budget")

    def test_a_non_claude_window_still_wins_over_a_claude_one(self):
        # The exclusion is per provider, not "no windows at all": cc turns cheap
        # at 09:30 and cheap-p at 10:00, and the card waits for cheap-p -- the
        # Claude window is not merely later, it is off the board.
        registry = self.claude_only()
        registry["providers"]["cc"]["windows"] = [
            {"days": ["mon", "tue", "wed", "thu", "fri"], "utc_from": "09:30",
             "utc_to": "23:59", "price_factor": 0.5, "kind": "load",
             "source": "test"}]
        plan = self.plan(registry, deferrable=True)
        self.assertEqual(plan["wait_until"], "2026-09-29T10:00Z")

    def test_budget_wait_until_reads_only_non_claude_providers(self):
        registry = self.claude_only()
        now = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)
        del registry["providers"]["cheap-p"]["windows"]
        registry["providers"]["mid-p"]["windows"] = [
            {"days": ["mon", "tue", "wed", "thu", "fri"], "utc_from": "19:00",
             "utc_to": "23:59", "price_factor": 0.5, "kind": "load",
             "source": "test"}]
        registry["providers"]["cc"]["windows"] = [
            {"days": ["mon", "tue", "wed", "thu", "fri"], "utc_from": "18:00",
             "utc_to": "23:59", "price_factor": 0.5, "kind": "load",
             "source": "test"}]
        wait, reason = r.budget_wait_until(registry, now)
        self.assertEqual(wait, "2026-09-29T19:00Z")
        self.assertIn("claude_budget", reason)

    def test_a_non_deferrable_claude_only_card_never_gets_claude(self):
        plan = self.plan(self.claude_only())
        self.assertIsNone(plan["route"])
        self.assertNotEqual(plan["state"], "ready")
        self.assertIn("claude_budget", plan["reason"])

    # --- the critical-path override ---------------------------------------

    def test_a_critical_card_alone_routes_to_nothing(self):
        # CLAUDEBUDGET-b item 1 rewrote this test. HEAD's version was the hole:
        # `critical=true` on a card unlocked Claude, and a card is written by the
        # worker that wants the model, so every worker could unlock it. Without
        # the orchestrator's env there is now no route left at all.
        plan = self.plan(self.claude_only(), critical=True)
        self.assertIsNone(plan["route"])
        self.assertIn(r.CLAUDE_BUDGET_CRITICAL_NEEDED, plan["reason"])

    def test_a_declared_critical_card_routes_to_claude_and_cites_the_reason(self):
        # The declaration is the orchestrator's, and the reason text it put in
        # the env is what the plan carries, so a DONE line can cite why Claude
        # was spent (item 1: the record has to name the authority, not the flag).
        reason = "CI is red on main, nothing else can close it"
        plan = self.plan(self.claude_only(), critical=True,
                         env={r.CLAUDE_CRITICAL_ENV: reason})
        self.assertEqual(plan["route"], "r-claude")
        self.assertIn("claude_budget: critical-path override", plan["reason"])
        self.assertIn(reason, plan["reason"])

    def test_a_declared_critical_card_never_claims_the_field(self):
        # The env alone is the declaration: a card that says nothing about
        # critical still gets Claude, because it is not the card's call.
        plan = self.plan(self.claude_only(),
                         env={r.CLAUDE_CRITICAL_ENV: "critical path"})
        self.assertEqual(plan["route"], "r-claude")

    def test_a_deferred_plan_carries_the_keys_a_ready_plan_has(self):
        # Item 4: the deferred dict had its own, smaller shape, so a caller that
        # read plan["reviewers"] on every plan raised only on a deferred one --
        # the state that arrives most when the fleet is busy.
        plan = self.plan(self.claude_only(), deferrable=True)
        self.assertEqual(plan["state"], "deferred")
        for key, expected in (("leg", None), ("p", None), ("theta", None),
                              ("expected_cost", None), ("reviewers", []),
                              ("escalation", [])):
            self.assertIn(key, plan, key)
            self.assertEqual(plan[key], expected, key)

    def test_a_declared_final_routes_to_claude_without_the_override_line(self):
        plan = self.plan(self.claude_only(), kind="final",
                         env={r.CLAUDE_FINAL_ENV: "L1-routing@deadbeef"})
        self.assertEqual(plan["route"], "r-claude")
        self.assertNotIn("critical-path", plan["reason"])

    def test_a_forged_final_does_not_route_to_claude(self):
        # No declaration in the caller's env, so the reserved Claude route is
        # not reachable from the card alone.
        plan = self.plan(self.claude_only(), kind="final")
        self.assertNotEqual(plan["route"], "r-claude")

    def test_budget_off_plan_carries_no_budget_override(self):
        registry = self.registry(mode="normal", weekly_share_left=0.9)
        plan = self.plan(registry, critical=True)
        self.assertNotIn("claude_budget: critical-path", plan["reason"])

    def test_a_deferred_plan_explains_the_budget(self):
        # The deferred plan has no scored route to explain, so its whole explain
        # block is the budget state -- otherwise "deferred" looks like a bug in
        # the resolver rather than a policy.
        plan = self.plan(self.claude_only(), deferrable=True)
        self.assertTrue(plan["explain"][0].startswith("claude_budget: ON"),
                        plan["explain"])


class ClaudeBudgetRealRegistryTests(unittest.TestCase):
    """The before/after table on the shipped catalog (D-102, 3 cards).

    Budget OFF is the status quo: these three cards must route exactly as they
    did before the field existed, or the change moves work nobody asked to move.
    Budget ON must never land a non-final card on a Claude leg, and must leave a
    final card's Claude legs alone.

    The three cards are the non-agentic kinds: an implement/debug/bulk card
    needs a tool_calls-proven leg from the measured overlay, which is host state
    this file never reads -- the CLI's own `route --card kind=implement` check
    covers that path against the real overlay instead.
    """

    NOW = datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)
    OFF = {"mode": "normal", "weekly_share_left": None}
    ON = {"mode": "budget", "weekly_share_left": 0.10}

    @classmethod
    def setUpClass(cls):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        cls.registry = json.loads(path.read_text(encoding="utf-8"))

    def plan(self, kind, budget):
        import copy
        registry = copy.deepcopy(self.registry)
        if budget is not None:
            registry["policy"]["claude_budget"].update(budget)
        card = {"kind": kind, "spec": "exact", "risk": "normal",
                "mode": "balanced", "privacy": "public"}
        features = {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                    "tests": True, "need_tokens": 1000}
        state = {name: {"installed": True, "signed_in": True, "reason": ""}
                 for name in registry["clients"]}
        return r.plan(card, features, state, registry, {}, [],
                      "claude-opus-4-6", self.NOW)

    def on_registry(self):
        """A deep copy of the shipped registry with the budget forced ON.

        The shipped value is mode=normal since the operator's 2026-10-01
        routing-00 decision (the weekly limit reset), so a test that proves the
        ON hold must supply its own registry copy instead of reading the shipped
        value -- otherwise it asserts the OFF behaviour and passes vacuously.
        """
        import copy
        registry = copy.deepcopy(self.registry)
        registry["policy"]["claude_budget"].update(self.ON)
        return registry

    def legs(self, card_kind, route_id, env=None, registry=None):
        """usable_legs() for one shipped route. The cards here are non-agentic
        kinds, so no tool_calls overlay is consulted, and need_tokens stays
        under every leg's context -- the budget hold is the only reason a leg
        can be skipped in these tests. `registry` defaults to the shipped one;
        pass `self.on_registry()` to prove the ON hold."""
        registry = self.registry if registry is None else registry
        state = {name: {"installed": True, "signed_in": True, "reason": ""}
                 for name in registry["clients"]}
        return r.usable_legs(registry["routes"][route_id],
                             {"kind": card_kind, "privacy": "public"},
                             {"need_tokens": 1000}, state, registry, {},
                             "opencode", self.NOW, env)

    # card kind -> (route, leg) with the budget off, read off the shipped
    # catalog on 2026-09-28 and approved as the before/after pin. FREEWIRE
    # 2026-09-30: removing the gemini head and adding the pinned free combos
    # moved all three small cards onto the probe-passed groq-qwen3.8-27b combo
    # (free, and the cheapest usable leg for these non-agentic kinds).
    BEFORE_AFTER = {
        "review": ("groq-qwen3.8-27b", "groq/qwen/qwen3.8-27b"),
        "research": ("groq-qwen3.8-27b", "groq/qwen/qwen3.8-27b"),
        "plan": ("groq-qwen3.8-27b", "groq/qwen/qwen3.8-27b"),
    }

    def test_budget_off_routes_are_the_pinned_table(self):
        for kind, (route, leg) in self.BEFORE_AFTER.items():
            plan = self.plan(kind, self.OFF)
            self.assertEqual((plan["route"], plan["leg"]), (route, leg), kind)

    def test_budget_on_changes_nothing_for_the_3_cards(self):
        for kind in self.BEFORE_AFTER:
            off = self.plan(kind, self.OFF)
            on = self.plan(kind, self.ON)
            self.assertEqual((on["route"], on["leg"]),
                             (off["route"], off["leg"]), kind)
            self.assertFalse([leg for leg in [on["leg"]]
                              if r.is_claude_leg(leg, self.registry)], kind)

    def test_the_shipped_value_turns_budget_mode_off(self):
        # Operator decision 2026-10-01 via routing-00: the weekly Claude limit
        # reset, so the shipped number is mode=normal and the gate is OFF. The
        # shipped `source` keeps that date; this pins the value the host runs.
        line = r.claude_budget_explain(self.registry)[0]
        self.assertTrue(line.startswith("claude_budget: off (mode=normal"), line)

    def test_a_non_final_card_holds_every_claude_leg_it_offers(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY removed the
        # antigravity legs) and 60191348 (B2-PRUNE: cc unavailable). CLAUDE55
        # 2026-10-05 (operator): the opus-4-6 route was renamed opus-5-5 with
        # an antigravity Opus 5.5 leg and the provider is servable on shipped
        # data again, so the hold is proven directly on the shipped catalog
        # (built ON from a copy): a non-final card holds the Claude leg.
        # t2-orchestrator still offers none (CACHEORCH kept it
        # sensitive-capable with no Claude leg).
        on = self.on_registry()
        _kept, skipped, _ = self.legs("implement", "opus-5-5", registry=on)
        held = [leg for leg, reasons in skipped.items()
                if any(x.startswith("claude_budget:") for x in reasons)]
        self.assertEqual(held, ["antigravity/claude-opus-5-5-medium"], "opus-5-5")
        for leg in held:
            self.assertEqual(
                [x for x in skipped[leg] if x.startswith("claude_budget:")],
                ["claude_budget: %s held for finals" % leg],
                "%s of %s" % (leg, "opus-5-5"))
        # PRIVACY 2026-10-05 (operator): the sensitive-capable pin no longer
        # keeps t2-orchestrator Claude-free - the route carries the antigravity
        # sonnet 5-5 head again, so a non-final card holds it too.
        _kept, skipped, _ = self.legs("implement", "t2-orchestrator",
                                      registry=on)
        held = [leg for leg, reasons in skipped.items()
                if any(x.startswith("claude_budget:") for x in reasons)]
        self.assertEqual(held, ["antigravity/claude-sonnet-5-5-medium"],
                         "t2-orchestrator")
        self.assertEqual(
            [leg for leg in on["routes"]["t2-orchestrator"]["legs"]
             if r.is_claude_leg(leg, on)],
            ["antigravity/claude-sonnet-5-5-medium"])

    def test_a_final_card_keeps_the_same_claude_legs_when_declared(self):
        on = self.on_registry()
        for route_id in ("opus-5-5", "t2-orchestrator"):
            _kept, skipped, _ = self.legs(
                "final", route_id,
                env={r.CLAUDE_FINAL_ENV: "L1-routing@deadbeef"}, registry=on)
            self.assertEqual([leg for leg, reasons in skipped.items()
                              if any(x.startswith("claude_budget:")
                                     for x in reasons)], [], route_id)

    def legs_of_card(self, card, route_id, env=None):
        state = {name: {"installed": True, "signed_in": True, "reason": ""}
                 for name in self.registry["clients"]}
        return r.usable_legs(self.registry["routes"][route_id], card,
                             {"need_tokens": 1000}, state, self.registry, {},
                             "opencode", self.NOW, env)

    def test_a_critical_card_is_held_by_the_budget_now(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY removed the
        # antigravity leg from t2-orchestrator). CLAUDE55 2026-10-05
        # (operator): opus-5-5 is now the one route that offers a Claude leg,
        # with its provider servable on shipped data - so a
        # self-declared-critical card without the orchestrator declaration is
        # budget-held, not kept, straight off the shipped catalog.
        # CLAUDEBUDGET-b item 1 still holds: the card's own `critical` field
        # is the self-grantable override the operator's D-102 answer removed;
        # the orchestrator, not the card, declares a critical path.
        registry = self.on_registry()
        state = {name: {"installed": True, "signed_in": True, "reason": ""}
                 for name in registry["clients"]}
        kept, skipped, _ = r.usable_legs(
            registry["routes"]["opus-5-5"],
            {"kind": "review", "privacy": "public", "critical": True},
            {"need_tokens": 1000}, state, registry, {},
            "opencode", self.NOW, {})
        self.assertEqual([leg for leg, reasons in skipped.items()
                          if any(x.startswith("claude_budget:")
                                 for x in reasons)],
                         ["antigravity/claude-opus-5-5-medium"])
        self.assertNotIn(("antigravity", "claude-opus-5-5-medium"), kept)

    def test_the_orchestrator_env_holds_nothing_on_the_shipped_registry(self):
        # CIGREEN: expectation moved by aced9915 (B2-AGY removed the
        # antigravity leg from t2-orchestrator). CACHEORCH 2026-10-05 kept the
        # route on caching providers; PRIVACY (same day) re-allowed the
        # antigravity claude head, so with the orchestrator declaration set
        # the sonnet leg is KEPT (not budget-held) alongside the caching legs.
        # The same card, same route, with the declaration set by the spawner,
        # under the ON budget (the shipped value is mode=normal, so the ON
        # case is built from a copy).
        on = self.on_registry()
        state = {name: {"installed": True, "signed_in": True, "reason": ""}
                 for name in on["clients"]}
        kept, skipped, _ = r.usable_legs(
            on["routes"]["t2-orchestrator"],
            {"kind": "review", "privacy": "public", "critical": True},
            {"need_tokens": 1000}, state, on, {},
            "opencode", self.NOW,
            {r.CLAUDE_CRITICAL_ENV: "CI is red on main"})
        self.assertIn(("vertex_ai", "gemini-3.8-flash"), kept)
        self.assertIn(("antigravity", "claude-sonnet-5-5-medium"), kept)
        self.assertEqual(
            [leg for leg, reasons in skipped.items()
             if any(x.startswith("claude_budget:") for x in reasons)], [])
        state = {name: {"installed": True, "signed_in": True, "reason": ""}
                 for name in on["clients"]}
        kept, skipped, _ = r.usable_legs(
            on["routes"]["opus-5-5"],
            {"kind": "review", "privacy": "public", "critical": True},
            {"need_tokens": 1000}, state, on, {},
            "opencode", self.NOW,
            {r.CLAUDE_CRITICAL_ENV: "CI is red on main"})
        self.assertIn(("antigravity", "claude-opus-5-5-medium"), kept)
        self.assertNotIn("antigravity/claude-opus-5-5-medium", skipped)

    def test_a_budget_on_copy_refuses_claude_and_the_env_unlocks_it(self):
        # The CLI half of the same rule (item 3(d)): see ClaudeBudgetSpawnTests
        # in tests/test_autoos_spawner.py, which runs this through
        # `autoos-agent.py run` rather than the resolver alone. The shipped
        # value is mode=normal now, so build the ON case explicitly.
        self.assertTrue(r.claude_budget_of(self.on_registry())["on"])



class ZenClaudeLegRulesTests(unittest.TestCase):
    """SB-C item 3 (the hole FREEKEYS-1 found): a provider wildcard allow must
    not re-open a Claude leg that ``deny-claude-paid-api`` denies.

    ``allow-opencode-zen-client-bound`` (match ``opencode-zen/*``) sat ABOVE
    ``deny-claude-paid-api`` (match ``*/claude-*``) and matched first, so a
    Claude leg spelled under zen was never denied by the leg rules — and the leg
    rules are the only Claude filter that still applies once the budget gate
    says yes (a declared final, or the budget off). The rule list is the real
    catalog/ai-registry.json; only the legs are synthetic (an unknown provider is
    a dead route, so a bare new spelling would not reach the rule filter).
    """

    ZEN_CLAUDE = "opencode-zen/claude-sonnet-5"
    ZEN_FREE = "opencode-zen/muse-spark-1.3-contributor-free"

    def registry(self):
        path = (Path(__file__).resolve().parent.parent
                / "catalog" / "ai-registry.json")
        registry = json.loads(path.read_text(encoding="utf-8"))
        registry["models"]["claude-sonnet-5"] = {
            "id": "claude-sonnet-5", "family": "anthropic", "tier": "paid",
            "tool_calls": "proven",
            "context_usable": {"tokens": 200000, "source": "default"}}
        registry["routes"]["r-zen-claude"] = {
            "id": "r-zen-claude", "class": "frontier",
            "legs": [self.ZEN_CLAUDE, self.ZEN_FREE]}
        registry["policy"]["claude_budget"] = {
            "mode": "budget", "weekly_share_left": 0.10, "budget_below": 0.25,
            "source": "test SB-C"}
        return registry

    def features(self):
        return {"need_tokens": 1000}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}

    def legs(self, card, registry, env, route_id="r-zen-claude"):
        kept, skipped, _ = r.usable_legs(registry["routes"][route_id], card,
                                        self.features(), self.state(),
                                        registry, {}, "opencode", None, env)
        return kept, skipped

    # --- the committed verdict, rules only ----------------------------------

    def test_a_claude_leg_under_the_zen_wildcard_is_denied(self):
        registry = self.registry()
        rule = registry_tool.leg_rule_for(self.ZEN_CLAUDE, registry)
        self.assertIsNotNone(rule, self.ZEN_CLAUDE)
        self.assertFalse(rule["allow"],
                         "matched %s: the zen wildcard re-opens Claude" % rule["id"])
        self.assertTrue(registry_tool.leg_denied(self.ZEN_CLAUDE, registry))

    def test_a_declared_final_does_not_reopen_a_claude_leg_under_zen(self):
        """The budget gate says yes (the orchestrator declared the final), and
        the leg rules must still say no: a zen Claude leg is a paid API."""
        registry = self.registry()
        env = {"AUTOOS_CLAUDE_FINAL": "L1-routing@deadbeef"}
        _kept, skipped = self.legs({"kind": "final", "privacy": "public"},
                                   registry, env)
        self.assertNotIn(("zen", "claude-sonnet-5"), _kept)
        reasons = " ".join(skipped[self.ZEN_CLAUDE])
        self.assertIn("leg_rules", reasons)
        self.assertNotIn("claude_budget", reasons)

    def test_without_a_declaration_the_budget_holds_it_too(self):
        """Latent half of the same hole: with the budget ON and nothing
        declared, the leg is held by the budget — so the leg-rule hole only
        shows through when the gate allows Claude. Pinned so a future reader
        knows which layer closed it first."""
        registry = self.registry()
        _kept, skipped = self.legs({"kind": "final", "privacy": "public"},
                                   registry, {})
        reasons = " ".join(skipped[self.ZEN_CLAUDE])
        self.assertIn("claude_budget", reasons)

    # --- what must NOT change ----------------------------------------------

    def test_non_claude_zen_legs_stay_allowed(self):
        registry = self.registry()
        for leg in [self.ZEN_FREE, "opencode-zen/deepseek-v4.1-flash",
                    "opencode-zen/glm-5.2"]:
            rule = registry_tool.leg_rule_for(leg, registry)
            self.assertIsNotNone(rule, leg)
            self.assertTrue(rule["allow"], "%s denied by %s" % (leg, rule["id"]))

    def test_the_two_free_claude_seats_keep_their_allow(self):
        """The reorder moves the paid-API deny up, never onto a seat that is
        Claude by contract: the subscription seat and the free sign-in."""
        registry = self.registry()
        for leg, seat in [("cc/claude-opus-4-6", "allow-claude-code-subscription"),
                          ("antigravity/claude-opus-4-6-thinking",
                           "allow-antigravity-signin")]:
            rule = registry_tool.leg_rule_for(leg, registry)
            self.assertEqual((rule or {}).get("id"), seat, leg)

    def test_the_claude_deny_precedes_every_provider_wildcard_allow(self):
        """Structural: the deny is only as strong as its position, so no
        wildcard provider allow except the named free seats may match first."""
        rules = self.registry()["policy"]["leg_rules"]
        ids = [rule["id"] for rule in rules]
        deny = ids.index("deny-claude-paid-api")
        seats = {"allow-claude-code-subscription", "allow-antigravity-signin"}
        for rule in rules[:deny]:
            if rule["allow"] is True and rule["match"].endswith("/*"):
                self.assertIn(rule["id"], seats,
                              "%s re-opens Claude before deny-claude-paid-api"
                              % rule["id"])

class ComboFallthroughTests(unittest.TestCase):
    """FREEKEYS-2 (brief item 4): a forced 429 on a combo's head leg must land
    the request on a DIFFERENT provider, and it must land on a leg an agentic
    card can actually use.

    The fall-through itself is OmniRoute's `priority` combo strategy — the
    gateway walks the combo's model list and retries the next entry on a 429, so
    the spawner never reacts to one (tools/autoos_routing.py). That makes the
    *rendered combo list* the thing under test: an ordered leg list whose
    neighbours share one provider is a combo that 429s into the same 429. No
    gateway is called here — `fake_priority_walk` mirrors the documented strategy
    against a status table this test supplies.
    """

    ROUTES = ("t1-orchestrator", "t1-orchestrator-free-only", "t2-worker",
              "t2-worker-free-only", "t3-driver", "t3-driver-free-only")

    # CIGREEN-REWORK1 (Sonnet seat; expectation moved by 06d0e714): D-TORDER-2
    # accepts exactly this route as single-provider. Branch on the ROUTE ID,
    # never on leg count, so truncating any other route to one leg still fails
    # the multi-provider pins below instead of passing as "single-provider".
    SINGLE_PROVIDER_EXEMPT = {"t1-orchestrator-free-only"}

    @classmethod
    def setUpClass(cls):
        with (Path(__file__).resolve().parent.parent
              / "catalog" / "ai-registry.json").open(encoding="utf-8") as fh:
            cls.reg = json.load(fh)
        cls.combos = {c["name"]: c
                      for c in registry_tool.render_omniroute(cls.reg)["combos"]}

    def leg_for_ref(self, route_id, ref):
        """The registry leg that rendered to this combo ref (a gateway ref is the
        leg's model_prefix rewritten, so map back through the route's own legs)."""
        for leg in self.reg["routes"][route_id]["legs"]:
            if registry_tool.gateway_ref(leg, self.reg) == ref:
                return leg
        raise AssertionError("%s renders %s from no leg" % (route_id, ref))

    @staticmethod
    def fake_priority_walk(models, statuses):
        """OmniRoute's priority strategy: try each model in order and take the
        first that does not answer 429. None when every leg failed."""
        for ref in models:
            if statuses.get(ref.split("/", 1)[0], 200) != 429:
                return ref
        return None

    def test_a_429_on_the_head_falls_through_to_another_provider(self):
        # CIGREEN: expectation moved by 06d0e714 (D-TORDER-2 ACCEPT:
        # t1-orchestrator-free-only is deliberately single-provider, so a head
        # 429 there has nowhere to fall and fail-closes to None). Multi-leg
        # combos still fall through to another provider.
        # CIGREEN-REWORK1 (Sonnet seat): the exemption is SINGLE_PROVIDER_EXEMPT
        # by route id, not len(combo["models"]) == 1, and the exempt route pins
        # its single leg/single prefix so the exemption goes stale loudly.
        for route_id in self.ROUTES:
            combo = self.combos[route_id]
            head = combo["models"][0]
            chosen = self.fake_priority_walk(
                combo["models"], {head.split("/", 1)[0]: 429})
            if route_id in self.SINGLE_PROVIDER_EXEMPT:
                self.assertEqual(len(self.reg["routes"][route_id]["legs"]), 1,
                                  "%s: the exemption goes stale if this route "
                                  "grows a second leg" % route_id)
                self.assertEqual(len(combo["models"]), 1,
                                  "%s: the exemption goes stale if this combo "
                                  "grows a second leg" % route_id)
                self.assertEqual(len({ref.split("/", 1)[0]
                                      for ref in combo["models"]}), 1,
                                  "%s: the exemption goes stale if this combo "
                                  "grows a second prefix" % route_id)
                self.assertIsNone(chosen,
                                  "%s: a single-provider combo must "
                                  "fail closed, not pick the 429ing leg"
                                  % route_id)
                continue
            self.assertGreater(len(combo["models"]), 1,
                                "%s: only the exempt single-provider route "
                                "may render one leg" % route_id)
            self.assertIsNotNone(chosen, "%s: every leg 429s" % route_id)
            self.assertNotEqual(chosen.split("/", 1)[0], head.split("/", 1)[0],
                                "%s: the fall-through stayed on %s"
                                % (route_id, head.split("/", 1)[0]))

    def test_two_consecutive_provider_failures_still_leave_a_third(self):
        # CIGREEN: expectation moved by 06d0e714 (D-TORDER-2 ACCEPT:
        # t1-orchestrator-free-only is deliberately a single-provider combo,
        # so it has one prefix, not three, and two failures leave nothing).
        # CIGREEN-REWORK1 (Sonnet seat): the exemption is SINGLE_PROVIDER_EXEMPT
        # by route id, not len(prefixes) == 1, and the exempt route pins its
        # single leg/single prefix so the exemption goes stale loudly.
        for route_id in self.ROUTES:
            combo = self.combos[route_id]
            prefixes = []
            for ref in combo["models"]:
                prefix = ref.split("/", 1)[0]
                if prefix not in prefixes:
                    prefixes.append(prefix)
            if route_id in self.SINGLE_PROVIDER_EXEMPT:
                self.assertEqual(len(combo["models"]), 1,
                                  "%s: the exemption goes stale if this combo "
                                  "grows a second leg" % route_id)
                self.assertEqual(
                    prefixes, [prefixes[0]],
                    "%s: the exemption goes stale if this combo grows a "
                    "second prefix" % route_id)
                self.assertIsNone(
                    self.fake_priority_walk(
                        combo["models"], {prefixes[0]: 429}), route_id)
                continue
            self.assertGreater(len(combo["models"]), 1,
                                "%s: only the exempt single-provider route "
                                "may render one leg" % route_id)
            self.assertGreaterEqual(len(prefixes), 3,
                                    "%s: %s has no third provider to fall to"
                                    % (route_id, combo["models"]))
            statuses = {prefix: 429 for prefix in prefixes[:2]}
            chosen = self.fake_priority_walk(combo["models"], statuses)
            self.assertIsNotNone(chosen, route_id)
            self.assertNotIn(chosen.split("/", 1)[0], statuses, route_id)

    def test_the_fall_through_lands_on_a_leg_an_agentic_card_can_use(self):
        # A combo that falls through to a leg whose tool_calls is unproven (or to
        # an unpriced credit leg, or to one whose window is smaller than the
        # context the route declares) falls to a leg the resolver refuses to plan
        # or cannot carry the card at all, so an agentic run landing there has no
        # answer behind the fallback. The context half is the resolver's own
        # promise check, `route_leg_context_fits` -- named, not restated here
        # (FREEKEYS-2c, rev-freekeys2 finding 2).
        # CIGREEN: expectation moved by 1de6603d (TRIAL FINAL: the Gemini hand
        # entries read tool_calls=unproven at model level, so t1-orchestrator's
        # registry-alone usable set is empty). D20 keeps unproven the default
        # until a probe promotes, so tool_calls evidence is supplied the way
        # production supplies it -- an inline probe overlay proving every combo
        # leg -- while priced/context-fit still read the live registry. The
        # accepted single-provider combo (06d0e714) pins exactly one usable leg.
        # CIGREEN-REWORK1 (Sonnet seat): the exemption is SINGLE_PROVIDER_EXEMPT
        # by route id, not len(combo["models"]) == 1, and the exempt route pins
        # its single leg/single prefix so the exemption goes stale loudly.
        proven = {"legs": {}}
        for route_id in self.ROUTES:
            for ref in self.combos[route_id]["models"]:
                proven["legs"].setdefault(
                    self.leg_for_ref(route_id, ref),
                    {"tool_calls": {"value": "proven"}})
        for route_id in self.ROUTES:
            combo = self.combos[route_id]
            route = self.reg["routes"][route_id]
            usable = []
            for ref in combo["models"]:
                leg = self.leg_for_ref(route_id, ref)
                provider_id, model_id = registry_tool.resolve_leg(leg, self.reg)
                model = self.reg["models"][model_id]
                tool_calls = (proven["legs"].get(leg, {})
                              .get("tool_calls", {}).get("value")
                              or model.get("tool_calls"))
                if tool_calls != "proven":
                    continue
                if (self.reg["providers"][provider_id] or {}).get("tier") == "credit":
                    try:
                        if not (float(model.get("price_in")) > 0.0
                                and float(model.get("price_out")) > 0.0):
                            continue
                    except (TypeError, ValueError):
                        continue
                if not r.route_leg_context_fits(leg, route, self.reg):
                    continue
                usable.append(ref)
            if route_id in self.SINGLE_PROVIDER_EXEMPT:
                self.assertEqual(len(self.reg["routes"][route_id]["legs"]), 1,
                                  "%s: the exemption goes stale if this route "
                                  "grows a second leg" % route_id)
                self.assertEqual(len(combo["models"]), 1,
                                  "%s: the exemption goes stale if this combo "
                                  "grows a second leg" % route_id)
                self.assertEqual(
                    usable, combo["models"],
                    "%s: the single-provider combo's one leg must stay "
                    "usable by an agentic card once proven" % route_id)
                continue
            self.assertGreater(len(combo["models"]), 1,
                                "%s: only the exempt single-provider route "
                                "may render one leg" % route_id)
            self.assertGreaterEqual(
                len(usable), 2, "%s: only %s of %s is usable by an agentic card"
                % (route_id, usable, combo["models"]))
            head_prefix = combo["models"][0].split("/", 1)[0]
            self.assertTrue(
                any(u.split("/", 1)[0] != head_prefix for u in usable),
                "%s: no usable fall-through off provider %s in %s"
                % (route_id, head_prefix, usable))

class PaidLastResortTests(unittest.TestCase):
    """R4a (lane T0-PAID-2b1, operator decision D-212): paid legs are the TAIL,
    extended by T0-PAID-3 P1 to every non-free leg.

    A leg whose effective tier (models.<id>.tier else providers.<id>.tier) is
    outside the known free-ish set (free/trial/credit) must never be selected
    while a free/trial/credit leg of the same route is healthy -- even when
    the non-free leg is listed FIRST. A missing or unrecognised tier
    ("subscription" included) is non-free: held back like paid, never counted
    as healthy. When no free/trial/credit leg is servable the non-free leg is
    allowed and the plan carries a `last_resort` line naming every skipped
    free-ish leg + reason. Applies to implement AND review cards, reviewers
    included. Existing credit/trial fail-open behaviour stays untouched
    (credit legs are blockers of paid here, never blocked themselves).
    Fixture style copies the PlanTests registry/card/features/state shapes
    and the leg-overlay shape {"legs": {leg: {"tool_calls": {"value": ...}}}};
    real-registry style copies the ComboFallthroughTests setUpClass loading
    catalog/ai-registry.json.
    """

    def fixture(self, legs=("paid-p/paid-model", "free-p/free-model"),
                extra_models=None, extra_providers=None, policy_reviewers=None):
        providers = {"paid-p": {"id": "paid-p", "tier": "paid",
                                "trains_on_prompts": False},
                     "free-p": {"id": "free-p", "tier": "free",
                                "trains_on_prompts": False}}
        providers.update(extra_providers or {})
        models = {"paid-model": {"id": "paid-model", "family": "paidfam",
                                 "reasoning": False, "effort_ladder": [],
                                 "tool_calls": "proven", "price_in": 1e-5,
                                 "price_out": 2e-5, "output_max": 1000,
                                 "context_usable": {"tokens": 100000,
                                                    "source": "default"}},
                  "free-model": {"id": "free-model", "family": "freefam",
                                 "reasoning": False, "effort_ladder": [],
                                 "tool_calls": "proven", "price_in": 0.0,
                                 "price_out": 0.0, "output_max": 1000,
                                 "context_usable": {"tokens": 100000,
                                                    "source": "default"}},
                  "orch": {"id": "orch", "price_in": 5e-6, "price_out": 1e-5,
                           "context_usable": {"tokens": 200000,
                                              "source": "default"}}}
        models.update(extra_models or {})
        return {"providers": providers, "models": models,
                "routes": {"r-mix": {"id": "r-mix", "class": "cheap",
                                     "legs": list(legs)}},
                "clients": {"opencode": {"id": "opencode"}},
                "policy": {"modes": {"balanced": {"theta": {"value": 0.8},
                                                  "lambda": {"value": 0.01}}},
                           "verify_tokens": {"S0": {"tokens": 2000}},
                           "latency_seed": {"cheap": {"minutes": 10}},
                           "seed_priors": {"cheap": {"S0": {"alpha": 8,
                                                             "beta": 2}}},
                           "reviewers": policy_reviewers or []}}

    def card(self, kind="implement", **over):
        base = {"kind": kind, "spec": "exact", "risk": "normal",
                "mode": "balanced", "privacy": "public"}
        base.update(over)
        return base

    def feats(self, need_tokens=1000):
        return {"files": 1, "modules": 1, "fanout": 4, "lines": 29,
                "tests": True, "need_tokens": need_tokens}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}

    def now(self):
        return datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

    def run_plan(self, registry, card, overlay=None):
        return r.plan(card, self.feats(), self.state(), registry,
                      {} if overlay is None else overlay, [], "orch", self.now())

    def test_implement_paid_first_healthy_free_chooses_free_and_names_paid_held_back(self):
        plan = self.run_plan(self.fixture(), self.card("implement"))
        self.assertEqual(plan["leg"], "free-p/free-model")
        self.assertIn("paid-p/paid-model", " ".join(plan["explain"]))

    def test_implement_all_free_down_paid_allowed_with_last_resort(self):
        reg = self.fixture()
        reg["routes"]["r-mix"]["unavailable_legs"] = {
            "free-p/free-model": {"available": False}}
        plan = self.run_plan(reg, self.card("implement"))
        self.assertEqual(plan["leg"], "paid-p/paid-model")
        blob = " ".join(plan["explain"])
        self.assertIn("last_resort", blob)
        self.assertIn("free-p/free-model", blob)

    def test_review_card_paid_first_healthy_free_chooses_free(self):
        plan = self.run_plan(self.fixture(), self.card("review"))
        self.assertEqual(plan["leg"], "free-p/free-model")
        self.assertIn("paid-p/paid-model", " ".join(plan["explain"]))

    def test_review_card_all_free_down_paid_allowed_with_last_resort(self):
        reg = self.fixture()
        reg["routes"]["r-mix"]["unavailable_legs"] = {
            "free-p/free-model": {"available": False}}
        plan = self.run_plan(reg, self.card("review"))
        self.assertEqual(plan["leg"], "paid-p/paid-model")
        blob = " ".join(plan["explain"])
        self.assertIn("last_resort", blob)
        self.assertIn("free-p/free-model", blob)

    def test_reviewer_paid_entry_first_healthy_free_entry_wins(self):
        # T0-PAID-4 Q1 (operator rule D-212/D-219): paid reviewers are
        # LAST-RESORT, used only when no free/trial/credit reviewer serves.
        # The walk tries every NON-paid entry first (registry order kept),
        # then the paid ones -- so a paid head must not win while a free
        # entry behind it is healthy.
        reviewers = [
            {"client": "opencode", "family": "paidfam", "paid": True,
             "leg": "paid-p/paid-model", "model": "paid-model"},
            {"client": "opencode", "family": "freefam",
             "leg": "free-p/free-model", "model": "free-model"}]
        reg = self.fixture(policy_reviewers=reviewers)
        reg["models"]["author-model"] = {"id": "author-model",
                                         "family": "otherfam"}
        got = r.reviewer_for("author-model", reg, self.state(), self.now())
        self.assertIsNotNone(got["reviewer"])
        self.assertEqual(got["reviewer"]["model"], "free-model")
        self.assertNotIn("last resort", got["reason"])

    def test_reviewer_unknown_tier_entry_waits_behind_a_free_entry(self):
        # T0-PAID-5 P2 (fail closed): a reviewer entry whose leg's tier
        # cannot be read (its provider carries no tier) walks in the LAST
        # pass, so a free entry listed behind it still wins.
        providers = {"tierless-p": {"id": "tierless-p",
                                    "trains_on_prompts": False}}
        models = {"tierless-model": {"id": "tierless-model",
                                     "family": "mystfam"},
                  "author-model": {"id": "author-model",
                                   "family": "otherfam"}}
        reviewers = [
            {"client": "opencode", "family": "mystfam",
             "leg": "tierless-p/tierless-model", "model": "tierless-model"},
            {"client": "opencode", "family": "freefam",
             "leg": "free-p/free-model", "model": "free-model"}]
        reg = self.fixture(policy_reviewers=reviewers,
                           extra_models=models, extra_providers=providers)
        got = r.reviewer_for("author-model", reg, self.state(), self.now())
        self.assertIsNotNone(got["reviewer"])
        self.assertEqual(got["reviewer"]["model"], "free-model")
        self.assertNotIn("last resort", got["reason"])

    def test_reviewer_all_free_skipped_paid_allowed_as_last_resort(self):
        # T0-PAID-4 Q1: when every free entry is skipped (same family here),
        # the paid entry is chosen and the reason says it was last resort.
        reviewers = [
            {"client": "opencode", "family": "freefam",
             "leg": "free-p/free-model", "model": "free-model"},
            {"client": "opencode", "family": "paidfam", "paid": True,
             "leg": "paid-p/paid-model", "model": "paid-model"}]
        reg = self.fixture(policy_reviewers=reviewers)
        reg["models"]["author-model"] = {"id": "author-model",
                                         "family": "freefam"}
        got = r.reviewer_for("author-model", reg, self.state(), self.now())
        self.assertIsNotNone(got["reviewer"])
        self.assertEqual(got["reviewer"]["model"], "paid-model")
        self.assertIn("last resort", got["reason"])

    def test_shipped_registry_reviewers_prefer_free_over_paid_head(self):
        # T0-PAID-4 Q1 on shipped data: the paid meta head
        # (omniroute/spark-1.3-contributor) must NOT review while a free
        # entry is usable. Authors are spelled as the registry resolves
        # them (bare "gemini" is not a registry spelling -- the model id
        # "gemini-3.8-flash" is). A deepseek-family author must still get
        # a cross-family reviewer, never a same-family one.
        import copy
        with (Path(__file__).resolve().parent.parent
              / "catalog" / "ai-registry.json").open(encoding="utf-8") as fh:
            shipped = copy.deepcopy(json.load(fh))
        state = {client: {"installed": True, "signed_in": True, "reason": ""}
                 for client in ("opencode", "gemini", "qoder", "qwen",
                                "claude", "codex")}
        for author, family in (("gemini-3.8-flash", "google"),
                               ("qwen3.8-27b", "qwen"),
                               ("muse-spark-1.3-contributor", "meta")):
            got = r.reviewer_for(author, shipped, state, self.now())
            self.assertIsNotNone(got["reviewer"], author)
            self.assertIsNot(got["reviewer"].get("paid"), True, author)
            self.assertNotEqual(got["reviewer"]["family"], family, author)
        got = r.reviewer_for("deepseek/deepseek-v4.1-flash", shipped,
                             state, self.now())
        self.assertIsNotNone(got["reviewer"])
        self.assertNotEqual(got["reviewer"]["family"], "deepseek")

    def test_shipped_registry_standard_card_chooses_non_paid_while_free_healthy(self):
        # Card complexity=standard, ctx=128k, privacy=public, role=implement,
        # spend=free-ok: v1 shape; resolver v2 equivalent is kind=implement,
        # privacy=public, need_tokens well under 128k. Uses the real
        # route/plan entry (t1-orchestrator). The tail property is derived
        # from the registry, not pinned by leg name: every paid-tier leg of
        # the route is listed after every non-paid leg.
        with (Path(__file__).resolve().parent.parent
              / "catalog" / "ai-registry.json").open(encoding="utf-8") as fh:
            shipped = json.load(fh)
        route = shipped["routes"]["t1-orchestrator"]
        paid_idx = [i for i, leg in enumerate(route["legs"])
                    if r.leg_tier(leg, shipped) == "paid"]
        non_paid_idx = [i for i, leg in enumerate(route["legs"])
                        if r.leg_tier(leg, shipped) != "paid"]
        self.assertTrue(paid_idx, "route has no paid leg to hold back")
        self.assertTrue(non_paid_idx, "route has no non-paid leg to prefer")
        self.assertLess(max(non_paid_idx), min(paid_idx),
                        "paid legs are not the tail: %s" % (route["legs"],))
        overlay = {"legs": {leg: {"tool_calls": {"value": "proven"}}
                            for leg in route["legs"]}}
        legs, _skipped, _notes = r.usable_legs(
            route, self.card("implement"), self.feats(), self.state(),
            shipped, overlay, "opencode", self.now())
        self.assertTrue(legs)
        provider_id, model_id = legs[0]
        tier = r.leg_tier("%s/%s" % (provider_id, model_id), shipped)
        self.assertIn(tier, ("free", "trial", "credit"))

    def unknown_tier_fixture(self):
        providers = {"myst-p": {"id": "myst-p",
                                 "trains_on_prompts": False},
                     "sub-p": {"id": "sub-p", "tier": "subscription",
                               "trains_on_prompts": False}}
        models = {}
        for model_id, family in (("myst-model", "mystfam"),
                                 ("sub-model", "subfam")):
            models[model_id] = {
                "id": model_id, "family": family, "reasoning": False,
                "effort_ladder": [], "tool_calls": "proven",
                "price_in": 1e-5, "price_out": 2e-5, "output_max": 1000,
                "context_usable": {"tokens": 100000, "source": "default"}}
        return self.fixture(legs=("myst-p/myst-model", "sub-p/sub-model",
                                  "free-p/free-model"),
                            extra_models=models, extra_providers=providers)

    def test_implement_unknown_and_subscription_tiers_first_healthy_free_wins(self):
        # T0-PAID-3 P1: a leg whose tier is missing (None) or unrecognised
        # ("subscription" here) is NON-free -- held back like paid while a
        # free leg is usable, never chosen ahead of it.
        plan = self.run_plan(self.unknown_tier_fixture(),
                             self.card("implement"))
        self.assertEqual(plan["leg"], "free-p/free-model")
        blob = " ".join(plan["explain"])
        self.assertIn("myst-p/myst-model", blob)
        self.assertIn("sub-p/sub-model", blob)
        self.assertIn("subscription", blob)

    def test_implement_all_free_down_unknown_tier_allowed_with_last_resort(self):
        # T0-PAID-3 P1: with every free leg down the non-free legs stay --
        # last resort, with a last_resort line naming the skipped free leg.
        reg = self.unknown_tier_fixture()
        reg["routes"]["r-mix"]["unavailable_legs"] = {
            "free-p/free-model": {"available": False}}
        plan = self.run_plan(reg, self.card("implement"))
        self.assertEqual(plan["leg"], "myst-p/myst-model")
        blob = " ".join(plan["explain"])
        self.assertIn("last_resort", blob)
        self.assertIn("free-p/free-model", blob)

    def test_shipped_registry_subscription_leg_held_back_while_free_healthy(self):
        # T0-PAID-3 P1 on shipped data: the cc subscription leg (tier
        # "subscription") is held back while a free leg is healthy. The copy
        # re-opens the two orthogonal gates -- the operator-wide cc outage and
        # the Claude budget -- so the tier rule is what decides.
        import copy
        with (Path(__file__).resolve().parent.parent
              / "catalog" / "ai-registry.json").open(encoding="utf-8") as fh:
            shipped = copy.deepcopy(json.load(fh))
        self.assertEqual(r.leg_tier("cc/claude-opus-4-6", shipped),
                         "subscription")
        shipped["providers"]["cc"]["available"] = True
        shipped["policy"]["claude_budget"]["mode"] = "normal"
        shipped["policy"]["claude_budget"]["weekly_share_left"] = 0.9
        route = {"id": "r-sub", "class": "cheap",
                 "legs": ["cc/claude-opus-4-6", "groq/openai/gpt-oss-120b"]}
        overlay = {"legs": {leg: {"tool_calls": {"value": "proven"}}
                            for leg in route["legs"]}}
        legs, skipped, _notes = r.usable_legs(
            route, self.card("implement"), self.feats(), self.state(),
            shipped, overlay, "opencode", self.now())
        self.assertTrue(legs)
        self.assertEqual(legs[0], ("groq", "openai/gpt-oss-120b"))
        self.assertIn("cc/claude-opus-4-6", skipped)
        blob = " ".join(sum(skipped.values(), []))
        self.assertIn("subscription", blob)

    def test_review_card_paid_cross_family_reviewer_allowed_as_last_resort(self):
        # T0-PAID-3 P3: writer family meta; the reviewer route's healthy free
        # legs are all meta and its only non-meta leg is paid. A free leg of
        # the fenced writer family is not healthy for this card, so the paid
        # cross-family leg is allowed as a last resort -- never reviewer-less.
        providers = {"meta-p": {"id": "meta-p", "tier": "free",
                                "trains_on_prompts": False},
                     "paid-p": {"id": "paid-p", "tier": "paid",
                                "trains_on_prompts": False}}
        models = {"writer-model": {"id": "writer-model", "family": "meta",
                                   "reasoning": False, "effort_ladder": [],
                                   "tool_calls": "proven", "price_in": 0.0,
                                   "price_out": 0.0, "output_max": 1000,
                                   "context_usable": {"tokens": 100000,
                                                      "source": "default"}}}
        for model_id, family, price in (("meta-model", "meta", 0.0),
                                       ("paid-model", "otherfam", 1e-5)):
            models[model_id] = {
                "id": model_id, "family": family, "reasoning": False,
                "effort_ladder": [], "tool_calls": "proven",
                "price_in": price, "price_out": 2 * price,
                "output_max": 1000,
                "context_usable": {"tokens": 100000, "source": "default"}}
        reg = self.fixture(legs=("paid-p/paid-model", "meta-p/meta-model"),
                           extra_models=models, extra_providers=providers)
        card = self.card("review", author="writer-model")
        plan = self.run_plan(reg, card)
        self.assertEqual(plan["leg"], "paid-p/paid-model")
        blob = " ".join(plan["explain"])
        # T0-PAID-5 P4: the fenced free leg is ineligible, not down -- it
        # reads as skipped, never as a last-resort cause for the paid leg.
        self.assertIn("meta-p/meta-model skipped (same family as author)",
                      blob)
        self.assertNotIn("last_resort", blob)

    def test_v1_role_review_card_arms_the_author_fence(self):
        # T0-PAID-4 Q3: v1 cards spell the review as role=review (see
        # is_final_card) -- the P3 author fence must arm for kind OR role.
        # usable_legs is the fence's home (plan() requires a v2 kind), so
        # the v1 card goes straight there: the author's own family (meta)
        # is fenced and the paid cross-family leg stays usable -- the same
        # verdict the kind=review control gets.
        providers = {"meta-p": {"id": "meta-p", "tier": "free",
                                "trains_on_prompts": False},
                     "paid-p": {"id": "paid-p", "tier": "paid",
                                "trains_on_prompts": False}}
        models = {"writer-model": {"id": "writer-model", "family": "meta",
                                   "reasoning": False, "effort_ladder": [],
                                   "tool_calls": "proven", "price_in": 0.0,
                                   "price_out": 0.0, "output_max": 1000,
                                   "context_usable": {"tokens": 100000,
                                                      "source": "default"}}}
        for model_id, family, price in (("meta-model", "meta", 0.0),
                                       ("paid-model", "otherfam", 1e-5)):
            models[model_id] = {
                "id": model_id, "family": family, "reasoning": False,
                "effort_ladder": [], "tool_calls": "proven",
                "price_in": price, "price_out": 2 * price,
                "output_max": 1000,
                "context_usable": {"tokens": 100000, "source": "default"}}
        reg = self.fixture(legs=("meta-p/meta-model", "paid-p/paid-model"),
                           extra_models=models, extra_providers=providers)
        route = reg["routes"]["r-mix"]
        overlay = {"legs": {leg: {"tool_calls": {"value": "proven"}}
                            for leg in route["legs"]}}
        v1 = {"role": "review", "author": "writer-model", "spec": "exact",
              "risk": "normal", "mode": "balanced", "privacy": "public"}
        legs, skipped, _notes = r.usable_legs(
            route, v1, self.feats(), self.state(), reg, overlay,
            "opencode", self.now())
        self.assertEqual(legs, [("paid-p", "paid-model")])
        self.assertIn("meta-p/meta-model", skipped)
        self.assertTrue(any("same family as author" in reason
                            for reason in skipped["meta-p/meta-model"]))
        control = dict(v1, kind="review")
        legs2, skipped2, _notes2 = r.usable_legs(
            route, control, self.feats(), self.state(), reg, overlay,
            "opencode", self.now())
        self.assertEqual(legs2, legs)
        self.assertEqual(skipped2, skipped)

    def test_hold_back_paid_legs_survives_a_missing_provider(self):
        # T0-PAID-4 Q4: a registry oddity (a leg whose provider is gone)
        # must not crash the plan -- the unresolvable leg reads as an
        # unknown tier (non-free, held back) instead of raising.
        reg = self.fixture()
        legs = [("free-p", "free-model"), ("ghost-p", "ghost-model")]
        kept, skipped = r._hold_back_paid_legs(
            legs, ["free-p/free-model", "ghost-p/ghost-model"], {}, reg)
        self.assertEqual(kept, [("free-p", "free-model")])
        self.assertIn("ghost-p/ghost-model", skipped)

    def test_last_resort_lines_survive_a_missing_provider(self):
        # T0-PAID-4 Q4: same oddity through the explain path -- naming a
        # held-back leg whose provider is gone must not raise.
        reg = self.fixture()
        lines = r._paid_last_resort_lines(
            "free-p/free-model",
            {"ghost-p/ghost-model": ["non-free held back: ghost-p/ghost-model "
                                     "is last resort while free-p/free-model "
                                     "is healthy"]}, reg)
        self.assertTrue(any("ghost-p/ghost-model" in line for line in lines))

    def test_hold_back_unreadable_tier_recorded_with_last_resort(self):
        # T0-PAID-5 P3: when the only would-be-free leg's tier lookup raises,
        # the early return must not leave the paid leg selectable with no
        # record -- the unreadable leg lands in skipped ('tier unreadable')
        # and the paid leg carries a last_resort line naming it.
        import copy
        reg = copy.deepcopy(self.fixture())
        kept, skipped = r._hold_back_paid_legs(
            [("ghost-p", "ghost-model"), ("paid-p", "paid-model")],
            ["ghost-p/ghost-model", "paid-p/paid-model"], {}, reg)
        self.assertEqual(kept, [("paid-p", "paid-model")])
        self.assertIn("ghost-p/ghost-model", skipped)
        self.assertTrue(any("tier unreadable" in reason
                            for reason in skipped["ghost-p/ghost-model"]),
                        skipped["ghost-p/ghost-model"])
        lines = r._paid_last_resort_lines("paid-p/paid-model", skipped, reg)
        self.assertTrue(any("last_resort" in line
                            and "ghost-p/ghost-model" in line
                            for line in lines), lines)


if __name__ == "__main__":
    unittest.main()

class ClaudeBudgetGateTests(unittest.TestCase):
    """CLAUDEBUDGET-b item 3: one gate function, every Claude caller.

    HEAD wrote the same policy three times in three shapes (the leg filter, the
    client hold in filter_routes, the plan's override note) and never wrote it at
    all in the two places that also pick a model: reviewer selection and the
    escalation ladders. One function -- `claude_allowed(kind, env, registry,
    now)` -- so a caller cannot escape the budget by forgetting to copy it.
    """

    NOW = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)
    DECLARED = {r.CLAUDE_CRITICAL_ENV: "CI is red on main, nothing else closes it"}
    FINAL = {r.CLAUDE_FINAL_ENV: "L1-routing@deadbeef1234"}

    def registry(self, on=True):
        entry = {"mode": "budget" if on else "normal",
                 "weekly_share_left": 0.05 if on else 0.9,
                 "budget_below": 0.25, "source": "test"}

        def model(model_id, family):
            return {"id": model_id, "family": family, "tool_calls": "proven",
                    "reasoning": True, "output_max": 32000,
                    "effort_ladder": ["none", "low", "high"],
                    "price_in": 1e-5, "price_out": 5e-5,
                    "context_usable": {"tokens": 200000, "source": "default"}}
        return {
            "providers": {"cc": {"id": "cc"}, "antigravity": {"id": "antigravity"},
                          "groq": {"id": "groq"}},
            "models": {"claude-opus-4-6": model("claude-opus-4-6", "anthropic"),
                       "gemini-3-flash": model("gemini-3-flash", "google"),
                       "openai/gpt-oss-120b": model("openai/gpt-oss-120b",
                                                    "openai-oss")},
            "routes": {
                # "mid", not "frontier": the escalation test starts on the cheap
                # route, and the class immediately above "cheap" is the one the
                # Claude route has to occupy for the ladder to reach for it.
                "r-claude": {"id": "r-claude", "class": "mid",
                             "legs": ["cc/claude-opus-4-6"]},
                "r-mixed": {"id": "r-mixed", "class": "mid",
                            "legs": ["groq/openai/gpt-oss-120b"]},
                "r-clean": {"id": "r-clean", "class": "cheap",
                            "legs": ["antigravity/gemini-3-flash"]},
            },
            "clients": {"claude": {"id": "claude"}, "opencode": {"id": "opencode"}},
            "policy": {"claude_budget": entry}}

    def score(self, route_id, registry):
        leg = registry["routes"][route_id]["legs"][0]
        return {"route": route_id, "leg": leg, "effort": "low",
                "expected_cost": 0.001, "p": 0.9}

    def candidates(self, registry, route_ids):
        return [self.score(rid, registry) for rid in route_ids]

    # --- (0) the gate itself ------------------------------------------------

    def test_the_gate_is_open_for_every_kind_when_budget_is_off(self):
        registry = self.registry(on=False)
        for kind in r.CLAUDE_USE_KINDS:
            allowed, reason = r.claude_allowed(kind, {}, registry, self.NOW)
            self.assertTrue(allowed, kind)
            self.assertIn("off", reason, kind)

    def test_no_kind_passes_the_gate_without_a_declaration(self):
        # CLAUDEBUDGET-d item 1: even `final`. HEAD returned True for it with no
        # env at all, so a card that forged kind=final bought Claude legs, the
        # claude client, a Claude reviewer and the high-risk closer -- a
        # permission the worker handed itself, because the worker writes the card.
        registry = self.registry()
        for kind in r.CLAUDE_USE_KINDS:
            allowed, reason = r.claude_allowed(kind, {}, registry, self.NOW)
            self.assertFalse(allowed, kind)
        allowed, reason = r.claude_allowed("final", {}, registry, self.NOW)
        self.assertIn(r.CLAUDE_FINAL_ENV, reason)

    def test_the_orchestrators_final_declaration_opens_the_final_gate(self):
        allowed, reason = r.claude_allowed("final", self.FINAL,
                                           self.registry(), self.NOW)
        self.assertTrue(allowed)
        # The record has to cite WHICH final was declared, lane and sha, or a
        # DONE line claiming "declared" proves nothing.
        self.assertIn("L1-routing@deadbeef1234", reason)

    def test_a_blank_final_declaration_is_not_a_declaration(self):
        registry = self.registry()
        for value in ("", "  ", "\n"):
            allowed, _ = r.claude_allowed("final", {r.CLAUDE_FINAL_ENV: value},
                                          registry, self.NOW)
            self.assertFalse(allowed, repr(value))

    def test_the_critical_declaration_opens_the_final_too(self):
        # An orchestrator that declared the run critical path declared a bigger
        # thing than a final; the final gate must not be stricter than the
        # one it contains.
        self.assertTrue(r.claude_allowed("final", self.DECLARED, self.registry(),
                                         self.NOW)[0])

    def test_a_final_declaration_does_not_open_the_ordinary_kinds(self):
        # The narrower declaration buys only what it names.
        registry = self.registry()
        for kind in ("leg", "review", "escalation", "spawn"):
            self.assertFalse(r.claude_allowed(kind, self.FINAL, registry,
                                              self.NOW)[0], kind)

    def test_a_per_call_reason_opens_the_gate_it_is_passed_to(self):
        # CLAUDEBUDGET-d item 3: the MCP spawn request carries `claude_reason`,
        # so an orchestrator declares ONE spawn instead of exporting the
        # exception server-wide. It is the caller's own text, never the card's.
        registry = self.registry()
        allowed, reason = r.claude_allowed("spawn", {}, registry, self.NOW,
                                           reason="closing the final on L1-routing")
        self.assertTrue(allowed)
        self.assertIn("closing the final on L1-routing", reason)

    def test_the_declaration_opens_every_kind_and_travels_with_the_verdict(self):
        registry = self.registry()
        for kind in ("leg", "review", "escalation", "spawn"):
            allowed, reason = r.claude_allowed(kind, self.DECLARED,
                                               registry, self.NOW)
            self.assertTrue(allowed, kind)
            # Item 1: the reason text has to reach the plan, because the DONE
            # line is what cites why a Claude run was allowed at all.
            self.assertIn("nothing else closes it", reason, kind)

    def test_a_blank_declaration_is_not_a_declaration(self):
        registry = self.registry()
        for value in ("", "   ", "\n"):
            allowed, _ = r.claude_allowed("leg",
                                         {r.CLAUDE_CRITICAL_ENV: value},
                                         registry, self.NOW)
            self.assertFalse(allowed, repr(value))

    def test_an_unknown_kind_is_refused_not_assumed(self):
        with self.assertRaises(ValueError):
            r.claude_allowed("vibes", {}, self.registry(), self.NOW)

    def test_the_claude_client_is_always_a_claude_spend(self):
        self.assertTrue(r.is_claude_client("claude"))
        self.assertFalse(r.is_claude_client("opencode"))
        self.assertFalse(r.is_claude_client("agy"))

    # --- (b) cross-family reviewer selection --------------------------------

    def review(self, card, registry, env=None, chosen="r-mixed"):
        return r._select_reviewers(
            self.candidates(registry, ["r-claude", "r-clean", "r-mixed"]),
            ["r-claude", "r-clean", "r-mixed"],
            self.score(chosen, registry), card, "S2",
            {"need_tokens": 1000}, {}, registry, {}, [], "orch", "balanced",
            "opencode", self.NOW, env)

    def reviewers(self, card, registry, env=None, chosen="r-mixed"):
        return self.review(card, registry, env, chosen)["routes"]

    def test_a_claude_reviewer_is_not_selected_for_a_non_final_card(self):
        routes = self.reviewers({"kind": "implement", "risk": "high"},
                                self.registry())
        for route_id in routes:
            self.assertFalse(any(r.is_claude_leg(leg, self.registry())
                                 for leg in self.registry()["routes"][route_id]["legs"]),
                             "%s reviewed by Claude" % route_id)

    def test_a_claude_reviewer_is_selected_for_a_declared_final(self):
        self.assertIn("r-claude", self.reviewers({"kind": "final", "risk": "high"},
                                                 self.registry(), env=self.FINAL))

    def test_a_forged_final_selects_no_claude_reviewer(self):
        # The reviewer walk asked the gate, and the gate now answers from the
        # caller's env, so `kind=final` in a card buys no Claude reviewer.
        self.assertNotIn("r-claude", self.reviewers({"kind": "final",
                                                     "risk": "high"},
                                                    self.registry()))

    # --- (f) the high-risk closer is a Claude client run, so it is gated ------

    def test_the_high_risk_closer_is_held_without_a_final_declaration(self):
        # CLAUDEBUDGET-d item 1: `_CLOSER` is client claude / model sonnet, and
        # HEAD emitted it for every risk=high card with no gate at all -- the
        # same forged card, the same free Claude spend through a fourth door.
        closer = self.review({"kind": "implement", "risk": "high"},
                             self.registry())["closer"]
        self.assertIsNone(closer)

    def test_the_high_risk_closer_needs_both_the_final_card_and_the_declaration(self):
        # CLAUDEBUDGET-f item 4: HEAD asked only the env half. `AUTOOS_CLAUDE_FINAL`
        # is one declaration about ONE named final, and the card is what says
        # whether THIS plan is that final -- a risk=high implement card with the
        # variable sitting in a profile bought the fourth Claude door anyway,
        # which is the leak item 1 removed and the env variable handed back.
        out = self.review({"kind": "implement", "risk": "high"}, self.registry(),
                          env=self.FINAL)
        self.assertIsNone(out["closer"])

    def test_the_high_risk_closer_applies_for_a_declared_final(self):
        out = self.review({"kind": "final", "risk": "high"}, self.registry(),
                          env=self.FINAL)
        self.assertEqual(out["closer"], {"client": "claude", "model": "sonnet"})

    def test_a_critical_declaration_alone_does_not_emit_the_closer(self):
        # The critical declaration opens the *legs* (a declared run is on the
        # critical path); the closer is not a leg, it is the final's last word,
        # and a non-final card is not that final (item 4).
        out = self.review({"kind": "implement", "risk": "high"}, self.registry(),
                          env=self.DECLARED)
        self.assertIsNone(out["closer"])

    def test_a_role_final_card_is_the_same_card_as_kind_final(self):
        # v1 cards say `role`, v2 say `kind`; the card test must read both, or a
        # v1 final loses its closer while a v2 one keeps it.
        out = self.review({"role": "final", "risk": "high"}, self.registry(),
                          env=self.FINAL)
        self.assertEqual(out["closer"], {"client": "claude", "model": "sonnet"})

    def test_budget_off_still_emits_the_high_risk_closer(self):
        out = self.review({"kind": "implement", "risk": "high"},
                          self.registry(on=False))
        self.assertEqual(out["closer"], {"client": "claude", "model": "sonnet"})

    def test_the_declaration_selects_the_claude_reviewer_for_a_non_final(self):
        routes = self.reviewers({"kind": "implement", "risk": "high"},
                                self.registry(), env=self.DECLARED)
        self.assertIn("r-claude", routes)

    def test_the_budget_line_says_what_unlocks_it(self):
        # The explain line is what an operator reads; naming the card field as
        # the key taught them to set it on the card.
        line = r.claude_budget_explain(self.registry())[0]
        self.assertIn(r.CLAUDE_CRITICAL_ENV, line)
        self.assertNotIn("critical=true", line)

    def test_budget_off_selects_the_claude_reviewer_as_before(self):
        self.assertIn("r-claude",
                      self.reviewers({"kind": "implement", "risk": "high"},
                                     self.registry(on=False)))

    # --- (c) escalation ladders ---------------------------------------------

    def escalator(self, card, registry, env=None):
        chosen = self.score("r-clean", registry)
        # Only the Claude route occupies the class above the chosen one, so a
        # step that appears here appeared *onto Claude*, and one that is absent
        # is absent because the budget took it away -- not because a cheaper
        # non-Claude route won the cost comparison.
        steps = r._escalation(chosen,
                              self.candidates(registry, ["r-claude"]),
                              registry, card, env)
        return {step["on"]: step for step in steps}

    def test_a_capability_escalation_never_raises_onto_claude_in_budget_mode(self):
        steps = self.escalator({"kind": "implement"}, self.registry())
        route_id = steps["capability"].get("route")
        self.assertNotEqual(route_id, "r-claude")
        if route_id:
            self.assertFalse([leg for leg
                              in self.registry()["routes"][route_id]["legs"]
                              if r.is_claude_leg(leg, self.registry())])

    def test_a_capability_escalation_may_raise_onto_claude_when_declared(self):
        steps = self.escalator({"kind": "implement"}, self.registry(),
                               env=self.DECLARED)
        self.assertEqual(steps["capability"]["route"], "r-claude")

    def test_a_final_escalates_onto_claude_only_when_the_orchestrator_declares(self):
        # The escalation ladder reads the same gate as the legs and the
        # reviewers: `kind=final` in the card is a description, not a permission.
        self.assertNotEqual(
            self.escalator({"kind": "final"}, self.registry())["capability"]
            .get("route"), "r-claude")
        steps = self.escalator({"kind": "final"}, self.registry(), env=self.FINAL)
        self.assertEqual(steps["capability"]["route"], "r-claude")

    # --- (a) the leg filter and the plan line it feeds -----------------------

    def legs(self, card, registry, env=None):
        kept, skipped, _ = r.usable_legs(
            registry["routes"]["r-claude"], card, {"need_tokens": 1000},
            {}, registry, {}, "opencode", self.NOW, env)
        return kept, skipped

    def test_a_held_critical_card_names_what_would_unlock_it(self):
        _kept, skipped = self.legs({"kind": "implement", "critical": True},
                                   self.registry(), env={})
        self.assertIn(r.CLAUDE_BUDGET_CRITICAL_NEEDED,
                      skipped["cc/claude-opus-4-6"])

    def test_a_held_non_critical_card_keeps_the_plain_reason(self):
        _kept, skipped = self.legs({"kind": "implement"}, self.registry(),
                                   env={})
        self.assertEqual(skipped["cc/claude-opus-4-6"],
                         ["claude_budget: cc/claude-opus-4-6 held for finals"])


class CreditGuardLegFilterTests(unittest.TestCase):
    """FREEKEYS-1b (items 2-4): the spend guard on a `credit` provider BLOCKS a
    leg instead of only reporting one.

    The two halves the reviewer named as missing (rev-freekeys1 findings 2 and 3)
    are both pinned here. `refuse` (100 % of the operator's grant) drops the leg
    through `usable_legs`, so a route whose only leg is a drained grant is removed
    by `filter_routes` exactly as an unavailable leg is; `warn` (80 % by default)
    keeps the leg and says so in the plan's `explain`. And a `credit` model with
    no price on file is refused outright: an unpriced grant would bill $0 to
    `paid_spend` and read as an untouched $10 while it drains, which is the
    fail-open the guard must not ship with. Free-tier legs are untouched by all
    of this -- they cost nothing measured and the guard is about money.

    The spend is NOT hand-written here: it is `autoos_usage.paid_spend` over
    recorded usage rows at real registry prices, so the number the resolver acts
    on is the number the usage report prints (one reader, one figure).
    """

    CAP = 10.0
    NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    SINCE = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    def registry(self):
        return {
            "providers": {
                "morph": {"id": "morph", "tier": "credit", "model_prefix": "morph",
                          "credit_usd": self.CAP, "monthly_cap_usd": self.CAP,
                          "monthly_warn_fraction": 0.8, "trains_on_prompts": False},
                "groq": {"id": "groq", "tier": "free", "trains_on_prompts": False},
            },
            "models": {
                "morph-priced": {"id": "morph-priced", "tool_calls": "proven",
                                 "context_usable": {"tokens": 100000, "source": "default"},
                                 "price_in": 1e-06, "price_out": 1e-06},
                "morph-unpriced": {"id": "morph-unpriced", "tool_calls": "proven",
                                   "context_usable": {"tokens": 100000, "source": "default"},
                                   "price_in": 0, "price_out": 0},
                "groq-free": {"id": "groq-free", "tool_calls": "proven",
                              "context_usable": {"tokens": 100000, "source": "default"}},
            },
            "routes": {
                "r-credit": {"id": "r-credit",
                             "legs": ["morph/morph-priced", "morph/morph-unpriced",
                                      "groq/groq-free"]},
                "r-only-credit": {"id": "r-only-credit",
                                  "legs": ["morph/morph-priced"]},
            },
            "policy": {"leg_rules": []},
        }

    def card(self):
        return {"kind": "implement", "privacy": "public"}

    def feats(self):
        return {"need_tokens": 10}

    def state(self):
        return {"opencode": {"installed": True, "signed_in": True, "reason": ""}}

    def rows(self, tokens_in, tokens_out):
        """Recorded usage rows for the priced leg, in the gateway's call-log shape."""
        return [{"timestamp": self.NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "provider": "morph", "model": "morph-priced",
                 "tokens": {"in": tokens_in, "out": tokens_out}}]

    def guards(self, tokens_in, tokens_out):
        """The resolver-facing guard map, built by the usage module from real rows."""
        return usage.credit_guards(self.registry(),
                                   self.rows(tokens_in, tokens_out), self.SINCE)

    def legs(self, guards=None, warns=None):
        reg = self.registry()
        return r.usable_legs(reg["routes"]["r-credit"], self.card(), self.feats(),
                             self.state(), reg, {}, credit_guards=guards,
                             credit_warns=warns)

    # --- refuse: the guard blocks ------------------------------------------

    def test_a_drained_grant_drops_the_leg_at_the_cap(self):
        # 5M in + 5M out at 1e-06/token is exactly the $10 grant.
        guards = self.guards(5_000_000, 5_000_000)
        self.assertEqual(guards["morph"]["state"], "refuse")
        _kept, skipped, _notes = self.legs(guards)
        self.assertEqual(skipped["morph/morph-priced"],
                         ["credit exhausted morph $%.2f/$%.2f" % (self.CAP, self.CAP)])

    def test_a_drained_grant_removes_the_route_that_has_only_it(self):
        """Blocking, not reporting: the lone-leg route loses its survivorship."""
        reg = self.registry()
        survivors, removed = r.filter_routes(self.card(), self.feats(), self.state(),
                                             reg, {},
                                             credit_guards=self.guards(5_000_000,
                                                                       5_000_000))
        self.assertNotIn("r-only-credit", survivors)
        self.assertIn("credit exhausted morph", removed["r-only-credit"][0])

    def test_the_free_leg_survives_a_drained_credit_grant(self):
        """FT fall-through preserved: the guard removes a leg, never a whole route
        that still has capacity that costs nothing."""
        _kept, skipped, _notes = self.legs(self.guards(5_000_000, 5_000_000))
        self.assertNotIn("groq/groq-free", skipped)

    # --- warn: the leg stays, the plan says so ------------------------------

    def test_at_eighty_percent_the_leg_is_kept_and_warned(self):
        warns = []
        guards = self.guards(4_000_000, 4_000_000)  # $8 of $10
        self.assertEqual(guards["morph"]["state"], "warn")
        kept, skipped, _notes = self.legs(guards, warns)
        self.assertIn(("morph", "morph-priced"), kept)
        self.assertNotIn("morph/morph-priced", skipped)
        self.assertEqual(warns, ["credit warn morph $8.00/$10.00",
                                "morph credit spend measured month-to-date only "
                                "(set credit_started)"])

    def test_below_the_warn_line_nothing_is_said_and_nothing_is_dropped(self):
        warns = []
        guards = self.guards(1_000_000, 1_000_000)  # $2 of $10
        kept, _skipped, _notes = self.legs(guards, warns)
        self.assertIn(("morph", "morph-priced"), kept)
        # T1-CREDIT-FIX-9 T2: a window-limited ok grant still prints its
        # month-to-date caveat, so the plan never reads windowed spend as a total.
        self.assertEqual(warns, ["morph credit spend measured month-to-date only "
                                "(set credit_started)"])

    # --- fail closed when there is no price on file -------------------------

    def test_an_unpriced_credit_leg_is_dropped_with_no_spend_data(self):
        """No `credit_guards` passed at all still refuses the unpriced leg: the
        missing number is the reason to refuse, not a licence to assume free."""
        _kept, skipped, _notes = self.legs(None)
        self.assertEqual(skipped["morph/morph-unpriced"],
                         ["credit leg unpriced morph-unpriced"])

    def test_an_unpriced_credit_leg_is_dropped_even_when_spend_is_zero(self):
        _kept, skipped, _notes = self.legs(self.guards(0, 0))
        self.assertEqual(skipped["morph/morph-unpriced"],
                         ["credit leg unpriced morph-unpriced"])

    def test_a_free_leg_needs_no_price(self):
        """The unpriced rule is about a finite grant; a free tier costs nothing
        whether or not anyone recorded a number."""
        _kept, skipped, _notes = self.legs(None)
        self.assertNotIn("groq/groq-free", skipped)

    def test_the_priced_credit_leg_is_usable_when_the_grant_is_intact(self):
        """The guard must not refuse what it has money left for, or the whole
        tier reads as dead data."""
        kept, _skipped, _notes = self.legs(self.guards(1_000_000, 1_000_000))
        self.assertIn(("morph", "morph-priced"), kept)


class CreditFailOpenTests(unittest.TestCase):
    """T1-CREDIT-FIX: `$0 spent of $N` is an intact grant, and unmeasurable spend
    keeps the leg with a visible note instead of skipping it.

    Semantics pinned here (see `autoos_usage.credit_guards`): `credit_usd` is
    the trial grant TOTAL in USD; `spent` is what the grant already billed;
    remaining = credit_usd - spent; the leg is exhausted only when
    spent >= monthly_cap_usd (= credit_usd). An unknown figure fails OPEN: a
    prepaid trial grant that is truly spent rejects at the provider (402/429)
    and the combo falls through at run time, while fail-closed dropped the
    whole trial tier on a 403 (measured: `credit exhausted ovhcloud
    $0.00/$200.00` with $0.00 spent).
    """

    CAP = 200.0
    NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    SINCE = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    def registry(self):
        return {
            "providers": {
                "ovhcloud": {"id": "ovhcloud", "tier": "credit",
                             "model_prefix": "ovh", "credit_usd": self.CAP,
                             "monthly_cap_usd": self.CAP,
                             "monthly_warn_fraction": 0.8,
                             "trains_on_prompts": False},
            },
            "models": {
                "ovh-priced": {"id": "ovh-priced", "tool_calls": "proven",
                               "context_usable": {"tokens": 100000,
                                                 "source": "default"},
                               "price_in": 1e-06, "price_out": 1e-06},
            },
            "routes": {
                "r-trial": {"id": "r-trial",
                            "legs": ["ovhcloud/ovh-priced"]},
            },
            "policy": {"leg_rules": []},
        }

    def rows(self, tokens_in, tokens_out):
        return [{"timestamp": self.NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "provider": "ovhcloud", "model": "ovh-priced",
                 "tokens": {"in": tokens_in, "out": tokens_out}}]

    def legs(self, guards, warns=None):
        reg = self.registry()
        if warns is None:
            warns = []
        kept, skipped, _notes = r.usable_legs(
            reg["routes"]["r-trial"], {"kind": "implement", "privacy": "public"},
            {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards, credit_warns=warns)
        return kept, skipped, warns

    def test_zero_spend_of_a_grant_keeps_the_leg(self):
        """$0 spent of $200 is intact, never exhausted."""
        guards = usage.credit_guards(self.registry(), [], self.SINCE)
        self.assertEqual(guards["ovhcloud"]["state"], "ok")
        kept, skipped, _warns = self.legs(guards)
        self.assertIn(("ovhcloud", "ovh-priced"), kept)
        self.assertNotIn("ovhcloud/ovh-priced", skipped)

    def test_full_spend_of_a_grant_exhausts_the_leg(self):
        """100M in + 100M out at 1e-06/token is exactly the $200 grant."""
        guards = usage.credit_guards(self.registry(),
                                     self.rows(100_000_000, 100_000_000),
                                     self.SINCE)
        self.assertEqual(guards["ovhcloud"]["state"], "refuse")
        _kept, skipped, _warns = self.legs(guards)
        # T1-CREDIT-FIX-7 R6: the reason names the effective threshold (the
        # $200 grant less the $20 default margin), not the cap.
        self.assertEqual(skipped["ovhcloud/ovh-priced"],
                         ["credit exhausted ovhcloud $%.2f/$%.2f"
                          % (self.CAP, self.CAP - 20.0)])

    def test_unknown_spend_keeps_the_leg_with_a_note(self):
        """No measurable figure (gateway unreadable, no manual fallback) is
        fail open: the leg stays and the plan says why."""
        guards = usage.credit_guards(self.registry(), None, self.SINCE,
                                     failure="manage key rejected (403) - "
                                             "spend unmeasured")
        self.assertEqual(guards["ovhcloud"]["state"], "unknown")
        kept, skipped, warns = self.legs(guards)
        self.assertIn(("ovhcloud", "ovh-priced"), kept)
        self.assertNotIn("ovhcloud/ovh-priced", skipped)
        self.assertTrue(any("spend unknown" in w for w in warns), warns)

    def test_unknown_spend_puts_the_loud_d220_line_in_the_plan_warns(self):
        """T1-CREDIT-FIX-5 M2 (D-220): while a credit leg is kept on unknown
        spend the plan says so LOUDLY -- the line is the warn accumulator the
        plan's `explain` carries, not a quiet note. Production reaches it
        through `filter_routes` (the entry `plan` uses), not `usable_legs`
        directly."""
        reg = self.registry()
        guards = usage.credit_guards(reg, None, self.SINCE,
                                     failure="manage key rejected (403) - "
                                             "spend unmeasured")
        warns = []
        r.filter_routes({"kind": "implement", "privacy": "public"},
                        {"need_tokens": 10},
                        {"opencode": {"installed": True, "signed_in": True,
                                      "reason": ""}},
                        reg, {}, credit_guards=guards,
                        credit_warns=warns)
        self.assertTrue(
            any(w.startswith("SPEND UNKNOWN: ovhcloud credit leg kept")
                and "grant $200.00" in w and "fail-open per D-220" in w
                and "measured spend unavailable" in w for w in warns), warns)

    def test_missing_guard_data_keeps_the_leg_with_a_note(self):
        """No guard map at all is also 'nothing known': available, noted --
        with its own `no guard for` line (T1-CREDIT-FIX-2), never borrowing
        the `spend unknown` line that means the gateway gave no figure."""
        kept, skipped, warns = self.legs(None)
        self.assertIn(("ovhcloud", "ovh-priced"), kept)
        self.assertNotIn("ovhcloud/ovh-priced", skipped)
        self.assertTrue(any("no guard for ovhcloud" in w for w in warns), warns)

    def test_a_route_whose_class_has_no_scoring_priors_is_removed_not_crashed(self):
        """T1-CREDIT-FIX follow-on: the four class-`credit` single-leg routes
        (TORDER TASK4) ship without policy.latency_seed.credit /
        policy.seed_priors.credit. While fail-closed they never survived
        filtering so nobody noticed; with the legs back, scoring them raises
        ValueError and takes the whole plan down (rc=2). The filter removes
        the unscorable route with the gap named instead."""
        reg = self.registry()
        reg["routes"] = {
            "r-trial": {"id": "r-trial", "class": "credit",
                        "legs": ["ovhcloud/ovh-priced"]},
            "r-free": {"id": "r-free", "class": "free", "legs": ["groq/groq-free"]},
        }
        reg["providers"]["groq"] = {"id": "groq", "tier": "free",
                                    "trains_on_prompts": False}
        reg["models"]["groq-free"] = {
            "id": "groq-free", "tool_calls": "proven",
            "context_usable": {"tokens": 100000, "source": "default"}}
        reg["policy"] = {"leg_rules": [],
                         "latency_seed": {"free": {"minutes": 5}},
                         "seed_priors": {"free": {}}}
        guards = usage.credit_guards(reg, [], self.SINCE)
        survivors, removed = r.filter_routes(
            {"kind": "implement", "privacy": "public"}, {"need_tokens": 10},
            {"opencode": {"installed": True, "signed_in": True, "reason": ""}},
            reg, {}, credit_guards=guards)
        self.assertIn("r-free", survivors)
        self.assertIn("r-trial", removed)
        self.assertTrue(any("scoring priors" in reason
                            for reason in removed["r-trial"]),
                        removed["r-trial"])




if __name__ == "__main__":
    unittest.main()
