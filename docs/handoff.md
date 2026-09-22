# Handoff — AutoOS live state (2026-09-22, main @ router-expansion era)

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

## MCP servers — use these, not raw reads (MANDATORY in every session)

**Every coding app is already wired** (2026-09-22): repo `opencode.jsonc`
and the global user config carry all five servers; Zed's `context_servers`
carry all five; OpenHands `mcp_config` carries all five; Neovim+sidekick
inherits through the opencode CLI. `serena` MEMORY tools are disabled at the
top level of both opencode configs (derived from
`catalog/agent-harness.json` → `mcp_servers.serena.memory_tools`) and on
leaf-agent blocks. **Omnigraph is the only memory layer — never use serena
memory, never write project data to the global `memory` graph.**

| Server | Scope | Use for | Activate / address |
|---|---|---|---|
| **serena** | user scope, ONE entry | symbols, references, rename (90%+ cheaper than file reads) | `activate_project` with the ABSOLUTE worktree path; one session = one worktree (activation kills the previous LSP) |
| **graphify** | user scope, cwd-relative `graphify-out/graph.json` | blast radius, "what connects X to Y" | link `graphify-out/` into worktrees; never hand-build a graph in a worktree |
| **omnigraph** | per-repo (`OMNIGRAPH_GRAPH_ID=autoos`), bearer token | durable decisions/rules (recall at start, persist at end — contract: `docs/omnigraph.md`) | never write project data to the global `memory` graph |
| playwright | user scope | UI validation / E2E only, never web search | global npm install, not npx |
| context7 | user scope | library docs before web search | needs API key in args |

**Session protocol for the items below** (this is not optional):

1. **Start**: `omniroute/...` routing is the default; recall memory first —
   if the graph pin is wrong, fix it before reading code (`docs/omnigraph.md`).
2. **Navigate with serena + graphify, not `read`.** Read a symbol's body, a
   reference list, or a graph neighbourhood; only fall back to whole-file
   reads when the tools cannot answer.
3. **Persist durable findings to omnigraph** as typed nodes edged to the
   `autoos` Project plus the component they touch — a new decision, rule, or
   convention with no hub edge renders as "global" and is a bug.
4. A worktree holds tracked files only: `.env`, `api-keys.yml`, graphs and
   venvs never exist there — resolve through the main checkout, never copy
   secrets.

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
- **Suites**: green 2026-09-22 on the router-expansion batch
  (ps1 823/0/1, sh 402/0/0). CI runs both on push.
- **Router combos (12)** on `:20128`, all live-acked 2026-09-22:
  `tier1`/`tier1-clean`/`tier2`/`tier2-clean`/`tier3`/`tier3-clean` (roles),
  `spark-1.3-contributor`, `gemini-3.8-flash`, `deepseek-v4.1-flash`,
  `tier2-credit`, `tier3-credit` (paid-credit burn), `rag` (cohere trial).
  Leg order lives in `configuration/omniroute/combos.json` (single source);
  `tools/sync-router-tiers.py` mirrors only tier2/tier3 into LiteLLM. Every
  new leg needs one direct `:20128` chat probe — `simulate` resolves refs the
  gateway then 400s on (phantom legs, fixed 2026-09-22).
- **LiteLLM proxy**: start it with `configuration/litellm/start-litellm.ps1`
  (exports `.env` into the process; the proxy does not read it itself).
  Master key must equal the machine `LITELLM_MASTER_KEY` /
  `AUTOOS_LITELLM_API_KEY` value or every call answers "No connected db".
- **Tier profiles single-sourced**: `configuration/openhands/tier-profiles.json`
  (profile ids, model ids, token windows, reasoning flags, container-side
  base URLs — no keys); both embedded installers read it (inline tuples
  deleted); `tools/sync-openhands-profiles.py` regenerates user profiles
  byte-identical to installer output and runs from `start-stack.*` on every
  openhands start. Profiles: `omniroute-tier1/2/3` + `-clean` twins on
  `:20128`, `litellm-tier1/2/3` fallback on `:4000`.
- **Zed**: `autoos-omniroute` + `autoos-litellm` providers (15+6 models:
  tiers + clean twins + 4 pinned/credit routes + rag + auto/*, litellm tiers
  + paid),
  `bypass` profile (17/17 tools, tier1 default, opts into context servers),
  top-level `context_servers` (all 5 MCP servers, harness pins resolved at
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
  Repo `opencode.jsonc` pins all five servers
  (serena/graphify/playwright/context7/omnigraph) to harness pins,
  asserted by both suites. Omnigraph in the repo config uses the same pin
  as `.mcp.json` (per-repo env: `OMNIGRAPH_GRAPH_ID=autoos`).
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

## Done 2026-09-22 (router expansion + client wiring; suites green)

- **Tier profile rename**: `autoos-tier1/2/3` → `omniroute-tier1/2/3` +
  `-clean` twins on `:20128`, `litellm-tier1/2/3` fallback on `:4000`
  (single source `configuration/openhands/tier-profiles.json`).
- **Stale direct models removed**: catalog `deepseek-chat`/`deepseek-reasoner`
  and the plain `muse-spark-1.3` alias deleted; `muse-spark-1.3-contributor`
  kept and re-added as a direct opencode provider (`{env:META_API_KEY}`).
- **New gateway combos (6)**: `spark-1.3-contributor`, `rag` (cohere trial
  keys), `gemini-3.8-flash`, `deepseek-v4.1-flash` (both pinned single-model
  routes), `tier2-credit`, `tier3-credit` (paid-credit burn, no free heads,
  no strong-provider tails). All live-acked; **12 combos total** on `:20128`.
- **MCP everywhere + serena memory off**: all five servers in repo config,
  global opencode config, Zed `context_servers`, OpenHands `mcp_config`;
  Neovim inherits. Serena memory tools disabled via the harness
  `memory_tools` field (validated exact-set) — Omnigraph is the memory layer.
- **Permission fix**: leaf blockers (`commit`/`checkout`/`stash`) no longer
  merge into the top level; `git push` moved to the leaf fence. Top level and
  spawners can push; leaves cannot. `github push --dry-run` proven in-shell
  and inside a tier3 agent run.
- **LiteLLM proxy repaired**: needs its `.env` in process env → new
  `configuration/litellm/start-litellm.ps1`; master key aligned with the
  machine value (was the "No connected db" cause); cohere legs need
  `additional_drop_params: ["strict"]`; `rag` ack via `litellm/rag`.
- **Zed `settings.json` BOM fix**: PowerShell `Out-File -Encoding utf8`
  emits a BOM that serde_json rejects ("expected value at line 1 column 1").
  Writer now uses BOM-less UTF-8; the live file was repaired; a ps1 test
  asserts no BOM.
- **Phantom legs falsified**: `openrouter/gemini-3.8-flash` and
  `deepseek/deepseek-v4.1-flash` 400 at chat time (simulate resolves them) —
  both removed from `combos.json` and the live gateway; both suites now gate
  the falsified refs (`combos.json carries no phantom legs`).
- **Suites**: sh 402/0/0, ps1 823/0/1.

## Open (in this order)

0. **Commit the 2026-09-22 working tree** — the whole expansion is uncommitted
   (config, lib writers, catalog, tests, docs, new `docs/omnigraph.md`,
   `configuration/litellm/start-litellm.ps1`). Stage explicit paths, run both
   suites once, commit in coherent chunks; never `git add -A` blindly and
   watch for a stray `~/` dir some tooling creates from a literal `~`.
1. **Zed model picker proof** — restart Zed (tray → Quit; running process
   predates the env keys), open the agent-panel model picker, confirm the
   `autoos-omniroute` list (15 entries now) and the litellm fallback, default
   `tier1`, and send one message on `tier3` + one litellm fallback.
2. **OpenHands container proof** — `start-stack -App openhands`, profile
   `omniroute-tier1` via `host.docker.internal:20128`, sandbox round-trip.
   (The two `start-stack.ps1` defects from DONE-L2B are already fixed in
   tree; confirm on the current image.)
3. **Remaining PRs** — DONE #85, #101, #83 (do_GET split), #88 (do_POST
   split, usb endpoint preserved as `_post_usb_create`). NEXT in order:
   #89 (append_line_once tests, trivial) → #84 (build_state test) →
   #98 (detect_system tests) → #96 (O(N²) fix) → #95 (UI color tests) →
   #94 (O(1) catalog) → #93 (prompt cache) → #92 (broken_links tests) →
   #90 (os.walk). Shared rules: empty-catch hunks land once (main already
   carries them — drop from PRs), strip scratch artifacts, serialize
   #84→#86→#87. DEFER: #99 (superseded), #97, #87, #86.
4. **Chatter tier** — route status/summary/dispatch text to tier3 free-first;
   local ollama is LAST resort only (model startup latency too high for quick
   extractions). Reasoning stays tier1/2; cheap tiers transport verbatim and
   extract with exact quotes, never rephrase decisions; record spend per
   DONE note. See `docs/unattended-orchestration.md`.
5. **Leg-probe sweep** — DONE 2026-09-22 for the current 20 legs (no
   phantoms; results table in `docs/verification.md`). Repeat after any leg
   add/edit: one direct `:20128` chat per leg; `simulate` is not proof. Keep
   the `combos.json carries no phantom legs` test's banned list growing with
   every falsified ref.
6. **Credit tiers burn-in** — run real work through `tier2-credit` /
   `tier3-credit` and record actual spend per provider (the point is to spend
   the stored cerebras/sambanova/cheaperinference balances; confirm the
   gateway bills those legs and not the direct-paid ones).
6b. **Effort control per call** — use `openrouter/meta/muse-spark-1.3-contributor#<minimal|low|medium|high|xhigh>` when the full ladder matters; gateway combos only resolve `low/medium/high`. `#max` is Zen-native only. Documented in `docs/models.md`.

6c. **Router drift gate** — run `python3 tools/audit-router.py` after ANY
   combo/client edit (live probes) and `--offline` in CI (already wired into
   both suites). It compares the repo against the live gateway store, all four
   client surfaces, the OpenHands tier profiles and the LiteLLM groups, and
   rejects combo-bypassing refs plus a too-short `maxWaitMs`. **Zen free leg is
   demoted to last in `tier1`/`spark`** — do not "restore free-first" without
   re-probing: it 403s through the gateway.

7. **Autostart** — gateway + litellm + OpenHands container resume after
   reboot (`configuration/autostart/`); Tailscale already Automatic. Use
   `configuration/litellm/start-litellm.ps1` as the proxy launch step.
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
.\configuration\litellm\start-litellm.ps1    # proxy on :4000 WITH its .env
$env:AUTOOS_OMNIROUTE_KEY = '<from api-keys.yml>'  # opencode + automation
# Zed uses AUTOOS_OMNIROUTE_API_KEY / AUTOOS_LITELLM_API_KEY instead
opencode run --agent tier2-worker --model omniroute/tier2 --auto "<brief>"
opencode run --agent tier3-reviewer --model omniroute/tier3 --auto "<review>"
opencode run --model omniroute/tier2-credit --auto "Reply with exactly: ack"
python tools/sync-router-tiers.py --check
powershell -NoProfile -File tests\run-tests.ps1
bash tests/run-tests.sh
```

## Known issues (detail: docs/verification.md)

- **A leg that `simulate` resolves can still 400 at chat time** ("not
  available in the active live catalog"). Probe every new leg; the two
  falsified refs are gated by both suites.
- **A reasoning leg needs an output budget > 100 AND a gateway deadline >
  its thinking time.** `requestQueue.maxWaitMs` ships at 15000 ms and kills
  spark mid-think; `apply.*` now sets it to 180000. A tiny `max_tokens` makes
  the same leg look dead ("empty response"). Both are budget faults, not
  routing faults.
- **Probe with `tools/audit-router.py`** (`--offline` for drift, live for the
  gateways) before blaming a model: it separates config drift (400/404) from
  provider/balance state (402/429/5xx) and checks the resilience deadline.
- **Zen's free promo leg cannot serve through OmniRoute** — it 403s with
  "OpenCode's free tier can only be used from within OpenCode". It is kept
  LAST in `tier1`/`spark` so a future policy change is still picked up, but it
  is never the critical path.
- **Direct (non-combo) models have no fallback and no quota protection** — a
  session with `gemini/gemini-3.7-flash` selected blew the 250k
  free-input-token cap in one 1935-message turn. Read `comboName` in the call
  log first when triaging (absent = a direct model, not a combo failure).
- **Serena memory tools are off by design**; Omnigraph + graphify are the
  memory/graph layers. Don't "fix" that by re-enabling them.
- Zen free 500s at peak / Zen paid 402s without balance (chain hops).
- `/v1/models` 401s for client keys (upstream); `--probe` is the check.
- `omniroute models --json` truncates at 50 (do not treat as authoritative).
- Cloudflare Workers AI needs Account ID in the dashboard before serving.
- `providers edit <name> --inactive` does not persist; use the connection ID.
- `providers test <id>` single-probe "not supported" for some providers;
  `test-all` is the reliable one. `providers list --output json` ignores the
  flag (table anyway).
- litellm crashes at startup under cp1252; launch with `PYTHONUTF8=1` (the
  starter sets it).
- PowerShell `Out-File -Encoding utf8` writes a BOM — Zed rejects it. Only
  `[IO.File]::WriteAllText(..., UTF8Encoding($false))` for Zed settings.
- WSL shells see their own loopback: `apply.sh` from WSL reports the Windows
  gateway "down" — use `apply.ps1` on Windows.
