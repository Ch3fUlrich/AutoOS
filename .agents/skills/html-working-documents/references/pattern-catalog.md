# HTML Working Documents Pattern Catalog

This catalog distills the project convention from Thariq Shihipar's "Using Claude Code: The Unreasonable Effectiveness of HTML" and the companion example gallery:

- Article mirror: https://www.techtwitter.com/articles/using-claude-code-the-unreasonable-effectiveness-of-html
- Example gallery: https://thariqs.github.io/html-effectiveness/

## Core Thesis

HTML is useful for agent work because it can carry prose, tables, code, diagrams, mockups, color, layout, and small interactions in one portable file. The point is not to replace every note with HTML. The point is to make long agent outputs easier to read, compare, share, and feed back into later agent work.

## What The Example Set Shows

The companion gallery uses twenty self-contained `.html` files across nine categories:

| Category | Example jobs | Effective structure |
| --- | --- | --- |
| Exploration and planning | Code approach comparison, visual direction grid, implementation plan | Put alternatives beside each other, expose tradeoffs, end with a recommendation or handoff plan. |
| Code review and understanding | Annotated PR, PR writeup, module map | Preserve code shape with annotated diffs, file maps, severity tags, review focus, and test/rollout context. |
| Design | Design system reference, component variant matrix | Render tokens, states, density, and components live so the reviewer reacts to the thing itself. |
| Prototyping | Animation sandbox, clickable flow | Make the interaction feelable with small controls and copyable implementation values. |
| Diagrams | SVG figure sheet, annotated flowchart | Use inline SVG or structured HTML to show paths, timings, failure branches, and legends. |
| Decks | Arrow-key slide deck | Use sections and tiny JS for a browser-native meeting deck. |
| Research and learning | Feature explainer, concept explainer | Use TL;DR, stepwise paths, collapsible details, code snippets, comparison tables, and glossaries. |
| Reports | Status report, incident timeline | Turn recurring updates into metric cards, timelines, tables, source notes, and action items. |
| Custom editors | Ticket board, flag editor, prompt tuner | Build a one-off UI for hard-to-describe choices, then export the changed state back to text. |

## Reusable Sections

Choose the sections that fit the artifact:

- Premise: what problem this artifact helps decide.
- Source context: files read, commands run, links checked, assumptions.
- Navigation: sticky or top-of-page links for longer pages.
- Summary: TL;DR, verdict, or recommendation near the top.
- Main canvas: comparison grid, mockup, flowchart, timeline, diff, chart, or editor.
- Evidence: relevant code snippets, data rows, test results, screenshots, or citations.
- Risk: severity, likelihood, mitigation, owner, and timing.
- Decision support: tradeoff table, open questions, or "where to focus review".
- Export: copy markdown, copy JSON, copy diff, copy prompt, or download SVG when the page is an editor.
- Handoff: next slices, implementation order, validation plan, and unresolved decisions.

## Quality Bar

Before delivering an HTML working document, check:

- It opens directly from disk or a simple static server.
- It is useful without the chat transcript.
- Its key idea is visible in the first viewport.
- It has a clear reading order and does not require horizontal scrolling for prose.
- Any interaction has labels and a reset or export path when state matters.
- Code snippets are short and chosen for the decision, not dumped wholesale.
- The final section tells a future agent or human what to do next.

## Anti-Patterns

Avoid these:

- Making HTML for a short answer that would be clearer as five markdown bullets.
- Producing a decorative page with no decision, recommendation, or evidence.
- Depending on a CDN or app build unless the user asked for a real app.
- Hiding implementation details in visuals without filenames, snippets, or assumptions.
- Creating noisy HTML diffs for documentation that must be frequently hand-reviewed.
- Omitting export from one-off editors, which traps the user's work in the browser.

