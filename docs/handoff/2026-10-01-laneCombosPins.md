# Lane Combos-Pins — carry main's `24a98228` pins into `combos.json` (2026-10-01)

**Lane:** `L1-backlog/ws-combos-pins-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-combospins` (proven: `git rev-parse --show-toplevel` = that path)
**Base:** `f2d8d607` — the combos-chain tip (freewire → nebius removal → invariant fix)
**Writer:** combos-pins (critical writer, pinned)
**Scope:** `configuration/omniroute/combos.json` + its renders only. No registry edit (L1-beta owns it).
**Date:** 2026-10-01

> **REVIEW NONCE: `COMBOSPIN-NONCE-7Qm3Vt9K`** — a reviewer must quote this string
> back, read from THIS file, to prove it read the evidence.

---

## 0. Why this lane exists

`origin/main` is `e58274a8`; across this branch's touched files **only
`configuration/omniroute/combos.json` conflicts** with main. The operator will
rebase / **Take** this branch, so the committed `combos.json` must already carry
main's pins — otherwise a "take ours" resolution silently drops them.

Pure facts, verified this session (command + output):

- `git rev-parse origin/main` → `e58274a8030625c19b739032d37aa82d2ec24a73`.
- `git rev-list --count e58274a8..HEAD` → `19` (this branch's unique commits).
- Intersection of files changed by main after the common base and files changed
  by this branch = **`configuration/omniroute/combos.json` only**. Main's other
  seven files from `24a98228` (`tools/render-opencode-container-config.py`,
  `tests/test_render_opencode_config.py`, the four `configuration/docker/ai-stack/*`
  files, `.agents/skills/unattended-orchestration/SKILL.md`) are **not touched by
  this branch**, so they cannot conflict and need no treatment here (flag F1).

---

## 1. Pin inventory — exactly what `24a98228` adds

`git show 24a98228 --stat` → 8 files, but only the `combos.json` part is in scope.
`git show 24a98228 -- configuration/omniroute/combos.json` yields:

| # | Combo | Field | Before | After (the pin) |
|---|---|---|---|---|
| P1 | `deepseek-v4.1-flash` | `context` | `128k` | `1M` |
| P2 | `deepseek-v4.1-flash` | `models[0]` | `deepseek/deepseek-flash` | `deepseek/deepseek-v4-flash` |
| P3 | `gemini-3.8-flash` | `context` | `128k` | `1M` |
| P4 | `opus-4-6` | `context` | `200k` | `1M` |
| P5 | `t1-orchestrator` | `context` | `128k` | `1M` |
| P6 | `t1-orchestrator-free-only` | `context` | `128k` | `1M` |
| P7 | `t2-orchestrator` | `context` | `200k` | `1M` |

Plus two `$comment` blocks (CTXAUDIT 2026-09-30 / CTXFIX 2026-09-29).

**Not in `combos.json`** (so not applied here, and not in this branch's scope):

- **DeepSeek effort ladder** — `render-opencode-container-config.py`
  `DEEPSEEK_VARIANTS = ("low","high","max")` pinning
  `provider.<p>.models.deepseek-v4.1-flash.variants`.
- **Vertex leaf models** — `FLEET_VERTEX_MODELS` in the same renderer
  (`vertex-gemini-3.1-pro-preview`, `vertex-gemini-2.5-flash`,
  `vertex-claude-sonnet-4-5`, `vertex-deepseek-v4-flash`).
- **Stack no-OOM** — `ai-stack.sh` / `compose.yml` / `opencode.Dockerfile` /
  `stack.env.example` (drop `OPENCODE_MEM_LIMIT`, image `2.0.19`). **No part of
  "stack no-OOM" touches `combos.json`.**

---

## 2. What our lineage already carried vs what was missing

Compared programmatically across `24a98228`, `origin/main` (`e58274a8`) and `HEAD`:

| Combo | Field | main `24a98228` pin | origin/main tip `e58274a8` | ours (pre-edit) | Action |
|---|---|---|---|---|---|
| `deepseek-v4.1-flash` | context | `1M` | `1M` | `1M` | none |
| `deepseek-v4.1-flash` | models | `['deepseek/deepseek-v4-flash']` | `['deepseek/deepseek-flash']` | `['deepseek/deepseek-flash']` | **keep ours** (D2) |
| `gemini-3.8-flash` | context | `1M` | `1M` | `1M` | none |
| `opus-4-6` | context | `1M` | `1M` | `1M` | none |
| `t1-orchestrator` | context | `1M` | `1M` | `128k` | **apply 1M** (D1) |
| `t1-orchestrator-free-only` | context | `1M` | `1M` | `128k` | **apply 1M** (D1) |
| `t2-orchestrator` | context | `1M` | `1M` | `1M` | none |

Net: **only P5 + P6 were missing.** The applied diff is `1 file changed, 25
insertions(+), 4 deletions(-)` (commit `2fdf6a7`).

---

## 3. Decisions (combo-named, with reasoning)

**D1 — `t1-orchestrator` and `t1-orchestrator-free-only`: context `128k` → `1M`
(APPLY main's pin).** Main's pin, and `origin/main`'s own tip carries `1M`. Our
`CTXFIX 2026-09-30` note had clamped these to `128k` ("honest clamp": the route
falls to 128k legs), but that is this lineage's lane reasoning, **not an operator
directive**, and no operator directive requires the clamp — so per L0's rule
main's pin wins. The `$comment` CTXFIX sentence was edited to stop claiming
`t1-orchestrator*` keeps `128k` (it now names `t2-worker`/`t3-driver` only, and a
new `MAINPIN` block records the override).

**D2 — `deepseek-v4.1-flash` keeps leg `deepseek/deepseek-flash` (KEEP ours; the
pin is already satisfied by main's own tip).** `24a98228` P2 pinned
`deepseek/deepseek-v4-flash`, **but `origin/main`'s current tip `e58274a8` carries
`deepseek/deepseek-flash`** — the P2 pin was superseded *inside main itself* by
the `d08f7f2` merge (`git show e58274a8:configuration/omniroute/combos.json` →
`deepseek-v4.1-flash` models `['deepseek/deepseek-flash']`). Applying P2 literally
would **reverse main's own current state** and would contradict this lineage's
operator-session DS1M measurement (`deepseek-flash` = DeepSeek-V4.1-Flash, 1M
native — `docs/handoff/2026-09-30-workstation-omniroute-handoff.md` §Addendum).
It would also add a 7th `render omniroute --check` drift line, since this branch's
registry route leg is `deepseek/deepseek-flash`.

**D3 — `gemini-3.8-flash` keeps `vertex/gemini-3.8-flash`; `opus-4-6` and
`t2-orchestrator` keep `antigravity/claude-opus-4-6-thinking` (KEEP ours).**
`24a98228`'s pins on these combos were the **context** (`1M`, already matched),
**not the leg**. The leg changes (`gemini/*` excluded + vertex repoint = FREEWIRE;
`agy/*` → `antigravity/*` = AGYCANON) are this lineage's operator-directed work,
which the task explicitly preserves. `t2-orchestrator`'s extra
`deepseek/deepseek-flash` leg and `t1-orchestrator-paid`'s are this lineage's too.

**D4 — no other file needs the same treatment.** Main `24a98228`'s other seven
files do not overlap this branch's touched set (§0), so nothing else conflicts.

---

## 4. Verification (quoted; run from the worktree)

- `python tools/registry.py check` and `validate` →
  `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers` (+ the known
  `t1-orchestrator-clean` privacy-exemption info line). exit 0.
- `python tools/registry.py render omniroute --check` → **exactly the six known
  nebius drift lines** (identical to base; these are **L1-beta's half**, the
  registry still lists the removed `nebius/*` legs):
  `combos.t1-orchestrator`, `combos.t1-orchestrator-free-only`, `combos.t2-worker`,
  `combos.t2-worker-free-only`, `combos.t3-driver`, `combos.t3-driver-free-only`.
- `render litellm --check`, `render ide --check`, `render openhands --check`,
  `render models-doc --check` → all `ok` (exit 0).
- `python tools/audit-router.py --offline` → `no drift` (exit 0).
- `configuration/omniroute/apply.ps1 -DryRun` → exit 0, all 22 combos planned
  with this lineage's legs. **No live apply.**

> Pre-existing `apply.ps1 -DryRun` warning (NOT caused here):
> `ConvertFrom-Json: ... contains keys with different casing ...
> 'mistral-small-3.2-24b-instruct-2506' vs 'Mistral-Small-3.2-24B-Instruct-2506'`
> — the catalog read fails and the run proceeds without catalog validation. This
> happens before `combos.json` is read; flagged F2 for L0.

**Tests vs base** (`python -m pytest tests/test_registry.py
tests/test_registry_render.py tests/test_registry_loader.py -q`):

| Run | Result |
|---|---|
| base (`git stash` of this change) | `6 failed, 459 passed` |
| after this change | `6 failed, 459 passed` — same six, no new failure |

The six: `test_registry.py::ComboCrossProviderTests::test_a_credit_leg_is_last_and_gated_until_priced`
(this lineage's OVH credits leg, pre-existing) and five render/drift tests whose
list is the six nebius combos.
`tests/test_audit_router_registry.py` + `tests/test_mirror_litellm_env_registry.py`
+ `tests/test_sync_router_tiers_registry.py` → `1 failed, 42 passed` both at base
and after (same `test_explicit_combos_override_still_works`, nebius drift).

---

## 5. Union proof

**Main-pin presence:** every applicable pin from `24a98228` is now present in our
`combos.json` except P2, whose value main's own tip no longer carries (D2).
After the edit, for P1/P3–P7 our value **equals** `origin/main e58274a8`'s value.
Scoped diffs:
`git diff --stat 24a98228 HEAD -- configuration/omniroute/combos.json` →
`1 file changed, 207 insertions(+), 40 deletions(-)` (our lineage's content);
`git diff --stat e58274a8 HEAD -- configuration/omniroute/combos.json` →
`1 file changed, 193 insertions(+), 37 deletions(-)` — **all additions are our
lineage; no main pin is a deletion.**

**Before/after of the pinned lines** (`git diff -- configuration/omniroute/combos.json`,
commit `2fdf6a7`):

```
       "name": "t1-orchestrator",
       "strategy": "priority",
-      "context": "128k",
+      "context": "1M",
...
       "name": "t1-orchestrator-free-only",
       "strategy": "priority",
-      "context": "128k",
+      "context": "1M",
```

**Our lineage markers, still present after the edit** (regenerated programmatically):

```
combos count: 22   has combo 'groq-qwen3.8-27b': True
gemini-3.8-flash models: ['vertex/gemini-3.8-flash']          # vertex repoint kept
t1-orchestrator models: [scw/qwen3-235b..., scw/mistral-small..., groq/qwen/qwen3.8-27b, meta-api/...contributor, deepseek/deepseek-flash]
any nebius leg: []                                            # NEBREMOVAL kept
OVH legs in t2-worker: ['ovh/gpt-oss-120b','ovh/Qwen3-Coder-30B-A3B-Instruct','ovh/Qwen3.8-27B']
clean contexts: {'t2-worker-clean': '1M', 't3-driver-clean': '1M'}   # CTXFIX kept
```

**Union statement:** main's applicable `24a98228` pin content (5 contexts, 4 of
them already present + 2 applied here) **and** this lineage's content (proven free
legs, gemini exclusion + vertex repoint, nebius removal, OVH legs, 1M contexts,
the `t1-orchestrator` `groq/qwen/qwen3.8-27b` leg) are **both present** in the one
committed `configuration/omniroute/combos.json`.

---

## 6. Flags for L0 (not in this branch's scope)

- **F1:** main `24a98228`'s other seven files (deepseek effort ladder + vertex
  leaf models in `tools/render-opencode-container-config.py`; the ai-stack no-OOM
  changes; the SKILL.md rules) are main-only and untouched by this branch — they
  stay on main and need nothing here.
- **F2:** pre-existing `apply.ps1` catalog-case-sensitivity defect (see §4) —
  reading `catalog/ai-registry.json` with `ConvertFrom-Json` fails on
  `mistral-small-3.2-24b-instruct-2506` / `Mistral-Small-3.2-24B-Instruct-2506`,
  so the dry run silently skips catalog validation. Not introduced here.

---

## 7. Reviews

Two read-only `t3-reviewer` leaves, **free model families only** (operator: no
DeepSeek / `t3-driver-clean`), nonce-gated on `COMBOSPIN-NONCE-7Qm3Vt9K`.
Verdicts recorded in the follow-up commit to this file (§7 filled after the run).

| # | Reviewer route (family) | Verdict | Session |
|---|---|---|---|
| 1 | _pending_ | _pending_ | — |
| 2 | _pending_ | _pending_ | — |

---

## 8. Explicitly NOT done

- No `catalog/ai-registry.json` edit (L1-beta's half; the six nebius registry
  rows are theirs).
- No gateway restart, no live apply.
- No other lane's files touched.
