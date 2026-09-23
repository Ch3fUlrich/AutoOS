"""The launch path: where every rule this lane states is actually applied.

Before this module existed, :mod:`cao.plan`, :mod:`cao.budget`, :mod:`cao.leases`
and :mod:`cao.wire` were well-tested libraries that **nothing called**. The
headline rule — an orchestrator must not just start — was a function with no
caller, which is indistinguishable from not having the rule at all.

One entry point, :func:`plan_launch`, and it refuses rather than improvises:

* no validated ``PLAN.lock.json`` -> refuse (the interactive planning gate);
* an unknown phase, or one without a guard -> refuse;
* every pool in the task's ladder closed -> refuse;
* the phase already claimed by another agent -> refuse.

Refusing is the point. Each of these has a tempting "sensible default" — assume
the plan, pick any model, run it twice — and each default silently produces work
nobody asked for.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from cao import leases, plan as plan_mod, wire
from cao.dispatch import pick_reviewer
from cao.profiles import PROVIDER_BY_POOL, profile_name, project_slug
from cao.routing import ladder, select as routing_select
from cao.worktree import provision_script


class LaunchRefused(Exception):
    """A precondition failed. Nothing was started; nothing was claimed."""


@dataclass
class Launch:
    phase_id: str
    candidate: object
    task: str
    argv: list
    worktree: str
    lease_held: bool = False
    descended: bool = False
    supervisor_profile: str = ""
    supervisor_provider: str = ""
    session_name: str = ""
    review_degraded: bool = False
    notes: list = field(default_factory=list)


def session_name_for(project: str, phase_id: str, attempt: int = 1) -> str:
    """``cao-<project>-<phase>``, with ``-r<n>`` from the second attempt on."""
    base = f"cao-{project}-{phase_id}"
    return base if attempt <= 1 else f"{base}-r{attempt}"


def pick_attempt(existing_names, project: str, phase_id: str, start: int = 2,
                 limit: int = 50) -> int:
    """The lowest attempt number whose session name is still free.

    Derived from the names CAO actually has, not from a local counter.
    Measured live 2026-09-11: the ledger said "no reworks yet" while the server
    already held `-r2` from a spawn recorded before the counter was wired, so
    every rework asked for a name that existed and was refused with 400. A
    counter and a server can disagree; only one of them owns the namespace.
    """
    taken = set(existing_names or ())
    for attempt in range(max(start, 1), max(start, 1) + limit):
        if session_name_for(project, phase_id, attempt) not in taken:
            return attempt
    raise LaunchRefused(
        f"{limit} session names for phase {phase_id!r} are all taken; shut some "
        "down (`cao shutdown`) rather than accumulating dead sessions"
    )


def _phase(doc, phase_id):
    for phase in doc.get("phases", []):
        if phase.get("id") == phase_id:
            return phase
    known = [p.get("id") for p in doc.get("phases", [])]
    raise LaunchRefused(f"unknown phase {phase_id!r}; plan defines {known}")


def _select(cfg, task_class, is_open):
    """First candidate whose pool is open, and whether we had to descend.

    Delegates to :func:`cao.routing.select` rather than re-implementing the
    walk: two copies of "which model do we use" is exactly the kind of drift
    that makes a ladder mean different things in different places.
    """
    try:
        candidates = ladder(cfg, task_class)
        chosen = routing_select(cfg, task_class, is_open)
    except KeyError as exc:
        raise LaunchRefused(f"unknown task class: {exc}") from None
    except RuntimeError as exc:
        raise LaunchRefused(str(exc)) from None
    return chosen, candidates.index(chosen) > 0


def _supervisor_level(cfg):
    """The shallowest spawning level below Layer 1 — the one Layer 1 launches."""
    spawners = [
        level
        for level in cfg.levels
        if level.get("canSpawn") and level["level"] > 1
    ]
    if not spawners:
        raise LaunchRefused(
            "cao.levels defines no spawning level below Layer 1, so there is "
            "nothing for this session to launch"
        )
    return min(spawners, key=lambda level: level["level"])


def _graph_id(repo_root) -> str:
    """Repo folder name, resolved first.

    A relative repo_root (``.``) yields an EMPTY name, and an empty
    OMNIGRAPH_GRAPH_ID is the wrong-graph failure this lane exists to prevent —
    agents would silently fall back to the global pin. Measured in a dry run
    that printed ``--env OMNIGRAPH_GRAPH_ID=''``.
    """
    text = str(Path(str(repo_root)).resolve()).replace("\\", "/").rstrip("/")
    name = text.rsplit("/", 1)[-1] if "/" in text else text
    if not name:
        raise LaunchRefused(
            f"cannot derive an Omnigraph graph id from repo root {repo_root!r}; "
            "an empty graph id makes every agent write to the wrong graph"
        )
    return name


def _shell_path(path: str) -> str:
    """Quote a path for a shell WITHOUT killing variable expansion.

    ``shlex.quote`` wraps in single quotes, which is correct for literals and
    wrong for ``$HOME/cao-worktrees/...`` — measured, it produced a
    ``--working-directory '$HOME/...'`` that the shell took literally. Paths
    carrying a variable are double-quoted so the DISTRO expands them; everything
    else is quoted normally.
    """
    text = str(path)
    if "$" in text:
        return '"' + text.replace('"', '\\"') + '"'
    return shlex.quote(text)


def _posix_repo(repo_root, wsl_distro) -> str:
    r"""The repo path as the distro sees it.

    On Windows the driving host says ``C:\Users\...`` and the distro needs
    ``/mnt/c/Users/...``. ``wslpath -a`` does that translation correctly for
    every mount layout, so it is computed IN the shell rather than guessed here.
    """
    text = str(repo_root)
    if wsl_distro:
        return f'"$(wslpath -a {shlex.quote(text)})"'
    return shlex.quote(text)


def _refuse_if_main_checkout(work_dir, repo_root) -> None:
    """Refuse to point an agent at the main checkout.

    Compared textually, not by resolving: ``work_dir`` may legitimately contain
    an unexpanded ``$HOME`` that only the distro can resolve, and resolving it
    here on the wrong host would compare two different filesystems.
    """
    left = str(work_dir).replace("\\", "/").rstrip("/")
    right = str(Path(str(repo_root)).resolve()).replace("\\", "/").rstrip("/")
    if left.lower() == right.lower():
        raise LaunchRefused(
            f"refusing to run an agent in the main checkout ({right}). Workers "
            "hold an unrestricted shell; they get a disposable worktree so a bad "
            "command costs a throwaway directory, not your uncommitted work."
        )


def plan_launch(
    cfg,
    plan_path,
    phase_id: str,
    *,
    lease_dir=None,
    is_open=None,
    dry_run: bool = False,
    wsl_distro: str | None = None,
    owner: str = "layer1",
    worktree: str | None = None,
    secrets=None,
    extra_brief: str | None = None,
    implemented_by: str | None = None,
    attempt: int = 1,
) -> Launch:
    """Validate everything, claim the phase, and build the launch command.

    Nothing here talks to CAO. The result is a plan of action the caller may
    print (``dry_run``) or execute, which keeps the gate testable without a
    running server — the reason the gate went unwired the first time.
    """
    is_open = is_open or (lambda _pool: True)
    plan_file = Path(plan_path)

    # 1. The gate. A missing contract is reported as a refusal, not a traceback.
    if not plan_file.is_file():
        raise LaunchRefused(
            f"no plan contract at {plan_file} — agree one with the user first "
            "(`python -m cao plan`). Long-horizon work does not start "
            "from an unasked question."
        )
    try:
        doc = plan_mod.load(plan_file, cfg.max_depth)
    except plan_mod.PlanError as exc:
        raise LaunchRefused(str(exc)) from None
    except ValueError as exc:  # malformed JSON
        raise LaunchRefused(f"{plan_file} is not valid JSON: {exc}") from None

    phase = _phase(doc, phase_id)

    # 2. Model. A review phase pairs on FAMILY, not pool: reviewing Claude code
    #    with claude-opus-4-6 via agy spends different quota and consults the
    #    same blind spots. `reviews: <phase_id>` names what is being reviewed;
    #    implemented_by says which family wrote it.
    notes = []
    task_class = phase.get("taskClass", "implement")
    degraded = False
    if task_class == "review" and implemented_by:
        try:
            candidate, degraded = pick_reviewer(cfg, implemented_by, is_open)
        except RuntimeError as exc:
            raise LaunchRefused(str(exc)) from None
        descended = False
        if degraded:
            notes.append(
                f"review DEGRADED to the same family ({candidate.family}) — every "
                "other family was closed. A same-family review shares the blind "
                "spot it is meant to catch; this is recorded, never hidden."
            )
        else:
            notes.append(
                f"cross-family review: {implemented_by} code reviewed by "
                f"{candidate.family}"
            )
    else:
        candidate, descended = _select(cfg, task_class, is_open)

    if descended:
        notes.append(
            f"descended to {candidate.pool}/{candidate.model} — a higher-ranked "
            "pool was closed; recorded so the report cannot misstate the model"
        )

    # 3. Claim, so two agents cannot take the same phase. Dry runs never claim:
    #    a dry run that took the lease would block the real launch after it.
    lease_held = False
    if not dry_run:
        lease_root = lease_dir or Path(cfg.repo_root) / ".cao-leases"
        if not leases.claim(lease_root, phase_id, owner):
            state = leases.state(lease_root, phase_id) or {}
            raise LaunchRefused(
                f"phase {phase_id!r} is already claimed by "
                f"{state.get('owner', 'another agent')} — resume it or release "
                "the lease; starting a second worker duplicates the work"
            )
        lease_held = True

    # 4. The envelope. plan_ref, never the plan itself.
    acceptance = phase.get("acceptance") or {}
    task = wire.render_task(
        task_id=phase_id,
        goal=phase.get("goal", ""),
        plan_ref=f"{plan_file.name}#/phases/{phase_id}",
        files=phase.get("files") or [],
        guard=acceptance.get("guard", ""),
        expect=acceptance.get("expect", "exit0"),
        depth=2,
        max_depth=int(doc.get("depth", cfg.max_depth)),
        budget_tokens=phase.get("budgetTokens", 40000),
        reply_to=owner,
        # The supervisor is the one that spawns the leaf, so the ladder's choice
        # has to travel to IT. Printing it in a dry run and dropping it from the
        # envelope made the whole routing/effort ladder advisory.
        leaf_pool=candidate.pool,
        leaf_model=candidate.model,
        leaf_effort=getattr(candidate, "effort", None),
    )

    if extra_brief:
        # Rework: the guard's own output travels with the task, unparaphrased.
        # The previous agent already believed it had succeeded, so a summary of
        # the failure is one more chance to soften it.
        task = task + "\n" + extra_brief

    # 5. Where the work happens. Slow filesystems are a 20x tax (SKILL 9.5).
    graph_id = _graph_id(cfg.repo_root)
    work_dir = worktree or f"{cfg.worktree_root}/{graph_id}-{phase_id}"

    # An agent gets a DISPOSABLE worktree, never the main checkout. Leaves hold
    # execute_bash and fs_*, which is an unrestricted shell: pointed at the real
    # repository, one bad command destroys uncommitted work in a place nothing
    # here can undo. In a worktree the blast radius is a directory that exists to
    # be thrown away.
    _refuse_if_main_checkout(work_dir, cfg.repo_root)

    # 6. The command. Every element here is a measured trap: CAO_HOME_DIR
    #    (or profiles silently do not exist), --provider (or CAO falls back to
    #    kiro_cli), a LOGIN shell (or ~/.local/bin is invisible), and the graph
    #    pin forwarded to the supervisor AND every worker it spawns.
    level = _supervisor_level(cfg)
    provider = PROVIDER_BY_POOL[level.get("pool", "anthropic")]
    project = project_slug(graph_id)
    # CAO session names are unique, server-side: a rework that reuses the failed
    # attempt's name is refused with 400 "Session ... already exists" and the
    # whole rework path is dead. Measured live 2026-09-11 on the first rework
    # this lane ever attempted.
    session_name = session_name_for(project, phase_id, attempt)
    profile = profile_name(project, level["level"], level.get("role", "supervisor"))

    # The worktree must EXIST before launch: CAO resolves the working directory
    # with allow_create=False. Provisioning, hydrating and trusting it happen in
    # the same shell invocation so there is no window where a half-made worktree
    # is handed to an agent, and so this works identically on Linux and via WSL.
    #
    # Trusting is not optional: measured, an untrusted worktree leaves Claude
    # Code sitting at an approval prompt until CAO deletes the terminal.
    repo_posix = _posix_repo(cfg.repo_root, wsl_distro)
    skill_posix = f"{repo_posix}/skills/unattended-orchestration"
    wt = _shell_path(work_dir)

    inner = "; ".join(
        [
            'export CAO_HOME_DIR="$HOME/.cao"',
            provision_script(repo_posix, wt, skill_posix, graph_id=graph_id),
            " ".join(
                [
                    "cao launch",
                    f"--agents {shlex.quote(profile)}",
                    f"--provider {shlex.quote(provider)}",
                    # The project name makes a busy `cao session list` readable when several
                    # repositories are orchestrating at once.
                    f"--session-name {shlex.quote(session_name)}",
                    "--headless --async --auto-approve",
                    f"--working-directory {wt}",
                    f"--env OMNIGRAPH_GRAPH_ID={shlex.quote(graph_id)}",
                    shlex.quote(task),
                ]
            ),
        ]
    )
    argv = (
        ["wsl", "-d", wsl_distro, "-e", "bash", "-lc", inner]
        if wsl_distro
        else ["bash", "-lc", inner]
    )

    return Launch(
        phase_id=phase_id,
        candidate=candidate,
        task=task,
        argv=argv,
        worktree=work_dir,
        lease_held=lease_held,
        supervisor_profile=profile,
        supervisor_provider=provider,
        session_name=session_name,
        descended=descended,
        review_degraded=degraded,
        notes=notes,
    )
