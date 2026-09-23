# Agent Compatibility Notes

Each coding agent has its own format for project-level instructions. This page
collects the official documentation links for reference when you need to add
instructions for a specific agent.

## Agent Instruction Formats

| Agent/tool | Instruction file | Documentation |
| --- | --- | --- |
| OpenAI Codex / AGENTS.md-aware tools | `AGENTS.md` | Used by Codex, Cursor, Copilot, and others |
| Claude Code | `CLAUDE.md` or `.claude/CLAUDE.md` | https://code.claude.com/docs/en/memory |
| Cursor | `.cursor/rules/*.mdc` | https://docs.cursor.com/en/context/rules |
| VS Code / GitHub Copilot | `.github/copilot-instructions.md` | https://code.visualstudio.com/docs/copilot/customization/custom-instructions |
| Roo Code | `.roo/rules/*.md` | https://docs.roocode.com/ |
| Cline | `.clinerules/*.md` | https://docs.cline.bot/customization/cline-rules |
| Continue | `.continue/rules/*.md` | https://docs.continue.dev/customize/rules |
| Windsurf | `.windsurf/rules/*.md` or `.windsurfrules` | — |
| Gemini CLI | `GEMINI.md` | https://google-gemini.github.io/gemini-cli/docs/cli/gemini-md.html |

## Using With Skills

To reference a skill from any of these instruction files, add a short pointer:

```markdown
For long planning, research, review, and handoff work, follow the skill at
`skills/html-working-documents/SKILL.md`.
```

Keep agent-specific instruction files short. The skill itself carries the full
workflow.

## Adapters in this repository

An *instruction file* tells an agent what to do; an *adapter* makes the agent's own machinery obey
it. This repository ships both, and adapters are always derived from the skill — never a second copy
of it.

| Adapter | Agent | Status |
| --- | --- | --- |
| `skills/<name>/agents/openai.yaml` | OpenAI Codex | live — display name, short description, default prompt |
| `AGENTS.md` · `CLAUDE.md` · `GEMINI.md` | all · Claude Code · Gemini/Antigravity | live — thin pointers to `skills/repository-index/SKILL.md` |
| `.claude/agents/`, `.claude/workflows/`, `.claude/hooks/` | Claude Code native `Agent`/`Workflow` | **planned** — generated from `agent_orchestration.config.yaml`; see [ADR 0004](decisions/0004-executable-orchestration-policy.md) |

The Claude adapter is generated rather than hand-written for a specific reason: model tiers and
review lenses would otherwise exist in both the YAML and the adapter, and two hand-maintained copies
of the same list drift. Generation with a `--check` drift gate keeps one source.
