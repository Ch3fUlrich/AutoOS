"""``python -m cao <verb>`` — the operator surface for the CAO lane.

Verbs are deliberately read-mostly. The one that acts (``sweep --enforce``)
terminates only what :mod:`cao.monitor` is willing to act on automatically, and
prints what it did.

    python -m cao check      # config sanity + provider availability
    python -m cao probe      # is each pool actually USABLE, not just installed
    python -m cao profiles   # generate per-level profiles, print install commands
    python -m cao plan       # scaffold a contract (ships deliberately INVALID)
    python -m cao launch     # refuses without an approved plan
    python -m cao verify     # run the guard yourself; --terminal catches a
                             #   'status=ok' the guard disagrees with
    python -m cao sweep      # depth violations + agents waiting on input
    python -m cao resume     # restart work nobody else continued

Exit codes are distinguishable on purpose: 0 done, 1 action needed, 2 crash or
unreachable, 3 refused, 4 needs a human.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from cao import monitor, watchdog, worktree
from cao.caoapi import CaoClient
from cao.config import (
    ConfigMissing,
    find_secrets,
    load_config,
    load_secrets,
    pool_available,
)
from cao.budget import (
    Ledger,
    mark_available,
    is_open as pool_is_open,
    mark_unavailable,
    note_provider_output,
)
from cao.launch import LaunchRefused, plan_launch
from cao import leases
from cao.plan import SCHEMA as PLAN_SCHEMA, PlanError, load as plan_load
from cao.probe import PROBE_TOKEN, classify, closes_pool, probe_argv
from cao.profiles import (
    project_slug,
    generate,
    leaf_enforcement_warnings,
    orchestrator_model_warnings,
    read_profile,
    spawner_restriction_warnings,
)
from cao.routing import check_freshness
from cao.verify import (
    CONTRADICTED,
    NO_CLAIM,
    attempts_for,
    record_rework,
    compare_claim,
    record,
    rework_brief,
    verify_phase,
)
from cao.worktree import warnings_for


def _cfg(args):
    return load_config(args.repo)


def cmd_check(args) -> int:
    cfg = _cfg(args)
    try:
        secrets_path = find_secrets(args.repo)
    except FileNotFoundError as e:
        print(f"secrets: {e}")
        return 1  # action needed: fix the path, or unset AUTOOS_SECRETS
    secrets = load_secrets(secrets_path)

    print(f"secrets: {secrets_path} ({'found' if secrets else 'absent'})")
    print("pools:")
    ledger = Ledger(Path(args.repo) / args.state_dir / "budget.ndjson")
    unavailable = []
    for pool in sorted(cfg.pools):
        ok = pool_available(cfg, pool, secrets)
        print(f"  {pool:<14} {'available' if ok else 'NO CREDENTIALS'}")
        if not ok:
            unavailable.append(pool)
            # Record it, so routing ladders SKIP this pool instead of dispatching
            # to a provider with no key and reading the failure as a code problem.
            # Printing alone left the budget ledger ignorant of it.
            mark_unavailable(ledger, pool)

    # Every verb that does anything -- launch, sweep, verify --terminal, resume
    # -- needs this server. Reporting pools as "available" while it is down is
    # the same shape as a check that cannot fail: an agent reads green, runs
    # `launch`, and gets a connection error it has no reason to expect.
    server_ok = True
    try:
        CaoClient(cfg.server).health()
        print(f"\ncao server: {cfg.server} (reachable)")
    except Exception as exc:
        server_ok = False
        print(f"\ncao server: {cfg.server} UNREACHABLE ({type(exc).__name__})")

    warns = (
        check_freshness(cfg)
        + orchestrator_model_warnings(cfg)
        + leaf_enforcement_warnings(cfg)
        + spawner_restriction_warnings(cfg)
        + warnings_for(cfg.worktree_root, cfg.repo_root)
    )
    print(f"\nwarnings ({len(warns)}):")
    for w in warns:
        print(f"  - {w}")

    # Unavailable pools are NOT an error here: ladders skip them exactly as they
    # skip a cooling pool. `setup_cao.py --check` is the gate that fails. An
    # unreachable SERVER is different -- nothing can run at all -- so it is the
    # one thing here that makes preflight say "action needed", not "ready".
    if not server_ok:
        print(
            f"\nNOT READY: no CAO server at {cfg.server}. Start it where the "
            "agents will run:\n"
            '  export CAO_HOME_DIR="$HOME/.cao" && cao-server\n'
            "Every verb that acts (launch, sweep, verify --terminal, resume) "
            "needs it. Config and credentials above are fine.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_probe(args) -> int:
    ledger = Ledger(Path(args.repo) / args.state_dir / "budget.ndjson")
    print(f"{'pool':<14}{'verdict':<20}evidence")
    print("-" * 72)
    worst = 0
    for pool in ("anthropic", "antigravity", "deepseek"):
        argv = probe_argv(pool, repo=args.wsl_repo or args.repo, wsl_distro=args.distro)
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=args.timeout)
            verdict = classify(r.stdout, r.stderr, r.returncode)
            lines = [ln for ln in (r.stdout or r.stderr).splitlines() if ln.strip()]
            # Prefer the line that ANSWERS. A pane capture ends with a prompt
            # or the echoed command, so "last line" showed the question back.
            # The line that IS the answer, not one that merely quotes it: a
            # pane capture contains the echoed question too, inside a prompt
            # box the shell may split across rows.
            answer = [ln for ln in lines if ln.strip() == PROBE_TOKEN]
            evidence = (answer[-1] if answer else lines[-1]).strip()[:40] if lines else "(no output)"
        except subprocess.TimeoutExpired:
            verdict, evidence = "failed", f"no answer in {args.timeout}s"
        print(f"{pool:<14}{verdict:<20}{evidence}")
        # Record it, or the ladder keeps selecting a pool this verb just proved
        # unusable. Measured live 2026-09-11: probe reported `antigravity
        # failed`, routing chose antigravity anyway, and two spawned workers
        # died on a model the provider would not serve. `check` asks whether
        # credentials exist; `probe` asks whether the thing WORKS, and only the
        # second question should gate dispatch.
        if verdict == "ok":
            mark_available(ledger, pool, reason="probe ok")
        elif closes_pool(verdict):
            worst = 1
            mark_unavailable(ledger, pool, reason=f"probe: {verdict}",
                             raw=evidence)
        else:
            # Reported, never acted on. A timeout or a harness limitation is
            # not proof the pool is down, and closing on it declines capacity
            # this lane has a standing rule against declining.
            worst = 1
    return worst


def cmd_profiles(args) -> int:
    cfg = _cfg(args)
    out = Path(args.out)
    written = generate(cfg, out)
    print(f"generated {len(written)} profiles in {out}:")
    for path in written:
        prof = read_profile(path)
        print(f"  {prof['name']:<22} {prof['provider']:<14} {prof['model']}")

    print("\ninstall them into the SERVER's home (they do not exist otherwise):")
    print(f'  export CAO_HOME_DIR="$HOME/.cao"')
    for path in written:
        print(f"  cao install {path.as_posix()}")
    print("\nand launch with an explicit --provider (the profile's is ignored):")
    first = read_profile(written[0])
    print(f"  cao launch --agents {first['name']} --provider {first['provider']} ...")
    return 0


def cmd_sweep(args) -> int:
    cfg = _cfg(args)
    client = CaoClient(cfg.server)
    try:
        sessions = client.sessions()
        # Three hops, not one: /sessions carries no terminals array, and the
        # per-session list carries no caller_id or status. Shortcutting either
        # makes this sweep a no-op that prints "0 violation(s)".
        terminals = client.all_terminals()
    except Exception as exc:
        print(f"cannot reach CAO at {cfg.server}: {exc}", file=sys.stderr)
        return 2

    print(f"{len(sessions)} session(s), {len(terminals)} terminal(s)")

    limit = monitor.cao_depth_limit(cfg.max_depth)
    violations = monitor.violations(terminals, limit)
    print(
        f"\ndepth (cao.maxDepth={cfg.max_depth} -> CAO terminal depth <= {limit}): "
        f"{len(violations)} violation(s)"
    )
    for v in violations:
        print(f"  {v.terminal_id} [{v.agent_profile}] depth={v.depth} - {v.reason}")

    # Fetch the PROMPT for each blocked terminal: the status says a decision is
    # pending, only the text says whether it is a trust dialog or a request to
    # spend money. An unreadable prompt is escalated, never silently skipped.
    prompts = {}
    for term in terminals:
        if watchdog.is_blocked(term) and term.get("id"):
            try:
                prompts[term["id"]] = client.terminal_output(term["id"])
            except Exception:
                pass

    blocked = watchdog.blocked_terminals(terminals, prompts)
    print(f"\nwaiting on input: {len(blocked)}")
    print("  " + watchdog.summarise(blocked).replace("\n", "\n  "))

    if args.answer and blocked:
        answerable = watchdog.auto_answerable(blocked)
        human = watchdog.needs_human(blocked)
        print(f"\nanswering {len(answerable)}; {len(human)} need a human:")
        for b in answerable:
            try:
                client.answer(b.terminal_id, b.answer)
                print(f"  {b.terminal_id}: sent {b.answer!r}")
            except Exception as exc:
                print(f"  {b.terminal_id}: FAILED to answer: {exc}", file=sys.stderr)
        for b in human:
            print(f"  {b.terminal_id}: ESCALATED - {b.reason}")

    # Worktrees whose session is gone. Reported, never removed: a worktree holds
    # an agent's uncommitted work, and "no session" can also mean the server was
    # restarted. Measured 2026-09-11: three survived four live runs and had to be
    # found by hand, so without this they just accumulate.
    try:
        listing = subprocess.run(
            ["git", "-C", str(cfg.repo_root), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=60,
        ).stdout
        graph = Path(str(cfg.repo_root)).resolve().name
        stale = worktree.orphans(
            worktree.parse_worktrees(listing),
            [s.get("name", "") for s in sessions],
            graph,
            project_slug(graph),
        )
    except (OSError, subprocess.SubprocessError):
        stale = []

    print(f"\nworktrees with no session: {len(stale)}")
    for path in stale:
        state = "DIRTY - has uncommitted work" if worktree.is_dirty(path) else "clean"
        print(f"  {path}  ({state})")
    if stale:
        print("  reclaim a clean one with:")
        print(f"    git -C {cfg.repo_root} worktree remove <path>")
        print("  a DIRTY one holds work an agent never committed - look first.")

    if args.enforce:
        acted = monitor.terminable(violations)
        print(f"\nterminating {len(acted)} (unknown-depth terminals are never auto-killed):")
        for v in acted:
            print(f"  would terminate {v.terminal_id} (depth {v.depth})")

    return 1 if (violations or watchdog.needs_human(blocked)) else 0


PLAN_TEMPLATE = {
    "schema": PLAN_SCHEMA,
    "goal": "<one sentence: what this run is for>",
    "createdBy": "claude-code",
    "approvedAt": "<ISO timestamp — set this only after the user approves>",
    "depth": 3,
    "openQuestions": [
        {
            "q": "<a question you actually asked the user>",
            "answer": "<their answer>",
            "answeredBy": "user",
        }
    ],
    "assumedDefaults": [],
    "phases": [
        {
            "id": "p1",
            "goal": "<what this phase delivers>",
            "taskClass": "implement",
            "acceptance": {"guard": "<the command that proves it done>", "expect": "exit0"},
            "dependsOn": [],
            "files": [],
        }
    ],
    "refusals": [],
}


def cmd_plan(args) -> int:
    """Scaffold a plan contract. It is deliberately INVALID until edited."""
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"error: {out} exists; pass --force to overwrite", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(PLAN_TEMPLATE, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(
        "\nIt will NOT validate yet, on purpose:\n"
        "  - approvedAt is a placeholder; set it only after the user approves\n"
        "  - openQuestions holds a template, not a question you actually asked\n"
        "  - every phase needs a guard: the command that proves it done\n"
        "Fill those in WITH the user, then: python -m cao launch --phase p1"
    )
    return 0


def cmd_launch(args) -> int:
    cfg = _cfg(args)
    state = Path(args.repo) / args.state_dir
    ledger = Ledger(state / "budget.ndjson")
    try:
        result = plan_launch(
            cfg,
            args.plan,
            args.phase,
            lease_dir=state / "leases",
            is_open=lambda pool: pool_is_open(ledger, pool),
            dry_run=args.dry_run,
            wsl_distro=args.distro,
        )
    except LaunchRefused as exc:
        # Refusing is the feature. Exit 3 so a wrapper can tell a refusal from a
        # crash (2) or a completed run (0).
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    effort = f" effort={result.candidate.effort}" if result.candidate.effort else ""
    print(f"phase:      {result.phase_id}")
    # Two different models, and conflating them misreads the whole run: the
    # SUPERVISOR is what this command launches, and `leaf model` is what the
    # ladder picked for the work itself, carried to the supervisor in the
    # envelope. A single "model:" line here said Gemini while launching Opus.
    print(f"supervisor: {result.supervisor_provider}/{result.supervisor_profile}"
          "  (what this command launches)")
    print(f"leaf model: {result.candidate.pool}/{result.candidate.model}{effort}"
          "  (what the supervisor spawns, carried in the envelope)")
    print(f"worktree:   {result.worktree}")
    print(f"lease:      {'claimed' if result.lease_held else 'not claimed (dry run)'}")
    for note in result.notes:
        print(f"note:     {note}")

    print("\nTASK envelope:")
    for line in result.task.splitlines():
        print(f"  {line}")

    if args.dry_run:
        print("\ncommand (not executed):")
        print("  " + " ".join(result.argv))
        return 0

    print("\nlaunching...")
    completed = subprocess.run(
        result.argv, capture_output=True, text=True, timeout=args.timeout
    )
    blob = (completed.stdout or "") + (completed.stderr or "")
    for line in blob.strip().splitlines()[-6:]:
        print(f"  {line}")
    # Cool the pool if the provider said it is out of quota; otherwise the
    # ladder never descends and `is_open` stays True forever.
    hit = note_provider_output(ledger, result.candidate.pool, blob)
    if hit == "billing":
        # Waiting cannot help. Saying "cooled" here would tell an operator
        # to come back in an hour to the identical wall.
        print(
            f"\nquota: {result.candidate.pool} CLOSED - a billing wall, which "
            "does not clear by waiting. The next launch descends the ladder; "
            "this pool needs a human."
        )
    elif hit:
        print(
            f"\nquota: {result.candidate.pool} cooled for an hour (quota "
            "exhausted); the next launch descends the ladder."
        )
    if "timed out" in blob.lower():
        # Measured: the server usually created the session anyway.
        print(
            "\nnote: a launch client-timeout is NOT a failure. Check "
            "`cao session list` before retrying, or you will create duplicates."
        )
    return 0

def _free_attempt(cfg, repo, phase_id, fallback: int) -> int:
    """Lowest unused rework number, asked of CAO; the ledger is a fallback."""
    from cao.launch import pick_attempt
    from cao.profiles import project_slug

    project = project_slug(Path(str(repo)).resolve().name)
    try:
        names = [s.get("name", "") for s in CaoClient(cfg.server).sessions()]
    except Exception:
        return fallback
    return pick_attempt(names, project, phase_id, start=2)


def cmd_verify(args) -> int:
    """Run the phase's own guard and act on the verdict.

    This is the only place a phase becomes "done". An agent's report is a claim;
    an exit code obtained by someone who was not asked to produce a good answer
    is evidence. Where they disagree, the exit code wins.
    """
    cfg = _cfg(args)
    state = Path(args.repo) / args.state_dir
    ledger = Ledger(state / "verify.ndjson")

    try:
        doc = plan_load(args.plan, cfg.max_depth)
    except (PlanError, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    graph = Path(str(Path(args.repo).resolve())).name
    worktree = args.worktree or f"{cfg.worktree_root}/{graph}-{args.phase}"

    verdict = verify_phase(doc, args.phase, worktree, wsl_distro=args.distro,
                           timeout=args.timeout)
    record(ledger, verdict)
    attempts = attempts_for(ledger, args.phase)

    print(f"phase:     {verdict.phase_id}")
    print(f"guard:     {verdict.guard}")
    print(f"exit_code: {verdict.exit_code}")
    print(f"verdict:   {verdict.status.upper()}")
    if verdict.output:
        print("output:")
        for line in verdict.output.splitlines()[-15:]:
            print(f"  {line}")

    # Did the agent claim this was done? A failure after an honest "blocked" is
    # ordinary; a failure after "status=ok" means the reporting cannot be trusted
    # on ANY phase, including the ones whose guards happened to pass.
    if args.terminal:
        try:
            finding, claim = compare_claim(
                verdict, CaoClient(cfg.server).terminal_output(args.terminal)
            )
        except Exception:
            finding, claim = None, None
        if finding == NO_CLAIM and not verdict.ok:
            # Measured live 2026-09-11: a supervisor's session was killed by a
            # Remote Control disconnect mid-delegation. CAO marked the terminal
            # "completed", the guard failed, and nothing said the agent had
            # never reported. Silence plus a failing guard is not dishonesty --
            # it is an agent that died, and the operator has to be told which.
            print(
                "\nNO REPORT: the guard failed and this terminal never emitted a "
                "RESULT envelope. The agent ended without reporting (killed, "
                "crashed, or still running) — this is not a false claim.",
                file=sys.stderr,
            )
        if finding == CONTRADICTED:
            print(
                "\nCONTRADICTION: the agent reported status=ok and the guard "
                "disagrees. Treat this agent's other reports as unverified too, "
                "not just this phase.",
                file=sys.stderr,
            )
            ledger.append({
                "ts": verdict.checked_at, "kind": "contradiction",
                "phase": args.phase, "claimed": "ok", "measured": verdict.status,
                "guard": verdict.guard,
            })

    if verdict.ok:
        # The lease is released only on real evidence, never on a claim.
        leases.release(state / "leases", args.phase)
        print("\nlease released: the guard passed, so the phase is genuinely done.")
        return 0

    max_attempts = int((cfg.resume or {}).get("maxAttempts", 3))
    if attempts >= max_attempts:
        print(
            f"\nNEEDS HUMAN: {attempts} rework attempts already spawned (cap {max_attempts}). "
            "Rework that never converges burns budget without approaching done; "
            "stopping here and leaving the lease held.",
            file=sys.stderr,
        )
        return 4

    print(f"\nrework needed (attempt {attempts + 1} of {max_attempts}).")
    print("brief for the next agent:")
    for line in rework_brief(verdict, attempts + 1).splitlines():
        print(f"  {line}")

    if not args.rework:
        print("\nre-run with --rework to spawn an agent to fix it.")
        return 1

    # Release first: the fixing agent must be able to claim the phase.
    leases.release(state / "leases", args.phase)
    try:
        result = plan_launch(
            cfg,
            args.plan,
            args.phase,
            lease_dir=state / "leases",
            is_open=lambda pool: pool_is_open(Ledger(state / "budget.ndjson"), pool),
            wsl_distro=args.distro,
            worktree=worktree,
            extra_brief=rework_brief(verdict, attempts + 1),
            # From the server's namespace, not a local counter: the two can
            # disagree, and only the server owns session names.
            attempt=_free_attempt(cfg, args.repo, args.phase, attempts + 2),
        )
    except LaunchRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    print(f"\nrespawning on {result.candidate.pool}/{result.candidate.model}...")
    # Recorded HERE, where an agent is actually spawned -- not on every
    # verdict. Counting failed verdicts made three inspections look like
    # three attempts and escalated a phase nobody had reworked once.
    record_rework(ledger, args.phase,
                  f"{result.candidate.pool}/{result.candidate.model}")
    completed = subprocess.run(result.argv, capture_output=True, text=True,
                               timeout=args.timeout)
    for line in ((completed.stdout or "") + (completed.stderr or "")).strip().splitlines()[-5:]:
        print(f"  {line}")
    return 1

def cmd_resume(args) -> int:
    """Restart work that stopped -- but only where nobody else continued it.

    Four conditions must hold (cao/leases.py). The load-bearing one is the live
    task list from CAO: a lease file can be stale after a crash, but the server
    knows which terminals are actually running. Trusting the file alone is how
    two agents end up editing the same worktree.
    """
    cfg = _cfg(args)
    state = Path(args.repo) / args.state_dir
    lease_dir = state / "leases"
    records = leases.all_leases(lease_dir)
    if not records:
        print("no leases: nothing was interrupted")
        return 0

    try:
        live = CaoClient(cfg.server).live_task_ids()
    except Exception as exc:
        # Refuse rather than assume nothing is running. An empty set would read
        # as "nobody is working" and license restarting live work.
        print(
            f"cannot reach CAO at {cfg.server}: {exc}\n"
            "Refusing to resume: without the live task list this cannot tell a "
            "dead agent from a working one.",
            file=sys.stderr,
        )
        return 2

    max_attempts = int((cfg.resume or {}).get("maxAttempts", 3))
    resumable, held = [], []
    for rec in records:
        if leases.may_resume(rec, live, max_attempts):
            resumable.append(rec)
        else:
            held.append(rec)

    print(f"{len(records)} lease(s); {len(resumable)} resumable")
    for rec in held:
        why = (
            "still alive per CAO" if rec.get("task_id") in live
            else "continued by " + str(rec.get("continued_by")) if rec.get("continued_by")
            else f"attempts exhausted ({rec.get('attempt')}/{max_attempts})"
            if int(rec.get("attempt", 1)) >= max_attempts
            else f"state={rec.get('state')}" if rec.get("state") not in leases.RESUMABLE
            else "lease still valid"
        )
        print(f"  HOLD   {rec.get('task_id')}: {why}")

    for rec in resumable:
        print(f"  RESUME {rec.get('task_id')} (attempt {int(rec.get('attempt', 1)) + 1})")

    if not args.apply:
        print("\nre-run with --apply to relaunch the resumable phases.")
        return 1 if resumable else 0

    for rec in resumable:
        phase = rec.get("task_id")
        leases.release(lease_dir, phase)
        try:
            result = plan_launch(
                cfg,
                args.plan,
                phase,
                lease_dir=lease_dir,
                is_open=lambda pool: pool_is_open(Ledger(state / "budget.ndjson"), pool),
                wsl_distro=args.distro,
                owner=f"resume-{int(rec.get('attempt', 1)) + 1}",
            )
        except LaunchRefused as exc:
            print(f"  REFUSED {phase}: {exc}", file=sys.stderr)
            continue
        completed = subprocess.run(
            result.argv, capture_output=True, text=True, timeout=args.timeout
        )
        blob = (completed.stdout or "") + (completed.stderr or "")
        hit = note_provider_output(
            Ledger(state / "budget.ndjson"), result.candidate.pool, blob
        )
        print(f"  relaunched {phase} on {result.candidate.pool}/{result.candidate.model}")
        if hit:
            # A resume that relaunches into a wall and says nothing is how a
            # run spends a night re-hitting the same closed pool.
            print(f"    WARNING: {result.candidate.pool} hit a {hit} wall on this relaunch")
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cao", description=__doc__)
    parser.add_argument("--repo", default=".", help="repo holding handoff.config.json")
    sub = parser.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("check", help="config sanity + pool credentials")
    p.add_argument("--state-dir", default="output/cao")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("probe", help="is each pool actually usable")
    p.add_argument("--state-dir", default="output/cao")
    p.add_argument("--distro", default=None, help="WSL distro (omit on Linux)")
    p.add_argument("--wsl-repo", default=None, help="repo path as seen from the distro")
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("profiles", help="generate per-level profiles")
    p.add_argument("--out", default="./cao-profiles")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("plan", help="scaffold a plan contract to fill in WITH the user")
    p.add_argument("--out", default="PLAN.lock.json")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("launch", help="launch a phase — refuses without an approved plan")
    p.add_argument("--plan", default="PLAN.lock.json")
    p.add_argument("--phase", required=True)
    p.add_argument("--state-dir", default="output/cao")
    p.add_argument("--distro", default=None, help="WSL distro (omit on Linux)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("resume", help="restart stopped work nobody else continued")
    p.add_argument("--plan", default="PLAN.lock.json")
    p.add_argument("--state-dir", default="output/cao")
    p.add_argument("--distro", default=None)
    p.add_argument("--apply", action="store_true", help="actually relaunch")
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("verify", help="run the phase guard yourself; rework on failure")
    p.add_argument("--plan", default="PLAN.lock.json")
    p.add_argument("--phase", required=True)
    p.add_argument("--state-dir", default="output/cao")
    p.add_argument("--worktree", default=None)
    p.add_argument("--distro", default=None)
    p.add_argument("--rework", action="store_true",
                   help="spawn an agent to fix it when the guard fails")
    p.add_argument("--terminal", default=None,
                   help="terminal id whose RESULT to check against the guard")
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("sweep", help="depth violations + agents waiting on input")
    p.add_argument("--enforce", action="store_true", help="act on depth violations")
    p.add_argument("--answer", action="store_true",
                   help="answer blocked agents whose prompt the config already implies")
    p.set_defaults(func=cmd_sweep)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigMissing, ValueError, json.JSONDecodeError) as exc:
        # A config problem is an operator problem: one actionable line beats a
        # traceback that buries it under nine frames of pathlib.
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
