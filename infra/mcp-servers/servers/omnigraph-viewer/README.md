# Omnigraph Memory Viewer

> **Moving:** the canonical home of this viewer is moving to its own `omnigraph-viewer` repository; the live compose file still builds from this folder until then.

A minimal **read-only** web viewer for the Omnigraph memory graphs. Omnigraph
ships no self-hostable UI (the `ui` cluster field is reserved upstream), so this
fills the gap: it shows the typed memory nodes (Decisions, Rules, Preferences,
Conventions, Components, Tasks) and how they connect.

Per-project isolation means one graph per repo, so **graph ≈ project**.

## Features

- **Graph chips** (the tab bar) — one chip per graph, with its node count.
  **Click** a chip to switch to it; **ctrl/⌘/shift-click** to add graphs and show
  several **at the same time**; **all graphs** shows the whole cluster at once.
  Each graph keeps a stable colour and gets its own tinted hull + label, so
  clusters read at a glance. (This replaces the old dropdown + project tabs: with
  one graph per project, per-project tabs inside a graph were redundant.)
- **Focus-on-click exploration** — click a node and its **1-hop neighbours gather
  at the centre while everything unrelated drifts out to a ring** and fades, so a
  dense star becomes readable. A focus bar names the node and its connection
  count; click the node again, click empty space, press `Esc`, or hit *clear* to
  exit.
- **Interactive graph** — force-directed nodes + edges (vanilla JS/SVG, no CDN).
  Drag nodes; scroll to zoom; drag empty space to pan. Click a node for all its
  fields, its graph, and its connections; click an edge for its type, endpoints,
  and meaning. A legend toggles each node type on/off.
- **Table view** — one sortable table per node type (click a header to sort) with
  a live text filter across rows. Shows a `graph` column when several are selected.
- **Search** — highlights matching nodes/edges in the graph (dims the rest) and
  outlines matching table rows.
- **Branch selector + management** — view any branch, and (single graph selected)
  **create** a branch forked from the current one, **merge** it into `main`
  (native, edge-deduped), or **delete** it via the `+branch` / `merge→main` / `del`
  buttons. Slashed device names (`device/<host>`) are handled. Branches are
  per-graph, so the controls are disabled while several graphs are selected.

Node ids are namespaced `<graph>::<slug>` internally, so identical slugs in
different graphs can never collide or appear joined — edges only exist within one
graph. The UI shows the bare slug.

## Design

- Flask + gunicorn. The browser gets data from `/api/graph?graph=a,b,c&branch=`
  (JSON merged from one `POST /graphs/<id>/export` per graph, edges de-duplicated);
  the frontend is a single self-contained HTML/CSS/JS string — no external assets,
  Authelia/Caddy-friendly. A graph that fails to export is reported in `errors`
  rather than blanking the page.
- **Holds the Omnigraph bearer token server-side**; the browser never sees it.
- Reads via `GET /graphs/<id>/branches` + `POST /graphs/<id>/export`. Branch writes
  proxy to the server: `POST …/branches` (create), `POST …/branches/merge`
  (`{source,target}` — body-based so slashed names work), `DELETE …/branches/<name>`
  (%2F-encoded). All gated by Authelia in front (below), since the app has no auth.

> **Security:** the app has no auth of its own — put **Authelia SSO in front**
> (Caddy `import authelia`). Never expose it directly. The Omnigraph **API**
> host, by contrast, is protected by its own bearer token and must NOT sit
> behind Authelia (that would block programmatic/MCP clients).

## Env

| Var | Default | Purpose |
|---|---|---|
| `OMNIGRAPH_URL` | `http://omnigraph-server:8080` | Omnigraph server base URL |
| `OMNIGRAPH_TOKEN` | — | bearer token (kept server-side) |
| `OMNIGRAPH_GRAPH` | `memory` | graph the page **opens on** (any graph is reachable from the chips) |
| `PORT` | `8090` | listen port |

> `memory` holds **only** global-scope `Preference`s now, so the default view is
> deliberately near-empty — that is not a fault. Click a project chip for the
> content, or set `OMNIGRAPH_GRAPH` to the graph you open most.
>
> Note this is `OMNIGRAPH_GRAPH` (the viewer's var). The **MCP bridge** uses
> `OMNIGRAPH_GRAPH_ID` — mixing them up silently leaves an agent on the wrong graph.

## Run

It is defined as the `omnigraph-viewer` service in the deployment compose
(`<site-repo>/mcp-servers/docker-compose.yml`) and, for local testing,
in the client/server composes (`docker-compose.server.yml`). Behind Caddy it is
served at `omnigraph-ui.example.org` (Authelia-gated).

```bash
docker compose --env-file .env.shared --env-file .env.server --env-file ~/.config/autoos/site.env -f docker-compose.server.yml up -d omnigraph-viewer  # browse http://127.0.0.1:8090
```
