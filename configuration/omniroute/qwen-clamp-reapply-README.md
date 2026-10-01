# Qwen3-235b Output-Cap Clamp — Reapply Artifact

**Date:** 2026-09-30
**Script:** `qwen-clamp-reapply.ps1` (this directory)
**Branch:** `L1-backlog/ws-fixes-20260930`
**Gateway:** omniroute v3.8.50

## Why

Scaleway's `qwen3-235b-a22b-instruct-2507` rejects `max_completion_tokens` above
16384 with a 400 payload-validation error, which fails the whole request (so a
combo leg falls through):

```
400: max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507
```

Measured 2026-09-30: `max_tokens=32768` → 400, `max_tokens=16` → 200.

## What this script changes

The gateway resolves a per-model output ceiling from a **compiled static array**
in its Next build. Six chunk copies of that array exist; each has the azure-ai
gpt-4o-mini entry last. The script performs one 106-byte insertion per chunk:

```js
// before
{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384}];

// after
{provider:"azure-ai",match:/^gpt-4o-mini/i,maxOutputCap:16384},
{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}];
```

Chunks (exactly the set the live producer touched):

```
_08_y1bx._.js   _0o8_5h8._.js   _0t1t5fj._.js
_18ct13i._.js   _1j_edf1._.js   _1luyz1c._.js
```

`_14jycqh._.js`, `_15ose6x._.js` and `_1xkpq2s._.js` do **not** carry the array
and are not touched.

## Provenance

The live edit was delivered on 2026-09-30 (14:49Z) by an **untracked** diagnostic,
`AutoOS-ws-qwenclamp/logs/patch_dist.py`. Its OLD/NEW strings and its six-file
list match the live bytes exactly. Until this script existed, **no tracked file
contained `maxOutputCap` or `clampToModelMaxOutput`** (`git log --all -S
"maxOutputCap"` was empty), so a pristine reinstall plus the documented reapply
set could not rebuild the live state — see
`docs/handoff/2026-09-30-lanePatchIntegrity.md`.

This script ports that behaviour into the repository. It does **not** modify
`patch_dist.py`.

## Relationship to the qwen-clamp lane's tracked surfaces

`L1-backlog/ws-qwenclamp-20260930` already tracks two *other* expressions of the
same intent:

| Artifact | How it acts |
|---|---|
| `capability-overrides.json` + `apply-capability-overrides.ps1` | Applies the cap through the live management route `PATCH /api/model-capability-overrides`. Needs a running gateway. |
| `patch-gateway-clamp.ps1` | Removes the `supportsThinking` gate from the clamp helper so the cap applies to instruct models, not only reasoning models. |

Neither edits the compiled static array, which is why the +106 B live bytes were
unrepresented. This script closes that gap. All three are complementary: the
override supplies the cap value, the gate patch lets it apply to a non-reasoning
model, and this array entry is the compiled build's own copy of the ceiling.

## Line endings — the LF → CRLF note (read this)

The historical producer used Python `Path.write_text(...)` with its default
`newline=None`, which translates every `\n` to `os.linesep`. On Windows that
rewrote the whole file as CRLF. Measured result on the live install:

| chunk | live bytes | composition |
|---|---|---|
| `_0o8_5h8._.js`, `_0t1t5fj._.js` | +755 | +649 (LF→CRLF rewrite) **+** +106 (clamp entry) |
| `_08_y1bx._.js`, `_18ct13i._.js`, `_1j_edf1._.js`, `_1luyz1c._.js` | +106 (clamp) plus other lanes | clamp entry only; files later rewritten as LF by the deepseek/vertex patchers |

CRLF vs LF is semantically irrelevant in JavaScript. **This script preserves each
file's existing line endings** — it reads and writes text without normalisation —
and therefore does **not** reproduce the accidental CRLF rewrite. On a pristine
(LF) install it produces LF. That is deliberate: the CRLF was an artifact of one
tool's default, not part of the fix, and the live CRLF is itself now accounted for
as unexplained-by-design. The semantic clamp entry is what matters, and it is
reproduced exactly.

## Usage

```powershell
# From the AutoOS repository root:
pwsh -File configuration/omniroute/qwen-clamp-reapply.ps1

# Preview without writing:
pwsh -File configuration/omniroute/qwen-clamp-reapply.ps1 -DryRun

# Override the package directory:
pwsh -File configuration/omniroute/qwen-clamp-reapply.ps1 -PackageDir C:\dev\omniroute
```

The script backs each changed file up to `<file>.autoos-backup-<timestamp>`, is
idempotent (a second run reports `SKIP ... already patched`), exits non-zero if any
anchor is missing or found more than once, and prints a `patched / skipped /
errors` summary. Restart the gateway to load the change.

## Verification

Proved on a throwaway copy of the pristine `omniroute@3.8.50` tarball members
(host deletes Temp extraction trees, so members were streamed with `tar -xOf` /
`tar -xzf`):

* run 1 → `6 patched, 0 skipped, 0 errors`; run 2 → `0 patched, 6 skipped, 0 errors`
* under both `pwsh` 7.5.8 and Windows PowerShell 5.1

The full commands and output are quoted in
`docs/handoff/2026-09-30-lanePatchIntegrity.md`.

## Files

| File | Role |
|------|------|
| `qwen-clamp-reapply.ps1` | Idempotent reapply script (this patch) |
| `qwen-clamp-reapply-README.md` | This file |
| `capability-overrides.json` | Durable cap value (REST route) — other lane |
| `patch-gateway-clamp.ps1` | Un-gate the clamp helper — other lane |
| `reason-fix-reapply.ps1` | DeepSeek reasoning 400 fix reapply |
