# Plan v2 — AI Agent Model Routing Overhaul (2026-09-23, supersedes DRAFT)

Reviewed by spark + gemini + deepseek (all REQUEST_CHANGES, merged). Updated for
commits 2d0a790 / e176ab4 / 2e1fba7 / 27cb4d1 which landed overnight.

## 1. Already landed — do not re-propose
- Breaker `failureThreshold` 12→2 + zen-first + fast-skip (`apply.*`, both platforms).
- `requestQueue.maxWaitMs` 15000→180000 (reasoning legs need it; under `budgetMs` 300s).
- `opencode.jsonc` repaired; both suites assert it parses.
- `tools/audit-router.py` (live + `--offline` in CI) — drift gate exists.
- OpenHands `openrouter-*` gateway-aware key resolution (installers + `sync-openhands-profiles.py`).
- OpenHands schema-6/enabled-key clamp on start.
- Handoff task 8 specifies the skills migration (git subtree, full folder, move
  `docs/unattended-orchestration.md` into the skill, licence check, link fixes).

## 2. Implemented this session
- **LiteLLM META legs deleted** (`configuration/litellm/config.yaml` tier1 +
  tier1-paid): meta-direct is deactivated upstream (502) + insufficient funds,
  and an unset `META_API_KEY` fails startup validation for the whole group.
  Verified: `sync-router-tiers.py --check` OK, `audit-router.py --offline` no drift.
- **Open question (needs live gateway, operator or night lane):** the omniroute
  `META_API_KEY` SchemaError on the spark combo. Gateway combos carry no
  muse-code legs, so the failure is in provider-auth resolution, not leg order.
  Next probe: `omniroute providers test muse-code`, one direct `:20128` spark
  chat, read the exact failing provider from the error — then unregister or
  repair that provider in `apply.*`. Do NOT delete the `meta→muse-code` mapping
  or its tests (run-tests.sh:5648, run-tests.ps1:4285) until the probe names it.

## 3. Done this session (verified, targeted tests only)
- **LiteLLM META legs deleted** (`configuration/litellm/config.yaml` tier1 +
  tier1-paid). Verified: `sync-router-tiers.py --check` OK,
  `audit-router.py --offline` no drift.
- **Comma-OR test filters** (`it()`, `Test-Case` + usage docs). Verified:
  zero-match clean both suites; OR-equivalence 3==3 (sh), 7==7 (ps1);
  shellcheck clean on `run-tests.sh`.
- **OpenRouter-first gateway cleanup (operator decision 2026-09-23, META probe
  dropped):** `meta→muse-code` mapping removed from `apply.ps1`/`apply.sh`
  (502 upstream, zero combo legs reference it); absence asserted in both
  suites (sh test renamed `...stays openrouter-first`, ps1
  `...stay openrouter-first`). Verified: sh `--filter "Cloudflare UA"` 1/1,
  ps1 `-Filter openrouter-first` 7/7, shellcheck clean on `apply.sh`.
  Kept deliberately: `mirror-litellm-env.py` meta→META_API_KEY + opencode
  `meta` direct provider (own key, own billing, no gateway involvement).
- **W1 test-sharding inventory (deepseek, delivered)** + **W2
  skills-migration inventory (laguna-free, delivered)** — see §3previous.
- **Test policy (operator decision):** touched parts only — run the
  `--filter`/`-Filter` groups covering edited files, never full suites,
  until a FINAL guardsOnly lane.
- **W1 test-sharding inventory (deepseek, delivered):** sh 367 `it` tests
  (L1 usb/cache 101, L2 catalog/image 36, L3 ai/router 78, L4 core/web 152);
  ps1 296 `Test-Case` (W1 usb 64, W2 router/ai 46, W3 catalog/serve 46,
  W4 core ~100). Both suites already had `--filter`/`-Filter` (sh
  case-sensitive, ps1 case-insensitive). Slowest: shellcheck/PSScriptAnalyzer
  self-lints, wheelhouse/cache builds, double dry-runs, child-process setup.ps1
  USB spawns. Merge candidates: serena-exclusion fixtures, image_resolve
  scenarios, usb guard/plan/execute tables, MCP pins, agent-harness generator,
  web/index.html assertions, 2–3× linter runs (suite + ci.yml +
  check-merge-guards). Suites are process-isolated (mktemp scratch, port 0)
  → safe in parallel from separate worktrees; no shard in main checkout
  during merges (guardsOnly-lane rule).
- **W2 skills-migration inventory (laguna-free, delivered):** source has all 14
  skills + SYNC.md, no licence files (qa-swarm/review-triage/babysit-prs flag
  stands — raise to operator). AutoOS tracks only
  `.claude/skills/autoos-install`; no half-copied remnants on disk. 7 inbound
  links to `docs/unattended-orchestration.md` (README:104, opencode.jsonc:58,
  openhands-runbook:18, docs/README:24, handoff:8/245/281); tasks.md has none
  (handoff §8.283 lists it — verify). Instruction gaps: no skill router, no
  MCP/memory model, no scratch-isolation rule, no Claude scope-trap, no
  GEMINI.md in AutoOS.

## 4. Queued implementation (in order)
1. **Test parallelization** (OR-filter landed; remainder): document shard
   recipe in `docs/testing.md` (L1..L4 / W1..W4 tokens from §3 + one command
   per shard for separate worktrees); merge redundant coverage per W1 list
   (serena fixtures, image_resolve, usb tables, MCP pins, harness generator,
   web assertions, single linter authority); add shard-completeness guard so
   a filter matching zero tests fails loudly (sh:187 pattern already exists —
   extend to shards). Constraints: no framework (AGENTS.md §5), WSL2-green,
   skip-is-not-pass.
2. **Skills migration** (per handoff task 8 + W2 inventory + W3 rewrite
   drafts LANDED in `docs/plans/rewrites/` — qa-swarm 109 lines,
   review-triage 103, babysit-prs 104; normative, config-referenced, no
   copied upstream text): `git subtree` full `agent-skills/skills` →
   prefix per placement decision; move `docs/unattended-orchestration.md`
   into the skill + rewrite the 7 inbound links (verify tasks.md); replace
   the 3 unlicensed dirs with the native drafts; verify `check-links.py` +
   touched filters + real opencode skill-load. Needs operator: prefix
   answer + `git commit` (refused in-session 2026-09-23) + runner launch.
   (Correction: prior briefs cited “§4.2” — the skills item is §4 item 2.)
3. **Instruction-file merge:** agent-skills AGENTS.md (skill router, MCP-mandatory,
   memory model, scratch isolation) → AutoOS AGENTS.md as new §8 (pointers, not
   copies — one home per fact); agent-skills CLAUDE.md trap detail → AutoOS
   CLAUDE.md; new AutoOS GEMINI.md from agent-skills GEMINI.md (repo has none).
   Keep AutoOS hard rules §§1–7 untouched and canonical.
4. **Remaining router items:** single `fallbackOrder` constant (cheapinference
   position contradiction); Claude never-API lint test; `catalog/providers.json`
   consolidating the 5 name maps; `sync-ide-models.py` + static effort aliases;
   effort-clamp rule for heterogeneous tiers; BYOK verdict (default manual
   checklist); `docs/models-proposed.md` tables + `docs/routing.md` mermaid.

## 5. Definition of done (AGENTS.md §7)
Both suites pass · shellcheck/PSScriptAnalyzer clean · `--dry-run` reviewed ·
double-run `skipped` · no secrets/binaries/usernames · README/docs updated.
