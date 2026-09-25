"""l1_handoff: stale detection and rendering are pure over a snapshot."""
import pathlib
import sys

SKILL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

import l1_handoff as h  # noqa: E402

REPO = "/r/AutoOS"


def snap(**kw):
    base = {"when": "2026-09-25 20:00", "repo": REPO,
            "git": {"branch": "main", "head": "abc x", "main": "abc", "origin_main": "abc", "dirty": 0},
            "lanes": [], "sessions": [], "helpers": [], "done": []}
    base.update(kw)
    return base


def test_merged_lane_session_is_a_stop_candidate():
    s = snap(lanes=[{"path": REPO + "-lanes/R", "branch": "lane/R", "merged": True, "dirty": False}],
             sessions=[{"id": "a1", "name": "autoos-R", "cwd": REPO + "-lanes/R", "status": "idle"}])
    notes = h.stale(s)
    assert any("claude stop a1" in n and "merged" in n for n in notes)
    assert any("git worktree remove" in n for n in notes)


def test_session_whose_worktree_is_gone_is_a_stop_candidate():
    s = snap(sessions=[{"id": "b2", "name": "autoos-D", "cwd": REPO + "-lanes/D", "status": "blocked"}])
    assert any("worktree gone" in n and "claude stop b2" in n for n in h.stale(s))


def test_open_lane_and_the_main_session_are_left_alone():
    s = snap(lanes=[{"path": REPO + "-lanes/X", "branch": "lane/X", "merged": False, "dirty": True}],
             sessions=[{"id": "", "name": "AutoOS", "cwd": REPO, "status": "busy"},
                       {"id": "c3", "name": "autoos-X", "cwd": REPO + "-lanes/X", "status": "busy"}])
    assert h.stale(s) == []


def test_helper_in_a_lane_without_a_live_session_is_a_kill_candidate():
    s = snap(helpers=[{"pid": 42, "cwd": REPO + "-lanes/R", "args": "docker run graphify"},
                      {"pid": 43, "cwd": "", "args": "serena start-mcp-server"}])
    notes = h.stale(s)
    assert notes == ["helper pid 42 in %s-lanes/R has no live session: kill 42" % REPO]


def test_render_embeds_state_and_says_none_when_clean():
    text = h.render(snap(), "## Next\n- task one")
    assert "## Operator state (hand-written)" in text and "- task one" in text
    assert "## Cleanup candidates (check, then run)\n\n- none" in text


def test_a_stopped_session_is_not_suggested_again():
    s = snap(sessions=[{"id": "d4", "name": "autoos-R", "cwd": REPO + "-lanes/R", "live": False,
                        "status": "not running (last state: working)"}])
    assert h.stale(s) == []
