# OpenHands runbook — continue this work from inside OpenHands

You are an agent inside the OpenHands container (`:3000`, profile
`openai_tier1` → gateway `:20128`). This file is your zero-context entry:
read it, then continue from `docs/handoff.md` Open item 1.

## 1. Where you are

- Container started by `.\configuration\start-stack.ps1 -App openhands`
  (or `start-stack.sh openhands`): `LLM_MODEL=openai/tier1`,
  `LLM_BASE_URL=http://host.docker.internal:20128/v1`, key via
  `-e LLM_API_KEY` (inherited, never on a command line).
- UI: `http://localhost:3000` locally, `http://<tail-ip>:3000` by phone.
  Find `<tail-ip>` with `tailscale ip -4` on the host.
- You route through the same OmniRoute combos as opencode
  (`configuration/omniroute/combos.json`): `tier1` = spark-only + xhigh,
  `*-clean` = paid legs only (privacy). Full skill:
  `.agents/skills/unattended-orchestration/unattended-orchestration.md`. Live board: `docs/tasks.md`.

## 2. Get the repo in your sandbox

```bash
# inside the OpenHands sandbox terminal
if [ ! -d /workspace/AutoOS ]; then
  git clone https://github.com/Ch3fUlrich/AutoOS.git /workspace/AutoOS
else
  git -C /workspace/AutoOS pull --ff-only
fi
cd /workspace/AutoOS
```

(`~/.openhands` persists on the host; the sandbox workspace itself is
ephemeral — commit + push before you stop, see section 5.)

## 3. Tier discipline (same 2+2+2, OpenHands-flavoured)

- **You = tier1-equivalent orchestrator**: plan, slice into at most 2
  tracks with disjoint file sets, define DONE per track, never code
  directly.
- Delegate each track to sandbox subagents (at most 2), each spawning at
  most 2 reviewers with different lenses (cheap mistral/qwen + smart
  gpt-oss/deepseek). Tier3-equivalents spawn nothing.
- Verify, don't trust: every track runs its suite + one live `ack` probe
  per touched tier before reporting up.

## 4. DONE-proof commands (repo root in the sandbox)

```bash
./configuration/omniroute/apply.sh --dry-run   # combos parse, registers nothing
./configuration/omniroute/apply.sh --probe     # one live ack per combo (needs gateway)
bash tests/run-tests.sh                        # full sh suite, must be green
```

(Windows host runs `tests/run-tests.ps1` — same bar, other shell.
`configuration/healthcheck.sh --fix` resumes gateway/container/serve.)

## 5. Finish like a tier1: update, commit, push

1. Update `docs/tasks.md` (your rows to Done/Blocked) and
   `docs/handoff.md` (finished items to Done with proof, re-prioritise
   Open).
2. Commit + push to `main` from the sandbox so the next machine can pull.
   Never commit secrets, IPs, or binaries (AGENTS.md hard rules 1-2).

## 6. Fallback ladder (when a rung breaks, step down one)

OpenHands `:3000` → `opencode serve :4096` (`start-stack.sh
opencode-serve`, pair via `opencode pair`) → Zed (`autoos-omniroute`
provider) → opencode desktop → nvim+sidekick (`<leader>aa`). Every rung
routes through `:20128` with the one client key (`AUTOOS_OMNIROUTE_KEY`).

---
*No secrets or real IPs here — only `<tail-ip>` placeholders.*
