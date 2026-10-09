"""Launch-time routing: a task card -> one OmniRoute combo (ADR 0006).

The one decision point for "which model runs this". tools/autoos-agent.py and
tools/autoos_agent_mcp.py both import select_combo; nothing else picks a combo.
Pure: no I/O, no clock, no network. The gateway still does runtime failover
inside the chosen combo - this never reacts to a 429.

Card v1 (unknown fields and values are an error):

    role        orchestrate | implement | review     default implement
    complexity  trivial | standard | hard            default standard
    ctx         128k | 1m                            default 128k
    privacy     public | sensitive                   default public
    spend       free-ok                            default free-ok

Resolution is filters, then preference: privacy, ctx, spend, then
role/complexity. ctx=1m routes to l1-orchestrator again since 2026-09-27
(T1FREE): the route keeps a free gemini/gemini-3.8-flash fallback leg while
OpenRouter/DeepSeek credit is out (L0 2026-09-27T16:00:38Z every tier keeps
a free leg). Sensitive 1m still has no route: l1-orchestrator-clean's only
leg is a contributor model that trains on prompts and stays off, so it is
reachable only through an explicit allow_training, which the caller logs -
but that leg is provider-off too, so allow_training is retained for
compatibility and is inert. spend no longer accepts credit (DEADROWS,
2026-10-08): the -credit chains were dropped 2026-09-23, no -credit combo is
left in ALL_COMBOS and the spawner's TIERS has no credit tier, so a
spend=credit card could only ever resolve to a combo that ignores the request.
Refusing it at parse time is honest; a card that wants a paid leg says so by
role/complexity and gets l2-worker / l3-driver, which already overflow to their
paid legs.
"""
from __future__ import annotations

import datetime
import json
import re

ROUTING_VERSION = "1"

CARD_VALUES = {
    "role": ("orchestrate", "implement", "review"),
    "complexity": ("trivial", "standard", "hard"),
    "ctx": ("128k", "1m"),
    "privacy": ("public", "sensitive"),
    "spend": ("free-ok",),
}
CARD_DEFAULTS = {"role": "implement", "complexity": "standard", "ctx": "128k",
                 "privacy": "public", "spend": "free-ok"}
ALL_COMBOS = ("l1-orchestrator", "l2-worker", "l3-driver",
              "l2-worker-clean", "l3-driver-clean")

# Card v2 (spec docs/plans/2026-09-25-routing-v2-spec.md §4). v1 stays valid:
# role/complexity/ctx/spend map onto kind/bucket_hint/min_context, and privacy
# is shared by both versions. normalize_v2 is additive - normalize/select_combo
# and their results do not change.
#
# D-102 CLAUDEBUDGET (2026-09-28) adds two v2 fields for the Claude budget:
# kind=final names the reserved final review, and critical=true names blocking
# work. Both are the *only* non-deferrable reasons a Claude leg survives the
# hold in tools/autoos_resolver.py; a v1 card cannot say either, so a final
# review is spelled kind=final.
CARD_V2_VALUES = {
    "kind": ("implement", "debug", "review", "plan", "bulk", "research",
             "final"),
    "risk": ("normal", "high"),
    "spec": ("exact", "partial", "vague"),
    "privacy": ("public", "sensitive"),
    "mode": ("cost-first", "balanced", "quality-first"),
    "task_type": ("ops", "code", "docs", "infra"),
}
CARD_V2_DEFAULTS = {"kind": "implement", "risk": "normal", "spec": "partial",
                    "privacy": "public", "mode": "balanced", "deferrable": False,
                    "deadline": None, "paths": [], "override": {},
                    "author": None, "critical": False}
CARD_V1_ONLY = frozenset({"role", "complexity", "ctx", "spend"})
CARD_V2_ONLY = frozenset({"kind", "risk", "spec", "mode", "deferrable",
                          "deadline", "paths", "override", "critical",
                          "task_type"})
# privacy and author belong to both dialects: REVROUTE (S2) item 2 put the
# author on the card so a review can be resolved to a different-family
# reviewer, and role=review is how most lanes already spell a review.
CARD_SHARED = frozenset({"privacy", "author"})
# The shared fields that are not one of v1's five combo inputs. privacy is
# shared AND an input (it hard-filters routes); author is shared and never one
# (it changes who reviews, not what runs) -- so normalize() validates it
# separately instead of choice-checking it against CARD_VALUES.
_CARD_NON_COMBO_SHARED = CARD_SHARED - frozenset(CARD_VALUES)
# The shared fields that are not one of v1's five validated combo inputs.
# privacy is in both sets, so it stays choice-checked by normalize(); author is
# the only member and is checked by _check_author instead.
_CARD_NON_COMBO_SHARED = CARD_SHARED - frozenset(CARD_VALUES)
CARD_V2_KNOWN = CARD_V1_ONLY | CARD_V2_ONLY | CARD_SHARED
OVERRIDE_FIELDS = ("route", "client", "effort")

_V1_ROLE_TO_KIND = {"orchestrate": "plan", "implement": "implement", "review": "review"}
_V1_COMPLEXITY_TO_BUCKET = {"trivial": "S0", "standard": "S2", "hard": "S3"}
_V1_CTX_TO_MIN = {"128k": 128000, "1m": 1000000}
_DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")


class CardError(ValueError):
    """The card has an unknown field or value."""


class NoRoute(ValueError):
    """The card passed validation but no combo satisfies its hard filters."""


def parse_card(text: str) -> dict:
    """`role=review,privacy=sensitive` or a JSON object -> dict (not yet validated)."""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            card = json.loads(text)
        except ValueError as exc:
            raise CardError("card is not valid JSON: %s" % exc)
        if not isinstance(card, dict):
            raise CardError("card JSON must be an object")
        return card
    card = {}
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        key, sep, val = part.partition("=")
        if not sep or not key or not val:
            raise CardError("card entry %r is not field=value" % part)
        card[key.strip()] = val.strip()
    return card


def _check_author(value) -> str:
    """A card's author: a model id, a leg, a route id, or a bare family name.

    Deliberately not validated against the registry here -- autoos_routing is
    the card-shape module and has no registry loaded, and
    ``autoos_resolver.author_family()`` is the one place that resolves those
    spellings (and fails closed on a name it cannot place, REVFIX S2).
    """
    if not isinstance(value, str) or not value.strip():
        raise CardError("card author=%r: expected a non-empty model id, leg or "
                        "family name (e.g. author=qwen)" % (value,))
    return value.strip()


def normalize(card: dict) -> dict:
    unknown = sorted(set(card) - set(CARD_VALUES) - _CARD_NON_COMBO_SHARED)
    if unknown:
        raise CardError("unknown card field(s): %s (v%s knows: %s)"
                        % (", ".join(unknown), ROUTING_VERSION,
                           ", ".join(sorted(set(CARD_VALUES)
                                            | _CARD_NON_COMBO_SHARED))))
    out = dict(CARD_DEFAULTS)
    for key, val in card.items():
        if key in _CARD_NON_COMBO_SHARED:
            # author changes who reviews, never which combo runs: select_combo
            # reads the five CARD_VALUES fields and ignores this one.
            out[key] = _check_author(val)
            continue
        if val not in CARD_VALUES[key]:
            raise CardError("card %s=%r: expected one of %s" % (key, val, ", ".join(CARD_VALUES[key])))
        out[key] = val
    return out


def _v2_defaults() -> dict:
    """A fresh copy of CARD_V2_DEFAULTS - the list/dict values must not alias."""
    return {k: (list(v) if isinstance(v, list) else
                dict(v) if isinstance(v, dict) else v)
            for k, v in CARD_V2_DEFAULTS.items()}


def _check_choice(field: str, value, allowed: tuple) -> str:
    if value not in allowed:
        raise CardError("card %s=%r: expected one of %s"
                        % (field, value, ", ".join(allowed)))
    return value


def _check_bool(field: str, value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise CardError("card %s=%r: expected true or false" % (field, value))


def _check_deferrable(value) -> bool:
    return _check_bool("deferrable", value)


def _check_deadline(value: str) -> str:
    if not isinstance(value, str) or not _DEADLINE_RE.match(value):
        raise CardError("card deadline=%r: expected ISO-8601 UTC like 2026-09-26T06:00Z"
                        % (value,))
    core = value[:-1]  # drop the trailing Z
    fmt = "%Y-%m-%dT%H:%M:%S" if core.count(":") == 2 else "%Y-%m-%dT%H:%M"
    try:
        datetime.datetime.strptime(core, fmt)
    except ValueError as exc:
        raise CardError("card deadline=%r: not a real UTC date: %s" % (value, exc))
    return value


def _normalize_paths(value) -> list:
    if isinstance(value, str):
        parts = value.split("|")  # key=value form: one string, "|"-separated
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise CardError("card paths=%r: expected a list of relative paths" % (value,))
    out = []
    for path in parts:
        if not isinstance(path, str) or not path:
            raise CardError("card paths=%r: expected non-empty string paths" % (path,))
        # Rooted (/x, \\x, \\\\server\\share), drive (C:x) and home (~) paths all
        # leave the repo; only plain relative paths are declared scope.
        if path[0] in "/\\~" or re.match(r"^[A-Za-z]:", path):
            raise CardError("card paths=%r: absolute paths are not allowed" % (path,))
        if any(seg == ".." for seg in re.split(r"[\\/]+", path)):
            raise CardError("card paths=%r: '..' segments are not allowed" % (path,))
        out.append(path)
    return out


def _normalize_override(value) -> dict:
    if not isinstance(value, dict):
        raise CardError("card override=%r: expected an object, or override.route=, "
                        "override.client=, override.effort= in key=value form"
                        % (value,))
    out = {}
    for key, val in value.items():
        if key not in OVERRIDE_FIELDS:
            raise CardError("card override key %r: expected one of %s"
                            % (key, ", ".join(OVERRIDE_FIELDS)))
        if not isinstance(val, str):
            raise CardError("card override.%s=%r: expected a string" % (key, val))
        out[key] = val
    return out


def normalize_v2(card: dict) -> dict:
    """Validate a v1 or v2 task card and return a v2 dict (spec §4).

    A card is v1 when it carries any v1-only field (role/complexity/ctx/spend)
    and v2 otherwise - an empty card is v2 with defaults. v1 fields map to kind,
    bucket_hint and min_context (spend is kept for compatibility); v2 fields are
    validated against CARD_V2_VALUES. Mixing the two families is a CardError
    (privacy belongs to both). The result holds exactly the CARD_V2_DEFAULTS
    keys plus ``version`` - and, for v1 input, bucket_hint/min_context/spend.
    """
    if not isinstance(card, dict):
        raise CardError("card must be an object, got %s" % type(card).__name__)

    # key=value form spells the override as override.route= etc.
    flat, dotted_override = {}, {}
    for key, val in card.items():
        if isinstance(key, str) and key.startswith("override."):
            sub = key[len("override."):]
            if sub not in OVERRIDE_FIELDS:
                raise CardError("card override field %r: expected one of %s"
                                % (sub, ", ".join(OVERRIDE_FIELDS)))
            dotted_override[sub] = val
        else:
            flat[key] = val

    unknown = sorted(set(flat) - CARD_V2_KNOWN)
    if unknown:
        raise CardError("unknown card field(s): %s (v2 knows: %s)"
                        % (", ".join(unknown), ", ".join(sorted(CARD_V2_KNOWN))))

    present = set(flat)
    if dotted_override:
        present.add("override")
    v1_fields = present & CARD_V1_ONLY
    v2_fields = present & CARD_V2_ONLY
    if v1_fields and v2_fields:
        raise CardError("mixes v1 and v2 fields: v1=%s, v2=%s"
                        % (", ".join(sorted(v1_fields)), ", ".join(sorted(v2_fields))))

    out = _v2_defaults()
    if v1_fields:  # v1: validate, map, and add the compat extras
        v1 = dict(CARD_DEFAULTS)
        v1.update({k: v for k, v in flat.items()
                   if k in CARD_V1_ONLY or k in CARD_SHARED})
        for key in ("role", "complexity", "ctx", "privacy", "spend"):
            _check_choice(key, v1[key], CARD_VALUES[key])
        out["kind"] = _V1_ROLE_TO_KIND[v1["role"]]
        out["privacy"] = v1["privacy"]
        out["version"] = "1"
        out["bucket_hint"] = _V1_COMPLEXITY_TO_BUCKET[v1["complexity"]]
        out["min_context"] = _V1_CTX_TO_MIN[v1["ctx"]]
        out["spend"] = v1["spend"]
        if "author" in v1:
            out["author"] = _check_author(v1["author"])
        return out

    for key in ("kind", "risk", "spec", "privacy", "mode", "task_type"):
        if key in flat:
            out[key] = _check_choice(key, flat[key], CARD_V2_VALUES[key])
    if "author" in flat:
        out["author"] = _check_author(flat["author"])
    if "deferrable" in flat:
        out["deferrable"] = _check_deferrable(flat["deferrable"])
    if "critical" in flat:
        out["critical"] = _check_bool("critical", flat["critical"])
    if "deadline" in flat and flat["deadline"] is not None:
        if not out["deferrable"]:
            raise CardError("card deadline=%r: only allowed with deferrable=true"
                            % (flat["deadline"],))
        out["deadline"] = _check_deadline(flat["deadline"])
    if "paths" in flat:
        out["paths"] = _normalize_paths(flat["paths"])
    if "override" in flat:
        out["override"] = _normalize_override(flat["override"])
    if dotted_override:
        out["override"].update(_normalize_override(dotted_override))
    out["version"] = "2"
    return out


def select_combo(card: dict, allow_training: bool = False) -> tuple:
    """Return (combo, reason). Raises CardError or NoRoute.

    ``allow_training`` is accepted for backward compatibility and is inert:
    sensitive ctx=1m's only candidate leg (l1-orchestrator-clean's
    contributor leg) is off at the provider level since 2026-09-27, so there
    is no trainable gateway leg for it to unlock. It never changes the
    result.
    """
    c = normalize(card)
    strong = c["role"] == "orchestrate" or c["complexity"] == "hard"
    light = not strong and (c["role"] == "review" or c["complexity"] == "trivial")

    # 1. privacy - a hard filter; only -clean combos serve sensitive work.
    if c["privacy"] == "sensitive":
        # 2. ctx - the only 1M leg was switched off 2026-09-27 (OpenRouter
        #    off; the Zen leg is client-bound), so 1M fails closed.
        if c["ctx"] == "1m":
            raise NoRoute(
                "privacy=sensitive with ctx=1m has no route: the only 1M leg "
                "(muse-spark-1.3-contributor) was switched off 2026-09-27 "
                "(OpenRouter off; the Zen leg is client-bound). Split the work so "
                "each part fits 128k and run it on l2-worker-clean "
                "(card privacy=sensitive,ctx=128k).")
        # 3. spend - only free-ok parses, so it changes nothing here.
        # 4. role/complexity preference.
        return ("l3-driver-clean", "sensitive-light") if light else ("l2-worker-clean", "sensitive")

    # public
    # ctx=1m routes to l1-orchestrator again (T1FREE, 2026-09-27): the route
    # now carries a free gemini/gemini-3.8-flash fallback leg, so it stays
    # servable while OpenRouter/DeepSeek credit is out.
    if c["ctx"] == "1m":
        return "l1-orchestrator", "public-1m"
    if strong:
        return "l1-orchestrator", "public-strong"
    # 3. spend - spend=credit is refused by normalize() (DEADROWS 2026-10-08: no
    #    -credit combo since 2026-09-23, no credit tier in the spawner's TIERS),
    #    so nothing here branches on it; these combos already overflow to paid.
    return ("l3-driver", "public-light") if light else ("l2-worker", "public-default")
