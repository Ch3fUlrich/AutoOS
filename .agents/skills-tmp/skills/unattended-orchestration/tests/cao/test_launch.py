"""The launch path — where the plan gate is actually enforced.

Before this existed, plan.py / budget.py / leases.py / wire.py were tested
libraries with no caller: the headline rule ("do not just start") was a function
nobody invoked. These tests exist to keep that from being true again.
"""

import json

import pytest

from cao.profiles import VALID_PROVIDERS
from cao.launch import pick_attempt, session_name_for
from cao.launch import LaunchRefused, plan_launch


def good_plan(task_class="implement"):
    return {
        "schema": "cao-plan/1",
        "goal": "add the thing",
        "createdBy": "claude-code",
        "approvedAt": "2026-09-10T14:22:03Z",
        "depth": 3,
        "openQuestions": [{"q": "which graph?", "answer": "agent-skills", "answeredBy": "user"}],
        "assumedDefaults": [],
        "phases": [
            {
                "id": "p1",
                "goal": "implement X",
                "taskClass": task_class,
                "acceptance": {"guard": "pytest -q", "expect": "exit0"},
                "dependsOn": [],
                "files": ["src/x.py"],
            }
        ],
    }


class Cfg:
    max_depth = 3
    server = "http://localhost:9889"
    worktree_root = "/home/u/cao-worktrees"
    review_pairing = "cross-family"
    resume = {"leaseMinutes": 30, "maxAttempts": 3}
    pools = {
        "anthropic": {"via": "claude"},
        "deepseek": {"via": "opencode", "apiKeyEnv": "DEEPSEEK_API_KEY"},
    }
    routing = {
        "implement": [
            {"pool": "deepseek", "family": "deepseek", "model": "deepseek-direct/deepseek-flash"},
            {"pool": "anthropic", "family": "anthropic", "model": "opus", "effort": "high"},
        ],
        "review": [
            {"pool": "anthropic", "family": "anthropic", "model": "opus", "effort": "high"},
            {"pool": "deepseek", "family": "deepseek", "model": "deepseek-direct/deepseek-flash"},
        ],
    }
    levels = [
        {"level": 1, "role": "orchestrator", "canSpawn": True},
        {"level": 2, "role": "supervisor", "canSpawn": True, "pool": "anthropic", "model": "opus"},
        {"level": 3, "role": "worker", "canSpawn": False, "pool": "deepseek"},
    ]
    repo_root = "/repo"


def _write(tmp_path, doc):
    p = tmp_path / "PLAN.lock.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ------------------------------------------------------------------ the gate --


def test_a_valid_plan_produces_a_launch(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert got.phase_id == "p1"
    assert got.candidate.model


def test_a_missing_plan_refuses(tmp_path):
    with pytest.raises(LaunchRefused, match="no plan contract"):
        plan_launch(Cfg(), tmp_path / "nope.json", "p1", lease_dir=tmp_path)


def test_an_unapproved_plan_refuses(tmp_path):
    doc = good_plan()
    del doc["approvedAt"]
    with pytest.raises(LaunchRefused, match="approvedAt"):
        plan_launch(Cfg(), _write(tmp_path, doc), "p1", lease_dir=tmp_path)


def test_a_plan_that_never_asked_the_user_refuses(tmp_path):
    """The whole point: an agent cannot skip the conversation and start."""
    doc = good_plan()
    doc["openQuestions"] = []
    doc["assumedDefaults"] = []
    with pytest.raises(LaunchRefused, match="ask the user"):
        plan_launch(Cfg(), _write(tmp_path, doc), "p1", lease_dir=tmp_path)


def test_a_phase_without_a_guard_refuses(tmp_path):
    doc = good_plan()
    doc["phases"][0]["acceptance"] = {"guard": "", "expect": "exit0"}
    with pytest.raises(LaunchRefused, match="acceptance.guard"):
        plan_launch(Cfg(), _write(tmp_path, doc), "p1", lease_dir=tmp_path)


def test_an_unknown_phase_names_the_known_ones(tmp_path):
    with pytest.raises(LaunchRefused, match="p1"):
        plan_launch(Cfg(), _write(tmp_path, good_plan()), "ghost", lease_dir=tmp_path)


def test_a_plan_deeper_than_maxdepth_refuses(tmp_path):
    doc = good_plan()
    doc["depth"] = 9
    with pytest.raises(LaunchRefused, match="depth 9 exceeds"):
        plan_launch(Cfg(), _write(tmp_path, doc), "p1", lease_dir=tmp_path)


# -------------------------------------------------------------- quota ladder --


def test_the_first_open_pool_is_chosen(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path,
                      is_open=lambda pool: True)
    assert got.candidate.pool == "deepseek"


def test_a_cooling_pool_is_skipped_and_recorded(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path,
                      is_open=lambda pool: pool != "deepseek")
    assert got.candidate.pool == "anthropic"
    assert got.descended is True


def test_every_pool_closed_refuses_rather_than_guessing(tmp_path):
    with pytest.raises(LaunchRefused, match="no open pool"):
        plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path,
                    is_open=lambda pool: False)


def test_an_unknown_task_class_refuses(tmp_path):
    with pytest.raises(LaunchRefused, match="task class"):
        plan_launch(Cfg(), _write(tmp_path, good_plan(task_class="frobnicate")), "p1",
                    lease_dir=tmp_path)


# -------------------------------------------------------------------- leases --


def test_a_lease_is_claimed(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert got.lease_held is True


def test_a_phase_already_claimed_refuses(tmp_path):
    plan = _write(tmp_path, good_plan())
    plan_launch(Cfg(), plan, "p1", lease_dir=tmp_path)
    with pytest.raises(LaunchRefused, match="already claimed"):
        plan_launch(Cfg(), plan, "p1", lease_dir=tmp_path)


def test_dry_run_claims_nothing(tmp_path):
    """A dry run that took the lease would block the real launch after it."""
    plan = _write(tmp_path, good_plan())
    plan_launch(Cfg(), plan, "p1", lease_dir=tmp_path, dry_run=True)
    got = plan_launch(Cfg(), plan, "p1", lease_dir=tmp_path)
    assert got.lease_held is True


# ------------------------------------------------------------------ envelope --


def test_the_task_envelope_carries_the_guard_and_plan_ref(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert "pytest -q" in got.task
    assert "plan_ref:" in got.task
    assert "PLAN.lock.json" in got.task


def test_the_envelope_never_inlines_the_plan(tmp_path):
    """A supervisor must reference the contract, not paraphrase it downward."""
    doc = good_plan()
    doc["goal"] = "SECRET_GOAL_TEXT " * 200
    got = plan_launch(Cfg(), _write(tmp_path, doc), "p1", lease_dir=tmp_path)
    assert got.task.count("SECRET_GOAL_TEXT") == 0


def test_depth_is_stated_against_the_maximum(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert "3" in got.task


# -------------------------------------------------------------------- launch --


def test_the_argv_carries_every_trap_fix(tmp_path):
    """CAO_HOME_DIR, --provider and a login shell are each a silent failure."""
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path,
                      wsl_distro="Ubuntu")
    argv = " ".join(got.argv)
    assert "CAO_HOME_DIR" in argv
    assert "--provider" in argv
    assert "bash -lc" in argv or "-lc" in got.argv


def test_the_graph_is_pinned_for_the_supervisor_and_its_workers(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert "OMNIGRAPH_GRAPH_ID" in " ".join(got.argv)


def test_a_linux_host_needs_no_wsl_wrapper(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path,
                      wsl_distro=None)
    assert "wsl" not in got.argv


def test_the_supervisor_profile_is_launched_not_the_leaf(tmp_path):
    """Layer 1 launches Layer 2; Layer 2 assigns Layer 3."""
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    argv = " ".join(got.argv)
    assert "_L2_supervisor" in argv
    assert "_L3_worker" not in argv


def test_the_supervisor_provider_comes_from_its_level_not_the_task(tmp_path):
    """The task ladder picks the WORKER's model; the supervisor is level config."""
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert "claude_code" in " ".join(got.argv)


# --------------------------------------------------------------------------
# Measured in a dry run: --env OMNIGRAPH_GRAPH_ID='' because repo_root was ".".
# An empty graph id is the wrong-graph failure this lane exists to prevent.
# --------------------------------------------------------------------------


def test_a_relative_repo_root_still_yields_a_real_graph_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RelCfg(Cfg):
        repo_root = "."

    got = plan_launch(RelCfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    argv = " ".join(got.argv)
    assert "OMNIGRAPH_GRAPH_ID=''" not in argv
    assert "OMNIGRAPH_GRAPH_ID=" in argv
    assert tmp_path.name in argv


def test_the_worktree_name_carries_the_repo_and_phase(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class RelCfg(Cfg):
        repo_root = "."

    got = plan_launch(RelCfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert got.worktree.endswith(f"{tmp_path.name}-p1")


def test_home_is_left_for_the_distro_shell_to_expand(tmp_path):
    r"""Expanding $HOME on Windows produced a C:\ path for a Linux shell."""
    class HomeCfg(Cfg):
        worktree_root = "$HOME/cao-worktrees"

    got = plan_launch(HomeCfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert "$HOME" in got.worktree
    assert "C:" not in got.worktree


# --------------------------------------------------------------------------
# Measured in a dry run: --working-directory '$HOME/...' was single-quoted, so
# the shell took it literally; and CAO resolves the working directory with
# allow_create=False, so an unprovisioned worktree fails the launch outright.
# --------------------------------------------------------------------------


def test_a_variable_path_is_double_quoted_so_the_distro_expands_it(tmp_path):
    class HomeCfg(Cfg):
        worktree_root = "$HOME/cao-worktrees"

    got = plan_launch(HomeCfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    inner = got.argv[-1]
    assert '"$HOME/cao-worktrees' in inner
    assert "'$HOME" not in inner


def test_a_literal_path_is_still_safely_quoted(tmp_path):
    class OddCfg(Cfg):
        worktree_root = "/home/u/work trees"

    got = plan_launch(OddCfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert "'/home/u/work trees" in got.argv[-1]


def test_the_worktree_is_provisioned_before_launch(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    inner = got.argv[-1]
    assert "worktree add" in inner
    assert inner.index("worktree add") < inner.index("cao launch")


def test_provisioning_is_idempotent(tmp_path):
    """A rerun must not fail on an existing worktree."""
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert "[ -d " in got.argv[-1]


def test_the_worktree_is_trusted_before_launch(tmp_path):
    """Untrusted, Claude Code never reaches idle and CAO deletes the terminal."""
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    inner = got.argv[-1]
    assert "trust_worktree.py" in inner
    assert inner.index("trust_worktree.py") < inner.index("cao launch")


def test_windows_translates_the_repo_path_for_the_distro(tmp_path):
    r"""C:\Users\... is not a path the distro can use."""
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path,
                      wsl_distro="Ubuntu")
    assert "wslpath -a" in got.argv[-1]


def test_linux_uses_the_repo_path_directly(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path,
                      wsl_distro=None)
    assert "wslpath" not in got.argv[-1]


def test_the_session_name_carries_the_project(tmp_path, monkeypatch):
    """`cao session list` must stay readable with several repos running."""
    monkeypatch.chdir(tmp_path)

    class RelCfg(Cfg):
        repo_root = "."

    got = plan_launch(RelCfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    argv = " ".join(got.argv)
    assert f"cao-{tmp_path.name}-p1" in argv


def test_the_launched_profile_is_project_scoped(tmp_path, monkeypatch):
    """An unscoped profile name is overwritten by the next project."""
    monkeypatch.chdir(tmp_path)

    class RelCfg(Cfg):
        repo_root = "."

    got = plan_launch(RelCfg(), _write(tmp_path, good_plan()), "p1", lease_dir=tmp_path)
    assert f"cao_{tmp_path.name}_L2_supervisor" in " ".join(got.argv)


# --------------------------------------------------------------------------
# Destruction. Leaves hold execute_bash and fs_*, i.e. an unrestricted shell.
# Pointed at the main checkout, one bad command destroys uncommitted work.
# --------------------------------------------------------------------------


def test_running_in_the_main_checkout_is_refused(tmp_path):
    class HereCfg(Cfg):
        repo_root = str(tmp_path)

    with pytest.raises(LaunchRefused, match="main checkout"):
        plan_launch(HereCfg(), _write(tmp_path, good_plan()), "p1",
                    lease_dir=tmp_path, worktree=str(tmp_path))


def test_the_refusal_explains_the_blast_radius(tmp_path):
    class HereCfg(Cfg):
        repo_root = str(tmp_path)

    with pytest.raises(LaunchRefused, match="disposable worktree"):
        plan_launch(HereCfg(), _write(tmp_path, good_plan()), "p1",
                    lease_dir=tmp_path, worktree=str(tmp_path))


def test_a_trailing_slash_does_not_defeat_the_check(tmp_path):
    class HereCfg(Cfg):
        repo_root = str(tmp_path)

    with pytest.raises(LaunchRefused, match="main checkout"):
        plan_launch(HereCfg(), _write(tmp_path, good_plan()), "p1",
                    lease_dir=tmp_path, worktree=str(tmp_path) + "/")


def test_a_real_worktree_is_allowed(tmp_path):
    class HereCfg(Cfg):
        repo_root = str(tmp_path)

    got = plan_launch(HereCfg(), _write(tmp_path, good_plan()), "p1",
                      lease_dir=tmp_path, worktree=str(tmp_path / "wt-p1"))
    assert got.worktree.endswith("wt-p1")


# --------------------------------------------------------------------------
# Cross-family review. Pairing is by FAMILY, not pool: claude-opus-4-6 via agy
# spends different quota and consults the same blind spots.
# --------------------------------------------------------------------------


def _review_plan():
    doc = good_plan()
    doc["phases"].append({
        "id": "rev", "goal": "review p1", "taskClass": "review",
        "acceptance": {"guard": "true", "expect": "exit0"},
        "dependsOn": ["p1"], "files": [],
    })
    return doc


def test_a_review_phase_pairs_against_a_different_family(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, _review_plan()), "rev",
                      lease_dir=tmp_path, implemented_by="anthropic")
    assert got.candidate.family != "anthropic"
    assert got.review_degraded is False
    assert any("cross-family" in n for n in got.notes)


def test_a_deepseek_implementation_is_reviewed_by_anthropic(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, _review_plan()), "rev",
                      lease_dir=tmp_path, implemented_by="deepseek")
    assert got.candidate.family == "anthropic"


def test_a_degraded_review_is_flagged_never_hidden(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, _review_plan()), "rev",
                      lease_dir=tmp_path, implemented_by="anthropic",
                      is_open=lambda pool: pool == "anthropic")
    assert got.review_degraded is True
    assert any("DEGRADED" in n for n in got.notes)


def test_a_review_with_no_reachable_reviewer_refuses(tmp_path):
    with pytest.raises(LaunchRefused, match="no reviewer"):
        plan_launch(Cfg(), _write(tmp_path, _review_plan()), "rev",
                    lease_dir=tmp_path, implemented_by="anthropic",
                    is_open=lambda pool: False)


def test_a_non_review_phase_ignores_the_pairing(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, _review_plan()), "p1",
                      lease_dir=tmp_path, implemented_by="anthropic")
    assert got.review_degraded is False
    assert got.candidate.pool == "deepseek"


def test_the_launch_reports_both_models_it_involves(tmp_path):
    """One says what is launched, the other what it will spawn. Conflating
    them made a dry run announce Gemini while starting an Opus supervisor."""
    result = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", dry_run=True)
    assert result.supervisor_profile, "the launched profile must be reported"
    assert result.supervisor_provider in VALID_PROVIDERS
    assert result.candidate.model
    assert result.supervisor_profile not in result.candidate.model


def test_the_envelope_tells_the_supervisor_which_model_to_spawn(tmp_path):
    result = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", dry_run=True)
    assert f"use_pool:   {result.candidate.pool}" in result.task
    assert f"use_model:  {result.candidate.model}" in result.task
    # The slash form is what killed a live worker.
    assert f"{result.candidate.pool}/{result.candidate.model}" not in result.task


# ---------------------------------------------------------------------------
# CAO session names are unique server-side. Measured live 2026-09-11, on the
# first rework this lane ever attempted:
#   Error: Failed to connect to cao-server: 400 Bad Request
#   {"detail":"Session 'cao-agent-skills-smoke1' already exists"}
# Every rework would have failed this way, which makes the rework path -- the
# lane's whole answer to "an agent did shit" -- unreachable in practice.
# ---------------------------------------------------------------------------


def _session_name(argv):
    joined = " ".join(argv)
    after = joined.split("--session-name ", 1)[1]
    return after.split(" ", 1)[0].strip("'\"")


def test_a_first_attempt_has_the_plain_session_name(tmp_path):
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", dry_run=True)
    assert _session_name(got.argv).endswith("-p1")
    assert "-r" not in _session_name(got.argv).rsplit("-p1", 1)[-1]


def test_a_rework_does_not_reuse_the_failed_attempts_name(tmp_path):
    plan = _write(tmp_path, good_plan())
    first = plan_launch(Cfg(), plan, "p1", dry_run=True)
    second = plan_launch(Cfg(), plan, "p1", dry_run=True, attempt=2)
    assert _session_name(first.argv) != _session_name(second.argv)
    assert _session_name(second.argv).endswith("-r2")


def test_every_attempt_gets_a_distinct_name(tmp_path):
    plan = _write(tmp_path, good_plan())
    names = {
        _session_name(plan_launch(Cfg(), plan, "p1", dry_run=True, attempt=n).argv)
        for n in range(1, 5)
    }
    assert len(names) == 4


def test_the_session_name_is_reported(tmp_path):
    """An operator needs it to find the terminal in `cao session list`."""
    got = plan_launch(Cfg(), _write(tmp_path, good_plan()), "p1", dry_run=True)
    assert got.session_name and got.session_name in " ".join(got.argv)


def test_the_first_free_name_is_chosen_from_what_exists():
    """Measured live: the ledger said "no reworks yet" while the server already
    held -r2, so every rework asked for a taken name and got a 400."""
    taken = ["cao-demo-p1", "cao-demo-p1-r2", "cao-demo-p1-r3"]
    assert pick_attempt(taken, "demo", "p1") == 4


def test_an_empty_server_starts_at_the_first_rework():
    assert pick_attempt([], "demo", "p1") == 2


def test_other_phases_and_projects_do_not_block_a_name():
    taken = ["cao-demo-p2-r2", "cao-other-p1-r2"]
    assert pick_attempt(taken, "demo", "p1") == 2


def test_a_namespace_full_of_dead_sessions_refuses_with_advice():
    taken = [session_name_for("demo", "p1", n) for n in range(2, 60)]
    with pytest.raises(LaunchRefused, match="cao shutdown"):
        pick_attempt(taken, "demo", "p1", limit=50)


def test_the_first_attempt_name_carries_no_suffix():
    assert session_name_for("demo", "p1", 1) == "cao-demo-p1"
    assert session_name_for("demo", "p1") == "cao-demo-p1"
