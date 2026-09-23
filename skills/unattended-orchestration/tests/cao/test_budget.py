from datetime import datetime, timedelta, timezone

from cao.budget import Ledger, detect_limit, is_open, mark_unavailable, record_cooldown


def test_detect_limit_matches_conservative_defaults():
    assert detect_limit("Error: 429 Too Many Requests") is not None
    assert detect_limit("RESOURCE_EXHAUSTED: quota") is not None
    assert detect_limit("you have hit your usage limit") is not None
    assert detect_limit("rate limit exceeded, retry later") is not None


def test_detect_limit_returns_none_for_ordinary_errors():
    assert detect_limit("SyntaxError: invalid syntax") is None
    assert detect_limit("test failed: 3 assertions") is None
    assert detect_limit("") is None
    assert detect_limit(None) is None


def test_detect_limit_does_not_match_429_inside_a_larger_number():
    assert detect_limit("processed 14290 tokens") is None


def test_unknown_pool_is_open_fail_open(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    assert is_open(led, "antigravity") is True


def test_cooling_pool_is_closed_then_reopens(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    record_cooldown(led, "antigravity", raw="429 rate limit", pattern="429", minutes=60)
    assert is_open(led, "antigravity") is False
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    assert is_open(led, "antigravity", now=later) is True


def test_cooling_one_pool_does_not_close_another(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    record_cooldown(led, "antigravity", raw="429", pattern="429")
    assert is_open(led, "anthropic") is True


def test_unavailable_pool_is_closed_indefinitely(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    mark_unavailable(led, "deepseek")
    far = datetime.now(timezone.utc) + timedelta(days=30)
    assert is_open(led, "deepseek", now=far) is False


def test_latest_record_wins_so_a_pool_can_be_reinstated(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    mark_unavailable(led, "deepseek")
    led.append({"ts": "now", "pool": "deepseek", "state": "ok"})
    assert is_open(led, "deepseek") is True


def test_cooldown_truncates_raw_and_keeps_it_short(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    rec = record_cooldown(led, "antigravity", raw="x" * 5000, pattern="429")
    assert len(rec["raw"]) <= 500


def test_ledger_is_append_only_ndjson(tmp_path):
    led = Ledger(tmp_path / "budget.ndjson")
    led.append({"a": 1})
    led.append({"a": 2})
    lines = (tmp_path / "budget.ndjson").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert [line.count("\n") for line in lines] == [0, 0]


def test_ledger_tolerates_corrupt_trailing_line(tmp_path):
    p = tmp_path / "budget.ndjson"
    p.write_text('{"a": 1}\n{"a": 2}\n{"a": ', encoding="utf-8")
    assert Ledger(p).read() == [{"a": 1}, {"a": 2}]


def test_ledger_read_of_missing_file_is_empty(tmp_path):
    assert Ledger(tmp_path / "nope.ndjson").read() == []


# --------------------------------------------------------------------------
# Wiring. detect_limit and record_cooldown were never called from anywhere, so
# is_open returned True forever and the ladder never descended: the whole quota
# feature was inert while looking implemented. Found by a function-level audit.
# --------------------------------------------------------------------------


def test_a_quota_message_cools_the_pool(tmp_path):
    from cao.budget import note_provider_output

    led = Ledger(tmp_path / "budget.ndjson")
    assert is_open(led, "antigravity") is True
    hit = note_provider_output(led, "antigravity", "Error: 429 rate limit exceeded")
    assert hit
    assert is_open(led, "antigravity") is False


def test_ordinary_output_cools_nothing(tmp_path):
    from cao.budget import note_provider_output

    led = Ledger(tmp_path / "budget.ndjson")
    assert note_provider_output(led, "antigravity", "14 passed in 3s") is None
    assert is_open(led, "antigravity") is True


def test_cooling_one_pool_leaves_the_others_open(tmp_path):
    from cao.budget import note_provider_output

    led = Ledger(tmp_path / "budget.ndjson")
    note_provider_output(led, "antigravity", "quota exhausted")
    assert is_open(led, "anthropic") is True


def test_empty_output_is_safe(tmp_path):
    from cao.budget import note_provider_output

    led = Ledger(tmp_path / "budget.ndjson")
    assert note_provider_output(led, "antigravity", "") is None
    assert note_provider_output(led, "antigravity", None) is None


def test_bookkeeping_failure_never_takes_down_the_run(tmp_path):
    """A budget ledger that cannot be written must not abort the work."""
    from cao.budget import note_provider_output

    class Unwritable(Ledger):
        def append(self, obj):
            raise OSError("disk full")

    led = Unwritable(tmp_path / "budget.ndjson")
    assert note_provider_output(led, "antigravity", "429 rate limit") is None


def test_the_cooldown_records_what_matched(tmp_path):
    from cao.budget import note_provider_output

    led = Ledger(tmp_path / "budget.ndjson")
    note_provider_output(led, "deepseek", "RESOURCE_EXHAUSTED: quota")
    rec = [r for r in led.read() if r.get("pool") == "deepseek"][-1]
    assert rec["state"] == "cooling"
    assert rec["pattern"]
    assert "RESOURCE_EXHAUSTED" in rec["raw"]


# ---------------------------------------------------------------------------
# Measured, not guessed. Counted with `strings` in the shipped Claude Code
# 2.1.236 binary on 2026-09-11:
#
#   billing_error 28 · rate_limit_error 25 · overloaded_error 25
#   "Usage limit reached" 16 · RESOURCE_EXHAUSTED 16 · "request timed out" 34
#   "quota exceeded" 7 · "spend limit reached" 4 · "credit balance too low" 5
#
# Three of those closed a pool wrongly or not at all under the old single
# pattern list, which is why this file previously admitted the detection was
# unverified.
# ---------------------------------------------------------------------------

from cao.budget import BILLING, EXHAUSTED, TRANSIENT, classify_limit, note_provider_output

MEASURED_BILLING = (
    '{"type":"error","error":{"type":"billing_error","message":"spend limit '
    'reached (daily; resets 2026-08-08 00:00 UTC)"}}',
    "credit balance too low",
)
MEASURED_EXHAUSTED = (
    "Usage limit reached",
    '{"type":"rate_limit_error"}',
    "RESOURCE_EXHAUSTED",
    "quota exceeded",
)
MEASURED_TRANSIENT = ('{"type":"overloaded_error"}', "Request timed out")


def test_a_billing_wall_is_not_a_cooldown():
    """It does not clear in an hour, so cooling the pool wastes the hour."""
    for text in MEASURED_BILLING:
        assert classify_limit(text)[0] == BILLING, text


def test_exhaustion_is_a_cooldown():
    for text in MEASURED_EXHAUSTED:
        assert classify_limit(text)[0] == EXHAUSTED, text


def test_congestion_closes_nothing():
    """A 529 is seconds of busy, not an exhausted quota."""
    for text in MEASURED_TRANSIENT:
        assert classify_limit(text)[0] == TRANSIENT, text


def test_ordinary_output_is_still_clean():
    for text in ("14 passed in 3s", "processed 14290 tokens", "", None):
        assert classify_limit(text) == (None, None), text


def test_a_billing_wall_closes_the_pool_outright(tmp_path):
    led = Ledger(tmp_path / "b.ndjson")
    note_provider_output(led, "anthropic", MEASURED_BILLING[0])
    assert is_open(led, "anthropic") is False
    rec = led.read()[-1]
    assert rec["state"] == "unavailable", "a billing wall must not be 'cooling'"
    assert rec["reason"] == "billing wall"


def test_congestion_leaves_the_pool_open(tmp_path):
    """Never decline capacity that was never gone."""
    led = Ledger(tmp_path / "b.ndjson")
    assert note_provider_output(led, "anthropic", MEASURED_TRANSIENT[0]) is None
    assert is_open(led, "anthropic") is True
    assert led.read() == []


def test_exhaustion_still_cools(tmp_path):
    led = Ledger(tmp_path / "b.ndjson")
    note_provider_output(led, "antigravity", "RESOURCE_EXHAUSTED")
    assert led.read()[-1]["state"] == "cooling"
    assert is_open(led, "antigravity") is False


def test_transient_wins_over_a_limit_word_in_the_same_body(tmp_path):
    """Anthropic bodies carry prose; one stray "rate limit" must not close it."""
    body = '{"type":"overloaded_error","message":"try again; not a rate limit"}'
    assert classify_limit(body)[0] == TRANSIENT
