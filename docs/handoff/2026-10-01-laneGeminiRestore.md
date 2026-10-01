# Lane GeminiRestore — cancel the gemini exclusion, restore pre-freewire heads (2026-10-01)

**Lane:** `L1-backlog/ws-gemini-restore-20260930`
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-geminirestore`
**Base:** `ba0c707f` (combos-pins tip: freewire + nebius removal + T1SECOND + MAINPIN 24a98228 pins)
**Writer:** gemini-restore (public-only lane: repo/config state only, no private data, no key values)
**Date:** 2026-10-01
**Operator directive:** gemini exclusion CANCELLED. Gemini stays in the combos; it must only not be hammered (retries read as a DDoS), governed by the landed resilience policy `51fd01d`…`13d853e4` (3×429/120 s → 300 s cooldown per leg; 30 min park).

> **REVIEW NONCE: `GEMRESTORE-NONCE-4Xk9Qm2Z`** — a reviewer must quote this string
> back, read from THIS file, to prove it read the evidence and not a summary.

**Commits on this lane (so far):**
- `e3436a4` — restore gemini/gemini-3.8-flash heads to pre-freewire positions (GEMRESTORE)

---

## 0. Cwd guard (first action, quoted)

- `git worktree add -b L1-backlog/ws-gemini-restore-20260930 C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-geminirestore ba0c707f` → `HEAD is now at ba0c707f doc(handoff): MAINPIN evidence + DONE note (combos.json carries main 24a98228 pins)` (exit 0)
- `git rev-parse --show-toplevel` (from the new worktree) → `C:/Users/<user>/Documents/Code/AutoOS-worktrees/AutoOS-ws-geminirestore`
- `git rev-parse HEAD` → `ba0c707f1255d6c551fbb1a07722171e6fb534e4`
- Never worked or committed in `C:\Users\<user>\Documents\Code\AutoOS` (main). No push/merge/rebase/checkout used.

## 1. Pre-freewire gemini positions (source: `git show a975d48:configuration/omniroute/combos.json`)

Command: `git show a975d48:configuration/omniroute/combos.json | python -c "import json,sys; d=json.load(sys.stdin); [print(c['name'], '|', c.get('context'), '|', '|'.join(c['models'])) for c in d['combos']]"`

Exact output (gemini-bearing rows only; full output in session log):
```
gemini-3.8-flash | 1M | gemini/gemini-3.8-flash|vertex/gemini-3.8-flash
t1-orchestrator | 128k | gemini/gemini-3.8-flash|scw/qwen3-235b-a22b-instruct-2507|nebius/zai-org/GLM-5.3-Flash|scw/mistral-small-3.2-24b-instruct-2506|meta-api/muse-spark-1.3-contributor|deepseek/deepseek-flash
t1-orchestrator-free-only | 128k | gemini/gemini-3.8-flash|scw/qwen3-235b-a22b-instruct-2507|nebius/zai-org/GLM-5.3-Flash|scw/mistral-small-3.2-24b-instruct-2506
t2-worker | 128k | gemini/gemini-3.8-flash|antigravity/gemini-3.7-flash-high|scw/qwen3-235b-a22b-instruct-2507|scw/mistral-small-3.2-24b-instruct-2506|nebius/zai-org/GLM-5.2|ovh/gpt-oss-120b|ovh/Qwen3-Coder-30B-A3B-Instruct|ovh/Qwen3.8-27B|deepseek/deepseek-flash|meta-api/muse-spark-1.3-contributor|free-ai/qwen7b
t2-worker-free-only | 128k | gemini/gemini-3.8-flash|antigravity/gemini-3.7-flash-medium|scw/qwen3-235b-a22b-instruct-2507|scw/mistral-small-3.2-24b-instruct-2506|nebius/zai-org/GLM-5.2|free-ai/qwen7b
```
- `t3-driver` and `t3-driver-free-only` carried NO `gemini/*` leg pre-freewire (verified: `grep gemini/` on the pre-freewire file matches only the 5 rows above + the T1FREE comment line).
- Pre-freewire `gemini-3.8-flash` combo head/legs: pos0 `gemini/gemini-3.8-flash`, pos1 `vertex/gemini-3.8-flash`, context `1M` (VTXLEG order per operator directive).

## 2. Restored positions (this lane, commit `e3436a4`)

Command: `python -c "import json; d=json.load(open('configuration/omniroute/combos.json')); [print(c['name'], '|', c.get('context'), '|', '|'.join(c['models'])) for c in d['combos'] if 'gemini' in c['name'] or c['name'].startswith('t1') or c['name'].startswith('t2-worker')]"`

Exact output:
```
gemini-3.8-flash | 1M | gemini/gemini-3.8-flash|vertex/gemini-3.8-flash
t1-orchestrator | 1M | gemini/gemini-3.8-flash|scw/qwen3-235b-a22b-instruct-2507|scw/mistral-small-3.2-24b-instruct-2506|groq/qwen/qwen3.8-27b|meta-api/muse-spark-1.3-contributor|deepseek/deepseek-flash
t1-orchestrator-free-only | 1M | gemini/gemini-3.8-flash|scw/qwen3-235b-a22b-instruct-2507|openrouter/nvidia/nemotron-3-super-120b-a12b:free|huggingface/zai-org/GLM-5.2|groq/qwen/qwen3.8-27b|scw/mistral-small-3.2-24b-instruct-2506
t1-orchestrator-paid | 1M | meta-api/muse-spark-1.3-contributor|deepseek/deepseek-flash
t2-worker | 128k | gemini/gemini-3.8-flash|antigravity/gemini-3.7-flash-high|scw/qwen3-235b-a22b-instruct-2507|scw/mistral-small-3.2-24b-instruct-2506|openrouter/nvidia/nemotron-3-super-120b-a12b:free|huggingface/zai-org/GLM-5.2|groq/qwen/qwen3.8-27b|openrouter/poolside/laguna-s-2.1:free|huggingface/Qwen/Qwen3.8-27B|groq/openai/gpt-oss-20b|openrouter/cohere/north-mini-code:free|groq/openai/gpt-oss-120b|openrouter/qwen/qwen3.8-27b:free|ovh/gpt-oss-120b|ovh/Qwen3-Coder-30B-A3B-Instruct|ovh/Qwen3.8-27B|deepseek/deepseek-flash|meta-api/muse-spark-1.3-contributor|free-ai/qwen7b
t2-worker-clean | 1M | deepseek/deepseek-flash
t2-worker-free-only | 128k | gemini/gemini-3.8-flash|antigravity/gemini-3.7-flash-medium|openrouter/nvidia/nemotron-3-super-120b-a12b:free|huggingface/zai-org/GLM-5.2|groq/qwen/qwen3.8-27b|openrouter/poolside/laguna-s-2.1:free|huggingface/Qwen/Qwen3.8-27B|groq/openai/gpt-oss-20b|openrouter/cohere/north-mini-code:free|scw/qwen3-235b-a22b-instruct-2507|groq/openai/gpt-oss-120b|scw/mistral-small-3.2-24b-instruct-2506|openrouter/qwen/qwen3.8-27b:free|free-ai/qwen7b
```

### Position table (pre-freewire vs restored)

| Combo | Pre-freewire (a975d48) gemini pos/pin | Restored (e3436a4) | Kept |
|---|---|---|---|
| `gemini-3.8-flash` | pos0 `gemini/gemini-3.8-flash` head, pos1 `vertex/gemini-3.8-flash`, ctx 1M | pos0 `gemini/gemini-3.8-flash`, pos1 `vertex/gemini-3.8-flash`, ctx 1M | VTXLEG order + MAINPIN 1M |
| `t1-orchestrator` | pos0 `gemini/gemini-3.8-flash` head | pos0 `gemini/gemini-3.8-flash` head (PREPENDED) | 10 free legs, T1SECOND groq leg, OVH n/a, 1M (MAINPIN d), nebius still out, deepseek-flash (MAINPIN D2) |
| `t1-orchestrator-free-only` | pos0 `gemini/gemini-3.8-flash` head | pos0 `gemini/gemini-3.8-flash` head (PREPENDED) | free band kept in order, 1M (MAINPIN e), nebius still out |
| `t2-worker` | pos0 `gemini/gemini-3.8-flash` head, pos1 `antigravity/gemini-3.7-flash-high` | pos0 `gemini/gemini-3.8-flash`, pos1 `antigravity/gemini-3.7-flash-high` (PREPENDED) | free band, OVH x3, 128k, nebius still out |
| `t2-worker-free-only` | pos0 `gemini/gemini-3.8-flash` head, pos1 `antigravity/gemini-3.7-flash-medium` | pos0 `gemini/gemini-3.8-flash`, pos1 `antigravity/gemini-3.7-flash-medium` (PREPENDED) | free band, 128k, nebius still out |
| `t3-driver` | NO gemini leg pre-freewire | unchanged (no gemini added) | free band, OVH x3, nebius still out |
| `t3-driver-free-only` | NO gemini leg pre-freewire | unchanged (no gemini added) | free band, nebius still out |

### Decisions (recorded, combo-named)

- **D1 (no displacement):** where the restore would displace a free leg, the pre-freewire gemini pin wins for gemini's own position: the gemini head was PREPENDED at pos0 and every free leg kept in its base order. No free leg displaced, no free leg removed. Verified: `python -c` above shows all base free legs still present in order after the gemini head.
- **D2 (main pins kept):** contexts `t1-orchestrator`/`t1-orchestrator-free-only` stay `1M` (MAINPIN d/e, also `origin/main e58274a8`); `deepseek/deepseek-flash` kept over main `24a98228`'s literal `deepseek/deepseek-v4-flash` per MAINPIN D2 (main's own tip superseded it); `vertex/gemini-3.8-flash` second leg kept (MAINPIN b was context-only); `antigravity/` spellings kept (AGYCANON). No main pin reversed.
- **D3 (t3-* unchanged):** `t3-driver`/`t3-driver-free-only` had no `gemini/*` leg at `a975d48`, so nothing restored there. Explicit, not an omission.
- **D4 (nebius stays out):** `python -c "import json; d=json.load(open('configuration/omniroute/combos.json')); print([l for c in d['combos'] for l in c['models'] if 'nebius' in l])"` → `[]`. No nebius leg re-added.
- **D5 (OVH/groq/1M kept):** `t1 ctx 1M 1M`, `t1 groq True`, `t2 ovh ['ovh/gpt-oss-120b', 'ovh/Qwen3-Coder-30B-A3B-Instruct', 'ovh/Qwen3.8-27B']`, `t3 ovh [...]` (quoted from session `python -c` output).

## 3. Gemini connection stays wired (no registry edit)

- `catalog/ai-registry.json` NOT edited by this lane (L1-beta's file). Verified: `git status --short` shows only `M configuration/omniroute/combos.json` (then committed).
- Provider row still defined: `python -c "import json; d=json.load(open('catalog/ai-registry.json')); p=d['providers']['google_ai_studio']; print({k:p[k] for k in p if k in ['available','unavailable_until','tier','model_prefix']}); print('models gemini row:', 'gemini-3.8-flash' in d['models'])"` → `{'tier': 'free', 'available': False, 'unavailable_until': '2026-10-07T00:00:00Z'}` + `models gemini row: True`. The connection definition is not deleted; the key stays unwired; `available:false` + `unavailable_until` is beta's to re-open under the backoff policy.

### Registry flags for L1-beta (exact lines, do NOT edit here)

- `catalog/ai-registry.json:3391-3393` (`routes.gemini-3.8-flash.$comment` FREEWIRE repoint + `legs: ["vertex/gemini-3.8-flash", "openrouter/google/gemini-3.8-flash", ...]`): lacks `gemini/gemini-3.8-flash` head — beta to restore it as pos0 (vertex second).
- `routes.t1-orchestrator.legs`, `routes.t1-orchestrator-free-only.legs`, `routes.t2-worker.legs`, `routes.t2-worker-free-only.legs`: lack the `gemini/gemini-3.8-flash` head restored in combos — beta to mirror pos0.
- `providers.google_ai_studio` (`available: false`, `unavailable_until: "2026-10-07T00:00:00Z"`, FREEWIRE comment at `catalog/ai-registry.json:2884`): beta to re-open under the repeated-429 backoff policy, not a blanket exclusion.
- `models.gemini-3.8-flash.context_advertised` is already `1048576` (GEM1M); no change needed.

## 4. Wording correction (`git grep -n -i -e 'gemini.*exclu' -e 'exclu.*gemini'`)

Exact grep output on this base (quoted):
```
CHANGELOG.md:1458: ... spark and gemini **400k** ... rotate acquires exclusive ...
catalog/ai-registry.json:3037: ... every paid openrouter leg is still excluded by deny-openrouter ... openrouter/google/gemini-3.8-flash ...
docs/handoff/2026-09-30-laneFreeWire.md:1:# Lane FreeWire — free-leg wiring, gemini exclusion, 429 policy (2026-09-30)
docs/handoff/2026-09-30-laneFreeWire.md:111:## 3. Gemini exclusion
docs/handoff/2026-10-01-laneCombosPins.md:111:**not the leg**. The leg changes (`gemini/*` excluded + vertex repoint = FREEWIRE;
docs/handoff/2026-10-01-laneCombosPins.md:200:legs, gemini exclusion + vertex repoint, nebius removal, OVH legs, 1M contexts,
docs/handoff/2026-10-01-laneNebiusCombos.md:5:**Base:** FREEWIRE wiring tip `2ff537a` (free legs wired, gemini excluded)
logs/handoff-sessions/DONE-ws-freewire.md:1:# DONE — L1-backlog/ws-freewire-20260930 (free-leg wiring + gemini exclusion)
logs/handoff-sessions/DONE-ws-nebius-combos.md:5:**Base:** FREEWIRE wiring tip `2ff537a` (free legs wired, gemini excluded)
```

- `CHANGELOG.md:1458` and `catalog/ai-registry.json:3037` are FALSE POSITIVES (gemini + "exclusive"/"excluded by deny-openrouter", not a gemini-exclusion claim). No edit; registry is off-limits anyway.
- All other hits are HISTORICAL lane records (freewire's title/section, combos-pins/nebius-combos base descriptions, DONE titles). They accurately describe what those lanes did at the time. This lane does NOT rewrite other lanes' history. The corrected standing wording lives in THIS lane: the `GEMRESTORE` `$comment` in `combos.json` and this evidence file state "**gemini retained; usage governed by the repeated-429 backoff policy (3×429/120 s → 300 s cooldown per leg; 30 min park)**".
- Follow-up for L0/another lane: update the titles/sections listed above if the operator wants historical docs to carry a reversal addendum. Exact file:line spots are listed here; no other worktree edited.

Other-branch docs (not edited, listed for follow-up):
- `docs/handoff/2026-09-30-laneFreeWire.md` on `ws-freewire` (lines 1, 111, 122-125 per that file's §3.2) — canonical home is that branch.
- `docs/handoff/2026-09-30-laneFreeProbe.md` on `ws-free-probe` — §5.1 measured 429s / §6 rotation proposal; check for `gemini.*exclu` wording there (not present in this worktree).

## 5. Verification (every command quoted)

- `python tools/registry.py render omniroute --check` → `differs: combos.gemini-3.8-flash / differs: combos.t1-orchestrator / differs: combos.t1-orchestrator-free-only / differs: combos.t2-worker / differs: combos.t2-worker-free-only / differs: combos.t3-driver / differs: combos.t3-driver-free-only` (exit 1). HONEST DELTA vs the task's "six known nebius lines": base showed the six nebius combos; after restore there are SEVEN (the same six names + `gemini-3.8-flash`). The four gemini-restored combos were already in the six; only `gemini-3.8-flash` is a new name, because the registry render is still vertex-only (beta's half: registry lacks the gemini head + provider still gated). No free leg or main pin caused it; the `$comment` GEMRESTORE note records this.
- `python tools/registry.py render litellm --check` → `ok: render litellm matches .../configuration/litellm/config.yaml` (exit 0)
- `python tools/registry.py render ide --check` → `ok: render ide matches .../catalog/ide-models.json` (exit 0)
- `python tools/registry.py render openhands --check` → `ok: render openhands matches .../configuration/openhands/tier-profiles.json` (exit 0)
- `python tools/registry.py render models-doc --check` → `ok: render models-doc matches .../docs/models.md` (exit 0)
- `python tools/registry.py check` → `info: t1-orchestrator-clean exempt from privacy rule 3 ...` + `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers` (exit 0)
- `python tools/registry.py validate` → same ok line (exit 0)
- `python tools/audit-router.py --offline` → `combos: 22 (...)` + `no drift (non-200 legs above are provider/balance state, not config)` (exit 0)
- `powershell -ExecutionPolicy Bypass -File configuration/omniroute/apply.ps1 -DryRun` → `Gateway OK on http://127.0.0.1:20128` + pre-existing `ConvertFrom-Json ... duplicated keys 'mistral-small-3.2-24b-instruct-2506' and 'Mistral-Small-3.2-24B-Instruct-2506'` warning (baseline-identical, flagged F2 in laneCombosPins) + `Catalog: ! could not read /v1/models (check the omniroute client key in api-keys.yml)` (no api-keys.yml in this public-only worktree) + combos planned with gemini heads, incl. `gemini-3.8-flash: would create [priority] with gemini/gemini-3.8-flash,vertex/gemini-3.8-flash`. NO live apply (combined apply is L0/operator-canonical).
- Tests `python -m pytest tests/test_registry.py tests/test_registry_render.py tests/test_registry_loader.py -q`: base (stashed) `6 failed, 459 passed, 37 subtests passed` — the six: `test_a_credit_leg_is_last_and_gated_until_priced` + five render/drift tests listing the six nebius combos. After restore: `6 failed, 459 passed, 37 subtests passed` — SAME six test names, no new failure; the five render/drift diffs now list seven combos (six nebius + `gemini-3.8-flash`), which is beta's registry gap, not a code regression.
- Tests `python -m pytest tests/test_audit_router_registry.py tests/test_mirror_litellm_env_registry.py tests/test_sync_router_tiers_registry.py -q`: base `1 failed, 42 passed` (`test_explicit_combos_override_still_works`, nebius-only drift of six combos); after `1 failed, 42 passed` (same test, drift now seven combos incl. gemini heads). No new failure.
- Resilience env grep: on THIS branch `git grep -n OMNIROUTE_ROTATION_ENABLED -- configuration/start-stack.ps1 ...` → no output (exit 1): the six resilience env names are NOT present here (expected — separate lane). On `L1-backlog/ws-resilience-env-20260930` @ `13d853e4` (worktree `AutoOS-ws-resenv`) the same six-name grep proves present in all four launcher files, e.g. `configuration/omniroute/apply.ps1:94-110` (`ROTATION_ENABLED true`, `ROTATE_ON_429 true`, `ROTATE_429_THRESHOLD 3`, `ROTATE_429_WINDOW_SECONDS 120`, `ROTATION_RATE_LIMIT_RESET_SECONDS 300`, `PROVIDER_BREAKER_API_KEY_COOLDOWN_MS 1800000`), mirrored in `apply.sh:455-460`, `start-stack.ps1:117-133`, `start-stack.sh:86-91`. Values = 3×429/120 s → 300 s cooldown per leg; 30 min (1800000 ms) park. This lane relies on that landed policy; L0 combines the branches.

### Gemini-leg ack probe: SKIP (honest, quota-limited, public-only)

- Attempt: unauthenticated `GET http://127.0.0.1:20128/v1/models` → verbatim `urllib.error.HTTPError: HTTP Error 401: Unauthorized` (exit 1). This worktree has no `configuration/api-keys.yml` (`apply.ps1 -DryRun` prints `No configuration\api-keys.yml yet`), and this lane is public-only (no private data, no key values) so no client key was read or borrowed.
- No hammering: single unauthenticated metadata read only; zero chat-completion attempts against `gemini/gemini-3.8-flash` (retries read as a DDoS; gemini is quota-limited per FREEWIRE §3.1: 8088 cooling-down lines, 7187×429 on 2026-09-30).
- Result: **SKIP** — `gemini/gemini-3.8-flash` ack not attempted without a client key in this public-only lane; the restored head is DryRun-planned (`would create [priority] with gemini/gemini-3.8-flash,vertex/gemini-3.8-flash`) and governed by the backoff policy when the combined apply runs. No `chat_admission_busy` / `Rate limit exceeded` encountered (noCompleions call made); no backoff needed.

## 6. Reviews (nonce-gated, FREE families only)

| # | Reviewer route (family) | Verdict | Session |
|---|---|---|---|
| 1 | FREE (reviewer states FREE pool: nemotron / hf-glm / groq / openrouter-free; explicitly NOT DeepSeek) | APPROVED | `ses_f096ea65fffet1N2oQr0lsxOdv` |
| 2 | `omniroute/or-nemotron-3-super-free` (NVIDIA, FREE) | APPROVED | `ses_f096df7b9ffebSRYYl75P1HhR2` |

Never `t3-driver-clean` (DeepSeek; operator flagged DeepSeek saturation). Each reviewer quoted `GEMRESTORE-NONCE-4Xk9Qm2Z` from THIS file.
- #1 verified `e3436a4` gemini pos0 restores on all five combos, t3-* unchanged, no nebius re-added, registry untouched, and `render omniroute --check` seven differs lines (quoted above). Verdict APPROVED.
- #2 verified GEMRESTORE `$comment` phrase, the `git grep` exclu spots vs §4, and `git diff --name-only ba0c707f..50b28e7` scope. Verdict APPROVED.
- Two pre-commit review attempts (`ses_f099f406affejDbYbxZWkROmsL`, `ses_f099f4069ffebCbNfT4I3hnN4i`) ran before the evidence file was committed and could not read it (file-not-found / nonce-not-found); discarded and re-run after commit `50b28e7`. No refusals; no `chat_admission_busy` / `Rate limit exceeded` encountered (no gateway chat calls made).

## 7. CHANGELOG bullet

- `fix(routing): cancel the gemini exclusion — restore gemini/gemini-3.8-flash to its pre-freewire head positions (gemini-3.8-flash combo head + vertex second; t1-orchestrator, t1-orchestrator-free-only, t2-worker, t2-worker-free-only heads), keeping the 10 proven free legs, nebius removal, T1SECOND groq leg, OVH legs, 1M contexts and main 24a98228 pins; gemini retained under the repeated-429 backoff policy (3×429/120 s → 300 s cooldown; 30 min park).`

## 8. Files changed / overlaps for L0

- `configuration/omniroute/combos.json` only (7 insertions, 1 deletion in `e3436a4` + this evidence + DONE note). No registry edit. No launcher edit (resilience lives on `ws-resilience-env`).
- Combined apply is L0/operator-canonical; this lane ran `-DryRun` only.
