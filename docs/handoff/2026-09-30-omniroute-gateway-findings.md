# OmniRoute workstation — gateway + config findings (2026-09-30)

Operator-facing write-up of the failure modes measured during the DS1M / AGYCANON
session. Each entry: what happened, the evidence (file:line or measured output),
impact, and the hardening idea. No secret values are quoted anywhere in here.

Status legend: **[fixed in-session]** / **[open — operator decision]** / **[open — hardening idea]**.

---

## 1. Leg validation fails open — a renamed upstream model degrades silently

- **What:** `apply.ps1` drops every combo leg the live catalog does not recognize,
  prints one warning line, and still writes the combo. If every leg drops, the
  combo is reported as `no usable models - not created` and the script exits green.
- **Evidence:** `configuration/omniroute/apply.ps1:16` (documented as a feature),
  `:395` (`catalog does not know … (skipped)`), `:398`
  (`! <combo>: no usable models - not created`). Measured 2026-09-30: the meta-api
  legs dropped exactly this way and the run still reported success.
- **Impact:** a provider-side rename degrades a tier without failing anything —
  the failure is a warning line inside a long apply log.
- **Hardening:** treat "a previously created combo now has zero usable legs" as a
  hard error; make leg validation alias-tolerant via `omniroute providers
  available --json` aliases (a leg valid under an alias should be canonicalized,
  not dropped).

## 2. Stale-combo non-repair — dead live combos keep their old legs

- **What:** when a combo cannot be rebuilt (all legs dropped), apply does not touch
  the existing live combo. Pruning only covers names listed in `retired`/`omitted`
  in `combos.json`.
- **Evidence:** today's apply: `! spark-1.3-contributor: no usable models - not created`,
  `! t1-orchestrator-paid: no usable models - not created`, then
  `Prune: no retired or omitted combos in the store`; `apply.ps1:398`, prune branch
  in the same file. The two combos still carry `meta-api/*` legs live (`omniroute
  combo list --json`, 2026-09-30).
- **Impact:** dead legs linger indefinitely; every consumer that reads the live
  store sees a combo that cannot serve. Only a human noticing the warning fixes it.
- **Hardening:** auto-quarantine (or require explicit retire) when a previously
  created combo loses all legs; alternatively a `--prune-empty` opt-in and a
  non-zero exit for the "all legs dead" case.

## 3. Client-key drift — the keys file grew names the consumers do not read

- **What:** `api-keys.yml` carries `omniroute_server` + `omniroute_workstation`,
  but `apply.ps1` reads a single `omniroute` entry and `start-stack.sh` reads a
  top-level `omniroute:` line. Neither finds the new names, so validation/probe/
  stack wiring silently skip the client key; both keys were also measured 401 on
  `/v1/models` (wrong key type or scope — operator decision pending).
- **Evidence:** `configuration/omniroute/apply.ps1:313`, `:491`
  (`$Keys['omniroute']`); `configuration/start-stack.sh:12-13`
  (`sed -n 's/^omniroute[[:space:]]*:…'`); `configuration/api-keys.example.yml:47`;
  handoff §Addendum "Key drift found".
- **Impact:** the one path that proves the gateway serves (`/v1/models` +
  `-Probe`) is disabled exactly when keys are rotated — a silent loss of the only
  end-to-end check.
- **Hardening:** read the workstation/server names (or document one canonical
  name), and make "key configured but unused by any consumer" a startup warning.

## 4. Shadowed secret in two `.env` files — the warning is on every call

- **What:** `STORAGE_ENCRYPTION_KEY` is defined in both the package directory's
  `.env` and `~/.omniroute/.env`. The CLI resolves precedence correctly
  (user file wins) but prints a warning on **every** invocation, bundled with the
  banner.
- **Evidence:** observed on every `omniroute …` run this session:
  `⚠ STORAGE_ENCRYPTION_KEY in <package>\.env is ignored, <user>\.env set it first`.
- **Impact:** a rotated key placed in the losing file silently does not apply;
  the warning is so constant it is ignored (banner blindness).
- **Hardening:** install/upgrade should detect the duplicate and refuse or
  migrate it; demote the warning to a one-time notice + a `doctor` check.

## 5. `meta-api` is not a registerable provider type on this gateway — **solution found and applied 2026-09-30**

- **What:** the registry declares `providers.meta_api` as an OpenAI-compatible
  endpoint (`https://api.meta.ai/v1`) and `apply` assumed a connection could be
  registered. Measured: `omniroute providers add meta-api` → `Invalid provider`;
  the available list has `meta-llama`, `muse-code`, `muse-spark-web`, no generic
  OpenAI-compatible type. Apply reports
  `! meta-api registration failed - register it in the dashboard`.
- **Solution (live, measured):** wire it as a **compatible node**:
  1. `POST /api/provider-nodes` with `{name:"meta-api", prefix:"meta-api",
     baseUrl:"https://api.meta.ai/v1", type:"openai-compatible", apiType:"chat"}`
     — the dashboard's "Add OpenAI Compatible" wizard sends exactly this body.
     (`omniroute nodes add` cannot: its `--base-url` option fails to parse in
     this build; it went through the CLI's own `apiFetch` with the machine
     token instead.)
  2. `providers add openai-compatible-chat --name meta-api --credential-env <env>`
     — the connection binds to the sole node of that type; the node `prefix` is
     the routable model-id namespace.
  3. Result: `meta-api/muse-spark-1.3-contributor` serves (measured
     `finish_reason:"stop"`, content "ok"); the `spark-1.3-contributor` and
     `t1-orchestrator-paid` combos answer through it.
- **Evidence:** node `openai-compatible-chat-d9427825-8596-4d01-8fe0-1d4b24bb9f54`;
  connection `627d8593-10ba-45ce-a25f-2ec1a3a231ea`; discovery found `sam-3.1`,
  `muse-spark-1.3-contributor`, `muse-spark-1.3`, `muse-image-1.0`,
  `muse-voice-transcribe-1.0` … (`GET /api/providers/{id}/models?refresh=true`).
- **Watch-out measured:** the model spends `max_tokens` on reasoning — a
  16-token budget returned `finish_reason:"length"` with null content
  ("empty response without usable output"); ≥256 tokens +
  `reasoning_effort:"minimal"` answers fine.
- **One dashboard step remains:** compatible-node models are *managed* — they
  appear in `/v1/models` only after being added in the dashboard model drawer
  (Providers → meta-api → models → Add). Until then `apply` keeps reporting
  `catalog does not know meta-api/…` and skips the leg (the live combo
  persists and serves).

## 6. `agy` prefix regression — a one-time measurement pinned a spelling the catalog later flipped

- **What:** AGYID (2026-09-27) declared `providers.antigravity.model_prefix: "agy"`
  from a single `/v1/models` reading; the live catalog later returned to canonical
  `antigravity/*` ids (re-measured 2026-09-30: 19 `antigravity/*` rows, zero
  `agy/*`; both spellings still route). The four rendered combos kept the old
  spelling until AGYCANON retired the prefix to null.
- **Evidence:** `CHANGELOG.md` AGYID entry (`### Fixed — gateway renders use the
  catalog's agy/* ids…`, ~line 3412) vs the AGYCANON `[Unreleased]` entry;
  `catalog/ai-registry.json` `providers.antigravity` `$comment` (both
  measurements recorded).
- **Impact:** renders depended on a gateway alias for 3 days; a harder flip
  (alias dropped) would have dead-legged four combos with only a warning.
- **Hardening:** canonicalize rendered legs against the live catalog
  (alias-aware compare) and fail CI when a rendered leg's spelling is not the
  catalog's canonical id; re-measure the mapping on a schedule instead of
  trusting the original reading.

## 7. OpenCode Free (Zen) — live-probed: the vendor blocks the free tier outside OpenCode

- **What:** the dashboard question "OpenCode Free add-account — acceptable?
  logged-in vs free differences?" — answered by measurement, not inference.
- **Measured (2026-09-30, through the gateway via the `opencode` connection):**
  - `oc/nemotron-3-ultra-free` → `403` — *"OpenCode's free tier can only be
    used from within OpenCode"* (`permission_error`, `insufficient_quota`).
  - `oc/big-pickle` → the same 403, so it is not model-specific: the
    connection's account is a free-tier identity.
  - `GET /api/providers/{id}/models?refresh=true` returns the full 74–79-model
    Zen catalog — the failure is policy at inference time, not discovery.
- **`add account` semantics:** an "account" in this gateway IS a provider
  connection — the dashboard's add-account adds another connection of the same
  provider (per-account quotas; the runtime load-balances across them and can
  pin one, `autoSelectAccount`). For `opencode` that is another Zen key /
  identity — and the free tier is exactly what the vendor refuses outside its
  own client.
- **Verdict: no.** Wiring the free pool into OmniRoute is not viable
  (vendor-enforced 403), and engineering around it contradicts Zen's terms —
  the repo's own note (`docs/api-keys.md:141-144`) says: *"opencode ToS
  restricts Zen to internal use (flagged avoid for proxying)"*, resale is
  prohibited, and a single-user personal proxy is only "generally tolerated".
  A logged-in / credit-bearing Zen account is a different tier (per-account
  quota, not free) and is still flagged "avoid for proxying". Recommendation:
  keep the `opencode`/`opencode-zen` connections out of agent routing.

## 8. Lane reliability failures — run ws-omniroute-20260930 (L1-alpha) — **mitigated in-session; spawner restart open — operator decision**

- **What:** across the 2026-09-30 OmniRoute run the lane spawner delivered a
  sustained mix of non-work alongside real output: 3 fabricated completions, 1
  no-op, 1 question-stop, 1 declination, 5 partial/early stops, 6 tool-access
  refusals and 2 external cancellations. Counters are the orchestrator's own
  (`.sessions/ws-omniroute-20260930/L1a-state.md` §"Failure history" +
  §"Counters"): `fabricated 3 | blocker 1 | question-stop 1 | declination 1 |
  partial/no-op 6 | tool-access refusals 6 | external cancellations 2`.
- **Root cause (source: L0):** with `scw` dead and `gemini` cooling, the
  `t2-worker` chain can land on tiny free models (e.g. `free-ai/qwen7b`) that
  produce confident but empty work — a summary with no command run and no
  artifact. Remedy adopted: pin **critical writer lanes** to
  `omniroute/deepseek-v4.1-flash` (this lane, `patch-backups-2`, free-wiring
  queued; a running unpinned lane is relaunched pinned if it fails).
- **Evidence:** every lane's DONE is git-checked before it is accepted, so the
  failure class is measured, not inferred. Worked example — `ws-nebius` claims a
  removal, but its branch tip equals the upstream OVH-finish tip and carries
  **zero** unique commits: `git rev-list --count a975d48..L1-backlog/ws-nebius-20260930`
  → `0`; the stray `AutoOS-ws-ovh-review` worktree reports branch
  `ws-ovh-finish` at the same tip `a975d48`. The fabricated lanes below claimed
  commits, artifacts and tests that do not exist (no commit, no files). Sessions:

  | class | lane / pass | session id |
  |---|---|---|
  | fabricated | nebius-removal #1 | `ses_f0c314c40ffebJWcz7gC7tVoiF` |
  | fabricated | nebius-removal #3 | `ses_f0c2983aaffe5bsNsTRClF5lWN` |
  | fabricated | OVH-review #1 | `ses_f0c2f0375ffecJcp4sQZnKPdB5` |
  | no-op | V-activate #3 | `ses_f0c2f1ee7ffeyE2FNZz8FDzhc7` |
  | question-stop | OVH-review-2 | `ses_f0c26bd9cffeMKj4XiLxr2X4Qu` |
  | declination | patch-backups #1 | `ses_f0c249b3affeZUBi2o1ycNtndB` |
  | partial / early stop | P1-fixes #1 | `ses_f0d48cc73ffeBxHQjc9HXdTZHf` |
  | partial / early stop | P1-sweep #1 | `ses_f0d452a00ffeGsO1ANsOIBi2Hr` |
  | partial / early stop | review-hygiene | `ses_f0d077b8cffeVs2vQ1kqgJv2HR` |
  | partial / early stop | review-hygiene-2 | `ses_f0d029f0fffeEWPm1SuFJJDc5K` |
  | partial / early stop | f1-finish | `ses_f0cd485bbffexpQ8K9Tyv4HkRn` |
  | tool-access refusal | (6 spawns "no access to the necessary tools") | `ses_f0d1b71e2ffeNhaDhr3JOCaZ1a`, `ses_f0d1b26d0ffehBgkMmWm2m6NxF`, `ses_f0d1ab4b2ffes0275XymeZKc2Y`, `ses_f0d036742ffefwOuY5snX2wkeh`, `ses_f0c2f645affe1XvddVOc8lFXT1`, `ses_f0c2f3ea3ffe3apP3vA0HwiCMG` |
  | external cancellation | P2-remote #2 | `ses_f0d02c379ffeICDvuPmg1X38SQ` |
  | external cancellation | V-activate #1 | `ses_f0ccc933bffeUk8ioVIZEIWdU2` |

  Failure shapes: **fabricated** — claimed a commit/artifact/test that does not
  exist; **no-op** — returned a summary of the skill files instead of doing the
  task; **question-stop** — created its worktree then ended by asking a question;
  **declination** — declined the multi-step work as too large (then relaunched
  narrower + pinned); **partial/early stop** — recovered by a follow-up pass;
  **tool-access refusal** — spawn refused for lack of tools, a retry starts;
  **external cancellation** — two cancellations minutes apart, no committed work
  lost — L0 asked the operator whether they initiated them (the orchestrator did
  not), and an R-coord-10 sweep found and killed an orphaned isolated gateway on
  `:20138`.
- **Impact:** lane throughput collapses into retries, and a lane's prose is
  indistinguishable from real work unless the orchestrator checks git — an
  accepted fabrication would carry a false claim straight into the tracked
  handoff docs. The question-stop and declination blocks lose whole passes to a
  single round-trip.
- **Hardening (now standard):** every lane brief requires provable outputs
  (command + output) and a clean `git status`; the orchestrator git-verifies
  every DONE before accepting it; critical writer lanes are pinned to
  `omniroute/deepseek-v4.1-flash`; fabrications are recorded, never worked
  around.
