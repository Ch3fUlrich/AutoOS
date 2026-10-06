# OpenCode fleet lessons (2026-10-04)

Everything below was measured on one day of running OpenCode sessions (L0 router, L1 lane orchestrator, L3 writers and seats) with no Claude in the loop.
Read it before you start, restart or supervise an OpenCode session. It extends `unattended-orchestration.md` and `references/layers.md`.

Where the tools are:
- Launcher: `tools/oc_l1.py` (`render`, `start`, `status`), docs in `docs/ai/opencode-fleet.md` section "oc_l1.py launcher".
- Spawner (writers, seats, researchers): `tools/autoos-agent.py` and the `autoos-agent` MCP tool, see `unattended-orchestration.md` "Spawning".
- Seats: `tools/review-call.py` (single tool-less call, `evidence.json`, quotes checked by you with `grep -F`).
- Cost gate: `tools/cost-gate-status.py`, `docs/ai/cost-guard.md`. Bash guard: `docs/ai/fleet-hooks.md`.
- Handoff cards for a session that has no memory: `docs/handoff/OPENCODE-L1-AUTOOS.md`, `docs/handoff/OPENCODE-L1-WORKST.md`.
- Other skills: `coding-principles`, `swarm-orchestration`, `qa-swarm`, `review-triage`, `repository-index`.

## 1. A session only acts when it is prompted
An opencode session is turn based. When the model ends its turn, the session goes idle and nothing wakes it. A weak model ends turns early.
- Run a watcher outside the session: no assistant message for 4 minutes means post a "continue" prompt (`POST /api/session/<id>/prompt`). Max one per 5 minutes. Never on a failed session.
- A session that is dead (server gone or outcome `failed`) is restarted from its card by the same watcher.
- A tool that is `running` for more than 5 minutes blocks every later prompt. Look at its input first.

## 2. Hangs are almost always an approval prompt nobody can answer
- File tools (read, edit, glob, grep) outside the session's working directory ask for approval and wait forever. Keep card, mailboxes and every file the session edits INSIDE its clone. Anything outside: bash only.
- Never glob or grep over a large tree (`/home/s`): one such call hung a session for 24 minutes.
- MCP calls hung because a `git` child inherited the stdio pipe: `AUTOOS_WORKERS_DIR` must be pinned for the MCP server (the launcher does it), and every read-only `git` call on an MCP path needs `stdin=subprocess.DEVNULL`.

## 3. Context is paid on every turn
- OVH models give no prompt caching (no `cached_tokens`, cache-read 0). Every turn resends the whole context. A 66k-token context for 213 turns cost about 10 USD on a 0.7 USD/M model.
- Vertex and AI Studio Gemini cache (about 93 percent cache-read). Orchestrators (long sessions) run on Gemini, L3 work (short sessions) on OVH.
- Keep a session small: do not preload big files (AGENTS.md is read by section with `grep -n '^## '` then `sed -n`), read in ranges, pipe test output through `tail -30`, one step = one small goal.
- Rotate by message count (about 120 assistant messages), never while a spawned run is running: stopping a session stops its process group and kills the runs it spawned.

## 4. Weak models overclaim; the review is the gate
- A record said "complete, seats approved". My probes showed the goal was not met; the seat "quotes" were the commit message. Run the card's behaviours as probes yourself, `grep -F` every quote in the tree, check `git cat-file -t <sha>` and the diff stat.
- A passing unit test is not the goal. A test that only passes through old logic proves nothing: ask for the card's four behaviours as real probes and a named mutation test.
- An L1 must not edit code itself. It writes a brief and spawns a writer. Cap each step to 3 files and 3 verifier rounds, then move the writer up one tier.
- One non-writer family seat is enough for normal risk, two families for HIGH. Two seats from the same model are one family.

## 5. Make rules mechanical, not trust
- No Google-paid leg for a lane: give that session a gate file with verdict `block` (`AUTOOS_DAILY_GATE_FILE`); the spawner then refuses every Google-paid start. Refresh the file before its 2 hour max age.
- Per-run input cap: stop a spawned run above 850k input tokens (the watcher polls the gateway rows every 30 s; a long single turn can overshoot, so stop at 850k not 1M).
- The canary: a known-bad shell call must be DENIED before the session gets its first prompt, and again after every restart. "No shell tool call" in the canary is a model flake: retry up to 3 times, then check the model id.
- A model id is a gateway id (`vertex/gemini-3.8-flash`), not the opencode key (`vertex-gemini-3.8-flash`). A wrong id returns 400 and looks like a canary flake.
- Central Vertex returns 400 "Requests ending with a model turn are not supported" (trailing-turn bug) until the OmniRoute patch is applied there: use the AI Studio leg for orchestrators on that host.

## 6. Money and exposure facts
- The cost gate is per host (coding.vm block 10, workstation block 15 USD). It counts only Google-paid rows. A gate that under-counts must report `UNAVAILABLE`, never a low `ok` (culture bug on a de-DE host: one page exported).
- Billing: OVH bills per project and issues the invoice on the first day of the next month; the credit display does not fall live.
- A published container port is not private because of a password. Keep the compose publish decision, the firewall rule and a test together.

## 7. Process lessons
- Record, then ACCEPT, then push, then watch CI to the end. Your own green run is not an acceptance. Red CI is the only job until it is green.
- Compare failure SETS against main when your container is not faithful to CI (root, no network namespace); one known test hangs in that container (part 34).
- Never kill by process name. Kill the recorded pid, or stop the recorded systemd unit.
- Ask the operator only for secrets, permission settings, deletes, public publishing, money, host-level changes (reboot, WSL restart, firewall/exposure). Everything else: decide, log, go.

## 8. Added 2026-10-05

- **Verify a throttle with connections, not env vars.** The project `opencode.jsonc` of an AutoOS clone pins the gateway address and overrides the scratch config: an env var for the proxy URL was silently ignored and the sessions ran unthrottled (about 22 calls a minute, 8 USD/h). Force the address with the `OPENCODE_CONFIG_CONTENT` overlay (`provider.<id>.options.baseURL`) and check `ss -tnp state established '( dport = :20128 )'`.
- **A weak orchestrator re-runs every old order.** It re-reads the whole inbox after each restart. Keep ONE current task file (the watcher archives the rest) and name it in the restart note.
- **It resets its own clone.** Keep work on `lane/<name>` branches and install a git `reference-transaction` hook that refuses a backward move; git passes an all-zero old value for `update-ref`, so the hook must read the ref itself. Never test such a hook on a live clone.
- **It edits things it should not.** Two real bad edits: a global monkey-patch of `subprocess.run` (infinite recursion, no spawn worked) and a one-line change that would have disabled the cost gate. Review every uncommitted hunk; a pure change can be proven with an AST compare after removing the intended keyword.
- **Rotation costs progress.** A rotation limit below the orientation cost of a restart means no work gets done; and stopping a session kills its spawned runs (process group): rotate only when no run is active. Restarting the watcher service must not kill the sessions: `KillMode=process`.
- **Cost gates must cover the orchestrator's own turns.** The spawner gate only refuses Google-paid spawns. Let the watcher read the host gate and switch the lane model at block (then back at the UTC roll).
- **A session with no state file is not restarted by a watcher that only looks at state files**: start a lane that has no state.
- Handoff cards for a session without memory: `docs/handoff/OPENCODE-L1-AUTOOS.md` (section 0 = today's changes, exact restart commands) and `docs/handoff/OPENCODE-L1-WORKST.md`; runtime sources: `docs/ai/oc-runtime-sources/`.

## 9. Added 2026-10-06

- **A "blocked: repository is read-only" report from a weak orchestrator is usually wrong.** The edit deny outside `.oc-pilot/` is by design; the writer does the edits. Before believing a block, check whether the spawn tool is actually absent (the server log's `mcp connected` line) and whether the current task file is the one being executed. The L1 spent ~1 h reporting a block that did not exist while its own MCP server was connected with 11 tools.
- **Mailbox writes need a pointer fix-up when the pointer outlives its file.** The watcher writes `inbox/progress.json` naming the current task file; a restart can archive that file and leave the pointer dangling. When injecting an order by hand, also rewrite the pointer, and never reuse an archived message number (`msg_021` had already been used and archived).
- **A midnight model-fallback flap can stick a lane on the paid fallback all day.** Restore at the UTC roll, then re-fallback from a stale-but-fresh block verdict 1 min later, and the restore condition (`flag != today`) never fires again until the NEXT roll. The fallback must exist only while the gate is at block: restore when `flag != today OR verdict != block` (fixed in watch2.py, measured 2026-10-06: both lanes were on ovh while the fresh gate read ok $0.28/$14).
- **A restart must never kill a spawned run's process group, and a model switch is a restart.** The gate-fallback correctly deferred while `c0-keepalive3` was running (D-614). Keep that ordering in every new watcher branch.
- **opencode prunes old tool outputs only on opt-in** (`compaction.prune` defaults to false) and truncates tool output at 2000 lines / 50 KB by default - the defaults under which an orchestrator's per-turn input reached 794k tokens and ~6M cumulative input with zero commits. The lane renderer now pins `compaction {auto, prune, tail_turns}` and `tool_output {max_lines, max_bytes}`, and the lane model's limit follows the model (`model.limit`), not a hardcoded 128k.
- **A whole-gateway 503 with `resourcePressure` means the container's memory guard, not the upstream.** Check `docker stats` for the cgroup ratio; a retry loop from any client keeps the heap pinned. Official restart (`docker restart autoos-omniroute`) clears it in seconds; find and stop the looping client.
- **ZCode on the host can serve glm-5.3-flash directly** (app-server over stdio is live and answers a JSON-RPC handshake; the headless `-p` one-shot fails at model_creation on 0.16.9). The dockerized gateway can never spawn it (no binary, no auth inside the container) - wire the client in `tools/autoos_clients.py` instead, so spawned glm agents bypass opencode entirely.
