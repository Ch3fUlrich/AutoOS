"""Task class -> ordered candidate ladder.

Two axes, deliberately kept apart (spec 5.2):

    pool   = which quota bucket the capacity is drawn from  -> decides fallback
    family = whose training, whose blind spots              -> decides review pairing

They are not the same thing, and conflating them produced a real design error in
the first draft of this lane. ``claude-opus-4-6-thinking`` reached through ``agy``
is the *anthropic family* drawn from the *antigravity pool*: valid extra capacity,
and an invalid reviewer for Claude-written code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FAMILY_ORDER = ("anthropic", "google", "meta", "deepseek", "openrouter", "oss")

_VERSION = re.compile(r"(\d+)\.(\d+)")


@dataclass(frozen=True)
class Candidate:
    pool: str
    family: str
    model: str
    effort: str | None = None


def ladder(cfg, task_class: str) -> list:
    try:
        entries = cfg.routing[task_class]
    except KeyError:
        raise KeyError(
            f"unknown task class {task_class!r}; known: {sorted(cfg.routing)}"
        ) from None
    return [
        Candidate(e["pool"], e["family"], e["model"], e.get("effort")) for e in entries
    ]


def select(cfg, task_class: str, is_open):
    """First candidate whose pool is open.

    A ladder is therefore both a preference order and a quota fallback — one
    structure, so there is no second place for the two to disagree.
    """
    candidates = ladder(cfg, task_class)
    for candidate in candidates:
        if is_open(candidate.pool):
            return candidate
    raise RuntimeError(
        f"no open pool for task class {task_class!r}; "
        f"tried {[c.pool for c in candidates]}"
    )


def _version_key(model: str):
    """(major, minor) if the id carries a version, else None.

    None means an alias such as ``opus`` or ``sonnet``, which the CLI resolves to
    the latest release — an alias can never be stale, so freshness skips it.
    """
    match = _VERSION.search(model)
    return (int(match.group(1)), int(match.group(2))) if match else None


def check_freshness(cfg) -> list:
    """Warn when a pool ranks an older model ahead of a newer one IT ALSO offers.

    Cross-pool descent is silent on purpose. Refusing a pool's older model does
    not get you a better model — it gets you a smaller total budget.
    """
    out = []
    for task_class, entries in cfg.routing.items():
        if not isinstance(entries, list):
            continue  # the "cross-family" sentinel
        best_seen = {}
        for entry in entries:
            pool = entry["pool"]
            version = _version_key(entry["model"])
            if version is None:
                continue
            if pool in best_seen and best_seen[pool][0] < version:
                out.append(
                    f"{task_class}: pool {pool!r} ranks {best_seen[pool][1]!r} ahead "
                    f"of newer {entry['model']!r} from the same pool"
                )
            else:
                best_seen[pool] = (version, entry["model"])
    return out
