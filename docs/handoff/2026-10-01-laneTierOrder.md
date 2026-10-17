# Lane Tier Order (TORDER) — 2026-10-01 — evidence

Branch `L1-backlog/ws-tier-order-20261001`, base `e3436a4d`.
Worktree `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-tier-order` ONLY.
No live apply (`-DryRun` only). Every claim = command + exact output (below).

Reviewer nonce (reviewers must echo): **TORDER-NONCE-C493B548**

## Decisions

- **D-TORDER-1(b) implemented**: t1 band = 1M ONLY. `t1-orchestrator` and
  `t1-orchestrator-free-only` keep only legs window `>= 600000`
  (operator update 3; explicit constant `T1_MIN_WINDOW = 600000` in
  `tools/combo-contract.py`; rendered result still 1M). Sub-1M legs removed to t2/t3.
- **D-TORDER-2 (usable gap, kept what exists)**: `t1-orchestrator` (5 servable 1M
  legs / 4 providers) and `t1-orchestrator-free-only` (2 servable 1M legs / 2 free
  providers) have **0 `usable_legs`** (proven+priced+fits) because no 1M leg is
  `tool_calls:proven` yet (gemini-3.8-flash unproven, google twin unproven,
  meta_api unproven, deepseek unproven; only `free_ai/google/gemini-3.7-flash`
  1000000 is proven but not wired as primary per brief). Measured live windows
  (2026-10-01, `/v1/models` 4168 models): `gemini/gemini-3.8-flash` 1048576/65536,
  `vertex/gemini-3.8-flash` 1048576, `free-ai/google/gemini-3.8-flash` 1048576,
  `free-ai/google/gemini-3.7-flash` 1048576, `meta-api/muse-spark-1.3-contributor`
  1048576, `deepseek/deepseek-flash` 1048576. No leg invented; probes needed for
  `tool_calls:proven` to make t1 usable. `tests/test_registry.py` usable tests
  exempt t1 pair with this note; contract (d) still gates t1 1M + >=600k.
- **TORDER-OR (openrouter NO credits -> :free only)**: 7 paid `openrouter/*` legs
  dropped (were already unavailable/gated; combos clean); `t1-orchestrator-clean`
  keeps declared+gated paid leg so it stays `omitted`, not legless.
  `providers.openrouter.available` stays `True` (gating paid models, not provider).
- **t2/t3 stay 128k CONFIRMED** (operator update 5): contract literal remains 128k
  clamp; sub-1M allowed.

## Before/after drift (`python tools/registry.py render omniroute --check`)

Before (base `e3436a4d` + GEMRESTORE worktree, measured):
`python tools/registry.py render omniroute --check` exit **1** on 7 combos:
`gemini-3.8-flash` (render `[vertex]` vs committed `[gemini, vertex]`),
`t1-orchestrator` (render ctx 128k vs committed 1M),
`t1-orchestrator-free-only` (same),
`t2-worker` (render lacks gemini head, has `nebius/zai-org/GLM-5.2`, deepseek BEFORE meta-api),
`t2-worker-free-only` (nebius),
`t3-driver` (nebius; deepseek before meta-api),
`t3-driver-free-only` (nebius). `omitted` matched.

After (this lane):
`python tools/registry.py render omniroute --check` exit **0**:
`ok: render omniroute matches .../configuration/omniroute/combos.json`
26 combos, 5 omitted (unchanged list).

## Per-tier leg tables (rendered `combos.json` models, gateway spelling)

- `t1-orchestrator` **1M**: `gemini/gemini-3.8-flash`, `free-ai/google/gemini-3.8-flash`,
  `vertex/gemini-3.8-flash`, `meta-api/muse-spark-1.3-contributor`,
  `deepseek/deepseek-flash` (LAST).
- `t1-orchestrator-free-only` **1M**: `gemini/gemini-3.8-flash`,
  `free-ai/google/gemini-3.8-flash` (2 distinct FREE 1M providers).
- `t2-worker` **128k**: `gemini/gemini-3.8-flash` (head),
  `groq/qwen/qwen3.8-27b`, `huggingface/zai-org/GLM-5.2`,
  `openrouter/nvidia/nemotron-3-super-120b-a12b:free`, `huggingface/Qwen/Qwen3.8-27B`,
  `groq/openai/gpt-oss-20b`, `openrouter/cohere/north-mini-code:free`,
  `groq/openai/gpt-oss-120b`, `openrouter/qwen/qwen3.8-27b:free`,
  `openrouter/poolside/laguna-s-2.1:free`, `free-ai/qwen7b`,
  `scw/qwen3-235b-a22b-instruct-2507`, `scw/mistral-small-3.2-24b-instruct-2506`,
  `antigravity/gemini-3.7-flash-high` (not head),
  `ovh/gpt-oss-120b`, `ovh/Qwen3-Coder-30B-A3B-Instruct`, `ovh/Qwen3.8-27B`,
  `vertex/gemini-3.8-flash` (NEW), `meta-api/muse-spark-1.3-contributor`,
  `deepseek/deepseek-flash` (LAST). `nebius/*` removed.
- `t2-worker-free-only` **128k**: same free band + `antigravity/gemini-3.7-flash-medium`
  tail; NO credit/paid.
- `t3-driver` **128k**: same free band (gemini head, scaleway not heads),
  `ovh` x3 + `vertex` credits, `mistral/mistral-code-latest` (first paid),
  `meta_api`, `deepseek` LAST. `nebius/*` removed.
- `t3-driver-free-only` **128k**: free band only, gemini head, scaleway tails.
- `gemini-3.8-flash` **1M**: `gemini/gemini-3.8-flash`, `vertex/gemini-3.8-flash`
  (openrouter paid removed per OR rule; deepinfra tail still gated).

Live catalog (2026-10-01, 4168 models, key `omniroute` read-only from main
`configuration/api-keys.yml`): gemini 1048576/65536, vertex 1048576,
free-ai/gemini-3.8-flash 1048576, free-ai/gemini-3.7-flash 1048576,
openrouter/gemini-3.8-flash 1048576/65536, deepseek 1048576,
meta-api/muse-spark-1.3-contributor 1048576, ovh/gpt-oss-120b 400000,
ovh/Qwen3-Coder 128000, ovh/Qwen3.8-27B 128000 (registry 262000, catalogue-page
source; live 128000 — audit note), free-ai/qwen7b 128000, groq/qwen 131072,
groq/gpt-oss 131072, openrouter :free 256k-262144, scw/scaleway 128000 (both
spellings), mistral 128000. NOT_FOUND (apply skips with warning):
`huggingface/*` (0 rows), `antigravity/gemini-3.7-flash-high/medium` (0 rows),
`deepinfra/google/*` (0), `nebius/*` (0 — confirms removal).

## Four new combos (class credit, `opencode/zed` only, no openhands_profile)

- `ovh-qwen3.8-27b` (`ovhcloud/Qwen3.8-27B` → `ovh/Qwen3.8-27B`): 256k, output 32768.
  Registry adv 262000 (OVH catalogue), live 128000 (audit note above).
- `ovh-gpt-oss-120b` (`ovhcloud/gpt-oss-120b` → `ovh/gpt-oss-120b`): 128k, 32768.
  Shared model adv 131072, live OVH 400000 (audit note).
- `ovh-qwen3-coder-30b` (`ovhcloud/Qwen3-Coder-30B-A3B-Instruct`): 128k, 32768.
  Adv/live 128000 match.
- `vertex-gemini-3.8-flash` (`vertex/gemini-3.8-flash`, live spelling not
  `vertex_ai/` which live does not know): 1M, output 65536. Adv/live 1048576 match.

## Context/output fixes

- `models.gemini-3.8-flash`: `output_max` 32768 → **65536** (live
  `max_output_tokens` 65536 gemini + openrouter twin); `context_advertised`
  1048576 (kept); `context_usable.tokens` 65536 → **1048576**, source stays
  `default` (operator update 4; prior `$comment` claimed 524288 50% — superseded).
- `models.google/gemini-3.8-flash` (openrouter twin): `context_advertised`
  131072 → **1048576** (live 1048576); `output_max` 32768 → **65536** (live);
  `context_usable` 65536 → **524288** (50% default, matching 1M siblings).
- `routes.gemini-3.8-flash`: legs → `[gemini, vertex, deepinfra (gated)]`
  (openrouter paid removed per OR rule; TASK3 spec was 4 with openrouter gated —
  superseded); `surfaces.omniroute.context` 1048576, declared `1M`, output
  32768 → **65536**, openhands `max_output` → 65536, display updated to gemini head.
- `providers.google_ai_studio.available` False → **True** (`unavailable_until`
  removed per rule 8), `$comment` += backoff policy string (gateway 08:07Z ACTIVE).

## Window audit (`context_usable` wildly below `context_advertised`)

Measured in-registry (all routes, `usable/advertised`):
- `gemini-3.8-flash` 1048576/65536 (0.06) — **fixed** to 1048576 per operator update 4.
- `claude-opus-4-6-thinking` 1048576/100000 (0.10) — stale (usable is old 200k 50%);
  NOT fixed (no live usable measurement; needs probe-recall).
- `Qwen3.8-27B` 262000/64000 (0.24) — low (50% would be 131000); NOT fixed
  (no live usable measurement; registry usable is conservative default).
- `google/gemini-3.8-flash` was 131072/65536 (0.50, not wild) before adv fix;
  after adv 1048576, usable set 524288 (0.50) to avoid creating a wild ratio.

## Contract rule + where wired

`tools/combo-contract.py` (explicit `T1_MIN_WINDOW = 600000`): (a) ladder-floor
contexts equal (registry numeric ↔ combos label ↔ ide/opencode client limit);
(b) trial→free→credits→paid, paid last (free-only/all-credit exempt), deepseek
last, no free after paid, servable openrouter MUST `:free`, declared paid
openrouter MUST gated (`t1-clean` declared+gated exception so it stays omitted);
(c) every leg `resolve_leg` (fail); live existence warn-only (apply skips unknown
with warning, exit 0 — failing here would break `apply -DryRun` exit 0);
(d) t1 1M + every leg ≥600000; t2/t3 128k clamp (do not raise).

Fail-closed (non-zero, also under `-DryRun`): `configuration/omniroute/apply.ps1`
+ `apply.sh` (before creating anything); CI `.github/workflows/ci.yml` lint job;
harnesses `tests/run-tests.ps1` registry-drift case + `tests/linux/17-ai-routing.sh`;
pytest `tests/test_registry.py::ComboContractTests`. Skill
`.agents/skills/combo-create/SKILL.md` (one home; states 600000 explicitly).

Superseded/adapted tests (recorded): `MetaApiProviderTests::
test_on_t1_it_follows_the_free_band` → `test_on_t1_it_follows_the_600k_qualified_band`
(no paid before free, credits before paid, deepseek last, ≥600000);
`t3 head` → gemini head, nebius gone, paid[0] mistral kept;
`credit_last_and_gated` → priced/servable credits band vs unpriced gated tails;
usable 2-provider/3-legs → t1 pair exempt (D-TORDER-2);
`three_usable_carry` → nebius→groq/scaleway; privacy 2 → deepseek (openrouter removed);
BYOK 4 → paid removed, :free stays; FreeAi 2 → free_ai in-band, deepseek last;
meta-follows-free → servable only; `openrouter_serves_only_free` → only t1-clean
gated, rest removed; `run-tests.ps1` contexts → 1M/128k/256k per renders, regex
allows `:`, free-only `paidRe` allows `:free`.

## --check outputs (all exit 0)

- `python tools/registry.py check` → `ok: registry 2026-09-30, 36 routes, 80 models, 34 providers` (+ t1-clean info line), exit 0.
- `python tools/registry.py validate` → same, exit 0.
- `python tools/registry.py render omniroute --check` → `ok: render omniroute matches .../combos.json`, exit 0.
- `python tools/sync-ide-models.py --check` → `OK: opencode.jsonc, ... match ai-registry.json`, exit 0.
- `render litellm/ide/openhands/models-doc --check` → each `ok: ... matches ...`, exit 0.
- `python tools/audit-router.py --offline` → `combos: 26 (...)` + `no drift`, exit 0.
- `python tools/combo-contract.py` → 26 `PASS [LIVE 4168 models]`, `contract PASS: 26 combos`, exit 0.
- `opencode.jsonc` `gemini-3.8-flash`: `"limit": { "context": 1048576, "output": 65536 }` (measured).
- `tests/test_registry.py -q` → **301 passed**, 33 subtests passed, exit 0.
  Full `tests/ -q` on Windows: 2 collection errors (`test_hostexec_log/server`,
  `import fcntl` Linux-only, pre-existing) + timeout on full run; registry green.
  Base `e3436a4d` registry tests: 300 (1 known fail `credit_leg_is_last` from VTXLEG);
  after: 301 (+1 `ComboContractTests`), 0 fail.
- `powershell -File tests/run-tests.ps1 -Filter "combos.json"` → passed 245 failed 0, exit 0.
- `powershell -File tests/run-tests.ps1 -Filter "ai routing"` → passed 5 failed 0, exit 0.
- `configuration/omniroute/apply.ps1 -DryRun` → contract 26 PASS, exit 0
  (pre-existing `ConvertFrom-Json` casing warnings on missing api-keys.yml path only).
- shellcheck (container): clean on touched blocks; one pre-existing SC2218 at
  `tests/linux/17-ai-routing.sh:2056` (not touched). PSSA: no errors on touched
  `.ps1` (pre-existing Information + Write-Host/KeysMissing warnings only).

## Commits (9, one per task + OR rule + test adapt)

`018438ed` TASK1, `ba73f1cf` TASK2, `20c4a816` TASK3, `f0285616` TASK4,
`c4c3654b` TORDER-OR, `4f478ba2` TASK5, `b9229fff` TASK6, `2e67732c` TASK7,
`ff7e6b09` test-adapt. `git status --short --branch` clean on
`L1-backlog/ws-tier-order-20261001` at final commit (see DONE record).
