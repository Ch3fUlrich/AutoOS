# Placement decision (operator input needed)

## Recommendation: `.claude/skills/<name>/`
- It is the only tracked skill location in this repo
  (`.claude/skills/autoos-install/SKILL.md`).
- `git subtree add --prefix=.claude/skills` cannot take a source *subdir*
  directly. Procedure: in the agent-skills clone
  `git subtree split -P skills -b skills-split`, then here
  `git subtree add -P .claude/skills <agent-skills-path> skills-split --squash`
  (future pulls repeat split + `subtree pull`). `autoos-install` stays
  alongside; subtree must not touch it.
- Exclude from the subtree: `qa-swarm`, `review-triage`, `babysit-prs`
  (unlicensed — native rewrites from `docs/plans/rewrites/` land instead).
  Practical form: split, then `git rm -r` the three dirs on the split branch
  before adding... but that breaks future pulls. Alternative: subtree-add the
  full split, then replace those three dirs with rewrites in a follow-up
  commit (history shows provenance; HEAD is clean). **Recommended: full add,
  then replace.**
- `docs/unattended-orchestration.md` moves into
  `.claude/skills/unattended-orchestration/`; rewrite the 7 inbound links
  (README:104, opencode.jsonc:58, openhands-runbook:18, docs/README:24,
  handoff:8/245/281). tasks.md needs no change (no link found).

## Open question for the RW lane
Opencode skill loading from `.claude/skills/` is assumed (matches the one
tracked skill) but not proven — the lane must do a real skill-load check from
opencode and report. If opencode wants a different dir, the whole tree moves
in one commit before any other lane merges.

## Operator: confirm prefix (or propose another) by replying; SUB lane stays
blocked until this file records the answer.
**Answer (2026-09-23, evidence-backed): `.agents/skills/` as the single
content home, Claude Code served via symlinks in `.claude/skills/`.**
Evidence: Claude Code reads project skills ONLY from `.claude/skills/` but
explicitly supports symlinked skill folders (deduped); OpenCode reads
`.opencode/skills/` natively plus compat `.claude/skills` AND
`.agents/skills` (agents outranks claude in its precedence order); Codex,
Cursor and VS Code converge on `.agents/skills/`; OpenHands takes any dir
via `load_skills_from_dir` (our installer configures it). So `.agents/skills/`
is native-or-compat for every agent except Claude Code, which is covered by
installer-created symlinks (idempotent, guarded — never committed links,
so fresh checkouts without symlink rights stay intact).
`.claude/skills/autoos-install` stays a real dir, untouched.
`SYNC.md` lives at `.agents/SYNC.md` (NOT inside `skills/`, where a root
`.md` would register as a flat skill named SYNC).
