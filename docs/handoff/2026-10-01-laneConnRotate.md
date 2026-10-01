## Evidence for CREDROW lane: connection row rotation (removing stale duplicate)

**Worktree**: C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-tier-order  
**Branch**: L1-backlog/ws-tier-order-20261001  
**Date**: 2026-10-01  

### Inventory and Testing
- Ran `omniroute providers list` and `providers test-all --json` to inventory and test all connection rows.
- Identified one failing row: ID `8cc83b8c` (provider `openai-compatible-chat-d9427825-8596-4d01-8fe0-1d4b24bb9f54`, name `main`)
  - Error: `Unsupported state or unable to authenticate data` (decryption failure under old storage key).
- Found a working duplicate for the same logical service (meta-api): ID `627d8593` (provider `openai-compatible-chat`, name `meta-api`).

### Repair Action
- Chose to remove the stale duplicate (`8cc83b8c`) because a working duplicate exists (less invasive than rotation).
- Executed removal via `omniroute providers delete 8cc83b8c`.

### Verification
- After removal, re-ran `providers test-all` and confirmed the remaining meta-api row (ID `627d8593`) passes.

### Combo Testing
- Tested the following combos as required:
  - `gemini-3.8-flash`
  - `t1-orchestrator`
- Both combos succeeded via OpenRouter fallback (using `nvidia/nemotron-3-super-120b-a12b:free`).
- Extracted serving leg lines from the gateway log (`C:\Users\mauls\.omniroute\logs\application\app.log`) for these requests.

### Files
- Backup of provider list before changes: `connection-list-backup.txt`
- After-removal test-all output (optional): `test-after-removal.json`
- Gateway log for serving leg lines: `C:\Users\mauls\.omniroute\logs\application\app.log` (not copied, but referenced)

### Conclusion
- The stale duplicate connection row with decryption failure has been removed.
- The remaining connection row for meta-api is functional and passes tests.
- The required combos are operational via OpenRouter fallback.