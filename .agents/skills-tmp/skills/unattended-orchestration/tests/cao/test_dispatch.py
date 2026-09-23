import pytest

from cao.dispatch import pick_reviewer


class FakeCfg:
    review_pairing = "cross-family"
    routing = {
        "review": [
            {"pool": "anthropic", "family": "anthropic", "model": "sonnet", "effort": "high"},
            {"pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high"},
            {"pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash"},
        ]
    }


def OPEN_ALL(_pool):
    return True


def test_claude_written_code_is_reviewed_by_a_different_family():
    cand, degraded = pick_reviewer(FakeCfg(), "anthropic", OPEN_ALL)
    assert cand.family != "anthropic"
    assert degraded is False


def test_gemini_written_code_is_reviewed_by_anthropic():
    cand, degraded = pick_reviewer(FakeCfg(), "google", OPEN_ALL)
    assert cand.family == "anthropic"
    assert degraded is False


def test_deepseek_written_code_is_reviewed_by_anthropic():
    cand, degraded = pick_reviewer(FakeCfg(), "deepseek", OPEN_ALL)
    assert cand.family == "anthropic"
    assert degraded is False


def test_same_family_from_a_different_pool_is_still_rejected():
    """The pool/family split in one assertion.

    claude-opus-4-6 via agy spends DIFFERENT quota but consults the SAME blind
    spots, so it is valid capacity and an invalid reviewer for Claude code.
    """
    cfg = FakeCfg()
    cfg.routing = {
        "review": [
            {"pool": "antigravity", "family": "anthropic", "model": "claude-opus-4-6-thinking"},
            {"pool": "deepseek", "family": "deepseek", "model": "deepseek-v4-flash"},
        ]
    }
    cand, degraded = pick_reviewer(cfg, "anthropic", OPEN_ALL)
    assert cand.family == "deepseek"
    assert degraded is False


def test_deepseek_gives_a_second_escape_hatch_when_anthropic_is_cooling():
    cand, degraded = pick_reviewer(FakeCfg(), "google", lambda p: p != "anthropic")
    assert cand.family == "deepseek"
    assert degraded is False


def test_degrades_and_flags_when_no_other_family_is_open():
    cand, degraded = pick_reviewer(FakeCfg(), "anthropic", lambda p: p == "anthropic")
    assert cand.family == "anthropic"
    assert degraded is True


def test_raises_when_nothing_at_all_is_open():
    with pytest.raises(RuntimeError, match="no reviewer"):
        pick_reviewer(FakeCfg(), "anthropic", lambda p: False)


def test_prefers_the_highest_ranked_differing_family():
    """Ladder order is a preference order, not just a fallback chain."""
    cand, _ = pick_reviewer(FakeCfg(), "google", OPEN_ALL)
    assert cand.model == "sonnet"
