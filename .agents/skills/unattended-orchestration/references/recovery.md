# Recovery — classifying a stopped turn

Moved out of `SKILL.md` (routing v2 spec §8.1, C2). `SKILL.md` links here; nothing in this file
is restated there.

Every turn that ends is classified from the session log, and the kind decides the response:

| Kind | Response | Why |
|---|---|---|
| `auth` | **stop the lane immediately** | An expired login fails every launch instantly. Retrying burns the whole night for nothing. |
| `limit` | sleep until the reset epoch Claude reports, else a flat wait, then **resume the same session** | The message carries the exact reset time; waiting blind wastes hours. |
| `transient` | short wait, then resume | 500/529/network are self-clearing. |
| `other` + no DONE note | resume with a nudge, up to the continue cap | A session that stopped early has committed work; resuming continues from it. |
| operator's `stop-<key>` marker in `<stateDir>/queue` | **stop the lane at its next decision point** — with a DONE note present, go to the guards; without one, stop | A finished lane used to be killable only with `Stop-Process` on its poller. |
| branch already an ancestor of the base branch (merged by hand or by another runner) | mark `merged`, `finished_by: merged-elsewhere`, skip | Two runners over one state file, or an orchestrator merging by hand, must not re-run a session whose work is already in. |
| DONE note present, branch clean and quiet for `quietMinutes` | **finished** — stop the session, run the guards, merge — even while the agents view still says "working" | Measured twice in one day: a finished session reported `working` for three hours, hit the session-hours cap, and was stopped and *resumed* — a fresh session that read the brief, found the work done, and idled. The evidence of completion is the DONE note, not the turn. |
| permission prompt | **`blocked`**, lane stops | A background session cannot answer. It would sit there until morning. |
| no DONE note, and log, worktree and process tree all quiet for `stallMinutes` | **`stalled`** — end the child and resume it, exactly as an early stop | A hung child is invisible to `quietMinutes`, which only ever closes a session that already finished. |
| `stallWindow.maxStalls` stalls inside `stallWindow.hours` | **`stalled-repeatedly`**, lane stops | Stalls that frequent are a general problem; a sixth resume only spends another hour proving it. |

The exact patterns and waits live in `Classify-HandoffFailure`; they are covered by
[`tests/HandoffCore.Tests.ps1`](../tests/HandoffCore.Tests.ps1), because the alternative way to
test them is to actually hit a usage limit at 03:00.

**Every provider words exhaustion differently, and a wording the classifier does not know is not
a small miss — it is a wall resumed as an early stop.** Claude Code says `usage limit
reached|<epoch>`, and the epoch is preferred over every other reading because it is exact.
Antigravity (`agy`) says `Individual quota reached ... Resets in 98h52m33s` in its JSON `error`
field and `RESOURCE_EXHAUSTED (code 429): Individual quota reached` on a retried API call — so
`quota reached|quota exceeded|resource_exhausted` also means `limit`, with `Resets in
(\d+)h(\d+)m(\d+)s` parsed into the wait and clamped to the same six-hour ceiling as the epoch
branch, falling back to the flat 30 minutes when the phrase carries no reset time. Added
2026-09-12, after the agy JSON form (which contains neither "limit reached" nor "429") put a
99-hour quota wall through the `other` path and spent eight six-minute resume attempts on it.

**A hung child is not a finished one, and the runner used to notice neither** (added 2026-09-12).
`quietMinutes` closes a session that has **already written its DONE note**; nothing watched a child
that hangs *before* writing one, so the lane simply waited for the launcher's own print-timeout —
360 minutes for `agy`. Measured that day: an agy child sat 48 minutes with an empty stdout, no new
commit and no DONE note. A session is now **stalled** when its session log, its worktree
(tracked *and* untracked files, not just commits) and **its process tree's CPU** have *all* been
quiet for `stallMinutes` with no DONE note (`0` disables it; the default, like every timeout here,
lives in `$HandoffDefaults` and is printed by `-Validate`); the runner then ends the
child and resumes it down the same path as any early stop, so a stall that is really a usage limit
or an expired login is still classified from the log. The third signal is not belt-and-braces: the
same day, an agy child that looked identical from outside — empty stdout, no commit, no dirty file,
35 minutes of it — was working the whole time and went on to make 7 commits and write its DONE
note, so a two-signal detector would have killed a session 35 minutes into finishing its plan. The
rate rule is `stallWindow` — `maxStalls` stalls inside `hours` hours: stalls that frequent are a
**general** problem rather than a one-off, so the lane stops as `stalled-repeatedly` for the
operator instead of being resumed again; stalls older than the window drop out of the count.

**A print-mode launcher's stdout is not a life sign.** Under `agy -p --output-format json` the
stdout file is created at 0 bytes and written **once, at turn end** — measured 2026-09-12: created
08:43:48, still 0 bytes 57 minutes later, then 92,837 bytes at 09:40:13. Its mtime is therefore the
*launch* time for the whole turn, so for a `runner-managed` launcher only the **stderr** file, the
worktree and the process tree may count towards a stall; everything else has a stdout log that is
appended as it goes and that is the only log it has. Two corollaries. First, `agy`'s stderr line
`root agent idle; waiting for N background task(s) (bounded by --print-timeout)` means the **root
agent is blocked on its own asynchronous terminal command** (a `pytest` run, that day) — it is
*not* evidence of a subagent: `agy agents` was empty and no child conversation existed. Second,
`--output-format stream-json` (NDJSON, written incrementally) is the recorded future path to live
progress and is **not implemented here**; until it is, the process tree is the only signal that
distinguishes a hung child from a busy one.

**Success is the DONE note and the guards — never the agent's own final status.** Measured
2026-09-12: an agy turn that completed every task, made 7 commits and wrote its DONE note still
reported `"status":"ERROR"` (`Your previous response contained an improperly formatted function
call`). The runner reads that JSON in exactly two places — the preflight probe, and recovering a
session id — and in neither does it decide whether a session succeeded.

**Resume, never restart.** The runner keeps one live session per handoff: it stops the finished
one before resuming, so retries never accumulate background sessions holding the same
conversation. Nothing a session committed is ever lost.

**A resumed session is told why, when, and that its subagents are gone.** The nudge names the
reason (limit reset, transient failure, session-hours cap, host or runner restart, early stop),
the current time, and the one fact the session cannot see for itself: every subagent it had in
flight died with the previous process, so their staged or unstaged work must be checked and
re-dispatched. Measured 2026-09-08: a host shutdown killed a session with two fix-round
implementers mid-edit; the generic "continue where you stopped" would have left it waiting.
