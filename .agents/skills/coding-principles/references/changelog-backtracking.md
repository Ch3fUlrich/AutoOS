# Backtracking via Changelogs and ADRs

The goal: any future agent (or human) can answer "why is the code like this?" and
"how do I safely undo it?" from the repository alone, without the original
author.

Two complementary records:

1. **`CHANGELOG.md`** — *what* changed, in human terms, over time.
2. **`docs/decisions/`** (ADRs) — *why* a non-obvious choice was made.

Plus the discipline that makes both usable: **small, frequently committed,
independently revertible changes**.

## CHANGELOG.md

Follow [Keep a Changelog](https://keepachangelog.com/) conventions. Newest entry
on top. Group by `Added / Changed / Fixed / Removed / Deprecated / Security`.

```markdown
# Changelog

## [Unreleased]

### Removed
- The Mem0 fallback, entirely — Omnigraph is the only memory layer. The escape hatch
  was never exercised but kept leaking back into docs as a live option. See
  docs/decisions/0003-remove-mem0-fallback.md.

### Added
- `coding-principles` and `structured-memory` skills with starters.
```

Rules:
- One bullet per meaningful change; reference the ADR or commit where relevant.
- Write it in the same change that makes the code change — not later.
- User-facing language: describe the effect, not the diff.

## Architecture Decision Records (ADRs)

For any decision that a reasonable engineer might later question — a dependency
choice, a tradeoff, a rejected alternative — write an ADR. Keep them short.

Path: `docs/decisions/NNNN-kebab-title.md` (zero-padded, monotonic).

```markdown
# 0001. Omnigraph over Mem0 for cross-project memory

- **Status:** Accepted (2026-07-09)
- **Context:** We need structured, cross-agent, cross-project memory. Mem0
  auto-extracts unstructured vector blobs; we want typed, queryable, reviewable
  memory so rules and decisions stay integrated.
- **Decision:** Adopt Omnigraph (typed nodes/edges + graph/vector/full-text
  retrieval) as the default. Replace Mem0's auto-extraction with an explicit
  structured-memory protocol.
- **Consequences:** Heavier infra (omnigraph-server + MinIO). Memory now depends
  on an agent-discipline protocol (treated as a feature). Mem0 kept as fallback.
- **Switch-back criteria:** If operating Omnigraph proves unsustainable, re-enable
  the `mem0-fallback` compose profile and layer the structured protocol on top.
```

**Amend, don't rewrite.** When reality moves on, an ADR is a record of what was decided
*then* — so leave the text and add a dated note pointing at the ADR that changed it. The
real 0001 above later had its fallback clause voided; the fix was a header amendment plus
a `> Superseded by ADR 0003` note above that section — **not** a silent edit. Write the
new ADR for the new decision (0003 here), and keep the old one findable:

```markdown
# 0001. Omnigraph over Mem0 for cross-project memory

- **Status:** Accepted (2026-07-09)
- **Amended (2026-07-16):** the Mem0 fallback below was removed — see
  docs/decisions/0003-remove-mem0-fallback.md. The decision to adopt Omnigraph stands;
  only the escape hatch is gone.
```

The same rule applies to memory: supersede an accepted `Decision` with a new node plus a
`Supersedes` edge rather than overwriting its rationale (`skills/structured-memory/SKILL.md`).

Statuses: `Proposed → Accepted → Superseded by NNNN → Deprecated`. Never edit the
decision of an accepted ADR — supersede it with a new one and link both ways.

## Commit discipline (what makes reverts possible)

- One logical change per commit; each commit leaves the tree working.
- Message states **intent**: `feat: default memory to Omnigraph` not `update files`.
- Prefer many small commits over one large one — a bad change should be a single
  `git revert`, not archaeology.
- Reference the ADR/changelog entry in the body when the change is a decision.
