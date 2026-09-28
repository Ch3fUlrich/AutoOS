#!/usr/bin/env python3
"""Vibe Kanban bridge: the three things the board's own task model lacks.

FLEETSPEC §6.1 / D-089 / D-095. Vibe Kanban
(https://vibekanban.com, BloopAI/vibe-kanban) is the fleet's interim task board.
Its native model (To do -> In Progress -> In Review -> Done, driven by attempts)
has no inter-task dependency, no capacity-aware auto-pickup and no project/plan
pause, so this bridge adds them *outside* the board:

  * dependencies live in a small SQLite store keyed on Vibe Kanban's own task
    ids, so the board stays the single source of task identity;
  * "blocked" is computed from that store at read time and never stored, so it
    cannot drift from the dependency rows; the board is told by prefixing the
    task title with ``[blocked: <dep title>]`` -- the one honest place a blocked
    todo can sit in a model with no `blocked` column;
  * pausing is checked against the owning project/plan state, never copied onto
    the board.

Vibe Kanban is **not deployed yet**, so the Vibe-Kanban-facing calls are a
documented Protocol (``VibeKanbanClient``) with no real HTTP/MCP body. A real
implementation is separate follow-up work, once the service exists and its
API/auth are known; nothing here guesses at endpoint paths or payload shapes.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Callable, Protocol

__all__ = [
    "open_db",
    "add_dependency",
    "remove_dependency",
    "dependencies_of",
    "is_ready",
    "blocking_titles",
    "BLOCKED_PREFIX_RE",
    "blocked_title",
    "project_is_pollable",
    "VibeKanbanClient",
    "poll_and_dispatch",
]


# ---------------------------------------------------------------------------
# 1. Storage
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id           TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id)
)
"""


def open_db(path: str) -> sqlite3.Connection:
    """Open (creating if needed) the bridge's SQLite file, ensuring the schema.

    Safe to call twice on the same path: the DDL is ``IF NOT EXISTS``.
    """
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def add_dependency(conn: sqlite3.Connection, task_id: str,
                   depends_on_task_id: str) -> None:
    """Idempotently record that *task_id* depends on *depends_on_task_id*.

    ``INSERT OR IGNORE`` against the composite primary key makes a repeat a
    no-op rather than an error or a duplicate row. A self-dependency is
    rejected: it can never be satisfied, so it would hide a bug behind a task
    that waits on itself forever.
    """
    if task_id == depends_on_task_id:
        raise ValueError(
            "a task cannot depend on itself: %r" % (task_id,))
    conn.execute(
        "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id)"
        " VALUES (?, ?)",
        (task_id, depends_on_task_id),
    )
    conn.commit()


def remove_dependency(conn: sqlite3.Connection, task_id: str,
                      depends_on_task_id: str) -> None:
    """Idempotently drop the *task_id* -> *depends_on_task_id* row.

    Removing a row that does not exist is a no-op, not an error.
    """
    conn.execute(
        "DELETE FROM task_dependencies"
        " WHERE task_id = ? AND depends_on_task_id = ?",
        (task_id, depends_on_task_id),
    )
    conn.commit()


def dependencies_of(conn: sqlite3.Connection, task_id: str) -> list[str]:
    """All ``depends_on_task_id`` rows for *task_id*, in insertion order.

    Order is ``rowid`` order: the schema has no sequence column, and the table
    is a normal rowid table, so insertion order is what the board reader wants.
    """
    rows = conn.execute(
        "SELECT depends_on_task_id FROM task_dependencies"
        " WHERE task_id = ? ORDER BY rowid",
        (task_id,),
    ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# 2. Readiness (pure logic, no I/O)
# ---------------------------------------------------------------------------

# The one place this module states its own title convention. A blocked todo has
# nowhere honest to sit in Vibe Kanban's four columns, so the bridge writes the
# reason into the title instead; this regex is the only thing that recognises a
# prefix it wrote, which is what keeps blocked_title idempotent.
BLOCKED_PREFIX_RE = re.compile(r"^\[blocked: [^\]]*\] ")


def is_ready(depends_on: list[str], is_done: Callable[[str], bool]) -> bool:
    """True iff *depends_on* is empty, or every dep is done."""
    return all(is_done(dep) for dep in depends_on)


def blocking_titles(depends_on: list[str], is_done: Callable[[str], bool],
                    title_of: Callable[[str], str]) -> list[str]:
    """Titles of the not-yet-done dependencies, in *depends_on* order.

    An empty list means ready. A caller that has the ids but not all titles can
    pass a lookup that falls back to the id; a done dep is never looked up.
    """
    return [title_of(dep) for dep in depends_on if not is_done(dep)]


def blocked_title(base_title: str, blocking: list[str]) -> str:
    """*base_title* with at most one fresh ``[blocked: ...]`` prefix.

    Any existing prefix this module wrote is stripped first, then one is added
    back iff *blocking* is non-empty, so the call is idempotent (re-polling a
    blocked task rewrites the same string) and a resolved dependency removes
    the prefix rather than stacking a second one.
    """
    stripped = BLOCKED_PREFIX_RE.sub("", base_title)
    if not blocking:
        return stripped
    return "[blocked: %s] %s" % (", ".join(blocking), stripped)


# ---------------------------------------------------------------------------
# 3. Pause check (pure logic, no I/O)
# ---------------------------------------------------------------------------

def project_is_pollable(project_state: str,
                        plan_phase_status: str | None) -> bool:
    """False if the project is paused or its plan phase is blocked; else True.

    Pause is fleet-level policy that already lives in ``projects.state`` and
    ``plan_phases.status`` (FLEETSPEC §4.1), so the bridge only reads it -- no
    pause field is copied onto Vibe Kanban. ``None`` means no phase info is
    available, which is no reason to stall the poll.
    """
    if project_state == "paused":
        return False
    if plan_phase_status == "blocked":
        return False
    return True


# ---------------------------------------------------------------------------
# 4. Client interface (documented stub, no live calls)
# ---------------------------------------------------------------------------

class VibeKanbanClient(Protocol):
    """What ``poll_and_dispatch`` needs from Vibe Kanban.

    NOT implemented against the real API in this bridge. Vibe Kanban is not
    deployed anywhere yet, so its endpoint paths, auth headers and payload
    shapes are unknown and nothing here guesses at them; a real implementation
    (HTTP or MCP-backed) is separate follow-up work once the service exists.
    Tests drive this Protocol with a small fake.
    """

    def list_todo_tasks(self) -> list[dict]:
        """Every task the board shows in "To do", attempted or not.

        Each dict carries at least ``{id, title, project, attempted}``.
        """
        ...

    def is_task_done(self, task_id: str) -> bool:
        """True when the task has reached the board's Done state."""
        ...

    def set_title(self, task_id: str, title: str) -> None:
        """Set a task's title; a no-op when it is already exactly *title*."""
        ...

    def start_attempt(self, task_id: str, agent_profile: str) -> None:
        """Start a new attempt on *task_id* with the given agent profile."""
        ...


# ---------------------------------------------------------------------------
# 5. The poll loop
# ---------------------------------------------------------------------------

def _title_lookup(tasks: list[dict]) -> Callable[[str], str]:
    """Resolve a dependency id to its current title, falling back to the id.

    Dependency rows name Vibe Kanban task ids, but the protocol exposes titles
    only through ``list_todo_tasks()``. A done dependency is never looked up,
    and one that is mid-attempt (not in "To do") falls back to its id rather
    than stalling the poll.
    """
    titles = {task["id"]: task["title"] for task in tasks}
    return lambda task_id: titles.get(task_id, task_id)


def poll_and_dispatch(
    conn: sqlite3.Connection,
    client: VibeKanbanClient,
    project_status: Callable[[str], tuple[str, str | None]],
    route: Callable[[dict], str | None],
) -> list[str]:
    """One poll: title blocked todos honestly, start the ones that route.

    For every un-attempted todo task from ``client.list_todo_tasks()``:

    * skip it (no title change) when ``project_status(task['project'])`` says
      the project is not pollable (paused, or its plan phase is blocked);
    * otherwise resolve its dependencies; if any is not done, title it with
      ``blocked_title`` and move on -- a ``route()`` refusal is not why it was
      skipped here, it never got that far;
    * otherwise (ready) consult ``route(task)``; a returned agent profile starts
      an attempt and clears any stale ``[blocked: ...]`` prefix, while ``None``
      (no capacity, no credit, breaker open) leaves the task untouched for the
      next poll -- not an error, just the resolver's own deferred behaviour.

    Returns the task ids started this run, for the caller's log line.
    """
    tasks = client.list_todo_tasks()
    title_of = _title_lookup(tasks)

    started: list[str] = []
    for task in tasks:
        if task.get("attempted"):
            continue

        state, phase = project_status(task["project"])
        if not project_is_pollable(state, phase):
            continue

        depends_on = dependencies_of(conn, task["id"])
        blocking = blocking_titles(depends_on, client.is_task_done, title_of)
        if blocking:
            client.set_title(task["id"], blocked_title(task["title"], blocking))
            continue

        agent_profile = route(task)
        if agent_profile is None:
            continue

        client.set_title(task["id"], blocked_title(task["title"], []))
        client.start_attempt(task["id"], agent_profile)
        started.append(task["id"])

    return started
