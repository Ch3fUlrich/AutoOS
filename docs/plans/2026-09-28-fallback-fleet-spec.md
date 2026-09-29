# Fallback fleet — keeping the run going when the Claude limit is hit (spec)

- Date: 2026-09-28.
- Path: `docs/plans/2026-09-28-fallback-fleet-spec.md`.
- Task: L2-general, routing-00 D-143 (fallback fleet).
- Status: **spec, not implemented.** This is a design document. Every mechanism it
  relies on is either already live in this checkout (tracked files only; cited below with a verified
  `path:line`; git-ignored runtime files are named as such) or explicitly marked as a dependency that is spec'd-not-built or
  `(inference)`. No code is changed by this file.
- Owner: `autoos-L2-general`.
- Related specs: the HOOKS spec (`docs/plans/2026-09-28-agent-hooks-spec.md`),
  ORCH-A1 role launch profiles (`docs/plans/2026-09-28-orch-a1-role-launch-profiles-spec.md`),
  the restart spec's relaunch line (`docs/plans/2026-09-28-restart-spec.md`), and the
  fleetd event-bus spec (`docs/plans/2026-09-28-fleetd-eventbus-spec.md`). This design
  extends the private plan's router-fallback decision
  (private plan D-073, router-fallback — not in this checkout; never quoted here).

Provenance note: some facts below come from a **router research note** that is private
and not in this checkout. It is cited only as "router research note" — never by path,
and none of its URLs or hostnames appear here. Where a claim is not verifiable in this
checkout it is marked `(inference)`; where this checkout resolves something the research
note flagged as uncertain, the real citation wins and the note's uncertainty is corrected.

## 1. Summary

The Claude usage limit is expected to be hit soon. This spec designs the fallback path
so the orchestration fleet keeps running on non-Anthropic-billed models when that
happens, and how it switches back after the reset. The design reuses substrate that is
already deployed, not new infrastructure:

- **Fallback L0** is an `opencode serve` web session — already live and already behind
  the two-layer auth gate (`configuration/README.md:40-59`, `docs/web-services.md:24`).
- **Fallback L1/L2** points the Claude Code harness at the local OmniRoute gateway via
  the already-shipped, opt-in routing (`docs/api-keys.md:195-205`; live code
  `lib/windows/AutoOS.Install.psm1:3362`, `lib/linux/install.sh:2641`), or runs opencode
  sessions instead. Which of the two is chosen is decided by a **measured comparison**,
  defined in §3, that has not yet been run (phase 1 of the rollout).
- **L3 workers are unchanged** — they already run non-Claude models
  (`docs/plans/2026-09-25-routing-v2-plan.md:5-6,15-16`), so the trigger has no effect at L3.
- The **switch trigger** composes with the ORCH-B4 budget governor
  (`docs/tasks.md:53`, queued/not-yet-built) and the already-live CLAUDEBUDGET gate
  (`tools/autoos_resolver.py:493,638-674`).
- What is **lost** in fallback (Remote Control, cross-host messaging, some providers'
  web search/artifacts) and its substitutes are tabled in §6.
- There is **no built-in web UI** for the harness+gateway combination, which is why the
  opencode web session in §2 matters for every operator, not just phone/L0 access
  (router research note; corroborated by `configuration/README.md:40-59`).

## 2. Fallback L0 — the opencode web session (already deployed)

`opencode serve` runs the browser UI and the JSON API on port 4096 and guards `/api/*`
with its own HTTP Basic auth (`docs/web-services.md:24`), with the password pinned in
`configuration/api-keys.yml` (git-ignored; template `configuration/api-keys.example.yml`) via the documented `opencode_password` single source
(`configuration/README.md:47-55`). On the server profile this service reaches the phone
exactly as `opencode.<domain>` already does: Authelia's admins-group + 2FA gate sits at
the reverse proxy (`docs/web-services.md:43`) and, before it, Caddy **strips** the
client's `Authorization` header and **re-applies** opencode's Basic credentials after the
forward-auth check (`docs/web-services.md:238`). That strip-then-reapply is the reason two
auth layers can both be satisfied on one request; it is a running mechanism, not a proposal.

State plainly: this is an **already-deployed substrate the fallback design reuses**, not
new infrastructure. In fallback mode the operator's L0 becomes a session started through
that same URL, against gateway/free-model legs rather than a Claude subscription.

**Dependency (correction 1).** A Claude-Code-equivalent L0 requires the router's
`CLAUDE.md`/skills/guards to be rendered for opencode. That renderer is the HOOKS spec's
**H3** lane (`docs/plans/2026-09-28-agent-hooks-spec.md:255`, "opencode + OpenHands
renderers … owns the README update for its new subcommands"), whose per-client hook model
is `docs/plans/2026-09-28-agent-hooks-spec.md:48-92` (opencode `tool.execute.before` /
`tool.execute.after` and the session-lifecycle events incl. `session.compacted`,
`docs/plans/2026-09-28-agent-hooks-spec.md:61-65`). **H3 is specced, not built** — only the
spec doc is present in this checkout. Until H3 lands, the opencode web L0 runs *without*
the rendered router `CLAUDE.md`/skills (inference), so it is a functional but
context-thinner orchestrator, not yet a full Claude-Code equivalent. This is recorded as a
"Waits for" dependency in §11.

## 3. Fallback L1/L2 — harness-on-gateway vs opencode sessions (the phase-1 measurement)

Both candidates for the L1/L2 role are real:

- **Claude Code harness pointed at OmniRoute.** The gateway routing is opt-in and already
  shipped. The `claude_gateway_routing` answer selects `gateway` or `login`
  (`docs/api-keys.md:195-205`); the Windows side `Set-AutoOSClaudeGateway`
  (`lib/windows/AutoOS.Install.psm1:3362`) and the Linux/macOS side
  `route_claude_to_gateway` (`lib/linux/install.sh:2641`) merge
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` into `~/.claude/settings.json`, and the
  `login` mode removes exactly those two keys again. The `docs/models-proposed.md:145`
  table row records this as "yes (`ANTHROPIC_BASE_URL/AUTH_TOKEN` merged live)", reaching
  "all combos"; `docs/models-proposed.md:203-207` records the `setup-claude` recipe
  (`--dry-run`-able) and the zero-write `omniroute run <target>` launcher. Reading the
  function bodies confirms the doc table: `gateway` is reversible, dry-run-safe, and
  **idempotent** — a rerun that would not change the file reports `SKIPPED` and writes
  nothing (`lib/linux/install.sh:2689-2693`; `lib/windows/AutoOS.Install.psm1:3409-3414`).
- **opencode sessions** instead, reached the same way §2 describes, on free/gateway legs.

What the harness *does* on a gateway model (router research note, private and not in this checkout — `(inference)` here): the
harness — tool use, subagents, hooks, background sessions, and same-machine
`SendMessage` — keeps working; what breaks is **Remote Control** and the cloud/Desktop
surface, not the local orchestration loop. There is no documented in-session model swap
(Claude Code's auth is fixed at process startup), so switching means relaunching the
process with the gateway env, not flipping a live session. (correction 2: gateway-model env
var details are not documented in the research note; headless operation is `claude -p` /
the Agent SDK with no built-in web UI; Remote Control fails with **any** gateway, incl.
LiteLLM/Bedrock/Vertex, not only OmniRoute.)

**Genuinely unverified — this is what phase 1 must measure (do not guess the answer):**

- (a) **tool-call fidelity** for the harness's real orchestration patterns (subagent spawn,
  `--bg`, hooks firing) when it runs against OmniRoute rather than the direct API. The
  `docs/models-proposed.md:145` "all combos" cell is a *routing* claim (the legs are
  reachable), not a verified *fidelity* claim.
- (b) **model aliasing** — which OmniRoute-served model stands in for the harness's default
  model and its haiku-class "small" model once Claude subscription models are unavailable
  (carried to §10 as open questions, per correction 2, alongside OmniRoute's expected Anthropic-format
  `/v1/messages` endpoint, which is unverified — see §10 `messages-endpoint`).

**The measured task (phase 1, concrete and cheap to run twice):** spawn one leaf worker via
the `autoos-agent` spawn path (`tools/autoos-agent.py:1549` and `:1762`, `claude_allowed("spawn", …)` — the
same gate every spawn routes through), have it edit one file and report back, then compare the
report's tool-call shape against a baseline run on a normal Claude-Code harness. **Metrics:**
does the task complete; tool-call / argument fidelity vs the baseline; wall time; do
`--bg`/hooks/skill-loading still fire. Compare the harness-on-gateway candidate and the opencode
candidate on that same task and pick the winner per layer.

State plainly: **running this comparison is phase 1 of the rollout**; this spec does not claim it
has already happened, and does not pre-decide which candidate wins.

## 4. L3 is unchanged

L3 leaf workers already run non-Claude models by default — the routing-v2 plan lists the live
worker routes as `t2-worker-clean` (native DeepSeek), `t2-worker-free-only` (GPT-OSS),
`t3-driver` (Mistral Code) and `t3-driver-free-only` (Qwen)
(`docs/plans/2026-09-25-routing-v2-plan.md:5-6`), with writer routing by bucket choosing those
free/non-Claude legs (`docs/plans/2026-09-25-routing-v2-plan.md:15-16`); so the fallback trigger
has no effect at L3, which is already off Claude.

## 5. Switch trigger — what this spec needs from ORCH-B4

The **CLAUDEBUDGET gate is live** in this checkout: the single Claude decision gate
`claude_allowed(...)` (`tools/autoos_resolver.py:638-674`), the orchestrator's final declaration
`CLAUDE_FINAL_ENV = "AUTOOS_CLAUDE_FINAL"` (`tools/autoos_resolver.py:493`) with
`final_declaration`/`critical_declaration` (`tools/autoos_resolver.py:612-635`), and the per-spawn
MCP `claude_reason` plumbing (`tools/autoos_agent_mcp.py:342-359`). It is merged to main (main
commit `ccf6f84`); note that `docs/tasks.md:31` still prints its status as "Queued (top priority)"
— that row is stale relative to the code that is actually present, and the code is the ground truth
here. This spec's trigger **composes with** that gate; it does not redesign it.

The **budget governor** that will actually flip normal→budget→emergency and relaunch
orchestrators on limit exhaustion is **ORCH-B4**, and it is **queued, not built**
(`docs/tasks.md:53`: "ORCH-B4 budget governor normal/budget/emergency + fallback router …
Queued after CLAUDEBUDGET — est. Claude ~30k"). This spec does not claim B4 relaunches anything
today. What **this spec needs from B4** (a different lane's job) is exactly two things:

- a **trigger condition**: "Claude usage exhausted" (and, for the reverse switch, "reset reached"),
  read from measured usage — B4's own DONE-criteria is a mode switch from measured usage
  (`docs/tasks.md:53`);
- a **relaunch-with-env action**: take an orchestrator's status card and relaunch it with the
  gateway env set (§3), then relaunch it back off the gateway env after the reset.

The **relaunch-from-status-card pattern** already has a documented home: the restart spec's
`relaunch-line <name>` builds a context pack from the run's state and prints one relaunch
command (`docs/plans/2026-09-28-restart-spec.md:343-357`), including the crash-recovery path that
reuses the same line. B4's automatic trigger is the same mechanism fired by usage rather than by a
human/L1-main. Whether orchestrators have already been relaunched from status cards *in this run*
is `(inference)` — the handoff inbox/status logs where that would be evidenced are not part of this
public checkout (correction 4), so no `path:line` is offered for the live precedent.

**Idempotency (AGENTS.md rule 3, applied to the switch):** the gateway env itself is
idempotent-safe by construction — `Set-AutoOSClaudeGateway`/`route_claude_to_gateway` already
report `SKIPPED` and write nothing on a second run with the same desired mode
(`lib/linux/install.sh:2689-2693`; `lib/windows/AutoOS.Install.psm1:3409-3414`). A second relaunch
with the same env should therefore be a no-op/skip, not a re-init: the trigger may fire
repeatedly (beats, retries) without double-applying, and B4 must not treat a repeat as a fresh
initialisation. (The relaunch *command* being safe to repeat is B4's obligation; the env-write
being safe to repeat is already shipped.)

## 6. What the fallback loses, and the substitute

| Lost on a gateway model (router research note) | Substitute | Status of the substitute |
|---|---|---|
| Remote Control (needs claude.ai subscription login; fails with any gateway — correction 2) | opencode web L0 (§2) | substrate **live** (`configuration/README.md:40-59`, `docs/web-services.md:24`); Claude-Code-equivalent **waits on H3** (`docs/plans/2026-09-28-agent-hooks-spec.md:255`) |
| Cross-host `SendMessage` / messaging | fleetd's event bus | **spec'd, not built** — `docs/plans/2026-09-28-fleetd-eventbus-spec.md:3` "SPEC v1, unreviewed"; it has no build row in `docs/tasks.md` (grep confirms none), so treat as not started |
| Cloud sessions / Desktop / Routines | (none yet) | degraded in fallback mode `(inference)` |
| artifacts / web-search on some providers | provider-dependent | verify per leg in phase 1 (§3) |

Same-machine limitation, named plainly: per the router research note `(inference)`, `SendMessage` works
**same-machine only**. Until fleetd ships, **cross-host coordination in fallback mode is
degraded, not equivalent** — an operator on a second host cannot be messaged into a gateway-model
session the way Remote Control allowed. That gap is fleetd's to close, and fleetd is a sibling spec,
not this one.

## 7. Web UI

The harness+gateway combination has **no built-in web UI** (router research note; correction 2 —
headless is `claude -p` / Agent SDK). So the UI in fallback is `opencode serve` (§2) or OpenHands —
OpenHands is the docker app on port 3000 with no own login, guarded by Authelia alone
(`docs/web-services.md:25,52`). This is the reason item (1)'s opencode web session matters **even for
L1/L2 operators**, not only for phone/L0 access: when the harness is on a gateway model, the web
UI is a separate already-running service, and `opencode.<domain>` / `openhands.<domain>` are how a
human sees and steers the fallback run.

## 8. Security

- Nothing here introduces a new secret surface. The gateway token travels in an environment
  variable and the merged settings file — both already mode-0600 and backup-on-change
  (`lib/linux/install.sh:2651-2655`). No token value is ever written to a tracked file or to
  the log (AGENTS.md rule 1).
- The opencode/OpenHands phone path keeps **both** auth layers: Authelia admins-group + 2FA at
  the proxy (`docs/web-services.md:43`) and the app's own Basic auth (`docs/web-services.md:24`),
  with the Caddy strip-then-reapply unchanged (`docs/web-services.md:238`). Fallback mode does not
  weaken this; it reuses it.
- Routing to the gateway is **opt-in and reversible** (`docs/api-keys.md:195-205`), default
  `login`; nothing silently flips a subscription machine onto a gateway.
- The budget gate stays in force through fallback: `claude_allowed` still refuses unsanctioned
  Claude spends (`tools/autoos_resolver.py:638-674`), so a "switch back to Claude" attempt
  before the reset still needs the orchestrator's declaration.
- No URL, hostname, IP or username from the private router research note is reproduced in this
  file; proxied names are `<domain>` placeholders only.

## 9. Migration / rollout

- **Phase 0 (now):** nothing to build in this checkout — the opencode substrate, the gateway
  routing, the budget gate and the L3 routes all already exist (§2, §3, §4, §5). The operator can
  flip `claude_gateway_routing=gateway` today and drive opencode web; the loss table in §6 is the
  honest description of that interim state.
- **Phase 1:** run the §3 measured comparison on the harness-on-gateway and opencode candidates;
  answer the two unverified items (a) fidelity and (b) aliasing; record the per-layer winner.
- **Phase 2:** build the H3 renderer so the opencode L0 is a real Claude-Code equivalent
  (`docs/plans/2026-09-28-agent-hooks-spec.md:255`) — this is the dependency §2 flags.
- **Phase 3:** land ORCH-B4 (`docs/tasks.md:53`) so the trigger and relaunch-with-env are automatic
  rather than manual, honouring the §5 idempotency obligation.
- **Phase 4:** fleetd bus (§6) to restore cross-host coordination. Rollback at any phase = answer
  `claude_gateway_routing=login`, which removes only the two gateway keys and leaves everything
  else (`docs/api-keys.md:200-201`; `lib/linux/install.sh:2699-2707`) — a second run is `SKIPPED`.

## 10. Open questions

Format follows the sibling specs' `Q:` cards (see `docs/plans/2026-09-28-fleetd-eventbus-spec.md:351-364`).
All questions below are open (no `answer:`); each is `(inference)` unless a citation is given.

- `Q: FALLBACK | harness-fidelity | Does the Claude Code harness keep tool-call/subagent/--bg/hooks fidelity on OmniRoute legs, or must L1/L2 fall back to opencode? | options: (a) harness-on-gateway wins (b) opencode sessions win (c) split by layer | default: (c) until §3 phase-1 measurement lands — the "all combos" cell (`docs/models-proposed.md:145`) is a routing claim, not a fidelity claim | blocks: §3 rollout choice | reversible: yes (either candidate can be re-run) |` (inference; open item (a) of §3.)
- `Q: FALLBACK | model-alias-default | Which OmniRoute-served model stands in for the harness's default model? | options: (a) gateway combo id passed directly (b) a pinned alias in the registry (c) decided in phase 1 | default: (c) — aliasing is unverified here | blocks: §3 measurement, any gateway launch profile | reversible: yes |` (inference; open item (b) of §3, per correction 2.)
- `Q: FALLBACK | model-alias-small | Which model stands in for the harness's haiku-class "small" model (subagent/reviewer legs)? | options: (a) a free spark/GPT-OSS-class leg (b) an explicit small-model combo (c) phase 1 | default: (c) | blocks: §3 measurement | reversible: yes |` (inference; open item (b) of §3.)
- `Q: FALLBACK | messages-endpoint | Does OmniRoute expose the Anthropic-format `/v1/messages` endpoint the harness expects, or only OpenAI-format? | options: (a) native Anthropic-format (b) translation layer sufficient (c) verify in phase 1 | default: (c) — the endpoint shape is not documented in the router research note (correction 2) | blocks: whether harness-on-gateway is even viable (§3) | reversible: n/a (a fact to discover) |` (inference; correction 2.)
- `Q: FALLBACK | harness-launch-permission | A fleet agent cannot launch another Claude Code harness session on a gateway without an operator-approved permission rule (the auto-mode classifier refused both a bypass-permissions run and a narrow `--allowedTools` run, 2026-09-29). Must ORCH-B4's relaunch-with-gateway-env be operator-sanctioned by a permission rule, or run by a non-agent systemd unit? | options: (a) permission rule for the launcher (b) non-agent systemd unit launches the relaunch (c) operator launches by hand | default: (b) | blocks: B4 relaunch, phase-1 measurement | reversible: yes |` (observed, phase 1.)
- `Q: FALLBACK | b4-trigger-source | Does B4 read "exhausted/reset" from the provider usage report or a wall-clock reset heuristic? | options: (a) measured usage (matches B4 DONE-criteria, `docs/tasks.md:53`) (b) fixed reset window (c) both | default: (a) because B4's own DONE-criteria is a mode switch from measured usage | blocks: §5 trigger condition | reversible: yes (configuration) |` (precedent cited in `docs/tasks.md:53`; the answer is B4's lane, not this spec's.)
- `Q: FALLBACK | cross-host-until-fleetd | Until fleetd ships, how do two hosts coordinate in fallback? | options: (a) accept degraded, same-machine-only (b) interim shared bus (c) wait for fleetd before enabling cross-host | default: (a) because inventing a stopgap bus duplicates fleetd (`docs/plans/2026-09-28-fleetd-eventbus-spec.md:3`) | blocks: §6 messaging row | reversible: yes |` (inference.)

## 11. Closing table — lanes + estimated Claude tokens

Budget mode (D-102, live per §5): writers and first-reviewers are free models; Claude is
used only for reviews/finals, declared through `claude_allowed`
(`tools/autoos_resolver.py:638-674`). Estimates are sizing judgement `(inference)`, not
measurement; every lane is kept small so no row is flagged as over ~1M weighted tokens.

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| FALLBACK-0 (phase 0, operator action) | Confirm the live substrate end-to-end: flip `claude_gateway_routing=gateway` (§3), drive one opencode web L0 session (§2), read the §6 loss table back to the operator as the interim state | tiny (final review only; free-model writer) | <1 day | nothing — all pieces already in the checkout |
| FALLBACK-1 (phase 1) | Run the §3 measured comparison on both candidates; answer fidelity (a) and aliasing (b); record the per-layer winner and the `/v1/messages` + small-model facts (§10) | small (final review only) | 1–2 days | nothing new to build; operator time on the probe |
| FALLBACK-2 (phase 2) | H3 opencode/OpenHands renderer so the L0 is Claude-Code-equivalent (§2) | small (final review only) | ~2 evenings | HOOKS H1 shared shape; H3 renderer built (`docs/plans/2026-09-28-agent-hooks-spec.md:255`) |
| FALLBACK-3 (phase 3) | Wire B4's trigger + relaunch-with-env to this spec's §5 needs; enforce the repeat-relaunch-is-skip obligation | small–medium FINAL ONLY (a relaunch path that mis-fires on real orchestrators is state-mutating) | ~2–3 evenings | ORCH-B4 built (`docs/tasks.md:53`); CLAUDEBUDGET gate (already live, §5) |
| FALLBACK-4 (phase 4) | Cross-host coordination substitute for the §6 messaging row | out of this lane's scope | — | fleetd bus built (`docs/plans/2026-09-28-fleetd-eventbus-spec.md:3`) |
