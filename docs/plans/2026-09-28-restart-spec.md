# RESTART — cheap relaunches: state card + context pack (spec)

Owner: autoos-L1-routing. Operator decision D-040 (2026-09-28), relayed by the L0 router.
Status: SPEC — cross-family review, then Sonnet, before any code lands.

## Why (measured, L0 2026-09-28)

- A relaunch reads 14–17k tokens when it reads only the inbox tail, and 40–70k when it re-reads
  whole inboxes. One L2 inbox alone is ~53k tokens.
- 20+ orchestrator relaunches in 48 h.
- One turn at 600k context costs ~60k token-equivalents even when fully cached, so a later cap is
  not free: every turn past ~350k costs more than a relaunch does.

## Goal

A relaunched L1/L2 orchestrator reads one generated pack of at most 8k *variable* tokens, behind
a byte-identical cached prefix. It never opens a whole inbox. The pack is built by code from state
the session keeps small at every wave. Then the context cap drops to ~350k.

## Non-goals

- The console/event store that replaces inbox markdown (later; the inbox stays the archive).
- Memory recall itself: the pack reserves a slot and calls a stub until the memory spec lands.
- Worker (L3) relaunch: workers are relaunched from their brief (R-orch-06), not from a card.

## 1. State card — `RUN/status/<name>.card.md`

One per session, written only by that session (one writer per file).

- At most 40 lines. Fixed sections in this order, each required, each at most the listed lines:

| section | max lines | content |
|---|---|---|
| header | 1 | `# card <name> — <UTC> | <session id> | context <n>k/<cap>k | last-event <id>` |
| goal | 3 | the standing goal, who set it |
| state | 6 | branch shas, main sha, what is held and why |
| next | 3 | the next three actions, in order |
| threads | 12 | one line per open lane/question/ready: `<id> | <where> | <state> | <next>` |
| traps | 8 | things a successor would get wrong, each with an evidence pointer |
| operator | 4 | operator-only steps, verbatim |

- `last-event <id>` is the inbox position the card has absorbed: the UTC timestamp of the last
  inbox line acted on. Everything after it is "events since the card".
- It is updated every wave with a small edit, not rewritten at the cap. It replaces the long free-form
  status file as the relaunch input. The status file stays for humans, but nothing reads it at relaunch.
- `autoos-agent.py card check <file>` validates it: section order, line caps, header fields, that
  `last-event` parses, and that no line exceeds 200 characters. Exit 0 ok, 1 invalid (a reason per
  problem), 2 unreadable.
- A card check runs at every heartbeat (`heartbeat` reports `card: stale` when `last-event` is
  older than the newest inbox line it has already answered with `→ done`).

## 2. Inbox reads bounded by the card

- `autoos-agent.py inbox <name> --since-card` prints only the inbox lines after the card's
  `last-event`. `--since <UTC>` does the same from a given time. `--max-lines N` (default 60) caps
  the output and says how many were cut.
- Multi-line blocks: a continuation line (no leading timestamp) belongs to the line above and is
  printed with it.
- The tool never prints a whole inbox without an explicit `--all`. Relaunch prompts and the skill
  use `--since-card` only.

## 3. Context pack — `autoos-agent.py pack <name>`

The pack is printed to stdout (or `--out`) in this order, so the stable part is a cacheable prefix:

1. **Prefix (byte-identical across relaunches of the same role):** the orchestration skill's rule
   section plus the role brief file. No timestamps, no shas. The pack prints the prefix's sha256
   so a test can pin that it did not change between two runs.
2. **Live snapshot (code-measured, not remembered):** the `l1_handoff.py` snapshot (repo branch/sha,
   main/origin main, sessions, leftover helpers), plus the lanes from `git worktree list`, the live
   spawner workers (`ps`), open readies (`ready` lines without a matching `main=` answer), and open
   questions (`Q:`/`question:` lines without an answer).
3. **Memory:** `context_pack` from the memory facade. Stub: one line `memory: not wired (MEMSPEC)`.
4. **State card:** verbatim.
5. **Events since the card:** the `inbox --since-card` output.

- It prints an estimate of the variable part (sections 2–5) in tokens (chars/4) on stderr. It exits
  1 with that estimate when the variable part exceeds `--budget` (default 8000), so a bloated
  card is caught at relaunch, not after.
- `l1_handoff.py` gains `--pack`: the handoff file then is the pack.

## 4. Relaunch command from code

`autoos-agent.py relaunch-line <name>` prints the exact command that relaunches an L1/L2 session.
It uses the session name, worktree, model and MCP config from one data file (the run config),
with the pack as the first prompt. No prompt or skill text carries a hand-built `claude --bg …` line.
It prints the line only; running it is the parent's action.

## 5. Context cap and the metric

- `policy.handoff_caps.claude-opus-1m.cap_tokens` 600000 → 350000 (cap_fraction 0.35), with
  source D-040. The registry is the only home (C4). `autoos-agent.py context` and heartbeat read it.
- **Metric: orchestrator tokens per merged change.** Over a window, sum the orchestrator sessions'
  transcript tokens (input + output + cache write + cache read × the cached-read weight), then
  divide by the first-parent merges into `main` in the same window. It is computed by
  `autoos-agent.py usage --orchestrators --since <ts>` from the Claude Code transcript JSONL files
  (the same records `context` already reads).
- Before: the 48 h up to the RESTART merge. After: the 48 h after the cap change is live. Both
  numbers go to the L0 router with their windows and the counts behind them.

## Delivery (lanes, each test-first, cheap writer → cross-family review → Sonnet final)

| lane | scope | files |
|---|---|---|
| R1 inbox | `inbox --since-card/--since/--max-lines`, continuation lines | tools/autoos-agent.py, tests |
| R2 card | card schema, `card check`, heartbeat `card: stale` | tools/autoos-agent.py, tests, references/state-file.md |
| R3 pack | `pack`, `l1_handoff.py --pack`, budget exit, prefix sha | tools/autoos-agent.py, l1_handoff.py, tests |
| R4 relaunch | `relaunch-line` from the run config | tools/autoos-agent.py, config example, tests |
| R5 metric+cap | `usage --orchestrators`, before number, then cap 350k | tools/autoos_usage.py, catalog/ai-registry.json, tests |
| R6 skill | R-coord-06/08 point at card/pack/inbox tools; state-file.md becomes the card spec | SKILL.md, references/ |

Order: R1 → R2 → R3 (needs R1, R2) → R4, then R5 (before-number first, cap last), then R6. R1 and R5's
measurement can run in parallel.

## Open (decide in review)

1. Is the cached-read weight for the metric 0.1 (Anthropic cache-read price ratio), or plain
   token counts? Proposed: report both.
2. Card location: `RUN/status/` (git-ignored run dir) is proposed; a card that must survive a
   run change would need a repo home.
