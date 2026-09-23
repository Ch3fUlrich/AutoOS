"""Pool-keyed quota ledger.

Keyed by POOL rather than by provider, because a pool is what actually runs out:
``agy``'s Gemini and Claude models share one Antigravity bucket, and exhausting
it closes both while leaving Claude Code's own bucket untouched.

The detection patterns below are **unverified**. No provider's real quota string
has been measured yet, so ``is_open`` deliberately fails OPEN: a pool with no
record is usable. Falsely cooling a healthy pool costs more than a missed wall,
which the very next call detects anyway.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Conservative on purpose. `\b429\b` rather than `429` so a token count like
# "processed 14290 tokens" cannot be mistaken for a rate-limit response.
#: A wall that CLEARS on its own. Cooling the pool for an hour is the right
#: response: the capacity comes back.
EXHAUSTED_PATTERNS = (
    r"rate.?limit",
    r"quota",
    r"resource.?exhausted",
    r"\b429\b",
    r"usage limit",
)

#: A wall that does NOT clear on its own. Measured in the shipped Claude Code
#: 2.1.236 binary (`strings`, 2026-09-11): billing_error x28, "credit balance
#: too low" x5, "spend limit reached" x4 -- none of which the patterns above
#: match. Waiting an hour changes nothing; only another pool or a human does.
BILLING_PATTERNS = (
    r"billing_error",
    r"credit balance too low",
    r"spend limit reached",
    r"insufficient_quota",
    r"insufficient balance",
)

#: NOT a wall. `overloaded_error` x25 and "request timed out" x34 in the same
#: binary: server-side congestion measured in seconds. Closing a pool for an
#: hour over a 529 removes capacity that was never gone -- the opposite of the
#: rule that a pool is never declined.
TRANSIENT_PATTERNS = (
    r"overloaded_error",
    r"\b529\b",
    r"request timed out",
    r"connection reset",
)

#: What "we hit a wall" means for :func:`detect_limit`: both kinds that close a
#: pool, and deliberately not the transient ones.
DEFAULT_PATTERNS = EXHAUSTED_PATTERNS + BILLING_PATTERNS

EXHAUSTED, BILLING, TRANSIENT = "exhausted", "billing", "transient"

MAX_RAW = 500


class Ledger:
    """Append-only NDJSON, shared format with the batch lane's ledger."""

    def __init__(self, path):
        self.path = Path(path)

    def append(self, obj: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def read(self) -> list:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # A crash mid-write leaves one partial trailing line. Skipping it
                # is correct; refusing to read the file would lose all history.
                continue
        return out


def detect_limit(text, patterns=None):
    """Return the pattern that matched, or None. Never raises on odd input."""
    for pattern in patterns or DEFAULT_PATTERNS:
        if re.search(pattern, text or "", re.I):
            return pattern
    return None


def classify_limit(text):
    """``(kind, pattern)`` for a provider's output; ``(None, None)`` if clean.

    Transient is checked FIRST and wins. Anthropic error bodies carry both an
    error type and surrounding prose, and matching "rate limit" somewhere in a
    529 body would close a pool that is merely busy.
    """
    blob = text or ""
    for group, kind in ((TRANSIENT_PATTERNS, TRANSIENT),
                        (BILLING_PATTERNS, BILLING),
                        (EXHAUSTED_PATTERNS, EXHAUSTED)):
        for pattern in group:
            if re.search(pattern, blob, re.I):
                return kind, pattern
    return None, None


def _now(now=None):
    return now or datetime.now(timezone.utc)


def record_cooldown(
    ledger: Ledger, pool: str, raw: str, pattern: str, minutes: int = 60
) -> dict:
    rec = {
        "ts": _now().isoformat(),
        "pool": pool,
        "state": "cooling",
        "cooling_until": (_now() + timedelta(minutes=minutes)).isoformat(),
        "pattern": pattern,
        "raw": (raw or "")[:MAX_RAW],
    }
    ledger.append(rec)
    return rec


def mark_unavailable(ledger: Ledger, pool: str, reason: str = "no credentials",
                     raw: str = "") -> dict:
    """Closed, and not reopening by waiting.

    Two causes, one state: no credentials, or a billing wall. Both need a
    different pool or a human, and neither improves in an hour -- which is
    why they are NOT recorded as cooling.
    """
    rec = {"ts": _now().isoformat(), "pool": pool, "state": "unavailable",
           "reason": reason}
    if raw:
        rec["raw"] = raw[:MAX_RAW]
    ledger.append(rec)
    return rec


def mark_available(ledger: Ledger, pool: str, reason: str = "probe ok") -> dict:
    """Reinstate a pool. The LAST record for a pool wins, so this reopens one.

    Needed because closure has to be revocable by evidence. A pool that failed
    a probe at 09:00 and answers at 11:00 must come back, or one bad minute
    removes it for the rest of the run -- and this lane's rule is that a pool is
    never declined, only routed around while it is actually down.
    """
    rec = {"ts": _now().isoformat(), "pool": pool, "state": "available",
           "reason": reason}
    ledger.append(rec)
    return rec


def is_open(ledger: Ledger, pool: str, now=None) -> bool:
    latest = None
    for rec in ledger.read():
        if rec.get("pool") == pool:
            latest = rec  # last record wins, so a pool can be reinstated
    if latest is None:
        return True  # fail open — see module docstring
    state = latest.get("state")
    if state == "unavailable":
        return False
    if state == "cooling":
        return _now(now) >= datetime.fromisoformat(latest["cooling_until"])
    return True


def note_provider_output(ledger, pool, text, cooldown_minutes=60) -> str | None:
    """Scan a provider's output for a quota wall and cool the pool if found.

    Without this, `budget.detect_limit` and `record_cooldown` are never called
    from anywhere, `is_open` returns True forever, and the ladder never descends
    — the whole quota feature is inert while looking implemented. Measured by a
    function-level wiring audit: module imported, functions never invoked.

    Returns the KIND that closed the pool (``exhausted`` / ``billing``), or
    None when nothing did -- including a transient 529, which closes nothing.
    Callers print it, so it has to say what actually happened: "cooled for an
    hour" and "closed until someone pays" are different instructions.

    Never raises: a budget bookkeeping failure must not take down the run it is
    bookkeeping for.
    """
    try:
        kind, pattern = classify_limit(text)
        if kind == EXHAUSTED:
            record_cooldown(ledger, pool, raw=text or "", pattern=pattern,
                            minutes=cooldown_minutes)
        elif kind == BILLING:
            # An hour's cooldown would expire into the same wall. Closing the
            # pool is what makes the ladder descend to one that can work.
            mark_unavailable(ledger, pool, reason="billing wall", raw=text or "")
        # TRANSIENT and None: the pool is fine. Recording a 529 as exhaustion
        # would remove capacity that was never gone.
        return kind if kind in (EXHAUSTED, BILLING) else None
    except OSError:
        return None
