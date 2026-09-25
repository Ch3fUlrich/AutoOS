#!/usr/bin/env python3
"""Golden tests for tools/autoos_resolver.py (routing v2 spec, sections 5.2 and 5.5).

Every boundary of the bucket table is pinned here, plus every effort row, the
ladder clamp and the max_tokens floors. The module is pure: it imports from any
cwd once `tools/` is on sys.path, which is the first thing this file does.
"""
import sys
import unittest
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

if __name__ == "__main__":
    unittest.main()
