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
from registry import (resolve_leg, private_safe, unavailable_now,  # tools/ is on sys.path
                      _parse_until, leg_denied, leg_rule_for,
                      plan_dead_reasons, claude_budget as claude_budget_of,
                      context_label_to_tokens, gateway_legs,
                      leg_advertised_context)

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
             "debug": 2, "plan": 3, "final": 0},
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


def _now_or_default(now):
    """`now` when the caller gave one, else the current UTC clock reading.

    The resolver core is pure: only the entry points default ``now``, and only
    so a caller that never knew about ``unavailable_until`` keeps working. One
    reading is threaded down the whole call chain (filter -> usable legs ->
    score -> tie-break), so every leg of one plan is judged at the same
    instant.
    """
    return datetime.now(timezone.utc) if now is None else now


def _serving_legs_raw(route, registry, now=None):
    """Serving legs of `route`, keeping each leg exactly as written in ``legs``.

    Same filtering as `serving_legs` (an ``unavailable_legs`` entry or a
    provider-wide entry that is unavailable at `now` drops it; an unresolvable
    leg raises ValueError). ``now`` defaults to the clock through
    `_now_or_default`. Returns ``(leg, provider_id, model_id)`` so a caller
    that needs the raw leg string too -- e.g. to look it up in the tool_calls
    overlay, an omniroute_id alias included -- does not have to re-derive it
    from the resolved ids (which would lose that alias).
    """
    now = _now_or_default(now)
    providers = registry["providers"]
    unavailable = route.get("unavailable_legs") or {}
    out = []
    for leg in route.get("legs") or []:
        # One leg-resolution rule for the validator and the resolver.
        provider_id, model_id = resolve_leg(leg, registry)
        # `unavailable_now` is the one place that reads `available` and
        # `unavailable_until` (rule R-gateway-12): an entry with a future
        # until is down, one whose until has passed is up again.
        if unavailable_now(unavailable.get(leg), now):
            continue
        if unavailable_now(providers[provider_id], now):
            continue
        out.append((leg, provider_id, model_id))
    return out


def serving_legs(route, registry, now=None):
    """Available ``(provider_id, model_id)`` legs of `route`, in leg order.

    A leg resolves through its provider id (or that provider's omniroute_id)
    and its model id; an ``unavailable_legs`` entry or a provider-wide entry
    that is unavailable at `now` drops it. A leg that resolves to nothing
    raises ValueError: a broken registry must fail closed, never silently drop
    a candidate. ``now`` defaults to the clock through `_now_or_default`.
    """
    return [(provider_id, model_id)
           for _, provider_id, model_id in _serving_legs_raw(route, registry, now)]


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


def _rate_limited(leg, overlay):
    """True when `leg`'s last recorded tool_calls probe error was all 429s.

    Reads ``overlay["legs"][leg]["tool_calls_last_error"]["trials"]``, the
    shape ``tools/probe-toolcalls.py`` writes (``record_verdict``/
    ``run_trial``): a list of trial dicts, each carrying its HTTP ``status``.
    No error on record, or no trials in it, is not rate-limited -- this is
    about a *current* rate limit, not a leg that has simply never been
    probed.
    """
    legs_overlay = (overlay or {}).get("legs") or {}
    last_error = (legs_overlay.get(leg) or {}).get("tool_calls_last_error") or {}
    trials = last_error.get("trials") or []
    if not trials:
        return False
    return all(t.get("status") == 429 for t in trials)


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


# Brief R4FIX / FREEKEYS-2c: the reserve a card estimate is multiplied by before
# it is compared with a leg's window. One name, because a leg that is 5 % short
# on one caller and 30 % short on another is the same bug twice.
CONTEXT_HEADROOM = 1.3


def context_fits(model_id, need_tokens, registry, overlay=None):
    """The resolver's per-card context check: ``need * CONTEXT_HEADROOM`` must fit
    the leg's usable window. ``usable_legs`` gates a leg on this, and a caller
    that wants the same answer asks instead of writing the multiply again.
    """
    return need_tokens * CONTEXT_HEADROOM <= usable_context(model_id, registry,
                                                            overlay)


def route_leg_context_fits(leg, route, registry):
    """Does `leg` carry the context `route` is asked to hand a card?

    The registry invariant this serves (FREEKEYS-2c, rev-freekeys2 finding 2) is
    not the per-card check above -- that one is `context_fits`, gated on a
    *card estimate* with the headroom reserve, and no leg can ever hold 1.3x its
    own window, so feeding a route's promise through it refuses every leg of
    every route. This is the promise half, in the registry's promise currency
    (`context_advertised`, the same number `route_context_cap` clamps a promise
    to): a leg counts toward a route's fallback band only when it advertises at
    least as much as the route is contracted to give, measured as the smaller of

    - what the route declares it sells (`surfaces.omniroute.context_declared`,
      the combos.json label, through `context_label_to_tokens`), and
    - the smallest advertised window among the route's OTHER gateway-servable
      legs -- the band the leg is being counted a member of.

    Leave-the-leg-out is the point: it asks whether THIS leg is the weak link, so
    a 128k leg in a 128k band is a real fallback, while a 32k leg added to the
    same band is not -- it answers only a smaller request than the cards the rest
    of the route serves, which is exactly the leg the invariant used to count as
    usable. A leg or a route with no recorded window is not a deny (no evidence
    never clamps, the rule `leg_advertised_context` already states), and the
    estimate reserve is deliberately not applied here: this compares two declared
    capacities, not a request against a window.
    """
    declared = context_label_to_tokens_route(route)
    others = []
    for other in gateway_legs(route, registry):
        if other == leg:
            continue
        window = leg_advertised_context(other, registry)
        if window is not None:
            others.append(window)
    need = min([w for w in (declared, min(others) if others else None)
                if w is not None], default=None)
    if need is None:
        return True
    window = leg_advertised_context(leg, registry)
    return window is None or window >= need


def context_label_to_tokens_route(route):
    """`routes.<id>.surfaces.omniroute.context_declared` as tokens, or None when
    the route declares no label (an unknown spelling is left alone, never
    guessed)."""
    surfaces = route.get("surfaces")
    omniroute = surfaces.get("omniroute") if isinstance(surfaces, dict) else None
    if not isinstance(omniroute, dict):
        return None
    return context_label_to_tokens(omniroute.get("context_declared"))


def provider_tpm(provider_id, model_id, registry):
    """The per-minute token cap for ``provider_id``'s ``model_id`` leg, or None
    when the provider carries no ``limits`` for that model.

    Brief R4 (2026-09-27): providers.<id>.limits is keyed by the provider's own
    model spelling (the part of a leg after its ``<provider>/`` prefix), which
    is exactly ``model_id`` as resolve_leg returns it. This function reads
    ``tpm`` only -- a request-size filter in usable_legs. ``rpm`` is read
    separately, and only for its zero, by registry.plan_dead_reasons()
    (MISTRALFIX 2026-09-28); rpd/tpd stay data only (no clock, no counters). A
    missing limits table or a missing model key means the leg is not
    size-filtered.

    The estimate compared against tpm is *input only* (review R4FIX,
    2026-09-27): ``need_tokens`` is the brief plus the files, with no output
    reserve. TPM counts input+output, so a leg close to its cap can still 413
    on a long generation even though it passes here.
    """
    provider = (registry.get("providers") or {}).get(provider_id) or {}
    limits = provider.get("limits") or {}
    entry = limits.get(model_id) or {}
    tpm = entry.get("tpm")
    if tpm is None:
        return None
    return int(tpm)


def _client_reason(client_state, client, registry=None, now=None):
    """The client filter's reason for `client`, or None when it passes.

    A client absent from `client_state` is not installed; an installed client
    whose ``signed_in`` is explicitly False is not signed in. ``signed_in``
    None (no probe) does not remove a route -- only a measured False does.

    `registry`'s ``clients.<id>`` entry adds the time-aware outage (brief
    UNTIL, 2026-09-26; rule R-gateway-12):

    - a future ``unavailable_until`` removes the client with the date named
      (``client: <id> unavailable until <date>``);
    - ``available: false`` with no until removes it forever
      (``client: <id> unavailable``);
    - ``available: false`` whose until has passed *stays removed*, but the
      reason says ``re-probe: <id> unavailable_until passed`` -- unlike a
      provider, an own-account client does not come back without a fresh
      sign-in probe, so the operator is told to re-probe rather than the
      route silently returning.

    `now` defaults to the clock through `_now_or_default`.
    """
    entry = (client_state or {}).get(client) or {}
    if not entry.get("installed"):
        return "client: %s not installed" % client
    if entry.get("signed_in") is False:
        return "client: %s not signed in" % client

    now = _now_or_default(now)
    client_entry = ((registry or {}).get("clients") or {}).get(client) or {}
    if unavailable_now(client_entry, now):
        until = client_entry.get("unavailable_until")
        if until is not None:
            return "client: %s unavailable until %s" % (client, until)
        return "client: %s unavailable" % client
    if (client_entry.get("available") is False
            and client_entry.get("unavailable_until") is not None):
        return "re-probe: %s unavailable_until passed" % client
    return None


def _unavailable_reason(entry, name):
    """The hard "unavailable" reason for `entry`, naming `name` and any date.

    ``name`` is the leg string for a route's own ``unavailable_legs`` entry
    and the provider id for a provider-wide entry, so a reader can tell which
    surface dropped the leg. An entry that carries an ``unavailable_until``
    names it (``unavailable: <name> until <date>``); an entry that only says
    ``available: false`` stays down forever (bare ``unavailable``).
    """
    until = entry.get("unavailable_until") if isinstance(entry, dict) else None
    if until is not None:
        return "unavailable: %s until %s" % (name, until)
    return "unavailable"


def _until_passed(entry, now):
    """True when `entry` carries a *parsable* ``unavailable_until`` at or
    before ``now`` -- the "an outage timed out, re-probe it" condition.

    A missing, unparsable or still-future until yields False, so a
    check-failed registry never claims a date "passed" on the strength of a
    value nothing could read (rule 7 reports that value itself; until then
    ``unavailable_now`` falls through to the plain ``available`` flag). A
    naive ``now`` is read as UTC.
    """
    if not isinstance(entry, dict):
        return False
    moment = _parse_until(entry.get("unavailable_until"))
    if moment is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= moment


def _leg_availability(leg, provider_id, unavailable, registry, now):
    """``(hard_reason, re_probe_notes)`` for `leg` at `now`.

    A hard reason drops the leg: its own ``unavailable_legs`` entry or its
    provider is unavailable at `now` (`registry.unavailable_now`). The leg's
    own entry is checked first because it names the leg (``unavailable:
    cheap/fast until ...``); a provider-wide entry names the provider
    (``unavailable: quota until ...``).

    An entry whose ``unavailable_until`` has passed is available again and
    yields no hard reason, only a ``re-probe: <name> unavailable_until passed``
    note for the caller to surface -- a temporary outage that timed out comes
    back on its own, but a probe should confirm it (R-gateway-12).
    """
    entry = unavailable.get(leg)
    if unavailable_now(entry, now):
        return _unavailable_reason(entry, leg), []

    notes = []
    if _until_passed(entry, now):
        notes.append("re-probe: %s unavailable_until passed" % leg)

    provider = registry["providers"][provider_id]
    if unavailable_now(provider, now):
        return _unavailable_reason(provider, provider_id), notes
    if _until_passed(provider, now):
        notes.append("re-probe: %s unavailable_until passed" % provider_id)
    return None, notes


# ---------------------------------------------------------------------------
# Claude budget (D-102 CLAUDEBUDGET, S2 item 2, operator 2026-09-28)
# ---------------------------------------------------------------------------

# The provider ids that are Claude, whatever a model's family row says. `cc` is
# the Claude Code subscription and `anthropic` the direct API; both spellings
# have to hold, or a route renamed onto one of them escapes the budget.
CLAUDE_PROVIDERS = ("cc", "anthropic")

# The clients that ARE Claude: a run inside Claude Code spends the allowance
# whether or not a route leg names it.
CLAUDE_CLIENTS = ("claude",)

# The card kinds that name the reserved final review (the same spelling the
# report line already uses: "AutoOS-Review: kind=final reviewer=sonnet
# verdict=READY"). Naming it is not permission to spend Claude on it: only the
# orchestrator's declaration in `env` is (CLAUDEBUDGET-d item 1).
CLAUDE_BUDGET_ALLOWED_KINDS = ("final",)

# CLAUDEBUDGET-b item 1: the critical-path exception is the ORCHESTRATOR's
# declaration, not the card's. A card field is self-grantable -- every worker
# can edit its own card, so `critical: true` was a permission a worker handed
# itself. An environment variable only the spawning process can set (and which
# the spawner strips from every child, see `autoos-agent.py` CLAUDE_ENV_PREFIX)
# is the difference between a declaration and an excuse.
# Accepted residual (CLAUDEBUDGET-h, finding 1 REJECTED by the operator): the
# declaration is still forgeable by a worker that re-exports `AUTOOS_CLAUDE_*` in
# its own shell, because this is a budget control that rationates an operator's
# credits, not a security fence that denies a hostile principal.
CLAUDE_CRITICAL_ENV = "AUTOOS_CLAUDE_CRITICAL"

# CLAUDEBUDGET-d item 1: the same rule for the `final` exemption. D-102 reserved
# Claude for the finals, and CLAUDEBUDGET-b read that as "kind=final is exempt on
# its own" -- but `kind` is a card field and the card is what the worker that
# wants the model writes. A worker could fork `kind=final` and get a Claude leg,
# the claude client, a Claude reviewer and the high-risk closer, all without
# asking. The final is therefore what the ORCHESTRATOR declares, in the shape the
# declaration is only worth having: which final, on which lane at which sha.
CLAUDE_FINAL_ENV = "AUTOOS_CLAUDE_FINAL"

# CLAUDEBUDGET-b item 5: Claude's own model names. A leg whose model id carries
# one of these is Claude whatever provider it is spelled under -- a proxy, an
# `openrouter/anthropic/*` combo, or a provider that renamed the model. Checked
# on the model id, so a family row that says nothing cannot hide it.
CLAUDE_MODEL_MARKERS = ("claude", "opus", "sonnet", "haiku", "fable")

# What each caller is asking Claude for (item 3). `final` is the exemption;
# every other kind is a spend that needs the declaration.
CLAUDE_USE_KINDS = ("final", "leg", "review", "escalation", "spawn")

CLAUDE_BUDGET_HELD = "claude_budget: %s held for finals"
CLAUDE_BUDGET_HELD_CLIENT = "claude_budget: client %s held for finals"
CLAUDE_BUDGET_OVERRIDE = "claude_budget: critical-path override"
CLAUDE_BUDGET_CRITICAL_NEEDED = ("claude_budget: critical needs the "
                                 "orchestrator's %s" % CLAUDE_CRITICAL_ENV)
CLAUDE_BUDGET_FINAL_NEEDED = ("claude_budget: a final needs the orchestrator's "
                              "%s=<lane>@<sha>" % CLAUDE_FINAL_ENV)
CLAUDE_BUDGET_FINAL_DECLARED = "claude_budget: final declared"


def claude_model_name(model_id) -> bool:
    """Whether a model id names Claude in any of its spellings.

    Case-insensitive: `Sonnet-5` and `sonnet-5` are one model to the gateway and
    must be one verdict to the budget.
    """
    low = str(model_id or "").lower()
    return any(marker in low for marker in CLAUDE_MODEL_MARKERS)


def leg_text(leg, registry) -> str:
    """One leg spelling for the predicates: a `provider/model` string, whether
    the caller has the string from `route["legs"]` or the `(provider_id,
    model_id)` tuple `usable_legs` returns.

    Scores carry the tuple, routes carry the string, and a predicate that only
    accepted one of them silently missed half the legs -- which for a spend
    filter is the side that costs money.
    """
    if isinstance(leg, (tuple, list)):
        return "%s/%s" % (leg[0], leg[1])
    return str(leg)


def is_claude_leg(leg, registry) -> bool:
    """Whether `leg` is a Claude leg (CLAUDEBUDGET-b item 5).

    The name check comes FIRST and reads the raw leg string, not the resolved
    model row, for one reason: a provider the registry does not know --
    `openrouter/anthropic/claude-opus-4-6`, a proxy nobody added yet -- has no
    row to look up, and `resolve_leg` raises on it. An unknown provider is not
    evidence of a non-Claude model, and in budget mode guessing "no" is the side
    that spends the allowance. The rest is the registry's own data: the
    `anthropic` family, and the two provider ids that are Claude by definition.

    A leg whose provider IS known but whose model row is missing still raises
    through resolve_leg, exactly as everywhere else in the resolver: a broken
    registry fails closed.
    """
    text = leg_text(leg, registry)
    model_part = text.partition("/")[2] or text
    if claude_model_name(model_part) or claude_model_name(text):
        return True
    provider_id, model_id = resolve_leg(text, registry)
    if provider_id in CLAUDE_PROVIDERS:
        return True
    model = (registry.get("models") or {}).get(model_id) or {}
    return str(model.get("family") or "").lower() == "anthropic"


def is_claude_client(client) -> bool:
    """Whether running the work *inside* `client` spends the Claude allowance."""
    return client in CLAUDE_CLIENTS


def is_claude_provider(provider_id, registry) -> bool:
    """Whether `provider_id` can only ever answer with Claude.

    CLAUDEBUDGET-b item 2 asks the wait-side question: is a cheap window on this
    provider a window worth waiting for? A provider whose every leg is Claude is
    not -- waiting for it means waiting to run Claude, which is the one thing the
    budget forbids. A provider that also serves non-Claude models (the
    antigravity bridge: Gemini and Claude legs) IS worth waiting for, because
    the held work can run on its non-Claude leg when the window opens.

    So: the two Claude-by-name provider ids always; otherwise every leg the
    registry routes through it must be a Claude leg, and it must have at least
    one. The `>= 1 leg` half matters -- `all([])` is True, and a provider with
    no legs is a provider with no Claude exposure, not a pure-Claude one.
    """
    if provider_id in CLAUDE_PROVIDERS:
        return True
    legs = provider_legs(provider_id, registry)
    return bool(legs) and all(is_claude_leg(leg, registry) for leg in legs)


def provider_legs(provider_id, registry):
    """Every leg string in the registry's routes that resolves to `provider_id`.

    The routes are the only provider -> model association the registry keeps (a
    model row names no provider, because the same model id is served by several),
    so this is what "what can this provider answer with" means. Unresolvable legs
    are skipped rather than raised: this is a survey of the whole graph, and one
    broken leg elsewhere must not hide the provider's other legs.
    """
    out = []
    for route in (registry.get("routes") or {}).values():
        for leg in route.get("legs") or []:
            try:
                found_id, _ = resolve_leg(leg, registry)
            except ValueError:
                continue
            if found_id == provider_id:
                out.append(leg)
    return out


def critical_declaration(env) -> str | None:
    """The orchestrator's critical-path reason, or None.

    Whitespace-only is None: `AUTOOS_CLAUDE_CRITICAL=""` in a shell profile is
    not a declaration about a specific run, and an empty reason would make the
    DONE line cite nothing.
    """
    return _declaration(env, CLAUDE_CRITICAL_ENV)


def final_declaration(env) -> str | None:
    """The orchestrator's declared final ("<lane>@<sha>"), or None.

    The same whitespace rule as `critical_declaration`: a blank `AUTOOS_CLAUDE_FINAL`
    exported once in a profile would otherwise exempt every run the shell ever
    spawns, which is the self-grant item 1 removed, bought back with an env var.
    """
    return _declaration(env, CLAUDE_FINAL_ENV)


def _declaration(env, name) -> str | None:
    value = (env or {}).get(name)
    value = value if isinstance(value, str) else ""
    return value.strip() or None


def claude_allowed(kind, env, registry, now=None, reason=None):
    """``(allowed, reason)`` -- the one Claude gate (CLAUDEBUDGET-b item 3,
    amended by CLAUDEBUDGET-d item 1 and 3).

    Every Claude decision routes through here: the resolver's leg filter, the
    client-bound hold in `filter_routes`, cross-family reviewer selection, the
    escalation ladders, the high-risk closer, and both spawn paths (`autoos-agent.py
    run --client claude` and the MCP `spawn` tool). HEAD had the same policy written
    three times in three shapes, and the shape a caller forgot to copy is the
    hole: reviewer selection was one of them.

    `kind` is what the Claude spend is FOR -- one of `CLAUDE_USE_KINDS`. Nothing is
    exempt on its own any more: a `final` needs the orchestrator's
    `AUTOOS_CLAUDE_FINAL=<lane>@<sha>`, everything else needs
    `AUTOOS_CLAUDE_CRITICAL=<why>`, and a critical declaration opens the final too
    (it declared the bigger thing). A card's own `kind`/`critical` fields are not
    input here, because the card is what a worker writes.

    `reason` is the same authority as an env declaration, one call wide: the MCP
    `spawn` request's `claude_reason` (item 3), so an orchestrator declares the
    single spawn instead of exporting an exception every later caller inherits.
    It comes from the request, never from the card.

    `reason`'s text and the declaration's text both travel in the returned reason,
    which is what goes into the plan and the DONE line, so a Claude run always
    cites what let it happen. `now` is the caller's clock, unused by the rule
    itself and kept in the signature because every caller already threads one -- a
    time-window rule added later must not need the callers changed again.
    """
    if kind not in CLAUDE_USE_KINDS:
        raise ValueError("unknown Claude use %r (one of %s)"
                         % (kind, ", ".join(CLAUDE_USE_KINDS)))
    if not claude_budget_of(registry)["on"]:
        return True, "claude_budget: off - %s may use Claude" % kind
    declared = (str(reason).strip() if reason else "") or critical_declaration(env)
    if kind == "final":
        declared_final = final_declaration(env)
        if declared_final:
            return True, "%s (%s)" % (CLAUDE_BUDGET_FINAL_DECLARED, declared_final)
        if declared:
            return True, "%s (%s)" % (CLAUDE_BUDGET_OVERRIDE, declared)
        return False, CLAUDE_BUDGET_FINAL_NEEDED
    if declared:
        return True, "%s (%s)" % (CLAUDE_BUDGET_OVERRIDE, declared)
    return False, CLAUDE_BUDGET_CRITICAL_NEEDED


def is_final_card(card) -> bool:
    """Whether `card` is the reserved final review (by v2 `kind` or v1 `role`).

    A description of the card, nothing more: it selects which gate question gets
    asked ("is this the spend the budget reserves?"), it never answers it. The
    answer comes from the orchestrator's `AUTOOS_CLAUDE_FINAL` (item 1).
    """
    return (card or {}).get("kind") in CLAUDE_BUDGET_ALLOWED_KINDS or \
        (card or {}).get("role") in CLAUDE_BUDGET_ALLOWED_KINDS


def budget_holds_claude(card, registry, env=None, kind="leg", reason=None) -> bool:
    """Whether the Claude budget is on *and* nothing permits this spend.

    `kind` is what the caller wants Claude for -- "leg", "review", "escalation" or
    "spawn" -- so the answer names the true spend, and a card that says it is a
    final is asked the final question (`is_final_card`), because D-102 reserves the
    model for exactly that. Exempt in exactly two cases, both D-102's wording as
    amended by CLAUDEBUDGET-b and CLAUDEBUDGET-d: the orchestrator declared this
    run's final in `env`, or it declared the run critical path. Neither a card's
    `kind=final` nor its `critical` field alone is an exemption -- both are
    self-grantable.
    """
    allowed, _ = claude_allowed("final" if is_final_card(card) else kind,
                                env, registry, reason=reason)
    return not allowed


def claude_budget_explain(registry) -> list:
    """One line saying what the budget is doing, for `route --explain`.

    The state has to be visible when it is OFF too: an orchestrator reading a
    plan cannot tell "no Claude leg appeared because the budget held it" from
    "this registry has no Claude legs" without the line.
    """
    state = claude_budget_of(registry)
    numbers = ("weekly_share_left=%s, budget_below=%s"
               % (_share_text(state["weekly_share_left"]),
                  _share_text(state["budget_below"])))
    if not state["on"]:
        return ["claude_budget: off (mode=%s, %s) - Claude legs route normally"
                % (state["mode"], numbers)]
    # Which half of the rule flipped it matters to the reader: a share under the
    # threshold says the allowance ran down, mode=budget says the operator said
    # so before it did.
    reason = ("mode" if state["mode"] == "budget"
              else "share below %s" % _share_text(state["budget_below"]))
    return ["claude_budget: ON (%s, %s, by %s) - Claude legs held; only the "
            "orchestrator's %s=<lane>@<sha> (a final) or %s=<why> (critical "
            "path) may use them -- a card's own kind/critical grants nothing "
            "(source: %s)"
            % (state["mode"], numbers, reason, CLAUDE_FINAL_ENV,
               CLAUDE_CRITICAL_ENV, state["source"])]


def _share_text(value) -> str:
    return "null" if value is None else ("%g" % value)


def budget_wait_until(registry, now):
    """When deferrable work may run again: the earliest cheap/off-peak window
    of a provider that is NOT Claude (CLAUDEBUDGET-b item 2).

    Reuses the same window data `defer_until` reads (`providers.<id>.windows`,
    price_factor < 1.0), scanned over every provider rather than one chosen leg
    -- with every Claude leg held there is no chosen leg to ask about.

    A Claude provider's window is skipped on purpose. HEAD read every provider's
    and so produced the one sentence the operator must never see: `wait_until
    21:00`, which is `cc`'s quiet hour -- a deferred card told to wait for the
    moment Claude gets cheap, i.e. told to run on Claude. A wait is only a wait
    if something non-Claude can serve it. When nothing can, the honest answer is
    "free capacity": the work waits for a free leg, and the operator sees why.
    """
    best = None
    for provider_id in sorted(registry.get("providers") or {}):
        if is_claude_provider(provider_id, registry):
            continue
        start = next_cheap_start(provider_id, registry, now)
        if start is not None and (best is None or start < best):
            best = start
    if best is None:
        return "free capacity", "claude_budget"
    return (_format_iso_z(best),
            "claude_budget: Claude held for finals, cheapest window from %s"
            % _format_iso_z(best))


def _claude_budget_removed(removed) -> bool:
    """Whether a no-survivors result was caused by the Claude hold.

    Required before deferring, so a card that failed for an unrelated reason
    (privacy, a client nobody installed) is not reported as "waiting for free
    capacity" when nothing is waiting for it.
    """
    return any("claude_budget" in reason
               for reasons in removed.values() for reason in reasons)


def credit_leg_priced(model_id, registry) -> bool:
    """True when the registry carries a real price for `model_id`.

    ``0`` is not a price (brief FREEKEYS-1b item 3 / rev-freekeys1 finding 3): a row
    with `price_in`/`price_out` of 0 bills $0 through `autoos_usage.paid_spend`, so a
    finite grant reads as untouched money while it drains. `prices_from_registry`
    drops those rows from its table for the same reason, so the ledger and this
    filter never disagree about what counts as priced.
    """
    model = registry["models"].get(model_id) or {}
    try:
        return float(model.get("price_in")) > 0.0 and float(model.get("price_out")) > 0.0
    except (TypeError, ValueError):
        return False


# T0-PAID-2b1 R4a (operator decision D-212): paid legs are the TAIL of every
# route -- standing last-resort legs, used only when no free/trial/credit leg
# of the route is servable. The tier is declared per provider
# (providers.<id>.tier) with an additive model-level override
# (models.<id>.tier): the effective tier is the model override when present,
# else the provider's -- the same reading registry.private_safe uses, so a
# free :free id on a paid provider (and a paid leg on a free provider)
# resolves correctly either way.
_PAID_TIER = "paid"
_FREEISH_TIERS = ("free", "trial", "credit")


def leg_tier(leg, registry):
    """The effective tier of `leg` (model override wins, else the provider's)."""
    provider_id, model_id = resolve_leg(leg, registry)
    model = registry["models"][model_id]
    if "tier" in model:
        return model["tier"]
    return registry["providers"][provider_id].get("tier")


def _hold_back_paid_legs(legs, leg_names, skipped, registry):
    """Drop usable non-free legs while a free/trial/credit leg is usable (R4a).

    `legs`/`leg_names` are parallel (the resolved tuple and the route string
    as written); `skipped` is usable_legs' own map, extended in place. A held
    back leg lands in `skipped` with a held-back reason naming it and its
    tier, so the plan's "falls through" count and explain keep naming what was
    passed over. When no freeish leg is usable the non-free legs stay -- last
    resort, recorded by plan()'s `last_resort` lines instead. Credit/trial
    fail-open behaviour is untouched: an *unusable* credit leg (unpriced,
    exhausted, cooling) is already in `skipped`, so it never blocks a paid
    leg; only a *usable* one does.

    Fail closed on tier (T0-PAID-3 P1): only the known free-ish tiers
    (`_FREEISH_TIERS`) count as free or as healthy blockers. A leg whose tier
    is missing (None) or unrecognised -- "subscription" (the Claude Code /
    antigravity seats, gated elsewhere by the budget and leg rules) included --
    is NON-free: held back exactly like paid while a free-ish leg is usable,
    never counted as healthy, allowed with a last_resort line when every
    free-ish leg is down. The registry's own effective-tier reader fails
    closed the same way (tests/test_registry.py), so the resolver must not
    read an unknown tier as free.
    """
    tiers = [leg_tier(name, registry) for name in leg_names]
    if not any(tier in _FREEISH_TIERS for tier in tiers):
        return legs, skipped
    healthy = ", ".join(name for name, tier in zip(leg_names, tiers)
                        if tier in _FREEISH_TIERS)
    kept = []
    for entry, name, tier in zip(legs, leg_names, tiers):
        if tier in _FREEISH_TIERS:
            kept.append(entry)
        elif tier == _PAID_TIER:
            skipped[name] = ["paid held back: %s is last resort while %s "
                             "is healthy" % (name, healthy)]
        else:
            skipped[name] = ["non-free held back: %s (tier %s) is last "
                             "resort while %s is healthy"
                             % (name, "unknown" if tier is None else tier,
                                healthy)]
    return kept, skipped


def usable_legs(route, card, features, client_state, registry, overlay,
               client="opencode", now=None, env=None, toolcalls_skips=None,
               credit_guards=None, credit_warns=None):
    """``(legs, skipped, re_probe_notes)`` -- FT (fall-through, spec 2026-09-26
    operator decision): the serving legs of `route` that also pass every
    *per-leg* filter, and why each rejected leg did not.

    ``legs`` is ``(provider_id, model_id)`` tuples, same shape as
    ``serving_legs``, in route order. ``skipped`` maps the leg string exactly
    as written in the route (an omniroute_id alias included) to every reason
    that leg did not qualify -- collecting all of them for that leg, never
    stopping at the first, in route order. ``re_probe_notes`` is a parallel
    dict for legs that ARE usable but carry informational re-probe notes
    (a past ``unavailable_until`` that self-healed -- such notes must not
    count as a "skipped leg" in plan()).

    Per-leg filters (replacing the old route-level context/tool_calls/
    client_bound checks, which blocked the whole route on one bad leg):

    - claude_budget (D-102, 2026-09-28): while budget mode is on, a Claude leg
      is held for finals -- reason ``claude_budget: <leg> held for finals`` --
      unless the card is ``kind=final`` or the orchestrator set
      ``AUTOOS_CLAUDE_CRITICAL`` in `env`. One predicate,
      `budget_holds_claude`, over the one gate `claude_allowed`, and
      `is_claude_leg` is the leg half of it. A card's own ``critical`` field is
      not an exemption (it is self-grantable); a held card that claimed it gets
      the reason that names what would unlock it.
    - plan limits (MISTRALFIX, 2026-09-28): a leg the recorded plan cannot
      answer at all is skipped -- ``providers.<id>.limits.<model>.rpm`` of 0
      (reason ``plan: 0 rpm``) or ``plan_available: false`` (reason
      ``plan: plan_available false``), both read by
      `registry.plan_dead_reasons`. A small-but-real quota is *not* dead, and a
      model with no limits row is not gated at all: no measurement is never a
      deny.
    - context: ``context_fits`` -- ``need_tokens * CONTEXT_HEADROOM <=
      usable_context`` -- the one place that math lives, so an invariant test
      can ask the same question instead of restating it (FREEKEYS-2c).
    - tpm (brief R4, 2026-09-27): a leg whose provider limits for that model
      carry ``tpm`` is skipped when ``need_tokens * 1.3 > tpm`` -- a
      request-size cap Groq's free tier enforces (a request above ~8K tokens
      413s there). rpd/tpd are data only for now (no clock, no counters), and a
      non-zero ``rpm`` is no size filter -- only ``rpm: 0`` gates, through the
      plan-limits bullet above.
      Keep-on-equal (review R4FIX): ``need_tokens * 1.3 == tpm`` keeps the leg;
      only a strictly greater need skips it. The estimate is input only --
      ``need_tokens`` is the brief plus the files, no output reserve -- so a
      leg near its cap may still 413 on long outputs even though it passes.
    - tool_calls (agentic kinds only): a value other than ``"proven"``
      (unproven, broken, or no verdict at all) skips the leg.
    - client_bound: a bound leg is always skipped - a route is served through
      the gateway (omniroute/<route>), never from inside the bound client, so
      even `client` == bound gets 403 (run 20260926-142228, R-gateway-03).
    - leg_rules (brief OR1f, 2026-09-27): a leg ``policy.leg_rules`` denies
      (``registry.leg_denied``) is skipped with reason ``leg_rules: <leg>
      denied by <rule id>`` - the same legs ``registry.gateway_legs`` drops,
      since a gateway combo never carries a denied leg.
    - the credit grant (brief FREEKEYS-1b, items 2-3): a leg of a provider whose
      ``tier`` is ``credit`` is a finite amount of the operator's money, so two
      things refuse it. No price on file -- ``credit leg unpriced <model>`` --
      because an unpriced grant bills $0 and would read as an untouched allowance
      while it drains; this half needs no `credit_guards` at all, which is what
      makes it fail closed rather than open. And the guard's own ``refuse`` at
      100 % of ``providers.<id>.monthly_cap_usd`` -- ``credit exhausted <provider>
      $x/$cap`` -- reading the state `autoos_usage.credit_guards` computes from
      recorded usage rows, so the figure the resolver acts on is the figure the
      usage report prints. At the warn line (``monthly_warn_fraction``, 80 % by
      default) the leg stays: there is money left. It is named in `credit_warns`
      so the plan's ``explain`` and the caller's report say so instead of the
      guard being silent until it blocks.
    - an overlay rate limit (agentic kinds only, and only when the leg is
      not already proven): every trial of the leg's last tool_calls probe
      error was HTTP 429. A leg already proven is kept even if currently
      rate-limited -- OmniRoute's own "priority" combo strategy falls
      through to the next leg of the route at run time on a 429; the
      resolver does not need to pre-empt that for a leg it already trusts.

    Availability (brief UNTIL, 2026-09-26) is a *per-leg* filter too, and this
    iterates every leg as written in ``route["legs"]`` -- not the
    `serving_legs` output -- precisely so an unavailable leg can be named in
    ``skipped`` instead of vanishing. A leg whose own ``unavailable_legs``
    entry or whose provider is unavailable at `now`
    (`registry.unavailable_now`) is skipped with reason ``unavailable: <name>
    until <date>`` (or a bare ``unavailable`` when it stays down forever). A
    leg that comes back because its ``unavailable_until`` has passed carries a
    ``re-probe: <name> unavailable_until passed`` note -- informational, it is
    still usable and is appended to ``legs``.

    None of these remove the *route* by themselves -- ``filter_routes`` does
    that only when every leg is unusable (``legs`` comes back empty).
    ``client_state`` is accepted for symmetry with ``filter_routes`` (the
    client installed/signed-in check stays route-level, spec FT point 2; it
    plays no part in a per-leg reason here). `now` defaults to the clock
    through `_now_or_default`.
    """
    now = _now_or_default(now)
    if "need_tokens" not in features:
        raise ValueError("missing feature 'need_tokens' in features")
    need = features["need_tokens"]
    agentic = card.get("kind") in AGENTIC_KINDS
    holds = budget_holds_claude(card, registry, env, kind="leg")
    # The card claimed blocking work but nobody in authority backed the claim,
    # so the plain "held for finals" line would leave the reader asking why a
    # `critical` card was refused. Say what unlocks it.
    critical_untethered = holds and bool(card.get("critical"))
    unavailable = route.get("unavailable_legs") or {}

    legs = []
    leg_names = []
    skipped = {}
    re_probe_notes = {}
    for leg in route.get("legs") or []:
        # One leg-resolution rule for the validator and the resolver. Every
        # leg is resolved, unavailable or not, so a broken registry fails
        # closed here exactly as it does through `serving_legs`.
        provider_id, model_id = resolve_leg(leg, registry)

        hard, notes = _leg_availability(leg, provider_id, unavailable,
                                        registry, now)
        if hard is not None:
            skipped[leg] = [hard]
            continue

        reasons = []

        # D-102 CLAUDEBUDGET (2026-09-28): while the budget is on, Claude is
        # reserved for finals. Held per leg, not per route, so a mixed route
        # keeps its cheap legs instead of being dropped along with the Claude
        # one -- dropping the route would send work to no model that free
        # capacity can serve.
        if holds and is_claude_leg(leg, registry):
            reasons.append(CLAUDE_BUDGET_HELD % leg)
            if critical_untethered:
                reasons.append(CLAUDE_BUDGET_CRITICAL_NEEDED)

        # MISTRALFIX (2026-09-28): the plan itself can veto a leg. Measured on
        # api.mistral.ai with the operator's key, four of its models answer 429
        # at 0 requests/minute and one 403 (not on the plan) -- nothing read
        # `rpm` before, so such a leg was planned as if it answered and the
        # gateway burned the request falling through. One predicate,
        # registry.plan_dead_reasons(), shared with the route-liveness
        # invariant test.
        reasons.extend(plan_dead_reasons(provider_id, model_id, registry))

        if not context_fits(model_id, need, registry, overlay):
            reasons.append("context: need %sx%s > usable %s on %s/%s"
                           % (need, CONTEXT_HEADROOM,
                              usable_context(model_id, registry, overlay),
                              provider_id, model_id))

        # Brief R4 (2026-09-27): a request-size cap from the provider's own
        # limits table -- need * 1.3 > tpm skips the leg, same shape as the
        # context filter. tpm is a per-minute token cap Groq's free tier
        # enforces (a request above ~8K tokens 413s there); rpd/tpd stay
        # data-only for now (no clock, no counters) and a non-zero rpm is no
        # size filter -- only `rpm: 0` gates, through the plan check above.
        tpm = provider_tpm(provider_id, model_id, registry)
        if tpm is not None and need * 1.3 > tpm:
            reasons.append("limit: %s/%s tpm %s < need %s"
                           % (provider_id, model_id, tpm, need))

        value = _tool_calls_value(leg, model_id, registry, overlay)
        proven = value == "proven"
        if agentic and not proven:
            reasons.append("tool_calls: %s/%s is %s"
                           % (provider_id, model_id, value))
            # OVERLAYHOME: a structured record of the skip, so a caller can
            # tell "no overlay" apart without parsing reason text.
            if toolcalls_skips is not None:
                toolcalls_skips.add(leg)

        bound = registry["models"][model_id].get("client_bound")
        if bound:
            reasons.append("client_bound: %s/%s needs %s"
                           % (provider_id, model_id, bound))

        # Brief OR1f (2026-09-27): the gateway renders only legs
        # policy.leg_rules allows (registry.gateway_legs drops every denied
        # leg), so the resolver must not plan a leg no combo serves. One
        # predicate -- leg_denied, the same one gateway_legs calls; leg_rule_for
        # only names the rule in the reason.
        if leg_denied(leg, registry):
            rule = leg_rule_for(leg, registry) or {}
            reasons.append("leg_rules: %s denied by %s"
                           % (leg, rule.get("id", "(unnamed)")))

        # FREEKEYS-1b (items 2-3): a `credit` provider's grant is finite, and the
        # guard has to bite here -- a number the report prints and nothing refuses
        # is what let a $10 grant drain invisibly (rev-freekeys1 findings 2 and 3).
        # Both halves fail closed: no `credit_guards` from the caller means no spend
        # data, and no spend data plus no price is a leg nobody can cost, so the
        # unpriced check refuses it outright.
        if registry["providers"][provider_id].get("tier") == "credit":
            if not credit_leg_priced(model_id, registry):
                reasons.append("credit leg unpriced %s" % model_id)
            guard = (credit_guards or {}).get(provider_id) or {}
            if guard.get("state") == "refuse":
                reasons.append("credit exhausted %s $%.2f/$%.2f"
                               % (provider_id, float(guard.get("spend_usd") or 0.0),
                                  float(guard.get("cap_usd") or 0.0)))
            elif guard.get("state") == "warn" and credit_warns is not None:
                line = "credit warn %s $%.2f/$%.2f" % (
                    provider_id, float(guard.get("spend_usd") or 0.0),
                    float(guard.get("cap_usd") or 0.0))
                # One line per grant per plan, however many of its legs a route
                # carries and however many routes were filtered to get here.
                if line not in credit_warns:
                    credit_warns.append(line)

        if agentic and not proven and _rate_limited(leg, overlay):
            reasons.append("rate_limited: %s/%s (429)" % (provider_id, model_id))

        if reasons:
            skipped[leg] = notes + reasons
        else:
            legs.append((provider_id, model_id))
            leg_names.append(leg)
            if notes:
                re_probe_notes[leg] = notes

    # R4a: non-free legs are last resort within the route -- never selected
    # while a free/trial/credit leg of the same route is usable, wherever the
    # non-free leg is listed (T0-PAID-3 P1: only _FREEISH_TIERS count as free;
    # a missing or unrecognised tier is held back like paid). This also covers reviewer *routes* (_select_reviewers
    # scores through here) for implement and review cards alike. The
    # operator-ordered policy.reviewers walk (reviewer_for) is out of scope:
    # its entries name no route, so "same route class" cannot apply.
    legs, skipped = _hold_back_paid_legs(legs, leg_names, skipped, registry)

    return legs, skipped, re_probe_notes


def filter_routes(card, features, client_state, registry, overlay,
                  client="opencode", now=None, env=None, toolcalls_skips=None,
                  credit_guards=None, credit_warns=None):
    """Split routes into ``(survivors, removed)`` per spec 5.3 step 1, as
    amended by FT (2026-09-26 operator decision): "a leg that is
    rate-limited or unproven makes the combo fall through to the next proven
    leg; it does not block the route. A route is blocked only when NO leg is
    available."

    ``survivors`` is route ids in registry order. ``removed`` maps a route id
    to every reason that removed it -- one per failing filter, collecting all
    of them rather than stopping at the first. Filters are never relaxed; an
    override runs later, over exactly these survivors.

    Route-level filters (unchanged by FT -- these remove the route outright,
    regardless of any leg's own usability): retired; privacy (a
    privacy-sensitive card removed by *any* serving leg that is not
    registry.private_safe -- not just one that trains, but also a free-tier
    pool, per the 2026-09-26 11:05Z operator decision "a training leg is
    never a fallback for sensitive work" as amended by the PRIV finding
    "Free first, private never": a free pool must never be a fallback for a
    private prompt either, so it disqualifies the whole route even when
    another leg of it would otherwise be perfectly usable); client
    installed/signed in (a client marked ``unavailable_until``/``available:
    false`` in ``registry["clients"]`` is folded into that same reason by
    ``_client_reason``).

    Every other filter (context, tool_calls, client_bound, policy.leg_rules,
    the rate-limit overlay, an unavailable leg/provider) is now per-leg
    (``usable_legs``): it
    no longer removes the route by itself. The route is removed only when
    ``usable_legs`` comes
    back with no usable leg at all, with reason ``"no usable leg: " +
    "<leg>: <reasons>" for each skipped leg, joined by "; "`` -- the same
    "no usable leg" reason covers a route with zero serving legs to begin
    with (nothing to list after the colon) as one where every leg was
    individually filtered out.
    """
    if "need_tokens" not in features:
        raise ValueError("missing feature 'need_tokens' in features")
    now = _now_or_default(now)
    privacy_sensitive = card.get("privacy") == "sensitive"
    client_reason = _client_reason(client_state, client, registry, now)
    # D-102 CLAUDEBUDGET: the client half of the same rule. Running the work
    # *inside* Claude Code spends the allowance exactly as a Claude leg does,
    # and no leg filter would catch it -- a client-native run never reads
    # route["legs"]. Route-level, because for a bound client the whole route is
    # that client.
    # CLAUDEBUDGET-d item 3: this is a *spawn*, and it asks with the spawn kind --
    # HEAD asked with the default "leg", so the plan's client hold and the spawn
    # gate answered to two different kinds, and a caller's per-spawn declaration
    # had no kind to arrive under.
    client_held = (is_claude_client(client)
                   and budget_holds_claude(card, registry, env, kind="spawn"))

    survivors = []
    removed = {}
    for route_id, route in registry["routes"].items():
        reasons = []

        if route.get("retired"):
            reasons.append("retired")

        if privacy_sensitive:
            # Every leg the gateway serves, unavailable ones included:
            # unavailable_legs is registry-only and the combo still lists the
            # leg (close-priv 2026-09-26; same rule as registry.py check).
            for leg in route.get("legs") or []:
                provider_id, model_id = resolve_leg(leg, registry)
                safe, why = private_safe(provider_id, model_id, registry)
                if not safe:
                    reasons.append("privacy: %s/%s %s" % (provider_id, model_id, why))

        if client_reason:
            reasons.append(client_reason)

        if client_held:
            reasons.append(CLAUDE_BUDGET_HELD_CLIENT % client)

        usable, skipped, _ = usable_legs(route, card, features, client_state,
                                        registry, overlay, client, now, env,
                                        toolcalls_skips, credit_guards,
                                        credit_warns)
        if not usable:
            reasons.append("no usable leg: " + "; ".join(
                "%s: %s" % (leg, "; ".join(leg_reasons))
                for leg, leg_reasons in skipped.items()))

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
                orchestrator_model, mode, leg=None):
    """Score one surviving route at a bucket/effort, per spec 5.3 step 4.

    Returns a dict::

        {route, leg, p, p_source, c_attempt, c_verify, minutes,
         expected_cost, reason}

    ``expected_cost`` is ``(C_attempt + C_verify) / p + lambda_mode * T / p``.
    A missing policy key, an unscored route with no serving leg, or a missing
    ``need_tokens`` raises ValueError.

    ``leg`` (FT, spec 2026-09-26 operator decision) is the ``(provider_id,
    model_id)`` to score -- the route's first *usable* leg, per
    ``usable_legs``, not merely its first serving one. Left at its default
    ``None``, it falls back to ``expected_leg`` (the first serving leg), so
    an old caller that never knew about per-leg filters keeps working
    unchanged.
    """
    if "need_tokens" not in features:
        raise ValueError("missing feature 'need_tokens' in features")
    route = registry["routes"][route_id]
    route_class = route["class"]
    if leg is None:
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
# decompose(): spec 5.3 step 3 / D7. Pure: it only reads the `whole_plan` and
# `subtask_plans` dicts the caller already produced by calling `plan()` once
# on the whole task and once per orchestrator-proposed subtask card. One level
# deep only -- a subtask plan's own `decompose` flag (true when it is itself
# S3/S4) is never read here, so a subtask is always just costed, never split
# again.
# ---------------------------------------------------------------------------


def decompose(whole_plan, subtask_plans, bucket_name, registry, orchestrator_model):
    """Whether to split into `subtask_plans` instead of running `whole_plan`.

    Only for `bucket_name` in ("S3", "S4"); every other bucket returns
    ``split: False`` without touching `registry` or either plan. A subtask
    plan whose ``route`` is None (no route survived its own filters) fails
    the whole split closed -- naming which subtask and its reason -- since a
    piece that cannot run makes the split worse than the whole task, not
    better.

    ``overhead`` is the cost of the orchestrator writing one extra brief per
    subtask: ``len(subtask_plans) * policy.brief_tokens[bucket_name].tokens *
    registry["models"][orchestrator_model]["price_in"]``. Split when
    ``overhead + sum(subtask expected_cost) < whole_plan["expected_cost"]``.
    A `whole_plan` with no route (``route`` is None, as `no_route()` returns
    it -- with no ``expected_cost`` key at all) counts as infinite cost, so a
    working decomposition always beats it.

    A missing ``policy.brief_tokens[bucket_name]`` or an `orchestrator_model`
    absent from ``registry["models"]`` raises ValueError naming it (the same
    fail-closed style as `verify_cost`).
    """
    if bucket_name not in ("S3", "S4"):
        return {"split": False,
                "reason": "bucket %s: no decompose (S3/S4 only)" % bucket_name}

    if not subtask_plans:
        # review-b5a4: zero proposed subtasks previously fell through to the
        # arithmetic below, where overhead and subtasks_cost are both 0 --
        # cheaper than any positive whole_cost -- and returned split: True
        # into nothing. No subtasks proposed is not a cheaper split.
        return {"split": False, "reason": "no subtasks proposed"}

    for index, subtask in enumerate(subtask_plans, start=1):
        if subtask.get("route") is None:
            return {"split": False,
                    "reason": "subtask %d has no route: %s"
                             % (index, subtask.get("reason"))}

    tokens = _policy_value(registry, "brief_tokens", bucket_name, "tokens")
    models = registry["models"]
    if orchestrator_model not in models:
        raise ValueError("unknown orchestrator model %r" % orchestrator_model)

    overhead = (len(subtask_plans) * float(tokens)
               * float(models[orchestrator_model]["price_in"]))
    subtasks_cost = sum(s["expected_cost"] for s in subtask_plans)
    total = overhead + subtasks_cost

    whole_route = whole_plan.get("route")
    whole_cost = None if whole_route is None else whole_plan["expected_cost"]
    compare_to = whole_cost if whole_cost is not None else float("inf")
    whole_display = "inf" if whole_cost is None else "%.6f" % whole_cost

    split = total < compare_to
    if split:
        reason = "split: %.6f < %s" % (total, whole_display)
    else:
        reason = "keep whole: %.6f >= %s" % (total, whole_display)

    return {
        "split": split,
        "overhead": overhead,
        "subtasks_cost": subtasks_cost,
        "whole_cost": whole_cost,
        "reason": reason,
        "subtasks": [s["route"] for s in subtask_plans],
    }


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

# D2's numbers, kept only as the fallback for a registry that declares no
# policy.review_counts (RISKTIER-a): normal risk gets 1 cross-family API review,
# high risk gets 2 plus a Sonnet close.
_REVIEW_COUNTS = {"normal": 1, "high": 2}
# The high-risk closer is a Claude client run (Agent-tool Sonnet), not a
# registry route, so it has its own key beside the reviewer routes.
_CLOSER = {"client": "claude", "model": "sonnet"}


def _review_policy(risk, registry):
    """`(cross_family_count, final?)` for a risk class (RISKTIER-a).

    `policy.review_counts.<risk>` is what a class costs; the constants above are
    the fallback for a registry that predates the field, so the field's absence
    changes no routing. An unknown class still raises, and a declared count that
    is not a non-negative int raises too -- a resolver that guessed at "1.5
    reviewers" would review less than the policy asked for and say nothing.
    """
    if risk not in _REVIEW_COUNTS:
        raise ValueError("unknown card risk %r" % (risk,))
    entry = (((registry or {}).get("policy") or {}).get("review_counts")
             or {}).get(risk)
    if entry is None:
        return _REVIEW_COUNTS[risk], risk == "high"
    if not isinstance(entry, dict):
        raise ValueError("policy.review_counts.%s is not an object: %r" % (risk, entry))
    count = entry.get("cross_family", _REVIEW_COUNTS[risk])
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("policy.review_counts.%s.cross_family must be an int "
                         ">= 0, got %r" % (risk, count))
    final = entry.get("final", risk == "high")
    if not isinstance(final, bool):
        raise ValueError("policy.review_counts.%s.final must be a boolean, got %r"
                         % (risk, final))
    return count, final


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


def _score_candidates(route_ids, bucket_name, card, features, client_state,
                      registry, overlay, track_record, orchestrator_model,
                      mode, client="opencode", now=None, env=None):
    """``score_route`` (with ``effort`` kept on it) for each id, in order.

    Mirrors spec 5.3 step 4 as amended by FT: for every route, its first
    *usable* leg (``usable_legs`` -- not merely its first serving one)
    decides the effort rung, then the route is scored at that rung on that
    leg. Every ``route_id`` here already survived ``filter_routes``, so
    ``usable_legs`` is never empty for it; a caller that passes one that did
    not gets a ValueError naming it, the same fail-closed shape as before.

    Spec 4's ``override.effort`` replaces the bucket's rung on the leg that
    would answer (DSBACK: it was parsed, validated and then dropped, so a card
    pinning ``effort=max`` silently ran the bucket's rung). It is clamped to
    that leg's ladder exactly like a bucket-derived rung, so a pin cannot
    invent a rung the model lacks and cannot make a non-reasoning leg reason.
    """
    out = []
    for route_id in route_ids:
        route = registry["routes"][route_id]
        legs, _, _ = usable_legs(route, card, features, client_state, registry,
                                overlay, client, now, env)
        if not legs:
            raise ValueError("route %r has no usable leg to score" % route_id)
        leg = legs[0]
        model_id = leg[1]
        model = registry["models"][model_id]
        eff = effort(bucket_name, card["kind"], route["class"],
                    model["effort_ladder"], model["reasoning"])
        pinned = (card.get("override") or {}).get("effort")
        if pinned:
            eff = _clamp(pinned, model["effort_ladder"])
        score = dict(score_route(route_id, bucket_name, eff, features, registry,
                                 track_record, orchestrator_model, mode,
                                 leg=leg))
        score["effort"] = eff
        out.append(score)
    return out


def _select_reviewers(scores, survivors, chosen, card, bucket_name, features,
                      client_state, registry, overlay, track_record,
                      orchestrator_model, mode, client="opencode", now=None,
                      env=None):
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
    risk = card["risk"]
    needed, final = _review_policy(risk, registry)

    pool = scores if len(scores) > 1 else _score_candidates(
        survivors, bucket_name, card, features, client_state, registry,
        overlay, track_record, orchestrator_model, mode, client, now, env)
    # CLAUDEBUDGET-b item 3(b): a reviewer is a model choice, and HEAD made it
    # with no budget check at all -- a non-final card could be handed a Claude
    # reviewer by the route that policy.reviewers listed first. D-102's answer is
    # narrower: a Claude reviewer only for the final it is reserved for (or a
    # run the orchestrator declared critical).
    holds = budget_holds_claude(card, registry, env, kind="review")

    # Compared in family_key form: two legs of one family spelled two ways
    # ("meta", "Meta") are one pair of eyes, not two distinct reviewers.
    chosen_family = family_key(_model_family(chosen["leg"], registry))
    ranked = sorted(
        (s for s in pool if s["route"] != chosen["route"]),
        key=lambda s: s["expected_cost"])

    picked = []
    excluded = {chosen_family}
    for score in ranked:
        # The cap is checked BEFORE the append: a policy that asks for zero
        # cross-family reviewers means zero, not one (the append-then-check shape
        # this replaced returned one — tests/test_autoos_resolver.py).
        if len(picked) >= needed:
            break
        if holds and is_claude_leg(score["leg"], registry):
            continue
        family = family_key(_model_family(score["leg"], registry))
        if family in excluded:
            continue
        picked.append(score["route"])
        excluded.add(family)

    if len(picked) < needed:
        reason = "only %d of %d distinct-family reviewer(s) available: %s" % (
            len(picked), needed, ", ".join(picked) if picked else "none")
    else:
        reason = "cross-family reviewer(s): %s" % ", ".join(picked)

    # CLAUDEBUDGET-d item 1: the closer is a Claude client run, so it is a Claude
    # spend and it asks the final question -- it closes the final review. HEAD
    # emitted it for every risk=high card with no gate at all, which handed a
    # worker that could write `kind=final` a fourth Claude door (client claude,
    # model sonnet) beside the leg, the reviewer and the escalation ladder.
    # CLAUDEBUDGET-f item 4: the gate was only half the answer. The env says an
    # orchestrator declared ONE final; the card says whether THIS plan is a final
    # at all. Asking the env alone gave every risk=high card the closer whenever
    # the variable happened to be set -- so the closer needs both, and a
    # non-budget run keeps the behaviour it always had (off-mode is unchanged).
    # RISKTIER: `final` is policy's answer to *which* tiers close at all, so it
    # replaces the hardcoded risk == "high" HEAD carried here.
    closer = None
    if final and (is_final_card(card) or not claude_budget_of(registry)["on"]):
        allowed, _gate = claude_allowed("final", env, registry)
        closer = dict(_CLOSER) if allowed else None
    return {"routes": list(picked), "closer": closer, "reason": reason}


# ---------------------------------------------------------------------------
# Reviewer routing (brief REVROUTE (S2) item 2, operator 2026-09-27T20:3xZ).
#
# Which client/model reviews is `policy.reviewers`'s business (item 1 put it in
# data); this is the walk. It answers one question -- given the model that WROTE
# the diff, who is allowed to read it -- and the operator's rule is that it must
# not be the author's own family: a model reviewing its own output agrees with
# itself. So the walk takes the first entry that clears four checks (family,
# role in the review, availability, privacy) and reports every entry it passed
# over, with its reasons.
#
# Pure, like the rest of the module: `now` is passed in, the registry is the
# caller's, nothing is read or written.
# ---------------------------------------------------------------------------

# A rejection that says "not this one, ever" as opposed to "not right now".
# Only a temporary rejection can make a run WAIT (state "queued", which the
# spawner turns into SPAWNFREE's rc-9 queue); a structural one is answered by
# picking someone else or by saying there is nobody.
_TEMPORARY_REASONS = ("unavailable", "rate_limited")
# A reason that names a dated outage, whoever produced it. The client filter's
# reason ("client: agy unavailable until 2026-10-01T09:05:00Z") is written by
# _client_reason for the route filter too, so it is matched on its wording
# rather than by re-shaping a string another caller asserts on byte for byte.
# "unavailable_until passed" (the re-probe note) deliberately does NOT match:
# that one says the wait is already over.
_TEMPORARY_MARKER = "unavailable until "


def _is_temporary(reasons):
    """True when every reason in `reasons` says "come back later"."""
    return bool(reasons) and all(
        any(reason.startswith(prefix) for prefix in _TEMPORARY_REASONS)
        or _TEMPORARY_MARKER in reason
        for reason in reasons)


def family_key(value):
    """The comparison form of a family (or any registry spelling): trimmed, casefolded.

    A vendor markets "Meta Muse" while the registry says ``meta``, and a lane
    record quotes whichever the writer saw. Two names are the same family when
    these agree, so EVERY family comparison goes through here. A non-string or a
    blank is its own key (None), never a match for a real name.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold()


def ci_key(mapping, name):
    """The key of `mapping` equal to `name` ignoring case, or None.

    An exact hit short-circuits: ids are compared as written first, so a registry
    that ever carries two ids differing only in case keeps its own precedence.
    """
    if not isinstance(mapping, dict):
        return None
    if name in mapping:
        return name
    key = family_key(name)
    if key is None:
        return None
    for candidate in mapping:
        if isinstance(candidate, str) and family_key(candidate) == key:
            return candidate
    return None


def ci_value(mapping, name):
    """`mapping[name]`, or the value under the key that differs only in case."""
    key = ci_key(mapping, name)
    return None if key is None else mapping[key]


def _ci_leg_model_id(leg, registry):
    """The models key a ``provider/model`` leg names, ignoring case; else None.

    `resolve_leg` is the authority and matches exactly; this is only the retry
    for a spelling a person typed from a client's UI, and it returns None rather
    than inventing a leg the registry does not have.
    """
    prefix, sep, model_id = (leg or "").partition("/")
    if not sep:
        return None
    wanted = family_key(prefix)
    if wanted is None:
        return None
    providers = registry.get("providers") or {}
    provider_id = ci_key(providers, prefix)
    if provider_id is None:
        for pid, provider in providers.items():
            alias = provider.get("omniroute_id") if isinstance(provider, dict) else None
            if family_key(alias) == wanted:
                provider_id = pid
                break
    if provider_id is None:
        return None
    return ci_key(registry.get("models") or {}, model_id)


def registry_families(registry):
    """Every family name the registry itself uses, in comparison form.

    The two places an operator states a family -- ``models.*.family`` and
    ``policy.reviewers[].family``. It answers the one question the author
    spelling cannot settle on its own: is this bare word real shorthand for a
    family ("qwen"), or a model nobody registered ("qwen3.8-flsh")?
    """
    families = set()
    for table in (list((registry.get("models") or {}).values()),
                  list((registry.get("policy") or {}).get("reviewers") or [])):
        for entry in table:
            if isinstance(entry, dict):
                key = family_key(entry.get("family"))
                if key:
                    families.add(key)
    return families


def author_family(author, registry):
    """``(family, why_not)`` for the model that wrote the card.

    An author is accepted in any of the spellings a caller has to hand, and case
    is not part of any of them: a registry model id
    (``muse-spark-1.3-contributor``), a leg
    (``meta_api/muse-spark-1.3-contributor``), a route id (whose head leg is the
    one that took the traffic), one of the operator's reviewer spellings
    (``omniroute/spark-1.3-contributor``), or a bare family name the registry
    already uses (``qwen``).

    REVFIX (S2): a name that resolves none of those ways has NO family, and the
    caller fails closed. It used to be read as a family of its own -- "an author
    from a family nobody registered is cross-family to every reviewer" -- which
    made the rule unenforceable, because a typo or an invented model name is
    cross-family to every reviewer too and so always passed. Not knowing who
    wrote the diff is not evidence that the reviewer is someone else. A model
    that IS registered but carries no ``family`` still fails closed for the same
    reason.
    """
    models = registry.get("models") or {}
    if not isinstance(author, str) or not author.strip():
        return None, "author is empty"
    name = author.strip()

    entry = ci_value(models, name)
    if entry is not None:
        if not isinstance(entry, dict):
            return None, "models.%s is not an object" % name
        key = family_key(entry.get("family"))
        if key:
            return key, None
        return None, "models.%s carries no family" % name

    if "/" in name:
        try:
            _provider_id, model_id = resolve_leg(name, registry)
        except ValueError:
            model_id = _ci_leg_model_id(name, registry)
        if model_id is None:
            # A client's own spelling ("<client>/<model>"), e.g. opencode's
            # "opencode/nemotron-3-ultra-free": the model half is the registry id.
            model_id = ci_key(models, name.rpartition("/")[2])
        if model_id is not None:
            return author_family(model_id, registry)
    else:
        route = ci_value(registry.get("routes") or {}, name)
        if isinstance(route, dict):
            # Legs are a priority order; the first one the registry can place IS
            # the model that ran, the same reading stop_provider_id gives a 429.
            whys = []
            for leg in route.get("legs") or []:
                # Only real ``provider/model`` legs: a bare name here would come
                # back through this branch as a route and could cycle.
                if not isinstance(leg, str) or "/" not in leg:
                    continue
                family, why_not = author_family(leg, registry)
                if family:
                    return family, None
                whys.append(why_not)
            return None, ("routes.%s names no leg with a family (%s)"
                          % (name, "; ".join(whys) or "no legs"))

    for candidate in ((registry.get("policy") or {}).get("reviewers") or []):
        # A client's own model string (qoder's ``qwen3.8-flash``, claude's
        # ``haiku``) is a reviewer spelling, not a registry id, and the operator
        # already stated its family there -- check rule 11 keeps it honest. Read
        # it before the bare-family shorthand: ``haiku`` taken as a family name
        # looks cross-family to every anthropic reviewer.
        if (isinstance(candidate, dict)
                and family_key(candidate.get("model")) == family_key(name)
                and family_key(candidate.get("family"))):
            return family_key(candidate.get("family")), None

    if family_key(name) in registry_families(registry):
        return family_key(name), None
    return None, ("author %s is not a model, leg, route or reviewer the registry "
                  "knows, and not a family it declares" % name)


def _reviewer_rejections(entry, family, registry, client_state, now, risk,
                         privacy):
    """``(reasons, resolved_leg)`` for one reviewer entry; empty reasons = usable.

    Every reason is collected, never just the first: a skipped reviewer that
    reports one of its four problems hides the other three from whoever reads
    ``--explain``, and "why is Muse not reviewing this" is usually answered by
    the second reason, not the first.
    """
    reasons = []
    reviewer_family = entry.get("family")
    if not reviewer_family:
        reasons.append("no family: cannot check the different-family rule")
    elif family_key(reviewer_family) == family:
        # `family` is already in comparison form (author_family normalized it);
        # the entry's is normalized here so an operator's "Meta" is the registry's
        # "meta" and a self-review cannot be spelled into independence.
        reasons.append("same family as author (%s)" % family)

    # Haiku's fixed role (operator 2026-09-27): a fallback FIRST pass only. At
    # high risk the second reviewer closes toward the Sonnet check, and a
    # first-pass-only model has no business in that seat.
    if entry.get("first_pass_only") and risk == "high":
        reasons.append("first-pass only: not eligible for a high-risk review")

    client_reason = _client_reason(client_state, entry.get("client"), registry, now)
    if client_reason:
        reasons.append(client_reason)

    leg = entry.get("leg")
    resolved = None
    if leg:
        provider_id, model_id = resolve_leg(leg, registry)
        resolved = (provider_id, model_id)
        provider = registry["providers"].get(provider_id) or {}
        if unavailable_now(provider, now):
            reasons.append(_unavailable_reason(provider, provider_id))
        bound = (registry["models"].get(model_id) or {}).get("client_bound")
        if bound and bound != entry.get("client"):
            reasons.append("client_bound: %s needs %s, not %s"
                           % (leg, bound, entry.get("client")))
        # Never privacy=sensitive to a training model (operator's fixed rule:
        # Muse trains by contributor contract). private_safe is the one
        # predicate that already encodes "paid AND does not train", so a free
        # pool cannot be a fallback for a private prompt here either.
        if privacy == "sensitive":
            safe, why = private_safe(provider_id, model_id, registry)
            if not safe:
                reasons.append("privacy: %s %s" % (leg, why))
    elif privacy == "sensitive":
        # Fail closed: with no leg there is nothing to check training against,
        # and "unknown" is not "safe" for a private prompt.
        reasons.append("privacy: no registry leg to check training against")
    return reasons, resolved


def reviewer_for(author, registry, client_state, now=None, risk="normal",
                 privacy="public"):
    """The reviewer decision for an authored review card (spec 5.7 + D2).

    Returns ``{author, author_family, reviewer, skipped, state, retry_at,
    reason}``:

    - ``reviewer`` -- the first ``policy.reviewers`` entry that clears the
      family, availability and privacy checks, or None. The list is an ORDERED
      preference, so the walk stops at the first usable entry and ``skipped``
      holds exactly what it passed over, each with all of its reasons.
    - ``state`` -- ``resolved``, ``queued`` (nothing usable, and at least one
      entry the walk passed over is down *temporarily* with a known reset: a
      reviewer will come back), or ``unresolved`` (nobody eligible, and no wait
      will change that -- every rejection was structural, or nothing was down).
    - ``retry_at`` -- the earliest ``unavailable_until`` among the temporary
      rejections; the queue is over when the FIRST reviewer returns, not the
      last.

    ``client_state`` is the same measured dict ``filter_routes`` takes: a
    reviewer that runs on a signed-out client cannot review anything.
    """
    now = _now_or_default(now)
    family, why_not = author_family(author, registry)
    if family is None:
        return {"author": author, "author_family": None, "reviewer": None,
                "skipped": [], "state": "unresolved", "retry_at": None,
                "reason": "cannot resolve the author's family: %s" % why_not}

    reviewers = (registry.get("policy") or {}).get("reviewers")
    if not isinstance(reviewers, list) or not reviewers:
        return {"author": author, "author_family": family, "reviewer": None,
                "skipped": [], "state": "unresolved", "retry_at": None,
                "reason": "policy.reviewers is missing or empty: nobody is "
                          "registered to review (author %s)" % family}

    skipped = []
    waits = []
    for entry in reviewers:
        if not isinstance(entry, dict):
            continue
        reasons, _resolved = _reviewer_rejections(entry, family, registry,
                                                 client_state, now, risk,
                                                 privacy)
        if not reasons:
            model = entry.get("model")
            return {
                "author": author, "author_family": family,
                "reviewer": dict(entry), "skipped": skipped,
                "state": "resolved", "retry_at": None,
                "reason": "reviewer: %s %s (family %s, author %s)"
                          % (entry.get("client"), model, entry.get("family"),
                             family),
            }
        temporary = _is_temporary(reasons)
        skipped.append({"client": entry.get("client"),
                        "model": entry.get("model"),
                        "family": entry.get("family"),
                        "reasons": reasons,
                        "waiting": temporary})
        if temporary:
            waits.extend(_entry_unavailable_until(entry, registry, now))

    if waits:
        retry_at = _format_retry(min(waits))
        return {"author": author, "author_family": family, "reviewer": None,
                "skipped": skipped, "state": "queued", "retry_at": retry_at,
                "reason": "queued until %s: no reviewer is available now, the "
                          "earliest one returns then (author family %s)"
                          % (retry_at, family)}
    return {"author": author, "author_family": family, "reviewer": None,
            "skipped": skipped, "state": "unresolved", "retry_at": None,
            "reason": "no reviewer is eligible for an author from family %s "
                      "(%d candidate(s) rejected)" % (family, len(skipped))}


def _entry_unavailable_until(entry, registry, now):
    """The ISO dates a reviewer entry is down until, parsed to datetimes.

    Reads the same ``unavailable_until`` values the rejection reasons named
    (provider-level for a leg; client-level for a client-native reviewer), so
    ``retry_at`` can never promise a time nothing said. Returns [] when a
    rejection carried no date -- a bare ``available: false`` comes back when an
    operator flips it, not when the clock reaches something.
    """
    moments = []
    leg = entry.get("leg")
    if leg:
        try:
            provider_id, _model_id = resolve_leg(leg, registry)
        except ValueError:
            return moments
        entry_data = registry["providers"].get(provider_id) or {}
    else:
        entry_data = (registry.get("clients") or {}).get(entry.get("client")) or {}
    moment = _parse_until(entry_data.get("unavailable_until"))
    if moment is not None and moment > now:
        moments.append(moment)
    return moments


def _format_retry(moment):
    """An outage instant as the short UTC stamp the plan prints elsewhere."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def reviewer_explain_lines(result):
    """One ``--explain`` line per reviewer the walk passed over, in list order.

    The plan's own ``explain`` block is the operator's answer to "who did NOT
    review this and why"; a reviewer that vanished from the output instead of
    being named is how a mis-registered list reads as a working one.
    """
    return ["reviewer skipped: %s %s (%s)"
            % (entry["client"], entry["model"], "; ".join(entry["reasons"]))
            for entry in (result or {}).get("skipped") or []]


def _escalation(chosen, scores, registry, card=None, env=None):
    """The two escalation steps, spec 5.7.

    ``logic`` raises effort one rung on the chosen leg's ladder; ``capability``
    moves up a route class. Both look only at the routes actually scored in
    this plan (never at unscored survivors): an override that narrowed
    scoring to one route correctly reports no capability escalation rather
    than inventing one from routes nobody costed.

    CLAUDEBUDGET-b item 3(c): the ladder picks a model too, so it asks the same
    gate the legs asked. `card`/`env` are the gate's inputs; a plan that never
    had a Claude question still passes them through.
    """
    holds = budget_holds_claude(card or {}, registry, env,
                                kind="escalation")
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
        # CLAUDEBUDGET-b item 3(c): an escalation ladder is a second place a plan
        # chooses a model, and HEAD checked none of them against the budget -- a
        # card refused a Claude leg came back with a Claude *escalation*. The
        # held candidates are dropped rather than the whole step, so a route that
        # is one rung up and not Claude still escalates.
        if holds:
            up = [s for s in up if not is_claude_leg(s["leg"], registry)]
        if up:
            capability_route = min(up, key=lambda s: s["expected_cost"])["route"]

    capability_step = {"on": "capability", "route": capability_route}
    return [logic_step, capability_step]


def _paid_last_resort_lines(chosen_leg, skipped_legs, registry):
    """Explain lines for R4a (D-212): held-back non-free legs, or the last resort.

    - Every skipped leg held back as paid/non-free-last-resort names itself
      (and, for a non-paid tier, the tier), so the plan says which leg it
      passed over and why.
    - When the chosen leg itself is NON-free (paid, subscription, missing or
      otherwise unrecognised -- anything outside _FREEISH_TIERS), every
      skipped free-ish leg gets one `last_resort` line carrying its existing
      skip reasons -- the record of which free legs were down and why the
      non-free leg was allowed.
    """
    lines = []
    for leg, reasons in skipped_legs.items():
        if any(reason.startswith("paid held back") for reason in reasons):
            lines.append("paid held back: %s held as last resort while a "
                         "free/trial/credit leg is healthy" % leg)
        elif any(reason.startswith("non-free held back")
                 for reason in reasons):
            try:
                tier = leg_tier(leg, registry)
            except ValueError:
                tier = None
            lines.append("non-free held back: %s (tier %s) held as last "
                         "resort while a free/trial/credit leg is healthy"
                         % (leg, "unknown" if tier is None else tier))
    try:
        chosen_tier = leg_tier(chosen_leg, registry)
    except ValueError:
        chosen_tier = None
    if chosen_tier not in _FREEISH_TIERS:
        for leg, reasons in skipped_legs.items():
            try:
                tier = leg_tier(leg, registry)
            except ValueError:
                tier = None
            if tier in _FREEISH_TIERS:
                lines.append("last_resort: %s skipped (%s)"
                             % (leg, "; ".join(reasons)))
    return lines


def plan(card, features, client_state, registry, overlay, track_record,
        orchestrator_model, now=None, client="opencode", env=None,
        credit_guards=None):
    """The resolver v2 entry point: compose the pure functions into a ``route_plan``.

    Pure -- no I/O, no clock of its own; `now` is the caller's clock reading,
    defaulting to the current UTC time when omitted. One reading is threaded
    through filtering, scoring, the tie-break and the defer, so every
    ``unavailable_until`` in the plan is judged at the same instant.
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

    now = _now_or_default(now)
    toolcalls_skips = set()
    # FREEKEYS-1b item 2: a credit grant that has reached its warn line is still
    # usable, so it is not a skipped leg -- it is a line the operator reads. The
    # leg filter fills this accumulator and the plan puts it in `explain`.
    credit_warns = []
    survivors, removed = filter_routes(card, features, client_state, registry,
                                       overlay, client, now, env,
                                       toolcalls_skips, credit_guards,
                                       credit_warns)
    bucket_name, _ = bucket(features, card)

    if not survivors:
        # D-102 CLAUDEBUDGET: deferrable work that lost its last leg to the
        # Claude hold waits for free/off-peak capacity -- it never falls back to
        # Claude, because "the cheap legs are busy" and "there are no cheap
        # legs" are the same sentence to an operator reading a `ready` plan.
        if card.get("deferrable") and _claude_budget_removed(removed):
            wait_until, wait_reason = budget_wait_until(registry, now)
            # CLAUDEBUDGET-b item 4: the deferred dict used to be its own small
            # shape, so a caller that read plan["reviewers"] on every plan it got
            # raised only on a deferred one -- the state that arrives most when
            # the fleet is busy. Same keys as a ready plan, with the values a
            # plan that chose nothing has for them.
            return {
                "route": None,
                "leg": None,
                "state": "deferred",
                "bucket": bucket_name,
                "p": None,
                "theta": None,
                "expected_cost": None,
                "reviewers": [],
                "escalation": [],
                "wait_until": wait_until,
                "reason": wait_reason,
                "explain": claude_budget_explain(registry) + list(credit_warns),
            }
        result = no_route(removed)
        result["bucket"] = bucket_name
        # True when a leg was skipped as not proven for tool calls, so the
        # caller can name a missing overlay without parsing the reason.
        result["unproven_toolcalls"] = bool(toolcalls_skips)
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
    scores = _score_candidates(route_ids, bucket_name, card, features,
                               client_state, registry, overlay, track_record,
                               orchestrator_model, card["mode"], client, now,
                               env)

    chosen, pick_reason = pick(scores, card["mode"], registry)
    reason_parts = [pick_reason]

    # D-102 CLAUDEBUDGET: the Claude spend a plan may still carry is one the
    # ORCHESTRATOR declared -- a critical path, or this run's final -- and the plan
    # has to say so, because a DONE line citing a Claude leg with no authority
    # named looks like a violated policy and the operator cannot tell the two
    # apart without reading the card. The gate's own text (which carries what was
    # declared) is what goes on the plan, so the record cites why, not just that.
    # CLAUDEBUDGET-d item 1: "final declared (<lane>@<sha>)" is an authority line
    # too, not only the critical-path override.
    allowed, gate_reason = claude_allowed("final" if is_final_card(card)
                                          else "leg", env, registry, now)
    if allowed and is_claude_leg(chosen["leg"], registry) and gate_reason.startswith(
            (CLAUDE_BUDGET_OVERRIDE, CLAUDE_BUDGET_FINAL_DECLARED)):
        reason_parts.append(gate_reason)

    theta = _policy_value(registry, "modes", card["mode"], "theta", "value")
    eligible = [s for s in scores if s["p"] >= theta]
    if eligible:
        chosen, time_reason = tie_break(eligible, registry, now)
        reason_parts.append(time_reason)

    # FT (spec 2026-09-26 operator decision): the chosen route's own skipped
    # legs, so a caller can see which legs it fell through past.
    # FUP (2026-09-27): re-probe notes tracked separately so they do not
    # inflate the "falls through" count.
    _, skipped_legs, re_probe_notes = usable_legs(
        registry["routes"][chosen["route"]], card,
        features, client_state, registry, overlay,
        client, now, env, None, credit_guards)
    if skipped_legs:
        reason_parts.append(
            "falls through %d skipped leg(s)" % len(skipped_legs))
    if re_probe_notes:
        for leg, notes in re_probe_notes.items():
            for note in notes:
                reason_parts.append(note)

    defer_time, defer_reason = defer_until(card, chosen, registry, now)
    reason_parts.append(defer_reason)

    model_id = _leg_model_id(chosen["leg"])
    model = registry["models"][model_id]
    route_class = registry["routes"][chosen["route"]]["class"]

    reviewers = _select_reviewers(scores, survivors, chosen, card, bucket_name,
                                  features, client_state, registry, overlay,
                                  track_record, orchestrator_model,
                                  card["mode"], client, now, env)
    escalation = _escalation(chosen, scores, registry, card, env)

    # REVROUTE (S2) item 2: an authored review card also gets the *client/model*
    # that must read the diff, resolved from policy.reviewers. Only a review
    # asks: an implement card with a stray author field must not start a review
    # nobody ordered.
    review = None
    if card.get("kind") == "review" and card.get("author"):
        review = reviewer_for(card["author"], registry, client_state, now,
                              card.get("risk", "normal"),
                              card.get("privacy", "public"))

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
        "review": review,
        "escalation": escalation,
        "state": "deferred" if defer_time is not None else "ready",
        "defer_until": _format_iso_z(defer_time) if defer_time is not None else None,
        "reason": "; ".join(reason_parts),
        "explain": (list(credit_warns) + [s["reason"] for s in scores]
                    + _paid_last_resort_lines(chosen["leg"], skipped_legs,
                                              registry)
                    + reviewer_explain_lines(review)),
        "skipped_legs": skipped_legs,
        "re_probe_notes": re_probe_notes,
    }
