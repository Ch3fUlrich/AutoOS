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

## 8. Storage Key Incident — 2026-09-30

- **What:** The gateway's user config file `~/.omniroute/.env` was rewritten with a 3-character STORAGE_ENCRYPTION_KEY plus `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=4`. The short key does not match the database. From the next gateway restart (13:39:12Z), every credential decrypt failed — 524 "Auth tag validation failed" lines across all 29 providers, surfacing as mass 401s / "authentication expired." The original key was never recovered; recovery = set/keep a file key, restart, then rotate every connection's credential (`omniroute providers rotate <conn> --from-env <VAR> --yes --skip-test`; script pattern: `logs/rotate-tmp.ps1`; 28 rotated; `providers test-all` green afterwards). DB content was intact throughout. Most plausibly a fix lane persisted the admission knob into that file (lane reports claimed the file was untouched; the file content+mtime falsify that). Full write-up: `docs/handoff/2026-09-30-storage-key-incident.md`.
- **Evidence:**
  - Log Line Counts: 524 "Auth tag validation failed" lines across all 29 providers, starting at the next gateway restart (2026-09-30 13:39:12Z).
  - File Modification Time: `~/.omniroute/.env` mtime 2026-09-30 15:35:29 local.
  - File Content: 3-char `STORAGE_ENCRYPTION_KEY` (value not quoted — secret) + `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=4` (the admission knob a fix lane was persisting).
  - DB Content: Intact throughout (verified via `providers test-all` green after recovery).
  - Recovery: 28 connections rotated; `providers test-all` green.
- **Impact:** All 29 providers experienced authentication failures; consumers saw mass 401s / "authentication expired" (indistinguishable from "all api keys destroyed").
- **Recovery Procedure:**
  1. Set/Keep a file key in `~/.omniroute/.env` (the original was never recovered — do NOT copy from the npm package `.env` template, which ships EMPTY; see finding #11).
  2. Restart the gateway (use the correct launcher — see finding #9: `%APPDATA%\npm\omniroute.cmd`, not bare `omniroute`).
  3. Rotate each connection's credential: `omniroute providers rotate <conn> --from-env <VAR> --yes --skip-test` (script: `logs/rotate-tmp.ps1`; 28 rotated).
  4. Test: `omniroute providers test-all` → green.
- **Prevention Rules:**
  1. Never write the gateway data-dir key/config files (`~/.omniroute`); operator-only. (Now skill rule R-coord-15.)
- **Status:** [fixed in-session]

## 9. Gateway Process Death + Restart Machinery

- **What:** The gateway process died ~12:49:56Z with no shutdown log and stale .pid files under ~/.omniroute/{server,supervisor}. The launcher resolves the npm .ps1 shim incorrectly.
- **Evidence:**
  - `configuration/start-stack.ps1` line 111 `Start-Process -FilePath 'omniroute'` resolves the npm .ps1 shim → "%1 is not a valid Win32 application". The working form is `Start-Process '%APPDATA%\npm\omniroute.cmd' -ArgumentList '--no-open','--port','20128'`.
  - Fixed launcher saved in commit d88ed3b (branch L1-backlog/ws-p0-admission-fix-20260930).
  - `configuration/omniroute/apply.ps1` carries the same bare-`omniroute` pattern as a follow-up (open).
- **Impact:** The gateway process failed to restart automatically, leading to extended downtime.
- **Recovery Procedure:**
  1. Correct the launcher script to use the correct path to the gateway executable (`%APPDATA%\npm\omniroute.cmd`, not bare `omniroute`).
  2. Restart the gateway process manually if necessary.
- **Prevention Rules:**
  1. Ensure the launcher script uses the correct path to the gateway executable.
  2. Fix `apply.ps1`'s bare-`omniroute` pattern (open follow-up).
- **Status:** [fixed in-session] (launcher); [open — operator decision] (apply.ps1 follow-up)

## 10. Apply Skips Existing Connections

- **What:** `configuration/omniroute/apply.ps1` intentionally skips already-registered connections (lines ~322-340).
- **Evidence:**
  - `configuration/omniroute/apply.ps1` lines ~322-340
- **Impact:** Credentials refresh must use `providers rotate`, not apply.
- **Prevention Rules:**
  1. Document the behavior and ensure it is understood by all users.
- **Status:** [open — operator decision]

## 11. Template Shadowing

- **What:** The npm package ships `.env` as a 170 KB commented template with an EMPTY STORAGE_ENCRYPTION_KEY; the user file in `~/.omniroute` wins.
- **Evidence:**
  - Package `.env` template and user `~/.omniroute/.env` file.
- **Impact:** Never treat the package file as the key source; never "restore" by copying from it.
- **Prevention Rules:**
  1. Document the behavior and ensure it is understood by all users.
- **Status:** [open — operator decision]

## 12. Admission Gate

- **What:** `chat_admission_busy` killed three lanes; root cause `chatBodyAdmission.ts` heavy-slot=1 default; fixed via `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT` 1→4 (commit c41de4a) + launcher persistence (d88ed3b).
- **Evidence:**
  - Commit c41de4a and d88ed3b.
- **Impact:** The admission gate was causing lanes to fail.
- **Recovery Procedure:**
  1. Increase the `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT` setting to 4.
  2. Persist the launcher with the correct settings.
- **Prevention Rules:**
  1. Monitor the admission gate settings and adjust as necessary.
- **Status:** [fixed in-session]

## 13. Free-Model Rate Limits

- **What:** `opencode/muse-spark-1.3-contributor-free` rate-limits under orchestrator spawn bursts; stagger spawns + back off; a standby direct free model verified today: `opencode/nemotron-3-ultra-free`.
- **Evidence:**
  - Observed rate-limiting issues with `opencode/muse-spark-1.3-contributor-free`.
  - Verified `opencode/nemotron-3-ultra-free` as a standby model.
- **Impact:** Rate-limiting issues can cause delays and failures in model inference.
- **Recovery Procedure:**
  1. Stagger model spawns to avoid rate-limiting issues.
  2. Use a standby model like `opencode/nemotron-3-ultra-free` when necessary.
- **Prevention Rules:**
  1. Monitor rate-limiting issues and adjust model usage accordingly.
- **Status:** [open — operator decision]

## 14. Open Fixes

- **What:** Deepseek `reasoning_text` 400 (`The reasoning_text in the thinking mode must be passed back to the API`).
- **Evidence:**
  - Observed 400 errors with Deepseek `reasoning_text`.
- **Impact:** The error prevents the use of Deepseek in thinking mode.
- **Status:** [open — hardening idea]

- **What:** Vertex leg 400 `Requests ending with a model turn are not supported` on agentic shapes during gemini-leg cooldowns.
- **Evidence:**
  - Observed 400 errors with Vertex leg during cooldowns.
- **Impact:** The error prevents the use of Vertex leg during cooldowns.
- **Status:** [open — hardening idea]

- **What:** Scaleway qwen leg 400 `payload validation: max_completion_tokens is limited to 16384 for qwen3-235b-a22b-instruct-2507` (gateway should clamp per-model output).
- **Evidence:**
  - Observed 400 errors with Scaleway qwen leg.
- **Impact:** The findings are documented and the operator has been informed.
- **Status:** [open — hardening idea]

- **What:** Operator wants fast failover away from rate-limited legs.
- **Evidence:**
  - Operator request for fast failover.
- **Impact:** Rate-limited legs can cause delays and failures in model inference.
- **Status:** [open — operator decision]

