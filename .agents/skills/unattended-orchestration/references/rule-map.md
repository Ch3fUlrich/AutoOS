# Rule map — old ids → new ids or code pointers

Every old `R-<topic>-NN` cited in code, tests or docs resolves here.
`tests/test_skill_rules.py::RuleResolutionTests` asserts that every cited old id has a row,
and every new id target exists in `SKILL.md`.

| old | new / code | notes |
|---|---|---|
| `R-spawn-01` | partial: `l1_handoff.py` stale() lists the stop/remove commands | listing only, the orchestrator runs them (not enforced) |
| `R-spawn-02` | code: MCP `spawn` uses `start_new_session=True` + `_reap()` | process-group isolation |
| `R-spawn-03` | code: `autoos_clients.py build_command` argv-order check | --mcp-config variadic before prompt |
| `R-spawn-04` | code: `trust_worktree.py --lane-mcp` strict per-worktree config | lane MCP isolation |
| `R-spawn-05` | R-orch-12 | not enforced in code (cao/worktree.py only prints the trust_worktree.py line) |
| `R-spawn-06` | R-orch-04 | not enforced in code (no brief validator exists) |
| `R-spawn-07` | code: `run`/`spawn` refuses shell tasks under isolation:worktree | isolation enforcement |
| `R-spawn-08` | code: `client_key`/`key_files` fall back to main checkout | api-keys.yml fallback |
| `R-spawn-09` | `R-orch-13` | absorbed: cross-family routing |
| `R-spawn-10` | `R-coord-01` | absorbed: cut lanes from main |
| `R-spawn-11` | `R-coord-08` | absorbed: re-read inbox before launch |
| `R-spawn-12` | code: `run --lean` — honours opencode / claude / qoder (`LEAN_CLIENTS`); another client notes and continues a read-only run, exit 2 on a writer one (`lean_decision`) | enforced in `tools/autoos-agent.py` |
| `R-spawn-13` | `R-orch-06` | absorbed: relaunch, never resume |
| `R-spawn-14` | `R-coord-01` | absorbed: cut lanes from main |
| `R-spawn-15` | `R-orch-06` | absorbed: verify worktree |
| `R-spawn-16` | code: `cancel` verifies pid gone, then SIGKILL | process reap |
| `R-spawn-17` | `R-orch-08` | absorbed: commit under lane identity |
| `R-spawn-18` | code: MCP `spawn` backgrounds via Popen (never nohup/setsid) | already enforced |
| `R-spawn-19` | `R-coord-01` | absorbed: push before dispatch |
| `R-spawn-20` | `R-orch-06` | absorbed: verify WIP scope |
| `R-spawn-21` | code: `--isolate` parent-checkout writes exit 7 (LEAK) | enforced, but false-positives on a moved parent — lanes.md "frozen parent" |
| `R-spawn-22` | `R-orch-04` | absorbed: feed isolated workers inline |
| `R-spawn-23` | `R-orch-13` | absorbed: cross-family routing |
| `R-spawn-24` | `R-coord-03` | absorbed: Claude orchestrates only |
| `R-spawn-25` | `R-orch-06` | absorbed: never delete predecessor leftovers |
| `R-review-01` | code: sandbox NO-OP exits 5 | already enforced |
| `R-review-02` | `R-coord-02` | absorbed: verify cheap done |
| `R-review-03` | `R-orch-13` | absorbed: cross-family reviewers |
| `R-review-04` | `R-orch-04` | absorbed: feed isolated workers |
| `R-review-05` | `R-orch-13` | absorbed: pin review model |
| `R-review-06` | `R-orch-04` | absorbed: copy inputs into clone |
| `R-review-07` | `R-orch-13` | absorbed: demand files-read evidence |
| `R-review-08` | `R-orch-13` | absorbed: never accept t3-driver review |
| `R-review-09` | `R-coord-02` | absorbed: consumer must accept config |
| `R-review-10` | `R-orch-13` | absorbed: diff reused symbols |
| `R-tests-01` | references/runner-setup.md: `-Validate` then `-DryRun` | runner-specific procedure |
| `R-tests-02` | `R-worker-03` | absorbed: never full suite in moving tree |
| `R-tests-03` | `R-worker-05` | absorbed: pipefail + N passed gate |
| `R-tests-04` | `R-worker-03` | absorbed: filter tests to touched area |
| `R-tests-05` | `R-worker-03` | absorbed: full suites once per phase |
| `R-tests-06` | `R-worker-03` | absorbed: specific filters |
| `R-tests-07` | `R-worker-02` | absorbed: assert the reason |
| `R-tests-08` | `R-worker-03` | absorbed: snippet-lint on OOM |
| `R-tests-09` | `R-worker-02` | absorbed: guard platform |
| `R-tests-10` | `R-worker-02` | absorbed: skip without CLI |
| `R-tests-11` | dropped: process gap (unwired suite), not a repeatable rule | one-off |
| `R-tests-12` | `R-worker-03` | absorbed: real --no-cache builds |
| `R-tests-13` | `R-worker-02` | absorbed: seed bug state, assert reason |
| `R-tests-14` | R-worker-08 | |
| `R-tests-15` | `R-worker-02` | absorbed: guard platform (SUDO_USER) |
| `R-tests-16` | `R-coord-02` | absorbed: verify yourself before merge |
| `R-tests-17` | `R-worker-03` | absorbed: include helpers in touched area |
| `R-tests-18` | code: lint enforces `*_argv` array naming (SC2178) | shellcheck rule |
| `R-tests-19` | code: lint enforces 5.1-safe `@(...ConvertFrom-Json...)` wrap | PS 5.1 compat |
| `R-tests-20` | `R-worker-03` | absorbed: specific filters |
| `R-tests-21` | `R-worker-02` | absorbed: guard platform (host state) |
| `R-tests-22` | code: lint flags `${v/pat/repl}` with `&` in repl under bash >=5.2 | bash compat |
| `R-tests-23` | `R-worker-03` | absorbed: conflicts resolved first |
| `R-gateway-01` | code: `autoos_resolver.py` enforces bucket leg order | already enforced |
| `R-gateway-02` | code: resolver `effort`/`max_tokens` sizes reviewer output budget | already enforced |
| `R-gateway-03` | code: `--free` is opencode-only (refuses other clients) | already enforced |
| `R-gateway-04` | docs: `configuration/omniroute/apply.sh --drift` (run after every apply) | `route` does not check live drift |
| `R-gateway-05` | code: preflight checks tool-allowlist prefix matches MCP wiring | prefix validation |
| `R-gateway-06` | code: resolver caps Qwen free-only requests (~7000 input tokens) | TPM guard |
| `R-gateway-07` | code: `heartbeat` surfaces probe-proposals.jsonl lines | heartbeat report |
| `R-gateway-08` | code: `probe_stale` 7-day re-probe | already enforced |
| `R-gateway-09` | dropped: one-off false positive, fixed in measure | not repeatable |
| `R-gateway-10` | code: codex path always passes `-m <model>` | client argv |
| `R-gateway-11` | `R-router-02` | absorbed: diagnose before blame |
| `R-gateway-12` | code: `unavailable_until` (registry.py, autoos_resolver.py) | already enforced |
| `R-gateway-13` | code: resolver `provider_tpm`/`usable_context` per-minute caps | already enforced |
| `R-gateway-14` | `R-orch-13` | absorbed: cross-family routing |
| `R-gateway-15` | code: resolver refuses Groq+DeepSeek multi-turn combos | combo guard |
| `R-gateway-16` | `R-coord-02` | absorbed: verify cheap done |
| `R-gateway-17` | code: docker OmniRoute via apply.sh omni wrapper | management path |
| `R-cost-01` | `R-coord-03` | absorbed: Claude orchestrates only |
| `R-cost-02` | `R-coord-03` | absorbed: no Claude subagent to implement |
| `R-cost-03` | `R-coord-03` | absorbed: spawn by bucket table |
| `R-cost-04` | `R-coord-03` | absorbed: cheap-first lane order |
| `R-cost-05` | `R-coord-02` | absorbed: verify cheap done |
| `R-cost-06` | `R-coord-04` | absorbed: host headroom |
| `R-cost-07` | code: OpenRouter 402 marks leg down (`unavailable_now`) | resolver guard |
| `R-brief-01` | `R-orch-02` | absorbed: one file, exact spec |
| `R-brief-02` | `R-orch-01` | absorbed: fixed BRIEF/REPORT fields |
| `R-brief-03` | `R-orch-01` | absorbed: evidence by pointer |
| `R-brief-04` | `R-orch-01` | absorbed: name this skill |
| `R-brief-05` | `R-worker-01` | absorbed: derive from registry fields |
| `R-brief-06` | `R-worker-01` | absorbed: --check only |
| `R-brief-07` | `R-orch-02` | absorbed: file:line anchors |
| `R-brief-08` | `R-orch-02` | absorbed: research on t1 |
| `R-level-01` | `R-router-01` | absorbed: background sessions never ask |
| `R-level-02` | `R-router-01` | absorbed: L0 routes, L2 researches |
| `R-heartbeat-01` | `R-coord-07` | absorbed: the session keeps one recurring 10-min heartbeat cron |
| `R-heartbeat-02` | `R-coord-08` | absorbed: beat pushes and rewrites status; `autoos-agent.py heartbeat` is the read-only report |
| `R-heartbeat-03` | code: `autoos-agent.py heartbeat` exit 3 pause / 4 over-cap | already enforced |
| `R-pause-01` | code: `heartbeat`+`run`+MCP `spawn` all refuse on PAUSE (exit 3) | already enforced |
| `R-git-01` | `R-orch-08` | absorbed: harness trailer wins |
| `R-git-02` | `R-orch-08` | absorbed: lane identity |
| `R-merge-01` | `R-coord-01` | absorbed: guards gate merge |
| `R-merge-02` | `R-coord-01` | absorbed: merged-elsewhere skip |
| `R-merge-03` | `R-coord-01` | absorbed: no-ff under mutex |
| `R-merge-04` | `R-coord-01` | absorbed: two-stage merge |
| `R-merge-05` | `R-coord-01` | absorbed: ready line to coordinator |
| `R-merge-06` | `R-coord-01` | absorbed: only coordinator on main |
| `R-handoff-01` | `R-coord-06` | absorbed: rewrite state every wave |
| `R-handoff-02` | `R-coord-06` | absorbed: delegate reading |
| `R-handoff-03` | `R-coord-06` | absorbed: brief from DONE note |
| `R-handoff-04` | `R-coord-06` | absorbed: run l1_handoff.py |
| `R-handoff-05` | `R-coord-06` | absorbed: parent inbox line |
| `R-handoff-06` | `R-router-01` | absorbed: never ask operator directly |
| `R-handoff-07` | code: `heartbeat --transcript --cap` measures child, exit 4 relaunches | already enforced |
| `R-handoff-08` | `R-orch-01` | absorbed: one writer per file |
| `R-handoff-09` | `R-coord-08` | absorbed: answer ping with pong |
| `R-handoff-10` | `R-coord-06` | absorbed: state file template |
| `R-handoff-11` | `R-coord-06` | absorbed: relaunch re-measures |
| `R-host-01` | `R-coord-04` | absorbed: Serena ~200 MB budget |
| `R-host-02` | `R-coord-04` | absorbed: Omnigraph ~7 MB each |
| `R-host-03` | `R-router-02` | absorbed: diagnose before blame |
| `R-host-04` | dropped: environment one-off the runner cannot act on | not agent-actionable |
| `R-host-05` | `R-coord-04` | absorbed: <=3 lanes + 3 readers |
| `R-host-06` | code: lint flags `path` vars and bare multi-step shell | zsh safety |
| `R-host-07` | code: liveness via sessions-json procStart vs /proc | heartbeat measure |
| `R-host-08` | R-worker-07 | unfiltered run-tests.sh is refused in code (AUTOOS_FULL_SUITE, FULLGUARD) |
| `R-host-09` | `R-coord-04` | absorbed: cap heavy runs |
| `R-host-10` | R-worker-09 | no code guard yet |
| `R-safety-01` | code: preflight refuses secrets in committed handoff configs | secret guard |
| `R-safety-02` | `R-orch-08` | absorbed: record refusals verbatim |
| `R-safety-03` | `R-worker-06` | absorbed: leaf never spawns |
| `R-safety-04` | code: `private_safe` + resolver filter on every leg | already enforced |
| `R-safety-05` | code: trust-boundary reader catches Exception, 0700 leaf dirs | security hardening |

## Retired second-generation ids (fold 2026-09-27)

Five first-draft rules restated another rule's fact and were folded instead of kept side by
side (one home per fact). A citation of any of them resolves to its surviving rule.

| retired | now | absorbed |
|---|---|---|
| `R-orch-03` | `R-router-01` | only L0 asks; everyone else writes `question:` to the inbox |
| `R-orch-05` | `R-coord-01` | lane cuts come from main; the merge path is one rule |
| `R-orch-07` | `R-orch-13` | cross-family review, reviewer model pinned, evidence by pointer |
| `R-orch-09` | `R-coord-08` | read the inbox at the heartbeat and before every launch |
| `R-coord-05` | `R-worker-03` | filtered tests while working, full suites once per phase |
