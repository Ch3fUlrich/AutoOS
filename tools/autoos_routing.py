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
    spend       free-ok | credit                     default free-ok

Resolution is filters, then preference: privacy, ctx, spend, then
role/complexity. sensitive + 1m has no route: t1-orchestrator-clean's only leg
is a contributor model that trains on prompts, so it is reachable only through
an explicit allow_training, which the caller logs. spend=credit stays a valid
value but has no combo of its own: the -credit chains were dropped 2026-09-23,
and t2-worker / t3-driver already overflow to their paid legs.
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
    "spend": ("free-ok", "credit"),
}
CARD_DEFAULTS = {"role": "implement", "complexity": "standard", "ctx": "128k",
                 "privacy": "public", "spend": "free-ok"}
ALL_COMBOS = ("t1-orchestrator", "t2-worker", "t3-driver",
              "t2-worker-clean", "t3-driver-clean", "t1-orchestrator-clean")

# Card v2 (spec docs/plans/2026-09-25-routing-v2-spec.md §4). v1 stays valid:
# role/complexity/ctx/spend map onto kind/bucket_hint/min_context, and privacy
# is shared by both versions. normalize_v2 is additive - normalize/select_combo
# and their results do not change.
CARD_V2_VALUES = {
    "kind": ("implement", "debug", "review", "plan", "bulk", "research"),
    "risk": ("normal", "high"),
    "spec": ("exact", "partial", "vague"),
    "privacy": ("public", "sensitive"),
    "mode": ("cost-first", "balanced", "quality-first"),
}
CARD_V2_DEFAULTS = {"kind": "implement", "risk": "normal", "spec": "partial",
                    "privacy": "public", "mode": "balanced", "deferrable": False,
                    "deadline": None, "paths": [], "override": {}}
CARD_V1_ONLY = frozenset({"role", "complexity", "ctx", "spend"})
CARD_V2_ONLY = frozenset({"kind", "risk", "spec", "mode", "deferrable",
                          "deadline", "paths", "override"})
CARD_SHARED = frozenset({"privacy"})
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


def normalize(card: dict) -> dict:
    unknown = sorted(set(card) - set(CARD_VALUES))
    if unknown:
        raise CardError("unknown card field(s): %s (v%s knows: %s)"
                        % (", ".join(unknown), ROUTING_VERSION, ", ".join(CARD_VALUES)))
    out = dict(CARD_DEFAULTS)
    for key, val in card.items():
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


def _check_deferrable(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise CardError("card deferrable=%r: expected true or false" % (value,))


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
        return out

    for key in ("kind", "risk", "spec", "privacy", "mode"):
        if key in flat:
            out[key] = _check_choice(key, flat[key], CARD_V2_VALUES[key])
    if "deferrable" in flat:
        out["deferrable"] = _check_deferrable(flat["deferrable"])
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
    """Return (combo, reason). Raises CardError or NoRoute."""
    c = normalize(card)
    strong = c["role"] == "orchestrate" or c["complexity"] == "hard"
    light = not strong and (c["role"] == "review" or c["complexity"] == "trivial")

    # 1. privacy - a hard filter; only -clean combos serve sensitive work.
    if c["privacy"] == "sensitive":
        # 2. ctx - there is no 1m leg that does not train on prompts.
        if c["ctx"] == "1m":
            if allow_training:
                return "t1-orchestrator-clean", "sensitive-1m-allow-training"
            raise NoRoute(
                "privacy=sensitive with ctx=1m has no route: t1-orchestrator-clean's only leg "
                "trains on prompts. Split the work so each part fits 128k and run it on "
                "t2-worker-clean "
                "(card privacy=sensitive,ctx=128k), or pass --allow-training to accept a "
                "training leg explicitly (logged).")
        # 3. spend - -clean is paid only, so spend changes nothing here.
        # 4. role/complexity preference.
        return ("t3-driver-clean", "sensitive-light") if light else ("t2-worker-clean", "sensitive")

    # public
    if c["ctx"] == "1m":
        return "t1-orchestrator", "public-1m"
    if strong:
        return "t1-orchestrator", "public-strong"
    # 3. spend - no -credit chains since 2026-09-23; the reason still says credit.
    if c["spend"] == "credit":
        return ("t3-driver", "public-light-credit") if light else ("t2-worker", "public-credit")
    return ("t3-driver", "public-light") if light else ("t2-worker", "public-default")
