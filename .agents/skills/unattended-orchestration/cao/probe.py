"""Is a provider actually *usable*, not merely installed?

CAO's ``GET /health`` answers "is the binary there". Measured on 2026-09-10 that
returned ``"claude":"ok"`` for a Claude Code install that was not logged in —
every worker spawned against it would have died on its first turn with
``Not logged in · Please run /login``, and the orchestration would have quietly
become all-Gemini with no error anywhere.

So the lane probes: send the cheapest possible prompt and classify what comes
back. Four outcomes, each needing a different response from the operator:

    ok                 dispatch freely
    not_authenticated  a human must log in; no amount of retrying helps
    quota              cool the pool and descend the ladder (cao/budget.py)
    not_installed      run setup_cao.py
    failed             unrecognised — surfaced, never assumed benign
"""

from __future__ import annotations

import re
import shlex

PROBE_TOKEN = "PROBE_OK"

_SECRETS = "secrets/api_keys.conf"

# Checked BEFORE quota: an unauthenticated pool that gets recorded as "cooling"
# would silently reopen after the cooldown and fail again, forever. Auth is a
# human action, and saying so is more useful than a timer.
_AUTH_PATTERNS = (
    r"not logged in",
    r"/login\b",
    r"\bclaude login\b",
    r"invalid api key",
    r"\b401\b",
    r"unauthorized",
    r"authentication failed",
    r"not authenticated",
    r"not signed in",
)

_QUOTA_PATTERNS = (
    r"rate.?limit",
    r"quota",
    r"resource.?exhausted",
    r"\b429\b",
    r"usage limit",
)

_MISSING_PATTERNS = (r"command not found", r"not found in %PATH%", r"no such file")

#: pool -> the cheapest model that still proves the account works.
_PROBE_MODELS = {
    "anthropic": "haiku",
    # Measured 2026-09-11: `-low` returned nothing in 120s across three
    # runs; `-high` answered PROBE_OK in ~22s. The cheapest model is only
    # the right probe if it actually answers.
    "antigravity": "gemini-3.8-flash-high",
    "deepseek": "deepseek/deepseek-v4-flash",
}


def _matches(text: str, patterns) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


#: A probe verdict that PROVES the pool cannot serve work. Only these close a
#: pool for routing.
CONCLUSIVE = ("not_installed", "not_authenticated")

#: Measured 2026-09-11: `agy -p '...' --model gemini-3.8-flash-high` answers
#: PROBE_OK with exit 0 inside tmux, and prints "error: interrupted" when its
#: stdout is a pipe. The probe captures output, so it never has a terminal and
#: agy always looked broken -- while the pool was healthy and answering.
_NO_TERMINAL_PATTERNS = (
    r"error: interrupted",
    r"not a (tty|terminal)",
    r"inappropriate ioctl for device",
    r"stdin is not a terminal",
)


def classify(stdout: str, stderr: str, returncode: int) -> str:
    blob = f"{stdout or ''}\n{stderr or ''}"
    if _matches(blob, _MISSING_PATTERNS):
        return "not_installed"
    if _matches(blob, _AUTH_PATTERNS):
        return "not_authenticated"
    if _matches(blob, _QUOTA_PATTERNS):
        return "quota"
    if PROBE_TOKEN in (stdout or ""):
        return "ok"
    if _matches(blob, _NO_TERMINAL_PATTERNS):
        # The HARNESS could not ask, which says nothing about the pool. Reporting
        # this as a failure once cost a working provider: probe printed
        # "antigravity failed", routing closed the pool, and the lane declined
        # capacity it had -- the opposite of the rule it states.
        return "inconclusive"
    # Exit 0 without the token means the CLI printed something else entirely —
    # a banner, a prompt, an update notice. Not an answer, so not ok.
    return "failed"


def closes_pool(verdict: str) -> bool:
    """Should this verdict stop the ladder dispatching to the pool?

    Only a verdict that PROVES unusability. A timeout, a harness limitation or
    an unrecognised banner is not proof, and closing on those declines capacity
    that may be there -- which this lane has a standing rule against.
    """
    return verdict in CONCLUSIVE


#: Printed by the probe shell immediately BEFORE the CLI runs. Everything after
#: the last line carrying it is the CLI's own output.
PANE_BEGIN = "CAO_PROBE_BEGIN"
PANE_DONE = "CAO_PROBE_DONE"


def in_tmux(command: str, session: str = "cao-probe", seconds: int = 90) -> str:
    """Run ``command`` in a throwaway tmux session; echo only what IT printed.

    Two measured problems, both solved here.

    *No terminal.* `agy -p` answers with exit 0 inside tmux and prints
    "error: interrupted" when its stdout is a pipe -- which is what
    ``subprocess.run(capture_output=True)`` always gives it. tmux is not a
    workaround but the honest test: CAO runs every agent in a tmux pane.

    *The pane contains the question.* A capture holds the echoed command line
    as well as the answer, so matching the token anywhere in it made
    ``PROBE_TOKEN in stdout`` true however the model behaved -- a probe that
    could only ever say ok. The sentinel is printed by the shell AFTER the
    command line is echoed, so the slice after its LAST occurrence is the
    CLI's output and nothing else.
    """
    inner = f"echo {PANE_BEGIN}; {command}; echo {PANE_DONE}"
    slice_after = (
        "awk '/" + PANE_BEGIN + "/{b=NR} {a[NR]=$0} "
        "END{for(i=b+1;i<=NR;i++) print a[i]}'"
    )
    return (
        f"tmux kill-session -t {session} 2>/dev/null; "
        f"tmux new-session -d -s {session} -x 200 -y 50; "
        f"tmux send-keys -t {session} {shlex.quote(inner)} Enter; "
        f"for i in $(seq 1 {seconds}); do "
        f"tmux capture-pane -p -t {session} 2>/dev/null "
        f"| grep -q '^{PANE_DONE}' && break; sleep 1; done; "
        f"tmux capture-pane -p -t {session} 2>/dev/null | {slice_after}; "
        f"tmux kill-session -t {session} 2>/dev/null; true"
    )


def secrets_lookup(repo: str = ".") -> str:
    """Shell snippet that sets ``$s`` to the secrets file, in the shell that runs the probe.

    Same order as :func:`cao.config.find_secrets`, resolved where the key is read
    (the distro, on Windows): ``$AUTOOS_SECRETS``, ``~/.config/autoos/api_keys.conf``,
    then the legacy ``<repo>/secrets/api_keys.conf`` with a notice on stderr.
    """
    return (
        # An explicit AUTOOS_SECRETS that is not a file fails loudly, as the review scripts do.
        'if [ -n "${AUTOOS_SECRETS:-}" ] && [ ! -f "$AUTOOS_SECRETS" ]; then '
        'echo "secrets file not found: $AUTOOS_SECRETS (AUTOOS_SECRETS)" >&2; exit 2; fi; '
        f's="${{AUTOOS_SECRETS:-$HOME/.config/autoos/api_keys.conf}}"; '
        f'if [ ! -s "$s" ]; then s="{repo}/{_SECRETS}"; '
        f'echo "notice: using legacy secrets file $s; move it to '
        f'~/.config/autoos/api_keys.conf (AUTOOS_SECRETS overrides)" >&2; fi; '
    )


def probe_command(pool: str, repo: str = ".") -> str:
    """The shell command that probes ``pool``, as ONE string for ``bash -lc``.

    Deliberately not pre-wrapped in ``bash -lc``: :func:`probe_argv` passes this
    as a single argv element, so the outer layer never re-parses it. Building the
    wrapper here with quoting instead produced an "unexpected EOF" the first time
    a command mixed single and double quotes.
    """
    ask = f"Reply with exactly: {PROBE_TOKEN}"
    model = _PROBE_MODELS[pool]

    if pool == "anthropic":
        return f"claude -p '{ask}' --model {model} --dangerously-skip-permissions"
    if pool == "antigravity":
        # Through tmux: agy will not answer without a terminal, and a pipe is
        # what `subprocess.run(capture_output=True)` always gives it.
        return in_tmux(f"agy -p '{ask}' --model {model}", session="cao-probe-agy")
    if pool == "deepseek":
        # The key is read inside the shell from the untracked secrets file, so it
        # never appears in this command string, a process list, or a log.
        return (
            f"{secrets_lookup(repo)}"
            f'export DEEPSEEK_API_KEY=$(grep "^deepseek=" "$s" '
            f"| cut -d= -f2-); mkdir -p /tmp/cao-probe && cd /tmp/cao-probe && "
            f"opencode run --model {model} '{ask}'"
        )
    raise KeyError(f"no probe defined for pool {pool!r}")


def probe_argv(pool: str, repo: str = ".", wsl_distro: str | None = None) -> list:
    """Full argv for the probe. Always a LOGIN shell.

    ``bash -c`` cannot see ``~/.local/bin``, where every one of these binaries
    lives — that is the exact %PATH% failure this integration began with, and it
    is why this is ``-lc`` everywhere rather than only where it was noticed.

    ``wsl_distro`` is None on Linux, where CAO and its agents run natively.
    """
    inner = probe_command(pool, repo)
    if wsl_distro:
        return ["wsl", "-d", wsl_distro, "-e", "bash", "-lc", inner]
    return ["bash", "-lc", inner]
