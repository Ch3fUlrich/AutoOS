#!/usr/bin/env python3
"""Spawn one agent - any client, the right model, key, isolation and depth - one command.

The 3-tier agents live in opencode.jsonc (t1-orchestrator -> t2-worker ->
t3-reviewer, .agents/skills/unattended-orchestration/unattended-orchestration.md).
Driving them by hand has
four traps, all measured 2026-09-24 against opencode 2.0.16:

  1. `opencode run --agent t2-worker` runs on the TOP-LEVEL default model
     (omniroute/t1-orchestrator), not the agent's own - the agent/model pairing only
     holds for children spawned through the subagent tool. This tool always
     passes the agent's model explicitly.
  2. Without --standalone, `opencode run` talks to a background service that
     kept the environment it was started with, so a key exported afterwards
     (or a config overlay) is silently ignored. This tool always runs
     standalone, so the child sees exactly the environment given here.
  3. With stdin an open pipe (cron, CI, another agent's shell) `opencode
     run` waits to read it as extra prompt text and never starts; the child
     always gets /dev/null here.
  4. A worker edits the checkout it runs in. --isolate gives it a private
     `git clone --local` (from HEAD - uncommitted changes are not in it) on
     its own branch, its own opencode data dir, and a deny on every path
     outside the clone (opencode's default there is "ask", which --auto
     approves). NOT a git worktree: opencode resolves a worktree to the main
     checkout's root, and a relative write from inside one landed in the main
     repo (live, 2026-09-24). Nothing is merged or deleted for you.

Routing: without --tier the model comes from a task card. A v1 card
(role/complexity/ctx/spend, or empty) goes through autoos_routing.select_combo
(ADR 0006) - the one decision point, shared with the MCP server. An empty card
is t2-worker; `--card privacy=sensitive,ctx=1m` fails closed (the only
sensitive 1M leg is off, so --allow-training is accepted for
compatibility but inert). A v2 card (any of kind/risk/spec/mode/deferrable/deadline/
paths/override, spec 6.1 "run takes card v2") instead goes through the
resolver (route_plan_for/autoos_resolver.plan, the same core the `route`
subcommand uses): `state` input_required refuses with exit 2 and the plan's
reason; `deferred` refuses with exit 2 ("deferred until <time>: <reason>")
unless --no-defer. Either way the combo's tier picks the opencode agent (a v2
route id's own t1/t2/t3- prefix, when it has one, else tier 2). Every run logs
card -> combo, reason and the routing version.

Clients (autoos_clients.py; `list` prints the matrix): opencode (default),
claude (`claude -p` on its own login; --joinable = a `--bg --remote-control`
session), qwen / gemini / codex through `omniroute run <target>` on the card's
combo, agy and qoder on their own account login (no gateway; qoder is a promo
and takes privacy=public work only).

--lean: no serena/playwright/context7 for research and review agents - about
0.7 GB less per agent (LEAN_DROP). Implemented for the clients that can honour
it: opencode (config overlay) and claude / qoder (--strict-mcp-config,
LEAN_CLIENTS). Another client cannot drop its servers: on a read-only run --lean
is a note and the run goes ahead (the servers cost memory, not safety); on a
writer run it still refuses (rc 2), because there the tool surface is the point.

Depth: each child gets AUTOOS_AGENT_DEPTH (parent + 1) and
AUTOOS_AGENT_MAX_DEPTH (default 2, only ever lowered by --max-depth); a spawn
past the max is refused with exit code 4.

Usage:
    python3 tools/autoos-agent.py list
    python3 tools/autoos-agent.py run --card role=review "Review lib/linux/ui.sh"
    python3 tools/autoos-agent.py run --client qwen --card complexity=trivial "..."
    python3 tools/autoos-agent.py run --client claude --joinable --title d1 "..."
    python3 tools/autoos-agent.py run --tier 3 "Review lib/linux/ui.sh for quoting bugs"
    python3 tools/autoos-agent.py run --tier 2 --isolate "Add a test for X"
    python3 tools/autoos-agent.py run --isolate --read-only "Map every retry path in the spawner"
    python3 tools/autoos-agent.py run --card kind=research --isolate "Map every retry path"
    python3 tools/autoos-agent.py run --tier 3 --clean "..."       # no-training twin
    python3 tools/autoos-agent.py run --tier 2 --model omniroute/t2-orchestrator "..."
    python3 tools/autoos-agent.py run --tier 1 --free "..."        # no keys at all
    python3 tools/autoos-agent.py run --tier 3 --dry-run "..."     # print the plan only
    python3 tools/autoos-agent.py context                          # this session's fill
    python3 tools/autoos-agent.py context --transcript s.jsonl --json
    python3 tools/autoos-agent.py heartbeat --inbox i.md --transcript s.jsonl --json
    python3 tools/autoos-agent.py route --card kind=review,paths=tools/registry.py --explain

--free maps every tier agent to one of opencode's own free models (default
opencode/muse-spark-1.3-contributor-free) through OPENCODE_CONFIG_CONTENT: no
gateway, no key, no spend - for exercising the tier chain and the permission
fences. Free promo models may train on prompts, so --free refuses --clean. A
--free run that hits a provider stop re-runs the same task in the same sandbox
on the next model of policy.free_client_models in catalog/ai-registry.json
(SPAWNFREE item 1), and waits for a free slot before starting at all when the
provider's live workers already reach policy.free_concurrency (item 2).

Never prints a key. The OmniRoute client key comes from AUTOOS_OMNIROUTE_KEY
or the `omniroute:` line of configuration/api-keys.yml and is handed to the
child through its environment only. One line per run is appended to
logs/orch-<date>.log (git-ignored), which the watchdog protocol reads.

Exit codes: 5 = an --isolate implement run changed nothing (NO-OP); 6 = a headless client auto-denied a tool and
exited 0 (HEADLESS-REFUSAL); 7 = an --isolate run wrote to the parent checkout (LEAK: a commit authored or
committed by autoos-worker@users.noreply.github.com on a parent branch that existed at the start - HEAD range,
branch reflog (commit-then-reset) or moved side refs - or a tracked file outside logs/ left dirtier than
before - the one exemption being a ref that is another lane's own worktree branch at both ends of the run, and an
orchestrator that merged or fast-forwarded this parent mid-run is expected to see it: freeze the parent (skill
R-coord-01). The shas and paths are printed, nothing is reverted, the track record carries
failure class "containment"; LEAK 7 overrides ANY child rc, including 5, 6, 8, 10 and 11); 8 = a provider stop
(rate limit, 429, capacity, quota or billing) appeared in the last lines of the captured client
output while the client exited 0, 3 or 6 (PROVIDER-STOP; rc 3 is agy's own quota exit - AGYFIX
item 3, measured 2026-09-27; the track record carries failure class
"provider"; an --isolate run WIP-commits its uncommitted work first (a review run exempted - its
deliverable is its diff), so nothing is lost); 10 = an --isolate run that exited 0 having changed
nothing AND printed no REPORT heading (INCOMPLETE: the worker stopped mid-task, so there is no report
to disbelieve - relaunch it, never resume, skill R-orch-06; the track record carries failure class
"capability", like the NO-OP); 11 = an --isolate run marked --read-only (or a card whose kind/role is
research) that changed its sandbox anyway (READ-ONLY WRITE: changing nothing is that run's success, so an
edit is the failure - the diff stat is printed and the track record carries failure class "capability".
The same marked run that only reported is NOT a failure: it exits 0 with "RESEARCH: report only"); the child's
exit code; 2 bad arguments, card or route refused, or a --permission-mode/--approval-mode/--sandbox
value the client's own --help does not offer (CLIENT-MODE, SPAWNFREE item 3 - the message names the
mode and the client's accepted list);
3 gateway, key or client binary missing, OR AUTOOS_AGENT_INBOX names an inbox with an active
PAUSE (R-pause-01); 4 depth budget exhausted; 9 a --free run waited the whole bounded queue
(FREE_QUEUE_TIMEOUT_SECONDS) for a slot on its free provider and nothing started (SPAWNFREE item 2).

`heartbeat` (R-heartbeat-02/03, R-pause-01, R-handoff-07) is read-only - it never pushes,
commits or writes anything. Exit codes of its own: 3 an inbox PAUSE is active (takes
precedence over everything else), 4 the context fill is at or past its cap, 1 some repo has an
unpushed branch or a dirty working tree, 0 all clear.

`route` (spec 6.1) has its own exit codes: 0 a route was chosen (ready or
deferred), 5 input_required (no route survived the filters, or a removed
override), 2 bad input (a bad card, a bad --now, an unknown
--orchestrator-model, or any other measure()/plan() ValueError - fail closed,
message on stderr).
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
if os.name != "nt":
    import fcntl  # the free-leg provider lock (SPAWNFIX item 2); msvcrt on Windows

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autoos_clients as clients  # noqa: E402
import autoos_context as ctx  # noqa: E402
import autoos_heartbeat as heartbeat  # noqa: E402
import autoos_measure as measure_mod  # noqa: E402
import autoos_resolver as resolver  # noqa: E402
import autoos_routing as routing  # noqa: E402
import autoos_track as track  # noqa: E402
import autoos_usage as usage_mod  # noqa: E402
from registry import private_safe, resolve_leg, unavailable_now  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIERS = {1: "t1-orchestrator", 2: "t2-worker", 3: "t3-reviewer"}
GATEWAY = "http://127.0.0.1:20128"
DEFAULT_FREE_MODEL = "opencode/muse-spark-1.3-contributor-free"
# run_client re-emits a child's output as it arrives and keeps this much of it
# so cmd_run can spot a headless refusal that still exited 0 (bug 2).
TAIL_LIMIT = 64 * 1024
# Track record class per route family (spec §5.6). Provisional until the
# registry supplies route.class: t1 frontier, t2 cheap, t3 free.
TRACK_CLASS = {"t1": "frontier", "t2": "cheap", "t3": "free"}
TRACK_RECORD = os.path.join(ROOT, "logs", "routing", "track-record.jsonl")
# operator request 2026-09-26: propose a tool-calling re-probe whenever a real
# run's gate contradicts the recorded status of its route's legs.
REGISTRY_PATH = os.path.join(ROOT, "catalog", "ai-registry.json")
MEASURED_OVERLAY_PATH = os.path.join(ROOT, "logs", "routing", "measured.json")
# REVROUTE (S2) item 3: the spawner's own transient note about which provider
# just told it a reset time. Git-ignored beside measured.json - the registry is
# the operator's file, this one is the machine's observation, and a record whose
# window has passed stops mattering on its own (registry.unavailable_now).
PROVIDER_STATE_PATH = os.path.join(ROOT, "logs", "routing", "provider-state.json")
PROBE_PROPOSALS_LOG = os.path.join(ROOT, "logs", "routing", "probe-proposals.jsonl")
# `route`'s default orchestrator model (spec 6.1): a registry model id billed
# for verification cost when the caller does not pin one.
DEFAULT_ORCHESTRATOR_MODEL = "claude-opus-4-6"
# A v1 combo (t1-orchestrator, t2-worker, t3-driver, their -clean twins) always
# carries one of these prefixes. A resolver v2 route id (RUNV2) may or may not
# (e.g. "t1-orchestrator-free-only" does; "t4-rag" and "deepseek-v4.1-flash" do
# not - t4 is not even a TIERS key). _tier_for_route defaults to tier 2 when it
# does not: the resolver has already priced and picked the model that will
# actually run, so this only decides which local opencode agent identity
# (t1-orchestrator/t2-worker/t3-reviewer) spawns the client.
_TIER_PREFIX_RE = re.compile(r"^t([123])-")
# --lean drops these MCP servers. Measured 2026-09-24, one --free opencode run,
# peak process-tree RSS: 1406 MB with every server, 678 MB with these off
# (516 MB with graphify off too).
# The graph lookups stay: research and review agents navigate with them.
LEAN_DROP = ("serena", "playwright", "context7")
# Clients whose OWN argv accepts --strict-mcp-config (verified against each CLI's
# --help on 2026-09-27: qodercli 1.1.63 and claude both document "Only use MCP
# servers from --mcp-config"). The flag is not universal: qwen / gemini / codex
# run behind `omniroute run <target>`, where it would land in omniroute's own
# argument list and be rejected.
MCP_STRICT_CLIENTS = ("claude", "qoder")
# The clients --lean is actually implemented for: opencode through a config
# overlay, these two through --strict-mcp-config. SPAWNFREE (S2) item 4: for any
# other client --lean is a note on a read-only run and a refusal on a writer one,
# not a blanket error.
LEAN_CLIENTS = ("opencode",) + MCP_STRICT_CLIENTS

# --isolate containment (ISOfix, measured 2026-09-26): a worker given absolute
# parent paths edited and committed there; outside_fence only fenced opencode's
# file tools and sandbox_verdict only diffed the sandbox, so the spawner
# reported NO-OP (exit 5) instead of a leak.
WORKER_EMAIL = "autoos-worker@users.noreply.github.com"
ISOLATE_PUSH_DISABLED = "DISABLED-autoos-isolate"


def isolate_task_prefix(sandbox_path: str, root: str, read_only: bool = False) -> str:
    """The lines prepended to the task text of an --isolate run.

    SPAWNFIX (S3) item 2 (work/L1-routing/LEAKFP.out): a headless worker that
    reaches for approval stops there and exits 0, so the containment line is
    paired with the one fact the worker cannot probe: nobody is answering.

    Item 4 adds the third line a research run needs: an edit is not the
    deliverable, and a worker that is never told so will helpfully make one.
    """
    lines = ("Your working directory %s is your only writable checkout; "
             "never cd, git -C or write into %s or any other path outside it.\n"
             "The run is headless: nobody will answer questions or approve "
             "anything - decide, commit, and report."
             % (sandbox_path, root))
    if read_only:
        lines += ("\nThis run is read-only: its deliverable is its REPORT, and "
                  "changing nothing is success - do not edit, commit or "
                  "reorganise anything; read and report.")
    return lines


def load_jsonc(path: str) -> dict:
    # Same comment strip as both suites: whole-line // comments only.
    with io.open(path, encoding="utf-8") as fh:
        text = fh.read()
    return json.loads(re.sub(r"(?m)^\s*//.*$", "", text))


def declared_models(cfg: dict) -> set:
    out = set()
    for pid, prov in (cfg.get("providers") or {}).items():
        for mid in (prov.get("models") or {}):
            out.add("%s/%s" % (pid, mid))
    return out


def resolve_model(cfg: dict, tier: int, clean: bool, override: str | None) -> str:
    agent = TIERS[tier]
    model = override or cfg["agents"][agent]["model"]
    base, _, variant = model.partition("#")
    if clean and not base.endswith("-clean"):
        base += "-clean"
    if base not in declared_models(cfg):
        raise ValueError("%s is not declared in opencode.jsonc providers" % base)
    return base + ("#" + variant if variant else "")


def free_overlay(model: str) -> dict:
    return {"model": model, "agents": {a: {"model": model} for a in TIERS.values()}}


# SPAWNFREE (S2) item 1: the one home for the ordered free-model list is the
# registry's policy section (catalog/ai-registry.json), keyed by client. A
# --free run that hits a provider stop re-runs on the NEXT model of this list;
# the --free-model the caller gave goes first, so an explicit model is still
# what runs.
FREE_MODEL_POLICY_KEY = "free_client_models"


def free_model_chain(policy: dict | None, client: str, first: str) -> list:
    """The free models to try in order: `first`, then the client's registry
    list that `first` did not already name. A missing policy section (an old
    registry, a registry that failed to load) leaves the single given model -
    exactly today's one-shot --free run."""
    chain = [first]
    listed = ((policy or {}).get(FREE_MODEL_POLICY_KEY) or {}).get(client) or []
    for model in listed:
        if model and model not in chain:
            chain.append(model)
    return chain


def _free_fallthrough_plan(args, cfg: dict, plan: dict, chain: list | None,
                           tried: set) -> tuple:
    """The next --free attempt: the SAME task in the SAME sandbox on the next
    untried model of `chain`. Returns (plan, from, to), or (None, None, None)
    when the chain is spent or the re-plan is refused (privacy, depth, no
    route) - a refused re-plan ends the run at exit 8, never a silent retry.

    `tried` holds every free model this run has already burned; the model the
    stop just landed on joins it here, so no later attempt can return to it.
    """
    chain = list(chain or [plan["model"]])
    current = plan["model"]
    tried.add(current)
    at = chain.index(current) if current in chain else -1
    for model in chain[at + 1:]:
        if model in tried:
            continue
        given = args.free_model
        args.free_model = model
        try:
            next_plan = build_plan(args, cfg, sandbox=plan["sandbox"])
        except (clients.DepthError, RouteInputRequired, RouteDeferred, PrivacyRefused,
                ValueError):
            return None, None, None  # the guard says no: exit 8 below
        finally:
            args.free_model = given
        tried.add(model)
        return next_plan, current, model
    return None, None, None


# SPAWNFREE (S2) item 2: one free provider is one shared account, so N workers
# on it is N times the same rate limit (measured 2026-09-27: 3 Muse free workers
# started together on the L1-backlog lanes lstby2e, hx4m and rv3, and all three
# printed 'Rate limit exceeded'). A --free run therefore counts the host's live
# worker records that use the same free provider before it starts, and waits
# for a slot rather than starting into a 429. The cap is
# policy.free_concurrency in catalog/ai-registry.json; the wait is bounded, and
# a queue that outlasts the bound exits 9 having started nothing.
DEFAULT_FREE_CONCURRENCY = 1
FREE_QUEUE_POLL_SECONDS = 15
FREE_QUEUE_TIMEOUT_SECONDS = 20 * 60
EXIT_FREE_QUEUE_TIMEOUT = 9
# SPAWNFIX (S3) item 1: a worker that stops mid-thought exits rc 0 having printed
# neither a REPORT nor a commit (work/L1-routing/MUSEREG.try1.out quit after "Let
# me find the POST handler"). A NO-OP says "it reported, and the report is worth
# nothing"; this says "it never reported at all", and the next action is a
# relaunch (skill R-orch-06: never resume a no-change child).
EXIT_INCOMPLETE = 10
# SPAWNFIX (S3) item 4: a research run is graded on its report, because an edit
# was never the deliverable (work/L1-routing/R6RES.out and FOLD4MAP.out both
# ended "NO-OP exit 5" for a run whose job was to read and say something). A
# run marked read-only that changed its sandbox anyway is its own failure: the
# report exists, the instruction was not followed.
EXIT_READ_ONLY_WRITE = 11

# SPAWNFIX (S2 fix of SPAWNFREE) item 2: counting the live workers and starting
# are two steps, and the worker record — the thing the count reads — used to be
# written only after the clone. N spawners started together each counted the
# others as absent and all N started, which is the 429 storm the cap exists to
# prevent. So the count and the taking of a slot are now one critical section:
# an exclusive lock over FREE_SLOT_LOCK_NAME in the spawner state dir, held while
# a `.free-reservation` placeholder is written for this run. The placeholder is
# what the next lock holder counts, so it cannot be shown a slot that another
# spawner already claimed. It is not `.json`, so `ps` — which reads every `.json`
# in that dir as a worker — never lists it.
FREE_SLOT_LOCK_NAME = "free-slot.lock"
FREE_RESERVATION_SUFFIX = ".free-reservation"
FREE_LOCK_RETRY_SECONDS = 0.05
FREE_LOCK_GIVEUP_SECONDS = 60


def _free_lock_lock(fh) -> None:
    """Try once to take `fh`'s exclusive lock; raise OSError when it is held."""
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def free_slot_lock_take(directory: str):
    """Take the free-leg provider lock, retrying up to FREE_LOCK_GIVEUP_SECONDS.

    Returns the handle to hand back to free_slot_lock_give(), or None when the
    lock could not be taken at all. A lock that is unavailable (an unwritable
    state dir, a spawner wedged for a minute) degrades to today's best-effort
    count — it never refuses a run and never hangs one.
    """
    try:
        fd = os.open(os.path.join(directory, FREE_SLOT_LOCK_NAME),
                     os.O_RDWR | os.O_CREAT, 0o600)
        fh = os.fdopen(fd, "r+b")
    except OSError:
        return None
    deadline = time.time() + FREE_LOCK_GIVEUP_SECONDS
    while True:
        try:
            _free_lock_lock(fh)
            return fh
        except OSError:
            if time.time() >= deadline:
                try:
                    fh.close()
                except OSError:
                    pass
                return None
            time.sleep(FREE_LOCK_RETRY_SECONDS)


def free_slot_lock_give(fh) -> None:
    """Release a handle from free_slot_lock_take(); None (no lock taken) is fine."""
    if fh is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass


def _reservation_rows(directory: str, provider: str) -> tuple:
    """(live paths, stale paths) of the reservations in `directory` naming `provider`.

    A reservation is claimed by a live process or by none at all: the pid in it
    is gone (a killed spawner, a reboot) or its file cannot be read, so it is
    stale and reaped. A stale placeholder must never queue a run — the cost of
    dropping one is a 429, the cost of trusting it is a leg nothing can leave.
    """
    live, stale = [], []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return live, stale
    for name in names:
        if not name.endswith(FREE_RESERVATION_SUFFIX):
            continue
        path = os.path.join(directory, name)
        try:
            with io.open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            stale.append(path)
            continue
        if not isinstance(record, dict) or record.get("provider") != provider:
            continue
        try:
            alive = _worker_state(record) == "running"
        except Exception:  # noqa: BLE001 - an unjudgeable placeholder is a stale one
            alive = False
        (live if alive else stale).append(path)
    return live, stale


def live_free_reservations(directory: str, provider: str) -> int:
    """Reservations in `directory` for `provider` whose spawner is still alive.

    Reaps the stale ones on the way, so a run that died holding a slot leaves it
    rather than blocking the leg forever.
    """
    live, stale = _reservation_rows(directory, provider)
    for path in stale:
        try:
            os.remove(path)
        except OSError:
            pass
    return len(live)


def _reserve_free_slot(directory: str, provider: str, model: str):
    """Write this run's placeholder and return its path (None when it cannot be
    written — the run then proceeds unreserved, exactly as it used to)."""
    wid = "free-%d-%s" % (os.getpid(), os.urandom(3).hex())
    record = {"id": wid, "pid": os.getpid(), "pid_start": _proc_starttime(os.getpid()),
              "started": utc_now_iso(), "provider": provider, "model": model}
    path = os.path.join(directory, wid + FREE_RESERVATION_SUFFIX)
    try:
        _write_worker_record(path, record)
    except OSError:
        return None
    return path


def free_reservation_release(token) -> None:
    """Give up a slot taken by _reserve_free_slot(); None and an vanished file are
    both fine — a killed holder's placeholder is stale and reaped by the next
    count anyway."""
    if not token:
        return
    try:
        os.remove(token)
    except OSError:
        pass


def free_provider(model) -> str:
    """The provider prefix of a model id ('opencode/muse-...-free' -> 'opencode')."""
    return (model or "").partition("/")[0]


def free_concurrency_cap(policy: dict | None, provider: str) -> int:
    """policy.free_concurrency for `provider`; DEFAULT_FREE_CONCURRENCY (1) for
    an unlisted provider, a junk value or a cap below 1 (a 0 cap is a queue
    nothing can ever leave)."""
    cap = ((policy or {}).get("free_concurrency") or {}).get(provider)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        return DEFAULT_FREE_CONCURRENCY
    return cap


def live_free_workers(directory: str, provider: str) -> int:
    """Worker records in `directory` that are live and run a model of `provider`.

    `died` (gone, or a recycled pid) and `exited` records are not live; an
    unjudgeable record counts, because the cost of a false queue is a wait and
    the cost of missing one is a 429.
    """
    try:
        rows = list_workers(directory)
    except OSError:
        return 0
    return sum(1 for row in rows
               if not row["state"].startswith("exited") and row["state"] != "died"
               and free_provider(row["model"]) == provider)


def wait_for_free_slot(provider: str, cap: int, directory: str, printer=None,
                       sleep=None, now=None, poll_seconds: int = FREE_QUEUE_POLL_SECONDS,
                       deadline_seconds: int = FREE_QUEUE_TIMEOUT_SECONDS,
                       on_free=None) -> tuple:
    """Poll until fewer than `cap` workers of `provider` are live.

    Counts the live worker records *and* the live reservations (a spawner that
    claimed a slot but has not got its record down yet occupies it). Every
    count happens under the free-leg lock, and `on_free` — the caller's
    reservation — runs in the same critical section, so no two spawners are ever
    shown the same slot.

    Returns (live_count, timed_out). Each poll with the slot still taken prints
    the one line 'queued behind <n> <provider> workers', so a log says why a
    spawn took 20 minutes. Never raises on a registry problem: an unreadable
    directory reads as 0 live workers (start, and let the client fail loudly).
    """
    printer = printer or (lambda line: print(line, file=sys.stderr))
    sleep = sleep or time.sleep
    now = now or time.time
    limit = now() + deadline_seconds
    while True:
        lock = free_slot_lock_take(directory)
        try:
            live = (live_free_workers(directory, provider)
                    + live_free_reservations(directory, provider))
            slot = live < cap
            if slot and on_free is not None:
                on_free()
        finally:
            free_slot_lock_give(lock)
        if slot:
            return live, False
        printer("queued behind %d %s workers" % (live, provider))
        if now() + poll_seconds > limit:
            return live, True
        sleep(poll_seconds)


def free_slot_refusal(plan: dict, policy: dict | None, directory: str | None = None,
                      printer=None, sleep=None, now=None,
                      poll_seconds: int = FREE_QUEUE_POLL_SECONDS,
                      deadline_seconds: int = FREE_QUEUE_TIMEOUT_SECONDS) -> tuple:
    """Wait for a free slot for `plan`'s model and claim it.

    Returns (None, reservation-token) when the run may start, and
    (refusal-message, None) when the bounded wait ran out. The token keeps the
    slot claimed from this count until the run's own worker record exists;
    release it with free_reservation_release() once that record is down (or the
    run is over)."""
    provider = free_provider(plan["model"])
    cap = free_concurrency_cap(policy, provider)
    directory = directory or workers_dir()
    claimed = []

    def reserve():
        claimed.append(_reserve_free_slot(directory, provider, plan["model"]))

    live, timed_out = wait_for_free_slot(provider, cap, directory, printer=printer,
                                         sleep=sleep, now=now,
                                         poll_seconds=poll_seconds,
                                         deadline_seconds=deadline_seconds,
                                         on_free=reserve)
    if not timed_out:
        return None, (claimed[0] if claimed else None)
    return ("the %s free leg is saturated: %d worker(s) of the same provider were "
            "live for %d min (cap %d per provider, policy.free_concurrency). Nothing "
            "started - retry later, run without --free, or raise the cap if the "
            "provider says it serves more at once."
            % (provider, live, FREE_QUEUE_TIMEOUT_SECONDS // 60, cap), None)


def lean_decision(client_name: str, route: dict) -> tuple:
    """(note, refusal) for `--lean` on `client_name` given a planned `route`.

    SPAWNFREE (S2) item 4: --lean on a client that cannot drop its MCP servers
    used to be a flat refusal, which killed the role=review qoder runs the skill
    briefs lean (inbox 2026-09-27T17:42:09Z,
    work/L2-general/review-edgesecret-brief.out). A read-only run pays those
    servers in RAM only, so note it and continue; on a writer run the servers'
    write-capable tool surface is the documented reason --lean is asked for, so
    a client that cannot provide it still refuses. Both are None when the client
    is in LEAN_CLIENTS, where --lean is honoured outright."""
    if client_name in LEAN_CLIENTS:
        return None, None
    if route.get("review"):
        return "%s cannot drop its MCP servers" % client_name, None
    return None, ("--lean cannot be honoured for %s: it would still start its MCP "
                  "servers, and a writer run is where that tool surface is exactly "
                  "what --lean asks to remove (%s can drop them)." % (
                      client_name, " and ".join(LEAN_CLIENTS)))


def lean_overlay(cfg: dict) -> dict:
    # opencode 2.x disables a server with `disabled: true` on a FULL entry:
    # `enabled` is not a v2 field (stripped without a warning, the server
    # still starts), and an entry without type/command is dropped as
    # malformed. The overlay document is merged last, so it wins per server.
    servers = (cfg.get("mcp") or {}).get("servers") or {}
    return {"mcp": {"servers": {n: dict(servers[n], disabled=True)
                                for n in LEAN_DROP if n in servers}}}


def outside_fence(data_dir: str, task_dir: str | None = None) -> list:
    # opencode's default for paths outside the project is "ask", and --auto
    # approves every ask - live 2026-09-24 an isolated worker wrote to the
    # main checkout by absolute path. Deny outside paths, then re-allow the
    # scratch dirs opencode itself pages large tool output into.
    home = os.path.expanduser("~")
    allow = [os.path.join(home, ".local", "share", "opencode", "tool-output", "*"),
             os.path.join(home, ".local", "share", "opencode", "shell", "*", "*"),
             "/tmp/opencode/*"]
    allow.append(os.path.join(data_dir, "opencode", "*"))
    if task_dir is not None:
        # FENCE (L1-backlog 2026-09-27): the MCP run_job sets AUTOOS_TASK_DIR
        # to <ROOT>/logs/agents/<run id>, and a blocked worker's
        # tools/autoos-ask.py writes question.json there. Under --isolate the
        # run dir is outside the sandbox clone, so the deny above refused the
        # write (autoos-ask exit 5). Re-allow exactly that dir - only when
        # its realpath stays under logs/agents; anything else (an escape via
        # .. or a symlink, a relative path, a missing dir) adds no rule and
        # warns once, it never refuses the run. The allow entries above set
        # the form: "<dir>/*" matches the files written inside the dir, so
        # the bare dir itself needs no entry.
        task_real = os.path.realpath(task_dir)
        agents = os.path.realpath(os.path.join(ROOT, "logs", "agents"))
        if (os.path.isabs(task_dir) and os.path.isdir(task_real)
                and task_real.startswith(agents + os.sep)):
            allow.append(os.path.join(task_real, "*"))
        else:
            print("AUTOOS_TASK_DIR %s is not an existing run dir under %s - "
                  "the --isolate fence keeps it denied (autoos-ask exits 5)"
                  % (task_dir, agents), file=sys.stderr)
    return ([{"action": "external_directory", "resource": "*", "effect": "deny"}] +
            [{"action": "external_directory", "resource": p, "effect": "allow"} for p in allow])


def key_files(root: str) -> list:
    """This checkout's api-keys.yml, then the main checkout's.

    The file is git-ignored, so a lane worktree has none (measured 2026-09-25:
    exit 3 "No OmniRoute client key", and linking it in was refused).
    """
    roots = [root]
    r = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        main = os.path.dirname(r.stdout.strip())
        if os.path.realpath(main) != os.path.realpath(root):
            roots.append(main)
    return [os.path.join(r_, "configuration", "api-keys.yml") for r_ in roots]


def client_key(root: str) -> str | None:
    key = os.environ.get("AUTOOS_OMNIROUTE_KEY")
    if key:
        return key
    for path in key_files(root):
        if not os.path.isfile(path):
            continue
        for line in io.open(path, encoding="utf-8"):
            m = re.match(r"^omniroute\s*:\s*(.+?)\s*$", line)
            if m:
                val = m.group(1).strip("\"'")
                return None if val.startswith("REPLACE_WITH_") else val
    return None


def gateway_up() -> bool:
    try:
        with urllib.request.urlopen(GATEWAY + "/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "task"


SESSION_TAG_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
SESSION_TAG_HEADER = "x-omniroute-session-id"


def session_tag(title: str, env=None) -> str:
    """The lane tag stamped on every spawned gateway request (OR3).

    OmniRoute fills call_logs.session_tag from the request header
    `x-omniroute-session-id` (chatCore.ts explicitSessionIdHeader wins over
    the conversation id). Env AUTOOS_SESSION_TAG wins when it is a valid tag
    ([A-Za-z0-9._/-]{1,120}); anything else warns once and falls back to
    "<lane worktree basename>/<slugified title>".
    """
    env = os.environ if env is None else env
    raw = env.get("AUTOOS_SESSION_TAG")
    if raw:
        if SESSION_TAG_RE.match(raw):
            return raw
        print("autoos-agent: AUTOOS_SESSION_TAG %r is not [A-Za-z0-9._/-]{1,120} - "
              "falling back to <lane>/<title>" % raw, file=sys.stderr)
    # The worktree name is not ours to trust: keep the header charset and
    # leave room for "/<slug>" (slugify caps it at 40) inside 120 chars.
    lane = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(ROOT)).strip("-")[:79] or "lane"
    return "%s/%s" % (lane, slugify(title))


def unique_suffix() -> str:
    """Six random hex chars that keep a sandbox name and branch unique.

    Measured 2026-09-25: two spawns in the same second whose tasks started with
    the same words named the same --isolate clone, and the second `git clone
    --local` died with "fatal: destination path ... already exists" (rc 1).
    """
    return os.urandom(3).hex()


def _tier_for_route(combo: str) -> int:
    """The opencode tier agent for a resolver v2 route id (RUNV2, spec 6.1).

    See the comment on _TIER_PREFIX_RE for why an unrecognised prefix defaults
    to tier 2 rather than raising.
    """
    match = _TIER_PREFIX_RE.match(combo or "")
    return int(match.group(1)) if match else 2


def _is_v2_card(parsed: dict) -> bool:
    """True when `parsed` (routing.parse_card's own output, not yet validated
    or defaulted) carries any card-v2-only field (spec 4): kind, risk, spec,
    mode, deferrable, deadline, paths, override (spelled either as one
    ``override`` object/JSON value or as ``override.route=``/``.client=``/
    ``.effort=`` key=value pairs). An empty card, or one with only v1 and/or
    the shared ``privacy`` field, is not v2 - it keeps today's select_combo
    path unchanged (spec 4: "v1 cards stay valid").
    """
    for key in parsed:
        if key in routing.CARD_V2_ONLY:
            return True
        if isinstance(key, str) and key.startswith("override."):
            return True
    return False


# SPAWNCAP (S2). A client advertises shell/write in the registry
# (clients.<id>.capabilities); a task that needs one is refused before dispatch
# rather than started on a client that cannot do it, and auto-choice skips such
# a client. The only explicit card kinds/roles that ask for shell+write: a v2
# kind of implement/debug/bulk, or a v1 role=implement.
CAPABILITY_EDITING_KINDS = frozenset({"implement", "debug", "bulk"})


def client_capabilities(name: str, registry: dict | None = None) -> dict:
    """One client's declared {shell, write}; a missing entry reads as both False."""
    if registry is None:
        registry = load_registry(REGISTRY_PATH)
    declared = ((registry.get("clients") or {}).get(name) or {}).get("capabilities") or {}
    return {"shell": bool(declared.get("shell")), "write": bool(declared.get("write"))}


def _parsed_card_fields(text) -> dict | None:
    """routing.parse_card's output for `text`, or None for an empty/malformed one.

    A malformed card is build_plan's to report (its own CardError message, with
    the existing "(see: ... list)" suffix); the capability gate must not shadow it.
    """
    try:
        parsed = routing.parse_card(text or "")
    except routing.CardError:
        return None
    return parsed or None


def required_capabilities(args) -> tuple:
    """The shell/write capabilities this run's task needs, in a fixed order.

    `--isolate` means the worker writes to its clone and uses git, so both are
    needed. Otherwise only an EXPLICIT editing card is a write request: a v2
    `kind` of implement/debug/bulk, or a v1 `role=implement`. An absent, empty,
    malformed, read-only (review/research/plan) or defaults-only card (e.g.
    `privacy=sensitive` alone) asks for nothing - being explicit is what makes a
    run a write run.
    """
    if getattr(args, "isolate", False):
        return ("shell", "write")
    parsed = _parsed_card_fields(getattr(args, "card", None))
    if not parsed:
        return ()
    if _is_v2_card(parsed):
        if parsed.get("kind") in CAPABILITY_EDITING_KINDS:
            return ("shell", "write")
        return ()
    if parsed.get("role") == "implement":
        return ("shell", "write")
    return ()


def _client_order() -> list:
    """The preference order auto-choice walks: opencode first, then
    autoos_clients.CLIENTS' own order."""
    return ["opencode"] + [name for name in clients.CLIENTS if name != "opencode"]


def choose_client(required: tuple, registry: dict | None = None) -> str | None:
    """The first client declaring every capability in `required`, or None.

    With no requirement this is `opencode` - exactly today's default.
    """
    if not required:
        return "opencode"
    for name in _client_order():
        caps = client_capabilities(name, registry)
        if all(caps.get(cap) for cap in required):
            return name
    return None


def capability_refusal(name: str, required: tuple, registry: dict | None = None) -> str | None:
    """Why `name` cannot take this run, or None when it declares every capability.

    Names the missing capability and the clients that do have it, so the
    operator can reroute without guessing.
    """
    caps = client_capabilities(name, registry)
    missing = [cap for cap in required if not caps.get(cap)]
    if not missing:
        return None
    capable = [other for other in _client_order()
               if all(client_capabilities(other, registry).get(cap) for cap in missing)]
    return ("client %s cannot run this task: it lacks %s (declares shell=%s, write=%s). "
            "Clients with %s: %s" % (
                name, " and ".join(missing), str(caps["shell"]).lower(), str(caps["write"]).lower(),
                " and ".join(missing), ", ".join(capable) or "none"))


class RouteInputRequired(ValueError):
    """A v2 card's resolver plan is input_required: no route survives the filters."""


class RouteDeferred(ValueError):
    """A v2 card's resolver plan is deferred and --no-defer was not given."""


def resolve_review_plan(card: dict, now=None) -> dict | None:
    """The ``policy.reviewers`` decision for an authored review card.

    None unless the card both asks for a review and names who wrote the diff --
    the different-family rule needs an author, and an unauthored review is the
    orchestrator's own business (the spec 5.7 reviewer *routes* still apply).
    Both card shapes count: v2 says ``kind=review``, v1 says ``role=review``,
    and ``author`` is shared (autoos_routing.CARD_SHARED). Reads the same
    registry, client probes and clock the route plan reads, so `route` and `run`
    cannot disagree about who reviews.
    """
    if not _card_asks_review(card) or not card.get("author"):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return resolver.reviewer_for(
        card["author"], load_live_registry(),
        measure_mod.client_state(clients), now,
        risk=card.get("risk", "normal"), privacy=card.get("privacy", "public"))


def _card_asks_review(card: dict) -> bool:
    """True when a normalized card asks for a review, v1 or v2 spelling."""
    return card.get("kind") == "review" or card.get("role") == "review"


def read_only_run(args, card) -> bool:
    """True when this run's success is an unchanged sandbox (SPAWNFIX3 item 4).

    Two spellings, one fact: `run --read-only` says it out loud, and a research
    card (v2 ``kind=research``, v1 ``role=research``) asks for it by name. The
    verdict that reads this mark is what turned R6RES and FOLD4MAP into a NO-OP:
    a run that reads code and reports is not a run that failed to edit.

    ``args`` is read with a default because the route helpers are shared with
    subcommands that carry no run flags at all (``route``, and the hand-built
    namespaces the tests plan with) - the same convention `args.isolate` uses.
    """
    return bool(getattr(args, "read_only", False)) or (
        (card or {}).get("kind") == "research" or (card or {}).get("role") == "research")


def review_run_refusal(review: dict | None):
    """Why an authored review run must not start yet, or None when it may.

    ``queued`` reuses SPAWNFREE's exit 9: every reviewer that could take this
    card is down with a known reset, so the right answer is to wait and re-spawn
    (the same rc the caller's queue loop already understands), not to run the
    review on a same-family model and call it independent. ``unresolved`` is the
    opposite -- nobody is eligible and no wait changes that (a signed-out client
    needs a human) -- so it is a plain exit-2 refusal, never a retry the caller
    would have to bound.

    Either way the skipped reviewers are printed first, one line each: "who is
    blocked and why" is the question a refusal gets asked, and the answer is
    already in the walk.
    """
    if not review:
        return None
    if review["state"] not in ("queued", "unresolved"):
        return None
    for line in resolver.reviewer_explain_lines(review):
        print(line, file=sys.stderr)
    if review["state"] == "queued":
        return refuse("reviewer %s" % review["reason"], EXIT_FREE_QUEUE_TIMEOUT)
    return refuse("reviewer unavailable: %s" % review["reason"])


# REVROUTE (S2) item 5: what a lane record must SAY before the lane is ready.
# A line, not a section: the record is free-form markdown a person writes, so
# the gate looks for one machine-readable line per review and ignores the prose
# around it. `review-status` prints the same hint it parses, and `run` prints a
# paste-ready one, so the format is never something you have to go looking for.
REVIEW_ENTRY_RE = re.compile(r"^\s*(?:[#>*-]+\s*)?AutoOS-Review:\s*(?P<body>.+)$")
REVIEW_ENTRY_FIELDS = ("kind", "author", "reviewer", "verdict")
READY_VERDICTS = frozenset(("ready", "pass", "passed", "approve", "approved", "lgtm",
                            # SPAWNFIX3 (S3) item 5 (REVGATE.record.md): a Sonnet
                            # final signs its lanes "SHIP"; "fix-first" is the same
                            # vocabulary's OPEN finding and stays refused.
                            "ship"))
# The final check is the operator's unchanged decision (Q-003 2026-09-27): a
# cross-family model reads the diff, Sonnet signs it off. Sonnet is not a
# registry route -- it is the orchestrator's own interactive model -- so this one
# matches the NAME, while the cross-family half is decided by registry families.
FINAL_REVIEWER = "sonnet"
# REVGATE2: the final checker is recognized by its NAME, never as a substring of
# one -- "notsonnet" contains "sonnet" and is not a sign-off. The vendor's full
# model id (claude-sonnet-5, claude-sonnet-4-6) is the same checker spelled by
# the client, so it counts too; anything else does not.
FINAL_REVIEWER_RE = re.compile(r"^claude-sonnet-[0-9][0-9a-z.-]*$")
REVIEW_ENTRY_HINT = ("AutoOS-Review: kind=cross-family author=<model> "
                     "reviewer=<model> verdict=<ready|pass|ship|lgtm|...>")


def _family_of_one_spelling(name, registry):
    """The family the registry declares for exactly one reviewer spelling, or None."""
    key = resolver.family_key(name)
    for entry in ((registry.get("policy") or {}).get("reviewers") or []):
        if isinstance(entry, dict) and resolver.family_key(entry.get("model")) == key:
            return resolver.family_key(entry.get("family"))
    models = registry.get("models") or {}
    for candidate in (key, key.rpartition("/")[2]):
        entry = resolver.ci_value(models, candidate)
        if isinstance(entry, dict):
            return resolver.family_key(entry.get("family"))
    return None


# A client reports its own models by full id, so the Claude pass that policy
# .reviewers spells "haiku" signs "claude-haiku-4-5" (SPAWNFIX3 (S3) item 5,
# work/L1-routing/LEAKFP2.record.md). The vendor's prefix and the version tail
# are not a different reviewer; the name between them is what the registry may
# know. Same tail shape as FINAL_REVIEWER_RE.
_CLAUDE_MODEL_ID_RE = re.compile(r"^claude-([a-z]+)(?:-[0-9][0-9a-z.-]*)?$")


def reviewer_family(spelling, registry):
    """The model FAMILY of a reviewer named in a lane record, or None.

    ``policy.reviewers`` first: its ``model`` column holds exactly the spelling a
    spawn used (``omniroute/spark-1.3-contributor``), and registry check rule 11
    keeps its ``family`` honest against ``models``. Then the registry's own model
    ids, whole and after a client/provider prefix; then that spelling again with a
    client's own model-id prefix and version tail removed, which is how a finished
    pass reports the reviewer the operator named ("claude-haiku-4-5" == "haiku").
    The answer is in ``resolver.family_key`` form, so a record's "Meta" and the
    registry's "meta" are one family wherever it is compared (REVFIX S2).

    Unknown returns None rather than a guess. An invented *reviewer* name would
    differ from every author family and read as an independent review that never
    happened -- which is also why ``author_family`` no longer guesses at an
    unknown *author* (REVFIX S2: both halves must be known before they may
    disagree).
    """
    if not isinstance(spelling, str) or not spelling.strip():
        return None
    key = spelling.strip()
    family = _family_of_one_spelling(key, registry)
    if family is not None:
        return family
    bare = _CLAUDE_MODEL_ID_RE.match(resolver.family_key(key) or "")
    if bare:
        return _family_of_one_spelling(bare.group(1), registry)
    return None


def is_final_reviewer(spelling):
    """True when ``spelling`` NAMES the final checker, stripped and lower-cased.

    One helper owns the answer so the gate and any future caller agree on what
    "Sonnet signed this off" means. A substring is not a name: matching
    ``FINAL_REVIEWER in reviewer`` let ``notsonnet`` pass (REVGATE2, HIGH)."""
    name = (spelling or "").strip().lower()
    return name == FINAL_REVIEWER or bool(FINAL_REVIEWER_RE.match(name))


def _review_entry_verdict(entry):
    """``(ok, reason)`` for one entry's verdict field."""
    verdict = (entry.get("verdict") or "").strip()
    if verdict.lower() in READY_VERDICTS:
        return True, None
    return False, "verdict %s" % (verdict or "missing")


def _cross_family_review(entries, registry):
    """The record's independent review: a reviewer from a DIFFERENT family."""
    wanted = [e for e in entries if e.get("kind") == "cross-family"]
    if not wanted:
        return {"ok": False, "family": None,
                "detail": "no AutoOS-Review: kind=cross-family entry"}
    reasons = []
    for entry in wanted:
        reviewer = entry["reviewer"]
        family = reviewer_family(reviewer, registry)
        if family is None:
            reasons.append("%s is not a known reviewer (policy.reviewers or models)"
                           % reviewer)
            continue
        author, why = resolver.author_family(entry.get("author") or "", registry)
        if author is None:
            # REVFIX S2: an author the registry cannot place is NOT treated as a
            # family of its own. "Who wrote this" unanswered is not evidence that
            # the reviewer is someone else, so the check fails and says so.
            reasons.append("author: %s" % why)
            continue
        if author == resolver.family_key(family):
            reasons.append("%s is the same family as the author (%s)" % (reviewer, family))
            continue
        ok, reason = _review_entry_verdict(entry)
        if not ok:
            reasons.append("%s %s" % (reviewer, reason))
            continue
        return {"ok": True, "family": family,
                "detail": "%s reviewed by %s (%s)" % (entry.get("author"), reviewer, family)}
    return {"ok": False, "family": None, "detail": "; ".join(reasons)}


def _final_review(entries):
    """The record's sign-off: a kind=final entry naming the final checker."""
    wanted = [e for e in entries if e.get("kind") == "final"]
    if not wanted:
        return {"ok": False,
                "detail": "no AutoOS-Review: kind=final entry naming %s" % FINAL_REVIEWER}
    named = [e for e in wanted if is_final_reviewer(e["reviewer"])]
    if not named:
        return {"ok": False,
                "detail": "the final entries name %s, not %s"
                          % (", ".join(sorted(e["reviewer"] for e in wanted)), FINAL_REVIEWER)}
    reasons = []
    for entry in named:
        ok, reason = _review_entry_verdict(entry)
        if ok:
            return {"ok": True,
                    "detail": "%s verdict %s" % (entry["reviewer"], entry.get("verdict"))}
        reasons.append("%s %s" % (entry["reviewer"], reason))
    return {"ok": False, "detail": "; ".join(reasons)}


def review_status(text, registry):
    """Which of the two reviews a lane record carries, read off the record itself.

    An item 2 spawn has already proved a reviewer EXISTS for this card; this is
    the other half -- proof it RAN and said something, in the file that gets
    merged. A same-family reviewer, an unknown reviewer spelling and a verdict
    that says FIX-FIRST all fail, and each says which, because "missing" would
    send someone to book a review that already happened and did not pass.
    """
    entries, malformed = [], []
    for line in (text or "").splitlines():
        match = REVIEW_ENTRY_RE.match(line)
        if not match:
            continue
        fields = {}
        for token in match.group("body").split():
            key, _sep, value = token.partition("=")
            if value and key.lower() in REVIEW_ENTRY_FIELDS:
                fields[key.lower()] = value
        if fields.get("kind") and fields.get("reviewer"):
            entries.append(fields)
        else:
            malformed.append(match.group("body").strip())
    cross = _cross_family_review(entries, registry)
    final = _final_review(entries)
    return {"entries": len(entries), "malformed": malformed,
            "cross_family": cross, "final": final,
            "ready": cross["ok"] and final["ok"],
            "hint": REVIEW_ENTRY_HINT}


def read_lane_record(path):
    """A lane record's text and the label to name it with; `-` is stdin.

    OSError (a typo'd path) is the caller's to report: rc 2, not rc 1 -- "the
    gate could not run" is a different next action from "not ready yet".
    """
    if path == "-":
        return sys.stdin.read(), "<stdin>"
    with io.open(path, encoding="utf-8") as fh:
        return fh.read(), path


def print_review_report(label, report):
    """The review-status report, shared by `review-status` and `ready`.

    One owner on purpose: the line an orchestrator reads to decide what to do
    next must not drift between the gate that tells it to go and the gate that
    refuses to write the claim down.
    """
    print("review-status: %s -- %d review entr%s"
          % (label, report["entries"], "y" if report["entries"] == 1 else "ies"))
    for key, name in (("cross_family", "cross-family"), ("final", "final (%s)" % FINAL_REVIEWER)):
        item = report[key]
        print("  %-16s %s: %s" % (name, "ok" if item["ok"] else "NOT READY", item["detail"]))
    for line in report["malformed"]:
        print("  note: entry without a kind= or reviewer= ignored: %s" % line)
    if not report["entries"]:
        print("  note: write one line per review, e.g.: %s" % report["hint"])
    print("ready: %s" % ("yes" if report["ready"] else "no"))


def cmd_review_status(args) -> int:
    """Report whether a lane record carries both reviews a ready lane needs.

    Exit 0 ready, 1 a review is missing or still open, 2 the record could not be
    read -- a typo'd path is not a lane that needs reviewing, and a caller that
    waits on 1 would wait forever on that mistake.
    """
    try:
        text, label = read_lane_record(args.record)
    except OSError as exc:
        print("review-status: %s" % exc, file=sys.stderr)
        return 2
    registry = load_registry(args.registry or REGISTRY_PATH)
    report = review_status(text, registry)
    print_review_report(label, report)
    return 0 if report["ready"] else 1


def remote_branch_tip(repo, branch):
    """``(sha, error)`` -- what ``origin`` says ``refs/heads/<branch>`` points at.

    ``(None, None)`` means the remote ANSWERED and the branch simply is not there:
    the work lives only in someone's checkout, which is exactly what a `ready`
    line must not claim. A non-None ``error`` is git failing to answer at all (no
    ``origin`` remote, not a checkout, an unreachable host) -- "the gate could not
    run", which is a different next action and a different exit code.

    The timeout is not decoration: the real origin is a network remote, and a
    gate that hangs takes the lane's session with it.
    """
    ref = "refs/heads/%s" % branch
    try:
        proc = subprocess.run(["git", "-C", repo, "ls-remote", "origin", ref],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "%s" % exc
    if proc.returncode != 0:
        return None, ((proc.stderr or proc.stdout or "").strip()
                      or "git ls-remote exited %d" % proc.returncode)
    for line in proc.stdout.splitlines():
        sha, _sep, name = line.partition("\t")
        if name.strip() == ref and sha.strip():
            return sha.strip(), None
    return None, None


def append_inbox_line(path, line):
    """Append exactly one line to a controller's inbox, creating it if missing.

    Append-only because the inbox is a log: it holds orders other agents already
    acted on, and rewriting it to add a line deletes that history. A file whose
    last byte is not a newline is terminated first, so the new line is never
    glued to the old one.
    """
    needs_newline = False
    if os.path.isfile(path) and os.path.getsize(path):
        with io.open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            needs_newline = fh.read(1) not in (b"\n", b"\r")
    with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
        if needs_newline:
            fh.write("\n")
        fh.write(line + "\n")


def ci_run_status(run_id, runner=None):
    """``(conclusion, head_sha, error)`` — what one CI run says about its own commit.

    ``gh run view --json conclusion,headSha``, through an injectable runner:
    SPAWNFIX3 item 6, because a test cannot ask GitHub about a commit that only
    exists under ``/tmp``, and the real call has exactly one place to live.
    Mirrors `remote_branch_tip`: a non-None ``error`` means the question was
    never answered (gh missing, gh refusing, output that is not the JSON asked
    for), which is a different next action and a different exit code from a run
    that came back red. The timeout is not decoration — `ready` runs inside a
    lane's session, and a gate that hangs takes that session with it.
    """
    runner = runner or subprocess.run
    argv = ["gh", "run", "view", str(run_id), "--json", "conclusion,headSha"]
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "%s" % exc
    if proc.returncode != 0:
        return None, None, ((proc.stderr or proc.stdout or "").strip()
                            or "gh run view exited %d" % proc.returncode)
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        return None, None, "gh run view printed output that is not JSON: %s" % exc
    head_sha = data.get("headSha")
    if not head_sha:
        return None, None, ("gh run view answered with no headSha (conclusion %r)"
                            % data.get("conclusion"))
    return data.get("conclusion"), head_sha, None


def cmd_ready(args) -> int:
    """Write the `ready` line an orchestrator used to type by hand.

    Four gates, in this order, each naming itself when it fails: the record
    carries both reviews (``review_status``), ``--sha`` is what ``origin`` holds
    for ``--branch``, and — when ``--ci-run`` names one — that GitHub Actions run
    finished ``success`` with ``headSha`` equal to ``--sha``, so the line cannot
    certify a commit the gate never tested (SPAWNFIX3 item 6; the run id rides
    on the line as ``ci=<id>``). Without ``--ci-run`` the lane is still allowed
    and one note says the gate was skipped. The gates live in code because the
    hand-written claim was wrong once -- L1-main refused a `ready` line whose
    record had no reviews (inbox 00:31:52Z).

    Exit 0 the line was written (or, with --dry-run, would be), 1 a gate is not
    met, 2 a gate could not be read (unreadable record, git or gh failure,
    unwritable inbox)."""
    try:
        text, label = read_lane_record(args.record)
    except OSError as exc:
        print("ready: %s" % exc, file=sys.stderr)
        return 2
    registry = load_registry(args.registry or REGISTRY_PATH)
    report = review_status(text, registry)
    print_review_report(label, report)
    if not report["ready"]:
        print("ready: not appended -- the record does not carry both reviews")
        return 1
    tip, git_error = remote_branch_tip(args.repo or os.getcwd(), args.branch)
    if git_error:
        print("ready: cannot read origin/%s: %s" % (args.branch, git_error), file=sys.stderr)
        return 2
    if tip is None:
        print("ready: not pushed -- origin has no refs/heads/%s; push the lane "
              "before declaring it ready" % args.branch)
        return 1
    if tip != args.sha:
        print("ready: not pushed -- %s is at %s on origin, not %s"
              % (args.branch, tip, args.sha))
        return 1
    ci_field = ""
    if getattr(args, "ci_run", None):
        conclusion, head_sha, ci_error = ci_run_status(args.ci_run)
        if ci_error:
            print("ready: cannot read CI run %s: %s" % (args.ci_run, ci_error),
                  file=sys.stderr)
            return 2
        if conclusion != "success":
            print("ready: not appended -- CI run %s is %s, not success"
                  % (args.ci_run, conclusion or "still running"))
            return 1
        if head_sha != args.sha:
            print("ready: not appended -- CI run %s tested %s, not %s"
                  % (args.ci_run, head_sha, args.sha))
            return 1
        ci_field = " ci=%s" % args.ci_run
    else:
        print("note: no --ci-run given -- the lane is declared ready on the "
              "reviews and the pushed sha alone")
    line = "%s ready %s %s reviews: %s | %s%s" % (
        _iso_zulu(datetime.datetime.now(datetime.timezone.utc)),
        args.branch, args.sha,
        report["cross_family"]["detail"], report["final"]["detail"], ci_field)
    if args.dry_run:
        print("ready: --dry-run, nothing appended to %s" % args.inbox)
        print("  %s" % line)
        return 0
    try:
        append_inbox_line(args.inbox, line)
    except OSError as exc:
        print("ready: cannot write %s: %s" % (args.inbox, exc), file=sys.stderr)
        return 2
    print("ready: appended to %s" % args.inbox)
    print("  %s" % line)
    return 0


def reviewer_run_override(review, client, cfg, tier, model, override, free):
    """``(model, combo, note)`` -- the run this reviewer resolution implies.

    A review card that names its author is a *reviewer* request, so when the
    reviewer the list picked runs on the client this run is already using, the
    run carries that reviewer's model spelling rather than the resolver's
    generic route (REVROUTE (S2) item 2; item 4 is what makes the paid Muse
    reviewer's spelling resolvable at all).

    Anything that is not that clean case leaves the run alone and says so in
    ``note`` -- the operator's explicit ``--model``, a ``--free`` promo run, a
    reviewer that lives on another client, and a non-gateway client (which takes
    its own ``--model`` and has no gateway combo to name) all keep what they had.
    Silence here would be the bug: the review would run on a model nobody chose.
    """
    if not review or review.get("state") != "resolved":
        return model, None, None
    entry = review["reviewer"]
    asked = "%s %s" % (entry.get("client"), entry.get("model"))
    if override:
        return model, None, "note: an explicit --model wins over policy.reviewers (%s)" % asked
    if free:
        return model, None, "note: --free keeps its promo model, not policy.reviewers (%s)" % asked
    if entry.get("client") != client.name:
        return (model, None,
                "note: policy.reviewers wants --client %s --model %s; this run stays on %s, "
                "so it is NOT the review that list picked"
                % (entry.get("client"), entry.get("model"), client.name))
    if not client.gateway:
        return (model, None,
                "note: %s is not a gateway client, so --model stays the caller's; "
                "policy.reviewers picked %s" % (client.name, asked))
    # The gateway only serves a route the client config declares, so a combo
    # spelling is checked -- an undeclared heading is exactly the failure the
    # operator must see, not a silent fallback (item 4).
    # A model spelled on the client's OWN provider (opencode's zen free models,
    # the same shape a --free run passes today) is the client's to resolve:
    # there is no gateway route behind it to declare, and refusing it here would
    # bench a reviewer that runs fine.
    if not entry["model"].partition("#")[0].startswith("omniroute/"):
        return entry["model"], None, "reviewer-model: %s" % asked
    return (resolve_model(cfg, tier, False, entry["model"]),
            entry["model"].partition("#")[0].replace("omniroute/", "", 1),
            "reviewer-model: %s" % asked)


def _resolve_route_v2(args, parsed_card: dict, cfg: dict, override: str | None,
                      exclude_routes: set | None = None) -> dict:
    """A v2 card is routed by the resolver, not select_combo (RUNV2, spec 6.1
    "run takes card v2"). Shares route_plan_for/autoos_resolver.plan with the
    `route` subcommand and the MCP `route` tool, so `run` and `route` can never
    disagree about the same card.

    `exclude_routes` (SPAWNCAP, S2) drops the named route ids from the registry
    the resolver sees, so a provider-stopped attempt is never picked again when
    the run falls through to the next route. The registry is copied, never
    mutated; the returned route is marked ``resolver: True`` so cmd_run knows
    the run was resolver-routed (the v1/--tier paths are not).

    `state` "input_required" (no route survived the filters) raises
    RouteInputRequired with the plan's own reason; "deferred" raises
    RouteDeferred with "deferred until <time>: <reason>" unless --no-defer was
    given, in which case the deferral is ignored and the chosen route runs now
    (the reason says so). Both exceptions are ValueError subclasses that
    cmd_run catches ahead of its generic ValueError handler, so the message is
    printed as-is - no "(see: ...)" suffix tacked on.

    privacy: a card's privacy=sensitive is enforced entirely inside plan()'s
    own filters (spec 5.3 step 1) - this function adds no privacy rule of its
    own; it only turns whatever route plan() already picked into a combo/tier.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    registry = load_live_registry()
    if exclude_routes:
        # A copy, so the shared load_registry() cache (and the caller's own
        # reference) never loses the routes a previous attempt needs recorded.
        registry = dict(registry)
        registry["routes"] = {rid: route for rid, route in (registry.get("routes") or {}).items()
                              if rid not in exclude_routes}
    overlay = load_overlay(MEASURED_OVERLAY_PATH)
    track_record = track.load(TRACK_RECORD)
    client_state = measure_mod.client_state(clients)
    result = route_plan_for(parsed_card, args.task, ROOT, DEFAULT_ORCHESTRATOR_MODEL,
                            now, registry, overlay, track_record, client_state)

    if result["state"] == "input_required":
        raise RouteInputRequired(result["reason"])
    if result["state"] == "deferred" and not args.no_defer:
        raise RouteDeferred("deferred until %s: %s" % (result["defer_until"], result["reason"]))

    combo = result["route"]
    tier = _tier_for_route(combo)
    model = None if args.free else resolve_model(cfg, tier, False, override or "omniroute/" + combo)
    reason = "resolver-v2: %s" % result["reason"]
    if result["state"] == "deferred":  # only reachable with --no-defer, per the raise above
        reason = "resolver-v2 (ignoring defer until %s via --no-defer): %s" % (
            result["defer_until"], result["reason"])
    if override and model:  # an explicit --model wins over the resolver's route, and says so
        combo, reason = model.partition("#")[0].replace("omniroute/", "", 1), reason + "+model"

    card = routing.normalize_v2(parsed_card)
    # An authored review card runs its reviewer, not just any survivable route
    # (REVROUTE item 2); a reviewer on another client is announced, never faked.
    model, reviewer_combo, reviewer_note = reviewer_run_override(
        result.get("review"), clients.CLIENTS[args.client], cfg, tier, model,
        override, args.free)
    if reviewer_combo:
        combo = reviewer_combo
    # the registry class of the combo that actually runs (an explicit --model may
    # have replaced the resolver's route); the track record keys on it
    route_class = registry.get("routes", {}).get(combo, {}).get("class")
    # "effort" is the rung the resolver scored (spec 5.2); track_entry stamps it
    # into the record so p_success can match the resolver's bucket+effort query.
    # An explicit --model override may have replaced the combo above, but the
    # resolver's bucket, reason and effort still describe the card's plan.
    return {"tier": tier, "model": model, "combo": combo, "reason": reason, "card": card,
            "privacy": card["privacy"], "review": card["kind"] == "review",
            "read_only": read_only_run(args, card),
            "bucket": result["bucket"], "class": route_class, "resolver": True,
            "effort": result.get("effort"),
            # who reviews this card (REVROUTE item 2); plan() already walked
            # policy.reviewers with the same registry/probes/clock it used to
            # pick the route, so the spawner never re-derives it.
            "review_plan": result.get("review"), "reviewer_note": reviewer_note}


class PrivacyRefused(ValueError):
    """A sensitive run whose explicit --model names a combo with a leg that is
    not private-safe (PRIV3)."""


def sensitive_combo_refusal(combo: str, registry: dict):
    """Why `combo` may not carry privacy=sensitive work, or None when it may.

    Every leg the gateway serves counts - unavailable_legs included, since
    combos.json keeps them and OmniRoute can still fall through to them. An id
    that is not a registry route is refused: nothing proves it private-safe.
    """
    route = (registry.get("routes") or {}).get(combo)
    if route is None:
        return "privacy: %r is not a registry route, so nothing proves it private-safe" % combo
    for leg in route.get("legs") or []:
        try:
            provider_id, model_id = resolve_leg(leg, registry)
        except ValueError as exc:
            return "privacy: %s: %s" % (leg, exc)
        safe, why = private_safe(provider_id, model_id, registry)
        if not safe:
            return "privacy: --model %s serves %s, which is not private-safe (%s)" % (combo, leg, why)
    return None


def resolve_route(args, cfg: dict, client, exclude_routes: set | None = None) -> dict:
    """resolve_route_unchecked plus the PRIV3 check: a sensitive run whose
    explicit --model replaced the card's combo must still land on private-safe
    legs only (--allow-training keeps its compatibility escape, which now only
    waives that explicit-override check - it no longer unlocks a trainable leg,
    since 2026-09-27)."""
    route = resolve_route_unchecked(args, cfg, client, exclude_routes)
    if args.free and route.get("privacy") == "sensitive":
        # close-priv 2026-09-26: --free replaces the combo with the promo
        # model, which may train on prompts - never for a sensitive task.
        raise PrivacyRefused("privacy: --free runs the promo model %s, which may train on "
                             "prompts; a privacy=sensitive task cannot use it" % args.free_model)
    override = args.model if client.gateway else None
    if (override and route.get("model") and route.get("privacy") == "sensitive"
            and not args.allow_training):
        reason = sensitive_combo_refusal(route["combo"], load_registry(REGISTRY_PATH))
        if reason:
            raise PrivacyRefused(reason)
    return route


def resolve_route_unchecked(args, cfg: dict, client, exclude_routes: set | None = None) -> dict:
    """Tier/model/combo for this run: an explicit --tier, a v2 card through the
    resolver (RUNV2), or a v1 card through select_combo.

    ``exclude_routes`` is forwarded to _resolve_route_v2 only (SPAWNCAP, S2):
    the v1/--tier paths have a single combo and never fall through."""
    # --model names a gateway combo for opencode and the gateway clients; for
    # agy/claude/qoder it is the client's own model id and is not checked here.
    override = args.model if client.gateway else None
    if args.tier is not None:
        model = None if args.free else resolve_model(cfg, args.tier, args.clean, override)
        combo = (model or "").partition("#")[0].replace("omniroute/", "", 1) or None
        return {"tier": args.tier, "model": model, "combo": combo, "reason": "explicit-tier",
                "card": None, "privacy": "sensitive" if args.clean else "public",
                "review": args.tier == 3, "read_only": read_only_run(args, None),
                "review_plan": None}
    parsed = routing.parse_card(args.card or "")
    if _is_v2_card(parsed):
        return _resolve_route_v2(args, parsed, cfg, override, exclude_routes)
    card = routing.normalize(parsed)
    combo, reason = routing.select_combo(card, args.allow_training)
    tier = int(re.match(r"t(\d)-", combo).group(1))  # t2-worker-clean -> 2
    model = None if args.free else resolve_model(cfg, tier, False, override or "omniroute/" + combo)
    if override and model:  # an explicit --model wins over the card's combo, and says so
        combo, reason = model.partition("#")[0].replace("omniroute/", "", 1), reason + "+model"
    review = resolve_review_plan(card)
    model, reviewer_combo, reviewer_note = reviewer_run_override(
        review, clients.CLIENTS[args.client], cfg, tier, model, override, args.free)
    if reviewer_combo:
        combo = reviewer_combo
    return {"tier": tier, "model": model, "combo": combo, "reason": reason, "card": card,
            "privacy": card["privacy"], "review": card["role"] == "review",
            "read_only": read_only_run(args, card),
            # a v1 card asks for a review with role=review; author is the shared
            # field, so the same reviewer walk applies (REVROUTE item 2).
            "review_plan": review, "reviewer_note": reviewer_note}


def build_plan(args, cfg: dict, exclude_routes: set | None = None,
               sandbox: dict | None = None) -> dict:
    """The full run plan for `args`.

    ``exclude_routes`` (SPAWNCAP, S2) is passed through to the resolver so a
    fallthrough re-run does not pick a route that already stopped. ``sandbox``
    reuses an existing clone (same path/branch) instead of naming a new one -
    a fallthrough re-runs in the same checkout, so its WIP commit and its work
    stay on one branch.
    """
    client = clients.CLIENTS[args.client]
    route = resolve_route(args, cfg, client, exclude_routes)
    depth, max_depth = clients.child_depth(os.environ, args.max_depth)
    env = {"AUTOOS_AGENT_DEPTH": str(depth), "AUTOOS_AGENT_MAX_DEPTH": str(max_depth)}
    overlay = {}
    title = args.title or ("t%d %s" % (route["tier"], args.task[:50]))
    tag = None
    if client.name == "opencode":
        agent = TIERS[route["tier"]]
        if args.free:
            model = args.free_model
            overlay.update(free_overlay(model))
        else:
            model = route["model"]
        if args.lean:
            overlay.update(lean_overlay(cfg))
        # OR3: a spawned opencode run whose model sits on the omniroute
        # provider stamps every gateway request with the lane tag, so
        # call_logs.session_tag attributes the call (1881 live calls carried
        # none). Provider-level `headers` (opencode v2 config schema
        # packages/schema/src/config/provider.ts:32, spread into the provider
        # at :90 and merged onto each model's requests in
        # packages/core/src/model.ts:198) - merged into any existing provider
        # block, never replacing it.
        if (model or "").startswith("omniroute/"):
            tag = session_tag(title)
            prov = overlay.setdefault("providers", {}).setdefault("omniroute", {})
            prov.setdefault("headers", {})[SESSION_TAG_HEADER] = tag
        cmd = ["opencode", "run", "--standalone", "--agent", agent, "--model", model,
               "--title", title]
        if args.auto:
            cmd.append("--auto")
        cmd.append(args.task)
    else:
        agent = client.name
        level = "ask" if not args.auto else ("read" if route["review"] else "edit")
        if client.name == "qoder" and level != "read":
            # qoder writes with bypass_permissions (no other headless write mode
            # exists) - only inside a private clone, where the leak check applies.
            # Not only "edit": --no-auto ("ask") ran with no flag and no sandbox
            # (review of 6622d29). qodercli has no path fence of its own, so the
            # containment prompt + leak check are the controls; writes outside
            # the parent checkout (e.g. $HOME) are not detected.
            args.isolate = True
        model = args.model if not client.gateway else None
        joinable = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-") if args.joinable else None
        cmd = clients.build_command(client, args.task, route["combo"], level, model, joinable)
        if args.lean and client.name in MCP_STRICT_CLIENTS \
                and "--strict-mcp-config" not in cmd:  # claude/qoder only: no MCP servers
            cmd[1:1] = ["--strict-mcp-config"]
        if client.name == "qoder":
            model = model or clients.QODER_DEFAULT_MODEL
        model = model or (route["combo"] if client.gateway else "(client default)")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.isolate:
        if sandbox is None:
            # The readable prefix stays; the random suffix keeps two spawns in the
            # same second (same task) from naming the same clone (bug 1).
            slug = slugify(args.task)
            uniq = unique_suffix()
            name = "%s-%s-%s-%s" % (os.path.basename(ROOT), stamp, slug, uniq)
            # Inside the repo's git-ignored logs/ (clients.state_dir). The clone has
            # its own .git, so opencode resolves it as its own project root.
            sandbox = {"path": os.path.join(clients.state_dir(), "sandboxes", name),
                       "branch": "agent/%s-%s-%s" % (stamp, slug, uniq)}
        if client.name == "opencode":
            # opencode keys a project by its root commit and remembers the root it
            # saw first; a private data dir keeps the clone from inheriting the
            # main checkout's recorded root.
            env["XDG_DATA_HOME"] = sandbox["path"] + ".opencode-data"
            overlay["permissions"] = outside_fence(env["XDG_DATA_HOME"],
                                                   os.environ.get("AUTOOS_TASK_DIR"))
    if sandbox is not None:
        # The fence denies opencode's file tools outside the clone, but a
        # worker told (or shown) an absolute parent path can still cd, git -C
        # or shell-write into it (live 2026-09-26) - say so in the task
        # itself. Every client takes the task as its last argv
        # (clients.build_command puts it there; opencode appends it above),
        # and the brief follows the line verbatim.
        cmd[-1] = isolate_task_prefix(sandbox["path"], ROOT,
                                     read_only=bool(route.get("read_only"))) + "\n" + cmd[-1]
    if overlay:
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(overlay)
    return {"agent": agent, "client": client.name, "model": model, "cmd": cmd, "env": env,
            "route": route, "depth": (depth, max_depth), "free": bool(args.free),
            "sandbox": sandbox, "cwd": sandbox["path"] if sandbox else os.getcwd(),
            "session_tag": tag}


def cmd_list(cfg: dict) -> int:
    for tier, agent in TIERS.items():
        a = cfg["agents"][agent]
        allow = [p["resource"] for p in a.get("permissions", [])
                 if p["action"] == "subagent" and p["effect"] == "allow"]
        print("t%d     %-19s %-24s spawns: %s" % (tier, agent, a["model"], ", ".join(allow) or "nothing"))
    omni = sorted(m for m in declared_models(cfg) if m.startswith("omniroute/"))
    print("\n--model accepts any declared model, e.g.: " + ", ".join(omni))
    print("--free runs every tier on %s (no key, no gateway)" % DEFAULT_FREE_MODEL)
    print("--card routes by task card (routing v%s): %s" % (routing.ROUTING_VERSION, ", ".join(
        "%s=%s" % (k, "|".join(v)) for k, v in routing.CARD_VALUES.items())))
    print("\nclients (--client):")
    print("%-9s %-9s %-8s %-8s %-10s %-38s %s" % ("client", "headless", "gateway", "subagts",
                                                  "installed", "auth", "notes"))
    yn = {True: "yes", False: "no"}
    for c in clients.CLIENTS.values():
        notes = c.notes
        if c.promo:
            age = clients.probe_age_days(c.name)
            notes += "; last probe: %s" % ("never" if age is None else "%.0f d ago%s" % (
                age, " (stale)" if age > clients.PROBE_STALE_DAYS else ""))
        installed = yn[shutil.which(c.binary) is not None]
        ok, reason = clients.signin_state(c)
        if ok is False:  # installed, but it cannot run a task here
            installed = "signed-out" if clients.signed_out(reason) else "broken"
            notes += "; NOT USABLE: %s - run `%s` once to sign in" % (reason, c.binary)
        print("%-9s %-9s %-8s %-8s %-10s %-38s %s" % (
            c.name, yn[c.headless], yn[c.gateway], yn[c.subagents], installed, c.auth, notes))
    try:
        print("\ndepth budget: a child of this shell runs at depth %d of %d" % clients.child_depth(os.environ))
    except clients.DepthError as exc:
        print("\ndepth budget: %s" % exc)
    return 0


def context_state(transcript_path: str | None, model_override: str | None) -> tuple:
    """(data, rc): the same fields the `context` subcommand prints, as data.

    A known fill returns ``{tokens, cap, pct, model, transcript, source}`` and
    rc 0. An unknown state (no transcript, no usage, or an unreadable
    ``transcript_path``) returns ``{"context": "unknown", "reason": ...}``; rc
    is 2 only for the unreadable-path case (matching the CLI's own exit code),
    0 otherwise. Pure aside from the transcript read itself - `autoos-agent.py
    context` and the MCP `context` tool both call this so their numbers can
    never drift apart.
    """
    if transcript_path:
        path = transcript_path
        try:
            with io.open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            return ({"context": "unknown",
                    "reason": "cannot read %s: %s" % (path, exc.strerror or exc)}, 2)
    else:
        path = ctx.discover_transcript(os.getcwd())
        if path is None:
            return {"context": "unknown", "reason": "no transcript"}, 0
        with io.open(path, encoding="utf-8") as fh:
            lines = fh.readlines()

    fill = ctx.fill_from_transcript(lines)
    if fill is None:
        return {"context": "unknown", "reason": "no usage"}, 0

    model = model_override or fill.get("model") or "unknown"
    caps, source = ctx.load_caps()
    cap = ctx.cap_for(model, caps)
    tokens = fill["tokens"]
    pct = int(round(100 * tokens / cap)) if cap else 0
    return ({"tokens": tokens, "cap": cap, "pct": pct, "model": model,
             "transcript": path, "source": source}, 0)


def cmd_context(args) -> int:
    """Print the calling session's context fill (spec 6.1, caps 8.3).

    The fill comes from the Claude Code transcript's latest assistant usage
    record. `--model` overrides only the model the cap is looked up for; the
    tokens still come from the transcript. `--json` prints the same numbers as
    an object (its `source` is the cap's provenance: `policy` when read from
    the registry's policy.handoff_caps, `default` when that is unreadable).
    """
    data, rc = context_state(args.transcript, args.model)
    if data.get("context") == "unknown":
        stream = sys.stderr if rc == 2 else sys.stdout
        print("context: unknown (%s)" % data["reason"], file=stream)
        return rc
    if args.json:
        print(json.dumps({k: data[k] for k in
                          ("tokens", "cap", "pct", "model", "transcript", "source")}))
    else:
        print("context: %d / %d (%d%%) model=%s transcript=%s"
              % (data["tokens"], data["cap"], data["pct"], data["model"],
                 data["transcript"]))
    return rc


def heartbeat_state(inbox: str | None, transcript: str | None, repos: list | None,
                    cap: int | None) -> tuple:
    """(data, rc): the same facts `autoos-agent.py heartbeat --json` prints and
    the MCP `heartbeat` tool returns (R-heartbeat-02/03, R-pause-01,
    R-handoff-07 migrated into code).

    Read-only: computes ``heartbeat.pause_state`` on `inbox`, ``heartbeat.
    repo_branch_state`` on every path in `repos` (``[os.getcwd()]`` when
    `repos` is empty/None), and reuses ``context_state`` on `transcript` -
    nothing here pushes, commits or writes. `cap` overrides the model's
    looked-up hand-off cap (same table ``context_state``/``ctx.cap_for`` use)
    with an exact token count, the way `context --model` overrides which
    table row applies; "over cap" is `tokens >= cap`.

    Exit code: 3 when the pause is active (takes precedence over everything
    else), else 4 when over cap, else 1 when any repo has an unpushed branch
    or a dirty working tree, else 0.
    """
    pause = heartbeat.pause_state(inbox, since=heartbeat.session_start(transcript))
    repo_list = list(repos) if repos else [os.getcwd()]
    repo_rows = []
    any_problem = False
    for repo in repo_list:
        state = heartbeat.repo_branch_state(repo)
        row = {"repo": repo,
               "unpushed": [{"branch": b, "ahead": n} for b, n in state["unpushed"]],
               "dirty": state["dirty"]}
        if state["unpushed"] or state["dirty"]:
            any_problem = True
        repo_rows.append(row)
    ctx_data, _ = context_state(transcript, None)
    over_cap = False
    if ctx_data.get("context") != "unknown":
        ctx_data = dict(ctx_data)
        if cap is not None:
            ctx_data["cap"] = cap
            ctx_data["pct"] = int(round(100 * ctx_data["tokens"] / cap)) if cap else 0
        over_cap = bool(ctx_data["cap"]) and ctx_data["tokens"] >= ctx_data["cap"]
    if pause["active"]:
        rc = 3
    elif over_cap:
        rc = 4
    elif any_problem:
        rc = 1
    else:
        rc = 0
    data = {"pause": pause, "repos": repo_rows, "context": ctx_data, "over_cap": over_cap,
            "exit_code": rc}
    return data, rc


def cmd_heartbeat(args) -> int:
    """Print the read-only heartbeat report (spec: R-heartbeat-02/03,
    R-pause-01, R-handoff-07). Never pushes, commits or writes anything - see
    tools/autoos_heartbeat.py's own docstring."""
    data, rc = heartbeat_state(args.inbox, args.transcript, args.repos, args.cap)
    if args.json:
        print(json.dumps({k: data[k] for k in
                          ("pause", "repos", "context", "over_cap", "exit_code")}))
        return rc
    pause = data["pause"]
    if pause["active"]:
        print("pause: active %s %s" % (pause["at"], pause["text"]))
    else:
        print("pause: none")
    for row in data["repos"]:
        for u in row["unpushed"]:
            print("unpushed: %s %s %d" % (row["repo"], u["branch"], u["ahead"]))
        if row["dirty"]:
            print("dirty: %s %d files" % (row["repo"], row["dirty"]))
    ctx_data = data["context"]
    if ctx_data.get("context") == "unknown":
        print("context: unknown (%s)" % ctx_data["reason"])
    else:
        print("context: %d/%d %d%%%s" % (ctx_data["tokens"], ctx_data["cap"], ctx_data["pct"],
                                         " over-cap" if data["over_cap"] else ""))
    return rc


def parse_now(value: str | None):
    """--now as a UTC datetime; None means "the wall clock, right now".

    Accepts an ISO 8601 string, a trailing "Z" included (autoos_resolver's own
    format, spec 6.4); a naive string is read as UTC. ValueError names the bad
    text.
    """
    if value is None:
        return datetime.datetime.now(datetime.timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("now=%r: expected an ISO 8601 UTC string" % (value,))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def load_registry(path: str) -> dict:
    """catalog/ai-registry.json (or a test's own copy); a missing/bad file raises."""
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_overlay(path: str) -> dict:
    """logs/routing/measured.json if present, else {} (spec 3.1: git-ignored)."""
    if not os.path.isfile(path):
        return {}
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def route_plan_for(card, brief: str, repo: str, orchestrator_model: str, now,
                   registry: dict, overlay: dict, track_record: list,
                   client_state: dict) -> dict:
    """card -> route_plan (spec 6.1/6.2): the CLI `route` subcommand and the MCP
    `route` tool's shared, pure-ish core.

    `card` is a task card exactly as `run --card` accepts it - text
    (``kind=review,paths=...`` or a JSON object string, parsed by
    ``routing.parse_card``) - or already a dict (the MCP tool's own shape).
    Either way it is normalized to v2 with ``routing.normalize_v2`` (a
    ``routing.CardError`` on a bad one propagates to the caller). Features come
    from ``autoos_measure.measure`` against `repo`; the resolver itself
    (``autoos_resolver.plan``) is pure, so `registry`, `overlay`,
    `track_record` and `client_state` are exactly what the caller passes -
    real data from disk/probes for the CLI and the MCP tool, anything a test
    wants to inject. A ``measure``/``plan`` ValueError (a missing feature, an
    unknown orchestrator model, ...) is not caught here: both callers fail
    closed on it.
    """
    parsed = routing.parse_card(card) if isinstance(card, str) else dict(card or {})
    normalized = routing.normalize_v2(parsed)
    features = measure_mod.measure(normalized, repo, brief or "")
    return resolver.plan(normalized, features, client_state, registry, overlay,
                         track_record, orchestrator_model, now)


def cmd_route(args) -> int:
    """Print the resolver v2 route_plan for one task card (spec 6.1).

    Exit 0 when a route was chosen (ready or deferred), 5 when the state is
    input_required (no route survived the filters or an override was
    refused), 2 on bad input (a bad card, a bad --now, an unknown
    --orchestrator-model, or any other measure()/plan() ValueError - all fail
    closed with the message on stderr). --explain writes the per-route explain
    lines and the final reason to stderr before the JSON, so stdout always
    stays one parseable route_plan.
    """
    try:
        now = parse_now(args.now)
    except ValueError as exc:
        return refuse(str(exc))
    repo = args.repo or ROOT
    try:
        registry = load_live_registry()
        overlay = load_overlay(MEASURED_OVERLAY_PATH)
    except (OSError, ValueError) as exc:
        return refuse("cannot load routing data: %s" % exc)
    track_record = track.load(TRACK_RECORD)
    client_state = measure_mod.client_state(clients)
    try:
        result = route_plan_for(args.card, args.brief or "", repo,
                                args.orchestrator_model, now, registry, overlay,
                                track_record, client_state)
    except (routing.CardError, ValueError) as exc:
        return refuse(str(exc))
    if args.explain:
        for line in result.get("explain") or []:
            print(line, file=sys.stderr)
        print(result.get("reason", ""), file=sys.stderr)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("route") is not None else 5


def log_run(plan: dict, rc: int, secs: float, free: bool) -> None:
    logs = os.path.join(ROOT, "logs")
    os.makedirs(logs, exist_ok=True)
    route = plan["route"]
    card = ",".join("%s=%s" % kv for kv in sorted((route["card"] or {}).items())) or "-"
    line = ("%s client=%s agent=%s model=%s combo=%s reason=%s routing=%s card=%s depth=%d/%d "
            "free=%d sandbox=%s rc=%d secs=%.0f\n") % (
        datetime.datetime.now().isoformat(timespec="seconds"), plan["client"], plan["agent"],
        plan["model"], route["combo"] or "-", route["reason"], routing.ROUTING_VERSION, card,
        plan["depth"][0], plan["depth"][1], int(free),
        (plan["sandbox"] or {}).get("path", "-"), rc, secs)
    with io.open(os.path.join(logs, "orch-%s.log" % datetime.date.today().isoformat()), "a", encoding="utf-8") as fh:
        fh.write(line)


def refuse(msg: str, rc: int = 2) -> int:
    print("autoos-agent: %s" % msg, file=sys.stderr)
    return rc


# A headless client that hits a tool it cannot prompt for prints one of these
# and still exits 0 (measured 2026-09-25, run 20260925-215048-85ba11). Only a
# line agy's own harness prints ("jetski: ...") counts: a worker whose brief or
# report quotes the marker must not fail. Matched case-insensitively.
HEADLESS_REFUSAL_PREFIX = "jetski:"
# Only these clients are piped (to spot the refusal); every other client keeps
# the spawner's own stdout/stderr, so a terminal stays a terminal for it -
# except that every --isolate run is captured too (tee'd to our stdout), so
# the provider-stop check has the child's tail.
CAPTURE_CLIENTS = ("agy",)
HEADLESS_REFUSAL_MARKERS = ("no output produced", "headless mode cannot prompt")


def headless_refusal(tail: str) -> str | None:
    """The one-line refusal a headless client printed, or None.

    Measured 2026-09-25: agy printed "jetski: no output produced - a tool
    required the \"command\" permission that headless mode cannot prompt for,
    so it was auto-denied" and exited 0. The agent's report is not evidence,
    but its harness's own refusal line is.
    """
    for line in tail.splitlines():
        low = line.strip().lower()
        if low.startswith(HEADLESS_REFUSAL_PREFIX) and any(
                m in low for m in HEADLESS_REFUSAL_MARKERS):
            return line.strip()
    return None


def refusal_exit(rc: int, tail: str) -> tuple:
    """(exit code, stderr message) for a finished client.

    rc 0 plus a refusal in the tail is a failure, not a success: exit 6 and
    print the refusal. Any other rc passes through untouched.
    """
    if rc == 0:
        msg = headless_refusal(tail)
        if msg is not None:
            return 6, msg
    return rc, None


# A provider that stops a worker mid-task (rate limit, capacity, quota,
# billing) still lets the client exit 0 with the work uncommitted. WIPfix
# (measured 2026-09-26 19:1x-19:3xZ): three --isolate workers were cut off
# right before `git commit`. Matched case-insensitively against the captured
# client tail (run_client keeps it for CAPTURE_CLIENTS and every --isolate
# run); the leading space on " 429"/" 402" keeps those digits from matching
# mid-word. ONE tuple, so the list is the whole contract.
PROVIDER_STOP_MARKERS = (
    "rate limit exceeded",
    "capacity is temporarily unavailable",
    "resource_exhausted",
    " 429",
    "quota reached",
    "payment required",
    " 402",
    # FUP (2026-09-27, measured as the whole last line of a qoder run):
    # qodercli stops when no credits remain.
    "your personal credits have been exhausted",
    # TOOLFIX item 3 (measured 2026-09-27): an opencode run that printed
    # "Error: No active credentials for provider: sambanova." as its error line
    # then exited 1 instead of 8 -- a provider stop, not a normal exit.
    "no active credentials for provider",
    # SPAWNCAP (S2, 2026-09-27): the gateway's own 503 ALL_TARGETS_SKIPPED
    # ("no route's legs are currently servable") is a provider stop like any
    # other - the run was cut off and can fall through to the next route.
    "all targets were skipped by pre-dispatch filters",
    # FUP form of the qoder credits stop above; other clients word it without
    # "your personal".
    "credits exhausted",
    # R6STOP (measured 2026-09-27T19:0x-19:1xZ, work/L1-routing/R6RES.out): the
    # gateway's per-credential cooldown - "Error: [429] All credentials for
    # model gemini-3.8-flash are cooling down (reset after 37s)". It carries no
    # " 429" (the digits sit inside brackets) and no rate-limit wording, so a
    # cooled leg was not a stop at all and its stated window was never read.
    "are cooling down",
)

# SPAWNCAP (S2): how many times a provider-stopped resolver-routed --isolate
# run re-runs the same task on the next route before it gives up with exit 8.
MAX_FALLTHROUGH = 2


def fallthrough_line(combo: str, stop: str, next_combo: str) -> str:
    """The one line printed when a provider-stopped attempt falls through.

    Pure, so the exact wording is pinned by a test rather than by the caller.
    """
    return "provider stop on %s: %s -> falling through to %s" % (combo, stop, next_combo)

# WIPfix2 (measured 2026-09-26 20:2xZ): a worker that merely READS or prints
# text containing a marker mid-run - a brief or lesson quoting a past 429 -
# then finishes normally was reported as a provider stop. A real provider stop
# is the client's LAST output before it exits, so only this many non-empty
# lines from the end of the tail are scanned.
PROVIDER_STOP_WINDOW = 8

# WIPfix3 (measured 2026-09-26, work/L1-routing/WIPfix2.out): the window alone
# still false-positived - a normal run whose last lines contained a CODE line
# quoting 'print("Error: Rate limit exceeded.")' was reported PROVIDER-STOP. A
# real stop is an error-prefixed LINE, so the stripped line must also START
# with one of these prefixes (case-insensitive); it covers "error: ... 429"
# status lines as clients print them. The bare " 429"/" 402" markers stay in
# PROVIDER_STOP_MARKERS and must - it is this prefix requirement, not marker
# removal, that stops a marker quoted in prose or code from matching.
PROVIDER_STOP_PREFIXES = ("error", "agy_error", "fatal")

# ANSI escape sequences a client wraps (colour/bold) or redraws (erase-line,
# cursor moves) its stderr prefix in: every CSI sequence, plus a leading
# carriage return from an in-place line redraw ("\r\x1b[2KError: ...").
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|^\r")


def provider_stop(tail: str) -> str | None:
    """The provider-stop line among the client's last lines, or None.

    Only the last PROVIDER_STOP_WINDOW non-empty lines of `tail` are scanned:
    a real provider stop is the client's last output before it exits, so a
    marker quoted earlier in the run must not match. Inside the window a line
    only when, after lstrip() and removing ANSI colour codes, it STARTS with
    an error prefix (PROVIDER_STOP_PREFIXES, case-insensitive) AND contains a
    PROVIDER_STOP_MARKERS entry (WIPfix3). Returns the matching line nearest
    the end when several are in the window.
    """
    lines = [line for line in (tail or "").splitlines() if line.strip()]
    for line in reversed(lines[-PROVIDER_STOP_WINDOW:]):
        clean = _ANSI_RE.sub("", line.lstrip())
        low = clean.lower()
        if low.startswith(PROVIDER_STOP_PREFIXES) and any(
                marker in low for marker in PROVIDER_STOP_MARKERS):
            return clean
    return None


# --- REVROUTE (S2) item 3: a stop that states its own reset time -------------
#
# A 429 that says "resets in ~83h" is worth more than a track record: the client
# is the only witness to the window, and handing the same provider the next task
# for three days wastes every one of them. So the spawner records the window in
# its own state file and every ROUTING read (route, run, the reviewer walk)
# merges it into the registry as the provider's `unavailable_until` -- which
# `registry.unavailable_now` already knows how to skip and how to let expire.
# Renders and `registry.py validate` never read it: they stay clock-free.

# A client printing "resets in ~400d" must not bench a provider for a year on
# the spawner's authority; a window this long is an operator edit to the
# registry, with the evidence quoted next to it.
MAX_AUTO_RESET_SECONDS = 7 * 86400

# R6STOP: Google counts its own cooldown down in fractional seconds
# ("Please retry in 59.250991496s."), and words it "retry in" as well as
# "retry after". The number group is the whole part and the fraction is
# skipped, so 59.25... reads as 59 -- the window the run was told, to the
# second it stated it.
_RESET_RE = re.compile(
    r"(?:resets? in|reset after|try again in|retry (?:after|in))"
    r"\s*~?\s*(\d+)(?:\.\d+)?\s*([a-z]+)")
_RESET_UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                "m": 60, "min": 60, "minute": 60, "minutes": 60,
                "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
                "d": 86400, "day": 86400, "days": 86400}

# "provider: sambanova. ..." - the gateway names the connection it tried. The
# class stops at a comma or period so the sentence's own punctuation is not part
# of the id.
_STOP_PROVIDER_RE = re.compile(r"provider:\s*([A-Za-z0-9_-]+)", re.IGNORECASE)


def parse_reset(text: str) -> int | None:
    """The seconds a stop line counts itself out for, or None if it names none.

    Reads the reset the client states ("resets in ~83h", "reset after 51s",
    "try again in 15 minutes"), not a guess: a plain "Rate limit exceeded"
    without a window returns None and records nothing. An unknown unit reads as
    no window rather than as seconds.
    """
    m = _RESET_RE.search((text or "").lower())
    if not m:
        return None
    unit = _RESET_UNITS.get(m.group(2))
    return int(m.group(1)) * unit if unit else None


def _leg_provider_model(leg, registry):
    """A route leg as ``(provider_id, model_id)``, or None if it does not resolve.

    `registry.resolve_leg` is the one place that knows a leg's prefix may be
    spelled either as the provider's key or as its ``omniroute_id`` (25 such
    legs in the real registry, ``gemini/gemini-3.8-flash`` among them); a leg
    that names no provider or no model is None here and reported by
    `registry.py check`, not by a routing read.
    """
    try:
        return resolve_leg(leg, registry)
    except ValueError:
        return None


# The characters that make a model id longer, not a boundary: the gateway quotes
# the id as one token, so "gemini-3.7-flash-high" is a different model from
# "gemini-3.7-flash" and "openai/gpt-oss-120b" from "gpt-oss-120b" (R6STOP
# review, SPAWNFIX3 item 7). Git's own `\b` treats '-' '/' '.' as breaks, which
# matched the shorter id inside the longer one and benched the wrong provider.
# A '.' is only part of an id when something id-like follows it: the gateway
# ends its sentences with one ("... model gpt-oss-120b."), and treating that as
# a continuation would attribute the stop to no provider at all.
_MODEL_ID_TOKEN = r"[0-9A-Za-z._/-]"
_MODEL_ID_NEXT = r"[0-9A-Za-z_/-]|\.[0-9A-Za-z]"


def _line_names_model(line, model_id) -> bool:
    """True when the stop line quotes `model_id` as a word of its own.

    The gateway says "All credentials for model gemini-3.8-flash ...", so the
    model it had already chosen is in the text. Whole-token so a model id that
    is a fragment of another one's spelling cannot drag in the wrong provider.
    """
    if not model_id:
        return False
    pattern = (r"(?<!%s)%s(?!%s)" % (_MODEL_ID_TOKEN,
                                     re.escape(str(model_id).lower()),
                                     _MODEL_ID_NEXT))
    return re.search(pattern, line) is not None


def stop_provider_id(line: str, registry: dict, legs, now=None) -> str | None:
    """Which provider took the stop, as the registry's own id, or None.

    A line that names its provider wins -- including the gateway's spelling of
    it (providers.<id>.omniroute_id), which is the same connection, not a new
    one. Next a line that names the MODEL it tried (R6STOP: "All credentials
    for model gemini-3.8-flash are cooling down"): the gateway is telling us
    who served it, and the provider of the leg heading that model is benched
    whatever else the route happens to hold -- unless more than one provider of
    the route serves that model, when NOTHING is benched and the line says so
    out loud (R6STOP review, SPAWNFIX3 item 7: t2-worker's route carries
    gpt-oss-120b on both cerebras and sambanova, and a stop line names the
    model, never who served it; benching one of them on a 50/50 guess starves a
    provider that may be perfectly healthy). Only an unnamed line falls back
    to the gateway working down the route's legs in order, so the one that took
    the traffic is the first leg whose provider is up right now. Both read legs
    through `resolve_leg`, so a leg spelled with a gateway alias attributes to
    its provider rather than being skipped (measured: a t2-worker gemini
    cooldown benched antigravity). A line that names nothing and has no live leg
    to choose from is attributed to no provider (recording a guess would bench
    the wrong one).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    providers = registry.get("providers") or {}
    line = line or ""
    m = _STOP_PROVIDER_RE.search(line)
    if m:
        named = m.group(1)
        for pid, provider in providers.items():
            if pid == named or (isinstance(provider, dict)
                                and provider.get("omniroute_id") == named):
                return pid
        return None
    low = line.lower()
    served = {}
    for leg in legs or []:
        resolved = _leg_provider_model(leg, registry)
        if resolved is None:
            continue
        pid, model_id = resolved
        if _line_names_model(low, model_id):
            pids = served.setdefault(model_id, [])
            if pid not in pids:
                pids.append(pid)
    if served:
        # The most specific id the line quoted is the model it named.
        model_id = max(served, key=len)
        pids = served[model_id]
        if len(pids) > 1:
            print("ambiguous stop: %s served by %s - not benched"
                  % (model_id, ", ".join(pids)))
            return None
        return pids[0]
    for leg in legs or []:
        pid, _model_id = _leg_provider_model(leg, registry) or (None, None)
        provider = providers.get(pid)
        if isinstance(provider, dict) and not unavailable_now(provider, now):
            return pid
    return None


def load_provider_state(path: str) -> dict:
    """The spawner's provider-stop state; a missing or broken file reads as empty.

    A record of a transient outage is never worth failing a run over, so this
    cannot raise: an unreadable file is an empty state (silently -- there is
    nothing to have said), and unparseable JSON is named on stderr and reads as
    empty too, because the shape that follows cannot be trusted. What the file
    SAYS once it parses is checked by `_validated_provider_entries`.
    """
    try:
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    try:
        state = json.loads(text)
    except ValueError as exc:
        _provider_state_warning("it is not JSON (%s)" % exc)
        return {}
    if not isinstance(state, dict):
        _provider_state_warning("the file is a %s, not an object"
                                % type(state).__name__)
        return {}
    return state


def _provider_state_warning(why: str) -> None:
    """One line on stderr about the provider-state file. Never an exception.

    One per call, however many rows were bad: the caller is a routing read that
    has already decided to ignore the file, and a page per row would bury the
    rest of the run's output.
    """
    print("autoos-agent: ignoring the provider-state file: %s" % why,
          file=sys.stderr)


def _iso_utc(text):
    """An ISO instant as an aware UTC datetime, or None.

    `_parse_iso` accepts a naive value; a naive instant in a state file is
    someone's local wall clock, and comparing it to an aware `now` would raise
    TypeError. It is read as UTC -- the shape the writer emits (`_iso_zulu`) and
    registry check rule 7 enforce.
    """
    parsed = _parse_iso(text)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _validated_provider_entries(state, now=None):
    """``(rows, reasons_ignored)`` for a loaded provider-state file.

    REVFIX S1/S3. The file is this tool's own, but it lives in logs/ where a
    person fixes things by hand and where an older build may already have
    written it, so no part of its shape is trusted: ``providers`` must be an
    object, every row an object, and every row's ``unavailable_until`` a
    parseable ISO instant no more than MAX_AUTO_RESET_SECONDS ahead -- the cap
    the WRITER already applies, enforced on the way in as well or a single row
    ("2099-01-01") benches a provider forever on the spawner's authority.

    Every failure is a reason in the second half of the tuple, never an
    exception: a routing read must go on with the registry as it is.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows, reasons = {}, []
    raw = state.get("providers") if isinstance(state, dict) else None
    if raw is None:
        return rows, reasons
    if not isinstance(raw, dict):
        return rows, ['its "providers" section is a %s, not an object of '
                      "provider rows" % type(raw).__name__]
    cap = now + datetime.timedelta(seconds=MAX_AUTO_RESET_SECONDS)
    for pid, entry in raw.items():
        if not isinstance(entry, dict):
            reasons.append("%s is a %s, not an object" % (pid, type(entry).__name__))
            continue
        until = entry.get("unavailable_until")
        parsed = _iso_utc(until)
        if parsed is None:
            reasons.append("%s carries no parseable ISO unavailable_until (%r)"
                           % (pid, until))
            continue
        if parsed > cap:
            reasons.append("%s claims a reset %s, past the %d-day window a machine "
                           "observation may assert (an operator's own registry edit "
                           "is how a longer outage is declared)"
                           % (pid, until, MAX_AUTO_RESET_SECONDS // 86400))
            continue
        rows[pid] = entry
    return rows, reasons


def _iso_zulu(moment) -> str:
    return moment.astimezone(datetime.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _until_is_expired(entry, now) -> bool:
    """True when `entry`'s window already passed (so it says nothing anymore)."""
    until = (entry or {}).get("unavailable_until")
    parsed = _iso_utc(until)
    return parsed is not None and now >= parsed


def record_reset_stop(stop_line: str, combo, registry: dict, now=None,
                      path: str | None = None):
    """Record a stopped provider's own reset window; returns (provider, until) or None.

    Nothing is recorded when the line states no window, when the window is
    implausible (MAX_AUTO_RESET_SECONDS), or when no provider can be named for
    it. A second stop for the same provider keeps the LATER window, and entries
    whose window already passed are dropped on the way in, so the file cannot
    grow into a list of historical outages. Writes atomically (mkstemp in the
    target directory, then os.replace) - a reader mid-run must never see a
    half-written state.
    """
    seconds = parse_reset(stop_line)
    if seconds is None or seconds > MAX_AUTO_RESET_SECONDS:
        return None
    legs = ((registry.get("routes") or {}).get(combo) or {}).get("legs") or []
    provider_id = stop_provider_id(stop_line or "", registry, legs, now)
    if provider_id is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    path = path or PROVIDER_STATE_PATH
    until = _iso_zulu(now + datetime.timedelta(seconds=seconds))
    state = load_provider_state(path)
    kept, ignored = _validated_provider_entries(state, now)
    if ignored:
        # REVFIX S1: the writer is not poisoned by what it is reading. The bad
        # rows are named and dropped, and the file that comes back is the shape
        # the reader expects.
        _provider_state_warning("; ".join(ignored))
    providers = {pid: entry for pid, entry in kept.items()
                 if not _until_is_expired(entry, now)}
    previous = providers.get(provider_id) or {}
    previous_until = _iso_utc(previous.get("unavailable_until"))
    if previous_until is not None and _iso_utc(until) < previous_until:
        until = previous["unavailable_until"]
    providers[provider_id] = {"unavailable_until": until, "combo": combo,
                              "reason": stop_line, "recorded_at": _iso_zulu(now)}
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".provider-state-")
        try:
            with io.open(fd, "w", encoding="utf-8") as fh:
                json.dump({"$comment": "providers a real run reported as stopped, "
                                       "with the reset that run was told; transient "
                                       "state, not the operator's registry",
                           "providers": providers}, fh)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
    except OSError as exc:
        print("autoos-agent: could not record the provider stop: %s" % exc,
              file=sys.stderr)
        return None
    return provider_id, until


def apply_provider_state(registry: dict, state: dict, now=None) -> dict:
    """`registry` with the recorded windows applied as providers' `unavailable_until`.

    Returns a copy - the shared `load_registry()` object is never mutated. The
    state is validated first (`_validated_provider_entries`): a row that is
    missing, mis-shaped or beyond the cap is ignored with one warning and the
    registry stands as it is, because this runs on the routing path of every
    `route`/`run` and a broken outage record must not take the router down with
    it (REVFIX S1/S3). An entry for a provider the registry does not know is
    ignored (the state file follows the registry, not the other way round), and a
    provider the operator already benched for LONGER keeps the operator's date: a
    machine observation must never shorten an outage someone wrote on purpose, and
    the cap is on the machine's word, not the operator's. A window that has passed
    is applied as it is and self-heals in `unavailable_now`.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows, ignored = _validated_provider_entries(state, now)
    if ignored:
        _provider_state_warning("; ".join(ignored))
    merged = dict(registry)
    providers = dict(registry.get("providers") or {})
    merged["providers"] = providers
    for pid, entry in rows.items():
        provider = providers.get(pid)
        if not isinstance(provider, dict):
            continue
        parsed = _iso_utc(entry.get("unavailable_until"))
        current = _iso_utc(provider.get("unavailable_until"))
        if current is not None and current >= parsed:
            continue
        provider = dict(provider)
        provider["available"] = False
        provider["unavailable_until"] = entry["unavailable_until"]
        providers[pid] = provider
    return merged


def load_live_registry(reg_path: str | None = None, state_path: str | None = None,
                       now=None) -> dict:
    """The registry as it stands for the routing decisions RIGHT NOW.

    The catalog plus the spawner's recorded provider stops. Use this wherever
    `route`/`run`/the reviewer walk ask "who is up"; keep plain
    `load_registry()` for the privacy and capability reads (which a provider
    outage does not change) and in the renders (which must not move with the
    date). With no state recorded this is the cached registry, unchanged.
    """
    registry = load_registry(reg_path or REGISTRY_PATH)
    state = load_provider_state(state_path or PROVIDER_STATE_PATH)
    if not (state.get("providers") or {}):
        return registry
    return apply_provider_state(registry, state, now)


def _porcelain_path_is_logs(p: str) -> bool:
    """True when a porcelain path (quoted or not) is logs/ or under it."""
    p = p.strip().strip('"')
    return p == "logs" or p.startswith("logs/")


def _filtered_parent_status(root: str) -> dict:
    """{path-part: XY} of `git status --porcelain --untracked-files=all`, logs/ excluded.

    The --isolate clone and every run log live under logs/ (clients.state_dir),
    so logs/ paths are the spawner's own, never a worker's leak. Only entries
    where EVERY path is under logs/ drop; a rename with one side outside
    (e.g. `R  catalog/x -> logs/x`) is kept, keyed by the non-logs side.
    """
    # Untracked files count too: a worker with write rights (qoder
    # bypass_permissions, review of 6622d29) can drop a NEW file into the parent.
    r = subprocess.run(["git", "-C", root, "status", "--porcelain",
                        "--untracked-files=all"], capture_output=True, text=True)
    out = {}
    if r.returncode != 0:
        return out
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        rest = line[3:] if len(line) > 3 else ""
        paths = [p.strip() for p in rest.split(" -> ")]
        if all(_porcelain_path_is_logs(p) for p in paths):
            continue
        out[rest] = line[:2]
    return out


def _scan_first_parent_range(root: str, old: str, new: str) -> list:
    """Shas of first-parent commits in old..new with WORKER_EMAIL as author
    or committer (an --amend of a tip keeps the author but makes the worker
    the committer, so either field matches - review 2026-09-26)."""
    out = []
    if not old or not new or old == new:
        return out
    r = subprocess.run(["git", "-C", root, "log", "--first-parent",
                        "--format=%H%x00%ae%x00%ce", old + ".." + new],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return out
    for line in r.stdout.splitlines():
        sha, _, emails = line.partition("\x00")
        ae, _, ce = emails.partition("\x00")
        if sha and WORKER_EMAIL in (ae.strip(), ce.strip()):
            out.append(sha)
    return out


def _branch_reflog_entries(root: str, branch: str, count: int) -> list:
    """The newest `count` reflog entries of refs/heads/<branch>.

    The branch's OWN reflog, not HEAD's: the lane commits of an orchestrator
    merge update the lane ref's reflog, never the checked-out branch's, so
    they stay exempt.
    """
    r = subprocess.run(["git", "-C", root, "reflog", "show", "--format=%H%x00%ae%x00%ce",
                        "-n", str(count), "refs/heads/" + branch],
                       capture_output=True, text=True)
    out = []
    if r.returncode != 0:
        return out
    for line in r.stdout.splitlines():
        sha, _, emails = line.partition("\x00")
        ae, _, ce = emails.partition("\x00")
        if sha and WORKER_EMAIL in (ae.strip(), ce.strip()):
            out.append(sha)
    return out


def parent_snapshot(root=None):
    """(HEAD, branch, reflog count, ref tips, filtered porcelain, worktree
    branches) of the parent checkout. `branch` is None on a detached HEAD (the
    commit-then-reset check is skipped then); the reflog count is the checked-out
    branch's own log, and the worktree map bounds the single exemption the leak
    check grants - another lane's own checked-out branch."""
    if root is None:
        root = ROOT
    head = None
    r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        head = r.stdout.strip()
    branch = None
    r = subprocess.run(["git", "-C", root, "symbolic-ref", "-q", "--short", "HEAD"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        branch = r.stdout.strip()
    reflog_count = _reflog_len(root, branch) if branch else 0
    refs = {}
    r = subprocess.run(["git", "-C", root, "for-each-ref", "refs/heads",
                        "--format=%(refname)%00%(objectname)"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            name, _, sha = line.partition("\x00")
            if name and sha:
                refs[name] = sha
    return (head, branch, reflog_count, refs, _filtered_parent_status(root),
            _worktree_branches(root))


def _worktree_branches(root):
    """{ref: worktree path} for the branches checked out in this repository.

    All lanes are worktrees of one .git, so a parallel lane's worker commit
    moves its OWN checked-out branch without touching this run's parent
    checkout. An empty dict when `git worktree list` fails, which exempts
    nothing - the strict reading of every moved ref.
    """
    r = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    out, path = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            path = os.path.realpath(line[len("worktree "):])
        elif line.startswith("branch ") and path:
            out[line[len("branch "):].strip()] = path
    return out


def _lane_worktree_moved(name, snap_wt, end_wt, root, sandbox):
    """True when `name` is ANOTHER worktree's own branch, before and after.

    The one exemption a moved ref gets (false positive B, LEAKFP2). Every leg
    is required, because dropping any one of them is a leak shape:

    - the same worktree holds the branch at the snapshot AND at the check - a
      branch, or a worktree, that only appeared during the run is not excused;
      that is exactly what a worker's `git switch -c` plus a fast-forward back
      looks like;
    - it is not this worktree - the parent's own branch moving IS the leak;
    - it is not the run's sandbox - a worktree added inside the sandbox
      directory belongs to this run, not to another lane.
    """
    at_snap = snap_wt.get(name)
    if not at_snap or at_snap != end_wt.get(name):
        return False
    if at_snap == os.path.realpath(root):
        return False
    if sandbox:
        sb = os.path.realpath(sandbox)
        if at_snap == sb or at_snap.startswith(sb + os.sep):
            return False
    return True


def parent_leak(snapshot, root=None, sandbox=None):
    """(leak lines) since a parent_snapshot; empty list = no leak.

    A LEAK is, since the snapshot:
    - a first-parent commit on the checked-out branch (HEAD range, or the
      branch reflog when HEAD is back where it started - a commit-then-reset)
      whose author OR committer is WORKER_EMAIL (the committer catches an
      --amend of the parent tip);
    - the same scan on every branch ref that EXISTED at the snapshot and
      moved (a worker committing on a side branch). NEW refs are never
      scanned: the orchestrator fetches or merges worker lanes into new refs
      during a run - never a leak. A moved ref is exempted in exactly one
      case, another lane's own worktree (_lane_worktree_moved): all lanes are
      worktrees of one .git, so a parallel lane commits on the branch checked
      out in ITS worktree and this worktree never visited it (false positive
      B, MUSEAPI2.out). Nothing else is exempt - no run window, no
      "contained in a new branch", no identity beyond the worker's own email;
      75f2866 had all three and each was bypassable by a decoy `git branch`, a
      backdated GIT_COMMITTER_DATE, or a `switch -c` plus a fast-forward back
      (LEAKFP2 review). The --first-parent scan keeps an orchestrator's
      --no-ff lane merge out; a mid-run merge or fast-forward of THIS parent
      onto another lane is reported and only the frozen-parent rule (skill
      R-coord-01) tells the operator it was their own move - the exit-7
      message says so;
    - any tracked path outside logs/ whose porcelain state is DIRTY after
      and differs from before (new dirt is the worker-shaped signal; a path
      that became clean - the orchestrator committing its own WIP - is not a
      leak).

    KNOWN HOLES, both inherited from 75f2866~1 and accepted with the strict
    rule: a worker that commits on a branch CREATED during the run and then
    moves HEAD back off it is missed, because new refs are never scanned (the
    orchestrator fetches lanes into them); and plumbing that writes a commit
    without moving a pre-existing ref or dirtying the tree is missed. The one
    exemption (another lane's own worktree branch) can be carried by ANY
    worktree the sandboxed worker can reach - a live one it did not make and a
    stale one left by an earlier run, and a worker that runs `git worktree add`
    itself makes one: every worktree of the parent's .git is inside the fence,
    because a worktree is a directory the parent checkout points at, not a
    separate repository. None of the three is a containment proof; each is
    another reason the parent is frozen (skill R-coord-01).

    Git-ignored parent files (configuration/api-keys.yml, inventory.yml) are
    NOT covered: porcelain cannot see them. Nothing here is reverted.
    """
    if root is None:
        root = ROOT
    before_head, branch, reflog_count, before_refs, before_status, snap_wt = snapshot
    leaks = []
    after_head = None
    r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        after_head = r.stdout.strip()
    shas = _scan_first_parent_range(root, before_head, after_head)
    if not shas and branch and after_head == before_head:
        # commit-then-reset: HEAD is back at the start; scan the reflog
        # entries appended during the run.
        shas = _branch_reflog_entries(
            root, branch, max(0, _reflog_len(root, branch) - reflog_count))
    if shas:
        leaks.append("worker commits in the parent checkout: %s" % ", ".join(shas))
    r = subprocess.run(["git", "-C", root, "for-each-ref", "refs/heads",
                        "--format=%(refname)%00%(objectname)"],
                       capture_output=True, text=True)
    moved = {}
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            name, _, sha = line.partition("\x00")
            if name and sha and name in before_refs and before_refs[name] != sha:
                moved[name] = (before_refs[name], sha)
    end_wt = _worktree_branches(root)
    side = []
    for name, (old, new) in sorted(moved.items()):
        if _lane_worktree_moved(name, snap_wt, end_wt, root, sandbox):
            continue  # another lane's own worktree branch: not this run's parent
        # New refs are skipped: only refs that existed at the snapshot count.
        for sha in _scan_first_parent_range(root, old, new):
            side.append("%s %s" % (name, sha))
    if side:
        leaks.append("worker commits on moved parent branches: %s" % ", ".join(side))
    after_status = _filtered_parent_status(root)
    changed = sorted(p for p, xy in after_status.items()
                     if before_status.get(p) != xy)
    if changed:
        leaks.append("changed tracked paths in the parent checkout: %s" % ", ".join(changed))
    return leaks


def sandbox_diffstat(path: str, base: str) -> str:
    """One line saying how much the sandbox's worktree differs from `base`.

    Covers committed and uncommitted change in one read (`git diff` against a
    revision compares it with the worktree), which is what a read-only run's
    failure message has to name. A git that cannot answer returns "": the
    verdict does not depend on this string, only its detail does.
    """
    proc = subprocess.run(["git", "-C", path, "diff", "--shortstat", base],
                          capture_output=True, text=True)
    return proc.stdout.strip()


def _reflog_len(root: str, branch: str) -> int:
    r = subprocess.run(["git", "-C", root, "reflog", "show", "refs/heads/" + branch],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 0
    return len([ln for ln in r.stdout.splitlines() if ln.strip()])


# The return contract (docs/agent-protocol.md): a finished worker prints a
# REPORT heading, optionally wrapped in markdown (`**REPORT**`, `# REPORT:`).
# Prose that merely mentions a report is not one, and neither is a word that
# only starts with the same letters ("REPORTED").
REPORT_HEADING_RE = re.compile(r"(?im)^\s*(?:[#>*-]+\s*)?(?:\*\*)?REPORT(?:\*\*)?\b")

INCOMPLETE_MESSAGE = ("INCOMPLETE: the worker stopped without a REPORT - "
                      "relaunch it (never resume)")


def has_report(output: str) -> bool:
    """True when the captured client output carries a REPORT heading line."""
    return REPORT_HEADING_RE.search(output or "") is not None


def sandbox_verdict(route: dict, changed: str, ahead: str, output: str = "",
                    diffstat: str = ""):
    """(rc override or None, message) for an --isolate run.

    Measured 2026-09-25: t2-worker agents answered "all fixed" with placeholder
    commit hashes and changed nothing. A run whose job is to implement must
    leave a commit or a change; an agent's report is not evidence.

    SPAWNFIX (S3) item 1: a run that changed nothing AND never printed its
    REPORT did not finish the task at all (work/L1-routing/MUSEREG.try1.out),
    which is a different next action from a reported no-change: relaunch it.

    SPAWNFIX (S3) item 4: for a read-only run the empty sandbox IS the success,
    so a REPORT and no change exits 0 (work/L1-routing/R6RES.out and
    FOLD4MAP.out were graded NO-OP for doing exactly that) - and the change a
    writer run is rewarded for is the failure this one is judged by. The diff
    stat goes into the message because the run's work is now in the sandbox: the
    operator has to see what to throw away without another git command.
    """
    if route.get("review"):
        return None, ""
    if not changed and not ahead:
        if not has_report(output):
            return EXIT_INCOMPLETE, INCOMPLETE_MESSAGE
        if route.get("read_only"):
            return 0, "RESEARCH: report only"
        return 5, ("NO-OP: the agent changed nothing in its sandbox - treat its report as "
                   "unverified and the run as failed (exit 5)")
    if route.get("read_only"):
        return EXIT_READ_ONLY_WRITE, (
            "READ-ONLY WRITE: a read-only run changed its sandbox "
            "(exit %d) - its deliverable was the report, never the edit: %s"
            % (EXIT_READ_ONLY_WRITE, diffstat or "(no diff stat)"))
    return None, ""


def wip_commit(sandbox: str, rc: int, stop: str | None, branch: str | None = None) -> str | None:
    """Commit everything uncommitted in the sandbox; return the sha, or None.

    WIPfix (measured 2026-09-26 19:1x-19:3xZ): three --isolate workers were
    stopped by the provider right before `git commit`; the spawner printed
    "sandbox changes (uncommitted)" and exited, leaving the work to be
    recovered by hand. Commit it on the sandbox branch instead, so `take it:`
    always has a commit to fetch. The sandbox is a private clone with its push
    URL disabled, so this can never touch the parent. Untracked files are
    staged except logs/ (the spawner's own state_dir) and the git-ignored
    secrets (review WIPfix4: --exclude-standard alone honours the repo's own
    .gitignore, so the sandbox gets an explicit excludes file that also names
    configuration/api-keys.yml). A commit that does not land (e.g. an
    unmerged index the worker left behind) prints one stderr line and returns
    None; on a detached HEAD the sandbox branch is pointed at the WIP commit,
    so `take it: git fetch <path> <branch>` can fetch it.
    """
    msg = "WIP(autoos-agent): uncommitted at exit rc=%d" % rc
    if stop:
        msg += "; provider stop: %s" % stop
    # The clone carries this repo's .gitignore (which already names both of
    # these), but the WIP commit must hold even if that file is edited -
    # belt and braces via the sandbox's own excludes, never the user's.
    info = os.path.join(sandbox, ".git", "info")
    os.makedirs(info, exist_ok=True)
    with io.open(os.path.join(info, "exclude"), "a", encoding="utf-8") as fh:
        fh.write("configuration/api-keys.yml\nlogs/\n")
    others = subprocess.run(["git", "-C", sandbox, "ls-files", "--others",
                             "--exclude-standard", "-z"],
                            capture_output=True, text=True)
    add = [p for p in others.stdout.split("\0")
           if p and not _porcelain_path_is_logs(p)]
    if add:
        subprocess.run(["git", "-C", sandbox, "add", "--", *add],
                       capture_output=True, text=True)
    done = subprocess.run(["git", "-C", sandbox, "-c", "user.name=autoos-worker",
                           "-c", "user.email=" + WORKER_EMAIL, "commit", "-am", msg],
                          capture_output=True, text=True)
    if done.returncode != 0:
        reason = done.stderr.strip() or done.stdout.strip()
        print("WIP-COMMIT FAILED: %s" % (reason.splitlines()[0] if reason else
              "git commit exited %d" % done.returncode), file=sys.stderr)
        return None
    sha = subprocess.run(["git", "-C", sandbox, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    if not sha:
        return None
    if branch:
        current = subprocess.run(["git", "-C", sandbox, "branch", "--show-current"],
                                 capture_output=True, text=True).stdout.strip()
        if current != branch:
            # A detached sandbox HEAD (the worker checked out a sha) leaves
            # the WIP commit on no branch; point the sandbox branch at it so
            # `take it: git fetch <path> <branch>` has something to fetch.
            subprocess.run(["git", "-C", sandbox, "branch", "-f", branch, "HEAD"],
                           capture_output=True, text=True)
    return sha


def track_class(combo: str | None) -> str | None:
    """The track-record class for a combo, or None when it is not a tier combo."""
    if not combo:
        return None
    return TRACK_CLASS.get(combo.split("-", 1)[0])


def track_entry(plan: dict, rc: int, secs: float) -> dict | None:
    """The track-record line for a finished run, or None when it has no combo.

    The class is the route's registry class (RUNV2 sets ``route["class"]``),
    else the v1 tier prefix's class.

    Only a card or --tier run carries a combo (--free is keyless); a gateway
    run's served leg and tokens are unknown to this process, so they are
    recorded as unknown/0 until the resolver measures them. The effort is the
    rung RUNV2's route carries (``route["effort"]``) when there is one, else
    "unknown". rc is the same value the run exits with, the NO-OP (5), the
    headless refusal (6, failure class "refusal"), the LEAK (7, failure class
    "containment") and the INCOMPLETE (10, failure class "capability" like the
    NO-OP) and the READ-ONLY WRITE (11, "capability" too - a model that edits
    when told not to failed the instruction, which is answer quality) overrides
    included.

    ``bucket`` is the resolver's own bucket (RUNV2: ``route["bucket"]``, set
    only for a v2-routed run) when there is one, else the v1 compat card's
    ``bucket_hint`` (also unset today - v1's ``normalize`` never adds it),
    else "unknown".
    """
    route = plan["route"]
    client = clients.CLIENTS.get(plan.get("client"))
    if client is not None and not client.gateway:
        return None  # an own-account client never ran the gateway route it names
    if plan.get("free"):
        return None  # --free ran a keyless promo model, not the route it names
    klass = route.get("class") or track_class(route.get("combo"))
    if not klass:
        return None
    card = route.get("card") or {}
    # RUNV2's plan() carries the rung the resolver scored; a route whose leg is
    # not a reasoning model carries effort None and the resolver reads that as
    # "none". A v1/--tier route has no effort key at all: its record is stamped
    # "unknown", which p_success matches against any queried rung (REVFIX).
    if "effort" in route:
        effort = route.get("effort") or "none"
    else:
        effort = "unknown"
    return {
        "route": route["combo"],
        "class": klass,
        "served_leg": "unknown",
        "bucket": route.get("bucket") or card.get("bucket_hint") or "unknown",
        "effort": effort,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0,
        "latency_s": secs,
        "gate": "pass" if rc == 0 else "fail",
        "failure_class": (None if rc == 0 else ("capability" if rc in (
                              5, EXIT_INCOMPLETE, EXIT_READ_ONLY_WRITE) else
                          ("refusal" if rc == 6 else
                           ("containment" if rc == 7 else
                            ("provider" if rc == 8 else "logic"))))),
    }


def record_run(path: str, entry: dict) -> bool:
    """Append one track record; failing to record never changes the run's exit code."""
    try:
        track.record(path, entry)
        return True
    except (OSError, ValueError) as exc:  # a bad entry must not fail a finished run either
        print("autoos-agent: track record not written (%s): %s"
              % (path, getattr(exc, "strerror", None) or exc), file=sys.stderr)
        return False


def _available_legs(route: dict, registry: dict) -> list:
    """Leg strings of `route` that are actually available.

    Drops a leg whose own `unavailable_legs` entry or whose provider is
    unavailable now, read through ``registry.unavailable_now`` -- the one
    availability rule autoos_resolver.serving_legs applies, so this can never
    disagree with the resolver about an ``unavailable_until`` window. Order is
    the route's own leg order (the priority strategy needs the first one).
    """
    unavailable = route.get("unavailable_legs") or {}
    providers = registry.get("providers") or {}
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for leg in route.get("legs") or []:
        provider_id, _ = resolve_leg(leg, registry)
        if unavailable_now(unavailable.get(leg), now):
            continue
        if unavailable_now(providers.get(provider_id), now):
            continue
        out.append(leg)
    return out


def _leg_tool_calls(leg: str, registry: dict, overlay: dict):
    """The recorded tool-calling status of `leg`: the overlay's probed value
    (`legs[leg].tool_calls.value`) wins over the registry's declared
    `models[<model>].tool_calls`."""
    measured = (((overlay or {}).get("legs") or {}).get(leg) or {}).get("tool_calls") or {}
    if "value" in measured:
        return measured["value"]
    _, model_id = resolve_leg(leg, registry)
    return (registry.get("models") or {}).get(model_id, {}).get("tool_calls")


def probe_proposal(route_id, gate, failure_class, registry: dict, overlay: dict):
    """A re-probe proposal when a finished run contradicts the recorded
    tool-calling status of its route's legs, or None. Pure.

    gate == "pass" with a non-"proven" leg among the route's available legs
    proposes promoting it (kind "unexpected-pass"). gate == "fail" whose
    failure_class is "capability" (the class record_run gives the NO-OP guard)
    on a route whose FIRST available leg is already "proven" proposes
    re-probing a possible regression (kind "unexpected-fail"). An unknown
    route (not in the registry, e.g. a client-only run) is None, and so is
    every other gate/failure_class combination.
    """
    route = (registry.get("routes") or {}).get(route_id)
    if route is None:
        return None
    legs = _available_legs(route, registry)
    if not legs:
        return None
    command = "python3 tools/probe-toolcalls.py --route %s" % route_id
    if gate == "pass":
        unproven = [leg for leg in legs if _leg_tool_calls(leg, registry, overlay) != "proven"]
        if not unproven:
            return None
        return {"route": route_id, "legs": unproven, "kind": "unexpected-pass",
                "reason": "gated run passed on unproven tool calling: re-probe to promote",
                "command": command}
    if gate == "fail" and failure_class == "capability":
        first = legs[0]
        if _leg_tool_calls(first, registry, overlay) == "proven":
            return {"route": route_id, "legs": [first], "kind": "unexpected-fail",
                    "reason": "proven leg failed a run: re-probe (tool calling may have regressed)",
                    "command": command}
    return None


def record_probe_proposal(path: str, entry: dict) -> bool:
    """Append one probe-proposal JSON line; a write failure is printed and ignored
    (same contract as record_run)."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with io.open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return True
    except OSError as exc:
        print("autoos-agent: probe proposal not written (%s): %s"
              % (path, getattr(exc, "strerror", None) or exc), file=sys.stderr)
        return False


def propose_reprobe(entry: dict, registry_path: str, overlay_path: str,
                    proposals_path: str, sandbox_path: str) -> None:
    """Compute and log a probe proposal for a finished run's track entry.

    Never raises and never touches the run's exit code: a registry that
    cannot be loaded, or an overlay file present but unreadable, is a one-line
    stderr note and no proposal; a write failure is record_probe_proposal's
    own concern. `entry` is a track_entry()-shaped dict (route/gate/failure_class).
    """
    try:
        with io.open(registry_path, encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, ValueError) as exc:
        print("autoos-agent: probe proposal skipped (cannot load %s): %s"
              % (registry_path, getattr(exc, "strerror", None) or exc), file=sys.stderr)
        return
    overlay = {}
    if os.path.isfile(overlay_path):
        try:
            with io.open(overlay_path, encoding="utf-8") as fh:
                overlay = json.load(fh)
        except (OSError, ValueError) as exc:
            print("autoos-agent: probe proposal skipped (cannot load %s): %s"
                  % (overlay_path, getattr(exc, "strerror", None) or exc), file=sys.stderr)
            return
    try:
        proposal = probe_proposal(entry["route"], entry["gate"], entry["failure_class"], registry, overlay)
    except ValueError as exc:  # a leg in the registry itself does not resolve
        print("autoos-agent: probe proposal skipped (%s)" % exc, file=sys.stderr)
        return
    if proposal is None:
        return
    at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    proposal = dict(proposal, at=at, sandbox=sandbox_path)
    if record_probe_proposal(proposals_path, proposal):
        print("autoos-agent: PROBE-PROPOSAL: %s %s: %s"
              % (proposal["kind"], proposal["route"], proposal["command"]), file=sys.stderr)



def _terminate_group(proc, pgid) -> None:
    """Stop whatever is left of the client's process group (best effort)."""
    if os.name == "nt":
        subprocess.call(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class ClientExit(int):
    """A client's exit code, carrying the tail run_client captured.

    An int subclass, so every existing caller still compares it to a plain exit
    code; `.tail` is the last TAIL_LIMIT bytes of the client's merged
    stdout+stderr, decoded (bug 2 needs it to spot a headless refusal).
    """
    tail = ""
    refusal = None

    def __new__(cls, rc: int, tail: str = "", refusal: str | None = None):
        obj = super().__new__(cls, rc)
        obj.tail = tail
        obj.refusal = refusal
        return obj


def run_client(cmd, cwd: str, env: dict, reap: bool = True, capture: bool = False) -> int:
    """Run one client in its own process group; reap whatever it leaves behind.

    capture=True (CAPTURE_CLIENTS only): the child's stdout+stderr are merged,
    streamed to our stdout line by line (unbuffered), the last TAIL_LIMIT bytes
    are kept on ClientExit.tail, and the first headless-refusal line seen
    anywhere in the stream on ClientExit.refusal. capture=False: the child
    inherits our stdout/stderr, as before. Returns the client's exit code; KeyboardInterrupt is
    re-raised after cleanup. reap=False (a --joinable `claude --bg` session)
    leaves the group alone after exit code 0: that session is meant to outlive
    this spawner. A failed start is reaped.
    """
    # stdin closed: when it is an open pipe (cron, CI, an agent's shell)
    # `opencode run` waits to read it as extra prompt text and never starts
    # (measured 2026-09-24: 150 s hang vs 6 s with /dev/null).
    # leftovers (private Serena, language servers) survived a cancelled worker, measured 2026-09-25.
    pipe, merge = (subprocess.PIPE, subprocess.STDOUT) if capture else (None, None)
    if os.name == "nt":
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=pipe, stderr=merge,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        pgid = None
    else:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=pipe, stderr=merge,
                                start_new_session=True)
        pgid = proc.pid  # start_new_session makes the client its own group leader
    tail = bytearray()
    found = []

    def pump():
        try:
            for raw in proc.stdout:
                try:
                    sys.stdout.write(raw.decode("utf-8", "replace"))
                    sys.stdout.flush()
                except (OSError, ValueError):  # a closed/odd stdout must not kill the run
                    pass
                if not found:
                    hit = headless_refusal(raw.decode("utf-8", "replace"))
                    if hit:
                        found.append(hit)
                tail.extend(raw)
                if len(tail) > TAIL_LIMIT:
                    del tail[:len(tail) - TAIL_LIMIT]
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=pump, daemon=True)
    if capture:
        reader.start()
    try:
        rc = proc.wait()
    except BaseException:  # KeyboardInterrupt included: clean up, then re-raise
        _terminate_group(proc, pgid)
        if capture:
            reader.join(timeout=5)
        raise
    if reap or rc != 0:
        _terminate_group(proc, pgid)
        if capture:
            reader.join(timeout=5)
    elif capture:
        # A joinable session's background child may hold the pipe open; do not
        # block the spawner waiting on output that is not this run's anyway.
        reader.join(timeout=0.5)
    if capture and not reader.is_alive():  # a joinable session's background child owns the pipe
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass
    return ClientExit(rc, tail.decode("utf-8", "replace"), found[0] if found else None)


# --- the host-wide worker registry (`ps`) ---------------------------------
# One directory per host, shared by every worktree and clone: a spawned
# worker's own track record lives where the operator can find it, not in the
# clone it runs in. AUTOOS_WORKERS_DIR wins (the tests use it); otherwise the
# main checkout - the parent of git's common dir - so an --isolate clone (its
# own .git) inherits the parent's dir through the child env cmd_run exports.

def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(text):
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


# Windows process queries (V1/V2, ps final review): os.kill(pid, 0) is signal
# 0, which on Windows is CTRL_C_EVENT - GenerateConsoleCtrlEvent - so it would
# Ctrl+C a live worker and read it as dead. Query the process instead. One
# OpenProcess handle answers both _pid_alive and _proc_starttime.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259

_WIN_KERNEL32 = None


def _win_value_types():
    """The ctypes value types _win_liveness passes by reference - the ONE source
    for both the declared prototypes and the call arguments."""
    import ctypes
    from ctypes import wintypes
    return wintypes.DWORD, ctypes.c_ulonglong


def _win_kernel32():
    """kernel32 with explicit Win32 signatures, configured once per process.

    ctypes assumes a C ``int`` return, so a 64-bit HANDLE with a high bit set
    would come back truncated and then be CloseHandle'd as a different, invalid
    value. Declare restype/argtypes once and cache the DLL, never per call.
    """
    global _WIN_KERNEL32
    if _WIN_KERNEL32 is not None:
        return _WIN_KERNEL32
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    exit_code_t, stamp_t = _win_value_types()
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(exit_code_t)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    # A FILETIME is two little-endian DWORDs = one 64-bit integer: declared as
    # c_ulonglong so the prototype matches the byref() the caller passes (a
    # POINTER(FILETIME) prototype rejects byref(c_ulonglong) with ArgumentError).
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(stamp_t) for _ in range(4)]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    _WIN_KERNEL32 = k32
    return k32


def _win_liveness(pid):
    """(alive, creation_time) of a Windows pid, or (False, None) when gone.

    OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) with a NULL handle and
    ERROR_ACCESS_DENIED means the process exists but belongs to someone else:
    alive, start time unknown. GetExitCodeProcess == STILL_ACTIVE keeps it
    alive; GetProcessTimes' creation FILETIME (as an int) is the pid-reuse
    guard's start time, None when the process exists but its times are
    unreadable. Never touches a signal.
    """
    import ctypes
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False, None
    k32 = _win_kernel32()
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return (ctypes.get_last_error() == ERROR_ACCESS_DENIED), None
    try:
        exit_code_t, stamp_t = _win_value_types()
        code = exit_code_t()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False, None
        stamps = [stamp_t() for _ in range(4)]
        if k32.GetProcessTimes(handle, *(ctypes.byref(s) for s in stamps)):
            return True, stamps[0].value
        return True, None
    finally:
        k32.CloseHandle(handle)


def _proc_starttime(pid):
    """The process's start time: field 22 (clock ticks) of /proc/<pid>/stat on
    POSIX, or the GetProcessTimes creation FILETIME (as an int) on Windows;
    None when it cannot be read.

    The reuse guard: a recycled pid is a different process, so its start time
    no longer matches the one a record stored when it was alive.
    """
    if os.name == "nt":
        return _win_liveness(pid)[1]
    try:
        with io.open("/proc/%d/stat" % int(pid), encoding="utf-8") as fh:
            data = fh.read()
    except (OSError, ValueError, TypeError):
        return None
    rparen = data.rfind(")")
    if rparen < 0:
        return None
    fields = data[rparen + 1:].split()
    idx = 22 - 3  # /proc field 3 is the first token after the command name
    if len(fields) <= idx:
        return None
    try:
        return int(fields[idx])
    except ValueError:
        return None


def workers_dir() -> str:
    override = os.environ.get("AUTOOS_WORKERS_DIR")
    if override:
        path = override
    else:
        base = None
        try:
            out = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                                  "--git-common-dir"],
                                 cwd=ROOT, capture_output=True, text=True)
            common = out.stdout.strip()
            if out.returncode == 0 and common:
                base = os.path.dirname(common)
        except (OSError, subprocess.SubprocessError):
            base = None
        path = os.path.join(base or ROOT, "logs", "workers")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _write_worker_record(path: str, record: dict) -> None:
    tmp = "%s.tmp-%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    os.replace(tmp, path)


def _worker_record_start(plan: dict, args, directory: str):
    """Write the live record; return (id, record) for the ended rewrite."""
    wid = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-") + os.urandom(3).hex()
    pid = os.getpid()
    task = (args.task or "").splitlines()
    record = {"id": wid, "pid": pid, "pid_start": _proc_starttime(pid),
              "started": utc_now_iso(), "session_tag": plan.get("session_tag"),
              "client": plan.get("client"), "model": plan.get("model"),
              "route": (plan.get("route") or {}).get("combo") or "",
              "title": args.title or "", "cwd": plan.get("cwd"),
              "sandbox": (plan.get("sandbox") or {}).get("path", ""),
              "task_head": (task[0] if task else "")[:120], "depth": plan["depth"][0]}
    _write_worker_record(os.path.join(directory, wid + ".json"), record)
    return wid, record


def _worker_record_end(directory: str, wid: str, record: dict, rc) -> None:
    record = dict(record, ended=utc_now_iso(), rc=(int(rc) if rc is not None else None))
    _write_worker_record(os.path.join(directory, wid + ".json"), record)


def _pid_alive(pid) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) here is CTRL_C_EVENT, not a probe (V1).
        return _win_liveness(pid)[0]
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:  # alive, owned by someone else
        return True
    except OSError:
        return False
    return True


def _worker_state(record: dict) -> str:
    if record.get("ended"):
        return "exited rc=%s" % record.get("rc")
    pid = record.get("pid")
    if pid is None or not _pid_alive(pid):
        return "died"
    pid_start = record.get("pid_start")
    if pid_start is not None:
        current = _proc_starttime(pid)
        # A live pid whose start time cannot be read now (permissions, a
        # GetProcessTimes failure) skips the reuse guard: unknown is not a
        # mismatch, and list_workers' contract is to still call it running.
        if current is not None and current != pid_start:
            return "died"
    return "running"


def _fmt_elapsed(seconds) -> str:
    if seconds is None:
        return "?"
    secs = max(0, int(seconds))
    if secs < 60:
        return "%ds" % secs
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return "%dm%02ds" % (mins, secs)
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return "%dh%02dm" % (hours, mins)
    days, hours = divmod(hours, 24)
    return "%dd%02dh" % (days, hours)


def list_workers(directory: str, now=None, include_ended: bool = False) -> list:
    """Every spawned worker in ``directory`` as rows, newest last.

    state is "running" (alive, start time matches - the pid-reuse guard),
    "died" (gone or a recycled pid) or "exited rc=N". Records with no ``ended``
    whose process cannot be checked are not judged here; the pid-reuse guard is
    skipped only when a live pid's start time is unreadable (a Windows process
    owned by another user), which still counts as running. A record whose ended
    time (or, for a died worker, whose started time) is older than 7 days is
    deleted. A corrupt record is skipped, never raised on.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif not isinstance(now, datetime.datetime):
        now = _parse_iso(now) or datetime.datetime.now(datetime.timezone.utc)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    cutoff = now - datetime.timedelta(days=7)
    rows = []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with io.open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        try:
            state = _worker_state(record)
        except Exception:  # noqa: BLE001 - one unjudgeable record never breaks ps
            state = "unknown"
        started = _parse_iso(record.get("started"))
        ended = _parse_iso(record.get("ended"))
        age_ref = ended if ended is not None else (started if state == "died" else None)
        if age_ref is not None and age_ref < cutoff:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        if state.startswith("exited") and not include_ended:
            continue
        ref_end = ended if ended is not None else now
        secs = (ref_end - started).total_seconds() if started is not None else None
        rows.append({"id": record.get("id"), "state": state,
                     "elapsed": _fmt_elapsed(secs), "elapsed_seconds": secs,
                     "client": record.get("client") or "", "model": record.get("model") or "",
                     "lane": record.get("session_tag") or "", "pid": record.get("pid"),
                     "title": record.get("title") or "", "task": record.get("task_head") or "",
                     "cwd": record.get("cwd") or "", "sandbox": record.get("sandbox") or "",
                     "started": record.get("started"), "ended": record.get("ended"),
                     "rc": record.get("rc"), "depth": record.get("depth")})
    rows.sort(key=lambda r: (r.get("started") or "", r.get("id") or ""))
    return rows


ENDED_VISIBLE_WINDOW = datetime.timedelta(hours=24)


def visible_workers(directory: str, include_ended: bool = False, now=None) -> list:
    """The rows both ps surfaces show (V3 ps final review).

    `list_workers` with the one ``--all`` window the CLI and the MCP tool
    share: with include_ended, an ended row is shown only when it *ended*
    within the last 24 h - a long run that ended an hour ago is in, a short
    one that ended three days ago is out. Running and died rows always pass;
    without include_ended exited rows are already gone from `list_workers`.
    ``now`` is a datetime, an ISO string or None (as in `list_workers`).
    """
    rows = list_workers(directory, now=now, include_ended=include_ended)
    if not include_ended:
        return rows
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif not isinstance(now, datetime.datetime):
        now = _parse_iso(now) or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - ENDED_VISIBLE_WINDOW
    return [r for r in rows if not r["state"].startswith("exited")
            or (_parse_iso(r["ended"]) or cutoff) > cutoff]


def _print_worker_table(rows: list) -> None:
    head = ["ID", "STATE", "ELAPSED", "CLIENT", "MODEL", "LANE", "PID", "TITLE/TASK"]
    cells = [[r["id"], r["state"], r["elapsed"] or "-", r["client"], r["model"],
              r["lane"], str(r["pid"] or ""), r["title"] or r["task"] or ""] for r in rows]
    widths = [max(len(head[i]), max(len(c[i]) for c in cells)) for i in range(len(head))]
    width = shutil.get_terminal_size((120, 24)).columns
    avail = max(15, width - sum(widths[:-1]) - 2 * (len(head) - 1))
    widths[-1] = min(widths[-1], avail)
    fmt = "  ".join("%-" + str(w) + "s" for w in widths)
    print(fmt % tuple(head))
    for cell in cells:
        cell = list(cell)
        cell[-1] = cell[-1][:widths[-1]]
        print(fmt % tuple(cell))


def cmd_ps(args) -> int:
    directory = workers_dir()
    rows = visible_workers(directory, include_ended=args.all)
    if args.json:
        print(json.dumps({"workers": rows, "dir": directory}, indent=1))
        return 0
    if not rows:
        print("no workers running")
        return 0
    _print_worker_table(rows)
    return 0


def cmd_run(args, cfg: dict) -> int:
    # R-pause-01/R-heartbeat-03: a hard stop, checked before every launch. Only
    # when the caller names an inbox - a run with no AUTOOS_AGENT_INBOX set is
    # not policed here (e.g. an interactive, watched run). AUTOOS_AGENT_TRANSCRIPT
    # (the caller's session transcript) makes a PAUSE older than that session
    # history: the relaunch after a pause is its resume.
    inbox = os.environ.get("AUTOOS_AGENT_INBOX")
    if inbox:
        pause = heartbeat.pause_state(inbox, since=heartbeat.session_start(
            os.environ.get("AUTOOS_AGENT_TRANSCRIPT")))
        if pause["active"]:
            return refuse("PAUSE active (%s): %s" % (pause["at"], pause["text"]), 3)
    # FUP (2026-09-27): refuse an empty or whitespace-only task before any
    # clone or client start (measured: $(cat missing-file) produced '' and
    # a worker chatted twice before the route planner caught it).
    if not args.task or not args.task.strip():
        return refuse("task is empty or whitespace-only", 2)
    # SPAWNCAP (S2): decide the client from the task's shell/write needs before
    # anything is planned or started. An explicit --client that lacks one is
    # refused with the capable clients named; with no --client the first capable
    # one is chosen (opencode, today's default when nothing is required).
    required = required_capabilities(args)
    registry = None
    if required:
        try:
            registry = load_registry(REGISTRY_PATH)
        except (OSError, ValueError) as exc:
            return refuse("cannot load client capabilities: %s" % exc)
    if args.client is None:
        args.client = choose_client(required, registry)
        if args.client is None:
            return refuse("no client declares %s, which this task needs" % " and ".join(required))
    else:
        refusal = capability_refusal(args.client, required, registry)
        if refusal is not None:
            return refuse(refusal)
    client = clients.CLIENTS[args.client]
    # SPAWNFREE (S2) item 3: a mode the CLI does not offer is not rejected by
    # the CLI - qodercli 1.1.63 took `--permission-mode accept_edits`, ignored
    # it, and refused every write (62 runs). Check the adapter's modes against
    # the client's own --help (cached per binary version) before anything is
    # cloned, planned or started.
    modes_ok, modes_why = clients.check_client_modes(client)
    if modes_ok is False:
        return refuse(modes_why)
    if args.free and args.clean:
        return refuse("--free uses promo models that may train on prompts; it cannot be --clean.")
    if args.tier is not None and args.card is not None:
        return refuse("--tier and --card both pick the model; pass one of them.")
    if args.card is not None and args.clean:
        return refuse("--clean is for --tier; with a card say privacy=sensitive.")
    if args.free and client.name != "opencode":
        return refuse("--free is opencode's own free model; --client %s cannot use it." % client.name)
    if args.joinable and client.name != "claude":
        return refuse("--joinable is a Claude Code --bg --remote-control session; only --client claude.")
    try:
        plan = build_plan(args, cfg)
    except clients.DepthError as exc:
        return refuse(str(exc), 4)
    except (RouteInputRequired, RouteDeferred, PrivacyRefused) as exc:  # plan's / PRIV3's own
        return refuse(str(exc))                        # message, no suffix added
    except ValueError as exc:  # CardError, NoRoute, an undeclared model
        return refuse("%s (see: tools/autoos-agent.py list)" % exc)
    route = plan["route"]
    # REVROUTE (S2) item 2: an authored review card needs an eligible reviewer
    # before anything is started -- a review by the author's own model family is
    # not an independent one, and "everyone is rate-limited" is a wait (rc 9,
    # the same code SPAWNFREE's queue loop already handles), not a silent pass.
    refusal = review_run_refusal(route.get("review_plan"))
    if refusal is not None:
        return refusal
    if client.promo and route["privacy"] != "public":
        return refuse("%s is a promo client that may keep prompts; it runs privacy=public work only." % client.name)
    # SPAWNFREE (S2) item 4: --lean is only a hard error where it cannot be
    # honoured *and* the run needs what it asks for (lean_decision).
    lean_note = None
    if args.lean:
        lean_note, lean_refusal = lean_decision(client.name, route)
        if lean_refusal is not None:
            return refuse(lean_refusal)
    uses_key = client.gateway and not args.free
    env_names = sorted(plan["env"]) + (["AUTOOS_OMNIROUTE_KEY"] if uses_key else [])
    print("route: %s reason=%s routing=%s" % (route["combo"] or plan["model"], route["reason"],
                                              routing.ROUTING_VERSION))
    if route.get("review_plan"):
        # who reviews, and who was passed over -- an operator reading a spawn
        # should not have to re-run `route --explain` to see the family rule work.
        reviewer = route["review_plan"]["reviewer"]
        print("reviewer: %s %s (family %s, author %s)" % (
            reviewer["client"], reviewer["model"], reviewer["family"],
            route["review_plan"]["author_family"]))
        # Item 5: the line that has to end up in the lane record for the lane to
        # read as reviewed. Filling in the verdict is the reviewer's job at the
        # end of the run, not the spawner's guess at the start of it.
        print("record-line: AutoOS-Review: kind=cross-family author=%s reviewer=%s "
              "verdict=<fill in>" % (route["card"].get("author"), reviewer["model"]))
        for line in resolver.reviewer_explain_lines(route["review_plan"]):
            print(line)
    if route.get("reviewer_note"):
        print(route["reviewer_note"])
    print("depth: %d/%d" % plan["depth"])
    if route.get("read_only"):
        print("read-only: this run's success is an unchanged sandbox and a REPORT "
              "(an edit in it exits %d)" % EXIT_READ_ONLY_WRITE)
    if plan.get("session_tag"):
        print("session-tag: %s" % plan["session_tag"])
    if args.lean:
        if lean_note:
            print("note: %s; this run is read-only, so its servers cost memory, not safety" % lean_note)
        else:
            print("lean: no %s" % (", ".join(LEAN_DROP) if client.name == "opencode" else "MCP servers"))
    if plan["sandbox"] and client.name != "opencode":
        print("note: --isolate gives %s a private clone as its cwd; the outside-path fence is "
              "opencode-only, but every client gets the containment prompt line and the "
              "post-run leak check (exit 7)." % client.name)
    if args.dry_run:
        if plan["sandbox"]:
            print("would run: git clone --local %s %s && git switch -c %s" % (ROOT, plan["sandbox"]["path"], plan["sandbox"]["branch"]))
        print("would run: " + " ".join(shlex.quote(c) for c in plan["cmd"]))
        print("cwd: %s" % plan["cwd"])
        print("env: %s" % (", ".join(env_names) or "-"))
        return 0
    if not shutil.which(plan["cmd"][0]):
        return refuse("%s is not installed (catalog: ./setup.sh --only <id> -y); see: list" % plan["cmd"][0], 3)
    ok, reason = clients.signin_state(client)
    if ok is False:
        what = "installed but not signed in" if clients.signed_out(reason) else "installed but not usable"
        return refuse("%s is %s: %s. Run `%s` once interactively to sign in (own account, "
                      "not the gateway); see: list" % (client.binary, what, reason.rstrip("."), client.binary), 3)
    # PWD too, not just cwd=: opencode takes the project directory from $PWD,
    # so an inherited PWD sent an isolated worker's writes to the caller's
    # checkout (live 2026-09-24).
    env = dict(os.environ, **plan["env"], PWD=plan["cwd"])
    env.pop("AUTOOS_OMNIROUTE_KEY", None)
    if uses_key:
        key = client_key(ROOT)
        if not key:
            print("No OmniRoute client key: export AUTOOS_OMNIROUTE_KEY or add 'omniroute:' to "
                  "configuration/api-keys.yml (or use --free for a keyless run).", file=sys.stderr)
            return 3
        if not gateway_up():
            print("OmniRoute is not answering on %s - start it: configuration/start-stack.sh "
                  "(or use --free)." % GATEWAY, file=sys.stderr)
            return 3
        env["AUTOOS_OMNIROUTE_KEY"] = key
    # SPAWNCAP (S2): the route ids a provider-stopped attempt has already
    # burned, so a fallthrough re-run never picks one of them again.
    excluded_routes = set()
    # SPAWNFREE (S2) item 1: and the free models it burned. A --free run's
    # model is not a route, so the resolver's fallthrough never reached it -
    # the first 'Rate limit exceeded' ended the run (inbox 2026-09-27T17:08:22Z
    # and 17:19:45Z, work/L1-routing/T1FREE.r2.out).
    excluded_free_models = set()
    # How many re-runs this run has already started (route or free model): the
    # one bound MAX_FALLTHROUGH is about.
    fallthroughs = 0
    free_chain = None
    free_policy = None
    if args.free:
        if registry is None:
            try:
                registry = load_registry(REGISTRY_PATH)
            except (OSError, ValueError):
                registry = None  # an unreadable registry leaves one free model
        free_policy = (registry or {}).get("policy") or {}
        free_chain = free_model_chain(free_policy, client.name, args.free_model)
    # SPAWNFREE (S2) item 2: a free provider is one shared account, so a second
    # worker on it does not add capacity, it adds a 429. Wait for a slot before
    # anything is cloned or started (a queue that outlasts the bound costs
    # nothing but time and exits 9).
    # SPAWNFIX (S2) item 2: and claim it while holding the free-leg lock, so a
    # second spawner that starts at the same moment counts this one instead of
    # the empty leg. The claim lives until this run's worker record exists.
    free_reservation = None
    if args.free:
        queue_msg, free_reservation = free_slot_refusal(plan, free_policy)
        if queue_msg is not None:
            return refuse(queue_msg, EXIT_FREE_QUEUE_TIMEOUT)
    parent_snap = None
    if plan["sandbox"]:
        sb = plan["sandbox"]
        # Snapshot the parent checkout before the run: a worker that writes
        # outside its clone (live 2026-09-26) must fail as a leak, not a NO-OP.
        parent_snap = parent_snapshot()
        os.makedirs(os.path.dirname(sb["path"]), exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--local", ROOT, sb["path"]], check=True)
        # The orchestrator still fetches from the sandbox path (unchanged);
        # only the push URL is disabled, so `git push` from the sandbox
        # cannot update the parent's branches.
        subprocess.run(["git", "-C", sb["path"], "remote", "set-url", "--push",
                        "origin", ISOLATE_PUSH_DISABLED], check=True)
        subprocess.run(["git", "-C", sb["path"], "switch", "-q", "-c", sb["branch"]], check=True)
        sb["base"] = subprocess.run(["git", "-C", sb["path"], "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        print("sandbox: %s (branch %s)" % (sb["path"], sb["branch"]))
    start = time.time()
    # Every finished --isolate run is captured (tee'd to our stdout), so the
    # WIPfix provider-stop check has the child's tail. A --joinable launcher
    # (claude --bg) exits while its session lives: its tail is a partial
    # mid-flight score, so it is never captured and never stop-checked or
    # WIP-committed - exactly as before WIPfix. CAPTURE_CLIENTS keeps its own.
    capture = client.name in CAPTURE_CLIENTS or (bool(plan["sandbox"]) and not args.joinable)
    while True:
        # SPAWNFREE (S2) item 2: a fallthrough re-run is a fresh start on the
        # same shared free account, so it passes the same gate (the first
        # attempt passed it before the clone was made). Breaking here still
        # runs the sandbox summary below, so the WIP commit the stopped
        # attempt left is announced. A --free run never reaches the track
        # record (track_entry returns None for it), so a queue timeout costs
        # the route nothing.
        if args.free and fallthroughs:
            queue_msg, free_reservation = free_slot_refusal(plan, free_policy)
            if queue_msg is not None:
                print("autoos-agent: %s" % queue_msg, file=sys.stderr)
                rc = EXIT_FREE_QUEUE_TIMEOUT
                break
        # Worker registry (ps): one record per attempt (a SPAWNCAP fallthrough
        # is its own record), live for the whole run, so `ps` shows this spawn
        # from any checkout. The child inherits the dir so a nested spawn in an
        # --isolate clone (its own .git) records in the same host-wide place.
        # A registry problem (disk full, read-only dir, a ctypes error) never
        # stops the client or replaces its rc: the run then shows as died.
        workers = worker_id = worker_rec = None
        try:
            workers = workers_dir()
            env["AUTOOS_WORKERS_DIR"] = workers
            worker_id, worker_rec = _worker_record_start(plan, args, workers)
        except Exception as exc:  # noqa: BLE001
            print("autoos-agent: could not write worker record: %s" % exc, file=sys.stderr)
        if worker_id is not None:
            # The record is the thing every count reads, so the placeholder this
            # run claimed the slot with has done its job (SPAWNFIX item 2). It is
            # kept when there is no record, so a run nobody can see still cannot
            # share the leg with one.
            free_reservation_release(free_reservation)
            free_reservation = None
        attempt_start = time.time()
        run_rc = None
        try:
            run_rc = run_client(plan["cmd"], plan["cwd"], env, reap=not args.joinable,
                                capture=capture)
        finally:
            if worker_id is not None:
                try:
                    _worker_record_end(workers, worker_id, worker_rec, run_rc)
                except OSError as exc:
                    print("autoos-agent: could not update worker record %s: %s"
                          % (worker_id, exc), file=sys.stderr)
        client_tail = getattr(run_rc, "tail", "") or ""
        rc, refusal = refusal_exit(int(run_rc), getattr(run_rc, "refusal", None) or "")
        child_rc = rc  # the WIP message names the client's own rc, not a verdict override
        if refusal is not None:
            print("autoos-agent: HEADLESS-REFUSAL: %s" % refusal, file=sys.stderr)
        # A provider stop is a failure even though the client often exited 0: it
        # was cut off mid-task (WIPfix, 2026-09-26). rc 0 and 6 upgrade to 8; a
        # LEAK (7) still wins below, and neither the NO-OP (5) nor the
        # INCOMPLETE (10) verdict fires on an 8.
        # AGYFIX item 3 (measured 2026-09-27, K3 audit addendum 08:1xZ): agy with
        # no --model exits 3 after its default Gemini quota runs out, so rc 3 joins
        # them - the provider_stop() tail check still gates the upgrade, and other
        # rc-3 runs (missing binary is the spawner's own 3, set earlier) never
        # reach here with a provider-stop line.
        # Exit precedence 7 > 8 > 5/10 > 6: a HEADLESS-REFUSAL (6) run that is then
        # provider-stopped exits 8 with failure_class "provider" (agy measured:
        # jetski refusal + AGY_ERROR 429, R-gateway-12).
        # FUP (2026-09-27): record_probe runs AFTER the provider stop upgrade so
        # a promo client whose tail is a provider stop does not get a false probe.
        stop = provider_stop(client_tail)
        if stop is not None:
            # REVROUTE (S2) item 3: when the stop line states its own reset,
            # that window becomes the provider's unavailable_until for every
            # later routing read - the next task goes elsewhere until then.
            recorded = record_reset_stop(stop, plan["route"].get("combo"),
                                         load_live_registry())
            if recorded:
                print("provider %s unavailable until %s" % recorded)
        if stop is not None and rc in (0, 3, 6):
            print("autoos-agent: PROVIDER-STOP: %s" % stop, file=sys.stderr)
            rc = 8
        if rc == 0 and client.promo:
            clients.record_probe(client.name)
        # SPAWNCAP (S2): a provider-stopped resolver-routed --isolate run
        # re-runs the SAME task in the SAME sandbox on the next route (WIPfix
        # preserved the work but stranded it on a dead route). At most
        # MAX_FALLTHROUGH re-runs; a --joinable/--tier/v1 run keeps today's
        # immediate exit 8 (its combo is not the resolver's to replace).
        # SPAWNFREE (S2) item 1: a --free run has no route to fall through to
        # (its model is the caller's --free-model, not the resolver's choice),
        # so it falls through the registry's ordered free-model list instead.
        fell_through = False
        if (stop is not None and not args.joinable
                and fallthroughs < MAX_FALLTHROUGH):
            next_plan = None
            fell_from = fell_to = None
            if args.free:
                next_plan, fell_from, fell_to = _free_fallthrough_plan(
                    args, cfg, plan, free_chain, excluded_free_models)
            elif plan["route"].get("resolver") and plan["sandbox"]:
                try:
                    next_plan = build_plan(args, cfg,
                                           exclude_routes=excluded_routes | {plan["route"]["combo"]},
                                           sandbox=plan["sandbox"])
                except (clients.DepthError, RouteInputRequired, RouteDeferred, PrivacyRefused,
                        ValueError):
                    next_plan = None  # no route left (or unplannable): exit 8 below
                next_combo = ((next_plan or {}).get("route") or {}).get("combo")
                if not next_combo or next_combo == plan["route"]["combo"]:
                    next_plan = None
                else:
                    fell_from, fell_to = plan["route"]["combo"], next_combo
                    excluded_routes.add(plan["route"]["combo"])
            if next_plan is not None:
                fell_through = True
                fallthroughs += 1
                # Preserve this stopped attempt before leaving its checkout: the
                # re-run shares the sandbox (and its branch), so the WIP commit is
                # what the final `take it:` and the track record see.
                # SPAWNFIX (S2) item 1: a run without --isolate has no checkout to
                # preserve (its cwd is the caller's own tree, which we never
                # commit into) — the guard is the same one the route branch above
                # and the post-loop summary below use. It still falls through.
                if plan["sandbox"]:
                    sb = plan["sandbox"]
                    changed = subprocess.run(["git", "-C", sb["path"], "status", "--short"],
                                             capture_output=True, text=True).stdout.strip()
                    if changed and not plan["route"].get("review"):
                        wip_sha = wip_commit(sb["path"], child_rc, stop, sb["branch"])
                        if wip_sha:
                            print("WIP-COMMITTED: %s" % wip_sha)
                # REVFIX: each provider-stopped attempt is its own observation,
                # not just the final plan's. Without this the dead route looked
                # healthy (no fail record), so the resolver kept handing it the
                # work. (A --free stop records nothing: the route was never the
                # thing that failed, the promo model was, and the survivor's own
                # record follows the loop.)
                if not args.free:
                    stopped = track_entry(plan, rc, time.time() - attempt_start)
                    if stopped is not None:
                        record_run(TRACK_RECORD, stopped)
                print(fallthrough_line(fell_from, stop, fell_to), file=sys.stderr)
                plan = next_plan
                # The re-run runs under the new plan's env (its OPENCODE_CONFIG_CONTENT
                # and session tag), not the stopped route's (qoder review 2026-09-27).
                env = dict(os.environ, **plan["env"], PWD=plan["cwd"])
                env.pop("AUTOOS_OMNIROUTE_KEY", None)
                if uses_key:
                    env["AUTOOS_OMNIROUTE_KEY"] = key
        if not fell_through:
            break
    # Nothing that runs after here occupies the free leg: whatever is left of the
    # summary is bookkeeping, and a placeholder with no run behind it would make
    # the next spawner wait for nothing.
    free_reservation_release(free_reservation)
    free_reservation = None
    if plan["sandbox"] and not args.joinable:
        sb = plan["sandbox"]
        branch = sb["branch"]
        changed = subprocess.run(["git", "-C", sb["path"], "status", "--short"],
                                 capture_output=True, text=True).stdout.strip()
        # WIPfix: never lose a worker's uncommitted work. Commit it on the
        # sandbox branch, then re-read changed/ahead so a run that only ever
        # produced this WIP commit is no longer a NO-OP. A review run's
        # deliverable is its diff, so leave that one untouched.
        if changed and not plan["route"].get("review"):
            wip_sha = wip_commit(sb["path"], child_rc, stop, branch)
            if wip_sha:
                print("WIP-COMMITTED: %s" % wip_sha)
                changed = subprocess.run(["git", "-C", sb["path"], "status", "--short"],
                                         capture_output=True, text=True).stdout.strip()
        ahead = subprocess.run(["git", "-C", sb["path"], "log", "--oneline",
                                sb["base"] + ".." + branch],
                               capture_output=True, text=True).stdout.strip()
        print("\nsandbox changes (uncommitted):\n" + (changed or "  (none)"))
        if ahead:
            print("sandbox commits:\n" + ahead)
        q = shlex.quote(sb["path"])
        print("review:  git -C %s diff" % q)
        print("take it: git fetch %s %s   (then review FETCH_HEAD)" % (q, sb["branch"]))
        extra = " " + shlex.quote(sb["path"] + ".opencode-data") if client.name == "opencode" else ""
        print("discard: rm -rf %s%s" % (q, extra))
        override, message = sandbox_verdict(
            plan["route"], changed, ahead, client_tail,
            # Only the read-only verdict quotes it, so only the read-only run
            # pays for the extra git call.
            sandbox_diffstat(sb["path"], sb["base"])
            if plan["route"].get("read_only") else "")
        leak = parent_leak(parent_snap, sandbox=sb["path"])
        if leak:
            # A LEAK overrides the child's rc AND the NO-OP verdict: the run
            # did change something, just in the wrong checkout. Never reverts
            # anything.
            print("LEAK: the --isolate run wrote outside its sandbox "
                  "(containment failure, exit 7): %s" % "; ".join(leak), file=sys.stderr)
            if any(ln.startswith("worker commits in the parent checkout") for ln in leak):
                # LEAKFP2: no code can tell the orchestrator's own mid-run merge
                # of this parent from the worker's write, so the message names
                # the procedural rule instead of exempting the case.
                print("LEAK: if you moved this branch yourself during the run "
                      "(merge/ff), this is expected - do not move a parent while "
                      "its child runs (skill R-coord-01)", file=sys.stderr)
            rc = 7
        elif override is not None and rc == 0:
            print(message)
            rc = override
        # Every finished --isolate run is a track-record observation (spec §5.6).
        # REVFIX review 2: the survivor's latency is its OWN attempt, not the
        # cumulative `start` that also spans the dead attempts (each of which
        # already recorded its own sample above).
        tracked = track_entry(plan, rc, time.time() - attempt_start)
        if tracked is not None:
            record_run(TRACK_RECORD, tracked)
            propose_reprobe(tracked, REGISTRY_PATH, MEASURED_OVERLAY_PATH,
                            PROBE_PROPOSALS_LOG, sb["path"])
    # Log the FINAL rc: the LEAK override happens above and the log, the
    # orch-*.log watchdog and the track record must not disagree.
    log_run(plan, rc, time.time() - start, args.free)
    return rc


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["usage"]:  # everything after `usage` belongs to autoos_usage
        return usage_mod.main(list(argv[1:]))
    ap = argparse.ArgumentParser(description="Spawn one AutoOS tier agent (see module docstring).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("usage", help="usage report by provider/combo/lane from the OmniRoute "
                                 "gateway (OR4); its own flags follow `usage`, e.g. "
                                 "`usage --since 1h --by provider,lane`")
    sub.add_parser("list", help="show the tiers, their models and who may spawn whom")
    ps = sub.add_parser("ps", help="live table of every spawned worker on this host (all "
                                   "worktrees and clones); deletes records that ended "
                                   "(or died) more than 7 days ago")
    ps.add_argument("--all", action="store_true",
                    help="also show exited workers from the last 24 h")
    ps.add_argument("--json", action="store_true", help="print the rows as JSON")
    run = sub.add_parser("run", help="run one task on one tier")
    run.add_argument("--tier", type=int, choices=sorted(TIERS),
                     help="pick the tier by hand (default: resolve --card, an empty card is t2-worker)")
    run.add_argument("--card", help="task card, e.g. role=review,privacy=sensitive (or JSON)")
    run.add_argument("--no-defer", dest="no_defer", action="store_true",
                     help="a v2 card (RUNV2): ignore the resolver's deferral (state=deferred) "
                          "and run now instead of refusing with exit 2")
    run.add_argument("--allow-training", action="store_true",
                     help="accepted for compatibility; since 2026-09-27 the only 1M leg is off, so this no longer unlocks a route")
    run.add_argument("--client", choices=sorted(clients.CLIENTS), default=None,
                     help="agent CLI to spawn (default: the first client that declares the "
                          "capabilities the task needs, opencode when it needs none)")
    run.add_argument("--joinable", action="store_true",
                     help="claude only: a background session you can join through Remote Control")
    run.add_argument("--max-depth", type=int, help="lower the depth budget for this child's subtree")
    run.add_argument("--clean", action="store_true", help="use the -clean (paid, no free legs) twin")
    run.add_argument("--model", help="a model declared in opencode.jsonc, e.g. omniroute/t2-orchestrator")
    run.add_argument("--free", action="store_true", help="keyless: every tier on opencode's free model")
    run.add_argument("--free-model", default=DEFAULT_FREE_MODEL)
    run.add_argument("--isolate", action="store_true",
                     help="run in a private git clone on its own branch; writes outside it are denied")
    run.add_argument("--no-auto", dest="auto", action="store_false",
                     help="ask before tools the config does not explicitly allow (default: --auto)")
    run.add_argument("--lean", action="store_true",
                     help="no heavy MCP servers (%s) - for research/review agents" % ", ".join(LEAN_DROP))
    run.add_argument("--read-only", dest="read_only", action="store_true",
                     help="a research run: an unchanged sandbox plus a REPORT is success (exit 0), an "
                          "edit in it is the failure (exit 11). A v2 card kind=research marks the same "
                          "run without this flag")
    run.add_argument("--title")
    run.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    run.add_argument("task")
    context = sub.add_parser("context", help="print this session's context fill")
    context.add_argument("--transcript", help="a Claude Code transcript JSONL (default: discover)")
    context.add_argument("--model", help="override the model the cap is looked up for")
    context.add_argument("--json", action="store_true", help="print the fill as JSON")
    heartbeat_p = sub.add_parser("heartbeat", help="read-only pause/branch/context check "
                                 "(R-heartbeat-02/03, R-pause-01, R-handoff-07)")
    heartbeat_p.add_argument("--inbox", help="an inbox file to scan for the newest PAUSE/RESUME line")
    heartbeat_p.add_argument("--transcript", help="a Claude Code transcript JSONL (default: discover)")
    heartbeat_p.add_argument("--repo", dest="repos", action="append",
                             help="a git repo to check for unpushed/dirty state (default: cwd); repeatable")
    heartbeat_p.add_argument("--cap", type=int, help="override the model's hand-off cap (tokens)")
    heartbeat_p.add_argument("--json", action="store_true", help="print the report as one JSON object")
    route = sub.add_parser("route", help="print the resolver v2 route_plan for a task card")
    route.add_argument("--card", required=True,
                       help="task card, e.g. kind=review,paths=tools/registry.py (or JSON)")
    route.add_argument("--brief", default="", help="the task brief text (counts toward need_tokens)")
    route.add_argument("--explain", action="store_true",
                       help="print the explain lines and reason to stderr before the JSON")
    route.add_argument("--orchestrator-model", dest="orchestrator_model",
                       default=DEFAULT_ORCHESTRATOR_MODEL,
                       help="registry model id billed for verification (default: %(default)s)")
    route.add_argument("--repo", help="repo root to measure against (default: this checkout)")
    route.add_argument("--now", help="ISO 8601 UTC clock reading (default: now)")
    review_status_p = sub.add_parser(
        "review-status", help="read a lane record and report whether it carries both "
                              "reviews a ready lane needs: a cross-family review and the "
                              "final check (REVROUTE)")
    review_status_p.add_argument("record", help="the lane record (status/<lane>.<name>.md), or - for stdin")
    review_status_p.add_argument("--registry",
                                 help="registry to resolve model families against "
                                      "(default: catalog/ai-registry.json)")
    ready_p = sub.add_parser(
        "ready", help="declare a lane ready INSTEAD of typing the inbox line by "
                      "hand: gate on review-status and on --sha being the tip of "
                      "origin/--branch, then append one line to the controller's "
                      "inbox (REVGATE)")
    ready_p.add_argument("record", help="the lane record (status/<lane>.<name>.md), or - for stdin")
    ready_p.add_argument("--branch", required=True,
                         help="the lane branch, checked as refs/heads/<branch> on origin")
    ready_p.add_argument("--sha", required=True,
                         help="the commit the lane is ready at; must equal origin's tip for --branch")
    ready_p.add_argument("--inbox", required=True,
                         help="the controller's inbox file to append the ready line to "
                              "(created if missing, never rewritten)")
    ready_p.add_argument("--registry",
                         help="registry to resolve model families against "
                              "(default: catalog/ai-registry.json)")
    ready_p.add_argument("--repo",
                         help="git checkout to ask origin about (default: the cwd)")
    ready_p.add_argument("--ci-run", dest="ci_run", metavar="RUN_ID",
                         help="a GitHub Actions run that tested --sha: it must have "
                              "conclusion=success AND headSha=--sha, else the lane is not "
                              "ready (exit 1) or the gate could not be read (exit 2). Its "
                              "id is appended to the inbox line as ci=<RUN_ID>; without it "
                              "the lane is still allowed and one note says so")
    ready_p.add_argument("--dry-run", action="store_true",
                         help="print the line, append nothing")
    args = ap.parse_args(argv)
    if args.cmd == "context":
        return cmd_context(args)
    if args.cmd == "heartbeat":
        return cmd_heartbeat(args)
    if args.cmd == "route":
        return cmd_route(args)
    if args.cmd == "review-status":
        return cmd_review_status(args)
    if args.cmd == "ready":
        return cmd_ready(args)
    if args.cmd == "ps":
        return cmd_ps(args)
    cfg = load_jsonc(os.path.join(ROOT, "opencode.jsonc"))
    return cmd_list(cfg) if args.cmd == "list" else cmd_run(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
