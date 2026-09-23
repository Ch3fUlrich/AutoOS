import pytest

from cao.routing import check_freshness, ladder, select


class FakeCfg:
    def __init__(self, routing):
        self.routing = routing


LADDER = {
    "implement": [
        {"pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high"},
        {"pool": "anthropic", "family": "anthropic", "model": "sonnet", "effort": "medium"},
    ]
}


def test_ladder_returns_candidates_in_order():
    c = ladder(FakeCfg(LADDER), "implement")
    assert [x.model for x in c] == ["gemini-3.8-flash-high", "sonnet"]
    assert c[1].effort == "medium"
    assert c[0].effort is None


def test_select_takes_first_open_pool():
    got = select(FakeCfg(LADDER), "implement", is_open=lambda p: True)
    assert got.model == "gemini-3.8-flash-high"


def test_select_descends_when_first_pool_is_closed():
    got = select(FakeCfg(LADDER), "implement", is_open=lambda p: p != "antigravity")
    assert got.model == "sonnet"
    assert got.family == "anthropic"


def test_select_raises_when_every_pool_closed():
    with pytest.raises(RuntimeError, match="no open pool"):
        select(FakeCfg(LADDER), "implement", is_open=lambda p: False)


def test_unknown_task_class_names_the_known_ones():
    with pytest.raises(KeyError, match="implement"):
        ladder(FakeCfg(LADDER), "nonsense")


def test_freshness_warns_when_older_model_precedes_newer_in_same_pool():
    cfg = FakeCfg(
        {
            "test": [
                {"pool": "antigravity", "family": "google", "model": "gemini-3.6-flash-high"},
                {"pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high"},
            ]
        }
    )
    warnings = check_freshness(cfg)
    assert any(
        "gemini-3.6-flash-high" in w and "gemini-3.8-flash-high" in w for w in warnings
    )


def test_freshness_silent_when_newer_precedes_older_in_same_pool():
    cfg = FakeCfg(
        {
            "test": [
                {"pool": "antigravity", "family": "google", "model": "gemini-3.8-flash-high"},
                {"pool": "antigravity", "family": "google", "model": "gemini-3.6-flash-high"},
            ]
        }
    )
    assert check_freshness(cfg) == []


def test_freshness_silent_across_different_pools():
    """The corrected design (spec 5.4).

    Descending to an older family member in a DIFFERENT pool is legitimate extra
    capacity, not staleness: refusing it shrinks the budget without improving
    the model.
    """
    cfg = FakeCfg(
        {
            "test": [
                {"pool": "anthropic", "family": "anthropic", "model": "opus"},
                {
                    "pool": "antigravity",
                    "family": "anthropic",
                    "model": "claude-opus-4-6-thinking",
                },
            ]
        }
    )
    assert check_freshness(cfg) == []


def test_freshness_ignores_aliases_which_are_always_latest():
    cfg = FakeCfg(
        {
            "test": [
                {"pool": "anthropic", "family": "anthropic", "model": "sonnet"},
                {"pool": "anthropic", "family": "anthropic", "model": "opus"},
            ]
        }
    )
    assert check_freshness(cfg) == []


def test_freshness_skips_the_cross_family_sentinel():
    assert check_freshness(FakeCfg({"review": "cross-family"})) == []
