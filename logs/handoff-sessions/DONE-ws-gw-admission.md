# DONE — ws-gw-admission-20260930 (lane F2)

Verdict: **FIXED (config-only)**. `chat_admission_busy` sheds under ≥3
concurrent heavy streams were caused by the single-slot structural gate
(`OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT` default 1 → 1 primary + 1 headroom = 2
concurrent heavy max). Knob set to **4** (USER env, durable) → 4+4 = 8.
No code patch; `combos.json` untouched; no secrets committed.

Evidence: `docs/handoff/2026-09-30-laneF2-admission.md` (§1 knobs with
file:line, §2 measurements, §3 fix + verbatim restart, §4 proof, §5 open item).
Scripts kept: `measure-admission.ps1` (`-Model`/`-Stream` params added),
`debug-single.ps1`.

Proof: before-3 → 2×200+1×503; before-4 → 1×200+3×503 (all `chat_admission_busy`
@~2.1 s = 2000 ms queue exhausted). After: after2-3 → 3×200 overlapping ~13 s;
after3-4 → 4 admitted, 0 `chat_admission_busy`. Zero admission sheds in the
gateway log since the fix; slot release verified (no leak — `activeHeavy`
never grows).

Reviewer: t3-reviewer leaf attempted 2×, both failed inside the leaf: all
gateway legs returned 401 (expired provider grants — see open item), so the
leaf never ran. Reconciled by self-review against the 6 attack points:
capacity math 1+1→4+4 confirmed (`chatBodyAdmission.ts:44-47,119-122,227,
282-294`); ~2.1 s latencies prove the 2000 ms bounded wait (`:392-468`,
structural `queueMs` wired in running build); 401s prove gate passage
(post-dispatch only); first restart missed the knob via stale parent env
block, final restart carries it (behavioral proof; no read-only endpoint
exposes effective slots); heap valve (4 MB queued-bytes) + 0.75 shed ratio
untouched; git shows docs+scripts only. Verdict: ACCEPT with caveats noted.

Open items: (1) OPERATOR — provider OAuth grants expired (gemini,
antigravity, scaleway, nebius, deepseek…): "authentication expired — please
reconnect in the dashboard"; pre-existing decay (breakers since 09-22),
unmasked by the restart. (2) Re-run the 4-wide proof after reconnect.
(3) Future gateway restarts inherit the knob from USER env automatically;
any manual `Start-Process` from an old shell must set it in the spawning
block (see evidence §3 pitfall).
