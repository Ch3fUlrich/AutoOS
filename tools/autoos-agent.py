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
  5. A spawned tier (2 or 3) is refused without --isolate (KEYDENY3b/KEYDENY3g):
     grep/glob is fenced on the search *pattern*, so a worker grepping "sk-" in a
     working checkout walks straight through it into
     configuration/api-keys.yml. The clone is the control that holds — an ignored
     file is not in it. Tier 1 (role=orchestrate) is the only tier that may still
     run in the caller's checkout.

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

Run identity: one canonical id per spawn, minted once in UTC as
`YYYYMMDD-HHMMSS-<slug>-<hex6>` (slug from --title else the task, capped at 24
chars). It names the sandbox dir, the `agent/<id>` branch, logs/workers/<id>.json
and the child's AUTOOS_AGENT_RUN_ID, and rides the gateway as X-AutoOS-Run-Id
beside x-omniroute-session-id. The record also stores the parent's id (that env
var at spawn time), the host, and the full route_plan the run was scored on;
`ps --tree` prints the spawn tree from the parent edge. An id minted elsewhere
still keeps one spawn to one id: `run --run-id <id>` takes that id (the MCP
server's `spawn` does, since it names its own run dir with it) and refuses a
shape that is not canonical with exit 2. The parent edge is only ever the
caller's own env var, never the handed-in id - else a child would be its parent.
docs/routing.md.

Usage:
    python3 tools/autoos-agent.py list
    python3 tools/autoos-agent.py run --card role=review --isolate "Review lib/linux/ui.sh"
    python3 tools/autoos-agent.py run --client qwen --card complexity=trivial --isolate "..."
    python3 tools/autoos-agent.py run --client claude --joinable --isolate --title d1 "..."
    python3 tools/autoos-agent.py run --tier 3 --isolate "Review lib/linux/ui.sh for quoting bugs"
    python3 tools/autoos-agent.py run --tier 2 --isolate "Add a test for X"
    python3 tools/autoos-agent.py run --isolate --read-only "Map every retry path in the spawner"
    python3 tools/autoos-agent.py run --card kind=research --isolate "Map every retry path"
    python3 tools/autoos-agent.py run --run-id 20260928-092516-fix-the-router-abc123 "..."
    python3 tools/autoos-agent.py run --card kind=review --isolate --review-of 20260928-092516-fix-the-router-abc123 "..."
    python3 tools/autoos-agent.py run --card kind=review --isolate --not-family nvidia --no-fallthrough "..."
    python3 tools/autoos-agent.py run --tier 3 --isolate --clean "..."       # no-training twin
    python3 tools/autoos-agent.py run --tier 2 --isolate --model omniroute/t2-orchestrator "..."
    python3 tools/autoos-agent.py run --tier 1 --free "..."        # no keys at all
    python3 tools/autoos-agent.py run --tier 3 --isolate --dry-run "..."     # print the plan only
    python3 tools/autoos-agent.py context                          # this session's fill
    python3 tools/autoos-agent.py context --transcript s.jsonl --json
    python3 tools/autoos-agent.py heartbeat --inbox i.md --transcript s.jsonl --json
    python3 tools/autoos-agent.py inbox L1-routing --since-card status/L1-routing.card.md
    python3 tools/autoos-agent.py card check status/L1-routing.card.md
    python3 tools/autoos-agent.py route --card kind=review,paths=tools/registry.py --explain
    python3 tools/autoos-agent.py ps --tree                          # runs under their parent
    python3 tools/autoos-agent.py risk --sha <sha> --base origin/main   # the diff's risk class

--free maps every tier agent to one of opencode's own free models (default
opencode/muse-spark-1.3-contributor-free) through OPENCODE_CONFIG_CONTENT: no
gateway, no key, no spend - for exercising the tier chain and the permission
fences. Free promo models may train on prompts, so --free refuses --clean. A
--free run that hits a provider stop re-runs the same task in the same sandbox
on the next model of policy.free_client_models in catalog/ai-registry.json
(SPAWNFREE item 1), and waits for a free slot before starting at all when the
provider's live workers already reach policy.free_concurrency (item 2).

Family fence (FAMILYFENCE): a review that runs on the writer's own model family is
not an independent one, and before this the fallthrough walked the ordered free
chain onto the writer's family and labelled the result a cross-family review
(measured 2026-09-29). `--not-family <fam>` (repeatable) removes those families
from the WHOLE plan — the model or route picked up front and every fallthrough
candidate — and a plan with nothing outside the fence left exits 12 instead of
serving the run inside it. A name the registry carries no family under is refused
(rc 2) before any leg choice, because a fence that excludes nothing reads as a
guard while it is none (FAMILYFENCE-3 B2). It removes them from what SERVES: when
`--free` (or an own-account `--model` pin) already decided the model, the fence is
judged on that model, and a gateway combo whose legs no run of this shape ever
resolves does not refuse it (FAMILYFENCE-3 B1). `--review-of <run-id>` names the
run whose WRITER this
one reviews: a review defaults to fencing that writer's family, read from that
run's runner-private kill record and never from its job.json (skill R-orch-17 —
job.json lives in the directory the worker owns). That store is per-checkout, so a
review spawned from a tree that did not spawn its writer reads no family at all —
and exits 2 naming the store searched and both ways out (spawn where the writer ran,
or `--not-family`), because a fence that was asked for and cannot be built is not a
warning (FAMILYFENCE-4). A review with neither is
possible and says so on stderr, because its silence is what got measured.
`--no-fallthrough` pins the run to the model it was planned on: a stop there is
the run's answer, its own rc, with no re-plan onto whatever model survived. After
the run, a review prints `family: writer=... reviewer=... CROSS-FAMILY: yes|NO|
NO (assumed)|unknown` beside its `writer:` line, and a family that collides — the
author's, or any `--not-family` name — exits 12, because OmniRoute can fall through
to a leg inside a combo the spawner's plan never showed.
That verdict is a *provenance* claim (FAMILYFENCE-b): `yes` prints only for a
witnessed model — the gateway call log, or an own-account client's own transcript
(qoder, claude report theirs through a per-attempt `--session-id`); `--model` is
`pinned`; a model known only from the plan's assumed default is never proof, so an
unproven reviewer prints `unknown`, never `yes`. A collision is the conservative
direction and the exit code acts on it either way, so an unattested one prints
`NO (assumed)` rather than `unknown` next to a refusal (FAMILYFENCE-3 N5). The
resolved writer carries a
`source` field (`gateway-log`/`client-reported`/`pinned`/`assumed-default`) that
`writer:` prints and `ps`/`status`/`result` surface for every client, read only from
the runner-private kill record. `--model` pins an own-account client too, and its
family feeds the fence like a gateway leg's — qoder appears in no route, so a fence
that only read routes was blind to that choice.

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
"capability", like the NO-OP; the heading counts only in the client's final message, and a line the
spawner itself sent (the brief a worker echoes back) never counts - SPAWNFIX3c);
11 = an --isolate run marked --read-only (or a card whose kind/role is
research) that changed its sandbox anyway (READ-ONLY WRITE: changing nothing is that run's success, so an
edit is the failure - the diff stat is printed, plus any commit the sandbox's own reflog still shows
beyond its base (a commit the run reset away - SPAWNFIX3c), and the track record carries failure class
"capability".
The same marked run that only reported is NOT a failure: it exits 0 with "RESEARCH: report only");
12 = a run whose every remaining model and route sits inside its family fence (NO-OTHER-FAMILY:
FAMILYFENCE — nothing outside the excluded families could serve the run, so it refuses instead of
falling through onto the writer's own family and calling the result a cross-family review; the
track record carries failure class "refusal", like the other refusals); the child's
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

`inbox <name>|--file PATH` (RESTART spec §0/§2, lane R1) prints one inbox's
records whole, oldest first, each headed by its `<timestamp>#<ordinal>` position
so a reader can write that position into its card as `last-event`. The window is
required: `--since-card <card>` (a successor's resume point, the form a relaunch
prompt uses), `--since <position|UTC>`, or `--all`; `--max-records N` (default
30) cuts the oldest and says how many. Records print on stdout, notices on stderr,
so a pack can embed stdout verbatim. The RUN dir is `$AUTOOS_RUN_DIR` only - an
unset variable with no `--file` is an error, never an empty read. Exit codes: 0
read, 1 the file holds no timestamped record (an inbox of another shape is never
"no events"), 2 no window named, a bad position, or an unreadable file.

`card check <file>` (RESTART spec §1, lane R2a) validates the one state card a
successor resumes from — `<RUN>/status/<name>.card.md`, written only by the
session it names. It checks the header fields (`# card <name> — <UTC> |
gen=<id> | context <n>k/<cap>k | last-event <position>`), the fixed section
order (goal, state, next, threads, traps, operator), each section's line cap,
the 40-line total, the 200-char line limit, and that `last-event` parses as a
position — through `autoos_inbox.parse_position`, because §0 keeps one position
parser. A `threads` line whose id matches the Q-id shape (`^[Qq][-:]?\\d`:
`Q-008`, `q-008`) needs an `asked <time>` field (§3: those lines are the pack's
open questions). Every problem prints on stdout with its line number, sorted by
line; the reason a file cannot be read goes to stderr. The rules are
`tools/autoos_card.py`, not restated here, and §0's acknowledgement markers are
`autoos_heartbeat.ACK_MARKERS` — the same list the heartbeat's pause filter (at
the head of a record) and the future `card: stale` check read. Read-only,
so it is safe to run twice. Exit codes: 0 valid, 1 the card breaks at least one
rule, 2 the file is unreadable.
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
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
if os.name != "nt":
    import fcntl  # the free-leg provider lock (SPAWNFIX item 2); msvcrt on Windows

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autoos_card as card_mod  # noqa: E402
import autoos_clients as clients  # noqa: E402
import autoos_context as ctx  # noqa: E402
import autoos_heartbeat as heartbeat  # noqa: E402
import autoos_overlay as overlay_mod  # noqa: E402
import autoos_inbox as inbox  # noqa: E402
import autoos_measure as measure_mod  # noqa: E402
import autoos_redact as redact  # noqa: E402
import autoos_resolver as resolver  # noqa: E402
import autoos_risk as risk  # noqa: E402
import autoos_routing as routing  # noqa: E402
import autoos_tokenrate as tokenrate_mod  # noqa: E402
import autoos_track as track  # noqa: E402
import autoos_usage as usage_mod  # noqa: E402
from registry import private_safe, resolve_leg, unavailable_now  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIERS = {1: "t1-orchestrator", 2: "t2-worker", 3: "t3-reviewer"}
# gwloopback (2026-09-30): the gateway address is resolved, not hard-coded.
# `http://127.0.0.1:20128` is right on a host and refused inside the stack's
# containers, where the gateway is a sibling container the compose network
# reaches as `omniroute` (configuration/docker/ai-stack/compose.yml spells the
# in-network address "http://omniroute:20128"). An override wins outright, the
# docker DNS name is the container answer, loopback the host fallback.
# Resolution is string-only and takes no I/O: importing this module must not
# contact a gateway (the suite asserts it, and a health GET per import would
# make every import depend on the host's stack). So the const is a guess, and
# gateway_up() further down is the pre-check that confirms it and rebinds
# GATEWAY to the candidate that actually answers.
GATEWAY_ENV_VAR = "AUTOOS_OMNIROUTE_URL"
GATEWAY_DOCKER = "http://omniroute:20128"
GATEWAY_FALLBACK = "http://127.0.0.1:20128"


def gateway_candidates(env=None) -> list:
    """The gateway base URLs to try, in order.

    An `AUTOOS_OMNIROUTE_URL` override is the operator's deliberate answer -
    the shell suite sets one to a dead endpoint on purpose - so it is the ONLY
    candidate: a dead override must fail closed (cmd_run still refuses, rc 3)
    instead of being rescued by a live gateway behind another name.
    """
    env = os.environ if env is None else env
    override = (env.get(GATEWAY_ENV_VAR) or "").strip().rstrip("/")
    if override:
        return [override]
    return [GATEWAY_DOCKER, GATEWAY_FALLBACK]


def resolve_gateway(env=None) -> str:
    """The address to start from: the override, else the docker DNS name.

    Pure selection - which candidate answers is gateway_up()'s job; loopback
    is the last candidate that pre-check falls through to.
    """
    return gateway_candidates(env)[0]


GATEWAY = resolve_gateway()


def gateway_base_url(gateway: str | None = None) -> str:
    """The gateway address as a provider's API root, for a worker's config.

    GWLOOPBACK-2 (2026-09-30): `GATEWAY` carries the scheme://host:port the
    `gateway_up()` pre-check answered on, and the repo's provider block spells
    its API root as `/v1` - the child's overlay must carry the same shape. An
    override that already carries a path keeps it (a reverse-proxied gateway
    can live under one). `GATEWAY` is read at call time, so a caller gets the
    rebound address, never the import-time guess.
    """
    parts = urllib.parse.urlsplit((gateway or GATEWAY).rstrip("/"))
    path = parts.path if parts.path not in ("", "/") else "/v1"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


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
# OVERLAYHOME (2026-09-28): one tool_calls overlay per machine, not per checkout
# (tools/autoos_overlay.py). The old per-checkout file is a read-only fallback.
MEASURED_OVERLAY_PATH = overlay_mod.default_path()
LEGACY_OVERLAY_PATH = overlay_mod.legacy_path(ROOT)
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
# SB-A2 (D-103) item A/B replaced SB-A's channel. SB-A had `run_client` write the
# client's pgid into `<run-dir>/pgrp.json`, whose path arrived as the env var
# AUTOOS_WORKER_PGRP: the worker shares the uid of the run dir, so a worker could
# write any pgid it liked into the file `cancel` kills from — including its own
# parent's or a bystander's — and an env var is visible in `/proc/<pid>/environ`.
# What `cancel` reads now is written by the RUNNER, outside the sandbox:
#   * a systemd transient scope (`autoos-worker-<run-id>.scope`), a cgroup, so
#     `setsid()`/`setpgid()` leaves a process in it — the primary mechanism;
#   * the runner's own process group plus the leader's start time, for hosts with
#     no user manager (containers, CI, the test suite).
WORKER_SCOPE_PREFIX = "autoos-worker-"

# KEYDENY3b item 1: the spawn gate is spelled two ways in opencode v2.0.16 — its
# rename map is {bash: "shell", task: "subagent", apply_patch: "patch"}, so the
# canonical *action* is `subagent` (what the repo's opencode.jsonc rules assert)
# while the `permission` object still declares the tool-name key `task`, annotated
# "Deprecated alias for subagent". opencode.jsonc:115 is the cautionary tale: a
# `bash` rule matches nothing in v2, so an alias is never the fence you prove —
# hence both spellings go into the run's own overlay, which merges after the
# checkout's config. Fence only the one the drifted checkout happens not to name
# and the leaf still launches a child that carries none of the leaf's fences: it
# can open configuration/api-keys.yml and put the key in its answer.
SPAWN_GATES = ("subagent", "task")
# Who each tier may launch: the tier contract, and nothing for a leaf.
TIER_SPAWN_CHILD = {1: "t2-worker", 2: "t3-reviewer", 3: None}


def spawn_gate_rules(tier: int) -> list:
    """The overlay rules that fence this tier's spawn gate: deny all, then allow
    its one child (last matching rule wins)."""
    child = TIER_SPAWN_CHILD.get(tier)
    rules = [{"action": gate, "resource": "*", "effect": "deny"} for gate in SPAWN_GATES]
    if child:
        rules += [{"action": gate, "resource": child, "effect": "allow"}
                  for gate in SPAWN_GATES]
    return rules


# KEYDENY3b item 2: grep/glob is asserted against the *pattern* the agent
# passes, not the files it searches, so `grep "sk-" .` inside a checkout holding
# a git-ignored api-keys.yml is unfenceable — the pattern matches nothing. The
# guarantee instead lives in the directory a worker runs in: an --isolate clone
# is `git clone --local`, which materialises committed files only, so an ignored
# secret is never present for the search to walk.
# KEYDENY3g (L1-routing policy decision): that directory is mandatory for every
# *spawned* tier. Tier 2 was left in place by KEYDENY3b and the hole it recorded
# is the one that matters: a t2 running in the caller's checkout greps an ignored
# key file, and the native t3 child it launches inherits that cwd and carries
# none of the fences. Only tier 1 (role=orchestrate) — the operator's own session,
# in a lane the operator is watching — may still run in place.
ISOLATE_TIERS = (2, 3)
# A client that cannot run in a clone would have to lose grep/glob for its leaf
# runs instead (the fence is the only control there). None today: isolation is
# `git clone --local` plus a cwd, and every client here is a CLI started with one.
NO_ISOLATE_CLIENTS = frozenset()
# SB-C (SPAWNISO), read by BOTH entry points through `is_write_role`: a card that
# is not one of these is a run that edits files, and a run that edits files gets no
# checkout it did not clone. v2 spells the same pair in `kind` (spec 6.1) —
# `plan`/`research` read the tree and report, which is what `read_only_run` already
# calls a read-only run for the verdict.
READ_ONLY_CARD_ROLES = ("review", "orchestrate")
READ_ONLY_CARD_KINDS = ("review", "plan", "research")
# The harness role each tier runs as, and the card role that overrides it. The
# `leaf` flag itself is read from catalog/agent-harness.json — one home per fact.
HARNESS_PATH = os.path.join(ROOT, "catalog", "agent-harness.json")
TIER_HARNESS_ROLE = {1: "orchestrator", 2: "suborchestrator", 3: "leaf-reviewer"}
HARNESS_LEAF_CACHE = {}


def harness_role_is_leaf(role: str) -> bool:
    """The catalog's `leaf` flag for a harness role (False when unknown)."""
    if role not in HARNESS_LEAF_CACHE:
        try:
            roles = load_jsonc(HARNESS_PATH).get("roles") or {}
        except (OSError, ValueError):
            roles = {}
        HARNESS_LEAF_CACHE[role] = bool((roles.get(role) or {}).get("leaf"))
    return HARNESS_LEAF_CACHE[role]


def role_for_run(tier, card) -> str:
    """Which harness role this run wears. A review card is a leaf at any tier;
    an implement card only becomes the leaf role when it routed down to tier 3."""
    card_role = (card or {}).get("role")
    if card_role == "review":
        return "leaf-reviewer"
    if card_role == "implement" and (tier or 2) >= 3:
        return "leaf-implementer"
    return TIER_HARNESS_ROLE.get(tier, "suborchestrator")


def role_is_leaf(tier, card) -> bool:
    return harness_role_is_leaf(role_for_run(tier, card))


def leaf_isolation_refusal(tier, isolate: bool, client: str, leaf: bool = False,
                           card=None, read_only: bool = False) -> str | None:
    """Why this run must not start where it stands, or None when it may.

    Keyed on the role's leaf flag OR the tier being in ISOLATE_TIERS, never on
    the tier number alone: a leaf role that somehow routed to tier 1 is still a
    leaf. Everything outside that — the tier-1 write-role leg — is delegated to
    `writer_isolation_refusal`, so one function answers the whole question for
    both entry points and a caller that deliberately allows an in-place run has
    one gate to allow, not two that can drift apart.
    """
    if isolate or client in NO_ISOLATE_CLIENTS:
        return None
    if not (leaf or tier in ISOLATE_TIERS):
        return writer_isolation_refusal(tier, isolate, client, card=card,
                                        read_only=read_only)
    who = "is a leaf role" if leaf else "is a spawned tier"
    return ("tier %s %s and cannot run in the caller's checkout: pass --isolate "
            "so it gets its own isolated clone. It greps and globs the whole "
            "tree, and git-ignored files (configuration/api-keys.yml, .env*) "
            "live in a working checkout — the pattern fence cannot see them, "
            "because grep/glob is matched against the search pattern, not the "
            "searched file. An --isolate clone holds committed files only, so "
            "no ignored secret is present, and a child it spawns inherits the "
            "clone, not your checkout." % (tier, who))


def is_write_role(card) -> bool:
    """Does this card ask for the work that edits files?

    ONE rule for both entry points (SB-B merge, D-103): this is SB-C's predicate,
    kept with the semantics the fleet server was tested against — a card is a
    writer unless it names one of the read-only roles, and a card that names
    nothing takes the registry's default role (`implement`), which is a writer.
    `routing.CARD_DEFAULTS` is what makes an absent card a writer rather than an
    unknown: an empty card routes as `role=implement`, so the run that never said
    what it touches is the run that touches files.

    Both card dialects answer it: v1 spells the role in `role`, v2 in `kind`
    (spec 6.1), and `plan`/`research` are v2 spellings of a read-only run — the
    same fact `read_only_run` reads for the verdict. A string card is parsed first
    so a caller that holds `--card role=review` text and one that holds the dict
    get one answer.
    """
    if isinstance(card, str):
        try:
            card = routing.parse_card(card)
        except (ValueError, routing.CardError):
            card = None  # normalize() reports the bad card; this is not its job
    card = card or {}
    if _is_v2_card(card):
        return card.get("kind") not in READ_ONLY_CARD_KINDS
    role = card.get("role")
    if role is None:
        return True
    return role not in READ_ONLY_CARD_ROLES


def writer_isolation_refusal(tier, isolate: bool, client: str, card=None,
                             read_only: bool = False) -> str | None:
    """The tier-1 half of the isolate rule: a WRITE run gets no checkout of yours.

    SB-B's CLI leg, now running on SB-C's `is_write_role` — the same predicate the
    fleet server's spawn refusal uses, so a card cannot be a writer to one entry
    point and read-only to the other. `leaf_isolation_refusal` keys on the harness
    role's leaf flag and on ISOLATE_TIERS, so tier 1 was never in it: a card routed
    to t1 with a write role, or an MCP tier-1 spawn, still ran in place and could
    edit the caller's tree. The tier-1 exemption is for the operator's own session,
    not for a worker whose brief is an edit.

    Reached through `leaf_isolation_refusal` (which delegates here for everything
    it does not refuse itself). Call it directly only when you want this leg alone
    — the unit tests do; `cmd_run` and the MCP server use the one gate.

    A run with NO card is not refused: `--tier 1` with nothing else on the card
    side is the operator naming their own session, which is the exemption
    ISOLATE_TIERS itself states ("Only tier 1 ... the operator's own session, in a
    lane the operator is watching, may still run in place"). The fleet server does
    not get to make that call for its callers — a spawn with no card is a WORKER
    and its own rule (SB-C's block in `build_argv`) refuses and isolates it — and
    it is not a second definition of a writer either, because it never asks the
    predicate: no card, no claim, no leg. A `--card role=implement` at tier 1, or
    a card the resolver routed up to tier 1, IS a claim and is refused here.

    Tiers 2 and 3 return None: the leaf helper already refuses those, and two
    refusals firing for one run is two messages about one defect."""
    if isolate or read_only or client in NO_ISOLATE_CLIENTS or card is None:
        return None
    if (tier or 2) in ISOLATE_TIERS or not is_write_role(card):
        return None
    return ("tier %s runs a write-role card (%s) in the caller's checkout: pass "
            "--isolate so it edits its own clone. The tier-1 exemption is the "
            "orchestrator's own session; a worker that writes files needs a clone "
            "or it writes what you are standing in. (A run that only reads — "
            "--read-only, or a review/research/plan card — is unaffected.)"
            % (tier, card_claim(card)))


def card_claim(card) -> str:
    """The word the card used to name its role, for a refusal that quotes it back.

    `card` is text as often as it is a dict — `--card role=implement` arrives from
    argparse unparsed — so read it through the same parse `is_write_role` uses.
    Two dialects, two key names, and a message that must not guess which.
    """
    if isinstance(card, str):
        try:
            card = routing.parse_card(card)
        except (ValueError, routing.CardError):
            card = None
    card = card or {}
    return str(card.get("role") or card.get("kind") or "no role")


def isolate_source(cwd: str | None = None) -> str:
    """The repo an --isolate clone is forked from (KEYDENY3g item 7).

    ROOT is where *this script* lives, which for the MCP server is its own
    checkout — so cloning from ROOT silently started every MCP-isolated sandbox
    on the wrong branch while the worker ran in a clone of an unrelated HEAD.
    The caller's cwd is the truth: its toplevel, falling back to ROOT when the
    cwd is not a repository at all (a temp dir a test planned in).
    """
    cwd = cwd or os.getcwd()
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        return ROOT
    return top or ROOT


def isolate_clone(root: str, path: str, branch: str) -> str:
    """Create the --isolate sandbox and return its base sha.

    The steps are one function because the containment claim is about the
    directory the worker lands in: `git clone --local` copies HEAD's tracked
    files, so nothing git-ignored and nothing untracked exists in the clone
    (KEYDENY3b), and the push fence plus the credential-free env make a push
    out of it an accident guard, not a boundary (FF1, D-106).
    """
    subprocess.run(["git", "clone", "-q", "--local", root, path], check=True)
    # The orchestrator still fetches from the sandbox path (unchanged); every
    # remote's push URL is disabled and a pre-push hook is installed, so an
    # unplanned `git push` — to origin or to the parent's absolute path the
    # containment brief names — fails. ACCIDENT GUARD, not containment: see
    # fence_sandbox_push. The credentials it cannot use are what really
    # keeps the parent safe (worker_env, FF1b).
    fence_sandbox_push(path)
    subprocess.run(["git", "-C", path, "switch", "-q", "-c", branch], check=True)
    # FF1c: the clone gets its own identity, local to itself. The worker's
    # git no longer reads any global config (GIT_CONFIG_GLOBAL is a dead
    # path), so the operator's `user.name` is gone — and a worker that ends
    # its brief with `git commit` would die on "Author identity unknown",
    # leaving the run's work uncommitted. Same author the spawner's own
    # end-of-run commit signs with.
    for name, value in (("user.name", "autoos-worker"),
                        ("user.email", WORKER_EMAIL)):
        subprocess.run(["git", "-C", path, "config", "--local", name, value],
                       check=True)
    return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()

# FF1 (D-106): what a spawned worker inherits. The caller's environment on this
# host carries GitHub, provider and cloud credentials plus an ssh-agent socket,
# and a worker reads its own env (`env`, `git`, a script it was told to run).
# So the set is chosen by an ALLOWLIST: a denylist only ever covers the names
# somebody remembered to write down, and an unlisted name is a name that got
# through.
WORKER_ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR",
                    "SHELL", "NVM_DIR",
                    # Windows: a process with no SYSTEMROOT cannot start a
                    # thread, and a client with no USERPROFILE finds no home.
                    # None of these carry a credential.
                    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
                    "USERPROFILE", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA",
                    "PROGRAMDATA")
WORKER_ENV_ALLOW_PREFIXES = ("LC_", "XDG_")

# WSLSHELL (SB-B, D-103 item 2): the shell a worker runs its commands in. On
# Linux/WSL an opencode worker picked up PowerShell whenever pwsh was installed,
# because opencode takes its shell from $SHELL (and from its own `shell` config
# default) and the caller's $SHELL is the operator's — a bash brief then dies on
# PowerShell syntax two tools in. SHELL stays on the allowlist below (a client
# that reads it for something else still gets a sane value) but its CONTENT is
# pinned here, and stated again in the rendered config, so neither of the two
# paths a worker can take finds the operator's shell.
WORKER_SHELL = "/bin/bash"


def worker_shell():
    """The pinned shell, or None where there is nothing to pin.

    Windows has no /bin/bash, and PowerShell *is* the shell the Windows tree is
    written in (AGENTS.md §6): pinning bash there would break every worker."""
    return None if os.name == "nt" else WORKER_SHELL


# AUTOOS_* by name. AUTOOS_KEYS_FILE and the *_API_KEY ones are deliberately not
# here: the child gets the minted key, never the path to the file it came from.
WORKER_ENV_AUTOOS = ("AUTOOS_STATE_DIR", "AUTOOS_WORKERS_DIR", "AUTOOS_TASK_DIR",
                     # SB-A2 (D-103) item A: AUTOOS_WORKER_PGRP is gone. It named
                     # the file `cancel` killed a group from, and the worker — same
                     # uid, same run dir — could rewrite it. The runner records the
                     # scope unit and the group in job.json instead; nothing a
                     # worker can see or write decides what a cancel signals.
                     "AUTOOS_NO_COLOR", "AUTOOS_DRY_RUN", "AUTOOS_NONINTERACTIVE",
                     "AUTOOS_AGENT_RUN_ID", "AUTOOS_AGENT_DEPTH",
                     "AUTOOS_AGENT_MAX_DEPTH", "AUTOOS_AGENT_INBOX",
                     "AUTOOS_AGENT_TRANSCRIPT", "AUTOOS_AGENT_MCP_DRY_RUN")
# Cross-check on top of the allowlist, applied to what the *plan* injects too:
# no secret-shaped name reaches the child from either side.
WORKER_ENV_DENY = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
                   "SSH_AUTH_SOCK", "SSH_ASKPASS", "AUTOOS_OMNIROUTE_KEY",
                   # Loader/interpreter injection: a value under any of these
                   # names repoints what the child executes before its first
                   # line runs, so no amount of allowlisting the name is safe.
                   "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
                   "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
                   "PYTHONPATH", "PYTHONSTARTUP", "PERL5OPT", "RUBYOPT",
                   "NODE_OPTIONS", "NODE_REPL_EXTERNAL_MODULE",
                   # git reaching a credential or a helper of the operator's.
                   "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT",
                   "GIT_PROXY_COMMAND", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                   "GIT_DIR", "GIT_WORK_TREE", "GIT_EXEC_PATH", "GIT_CONFIG",
                   "SUDO_ASKPASS", "SSH_ASKPASS_REQUIRE",
                   # Other credential caches the child would read by itself.
                   "KUBECONFIG", "DOCKER_CONFIG", "NETRC", "_NETRC",
                   "AUTOOS_KEYS_FILE")
WORKER_ENV_DENY_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD",
                            "_ACCESS_KEY", "_CREDENTIALS", "_ASKPASS",
                            "_CONFIG_FILE", "_CONFIG_PATH")
WORKER_ENV_DENY_PREFIXES = ("AWS_", "AZURE_", "GCP_", "GOOGLE_", "ANTHROPIC_",
                            "OPENAI_", "OPENROUTER_", "DEEPSEEK_", "GH_",
                            "GITHUB_", "GITLAB_", "SLACK_",
                            "LD_", "DYLD_", "GIT_", "SSH_", "KUBE", "DOCKER_")
# And what the *plan* is allowed to add, on top of clearing the deny check. The
# allowlist above only ever covered the caller's own exports: plan["env"] was
# copied in behind it, so a builder that set PATH, LD_PRELOAD or PYTHONPATH
# owned the child without anyone noticing (FF1b, Muse#high on 362b8af..6bdeca5).
# This is the list of names the spawner's builders genuinely set; anything else
# is refused and announced, because a name nobody wrote down here is a name
# nobody decided the child should have.
WORKER_PLAN_ENV_PASSLIST = ("OPENCODE_CONFIG_CONTENT", "XDG_DATA_HOME",
                            "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME",
                            clients.GEMINI_CUSTOM_HEADERS_ENV)
WORKER_PLAN_ENV_PASSLIST_PREFIXES = ("AUTOOS_AGENT_",)

# git in the worker must fail rather than ask: askpass helpers that always exit
# non-zero, no terminal prompt, and a config that cancels any stored credential.
_GIT_ASKPASS_FALSE = ("/bin/false" if os.path.exists("/bin/false")
                      else "/usr/bin/false")
# The only git config the worker may read: these two, cancelling the credential
# helper and askPass. GIT_CONFIG_COUNT has to equal their number — a parent's
# leftover GIT_CONFIG_KEY_n/VALUE_n (what `git -c ...` exports, and the
# orchestrator runs `git -c` a lot) is read by name, so anything past the count
# would be an unreviewed channel.
_GIT_GUARD_CONFIG = (("credential.helper", ""), ("core.askPass", ""))
WORKER_GIT_GUARDS = ((
    ("GIT_TERMINAL_PROMPT", "0"),
    ("GIT_ASKPASS", _GIT_ASKPASS_FALSE),
    # GIT_CONFIG_PARAMETERS is how `git -c key=value` reaches a child git; it
    # is read before GIT_CONFIG_KEY_n, so forcing it empty is not optional.
    ("GIT_CONFIG_PARAMETERS", ""),
    # FF1c item 1: the two numbered channels *cancel* settings, they do not stop
    # git reading a config. $GIT_CONFIG_GLOBAL overrides both $HOME/.gitconfig
    # and $XDG_CONFIG_HOME/git/config; naming a dead path is the only way to shut
    # the file itself, and an inherited HOME reopened it — url.insteadOf (a
    # remote repointed at the parent), core.sshCommand and core.hooksPath (a
    # program of the operator's) are all beyond the reach of the guard above.
    # NOSYSTEM shuts /etc/gitconfig, which the repo's own installers write.
    ("GIT_CONFIG_GLOBAL", os.devnull),
    ("GIT_CONFIG_NOSYSTEM", "1"),
    ("GIT_CONFIG_COUNT", str(len(_GIT_GUARD_CONFIG))))
    + tuple(("GIT_CONFIG_KEY_%d" % i, k) for i, (k, _v) in enumerate(_GIT_GUARD_CONFIG))
    + tuple(("GIT_CONFIG_VALUE_%d" % i, v) for i, (_k, v) in enumerate(_GIT_GUARD_CONFIG))
)
# Named off rather than left to the deny patterns, because these are the three
# ways git reaches a *program* the operator installed (FF1b item 2): an ssh
# transport, a proxy command, and an askpass helper. The guards above cannot
# cancel them, and an allowlist miss is silent.
WORKER_ENV_FORCED_OFF = ("GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND",
                         "SSH_ASKPASS", "SUDO_ASKPASS", "GIT_ASKPASS_REQUIRE")


def _worker_env_denied(name: str) -> bool:
    return (name in WORKER_ENV_DENY or
            name.endswith(WORKER_ENV_DENY_SUFFIXES) or
            name.startswith(WORKER_ENV_DENY_PREFIXES))


def _worker_env_allowed(name: str) -> bool:
    if _worker_env_denied(name):
        return False
    return (name in WORKER_ENV_ALLOW or name in WORKER_ENV_AUTOOS or
            name.startswith(WORKER_ENV_ALLOW_PREFIXES))


def _plan_env_passed(name: str) -> bool:
    return (name in WORKER_PLAN_ENV_PASSLIST or
            name.startswith(WORKER_PLAN_ENV_PASSLIST_PREFIXES))


def _drop_extra_git_config(env: dict) -> None:
    """Keep git's numbered config channels to the guards (FF1b item 2).

    GIT_CONFIG_KEY_n/VALUE_n are read *by index*, so a leftover GIT_CONFIG_KEY_3
    from a parent's `git -c ...` is consulted even with GIT_CONFIG_COUNT right,
    as long as it is present. Anything at or past the guard count is removed.
    """
    for name in list(env):
        for prefix in ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"):
            if name.startswith(prefix):
                tail = name[len(prefix):]
                if not tail.isdigit() or int(tail) >= len(_GIT_GUARD_CONFIG):
                    del env[name]
                break


def stamp_worker_gateway(env: dict) -> bool:
    """Point a worker's opencode provider at the gateway this process bound.

    GWLOOPBACK-2 (2026-09-30): `gateway_up()` cured the spawner's own
    pre-check, but the isolated worker's opencode reads the SANDBOX CLONE's
    repo config, whose omniroute provider pins `http://127.0.0.1:20128/v1` -
    refused in-container, so every in-container gateway-path worker died on
    its first model call (0 of 58 records; the memspec P1 review seat 1R died
    ConnectionRefused). opencode merges `OPENCODE_CONFIG_CONTENT` last
    (v2.0.19 `Config.load` appends the content source after the discovered
    files; measured 2026-09-30: same clone, same env - `opencode run
    --standalone` fails ConnectionRefused without the stamp and reaches the
    gateway with it), and the overlay already carries the provider's session
    headers, so the resolved address is written there too.

    Only a document that already configures the omniroute provider is
    touched: a `--free` overlay carries only a model, and a run whose model
    does not go through the gateway has no provider block to steer. The
    plan's copy is left alone - the child's env is the stamped one - no file
    on disk is rewritten, and a host resolves loopback, so a host-side
    spawn's provider block is exactly what it was.

    Returns True when a document was stamped; a malformed or unrelated
    overlay passes through untouched.
    """
    raw = env.get("OPENCODE_CONFIG_CONTENT")
    if not raw:
        return False
    try:
        doc = json.loads(raw)
    except ValueError:
        return False
    providers = doc.get("providers") if isinstance(doc, dict) else None
    prov = providers.get("omniroute") if isinstance(providers, dict) else None
    if not isinstance(prov, dict):
        return False
    settings = prov.get("settings")
    if not isinstance(settings, dict):
        settings = prov["settings"] = {}
    settings["baseURL"] = gateway_base_url()
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(doc)
    return True


def worker_env(plan: dict, key: str | None = None, base: dict | None = None) -> dict:
    """The environment of one spawned worker: an allowlist of the caller's
    environment, the plan's own passlisted entries, the worker's single gateway
    ``key``, and the guards that stop git from prompting or reading a stored
    credential.

    The caller's ``AUTOOS_OMNIROUTE_KEY`` is never inherited — the minted one is
    added only when this run genuinely goes through the gateway. ``base`` exists
    so the scrub is testable without touching the real environment.

    A gateway-backed opencode worker's ``OPENCODE_CONFIG_CONTENT`` overlay is
    stamped with the address `gateway_up()` bound (``stamp_worker_gateway``,
    GWLOOPBACK-2): the sandbox clone's own `opencode.jsonc` pins loopback,
    which its container refuses, and opencode merges the overlay last.
    """
    src = os.environ if base is None else base
    env = {n: v for n, v in src.items() if _worker_env_allowed(n)}
    # The operator's session directory (bus, sockets, sometimes the agent's own)
    # is not the worker's, whatever the XDG_ prefix rule above decided (FF1b
    # item 4). The plan puts a private one back.
    env.pop("XDG_RUNTIME_DIR", None)
    # Same rule, same reason, one round later (FF1c item 1): $XDG_CONFIG_HOME is
    # not only git's config file, it is where gh, npm, pip and the clients look
    # for the operator's own settings and any token they stored there. git is
    # already shut by GIT_CONFIG_GLOBAL above; nothing else is.
    env.pop("XDG_CONFIG_HOME", None)
    for n, v in (plan.get("env") or {}).items():
        # A plan entry is our own code talking, so a name that is not on the
        # passlist is drift, not an attack — refuse it and say so loudly rather
        # than let the child quietly run under a repointed PATH or loader.
        if not _plan_env_passed(n):
            print("autoos-agent: refused plan env %s: not on the plan passlist"
                  % n, file=sys.stderr)
            continue
        if _worker_env_denied(n):
            print("autoos-agent: refused plan env %s: secret-shaped name"
                  % n, file=sys.stderr)
            continue
        env[n] = v
    env["PWD"] = plan["cwd"]
    # WSLSHELL: after the plan merge, so this is a forced value rather than a
    # default that a caller's env or a plan entry can win.
    pin = worker_shell()
    if pin:
        env["SHELL"] = pin
    for n, v in WORKER_GIT_GUARDS:
        env[n] = v
    _drop_extra_git_config(env)
    for n in WORKER_ENV_FORCED_OFF:
        env.pop(n, None)
    env.pop("AUTOOS_OMNIROUTE_KEY", None)
    if key:
        env["AUTOOS_OMNIROUTE_KEY"] = key
    # CLAUDEBUDGET item 1 (ccf6f84) crossed with FF1 (D-106): this is the one
    # place a worker's env is built, so the orchestrator's Claude declaration is
    # stripped *here* rather than at each call site. The allowlist above already
    # never let an AUTOOS_CLAUDE* in from the caller's environment — this is the
    # half that stops a plan (or a name added to the prefix later) from carrying
    # one down the tree.
    # GWLOOPBACK-2: last, so the overlay this env actually hands the child is
    # stamped after the plan merge (which is what produced it) and after the
    # key. A `--free` overlay has no provider block; nothing to do.
    stamp_worker_gateway(env)
    return strip_claude_env(env)


def _child_env_passed(name: str) -> bool:
    """Whether an ``extra`` entry may reach a CLI child (FF1c item 2).

    The plan passlist plus the spawner's own ``AUTOOS_*`` state names — the same
    decision the plan side makes. ``PATH`` is deliberately *not* in here: it is
    inheritable, and an extra that repoints it is not something a caller should
    get to decide on the child's behalf.

    The one exception is the Claude budget declaration (CLAUDEBUDGET item 3,
    ccf6f84): an MCP caller's ``claude_reason`` is materialized for the CLI
    processes of *that* spawn only, and the CLI re-reads it at its own gate. It
    is named here rather than left to a wholesale ``dict(os.environ)``, so the
    fence stays the only channel a child env is built through. A client worker
    never gets it — ``worker_env`` strips the namespace on its way out.
    """
    return (_plan_env_passed(name) or name in WORKER_ENV_AUTOOS or
            name == resolver.CLAUDE_CRITICAL_ENV)


def spawner_child_env(base: dict | None = None, extra: dict | None = None,
                      scope_bus: bool = False) -> dict:
    """The environment of a child that is *our own CLI*, not a worker.

    The MCP server preflights a plan and runs a detached job, both by exec'ing
    ``tools/autoos-agent.py``, which scrubs again for the client it launches. The
    child still gets the same allowlist — a token does not need to travel to the
    process that only forwards it — plus the one credential the CLI reads
    directly (``AUTOOS_OMNIROUTE_KEY``, which it mints the worker's client key
    from) and whatever ``extra`` names for itself. The path to the keys file is
    not among them: the CLI finds it under ``ROOT``.

    ``extra`` is a caller's own dictionary, which is exactly why it has to clear
    the same checks as everything else: a bare ``env.update(extra)`` made the
    whole policy a matter of caller discipline (FF1c item 2).

    ``scope_bus`` is for the MCP server's detached runner only: it launches the
    worker scope, and `systemd-run --user` needs the user bus (SCOPEBUS). Without
    it the runner's scope probe saw no bus and every worker ran unscoped.
    """
    env = worker_env({"cwd": os.getcwd(), "env": {}}, None, base=base)
    src = os.environ if base is None else base
    if scope_bus:
        for n, v in scope_bus_env(src).items():  # SCOPE_BUS_ENV names only
            env[n] = v
    if src.get("AUTOOS_OMNIROUTE_KEY"):
        env["AUTOOS_OMNIROUTE_KEY"] = src["AUTOOS_OMNIROUTE_KEY"]
    # CLAUDEBUDGET item 1/3 (ccf6f84) meeting FF1 (D-106): the budget
    # declaration is the orchestrator's authority over *one* run, and the CLI
    # child re-reads it — the MCP server's preflight and detached runner both
    # reach the last-mile gate inside that CLI. So it is forwarded here, by
    # name, to our own CLI only. One line above, worker_env stripped the whole
    # AUTOOS_CLAUDE* namespace out of what a client worker gets, and nothing
    # else in it is let back in.
    if src.get(resolver.CLAUDE_CRITICAL_ENV):
        env[resolver.CLAUDE_CRITICAL_ENV] = src[resolver.CLAUDE_CRITICAL_ENV]
    for n, v in (extra or {}).items():
        if _worker_env_denied(n):
            print("autoos-agent: refused child env %s: secret-shaped name"
                  % n, file=sys.stderr)
            continue
        if not _child_env_passed(n):
            print("autoos-agent: refused child env %s: not on the child passlist"
                  % n, file=sys.stderr)
            continue
        env[n] = v
    return env


def _provision_path_usable(st, path: str, what: str) -> str | None:
    """Why an already-present `path` may not be provisioned through, or None.

    The same three rules judge the leaf and its parent (FF1c item 3, and the
    parent half the Sonnet review of FF1 asked for): ``makedirs`` and ``chmod``
    both follow a symlink, and a directory belonging to another uid is somebody
    else's tree on a shared host — tightening it is a denial of service on its
    real owner, writing into it is the leak.
    """
    if stat.S_ISLNK(st.st_mode):
        return "%s is a symlink" % what
    if not stat.S_ISDIR(st.st_mode):
        return "%s is not a directory" % what
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return "%s is owned by uid %d" % (what, st.st_uid)
    return None


def provision_runtime_dir(path: str | None, _retry: bool = False) -> str | None:
    """Create the worker's private ``XDG_RUNTIME_DIR``: empty, mode 0700.

    Provisioned at the launch site, not in the plan builder — a dry run writes
    nothing, and git refuses to clone into a directory that is not empty, so a
    dir beside the clone would have to come after it. Failing to make it is not
    fatal: a client with an unusable runtime dir falls back to its own default,
    which is exactly the state before this change.

    A leaf that is already there is judged, not inherited (FF1c item 3).
    ``makedirs(exist_ok=True)`` happily walked *through* a pre-existing symlink
    and then chmod 0700'd whatever it pointed at: on a shared host, where the
    state tree is reachable by more than one account, that is both a write into
    someone else's directory and a denial of service on it. Same for a leaf of
    another owner, and for a leaf that is not a directory at all.

    The parent is judged by the same rule (FF1 Sonnet LOW): ``os.path.isdir``
    follows a symlink, so a parent that is a link — or a directory of another
    uid — was walked through and chmod'd from underneath, which is the leaf bug
    one level up and the only path the leaf check could not see.
    """
    if not path or not os.path.isabs(path):
        return None
    try:
        st = os.lstat(path)
    except OSError:
        st = None                    # absent: the normal case
    if st is not None:
        why = _provision_path_usable(st, path, "it")
        if why is not None:
            print("autoos-agent: refusing to provision %s: %s" % (path, why),
                  file=sys.stderr)
            return None
        os.chmod(path, 0o700)        # ours already: re-tighten, idempotent
        return path
    try:
        parent = os.path.dirname(path)
        try:
            pst = os.lstat(parent)
        except OSError:
            pst = None               # absent too: makedirs creates it
        if pst is not None:
            why = _provision_path_usable(pst, parent, "its parent")
            if why is not None:
                print("autoos-agent: refusing to provision %s (%s): %s"
                      % (path, parent, why), file=sys.stderr)
                return None
        if not os.path.isdir(parent):
            # 0700 on the parent too — makedirs(mode) only ever applies it to
            # the leaf, and a world-readable sibling is the same leak.
            os.makedirs(parent, mode=0o700, exist_ok=True)
            os.chmod(parent, 0o700)
        # mkdir, not makedirs: the leaf is created atomically, so a competitor
        # that wins the race is seen as an existing entry instead of merged into.
        os.mkdir(path, 0o700)
    except FileExistsError:
        # Something appeared in the window between the lstat and the mkdir.
        # Re-check it under the same rules, once: a racer that pre-creates the
        # leaf as a symlink must still be refused, not followed.
        if _retry:
            print("autoos-agent: refusing to provision %s: still there after "
                  "a retry" % path, file=sys.stderr)
            return None
        return provision_runtime_dir(path, _retry=True)
    except OSError as exc:
        print("autoos-agent: could not provision the worker's runtime dir %s: %s"
              % (path, exc), file=sys.stderr)
    return path


def worker_dir_refusal(path: str) -> str | None:
    """Why ``path`` cannot serve as the worker's private dir; None if it can.

    Judged by the same rule `provision_runtime_dir` applies, and one level up:
    a leaf that was never created is only fine if it could have been.
    """
    try:
        st = os.lstat(path)
    except OSError:
        parent = os.path.dirname(path)
        try:
            pst = os.lstat(parent)
        except OSError:
            return "it was never created"
        return _provision_path_usable(pst, parent, "its parent") or "it was never created"
    return _provision_path_usable(st, path, "it")


def provision_worker_dirs(env: dict) -> bool:
    """Provision both private XDG dirs a worker launches with; False = refuse it.

    `provision_runtime_dir` returns the path it made usable and None when it
    REFUSES — a symlink, a tree of another uid, a non-dir leaf, a parent like
    that, or a racer still there after the retry. Both launch sites used to drop
    that result (FF1 merge review, rev-merge MED), so the worker started with the
    very directory the fence had just refused: its sockets, tokens and client
    state land wherever the link points, and the 0700 chmod punches a hole in a
    directory it never owned. A directory that merely could not be created keeps
    the old fallback — the client uses its own default — but only once verified,
    because an absent or hostile one is the same exposure the refusal was for.

    An env that names no dir (the scrub pops both when the plan has no state
    tree) is not a refusal: there is nothing to provision.
    """
    for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME"):
        path = env.get(name)
        if not path:
            continue
        provision_runtime_dir(path)
        why = worker_dir_refusal(path)
        if why is not None:
            print("autoos-agent: refusing to launch the worker: its %s %s "
                  "could not be provisioned: %s" % (name, path, why),
                  file=sys.stderr)
            return False
    return True


def fence_sandbox_push(sandbox: str) -> None:
    """Make the obvious push out of an --isolate clone fail: every remote's push
    URL is disabled, and the clone gets a pre-push hook that exits 1.

    This is an ACCIDENT GUARD, not a containment boundary, and it must not be
    described as one. `git push --no-verify` skips the hook, `core.hooksPath`
    points it somewhere else, and `git remote set-url` (or a URL-addressed push,
    which never consults the disabled pushurl) sidesteps both — all three in one
    command, with nothing stolen to do it. `tests/test_autoos_spawner.py`
    `PushFenceHonestyTests.test_a_no_verify_push_is_NOT_blocked_by_the_hook`
    asserts the bypass works, so a future reader cannot take this for a fence.

    What it is for: a worker that *means* no harm and types `git push` — which a
    brief that names the parent's path invites, since `pushurl` alone does not
    cover `git push </parent>`. The containment is elsewhere: the clone is
    disposable, and the worker's environment (worker_env, FF1b items 1, 2 and 4)
    carries no credential, no ssh transport and no config channel to push with.
    """
    remotes = subprocess.run(["git", "-C", sandbox, "remote"],
                             capture_output=True, text=True).stdout.split()
    for remote in remotes:
        subprocess.run(["git", "-C", sandbox, "remote", "set-url", "--push",
                        remote, ISOLATE_PUSH_DISABLED], check=True)
    hooks = os.path.join(sandbox, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    path = os.path.join(hooks, "pre-push")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n"
                 "# autoos --isolate: a worker's work leaves the sandbox by the\n"
                 "# spawner's take-it step, never by push.\n"
                 "#\n"
                 "# This is an ACCIDENT GUARD, not a security boundary:\n"
                 "# `git push --no-verify` skips it, `core.hooksPath` moves it,\n"
                 "# and `git remote set-url`/a URL-addressed push walks past the\n"
                 "# disabled pushurl. Containment is the disposable clone plus\n"
                 "# an env with no credential in it (tools/autoos-agent.py\n"
                 "# worker_env). Do not add a check here and call the sandbox\n"
                 "# sealed.\n"
                 'echo "autoos: git push is disabled in an --isolate sandbox" >&2\n'
                 "exit 1\n")
    os.chmod(path, 0o755)


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


def declared_variants(cfg: dict, model: str) -> list:
    """The variant ids opencode.jsonc declares for `model` ("provider/mid"),
    [] for a model that declares none or names nothing opencode knows."""
    base, _, _ = (model or "").partition("#")
    provider, _, mid = base.partition("/")
    entry = ((cfg.get("providers") or {}).get(provider) or {}).get("models") or {}
    return [v["id"] for v in (entry.get(mid) or {}).get("variants") or [] if v.get("id")]


def apply_effort_rung(cfg: dict, model: str, rung) -> str:
    """Stamp the resolver's effort rung (spec 5.5) on an opencode model id.

    opencode selects a per-request effort with a model VARIANT: `mid#high`
    applies that variant entry's `settings.reasoningEffort`, which the
    OpenAI-compatible protocol sends as `reasoning_effort=high`. Three cases
    deliberately emit no suffix (DSBACK item 3, measured: the rung used to
    reach only the track record, so every rung sent the same bare request):

      * `none`/None — a non-reasoning leg, or a rung clamped to the ladder's
        "none" rung. The base model entry carries no reasoning settings, so
        the request goes out with NO reasoning param at all.
      * a rung the model declares no variant for — dropped, never invented.
        The render only generates variants for the leg that ANSWERS, so an
        undeclared rung is a rung that leg would reject (PROVFIX3 finding 8).
      * a model that already carries a variant — an explicit `--model x#low`
        is the operator's choice and wins over the card's rung; appending a
        second one would be an unresolvable id.
    """
    if not model or not rung or rung == "none" or "#" in model:
        return model
    return model + "#" + rung if rung in declared_variants(cfg, model) else model


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
                           tried: set, benched: set | None = None,
                           fence: dict | None = None,
                           registry: dict | None = None) -> tuple:
    """The next --free attempt: the SAME task in the SAME sandbox on the next
    untried model of `chain`. Returns (plan, from, to), or (None, None, None)
    when the chain is spent or the re-plan is refused (privacy, depth, no
    route) - a refused re-plan ends the run at exit 8, never a silent retry.

    `tried` holds every free model this run has already burned; the model the
    stop just landed on joins it here, so no later attempt can return to it.

    `benched` (RATELIMITRETRY, SB-B) is the set of providers rate-limited during
    THIS run; a model on one of them is tried last, not first — one free account
    per provider, so its next model is the same 429 fifteen seconds later. It is
    preferred, not required: opencode's whole free list sits on one provider, and
    a run that gave up at the first same-provider model would strand work that
    two healthy models could still finish.

    `fence` (FAMILYFENCE) is removed from this chain before it is chosen, not
    deprioritised: a run that has fenced the writer's family and finds only the
    writer's family left raises `FamilyFenceRefused` and ends the run at
    `EXIT_NO_OTHER_FAMILY`. Measured 2026-09-29, this is the exact walk that turned
    two rate-limited cross-family reviews into a same-family one labelled
    cross-family.
    """
    chain = list(chain or [plan["model"]])
    current = plan["model"]
    tried.add(current)
    at = chain.index(current) if current in chain else -1
    rest = [m for m in chain[at + 1:] if m and m not in tried]
    if fence and fence.get("families"):
        registry = registry if registry is not None else load_live_registry()
        allowed = [m for m in rest if not fence_blocks_model(m, registry, fence)]
        if rest and not allowed:
            # Not an exhausted chain — an exhausted FENCE. The distinction is the
            # whole report: exit 8 says "no leg answered", this says "the only leg
            # left was the one the run must not use".
            raise FamilyFenceRefused(fence_refusal(fence))
        rest = allowed
    others = [m for m in rest if free_provider(m) not in (benched or set())]
    for model in others + [m for m in rest if m not in others]:
        given = args.free_model
        args.free_model = model
        try:
            # FAMILYFENCE-3 N1: the fence is not only the chain walk's — build_plan
            # answers it for whatever leg IT picks too, so a re-build that dropped
            # the argument re-admits the fenced family one leg after it was refused.
            next_plan = build_plan(args, cfg, sandbox=plan["sandbox"], fence=fence)
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
# FAMILYFENCE: a review that runs on the writer's own model family is not an
# independent one (D-115), and "every cross-family reviewer is rate-limited" used to
# be answered by walking the free chain onto the writer's family and calling the
# result a cross-family review. This is the code for "nothing outside that family is
# left to serve the run" — the run refuses rather than lie.
EXIT_NO_OTHER_FAMILY = 12

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


# CLAUDEBUDGET-b item 1: the prefix the orchestrator's Claude declarations
# live under. A worker must never hold one, so this is both the gate's env
# namespace and the strip list -- one home for the spelling.
CLAUDE_ENV_PREFIX = "AUTOOS_CLAUDE"


def model_route_id(value) -> str:
    """The route/model id a model spelling denotes: no `omniroute/` prefix, no
    `#effort` suffix (CLAUDEBUDGET-g item C).

    The launcher and the gate used to normalise by hand in five places, and the
    spellings differ by path: `build_plan` hands the client `omniroute/<combo>`
    (or that plus `#<rung>` from the effort stamp), a card names the bare combo,
    a registry row names whatever the operator typed. Two spellings of one route
    read as two values, so a route the resolution already priced looked like a
    caller's unknown model -- this is the one function both sides call.
    """
    text = str(value or "").strip()
    if text.startswith("omniroute/"):
        text = text[len("omniroute/"):]
    return text.partition("#")[0].strip()


def _combo_of(model: str) -> str:
    """The route id a configured model string names, if it names one.

    A gateway model string is `omniroute/<combo>#<effort>` (opencode.jsonc spells
    its defaults that way), and a card's combo is the bare id -- both reduce to
    the id through `model_route_id`, the one normaliser. Anything else --
    "sonnet", "groq/openai/gpt-oss-120b" -- is a model, not a combo, and yields "".
    """
    base = model_route_id(model)
    return base if "/" not in base else ""


def _leg_is_claude(leg, registry) -> bool:
    """`is_claude_leg` for a string the registry may simply not know.

    The resolver raises on an unresolvable provider on purpose (a broken registry
    fails closed there); here an unknown name is a *client default* like
    "Qwen3.8-Flash", which no registry row describes, and calling that Claude
    would refuse every qoder run. So: the name decides, and the name says nothing
    about anything that is not spelled like Claude.
    """
    try:
        return resolver.is_claude_leg(leg, registry)
    except ValueError:
        return resolver.claude_model_name(leg)


def _gateway_client(client_name: str) -> bool:
    """Whether this client's model is resolved *through* the gateway/registry.

    Read from the adapter table, which is what builds the argv — the one place
    that knows whether the run receives a gateway route id or a vendor-native
    model id. An unknown client is assumed gateway (fail closed: a name this
    host does not know is a name nobody can price).
    """
    client = clients.CLIENTS.get(client_name)
    return True if client is None else client.gateway


def _native_model_name(source: str | None) -> bool:
    """Whether this value is a vendor-native model id rather than a route id.

    CLAUDEBUDGET-g item A (review finding 1): only the defaults compiled into the
    adapter are. A caller's `--model` on an own-account client is the same kind of
    string -- qoder, agy and claude pass it to their own CLI verbatim -- and that
    is exactly why it cannot be priced by name: the name is the caller's, and a
    client that runs on the operator's own account answers with whatever it is
    handed. Reading "Efficient" as *not Claude* because no marker matched is a
    guess on the side that spends. A compiled default is code this host reviewed,
    so its name is evidence; a named string is not.
    """
    return (source or "").startswith("clients.")


def spawn_spends_claude(client_name: str, model, registry: dict,
                        source: str | None = None):
    """True / False / None for what running `client_name` at `model` costs.

    None is "cannot tell", and the caller treats it as a spend (CLAUDEBUDGET-d
    item 2): under a budget, an unknown model for a client that can reach Claude
    is refused rather than assumed free.

    A gateway spawn names a *combo route id*, and the registry is what says what
    a combo answers with — so a combo the registry does not carry is unknowable,
    not free. CLAUDEBUDGET-f item 1/5 removed the exception that used to price it
    as free whenever the caller named the string itself (`--model`, a registry
    `clients` row): an attacker could name an all-Claude combo the registry does
    not carry and be told the run costs nothing. CLAUDEBUDGET-g item A removed the
    mirror of that exception on the own-account side, where the same trick worked
    on a bare name. The name still decides for a default compiled into the adapter
    (`_native_model_name`) — reading `Qwen3.8-Flash` as an unknown combo would
    bench every qoder worker on a string no registry row describes.
    """
    if resolver.is_claude_client(client_name):
        return True
    if not model:
        return None
    if resolver.claude_model_name(model):
        return True
    combo = _combo_of(model)
    if combo:
        route = (registry.get("routes") or {}).get(combo)
        if route is None:
            if _native_model_name(source):
                return _leg_is_claude(model, registry)
            # SB-C2 item 4: a bare NAME that is no route can still be a model
            # the registry prices — `deepseek-flash` is the bulk paid leg under
            # DSGUARD's $25 cap, and its row (and every alias row of the same
            # family spelling) carries the same price the spend guard bills.
            # The refusal was right for a string the registry knows nothing
            # about; it was wrong for one it has a priced row for. An
            # anthropic-family row answers Claude whatever its name says.
            row = (registry.get("models") or {}).get(combo)
            if isinstance(row, dict):
                if (row.get("family") or "").lower() in ("anthropic", "claude"):
                    return True
                if resolver.credit_leg_priced(combo, registry):
                    return False
            return None
        # A combo route is what the gateway resolves; it falls through past a
        # rate-limited leg to the next one, so the route is a Claude spend
        # only when every leg of it is — one non-Claude leg is the leg that
        # answers, exactly as the resolver's own leg filter reads it.
        legs = route.get("legs") or []
        return bool(legs) and all(_leg_is_claude(leg, registry) for leg in legs)
    return _leg_is_claude(model, registry)


def opencode_cfg(cfg: dict | None = None) -> dict:
    """`cfg` when the caller brought a real one (main loads opencode.jsonc for
    `run`), else read the same file -- the MCP server calls the gate with none, and
    in-process callers pass an empty stub. Either way the gate reads the config the
    spawn itself is about to read."""
    if cfg:
        return cfg
    try:
        return load_jsonc(os.path.join(ROOT, "opencode.jsonc"))
    except (OSError, ValueError):
        return {}


def effective_spawn_model(client_name: str, model=None, card=None,
                          registry: dict | None = None, cfg: dict | None = None,
                          tier=None, free: bool = False, free_model=None,
                          clean: bool = False) -> tuple:
    """``(model, source)`` this spawn answers with, or ``(None, None)``.

    CLAUDEBUDGET-f item 2/3: this is the runner's own order, in the one place the
    gate reads it, so the value the gate judges is the value the argv carries --
    there is no second resolution path to fall out of agreement with `build_plan`.

      * an explicit `--model` replaces everything (for a gateway client it is the
        `override` `resolve_model` puts first; for an own-account client it goes
        to the CLI verbatim);
      * a gateway client with `--free` runs the promo model, not the tier's;
      * a gateway client with `--tier` runs that tier agent's `model` in
        opencode.jsonc -- through `resolve_model`, the launcher's own function, so
        a `--clean` tier and a declared-variant model come back spelled exactly as
        the run receives them. HEAD read the client default first, which let a
        tier agent whose model IS Claude be priced as a free client default;
      * a gateway client with neither runs the card's combo (an absent card is the
        empty card the CLI parses, whose combo the router picks); a v2 card names
        no combo and is priced at the client's configured default, because the
        resolver that routes it already held its Claude legs behind this gate;
      * an own-account client (agy, qoder, claude) takes the caller's `--model` or
        its own default -- the registry's `clients` row first if the operator wrote
        one, else the adapter's constant -- and no tier agent ever reaches it,
        because `clients.build_command` never receives one.

    ``(None, None)`` means nothing answered, and the gate refuses rather than
    guesses.
    """
    given = str(model or "").strip()
    if given:
        return given, "--model"
    if _gateway_client(client_name):
        if free:
            return str(free_model or DEFAULT_FREE_MODEL), "--free-model"
        if tier is not None:
            agent = TIERS.get(_as_int(tier))
            if agent is None:
                return None, None
            # The launcher's own function: the same clean suffix, the same
            # "is this declared" refusal, which the caller turns into its message.
            try:
                return (resolve_model(opencode_cfg(cfg), _as_int(tier), clean, None),
                        "opencode.jsonc agent %s" % agent)
            except (KeyError, TypeError) as exc:
                raise ValueError("tier %s has no model in opencode.jsonc: %s"
                                 % (tier, exc))
        parsed = card if isinstance(card, dict) else routing.parse_card(card or "")
        if _is_v2_card(parsed):
            # A v2 card names no combo: `_resolve_route_v2` lets the resolver pick
            # the route, and the resolver holds every Claude leg behind this same
            # gate (`claude_allowed`), so the leg half is already answered here.
            # Refusing would bench every resolver-routed worker; what the spawn
            # gate still has to read is the client's own configured default —
            # which exists only for opencode, the one client whose config this
            # is. Any other gateway client on a v2 card stays (None, None) and is
            # refused: not seeing a model is not the same as seeing a free one.
            if client_name != "opencode":
                return None, None
            default = str(opencode_cfg(cfg).get("model") or "").strip()
            return (default, "opencode.jsonc default") if default else (None, None)
        # An absent card is the empty card the CLI parses, so the gate and the
        # plan read the same default: a gateway spawn that names nothing is
        # t2-worker, not "unknown". A card the router refuses raises here, and
        # the caller passes its own words through — they carry the next steps,
        # and a refused card spends nothing whatever the budget says.
        combo, _ = routing.select_combo(parsed)
        return (combo, "card combo") if combo else (None, None)
    row = ((registry or {}).get("clients") or {}).get(client_name) or {}
    configured = str(row.get("default_model") or "").strip()
    if configured:
        return configured, "registry clients row"
    if client_name == "agy":
        return clients.AGY_DEFAULT_MODEL, "clients.AGY_DEFAULT_MODEL"
    if client_name == "qoder":
        return clients.QODER_DEFAULT_MODEL, "clients.QODER_DEFAULT_MODEL"
    return None, None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def claude_spawn_refusal(client_name: str, env: dict, registry: dict | None = None,
                         now=None, model: str | None = None, card=None,
                         cfg: dict | None = None, reason: str | None = None,
                         tier=None, free: bool = False, free_model=None,
                         clean: bool = False) -> tuple:
    """``(refusal, note)`` for a spawn of `client_name` under the Claude budget.

    CLAUDEBUDGET-b item 3(d): the resolver holds Claude *legs*, and HEAD had
    nothing at all for the case where the caller simply names the client --
    `--client claude` ran inside Claude Code and spent the allowance on ordinary
    implement work, which is the exact spend D-102 reserved for finals. The
    answer comes from the resolver's one gate (`claude_allowed`), so a budget
    that flips in the registry flips this too, and a card's own `critical=true`
    is not input: only the orchestrator's declaration is.

    CLAUDEBUDGET-d item 2: the client NAME was not enough, and was the hole. What
    spends the allowance is the model that answers, and three other ways reach a
    Claude model than naming the client: `--model sonnet` on any client, a combo
    route whose legs are all Claude, and a client whose own configured default IS
    Claude (`clients.AGY_DEFAULT_MODEL` is `claude-opus-4-6-thinking` today). So
    the gate reads the effective model through `effective_spawn_model` and
    `spawn_spends_claude`, and refuses when it cannot tell what will answer --
    guessing free is the side that spends.

    `reason` (item 3) is the orchestrator's per-spawn declaration, the MCP
    request's `claude_reason`; it is authority for this call only, so an
    orchestrator need not export `AUTOOS_CLAUDE_CRITICAL` server-wide.

    `note` is the reason an *allowed* Claude spawn is allowed, with the model and
    where it came from, so the run says out loud what let it happen (item 1: the
    record has to cite the authority).
    """
    if registry is None:
        registry = load_live_registry()
    try:
        eff, source = effective_spawn_model(client_name, model, card, registry, cfg,
                                           tier, free, free_model, clean)
    except ValueError as exc:
        # CLAUDEBUDGET-g item B (review finding 5): a card or tier the *router*
        # refuses is a routing error, and it has to leave as one. HEAD answered it
        # with a `claude_budget:` refusal on every host, budget or not: a different
        # door than the one that actually refused, the next steps hidden behind a
        # policy that had no opinion, and a behavior change for every non-budget
        # run. Out of budget mode the router's own exception goes back to the
        # caller, which is where the "(see: … list)" advice lives; in budget mode
        # the run is still refused, but the first words name the error.
        if not resolver.claude_budget_of(registry)["on"]:
            raise
        return ("routing error: %s (claude_budget: nothing was priced, so nothing "
                "was spent)" % exc), None
    spends = spawn_spends_claude(client_name, eff, registry, source)
    if spends is False:
        return None, None
    allowed, gate = resolver.claude_allowed("spawn", env, registry, now,
                                           reason=reason)
    if spends is None:
        if allowed:
            # Budget off, or the orchestrator declared this run: there is still
            # nothing to say about a model nobody identified, and nothing to
            # refuse -- the gate is open regardless of what turns out to answer.
            return None, None
        unpriced = ("no model was resolved at all -- no --model, no tier agent, "
                    "no client default and no card combo the registry can read"
                    if not eff else
                    "the model %r (from %s) is no route the registry carries, so "
                    "nothing can say what it costs" % (eff, source))
        return ("claude_budget: %s cannot be priced -- %s. The budget is on, so "
                "the gate will not assume the free answer. Name a route the "
                "registry carries (--model=<provider/leg>), or declare the run "
                "with %s=<why>." % (client_name, unpriced,
                                    resolver.CLAUDE_CRITICAL_ENV), None)
    if allowed:
        return None, "%s (client %s, model %s from %s)" % (gate, client_name,
                                                           eff, source)
    return ("%s runs the Claude model %s (from %s), which spends the Claude "
            "allowance whatever a route leg says: %s. Finals only, unless the "
            "orchestrator declares this spawn -- %s=<why> in its environment, or "
            "the MCP request's claude_reason (a card's own critical=true does "
            "not)." % (client_name, eff, source, gate,
                       resolver.CLAUDE_CRITICAL_ENV), None)


def _argv_flag_values(cmd, flag) -> list:
    """Every value of `flag <value>` / `flag=<value>` in argv (CLAUDEBUDGET-h 2).

    The `=` spelling is the same flag to every parser here and used to be
    invisible to this gate, which read only the two-token form.
    """
    out = [cmd[index + 1] for index, token in enumerate(cmd[:-1]) if token == flag]
    out += [token.split("=", 1)[1] for token in cmd
            if str(token).startswith(flag + "=")]
    return out


def _overlay_models(env):
    """The models in the OPENCODE_CONFIG_CONTENT document, or None if unreadable.

    `free_overlay` writes `model` and every agent's model there (build_plan), and
    opencode merges that document last, so it beats the jsonc AND the argv: a model
    named in it is a model the process answers with. A document that will not parse
    is a channel this gate cannot read, and None says so — the caller refuses rather
    than pricing half of it.
    """
    raw = (env or {}).get("OPENCODE_CONFIG_CONTENT") or ""
    if not str(raw).strip():
        return []
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    out = [doc["model"]] if doc.get("model") else []
    for agent in (doc.get("agents") or {}).values():
        if isinstance(agent, dict) and agent.get("model"):
            out.append(agent["model"])
    return out


def plan_launch_models(plan, args=None, cfg: dict | None = None,
                      registry: dict | None = None) -> list:
    """``[(value, source)]`` the FINAL plan can answer with (CLAUDEBUDGET-g item A).

    The spawn gate reads the *flags*; this reads what the flags became. Three
    rewrites happen after it and none of them is visible in a flag: the reviewer
    resolution swaps in the model the review list picked
    (`reviewer_run_override`), a v2 card runs on whatever combo the resolver
    settled on (`_resolve_route_v2`), and `--free` replaces the model with the
    promo one. So the authority prices everything the launch itself carries.

    CLAUDEBUDGET-h item 2 (findings 2+4) is *how* it reads them: through
    `clients.MODEL_INPUT`, the one table that says how each client receives its
    model — its argv flag, whatever that client spells it and in either spelling,
    the gateway route it resolves to legs, the config documents (opencode.jsonc's
    tier-agent model and the overlay injected through the child env), and the
    registry `clients` row this host resolves ahead of the adapter constant. HEAD
    scanned argv for the literal `--model` and nothing else, so a row swap or a
    config model reached the process unpriced.

    What it does NOT do is fall back to `effective_spawn_model` when the plan names
    nothing readable (finding 4): that resolution is the early value this gate
    exists to replace, and pricing it is gating a run on a value the launch may
    never carry. An unreadable plan — a client with no row, a client whose every
    channel is empty, an overlay that will not parse — comes back as the single
    ``("", …)`` entry, which the budget gate reads as a refusal.

    Raises the router's ``ValueError`` for a card or tier it refuses (finding 5):
    that is a routing error, and the caller has to label it one.

    An argv value that IS the resolution's own answer keeps that resolution's
    source, so a default compiled into the adapter is still read by name; every
    other value is priced as a caller-named model, the strict reading.
    """
    client = (plan or {}).get("client") or getattr(args, "client", None) or ""
    eff, eff_source = effective_spawn_model(
        client, getattr(args, "model", None), getattr(args, "card", None),
        registry, cfg, getattr(args, "tier", None),
        bool(getattr(args, "free", False)),
        getattr(args, "free_model", None) or DEFAULT_FREE_MODEL,
        bool(getattr(args, "clean", False)))
    out = []
    seen = set()

    def add(value, source):
        text = str(value or "").strip()
        if not text:
            return
        if eff and model_route_id(text) == model_route_id(eff):
            source = eff_source or source
        key = (model_route_id(text), source)
        if key not in seen:
            seen.add(key)
            out.append((text, source))

    cmd = [str(token) for token in ((plan or {}).get("cmd") or [])]
    route = (plan or {}).get("route") or {}
    env = (plan or {}).get("env") or {}
    row = ((registry or {}).get("clients") or {}).get(client) or {}
    unreadable = False
    for entry in clients.MODEL_INPUT.get(client, ()):
        kind, key = entry if len(entry) == 2 else (entry[0], "")
        if kind == "flag":
            for value in _argv_flag_values(cmd, key):
                add(value, "argv %s" % key)
        elif kind == "route":
            # The route combo is what the gateway resolves to its legs, so it is a
            # launch model for a gateway client (only gateway clients carry the
            # row) -- and `--free` replaces it with the promo model before the argv
            # is built, so pricing it anyway would bench a free run for a route it
            # does not run.
            if not bool(getattr(args, "free", False)):
                add(route.get("combo"), "route combo")
        elif kind == "config" and key == "agent":
            agent = (plan or {}).get("agent")
            if agent:
                add(((cfg or {}).get("agents") or {}).get(agent, {}).get("model"),
                    "opencode.jsonc agent %s" % agent)
        elif kind == "config" and key == "overlay":
            models = _overlay_models(env)
            if models is None:
                unreadable = True
            else:
                for value in models:
                    add(value, "OPENCODE_CONFIG_CONTENT overlay")
        elif kind == "registry":
            add(row.get(key), "registry clients row %s" % key)
    if unreadable or not out:
        # Nothing the table names says what this process answers with. Under a
        # budget that is a refusal, never a free pass (finding 3).
        out.append(("", "no model in the final plan"))
    return out


def claude_plan_refusal(args, plan, env: dict | None = None, registry: dict | None = None,
                        cfg: dict | None = None) -> tuple:
    """``(refusal, note)`` for the launch this plan describes: the last-mile gate.

    CLAUDEBUDGET-g item A (Muse#high on 2dff253..4fc082b, findings 1/2/3/6): the
    early gate proves the model the *flags* imply, and the flags are not the last
    word — a reviewer override, a resolver-routed v2 combo, or a registry clients
    row can all replace it after the gate has said "free". A gate that prices an
    earlier resolution is a gate on a value the run will never use. This one runs
    on the final plan, immediately before the client is started, in `cmd_run`'s own
    launch loop (so a fallthrough re-plan passes it too) and in the MCP spawn path
    through the CLI it invokes; the early gate stays, for the fast message that
    comes before any planning.

    CLAUDEBUDGET-h (Muse#high on 4fc082b..d1eb9c8): it prices the model the way
    each client receives it, through `clients.MODEL_INPUT` (findings 2+4); an
    unpriceable plan is a refusal rather than a fallback to the early value
    (findings 3+4); and a card the router refuses leaves as the routing error it
    is, with the router's own words, never as a `claude_budget:` refusal (finding
    5). Accepted residual, stated rather than fixed: the `AUTOOS_CLAUDE*`
    declaration this gate honours is forgeable by a worker that re-exports it in
    its own shell — this is a budget control, not a security fence, and children
    are stripped of it on the way out (`strip_claude_env`).

    Like the early gate it reads the model, not the client name, and refuses both a
    Claude answer and one that cannot be priced — an own-account client handed a
    model string no registry row describes answers with whatever it is handed, so
    under a budget "the name has no Claude marker in it" is not evidence of free.
    """
    env = os.environ if env is None else env
    if registry is None:
        registry = load_live_registry()
    if not resolver.claude_budget_of(registry)["on"]:
        # Nothing to enforce, and nothing to mislabel: out of budget mode the plan
        # launches exactly as it did before this gate existed.
        return None, None
    client = plan.get("client") or getattr(args, "client", None) or "opencode"
    try:
        pairs = plan_launch_models(plan, args, cfg, registry)
    except ValueError as exc:
        # Finding 5: the router refused the card, and the router's door is the one
        # the caller has to be pointed at — a budget label here hides the next
        # steps behind a policy that had no opinion on the card.
        return ("routing error: %s (claude_budget: the run stopped before anything "
                "was priced, so nothing was spent; the words above are the router's, "
                "and `list` prints the values a card may take)" % exc), None
    spends = []
    for value, source in pairs:
        verdict = spawn_spends_claude(client, value, registry, source)
        if verdict is not False:
            spends.append((value, source, verdict))
    if not spends:
        return None, None
    allowed, gate = resolver.claude_allowed("spawn", env, registry)
    named = ", ".join("%s (from %s)" % (value, source) for value, source, _ in spends)
    if allowed:
        # The record cites the authority and the value it let through — the same
        # rule as the early gate, on the model the run really starts on. The
        # declaration is the orchestrator taking responsibility for whatever
        # answers, priced or not, which is why it is read before the refusal below.
        return None, "%s (final plan for %s: %s)" % (gate, client, named)
    if not any(value for value, _s, _v in spends):
        return ("claude_budget: the plan %s is about to launch cannot be priced at "
                "all — no argv flag this client is known to take, no route combo, no "
                "injected config model and no registry clients row says what answers "
                "(clients.MODEL_INPUT reads no model input for %r, or the plan "
                "carries none). The budget is on, so an unpriced launch is a refusal, "
                "not a free pass: name a model the registry can price, declare this "
                "run with %s=<why>, or add the client's model input to "
                "clients.MODEL_INPUT." % (client, client,
                                          resolver.CLAUDE_CRITICAL_ENV), None)
    return ("claude_budget: the plan %s is about to launch carries %s, which this "
            "budget holds for finals and nothing declares: %s. This is the last "
            "gate, after every model the flags implied was rewritten (the "
            "reviewer resolution, the resolver's route, --free). Declare this run "
            "with %s=<why> in the orchestrator's environment, or the MCP request's "
            "claude_reason, or name a model the registry can price." % (
                client, named, gate, resolver.CLAUDE_CRITICAL_ENV), None)


def strip_claude_env(env: dict) -> dict:
    """`env` without any `AUTOOS_CLAUDE*` key -- the child's view of it.

    The declaration is one process's authority over one run. If it were
    inherited, the first worker spawned under it could spawn its own Claude
    workers with the reason it was handed, and the budget would be back where
    item 1 started. Stripped on the way out, at the one place a child env is
    built (and in the MCP server's own launch), so no lane has to remember it.
    """
    return {k: v for k, v in env.items()
            if not k.startswith(CLAUDE_ENV_PREFIX)}


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


def gateway_answers(url: str, timeout: int = 3) -> bool:
    """True when `url` answers GET /api/health with 200; never raises."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def gateway_up(url: str | None = None, timeout: int = 3) -> bool:
    """The gateway pre-check: is a gateway there, and at which address?

    With an explicit `url` it probes just that address. Otherwise it walks
    gateway_candidates() in order - override alone, docker DNS, loopback - and
    rebinds GATEWAY to the first candidate that answers, so the import-time
    guess above becomes the address the rest of this process talks to (the
    host case: `omniroute` does not resolve outside the compose network, and
    the run must still find the gateway on loopback). False when nothing
    answers; GATEWAY is then left as it was for the caller to report.
    """
    global GATEWAY
    if url is not None:
        return gateway_answers(url, timeout)
    for candidate in gateway_candidates():
        if gateway_answers(candidate, timeout):
            GATEWAY = candidate
            return True
    return False


def slugify(text: str, cap: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:cap].strip("-")
    return slug or "task"


# FLEETSPEC P0 (FLEET): one canonical id per spawn. Before this a spawn minted
# two ids from two clocks - the sandbox clone/branch was stamped in LOCAL time
# from the task text, the worker record a separate UTC id - and nothing tied a
# run dir, a record and a branch together.
RUN_ID_SLUG_CAP = 24
# The exact shape mint_run_id produces, and the check `run --run-id` holds an
# id to: a run id is a filename, a branch name and a header value at once, so
# anything else (a path, an empty or over-long slug, a stamp that is not a real
# date and time, a non-hex tail) is refused rather than written into those three
# places. A well-shaped stamp from the wrong clock cannot be told apart here -
# that is why the minting lives in one function, not in each caller.
RUN_ID_RE = re.compile(r"^(\d{8}-\d{6})-([a-z0-9]+(?:-[a-z0-9]+)*)-([0-9a-f]{6})$")


def is_canonical_run_id(run_id) -> bool:
    """True for exactly what mint_run_id mints (a real UTC stamp, a slug of at
    most RUN_ID_SLUG_CAP chars, a 6-hex tail)."""
    match = RUN_ID_RE.match(run_id or "")
    if not match or len(match.group(2)) > RUN_ID_SLUG_CAP:
        return False
    try:
        datetime.datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return False
    return True


def mint_run_id(title: str | None, task: str, now=None) -> str:
    """`YYYYMMDD-HHMMSS-<slug>-<hex6>`, always UTC.

    The slug comes from the title the run is named by - a titleless spawn's
    title is build_plan's own `tN <task head>`, so this is the same text a human
    reads in `ps`, capped at RUN_ID_SLUG_CAP chars and reduced to [a-z0-9-]. A
    run id is a filename, a branch name and a header value at once, so the slug
    is scrubbed first (`slug_source`) and nothing past it is task text at
    all. The hex tail is what keeps two spawns in the same second apart
    (measured 2026-09-25, the bug `unique_suffix` names).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(datetime.timezone.utc)
    return "%s-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"),
                         slugify(slug_source(title or task or ""),
                                 RUN_ID_SLUG_CAP), unique_suffix())


def slug_source(text: str) -> str:
    """The scrubbed copy of `text` that a slug is about to be cut from.

    Both slug producers use it (`mint_run_id`, `session_tag`), because both cut
    BELOW the length of a vendor key - 24 and 40 characters - so the cap that was
    meant to keep task text out of an id kept a pasted key in it whole, into the
    branch name, the sandbox dir, the gateway header and the printed `run-id:`
    line. The shared redactor masks first; the mask token is then dropped rather
    than slugged (an id that reads "autoos-redacted" names nothing and still
    shows that a secret was there).
    """
    scrubbed = redact.Redactor().text(text or "")
    return scrubbed.replace(redact.TEXT_MASK, " ")


# --- redacting the worker's output (SPAWNREDACT item 2) --------------------
# Lesson inbox 2026-09-27T18:58:28Z: a worker's REPORT printed a secret it had
# found, and the spawner passed it straight through to the caller's terminal,
# its log and its WIP commit. Everything the spawner writes *of* or *from* a
# worker's output goes through this one redactor, so no stream has to remember
# to be careful. The patterns live in tools/autoos_redact.py, shared with
# hostexec's argv redaction (item 1).
_OUTPUT_REDACTOR = redact.Redactor()


def register_secret_env(env) -> None:
    """Remember the literal values of the secret-named variables this run hands
    the child (AUTOOS_OMNIROUTE_KEY and anything the plan injects), so a worker
    echoing back the key it was given is masked whatever its shape looks like."""
    _OUTPUT_REDACTOR.add_env(env)


def redact_output(text: str) -> str:
    """Mask secrets in a chunk of worker output (line-oriented, so a stream can
    be redacted as it arrives; already-masked text comes back unchanged)."""
    if not text:
        return text
    return _OUTPUT_REDACTOR.text(text)


def redact_record(record: dict) -> dict:
    """Redact the string fields of a worker record / track entry. Keys and
    non-text values (rc, cost, latency) pass through: the file stays readable.
    A nested value (the persisted route_plan) is walked the same way, because a
    resolver `reason` carries whatever a probe line said."""
    return {k: _redact_value(v) for k, v in record.items()}


def _redact_value(value):
    if isinstance(value, str):
        return redact_output(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    # FLEETP0 review LOW: this walked dicts and lists only, so a secret inside
    # any other container reached the record, `ps` and the log intact. The
    # container type is kept, because the record's shape is what `ps --tree` and
    # the console read.
    if isinstance(value, (list, tuple, set, frozenset)):
        return type(value)(_redact_value(v) for v in value)
    return value


def report_redactions() -> None:
    """Tell the caller its worker's output was altered, once, with a count --
    silence would read as 'the report you saw was the worker's own words'. The
    count resets, so a second cmd_run in this process reports its own secrets,
    not the two runs' total."""
    n = _OUTPUT_REDACTOR.count
    if n:
        print("autoos-agent: redacted %d secret(s) from worker output" % n)
        _OUTPUT_REDACTOR.reset_count()


SESSION_TAG_MAX_LEN = 120
SESSION_TAG_RE = re.compile(r"^[A-Za-z0-9._/-]{1,%d}$" % SESSION_TAG_MAX_LEN)
SESSION_TAG_HEADER = "x-omniroute-session-id"
# FLEETSPEC P0 item 5: the run id goes next to the session tag, so a gateway
# row can be tied back to one spawn's sandbox, record and branch.
RUN_ID_HEADER = "X-AutoOS-Run-Id"
# Measured in the running gateway build (read-only, 2026-09-28): OmniRoute keys a
# conversation on `headers.get("x-omniroute-session-id").trim().slice(0, 128)`
# (resolveConversationId, in .build/next/server/chunks) and no charset check runs
# on it at all. So the limit is a LENGTH and it is a silent TRUNCATION: a value
# past 128 chars loses its tail, and the tail is where the run id lives. `/` is
# accepted, which is what D-063 relies on.
OMNIROUTE_SESSION_ID_MAX = 128


def session_header_value(tag: str, run_id: str | None = None) -> str:
    """The `x-omniroute-session-id` value for one run: `<tag>/<run-id>` (D-063).

    OmniRoute's conversationTracker/chatCore path takes the header verbatim as
    the conversation id, so one value per run threads every leg of that run
    through one Conversation while the part before the first `/` stays the lane
    (`autoos_usage.py --by lane`) and the part after the last `/` is the run
    (`--by run`). `X-AutoOS-Run-Id` still rides beside it, unchanged.

    A tag long enough that tag + "/" + run id would pass the gateway's 128-char
    truncation is sent alone, with one warning: a silently cut run id would make
    every run of that lane share one conversation and look like a working id.
    """
    if not run_id:
        return tag
    combined = "%s/%s" % (tag, run_id)
    if len(combined) <= OMNIROUTE_SESSION_ID_MAX:
        return combined
    print("autoos-agent: session tag %r + run id would pass the gateway's %d-char "
          "%s cap, sending the tag alone (this run is not traceable by session id, "
          "only by %s)" % (tag, OMNIROUTE_SESSION_ID_MAX, SESSION_TAG_HEADER,
                           RUN_ID_HEADER), file=sys.stderr)
    return tag


def gateway_headers(tag: str, run_id: str) -> dict:
    """The two request headers one run stamps its gateway calls with (D-063).

    One dict, so every client that can carry headers carries the same pair -
    the session id (tag + run id, the conversation) and the bare run id.
    """
    return {SESSION_TAG_HEADER: session_header_value(tag, run_id),
            RUN_ID_HEADER: run_id}


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
    # The title is the same untrusted text the run id is cut from, and this tag
    # rides the same gateway header (FLEETP0 review HIGH, item 3): scrubbed first.
    return "%s/%s" % (lane, slugify(slug_source(title)))


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


def cmd_risk(args) -> int:
    """Classify a commit's diff by risk (RISKTIER-a, operator Q-013/D-060).

    The writer does not grade its own work: the class comes from the diff, read
    against `policy.risk_rules` by tools/autoos_risk.py. The rev is resolved to one
    commit hex first, so `--sha HEAD`, a branch and the full sha all classify the
    same commit the same way. Prints

        commit: 59aa3a9794f4d81a1a67241a202eb4fb7de3e527
        risk: high
          reason: secrets handling: configuration/api-keys.yml
        audit: no (20%)

    on stdout; `--json` prints the whole assessment (reasons, the resolved commit,
    the audit draw and the changed files) instead. Exit 0 classified, 2 the diff or
    the registry could not be read — an unclassified diff is never reported as
    `normal`, because a secrets change that reads as low risk gets one cheap
    review.
    """
    try:
        registry = load_registry(args.registry or REGISTRY_PATH)
    except (OSError, ValueError) as exc:
        print("risk: cannot read the registry %s: %s" % (args.registry or REGISTRY_PATH, exc),
              file=sys.stderr)
        return 2
    try:
        out = risk.assess(args.repo, args.base, args.sha, registry)
    except risk.RiskError as exc:
        print("risk: %s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    print("commit: %s" % out["sha"])
    print("risk: %s" % out["risk"])
    for reason in out["reasons"]:
        print("  reason: %s" % reason)
    print("audit: %s (%d%%)" % ("yes" if out["audit"] else "no",
                                out["audit_percent"]))
    return 0


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


def reviewer_run_override(review, client, cfg, tier, model, override, free,
                          fence=None):
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

    ``fence`` (FAMILYFENCE-5 item 1) is the family fence this run carries. The swap
    below is the SECOND model choice of the run, made after every upstream fence
    check had already answered, so a reviewer inside the fence would otherwise reach
    the client unfenced — which is why the callers hand it in rather than check the
    answer afterwards.
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
    # FAMILYFENCE-5 item 1: both returns below put the reviewer's own spelling on
    # the run, so the fence is asked HERE — the resolver's route was fenced
    # upstream, the reviewer that replaces it never was.
    _fence_check_reviewer(asked, entry["model"], fence)
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
            model_route_id(entry["model"]),
            "reviewer-model: %s" % asked)


def _resolve_route_v2(args, parsed_card: dict, cfg: dict, override: str | None,
                      exclude_routes: set | None = None,
                      provider_cooldown: dict | None = None,
                      fence: dict | None = None,
                      model_decided: bool = False) -> dict:
    """A v2 card is routed by the resolver, not select_combo (RUNV2, spec 6.1
    "run takes card v2"). Shares route_plan_for/autoos_resolver.plan with the
    `route` subcommand and the MCP `route` tool, so `run` and `route` can never
    disagree about the same card.

    `exclude_routes` (SPAWNCAP, S2) drops the named route ids from the registry
    the resolver sees, so a provider-stopped attempt is never picked again when
    the run falls through to the next route. The registry is copied, never
    mutated; the returned route is marked ``resolver: True`` so cmd_run knows
    the run was resolver-routed (the v1/--tier paths are not).

    ``provider_cooldown`` (RATELIMITRETRY, SB-B) is this process's own bench of
    providers that just rate-limited it, in the provider-state file's shape
    (``{provider_id: {"unavailable_until": ...}}``). It is applied to the same
    COPY the resolver reads, so the fallthrough picks a leg elsewhere — and
    nothing is written: a 429 that outlives the run is a routing decision for the
    operator, not a fact this spawner gets to record for everyone.

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
    if provider_cooldown:
        # A copy again: apply_provider_state returns one, and the shared
        # load_registry() object must not carry this run's bench into the next.
        registry = apply_provider_state(registry, {"providers": provider_cooldown}, now)
    # FAMILYFENCE-3 B1: `model_decided` means the serving model was already
    # picked by --free (whose chain is fenced by `fence_free_head` in cmd_run) or
    # by an own-account --model pin (fenced by `fence_blocks_model` in
    # resolve_route_unchecked). No combo serves such a run — OmniRoute never
    # resolves it — so the combo-leg check neither refuses it nor strips routes
    # from the resolver's copy (which would leave nothing and exit 2 there).
    fenced = fenced_route_ids(registry, fence) if not model_decided else set()
    reachable = set((registry.get("routes") or {})) - set(exclude_routes or ())
    if fence and fence.get("families") and not model_decided \
            and reachable and not (reachable - fenced):
        # Every route this run can still reach is inside the fence. The resolver
        # would answer that with input_required and the run would exit 2, which
        # reads as "the card was refused" — this is the fenced-family case, and it
        # has its own code and its own words.
        raise FamilyFenceRefused(route_fence_refusal(fence))
    if fenced:
        # The same copy the resolver reads, never the shared registry: a fenced
        # family is out of THIS run's plan, not out of the catalog.
        exclude_routes = set(exclude_routes or ()) | fenced
    if exclude_routes:
        # A copy, so the shared load_registry() cache (and the caller's own
        # reference) never loses the routes a previous attempt needs recorded.
        registry = dict(registry)
        registry["routes"] = {rid: route for rid, route in (registry.get("routes") or {}).items()
                              if rid not in exclude_routes}
    overlay, overlay_missing_at = load_measured_overlay()
    track_record = track.load(TRACK_RECORD)
    client_state = measure_mod.client_state(clients)
    # CLAUDEBUDGET-b item 3(d): `run` and `route` share this core, so the client
    # being spawned has to reach it -- a card routed for client "claude" is a
    # Claude spend even where no route leg names Claude. `env` carries the
    # orchestrator's critical-path declaration (item 1); it is this process's
    # env, which is where a declaration can come from with authority.
    result = route_plan_for(parsed_card, args.task, ROOT,
                            DEFAULT_ORCHESTRATOR_MODEL, now, registry, overlay,
                            track_record, client_state,
                            getattr(args, "client", None) or "opencode",
                            os.environ, overlay_missing_at=overlay_missing_at,
                            credit_guards=plan_credit_guards(registry))

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
        combo, reason = model_route_id(model), reason + "+model"

    card = routing.normalize_v2(parsed_card)
    # An authored review card runs its reviewer, not just any survivable route
    # (REVROUTE item 2); a reviewer on another client is announced, never faked.
    model, reviewer_combo, reviewer_note = reviewer_run_override(
        result.get("review"), clients.CLIENTS[args.client], cfg, tier, model,
        override, args.free, fence=fence)
    if reviewer_combo:
        combo = reviewer_combo
    # DSBACK item 3: the rung the resolver scored has to reach the client that
    # can honour it. opencode carries a per-request effort as a model variant
    # (`omniroute/deepseek-v4.1-flash#high` -> that variant's
    # settings.reasoningEffort -> reasoning_effort), so the stamp goes on the
    # opencode model id only — an OmniRoute combo cannot carry a per-effort
    # alias (pinned by tests/test_registry_render.py), and build_plan hands
    # the gateway clients the bare combo. Until this, route["effort"] reached
    # only the track record and every rung sent the same unadorned request.
    if clients.CLIENTS[args.client].name == "opencode":
        model = apply_effort_rung(cfg, model, result.get("effort"))
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
            # FLEETSPEC P0 item 4: the resolver's own plan, whole (card, route,
            # class, leg, effort, p, expected_cost, bucket, reviewers, review,
            # reason, skipped_legs). The projection above is what the spawner
            # acts on; this is what the run must be auditable against - an
            # operator cannot re-derive it later, because the probes, the
            # cooldowns and the track record it was scored from have moved on.
            "route_plan": result,
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


def resolve_route(args, cfg: dict, client, exclude_routes: set | None = None,
                  provider_cooldown: dict | None = None,
                  fence: dict | None = None) -> dict:
    """resolve_route_unchecked plus the PRIV3 check: a sensitive run whose
    explicit --model replaced the card's combo must still land on private-safe
    legs only (--allow-training keeps its compatibility escape, which now only
    waives that explicit-override check - it no longer unlocks a trainable leg,
    since 2026-09-27)."""
    route = resolve_route_unchecked(args, cfg, client, exclude_routes, provider_cooldown,
                                    fence)
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


def resolve_route_unchecked(args, cfg: dict, client, exclude_routes: set | None = None,
                            provider_cooldown: dict | None = None,
                            fence: dict | None = None) -> dict:
    """Tier/model/combo for this run: an explicit --tier, a v2 card through the
    resolver (RUNV2), or a v1 card through select_combo.

    ``exclude_routes`` and ``provider_cooldown`` are forwarded to
    _resolve_route_v2 only (SPAWNCAP, S2 / RATELIMITRETRY, SB-B): the v1/--tier
    paths have a single combo and never fall through.

    ``fence`` (FAMILYFENCE) applies to every path that picks a model here: the
    resolver's route set, the v1 combo, and the --tier agent's model. A fence that
    leaves nothing raises ``FamilyFenceRefused`` rather than returning a route,
    because a fallback past the excluded family is the defect, not a fallback.
    """
    # --model names a gateway combo for opencode and the gateway clients; for
    # agy/claude/qoder it is the client's own model id and never a route.
    # FAMILYFENCE-b: it is still a MODEL CHOICE, and the one the fence exists for —
    # a qoder review pinned onto the writer's family would otherwise reach the CLI
    # unheard, because no route leg names it and `override` was gateway-only. The
    # route half stays gateway-only: an own-account model id is not a route id, and
    # `_fence_check_route` would be reading a table that has no row for it.
    override = args.model if client.gateway else None
    # FAMILYFENCE-3 B1: --free and an own-account --model pin decide the model
    # that serves the run, and both are fenced on the MODEL (`fence_free_head`
    # upstream, `fence_blocks_model` just below). The combo is a label on such a
    # run — no leg of it is ever resolved — so `fence_blocks_route` must not
    # refuse it. A gateway --model pin still is a combo run: OmniRoute resolves
    # it to its legs, so the combo-leg check keeps its teeth there.
    model_decided = bool(args.free) or (bool(args.model) and not client.gateway)
    if fence and fence.get("families") and args.model:
        registry = load_live_registry()
        if fence_blocks_model(args.model, registry, fence):
            raise FamilyFenceRefused(fence_refusal(fence))
        if client.gateway:
            _fence_check_route(model_route_id(args.model) or "", fence, registry)
    if args.tier is not None:
        model = None if args.free else resolve_model(cfg, args.tier, args.clean, override)
        combo = model_route_id(model) or None
        if not model_decided:
            _fence_check_route(combo, fence)
        return {"tier": args.tier, "model": model, "combo": combo, "reason": "explicit-tier",
                "card": None, "privacy": "sensitive" if args.clean else "public",
                "review": args.tier == 3, "read_only": read_only_run(args, None),
                "review_plan": None}
    parsed = routing.parse_card(args.card or "")
    if _is_v2_card(parsed):
        return _resolve_route_v2(args, parsed, cfg, override, exclude_routes,
                                 provider_cooldown, fence, model_decided)
    card = routing.normalize(parsed)
    combo, reason = routing.select_combo(card, args.allow_training)
    if not model_decided:
        _fence_check_route(combo, fence)
    tier = int(re.match(r"t(\d)-", combo).group(1))  # t2-worker-clean -> 2
    model = None if args.free else resolve_model(cfg, tier, False, override or "omniroute/" + combo)
    if override and model:  # an explicit --model wins over the card's combo, and says so
        combo, reason = model_route_id(model), reason + "+model"
    review = resolve_review_plan(card)
    model, reviewer_combo, reviewer_note = reviewer_run_override(
        review, clients.CLIENTS[args.client], cfg, tier, model, override, args.free,
        fence=fence)
    if reviewer_combo:
        combo = reviewer_combo
    return {"tier": tier, "model": model, "combo": combo, "reason": reason, "card": card,
            "privacy": card["privacy"], "review": card["role"] == "review",
            "read_only": read_only_run(args, card),
            # a v1 card asks for a review with role=review; author is the shared
            # field, so the same reviewer walk applies (REVROUTE item 2).
            "review_plan": review, "reviewer_note": reviewer_note}


def build_plan(args, cfg: dict, exclude_routes: set | None = None,
               sandbox: dict | None = None,
               provider_cooldown: dict | None = None,
               fence: dict | None = None) -> dict:
    """The full run plan for `args`.

    ``exclude_routes`` (SPAWNCAP, S2) is passed through to the resolver so a
    fallthrough re-run does not pick a route that already stopped.
    ``provider_cooldown`` (RATELIMITRETRY, SB-B) is this process's own bench of
    the providers that just rate-limited it, forwarded to the resolver the same
    way. ``sandbox`` reuses an existing clone (same path/branch) instead of
    naming a new one - a fallthrough re-runs in the same checkout, so its WIP
    commit and its work stay on one branch. ``fence`` (FAMILYFENCE) is the family
    fence, applied to whatever leg this plan picks — see `family_fence`.
    """
    client = clients.CLIENTS[args.client]
    route = resolve_route(args, cfg, client, exclude_routes, provider_cooldown, fence)
    if not client.gateway:
        # FAMILYFENCE-3 N4: an own-account run answers on the client's OWN model -
        # `build_command` hands it the pin or the registry default and never names a
        # route to OmniRoute. So the combo the resolver picked for it is a label no
        # leg of it ever serves, and `ps`, the worker record and the run log used to
        # report `t1-orchestrator` for a run that in fact ran `Efficient` on qoder.
        # Recorded as `native:<client>`: honest, and `combo_legs` answers it with no
        # legs, which is exactly what provider-benching should see. The rewrite is
        # AFTER `resolve_route` on purpose: the fence and the PRIV3 check both read
        # the real combo of the card, and standing them down here would be a hole.
        route = dict(route, combo="native:%s" % client.name)
    depth, max_depth = clients.child_depth(os.environ, args.max_depth)
    env = {"AUTOOS_AGENT_DEPTH": str(depth), "AUTOOS_AGENT_MAX_DEPTH": str(max_depth)}
    overlay = {}
    title = args.title or ("t%d %s" % (route["tier"], args.task[:50]))
    # FLEETSPEC P0 item 1: minted ONCE, here. The sandbox clone, its branch, the
    # worker record and the child's own environment all carry this one id, so
    # one run is nameable from any of the four (and the child that spawns again
    # knows who its parent is).
    # FLEETP0b: the caller may hand the id in instead (--run-id, which is how the
    # MCP server's spawn keeps ONE id for a run it also names its own state dir
    # with). A handed-in id wins over everything, including the sandbox-reuse
    # line below: the caller owns the identity, and a handed-in id that disagreed
    # with the clone it was told to reuse would be a lie either way.
    given_run_id = getattr(args, "run_id", None)
    run_id = given_run_id or mint_run_id(title, args.task)
    if sandbox is not None and not given_run_id \
            and (sandbox.get("branch") or "").startswith("agent/"):
        # a fallthrough re-run shares the first attempt's clone and branch, so it
        # shares its id: one spawn is one id, not one per attempt.
        # FLEETP0 review LOW: only when that suffix IS a run id. A branch named
        # by hand, or one from before the canonical id existed, is not an id, and
        # pasting its tail into the header, the record and `ps` unvalidated is
        # exactly what is_canonical_run_id was written to refuse. A fresh id
        # still names a fresh record; the reused clone is unchanged either way.
        inherited = sandbox["branch"][len("agent/"):]
        if is_canonical_run_id(inherited):
            run_id = inherited
    env["AUTOOS_AGENT_RUN_ID"] = run_id
    tag = None
    # FAMILYFENCE-b: the two provenance fields every writer record reads. Both are
    # only ever set on the native branch below; opencode's model is named by the
    # gateway's own call log, and a client that keeps no transcript leaves the
    # second one None — which `resolved_writer` reads as "no witness, no report".
    client_session_id = None
    model_source = WRITER_SOURCE_PIN if args.model else WRITER_SOURCE_ASSUMED
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
            # D-063: the value is the tag AND the run id, one conversation per run.
            # item 5: the same requests also carry the run id, so a call_logs
            # row is not merely a lane's, it is one spawn's.
            prov.setdefault("headers", {}).update(gateway_headers(tag, run_id))
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
        # KEYDENY3g item 3: a leaf that runs on a CLI with its own spawn gate has
        # that gate denied in argv; opencode's gate is the overlay below.
        # FLEETP0 review item 4: the identity headers are not opencode's alone.
        # Every gateway client that can put a header on its own requests gets the
        # same pair (clients.HEADER_CLIENTS; the carriers and the qwen dead end
        # are measured in autoos_clients.py and docs/routing.md), so a call_logs
        # row is one spawn's wherever the work ran.
        headers = None
        if client.name in clients.HEADER_CLIENTS:
            tag = session_tag(title)
            headers = gateway_headers(tag, run_id)
            if client.name == "gemini":
                env[clients.GEMINI_CUSTOM_HEADERS_ENV] = clients.gemini_custom_headers(headers)
        # FAMILYFENCE-b: a client that keeps a session transcript names its rows by
        # a session id, and `--session-id` lets the CALLER pick it. Minting one here
        # is what turns "the newest file in ~/.qoder/projects" into an exact join on
        # this run. A --bg/--remote-control claude session owns its own id, so it
        # gets none (clients.build_command puts the flag only where it is honoured).
        if client.name in clients.MODEL_REPORT and not joinable:
            client_session_id = mint_client_session_id()
        cmd = clients.build_command(client, args.task, route["combo"], level, model, joinable,
                                    deny_spawn=role_is_leaf(route.get("tier"),
                                                            route.get("card")),
                                    headers=headers, session_id=client_session_id)
        if args.lean and client.name in MCP_STRICT_CLIENTS \
                and "--strict-mcp-config" not in cmd:  # claude/qoder only: no MCP servers
            cmd[1:1] = ["--strict-mcp-config"]
        if client.name == "qoder":
            model = model or clients.QODER_DEFAULT_MODEL
        # FAMILYFENCE-b: `source` says where this model came from, because the two
        # answers a run can give are not equally strong — a caller's `--model` is an
        # instruction, an adapter default is this file's own guess, and neither is
        # evidence that the model served. A gateway client's model is the combo it
        # was routed to, which is the same kind of claim.
        model_source = WRITER_SOURCE_PIN if args.model else WRITER_SOURCE_ASSUMED
        model = model or (route["combo"] if client.gateway else PLAN_MODEL_UNNAMED)
    if args.isolate:
        if sandbox is None:
            # The readable prefix stays; the hex tail inside the run id is what
            # keeps two spawns in the same second (same task) from naming the
            # same clone (bug 1).
            name = "%s-%s" % (os.path.basename(ROOT), run_id)
            # Inside the repo's git-ignored logs/ (clients.state_dir). The clone has
            # its own .git, so opencode resolves it as its own project root.
            sandbox = {"path": os.path.join(clients.state_dir(), "sandboxes", name),
                       "branch": "agent/%s" % run_id,
                       # Forked from the caller's checkout, not from wherever this
                       # script happens to live (KEYDENY3g item 7).
                       "source": isolate_source()}
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
        sandbox.setdefault("source", isolate_source())
        cmd[-1] = isolate_task_prefix(sandbox["path"], sandbox["source"],
                                     read_only=bool(route.get("read_only"))) + "\n" + cmd[-1]
    if client.name == "opencode":
        # WSLSHELL (SB-B): opencode's own config schema carries a top-level
        # `shell` ("Default shell to use for terminal"), which its resolver
        # reads with priority "config" — ahead of the pinned $SHELL for a worker
        # whose checkout or user config states one. Say it in the config too.
        shell = worker_shell()
        if shell:
            overlay["shell"] = shell
        # KEYDENY3b item 1: the spawn gate is re-asserted in the overlay, which
        # opencode merges after the checkout's own rules — a leaf cannot spawn an
        # unfenced child even in a checkout whose opencode.jsonc drifted. The
        # rules go last so that "last matching wins" cannot re-open a path the
        # outside fence above just closed.
        overlay.setdefault("permissions", []).extend(spawn_gate_rules(route["tier"]))
    if overlay:
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(overlay)
    # FF1b item 4: a private, empty XDG_RUNTIME_DIR of its own instead of the
    # operator's session one (message bus, sockets, sometimes the ssh-agent).
    # Keyed by run id inside the git-ignored state tree, so a non-isolate run
    # does not leave a directory in the checkout it works in. Only the *name* is
    # decided here: provisioning happens at the launch site, because a dry run
    # writes nothing and git will not clone into a directory that has content.
    env["XDG_RUNTIME_DIR"] = os.path.join(clients.state_dir(), "runtimes", run_id)
    # FF1c item 1: the same treatment for the config home. Inheriting the
    # operator's $XDG_CONFIG_HOME handed the worker git's global config (the
    # guard above only cancels two settings, it cannot hide a file) along with
    # wherever else a tool reads a config and finds a token. Private, per run,
    # inside the same git-ignored state tree; provisioning at the launch site
    # for the same reason as the runtime dir — a dry run writes nothing.
    env["XDG_CONFIG_HOME"] = os.path.join(clients.state_dir(), "configs", run_id)
    return {"agent": agent, "client": client.name, "model": model, "cmd": cmd, "env": env,
            # FAMILYFENCE-b: the ask and its witness, kept beside the ask so a
            # reader never has to re-derive one from the other. `client_session_id`
            # is the join key into the client's own transcript; `model_source` is
            # where `model` came from before any transcript was read.
            "model_source": model_source, "client_session_id": client_session_id,
            # The text this run sends the client (containment prefix + task),
            # kept so the REPORT check can tell the worker's own words from its
            # brief echoed back at it (SPAWNFIX3c).
            "brief": cmd[-1],
            "route": route, "depth": (depth, max_depth), "free": bool(args.free),
            "run_id": run_id, "sandbox": sandbox,
            "cwd": sandbox["path"] if sandbox else os.getcwd(),
            "session_tag": tag}


# --- RUNMODEL (D-103, SB-B item 4): who ACTUALLY served this run -------------
#
# A lane record has always said which route the spawner asked for and never which
# provider and model answered. After a fallthrough that distinction is the whole
# story — the leg a run started on is not the leg that wrote the diff — and a
# review that wants to hold a family to account needs the writer, not the plan.
#
# The gateway is the only witness: it stamps every call with the run's
# `x-omniroute-session-id` (D-063), so ONE read-only GET of the call log, matched
# to this run's session, is the evidence. A log it refuses to read is recorded as
# `unresolved`: a plan says what was asked for, this field is about what happened,
# and the two are not interchangeable when they disagree.

WRITER_UNRESOLVED = "unresolved"
# The endpoint's own default page size (callLogs.ts:1014), newest-first, so one
# GET reaches the calls this run made last — which is the attempt that survived.
CALL_LOG_PAGE = 200

# --- FAMILYFENCE-b: `source`, or how the record knows what it is saying --------
#
# `unresolved` answered "the run named no model". It never answered "this model is
# what we asked for, not what served", and the two read identically in a record —
# which is the gap a qoder review fell through: qodercli 1.1.63 takes an unknown
# `--model`, prints `falling back to default model "efficient"` and exits 0, so the
# plan's model was never evidence of anything. A gateway row IS evidence, and a
# client's own session transcript is evidence of a different shape. `source` names
# the witness, and only the two witnesses below count as proof: a pin is what a
# caller typed and an assumed default is what this spawner would have typed.
WRITER_SOURCE_GATEWAY = "gateway-log"
WRITER_SOURCE_REPORT = "client-reported"
WRITER_SOURCE_PIN = "pinned"
WRITER_SOURCE_ASSUMED = "assumed-default"
WRITER_PROVEN_SOURCES = frozenset({WRITER_SOURCE_GATEWAY, WRITER_SOURCE_REPORT})
# The import-time home, the fallback under `client_home()` for a process whose
# environment names none. A client's transcript is read relative to the home the
# CHILD ran with, so a test repoints $HOME — this constant only exists so that
# fallback has one documented shape instead of an inline expanduser at each site.
HOME_DIR = os.path.expanduser("~")
# build_plan's placeholder for "this client picks its own model": a string that
# reads like a model name but names none.
PLAN_MODEL_UNNAMED = "(client default)"


def mint_client_session_id() -> str:
    """A fresh session id to hand a client that keeps a transcript (MODEL_REPORT).

    Minted per attempt, not per spawn: qodercli refuses a `--session-id` that is
    already a session on disk, and a fallthrough re-launch is a second session.
    A uuid4 is what both documented shapes accept (`claude --help`: "must be a
    valid UUID"; `qodercli --help`: "--session-id <id>")."""
    return str(uuid.uuid4())


def client_home() -> str:
    """The home the CLIENT writes its own records into, resolved at call time.

    The child inherits this process's `$HOME` — `worker_env` allowlists HOME and
    only makes the XDG directories private (FF1b/FF1c) — so a transcript the run
    wrote is found under the environment it ran with, never under the home this
    module happened to be imported in. That distinction is the difference between
    reading the operator's other sessions and reading this run."""
    return os.environ.get("HOME") or os.environ.get("USERPROFILE") or HOME_DIR


def writer_is_proven(writer) -> bool:
    """Whether `writer` names a model a witness attested to, not one we assumed."""
    return bool(writer) and writer.get("source") in WRITER_PROVEN_SOURCES


def manage_key(env=None) -> str | None:
    """The host's manage-scoped OmniRoute key, or None when there is none.

    Read exactly as `autoos-agent.py usage` reads it (the ai-stack config the
    installer left on the host), because the call log answers the manage scope
    only. A `--free` run has no gateway to ask, so it never gets this far.
    """
    env = os.environ if env is None else env
    try:
        return usage_mod.read_manage_key(usage_mod.key_file_path(env))
    except (OSError, ValueError):
        return None


def _call_log_rows(fetch, gateway, key, limit):
    """The rows of ONE read-only call-log GET, or None on any failure.

    Never pages: one read is the contract, and a second page of a busy gateway
    is not worth a longer tail on every finished run.
    """
    url = "%s/api/usage/call-logs?limit=%d&offset=0&excludeTests=1" % (gateway, limit)
    try:
        status, body = fetch(url, usage_mod.auth_headers(key), usage_mod.TIMEOUT_S)
    except Exception:  # noqa: BLE001 - transport shapes vary (URLError, OSError, http.client)
        return None
    if status != 200:
        return None
    try:
        rows = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
    except (ValueError, UnicodeDecodeError):
        return None
    return rows if isinstance(rows, list) else None


def gateway_writer(session_id, gateway=None, key=None, fetch=None, limit=CALL_LOG_PAGE):
    """(provider, model) of the newest call the gateway logged for `session_id`.

    A run that fell through leaves one row per dead attempt under the SAME
    session id, so the newest row is the attempt that finished the work; a
    successful row is preferred over an error row because an error row names a
    provider that refused rather than one that served. None when the log cannot
    be read or names nothing for this run — the caller records `unresolved`
    rather than guessing from the plan.
    """
    if not session_id:
        return None
    gateway = gateway or GATEWAY
    key = manage_key() if key is None else key
    if not key:
        return None
    rows = _call_log_rows(fetch or usage_mod.urllib_fetch, gateway, key, limit)
    if not rows:
        return None
    mine = [r for r in rows if isinstance(r, dict)
            and r.get("sessionTag") == session_id
            and (r.get("provider") or r.get("model"))]
    if not mine:
        return None
    ok = [r for r in mine if not (int(r.get("status") or 0) >= 400 or r.get("error"))]

    def newest(candidates):
        def stamp(row):
            ts = usage_mod.row_timestamp(row)
            return ts if ts is not None else 0
        return max(candidates, key=stamp)

    row = newest(ok or mine)
    return (row.get("provider") or WRITER_UNRESOLVED, row.get("model") or WRITER_UNRESOLVED)


def resolved_writer(plan, uses_gateway, registry=None, key=None, fetch=None,
                    gateway=None, home=None) -> dict:
    """The writer of this run: `{provider, model, family, source}`.

    A gateway run asks the gateway (one GET, by the run's own session id); a
    native run asks the client first — its own session transcript, joined by the
    session id this spawner minted into the argv — and only falls back on the plan
    when the client said nothing. `--free` names the provider in that same
    `provider/model` id. The family is the registry's own declaration for that
    model spelling, never a guess from the name.

    `source` is the witness behind the answer (`WRITER_SOURCE_*`), because a model
    the plan assumed and a model the client attested are not the same claim — a
    review is only independent of an author it can PROVE it ran elsewhere.

    Any field this cannot prove is `unresolved`, which is the point of the
    record: an empty field reads like "the run did not use a model".
    """
    registry = registry if registry is not None else load_live_registry()
    provider = model = None
    if uses_gateway:
        session = session_header_value(plan.get("session_tag") or "", plan.get("run_id")) \
            if plan.get("session_tag") else None
        found = gateway_writer(session, gateway=gateway, key=key, fetch=fetch)
        if found is None:
            return {"provider": WRITER_UNRESOLVED, "model": WRITER_UNRESOLVED,
                    "family": WRITER_UNRESOLVED, "source": WRITER_UNRESOLVED}
        provider, model = found
        source = WRITER_SOURCE_GATEWAY
    else:
        asked = plan.get("model") or None
        if asked == PLAN_MODEL_UNNAMED:
            asked = None
        source = plan.get("model_source") or WRITER_SOURCE_ASSUMED
        reported = clients.reported_model(plan.get("client") or "",
                                          plan.get("client_session_id"),
                                          home=client_home() if home is None else home)
        if reported:
            model = reported
            source = WRITER_SOURCE_REPORT
        else:
            model = asked
        if model:
            # A native client's own id, or a `provider/model` promo id on --free.
            provider = free_provider(model) if "/" in model else plan.get("client")
            # The gateway leg answers with the bare model name, so this one must
            # too: `mimo/mimo-7` in a record read beside `mimo-7` looks like two
            # different models, and the prefix is already in `provider`.
            if "/" in model:
                model = model.rpartition("/")[2]
    family = (_family_of_one_spelling(model, registry) if model else None)
    return {"provider": provider or WRITER_UNRESOLVED,
            "model": model or WRITER_UNRESOLVED,
            "family": family or WRITER_UNRESOLVED,
            "source": source or WRITER_UNRESOLVED}


def writer_line(writer: dict) -> str:
    """The one line every reader of the run sees: `writer: <provider>/<model> (<family>) source=<witness>`."""
    return "writer: %s/%s (%s) source=%s" % (
        writer.get("provider") or WRITER_UNRESOLVED,
        writer.get("model") or WRITER_UNRESOLVED,
        writer.get("family") or WRITER_UNRESOLVED,
        writer.get("source") or WRITER_UNRESOLVED)



# --- FAMILYFENCE: a review never silently runs on the writer's own family ------
#
# Measured 2026-09-29: an ORCH-A1 writer resolved to a NVIDIA nemotron, the two
# cross-family reviews it asked for (mimo, muse) both hit their rate limits, and
# SB-B's fallthrough walked the ordered free chain with no family rule in it onto
# nemotron again. The run reported a same-family read as a cross-family review.
# D-115 already had the family rule for the *lane record*; nothing had it for the
# chain the spawner itself walks.
#
# One fence object (below) is the whole rule, and one predicate
# (`fence_blocks_model`) answers it, so the first model choice, the fallthrough
# candidates and the route filters cannot drift apart.

class FamilyFenceRefused(Exception):
    """A plan with nothing outside the fenced families left.

    Deliberately NOT a ``ValueError``: every fallthrough path already catches a
    ``ValueError`` as "this re-plan is impossible, end at exit 8", and a fence that
    got relabelled as an ordinary provider exhaustion is the lie this exists to
    stop. `cmd_run` catches it where it can name its own exit code."""


FAMILY_FENCE_REFUSAL = "no model outside family %s left - refusing (FAMILYFENCE)"
FAMILY_FENCE_NO_WRITER = ("autoos-agent: review without a known writer family - "
                          "cross-family not enforced")


def fence_unreadable_writer_refusal(review_of):
    """FAMILYFENCE-4: a `--review-of` whose writer family cannot be read is refused,
    not warned about.

    The record store is per-checkout (`kill_store_dir`), so a review spawned from a
    lane that did not spawn its writer holds no record for it: `writer_family_of_run`
    answers None, the fence excludes nothing, and the run used to continue onto a
    combo carrying the writer's own family with one stderr line beside it. A warning
    is not an answer to a fence that was ASKED for — and the caller cannot see the
    difference from the plan alone. The message names the store it searched and both
    ways out, because "spawn it from where the writer ran" is the one the orchestrator
    usually wants.

    FAMILYFENCE-5 item 2 (cross-family review Muse): the second way out used to be
    "name the family with --not-family", but `--not-family` does not clear this
    refusal — the writer stays unnamed and the run is still refused. The way out that
    actually works is to drop `--review-of` and fence by name instead, so the text
    says that; a caller following the old sentence would have re-spawned with one
    more flag and the same exit 2."""
    return ("--review-of %s: no resolved writer family in this checkout's "
            "runner-private record store (%s), so no family can be fenced out - "
            "refusing. Spawn the review through the same autoos-agent MCP/checkout "
            "that spawned the writer, or drop --review-of and name the writer's "
            "family with --not-family."
            % (review_of, kill_store_dir()))


def fence_family_names(values):
    """`values` in `resolver.family_key` form, de-duplicated, order kept.

    The registry's own spelling is the only comparison form for a family (REVFIX:
    a record's "Meta" and the registry's "meta" are one family), so a caller typing
    `--not-family NVIDIA` fences the same models as `nvidia`.

    A bare string is ONE name, not its letters: `family_fence` is reached from the
    CLI (whose `append` action always hands over a list) and from callers that pass
    one value directly, and iterating a string char-by-char fenced "m", "i", "o"
    instead of "mimo" — nothing the registry carries, so the fence was a no-op that
    read as a guard (FAMILYFENCE-3 N2)."""
    if isinstance(values, str):
        values = [values]
    out = []
    for value in (values or []):
        key = resolver.family_key(value)
        if key and key not in out:
            out.append(key)
    return out


def registry_family_names(registry):
    """Every family the registry itself declares, in `resolver.family_key` form.

    The `models` rows and `policy.reviewers` rows — the same two sources
    `reviewer_family` reads, so a name this lists as known and the family a fence
    compares against cannot drift apart (FAMILYFENCE-3 B2)."""
    out = []
    models = (registry or {}).get("models") or {}
    rows = list(models.values()) if isinstance(models, dict) else list(models)
    for entry in rows + list(((registry or {}).get("policy") or {})
                             .get("reviewers") or []):
        key = resolver.family_key((entry or {}).get("family"))
        if key and key not in out:
            out.append(key)
    return out


def fence_name_refusal(unknown, known):
    """FAMILYFENCE-3 B2: a `--not-family` that names no family the registry
    carries excludes nothing, and the run reads as fenced while nothing is.
    The refusal names both halves: what was typed, and what could be fenced."""
    return ("--not-family %s names no family the registry carries (known: %s)"
            % (", ".join(unknown), ", ".join(sorted(known))))


def fence_blank_refusal():
    """FAMILYFENCE-5 item 4 (cross-family review Muse): `fence_family_names` drops
    a blank before the B2 check can call it unknown, so `--not-family ""` fenced
    nothing and the run planned and launched UNFENCED while reading as guarded —
    while MCP `spawn` had already refused that value as a broken argument. A blank
    is a broken argument on the CLI too, and is refused before any name beside it is
    judged, so one flag never answers rc 2 and another the fence's own code
    depending on what rode with it."""
    return ("--not-family takes a family name, not a blank value - a blank fences "
            "nothing, so the run would not be fenced at all")


def not_family_values(args):
    """The raw `--not-family` values as a list, exactly as the caller typed them.

    `fence_family_names` normalises names and drops blanks, so it cannot answer the
    question "did the caller hand me a blank" — this reads the argument before that
    normalisation and nothing else does (FAMILYFENCE-5 item 4). A bare string is one
    value, the same rule `fence_family_names` applies."""
    raw = getattr(args, "not_family", None)
    if raw is None:
        return []
    return [raw] if isinstance(raw, str) else list(raw)


def writer_family_of_run(run_id, read=None):
    """The family of the model that WROTE `run_id`'s diff, or None.

    The runner-private kill record and nothing else: job.json lives in the task
    directory the worker owns, so a writer that wanted a different family fenced
    off could put one there. A run with no record, or one whose writer never
    resolved, has no known family — and `family_fence` refuses the review rather
    than running it unfenced (FAMILYFENCE-4)."""
    if not run_id:
        return None
    record = (read or read_kill_record)(run_id) or {}
    family = (record.get("writer") or {}).get("family")
    if not family or family == WRITER_UNRESOLVED:
        return None
    return resolver.family_key(family)


def family_fence(args, registry=None):
    """The fence this run must respect, or None when nothing is excluded.

    Two sources, one answer: the families named with `--not-family`, and the writer
    family of the run named with `--review-of`. For a review role the writer family
    is excluded automatically when it is known, because an unrequested same-family
    review is the defect, not a configuration someone forgot to type.

    `strict` is the review role: a model the registry cannot place is NOT safe for a
    review either. An unknown reviewer name is the invented reviewer D-115 refuses —
    "I cannot tell you who this is" is never evidence of independence.

    `refusal` is FAMILYFENCE-4's answer to a `--review-of` whose writer family this
    checkout's record store cannot name: the caller asked for a fence and none can be
    built, so the run is refused (exit 2) rather than planned unfenced beside a
    warning. `warn_no_writer` is only the review that asked for nothing at all —
    neither `--review-of` nor `--not-family` — and its silence is what got measured.
    """
    given = fence_family_names(getattr(args, "not_family", None))
    review_of = getattr(args, "review_of", None)
    writer_family = writer_family_of_run(review_of)
    families = fence_family_names(list(given) + ([writer_family] if writer_family else []))
    try:
        card = routing.parse_card(getattr(args, "card", None) or "")
    except (ValueError, KeyError, TypeError):
        card = {}  # an unparseable card is the router's problem, refused downstream
    review = (_card_asks_review(card) if isinstance(card, dict) else False) or \
        getattr(args, "tier", None) == 3
    return {"families": families, "review": review, "strict": review,
            "writer_family": writer_family,
            "warn_no_writer": bool(review) and not review_of and not given,
            "refusal": (fence_unreadable_writer_refusal(review_of)
                        if review_of and not writer_family else None)}


def fence_blocks_model(spelling, registry, fence):
    """True when `spelling` may not serve a run carrying `fence`.

    The family is the registry's declaration for that spelling (`reviewer_family`,
    which also reads `policy.reviewers`), never a guess from the name. Unknown is
    unsafe only for a review — see `family_fence`."""
    if not fence or not fence.get("families"):
        return False
    family = reviewer_family(spelling, registry)
    if family is None:
        return bool(fence.get("strict"))
    return family in fence["families"]


def fence_blocks_route(route_id, registry, fence):
    """True when a gateway route may not serve a run carrying `fence`.

    EVERY leg counts, not the first: a combo is a fall-through list, so a route
    whose second leg is the writer's family can answer with it inside OmniRoute,
    where the spawner cannot see it. A leg the registry cannot place is unknown,
    and unknown is unsafe for a review.

    A route id the registry does not carry at all is unknown in the same way. A
    carried route with an EMPTY leg list is not: it declares no fall-through set,
    so no leg of it can be the writer's family, and what actually serves such a
    run (a --free run's promo model, for one) is fenced by `fence_blocks_model` at
    the moment it is chosen. Fencing it here would refuse the plan on a marker."""
    if not fence or not fence.get("families"):
        return False
    entry = (registry.get("routes") or {}).get(route_id)
    if entry is None:
        return bool(fence.get("strict"))
    legs = entry.get("legs") or []
    if not legs:
        return False
    for leg in legs:
        try:
            _, model_id = resolve_leg(leg, registry)
        except ValueError:
            model_id = None  # a leg the registry cannot resolve is an unknown family
        if fence_blocks_model(model_id or leg, registry, fence):
            return True
    return False


def fenced_route_ids(registry, fence):
    """Every route id the fence rules out, so a caller can drop them in one copy."""
    if not fence or not fence.get("families"):
        return set()
    return {rid for rid in (registry.get("routes") or {})
            if fence_blocks_route(rid, registry, fence)}


def _fence_check_route(combo, fence, registry=None):
    """Refuse a run whose chosen route the fence rules out.

    A combo the registry does not carry is left alone: this is a family rule, and
    "no leg list" is not a leg list that contains the writer's family. The privacy
    gate already refuses an id that is not a route where that matters."""
    if not combo or not fence or not fence.get("families"):
        return
    registry = registry if registry is not None else load_live_registry()
    if (registry.get("routes") or {}).get(combo) is None:
        return
    if fence_blocks_route(combo, registry, fence):
        raise FamilyFenceRefused(route_fence_refusal(fence))


def _fence_check_reviewer(asked, spelling, fence, registry=None):
    """Refuse a review whose `policy.reviewers` pick the fence rules out.

    The reviewer walk is cross-family to the card's ``author``; the fence is built
    from ``--review-of``'s WRITER (or the names ``--not-family`` gave). When those
    are two different models, every route the resolver saw can sit outside the fence
    while the reviewer that then replaces the run's combo sits squarely inside it —
    and no upstream check ever looked at it (FAMILYFENCE-5 item 1, cross-family
    review Muse).

    Judged on the model spelling, whose family is the registry's own
    `policy.reviewers` declaration (`reviewer_family`), and on the combo's legs
    where the registry carries that route — the same two reads the fence uses
    everywhere else, so the first choice and this one cannot drift."""
    if not spelling or not fence or not fence.get("families"):
        return
    registry = registry if registry is not None else load_live_registry()
    if fence_blocks_model(spelling, registry, fence):
        raise FamilyFenceRefused("%s: policy.reviewers picked %s" % (
            route_fence_refusal(fence), asked))
    _fence_check_route(model_route_id(spelling), fence, registry)


def fence_refusal(fence):
    """The one line that says why nothing ran."""
    return FAMILY_FENCE_REFUSAL % ", ".join((fence or {}).get("families") or ["?"])


def route_fence_refusal(fence):
    """A combo-based refusal, with the way out named (FAMILYFENCE-3 B1).

    The run is refused because every gateway COMBO it could reach carries a leg
    inside the fence — but the fence rules combos only while a combo serves. The
    two ways to serve from a model instead of a combo are the sentence's tail."""
    families = ", ".join((fence or {}).get("families") or ["?"])
    return (FAMILY_FENCE_REFUSAL % families) + \
        " - use --free or pin --model outside family %s" % families


def fence_free_head(chain, registry, fence):
    """The first model of the free `chain` the fence allows, or None.

    None is the whole plan being empty, and the caller refuses rather than starting
    the run on a fenced model: an excluded family that is merely deprioritised is
    still the family that writes the review."""
    for model in (chain or []):
        if not fence_blocks_model(model, registry, fence):
            return model
    return None


def fence_collision(family, fence):
    """True when the family that served a run is a family that fence rules out: the
    author's own family, or any name `--not-family` gave.

    One home for the question, because two callers answer it together — `cmd_run`
    refuses the run after it exits 0, and `cross_family_line` prints the verdict
    beside that refusal. They drifted apart once (FAMILYFENCE-3 N5): the line said
    `unknown` about the very collision the exit code acted on. Compared in
    `resolver.family_key` form, as both sides of a fence are stored."""
    key = resolver.family_key(family)
    if not key:
        return False
    author = resolver.family_key((fence or {}).get("writer_family"))
    return key == author or key in fence_family_names((fence or {}).get("families"))


def cross_family_line(writer, fence):
    """The review run's own verdict on its independence, beside the writer line.

    `writer` is the family of the model that wrote the diff being read
    (`--review-of`'s record), `reviewer` the family that ACTUALLY served this run.
    `NO` is printed when the family that served is a family this run was told to
    stay off — either the author's own family or any family of `--not-family` — and
    `cmd_run` refuses the run on exactly that answer, because a same-family verdict
    that exits 0 is the lie this whole fence exists to stop.

    FAMILYFENCE-b: `reviewer` counts only when a witness attested to it. A model
    the plan assumed is a model that may never have run — qodercli substitutes an
    unknown `--model` and exits 0 — so an unproven reviewer is reported as
    `unresolved` and the verdict is never `yes`. An independence nobody can show is
    not an independence to claim; the run still goes ahead, because requirement 3
    asks for the sentence, not for a second refusal.

    FAMILYFENCE-3 N5: it was never a licence to print `unknown` about a collision
    the run is being REFUSED for. `cmd_run`'s backstop reads the serving family
    whether or not a witness attested to it, so an unattested reviewer that landed
    inside the fence printed `CROSS-FAMILY: unknown` next to exit 12 — a log that
    said "cannot tell" about the one thing the spawner had just acted on. `NO` is
    now that collision too, marked `(assumed)` when nothing witnessed the model, so
    the sentence and the exit code answer the same question at the same strength.
    """
    family = (writer or {}).get("family") or WRITER_UNRESOLVED
    reviewer = family if writer_is_proven(writer) else WRITER_UNRESOLVED
    author = (fence or {}).get("writer_family") or WRITER_UNRESOLVED
    unknown = (WRITER_UNRESOLVED, None, "")
    # The collision the backstop refuses on, from the one predicate both read.
    fenced = fence_collision(family, fence)
    if fenced:
        verdict = "NO" if reviewer not in unknown else "NO (assumed)"
    elif reviewer in unknown or author in unknown:
        verdict = "unknown"
    else:
        verdict = "yes"
    return "family: writer=%s reviewer=%s CROSS-FAMILY: %s" % (author, reviewer, verdict)


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
            "overlay": overlay_mod.status(MEASURED_OVERLAY_PATH, LEGACY_OVERLAY_PATH),
            "exit_code": rc}
    return data, rc


def cmd_heartbeat(args) -> int:
    """Print the read-only heartbeat report (spec: R-heartbeat-02/03,
    R-pause-01, R-handoff-07). Never pushes, commits or writes anything - see
    tools/autoos_heartbeat.py's own docstring."""
    data, rc = heartbeat_state(args.inbox, args.transcript, args.repos, args.cap)
    if args.json:
        print(json.dumps({k: data[k] for k in
                          ("pause", "repos", "context", "over_cap", "exit_code",
                           "overlay")}))
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


def load_measured_overlay() -> tuple:
    """(overlay, missing_at): the machine-wide overlay, else the legacy per-checkout
    one (with a stderr note), else ({}, MEASURED_OVERLAY_PATH) so `route` can say
    so out loud instead of calling every agentic leg "unproven" (OVERLAYHOME)."""
    missing = None
    if overlay_mod.found(MEASURED_OVERLAY_PATH, LEGACY_OVERLAY_PATH) is None:
        missing = MEASURED_OVERLAY_PATH
    return overlay_mod.load(MEASURED_OVERLAY_PATH, LEGACY_OVERLAY_PATH), missing


# FREEKEYS-2 (brief item 2): the credit guard's per-process cache. `plan` is
# called once per candidate route and a `run` re-plans on every fall-through, so
# without this the refuse-at-100 % check would page the gateway's call log again
# for every one of them. One read, one answer, per registry, for the life of the
# process — and keyed by the registry it was built from (FREEKEYS-2c): a second
# registry in the same process has its own caps, and must not inherit the first
# one's answer. The entry keeps a reference to that registry, both to make the
# identity check (`is`) meaningful and to pin the object, so `id()` can never be
# recycled out from under a live key.
CREDIT_GUARD_CACHE: dict = {}


def _credit_guards_unreadable(registry: dict, why: str) -> dict:
    """Every `credit` grant refuses, because nobody can say what it has spent.

    The half of the guard that must not be optimistic (brief FREEKEYS-2 item 2):
    a usage read that fails leaves the resolver with no spend figure, and "no
    figure" is not "$0 left" — it is the state that let a $10 grant drain
    invisibly before. Returning `refuse` per grant drops only the credit legs of
    a route, so a card with a free leg still plans; returning `{}` would drop
    nothing and let the grant spend past its cap.

    `why` is an exception *type name*, never its message: a gateway error text
    can carry the URL and a key-file error the home path, and this note is
    printed into the plan, the run log and `route --explain` (AGENTS.md rule 1).
    """
    out = {}
    for provider in usage_mod.credit_guard_providers(registry):
        cap = warn = 0.0
        try:
            cap = usage_mod.monthly_cap_usd(registry, provider)
            warn = usage_mod.spend_warn_usd(registry, provider)
        except ValueError:
            pass  # a grant with no cap cannot be judged, only refused
        out[provider] = {"provider": provider, "state": "refuse", "spend_usd": 0.0,
                         "cap_usd": cap, "warn_usd": warn, "models_unpriced": 0,
                         "note": "credit grant unreadable (%s): no spend data, so "
                                 "the leg is refused until the gateway answers" % why}
    return out


def plan_credit_guards(registry: dict, now=None, fetch=None,
                       env: dict | None = None) -> dict:
    """The `{provider: guard}` map `autoos_resolver.usable_legs` refuses a credit
    leg with (brief FREEKEYS-2 item 2): this month's spend per `credit` provider
    against its own `monthly_cap_usd`, read from the gateway's call log.

    Cheap by design — one usage read per registry per process
    (`CREDIT_GUARD_CACHE`), and a host with no `credit` provider in the registry
    makes no call at all. The figure is built by `autoos_usage.credit_guards`,
    the same reader the `usage` report prints, so what blocks a leg and what the
    ledger shows are never two numbers. A read that fails (gateway down, key
    missing or unauthorised, an unparseable page) returns
    `_credit_guards_unreadable`, not an empty map.

    Two things make this safe inside a plan (FREEKEYS-2c, rev-freekeys2 finding
    4). The cache is keyed by `registry` identity, so a caller that hands this a
    different registry — a candidate registry compared against the committed one,
    a lane that reloads — gets guards built from that registry's own caps
    instead of the first one's answer. And the usage read is wrapped in
    `except Exception`, not a list of expected types: every failure mode this
    can predict already refuses the credit legs, and one it cannot predict must
    do the same rather than raise through `route_plan_for` and take the plan
    down with it. `KeyboardInterrupt`/`SystemExit` are `BaseException`, outside
    `Exception`, so Ctrl-C still works.

    `fetch`/`env`/`now` are injectable so a test can drive this without a
    gateway, a key or the clock; the callers pass none of them.
    """
    cache_key = id(registry)
    cached = CREDIT_GUARD_CACHE.get(cache_key)
    if cached is not None and cached["registry"] is registry:
        return cached["guards"]
    providers = usage_mod.credit_guard_providers(registry)
    if not providers:
        CREDIT_GUARD_CACHE[cache_key] = {"registry": registry, "guards": {}}
        return CREDIT_GUARD_CACHE[cache_key]["guards"]
    env = os.environ if env is None else env
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = usage_mod.month_start(now)
    gateway = (env.get("AUTOOS_OMNIROUTE_URL")
               or usage_mod.DEFAULT_GATEWAY).rstrip("/")
    fetch = fetch or usage_mod.urllib_fetch
    try:
        key = usage_mod.read_manage_key(usage_mod.key_file_path(env))
        rows, _pages, _truncated = usage_mod.fetch_window(fetch, gateway, key, cutoff)
        guards = usage_mod.credit_guards(registry, rows, cutoff)
    except Exception as exc:  # noqa: BLE001 - fail closed, never fail the plan
        guards = _credit_guards_unreadable(registry, type(exc).__name__)
    CREDIT_GUARD_CACHE[cache_key] = {"registry": registry, "guards": guards}
    return guards


def route_plan_for(card, brief: str, repo: str, orchestrator_model: str, now,
                   registry: dict, overlay: dict, track_record: list,
                   client_state: dict, client: str = "opencode",
                   env: dict | None = None,
                   overlay_missing_at: str | None = None,
                   credit_guards: dict | None = None) -> dict:
    """card -> route_plan (spec 6.1/6.2): the CLI `route` subcommand and the MCP
    `route` tool's shared, pure-ish core.

    `card` is a task card exactly as `run --card` accepts it - text
    (``kind=review,paths=...`` or a JSON object string, parsed by
    ``routing.parse_card``) - or already a dict (the MCP tool's own shape).
    `client`/`env` (CLAUDEBUDGET-b item 3) are the two things the Claude gate
    asks about: the client because a run *inside* Claude Code spends the
    allowance whatever the route says, and the env because the critical-path
    exception belongs to the orchestrator that set it, not to the card. A caller
    that left them out gets the conservative answer -- client "opencode", no
    declaration -- never a Claude leg it was not entitled to.

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
    result = resolver.plan(normalized, features, client_state, registry,
                           overlay, track_record, orchestrator_model, now,
                           client, env, credit_guards)
    # OVERLAYHOME: with no overlay file at all, "tool_calls: ... unproven" is
    # the machine's missing data, not the legs' verdict - say which.
    if (overlay_missing_at and result.get("state") == "input_required"
            and result.get("unproven_toolcalls")):
        result = dict(result)
        result["reason"] = "%s; %s" % (overlay_mod.missing_reason(overlay_missing_at),
                                       result["reason"])
    # D-102 CLAUDEBUDGET: the budget state leads every explain block, ON or off.
    # A plan that dropped a Claude leg looks identical to one that never had a
    # Claude candidate, and `route --explain` is what an operator reads to tell
    # them apart. A budget-deferred plan already carries the line (plan() has no
    # routes to explain), so it is not added twice.
    budget_line = resolver.claude_budget_explain(registry)[0]
    explain = result.get("explain") or []
    if explain and explain[0].startswith("claude_budget:"):
        return result
    return dict(result, explain=[budget_line] + list(explain))


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
        overlay, overlay_missing_at = load_measured_overlay()
    except (OSError, ValueError) as exc:
        return refuse("cannot load routing data: %s" % exc)
    track_record = track.load(TRACK_RECORD)
    client_state = measure_mod.client_state(clients)
    try:
        result = route_plan_for(args.card, args.brief or "", repo,
                                args.orchestrator_model, now, registry, overlay,
                                track_record, client_state,
                                getattr(args, "client", None) or "opencode",
                                os.environ, overlay_missing_at=overlay_missing_at,
                                credit_guards=plan_credit_guards(registry))
    except (routing.CardError, ValueError) as exc:
        return refuse(str(exc))
    if args.explain:
        for line in result.get("explain") or []:
            print(line, file=sys.stderr)
        print(result.get("reason", ""), file=sys.stderr)
    print(json.dumps(result, sort_keys=True, indent=2))
    # A budget-deferred plan carries no route (there is nothing to run yet), but
    # it is an answer, not a refusal: exit 5 means "input_required", and a
    # caller that retries on 5 would spin against a policy that is working.
    if result.get("route") is not None or result.get("state") == "deferred":
        return 0
    return 5


def log_run(plan: dict, rc: int, secs: float, free: bool) -> None:
    logs = os.path.join(ROOT, "logs")
    os.makedirs(logs, exist_ok=True)
    route = plan["route"]
    card = ",".join("%s=%s" % kv for kv in sorted((route.get("card") or {}).items())) or "-"
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
    # RATELIMITRETRY (SB-B, D-103 item 1): 'rate limit exceeded' is ONE client's
    # wording, and the run that died in the field said 'Rate limit' — so the
    # family is matched, not the phrase. The error-prefix rule below is what
    # keeps the task's own words out of it, not a narrow marker list.
    "rate limit",
    "too many requests",
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
# SB-B (D-103) item 1 raised this from 2 to 3 for the rate-limit case, and the
# bound is one number rather than two: a run that falls through a 429 and a run
# that falls through a 503 are the same act — try the next leg — and a second
# counter would only be a second thing to keep in sync.
MAX_FALLTHROUGH = 3

# RATELIMITRETRY: how long the provider that just rate-limited stays benched
# INSIDE this process. A stated reset ("resets in ~83h") is recorded for every
# later routing read by record_reset_stop; an unstated 429 is the common case
# and would otherwise leave the next attempt on the same shared account. One
# bench window per fallthrough, so a 3-fallthrough run walks off the rate limit
# of at most three providers.
RATELIMIT_COOLDOWN_SECONDS = 300


def fallthrough_line(combo: str, stop: str, next_combo: str) -> str:
    """The one line printed when a provider-stopped attempt falls through.

    Wording is pinned by a test rather than by the caller; `stop` is a quote of
    the worker's own text, so it goes through the redactor (SPAWNREDACT item 2).
    """
    return "provider stop on %s: %s -> falling through to %s" % (
        combo, redact_output(stop), next_combo)

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


def provider_stop(tail: str, stderr_tail: str | None = None) -> str | None:
    """The provider-stop line among the client's last lines, or None.

    Only the last PROVIDER_STOP_WINDOW non-empty lines of `tail` are scanned:
    a real provider stop is the client's last output before it exits, so a
    marker quoted earlier in the run must not match. Inside the window a line
    only when, after lstrip() and removing ANSI colour codes, it STARTS with
    an error prefix (PROVIDER_STOP_PREFIXES, case-insensitive) AND contains a
    PROVIDER_STOP_MARKERS entry (WIPfix3) or a bare 429 status
    (_STATUS_429_RE, RATELIMITRETRY). Returns the matching line nearest the end
    when several are in the window.

    `stderr_tail` (ERRCHANNEL, SB-B review 1) is the same window of the client's
    OWN stderr channel. Given, a rate-limit line is accepted only when that
    channel says it too: a worker quoting `Error: 429` out of a log it cat'ed, a
    fixture it wrote or the brief it was handed, is the task talking about an
    outage, not a provider refusing one - and the widened rate-limit family the
    field needed is exactly the wording such a line carries. The other markers
    keep the merged window, so a 503 or a billing wall is still caught wherever
    the client printed it. Absent (a caller with no channel split: every unit
    test, every non-capture client) nothing is re-required, which is how this
    classified before the review.
    """
    lines = [line for line in (tail or "").splitlines() if line.strip()]
    err = None
    if stderr_tail is not None:
        err = {_ANSI_RE.sub("", ln.lstrip())
               for ln in stderr_tail.splitlines() if ln.strip()}
    for line in reversed(lines[-PROVIDER_STOP_WINDOW:]):
        clean = _ANSI_RE.sub("", line.lstrip())
        low = clean.lower()
        if not low.startswith(PROVIDER_STOP_PREFIXES):
            continue
        if err is not None and _is_rate_limit_line(low) and clean not in err:
            continue
        if (any(marker in low for marker in PROVIDER_STOP_MARKERS)
                or _is_rate_limit_line(low)):
            return clean
    return None


# RATELIMITRETRY (SB-B, D-103 item 1). '429' as a status of its own, not a
# fragment: a digit or a dot on either side disqualifies it, so a model id or a
# version that merely contains the three digits is not an outage. Whether the line
# is a provider error at all stays `provider_stop`'s prefix rule to decide — this
# only says which digits count as the status.
_STATUS_429_RE = re.compile(r"(?<![\d.])429(?![\d.])")
_RATE_LIMIT_MARKERS = ("rate limit", "too many requests")


def _is_rate_limit_line(low: str) -> bool:
    """Does this already-lowercased, already-cleaned line say a RATE limit?

    One spelling of the family for both checks: `provider_stop` uses it to decide
    which lines need the stderr channel (ERRCHANNEL) and `rate_limit_stop` uses it
    to decide which stop benches a provider."""
    return (any(marker in low for marker in _RATE_LIMIT_MARKERS)
            or _STATUS_429_RE.search(low) is not None)


def rate_limit_stop(line: str) -> bool:
    """True when a stop line is a rate limit (429) rather than another outage.

    A rate limit says WHO is out of capacity, so the next attempt is picked for a
    DIFFERENT provider; a 503-all-targets or an exhausted-credit line says
    something else, and re-trying the same account on it is not a plan.

    Call it on a line `provider_stop` returned, never on raw output: the channel
    rule that keeps a worker's own quoted `Error: 429` out of the bench lives in
    that call, and re-deriving it here from text alone is how the same line would
    get a second, unfenced chance.
    """
    return _is_rate_limit_line(_ANSI_RE.sub("", (line or "").lstrip()).lower())


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


def combo_legs(combo, registry) -> list:
    """The route's own `provider/model` legs, as the registry spells them."""
    return ((registry.get("routes") or {}).get(combo) or {}).get("legs") or []


def combo_providers(combo, registry) -> list:
    """The provider ids a route's legs name, in leg order and de-duplicated.

    Empty for a route with no legs, an unknown route, or legs that do not resolve
    — a caller that cannot tell who serves a combo must not guess and bench the
    wrong provider (the same rule `stop_provider_id` states for a stop line)."""
    out = []
    for leg in combo_legs(combo, registry):
        pid = (_leg_provider_model(leg, registry) or (None, None))[0]
        if pid and pid not in out:
            out.append(pid)
    return out


def combo_is_benched(combo, cooldown, registry) -> bool:
    """True when every leg of `combo` sits on a provider this process benched.

    A combo with an unresolvable leg is not benched: "we do not know who serves
    this" is a reason to try it, not a reason to give up on it."""
    if not cooldown:
        return False
    pids = combo_providers(combo, registry)
    return bool(pids) and all(pid in cooldown for pid in pids)


def rate_limit_bench(stop_line: str, plan: dict, registry: dict) -> str | None:
    """The provider this process benches after a rate limit, or None.

    A --free run's combo is a `provider/model` promo id rather than a registry
    route, so the route read finds nothing and the model's own prefix is the
    answer (the free-leg queue already reads a provider that way).

    `stop_provider_id` is handed the route's LEGS, not its provider ids: it
    resolves each leg through the registry itself, and a `provider` id with no
    `/model` after it is not a leg."""
    combo = (plan.get("route") or {}).get("combo")
    pid = stop_provider_id(stop_line or "", registry, combo_legs(combo, registry))
    if pid is not None:
        return pid
    model = plan.get("model") or ""
    return free_provider(model) if "/" in model else None


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
                              # REDACTFIX3 (S1): `stop_line` is the child's OWN
                              # text (REDACTFIX item 2 kept it raw so a masked
                              # stop marker can still be classified), and the
                              # window and the provider above are read off it
                              # while that is true. This dict is a copy that
                              # leaves the process into logs/, where it outlives
                              # the run, so it is redacted like every other one.
                              "reason": redact_output(stop_line),
                              "recorded_at": _iso_zulu(now)}
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


# MODEFLIP (SB-B, D-103 item 3). `git diff --raw` line, read with --no-renames:
# :<old mode> <new mode> <old blob> <new blob> <status>\t<path>
_MODE_ONLY_RAW_RE = re.compile(r"^:(\d{6}) (\d{6}) ([0-9a-f]{4,40}) ([0-9a-f]{4,40}) ([A-Z]+)\t(.*)$")


def _git_stdout(proc) -> str:
    """A read's stdout when git answered, "" when it could not."""
    return proc.stdout if proc.returncode == 0 else ""


def sandbox_mode_only(path: str, base: str, rev: str) -> str | None:
    """A description of the sandbox's diff when it is ONLY file-mode changes, else None.

    100644 <-> 100755 with the same blob on both sides is a mode flip: no byte of
    content moved (a checkout on a filesystem without the exec bit, or a worker
    that reached for chmod instead of editing). Both reads count — `base..rev`
    for the commits the run made and the worktree for what it left uncommitted —
    and any untracked or added file disqualifies the diff, because that is the
    work the run came for. An EMPTY diff returns None too: that is the NO-OP
    verdict's own case, and this is about a diff that looks like work and is not.
    A git that cannot answer reads as an empty diff: the run keeps the verdict
    it claimed rather than a refusal built on no evidence.
    """
    rows = [
        _git_stdout(subprocess.run(
            ["git", "-C", path, "diff", "--raw", "--no-renames", base + ".." + rev],
            capture_output=True, text=True)),
        _git_stdout(subprocess.run(
            ["git", "-C", path, "diff", "--raw", "--no-renames", "HEAD"],
            capture_output=True, text=True)),
    ]
    status = _git_stdout(subprocess.run(
        ["git", "-C", path, "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True))
    if any(ln.startswith("??") or ln.startswith("A ") for ln in status.splitlines()):
        return None
    entries = [ln for block in rows for ln in block.splitlines() if ln.strip()]
    if not entries:
        return None
    seen = []
    for line in entries:
        m = _MODE_ONLY_RAW_RE.match(line)
        if m is None:
            return None
        old_mode, new_mode, old_blob, new_blob = m.group(1), m.group(2), m.group(3), m.group(4)
        if old_blob != new_blob or old_mode == new_mode or set(old_blob) == {"0"}:
            return None
        seen.append("%s -> %s %s" % (old_mode, new_mode, m.group(6)))
    return "; ".join(seen)


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


def _reflog_count(root: str, ref: str) -> int:
    """How many entries `ref`'s own reflog has (0 when it has none at all)."""
    r = subprocess.run(["git", "-C", root, "reflog", "show", ref],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 0
    return len([ln for ln in r.stdout.splitlines() if ln.strip()])


def _reflog_len(root: str, branch: str) -> int:
    return _reflog_count(root, "refs/heads/" + branch)


# The refs a sandbox run can move. HEAD and its own branch carry a commit the
# run made; refs/stash is the same commit hidden from the worktree by
# `git stash` instead of a reset.
SANDBOX_REFLOG_REFS = ("HEAD", "refs/stash")


def sandbox_reflog_snapshot(path: str, branch: str) -> dict:
    """{"counts": {ref: reflog length}, "known": {sha, ...}} for a sandbox.

    Taken before the client starts, so the clone and its `switch -c` are already
    in it. `counts` bounds the reflog read to what the run appended; `known` is
    every commit that already existed anywhere in the clone (`git rev-list
    --all`, and `--all` walks every fetched ref, so a full clone's whole history
    costs ~15 ms for ~1.4 k commits here) — the shas a run may legitimately move
    HEAD onto. A commit the run itself made is in neither.
    """
    refs = list(SANDBOX_REFLOG_REFS) + ["refs/heads/" + branch]
    r = subprocess.run(["git", "-C", path, "rev-list", "--all"],
                       capture_output=True, text=True)
    known = set(r.stdout.split()) if r.returncode == 0 else set()
    return {"counts": {ref: _reflog_count(path, ref) for ref in refs},
            "known": known}


def sandbox_reflog_writes(path: str, snapshot, base: str) -> list:
    """Short shas of commits the sandbox's own reflogs still show beyond `base`.

    A run that edits, commits and then `reset --hard`s back to its base leaves a
    clean final `git status --short` and an empty `base..branch` - the only two
    reads sandbox_verdict used to make, and the escape SPAWNFIX3c (S2) item 2
    measured. A reflog entry whose new value carries commits past `base` IS that
    commit. The run's own moves are not: a switch, a checkout or a reset onto a
    commit that already existed (`snapshot["known"]`, taken before the client
    started) is a research run reading history, and a reset back onto `base`
    names `base` itself, which `base..sha` renders empty.

    Same shape as parent_leak's commit-then-reset scan, with one difference:
    there the worker identity is the signal (an orchestrator legitimately merges
    into a parent); here the sandbox is private to the run, so ANY commit past
    its base is the write. Returns [] when the snapshot is missing (a run that
    never took one).
    """
    counts = (snapshot or {}).get("counts") or {}
    known = (snapshot or {}).get("known") or set()
    found = set()
    for ref, before in counts.items():
        new = _reflog_count(path, ref) - before
        if new <= 0:
            continue
        r = subprocess.run(["git", "-C", path, "reflog", "show", "--format=%H",
                            "-n", str(new), ref], capture_output=True, text=True)
        if r.returncode != 0:
            continue
        for sha in [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]:
            if sha == base or sha in known:
                continue
            probe = subprocess.run(["git", "-C", path, "rev-list", "--max-count=1",
                                    base + ".." + sha],
                                   capture_output=True, text=True)
            if probe.returncode == 0 and probe.stdout.strip():
                found.add(sha[:10])
    return sorted(found)


def sandbox_ref_heads(sandbox: str) -> dict:
    """``{refname: sha}`` for every ref the sandbox clone carries."""
    out = subprocess.run(["git", "-C", sandbox, "for-each-ref",
                          "--format=%(refname) %(objectname)"],
                         capture_output=True, text=True)
    heads = {}
    for line in out.stdout.splitlines():
        name, _, sha = line.partition(" ")
        if name and sha.strip():
            heads[name] = sha.strip()
    return heads


def sandbox_committed_work(sandbox: str, base: str, branch: str, start: dict) -> list:
    """Every ref the run moved off the start sha, as ``"ref sha subject"`` lines.

    SB-A (D-103) item 2 (NOOPCOMMIT): the no-change verdict read the uncommitted
    porcelain and `<start-sha>..<sandbox-branch>`, so a worker that committed in
    its own sandbox on a branch it made — or on a detached HEAD — was graded
    "NO-OP (agent changed nothing)" with a real commit sitting in the clone. The
    start snapshot (taken before the client runs) is what makes this cheap and
    honest: a `--local` clone carries every branch of the parent, so "differs
    from the start sha" alone would report pre-existing lanes as this run's work,
    and a ref reset back to the start sha is still no work at all.

    SB-A2 (D-103) item C completes that comparison: it is against the FULL start
    SET of tips (and the start HEAD, which is `base`), not the sha each name held.
    A worker that only `git switch`ed onto a ref that already existed — another
    lane in the clone — moved no tip at all, yet the per-name read saw a name it
    had never seen and billed the pre-existing commit as this run's work. A tip
    that existed when the run started is not work, whichever name points at it now.
    """
    end = sandbox_ref_heads(sandbox)
    known = {sha for sha in (start or {}).values()}
    if base:
        known.add(base)
    found = []
    moved = set()
    for name in sorted(end):
        if end[name] in known:
            continue
        moved.add(end[name])
        found.append(sandbox_ref_subject(sandbox, name, end[name]))
    head = subprocess.run(["git", "-C", sandbox, "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    tip = end.get("refs/heads/" + branch)
    if head and head not in known and head != tip and head not in moved:
        found.append(sandbox_ref_subject(sandbox, "HEAD-detached", head))
    return found


def sandbox_ref_subject(sandbox: str, ref: str, sha: str) -> str:
    """One line naming a ref, its short sha and its subject (worker text)."""
    out = subprocess.run(["git", "-C", sandbox, "log", "-1", "--format=%h %s", sha],
                         capture_output=True, text=True)
    return "%s %s" % (ref, out.stdout.strip() or sha[:10])


# The return contract (docs/agent-protocol.md): a finished worker prints a
# REPORT heading, optionally wrapped in markdown (`**REPORT**`, `# REPORT:`).
# Prose that merely mentions a report is not one; neither is a word that only
# starts with the same letters ("REPORTED") or a file named after it
# ("REPORT.md" — measured in work/L1-routing/A7spike.out, whose run wrote its
# report to a file and named it in its closing line).
REPORT_HEADING_RE = re.compile(
    r"(?im)^\s*(?:[#>*-]+\s*)?(?:\*\*)?REPORT(?:\*\*)?(?![\w.])")

# An inline-code span (backticks) or a string/regex literal (quotes): the shapes
# a line carries when it QUOTES something instead of saying something.
_QUOTED_SPAN_RE = re.compile(r"`[^`]*`|\"[^\"]*\"|'[^']*'")

# The word rendered AS the heading convention: markup touching it (`**REPORT**`,
# `# REPORT:`) or a regex escape next to it (\breport\b). A span that quotes an
# ordinary message string ("RESEARCH: report only") names a message the worker
# saw, and a real report does exactly that (SPAWNFIX3c.out).
_SELFQUOTE_MARKUP_RE = re.compile(
    r"[*#>?+|]{1,3}\s*\breport\b"        # `**REPORT**`, `# REPORT:`
    r"|\breport\b\s*[*#>?+|]"            # the bold close, `REPORT*`
    r"|\\[a-zA-Z][*+?\s]*report"         # \breport, \s*report
    r"|report[*+?\s]*\\[a-zA-Z]",        # report\b, report\s*
    re.IGNORECASE)


def is_self_quoting(line: str) -> bool:
    """True when a heading line displays the heading convention itself.

    The old rule counted the word "report" in the line and rejected two or more
    mentions; that also rejected the real headers of SPAWNFIX3c.out and
    OR34spike.out, which say it twice because they are long lines of prose.
    Only a QUOTED mention that carries the markup counts: the spawner's own
    comment is out, a run's own sentence is in.
    """
    for span in _QUOTED_SPAN_RE.findall(line):
        if _SELFQUOTE_MARKUP_RE.search(span):
            return True
    return False


# The lines a client's harness prints for its own tool calls, as opposed to its
# message text. Everything before the LAST of them is transcript: a REPORT
# heading there belongs to an earlier turn (or to a run that died in that tool
# call), not to the closing message the return contract asks for. Anchored at
# the line start and deliberately tiny: a report body may quote any of these
# shapes mid-sentence, and a false INCOMPLETE relaunches a finished research run.
FINAL_SEGMENT_MARKER_RES = (
    # agy prints one of these per tool event - the same prefix
    # headless_refusal() trusts (HEADLESS_REFUSAL_PREFIX).
    re.compile(r"^jetski[:>]", re.IGNORECASE),
    # A tool call as the captured transcript renders it: `Read(path)`,
    # `Bash(git status)`, `apply_patch(...)`.
    re.compile(r"^(?:read|write|edit|glob|grep|bash|shell|exec|search|find|task|think"
               r"|apply_patch|webfetch|web_fetch|web_search|fetch|question)\w*\s*\(",
               re.IGNORECASE),
)

INCOMPLETE_MESSAGE = ("INCOMPLETE: the worker stopped without a REPORT - "
                      "relaunch it (never resume)")


def final_message_segment(output: str, brief: str = "") -> str:
    """The part of a captured client tail that is its closing message.

    run_client merges the child's stdout+stderr and keeps the last TAIL_LIMIT
    bytes verbatim, so the tail is the whole raw transcript: the task text the
    client echoed, its tool output, and only at the end the assistant's final
    message. Two things come out of it here:

    - everything up to the last tool-call line (FINAL_SEGMENT_MARKER_RES): the
      client was still working there, and whatever it or its tools printed in
      that part is the transcript, quotations included;
    - the brief's own lines: a worker that cats or echoes its brief prints a
      line starting "REPORT:" (every brief ends with the return contract's field
      list) and that is the spawner's text, not the worker's report.

    What is left is what the client's final message said. A tool RESULT printed
    plainly after its own command echo is indistinguishable from message text in
    a merged stream, which is why the brief rule above is the one that carries
    the measured case - and why fences are not stripped: in the final message a
    fenced REPORT heading is the message (SPAWNFIX3d).
    """
    quoted = {ln.strip() for ln in (brief or "").splitlines() if ln.strip()}
    lines = [(_ANSI_RE.sub("", raw).strip()) for raw in (output or "").splitlines()]
    start = 0
    for i, line in enumerate(lines):
        if line and any(m.match(line) for m in FINAL_SEGMENT_MARKER_RES):
            start = i + 1
    return "\n".join(ln for ln in lines[start:] if ln and ln not in quoted)


def has_report(output: str, brief: str = "") -> bool:
    """True when the client's FINAL MESSAGE carries a REPORT heading line.

    `brief` is the text this run sent the client (plan["brief"]): its lines never
    count, because echoing the brief back is not reporting on the task (that is
    final_message_segment's filter). A heading line that renders the heading
    markup itself quotes the contract instead of filing a report
    (is_self_quoting).
    """
    for line in final_message_segment(output, brief).splitlines():
        if REPORT_HEADING_RE.search(line) and not is_self_quoting(line):
            return True
    return False


def sandbox_verdict(route: dict, changed: str, ahead: str, output: str = "",
                    diffstat: str = "", reflog: str = "", brief: str = "",
                    extra: str = ""):
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

    SPAWNFIX3c (S2) item 2: a read-only run's two reads were the final
    `git status --short` and `base..branch`, so a worker that committed and
    `reset --hard` back to base escaped with an empty sandbox in both. `reflog`
    is what sandbox_reflog_writes found in the sandbox's own reflogs (short shas,
    comma-joined) and it counts as a change for the read-only verdict ONLY: a
    writer run that reset its own work away is still the unfinished run its
    porcelain says it is, never a pass.

    `brief` (item 1) is the text this run sent the client: has_report() must not
    read the worker's own instructions back to it as its report.

    SB-A (D-103) item 2 (NOOPCOMMIT): `extra` is what sandbox_committed_work
    found by comparing every ref (and a detached HEAD) to the start sha — a
    commit on a branch the worker made is a change, even though the porcelain is
    clean and `<start-sha>..<sandbox-branch>` is empty.
    """
    if route.get("review"):
        return None, ""
    read_only = bool(route.get("read_only"))
    if read_only and (changed or ahead or reflog or extra):
        detail = diffstat or "(no diff stat)"
        if extra:
            detail += "; commits it left on another ref: %s" % extra
        if reflog:
            detail += ("; commits its reflog still shows, beyond the base: %s "
                       "(commit then reset; the sandbox reflog is the only witness)"
                       % reflog)
        return EXIT_READ_ONLY_WRITE, (
            "READ-ONLY WRITE: a read-only run changed its sandbox "
            "(exit %d) - its deliverable was the report, never the edit: %s"
            % (EXIT_READ_ONLY_WRITE, detail))
    if not changed and not ahead:
        if extra:
            # SB-A (D-103) item 2 (NOOPCOMMIT): the commit is there, the
            # porcelain and `<start-sha>..<branch>` just never looked at it.
            return None, ""
        if not has_report(output, brief):
            return EXIT_INCOMPLETE, INCOMPLETE_MESSAGE
        if read_only:
            return 0, "RESEARCH: report only"
        return 5, ("NO-OP: the agent changed nothing in its sandbox - treat its report as "
                   "unverified and the run as failed (exit 5)")
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
        # SPAWNREDACT item 2: the stop line is a quote of the worker's output,
        # and a commit message outlives every log rotation. Redact it again --
        # since REDACTFIX item 2 the stop cmd_run hands us is the child's own
        # (raw) text, so this is the copy's only masking pass.
        msg += "; provider stop: %s" % redact_output(stop)
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
    included. The NO-OTHER-FAMILY fence (12) is "refusal" like rc 6: the fence
    refused, so the route never got the chance to be unreliable (FAMILYFENCE).

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
                          ("refusal" if rc in (6, EXIT_NO_OTHER_FAMILY) else
                           ("containment" if rc == 7 else
                            ("provider" if rc == 8 else "logic"))))),
    }


def record_run(path: str, entry: dict) -> bool:
    """Append one track record; failing to record never changes the run's exit
    code. SPAWNREDACT item 2: the entry is redacted at the one choke point every
    record passes, so no field of a new entry type can be written raw."""
    try:
        track.record(path, redact_record(entry))
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
                    proposals_path: str, sandbox_path: str,
                    legacy_path: str | None = None) -> None:
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
    used = overlay_mod.found(overlay_path, legacy_path)
    if used is not None:
        try:
            with io.open(used, encoding="utf-8") as fh:
                overlay = json.load(fh)
        except (OSError, ValueError) as exc:
            print("autoos-agent: probe proposal skipped (cannot load %s): %s"
                  % (used, getattr(exc, "strerror", None) or exc), file=sys.stderr)
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



def proc_start_time(pid) -> int | None:
    """Field 22 (starttime, clock ticks since boot) of /proc/<pid>/stat, or None.

    SB-A2 (D-103) item B: this is the one number that tells a pgid the runner
    recorded apart from the same number the kernel handed to an unrelated process
    after pid wrap. It is read for the group LEADER only, because a leader's
    death is what frees the number.
    """
    if os.name == "nt":
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        with io.open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return None
    # Field 2 (comm) is parenthesised and can hold spaces and ')', so count the
    # fields from the far end of it: what follows is field 3 onward.
    tail = data.rsplit(") ", 1)
    if len(tail) != 2:
        return None
    fields = tail[1].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])       # field 22 overall, fields[0] is field 3
    except ValueError:
        return None


def kill_store_dir() -> str:
    """The runner-private home of each run's decided-at-spawn record (SB-A3,
    D-103 item C; the run MODE joined it in SB-A4).

    Deliberately NOT `AUTOOS_TASK_DIR`: the run dir is handed to the worker as an
    environment variable, the worker is the same uid as everything in it, and a
    kill record the cancelled process can rewrite is a channel it gives orders
    through — which is what SB-A's `pgrp.json` and SB-A2's `job.json` group were.
    This is a sibling of the `agents/` tree, 0700 with 0600 files, written by the
    server and the runner and by nobody else.

    RESIDUAL, stated rather than papered over: a same-uid worker that goes looking
    can still find this directory (the state dir is in its environment) and write
    into it, because a file cannot be hidden from the uid that owns it. What
    closes that hole is the scope path, which is derived from the run id and
    depends on no file at all; the private store is only the fallback for a host
    with no user manager.

    SB-B merge: this moved here from the fleet server, because the CLI is the
    process that starts and re-starts the client — an attempt's group has to reach
    the same record from inside `run_client`, and a second copy of a store is a
    second place a killer's answer can come from. The server reads and writes it
    through this one implementation.
    """
    return os.path.join(clients.state_dir(), "kill")


def kill_store_path(run_id: str) -> str:
    """The one record file for `run_id`, named so no run id escapes the store."""
    name = os.path.basename(os.path.normpath(str(run_id or "")))
    if not name or name.startswith(".") or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("bad run id %r" % (run_id,))
    return os.path.join(kill_store_dir(), name + ".json")


def write_kill_record(run_id: str, record: dict) -> bool:
    """Write (or merge into) the runner's private record for one run.

    SB-A4 (D-103, the rest of item C) grew this from a group record into the run's
    whole decided-at-spawn identity: `run_id`, `mode` (review|write), `scope`,
    `dry_run`, `allow_mode_only`, `created_at`, plus the `pgid`/`start` group
    record and the RUNMODEL `writer` the run resolves at its end. It is the only
    place any of that is read from, because everything a killer or a dispatcher
    decides must come from a record the worker cannot rewrite through its own
    `job.json`.

    Merge semantics are deliberately asymmetric. The group is re-recorded by the
    runner in `run_job` and by the spawner for every client attempt it launches
    (`record_attempt_group`), so `pgid`/`start` are replaced whenever they are
    given. The decided-at-spawn fields are IMMUTABLE: a later write cannot
    re-decide the mode even by accident, and `created_at`/`run_id` are stamped on
    the record's first write only.
    """
    if os.name == "nt" or not record:
        return False
    try:
        path = kill_store_path(run_id)
        directory = kill_store_dir()
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o700)
        existing = read_kill_record(run_id) or {}
        merged = dict(existing)
        merged.setdefault("run_id", run_id)
        merged.setdefault("created_at", _iso_zulu(datetime.datetime.now(
            datetime.timezone.utc)))
        for key in ("pgid", "start", "writer", "attempt"):
            if record.get(key) is not None:
                merged[key] = record[key]
        for key in ("mode", "scope", "dry_run", "allow_mode_only"):
            if record.get(key) is not None:
                merged.setdefault(key, record[key])
        tmp = "%s.tmp-%d" % (path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with io.open(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        print("autoos-agent: no kill record for %s: %s" % (run_id, exc),
              file=sys.stderr)
        return False
    return True


def read_kill_record(run_id: str):
    """The private record, or None. A run with no record is a run whose group and
    mode were never decided by a server — the direct CLI path — and a killer must
    say so rather than kill what it cannot identify."""
    try:
        path = kill_store_path(run_id)
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def record_attempt_group(run_id, proc=None, attempt=None) -> bool:
    """Re-record the live process group of one run, after each client launch.

    SB-B merge: the fallthrough loop re-starts the SAME task on another leg, and
    every attempt leads a NEW session (`run_client` starts it with
    `start_new_session=True`), so the `pgid`/`start` the runner wrote for its own
    group is not the group the current attempt lives in. Where there is no user
    manager the group kill IS the cancel, and a stale group is a cancel that
    reports success while the worker keeps writing.

    It updates only an EXISTING record, because a record is minted by the fleet
    server at spawn: a `run` started straight from a shell has no run dir, no
    `mode`, and no canceller, and creating a record here would hand the store a
    file nothing else describes. Never job.json — the worker owns that file's
    directory and SB-A4 is explicit that a kill target read out of it is a channel
    the cancelled process writes through.
    """
    if os.name == "nt" or not run_id or read_kill_record(run_id) is None:
        return False
    group = group_record(getattr(proc, "pid", None))
    if not group:
        return False
    if attempt is not None:
        group["attempt"] = attempt
    return write_kill_record(run_id, group)


def group_record(pid=None) -> dict:
    """`{"pgid", "start"}` for the group `pid` leads — the runner's own record.

    Written to the runner-private kill store (SB-A4: never job.json, which the
    worker's directory holds), which is what `cancel` verifies a kill against.
    """
    if os.name == "nt":
        return {}
    try:
        pgid = os.getpgid(int(pid) if pid else 0)
    except OSError:
        return {}
    return {"pgid": pgid, "start": proc_start_time(pgid)}


def kill_groups(pgids, grace=5.0) -> list:
    """SIGTERM every named process group, then SIGKILL whatever survives `grace`.

    SB-A (D-103) item 1 (CANCELORPHAN): a worker's client is started in its own
    session, so it leads a group the spawner's runner is NOT in, and a child that
    outlives it is reparented to systemd --user — but reparenting never changes a
    pgid, so a group kill still reaches it. Sending the signal to each group
    first and polling them together is what keeps one 5 s window instead of one
    per group.

    SB-A2 (D-103) item B: two groups are never signalled, and this is the last
    gate — the guard lives here, not only at the call site, because the caller
    can be wrong. `pgid <= 1` is init and the kernel thread groups. The caller's
    OWN group is the process answering `cancel`: the MCP server, which would
    otherwise SIGTERM itself along with every other run it hosts. Returns the
    refused `[(pgid, reason)]` so the caller can report what it did not kill.
    """
    refused = []
    if os.name == "nt":
        return refused
    own = os.getpgrp()
    live = []
    for pgid in pgids:
        try:
            pgid = int(pgid)
        except (TypeError, ValueError):
            continue
        if pgid <= 1:
            refused.append((pgid, "pgid <= 1 is init or a kernel thread group"))
            continue
        if pgid == own:
            refused.append((pgid, "the canceller's own process group"))
            continue
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
        live.append(pgid)
    deadline = time.time() + grace
    while live and time.time() < deadline:
        time.sleep(0.05)
        still = []
        for pgid in live:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                continue
            still.append(pgid)
        live = still
    for pgid in live:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return refused


def kill_verified_groups(groups, grace=5.0) -> list:
    """Kill only the groups whose leader is provably the process that was recorded.

    `groups` is what the runner wrote in job.json: `[{"pgid": int, "start": int}]`.
    One report per entry: `{"pgid", "action": "killed"|"skipped", "reason"}`.
    Without a recorded start time, or with a leader whose start time differs from
    it, NOTHING is signalled: the group is gone or recycled, and a recycled pgid
    belongs to a process this run has no claim on.

    RESIDUAL, stated rather than papered over: this is a *process-group* kill, so
    a worker that calls `setsid()`/`setpgid()` moves itself out of reach, and so
    does one reparented out of a group the runner never recorded. Nothing in the
    fallback catches it — the mechanism that does is the systemd scope (item A),
    which is a cgroup and cannot be escaped by changing a process group. `cancel`
    uses the scope whenever `scope_supported()` says a user manager is reachable,
    and this function only when it is not.
    """
    reports, to_kill = [], []
    for group in groups or []:
        try:
            pgid = int(group.get("pgid"))
        except (AttributeError, TypeError, ValueError):
            continue
        start = group.get("start")
        try:
            start = int(start)
        except (TypeError, ValueError):
            reports.append({"pgid": pgid, "action": "skipped",
                            "reason": "no recorded leader start time: the group "
                                      "cannot be told from a recycled pgid"})
            continue
        now = proc_start_time(pgid)
        if now is None:
            reports.append({"pgid": pgid, "action": "skipped",
                            "reason": "the group leader is gone: nothing to kill"})
            continue
        if now != start:
            reports.append({"pgid": pgid, "action": "skipped",
                            "reason": "the recorded pgid was recycled: leader start "
                                      "time %d is not the recorded %d" % (now, start)})
            continue
        to_kill.append(pgid)
        reports.append({"pgid": pgid, "action": "killed",
                        "reason": "leader start time matches the record"})
    for pgid, reason in kill_groups(to_kill, grace=grace):
        for report in reports:
            if report["pgid"] == pgid and report["action"] == "killed":
                report["action"], report["reason"] = "skipped", reason
    return reports


def kill_group(pgid, grace=5.0) -> None:
    """Stop one process group: SIGTERM, then SIGKILL past the grace."""
    kill_groups([pgid], grace=grace)


def _terminate_group(proc, pgid) -> None:
    """Stop whatever is left of the client's process group (best effort)."""
    if os.name == "nt":
        subprocess.call(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    kill_group(pgid)


def scope_unit_name(run_id) -> str:
    """The transient scope unit a worker run is launched in (SB-A2 item A).

    Only `[A-Za-z0-9_-]` survives — a unit name is systemd syntax, and the run id
    is a string this function did not choose. The `.scope` suffix is part of the
    name so `systemctl --user` addresses the unit it created and not a service of
    the same stem.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(run_id or "run"))[:120]
    return "%s%s.scope" % (WORKER_SCOPE_PREFIX, safe)


# SB-A3 (D-103) item C: the shape of the id a KILL may be derived from. The
# spawner mints `<15-char UTC stamp>-<slug of at most RUN_ID_SLUG_CAP>-<6 hex>`
# (`mint_run_id`), 47 characters at most, which this admits. It is looser than
# RUN_ID_RE on purpose: RUN_ID_RE decides what may be written into a branch name,
# this decides what may be signalled, and an id minted before the mint lived in
# one place still has to be cancellable. The cap is 47 with room, so a padded
# slug cannot stretch a unit name.
KILL_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9-]+$")
KILL_RUN_ID_MAX = 64


def is_killable_run_id(run_id) -> bool:
    """True for a string that may name a kill: a run id, nothing else."""
    return (isinstance(run_id, str) and len(run_id) <= KILL_RUN_ID_MAX
            and bool(KILL_RUN_ID_RE.match(run_id)))


def worker_scope_unit(run_id) -> str:
    """The scope unit run_id's worker is in — derived, never read from a record.

    SB-A3 (D-103) item C: SB-A2 wrote the unit name into `job.json` and had
    `cancel` stop whatever it found there. `job.json` sits in the run dir, which
    the runner exports to the worker as AUTOOS_TASK_DIR, and the worker is the
    same uid as the file — so the process being cancelled owned the pen that
    named what the killer signalled, and any `--user` unit or another run's scope
    would do. The name is a pure function of the run id instead; anything that is
    not a run id is refused before it can become unit syntax.
    """
    if not is_killable_run_id(run_id):
        raise ValueError("refused to derive a scope unit from %r: not a run id "
                         "(want %s, at most %d chars)"
                         % (run_id, KILL_RUN_ID_RE.pattern, KILL_RUN_ID_MAX))
    return "%s%s.scope" % (WORKER_SCOPE_PREFIX, run_id)


def scope_unit_is_worker_scope(unit) -> bool:
    """Is `unit` shaped like a scope this spawner could have created?

    The second gate, for a caller that built the name itself: prefix, suffix, and
    nothing but unit-safe characters. `foo.service` and `multi-user.target` are
    not this spawner's; a name with a `;`, a space or a `/` in it is not systemd
    syntax this process should be handing to `systemctl`.
    """
    return (isinstance(unit, str) and 0 < len(unit) <= 141
            and unit.startswith(WORKER_SCOPE_PREFIX) and unit.endswith(".scope")
            and bool(re.fullmatch(r"[A-Za-z0-9_.-]+", unit)))


def self_cgroup() -> str:
    """This process's own `/proc/self/cgroup`, or "" where it cannot be read.

    Separate from `in_worker_scope()` so a test can hand it a synthetic cgroup —
    the suite itself runs inside a worker scope in a lane sandbox, and a process
    cannot leave the cgroup it was started in.
    """
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# The one shape of "this spawner's scope, wherever in the cgroup tree it sits": a
# path SEGMENT that starts with the worker prefix and ends `.scope`. A delegated
# sub-cgroup (`…/autoos-worker-x.scope/1000`) is still inside it, so the search
# finds the segment rather than anchoring at the end of the line.
WORKER_SCOPE_UNIT_RE = re.compile(
    r"(?:^|/)(" + re.escape(WORKER_SCOPE_PREFIX) + r"[^/\s]+\.scope)")


def worker_scope_unit_from_cgroup(text) -> str | None:
    """The `autoos-worker-*.scope` unit holding the cgroup `text` names, or None.

    SCOPECLI-b (c): `0::/init.scope` — the shape a WSL session and a plain
    terminal both show — is NOT a worker scope and must not be read as one, or the
    second gate below would refuse to scope a run that never had a cgroup.
    """
    found = WORKER_SCOPE_UNIT_RE.search(text or "")
    return found.group(1) if found else None


def in_worker_scope() -> bool:
    """Does this process already run inside an `autoos-worker-*.scope` cgroup?

    SCOPECLI's second gate. `systemd-run --user --scope` does not nest: the new
    scope lands as a SIBLING under `app.slice` (measured on the host), so a client
    scoped from inside a worker scope would LEAVE that cgroup — and MCP `cancel`
    stops the outer scope by name, so it would no longer reach the client. A run
    started straight from a shell is outside every scope and is the only one that
    needs a scope of its own.
    """
    return worker_scope_unit_from_cgroup(self_cgroup()) is not None


def worker_scope_argv(unit, cmd) -> list:
    """`systemd-run --user --scope` around `cmd`: the whole subtree joins the cgroup."""
    return (["systemd-run", "--user", "--scope",
             "--unit", unit[:-len(".scope")] if unit.endswith(".scope") else unit,
             # the task text is in cmd; systemd >= 256 would expand its $VARs
             # (older ones fail this flag, the probe sees it, workers fall back)
             "--collect", "--expand-environment=no", "--"] + list(cmd))


# What `systemd-run --user` needs to reach the user manager, and what the worker
# must not keep: worker_env drops both, so the launcher gets them back and `env -u`
# takes them away again inside the scope (SCOPEBUS).
SCOPE_BUS_ENV = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")


def worker_scope_launch(unit, cmd, child_env: dict) -> tuple:
    """(argv, env) that start `cmd` in the worker scope from a scrubbed env.

    `child_env` is the scrubbed env `cmd` is meant to run with. systemd-run gets
    the caller's bus address on top of it, and the command inside the scope gets
    exactly `child_env` again. Without the first half every launch fails with
    "Failed to connect to bus: No medium found" (f51fc25).
    """
    # the bus address goes to systemd-run only; `env -u` removes it before the
    # worker command starts
    env = dict(child_env, **scope_bus_env())
    # absolute, from the caller's PATH: the scrubbed PATH must not pick the binary
    strip = [shutil.which("env") or "/usr/bin/env"]
    for name in SCOPE_BUS_ENV:
        strip += ["-u", name]
    return worker_scope_argv(unit, strip + list(cmd)), env


def scope_bus_env(src: dict | None = None) -> dict:
    """The set SCOPE_BUS_ENV names from `src` (default: this process's env)."""
    src = os.environ if src is None else src
    return {n: src[n] for n in SCOPE_BUS_ENV if src.get(n)}


_SCOPE_UNSUPPORTED: str | None = None

# What a `unscoped` record says when the gate was decided somewhere a reason was
# never computed for — an empty reason beside "UNSCOPED" is the silence this
# whole mechanism exists to end.
SCOPE_REASON_UNSUPPORTED = "systemd-run --user is not usable here"
SCOPE_REASON_WINDOWS = "windows"

# SCOPECLI-b (b): the fallback is allowed on POSIX, but never quiet. A host with
# no user manager kills a run through its process group, which a `setsid()` child
# escapes, so whoever meant to be able to cancel this run has to find out now.
SCOPE_WARNING = ("autoos-agent: WARNING client runs UNSCOPED (%s): "
                 "cancel falls back to the process-group kill")


def scope_unsupported_reason(force: bool = False) -> str:
    """Why this host cannot LAUNCH a `systemd-run --user --scope`; "" when it can.

    SCOPECLI-b (a): the same fact `scope_supported()` answers, in the words the
    record and the log carry. One probe, one cache, one home: the bool is this
    function's projection, so a record can never claim a reason the gate did not
    hit. Cached — the probe starts a real scope — and `force=True` re-probes,
    because the unit tests change PATH under it.
    """
    global _SCOPE_UNSUPPORTED
    if _SCOPE_UNSUPPORTED is None or force:
        _SCOPE_UNSUPPORTED = _probe_scope()
    return _SCOPE_UNSUPPORTED


def scope_supported(force: bool = False) -> bool:
    """Can this host launch (not merely contain) a `systemd-run --user --scope`?

    A container can ship both binaries and have no user manager, and a worker that
    never started is worse than a worker in the fallback, so the answer comes from
    launching the real thing to a no-op command.
    """
    return not scope_unsupported_reason(force)


def _probe_scope() -> str:
    """Launch a real scope around a no-op and say why it cannot work here ("" = ok).

    The site below is audited as an exception on purpose: `systemd-run` reaches
    the user manager over the session bus, and the bus address
    (``DBUS_SESSION_BUS_ADDRESS``, ``XDG_RUNTIME_DIR``) is exactly what
    ``worker_env`` strips for a client. A probe that scrubbed them would report
    "no scope here" on a host that has one, and every worker would take the
    fallback whose kill cannot follow a `setsid()` child.

    The order is the order a reader can act on: no mechanism at all (Windows), a
    missing binary, no address to talk to, and only then a manager that is
    reachable in name but refuses the launch.
    """
    if os.name == "nt":
        return SCOPE_REASON_WINDOWS
    if not shutil.which("systemd-run"):
        return "no systemd-run"
    if not shutil.which("systemctl"):
        return "no systemctl"
    if not os.environ.get("XDG_RUNTIME_DIR"):
        return "no user manager / XDG_RUNTIME_DIR"
    argv = worker_scope_argv(scope_unit_name("probe-%d" % os.getpid()),
                             [sys.executable, "-c", "pass"])
    try:
        # the probe keeps the caller's session-bus address, which the worker scrub
        # drops on purpose; it runs `python -c pass` and nothing else. subprocess-audit: ok
        return "" if subprocess.call(argv, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=30) == 0 \
            else "user manager unreachable"
    except (OSError, subprocess.SubprocessError):
        return "user manager unreachable"


def scope_decision(run_id=None, attempt=None) -> dict:
    """Which launch path a client started here would take: the record's `scope`.

    `{"path": "scoped"|"inherited"|"unscoped", "unit": <unit or None>,
    "reason": <why>}` — one dict, decided once, read by `run_client` to choose the
    launch and by the worker registry record, the `ps` row and the run's `scope:`
    line to say what happened. `inherited` names the OUTER unit read out of this
    process's own cgroup, which is what a canceller stops; `run_id`/`attempt` only
    name the unit a `scoped` launch creates, so a fallthrough re-run's fresh id
    never overstates the cgroup that actually holds it.

    Runner-private by rule (R-orch-17): this goes in the worker registry record and
    the spawner's own output, never into `job.json`, the file the worker shares its
    uid with.
    """
    if os.name == "nt":
        return {"path": "unscoped", "unit": None, "reason": SCOPE_REASON_WINDOWS}
    outer = worker_scope_unit_from_cgroup(self_cgroup())
    if outer:
        return {"path": "inherited", "unit": outer, "reason": "already inside " + outer}
    if not scope_supported():
        return {"path": "unscoped", "unit": None,
                "reason": scope_unsupported_reason() or SCOPE_REASON_UNSUPPORTED}
    return {"path": "scoped", "unit": cli_scope_unit(run_id, attempt), "reason": ""}


def scope_line(scope: dict) -> str:
    """The one line a reader of the run sees beside `writer:`: which path it took.

    A `scoped`/`inherited` run states the unit — the name `systemctl --user stop`
    takes; an `unscoped` one states the reason, because the reason is the whole
    warning.
    """
    unit = scope.get("unit")
    if unit:
        return "scope: %s unit=%s" % (scope.get("path"), unit)
    return "scope: %s (%s)" % (scope.get("path") or "unscoped",
                               scope.get("reason") or SCOPE_REASON_UNSUPPORTED)


def _systemctl(*args):
    """`systemctl --user ...`, or None when the manager cannot be reached at all.

    Audited exception, same reason as `_probe_scope`: signalling a unit is the
    spawner's own plumbing, spoken to the caller's user manager over the caller's
    session bus. It carries no credential onward and runs no worker code.
    """
    try:
        # subprocess-audit: session-bus plumbing, not a child that forwards a token
        return subprocess.run(["systemctl", "--user", *args], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None


def stop_scope(unit, grace: float = 5.0) -> dict:
    """Kill every process inside the worker's transient scope, then release the unit.

    `--kill-whom=all` signals the whole cgroup, so a member that `setsid()`ed out
    of its process group is still reached — the escape the group-kill fallback
    cannot cover. SIGTERM, then SIGKILL past the grace, then `stop` so the
    `--collect` unit goes with it. A unit that is unknown (already exited, or a
    run from before this mechanism) is reported, not raised: cancelling a run that
    has ended is a no-op, never a failure.

    A name that is not an `autoos-worker-*.scope` unit is refused with nothing
    signalled and no `systemctl` call made: this is the gate, not the caller.
    """
    report = {"unit": unit, "stopped": False, "reason": ""}
    if not unit:
        report["reason"] = "no scope unit recorded for this run"
        return report
    # SB-A3 (D-103) item C, the second gate: `cancel` derives the name, but a
    # killer should not signal a unit because a caller said so. Anything that is
    # not this spawner's own worker scope is refused here, where the caller cannot
    # talk it past it.
    if not scope_unit_is_worker_scope(unit):
        report["refused"] = True
        report["reason"] = ("refused: %r is not an %s*.scope unit name"
                            % (unit, WORKER_SCOPE_PREFIX))
        return report
    if _systemctl("status", unit) is None:
        report["reason"] = "systemctl --user is not reachable"
        return report
    out = _systemctl("show", "-p", "ActiveState", "--value", unit)
    state = out.stdout.strip() if out else ""
    if state != "active":
        # SB-A4 item 3: `stopped` here means "there is nothing running in it",
        # which the canceller did not do. `was_active` is what tells an honest
        # caller that no kill was delivered by this call.
        report.update({"stopped": True, "was_active": False,
                       "reason": "the scope is already %s" % (state or "unknown")})
        return report
    for sig in ("SIGTERM", "SIGKILL"):
        _systemctl("kill", "--kill-whom=all", "--signal=%s" % sig, unit)
        deadline = time.time() + (grace if sig == "SIGTERM" else 5.0)
        while time.time() < deadline:
            out = _systemctl("show", "-p", "ActiveState", "--value", unit)
            if not out or out.stdout.strip() != "active":
                break
            time.sleep(0.1)
    out = _systemctl("show", "-p", "ActiveState", "--value", unit)
    state = out.stdout.strip() if out else ""
    _systemctl("stop", unit)
    report["was_active"] = True
    report["stopped"] = state != "active"
    report["reason"] = ("the scope is %s" % (state or "gone")) if report["stopped"] \
        else "the scope is still active after SIGKILL to every process in it"
    return report


def _trim_tail(buf: bytearray, chunk: bytes) -> None:
    """Append one chunk of the child's output, keeping only the last TAIL_LIMIT
    bytes. The cut is bytewise, so it can split a multi-byte character; the
    decode at the other end uses `replace` and accepts one mojibake character at
    the front of the window."""
    buf.extend(chunk)
    if len(buf) > TAIL_LIMIT:
        del buf[:len(buf) - TAIL_LIMIT]


class ClientExit(int):
    """A client's exit code, carrying the tail run_client captured.

    An int subclass, so every existing caller still compares it to a plain exit
    code; `.tail` is the last TAIL_LIMIT bytes of the client's merged
    stdout+stderr, decoded (bug 2 needs it to spot a headless refusal) and
    redacted of any secret it carried (SPAWNREDACT item 2). `.raw_tail` is the
    same window of the child's OWN text: redaction can mask the very marker the
    refusal and provider-stop checks look for, so they classify on this copy and
    nothing else ever reads it - it is never printed, never recorded, never
    committed (REDACTFIX item 2).

    `.raw_err` is the narrower window of the two: the child's OWN text from its
    OWN stderr channel only (ERRCHANNEL, SB-B review 1). A task that quotes
    `Error: 429` at itself — in a log it cat'ed, in a fixture it wrote, in the
    brief it was handed — puts that line on stdout, and a rate limit read out of
    the task's own words benches a provider that never refused anyone and re-runs
    the whole run on another leg. The channel is the only thing that tells a
    client's failure from a worker quoting one, so the rate-limit family is
    classified on this copy alone. It is None (no split was made, so nothing may
    be demanded of a channel that was never read) and "" (the channel was read
    and said nothing) are different answers, and that difference is the rule.

    `.scope` is the launch path this client actually took, as `scope_decision`
    framed it (SCOPECLI-b): who ever cancels this run — or reads `ps` the morning
    after — learns from it whether a cgroup holds the subtree or only the
    process-group kill does. None for an exit object this spawner did not produce
    (a test's own stub, a run that never launched).
    """
    tail = ""
    refusal = None
    raw_tail = ""
    raw_err = None
    scope = None

    def __new__(cls, rc: int, tail: str = "", refusal: str | None = None,
                raw_tail: str = "", raw_err: str | None = None,
                scope: dict | None = None):
        obj = super().__new__(cls, rc)
        obj.tail = tail
        obj.refusal = refusal
        obj.raw_tail = raw_tail
        obj.raw_err = raw_err
        obj.scope = scope
        return obj


SCOPE_WRAPPER = "systemd-run"


class ClientMissing(Exception):
    """A planned launch's program resolves to nothing on PATH (WINSHIM).

    Raised by `resolve_client_executable` instead of letting subprocess answer a
    bare name with a FileNotFoundError traceback.
    """


def client_program_index(cmd) -> int:
    """Which element of `cmd` names the program this argv actually starts.

    0 for a client launch. A worker started inside its transient scope is the one
    shape where the client is not argv[0]: `systemd-run` is the spawner's own
    plumbing, started by name, and the program it launches hides behind the
    wrapper's `--` (see `worker_scope_argv`). Resolving the wrapper rather than
    what is after it would break the POSIX cancel channel.
    """
    argv = list(cmd)
    if argv and os.path.basename(argv[0]) == SCOPE_WRAPPER and "--" in argv:
        return argv.index("--") + 1
    return 0


def resolve_client_executable(cmd) -> list:
    """`cmd` with the client's program replaced by what `shutil.which()` found.

    WINSHIM (reported by Workstation-AutoOS): on Windows a client installed as a
    `.cmd`/`.ps1` shim is on PATH — so the spawner's own which() pre-check calls it
    installed — while `CreateProcess` appends only `.exe` and Popen of the bare
    name raised FileNotFoundError [WinError 2]. which() honours PATHEXT, so it
    finds the shim and Popen is handed its full path. `shell=True` is not the
    answer: it would put argv quoting and an injection surface behind every spawn.
    On POSIX which() returns the file execvp would have picked, so a client that
    works today is untouched. Every launch site goes through here — `run_client`
    (the first attempt and each fallthrough re-run) and the MCP runner's detached
    `run` — so one rule decides what starts, and a program that is not there is
    named, with the PATH that was searched, instead of traced back.
    """
    argv = list(cmd)
    index = client_program_index(argv)
    name = argv[index]
    exe = shutil.which(name)
    if exe is None:
        raise ClientMissing(
            "not installed: %s (PATH %s). Install it (catalog: ./setup.sh --only "
            "<id> -y; on Windows the directory holding the .cmd/.ps1 shim has to "
            "be on PATH); see: list" % (name, os.environ.get("PATH", "")))
    argv[index] = exe
    return argv


def cli_scope_unit(run_id, attempt) -> str:
    """The transient scope unit a `run` started from a shell launches its client in.

    SCOPECLI: a shell run has no runner-private record, so it needs a unit name
    nothing else owns. `cli-` keeps it clear of the runner's own
    `autoos-worker-<run-id>.scope`, and the attempt number matters because the
    fallthrough loop re-starts the client in a new scope each time — a reused unit
    name is a `systemd-run` failure, not a no-op. A run with no id (a plain
    `run …` from a terminal) is named by its own pid, which is unique per process.
    """
    return scope_unit_name("cli-%s-a%s" % (run_id or "pid%d" % os.getpid(),
                                           attempt or 1))


def run_client(cmd, cwd: str, env: dict, reap: bool = True, capture: bool = False,
               run_id=None, attempt=None) -> int:
    """Run one client in its own process group; reap whatever it leaves behind.

    The client leads a session of its own on purpose — but that is exactly the
    group a canceller cannot reach from the runner, so `cancel` stops the systemd
    SCOPE this process was launched inside (SB-A2 item A), which is a cgroup and
    holds a setsid() child anyway. Nothing here writes a file for `cancel` to
    read: the worker shares the uid of every path it could be given.

    SCOPECLI: where this process is NOT already in such a scope — a `run` started
    straight from a shell — there was no cgroup at all, and one is made here,
    around the client only. `scope_decision()` weighs both gates: a user manager
    must be reachable (`scope_supported()`, else the launch would fail and the run
    with it) and nothing outer may already hold this subtree
    (`in_worker_scope()`), because a nested scope is a sibling, not a child, and
    would move the client out of the scope the MCP `cancel` stops. One line on our
    stderr names the unit and the command that stops it. Windows and a host
    without a user manager are exactly the code that ran before.

    SCOPECLI-b: which of those three paths was taken is `scope_decision()`'s dict,
    decided before the launch and returned on `ClientExit.scope` — the caller
    writes it to the worker registry record and the run's `scope:` line, so
    neither `ps` nor the log can show a run whose kill channel is a guess. A
    POSIX run that ends up UNSCOPED says so out loud on our stderr first: the
    group kill that is all a canceller has there cannot follow a `setsid()` child.

    `run_id`/`attempt` (SB-B merge): the client's NEW group is re-recorded in the
    runner-private kill store on every launch, so a `cancel` that falls back to the
    group kill where there is no user manager is aimed at the attempt that is
    running now, not at the one that rate-limited and died — the fallthrough loop
    re-starts this task, and each re-start is a new session with a new pgid. The
    store is updated, never job.json (the worker owns that file's directory), and
    nothing is written at all for a run with no record: a `run` started straight
    from a shell has no canceller.

    capture=True (CAPTURE_CLIENTS only): on POSIX the child's stdout and stderr are
    two pipes, both streamed to our stdout line by line (unbuffered) and both kept
    in one merged window; on Windows they are one merged pipe. The last TAIL_LIMIT
    bytes are kept on ClientExit.tail, the first headless-refusal line seen
    anywhere in the stream on ClientExit.refusal, and the stderr window alone on
    ClientExit.raw_err (None where no split was made). capture=False: the child
    inherits our stdout/stderr, as before. Returns the client's exit code; KeyboardInterrupt is
    re-raised after cleanup. reap=False (a --joinable `claude --bg` session)
    leaves the group alone after exit code 0: that session is meant to outlive
    this spawner. A failed start is reaped.

    SPAWNREDACT item 2: every byte of that stream is redacted before it reaches
    the caller's terminal, our log or ClientExit.tail -- but redaction is not
    free for the checks: a provider-stop marker can BE the secret (an
    ``error: retry_key = 429`` line), and then the redacted copy can no longer
    be classified. So the pump keeps the same window twice: ClientExit.raw_tail
    holds the child's own text and is read by nothing but the refusal and
    provider-stop checks (REDACTFIX item 2); it is never printed, recorded or
    committed.
    """
    # stdin closed: when it is an open pipe (cron, CI, an agent's shell)
    # `opencode run` waits to read it as extra prompt text and never starts
    # (measured 2026-09-24: 150 s hang vs 6 s with /dev/null).
    # leftovers (private Serena, language servers) survived a cancelled worker, measured 2026-09-25.
    register_secret_env(env)
    # WINSHIM: Popen starts the file which() resolved, never the bare name.
    cmd = resolve_client_executable(cmd)
    pipe, merge = (subprocess.PIPE, subprocess.STDOUT) if capture else (None, None)
    # ERRCHANNEL: on POSIX the two channels stay two pipes, so the rate-limit
    # family can be classified on stderr alone. Their bytes still land in one
    # merged window and one terminal stream, exactly as the single merged pipe
    # produced, so nothing that reads `.tail`/`.raw_tail` today sees a different
    # shape. Windows keeps the merged pipe: no kill store exists there, and a
    # second pipe-reader is not worth the platform risk for a classification that
    # only the POSIX fallthrough acts on.
    separate_err = capture and os.name != "nt"
    # SCOPECLI-b: ONE decision, made here, serves the launch below and the record
    # and the log line the caller prints — a run that said "scoped" while it
    # launched bare is the exact defect this task closes.
    scope = scope_decision(run_id, attempt)
    if os.name == "nt":
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=pipe, stderr=merge,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        pgid = None  # Windows reaps with `taskkill /T`, which walks the tree
    else:
        argv, launch_env = cmd, env
        if scope["path"] == "scoped":
            # SCOPECLI: a `run` started from a shell is the one launch nobody
            # scoped — the runner wraps the whole CLI only on the MCP path, so this
            # client led a bare session whose subtree no `systemctl --user stop`
            # could reach. `systemd-run --scope` keeps the pid, the group and both
            # pipes (measured), so the reap, the wait and the capture below are
            # unchanged; only the cgroup is new.
            unit = scope["unit"]
            print("autoos-agent: client runs in scope %s "
                  "(stop: systemctl --user stop %s)" % (unit, unit), file=sys.stderr)
            argv, launch_env = worker_scope_launch(unit, cmd, env)
        elif scope["path"] == "unscoped":
            # SCOPECLI-b (b): the fallback is legal, the silence was not — say what
            # is missing and what cancel is therefore reduced to.
            print(SCOPE_WARNING % scope["reason"], file=sys.stderr)
        proc = subprocess.Popen(argv, cwd=cwd, env=launch_env, stdin=subprocess.DEVNULL,
                                stdout=pipe, stderr=pipe if separate_err else merge,
                                start_new_session=True)
        pgid = proc.pid  # start_new_session makes the client its own group leader
    record_attempt_group(run_id, proc, attempt)
    tail = bytearray()
    raw_tail = bytearray()
    raw_err = bytearray()
    found = []
    write_lock = threading.Lock()

    def pump(stream, is_err):
        try:
            for raw in getattr(proc, stream):
                text = raw.decode("utf-8", "replace")
                if not found:
                    hit = headless_refusal(text)  # classified on the child's own text
                    if hit:
                        found.append(redact_output(hit))
                # REDACTFIX item 2: the classification windows are kept twice,
                # the raw one for the checks below and the redacted one for
                # every copy that leaves this process.
                _trim_tail(raw_tail, text.encode("utf-8", "surrogateescape"))
                if is_err:
                    _trim_tail(raw_err, text.encode("utf-8", "surrogateescape"))
                text = redact_output(text)
                # Whole lines under one lock, so the merged terminal stream keeps
                # its line granularity now that two channels feed it.
                with write_lock:
                    try:
                        sys.stdout.write(text)
                        sys.stdout.flush()
                    except (OSError, ValueError):  # a closed/odd stdout must not kill the run
                        pass
                _trim_tail(tail, text.encode("utf-8", "surrogateescape"))
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=pump, args=("stdout", False), daemon=True)]
    if separate_err:
        readers.append(threading.Thread(target=pump, args=("stderr", True), daemon=True))

    def join_readers(timeout):
        for reader in readers:
            reader.join(timeout=timeout)

    if capture:
        for reader in readers:
            reader.start()
    try:
        rc = proc.wait()
    except BaseException:  # KeyboardInterrupt included: clean up, then re-raise
        _terminate_group(proc, pgid)
        if capture:
            join_readers(5)
        raise
    if reap or rc != 0:
        _terminate_group(proc, pgid)
        if capture:
            join_readers(5)
    elif capture:
        # A joinable session's background child may hold the pipe open; do not
        # block the spawner waiting on output that is not this run's anyway.
        join_readers(0.5)
    if capture and not any(reader.is_alive() for reader in readers):
        # a joinable session's background child owns the pipe
        for name in (["stdout", "stderr"] if separate_err else ["stdout"]):
            try:
                getattr(proc, name).close()
            except (OSError, ValueError):
                pass
    return ClientExit(rc, tail.decode("utf-8", "replace"),
                      found[0] if found else None,
                      raw_tail.decode("utf-8", "replace"),
                      raw_err.decode("utf-8", "replace") if separate_err else None,
                      scope=scope)


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


def _worker_record_start(plan: dict, args, directory: str, attempt=None):
    """Write the live record; return (id, record) for the ended rewrite.

    FLEETSPEC P0: the record's id IS the run id the plan minted (it used to be a
    second, unrelated id), so `ps`, the sandbox clone, its branch and the child's
    own AUTOOS_AGENT_RUN_ID name the same run. ``parent_run_id`` is the id this
    spawner was itself spawned with (None at top level): the spawn tree a console
    reads. ``host`` and the full ``route_plan`` are for the record only - the
    host is a git-ignored file, never a committed fixture.

    SCOPECLI-b: ``scope`` says which launch path this attempt takes — scoped, the
    outer unit it inherits, or unscoped with the reason — live, from the moment the
    record exists, so `ps` on a run in progress already tells an operator whether
    `cancel` reaches a cgroup or only a process group. The same function `run_client`
    launches from, so the record cannot disagree with the child.
    """
    wid = plan.get("run_id") or mint_run_id(args.title or None, args.task or "")
    pid = os.getpid()
    task = (args.task or "").splitlines()
    route = plan.get("route") or {}
    # FLEETP0 review LOW: the caller's own id is the parent, EXCEPT when it is
    # this run's id - a fallthrough re-run adopts the reused branch's id, and a
    # caller can hand --run-id down to the very run it spawned. A record parented
    # to itself is a cycle in the tree `ps --tree` prints.
    parent = os.environ.get("AUTOOS_AGENT_RUN_ID") or None
    if parent == wid:
        parent = None
    record = redact_record({
        "id": wid, "pid": pid, "pid_start": _proc_starttime(pid),
        "started": utc_now_iso(), "session_tag": plan.get("session_tag"),
        "client": plan.get("client"), "model": plan.get("model"),
        # FAMILYFENCE-b: the row names the model the run was ASKED to use and where
        # that ask came from, so a default never reads as a pin. What actually
        # answered is not here — it lives in the runner-private record, which is
        # the only writer store a worker cannot edit (R-orch-17).
        "model_source": plan.get("model_source"),
        "route": route.get("combo") or "",
        "title": args.title or "", "cwd": plan.get("cwd"),
        "sandbox": (plan.get("sandbox") or {}).get("path", ""),
        "task_head": (task[0] if task else "")[:120], "depth": plan["depth"][0],
        "parent_run_id": parent,
        "host": socket.gethostname(),
        "task_dir": os.environ.get("AUTOOS_TASK_DIR") or None,
        "scope": scope_decision(plan.get("run_id"), attempt),
        "route_plan": route.get("route_plan")})
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


def worker_writer(run_id):
    """The writer the RUNNER recorded for `run_id`, or None when nothing was.

    The runner-private kill store and nothing else (R-orch-17): a `ps` row is read
    by whoever is deciding whether a review is independent, and both the worker
    record and job.json live in directories the worker itself can write. A run with
    no record — one started by hand, one older than the store — answers None rather
    than falling back to the ask.
    """
    if not run_id:
        return None
    writer = (read_kill_record(run_id) or {}).get("writer")
    return writer if isinstance(writer, dict) else None


def list_workers(directory: str, now=None, include_ended: bool = False) -> list:
    """Every spawned worker in ``directory`` as rows, newest last.

    state is "running" (alive, start time matches - the pid-reuse guard),
    "died" (gone or a recycled pid) or "exited rc=N". Records with no ``ended``
    whose process cannot be checked are not judged here; the pid-reuse guard is
    skipped only when a live pid's start time is unreadable (a Windows process
    owned by another user), which still counts as running. A record whose ended
    time (or, for a died worker, whose started time) is older than 7 days is
    deleted. A corrupt record is skipped, never raised on.

    Each row carries both halves of the model story: `model`/`model_source` — what
    the run was ASKED to use and where that ask came from — and `writer`/`family`/
    `model_proven`, what the runner recorded as having ANSWERED (see
    `worker_writer`).
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
        writer = worker_writer(record.get("id"))
        family = (writer or {}).get("family") or ""
        if family == WRITER_UNRESOLVED:
            family = ""
        rows.append({"id": record.get("id"), "state": state,
                     "elapsed": _fmt_elapsed(secs), "elapsed_seconds": secs,
                     "client": record.get("client") or "", "model": record.get("model") or "",
                     "model_source": record.get("model_source") or "",
                     "lane": record.get("session_tag") or "", "pid": record.get("pid"),
                     # FAMILYFENCE-b: WHO ANSWERED, from the runner-private kill
                     # store — never from job.json or the worker record, both of
                     # which live where the worker could write (R-orch-17). `model`
                     # above stays the ask; a reader sees the ask and the answer
                     # side by side, and `model_proven` is the difference between
                     # the two claims: a gateway log row or the client's own
                     # transcript attested to this one, nothing attested to that.
                     "writer": writer,
                     "family": family,
                     "model_proven": writer_is_proven(writer),
                     "title": record.get("title") or "", "task": record.get("task_head") or "",
                     "cwd": record.get("cwd") or "", "sandbox": record.get("sandbox") or "",
                     "parent_run_id": record.get("parent_run_id") or None,
                     # SCOPECLI-b: null on a record written before the path was
                     # stated — `ps` says so rather than inventing one.
                     "scope": record.get("scope"),
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
    head = ["ID", "STATE", "ELAPSED", "CLIENT", "MODEL", "FAMILY", "LANE", "PID", "TITLE/TASK"]
    cells = []
    for r in rows:
        # FAMILYFENCE-b: MODEL is what ANSWERED when the run has a witness for it
        # and what was ASKED when it has none — the trailing "?" is the difference,
        # because "Efficient" from the plan and "Efficient" from a gateway log row
        # are not the same claim, and a reader of `ps` decides who to trust with
        # which. Same for FAMILY: a family nobody attested to gets a "?".
        proven = r.get("model_proven")
        writer = r.get("writer") or {}
        answered = writer.get("model") or ""
        model = answered if (proven and answered) else (r["model"] or "-")
        family = r.get("family") or "-"
        if not proven and (model != "-" or family != "-"):
            model = model + "?" if model != "-" else model
            family = family + "?" if family != "-" else family
        cells.append([r["id"], r["state"], r["elapsed"] or "-", r["client"], model,
                      family, r["lane"], str(r["pid"] or ""), r["title"] or r["task"] or ""])
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


def worker_tree(rows: list) -> list:
    """The ps rows as ``(depth, row)`` pairs in tree order (FLEETSPEC P0 item 2).

    A row hangs under its ``parent_run_id`` while that parent is still listed.
    A row whose parent record is gone (pruned, or spawned on another host) is a
    root: `ps --tree` says so on its line, because a silent top-level row reads
    as "nobody spawned this". A parent that is its own descendant would hang the
    walk, so an unvisited row is emitted as a root too - `ps` never loses one.
    """
    by_id = {r["id"]: r for r in rows if r.get("id")}
    children = {}
    for row in rows:
        parent = row.get("parent_run_id")
        if parent and parent in by_id and parent != row.get("id"):
            children.setdefault(parent, []).append(row)
    out, visited = [], set()

    def walk(row, depth):
        if row.get("id") in visited:
            return
        visited.add(row.get("id"))
        out.append((depth, row))
        for child in children.get(row.get("id"), []):
            walk(child, depth + 1)

    for row in rows:
        parent = row.get("parent_run_id")
        if not parent or parent not in by_id or parent == row.get("id"):
            walk(row, 0)
    for row in rows:  # a cycle: still listed, never swallowed by the walk
        walk(row, 0)
    return out


def _print_worker_tree(rows: list) -> None:
    """The same columns as `ps`, in tree order and indented by depth."""
    gone = {r.get("parent_run_id") for r in rows} - {r["id"] for r in rows if r.get("id")} - {None}
    shown = []
    for depth, row in worker_tree(rows):
        row = dict(row)
        row["id"] = "  " * depth + str(row.get("id") or "")
        parent = row.get("parent_run_id")
        if parent in gone:
            # the note comes first: the last column is the one the width budget
            # clips, and a clipped prose tail still says who the orphan is.
            # `ps --json` is the untruncated source of the edge.
            row["title"] = ("(parent %s gone) " % parent
                            + (row.get("title") or row.get("task") or "")).strip()
        shown.append(row)
    _print_worker_table(shown)


def cmd_ps(args) -> int:
    directory = workers_dir()
    rows = visible_workers(directory, include_ended=args.all)
    if args.json:
        print(json.dumps({"workers": rows, "dir": directory}, indent=1))
        return 0
    if not rows:
        print("no workers running")
        return 0
    if args.tree:
        _print_worker_tree(rows)
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
    # FLEETP0b: `--run-id` names a directory, a branch and a header, so a shape
    # that is not the canonical one is refused here - before any clone, record or
    # client start (and before the MCP server's own state dir is created for it).
    if getattr(args, "run_id", None) and not is_canonical_run_id(args.run_id):
        return refuse("--run-id %r is not a canonical run id "
                      "(YYYYMMDD-HHMMSS-<slug up to %d chars>-<6 hex>, as minted by "
                      "the spawner or the MCP server's spawn)"
                      % (args.run_id, RUN_ID_SLUG_CAP), 2)
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
    # CLAUDEBUDGET-b item 3(d): the client is a spend, so it is gated here --
    # ahead of build_plan, so a refused Claude spawn clones no sandbox, writes no
    # worker record and burns no route. CLAUDEBUDGET-d item 2: the gate reads the
    # model that answers (--model, --free-model, the client's own default, the
    # tier/card combo), because a non-Claude client name never was a claim that
    # the run is free.
    try:
        budget_refusal, budget_note = claude_spawn_refusal(
            client.name, os.environ, registry,
            # CLAUDEBUDGET-f item 3: the flags are passed as the flags, not
            # pre-OR'd into one string -- `args.model or args.free_model` hid the
            # tier from the resolution, which is a second path from the one
            # build_plan takes. One resolution, same inputs, same value.
            model=args.model, card=args.card, cfg=cfg, tier=args.tier,
            free=bool(args.free), free_model=args.free_model,
            clean=bool(args.clean))
    except OSError as exc:
        return refuse("cannot read the Claude budget: %s" % exc)
    except ValueError as exc:
        # CLAUDEBUDGET-g item B (finding 5): a card or tier the router itself
        # refuses is the routing error it always was, in the router's own words
        # with the router's own next step. Only the budget speaks as a budget.
        return refuse("%s (see: tools/autoos-agent.py list)" % exc)
    if budget_refusal is not None:
        return refuse(budget_refusal)
    if budget_note is not None:
        print("claude-budget: %s" % budget_note, file=sys.stderr)
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
    # FAMILYFENCE: who may NOT serve this run, settled before any leg is picked —
    # from here on the fence is read-only, and every leg choice reads it.
    # FAMILYFENCE-5 item 4: a blank --not-family value is a broken argument, refused
    # here (rc 2) before fence_family_names can drop it silently and let the run read
    # as fenced while nothing is.
    if any(not str(v).strip() for v in not_family_values(args)):
        return refuse(fence_blank_refusal(), 2)
    fence = family_fence(args)
    # `--no-fallthrough` is read once, here, so the pin cannot be seen differently
    # by the branch that announces it and the branch that re-plans. The default is
    # False, which is what the flag's absence means to argparse too.
    args.no_fallthrough = bool(getattr(args, "no_fallthrough", False))
    if getattr(args, "review_of", None) and not is_canonical_run_id(args.review_of):
        # The writer's family comes out of that run's record, so the id has to name
        # a run at all: a loose id here reads a stranger's record and fences off the
        # wrong family (the same rule `--run-id` applies).
        return refuse("--review-of %r is not a canonical run id "
                      "(YYYYMMDD-HHMMSS-<slug up to %d chars>-<6 hex>, as the spawner "
                      "mints one)" % (args.review_of, RUN_ID_SLUG_CAP), 2)
    # FAMILYFENCE-4: the id is a run, but this checkout's store holds no writer
    # family for it. Nothing can be fenced, so nothing runs — a warning here is what
    # sent a Qwen-authored review onto a combo with qwen legs in it.
    if fence.get("refusal"):
        return refuse(fence["refusal"], 2)
    # FAMILYFENCE-3 B2: a `--not-family` the registry carries no family under
    # ("mimo" while the model opencode/mimo-v2.6-flash-free is family `xiaomi`)
    # excluded nothing, and the run planned and launched reading as fenced while
    # it was not. Only the names THIS caller typed are checked — a writer family
    # the fence read out of a kill record is the registry's own answer already.
    # An unreadable registry or one that declares no family at all is not evidence
    # a name is wrong, so the check stands down rather than refuse every fence.
    typed_families = fence_family_names(getattr(args, "not_family", None))
    if typed_families:
        if registry is None:
            try:
                registry = load_registry(REGISTRY_PATH)
            except (OSError, ValueError):
                registry = None
        known_families = registry_family_names(registry)
        if known_families:
            unknown = [name for name in typed_families if name not in known_families]
            if unknown:
                return refuse(fence_name_refusal(unknown, known_families), 2)
    if fence["warn_no_writer"]:
        print(FAMILY_FENCE_NO_WRITER, file=sys.stderr)
    # SPAWNFREE (S2) item 1: the free-model chain, read BEFORE the plan because
    # FAMILYFENCE removes the fenced families from it and the fence has to bind
    # before the first model is chosen.
    free_policy = None
    free_chain = None
    if args.free:
        if registry is None:
            try:
                registry = load_registry(REGISTRY_PATH)
            except (OSError, ValueError):
                registry = None  # an unreadable registry leaves one free model
        free_policy = (registry or {}).get("policy") or {}
        free_chain = free_model_chain(free_policy, client.name, args.free_model)
        if fence["families"]:
            head = fence_free_head(free_chain, registry, fence)
            if head is None:
                return refuse(fence_refusal(fence), EXIT_NO_OTHER_FAMILY)
            if head != args.free_model:
                print("family fence: %s is inside the fence (%s), this run starts on %s"
                      % (args.free_model, ", ".join(fence["families"]), head),
                      file=sys.stderr)
                args.free_model = head
    try:
        plan = build_plan(args, cfg, fence=fence)
    except FamilyFenceRefused as exc:  # FAMILYFENCE: its own code, never exit 2
        return refuse(str(exc), EXIT_NO_OTHER_FAMILY)
    except clients.DepthError as exc:
        return refuse(str(exc), 4)
    except (RouteInputRequired, RouteDeferred, PrivacyRefused) as exc:  # plan's / PRIV3's own
        return refuse(str(exc))                        # message, no suffix added
    except ValueError as exc:  # CardError, NoRoute, an undeclared model
        return refuse("%s (see: tools/autoos-agent.py list)" % exc)
    route = plan["route"]
    # KEYDENY3b item 2 / KEYDENY3g: a spawned tier gets no option to work in the
    # caller's checkout. Read *after* build_plan because that is where a client
    # that always isolates (qoder writes) forces it on — the force is what keeps
    # that run legal. The verdict is computed here and returns before any client
    # starts; a --dry-run only announces it, because planning touches nothing and
    # an operator previews a route before deciding to run it. One call is the whole
    # rule: `leaf_isolation_refusal` carries the tier-1 write-role leg too (SB-B),
    # so there is a single gate to consult and a single gate to allow.
    leaf_refusal = leaf_isolation_refusal(route.get("tier"), bool(args.isolate),
                                          client.name,
                                          leaf=role_is_leaf(route.get("tier"),
                                                             route.get("card")),
                                          card=route.get("card"),
                                          read_only=bool(route.get("read_only")))
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
    # FF1 (D-106): the dry run names what the child actually gets, so an
    # operator can see the containment instead of trusting it. Item 1: that is
    # the *child's* env, not this process's, so a Claude declaration this
    # orchestrator holds never looks like it was passed on.
    env_names = sorted(worker_env(plan, "x" if uses_key else None))
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
              "verdict=<fill in>" % ((route.get("card") or {}).get("author"), reviewer["model"]))
        for line in resolver.reviewer_explain_lines(route["review_plan"]):
            print(line)
    if route.get("reviewer_note"):
        print(route["reviewer_note"])
    print("depth: %d/%d" % plan["depth"])
    print("run-id: %s" % plan["run_id"])
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
        # CLAUDEBUDGET-g item A: the last-mile gate, on the plan this process would
        # hand the client. The early gate proved the *flags*; a reviewer override,
        # the resolver's v2 route and --free have rewritten the model since, so the
        # value the run answers with only exists here. A dry run is checked too,
        # because a dry run is exactly what the MCP spawn path preflights with: an
        # override that turns the plan Claude has to be refused before a caller
        # reads "would run:" and hands the child its key.
        last_mile_refusal, last_mile_note = claude_plan_refusal(
            args, plan, cfg=cfg, registry=registry)
        if last_mile_refusal is not None:
            return refuse(last_mile_refusal)
        if last_mile_note is not None:
            print("claude-budget: %s" % last_mile_note, file=sys.stderr)
        if plan["sandbox"]:
            print("would run: git clone --local %s %s && git switch -c %s" % (
                plan["sandbox"].get("source") or isolate_source(),
                plan["sandbox"]["path"], plan["sandbox"]["branch"]))
        print("would run: " + " ".join(shlex.quote(c) for c in plan["cmd"]))
        print("cwd: %s" % plan["cwd"])
        print("env: %s" % (", ".join(env_names) or "-"))
        if leaf_refusal is not None:
            print("note: this run would be refused: %s" % leaf_refusal)
        return 0
    # KEYDENY3b: the leaf fence returns here, after the preview above and before
    # anything is cloned or started.
    if leaf_refusal is not None:
        return refuse(leaf_refusal)
    try:
        # WINSHIM: the pre-check asks the same question the launch site will, so a
        # client that is not there is refused before anything is cloned rather than
        # crashing on its bare name later. The plan keeps the bare name; `run_client`
        # resolves it again, at the one point Popen reads it.
        resolve_client_executable(plan["cmd"])
    except ClientMissing as exc:
        return refuse(str(exc), 3)
    ok, reason = clients.signin_state(client)
    if ok is False:
        what = "installed but not signed in" if clients.signed_out(reason) else "installed but not usable"
        return refuse("%s is %s: %s. Run `%s` once interactively to sign in (own account, "
                      "not the gateway); see: list" % (client.binary, what, reason.rstrip("."), client.binary), 3)
    # PWD too, not just cwd=: opencode takes the project directory from $PWD,
    # so an inherited PWD sent an isolated worker's writes to the caller's
    # checkout (live 2026-09-24).
    # FF1 (D-106): the rest of what the worker gets is chosen, not inherited —
    # see worker_env, which also strips this process's Claude declaration
    # (CLAUDEBUDGET item 1) on the way out. The key is minted first so the scrub
    # can add it.
    key = None
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
    env = worker_env(plan, key)
    # The worker's private XDG_RUNTIME_DIR is real from here on (FF1b item 4):
    # this is the first point past the dry run, where a directory is allowed.
    # FF1c item 1: the private config home is named in the plan and created
    # here, under the same rule — a dry run writes nothing.
    if not provision_worker_dirs(env):
        # FF1d: a dir the provisioner refused is not a dir to launch into.
        return 2
    # SPAWNREDACT item 2: the key is in the child's env from here on, so a
    # worker echoing it back must be masked before anything of this run is
    # written -- the record, the log line and the caller's terminal all read
    # this same dict.
    register_secret_env(env)
    # SPAWNCAP (S2): the route ids a provider-stopped attempt has already
    # burned, so a fallthrough re-run never picks one of them again.
    excluded_routes = set()
    # SPAWNFREE (S2) item 1: and the free models it burned. A --free run's
    # model is not a route, so the resolver's fallthrough never reached it -
    # the first 'Rate limit exceeded' ended the run (inbox 2026-09-27T17:08:22Z
    # and 17:19:45Z, work/L1-routing/T1FREE.r2.out).
    excluded_free_models = set()
    # RATELIMITRETRY (SB-B, D-103 item 1): the providers that answered THIS run
    # with a 429, in the provider-state file's shape. In-process only — an outage
    # a run survived is not this spawner's authority to bench a provider for every
    # later routing read (that is record_reset_stop, and only for a window the
    # provider itself stated).
    provider_cooldown = {}
    live_registry = None
    # How many re-runs this run has already started (route or free model): the
    # one bound MAX_FALLTHROUGH is about.
    fallthroughs = 0
    # `free_policy` / `free_chain` were settled above, before the plan: FAMILYFENCE
    # trims the chain before the first model is picked, so a second copy of that
    # read here would be a fence applied too late to bind.
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
        # The parent is the repo the clone was forked from (the caller's cwd),
        # not the checkout this script happens to live in (KEYDENY3g item 7).
        source = sb.get("source") or isolate_source()
        parent_snap = parent_snapshot(source)
        os.makedirs(os.path.dirname(sb["path"]), exist_ok=True)
        isolate_clone(source, sb["path"], sb["branch"])
        sb["base"] = subprocess.run(["git", "-C", sb["path"], "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        # SB-A (D-103) item 2 (NOOPCOMMIT): the ref tips as the clone stands up.
        # A `--local` clone carries every branch of the parent, so the run's own
        # commits are only identifiable against this baseline, and the baseline
        # cannot be taken later. One `for-each-ref`; a review run is exempt from
        # the sandbox verdict, so it does not pay for it (like the reflog).
        if not plan["route"].get("review"):
            sb["refs"] = sandbox_ref_heads(sb["path"])
        # SPAWNFIX3c (S2) item 2: the reflog lengths as the clone stands up, so a
        # later read sees only what the run appended. Kept in the dict, which a
        # provider-stop fallthrough re-run inherits with the sandbox itself.
        # SPAWNFIX3d (S2) item 3: only a read-only run is judged on an untouched
        # sandbox, so only it pays for the snapshot - exactly like the diff stat
        # at the summary below (sandbox_reflog_writes is never called otherwise).
        if plan["route"].get("read_only"):
            sb["reflog"] = sandbox_reflog_snapshot(sb["path"], sb["branch"])
        print("sandbox: %s (branch %s)" % (sb["path"], sb["branch"]))
    start = time.time()
    # Every finished --isolate run is captured (tee'd to our stdout), so the
    # WIPfix provider-stop check has the child's tail. A --joinable launcher
    # (claude --bg) exits while its session lives: its tail is a partial
    # mid-flight score, so it is never captured and never stop-checked or
    # WIP-committed - exactly as before WIPfix. CAPTURE_CLIENTS keeps its own.
    capture = client.name in CAPTURE_CLIENTS or (bool(plan["sandbox"]) and not args.joinable)
    # SCOPECLI-b: the launch path the attempt that finished last took, for the
    # `scope:` line under the `writer:` one. It comes from the same
    # `scope_decision()` that chose the launch — taken from the worker record
    # before the client starts, so a run in progress already says it, and from the
    # exit object after, so what is printed is what ran.
    scope_rec = None
    while True:
        # CLAUDEBUDGET-g item A: the authority. Checked on every plan this run is
        # about to launch, here and not only at the dry-run branch above, because a
        # provider-stopped fallthrough re-plans *after* the early gate ran, and the
        # route it falls through to is a different model. A refusal stops the run
        # before the worker record, the clone and the child: 2 is the code a refused
        # card or route already returns.
        launch_refusal, launch_note = claude_plan_refusal(args, plan, cfg=cfg,
                                                         registry=registry)
        if launch_refusal is not None:
            print("autoos-agent: %s" % launch_refusal, file=sys.stderr)
            rc = 2
            break
        if launch_note is not None:
            print("claude-budget: %s" % launch_note, file=sys.stderr)
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
            worker_id, worker_rec = _worker_record_start(plan, args, workers,
                                                         attempt=fallthroughs + 1)
        except Exception as exc:  # noqa: BLE001
            print("autoos-agent: could not write worker record: %s" % exc, file=sys.stderr)
        scope_rec = (worker_rec or {}).get("scope") or scope_rec
        if worker_id is not None:
            # The record is the thing every count reads, so the placeholder this
            # run claimed the slot with has done its job (SPAWNFIX item 2). It is
            # kept when there is no record, so a run nobody can see still cannot
            # share the leg with one.
            free_reservation_release(free_reservation)
            free_reservation = None
        attempt_start = time.time()
        run_rc = None
        missing = None
        try:
            run_rc = run_client(plan["cmd"], plan["cwd"], env, reap=not args.joinable,
                                capture=capture,
                                # SB-B merge: every attempt — the first and each
                                # fallthrough re-run — leads a new session, so every
                                # attempt re-records its group in the runner-private
                                # store `cancel` kills from.
                                run_id=plan.get("run_id"), attempt=fallthroughs + 1)
            scope_rec = getattr(run_rc, "scope", None) or scope_rec
        except ClientMissing as exc:
            # WINSHIM: gone between the pre-check and this attempt (a fallthrough
            # re-run can be hours later, or the PATH moved). The spawner's own 3,
            # named in one line — not a FileNotFoundError traceback.
            missing = str(exc)
        finally:
            if worker_id is not None:
                try:
                    _worker_record_end(workers, worker_id, worker_rec, run_rc)
                except OSError as exc:
                    print("autoos-agent: could not update worker record %s: %s"
                          % (worker_id, exc), file=sys.stderr)
        if missing is not None:
            rc = refuse(missing, 3)
            break
        client_tail = getattr(run_rc, "tail", "") or ""
        # REDACTFIX item 2 (review-spfix S2): both checks classify on the
        # child's OWN text, because redaction can mask the very marker they look
        # for (`error: retry_key = 429` is a stop line and a secret carrier at
        # once). Only the copies that leave this process are redacted. A client
        # exit with no raw window falls back to the redacted tail, which is what
        # this checked before.
        check_tail = getattr(run_rc, "raw_tail", "") or client_tail
        rc, refusal = refusal_exit(int(run_rc), check_tail)
        if refusal is None and getattr(run_rc, "refusal", None):
            # the refusal line has since scrolled out of the window: the pump
            # caught it while streaming and kept a copy for exactly this.
            rc, refusal = refusal_exit(int(run_rc), run_rc.refusal)
        child_rc = rc  # the WIP message names the client's own rc, not a verdict override
        if refusal is not None:
            print("autoos-agent: HEADLESS-REFUSAL: %s" % redact_output(refusal), file=sys.stderr)
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
        # ERRCHANNEL (SB-B review 1): the client's own stderr window rides along,
        # so a rate limit has to come from the channel a client fails on rather
        # than from anywhere in the merged output — a worker that prints
        # `Error: 429` while cat'ing a log, quoting a fixture or reading its own
        # brief used to bench a provider that never refused it and re-run the task
        # elsewhere. A client with no split channel (a non-capture client, or a
        # test's own exit object) passes None and keeps the old merged scan.
        stop = provider_stop(check_tail, getattr(run_rc, "raw_err", None))
        rate_limited = stop is not None and rate_limit_stop(stop)
        if stop is not None:
            # REVROUTE (S2) item 3: when the stop line states its own reset,
            # that window becomes the provider's unavailable_until for every
            # later routing read - the next task goes elsewhere until then.
            live_registry = load_live_registry()
            recorded = record_reset_stop(stop, plan["route"].get("combo"),
                                         live_registry)
            if recorded:
                print("provider %s unavailable until %s" % recorded)
            if rate_limited:
                # RATELIMITRETRY (SB-B): an unstated 429 is the common case, and
                # the run is about to choose its own next leg, so bench the
                # provider that just refused it for the rest of THIS process.
                benched = rate_limit_bench(stop, plan, live_registry)
                if benched:
                    until = datetime.datetime.now(
                        datetime.timezone.utc) + datetime.timedelta(
                            seconds=RATELIMIT_COOLDOWN_SECONDS)
                    provider_cooldown[benched] = {
                        "unavailable_until": _iso_zulu(until),
                        "combo": plan["route"].get("combo"),
                        "reason": redact_output(stop),
                        "recorded_at": _iso_zulu(datetime.datetime.now(
                            datetime.timezone.utc))}
                    print("rate limit: provider %s benched for this run's next "
                          "leg (%s)" % (benched, _iso_zulu(until)))
        if stop is not None and (rc in (0, 3, 6) or (rate_limited and rc == 1)):
            # SB-B (RATELIMITRETRY): rc 1 joins the upgrade only for a rate limit,
            # which is the shape the field actually failed in (the client printed
            # its 429 and exited 1). Other rc-1 exits stay the client's own failure
            # class: a provider that refused service is exit 8, a client that
            # crashed is not.
            print("autoos-agent: PROVIDER-STOP: %s" % redact_output(stop), file=sys.stderr)
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
        fence_refused = None
        if stop is not None and args.no_fallthrough:
            # FAMILYFENCE item 3: a pinned run's stop IS its answer. Re-planning
            # onto another model and reporting the verdict as that model's would be
            # the same mislabelling the fence exists to stop, just with the
            # spawner's blessing — so the run ends on the stop's own rc.
            print("autoos-agent: --no-fallthrough: the run ends on its pinned model "
                  "(%s), no re-plan" % (plan["model"] or plan["route"].get("combo")),
                  file=sys.stderr)
        if (stop is not None and not args.joinable and not args.no_fallthrough
                and fallthroughs < MAX_FALLTHROUGH):
            next_plan = None
            fell_from = fell_to = None
            try:
                if args.free:
                    next_plan, fell_from, fell_to = _free_fallthrough_plan(
                        args, cfg, plan, free_chain, excluded_free_models,
                        benched=set(provider_cooldown), fence=fence,
                        registry=live_registry or registry)
                elif plan["route"].get("resolver") and plan["sandbox"]:
                    # RATELIMITRETRY: after a 429 the next leg is picked for a
                    # DIFFERENT provider, so walk past a candidate that every one of
                    # its legs benches. A candidate whose provider is not in the
                    # cooldown set is taken as it stands — preferring another provider
                    # is not the same as requiring one, and a route that resolves to no
                    # provider at all is a route we cannot blame for the 429.
                    excluded = excluded_routes | {plan["route"]["combo"]}
                    while True:
                        try:
                            next_plan = build_plan(args, cfg, exclude_routes=excluded,
                                                   sandbox=plan["sandbox"],
                                                   provider_cooldown=provider_cooldown,
                                                   fence=fence)
                        except (clients.DepthError, RouteInputRequired, RouteDeferred,
                                PrivacyRefused, ValueError):
                            next_plan = None  # no route left (or unplannable): exit 8
                        except FamilyFenceRefused as exc:
                            fence_refused = exc
                            next_plan = None
                        next_combo = ((next_plan or {}).get("route") or {}).get("combo")
                        if not next_combo or next_combo == plan["route"]["combo"]:
                            next_plan = None
                            break
                        if not combo_is_benched(next_combo, provider_cooldown,
                                                live_registry):
                            break
                        if next_combo in excluded:
                            next_plan = None  # every candidate benches: no way round it
                            break
                        excluded = excluded | {next_combo}
                    if next_plan is not None:
                        fell_from, fell_to = plan["route"]["combo"], next_combo
                        excluded_routes.add(plan["route"]["combo"])
            except FamilyFenceRefused as exc:
                fence_refused = exc
                next_plan = None
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
                # Scrubbed the same way as the first launch (FF1, D-106): the
                # fallthrough must not be the site that inherits the caller's
                # tokens back in.
                env = worker_env(plan, key if uses_key else None)
                # FF1 Sonnet LOW: the re-plan minted a fresh run id, and the
                # private runtime/config dirs are named after it — so the
                # re-run's dirs are the ones that do not exist yet. The first
                # launch provisioned the stopped attempt's; without this the
                # fallthrough worker runs with an XDG dir nobody created (and
                # may create itself, outside the 0700 rule).
                if not provision_worker_dirs(env):
                    # FF1d: same rule as the first launch — a refused dir stops
                    # the re-run (2 is the code a refused card or route gives),
                    # and the break still announces the stopped attempt's WIP.
                    rc = 2
                    break
                register_secret_env(env)
        if not fell_through:
            if fence_refused is not None:
                # FAMILYFENCE: the stop's own rc (8) says "no leg answered". What
                # actually answered is "the only leg left was the fenced family",
                # and that difference is the whole point of the exit code.
                print("autoos-agent: %s" % fence_refused, file=sys.stderr)
                rc = EXIT_NO_OTHER_FAMILY
            elif stop is not None and fallthroughs >= MAX_FALLTHROUGH:
                # RATELIMITRETRY (SB-B): the cap is the only thing that ends the
                # run, so say what ran out — a silent exit 8 reads as "the task
                # failed", not as "every leg we could reach refused to serve it".
                why = "rate limit (429)" if rate_limited else "provider stop"
                benched = ", ".join(sorted(provider_cooldown)) or "no provider named"
                print("autoos-agent: %s on every leg after %d fallthroughs "
                      "(benched this run: %s) - giving up" % (why, fallthroughs, benched),
                      file=sys.stderr)
            break
    # RUNMODEL (D-103 item 4, SB-B): WHO served this run, once the attempts are
    # over — after a fallthrough the route the run started on is not the route
    # that wrote the diff. Recorded three places that a reader of the run looks
    # at: the private record `cancel` already reads, this run's own log line (and
    # so the output.log the fleet runner merges), and the header of the sandbox
    # report block below. A gateway run that cannot be resolved says `unresolved`
    # rather than repeating what the plan asked for. FAMILYFENCE-b: what it CAN
    # name carries `source` — the witness that named it — because after a fallthrough
    # on an own-account client, "the plan said Efficient" is not "Efficient answered".
    writer = None
    if not args.dry_run:
        try:
            writer = resolved_writer(plan, uses_key, registry=live_registry)
        except Exception as exc:  # noqa: BLE001 - a record never replaces a verdict
            print("autoos-agent: writer unresolved (%s)" % type(exc).__name__,
                  file=sys.stderr)
            writer = {"provider": WRITER_UNRESOLVED, "model": WRITER_UNRESOLVED,
                      "family": WRITER_UNRESOLVED, "source": WRITER_UNRESOLVED}
        if writer is not None:
            print(writer_line(writer))
            # FAMILYFENCE item 4: a review's independence is a claim about the
            # RESOLVED writer, and the plan cannot prove it — OmniRoute falls
            # through to legs inside a combo that the spawner never sees. So the
            # claim is checked here, after the run, against the same evidence the
            # writer line uses, and a same-family verdict does not exit 0.
            if fence["review"] or plan["route"].get("review"):
                print(cross_family_line(writer, fence))
                # FAMILYFENCE-3 N5: this is the same question `cross_family_line`
                # just printed, so it reads the same predicate — a verdict line and
                # an exit code that disagree are two answers, and only one of them
                # stops the run.
                if rc == 0 and fence_collision(writer.get("family"), fence):
                    print("autoos-agent: %s" % fence_refusal(fence), file=sys.stderr)
                    rc = EXIT_NO_OTHER_FAMILY
            # The record belongs to a spawn; a `run` started straight from a
            # shell has no record and mints one here for nothing to read.
            if read_kill_record(plan.get("run_id")) is not None:
                write_kill_record(plan.get("run_id"), {"writer": writer})
        # SCOPECLI-b: WHO wrote it is only half of what a reader of this log can
        # act on — WHERE it ran decides whether a cancel can reach it at all. Said
        # only when a launch was actually decided, so a run refused before the
        # first attempt has no line claiming a path it never took.
        if scope_rec is not None:
            print(scope_line(scope_rec))
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
        # SPAWNREDACT item 2: a filename or a commit subject is worker text too.
        print("\nsandbox changes (uncommitted):\n" + redact_output(changed or "  (none)"))
        if ahead:
            print("sandbox commits:\n" + redact_output(ahead))
        # SB-A (D-103) item 2 (NOOPCOMMIT): neither read above sees a commit on a
        # branch the worker made, and a detached HEAD hides both. Only the run
        # that looked like no work at all pays for the third read.
        off_ref = (sandbox_committed_work(sb["path"], sb["base"], branch, sb["refs"])
                   if not changed and not ahead and sb.get("refs") is not None else [])
        if off_ref:
            print("sandbox commits off the run branch:\n"
                  + redact_output("\n".join("  " + ln for ln in off_ref)))
        # MODEFLIP (SB-B, D-103 item 3): a diff that only flips 100644 <-> 100755
        # is not work — it is a filesystem artifact of the checkout. Read after the
        # WIP commit above, so what it judges is everything the run produced
        # (committed and uncommitted). A review run is exempt: its diff is the
        # deliverable someone asked for, not a claim of implementation. Two ways
        # out of the refusal (SB-B review 2), because a real task can BE a chmod:
        # `--allow-mode-only` says so on the command line, and a spawn says it with
        # the `allow_mode_only` field — which the runner recorded in the private
        # record at spawn, so what releases the verdict is a decision made before
        # the worker ran, not something the worker can put in its own job.json.
        allow_mode_only = bool(getattr(args, "allow_mode_only", False) or
                               (read_kill_record(plan.get("run_id")) or {})
                               .get("allow_mode_only"))
        mode_flip = (None if plan["route"].get("review") or allow_mode_only else
                     sandbox_mode_only(sb["path"], sb["base"], "HEAD"))
        q = shlex.quote(sb["path"])
        if mode_flip:
            print("MODEFLIP: the sandbox diff is file-mode changes only (%s) - "
                  "mode-only diff (refused), so there is no take-it line to fetch. "
                  "If the mode IS the task, re-run with --allow-mode-only (a spawn: "
                  "allow_mode_only=True)." % redact_output(mode_flip))
        else:
            print("review:  git -C %s diff" % q)
            print("take it: git fetch %s %s   (then review FETCH_HEAD)" % (q, sb["branch"]))
            for ln in off_ref:
                ref, _, rest = ln.partition(" ")
                if ref.startswith("refs/heads/"):
                    print("take it: git fetch %s %s   (the worker's own branch)"
                          % (q, ref[len("refs/heads/"):]))
                else:
                    print("take it: git -C %s branch <name> %s   (detached HEAD)"
                          % (q, rest.split()[0]))
        extra = " " + shlex.quote(sb["path"] + ".opencode-data") if client.name == "opencode" else ""
        print("discard: rm -rf %s%s" % (q, extra))
        read_only = bool(plan["route"].get("read_only"))
        # SPAWNFIX3c (S2): only a read-only run is judged on an untouched
        # sandbox, so only it pays for the reflog read and the diff stat. A
        # commit the run reset away leaves both of those empty (the worktree is
        # back at base) — the reflog shas are the only witness.
        reset_away = sandbox_reflog_writes(sb["path"], sb.get("reflog"),
                                           sb["base"]) if read_only else []
        override, message = sandbox_verdict(
            plan["route"], changed, ahead, client_tail,
            # Only the read-only verdict quotes the diff stat, so only the
            # read-only run pays for the extra git call.
            sandbox_diffstat(sb["path"], sb["base"]) if read_only else "",
            reflog=", ".join(reset_away),
            brief=plan.get("brief") or "",
            extra="; ".join(off_ref))
        leak = parent_leak(parent_snap, root=sb.get("source") or ROOT,
                           sandbox=sb["path"])
        if leak:
            # A LEAK overrides the child's rc AND the NO-OP verdict: the run
            # did change something, just in the wrong checkout. Never reverts
            # anything.
            print("LEAK: the --isolate run wrote outside its sandbox "
                  "(containment failure, exit 7): %s" % redact_output("; ".join(leak)),
                  file=sys.stderr)
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
        elif mode_flip and rc == 0:
            # The run changed a mode and nothing else: the same verdict as having
            # changed nothing (5), because a merge candidate with no content in it
            # is not work someone could review.
            rc = 5
        # Every finished --isolate run is a track-record observation (spec §5.6).
        # REVFIX review 2: the survivor's latency is its OWN attempt, not the
        # cumulative `start` that also spans the dead attempts (each of which
        # already recorded its own sample above).
        tracked = track_entry(plan, rc, time.time() - attempt_start)
        if tracked is not None:
            # Redact once, here: `record_run` writes it and `propose_reprobe`
            # derives a logged proposal and a printed line from the same dict.
            tracked = redact_record(tracked)
            record_run(TRACK_RECORD, tracked)
            propose_reprobe(tracked, REGISTRY_PATH, MEASURED_OVERLAY_PATH,
                            PROBE_PROPOSALS_LOG, sb["path"], LEGACY_OVERLAY_PATH)
    # SPAWNREDACT item 2: tell the caller its worker's output was altered,
    # once, after every stream of this run has been written.
    report_redactions()
    # Log the FINAL rc: the LEAK override happens above and the log, the
    # orch-*.log watchdog and the track record must not disagree.
    log_run(plan, rc, time.time() - start, args.free)
    return rc


def cmd_inbox(args) -> int:
    """Print one inbox's records since a position (RESTART spec §2, lane R1).

    The record shapes live in tools/autoos_inbox.py (§0, one home); this is the
    window and the printing. Records go to stdout with their continuations and
    a malformed line inside the window goes there too, tagged `(malformed line
    N)` with the lines under it, so a context pack that embeds stdout keeps
    every acknowledgement. The notice is made only for an entry this run
    actually prints — it promises the text is on stdout, and a line behind
    `--since` or dropped by the `--max-records` cut reaches nowhere. A malformed
    entry cut with the record it rode on is counted in the cut line.
    Every notice goes to stderr.

    Exit 0 read, 1 the file holds no timestamped record (an inbox of another
    shape is never read as "no events"), 2 no window named, a bad position, or
    an unreadable file."""
    if args.file and args.name:
        print("inbox: name one source -- an <name> under the RUN dir or --file, "
              "not both", file=sys.stderr)
        return 2
    try:
        path = args.file or inbox.inbox_path(args.name)
    except inbox.InboxError as exc:
        print("inbox: %s" % exc, file=sys.stderr)
        return 2
    if args.all and (args.since or args.since_card):
        print("inbox: --all reads the whole file; drop --since/--since-card",
              file=sys.stderr)
        return 2
    if args.since and args.since_card:
        print("inbox: name one window -- --since-card reads where the card "
              "stopped, --since where you say", file=sys.stderr)
        return 2
    if not args.all and not args.since and not args.since_card:
        print("inbox: name the window -- --since-card <card>, --since "
              "<position|UTC> or --all", file=sys.stderr)
        return 2
    if args.max_records < 1:
        print("inbox: --max-records must be at least 1", file=sys.stderr)
        return 2
    since = None
    since_text = ""
    if args.since_card:
        try:
            position = inbox.card_last_event(args.since_card)
        except inbox.InboxError as exc:
            print("inbox: %s" % exc, file=sys.stderr)
            return 2
        if position is None:
            print("inbox: %s has no last-event -- reading from the start"
                  % args.since_card, file=sys.stderr)
        else:
            since, since_text = position, position
            source = args.since_card
    elif args.since:
        since, source = args.since, "--since"
        since_text = args.since
    if isinstance(since, str):
        try:
            since = inbox.parse_position(since)
        except ValueError as exc:
            print("inbox: %s: %s" % (source, exc), file=sys.stderr)
            return 2
    try:
        records, malformed = inbox.parse_file(path)
    except inbox.InboxError as exc:
        print("inbox: %s" % exc, file=sys.stderr)
        return 2
    if not records:
        print("inbox: no timestamped records in %s" % path, file=sys.stderr)
        return 1
    entries = inbox.window_entries(records, malformed, since)
    selected = [entry for entry in entries if not
                isinstance(entry, inbox.Malformed)]
    if len(selected) > args.max_records:
        cut = len(selected) - args.max_records
        dropped = {id(entry) for entry in selected[:cut]}
        kept = [entry for entry in entries
                if id(entry) not in dropped
                and not (isinstance(entry, inbox.Malformed)
                         and id(entry.anchor) in dropped)]
        # Names the malformed entries that rode on a cut record, so the budget
        # that dropped them is stated whole rather than implied by the records.
        lost = sum(1 for entry in entries
                   if isinstance(entry, inbox.Malformed)
                   and id(entry.anchor) in dropped)
        print("inbox: cut %d earlier records%s"
              % (cut, " and %d malformed entries" % lost if lost else ""),
              file=sys.stderr)
        entries = kept
    for entry in entries:
        if isinstance(entry, inbox.Malformed):
            lines = 1 + len(entry.continuations)
            print("inbox: malformed at line %d (%d %s): the line looks like a UTC "
                  "timestamp but is not one, so it is neither a record nor a "
                  "continuation; its text is on stdout tagged (malformed line %d)"
                  % (entry.line, lines, "line" if lines == 1 else "lines",
                     entry.line), file=sys.stderr)
    if not selected:
        # Names the window as the caller wrote it: a bare UTC cut reads that
        # whole second internally (ordinal 0), and "#0" is not a position a card
        # may hold, so echoing it would invite an unparseable last-event.
        print("inbox: nothing since %s (%d records in %s)"
              % (since_text or "the start", len(records), path), file=sys.stderr)
    for entry in entries:
        if isinstance(entry, inbox.Malformed):
            # No position to head it with — the tag is its place in the file.
            print("(malformed line %d) %s" % (entry.line, entry.text))
        else:
            print("%s%s %s" % (entry.position,
                               " (late)" if entry.late else "", entry.text))
        for extra in entry.continuations:
            print(extra)
    return 0


def cmd_card(args) -> int:
    """`card check <file>` — validate one state card (RESTART spec §1, lane R2a).

    The rules live in tools/autoos_card.py; this is the printing and the exit
    code. Every problem prints on stdout with its line number, because the
    lines *are* the deliverable: a successor edits the card from them. What is
    wrong with the file as a whole (it is not there, not readable) is a notice
    on stderr, the way `inbox` splits content from notices, so a pack can embed
    stdout verbatim. A `last-event` that does not parse is §0's problem, so it
    reads §0's own parser and its own wording.

    Exit 0 valid, 1 the card breaks at least one §1 rule, 2 the file is
    unreadable (argparse itself refuses when no card is named)."""
    try:
        text = card_mod.read_card(args.file)
    except card_mod.CardUnreadable as exc:
        print("card check: %s" % exc, file=sys.stderr)
        return 2
    problems = card_mod.check_card(text)
    if not problems:
        print("card check: OK — %s (%d lines, cap %d)"
              % (args.file, len(card_mod.split_lines(text)), card_mod.MAX_TOTAL_LINES))
        return 0
    for problem in problems:
        print("card check: line %d: %s" % (problem.line, problem.text))
    print("card check: %s is not a card (§1): %d %s"
          % (args.file, len(problems), "problem" if len(problems) == 1 else "problems"),
          file=sys.stderr)
    return 1


# ── verbs: one table builds the parsers, one table dispatches ────────────────
# What this replaces is the old last line of main(),
# `cmd_list(cfg) if args.cmd == "list" else cmd_run(args, cfg)`: every verb that
# was not `list` fell through into `run`, so a verb added to the parser but not
# to the dispatch silently spawned an agent, and a mistyped verb spawned one too.
# A verb now has to be in BOTH tables; one without a handler is refused (RESTART
# spec R1, "it freezes the dispatch").

def _parser_usage(sub):
    sub.add_parser("usage", help="usage report by provider/combo/lane from the OmniRoute "
                                 "gateway (OR4); its own flags follow `usage`, e.g. "
                                 "`usage --since 1h --by provider,lane`")


def _parser_token_rate(sub):
    sub.add_parser("token-rate",
                   help="orchestrator tokens per merged change (RESTART spec §5); "
                        "its own flags follow `token-rate`, e.g. "
                        "`token-rate --since 48h --cwd-prefix <lane dir>`")


def _parser_list(sub):
    sub.add_parser("list", help="show the tiers, their models and who may spawn whom")


def _parser_ps(sub):
    ps = sub.add_parser("ps", help="live table of every spawned worker on this host (all "
                                   "worktrees and clones); deletes records that ended "
                                   "(or died) more than 7 days ago")
    ps.add_argument("--all", action="store_true",
                    help="also show exited workers from the last 24 h")
    ps.add_argument("--tree", action="store_true",
                    help="indent each run under the run that spawned it (the "
                         "parent_run_id edge; --json wins over --tree)")
    ps.add_argument("--json", action="store_true", help="print the rows as JSON")


def _parser_run(sub):
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
    run.add_argument("--model", help="pin the model: a combo declared in opencode.jsonc "
                                     "(e.g. omniroute/t2-orchestrator) for a gateway client, "
                                     "or the client's own model name for an own-account one "
                                     "(qodercli/claude --model; its family feeds the fence)")
    run.add_argument("--free", action="store_true", help="keyless: every tier on opencode's free model")
    run.add_argument("--free-model", default=DEFAULT_FREE_MODEL)
    run.add_argument("--not-family", dest="not_family", action="append",
                     metavar="FAMILY",
                     help="FAMILYFENCE: exclude a model FAMILY from the whole plan "
                          "(the initial leg and every fallthrough candidate); repeat it "
                          "for more than one. A review run gets its writer's family "
                          "excluded automatically when --review-of can name it")
    run.add_argument("--review-of", dest="review_of", metavar="RUN_ID",
                     help="FAMILYFENCE: this run reviews RUN_ID's diff, so the family "
                          "that WROTE it is fenced out. Read from that run's "
                          "runner-private record, never from its job.json; a family "
                          "this checkout's record store cannot name is refused (rc 2) "
                          "rather than run unfenced (FAMILYFENCE-4)")
    run.add_argument("--no-fallthrough", dest="no_fallthrough", action="store_true",
                     help="FAMILYFENCE: a provider stop on the pinned model ends the run "
                          "with its stop code; no re-plan onto another model")
    run.add_argument("--isolate", action="store_true",
                     help="run in a private git clone on its own branch; writes outside it are "
                          "denied. Mandatory for every spawned tier (%s): a clone holds "
                          "committed files only, so no git-ignored key file is in the tree it "
                          "greps" % ", ".join(str(t) for t in ISOLATE_TIERS))
    run.add_argument("--no-auto", dest="auto", action="store_false",
                     help="ask before tools the config does not explicitly allow (default: --auto)")
    run.add_argument("--lean", action="store_true",
                     help="no heavy MCP servers (%s) - for research/review agents" % ", ".join(LEAN_DROP))
    run.add_argument("--read-only", dest="read_only", action="store_true",
                     help="a research run: an unchanged sandbox plus a REPORT is success (exit 0), an "
                          "edit in it is the failure (exit 11). A v2 card kind=research marks the same "
                          "run without this flag")
    # MODEFLIP opt-out (SB-B review 2): the refusal is the default because a diff
    # that only flips 100644<->100755 is nearly always a filesystem artifact, but
    # `chmod +x scripts/deploy.sh` is a real task somebody can ask for, and the
    # run that does it has nothing else in its diff to show for it.
    run.add_argument("--allow-mode-only", dest="allow_mode_only", action="store_true",
                     help="accept a sandbox whose only change is a file mode "
                          "(100644 <-> 100755): the MODEFLIP refusal is what an "
                          "artifact of the checkout looks like, and this says the "
                          "diff is the task")
    run.add_argument("--title")
    run.add_argument("--run-id", dest="run_id", metavar="ID",
                     help="use this id (a canonical one, as minted by the MCP server's spawn) "
                          "instead of minting a new one, so one spawn has one id: it names the "
                          "branch, the sandbox, logs/workers/<id>.json and the child env; a bad "
                          "shape is refused with exit 2")
    run.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    run.add_argument("task")


def _parser_context(sub):
    context = sub.add_parser("context", help="print this session's context fill")
    context.add_argument("--transcript", help="a Claude Code transcript JSONL (default: discover)")
    context.add_argument("--model", help="override the model the cap is looked up for")
    context.add_argument("--json", action="store_true", help="print the fill as JSON")


def _parser_heartbeat(sub):
    heartbeat_p = sub.add_parser("heartbeat", help="read-only pause/branch/context check "
                                 "(R-heartbeat-02/03, R-pause-01, R-handoff-07)")
    heartbeat_p.add_argument("--inbox", help="an inbox file to scan for the newest "
                             "stop (PAUSE anywhere, or a bare leading STOP/HALT/ABORT; "
                             "HOLD/FREEZE are capacity notes, not stops) "
                             "or release (RESUME)")
    heartbeat_p.add_argument("--transcript", help="a Claude Code transcript JSONL (default: discover)")
    heartbeat_p.add_argument("--repo", dest="repos", action="append",
                             help="a git repo to check for unpushed/dirty state (default: cwd); repeatable")
    heartbeat_p.add_argument("--cap", type=int, help="override the model's hand-off cap (tokens)")
    heartbeat_p.add_argument("--json", action="store_true", help="print the report as one JSON object")


def _parser_route(sub):
    route = sub.add_parser("route", help="print the resolver v2 route_plan for a task card")
    route.add_argument("--card", required=True,
                       help="task card, e.g. kind=review,paths=tools/registry.py (or JSON)")
    route.add_argument("--brief", default="", help="the task brief text (counts toward need_tokens)")
    route.add_argument("--client", choices=sorted(clients.CLIENTS),
                       help="the client the card would run under, so the Claude "
                            "budget is answered for that client (CLAUDEBUDGET-b "
                            "item 3); default opencode")
    route.add_argument("--explain", action="store_true",
                       help="print the explain lines and reason to stderr before the JSON")
    route.add_argument("--orchestrator-model", dest="orchestrator_model",
                       default=DEFAULT_ORCHESTRATOR_MODEL,
                       help="registry model id billed for verification (default: %(default)s)")
    route.add_argument("--repo", help="repo root to measure against (default: this checkout)")
    route.add_argument("--now", help="ISO 8601 UTC clock reading (default: now)")


def _parser_risk(sub):
    risk_p = sub.add_parser(
        "risk", help="classify a commit's diff as normal or high risk from "
                     "policy.risk_rules (RISKTIER-a): the writer does not grade "
                     "its own work, the diff does")
    risk_p.add_argument("--sha", required=True,
                        help="the commit to classify: any rev git resolves to a "
                             "commit (HEAD, a branch, a short sha); resolved with "
                             "rev-parse --verify before the audit draw")
    risk_p.add_argument("--base", default="origin/main",
                        help="the diff's other end, compared at its merge base "
                             "with --sha (default: %(default)s)")
    risk_p.add_argument("--repo", default=".",
                        help="git checkout to read the diff from (default: the cwd)")
    risk_p.add_argument("--registry",
                        help="registry holding policy.risk_rules "
                             "(default: catalog/ai-registry.json)")
    risk_p.add_argument("--json", action="store_true",
                        help="print the whole assessment as one JSON object")


def _parser_review_status(sub):
    review_status_p = sub.add_parser(
        "review-status", help="read a lane record and report whether it carries both "
                              "reviews a ready lane needs: a cross-family review and the "
                              "final check (REVROUTE)")
    review_status_p.add_argument("record", help="the lane record (status/<lane>.<name>.md), or - for stdin")
    review_status_p.add_argument("--registry",
                                 help="registry to resolve model families against "
                                      "(default: catalog/ai-registry.json)")


def _parser_ready(sub):
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
                         help="the controller's inbox to append the ready line to "
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


def _parser_inbox(sub):
    inbox_p = sub.add_parser(
        "inbox", help="read an inbox window instead of the whole file (RESTART §2): "
                      "records print whole with their positions, so a successor "
                      "resumes at `last-event` and never opens a 50k-token inbox")
    inbox_p.add_argument("name", nargs="?",
                         help="the inbox name, read as <RUN>/inbox/<name>.md "
                              "(RUN = $AUTOOS_RUN_DIR)")
    inbox_p.add_argument("--file", help="read this inbox file instead (wins over <name>)")
    inbox_p.add_argument("--since-card", metavar="CARD",
                         help="resume at the `last-event <position>` in this card's header")
    inbox_p.add_argument("--since", metavar="POSITION_OR_UTC",
                         help="read after this `<timestamp>#<ordinal>` position, or after "
                              "this UTC second (a bare stamp reads the whole second)")
    inbox_p.add_argument("--all", action="store_true", help="read every record in the file")
    inbox_p.add_argument("--max-records", type=int, default=30, metavar="N",
                         help="print at most N records, cutting the oldest (default: %(default)s)")


def _parser_card(sub):
    card_p = sub.add_parser(
        "card", help="the state card a successor resumes from (RESTART §1) — `card check "
                      "<file>` prints every problem with its line number")
    card_sub = card_p.add_subparsers(dest="card_action", metavar="ACTION",
                                     required=True)
    check_p = card_sub.add_parser(
        "check", help="check one card: the header fields, the section order, each "
                      "section's line cap, the 40-line total, the 200-char line limit "
                      "and that `last-event` parses as a position (§0's parser)")
    check_p.add_argument("file", help="the card file, usually <RUN>/status/<name>.card.md")


VERB_PARSERS = {
    "usage": _parser_usage,
    "token-rate": _parser_token_rate,
    "list": _parser_list,
    "ps": _parser_ps,
    "run": _parser_run,
    "context": _parser_context,
    "heartbeat": _parser_heartbeat,
    "route": _parser_route,
    "risk": _parser_risk,
    "review-status": _parser_review_status,
    "ready": _parser_ready,
    "inbox": _parser_inbox,
    "card": _parser_card,
}

# Every handler takes (args, cfg); cfg is the opencode.jsonc only the spawning
# verbs read, loaded for those two and None for the rest. `usage` and
# `token-rate` are the registered verbs with no entry here: main() hands them to
# autoos_usage / autoos_tokenrate before argparse runs, because every flag after
# them belongs to that module.
VERB_HANDLERS = {
    "context": lambda args, cfg: cmd_context(args),
    "card": lambda args, cfg: cmd_card(args),
    "heartbeat": lambda args, cfg: cmd_heartbeat(args),
    "inbox": lambda args, cfg: cmd_inbox(args),
    "list": lambda args, cfg: cmd_list(cfg),
    "ps": lambda args, cfg: cmd_ps(args),
    "ready": lambda args, cfg: cmd_ready(args),
    "review-status": lambda args, cfg: cmd_review_status(args),
    "route": lambda args, cfg: cmd_route(args),
    "risk": lambda args, cfg: cmd_risk(args),
    "run": lambda args, cfg: cmd_run(args, cfg),
}

CFG_VERBS = frozenset({"list", "run"})


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["usage"]:  # everything after `usage` belongs to autoos_usage
        return usage_mod.main(list(argv[1:]))
    # ... and everything after `token-rate` belongs to autoos_tokenrate
    # (RESTART spec §5: the metric verb, no gateway needed).
    if argv[:1] == ["token-rate"]:
        return tokenrate_mod.main(list(argv[1:]))
    ap = argparse.ArgumentParser(description="Spawn one AutoOS tier agent (see module docstring).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for build_parser in VERB_PARSERS.values():
        build_parser(sub)
    args = ap.parse_args(argv)
    handler = VERB_HANDLERS.get(args.cmd)
    if handler is None:
        print("autoos-agent: verb '%s' is registered but has no handler -- refusing "
              "rather than falling through to `run`" % args.cmd, file=sys.stderr)
        return 2
    cfg = load_jsonc(os.path.join(ROOT, "opencode.jsonc")) if args.cmd in CFG_VERBS else None
    return handler(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
