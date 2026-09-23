"""The three-hop terminal fetch.

Regression: an earlier version read ``session["terminals"]``, a key CAO's
/sessions response does not contain. It found zero terminals forever and printed
"0 violation(s)" — a check that looked at nothing and reported success.
"""

import urllib.error

import pytest

from cao.caoapi import CaoClient

# Verbatim shapes from CAO 2.5.0 on 2026-09-10.
SESSIONS = [
    {
        "id": "cao-e2e9",
        "name": "cao-e2e9",
        "status": "detached",
        "agent_profile": "cao_L2_supervisor",
    }
]
SESSION_TERMINALS = [
    {
        "id": "d2ef95ee",
        "tmux_window": "cao_L2_supervisor-122b",
        "provider": "claude_code",
        "agent_profile": "cao_L2_supervisor",
    }
]
TERMINAL_DETAIL = {
    "id": "d2ef95ee",
    "caller_id": None,
    "status": "idle",
    "metadata": {"task_id": "p1-impl"},
}


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_get(self, path):
        calls.append(path)
        if path == "/sessions":
            return SESSIONS
        if path.startswith("/sessions/") and path.endswith("/terminals"):
            return SESSION_TERMINALS
        if path.startswith("/terminals/"):
            return TERMINAL_DETAIL
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(CaoClient, "_get", fake_get)
    c = CaoClient()
    c._calls = calls
    return c


def test_sessions_response_has_no_terminals_key():
    """Pins the shape that caused the bug."""
    assert "terminals" not in SESSIONS[0]


def test_all_terminals_walks_all_three_hops(client):
    terms = client.all_terminals()
    assert len(terms) == 1
    assert client._calls == [
        "/sessions",
        "/sessions/cao-e2e9/terminals",
        "/terminals/d2ef95ee",
    ]


def test_detail_supplies_caller_id_and_status(client):
    """Without the third hop the monitor has no ancestry and the watchdog no status."""
    term = client.all_terminals()[0]
    assert "caller_id" in term
    assert term["status"] == "idle"


def test_summary_fields_survive_the_merge(client):
    term = client.all_terminals()[0]
    assert term["agent_profile"] == "cao_L2_supervisor"
    assert term["provider"] == "claude_code"


def test_detail_false_skips_the_third_hop(client):
    client.all_terminals(detail=False)
    assert "/terminals/d2ef95ee" not in client._calls


def test_a_terminal_that_vanishes_mid_sweep_is_skipped(monkeypatch):
    """Sessions are torn down constantly; a sweep must not crash on the race."""

    def fake_get(self, path):
        if path == "/sessions":
            return SESSIONS
        if path.endswith("/terminals"):
            return SESSION_TERMINALS
        raise urllib.error.HTTPError(path, 404, "gone", {}, None)

    monkeypatch.setattr(CaoClient, "_get", fake_get)
    assert CaoClient().all_terminals() == []


def test_session_names_are_url_quoted(monkeypatch):
    seen = []

    def fake_get(self, path):
        seen.append(path)
        if path == "/sessions":
            return [{"name": "a/b session"}]
        return []

    monkeypatch.setattr(CaoClient, "_get", fake_get)
    CaoClient().all_terminals()
    assert "/sessions/a%2Fb%20session/terminals" in seen


def test_sessions_without_a_name_are_skipped(monkeypatch):
    monkeypatch.setattr(CaoClient, "_get", lambda self, p: [{}] if p == "/sessions" else [])
    assert CaoClient().all_terminals() == []


def test_live_task_ids_reads_terminal_metadata(client):
    assert client.live_task_ids() == {"p1-impl"}


def test_live_task_ids_propagates_errors_rather_than_returning_empty(monkeypatch):
    """Empty reads as 'nobody is working', which would license restarting live work."""

    def boom(self, path):
        raise OSError("connection refused")

    monkeypatch.setattr(CaoClient, "_get", boom)
    with pytest.raises(OSError):
        CaoClient().live_task_ids()


def test_available_providers_maps_health_components(monkeypatch):
    monkeypatch.setattr(
        CaoClient,
        "_get",
        lambda self, p: {"components": {"cao": "ok", "herdr": "unavailable"}},
    )
    assert CaoClient().available_providers() == {"cao": True, "herdr": False}
