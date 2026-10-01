# Lane Review — Wave 1 (2026-09-30)

**Review branch:** `L1-backlog/ws-review-20260930`
**Base:** d08f7f2 (main == origin/main at branch cut)
**Worktree:** `AutoOS-worktrees/AutoOS-ws-review`
**Review date:** 2026-09-30
**Method:** 2 independent t3-reviewer leaves per milestone, run sequentially
  (rate-limit hygiene), from different model families. Each leaf received the
  exact commit SHA, the invariants to check, and the return contract
  (verdict APPROVE/REJECT + findings with file:line + confidence).

**Reviewer model families used:**

| Family | Model ID |
|---|---|
| DeepSeek | `omniroute/deepseek-v4.1-flash` |
| Vertex / Gemini | `omniroute/vertex-3.8-flash` |

Both families were pre-verified working before reviewer spawns.

**Admission / rate-limit events:** 0 — no `chat_admission_busy` or
`Rate limit exceeded` errors encountered during any reviewer spawn.
**Refusals:** none.

---

## Milestone 1: Combos Lane (commit a5bcb69)

**Writer lane:** L1-alpha (combos)
**Branch reviewed:** `L1-backlog/ws-combos-20260930` (read via `git show a5bcb69` only)
**Primary file:** `configuration/omniroute/combos.json`

### Invariants checked

1. vertex is leg 2 of exactly `gemini-3.8-flash` (no other model gets vertex)
2. Only the 5 expected combos changed (gemini-3.8-flash, opus-4-6, t2-orchestrator, t2-worker-clean, t3-driver-clean)
3. No free-only / clean model pollution (free-only combos not touched)
4. `catalog/ai-registry.json` untouched
5. JSON is valid and parseable
6. `apply.*` changes are comment-only (no semantic edit)

### Reviewer 1 — DeepSeek family (`omniroute/deepseek-v4.1-flash`)

**Verdict:** **APPROVE**
**Confidence:** HIGH

| Invariant | Result |
|---|---|
| 1. vertex is leg 2 of exactly gemini-3.8-flash | ✅ PASS |
| 2. Only 5 combos changed | ✅ PASS |
| 3. No free-only/clean pollution | ✅ PASS |
| 4. catalog/ai-registry.json untouched | ✅ PASS |
| 5. JSON valid | ✅ PASS |
| 6. apply.* comment-only | ✅ PASS |

**Findings (non-blocking):**

- **R-1a** (risk): `tests/test_registry_render.py` has exactly 5 failures on the
  combos branch (155 passed, 5 failed): `combos.gemini-3.8-flash`,
  `combos.opus-4-6`, `combos.t2-orchestrator`, `combos.t2-worker-clean`,
  `combos.t3-driver-clean` — all reporting "differs". These are an expected
  cross-lane dependency on L1-beta updating `catalog/ai-registry.json`
  `context_advertised` values (currently stale: gemini-3.8-flash=131072 vs
  live 1048576, opus-4-6-thinking=200000 vs live 1048576). No test files were
  changed in commit a5bcb69 (diff vs d08f7f2 is empty for `tests/`).
- **R-1b** (risk): An extra handoff doc file was committed outside the
  combos scope — minor scope creep, not a code defect.
- **R-1c** (risk): Prose claims about "gateway computed_context_length" in the
  commit message are supported by the combo store but not independently
  verifiable via `/v1/models` for all model IDs (see R-2b below).

### Reviewer 2 — Vertex / Gemini family (`omniroute/vertex-3.8-flash`)

**Verdict:** **APPROVE**
**Confidence:** HIGH

| Check | Result |
|---|---|
| Gateway catalog match (live `/v1/models`, 5296 models) | ✅ PASS — `gemini/gemini-3.8-flash`=1048576, `vertex/gemini-3.8-flash`=1048576, `deepseek/deepseek-flash`=1048576 confirmed live |
| `docs/models.md` claims match `combos.json` | ✅ PASS |
| No secret in tracked files | ✅ PASS |

**Findings (non-blocking):**

- **R-2a** (risk): `antigravity/claude-opus-4-6-thinking` does NOT exist in the
  live gateway `/v1/models` catalog (0 `antigravity/*` models returned; 0
  `agy/*` models returned). The combo store retains
  `computed_context_length: 1048576` for `opus-4-6` and `t2-orchestrator` legs
  that reference this model. The commit's claim of "gateway
  computed_context_length 1048576" is supported by the combo store's computed
  value but cannot be directly verified via `/v1/models` for that exact model
  ID. The bare `opus-4-6` token does resolve to 1048576 in the live catalog.
- **R-2b** (info): The 5 `test_registry_render.py` failures match the expected
  cross-lane dependency on L1-beta (same as R-1a). Not a combos-lane defect.

### Milestone 1 summary

Both reviewers APPROVED with HIGH confidence. All 6 invariants passed. The 5
test failures are a known cross-lane dependency on L1-beta updating
`catalog/ai-registry.json` `context_advertised` values — not a combos-lane
defect. Recommend L1-alpha approve pending L1-beta's registry update.

---

## Milestone 2: P0 / Admission Lane (commit d88ed3b)

**Writer lane:** L1-beta (P0 / admission)
**Branch reviewed:** `L1-backlog/ws-p0-20260930` (read via `git show d88ed3b` only)
**Primary files:** `configuration/start-stack.ps1`, `docs/handoff/2026-09-30-laneP0-admission.md`

### Invariants checked

1. Idempotency: safe to run twice (guard checks, no blind overwrites)
2. Read-modify-write for PATH/config (never wholesale replace)
3. No hardcoded username in scripts (use `$env:APPDATA` / env-relative paths)
4. No secret in tracked files
5. Knob is set before gateway start (ordering)
6. Shim resolves `.cmd` explicitly (not bare `omniroute`)
7. Missing shim fails loudly (not silently)
8. `apply.ps1` shim fix deferral is documented (not silently omitted)

### Reviewer 1 — DeepSeek family (`omniroute/deepseek-v4.1-flash`)

**Verdict:** **REJECT**
**Confidence:** HIGH

| Invariant | Result |
|---|---|
| 1. Idempotency | ✅ PASS |
| 2. Read-modify-write | ✅ PASS |
| 3. No hardcoded username in scripts | ✅ PASS (scripts use `$env:APPDATA`) |
| 4. No secret | ✅ PASS |
| 5. Knob set before gateway start | ✅ PASS |

**FAIL:**

- **P0-R1-FAIL** (blocking): `docs/handoff/2026-09-30-laneP0-admission.md:4`
  contains the hardcoded username path
  `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute`.
  This violates **AGENTS.md Hard Rule 1** ("Never commit a secret... no
  usernames... not in a comment, not in an example"). The username `<user>` is
  a real system username embedded in a tracked file. This is in a DOC file,
  not in the launcher scripts — the scripts themselves correctly use
  `$env:APPDATA` (environment-relative).

**Findings (non-blocking risks):**

- **P0-R1a** (risk): `apply.ps1:97` still uses bare `omniroute` — the shim
  fix in `start-stack.ps1` is not mirrored in `apply.ps1`. This is documented
  as a deferred fix, but the latent failure remains.
- **P0-R1b** (risk): The PowerShell guard added in `start-stack.ps1` is
  per-process — it does not persist across sessions. A second invocation in a
  new process is not protected by the guard.
- **P0-R1c** (risk): The DONE note was force-added to the git-ignored
  `logs/` tree. This is consistent with repo convention but means the DONE
  note is not version-controlled alongside the code.

### Reviewer 2 — Vertex / Gemini family (`omniroute/vertex-3.8-flash`)

**Verdict:** **REJECT**
**Confidence:** HIGH

| Invariant | Result |
|---|---|
| 6. Shim resolves `.cmd` explicitly | ✅ PASS |
| 7. Missing-shim fails loudly | ✅ PASS (`Start-Process` throws under `$ErrorActionPreference='Stop'`) |
| 4. No secret | ✅ PASS (in scripts) |
| 8. apply.ps1 deferral documented | ✅ PASS (intentional, documented) |

**FAIL:**

- **P0-R2-FAIL** (blocking): Same defect as P0-R1-FAIL.
  `docs/handoff/2026-09-30-laneP0-admission.md:4` contains the hardcoded
  username path `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute`.
  Violates AGENTS.md Hard Rule 1. Both reviewers independently identified the
  same file:line.

**Findings (non-blocking risks):**

- **P0-R2a** (risk): `apply.ps1:96` contains bare `omniroute` — latent
  failure if `apply.ps1` is run before the deferred shim fix lands. The
  `start-stack.ps1` shim fix does not cover `apply.ps1`.

### Milestone 2 summary

Both reviewers REJECTED with HIGH confidence. All code invariants passed
(idempotency, read-modify-write, env-relative paths in scripts, knob
ordering, shim resolution, loud failure). The sole blocking defect is a
**hardcoded username in a tracked doc file**
(`docs/handoff/2026-09-30-laneP0-admission.md:4`), violating AGENTS.md Hard
Rule 1. The defect is in a documentation file, not in the launcher scripts —
the scripts correctly use `$env:APPDATA`. Both reviewers independently
identified the same file and line.

---

## Consolidated verdict table

| Milestone | Writer lane | Reviewer family | Verdict | Confidence |
|---|---|---|---|---|
| Combos (a5bcb69) | L1-alpha | DeepSeek | APPROVE | HIGH |
| Combos (a5bcb69) | L1-alpha | Vertex/Gemini | APPROVE | HIGH |
| P0 (d88ed3b) | L1-beta | DeepSeek | REJECT | HIGH |
| P0 (d88ed3b) | L1-beta | Vertex/Gemini | REJECT | HIGH |

## Recommended follow-up actions

1. **P0 blocking defect (High priority):** Redact the hardcoded username
   `<user>` from `docs/handoff/2026-09-30-laneP0-admission.md:4`. Replace the
   absolute path with an environment-relative form (e.g.
   `$env:APPDATA\npm\node_modules\omniroute`). This is an L1-beta lane fix.
   The repository is public and has leaked credentials once before (history
   rewritten 2026-08-21); a username in a tracked file is a Hard Rule 1
   violation regardless of intent.

2. **P0 deferred shim fix (Medium priority):** `apply.ps1:96-97` still uses
   bare `omniroute`. The `start-stack.ps1` shim fix should be mirrored to
   `apply.ps1` to prevent a latent failure when `apply.ps1` is invoked
   independently.

3. **Combos test dependency (Expected):** The 5 `test_registry_render.py`
   failures on the combos branch are expected — they depend on L1-beta
   updating `catalog/ai-registry.json` `context_advertised` values to match
   the live gateway catalog (1048576). Once L1-beta lands the registry
   update, these tests will pass. No action needed from L1-alpha.

4. **Combos unverifiable model (Low priority):** The
   `antigravity/claude-opus-4-6-thinking` model is not present in the live
   gateway `/v1/models` catalog (0 `antigravity/*` models). The combo store
   uses `computed_context_length: 1048576` for this model. If the model is
   retired or renamed, the combo should be updated. The bare `opus-4-6`
   token does resolve in the live catalog.

---

## Appendix: Gateway catalog measurement (2026-09-30)

Live `/v1/models` queried with a probe script that reads the omniroute client
key from `configuration/api-keys.yml` (key value never printed). Total models
returned: 5296.

| Model ID (gateway) | Context length |
|---|---|
| `gemini/gemini-3.8-flash` | 1048576 |
| `vertex/gemini-3.8-flash` | 1048576 |
| `deepseek/deepseek-flash` | 1048576 |
| `opus-4-6` (bare token) | 1048576 |
| `antigravity/*` (any) | 0 models returned |
| `agy/*` (any) | 0 models returned |

## Appendix: Test state verification

| Branch | Test file | Passed | Failed | Notes |
|---|---|---|---|---|
| Combos (a5bcb69) | `tests/test_registry_render.py` | 155 | 5 | Expected cross-lane dep on L1-beta |
| P0 (d88ed3b) | full suite | 160 | 0 | Clean |
| Either commit | `tests/` diff vs d08f7f2 | — | — | Empty — no test files changed |

The 5 combos-branch failures: `combos.gemini-3.8-flash`, `combos.opus-4-6`,
`combos.t2-orchestrator`, `combos.t2-worker-clean`, `combos.t3-driver-clean` —
all "differs" (registry `context_advertised` stale vs combos expected value).
