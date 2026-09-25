# Changing the runner

Moved out of `SKILL.md` (routing v2 spec §8.1, C2). `SKILL.md` links here; nothing in this file
is restated there.

Tests are the contract, and all test suites must stay green:

```bash
pwsh -File skills/unattended-orchestration/tests/AdapterContract.Tests.ps1 # adapter schemas & vectors
pwsh -File skills/unattended-orchestration/tests/HandoffCore.Tests.ps1     # pure logic
pwsh -File skills/unattended-orchestration/tests/Ledger.Tests.ps1          # NDJSON event ledger & crash recovery
pwsh -File skills/unattended-orchestration/tests/McpTopology.Tests.ps1     # Serena/Omnigraph/Graphify topology & leaf rules
pwsh -File skills/unattended-orchestration/tests/Archive.Tests.ps1         # artifact archival & idempotent cleanup
pwsh -File skills/unattended-orchestration/tests/Portability.Tests.ps1     # cross-repo adoption
pwsh -File skills/unattended-orchestration/tests/Runner.Smoke.Tests.ps1    # the driver, via -Validate/-DryRun
```

For evaluation of external persistence multiplexers (Herdr) vs autonomous unattended runners, see
ADR 0007 (`docs/decisions/0007-herdr-as-unattended-backend.md`) — not yet written; this pointer
predates it.

Proposals that came out of real runs but are not built yet — lane control while running,
resource exclusion beyond ordering, a machine-wide compute budget — are collected in
[`PROPOSALS-2026-09-05-from-a-downstream-orchestrator.md`](PROPOSALS-2026-09-05-from-a-downstream-orchestrator.md),
each with the incident that motivates it. Read it before inventing a feature; the incident may
already be there.

Put anything that is a function of its arguments in `HandoffCore.psm1` so it can be tested
without an overnight run; the driver keeps only what genuinely touches git, `claude` or the
clock. Both bugs found on this runner's first real invocation lived in the driver and were
invisible to the unit tests — which is why the smoke suite invokes the script itself.

> **PowerShell array trap, twice over.** The output stream unrolls one array level. Both a
> `ForEach-Object` over nested arrays and an `if`/`else` **expression** assigned to a variable
> collapsed `[["E","A"]]` into `["E","A"]`, turning one sequential lane into two parallel ones —
> silently, and exactly against the contention lanes exist to prevent. Build nested arrays with
> an explicit loop and `+= ,`, and return them with a leading comma.
