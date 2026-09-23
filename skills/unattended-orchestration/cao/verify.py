"""Run the phase's guard yourself. An agent's claim is not evidence.

Every phase in a plan contract declares ``acceptance.guard`` — the command that
proves it done. Until this module existed nothing ran it, so "done" meant
whatever the agent said, and the whole contract was decorative.

Measured on 2026-09-10, in one short run:

* a worker created a file correctly and reported an ``od -c`` dump that did not
  match it — confident, specific, and wrong;
* its supervisor relayed that verbatim while holding ``fs_read``.

Neither was malicious and neither was detectable from the report. The only thing
that separates a real result from a plausible sentence is an exit code obtained
by someone who was not asked to produce a good answer.

So Layer 1 runs the guard itself, in the worktree, after the agent says it is
done, and the verdict beats the report every time. A phase that fails goes back
out for rework with the guard's own output as the brief — the agent does not get
to argue with it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cao import wire

#: Guard output kept in the ledger. Enough to brief a rework agent, bounded so a
#: runaway test log cannot bloat the run's state.
MAX_OUTPUT = 4000

PASS, FAIL, ERROR = "pass", "fail", "error"

#: Exit code the wrapper uses when it cannot even reach the worktree. Chosen
#: outside the range test runners use, so a real guard cannot collide with it.
#:
#: The distinction matters: "the guard ran and the code failed" is a rework, and
#: "the guard could not run" is a broken environment. Measured 2026-09-10 —
#: a missing worktree made `cd` return 1, which read as a genuine test failure,
#: consumed three rework attempts, and escalated to a human with a message
#: blaming code that was never executed.
CANNOT_RUN = 97


@dataclass(frozen=True)
class Verdict:
    phase_id: str
    status: str  # pass | fail | error
    guard: str
    exit_code: object  # int, or None when the guard could not run
    output: str
    checked_at: str

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def as_record(self) -> dict:
        return {
            "ts": self.checked_at,
            "kind": "verify",
            "phase": self.phase_id,
            "status": self.status,
            "guard": self.guard,
            "exit_code": self.exit_code,
            "output": self.output,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_guard(
    guard: str,
    worktree,
    *,
    wsl_distro: str | None = None,
    timeout: int = 900,
    runner=None,
) -> Verdict:
    """Run ``guard`` in ``worktree``. A LOGIN shell, always.

    ``bash -c`` cannot see ``~/.local/bin``, so a guard invoking ``pytest`` or
    ``uv`` would fail for reasons that have nothing to do with the code — and a
    guard that fails spuriously trains everyone to ignore it.
    """
    if not guard or not guard.strip():
        return Verdict("", ERROR, guard, None, "no guard command", _now())

    inner = f"cd {worktree} 2>/dev/null || exit {CANNOT_RUN}; {guard}"
    argv = (
        ["wsl", "-d", wsl_distro, "-e", "bash", "-lc", inner]
        if wsl_distro
        else ["bash", "-lc", inner]
    )
    run = runner or (
        lambda a: subprocess.run(a, capture_output=True, text=True, timeout=timeout)
    )
    try:
        completed = run(argv)
    except subprocess.TimeoutExpired:
        return Verdict(
            "", ERROR, guard, None, f"guard exceeded {timeout}s", _now()
        )
    except OSError as exc:
        return Verdict("", ERROR, guard, None, f"guard could not run: {exc}", _now())

    blob = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if len(blob) > MAX_OUTPUT:
        # Keep the TAIL: assertion failures and tracebacks land at the end.
        blob = f"…[{len(blob) - MAX_OUTPUT} chars trimmed]\n" + blob[-MAX_OUTPUT:]

    if completed.returncode == CANNOT_RUN:
        return Verdict(
            "", ERROR, guard, completed.returncode,
            f"cannot enter worktree {worktree} — the guard never ran, so this "
            "says nothing about the code",
            _now(),
        )

    status = PASS if completed.returncode == 0 else FAIL
    return Verdict("", status, guard, completed.returncode, blob, _now())


def verify_phase(plan_doc, phase_id, worktree, **kwargs) -> Verdict:
    """Run the guard a phase declared, not one the agent chose."""
    phase = next(
        (p for p in plan_doc.get("phases", []) if p.get("id") == phase_id), None
    )
    if phase is None:
        raise KeyError(f"unknown phase {phase_id!r}")
    guard = (phase.get("acceptance") or {}).get("guard", "")
    verdict = run_guard(guard, worktree, **kwargs)
    return Verdict(
        phase_id, verdict.status, verdict.guard, verdict.exit_code,
        verdict.output, verdict.checked_at,
    )


def rework_brief(verdict: Verdict, attempt: int) -> str:
    """The brief for the agent sent to fix it.

    Deliberately quotes the guard's own output rather than a summary of it: a
    paraphrase of a failure is another chance to soften it, and the previous
    agent already believed it had succeeded.
    """
    return (
        f"REWORK {verdict.phase_id} (attempt {attempt})\n"
        f"A previous agent reported this phase complete. It is not: the "
        f"acceptance guard fails.\n\n"
        f"guard:     {verdict.guard}\n"
        f"exit_code: {verdict.exit_code}\n"
        f"output:\n{verdict.output}\n\n"
        "Fix the cause, not the guard. Do NOT edit or weaken the guard command, "
        "and do not report success until the guard passes when someone else runs "
        "it — it will be run again by Layer 1, and a claim that disagrees with an "
        "exit code loses.\n"
        "If the guard itself is wrong, emit RESULT status=needs_revision with the "
        "evidence and stop; Layer 1 decides, not you."
    )


#: What an agent claimed, set against what the guard measured.
AGREED, CONTRADICTED, NO_CLAIM = "agreed", "contradicted", "no-claim"


def compare_claim(verdict: Verdict, terminal_text: str) -> tuple:
    """Did the agent's own RESULT agree with the guard? Returns (finding, claim).

    Running the guard proves whether the work is done. It does NOT, by itself,
    reveal that an agent *said* otherwise — and that is the more useful signal.
    A phase that fails after an honest "status=blocked" is ordinary; a phase that
    fails after "status=ok" means the reporting cannot be trusted on any phase,
    including the ones whose guards happened to pass.

    Measured 2026-09-10: a worker reported a byte-level `od -c` dump that did not
    match the file it had just written, and its supervisor relayed that verbatim.
    """
    try:
        # A PANE, not a message: the envelope is buried in ANSI scrollback.
        # Using the strict parser here made this check silently inert.
        claim = wire.find_result(terminal_text or "")
    except ValueError:
        return NO_CLAIM, None
    claimed_ok = claim.get("status") == "ok"
    if verdict.status == PASS and claimed_ok:
        return AGREED, claim
    if verdict.status == FAIL and claimed_ok:
        return CONTRADICTED, claim
    return AGREED, claim


def record(ledger, verdict: Verdict) -> dict:
    rec = verdict.as_record()
    ledger.append(rec)
    return rec


def read_history(ledger, phase_id) -> list:
    return [
        r
        for r in ledger.read()
        if r.get("kind") == "verify" and r.get("phase") == phase_id
    ]


def record_rework(ledger, phase_id, model: str = "") -> dict:
    """Note that an agent was actually SPAWNED to fix this phase."""
    rec = {"ts": _now(), "kind": "rework", "phase": phase_id, "model": model}
    ledger.append(rec)
    return rec


def attempts_for(ledger, phase_id) -> int:
    """How many agents have been spawned to fix this phase.

    Counted from rework LAUNCHES, not from failed verdicts. Measured live on
    2026-09-11: three `cao verify` runs on one phase -- two of them pure
    inspection, nothing respawned -- reported "3 failed attempts (cap 3)" and
    escalated to a human. `verify` is the read-mostly operator surface; looking
    at a phase must not spend its budget for fixing it.
    """
    return len([
        r for r in ledger.read()
        if r.get("kind") == "rework" and r.get("phase") == phase_id
    ])


def failures_for(ledger, phase_id) -> int:
    """How many times the guard has been observed failing. Diagnostic only."""
    return len([r for r in read_history(ledger, phase_id) if r.get("status") == FAIL])
