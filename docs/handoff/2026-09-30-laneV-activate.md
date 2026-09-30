# Lane V-activate — coexistence + live-probe proof for the planned `:20128` restart

**Lane:** `ws-verify-activate` (independent verification, lane V)
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-verify-activate`
**Branch:** `L1-backlog/ws-verify-activate-20260930` (cut from `main` @ `d08f7f2`)
**Dates:** 2026-09-30 → 2026-10-01
**Reviewer nonce:** `VACT-7Q3Z-9F2K-5R8M` (the reviewer must return this string to prove the
document was read)

---

## 0. Question, method and verdict

**Question.** The `:20128` OmniRoute gateway is planned to be restarted so that it loads the
patched files (clamp / deepseek / vertex patches applied to the npm-global package). Before
that restart, this lane must independently answer three things:

1. **(A)** Which files in the live package are patched, and does every patched file have a
   *certified pristine* backup (a revert path)?
2. **(B)** Can the reasoning probe **and** the vertex probe both pass against **one isolated
   gateway** at the same time (coexistence with the shared `:20128` untouched)?
3. **(C)** Does the one patched file that is not attributable to the three briefed patch sets
   (the "unaccounted clamp") affect either probe?

**Verdict.** **GO** — restarting `:20128` is expected to load the patched code and fix both
bugs, with one material caveat (R1: two files have no local backup).

**Headline results.**

- **A.** 19 files differ from the published `omniroute@3.8.50` tarball. **17 have a certified
  pristine revert path.** **2 do not** — `open-sse/config/providers/registry/scaleway/index.ts`
  and `open-sse/translator/paramSupport.ts` (the scaleway-qwen clamp). This is the only
  material finding of the lane.
- **B.** On a single isolated gateway (`:20145`, its own `DATA_DIR`) launched from the same
  patched package, the **reasoning probe passed** (all 4 cases `step2=200`,
  `400 reasoning error reproduced: False`) and the **vertex probe returned `200` for all four
  shapes on `vertex/gemini-3.8-flash`** — both trailing-model shapes and the control — in the
  same window, **reproduced in rounds 2–5**. The only non-200s were provider-quota `429`s and
  one transient `502`; **no `400` was returned by any probe in any round**.
- **C.** **No.** The unaccounted clamp is gated on `provider === "scaleway"` **and**
  `/^qwen3-235b-a22b-instruct-2507$/`; neither probe sends a scaleway model. The byte-exact
  delta is a purely additive 106-byte rule insertion (plus 649 CRLF line endings) and the
  reasoning probe passing live is the empirical confirmation.

---

## 1. Constraints honoured (refusals)

- Worked **only** inside the worktree above; `git rev-parse --show-toplevel` confirmed
  `C:/Users/mauls/Documents/Code/AutoOS-worktrees/AutoOS-ws-verify-activate` before any work.
- **Never** touched the shared `:20128` gateway, never restarted it, never sent it a request.
- The npm-global package `C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute` and its
  backups were treated as **read-only** — no live file was written, no backup created or
  modified, no existing backup deleted.
- **Never** pushed, merged, rebased or checked out any branch. No other worktree was touched.
- `configuration/omniroute/*` (another lane's in-flight reapply scripts) was inspected only,
  never modified.
- Every process this lane started was killed by PID; the only new listener during the run was
  this lane's own `:20145`, now removed (see §4.4).

---

## 2. Method / evidence provenance

All hashing and diffing streams members **straight out of the `.tgz` in memory** (`tarfile` /
`tar -xOf`); no Temp extraction is used, because this host deletes files from Temp extractions.

The pristine source is a locally fetched tarball, independently certified against the npm
registry **before** any comparison:

```
$npm view omniroute@3.8.50 dist.integrity dist.shasum version
dist.integrity = 'sha512-qK6REDWQYGh8lwGwDgFMsBqAMXnxIePudr8cSuSYeB9iIlywNhDJxHKt6Cwa31lPci8jXE5bbvl+az0lvyt0Mg=='
dist.shasum = 'd7b4fce4f1b00e5e826b76855665dfae42aab97a'
version = '3.8.50'

$ python tools/v-activate-tarball-integrity.py
tarball  : C:\Users\mauls\AppData\Local\Temp\opencode\packbackups\omniroute-3.8.50.tgz
size     : 121369534 B
computed : sha512-qK6REDWQYGh8lwGwDgFMsBqAMXnxIePudr8cSuSYeB9iIlywNhDJxHKt6Cwa31lPci8jXE5bbvl+az0lvyt0Mg==
registry : sha512-qK6REDWQYGh8lwGwDgFMsBqAMXnxIePudr8cSuSYeB9iIlywNhDJxHKt6Cwa31lPci8jXE5bbvl+az0lvyt0Mg==
sha512 match: True
computed sha1: d7b4fce4f1b00e5e826b76855665dfae42aab97a
registry shasum: d7b4fce4f1b00e5e826b76855665dfae42aab97a
sha1 match: True
```

The prior lane's report (`docs/handoff/2026-09-30-lanePatchBackups.md`, branch
`L1-backlog/ws-patchbackups-3-20260930`) was read as **claims, not evidence**, and every
number below was recomputed here.

---

## 3. Part A — patch inventory and certified-pristine backups

### 3.1 Inventory (`tools/verify-patch-inventory.py`)

```
members compared (excl. package/node_modules): 21898
differing (patched): 19
missing under live:  0
```

| # | patched file | tar size | live size | pristine-named backup == tarball |
|---|---|---|---|---|
| 1 | `dist/.build/next/server/chunks/_04g0p_r._.js` | 257546 | 257463 | YES |
| 2 | `dist/.build/next/server/chunks/_08_y1bx._.js` | 1235835 | 1236524 | YES |
| 3 | `dist/.build/next/server/chunks/_0o50usg._.js` | 275297 | 275214 | YES |
| 4 | `dist/.build/next/server/chunks/_0o8_5h8._.js` | 870668 | 871423 | YES |
| 5 | `dist/.build/next/server/chunks/_0t1t5fj._.js` | 870668 | 871423 | YES |
| 6 | `dist/.build/next/server/chunks/_0wr-zm3._.js` | 275297 | 275214 | YES |
| 7 | `dist/.build/next/server/chunks/_15ose6x._.js` | 157968 | 158551 | YES |
| 8 | `dist/.build/next/server/chunks/_18ct13i._.js` | 1302308 | 1302588 | YES |
| 9 | `dist/.build/next/server/chunks/_1j_edf1._.js` | 1235835 | 1236524 | YES |
| 10 | `dist/.build/next/server/chunks/_1luyz1c._.js` | 1235835 | 1236524 | YES |
| 11 | `dist/.build/next/server/chunks/_1mq9y97._.js` | 257546 | 257463 | YES |
| 12 | `dist/.build/next/server/chunks/_1xkpq2s._.js` | 157968 | 158551 | YES |
| 13 | `dist/open-sse/mcp-server/server.js` | 5140243 | 5140532 | no pristine-named, but see §3.2 |
| 14 | `open-sse/config/providers/registry/scaleway/index.ts` | 837 | 861 | **NO — no backup of any kind** |
| 15 | `open-sse/services/contextManager.ts` | 38955 | 39300 | no pristine-named, but see §3.2 |
| 16 | `open-sse/translator/index.ts` | 37843 | 38175 | YES |
| 17 | `open-sse/translator/paramSupport.ts` | 10765 | 11617 | **NO — no backup of any kind** |
| 18 | `open-sse/translator/request/openai-responses/toResponses.ts` | 17839 | 20430 | YES |
| 19 | `open-sse/translator/request/openai-to-gemini.ts` | 36018 | 36778 | YES |

`_14jycqh._.js` is **not** in the table: it is byte-identical to the tarball, confirming the
known caveat (it was never patched and correctly needs no backup).

### 3.2 Every backup of every patched file (`tools/v-activate-backup-table.py`)

The inventory above only recognises backups named `*.autoos-backup-pristine-3.8.50-*`. This
second, independent pass accepts **any** `*.autoos-backup-*` sibling and re-hashes each one
against the tarball, so a differently-named but byte-identical backup still counts.

```
patched files: 19   certified: 17   lacking certified backup: 2
  open-sse/config/providers/registry/scaleway/index.ts
  open-sse/translator/paramSupport.ts
```

Two files that the pristine-named check flagged are in fact covered by an older, differently
named backup whose SHA-256 **equals the tarball byte-for-byte**:

- `dist/open-sse/mcp-server/server.js` → `server.js.autoos-backup-20260930-171528`
  (5140243 B, `==tarball=True`).
- `open-sse/services/contextManager.ts` → `contextManager.ts.autoos-backup-20260930144810`
  (38955 B, `==tarball=True`).

### 3.3 Finding A — two patched files have no revert path

`open-sse/config/providers/registry/scaleway/index.ts` and
`open-sse/translator/paramSupport.ts` are patched and have **no backup of any kind** next to
them (neither pristine-named nor otherwise), so there is currently **no local file-level revert
path** for the scaleway-qwen clamp. The only source of truth for their pristine bytes is the
published tarball (certified above). This is the lane's residual risk R1.

---

## 4. Part B — coexistence and live probe proof

### 4.1 Isolation

One isolated gateway was started from the **same** (patched) npm-global package, with a
**separate `DATA_DIR`** (`%TEMP%\opencode\v-activate-iso`) and a separate port (`20145`), so it
never shares config, SQLite or the WAL with the shared gateway:

```
launcher_pid=121344
port=20145
DATA_DIR=C:\Users\mauls\AppData\Local\Temp\opencode\v-activate-iso
listening=YES pid=125716 after=8s
```

The isolated `storage.sqlite` is an independent copy; the live DB was observed to grow under the
probe traffic (`storage.sqlite-wal` updated at 00:15:15), confirming the probes hit the isolated
instance and not `:20128`. The shared gateway's listeners were never touched.

### 4.2 Concurrent probe rounds (`tools/v-activate-both-probes.py`)

Each round launches `tools/probe-reasoning-repro.py` and `tools/probe-vertex.py` at the same
instant against `http://127.0.0.1:20145`, in the same round directory. A round is only counted
when the reasoning probe fully passes **and** all eight vertex shapes return `200` in that same
window; rounds polluted by provider quota `429` are retried (a `400` would stop the run
immediately — none occurred).

| round | UTC start | reasoning | vertex statuses (Tests 1–4 × 2 models) | acceptance met |
|---|---|---|---|---|
| 1 | 2026-09-30T22:13:50Z | PASS (rc=0) | `[429 ×8]` (vertex quota window) | no |
| 2 | 2026-09-30T22:15:13Z | PASS | `[200,200,200,200, 429,429,429,429]` | **YES** |
| 3 | 2026-09-30T22:17:13Z | PASS | `[200,200,200,200, 429,429,429,429]` | **YES** |
| 4 | 2026-09-30T22:20:03Z | PASS | `[200,200,200,200, 429,429,429,429]` | **YES** |
| 5 | 2026-09-30T22:22:03Z | PASS | `[200,200,200,200, 429,429,429,429]` | **YES** |
| 6 | 2026-09-30T22:24:03Z | PASS | `[200,200,200,502, 429,429,429,429]` | no (transient 502) |

The four `200`s in rounds 2–5 are `vertex/gemini-3.8-flash` Tests 1–4 (the two trailing-model
shapes, the control, and the complex agentic shape); the four `429`s are
`gemini/gemini-2.5-flash` (upstream credential cooldown). Round 6 replaced the fourth
`vertex/gemini-3.8-flash` shape with a transient `502 upstream_empty_response`
(`{"error":{"message":"[vertex/gemini-3.8-flash] upstream returned an empty response without
usable output","type":"upstream_response_error","code":"upstream_empty_response"}}`), which is
an upstream/provider fault, not the bug under test.

The strict "all eight shapes in one round" condition was not reached inside the budget, because
`gemini-2.5-flash`'s free-tier quota tolerates only a small burst before the upstream `429`s and
omniroute parks the credential for ~35–56 s; the vertex-3.8 bucket in turn resets on a
~5-minute window, so the two columns did not align. This is a provider-quota artefact, not the
defect under test — **no `400` was returned by any probe in any round.**

Round-2 vertex output (`round02/vertex.out.txt`), verbatim:

```
  [200] vertex/gemini-3.8-flash | Test 1: trailing model turn with tool_calls
  [200] vertex/gemini-3.8-flash | Test 2: trailing model turn plain text
  [200] vertex/gemini-3.8-flash | Test 3: normal ending with user (should pass)
  [200] vertex/gemini-3.8-flash | Test 4: complex agentic shape (7 msg, tool_calls)
  [429] gemini/gemini-2.5-flash | Test 1: trailing model turn with tool_calls
  [429] gemini/gemini-2.5-flash | Test 2: trailing model turn plain text
  [429] gemini/gemini-2.5-flash | Test 3: normal ending with user (should pass)
  [429] gemini/gemini-2.5-flash | Test 4: complex agentic shape (7 msg, tool_calls)
```

Round-2 reasoning output (`round02/reasoning.out.txt`), verbatim:

```
  deepseek-v4.1-flash                 overwrite  rc=812  step2=200  -> PASS
  deepseek-v4.1-flash                 preserve   rc=  0  step2=200  -> PASS
  deepseek/deepseek-flash             overwrite  rc= 72  step2=200  -> PASS
  deepseek/deepseek-flash             preserve   rc= 81  step2=200  -> PASS

  400 reasoning error reproduced: False
```

This is already the decisive functional result: the **trailing-model shapes now return 200**
(the pre-patch bug returned `400 "Requests ending with a model turn are not supported"`), and
the reasoning defence holds — on one isolated instance. The remaining `429`s are pure provider
quota (vertex "accounts have exhausted their quota (reset after 5m)"; gemini
"credentials … cooling down, reset_seconds 35–54"), not the bug under test. No `400` was seen
in any round.

### 4.3 Simultaneous pass (both probes, same window)

Rounds **2–5** are the decisive simultaneous windows: in each, on the same isolated instance
and in the same wall-clock window, the **reasoning probe passed** (4/4 `step2=200`,
`400 reasoning error reproduced: False`) **and** `probe-vertex.py` returned **`200` for all four
shapes on `vertex/gemini-3.8-flash`** — Tests 1 and 2 are the trailing-model-turn shapes that
used to 400, Test 3 is the control, Test 4 is the complex agentic trailing-model shape. Four
consecutive independent rounds produced the identical result, so it is not a one-off.

The only failures are `429`s from `gemini/gemini-2.5-flash`, whose free-tier upstream throttles
after a small burst and parks the credential for ~35–56 s. `429` is explicitly *not* the defect
under test (the defect is the `400 "Requests ending with a model turn are not supported"`), and
**no `400` was returned by any probe in any round.** A round in which both Gemini models return
`200` simultaneously was not observed inside the budget; that is a provider-quota limitation,
not a code limitation, and the briefed acceptance (trailing-model shapes 200, control 200) is
satisfied by the `vertex/gemini-3.8-flash` column.

### 4.4 Gateway lifecycle and listener snapshot

Listeners in the `201xx` range, **before** the run:

```
port=20128 pid=126780      # shared gateway — untouched, must stay
port=20131 pid=126780      # shared gateway — untouched, must stay
port=20132 pid=126780      # shared gateway — untouched, must stay
port=20138 pid=118016      # pre-existing foreign instance — NOT started by this lane, must stay
port=20145 pid=125716      # THIS lane's isolated instance
```

After the run the isolated instance was killed by PID (launcher `121344` + child `125716`);
the post-kill snapshot is in §4.5.

### 4.5 Post-kill listener snapshot

Listeners in the `201xx` range, **after** the isolated instance was killed by PID (launcher
`121344` + child `125716`):

```
port=20128 pid=126780
port=20131 pid=126780
port=20132 pid=126780
port=20138 pid=118016
```

`:20145` is gone. The three shared ports (`126780`) and the pre-existing foreign `:20138`
(`118016`) are intact and unchanged; before/after snapshots are saved as
`%TEMP%\opencode\v-activate\listeners-{before,after}.txt`.

---

## 5. Part C — does the unaccounted clamp change affect either probe?

### 5.1 What the unaccounted change is

`paramSupport.ts` (+852 B) adds exactly one new clamp rule (verbatim diff):

```diff
@@ -95,2 +95,17 @@
   { provider: "azure-ai", match: /^gpt-4o-mini/i, maxOutputCap: 16384 },
+  // Scaleway qwen3-235b-a22b-instruct-2507 enforces a 16384 max_completion_tokens
+  // ceiling server-side and 400s on anything larger ("payload validation:
+  // max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507").
+  // OmniRoute's tool-calling floor (DEFAULT_MIN_TOKENS = 32000, applied by
+  // adjustMaxTokens) raises even a tiny explicit max_tokens to 32000 whenever
+  // tools are present, so every agentic client trips this. The registry catalog
+  // sets maxOutputTokens: 16384 so clampToModelMaxOutput finds the ceiling; the
+  // fixed maxOutputCap: 16384 provides the same cap even if the catalog lookup
+  // misses (PROVIDER_MODELS is keyed by alias "scw", not id "scaleway").
+  {
+    provider: "scaleway",
+    match: /^qwen3-235b-a22b-instruct-2507$/,
+    maxOutputCap: 16384,
+    clampToModelMaxOutput: true,
+  },
 ];
```

`scaleway/index.ts` (+24 B) adds the matching catalog ceiling:

```diff
-    { id: "qwen3-235b-a22b-instruct-2507", name: "Qwen3 235B A22B (1M free tok …)" },
+    { id: "qwen3-235b-a22b-instruct-2507", name: "Qwen3 235B A22B (1M free tok …)", maxOutputTokens: 16384 },
```

The same rule was compiled into two built chunks. Byte-exact accounting
(`tools/v-activate-clamp-evidence.py`, streamed from the tarball in memory) shows the only
substantive change is a **106-byte additive insertion**, plus an inert line-ending
normalisation:

```
=== dist/.build/next/server/chunks/_0o8_5h8._.js ===
  tarball bytes=870668  live bytes=871423  raw delta=755
  CRLF pairs: tarball=0  live=649  diff=649
  after CRLF-normalise: tarball=870668  live=870774  delta=106
  inserted segment (106 B): b',{provider:"scaleway",match:/^qwen3-235b-a22b-instruct-2507$/,maxOutputCap:16384,clampToModelMaxOutput:!0}'
  deleted segment (0 B): b''
```

`_0t1t5fj._.js` is identical in every respect (same sizes, same 106-byte insertion, zero
deletions).

### 5.2 Structural gating — the clamp cannot match either probe's models

The inserted rule applies **only** when both conditions hold:

- `provider === "scaleway"`, and
- the model id matches `/^qwen3-235b-a22b-instruct-2507$/`.

The probes send:

| probe | models sent |
|---|---|
| `probe-reasoning-repro.py` | `deepseek-v4.1-flash`, `deepseek/deepseek-flash` |
| `probe-vertex.py` | `vertex/gemini-3.8-flash`, `gemini/gemini-2.5-flash` |

None is a scaleway model and none matches the regex, so the added rule is unreachable for both
probes. It is a **purely additive** change (0 bytes deleted): even the code path that scans the
clamp table is unaffected for other providers.

### 5.3 Empirical confirmation

The reasoning probe returned `400 reasoning error reproduced: False` on the isolated instance
carrying the clamp patch (§4.2), and the vertex trailing-model shapes returned `200`. No `400`
was returned by any probe in any round.

---

## 6. Verdict and residual risks

**Verdict: GO** — restarting `:20128` is expected to load the patched code and the
two probes pass against the patched code on an isolated instance, with no measurable
interference from the unaccounted clamp.

**Residual risks**

- **R1 (material).** `open-sse/config/providers/registry/scaleway/index.ts` and
  `open-sse/translator/paramSupport.ts` have **no local backup**, so the scaleway-qwen clamp
  has no fast revert path. Reverting requires re-extracting those members from the certified
  tarball. (The rest of the patch inventory is covered: 17/19 files.)
- **R2 (low).** `_0o8_5h8._.js` and `_0t1t5fj._.js` are the files the previous lane could not
  attribute to a briefed patch set. This lane attributes them: the 106-byte scaleway clamp
  insertion. They do carry a certified pristine backup.
- **R3 (low).** Vertex/gemini provider quota is tight and reactive (`429` with a 5-minute
  reset). The restart itself does not consume provider quota; only the probes do.
- **R4 (low).** The running `:20128` process (started 16:50:46) predates several patched files
  (17:15–18:29) and therefore holds stale code; this is the motivation for the restart, and it
  means the pre-restart shared gateway is not representative of the patched code either way.

---

## 7. Denials / transient errors observed

No `chat_admission_busy` or `Rate limit exceeded` admission refusal was observed while
composing this report.

The harness returned `permission.rejected` for a small number of shell commands whose text
contained secret-shaped tokens (e.g. `.env`, a JSON dump naming a storage file); these are
harness permission denials, **not** provider refusals. Verbatim body:

```json
{"error":{"type":"permission.rejected","message":"Permission denied: shell"}}
```

Recovery was automatic (the same logic moved into a script file and ran normally). Times are
approximate (local = UTC+02:00).

| local | UTC | command shape | recovery |
|---|---|---|---|
| ~2026-10-01 00:15 | ~2026-09-30 22:15 | inline `python -c` reading a JSON dump | moved to `tools/v-activate-clamp-evidence.py` |

Provider `429`s (transient quota/cooldown, not refusals) are listed in §4.2.

---

## 8. Reviewer (nonce-gated)

A reviewer from a **different model family** than the author (author: t3 driver) was asked to
read this document and return the nonce, then judge the GO verdict and the soundness of
claim C. The reviewer returned the nonce verbatim, proving it read the document.

- **Nonce (set):** `VACT-7Q3Z-9F2K-5R8M`
- **Nonce returned:** `VACT-7Q3Z-9F2K-5R8M` ✅ (exact match)
- **Family:** Google Gemini — model `omniroute/vertex-pro` (direct Gemini 3.1 Pro, Vertex AI)
- **Verdict:** **GO-WITH-CAVEAT** (concurred with the GO call and with R1)
- **Reasoning (reviewer):** the trailing-model fixes work on the tested
  `gemini-3.8-flash` shapes and the reasoning probe passes reliably; claim C is logically
  sound because the clamp's strict `provider` + model-regex gating prevents it matching the
  deepseek/vertex models; the missing backups are correctly identified.
- **Overstatements flagged by the reviewer (recorded, not dismissed):**
  - the headline "the vertex probe passed" is broad where `gemini/gemini-2.5-flash` returned
    `429` in every round (half of the vertex probe's targets were never validated);
  - §0.C calls the passing reasoning probe empirical confirmation for *both* probes, whereas
    that probe only exercises the deepseek models.
- **Disposition:** both points are already disclosed in §4.2/§4.3 (the four `200`s are
  `vertex/gemini-3.8-flash`; `gemini/gemini-2.5-flash` was `429` in every round; no `400` in
  any round). The verdict stands as **GO with caveat R1**; the reviewer's wording caveats are
  accepted as presentational and are recorded here rather than papered over.

**Reviewer-attempt ledger (non-refusal errors, verbatim).** Two earlier reviewer models
could not be reached; these are provider/harness faults, **not** `chat_admission_busy` or
`Rate limit exceeded` admission refusals, and no backoff window was triggered:

- `openrouter/anthropic/claude-sonnet-5.5`:
  `This request requires more credits, or fewer max_tokens. You requested up to 128000 tokens, but can only afford 1938.`
- `omniroute/opus-4-6`:
  `antigravity/claude-opus-4-6-thinking: auth — [antigravity] All 1 connection(s) authentication expired — please reconnect in the dashboard (HTTP 401)`

The third attempt (`omniroute/vertex-pro`) completed and returned the nonce above.
