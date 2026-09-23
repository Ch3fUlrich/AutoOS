"""Independent verification — the anti-lying layer.

Measured 2026-09-10: a worker created a file correctly and reported an `od -c`
dump that did not match it; its supervisor relayed that verbatim. Neither was
malicious and neither was detectable from the report. Only an exit code obtained
by someone who was not asked to produce a good answer separates a real result
from a plausible sentence.
"""

import subprocess

from cao.budget import Ledger
from cao.verify import (
    ERROR,
    FAIL,
    MAX_OUTPUT,
    PASS,
    attempts_for,
    failures_for,
    record_rework,
    read_history,
    record,
    rework_brief,
    run_guard,
    verify_phase,
)


class FakeCompleted:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def runner(code, out="", err="", capture=None):
    def run(argv):
        if capture is not None:
            capture.append(argv)
        return FakeCompleted(code, out, err)

    return run


def test_a_passing_guard_is_a_pass():
    v = run_guard("pytest -q", "/wt", runner=runner(0, "12 passed"))
    assert v.status == PASS and v.ok is True
    assert v.exit_code == 0


def test_a_failing_guard_is_a_fail_with_its_output():
    v = run_guard("pytest -q", "/wt", runner=runner(1, "", "2 failed"))
    assert v.status == FAIL and v.ok is False
    assert "2 failed" in v.output


def test_an_empty_guard_is_an_error_not_a_pass():
    """A phase with no guard must never read as verified."""
    v = run_guard("", "/wt", runner=runner(0))
    assert v.status == ERROR and v.ok is False


def test_a_whitespace_guard_is_also_an_error():
    assert run_guard("   ", "/wt", runner=runner(0)).status == ERROR


def test_the_guard_runs_in_the_worktree():
    seen = []
    run_guard("pytest -q", "/wt/phase1", runner=runner(0, capture=seen))
    assert "cd /wt/phase1 " in seen[0][-1]


def test_the_guard_uses_a_login_shell():
    """`bash -c` cannot see ~/.local/bin, so pytest/uv would fail spuriously."""
    seen = []
    run_guard("pytest -q", "/wt", runner=runner(0, capture=seen))
    assert seen[0][:2] == ["bash", "-lc"]


def test_windows_runs_the_guard_inside_the_distro():
    seen = []
    run_guard("pytest -q", "/wt", wsl_distro="Ubuntu", runner=runner(0, capture=seen))
    assert seen[0][:3] == ["wsl", "-d", "Ubuntu"]


def test_a_timeout_is_an_error_not_a_pass():
    def boom(argv):
        raise subprocess.TimeoutExpired(argv, 5)

    v = run_guard("sleep 999", "/wt", runner=boom)
    assert v.status == ERROR
    assert "exceeded" in v.output


def test_a_guard_that_cannot_run_is_an_error_not_a_pass():
    def boom(argv):
        raise OSError("no shell")

    assert run_guard("x", "/wt", runner=boom).status == ERROR


def test_long_output_keeps_the_tail_where_failures_live():
    noisy = "line\n" * 5000 + "AssertionError: the important bit"
    v = run_guard("pytest", "/wt", runner=runner(1, noisy))
    assert "AssertionError: the important bit" in v.output
    assert len(v.output) < len(noisy)
    assert "trimmed" in v.output


def test_short_output_is_untouched():
    v = run_guard("pytest", "/wt", runner=runner(1, "boom"))
    assert v.output == "boom"
    assert len(v.output) <= MAX_OUTPUT


# ------------------------------------------------------------------- phases --

PLAN = {
    "phases": [
        {"id": "p1", "acceptance": {"guard": "test -s out.txt", "expect": "exit0"}},
        {"id": "p2", "acceptance": {}},
    ]
}


def test_verify_phase_runs_the_guard_the_plan_declared():
    """Not one the agent chose — that would be marking its own homework."""
    seen = []
    v = verify_phase(PLAN, "p1", "/wt", runner=runner(0, capture=seen))
    assert "test -s out.txt" in seen[0][-1]
    assert v.phase_id == "p1" and v.ok


def test_a_phase_without_a_guard_errors_rather_than_passing():
    assert verify_phase(PLAN, "p2", "/wt", runner=runner(0)).status == ERROR


def test_an_unknown_phase_raises():
    import pytest

    with pytest.raises(KeyError):
        verify_phase(PLAN, "ghost", "/wt", runner=runner(0))


# ------------------------------------------------------------------- rework --


def test_the_rework_brief_quotes_the_guards_own_output():
    """A paraphrase of a failure is another chance to soften it."""
    v = run_guard("pytest -q", "/wt", runner=runner(1, "E   assert 3 == 4"))
    brief = rework_brief(v, attempt=2)
    assert "E   assert 3 == 4" in brief
    assert "attempt 2" in brief


def test_the_rework_brief_forbids_weakening_the_guard():
    v = run_guard("pytest -q", "/wt", runner=runner(1, "fail"))
    brief = rework_brief(v, attempt=1)
    assert "Do NOT edit or weaken the guard" in brief


def test_the_rework_brief_says_the_guard_will_be_rerun_by_someone_else():
    v = run_guard("pytest -q", "/wt", runner=runner(1, "fail"))
    assert "run again by Layer 1" in rework_brief(v, attempt=1)


def test_the_rework_brief_offers_the_escalation_path():
    """If the guard is genuinely wrong, escalate — do not silently adjust it."""
    v = run_guard("pytest -q", "/wt", runner=runner(1, "fail"))
    assert "needs_revision" in rework_brief(v, attempt=1)


# ------------------------------------------------------------------- ledger --


def test_a_verdict_is_recorded_for_audit(tmp_path):
    led = Ledger(tmp_path / "verify.ndjson")
    record(led, verify_phase(PLAN, "p1", "/wt", runner=runner(0, "ok")))
    history = read_history(led, "p1")
    assert len(history) == 1
    assert history[0]["status"] == PASS
    assert history[0]["guard"] == "test -s out.txt"


def test_spawned_reworks_are_counted_for_the_cap(tmp_path):
    led = Ledger(tmp_path / "verify.ndjson")
    for _ in range(3):
        record_rework(led, "p1", "anthropic/opus")
    assert attempts_for(led, "p1") == 3


def test_inspecting_a_phase_does_not_spend_its_rework_budget(tmp_path):
    """Measured live 2026-09-11: three `cao verify` runs on one phase -- two
    of them pure inspection -- reported "3 failed attempts (cap 3)" and
    escalated to a human. `verify` is the read-mostly operator surface."""
    led = Ledger(tmp_path / "verify.ndjson")
    for _ in range(5):
        record(led, verify_phase(PLAN, "p1", "/wt", runner=runner(1, "nope")))
    assert attempts_for(led, "p1") == 0
    assert failures_for(led, "p1") == 5  # still visible, just not charged


def test_the_two_counts_are_independent(tmp_path):
    led = Ledger(tmp_path / "verify.ndjson")
    record(led, verify_phase(PLAN, "p1", "/wt", runner=runner(1)))
    record_rework(led, "p1")
    record(led, verify_phase(PLAN, "p1", "/wt", runner=runner(1)))
    assert (attempts_for(led, "p1"), failures_for(led, "p1")) == (1, 2)


def test_reworks_are_counted_per_phase(tmp_path):
    led = Ledger(tmp_path / "verify.ndjson")
    record_rework(led, "p1")
    assert attempts_for(led, "p2") == 0


def test_history_is_per_phase(tmp_path):
    led = Ledger(tmp_path / "verify.ndjson")
    record(led, verify_phase(PLAN, "p1", "/wt", runner=runner(1)))
    assert read_history(led, "p2") == []


# --------------------------------------------------------------------------
# "The guard ran and the code failed" is a rework. "The guard could not run" is
# a broken environment. Measured: a missing worktree made `cd` return 1, which
# read as a test failure, ate three rework attempts, and escalated to a human
# blaming code that had never been executed.
# --------------------------------------------------------------------------


def test_an_unreachable_worktree_is_an_error_not_a_failure():
    from cao.verify import CANNOT_RUN

    v = run_guard("pytest -q", "/nope", runner=runner(CANNOT_RUN))
    assert v.status == ERROR
    assert v.ok is False
    assert "never ran" in v.output


def test_the_wrapper_cannot_collide_with_a_real_exit_code():
    """97 is outside the range test runners use."""
    from cao.verify import CANNOT_RUN

    assert CANNOT_RUN not in range(0, 10)


def test_a_genuine_failure_is_still_a_failure():
    v = run_guard("pytest -q", "/wt", runner=runner(1, "1 failed"))
    assert v.status == FAIL


def test_the_cd_failure_is_trapped_in_the_command():
    seen = []
    run_guard("pytest -q", "/wt", runner=runner(0, capture=seen))
    assert "|| exit 97" in seen[0][-1]


def test_environment_errors_do_not_consume_rework_attempts(tmp_path):
    """Only real failures count toward the cap, or a broken path burns it."""
    from cao.verify import CANNOT_RUN

    led = Ledger(tmp_path / "verify.ndjson")
    for _ in range(5):
        record(led, verify_phase(PLAN, "p1", "/nope", runner=runner(CANNOT_RUN)))
    assert attempts_for(led, "p1") == 0


# --------------------------------------------------------------------------
# Running the guard proves whether the work is done. It does NOT reveal that an
# agent SAID otherwise -- and that is the more useful signal. A failure after an
# honest "blocked" is ordinary; a failure after "ok" means the reporting cannot
# be trusted on ANY phase, including those whose guards happened to pass.
# --------------------------------------------------------------------------

from cao.verify import AGREED, CONTRADICTED, NO_CLAIM, compare_claim

OK_RESULT = (
    "RESULT p1 status=ok\n"
    "evidence:      wrote out.txt\n"
    "files_changed: out.txt\n"
    "notes:         done\n"
    "cost:          pool=deepseek\n"
)
BLOCKED_RESULT = "RESULT p1 status=blocked\nevidence: no write tool\nnotes: -\n"


def test_a_pass_matching_an_ok_claim_agrees():
    v = run_guard("t", "/wt", runner=runner(0, "fine"))
    finding, claim = compare_claim(v, OK_RESULT)
    assert finding == AGREED
    assert claim["status"] == "ok"


def test_a_failure_after_an_ok_claim_is_a_contradiction():
    """The agent said done; the exit code says otherwise."""
    v = run_guard("t", "/wt", runner=runner(1, "boom"))
    finding, _ = compare_claim(v, OK_RESULT)
    assert finding == CONTRADICTED


def test_a_failure_after_an_honest_blocked_claim_is_not_a_contradiction():
    v = run_guard("t", "/wt", runner=runner(1, "boom"))
    assert compare_claim(v, BLOCKED_RESULT)[0] == AGREED


def test_unparseable_terminal_text_is_no_claim_not_a_contradiction():
    """Absence of a RESULT is not evidence of dishonesty."""
    v = run_guard("t", "/wt", runner=runner(1, "boom"))
    finding, claim = compare_claim(v, "the pane was full of ANSI noise")
    assert finding == NO_CLAIM
    assert claim is None


def test_empty_terminal_text_is_no_claim():
    v = run_guard("t", "/wt", runner=runner(0, "fine"))
    assert compare_claim(v, "")[0] == NO_CLAIM


def test_an_environment_error_never_reads_as_a_contradiction():
    """The guard never ran, so it says nothing about the agent's honesty."""
    from cao.verify import CANNOT_RUN

    v = run_guard("t", "/nope", runner=runner(CANNOT_RUN))
    assert compare_claim(v, OK_RESULT)[0] == AGREED
