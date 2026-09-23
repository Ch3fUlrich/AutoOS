"""``PLAN.lock.json`` — the artifact that gates dispatch.

Instructions do not hold; a validator does. The current CAO workflow starts
executing the moment a task is submitted, and its profiles *tell* agents to plan
with nothing checking that they did. Prompt text is not a gate.

The decisive rule is :func:`_require_interactivity`: a plan must record either an
answer the **user** gave, or an explicitly justified assumption. There is no way
to express "I did not ask" in a document that validates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA = "cao-plan/1"

#: Anything still wearing angle brackets was never filled in. Measured
#: 2026-09-10: the scaffold written by `cao plan` PASSED validation unedited —
#: its placeholder approvedAt was a non-empty string and its template question
#: carried answeredBy "user" — so an agent could scaffold a plan and launch
#: immediately, having asked the user nothing. A gate with a bypass in the
#: scaffold it ships is not a gate.
#:
#: A placeholder has no whitespace just inside its brackets (``<fill me>``),
#: which distinguishes it from ordinary prose comparisons (``latency < 20ms and
#: memory > 1GB``) and from shell redirects. Getting this wrong in the strict
#: direction would reject valid plans, so the pattern errs toward permissive:
#: the interactivity and guard rules still stand behind it.
PLACEHOLDER = re.compile(r"<[^\s<>][^<>]*[^\s<>]>")


class PlanError(Exception):
    """The plan contract is invalid. Dispatch must not proceed."""


def _require(condition, message):
    if not condition:
        raise PlanError(message)


def _require_interactivity(doc):
    answered = [q for q in doc.get("openQuestions", []) if q.get("answeredBy") == "user"]
    assumed = doc.get("assumedDefaults", [])
    _require(
        answered or assumed,
        "planning gate: ask the user at least one question, or record "
        "assumedDefaults saying what you decided on their behalf",
    )
    for entry in assumed:
        _require(
            entry.get("assumption") and entry.get("why"),
            "each assumedDefaults entry needs both 'assumption' and 'why' — "
            "an undocumented assumption is just a skipped question",
        )


def _placeholders(node, path="") -> list:
    """Every ``<like this>`` value still left in the document, with its path."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += _placeholders(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _placeholders(value, f"{path}[{index}]")
    elif isinstance(node, str) and PLACEHOLDER.search(node):
        found.append(f"{path} = {node!r}")
    return found


def _require_no_placeholders(doc):
    left = _placeholders(doc)
    _require(
        not left,
        "the plan still contains unfilled placeholders, so it was never agreed "
        "with anyone: " + "; ".join(left[:4]) + ("; ..." if len(left) > 4 else ""),
    )


def _require_dag(phases):
    ids = {p["id"] for p in phases}
    for phase in phases:
        for dep in phase.get("dependsOn", []):
            _require(
                dep in ids,
                f"phase {phase['id']!r} dependsOn unknown phase {dep!r}",
            )

    graph = {p["id"]: list(p.get("dependsOn", [])) for p in phases}
    # Three-colour DFS: "open" marks a node on the current path, so revisiting
    # one is a cycle, while revisiting a "done" node is just a diamond.
    state = {}

    def walk(node):
        if state.get(node) == "done":
            return
        _require(state.get(node) != "open", f"dependency cycle at phase {node!r}")
        state[node] = "open"
        for dep in graph[node]:
            walk(dep)
        state[node] = "done"

    for node in graph:
        walk(node)


def validate(doc: dict, max_depth: int) -> None:
    _require(
        doc.get("schema") == SCHEMA,
        f"schema must be {SCHEMA!r}, got {doc.get('schema')!r}",
    )
    _require(doc.get("approvedAt"), "approvedAt is required — the plan is not approved")
    _require(doc.get("goal"), "goal is required")

    depth = int(doc.get("depth", 0))
    _require(depth <= max_depth, f"depth {depth} exceeds cao.maxDepth {max_depth}")

    phases = doc.get("phases") or []
    _require(phases, "a plan with no phases dispatches nothing")
    for phase in phases:
        _require(phase.get("id"), "every phase needs an id")
        _require(
            (phase.get("acceptance") or {}).get("guard"),
            f"phase {phase.get('id')!r}: acceptance.guard is required — "
            "a phase with no way to be checked is not a phase",
        )

    _require_dag(phases)
    _require_no_placeholders(doc)
    _require_interactivity(doc)


def load(path, max_depth: int) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(doc, max_depth)
    return doc
