# DONE — lane `ws-renderfix-20261001` (RENDERFIX)

**Branch** `L1-backlog/ws-renderfix-20261001`, base **`75af3236`** (the operator's pushed main
at hand-off time), worktree `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-renderfix`.

## What was wrong

`tests/test_registry_render.py` was **red at the TORDER wave's own carrier tip** (`11757db4`)
and stayed red through the researcher-tier merge and every take after it. It was never run by
the wave: the wave's verification covered `tests/test_registry.py` and the
`run-tests.ps1 -Filter combos.json` case, not the render-expectation file. Measured at three
revisions (`e3436a4d` 6 failed → `11757db4` 15 failed → `75af3236` 15 failed, identical
names), so **none of the 15 is merge-caused**.

## Commits

- `274d9c42` — `test(combos): pin t4-researcher context 128k in the combos gate` — the
  researcher lane's route had no `$contexts` entry, so the combos gate reported
  `expected [] but got [128k]`.
- `ec59a3b5` — `test(render): update 15 stale render expectations to the post-TORDER registry`
  (107+/59−) — the 15-row table is in `docs/handoff/2026-10-01-laneRenderFix.md`; **all 15 are
  (A) stale expectations and zero are (B) defects**, including the `xhigh` case, which resolved
  as (A) (gemini's ladder legitimately excludes it, so the render correctly drops the default).

## Verified on `ec59a3b5`

- `pytest tests/test_registry.py tests/test_registry_render.py tests/test_registry_loader.py`
  → **472 passed / 0 failed** (was 15 failed).
- `registry.py render omniroute --check` → exit 0 · `combo-contract.py` → **24 PASS (LIVE
  4161)** · `registry.py check` → ok (37 routes / 80 models / 53 providers).
- `run-tests.ps1 -Filter "combos.json"` → **242 passed / 0 failed**.
- `render litellm|ide|openhands|models-doc --check` → all exit 0.
- `.gitignore` and `tests/test_credential_files_ignored.py` untouched; no live apply; nothing
  written to the operator's `main` checkout (only read-only commands there).

## Guard failures of the retired attempts (recorded)

The first two attempts wrote to `C:\Users\mauls\Documents\Code\AutoOS` (the operator's main
checkout) and the first also switched its branch; both were halted, their WIP preserved
(`…\Temp\opencode\renderfix-wip.patch`, `renderfix2-partial.patch`) and reviewed, and this work
was finished by L1-alpha directly with no subagent — the branch is checked out only in this
worktree, which is the fence that closes the failure mode.

### CHANGELOG bullet

- `test(render): update the 15 stale `tests/test_registry_render.py` expectations to the
  post-TORDER registry (gemini head restored, antigravity/huggingface unavailable with their
  legs removed, `free-ai/qwen7b` mid-band rather than last, t1 the honest 1M band, t2/t3 the
  128k clamp) and pin `t4-researcher` `128k` in the combos gate — `pytest` render trio 15
  failed → **472 passed / 0 failed**, with `render --check`, `combo-contract` (24 PASS) and the
  combos ps1 filter (242/0) all green.`
