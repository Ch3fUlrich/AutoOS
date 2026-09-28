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
- D1.4 `[--include-units PREFIX]` is the lane monitor's prefix switch, not a follower flag: the follower takes no unit-watch flag and never watches units directly; unit events arrive only as monitor-written inbox lines (D5.1).
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
- D1.22 The follower reads by byte offset, not by timestamp: append order is the truth. A late-stamped record (stamp older than records already read) is still surfaced once in append position; it is never skipped and never reorders already-read records (see D2.6).

| Actionable | Not actionable |
|---|---|
| Order via heartbeat gate | This reader's own lines |
| Ping | `→ done:` acks |
| `main=` line | `lesson:` lines |
| Review request | Non-matching chatter |
| Ready | Already-acked orders |
| Any `from <parent>` line addressed to this reader | — |
| Lane-monitor unit event line (`exit-ok`, `exit-failed`, `exit-unknown`, `stall` for a tracked prefix, D5.1/D5.4) | Duplicate `lane@run-id@event-type` or unit active with fresh output |
| Opt-in CI conclusion for tracked branch | CI for untracked branch |

- D1.17 Lane-unit events arrive only as lane-monitor inbox lines (D5.1); tracking is opt-in by prefix on the monitor.
- D1.18 CI watch is opt-in by branch list.
- D1.19 Default follower watches inbox only.
- D1.20 Edge: malformed lines do not crash the follower.
- D1.21 Edge: late-stamped lines are still surfaced once.

### D2 Offsets
- D2.1 Follower resumes from status card `last-event` via `card_last_event`.
- D2.2 If no card is given, resume from this session's own last handled line: scan the inbox for the newest `→ done:` record written by this reader and resume after it; if none exists, resume from the START OF THE FILE (not the start of today - a pre-today order with no ack is still an unhandled order). There is NO "last 30 records" fallback - it can silently skip an order that landed 31 records back. Spurious re-delivery from the wider fallback is accepted (policy 1.4/1.5: missed = HIGH, spurious = fine).
- D2.3 Explicit `--since` overrides card when provided for repair.
- D2.4 Order of effects per handled event (safety asymmetry: a missed order/ping/ready is HIGH, a spurious wake is fine, so a crash must re-deliver, never drop): (1) perform the side effect idempotently, keyed by the record's inbox offset/position (repeating the same offset is a no-op, e.g. ack already present); (2) advance the cursor by writing the new position into the card `last-event`; (3) re-arm the follower from that card. Re-arming before the card write would self-wake on the same event; writing the card before an idempotent side effect would drop the event on a crash between the two steps.
- D2.5 A restart therefore replays (re-delivers) at most the one event whose card write never landed, and never misses a handled event. Replays are spurious wakes (acceptable); skips are lost orders (HIGH).
- D2.6 Malformed/late lines are reported once as one event, in append (byte-offset) order, not timestamp order (D1.22).
- D2.7 They do not advance past unprocessed good records.
- D2.8 Rationale: card is already the durable per-reader cursor.
- D2.9 Edge: missing card falls back as in D2.2; a replayed already-handled event is a spurious wake (acceptable), a skipped one is a lost order (HIGH).
- D2.10 Edge: corrupt position is reported once as an event, then the follower continues with the D2.2 fallback - it never stops following.
- D2.11 B3 will replace card cursor with per-reader offsets without changing D2 semantics.
- D2.12 Spawn idempotency (a spawn is a D2.4 step-1 side effect, so it must be safe to repeat): every spawn side effect carries a deterministic title `lane@offset` (lane name + the triggering record's inbox offset/position). The spawner refuses a second spawn with the same title for as long as a worker with that title is still RUNNING (no time limit on the running refusal); once the title is completed (done), the refusal persists for 24 h after completion - the refusal is a no-op success, not an error. A crash after spawn but before the card write therefore re-delivers the order as a refused-duplicate no-op on restart, never as a second worker. Rationale: the record offset is the only id that survives the crash; the 24-h window bounds spawner state for completed titles, while a still-running worker is authoritative so its refusal never expires.

### D3 Harness binding
- D3.1 Primary is `Bash run_in_background` with `--exit-on-first`.
- D3.2 Quiet cost is 0 turns while the loop does not exit.
- D3.3 One wake covers one event batch, then the session re-arms.
- D3.4 `Monitor` is fallback only.
- D3.5 Fallback is used only if phase 0 shows background Bash is killed or silently lost.
- D3.6 Events within 5 s are coalesced into one line.
- D3.7 Storm backoff: a burst of > 20 events/min collapses to one `N events, read inbox` wake, and while the rate stays above that threshold the follower emits at most one wake per 60 s (sustained collapse, not one wake per record and not one collapse followed by per-record wakes).
- D3.8 Rationale: `Monitor` cannot meet idle budget due to expiry turns.
- D3.9 Rationale: exit-on-first bounds one wake to one turn.
- D3.10 Rationale: coalescing prevents wake storms from matching polling cost.
- D3.11 Edge: events arriving mid-turn are caught by next re-arm from card offset.
- D3.12 Edge: re-arm always uses card offset, never in-memory offset alone.
- D3.13 Background Bash lifetime across `/clear` is measured in phase 0.
- D3.14 Background Bash lifetime across compaction is measured in phase 0.

### D4 Dead-man keepalive
- D4.1 Dead-man interval is taken from exactly one table (no other section states an interval):

| Session state | Dead-man interval | Idle cost |
|---|---|---|
| No lane unit of this session running | 3 h | 8 turns/day |
| Any lane unit of this session running | 30 min | <= 48 turns/day while lanes run |

- D4.1a The D4.1 cadence guards the follower directly and the lane monitor indirectly through it: each dead-man firing checks the follower heartbeat and re-arms the follower when the single D4.4 rule reports DEAD (after the D4.4 takeover of any dead holder). The dead-man never starts the monitor directly: when the follower is DEAD it re-arms the follower (after the D4.4 takeover of any dead holder), and the follower's arm step then calls ensure_monitor(). ensure_monitor() is the ONLY code that starts a monitor - a single idempotent start routine (start iff PENDING runs exist per D4.2 and the monitor lock is not fresh, per D5.12) called from exactly three places: (1) the follower's arm step, (2) the follower's own heartbeat tick (every <= 60 s, D4.3), (3) the spawner only after the unit has started (systemd-run returned success) and it has written started_at into job.json. The extra two call sites close the healthy-follower gap: a newly PENDING run, or a monitor killed while the follower is healthy, restarts the monitor within one follower tick (<= 60 s) without waiting for a dead-man fire.

- D4.2 It checks the follower AND the lane monitor are alive. The monitor writes its own heartbeat file `RUN/state/<name>.monitor` (content `{pid, started, session_id}`, mtime is the heartbeat, touched at least every 60 s, same cadence as the follower in D4.3). The dead-man re-arms the follower when its heartbeat is missing or stale per the single D4.4 rule (pointer only; one DEAD rule for both) - it never starts the monitor directly (D4.1a). The monitor runs ONLY while at least one PENDING run exists (a PENDING run = a run whose `job.json` carries `owner_inbox` and has no terminal inbox line yet written for its run-id; `started_at` is optional for pendingness - a run without `started_at` is still PENDING). When the last PENDING run becomes terminal the monitor exits cleanly and removes its heartbeat file and lock on the clean exit. The D4.4 DEAD rule applies to the monitor only while PENDING runs exist; with no PENDING runs, a missing monitor heartbeat is not DEAD and the dead-man takes no monitor action. With a healthy follower the monitor is (re)started within one follower tick (<= 60 s) via the D4.1a ensure_monitor() calls (follower tick, spawner), not via the dead-man.
- D4.3 The follower touches (rewrites) `RUN/state/<name>.follow` with `{pid, started, session_id}` at least every 60 s in BOTH `inotify` and poll modes - the file mtime IS the heartbeat.
- D4.4 Single liveness/takeover rule (exactly one rule - it reconciles the former mtime-only and lock-only wordings; D4.5, D4.6 and D4.15a are pointers to it, not second rules): the follower is DEAD when ANY of (a) the lock pid is gone or its start time differs from the recorded start time (pid gone or recycled), OR (b) the heartbeat file `RUN/state/<name>.follow` mtime is > 2 min stale (covers a SIGSTOPped or hung follower whose pid is alive but makes no progress), OR (c) the heartbeat file is missing (missing = stale). The dead-man never judges by a `last_poll` content field (a dead follower's last-written content looks fresh forever). Takeover: verify the lock's recorded start time against the live pid FIRST; if the start-time check FAILS (the pid was recycled), send NO signal to that pid - treat the lock as dead and take it over (start the replacement and log the TAKEOVER line without signalling the recycled pid). Only when the start time MATCHES, SIGTERM the old pid, then SIGKILL if it is still alive after a grace period, then start the replacement and log a loud TAKEOVER line to the wake log. A second starter against a FRESH follower (pid alive with matching start time AND heartbeat file present with mtime <= 2 min stale) exits nonzero with one loud stderr line and starts no second reader.
- D4.5 Pointer to D4.4, not a second rule: a missing heartbeat file is case (c) of the DEAD rule (missing = stale) and is re-armed via the D4.4 takeover procedure.
- D4.6 Pointer to D4.4, not a second rule.
- D4.7 Otherwise the beat ends the turn with no output.
- D4.8 What keeps the session alive: each dead-man `CronCreate` firing IS a model turn, and a turn counts as session activity against the 8 h idle-retirement rule - phase-0 probe 5.10 is a GATE, not a measurement: phase 1 is NOT built until 5.10 passes. If dead-man fires do not count as activity (FAIL), the spec STOPS at phase 0 and returns to L1-routing with the measurement; the D4.1 interval table is void and there is no hedged "re-decided" inside this spec. While the gate is unpassed, both intervals are <= the idle limit minus a margin (30 min and 3 h are both < 8 h), so the session never idles out even if every inbox event is missed.
- D4.9 ORCH-C1 REVIVE covers actual session death, not this beat.
- D4.10 Resulting idle cost is 8 turns/day versus 144 (30-min cadence applies only while lanes run, when the session is not idle by definition).
- D4.11 The `0 turns/h` target is met as `0 event-less turns except dead-man`.
- D4.12 Say so plainly in operator reports; do not claim literal zero.
- D4.13 Interval is a policy knob, not hardcoded doctrine.
- D4.14 Raise it if phase 0 shows the 8 h rule counts only user turns.
- D4.15 The cadence switches at the transition itself: spawning the first lane recreates the dead-man cron at 30 min immediately; the last lane ending recreates it at 3 h. (Stall detection itself is NOT the dead-man's job - see D5.4.)
- D4.15a One reader per inbox by design (one session = one inbox). The follower holds `RUN/state/<name>.follow.lock` containing `{pid, start_time, session_id}`. Liveness and takeover follow the single D4.4 rule (this paragraph states the lock mechanics, not a second liveness rule): a stale lock (either D4.4 condition) is taken over via SIGTERM-then-SIGKILL after start-time verification, with a loud TAKEOVER line in the wake log; a second starter against a FRESH heartbeat still exits nonzero with one loud stderr line and never starts a second reader (a silent second reader would starve or double-deliver).
- D4.16 Every dead-man firing unconditionally recreates its own cron (delete + CronCreate) so the 7-day expiry never removes it; also recreated after relaunch and clear.
- D4.17 Rationale: polling is retained only as loss detector.
- D4.18 Edge: dead-man itself must not do polling work when follower is healthy.

### D5 Lane and CI watch
- D5.1 The inbox is the ONLY event source. The follower never watches units directly; while lanes run, the lane monitor writes EVERY unit event (exit ok, exit failed, stall) as an inbox line keyed `lane@run-id@event-type`, and the follower surfaces those lines like any other actionable event. `--include-units` is the monitor's prefix switch, not a follower watch. PRIMARY PATH for exits is the unit itself: every lane unit carries an `ExecStopPost=` hook (fires on success and failure) that appends that unit's own `lane@run-id@exit-ok|exit-failed` line to the owning session's inbox; the monitor's reconciliation (D5.4) is the backstop, not the primary. Inbox resolution: at spawn, the spawner writes the owning session's ABSOLUTE inbox path into job.json (`owner_inbox`) and bakes it as a literal argument into the unit's `ExecStopPost=` command line (no env lookup at exit time; the unit runs as the same user), starts the unit; when systemd-run FAILS the spawner itself synchronously appends that run's `lane@run-id@exit-failed` line to the owner inbox (the common cause - no monitor or grace wait needed); only after systemd-run returns success writes `started_at` into job.json and then calls ensure_monitor() (D4.1a) so a newly PENDING run starts the monitor even when the follower is healthy and the monitor had exited cleanly. A PENDING run without `started_at` whose spawner crashed between the job.json write and `started_at` is covered by the D5.4 2-min grace path, not by the spawner path. Scope: only units launched by the spawner are lanes. A unit without job.json/`owner_inbox` is not tracked by design; the ready-check refuses a lane whose run has no job.json.
- D5.2 It also watches CI conclusions when `--include-ci` is set.
- D5.3 No separate polling beat is needed for `is my worker done`.
- D5.4 All unit events are detected by the lane monitor, NOT the dead-man and NOT the follower: the lane monitor watches each tracked unit and its output file `work/<lane>/<name>.out` and emits an inbox event line for EVERY unit event - exit ok, exit failed, and stall (unit active but output file shows no new bytes for N min, default N = 20); the follower surfaces each line like any other actionable event. Exit lines are written PRIMARILY by each unit's own `ExecStopPost=` hook (D5.1), which runs on success and failure. On every (re)start the monitor reconciles from DURABLE per-run records, never from live unit state: it reads `logs/agents/<run-id>/job.json` (written at spawn, never garbage-collected by systemd) and for every PENDING run (`job.json` with `owner_inbox` per D4.2, `started_at` optional, and no terminal inbox line already written) it writes exactly one - for a run that already has ANY terminal line (exit-ok, exit-failed, exit-unknown, or any other terminal type) reconciliation writes nothing - exit state taken from `job.json` (or the still-present unit when it agrees), else `exit-unknown`, which the follower surfaces as a wake like any other exit type. The 2-min grace applies ONLY to reconciliation's `exit-unknown`: reconciliation never writes `exit-unknown` for a PENDING run without `started_at` whose `job.json` is younger than 2 min (the unit may not have started yet - the spawner writes `started_at` only after systemd-run success, D5.1); a PENDING run without `started_at` whose `job.json` is older than 2 min gets `exit-unknown` (the spawner crashed between the job.json write and `started_at`, and it wakes like any other exit type). A systemd-run failure itself never waits for this grace path - the spawner writes `exit-failed` synchronously (D5.1). Live `systemctl` state is consulted only as a hint, never as the source of truth, so a unit that exits while the monitor is down and is GC'd before restart is still delivered once. The dead-man only guards the follower and the monitor (D4.1a/D4.2) and never classifies worker events.
- D5.5 Stall classification is unchanged.
- D5.6 Rationale: unit exit is an event, not a polled state.
- D5.7 Edge: flapping is a pointer to D5.10, not a separate rule: each run-id is its own event; a flap = two runs = two wakes, accepted.
- D5.8 Edge: CI branch list must be explicit; no wildcard watch.
- D5.9 CI polling mechanism itself must stay quiet-turn-free or be disabled.
- D5.10 The lane monitor writes each unit event as an inbox line carrying id `lane@run-id` (unit name + systemd invocation/run id, recorded by the monitor at spawn) plus event type. The follower dedupes unit events on the key `lane@run-id@event-type` (exit-ok, exit-failed, exit-unknown, stall are DISTINCT types; "ready" is NOT a unit event type - "ready" is an inbox record from a person/session, deduped by its offset), NOT on the inbox offset: the same `lane@run-id@exit-ok` twice (hook line plus reconciliation backstop) emits one wake; a `stall` followed by an `exit-*` for the same run-id emits TWO wakes (the exit is new information even after the stall); two different run-ids of the same lane emit two wakes. `exit-unknown` (reconciliation found a run-id with no terminal line and no exit state in `job.json` or the live unit) wakes like any other exit type - it means "finished, state lost", never silence.
- D5.11 Append atomicity (writers and follower move together): every writer (monitor, `ExecStopPost` hook, send tools) appends each inbox line in ONE `O_APPEND` write of under 4096 bytes (a single `write(2)` of one `\n`-terminated line, so POSIX guarantees atomicity); no writer ever emits a line in two writes. The follower consumes only up to the last `\n` in the file and never advances its byte offset past a partial (unterminated) tail - a mid-line writer pause is neither surfaced nor skipped; the completed line is read on the next pass.
- D5.12 One monitor per session by design. The monitor holds `RUN/state/<name>.monitor.lock` and follows the single D4.4 liveness/takeover rule by pointer (this paragraph states only the lock path, not a second rule): a stale monitor lock is taken over exactly as D4.4 prescribes. `ensure_monitor()` checks the lock first: if it is fresh, it returns 0 silently (no start, no error). If a monitor process it did start loses the lock race, that process exits nonzero, and `ensure_monitor()` treats that as success, not surfaced (quiet loser). The loud nonzero exit with one stderr line applies only to a monitor started directly (outside `ensure_monitor()`). Duplicate monitor-written lines remain harmless through the D5.10 `lane@run-id@event-type` dedupe.

### D6 Skill rewrite
- D6.1 Rewrite R-coord-07 to `Wake on events`.
- D6.2 New text: arm `inbox --follow --actionable --exit-on-first` in background at launch and after every handled event; the follower's arm step, the follower's heartbeat tick, and the spawner all call ensure_monitor() (D4.1a) - the single idempotent routine and ONLY monitor starter: it starts the lane monitor (10.5a) iff PENDING runs exist (D4.2) and its lock is not fresh. The dead-man guards the follower directly and the monitor indirectly through it (D4.1a/D4.2) and never starts the monitor directly.
- D6.3 New text: a `CronCreate` dead-man beat re-arms the follower at the D4.1 interval-table cadence (30 min while any lane of this session runs, 3 h otherwise) - not a flat 3 h; it never starts the monitor directly - when the follower is DEAD it re-arms the follower (after the D4.4 takeover of any dead holder), and the follower's arm then ensures the monitor iff PENDING runs exist. A dead monitor with a healthy follower is left to the follower's next tick (<= 60 s, D4.1a), not to the dead-man.
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
- D6.11 Edge: handler order is idempotent side effect (keyed by record offset) -> card write -> re-arm (D2.4).
- D6.13 The parent's 'status quiet > 25 min -> relaunch child' check (SKILL.md levels table, R-coord-08) reads the card header stamp; every card write (D2.4 per event, dead-man beat) refreshes it. The threshold becomes 'quiet > dead-man interval + 10 min' per the D4.1 table (40 min with lanes running, 3 h 10 min idle); rewritten together with R-coord-07/08.
- D6.12 Edge: dead-man must skip WIP-commit when tree is clean.

## 5. Phases
- 5.1 Phase 0 is probe, no fleet change.
- 5.2 Phase 1 is follower plus tests.
- 5.3 Phase 2 is 24-h pilot on one L2.
- 5.4 Phase 3 is fleet plus skill rewrite.

| Phase | Work | Exit signal |
|---|---|---|
| 0 probe | Background Bash lifetime, `/clear`, compaction, REPL-busy, Monitor expiry cost, 8-h idle definition | Measurements recorded, binding chosen, 5.10 PASS (gate: no PASS = no phase 1) |
| 1 follower | Implement D1–D2, replay tests, offset-resume tests | Tests pass, no missed ready |
| 2 pilot | One L2 for 24 h, turns/h via transcripts | Idle <= 8/day, zero missed orders/readies |
| 3 fleet | Roll to all orchestrators, rewrite R-coord-07/08 | Fleet idle budget met |

- 5.5 Phase 0 measures Bash hard-timeout behavior.
- 5.6 Phase 0 measures survival across `/clear`.
- 5.7 Phase 0 measures survival across compaction.
- 5.8 Phase 0 measures behavior when REPL is busy.
- 5.9 Phase 0 confirms Monitor expiry turn cost.
- 5.10 Phase 0 probe for the 8 h rule (GATE for phase 1 - PASS/FAIL recorded in the pilot notes): leave a session idle except for dead-man fires at the D4.1 cadence and check it is NOT retired after 8 h. PASS = alive with only dead-man turns: phase 1 may start. FAIL = retired: STOP, return to L1-routing with the measurement, do not build phase 1; the D4.1 table is void (see D4.8 - no in-spec re-decision).
- 5.10a Phase 0 harness-lifetime fallback: if neither background Bash nor Monitor survives `/clear`, compaction, and timeouts (all FAIL per 5.6/5.7), the fallback is the dead-man cron alone with no background process: every 30 min ALWAYS, idle included (no 3 h tier - with no follower there is no event path, so the dead-man is the only delivery path and a 3 h idle latency is unacceptable; this supersedes the D4.1 table). Honest cost: one turn per fire = up to 48 turns/day in every state (idle included). That fallback still meets zero-missed-orders (30-min polling bound) but NOT the 0 event-less turns target - the pilot report states which binding shipped.
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
- 6.8 Offset resume replays at most the one event whose card write never landed, and never skips a handled event (D2.5).
- 6.8a Effects-before-cursor kill test (finding 1): scripted harness delivers one order, then SIGKILLs the follower (a) after the side effect but before the card write, and (b) after the card write but before re-arm. In both cases the order is re-delivered on restart (spurious wake accepted) and never dropped; the side effect applied exactly once (idempotent key verified by record offset). Case (c) crash-after-spawn-before-card: the order triggers a spawn, the follower is SIGKILLed before the card write, the restart re-delivers the order and the spawner refuses the duplicate `lane@offset` title - the test asserts exactly one worker exists for that title. Case (c) also covers a worker with the same title still RUNNING after 24 h: a restart re-delivers the order and spawns nothing (the running refusal has no time limit, D2.12).
- 6.8b Byte-offset read test (finding 8, D1.22): scripted inbox appends a record with a backdated stamp AFTER newer records were read; the follower surfaces it once in append position and does not reorder or skip.
- 6.8c Cursor-fallback test (finding 3): with the card missing, the follower resumes after this session's newest `→ done:` line; with no `→ done:` line either, from the START OF THE FILE. The test inbox includes a pre-today unhandled order (no ack) and asserts it is delivered; it asserts no "last 30 records" cutoff exists and no order is skipped.
- 6.8d Unit-id dedupe test (finding 12): the same `lane@run-id@exit` twice (flap) emits one wake; two run-ids of the same lane emit two wakes; a `stall` followed by an `exit` for the SAME run-id emits two wakes (distinct event types, D5.10); inbox-offset dedupe alone would fail this test.
- 6.8e Follower-downtime unit-exit test (D5.1/D5.4): stop the follower, let a tracked unit exit (the unit's own `ExecStopPost=` hook delivers the exit by appending the `lane@run-id@exit-ok|exit-failed` line while the follower is down; reconciliation is the backstop only and writes nothing here), restart the follower -> exactly one wake for that exit, no loss and no duplicate.
- 6.8f Monitor-downtime + GC test (D5.1/D5.4/D5.10): stop the monitor, let a tracked unit exit (its `ExecStopPost` hook appends the `lane@run-id@exit-ok|exit-failed` line while the monitor is down), run `systemctl --user reset-failed` plus systemd GC so the unit record disappears, restart the monitor (its `job.json` reconciliation finds the run-id already has its terminal line and writes nothing) -> exactly one wake for that exit, from the hook line, with no loss and no duplicate.
- 6.8g Partial-line test (D5.11): a writer appends a line in two writes with a pause between them; the follower pass during the pause neither wakes on nor skips the partial tail (offset stays before it), and the next pass after the line completes delivers it exactly once.
- 6.8h Hook-failure backstop test (D5.1/D5.4/D5.10): with the monitor down and the unit's `ExecStopPost=` hook FAILING (inbox path unset or read-only, so no hook line is written), let a tracked unit exit, run `systemctl --user reset-failed` plus systemd GC so the unit record disappears, restart the monitor -> its `job.json` reconciliation writes exactly one terminal line for that run-id (exit state from `job.json`, or `exit-unknown` when no exit state survives), and the follower emits exactly one wake for that exit, no loss and no duplicate.
- 6.8i Concurrent-writer test (D5.11): two writers append concurrently (N lines each, interleaved, each line in one `O_APPEND` write per D5.11) -> every line is parsed intact, none is lost or merged (proves the one-write-per-line rule, not only the reader's partial-tail handling in 6.8g).
- 6.8j Spawn inbox-resolution test (D5.1): spawn -> `job.json` has the owning session's ABSOLUTE inbox path as `owner_inbox`, and the unit's `ExecStopPost=` carries that path as a literal argument (no env lookup); a lane whose run has no `job.json` is refused by the ready-check and untracked by design.
- 6.8k Flap-dedupe test (D5.7/D5.10): a unit that flaps (exits and is relaunched as a new run-id) emits two wakes - one per run-id (two keys, `lane@run-id-A@exit-*` and `lane@run-id-B@exit-*`); the test asserts two wakes, proving same-key-twice dedupe does not collapse two run-ids.
- 6.8l Premature-reconciliation test (D4.2/D5.1/D5.4): `job.json` written with `owner_inbox` but the unit not yet started (no `started_at`) -> monitor (re)start writes no `exit-unknown` line for that run-id. OLDER case: `job.json` 3 min old, no `started_at`, no terminal line, follower healthy, monitor stopped -> the next ensure_monitor starts a monitor, and exactly one `exit-unknown` line and one wake follow.
- 6.8m Spawn-failure test (D5.1/D5.4/D5.10): systemd-run fails -> the spawner writes `exit-failed` itself, synchronously; the follower emits exactly one wake for that exit, and a later monitor reconciliation writes no `exit-unknown` for that run-id (a terminal line already exists, deduped per D5.10).
- 6.9 Dead-man beat re-arms a dead follower within one tick of the D4.1 table (30 min while lanes run, 3 h idle). Heartbeat test (finding 2): follower heartbeat mtime advances at least every 60 s in BOTH inotify and poll modes; dead-man re-arms when and only when the single D4.4 rule says DEAD (mtime > 2 min stale, heartbeat file missing per case (c), OR pid gone/recycled - a test that stops the follower but leaves a fresh `last_poll` content field must still re-arm). Missing-file case: deleting `RUN/state/<name>.follow` makes the next dead-man check report DEAD per D4.4 case (c) and re-arm. Agrees with 6.9a: both tests assert the same D4.4 rule, one via the heartbeat leg, one via the lock leg.
- 6.9a Lock test (finding 4): a stale lock (pid gone, pid start time differs, OR heartbeat > 2 min stale per the single D4.4 rule) is taken over - SIGTERM then SIGKILL after start-time verification, loud TAKEOVER line in the wake log; a second starter against a live lock AND fresh heartbeat exits nonzero with one loud stderr line and starts no reader. Recycled-pid case: a decoy process holding the old pid with a different start time receives NO signal during takeover (it survives) - the test asserts the decoy is still alive after the takeover. SIGSTOP test: SIGSTOP the follower (pid stays alive, so the pid leg alone passes) -> heartbeat goes > 2 min stale -> the dead-man check replaces it (SIGTERM then SIGKILL the stopped pid, loud log line); a replay harness driving the dead-man check directly asserts replacement within 3 min of the staleness threshold. Monitor-lock variant (D5.12): `ensure_monitor()` checks the lock first - against a fresh monitor lock (`RUN/state/<name>.monitor.lock` with live pid, matching start time, and fresh heartbeat) it returns 0 silently and starts nothing; a monitor process it did start that loses the lock race exits nonzero, treated as success and not surfaced. The loud nonzero exit with one stderr line applies only to a monitor started directly (outside `ensure_monitor()`); a stale monitor lock is taken over per the D4.4 rule.
- 6.9b Monitor-lifecycle test (D4.1a/D4.2, dead-man guards the monitor indirectly through the follower): kill -9 the monitor while a PENDING run exists -> restarted within one follower tick (<= 60 s) even with the follower healthy (the follower tick's ensure_monitor() call; single D4.4 rule; monitor heartbeat `RUN/state/<name>.monitor` missing or stale); a unit that exited meanwhile is delivered exactly once (hook line, or reconciliation on restart per D5.4, deduped per D5.10).
- 6.9e Idle-to-pending spawn test (D4.1a/D4.2/D5.1): monitor stopped cleanly (no PENDING runs), follower healthy, spawn a new run with the follower tick disabled (or its interval set above the test window, so the tick path in 6.9b cannot fire) -> exactly one monitor starts within 5 s of the spawn (the spawner's ensure_monitor() call - the test fails if the spawner path is broken); that unit's `ExecStopPost=` hook is made to fail, and the exit is still delivered exactly once via reconciliation on (re)start (D5.4), deduped per D5.10.
- 6.9f Concurrent-starter test (D4.1a/D5.12): three concurrent ensure_monitor() callers -> exactly one monitor, and no contention error surfaced (the D5.12 lock serialises the starters; the losers see a fresh lock and return quietly, not nonzero/loud).
- 6.9c Monitor-idle test (D4.2): with no PENDING runs (no `job.json` with `owner_inbox` lacking a terminal line) the dead-man does not start a monitor - the test asserts no monitor process, no fresh monitor heartbeat, and no DEAD verdict after a dead-man fire.
- 6.9d Single-starter test (D4.1a/D5.12): kill -9 both follower and monitor while a PENDING run exists -> after exactly one dead-man fire exactly one follower and one monitor run, and the run reports no D5.12 contention error (no second-starter nonzero exit from the dead-man path).
- 6.10 Storm input collapses instead of emitting one wake per record: sustained > 20 events/min emits at most one wake per 60 s until the rate drops (finding 9).
- 6.11 Stall latency (finding 5): a hung-but-alive worker (unit active, no output-file bytes for N min) is surfaced as a lane-monitor `stall` inbox event; with a healthy follower it arrives within N + 10 min slack via the lane-monitor event path, and the test fails if it arrives via the dead-man path instead. Worst case, when the follower itself is dead, is N + the applicable dead-man interval (the stall event sits unseen until the dead-man re-arms the follower): N + 30 min with lanes running, N + 30 min in the dead-man-only fallback (30 min always, 5.10a). The test harness asserts both bounds: healthy-follower arrival within N + 10 min, follower-killed-first arrival within N + interval. Default N = 20.
- 6.12 Dead-man self-recreation test (measurable, not a 7-day wait): a replay harness fires the dead-man handler twice and asserts each firing issues delete + CronCreate and appends a recreation line to the wake log; the launch/clear checklist asserts the cron exists afterwards. A 7-day sleep is NOT required.
- 6.13 Interval-consistency check (finding 11): D4.1 is the only interval table in the spec and skill; a link/plan check fails if any other section or skill text states a flat interval without pointing at D4.1 (the 5.10a fallback's 30-min-always is allowed: it names D4.1 as superseded).
- 6.14 Idle-lifetime probe (finding 6) and harness-lifetime probe (finding 7) record PASS/FAIL per 5.10/5.10a before phase 1; 5.10 is a gate (FAIL stops the spec, returns to L1-routing). If the harness probe fails for both Bash and Monitor, phase 1 ships the dead-man-alone fallback at its honest budget (up to 48 turns/day in every state, idle included - 30 min always).

## 7. Risks and edge cases
- 7.1 Follower dies silently and events queue unseen until dead-man.
- 7.2 Mitigation is `RUN/state/<name>.follow` heartbeat mtime (touched every <= 60 s in both modes) plus the D4.1-cadence liveness check on mtime only.
- 7.3 Events arrive while session is mid-turn.
- 7.4 Mitigation is next re-arm starts from card offset, so nothing is lost.
- 7.5 Two followers run on one inbox.
- 7.6 Mitigation is `RUN/state/<name>.follow.lock` holding `{pid, start_time, session_id}` under the single D4.4 rule: one reader per inbox by design; a stale lock (pid gone/recycled OR heartbeat > 2 min stale) is taken over via SIGTERM-then-SIGKILL after start-time verification with a loud log line, a second starter against a fresh heartbeat exits nonzero with one loud stderr line, never a silent starve (D4.15a).
- 7.7 Clock skew produces out-of-order stamps.
- 7.8 Mitigation is byte-offset order (append order is the truth) plus report-late-once (D1.22, D2.6).
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
- 7.24 Mitigation is treat either as wake; the inbox is the only event source (D5.1) so a unit exit and its inbox `ready` both arrive as inbox lines and either wakes, with monitor (re)start reconciliation (D5.4) covering follower downtime; dedupe unit events by `lane@run-id@event-type` (D5.10), inbox events by card offset.

## 8. Open questions
- Q1 Does 8 h retirement count background-task wakes as activity?
- Q1 Resolution is the phase-0 probe in 5.10 (GATE for phase 1): the spec assumes each dead-man fire is a turn and therefore activity (D4.8); a FAIL stops the spec and returns to L1-routing with the measurement - phase 1 is not built.
- Q2 Should dead-man shorten while lanes run, e.g. 1 h?
- Q2 Decided: yes, 30 min while lanes run (D4.1 table); stall detection itself lives in the lane monitor (D5.4), not the dead-man - revisit N and the 30-min cadence with pilot stall data.
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
- 10.3 Follower state: `RUN/state/<name>.follow` (content `{pid, started, session_id}`, mtime is the heartbeat, touched every <= 60 s in both modes).
- 10.3a Follower lock: `RUN/state/<name>.follow.lock` (content `{pid, start_time, session_id}`; stale takeover logged, live contention exits nonzero with one stderr line).
- 10.3b Monitor heartbeat: `RUN/state/<name>.monitor` (content `{pid, started, session_id}`, mtime is the heartbeat, touched every <= 60 s); monitor lock: `RUN/state/<name>.monitor.lock` (same contention rule as 10.3a, per D5.12).
- 10.4 Wake log: `RUN/state/<name>.wakes`.
- 10.5 CLI: `autoos-agent.py inbox <name> --follow --actionable [--since-card CARD] [--exit-on-first] [--include-ci BRANCHES]` (no `--include-units` on the follower; that prefix switch lives on the lane monitor per D5.1).
- 10.5a Lane-monitor CLI/flags (next to the follower's 10.5): the monitor command owns the `--include-units PREFIX` prefix switch (plus opt-in `--include-ci BRANCHES`); it writes unit events as inbox lines per D5.1/D5.4, holds heartbeat `RUN/state/<name>.monitor` and lock `RUN/state/<name>.monitor.lock`, and is guarded by the dead-man per D4.1a/D4.2.
- 10.6 Exit code 0 on `--exit-on-first` event batch.
- 10.7 Nonzero with one stderr line on lock contention.
- 10.8 Stdout contract is one line per actionable event batch, nothing else.

## 11. Review round 3 (deepseek-flash cross-family, fix-first)
- All 12 findings resolved in the spec above; none rejected.
- 1 HIGH effects-before-cursor -> D2.4 (idempotent side effect keyed by record offset, then card write, then re-arm), D2.5 (restart re-delivers, never drops), D6.11; test 6.8a (SIGKILL between each pair).
- 2 HIGH follower heartbeat -> D4.3 (touch every <= 60 s both modes), D4.4 (mtime > 2 min stale, never content), 7.2; test 6.9.
- 3 HIGH missing/corrupt card -> D2.2 (own `→ done:` scan, else start of today; no last-30), D2.10; test 6.8c.
- 4 HIGH shared inbox lock -> D4.15a (pid + start time + session id; stale takeover logged; live second reader loud nonzero exit), 7.6, 10.3a; test 6.9a.
- 5 HIGH hung-but-alive stall path -> D5.4 (lane monitor watches `work/<lane>/<name>.out`, emits `stall` inbox event; dead-man guards only the follower), D4.15; test 6.11.
- 6 HIGH idle-session lifetime -> D4.8 (dead-man fire = a turn; intervals <= limit minus margin), 5.10 (phase-0 PASS/FAIL probe), 8 Q1; test 6.14.
- 7 HIGH harness lifetime -> 5.10a (dead-man-alone fallback: 30 min lanes-running, 3 h idle, honest budget); test 6.14.
- 8 MED byte offset -> D1.22, D2.6; test 6.8b.
- 9 MED storm backoff -> D3.7 (one wake per 60 s sustained after 20/min); test 6.10.
- 10 MED measurable 6.12 -> 6.12 (replay-harness recreation assertions, no 7-day wait).
- 11 MED interval contradiction -> D4.1 (single table), D4.15, D6.3, D6.13 pointing at it; check 6.13.
- 12 MED unit-exit id -> D5.10 (`lane@run-id` dedupe, not inbox offset), 7.24; test 6.8d.
- Rejected: none.

### Review round 4 (deepseek-flash cross-family, fix-first)
- All 7 findings resolved in the spec above; none rejected.
- 1 HIGH hung follower -> single D4.4 liveness/takeover rule (DEAD = pid gone/recycled OR heartbeat mtime > 2 min stale; takeover is verify-start-time then SIGTERM then SIGKILL with a loud TAKEOVER line; second starter against a FRESH heartbeat still exits nonzero), D4.6 and D4.15a reduced to pointers, 7.6; tests 6.9 and 6.9a assert the same rule from opposite legs and agree, plus SIGSTOP-the-follower -> replaced within 3 min of the staleness threshold.
- 2 HIGH D2.2 fallback -> D2.2 resumes from the START OF THE FILE with no card and no `→ done:` record (not start of today); test 6.8c inbox includes a pre-today unhandled order that must be delivered.
- 3 HIGH D5.10 typed dedupe -> key is `lane@run-id@event-type` (stall, exit, ready distinct), 7.24; test 6.8d asserts a stall followed by an exit for the same run-id emits two wakes.
- 4 HIGH D4.8 phase-0 gate -> phase 1 is NOT built until 5.10 passes; FAIL stops the spec and returns to L1-routing with the measurement (no hedged "re-decided"); phase-0 table exit, Q1, 6.14 updated to the gate wording.
- 5 MED spawn idempotency -> D2.12 (spawn title `lane@offset`, spawner refuses already-running/done duplicates within 24 h as no-op success); test 6.8a case (c) covers crash-after-spawn-before-card with exactly-one-worker assertion.
- 6 MED 5.10a fallback latency -> dead-man-only fallback runs every 30 min ALWAYS, idle included (no 3 h tier; supersedes D4.1), honest cost up to 48 turns/day in every state; 6.13 exempts it by name, 6.14 updated.
- 7 MED 6.11 stall bound -> healthy-follower bound stays N + 10 min via the lane-monitor event path; worst case with a dead follower is N + dead-man interval (N + 30 with lanes running, N + 30 in fallback); test asserts both bounds.
- Rejected: none.
- Open: none in this round - all seven findings carry their spec decision plus acceptance test above.

### Review round 5 (fix-first)
- All 3 findings resolved in the spec above; none rejected.
- 1 MED missing heartbeat file -> folded into D4.4 as DEAD case (c) (missing = stale); D4.5 reduced to a pointer to D4.4, not a second rule; test 6.9 adds the missing-file case (deleted heartbeat file reports DEAD and re-arms).
- 2 MED recycled-pid signal safety -> D4.4 states explicitly: if the start-time check FAILS (the pid was recycled), send NO signal to that pid; treat the lock as dead and take it over (replacement starts and the TAKEOVER line is logged without signalling the recycled pid); test 6.9a asserts a decoy process with the old pid but a different start time receives no signal and survives the takeover.
- 3 MED running-title refusal -> D2.12: a spawn whose same-title worker is still RUNNING is refused for as long as it runs (no time limit); the 24 h window applies only to a completed/done title; test 6.8a case (c) adds a worker still running after 24 h -> a restart spawns nothing (exactly one worker).
- Rejected: none.
- Open: none in this round - all three findings carry their spec decision plus acceptance test above.

### Review round 6 (fix-first)
- All 3 findings resolved in the spec above; none rejected.
- 1 HIGH lost unit-exit/ready during follower downtime -> D5.1 (inbox is the ONLY event source; lane monitor writes EVERY unit event - exit ok, exit failed, stall - as an inbox line keyed `lane@run-id@event-type`; follower never watches units directly; `--include-units` is the monitor's prefix switch), D5.4 (monitor (re)start reconciliation: compare every tracked unit's current state with the lines already written, write any missing ones), D5.10 (monitor-written lines plus `lane@run-id@event-type` dedupe), D1 table row (lane-monitor event line actionable), D1.4/D1.17/10.5 (follower takes no unit flag), 7.24; test 6.8e (stop follower, exit unit, restart -> exactly one wake).
- 2 MED duplicate id D1.17 -> byte-offset decision renumbered to D1.22 (lane-unit D1.17 keeps its id); citations updated in D2.6, 7.8, 6.8b, and the round-3 changelog line.
- 3 LOW D4.6 restatement -> reduced to a pure pointer to D4.4 (no (a)/(b) restatement).
- Rejected: none.
- Open: none in this round - all three findings carry their spec decision plus acceptance test above.

### Review round 7 (fix-first)
- All 3 findings resolved in the spec above; none rejected.
- 1 HIGH monitor downtime + systemd GC -> (a) D5.1: every lane unit gets an `ExecStopPost=` hook (runs on success and failure) that appends its own `lane@run-id@exit-ok|exit-failed` line to the owning session's inbox - the primary path; (b) D5.4: monitor reconciliation reads the DURABLE per-run records `logs/agents/<run-id>/job.json` (written at spawn, never GC'd), never live unit state - every run-id with no terminal line gets one (exit state from `job.json` / the unit if still present, else `exit-unknown`, which wakes); D5.10: dedupe key `lane@run-id@event-type` now covers `exit-ok` / `exit-failed` / `exit-unknown` as distinct types, so the hook line and the reconciliation backstop dedupe to one wake; D1 table row lists `exit-unknown` as actionable; test 6.8f (stop monitor, unit exits, `reset-failed`/GC, restart monitor -> exactly one wake from the hook line, deduped against reconciliation).
- 2 MED append atomicity -> D5.11: every writer (monitor, `ExecStopPost` hook, send tools) appends a whole line in ONE `O_APPEND` write under 4096 bytes; the follower consumes only up to the last `\n` and never advances its offset past a partial line; test 6.8g (writer paused mid-line -> follower neither wakes on nor skips the partial line).
- 3 LOW one monitor per session -> D5.12: the monitor holds `RUN/state/<name>.monitor.lock` under the same lock + DEAD rule as the follower (D4.4, pointer only); duplicate lines remain harmless through D5.10 dedupe.
- Rejected: none.
- Open: none in this round - all three findings carry their spec decision plus acceptance test above.

### Review round 8 (fix-first)
- All 3 findings resolved in the spec above; none rejected.
- 1 HIGH hook-failure backstop gap -> D5.4 states reconciliation writes nothing for a run that already has ANY terminal line (exit-ok, exit-failed, exit-unknown, or any other terminal type); 6.8e reworded so the hook is named as the path that delivers the exit (reconciliation is the backstop only); test 6.8h (hook FAILS via unset or read-only inbox path, monitor down, unit exits, reset-failed/GC, monitor restarts -> exactly one reconciliation-written terminal line from `job.json` or `exit-unknown`, exactly one wake).
- 2 MED concurrent-writer gap -> test 6.8i (two writers append concurrently, N lines each interleaved, one `O_APPEND` write per line -> every line parsed intact, none lost or merged; proves D5.11's one-write-per-line rule, not only 6.8g's partial-tail handling).
- 3 LOW monitor-lock variant -> 6.9a gains a monitor-lock variant (second monitor against a fresh `RUN/state/<name>.monitor.lock` exits nonzero; stale monitor lock taken over per D4.4).
- Rejected: none.
- Open: none in this round - all three findings carry their spec decision plus acceptance test above.

### Review round 9 (fix-first)
- 1 HIGH with two prerequisites unspecified, resolved in the spec above; none rejected.
- (a) Inbox resolution -> D5.1 (at spawn, the spawner writes the owning session's ABSOLUTE inbox path into job.json as `owner_inbox` and bakes it as a literal argument into the unit's `ExecStopPost=` command line - no env lookup at exit time, same user; scope: only spawner-launched units are lanes; a unit without job.json/`owner_inbox` is untracked by design; the ready-check refuses a lane whose run has no job.json); test 6.8j (spawn -> job.json has `owner_inbox`, unit's `ExecStopPost` carries that path).
- (b) Monitor lifecycle -> D4.1a (the D4.1 cadence guards BOTH follower and monitor), D4.2 (monitor heartbeat `RUN/state/<name>.monitor`, same <= 60 s cadence as D4.3, dead-man re-arms it on missing/stale per the single D4.4 rule - pointer only, one DEAD rule for both), D6.2/D6.3 (arm and re-arm both), 10.3b/10.5a (heartbeat/lock paths, monitor CLI flags); test 6.9b (kill -9 the monitor -> restarted by the next dead-man fire; a unit that exited meanwhile is delivered exactly once).
- 2 LOW D5.10 unit event types -> "ready" removed as a separate unit event type (unit events are exit-ok, exit-failed, exit-unknown, stall; "ready" is an inbox record from a person/session, deduped by its offset).
- 3 LOW D5.7 flapping -> replaced with a pointer to D5.10 (each run-id is its own event; a flap = two runs = two wakes, accepted).
- 4 LOW §10 monitor CLI -> 10.5a entry next to the follower's 10.5 (monitor owns the `--include-units` prefix switch), plus 10.3b heartbeat/lock paths.
- Rejected: none.
- Open: none in this round - all findings carry their spec decision plus acceptance test above.

### Review round 10 (fix-first)
- All 3 findings resolved in the spec above; none rejected.
- 1 flap dedupe -> D5.10: deleted "or flap" from the same-key-twice clause (a flap is two run-ids = two keys = two wakes, per D5.7); test 6.8k (a unit that flaps as a new run-id emits two wakes).
- 2 monitor only while lanes are tracked -> D4.2 (the monitor runs only while at least one tracked run exists - `job.json` with `owner_inbox` and no terminal line; it exits cleanly when the last one is terminal and removes its heartbeat and lock on a clean exit; the D4.4 DEAD rule applies to the monitor only while tracked runs exist, so with none a missing monitor heartbeat is not DEAD), D6.2/D6.3 updated accordingly; test 6.9c (idle with no tracked runs -> the dead-man does not start a monitor).
- 3 single starter -> D4.1a (the ONLY code that starts the monitor is the follower's arm step - "ensure monitor": start it iff tracked runs exist and its lock is not fresh, idempotently; the dead-man never starts the monitor directly - when either heartbeat is DEAD it re-arms the follower after the D4.4 takeover of any dead holder, and the follower's arm then ensures the monitor), D6.2/D6.3 and 6.9b updated accordingly; test 6.9d (kill -9 both -> after one dead-man fire exactly one follower and one monitor run, and no D5.12 contention error).
- Rejected: none.
- Open: none in this round - all three findings carry their spec decision plus acceptance test above.

### Review round 11 (fix-first)
- 1 HIGH healthy-follower start gap, resolved in the spec above; none rejected.
- EDGE: the monitor stopped cleanly (no tracked runs) and the follower is healthy; then a new spawn makes a run tracked, and nothing starts the monitor (the dead-man only re-arms a DEAD follower). The same gap exists when the monitor is killed while the follower is healthy (6.9b).
- DECISION: "single starter" becomes "single start routine". ensure_monitor() (idempotent: start iff tracked runs exist and the monitor lock is not fresh; the D5.12 lock prevents duplicates) is the ONLY code that starts a monitor, and it is called from exactly three places: (1) the follower's arm step, (2) the follower's own heartbeat tick (every <= 60 s, D4.3), (3) the spawner, right after it writes job.json with owner_inbox. The dead-man still never starts the monitor directly. Updated D4.1a, D4.2, D5.1, D6.2 and 6.9b (restart within one follower tick, <= 60 s, with the follower healthy).
- Tests: 6.9e (idle with monitor stopped cleanly, follower healthy, spawn a new run -> exactly one monitor within 60 s; that unit's ExecStopPost hook is made to fail, and the exit is still delivered exactly once via reconciliation), 6.9f (three concurrent ensure_monitor callers -> exactly one monitor, no contention error surfaced).
- Rejected: none.
- Open: none in this round - the finding carries its spec decision plus acceptance tests above.

### Review round 12 (fix-first)
- All 4 findings resolved in the spec above; none rejected.
- 1 HIGH premature reconciliation -> the spawner calls ensure_monitor() only AFTER the unit has started (systemd-run returned success) and it has written started_at into job.json; a tracked run = job.json with owner_inbox AND started_at and no terminal line; reconciliation never writes exit-unknown for a run without started_at younger than 2 min, older than that it writes exit-unknown (the spawn is suspect, and it wakes). Updated D4.2 (tracked-run definition), D5.1 (spawner order: owner_inbox -> start unit -> started_at -> ensure_monitor), D5.4 (started_at guard + 2-min rule); test 6.8l (job.json written, unit not yet started -> no exit-unknown line).
- 2 MED quiet loser vs D5.12 -> ensure_monitor() checks the lock first: fresh lock returns 0 silently (no start); a monitor process it did start that loses the lock race exits nonzero, treated as success and not surfaced. The D5.12 loud nonzero exit applies only to a monitor started directly (outside ensure_monitor()). Updated D5.12 and 6.9a; 6.9f stays (three concurrent ensure_monitor callers -> exactly one monitor, no contention error surfaced).
- 3 MED dead-man vs healthy follower -> the dead-man re-arms the follower ONLY when the FOLLOWER is DEAD; a dead monitor with a healthy follower is left to the follower's next tick (<= 60 s). Fixed D4.1a ("either heartbeat DEAD" -> "follower DEAD") and D6.3 (same + explicit healthy-follower tick sentence), consistent with D4.2.
- 4 LOW 6.9e tighten -> 6.9e runs with the follower tick disabled (or its interval set above the test window) and asserts the monitor starts within 5 s of the spawn, so the test fails if the spawner path is broken; the tick path keeps its separate assertion in 6.9b.
- Rejected: none.
- Open: none in this round - all four findings carry their spec decision plus acceptance test above.

### Review round 13 (fix-first)
- 1 HIGH pending-vs-started contradiction, resolved in the spec above; none rejected.
- CONTRADICTION: D5.4 required exit-unknown for a run whose job.json is older than 2 min without started_at, but D4.2 counted only runs WITH started_at as tracked, so ensure_monitor() never started a monitor for it and the wake was never written.
- DECISION: split the two notions. (a) PENDING run = job.json with owner_inbox and no terminal line (started_at optional); ensure_monitor() starts the monitor iff at least one PENDING run exists, and the monitor stays up while any PENDING run exists (D4.2). (b) The 2-min grace applies only to reconciliation's exit-unknown: a pending run without started_at gets exit-unknown only when its job.json is older than 2 min (D5.4). (c) The spawner writes exit-failed itself, synchronously, when systemd-run fails (the common cause); the grace path covers a spawner that crashed between the job.json write and started_at (D5.1).
- Updated D4.2 (PENDING definition, monitor lifetime, DEAD-rule scope), D5.1 (spawner synchronous exit-failed on systemd-run failure, crashed-spawner pointer to D5.4), D5.4 (PENDING reconciliation scope, grace-only-for-exit-unknown, spawner-failure pointer to D5.1), and the 6.9c parenthetical ("PENDING", not "tracked" with started_at).
- Tests: 6.8l extended with the OLDER case (job.json 3 min old, no started_at, no terminal line, follower healthy, monitor stopped -> the next ensure_monitor starts a monitor, and exactly one exit-unknown line and one wake follow); new 6.8m (systemd-run fails -> the spawner writes exit-failed, exactly one wake, no later exit-unknown).
- Rejected: none.
- Open: none in this round - the finding carries its spec decision plus acceptance tests above.
