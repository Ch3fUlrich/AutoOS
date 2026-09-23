"""Compose the modules. The rule enforced here is cross-FAMILY review.

Different training means different blind spots. A same-family review shares the
failure mode it is supposed to catch, so pairing is by ``family`` — never by
``pool``, which only says whose quota paid for it.
"""

from __future__ import annotations

from cao.routing import ladder


def pick_reviewer(cfg, implementer_family: str, is_open):
    """Return ``(candidate, degraded)``.

    Two passes, in this order:

    1. the highest-ranked candidate from a DIFFERENT family whose pool is open;
    2. failing that, any open candidate at all — flagged ``degraded=True``.

    Degrading is allowed because a review by the same family still beats no
    review, but it is always labelled so a report cannot quietly present it as
    an independent check.
    """
    candidates = ladder(cfg, "review")

    for candidate in candidates:
        if candidate.family != implementer_family and is_open(candidate.pool):
            return candidate, False

    for candidate in candidates:
        if is_open(candidate.pool):
            return candidate, True

    raise RuntimeError(
        f"no reviewer available for family {implementer_family!r}; "
        f"every pool closed: {[c.pool for c in candidates]}"
    )
