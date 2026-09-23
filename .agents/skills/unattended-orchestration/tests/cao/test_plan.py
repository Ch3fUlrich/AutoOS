import json

import pytest

from cao.plan import PlanError, load, validate


def good():
    return {
        "schema": "cao-plan/1",
        "goal": "g",
        "createdBy": "claude-code",
        "approvedAt": "2026-09-10T14:22:03Z",
        "depth": 3,
        "openQuestions": [
            {"q": "which graph?", "answer": "agent-skills", "answeredBy": "user"}
        ],
        "assumedDefaults": [],
        "phases": [
            {
                "id": "p1",
                "goal": "x",
                "taskClass": "implement",
                "acceptance": {"guard": "pytest -q", "expect": "exit0"},
                "dependsOn": [],
            }
        ],
    }


def test_valid_plan_passes():
    validate(good(), max_depth=3)


def test_wrong_schema_rejected():
    d = good()
    d["schema"] = "cao-plan/99"
    with pytest.raises(PlanError, match="schema"):
        validate(d, max_depth=3)


def test_missing_approval_rejected():
    d = good()
    del d["approvedAt"]
    with pytest.raises(PlanError, match="approvedAt"):
        validate(d, max_depth=3)


def test_missing_goal_rejected():
    d = good()
    d["goal"] = ""
    with pytest.raises(PlanError, match="goal"):
        validate(d, max_depth=3)


def test_no_phases_rejected():
    d = good()
    d["phases"] = []
    with pytest.raises(PlanError, match="no phases"):
        validate(d, max_depth=3)


def test_phase_without_guard_rejected():
    d = good()
    d["phases"][0]["acceptance"] = {"guard": "", "expect": "exit0"}
    with pytest.raises(PlanError, match="acceptance.guard"):
        validate(d, max_depth=3)


def test_depth_over_max_rejected():
    d = good()
    d["depth"] = 9
    with pytest.raises(PlanError, match="depth 9 exceeds"):
        validate(d, max_depth=3)


def test_dependson_unknown_phase_rejected():
    d = good()
    d["phases"][0]["dependsOn"] = ["ghost"]
    with pytest.raises(PlanError, match="ghost"):
        validate(d, max_depth=3)


def test_dependency_cycle_rejected():
    d = good()
    d["phases"] = [
        {
            "id": "a",
            "goal": "",
            "taskClass": "implement",
            "acceptance": {"guard": "x", "expect": "exit0"},
            "dependsOn": ["b"],
        },
        {
            "id": "b",
            "goal": "",
            "taskClass": "implement",
            "acceptance": {"guard": "x", "expect": "exit0"},
            "dependsOn": ["a"],
        },
    ]
    with pytest.raises(PlanError, match="cycle"):
        validate(d, max_depth=3)


def test_diamond_dependency_is_not_a_cycle():
    d = good()
    base = {"goal": "", "taskClass": "implement", "acceptance": {"guard": "x", "expect": "exit0"}}
    d["phases"] = [
        {"id": "a", **base, "dependsOn": []},
        {"id": "b", **base, "dependsOn": ["a"]},
        {"id": "c", **base, "dependsOn": ["a"]},
        {"id": "d", **base, "dependsOn": ["b", "c"]},
    ]
    validate(d, max_depth=3)


# ---------------------------------------------------------------- the gate ---
# These four are the reason this module exists: they make "I did not ask the
# user" impossible to express in a valid plan (spec 3).


def test_no_user_answer_and_no_assumed_defaults_is_rejected():
    d = good()
    d["openQuestions"] = []
    d["assumedDefaults"] = []
    with pytest.raises(PlanError, match="ask the user"):
        validate(d, max_depth=3)


def test_question_answered_by_someone_other_than_the_user_does_not_satisfy_the_gate():
    d = good()
    d["openQuestions"] = [{"q": "?", "answer": "a", "answeredBy": "agent"}]
    d["assumedDefaults"] = []
    with pytest.raises(PlanError, match="ask the user"):
        validate(d, max_depth=3)


def test_assumed_defaults_satisfy_the_gate_when_documented():
    d = good()
    d["openQuestions"] = []
    d["assumedDefaults"] = [
        {"assumption": "graph = repo folder", "why": "matches trust_worktree"}
    ]
    validate(d, max_depth=3)


def test_assumed_default_without_why_is_rejected():
    d = good()
    d["openQuestions"] = []
    d["assumedDefaults"] = [{"assumption": "graph = repo folder"}]
    with pytest.raises(PlanError, match="why"):
        validate(d, max_depth=3)


def test_load_reads_and_validates(tmp_path):
    p = tmp_path / "PLAN.lock.json"
    p.write_text(json.dumps(good()), encoding="utf-8")
    assert load(p, max_depth=3)["goal"] == "g"


def test_load_propagates_validation_failure(tmp_path):
    d = good()
    d["openQuestions"] = []
    p = tmp_path / "PLAN.lock.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(PlanError):
        load(p, max_depth=3)


# --------------------------------------------------------------------------
# The scaffold must NOT validate. Measured 2026-09-10: `cao plan` wrote a
# template that passed the gate unedited, because its placeholder approvedAt
# was a non-empty string and its template question said answeredBy "user".
# An agent could scaffold and launch having asked nothing.
# --------------------------------------------------------------------------


def test_an_unfilled_placeholder_is_rejected():
    d = good()
    d["goal"] = "<one sentence: what this run is for>"
    with pytest.raises(PlanError, match="unfilled placeholders"):
        validate(d, max_depth=3)


def test_a_placeholder_anywhere_in_a_phase_is_rejected():
    d = good()
    d["phases"][0]["acceptance"]["guard"] = "<the command that proves it done>"
    with pytest.raises(PlanError, match="unfilled placeholders"):
        validate(d, max_depth=3)


def test_a_placeholder_inside_an_answered_question_is_rejected():
    """The subtlest bypass: answeredBy says 'user', the answer is a template."""
    d = good()
    d["openQuestions"] = [{"q": "<a question>", "answer": "<their answer>", "answeredBy": "user"}]
    with pytest.raises(PlanError, match="unfilled placeholders"):
        validate(d, max_depth=3)


def test_the_error_names_where_the_placeholders_are():
    d = good()
    d["goal"] = "<fill me>"
    with pytest.raises(PlanError, match="goal"):
        validate(d, max_depth=3)


def test_ordinary_angle_brackets_are_not_placeholders():
    """Real prose uses < and >; only <unfilled tokens> are rejected."""
    d = good()
    d["goal"] = "keep latency < 20ms and memory > 1GB"
    validate(d, max_depth=3)


def test_a_shell_redirect_in_a_guard_is_not_a_placeholder():
    d = good()
    d["phases"][0]["acceptance"]["guard"] = "pytest -q > out.txt 2>&1"
    validate(d, max_depth=3)


def test_the_shipped_scaffold_does_not_validate(tmp_path):
    """The template ships INVALID on purpose; this pins that."""
    from cao.cli import PLAN_TEMPLATE

    with pytest.raises(PlanError):
        validate(PLAN_TEMPLATE, max_depth=3)
