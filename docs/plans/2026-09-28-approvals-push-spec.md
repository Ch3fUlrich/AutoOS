# Approvals + push spec: from risk class to a human gate

Status: SPEC ONLY — no implementation. One new file per
`L2-general/approvals-push-spec`; nothing else touched.

## 0. Provenance and citation discipline

Every claim about this checkout ends in a `path:line` citation that resolves
in this branch. Anything not verifiable in the checkout is marked
`(inference)`. Two cited files live outside the git checkout but were readable
for this run and are cited as `file:line` per the brief:

- `/home/s/code/AutoOS/logs/handoff-sessions/20260925/briefs/common.md`
  (the operator decision log; `common.md:<line>` below means that file).
- This brief's own branch has no HOOKS spec, no fleet-console spec, and no
  `OPERATOR-TASKS.md` — verified by glob (`docs/plans/*.md` holds no
  `2026-09-28-agent-hooks-spec.md` and no `2026-09-28-fleet-console-spec.md`;
  `**/OPERATOR*` matches nothing). §6 and §9 say exactly what they fall back
  to instead of inventing those files. `(inference)` marks every step taken
  from the brief's scrubbed description rather than from a checkout file.

Placeholder rule (hard): `<push service>`, `<management host>`, and
`example.internal` below are placeholders, never product names or addresses.

## 1. Summary

The gap in one paragraph: this repo already classifies the risk of a change
from its diff — `tools/autoos_risk.py` applies `policy.risk_rules` to the diff
at `merge-base(base, sha)` (`tools/autoos_risk.py:1-11`), exposed as
`python3 tools/autoos-agent.py risk --sha <sha>` (`tools/autoos-agent.py:92`,
`tools/autoos-agent.py:1387-1426`, flags `--sha`/`--base`/`--repo`/`--registry`/`--json`
at `tools/autoos-agent.py:4818-4831`) — and already prices each class in review
eyes (`policy.risk_audit_percent` at `catalog/ai-registry.json:1525-1532`;
`policy.review_counts` at `catalog/ai-registry.json:1775-1790`, whose `$comment`
at `catalog/ai-registry.json:1778` records the D-060/Q-013 rule: "two diverse
cheap cross-family reviews, and for high the same two plus the Sonnet final").
What it does NOT have is a spec for what happens when a risk class requires a
**human**, not just more review: certain categories (secrets, sudo/root,
firewall/auth, deploys, public pushes and PRs, data deletion, migrations) need
**operator approval**, and today that approval is an ad-hoc inbox line plus the
generic ask-back block (`tools/autoos-ask.py`'s `question.json`/`answer.json`
mechanism — `tools/autoos-ask.py:12-24` — has no separate "approval" type; every
block is a generic question that leaves the run in `input_required` until an
answer lands). This spec designs the bridge: risk class → a gated approval
step, reusing the classifier, the ask-back primitive, and the inbox-line
practice already in production in this run, not a new subsystem.

## 2. Risk class → approval mapping

Each private-plan category against the REAL `policy.risk_rules` entries
(`catalog/ai-registry.json:1533-1774`). "Covered" means a high-risk diff in
that category already classifies `high` today; "gap" means it does not and
`policy.risk_rules` should grow (one closing-table lane, §10).

| # | Approval category | Checkout rule(s) | Covered or gap |
|---|---|---|---|
| 1 | secrets (paths) | `configuration/api-keys.yml` (`catalog/ai-registry.json:1534-1539`), `configuration/**/*.env` (`catalog/ai-registry.json:1540-1545`), `**/*.vault.yml` (`catalog/ai-registry.json:1546-1551`), `tools/autoos_redact.py` (`catalog/ai-registry.json:1642-1647`), `**/*.key` (`catalog/ai-registry.json:1648-1653`), `**/*secret*` (`catalog/ai-registry.json:1654-1659`), `**/*.pem` (`catalog/ai-registry.json:1660-1665`), `**/.env` (`catalog/ai-registry.json:1666-1671`), `**/.env.*` with `exclude: **/*.example` (`catalog/ai-registry.json:1672-1678`) | Covered (path side) |
| 2 | secrets (content) | Five `added_regex` rules over ADDED lines: `-----BEGIN [A-Z ]*PRIVATE KEY-----` (`catalog/ai-registry.json:1739-1744`), `AKIA[0-9A-Z]{16}` (`catalog/ai-registry.json:1745-1750`), `ghp_[A-Za-z0-9]{36}` (`catalog/ai-registry.json:1751-1756`), `sk-[A-Za-z0-9_-]{20,}` (`catalog/ai-registry.json:1757-1762`), `xox[baprs]-` (`catalog/ai-registry.json:1763-1768`) | Covered (content side; added because path-only rules let a key in `notes.txt` read `normal` — `CHANGELOG.md:248-257`) |
| 3 | sudo/root | `added_regex` `\bsudo\b` (`catalog/ai-registry.json:1733-1738`), reason `root/sudo (R-orch-10)`; deliberately textual and case-sensitive — a test that merely mentions sudo raises the class and the reviews decide (`CHANGELOG.md:293-304`, `tools/autoos_risk.py:26-34`) | Covered |
| 4 | firewall/auth | `**/*settings*.json`, reason `security settings` (`catalog/ai-registry.json:1588-1593`); `.github/workflows/**` (`catalog/ai-registry.json:1582-1587`) catches workflow-mediated edge changes only | Partial gap: no firewall/auth-specific rule (no rule names firewall, auth, or edge config). The run's live practice is stricter than the classifier — "Firewall/edge changes only via prox or the caddy-edge-deploy Semaphore template" (`common.md:72-74`) — but a diff touching firewall config classifies on generic settings globs or not at all |
| 5 | deploys | `Windows/ansible/**` reason `installer` (`catalog/ai-registry.json:1697-1702`); installer globs `**/install*.sh` (`catalog/ai-registry.json:1703-1708`), `**/install*.ps1` (`catalog/ai-registry.json:1709-1714`), `**/setup*` (`catalog/ai-registry.json:1715-1720`), `**/bootstrap*` (`catalog/ai-registry.json:1721-1726`), `**/Install*` (`catalog/ai-registry.json:1727-1732`); `setup.sh`/`setup.ps1` as PATH/profile writers (`catalog/ai-registry.json:1552-1563`) | Partial gap: installer paths are covered, but a deploy *action* (a workflow or script change that ships to production, e.g. the refused `[Production Deploy]` gateway apply in `common.md:67-69`) has no deploy-specific rule unless its path happens to match |
| 6 | public pushes and PRs | None — no `policy.risk_rules` entry names remotes, forks, upstreams, PRs, or `gh` | Gap: `policy.risk_rules` cannot see push destination at all (it reads the diff, and destination is not in the diff — `tools/autoos_risk.py:535-561`). This category is enforced at the hook/lane layer instead (§6), and the classifier side needs a rule that fires on the closest visible proxy (e.g. workflow or config files that declare a public remote) or stays explicitly hook-owned — an open question (`Q: APPROVALS | public-push proxy`, §9) |
| 7 | data deletion | `diff_deletion` with `min_deleted_lines: 200` (`catalog/ai-registry.json:1576-1581`): any deleted file, or >200 deleted lines in surviving files (corpus: `f5744611` flips `normal` → `high`; `CHANGELOG.md:279-286`). Deletion counts come from `deleted_lines()` over `--numstat -z` (`tools/autoos_risk.py:250-273`) | Covered |
| 8 | migrations | `**/migrations/**` (`catalog/ai-registry.json:1679-1684`), `**/*.pg` (`catalog/ai-registry.json:1685-1690`), `**/*.sql` (`catalog/ai-registry.json:1691-1696`) | Covered |
| 9 | policy/skill/contract (adjacent, already approval-adjacent) | `.agents/skills/**` (`catalog/ai-registry.json:1594-1599`), `AGENTS.md` (`catalog/ai-registry.json:1600-1605`), `CLAUDE.md` (`catalog/ai-registry.json:1606-1611`), `docs/plans/**` (`catalog/ai-registry.json:1612-1617`), `docs/**/*spec*.md` (`catalog/ai-registry.json:1618-1623`), `docs/agent-protocol.md`, `tools/autoos_agent_mcp.py`, `catalog/*.schema.json` (`catalog/ai-registry.json:1624-1641`), `registry_policy` type (`catalog/ai-registry.json:1769-1773`, implemented at `tools/autoos_risk.py:370-394`) | Covered; listed because a policy edit is the one change class that can weaken the gate itself, so it must always route to approval, never auto-merge (§3) |

Net: categories 1–3, 7, 8 already classify `high` through real rules;
categories 4–6 need new `policy.risk_rules` entries (or an explicit
hook-owned decision for 6) — §10 lane 1.

## 3. Auto-merge path

Cite, don't redesign. The review-count policy already exists and this spec
does not change it:

- Normal: two cheap cross-family reviews; a 20% deterministic sample
  (`int(sha[:12], 16) % 100 < percent` — `tools/autoos_risk.py:494-514`,
  percent from `policy.risk_audit_percent`, default 20 —
  `tools/autoos_risk.py:517-532`, value 20 at
  `catalog/ai-registry.json:1525-1532`) additionally gets the final check.
- High: the SAME two cross-family reviews plus the Sonnet final on top
  (`catalog/ai-registry.json:1780-1784`; the RISKTIER-a2 fix for high being
  cheaper to review than normal — `CHANGELOG.md:258-263`).
- The class is read off the diff, never volunteered by the writer
  (`tools/autoos_risk.py:420-491` raises on an unknown rule type so a rule
  that silently does nothing is impossible; `tools/autoos-agent.py:1401-1404`:
  an unclassified diff exits 2, never `normal`).
- D-115 (every change, plan and spec gets a different-family review; Sonnet
  final for D-060 high-risk, every plan/spec, every NOT-READY override, +
  20% audit — `common.md:137-139`) is the standing review rule this path
  already runs under; the auto-merge gate below assumes it, not replaces it.

Auto-merge rule (this spec's only new sentence about merging): a lane whose
diff classifies `normal` (or `high` with its final in hand), whose two
cross-family reviews and required final are recorded, whose deterministic
gates (tests, lint, drift, CI — the Q-013 list at `common.md:78-82`) pass, and
whose risk reasons touch NONE of the §2 approval categories, merges without a
human. Every other shape — any §2 reason present, any gate unproven rather
than passed, any policy-file reason (§2 row 9) — takes the §4 path. "Unproven"
includes exit-2 classification: a diff nobody could read is approval-path, not
auto-merge (`tools/autoos-agent.py:1412-1416`).

## 4. Operator-approval path

### 4.1 The decision record (channel-independent)

Whichever channel carries the decision, exactly one record format is written,
so migrating from inbox-line to push service is additive downstream (no
consumer rewrites). The record is one JSON object, written once, never edited:

```json
{"type": "approval", "sha": "<resolved 40-hex commit>",
 "risk_reasons": ["<reason>: <path or detail>", "..."],
 "decision": "approve|deny", "by": "<operator id>",
 "at": "<UTC ISO-8601>", "channel": "inbox|push", "nonce": "<uuid>"}
```

- `sha` is the `resolve_sha` hex (`tools/autoos_risk.py:160-184`), the same
  hex `risk --sha` prints as `commit:` (`tools/autoos-agent.py:1420-1425`) —
  so the approval names the exact commit the classifier graded, not a branch
  that may have moved since.
- `risk_reasons` are the `classify()` reason lines verbatim
  (`tools/autoos_risk.py:434-443`, one per matching path per rule).
- `type: approval` is new and mandatory: today's ask-back has no approval
  type — every block is a generic question (`tools/autoos-ask.py:12-24`) —
  and the type tag is what lets consumers distinguish an approval from a
  question without parsing prose.
- Exactly-once: the writer creates the record with exclusive-create semantics
  (the `_create_json_exclusive` pattern — `tools/autoos-ask.py:74-89`); a
  duplicate decision for the same `(sha, nonce)` is rejected, never merged.
  History is append-only (`qa-<n>.json` precedent —
  `tools/autoos-ask.py:110-144`); a stale/orphan answer is archived with a
  marker, never silently deleted (`tools/autoos-ask.py:146-156`).

### 4.2 Today's stand-in: inbox-line + ask-back (already in production)

Operator approvals already flow as inbox lines in this run: `common.md:62`
(`EXECUTION (operator-confirmed 11:3xZ): routing-00 routes/decides/asks/verifies
and executes nothing…`) and `common.md:67` (`GATEWAY STEPS (operator-confirmed
to L1-main 11:5xZ): … If refused, it reports verbatim to routing-00 and the
change stays off`). Until `<push service>` exists, the approval path runs on
that exact pattern composed with the ask-back primitive:

1. The worker classifies (`risk --sha`, `tools/autoos-agent.py:1387-1426`); on
   any §2 reason it writes `question.json` (`{"text", "asked"}` —
   `tools/autoos-ask.py:19-21`, exclusively — `tools/autoos-ask.py:192-199`)
   whose text embeds the machine-readable core `approval? sha=<hex>
   reasons=<n>`, and blocks in `input_required` (`tools/autoos-ask.py:19-24`).
2. The orchestrator surfaces the pending question to the operator (today: the
   inbox line; tomorrow: §4.3) and records the operator's words.
3. The answer lands via `answer.json` (`{"text", "answered"}` —
   `tools/autoos-ask.py:22-24`); the helper prints it, archives the pair to
   `qa-<n>.json`, and removes both files (`tools/autoos-ask.py:205-217`).
4. The orchestrator translates the answered pair into the §4.1 record
   (`channel: inbox`) and appends it where downstream consumers read it.
5. Timeout/withdrawal follows the existing rule: question withdrawn, run back
   to working, late answers archived stale (`tools/autoos-ask.py:222-232`,
   exit codes `tools/autoos-ask.py:39-44`). A timed-out approval is a DENY
   by default (fail-closed; §5) — the lane does not proceed on silence.

### 4.3 Target: `<push service>` with Approve/Deny action buttons

Requirement, not vendor: a self-hosted push service (`<push service>`,
placeholder — no product name is specified anywhere in this checkout) that
delivers to the operator's phone a notification carrying the approval core
(`sha`, risk reasons, lane) with **Approve/Deny action buttons**, whose
callback writes the §4.1 record (`channel: push`) via an approval endpoint.
The endpoint enforces §5 (operator credential only) and exactly-once
(§4.1). Downstream consumers read the §4.1 record identically for
`channel: inbox` and `channel: push` — the migration changes only the
producer, never the consumer. Whether the endpoint lives in the existing
`autoos-agent` MCP/CLI surface or a new one is `Q: APPROVALS | endpoint home`
(§9); the record format is fixed either way `(inference)`.

### 4.4 What the operator sees (both channels)

The human-readable surface carries, at minimum: the lane/branch, the resolved
sha (short), the risk reasons verbatim, what is being approved (merge? push?
deploy? — one verb), and what happens on deny (the change stays off, cf. the
gateway refusal precedent — `common.md:67-69`). The phone notification is the
same content compressed to the action-button callback's payload plus a short
label `(inference)`.

## 5. Never-a-bypass-key invariant

Stated plainly: **agents never hold the credential that would let them skip a
human approval. An approval gate an agent can route around by minting its own
key is not a gate.** Hard invariant, not a nice-to-have:

- The approval endpoint (§4.3) accepts exactly one credential: the operator's.
  There is no agent-scoped token, no "approve own work" role, no break-glass
  flag readable by a worker. Any lane proposing such a credential fails review
  unconditionally.
- Timeout is deny (§4.2 step 5). A worker that cannot reach the operator waits
  or stops; it never self-approves, and "the operator is slow" is never a
  reason recorded for proceeding.
- Adjacent pieces this checkout already enforces: the sudo/root category is
  already approval-routed at the classifier (`catalog/ai-registry.json:1733-1738`,
  reason `root/sudo (R-orch-10)`); operator-only steps already exist as a
  first-class blocked state — the restart spec's lane table carries an
  `operator` row for "operator-only steps, verbatim"
  (`docs/plans/2026-09-28-restart-spec.md:147`), spawned briefs terminate in
  "blocked on operator-only input" (`.claude/handoff.config.json:58`,
  `.claude/handoff.phase2.json:61`), `probe-rtk.py` refuses to mint keys
  itself (`tools/probe-rtk.py:694`, asserted at
  `tests/test_probe_rtk.py:426,453`), and the decision log tracks
  "Operator-only steps pending: see `OPERATOR-TASKS.md`" (`common.md:61` —
  that file is not in this checkout, so the content of the operator-only list
  itself is `(inference)`; its existence as a category is attested).
- Auditability: every approval record names `by` and `at` (§4.1); a decision
  without an operator identity is malformed and closes nothing.

## 6. Push guard composition

The HOOKS spec this section composes with
(`docs/plans/2026-09-28-agent-hooks-spec.md` — PreToolUse push guard,
leaf/all split via `AUTOOS_AGENT_ROLE`, §5/§6 of that spec) **is not in this
checkout** (verified by glob; §0). This section therefore cites the brief's
scrubbed description of it rather than the spec itself, and does NOT re-derive
the guard. The closest in-checkout hook material is unrelated: decision 0003
refuses Claude Code *lifecycle* hooks (`SessionStart`/`SessionEnd` —
`docs/decisions/0003-no-claude-code-lifecycle-hooks.md:1-30`), a different
hook layer from a push guard; it is cited here only to prevent confusion.

Composition (from the brief's description; the guard side is `(inference)`
until the HOOKS spec lands in this branch):

- The push guard stops an agent from pushing to `main`/`master` at the hook
  layer. This spec's public-push/PR category (§2 row 6) is what happens for
  the pushes and PRs that are technically allowed but still need a human:
  pushing a lane branch, or opening a PR, is not the guarded action — the
  *destination's publicness* is what routes to approval.
- The established pattern for external destinations in this run: pushes to an
  external fork go via routing-00, not directly — OmniRoute PR #14992 was
  "published by routing-00 on the operator's instruction" (`common.md:123`).
  This spec generalises that precedent: a PR to a public upstream fork, or a
  push anywhere outside the fleet's own remotes, requires a §4 approval
  naming the destination; the approval record's `risk_reasons` carry the
  public-push reason once §2 row 6 has a rule (or the hook-owned decision,
  per `Q: APPROVALS | public-push proxy`).
- Layering: hook guard (mechanism: block the disallowed push) → approval gate
  (mechanism: allow only with a recorded human decision) → auto-merge
  (mechanism: merge without a human when §3 holds). A push that the hook
  layer blocks never reaches the approval gate; an approval never overrides a
  hook block — it only permits what the hooks already permit `(inference)`.

## 7. Quality gate

Anchor in the decision log: "Track revert rate + red CI on main per lane;
rise -> routing-00" (`common.md:81-82`, tail of the Q-013/D-060 entry at
`common.md:78-82`). This checkout measures neither today — no code here
computes a revert rate or a per-lane red-CI signal (verified by grep for
`revert` outside CHANGELOG prose; the metric definitions below are
`(inference)` and the checkoutal claim is only that they are absent).

- **CI-red pause:** if CI on `main` goes red, auto-merge (§3) pauses for that
  project: lanes keep classifying and reviewing, but no merge lands until a
  human clears it. The pause is per-project (a red Windows twin does not stop
  Linux lanes) `(inference)`.
- **Revert-rate pause:** proposed definition `(inference)` — reverts merged to
  `main` in the trailing 7 days divided by merges to `main` in the same
  window, per project; pause when it exceeds 10% or 2 reverts, whichever
  comes first. The window, threshold, and per-project-vs-per-lane scoping are
  `Q: APPROVALS | revert-rate definition` (§9).
- **What "clears it" means:** a human (the operator, or routing-00 on the
  operator's instruction — cf. `common.md:62-66`) records an explicit clear
  decision naming the evidence: the red run id and its fix commit, or the
  revert-rate numbers recomputed after the window moved on. The clear is
  itself an approval-path action — a §4.1 record with `decision: approve`
  and the evidence in place of `risk_reasons` — not a separate unlock
  mechanism, so there is exactly one place to audit who let merging resume
  `(inference)`. A clear never back-dates: lanes merged during the pause stay
  unmerged until re-approved.

## 8. Migration

Rollout without the two failure modes: a risk-flagged change silently
auto-merging (unsafe) or an approval pending with nobody told (stuck).
Safe-to-run-twice per AGENTS.md §4 (idempotency): every step below is a no-op
when re-run — records are exclusive-create (§4.1), questions refuse a second
pending ask (`tools/autoos-ask.py:186-197`, exit 2 on "already pending"), and
a resolved-then-re-run classifier returns the same class for the same sha
(deterministic audit — `tools/autoos_risk.py:494-514`).

1. **Classifier first (already landed).** `risk --sha` and `policy.risk_rules`
   exist (`tools/autoos-agent.py:1387-1426`,
   `catalog/ai-registry.json:1533-1774`); lanes can classify today with no
   behavior change.
2. **Record format + inbox stand-in.** Adopt the §4.1 record and run approvals
   through the §4.2 inbox-line + ask-back path. No new service, no new
   credential; the only new artifact is the `type: approval` tag
   distinguishing approvals from generic questions.
3. **Default-deny wiring.** Flip the merge gate: any §2 reason without a
   matching §4.1 approval record blocks merge. Order matters — wire the block
   before announcing the gate, so there is no window where a flagged change
   auto-merges because the gate "isn't on yet". Re-running the wiring is a
   no-op (the block is a check, not a mutation).
4. **Stuck-approval watch.** Every pending approval has a visible owner and an
   age: surface open approvals where open questions are already surfaced (the
   card `threads` Q-lines — `docs/plans/2026-09-28-restart-spec.md:194-200`).
   An approval pending past its timeout is deny (§4.2 step 5), recorded, not
   silent.
5. **Push-service cutover.** Stand up `<push service>` (§4.3); dual-write
   inbox + push during the trial; retire the inbox producer when the push
   callback has written N consecutive matching records (N itself a
   `Q: APPROVALS | cutover N` decision). Consumers never change — they read
   §4.1 either way.
6. **Quality gate last.** CI-red / revert-rate pause (§7) wires up only after
   steps 1–5 are live, because a pause nobody can clear (no approval path
   yet) is a fleet stop.

## 9. Open questions

Format note: the brief points at `docs/plans/2026-09-28-fleet-console-spec.md`
§12 (or a fleetd-eventbus spec) for the `Q: APPROVALS |` format — neither file
is in this checkout (verified by glob; §0). The closest attested question
format here is the card `threads` Q-line (`Q-008 | routing-00 | asked 22:33Z |
default a` — `docs/plans/2026-09-28-restart-spec.md:197-198`) and the inbox
`question: … | options: …` convention (unattended-orchestration R-router-01).
The questions below use the brief's `Q: APPROVALS |` prefix with the Q-line's
pipe fields, so they drop into either convention unchanged.

- `Q: APPROVALS | public-push proxy | asked <date> | default hook-owned` — §2
  row 6: the classifier reads the diff, and push destination is not in the
  diff. Options: (a) leave public-push/PR enforcement entirely to the hook
  layer + routing-00 mediation (`common.md:123` precedent), no classifier
  rule; (b) add a proxy rule (e.g. workflows/configs declaring a public
  remote) so the reason line exists for the approval record. (a) is smaller;
  (b) gives the §4.1 record a reason to carry.
- `Q: APPROVALS | endpoint home | asked <date> | default autoos-agent MCP` —
  §4.3: does the Approve/Deny callback land in the existing `autoos-agent`
  MCP/CLI surface (`tools/autoos-agent.py`, whose `respond`-side tool today
  is `respond(run_id, text)` per `tools/autoos-ask.py:22-24`) or a new
  service? Either way the §4.1 record format is fixed.
- `Q: APPROVALS | revert-rate definition | asked <date> | default §7 proposal`
  — §7: window, threshold, and per-project vs per-lane scoping for the
  revert-rate pause; plus who computes it (a lane? a cron? routing-00?) since
  nothing in this checkout computes it today.
- `Q: APPROVALS | firewall/auth + deploy rules | asked <date> | default add
  both` — §2 rows 4–5: exact glob/regex list for the two partial-gap
  categories (firewall config paths? deploy workflow paths? an
  `added_regex` for deploy commands?), or accept the partial coverage as
  designed and rely on the hook/operator layer.
- `Q: APPROVALS | cutover N | asked <date> | default 10` — §8 step 5: how many
  consecutive dual-written push records before the inbox producer retires.

## 10. Closing table

Budget mode (D-102: "Claude only orchestrates + gives finals. Writers,
researchers, first reviewers = free/cheap legs" — `common.md:124-129`):
writers/first-reviewers are free models, Claude only reviews/finals — every
row's Claude estimate is kept small; any row over ~1M weighted tokens would be
flagged, and none below is.

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| risk-rules-gap | Grow `policy.risk_rules` for the §2 gaps: firewall/auth + deploy rules (rows 4–5) and the row-6 decision (proxy rule or explicit hook-owned, per `Q: APPROVALS | public-push proxy`); `tools/registry.py` rule-12 validation for any new field; failing-first tests in `tests/test_autoos_risk.py` (red→green per the RISKTIER-a2 precedent — `CHANGELOG.md:239-241`) | ~60k (final only; writer free) | 2–4 h | `Q: APPROVALS | public-push proxy`, `Q: APPROVALS | firewall/auth + deploy rules` |
| approval-record | Implement the §4.1 record + `type: approval` handling on the §4.2 inbox/ask-back path (exclusive-create, exactly-once, stale-archive per `tools/autoos-ask.py:74-89,146-156` semantics); default-deny merge gate (§8 step 3); stuck-approval surfacing in card Q-lines (`docs/plans/2026-09-28-restart-spec.md:194-200`) | ~80k (final only; writer free) | 3–5 h | §2 mapping agreed; risk-rules-gap (for reason coverage) |
| push-service | Stand up `<push service>` (§4.3: self-hosted, action-button callbacks to the approval endpoint, operator-only credential per §5); per `Q: APPROVALS | endpoint home`; dual-write trial + cutover (§8 step 5) | ~120k (final only; writer free) | 1–2 days | approval-record live; `Q: APPROVALS | endpoint home`, `Q: APPROVALS | cutover N` |
| quality-gate | Implement §7: CI-red pause + revert-rate computation (per `Q: APPROVALS | revert-rate definition`), clear-as-approval-action; per-project scoping | ~60k (final only; writer free) | 2–4 h | `Q: APPROVALS | revert-rate definition`; approval-record live (clear reuses it) |
