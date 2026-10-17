# Gateway Quota Null-Safety Patch

## Purpose and Context

In OmniRoute 3.8.50, provider usage tracking for Vertex AI (`open-sse/services/usage/vertex.ts`) returns a self-tracked `spend` quota window (`costUsd` via `getConnectionSpendUsdSinceAdded`):
```json
{
  "spend": {
    "used": 1.5,
    "displayName": "Spend (USD)",
    "quotaSource": "localUsageHistory",
    "resetAt": null,
    "unlimited": false
  }
}
```
This window contains `used`, but deliberately contains **no `total`** and **no `remainingPercentage`** (spend is cumulative and unbounded by a fixed cap).

## Root Cause Diagnosis

1. **Default to 0%**: In `src/domain/quotaCache.ts` (`normalizeQuotas` and `setQuotaCache`), `remainingPercentage` was computed as:
    ```javascript
    safePercentage(q.remainingPercentage) ?? (q.total > 0 ? Math.round(((q.total - (q.used || 0)) / q.total) * 100) : 0)
    ```
    When `total` was missing or zero, this defaulted to `0` instead of `null` (unknown).
2. **False Exhaustion**: `isExhausted` evaluated:
    ```javascript
    entries.every((q) => q.fractionReported !== false && q.remainingPercentage <= 0)
    ```
    With `remainingPercentage = 0`, `0 <= 0` was `true`. For Vertex AI, `spend` was the only window, so the entire connection was flagged `exhausted: true`.
3. **5-Minute Blackout**: With `resetAt: null`, `isStandardQuotaExhausted` held the account exhausted for `EXHAUSTED_TTL_MS = 5 * 60 * 1000` (5 minutes). This occurred every 70 minutes upon quota refresh, causing `503 ALL_TARGETS_SKIPPED` across all Vertex requests.
4. **Snapshot Persistence & Reload Gaps**:
    - In `setQuotaCache`, `let windowExhausted = remainingPercentage <= 0` evaluated `null <= 0` to `true`, persisting total-less windows as `is_exhausted: 1`.
    - In `hydrateQuotaCacheFromSnapshots`, `e.remainingPercentage ?? e.remaining_percentage ?? 0` coerced `null` to `0`, re-arming the 5-minute lockout on process restart.
    - In `getQuotaWindowStatus`, `clampPercent(null)` returned `0`, tripping `reachedThreshold: true` for total-less windows.

## The Fix

`tools/apply-gateway-quota-patch.py` applies a loud-fail substitution patch across the 12 compiled Next.js chunks in `dist/.build/next/server/chunks/`. Validation of all files is all-or-nothing (nothing is written if any file fails validation); the write itself is per chunk in two phases (backups and temporary files first, then renames); a failure part-way through the renames can leave earlier chunks patched; a re-run converges:

1. **Quota Computation**: When `total <= 0` or is absent, `remainingPercentage` evaluates to `null` (unknown) rather than `0`:
    ```javascript
    VAR.total > 0 ? Math.round((VAR.total - (VAR.used || 0)) / VAR.total * 100) : null
    ```
2. **Case H Exhaustion Decision**: Windows without a valid total are **ignored** in the exhaustion decision (not mapped to 100). A connection is exhausted iff **at least one window has a valid total and EVERY window that has a total is exhausted**:
    ```javascript
    t.every((e,_,a)=>a.some(w=>null!=w.remainingPercentage)&&(null==e.remainingPercentage||(!1!==e.fractionReported&&e.remainingPercentage<=0)))
    ```
    A real 0% window stays exhausted even next to a total-less one (`B + C => exhausted`). 50% + 0% is NOT exhausted because the 50% window fails the `every`. If there is no window with a valid total at all, the connection is not exhausted.
3. **Snapshot Persistence**: Total-less windows are persisted as not exhausted with null remaining:
    ```javascript
    let exh_var = null != rem_var && rem_var <= 0;
    ```
4. **Snapshot Reload**: Saved snapshot reload keeps null remaining percentage as null on restart:
    ```javascript
    remainingPercentage: null == (e.remainingPercentage ?? e.remaining_percentage) ? null : clamp(Number(e.remainingPercentage ?? e.remaining_percentage))
    ```
5. **Window Status**: `getQuotaWindowStatus` preserves null as unknown and evaluates `reachedThreshold` to false for total-less windows:
    ```javascript
    let rem = null == w.remainingPercentage ? null : clamp(w.remainingPercentage),
        used = null == rem ? null : clamp(100 - rem);
    reachedThreshold: !expired && !1 !== w.fractionReported && null != rem && (rem <= 0 || used >= thresh)
    ```
6. **MCP Server Bundle**: `dist/open-sse/mcp-server/server.js` is a standalone MCP server bundle used by the separate MCP entry and is **NOT patched**; it keeps the old exhaustion logic.

### Behaviour on Standard Inputs

| Input | Description | `remainingPercentage` | `isExhausted` |
|---|---|---|---|
| **A** | `total > 0`, 40% remaining | `40` | `false` (not exhausted) |
| **B** | `total > 0`, 0% remaining | `0` | `true` (exhausted) |
| **C** | `total` absent/null/0/NaN/negative, `used > 0` | `null` | `false` (not exhausted) |
| **D** | No window (`{}`) | `null` | `false` (not exhausted) |
| **E** | `total > 0`, 100% remaining | `100` | `false` (not exhausted) |
| **50% + 0%** | `total > 0` on both, 50% remaining and 0% remaining | `50` / `0` | `false` (not exhausted) |
| **0% + 0%** | `total > 0` on both, 0% remaining and 0% remaining | `0` / `0` | `true` (exhausted) |
| **H1** | Real 0% + total-less (`B + C` together) | `0` / `null` | `true` (exhausted) |
| **H2** | `A + C` together (40% window + total-less window) | `40` / `null` | `false` (not exhausted) |
| **Overuse** | `used: 150`, `total: 100` | `-50` | `true` (exhausted) |
| **Near-cap** | `used: 999`, `total: 1000` (rounds to 0%) | `0` | `true` (exhausted) |

## Tool Usage

```bash
# Check mode (dry run; reports planned actions, writes nothing)
python3 tools/apply-gateway-quota-patch.py --root /path/to/omniroute --check
python3 tools/apply-gateway-quota-patch.py --root /path/to/omniroute --dry-run

# Apply with automated backup
python3 tools/apply-gateway-quota-patch.py --root /path/to/omniroute

# Apply without backup
python3 tools/apply-gateway-quota-patch.py --root /path/to/omniroute --no-backup

# Re-run (idempotent, skips all 12 chunks cleanly)
python3 tools/apply-gateway-quota-patch.py --root /path/to/omniroute
```

- **Two-Phase Write & Convergence**: The script validates everything first, then writes in two phases (backups and temp files, then renames). A rename failure part-way leaves earlier chunks patched and `.autoos-tmp-*` leftovers; a re-run converges.
- One patched chunk + eleven originals is patched to completion and exits 0 (it converges); a half-patched FILE is an error and writes nothing.
- **Backups**: A run that patches nothing writes nothing (no backup churn). A run that patches creates a timestamped backup (`<chunk>.autoos-backup-<timestamp>`) for each modified chunk before writing new content (unless `--no-backup` is passed).
- **Chunk Discovery by Signature**: Scans chunks for `__omnirouteQuotaCacheState`. If any chunk contains the signature but is not in the handled set (e.g. after an unexpected package upgrade that renames chunks), it fails loudly.
- **Upgrade Support**: Automatically detects chunks patched by an earlier version of this script (`(e.remainingPercentage??100)<=0`) and cleanly upgrades them to the new null-safe implementation, as well as applying cleanly to the original unpatched package.
- **Strict Argparse**: Requires `--root` (fails loudly if missing, empty, or non-existent); unknown flags result in immediate non-zero exit.

## Known Limits
- a package patched by the v2 version of this script is NOT upgraded (the script aborts and writes nothing; v2 was never applied live);
- the patch takes effect only after a gateway restart and the restart reloads the saved snapshot (a persisted row from the old process that says exhausted and is under 5 minutes old still reloads exhausted);
- `.autoos-tmp-*` temporary files left by a failed rename persist even after a successful re-run until they are removed by hand;
- the mcp-server copy (dist/open-sse/mcp-server/server.js) is NOT patched and keeps the old logic;
- discovery scans top-level .js files only;
- argparse accepts unambiguous abbreviations;
- a total of Infinity gives a NaN remaining and the window status reports the threshold reached.

## Documented Test Gaps
- no test feeds CRLF-encoded chunk input (byte preservation is tested for LF input);
- no test simulates a rename failure;
- the persisted row of a real 0% window (is_exhausted 1) is not asserted;
- the fractionReported:false behaviour is pinned by a text check of the patched expression rather than a behavioural test.

## Central Image (prox) RUN Line (UNVERIFIED)

> **Note: UNVERIFIED** — The exact image filesystem layout and omniroute installation path in the central container image are unverified: the image layout is not known. Verify the container paths before applying.

```dockerfile
RUN python3 <path to tools/apply-gateway-quota-patch.py in the image (UNVERIFIED)> --root <omniroute package root in the image (UNVERIFIED: the image layout is not known)>
```
