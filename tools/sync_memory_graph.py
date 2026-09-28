#!/usr/bin/env python3
"""Emit NDJSON for the omnigraph `autoos` memory graph from this repo (only what is not loaded yet).

Sources (all read-only): docs/decisions/ (Decision nodes, DecidedIn -> autoos),
the unattended-orchestration skill's ``## Rules`` section (Rule nodes, one per
``R-<topic>-NN`` line, all ``must`` - they are mandatory standing orders),
docs/plans/*.md headline decisions + repo D-nnn decision lines (Decision nodes),
docs/tasks.md live rows (Task nodes), and a fixed Component set for the repo's
top-level dirs (PartOf -> autoos).

Why this shape (copied from the router's bin/sync-memory-graph, same project
graph): edges have no @key and duplicate on every re-load (structured-memory
operations.md rule 7), so every emitted slug is recorded in the local state
ledger ``.state/graph-loaded.txt`` (gitignored); ``--mark`` appends them only
after a load is confirmed. Load mode is always ``merge`` (never ``overwrite`` -
rule 8). Cross-project links to ``routing-d-NNN`` decisions are plain-text
``rationale`` references, not edges: Implements is Task->Decision and there is
no Decision->Decision implements edge, and fabricating a stand-in node would be
worse than a text reference.

Usage: tools/sync_memory_graph.py > out.ndjson   (then load with omnigraph `load` mode=merge)
       tools/sync_memory_graph.py --mark          (after a verified load: remember the emitted slugs)
       tools/sync_memory_graph.py --load          (POST new records to $OMNIGRAPH_BASE_URL/graphs/autoos/load, then --mark)
"""
import json
import os
import re
import sys
import urllib.request

GRAPH_ID = "autoos"
PROJECT_SLUG = "autoos"

# Kept small and meaningful: top-level dirs/modules that matter, not one node per file.
COMPONENTS = [
    ("autoos-windows-lib", "Windows installer library", "lib", "lib/windows/"),
    ("autoos-linux-lib", "Linux/macOS installer library", "lib", "lib/linux/"),
    ("autoos-catalog", "Software catalog (data)", "data", "catalog/"),
    ("autoos-orchestrator-harness", "Orchestrator harness (autoos-agent, resolver, registry)", "tooling", "tools/"),
    ("autoos-mcp-stack", "Self-hosted MCP stack (cluster, omnigraph, serena)", "infra", "infra/mcp-servers/"),
    ("autoos-docs", "Docs, plans, ADRs, task board", "docs", "docs/"),
    ("autoos-tests", "Test harnesses (no-framework suites)", "tests", "tests/"),
    ("autoos-agent-skills", "Project skills (single home)", "skills", ".agents/skills/"),
]

# D-nnn decision lines mined from this repo's own record (docs/plans, CHANGELOG,
# skill text - the routing counterparts routing-d-NNN already live in this same
# graph, so the reference below resolves by lookup, not by cross-graph edge).
# Kept to decisions this repo owns or directly implements; the router's own
# ledger decisions stay in the routing project.
DECISIONS = [
    ("autoos-adr-0001-transcript-session-discovery",
     "ADR 0001: discover Claude sessions from transcripts, not process command lines",
     "Win32_Process exposes no cwd and command-line parsing is silently wrong on both "
     "platforms; transcripts are the observable contract. Accepted 2026-09-11.",
     "accepted", "2026-09-11", ["autoos-orchestrator-harness"], None),
    ("autoos-adr-0002-restored-session-visible-terminal",
     "ADR 0002: a restored session must land in a terminal a human can see",
     "A TUI needs a TTY and a keyboard; nohup-to-/dev/null and hidden scheduled tasks "
     "are unrestorable. Accepted 2026-09-11.",
     "accepted", "2026-09-11", ["autoos-orchestrator-harness"], None),
    ("autoos-adr-0003-no-lifecycle-hooks",
     "ADR 0003: do not install Claude Code lifecycle hooks",
     "SessionStart/SessionEnd hooks in the user's global settings.json failed on their "
     "own terms; periodic snapshot only. Accepted 2026-09-11.",
     "accepted", "2026-09-11", ["autoos-orchestrator-harness"], None),
    ("autoos-adr-0004-ventoy-wsl-not-rufus",
     "ADR 0004: Ventoy is the default USB engine, WSL the Windows power path; Rufus stays a catalog entry",
     "Rufus has no scriptable interface, so --dry-run and idempotency cannot hold; "
     "Ventoy (scriptable multi-boot) and WSL2+usbipd-win carry the machinery. "
     "Accepted 2026-09-17. Implements routing-d-042 provenance thinking for image bytes.",
     "accepted", "2026-09-17", ["autoos-windows-lib", "autoos-linux-lib", "autoos-catalog"], "routing-d-042"),
    ("autoos-adr-0005-openai-reviewer-pool-not-backbone",
     "ADR 0005: the OpenAI Agents API is not the orchestration backbone; OpenAI joins as one reviewer pool",
     "Survey 2026-09-18: the swarm (L1/L2 Claude orchestrators, cheap L3 executors) stays; "
     "OpenAI reviews. Accepted 2026-09-18.",
     "accepted", "2026-09-18", ["autoos-orchestrator-harness"], None),
    ("autoos-adr-0006-launch-time-routing-resolver",
     "ADR 0006: routing rules live in a launch-time resolver, not in the combos; time never overrides a hard filter",
     "Record re-verified 2026-09-24 against current route ids. Accepted, trimmed. "
     "Implements routing-d-044 (per-role caps) and routing-d-089 (board work breakdown).",
     "accepted", "2026-09-24", ["autoos-orchestrator-harness"], "routing-d-044"),
    ("autoos-plan-usb-rescue-profile",
     "Plan: bootable Linux USB + rescue profile through the standard pipeline",
     "Image list becomes catalog data (catalog/images.json); --create-usb runs "
     "detect->select->plan->confirm->execute->report so --dry-run means something. "
     "docs/plans/2026-09-11-installer-usb-and-rescue-profile.md.",
     "accepted", "2026-09-11", ["autoos-catalog", "autoos-windows-lib", "autoos-linux-lib"], None),
    ("autoos-plan-opencode-ollama",
     "Plan: OpenCode + local Ollama, keyless first (corrected plan)",
     "The first draft was unimplementable (phantom deletions, unverified URLs, key "
     "paths violating hard rule 1); the corrected plan verifies the vendor URL, follows "
     "the script-provider pattern, and keeps keyless working. "
     "docs/plans/2026-09-14-opencode-ollama-orchestration.md.",
     "accepted", "2026-09-14", ["autoos-catalog"], None),
    ("autoos-plan-skills-placement",
     "Skills placement: .agents/skills/ is the single home",
     "Installers link each skill per client (.claude/skills, ~/.agents/skills, ...); "
     "never copy a skill into both places by hand. AGENTS.md section 8; "
     "docs/plans/skills-placement-decision.md.",
     "accepted", "2026-09-27", ["autoos-agent-skills"], None),
    ("autoos-plan-registry-mapping",
     "Plan: one model registry via field-level mapping (routing v2 phase 1)",
     "Every field of the six old files lands at a catalog/ai-registry.json path, "
     "dropped with a why, or derived; tools/registry-convert.py is the executable form. "
     "docs/plans/2026-09-25-registry-mapping.md. Implements routing-d-089.",
     "accepted", "2026-09-25", ["autoos-orchestrator-harness", "autoos-catalog"], "routing-d-089"),
    ("autoos-plan-restart-spec",
     "Plan: RESTART state card + generated context pack, per-role context caps",
     "State card (<=40 lines) + code-generated pack + events-since-card; caps from "
     "operator D-088 (orchestrators 500k, workers min(40%, 400k)), superseding D-085's "
     "interim 250k. docs/plans/2026-09-28-restart-spec.md. "
     "Implements routing-d-040, routing-d-044, routing-d-085.",
     "accepted", "2026-09-28", ["autoos-orchestrator-harness"], "routing-d-085"),
    ("autoos-plan-orch-a1-role-launch-profiles",
     "Plan: ORCH-A1 role launch profiles with pre-granted permissions (spec first)",
     "Every orchestrator/worker launches from a per-role settings file; spec approved "
     "by routing-00 before build. docs/plans/2026-09-28-orch-a1-role-launch-profiles-spec.md. "
     "Implements routing-d-117.",
     "accepted", "2026-09-28", ["autoos-orchestrator-harness"], "routing-d-117"),
    ("autoos-decision-d060-risk-tiered-finals",
     "D-060: final reviews risk-tiered (Sonnet final for high-risk only)",
     "card.risk was the writer's own typing; Q-013/D-060 makes high-risk (security, "
     "secrets, installer/root, skill rules) Sonnet-final with two diverse cheap "
     "cross-family reviews. Implements routing-d-060.",
     "accepted", "2026-09-28", ["autoos-orchestrator-harness"], "routing-d-060"),
    ("autoos-decision-d103-tested-lessons-become-rules",
     "D-103: every error/fix becomes a tested skill rule; leaf self-spawn fence",
     "R-orch-15 mechanically; FREEFENCE denies leaf spawn in bash+MCP. "
     "Implements routing-d-103.",
     "accepted", "2026-09-28", ["autoos-agent-skills"], "routing-d-103"),
    ("autoos-decision-d100-wslboot-default",
     "D-100: WSL2 is the default Windows route, setup.ps1 a thin bootstrap; native pwsh frozen",
     "Workstation lane WS-WSLBOOT (Q-019=A). Implements routing-d-100.",
     "accepted", "2026-09-28", ["autoos-windows-lib", "autoos-linux-lib"], "routing-d-100"),
    ("autoos-decision-d101-workstation-lanes",
     "D-101: Workstation-AutoOS L1 lanes (fleet clones, CI green + pwsh counts each)",
     "WS-SKILLWIN, WS-OS11-*, WS-WSLBOOT, WS-OLLAMA, WS-OMNIREMOTE, WS-DSCALL. "
     "Implements routing-d-101.",
     "accepted", "2026-09-28", ["autoos-windows-lib", "autoos-linux-lib"], "routing-d-101"),
]

RULE_COMPONENT = {
    "R-coord-09": "autoos-orchestrator-harness",
    "R-coord-01": "autoos-orchestrator-harness",
    "R-orch-12": "autoos-orchestrator-harness",
    "R-worker-02": "autoos-tests",
    "R-worker-07": "autoos-tests",
    "R-worker-08": "autoos-orchestrator-harness",
    "R-worker-01": "autoos-catalog",
    "R-orch-11": "autoos-catalog",
    "R-orch-10": "autoos-orchestrator-harness",
    "R-worker-06": "autoos-orchestrator-harness",
}


def repo_root(explicit=None):
    if explicit is not None:
        return str(explicit)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ledger_path(root):
    return os.path.join(repo_root(root), ".state", "graph-loaded.txt")


def loaded(root):
    try:
        with open(ledger_path(root), encoding="utf-8") as f:
            return set(f.read().split())
    except FileNotFoundError:
        return set()


def parse_skill_rules(root):
    """One (slug, statement) per R-<topic>-NN line in the skill's ## Rules section."""
    path = os.path.join(repo_root(root), ".agents", "skills",
                        "unattended-orchestration", "SKILL.md")
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return []
    section = text.split("## Rules", 1)[-1]
    out = []
    for line in section.splitlines():
        m = re.match(r"- (R-[a-z]+-\d+): (.+)$", line.strip())
        if m:
            rid, body = m.groups()
            out.append((rid, " ".join(body.split())[:600]))
    return out


def parse_task_board(root):
    """Live rows (Running/Queued/Open) from docs/tasks.md; Done/Closed history stays out."""
    path = os.path.join(repo_root(root), "docs", "tasks.md")
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("Task", "---"):
            continue
        task, owner, _started, _done, status = cells[:5]
        if re.match(r"(Done|Closed)\b", status):
            continue
        state = "active" if status.startswith("Running") else "planned"
        if status == "Open":
            state = "planned"
        slug = "autoos-task-" + re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:60]
        out.append((slug, task[:160], state, status[:300]))
    return out


def decide_rule_severity(statement):
    """All current R-*-NN lines are phrased mandatory; an explicitly softer one
    (may/consider/optionally) maps to should."""
    if re.search(r"\b(may|consider|optionally|preferred|usually)\b", statement, re.I):
        return "should"
    return "must"


def records(root=None):
    """(ledger_key, node, [edges]) for everything mineable. Pure: reads repo files only."""
    root = repo_root(root)
    out = []
    for slug, name, kind, location in COMPONENTS:
        out.append((slug,
                    {"type": "Component",
                     "data": {"slug": slug, "name": name, "kind": kind, "location": location}},
                    [{"edge": "PartOf", "from": slug, "to": PROJECT_SLUG}]))
    for slug, title, rationale, status, date, components, _routing_ref in DECISIONS:
        edges = [{"edge": "DecidedIn", "from": slug, "to": PROJECT_SLUG}]
        for comp in components:
            edges.append({"edge": "Affects", "from": slug, "to": comp})
        out.append((slug,
                    {"type": "Decision",
                     "data": {"slug": slug, "title": title[:300], "rationale": rationale[:1800],
                              "status": status, "date": date}},
                    edges))
    for rid, statement in parse_skill_rules(root):
        slug = "autoos-" + rid.lower()
        edges = [{"edge": "ConstrainsProject", "from": slug, "to": PROJECT_SLUG}]
        comp = RULE_COMPONENT.get(rid)
        if comp:
            edges.append({"edge": "ConstrainsComponent", "from": slug, "to": comp})
        out.append((slug,
                    {"type": "Rule",
                     "data": {"slug": slug,
                              "statement": f"{rid}: {statement}"[:800],
                              "severity": decide_rule_severity(statement)}},
                    edges))
    for slug, title, state, status in parse_task_board(root):
        out.append((slug,
                    {"type": "Task",
                     "data": {"slug": slug, "title": title, "state": state}},
                    [{"edge": "Tracks", "from": slug, "to": PROJECT_SLUG}]))
    return out


def emit(root=None, state=None):
    """Records whose ledger key is not yet in the state ledger."""
    done = loaded(root) if state is None else set(open(state).read().split()) \
        if os.path.exists(state) else set()
    return [(k, n, e) for k, n, e in records(root) if k not in done]


def mark(root=None, state=None, batch=None):
    """Append emitted ledger keys. Call only after a load is confirmed."""
    path = state or ledger_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(k + "\n" for k, _, _ in batch)


def post_load(base_url, token, lines):
    body = json.dumps({"branch": "main", "mode": "merge",
                       "data": "\n".join(lines) + "\n"}).encode()
    url = base_url.rstrip("/") + f"/graphs/{GRAPH_ID}/load"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def main(argv=None, root=None, env=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    root = repo_root(root)
    new = emit(root, ledger_path(root))
    if "--load" in argv:
        if not new:
            print("nothing new", file=sys.stderr)
            return 0
        lines = [json.dumps(n, ensure_ascii=False) for _, n, _ in new] + \
                [json.dumps(e) for _, _, es in new for e in es]
        res = post_load(env.get("OMNIGRAPH_BASE_URL", "http://localhost:8080"),
                        env["OMNIGRAPH_TOKEN"], lines)
        print("loaded:", {t["table_key"]: t["rows_loaded"]
                           for t in res.get("tables", [])}, file=sys.stderr)
        argv = list(argv) + ["--mark"]
    if "--mark" in argv:
        mark(root, ledger_path(root), new)
        print(f"marked {len(new)} records as loaded", file=sys.stderr)
        return 0
    for _, node, _ in new:
        print(json.dumps(node, ensure_ascii=False))
    for _, _, edges in new:
        for e in edges:
            print(json.dumps(e))
    print(f"{len(new)} new records", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
