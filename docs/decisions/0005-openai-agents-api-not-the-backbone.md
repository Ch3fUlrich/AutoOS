# 0005 — The OpenAI Agents API is not the orchestration backbone; OpenAI joins as one reviewer pool

Status: accepted · 2026-09-18 · evidence: [the survey](../research/2026-09-18-openai-agents-api.md)

## Context

The agent hierarchy that AutoOS is taking over from `agent-skills` has three levels:
- **L1 and L2 orchestrators:** Claude Code sessions that hold the vision and the phase plans.
- **L3 executors:** cheap, fast, and able to reach Claude, Gemini (`agy`), DeepSeek, Muse
  Spark, local Ollama and OpenRouter.

They work as a swarm. Every session gets its own git worktree, merges are gated by guards, and
`resources` locks keep two lanes off the same files.

OpenAI's Agents API went into public beta on 2026-09-10. It is a hosted Codex harness with
durable sessions, compaction, subagents, MCP and an optional sandbox. It charges no platform
fee; you pay for tokens, tools and container time.

## Decision

- **Don't adopt the Agents API as the orchestration layer.**
  - It runs OpenAI models only: there is no provider or `base_url` field. So it can't run
    L1/L2 or most L3 pools.
  - Its subagents share one filesystem and one model: there is no equivalent of worktree
    isolation, guard-gated merges or resource locks.
  - It is not ZDR-eligible and keeps data in the US only.
  - Its self-hosted executor is an alpha build, and Windows support isn't documented.
- **Add OpenAI as one L3/reviewer pool through the `codex` CLI.** Model it like every other
  pool: an `openai` entry in CAO's pool map pointing at the existing `codex` provider type, plus
  `family: "openai"`. That gives the pairing rule a reviewer that is neither Anthropic nor
  Google, and needs no new infrastructure.
- **Don't adopt the Agents SDK (with LiteLLM) now.** It would re-implement what Claude Code
  plus CAO already do, and its non-OpenAI adapters are "best-effort, beta".

## Consequences

- Cross-family review becomes possible for anyone with a ChatGPT plan or an OpenAI key.
  Without one, nothing changes.
- One idea is borrowed rather than the product: an output-guardrail-style check that a DONE
  note's artefacts exist before the guards run.
- Revisit if the Agents API reaches GA with bring-your-own-model, becomes ZDR-eligible, gets a
  stable Windows executor, or gains per-subagent model and environment selection. Also revisit
  if a reboot-proof orchestrator becomes a hard requirement. The full trigger list is in §6 of
  the survey.
