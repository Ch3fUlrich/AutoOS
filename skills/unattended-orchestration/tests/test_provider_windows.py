"""provider_windows: which provider is in its cheap or quiet window at a given UTC time."""
from datetime import datetime, timezone
import json
import pathlib
import sys

SKILL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

import provider_windows as pw  # noqa: E402


def utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_deepseek_peak_and_offpeak_follow_the_published_utc_windows():
    # 2026-09-16 is a Wednesday. Peak: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.
    assert pw.state("deepseek", utc(2026, 9, 16, 2)) == "peak"
    assert pw.state("deepseek", utc(2026, 9, 16, 5)) == "offpeak"
    assert pw.state("deepseek", utc(2026, 9, 16, 9, 59)) == "peak"
    assert pw.state("deepseek", utc(2026, 9, 16, 10)) == "offpeak"


def test_deepseek_weekend_is_offpeak_all_day():
    assert pw.state("deepseek", utc(2026, 9, 19, 8)) == "offpeak"  # Saturday


def test_claude_and_muse_are_busy_in_the_operator_window():
    # Operator's table: high load 14:00-23:00 CEST = 12:00-21:00 UTC.
    assert pw.state("anthropic", utc(2026, 9, 16, 13)) == "busy"
    assert pw.state("anthropic", utc(2026, 9, 16, 22)) == "quiet"
    assert pw.state("meta", utc(2026, 9, 16, 6)) == "quiet"


def test_every_provider_names_a_source_and_a_kind():
    data = json.loads((SKILL / "provider-windows.json").read_text(encoding="utf-8"))
    for name, p in data["providers"].items():
        assert p["kind"] in ("price", "load"), name
        assert p["source"], name


def test_preferred_orders_cheap_or_quiet_pools_first():
    # Wednesday 13:00 UTC: DeepSeek off-peak, Claude/Muse busy.
    order = pw.preferred(["anthropic", "deepseek", "meta"], utc(2026, 9, 16, 13))
    assert order[0] == "deepseek"


def test_next_change_lands_on_the_window_boundary_not_now_plus_steps():
    # Friday 2026-09-18 10:10 UTC: DeepSeek just left peak; next peak is Monday 01:00 UTC.
    assert pw.next_change("deepseek", utc(2026, 9, 18, 10, 10)) == utc(2026, 9, 21, 1, 0)
