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

31 file combos: 19 differ in legs/order, 7 missing live, 7 live-only extras (6 are "omitted" orphans, 2 unnamed in git: `ha-assist`, `l1-orchestrator-paid`).
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
3. `apply.sh` guard (gate change): a mutating run first reads live; if live differs from the committed snapshot (= live changes git never saw) it
   refuses, exit 4, naming the combos; `--overwrite-live <ref>` (same GO artefact as D-825) is the explicit way through. `--dry-run`/`--drift` unchanged.
   Unreadable live store keeps today's behaviour but now says so before writing (no silent delete against an unseen store).
4. Done state: registry fold + (approved) live apply, then drift = 0. Live apply is a quoted live-step packet to fixes-L1, never run by this lane.

## Daily check

systemd user unit `autoos-combo-drift.service` (oneshot) + `.timer` (`OnCalendar=daily`, `Persistent=true`), ExecStart wrapped:
`unit_breaker.py --unit autoos-combo-drift.service -- python3 tools/autoos_gateway_key.py exec -- python3 tools/combo_drift.py --alert`.
Not cron. Alert: one line to `$AUTOOS_ALERT_INBOX` on rc 1 (count + first 5 names), on rc 3 (unreadable); silent on 0. Breaker disables the unit after 5 identical failures.
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
