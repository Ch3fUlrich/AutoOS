# DONE-W2 — nesting depth + reviewer toolset (worker + L1 amendment)

Worker (tier2-worker, omniroute/tier2) landed on branch merge/w2-nesting-depth:
- `opencode.jsonc`: `"subagent_depth": 2` (tier1→tier2→tier3 nesting; default
  1 forbids subagents spawning subagents).
- `tests/run-tests.ps1`: `subagent depth config` test + tier-fence assertions.

L1 amendment (worker exited without committing; refusal evidence forced it):
- tier3-reviewer as shipped (subagent-deny only) and with worker's bash-deny
  has NO tools — two live reviews refused ("no access to the necessary
  tools"). Gateway probing proved 6/8 tier3 legs support function calls, so
  the stanza, not the models, was at fault.
- Reviewer stanza corrected: allow read/grep/glob/bash, deny
  edit/write/subagent. Proven live: `--agent tier3-reviewer` on
  tier3-clean answers `ack`.
- Test updated to assert the 7-rule stanza.

Verify: ps1 filter on `tier depth` + `subagent depth config` green (run at
landing). Remaining: sh-suite mirror test for the stanza (ps1-only for now),
W2's own tier3 review, merge to main.
