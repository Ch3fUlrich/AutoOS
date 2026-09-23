"""Never leave a waiting agent unanswered.

An agent that stops to ask a question is not idle and not working — it is a held
slot doing nothing, and it stalls everything above it. Measured on 2026-09-10: a
DeepSeek worker sat on OpenCode's ``Permission required — Access external
directory /tmp`` dialog for seven minutes while its Claude supervisor waited for
a callback that could never arrive. Nothing was broken; nothing was progressing.

So answering is an obligation of the orchestrating layer, not a courtesy. The
rule this module enforces:

    A terminal in a waiting state MUST be answered — automatically when the
    prompt is recognised, by the human when it is not. It is never left.

Auto-answering is deliberately narrow. It covers prompts whose correct answer is
already implied by the run's configuration — a sandboxed agent being asked
whether it may touch its own worktree. Anything that is a real decision
(spending money, deleting data, an unrecognised dialog) is escalated with its
text, because an orchestrator that reflexively clicks "yes" is a worse failure
than one that stalls visibly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: CAO statuses that mean "this terminal is blocked on a human".
BLOCKED_STATUSES = ("waiting_user_answer",)

#: How long a terminal may sit unanswered before the watchdog reports it.
#: Short, because the cost of a blocked agent is a completely idle slot.
DEFAULT_GRACE_SECONDS = 45


@dataclass(frozen=True)
class Blocked:
    terminal_id: str
    agent_profile: str
    prompt: str
    answer: str | None  # None => escalate to a human
    reason: str


#: Prompts whose answer is already implied by the run's configuration.
#: Each entry is (pattern, answer, why).
_AUTO_ANSWERS = (
    (
        r"permission required.*access external directory|access external directory",
        "Allow always",
        "the agent runs in a disposable worktree and the run already granted it "
        "filesystem scope; the dialog is about a path outside its cwd",
    ),
    (
        # Measured wordings, not guessed ones. agy 1.2.1 says "Do you trust the
        # contents of this project?" and offers "Yes, I trust this folder";
        # Claude Code says "Do you trust the files in this folder?". An earlier
        # version of this pattern matched only the Claude phrasing, so a Gemini
        # worker would have sat on its trust prompt unanswered -- exactly the
        # stall this module exists to prevent.
        r"do you trust the (files|contents) of this (project|folder|directory)"
        r"|do you trust the files in this (folder|directory)"
        r"|trust the authors|yes, i trust this folder",
        "Yes, I trust this folder",
        "the worktree is a disposable checkout this run created; trusting it "
        "grants nothing the run had not already decided to grant",
    ),
    (
        r"allow .*to (read|edit|write) .*\?|allow this (edit|command)",
        "Allow always",
        "tool access is what the profile's allowedTools already granted",
    ),
)

#: Prompts that must NEVER be auto-answered, checked first. These are real
#: decisions, and an orchestrator that clicks through them is worse than one
#: that stalls where a human can see it.
_NEVER_AUTO = (
    r"\bpurchase|\bpayment|\bcredit card|\bbilling",
    r"delete .*permanently|force[- ]push|rm -rf|drop (table|database)",
    r"\bpassword\b|\bapi key\b|\bcredential|\bsecret\b|\blogin\b|sign in",
    r"publish|deploy to production|make public",
)


def _matches(text: str, patterns) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def classify_prompt(prompt: str):
    """Return ``(answer, reason)``. ``answer is None`` means escalate."""
    text = prompt or ""
    if _matches(text, _NEVER_AUTO):
        return None, (
            "prompt involves credentials, money, publication or destructive "
            "action — a human decides, never the orchestrator"
        )
    for pattern, answer, why in _AUTO_ANSWERS:
        if re.search(pattern, text, re.I):
            return answer, why
    return None, "prompt not recognised — escalate with its text rather than guess"


def is_blocked(terminal) -> bool:
    return terminal.get("status") in BLOCKED_STATUSES


def blocked_terminals(terminals, prompts=None) -> list:
    """Every terminal waiting on an answer, with the answer to send if known.

    ``prompts`` maps terminal_id -> the visible prompt text (from
    ``read_session_output`` or a pane capture). A terminal whose prompt cannot be
    read is escalated rather than skipped: unknown is not the same as fine.
    """
    prompts = prompts or {}
    out = []
    for term in terminals:
        if not is_blocked(term):
            continue
        tid = term.get("id") or "(unknown)"
        prompt = prompts.get(tid, "")
        if not prompt:
            answer, reason = None, "prompt text unavailable — escalate, do not guess"
        else:
            answer, reason = classify_prompt(prompt)
        out.append(
            Blocked(
                terminal_id=tid,
                agent_profile=term.get("agent_profile") or "(unknown)",
                prompt=prompt,
                answer=answer,
                reason=reason,
            )
        )
    return out


def auto_answerable(blocked) -> list:
    return [b for b in blocked if b.answer is not None]


def needs_human(blocked) -> list:
    return [b for b in blocked if b.answer is None]


def summarise(blocked) -> str:
    """One line per blocked agent, for the orchestrator to act on or relay."""
    if not blocked:
        return "no terminal is waiting for input"
    lines = []
    for b in blocked:
        action = f"answer {b.answer!r}" if b.answer else "ESCALATE TO HUMAN"
        lines.append(f"{b.terminal_id} ({b.agent_profile}): {action} — {b.reason}")
    return "\n".join(lines)
