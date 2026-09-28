# Telemetry ingestion — the join, not a pipeline: spec v1

Status: SPEC v1, unreviewed. Owner per brief: L2-general, gap spec 3/6 (routing-00).
Source architecture: the private routing plan §18, paraphrased below —
this file never quotes it verbatim and invents nothing beyond it.

Citation discipline: identical to the two sibling specs — every claim about this
checkout ends in a `path:line` citation that resolves on this branch
(`L2-general/telemetry-spec`), verified by a script pass before finishing.
Anything not verifiable in this checkout is marked `(inference)` or
`(brief-sourced)`. Content from the sibling branches is cited by
branch + file + line and explicitly flagged **(sibling-branch)** — those lines
were verified with `git show <branch>:<file>` before citing, not trusted from
the brief. Placeholders (`<management host>`, `<tailnet>`, `example.internal`)
are never real values — this repository is public (`AGENTS.md:16`).

Related specs (sibling branches, NOT in this checkout): fleet-node
(branch `L2-general/fleetnode`,
`docs/plans/2026-09-28-fleet-node-spec.md`) owns the per-host agent — this spec
is a metrics *consumer* of it and does not redesign its watchdog-report shape
(sibling-branch `L2-general/fleetnode`,
`docs/plans/2026-09-28-fleet-node-spec.md:356-393`); fleetd
(branch `L2-general/fleetd-spec`,
`docs/plans/2026-09-28-fleetd-eventbus-spec.md`) owns the fleet API/event bus —
telemetry is NOT fleetd's job per that spec's own scoping, which this spec
reuses without duplicating its §3 (sibling-branch `L2-general/fleetd-spec`,
`docs/plans/2026-09-28-fleetd-eventbus-spec.md:23-51`); the fleet console
(branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md`) owns the UI that renders what
this spec produces.

Note on a brief pointer (verified, not trusted): the brief says the console
spec's §12 question format "exists in this checkout" — it does not.
`docs/plans/2026-09-28-fleet-console-spec.md` is absent from this checkout
(`ls docs/plans/` shows no such file) and lives on sibling branch
`L2-general/fleetspec`. It is cited below as sibling-branch, with line numbers
verified via `git show`.

## 1. Summary

Telemetry ingestion is **mostly a join, not a build**. The private plan's §18
finding (brief-sourced) is that most of what a "telemetry ingestion" system
would build already exists, tested, inside OmniRoute: request/response logs,
per-session conversations, per-call audit, a per-request routing ledger, combo
health, and scoped access tokens. A read-only code audit commissioned by the
operator established this (brief-sourced; OmniRoute's source is not in this
checkout, so every OmniRoute-internal claim below is brief-sourced, never a
checkout citation). This spec therefore designs **no new collection machinery**
for anything §2 lists as already existing — it designs the join across three
existing source families by one key, plus the data that join produces for the
console to read.

The three families (brief-sourced, the plan's own conclusion): (1) Claude's own
OTEL token-usage export (cumulative); (2) this repo's own worker records (the
`ps --json` rows / per-run `job.json` files); (3) OmniRoute's own call logs /
routing ledger — **joined by the canonical run id**. "No Grafana UI"
(brief-sourced): the console (sibling branch `L2-general/fleetspec`) renders
telemetry directly; this spec designs only the join and the joined row, never a
dashboard (§5).

Relationship to the two sibling specs: fleet-node is a metrics **source** for
this spec — its `watchdog-report` carries per-unit CPU/RSS plus host free RAM
for the telemetry pipeline to pull (sibling-branch `L2-general/fleetnode`,
`docs/plans/2026-09-28-fleet-node-spec.md:356-393`), and this spec consumes
that shape unchanged. fleetd is **not** where telemetry lives: fleetd is a thin
coordinator API over a bought event bus, and per that spec's own scoping the
heavy lifting happens elsewhere (sibling-branch `L2-general/fleetd-spec`,
`docs/plans/2026-09-28-fleetd-eventbus-spec.md:23-51`). Telemetry rows are
produced by a read-through join (§6) and read by the console; fleetd neither
stores them nor serves them.

## 2. Sources

Seven legs. The five OmniRoute legs (§2.1–§2.5) are brief-sourced — paraphrased
from the audit via the brief, not re-verifiable from this checkout. The Claude
OTEL leg (§2.6) was searched for in this checkout and found absent, so the
whole leg is marked `(inference)`. This repo's own worker records (§2.7) are
fully checkout-cited. Fleet-node metrics (§2.8) are a sibling-branch source.

### 2.1 Logs / call logs / proxy logs / console logs / timeline (brief-sourced)

OmniRoute keeps full request/response bodies as artifacts, with summaries in a
database (brief-sourced). This is the raw record behind every agent gateway
call: what was sent, what came back, when, under which key. Reached
(inference) by a read-only API call against OmniRoute's management surface —
never by opening its database file (§6). Nothing here is built; the log store
already exists and this spec only reads it.

### 2.2 Conversations (brief-sourced store; checkout-verifiable key)

Every turn of one session is threaded into one conversation, keyed by the
`x-omniroute-session-id` header (brief-sourced). The sending side IS
checkout-verifiable: the header name is `SESSION_TAG_HEADER =
"x-omniroute-session-id"` (`tools/autoos-agent.py:900`), the value for one run
is `<tag>/<run-id>` (`tools/autoos-agent.py:913`), both headers for a run are
stamped together by `gateway_headers`
(`tools/autoos-agent.py:938`), and the lane tag itself is minted by
`session_tag` (`tools/autoos-agent.py:948`). So a conversation is addressed by
the same canonical run id the join uses (§3): the full header value isolates
one run, its prefix isolates one lane (inference on the prefix/exact-match
reading; the grouping semantics are checkout-verified in §2.7's usage report).
Reached (inference) as a read-only conversation fetch by that header value;
the console links to it rather than copying transcript text (§7).

### 2.3 Audit (brief-sourced)

Per-call tool audit (MCP audit), A2A task lifecycle with cost, and a
per-request routing ledger (`routing_decisions`: combo, provider, score,
factors, cost, latency) — all keyed by API key (brief-sourced). This is the
"why this call went where it went" record and the per-call cost evidence. The
checkout side holds no copy of this ledger (inference — no routing-decisions
store was found in this checkout); it is read (inference) through OmniRoute's
read surface keyed by the run's gateway calls. The joined row carries an audit
trail *link*, never a copied audit body (§3, §7).

### 2.4 Combo health (brief-sourced store; checkout-verified relative)

Learned score, success rate, latency, and `excluded_until` per combo/provider
(brief-sourced) — already the conceptual input to this repo's own resolver
track record. The repo side is checkout-verifiable: the track record is
append-only `logs/routing/track-record.jsonl` (`tools/autoos_track.py:1`), one
line per finished run, with the exact schema `route, class, served_leg,
bucket, effort, tokens_in, tokens_out, cost, latency_s, gate, failure_class`
(`tools/autoos_track.py:20`). How OmniRoute's combo health relates to this
file is explicitly NOT assumed: unless verified, they are not the same store
(inference) — the track record is the repo's local resolver memory, combo
health is the gateway's learned state, and the join reads both without merging
their writes.

### 2.5 Access tokens (brief-sourced)

Named, scoped, revocable tokens with `whoami` — one per fleet host gives
per-host attribution and one-click revocation "for free," which is telemetry's
attribution layer (brief-sourced). Every gateway call a host's agents make
arrives already stamped with whose call it was, so the join never has to infer
ownership from timing or logs. Token issuance/rotation UI is not this spec's
work (§5); the join only assumes one stable token identity per host
(inference).

### 2.6 Claude OTEL token-usage export (inference — genuinely absent)

(Whole leg `(inference)`, stated plainly:) a checkout-wide search for
`otel|opentelemetry|D-023|token-usage` finds **no Claude OTEL token-usage
export anywhere in this checkout. The only hits are unrelated: per-request
token-usage *log lines* in the probe harness (`tools/probe_common.py:282`), a
transitive `@opentelemetry/api` entry in a vendored MCP server's lockfile
(third-party code, never edited), and remote-access docs prose. There is no
OTEL collector config, no OTEL receiver, no cumulative-usage exporter in the
tracked tree. So the OTEL leg of the join is designed here against the brief's
one-line description — "Claude's own OTEL token-usage export (cumulative)"
(brief-sourced as "D-023") — and every sentence about how it is reached is
`(inference)`: assumed to be a cumulative per-run token counter pulled
read-only from the host where the Claude session ran, stamped with the same
canonical run id (§3). If the export's event shape carries transcript text,
§7's redaction gate applies before anything leaves the host. The first
implementation task for this leg is confirming the export exists at all (§8,
`otel-scope`).

### 2.7 This repo's own worker records (checkout-verified)

The spawner already writes everything the join needs on the repo side. Each
run lives in a per-run directory carrying `job.json` (the request, the
canonical run id, argv, pid, route, start time), `output.log`, `exit.json`,
and ask-back files (`tools/autoos_agent_mcp.py:16`); a run's lifecycle state
uses the A2A vocabulary `submitted → working → completed | failed | canceled`,
with `rejected` for refused spawns and `input_required` while a
`question.json` waits without an `answer.json`
(`tools/autoos_agent_mcp.py:27`, `tools/autoos_agent_mcp.py:84`). The live
worker record is written atomically (`tools/autoos-agent.py:3851`), its id IS
the plan's minted run id, and it carries `parent_run_id` (the spawn tree),
`session_tag`, client, model, route combo, and the full `route_plan`
(`tools/autoos-agent.py:3860`). Live plus recently-exited workers are listed
by `ps --json` as `{"workers": rows, "dir": directory}`
(`tools/autoos-agent.py:4104`). Lane/run grouping off the session tag is the
existing usage report's semantics: lane is the part before the first `/`, run
is the part after the last `/` when it is a run id
(`tools/autoos_usage.py:51`, `tools/autoos_usage.py:136`,
`tools/autoos_usage.py:141`), with run-id recognition in
(`tools/autoos_usage.py:128`) and the `lane` dimension keyed on the row's
session tag (`tools/autoos_usage.py:461`; row fields incl. `sessionTag` at
`tools/autoos_usage.py:37`). Reached by a file read — no API, no new writer.

### 2.8 Fleet-node resource metrics (sibling-branch source)

Per-unit CPU/RSS plus host free RAM arrive in fleet-node's `watchdog-report`,
which the sibling spec explicitly shapes so "the ingestion spec has a stable
source to pull from" (sibling-branch `L2-general/fleetnode`,
`docs/plans/2026-09-28-fleet-node-spec.md:356-393`). This spec pulls that
report read-only and treats it as the resource leg of the joined row (§3). It
does not rename, reshape, or re-collect anything on the fleet-node side —
fleet-node remains the producer, this spec the consumer (§1, §5).

## 3. The join

The join key is the canonical run id, and this is the one part of the design
that is fully checkout-verifiable end to end. A run id is minted once, in UTC,
as `YYYYMMDD-HHMMSS-<slug>-<hex6>` (`tools/autoos-agent.py:806`); exactly that
shape — filename, branch name, and header value at once — is enforced by
`is_canonical_run_id` (`tools/autoos-agent.py:793`, shape comment at
`tools/autoos-agent.py:784`). The same id rides the gateway twice: inside
`x-omniroute-session-id` as `<tag>/<run-id>` (`tools/autoos-agent.py:913`)
and beside it as the bare `X-AutoOS-Run-Id` header
(`tools/autoos-agent.py:903`), stamped as a pair by `gateway_headers`
(`tools/autoos-agent.py:938`). It names the run's directory, its worker
record, its branch, and its gateway conversation alike
(`tools/autoos-agent.py:3860`, `tools/autoos-agent.py:900`).

The join is therefore five-plus lookups on one string, executed read-only at
console-read time (§6):

| leg | key into the source | what comes back |
|---|---|---|
| gateway cost | run id → session-tag exact match, then the proven priced join (§4) | tokens in/out/cache, `cost_usd`, provider/model per request |
| conversation | full `x-omniroute-session-id` value (§2.2) | conversation link (id + viewer reference, never copied text) |
| audit | run's gateway calls → routing ledger rows (§2.3, brief-sourced) | audit trail link (combo, provider, score, factors per call) |
| combo health | combo/provider used by the run (§2.4, brief-sourced) | learned score, success rate, latency, `excluded_until` at read time |
| Claude OTEL | run id against the cumulative export (§2.6, inference) | cumulative token counts for the run |
| worker lifecycle | run id → worker record / `job.json` (§2.7) | state, `parent_run_id`, client, model, route, rc, timing |
| resource use | run id → fleet-node `watchdog-report` (§2.8, sibling-branch) | CPU seconds, RSS, host free RAM at report time |

What the joined row looks like (per run — shape `(inference)`, fields all
sourced above): `run_id`, `parent_run_id`, `session_tag`, `host_id`,
`client`, `model`, `provider/combo used`, `tokens_in`, `tokens_out`,
`tokens_cache`, `cost_usd` (NULL → rendered "unknown", per the console spec's
non-gateway rule, sibling-branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md:323-337`), `latency`,
`conversation_link`, `audit_trail_link`, `worker_state` (A2A vocabulary,
`tools/autoos_agent_mcp.py:84`), `resource_use`. A leg with no data for the
run yields NULL for its columns, never a failed row: unpriced clients,
pre-OTEL Claude sessions, and hosts without fleet-node all still render.

## 4. Cost per run (P2, already proven)

This section cites and summarizes the existing proof. It does not re-derive
it. The console spec's §5.3 (sibling-branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md:323-337`) establishes, verified
2026-09-28 against OmniRoute's source: the join key is
`call_logs.correlation_id ↔ request_cost_ledger.request_id` — both fall back
to the request's `traceId` when no explicit correlation id is supplied — and
`call_logs.id` is a fresh UUID that joins to nothing. Session/run threading
needs no upstream change: the spawner's existing `x-omniroute-session-id`
header lands in the already-shipped `call_logs.session_tag` column carrying
`<lane>/<task slug>/<run-id>` (sibling-branch §5.1a at
`docs/plans/2026-09-28-fleet-console-spec.md:308-318`; checkout mechanics at
`tools/autoos-agent.py:900`, `tools/autoos-agent.py:913`). The upstream ask
shrank to one new read-only management endpoint over two existing columns —
`GET /api/usage/by-run?run_id=` — with no schema or migration change, and it
is already filed as `diegosouzapw/OmniRoute#14992`; §12's Q-011 is CLOSED on
that basis (sibling-branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md:802-843`).

What is LEFT for this spec beyond that one proven join: everything else in §3
still needs its own join logic — the conversation leg (§2.2), the audit leg
(§2.3), the combo-health leg (§2.4), the Claude OTEL leg (§2.6, inference),
and the worker-record leg (§2.7) have no proven endpoint and no filed PR. The
proven cost join is the template (one key, API-level, cached — §6), not the
finished work: generalizing that ONE join (cost) into the FULL telemetry join
is this spec's job, and the join key's existence is settled fact, cited above,
not re-investigated here.

## 5. What this spec does NOT build

Explicit non-goals — each is owned elsewhere or forbidden:

- **No new logging pipeline.** OmniRoute's logs/call logs/proxy logs/console
  logs/timeline already exist (brief-sourced, §2.1); fleetd and the console do
  NOT build a second logging/metrics pipeline (brief-sourced, §1). Telemetry
  ingestion reads; it never collects.
- **No Grafana, no dashboard, no Alloy UI.** "No Grafana UI" (brief-sourced):
  the console renders telemetry directly (sibling branch
  `L2-general/fleetspec`); this spec produces only the join and the joined row
  (§3) for the console to read.
- **No re-implementation of Conversations, Audit, or Combo Health.** Those are
  OmniRoute stores, read through OmniRoute's surface (brief-sourced §2.2–§2.4).
  Copying any of them into a second store would fork the truth about cost and
  routing; the API-level-join rule (§6) forbids it.
- **No fleetd changes.** Telemetry is not fleetd's job per that spec's own
  scoping (sibling-branch `L2-general/fleetd-spec`,
  `docs/plans/2026-09-28-fleetd-eventbus-spec.md:23-51`); this spec cites its
  §3 surface and duplicates none of it.
- **No watchdog-report redesign.** Fleet-node's report shape is settled on its
  branch (sibling-branch `L2-general/fleetnode`,
  `docs/plans/2026-09-28-fleet-node-spec.md:356-393`); telemetry consumes it
  verbatim.
- **No new token issuance.** Per-host access tokens are OmniRoute's
  attribution layer (brief-sourced, §2.5); rotation/issuance policy belongs to
  whoever owns that store, not to this spec (§8 notes the open question only).

## 6. Storage

**Picked: always a live read-through join; no new telemetry store.** The join
in §3 executes at console-read time against the sources in §2, and its rows
are not persisted anywhere new. The single exception is inherited, not built:
the priced cost leg reuses the console spec's `run_usage` **cache table**
(sibling-branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md:154-177`) — filled by the
API-level join inside OmniRoute's per-run cost endpoint and cached by the
reader, never by joining databases. Conversation, audit, combo-health, OTEL,
worker-record, and resource legs are never cached by this design: they are
read live on each console render.

Justification against the console spec's boundary rule: the join stays
API-level, never database-level (sibling-branch §5.3 at
`docs/plans/2026-09-28-fleet-console-spec.md:323-337`, and the fleetd ↔
OmniRoute boundary at §4.8 in the same file, between its §4.7 and §5
(sibling-branch headings verified at lines 261 and 289 via `git show`). No `ATTACH DATABASE`, no cross-file view, no read path into a
database that holds credentials-adjacent tables: the reader calls endpoints
and reads files, and if a source is unreachable its columns render `unknown`
rather than the reader reaching around the API. Caching the full joined row
would create a second ledger with its own retention, backup, and staleness
semantics for data whose sources already version and trim themselves — the
console spec's retention table keeps `run_usage` on a 30-day cache default
(sibling-branch §4.7, heading verified at line 261 via `git show`), and this spec inherits
that default for the one cached leg while asserting no retention policy at all
for legs it does not store. At-least-once read precedent for the file-based
legs is the repo's own position-based resume (`docs/plans/2026-09-28-restart-spec.md:76`).

## 7. Security / privacy

Attribution rests on OmniRoute's per-host access tokens (brief-sourced, §2.5):
one named, scoped, revocable token per fleet host, verified with `whoami`
(brief-sourced). The join keys cost and conversation reads off the token
identity the gateway already stamped, so no new identity machinery is trusted
and revocation stays one click on the OmniRoute side (inference on the
one-click mechanics; the token properties are brief-sourced). This spec mints
no credentials and stores none — consistent with the public-repo rule that
real values never enter tracked files (`AGENTS.md:16`).

The sensitive leg is Claude's OTEL export: anything in it that could carry
transcript text must never leave the host un-redacted. The gate is the repo's
own redaction home — the `Redactor` (`tools/autoos_redact.py:193`) and its
argv scrubber (`tools/autoos_redact.py:266`) — applied (inference on the exact
wiring, since the export itself is absent per §2.6) as a host-local pass
before any OTEL-derived value crosses into a joined row: token counts pass,
text does not. Worker records already pass through redaction at construction
(`tools/autoos-agent.py:3860`), and ask-back content keeps its question/answer
file discipline (`tools/autoos-ask.py:57`, writer at
`tools/autoos-ask.py:110`), so the repo-side legs arrive pre-scrubbed; the
OTEL leg is the one that must be fenced the same way before it is trusted.
Audit and conversation legs travel as links (§3), so their bodies never enter
the telemetry path at all. Operator headroom/admission state (at most 3 lanes
+ 3 readers per orchestrator,
`.agents/skills/unattended-orchestration/SKILL.md:105`) is out of scope for
telemetry reads — resource metrics come from `watchdog-report`, not from
orchestrator internals.

## 8. Open questions

Format note: the `Q: FLEETSPEC |` shape below is the console spec's §12 format
(sibling-branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md:802-843`): `Q: FLEETSPEC |
<topic> | <question> | options: … | default: … | blocks: … | reversible: … |`,
with an `answer: (x)` suffix appended once decided. All questions below are
open (no `answer:`), all `(inference)` except where marked brief-sourced.

- `Q: FLEETSPEC | otel-scope | Does Claude's OTEL token-usage export exist on every fleet host, what is its event shape, and does any field carry transcript text | options: (a) cumulative counters only, no text, joinable by run id (b) counters plus text-bearing fields, host-local redaction first (c) no usable export, fall back to transcript token math | default: (a) because the brief describes it as a cumulative export, but the checkout has no trace of it (§2.6) | blocks: §2.6 leg implementation, §7 redaction wiring | reversible: yes (legs are additive) |` (inference; the absence is checkout-verified, §2.6.)
- `Q: FLEETSPEC | join-place | Where does the §3 read-through join execute | options: (a) console backend at render time (b) a small standalone telemetry reader the console calls (c) fleetd | default: (a) because the console already owns the priced leg's cache (sibling-branch run_usage, §6) and (c) contradicts fleetd's own scoping (sibling-branch L2-general/fleetd-spec, docs/plans/2026-09-28-fleetd-eventbus-spec.md:23-51) | blocks: §6 reader implementation, §9 lane 1 | reversible: yes (the joined-row shape does not move) |` (inference.)
- `Q: FLEETSPEC | link-shape | What does a conversation_link / audit_trail_link resolve to for the console | options: (a) opaque id plus a viewer URL on the existing management surface (b) id only, console builds the viewer (c) bounded excerpt inline | default: (a) because (c) re-copies bodies this spec refuses to store (§5, §7) | blocks: §3 joined-row contract | reversible: yes |` (inference.)
- `Q: FLEETSPEC | token-rotation | One access token per fleet host (brief-sourced, §2.5): who rotates them and on what cadence | options: (a) operator-rotated on a schedule (b) rotated on revocation events only (c) short-lived with auto-renewal | default: (b) because revocation "for free" is the brief's stated property and scheduled rotation is unasked-for machinery | blocks: §7 attribution wording only | reversible: yes |` (brief-sourced tokens; options inference.)
- `Q: FLEETSPEC | unknown-budget | Non-gateway and pre-OTEL runs render cost/tokens "unknown" (§3): is a partially-unknown joined row acceptable for P1, or does the node-agent transcript token math need to fill it first | options: (a) acceptable, unknown renders as unknown (b) fill unpriced token counts from transcript math before P1 | default: (a) because an honest unknown beats an estimated number in a cost column | blocks: §3 NULL-column policy, console rendering | reversible: yes |` (inference.)

## 9. Closing table — lanes + estimated Claude tokens

Budget mode (per brief: writers/first-reviewers are free models, Claude only
reviews/finals) — every row's Claude estimate stays small (under 1M weighted
tokens each), same shape as both sibling specs' closing tables (sibling-branch
`L2-general/fleetnode`,
`docs/plans/2026-09-28-fleet-node-spec.md:448-460`; sibling-branch
`L2-general/fleetd-spec`,
`docs/plans/2026-09-28-fleetd-eventbus-spec.md:366-380`). No lane below looks
larger than that; none is flagged (inference: sizing judgement, not
measurement).

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| telemetry-join-reader | §3 read-through join: run-id → cost (§4 template) + conversation + audit + combo-health + worker-record legs, joined-row contract, console read path; acceptance is one run rendering every leg from live sources | ~250k | 2–3 days | §8 join-place answer; upstream cost endpoint reachable (Q-011 filed, §4) |
| telemetry-otel-leg | §2.6 leg: confirm the cumulative export exists, stamp its join key, host-local redaction gate per §7, NULL-column behavior when absent; nothing ships un-redacted | ~200k | 2–3 days | §8 otel-scope answer; telemetry-join-reader (row contract to extend) |
| telemetry-resource-leg | §2.8 leg: pull fleet-node `watchdog-report` per run, map CPU/RSS/host-RAM into the joined row, degrade to unknown when the host has no fleet-node | ~120k | 1–2 days | telemetry-join-reader; fleet-node report live on at least one host (sibling branch `L2-general/fleetnode`) |
