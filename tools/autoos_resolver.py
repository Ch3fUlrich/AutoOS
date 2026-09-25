"""Complexity bucket and effort rule of routing v2 (spec sections 5.2 and 5.5).

Pure: no I/O, no clock, no network. Every point value, bucket threshold and the
canonical rung order that the clamp needs live here as data (D20: a value with
no home in data cannot be pinned by a test). The functions carry no magic
numbers of their own; a caller may pass a recalibrated table instead.

The public functions:

    points(features, card, table) -> per-feature points
    bucket(features, card, table) -> (bucket_name, total)
    effort(bucket_name, kind, route_class, ladder, reasoning) -> wanted rung
    max_tokens(effort_name, reasoning, output_max) -> int

A missing input fails closed with a ValueError that names the key, never a
default: a silently mis-scored task is routed to a model that cannot do it.
"""
from __future__ import annotations

# The only ordering fact the clamp needs. Effort names themselves never come
# from this module -- they come from the table (thresholds) or the caller's
# ladder.
CANONICAL_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# The spec's sections 5.2 points and bucket thresholds, exactly. `max` is the
# inclusive upper bound of a band; `None` means "and above". Feature bands are
# ranges from the table, so `files=3` and `files=5` share the `max: 5` row.
DEFAULT_BUCKET_TABLE = {
    "features": {
        "files": (
            {"max": 1, "points": 0},
            {"max": 2, "points": 1},
            {"max": 5, "points": 2},
            {"max": 10, "points": 3},
            {"max": None, "points": 4},
        ),
        "modules": (
            {"max": 1, "points": 0},
            {"max": 2, "points": 1},
            {"max": None, "points": 2},
        ),
        "fanout": (
            {"max": 4, "points": 0},
            {"max": 20, "points": 1},
            {"max": None, "points": 2},
        ),
        "lines": (
            {"max": 29, "points": 0},
            {"max": 150, "points": 1},
            {"max": 500, "points": 2},
            {"max": None, "points": 3},
        ),
    },
    "spec": {"exact": 0, "partial": 1, "vague": 2},
    "tests": {True: 0, False: 1},
    "kind": {"implement": 0, "bulk": 0, "review": 0, "research": 0,
             "debug": 2, "plan": 3},
    "buckets": (
        {"name": "S0", "max": 1},
        {"name": "S1", "max": 3},
        {"name": "S2", "max": 6},
        {"name": "S3", "max": 9},
        {"name": "S4", "max": None},
    ),
}

# Features read as a number against a band table. `tests` is boolean and read
# against a mapping; `spec` and `kind` come from the card.
NUMERIC_FEATURES = ("files", "modules", "fanout", "lines")
_CARD_FIELDS = ("spec", "kind")


def _points_from_bands(value, bands, feature):
    """The points of the first band whose inclusive max covers `value`."""
    for band in bands:
        if band["max"] is None or value <= band["max"]:
            return band["points"]
    # Only reachable with a malformed table (no open-ended final band).
    raise ValueError("bucket table has no band covering %s=%r" % (feature, value))


def _points_from_mapping(value, mapping, field):
    """The points of a categorical/bool value, failing closed on unknown."""
    try:
        return mapping[value]
    except (KeyError, TypeError):
        raise ValueError(
            "unknown %s=%r (table knows: %s)"
            % (field, value, ", ".join(repr(k) for k in mapping)))


def points(features, card, table=DEFAULT_BUCKET_TABLE):
    """Per-feature points for a task.

    `features` carries files, modules, fanout, lines (ints) and tests (bool).
    `card` carries spec (exact|partial|vague) and kind (the six v2 kinds).
    Raises ValueError naming any missing feature, missing card field or unknown
    categorical value.
    """
    band_tables = table["features"]
    result = {}

    for name in NUMERIC_FEATURES:
        if name not in features:
            raise ValueError("missing feature %r in features" % name)
        if name not in band_tables:
            raise ValueError("bucket table has no points for feature %r" % name)
        result[name] = _points_from_bands(features[name], band_tables[name], name)

    if "tests" not in features:
        raise ValueError("missing feature %r in features" % "tests")
    result["tests"] = _points_from_mapping(features["tests"], table["tests"], "tests")

    for name in _CARD_FIELDS:
        if name not in card:
            raise ValueError("missing card field %r" % name)
        result[name] = _points_from_mapping(card[name], table[name], name)

    return result


def _bucket_name(total, table=DEFAULT_BUCKET_TABLE):
    """The first bucket whose inclusive max covers `total` (S4 is open-ended)."""
    for bucket in table["buckets"]:
        if bucket["max"] is None or total <= bucket["max"]:
            return bucket["name"]
    raise ValueError("bucket table has no bucket covering total %r" % total)


def bucket(features, card, table=DEFAULT_BUCKET_TABLE):
    """Return (bucket_name, total_points), e.g. ("S2", 5)."""
    total = sum(points(features, card, table).values())
    return _bucket_name(total, table), total


def _clamp(wanted, ladder):
    """Nearest rung the ladder has, preferring the lower rung on a tie.

    An empty ladder, or a wanted rung outside the canonical order, is None: a
    rung the model lacks is never invented.
    """
    if not ladder or wanted is None:
        return None
    if wanted in ladder:
        return wanted
    if wanted not in CANONICAL_EFFORT_ORDER:
        return None

    target = CANONICAL_EFFORT_ORDER.index(wanted)
    best = None
    for rung in ladder:
        if rung not in CANONICAL_EFFORT_ORDER:
            continue
        index = CANONICAL_EFFORT_ORDER.index(rung)
        key = (abs(index - target), index)
        if best is None or key < best[0]:
            best = (key, rung)
    return best[1] if best else None


def effort(bucket_name, kind, route_class, ladder, reasoning):
    """The effort rung for a leg, before sending, per spec section 5.5.

    First matching row wins, exactly in the order the spec lists them:
    not reasoning -> None; plan/debug -> high; S4 frontier -> top rung;
    S3/S4 -> high; S1/S2 -> medium; S0 or bulk -> low. The result is then
    clamped to a rung the leg's ladder actually has.
    """
    if not reasoning:
        return None

    wanted = None
    if kind in ("plan", "debug"):
        wanted = "high"
    elif bucket_name == "S4" and route_class == "frontier":
        wanted = ladder[-1] if ladder else None
    elif bucket_name in ("S3", "S4"):
        wanted = "high"
    elif bucket_name in ("S1", "S2"):
        wanted = "medium"
    elif bucket_name == "S0" or kind == "bulk":
        wanted = "low"
    return _clamp(wanted, ladder)


def max_tokens(effort_name, reasoning, output_max):
    """The output cap for a reasoning leg, per spec section 5.5.

    48000 at any effort, 64000 at high and above, never above `output_max`.
    A non-reasoning leg gets no floor and no field sent: None.
    """
    if not reasoning:
        return None
    if effort_name in ("high", "xhigh", "max"):
        floor = 64000
    else:
        floor = 48000
    return min(floor, output_max)
