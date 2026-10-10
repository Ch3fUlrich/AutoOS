#!/usr/bin/env python3
"""Spawn one agent - any client, the right model, key, isolation and depth - one command.

The 3-tier agents live in opencode.jsonc (l1-orchestrator -> l2-worker ->
t3-reviewer, .agents/skills/unattended-orchestration/unattended-orchestration.md).
Driving them by hand has
four traps, all measured 2026-09-24 against opencode 2.0.16:

  1. `opencode run --agent l2-worker` runs on the TOP-LEVEL default model
     (omniroute/l1-orchestrator), not the agent's own - the agent/model pairing only
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
     repo built from HEAD (`isolate_clone` materialises the allowed HEAD
     entries into a fresh `git init` - uncommitted changes are not in it) on
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
is l2-worker; `--card privacy=sensitive,ctx=1m` fails closed (the only
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

Layer: a lane marks which layer it runs at in AUTOOS_AGENT_LAYER (tools/
oc_l1_render.py writes it; nothing else decides it). A spawn made for an L2 lane
stamps its child AUTOOS_AGENT_LAYER=L3 — a LEAF — and both fences are enforced by
the spawner, not inherited and never a caller's or a plan's own value: an L2 lane
may start tier 2 or tier 3 only (never tier 1, never a role=orchestrate card, never
Claude), and a leaf never spawns at all (R-worker-06). Either refusal exits 14,
distinct from 2 on purpose: a layer refusal is a report upward, not a flag to fix
and retry. An unmarked session is an L1's and is fenced by neither rule.

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
    python3 tools/autoos-agent.py run --tier 2 --isolate --model omniroute/l2-orchestrator "..."
    python3 tools/autoos-agent.py run --tier 1 --free "..."        # no keys at all
    python3 tools/autoos-agent.py run --tier 3 --isolate --dry-run "..."     # print the plan only
    python3 tools/autoos-agent.py context                          # this session's fill
    python3 tools/autoos-agent.py context --transcript s.jsonl --json
    python3 tools/autoos-agent.py heartbeat --inbox i.md --transcript s.jsonl --json
    python3 tools/autoos-agent.py ready status/<lane>.<name>.md --branch <B> --sha <SHA> \
        --inbox <INBOX> --brief <BRIEF path> --report <REPORT path>   # both, always
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
serving the run inside it. The family a spelling is judged on is the registry's row
for it, the vendor named inside the id, or the one family a route's legs all declare
(`fence_family_of`) — a pin the registry lists no row for names its own family from
its id and sits inside only the fence that names it (AO-FAMILYFENCE-QWEN). A name
the registry carries no family under is refused (rc 2) before any leg choice,
because a fence that excludes nothing reads as a
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
The family is RESOLVED, not read off one table only (AO-RUN-FAMILY-RECORD, D-807):
a registry row, the vendor the model id itself names, a route whose legs all declare
one family, the provider half, an own-account client's own default model, and finally
that client's name — in that order, the first that names a model answering. The
client-default layer answers only for an id that IS that default (its name or one of
`clients.CLIENT_DEFAULT_KEYS`), never for whatever the caller pinned. One
normalisation table (`FAMILY_VENDORS`) turns a spelling into the registry's family
name by WHOLE names, not by a piece of one — the id is split at every separator and
every letter/digit boundary and a token answers only where its own names stand
together, so `gpt-oss` is openai-oss and never openai while `notsonnet` and
`mimosa-v1` are nothing. A record carries `family_source` beside a family and
`family_reason` beside a null, and null is the answer only when nothing names a
model: `unresolved` is a display marker for "no model was witnessed", never a
family. A family that no witness attached to a served model — the spawn-time half in
job.json's `request`, the worker record, and `exit.json` when the kill record named
nothing — says `family_source: planned-model`, and that claim opens no gate:
`writer_family_of_run` and the review footer read a family only from a served source.

Never prints a key. The OmniRoute client key comes from AUTOOS_OMNIROUTE_KEY
or the gateway-named field of configuration/api-keys.yml (`omniroute_server`
for a non-local gateway, `omniroute_<host>` for the local one - see
docs/api-keys.md) and is handed to the
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
(FREE_QUEUE_TIMEOUT_SECONDS) for a slot on its free provider and nothing started (SPAWNFREE item 2);
13 HOST-ADMISSION: this host has no room for one more worker (HOSTADMISSION) - the live worker
count reached host_admission.max_live_workers, or /proc/meminfo's MemAvailable is below
host_admission.mem_available_floor_mb. It starts nothing and never waits: queueing is the caller's
job, or the run goes to another machine. The message names the count, the memory, both limits.

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
import copy
import datetime
import errno
import importlib.util
import io
import json
import os
import posixpath
import re
from pathlib import Path
import shlex
import shutil
import signal
import site
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
import prepush as prepush_mod  # noqa: E402  (D-110: a ready line needs a green gate)
import autoos_ready_guards as ready_guards  # noqa: E402  (AO-WRITER-GUARDS P4b: the ready gate)
import autoos_tokenrate as tokenrate_mod  # noqa: E402
import autoos_track as track  # noqa: E402
import autoos_usage as usage_mod  # noqa: E402
import autoos_verdict as seat_verdict  # noqa: E402  (AO-SEAT-VERDICT-GRAMMAR)
from registry import private_safe, registry_ref, resolve_leg, unavailable_now  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIERS = {1: "l1-orchestrator", 2: "l2-worker", 3: "t3-reviewer"}
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
TRACK_CLASS = {"t1": "frontier", "t2": "cheap", "t3": "free",
               "l1": "frontier", "l2": "cheap", "l3": "free"}
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
# for verification cost when the caller does not pin one. CLAUDE55 2026-10-05
# (operator): the 4-6 generation is retired upstream, so the default moves to
# the 5-5 spelling the agy CLI and the live gateway catalog serve.
DEFAULT_ORCHESTRATOR_MODEL = "claude-opus-5-5-medium"
# A v1 combo (l1-orchestrator, l2-worker, l3-driver, their -clean twins) always
# carries one of these prefixes. A resolver v2 route id (RUNV2) may or may not
# (e.g. "l1-orchestrator-free-only" does; "t4-rag" and "deepseek-v4.1-flash" do
# not - t4 is not even a TIERS key). _tier_for_route defaults to tier 2 when it
# does not: the resolver has already priced and picked the model that will
# actually run, so this only decides which local opencode agent identity
# (l1-orchestrator/l2-worker/t3-reviewer) spawns the client.
_TIER_PREFIX_RE = re.compile(r"^[tl]([123])-")
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
TIER_SPAWN_CHILD = {1: "l2-worker", 2: "t3-reviewer", 3: None}


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
# materialises HEAD's allowed files into a fresh one-commit repo, so an ignored
# secret is never present for the search to walk - and neither is any excluded
# file's blob, in the worktree or in `.git`.
# KEYDENY3g (L1-routing policy decision): that directory is mandatory for every
# *spawned* tier. Tier 2 was left in place by KEYDENY3b and the hole it recorded
# is the one that matters: a t2 running in the caller's checkout greps an ignored
# key file, and the native t3 child it launches inherits that cwd and carries
# none of the fences. Only tier 1 (role=orchestrate) — the operator's own session,
# in a lane the operator is watching — may still run in place.
ISOLATE_TIERS = (2, 3)
# A client that cannot run in a clone would have to lose grep/glob for its leaf
# runs instead (the fence is the only control there). None today: isolation is
# a fresh repo plus a cwd, and every client here is a CLI started with one.
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


# T2-RECORD-PIN item 4: tier 3 is the reviewer's seat. The rule in one place,
# so the CLI's refusal, the MCP spawn's ValueError and every test read the same
# words (the spawner holds the predicate `is_write_role`, never a second copy).
REVIEW_TIER_WRITE_REASON = "review-only tier 3 cannot run an implement task"


def review_tier_write_refusal(tier, card, read_only: bool = False) -> str | None:
    """Why tier 3 will not run this task, or None when tier 3 may.

    T2-RECORD-PIN item 4: a run routed to tier 3 exists to produce a verdict,
    so a task that EDITS (a card naming nothing takes the registry default
    `implement`, exactly as `is_write_role` reads it) is refused rather than
    started in a seat whose success is a review. Two ways to say yes are kept
    open and both are read-only in effect: a review card (`role=review`, a v2
    `kind=review`/`kind=research`), and `--read-only`, where an unchanged
    sandbox is the deliverable. Every other tier is untouched — this keys on the
    tier number alone, never on the leaf flag the isolation gate owns.

    `tier` may be an int, a numeric string or None (an unparsable tier is not
    this function's error to report: it is not tier 3).
    """
    if read_only:
        return None
    try:
        if int(tier) != 3:
            return None
    except (TypeError, ValueError):
        return None
    if not is_write_role(card):
        return None
    return ("%s: pass a review card (role=review, kind=review/research) or "
            "--read-only, or run the task at tier 1 or 2"
            % REVIEW_TIER_WRITE_REASON)


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
                             capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    except OSError:
        return ROOT
    return top or ROOT


ISOLATE_EXCLUDED_TOPDIR = "secrets-generated"

# T2-ISOLATE-SECRETS S3: tracked names that look like a plaintext secret.
# Matching is case-folded (I8), `*.pub` is public by definition, and a
# template is exempt only by SUFFIX (I7) - never for containing ".example".
# A credential FILE, not a page that mentions one: a bare `api-keys*` here
# matched `docs/api-keys.md`, the tracked prose page explaining where each key
# lives, and every real --isolate spawn from this checkout died on a
# plaintext-secret refusal. Shapes name extensions a key file actually has.
_ISOLATE_SECRET_SHAPES = ("*.key", "*.pem", "*.p12", "*.pfx", "*.kdbx",
                          ".env", ".env.*", "credentials*.json")
_ISOLATE_SECRET_PATTERNS = _ISOLATE_SECRET_SHAPES + (
    "api_keys.*", "id_rsa*", "id_ed25519*", "secrets.yml", "secrets.json",
    "secrets.yaml")
_ISOLATE_EXEMPT_SUFFIXES = (".example", ".sample", ".template")


def _isolate_exempt_name(rel: str) -> bool:
    """True only for a template: the final suffix is .example / .sample /
    .template, or the word `example` sits in the penultimate position
    (`.env.example`, `deploy.example.key`, `config.sample.json`).

    I7: the old test was `".example" in base`, so `api.example.com.key` and
    `certs/www.example.com.pem` - real keys for a host whose name happens to
    contain ".example" - were exempt and rode into the sandbox.
    """
    base = os.path.basename(rel).lower()
    if base.endswith(_ISOLATE_EXEMPT_SUFFIXES):
        return True
    parts = base.split(".")
    return len(parts) >= 3 and parts[-2] == "example"


def _isolate_secret_name(rel: str) -> bool:
    """Whether a tracked path is named like a plaintext secret.

    I8: the old match was case-sensitive, so `ID_RSA`, `.ENV` and `a/Prod.PEM`
    passed it - and it flagged `id_rsa.pub`, which is public: a false positive
    there blocks every sandbox of a repo that ships one.
    """
    import fnmatch as _fn
    if _isolate_exempt_name(rel):
        return False
    base = os.path.basename(rel).lower()
    if base.endswith(".pub"):
        return False
    posix = rel.replace(os.sep, "/").lower()
    if base in (".env", "api-keys.yml", "secrets", "secret"):
        return True
    for pat in _ISOLATE_SECRET_PATTERNS:
        if _fn.fnmatchcase(base, pat) or _fn.fnmatchcase(posix, pat):
            return True
    return False


_ISOLATE_ENC_MARKER = "ENC[AES256_GCM,data:"
# SOPS dotenv/INI writes its own metadata keys at column 0 beside the values.
_ISOLATE_SOPS_META = ("sops_version", "sops_lastmodified", "sops_mac",
                      "sops_key_version", "sops_source_format",
                      "sops_message_format")


def _isolate_blob_encrypted(text: str) -> bool:
    """Whether a secret-named blob is SOPS-encrypted rather than plaintext.

    I9 closed three bypasses: an INDENTED `  sops:` line counted, a bare
    `sops_` anywhere counted, and `ENC[AES256_GCM,` inside a comment counted.
    Encrypted is exactly one of: a `sops:` key at COLUMN 0 (YAML); a top-level
    "sops" key of a JSON document; a dotenv/INI body whose every value is
    `ENC[AES256_GCM,data:` (SOPS' own column-0 metadata keys allowed beside
    them). Prose that merely mentions sops is not encryption.
    """
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            import json as _json
            loaded = _json.loads(stripped)
        except Exception:
            loaded = None
        if isinstance(loaded, dict) and "sops" in loaded:
            return True
        return "ENC[AES256_GCM," in text
    for line in text.splitlines():
        if line.startswith("sops:") or line.rstrip() == "sops":
            return True
    pairs = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            continue
        key, eq, value = line.partition("=")
        if not eq:
            # No `=` at all: the whole line is the value, which is the shape of
            # a file that holds one bare ciphertext blob.
            key, value = "", line
        pairs.append((key.strip(), value.strip().strip("\"'")))
    # Every line must be ciphertext (or SOPS' own column-0 metadata beside it),
    # and at least one line must really be ciphertext - a body of comments that
    # merely mention the marker is not an encrypted file.
    marked = [v for k, v in pairs if v.startswith(_ISOLATE_ENC_MARKER)]
    return bool(marked) and all(
        v.startswith(_ISOLATE_ENC_MARKER) or k.lower() in _ISOLATE_SOPS_META
        for k, v in pairs)


def _isolate_blob_plain(data: bytes) -> bool:
    """Whether a secret-named blob's BYTES are plaintext, so the clone refuses.

    I5: the preflight read the blob with text=True, so a binary client.p12
    raised UnicodeDecodeError whose message echoed the undecodable bytes - the
    secret value itself - and cmd_run only caught PrivacyRefused, so it surfaced
    as a traceback. Bytes are read here instead: not valid UTF-8, or holding a
    NUL, is plaintext unless the SOPS marker is in it. Only the PATH is ever
    reported, never a value.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _ISOLATE_ENC_MARKER.encode("utf-8") not in data
    if "\x00" in text:
        return _ISOLATE_ENC_MARKER.encode("utf-8") not in data
    return not _isolate_blob_encrypted(text)


def _isolate_agentignore_patterns(root: str) -> list:
    """One gitignore-style pattern per line from the source HEAD's .agentignore.

    Read from the committed blob (the worktree file may be edited or absent);
    `#` comments and blanks are skipped, absent file means no patterns.
    """
    proc = subprocess.run(["git", "-C", root, "show", "HEAD:.agentignore"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("!"):
            # I4: negation is not implemented for the sandbox filter, and an
            # unimplemented rule must fail closed: the line is dropped with a
            # warning and never read as "include everything".
            sys.stderr.write("autoos: .agentignore negation '%s' is not "
                             "supported by the sandbox filter; the line is "
                             "ignored, the paths stay excluded\n" % stripped)
            continue
        out.append(stripped)
    return out


def _isolate_pattern_matches(rel: str, pat: str) -> bool:
    """Whether one ignore pattern excludes one HEAD path - gitignore semantics.

    I4: the old matcher checked a slash-free pattern against the BASENAME only,
    so `private` left `private/k.txt` and `deep/private/k2.txt` in the sandbox.
    Git's rule: a slash-free pattern matches any path COMPONENT at any depth -
    and because such a component is a directory, everything under it goes too.
    A pattern holding a leading or middle slash is anchored to the repository
    root; a trailing `/` means directories only. Negation is NOT implemented:
    `_isolate_agentignore_patterns` drops a `!` line with a warning, and this
    returns False for one, so an unimplemented rule can never read as
    "include everything".
    """
    posix = rel.replace(os.sep, "/")
    pat = pat.strip()
    if not pat or pat.startswith("#") or pat.startswith("!"):
        return False
    core = pat.lstrip("/").rstrip("/")
    if not core:
        return False
    segs = posix.split("/")
    # Anchoring is a property of the pattern AS WRITTEN, so it is decided before
    # the leading slash is stripped: `/rooted.txt` names one file in the root,
    # `rooted.txt` names that name at any depth. Stripping first and asking
    # afterwards made every anchored rule a basename rule.
    anchored = pat.startswith("/") or "/" in pat.rstrip("/")
    if not anchored:
        import fnmatch as _fn
        # A slash-free pattern matches any component: a hit on a component that
        # is not the last one is a hit on a DIRECTORY, and a directory takes its
        # contents with it, which is what `private` now does to private/k.txt.
        return any(_fn.fnmatchcase(seg, core) for seg in segs)
    return _isolate_seg_match(segs, core.split("/"))


def _isolate_seg_match(segs: list, pats: list) -> bool:
    """Segment-wise match of an anchored pattern: `*` stops at `/`, `**` spans it."""
    import fnmatch as _fn
    if not pats:
        # The pattern is consumed: it named this path exactly, or it named a
        # directory - path components left below it prove the thing it matched
        # IS a directory - and then everything under it goes too. Without this
        # `deep/private` took the file `deep/private` and left its contents.
        return True
    if pats[0] == "**":
        return any(_isolate_seg_match(segs[i:], pats[1:])
                   for i in range(len(segs) + 1))
    if not segs:
        return False
    if not _fn.fnmatchcase(segs[0], pats[0]):
        return False
    return _isolate_seg_match(segs[1:], pats[1:])


def _isolate_cone_allowed(dirs: list, parents: list, head_files: list) -> set:
    """Cone semantics, reconstructed from the pattern list: every top-level
    file, each listed directory in full, and the files directly inside each
    parent of a listed directory (`git sparse-checkout set keep` writes `/*`,
    `!/*/`, `/keep/`, and for a nested dir also `/nested/`, `!/nested/*/`).

    This is the fallback for a source whose index cannot be asked (a bare or
    restored checkout); the primary answer is git's own - see
    `_isolate_sparse_allowed`. Read as plain ignore patterns the cone list
    allowed NOTHING, the sandbox materialised no file, died on "nothing to
    commit" and left a bare `.git` behind (I6).
    """
    listed = {d.strip("/") for d in dirs if d.strip("/")}
    # A cone writes `!/X/*/` for every directory X it only has to CROSS to reach
    # a listed directory: X's own files are kept, X's subdirectories are not
    # unless one is listed. Read as a plain recursive include, `/nested/` took
    # `nested/deep/x.txt` with it.
    crossed = {p.strip("/") for p in parents if p.strip("/").endswith("/*")}
    crossed = {c[:-2].rstrip("/") for c in crossed}
    recursive = {d for d in listed if d != "*" and d not in crossed}
    ancestors = set()
    for d in recursive:
        segs = d.split("/")
        for i in range(1, len(segs)):
            ancestors.add("/".join(segs[:i]))
    shallow = crossed | ancestors
    allowed = set()
    for rel in head_files:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if not parent or parent in shallow:
            # a top-level file, or a file lying directly inside a directory the
            # cone only passes through.
            allowed.add(rel)
            continue
        if any(parent == d or parent.startswith(d + "/") for d in recursive):
            allowed.add(rel)
    return allowed


def _isolate_sparse_allowed(root: str, head_files: list) -> set | None:
    """The HEAD files the source's own sparse-checkout allows, or None.

    None means the source is not sparse, so every non-excluded HEAD file is
    allowed. A sparse source lists only its checked-out subset with
    `core.sparseCheckout` true; honour it, so the sandbox never materialises
    what the source itself hides.

    git has already answered this question - it is written into the index as
    the skip-worktree bit on every entry - so `git ls-files -v` is asked
    first: an UPPERCASE status letter means the path is in the checkout, a
    lowercase one that it is skipped. That is exact for cone AND non-cone
    mode, where reimplementing the pattern language is not (I6). The pattern
    reconstruction is the fallback for a checkout whose index says nothing.
    """
    proc = subprocess.run(["git", "-C", root, "config", "--bool",
                           "core.sparseCheckout"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.stdout.strip() != "true":
        return None
    head_set = set(head_files)
    verbose = subprocess.run(["git", "-C", root, "ls-files", "-v"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
    wanted = set()
    seen_any = False
    for line in verbose.stdout.splitlines():
        flag, _, path = line.partition(" ")
        if not flag or len(flag) != 1:
            continue
        seen_any = True
        # 'S' is git's own skip-worktree mark: the entry is HEAD's but
        # deliberately NOT in the checkout. Every other letter (H, or a
        # lowercase one, which only adds assume-unchanged) means the source
        # keeps the file.
        if path in head_set and flag not in ("S", "s"):
            wanted.add(path)
    if seen_any:
        return wanted
    # No index to ask: read the pattern file and reconstruct the cone.
    gitdir = subprocess.run(["git", "-C", root, "rev-parse", "--git-dir"],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    gd = (gitdir.stdout.strip() or ".git")
    if not os.path.isabs(gd):
        gd = os.path.join(root, gd)
    try:
        with io.open(os.path.join(gd, "info", "sparse-checkout"),
                     encoding="utf-8") as fh:
            patterns = [ln.strip() for ln in fh.read().splitlines()
                        if ln.strip() and not ln.strip().startswith("#")]
    except OSError:
        return None
    if not patterns:
        return None
    return _isolate_cone_allowed([p for p in patterns if not p.startswith("!")],
                                 [p[1:] for p in patterns if p.startswith("!")],
                                 head_files)


def _isolate_sparse_keeps_deleted(root: str, deleted) -> set | None:
    """G2: HEAD's index answers nothing for a path DELETED at HEAD, so a sparse
    source dropped EVERY deletion from the patch; the pattern-level matcher —
    the same `_isolate_cone_allowed` over the pattern file
    `_isolate_sparse_allowed`'s fallback reads — decides instead. None when the
    source is not sparse. P1-FIX7: cone admits EVERY top-level path, so a
    NON-cone source leaked its sparse-hidden top-level DELETION as a `-` hunk;
    an unreproducible pattern matcher must not decide what leaks — on a sparse
    source, non-cone or unknown drops ALL deletions (documented omission)."""
    cfg = subprocess.run(["git", "-C", root, "config", "--bool", "core.sparseCheckout"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if cfg.stdout.strip() != "true":
        return None
    cone = subprocess.run(["git", "-C", root, "config", "--bool",
                           "core.sparseCheckoutCone"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if cone.stdout.strip() != "true":
        return set()
    gd = subprocess.run(["git", "-C", root, "rev-parse", "--git-dir"],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    try:
        with io.open(os.path.join(gd if os.path.isabs(gd) else os.path.join(root, gd),
                                  "info", "sparse-checkout"), encoding="utf-8") as fh:
            patterns = [ln.strip() for ln in fh.read().splitlines()
                        if ln.strip() and not ln.strip().startswith("#")]
    except OSError:
        return set()
    return _isolate_cone_allowed([p for p in patterns if not p.startswith("!")],
                                 [p[1:] for p in patterns if p.startswith("!")],
                                 deleted) if patterns else set()


def _isolate_path_excluded(rel: str, agentignore: list) -> bool:
    """Whether a HEAD path stays out of the sandbox.

    I10: the generated-secrets directory was excluded only as a TOP-level name,
    so `sub/secrets-generated/` and `Secrets-Generated/` were kept. A path
    component equal to it, case-insensitively, at any depth is excluded - the
    same directory by another spelling.
    """
    posix = rel.replace(os.sep, "/")
    if any(seg.lower() == ISOLATE_EXCLUDED_TOPDIR for seg in posix.split("/")):
        return True
    return any(_isolate_pattern_matches(rel, p) for p in agentignore)


def _isolate_allowed_files(root: str) -> tuple:
    """`(source_sha, allowed, agentignore)`: HEAD files minus every exclusion."""
    source_sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                                capture_output=True, text=True,
                                check=True, stdin=subprocess.DEVNULL).stdout.strip()
    proc = subprocess.run(["git", "-C", root, "ls-tree", "-r", "--name-only",
                           "-z", "HEAD"], capture_output=True, text=True,
                          check=True, stdin=subprocess.DEVNULL)
    head_files = [p for p in proc.stdout.split("\0") if p]
    agentignore = _isolate_agentignore_patterns(root)
    sparse = _isolate_sparse_allowed(root, head_files)
    allowed = [p for p in head_files
               if not _isolate_path_excluded(p, agentignore)
               and (sparse is None or p in sparse)]
    return source_sha, allowed, agentignore


def isolate_preflight_refuse(root: str) -> None:
    """Raise PrivacyRefused when the source HEAD tracks a plaintext secret.

    Only files outside the excluded dirs are examined, and only blobs whose
    name looks secret-shaped are read - as BYTES, never the worktree (I5).
    SOPS-encrypted blobs pass by `_isolate_blob_encrypted`'s rules, templates
    by `_isolate_exempt_name`. The message names PATHS only, never contents.
    """
    source_sha, allowed, agentignore = _isolate_allowed_files(root)
    del agentignore
    bad = []
    for rel in sorted(set(allowed)):
        if not _isolate_secret_name(rel):
            continue
        blob = subprocess.run(["git", "-C", root, "cat-file", "-p",
                               "HEAD:" + rel], capture_output=True, stdin=subprocess.DEVNULL)
        if blob.returncode != 0:
            continue
        if not _isolate_blob_plain(blob.stdout):
            continue
        bad.append(rel)
    if bad:
        raise PrivacyRefused(
            "refusing sandbox: source HEAD tracks plaintext secret "
            "file(s): %s (encrypt with SOPS or list them in .agentignore; "
            "nothing was cloned)" % ", ".join(bad))


def review_base_preflight_refuse(root: str, pair, allowed) -> None:
    """Raise PrivacyRefused when the BASE side of --review-base tracks a plaintext
    secret on a path the patch would carry (F2).

    `isolate_preflight_refuse` reads HEAD only, but a `-` line of REVIEW-DIFF.patch
    IS base content: a secret committed at base and re-encrypted (or edited away)
    at head passes that preflight yet rides out as a removal hunk. Same bytes-only
    rule as the head preflight (`_isolate_blob_plain` on the BASE blob, paths the
    sandbox allows - the rest never enters the patch), and the same verdict it
    gives: REFUSE, not a silently narrowed patch. The message names PATHS only.
    """
    bad = []
    for rel in _review_diff_paths(root, pair, allowed):
        if not _isolate_secret_name(rel):
            continue
        # `rev:path` is a tree lookup, not a pathspec: a name like `*` resolves
        # to the one file called `*` and never globs the tree (P1-FIX2 checked).
        blob = subprocess.run(["git", "-C", root, "cat-file", "-p",
                               pair[0] + ":" + rel], capture_output=True,
                              stdin=subprocess.DEVNULL)
        if blob.returncode != 0:
            continue
        if not _isolate_blob_plain(blob.stdout):
            continue
        bad.append(rel)
    if bad:
        raise PrivacyRefused(
            "refusing sandbox: review-base %s tracks plaintext secret file(s): "
            "%s on the base side (the patch would carry them as removal lines; "
            "list them in .agentignore; nothing was cloned)"
            % (pair[0][:8], ", ".join(bad)))


def sandbox_repo_slug(source: str) -> str:
    """Filesystem/branch-safe basename of the source repo (S4)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-",
                  os.path.basename(os.path.abspath(source))).strip("-")
    return slug or "repo"


def _isolate_batch_entries(root: str, sha: str, allowed) -> list:
    """HEAD's `(mode, blob_sha, path)` triples for every allowed path.

    `ls-tree -r -z --full-tree` names the whole tree and the allow filter is
    then applied to names we already hold (I1). Handed to `git archive` those
    same names are PATHSPECS, which git interprets: a tracked file literally
    named `*`, `secrets-*` or `:(glob)**` is a glob that pulls every tracked
    path - `secrets-generated/` included - past the filter that just dropped
    it. A gitlink (160000) is skipped: the submodule's objects are not here and
    an empty directory carries nothing.
    """
    listing = subprocess.run(
        ["git", "-C", root, "ls-tree", "-r", "-z", "--full-tree", sha],
        capture_output=True, check=True, stdin=subprocess.DEVNULL)
    wanted = set(allowed)
    entries = []
    for rec in listing.stdout.split(b"\0"):
        if not rec:
            continue
        meta, sep, name = rec.partition(b"\t")
        if not sep:
            continue
        fields = meta.decode("utf-8", "surrogateescape").split(" ", 2)
        if len(fields) != 3:
            continue
        path = name.decode("utf-8", "surrogateescape")
        if path in wanted and fields[0] in ("100644", "100755", "120000"):
            entries.append((fields[0], fields[2], path))
    return entries


def _isolate_batch_blobs(root: str, entries: list) -> dict:
    """Every requested blob's bytes, from ONE `git cat-file --batch` call.

    The ids go in on stdin and stdin is closed before the stream is read: an
    interleaved write/read on the same pipe deadlocks, because `--batch`
    buffers its output until the process ends. One call, no path in the argv
    (I2 - ~30k paths overflowed ARG_MAX), and the objects are addressed by id,
    never by name (I1).
    """
    if not entries:
        return {}
    ids = "\n".join(sha for _m, sha, _p in entries) + "\n"
    proc = subprocess.run(["git", "-C", root, "cat-file", "--batch"],
                          input=ids.encode("utf-8"), capture_output=True)
    if proc.returncode != 0:
        raise IOError("git cat-file --batch failed: %s"
                      % proc.stderr.decode("utf-8", "replace").strip()[:200])
    out = proc.stdout
    blobs = {}
    pos = 0
    for _mode, sha, _path in entries:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        fields = out[pos:nl].decode("utf-8", "replace").split(" ")
        pos = nl + 1
        if len(fields) < 3 or "missing" in fields:
            continue
        size = int(fields[-1])
        blobs[sha] = out[pos:pos + size]
        pos += size + 1                       # skip the record's newline
    return blobs


def _isolate_safe_path(rel: str) -> bool:
    """Whether `rel` may be written under the sandbox destination (R1).

    A tracked NAME is trusted by nothing else: git tree objects can and do hold
    `..`, an empty or `.` component, a leading `/`, a NUL, and a `.git`
    directory — every one of them is a write outside the sandbox or into its own
    repo metadata (`sub/.GIT/config` is the same directory, another spelling). A
    NUL truncates the path in any later C caller. A backslash-shaped name
    (drive or UNC) is refused whole: the sandbox is a POSIX tree, and
    `os.path.join(dest, "C:\\x")` abandons `dest` for the drive. Conservative
    costs one skipped file on the rare checkout; the alternative is a sandbox
    whose contents are not HEAD.
    """
    if not rel or "\x00" in rel or "\\" in rel:
        return False
    posix = rel.replace(os.sep, "/")
    if posix.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", posix) or posix.startswith("?:"):
        return False
    for seg in posix.split("/"):
        if seg in ("", ".", "..") or seg.lower() == ".git":
            return False
    return True


def _isolate_link_inside(path: str, target: str) -> bool:
    """Whether a tracked symlink's own target stays inside the sandbox (R1b).

    `path` is the POSIX relative name and `target` the blob's bytes. An absolute
    or drive/UNC target, or one that climbs out of the tree
    (`normpath(dirname(path) + target)` starting with `..`), materialises a link
    the worker can read straight through into a host file — the source's own
    untracked `configuration/api-keys.yml` is exactly such a target. Refused:
    the link is simply not created.

    R8 names the three shapes that are not a target at all — an empty blob, a
    backslash-only one, and a NUL anywhere in it. A NUL makes `os.symlink`
    raise ValueError, which is no OSError and so the write loop does not catch
    it: the whole clone dies for one entry. Refuse it HERE, because the refusal
    line quotes the path and never this string.
    """
    if (not target or "\x00" in target or "\\" in target
            or os.path.isabs(target)):
        return False
    posix = target.replace(os.sep, "/")
    if posix.startswith("/") or re.match(r"^[A-Za-z]:", posix):
        return False
    rel = os.path.normpath(posixpath.join(
        posixpath.dirname(path.replace(os.sep, "/")), posix))
    return rel != ".." and not rel.startswith("../") and rel != "."


def _isolate_entry_refusal(path: str, mode: str, target) -> "str | None":
    """Why `_isolate_materialise` would skip this entry, or None when it would
    write it. ONE predicate shared with the review-diff filter (G1) so the two
    cannot drift — and the refusal strings stay what materialise prints."""
    if not _isolate_safe_path(path):
        return "unsafe path"
    if mode == "120000" and not _isolate_link_inside(path, target):
        return "symlink target escapes the sandbox"
    return None


def _isolate_clear_below(dest: str, rel: str) -> bool:
    """Whether `rel` can be written under `dest` without leaving it (R1c).

    Refuses when any ancestor of the target below `dest` is a symlink — the
    `a` -> /tmp/sibling entry followed by `a/x` writes OUTSIDE dest — and when
    realpath of the parent is not inside realpath(dest) (the belt after the
    component walk: it also catches a dest that a link pointed into).
    """
    real_dest = os.path.realpath(dest)
    cursor = dest
    for seg in rel.replace(os.sep, "/").split("/")[:-1]:
        cursor = os.path.join(cursor, seg)
        if os.path.islink(cursor):
            return False
    parent = os.path.dirname(os.path.join(dest, rel.replace("/", os.sep)))
    real_parent = os.path.realpath(parent) if parent else real_dest
    return real_parent == real_dest or \
        real_parent.startswith(real_dest + os.sep)


def _isolate_refuse_path(path: str, why: str) -> None:
    """One stderr line naming the PATH only — never its content (R1)."""
    sys.stderr.write("autoos: sandbox refused tracked path %s (%s)\n"
                     % (repr(path), why))


# Refusal reasons are CONSTANTS: the line is echoed to the operator's terminal
# and to any log that reads it, so it must never carry a byte of the offending
# entry (R8 — an OSError for a NUL-containing target quotes that target back).
_REFUSE_FINAL_LINK = "a link or non-file already sits at this path"
_REFUSE_DUPLICATE = "an earlier entry already wrote this path"
_REFUSE_DISK_STATE = "the destination already holds something else here"
_REFUSE_LINK = "could not create the link"
_REFUSE_OPEN = "could not open for writing"


def _isolate_materialise(root: str, entries: list, dest: str) -> None:
    """Write the allowed HEAD entries into `dest`, verbatim from the object DB.

    WHY this replaced `git archive HEAD -- <paths>` (the design change the
    cross-family review asked for; I1 + I2 + I3):
      * I1 - the path list is read as PATHSPECS, so one tracked file named `*`
        turns the whole allow list into a glob and re-imports the secrets the
        filter had just dropped;
      * I2 - ~30k paths do not fit in an argv: the child dies OSError
        "Argument list too long" and leaves a half-made directory;
      * I3 - `git archive` applies .gitattributes export-ignore (the file
        vanishes) and export-subst (`$Format:%H$` is rewritten), so the sandbox
        is not HEAD.
    Blobs come from one `cat-file --batch` addressed by OBJECT ID, so a path
    never reaches an argv and no name is ever pattern-matched; HEAD arrives
    verbatim, a symlink is created rather than followed, and the exec bit is
    carried. A gitlink has no bytes here and is skipped.

    A NAME is the one thing that is NOT trusted (R1): the tree object is the
    worker's/source's own data, and `ls-tree` happily prints `../x`, `//x`,
    `/abs/x`, `.git/hooks/pre-commit` and a symlink whose target is
    `/home/user/.ssh/id_ed25519`. `_isolate_safe_path` vetts the shape,
    `_isolate_link_inside` vetts where a link points, and
    `_isolate_clear_below` vetts the destination itself (no symlinked ancestor,
    parent still under `dest`). An unsafe entry is skipped with one stderr line
    naming the PATH only; a sandbox missing one weird file is still HEAD for
    every path the filter allowed, while the alternative is bytes outside it.
    """
    blobs = _isolate_batch_blobs(root, entries)
    written: set = set()
    for mode, sha, path in entries:
        data = blobs.get(sha)
        if data is None:
            continue
        target = data.decode("utf-8", "surrogateescape")
        refusal = _isolate_entry_refusal(path, mode, target)
        if refusal:
            _isolate_refuse_path(path, refusal)
            continue
        if not _isolate_clear_below(dest, path):
            _isolate_refuse_path(path, "an ancestor is a symlink or outside")
            continue
        full = os.path.join(dest, path.replace("/", os.sep))
        # R6: refuse a FINAL component that is already a link (or anything but
        # a regular file) before opening it, and refuse a path two entries
        # claim (the first one wins). `_isolate_clear_below` vets ancestors
        # only, so `evil -> ../outside` passes every earlier check; O_NOFOLLOW
        # below is the second layer and is 0 where the attribute does not exist
        # (Windows), which is exactly the platform that writes through.
        if path in written:
            _isolate_refuse_path(path, _REFUSE_DUPLICATE)
            continue
        if mode != "120000" and (
                os.path.islink(full)
                or (os.path.lexists(full) and not os.path.isfile(full))):
            _isolate_refuse_path(path, _REFUSE_FINAL_LINK)
            continue
        parent = os.path.dirname(full)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            if mode == "120000":
                if os.path.lexists(full):
                    os.unlink(full)
        except OSError:
            # R7: a tracked name that collides with what an earlier entry put
            # on disk (`a/x` then a symlink `a`, or a file `p` then `p/q`) is
            # one skipped entry, not an aborted clone.
            _isolate_refuse_path(path, _REFUSE_DISK_STATE)
            continue
        if mode == "120000":
            # Never follow the link: create it, pointing where HEAD says.
            try:
                os.symlink(target, full)
            except OSError:
                _isolate_refuse_path(path, _REFUSE_LINK)
                continue
            written.add(path)
            continue
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        # O_NOFOLLOW so a final-component link is refused, not written through.
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(full, flags, 0o600)
        except OSError:
            _isolate_refuse_path(path, _REFUSE_OPEN)
            continue
        with io.open(fd, "wb", closefd=True) as fh:
            fh.write(data)
        os.chmod(full, 0o755 if mode == "100755" else 0o644)
        written.add(path)


def is_autoos_source(source: str) -> bool:
    """Whether an --isolate source is this AutoOS checkout (S4).

    Compared by abspath, so any OTHER AutoOS worktree counts as foreign and
    gets the fleet root - intended: it is a different checkout, not this one.
    """
    try:
        return os.path.abspath(source) == os.path.abspath(ROOT)
    except OSError:
        return False


def sandbox_path_for(source: str, run_id: str) -> str:
    """Where a sandbox for `source` lives (S4).

    AutoOS's own cards keep the git-ignored `logs/sandboxes/` tree. A card
    whose cwd repo is anywhere else gets `~/fleet/sandboxes/<repo>/` outside
    the AutoOS tree, so a foreign checkout's files never land inside it.
    """
    slug = sandbox_repo_slug(source)
    name = "%s-%s" % (slug if not is_autoos_source(source)
                       else os.path.basename(ROOT), run_id)
    if is_autoos_source(source):
        return os.path.join(clients.state_dir(), "sandboxes", name)
    return os.path.join(os.path.expanduser("~"), "fleet", "sandboxes",
                        slug, name)


def sandbox_branch_for(source: str, run_id: str) -> str:
    """The sandbox branch for `source` (S4): it names the target repo."""
    if is_autoos_source(source):
        return "agent/%s" % run_id
    return "%s/%s" % (sandbox_repo_slug(source), run_id)


def _isolate_build(root: str, path: str, source_sha: str, allowed: list, review_base=None) -> None:
    """Materialise the allowed HEAD entries and commit them as the base sha.

    Runs inside isolate_clone's cleanup, so a raise at any step of it leaves no
    sandbox directory at all (I11). A `review_base` pair rides base..head in too.
    """
    subprocess.run(["git", "init", "-q", path], check=True, stdin=subprocess.DEVNULL)
    _isolate_materialise(root,
                         _isolate_batch_entries(root, source_sha, allowed), path)
    subprocess.run(["git", "-C", path, "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    if review_base is not None:
        write_review_diff(root, path, review_base, allowed)
        subprocess.run(["git", "-C", path, "add", "-f", "--", REVIEW_DIFF_FILE], check=True,
                       stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", path, "-c", "user.name=autoos-worker",
                    "-c", "user.email=" + WORKER_EMAIL, "commit", "-q", "-m",
                    "sandbox base (source %s)" % source_sha], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", path, "config", "--local",
                    "autoos.sandboxSource", source_sha], check=True, stdin=subprocess.DEVNULL)


def sandbox_root_prepare(source: str, path: str) -> None:
    """Refuse first, then create the sandbox's parent (I12, S4).

    cmd_run made `~/fleet/sandboxes/<repo>/` and chmodded the chain BEFORE the
    plaintext-secret refusal, so a card that was denied still created owner-only
    directories in the operator's home. A denied card must leave nothing.
    """
    isolate_preflight_refuse(source)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if is_autoos_source(source):
        return
    # S4: a foreign repo's sandbox lives under ~/fleet/sandboxes with
    # owner-only access.
    _fleet = os.path.join(os.path.expanduser("~"), "fleet", "sandboxes")
    _p = path
    while _p.startswith(_fleet + os.sep) or _p == _fleet:
        try:
            os.chmod(_p, 0o700)
        except OSError:
            pass
        if _p == _fleet:
            break
        _p = os.path.dirname(_p)


def take_it_hint(sandbox_path: str, branch: str, base_sha: str = "") -> str:
    """The line that tells the orchestrator how to take the worker's work.

    I13: the old text ended `(then review FETCH_HEAD)` on a base that shares NO
    history with the source repo, so the commands it implied are wrong -
    `git merge FETCH_HEAD` dies without --allow-unrelated-histories, and
    `git diff HEAD FETCH_HEAD` reports every path the sandbox never materialised
    as a deletion. The work is the RANGE above the base commit, so the hint
    prints the cherry-pick form and the base sha that range needs.
    """
    line = "take it: git fetch %s %s" % (sandbox_path, branch)
    if base_sha:
        line += "   (then: git cherry-pick %s..FETCH_HEAD)" % base_sha
    return line


# AO-L2-SEAT-INTEGRITY P1 (measured, greatwiki): the --isolate seat is ONE commit, so
# `git diff A B` inside it names nothing; `run --review-base` rides that diff in as this.
REVIEW_DIFF_FILE = "REVIEW-DIFF.patch"


class ReviewBaseRefused(ValueError):
    """--review-base named no commit the seat can diff from."""


def resolve_review_base(root: str, base):
    """`(base_sha, head_sha)` full shas, or None when `base` names no commit in the
    parent - the seat's snapshot IS the parent's HEAD, so the parent names it."""
    if not str(base or "").strip() or str(base).startswith("-"):
        return None
    proc = subprocess.run(["git", "-C", root, "rev-parse", "%s^{commit}" % base,
                           "HEAD^{commit}"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    return tuple(proc.stdout.split()) if proc.returncode == 0 else None


def _patch_side_accepted(root: str, sha: str, paths) -> tuple:
    """G1: `(present, accepted)` for side `sha` — accepted means the
    materialiser's OWN entry list (`_isolate_batch_entries`) passes its OWN
    predicate (`_isolate_entry_refusal`), so patch and seat cannot drift. Only
    symlink blobs are read: the refused target string itself is the leak."""
    entries = _isolate_batch_entries(root, sha, paths)
    blobs = _isolate_batch_blobs(root, [e for e in entries if e[0] == "120000"])
    # P1-FIX6 H2: the PATH predicate is not symlink-only - an unsafe regular name rode the patch.
    accepted = {path for mode, blob, path in entries if _isolate_entry_refusal(
        path, mode, (blobs.get(blob) or b"\0").decode("utf-8", "surrogateescape")
        if mode == "120000" else "") is None}
    return {e[2] for e in entries}, accepted


def _review_expansion_refuse(root: str, pair, kept) -> None:
    """P1-FIX5 (attacker-reproduced): a LITERAL pathspec still matches as a DIRECTORY
    PREFIX, so when `foo` is a tree at base (holding the excluded
    `foo/secrets-generated/key.pem`) and a file at head, `kept` names `foo` and the
    diff carries that child out as a removal hunk. Upstream predicates are path-level
    and cannot see a name the pathspec expands to, so ask git what the same pathspec
    covers and refuse on any name the filter dropped — before the patch is opened,
    naming the first offending PATH only, never its content."""
    rng = "%s..%s" % pair
    kept_set = set(kept)
    for i in range(0, len(kept), 200):   # bounded argv: the ARG_MAX cliff
        try:
            # subprocess-audit: git plumbing again; only its pathspec chunk is dynamic
            out = subprocess.run(["git", "-C", root, "--literal-pathspecs", "diff",
                                  "--no-renames", "--name-only", "-z", rng, "--"]
                                 + kept[i:i + 200], capture_output=True, check=True,
                                 stdin=subprocess.DEVNULL).stdout
        except subprocess.CalledProcessError as exc:  # D3: vanished base = rc2 refusal
            raise ReviewBaseRefused("review-base: %s vanished in %s (`git diff` rc %s)"
                                    % (pair[0][:8], root, exc.returncode)) from None
        for name in out.split(b"\0"):
            name = name.decode("utf-8", "surrogateescape")
            if name and name not in kept_set:
                raise ReviewBaseRefused("review-base: the patch pathspec expands to %s, "
                                        "which the exclude filter dropped; refusing to "
                                        "build it" % name)


def _review_diff_paths(root: str, pair, allowed) -> list:
    """The diff names that ride out: the allowed HEAD paths PLUS paths DELETED in
    the range (D1: `allowed` is HEAD-only, so removals were dropped and the seat -
    HEAD plus this patch - never saw them); deletions pass the same path-level
    predicates as HEAD names, re-read here. `--no-renames`: a rename must not hide
    a deletion end."""
    def names(*extra):
        try:
            # subprocess-audit: git plumbing; only the two resolved shas are dynamic
            out = subprocess.run(["git", "-C", root, "diff", "--no-renames", "--name-only", "-z"]
                                 + list(extra) + ["%s..%s" % pair], capture_output=True,
                                 check=True, stdin=subprocess.DEVNULL).stdout
        except subprocess.CalledProcessError as exc:  # D3: vanished base = rc2 refusal
            raise ReviewBaseRefused("review-base: %s vanished in %s (`git diff` rc %s)"
                                    % (pair[0][:8], root, exc.returncode)) from None
        return {n.decode("utf-8", "surrogateescape") for n in out.split(b"\0") if n}
    deleted, ignore = names("--diff-filter=D"), _isolate_agentignore_patterns(root)
    sparse = _isolate_sparse_keeps_deleted(root, deleted)  # G2: not HEAD's index
    kept = sorted((names() & set(allowed)) | {p for p in deleted
               if not _isolate_path_excluded(p, ignore)
               and (sparse is None or p in sparse)})
    # G1: drop (not refuse) paths whose own side's entry the materialiser would
    # skip — HEAD for adds/mods, BASE for deletions; a type change leaks on
    # neither side.
    pres_h, acc_h = _patch_side_accepted(root, pair[1], kept)
    pres_b, acc_b = _patch_side_accepted(root, pair[0], kept)
    kept = [p for p in kept if (p not in pres_h or p in acc_h)
            and (p not in pres_b or p in acc_b)]
    _review_expansion_refuse(root, pair, kept)  # P1-FIX5 backstop
    return kept


def write_review_diff(root: str, path: str, pair, allowed) -> None:
    """Ride base..head into the sandbox as REVIEW-DIFF.patch, keeping only the paths
    the seat materialises (S2: a patch with a plaintext secret in it is itself the
    leak). F1: the exclude list is NOT the allowed set - a sparse-hidden path is
    neither excluded nor allowed, and filtering on the exclude list alone carried
    its full base..head content out. `allowed` is the same set one-commit
    materialisation uses."""
    rng = "%s..%s" % pair
    kept = _review_diff_paths(root, pair, allowed)
    # D2: the SOURCE may track this very name, and io.open("wb") writes THROUGH
    # such a symlink: unlink, then create O_EXCL|O_NOFOLLOW - never write through.
    target = os.path.join(path, REVIEW_DIFF_FILE)
    if os.path.isdir(target) and not os.path.islink(target):
        # G3: the source tracks `REVIEW-DIFF.patch/x`; materialise built it and
        # os.remove() raised IsADirectoryError past cmd_run. Refusing (rc2) is
        # the smaller, fail-closed choice over deleting the seat's own tree.
        raise ReviewBaseRefused("review-base: source tracks a directory named "
                                "%s; the patch cannot ride" % REVIEW_DIFF_FILE)
    if os.path.lexists(target):
        os.remove(target)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with io.open(os.open(target, flags, 0o644), "wb") as fh:
        for i in range(0, len(kept), 200):   # bounded argv: the ARG_MAX cliff
            # P1-FIX2 (I1 again, cf. _isolate_batch_entries): a kept NAME may be
            # `*`, `secrets-*` or `:(glob)**`, and an INTERPRETED pathspec globs it
            # over the whole range - pulling paths the filter just dropped, secrets
            # included, back into the patch. Names are literal, always.
            # P1-FIX6 H1 (D3, unmapped): a vanished base/head BLOB — the name-only
            # passes read TREES and survive it, this needs the bytes: the siblings' rc2.
            try:
                # subprocess-audit: git plumbing again; only its pathspec chunk is dynamic
                subprocess.run(["git", "-C", root, "-c", "core.quotepath=false",
                                "--literal-pathspecs", "diff", "--no-renames",
                                rng, "--"] + kept[i:i + 200],
                               stdout=fh, check=True, stdin=subprocess.DEVNULL)
            except subprocess.CalledProcessError as exc:
                raise ReviewBaseRefused("review-base: %s vanished in %s (`git diff` rc %s)"
                                        % (pair[0][:8], root, exc.returncode)) from None


def isolate_clone(root: str, path: str, branch: str, review_base=None) -> str:
    """Create the --isolate sandbox and return its base sha.

    DESIGN (T2-ISOLATE-SECRETS S2, revised after the cross-family review) -
    WHY HEAD is materialised in Python into a fresh repo instead of cloned: a
    `git clone` copies the source's object DB, so HEAD blobs of excluded paths
    (`secrets-generated/`, `.agentignore` entries, sparse-hidden files) would
    still sit inside the sandbox's `.git` even when the worktree hides them, and
    the full history keeps every old plaintext blob reachable via
    `git show <old sha>:path`. So `ls-tree` names the allowed entries, ONE
    `cat-file --batch` streams their bytes (`_isolate_materialise`; deliberately
    NOT `git archive`, whose pathspec, ARG_MAX and .gitattributes behaviour are
    the three defects the review reproduced), and a fresh `git init` takes
    exactly one `sandbox base (source <sha>)` commit. That carries no history
    (S1: `rev-list --all` is 1), no excluded blob anywhere including `.git`,
    shares no objects with the source (`--no-hardlinks` is moot: there is no
    clone at all) and cannot inherit the source's refs. The source sha rides in
    the commit message and in `autoos.sandboxSource`, so provenance survives;
    the orchestrator fetches `git fetch <path> <branch>` and cherry-picks the
    range above the base (`take_it_hint`), and the sandbox_* diff helpers keep
    working with `base` = this single commit.

    The steps are one function because the containment claim is about the
    directory the worker lands in: the sandbox holds committed allowed files
    only, so nothing git-ignored and nothing untracked exists in it (KEYDENY3b),
    and the push fence plus the credential-free env make a push out of it an
    accident guard, not a boundary (FF1, D-106).

    ISOLATION STATEMENT (accepted by canary — tests/test_autoos_spawner.py
    T2IsolateSecretsS5CanaryTests: an untracked, git-ignored
    `configuration/api-keys.yml` holding one unique fake string, in a plain
    checkout AND in a `git worktree` of it):

    ISOLATED — the sandbox tree *and its own `.git`*, for every worker and every
    reviewer launched with --isolate. A reviewer gets a sandbox built from its
    material worktree's committed HEAD, never the worktree itself, whose
    `--git-common-dir` is the main checkout's `.git`: that is how an untracked
    secret outside the sandbox is reachable from inside it, and the reason the
    sandbox is a fresh `git init` with one materialised commit.

    NOT ISOLATED —
      (i) a client process that reads an ABSOLUTE path outside the sandbox. The
          outside-path fence exists for opencode only; every other client gets
          the containment prompt line, the post-run leak check (exit 7) and the
          credential-free env, which are guards, not a boundary;
      (ii) in-session subagents of a Claude Code session — they run in the
          caller's checkout, which is not a sandbox;
      (iii) a run launched WITHOUT --isolate: its cwd is the caller's checkout.
    """
    if os.path.lexists(path):
        raise FileExistsError("sandbox destination already exists: %s" % path)
    isolate_preflight_refuse(root)
    source_sha, allowed, _ = _isolate_allowed_files(root)
    if review_base is not None:
        if review_base[1] != source_sha:
            raise ReviewBaseRefused("review-base: parent HEAD moved %s -> %s, re-run"
                                    % (review_base[1], source_sha))
        # F2: the head preflight cannot see a secret that lives only on the
        # base side of the range the patch is about to carry.
        review_base_preflight_refuse(root, review_base, allowed)
    os.makedirs(path, exist_ok=True)
    try:
        _isolate_build(root, path, source_sha, allowed, review_base)
        # The orchestrator still fetches from the sandbox path (unchanged); every
        # remote's push URL is disabled and a pre-push hook is installed, so an
        # unplanned `git push` - to origin or to the parent's absolute path the
        # containment brief names - fails. ACCIDENT GUARD, not containment: see
        # fence_sandbox_push. The credentials it cannot use are what really
        # keeps the parent safe (worker_env, FF1b).
        fence_sandbox_push(path)
        subprocess.run(["git", "-C", path, "switch", "-q", "-c", branch],
                       check=True, stdin=subprocess.DEVNULL)
        # FF1c: the clone gets its own identity, local to itself. The worker's
        # git no longer reads any global config (GIT_CONFIG_GLOBAL is a dead
        # path), so the operator's `user.name` is gone - and a worker that ends
        # its brief with `git commit` would die on "Author identity unknown",
        # leaving the run's work uncommitted. Same author the spawner's own
        # end-of-run commit signs with.
        for name, value in (("user.name", "autoos-worker"),
                            ("user.email", WORKER_EMAIL)):
            subprocess.run(["git", "-C", path, "config", "--local", name,
                            value], check=True, stdin=subprocess.DEVNULL)
        if not is_autoos_source(root):
            # S4: a foreign repo's sandbox is owner-only, like its fleet parents.
            os.chmod(path, 0o700)
    except BaseException:
        # I11: after the directory exists, every later step is inside this
        # cleanup. A half-made sandbox is still a sandbox a worker can be
        # launched into with excluded paths already written; a failed build
        # leaves no directory, then re-raises.
        if os.path.lexists(path):
            shutil.rmtree(path, ignore_errors=True)
        raise
    return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()

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


# --- the agent layer fence (D-665 AO-L2-LAUNCH; L2SPAWN-TIER fix 1) ----------
#
# A lane marks which layer it runs at in its own environment; the marker is the
# lane's metadata, written by tools/oc_l1_render.py / tools/oc_l2.py, never an
# argument a model can point at a peer layer. Two fences read it back:
#
#   L2  an L2 lane spawns tier-2 and tier-3 workers, never the L1's own seat, and
#       never a Claude spend (`l2_spawn_refusal`).
#   L3  the worker an L2 spawned is a LEAF — stamped here, by the spawner, from
#       the *spawner's* environment, and a leaf never spawns at all (skill rule
#       R-worker-06, `leaf_spawn_refusal`).
#
# Before the stamp existed, the marker stopped at the lane: WORKER_ENV_AUTOOS did
# not name it, so a tier-2 child of an L2 ran the FULL tool profile again and
# could climb back to tier 1 (the default budget is 2, so a grandchild fitted).
# The value a child carries is therefore never inherited, never a plan entry and
# never a caller's `extra` — it is decided by whoever starts the child.
ENV_AGENT_LAYER = "AUTOOS_AGENT_LAYER"
AGENT_LAYER_L2 = "L2"
AGENT_LAYER_LEAF = "L3"
# The tiers a marked L2 lane may start. A SET, not `>= L2_SPAWN_TIER`: an open
# interval read tier 4 and tier 99 as "a worker" and let them through to the
# CLI's argparse, which exits 2 on them labelled as nothing.
L2_SPAWN_TIER = 2
L2_SPAWN_TIERS = (2, 3)


def agent_layer(env=None) -> str:
    """The layer this process runs at, as its environment marks it: "" when
    nothing marked it (an L1 session, a plain operator shell, a worker of an
    L1's). Stripped and upper-cased, so no spelling of a mark evades the fence."""
    source = os.environ if env is None else env
    return str(source.get(ENV_AGENT_LAYER, "")).strip().upper()


def child_agent_layer(env=None) -> str:
    """The layer to stamp the child of a spawn made from `env` with — "" for no
    mark at all. The spawner's own mark, never the child's opinion of it: an L2's
    worker is a leaf, a leaf's child cannot happen, and anything else is an
    ordinary worker that keeps whatever its own caller gave it."""
    layer = agent_layer(env)
    if layer in (AGENT_LAYER_L2, AGENT_LAYER_LEAF):
        return AGENT_LAYER_LEAF
    return ""


def leaf_spawn_refusal(env=None):
    """R-worker-06: why a leaf never spawns, or None when this process may.

    The message says what to do instead, because the leaf that reads it is
    headless: the work it cannot do belongs in its report to whoever started it,
    not in a child of its own.
    """
    if agent_layer(env) != AGENT_LAYER_LEAF:
        return None
    return ("%s=L3: this session is a spawned leaf, marked by the spawner that "
            "started it, and a leaf never spawns (skill rule R-worker-06). Do the "
            "task you were given and put what you could not do in your REPORT — "
            "the caller that needs a deeper tier starts it, that is not yours to "
            "reach down to" % ENV_AGENT_LAYER)


def l2_spawn_refusal(run_tier, card=None, client=None, models=(), env=None):
    """D-665 (AO-L2-LAUNCH criteria 3 and b): the gate a spawn made *for* an L2
    lane passes through; None when this process is not marked L2, or the spawn is
    a legal L2 worker.

    The one source for both entry points — the MCP `spawn` and the CLI `run` — so
    a lane's bash cannot get a different answer from its own MCP server:

    * Claude (criterion b): an L2 lane runs free/credit only. The `claude` CLIENT
      and any Claude MODEL PIN are refused whatever the tier, checked FIRST, so an
      L2 cannot spend the Claude allowance even with a declared reason — the credit
      budget is the L1's to apply, and cross-family review rides with it.
    * Tier (criterion 3): the only thing an L2 may start is a worker, in
      `L2_SPAWN_TIERS`, always in its own `--isolate` clone (the MCP profile forces
      the clone, whatever the caller passed). Tier 1 stays with the L1: it is the
      seat that carries the orchestration combo, and an L2 that could start one
      owns the lane above itself. A `role: orchestrate` card is that same seat
      reached through the resolver instead of through `--tier`, and the role is
      compared normalised — a card that spells it "Orchestrate" is the same claim.
      A tier outside the set (4, 99) is refused, not re-read as a worker.

    The original wording of the tier rule — "below tier 3 is a session that holds
    its own editor and a checkout of the main tree" — was half false: an isolated
    worker holds a clone, so the editor was never the danger, and the fence that
    mattered is the one the tier keeps anyway (tier 1 = the orchestrator's combo).
    It cost the lane its only write path, because tier 3 refuses an implement card
    (T2-RECORD-PIN).

    This is the minimal gate; the full ROLE-GATE (which role may spawn which) is a
    later lane.
    """
    if agent_layer(env) != AGENT_LAYER_L2:
        return None
    if client and resolver.is_claude_client(client):
        return ("l2 lane: the %r client stays with the L1 - an L2 spawns free/"
                "credit tiers only. Report to the L1 inbox and let the L1 run "
                "Claude." % client)
    for pin in models:
        if resolver.claude_model_name(pin):
            return ("l2 lane: pinning a Claude model (%s) is refused - an L2 runs "
                    "free/credit only. Report to the L1 inbox and let the L1 run "
                    "Claude." % pin)
    try:
        tier = int(run_tier)
    except (TypeError, ValueError):
        tier = None
    role = str((card or {}).get("role") or "").strip().casefold()
    if tier in L2_SPAWN_TIERS and role != "orchestrate":
        return None
    why = ("a role=orchestrate card is an L1's own seat"
           if role == "orchestrate" else
           "only tiers %s are workers" % " and ".join(str(t) for t in L2_SPAWN_TIERS))
    return ("l2 lane: an L2 spawns tier-%d and tier-3 workers only, this request "
            "is tier %s and %s. Report the work to the L1 inbox and let the L1 "
            "start the tier." % (L2_SPAWN_TIER, run_tier, why))


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
                     "AUTOOS_AGENT_TRANSCRIPT", "AUTOOS_AGENT_MCP_DRY_RUN",
                     # which layer this process runs at, so a worker that spawns
                     # is fenced by the mark its OWN spawner made (`ENV_AGENT_LAYER`
                     # above; the value is stamped, never copied — see worker_env).
                     "AUTOOS_AGENT_LAYER",
                     # the daily gate file path: the gate itself runs inside
                     # our own CLI, so the MCP server's preflight and detached
                     # runner must hand the CLI the path to the spend report
                     # that decides the refusal. A path to a report, not a
                     # credential, and the client worker it reaches never
                     # reads it.
                     "AUTOOS_DAILY_GATE_FILE")
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
# TOOLHOME (AO-L2-SEAT-INTEGRITY P3a): the cache roots the measured seat writes
# used (a qwen seat ran `ansible-galaxy collection install ansible.posix` and
# filled ~/.ansible, outside its sandbox — HOME and every tool cache are
# inherited). An --isolate worker gets each one repointed into a sandbox-private
# toolhome; the names MUST stay in sync with WORKER_PLAN_ENV_PASSLIST below
# (tests: the passlist and the redirect set cannot drift).
TOOL_HOME_REDIRECTS = ("ANSIBLE_HOME", "ANSIBLE_LOCAL_TEMP", "ANSIBLE_REMOTE_TEMP",
                       "ANSIBLE_COLLECTIONS_PATH", "PIP_CACHE_DIR", "npm_config_cache",
                       "CARGO_HOME", "UV_CACHE_DIR", "XDG_CACHE_HOME",
                       "PYTHONUSERBASE")
# The TWO sandbox-private siblings of a seat clone, named once and read by
# `toolhome_dir`, the plan, the `discard:` line and the leak filter (P3a-FIX).
TOOLHOME_SUFFIX = ".toolhome"
OPENCODE_DATA_SUFFIX = ".opencode-data"
SANDBOX_PRIVATE_SUFFIXES = (TOOLHOME_SUFFIX, OPENCODE_DATA_SUFFIX)
# And what the *plan* is allowed to add, on top of clearing the deny check. The
# allowlist above only ever covered the caller's own exports: plan["env"] was
# copied in behind it, so a builder that set PATH, LD_PRELOAD or PYTHONPATH
# owned the child without anyone noticing (FF1b, Muse#high on 362b8af..6bdeca5).
# This is the list of names the spawner's builders genuinely set; anything else
# is refused and announced, because a name nobody wrote down here is a name
# nobody decided the child should have.
WORKER_PLAN_ENV_PASSLIST = ("OPENCODE_CONFIG_CONTENT", "XDG_DATA_HOME",
                            "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME",
                            clients.GEMINI_CUSTOM_HEADERS_ENV,
                            # D8: the pin that keeps a gemini-cli internal call on
                            # the leg this run routed instead of its `auto` default.
                            clients.GEMINI_MODEL_ENV) + TOOL_HOME_REDIRECTS
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
    # TOOLHOME (P3a), and the HOME split it lives with: own-account clients
    # (claude, qoder, qwen, agy, codex, gemini) authenticate from $HOME, so
    # they inherit the operator's HOME and only the caches named in
    # TOOL_HOME_REDIRECTS move; a gateway client (opencode) needs nothing from
    # HOME, so its builder asks for the toolhome as home with plan["forced_home"].
    # It is honoured only when it is exactly the sandbox's own `.toolhome`
    # sibling — the passlist gate on HOME itself stays the way it was.
    forced_home = plan.get("forced_home")
    if forced_home is not None and forced_home == toolhome_dir(str(plan.get("cwd", ""))):
        env["HOME"] = forced_home
    # SEAT-PYTEST (P4): honoured only when the plan's key is exactly what this
    # spawner recomputes now — a plan cannot write down a path of its own.
    user_site = plan.get("user_site_path")
    if user_site is not None and user_site == seat_user_site_path(src):
        env["PYTHONPATH"] = user_site
    for n, v in WORKER_GIT_GUARDS:
        env[n] = v
    _drop_extra_git_config(env)
    for n in WORKER_ENV_FORCED_OFF:
        env.pop(n, None)
    env.pop("AUTOOS_OMNIROUTE_KEY", None)
    if key:
        env["AUTOOS_OMNIROUTE_KEY"] = key
    # LAYERFENCE (fix 1): the layer the child runs at is the spawner's decision,
    # made from the spawner's own environment, and nothing else gets a vote — the
    # allowlist copy above and a plan entry below both could otherwise name it, and
    # a child that chooses its own mark chooses its own fence. Applied after the
    # plan merge for the same reason as the SHELL pin. An unmarked spawner stamps
    # nothing, so an L1's worker keeps the profile it always had.
    layer = child_agent_layer(src)
    if layer:
        env[ENV_AGENT_LAYER] = layer
    else:
        env.pop(ENV_AGENT_LAYER, None)
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

    HOSTADMISSION-OFF (lane AO-ADMISSION fix 2) is the second name in that
    exception, for the same reason and with the same shape: ``AUTOOS_ADMISSION_OFF``
    is read by whichever process evaluates the gate, so a value the MCP server
    honours MUST reach the runner that evaluates it again or the two answer the
    same question differently — the server answers "spawned" and the runner exits
    13. Forwarding it to our own CLI (named here, copied in ``spawner_child_env``)
    is the whole fix, and it stops there: ``worker_env``'s allowlist does not name
    it, so a client worker never inherits the escape and cannot turn the host's
    gate off for its own nested spawns. One behaviour: the escape means what it
    says in every process that starts a worker, and in no process that is one.
    """
    return (_plan_env_passed(name) or name in WORKER_ENV_AUTOOS
            or name in (resolver.CLAUDE_CRITICAL_ENV, ADMISSION_OFF_ENV))


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
    # HOSTADMISSION-OFF: same rule, same one name, for the reason in
    # `_child_env_passed` — the escape is read by the process that evaluates the
    # gate, and the runner evaluates it, so the server's answer and the runner's
    # exit code must come from the same environment. `worker_env` above never
    # lets it through to a client worker: this copy is for our own CLI only.
    if src.get(ADMISSION_OFF_ENV) == "1":
        env[ADMISSION_OFF_ENV] = src[ADMISSION_OFF_ENV]
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
    # LAYERFENCE (fix 1), the one exception to the stamp, and copied LAST so an
    # `extra` cannot relabel it: a child that is our own CLI carries the LANE's own
    # mark verbatim, because it is that CLI which stamps the worker it launches and
    # it re-reads the same two gates the server just read. Handing it the leaf mark
    # instead would refuse the very spawn the profile had already allowed — the
    # HOSTADMISSION-OFF shape exactly: one environment decides both halves.
    layer = agent_layer(src)
    if layer:
        env[ENV_AGENT_LAYER] = layer
    else:
        env.pop(ENV_AGENT_LAYER, None)
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
    tree) is not a refusal: there is nothing to provision. The TOOLHOME cache
    roots an --isolate plan redirects are provisioned under the same rule: the
    sandbox-private toolhome appears as their parent, mode 0700.
    """
    for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME") + TOOL_HOME_REDIRECTS:
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


def toolhome_dir(sandbox_path: str) -> str:
    """The sandbox-private tool-cache home: a sibling of the clone, removed
    with it (the `discard:` line names both), created 0700 by the provisioner."""
    return sandbox_path + TOOLHOME_SUFFIX


def redirect_tool_caches(env: dict, toolhome: str) -> None:
    """Repoint every measured tool-cache root into the sandbox-private toolhome
    (P3a: HOME and the caches are inherited, and an `ansible-galaxy` install
    from a seat wrote into ~/.ansible with nothing watching — the parent leak
    check reads the parent checkout, and ~/.ansible is in neither tree).

    HOME itself is NOT touched here: own-account clients (claude, qoder, qwen,
    agy, codex, gemini) authenticate from $HOME, so redirecting it breaks the
    login; a gateway client (opencode) needs nothing from it, and that split is
    decided by `forced_home` in the plan and applied in `worker_env`."""
    for name in TOOL_HOME_REDIRECTS:
        env[name] = os.path.join(toolhome, name.lower())


# SEAT-PYTEST (AO-L2-SEAT-INTEGRITY P4, measured): the redirect above moves
# PYTHONUSERBASE into the toolhome and `site.getusersitepackages()` with it — in an
# --isolate seat `python3 -m pytest` printed "No module named pytest" (it lives in
# ~/.local/lib/python3.X/site-packages), so no seat could RUN the tests it was told to
# run. Writes stay redirected; the read side reopens for exactly ONE spawner-computed
# directory. PYTHONPATH itself stays denied on both sides (FF1b): never a plan value,
# never a caller value, only this revalidated one.
def user_site_packages() -> str:
    """The user site-packages dir `site` computes for THIS interpreter."""
    return site.getusersitepackages()


# SEAT-PYTHON (P4-FIX2, measured): the interpreter version is part of the path, so
# asking the spawner (3.13 here) handed a 3.12 seat a directory it does not have. Ask
# the `python3` the seat will run; its answer goes through the same qualification.
SEAT_PY_USER_SITE_TIMEOUT = 5.0
_SEAT_PY_USER_SITE_CACHE: dict = {}


def seat_python_user_site(py: str, src: dict) -> str | None:
    """What `py -I` prints for its own user site, or None: an error, a timeout or a
    non-absolute / multi-line answer is a None and the caller falls back. Cached per
    (interpreter, HOME), because a fallthrough re-plan builds the plan again. The PYTHON*
    scrub is load-bearing: `-I` does not ignore PYTHONUSERBASE — site.py reads it
    itself — so the toolhome redirect would come back as the seat's real site."""
    key = (py, src.get("HOME") or "")
    if key not in _SEAT_PY_USER_SITE_CACHE:
        answer = None
        try:
            # subprocess-audit: one read-only path query against the seat's own
            # interpreter — chosen env, no shell, a timeout, output validated below.
            got = subprocess.run([py, "-I", "-c",
                                  "import site,sys;"
                                  "sys.stdout.write(site.getusersitepackages())"],
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                 env={n: v for n, v in src.items()
                                      if not n.startswith("PYTHON")},
                                 timeout=SEAT_PY_USER_SITE_TIMEOUT)
            out = got.stdout.strip() if not got.returncode else ""
            if out and "\n" not in out and os.path.isabs(out):
                answer = out
        except Exception:
            answer = None
        _SEAT_PY_USER_SITE_CACHE[key] = answer
    return _SEAT_PY_USER_SITE_CACHE[key]


def qualified_user_site(user_site: str, home: str) -> str | None:
    """`user_site` when it is safe to hand a seat read access to it, else None: a
    real directory, no symlink anywhere up to HOME, a strict descendant of HOME, and
    writable by nobody but the operator — a directory someone else can write is a
    module the child would import, the exact exposure the denial is for."""
    if not user_site or not home:
        return None
    user_site, home = os.path.abspath(user_site), os.path.abspath(home)
    if user_site == home or not user_site.startswith(home + os.sep):
        return None
    # realpath resolves every component, so a HOME-level parent that is a symlink —
    # escaping HOME or not — already failed the equality below.
    if not os.path.isdir(user_site) or os.path.islink(user_site) or \
            os.path.realpath(user_site) != user_site:
        return None
    if os.name == "nt":
        return user_site
    st = os.stat(user_site)
    if st.st_mode & stat.S_IWOTH:
        return None
    if st.st_mode & stat.S_IWGRP:
        # `pip install --user` under the common umask 002 leaves the real site 0775,
        # so a flat "no group-write" would refuse the one directory this is for. It
        # is safe exactly when the group is our own and empty of anyone else — that,
        # not the mode bit, is the claim.
        import grp
        if (st.st_uid != os.getuid() or st.st_gid != os.getgid() or
                grp.getgrgid(st.st_gid).gr_mem):
            return None
    return user_site


def seat_user_site_path(base: dict | None = None) -> str | None:
    """The one PYTHONPATH value a seat may get, or None — the user site of the
    interpreter the seat will run, resolved on the PATH and checked against the HOME of
    the environment it is built from; anything unusable falls back to the spawner's own
    interpreter and then to no PYTHONPATH. Every failure is a None, never a raised
    error: a seat brief that says its tests may not run beats a spawner that crashes."""
    src = os.environ if base is None else base
    home = src.get("HOME") or os.path.expanduser("~")
    try:
        py = shutil.which("python3", path=src.get("PATH"))
        seat_site = qualified_user_site(seat_python_user_site(py, src) if py else None,
                                        home)
        return seat_site or qualified_user_site(user_site_packages(), home)
    except Exception:
        return None


# TOOLWATCH (P3a): the well-known tool dirs under the REAL home, as the
# measured escapes wrote to them. Before and after an --isolate run each one
# is *statted* — never opened, never followed through a symlink, and a read
# that raises is skipped in silence: a hygiene warning is not allowed to break
# the run it is watching.
TOOL_WATCH_DIRS = (".ansible", os.path.join(".cache", "pip"), ".npm",
                   os.path.join(".cargo", "registry"), os.path.join(".cache", "uv"),
                   os.path.join(".local", "lib"))


def tool_watch_paths(home: str) -> list:
    return [os.path.join(home, *d.split(os.sep)) for d in TOOL_WATCH_DIRS]


def tool_watch_snapshot(paths: list) -> dict:
    """{path: "absent" | (mtime_ns, entry_count) | None} — None means "no claim":
    the read raised, and no verdict is ever built on it."""
    out = {}
    for p in paths:
        try:
            if not os.path.lexists(p):
                out[p] = "absent"
                continue
            st = os.lstat(p)              # lstat: never follow a symlink
        except (OSError, ValueError):
            out[p] = None
            continue
        count = None
        if stat.S_ISDIR(st.st_mode):      # only a real directory is counted
            try:                          # scandir never opens an entry
                count = sum(1 for _ in os.scandir(p))
            except (OSError, ValueError):
                count = None
        out[p] = (st.st_mtime_ns, count)
    return out


def tool_watch_changes(before: dict, after: dict, home: str) -> list:
    """The watched dirs that changed during the run, home-relative (`~/.ansible`):
    a name for a WARNING line and a run record, never an absolute path with the
    operator's username in it (AGENTS.md 7)."""
    out = []
    for p, b in before.items():
        a = after.get(p)
        if b is None or a is None or b == a:
            continue
        out.append("~/%s" % os.path.relpath(p, home).replace(os.sep, "/"))
    return out


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
                             capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.split()
    for remote in remotes:
        subprocess.run(["git", "-C", sandbox, "remote", "set-url", "--push",
                        remote, ISOLATE_PUSH_DISABLED], check=True, stdin=subprocess.DEVNULL)
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


def isolate_task_prefix(sandbox_path: str, root: str, read_only: bool = False,
                        base_line: str = "", stamp_base: bool = True) -> str:
    """The lines prepended to the task text of an --isolate run.

    SPAWNFIX (S3) item 2 (work/L1-routing/LEAKFP.out): a headless worker that
    reaches for approval stops there and exits 0, so the containment line is
    paired with the one fact the worker cannot probe: nobody is answering.

    Item 4 adds the third line a research run needs: an edit is not the
    deliverable, and a worker that is never told so will helpfully make one.
    P1: a review seat is pointed at that file - its own history cannot resolve shas.
    P2/G1b: `stamp_base=False` says the base line is quoted by the seat template
    instead, so one prompt never carries the same proof line twice.
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
    if base_line:
        stated = base_line.strip() + "\n" if stamp_base else ""
        lines += ("\n%sThe change under review is written to %s - read that file; "
                  "`git diff`/`git show` on those shas name nothing here: this seat "
                  "is a one-commit snapshot."
                  % (stated, os.path.join(sandbox_path, REVIEW_DIFF_FILE)))
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


def qualify_pinned_model(cfg: dict, model) -> str | None:
    """`model` with the opencode.jsonc provider that declares it, or None.

    T2-RECORD-PIN item 3: `--free-model or-qwen3.8-27b-free` (and every other
    bare pin) is an id opencode cannot resolve on its own — the config declares
    models under a provider, and the model opencode is handed is
    `provider/mid`. Exactly ONE provider may declare it: two would make the
    prefix the caller's guess rather than a fact, so an ambiguous or wholly
    undeclared pin returns None and the caller refuses with the same
    "not declared in opencode.jsonc providers" reason `resolve_model` gives.

    An already-qualified pin is returned unchanged (and a variant tail rides
    with its base), so this is safe to call on every pin — idempotent, never
    double-prefixed.
    """
    text = str(model or "").strip()
    if not text:
        return None
    base, sep, variant = text.partition("#")
    if "/" in base:
        return text
    owners = [pid for pid, prov in (cfg.get("providers") or {}).items()
              if base in (prov.get("models") or {})]
    if len(owners) != 1:
        return None
    return "%s/%s%s%s" % (owners[0], base, sep, variant)


def resolve_model(cfg: dict, tier: int, clean: bool, override: str | None) -> str:
    agent = TIERS[tier]
    model = override or cfg["agents"][agent]["model"]
    base, _, variant = model.partition("#")
    # T2-RECORD-PIN item 3: a bare pin is qualified before it is tested, so a
    # model the config DOES declare is launched instead of refused for wearing
    # no provider prefix. A pin nothing declares still fails below, in the same
    # words it always used.
    qualified = qualify_pinned_model(cfg, base)
    if qualified:
        base = qualified
    if clean and not base.endswith("-clean"):
        base += "-clean"
    if base not in declared_models(cfg):
        raise ValueError("%s is not declared in opencode.jsonc providers "
                         "(a bare pin needs the provider prefix that declares "
                         "it, e.g. omniroute/%s)" % (base, base))
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


def root_agents_block(cfg: dict) -> dict:
    """The ``agents`` definitions from ROOT's opencode.jsonc (via ``cfg``).

    Only the agent definitions are copied — no providers, no keys, no env.
    This is merged into the overlay for foreign-repo sandboxes so the child
    opencode process finds l1-orchestrator / l2-worker / t3-reviewer / l2-researcher
    even though the foreign clone's own tree has no opencode.jsonc.
    """
    return dict(cfg.get("agents") or {})


def root_overlay_blocks(cfg: dict) -> dict:
    """ROOT's ``providers`` and top-level ``permissions`` (via ``cfg``).

    FLEET-AGENTS-2: a foreign-repo sandbox lacks these two blocks as well, so
    opencode stops with ``Model unavailable: omniroute/l2-worker`` and the
    worker runs without the shell/tool fence every AutoOS worker gets. Only
    these two keys are copied (deep copies, so a merge never mutates ``cfg``);
    never mcp / tools / experimental / model. ``providers`` carries env var
    NAMES (``AUTOOS_OMNIROUTE_KEY``), never a key value.
    """
    blocks = {}
    if isinstance(cfg.get("providers"), dict):
        blocks["providers"] = copy.deepcopy(cfg["providers"])
    if isinstance(cfg.get("permissions"), list):
        blocks["permissions"] = copy.deepcopy(cfg["permissions"])
    return blocks


def merge_root_providers(root_providers: dict, overlay_providers: dict) -> dict:
    """Per provider: ROOT's definition first, the overlay's per-run keys on top.

    A dict value present on both sides (``settings``, ``headers``) merges ONE
    level deep, so the per-run session headers and the stamped
    ``settings.baseURL`` keep winning while ROOT's package / name / env /
    models stay. Any other overlay value replaces ROOT's.
    """
    merged = copy.deepcopy(root_providers)
    for name, prov in (overlay_providers or {}).items():
        base = merged.get(name)
        if not isinstance(base, dict) or not isinstance(prov, dict):
            merged[name] = prov
            continue
        for key, value in prov.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = {**base[key], **value}
            else:
                base[key] = value
    return merged


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
# HOSTADMISSION (lane AO-ADMISSION, 2026-10-08): a spawn that starts a worker on
# a host that is already full does not fail, it succeeds somewhere worse - the
# box swaps, every lane on it goes red at once, and nothing in the routing layer
# says which run caused it. So the spawner asks the host for permission: the live
# worker count (`ps` state "running", all lanes of this checkout) and MemAvailable
# from /proc/meminfo, both measured against catalog/ai-registry.json's
# `host_admission` section. Distinct from 9 on purpose: 9 is "the provider said
# no, wait and it may open", this is "the machine said no, and waiting on it is
# the caller's decision, not a bounded sleep here". A child that exits 13 on its
# own reads the same from the code alone, so stderr is what tells them apart: an
# admission refusal always starts "host admission:".
EXIT_HOST_ADMISSION = 13
ADMISSION_MAX_LIVE_DEFAULT = 6
ADMISSION_MEM_FLOOR_MB_DEFAULT = 6144
ADMISSION_OFF_ENV = "AUTOOS_ADMISSION_OFF"
ADMISSION_MEMINFO_ENV = "AUTOOS_MEMINFO_PATH"
MEMINFO_PATH = "/proc/meminfo"
# LAYERFENCE (lane L2GATES-tier fix 12, 2026-10-08): the layer a spawn sits at is
# marked in its own environment, and two rules are enforced at the last mile with
# this code rather than rc 2: a leaf (L3) never spawns at all (R-worker-06), and a
# spawn made FOR an L2 lane is never the L1's seat (tier 1 / orchestrate / Claude).
# Distinct from 2 because a caller that reads only the code must be able to tell
# "your flags were wrong" from "your layer may not do this at all" — the second one
# is a report to the caller above, not a retry.
EXIT_LAYER_FENCE = 14
# HOSTADMISSION-RACE (lane AO-ADMISSION fix 1, 2026-10-08): the gate's other
# placeholder. `FREE_RESERVATION_SUFFIX` is one provider's leg; this one is the
# host's slot — taken by the spawner that the host admitted, released when that
# run's worker record exists. Not `.json`, so `ps` never lists it.
ADMISSION_RESERVATION_SUFFIX = ".admission-reservation"

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


def _reservation_rows(directory: str, provider: str | None = None,
                      suffix: str = FREE_RESERVATION_SUFFIX) -> tuple:
    """(live paths, stale paths) of the reservations in `directory` naming `provider`.

    A reservation is claimed by a live process or by none at all: the pid in it
    is gone (a killed spawner, a reboot) or its file cannot be read, so it is
    stale and reaped. A stale placeholder must never queue a run — the cost of
    dropping one is a 429, the cost of trusting it is a leg nothing can leave.

    `suffix` names which placeholder family (a free leg's, the host's);
    `provider=None` means the family is not provider-scoped.
    """
    live, stale = [], []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return live, stale
    for name in names:
        if not name.endswith(suffix):
            continue
        path = os.path.join(directory, name)
        try:
            with io.open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            stale.append(path)
            continue
        if not isinstance(record, dict):
            continue
        if provider is not None and record.get("provider") != provider:
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


def _reserve_free_slot(directory: str, provider: str, model: str,
                       suffix: str = FREE_RESERVATION_SUFFIX,
                       prefix: str = "free"):
    """Write this run's placeholder and return its path (None when it cannot be
    written — the run then proceeds unreserved, exactly as it used to).

    Two families write one shape: the free leg's (`provider` names it, so a
    second provider's run never counts it) and the host's (no provider, so every
    admitted spawner counts every other one's). The name never ends `.json`, so
    `ps` — which reads each `.json` in the directory as a worker — cannot list a
    placeholder.
    """
    wid = "%s-%d-%s" % (prefix, os.getpid(), os.urandom(3).hex())
    record = {"id": wid, "pid": os.getpid(), "pid_start": _proc_starttime(os.getpid()),
              "started": utc_now_iso()}
    if provider is not None:
        record["provider"] = provider
        record["model"] = model
    path = os.path.join(directory, wid + suffix)
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


GEMINI_ALLOWED_RE = re.compile(r"gemini-3\.[678]-flash(?:-[a-z0-9]+)*\Z", re.I)
# The variant tail above happily accepts `-pro` as one more suffix, which would
# let a Pro model (D-255 bans every one of them) in wearing a Flash name. A
# '-'-delimited segment that starts with 'pro' — glued spellings like `-promax`
# included, so the rule is conservative — refuses the id whatever else matched.
GEMINI_PRO_SEGMENT_RE = re.compile(r"(?:^|-)pro", re.I)


def gemini_model_allowed(model_id) -> bool:
    """Whether `model_id` is off the operator's Gemini ban list (D-255).

    No Gemini *Pro* model, and only 3.6 / 3.7 / 3.8 Flash — variants of those
    versions (-flash-high, -flash-medium, -flash-preview) count as allowed. The test
    is on the *model* part of the id: after the last '/', and from the 'gemini' that
    names the model onward, so `vertex-gemini-3.8-flash` and
    `openrouter/google/gemini-3.8-flash` read the same way, a provider namespace that
    merely spells 'gemini' (`gemini/gpt-oss-120b`) says nothing about the model behind
    it, and every non-Gemini id is unaffected. Read as: allow unless it names a Gemini
    model the list does not. (why: D-255 is a spend-and-data rule, not a preference)
    """
    if not isinstance(model_id, str):
        return True
    part = model_id.lower().rsplit("/", 1)[-1]
    if "gemini" not in part:
        return True
    tail = part[part.index("gemini"):]
    return bool(GEMINI_ALLOWED_RE.fullmatch(tail)) and not GEMINI_PRO_SEGMENT_RE.search(tail)


# T2-RECORD-PIN item 5 (D-284): the provider path this lane may not launch.
# omniroute/gemini-3.8-flash is no longer held (D-505 operator go, D-507 routing:
# paid tier, $200 hard stop / $180 warn, private-safe per the cited paid terms;
# training-leg classification of this pool is F0's business). What stays refused
# here is the openrouter route to the same model; every other Gemini spelling is
# answered by the other gates (D-255), as before.
D284_BANNED_PREFIXES = ("openrouter/google/",)


def d284_model_key(value) -> str:
    """The one spelling D-284 compares against (F1).

    The guard used to compare the raw string, so every spelling of the same
    model read as a different one and slipped past: `OmniRoute/...` (the prefix
    strip was case-sensitive), `omniroute/omniroute/...` (one prefix stripped),
    `...-clean` (the twin `_model_pin_key` already treats as the same model),
    `openrouter/ google/...` (whitespace inside the prefix) and any upper-case
    spelling of the banned prefix. Normalised ONCE here — strip, case-fold,
    collapse whitespace around `/`, drop the `#effort` rung, every leading
    `omniroute/` prefix and a trailing `-clean` — and compared. A D-284-private
    key, so `model_route_id`/`_model_pin_key` (which stay case-sensitive for
    the mismatch gate) keep the behaviour their own tests pin.
    """
    text = str(value or "").strip()
    text = re.sub(r"\s*/\s*", "/", text)
    text = text.partition("#")[0].strip().casefold()
    while text.startswith("omniroute/"):
        text = text[len("omniroute/"):]
    if text.endswith("-clean"):
        text = text[: -len("-clean")]
    return text


def d284_model_refusal(model) -> str | None:
    """Why D-284 will not launch this pin, or None when the pin is allowed.

    The ONE guard for both entry points (spec: a single D-284 helper): the CLI
    reads it on `--model` and `--free-model` before anything is priced or
    planned, the MCP server reads it on the spawn request before an argv exists.
    A pin that names no model (None, "") is nothing to guard, so it passes.
    Comparison runs on `d284_model_key`, never on the raw spelling.
    """
    text = str(model or "").strip()
    if not text:
        return None
    key = d284_model_key(text)
    banned = key.startswith(D284_BANNED_PREFIXES)
    if not banned:
        return None
    return ("D-284: %s stays off the spawn list until stage 2 (operator hold) - "
            "pin another model, or leave --model off and let the router pick "
            "the leg for this card." % text)


def gemini_spawn_refusal(model, combo, registry, cfg=None, explicit=False) -> str:
    """Why this spawn breaks D-255, or "" when it does not.

    What the run is *aimed at* is refused outright: the model the argv carries
    (`omniroute/vertex-pro`), the modelID an opencode.jsonc hand entry passes
    through to (`vertex/gemini-3.1-pro-preview` — refused even when no registry
    row describes it, because the name alone says which model answers), and the
    leg that name resolves to. A combo is a different case, and `--model` naming
    one is not an aim at its off-list leg: this registry still carries one
    off-list fall-through leg each in `l2-worker` and `gemini-3.8-flash`, and
    refusing those would bench every tier-2 spawn over a leg that only answers
    when the legs ahead of it are down — so a combo is refused only when EVERY
    leg is off-list, whether the router picked it or the caller named it
    (RWP2 S1: the explicit case is what made
    `ReviewFindingTests.test_gateway_client_model_override_wins_over_the_card`
    print nothing on the lane). D-255 is therefore enforced at the spawn door
    for what a run is aimed at; an off-list fall-through leg inside a combo is
    registry data the operator must remove, and it is reported here for that.
    """
    aimed = [model, combo, hand_entry_model_id(model, cfg),
             hand_entry_leg(model, registry, cfg) if model else ""]
    bad = [str(value) for value in aimed if value and not gemini_model_allowed(value)]
    legs = combo_legs(combo, registry) if combo else []
    off = [leg for leg in legs if not gemini_model_allowed(leg)]
    if off and len(off) == len(legs):  # `explicit` no longer switches anything

        bad += off
    if not bad:
        return ""
    return ("gemini allow-list: %s would answer with %s — operator D-255 allows only "
            "Gemini 3.6 / 3.7 / 3.8 Flash (variants included) and refuses every Gemini "
            "Pro model, so this spawn is refused."
            % (model or combo, ", ".join(sorted(set(bad)))))


def hand_entry_model_id(name, cfg=None) -> str:
    """The `modelID` an opencode.jsonc hand entry passes through to, or "".

    A hand entry is the config's own passthrough — `vertex/gemini-3.8-flash`,
    `ovh/gpt-oss-120b` — and the generated region's entries are not: their modelID
    is the route id itself. So the two shapes are told apart by exactly that.
    """
    base = model_route_id(name)
    if not base or "/" in base:
        return ""
    for provider in ((opencode_cfg(cfg) or {}).get("providers") or {}).values():
        if not isinstance(provider, dict):
            continue
        entry = (provider.get("models") or {}).get(base)
        model_id = entry.get("modelID") if isinstance(entry, dict) else None
        if isinstance(model_id, str) and "/" in model_id and model_id != base:
            return model_id
    return ""


def hand_entry_leg(model, registry, cfg=None) -> str:
    """The registry leg an opencode.jsonc hand entry's modelID names, or "".

    The modelID is the gateway spelling combos.json carries — no route, so
    `_combo_of` finds nothing and the budget gate benched the name as unpriceable.
    `registry_ref()` is the one inverse of `gateway_ref()`, so the modelID rewrites
    back to the registry leg whose model row says what family answers and what the
    provider tier costs. A name that is no hand entry, or whose modelID no registry
    row describes, yields "" and the caller keeps refusing — an id that resolves to
    nothing is still never assumed free. (D5)
    """
    model_id = hand_entry_model_id(model, cfg)
    if not model_id:
        return ""
    ref = registry_ref(model_id, registry)
    try:
        resolve_leg(ref, registry)
    except ValueError:
        return ""
    return ref


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
                        source: str | None = None, cfg=None):
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
            # D5: an opencode.jsonc hand entry is no route, but its modelID is a
            # provider/model leg the registry does carry, so the name is priceable
            # after all. Only the leg's own row decides Claude-ness; a hand entry
            # whose modelID resolves to nothing falls through to the refusal below.
            leg = hand_entry_leg(combo, registry, cfg)
            if leg:
                return _leg_is_claude(leg, registry)
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
        # l2-worker, not "unknown". A card the router refuses raises here, and
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


DAILY_GATE_ENV_VAR = "AUTOOS_DAILY_GATE_FILE"
DAILY_GATE_MAX_AGE_SECONDS = 7200  # 2 hours


def _is_google_paid_model(model: str) -> bool:
    """Whether one model string is a Google-paid one.

    Agy/antigravity and OVH names are never counted - per model string, so an
    OVH leg beside a vertex plan model does not stand down the check.
    """
    m = str(model or "").strip().lower()
    if m in ("antigravity", "agy", "ovh", "ovhcloud"):
        return False
    if m.startswith(("antigravity/", "agy/", "ovh/", "ovhcloud/")):
        return False
    if m.startswith(("vertex/", "gemini/", "omniroute/vertex-", "omniroute/gemini-",
                     "vertex-", "google/")):
        return True
    return m in ("vertex", "gemini", "google")


def is_google_paid_start(args=None, plan=None) -> bool:
    """True when ANY model or leg this start can use is Google-paid.

    `--free` does not exempt the model the run will actually use: `--free
    --model X` launches X, so it is checked like any other pin. A plain
    `--free` run uses the free chain (never a Google-paid model the caller did
    not name), so it passes. Deciding per model string - not per start - keeps
    an OVH or agy leg from disabling the gate for a vertex plan model.
    """
    client = None
    if args is not None:
        client = getattr(args, "client", None)
    if not client and isinstance(plan, dict):
        client = plan.get("client")
    if client in ("agy", "antigravity"):
        return False

    models = []
    if args is not None:
        if getattr(args, "model", None):
            models.append(getattr(args, "model"))
        free_model = getattr(args, "free_model", None)
        # the same two pins the CLI checks before anything else (the --model
        # pin, and the free model when this run would actually use it)
        if getattr(args, "free", False) or (free_model or "") != DEFAULT_FREE_MODEL:
            if free_model:
                models.append(free_model)
    if isinstance(plan, dict):
        if plan.get("model"):
            models.append(plan["model"])
        if plan.get("free_model"):
            models.append(plan["free_model"])
        route = plan.get("route")
        if isinstance(route, dict):
            if route.get("model"):
                models.append(route["model"])
            for leg in (route.get("legs") or []):
                if isinstance(leg, dict):
                    if leg.get("model"):
                        models.append(leg["model"])
                    if leg.get("provider"):
                        models.append(leg["provider"])
                elif isinstance(leg, str):
                    models.append(leg)

    return any(_is_google_paid_model(m) for m in models)


def _read_daily_gate(path: str):
    """The gate JSON from `path`, in whatever encoding the writer's shell gave it.

    `run_budget.py day` writes plain UTF-8, but a redirection may change the
    encoding on the way out: PowerShell 5 `>` writes UTF-16, `Out-File -Encoding
    utf8` and `Set-Content -Encoding utf8` add a BOM. Try UTF-8 (with or
    without BOM) first, then UTF-16, instead of failing open on a perfectly
    good block.
    """
    last = None
    for enc in ("utf-8-sig", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (OSError, ValueError) as exc:
            last = exc
    raise last


def _parse_gate_num(val, default: float) -> float:
    """Parse a gate number (usd or budget). Falsy values use the default.
    Booleans, non-numeric strings, non-finite values, and non-empty collections
    raise an exception to be handled as garbage values.
    """
    if val is True:
        raise ValueError("boolean true")
    if not val:
        return default
    num = float(val)
    import math
    if math.isnan(num) or math.isinf(num):
        raise ValueError("non-finite number")
    return num


def default_daily_gate_path(env=None, is_windows=None):
    """Where the refresh script writes the gate when nobody names a file.

    Linux: ${XDG_STATE_HOME:-~/.local/state}/autoos/daily-gate.json
    Windows: %LOCALAPPDATA%\\autoos\\daily-gate.json

    `env` is the environment to read XDG_STATE_HOME / LOCALAPPDATA from; when
    it is None, os.environ is used. `is_windows` overrides the platform check
    so a test can reach the Windows branch without touching os.name. Returns
    None when the base directory the platform needs is absent.
    """
    if env is None:
        env = os.environ
    if is_windows is None:
        is_windows = (os.name == "nt")
    if is_windows:
        base = (env.get("LOCALAPPDATA") or "").strip()
        if not base:
            return None
        return os.path.join(base, "autoos", "daily-gate.json")
    state = (env.get("XDG_STATE_HOME") or "").strip()
    if not state:
        # `~` is $HOME, read from the passed env (not the process home) so an
        # injected env is honoured; an env with no HOME has no default path.
        home = (env.get("HOME") or "").strip()
        if not home:
            return None
        state = os.path.join(home, ".local", "state")
    return os.path.join(state, "autoos", "daily-gate.json")


def daily_gate_refusal(args=None, plan=None, env=None, now=None) -> str | None:
    """Return refusal message if this start is Google-paid and the daily gate
    says block, else None.

    Fails OPEN (printing 'daily gate unavailable' to stderr) when the gate
    file is unset, unreadable, not written for today's UTC day, older than
    2 hours, or dated in the future: any of those is a report that does not
    speak about today's spend, and a guard that guesses is not a guard.
    """
    if not is_google_paid_start(args, plan):
        return None

    if env is None:
        env = os.environ
    if now is None:
        now = time.time()

    def unavailable(reason: str) -> None:
        print(f"daily gate unavailable: {reason}", file=sys.stderr)
        return None

    gate_path = (env.get(DAILY_GATE_ENV_VAR) or "").strip()
    if not gate_path:
        # No env var: the default gate file, if present, speaks for today.
        # A missing default file fails open - an absent guard is not a block.
        gate_path = default_daily_gate_path(env) or ""
        if not gate_path or not os.path.exists(gate_path):
            return unavailable("env var not set and no default gate file")

    try:
        mtime = os.path.getmtime(gate_path)
        # Stale means too old AND not-yet: the file is a verdict about a closed
        # UTC day, and a block written for another day must not ride into this
        # one on the strength of a fresh mtime alone.
        if mtime > now:
            return unavailable("file stale (future mtime)")
        if now - mtime > DAILY_GATE_MAX_AGE_SECONDS:
            return unavailable("file stale (age)")
        data = _read_daily_gate(gate_path)
    except OSError:
        return unavailable("file unreadable")
    except ValueError:
        return unavailable("not a JSON object")

    if not isinstance(data, dict):
        return unavailable("not a JSON object")

    day = str(data.get("day") or "").strip()
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    if day != today:
        return unavailable("file stale (not today's UTC day)")

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict != "block":
        return None

    try:
        total_usd = _parse_gate_num(data.get("usd"), 0.0)
        budget = _parse_gate_num(data.get("budget"), 25.0)
    except (TypeError, ValueError, OverflowError):
        # A word where a number belongs is an unreadable gate, not a gate with
        # no numbers: fail open with the note, never a traceback.
        return unavailable("garbage value")
    return "daily budget blocked: day total $%.2f exceeds budget $%.2f" % (total_usd, budget)


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
                        "--git-common-dir"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode == 0 and r.stdout.strip():
        main = os.path.dirname(r.stdout.strip())
        if os.path.realpath(main) != os.path.realpath(root):
            roots.append(main)
    return [os.path.join(r_, "configuration", "api-keys.yml") for r_ in roots]


def client_key(root: str) -> str | None:
    """The gateway client key, or None when no source has one.

    Resolution is tools/autoos_gateway_key.py's (the one rule): env
    AUTOOS_OMNIROUTE_KEY wins, then the gateway-named api-keys.yml field
    (`omniroute_server` / `omniroute_<host>`), then the legacy field with a
    deprecation line. Each checkout's file is tried in key_files() order;
    a missing key raises inside the helper and means "try the next file".
    """
    if (os.environ.get("AUTOOS_OMNIROUTE_KEY") or "").strip():
        return os.environ["AUTOOS_OMNIROUTE_KEY"]
    try:
        from autoos_gateway_key import resolve_client_key
    except ImportError:
        return None
    for path in key_files(root):
        if not os.path.isfile(path):
            continue
        try:
            return resolve_client_key(os.environ, Path(path))
        except KeyError:
            continue
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


def seat_deps_note(modules=("mcp",)):
    """One line naming the project-test modules this interpreter lacks ('' when none).

    AO-L2-SEAT-INTEGRITY P3b item 2 (measured): a seat sandbox had no `mcp`, so the
    project tests it was briefed to run could not start, and the seat reported an
    untested verdict as if it had tested. Read with `importlib.util.find_spec` in the
    interpreter the seat will use — `sys.executable` of this spawner, which is what the
    sandbox's `python3` resolves to — and only ever SAID: nothing here installs.

    The fix for a missing dep would be an optional per-sandbox `uv venv` from the repo
    lockfile — a network install inside a run that may be --read-only, into a tree the
    leak check has to reason about. Future work, for the operator; this stays a report.
    """
    missing = []
    for name in modules:
        try:
            found = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            found = None  # a missing parent package is a missing module, not a crash
        if found is None:
            missing.append(name)
    if not missing:
        return ""
    return "project tests unrunnable: missing python modules: %s" % ", ".join(missing)


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
# REVGATE2F (operator 2026-09-30): one line is one SEAT, and a ready lane needs
# two seats from two different families, so a record carries at least two lines.
REVIEW_ENTRY_RE = re.compile(r"^\s*(?:[#>*-]+\s*)?AutoOS-Review:\s*(?P<body>.+)$")
REVIEW_ENTRY_FIELDS = ("kind", "author", "reviewer", "family", "verdict", "evidence")
READY_VERDICTS = frozenset(("ready", "pass", "passed", "approve", "approved", "lgtm",
                            # SPAWNFIX3 (S3) item 5 (REVGATE.record.md): a Sonnet
                            # final signs its lanes "SHIP"; "fix-first" is the same
                            # vocabulary's OPEN finding and stays refused.
                            "ship", seat_verdict.READY_TOKEN))  # AO-SEAT-VERDICT-GRAMMAR
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
                     "reviewer=<model> [family=<family>] "
                     "verdict=<ready|pass|ship|lgtm|accept|...> [evidence=<path>]")


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


def family_for_model(model) -> str | None:
    """The family the registry declares for a model spelling, or None.

    T2-RECORD-PIN item 2: the worker record says which family answered, so a
    review can hold a family to account without re-deriving it from the pin.
    The ONE lookup is `_family_of_one_spelling` (policy.reviewers' own spelling
    first, then `models`, whole and after the last `/`), so a record and a
    reviewer resolution can never name two families for one model.

    An unreadable registry or a model the registry never heard of is None —
    the record must not invent a family (an invented one would read as a
    second, independent witness). Reading is the whole cost: this opens the
    registry, so it is called at record time, never in a loop.
    """
    text = str(model or "").strip()
    if not text:
        return None
    try:
        registry = load_registry(REGISTRY_PATH)
    except (OSError, ValueError):
        return None
    return _family_of_one_spelling(text, registry)


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


# --- AO-RUN-FAMILY-RECORD (D-807): the family of the model a run ANSWERED with ----
#
# `family_for_model` answers one question: what family does the registry declare
# for this exact spelling. The run record needs the wider one, because a client's
# own model id is not a registry spelling: `qfmodel` is Qoder's key for what its
# account serves, `claude-sonnet-5-5` is Anthropic's id for a Sonnet, and every run
# on one of them recorded `family: unresolved` — which the review footer read as
# "nobody knows who reviewed this" (CROSS-FAMILY: unknown beside a writer line that
# NAMED the model), and the plangraph keeper read as no reviewer token at all.
#
# The layers below answer in order and say WHICH one answered (`family_source`), so
# a reader can tell a registry row from a vendor name from a client's default. When
# none of them names a family the answer is `null` plus the reason — never the word
# `unresolved`, which is not a family and sat in a family's field reading like one.
# What stays unchanged is the conservative lookup the lane-record review seats use
# (`family_for_model`, `reviewer_family`): an invented author or reviewer family is
# still refused there, and a run's own record is not evidence about anyone else's.

#: The vendor spellings that name a registry family from inside a model id, a
#: provider id or a client name — the ONE normalisation table: "gpt-oss" is
#: openai-oss and never openai, "claude"/"sonnet"/"opus" are anthropic, "Meta Muse"
#: is meta. Matched longest-token-first across the whole table, so a spelling that
#: carries two vendor names takes the more specific one. A family is only ever a
#: key of this table: a name the table cannot place answers None, and nothing is
#: lifted out of an arbitrary string.
FAMILY_VENDORS = (
    ("openai-oss", ("gpt-oss", "openai-oss")),
    ("anthropic", ("claude", "anthropic", "sonnet", "opus", "haiku", "fable")),
    ("google", ("gemini", "google")),
    ("qwen", ("qwen", "tongyi", "qwq")),
    ("deepseek", ("deepseek",)),
    ("nvidia", ("nemotron", "nvidia")),
    ("meta", ("muse-spark", "llama", "meta")),
    ("mistral", ("mistral", "codestral", "devstral")),
    ("xiaomi", ("mimo", "xiaomi")),
    ("zhipu", ("glm", "zhipu", "zai-org")),
    ("moonshot", ("kimi", "moonshot")),
    ("minimax", ("minimax",)),
    ("meituan", ("longcat", "meituan")),
    ("cohere", ("command-r", "command-a", "north-mini", "cohere")),
    ("poolside", ("laguna", "poolside")),
    ("thinkingmachines", ("inkling",)),
    ("nex-agi", ("nex-pro", "nex-mini")),
    ("inclusionai", ("ling-", "inclusionai")),
    ("dots-studio", ("dots3",)),
    ("openai", ("gpt-4", "gpt-5", "gpt-6", "openai")),
)

#: Which layer named the family — said out loud, because "the registry declares it"
#: and "the vendor's own name says it" are claims of different strength.
FAMILY_SOURCE_REGISTRY = "registry-row"
FAMILY_SOURCE_MODEL_NAME = "model-vendor-name"
FAMILY_SOURCE_ROUTE_LEGS = "route-single-family"
FAMILY_SOURCE_PROVIDER_NAME = "provider-vendor-name"
FAMILY_SOURCE_CLIENT_DEFAULT = "client-default-model"
FAMILY_SOURCE_CLIENT_NAME = "client-vendor-name"
#: The plan's own claim, for a family nothing witnessed. It is the one value beside
#: the six above that names no model which ANSWERED: a run that died before it served
#: anything has a family because its launch line named a model, and "what I meant
#: to run" is not evidence about what wrote the diff. Every layer above reads a
#: family off the served id; this one is what the spawn-time records carry — the
#: worker record, job.json's `request`, and exit.json where no kill record answered
#: — so a reader can tell the two apart. A gate accepts a served source or nothing:
#: `family_is_planned` is the one question callers ask it (AO-RUN-FAMILY-RECORD).
FAMILY_SOURCE_PLANNED = "planned-model"

# The parts of a spelling that are not a name: the separators a vendor's id uses,
# and the two ends of a gateway or promo spelling that ride along without naming
# anybody. `omniroute/`, `opencode/` and `agy-` are a route's and a client's own
# prefix, and `-free` / `:free` is the promo tail of a leg the registry lists
# without it — strip them first, so `opencode/mimo-v2.6-flash:free` and `mimo` are
# one name to the table below.
_FAMILY_SEPARATORS_RE = re.compile(r"[\s._:/+]+")
_FAMILY_SEGMENTS_RE = re.compile(r"-|(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")
_FAMILY_PREFIXES = ("omniroute-", "opencode-", "agy-")
# The `:` of a `:free` spelling arrives here as a `-`, the separators go first.
_FAMILY_FREE_SUFFIXES = ("-free",)


def _family_spelling(spelling) -> str | None:
    """A spelling reduced to its separator-normalised, prefix-and-promo-free form."""
    key = resolver.family_key(spelling)
    if not key:
        return None
    key = _FAMILY_SEPARATORS_RE.sub("-", key).strip("-")
    key = re.sub(r"-{2,}", "-", key)
    for prefix in _FAMILY_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
    while True:
        stripped = None
        for suffix in _FAMILY_FREE_SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix):
                stripped = key[:-len(suffix)]
        if stripped is None:
            return key.strip("-")
        key = stripped


def _family_segments(text: str) -> list:
    """The whole names inside a normalised spelling, in order.

    A name is one run of letters or one run of digits, so `qwen3.8-flash` is
    `qwen, 3, 8, flash` and `gpt-4o` is `gpt, 4, o`: a vendor's name ends where its
    version digits begin. That boundary is what a plain `in` test on the whole id
    cannot see — it read `llama` inside `ollama` and `sonnet` inside `notsonnet`,
    and invented a family out of the middle of a word."""
    return [part for part in _FAMILY_SEGMENTS_RE.split(text) if part]


# Each token of the table as the name-segments it is, resolved once at import:
# `(length used to break ties, family, segments)`, longest token first.
_FAMILY_TOKEN_SEGMENTS = sorted(
    [(-len(token), family, tuple(_family_segments(_family_spelling(token) or "")))
     for family, tokens in FAMILY_VENDORS for token in tokens],
    key=lambda row: row[0])


def vendor_family_name(spelling) -> str | None:
    """The family a model / provider / client spelling names from inside its own id.

    The match is by WHOLE name, not by a piece of one: the spelling is normalised
    (case, separators, the route prefixes and the promo tail removed) and split into
    its names, and a table token answers only where its own names stand in that list
    together, in order. "OpenAI OSS", "openai_oss" and "gpt-oss-120b" all reach
    `openai-oss`; "notsonnet" and "mimosa-v1" reach nothing, because no name in them
    is `sonnet` or `mimo` (AO-RUN-FAMILY-RECORD REJECT 2). Where two tokens match,
    the longer one wins — `gpt-oss` before `openai`, so an OSS model never reads as
    its provider. None when the table places nothing, which the caller records as an
    unknown family rather than a guessed one.
    """
    key = _family_spelling(spelling)
    if not key:
        return None
    segments = _family_segments(key)
    for _neg_len, family, token in _FAMILY_TOKEN_SEGMENTS:
        if not token:
            continue
        width = len(token)
        if any(tuple(segments[i:i + width]) == token
               for i in range(len(segments) - width + 1)):
            return family
    return None


def vendor_family_name_unique(spelling) -> str | None:
    """The ONE family a model id names, or None when it names two.

    The fence's own read of a model id (`fence_family_of`). The longest-token
    tie-break above is right for a run's record (some family has to be named for
    the writer line) and wrong for a fence: `omniroute/deepseek-r1-distill-qwen-32b`
    names both `deepseek` and `qwen`, and picking the longer read it as deepseek, so
    a `--not-family qwen` review accepted a Qwen-derived model. Same for
    `nvidia/qwen3-nemotron`, which reads nvidia and slips the qwen fence. Two
    distinct vendors in one id at DISJOINT spans → None (unplaceable): a review's
    strict fence refuses it, a write role does not block — the existing unknown
    semantics. A shorter match nested INSIDE a longer one is that name's provider
    prefix, not a second vendor, so the longer family wins (S1 F1 round 2).
    """
    key = _family_spelling(spelling)
    if not key:
        return None
    segments = _family_segments(key)
    spans = []
    for _neg_len, family, token in _FAMILY_TOKEN_SEGMENTS:
        if not token:
            continue
        width = len(token)
        for i in range(len(segments) - width + 1):
            if tuple(segments[i:i + width]) == token:
                spans.append((i, i + width, family))
                break
    if not spans:
        return None
    families = {family for start, end, family in spans
                if not any(family != other
                           and start >= other_start and end <= other_end
                           for other_start, other_end, other in spans)}
    return families.pop() if len(families) == 1 else None


def names_client_default(client, model, registry=None, cfg=None) -> bool:
    """Whether ``model`` IS the model this own-account client serves as its default.

    The client-default layer reads a family off the account's default model, and that
    says something about what served only when the id in hand names that default: no
    model was named at all (the client picks its own), the default's own name, or one
    of the account's internal keys for it (`clients.CLIENT_DEFAULT_KEYS` — what a
    qoder transcript writes for the Qwen its account serves). Every other id is a
    model the caller pinned, and the account's default is a fabrication about IT:
    `--model widget-9` on qoder answers null, not qwen (AO-RUN-FAMILY-RECORD REJECT
    1, the sibling of the provider-qualified refusal above — both say the account's
    default is evidence about the account, not about whatever else was typed).
    """
    text = _family_spelling(model)
    if not text:
        return True
    default, _source = effective_spawn_model(client, registry=registry, cfg=cfg)
    spellings = [default] + list(clients.CLIENT_DEFAULT_KEYS.get(client) or ())
    return any(text == _family_spelling(spelling)
               for spelling in spellings if spelling)


def family_is_planned(source) -> bool:
    """True when ``source`` says the family is the PLAN's claim, not a witness's.

    One question, one home, because three readers ask it: the fence that names a
    run's author family, the footer that prints a review's independence, and the
    record a keeper reads a reviewer token out of. A planned family is information
    about what the launch line asked for — it is never evidence about what wrote a
    diff, so it opens no gate (AO-RUN-FAMILY-RECORD REJECT 3). A record that named no
    source at all is a record written before the field existed and is NOT disqualified
    here: the rule refuses a claim the record itself says is the plan's, it does not
    invent one about a record that stayed silent."""
    return resolver.family_key(source) == FAMILY_SOURCE_PLANNED


def witnessed_family(family, family_source, family_reason, proven) -> dict:
    """The `family` / `family_source` / `family_reason` fields a record carries.

    ``proven`` is whether a witness named the MODEL (`WRITER_PROVEN_SOURCES`). When
    it did, the layer that read the family off that served id is the claim's strength
    (`registry-row`, `model-vendor-name`, …). When it did not, no model was witnessed
    and no layer ever saw one either: the family the plan resolved travels as
    `planned-model`, so a reader holding only the record cannot mistake "the launch
    line said Qwen" for "a Qwen wrote this" (REJECT 3). A null family keeps the reason
    none of the layers answered, whatever the witness — exactly one of the two fields
    is ever present, and `unresolved` is a display marker, never a family (D-807).
    """
    if family is None:
        return {"family": None, "family_reason": family_reason}
    return {"family": family,
            "family_source": family_source if proven else FAMILY_SOURCE_PLANNED}


def route_family(route_id, registry, unique_legs=False) -> str | None:
    """The ONE family every leg of a route declares, or None when they differ.

    A combo is a fall-through list, so a chain whose legs sit in two families names
    neither, and a leg the registry cannot place makes the whole route unknown —
    the same rule `fence_blocks_route` applies to a chain. `unique_legs` reads each
    leg with the fence's own unique rule instead of the longest-token one (S1 F2
    round 2); the record's read keeps the default.
    """
    entry = (registry.get("routes") or {}).get(route_id) if route_id else None
    legs = (entry or {}).get("legs") or []
    family = None
    for leg in legs:
        try:
            _provider_id, model_id = resolve_leg(leg, registry)
        except ValueError:
            model_id = None  # a leg the registry cannot place names no family
        spelling = model_id or leg
        leg_read = vendor_family_name_unique if unique_legs else vendor_family_name
        key = (_family_of_one_spelling(spelling, registry) or leg_read(spelling))
        if not key:
            return None
        if family is None:
            family = key
        elif family != key:
            return None
    return family


def run_model_family(model, client=None, provider=None, registry=None,
                     cfg=None) -> tuple:
    """``(family, source, reason)`` for the model a run served — two of three, ever.

    The layers, ordered by how directly each names what ANSWERED:

      1. the registry's own declaration for the served spelling — `reviewer_family`,
         the one model→family lookup the record and the reviewer walk share, which
         also reads a client's `claude-<name>-<version>` id back to its bare name;
      2. the vendor named inside the model id (`vendor_family_name`);
      3. the route the spelling names, when every leg declares one family;
      4. the provider half, when the model half named nothing;
      5. an own-account client's own default model, and finally the client's own
         name — reached only for a model the client picked itself. A
         provider-qualified id (`--free somevendor/model`) is that vendor's model,
         not the account's own business, and the client's default family would be a
         fabrication read off the wrong half.

    ``reason`` says what was tried when the answer is None, and a None family keeps
    every cross-family check closed: an unknown family is never evidence of
    independence, exactly as an unknown author is not.
    """
    registry = registry if registry is not None else load_live_registry()
    text = str(model or "").strip()
    if text == PLAN_MODEL_UNNAMED:
        text = ""
    # The id as this client writes it (the bare name), and whether a vendor prefix
    # rode with it — the prefix is what says "this model is not the account's own".
    bare = text.rpartition("/")[2] if "/" in text else text
    qualified = "/" in text
    if text:
        family = reviewer_family(text, registry) or reviewer_family(bare, registry)
        if family:
            return family, FAMILY_SOURCE_REGISTRY, None
        family = vendor_family_name(bare)
        if family:
            return family, FAMILY_SOURCE_MODEL_NAME, None
        family = route_family(_combo_of(text), registry)
        if family:
            return family, FAMILY_SOURCE_ROUTE_LEGS, None
    if provider and str(provider).strip():
        family = vendor_family_name(provider)
        if family:
            return family, FAMILY_SOURCE_PROVIDER_NAME, None
    own_account = bool(client) and not _gateway_client(client)
    if own_account and not qualified and \
            names_client_default(client, bare, registry=registry, cfg=cfg):
        default, _source = effective_spawn_model(client, registry=registry, cfg=cfg)
        family = (reviewer_family(default, registry) if default else None) or \
            vendor_family_name(default)
        if family:
            return family, FAMILY_SOURCE_CLIENT_DEFAULT, None
        family = vendor_family_name(client)
        if family:
            return family, FAMILY_SOURCE_CLIENT_NAME, None
    return None, None, ("no registry row, vendor name or client default names a "
                        "family for model %s client %s provider %s"
                        % (text or "none", client or "none", provider or "none"))


def is_final_reviewer(spelling):
    """True when ``spelling`` NAMES the final checker, stripped and lower-cased.

    One helper owns the answer so the gate and any future caller agree on what
    "Sonnet signed this off" means. A substring is not a name: matching
    ``FINAL_REVIEWER in reviewer`` let ``notsonnet`` pass (REVGATE2, HIGH)."""
    name = (spelling or "").strip().lower()
    return name == FINAL_REVIEWER or bool(FINAL_REVIEWER_RE.match(name))


# P2/G1a: `evidence=<path>` points at the seat's own answer, so a READY claim in the
# record is checked against the file the reviewer wrote. No field, no change of behaviour.
EVIDENCE_DIR = os.path.join("logs", "briefs", "evidence")
EVIDENCE_MAX_BYTES = 1 << 20  # P2-FIX2: the gate reads a seat's answer, not a disk


def _resolve_evidence_path(root, raw):
    """`(path, None)` for an evidence file the repo owns, `(None, reason)` otherwise.

    A `..` in any component and any realpath that leaves the repo — or the evidence dir
    under it — is refused: a gate that reads whatever file a record names is a file
    reader, not a gate.
    """
    parts = [p for p in re.split(r"[\\/]+", raw) if p]
    if not parts or ".." in parts:
        return None, "evidence path outside the repo: %s" % raw
    candidate = raw if os.path.isabs(raw) else os.path.join(root, *parts)
    real = os.path.normcase(os.path.realpath(candidate))
    for allowed in (os.path.realpath(root),
                    os.path.realpath(os.path.join(root, EVIDENCE_DIR))):
        allowed = os.path.normcase(allowed)
        if real == allowed or real.startswith(allowed.rstrip(os.sep) + os.sep):
            return candidate, None
    return None, "evidence path outside the repo: %s" % raw


def _evidence_reason(entry, root):
    """Why this entry's evidence file does not back its verdict, or None when it does:
    it must sit inside the repo, be readable, be a valid `parse_verdict` answer, and say
    the word the record claims — PASS/SHIP/LGTM being one ACCEPT in different prose."""
    raw = (entry.get("evidence") or "").strip()
    if not raw:
        return None
    # P2-FIX2: attacker text from a lane record, and the syscalls below raise on it.
    try:
        path, refused = _resolve_evidence_path(root or os.getcwd(), raw)
    except (ValueError, OSError) as exc:
        return "evidence path invalid: %s" % type(exc).__name__
    if refused:
        return refused
    # P2-FIX3 (AO-SEAT-VERDICT-GRAMMAR): fd-only read — a fifo opened blocking hangs
    # the gate forever and a raced symlink misdirects it; O_NONBLOCK|O_NOFOLLOW fall
    # back to 0 where absent (Windows import), the cap is st_size AND bytes read.
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return "evidence not a regular file"
        if st.st_size > EVIDENCE_MAX_BYTES:
            return "evidence too large"
        chunks, total = [], 0
        while total <= EVIDENCE_MAX_BYTES:
            data = os.read(fd, min(EVIDENCE_MAX_BYTES + 1 - total, 1 << 16))
            if not data:
                break
            chunks.append(data)
            total += len(data)
        if total > EVIDENCE_MAX_BYTES:
            return "evidence too large"
        text = b"".join(chunks).decode("utf-8")
    except (ValueError, OSError) as exc:
        why = errno.errorcode.get(getattr(exc, "errno", None)) or type(exc).__name__
        # ELOOP (O_NOFOLLOW) and ENXIO (socket file) raise at open, before fstat.
        return ("evidence file missing: %s" % raw if why == "ENOENT" else
                "evidence not a regular file" if why in ("ELOOP", "ENXIO")
                else "evidence unreadable: %s" % why)
    finally:
        if fd is not None:
            os.close(fd)
    parsed = seat_verdict.parse_verdict(text)
    if not parsed["valid"]:
        return "evidence invalid: %s" % parsed["reason"]
    word = (seat_verdict.verdict_word(entry.get("verdict")) or "").upper()
    if parsed["verdict"] != ("ACCEPT" if word.lower() in READY_VERDICTS else word):
        return "evidence verdict %s != entry %s" % (parsed["verdict"], word or "missing")
    return None


def _review_entry_verdict(entry, root=None):
    """``(ok, reason)`` for one entry's verdict field, read through the seat grammar
    (AO-SEAT-VERDICT-GRAMMAR): `verdict=**ACCEPT**` is `verdict=ACCEPT`, a finding (REJECT,
    HOLD, fix-first) refuses, every legacy READY_VERDICTS word still works. When the entry
    names an `evidence=` file, that file has to say the same thing (P2/G1a)."""
    raw = (entry.get("verdict") or "").strip()
    if (seat_verdict.verdict_word(raw) or "").lower() not in READY_VERDICTS:
        return False, "verdict %s" % (raw or "missing")
    refused = _evidence_reason(entry, root)
    if refused:
        return False, refused
    return True, None


def _cross_family_seat(entry, registry, root=None):
    """``(seat, reason)`` for one kind=cross-family entry.

    A *seat* is one entry that counts: a reviewer the registry places, an author
    the registry places, two different families, a declared ``family=`` that
    agrees with the registry, and a READY verdict. Anything else is a reason the
    entry does not count, said out loud -- an unplaced name is never evidence of
    independence (REVFIX S2), and a non-READY verdict is a finding, not a seat.
    """
    reviewer = entry["reviewer"]
    family = reviewer_family(reviewer, registry)
    if family is None:
        return None, ("%s is not a known reviewer (policy.reviewers or models)"
                      % reviewer)
    author, why = resolver.author_family(entry.get("author") or "", registry)
    if author is None:
        # REVFIX S2: an author the registry cannot place is NOT treated as a
        # family of its own. "Who wrote this" unanswered is not evidence that
        # the reviewer is someone else, so the check fails and says so.
        return None, "author: %s" % why
    if author == family:
        return None, "%s is the same family as the author (%s)" % (reviewer, family)
    declared = (entry.get("family") or "").strip()
    if declared and resolver.family_key(declared) != family:
        # The optional family= is a cross-check, not a claim (REVGATE2F): when a
        # record prints one it must be the family the registry derives, or the
        # seat is refused and the mismatch names both sides.
        return None, ("%s declares family=%s but the registry says %s"
                      % (reviewer, declared, family))
    ok, reason = _review_entry_verdict(entry, root)
    if not ok:
        return None, "%s %s" % (reviewer, reason)
    return {"reviewer": reviewer, "family": family,
            "verdict": (entry.get("verdict") or "").strip()}, None


def _cross_family_review(entries, registry, root=None):
    """The record's independent review: >=2 READY seats from DISTINCT families.

    REVGATE2F (operator 2026-09-30): the gate used to pass on the FIRST valid
    entry, so one seat plus the final read as reviewed -- and two spellings of
    one family read as two seats. The floor is two counted seats whose families
    differ and are not the author's, each READY. There is no cap: past three
    seats the operator's 2-3 guidance is printed as a non-blocking note, never a
    refusal. Missing and duplicated seats are both named in the detail, because
    "try again" without names sends someone to book a review that already
    happened.
    """
    wanted = [e for e in entries if e.get("kind") == "cross-family"]
    if not wanted:
        return {"ok": False, "families": [], "seats": [],
                "detail": "no AutoOS-Review: kind=cross-family entry"}
    reasons, seats = [], []
    for entry in wanted:
        seat, reason = _cross_family_seat(entry, registry, root)
        if reason is not None:
            reasons.append(reason)
            continue
        same = [s for s in seats if s["family"] == seat["family"]]
        if same:
            reasons.append("reviewers %s and %s are the same family (%s)"
                           % (same[0]["reviewer"], seat["reviewer"], seat["family"]))
            continue
        seats.append(seat)
    if len(seats) < 2:
        reasons.append("2 cross-family seats required; have %d" % len(seats))
    if reasons:
        return {"ok": False, "families": [s["family"] for s in seats],
                "seats": seats, "detail": "; ".join(reasons)}
    detail = "%d cross-family seats: %s" % (
        len(seats), ", ".join("%s (%s)" % (s["reviewer"], s["family"]) for s in seats))
    if len(seats) > 3:
        detail += ("; note: %d seats, beyond the operator's 2-3 guidance -- no cap"
                   % len(seats))
    return {"ok": True, "families": [s["family"] for s in seats],
            "seats": seats, "detail": detail}


def _final_review(entries, root=None):
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
        ok, reason = _review_entry_verdict(entry, root)
        if ok:
            return {"ok": True,
                    "detail": "%s verdict %s" % (entry["reviewer"], entry.get("verdict"))}
        reasons.append("%s %s" % (entry["reviewer"], reason))
    return {"ok": False, "detail": "; ".join(reasons)}


def review_status(text, registry, root=None):
    """Which of the two reviews a lane record carries, read off the record itself.

    An item 2 spawn has already proved a reviewer EXISTS for this card; this is
    the other half -- proof it RAN and said something, in the file that gets
    merged. A same-family reviewer, an unknown reviewer spelling and a verdict
    that says FIX-FIRST all fail, and each says which, because "missing" would
    send someone to book a review that already happened and did not pass. The
    floor is two seats from distinct families (REVGATE2F): one entry is no
    longer a review, and two spellings of one family are one seat.
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
    cross = _cross_family_review(entries, registry, root)
    final = _final_review(entries, root)
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
        # REVGATE2F: every counted seat prints as model (family) + verdict, so
        # "which reviews" is answerable from the output without re-reading the
        # record line by line.
        for number, seat in enumerate(item.get("seats") or [], 1):
            print("  seat %d: %s (%s) verdict=%s"
                  % (number, seat["reviewer"], seat["family"], seat["verdict"]))
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
    report = review_status(text, registry, root=os.getcwd())
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
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
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


def main_ci_status(repo=None, runner=None):
    """``(conclusion, run_id, error)`` — what main's latest completed CI run says.

    WHY: main was red on 13 consecutive pushes while takes were merged anyway,
    so a lane that lands on a red main certifies a commit against a broken base
    (source: plan v3 T0-FREEZE). The gate is fail-closed: an unreadable gh is
    exit 2, never an allow.

    ``gh run list --branch main --status completed --limit 1 --workflow
    ci.yml --event push --json
    databaseId,conclusion,headSha``, through the same injectable-runner pattern
    ``ci_run_status`` uses for ``--ci-run`` (no new network style). Unlike ``gh
    run view``'s single object, ``gh run list`` prints a JSON ARRAY of such
    objects; the head row carries the latest completed run on main. Mirrors
    ``ci_run_status``: a non-None ``error`` means the question was never
    answered, which is a different exit code from a run that came back red.

    T0-FREEZE-5 H6: ``repo`` is the lane's repo directory -- the same
    ``args.repo or os.getcwd()`` every other gate in ``cmd_ready`` reads --
    and gh runs with cwd=<that dir>. WHY: ``gh run list`` resolves the
    repository from the PROCESS working directory, so without this the gate
    evaluated the wrong repo's main CI whenever ``--repo`` named another
    checkout. A dir that is not a git repo makes gh itself fail, which stays
    fail-closed (non-None ``error`` -> exit 2). Repo-first/runner-second keeps
    the pre-H6 ``lambda runner=None`` stubs working: a positional repo binds
    their single parameter; a positional runner (callable) is still honoured.
    The runner gains the ``cwd`` kwarg; existing ``def runner(argv, **kw)``
    fakes accept it untouched.
    """
    if callable(repo) and runner is None:
        runner, repo = repo, None
    if repo is None:
        repo = os.getcwd()
    runner = runner or subprocess.run
    argv = ["gh", "run", "list", "--branch", "main", "--status", "completed",
            "--limit", "1", "--workflow", "ci.yml", "--event", "push",
            "--json", "databaseId,conclusion,headSha"]
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=60,
                      cwd=repo)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "%s" % exc
    if proc.returncode != 0:
        return None, None, ((proc.stderr or proc.stdout or "").strip()
                            or "gh run list exited %d" % proc.returncode)
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        return None, None, "gh run list printed output that is not JSON: %s" % exc
    if not isinstance(data, list) or not data:
        return None, None, "gh run list answered with no completed runs on main"
    row = data[0]
    if not isinstance(row, dict):
        return None, None, "gh run list answered with a row that is not JSON: %r" % (row,)
    run_id = row.get("databaseId")
    if run_id is None:
        return None, None, ("gh run list answered with no databaseId (conclusion %r)"
                            % row.get("conclusion"))
    conclusion = row.get("conclusion")
    if not conclusion:
        return None, None, ("gh run list answered with no conclusion for run %s" % run_id)
    return conclusion, str(run_id), None


LANE_DIFF_MAX_BYTES = 2 << 20  # 2 MiB of git output; more is not a lane diff


class LaneDiffRefusal(Exception):
    """The lane diff ANSWERED the gate and the answer was no (P4b-fixes D2).

    ``lane_diff_paths`` *returns* an ``error`` when git could not be read at all
    -- which is `ready`'s exit 2, "the check never ran" -- and raises this when
    the check ran and the lane failed it, which is exit 1: a ``--base`` that is
    not a strict ancestor of ``--sha`` (the same commit included), and a range
    with nothing in it. Both read as an empty diff, and an empty diff read as
    "touched nothing risky", which is how ``--base == --sha`` walked a lane
    straight past the writer guards."""


def _git_out(raw, errors):
    """Decode one git buffer, leniently (P4b-fixes D3).

    git runs in bytes mode here on purpose. With ``text=True`` a lane diff that
    carries a non-UTF-8 path or any binary-adjacent line makes CPython raise
    UnicodeDecodeError *inside* ``subprocess.run`` -- a ValueError this gate's
    ``except`` does not name -- so the caller got a traceback where it was owed a
    verdict. ``surrogateescape`` is for the path list, so an odd byte is a NAME
    that survives to the scope fence instead of being renamed into a match or a
    miss (``lane_diff_paths`` asks git for it unquoted -- ``-z`` plus
    ``core.quotepath=false`` -- because a plain ``--name-only`` C-quotes it into
    ``"ops/caf\\303\\251.yml"``, a string of literal quotes and octal escapes no
    path regex matches); ``replace`` is enough for the diff body, which is only
    ever pattern-matched for risk tokens. git's
    own buffers are bytes, so a runner injected by a test hands bytes too.
    """
    return (raw or b"").decode("utf-8", errors)


def _diff_range(base, sha):
    """The one rev-range argument git will read, or None when either side is not
    a plain rev token. Git takes it as a single POSITIONAL argument, never
    through a shell, so the only live risk is a value that starts with `-` and is
    parsed as an option instead of a revision; empty, dash-leading and
    whitespace/control-carrying values are refused here rather than handed down.
    """
    for side in (base, sha):
        if (not isinstance(side, str) or not side or side.startswith("-")
                or side != side.strip() or any(ord(c) < 0x21 or c == "\x7f" for c in side)):
            return None
    return "%s...%s" % (base, sha)


def lane_diff_paths(repo, base, sha):
    """``(paths, raw_diff, error)`` -- what the lane's own diff changed.

    The reads of one range, in this order, each of them fail-closed:

      1. ``git rev-parse --verify <base>^{commit}`` and the same for ``<sha>``
         -- both sides have to name a commit;
      2. ``git merge-base --is-ancestor <base-oid> <sha-oid>`` -- the base has to
         sit BEHIND the sha, and (P4b-fixes D2) the two oids must differ, because
         a commit is its own ancestor: ``--base`` equal to ``--sha`` makes
         ``<sha>...<sha>`` empty, an empty diff means ``ops_required`` False, and
         the lane clears a gate it never stood in front of. A range that is not
         an ancestor, or is empty, raises LaneDiffRefusal (exit 1); a git that
         cannot answer is an ``error`` (exit 2);
      3. ``git -c core.quotepath=false diff -z --name-only --no-renames
         <base>...<sha>`` for the file list -- ``--no-renames`` so a rename
         arrives as both of its paths and the scope fence sees each half, and
         ``-z`` (P4b-fixes D4) because the default ``--name-only`` answer is a
         *quoted* rendering, not the name: git C-quotes a non-ASCII path into
         ``"ops/caf\\303\\251.yml"`` and a path with a tab, newline, quote or
         backslash into an escaped, quoted string -- and neither spelling matches
         the ops pattern, so a lane that added ``ops/café.yml`` was measured
         ``docs``/R1 and never stood in front of the writer guards. ``-z`` makes
         every record the exact path bytes, NUL-terminated, quoting off by
         construction (``core.quotepath=false`` is what keeps the same bytes
         unquoted in the body's headers);
      4. ``git -c core.quotepath=false diff -U0 --no-color <base>...<sha>`` for
         the RAW body, handed back verbatim (P4b-fixes D1).

    That last point is the whole reason the body is not reduced here:
    ``autoos_writer_rule._added``, which decides the risk level, is written for
    RAW ``git diff`` text and strips the leading '+' itself -- and a text whose
    lines *already* had the '+' stripped is read as a diff whose only added lines
    are the ones that happen to start with '+', '--- ', '@@' or 'diff '. So a real
    added line ``token: x`` sitting under one such source line disappeared from
    the judge's view entirely and an ops lane was waved through as docs. The
    ``--no-color`` keeps escape sequences out of the same text.

    Fail closed like every other unreadable gate: a git that errors, times out,
    cannot start, or prints more than LANE_DIFF_MAX_BYTES returns a non-None
    ``error`` (exit 2 at the caller, reading "cannot read diff" / "diff too large
    to judge") and never a half-read allow-list. The argvs are written out
    literally, not built in a loop, because the spawner's own subprocess audit
    (FF1b item 6) only exempts a *literal* ``git`` call as plumbing -- a variable
    called argv is not git, and this reads the operator's own checkout as the
    operator, like ``remote_branch_tip`` does. Injectable -- and REQUIRED to be
    injected in tests (HERMETIC, D-852): the ready tests stub it exactly as they
    stub ``remote_branch_tip`` and ``main_ci_status``, so no cmd_ready test ever
    shells out.
    """
    rng = _diff_range(base, sha)
    if rng is None:
        return None, None, ("cannot build a diff range from base %r and sha %r "
                            "(both must be plain rev tokens)" % (base, sha))
    try:
        resolves_base = subprocess.run(["git", "-C", repo, "rev-parse", "--verify",
                                        "%s^{commit}" % base], capture_output=True,
                                       timeout=60, stdin=subprocess.DEVNULL)
        resolves_sha = subprocess.run(["git", "-C", repo, "rev-parse", "--verify",
                                        "%s^{commit}" % sha], capture_output=True,
                                       timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "git rev-parse: %s" % exc
    for label, proc in (("base", resolves_base), ("sha", resolves_sha)):
        if proc.returncode != 0:
            return None, None, ((_git_out(proc.stderr, "replace").strip())
                                or "git rev-parse --verify %s exited %d"
                                   % (label, proc.returncode))
    base_oid = _git_out(resolves_base.stdout, "surrogateescape").strip()
    sha_oid = _git_out(resolves_sha.stdout, "surrogateescape").strip()
    if not base_oid or not sha_oid:
        return None, None, ("git rev-parse --verify answered with no commit id "
                            "for base %r / sha %r" % (base, sha))
    if base_oid == sha_oid:
        raise LaneDiffRefusal(
            "writer-guards: --base must be a strict ancestor of --sha (%s and %s "
            "are both %s; an empty range fences nothing)"
            % (base, sha, sha_oid[:12]))
    try:
        ancestor = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor",
                                   base_oid, sha_oid], capture_output=True,
                                  timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "git merge-base: %s" % exc
    if ancestor.returncode not in (0, 1):
        return None, None, ((_git_out(ancestor.stderr, "replace").strip())
                            or "git merge-base --is-ancestor exited %d"
                               % ancestor.returncode)
    if ancestor.returncode == 1:
        raise LaneDiffRefusal(
            "writer-guards: --base must be a strict ancestor of --sha (%s is not "
            "behind %s)" % (base, sha))
    try:
        named = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=false",
                                "diff", "-z", "--name-only",
                                "--no-renames", rng], capture_output=True,
                               timeout=60, stdin=subprocess.DEVNULL)
        body = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=false",
                               "diff", "-U0", "--no-color", rng],
                              capture_output=True, timeout=60,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "git diff: %s" % exc
    for label, proc in (("-z --name-only", named), ("-U0", body)):
        if proc.returncode != 0:
            return None, None, ((_git_out(proc.stderr, "replace").strip())
                                or "git diff %s exited %d" % (label, proc.returncode))
        size = len(proc.stdout or b"")
        if size > LANE_DIFF_MAX_BYTES:
            return None, None, ("diff too large to judge: git diff %s printed %d "
                                "bytes, over the %d limit"
                                % (label, size, LANE_DIFF_MAX_BYTES))
    # P4b-fixes D4: NUL is the separator, so a name with a newline in it is one
    # record, not two. git terminates the last record with a NUL too, which leaves
    # one trailing empty string -- dropped, like every empty record.
    paths = [p for p in _git_out(named.stdout, "surrogateescape").split("\x00") if p]
    if not paths:
        raise LaneDiffRefusal(
            "writer-guards: empty lane diff -- %s is a strict ancestor of %s and "
            "nothing changed between them; a lane with no change is not ready"
            % (base, sha))
    return paths, _git_out(body.stdout, "replace"), None


def read_guards_text(path):
    """``(text, error)`` -- one brief or report file, read whole or refused.

    A file the gate cannot read (missing, a directory, undecodable bytes) is the
    exit-2 shape, the same as an unreadable record: the caller has to know the
    check never ran, not that it passed.
    """
    try:
        with io.open(path, encoding="utf-8") as fh:
            return fh.read(), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, "%s" % exc


def cmd_ready(args) -> int:
    """Write the `ready` line an orchestrator used to type by hand.

    Seven gates, in this order, each naming itself when it fails: the record
    carries both reviews (``review_status``), ``--sha`` is what ``origin`` holds
    for ``--branch``, and — when ``--ci-run`` names one — that GitHub Actions run
    finished ``success`` with ``headSha`` equal to ``--sha``, so the line cannot
    certify a commit the gate never tested (SPAWNFIX3 item 6; the run id rides
    on the line as ``ci=<id>``). Without ``--ci-run`` the lane is still allowed
    and one note says the gate was skipped. The fifth is the pre-push gate's own
    record (D-110): the reviews say the lane was looked at and the sha says it
    shipped, but only that record says it was *run*, so a lane pushed with
    ``git push --no-verify`` — which steps over every hook — is refused here
    unless an orchestrator names a reason with ``--allow-unverified``. The sixth
    (T0-FREEZE, plan v3) refuses while main CI is red: the latest completed
    push run of the CI workflow on ``main`` must conclude ``success``, else the lane is refused
    as ``main-ci-red`` naming the run id — unless the lane fixes main itself,
    declared with ``--fixes-main`` AND the environment declaring
    ``AUTOOS_FIXES_MAIN=<lane>@<sha>`` whose sha equals ``--sha`` (T0-FREEZE-2:
    a bare flag, or a declaration for a different sha, is refused as
    ``waiver-not-declared`` saying the waiver was not declared and the CI was not
    consulted; a declared waiver is
    logged as ``<lane>@<sha>`` on the ready line's note and as
    ``fixes_main="<lane>@<sha>"`` on the ready line itself, copying the
    ``unverified="<reason>"`` style so the inbox timestamp parser still
    reads the line). The bypass is a CLI
    flag plus an orchestrator-owned env declaration, not a record
    field, because the record's machine-readable vocabulary is the closed
    ``REVIEW_ENTRY_FIELDS`` list and a free-form token there would be ignored
    prose. The gates
    live in code because the hand-written claim was wrong once -- L1-main refused
    a `ready` line whose record had no reviews (inbox 00:31:52Z).

    Exit 0 the line was written (or, with --dry-run, would be), 1 a gate is not
    met, 2 a gate could not be read (unreadable record, git or gh failure,
    unwritable inbox).

    WRITER-GUARDS (AO-WRITER-GUARDS P4b) sits between the pushed-sha gate and the
    CI gate and reads the lane's own diff (`lane_diff_paths`, injected from
    `--base`, default `origin/main`) rather than trusting the card: ops work is
    decided by `requires_ops_guards` from the real path list and the RAW diff text
    (`git diff -U0 --no-color`, handed over unstripped — P4b-fixes D1: the risk
    judge strips the leading '+' itself, and pre-stripping it let one source line
    starting with '+', '--- ', '@@' or 'diff ' hide every other added line from the
    check), so a `docs`/`code` label cannot skip it. `--base` must resolve to a
    commit that is a STRICT ancestor of `--sha` (P4b-fixes D2): the same commit, a
    base that does not sit behind the sha, or a range that changed nothing is
    refused at exit 1 as `writer-guards: --base must be a strict ancestor of --sha`
    / `writer-guards: empty lane diff` — an empty diff otherwise reads as "touched
    nothing risky" and clears the gate. When the diff says ops, `--brief` and
    `--report` are required — AO-READY-CALLERS is that every CALLER passes both on
    every run, the requirement being decided from the diff, which is the one thing a
    caller has not read; whenever `--brief` is given the brief's canonical
    FILES line becomes the allow-list and `scope_fence` refuses any touched path
    outside it, naming every violation. An ops lane's REPORT must carry CHECK
    1-6 with a PASS verdict and evidence (`report_checks`). A GuardError from any
    helper refuses (exit 1, fail closed); a diff or brief/report file the gate
    cannot read — including a diff over the 2 MiB cap, which reads `diff too large
    to judge` — is exit 2. A lane that clears the guards appends ` guards=ok` to
    the ready line (` ops=1` too for an ops lane); a lane that triggers nothing
    appends no new field, and `preflight` (the same module's tool check, exit 3 on
    input_required) tells the operator whether the guard tools exist at all."""
    try:
        text, label = read_lane_record(args.record)
    except OSError as exc:
        print("ready: %s" % exc, file=sys.stderr)
        return 2
    registry = load_registry(args.registry or REGISTRY_PATH)
    report = review_status(text, registry, root=args.repo or os.getcwd())
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
    # AO-WRITER-GUARDS P4b (writer-ops.md §4-5, helpers in
    # tools/autoos_ready_guards.py; the gate is documented in this function's
    # docstring): ops work is decided from the DIFF, not from the card's label --
    # a `docs` card that edited `playbooks/x.yml` is an ops lane. git failing to
    # answer the diff, or an unreadable --brief/--report file, is exit 2 like
    # every other unreadable gate: "the check never ran" is not "the lane failed
    # it", and a writer that cannot be judged must not be allowed. A range the
    # reader refuses (P4b-fixes D2: `--base` not a strict ancestor of `--sha`, or
    # an empty diff) is the lane failing it -- exit 1, never exit 0.
    guards_fields = ""
    brief_arg = getattr(args, "brief", None)
    report_arg = getattr(args, "report", None)
    base = getattr(args, "base", None) or "origin/main"
    task_type = getattr(args, "task_type", None) or "code"
    try:
        diff_paths, diff_raw, diff_error = lane_diff_paths(args.repo or os.getcwd(),
                                                           base, args.sha)
    except LaneDiffRefusal as exc:
        print("ready: not appended -- %s" % exc, file=sys.stderr)
        return 1
    if diff_error:
        print("ready: cannot read diff: %s" % diff_error, file=sys.stderr)
        return 2
    try:
        # P4b-fixes D1: the RAW diff body, not the added lines pre-stripped of
        # their '+'. `requires_ops_guards` -> `required_r_level` -> `_added` reads
        # diff text and does that strip itself.
        ops_required = ready_guards.requires_ops_guards(task_type, paths=diff_paths,
                                                        diff_text=diff_raw)
    except (ready_guards.GuardError, ValueError) as exc:
        print("ready: not appended -- writer-guards: %s" % exc, file=sys.stderr)
        return 1

    def guards_command():
        """The exact call that clears this lane's guard gate, its own arguments filled
        in (AO-READY-CALLERS): a refusal naming only a missing flag is one the caller
        has to guess its way out of."""
        return ("python3 tools/autoos-agent.py ready %s --branch %s --sha %s "
                "--inbox %s --brief <BRIEF path> --report <REPORT path>"
                % (args.record, args.branch, args.sha, args.inbox))

    if brief_arg or ops_required:
        if not brief_arg:
            print("ready: not appended -- writer-guards: ops lane without --brief/--report; "
                  "the diff reaches R2, so the rendered brief is what names the "
                  "files this lane was allowed to touch. Run: %s" % guards_command())
            return 1
        brief_text, brief_error = read_guards_text(brief_arg)
        if brief_error:
            print("ready: cannot read --brief %s: %s" % (brief_arg, brief_error),
                  file=sys.stderr)
            return 2
        try:
            allowed = ready_guards.brief_files(brief_text)
            violations = ready_guards.scope_fence(diff_paths, allowed)
            if violations:
                print("ready: not appended -- writer-guards scope-fence: the diff "
                      "touched files outside the brief's FILES line: %s"
                      % ", ".join(violations))
                return 1
        except ready_guards.GuardError as exc:
            print("ready: not appended -- writer-guards: %s" % exc, file=sys.stderr)
            return 1
        guards_fields = " guards=ok"
        if ops_required:
            if not report_arg:
                print("ready: not appended -- writer-guards: ops lane without "
                      "--report; the REPORT is where the CHECK evidence lives")
                return 1
            report_text, report_error = read_guards_text(report_arg)
            if report_error:
                print("ready: cannot read --report %s: %s" % (report_arg, report_error),
                      file=sys.stderr)
                return 2
            try:
                checks = ready_guards.report_checks(report_text)
                if not checks["ok"]:
                    def named(ids):
                        return ", ".join(str(n) for n in ids) or "none"
                    print("ready: not appended -- writer-guards report-checks: "
                          "missing CHECK %s; failed CHECK %s; input_required "
                          "CHECK %s" % (named(checks["missing"]),
                                        named(checks["failed"]),
                                        named(checks["input_required"])))
                    return 1
            except ready_guards.GuardError as exc:
                print("ready: not appended -- writer-guards: %s" % exc, file=sys.stderr)
                return 1
            guards_fields += " ops=1"
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
    # D-110 (PREPUSH, operator D-154): the reviews and the pushed sha say the lane
    # was looked at; only the pre-push gate's record says it was ever TESTED. A lane
    # pushed with `git push --no-verify` steps over every hook, and this is where it
    # is caught — the record is the one artefact that cannot be faked by luck.
    green = prepush_mod.local_green(args.sha, repo=args.repo or os.getcwd())
    unverified_field = ""
    if not green:
        waived = " ".join((getattr(args, "allow_unverified", None) or "").split())
        if not waived:
            print("ready: not appended -- %s has no green pre-push record (D-110). "
                  "The gate never ran for this sha: it was pushed with "
                  "`git push --no-verify`, or from a host that never ran "
                  "`python3 tools/prepush.py`. Run the gate, or declare the lane "
                  "with --allow-unverified \"<reason>\" (an orchestrator's flag, not "
                  "the writer's)." % args.sha[:12])
            return 1
        unverified_field = ' unverified="%s"' % waived
        print("note: --allow-unverified -- %s carries no green pre-push record, and "
              "the reason written on the line is: %s" % (args.sha[:12], waived))
    # T0-FREEZE (plan v3): no lane merges while main CI is red, except the lane
    # that fixes main. Runs after the pre-push gate so each refusal names the
    # gate the caller actually hit; fail-closed like every other unreadable gate.
    # T0-FREEZE-2 F1: --fixes-main alone waives nothing. The waiver is honoured
    # ONLY when the environment declares AUTOOS_FIXES_MAIN=<lane>@<sha> whose
    # sha equals the --sha being readied — a bare flag, or a declaration for a
    # different sha, is refused here naming waiver-not-declared. WHY the env dance: the
    # flag is typed by whoever runs the command (including a writer clearing
    # its own gate), while the env is set by the orchestrator that owns the
    # lane — so only a lane whose owner declared the fix can claim it.
    # T0-FREEZE-3: the env value is stripped of surrounding whitespace before
    # parsing; the split partitions at the FIRST '@' (a second '@' stays in
    # the sha half, which then mismatches and is refused); the lane half must
    # equal args.branch EXACTLY or equal its basename after the last '/'
    # (documented: exact or basename), and a whitespace-only lane is refused
    # like a missing one. A sha-matching declaration for another lane is
    # refused naming waiver-not-declared and 'waiver lane mismatch'. The waiver field
    # copies the unverified="..." style so inbox readers still parse the line.
    fixes_waiver = ""
    fixes_field = ""
    if getattr(args, "fixes_main", False):
        declared = os.environ.get("AUTOOS_FIXES_MAIN", "").strip()
        lane, sep, declared_sha = declared.partition("@")
        if not sep or not lane or not lane.strip() or declared_sha != args.sha:
            print("ready: not appended -- waiver-not-declared: --fixes-main was given "
                  "but the waiver was not declared for this sha (want "
                  "AUTOOS_FIXES_MAIN=<lane>@%s); CI was not consulted; merge "
                  "nothing until main is green" % args.sha)
            return 1
        # T0-FREEZE-4: the waiver rides the durable line as fixes_main="<lane>@<sha>",
        # so a lane carrying a quote, a backslash, whitespace or a control
        # character would break the quoting (or the inbox parser). Refused here,
        # before it is written, naming the problem.
        if any(c == '"' or c == "\\" or c.isspace() or ord(c) < 0x20
               or ord(c) == 0x7f for c in lane):
            print("ready: not appended -- waiver-not-declared: waiver lane %r "
                  "cannot be written on the ready line (it contains a quote, "
                  "backslash, whitespace or control character); declare a "
                  "plain lane name" % lane)
            return 1
        want_exact = args.branch
        want_base = args.branch.rsplit("/", 1)[-1]
        if lane != want_exact and lane != want_base:
            print("ready: not appended -- waiver-not-declared: waiver lane mismatch: "
                  "AUTOOS_FIXES_MAIN declares lane %r but the lane being "
                  "readied is %r (want exact match or basename after the last "
                  "'/'); CI was not consulted; merge nothing until main is "
                  "green" % (lane, args.branch))
            return 1
        fixes_waiver = declared
        fixes_field = ' fixes_main="%s"' % fixes_waiver
    if not fixes_waiver:
        # T0-FREEZE-5 H6: positional repo binds pre-H6 `lambda runner=None`
        # stubs; the real function reads it as the gh cwd (see main_ci_status).
        main_conclusion, main_run_id, main_error = main_ci_status(
            args.repo or os.getcwd())
        if main_error:
            print("ready: cannot read main CI: %s" % main_error, file=sys.stderr)
            return 2
        if main_conclusion != "success":
            print("ready: not appended -- main-ci-red: main CI run %s is %s, "
                  "not success; merge nothing until main is green, or declare "
                  "the fix with --fixes-main AND env "
                  "AUTOOS_FIXES_MAIN=<lane>@<sha>" % (main_run_id, main_conclusion))
            return 1
    else:
        print("note: --fixes-main -- %s declares it fixes main, "
              "so the main-ci-red gate is waived" % fixes_waiver)
    line = "%s ready %s %s reviews: %s | %s%s%s%s%s" % (
        _iso_zulu(datetime.datetime.now(datetime.timezone.utc)),
        args.branch, args.sha,
        report["cross_family"]["detail"], report["final"]["detail"], ci_field,
        unverified_field, fixes_field, guards_fields)
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


def cmd_preflight(args, which=None) -> int:
    """Say whether the guard tools exist -- and install nothing.

    The operator's own check (AO-WRITER-GUARDS P4b, writer-ops.md §5): a ready
    gate that refuses for a missing tool is worthless if the lane then "fixes" it
    by skipping the check, so the answer carries the documented install step and
    the run ends `input_required` (exit 3) for a human to act on. Exit 0 when
    every required tool is on PATH, 3 when one is not, never anything else.
    ``which`` is injectable (the parsed args may carry it) so a test never looks
    at the host (HERMETIC, D-852); the lookup itself lives in
    tools/autoos_ready_guards.py.
    """
    which = which or getattr(args, "which", None) or shutil.which
    missing = ready_guards.tool_preflight(which=which)
    report = ready_guards.preflight_report(missing)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["state"] == "ok" else 3


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


class GeminiRefused(ValueError):
    """A run aimed at a Gemini model the operator's allow-list does not name (D-255)."""


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


# T0-PAID-2a1: the paid combos a --free run must never be labelled with, and the
# *-free-only twin that labels it instead. One home for the mapping; every twin
# is a declared route in catalog/ai-registry.json and a combo in
# configuration/omniroute/combos.json.
FREE_ONLY_COMBOS = {
    "l1-orchestrator": "l1-orchestrator-free-only",
    "l2-worker": "l2-worker-free-only",
    "l3-driver": "l3-driver-free-only",
}


def free_only_combo(combo):
    """The *-free-only twin of a paid combo, or the combo unchanged.

    WHY: a --free run pins the keyless opencode model (build_plan uses
    args.free_model; no leg of route["combo"] is ever resolved — FAMILYFENCE-3
    B1), so the combo is only ever a label. But select_combo knows nothing of
    free — it stays pure, since the MCP `route` tool takes only a card — and
    labelling that run `l1-orchestrator` names a combo with a paid tail the run
    never touches (T0-PAID-2a1). None (a --tier --free run names no combo) and
    combos without a twin (-clean, t4-rag, resolver ids, and paid combos with
    no free-only twin such as l2-orchestrator and l1-orchestrator-paid) pass
    through: the rule is that a free run is labelled with the paid combo's
    twin whenever a twin exists, not that every free run names one.
    """
    if combo is None:
        return None
    return FREE_ONLY_COMBOS.get(combo, combo)


def resolve_route(args, cfg: dict, client, exclude_routes: set | None = None,
                  provider_cooldown: dict | None = None,
                  fence: dict | None = None) -> dict:
    """resolve_route_unchecked plus the PRIV3 check: a sensitive run whose
    explicit --model replaced the card's combo must still land on private-safe
    legs only (--allow-training keeps its compatibility escape, which now only
    waives that explicit-override check - it no longer unlocks a trainable leg,
    since 2026-09-27). A --free run is relabelled to its *-free-only twin (see
    free_only_combo) so the recorded combo never names a paid leg it resolves."""
    route = resolve_route_unchecked(args, cfg, client, exclude_routes, provider_cooldown,
                                    fence)
    if args.free:
        twin = free_only_combo(route.get("combo"))
        if twin != route.get("combo"):
            route = dict(route, combo=twin)
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
    # D-255 (operator 2026-10-01): no Gemini Pro model, only Gemini 3.6/3.7/3.8
    # Flash. Same door as the PRIV3 read above, because it is the same question —
    # what this run is aimed at — asked of the model name, the hand entry behind it
    # and the route's legs.
    gemini = gemini_spawn_refusal(route.get("model"), route.get("combo"),
                                  load_registry(REGISTRY_PATH), cfg,
                                  explicit=bool(override))
    if gemini:
        raise GeminiRefused(gemini)
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
    tier = int(re.match(r"[tl](\d)-", combo).group(1))  # l2-worker-clean -> 2
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


# --- T2-RECORD-PIN item 2: what was asked, what would launch -----------------
#
# A record that only says which model the plan ended on cannot tell an ask from
# a default, and a plan that silently answers on a model nobody pinned is the
# bug this pair exists for. `requested_model` is the pin (or "" when the caller
# pinned nothing), `launched_model` is what the argv would carry; when the two
# name DIFFERENT models the run is refused with both spellings, instead of
# launching one and recording the other.

def _model_pin_key(value) -> str:
    """The model a spelling denotes, with the launcher's own rewrites removed.

    `model_route_id` drops the `omniroute/` prefix and the `#<effort>` suffix;
    the `-clean` twin is dropped here because it is the same pin asked to run on
    its paid variant. Without these three, every clean run, every effort rung
    and every gateway spelling would read as a mismatch and refuse a plan that
    launches exactly what was asked for.
    """
    text = model_route_id(value)
    if text.endswith("-clean"):
        text = text[: -len("-clean")]
    return text


def model_mismatch_text(requested, launched) -> str:
    """The refusal line: both spellings, named as ask and as launch."""
    return "model_mismatch requested=%s launched=%s" % (requested, launched)


def model_mismatch_refusal(plan) -> str | None:
    """Why this plan must not launch (T2 item 2), or None when it may.

    Skipped when there was no ask (an unpinned run may launch whatever the
    router picked), when the client picks its own model (`PLAN_MODEL_UNNAMED`
    says exactly that and names no model), and when the two spellings denote
    one model (`_model_pin_key`).
    """
    requested = str(plan.get("requested_model") or "")
    launched = str(plan.get("launched_model") or "")
    if not requested or not launched or launched == PLAN_MODEL_UNNAMED:
        return None
    if _model_pin_key(requested) == _model_pin_key(launched):
        return None
    return model_mismatch_text(requested, launched)


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
        # report `l1-orchestrator` for a run that in fact ran `Efficient` on qoder.
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
    if sandbox is not None and not given_run_id:
        # a fallthrough re-run shares the first attempt's clone and branch, so it
        # shares its id: one spawn is one id, not one per attempt.
        # FLEETP0 review LOW: only when that suffix IS a run id. A branch named
        # by hand, or one from before the canonical id existed, is not an id, and
        # pasting its tail into the header, the record and `ps` unvalidated is
        # exactly what is_canonical_run_id was written to refuse. A fresh id
        # still names a fresh record; the reused clone is unchanged either way.
        # S4: non-AutoOS branches carry "<reposlug>/<run_id>", so the id is the
        # tail after the last slash, not an "agent/" prefix.
        _inherited = (sandbox.get("branch") or "").rsplit("/", 1)[-1] \
            if "/" in (sandbox.get("branch") or "") else ""
        if _inherited and is_canonical_run_id(_inherited):
            run_id = _inherited
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
        # AO-L2-SEAT-INTEGRITY P3b: an isolated reviewer is a SEAT, and a seat has to
        # write its findings inside its own clone. Read AFTER the qoder leg, so the level
        # the checks above acted on is the one they were read from.
        level = clients.seat_level(client, level, isolate=bool(args.isolate),
                                   read_only=bool(route.get("read_only")))
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
    # D8: gemini-cli re-resolves its model inside the process (next-speaker,
    # routing, a retry) and with no pin falls to its `auto` default, which its own
    # docs say is gemini-3-pro-preview / gemini-3-flash-preview — a leg this lane
    # never routed (measured 402 side call, 2026-10-01). Pin GEMINI_MODEL to the
    # value the argv carries so an internal call can only name the same leg. The
    # update is unconditional on purpose: a GEMINI_MODEL the caller's shell happens
    # to hold is not a leg this lane routed, so the routed model wins. (G3, RWP3)
    env.update(clients.gemini_side_model_env(client.name, model))
    forced_home = None
    user_site = None
    if args.isolate:
        if sandbox is None:
            # The readable prefix stays; the hex tail inside the run id is what
            # keeps two spawns in the same second (same task) from naming the
            # same sandbox (bug 1). S4: the source repo decides the home —
            # AutoOS's own cards keep logs/sandboxes, a foreign cwd repo goes
            # to ~/fleet/sandboxes/<repo>/ outside the AutoOS tree.
            _sandbox_source = isolate_source()
            sandbox = {"path": sandbox_path_for(_sandbox_source, run_id),
                       "branch": sandbox_branch_for(_sandbox_source, run_id),
                       # Forked from the caller's checkout, not from wherever this
                       # script happens to live (KEYDENY3g item 7).
                       "source": _sandbox_source}
            if tag is not None and not is_autoos_source(_sandbox_source):
                # S4: the session-tag prefix names the target repo, not AutoOS/.
                _slug = sandbox_repo_slug(_sandbox_source)
                tag = "%s/%s" % (_slug, tag.split("/", 1)[1]) if "/" in tag \
                    else _slug
                # D-426: the OR3 headers above were stamped BEFORE this rewrite,
                # so the gateway logged the AutoOS lane prefix while the run
                # record (and `ps`, the printed `session-tag:` line) stores the
                # repo one - and tools/seat-model-evidence.py matches a row's
                # sessionTag against the RECORD's tag with exact equality only,
                # so every foreign run read "no status-200 row carries this
                # seat's session tag". Re-stamp the pair with the tag the record
                # carries, in the same provider block the OR3 stamp wrote. Only
                # when OR3 actually stamped (omniroute gate): a `--free` overlay
                # or a non-gateway client has no such block, and an AutoOS
                # source never enters this branch, so its overlay is untouched.
                _prov = (overlay.get("providers") or {}).get("omniroute")
                if isinstance(_prov, dict) and isinstance(_prov.get("headers"), dict):
                    _prov["headers"].update(gateway_headers(tag, run_id))
        if client.name == "opencode":
            # opencode keys a project by its root commit and remembers the root it
            # saw first; a private data dir keeps the clone from inheriting the
            # main checkout's recorded root.
            env["XDG_DATA_HOME"] = sandbox["path"] + OPENCODE_DATA_SUFFIX
            overlay["permissions"] = outside_fence(env["XDG_DATA_HOME"],
                                                   os.environ.get("AUTOOS_TASK_DIR"))
        # TOOLHOME (P3a): an --isolate worker writes its tool caches under the
        # inherited HOME (measured: a qwen seat's `ansible-galaxy install` filled
        # ~/.ansible outside its sandbox). Every cache root named in
        # TOOL_HOME_REDIRECTS moves into the sandbox-private `<sandbox>.toolhome`;
        # the HOME split (own-account clients keep $HOME for their login, the
        # gateway client gets the toolhome as home) is documented at `forced_home`
        # in worker_env.
        _toolhome = toolhome_dir(sandbox["path"])
        redirect_tool_caches(env, _toolhome)
        # SEAT-PYTEST (P4): the seat reads the user site back, read-only.
        user_site = seat_user_site_path()
        if client.gateway:
            forced_home = _toolhome
    if sandbox is None and getattr(args, "review_base", None):
        raise ReviewBaseRefused("--review-base needs --isolate: the diff lands in the "
                                "sandbox, not in a shared checkout")
    if sandbox is not None:
        # The fence denies opencode's file tools outside the clone, but a
        # worker told (or shown) an absolute parent path can still cd, git -C
        # or shell-write into it (live 2026-09-26) - say so in the task
        # itself. Every client takes the task as its last argv
        # (clients.build_command puts it there; opencode appends it above),
        # and the brief follows the line verbatim.
        sandbox.setdefault("source", isolate_source())
        # a fallthrough re-plan passes this dict again: resolve once, so the stamp holds
        if getattr(args, "review_base", None) and not sandbox.get("review_base"):
            pair = resolve_review_base(sandbox["source"], args.review_base)
            if pair is None:
                raise ReviewBaseRefused("--review-base %s is not a reachable commit "
                                        "in %s" % (args.review_base, sandbox["source"]))
            sandbox["review_base"] = pair
            sandbox["base_line"] = "sandbox base: %s HEAD %s" % pair
        _base_line = sandbox.get("base_line", "")
        # P2/G1b: `--card role=review --review-base` is briefed through the ONE seat
        # template, so the answer grammar and the proof line are standard. The template
        # quotes the base line itself, so the prefix must not stamp it a second time.
        _card = route.get("card") or _parsed_card_fields(getattr(args, "card", None)) or {}
        _seat = bool(_base_line) and _card_asks_review(_card)
        _brief = (seat_verdict.seat_prompt(str(_card.get("angle") or
                                              "cross-family review of the change"),
                                           cmd[-1], _base_line) if _seat else cmd[-1])
        if _seat:
            # P3b item 2: a seat that cannot start the project tests says so in its
            # brief, so an untested verdict is never mistaken for a tested one.
            _deps = seat_deps_note()
            if _deps:
                _brief += _deps + "\n"
        cmd[-1] = (isolate_task_prefix(sandbox["path"], sandbox["source"],
                                       read_only=bool(route.get("read_only")),
                                       base_line=_base_line, stamp_base=not _seat) + "\n"
                   + _brief)
    if client.name == "opencode":
        # WSLSHELL (SB-B): opencode's own config schema carries a top-level
        # `shell` ("Default shell to use for terminal"), which its resolver
        # reads with priority "config" — ahead of the pinned $SHELL for a worker
        # whose checkout or user config states one. Say it in the config too.
        shell = worker_shell()
        if shell:
            overlay["shell"] = shell
        # FLEET-AGENTS: a foreign-repo sandbox has no opencode.jsonc of its own,
        # so the child opencode process would fail with "Agent not found".  Inject
        # ROOT's agent definitions into the overlay (the same block free_overlay
        # already writes for --free).  An AutoOS-checkout run must not get a new
        # agents block — its own opencode.jsonc already declares them.
        _child_source = sandbox.get("source") if sandbox else os.getcwd()
        if not is_autoos_source(_child_source):
            # Always inject the ROOT agent definitions for foreign repos
            root_agents = root_agents_block(cfg)
            if "agents" in overlay:
                # Per-agent merge: for every agent name in either dict, result[name] = {**root_agents.get(name, {}), **overlay["agents"].get(name, {})}
                # (definition first, overlay keys win)
                merged_agents = {}
                all_agent_names = set(root_agents.keys()) | set(overlay["agents"].keys())
                for agent_name in all_agent_names:
                    root_agent = root_agents.get(agent_name, {})
                    overlay_agent = overlay["agents"].get(agent_name, {})
                    merged_agents[agent_name] = {**root_agent, **overlay_agent}
                overlay["agents"] = merged_agents
            else:
                overlay["agents"] = root_agents
            # FLEET-AGENTS-2: the same sandbox also lacks ROOT's providers and
            # permission fence. Providers: ROOT's definition first, the per-run
            # keys (OR3 headers, free/lean overlays) on top, one level deep.
            # Permissions: ROOT's rules FIRST, then whatever the overlay holds
            # (the outside fence); the spawn gate below is appended after this
            # and stays LAST ("last matching wins").
            root_blocks = root_overlay_blocks(cfg)
            if "providers" in root_blocks:
                overlay["providers"] = merge_root_providers(
                    root_blocks["providers"], overlay.get("providers"))
            if root_blocks.get("permissions"):
                overlay["permissions"] = (root_blocks["permissions"]
                                          + overlay.get("permissions", []))
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
    # T2-RECORD-PIN item 2: the ask and the launch, kept beside `model` so a
    # reader never re-derives one from the other. `requested_model` is the pin
    # this caller typed ("" when it typed none — an unpinned run did not ask for
    # any particular model); a `--free` run asked for the promo model by name,
    # so that is its ask too. `launched_model` is what the argv above would hand
    # the client.
    _free_model = getattr(args, "free_model", None)
    requested_model = args.model or (_free_model if args.free and _free_model else None) or ""
    return {"agent": agent, "client": client.name, "model": model, "cmd": cmd, "env": env,
            # FAMILYFENCE-b: the ask and its witness, kept beside the ask so a
            # reader never has to re-derive one from the other. `client_session_id`
            # is the join key into the client's own transcript; `model_source` is
            # where `model` came from before any transcript was read.
            "model_source": model_source, "client_session_id": client_session_id,
            # T2-RECORD-PIN item 2: ask vs launch. Equal for every plan that
            # launches what it was asked for; `model_mismatch_refusal` refuses
            # the ones where they differ, and the worker record carries both.
            "requested_model": requested_model,
            "launched_model": model or "",
            # The text this run sends the client (containment prefix + task),
            # kept so the REPORT check can tell the worker's own words from its
            # brief echoed back at it (SPAWNFIX3c).
            "brief": cmd[-1],
            "route": route, "depth": (depth, max_depth), "free": bool(args.free),
            "run_id": run_id, "sandbox": sandbox,
            "cwd": sandbox["path"] if sandbox else os.getcwd(),
            # TOOLHOME (P3a): the gateway client's HOME, applied by worker_env
            # only when it names this sandbox's own .toolhome sibling.
            "forced_home": forced_home,
            # SEAT-PYTEST (P4): worker_env applies it only if it recomputes to this.
            "user_site_path": user_site,
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


def _writer_named(value) -> str | None:
    """`value` when it names something, None for the marker and the blank.

    `WRITER_UNRESOLVED` is what the record SAYS about a field nothing witnessed;
    it is never an input to a lookup, and reading it as a model or provider name is
    how a run that named nothing came to record a family called `unresolved`.
    """
    text = str(value or "").strip()
    return None if not text or text == WRITER_UNRESOLVED else text


def resolved_writer(plan, uses_gateway, registry=None, key=None, fetch=None,
                    gateway=None, home=None) -> dict:
    """The writer of this run: `{provider, model, family, source}` (+ the family's
    own witness, `family_source`, or `family_reason` when nothing names a family).

    A gateway run asks the gateway (one GET, by the run's own session id); a
    native run asks the client first — its own session transcript, joined by the
    session id this spawner minted into the argv — and only falls back on the plan
    when the client said nothing. `--free` names the provider in that same
    `provider/model` id. The family is `run_model_family`'s answer for that model:
    the registry's declaration first, and the vendor the served id itself names
    after it (AO-RUN-FAMILY-RECORD, D-807 — a client's own id is not a registry
    spelling, and was recording `unresolved` beside a model that named its vendor).

    `source` is the witness behind the model answer (`WRITER_SOURCE_*`), because a
    model the plan assumed and a model the client attested are not the same claim —
    a review is only independent of an author it can PROVE it ran elsewhere.

    Provider, model and source say `unresolved` when the run named no model. The
    family says null plus a reason (D-807): `unresolved` is not a family, and every
    cross-family check stays closed on a null whatever the reason.
    """
    registry = registry if registry is not None else load_live_registry()
    provider = model = served_id = None
    if uses_gateway:
        session = session_header_value(plan.get("session_tag") or "", plan.get("run_id")) \
            if plan.get("session_tag") else None
        found = gateway_writer(session, gateway=gateway, key=key, fetch=fetch)
        # AO-L2-SEAT-INTEGRITY P1 (measured: L2 children recorded `unresolved` although the
        # caller pinned a model). A pin names the ask; what a caller typed is its weakest
        # witness (FAMILYFENCE-b: `pinned` is never a proof, so REJECT 3 keeps holding).
        pin = _writer_named(plan.get("model")) if not found and plan.get("model_source") == WRITER_SOURCE_PIN else None
        if found is None and pin is None:
            return {"provider": WRITER_UNRESOLVED, "model": WRITER_UNRESOLVED,
                    "family": None, "source": WRITER_UNRESOLVED,
                    "family_reason": "the gateway call log named no model for "
                                     "this run"}
        if found is None:
            # G4: a pin without a `provider/` prefix names the client, not a
            # provider — as the non-gateway branch (:6529) already records it.
            provider = ((free_provider(pin) if "/" in pin else plan.get("client"))
                        or WRITER_UNRESOLVED)
            model = pin.rpartition("/")[2]
            served_id, source = pin, WRITER_SOURCE_PIN
        else:
            provider, model = found
            served_id = model
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
        # The family read keeps the id exactly as it was served — its vendor
        # prefix, if it carried one, is the fact that says the model is NOT this
        # client's own account's business (D-807). The record's `model` field is
        # the bare name (below and in `writer_line`), which is what the prefix is
        # for: `mimo/mimo-7` beside `mimo-7` reads as two models.
        served_id = model
        if model:
            # A native client's own id, or a `provider/model` promo id on --free.
            provider = free_provider(model) if "/" in model else plan.get("client")
            # The gateway leg answers with the bare model name, so this one must
            # too: `mimo/mimo-7` in a record read beside `mimo-7` looks like two
            # different models, and the prefix is already in `provider`.
            if "/" in model:
                model = model.rpartition("/")[2]
    client = plan.get("client") or None
    # A native run's provider half IS the client's name; that name answers at the
    # client layer, where the account's own default model is read with it, and not
    # as a vendor row beside it.
    named_provider = _writer_named(provider)
    if named_provider == client:
        named_provider = None
    family, family_source, family_reason = run_model_family(
        _writer_named(served_id), client=client, provider=named_provider,
        registry=registry)
    result = {"provider": provider or WRITER_UNRESOLVED,
              "model": model or WRITER_UNRESOLVED,
              "source": source or WRITER_UNRESOLVED}
    # REJECT 3: the family is read off the id above, so its witness is that id's.
    # A run that died before answering has no witness for its model, and the family
    # under it is the plan's claim — `planned-model`, which opens no gate — whatever
    # layer of `run_model_family` happened to place the model the argv asked for.
    result.update(witnessed_family(family, family_source, family_reason,
                                   (source or "") in WRITER_PROVEN_SOURCES))
    return result


def writer_line(writer: dict) -> str:
    """The one line every reader of the run sees: `writer: <provider>/<model> (<family>) source=<witness>`.

    A family nobody could name prints as `unresolved`, which is the marker the log
    has always used — the RECORD carries `null` plus `family_reason` (D-807), because
    a word sitting in a family's field reads as a family, and `ps`, the review fence
    and the keeper all read the record rather than this line.
    """
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
    than running it unfenced (FAMILYFENCE-4).

    REJECT 3 adds the third answer: a record whose family is `planned-model` names
    no writer either. That record was written from the launch line, not from an
    answer, and an author family is half of a cross-family claim — a review of a run
    that never answered is refused on FAMILYFENCE-4 exactly as one with no record is,
    because "the plan said Qwen" has never shown that a Qwen wrote the diff."""
    if not run_id:
        return None
    record = (read or read_kill_record)(run_id) or {}
    writer = record.get("writer") or {}
    if family_is_planned(writer.get("family_source")):
        return None
    family = writer.get("family")
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


def fence_family_of(spelling, registry):
    """The family a spelling denotes, or None when nothing can say.

    Three reads, in the order the fence trusts them: the registry's row for the
    spelling (`reviewer_family`), then the ONE family a route's legs all declare
    (`route_family`), then the vendor word inside the id via
    `vendor_family_name_unique` — NOT `vendor_family_name`'s longest-token
    tie-break, which let `omniroute/deepseek-r1-distill-qwen-32b` read as
    deepseek and slip a `--not-family qwen` review (S1 F1: an unrowed pin whose
    id names two vendors is unplaceable, so a review refuses it and a write
    role does not block — the existing unknown semantics). A gateway pin the
    client config declares and the registry lists no row for but that names
    ONE vendor — `omniroute/deepseek-direct-flash` is one — is still judged on
    that family (AO-FAMILYFENCE-QWEN). A route the registry CARRIES is judged on
    its legs alone, never by its own name (S1 F2 round 2: `deepseek-mixed` with a
    qwen leg). The fence is a check on the declared family of the pin and its
    legs, not proof of what the gateway served — the post-run backstop applies."""
    text = str(spelling or "").strip()
    if not text or text == PLAN_MODEL_UNNAMED:
        return None
    bare = text.rpartition("/")[2] if "/" in text else text
    family = reviewer_family(text, registry) or reviewer_family(bare, registry)
    if family:
        return family
    combo = _combo_of(text)
    if combo and combo in (registry.get("routes") or {}):
        return route_family(combo, registry, unique_legs=True)
    family = route_family(combo, registry, unique_legs=True)
    if family:
        return family
    return vendor_family_name_unique(bare)


def fence_blocks_model(spelling, registry, fence):
    """True when `spelling` may not serve a run carrying `fence`.

    The family is what the registry and the spelling's own vendor name declare
    (`fence_family_of`), never a guess from an arbitrary word. Unknown is unsafe
    only for a review — see `family_fence`."""
    if not fence or not fence.get("families"):
        return False
    family = fence_family_of(spelling, registry)
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
    asks for the sentence, not for a second refusal. The same rule reads the
    family's own witness (AO-RUN-FAMILY-RECORD REJECT 3): a record whose family says
    `planned-model` was read off what the launch line asked for, and that names no
    reviewer either.

    FAMILYFENCE-3 N5: it was never a licence to print `unknown` about a collision
    the run is being REFUSED for. `cmd_run`'s backstop reads the serving family
    whether or not a witness attested to it, so an unattested reviewer that landed
    inside the fence printed `CROSS-FAMILY: unknown` next to exit 12 — a log that
    said "cannot tell" about the one thing the spawner had just acted on. `NO` is
    now that collision too, marked `(assumed)` when nothing witnessed the model, so
    the sentence and the exit code answer the same question at the same strength.
    """
    family = (writer or {}).get("family") or WRITER_UNRESOLVED
    reviewer = family if (writer_is_proven(writer)
                          and not family_is_planned(writer.get("family_source"))) \
        else WRITER_UNRESOLVED
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
    """Every `credit` grant stays available, with the reason named.

    The fail-OPEN half of the guard (T1-CREDIT-FIX, supersedes the FREEKEYS-2
    fail-closed contract): a usage read that fails leaves the resolver with no
    spend figure, and "no figure" is not "$0 spent" -- printing it as $0.00 of
    a $200 grant is what reported an untouched trial grant as EXHAUSTED and
    skipped every trial leg (`credit exhausted ovhcloud $0.00/$200.00`). The
    figure falls back to the registry's dated manual spend when one exists
    (`autoos_usage.manual_credit_spend`), else to `unknown`, which the leg
    filter keeps with a `spend unknown` note. A prepaid trial grant that is
    truly spent rejects at the provider (402/429) and the combo falls through
    at run time, so keeping the leg risks one refused call, while refusing it
    puts the whole trial tier offline on a 403.

    `why` is the one-line `autoos_usage.spend_failure_note` for the failure --
    `manage key rejected (403) - spend unmeasured` for the observed short-key
    403 -- never a message that can carry key material (AGENTS.md rule 1).
    """
    try:
        guards = usage_mod.credit_guards(registry, None, None, failure=why)
    except Exception:  # noqa: BLE001 - the manual fallback must never fail the plan
        guards = usage_mod.credit_guards_unreadable(registry, why)
    for pid, entry in _paid_guards_unreadable(registry, why).items():
        guards.setdefault(pid, entry)
    return guards


def _paid_guard_ids(registry: dict) -> list:
    """Ids of `tier == paid` providers declaring a readable `monthly_cap_usd`.

    Total: never raises, so both fallback builders below can call it freely.
    A paid row with no (or no readable) cap names nothing to hold spend
    against, so it is simply not guarded."""
    try:
        items = list(((registry or {}).get("providers") or {}).items())
    except Exception:
        return []
    out = []
    for pid, entry in items:
        if not isinstance(entry, dict) or entry.get("tier") != "paid":
            continue
        try:
            usage_mod.monthly_cap_usd(registry, pid)
        except Exception:
            continue
        out.append(pid)
    return sorted(out)


def _paid_guards_unreadable(registry: dict, why: str) -> dict:
    """`{provider id: guard}` for paid rows when spend cannot be measured.

    Total: never raises. Every entry reads `unknown` with `spend_unknown`
    True -- D-212: paid legs are STANDING LAST-RESORT legs, so they are KEPT
    with a visible note 'paid spend unmeasured <provider> - leg kept (last
    resort, D-212)'. They are REFUSED only when MEASURED spend >= monthly_cap_usd."""
    out = {}
    for pid in _paid_guard_ids(registry):
        cap = warn = 0.0
        try:
            cap = usage_mod.monthly_cap_usd(registry, pid)
            warn = usage_mod.spend_warn_usd(registry, pid)
        except Exception:
            pass
        out[pid] = {"provider": pid, "state": "unknown",
                    "spend_usd": 0.0, "spend_unknown": True,
                    "cap_usd": cap, "warn_usd": warn, "models_unpriced": 0,
                    "note": "paid spend unmeasured %s - leg kept (last resort, D-212)"
                            % pid}
    return out


def _paid_guards_measured(registry: dict, rows, since, complete=True) -> dict:
    """`{provider id: guard}` for paid rows from recorded usage rows.

    Raises on malformed input like `credit_guards` does -- the caller folds
    those into the expected-failure fallback, and unforeseen bugs into the
    guard-error map."""
    prices = usage_mod.prices_from_registry(registry)
    out = {}
    for pid in _paid_guard_ids(registry):
        spend = usage_mod.paid_spend(rows, prices, registry, since,
                                     provider=pid)
        state, note = usage_mod.spend_guard(registry, pid, spend["spend_usd"])
        unreadable = int(spend.get("rows_unreadable") or 0)
        if unreadable and state != "refuse":
            # T1-CREDIT-FIX-8 C3 (paid half): unreadable rows are not silent
            # $0 -- the grant reads unknown (kept as last resort per D-212),
            # naming the COUNT beside the readable figure. Measured spend at
            # or over the cap still refuses: it beats unknown.
            state = "unknown"
            note = ("paid spend unmeasured %s - %d unreadable row(s), "
                    "readable spend $%.2f - leg kept (last resort, D-212)"
                    % (pid, unreadable, spend["spend_usd"]))
        if not complete and state != "refuse":
            # T1-CREDIT-FIX-9 T1: a fetch cut at the page cap is not a
            # measurement -- kept per D-212 with the note; measured spend
            # already at or over the cap still refuses.
            state = "unknown"
            note = ("paid spend unmeasured %s: fetch truncated at page cap "
                    "(readable spend $%.2f) - leg kept (last resort, D-212)"
                    % (pid, spend["spend_usd"]))
        out[pid] = {"provider": pid, "state": state,
                    "spend_usd": spend["spend_usd"],
                    "spend_unknown": state == "unknown",
                    "cap_usd": usage_mod.monthly_cap_usd(registry, pid),
                    "warn_usd": usage_mod.spend_warn_usd(registry, pid),
                    "models_unpriced": spend["models_unpriced"],
                    "note": note}
    # T1-CREDIT-FIX-10 M2 (D-240): an `unknown` paid guard is not the end of
    # the story -- the local estimate over these same rows either refuses the
    # leg at the local cap or keeps it with the D-240 line. Measured states
    # are untouched: measured spend governs.
    return usage_mod.apply_paid_local_cap(registry, out, rows, since)


def _credit_guard_error(registry: dict, type_name: str) -> dict:
    """`{provider id: guard}` for an UNFORESEEN read bug (T1-CREDIT-FIX-2).

    Distinct from `unknown`: `unknown` means "the gateway gave no figure"
    (fail open for credit, kept for paid per D-212), while `guard error` means
    "our own code raised something the contract does not predict" -- kept for
    credit, kept for paid (D-212 last resort), and always named in the plan so
    the bug is visible instead of silent. `type_name` is the exception TYPE
    NAME only: a message can carry a gateway URL, a home path or key material
    (AGENTS.md rule 1).
    Total: never raises."""
    out = {}
    try:
        providers = usage_mod.credit_guard_providers(registry)
    except Exception:
        providers = []
    for provider in providers:
        cap = warn = 0.0
        try:
            cap = usage_mod.monthly_cap_usd(registry, provider)
            warn = usage_mod.spend_warn_usd(registry, provider)
        except Exception:
            pass
        out[provider] = {"provider": provider, "state": "guard error",
                         "spend_usd": 0.0, "spend_unknown": True,
                         "cap_usd": cap, "warn_usd": warn,
                         "models_unpriced": 0,
                         "hard_stop_usd": usage_mod.hard_stop_usd(
                             registry, provider, cap),
                         "window_limited": usage_mod.credit_window_limited(
                             registry, provider),
                         "note": "credit guard error %s (%s) - spend "
                                 "unmeasured, leg kept" % (provider, type_name)}
    for pid in _paid_guard_ids(registry):
        cap = warn = 0.0
        try:
            cap = usage_mod.monthly_cap_usd(registry, pid)
            warn = usage_mod.spend_warn_usd(registry, pid)
        except Exception:
            pass
        out[pid] = {"provider": pid, "state": "guard error",
                    "spend_usd": 0.0, "spend_unknown": True,
                    "cap_usd": cap, "warn_usd": warn, "models_unpriced": 0,
                    "note": "paid guard error %s (%s) - spend unmeasured, "
                            "leg kept (last resort, D-212)" % (pid, type_name)}
    return out


def plan_credit_guards(registry: dict, now=None, fetch=None,
                       env: dict | None = None, helper=None) -> dict:
    """The `{provider: guard}` map `autoos_resolver.usable_legs` refuses a credit
    leg with (brief FREEKEYS-2 item 2): this month's spend per `credit` provider
    against its own `monthly_cap_usd`, read from the gateway's call log.

    Cheap by design — one usage read per registry per process
    (`CREDIT_GUARD_CACHE`), and a host with no `credit` provider in the registry
    makes no call at all. The figure is built by `autoos_usage.credit_guards`,
    the same reader the `usage` report prints, so what blocks a leg and what the
    ledger shows are never two numbers. A read that fails (gateway down, key
    missing or unauthorised, an unparseable page) returns
    `_credit_guards_unreadable` -- the fail-open fallback (dated manual figure,
    else `unknown` with a `spend unknown` note), not an empty map and no longer
    a `refuse` map.

    Two things make this safe inside a plan (FREEKEYS-2c, rev-freekeys2 finding
    4). The cache is keyed by `registry` identity, so a caller that hands this a
    different registry — a candidate registry compared against the committed one,
    a lane that reloads — gets guards built from that registry's own caps
    instead of the first one's answer. And the usage read predicts its failures
    by type (T1-CREDIT-FIX-2): `UsageError` (gateway refusal/unreachable),
    `OSError` (missing key file), `ValueError` (unreadable ledger, a grant with
    no cap -- JSON decode errors land here too) map to the `unknown` fallback
    (fail open for credit, kept for paid as last resort per D-212). An
    UNFORESEEN bug type
    (`TypeError`/`AttributeError`/...) is never folded into `unknown` -- which
    would read as a gateway outage -- but lands as its own `guard error` state
    (kept for credit, kept for paid as last resort per D-212, type name only
    in the note), so the bug
    stays visible. `KeyboardInterrupt`/`SystemExit` are `BaseException`, outside
    both, so Ctrl-C still works.

    `fetch`/`env`/`now` are injectable so a test can drive this without a
    gateway, a key or the clock; the callers pass none of them.

    T1-CREDIT-FIX-10 M1 (D-250): the rows come from the first transport that
    answers. The manage-key HTTP fetch wins when the key file is non-empty
    and answers 200; else the gateway's read-only CLI helper transport
    (`autoos_usage.helper_fetch`, docker exec into the gateway container --
    no key involved) is tried when docker and the container exist; else the
    guards fall back to unknown with the D-240 local estimate. The helper is
    the default stack's second transport: an explicitly injected `fetch`
    that fails reads as that transport being down (old tests drive exactly
    this), so the helper is attempted only when `helper` is injected or
    `fetch` is the default. `helper` is injectable for the same reason
    `fetch` is (a test must never touch the real container); production
    passes neither. Every measured guard note names its rows source
    (`measured via manage key` / `measured via gateway helper`); an
    unmeasured paid guard carries the D-240 line (`local estimate (D-240)`).

    T1-CREDIT-FIX-10 M4 (D-253): when the helper is in play, the scheduled
    provider balances (`GET /api/usage/provider-limits` through the same
    helper transport) become the paid meter -- a provider with an in-month
    balance series is judged on decreases, recorded reading by reading in
    the git-ignored state-dir ledger. No series: the call ledger governs
    (`measured via call ledger`), and an unmeasured paid guard keeps the
    D-240 line. Balance reads never fail the plan.
    """
    cache_key = id(registry)
    cached = CREDIT_GUARD_CACHE.get(cache_key)
    if cached is not None and cached["registry"] is registry:
        return cached["guards"]
    providers = usage_mod.credit_guard_providers(registry)
    paid = _paid_guard_ids(registry)
    if not providers and not paid:
        CREDIT_GUARD_CACHE[cache_key] = {"registry": registry, "guards": {}}
        return CREDIT_GUARD_CACHE[cache_key]["guards"]
    env = os.environ if env is None else env
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = usage_mod.month_start(now)
    fetch_cut = usage_mod.fetch_cutoff(registry, now)
    gateway = (env.get("AUTOOS_OMNIROUTE_URL")
               or usage_mod.DEFAULT_GATEWAY).rstrip("/")
    use_helper = helper is not None or fetch is None
    fetch = fetch or usage_mod.urllib_fetch
    helper_fetch_fn = helper or usage_mod.helper_fetch

    def _measured(rows, truncated):
        guards = usage_mod.credit_guards(registry, rows, cutoff, today=now,
                                         complete=not truncated)
        guards.update(_paid_guards_measured(registry, rows, cutoff,
                                            complete=not truncated))
        return guards

    def _tag_source(guards, source):
        # Every measured guard note names the rows source that produced it.
        # A D-240 line is terminal in its note (the kept/refused line reads
        # exactly), so the tag skips notes that already carry one.
        for guard in (guards or {}).values():
            if not isinstance(guard, dict) or not guard.get("note"):
                continue
            if "(D-240)" in guard["note"]:
                continue
            guard["note"] = "%s [%s]" % (guard["note"], source)
        return guards

    rows_source = None
    try:
        key = usage_mod.read_manage_key(usage_mod.key_file_path(env))
        rows, _pages, truncated = usage_mod.fetch_window(fetch, gateway, key, fetch_cut)
        rows_source = "measured via manage key"
        guards = _tag_source(_measured(rows, truncated), rows_source)
    except (usage_mod.UsageError, OSError, ValueError) as exc:
        # Every failure mode the read predicts (gateway refusal/unreachable,
        # missing key, unreadable ledger, a grant that cannot state its cap):
        # `unknown` (fail open for credit, kept for paid as last resort per
        # D-212, held to the D-240 local cap), never a
        # traceback.
        # ValueError already covers JSON decode errors: fetch_window rewraps
        # bad pages as UsageError, and JSONDecodeError subclasses ValueError.
        first_failure = exc
        _fallback_overlay_type = None
        if use_helper:
            # T1-CREDIT-FIX-10 M1 (D-250): the manage-key read did not answer
            # 200 -- try the read-only helper transport before giving up on a
            # measurement. The helper takes no key (it authenticates inside
            # the container), so `key` is never passed, printed or read here.
            def _helper_transport(url, headers, timeout):
                return helper_fetch_fn(url, None, timeout)
            try:
                rows, _pages, truncated = usage_mod.fetch_window(
                    _helper_transport, gateway, "", fetch_cut)
                rows_source = "measured via gateway helper"
                guards = _tag_source(_measured(rows, truncated), rows_source)
            except (usage_mod.UsageError, OSError, ValueError) as exc2:
                # CREDIT-16 B (D-274): explicit fail-closed helper fallback.
                # An exception inside this handler is NOT caught by the
                # sibling `except Exception` -- a crash is not a guard.
                try:
                    guards = _credit_guards_unreadable(
                        registry, usage_mod.spend_failure_note(exc2))
                    usage_mod.apply_paid_local_cap(registry, guards, [], cutoff)
                except Exception as exc_fb:  # noqa: BLE001 - fail closed
                    _tn = type(exc_fb).__name__
                    _fallback_overlay_type = _tn
                    try:
                        guards = _credit_guard_error(registry, _tn)
                    except Exception:
                        guards = {}
                    try:
                        guards = usage_mod.refuse_paid_on_overlay_error(
                            registry, guards, _tn)
                    except Exception:
                        if not isinstance(guards, dict):
                            guards = {}
                    # CREDIT-16 R3 (D-274): cover every paid provider even
                    # when guards is empty; the refuse call only mutates
                    # existing entries and must never crash the plan.
                    try:
                        if not isinstance(guards, dict):
                            guards = {}
                        for _pid in _paid_guard_ids(registry):
                            _g = guards.get(_pid)
                            if not isinstance(_g, dict) or _g.get("state") != "refuse":
                                _cap = _warn = 0.0
                                try:
                                    _cap = usage_mod.monthly_cap_usd(registry, _pid)
                                    _warn = usage_mod.spend_warn_usd(registry, _pid)
                                except Exception:
                                    pass
                                guards[_pid] = {"provider": _pid, "state": "refuse", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": _cap, "warn_usd": _warn, "models_unpriced": 0, "note": ("balance overlay failed (%s) (D-274) - paid leg refused" % (_tn,))}
                    except Exception:
                        pass
        else:
            # CREDIT-16 B (D-274): same explicit fail-closed fallback.
            try:
                guards = _credit_guards_unreadable(
                    registry, usage_mod.spend_failure_note(first_failure))
                usage_mod.apply_paid_local_cap(registry, guards, [], cutoff)
            except Exception as exc_fb:  # noqa: BLE001 - fail closed
                _tn = type(exc_fb).__name__
                _fallback_overlay_type = _tn
                try:
                    guards = _credit_guard_error(registry, _tn)
                except Exception:
                    guards = {}
                try:
                    guards = usage_mod.refuse_paid_on_overlay_error(
                        registry, guards, _tn)
                except Exception:
                    if not isinstance(guards, dict):
                        guards = {}
                # CREDIT-16 R3 (D-274): cover every paid provider even when
                # guards is empty; the refuse call only mutates existing
                # entries and must never crash the plan.
                try:
                    if not isinstance(guards, dict):
                        guards = {}
                    for _pid in _paid_guard_ids(registry):
                        _g = guards.get(_pid)
                        if not isinstance(_g, dict) or _g.get("state") != "refuse":
                            _cap = _warn = 0.0
                            try:
                                _cap = usage_mod.monthly_cap_usd(registry, _pid)
                                _warn = usage_mod.spend_warn_usd(registry, _pid)
                            except Exception:
                                pass
                            guards[_pid] = {"provider": _pid, "state": "refuse", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": _cap, "warn_usd": _warn, "models_unpriced": 0, "note": ("balance overlay failed (%s) (D-274) - paid leg refused" % (_tn,))}
                except Exception:
                    pass
        if use_helper and rows_source is None:
            # T1-CREDIT-FIX-14 rework M2 (D-274): the rows read failed, so
            # the overlay above never ran -- consult the ledger OUTSIDE the
            # rows gate. A persisted STALE marker refuses paid here (it beats
            # the D-240 estimate just applied), and with the helper down no
            # balance read was possible at all, so a first-failure marker is
            # recorded with the current stamp. Credit guards keep their
            # fail-open fallback; only paid refuses. (Unforeseen-bug rows
            # failures take the `guard error` path below, untouched.)
            # Rework 2 MINOR-2 (D-274): the refuse call itself must never
            # crash the plan -- ANY raise refuses paid with the TYPE named.
            try:
                guards = usage_mod.refuse_paid_without_balance_read(
                    registry, guards, env, now)
            except Exception as exc2:  # noqa: BLE001 - fail closed per D-274
                try:
                    guards = usage_mod.refuse_paid_on_overlay_error(
                        registry, guards, type(exc2).__name__)
                except Exception:  # noqa: BLE001 - fail closed per D-274
                    _tn = type(exc2).__name__
                    try:
                        guards = _credit_guard_error(registry, _tn)
                    except Exception:
                        guards = {}
                    try:
                        guards = usage_mod.refuse_paid_on_overlay_error(
                            registry, guards, _tn)
                    except Exception:
                        if not isinstance(guards, dict):
                            guards = {}
                        try:
                            if not isinstance(guards, dict):
                                guards = {}
                            for _pid in _paid_guard_ids(registry):
                                _g = guards.get(_pid)
                                if not isinstance(_g, dict) or _g.get("state") != "refuse":
                                    _cap = _warn = 0.0
                                    try:
                                        _cap = usage_mod.monthly_cap_usd(registry, _pid)
                                        _warn = usage_mod.spend_warn_usd(registry, _pid)
                                    except Exception:
                                        pass
                                    guards[_pid] = {"provider": _pid, "state": "refuse", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": _cap, "warn_usd": _warn, "models_unpriced": 0, "note": ("balance overlay failed (%s) (D-274) - paid leg refused" % (_tn,))}
                        except Exception:
                            pass
            # CREDIT-16 B: a fallback crash already refused paid with its
            # TYPE -- the ledger consult above must not overwrite it with a
            # generic stale reason. Re-assert the fallback TYPE last.
            if _fallback_overlay_type is not None:
                try:
                    guards = usage_mod.refuse_paid_on_overlay_error(
                        registry, guards, _fallback_overlay_type)
                except Exception:  # noqa: BLE001 - fail closed per D-274
                    _tn = _fallback_overlay_type
                    try:
                        guards = _credit_guard_error(registry, _tn)
                    except Exception:
                        guards = {}
                    try:
                        guards = usage_mod.refuse_paid_on_overlay_error(
                            registry, guards, _tn)
                    except Exception:
                        if not isinstance(guards, dict):
                            guards = {}
                    try:
                        if not isinstance(guards, dict):
                            guards = {}
                        for _pid in _paid_guard_ids(registry):
                            _g = guards.get(_pid)
                            if not isinstance(_g, dict) or _g.get("state") != "refuse":
                                _cap = _warn = 0.0
                                try:
                                    _cap = usage_mod.monthly_cap_usd(registry, _pid)
                                    _warn = usage_mod.spend_warn_usd(registry, _pid)
                                except Exception:
                                    pass
                                guards[_pid] = {"provider": _pid, "state": "refuse", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": _cap, "warn_usd": _warn, "models_unpriced": 0, "note": ("balance overlay failed (%s) (D-274) - paid leg refused" % (_tn,))}
                    except Exception:
                        pass
    except Exception as exc:  # noqa: BLE001 - unforeseen bug: named, not hidden
        # NOT `unknown`: an unforeseen bug (TypeError/AttributeError/...) must
        # surface as its own `guard error` state -- kept for credit, kept for
        # paid as last resort per D-212 (a local paid cap follows in
        # CREDIT-10 per D-240), explain line carries the TYPE NAME only --
        # never silently `unknown` (which would read as a gateway outage) or
        # `ok`.
        # T1-CREDIT-FIX-14 rework 2 MINOR-3 (D-274): paid is never governed
        # without a valid read -- refuse paid here too (TYPE NAME only in
        # the reason); credit guards keep their fail-open `guard error`.
        guards = _credit_guard_error(registry, type(exc).__name__)
        try:
            guards = usage_mod.refuse_paid_on_overlay_error(
                registry, guards, type(exc).__name__)
        except Exception:  # noqa: BLE001 - fail closed per D-274
            _tn = type(exc).__name__
            try:
                guards = _credit_guard_error(registry, _tn)
            except Exception:
                guards = {}
            try:
                guards = usage_mod.refuse_paid_on_overlay_error(
                    registry, guards, _tn)
            except Exception:
                if not isinstance(guards, dict):
                    guards = {}
            try:
                if not isinstance(guards, dict):
                    guards = {}
                for _pid in _paid_guard_ids(registry):
                    _g = guards.get(_pid)
                    if not isinstance(_g, dict) or _g.get("state") != "refuse":
                        _cap = _warn = 0.0
                        try:
                            _cap = usage_mod.monthly_cap_usd(registry, _pid)
                            _warn = usage_mod.spend_warn_usd(registry, _pid)
                        except Exception:
                            pass
                        guards[_pid] = {"provider": _pid, "state": "refuse", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": _cap, "warn_usd": _warn, "models_unpriced": 0, "note": ("balance overlay failed (%s) (D-274) - paid leg refused" % (_tn,))}
            except Exception:
                pass
    if use_helper and rows_source is not None:
        # T1-CREDIT-FIX-10 M4 (D-253): the scheduled provider balances are
        # the paid meter ... (see `usage_mod.overlay_balance_guards`).
        # T1-CREDIT-FIX-14 rework M1b (D-274): the overlay is a gate, not a
        # meter -- ANY exception from it (expected or not) refuses every
        # paid guard with the exception TYPE named, never `ok`.
        try:
            guards = usage_mod.overlay_balance_guards(
                registry, guards, gateway, helper_fetch_fn, env, cutoff, now)
        except Exception as exc:  # noqa: BLE001 - fail closed per D-274
            _tn = type(exc).__name__
            try:
                guards = usage_mod.refuse_paid_on_overlay_error(
                    registry, guards, _tn)
            except Exception:  # noqa: BLE001 - fail closed per D-274
                try:
                    guards = _credit_guard_error(registry, _tn)
                except Exception:
                    guards = {}
                try:
                    guards = usage_mod.refuse_paid_on_overlay_error(
                        registry, guards, _tn)
                except Exception:
                    if not isinstance(guards, dict):
                        guards = {}
                try:
                    if not isinstance(guards, dict):
                        guards = {}
                    for _pid in _paid_guard_ids(registry):
                        _g = guards.get(_pid)
                        if not isinstance(_g, dict) or _g.get("state") != "refuse":
                            _cap = _warn = 0.0
                            try:
                                _cap = usage_mod.monthly_cap_usd(registry, _pid)
                                _warn = usage_mod.spend_warn_usd(registry, _pid)
                            except Exception:
                                pass
                            guards[_pid] = {"provider": _pid, "state": "refuse", "spend_usd": 0.0, "spend_unknown": False, "cap_usd": _cap, "warn_usd": _warn, "models_unpriced": 0, "note": ("balance overlay failed (%s) (D-274) - paid leg refused" % (_tn,))}
                except Exception:
                    pass
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


# AO-SPAWN-COOLDOWN-RETRY (S2, measured 2026-10-09T16:40Z, the two runs named in
# the test): the gateway's per-credential cooldown is not an outage, it is a
# countdown — the SAME credential, back in the seconds it states. Re-planning
# around a 3 s window spends a route and a clone to solve a nap, so a stop that
# states a window this short waits and re-runs the leg it is on. Longer or
# unstated, it is an outage and falls through as a rate limit does.
COOLDOWN_SAME_LEG_MAX_SECONDS = 60

_COOLDOWN_MARKER = "are cooling down"


def cooldown_stop(line: str) -> bool:
    """Is this provider-stop line the gateway counting one credential down?

    Call it on a line `provider_stop` returned, like `rate_limit_stop`: the
    error-prefix rule that keeps the task's own quoted text out of it already
    ran there.
    """
    return _COOLDOWN_MARKER in (line or "").lower()


def cooldown_wait(line: str) -> int | None:
    """Seconds to wait before re-running THIS leg, or None: wait elsewhere.

    None when the line is no cooldown, states no window, or states more than
    COOLDOWN_SAME_LEG_MAX_SECONDS — a leg that says "back in two minutes" is not
    serving this run either way, and the next leg is.
    """
    if not cooldown_stop(line):
        return None
    secs = parse_reset(line)
    return secs if secs is not None and secs <= COOLDOWN_SAME_LEG_MAX_SECONDS else None


def cooldown_sleep(seconds: float) -> None:
    """The nap between a cooldown stop and its same-leg retry.

    A name of its own so a test can patch it out; nothing else sleeps here.
    """
    time.sleep(seconds)


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
    out loud (R6STOP review, SPAWNFIX3 item 7: l2-worker's route carries
    gpt-oss-120b on both cerebras and sambanova, and a stop line names the
    model, never who served it; benching one of them on a 50/50 guess starves a
    provider that may be perfectly healthy). Only an unnamed line falls back
    to the gateway working down the route's legs in order, so the one that took
    the traffic is the first leg whose provider is up right now. Both read legs
    through `resolve_leg`, so a leg spelled with a gateway alias attributes to
    its provider rather than being skipped (measured: a l2-worker gemini
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


def _porcelain_path_is_sandbox(p: str, rel: str) -> bool:
    """True when a porcelain path (quoted or not) is the seat tree, anything under
    it, or one of the TWO siblings named in SANDBOX_PRIVATE_SUFFIXES — exact, never
    by dot: `<seat>.anything` and `<seat>.toolhome-evil` are parent writes (P3a-FIX)."""
    p = p.strip().strip('"').rstrip("/")
    if p == rel or p.startswith(rel + "/"):
        return True
    return any(p == rel + s or p.startswith(rel + s + "/")
               for s in SANDBOX_PRIVATE_SUFFIXES)


def _sandbox_rel(root: str, sandbox: str | None) -> str | None:
    """The sandbox path relative to the parent checkout, git-style (forward
    slashes), or None when the sandbox is not a subtree of the root (a foreign
    repo's clone under ~/fleet, the same tree, another drive on Windows) — then
    there is nothing to subtract and the caller judges every path."""
    if not sandbox:
        return None
    try:
        rel = os.path.relpath(sandbox, root)
    except ValueError:
        return None
    if rel in (os.curdir, "") or rel.split(os.sep)[0] == os.pardir:
        return None
    return rel.replace(os.sep, "/")


def _filtered_parent_status(root: str, sandbox: str | None = None) -> dict:
    """{path-part: XY} of `git status --porcelain --untracked-files=all`, logs/ excluded.

    The --isolate clone and every run log live under logs/ (clients.state_dir),
    so logs/ paths are the spawner's own, never a worker's leak. Only entries
    where EVERY path is under logs/ drop; a rename with one side outside
    (e.g. `R  catalog/x -> logs/x`) is kept, keyed by the non-logs side.

    With a `sandbox`, paths inside the seat tree (and its `.toolhome` /
    `.opencode-data` siblings) drop the same way — the state dir is not always
    git-ignored under logs/, and a sandbox-private write is not a parent leak.
    """
    # Untracked files count too: a worker with write rights (qoder
    # bypass_permissions, review of 6622d29) can drop a NEW file into the parent.
    rel = _sandbox_rel(root, sandbox)
    r = subprocess.run(["git", "-C", root, "status", "--porcelain",
                        "--untracked-files=all"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    out = {}
    if r.returncode != 0:
        return out
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        rest = line[3:] if len(line) > 3 else ""
        paths = [p.strip() for p in rest.split(" -> ")]
        if all(_porcelain_path_is_logs(p)
               or (rel is not None and _porcelain_path_is_sandbox(p, rel))
               for p in paths):
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
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode == 0 and r.stdout.strip():
        head = r.stdout.strip()
    branch = None
    r = subprocess.run(["git", "-C", root, "symbolic-ref", "-q", "--short", "HEAD"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode == 0 and r.stdout.strip():
        branch = r.stdout.strip()
    reflog_count = _reflog_len(root, branch) if branch else 0
    refs = {}
    r = subprocess.run(["git", "-C", root, "for-each-ref", "refs/heads",
                        "--format=%(refname)%00%(objectname)"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
    - any tracked path outside logs/ and outside the seat tree (the sandbox and
      its `.toolhome` / `.opencode-data` siblings) whose porcelain state is
      DIRTY after and differs from before (new dirt is the worker-shaped
      signal; a path that became clean - the orchestrator committing its own
      WIP - is not a leak).

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
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
    after_status = _filtered_parent_status(root, sandbox)
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
            capture_output=True, text=True, stdin=subprocess.DEVNULL)),
        _git_stdout(subprocess.run(
            ["git", "-C", path, "diff", "--raw", "--no-renames", "HEAD"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)),
    ]
    status = _git_stdout(subprocess.run(
        ["git", "-C", path, "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL))
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
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return proc.stdout.strip()


def _reflog_count(root: str, ref: str) -> int:
    """How many entries `ref`'s own reflog has (0 when it has none at all)."""
    r = subprocess.run(["git", "-C", root, "reflog", "show", ref],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
                            "-n", str(new), ref], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            continue
        for sha in [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]:
            if sha == base or sha in known:
                continue
            probe = subprocess.run(["git", "-C", path, "rev-list", "--max-count=1",
                                    base + ".." + sha],
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if probe.returncode == 0 and probe.stdout.strip():
                found.add(sha[:10])
    return sorted(found)


def sandbox_ref_heads(sandbox: str) -> dict:
    """``{refname: sha}`` for every ref the sandbox clone carries."""
    out = subprocess.run(["git", "-C", sandbox, "for-each-ref",
                          "--format=%(refname) %(objectname)"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
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
                          capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    tip = end.get("refs/heads/" + branch)
    if head and head not in known and head != tip and head not in moved:
        found.append(sandbox_ref_subject(sandbox, "HEAD-detached", head))
    return found


def sandbox_ref_subject(sandbox: str, ref: str, sha: str) -> str:
    """One line naming a ref, its short sha and its subject (worker text)."""
    out = subprocess.run(["git", "-C", sandbox, "log", "-1", "--format=%h %s", sha],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
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

    Measured 2026-09-25: l2-worker agents answered "all fixed" with placeholder
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
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    add = [p for p in others.stdout.split("\0")
           if p and not _porcelain_path_is_logs(p)]
    if add:
        subprocess.run(["git", "-C", sandbox, "add", "--", *add],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    done = subprocess.run(["git", "-C", sandbox, "-c", "user.name=autoos-worker",
                           "-c", "user.email=" + WORKER_EMAIL, "commit", "-am", msg],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        reason = done.stderr.strip() or done.stdout.strip()
        print("WIP-COMMIT FAILED: %s" % (reason.splitlines()[0] if reason else
              "git commit exited %d" % done.returncode), file=sys.stderr)
        return None
    sha = subprocess.run(["git", "-C", sandbox, "rev-parse", "HEAD"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    if not sha:
        return None
    if branch:
        current = subprocess.run(["git", "-C", sandbox, "branch", "--show-current"],
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        if current != branch:
            # A detached sandbox HEAD (the worker checked out a sha) leaves
            # the WIP commit on no branch; point the sandbox branch at it so
            # `take it: git fetch <path> <branch>` has something to fetch.
            subprocess.run(["git", "-C", sandbox, "branch", "-f", branch, "HEAD"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
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


def kill_store_dir(state: str | None = None) -> str:
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

    `state` overrides the state root for a reader handed one (a recovery plan
    pointed at another tree): the store is always <root>/kill, never a second layout.
    """
    return os.path.join(state or clients.state_dir(), "kill")


def kill_store_path(run_id: str, state: str | None = None) -> str:
    """The one record file for `run_id`, named so no run id escapes the store."""
    name = os.path.basename(os.path.normpath(str(run_id or "")))
    if not name or name.startswith(".") or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("bad run id %r" % (run_id,))
    return os.path.join(kill_store_dir(state), name + ".json")


def write_kill_record(run_id: str, record: dict) -> bool:
    """Write (or merge into) the runner's private record for one run.

    SB-A4 (D-103, the rest of item C) grew this from a group record into the run's
    whole decided-at-spawn identity: `run_id`, `mode` (review|write), `scope`,
    `dry_run`, `allow_mode_only`, the spawning `lane` (AO-JOB-LANE-ID),
    `created_at`, plus the `pgid`/`start` group record and the RUNMODEL `writer`
    the run resolves at its end. It is the only
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
        for key in ("pgid", "start", "writer", "attempt", "outside_writes"):
            if record.get(key) is not None:
                merged[key] = record[key]
        for key in ("mode", "scope", "dry_run", "allow_mode_only", "lane"):
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


def read_kill_record(run_id: str, state: str | None = None):
    """The private record, or None. A run with no record is a run whose group and
    mode were never decided by a server — the direct CLI path — and a killer must
    say so rather than kill what it cannot identify. `state` reads the store of
    that state root instead of the ambient one."""
    try:
        path = kill_store_path(run_id, state)
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


INTERPRETER_NAME_RE = re.compile(
    r'^(?:node|nodejs|python\d*(?:\.\d+)?|pythonw|py)(?:\.exe)?$',
    re.IGNORECASE
)

NODE_VALUE_FLAGS = {
    "-r", "--require", "--import", "--loader", "--experimental-loader",
    "-e", "--eval", "-p", "--print", "--env-file"
}

PYTHON_VALUE_FLAGS = {
    "-X", "-W", "-c", "-m", "--check-hash-based-pycs"
}

SHIM_NO_SCRIPT_FLAGS = {"-e", "--eval", "-p", "--print", "-c", "-m"}

# Shell executables a shim may invoke to run its real target (`cmd.exe /c`,
# `powershell.exe -File`, ...). None of these is ever the client itself, so
# none of them may be taken as the native target: doing that would hand the
# prompt back to a shell interpreter.
SHIM_SHELL_EXE_NAMES = frozenset((
    "cmd", "powershell", "pwsh", "bash", "sh", "zsh",
    "wsl", "conhost", "wscript", "cscript",
))


def _split_shim_line(line: str) -> list[str]:
    """Tokenize a shim command line preserving quotes and Windows backslashes."""
    escaped = line.replace("\\", "\x00")
    try:
        parts = shlex.split(escaped, posix=True)
    except ValueError:
        return []
    return [p.replace("\x00", "\\") for p in parts]


class _ShimTarget(tuple):
    """Target script and interpreter flags, preserving optional interpreter name."""

    def __new__(cls, target: str, flags: list[str], interp: str | None = None):
        obj = super().__new__(cls, (target, flags))
        obj.interp = interp
        return obj


def resolve_client_executable(cmd, _nt: bool | None = None) -> list:
    """`cmd` with the client's program replaced by what `shutil.which()` found.

    On Windows a client installed as a `.cmd`/`.ps1` shim is on PATH — so the spawner's
    own which() pre-check calls it installed — while `CreateProcess` appends only `.exe`
    and Popen of the bare name raised FileNotFoundError [WinError 2]. which() honours
    PATHEXT, so it finds the shim and Popen is handed its full path. `shell=True` is not
    the answer: it would put argv quoting and an injection surface behind every spawn.
    On POSIX which() returns the file execvp would have picked, so a client that
    works today is untouched. Every launch site goes through here — `run_client`
    (the first attempt and each fallthrough re-run) and the MCP runner's detached
    `run` — so one rule decides what starts, and a program that is not there is
    named, with the PATH that was searched, instead of traced back.

    On Windows (`os.name == 'nt'`), executing a `.cmd`/`.bat` shim passes command-line
    arguments through `cmd.exe`, which truncates at the first newline and mangles `%`, `^`,
    `&`, `|`, `<`, `>`, and quotes. To prevent prompt truncation, the real entry is resolved:
      1. For `opencode`, sibling `node_modules/@opencode/cli/bin/opencode.exe` relative
         to the shim directory is checked first (standard npm global install layout).
      2. If not found, the shim is parsed for its target executable or script (node/python).
         A node/python script target is launched directly via `node.exe` or `python.exe`.
      3. If no target can be resolved and the command line contains newlines, shell hazard
         characters, or exceeds ~7000 characters, the launch is refused loudly
         (ClientMissing) rather than truncating.
    On POSIX which() returns the file execvp would have picked, byte-identical.
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

    if _nt is None:
        _nt = (os.name == "nt")

    if not _nt:
        argv[index] = exe
        return argv

    ext = os.path.splitext(exe)[1].lower()
    if ext not in (".cmd", ".bat", ".ps1"):
        argv[index] = exe
        return argv

    shim_dir = os.path.dirname(os.path.abspath(exe))
    target = None
    flags = []
    interp = None
    base_name = os.path.basename(exe).lower()
    if name == "opencode" or base_name.startswith("opencode"):
        sibling = os.path.normpath(os.path.join(shim_dir, "node_modules", "@opencode", "cli", "bin", "opencode.exe"))
        if os.path.isfile(sibling):
            target = sibling

    if target is None:
        parsed = parse_shim_target(exe)
        if parsed is not None:
            target, flags = parsed
            interp = getattr(parsed, "interp", None)

    if target is not None:
        t_ext = os.path.splitext(target)[1].lower()
        if t_ext == ".exe":
            argv[index:index + 1] = [target]
            return argv
        elif t_ext in (".js", ".mjs", ".cjs"):
            interp_file = os.path.basename(interp) if interp else "node.exe"
            sibling = os.path.join(shim_dir, interp_file)
            if os.path.isfile(sibling):
                node_cand = sibling
            elif os.path.isfile(os.path.join(shim_dir, "node.exe")):
                node_cand = os.path.join(shim_dir, "node.exe")
            else:
                node_cand = (shutil.which(interp_file) or shutil.which("node.exe")
                             or shutil.which("node"))
            if node_cand:
                argv[index:index + 1] = [node_cand] + flags + [target]
                return argv
        elif t_ext == ".py":
            interp_base = os.path.basename(interp).lower() if interp else "python.exe"
            if interp_base in ("py", "py.exe"):
                sibling = os.path.join(shim_dir, interp_base)
                if os.path.isfile(sibling):
                    py_cand = sibling
                else:
                    py_cand = shutil.which("py.exe") or shutil.which("py") or "py"
            else:
                interp_file = os.path.basename(interp) if interp else "python.exe"
                sibling = os.path.join(shim_dir, interp_file)
                if os.path.isfile(sibling):
                    py_cand = sibling
                else:
                    py_cand = sys.executable or shutil.which("python.exe") or shutil.which("python")
            if py_cand:
                argv[index:index + 1] = [py_cand] + flags + [target]
                return argv

    args = argv[index + 1:]
    total_len = sum(len(a) for a in args) + max(0, len(args) - 1)
    has_hazards = total_len > 7000 or any(_has_shim_hazards(arg) for arg in args)
    if has_hazards:
        raise ClientMissing(
            "cannot spawn %s on Windows: prompt contains newlines or special characters "
            "(or exceeds command line length limit) and shim %s has no resolvable native "
            "executable or script target" % (name, exe))

    argv[index] = exe
    return argv


def _has_shim_hazards(text: str) -> bool:
    """True if string contains newlines, cmd.exe metacharacters, or exceeds command line length limit."""
    if not isinstance(text, str):
        return False
    if len(text) > 7000:
        return True
    return any(c in text for c in ("\n", "\r", "%", "^", "&", "|", "<", ">", '"', "'"))


def parse_shim_target(path: str) -> tuple[str, list[str]] | None:
    """Extract target executable or script path and interpreter flags from a Windows .cmd, .bat, or .ps1 shim.

    Shell executables named in the line (cmd.exe, powershell.exe, and the rest
    of `SHIM_SHELL_EXE_NAMES`) are never returned as the target, so a shim that
    only runs a shell resolves to nothing rather than to that shell.
    """
    if not path or not os.path.isfile(path):
        return None
    shim_dir = os.path.dirname(os.path.abspath(path))
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None

    value_flags = NODE_VALUE_FLAGS | PYTHON_VALUE_FLAGS

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        lower_line = line_clean.lower()
        if (line_clean.startswith("::")
                or line_clean.startswith("#")
                or lower_line.startswith("rem ")
                or lower_line.startswith("@rem")
                or lower_line == "rem"
                or lower_line == "@rem"):
            continue

        for marker in ("%~dp0\\", "%~dp0/", "%~dp0", "%dp0%\\", "%dp0%/", "%dp0%"):
            line_clean = line_clean.replace(marker, shim_dir + os.sep)
        for marker in ("$basedir/", "$basedir\\", "$basedir"):
            line_clean = line_clean.replace(marker, shim_dir + os.sep)

        tokens = _split_shim_line(line_clean)
        if not tokens:
            continue

        flags: list[str] = []
        interp: str | None = None
        has_no_script = False

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            cleaned = tok.lstrip('@&').strip('"`\'')
            if not cleaned:
                i += 1
                continue

            base = os.path.basename(cleaned).lower()
            if INTERPRETER_NAME_RE.match(base) and interp is None:
                interp = cleaned
                i += 1
                continue

            if cleaned.startswith("-"):
                flag_name = cleaned.split("=", 1)[0]
                if flag_name in SHIM_NO_SCRIPT_FLAGS or (
                    not flag_name.startswith("--") and any(
                        flag_name.startswith(f) for f in ("-e", "-c", "-m", "-p")
                    )
                ):
                    has_no_script = True
                    break

                is_value_flag = False
                val_in_token = False
                if flag_name in value_flags:
                    is_value_flag = True
                    val_in_token = ("=" in cleaned)
                else:
                    for vf in ("-X", "-W", "-r"):
                        if cleaned.startswith(vf) and not cleaned.startswith("--") and len(cleaned) > len(vf):
                            is_value_flag = True
                            val_in_token = True
                            break

                if is_value_flag:
                    if val_in_token:
                        flags.append(cleaned)
                        i += 1
                    else:
                        flags.append(cleaned)
                        if i + 1 < len(tokens):
                            val_tok = tokens[i + 1].strip('"`\'')
                            flags.append(val_tok)
                            i += 2
                        else:
                            i += 1
                    continue
                else:
                    flags.append(cleaned)
                    i += 1
                    continue

            ext = os.path.splitext(cleaned)[1].lower()
            if ext in (".js", ".mjs", ".cjs", ".py", ".exe"):
                if ext == ".exe":
                    if INTERPRETER_NAME_RE.match(base):
                        if interp is None:
                            interp = cleaned
                        i += 1
                        continue
                    shell_base = base[:-4] if base.endswith(".exe") else base
                    if shell_base in SHIM_SHELL_EXE_NAMES:
                        # This token names the shell that runs the rest of the
                        # line, not the client: skip it and keep looking, so a
                        # `cmd.exe /c "%~dp0real.exe"` shim still resolves to
                        # the real target and a shell-only shim resolves to
                        # nothing instead of to the shell.
                        i += 1
                        continue
                cand = cleaned if os.path.isabs(cleaned) else os.path.join(shim_dir, cleaned)
                norm = os.path.normpath(cand)
                if os.name != "nt":
                    norm = norm.replace("\\", "/")
                if os.path.isfile(norm) and not norm.lower().endswith((".cmd", ".bat", ".ps1")):
                    if ext == ".exe":
                        return _ShimTarget(norm, [], interp)
                    return _ShimTarget(norm, flags, interp)
            i += 1

        if has_no_script:
            return None

    return None


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
                                 cwd=ROOT, capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL)
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
    # D-807: one resolution for the record, read off the model the plan launched.
    plan_family, plan_family_source, plan_family_reason = run_model_family(
        plan.get("launched_model") or plan.get("model"), client=plan.get("client"))
    record = redact_record({
        "id": wid, "pid": pid, "pid_start": _proc_starttime(pid),
        "started": utc_now_iso(), "session_tag": plan.get("session_tag"),
        "client": plan.get("client"), "model": plan.get("model"),
        # FAMILYFENCE-b: the row names the model the run was ASKED to use and where
        # that ask came from, so a default never reads as a pin. What actually
        # answered is not here — it lives in the runner-private record, which is
        # the only writer store a worker cannot edit (R-orch-17).
        "model_source": plan.get("model_source"),
        # T2-RECORD-PIN item 2: the ask, the launch and the family, so a record
        # answers "was this the model I pinned, and whose family wrote it"
        # without a second read of the plan. AO-RUN-FAMILY-RECORD (D-807): `family`
        # is the launched model's RESOLVED family — a registry row, the vendor the
        # id itself names, or the own-account client's default — and null plus
        # `family_reason` when nothing names one. REJECT 3: this record is written
        # before the child exists, so nothing witnessed the model it resolves and
        # `family_source` says `planned-model` whatever layer placed it — what a
        # keeper may read as "this run meant to be a Qwen", never as "a Qwen wrote
        # the diff", which is the claim the review gate and the reviewer token read.
        "requested_model": plan.get("requested_model") or "",
        "launched_model": plan.get("launched_model") or plan.get("model") or "",
        **witnessed_family(plan_family, plan_family_source, plan_family_reason,
                           False),
        "route": route.get("combo") or "",
        "title": args.title or "", "cwd": plan.get("cwd"),
        "sandbox": (plan.get("sandbox") or {}).get("path", ""),
        "sandbox_base": (plan.get("sandbox") or {}).get("base_line", ""),
        "task_head": (task[0] if task else "")[:120], "depth": plan["depth"][0],
        "parent_run_id": parent,
        "host": socket.gethostname(),
        "task_dir": os.environ.get("AUTOOS_TASK_DIR") or None,
        "scope": scope_decision(plan.get("run_id"), attempt),
        "route_plan": route.get("route_plan")})
    # T2-RECORD-PIN item 2: a plan whose ask and launch disagree is refused at
    # the launch site; the reason rides on the record it already wrote, so `ps`
    # shows WHY the run stopped instead of a bare rc 2. Absent otherwise — a
    # null `reason` on every record would read as a refusal nobody made.
    mismatch = model_mismatch_refusal(plan)
    if mismatch is not None:
        record["reason"] = mismatch
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


def host_admission_config(registry=None) -> tuple:
    """(max_live_workers, mem_available_floor_mb) - the host's own limits.

    catalog/ai-registry.json's top-level `host_admission` is the single source
    (operator order 2026-10-08). A section that is absent, or a field that is not
    a number, takes the documented default rather than no limit: an unparseable
    cap must not read as an unbounded host. AUTOOS_ADMISSION_OFF=1 is the
    test-only escape, honoured by `host_admission_refusal`, not by the numbers.
    """
    if registry is None:
        try:
            registry = load_registry(REGISTRY_PATH)
        except (OSError, ValueError):
            registry = {}
    section = (registry or {}).get("host_admission")
    section = section if isinstance(section, dict) else {}
    out = []
    for field, default in (("max_live_workers", ADMISSION_MAX_LIVE_DEFAULT),
                           ("mem_available_floor_mb", ADMISSION_MEM_FLOOR_MB_DEFAULT)):
        value = section.get(field)
        out.append(value if isinstance(value, int) and not isinstance(value, bool)
                   else default)
    return tuple(out)


def mem_available_mb(path=None):
    """MemAvailable from /proc/meminfo in MB, or None where it cannot be read.

    The kernel writes it in kB. None is Windows, or a container mounted without
    /proc: the live-worker half of the rule still binds there, and the memory
    half stands down instead of refusing every run on a host it cannot measure.
    """
    path = path or os.environ.get(ADMISSION_MEMINFO_ENV) or MEMINFO_PATH
    try:
        with io.open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    fields = line.split()
                    if len(fields) >= 2:
                        return int(fields[1]) // 1024
                    return None
    except (OSError, ValueError):
        return None
    return None


def live_worker_count(directory=None) -> int:
    """The workers this host is running now - the rows `ps` shows, not the files,
    plus the host slots another spawner has claimed but not yet recorded.

    Same directory (the checkout's git-common `logs/workers`, so every worktree
    and every lane of it counts), same `died` verdict: a record whose pid is gone
    is a crashed worker and must not keep its host closed.

    HOSTADMISSION-RACE: a claim placeholder is a worker for this purpose and not
    for `ps`. The count and the taking of a slot happen together in
    `host_admission_claim`, so a spawner is never shown a slot another one has
    already claimed - which is the difference between a cap and a suggestion.
    """
    directory = workers_dir() if directory is None else directory
    live = sum(1 for row in visible_workers(directory)
               if row.get("state") == "running")
    return live + live_admission_reservations(directory)


def live_admission_reservations(directory: str) -> int:
    """Host slots claimed in `directory` by a spawner that is still alive.

    Reaps the stale ones on the way, exactly as the free-leg count does: a
    spawner killed between its claim and its worker record leaves a placeholder
    no one can release, and trusting it would keep the host closed forever.
    """
    live, stale = _reservation_rows(directory, None, ADMISSION_RESERVATION_SUFFIX)
    for path in stale:
        try:
            os.remove(path)
        except OSError:
            pass
    return len(live)


def host_admission_claim(registry=None, workers=None, meminfo=None):
    """Ask the host for a worker, and take the slot when it says yes.

    Returns (refusal, claim-token). The two are one critical section: the same
    fcntl lock the free leg uses, held only across the count and the write of
    this run's placeholder — not across the clone or the client start, which are
    minutes and belong to nobody's lock. N spawners that arrive at live=cap-1
    therefore admit one, not N: each one after the first counts the placeholder
    the one before wrote.

    A lock that cannot be taken (an unwritable state dir, a spawner wedged for a
    minute) degrades to today's best-effort count, as `free_slot_lock_take` does:
    it never refuses a run and never hangs one. The token is released by
    `free_reservation_release()` once this run's worker record exists — the
    record is the same fact, and `ps` reads it — or when the run is over.
    """
    directory = workers_dir() if workers is None else workers
    lock = free_slot_lock_take(directory)
    try:
        refusal = host_admission_refusal(registry=registry, workers=directory,
                                         meminfo=meminfo)
        if refusal is not None:
            return refusal, None
        return None, _reserve_free_slot(directory, None, None,
                                        suffix=ADMISSION_RESERVATION_SUFFIX,
                                        prefix="host")
    finally:
        free_slot_lock_give(lock)


def _admission_text(live: int, cap: int, free_mb, floor: int, which: str) -> str:
    """One shape for both halves, naming all four numbers: a caller that reads
    "queue or run on workstation" has to be able to tell which of the two ran
    out, and how far off it was, without a second command."""
    free = "unknown" if free_mb is None else "%d MB" % free_mb
    return ("host admission: %d live workers, cap %d; MemAvailable %s, floor %d MB: "
            "%s reached, queue or run on workstation"
            % (live, cap, free, floor, which))


def host_admission_refusal(registry=None, workers=None, meminfo=None):
    """Why this host cannot take another worker right now, or None to admit.

    Read-only, and it never waits (R-worker: queueing is the caller's job - the
    CLI's caller decides whether to sleep and retry, or to route the task to a
    different machine). `workers` and `meminfo` name the two sources for a
    caller that has them (a test); otherwise the host's own are read.
    """
    if os.environ.get(ADMISSION_OFF_ENV) == "1":
        return None
    cap, floor = host_admission_config(registry)
    live = live_worker_count(workers)
    free_mb = mem_available_mb(meminfo)
    # HOSTADMISSION-FAILOPEN: `free_mb is None` means the host could not be
    # measured (no /proc/meminfo: Windows, a container mounted without it, an
    # unreadable path), and the memory half of the rule then stands down — it
    # FAILS OPEN rather than refuse every run on a machine it cannot read. The
    # live-worker half does not: `live >= cap` still binds, and it is the half
    # that counts the slots claimed below it. A host that cannot say how much
    # memory it has free can always say how many workers it is running.
    if live >= cap:
        return _admission_text(live, cap, free_mb, floor, "the live-worker cap")
    if free_mb is not None and free_mb < floor:
        return _admission_text(live, cap, free_mb, floor, "the memory floor")
    return None


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
    # LAYERFENCE (fix 1, R-worker-06): a leaf never spawns, and this is the last
    # mile of that rule — the MCP profile hides the tool, but a leaf that reaches
    # for `tools/autoos-agent.py run` from its own shell is the same call. Checked
    # before the inbox, the budget, the route and any clone, because the first word
    # of a run that must not happen is a refusal, whatever the rest of the plan
    # would have said. rc EXIT_LAYER_FENCE, named in the module header.
    leaf = leaf_spawn_refusal()
    if leaf is not None:
        return refuse(leaf, EXIT_LAYER_FENCE)
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
    # T2-RECORD-PIN item 5 (D-284): the pins this lane may not launch, refused
    # on the way IN - before the budget prices a model, before a route is burned
    # and before anything is cloned. Both pins are read: --free-model only when
    # this run would actually use it (with --free, or named explicitly instead
    # of the default). One helper, the same one the MCP server calls.
    pins = [args.model]
    if args.free or (getattr(args, "free_model", None) or "") != DEFAULT_FREE_MODEL:
        pins.append(getattr(args, "free_model", None))
    for pin in pins:
        d284 = d284_model_refusal(pin)
        if d284 is not None:
            return refuse(d284, 2)
    daily_early_refusal = daily_gate_refusal(args)
    if daily_early_refusal is not None:
        return refuse(daily_early_refusal)
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
    # T2-RECORD-PIN items 3 and 1: settle every pin HERE, before the fence, the
    # free-model chain and the plan read it, so one value is what all three see.
    #
    # Item 3: a bare pin gets the single opencode.jsonc provider that declares
    # it. A --free model never reaches resolve_model (build_plan takes it
    # straight from args.free_model), so without this an id the config DOES
    # declare is launched unprefixed and only fails inside opencode. Both pins
    # are qualified: --model names the same slot as --free-model.
    if args.free:
        # F3: first, check pins that already contain '/' against the free pool
        # (they skip qualify_pinned_model). The free pool uses the client's
        # own spellings (e.g. opencode/... for zen free models).
        # A prefixed pin is also accepted if it is declared in opencode.jsonc
        # (same set qualify_pinned_model uses), because a --free run takes the
        # model straight from args.free_model and never reaches resolve_model.
        if registry is None:
            try:
                registry = load_registry(getattr(args, "registry", None) or REGISTRY_PATH)
            except (OSError, ValueError):
                registry = None
        free_pool = ((registry or {}).get("policy") or {}).get("free_client_models") or {}
        client_free = free_pool.get(client.name) or []
        declared = declared_models(cfg)
        for attr in ("model", "free_model"):
            pin = getattr(args, attr, None)
            if pin and "/" in str(pin):
                pin_str = str(pin)
                # Skip F3 free pool check for opencode/ models when the pool is
                # empty/absent: opencode/* free models are served by opencode itself
                # and keep their old behaviour (they don't need a declared pool).
                if client_free and pin_str not in client_free and pin_str not in declared:
                    return refuse("%s is not in the free pool for %s (declared: %s)"
                                  % (pin_str, client.name, ", ".join(client_free) or "none"), 2)
                # When pool is empty/absent, still refuse non-opencode pins
                # (e.g. foo/bar) that are not declared.
                if not client_free and pin_str not in declared and not pin_str.startswith("opencode/"):
                    return refuse("%s is not in the free pool for %s (declared: %s)"
                                  % (pin_str, client.name, ", ".join(client_free) or "none"), 2)
        # Then qualify bare pins (no '/' in original)
        for attr in ("model", "free_model"):
            pin = getattr(args, attr, None)
            if not pin or "/" in str(pin):
                continue
            qualified = qualify_pinned_model(cfg, pin)
            if qualified is None:
                return refuse("%s is not declared in opencode.jsonc providers "
                              "(a bare pin needs the provider prefix that "
                              "declares it, e.g. omniroute/%s)" % (pin, pin), 2)
            setattr(args, attr, qualified)
    if args.free and args.model:
        # Item 1: `--free --model X` launches X. It used to record X as the pin
        # (model_source=pinned) and launch the promo default anyway — a swap
        # nobody could see from the record. Two pins naming the same slot that
        # disagree are REFUSED with both spellings; a --free-model that says the
        # same model as --model is not a disagreement, and --model's spelling
        # wins because it is the pin the caller named first.
        if getattr(args, "free_model", None) in (None, DEFAULT_FREE_MODEL):
            args.free_model = args.model
        elif _model_pin_key(args.free_model) != _model_pin_key(args.model):
            return refuse(model_mismatch_text(args.model, args.free_model), 2)
        else:
            args.free_model = args.model
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
    except (RouteInputRequired, RouteDeferred, PrivacyRefused, GeminiRefused,
            ReviewBaseRefused) as exc:  # plan's / PRIV3's / D-255's / P1's own
        return refuse(str(exc))                        # message, no suffix added
    except ValueError as exc:  # CardError, NoRoute, an undeclared model
        return refuse("%s (see: tools/autoos-agent.py list)" % exc)
    # The early gate read the flags; the plan may have rewritten the model
    # since (a re-resolved route, a fenced free head), so the gate reads the
    # plan it would actually launch on, not just the argv.
    daily_refusal = daily_gate_refusal(args, plan)
    if daily_refusal is not None:
        return refuse(daily_refusal)
    # T2-RECORD-PIN item 2: the plan may have rewritten the pin after the flags
    # were checked (the reviewer override, a re-resolved route, a fenced free
    # head) - a launch on a model nobody asked for is refused HERE, before the
    # clone, the worker record and the client. Checked in a dry run too: that is
    # exactly what the MCP spawn path preflights with, so a swap cannot slip
    # past the server either.
    # T2-RECORD-PIN item 2: a plan that would launch a model nobody asked for is
    # refused before anything starts. Computed here, enforced after the leaf and
    # tier verdicts (so the isolation refusal still speaks first) and announced
    # by a --dry-run preview instead of failing it — a preview touches nothing,
    # and the MCP preflight reads the note the same run would refuse on.
    mismatch = model_mismatch_refusal(plan)
    # F2: D-284 also guards the resolved plan model (card, combo, reviewer
    # override, fallthrough re-plan), not just the CLI pin strings. The check
    # runs on plan["model"] which is what the launcher actually hands the client.
    d284_plan = d284_model_refusal(plan.get("model"))
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
    # T2-RECORD-PIN item 4: review-only tier 3 does not run an implement task.
    # Computed beside leaf_refusal and never instead of it — the isolation
    # refusal speaks first (an in-place tier-3 run is refused for isolation,
    # flag and all), and this one is announced by the preview and enforced
    # right after the leaf verdict, still before anything is cloned or started.
    tier_write_refusal = review_tier_write_refusal(
        route.get("tier"), route.get("card"),
        read_only=bool(route.get("read_only") or getattr(args, "read_only", False)))
    # REVROUTE (S2) item 2: an authored review card needs an eligible reviewer
    # before anything is started -- a review by the author's own model family is
    # not an independent one, and "everyone is rate-limited" is a wait (rc 9,
    # the same code SPAWNFREE's queue loop already handles), not a silent pass.
    refusal = review_run_refusal(route.get("review_plan"))
    if refusal is not None:
        return refusal
    # LAYERFENCE (fix 1, findings 2 and 3): the lane's own fence, on the tier the
    # plan RESOLVED to and not on the flag the caller typed — `--tier 1` and a card
    # that routes to tier 1 are the same seat. The MCP `spawn` calls the same
    # helper, so a lane's bash cannot reach the CLI and get a warmer answer than its
    # own server gave. Unmarked here (an L1 session, an operator shell) means the
    # gate is not about this run at all and answers None.
    layer_refusal = l2_spawn_refusal(
        route.get("tier"), route.get("card"), client=client.name,
        models=tuple(p for p in (args.model,
                                 getattr(args, "free_model", None)) if p))
    if layer_refusal is not None:
        return refuse(layer_refusal, EXIT_LAYER_FENCE)
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
        # end of the run, not the spawner's guess at the start of it. REVGATE2F:
        # one line is one seat; a ready lane needs two, from two different
        # families, so the note below names the floor for the second seat.
        print("record-line: AutoOS-Review: kind=cross-family author=%s reviewer=%s "
              "verdict=<fill in>" % ((route.get("card") or {}).get("author"), reviewer["model"]))
        print("note: a ready lane needs TWO record lines above, from two different "
              "families; family=<family> is optional and cross-checked")
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
              "post-run leak check (exit 7). What is and is not isolated: "
              "\"ISOLATION STATEMENT\" in isolate_clone (tools/autoos-agent.py)."
              % client.name)
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
            print("would run: sandbox from %s at %s (branch %s; one-commit "
                  "materialisation of allowed HEAD files, secrets excluded)" % (
                plan["sandbox"].get("source") or isolate_source(),
                plan["sandbox"]["path"], plan["sandbox"]["branch"]))
        print("would run: " + " ".join(shlex.quote(c) for c in plan["cmd"]))
        print("cwd: %s" % plan["cwd"])
        print("env: %s" % (", ".join(env_names) or "-"))
        if leaf_refusal is not None:
            print("note: this run would be refused: %s" % leaf_refusal)
        if tier_write_refusal is not None:
            # T2 item 4: the preview still prints the plan (planning touches
            # nothing), and says plainly that starting it is refused — worded
            # so it can never be read as the leaf gate's own line above.
            print("note: spawning this plan is refused: %s" % tier_write_refusal)
        if d284_plan is not None:
            # F2: the preview names the D-284 refusal the real run would refuse.
            print("note: spawning this plan is refused: %s" % d284_plan)
        if mismatch is not None:
            # T2 item 2: the preview names the swap the real run would refuse.
            print("note: spawning this plan is refused: %s" % mismatch)
        return 0
    # KEYDENY3b: the leaf fence returns here, after the preview above and before
    # anything is cloned or started.
    if leaf_refusal is not None:
        return refuse(leaf_refusal)
    # T2-RECORD-PIN item 4: enforced after the leaf verdict, so an in-place
    # tier-3 run still names --isolate first, and before the client, the clone
    # and the record — a refusal starts nothing.
    if tier_write_refusal is not None:
        return refuse(tier_write_refusal, 2)
    # F2: D-284 post-plan check on the resolved model (card, combo, reviewer
    # override). Enforced after tier/leaf checks, before client/clone/record.
    if d284_plan is not None:
        return refuse(d284_plan, 2)
    # T2-RECORD-PIN item 2: last of the plan gates — the launch model must be
    # the model that was asked for, and a route that rewrote the pin is refused
    # before the client, the clone and the record.
    if mismatch is not None:
        return refuse(mismatch, 2)
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
            try:
                from autoos_gateway_key import client_key_field
                field = client_key_field(os.environ)
            except Exception:
                field = "omniroute_server` or `omniroute_<host>"
            print("No OmniRoute client key: export AUTOOS_OMNIROUTE_KEY or add `%s` to "
                  "configuration/api-keys.yml (docs/api-keys.md) "
                  "(or use --free for a keyless run)." % field, file=sys.stderr)
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
    # AO-SPAWN-COOLDOWN-RETRY: the wait-and-retry-the-same-leg, ONCE per run.
    # Deliberately not counted in `fallthroughs` — a 3 s cooldown must not spend
    # the run's re-plan budget — but counted with it when a launch is numbered
    # below, because the kill store and the scope unit key on the attempt.
    cooldown_retries = 0
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
    # HOSTADMISSION: the host is a gate too. Read after the preview above (a dry
    # run starts nothing, so a full host never refuses one - an operator previews
    # a route before deciding where to run it) and before the clone, the worker
    # record and the client.
    # HOSTADMISSION-RACE: and the answer is *taken*, not just read. Counting the
    # live workers and recording this one were two steps with the clone between
    # them, so N spawners that arrived at live=cap-1 each counted the others as
    # absent and all N started - the storm the cap exists to prevent. One fcntl
    # critical section (the free leg's lock, the free leg's placeholder shape)
    # now covers the count and the claim of the slot; the placeholder is released
    # where this run's worker record takes its place, so the host never sees the
    # same run twice, and a spawner killed before its record is down is stale and
    # reaped by the next count.
    # HOSTADMISSION-PLACE: taken here, not at the top of the command, because a
    # claim is a held resource and every `return` between here and that handover
    # would leave one behind until this pid dies. The plan gates, the client and
    # the sign-in, the key, the provisioned directories and the free-leg queue all
    # refuse before it and claim nothing; a run that waited minutes for a free
    # model and then found the host full is refused with both slots released.
    # After this point exactly one path still exits before the record: the privacy
    # refusal below, and it releases the claim on its way out.
    admission, host_claim = host_admission_claim(registry=registry)
    if admission is not None:
        return refuse(admission, EXIT_HOST_ADMISSION)
    parent_snap = None
    watch_home = watch_paths = watch_before = None
    if plan["sandbox"]:
        sb = plan["sandbox"]
        # Snapshot the parent checkout before the run: a worker that writes
        # outside its clone (live 2026-09-26) must fail as a leak, not a NO-OP.
        # The parent is the repo the clone was forked from (the caller's cwd),
        # not the checkout this script happens to live in (KEYDENY3g item 7).
        source = sb.get("source") or isolate_source()
        parent_snap = parent_snapshot(source)
        # OUTSIDEWATCH (P3a): the tool caches the redirect above cannot cover
        # (a tool that ignores its env, or a write made with a hardcoded path)
        # are caught by watching the well-known dirs under the REAL home before
        # and after the run - a warning line, never a changed exit code.
        watch_home = os.path.expanduser("~")
        watch_paths = tool_watch_paths(watch_home)
        watch_before = tool_watch_snapshot(watch_paths)
        # I12: the plaintext-secret refusal runs BEFORE anything is created,
        # so a denied card leaves no ~/fleet directory in the operator's home.
        try:
            sandbox_root_prepare(source, sb["path"])
            isolate_clone(source, sb["path"], sb["branch"], sb.get("review_base"))
        except (PrivacyRefused, ReviewBaseRefused) as exc:
            # HOSTADMISSION-RACE: the one exit between the claim and this run's
            # worker record, so it is the one place that gives the host slot back
            # by hand instead of at the handover.
            free_reservation_release(host_claim)
            return refuse(str(exc))
        sb["base"] = subprocess.run(["git", "-C", sb["path"], "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()
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
    # F4: child_rc and attempt_start are assigned inside the loop but used
    # after the loop (in wip_commit and track_entry). Initialize them to
    # avoid NameError if the loop breaks early (e.g., on mismatch or D-284).
    child_rc = None
    attempt_start = None
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
        if fallthroughs:
            daily_refusal = daily_gate_refusal(args, plan)
            if daily_refusal is not None:
                print("autoos-agent: %s" % daily_refusal, file=sys.stderr)
                rc = 2
                break
        # SPAWNFREE (S2) item 2: a fallthrough re-run is a fresh start on the
        # same shared free account, so it passes the same gate (the first
        # attempt passed it before the clone was made). Breaking here still
        # runs the sandbox summary below, so the WIP commit the stopped
        # attempt left is announced. A --free run never reaches the track
        # record (track_entry returns None for it), so a queue timeout costs
        # the route nothing.
        # AO-SPAWN-COOLDOWN-RETRY seat note: a cooldown retry is a fresh start
        # too, and it spends no fallthrough — so the gate cannot key on
        # `fallthroughs` alone. This run's worker record is closed while it naps,
        # and another --free run claims the freed slot in that window; retrying
        # without a re-claim would run as cap+1 over policy.free_concurrency.
        if args.free and (fallthroughs or cooldown_retries):
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
                                                         attempt=fallthroughs + cooldown_retries + 1)
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
            # HOSTADMISSION-RACE: the same handover for the host's slot. The two
            # are never both live for one run, so a spawner that arrives while
            # this one runs counts one occupant, not two.
            free_reservation_release(host_claim)
            host_claim = None
        attempt_start = time.time()
        run_rc = None
        missing = None
        # T2-RECORD-PIN item 2: the second net, for the one plan the check
        # before the clone never saw — a fallthrough re-plan (next route, next
        # free model) built AFTER the first attempt stopped. The record this
        # attempt just wrote carries the reason and the rc, and the client is
        # not started. The first attempt cannot get here: the same predicate on
        # the same plan refused it before any clone existed.
        mismatch = model_mismatch_refusal(plan)
        # F2: D-284 also guards fallthrough re-plans (next route, next free model)
        d284_fallthrough = d284_model_refusal(plan.get("model"))
        if mismatch is not None:
            if worker_id is not None:
                try:
                    _worker_record_end(workers, worker_id, worker_rec, 2)
                except OSError:
                    pass  # the reason is already on the record; rc 2 stands
            print("autoos-agent: %s" % mismatch, file=sys.stderr)
            rc = 2
            break
        if d284_fallthrough is not None:
            if worker_id is not None:
                try:
                    _worker_record_end(workers, worker_id, worker_rec, 2)
                except OSError:
                    pass
            print("autoos-agent: %s" % d284_fallthrough, file=sys.stderr)
            rc = 2
            break
        try:
            run_rc = run_client(plan["cmd"], plan["cwd"], env, reap=not args.joinable,
                                capture=capture,
                                # SB-B merge: every attempt — the first and each
                                # fallthrough re-run — leads a new session, so every
                                # attempt re-records its group in the runner-private
                                # store `cancel` kills from.
                                run_id=plan.get("run_id"),
                                attempt=fallthroughs + cooldown_retries + 1)
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
        # AO-SPAWN-COOLDOWN-RETRY: its own class of stop, decided from the line
        # and not from the window it stated — a cooldown that states nothing is
        # still a cooldown, and still exits the run on the stop's rc.
        cooling = stop is not None and cooldown_stop(stop)
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
        if stop is not None and (rc in (0, 3, 6)
                                 or ((rate_limited or cooling) and rc == 1)):
            # SB-B (RATELIMITRETRY): rc 1 joins the upgrade only for a rate limit,
            # which is the shape the field actually failed in (the client printed
            # its 429 and exited 1). Other rc-1 exits stay the client's own failure
            # class: a provider that refused service is exit 8, a client that
            # crashed is not. AO-SPAWN-COOLDOWN-RETRY: the cooldown is the same
            # shape (both field runs), and leaving it at the client's 1 is what
            # made a refused leg read as a failed task.
            print("autoos-agent: PROVIDER-STOP: %s" % redact_output(stop), file=sys.stderr)
            rc = 8
        if rc == 0 and client.promo:
            clients.record_probe(client.name)
        # AO-SPAWN-COOLDOWN-RETRY: the gateway named the model and counted the
        # credential down in seconds — that is THIS leg, shortly, not a dead one.
        # Sleep the stated window out and re-launch the SAME plan: same combo,
        # same sandbox, same work in it, no re-plan, no bench, no fallthrough
        # spent. Once per run; a second cooldown, or a window too long or unstated,
        # falls through below exactly as a rate-limit stop does, and
        # --no-fallthrough ends the run on the stop's own rc. Nor is the stopped
        # attempt track-recorded like a fallthrough's: a leg that serves 3 s later
        # did not fail the route it belongs to.
        nap = cooldown_wait(stop) if cooling else None
        if nap is not None and not cooldown_retries and not args.joinable:
            cooldown_retries += 1
            nap += 1  # the window stated, plus a beat for the clock to roll over
            print("autoos-agent: cooldown on %s: %s - waiting %ds and re-running "
                  "the same route" % (plan["model"] or plan["route"].get("combo"),
                                      redact_output(stop), nap), file=sys.stderr)
            cooldown_sleep(nap)
            continue
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
                                             capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
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
                      "family": None, "source": WRITER_UNRESOLVED,
                      "family_reason": "the writer lookup raised %s"
                                       % type(exc).__name__}
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
    # HOSTADMISSION-RACE: the host slot on the same rule, for the run that never
    # got a worker record at all (a refused attempt, a registry that could not be
    # written). A spawner that returns before either release still exits, and its
    # placeholder is stale by pid for the next count.
    free_reservation_release(host_claim)
    host_claim = None
    if plan["sandbox"] and not args.joinable:
        sb = plan["sandbox"]
        branch = sb["branch"]
        changed = subprocess.run(["git", "-C", sb["path"], "status", "--short"],
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        # WIPfix: never lose a worker's uncommitted work. Commit it on the
        # sandbox branch, then re-read changed/ahead so a run that only ever
        # produced this WIP commit is no longer a NO-OP. A review run's
        # deliverable is its diff, so leave that one untouched.
        if changed and not plan["route"].get("review"):
            wip_sha = wip_commit(sb["path"], child_rc, stop, branch)
            if wip_sha:
                print("WIP-COMMITTED: %s" % wip_sha)
                changed = subprocess.run(["git", "-C", sb["path"], "status", "--short"],
                                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        ahead = subprocess.run(["git", "-C", sb["path"], "log", "--oneline",
                                sb["base"] + ".." + branch],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
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
            print(take_it_hint(q, sb["branch"], sb.get("base", "")))
            for ln in off_ref:
                ref, _, rest = ln.partition(" ")
                if ref.startswith("refs/heads/"):
                    print("take it: git fetch %s %s   (the worker's own branch)"
                          % (q, ref[len("refs/heads/"):]))
                else:
                    print("take it: git -C %s branch <name> %s   (detached HEAD)"
                          % (q, rest.split()[0]))
        # TOOLHOME (P3a): the sandbox-private toolhome is removed with the
        # sandbox, like the opencode data dir beside it.
        extra = " " + shlex.quote(toolhome_dir(sb["path"]))
        if client.name == "opencode":
            extra += " " + shlex.quote(sb["path"] + OPENCODE_DATA_SUFFIX)
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
        # LEAKTREE (P3a): `root` must be the tree the snapshot above was taken
        # of — both fall back to isolate_source(), not one to ROOT (a different
        # checkout), which compared snapshot and verdict across two trees.
        leak = parent_leak(parent_snap, root=sb.get("source") or isolate_source(),
                           sandbox=sb["path"])
        # OUTSIDEWATCH (P3a): sandbox-private writes never reach this list (the
        # redirect moved them), a real write to the operator's tool dirs does —
        # warning and record only, the exit code stays whatever the run earned.
        if watch_before is not None:
            outside = tool_watch_changes(watch_before,
                                         tool_watch_snapshot(watch_paths), watch_home)
            if outside:
                print("WARNING: --isolate run wrote outside its sandbox: %s"
                      % ", ".join(outside), file=sys.stderr)
                write_kill_record(plan.get("run_id"), {"outside_writes": outside})
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
                     help="pick the tier by hand (default: resolve --card, an empty card is l2-worker)")
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
                                     "(e.g. omniroute/l2-orchestrator) for a gateway client, "
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
    run.add_argument("--review-base", dest="review_base", metavar="SHA",
                     help="with --isolate: ride the parent's `git diff SHA..HEAD` into the "
                          "sandbox as %s, stamp \"sandbox base: <sha> HEAD <sha>\" in the seat "
                          "prompt and record, refuse an unknown SHA (rc 2)" % REVIEW_DIFF_FILE)
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
    run.add_argument("--registry",
                     help="registry to use (default: catalog/ai-registry.json)")
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
                      "inbox (REVGATE). Pass --brief and --report on every call: "
                      "the gate requires them as soon as the DIFF reaches R2, "
                      "whatever the card says (AO-WRITER-GUARDS, AO-READY-CALLERS)")
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
    ready_p.add_argument("--allow-unverified", dest="allow_unverified", metavar="REASON",
                         help="declare a sha that has no green pre-push record anyway "
                              "(D-110). An orchestrator's flag for a lane tested on "
                              "another host or pushed past its own hooks: the reason is "
                              "written on the inbox line as unverified=\"<reason>\", and "
                              "a writer clearing its own gate is not what this is for")
    ready_p.add_argument("--fixes-main", dest="fixes_main", action="store_true",
                         help="this lane fixes main itself: waive the main-ci-red gate "
                              "(T0-FREEZE). Honoured only with AUTOOS_FIXES_MAIN="
                              "<lane>@<sha> in the environment naming this lane's sha; "
                              "a bare flag is refused. Without a declared waiver a red "
                              "main freezes every lane; with it the lane is allowed "
                              "onto a red main and one note logs the <lane>@<sha> "
                              "and the inbox line carries fixes_main=\"<lane>@<sha>\"")
    ready_p.add_argument("--brief", metavar="PATH",
                         help="the rendered ops brief the writer was given (AO-WRITER-GUARDS "
                              "P4b): its canonical FILES line is the allow-list the lane's "
                              "diff is fenced against. Required when the DIFF reaches R2, "
                              "even for a card labelled code or docs; a lane that touches a "
                              "file outside it is refused naming every violating path "
                              "(scope-fence)")
    ready_p.add_argument("--report", metavar="PATH",
                         help="the writer's REPORT text: an ops lane must carry it and its "
                              "CHECK 1-6 evidence must be present and PASS, else the lane is "
                              "refused naming what is missing, failed or input_required "
                              "(report-checks)")
    ready_p.add_argument("--base", default="origin/main",
                         help="the diff's other end for the writer-guards gate, compared at "
                             "its merge base with --sha (default: %(default)s). Must resolve "
                             "to a commit that is a STRICT ancestor of --sha: the same commit, "
                             "a base that is not behind --sha, or a range that changed "
                             "nothing is refused")
    ready_p.add_argument("--task-type", dest="task_type", default="code",
                         choices=("ops", "code", "docs", "infra"),
                         help="what the card claimed (default: %(default)s). Only ever a "
                              "FLOOR: the guard is raised by what the diff actually touched")


def _parser_preflight(sub):
    preflight_p = sub.add_parser(
        "preflight", help="check that the writer-guard tools (yamllint, ansible-playbook, "
                          "ansible-lint, gitleaks, pre-commit) are on PATH and print the "
                          "report as JSON (AO-WRITER-GUARDS P4b): exit 0 all present, exit 3 "
                          "input_required with the documented install step. Installs nothing")
    # No CLI arguments: the lookup is injectable for tests only (HERMETIC, D-852).
    preflight_p.set_defaults(which=None)


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
    "preflight": _parser_preflight,
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
    "preflight": lambda args, cfg: cmd_preflight(args),
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
