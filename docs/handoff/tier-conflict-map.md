# Tier-orchestration conflict map (T1 recon)

Date: 2026-09-21. Base: `main` 059c495. Tip: `origin/tier-orchestration-2026-09-20`
`4b8441a` (11 unique commits, merge-base `a5d9a34`, ~104 main commits since).
Method: `git merge-tree --write-tree --name-only main origin/tier-...`
(read-only, no worktree writes). Use the `origin/` ref — the stale local
branch of the same name was deleted (unrelated history, archived as tag
`archive/tier-stub-pr9`).

## Content conflicts (12)

| File | Class | Resolution rule for T2 |
|---|---|---|
| `.gitignore` | mechanical | union, keep main's runtime/output entries |
| `README.md` | judgement | keep main structure, fold tier's quick-nav + API-keys row |
| `catalog/linux.json` | judgement | main wins; replay tier additions via `catalog/agent-harness.json` generator |
| `catalog/macos.json` | judgement | same as linux |
| `catalog/windows.json` | judgement | same as linux |
| `docs/README.md` | mechanical | union of index entries |
| `lib/linux/install.sh` | judgement | main wins; replay tier routing hooks via `--config-dir` design (ADR 0006) |
| `lib/windows/AutoOS.Install.psm1` | judgement | same as install.sh |
| `lib/windows/AutoOS.Serve.psm1` | judgement | resolve per-hunk; keep main's handler split |
| `tests/check-links.py` | mechanical | union; keep link-guard strict |
| `tests/run-tests.ps1` | judgement | main wins; replay tier test additions file-by-file, never drop assertions |
| `tests/run-tests.sh` | judgement | same as ps1 |

## Clean auto-merges (semantic review only, no conflict)

- `lib/linux/serve.py`, `web/index.html`, `docs/troubleshooting.md` — merge
  cleanly; reviewer checks they need nothing from tier routing.
- Everything under `configuration/**`, root `opencode.jsonc`, remaining docs —
  purely additive.

## Binding design decisions (from plan reviews, reiterated for T2)

1. `catalog/agent-harness.json` + `lib/agent_harness.py` stay the single source
   for AGENT behavior. `configuration/` stays standalone for service runtime;
   the generator grows a `--config-dir` reader.
2. Tier's tracked root `opencode.jsonc` does NOT survive as a rival source.
3. Precedence rules go in `docs/decisions/0006-*.md`.
4. Model-review items ride along: dedicated non-writer-family reviewer profile,
   tier3 legs ordered for cross-family review, litellm tier2 gemini-first.
5. `skills/` is untouched (Phase 2). No secrets, no binaries, no unlicensed text.
