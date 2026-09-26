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

import re
from datetime import datetime, timedelta, timezone

import autoos_track as track  # tools/ is on sys.path for every caller
from registry import resolve_leg  # tools/ is on sys.path for every caller

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
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("%s=%r: expected a non-negative integer" % (feature, value))
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


# ---------------------------------------------------------------------------
# Hard filters (spec sections 4 and 5.3 step 1). An override is applied after
# these: it can pick any route that survived, never one a filter removed.
# ---------------------------------------------------------------------------

# Kinds whose result depends on the model actually calling tools (spec 5.3).
AGENTIC_KINDS = ("implement", "debug", "bulk")

_NO_ROUTE_HINTS = ["sign in", "narrow paths", "split the task", "override"]


def _serving_legs_raw(route, registry):
    """Serving legs of `route`, keeping each leg exactly as written in ``legs``.

    Same filtering as `serving_legs` (an ``unavailable_legs`` entry or a
    provider-wide ``available: false`` drops it; an unresolvable leg raises
    ValueError). Returns ``(leg, provider_id, model_id)`` so a caller that
    needs the raw leg string too -- e.g. to look it up in the tool_calls
    overlay, an omniroute_id alias included -- does not have to re-derive it
    from the resolved ids (which would lose that alias).
    """
    providers = registry["providers"]
    unavailable = route.get("unavailable_legs") or {}
    out = []
    for leg in route.get("legs") or []:
        # One leg-resolution rule for the validator and the resolver.
        provider_id, model_id = resolve_leg(leg, registry)
        if leg in unavailable:
            continue
        if providers[provider_id].get("available") is False:
            continue
        out.append((leg, provider_id, model_id))
    return out


def serving_legs(route, registry):
    """Available ``(provider_id, model_id)`` legs of `route`, in leg order.

    A leg resolves through its provider id (or that provider's omniroute_id)
    and its model id; an ``unavailable_legs`` entry or a provider-wide
    ``available: false`` drops it. A leg that resolves to nothing raises
    ValueError: a broken registry must fail closed, never silently drop a
    candidate.
    """
    return [(provider_id, model_id)
           for _, provider_id, model_id in _serving_legs_raw(route, registry)]


def _tool_calls_value(leg, model_id, registry, overlay):
    """The tool_calls verdict for `leg`; a probe's overlay entry wins.

    `leg` is the string exactly as written in a route's ``legs`` (an
    omniroute_id alias included), matching how tools/probe-toolcalls.py keys
    ``overlay["legs"]``. Falls back to the registry model's own ``tool_calls``
    when the overlay carries no measured value for this leg.
    """
    legs_overlay = (overlay or {}).get("legs") or {}
    measured = (legs_overlay.get(leg) or {}).get("tool_calls") or {}
    if "value" in measured:
        return measured["value"]
    return registry["models"][model_id].get("tool_calls")


def usable_context(model_id, registry, overlay):
    """Usable context tokens for `model_id`; the overlay (a probe) wins.

    Falls back to the registry's ``context_usable.tokens`` when the overlay
    carries no measured value for the model.
    """
    models = (overlay or {}).get("models") or {}
    measured = (models.get(model_id) or {}).get("context_usable") or {}
    if "tokens" in measured:
        return int(measured["tokens"])
    return int(registry["models"][model_id]["context_usable"]["tokens"])


def _client_reason(client_state, client):
    """The sign-in filter's reason for `client`, or None when it passes.

    A client absent from `client_state` is not installed; an installed client
    whose ``signed_in`` is explicitly False is not signed in. ``signed_in``
    None (no probe) does not remove a route -- only a measured False does.
    """
    entry = (client_state or {}).get(client) or {}
    if not entry.get("installed"):
        return "client: %s not installed" % client
    if entry.get("signed_in") is False:
        return "client: %s not signed in" % client
    return None


def filter_routes(card, features, client_state, registry, overlay,
                  client="opencode"):
    """Split routes into ``(survivors, removed)`` per spec 5.3 step 1.

    ``survivors`` is route ids in registry order. ``removed`` maps a route id
    to every reason that removed it -- one per failing filter, collecting all
    of them rather than stopping at the first. Filters are never relaxed; an
    override runs later, over exactly these survivors.
    """
    if "need_tokens" not in features:
        raise ValueError("missing feature 'need_tokens' in features")
    need = features["need_tokens"]
    privacy_sensitive = card.get("privacy") == "sensitive"
    agentic = card.get("kind") in AGENTIC_KINDS
    client_reason = _client_reason(client_state, client)

    survivors = []
    removed = {}
    for route_id, route in registry["routes"].items():
        legs = serving_legs(route, registry)
        reasons = []

        if route.get("retired"):
            reasons.append("retired")

        if not legs:
            reasons.append("no available leg")

        if privacy_sensitive:
            for provider_id, model_id in legs:
                if registry["providers"][provider_id].get("trains_on_prompts"):
                    reasons.append("privacy: %s/%s trains on prompts"
                                   % (provider_id, model_id))

        for provider_id, model_id in legs:
            usable = usable_context(model_id, registry, overlay)
            if need * 1.3 > usable:
                reasons.append("context: need %sx1.3 > usable %s on %s/%s"
                               % (need, usable, provider_id, model_id))

        if agentic:
            for leg, provider_id, model_id in _serving_legs_raw(route, registry):
                value = _tool_calls_value(leg, model_id, registry, overlay)
                if value != "proven":
                    reasons.append("tool_calls: %s/%s is %s"
                                   % (provider_id, model_id, value))

        if client_reason:
            reasons.append(client_reason)

        for provider_id, model_id in legs:
            bound = registry["models"][model_id].get("client_bound")
            if bound and bound != client:
                reasons.append("client_bound: %s/%s needs %s"
                               % (provider_id, model_id, bound))

        if reasons:
            removed[route_id] = reasons
        else:
            survivors.append(route_id)

    return survivors, removed


def apply_override(card, survivors, removed):
    """``(route_id, reason)`` for a card's override, after the hard filters.

    No ``route`` key -> ``(None, "no override")``. A surviving route is
    returned; a removed route is refused with every reason it failed; an
    unknown route id is refused as unknown. An override never resurrects a
    filtered route.
    """
    override = card.get("override") or {}
    if "route" not in override:
        return None, "no override"
    route = override["route"]
    if route in survivors:
        return route, "override: %s" % route
    if route in removed:
        return None, "override %s removed by filters: %s" % (
            route, "; ".join(removed[route]))
    return None, "override %s: unknown route" % route


def no_route(removed):
    """The ``input_required`` plan when no route survives the filters (5.3)."""
    return {
        "route": None,
        "state": "input_required",
        "reason": "no route survives the filters: " + " | ".join(
            "%s: %s" % (route_id, "; ".join(reasons))
            for route_id, reasons in removed.items()),
        "hints": list(_NO_ROUTE_HINTS),
    }


# ---------------------------------------------------------------------------
# Expected-cost scoring and theta picking (spec sections 5.3 steps 4-5, 5.4).
# Pure: the caller passes the track record in, nothing is read or written. A
# missing policy key is never defaulted: an unpriced or unseeded route must
# raise, not be scored as if it were safe.
# ---------------------------------------------------------------------------


def _policy_value(registry, *keys):
    """Walk ``registry["policy"]`` by ``keys``; ValueError names a missing one."""
    node = registry.get("policy")
    path = "policy"
    if not isinstance(node, dict):
        raise ValueError("missing policy key %r" % path)
    for key in keys:
        path = "%s.%s" % (path, key)
        try:
            node = node[key]
        except (KeyError, TypeError):
            raise ValueError("missing policy key %r" % path)
    return node


def _median(values):
    """The median of an odd/even list; an even list averages the middle two."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def expected_leg(route, registry):
    """The ``(provider_id, model_id)`` leg that will serve, or None.

    The route's first available leg wins (priority strategy, spec 5.3 step 4).
    """
    legs = serving_legs(route, registry)
    return legs[0] if legs else None


def attempt_cost(model, need_tokens, out_tokens):
    """USD to attempt one run: input at ``price_in``, output at ``price_out``.

    Prices are per token, as the catalog stores them; a free leg is 0.0.
    """
    return (float(need_tokens) * float(model["price_in"])
            + float(out_tokens) * float(model["price_out"]))


def verify_cost(bucket, registry, orchestrator_model):
    """USD to verify once: ``verify_tokens[bucket]`` at the orchestrator's input price.

    Unknown bucket or orchestrator fails closed with a ValueError.
    """
    tokens = _policy_value(registry, "verify_tokens", bucket, "tokens")
    models = registry["models"]
    if orchestrator_model not in models:
        raise ValueError("unknown orchestrator model %r" % orchestrator_model)
    return float(tokens) * float(models[orchestrator_model]["price_in"])


def latency_minutes(route_id, route_class, records, registry):
    """``(minutes, source)`` wall-clock for this route.

    The median of ``latency_s/60`` over the route's own records (source
    ``"route"``); with none, the class's ``latency_seed`` (source ``"seed"``).
    """
    samples = [r["latency_s"] / 60.0 for r in records
               if r.get("route") == route_id]
    if samples:
        return _median(samples), "route"
    seed = _policy_value(registry, "latency_seed", route_class, "minutes")
    return float(seed), "seed"


def score_route(route_id, bucket, effort_name, features, registry, records,
                orchestrator_model, mode):
    """Score one surviving route at a bucket/effort, per spec 5.3 step 4.

    Returns a dict::

        {route, leg, p, p_source, c_attempt, c_verify, minutes,
         expected_cost, reason}

    ``expected_cost`` is ``(C_attempt + C_verify) / p + lambda_mode * T / p``.
    A missing policy key, an unscored route with no serving leg, or a missing
    ``need_tokens`` raises ValueError.
    """
    if "need_tokens" not in features:
        raise ValueError("missing feature 'need_tokens' in features")
    route = registry["routes"][route_id]
    route_class = route["class"]
    leg = expected_leg(route, registry)
    if leg is None:
        raise ValueError("route %r has no serving leg to score" % route_id)
    provider_id, model_id = leg
    model = registry["models"][model_id]

    reasoning = bool(model.get("reasoning"))
    if reasoning:
        out_tokens = max_tokens(effort_name, reasoning, model["output_max"])
    else:
        out_tokens = model["output_max"]

    c_attempt = attempt_cost(model, features["need_tokens"], out_tokens)
    c_verify = verify_cost(bucket, registry, orchestrator_model)
    minutes = latency_minutes(route_id, route_class, records, registry)[0]
    p, p_source = track.p_success(records, route_id, route_class, bucket,
                                  effort_name or "none",
                                  _policy_value(registry, "seed_priors"))
    lam = _policy_value(registry, "modes", mode, "lambda", "value")
    expected = (c_attempt + c_verify) / p + lam * minutes / p

    leg_name = "%s/%s" % (provider_id, model_id)
    reason = "E=%.6f p=%.2f (%s) T=%gm on %s" % (
        expected, p, p_source, minutes, leg_name)
    return {
        "route": route_id,
        "leg": leg_name,
        "p": p,
        "p_source": p_source,
        "c_attempt": c_attempt,
        "c_verify": c_verify,
        "minutes": minutes,
        "expected_cost": expected,
        "reason": reason,
    }


def pick(scores, mode, registry):
    """``(score, reason)`` for the route to run, per spec 5.3 step 5.

    The lowest ``expected_cost`` among scores whose ``p >= theta_mode`` (ties
    keep input order); if none reaches theta, the highest ``p`` (ties take the
    lowest cost) and the reason says theta was missed. Empty ``scores`` or an
    unknown mode raises ValueError.
    """
    if not scores:
        raise ValueError("pick: no scores to choose from")
    theta = _policy_value(registry, "modes", mode, "theta", "value")

    eligible = [s for s in scores if s["p"] >= theta]
    if eligible:
        chosen = min(eligible, key=lambda s: s["expected_cost"])
        return chosen, "theta %s met: lowest E=%.6f on %s" % (
            theta, chosen["expected_cost"], chosen["route"])

    best = None
    for score in scores:
        key = (score["p"], -score["expected_cost"])
        if best is None or key > best[0]:
            best = (key, score)
    chosen = best[1]
    return chosen, "theta %s missed: best p %s on %s" % (
        theta, chosen["p"], chosen["route"])


# ---------------------------------------------------------------------------
# Time tie-break and defer (spec sections 5.3 step 6 and 6.4). Pure: `now` is
# passed in, never read from the clock; provider windows come from the
# registry. Time never overrides a hard filter (D4) -- these functions only
# order or delay candidates that already survived filtering and scoring.
# Quota headroom (the other half of step 6's tie-break) is not measured
# anywhere yet, so it plays no part here: this is price-window only, and a
# route is never preferred on invented headroom data.
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_DEADLINE_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})T"
    r"(?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?Z$")


def _minutes_of_day(hhmm):
    """Minutes since 00:00 for an "HH:MM" string."""
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def _window_contains(window, now):
    """True when `now` falls in `window`'s days and [utc_from, utc_to).

    "23:59" as `utc_to` means to midnight (1440), so 23:59 itself is inside.
    """
    if _WEEKDAY_NAMES[now.weekday()] not in window["days"]:
        return False
    start = _minutes_of_day(window["utc_from"])
    end_str = window["utc_to"]
    end = 1440 if end_str == "23:59" else _minutes_of_day(end_str)
    minute = now.hour * 60 + now.minute
    return start <= minute < end


def price_factor(provider_id, registry, now):
    """The price_factor of the first window of `provider_id` containing `now`.

    1.0 when the provider has no windows, or none of its windows cover `now`.
    """
    now = now.astimezone(timezone.utc)  # windows are UTC (spec 6.4)
    windows = registry["providers"].get(provider_id, {}).get("windows") or []
    for window in windows:
        if _window_contains(window, now):
            return float(window["price_factor"])
    return 1.0


def next_cheap_start(provider_id, registry, now, horizon_hours=48):
    """The first time strictly after `now` where `provider_id` turns cheap.

    Exact, not sampled: the candidates are the start minutes of the
    provider's windows on each day within `horizon_hours`; the earliest one
    whose price_factor (first matching window wins) is below 1.0 is it. None
    when the provider is already cheap at `now`, has no windows, or does not
    turn cheap again within the horizon. `now` is read in UTC.
    """
    now = now.astimezone(timezone.utc)
    if price_factor(provider_id, registry, now) < 1.0:
        return None
    windows = registry["providers"].get(provider_id, {}).get("windows") or []
    if not windows:
        return None

    deadline = now + timedelta(hours=horizon_hours)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    best = None
    for day in range(int(horizon_hours // 24) + 2):
        base = midnight + timedelta(days=day)
        for window in windows:
            if _WEEKDAY_NAMES[base.weekday()] not in window["days"]:
                continue
            start = base + timedelta(minutes=_minutes_of_day(window["utc_from"]))
            if not (now < start < deadline):
                continue
            if price_factor(provider_id, registry, start) < 1.0:
                if best is None or start < best:
                    best = start
    return best


def _leg_provider(leg):
    """The provider id half of a "<provider>/<model>" leg string."""
    return leg.partition("/")[0]


def _format_iso_z(dt):
    """`dt` (always on a whole minute here) as "YYYY-MM-DDTHH:MMZ" in UTC."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def tie_break(scores, registry, now):
    """(score, reason) tie-break by provider price window, spec 5.3 step 6.

    `best` is the score with the lowest `expected_cost`; `candidates` are the
    scores within 10% of it (input order kept). The first candidate whose leg
    provider is in a cheap window (`price_factor < 1.0`) at `now` wins; with
    none, `best` is kept. Quota headroom (the other half of step 6) is not
    measured anywhere yet, so it is not considered here -- this tie-break is
    price-window only, never invented data. Empty `scores` raises ValueError.
    """
    if not scores:
        raise ValueError("tie_break: no scores to choose from")

    best = min(scores, key=lambda s: s["expected_cost"])
    threshold = best["expected_cost"] * 1.10
    candidates = [s for s in scores if s["expected_cost"] <= threshold]

    for score in candidates:
        factor = price_factor(_leg_provider(score["leg"]), registry, now)
        if factor < 1.0:
            reason = ("time: %s in cheap window (x%g) within 10%% of best"
                      % (score["route"], factor))
            return score, reason

    return best, "time: no cheaper window within 10%"


def defer_until(card, chosen_score, registry, now):
    """(datetime or None, reason) opt-in defer for a chosen route, spec 5.3/6.4/D4.

    Only considered when `card["deferrable"]` is true and `card["deadline"]`
    is set; otherwise `(None, "not deferrable")`. Blocking work never waits
    (D4): a card that is not marked deferrable is never delayed here. When
    deferrable, `next_cheap_start` is asked about the chosen leg's provider;
    if it returns a time before the deadline, that is the defer time, else
    `(None, "no defer: ...")` names why (already cheap, no window in the
    horizon, or the next window starts too late).
    """
    if not (card.get("deferrable") and card.get("deadline")):
        return None, "not deferrable"

    deadline_str = card["deadline"]
    match = _DEADLINE_RE.match(deadline_str)
    if not match:
        raise ValueError(
            "deadline %r: expected an ISO 8601 UTC string" % deadline_str)
    groups = match.groupdict()
    deadline = datetime(
        int(groups["y"]), int(groups["mo"]), int(groups["d"]),
        int(groups["h"]), int(groups["mi"]), int(groups["s"] or 0),
        tzinfo=timezone.utc)

    provider_id = _leg_provider(chosen_score["leg"])
    start = next_cheap_start(provider_id, registry, now)

    if start is not None and start < deadline:
        return start, "defer: %s cheap from %s before deadline %s" % (
            provider_id, _format_iso_z(start), deadline_str)

    if start is None:
        if price_factor(provider_id, registry, now) < 1.0:
            why = "%s is already cheap" % provider_id
        else:
            why = "%s has no cheaper window within the horizon" % provider_id
    else:
        why = "%s's next cheap window (%s) is not before deadline %s" % (
            provider_id, _format_iso_z(start), deadline_str)
    return None, "no defer: %s" % why


# ---------------------------------------------------------------------------
# plan(): the resolver v2 entry point (spec sections 5 intro, 5.3 steps 1-7,
# 5.5, 5.7). Pure: it only composes the functions above, no I/O, no clock of
# its own -- `now` is passed in. A missing `card["kind"/"mode"/"risk"]` fails
# closed here; every other required key is checked by the function that reads
# it (filter_routes, bucket, pick, ...).
# ---------------------------------------------------------------------------

_REQUIRED_CARD_KEYS = ("kind", "mode", "risk")

# free < cheap < mid < frontier (spec 3.1); used to find "the next class up".
_CLASS_ORDER = ("free", "cheap", "mid", "frontier")

# D2: normal risk gets 1 cross-family API review, high risk gets 2 plus a
# Sonnet close.
_REVIEW_COUNTS = {"normal": 1, "high": 2}
# The high-risk closer is a Claude client run (Agent-tool Sonnet), not a
# registry route, so it has its own key beside the reviewer routes.
_CLOSER = {"client": "claude", "model": "sonnet"}


def _leg_model_id(leg):
    """The model id half of a "<provider>/<model>" leg string."""
    return leg.partition("/")[2]


def _model_family(leg, registry):
    """The ``family`` of the model half of a "<provider>/<model>" leg string."""
    return registry["models"][_leg_model_id(leg)].get("family")


def _next_rung(ladder, current):
    """The rung immediately above `current` on `ladder`; None at or past the top."""
    if current is None or current not in ladder:
        return None
    index = ladder.index(current)
    if index + 1 >= len(ladder):
        return None
    return ladder[index + 1]


def _score_candidates(route_ids, bucket_name, kind, features, registry,
                      track_record, orchestrator_model, mode):
    """``score_route`` (with ``effort`` kept on it) for each id, in order.

    Mirrors spec 5.3 step 4: for every route, its expected serving leg decides
    the effort rung, then the route is scored at that rung.
    """
    out = []
    for route_id in route_ids:
        route = registry["routes"][route_id]
        leg = expected_leg(route, registry)
        if leg is None:
            raise ValueError("route %r has no serving leg to score" % route_id)
        _, model_id = leg
        model = registry["models"][model_id]
        eff = effort(bucket_name, kind, route["class"], model["effort_ladder"],
                     model["reasoning"])
        score = dict(score_route(route_id, bucket_name, eff, features, registry,
                                 track_record, orchestrator_model, mode))
        score["effort"] = eff
        out.append(score)
    return out


def _select_reviewers(scores, survivors, chosen, risk, bucket_name, kind,
                      features, registry, track_record, orchestrator_model,
                      mode):
    """``{"routes": [...], "reason": ...}`` reviewer routes, spec 5.7 / D2.

    Candidates come from the already-scored routes when there is more than
    the chosen one to pick from; an override that scored only the chosen
    route falls back to scoring every survivor, so a pinned route never
    starves review of candidates. Reviewers are picked cheapest-first, each
    one's serving leg family differing from the chosen leg's and from every
    reviewer already picked -- a reviewer is never the writer's family twice
    over. Too few distinct families still returns what was found, naming the
    shortfall in ``reason`` rather than silently reviewing with fewer eyes.
    """
    try:
        needed = _REVIEW_COUNTS[risk]
    except KeyError:
        raise ValueError("unknown card risk %r" % (risk,))

    pool = scores if len(scores) > 1 else _score_candidates(
        survivors, bucket_name, kind, features, registry, track_record,
        orchestrator_model, mode)

    chosen_family = _model_family(chosen["leg"], registry)
    ranked = sorted(
        (s for s in pool if s["route"] != chosen["route"]),
        key=lambda s: s["expected_cost"])

    picked = []
    excluded = {chosen_family}
    for score in ranked:
        family = _model_family(score["leg"], registry)
        if family in excluded:
            continue
        picked.append(score["route"])
        excluded.add(family)
        if len(picked) == needed:
            break

    if len(picked) < needed:
        reason = "only %d of %d distinct-family reviewer(s) available: %s" % (
            len(picked), needed, ", ".join(picked) if picked else "none")
    else:
        reason = "cross-family reviewer(s): %s" % ", ".join(picked)

    closer = dict(_CLOSER) if risk == "high" else None
    return {"routes": list(picked), "closer": closer, "reason": reason}


def _escalation(chosen, scores, registry):
    """The two escalation steps, spec 5.7.

    ``logic`` raises effort one rung on the chosen leg's ladder; ``capability``
    moves up a route class. Both look only at the routes actually scored in
    this plan (never at unscored survivors): an override that narrowed
    scoring to one route correctly reports no capability escalation rather
    than inventing one from routes nobody costed.
    """
    model = registry["models"][_leg_model_id(chosen["leg"])]
    ladder = model["effort_ladder"]
    logic_step = {
        "on": "logic",
        "route": chosen["route"],
        "effort": _next_rung(ladder, chosen["effort"]),
    }

    chosen_class = registry["routes"][chosen["route"]]["class"]
    next_class = None
    if chosen_class in _CLASS_ORDER:
        index = _CLASS_ORDER.index(chosen_class)
        if index + 1 < len(_CLASS_ORDER):
            next_class = _CLASS_ORDER[index + 1]

    capability_route = None
    if next_class is not None:
        up = [s for s in scores
              if registry["routes"][s["route"]]["class"] == next_class]
        if up:
            capability_route = min(up, key=lambda s: s["expected_cost"])["route"]

    capability_step = {"on": "capability", "route": capability_route}
    return [logic_step, capability_step]


def plan(card, features, client_state, registry, overlay, track_record,
        orchestrator_model, now, client="opencode"):
    """The resolver v2 entry point: compose the pure functions into a ``route_plan``.

    Pure -- no I/O, no clock of its own; `now` is the caller's clock reading.
    Spec 5 (intro), 5.3 steps 1-7, 5.5, 5.7:

    1. filter, 2. bucket, 3. an override scores only its route (fail closed on
    a removed/unknown one), 4. score every remaining route, 5. pick the
    cheapest above theta (tie-broken by time when theta was met -- time never
    beats reliability), 6. an opt-in defer by provider window, 7. emit the
    plan with reviewers, escalation and one explain line per scored route.

    A missing ``card["kind"/"mode"/"risk"]`` raises ValueError naming it.
    """
    for key in _REQUIRED_CARD_KEYS:
        if key not in card:
            raise ValueError("missing card key %r" % key)

    survivors, removed = filter_routes(card, features, client_state, registry,
                                       overlay, client)
    bucket_name, _ = bucket(features, card)

    if not survivors:
        result = no_route(removed)
        result["bucket"] = bucket_name
        return result

    override_route, override_reason = apply_override(card, survivors, removed)
    if override_route is None and override_reason != "no override":
        return {
            "route": None,
            "state": "input_required",
            "reason": override_reason,
            "bucket": bucket_name,
        }

    route_ids = [override_route] if override_route else survivors
    scores = _score_candidates(route_ids, bucket_name, card["kind"], features,
                               registry, track_record, orchestrator_model,
                               card["mode"])

    chosen, pick_reason = pick(scores, card["mode"], registry)
    reason_parts = [pick_reason]

    theta = _policy_value(registry, "modes", card["mode"], "theta", "value")
    eligible = [s for s in scores if s["p"] >= theta]
    if eligible:
        chosen, time_reason = tie_break(eligible, registry, now)
        reason_parts.append(time_reason)

    defer_time, defer_reason = defer_until(card, chosen, registry, now)
    reason_parts.append(defer_reason)

    model_id = _leg_model_id(chosen["leg"])
    model = registry["models"][model_id]
    route_class = registry["routes"][chosen["route"]]["class"]

    reviewers = _select_reviewers(scores, survivors, chosen, card["risk"],
                                  bucket_name, card["kind"], features,
                                  registry, track_record, orchestrator_model,
                                  card["mode"])
    escalation = _escalation(chosen, scores, registry)

    return {
        "route": chosen["route"],
        "class": route_class,
        "client": client,
        "leg": chosen["leg"],
        "effort": chosen["effort"],
        "max_tokens": max_tokens(chosen["effort"], model["reasoning"],
                                 model["output_max"]),
        "context_budget": usable_context(model_id, registry, overlay),
        "bucket": bucket_name,
        "decompose": bucket_name in ("S3", "S4"),
        "p": chosen["p"],
        "expected_cost": chosen["expected_cost"],
        "reviewers": reviewers,
        "escalation": escalation,
        "state": "deferred" if defer_time is not None else "ready",
        "defer_until": _format_iso_z(defer_time) if defer_time is not None else None,
        "reason": "; ".join(reason_parts),
        "explain": [s["reason"] for s in scores],
    }
