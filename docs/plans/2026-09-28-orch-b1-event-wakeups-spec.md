# ORCH-B1 — Event-Driven Wake-Ups
- Date: 2026-09-28.
- Path: `docs/plans/2026-09-28-orch-b1-event-wakeups-spec.md`.
- Task: ORCH-B1.
- Status: spec, not implemented.
- Owners: orchestrator sessions (`L1-main`, `L1-routing`, `L1-backlog`, `L2-general`).
- Related: D-102, D-121, R-coord-07, R-coord-08, ORCH-C1, ORCH-B2, ORCH-B3, B4.

## 1. Purpose
- 1.1 Replace 10-min polling heartbeats with event-driven wake-ups.
- 1.2 Meet D-121: idle orchestrator spends 0 event-less turns except the dead-man beat (D4), measured.
- 1.3 Keep idle sessions under 8 h retirement without idle polling.
- 1.4 Preserve zero missed orders, pings, readies, and reviews.
- 1.5 Cut D-102 budget-mode idle cost to dead-man beats only.

## 2. Background
- 2.1 Today each orchestrator runs a 10-min `CronCreate` heartbeat.
- 2.2 Rule source is skill rule R-coord-07.
- 2.3 Stated why: an idle session is retired after 8 h.
- 2.4 Each beat costs one model turn even when nothing changed.
- 2.5 Current rate is 6 turns/h per session.
- 2.6 Current rate is 144 turns/day per session.
- 2.7 Most beats are no-ops.
- 2.8 Under D-102 budget mode this is the biggest idle cost.
- 2.9 Operator goal D-121 is 0 turns/h idle, measured.
- 2.10 Quiet hours must be 0 turns except the dead-man beat.
- 2.11 Inboxes are append-only markdown at `RUN/inbox/<name>.md`.
- 2.12 One record per line starting with ISO UTC stamp.
- 2.13 `tools/autoos_inbox.py` parses them.
- 2.14 It exposes `Record`.
- 2.15 It exposes `Position` as `<timestamp>#<ordinal>`.
- 2.16 It exposes `read_since`.
- 2.17 It exposes `latest_position`.
- 2.18 It exposes `card_last_event` as `last-event <position>` in status card header.
- 2.19 `autoos-agent.py inbox <name>` prints records.
- 2.20 It supports `[--since POS|--since-card CARD]`.
- 2.21 The order gate (`_gives_order`, `pause_state` hardening) is being built on lane L1-routing/R2a (R2a3..R2a7), NOT yet on main; B1 phase 1 starts after R2a merges and depends on it.
- 2.22 On main today `pause_state` has only the basic PAUSE/STOP/HOLD/RESUME scan.
- 2.23 R2a adds: ack markers `→ done:` and `lesson:`.
- 2.24 R2a adds: closing words within a window.
- 2.25 R2a adds: a negation veto; RESUME gated like PAUSE (R2a7).
- 2.26 Policy is lost order = HIGH severity.
- 2.27 Policy is spurious wake = acceptable.
- 2.28 Lane workers run as `systemd --user` units.
- 2.29 Unit names look like `autoos-lane-<l1r|...>–<name>`.
- 2.30 Workers write `work/<lane>/<name>.out`.
- 2.31 A unit going inactive means worker finished/died.
- 2.32 CI results arrive via `gh`.
- 2.33 ORCH-C1 REVIVE restores bg sessions on crash/user-manager restart.
- 2.34 C1 uses a 5-min watchdog.
- 2.35 C1 uses a RUN/STOP marker.
- 2.36 C1 is owned by `L1-backlog` and in progress.
- 2.37 ORCH-B3 is JSONL inbox plus per-reader offsets, later.
- 2.38 ORCH-B2 is ready-check JSON record.
- 2.39 B4 is budget governor.

## 3. Harness constraints
- 3.1 `CronCreate` is session-only recurring prompt.
- 3.2 `CronCreate` fires only when the REPL is idle.
- 3.3 `CronCreate` auto-expires after 7 days.
- 3.4 `Monitor` runs a shell command.
- 3.5 Every `Monitor` stdout line becomes a notification that wakes the session.
- 3.6 Each `Monitor` is killed at timeout, max 30 min.
- 3.7 The expiry itself is one notification/turn.
- 3.8 A re-armed `Monitor` therefore costs >= 2 turns/h even when quiet.
- 3.9 `Bash run_in_background` runs detached.
- 3.10 The session is re-invoked once when that command exits.
- 3.11 An until-loop that exits only on actionable event costs 0 turns while quiet.
- 3.12 Whether background Bash has a hard timeout is measured in phase 0.
- 3.13 Whether it survives `/clear` is measured in phase 0.
- 3.14 Whether it survives compaction is measured in phase 0.
- 3.15 Whether it survives REPL-busy periods without loss is measured in phase 0.
- 3.16 What counts as idle for 8 h retirement is measured in phase 0.

| Mechanism | Quiet cost | Wake model |
|---|---|---|
| 10-min CronCreate | 6 turns/h, 144/day | 1 turn per beat |
| Monitor re-armed | >= 2 turns/h | 1 turn per line + 1 per expiry |
| Bash background + `--exit-on-first` | 0 turns while quiet | 1 turn per exit/batch |
| 3-h dead-man CronCreate | 8 turns/day | 1 turn per 3 h |

## 4. Decisions
### D1 Follower subcommand
- D1.1 Add `autoos-agent.py inbox <name> --follow --actionable`.
- D1.2 Support optional `[--since-card CARD]`.
- D1.3 Support optional `[--exit-on-first]`.
- D1.4 Support optional `[--include-units PREFIX]`.
- D1.5 Support optional `[--include-ci BRANCHES]`.
- D1.6 The command blocks instead of dumping and exiting.
- D1.7 It prints one line per ACTIONABLE event.
- D1.8 It never prints a raw record dump.
- D1.9 It reuses the R2a order gate helper, no copy; if R2a is not merged, phase 1 waits (no second implementation).
- D1.10 It reads through `autoos_inbox` API.
- D1.11 B3 JSONL support is therefore a reader swap, not a rewrite.
- D1.12 It uses `inotify` when available.
- D1.13 Else it polls mtime/size every 2 s.
- D1.14 Rationale: no copy prevents order-semantics drift.
- D1.15 Rationale: one line per event keeps wakes auditable.
- D1.16 Rationale: blocking follower enables 0-turn quiet wait.

| Actionable | Not actionable |
|---|---|
| Order via heartbeat gate | This reader's own lines |
| Ping | `→ done:` acks |
| `main=` line | `lesson:` lines |
| Review request | Non-matching chatter |
| Ready | Already-acked orders |
| Any `from <parent>` line addressed to this reader | — |
| Opt-in lane unit with prefix going inactive | Unit still active |
| Opt-in CI conclusion for tracked branch | CI for untracked branch |

- D1.17 Lane-unit watch is opt-in by prefix.
- D1.18 CI watch is opt-in by branch list.
- D1.19 Default follower watches inbox only.
- D1.20 Edge: malformed lines do not crash the follower.
- D1.21 Edge: late-stamped lines are still surfaced once.

### D2 Offsets
- D2.1 Follower resumes from status card `last-event` via `card_last_event`.
- D2.2 If no card is given, resume after this reader's newest own line in the inbox (its last ack), else from the last 30 records - never from the end (an event that landed before arming would be lost).
- D2.3 Explicit `--since` overrides card when provided for repair.
- D2.4 Order per handled event: (1) write the new position into the card `last-event`, (2) re-arm the follower from that card, (3) side effects (push, pong, spawn). Re-arming before the card write would self-wake on the same event.
- D2.5 A restart therefore neither replays nor misses handled events.
- D2.6 Malformed/late lines are reported once as one event.
- D2.7 They do not advance past unprocessed good records.
- D2.8 Rationale: card is already the durable per-reader cursor.
- D2.9 Edge: missing card falls back as in D2.2; a replayed already-handled event is a spurious wake (acceptable), a skipped one is a lost order (HIGH).
- D2.10 Edge: corrupt position is reported once as an event, then the follower continues with the D2.2 fallback - it never stops following.
- D2.11 B3 will replace card cursor with per-reader offsets without changing D2 semantics.

### D3 Harness binding
- D3.1 Primary is `Bash run_in_background` with `--exit-on-first`.
- D3.2 Quiet cost is 0 turns while the loop does not exit.
- D3.3 One wake covers one event batch, then the session re-arms.
- D3.4 `Monitor` is fallback only.
- D3.5 Fallback is used only if phase 0 shows background Bash is killed or silently lost.
- D3.6 Events within 5 s are coalesced into one line.
- D3.7 A storm of > 20 events/min collapses to one `N events, read inbox` line.
- D3.8 Rationale: `Monitor` cannot meet idle budget due to expiry turns.
- D3.9 Rationale: exit-on-first bounds one wake to one turn.
- D3.10 Rationale: coalescing prevents wake storms from matching polling cost.
- D3.11 Edge: events arriving mid-turn are caught by next re-arm from card offset.
- D3.12 Edge: re-arm always uses card offset, never in-memory offset alone.
- D3.13 Background Bash lifetime across `/clear` is measured in phase 0.
- D3.14 Background Bash lifetime across compaction is measured in phase 0.

### D4 Dead-man keepalive
- D4.1 Keep one `CronCreate` fallback beat every 3 h, not 10 min.
- D4.2 It checks the follower is alive.
- D4.3 Follower writes `RUN/state/<name>.follow` with `{pid, started, last_poll}`.
- D4.4 Stale `last_poll` > 2 min means re-arm.
- D4.5 Missing pid means re-arm.
- D4.6 Dead pid means re-arm.
- D4.7 Otherwise the beat ends the turn with no output.
- D4.8 3 h keeps the session under 8 h idle retirement even if every event is missed.
- D4.9 ORCH-C1 REVIVE covers actual session death, not this beat.
- D4.10 Resulting idle cost is 8 turns/day versus 144.
- D4.11 The `0 turns/h` target is met as `0 event-less turns except dead-man`.
- D4.12 Say so plainly in operator reports; do not claim literal zero.
- D4.13 Interval is a policy knob, not hardcoded doctrine.
- D4.14 Raise it if phase 0 shows the 8 h rule counts only user turns.
- D4.15 While any lane unit of this session runs, the dead-man interval is 30 min (stall detection for hung-but-alive workers); 3 h only when no lane runs (see Q2).
- D4.16 Every dead-man firing unconditionally recreates its own cron (delete + CronCreate) so the 7-day expiry never removes it; also recreated after relaunch and clear.
- D4.17 Rationale: polling is retained only as loss detector.
- D4.18 Edge: dead-man itself must not do polling work when follower is healthy.

### D5 Lane and CI watch
- D5.1 While lanes run, follower also watches unit exits when `--include-units` is set.
- D5.2 It also watches CI conclusions when `--include-ci` is set.
- D5.3 No separate polling beat is needed for `is my worker done`.
- D5.4 A worker exceeding expected runtime is caught by the dead-man beat.
- D5.5 Stall classification is unchanged.
- D5.6 Rationale: unit exit is an event, not a polled state.
- D5.7 Edge: unit flapping inactive/active must coalesce like inbox storms.
- D5.8 Edge: CI branch list must be explicit; no wildcard watch.
- D5.9 CI polling mechanism itself must stay quiet-turn-free or be disabled.

### D6 Skill rewrite
- D6.1 Rewrite R-coord-07 to `Wake on events`.
- D6.2 New text: arm `inbox --follow --actionable --exit-on-first` in background at launch and after every handled event.
- D6.3 New text: a 3 h `CronCreate` dead-man beat re-arms it.
- D6.4 New text: recreate both after relaunch or clear.
- D6.5 New why: `10-min beats cost 144 idle turns/day; an idle session is retired after 8 h`.
- D6.6 Move R-coord-08 beat duties to handler or dead-man.
- D6.7 Do not leave any 10-min duties on a timer.

| R-coord-08 duty | New home |
|---|---|
| push | event handler, after handling actionable inbox event |
| pong | event handler, on ping event |
| WIP-commit | dead-man beat, only if dirty work exists |
| status stamp | dead-man beat, lightweight `last-event` check |
| relaunch quiet child | dead-man beat, after follower-liveness check |

- D6.8 Rationale: push/pong are responses, not ticks.
- D6.9 Rationale: WIP-commit and stamps are loss-tolerant and fit 3-h cadence.
- D6.10 Rationale: relaunch belongs with liveness checks in dead-man and C1.
- D6.11 Edge: handler order is card write -> re-arm -> side effects (D2.4).
- D6.13 The parent's 'status quiet > 25 min -> relaunch child' check (SKILL.md levels table, R-coord-08) reads the card header stamp; every card write (D2.4 per event, dead-man beat) refreshes it. The threshold becomes 'quiet > dead-man interval + 10 min' (40 min with lanes running, 3 h 10 min idle); rewritten together with R-coord-07/08.
- D6.12 Edge: dead-man must skip WIP-commit when tree is clean.

## 5. Phases
- 5.1 Phase 0 is probe, no fleet change.
- 5.2 Phase 1 is follower plus tests.
- 5.3 Phase 2 is 24-h pilot on one L2.
- 5.4 Phase 3 is fleet plus skill rewrite.

| Phase | Work | Exit signal |
|---|---|---|
| 0 probe | Background Bash lifetime, `/clear`, compaction, REPL-busy, Monitor expiry cost, 8-h idle definition | Measurements recorded, binding chosen |
| 1 follower | Implement D1–D2, replay tests, offset-resume tests | Tests pass, no missed ready |
| 2 pilot | One L2 for 24 h, turns/h via transcripts | Idle <= 8/day, zero missed orders/readies |
| 3 fleet | Roll to all orchestrators, rewrite R-coord-07/08 | Fleet idle budget met |

- 5.5 Phase 0 measures Bash hard-timeout behavior.
- 5.6 Phase 0 measures survival across `/clear`.
- 5.7 Phase 0 measures survival across compaction.
- 5.8 Phase 0 measures behavior when REPL is busy.
- 5.9 Phase 0 confirms Monitor expiry turn cost.
- 5.10 Phase 0 confirms what counts toward 8 h retirement.
- 5.11 Phase 1 replay uses a scripted inbox.
- 5.12 Script includes orders, pings, acks, own lines.
- 5.13 Expected output is exactly the actionable subset.
- 5.14 Phase 1 tests no missed ready.
- 5.15 Phase 1 tests offset resume from card.
- 5.16 Phase 2 measures turns/h via transcripts.
- 5.17 Phase 2 runs a shadow 10-min cron log that only records, never acts.
- 5.18 Or phase 2 uses post-hoc inbox audit if shadow log perturbs measurement.
- 5.19 Phase 3 does not start until pilot acceptance passes.

## 6. Acceptance
- 6.1 Idle session <= 8 turns/day measured over 24 h.
- 6.2 Zero missed orders in replay test.
- 6.3 Zero missed readies in replay test.
- 6.4 Zero missed orders in pilot versus shadow log/audit.
- 6.5 Zero missed readies in pilot versus shadow log/audit.
- 6.6 Replay test covers acks that must not refire.
- 6.7 Replay test covers own lines that must not self-wake.
- 6.8 Offset resume neither replays nor skips.
- 6.9 Dead-man beat re-arms a dead follower within one 3-h tick.
- 6.10 Storm input collapses instead of emitting one wake per record.
- 6.11 Stall latency: a hung-but-alive worker is noticed within 40 min while lanes run (30 min dead-man + 10 min slack), measured in the pilot with an injected hung worker.
- 6.12 Dead-man cron still present after a simulated 7-day expiry (recreated on every firing).

## 7. Risks and edge cases
- 7.1 Follower dies silently and events queue unseen until dead-man.
- 7.2 Mitigation is `RUN/state/<name>.follow` plus 3-h liveness check.
- 7.3 Events arrive while session is mid-turn.
- 7.4 Mitigation is next re-arm starts from card offset, so nothing is lost.
- 7.5 Two followers run on one inbox.
- 7.6 Mitigation is pid lock; second starter exits with one line.
- 7.7 Clock skew produces out-of-order stamps.
- 7.8 Mitigation is ordinal tiebreak plus report-late-once.
- 7.9 Late-stamped lines are reported once, not silently dropped.
- 7.10 Session is compacted and forgets to re-arm.
- 7.11 Mitigation is dead-man beat plus launch/clear checklist in skill.
- 7.12 PAUSE semantics are unchanged.
- 7.13 A PAUSE event wakes immediately.
- 7.14 RESUME likewise wakes immediately.
- 7.15 Order-gate false negatives remain HIGH severity.
- 7.16 Order-gate false positives remain acceptable.
- 7.17 Each wake equals one turn for budget accounting.
- 7.18 Log wakes to `RUN/state/<name>.wakes` for measurement.
- 7.19 7-day `CronCreate` expiry can silently remove dead-man.
- 7.20 Mitigation is recreate-on-launch plus dead-man age check.
- 7.21 `Monitor` fallback would reintroduce >= 2 turns/h if chosen.
- 7.22 Therefore fallback requires explicit phase-0 justification.
- 7.23 Unit-exit signal may race inbox `ready`.
- 7.24 Mitigation is treat either as wake; dedupe by card offset.

## 8. Open questions
- Q1 Does 8 h retirement count background-task wakes as activity?
- Q1 Resolution is measured in phase 0.
- Q2 Should dead-man shorten while lanes run, e.g. 1 h?
- Q2 Decided: yes, 30 min while lanes run (D4.15); revisit with pilot stall data.
- Q3 One follower per session versus one per inbox shared by supervisor?
- Q3 Decision is deferred to B3.
- Q3 Per-session follower ships now; shared supervisor waits for JSONL offsets.

## 9. Out of scope
- 9.1 JSONL inbox belongs to B3.
- 9.2 Ready-check JSON record belongs to B2.
- 9.3 Budget governor belongs to B4.
- 9.4 Session restore on crash belongs to C1 REVIVE.
- 9.5 No change to PAUSE/STOP/HOLD order vocabulary here.
- 9.6 No change to lane worker execution itself.
- 9.7 No new dashboard or alerting channel.

## 10. Files and interfaces
- 10.1 Inbox path: `RUN/inbox/<name>.md`.
- 10.2 Card cursor: `last-event <position>`.
- 10.3 Follower state: `RUN/state/<name>.follow`.
- 10.4 Wake log: `RUN/state/<name>.wakes`.
- 10.5 CLI: `autoos-agent.py inbox <name> --follow --actionable [--since-card CARD] [--exit-on-first] [--include-units PREFIX] [--include-ci BRANCHES]`.
- 10.6 Exit code 0 on `--exit-on-first` event batch.
- 10.7 Nonzero with one stderr line on lock contention.
- 10.8 Stdout contract is one line per actionable event batch, nothing else.
