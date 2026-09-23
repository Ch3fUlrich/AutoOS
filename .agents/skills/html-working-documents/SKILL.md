---
name: html-working-documents
description: Create self-contained HTML artifacts for AI-agent planning, exploration, project handoff, code review, design comparison, prototyping, research explainers, reports, diagrams, and one-off editing interfaces. Use when a task would otherwise produce a long markdown plan, spec, report, review, or research note, especially when spatial comparison, mockups, tables, color, diagrams, interaction, export buttons, or browser-readable sharing would improve understanding. Do not use for tiny linear notes, normal source-code comments, terse README updates, or documentation that must remain plain markdown.
---

# HTML Working Documents

Use this skill to turn agent work into browser-readable working documents that are easier to scan, compare, verify, and hand off than long markdown. Treat HTML as a decision surface, not decoration.

## Decision Rule

Create an HTML artifact when at least one is true:

- The answer is more than roughly 100 lines of markdown.
- The user needs to compare multiple approaches, designs, components, risks, or timelines.
- A diagram, mockup, code path, annotated diff, chart, table, or state machine would carry meaning better than prose.
- The user needs to tune values, reorder items, toggle options, preview output, or export choices back to markdown, JSON, a prompt, or code.
- The document is likely to be shared with a reviewer, implementer, stakeholder, or future agent.

Prefer markdown when the output is short, linear, mainly prose, or intended as maintainable repository documentation.

## Workflow

1. Clarify the artifact job: exploration, plan, review, explainer, report, prototype, diagram, deck, or editor.
2. Gather enough source context from the repo, git history, tests, docs, issues, web, or existing artifacts before drawing conclusions.
3. Create one self-contained `.html` file with inline CSS and only small inline JavaScript when needed.
4. Put the artifact where the repo expects browser artifacts. Default to `webpage/` unless the repository's `AGENTS.md` says otherwise or the user asks for another location.
5. Make the page immediately useful: include a short premise, navigable sections, the important evidence, and a concrete recommendation, decision, open-question list, export, or next-step section.
6. Verify the file opens locally. For interactive or visual artifacts, use a browser/screenshot check when practical.

## Artifact Shapes

Use these common shapes:

- Exploration: side-by-side approaches with code snippets, tradeoffs, suitability notes, and one recommendation.
- Implementation plan: milestones, architecture/data flow, mockups, key code, risks, open questions, and handoff notes.
- Code review: annotated diff, severity labels, file jump links, findings first, and concrete next steps.
- PR writeup: motivation, before/after behavior, file-by-file tour, review focus, test plan, and rollout.
- Code understanding: request path or call graph, key files, expandable source snippets, trust boundaries, and gotchas.
- Design reference: tokens, swatches, type scale, spacing, components, states, and copyable values.
- Prototype: clickable interaction or animation with controls for timing, easing, density, color, or other parameters.
- Diagram: inline SVG or HTML/CSS diagram with labels, paths, legends, and clickable details if helpful.
- Report: metrics, highlights, timeline, table, carryover, sources, and generated-at context.
- Editor: purpose-built controls for one data set, with reset and copy/export output.

Read `references/pattern-catalog.md` when choosing between artifact shapes or when you need examples of what each shape should contain.

## HTML Requirements

Every artifact should be:

- Self-contained: no build step, no remote framework dependency, no missing assets.
- Readable in a browser from disk.
- Responsive enough for desktop and phone widths.
- Skimmable: strong headings, local navigation for longer pages, tables for structured data, and compact summaries.
- Evidence-backed: name files, commands, commits, sources, assumptions, and confidence where relevant.
- Actionable: end with a recommendation, risk table, checklist, export, open questions, or next implementation slice.
- Accessible enough for working use: semantic headings, buttons with clear labels, contrast that holds up, and no text overlap.

## Interaction Guidance

Add JavaScript only when it tightens the human loop:

- Tabs, filters, toggles, accordions, sliders, live previews, drag/drop, or copy/export buttons are good fits.
- Prefer tiny native browser APIs over dependencies.
- Keep state local to the document unless the user explicitly asks for persistence.
- For editors, always provide an export path such as "Copy markdown", "Copy JSON", "Copy diff", or "Copy prompt".

## Visual Guidance

Make the information visible, not just pretty:

- Use color to encode status, ownership, severity, risk, or selected options.
- Use inline SVG for diagrams and figures that must be portable.
- Use tables for comparable facts and cards only for genuinely repeated items.
- Do not bury the point under decorative layout. The artifact should help the user decide faster.

## Repository Convention

- Use `webpage/` for standalone HTML working documents unless the repository sets a different convention.
- Keep filenames descriptive and stable, for example `implementation-plan.html`, `approach-comparison.html`, or `review-summary.html`.
- Preserve existing HTML artifacts unless the user asks to replace them.
- If an HTML artifact becomes the project plan, make it concrete enough for a fresh agent session to implement from it.

