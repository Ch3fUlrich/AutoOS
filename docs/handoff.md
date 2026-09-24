# Handoff — AutoOS (2026-09-24, branch `main`, 31 ahead of `origin/main`)

**Start here, new agent.** Branch is **`main`** — local only, 31 commits ahead
of `origin/main`, working tree clean at `1f68b36`. Zero prior context needed
beyond this file + the repo. Proof for claims lives in
`docs/verification.md` (dated runs); routing truth lives in
`docs/models.md` + `docs/models-proposed.md`.

Your role: t1 orchestrator (`omniroute/t1-orchestrator#high`). Full protocol =
`.agents/skills/unattended-orchestration/unattended-orchestration.md`.
Hard rules (AGENTS.md): never commit secrets/binaries; destructive actions
are opt-in only; read-modify-write for PATH/profile/config; back up
user-owned files first; tests never install anything; suites are in-house,
no framework. Secrets live in git-ignored `configuration/api-keys.yml` +
`configuration/litellm/.env` — never in tracked files, never in output.

## 1. What landed (see CHANGELOG.md for detail)

- Tier ids renamed (`tier1/2/3`/`rag` → `t1-orchestrator`/`t2-worker`/
  `t3-driver`/`t4-rag`); retired ids deleted from the live gateway store
  (verified clean) with exact-list + retired-ids regression tests in both
  suites — a resurrection fails CI.
- New combos (repo-side, need `apply` to go live): pinned `opus-4-6`
  (agy thinking → cc opus, 200k) and `t2-orchestrator` (opus pair +
  deepseek tail). `t1-orchestrator` stays spark-only by design.
- Curated legs: `t2-worker` += `antigravity/gemini-3.7-flash-high`
  (ack 3.6s); `t2-worker-free-only` keeps `-medium` (ack 1.6s).
  No `antigravity/*3.8*` refs exist on the gateway — do not invent any.
- Free-only LiteLLM groups (`t1/t2/t3-*-free-only`, no fallbacks) mirror
  combos minus gateway-only legs (`antigravity`, `cc`); new suite test pins
  this. Credit combos + Z.AI dropped (documented in
  `docs/models-proposed.md`).
- Live connections: `antigravity` + `claude` OAuth (account-named, active);
  `zai`, `zcode`, `opencode`, `devin` gateway connections registered
  (`devin`/`zcode`/`opencode` list no servable models — upstream limits).
- CLIs wired: Qwen Code (npm 0.24.4, `t2-worker` entry, headless ack),
  Claude Code Merchant (`ANTHROPIC_BASE_URL/AUTH_TOKEN` merged live),
  `qodercli` 1.1.62 (PATH + PAT automation committed), Devin CLI 3000.11.3
  (winget). agy 1.2.9 confirmed — no Gemini CLI needed.
- Suites: sh full 422/0/0; ps1 router set 484 passed, 1 pre-existing skip.
  A crashing python heredoc used to pass on empty stdout — all 16
  empty-expected checks now capture stderr plus a meta-guard test.

## 2. Merge to main (operator — agents cannot push)

`opencode.jsonc` denies `git push *` to agent sessions. That rule is
deliberate (it keeps unreviewed agent work off the public repo) — do not
weaken it to push this work. The branch is already `main`; "merging to
main" means publishing local `main` to `origin/main`:

```powershell
git log origin/main..main --oneline   # review the 31 commits first
git push origin main                  # fast-forward; nothing to merge locally
```

Pre-push checklist: both suites green (`bash tests/run-tests.sh`,
`pwsh -NoProfile -File tests/run-tests.ps1`), `shellcheck` /
`Invoke-ScriptAnalyzer` clean on touched files, no secrets in the diff
(`git diff origin/main --stat` + grep for credential shapes). CI runs both
suites on push.

## 3. Remaining tasks — operator

| # | Task | How |
|---|---|---|
| 1 | Push (see §2) | `git push origin main` |
| 2 | Restart OmniRoute gateway | Process dates to 21.09 (predates qwen install, OAuth, PATH fix) — dashboard checks + new binaries only resolve after restart, then "⟳ Refresh detection" |
| 3 | `devin auth login`, then dashboard `devin-cli` connection | `providers add devin-cli --oauth` answers Unknown (CLI OAuth allowlist holds 8) — dashboard only |
| 4 | Dashboard `qoder` connection, then send `models qoder` output | No CLI OAuth flow exists for qoder |
| 5 | `apply` to create `opus-4-6` + `t2-orchestrator` live (repo has them, store lacks them), then ack-probe each leg | `.\configuration\omniroute\apply.ps1 -Probe` |
| 6 | Fund-or-drop Z.AI | Currently 429-insufficient-balance; history kept in `docs/models-proposed.md` §E |
| 7 | Zed picker proof (16+ entries now) + OpenHands container proof | Manual UI checks, still unconfirmed |
| 8 | `gh` PR stack (`gh auth status` — harness blocks `gh` for agents) | Still parked |
| 9 | Autostart (gateway + litellm + OpenHands) | `configuration/autostart/` |

## 4. Remaining tasks — agent lanes

| # | Task | Notes |
|---|---|---|
| A | `tools/sync-ide-models.py` + static effort aliases | Touches opencode.jsonc, tier-profiles, both Zed writers — dedicated lane |
| B | Coverage merges per the W1 inventory | Most "dupes" are cross-language mirrors that stay; remainder is small |
| C | Nemotron review verdict (pending; GLM reviewer failed — its free endpoint cannot serve tool-using subagents, no silent substitution made) | Doc unchanged since spawn; folds into a follow-up commit on arrival |

## 5. Key commands

```powershell
.\configuration\omniroute\apply.ps1 -Probe   # register + rebuild + prove
.\configuration\litellm\start-litellm.ps1    # proxy on :4000 WITH its .env
python tools/sync-router-tiers.py --check
python tools/audit-router.py                 # live drift gate (--offline for CI)
powershell -NoProfile -File tests\run-tests.ps1
bash tests/run-tests.sh
```

## 6. Known issues (detail: docs/verification.md)

- **Long-running server processes keep stale env/PATH** (measured 2026-09-24:
  gateway from 21.09 missed qwen install, OAuth, PATH fix). Registry writes
  broadcast `WM_SETTINGCHANGE`, but the server itself needs a restart.
- **A leg that `simulate` resolves can still 400 at chat time.** Probe every
  new leg with one direct `:20128` chat; the falsified refs are gated by
  both suites.
- **`omniroute models` shows display names, not refs.** Routable refs come
  from authenticated `/v1/models` (client key, never printed) — never guess
  a ref into `combos.json`.
- **`providers add --dry-run` skips the OAuth allowlist check** — a passing
  dry-run does not prove the flow exists (`devin-cli --oauth`).
- **Free-model reviewer endpoints may lack tool support** (measured:
  `z-ai/glm-5.2:free`). Verify the exact model id serves tools before
  spawning review subagents on it.
- Serena memory tools are off by design; Omnigraph + graphify are the
  memory/graph layers. Zen free 500s at peak / Zen paid 402s without balance
  (chain hops). `/v1/models` 401s for client keys (use `--probe` / authed
  calls). Cloudflare Workers AI needs Account ID in the dashboard.
