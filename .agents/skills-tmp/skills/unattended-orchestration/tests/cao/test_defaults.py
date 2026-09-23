"""The shipped defaults must satisfy the rules the modules enforce.

A stale or malformed default is a bug that ships silently, so it fails here
rather than in someone else's repo after they copy the example.
"""

import json
from pathlib import Path

from cao.config import load_config, pool_available
from cao.dispatch import pick_reviewer
from cao.routing import check_freshness, ladder

SKILL = Path(__file__).resolve().parents[2]


def _cfg(tmp_path):
    example = json.loads(
        (SKILL / "handoff.config.example.json").read_text(encoding="utf-8")
    )
    (tmp_path / "handoff.config.json").write_text(json.dumps(example), encoding="utf-8")
    return load_config(tmp_path)


def test_example_config_carries_a_cao_block(tmp_path):
    assert _cfg(tmp_path).max_depth >= 2


def test_default_ladders_are_fresh(tmp_path):
    assert check_freshness(_cfg(tmp_path)) == []


def test_every_task_class_has_at_least_one_candidate(tmp_path):
    cfg = _cfg(tmp_path)
    for task_class, entries in cfg.routing.items():
        if isinstance(entries, list):
            assert ladder(cfg, task_class), f"{task_class} has an empty ladder"


def test_all_three_families_are_reachable(tmp_path):
    cfg = _cfg(tmp_path)
    families = {
        c.family
        for tc, entries in cfg.routing.items()
        if isinstance(entries, list)
        for c in ladder(cfg, tc)
    }
    assert {"anthropic", "google", "deepseek"} <= families


def test_every_routed_pool_is_declared(tmp_path):
    cfg = _cfg(tmp_path)
    used = {
        c.pool
        for tc, entries in cfg.routing.items()
        if isinstance(entries, list)
        for c in ladder(cfg, tc)
    }
    assert used <= set(cfg.pools), f"routing uses undeclared pools: {used - set(cfg.pools)}"


def test_deepest_level_cannot_spawn(tmp_path):
    cfg = _cfg(tmp_path)
    deepest = max(cfg.levels, key=lambda level: level["level"])
    assert deepest["level"] == cfg.max_depth
    assert deepest.get("canSpawn") is False


def test_cheap_work_never_defaults_to_the_most_expensive_model(tmp_path):
    """Mechanical work must not open on `opus` — that is the whole economy."""
    cfg = _cfg(tmp_path)
    assert ladder(cfg, "mechanical")[0].model != "opus"


def test_review_can_pair_cross_family_for_every_family(tmp_path):
    cfg = _cfg(tmp_path)
    for family in ("anthropic", "google", "deepseek"):
        cand, degraded = pick_reviewer(cfg, family, is_open=lambda _p: True)
        assert cand.family != family
        assert degraded is False


def test_deepseek_pool_declares_an_api_key_env(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.pools["deepseek"]["apiKeyEnv"]
    assert pool_available(cfg, "deepseek", secrets={}) is False


def test_cli_authenticated_pools_need_no_key(tmp_path):
    cfg = _cfg(tmp_path)
    assert pool_available(cfg, "anthropic", secrets={}) is True
    assert pool_available(cfg, "antigravity", secrets={}) is True


def test_agy_claude_models_are_kept_as_capacity(tmp_path):
    """The corrected design: separate pool, so never declined (spec 5.4)."""
    cfg = _cfg(tmp_path)
    models = {c.model for c in ladder(cfg, "plan") + ladder(cfg, "decompose")}
    assert {"claude-opus-4-6-thinking", "claude-sonnet-4-6"} & models
