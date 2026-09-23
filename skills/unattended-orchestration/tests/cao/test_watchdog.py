"""A waiting agent must always be answered.

Measured 2026-09-10: a DeepSeek worker sat on OpenCode's "Permission required —
Access external directory /tmp" for seven minutes while its supervisor waited
for a callback that could never arrive.
"""

from cao.watchdog import (
    DEFAULT_GRACE_SECONDS,
    auto_answerable,
    blocked_terminals,
    classify_prompt,
    is_blocked,
    needs_human,
    summarise,
)

PERMISSION_PROMPT = (
    "△ Permission required\n  ← Access external directory /tmp\n"
    "  Allow once   Allow always   Reject"
)


def T(tid, status="waiting_user_answer", profile="cao_L3_worker"):
    return {"id": tid, "status": status, "agent_profile": profile}


def test_waiting_status_is_blocked():
    assert is_blocked(T("a")) is True


def test_working_and_idle_are_not_blocked():
    assert is_blocked(T("a", status="processing")) is False
    assert is_blocked(T("a", status="idle")) is False


def test_the_measured_permission_prompt_is_auto_answered():
    answer, reason = classify_prompt(PERMISSION_PROMPT)
    assert answer == "Allow always"
    assert "worktree" in reason




def test_tool_permission_prompt_is_auto_answered():
    answer, _ = classify_prompt("Allow this edit to src/main.py?")
    assert answer == "Allow always"


# ---- the things an orchestrator must NEVER click through --------------------


def test_payment_prompts_are_escalated():
    answer, reason = classify_prompt("Confirm purchase of 100 credits?")
    assert answer is None
    assert "human decides" in reason


def test_credential_prompts_are_escalated():
    for prompt in ("Enter your password:", "Paste your API key", "Sign in to continue"):
        assert classify_prompt(prompt)[0] is None, prompt


def test_destructive_prompts_are_escalated():
    for prompt in ("Delete this branch permanently?", "Force-push to main?", "run rm -rf /data?"):
        assert classify_prompt(prompt)[0] is None, prompt


def test_publication_prompts_are_escalated():
    assert classify_prompt("Deploy to production?")[0] is None


def test_a_dangerous_prompt_wins_over_a_permissive_pattern():
    """"Allow always" must not leak a credential dialog through."""
    answer, _ = classify_prompt("Allow this edit to write your API key to disk?")
    assert answer is None


def test_an_unrecognised_prompt_is_escalated_not_guessed():
    answer, reason = classify_prompt("Choose a deployment region [1-9]:")
    assert answer is None
    assert "not recognised" in reason


# ---- sweep ------------------------------------------------------------------


def test_blocked_terminals_pairs_prompts_with_answers():
    found = blocked_terminals([T("w1")], prompts={"w1": PERMISSION_PROMPT})
    assert len(found) == 1
    assert found[0].answer == "Allow always"
    assert found[0].agent_profile == "cao_L3_worker"


def test_only_waiting_terminals_are_returned():
    terms = [T("w1"), T("w2", status="processing"), T("w3", status="idle")]
    assert [b.terminal_id for b in blocked_terminals(terms, {"w1": PERMISSION_PROMPT})] == ["w1"]


def test_an_unreadable_prompt_is_escalated_not_skipped():
    """Unknown is not the same as fine — a silent skip re-creates the stall."""
    found = blocked_terminals([T("w1")], prompts={})
    assert len(found) == 1
    assert found[0].answer is None
    assert "unavailable" in found[0].reason


def test_auto_and_human_partitions_are_complementary():
    terms = [T("w1"), T("w2")]
    prompts = {"w1": PERMISSION_PROMPT, "w2": "Enter your password:"}
    found = blocked_terminals(terms, prompts)
    assert [b.terminal_id for b in auto_answerable(found)] == ["w1"]
    assert [b.terminal_id for b in needs_human(found)] == ["w2"]


def test_summary_says_what_to_do_for_each():
    found = blocked_terminals([T("w1"), T("w2")],
                              {"w1": PERMISSION_PROMPT, "w2": "Enter your password:"})
    text = summarise(found)
    assert "answer 'Allow always'" in text
    assert "ESCALATE TO HUMAN" in text


def test_summary_of_nothing_blocked_is_explicit():
    assert summarise([]) == "no terminal is waiting for input"


def test_grace_is_short_because_a_blocked_agent_does_nothing():
    assert 15 <= DEFAULT_GRACE_SECONDS <= 120


# --------------------------------------------------------------------------
# Trust prompts differ per provider, and the pattern must match the MEASURED
# wording. agy 1.2.1 asks "Do you trust the contents of this project?"; the
# earlier pattern only matched Claude Code's "files in this folder", so a
# Gemini worker would have sat on its prompt unanswered.
# --------------------------------------------------------------------------

AGY_TRUST = (
    "Accessing workspace:\n/home/u/cao-worktrees/repo-p1\n"
    "Do you trust the contents of this project?\n"
    "Antigravity CLI requires permission to read, edit, and execute files here.\n"
    "> Yes, I trust this folder\n  No, exit"
)

CLAUDE_TRUST = "Do you trust the files in this folder?"


def test_the_measured_agy_trust_prompt_is_auto_answered():
    answer, _ = classify_prompt(AGY_TRUST)
    assert answer == "Yes, I trust this folder"


def test_the_claude_trust_prompt_is_still_auto_answered():
    assert classify_prompt(CLAUDE_TRUST)[0] == "Yes, I trust this folder"


def test_both_providers_get_the_same_answer_text():
    """The answer must be the option text the TUI actually offers."""
    assert classify_prompt(AGY_TRUST)[0] == classify_prompt(CLAUDE_TRUST)[0]


def test_the_reason_explains_why_trusting_is_safe_here():
    _, reason = classify_prompt(AGY_TRUST)
    assert "disposable" in reason


def test_a_trust_prompt_about_credentials_is_still_escalated():
    """"Trust" wording must not smuggle a credential request through."""
    prompt = "Do you trust this project to read your API key?"
    assert classify_prompt(prompt)[0] is None
