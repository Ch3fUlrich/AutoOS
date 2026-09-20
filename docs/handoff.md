# Handoff — AutoOS AI routing state (2026-09-20, cheap-inference added)

**Start here, new agent.** You need zero prior context: this file + the repo
is everything. Read top to bottom, then continue from Open item 1.

How to work: you are the tier1 orchestrator (`omniroute/tier1#high`).
Full protocol = `docs/unattended-orchestration.md` (tiers, enforcement,
watchdog, runtimes). Live board = `docs/tasks.md` (update your rows).
Inside-OpenHands variant = `docs/openhands-runbook.md` (works there too:
same tiers through the same gateway, `openai/tierN` names).

No keys, no IPs inside: secrets live in git-ignored
`configuration/api-keys.yml`, machine IPs are `<tail-ip>`/`<lan-ip>`
placeholders (find yours with `tailscale ip -4` / `ipconfig` /
`hostname -I`).

## Where things stand

- Gateway **OmniRoute 3.8.50** on `:20128`, all 6 combos created and probed
  OK today (`tier1`→spark-contributor, `tier1-clean`→spark,
  `tier2`→gpt-oss-120b, `tier3`→mistral-code-latest, cleans→paid legs).
  `tier1` is **spark-only** (no `gemini-3.1-pro` — reasons worse than
  3.8-flash); `*-clean` are **paid legs only** (any big free model may
  train). Orchestrator runs at xhigh effort (`omniroute/tier1#high`,
  ack-proven).
- Clients proven: **opencode CLI 2.0.11** (`tier1/1-clean/2/3/3-clean`
  all ack; `litellm/*` correctly refuses — fallback router not running,
  by design), **OpenHands** (`docker.openhands.dev/...:latest`, profile
  `openai_tier1`, message round-trip "sandbox ok" proven today after
  fixing a regression).
- Suites: **286 PS / 108 sh**, green. Tree: commit pending (see `git status`).
- Live processes: `omniroute` gateway (:20128 up), OpenHands container
  (:3000 up), `opencode serve --hostname 0.0.0.0 --port 4096` (:4096 up
  as 401). Phone-reachable via Tailscale; pairing via `opencode pair`.
  No orphan sandboxes (cleaned).
- Retries ≈3 before provider-hop (verified live 2026-09-20):
  `requestRetry=3`/`maxRetryIntervalSec=30` (settings API),
  `waitForCooldown maxRetries=3/30s`, `comboCooldownWait maxAttempts=5`
  (transient-only; quota_exhausted hops immediately). `config.toml`
  `num_retries` aligned 5→3. Short waits = dead legs hop, runs don't stall.

## Done (this session)

1. Serena + Graphify MCP in `opencode.jsonc`, both handshake-verified.
2. LiteLLM fallback router (`configuration/litellm/`), then OmniRoute as
   primary, LiteLLM kept as fallback.
3. Tier combos in `configuration/omniroute/combos.json` + `apply.*` that
   registers providers from `api-keys.yml` (single source) and rebuilds
   combos; `--probe` proves each combo end to end.
4. `-clean` tier twins (no prompt-training routes) + context limits in
   `opencode.jsonc` (1M/128k).
5. Catalog: `zed`, `litellm`, `omniroute`, `opencode-cli` (**@opencode/cli
   V2** — V1 `opencode-ai` silently drops the config), `openhands` (all 3
   platforms, correct current image).
6. Zed agent panel routed at `:20128` via postInstall (both libs, backup +
   idempotent); LazyVim `sidekick` extra enabled by installer.
7. OpenHands `config.toml` template (dev path), start scripts with correct
   `LLM_MODEL=openai/tier1` (the `openai/` prefix is mandatory for LiteLLM),
   port 3000, current image; stale `AGENT_SERVER_IMAGE_*=1.26.0` pins found
   and removed (they caused the sandbox error-state regression).
8. Web UI: AI-providers card (id/name/configured only, never values) +
   screenshots in `docs/assets/`.
9. Docs: `models.md` (tiers, quirks, routing map), `api-keys.md`,
   `verification.md` (dated proof), `configuration/README.md`, README
   routing section.
10. Suites extended alongside (schema, merges, no-leak, placeholder,
    consistency tests). Two strict-mode bugs found by the new tests and
    fixed.
11. Structure review by a subagent: H1/H2/M7 + most mediums fixed
    (start-stack key path/export/CRLF, exact-match validation, dry-run
    gateway, create-before-delete, context alignment, no-argv keys).
12. 3-tier orchestration run 2026-09-20 (tier1 → tier2-A/B → dual tier3
    reviewers each, all via `omniroute/*` tiers): Track A removed a
    committed-IP regression (`docs/troubleshooting.md` + both suites now
    assert `<tail-ip>` placeholders and fail on `100.70.*`/`192.168.178.59`);
    Track B added the Router-tiers UI card (display-only, backend
    apply/switch still open) + rewrote the pipeline section of
    `docs/unattended-orchestration.md`; `docs/README.md` indexes the skill.
    Verified: 264 PS / 107 sh green, healthcheck 3-up/1-expected-down,
    `omniroute/tier3` + sibling-leg `ack` probes OK.
13. Tier-enforcement follow-up (same day): `opencode.jsonc` now carries a
    hard `agents` block (`tier1-orchestrator` → `tier2-worker` →
    `tier3-reviewer` leaf via `subagent` deny-all + narrow allow; last
    match wins) with PS + sh tests asserting the shape; the skill gained a
    Tier-enforcement section, a 5-rule stuck-agent watchdog (5-min probe,
    15-min silence = stuck, 429-with-retry ≠ stuck, 3 respawns max, wall
    clock + `docs/tasks.md` audit), an opencode-vs-OpenHands runtimes
    section (one container per project until Canvas leaves beta), and a
    live `docs/tasks.md` board indexed from `docs/README.md`. Verified:
    280 PS / 108 sh green.
14. Tier reshape per user steer (same day): `tier1` dropped
    `gemini-3.1-pro` (spark-only: weaker model in the orchestrator slot is
    a defect), orchestrator pinned to `omniroute/tier1#high` (xhigh effort,
    ack-proven; Zed keeps `reasoning_effort xhigh`); `*-clean` rebuilt as
    **paid legs only** (any big free model may train: no contributor-free,
    no `-contributor`, no groq/cerebras/sambanova/gemini hosts, no
    mistral-code/qwen free pools — direct-key legs stay, they bill on the
    same key). Both suites assert the shape (spark-only tier1, paid-only
    cleans). Dual-lens review: `tier2-clean` deepseek leg + `tier2` gpt-oss
    leg, both APPROVE. Probes: `tier1-clean`, `tier2`, `tier3-clean` ack;
    `litellm/*` correctly refuses (fallback router off, by design).
    Verified: 281 PS / 108 sh green.
15. OpenHands runbook + 3-service resume (tier2-E, same day):
    `docs/openhands-runbook.md` rewritten as zero-context entry (start,
    clone to `/workspace/AutoOS`, 2+2+2 discipline, DONE-proof commands,
    commit+push, ladder); `start-stack.*` gained `opencode-serve` (resume
    `:4096` when down, no-op when up); `Start-AutoOSStack.*` resumes 3
    services (gateway + container + serve, docker-missing falls through);
    tests assert `opencode-serve` in both start scripts. Verified: 280 PS
    green at the time (281/108 after the tier-reshape tests above).
16. Cheap-inference partner legs + retry trim (same day, per user steer):
    `cheaperinference/*` (own `ci_live_…` key, own billing) inserted
    between free and paid in tier2 (`deepseek-v4-flash`, `glm-4.5-air`,
    `kimi-k3`) and tier3 (`glm-4.5-air`, `minimax-m2.7`); `*-clean` stays
    direct-paid (no reseller legs). `apply.*` registers the key
    (`cheapinference` → `cheaperinference`), `api-keys.example.yml` +
    provider-status payloads + docs carry it. Ref form
    `cheaperinference/<gateway-id>` proven via `simulate --explain`
    (legs 5-7 tier2 / 4-5 tier3, all CLOSED 100%). Retries ≈3 verified
    live (settings API + resilience status); `config.toml num_retries`
    5→3. `apply.ps1` run: 13 providers incl. cheaperinference, all 6
    combos replaced. Probes: tier2 + tier3 ack. Verified: 286 PS green.

## Open (do next, in this order — each names its DONE-proof)

1. **Neovim/LazyVim on Windows** — DONE 2026-09-20: `neovim` component in
   `catalog/windows.json` (winget `Neovim.Neovim`, requires git, PATH via
   the single `Add-AutoOSPathEntry` code path) + `Install-AutoOSNeovim` /
   LazyVim / sidekick postInstall + tests in both suites. Remaining: run
   `setup.ps1 -Only neovim -Yes` once on a box with `nvim.exe` present.
2. **Tiers by capability, not only context** — DONE 2026-09-20: role labels
   (`orchestrator-1M` / `smart-reasoning-128k` / `cheap-driver-128k`) on
   combos + `opencode.jsonc` + web UI; capability requirements per tier in
   `docs/models.md`; small-context sub-orchestration rule in the skill.
3. **Autostart after reboot** — sessions (omniroute, openhands container,
   autoos `--serve`) must resume; Tailscale already Automatic, verify the
   rest. Remote reachability via Tailscale + web UIs.
4. **OpenHands = Agent Canvas ("open-canvas")** — decide: current validation
   is the docker web app; check whether Agent Canvas
   (`npm i -g @openhands/agent-canvas` or `ghcr.io/openhands/agent-canvas`)
   replaces it, then switch wiring/docs.
5. **opencode via web UI** — expose `opencode serve`/`web` for remote use.
6. **Raspberry Pi 5** — verify `light` profile + arm64 filtering end to end
   (dry-run at minimum); opencode V2 has linux-arm64 builds, omniroute needs
   Node 22+.
7. **Web UI tab for OmniRoute/LiteLLM profiles** — new tab listing tiers,
   provider status, apply/switch actions (backend endpoints + tests).
8. **Per-model free→paid chains** — e.g. spark-free → other free spark →
   paid contributor; encode as combos + expose in `opencode.jsonc`.
9. **OpenHands newest + Agent Canvas** — retest on latest image; prefer
   Agent Canvas path if it is the current app.
10. **Reviewer backlog (from structure review, still open)** — single
    `providers.json` (M2), image-string cross-check test (M3), `custom`
    provider `skipped` reporting (M4), backup-count flake (M8, ~2.5%),
    `oh-start.png` at root (L6), second (routing-correctness) review never
    reported back — re-run it if wanted.

## Key commands

```powershell
.\configuration\omniroute\apply.ps1 -Probe   # register + rebuild + prove
$env:AUTOOS_OMNIROUTE_KEY = '<from api-keys.yml>'
opencode run --model omniroute/tier3 "Reply with exactly: ack"
.\configuration\start-stack.ps1 -App openhands  # needs Docker Desktop
omniroute simulate --combo tier1                # free fallback-tree preview
powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1
bash tests/run-tests.sh
```

## Known issues (see docs/verification.md for detail)

- Zen paid legs 402 without balance; Zen promo 500s at peak (chain hops).
- `muse-code` (Meta direct) 502s in OmniRoute 3.8.50; spark via OpenRouter.
- `/v1/models` 401s for client keys; `apply` warns, `--probe` is the check.
- OmniRoute CLI `models --json` truncates; don't treat it as authoritative.
- Cloudflare Workers AI needs Account ID in dashboard before serving.
