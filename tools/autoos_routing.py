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
