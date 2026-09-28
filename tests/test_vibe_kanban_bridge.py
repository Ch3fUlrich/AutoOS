#!/usr/bin/env python3
"""Tests for tools/vibe_kanban_bridge.py (FLEETSPEC §6.1, D-089/D-095).

The bridge adds the three things Vibe Kanban's own task model lacks -- inter-task
dependencies, capacity-aware auto-pickup, and a project/plan pause check -- in a
small store keyed on Vibe Kanban's own task ids. "Blocked" is computed at read
time from ``task_dependencies`` and never stored, so the board and this store
cannot drift. Vibe Kanban is not deployed yet, so the client is a documented
Protocol and these tests drive a small fake, never a live API.

Run:

    python3 tests/test_vibe_kanban_bridge.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import vibe_kanban_bridge as vkb  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Storage
# ---------------------------------------------------------------------------

class StorageTests(unittest.TestCase):
    """The SQLite dependency store, each test on its own throwaway file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = str(Path(self._tmp.name) / "bridge.sqlite")

    def open_db(self):
        conn = vkb.open_db(self._db)
        self.addCleanup(conn.close)
        return conn

    def test_dependencies_persist_across_reopen(self):
        conn = self.open_db()
        vkb.add_dependency(conn, "task-b", "task-a")
        conn.close()

        reopened = self.open_db()
        self.assertEqual(vkb.dependencies_of(reopened, "task-b"), ["task-a"])

    def test_adding_the_same_pair_twice_does_not_error_or_duplicate(self):
        conn = self.open_db()
        vkb.add_dependency(conn, "task-b", "task-a")
        vkb.add_dependency(conn, "task-b", "task-a")
        self.assertEqual(vkb.dependencies_of(conn, "task-b"), ["task-a"])

    def test_a_task_depending_on_itself_is_a_value_error(self):
        conn = self.open_db()
        with self.assertRaises(ValueError):
            vkb.add_dependency(conn, "task-a", "task-a")
        self.assertEqual(vkb.dependencies_of(conn, "task-a"), [])

    def test_removing_a_dependency_that_does_not_exist_is_a_noop(self):
        conn = self.open_db()
        vkb.remove_dependency(conn, "task-b", "task-a")  # never added
        self.assertEqual(vkb.dependencies_of(conn, "task-b"), [])

    def test_remove_dependency_leaves_the_other_dependencies_alone(self):
        conn = self.open_db()
        vkb.add_dependency(conn, "task-b", "task-a")
        vkb.add_dependency(conn, "task-b", "task-c")
        vkb.remove_dependency(conn, "task-b", "task-a")
        self.assertEqual(vkb.dependencies_of(conn, "task-b"), ["task-c"])

    def test_dependencies_of_keeps_insertion_order(self):
        conn = self.open_db()
        for dep in ("task-a", "task-c", "task-d"):
            vkb.add_dependency(conn, "task-b", dep)
        self.assertEqual(vkb.dependencies_of(conn, "task-b"),
                         ["task-a", "task-c", "task-d"])

    def test_dependencies_are_per_task(self):
        conn = self.open_db()
        vkb.add_dependency(conn, "task-b", "task-a")
        vkb.add_dependency(conn, "task-c", "task-x")
        self.assertEqual(vkb.dependencies_of(conn, "task-b"), ["task-a"])
        self.assertEqual(vkb.dependencies_of(conn, "task-c"), ["task-x"])
        self.assertEqual(vkb.dependencies_of(conn, "task-a"), [])


# ---------------------------------------------------------------------------
# 2. Readiness (pure logic, no I/O)
# ---------------------------------------------------------------------------

def _done(*ids):
    done = set(ids)
    return lambda task_id: task_id in done


def _title(task_id):
    return {"task-a": "do A", "task-b": "do B"}.get(task_id, task_id)


class ReadinessTests(unittest.TestCase):
    def test_a_task_with_no_dependencies_is_ready(self):
        self.assertIs(vkb.is_ready([], _done()), True)

    def test_a_task_whose_dependencies_are_all_done_is_ready(self):
        self.assertIs(vkb.is_ready(["task-a", "task-b"],
                                   _done("task-a", "task-b")), True)

    def test_a_task_with_one_undone_dependency_is_not_ready(self):
        self.assertIs(vkb.is_ready(["task-a", "task-b"], _done("task-a")),
                      False)

    def test_an_empty_dependency_list_blocks_nothing(self):
        self.assertEqual(vkb.blocking_titles([], _done(), _title), [])

    def test_blocking_titles_names_only_the_undone_dependencies_in_order(self):
        self.assertEqual(
            vkb.blocking_titles(["task-b", "task-a", "task-x"],
                                _done("task-a"), _title),
            ["do B", "task-x"])

    def test_blocked_title_round_trips_when_applied_twice(self):
        once = vkb.blocked_title("do B", ["do A"])
        self.assertEqual(vkb.blocked_title(once, ["do A"]), once)
        self.assertEqual(once, "[blocked: do A] do B")

    def test_blocked_title_with_an_empty_list_strips_a_previous_prefix(self):
        prefixed = vkb.blocked_title("do B", ["do A"])
        self.assertEqual(vkb.blocked_title(prefixed, []), "do B")

    def test_blocked_title_never_double_prefixes(self):
        prefixed = vkb.blocked_title("do B", ["do A"])
        self.assertEqual(prefixed.count("[blocked:"), 1)

    def test_blocked_title_with_no_list_is_the_bare_title(self):
        self.assertEqual(vkb.blocked_title("do B", []), "do B")

    def test_blocked_title_strips_an_unrelated_previous_prefix_then_readds(self):
        # A dependency set can change between polls; the prefix is rebuilt, never
        # appended, so an old blocking dep cannot linger in the title.
        self.assertEqual(vkb.blocked_title("[blocked: do X] do B", ["do A"]),
                         "[blocked: do A] do B")

    def test_blocked_title_round_trips_when_a_blocking_title_contains_brackets(self):
        # A blocking dependency's own title can contain "]" (e.g. "do [x] A"); the
        # strip must match the LAST "] ", not the first, or a repoll corrupts the
        # title instead of rewriting the same string.
        once = vkb.blocked_title("do B", ["do [x] A"])
        self.assertEqual(once, "[blocked: do [x] A] do B")
        self.assertEqual(vkb.blocked_title(once, ["do [x] A"]), once)


# ---------------------------------------------------------------------------
# 3. Pause check (pure logic, no I/O)
# ---------------------------------------------------------------------------

class PauseCheckTests(unittest.TestCase):
    """project_is_pollable: state x phase, expanded one case per method."""

    def test_a_live_project_with_a_non_blocked_phase_is_pollable__active__none(self):
        self.assertIs(vkb.project_is_pollable("active", None), True)

    def test_a_live_project_with_a_non_blocked_phase_is_pollable__active__in_progress(self):
        self.assertIs(vkb.project_is_pollable("active", "in_progress"), True)

    def test_a_live_project_with_a_non_blocked_phase_is_pollable__active__done(self):
        self.assertIs(vkb.project_is_pollable("active", "done"), True)

    def test_a_live_project_with_a_non_blocked_phase_is_pollable__running__none(self):
        self.assertIs(vkb.project_is_pollable("running", None), True)

    def test_a_live_project_with_a_non_blocked_phase_is_pollable__running__in_progress(self):
        self.assertIs(vkb.project_is_pollable("running", "in_progress"), True)

    def test_a_live_project_with_a_non_blocked_phase_is_pollable__running__done(self):
        self.assertIs(vkb.project_is_pollable("running", "done"), True)

    def test_a_blocked_phase_is_never_pollable__active(self):
        self.assertIs(vkb.project_is_pollable("active", "blocked"), False)

    def test_a_blocked_phase_is_never_pollable__running(self):
        self.assertIs(vkb.project_is_pollable("running", "blocked"), False)

    def test_a_blocked_phase_is_never_pollable__paused(self):
        self.assertIs(vkb.project_is_pollable("paused", "blocked"), False)

    def test_a_paused_project_is_never_pollable__none(self):
        self.assertIs(vkb.project_is_pollable("paused", None), False)

    def test_a_paused_project_is_never_pollable__in_progress(self):
        self.assertIs(vkb.project_is_pollable("paused", "in_progress"), False)

    def test_a_paused_project_is_never_pollable__done(self):
        self.assertIs(vkb.project_is_pollable("paused", "done"), False)

    def test_a_paused_project_is_never_pollable__blocked(self):
        self.assertIs(vkb.project_is_pollable("paused", "blocked"), False)

    def test_no_phase_information_means_pollable_unless_paused(self):
        self.assertIs(vkb.project_is_pollable("active", None), True)
        self.assertIs(vkb.project_is_pollable("paused", None), False)


# ---------------------------------------------------------------------------
# 4/5. Client interface + poll loop
# ---------------------------------------------------------------------------

class FakeKanbanClient:
    """An in-memory VibeKanbanClient.

    The contract is four methods, so a small class states it more plainly than
    any mock chain -- and no mocking framework beyond the stdlib is used, as
    this repo's tests already do.
    """

    def __init__(self, tasks, done=()):
        self.tasks = [dict(task) for task in tasks]
        self.done = set(done)
        self.list_calls = 0
        self.set_title_calls = []
        self.start_attempt_calls = []

    def list_todo_tasks(self):
        self.list_calls += 1
        return [dict(task) for task in self.tasks]

    def is_task_done(self, task_id):
        return task_id in self.done

    def set_title(self, task_id, title):
        self.set_title_calls.append((task_id, title))
        self._task(task_id)["title"] = title

    def start_attempt(self, task_id, agent_profile):
        self.start_attempt_calls.append((task_id, agent_profile))
        self._task(task_id)["attempted"] = True

    def title(self, task_id):
        return self._task(task_id)["title"]

    def _task(self, task_id):
        return next(task for task in self.tasks if task["id"] == task_id)


def _todo(task_id, title, project="p", attempted=False):
    return {"id": task_id, "title": title, "project": project,
            "attempted": attempted}


def _pollable(conn, client, route):
    """poll_and_dispatch with a project that is always live."""
    return vkb.poll_and_dispatch(conn, client,
                                 lambda project: ("active", None), route)


class PollLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = vkb.open_db(str(Path(self._tmp.name) / "bridge.sqlite"))
        self.addCleanup(self.conn.close)

    def test_a_ready_unblocked_task_is_started_and_its_stale_prefix_cleared(self):
        client = FakeKanbanClient([_todo("b", "[blocked: do A] do B")])
        started = _pollable(self.conn, client, lambda task: "worker")
        self.assertEqual(started, ["b"])
        self.assertEqual(client.start_attempt_calls, [("b", "worker")])
        self.assertEqual(client.title("b"), "do B")

    def test_a_task_with_an_undone_dependency_is_blocked_and_never_started(self):
        vkb.add_dependency(self.conn, "b", "a")
        client = FakeKanbanClient([_todo("a", "do A"), _todo("b", "do B")])
        started = _pollable(self.conn, client, lambda task: "worker")
        self.assertIn(("b", "[blocked: do A] do B"), client.set_title_calls)
        self.assertTrue(all(task_id != "b"
                            for task_id, _ in client.start_attempt_calls))
        self.assertEqual(started, ["a"])

    def test_all_done_dependencies_make_the_task_startable(self):
        vkb.add_dependency(self.conn, "b", "a")
        client = FakeKanbanClient([_todo("b", "do B")], done={"a"})
        started = _pollable(self.conn, client, lambda task: "worker")
        self.assertEqual(started, ["b"])

    def test_a_ready_task_with_no_route_is_left_completely_alone(self):
        client = FakeKanbanClient([_todo("b", "do B")])
        started = _pollable(self.conn, client, lambda task: None)
        self.assertEqual(started, [])
        self.assertEqual(client.set_title_calls, [])
        self.assertEqual(client.start_attempt_calls, [])
        self.assertEqual(client.title("b"), "do B")

    def test_a_blocked_task_with_no_route_is_still_titled_blocked(self):
        # The refusal that leaves a task alone is the resolver's, not the
        # dependency's: a blocked task is titled before route() is ever
        # consulted.
        vkb.add_dependency(self.conn, "b", "a")
        client = FakeKanbanClient([_todo("a", "do A"), _todo("b", "do B")])
        _pollable(self.conn, client, lambda task: None)
        self.assertEqual(client.title("b"), "[blocked: do A] do B")
        self.assertEqual(client.start_attempt_calls, [])

    def test_a_paused_project_is_skipped_before_any_client_write(self):
        client = FakeKanbanClient([_todo("b", "do B", project="paused-proj")])
        started = vkb.poll_and_dispatch(self.conn, client,
                                        lambda project: ("paused", None),
                                        lambda task: "worker")
        self.assertEqual(started, [])
        self.assertEqual(client.set_title_calls, [])
        self.assertEqual(client.start_attempt_calls, [])
        self.assertEqual(client.list_calls, 1)

    def test_a_blocked_plan_phase_is_skipped_before_any_client_write(self):
        client = FakeKanbanClient([_todo("b", "do B")])
        started = vkb.poll_and_dispatch(self.conn, client,
                                        lambda project: ("active", "blocked"),
                                        lambda task: "worker")
        self.assertEqual(started, [])
        self.assertEqual(client.set_title_calls, [])
        self.assertEqual(client.start_attempt_calls, [])

    def test_an_already_attempted_todo_is_skipped(self):
        client = FakeKanbanClient([_todo("b", "do B", attempted=True)])
        started = _pollable(self.conn, client, lambda task: "worker")
        self.assertEqual(started, [])
        self.assertEqual(client.start_attempt_calls, [])

    def test_polling_twice_attempts_each_ready_task_once(self):
        client = FakeKanbanClient([_todo("b", "do B")])
        first = _pollable(self.conn, client, lambda task: "worker")
        second = _pollable(self.conn, client, lambda task: "worker")
        self.assertEqual(first, ["b"])
        self.assertEqual(second, [])
        self.assertEqual(client.start_attempt_calls, [("b", "worker")])

    def test_a_blocked_task_unblocks_when_its_dependency_finishes(self):
        vkb.add_dependency(self.conn, "b", "a")
        client = FakeKanbanClient([_todo("a", "do A"), _todo("b", "do B")])

        _pollable(self.conn, client, lambda task: "worker")
        self.assertEqual(client.title("b"), "[blocked: do A] do B")

        client.done.add("a")
        started = _pollable(self.conn, client, lambda task: "worker")
        self.assertIn("b", started)
        self.assertEqual(client.title("b"), "do B")


if __name__ == "__main__":
    unittest.main()
