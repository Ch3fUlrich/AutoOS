# RT-P0-OR-DRIFT design note (2026-10-15)

Item: one source of truth for gateway combos; daily drift check = 0; `apply.sh` never silently undoes a live change.
Basis: rethink-setup design R2 (implication 4). Lane `lane/ao-rt-p0-or-drift` off origin/main 4314a00d2.

## Today's flow

- `catalog/ai-registry.json` -> `tools/registry.py` render -> `configuration/omniroute/combos.json` (generated, "do not edit").
- `configuration/omniroute/apply.sh` (real run, D-825 GO-gated) deletes + recreates every combo whose live legs differ from the file.
  A leg the live catalog does not know is skipped with a warning (second, legitimate source of difference).
- Live edits happen outside git: operator via gateway UI/CLI, probes, mimo wiring. Nothing exports them back.
- Detector exists but is manual: `apply.sh --drift` (read-only, rc 0/1/3). Not wrapped, not scheduled, not machine-readable.
  So the next `apply.sh` from main overwrites live-only legs and re-creates retired/orphan shapes with no warning.

## Measured drift (2026-10-15, `apply.sh --drift`, read-only, names only)

31 file combos: 19 differ in legs/order, 7 missing live, 5 in sync; plus 7 live-only extras (5 "omitted" orphans, 2 unknown to git: `ha-assist`, `l1-orchestrator-paid`).
- differ: l1-orchestrator(+clean,+free-only), l2-orchestrator(+clean), l2-researcher, l2-worker(+clean,+free-only), l3-driver(+clean,+free-only),
  l3-implementer(+clean), l3-researcher(+clean), l3-review-{diff,tests,codebase}.
- missing live: gemini-3.8-flash, l2-worker-1m, l3-driver-1m, l3-driver-256k, mimo-v2.6-flash, mimo-v2.6-pro, vertex-gemini-3.8-flash.
- Pattern: live carries mimo/nemotron legs the file lacks; file carries the vertex gemini leg live lacks.

## Direction: registry (git) stays the truth; live -> git is an EXPORT for review, git -> live is a GUARDED apply

Never auto-write the registry from live (live holds skipped/experimental legs; registry is rendered and tested). Instead:
1. `tools/combo_drift.py` (new, one read path = the existing `apply.sh --drift` normaliser, no second reader): runs the check, prints
   one line per difference (names + leg refs, no keys), writes `drift.json` (counts, names, sha256 of live export), exit 0 in sync / 1 drift / 3 unreadable.
2. `tools/combo_drift.py export` writes the live store to `configuration/omniroute/combos.live.json` (committed snapshot: name, strategy, ordered refs).
   A reviewer diffs it against `combos.json` and folds wanted legs into the registry (normal render + tests). The snapshot is the audit trail of what live held.
3. `apply.sh` guard (gate change): a mutating run first reads live and passes only if every live combo equals the committed snapshot (live changes are in git's
   audit trail) or equals the file (nothing to lose). Else it refuses, exit 4, naming the combos; `--overwrite-live <ref>` (same GO artefact as D-825) is the explicit
   way through. The guard compares to the snapshot, not to `combos.json`: apply exists to move live to the file, so a file comparison would refuse every real run.
   Unreadable live store, or any combo with a step the normaliser cannot read (dropped>0): refused too (exit 4) unless `--overwrite-live`; today's "replacing every
   combo" fallback ends. `--dry-run`/`--drift` unchanged. After an approved apply the packet re-exports, so the snapshot tracks the new live state.
   Export writes `combos.live.json` with `count` and `sha256` of the combo body; the guard recomputes both and refuses a snapshot that does not verify.
   Known gaps: `configuration/omniroute/drift-known-gaps.json` lists `{ref, reason}` legs (today `vertex/gemini-3.8-flash`, live vertex connection is L0's)
   that the check ignores on the file side, printed as `known gap`, so group D does not hold drift above 0 forever; an entry without a reason fails the check.
4. Done state: registry fold + (approved) live apply, then drift = 0. Live apply is a quoted live-step packet to fixes-L1, never run by this lane.

## Daily check

systemd user unit `autoos-combo-drift.service` (oneshot) + `.timer` (`OnCalendar=daily`, `Persistent=true`), ExecStart wrapped:
`unit_breaker.py --unit autoos-combo-drift.service -- python3 tools/autoos_gateway_key.py exec -- python3 tools/combo_drift.py --alert`.
Not cron. `--alert` mode: drift (rc 1) writes one line to `$AUTOOS_ALERT_INBOX` (count + first 5 names) EVERY day it persists and exits 0, so the breaker never
disables the check over persistent drift (the alert is the signal); an unreadable store (rc 3) or a crash exits non-zero, alerts, and may trip the breaker (5 identical
failures), which itself appends the breaker alert line. Silent on 0. Plain `combo_drift.py` (no flag) keeps exit 0/1/3 for scripts.
Units live in `configuration/autostart/`; installing them on a host is a live step (packet), not done here.

## Tests (failing first) and verify

`tests/test_combo_drift.py`: fake `omniroute` on PATH (real CLI contract: read the live_combo_norm lines) -> rc 0/1/3, export shape, no-secret output,
guard refuses on snapshot mismatch and passes with `--overwrite-live`; unit file parse. Verify per R-orch-18: `test_registry_render.py`, renderer `--checks`,
`tests/test_combo_contract.py`; route-id consumers grepped (R-orch-11: audit-router, autoos_resolver, probe-sweep, sync-router-tiers read combos.json, unchanged).
Seats: 2 non-Claude, different families (gate change, R-orch-16); design reviewed cross-family first (R-orch-13).

## Open choices (default in bold)

- Guard strictness: **refuse (exit 4) on unexported live change** vs warn-only.
- Snapshot file name/location: **`configuration/omniroute/combos.live.json`**.
- First convergence: **fold live-only mimo/nemotron legs into registry where wanted, then safe apply**; alt: apply git as-is (loses live legs).

## Per-leg keep/drop list (for fixes-L1's judgment; every ref below is already a registry model, so "fold" = add it to that route's `legs`)

Source: `apply.sh --drift` 2026-10-15, set difference per combo (order-only differences listed separately). Defaults are proposals.

| Group | Combos | Live-only leg(s) | Default | Reason |
|---|---|---|---|---|
| A | l1-orchestrator(+clean), l2-orchestrator(+clean), l2-researcher, l2-worker(+clean), l3-review-tests | xiaomi-mimo/mimo-v2.6-flash | fold | model rows exist since AO-MIMO-S2, tier legs were hand-added live |
| B | l1-orchestrator(+clean,+free-only), l2-orchestrator-clean, l2-worker-clean, l3-researcher-clean | nemotron-3-ultra / nemotron-3-super / bzl deepseek-v4-flash (free) | fold | free legs the registry knows, absent from these routes' legs |
| C1 | l3-driver, l3-driver-free-only, l3-implementer, l3-review-diff | groq qwen3.8-27b, groq gpt-oss-120b | fold | free-quota legs hand-added live |
| C2 | l3-driver-clean, l3-implementer-clean | 10-leg live pool (nemotron x2, groq x2, laguna-s-2.1, cohere x2, north-mini-code, ovh Qwen, bzl deepseek-v4-flash); the two differ only in ovh model | judge | looks like a hand-built clean pool; confirm "clean" policy admits each vendor before folding |
| D | 14 combos carrying `vertex/gemini-3.8-flash` last (every combo in A/B/C2 above, plus l3-researcher, l3-review-codebase, l3-driver, l3-implementer) | file-only leg (live lacks it) | keep in registry | live has no vertex combos either (see missing); gateway-side vertex connection repair is L0's (apply.sh B2-VERTEX), not a registry change |
| E | l2-worker-free-only | none, order only | drop live order | rendered order is the contract; live reorder is undocumented |

Missing live (7): `l2-worker-1m`, `l3-driver-1m`, `l3-driver-256k`, `mimo-v2.6-flash`, `mimo-v2.6-pro` -> keep, safe apply creates them; `gemini-3.8-flash`, `vertex-gemini-3.8-flash` -> keep, blocked on vertex (group D).
Live-only (7): `deepseek-v4.1-flash`, `spark-1.3-contributor`, `groq-qwen3.8-27b`, `opus-5-5`, `ovh-gpt-oss-120b` -> drop (registry "omitted", no servable leg; apply prunes);
`l1-orchestrator-paid` -> drop (same family as the retired `*-paid` ids; add to `retired`); `ha-assist` -> not AutoOS's: add an `unmanaged` list the drift check ignores and apply never touches.

## Update 2026-10-15 (gen-3, after fixes-L1 gen-11 rulings)

- `--overwrite-live` is a boolean flag (not `<ref>`); the exit-4 guard runs before any mutating step.
- D-1349: deepseek-flash stays a leg of the six `*-clean` routes (registry `CLEAN_LEG_EXEMPTIONS`, cites D-1349 + D-1201); deny-deepseek-pro stands. `deepseek-direct-flash` (opencode.jsonc hand entry) is judged via route `deepseek-v4.1-flash`, never exempt.
- Order: live wins, MiMo leads every non-clean chain that carries it (D-1351). combo-contract (b) has a named exemption: a subscription HEAD leg only (`tier_order_findings`).
- Not folded: groq (pruned live, D-1344; GROQRETIRED pin), nemotron-ultra into l1/l2 chains (cache gate (f)). Vertex is out of every managed chain (forbidden fleet-wide, absent live); only the pinned `gemini-3.8-flash` / `vertex-gemini-3.8-flash` routes keep it.
- `l3-driver-256k` is not live: dropped north-mini-code and reordered (MiMo, nemotron-super, bzl, laguna) so (g) diversity passes.
- LiteLLM: the six clean twins get groups holding only the deepseek leg; no new LiteLLM provider.
