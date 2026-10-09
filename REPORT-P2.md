# REPORT-P2 — Vertex v3 mixed tool/text user-turn split (D-908)

Evidence: D-908 = `logs/briefs/evidence/vertex-capture-d908.md` (pointer only; no secrets copied).

## What it was
A signed tool call ending on `user` still 400s: the last `user` turn MIXES a `functionResponse`
with text (Vertex misreports "ending with a model turn"); v2 never looked inside a turn. Probes
made it a **split**: a synthetic assistant turn between tool result and user text 200s single
and parallel; folding text into the result 200s single but 400s parallel.

## Files changed
- `tools/apply-vertex-patch.py` — v3 transform; SKIP(v3)/PATCH(v2,v1,pristine)/ERROR; v2 anchor kept.
- `tools/vertex-patch-gate.py` — new `split` marker (the `Noted.` model turn); report line gains `split=`.
- `tools/vertex-trailing-turn-reapply.ps1` — v3 replacements + v2/v1/pristine anchors, chunks and `.ts`; regenerated from the patcher table.
- `tests/test_vertex_trailing_turn_patch.py`, `tests/test_vertex_patch_gate.py` — extended, not rewritten.
- `tests/linux/34-ai-services.sh` — vertex shard label/comments.
- `configuration/omniroute/vertex-trailing-turn-README.md` — v3 + D-908.
- `configuration/docker/ai-stack/omniroute.Dockerfile` — v3 guard comment.
- `configuration/docker/ai-stack/compose.yml`, `docs/web-services.md` — image tag `autoos4` -> `autoos5`.
- `CHANGELOG.md` — AO-VERTEX-LIVE entry.

## Red -> green
- RED (commit `5ca7e69`, tests only): trailing file 8 failed / 36 passed; gate file 1 collection error (no v2 form yet).
- GREEN: `python3 -m pytest tests/test_vertex_trailing_turn_patch.py tests/test_vertex_patch_gate.py -q` -> **69 passed**.
- `bash tests/run-tests.sh --filter vertex` -> **4 passed, 0 failed**; `shellcheck -S warning tests/linux/34-ai-services.sh` rc 0.

## Gate counts old -> new
- old: `total=12 patched=12 refill=12 v1=0 files=6 minimum=12`
- new: `total=12 patched=12 refill=12 split=12 v1=0 files=6 minimum=12`
- A 13th site left on the v2 guard now fails `split=0`; the arithmetic alone used to pass it.

## Consumers updated
Dockerfile build stage, `compose.yml` + `docs/web-services.md` tag, `CHANGELOG.md`,
`tests/linux/34-ai-services.sh`, README. Historical `docs/handoff`/`logs` left as history.

## Unverified
- No image build and no live gateway smoke (hermetic D-852: no docker, no network, no live
  package). `autoos5` is a tag string only; the autoos4 digest is untouched.
- The `.ps1` was exercised through `pwsh` over a fake package tree (v2->v3, 13 sites), never
  against a real Windows OmniRoute install.
