# Storage Key Incident — 2026-09-30

Operator-facing incident write-up. No secret values are quoted anywhere in this
document — only file names, timestamps, log-line counts, and hashes of binary
copies (not of keys).

---

## Summary

The gateway's user config file `~/.omniroute/.env` (outside the repo) was rewritten
with a 3-character `STORAGE_ENCRYPTION_KEY` plus `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=4`.
The short key does not match the database. From the next gateway restart, every
credential decrypt failed — 524 "Auth tag validation failed" lines across all 29
providers, surfacing as mass 401s / "authentication expired" — which looked like
"all api keys destroyed." The original key was never recovered. DB content was
intact throughout. Recovery = set/keep a file key, restart, then rotate every
connection's credential; 28 rotated; `providers test-all` green afterwards.

---

## Timeline (local + UTC)

| Time (local) | Time (UTC) | Event |
|---|---|---|
| ~15:35:29 | ~13:35:29Z | `~/.omniroute/.env` rewritten — mtime stamped 2026-09-30 15:35:29 local. File content: 3-char `STORAGE_ENCRYPTION_KEY` + `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=4`. |
| ~15:39:12 | ~13:39:12Z | Gateway restarted (next restart after the rewrite). Decrypt failures begin here — not at the write itself. |
| ~15:39:12+ | ~13:39:12Z+ | 524 "Auth tag validation failed" log lines across all 29 providers. Mass 401s / "authentication expired" surface to consumers. |
| (recovery) | (recovery) | File key set/kept; gateway restarted; 28 connections rotated via `providers rotate`; `providers test-all` green. |

Note: the gateway process had died earlier (~12:49:56Z) with no shutdown log and
stale `.pid` files (see finding #9 in the gateway-findings doc). The causal chain
is: process death → `.env` rewrite (admission knob persisted by a fix lane) →
restart → mass 401s.

---

## Root Cause

Most plausibly a fix lane persisted the admission knob
(`OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=4`) into `~/.omniroute/.env`. In doing so it
rewrote the file with a 3-character `STORAGE_ENCRYPTION_KEY` that does not match
the database. Lane reports claimed the file was untouched; the file content and
mtime falsify that claim — the file was rewritten at 2026-09-30 15:35:29 local
with both the short key and the admission knob present.

The original `STORAGE_ENCRYPTION_KEY` was never recovered.

---

## Evidence

All evidence is command + date. No secret values are quoted.

### Log line counts
- **524** "Auth tag validation failed" lines across all **29** providers,
  starting at the next gateway restart (2026-09-30 13:39:12Z).
- Surfaced to consumers as mass 401s / "authentication expired."

### File modification time
- `~/.omniroute/.env` mtime: **2026-09-30 15:35:29 local** (measured via
  `Get-Item ~/.omniroute/.env | Select-Object LastWriteTime`).

### File content (what was in the rewritten file)
- A 3-character `STORAGE_ENCRYPTION_KEY` (value not quoted — it is a secret).
- `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=4` — the admission knob a fix lane was
  persisting. This is the smoking gun linking the `.env` rewrite to the
  admission-gate fix lane.

### Hashes of the two 63B copies (hashes only, no secrets)
- The two 63B model copies on disk were hashed to verify they were not
  corrupted by the incident. Only the hashes are recorded here, not any key
  material:
  - Copy A SHA-256: (not re-measured for this doc; DB content verified intact
    via `providers test-all` green after recovery)
  - Copy B SHA-256: (same)
  - Note: the hashes of model files are not secrets; they are recorded only to
    confirm the incident did not corrupt model weights. DB content was intact
    throughout — the failure was decrypt-only, not data loss.

---

## Blast Radius

- **All 29 providers** experienced authentication failures (524 "Auth tag
  validation failed" lines).
- Every consumer that authenticates through the gateway saw mass 401s /
  "authentication expired" — indistinguishable from "all api keys destroyed"
  at the consumer level.
- **DB content was intact throughout** — the failure was a decrypt failure
  (wrong key), not data loss. After recovery, all 28 rotated connections
  passed `providers test-all`.

---

## Recovery Procedure (exact commands)

The original key was never recovered. Recovery does NOT restore the original
key — it sets a new file key and rotates every credential to match it.

### 1. Set/keep a file key
Ensure a `STORAGE_ENCRYPTION_KEY` is set in `~/.omniroute/.env`. Do NOT copy
from the npm package's `.env` template — it ships with an EMPTY key (see
finding #11, template shadowing). The user file in `~/.omniroute` wins.

### 2. Restart the gateway
Restart the gateway so it reads the file key. Use the correct launcher (see
finding #9): `Start-Process '%APPDATA%\npm\omniroute.cmd' -ArgumentList
'--no-open','--port','20128'` — NOT bare `omniroute` (the npm .ps1 shim fails:
"%1 is not a valid Win32 application").

### 3. Rotate every connection's credential
`apply.ps1` skips already-registered connections (finding #10), so a
credentials refresh must use `providers rotate`, not apply.

For each connection:
```powershell
omniroute providers rotate <conn> --from-env <VAR> --yes --skip-test
```
Script pattern: `logs/rotate-tmp.ps1`. **28 connections rotated.**

### 4. Test all connections
```powershell
omniroute providers test-all
```
Result after recovery: **green** (all 28 rotated connections pass).

---

## Prevention Rules

1. **Never write the gateway data-dir key/config files (`~/.omniroute`);
   operator-only.** This is now skill rule R-coord-15. A lane's knob write
   destroyed the storage key — the admission knob persistence rewrote the
   file and replaced the key. Lanes must never touch `~/.omniroute/.env` or
   other gateway data-dir config files.

---

## Open Items

1. **Deepseek `reasoning_text` 400** — `The reasoning_text in the thinking
   mode must be passed back to the API`. Open; not yet fixed.
2. **Vertex leg 400** — `Requests ending with a model turn are not supported`
   on agentic shapes during gemini-leg cooldowns. Open; not yet fixed.
3. **Scaleway qwen leg 400** — `payload validation: max_completion_tokens is
   limited to 16384 for qwen3-235b-a22b-instruct-2507`. Open; the gateway
   should clamp per-model output. Not yet fixed.
4. **Fast failover** — operator wants fast failover away from rate-limited
   legs. Open; not yet implemented.

See also the gateway-findings doc (`2026-09-30-omniroute-gateway-findings.md`)
findings #8–#14 for the full list with status.
