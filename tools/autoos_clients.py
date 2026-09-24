"""Client adapters for tools/autoos-agent.py: one headless command per agent CLI.

Each adapter declares what the `list` matrix prints - headless, routed through
OmniRoute, native sub-agents, auth source (an env var NAME or a login step,
never a value) - and builds its command for one task. Gateway clients other
than opencode go through `omniroute run <target>`, which writes a temporary
config overlay for the child and passes the client key by env var name.
agy and qoder cannot use the gateway: they run on their own account login.

Permission levels map onto each CLI's own modes:
    read  - role=review: plan / read-only, no edits
    edit  - the default (--auto): file edits approved, shell asks (and so
            fails headless) unless the CLI's sandbox allows it
    ask   - --no-auto: the CLI's default prompting

Depth budget: every child gets AUTOOS_AGENT_DEPTH (its own depth, 1 = spawned
by a human or a top-level session) and AUTOOS_AGENT_MAX_DEPTH. A spawn past the
max is refused - the only depth control qwen, gemini, codex, agy and qoder
have. opencode's in-process nesting is still experimental.subagent_depth.
"""
from __future__ import annotations

import io
import json
import os
import time
from dataclasses import dataclass, field

DEFAULT_MAX_DEPTH = 2  # = opencode.jsonc experimental.subagent_depth
PROBE_STALE_DAYS = 7   # ADR 0006 Q1


@dataclass(frozen=True)
class Client:
    name: str
    binary: str
    headless: bool
    gateway: bool
    subagents: bool
    auth: str
    notes: str = ""
    promo: bool = False
    modes: dict = field(default_factory=dict)


CLIENTS = {c.name: c for c in (
    Client("opencode", "opencode", True, True, True, "AUTOOS_OMNIROUTE_KEY",
           "tier agents from opencode.jsonc; --isolate adds the outside-path fence"),
    Client("claude", "claude", True, False, True, "claude login (subscription)",
           "-p headless; --joinable = --bg --remote-control session",
           modes={"read": ["--permission-mode", "plan"], "edit": ["--permission-mode", "acceptEdits"]}),
    Client("qwen", "qwen", True, True, True, "AUTOOS_OMNIROUTE_KEY (omniroute run)",
           modes={"read": ["--approval-mode", "plan"], "edit": ["--approval-mode", "auto-edit"]}),
    Client("gemini", "gemini", True, True, False, "AUTOOS_OMNIROUTE_KEY (omniroute run)",
           modes={"read": ["--approval-mode", "plan"], "edit": ["--approval-mode", "auto_edit"]}),
    Client("codex", "codex", True, True, False, "AUTOOS_OMNIROUTE_KEY (omniroute run)",
           "codex exec",
           modes={"read": ["--sandbox", "read-only"], "edit": ["--sandbox", "workspace-write"]}),
    Client("agy", "agy", True, False, False, "agy login (own Google account)",
           "own auth, not the gateway; headless only after one interactive login"),
    Client("qoder", "qodercli", True, False, True, "qodercli login (own account)",
           "own auth, not the gateway; promo - privacy=public only", promo=True,
           modes={"read": ["--permission-mode", "dont_ask"], "edit": ["--permission-mode", "accept_edits"]}),
)}


def build_command(client: Client, task: str, combo: str | None, level: str,
                  model: str | None = None, joinable: str | None = None) -> list:
    """The argv for one headless task. opencode is built by autoos-agent.py itself."""
    mode = client.modes.get(level, [])
    if client.name == "claude":
        if joinable:
            return ["claude", "--bg", "--remote-control", joinable, "--name", joinable] + mode + [task]
        return ["claude", "-p"] + mode + ([] if not model else ["--model", model]) + [task]
    if client.name == "codex":
        inner = ["exec"] + mode + ["--skip-git-repo-check", task]
    elif client.name == "qoder":
        return ["qodercli", "-p"] + mode + ([] if not model else ["--model", model]) + [task]
    elif client.name == "agy":
        return ["agy", "-p"] + ([] if not model else ["--model", model]) + [task]
    else:  # qwen, gemini
        inner = mode + ["-p", task]
    return ["omniroute", "run", client.name, "--model", model or combo,
            "--api-key-env", "AUTOOS_OMNIROUTE_KEY", "--"] + inner


class DepthError(RuntimeError):
    pass


def child_depth(env: dict, max_flag: int | None = None) -> tuple:
    """(child depth, max) for a spawn from a process with `env`; DepthError past the max.

    The inherited max can only be lowered - a child cannot grant itself depth."""
    try:
        cur = int(env.get("AUTOOS_AGENT_DEPTH", "0"))
        cap = int(env.get("AUTOOS_AGENT_MAX_DEPTH", str(DEFAULT_MAX_DEPTH)))
    except ValueError:
        raise DepthError("AUTOOS_AGENT_DEPTH/AUTOOS_AGENT_MAX_DEPTH are not integers; refusing to spawn")
    if max_flag is not None:
        cap = min(cap, max_flag)
    if cur + 1 > cap:
        raise DepthError("depth budget exhausted: this agent is at depth %d of %d; do the work "
                         "yourself or hand it back to your caller" % (cur, cap))
    return cur + 1, cap


def _probe_file(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    state = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "autoos", "agents", "probes.json")


def _probes(env=None) -> dict:
    try:
        with io.open(_probe_file(env), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def record_probe(client: str, now: float | None = None, env=None) -> None:
    """Remember that `client` worked (a promo's last successful run)."""
    path = _probe_file(env)
    data = _probes(env)
    data[client] = time.time() if now is None else now
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def probe_age_days(client: str, now: float | None = None, env=None):
    at = _probes(env).get(client)
    if at is None:
        return None
    return ((time.time() if now is None else now) - at) / 86400.0


def probe_stale(client: str, now: float | None = None, env=None) -> bool:
    age = probe_age_days(client, now, env)
    return age is None or age > PROBE_STALE_DAYS
