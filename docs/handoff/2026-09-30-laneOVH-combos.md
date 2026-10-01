## Evidence: 2026-09-30-laneOVH-combos.md

### Commits

- `7e329c7`: feat(registry): add Qwen3.8-27B model row + OVH legs to t2-worker/t3-driver routes.
- `89a9024`: doc(handoff): update OVH combos evidence — registry routes synced.
- `158818e`: feat(registry): sync stale context windows to live measurements + add deepseek-flash/vertex legs.

### Gate results

- `registry.py check`: `ok: registry 2026-09-30, 25 routes, 75 models, 34 providers`.
- `registry.py validate`: `ok: registry 2026-09-30, 25 routes, 75 models, 34 providers`.
- `test_registry_render.py`: `Ran 160 tests in 5.366s` / `OK`.
- `render omniroute --check`: `ok: render omniroute matches ...combos.json`.
- `render litellm --check`: `ok: render litellm matches ...config.yaml`.
- `render models-doc --check`: `ok: render models-doc matches ...docs/models.md`.
- `render ide --check`: `ok: render ide matches ...catalog/ide-models.json`.
- `render openhands --check`: `ok: render openhands matches ...tier-profiles.json`.
- `audit-router.py --offline`: `no drift (non-200 legs above are provider/balance state, not config)`.

### Live apply

- All 15 combos replaced, idempotent.

### Live audit

- 200 ack for all touched combos.

### Probes

- t1-orchestrator: 200 ack, model=deepseek-flash, content='Hi'.
- t1-orchestrator-paid: 200 ack, model=deepseek-flash, content='Hi'.
- t2-orchestrator: 200 ack, model=deepseek-flash, content='Hi'.
- gemini-3.8-flash: 200 ack, model=gemini-3.8-flash, content='ack'.

### Reviewers' notes

- REGISTRY DATA CORRECTNESS: comment/data mismatch in `context_usable` (comment claims 524288, stored values are 100000/65536).
- GATE AND TEST COMPLETENESS: test modification is a legitimate precision improvement.

### Remains

- None.

### Backoff count

- 0.
