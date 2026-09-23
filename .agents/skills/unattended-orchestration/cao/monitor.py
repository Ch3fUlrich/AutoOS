"""External depth policing, and an honest account of what it costs.

CAO has no spawn-depth limit, and depth cannot be enforced through a profile's
tool surface (see :mod:`cao.profiles` for the two measurements that closed that
door). So the limit is policed from outside: walk the terminal tree, find
anything deeper than ``maxDepth``, and terminate it.

Why ``caller_id`` and not ``group``
-----------------------------------
``metadata["group"]`` looks like an ancestry chain and is tempting, but it is
**written by the terminal's own agent** through ``update_group``
(``api/main.py:277``). Policing on a value the policed party controls is not
policing. ``caller_id`` is stamped server-side from the calling terminal's own
MCP identity (``mcp_server/server.py:611``, ``caller_id=current_terminal_id``),
which an agent cannot forge without forging the environment CAO gave it. Depth
is therefore the length of the ``caller_id`` chain.

What this control is, and is not
--------------------------------
It is a **monitoring** control, not a preventive one. Four consequences, none of
which go away by wording the prompt more firmly:

1. **Reactive.** The child is already running before the sweep sees it. It has
   burned tokens and may have written files. You are cleaning up, not preventing.
2. **The poll interval is the blast radius.** A 30 s sweep permits up to 30 s of
   unauthorised work. Shortening it costs API calls; lengthening it costs damage.
3. **Termination can interrupt a write.** A worker killed mid-edit leaves a
   partial file. This is survivable only because workers run in disposable
   worktrees — which is a reason to keep them there, not an argument that the
   risk is zero.
4. **An orphan reads as depth 1.** If a parent dies before the sweep runs, its
   child's ``caller_id`` points at a terminal that is gone. :func:`depth_of`
   reports ``None`` for an unresolvable chain, and :func:`violations` treats
   ``None`` as *unknown, not innocent* — it reports it for a human rather than
   silently allowing or silently killing it.

Used well, this turns a hard constraint into a budget: some over-spawn happens,
and is cleaned up promptly and visibly. That is the honest trade, and it is worth
taking only because the alternative — believing a prompt-level rule that CAO
documents as advisory — provides no bound at all.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Sweep interval. 30 s bounds unauthorised work to roughly one worker turn on a
#: fast model, at 2 API calls a minute. Below ~10 s the sweep costs more than the
#: work it prevents.
DEFAULT_POLL_SECONDS = 30


def cao_depth_limit(max_depth: int) -> int:
    """Config levels -> CAO terminal depths. They are off by one.

    ``cao.levels`` counts Layer 1 as the outer orchestrator — this Claude
    session — which is not a CAO terminal at all. CAO's first spawned terminal
    (the Layer 2 supervisor) is therefore depth 1. So ``maxDepth: 3`` (L1, L2,
    L3) permits CAO depths 1 and 2, not 1..3.

    Passing ``cfg.max_depth`` straight to :func:`violations` silently allows one
    extra layer, which is the difference between a three-level hierarchy and a
    four-level one.
    """
    return max(1, int(max_depth) - 1)


@dataclass(frozen=True)
class Violation:
    terminal_id: str
    depth: object  # int, or None when the chain cannot be resolved
    max_depth: int
    agent_profile: str
    reason: str


def depth_of(terminal_id: str, by_id: dict, _seen=None):
    """Depth via the server-stamped ``caller_id`` chain. Root == 1.

    Returns ``None`` when the chain cannot be resolved — a missing parent (the
    orphan case) or a cycle. ``None`` means *unknown*, never *fine*.
    """
    seen = _seen if _seen is not None else set()
    if terminal_id in seen:
        return None  # cycle; impossible via caller_id, but never loop forever
    seen.add(terminal_id)

    term = by_id.get(terminal_id)
    if term is None:
        return None
    parent = term.get("caller_id")
    if not parent:
        return 1  # launched directly, not spawned by another terminal
    parent_depth = depth_of(parent, by_id, seen)
    return None if parent_depth is None else parent_depth + 1


def index(terminals) -> dict:
    return {t["id"]: t for t in terminals if t.get("id")}


def violations(terminals, max_depth: int) -> list:
    """Terminals deeper than ``max_depth``, plus any whose depth is unknown."""
    by_id = index(terminals)
    out = []
    for term in terminals:
        tid = term.get("id")
        if not tid:
            continue
        depth = depth_of(tid, by_id)
        profile = term.get("agent_profile") or "(unknown)"
        if depth is None:
            out.append(
                Violation(
                    tid,
                    None,
                    max_depth,
                    profile,
                    "depth unknown: caller_id chain does not resolve (orphaned "
                    "parent?) — reported for a human, not auto-terminated",
                )
            )
        elif depth > max_depth:
            out.append(
                Violation(
                    tid,
                    depth,
                    max_depth,
                    profile,
                    f"depth {depth} exceeds maxDepth {max_depth}",
                )
            )
    return out


def terminable(violations_) -> list:
    """Only violations we are willing to act on automatically.

    An unknown depth is never auto-terminated. Killing on uncertainty would make
    a transient API read into destroyed work, which is a worse failure than the
    over-spawn it is trying to stop.
    """
    return [v for v in violations_ if v.depth is not None]
