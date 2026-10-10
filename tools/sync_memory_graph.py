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
operations.md rule 7), so every emitted record carries a ledger key recorded in
the local state ledger ``.state/graph-loaded.txt`` (gitignored); ``--mark``
appends them only after a load is confirmed. Load mode is always ``merge``
(never ``overwrite`` - rule 8).

Cross-project links to ``routing-d-NNN`` decisions are now real edges, not
plain-text references (D-140): each live Task that cites a router decision emits
``Implements: Task -> Decision`` by slug, and a Decision whose text explicitly
supersedes another emits ``Supersedes: Decision -> Decision``. There is no
Decision->Decision ``Implements`` edge and no schema change. A bare ``D-NNN``
names the *same* decision number as ``routing-d-NNN`` by design (this repo's
D-NNN ids ARE the router's routing decision numbers), so both forms normalise to
one zero-padded slug (``D-88`` -> ``routing-d-088``). ``Implements`` and
``Supersedes`` have no node of their own, so each is emitted as an edge-only
record keyed ``"<edge>:<from>-><to>"`` - distinct from node-slug keys, so an
edge still flows once on a machine whose ledger predates this change. Pass a set
of known decision slugs (``known=`` / ``--known-slugs``) to drop citations that
do not resolve, each with a stderr warning; the default ``None`` skips nothing
in plain dump mode, but ``--load`` refuses an unvalidated edge batch (see Usage).

Known limitation: a Task's board row is its slug source (``autoos-task-<row>``),
so editing a row's wording mints a new Task slug and re-emits its Implements
edges while the old slug's edges stay in the graph (stale-edge churn). Deduping
by ledger key collapses identical keys within one run, not renamed rows across
runs. A Decision that cites a routing-d-NNN in its own text emits no
Decision->Decision ``Implements`` edge - no such schema edge exists.

Usage: tools/sync_memory_graph.py > out.ndjson   (then load with omnigraph `load` mode=merge)
       tools/sync_memory_graph.py --mark          (after a verified load: remember the emitted slugs)
       tools/sync_memory_graph.py --load          (POST new records to $OMNIGRAPH_BASE_URL/graphs/autoos/load, then --mark)
       tools/sync_memory_graph.py --known-slugs FILE --load

``--load`` refuses (non-zero, nothing marked) when the batch carries an
edge-only Implements/Supersedes record and no ``--known-slugs`` was given: an
unresolved citation would create a dangling edge. Build the slug list from the
graph's Decision slugs first, one per line:

    # query decisions() { match { $d: Decision } return { $d.slug } }
    # POST that to $OMNIGRAPH_BASE_URL/graphs/autoos/query, write each returned
    # slug on its own line to known-slugs.txt, then:
    tools/sync_memory_graph.py --known-slugs known-slugs.txt --load

``--known-slugs`` accepts ``-`` (stdin), a file path, or a comma/space-separated
list; an empty value, or a value starting with ``--``, is an error.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

GRAPH_ID = "autoos"
PROJECT_SLUG = "autoos"

# D-1038 A3: Omnigraph 0.13.0 answers 400 api_contract_mismatch to any request
# that does not declare the contract it speaks.
CONTRACT_HEADER = "omnigraph-http-api"
CONTRACT_VERSION = "0.13"

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


# D-140: router decisions are looked up by slug ``routing-d-NNN``. A citation may
# also appear bare (``D-085``); normalise both to the slug form. The lookbehind
# stops ``routing-d-085``'s own inner ``d-085`` from matching a second time.
_ROUTING_CITATION_RE = re.compile(r"routing-d-(\d+)|(?<![A-Za-z0-9-])d-(\d+)", re.I)

# A citation only becomes Supersedes when a supersede/replace verb introduces it;
# the window keeps a distant citation later in the same sentence out.
_SUPERSEDE_VERB_RE = re.compile(r"\b(supersed\w*|replac\w*)\b", re.I)
_SUPERSEDE_WINDOW = 60


def routing_citations(text):
    """Normalised, de-duplicated ``routing-d-NNN`` slugs cited in ``text``.

    A bare ``D-NNN`` is zero-padded to the canonical three-digit slug, so
    ``D-88`` and ``routing-d-88`` both become ``routing-d-088`` (one number
    space: this repo's D-NNN ids ARE the router's decision numbers).
    """
    out = []
    for m in _ROUTING_CITATION_RE.finditer(text or ""):
        number = int(m.group(1) or m.group(2))
        slug = "routing-d-" + str(number).zfill(3)
        if slug not in out:
            out.append(slug)
    return out


def supersedes_targets(text):
    """Slugs a Decision explicitly supersedes/replaces, else empty.

    The citation must sit in the same clause as the supersede/replace verb:
    the ``_SUPERSEDE_WINDOW`` after the verb is cut at the first '.', ';' or
    newline, so a later sentence's citation is never read as a replacement,
    and the window keeps a distant citation in a run-on sentence out. Only the
    first citation in that clause counts.
    """
    out = []
    for m in _SUPERSEDE_VERB_RE.finditer(text or ""):
        tail = (text or "")[m.end():m.end() + _SUPERSEDE_WINDOW]
        clause = re.split(r"[.;\n]", tail, maxsplit=1)[0]
        cited = routing_citations(clause)
        if cited and cited[0] not in out:
            out.append(cited[0])
    return out


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
        row = " ".join(cells)
        out.append((slug, task[:160], state, status[:300], row))
    return out


def decide_rule_severity(statement):
    """All current R-*-NN lines are phrased mandatory; an explicitly softer one
    (may/consider/optionally) maps to should. Softening words inside a trailing
    non-normative parenthetical (e.g. the ``(why: ...)`` rationale clause) do
    not count - only the normative part is matched."""
    normative = statement
    while True:
        stripped = re.sub(r"\s*\([^()]*\)\s*$", "", normative)
        if stripped == normative:
            break
        normative = stripped
    if re.search(r"\b(may|consider|optionally|preferred|usually)\b", normative, re.I):
        return "should"
    return "must"


def records(root=None, known=None):
    """(ledger_key, node, [edges]) for everything mineable. Reads repo files only.

    ``known`` is an optional set of decision slugs. A citation whose target is
    not in it is skipped with a stderr warning (never an error); ``None`` accepts
    every citation. ``Implements``/``Supersedes`` are edge-only records (``node``
    is ``None``) keyed ``"<edge>:<from>-><to>"`` so a machine whose ledger already
    holds the node slugs still emits each edge once.
    """
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
        for target in supersedes_targets(f"{title} {rationale}"):
            if known is not None and target not in known:
                print(f"warning: skipping Supersedes edge {slug} -> {target}: "
                      "unknown decision slug", file=sys.stderr)
                continue
            out.append((f"Supersedes:{slug}->{target}", None,
                        [{"edge": "Supersedes", "from": slug, "to": target}]))
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
    for slug, title, state, status, row in parse_task_board(root):
        out.append((slug,
                    {"type": "Task",
                     "data": {"slug": slug, "title": title, "state": state}},
                    [{"edge": "Tracks", "from": slug, "to": PROJECT_SLUG}]))
        for target in routing_citations(row):
            if known is not None and target not in known:
                print(f"warning: skipping Implements edge {slug} -> {target}: "
                      "unknown decision slug", file=sys.stderr)
                continue
            out.append((f"Implements:{slug}->{target}", None,
                        [{"edge": "Implements", "from": slug, "to": target}]))
    # Two board rows can mint the same Task slug once their titles collide
    # truncated to 60 chars; emit each ledger key once (edges have no @key, so
    # a duplicate edge record would duplicate on load).
    seen, deduped = set(), []
    for key, node, edges in out:
        if key in seen:
            continue
        seen.add(key)
        deduped.append((key, node, edges))
    return deduped


def emit(root=None, state=None, known=None):
    """Records whose ledger key is not yet in the state ledger."""
    done = loaded(root) if state is None else set(open(state).read().split()) \
        if os.path.exists(state) else set()
    return [(k, n, e) for k, n, e in records(root, known) if k not in done]


def mark(root=None, state=None, batch=None):
    """Append emitted ledger keys. Call only after a load is confirmed."""
    path = state or ledger_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(k + "\n" for k, _, _ in batch)


def load_confirmed(res, sent, edge_only=0):
    """True when the load response confirms the batch landed.

    A response with no per-table detail is no evidence either way. A node
    record may be re-sent idempotently (merge upserts by @key), so preserve
    the prior permissive behavior for a batch with no edge-only record; an
    edge-only record has no @key and would silently duplicate, so an
    unconfirmed edge batch must not be marked. Otherwise every reported table
    must be error-free and must report rows when records were sent.

    D-1038 A3 adds the 0.13.0 shape: flat ``total_entities`` is the evidence and
    must equal what was sent (fewer is a partial load, more is not this batch);
    a non-empty flat ``error`` means the whole load failed, there is no
    per-table list. A dict with neither ``tables`` nor ``total_entities`` is an
    unknown shape and reads as unconfirmed - the permissive node-only path would
    mark edges that never landed as loaded, and they are gone for good."""
    if not isinstance(res, dict):
        return False
    if res.get("error"):
        return False
    total = res.get("total_entities")
    if isinstance(total, int) and not isinstance(total, bool):
        return total == sent
    tables = res.get("tables") or []
    if not tables:
        return "tables" in res and edge_only == 0
    for t in tables:
        if t.get("error"):
            return False
        try:
            rows = int(t.get("rows_loaded", 0))
        except (TypeError, ValueError):
            return False
        if sent > 0 and rows == 0:
            return False
    return True


def load_summary(res):
    """Counts to log for a load response: the per-table map of the 0.8.1 shape,
    the per-type map (``{name: entities_loaded}`` over nodes + edges) of the
    0.13.0 shape. Tolerates any shape - this only feeds a log line."""
    if not isinstance(res, dict):
        return {}
    tables = res.get("tables") or []
    if isinstance(tables, list) and tables:
        return {t.get("table_key"): t.get("rows_loaded")
                for t in tables if isinstance(t, dict)}
    counts = {}
    for group in ("nodes", "edges"):
        rows = res.get(group) or []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    counts[row.get("name")] = row.get("entities_loaded")
    return counts


def post_load(base_url, token, lines):
    body = json.dumps({"branch": "main", "mode": "merge",
                       "data": "\n".join(lines) + "\n"}).encode()
    url = base_url.rstrip("/") + f"/graphs/{GRAPH_ID}/load"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "content-type": "application/json",
                                          CONTRACT_HEADER: CONTRACT_VERSION})
    return json.load(urllib.request.urlopen(req, timeout=120))


def read_known_slugs(spec):
    """Slugs from ``-`` (stdin), a file path, or a comma/space-separated list."""
    if spec == "-":
        text = sys.stdin.read()
    elif os.path.isfile(spec):
        with open(spec, encoding="utf-8") as f:
            text = f.read()
    else:
        text = spec
    return {s for s in re.split(r"[,\s]+", text.strip()) if s}


def _read_slugs_or_exit(spec):
    slugs = read_known_slugs(spec)
    if not slugs:
        raise SystemExit("error: --known-slugs resolved to no slugs")
    return slugs


def _take_known_slugs(argv):
    """Split ``--known-slugs VALUE`` / ``--known-slugs=VALUE`` out of argv.

    A missing value, an empty value, or a value starting with ``--`` (which
    would otherwise swallow a following flag such as ``--load``) is an error.
    """
    known, rest, i = None, [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--known-slugs":
            i += 1
            value = argv[i] if i < len(argv) else None
            if value is None or value.startswith("--"):
                raise SystemExit("error: --known-slugs needs a non-empty value")
            known = _read_slugs_or_exit(value)
        elif arg.startswith("--known-slugs="):
            known = _read_slugs_or_exit(arg.split("=", 1)[1])
        else:
            rest.append(arg)
        i += 1
    return known, rest


def main(argv=None, root=None, env=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    root = repo_root(root)
    known, argv = _take_known_slugs(list(argv))
    new = emit(root, ledger_path(root), known)
    edge_only = sum(1 for _, node, _ in new if node is None)
    if "--load" in argv:
        if not new:
            print("nothing new", file=sys.stderr)
            return 0
        if known is None and edge_only:
            print(f"ERROR: --load would emit {edge_only} edge-only record(s) "
                  "(Implements/Supersedes) with no --known-slugs, so a citation "
                  "that does not resolve would create a dangling edge. Pass "
                  "--known-slugs with the graph's Decision slugs (see the "
                  "module Usage) or run without --load.", file=sys.stderr)
            return 1
        lines = [json.dumps(n, ensure_ascii=False) for _, n, _ in new if n is not None] + \
                [json.dumps(e) for _, _, es in new for e in es]
        try:
            res = post_load(env.get("OMNIGRAPH_BASE_URL", "http://localhost:8080"),
                            env["OMNIGRAPH_TOKEN"], lines)
        except urllib.error.HTTPError as exc:
            print(f"ERROR: the omnigraph server rejected the load "
                  f"(HTTP {exc.code} {exc.reason}); ledger NOT marked - fix and "
                  "retry.", file=sys.stderr)
            return 1
        except urllib.error.URLError as exc:
            print(f"ERROR: could not reach the omnigraph server "
                  f"({exc.reason}); ledger NOT marked - fix and retry.",
                  file=sys.stderr)
            return 1
        print("loaded:", load_summary(res), file=sys.stderr)
        if not load_confirmed(res, len(lines), edge_only):
            print("ERROR: load NOT confirmed by server response "
                  f"({res!r}); refusing to mark {len(new)} records as loaded. "
                  "Ledger untouched - fix the load and retry.",
                  file=sys.stderr)
            return 1
        argv = list(argv) + ["--mark"]
    if "--mark" in argv:
        mark(root, ledger_path(root), new)
        print(f"marked {len(new)} records as loaded", file=sys.stderr)
        return 0
    for _, node, _ in new:
        if node is not None:
            print(json.dumps(node, ensure_ascii=False))
    for _, _, edges in new:
        for e in edges:
            print(json.dumps(e))
    print(f"{len(new)} new records", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
