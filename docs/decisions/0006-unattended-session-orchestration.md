# 0006. Unattended session orchestration as a config-driven runner

- **Status:** Accepted (2026-09-04)

## Context

`sibling-analysis-repo` ran its wave-3 work overnight with
`scripts/management/run_handoff_sessions.ps1`: headless `claude --bg` sessions, one
git worktree each, in parallel lanes, recovered automatically across usage limits and
API outages, guard-tested and merged. It worked, and the expensive part was not the
orchestration idea — it was the several nights of measured failures encoded in it:

- `--allowedTools` is variadic and swallows a prompt placed after it, so the session
  starts with **no task**;
- a fresh worktree is an unapproved folder, and a background session cannot answer the
  trust prompt — it goes `blocked` and stays there;
- an expired OAuth session fails every launch instantly, so it must stop the run rather
  than be retried;
- a usage limit reports its reset epoch, so waiting blind wastes hours;
- a `$script:` assignment inside a mutex scriptblock writes a *different* variable, so
  every successful merge reported a conflict;
- `-WaitUntil 2026-09-04 09:45` arrives as two arguments and `ParseExact` throws under
  `ErrorActionPreference Stop`, silently cancelling a scheduled run.

That knowledge was trapped in one repository, hardcoded to its session table, its venv
path, its pytest node ids, its `refactor` branch and its plan directory. Any other
repository wanting overnight runs would have rediscovered the same six failures.

Three orchestration skills already exist here, and a fourth needs justification:

| | `swarm-orchestration` | `herdr-orchestration` | this |
|---|---|---|---|
| Lifetime | within one session | outlives the session | outlives the session |
| Mechanism | `Agent` / `Workflow` tools | Herdr socket API | `claude --bg` + git worktrees |
| Human present | yes | yes, supervising | **no** |
| Survives a usage limit | n/a | no | **yes — the core feature** |
| Isolation | none | pane | **worktree + branch** |
| Integration | n/a | n/a | **guard tests + auto-merge** |

## Decision

Adopt the runner into `skills/unattended-orchestration/`, **split in two** and made
config-driven:

1. **`HandoffCore.psm1`** — everything that is a pure function of its arguments:
   failure classification, config load and validation, template expansion, state I/O,
   guard-command construction. No git, no `claude`, no clock beyond the caller's.
2. **`run_handoff_sessions.ps1`** — only what genuinely touches git, `claude` or time.
3. **`handoff.config.json`** — every repository-specific fact: sessions, models,
   briefs, lanes, guards, branch names, worktree locations, tool allowlist.

The split is what makes the recovery logic testable. Previously the only way to
exercise "what happens on a usage limit" was to hit one at 03:00; now it is an
assertion. Guards are **opt-in and fully generic** (any command, not pytest), because
the hardcoded pytest invocation and venv path were precisely the parts no other
repository could reuse.

The dividing line from `herdr-orchestration` is **who recovers a stopped session**:
there, a human looking at a pane; here, nobody until morning. That is why every stop
must be classified automatically or recorded precisely enough to be read cold.

## Consequences

- Any repository gets overnight runs by copying an annotated config — the six failures
  above are already handled, not rediscovered.
- Two test suites are the contract. The unit suite covers the pure core; a **smoke
  suite invokes the script itself**, which is not optional: both bugs found on the
  first real invocation lived in the driver and were invisible to unit tests. Both were
  the same PowerShell trap — the output stream unrolls one array level, so a nested
  lane array collapsed and every *sequential* lane silently ran in *parallel*, which is
  exactly the contention lanes exist to prevent.
- PowerShell-only, and Windows-only where junctions are used. Acceptable: the hosts
  that run overnight batches are the Windows workstation and `coding.example.internal`. A POSIX port
  would need `ln -s` and a different mutex.
- The tool allowlist must track how MCP servers are wired — a user-scope server is
  `mcp__<name>__*`, the same server as a plugin is `mcp__plugin_<plugin>_<server>__*`.
  A stale prefix silently removes a tool from every unattended session. See
  [ADR-adjacent measurements](../../skills/mcp-servers-setup/SKILL.md) → Playwright.
- `sibling-analysis-repo` keeps its own copy for now; converging it onto this runner is
  follow-up work, not part of this change.
