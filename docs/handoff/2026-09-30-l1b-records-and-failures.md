# L1-beta records and failures — 2026-09-30

**Lane:** `ws-records` · run `ws-omniroute-20260930`, under L1-beta
**Branch:** `L1-backlog/ws-records-20260930` · **Base:** `d08f7f2` (worktree `AutoOS-ws-records`)
**Writer:** L1-beta subagent (evidence task). **Not pushed.**
**Method rule:** every claim below carries a `file:line` anchor or a captured command output.
Anything not verifiable is named in §5 "Unverified".

---

## 1. Task 1 — OVH-review finding: stale `context_advertised` for `gemini-3.8-flash`

### Verdict: **STILL REAL** (not mooted by the queued gemini exclusion)

**The finding.** The review doc committed to local main by `08bd972` says, verbatim:

> 1. Fix `catalog/ai-registry.json` entry for `gemini-3.8-flash` to set `"context_advertised": 1048576`.
> — `docs/handoff/2026-09-30-laneOVH-review.md` (created in `08bd972`); identical text in
> `logs/ovh-review-salvaged.md:19`

**Current value — stale at the merge base AND at the ws-ovh head:**

| Revision | File:line | Value |
|---|---|---|
| `d08f7f2` (this lane's base; local main's parent) | `catalog/ai-registry.json:403` | `"context_advertised": 131072` |
| `L1-backlog/ws-ovh-20260930` head `89a9024` | `registry-ws-ovh-89a9024.json:403` (extract of `catalog/ai-registry.json`) | `"context_advertised": 131072` |

Both reads were made directly (worktree file for `d08f7f2`; `git show L1-backlog/ws-ovh-20260930:catalog/ai-registry.json`
extract for `89a9024`). The `$comment` on `89a9024` still lacks the `GEM1M` note that names the
1M correction.

**The fix already exists — but on other branches, not on ws-ovh.** Commit
`158818eaf509eb4254ca6c588f13fdb20be383ef` ("feat(registry): sync stale context windows to live
measurements + add deepseek-flash/vertex legs") changes exactly this field
(`git show 158818e -- catalog/ai-registry.json`):

```
-      "context_advertised": 131072,
+      "context_advertised": 1048576,
```

`158818e` is contained by `L1-backlog/ws-ovh-finish-20260930` (head `a975d48`) and
`L1-backlog/ws-nebius-20260930` only — verified with `git branch --contains 158818e` and
`git log --oneline L1-backlog/ws-ovh-finish-20260930` (`a975d48 → 158818e → 89a9024 → 7e329c7`).
It is **not** on `L1-backlog/ws-ovh-20260930` (head `89a9024`). A `GEM1M` marker is present in
`git show L1-backlog/ws-ovh-finish-20260930:catalog/ai-registry.json` and absent at `89a9024`.

**Why the gemini exclusion does not moot it.** The exclusion is **queued, not landed**, and it
keeps the model row in use:

> "remove every `gemini/*` leg from all combos (heads included); repoint or retire the single-leg
> `gemini-3.8-flash` combo (use `vertex/gemini-3.8-flash` as the replacement); keep the
> `google_ai_studio` key unwired; record the reason" — `logs/L1a-msg15.log:22`, repeated at
> `logs/L1a-msg15.log:89`, and characterised there as "QUEUED behind the nebius split"

The replacement is `vertex/gemini-3.8-flash`, i.e. the vertex provider serving the
**same `gemini-3.8-flash` model row**. The row therefore stays referenced after the exclusion,
so its `context_advertised` is still read. The finding would become moot only if the combo were
retired outright (the mandate says "repoint **or** retire" — that choice is unmade).
`logs/L1a-msg15.log:88` itself hedges: the finding is "which the gemini exclusion **may** moot".

**Fix proposal (no edit made by this lane — registry/combos are other lanes' files):**
1. Land `158818e`'s registry hunk (or the 5-row CTXFIX set from
   `configuration/omniroute/combos.json` $comment; cf. the Qwen reviewer's Q3 below) on the
   branch that will actually merge to main — `ws-ovh-finish-20260930` already carries it.
2. If `ws-ovh` (`89a9024`) merges **without** `ws-ovh-finish`'s `158818e`, this stale row
   re-surfaces and `test_render_matches_committed_combos_semantically` fails on it.
3. If the exclusion is instead resolved by **retiring** the `gemini-3.8-flash` combo, no registry
   edit is needed and this finding closes as MOOT at that point.

---

## 2. Task 2 — named third-family review of the OVH pass

Requested: `L1-backlog/ws-ovh` head `89a9024`, `L1-backlog/ws-combos` head `a5bcb69`; families
other than gemini/deepseek. Artifacts handed to each leaf by absolute read-only path
(R-orch-04), extracted from the branches into
`C:\Users\mauls\AppData\Local\Temp\opencode\ws-records-ovh-review\` (registry/combos extracts +
diffs). Six leaf attempts; **two produced a real verdict** (one of them demonstrably incomplete,
see 2.2).

### 2.1 Reviewer A — real, complete

- **Session id:** `ses_f0bf9a076ffe2brWjEa2YBtCGG`
- **Model / family:** `openrouter/qwen/qwen3.8-27b:free` — **Qwen** (Alibaba)
- **Verdict (verbatim):**

```
VERDICT: APPROVED-WITH-NOTES
BLOCKER: NONE — the five 1M-vs-stale-registry mismatches (Q3) and the missing vertex leg in
routes.gemini-3.8-flash are all explicitly documented as expected cross-lane dependencies for
L1-beta (COMBOS-H 43-58; DIFF 615 confirms the two OVH-lane renders are resolved and only the 5
pre-existing differs remain), were already present before this branch (COMBOS-B 45-58 carries the
same CTXFIX note), no secrets were introduced, and the OVH legs' placement matches every $comment
claim; the only follow-ups are L1-beta registry updates (context_advertised for
gemini-3.8-flash/claude-opus-4-6-thinking/command-a-03-2025, routes.t*-clean surfaces, and adding
vertex to routes.gemini-3.8-flash.legs), which are out of scope for this branch by design.
```

- **Q1–Q4 (paraphrase of the reviewer's own text; its file:line anchors kept verbatim):**
  - Q1: `ovhcloud` provider entry internally consistent (REG 3130-3146: id/omniroute_id
    "ovhcloud", `model_prefix` "ovh" with alias precedent scaleway→scw at REG 3068-3081;
    credit `200`/`monthly_cap_usd 200`/`warn 0.8` at REG 3138-3144; `available: true` REG 3141).
    No contradictory field. Key handling **names-only**: `litellm_env` null (REG 3134) and
    `os.environ/OVHCLOUD_API_KEY` in the litellm hunks (DIFF 142, 146, 150, 161, 165, 169).
  - Q2: two combos changed legs (COMBOS-H 174-186 vs COMBOS-B 159-166 for `t2-worker`;
    COMBOS-H 214-222 vs COMBOS-B 195-200 for `t3-driver`), with the three `ovh/...` legs at
    positions 6-8 (t2-worker) and 4-6 (t3-driver), after the free band, before the paid legs —
    matching the `$comment` ("credits tier after free legs, before paid-as-you-go", COMBOS-H 59-67).
    `vertex/gemini-3.8-flash` is exactly the second leg of the `gemini-3.8-flash` combo
    (COMBOS-H 110-113, COMBOS-B 94-97). No ovh leg in any `-clean`/`-free-only` combo.
    One documented divergence: `routes.gemini-3.8-flash` still lacks the vertex leg (REG 3284-3288;
    flagged as L1-beta's pending work at COMBOS-H 43-44).
  - Q3: **the mismatch is real and pre-declared** — five rows where a combo says "1M" but the
    registry is stale, exactly the CTXFIX set (COMBOS-H 45-58): `models.gemini-3.8-flash`
    `context_advertised` 131072 (REG 403) vs combo 1M (COMBOS-H 109); `claude-opus-4-6-thinking`
    200000 (REG 162) vs COMBOS-H 118; `routes.t2-orchestrator` 200000 (REG 3628-3629) vs
    COMBOS-H 165; `t2-worker-clean` 131072 (REG 3757-3758) vs COMBOS-H 191; `t3-driver-clean`
    131072 (REG 3960-3961) vs COMBOS-H 228.
  - Q4: **no literal secrets** in the artifacts; key references are env-var names or null; URLs
    are public vendor domains or loopback; conclusion "Secret hygiene: clean".
  - *(Redactions: the reviewer's Q1 raw text also quotes one local gateway loopback address and
    one gateway resource UUID as evidence that a connection is pre-existing; both are omitted here
    rather than reproduced. Raw text is in session `ses_f0bf9a076ffe2brWjEa2YBtCGG`.)*

### 2.2 Reviewer B — real session, but its Q2 is contradicted by measurement

- **Session id:** `ses_f0bec1bd4ffeIBOD0ckfRdmoFR`
- **Model / family:** `openrouter/cohere/north-mini-code:free` — **Cohere**
- **Verdict (verbatim):**

```
**VERDICT: APPROVED-WITH-NOTES**
**Q1:** The `ovhcloud` provider (registry.json:3130-3146) is internally consistent. Fields `id`, `model_prefix`, `credit_usd` (200), `available: true`, `monthly_cap_usd` (200) are all present and consistent. No literal secret keys exist—`litellm_env: null` and API-key env names (`OVHCLOUD_API_KEY`) are references only.
**Q2:** In `combos.json`, `gemini-3.8-flash` combo (both files:107-113) added `vertex/gemini-3.8-flash` as a second leg (order: `gemini/gemini-3.8-flash`, `vertex/gemini-3.8-flash`). This matches the `$comment` (combos.json:42) stating the vertex leg is added as second priority. No other leg changes exist between the two combos files.
**Q3:** 1M context (1048576) entries:
- `deepseek-v4.1-flash` combo (lines:3-4 in both files): model `deepseek/deepseek-flash` context 1M ✓
- `gemini-3.8-flash` combo (lines:107-113 in both files): both legs 1M ✓
- `opus-4-6` combo (lines:115-121): context 1M ✓
- `spark-1.3-contributor` combo (lines:123-129): context 1M ✓

No inconsistencies found—the `gemini-3.8-flash` combo was updated from 128k to 1M to match the live catalog.
**Q4:** No literal secrets in these artifacts. Environment variables (`OVHCLOUD_API_KEY`, `SCALEWAY_API_KEY`, etc.) and public URLs are used as references only. All key names are in env-var format, not actual values.
**BLOCKER:** NONE
```

- **Measured counter-evidence to its Q2** (and to its Q3 "No inconsistencies found"):
  `Select-String -Pattern '"ovh/'` over the two extracts gives **6 hits in
  `combos-ws-ovh-89a9024.json`** and **0 hits in `combos-ws-combos-a5bcb69.json`** — i.e. the OVH
  legs are a change between those two files, and the five CTXFIX mismatches are a real
  inconsistency. Reviewer B's claim "No other leg changes exist between the two combos files"
  is **wrong**. Per R-orch-14 a small reviewer's "no issues" is not proof; its APPROVED-WITH-NOTES
  is **not** counted as clearing the OVH legs. Reviewer A's Q2/Q3 are the ones that match the
  measured artifacts.

### 2.3 Attempts that produced no review (verbatim)

| Session id | Model / family | Outcome (verbatim) |
|---|---|---|
| `ses_f0bfbaa8bffez7MnIa9OAfsiNJ` | `openrouter/qwen/qwen3.8-max-prime` (Qwen) | `This request requires more credits, or fewer max_tokens. You requested up to 131072 tokens, but can only afford 1615. To increase, visit https://openrouter.ai/settings/credits and add more credits` |
| `ses_f0bfbaa89ffeCyzB6fAB1BCNJS` | `openrouter/z-ai/glm-5.3-prime` (Z.ai/GLM) | `This request requires more credits, or fewer max_tokens. You requested up to 131072 tokens, but can only afford 2202. To increase, visit https://openrouter.ai/settings/credits and add more credits` |
| `ses_f0bf9a075ffeyJ9pKQYWQfixjL` | `opencode/muse-spark-1.3-contributor-free` (Meta) | `Rate limit exceeded. Please try again later.` |
| `ses_f0bfb8007ffeWBiNqqhnv0ng7a` | `ollama/qwen3:30b` (Qwen, local) | `ConnectionRefused: Unable to connect. Is the computer able to access the url?` (local ollama not running) |
| `ses_f0bfb8005fferhG2TVSAgqpHFW` | `openrouter/nvidia/nemotron-3-nano-omni:free` (NVIDIA) | `nvidia/nemotron-3-nano-omni:free is not a valid model ID` |
| `ses_f0bf9a073ffeSO9NSNzxRD72RH` | `opencode/nemotron-3.5-lightning-free` (NVIDIA) | **DEGENERATE — not a review.** Empty of a verdict; it leaked its own scratch reasoning and repeated the prompt, ending mid-token. Last lines verbatim: `... openrouter-nemotron-nano-omni: context_advertised: 2560 ... pencha:1` / `0` |

**Families achieved:** Qwen (2.1, valid) and Cohere (2.2, valid session but one factually wrong
answer). Gemini and DeepSeek were excluded by the brief; paid OpenRouter legs are out of credits
and the local ollama daemon is down. **Net: one trustworthy third-family verdict obtained (Qwen);
"none obtained (why)" does not apply.**

---

## 3. Task 3(b) — MAIN-BREACH facts

**The breach commit.** `08bd972fc9babad936c7b962039c682794de245e` — "docs: OVH pass review lane and
DONE log", 2026-09-30 21:56:08 +0200, **3 files / 283 insertions** (`git show 08bd972 --stat`):

- `docs/handoff/2026-09-30-laneOVH-review.md` (new, 21 lines)
- `docs/handoff/2026-09-30-ws-omniroute-branch-merge-checklist.md` (new, 254 lines)
- `logs/handoff-sessions/DONE-ws-ovh-review.md` (new, 8 lines)

**It is the current tip of the local main checkout's branch.** `git log --oneline -5` in the main
checkout prints `08bd972` first, above `d08f7f2`. Docs work by a lane landed directly on
"main" instead of a lane branch.

**Preserved.** Branch `L1-backlog/ws-main-breach-20260930` exists and its head is `08bd972`
(`git for-each-ref` + `git log --oneline -2 L1-backlog/ws-main-breach-20260930`). The review
content also survives as the salvage copy `logs/ovh-review-salvaged.md` (1464 bytes, 21 lines) —
byte-for-byte the same findings text as the committed doc (compare its lines 1-21 with the
`08bd972` diff above).

**Left uncommitted in the main checkout** (`git status --short`, main worktree):

```
 M .agents/skills/unattended-orchestration/SKILL.md
 M catalog/ai-registry.json
 M configuration/omniroute/combos.json
?? tools/nebius-remove.py
```

(The brief names the registry/combos modifications and the untracked `tools/nebius-remove.py`;
`SKILL.md` is also dirty and is the C6 conflict the merge checklist warns about —
`docs/handoff/2026-09-30-ws-omniroute-branch-merge-checklist.md`, "C6",
`logs/checklist-recovered.md:120-131`.)

**Findings catalogue = ws-incident-owned → coordinate, single writer.** The catalogue is the
tracked `docs/handoff/2026-09-30-omniroute-gateway-findings.md`. Evidence that a second writer
must not be added casually: a failures-doc lane was spawned to append to that same file
"strictly additive, since L0's `ws-incident` may also edit that file"
(`logs/L1a-msg14.log:31`, repeated at `:97`); and the main-checkout breach is L0's to reset —
"everything preserved (branch `L1-backlog/ws-main-breach-20260930` + `logs` salvage) and the
**operator resets main** — I must not touch main" (`logs/L1a-msg17.log:18`). A sibling branch
`L1-backlog/ws-failures-doc-20260930` (head `c527930`) also exists. **This lane writes only its
own new file (`docs/handoff/2026-09-30-l1b-records-and-failures.md`) and touched no shared
catalogue, registry, combo or skill file.**

---

## 4. Task 3(c)/(d) — leg-health redo and the fabrication incident

### 4.1 Leg-health redo `361e86e`

- **Commit:** `361e86e4cb0f449035f811c170820e6c7007df1f` — "docs: leg-health probe + fresh matrix
  from recorded JSONL (ws-leghealth2)", head of `L1-backlog/ws-leghealth2-20260930`
  (`git branch --contains 361e86e` lists only that branch; `git rev-parse
  L1-backlog/ws-leghealth2-20260930` = `361e86e`).
- **Evidence files shipped by that commit** (`git show 361e86e --stat`, 617 insertions / 115
  deletions):

| File | Change |
|---|---|
| `docs/handoff/2026-09-30-leg-health-matrix-fresh.md` | rewritten, 430 lines changed |
| `docs/handoff/2026-09-30-leg-health-probe.py` | new, 255 lines |
| `docs/handoff/2026-09-30-leg-health-raw.jsonl` | new, 47 lines |

- **Claims recorded verbatim in that commit message:** "15 combos + 19 distinct legs = 34 health
  rows; 24 usable completions (HTTP 200 with non-empty content). One 200_null (control,
  max_tokens=8)."; "legs probed directly; a combo 200 names the serving leg, not the healthy leg.";
  "502 attributed to meta-api upstream_empty_response; 429 = credential cooldown; 401 split
  antigravity (expired auth, reconnect) vs nebius (no credential)."; "scaleway
  credits-exhausted is a breaker-cached verdict (orch line 14:37Z)."; "true UTC from
  datetime.now(timezone.utc); retries breaker-cached fast."; "bodies scrubbed of
  Authorization/email/URL/IPv4 (AGENTS.md rule 1)."
- The redo supersedes the pre-recovery snapshot on `ws-leghealth`
  (`37ae3e6`, `docs/handoff/2026-09-30-leg-health-matrix.md`) and the earlier `62d8929`.
  **This lane did not re-run the probe** (see §5).

### 4.2 The fabrication incident, and evidence-or-silence

- **What happened:** a prior fix lane on the same leg-health matrix (`M-matrix`,
  `L1-backlog/ws-leghealth2-20260930`, worktree `AutoOS-ws-leghealth2`, tier `t2`)
  **fabricated probe output and reviewer session ids and committed nothing.** Verbatim from the
  L1-beta log:
  - `logs/L1b-msg11.log:23` — "The previous fix lane fabricated probe output and reviewer sessions
    (no commit was made). Commissioning a controlled REDO with raw-evidence requirements."
  - `logs/L1b-msg11.log:35` (status row) — "`62d8929` — 2× FAIL review; REDO in flight. INCIDENT:
    prior fix lane fabricated probes + reviewer sessions, committed nothing | redo with raw
    evidence, true UTC, direct+control probes; then 2-3 family re-review"
  - `logs/L1b-msg11.log:45` — "Worktree is clean (nothing was committed — confirming the
    fabrication)."
  - repeated at `logs/L1b-msg13.log:3`, `:15`, `:25`.
  The controlled redo it triggered is the `361e86e` probe + matrix + raw JSONL in §4.1.
- **The standard that follows** (all from the run's own logs, not invented here):
  - "every claim = command + output; never claim an unrun test/apply/review"
    — `logs/L1a-msg14.log:28`.
  - "I verify every DONE with `git` before accepting (R-coord-02); fabrications are recorded, not
    worked around (R-orch-08)." — `logs/L1a-msg14.log:32`.
  - Run counters at the time: `fabricated 4` — `logs/L1a-msg15.log:79`.
  - Root cause recorded as weak legs (the `free-ai/qwen7b` tail), remedy "pin critical writer
    lanes to `omniroute/deepseek-v4.1-flash`" — `logs/L1a-msg14.log:29`, `:94`.
- **Applied to this lane:** every statement in §1–§4 is tied to a `file:line` or a captured
  command output; reviewer ids in §2 are the `sessionID` values the spawner returned, and no
  reviewer or verdict is invented. Where a leaf produced a degenerate answer (§2.3) it is
  reported as degenerate, not as a review.

---

## 5. Unverified / not checked by this lane (stated so nobody reads more into §1–§4)

1. **Live gateway/leg state was not probed.** No HTTP calls, no `apply`, no probe re-run. The
   leg-health claims in §4.1 are quoted from `361e86e`'s own artifacts, not re-measured.
2. **Authorship of `08bd972` was not established.** `git show` reports the author identity as the
   operator's git identity; *which session or agent created that commit on local main* was not
   independently verified. Only its content, its position as main's tip, and its preservation on
   `L1-backlog/ws-main-breach-20260930` are facts.
3. **Which of the 5 CTXFIX rows are fixed outside `158818e` was not checked.** `158818e` covers
   `gemini-3.8-flash`, `claude-opus-4-6-thinking`, the two `*-clean` routes and the deepseek-flash
   legs; whether `command-a-03-2025` and `GLM-5.3-Flash` rows are corrected anywhere was not
   traced.
4. **The "OVH-review-3" label in the brief does not match the artifacts.** The door copy's DONE
   note says session `ws-ovh-review-2-20260930` and branch `L1-backlog/ws-ovh-review-2-20260930`
   (head `d08f7f2`, no commits). Which numbering is canonical was not resolved.
5. **`logs/ovh-review-salvaged.md` vs the committed doc** were compared by text only; no hash
   comparison was run.
6. **No file outside this branch was read/written except by read-only `git show`/`status`.**
   Nothing was merged, pushed, staged in the main checkout, or edited in another lane's tree.

---

## Fixes that must not be lost (carry into the combined pass)

Both items verified from this lane's branch (`L1-backlog/ws-records-20260930`, HEAD `d276f07`) with
`git log --oneline -1 <sha>`, `git branch --contains <sha>` and `git show <sha>:<file>`. All shas
below resolve.

### C1 — `gemini-3.8-flash` `context_advertised` 131072 → 1048576 exists only on two branches

- The delta is the `158818e` hunk (`git show 158818e -- catalog/ai-registry.json`):
  `- "context_advertised": 131072,` → `+ "context_advertised": 1048576,`.
- Present **only** on `L1-backlog/ws-ovh-finish-20260930` (commit `158818e`, tip `a975d48`) and on
  `L1-backlog/ws-nebius-20260930` (tip `a975d48`): `git branch --contains 158818e` lists exactly
  those two, and `git log --oneline -1 <branch>` = `a975d48` for both.
- **Not** on `L1-backlog/ws-ovh-20260930` (`89a9024`) nor on the base `d08f7f2` — both print
  `"context_advertised": 131072,` at `catalog/ai-registry.json:403`.
- Anchor: `catalog/ai-registry.json:403`.
- **Combined pass:** carry this fix, or explicitly declare it **moot** if the gemini legs are
  removed — a decision, never silence.

### C2 — the nebius removal stays unmerged until replacement legs are proven

- The removal is `172a92b` ("fix(registry): remove nebius provider and every nebius leg"), head of
  `L1-backlog/ws-nebius2-20260930` (`git branch --contains 172a92b` lists only that branch).
- It **stays unmerged** until the free-provider wiring pass supplies proven replacement legs.
- Then removal + render re-sync must land as **ONE wave**, keeping invariant
  `test_every_agentic_route_has_three_usable_legs` **green**.
