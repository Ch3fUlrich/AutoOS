# Task for the coding.example.internal agent — deploy graphify + omnigraph-viewer changes

You are on the server (coding.example.internal). A workstation agent changed the `agent-skills` repo.
Pull it, apply the two infra changes below, verify against the **central** omnigraph stack,
and report. Keep central `main` clean — never `overwrite` it.

## 0. Sync the repo
`cd ~/code/agent-skills && git fetch && git checkout graphify-single-entry-wiring && git pull`
(or `main` if it has been merged). Read
`docs/superpowers/specs/2026-07-20-graphify-single-entry-wiring-design.md` and
`skills/mcp-servers-setup/SKILL.md` → Graphify for the model.

## 1. Graphify — wire the single server entry
Graphify is now **one cwd-relative user-scope entry**, never per-repo. On this server use Docker:
- Build the image: `docker build -t graphify-mcp:latest infra/mcp-servers/servers/graphify-mcp`
- Put the wrapper on PATH: `ln -sf "$PWD/infra/mcp-servers/bin/graphify-mcp" /usr/local/bin/graphify-mcp`
- Register **one** user-scope entry `"graphify": { "command": "graphify-mcp" }` in `~/.claude.json`;
  **remove** any per-repo `graphify-docker` from every repo's `.mcp.json` and any user-scope
  hardcoded-mount graphify.
- Verify: `bash infra/mcp-servers/scripts/linux/check-graphify-scope.sh --fix` must exit 0
  (single user-scope entry, no project graphify entries). Confirm a graph query from two
  different repos returns each repo's OWN god_nodes.

## 2. Omnigraph viewer — deploy the new "Sync log" view
`infra/mcp-servers/servers/omnigraph-viewer/app.py` gained `/api/sync-history` and a **Sync log**
view (per-graph last-synced time + history + source).
- Rebuild + redeploy the `omnigraph-viewer` image in whatever compose runs the central stack
  (canonical: the Server repo compose — do NOT clobber the live viewer/other services).
- Verify: open the viewer, click **Sync log** → all 5 graphs list with a last-synced time and a
  history table. `curl -s localhost:<viewer>/api/sync-history` returns JSON with `last_synced_us`.

## 3. Make "source" meaningful (currently shows `default`)
The viewer reads each commit's `actor_id` as the sync source; today it's the server default
`"default"`. Make the sync tag its central pushes with the **client device name** so the column
shows *who* synced:
- Find whether `omnigraph load` (the CLI the sync uses in `omnigraph-setup/omnigraph-sync.sh`)
  accepts an actor/author flag, or whether the write API takes an actor header. Determine it
  against the live server, then set it to the device hostname (`$DEVICE`) on the push.
- Apply the same in `sync-windows.ps1` (Windows clients) for parity.
- Verify: after one sync, the newest central commit's `actor_id` == the device, and the viewer's
  Sync log shows the device (not `default`) in the source column.

## Notes
- A graphify Decision/Rule was written to the `agent-skills` graph on the workstation's LOCAL
  omnigraph (`as-dec-graphify-single-user-scope-entry`, `as-rule-graphify-one-user-scope-entry`);
  the workstation has synced it, so it should already be on central — confirm it's present.
- The Windows sync-popup fix (`omnigraph-sync-hidden.vbs`) is Windows-only — ignore on Linux.
