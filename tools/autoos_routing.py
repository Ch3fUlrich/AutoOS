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
role/complexity. sensitive + 1m has no route: tier1-clean's only leg is a
contributor model that trains on prompts, so it is reachable only through an
explicit allow_training, which the caller logs.
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
ALL_COMBOS = ("tier1", "tier2", "tier3", "tier2-credit", "tier3-credit",
              "tier2-clean", "tier3-clean", "tier1-clean")


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
                return "tier1-clean", "sensitive-1m-allow-training"
            raise NoRoute(
                "privacy=sensitive with ctx=1m has no route: tier1-clean's only leg trains on "
                "prompts. Split the work so each part fits 128k and run it on tier2-clean "
                "(card privacy=sensitive,ctx=128k), or pass --allow-training to accept a "
                "training leg explicitly (logged).")
        # 3. spend - -clean is paid only, so spend changes nothing here.
        # 4. role/complexity preference.
        return ("tier3-clean", "sensitive-light") if light else ("tier2-clean", "sensitive")

    # public
    if c["ctx"] == "1m":
        return "tier1", "public-1m"
    if strong:
        return "tier1", "public-strong"
    if c["spend"] == "credit":
        return ("tier3-credit", "public-light-credit") if light else ("tier2-credit", "public-credit")
    return ("tier3", "public-light") if light else ("tier2", "public-default")
