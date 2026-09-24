#!/usr/bin/env python3
"""Spawn one tier agent with the right model, key and isolation - one command.

The 3-tier agents live in opencode.jsonc (tier1-orchestrator -> tier2-worker ->
tier3-reviewer, docs/unattended-orchestration.md). Driving them by hand has
four traps, all measured 2026-09-24 against opencode 2.0.16:

  1. `opencode run --agent tier2-worker` runs on the TOP-LEVEL default model
     (omniroute/tier1), not the agent's own - the agent/model pairing only
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

Usage:
    python3 tools/autoos-agent.py list
    python3 tools/autoos-agent.py run --tier 3 "Review lib/linux/ui.sh for quoting bugs"
    python3 tools/autoos-agent.py run --tier 2 --isolate "Add a test for X"
    python3 tools/autoos-agent.py run --tier 3 --clean "..."       # no-training twin
    python3 tools/autoos-agent.py run --tier 2 --model omniroute/tier2-credit "..."
    python3 tools/autoos-agent.py run --tier 1 --free "..."        # no keys at all
    python3 tools/autoos-agent.py run --tier 3 --dry-run "..."     # print the plan only

--free maps every tier agent to one of opencode's own free models (default
opencode/big-pickle) through OPENCODE_CONFIG_CONTENT: no gateway, no key, no
spend - for exercising the tier chain and the permission fences. Free promo
models may train on prompts, so --free refuses --clean.

Never prints a key. The OmniRoute client key comes from AUTOOS_OMNIROUTE_KEY
or the `omniroute:` line of configuration/api-keys.yml and is handed to the
child through its environment only. One line per run is appended to
logs/orch-<date>.log (git-ignored), which the watchdog protocol reads.

Exit codes: the child's exit code; 2 bad arguments; 3 gateway or key missing.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIERS = {1: "tier1-orchestrator", 2: "tier2-worker", 3: "tier3-reviewer"}
GATEWAY = "http://127.0.0.1:20128"
DEFAULT_FREE_MODEL = "opencode/big-pickle"


def load_jsonc(path: str) -> dict:
    # Same comment strip as both suites: whole-line // comments only.
    text = io.open(path, encoding="utf-8").read()
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


def client_key(root: str) -> str | None:
    key = os.environ.get("AUTOOS_OMNIROUTE_KEY")
    if key:
        return key
    path = os.path.join(root, "configuration", "api-keys.yml")
    if not os.path.isfile(path):
        return None
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


def build_plan(args, cfg: dict) -> dict:
    agent = TIERS[args.tier]
    env = {}
    overlay = {}
    if args.free:
        model = args.free_model
        overlay.update(free_overlay(model))
    else:
        model = resolve_model(cfg, args.tier, args.clean, args.model)
    title = args.title or ("t%d %s" % (args.tier, args.task[:50]))
    cmd = ["opencode", "run", "--standalone", "--agent", agent, "--model", model,
           "--title", title]
    if args.auto:
        cmd.append("--auto")
    cmd.append(args.task)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    sandbox = None
    if args.isolate:
        name = "%s-%s-%s" % (os.path.basename(ROOT), stamp, slugify(args.task))
        state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        sandbox = {"path": os.path.join(state, "autoos", "sandboxes", name),
                   "branch": "agent/%s-%s" % (stamp, slugify(args.task))}
        # opencode keys a project by its root commit and remembers the root it
        # saw first; a private data dir keeps the clone from inheriting the
        # main checkout's recorded root.
        env["XDG_DATA_HOME"] = sandbox["path"] + ".opencode-data"
        overlay["permissions"] = outside_fence(env["XDG_DATA_HOME"])
    if overlay:
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(overlay)
    return {"agent": agent, "model": model, "cmd": cmd, "env": env,
            "sandbox": sandbox, "cwd": sandbox["path"] if sandbox else os.getcwd()}


def cmd_list(cfg: dict) -> int:
    for tier, agent in TIERS.items():
        a = cfg["agents"][agent]
        allow = [p["resource"] for p in a.get("permissions", [])
                 if p["action"] == "subagent" and p["effect"] == "allow"]
        print("tier%d  %-19s %-24s spawns: %s" % (tier, agent, a["model"], ", ".join(allow) or "nothing"))
    omni = sorted(m for m in declared_models(cfg) if m.startswith("omniroute/"))
    print("\n--model accepts any declared model, e.g.: " + ", ".join(omni))
    print("--free runs every tier on %s (no key, no gateway)" % DEFAULT_FREE_MODEL)
    return 0


def log_run(plan: dict, rc: int, secs: float, free: bool) -> None:
    logs = os.path.join(ROOT, "logs")
    os.makedirs(logs, exist_ok=True)
    line = "%s agent=%s model=%s free=%d sandbox=%s rc=%d secs=%.0f\n" % (
        datetime.datetime.now().isoformat(timespec="seconds"), plan["agent"], plan["model"],
        int(free), (plan["sandbox"] or {}).get("path", "-"), rc, secs)
    with io.open(os.path.join(logs, "orch-%s.log" % datetime.date.today().isoformat()), "a", encoding="utf-8") as fh:
        fh.write(line)


def cmd_run(args, cfg: dict) -> int:
    if args.free and args.clean:
        print("--free uses promo models that may train on prompts; it cannot be --clean.", file=sys.stderr)
        return 2
    try:
        plan = build_plan(args, cfg)
    except ValueError as exc:
        print("autoos-agent: %s (see: tools/autoos-agent.py list)" % exc, file=sys.stderr)
        return 2
    env_names = sorted(plan["env"]) + ([] if args.free else ["AUTOOS_OMNIROUTE_KEY"])
    if args.dry_run:
        if plan["sandbox"]:
            print("would run: git clone --local %s %s && git switch -c %s" % (ROOT, plan["sandbox"]["path"], plan["sandbox"]["branch"]))
        print("would run: " + " ".join(shlex.quote(c) for c in plan["cmd"]))
        print("cwd: %s" % plan["cwd"])
        print("env: %s" % (", ".join(env_names) or "-"))
        return 0
    # PWD too, not just cwd=: opencode takes the project directory from $PWD,
    # so an inherited PWD sent an isolated worker's writes to the caller's
    # checkout (live 2026-09-24).
    env = dict(os.environ, **plan["env"], PWD=plan["cwd"])
    if not args.free:
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
    # stdin closed: when it is an open pipe (cron, CI, an agent's shell)
    # `opencode run` waits to read it as extra prompt text and never starts
    # (measured 2026-09-24: 150 s hang vs 6 s with /dev/null).
    rc = subprocess.call(plan["cmd"], cwd=plan["cwd"], env=env, stdin=subprocess.DEVNULL)
    log_run(plan, rc, time.time() - start, args.free)
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
        print("discard: rm -rf %s %s" % (q, shlex.quote(sb["path"] + ".opencode-data")))
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Spawn one AutoOS tier agent (see module docstring).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show the tiers, their models and who may spawn whom")
    run = sub.add_parser("run", help="run one task on one tier")
    run.add_argument("--tier", type=int, choices=sorted(TIERS), required=True)
    run.add_argument("--clean", action="store_true", help="use the -clean (paid, no free legs) twin")
    run.add_argument("--model", help="a model declared in opencode.jsonc, e.g. omniroute/tier2-credit")
    run.add_argument("--free", action="store_true", help="keyless: every tier on opencode's free model")
    run.add_argument("--free-model", default=DEFAULT_FREE_MODEL)
    run.add_argument("--isolate", action="store_true",
                     help="run in a private git clone on its own branch; writes outside it are denied")
    run.add_argument("--no-auto", dest="auto", action="store_false",
                     help="ask before tools the config does not explicitly allow (default: --auto)")
    run.add_argument("--title")
    run.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    run.add_argument("task")
    args = ap.parse_args(argv)
    cfg = load_jsonc(os.path.join(ROOT, "opencode.jsonc"))
    return cmd_list(cfg) if args.cmd == "list" else cmd_run(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
