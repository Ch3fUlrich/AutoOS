"""Depth policing.

The values under test come from CAO's terminal API: ``caller_id`` is stamped
server-side from the calling terminal's MCP identity, unlike ``group`` which the
agent writes itself.
"""

from cao.monitor import (
    DEFAULT_POLL_SECONDS,
    depth_of,
    index,
    terminable,
    violations,
)


def T(tid, caller=None, profile="cao_L3_worker"):
    return {"id": tid, "caller_id": caller, "agent_profile": profile}


CHAIN = [
    T("a", None, "cao_L2_supervisor"),
    T("b", "a", "cao_L3_worker"),
    T("c", "b", "cao_L4_worker"),
]


def test_root_terminal_is_depth_one():
    assert depth_of("a", index(CHAIN)) == 1


def test_depth_follows_the_caller_chain():
    by_id = index(CHAIN)
    assert depth_of("b", by_id) == 2
    assert depth_of("c", by_id) == 3


def test_unknown_terminal_has_unknown_depth():
    assert depth_of("ghost", index(CHAIN)) is None


def test_orphan_reports_unknown_not_one():
    """A dead parent must not make its child look like a root."""
    orphan = [T("x", "vanished-parent")]
    assert depth_of("x", index(orphan)) is None


def test_a_cycle_terminates_instead_of_recursing_forever():
    cyclic = [T("p", "q"), T("q", "p")]
    assert depth_of("p", index(cyclic)) is None


def test_no_violations_within_the_limit():
    assert violations(CHAIN, max_depth=3) == []


def test_a_terminal_below_the_limit_is_reported():
    deep = CHAIN + [T("d", "c", "cao_L4_worker")]
    found = violations(deep, max_depth=3)
    assert [v.terminal_id for v in found] == ["d"]
    assert found[0].depth == 4
    assert "exceeds maxDepth 3" in found[0].reason


def test_lowering_max_depth_catches_more():
    found = violations(CHAIN, max_depth=2)
    assert [v.terminal_id for v in found] == ["c"]


def test_violation_names_the_profile_so_a_human_can_tell_what_died():
    deep = CHAIN + [T("d", "c", "cao_L4_worker")]
    assert violations(deep, max_depth=3)[0].agent_profile == "cao_L4_worker"


def test_unknown_depth_is_reported_as_a_violation():
    """Unknown means unknown — not silently allowed."""
    found = violations([T("x", "vanished-parent")], max_depth=3)
    assert len(found) == 1
    assert found[0].depth is None
    assert "reported for a human" in found[0].reason


def test_unknown_depth_is_never_auto_terminated():
    """Killing on uncertainty turns a flaky API read into destroyed work."""
    found = violations([T("x", "vanished-parent")], max_depth=3)
    assert terminable(found) == []


def test_a_real_violation_is_terminable():
    deep = CHAIN + [T("d", "c")]
    assert [v.terminal_id for v in terminable(violations(deep, max_depth=3))] == ["d"]


def test_terminals_without_ids_are_skipped_not_crashed_on():
    assert violations([{"caller_id": "a"}], max_depth=3) == []


def test_poll_default_is_stated_and_sane():
    assert 10 <= DEFAULT_POLL_SECONDS <= 60


def test_config_levels_and_cao_depths_are_off_by_one():
    """cao.levels counts L1 = the outer orchestrator, which is not a CAO terminal.

    maxDepth 3 (L1, L2, L3) therefore permits CAO terminal depths 1 and 2.
    Passing cfg.max_depth straight through silently allows a fourth layer.
    """
    from cao.monitor import cao_depth_limit

    assert cao_depth_limit(3) == 2
    assert cao_depth_limit(4) == 3
    assert cao_depth_limit(5) == 4


def test_depth_limit_never_drops_below_one():
    from cao.monitor import cao_depth_limit

    assert cao_depth_limit(2) == 1
    assert cao_depth_limit(1) == 1
    assert cao_depth_limit(0) == 1


def test_a_three_level_config_rejects_a_fourth_cao_layer():
    from cao.monitor import cao_depth_limit

    chain = CHAIN + [T("d", "c", "cao_L5_worker")]
    found = violations(chain, max_depth=cao_depth_limit(3))
    assert {v.terminal_id for v in found} == {"c", "d"}
