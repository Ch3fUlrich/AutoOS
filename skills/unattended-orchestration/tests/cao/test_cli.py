import json
import shutil
from pathlib import Path

import pytest

from cao.cli import build_parser, main

SKILL = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo(tmp_path):
    shutil.copy(SKILL / "handoff.config.example.json", tmp_path / "handoff.config.json")
    return tmp_path


def test_verb_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_unknown_verb_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["frobnicate"])


def _server(monkeypatch, up=True):
    """Pin server reachability. Without this these tests would pass or fail
    depending on whether something happens to be listening on :9889 on the
    machine running them -- which is not a property of the code."""
    if up:
        monkeypatch.setattr("cao.caoapi.CaoClient.health", lambda self: {"status": "ok"})
    else:
        def down(self):
            raise OSError("connection refused")
        monkeypatch.setattr("cao.caoapi.CaoClient.health", down)


def test_check_reports_pools_and_warnings(repo, capsys, monkeypatch):
    _server(monkeypatch)
    assert main(["--repo", str(repo), "check"]) == 0
    out = capsys.readouterr().out
    assert "pools:" in out
    assert "anthropic" in out and "deepseek" in out
    assert "warnings" in out


def test_check_marks_a_pool_without_credentials(repo, capsys, monkeypatch):
    _server(monkeypatch)
    main(["--repo", str(repo), "check"])
    out = capsys.readouterr().out
    # No secrets file in the fixture repo, so the keyed pool must show as missing.
    assert "NO CREDENTIALS" in out


def test_check_does_not_fail_on_a_missing_pool(repo, monkeypatch):
    """Ladders skip an unavailable pool; setup_cao.py --check is the gate."""
    _server(monkeypatch)
    assert main(["--repo", str(repo), "check"]) == 0


def test_check_reports_a_clean_default_config(repo, capsys, monkeypatch):
    """The shipped config puts the spawner on a hard-enforcement provider, so
    its read-only limit is real and there is nothing to warn about."""
    _server(monkeypatch)
    main(["--repo", str(repo), "check"])
    assert "warnings (0)" in capsys.readouterr().out


def test_profiles_generates_and_prints_install_commands(repo, tmp_path, capsys):
    out_dir = tmp_path / "profiles"
    assert main(["--repo", str(repo), "profiles", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "_L2_supervisor" in out
    assert "cao install" in out
    assert len(list(out_dir.glob("*.md"))) == 2


def test_profiles_reminds_about_the_two_traps(repo, tmp_path, capsys):
    """CAO_HOME_DIR and --provider are the two things that silently fail."""
    main(["--repo", str(repo), "profiles", "--out", str(tmp_path / "p")])
    out = capsys.readouterr().out
    assert "CAO_HOME_DIR" in out
    assert "--provider" in out


def test_sweep_reports_unreachable_server_without_crashing(repo, capsys, monkeypatch):
    def boom(self):
        raise OSError("connection refused")

    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", boom)
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", boom)
    assert main(["--repo", str(repo), "sweep"]) == 2
    assert "cannot reach CAO" in capsys.readouterr().err


def test_sweep_is_clean_when_nothing_is_running(repo, capsys, monkeypatch):
    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", lambda self: [])
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", lambda self, detail=True: [])
    assert main(["--repo", str(repo), "sweep"]) == 0
    out = capsys.readouterr().out
    assert "0 session(s), 0 terminal(s)" in out
    assert "no terminal is waiting for input" in out


def test_sweep_flags_a_depth_violation(repo, capsys, monkeypatch):
    deep = [
        {"id": "a", "caller_id": None, "agent_profile": "cao_L2_supervisor", "status": "idle"},
        {"id": "b", "caller_id": "a", "agent_profile": "cao_L3_worker", "status": "idle"},
        {"id": "c", "caller_id": "b", "agent_profile": "cao_L4_worker", "status": "idle"},
        {"id": "d", "caller_id": "c", "agent_profile": "cao_L5_worker", "status": "idle"},
    ]
    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", lambda self: [{"name": "s"}])
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", lambda self, detail=True: deep)
    assert main(["--repo", str(repo), "sweep"]) == 1
    out = capsys.readouterr().out
    # cao.maxDepth 3 counts L1 (this session, not a CAO terminal), so CAO
    # terminals may be at most depth 2: 'c' (L4, depth 3) and 'd' (L5) violate.
    assert "2 violation(s)" in out
    assert "cao_L4_worker" in out
    assert "cao_L5_worker" in out
    assert "CAO terminal depth <= 2" in out


def test_sweep_flags_a_blocked_terminal(repo, capsys, monkeypatch):
    terms = [{"id": "w1", "caller_id": None, "agent_profile": "cao_L3_worker",
              "status": "waiting_user_answer"}]
    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", lambda self: [{"name": "s"}])
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", lambda self, detail=True: terms)
    assert main(["--repo", str(repo), "sweep"]) == 1
    assert "ESCALATE TO HUMAN" in capsys.readouterr().out


def test_enforce_never_terminates_an_unknown_depth(repo, capsys, monkeypatch):
    orphan = [{"id": "x", "caller_id": "vanished", "agent_profile": "cao_L3_worker",
               "status": "idle"}]
    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", lambda self: [{"name": "s"}])
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", lambda self, detail=True: orphan)
    main(["--repo", str(repo), "sweep", "--enforce"])
    out = capsys.readouterr().out
    assert "terminating 0" in out
    assert "would terminate x" not in out


def test_missing_config_gives_one_actionable_line_not_a_traceback(tmp_path, capsys):
    """Found by running it: a raw FileNotFoundError buried the cause."""
    assert main(["--repo", str(tmp_path), "check"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: no ")
    assert "Traceback" not in err
    assert err.count("\n") == 1


def test_missing_config_points_at_the_example_when_one_exists(tmp_path, capsys):
    shutil.copy(SKILL / "handoff.config.example.json",
                tmp_path / "handoff.config.example.json")
    main(["--repo", str(tmp_path), "check"])
    assert "copy handoff.config.example.json" in capsys.readouterr().err


def test_malformed_config_is_also_a_clean_error(tmp_path, capsys):
    (tmp_path / "handoff.config.json").write_text("{not json", encoding="utf-8")
    assert main(["--repo", str(tmp_path), "check"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_config_without_a_cao_block_is_a_clean_error(tmp_path, capsys):
    (tmp_path / "handoff.config.json").write_text(json.dumps({"baseBranch": "main"}),
                                                  encoding="utf-8")
    assert main(["--repo", str(tmp_path), "check"]) == 2
    assert "no 'cao' block" in capsys.readouterr().err


# --------------------------------------------------------------------------
# resume / sweep --answer. may_resume and auto_answerable had no caller, so
# neither capability existed despite being tested and documented.
# --------------------------------------------------------------------------


def _patch_cao(monkeypatch, terminals=(), live=(), fail=False):
    def boom(self, *a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("cao.caoapi.CaoClient.sessions",
                        boom if fail else (lambda self: [{"name": "s"}]))
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals",
                        boom if fail else (lambda self, detail=True: list(terminals)))
    monkeypatch.setattr("cao.caoapi.CaoClient.live_task_ids",
                        boom if fail else (lambda self: set(live)))


def test_resume_with_no_leases_is_a_clean_noop(repo, capsys, monkeypatch):
    _patch_cao(monkeypatch)
    assert main(["--repo", str(repo), "resume"]) == 0
    assert "nothing was interrupted" in capsys.readouterr().out


def test_resume_refuses_when_cao_is_unreachable(repo, capsys, monkeypatch):
    """An empty live list would read as 'nobody is working' and license a restart."""
    from cao import leases

    leases.claim(repo / "output/cao/leases", "p1", "term-a", lease_minutes=0)
    _patch_cao(monkeypatch, fail=True)
    assert main(["--repo", str(repo), "resume"]) == 2
    assert "Refusing to resume" in capsys.readouterr().err


def test_resume_holds_a_phase_cao_says_is_alive(repo, capsys, monkeypatch):
    from cao import leases

    leases.claim(repo / "output/cao/leases", "p1", "term-a", lease_minutes=0)
    _patch_cao(monkeypatch, live=["p1"])
    main(["--repo", str(repo), "resume"])
    out = capsys.readouterr().out
    assert "HOLD   p1" in out
    assert "still alive per CAO" in out


def test_resume_offers_an_expired_orphaned_phase(repo, capsys, monkeypatch):
    from cao import leases

    leases.claim(repo / "output/cao/leases", "p1", "term-a", lease_minutes=0)
    _patch_cao(monkeypatch, live=[])
    assert main(["--repo", str(repo), "resume"]) == 1
    out = capsys.readouterr().out
    assert "RESUME p1" in out
    assert "--apply" in out


def test_resume_holds_a_phase_whose_attempts_are_spent(repo, capsys, monkeypatch):
    from cao import leases

    leases.claim(repo / "output/cao/leases", "p1", "term-a", lease_minutes=0, attempt=3)
    _patch_cao(monkeypatch, live=[])
    main(["--repo", str(repo), "resume"])
    assert "attempts exhausted" in capsys.readouterr().out


BLOCKED = {"id": "w1", "caller_id": None, "agent_profile": "cao_x_L3_worker",
           "status": "waiting_user_answer"}


def test_sweep_answers_a_prompt_the_config_already_implies(repo, capsys, monkeypatch):
    _patch_cao(monkeypatch, terminals=[BLOCKED])
    monkeypatch.setattr("cao.caoapi.CaoClient.terminal_output",
                        lambda self, tid, lines=60: "Permission required: Access external directory /tmp")
    sent = []
    monkeypatch.setattr("cao.caoapi.CaoClient.answer",
                        lambda self, tid, text: sent.append((tid, text)) or {})
    main(["--repo", str(repo), "sweep", "--answer"])
    assert sent == [("w1", "Allow always")]


def test_sweep_never_answers_a_prompt_about_money(repo, capsys, monkeypatch):
    _patch_cao(monkeypatch, terminals=[BLOCKED])
    monkeypatch.setattr("cao.caoapi.CaoClient.terminal_output",
                        lambda self, tid, lines=60: "Confirm purchase of 100 credits?")
    sent = []
    monkeypatch.setattr("cao.caoapi.CaoClient.answer",
                        lambda self, tid, text: sent.append((tid, text)) or {})
    main(["--repo", str(repo), "sweep", "--answer"])
    assert sent == []
    assert "ESCALATED" in capsys.readouterr().out


def test_sweep_without_answer_changes_nothing(repo, capsys, monkeypatch):
    _patch_cao(monkeypatch, terminals=[BLOCKED])
    monkeypatch.setattr("cao.caoapi.CaoClient.terminal_output",
                        lambda self, tid, lines=60: "Permission required: Access external directory /tmp")
    sent = []
    monkeypatch.setattr("cao.caoapi.CaoClient.answer",
                        lambda self, tid, text: sent.append((tid, text)) or {})
    main(["--repo", str(repo), "sweep"])
    assert sent == []


def test_an_unreadable_prompt_is_escalated_not_guessed(repo, capsys, monkeypatch):
    def boom(self, tid, lines=60):
        raise OSError("pane gone")

    _patch_cao(monkeypatch, terminals=[BLOCKED])
    monkeypatch.setattr("cao.caoapi.CaoClient.terminal_output", boom)
    sent = []
    monkeypatch.setattr("cao.caoapi.CaoClient.answer",
                        lambda self, tid, text: sent.append((tid, text)) or {})
    main(["--repo", str(repo), "sweep", "--answer"])
    assert sent == []


def test_check_records_an_uncredentialed_pool_so_routing_skips_it(repo, tmp_path, monkeypatch):
    """Printing 'NO CREDENTIALS' left the ledger ignorant, so ladders still
    dispatched to a pool with no key and read the failure as a code problem."""
    from cao.budget import Ledger, is_open

    _server(monkeypatch)
    main(["--repo", str(repo), "check", "--state-dir", "st"])
    led = Ledger(repo / "st" / "budget.ndjson")
    assert is_open(led, "deepseek") is False
    assert is_open(led, "anthropic") is True


def test_check_is_idempotent(repo, monkeypatch):
    from cao.budget import Ledger, is_open

    _server(monkeypatch)
    main(["--repo", str(repo), "check", "--state-dir", "st"])
    main(["--repo", str(repo), "check", "--state-dir", "st"])
    assert is_open(Ledger(repo / "st" / "budget.ndjson"), "deepseek") is False


# ---------------------------------------------------------------------------
# A guard verdict answers "is it done". The more useful question is whether the
# agent SAID it was done anyway -- a phase that fails after "status=ok" means
# this agent's reports cannot be trusted on the phases whose guards passed.
# The verdict is injected here: whether bash on THIS host can cd into a
# Windows path is not what these tests are about.
# ---------------------------------------------------------------------------

from cao import verify as _verify

OK_CLAIM = """RESULT p1 status=ok
evidence: wrote it
notes: -
"""


def _plan_at(repo):
    import json

    doc = {
        "schema": "cao-plan/1", "goal": "g", "createdBy": "claude-code",
        "approvedAt": "2026-09-11T09:00:00Z", "depth": 3,
        "openQuestions": [{"q": "q?", "answer": "a", "answeredBy": "user"}],
        "assumedDefaults": [],
        "phases": [{"id": "p1", "goal": "x", "taskClass": "implement",
                    "acceptance": {"guard": "pytest -q", "expect": "exit0"},
                    "dependsOn": [], "files": []}],
    }
    (repo / "PLAN.lock.json").write_text(json.dumps(doc), encoding="utf-8")


def _guard(monkeypatch, status, code):
    monkeypatch.setattr(
        _verify, "run_guard",
        lambda guard, worktree, **kw: _verify.Verdict(
            "", status, guard, code, "1 failed", "2026-09-11T09:30:00Z"),
    )


def _claim(monkeypatch, text=OK_CLAIM):
    monkeypatch.setattr("cao.caoapi.CaoClient.terminal_output",
                        lambda self, tid, lines=60: text)


def _verify_argv(repo, *extra):
    return ["--repo", str(repo), "verify", "--phase", "p1",
            "--plan", str(repo / "PLAN.lock.json"),
            "--worktree", str(repo), *extra]


def test_verify_flags_a_claim_the_guard_contradicts(repo, capsys, monkeypatch):
    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    _claim(monkeypatch)
    main(_verify_argv(repo, "--terminal", "t1"))
    err = capsys.readouterr().err
    assert "CONTRADICTION" in err
    assert "other reports" in err  # the point: it taints the agent, not the phase


def test_the_contradiction_is_recorded_not_just_printed(repo, capsys, monkeypatch):
    """An operator who was asleep must still find it afterwards."""
    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    _claim(monkeypatch)
    main(_verify_argv(repo, "--terminal", "t1"))
    written = (repo / "output" / "cao" / "verify.ndjson").read_text(
        encoding="utf-8")
    assert '"kind":"contradiction"' in written
    assert '"claimed":"ok"' in written


def test_verify_is_quiet_when_claim_and_guard_agree(repo, capsys, monkeypatch):
    _plan_at(repo)
    _guard(monkeypatch, _verify.PASS, 0)
    _claim(monkeypatch)
    main(_verify_argv(repo, "--terminal", "t1"))
    assert "CONTRADICTION" not in capsys.readouterr().err


def test_an_honest_blocked_report_is_not_a_contradiction(repo, capsys, monkeypatch):
    """Failing after saying so is ordinary work, not dishonesty."""
    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    _claim(monkeypatch, "RESULT p1 status=blocked\nevidence: -\nnotes: stuck\n")
    main(_verify_argv(repo, "--terminal", "t1"))
    assert "CONTRADICTION" not in capsys.readouterr().err


def test_a_guard_that_could_not_run_never_accuses_anyone(repo, capsys, monkeypatch):
    """CANNOT_RUN is the absence of evidence; it cannot contradict a claim."""
    _plan_at(repo)
    _guard(monkeypatch, _verify.ERROR, _verify.CANNOT_RUN)
    _claim(monkeypatch)
    main(_verify_argv(repo, "--terminal", "t1"))
    assert "CONTRADICTION" not in capsys.readouterr().err


def test_verify_without_a_terminal_skips_the_comparison(repo, capsys, monkeypatch):
    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    main(_verify_argv(repo))
    assert "CONTRADICTION" not in capsys.readouterr().err


def test_an_unreadable_terminal_does_not_break_verification(repo, capsys, monkeypatch):
    def boom(self, tid, lines=60):
        raise OSError("pane gone")

    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    monkeypatch.setattr("cao.caoapi.CaoClient.terminal_output", boom)
    assert main(_verify_argv(repo, "--terminal", "t1")) == 1


# ---------------------------------------------------------------------------
# The operator-facing half of quota handling. Classification is tested in
# test_budget; what matters here is that `cao launch` SAYS which wall it hit --
# "cooled for an hour" and "closed until someone pays" are different
# instructions, and an edit that silently fails to apply leaves the wrong one
# printed forever.
# ---------------------------------------------------------------------------


class _Done:
    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0


def _launch_emitting(repo, monkeypatch, provider_output):
    import json

    doc = {
        "schema": "cao-plan/1", "goal": "g", "createdBy": "claude-code",
        "approvedAt": "2026-09-11T09:00:00Z", "depth": 3,
        "openQuestions": [{"q": "q?", "answer": "a", "answeredBy": "user"}],
        "assumedDefaults": [],
        "phases": [{"id": "p1", "goal": "x", "taskClass": "implement",
                    "acceptance": {"guard": "pytest -q", "expect": "exit0"},
                    "dependsOn": [], "files": []}],
    }
    (repo / "PLAN.lock.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Done(provider_output))
    main(["--repo", str(repo), "launch", "--phase", "p1",
          "--plan", str(repo / "PLAN.lock.json")])


def test_launch_says_CLOSED_for_a_billing_wall(repo, capsys, monkeypatch):
    _launch_emitting(repo, monkeypatch, '{"type":"billing_error"}')
    out = capsys.readouterr().out
    assert "CLOSED" in out and "needs a human" in out
    assert "cooled" not in out


def test_launch_says_cooled_for_exhaustion(repo, capsys, monkeypatch):
    _launch_emitting(repo, monkeypatch, "Usage limit reached")
    out = capsys.readouterr().out
    assert "cooled for an hour" in out
    assert "CLOSED" not in out


def test_launch_says_nothing_about_quota_for_congestion(repo, capsys, monkeypatch):
    """A 529 is not a wall; announcing one would invite a needless descent."""
    _launch_emitting(repo, monkeypatch, '{"type":"overloaded_error"}')
    out = capsys.readouterr().out
    assert "quota:" not in out


def test_verify_says_when_an_agent_never_reported(repo, capsys, monkeypatch):
    """Measured live: a supervisor was killed by a Remote Control disconnect
    mid-delegation. CAO marked the terminal "completed", the guard failed, and
    nothing told the operator the agent had never reported at all."""
    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    _claim(monkeypatch, "working... no envelope here")
    main(_verify_argv(repo, "--terminal", "t1"))
    err = capsys.readouterr().err
    assert "NO REPORT" in err
    assert "not a false claim" in err  # silence is not dishonesty
    assert "CONTRADICTION" not in err


def test_a_passing_guard_with_no_report_says_nothing(repo, capsys, monkeypatch):
    """If the work is done, how it was reported is not an operator's problem."""
    _plan_at(repo)
    _guard(monkeypatch, _verify.PASS, 0)
    _claim(monkeypatch, "no envelope")
    main(_verify_argv(repo, "--terminal", "t1"))
    assert "NO REPORT" not in capsys.readouterr().err


def test_the_envelope_is_found_in_an_ansi_pane(repo, capsys, monkeypatch):
    """End to end: a real capture is ANSI, and the check must still fire."""
    esc = chr(27)
    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    _claim(monkeypatch,
           f"{esc}[2mbanner{esc}[0m\r\nchatter\r\n{esc}[1mRESULT p1 status=ok{esc}[0m\r\n"
           "evidence: all good\r\n")
    main(_verify_argv(repo, "--terminal", "t1"))
    assert "CONTRADICTION" in capsys.readouterr().err


def test_a_spawned_rework_is_recorded_against_the_cap(repo, monkeypatch, capsys):
    """The counter only means something if the spawn writes to it. Measured:
    `record_rework` was imported and never called, so every rework was the
    first one forever and maxAttempts could not stop anything."""
    from cao.budget import Ledger
    from cao.verify import attempts_for

    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Done("launched"))
    # A worktree, not the checkout: plan_launch refuses the latter, by design.
    main(["--repo", str(repo), "verify", "--phase", "p1",
          "--plan", str(repo / "PLAN.lock.json"),
          "--worktree", str(repo / "wt"), "--rework"])
    assert attempts_for(Ledger(repo / "output" / "cao" / "verify.ndjson"), "p1") == 1


def test_inspecting_never_records_an_attempt(repo, monkeypatch, capsys):
    from cao.budget import Ledger
    from cao.verify import attempts_for

    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    for _ in range(3):
        main(_verify_argv(repo))
    assert attempts_for(Ledger(repo / "output" / "cao" / "verify.ndjson"), "p1") == 0


def test_the_cap_stops_a_run_that_never_converges(repo, monkeypatch, capsys):
    from cao.budget import Ledger
    from cao.verify import record_rework

    _plan_at(repo)
    _guard(monkeypatch, _verify.FAIL, 1)
    led = Ledger(repo / "output" / "cao" / "verify.ndjson")
    for _ in range(3):
        record_rework(led, "p1")
    assert main(["--repo", str(repo), "verify", "--phase", "p1",
                 "--plan", str(repo / "PLAN.lock.json"),
                 "--worktree", str(repo / "wt"), "--rework"]) == 4
    assert "NEEDS HUMAN" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `check` asks whether credentials exist. `probe` asks whether the pool WORKS.
# Measured live 2026-09-11: probe reported `antigravity failed`, routing chose
# antigravity anyway, and two spawned workers died on a model the provider
# would not serve. Only the second question should gate dispatch.
# ---------------------------------------------------------------------------


#: The order cmd_probe walks its pools. Keying the fake on argv would key on
#: the BINARY name (agy, opencode), which is exactly the coupling that made an
#: earlier version of this fake silently return "ok" for everything.
PROBE_ORDER = ("anthropic", "antigravity", "deepseek")


def _probe_returning(monkeypatch, verdicts):
    """verdicts: pool -> (stdout, returncode); anything absent probes ok."""
    import subprocess as sp

    calls = iter(PROBE_ORDER)

    def fake(argv, **kw):
        out, rc = verdicts.get(next(calls), ("PROBE_OK", 0))
        return sp.CompletedProcess(argv, rc, out, "")

    monkeypatch.setattr("subprocess.run", fake)


def test_the_probe_fake_matches_the_verbs_real_pool_order(repo, monkeypatch, capsys):
    """If cmd_probe's order changes, the tests below silently test nothing."""
    seen = []
    import subprocess as sp
    monkeypatch.setattr("subprocess.run",
                        lambda argv, **k: seen.append(" ".join(argv))
                        or sp.CompletedProcess(argv, 0, "PROBE_OK", ""))
    main(["--repo", str(repo), "probe", "--timeout", "1"])
    assert len(seen) == len(PROBE_ORDER)


def test_a_proven_failure_closes_the_pool_for_routing(repo, monkeypatch, capsys):
    """Only PROOF closes a pool: not installed, or not authenticated."""
    from cao.budget import Ledger, is_open

    _probe_returning(monkeypatch, {"antigravity": ("boom: not signed in", 1)})
    main(["--repo", str(repo), "probe", "--timeout", "1"])
    led = Ledger(repo / "output" / "cao" / "budget.ndjson")
    assert is_open(led, "antigravity") is False
    assert is_open(led, "anthropic") is True


def test_a_passing_probe_reinstates_a_pool(repo, monkeypatch, capsys):
    """One bad minute must not remove a pool for the rest of the run."""
    from cao.budget import Ledger, is_open, mark_unavailable

    led = Ledger(repo / "output" / "cao" / "budget.ndjson")
    mark_unavailable(led, "antigravity", reason="probe: failed")
    assert is_open(led, "antigravity") is False
    _probe_returning(monkeypatch, {})
    main(["--repo", str(repo), "probe", "--timeout", "1"])
    assert is_open(led, "antigravity") is True


def test_the_probe_reason_says_which_verb_closed_it(repo, monkeypatch, capsys):
    from cao.budget import Ledger

    _probe_returning(monkeypatch, {"deepseek": ("command not found: opencode", 1)})
    main(["--repo", str(repo), "probe", "--timeout", "1"])
    rec = [r for r in Ledger(repo / "output" / "cao" / "budget.ndjson").read()
           if r["pool"] == "deepseek"][-1]
    assert rec["reason"].startswith("probe:")


def test_an_inconclusive_probe_never_closes_a_pool(repo, monkeypatch, capsys):
    """Measured 2026-09-11: `agy -p` answers PROBE_OK with exit 0 inside tmux
    and prints "error: interrupted" when stdout is a pipe. The probe always
    captures output, so a healthy provider always looked broken -- and once
    probe gated routing, that false negative DECLINED a working pool. This
    lane's standing rule is that a pool is never declined."""
    from cao.budget import Ledger, is_open

    _probe_returning(monkeypatch, {"antigravity": ("error: interrupted", 0)})
    main(["--repo", str(repo), "probe", "--timeout", "1"])
    assert is_open(Ledger(repo / "output" / "cao" / "budget.ndjson"),
                   "antigravity") is True


def test_an_unrecognised_banner_never_closes_a_pool(repo, monkeypatch, capsys):
    """A CLI that printed something we do not understand is not proof."""
    from cao.budget import Ledger, is_open

    _probe_returning(monkeypatch, {"deepseek": ("update available: v9", 0)})
    main(["--repo", str(repo), "probe", "--timeout", "1"])
    assert is_open(Ledger(repo / "output" / "cao" / "budget.ndjson"),
                   "deepseek") is True


def test_an_inconclusive_probe_is_still_a_nonzero_exit(repo, monkeypatch, capsys):
    """Not acted on is not the same as not reported."""
    _probe_returning(monkeypatch, {"antigravity": ("error: interrupted", 0)})
    assert main(["--repo", str(repo), "probe", "--timeout", "1"]) == 1


# ---------------------------------------------------------------------------
# Preflight has to be trustworthy before anything else is. `cao check` used to
# print three available pools, zero warnings and exit 0 while the CAO server --
# which launch, sweep, verify --terminal and resume all require -- was not
# running. An agent reads green, runs `launch`, and gets a connection error it
# had no reason to expect.
# ---------------------------------------------------------------------------


def test_check_says_not_ready_when_the_server_is_down(repo, capsys, monkeypatch):
    _server(monkeypatch, up=False)
    assert main(["--repo", str(repo), "check"]) == 1
    err = capsys.readouterr().err
    assert "NOT READY" in err
    assert "cao-server" in err  # the command that fixes it


def test_check_names_the_server_either_way(repo, capsys, monkeypatch):
    _server(monkeypatch, up=False)
    main(["--repo", str(repo), "check"])
    assert "UNREACHABLE" in capsys.readouterr().out
    _server(monkeypatch, up=True)
    main(["--repo", str(repo), "check"])
    assert "reachable" in capsys.readouterr().out


def test_a_down_server_does_not_hide_the_pool_report(repo, capsys, monkeypatch):
    """Preflight still has to say what it learned; it just cannot say ready."""
    _server(monkeypatch, up=False)
    main(["--repo", str(repo), "check"])
    out = capsys.readouterr().out
    assert "pools:" in out and "anthropic" in out


def test_a_reachable_server_with_no_pools_is_still_ready(repo, capsys, monkeypatch):
    """An uncredentialed pool is skipped by ladders, not a preflight failure."""
    _server(monkeypatch, up=True)
    assert main(["--repo", str(repo), "check"]) == 0


def test_sweep_reports_worktrees_whose_session_is_gone(repo, capsys, monkeypatch):
    """Measured 2026-09-11: three worktrees survived four live runs and had to
    be found by hand. Reported, never removed -- one may hold work an agent
    never committed, and "no session" can also mean the server restarted."""
    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", lambda self: [])
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", lambda self, detail=True: [])
    graph = Path(str(repo)).resolve().name
    porcelain = (f"worktree {repo}\nHEAD a\n\n"
                 f"worktree /tmp/cao-worktrees/{graph}-p1\nHEAD b\ndetached\n")
    import subprocess as sp
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: sp.CompletedProcess(a, 0, porcelain, ""))
    main(["--repo", str(repo), "sweep"])
    out = capsys.readouterr().out
    assert "worktrees with no session: 1" in out
    assert f"{graph}-p1" in out
    assert "worktree remove" in out  # the command that reclaims it


def test_sweep_says_nothing_to_reclaim_when_there_is_nothing(repo, capsys, monkeypatch):
    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", lambda self: [])
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", lambda self, detail=True: [])
    import subprocess as sp
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: sp.CompletedProcess(a, 0, f"worktree {repo}\n", ""))
    main(["--repo", str(repo), "sweep"])
    out = capsys.readouterr().out
    assert "worktrees with no session: 0" in out
    assert "worktree remove" not in out


def test_sweep_survives_git_being_unavailable(repo, capsys, monkeypatch):
    """Worktree reporting is a convenience; it must not break the sweep."""
    monkeypatch.setattr("cao.caoapi.CaoClient.sessions", lambda self: [])
    monkeypatch.setattr("cao.caoapi.CaoClient.all_terminals", lambda self, detail=True: [])

    def boom(*a, **k):
        raise OSError("git not found")

    monkeypatch.setattr("subprocess.run", boom)
    main(["--repo", str(repo), "sweep"])
    assert "worktrees with no session: 0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Every command this lane TELLS someone to run must be a real invocation.
# Measured 2026-09-11: `plan_launch`'s refusal -- the message an operator sees
# at the exact moment they need help -- said `python -m cao plan --init`, and
# there is no `--init`. Following it produces an argparse error on top of the
# original problem.
# ---------------------------------------------------------------------------

import re as _re

# The character class excludes a newline, a backtick and a comment marker. Built
# with chr(10) rather than an escape: this file is written through a heredoc
# where a doubled backslash collapses, which has silently broken edits all day.
_COMMAND = _re.compile("python -m cao ([a-z]+[^" + chr(10) + "`#]*)")


def _advertised_commands():
    """Every `python -m cao ...` string in the lane's code and its SKILL.md."""
    root = Path(__file__).resolve().parents[2]
    found = []
    for path in list((root / "cao").glob("*.py")) + [root / "SKILL.md"]:
        for match in _COMMAND.finditer(path.read_text(encoding="utf-8")):
            command = match.group(1).split("#")[0].strip().rstrip("`\"').,")
            if command:
                found.append((path.name, command))
    return found


def _argv(command):
    """Placeholders stand in for values the READER supplies, so give argparse
    something of the right shape rather than dropping the token and leaving a
    flag without its value."""
    out = []
    for token in command.split():
        if token.startswith("<") or (len(token) <= 2 and token.isupper()):
            out.append("PLACEHOLDER")
        else:
            out.append(token)
    return out


def _with_flags():
    """Only invocations carrying a flag. The module docstring lists bare verbs
    as a MENU (`python -m cao verify   # run the guard yourself`), which is not
    a runnable command and not what this guards against."""
    return [(w, c) for w, c in _advertised_commands() if "--" in c]


def test_we_advertise_some_flagged_commands():
    """A regex that matches nothing would make the test below vacuous."""
    assert len(_with_flags()) >= 6


def test_every_advertised_command_parses():
    from cao.cli import build_parser

    parser = build_parser()
    broken = []
    for where, command in _with_flags():
        try:
            parser.parse_args(_argv(command))
        except SystemExit:
            broken.append(f"{where}: python -m cao {command}")
    assert not broken, "commands we advertise that do not parse:\n" + "\n".join(broken)


def test_check_reports_an_explicit_missing_secrets_file_and_exits_1(repo, capsys, monkeypatch, tmp_path):
    """`check` turns a bad AUTOOS_SECRETS into one clear line and exit 1 (action needed), not a traceback."""
    _server(monkeypatch)
    missing = tmp_path / "nope.conf"
    monkeypatch.setenv("AUTOOS_SECRETS", str(missing))
    assert main(["--repo", str(repo), "check"]) == 1
    out = capsys.readouterr().out
    assert "AUTOOS_SECRETS points to a missing file" in out
