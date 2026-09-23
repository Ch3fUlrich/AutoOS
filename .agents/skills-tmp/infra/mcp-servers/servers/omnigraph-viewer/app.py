"""Read-only web viewer for the Omnigraph memory graphs.

Holds the Omnigraph bearer token server-side (the browser never sees it) and exposes the
graphs to the browser as JSON. The frontend (index.html-in-a-string below) renders graph
chips, an interactive force-directed graph with focus-on-click exploration, a
filterable/sortable table, and search highlighting.

Per-project isolation means one graph per repo, so **graph ≈ project**. The chips in the
tab bar select which graphs are on screen: click one to switch fast, ctrl/cmd/shift-click
to add more and compare several at once. Node ids are namespaced `<graph>::<slug>` so
slugs from different graphs can never collide or appear to be joined — edges only ever
exist inside one graph.

Human auth is handled in front of this service (Authelia via Caddy); the app has no auth
of its own — never expose it without the SSO/proxy in front.
"""
import fcntl
import html
import json
import os
import socket
import struct
import time

import requests
from flask import Flask, Response, jsonify, request
from urllib.parse import quote

OMNIGRAPH_URL = os.environ.get("OMNIGRAPH_URL", "http://omnigraph-server:8080").rstrip("/")
OMNIGRAPH_TOKEN = os.environ.get("OMNIGRAPH_TOKEN", "")
# Default graph the page opens on. `memory` now holds ONLY global-scope Preferences, so it
# is deliberately small — the chips switch to the project graphs where the content lives.
GRAPH_ID = os.environ.get("OMNIGRAPH_GRAPH", "memory")
TIMEOUT = float(os.environ.get("OMNIGRAPH_TIMEOUT", "15"))

app = Flask(__name__)
_headers = {"Authorization": f"Bearer {OMNIGRAPH_TOKEN}"}

NODE_TYPES = ["Project", "Decision", "Rule", "Preference", "Convention", "Component", "Task"]
# Which field is the human label / the main body text, per node type.
LABEL_FIELD = {
    "Project": "name", "Decision": "title", "Rule": "statement",
    "Preference": "statement", "Convention": "name", "Component": "name", "Task": "title",
}
HUB_EDGES = {"DecidedIn", "ConstrainsProject", "AppliesTo", "PartOf", "Tracks"}


def _graphs():
    """All graph IDs the cluster exposes (for the graph chips)."""
    try:
        r = requests.get(f"{OMNIGRAPH_URL}/graphs", headers=_headers, timeout=TIMEOUT)
        r.raise_for_status()
        ids = [g.get("graph_id") or g.get("graphId") for g in r.json().get("graphs", [])]
        return sorted(g for g in ids if g) or [GRAPH_ID]
    except Exception:  # noqa: BLE001
        return [GRAPH_ID]


def _resolve_graphs(raw):
    """Parse ?graph=a,b,c -> only the graphs the cluster actually exposes."""
    known = _graphs()
    want = [g.strip() for g in (raw or GRAPH_ID).split(",") if g.strip()]
    out = [g for g in want if g in known]
    return out or [GRAPH_ID if GRAPH_ID in known else known[0]]


def _branches(graph):
    try:
        r = requests.get(f"{OMNIGRAPH_URL}/graphs/{graph}/branches", headers=_headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("branches", ["main"])
    except Exception:  # noqa: BLE001
        return ["main"]


def _export(branch, graph):
    """POST export -> list of NDJSON records (nodes + edges) for a branch."""
    url = f"{OMNIGRAPH_URL}/graphs/{graph}/export"
    params = {"branch": branch} if branch and branch != "main" else None
    r = requests.post(url, json={}, params=params, headers=_headers, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for line in r.text.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _build_one(branch, graph):
    """Nodes + edges for a single graph, with ids namespaced `<graph>::<slug>`."""
    records = _export(branch, graph)
    nid = lambda slug: f"{graph}::{slug}"  # noqa: E731
    nodes, edges, edges_seen = {}, [], set()
    for rec in records:
        if "edge" in rec:
            key = (rec["edge"], rec.get("from"), rec.get("to"))
            if key in edges_seen:
                continue  # de-dup (edges aren't slug-keyed, merges can duplicate)
            edges_seen.add(key)
            edges.append({"type": rec["edge"], "from": nid(rec.get("from")),
                          "to": nid(rec.get("to")), "graph": graph})
        elif "type" in rec:
            data = rec.get("data", {})
            slug = data.get("slug")
            if slug:
                nodes[slug] = {"type": rec["type"], "data": data}

    project_slugs = {s for s, n in nodes.items() if n["type"] == "Project"}
    proj_of = {s: set() for s in nodes}
    for rec in records:  # attribute each node to the project(s) it hub-edges into
        if "edge" in rec and rec.get("edge") in HUB_EDGES:
            if rec.get("to") in project_slugs and rec.get("from") in proj_of:
                proj_of[rec["from"]].add(rec["to"])
    for s in project_slugs:
        proj_of[s].add(s)

    out_nodes = []
    for slug, n in nodes.items():
        data = n["data"]
        label = data.get(LABEL_FIELD.get(n["type"], "slug")) or slug
        projs = sorted(proj_of.get(slug, set()))
        is_global = (data.get("scope") == "global") or (not projs and n["type"] != "Project")
        out_nodes.append({
            "id": nid(slug), "slug": slug, "graph": graph, "type": n["type"], "label": label,
            "fields": data, "projects": [nid(p) for p in projs], "global": is_global,
        })
    projects = [{"id": nid(s), "name": nodes[s]["data"].get("name", s), "graph": graph}
                for s in sorted(project_slugs)]
    return out_nodes, edges, projects


def _build_graph(branch, graphs):
    """Merge several graphs into one view. Ids are namespaced, so no cross-graph edges."""
    all_nodes, all_edges, all_projects, errors = [], [], [], {}
    for g in graphs:
        try:
            # A branch only exists within its own graph; fall back to main elsewhere.
            b = branch if (len(graphs) == 1 and branch in _branches(g)) else "main"
            n, e, p = _build_one(b, g)
            all_nodes += n
            all_edges += e
            all_projects += p
        except Exception as exc:  # noqa: BLE001 — one bad graph must not blank the page
            errors[g] = str(exc)
    return {"nodes": all_nodes, "edges": all_edges, "projects": all_projects,
            "branch": branch, "graphs": graphs, "errors": errors}


@app.get("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")


@app.get("/api/graph")
def api_graph():
    graphs = _resolve_graphs(request.args.get("graph"))
    branch = request.args.get("branch", "main")
    try:
        return jsonify(_build_graph(branch, graphs))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "nodes": [], "edges": [], "projects": [],
                        "branch": branch, "graphs": graphs, "errors": {}}), 200


@app.get("/api/branches")
def api_branches():
    graphs = _resolve_graphs(request.args.get("graph"))
    # Branch switching only makes sense against a single graph.
    return jsonify({"branches": _branches(graphs[0]) if len(graphs) == 1 else ["main"]})


@app.get("/api/graphs")
def api_graphs():
    return jsonify({"graphs": _graphs(), "current": GRAPH_ID})


def _commits(graph, branch="main"):
    """Commit log for a graph's branch. Each commit carries created_at (µs since
    epoch) and actor_id. actor_id is NOT the device: the server resolves it from the
    bearer token, every client shares one token, so it reads `default` for all of them
    (see _ping_device). Newest first; timestamp-less rows dropped."""
    try:
        r = requests.get(f"{OMNIGRAPH_URL}/graphs/{graph}/commits",
                         params={"branch": branch}, headers=_headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        rows = data.get("commits") or data.get("rows") or (data if isinstance(data, list) else [])
    except Exception:  # noqa: BLE001 — one unreachable graph must not blank the panel
        return []
    out = []
    for c in rows:
        ts = c.get("created_at") or c.get("createdAt")
        if not ts:
            continue
        out.append({
            "commit": c.get("graph_commit_id") or c.get("graphCommitId"),
            "ts_us": ts,
            "source": c.get("actor_id") or c.get("actorId"),
            "merge": bool(c.get("merged_parent_commit_id") or c.get("mergedParentCommitId")),
        })
    out.sort(key=lambda c: c["ts_us"], reverse=True)
    return out


# ─── sync attribution by SOURCE IP ────────────────────────────────────────────
# Which DEVICE pushed cannot be read back off a commit: a commit record carries only
# graph_commit_id / created_at / actor_id / manifest_* / parent ids — no client address —
# and the server logs no request IPs either. actor_id is resolved from the BEARER TOKEN,
# so it takes a token per device to vary it; that was rejected (secrets to distribute to
# every machine). The one place a device identifies itself for free is the TCP connection,
# so the sync pings this endpoint after each graph and we attribute by the observed IP.
SYNC_PINGS = os.environ.get("SYNC_PINGS", "/data/sync-pings.json")
# OPTIONAL override, "ip=name,ip=name". Normally leave it empty: names are detected
# automatically (see _ping_device). Only needed for a host the LAN DNS cannot name.
DEVICE_MAP = {}
for _pair in os.environ.get("OMNIGRAPH_DEVICE_MAP", "").split(","):
    if "=" in _pair:
        _ip, _name = _pair.split("=", 1)
        DEVICE_MAP[_ip.strip()] = _name.strip()
# A ping lands just AFTER the commit it reports. Attribute a commit to the first ping
# that follows it within this window; anything older stays unattributed rather than
# guessing (a wrong device name is worse than none).
PING_WINDOW_US = int(float(os.environ.get("PING_WINDOW_SEC", "900")) * 1_000_000)
MAX_PINGS = 500
_NAME_CACHE = {}


def _gateway_ip():
    """This container's default gateway = the docker HOST as it appears to us.

    A sync running on the viewer's own host reaches us through the bridge gateway,
    never that host's LAN address, so its PTR lookup would otherwise miss. Read it
    from the routing table instead of hardcoding a bridge subnet — the bridge number
    changes with the compose project.
    """
    try:
        with open("/proc/net/route") as f:
            for line in f.read().splitlines()[1:]:
                cols = line.split()
                if len(cols) > 2 and cols[1] == "00000000":      # destination 0.0.0.0
                    return socket.inet_ntoa(struct.pack("<L", int(cols[2], 16)))
    except Exception:  # noqa: BLE001 — no /proc (non-Linux) just means "no local case"
        pass
    return None


def _ping_device(ip):
    """Device name for a source IP — detected, not configured.

    Reverse DNS answers this on any LAN whose router registers its DHCP clients
    (verified here: <coding-ip> -> coding.example.internal, <ollama-ip> -> ollama.example.internal). The one address that cannot
    resolve is the docker bridge gateway, which is what a sync on the viewer's OWN host
    arrives as — that is looked up as the host rather than left as a bare 172.x address.
    DEVICE_MAP stays available to override a host the DNS cannot name.

    Cached: gethostbyaddr ignores socket timeouts, so an unreachable resolver would
    otherwise stall a gunicorn worker on every ping instead of just the first.
    """
    if ip in DEVICE_MAP:
        return DEVICE_MAP[ip]
    if ip in _NAME_CACHE:
        return _NAME_CACHE[ip]
    lookup = ip
    if ip in ("127.0.0.1", "::1") or ip == _gateway_ip():
        lookup = _gateway_ip() or ip
    try:
        name = socket.gethostbyaddr(lookup)[0].split(".")[0]
    except Exception:  # noqa: BLE001 — no PTR record is normal, not an error
        name = ip
    _NAME_CACHE[ip] = name
    return name


def _pings_read():
    try:
        with open(SYNC_PINGS) as f:
            return json.load(f) or []
    except Exception:  # noqa: BLE001 — absent/corrupt file just means "no pings yet"
        return []


def _pings_append(rec):
    """Append under an exclusive lock — gunicorn runs 2 workers, so a plain
    read-modify-write would lose records raced between them."""
    os.makedirs(os.path.dirname(SYNC_PINGS) or ".", exist_ok=True)
    with open(SYNC_PINGS + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            rows = _pings_read()
            rows.append(rec)
            rows = rows[-MAX_PINGS:]
            tmp = SYNC_PINGS + ".tmp"
            with open(tmp, "w") as f:
                json.dump(rows, f)
            os.replace(tmp, SYNC_PINGS)   # atomic: readers never see a half file
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


@app.post("/api/sync-ping")
def api_sync_ping():
    """Called by omnigraph-sync after it finishes a graph. Records only what the
    SERVER observes (source IP + arrival time) plus a graph id validated against the
    cluster — nothing the caller sends is stored verbatim, so this stays safe to leave
    open on the LAN alongside the read-only viewer."""
    graph = (request.args.get("graph") or "").strip()
    if graph not in _graphs():
        return jsonify({"error": "unknown graph"}), 400
    ip = request.remote_addr or "?"
    rec = {"graph": graph, "ip": ip, "device": _ping_device(ip),
           "ts_us": int(time.time() * 1_000_000)}
    try:
        _pings_append(rec)
    except Exception as e:  # noqa: BLE001 — attribution is a nicety; never fail a sync
        return jsonify({"stored": False, "error": str(e), **rec}), 200
    return jsonify({"stored": True, **rec})


def _attribute(commits, pings):
    """Stamp each commit with the device whose ping first followed it, in-window."""
    ps = sorted(pings, key=lambda p: p["ts_us"])
    for c in commits:
        for p in ps:
            if 0 <= p["ts_us"] - c["ts_us"] <= PING_WINDOW_US:
                c["device"] = p["device"]
                c["device_ip"] = p["ip"]
                break
    return commits


@app.get("/api/sync-history")
def api_sync_history():
    """Per-graph last-synced time + recent history. 'Last synced' is the newest commit
    on the graph's main branch (clients only ever write central via the sync). 'source'
    is the DEVICE that pushed, resolved from the source IP of its sync ping; `actor`
    (the server's token actor) is reported alongside for diagnosis."""
    try:
        limit = max(1, min(50, int(request.args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    pings = _pings_read()
    out = []
    for g in _graphs():
        cs = _attribute(_commits(g, "main"), [p for p in pings if p.get("graph") == g])
        out.append({
            "graph": g,
            "last_synced_us": cs[0]["ts_us"] if cs else None,
            "last_source": (cs[0].get("device") or cs[0].get("source")) if cs else None,
            "commits": len(cs),
            "history": cs[:limit],
        })
    out.sort(key=lambda x: (x["last_synced_us"] or 0), reverse=True)
    return jsonify({"graphs": out, "pings": len(pings)})


def _branch_op(method, graph, path, payload=None):
    """Proxy a branch write to the omnigraph server. Slashed names (device/<host>)
    need body-based merge (source/target) and a %2F-encoded delete path — the plain
    path-based merge/delete 404 on the slash."""
    r = requests.request(method, f"{OMNIGRAPH_URL}/graphs/{graph}/branches{path}",
                         json=payload, headers=_headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() if r.text.strip() else {"ok": True}


@app.post("/api/branch/create")
def api_branch_create():
    d = request.get_json(force=True, silent=True) or {}
    graph, name = d.get("graph"), (d.get("name") or "").strip()
    if not graph or not name:
        return jsonify({"error": "graph and name are required"}), 400
    try:
        return jsonify(_branch_op("POST", graph, "", {"name": name, "from": d.get("from") or "main"}))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.post("/api/branch/merge")
def api_branch_merge():
    d = request.get_json(force=True, silent=True) or {}
    graph, name = d.get("graph"), (d.get("name") or "").strip()
    if not graph or not name or name == "main":
        return jsonify({"error": "graph and a non-main branch name are required"}), 400
    try:
        return jsonify(_branch_op("POST", graph, "/merge",
                                  {"source": name, "target": d.get("into") or "main"}))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.post("/api/branch/delete")
def api_branch_delete():
    d = request.get_json(force=True, silent=True) or {}
    graph, name = d.get("graph"), (d.get("name") or "").strip()
    if not graph or not name or name == "main":
        return jsonify({"error": "graph and a non-main branch name are required"}), 400
    try:
        return jsonify(_branch_op("DELETE", graph, "/" + quote(name, safe=""), None))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.get("/")
def index():
    return Response(PAGE.replace("__GRAPH__", html.escape(GRAPH_ID)), mimetype="text/html")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omnigraph Memory</title>
<style>
:root{color-scheme:light dark;
 --bg:#0f1115;--panel:#161a22;--card:#1b202b;--fg:#e6e9ef;--mut:#8b93a7;--bd:#2a3040;--acc:#6ea8fe;
 --Project:#f6c453;--Decision:#6ea8fe;--Rule:#e5675f;--Preference:#b98cf0;--Convention:#57c98a;--Component:#4bc4d6;--Task:#c7a3ff;}
@media (prefers-color-scheme:light){:root{--bg:#f5f6f8;--panel:#fff;--card:#fff;--fg:#1a1e27;--mut:#5b6472;--bd:#e3e6ec;--acc:#2563eb;}}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--fg);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;display:flex;flex-direction:column}
header{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--bd);background:var(--panel)}
header h1{font-size:15px;margin:0;font-weight:650}
header .sp{flex:1}
input,select,button{background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:8px;padding:6px 10px;font:inherit}
button{cursor:pointer}button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
button:disabled,select:disabled{opacity:.45;cursor:not-allowed}
#search{min-width:220px}
/* ---- graph chips (replaces the old dropdown + project tabs: graph == project now) ---- */
.tabs{display:flex;gap:6px;padding:8px 16px;border-bottom:1px solid var(--bd);flex-wrap:wrap;background:var(--panel);align-items:center}
.tab{padding:5px 12px;border-radius:999px;border:1px solid var(--bd);cursor:pointer;font-size:13px;background:var(--card);
     display:inline-flex;align-items:center;gap:6px;user-select:none;white-space:nowrap}
.tab:hover{border-color:var(--acc)}
.tab.on{border-color:var(--acc);box-shadow:inset 0 0 0 1px var(--acc)}
.tab .n{color:var(--mut);font-size:11px}
.tab.on .n{color:var(--fg)}
.tabs .hint{color:var(--mut);font-size:11px;margin-left:4px}
main{flex:1;display:flex;min-height:0}
#stage{flex:1;position:relative;overflow:hidden}
svg{width:100%;height:100%;display:block}
.link{stroke:var(--mut);stroke-opacity:.45}
.link.hl{stroke:var(--acc);stroke-opacity:1;stroke-width:2px}
.link.dim{stroke-opacity:.06}
.node{cursor:pointer}
.node circle{paint-order:stroke}   /* stroke = graph-cluster ring (set per-node); fill drawn on top */
.node text{font-size:11px;fill:var(--fg);pointer-events:none}
.node.dim{opacity:.12}
.node.hl circle{stroke:var(--acc);stroke-width:3px}
/* ---- focus mode: clicked node + its neighbours stay; everything else fades and drifts out ---- */
.node.focus circle{stroke:var(--acc);stroke-width:4px}
.node.near text{font-weight:650}
.node.far{opacity:.18}
.link.far{stroke-opacity:.04}
.link.near{stroke-opacity:.9;stroke-width:1.6px}
.elabel{font-size:9px;fill:var(--mut);pointer-events:none}
.elabel.far{opacity:.1}
.clabel{font-size:15px;font-weight:700;text-anchor:middle;opacity:.85;pointer-events:none;text-transform:uppercase;letter-spacing:.5px}
#side{width:320px;border-left:1px solid var(--bd);background:var(--panel);overflow:auto;padding:14px}
#side h3{margin:.1em 0 .4em;font-size:14px}
#side .k{color:var(--mut);font-size:12px;margin-top:8px}
#side .v{white-space:pre-wrap;word-break:break-word}
#side .pill{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;color:#fff}
#side .muted{color:var(--mut)}
#hint{position:absolute;right:10px;top:10px;background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:4px 9px;font-size:11px;color:var(--mut);pointer-events:none}
#focusbar{position:absolute;left:50%;transform:translateX(-50%);top:10px;background:var(--panel);border:1px solid var(--acc);
  border-radius:999px;padding:4px 6px 4px 12px;font-size:12px;display:none;align-items:center;gap:8px;z-index:3}
#focusbar b{font-weight:650}
#focusbar button{padding:1px 8px;border-radius:999px;font-size:11px}
#svg{cursor:grab}#svg.panning{cursor:grabbing}
.hit{stroke:transparent;stroke-width:12px;fill:none;cursor:pointer}
#legend{position:absolute;left:10px;bottom:10px;background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:8px 10px;font-size:12px;max-width:240px}
#legend label{display:inline-flex;align-items:center;gap:5px;margin:2px 6px 2px 0;cursor:pointer}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex:none}
#table{flex:1;overflow:auto;padding:0 16px 16px}
#table.hidden,#stage.hidden{display:none}
section{margin-top:16px}
section h2{font-size:14px;display:flex;gap:8px;align-items:center}
.count{background:var(--acc);color:#fff;border-radius:999px;font-size:11px;padding:1px 8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--bd);vertical-align:top}
th{color:var(--mut);cursor:pointer;user-select:none;white-space:nowrap;position:sticky;top:0;background:var(--panel)}
th.sort:after{content:" ↕";opacity:.5}th.asc:after{content:" ↑"}th.desc:after{content:" ↓"}
td{max-width:520px}
tr.match{outline:2px solid var(--acc);outline-offset:-2px}
.err{color:#e5675f;padding:16px}
</style></head><body>
<header>
  <h1>Omnigraph Memory</h1>
  <button id="v-graph" class="on">Graph</button>
  <button id="v-table">Table</button>
  <button id="v-sync" title="last sync time + history per graph, with source">Sync log</button>
  <input id="search" placeholder="search nodes / edges…">
  <span class="sp"></span>
  <label class="muted" style="color:var(--mut)">branch</label>
  <select id="branch" title="branch switching applies to a single graph"></select>
  <button id="b-new" title="create a new branch forked from the selected one">+branch</button>
  <button id="b-merge" title="merge the selected branch into main (native, edge-deduped)">merge→main</button>
  <button id="b-del" title="delete the selected branch">del</button>
</header>
<div class="tabs" id="tabs"></div>
<main>
  <div id="stage">
    <svg id="svg"><g id="viewport"><g id="hulls"></g><g id="clabels"></g><g id="links"></g><g id="elabels"></g><g id="nodes"></g></g></svg>
    <div id="focusbar"><span>focus: <b id="focusname"></b> <span class="muted" id="focusn"></span></span><button id="focusclear">clear ✕</button></div>
    <div id="hint">click node = focus · scroll = zoom · drag = pan/move</div>
    <div id="legend"></div>
  </div>
  <div id="table" class="hidden"></div>
  <div id="side"><p class="muted">Click a node to focus it — its neighbours stay close, everything else drifts out.</p></div>
</main>
<script>
const S={data:null,graphs:["__GRAPH__"],branch:"main",view:"graph",q:"",types:new Set(),sim:null,focus:null,near:null};
const TYPES=["Project","Decision","Rule","Preference","Convention","Component","Task"];
const COLOR=t=>getComputedStyle(document.documentElement).getPropertyValue("--"+t).trim()||"#888";
const CPAL=["#f6c453","#6ea8fe","#e5675f","#57c98a","#b98cf0","#4bc4d6","#f78fb3","#7bd389","#ffa94d","#a0a7b4"];
const $=s=>document.querySelector(s);
const esc=s=>(s==null?"":String(s)).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

/* ---- clustering ----------------------------------------------------------------
   Several graphs shown -> one cluster per graph (graph == project).
   A single graph -> detect relational COMMUNITIES so the graph does not collapse
   into a hub-and-spoke star: ignore the Project's hub edges, take the connected
   groups of the remaining (relational) edges, and give each its own anchored,
   hulled cluster. The Project node + the unclustered spokes stay in the centre. */
function computeCommunities(vis,links){
  const type={},adj={},byId={};vis.forEach(n=>{type[n.id]=n.type;adj[n.id]=[];byId[n.id]=n;});
  for(const e of links){                       // relational adjacency only (skip hub edges)
    if(type[e.from]==="Project"||type[e.to]==="Project")continue;
    if(adj[e.from]&&adj[e.to]){adj[e.from].push(e.to);adj[e.to].push(e.from);}}
  const comm={},name={},seen=new Set();let k=0;
  for(const n of vis){
    if(type[n.id]==="Project"){comm[n.id]="__hub";continue;}
    if(seen.has(n.id))continue;
    const st=[n.id],mem=[];seen.add(n.id);      // connected component over relational edges
    while(st.length){const u=st.pop();mem.push(u);for(const v of adj[u])if(!seen.has(v)){seen.add(v);st.push(v);}}
    if(mem.length>=3){const cid="grp"+(k++);
      const rep=mem.map(m=>byId[m]).sort((a,b)=>(b.type==="Component")-(a.type==="Component")||(b.label||"").length-(a.label||"").length)[0];
      name[cid]=((rep&&(rep.label||rep.slug))||"").slice(0,18);
      mem.forEach(m=>comm[m]=cid);
    } else mem.forEach(m=>comm[m]="•");          // too small to be a cluster
  }
  vis.forEach(n=>{if(!(n.id in comm))comm[n.id]="•";});
  S.commName=name;return comm;
}
function clusterOf(n){
  if(S.graphs.length>1)return n.graph;
  if(n.type==="Project")return "__hub";
  const c=S.comm&&S.comm[n.id];                 // relational community if this node is in one…
  return (c&&c!=="•")?c:("t:"+n.type);          // …otherwise group the spoke by its node type
}
function clusterName(cid){
  if(S.graphs.length>1)return cid;
  if(cid==="__hub")return "";
  if(cid.slice(0,2)==="t:")return cid.slice(2)+"s";       // "Decisions", "Rules", "Components", …
  return (S.commName&&S.commName[cid])||"";
}
function graphColor(g){const all=S.allGraphs||[g];const i=all.indexOf(g);return CPAL[(i<0?0:i)%CPAL.length];}
function clusterColor(cid){
  if(S.graphs.length>1)return graphColor(cid);
  if(cid==="__hub")return "#9aa0a6";                       // neutral grey for the hub
  if(cid.slice(0,2)==="t:")return COLOR(cid.slice(2))||"#9aa0a6";   // type cluster = the type's colour
  const ring=(S.cl&&S.cl.ring)||[];const i=ring.indexOf(cid);
  return CPAL[(i<0?0:i)%CPAL.length];                      // relational community = palette colour
}
function buildClusters(vis,W,H){
  const all=[...new Set(vis.map(clusterOf))];
  const ring=all.filter(c=>c!=="__hub"&&c!=="•").sort();  // real communities sit on a ring
  S.cl={ring};                                            // set early so clusterColor can index ring
  const info={},n=ring.length,R=Math.min(W,H)*0.46;
  all.forEach(cid=>{
    let anchor={x:W/2,y:H/2};                              // hub + unclustered gather at centre
    if(cid!=="__hub"&&cid!=="•"){const i=ring.indexOf(cid),a=-Math.PI/2+i*2*Math.PI/Math.max(n,1);
      anchor={x:W/2+R*Math.cos(a),y:H/2+R*Math.sin(a)};}
    info[cid]={color:clusterColor(cid),name:clusterName(cid),anchor};
  });
  return {ids:all,ring,info,multi:n>=1};                   // anchored layout whenever a community exists
}

const qg=()=>"graph="+encodeURIComponent(S.graphs.join(","));
async function load(){
  const r=await fetch("/api/graph?"+qg()+"&branch="+encodeURIComponent(S.branch));
  S.data=await r.json();
  const errs=S.data.errors||{};
  if(S.data.error)$("#side").innerHTML='<p class="err">'+esc(S.data.error)+'</p>';
  else if(Object.keys(errs).length)$("#side").innerHTML='<p class="err">'+Object.entries(errs).map(([g,m])=>esc(g)+": "+esc(m)).join("<br>")+'</p>';
  S.types=new Set(TYPES);
  setFocus(null);        // clear focus AND its bar — a reload changes which nodes exist
  buildTabs();buildLegend();render();
}
async function loadGraphs(){
  const r=await fetch("/api/graphs");const j=await r.json();const g=j.graphs||S.graphs;
  S.allGraphs=g;
  S.graphs=S.graphs.filter(x=>g.includes(x));
  if(!S.graphs.length)S.graphs=[g.includes(j.current)?j.current:g[0]];
}
async function loadBranches(){
  const r=await fetch("/api/branches?"+qg());const b=(await r.json()).branches||["main"];
  const sel=$("#branch");
  sel.innerHTML=b.map(x=>`<option ${x===S.branch?"selected":""}>${esc(x)}</option>`).join("");
  sel.disabled=S.graphs.length>1;                       // branches are per-graph
  sel.title=S.graphs.length>1?"select a single graph to switch branch":"branch";
  const one=S.graphs.length===1;
  ["b-new","b-merge","b-del"].forEach(id=>{const el=$("#"+id);if(el)el.disabled=!one;});
}
/* ---- branch write ops (create/merge/delete) — need exactly one graph ---- */
function curGraph(){
  if(S.graphs.length!==1){alert("select a single graph (chip) to manage its branches");return null;}
  return S.graphs[0];
}
async function branchOp(action,body){
  const r=await fetch("/api/branch/"+action,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok||j.error){alert("branch "+action+" failed: "+(j.error||r.status));return false;}
  return true;
}
async function doBranchNew(){
  const g=curGraph();if(!g)return;
  const name=(prompt("new branch name (forked from '"+S.branch+"')","device/host")||"").trim();
  if(!name)return;
  if(await branchOp("create",{graph:g,name,from:S.branch})){S.branch=name;await loadBranches();load();}
}
async function doBranchMerge(){
  const g=curGraph();if(!g)return; const b=S.branch;
  if(b==="main"){alert("'main' is the merge target, not a source");return;}
  if(!confirm("merge '"+b+"' into main on "+g+"?"))return;
  if(await branchOp("merge",{graph:g,name:b,into:"main"})){alert("merged '"+b+"' → main");load();}
}
async function doBranchDel(){
  const g=curGraph();if(!g)return; const b=S.branch;
  if(b==="main"){alert("cannot delete 'main'");return;}
  if(!confirm("delete branch '"+b+"' on "+g+"? unmerged changes are lost"))return;
  if(await branchOp("delete",{graph:g,name:b})){S.branch="main";await loadBranches();load();}
}
/* ---- chips: click = switch fast · ctrl/cmd/shift-click = add/remove (compare graphs) ---- */
function buildTabs(){
  const counts={};(S.data&&S.data.nodes||[]).forEach(n=>counts[n.graph]=(counts[n.graph]||0)+1);
  const chips=(S.allGraphs||[]).map(g=>{
    const on=S.graphs.includes(g);
    return `<div class="tab ${on?"on":""}" data-g="${esc(g)}" title="click to switch · ctrl/⌘/shift-click to add">
      <span class=dot style="background:${graphColor(g)}"></span>${esc(g)}${on&&counts[g]!=null?`<span class=n>${counts[g]}</span>`:""}</div>`;
  }).join("");
  const allOn=S.graphs.length===(S.allGraphs||[]).length;
  $("#tabs").innerHTML=chips+`<div class="tab ${allOn?"on":""}" data-all="1" title="show every graph at once">all graphs</div>`
    +`<span class="hint">click = switch · ctrl/⌘/shift-click = compare</span>`;
  $("#tabs").querySelectorAll(".tab").forEach(el=>el.onclick=ev=>{
    if(el.dataset.all){S.graphs=allOn?[S.allGraphs[0]]:[...S.allGraphs];}
    else{
      const g=el.dataset.g,multi=ev.ctrlKey||ev.metaKey||ev.shiftKey;
      if(multi){const i=S.graphs.indexOf(g);
        if(i>=0){if(S.graphs.length>1)S.graphs.splice(i,1);}   // never empty
        else S.graphs.push(g);}
      else{if(S.graphs.length===1&&S.graphs[0]===g)return;S.graphs=[g];}
    }
    S.branch="main";resetLayout();loadBranches().then(load);
  });
}
function buildLegend(){
  $("#legend").innerHTML="<b>types</b><br>"+TYPES.map(t=>
    `<label><input type=checkbox data-t="${t}" ${S.types.has(t)?"checked":""}><span class=dot style="background:${COLOR(t)}"></span>${t}</label>`).join("");
  $("#legend").querySelectorAll("input").forEach(cb=>cb.onchange=()=>{cb.checked?S.types.add(cb.dataset.t):S.types.delete(cb.dataset.t);render();});
}
function visibleNodes(){ return (S.data.nodes||[]).filter(n=>S.types.has(n.type)); }
function matches(n){
  if(!S.q)return false;const q=S.q.toLowerCase();
  return (n.slug+" "+n.type+" "+Object.values(n.fields||{}).join(" ")).toLowerCase().includes(q);
}
function render(){
  $("#v-graph").classList.toggle("on",S.view==="graph");
  $("#v-table").classList.toggle("on",S.view==="table");
  $("#v-sync").classList.toggle("on",S.view==="sync");
  $("#stage").classList.toggle("hidden",S.view!=="graph");
  $("#table").classList.toggle("hidden",S.view==="graph");   // #table area holds Table OR Sync log
  $("#legend").style.display=S.view==="graph"?"":"none";
  if(S.view==="graph")renderGraph();
  else if(S.view==="sync")renderSyncLog();
  else renderTable();
}
/* ---------- sync log view: last-synced time + history + source per graph ---------- */
function tsStr(us){if(!us)return"—";const d=new Date(us/1000);const p=n=>String(n).padStart(2,"0");
  return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())+" "+p(d.getHours())+":"+p(d.getMinutes());}
function agoStr(us){if(!us)return"never";let s=(Date.now()-us/1000)/1000;
  if(s<90)return"just now";let m=s/60;if(m<60)return Math.round(m)+"m ago";let h=m/60;if(h<48)return Math.round(h)+"h ago";return Math.round(h/24)+"d ago";}
async function renderSyncLog(){
  $("#table").innerHTML='<p class="muted" style="padding-top:12px">loading sync history…</p>';
  let j;try{j=await(await fetch("/api/sync-history")).json();}catch(e){$("#table").innerHTML='<p class="err">'+esc(e)+'</p>';return;}
  const out=(j.graphs||[]).map(g=>{
    const hist=(g.history||[]).map(h=>{
      // device (from the ping's source IP) is the real answer; actor is the shared
      // token name and only worth showing when nothing better is known.
      const src=h.device
        ? `<span title="source IP ${esc(h.device_ip||"?")}">${esc(h.device)}</span>`
        : (h.source?`<span class=muted title="server token actor, shared by every device">${esc(h.source)}</span>`
                   :'<span class=muted>—</span>');
      return `<tr><td>${esc(tsStr(h.ts_us))}</td><td>${src}</td><td class=muted>${esc((h.commit||"").slice(0,12))}${h.merge?' <span title="merge commit">⤵</span>':''}</td></tr>`;
    }).join("")||'<tr><td colspan=3 class=muted>no commits</td></tr>';
    return `<section><h2><span class=dot style="background:${graphColor(g.graph)}"></span>${esc(g.graph)}
      <span class="count">${esc(agoStr(g.last_synced_us))}</span>
      ${g.last_source?`<span class="muted">via ${esc(g.last_source)}</span>`:''}</h2>
      <div style="overflow-x:auto"><table><thead><tr><th>synced (local time)</th><th>source</th><th>commit</th></tr></thead><tbody>${hist}</tbody></table></div></section>`;
  }).join("");
  $("#table").innerHTML=`<div style="padding-top:12px"><p class="muted">“Last synced” = newest commit on each graph’s <b>main</b> (clients write central only via the sync). <b>Source</b> = the <b>device</b>, resolved from the source IP of the sync’s ping — a commit itself records no client address. Greyed names are the server’s token actor (<code>default</code> for every device), shown only where no ping matched.</p></div>`+(out||'<p class="muted">no graphs.</p>');
}
/* ---------- force-directed graph: build DOM once, update positions per tick ---------- */
const NS="http://www.w3.org/2000/svg";
function svgPt(e){const r=$("#svg").getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};}
function screenToGraph(sx,sy){return {x:(sx-S.vp.tx)/S.vp.k,y:(sy-S.vp.ty)/S.vp.k};}
function vpApply(){$("#viewport").setAttribute("transform",`translate(${S.vp.tx},${S.vp.ty}) scale(${S.vp.k})`);}
function edgeKey(e){return e.type+"|"+e.from+"|"+e.to;}
function ptSeg(px,py,ax,ay,bx,by){const dx=bx-ax,dy=by-ay,L=dx*dx+dy*dy||1;
  let t=((px-ax)*dx+(py-ay)*dy)/L;t=Math.max(0,Math.min(1,t));return Math.hypot(px-(ax+t*dx),py-(ay+t*dy));}
function nearestEdge(g){let best=null,bd=1e9;for(const e of (S.curLinks||[])){const a=S.pos[e.from],b=S.pos[e.to];
  if(!a||!b)continue;const d=ptSeg(g.x,g.y,a.x,a.y,b.x,b.y);if(d<bd){bd=d;best=e;}}return bd<25?best:null;}

/* ---- focus: the clicked node + its 1-hop neighbours are "near"; the rest are pushed out ---- */
function neighboursOf(id){
  const near=new Set([id]);
  for(const e of (S.curLinks||[])){if(e.from===id)near.add(e.to);else if(e.to===id)near.add(e.from);}
  return near;
}
function setFocus(id){
  S.focus=id;S.near=id?neighboursOf(id):null;
  const bar=$("#focusbar");
  if(id){const n=(S.data.nodes||[]).find(x=>x.id===id);
    $("#focusname").textContent=(n&&(n.label||n.slug))||id;
    $("#focusn").textContent="· "+(S.near.size-1)+" connected";
    bar.style.display="flex";}
  else bar.style.display="none";
  applyFocus();restartSim();      // re-run the layout so the split actually animates
}
function applyFocus(){
  if(!S.els)return;const f=S.focus,near=S.near;
  for(const {n,g} of S.els.nodeEls){
    const isNear=!f||near.has(n.id);
    g.classList.toggle("focus",!!(f&&n.id===f));
    g.classList.toggle("near",!!(f&&isNear&&n.id!==f));
    g.classList.toggle("far",!!(f&&!isNear));
    if(f&&isNear)g.parentNode.appendChild(g);         // raise the focused cluster
  }
  for(const {e,ln,tx} of S.els.linkEls){
    const touches=f&&(e.from===f||e.to===f);
    const inNear=f&&near.has(e.from)&&near.has(e.to);
    ln.classList.toggle("near",!!touches);
    ln.classList.toggle("far",!!(f&&!inNear));
    tx.classList.toggle("far",!!(f&&!inNear));
  }
}
// re-run the layout from tick 0. No-op before the first renderGraph() has built tickFn
// (setFocus(null) runs during load(), when there is nothing to simulate yet).
// re-run the layout from tick 0. No-op before the first renderGraph() has built tickFn
// (setFocus(null) runs during load(), when there is nothing to simulate yet).
function restartSim(){if(!S.tickFn)return;if(S.sim)cancelAnimationFrame(S.sim);S.ticks=0;S.alpha=1;S.sim=requestAnimationFrame(S.tickFn);}
// Drop the layout before an async reload. The running sim MUST be cancelled in the same
// breath: it ticks off S.pos, and a queued frame firing after S.pos=null throws.
function resetLayout(){if(S.sim)cancelAnimationFrame(S.sim);S.sim=null;S.pos=null;S.vp=null;}
function renderGraph(){
  const svg=$("#svg"),W=svg.clientWidth||800,H=svg.clientHeight||600;
  const vis=visibleNodes(),ids=new Set(vis.map(n=>n.id));
  const links=(S.data.edges||[]).filter(e=>ids.has(e.from)&&ids.has(e.to));
  S.curVis=vis;S.curLinks=links;
  S.type={};vis.forEach(n=>S.type[n.id]=n.type);          // id -> type (for hub-edge weakening)
  S.comm=S.graphs.length>1?null:computeCommunities(vis,links);
  S.userMoved=0;                                          // fresh layout -> auto-frame is back on
  if(S.focus&&!ids.has(S.focus)){S.focus=null;S.near=null;$("#focusbar").style.display="none";}
  S.cl=buildClusters(vis,W,H);
  const prev=S.pos||{};S.pos={};
  // Seed NEW nodes already spread out (roughly the final layout size) so the sim
  // only has to refine, not explode outward — the main cause of the long bounce.
  const seed=S.cl.multi?170:Math.max(260,Math.min(W,H)*0.5);
  vis.forEach(n=>{const p=prev[n.id],a=S.cl.info[clusterOf(n)].anchor;
    S.pos[n.id]={x:p?p.x:a.x+(Math.random()-.5)*seed,y:p?p.y:a.y+(Math.random()-.5)*seed,vx:0,vy:0};});
  if(!S.vp)S.vp={tx:0,ty:0,k:1};
  if(S.sim)cancelAnimationFrame(S.sim);

  const linkG=$("#links"),elG=$("#elabels"),nodeG=$("#nodes");
  linkG.innerHTML="";elG.innerHTML="";nodeG.innerHTML="";
  const linkEls=links.map(e=>{
    const ln=document.createElementNS(NS,"line");ln.setAttribute("class","link");
    const hit=document.createElementNS(NS,"line");hit.setAttribute("class","hit");
    hit.addEventListener("click",ev=>{ev.stopPropagation();
      const sp=svgPt(ev);onEdgeClick(nearestEdge(screenToGraph(sp.x,sp.y))||e);});
    const tx=document.createElementNS(NS,"text");tx.setAttribute("class","elabel");tx.textContent=e.type;
    linkG.append(ln,hit);elG.append(tx);return {e,ln,hit,tx};
  });
  const nodeEls=vis.map(n=>{
    const g=document.createElementNS(NS,"g");g.setAttribute("class","node");
    const r=n.type==="Project"?13:9;
    const c=document.createElementNS(NS,"circle");c.setAttribute("r",r);c.setAttribute("fill",COLOR(n.type));
    c.setAttribute("stroke",S.cl.info[clusterOf(n)].color);c.setAttribute("stroke-width",n.type==="Project"?3.5:2.5);
    const t=document.createElementNS(NS,"text");t.setAttribute("x",r+3);t.setAttribute("y",4);t.textContent=(n.label||n.slug).slice(0,22);
    g.append(c,t);nodeG.append(g);
    g.addEventListener("mousedown",ev=>{ev.stopPropagation();ev.preventDefault();
      const start=svgPt(ev);let moved=false;n._drag=1;
      const mv=e2=>{const sp=svgPt(e2);if(Math.hypot(sp.x-start.x,sp.y-start.y)>4)moved=true;
        const gp=screenToGraph(sp.x,sp.y);S.pos[n.id].x=gp.x;S.pos[n.id].y=gp.y;paint();};
      const up=()=>{n._drag=0;document.removeEventListener("mousemove",mv);document.removeEventListener("mouseup",up);
        if(!moved)onNodeClick(n);};
      document.addEventListener("mousemove",mv);document.addEventListener("mouseup",up);});
    return {n,g};
  });
  S.els={linkEls,nodeEls};

  svg.onwheel=ev=>{ev.preventDefault();S.userMoved=1;const sp=svgPt(ev),g=screenToGraph(sp.x,sp.y);
    const f=ev.deltaY<0?1.1:1/1.1;S.vp.k=Math.max(.2,Math.min(4,S.vp.k*f));
    S.vp.tx=sp.x-g.x*S.vp.k;S.vp.ty=sp.y-g.y*S.vp.k;vpApply();};
  svg.onmousedown=ev=>{if(ev.target.closest(".node")||ev.target.classList.contains("hit"))return;
    ev.preventDefault();svg.classList.add("panning");
    const s={x:ev.clientX,y:ev.clientY,tx:S.vp.tx,ty:S.vp.ty};let moved=false;
    const mv=e2=>{if(Math.hypot(e2.clientX-s.x,e2.clientY-s.y)>4){moved=true;S.userMoved=1;}
      S.vp.tx=s.tx+(e2.clientX-s.x);S.vp.ty=s.ty+(e2.clientY-s.y);vpApply();};
    const up=()=>{svg.classList.remove("panning");document.removeEventListener("mousemove",mv);document.removeEventListener("mouseup",up);
      if(!moved&&S.focus)setFocus(null);};        // click empty space = clear focus
    document.addEventListener("mousemove",mv);document.addEventListener("mouseup",up);};

  vpApply();applyHighlight();applyFocus();

  const FAR=Math.min(W,H)*0.62;                   // radius the unrelated nodes settle on
  // Physics tuned to settle in ≤3s WITHOUT bouncing: forces ease off through a
  // cooling `alpha`, velocity is damped and clamped (no violent jumps), a stronger
  // charge + longer links spread the graph out, and a final collision pass keeps
  // nodes from ever overlapping. Connected nodes sit near each other, not on top.
  const CHARGE=6500, LINK=115, VDECAY=0.70, VMAX=16, COOL=0.033, AMIN=0.012, MAXT=180;
  const rad=n=>(n.type==="Project"?13:9);
  S.tickFn=function tick(){
    const P=S.pos,f=S.focus,near=S.near,al=S.alpha;
    if(!P)return;                    // layout dropped by a reload — stop; renderGraph restarts us
    // repulsion (charge) — eased by alpha so it converges instead of oscillating
    for(let i=0;i<vis.length;i++)for(let j=i+1;j<vis.length;j++){
      const pa=P[vis[i].id],pb=P[vis[j].id];let dx=pa.x-pb.x,dy=pa.y-pb.y,d2=dx*dx+dy*dy||1,d=Math.sqrt(d2),f2=CHARGE/d2*al;
      pa.vx+=dx/d*f2;pa.vy+=dy/d*f2;pb.vx-=dx/d*f2;pb.vy-=dy/d*f2;}
    // links (springs) pull connected nodes to LINK apart; in focus mode only the
    // focused node's own edges pull, the rest go slack.
    for(const e of links){const pa=P[e.from],pb=P[e.to];if(!pa||!pb)continue;
      const active=!f||e.from===f||e.to===f;
      // hub edges (to/from the Project) are long + slack so spokes fan out instead of
      // collapsing into one ball; relational edges are short + tight so communities clump.
      const hub=S.type[e.from]==="Project"||S.type[e.to]==="Project";
      const rest=f?85:(hub?205:LINK),str=active?(hub?0.006:0.04):0.002;
      let dx=pb.x-pa.x,dy=pb.y-pa.y,d=Math.hypot(dx,dy)||1,fr=(d-rest)*str*al;
      pa.vx+=dx/d*fr;pa.vy+=dy/d*fr;pb.vx-=dx/d*fr;pb.vy-=dy/d*fr;}
    // gravity toward cluster/centre (or the focus ring), then integrate: damp + clamp
    for(const n of vis){const p=P[n.id];if(n._drag){p.vx=p.vy=0;continue;}
      if(f){
        if(near.has(n.id)){const a={x:W/2,y:H/2};             // related: gather at centre
          p.vx+=(a.x-p.x)*(n.id===f?0.06:0.03)*al;p.vy+=(a.y-p.y)*(n.id===f?0.06:0.03)*al;}
        else{let dx=p.x-W/2,dy=p.y-H/2,d=Math.hypot(dx,dy)||1; // unrelated: pushed to a ring
          const pull=(FAR-d)*0.02*al;p.vx+=dx/d*pull;p.vy+=dy/d*pull;}
      }else{
        const cid=clusterOf(n),a=S.cl.info[cid].anchor;
        // Project pinned firmly to centre; real communities pulled to their ring
        // anchor; unclustered spokes only gently centred so they fan out round the hub.
        const gk=n.type==="Project"?0.05:(cid==="•"?0.006:0.05);
        p.vx+=(a.x-p.x)*gk*al;p.vy+=(a.y-p.y)*gk*al;
      }
      p.vx*=VDECAY;p.vy*=VDECAY;
      if(p.vx>VMAX)p.vx=VMAX;else if(p.vx<-VMAX)p.vx=-VMAX;
      if(p.vy>VMAX)p.vy=VMAX;else if(p.vy<-VMAX)p.vy=-VMAX;
      p.x+=p.vx;p.y+=p.vy;}
    // collision — project any overlapping pair apart (position only → no bounce),
    // so nodes are guaranteed never to overlay.
    for(let i=0;i<vis.length;i++)for(let j=i+1;j<vis.length;j++){
      const a=P[vis[i].id],b=P[vis[j].id];let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,min=rad(vis[i])+rad(vis[j])+16;
      if(d<min){const push=(min-d)/2,ux=dx/d,uy=dy/d;
        if(!vis[i]._drag){a.x-=ux*push;a.y-=uy*push;}
        if(!vis[j]._drag){b.x+=ux*push;b.y+=uy*push;}}}
    paint();
    // smooth "camera": ease the viewport toward framing the whole graph every tick,
    // so it settles into view gradually instead of snapping/zooming at the end.
    // Disabled once the user zooms/pans (S.userMoved) and in focus mode.
    if(!f&&!S.userMoved){const t=fitTarget(W,H);
      S.vp.k+=(t.k-S.vp.k)*0.10;S.vp.tx+=(t.tx-S.vp.tx)*0.10;S.vp.ty+=(t.ty-S.vp.ty)*0.10;vpApply();}
    S.alpha*=(1-COOL);
    if(S.alpha>AMIN&&++S.ticks<MAXT)S.sim=requestAnimationFrame(S.tickFn);
    else S.sim=null;
  };
  S.ticks=0;S.alpha=1;S.sim=requestAnimationFrame(S.tickFn);
}
// Target viewport that frames the whole graph with padding (does NOT apply it —
// the sim eases toward this each tick for a smooth camera, no end-of-sim jump).
function fitTarget(W,H){
  const P=S.pos,def={k:S.vp.k,tx:S.vp.tx,ty:S.vp.ty};if(!P)return def;
  let mnx=1e9,mny=1e9,mxx=-1e9,mxy=-1e9;
  for(const n of (S.curVis||[])){const p=P[n.id];if(!p)continue;
    mnx=Math.min(mnx,p.x);mny=Math.min(mny,p.y);mxx=Math.max(mxx,p.x);mxy=Math.max(mxy,p.y);}
  if(mxx<mnx)return def;
  const gw=(mxx-mnx)||1,gh=(mxy-mny)||1,pad=70;
  const k=Math.max(.2,Math.min(1.6,Math.min((W-2*pad)/gw,(H-2*pad)/gh)));
  return {k,tx:W/2-(mnx+mxx)/2*k,ty:H/2-(mny+mxy)/2*k};
}
function paint(){
  if(!S.els)return;const P=S.pos;
  for(const {e,ln,hit,tx} of S.els.linkEls){const a=P[e.from],b=P[e.to];if(!a||!b)continue;
    for(const L of [ln,hit]){L.setAttribute("x1",a.x);L.setAttribute("y1",a.y);L.setAttribute("x2",b.x);L.setAttribute("y2",b.y);}
    tx.setAttribute("x",(a.x+b.x)/2);tx.setAttribute("y",(a.y+b.y)/2);}
  for(const {n,g} of S.els.nodeEls){const p=P[n.id];g.setAttribute("transform",`translate(${p.x},${p.y})`);}
  paintHulls();
}
// translucent tinted circle + name behind each cluster, so groups read at a glance
function paintHulls(){
  const hg=$("#hulls"),lg=$("#clabels");if(!hg||!S.cl)return;
  hg.innerHTML="";lg.innerHTML="";
  if(!S.cl.multi||S.focus)return;            // hulls are meaningless while focused
  const by={};for(const {n} of S.els.nodeEls){const p=S.pos[n.id];if(!p)continue;(by[clusterOf(n)]=by[clusterOf(n)]||[]).push(p);}
  for(const cid of S.cl.ids){const pts=by[cid];if(!pts||!pts.length)continue;
    let cx=0,cy=0;for(const p of pts){cx+=p.x;cy+=p.y;}cx/=pts.length;cy/=pts.length;
    let rad=0;for(const p of pts)rad=Math.max(rad,Math.hypot(p.x-cx,p.y-cy));rad+=34;
    const col=S.cl.info[cid].color;
    const ci=document.createElementNS(NS,"circle");ci.setAttribute("cx",cx);ci.setAttribute("cy",cy);ci.setAttribute("r",rad);
    ci.setAttribute("fill",col);ci.setAttribute("fill-opacity",".06");ci.setAttribute("stroke",col);
    ci.setAttribute("stroke-opacity",".55");ci.setAttribute("stroke-width","1.5");ci.setAttribute("stroke-dasharray","6 5");hg.append(ci);
    const tx=document.createElementNS(NS,"text");tx.setAttribute("x",cx);tx.setAttribute("y",cy-rad-7);
    tx.setAttribute("class","clabel");tx.setAttribute("fill",col);tx.textContent=S.cl.info[cid].name;lg.append(tx);}
}
function onNodeClick(n){
  if(S.q)toggleRemoved(n.id);
  setFocus(S.focus===n.id?null:n.id);      // click the focused node again = unfocus
  showNode(n,S.curLinks);
}
function onEdgeClick(e){if(S.q)toggleRemoved(edgeKey(e));showEdge(e.type,e.from,e.to);}
function toggleRemoved(key){if(!S.removed)S.removed=new Set();S.removed.has(key)?S.removed.delete(key):S.removed.add(key);applyHighlight();}
function applyHighlight(){
  if(!S.els)return;const q=S.q,rm=S.removed||new Set();
  for(const {n,g} of S.els.nodeEls){const isM=q&&matches(n);const hl=isM&&!rm.has(n.id);
    g.classList.toggle("hl",!!hl);g.classList.toggle("dim",!!(q&&!isM));
    if(hl)g.parentNode.appendChild(g);}
  for(const {e,ln,hit,tx} of S.els.linkEls){
    const fn=S.data.nodes.find(x=>x.id===e.from),tn=S.data.nodes.find(x=>x.id===e.to);
    const isM=q&&((fn&&matches(fn))||(tn&&matches(tn)));const hl=isM&&!rm.has(edgeKey(e));
    ln.classList.toggle("hl",!!hl);ln.classList.toggle("dim",!!(q&&!isM));
    if(hl){ln.parentNode.appendChild(ln);hit.parentNode.appendChild(hit);tx.parentNode.appendChild(tx);}}
}
const nodeById=id=>(S.data.nodes||[]).find(n=>n.id===id);
function showNode(n,links){if(!n)return;
  const conn=(links||S.data.edges).filter(e=>e.from===n.id||e.to===n.id);
  const rows=Object.entries(n.fields||{}).filter(([k])=>k!=="embedding")
    .map(([k,v])=>`<div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>`).join("");
  const cs=conn.map(e=>{const other=e.from===n.id?e.to:e.from,dir=e.from===n.id?"→":"←";const o=nodeById(other);
    return `<div class="v">${dir} <b>${esc(e.type)}</b> ${esc(o?o.slug:other)}</div>`;}).join("")||'<div class="muted">none</div>';
  $("#side").innerHTML=`<span class="pill" style="background:${COLOR(n.type)}">${esc(n.type)}</span>
    <h3>${esc(n.label||n.slug)}</h3><div class="muted">${esc(n.slug)} ${n.global?"· global":""}</div>
    <div class="k">graph</div><div class="v"><span class=dot style="background:${graphColor(n.graph)}"></span> ${esc(n.graph)}</div>
    ${rows}<div class="k">connections (${conn.length})</div>${cs}`;
}
function whyMatch(node){
  if(!S.q||!node)return "";const q=S.q.toLowerCase();
  for(const [k,v] of Object.entries(node.fields||{})){
    if(k==="embedding")continue;const s=String(v).toLowerCase();const i=s.indexOf(q);
    if(i>=0)return `${node.slug}.${k}: …${esc(String(v).slice(Math.max(0,i-15),i+q.length+25))}…`;}
  return "";
}
function showEdge(type,from,to){
  const fn=nodeById(from),tn=nodeById(to);
  const why=whyMatch(fn)||whyMatch(tn);
  const note=S.q?`<div class="k">search "${esc(S.q)}"</div><div class="v">${why?("highlighted because "+why):"<span class=muted>not matched — highlighted edges connect a node that matches</span>"}</div>`:"";
  $("#side").innerHTML=`<span class="pill" style="background:var(--acc)">edge</span><h3>${esc(type)}</h3>
    <div class="k">from</div><div class="v">${esc(fn?fn.slug:from)} <span class="muted">(${esc(fn?fn.type:"?")})</span></div>
    <div class="k">to</div><div class="v">${esc(tn?tn.slug:to)} <span class="muted">(${esc(tn?tn.type:"?")})</span></div>
    <div class="k">meaning</div><div class="v">${esc(EDGE_DOC[type]||"relationship")}</div>${note}`;
}
const EDGE_DOC={DecidedIn:"decision belongs to a project (hub)",ConstrainsProject:"rule constrains a project (hub)",
  AppliesTo:"convention applies to a project (hub)",PartOf:"component is part of a project (hub)",
  Tracks:"task belongs to a project (hub)",ConstrainsComponent:"rule governs a specific component",
  Affects:"decision changes a component",Addresses:"task works on a component",
  Implements:"task realizes a decision",DependsOn:"component depends on another component",
  Supersedes:"decision replaces an earlier decision"};
/* ---------- table view ---------- */
let SORT={};
function renderTable(){
  const vis=visibleNodes();const byType={};TYPES.forEach(t=>byType[t]=[]);
  vis.forEach(n=>byType[n.type].push(n));
  let hasFilter=($("#tf")&&$("#tf").value)||"";
  let hh=`<div style="position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:2">
    <input id="tf" placeholder="filter rows in tables…" value="${esc(hasFilter)}" style="min-width:280px"></div>`;
  const multi=S.graphs.length>1;
  const out=TYPES.filter(t=>byType[t].length).map(t=>{
    const cols=(multi?["graph"]:[]).concat(["slug"]).concat(t==="Decision"?["title","status","rationale"]:t==="Rule"?["severity","statement"]:
      t==="Preference"?["scope","statement"]:t==="Convention"?["name","example"]:t==="Component"?["name","kind","location"]:
      t==="Project"?["name","summary"]:t==="Task"?["title","state"]:["label"]);
    let rows=byType[t].map(n=>({graph:n.graph,slug:n.slug,...n.fields,_n:n}));
    if(hasFilter){const q=hasFilter.toLowerCase();rows=rows.filter(r=>cols.some(c=>String(r[c]==null?"":r[c]).toLowerCase().includes(q)));}
    const s=SORT[t];if(s){rows.sort((a,b)=>{const av=String(a[s.c]==null?"":a[s.c]),bv=String(b[s.c]==null?"":b[s.c]);return s.d*av.localeCompare(bv);});}
    const th=cols.map(c=>`<th class="sort ${s&&s.c===c?(s.d>0?"asc":"desc"):""}" data-t="${t}" data-c="${c}">${c}</th>`).join("");
    const body=rows.map(r=>`<tr class="${S.q&&matches(r._n)?"match":""}">`+cols.map(c=>`<td>${esc(r[c])}</td>`).join("")+"</tr>").join("")||`<tr><td colspan=9 class=muted>no rows</td></tr>`;
    return `<section><h2><span class="dot" style="background:${COLOR(t)}"></span>${t} <span class="count">${rows.length}</span></h2>
      <div style="overflow-x:auto"><table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div></section>`;
  }).join("");
  $("#table").innerHTML=hh+(out||'<p class="muted">no nodes in the selected graph(s).</p>');
  const tf=$("#tf");if(tf){tf.oninput=()=>renderTable();tf.focus();tf.setSelectionRange(tf.value.length,tf.value.length);}
  $("#table").querySelectorAll("th.sort").forEach(th=>th.onclick=()=>{
    const t=th.dataset.t,c=th.dataset.c,cur=SORT[t];SORT[t]=cur&&cur.c===c?{c,d:-cur.d}:{c,d:1};renderTable();});
}
/* ---------- wire header ---------- */
$("#v-graph").onclick=()=>{S.view="graph";render();};
$("#v-table").onclick=()=>{S.view="table";render();};
$("#v-sync").onclick=()=>{S.view="sync";render();};
$("#search").oninput=e=>{S.q=e.target.value;S.removed=new Set();if(S.view==="graph")applyHighlight();else renderTable();};
$("#branch").onchange=e=>{S.branch=e.target.value;resetLayout();load();};
$("#b-new").onclick=doBranchNew;$("#b-merge").onclick=doBranchMerge;$("#b-del").onclick=doBranchDel;
$("#focusclear").onclick=()=>setFocus(null);
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&S.focus)setFocus(null);});
window.addEventListener("resize",()=>{if(S.view==="graph")renderGraph();});
loadGraphs().then(loadBranches).then(load);
</script></body></html>"""
