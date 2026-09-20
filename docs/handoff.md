# Handoff — AutoOS AI routing state (2026-09-20)

Copy-paste this whole file to another agent together with the repo. No keys
inside: everything secret lives in git-ignored `configuration/api-keys.yml`.

## Where things stand

- Gateway **OmniRoute 3.8.50** on `:20128`, all 6 combos created and probed
  OK today (`tier1`→spark-contributor, `tier1-clean`→spark,
  `tier2`→gpt-oss-120b, `tier3`→mistral-code-latest, cleans→qwen/flash).
- Clients proven: **opencode CLI 2.0.11** (`tier1/2/3/3-clean` all ack),
  **OpenHands** (`docker.openhands.dev/...:latest`, profile `openai_tier1`,
  message round-trip "sandbox ok" proven today after fixing a regression).
- Suites: **210 PS / 102 sh**, green. Tree: commit pending (see `git status`).
- Live processes: `omniroute` gateway, `openhands-autoos-test` container.
  No orphan sandboxes (cleaned).

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

## Open (do next, in this order)

1. **Neovim/LazyVim on Windows** — catalog has no Windows neovim component;
   `nvim.exe` exists at `C:\Program Files\Neovim` but is not on PATH and has
   no LazyVim/sidekick. Add component + installer + test.
2. **Tiers by capability, not only context** — small-context models can
   sub-orchestrate; encode role/capability requirements per tier.
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
