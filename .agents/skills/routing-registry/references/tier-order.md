# Tier order and windows (TORDER) — the folded predecessor rule

This is the folded-in predecessor skill's rule, kept in substance (TORDER
2026-10-01,
D-TORDER-1b). It governs every leg curation in `catalog/ai-registry.json`.
Code, tests and apply scripts reference the skill; they do not restate it.

## Rule

### t1 band = `>= 600000`, renders 1M

`l1-orchestrator`, `l1-orchestrator-free-only`, `l1-orchestrator-paid`,
`l1-orchestrator-clean`: every leg window `>= 600000` (explicit constant
`T1_MIN_WINDOW = 600000` in `tools/combo-contract.py`; rendered result is still
1M). Current 1M legs still render 1M, so only the gate constant changed.
l1-orchestrator band: `gemini/gemini-3.8-flash` (free head),
`free_ai/google/gemini-3.8-flash` (second free 1M), `vertex/gemini-3.8-flash`
(credit), `meta_api/muse-spark-1.3-contributor` (paid),
`deepseek/deepseek-flash` (paid LAST).

Free-only twin: L0 D-TORDER-2 ACCEPT — **deliberately single-provider**
(`gemini` only; no verified genuinely-free live 1M tool-calling second exists);
real load on `l1-orchestrator` vertex credits. A single-provider tier is
allowed ONLY as a documented, gate-exempted constraint (registry note +
`SINGLE_PROVIDER_EXEMPTIONS` in `tools/combo-contract.py` +
`tests/test_registry.py` asserting the note). Verify each leg in live
`/v1/models` before adding; if impossible without inventing, keep what exists
and report D-TORDER-2 with measured windows.

D657-CHAIN 2026-10-08 (AO-DENYLEGS D2) holds the same exemption on the same
measured basis: free-ai 429s `premium_requires_purchase`, huggingface is
unusable (401, no active credentials), openrouter `:free` ids top out at
262144 (below the 600k floor), and vertex is a credit grant.

### t2/t3 order = trial -> free -> credits -> paid, DEEPSEEK LAST; 128k clamp on the legacy bands only

Free band head `gemini/gemini-3.8-flash`, then groq/huggingface/openrouter/
free_ai legs; `scaleway/*` and `antigravity/*` kept but NEVER heads. Credits
after all free: `vertex/gemini-3.8-flash` (NEW to t2/t3 full). The `ovhcloud/*`
credit legs are gone — the provider was removed 2026-10-18 (D-1816(12),
credits empty), so **no OVH leg may be curated anywhere**. Paid: `meta_api`
then `deepseek` LAST. No free leg after a paid leg. `-free-only` twins take NO
credit/paid leg.

The legacy bands (`l2-worker`, `l2-worker-free-only`, `l3-driver`,
`l3-driver-free-only`) render `128k` (lowest implementer window, do NOT
raise); sub-1M legs allowed. The D657 layer twins (`l2-orchestrator`,
`l3-researcher`, `l3-review-*`, + `-clean` twins) carry their OWN declared
windows (1M) and sit outside rule (d) — contract `T2T3_ROUTES` names only the
four legacy combos; the cache rule (contract (f), orchestrator-layer
`l0-`/`l1-`/`l2-`) is what binds orchestrator-layer combos. **Never infer a
window from the tier name — read the route's declared limit.**

### gemini context/output

`models.gemini-3.8-flash`: `context_advertised` 1048576, `output_max` 65536
(live), `context_usable.tokens` 1048576 (source `default`, operator update 4).
Twin `models.google/gemini-3.8-flash`: 1048576 / 65536 / usable 524288 (50%).
`routes.gemini-3.8-flash` legs `[gemini, vertex, openrouter (gated/removed per
OR rule)]`, `surfaces.omniroute.context` 1048576, label `1M`.
`providers.google_ai_studio.available` true with backoff note: "gemini
retained; usage governed by the repeated-429 backoff policy (3x429/120 s ->
300 s cooldown per leg; 30 min park)" (gateway 08:07Z ACTIVE).

### openrouter NO credits -> `:free` only

Every servable openrouter leg MUST end `:free`. Every declared paid
`openrouter/*` leg that survives MUST be gated in
`routes.<id>.unavailable_legs`. `providers.openrouter.available` stays `True`
(gating paid models, not provider, so `:free` still routes).
`l1-orchestrator-clean` keeps declared+gated paid leg so it stays `omitted`,
not legless. **Never set provider available:false** (drops `:free` too).

### New credit singles (class credit)

`vertex-gemini-3.8-flash`: single leg, strategy priority, `surfaces.omniroute`
(clients `[opencode]`, context/declared, display_name, output; no
openhands_profile per groq/hf precedent). (`ovh-qwen3-coder-30b` was retired by
OVHCODER-DROP 2026-10-08 — OVH withdrew the model upstream, live 404 — and
`ovh-qwen3.8-27b` / `ovh-gpt-oss-120b` were removed with the whole `ovhcloud`
provider 2026-10-18 (D-1816(12)): no OVH credit single exists any more.)

### Every leg add/remove is reasoned and dated

The reason lives in the route/model `$comment` (e.g. `ORQWEN-DEAD`,
`SCWREMOVAL`) AND in the availability ledger
(`python tools/probe-ledger.py record --leg <provider/model> --status
<served|failed|limited> --reason <text>`); `logs/routing/availability.json` is
the machine-readable mirror (git-ignored runtime state). No silent churn.

## Gate (fail-closed)

`python tools/combo-contract.py` asserts for every combo:

- (a) client limit == registry context == combos context (ladder floors);
- (b) trial -> free -> credits -> paid, paid last, no free after paid, deepseek
  last, openrouter servable `:free` + declared-paid gated;
- (c) every leg resolves (live existence warn-only: apply skips unknown with
  warning, exit 0, so the gate must not fail on dead huggingface/antigravity
  or the dry run (`-DryRun` on apply.ps1, `--dry-run` on apply.sh) breaks);
- (d) t1 1M + `>=600k`, legacy t2/t3 (l2-worker, l3-driver bands) 128k; D657
  layer twins carry own windows.

Wired fail-closed (non-zero exit, also under the dry run: `-DryRun` on
apply.ps1, `--dry-run` on apply.sh): `configuration/
omniroute/apply.ps1` + `apply.sh` (before creating anything), CI
(`.github/workflows/ci.yml` lint job), harnesses (`tests/run-tests.ps1`
registry drift case, `tests/linux/17-ai-routing.sh`), pytest
(`tests/test_registry.py` ComboContractTests via subprocess).

Green gate, then GO, then apply — the gate is the last check before the GO, not
a formality after the apply.
