# FALLBACK-FLEET phase 1 - facts found so far (2026-09-29)

Partial answers to spec open items in `2026-09-28-fallback-fleet-spec.md` section 10. Static source
reading only; the live fidelity measurement (section 3) is still to run.

| Open item | Finding | Status |
|---|---|---|
| `messages-endpoint` | The upstream OmniRoute source has `src/app/api/v1/messages/route.ts` and `messages/count_tokens/route.ts`, so an Anthropic-format `/v1/messages` and token counting exist in code. | Present in source; not yet called live |
| tool-call fidelity | Not answered by reading code. Needs the section 3 task run against a gateway leg. | Open |
| default/small model aliasing | Not answered. Needs a live probe of which model ids the harness sends. | Open |

Next: run the section 3 task on harness-on-gateway and opencode, needing a gateway key on the coding VM (never in a commit).

## Measuring the harness needs an operator-approved permission rule

The auto-mode classifier refused two shapes of the measured `claude -p` run on the local gateway:
first with `--dangerously-skip-permissions` ("Create Unsafe Agents"), then with
`--permission-mode default`, `--allowedTools Read,Edit,Skill`, `--strict-mcp-config`, a scratch worktree
and a systemd scope ("Auto-Mode Bypass"). The probe is `tools/fallback-phase1-probe.py` (committed, never run).
Running it is an operator step: either a Bash permission rule for it or the operator runs it.
