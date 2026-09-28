# fleet-node — per-host agent: spec v1

Status: SPEC v1, unreviewed. Owner per brief: L2-general, gap spec 1/6 (routing-00).
Source architecture: the private routing plan §§2.2/2.4/10-phase-D, paraphrased below —
this file never quotes it verbatim and invents nothing beyond it.

Citation discipline: every claim about this checkout ends in a `path:line` citation
that resolves in this branch. Anything not verifiable in the checkout is marked
`(inference)`. Placeholders (`<management host>`, `<tailnet>`, `example.internal`)
are never real values — this repository is public.

Related work (not this spec): fleetd (the fleet API/event-bus service, gap-spec 2
owns its API); telemetry ingestion (gap-spec 3; fleet-node is only a source for it).

## 1. Summary

fleet-node is one small, boring agent per host. On startup it registers what this
host can run, its measured free RAM, and its OS/platform with fleetd; afterwards it
launches sessions and workers locally on fleetd's behalf, refuses new launches when
measured free memory drops under a configured threshold, watches every local unit
with lease-based watchdog semantics (expiry, fencing, jittered backoff, a bounded
resume budget, and an operator kill switch), mints a short-lived per-session
capability token for each unit it starts, and exposes per-unit resource metrics for
the telemetry pipeline to pull. It exists because today's dispatch safety is an
orchestrator manually checking memory and a freeze line every heartbeat: the
"LOCAL DISPATCH FREEZE" (fleet-wide cap of 4 running local units, no new unit
while at cap or while measured memory is under its floor — the freeze text lives
in the private run's `briefs/common.md`, outside this checkout, so it is cited by
content here rather than by path) plus the standing headroom rules (at most 3 lanes
+ 3 readers per orchestrator, measured memory floor, heavy suites bounded —
`.agents/skills/unattended-orchestration/SKILL.md:105`) and the per-job memory bound
(heavy jobs under `systemd-run --user --scope -p MemoryMax=2G` —
`.agents/skills/unattended-orchestration/SKILL.md:134`). fleet-node turns that
informal, human-polled discipline into a first-class, per-host, always-on admission
check and supervisor, so a fleet of hosts can share one dispatch policy instead of
one tired orchestrator re-reading `free` output. The failure classifier that
decides whether/how to resume a stopped session — today PowerShell-only
(`Classify-HandoffFailure` in
`.agents/skills/unattended-orchestration/HandoffCore.psm1:284`) — is ported to a
platform-neutral data table (§4) so the Linux and Windows/WSL2 fleet-nodes evaluate
identical rules. D-100 applies throughout: WSL2 is the default Windows route and
the native PowerShell path is frozen to fixes only (`docs/tasks.md:36`) — this
spec's Windows fleet-node targets WSL2, not native.

## 2. Responsibilities

- **Register capabilities with fleetd on startup.** Report: a stable host id
  (inference: derived locally, e.g. machine-id, never a hostname — hostnames are
  secrets-class identifiers in this repo's public-repo rule), the list of agent
  clients this host can spawn (the same client set `spawn` validates against —
  `tools/autoos_agent_mcp.py:6-14`), measured free RAM at registration time, and
  OS/platform (linux / wsl2 / windows / macos). Registration is idempotent (§7):
  re-registering the same host id updates the row, never duplicates it (inference).
- **Launch sessions and workers on this host.** fleet-node is the per-host,
  coordinator-registered form of what `tools/autoos-agent.py`'s `run`/`spawn`
  machinery already does single-host today: `cmd_run` (`tools/autoos-agent.py:4056`)
  validates (PAUSE gate, non-empty task, canonical `--run-id` shape, capability
  choice) before any clone or client start; the MCP `spawn`
  (`tools/autoos_agent_mcp.py:6-14`) validates the same way asynchronously and
  returns a run id at once while the child runs detached in a per-run directory.
  fleet-node keeps that launch path and adds: every launch goes through the
  admission check first, and every launched unit gets a capability token (§5) and a
  lease (§2-watchdog). Nothing here changes the canonical run-id contract: one id
  minted once in UTC, valid as filename + branch + header
  (`tools/autoos-agent.py:784`, `tools/autoos-agent.py:800-816`).
- **Admission by measured RAM.** Before each launch, read measured free memory
  (Linux: `MemAvailable` from `${AUTOOS_MEMINFO:-/proc/meminfo}`, the same source
  the test suite already reads — `tests/run-tests.sh:174-191`; other platforms:
  the platform's equivalent free-memory counter (inference)) and refuse the launch
  when it is under the configured threshold. Refusal shape follows the spawner's
  existing convention: distinct exit code plus a machine-readable reason on stderr
  (the spawner already refuses with `refuse(reason, code)` — e.g.
  `tools/autoos-agent.py:4066-4080`), so fleetd can treat "refused: memory" as
  route-elsewhere rather than failure (inference). The threshold itself is
  configuration, defaulting to the current informal floor (inference: 6 GB fleet
  freeze floor; per-orchestrator 3 GB headroom floor in `SKILL.md:105` — the exact
  default is an open question, §8).
- **Watchdog: leases, fencing, backoff, resume budget, kill switch.**
  - *Lease:* each running unit holds a lease — a claim on its slot with an expiry
    that the unit (or its supervisor sidecar) must renew. A lease that expires
    with no live backing process is the fleet form of today's reconciler, which
    marks `running`/`working` sessions whose pid died as `crashed`
    (`.agents/skills/unattended-orchestration/HandoffCore.psm1:561-632`) and of
    the L1 handoff's stale-helper rule — "helper pid %d … has no live session:
    kill %d"
    (`.agents/skills/unattended-orchestration/l1_handoff.py:98-115`): the dead
    unit is reaped, its slot freed, and a `crashed`-equivalent event is reported
    to fleetd instead of the slot leaking forever.
  - *Fencing:* at most one watchdog instance may act on a lease at a time. Lease
    renewal and lease action (reap/resume/kill) carry a fencing token
    (monotonic generation per lease, inference); an action with a stale token is
    ignored, so a partitioned or double-started fleet-node can never reap and
    resume the same unit twice (inference).
  - *Jittered backoff:* watchdog retries (renewal attempts, resume attempts,
    fleetd reports) back off exponentially with jitter (inference: base and cap
    are configuration; the cap default mirrors the classifier's six-hour ceiling
    convention, `.agents/skills/unattended-orchestration/HandoffCore.psm1:297`).
  - *Max-resume budget:* a unit that keeps dying is resumed at most N times
    (configuration, inference default: small single digits — exact N is an open
    question, §8), then left stopped and surfaced to the operator via
    watchdog-report (§6) instead of looping forever. This is the fleet form of
    the runner's stall budget (stop after 5 stalls in 3 h —
    `.agents/skills/unattended-orchestration/HandoffCore.psm1:944-958`,
    defaults `.agents/skills/unattended-orchestration/HandoffCore.psm1:44-45`).
  - *Kill switch:* an operator or fleetd command stops one unit (`kill
    <run-id>`) or every unit on the host (`kill --all`) immediately; the local
    agent terminates the processes (SIGTERM, then SIGKILL after a grace period,
    inference) and releases the leases. Kill is the highest-priority action: it
    preempts pending resumes (inference).
- **Per-session capability tokens.** On each launch, fleet-node mints a token
  scoped to exactly (project, allowed roles, spawn quota, expiry)
  (`fleetnode-spec.brief` §-bullet; brief-sourced, no checkout path). fleetd
  checks every call against the token and writes an append-only audit log entry
  per call. No shared secrets between hosts — only short-lived per-session tokens
  (inference: requested scope wider than the session's own project/roles is
  refused at mint time, mirroring how `cmd_run` refuses before any side effect —
  `tools/autoos-agent.py:4068-4080`). Token shape and audit shape are in §5.
- **Metrics source for telemetry ingestion.** fleet-node exposes what it is
  running and each unit's resource use (CPU, RSS, lease state, classifier outcome
  of its last stop). It does NOT design ingestion, storage, or dashboards —
  gap-spec 3 owns those; this spec only fixes the field list (§6,
  watchdog-report) so the ingestion spec has a stable source to pull from
  (inference on field stability intent; the `ps --json` precedent —
  `tools/autoos-agent.py:4040-4044` — shows the established row shape to extend).
- **Classifier port (data, not code).** Ship and evaluate the platform-neutral
  classification table of §4. Every stop/resume decision on every platform loads
  the same table file; no platform carries its own fork of the rules (inference).

## 3. Platform design

Both platforms run the same logic (same classifier table from §4, same admission
policy, same token/audit shapes). They differ only in process supervision and
where state lives.

- **Linux: systemd user unit.**
  - *What it runs (inference):* a single long-lived fleet-node process
    (the launch + admission + watchdog + token + metrics responsibilities of §2),
    started as a systemd **user** unit (`autoos-fleet-node.service`,
    `WantedBy=default.target`) so it works identically as root, under sudo, and
    in a container — the same constraint the repo's shell code already honors
    (`AGENTS.md` §3: no `sudo` inside functions; `AUTOOS_SUDO` set once at
    startup). Heavy units it launches keep the existing R-worker-07 wrapper:
    `systemd-run --user --scope -p MemoryMax=2G`
    (`.agents/skills/unattended-orchestration/SKILL.md:134`) — admission checks
    the host, the scope bounds the unit (§5).
  - *Restart policy (inference):* `Restart=on-failure` with
    `RestartSec=` jittered (tens of seconds); restart does NOT resume units by
    itself — on startup the agent reconciles (leases whose backing pids are gone
    are marked crashed and reported, mirroring
    `.agents/skills/unattended-orchestration/HandoffCore.psm1:561-632`), then
    waits for fleetd direction. This keeps a crash-restart from resuming work
    nobody asked to resume.
  - *Where state lives (inference, following existing layout):* run state under
    the run/state dir convention the spawner already uses — per-run dirs with
    `job.json` / `output.log` / `exit.json` (`tools/autoos_agent_mcp.py:10-25`)
    and worker records as today (`ps` reads them from `workers_dir()` —
    `tools/autoos-agent.py:4040-4044`); fleet-node adds a leases file
    (`leases.json`, one record per live unit: run id, expiry, fencing
    generation, resume count) beside them. All state paths are git-ignored run
    state, never tracked files.
  - *Install/upgrade is idempotent (AGENTS.md: safe to run twice):* installing
    the unit writes the unit file only if content differs, runs
    `daemon-reload` only then, and enables/starts only if not already active;
    re-running reports `skipped`, never duplicates the unit (inference).
- **Windows/WSL2: a Windows service fronting the same WSL2-side agent.**
  - D-100: WSL2 is the default Windows route; the native PowerShell path is
    frozen to fixes only (`docs/tasks.md:36`). So the Windows fleet-node does
    NOT reimplement anything natively: the real agent is the same Linux agent
    above, running inside the WSL2 distro as the same systemd/user unit (or its
    distro-supported equivalent where systemd is unavailable (inference)).
  - *The Windows side is a thin service (inference):* a Windows service
    (`AutoOSFleetNode`) whose only jobs are (1) ensure the WSL2 distro is up and
    the in-distro agent unit is active (start/restart it on boot and on failure,
    with the same on-failure + jitter policy), (2) forward host-level stop/kill
    to the in-distro agent, and (3) report "distro down" distinctly from "agent
    down" so fleetd does not mistake a WSL2 outage for an idle host. All
    classification, admission, token, and audit behavior executes in-distro, so
    the two platforms cannot drift.
  - *Memory source on Windows (inference):* the admission check reads the
    distro-visible free memory (same `MemAvailable` source as Linux —
    `tests/run-tests.sh:174-191`) against the same threshold; the thin service
    additionally refuses to start the distro agent when the Windows host itself
    reports sustained memory pressure, but host-level and distro-level checks
    share one threshold definition so operators tune one number.
- **macOS (inference, noted for completeness):** same agent code; memory from the
  platform counter, supervision via launchd. No macOS-specific rules in v1.

## 4. The classifier port

### 4.1 What is being ported

`Classify-HandoffFailure` (`.agents/skills/unattended-orchestration/HandoffCore.psm1:284-349`)
maps a session's transcript tail + exit state to `@{ kind; wait }` where kind is
one of `auth | limit | transient | other`. Order is load-bearing ("Auth is tested
first because it … cannot be waited out" —
`.agents/skills/unattended-orchestration/HandoffCore.psm1:287-291`). Input is
lowercased once (`$t = … ToLower()` —
`.agents/skills/unattended-orchestration/HandoffCore.psm1:293`) and every branch is
a case-insensitive substring/regex test on that lowered text. One shared ceiling
`$capSeconds = 6 * 3600`
(`.agents/skills/unattended-orchestration/HandoffCore.psm1:297`) clamps every
provider-reset-derived wait. This is a **port** of these rules into data, not a
rewrite: every case below must be representable; §4.4 calls out the ones that do
not fit a plain pattern→outcome row.

### 4.2 Case-by-case mapping (in checkout order)

| # | Existing branch (`HandoffCore.psm1` lines) | Matcher (on lowercased tail) | Outcome today | Rule id (inference) |
|---|---|---|---|---|
| 1 | auth — `failed to authenticate\|oauth\|not logged in\|please run /login\|claude login\|invalid api key\|authentication` (`HandoffCore.psm1:299-301`) | regex alternation, substring | `{kind: auth, wait: 0}` — never retried by waiting | `auth-credentials` |
| 2 | epoch usage limit — `usage limit reached\|(\d{9,11})`, reset via `FromUnixTimeSeconds`, `wait = max(60, reset-now+90s)` clamped to cap (`HandoffCore.psm1:302-306`) | regex with one epoch capture | `{kind: limit, wait: computed}` | `limit-epoch-reset` |
| 3 | Antigravity quota — `quota reached\|quota exceeded\|resource_exhausted`, optional `resets in (Hh)?(Mm)?(Ss)?` duration parse; parsed secs when > 0 (clamped), else flat 1800 (`HandoffCore.psm1:313-325`) | regex + optional duration captures; comment notes the JSON form "carries no limit reached and no 429" (`HandoffCore.psm1:307-312`) | `{kind: limit, wait: computed-or-1800}` | `limit-quota-resets-in` |
| 4 | session-limit wall clock — `session limit[^…]{0,40}resets\s+(\d{1,2}):(\d{2})\s*(am\|pm)`, 12h→24h conversion, roll to next day when past, `wait = max(60, reset-now+90s)` clamped to 6 h (`HandoffCore.psm1:333-341`) | regex with hh/mm/meridiem captures | `{kind: limit, wait: computed}` | `limit-wallclock-reset` |
| 5 | generic limit — `usage limit\|session limit\|rate limit\|limit reached\|too many requests\|429` → 1800 (`HandoffCore.psm1:342-344`) | regex alternation, substring | `{kind: limit, wait: 1800}` | `limit-generic` |
| 6 | transient — `overloaded\|529\|500 internal\|internal server error\|econnreset\|etimedout\|enotfound\|socket hang up\|fetch failed\|network\|timed out\|503\|502` → 300 (`HandoffCore.psm1:345-347`) | regex alternation, substring | `{kind: transient, wait: 300}` | `transient-infra` |
| 7 | default (`HandoffCore.psm1:348`) | (no matcher — fallthrough) | `{kind: other, wait: 120}` | `other-unknown` |

### 4.3 The data format ( avant: one file both platforms load)

A single table file (JSON or YAML — exact encoding is an open question, §8;
field names below are normative, inference) — a **list**, because order is
load-bearing (§4.1):

```jsonc
{
  "version": 1,
  // "normalization": lower-case the whole tail once before matching —
  // mirrors $t = $Text.ToLower() (HandoffCore.psm1:293). (inference: field name)
  "normalization": "lowercase",
  // "ceiling_seconds": the one shared cap for reset-derived waits —
  // mirrors $capSeconds = 6*3600 (HandoffCore.psm1:297). (inference: field name)
  "ceiling_seconds": 21600,
  "rules": [
    {
      "id": "auth-credentials",           // stable, kebab-case (inference)
      "match": { "regex": "failed to authenticate|oauth|not logged in|please run /login|claude login|invalid api key|authentication" },
      "outcome": "auth",
      "wait": { "type": "fixed", "seconds": 0 }
    },
    {
      "id": "limit-epoch-reset",
      "match": { "regex": "usage limit reached\\|(\\d{9,11})" },
      // wait computed from capture group 1 as a unix epoch (inference: schema):
      "outcome": "limit",
      "wait": { "type": "epoch_capture", "group": 1, "min_seconds": 60, "pad_seconds": 90 }
    },
    {
      "id": "limit-quota-resets-in",
      "match": { "regex": "quota reached|quota exceeded|resource_exhausted" },
      "outcome": "limit",
      // duration parse is a second-stage matcher over the same tail (inference):
      "wait": { "type": "duration_capture_or_fixed", "regex": "resets in (?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?",
                "groups": [3600, 60, 1], "fallback_seconds": 1800 }
    },
    {
      "id": "limit-wallclock-reset",
      "match": { "regex": "session limit[^\\n]{0,40}resets\\s+(\\d{1,2}):(\\d{2})\\s*(am|pm)" },
      "outcome": "limit",
      "wait": { "type": "wallclock_capture", "groups": [1, 2, 3], "min_seconds": 60, "pad_seconds": 90 }
    },
    {
      "id": "limit-generic",
      "match": { "regex": "usage limit|session limit|rate limit|limit reached|too many requests|429" },
      "outcome": "limit",
      "wait": { "type": "fixed", "seconds": 1800 }
    },
    {
      "id": "transient-infra",
      "match": { "regex": "overloaded|529|500 internal|internal server error|econnreset|etimedout|enotfound|socket hang up|fetch failed|network|timed out|503|502" },
      "outcome": "transient",
      "wait": { "type": "fixed", "seconds": 300 }
    },
    { "id": "other-unknown", "match": null, "outcome": "other",
      "wait": { "type": "fixed", "seconds": 120 } }
  ]
}
```

- **Minimum required fields per rule:** a matcher (the transcript-substring test
  `Classify-HandoffFailure` already does — regex over the normalized tail,
  `null` only for the terminal fallthrough) and the outcome it maps to (`auth |
  limit | transient | other`), plus a `wait` spec (brief-required). Fixed waits
  carry the literal seconds; computed waits name their computation (§4.4).
- **Evaluation (both platforms, identically — inference):** load the table once
  at startup (reload on change); for each classification, normalize the tail
  per `normalization`, walk `rules` in order, first rule whose `match.regex`
  matches (or the `match: null` fallthrough) wins; compute `wait` per its spec;
  clamp any reset-derived wait to `ceiling_seconds`; return `{outcome, wait}`.
  Order-dependence (auth before epoch before quota before wallclock before
  generic before transient before other) is preserved by list order, mirroring
  the function body order (`.agents/skills/unattended-orchestration/HandoffCore.psm1:299-348`).

### 4.4 Cases that do not fit a plain pattern→outcome row (called out, not dropped)

Three branches need clock math no static table cell can hold; the `wait.type`
vocabulary above exists for them, and each evaluator must implement all four
`wait` types or refuse to load the table (inference):

- **Rule 2 (`limit-epoch-reset`):** capture is a unix epoch requiring
  `FromUnixTimeSeconds` + now + 90 s pad + `max(60, …)` + cap
  (`.agents/skills/unattended-orchestration/HandoffCore.psm1:302-306`). A pure
  pattern→outcome table cannot express "seconds until that epoch". The
  `epoch_capture` wait type carries exactly this formula (inference).
- **Rule 3 (`limit-quota-resets-in`):** two-stage — the outcome matcher fires on
  the quota words, then a *second* regex extracts an optional multi-group
  duration where every group is optional and all-absent means "no time given →
  fallback", not zero
  (`.agents/skills/unattended-orchestration/HandoffCore.psm1:314-324`).
  `duration_capture_or_fixed` carries the fallback explicitly so no evaluator can
  misread "resets in" with no numbers as a zero wait (inference).
- **Rule 4 (`limit-wallclock-reset`):** 12-hour→24-hour conversion, midnight
  rollover ("past time means tomorrow"), then the same now+pad+clamp formula
  (`.agents/skills/unattended-orchestration/HandoffCore.psm1:334-340`).
  `wallclock_capture` carries the meridiem-group convention; evaluators must
  share one timezone rule (inference: the host's local zone, matching the
  existing `Get-Date` behavior — flagged as an open question in §8 because fleet
  hosts may span zones).

## 5. Security

- **Capability tokens.** Minted per session/worker at launch (§2). Scope is
  exactly four fields: `project` (what tree the unit may touch), `roles`
  (allowed roles, e.g. writer/reviewer-class verbs — inference on the value
  vocabulary), `spawn_quota` (how many further units this unit may itself
  spawn; leaf workers get 0, enforcing the leaf rule — "a leaf role never
  spawns", `.agents/skills/unattended-orchestration/SKILL.md:136` — at the token
  layer rather than by convention (inference)), `expiry` (short-lived; inference:
  hours, never days — exact lifetime is an open question, §8). Token wire shape
  (inference): opaque random string presented by the caller; all four scope
  fields live server-side in fleetd, never inside the token, so scope cannot be
  edited by the holder. fleetd checks every call against the stored scope and
  rejects (not merely logs) out-of-scope calls (inference). No shared secrets
  between hosts: compromise of one host yields only its live session tokens,
  each expiring on its own (inference).
- **Audit log shape.** Append-only, one JSON object per line (the repo's
  established ledger pattern: schema-validated, secret-redacted NDJSON append —
  `Write-HandoffLedgerEvent` in
  `.agents/skills/unattended-orchestration/HandoffCore.psm1:468-519`), with at
  minimum: `timestamp` (UTC ISO), `token_id` (which session token acted —
  inference), `actor` (host/unit id — inference), `action` (the fleetd call —
  inference), `scope_at_check` (the scope the call was checked against —
  inference), `decision` (`allow | deny` — inference). Secret handling follows
  the existing redactor: key-name-matched fields and bearer-shaped strings are
  replaced before persistence (`Redact-HandoffSecrets` in
  `.agents/skills/unattended-orchestration/HandoffCore.psm1:380-439`) — tokens
  themselves are never written to the audit log, only their ids (inference).
- **Composition with R-worker-07 (absorbs, does not replace).** fleet-node's
  admission check (§2) decides *whether a new unit may start* given measured
  host memory; the `systemd-run --user --scope -p MemoryMax=2G` requirement
  (`.agents/skills/unattended-orchestration/SKILL.md:134`) bounds *how much a
  started heavy unit may consume*. Both stay: admission without bounding still
  lets one unit eat the host; bounding without admission still lets N units each
  eat 2 GB. The per-unit bound remains mandatory for heavy jobs on every
  platform (inference: the WSL2-side agent applies the same scope wrapper).

## 6. Interface to fleetd

Shapes (field lists), not API prose — gap-spec 2 owns fleetd's actual endpoints.
All payloads use the canonical run id where a unit is named
(`tools/autoos-agent.py:784`, `tools/autoos-agent.py:800-816`).

- **`register`** (fleet-node → fleetd, on startup + on capability/memory change
  beyond a threshold — inference on re-registration trigger): `{host_id,
  platform, clients[] (spawnable agent clients — the `list_clients` set,
  `tools/autoos_agent_mcp.py:6-14`), free_ram_mb (measured, same source as
  admission — `tests/run-tests.sh:174-191` on Linux), total_ram_mb (inference),
  max_units (configured slot count — inference), agent_version (inference)}.
  Response (inference): `{accepted, poll_interval_s, threshold_overrides?}` —
  fleetd may push a per-host threshold override, which replaces the local
  default until the next registration.
- **`launch`** (fleetd → fleet-node; local shape mirrors today's async spawn —
  validate, detach, return id at once — `tools/autoos_agent_mcp.py:6-14`):
  `{run_id (canonical, fleetd-minted or fleet-node-minted — the handed-in id
  wins, per the FLEETP0b rule — `tools/autoos-agent.py:1802-1808`), task,
  client, tier/model, token_scope {project, roles, spawn_quota, expiry} (§5)}.
  Result: `{run_id, accepted | refused, reason (e.g. `refused: memory` with
  measured value — the `refuse(reason, code)` convention,
  `tools/autoos-agent.py:4066-4080`), token_id (inference)}`. A refused launch
  starts nothing and writes nothing except the refusal report (inference,
  mirroring `cmd_run` refusing before any clone/record/client start —
  `tools/autoos-agent.py:4068-4080`).
- **`watchdog-report`** (fleet-node → fleetd, periodic + on events): per unit
  `{run_id, state (running | crashed | resume_pending | stopped_by_operator —
  inference on vocabulary), lease_expiry, fencing_generation (inference),
  resume_count, last_classifier {outcome, wait} (§4 — inference on inclusion),
  cpu_s, rss_mb (resource use for gap-spec 3 to pull — §2-metrics)}` plus host
  `{free_ram_mb}`. Shape extends today's `ps --json` `{workers[], dir}`
  (`tools/autoos-agent.py:4040-4044`) with lease and resource fields (inference).
- **`kill`** (fleetd/operator → fleet-node): `{run_id | --all, reason
  (inference)}`. Effect: terminate now (SIGTERM → SIGKILL after grace —
  inference), release leases, report the stop in the next watchdog-report.
  Highest priority: preempts pending resumes (§2-kill-switch) (inference).

## 7. Migration

- **Today's pattern (brief-sourced):** an orchestrator manually checks
  `MemAvailable` plus `autoos-agent.py ps --json` before every dispatch — the
  run's own heartbeat practice, where the freeze line is re-read each cycle and
  a same-batch check-then-spawn demonstrably fails to gate (the measured
  lesson: a memory check and its spawn in one parallel batch could not gate the
  spawn — private run inbox, cited by content, no checkout path). The spawner
  already refuses unconditionally on some gates (PAUSE — exit 3 —
  `tools/autoos-agent.py:4056-4067`); memory admission joins that family as a
  gate *inside* the launch path rather than advice *around* it, which is exactly
  what closes the check-then-spawn race (inference).
- **Cutover without breaking a mid-run (inference):** fleet-node ships
  observe-first — register + report + classify with admission in
  warn-but-allow mode, so its measurements can be diffed against the
  orchestrator's manual checks for at least one full run before enforcement is
  flipped per host. Enforcement flips per host (not fleet-wide at once), and a
  host with no fleet-node keeps today's manual discipline, so partial rollout
  never blocks dispatch anywhere.
- **Safe to run twice (AGENTS.md idempotency bar):**
  - *Registration* is upsert-by-`host_id`: re-registering updates the fleetd
    row, never creates a second host (inference).
  - *Lease renewal* is idempotent: renewing an unexpired lease with the current
    fencing token extends it to the same expiry regardless of how many times the
    renewal is delivered (at-least-once delivery is safe — inference, mirroring
    the repo's at-least-once read convention in
    `docs/plans/2026-09-28-restart-spec.md:39-44`).
  - *Unit install* (systemd unit file, Windows service registration) writes only
    on content change and reports `skipped` on re-run (§3) (inference).
  - *Kill* is idempotent: killing an already-stopped unit reports stopped, not
    an error (inference).
- **Never overwrite configs wholesale (AGENTS.md):** fleet-node reads existing
  unit files / service configs, appends or patches its own stanza
  idempotently, and writes back; threshold/lease/ceiling settings live in a
  fleet-node-owned config file that carries no operator content to clobber
  (inference).

## 8. Open questions

Format note: the brief requires the `Q: FLEETSPEC |`-style format "already used
in `docs/plans/2026-09-28-fleet-console-spec.md` §12" — that file does not exist
in this checkout (checked `docs/plans/` listing; no console-spec file present),
so the exact §12 punctuation is reconstructed from the brief's description
(inference); the `Q: FLEETSPEC | <topic> | <question> | <options>` shape below
is chosen to slot into the same review process once the console spec lands.

- `Q: FLEETSPEC | admission-default | What is the default admission threshold: the 6 GB fleet freeze floor, the 3 GB per-orchestrator headroom floor (SKILL.md:105), or a per-host fraction of total RAM? | options: (1) 6 GB flat (2) 3 GB flat (3) fraction-of-total (e.g. 25% free) with flat floor |` (inference: needs operator/router decision; the two floors in the checkout disagree because they govern different scopes.)
- `Q: FLEETSPEC | resume-budget-N | What is the max-resume budget N before a flapping unit stops and surfaces to the operator? | options: (1) 3 (2) 5 mirroring the runner stall budget (HandoffCore.psm1:44-45) (3) per-unit-class values |` (inference.)
- `Q: FLEETSPEC | token-lifetime | How short is "short-lived" for capability tokens: hours, one unit lifetime, or one fleetd call? | options: (1) unit lifetime, expiry = lease end (2) fixed hours with renewal (3) per-call |` (inference.)
- `Q: FLEETSPEC | classifier-encoding | JSON or YAML for the §4 table file, and where does it live so both platforms load byte-identical content? | options: (1) JSON beside the agent binary, hash-pinned (2) YAML in repo config dir (3) served by fleetd at registration |` (inference.)
- `Q: FLEETSPEC | wallclock-timezone | Which timezone evaluates the §4 rule-4 wall-clock reset when fleet hosts span zones: each host's local zone (today's Get-Date behavior, HandoffCore.psm1:337-340) or UTC fleet-wide? | options: (1) host-local (status quo) (2) UTC everywhere |` (inference.)
- `Q: FLEETSPEC | slot-count | Is max_units (§6 register) static configuration, derived from total RAM, or assigned by fleetd per host? | options: (1) static config (2) RAM-derived (3) fleetd-assigned |` (inference.)
- `Q: FLEETSPEC | kill-grace | What is the SIGTERM→SIGKILL grace period for kill, and does --all drain (let units checkpoint) or terminate immediately? | options: (1) fixed 30 s grace, immediate --all (2) configurable grace, --all drains first |` (inference.)

## 9. Closing table — lanes + estimated Claude tokens

Budget mode (per brief: writers/first-reviewers are free models, Claude only
reviews/finals) — every row's Claude estimate stays small (under 1M weighted
tokens each). No lane below looks larger than that; none is flagged (inference:
sizing judgement, not measurement).

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| fleet-node-classifier | §4 table schema + loader/evaluator in the shared language + ports of all 7 rules with failing-first tests per branch (auth/epoch/quota/wallclock/generic/transient/default) and a parity test against `Classify-HandoffFailure` (`HandoffCore.psm1:284-349`) | ~150k | 1–2 days | nothing |
| fleet-node-linux | §2–§3 Linux agent: registration, admission gate in the `cmd_run` path (`tools/autoos-agent.py:4056`), leases + fencing + resume budget + kill, token minting, watchdog-report, systemd unit, idempotent install | ~400k | 3–5 days | fleet-node-classifier (for §4); fleetd API (gap-spec 2) for wire compat, stub-able until then |
| fleet-node-wsl2 | §3 Windows side: thin `AutoOSFleetNode` service (distro ensure-up, stop-forward, distro-down reporting) + in-distro agent deploy; D-100 conformance (WSL2 only, `docs/tasks.md:36`) | ~250k | 2–4 days | fleet-node-linux (reuses its agent); a Windows/WSL2 test host |
| fleet-node-fleetd-wire | §5–§6: token scope enforcement + audit log against real fleetd once it exists; cutover from stub; observe-first rollout + enforcement flip per §7 | ~200k | 2–3 days | fleetd (gap-spec 2); fleet-node-linux |
