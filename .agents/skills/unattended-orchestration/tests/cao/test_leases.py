from datetime import datetime, timedelta, timezone

from cao.leases import claim, may_resume, release, state


def _rec(**over):
    base = {
        "task_id": "t1",
        "owner": "term-a",
        "state": "running",
        "lease_expires": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "continued_by": None,
        "attempt": 1,
    }
    base.update(over)
    return base


def test_claim_succeeds_once_then_blocks_second_claimer(tmp_path):
    assert claim(tmp_path, "t1", "term-a") is True
    assert claim(tmp_path, "t1", "term-b") is False


def test_claims_on_different_tasks_do_not_collide(tmp_path):
    assert claim(tmp_path, "t1", "term-a") is True
    assert claim(tmp_path, "t2", "term-b") is True


def test_release_allows_reclaim(tmp_path):
    claim(tmp_path, "t1", "term-a")
    release(tmp_path, "t1")
    assert claim(tmp_path, "t1", "term-b") is True


def test_release_of_unclaimed_task_is_not_an_error(tmp_path):
    release(tmp_path, "never-claimed")


def test_state_reports_owner(tmp_path):
    claim(tmp_path, "t1", "term-a")
    assert state(tmp_path, "t1")["owner"] == "term-a"


def test_state_of_unclaimed_task_is_none(tmp_path):
    assert state(tmp_path, "t1") is None


def test_expired_lease_with_no_successor_may_resume():
    assert may_resume(_rec(), live_task_ids=set(), max_attempts=3) is True


def test_unexpired_lease_may_not_resume():
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    assert may_resume(_rec(lease_expires=future), live_task_ids=set(), max_attempts=3) is False


# ------------------------------------------------- the user's constraint ---
# "continue work that stopped, but ONLY if another agent has not already
# picked it up."


def test_continued_by_blocks_resume():
    assert may_resume(_rec(continued_by="term-z"), live_task_ids=set(), max_attempts=3) is False


def test_live_terminal_on_that_task_blocks_resume_even_if_lease_expired():
    """The load-bearing check: a ledger can be stale, CAO knows who is alive."""
    assert may_resume(_rec(), live_task_ids={"t1"}, max_attempts=3) is False


def test_live_terminal_on_a_different_task_does_not_block():
    assert may_resume(_rec(), live_task_ids={"other"}, max_attempts=3) is True


def test_attempts_exhausted_blocks_resume():
    assert may_resume(_rec(attempt=3), live_task_ids=set(), max_attempts=3) is False


def test_attempts_below_the_cap_still_resume():
    assert may_resume(_rec(attempt=2), live_task_ids=set(), max_attempts=3) is True


def test_quota_stalled_state_is_resumable():
    assert may_resume(_rec(state="quota_stalled"), live_task_ids=set(), max_attempts=3) is True


def test_done_state_is_not_resumable():
    assert may_resume(_rec(state="done"), live_task_ids=set(), max_attempts=3) is False


def test_needs_human_state_is_not_resumable():
    assert may_resume(_rec(state="needs_human"), live_task_ids=set(), max_attempts=3) is False


def test_none_live_ids_is_treated_as_empty():
    assert may_resume(_rec(), live_task_ids=None, max_attempts=3) is True


# --------------------------------------------------------------------------
# The record claim() writes must be the record may_resume() reads. An earlier
# version wrote task_id/owner/claimed_at while may_resume looked for state,
# lease_expires and attempt -- so every lease read as unresumable and the
# resume path could never have worked. Nothing called may_resume, so nothing
# surfaced it.
# --------------------------------------------------------------------------


def test_a_claimed_lease_is_shaped_for_may_resume(tmp_path):
    claim(tmp_path, "t1", "term-a", lease_minutes=0)
    rec = state(tmp_path, "t1")
    for field in ("task_id", "owner", "state", "lease_expires", "continued_by", "attempt"):
        assert field in rec, field
    assert rec["state"] == "running"


def test_a_freshly_claimed_lease_is_not_resumable(tmp_path):
    """It is alive; resuming it would duplicate live work."""
    claim(tmp_path, "t1", "term-a", lease_minutes=30)
    assert may_resume(state(tmp_path, "t1"), live_task_ids=set(), max_attempts=3) is False


def test_an_expired_lease_round_trips_to_resumable(tmp_path):
    claim(tmp_path, "t1", "term-a", lease_minutes=0)
    assert may_resume(state(tmp_path, "t1"), live_task_ids=set(), max_attempts=3) is True


def test_attempt_is_recorded_so_the_cap_can_be_enforced(tmp_path):
    claim(tmp_path, "t1", "term-a", lease_minutes=0, attempt=3)
    assert may_resume(state(tmp_path, "t1"), live_task_ids=set(), max_attempts=3) is False


def test_all_leases_lists_every_record(tmp_path):
    from cao.leases import all_leases

    claim(tmp_path, "t1", "a")
    claim(tmp_path, "t2", "b")
    assert {r["task_id"] for r in all_leases(tmp_path)} == {"t1", "t2"}


def test_all_leases_skips_a_corrupt_file(tmp_path):
    from cao.leases import all_leases

    claim(tmp_path, "t1", "a")
    (tmp_path / "broken.lease").write_text("{not json", encoding="utf-8")
    assert [r["task_id"] for r in all_leases(tmp_path)] == ["t1"]


def test_all_leases_of_a_missing_dir_is_empty(tmp_path):
    from cao.leases import all_leases

    assert all_leases(tmp_path / "nope") == []
