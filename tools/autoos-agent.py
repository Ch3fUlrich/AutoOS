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
is t2-worker; `--card privacy=sensitive,ctx=1m` fails closed unless
--allow-training. A v2 card (any of kind/risk/spec/mode/deferrable/deadline/
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

--lean (opencode, claude): no serena/playwright/context7 for research and
review agents - about 0.7 GB less per agent (LEAN_DROP).

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
    python3 tools/autoos-agent.py run --tier 3 --clean "..."       # no-training twin
    python3 tools/autoos-agent.py run --tier 2 --model omniroute/t2-orchestrator "..."
    python3 tools/autoos-agent.py run --tier 1 --free "..."        # no keys at all
    python3 tools/autoos-agent.py run --tier 3 --dry-run "..."     # print the plan only
    python3 tools/autoos-agent.py context                          # this session's fill
    python3 tools/autoos-agent.py context --transcript s.jsonl --json
    python3 tools/autoos-agent.py heartbeat --inbox i.md --transcript s.jsonl --json
    python3 tools/autoos-agent.py route --card kind=review,paths=tools/registry.py --explain

--free maps every tier agent to one of opencode's own free models (default
opencode/big-pickle) through OPENCODE_CONFIG_CONTENT: no gateway, no key, no
spend - for exercising the tier chain and the permission fences. Free promo
models may train on prompts, so --free refuses --clean.

Never prints a key. The OmniRoute client key comes from AUTOOS_OMNIROUTE_KEY
or the `omniroute:` line of configuration/api-keys.yml and is handed to the
child through its environment only. One line per run is appended to
logs/orch-<date>.log (git-ignored), which the watchdog protocol reads.

Exit codes: 5 = an --isolate implement run changed nothing (NO-OP); 6 = a headless client auto-denied a tool and
exited 0 (HEADLESS-REFUSAL); the child's exit code; 2 bad arguments, card or route refused;
3 gateway, key or client binary missing, OR AUTOOS_AGENT_INBOX names an inbox with an active
PAUSE (R-pause-01); 4 depth budget exhausted.

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
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autoos_clients as clients  # noqa: E402
import autoos_context as ctx  # noqa: E402
import autoos_heartbeat as heartbeat  # noqa: E402
import autoos_measure as measure_mod  # noqa: E402
import autoos_resolver as resolver  # noqa: E402
import autoos_routing as routing  # noqa: E402
import autoos_track as track  # noqa: E402
from registry import private_safe, resolve_leg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIERS = {1: "t1-orchestrator", 2: "t2-worker", 3: "t3-reviewer"}
GATEWAY = "http://127.0.0.1:20128"
DEFAULT_FREE_MODEL = "opencode/big-pickle"
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


def lean_overlay(cfg: dict) -> dict:
    # opencode 2.x disables a server with `disabled: true` on a FULL entry:
    # `enabled` is not a v2 field (stripped without a warning, the server
    # still starts), and an entry without type/command is dropped as
    # malformed. The overlay document is merged last, so it wins per server.
    servers = (cfg.get("mcp") or {}).get("servers") or {}
    return {"mcp": {"servers": {n: dict(servers[n], disabled=True)
                                for n in LEAN_DROP if n in servers}}}


def outside_fence(data_dir: str) -> list:
    # opencode's default for paths outside the project is "ask", and --auto
    # approves every ask - live 2026-09-24 an isolated worker wrote to the
    # main checkout by absolute path. Deny outside paths, then re-allow the
    # scratch dirs opencode itself pages large tool output into.
    home = os.path.expanduser("~")
    allow = [os.path.join(home, ".local", "share", "opencode", "tool-output", "*"),
             os.path.join(home, ".local", "share", "opencode", "shell", "*", "*"),
             "/tmp/opencode/*"]
    allow.append(os.path.join(data_dir, "opencode", "*"))
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


class RouteInputRequired(ValueError):
    """A v2 card's resolver plan is input_required: no route survives the filters."""


class RouteDeferred(ValueError):
    """A v2 card's resolver plan is deferred and --no-defer was not given."""


def _resolve_route_v2(args, parsed_card: dict, cfg: dict, override: str | None) -> dict:
    """A v2 card is routed by the resolver, not select_combo (RUNV2, spec 6.1
    "run takes card v2"). Shares route_plan_for/autoos_resolver.plan with the
    `route` subcommand and the MCP `route` tool, so `run` and `route` can never
    disagree about the same card.

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
    registry = load_registry(REGISTRY_PATH)
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
    # the registry class of the combo that actually runs (an explicit --model may
    # have replaced the resolver's route); the track record keys on it
    route_class = registry.get("routes", {}).get(combo, {}).get("class")
    return {"tier": tier, "model": model, "combo": combo, "reason": reason, "card": card,
            "privacy": card["privacy"], "review": card["kind"] == "review",
            "bucket": result["bucket"], "class": route_class}


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


def resolve_route(args, cfg: dict, client) -> dict:
    """resolve_route_unchecked plus the PRIV3 check: a sensitive run whose
    explicit --model replaced the card's combo must still land on private-safe
    legs only (--allow-training keeps its documented, logged escape)."""
    route = resolve_route_unchecked(args, cfg, client)
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


def resolve_route_unchecked(args, cfg: dict, client) -> dict:
    """Tier/model/combo for this run: an explicit --tier, a v2 card through the
    resolver (RUNV2), or a v1 card through select_combo."""
    # --model names a gateway combo for opencode and the gateway clients; for
    # agy/claude/qoder it is the client's own model id and is not checked here.
    override = args.model if client.gateway else None
    if args.tier is not None:
        model = None if args.free else resolve_model(cfg, args.tier, args.clean, override)
        combo = (model or "").partition("#")[0].replace("omniroute/", "", 1) or None
        return {"tier": args.tier, "model": model, "combo": combo, "reason": "explicit-tier",
                "card": None, "privacy": "sensitive" if args.clean else "public",
                "review": args.tier == 3}
    parsed = routing.parse_card(args.card or "")
    if _is_v2_card(parsed):
        return _resolve_route_v2(args, parsed, cfg, override)
    card = routing.normalize(parsed)
    combo, reason = routing.select_combo(card, args.allow_training)
    tier = int(re.match(r"t(\d)-", combo).group(1))  # t2-worker-clean -> 2
    model = None if args.free else resolve_model(cfg, tier, False, override or "omniroute/" + combo)
    if override and model:  # an explicit --model wins over the card's combo, and says so
        combo, reason = model.partition("#")[0].replace("omniroute/", "", 1), reason + "+model"
    return {"tier": tier, "model": model, "combo": combo, "reason": reason, "card": card,
            "privacy": card["privacy"], "review": card["role"] == "review"}


def build_plan(args, cfg: dict) -> dict:
    client = clients.CLIENTS[args.client]
    route = resolve_route(args, cfg, client)
    depth, max_depth = clients.child_depth(os.environ, args.max_depth)
    env = {"AUTOOS_AGENT_DEPTH": str(depth), "AUTOOS_AGENT_MAX_DEPTH": str(max_depth)}
    overlay = {}
    title = args.title or ("t%d %s" % (route["tier"], args.task[:50]))
    if client.name == "opencode":
        agent = TIERS[route["tier"]]
        if args.free:
            model = args.free_model
            overlay.update(free_overlay(model))
        else:
            model = route["model"]
        if args.lean:
            overlay.update(lean_overlay(cfg))
        cmd = ["opencode", "run", "--standalone", "--agent", agent, "--model", model,
               "--title", title]
        if args.auto:
            cmd.append("--auto")
        cmd.append(args.task)
    else:
        agent = client.name
        level = "ask" if not args.auto else ("read" if route["review"] else "edit")
        model = args.model if not client.gateway else None
        joinable = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-") if args.joinable else None
        cmd = clients.build_command(client, args.task, route["combo"], level, model, joinable)
        if args.lean and "--strict-mcp-config" not in cmd:  # claude only: no MCP servers
            cmd[1:1] = ["--strict-mcp-config"]
        model = model or (route["combo"] if client.gateway else "(client default)")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    sandbox = None
    if args.isolate:
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
            overlay["permissions"] = outside_fence(env["XDG_DATA_HOME"])
    if overlay:
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(overlay)
    return {"agent": agent, "client": client.name, "model": model, "cmd": cmd, "env": env,
            "route": route, "depth": (depth, max_depth), "free": bool(args.free),
            "sandbox": sandbox, "cwd": sandbox["path"] if sandbox else os.getcwd()}


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
    cap = ctx.cap_for(model)
    tokens = fill["tokens"]
    pct = int(round(100 * tokens / cap)) if cap else 0
    return ({"tokens": tokens, "cap": cap, "pct": pct, "model": model,
             "transcript": path, "source": "default"}, 0)


def cmd_context(args) -> int:
    """Print the calling session's context fill (spec 6.1, caps 8.3).

    The fill comes from the Claude Code transcript's latest assistant usage
    record. `--model` overrides only the model the cap is looked up for; the
    tokens still come from the transcript. `--json` prints the same numbers as
    an object (its `source` is the cap's provenance, `default` until a probe
    measures one).
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
        registry = load_registry(REGISTRY_PATH)
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
# the spawner's own stdout/stderr, so a terminal stays a terminal for it.
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


def sandbox_verdict(route: dict, changed: str, ahead: str):
    """(rc override or None, message) for an --isolate run.

    Measured 2026-09-25: t2-worker agents answered "all fixed" with placeholder
    commit hashes and changed nothing. A run whose job is to implement must
    leave a commit or a change; an agent's report is not evidence.
    """
    if route.get("review") or changed or ahead:
        return None, ""
    return 5, ("NO-OP: the agent changed nothing in its sandbox - treat its report as "
               "unverified and the run as failed (exit 5)")


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
    run's served leg, effort and tokens are unknown to this process, so they
    are recorded as unknown/0 until the resolver measures them. rc is the same
    value the run exits with, the NO-OP override included.

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
    return {
        "route": route["combo"],
        "class": klass,
        "served_leg": "unknown",
        "bucket": route.get("bucket") or card.get("bucket_hint") or "unknown",
        "effort": "unknown",
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0,
        "latency_s": secs,
        "gate": "pass" if rc == 0 else "fail",
        "failure_class": None if rc == 0 else ("capability" if rc == 5 else "logic"),
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

    Drops a leg named in `unavailable_legs` and a leg whose provider is
    `available: false` -- the same rule autoos_resolver.serving_legs applies,
    kept local so this module carries no dependency on the resolver. Order is
    the route's own leg order (the priority strategy needs the first one).
    """
    unavailable = route.get("unavailable_legs") or {}
    providers = registry.get("providers") or {}
    out = []
    for leg in route.get("legs") or []:
        if leg in unavailable:
            continue
        provider_id, _ = resolve_leg(leg, registry)
        if providers.get(provider_id, {}).get("available") is False:
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
    client = clients.CLIENTS[args.client]
    if args.free and args.clean:
        return refuse("--free uses promo models that may train on prompts; it cannot be --clean.")
    if args.tier is not None and args.card is not None:
        return refuse("--tier and --card both pick the model; pass one of them.")
    if args.card is not None and args.clean:
        return refuse("--clean is for --tier; with a card say privacy=sensitive.")
    if args.free and client.name != "opencode":
        return refuse("--free is opencode's own free model; --client %s cannot use it." % client.name)
    if args.lean and client.name not in ("opencode", "claude"):
        return refuse("--lean is implemented for opencode and claude; %s would still start "
                      "its MCP servers." % client.name)
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
    if client.promo and route["privacy"] != "public":
        return refuse("%s is a promo client that may keep prompts; it runs privacy=public work only." % client.name)
    if route["reason"].endswith("allow-training"):
        print("autoos-agent: --allow-training: sensitive work goes to %s, whose leg trains on "
              "prompts (logged)." % route["combo"], file=sys.stderr)
    uses_key = client.gateway and not args.free
    env_names = sorted(plan["env"]) + (["AUTOOS_OMNIROUTE_KEY"] if uses_key else [])
    print("route: %s reason=%s routing=%s" % (route["combo"] or plan["model"], route["reason"],
                                              routing.ROUTING_VERSION))
    print("depth: %d/%d" % plan["depth"])
    if args.lean:
        print("lean: no %s" % (", ".join(LEAN_DROP) if client.name == "opencode" else "MCP servers"))
    if plan["sandbox"] and client.name != "opencode":
        print("note: --isolate gives %s a private clone as its cwd; the outside-path fence is "
              "opencode-only." % client.name)
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
    if plan["sandbox"]:
        sb = plan["sandbox"]
        os.makedirs(os.path.dirname(sb["path"]), exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--local", ROOT, sb["path"]], check=True)
        subprocess.run(["git", "-C", sb["path"], "switch", "-q", "-c", sb["branch"]], check=True)
        sb["base"] = subprocess.run(["git", "-C", sb["path"], "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        print("sandbox: %s (branch %s)" % (sb["path"], sb["branch"]))
    start = time.time()
    rc = run_client(plan["cmd"], plan["cwd"], env, reap=not args.joinable,
                    capture=client.name in CAPTURE_CLIENTS)
    rc, refusal = refusal_exit(int(rc), getattr(rc, "refusal", None) or "")
    if refusal is not None:
        print("autoos-agent: HEADLESS-REFUSAL: %s" % refusal, file=sys.stderr)
    log_run(plan, rc, time.time() - start, args.free)
    if rc == 0 and client.promo:
        clients.record_probe(client.name)
    if plan["sandbox"]:
        sb = plan["sandbox"]
        changed = subprocess.run(["git", "-C", sb["path"], "status", "--short"],
                                 capture_output=True, text=True).stdout.strip()
        ahead = subprocess.run(["git", "-C", sb["path"], "log", "--oneline", sb["base"] + "..HEAD"],
                               capture_output=True, text=True).stdout.strip()
        print("\nsandbox changes (uncommitted):\n" + (changed or "  (none)"))
        if ahead:
            print("sandbox commits:\n" + ahead)
        q = shlex.quote(sb["path"])
        print("review:  git -C %s diff" % q)
        print("take it: git fetch %s %s   (then review FETCH_HEAD)" % (q, sb["branch"]))
        extra = " " + shlex.quote(sb["path"] + ".opencode-data") if client.name == "opencode" else ""
        print("discard: rm -rf %s%s" % (q, extra))
        override, message = sandbox_verdict(plan["route"], changed, ahead)
        if override is not None and rc == 0:
            print(message)
            rc = override
        # Every finished --isolate run is a track-record observation (spec §5.6).
        tracked = track_entry(plan, rc, time.time() - start)
        if tracked is not None:
            record_run(TRACK_RECORD, tracked)
            propose_reprobe(tracked, REGISTRY_PATH, MEASURED_OVERLAY_PATH,
                            PROBE_PROPOSALS_LOG, sb["path"])
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Spawn one AutoOS tier agent (see module docstring).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show the tiers, their models and who may spawn whom")
    run = sub.add_parser("run", help="run one task on one tier")
    run.add_argument("--tier", type=int, choices=sorted(TIERS),
                     help="pick the tier by hand (default: resolve --card, an empty card is t2-worker)")
    run.add_argument("--card", help="task card, e.g. role=review,privacy=sensitive (or JSON)")
    run.add_argument("--no-defer", dest="no_defer", action="store_true",
                     help="a v2 card (RUNV2): ignore the resolver's deferral (state=deferred) "
                          "and run now instead of refusing with exit 2")
    run.add_argument("--allow-training", action="store_true",
                     help="let privacy=sensitive,ctx=1m use t1-orchestrator-clean, whose leg trains on prompts (logged)")
    run.add_argument("--client", choices=sorted(clients.CLIENTS), default="opencode")
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
    args = ap.parse_args(argv)
    if args.cmd == "context":
        return cmd_context(args)
    if args.cmd == "heartbeat":
        return cmd_heartbeat(args)
    if args.cmd == "route":
        return cmd_route(args)
    cfg = load_jsonc(os.path.join(ROOT, "opencode.jsonc"))
    return cmd_list(cfg) if args.cmd == "list" else cmd_run(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
