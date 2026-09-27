# State file — the one file a successor resumes from

`RUN/status/<name>.md` is written only by `<name>` and rewritten every wave (R-handoff-01,
R-handoff-08). It is the `--state` input of `l1_handoff.py`, which prepends the live snapshot
(branches, sessions, leftover helpers) and writes `RUN/status/<name>.handoff.md` (R-handoff-04).
Source: routing v2 spec §8.3 (D16); C4 skill-edit request 2026-09-27T07:19Z; working example
`status/L1-backlog.md`.

Keep every line a fact a successor can act on or verify: sha, path, run id, pid. No narrative.

```markdown
# status <name> — <UTC time> (<session id>, context <n>k/<cap>k)
status: working | waiting <on what> | HANDOFF. heartbeat cron <id> (<schedule>).

## Goal (priority order, who set it)
1. ...

## Decisions + why
- <decision> — <why> (<evidence pointer>)

## Open questions
- <question> — asked <where, when> | waiting on <whom>

## Lanes
| id | branch | worktree | worker (leg) | state | next |
|---|---|---|---|---|---|

## Failure history
- <UTC> <lane> <what failed> -> <what was done> (<evidence>)

## Operator steps
- <one command or action each, verbatim; prose allowed here only>

## Context fill
<n>k / <cap>k (source: policy|default, `autoos-agent.py context`)
```

Procedure (R-handoff-04, R-handoff-11):

1. At half the cap, move bulky tool output into files and keep pointers.
2. At the cap from `policy.handoff_caps`: rewrite this file, run
   `l1_handoff.py --state RUN/status/<name>.md --out RUN/status/<name>.handoff.md`, append
   `handoff <name>` to the parent's inbox, stop.
3. The successor reads the handoff first, recreates its heartbeat cron, re-measures every
   live-state line before acting on it, then reads its inbox from the handoff time on.
