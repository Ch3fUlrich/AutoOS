# DONE-W1: Router Sync Branch Landing

**Branch landed:** `merge/router-sync-openhands` onto `merge/w1-sync-rebase` (base `main` @ `7776c85`).

## Commits Applied
- Added new router tier sync script `tools/sync-router-tiers.py`.
- Updated LiteLLM router configuration (`configuration/litellm/config.yaml`) with the latest tier‑2 and tier‑3 model blocks (managed by `sync-router-tiers.py`).
- Updated docs:
  - `docs/handoff/DONE-L2B.md` (previous hand‑off).
  - `docs/models.md` pointer update.
- Adjusted test suite files (`tests/run-tests.ps1`, `tests/run-tests.sh`) to reflect new configuration.

## Verification
- `python tools/sync-router-tiers.py --check` → exit code `0`.
- `python tests/check-vendored.py` passed.
- Linux test filter `bash tests/run-tests.sh --filter 'sync|apply|combos'` passed with **0 failures**.
- Windows test filter equivalent passed (no conflicts).

## Remaining Work / Refusals
- **Do not resurrect** the plain `muse-spark-1.3` legs – blocked by operator policy (2026‑09‑21).
- **Do not re‑add** the retired `llama-3.3-70b` assertion – also blocked.
- No further changes to `tests/run-tests.*` needed; they remain clean.
- No permission prompts were encountered during this merge.

## Next Steps
- Continue development on `merge/w1-sync-rebase` as needed.
- Any future router updates should be applied via `tools/sync-router-tiers.py` to keep the managed blocks in sync.

*Commit recorded by the tier‑2 worker.*
