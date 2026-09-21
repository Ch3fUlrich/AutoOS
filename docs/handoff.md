# Handoff — AutoOS live state (2026-09-21, main @ tier-merge + router era)

**Start here, new agent.** Zero prior context needed: this file + the repo is
everything. Read top to bottom, then continue from Open item 1. Concrete
proof for every claim lives in `docs/verification.md` (dated runs).

Your role: tier1 orchestrator (`omniroute/tier1#high`). Full protocol =
`docs/unattended-orchestration.md` (tiers, enforcement, watchdog). Routing
reference = `docs/models.md` (per-tier mermaid + sync contract). Runbook =
`.claude/handoff.config.json` (lanes/sessions/guards for unattended runs).

Hard rules (AGENTS.md): never commit secrets/binaries; destructive actions
are opt-in only; read-modify-write for PATH/profile/config; back up
user-owned files first; tests never install anything; suites are in-house,
no framework. No keys, no IPs in tracked files: secrets live in git-ignored
`configuration/api-keys.yml` + `configuration/litellm/.env`; machine IPs are
`<tail-ip>`/`<lan-ip>` placeholders.

## MCP servers — use these, not raw reads

| Server | Scope | Use for | Activate / address |
|---|---|---|---|
| **serena** | user scope, ONE entry | symbols, references, rename (90%+ cheaper than file reads) | `activate_project` with the ABSOLUTE worktree path; one session = one worktree (activation kills the previous LSP) |
| **graphify** | user scope, cwd-relative `graphify-out/graph.json` | blast radius, "what connects X to Y" | link `graphify-out/` into worktrees; never hand-build a graph in a worktree |
| **omnigraph** | per-repo (`OMNIGRAPH_GRAPH_ID=AutoOS`), bearer token | durable decisions/rules (recall at start, persist at end) | never write project data to the global `memory` graph |
| playwright | user scope | UI validation / E2E only, never web search | global npm install, not npx |
| context7 | user scope | library docs before web search | needs API key in args |

Memory tools inside Serena are DISABLED — Omnigraph is the only memory layer.
A worktree holds tracked files only: `.env`, `api-keys.yml`, graphs and venvs
never exist there — resolve through the main checkout, never copy secrets.

## Where things stand (measured 2026-09-21)

- **Branch**: `main` = `origin/main`, clean. Remote `tier-orchestration-2026-09-20`
  kept as archive until integration is declared stable; all `merge/*` work
  branches deleted after landing. Stale local stub TAG: `archive/tier-stub-pr9`.
- **Gateway OmniRoute 3.8.50** on `:20128`: 13/13 providers registered
  (incl. groq/cerebras via fixed `apply.ps1`), 6/6 combos probe-green:
  tier1→`meta/muse-spark-1.3-contributor`, tier1-clean→same contributor,
  tier2→`gemini-3.8-flash`, tier2-clean→`deepseek-flash`,
  tier3→`mistral-code-latest`, tier3-clean→`deepseek-flash`.
- **Contributor-only block** (operator 2026-09-21): no combo may reference
  plain `muse-spark-1.3`; enforced by both suites. Consequence: tier1-clean
  is paid-only, NO LONGER trains-nothing (documented in combos.json +
  models.md — do not "fix" this back silently).
- **muse-code (Meta direct)**: connection carries an empty outbound URL
  (OmniRoute defect, open upstream) → **deactivated by connection ID**
  (by-name edit echoes success without persisting). Spark routes via
  OpenRouter/Zen contributor legs.
- **LiteLLM fallback** on `:4000` (uv tool install; `PYTHONUTF8=1` required —
  its banner crashes startup under cp1252). Keys mirrored from api-keys.yml
  by hand (no committed script does this yet — see Open 6).
- **Clients**: opencode CLI 2.0.12 V2 (`@opencode/cli`; never V1 `opencode-ai`),
  3-tier agents live (`tier1-orchestrator`→`tier2-worker`→`tier3-reviewer`,
  `subagent_depth: 2`, reviewer = read/grep/glob/bash allow +
  edit/write/subagent deny). Zed: `autoos-omniroute` + `autoos-litellm`
  providers, 15 models, `bypass` profile, keys via
  `AUTOOS_OMNIROUTE_API_KEY`/`AUTOOS_LITELLM_API_KEY` env (NEVER settings.json
  — Zed ignores `api_key` there and hides keyless providers). Neovim+LazyVim
  + sidekick installed via `setup.ps1 -Only`. OpenHands image pulled;
  container start + profile proof is Open 5.
- **Suites**: ps1 ~600 / sh ~390, gate re-running 2026-09-21 18:00 for the
  router batch (tier spec+sync, Zed MCP, key mirror, pin compliance). CI runs
  both on push.
- **Tier profiles single-sourced**: `configuration/openhands/tier-profiles.json`
  (model ids, token windows, reasoning flags, container-side base URL — no
  keys); both embedded installers read it (inline tuples deleted);
  `tools/sync-openhands-profiles.py` regenerates user profiles byte-identical
  to installer output and runs from `start-stack.*` on every openhands start.
- **Zed**: `autoos-omniroute` + `autoos-litellm` providers (9+6 models),
  `bypass` profile (17/17 tools, tier1 default, opts into context servers),
  top-level `context_servers` (serena+graphify, harness pins resolved at
  runtime). Keys NEVER in settings.json (Zed ignores `api_key` there and
  hides keyless providers): `AUTOOS_OMNIROUTE_API_KEY` /
  `AUTOOS_LITELLM_API_KEY` env, persisted via setx. Restart Zed after key
  changes — running processes never pick up setx.
- **opencode desktop** shares the CLI config files (bundles CLI v2.0.11):
  same stale-env rule, no separate key store.
- **Key mirror**: `tools/mirror-litellm-env.py` (api-keys.yml → litellm/.env
  + master-key gen, never prints values), tested both suites.
- **MCP shape verdict**: opencode loads BOTH flat `mcp.<name>` AND nested
  `mcp.servers.<name>` (cfgprobe, all connect) — no installer change.
  Repo `opencode.jsonc` pins serena/graphify/playwright/context7 to harness
  pins, asserted by both suites. Omnigraph stays in `.mcp.json` (per-repo env).
- **Live processes** (all manually started, none autostart yet — see Open 7):
  `omniroute --no-open --port 20128`, `litellm --config ... --port 4000`.
  Machine env (setx, user scope): `AUTOOS_OMNIROUTE_KEY`,
  `AUTOOS_OMNIROUTE_API_KEY`, `LITELLM_MASTER_KEY`, `AUTOOS_LITELLM_API_KEY`.
  New shells inherit them; long-running tool servers do NOT — restart a
  server process to pick up rotated keys.

## Proven 2026-09-21 (first live tiered runs, free pools, ~zero spend)

- W1 (tier2 worker): merged L2-B's `tools/sync-router-tiers.py` + litellm
  resync → tier3 review **MERGE** → landed. `--check` is the drift gate.
- W2 (tier2 worker + L1 amendment): `subagent_depth: 2`; found + fixed the
  tool-less reviewer stanza (two live refusals; gateway probing proved 6/8
  tier3 legs call tools, so the stanza was at fault) → review affirmative →
  landed.
- L2-A docs (mermaid per-tier map, ungated tier2, sync contract): rebased +
  reconciled to contributor truth → landed.
- Reviewer repair + nesting proof: `--agent tier3-reviewer` answers `ack`.
- Cross-family review rule: writer and reviewer must be different model
  families; record the serving model of both, re-run on a family match.

## Open (in this order)

1. **Zed model picker proof** — restart Zed (tray → Quit; running process
   predates the env keys), open the agent-panel model picker, confirm both
   providers list, send one message on `tier3` + one on a litellm fallback.
2. **OpenHands container proof** — `start-stack -App openhands`, profile
   `openai/tier1` via `host.docker.internal:20128`, sandbox round-trip.
   (Also fix the two `start-stack.ps1` defects in DONE-L2B: `-it` in
   non-interactive shells, stale `schema_version 6` profile.)
3. **Remaining PRs** — #83→#88→#89→#84→#98→#96→#95→#94→#93→#92→#90 rebase+land
   (serve/tests, empty-catch hunks land once, strip scratch artifacts);
   defer #99 (superseded), #97, #87, #86. #85 + #101 closed.
4. **Chatter tier** — route status/summary/dispatch text to tier3 free-first;
   local ollama is LAST resort only (model startup latency too high for quick
   extractions). Reasoning stays tier1/2; cheap tiers transport verbatim and
   extract with exact quotes, never rephrase decisions; record spend per
   DONE note. See `docs/unattended-orchestration.md`.
5. **MCP wiring per the tool-strength eval** (2026-09-21, in-session notes):
   9/12 tier legs proven tool-capable (tier1 MCP work: openrouter
   contributor only). Measured: opencode loads BOTH flat `mcp.<name>` AND
   nested `mcp.servers.<name>` (cfgprobe, all connect) — no installer change
   needed. Landed: repo `opencode.jsonc` pins all four servers
   (serena/graphify/playwright/context7) to harness pins, asserted by both
   suites. Left: context_servers for Zed, omnigraph stays in `.mcp.json`
   (per-repo env).
6. **Key mirroring script** — DONE 2026-09-21: `tools/mirror-litellm-env.py`
   (api-keys.yml → litellm/.env + master key gen, never prints values),
   tested both suites.
7. **Autostart** — gateway + litellm + OpenHands container resume after
   reboot (`configuration/autostart/`); Tailscale already Automatic.
8. **Skills import (Phase 2)** — agent-skills repo → AutoOS `skills/` via
   `git subtree` (never filesystem copy); EXCLUDE unlicensed
   qa-swarm/review-triage/babysit-prs (rewrite natively instead); fix the
   swarm-orchestration dangling cross-links in S2b.
9. **Housekeeping** — delete local `integration/tier-2026-09-20` after CI is
   green; remove `AutoOS-W1`/`AutoOS-W2` dirs (locked by the opencode
   background service — restart it or reboot, then delete); drop remote
   `tier-orchestration-2026-09-20` only when integration is declared stable.

## Key commands

```powershell
.\configuration\omniroute\apply.ps1 -Probe   # register + rebuild + prove
$env:AUTOOS_OMNIROUTE_KEY = '<from api-keys.yml>'  # opencode + automation
# Zed uses AUTOOS_OMNIROUTE_API_KEY / AUTOOS_LITELLM_API_KEY instead
opencode run --agent tier2-worker --model omniroute/tier2 --auto "<brief>"
opencode run --agent tier3-reviewer --model omniroute/tier3 --auto "<review>"
python tools/sync-router-tiers.py --check
powershell -NoProfile -File tests\run-tests.ps1
bash tests/run-tests.sh
```

## Known issues (detail: docs/verification.md)

- Zen free 500s at peak / Zen paid 402s without balance (chain hops).
- `/v1/models` 401s for client keys (upstream); `--probe` is the check.
- `omniroute models --json` truncates at 50 (do not treat as authoritative).
- Cloudflare Workers AI needs Account ID in the dashboard before serving.
- `providers edit <name> --inactive` does not persist; use the connection ID.
- `providers test <id>` single-probe "not supported" for some providers;
  `test-all` is the reliable one. `providers list --output json` ignores the
  flag (table anyway).
- litellm crashes at startup under cp1252; launch with `PYTHONUTF8=1`.
- WSL shells see their own loopback: `apply.sh` from WSL reports the Windows
  gateway "down" — use `apply.ps1` on Windows.
