# HTML Working Documents — Starter

Drop this README into any repository as a reminder to use the HTML working
documents skill. The full skill lives at
`skills/html-working-documents/SKILL.md` in the `agent-skills` repo.

## What It Does

Teaches agents to produce self-contained `.html` artifacts instead of long
markdown when the output benefits from layout, comparison, diagrams, mockups,
tables, color, small interactions, or durable handoff.

## How To Use

Reference the skill from your project's agent instruction file (AGENTS.md,
CLAUDE.md, GEMINI.md, or equivalent):

```markdown
## HTML Working Documents

For long planning, research, review, report, diagram, prototype, and handoff
work, follow the skill at `skills/html-working-documents/SKILL.md`.
```

## Convention

- Default browser artifacts to `webpage/`.
- Keep HTML self-contained: inline CSS, tiny inline JS only when useful,
  no build step, no remote dependencies.
- End substantial artifacts with a recommendation, implementation slice,
  risk table, open questions, or next action.
