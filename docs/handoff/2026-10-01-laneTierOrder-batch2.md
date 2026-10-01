# Lane Tier Order Batch 2 — 2026-10-01 — evidence

Branch `L1-backlog/ws-tier-order-20261001`, base `e3436a4d`, HEAD builds on
L1-alpha `06319eb2` + `4fd66091` (no rebase; did NOT touch `.gitignore` or
`tests/test_credential_files_ignored.py`). Worktree only. No live apply
(`-DryRun` only).

Reviewer nonce (reviewers must echo): **B2-NONCE-C2A88E01**

## (1) Latency — knobs, values, why no wiring change

Measured chain: `t1-orchestrator` 22556 ms before `deepseek/deepseek-flash`
served (gemini 503 → free-ai 429 → vertex error → meta-api 401).

Live `omniroute api system get-api-resilience` (measured):
- `requestQueue.maxWaitMs` **180000** (queue wait for Spark thinking time, NOT per-leg;
  a 429 returns in <1 s and hops; 180000 sits under `comboCooldownWait.budgetMs` 300000).
- `providerBreaker.apikey`: `failureThreshold` **2**, `degradationThreshold` **1**,
  `resetTimeoutMs` **30000** (fast-skip: ≤2 cheap round-trips, 429 counts as degradation).
- `providerBreaker.oauth`: 8 / 5 / 60000.
- `connectionCooldown.apikey`: `baseCooldownMs` 3000, `maxBackoffSteps` 5
  (oauth 5000/8, `useUpstreamRetryHints` apikey true).
- `waitForCooldown`: enabled, `maxRetries` 3, `maxRetryWaitSec` 30.
- `comboCooldownWait`: enabled, `maxWaitMs` 90000, `maxAttempts` 5, `budgetMs` 300000.
- `providerCooldown`: **enabled false** (no 30-min park knob here).
- No per-leg timeout knob exists in this API surface (no `legTimeoutMs`).
  The 30-min hard-down park + 3×429/120 s→300 s cooldown + `CHAT_MAX_HEAVY=8`
  are gateway process env (08:07Z restart), not patchable via
  `patch-api-resilience` (apply only sets `requestQueue` + `providerBreaker`).

Current == proposed (180000 / 2 / 1 / 30000) — recorded in
`configuration/omniroute/apply.ps1` + `apply.sh` `B2-LATENCY` comments, no value
change. The crawl stops by REMOVING dead legs (B2-HF/B2-AGY below), shortening
t1-adjacent chains and t2/t3 bands, not by retuning. `requestQueue.maxWaitMs`
is NOT what makes an unreachable leg slow (it is queue wait, not per-leg).

## (2) Vertex credential (documentation only)

- Canonical credential: full content of
  `configuration/vertex-credentials-autoos-510210-9fdf2297df6f.json`
  (GCP service-account JSON, NOT a plain API key; git-ignored per
  `.gitignore` `configuration/vertex-credentials-*.json` (L1-alpha CREDIGNORE);
  expected at that path in the checkout; worktree absent, main present).
  Documented in `catalog/ai-registry.json` `providers.vertex_ai.$comment`
  (B2-VERTEX) + `apply.ps1`/`apply.sh` headers.
- L0 probed vertex works (gemini combo acked via vertex; direct 200 at 08:46:52Z).
- Live `providers list`: `vertex` (cc63eabf, active) + `vertex-partner`
  (2bb78365, active); `openai-compatible-chat meta-api` (627d8593, active) +
  `openai-compatible-chat-d9427825` main (8cc83b8c, active). L1-alpha measured
  one vertex attempt 401 `[Encryption] Decryption failed` while direct 200 —
  likely one undecryptable key among the two; credential-store repair is L0's.

## (3) HuggingFace removed

- `providers.hugging_face.available` → **false** with B2-HF reason (401 no active
  credentials, absent live). Declaration kept + documented (openrouter t1-clean pattern).
- Bands (t2/t2-fo/t3/t3-fo) DELETE 8 hf legs (2 each); singles `hf-glm-5.2`,
  `hf-qwen3.8-27b` KEEP declared legs so they land in `omitted` (not legless).

## (4) Antigravity removed now

- `providers.antigravity.available` → **false** with B2-AGY reason (rate-limited
  for days, dead head + latency sink) + revisit-later **2026-10-15**. Declaration kept.
- DELETE 4 legs: t2-worker high, t2-fo medium, t2-orchestrator opus, opus-4-6 opus
  (opus left `[cc]`, provider cc unavailable → omitted).

## (5) Omitted / prune outcome

- `render_omniroute`: `hf-glm-5.2`, `hf-qwen3.8-27b`, `opus-4-6` legs non-empty,
  `gateway_legs` empty → **`omitted`** (declared, none servable).
  `t2-orchestrator` renders `[deepseek]` (1M).
- `combos.json`: 26 → **23** combos; `omitted` 5 → **8**
  (+hf-glm, +hf-qwen, +opus-4-6).
- Prune mechanism EXISTS (no new code): `apply.ps1` prunes retired+omitted,
  `apply.sh` Prune block deletes retired+omitted. `-DryRun` proves it:
  `Prune: hf-glm-5.2 omitted would delete; hf-qwen3.8-27b omitted would delete;
  opus-4-6 omitted would delete` (exit 0). Tracked source and live store converge
  via existing prune; L1-alpha applies.

## (6) OVH window fix (measured)

- `models."Qwen3.8-27B".context_advertised` 262000 → **128000** (live gateway
  `Combo context limit: 128000 (source=combo-min)` + live `ovh/Qwen3.8-27B`
  128000; OVH catalogue 262K over-declared). Usable stays 64000 (50%).
- `routes.ovh-qwen3.8-27b` surfaces 262144/256k → **131072/128k**.
- Contract live check passes without exception.

## (7) Meta key context (report only)

- Operator confirms meta key lives in `api-keys.yml` under **`meta`** and the
  gateway `meta-api` connection already uses it. `meta-api/muse-spark-1.3-contributor`
  `401 Unauthorized` is NOT a missing key. Not chased.

## Re-render + checks (all exit 0)

- `render omniroute --check` 0 (23 combos, 8 omitted).
- `render ide/litellm/openhands/models-doc --check` 0; `sync-ide-models.py --check` 0
  (tier-profiles dropped `omniroute-opus-4-6`; litellm pruned hf blocks; ide dropped
  hf/opus 31→28 models; opencode/config.toml synced).
- `registry.py check/validate` 0 (36 routes, 80 models, 34 providers).
- `audit-router.py --offline` 0 (23 combos, no drift).
- `combo-contract.py` 0 (23 PASS, LIVE 4172).
- `test_registry.py` 301 passed.
- `run-tests.ps1 -Filter "combos.json"` 237/0; `-Filter "ai routing"` 5/0
  (ovh-qwen context 256k→128k + `:`/`paidRe` fixes for `:free`).
- `apply.ps1 -DryRun` 0 (contract 23 PASS; Prune lists hf/opus would-delete).

## Commits (batch 2)

`44f21cdb` B2-HF, `aced9915` B2-AGY, `60191348` B2-PRUNE (combos 23),
`ff0c57ee` B2-OVH, `42697105` B2-VERTEX docs, `f99df3c9` B2-LATENCY docs,
`266e16da` B2-RERENDER + test fix.
