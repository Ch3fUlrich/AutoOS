# DONE — run `ws-omniroute-20260930` (L1-alpha), closed 2026-10-01

Carrier: `L1-backlog/ws-tier-order-20261001` — tip **`bd0ad278`** (25 commits from base
`e3436a4d`), taken into `main` as **`11757db4`**. This note closes the run; every claim
below was measured on the live host or on the merged `main` checkout.

## Delivered

- **Routing order** — trial → free → credits → paid, **`deepseek` last** of all, on
  `t1-orchestrator`, `t2-worker`, `t3-driver` and the free-only twins. `scaleway/*` and
  `antigravity/*` kept but never heads. `vertex/gemini-3.8-flash` added to the credits band
  of t1/t2/t3.
- **Tier windows** — t1 keeps only legs with a measured window **≥ 600000** and renders
  **1M**; t2/t3 stay **128k** (the lowest implementer's clamp).
- **Render-source fix for the 128k-compaction bug** — `routes.gemini-3.8-flash` context
  131072 → **1048576** with the `gemini/gemini-3.8-flash` free head restored;
  `models.gemini-3.8-flash` `output_max` 32768 → **65536**, `context_usable.tokens` →
  **1048576**; `providers.google_ai_studio` re-opened under the repeated-429 backoff policy;
  all five surfaces re-rendered so `render omniroute --check` exits 0 and `opencode.jsonc`
  carries `limit { context: 1048576, output: 65536 }`.
- **No paid openrouter leg is routable** — all declared paid legs removed or left gated;
  `providers.openrouter` stays available for the 22 `:free` legs.
- **Four credit single-combos** — `ovh-qwen3.8-27b`, `ovh-gpt-oss-120b`,
  `ovh-qwen3-coder-30b`, `vertex-gemini-3.8-flash`.
- **Unusable providers removed** — 10 `huggingface/*` + 4 `antigravity/*` legs, providers
  marked unavailable-with-reason plus a revisit note, declarations kept gated;
  `hf-glm-5.2`/`hf-qwen3.8-27b`/`opus-4-6` render `omitted` and the live apply **pruned**
  all three stale combos.
- **Combo-contract gate** `tools/combo-contract.py`, fail-closed in `apply.ps1`/`apply.sh`,
  CI and the test harnesses: per combo it asserts client limit == registry context ==
  `combos.json` context, the free→credits→paid order with paid last, leg resolvability
  against the live catalog, the t1 ≥600k / t2-t3 128k windows, and openrouter
  `:free`-only. Documented as the **`combo-create`** skill rule.
- **Secrets** — `configuration/vertex-credentials-*.json` (a live GCP service-account key)
  was untracked-but-UNIGNORED; now git-ignored with a red-first gate test
  `tests/test_credential_files_ignored.py`. `main` was protected immediately via the
  untracked `.git/info/exclude` guard until the branch landed.

## Live proofs (serving leg + latency)

| Probe | Serving leg | Latency |
|---|---|---|
| `t2-worker` | free `groq/qwen/qwen3.8-27b` | **629 ms**, 0 fallbacks |
| `t1-orchestrator` | credit `vertex/gemini-3.8-flash` | **6706 ms** / 1 fallback (was 22556 ms / 3 fallbacks → paid deepseek) |
| `ovh-qwen3.8-27b` | `ovh/Qwen3.8-27B` | 23455 ms upstream, 0 fallbacks |
| `vertex-gemini-3.8-flash` | `vertex/gemini-3.8-flash` | 2171 ms, 0 fallbacks |
| `spark-1.3-contributor` | `meta-api/muse-spark-1.3-contributor` | 7996 ms, 0 fallbacks (post-repair) |

Post-restart probes, all PASS: `probe-clamp` (over-cap qwen3-235b → 200, no cap-400),
`probe-vertex` (8/8 `[200]`, zero `400`), `probe-reasoning-repro` (4/4, no 400).

## Certified on the merged `main` (`11757db4`)

- `python tools/registry.py render omniroute --check` → **ok, exit 0** (the nebius/gemini
  drift is gone).
- `python tools/combo-contract.py` → **contract PASS: 23 combos (LIVE 4161 models)**, exit 0.
- `python tools/registry.py check` → ok, 36 routes / 80 models / 53 providers, exit 0.
- `tests/run-tests.ps1 -Filter combos.json` → **passed 236, failed 0, skipped 0**, exit 0.
- `git status` in `main` → **clean**; the three loose root artifacts moved to the ignored
  `logs/probe-artifacts/`.

## Honest ledger

- **CREDROW**: removed the genuinely undecryptable connection row `8cc83b8c`; `meta-api`
  serves again (7996 ms). Its own evidence was partly false — an impossible "OpenRouter
  fallback" serving leg, backup files that do not exist, and an unproven "meta-api
  duplicate" classification — all corrected in-file by L1-alpha (`bd0ad27`).
- **gemini-restore** left its ack as an honest **SKIP** (401/no key in a public-only
  worktree, zero completions; no hammering).
- **TORDER attempt 1** landed nothing (worktree clean); attempt 2 delivered.
- **No per-leg timeout knob exists** in the gateway; the crawl was fixed by removing dead
  legs and letting the 30-min park skip them.

## Review

Docs-only closeout commit reviewed by a **free family**: `opencode/longcat-2.5-preview-free`,
session `ses_f091c6fe0ffensOsHHC3YUlF0C`, nonce `CLOSEOUT-NONCE-7Yq3Zm8K` quoted back →
**APPROVED**. It independently reproduced: main's
`render omniroute --check` exit 0, `combo-contract.py` `23 PASS (LIVE 4161 models)`,
`registry.py check` (36/80/53), `run-tests.ps1 -Filter combos.json` `236/0/0`, the
25-commit count and the `bd0ad278 → 11757db4` take, `providers.google_ai_studio`
`available: True`, the ignored-and-moved artifacts, `test_credential_files_ignored.py`
3/3, and **0** secret-pattern matches across the three docs. It found no internal
contradictions and listed only point-in-time live measurements and historical session ids
as unreproducible (each corroborated in-repo). First reviewer
(`openrouter/nvidia/nemotron-3-super-120b-a12b:free`) died on a provider error with no
verdict — recorded, not counted as a pass.

## Open items (not owned by this track)

1. `free-ai/google/gemini-3.8-flash` is `premium_requires_purchase`, not free — recorded as
   the accepted constraint making `t1-orchestrator-free-only` deliberately single-provider
   (`SINGLE_PROVIDER_EXEMPTIONS`).
2. `ovh/Qwen3.8-27B` upstream latency (~23 s for a cold single-leg call) is a provider
   characteristic, not a chain crawl.
3. One credential-store repair class remains possible for any future row that re-encrypts
   under a superseded `STORAGE_ENCRYPTION_KEY`; the one live instance is resolved.
