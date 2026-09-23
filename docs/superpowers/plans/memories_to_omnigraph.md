# Memories to write to Omnigraph (held while the store is frozen)

**Status (2026-09-18): Omnigraph WRITES ARE FROZEN by the operator.** Nothing below has been
written to `main` of any graph. Do not write until the operator lifts the freeze and the `autoos`
graph is restored. Open task: "Restore the autoos graph and replay this file", recorded in the merge
plan's "Where things stand" and the L1 DONE note.

## 1. Incident: `autoos` main lost nodes (2026-09-18)

**Timeline (UTC, all measured):**

| Time | Event | Evidence |
|---|---|---|
| before 11:00 | `autoos` main: 1 Project (`autoos`), 2 Rules, 2 Decisions, 1 Task, 1 Component, with their edges | pre-session reads; snapshot `01M2T2K3EARTYSTYMC7QV718FX` is still readable |
| ~11:04–13:16 | L1 writes on branch `mem/autoos/merge-decisions-20260918`. Many calls returned `fetch failed` through the docker `omnigraph-mcp` bridge. `main`'s commit log gained 21 commits by actor `default` in the window, so some "failed" calls committed after the connection dropped (inferred from timing) | `commits_list` saved output, commits `01M2T30GN1…` → `01M2TAKAY5AYM29Y6JW6NEMB57` |
| ~12:1x | `omnigraph-server` at its 1 GiB limit: cgroup `memory.events max 316585`, anon 916 MiB. Operator approved a live raise: `docker update --memory 4g --memory-swap 4g omnigraph-server` (before: 1 GiB + 1 GiB swap). No restart | `docker inspect`, `/sys/fs/cgroup/memory.events` |
| 13:16:53 | commit `01M2TAKAY5AYM29Y6JW6NEMB57` removes Project `autoos` from main; its parent `01M2TAGE7P0HG16RPNA7RPFD97` (13:15:18) still has it. The timing matches L1's first `branches_merge` of the branch, which returned `fetch failed` | snapshot queries per commit |
| after | Retrying the merge: `stale view of 'edge:ConstrainsProject' … refresh and retry`, then `merge conflicts: … (orphan_edge) … and 5 more` | tool output |

**Lost from `autoos` main:**
- Project `autoos`;
- Decisions `autoos-image-mirrors-verified-against-canonical-manifest` and
  `autoos-usb-plan-fetch-first-and-wipe-flag-consent`;
- Component `autoos-usb-planner`;
- every edge touching those nodes: the Rules' `ConstrainsProject`, the Task's `Tracks` and
  `Implements`, `PartOf`, `DecidedIn`, `Affects`.

**Still on main:** Rules `autoos-usb-readback-before-ready` and
`autoos-windows-native-stderr-under-stop-preference`, and Task `autoos-usb-first-boot-on-good-stick`.
They are unlinked.

**Recovery sources:**
- snapshot `01M2T2K3EARTYSTYMC7QV718FX` (the full pre-session state);
- branch `mem/autoos/merge-decisions-20260918`, which holds the pre-session nodes plus the new ones
  below. Its edge set may hold duplicates from late-committing calls, so do not merge it: rebuild
  from the snapshot.
- Unrelated and pre-existing: MinIO's scanner logs `file is corrupted (cmd.StorageErr)` hourly
  (45 times in 48 h, first seen before this session).

**Bridge behaviour measured today** (cause unproven; verify before it becomes a rule):
- `load` (merge, `from: main`) returned `fetch failed` twice, and reads directly afterwards showed
  nothing landed.
- Under memory pressure, single-edge `mutate` calls also failed. After the 4 GiB raise, single
  `DecidedIn`/`ConstrainsProject`/`Tracks` inserts succeeded.
- `Implements` (Task→Decision) inserts failed 3 of 3, even with memory headroom. A 6-edge mutate
  containing `Implements` failed with headroom.
- A `mutate` whose params held `${OMNIGRAPH_TOKEN}` and quote characters failed; the same text
  without them succeeded once. This is NOT proven to be about content: memory pressure was active.
- Reads straight after a `fetch failed` write are not proof it did not land. The commit log showed
  more commits than the calls that succeeded.

## 2. Memories to write (`autoos` graph), in replay order

Replay rule: one statement per `mutate`, sequentially. After each write, check main with a read and
with the `commits_list` head. Never retry a `fetch failed` until a read at least a minute later
shows it absent.

### 2a. Restore the pre-session state first

Read every field of the lost nodes from snapshot `01M2T2K3EARTYSTYMC7QV718FX` and re-insert them
with their edges. Also re-link the surviving Rules and Task to the Project.

### 2b. New nodes from the L1 session (2026-09-18)

| Type | slug | Content |
|---|---|---|
| Task | `merge-agent-skills-into-autoos` | title: "Merge agent-skills' current state into AutoOS (plan 2026-09-18, agent-skills docs/superpowers/plans); then archive agent-skills"; state: in-progress |
| Decision | `merge-d10-openhands-profile-guard` | AutoOS prepends an AI_AGENT=openhands early-return guard to existing PowerShell profiles (D10). OpenHands probes PowerShell with a 5 s timeout and without -NoProfile (software-agent-sdk#5133, fix pending in #3913); the operator profile takes 5 to 13 s; the guard keeps the probe at about 0.5 s. Remove once #3913 ships. accepted, 2026-09-18 |
| Decision | `merge-d2-nothing-dropped-scrub-then-migrate` | Merging agent-skills into AutoOS drops nothing: the listed private-looking parts are scrubbed, then migrated and integrated; originals stay in agent-skills history; only the scanner's private patterns.txt is never migrated. accepted, 2026-09-18 |
| Decision | `merge-omnigraph-viewer-own-public-repo` | The Omnigraph web UI moves to its own public GitHub repository, prepared locally from the scrubbed current state with no history; MIT; only the operator creates the GitHub repo and pushes. accepted, 2026-09-18 |
| Decision | `merge-d3-site-values-in-user-site-env` | Private infrastructure values are placeholders in tracked files; the real values live in ~/.config/autoos/site.env; lookup order: environment, then site.env, then compose defaults. accepted, 2026-09-18 |
| Decision | `merge-d4-secrets-in-user-config` | Secrets live in ~/.config/autoos/api_keys.conf on every OS; lookup order: AUTOOS_SECRETS, then user scope, then legacy agent-skills/secrets with a notice until Phase 9. accepted, 2026-09-18 |
| Rule (must) | `mcp-token-never-literal-in-config` | Never write the Omnigraph token or any secret as a literal into an MCP config such as ~/.claude.json or .mcp.json. A docker launch passes it by name (-e OMNIGRAPH_TOKEN), and .mcp.json references the environment variable. Measured 2026-09-18 (Task 0.9a): by-name works; without the variable the server answers missing bearer token |
| Decision | `orchestration-deepseek-first-claude-closes` | Operator rule 2026-09-18: L3 implementation and bulk work go to DeepSeek first, because Claude usage limits are near. The last checks and the final implementation pass are done by Claude Sonnet/Haiku. A reviewer is never the writer's model (DeepSeek wrote → Sonnet reviews; Sonnet wrote → DeepSeek reviews). accepted |
| Decision | `orchestration-piecewise-test-ladder` | Operator rule 2026-09-18: development is piecewise; planning goes through the superpowers skills; development is test-driven and fast. Tests run first on small artificial data with known output, then on small slices of real data, and only then on the full data, so an error is caught before a long run. accepted |

**Edges:**
- `Tracks` merge-agent-skills-into-autoos → autoos;
- `DecidedIn` each of the 7 Decisions → autoos;
- `ConstrainsProject` mcp-token-never-literal-in-config → autoos;
- `Implements` merge-agent-skills-into-autoos → each of the 5 `merge-d*`/viewer Decisions.

### 2c. From the Phase 0 lanes

L1 appends here what the lanes' DONE notes list under "memory to write" (MEM's Serena-memory
migration, and anything HOST/INST record).
