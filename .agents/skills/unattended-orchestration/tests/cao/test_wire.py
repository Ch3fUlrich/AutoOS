import inspect

import pytest

from cao import wire
from cao.wire import (
    find_result,
    strip_ansi,
    NOTES_CAP,
    TASK_CAP,
    parse_result,
    render_result,
    render_task,
    truncate,
)


def test_truncate_marks_visibly():
    out = truncate("x" * 1000, 100)
    assert "truncated" in out
    assert len(out) <= 100 + 40


def test_truncate_leaves_short_text_untouched():
    assert truncate("hello", 100) == "hello"


def test_truncate_handles_none():
    assert truncate(None, 10) == ""


def _task(**over):
    kw = dict(
        task_id="p2-impl-03",
        goal="add X",
        plan_ref="PLAN.lock.json#/phases/p2",
        files=["src/a.py"],
        guard="pytest -q",
        expect="exit0",
        depth=3,
        max_depth=3,
        budget_tokens=40000,
        reply_to="term-a91f",
    )
    kw.update(over)
    return render_task(**kw)


def test_render_task_has_all_fields_and_stays_under_cap():
    out = _task()
    for token in ("TASK p2-impl-03", "plan_ref:", "acceptance:", "depth:", "reply:"):
        assert token in out
    assert len(out) <= TASK_CAP


def test_render_task_shows_depth_against_the_max():
    assert "3/3" in _task()


def test_render_task_caps_a_huge_goal():
    out = _task(goal="g" * 50000)
    assert len(out) <= TASK_CAP
    assert "truncated" in out


def test_render_task_renders_empty_file_list_explicitly():
    assert "(none)" in _task(files=[])


def test_render_task_has_no_parameter_that_can_carry_plan_prose():
    """The rule is structural, not advisory (spec 7).

    A worker references plan_ref; there is no field in which a supervisor could
    re-quote the plan even if its prompt drifted.
    """
    params = set(inspect.signature(render_task).parameters)
    assert not (params & {"plan", "plan_text", "context", "background", "notes"})


def test_render_task_is_keyword_only_so_fields_cannot_be_transposed():
    sig = inspect.signature(render_task)
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())


def test_render_result_roundtrips():
    out = render_result(
        task_id="p2-impl-03",
        status="ok",
        evidence="pytest -q -> 14 passed",
        files_changed=["src/b.py"],
        notes="fine",
        cost={"pool": "antigravity", "model": "gemini-3.8-flash-high", "in": 18211, "out": 2044},
    )
    got = parse_result(out)
    assert got["task_id"] == "p2-impl-03"
    assert got["status"] == "ok"
    assert got["files_changed"] == ["src/b.py"]
    assert "gemini-3.8-flash-high" in got["cost"]


def test_render_result_empty_files_roundtrips_to_empty_list():
    out = render_result(
        task_id="t", status="ok", evidence="e", files_changed=[], notes="", cost={}
    )
    assert parse_result(out)["files_changed"] == []


def test_render_result_caps_notes():
    out = render_result(
        task_id="t", status="ok", evidence="e", files_changed=[], notes="n" * 5000, cost={}
    )
    line = [line for line in out.splitlines() if line.startswith("notes:")][0]
    assert len(line) <= NOTES_CAP + 60


def test_render_result_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        render_result(
            task_id="t", status="fine", evidence="", files_changed=[], notes="", cost={}
        )


def test_parse_result_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        parse_result("RESULT t status=whatever\n")


def test_parse_result_rejects_a_non_envelope():
    with pytest.raises(ValueError, match="not a RESULT envelope"):
        parse_result("hello, I finished the task!")


def test_status_values_are_exactly_the_three_documented():
    assert wire.STATUSES == ("ok", "blocked", "needs_revision")


def test_needs_revision_is_representable_so_workers_can_push_back():
    out = render_result(
        task_id="t",
        status="needs_revision",
        evidence="guard fails: import cycle",
        files_changed=[],
        notes="plan assumes module split that does not exist",
        cost={},
    )
    assert parse_result(out)["status"] == "needs_revision"


# ---------------------------------------------------------------------------
# The routing ladder decides a model AND an effort. The agent that acts on that
# decision is the supervisor, which only ever sees the envelope -- so a ladder
# whose result is not in the envelope is a ladder that was computed and thrown
# away. Measured in a live dry run: it printed a Gemini model while launching
# an Opus supervisor that had no idea either.
# ---------------------------------------------------------------------------


def test_the_envelope_carries_the_chosen_model():
    body = render_task(
        task_id="p1", goal="g", plan_ref="r", files=[], guard="true",
        expect="exit0", depth=2, max_depth=3, budget_tokens=100,
        reply_to="layer1", leaf_pool="antigravity",
        leaf_model="gemini-3.8-flash-high", leaf_effort="high",
    )
    assert "use_pool:   antigravity" in body
    assert "use_model:  gemini-3.8-flash-high effort=high" in body


def test_the_pool_never_prefixes_the_model_id():
    """Measured live: "antigravity/gemini-3.8-flash-high" was passed verbatim
    as a model id and the worker died on a model that does not exist. The id
    itself was valid; the slash was ours."""
    body = render_task(
        task_id="p1", goal="g", plan_ref="r", files=[], guard="true",
        expect="exit0", depth=2, max_depth=3, budget_tokens=100,
        reply_to="layer1", leaf_pool="antigravity",
        leaf_model="gemini-3.8-flash-high",
    )
    assert "antigravity/gemini" not in body


def test_effort_is_omitted_when_the_pool_has_none():
    body = render_task(
        task_id="p1", goal="g", plan_ref="r", files=[], guard="true",
        expect="exit0", depth=2, max_depth=3, budget_tokens=100,
        reply_to="layer1", leaf_pool="deepseek", leaf_model="deepseek-flash",
    )
    assert "use_model:  deepseek-flash" in body
    assert "effort=" not in body


def test_an_envelope_without_a_model_is_unchanged():
    """Reviews and other callers may not route; they must not gain a blank line."""
    body = render_task(
        task_id="p1", goal="g", plan_ref="r", files=[], guard="true",
        expect="exit0", depth=2, max_depth=3, budget_tokens=100,
        reply_to="layer1",
    )
    assert "use_model" not in body
    assert body.endswith("reply:      RESULT -> layer1\n")


# ---------------------------------------------------------------------------
# A terminal capture is a RENDERED tmux pane, not a message. Measured against a
# live session on 2026-09-11: 19930 raw chars of ANSI, and `parse_result` --
# which matches line 0 -- returned "no claim" for it. Every terminal would have
# behaved the same way, so the contradiction check was silently inert.
# ---------------------------------------------------------------------------

ESC = chr(27)


def _pane(*lines):
    """Approximately what tmux hands back: colour plus cursor positioning."""
    return "".join(f"{ESC}[38;5;246m{ln}{ESC}[39m\r\n" for ln in lines)


def test_an_envelope_buried_in_a_pane_is_found():
    text = _pane("some prose", "RESULT p1 status=ok", "evidence: did it")
    assert find_result(text)["status"] == "ok"


def test_positioning_escapes_do_not_weld_the_header():
    """Runs of spaces arrive as ESC[nG. Deleting them made "RESULTp1status=ok"."""
    text = f"{ESC}[1mRESULT{ESC}[8Gp1{ESC}[12Gstatus=ok{ESC}[0m\nevidence: x\n"
    assert find_result(text)["task_id"] == "p1"


def test_words_survive_the_strip():
    welded = f"I{ESC}[42Gdelegated{ESC}[3Cto worker"
    assert strip_ansi(welded) == "I delegated to worker"


def test_our_own_task_banner_is_not_a_claim():
    """The TASK envelope contains "RESULT -> layer1". It is an instruction."""
    with pytest.raises(ValueError):
        find_result("TASK p1\nreply:      RESULT -> layer1\n")


def test_the_last_envelope_wins():
    """An agent that reports twice has concluded twice; the later one holds."""
    text = _pane("RESULT p1 status=ok", "evidence: a") + _pane(
        "RESULT p1 status=blocked", "evidence: actually not")
    assert find_result(text)["status"] == "blocked"


def test_silence_is_not_a_claim():
    with pytest.raises(ValueError):
        find_result(_pane("working...", "still working"))


def test_hyperlinks_and_titles_are_stripped():
    osc = f"{ESC}]8;id=x;https://example.test{ESC}" + chr(92) + "RESULT p1 status=ok\nnotes: -\n"
    assert find_result(osc)["status"] == "ok"
