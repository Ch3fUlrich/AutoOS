"""Which provider is in its cheap or quiet window right now, and when that changes.

    python provider_windows.py                 # table for now, in local time
    python provider_windows.py deepseek meta   # preferred order among these pools

States: price providers are "peak" or "offpeak"; load providers are "busy" or "quiet".
Windows live in provider-windows.json (UTC). An orchestrator reads this instead of doing
time-zone arithmetic, and records the choice it made in its DONE note.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

DATA = pathlib.Path(__file__).with_name("provider-windows.json")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
GOOD = {"offpeak", "quiet"}


_CACHE: dict | None = None


def _load():
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(DATA.read_text(encoding="utf-8"))["providers"]
    return _CACHE


def _inside(windows, days, when):
    if DAYS[when.weekday()] not in days:
        return False
    minute = when.hour * 60 + when.minute
    for start, end in windows:
        s = int(start[:2]) * 60 + int(start[3:])
        e = int(end[:2]) * 60 + int(end[3:])
        if s <= minute < e:
            return True
    return False


def state(provider: str, when: datetime | None = None) -> str:
    when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    p = _load()[provider]
    if p["kind"] == "price":
        return "peak" if _inside(p["peak_utc"], p["peak_days"], when) else "offpeak"
    return "busy" if _inside(p["busy_utc"], p["busy_days"], when) else "quiet"


def next_change(provider: str, when: datetime | None = None) -> datetime:
    when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    now = state(provider, when)
    # Windows start on quarter hours, so step from the NEXT quarter hour, not from `when`.
    t = when.replace(minute=when.minute - when.minute % 15) - timedelta(minutes=15)
    for _ in range(8 * 24 * 4):
        t += timedelta(minutes=15)
        if state(provider, t) != now:
            return t
    return t


def preferred(providers: list[str], when: datetime | None = None) -> list[str]:
    """Pools in a cheap/quiet window first; otherwise the caller's order is kept."""
    return sorted(providers, key=lambda p: 0 if state(p, when) in GOOD else 1)


def main(argv):
    names = argv or list(_load())
    now = datetime.now(timezone.utc)
    for name in preferred(names, now):
        change = next_change(name, now).astimezone()
        print(f"{name:10} {state(name, now):8} until {change:%a %H:%M} local  ({_load()[name]['source']})")


if __name__ == "__main__":
    main(sys.argv[1:])
