# Coding Principles — Starter

A thin pointer to the full [`coding-principles`](../../skills/coding-principles/SKILL.md)
skill. Drop the block below into your repository's agent instruction file so
every session applies the same engineering baseline.

## Install

Add to `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or your agent's equivalent
instruction file:

```markdown
## Coding Principles

For any implementation, refactor, or bugfix, follow the skill at
`skills/coding-principles/SKILL.md`: DRY / single source of truth,
test-driven development, single responsibility, document the *why*,
backtracking via `CHANGELOG.md` + `docs/decisions/` ADRs, and MCP-first
code navigation. Prefer a more specific skill (e.g. TDD, systematic
debugging) when one applies — this is the always-on floor.
```

Keep the pointer short. The `SKILL.md` carries the full workflow and the
`references/changelog-backtracking.md` detail — do not copy their contents here
(that would violate Principle 1, single source of truth).
