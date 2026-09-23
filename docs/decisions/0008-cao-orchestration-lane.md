# 0008. The CAO Orchestration Lane: Gated, Multi-Provider, Externally Policed

- **Status:** Accepted (2026-09-10)
- **Supersedes:** the `cao-orchestration` skill folded into `unattended-orchestration` by `69b229a`
- **Relates to:** [ADR 0006](0006-unattended-session-orchestration.md) (the batch lane), [ADR 0007](0007-herdr-as-unattended-backend.md) (why Herdr is not this)

## Context

`skills/unattended-orchestration` had one lane: a headless PowerShell batch runner
for overnight waves. A second mode was wanted — interactive, inspectable,
hierarchical, spread across model providers — and CAO 2.5.0 was integrated for it
in `33021f6` / `69b229a`.

Auditing that integration against the running system found that most of it did not
work, and that every failure was **silent**. A representative sample:

| Claimed | Measured |
|---|---|
| 3-layer hierarchy operating | Profiles declared `provider: antigravity`, not a `ProviderType` member; CAO fell back to `kiro_cli`, which is not installed |
| Profiles installed | `~/.cao/profiles/` is read by nothing; the store is `<name>.md` registered via `cao install`, in a home the CLI only uses when `CAO_HOME_DIR` is exported |
| `CAO_HOME_DIR="$HOME/.cao"` giving ext4 FIFO isolation | `CAO_HOME_DIR` was unset; CAO defaulted to a `/mnt/c` path |
| Cross-session memory via Omnigraph | Every agent inherited a global pin to graph `memory`, not the repo's graph |
| Tool restrictions enforced | Profiles used provider-native tool names, which CAO maps to nothing — so it blocked *every* native tool |

None of these raised an error. That shaped every decision below.

## Decision

### 1. Two lanes, one skill, shared formats — not shared code

The batch lane (PowerShell) keeps headless overnight waves. The CAO lane (Python)
owns interactive multi-provider hierarchies. They share the NDJSON ledger
conventions, `handoff.config.json`, and the `OMNIGRAPH_GRAPH_ID` derivation; they
share no implementation.

**Python for the CAO lane**, because `pwsh` is absent from the WSL distro and a
Linux server is a first-class target, while CAO itself guarantees Python 3.12 —
a dependency already satisfied wherever CAO can run at all.

### 2. The planning gate is an artifact with a validator, not an instruction

`PLAN.lock.json` must exist and validate before anything launches. Its schema
cannot express "I did not ask": it requires either a question answered by the
**user**, or an `assumedDefaults` entry recording what was decided on their behalf
**and why**. Unfilled `<placeholders>` are rejected, because the first version of
the shipped scaffold passed validation unedited — a gate with a bypass in its own
template is not a gate.

`cao/launch.py` is the single entry point and it **refuses** rather than
defaulting: no contract, unknown phase, no guard, every pool closed, phase already
claimed. Each has a tempting "sensible default", and each default silently
produces work nobody asked for.

### 3. Depth is policed externally; tool restrictions are enforced

These are different mechanisms and conflating them cost two wrong diagnoses.

- **Tool restrictions ARE enforceable**, through CAO's *universal* vocabulary
  (`execute_bash`, `fs_read`, `fs_write`, `fs_list`, `fs_*`, `web_fetch`,
  `@cao-mcp-server`). A spawner gets `fs_read, fs_list`, so CAO denies it `Bash`,
  `Write`, `Edit`, `Agent`, `Monitor` — "delegate, never implement" is a property,
  not a request.
- **Depth is NOT.** MCP is granted per *server*, so a leaf cannot be given
  `send_message` while being denied `assign`; and on `antigravity_cli` CAO
  documents restrictions as "advisory, not enforced". So depth is policed out of
  band by `cao/monitor.py`, walking the **server-stamped `caller_id` chain** — not
  the agent-written `group` field, because policing on a value the policed party
  controls is not policing.

Policing is accepted as a **monitoring** control with named costs: reactive, poll
interval as blast radius, termination can interrupt a write, unknown depth is
reported rather than auto-killed.

### 4. Quota pools and model families are orthogonal

`pool` decides fallback; `family` decides review pairing. `claude-opus-4-6` via
`agy` is the anthropic *family* from the antigravity *pool*: legitimate extra
capacity, and an invalid reviewer for Claude-written code.

**Never decline a pool.** Refusing an older model on a separate quota does not get
a better model; it gets a smaller budget. Freshness orders *within* a pool.

### 5. Orchestrators are never cheap

Layer 1 runs Fable 5.1 or Opus 5; any level that spawns runs Opus; only leaves may
run Sonnet, Gemini or DeepSeek. A level that delegates is doing judgement work, and
a weak model there does not fail loudly — it decomposes badly and every layer below
inherits it. Fable is Level-1 only and is never spawned as a subagent.
`orchestrator_model_warnings()` enforces this; it caught our own config on the
first run.

### 6. Providers: Claude and Gemini natively, DeepSeek via OpenCode

DeepSeek has no official CLI. It is reached through OpenCode, which CAO already
supports as `opencode_cli`, so no CAO patch is needed — a custom provider would
have meant a second vendored patch to maintain. The model is pinned to the exact
API id through a custom `deepseek-direct` provider rather than trusting OpenCode's
registry.

**`agy` requires the NATIVE Linux build** (superseded 2026-09-11; the earlier
claim that it was unusable was a misdiagnosis). A shim that `execv`s the Windows
`agy.exe` works with a cwd under `/mnt/c` and fails with one on ext4 — a Windows
process cannot operate with a Linux-only working directory, and the symptom
presents as "not signed in". Since this lane puts worktrees on ext4 for the 20x
win, the two decisions collided.

With the native Go binary installed and signed in, agy works on ext4: measured
`Antigravity CLI 1.2.1 / maulser@…​ (Google AI Pro) / Gemini 3.8 Flash (High)`
with cwd `~/agy-ext4-test`. `setup_cao.agy_flavour()` detects the flavour and
`--check` prints the remedy.

### 7. Layer 1 runs the guard; an agent's report never closes a phase

Every phase declares `acceptance.guard`. Until `cao/verify.py` existed nothing ran
it, so "done" meant whatever the agent said and the contract was decorative.

Layer 1 now runs the guard **the plan declared** (not one the agent chose), in the
worktree, after the agent reports done. The lease is released only on a pass. On
failure `--rework` respawns a fresh agent briefed with the guard's own output
verbatim — a paraphrase of a failure is one more chance to soften it, and the
previous agent already believed it had succeeded.

`pass` / `fail` / `error` are three states. A guard that could not run is `error`,
says so, and consumes no rework attempt: conflating it with `fail` once burned
three attempts and escalated to a human blaming code that had never executed.

### 8. Evidence from an agent is a claim, not a measurement

A worker created a file correctly and then reported an `od -c` dump that did not
match it; its supervisor relayed that verbatim while holding `fs_read`. Generated
supervisor prompts now require re-verifying anything checkable — *relaying is not
verifying* — and a test asserts the tool to do so is actually granted.

Detecting the *disagreement* came later, and is the sharper signal. `verify
--terminal <id>` compares the RESULT envelope an agent emitted with the verdict
Layer 1 just measured. A failure after an honest `status=blocked` is ordinary; a
failure after `status=ok` means that agent's reporting is unreliable on **every**
phase it touched, including the ones whose guards passed. Contradictions are
printed and appended to `verify.ndjson`, because the operator this protects was
asleep when it happened.

A guard that could not run (`error`, exit 97) never counts as a contradiction.
The absence of evidence cannot convict anyone, and a rule that manufactured
accusations out of a broken worktree would be ignored within a week.

### 9. `probe`, not `check`, decides whether a pool may be dispatched to

`check` asks whether credentials exist. `probe` asks whether the pool answers.
Only the second question predicts whether an agent will survive, and until
2026-09-11 only the first one reached the budget ledger: a live run had `probe`
report `antigravity failed` while routing dispatched two workers into it, both of
which died. `probe` now closes a failing pool and reinstates a passing one, so
the closure is revocable by evidence rather than permanent.

### 10. An agent's terminal is a rendered pane, and must be read as one

The contradiction check fed a strict line-0 parser 19,930 characters of ANSI and
silently reported "no claim" — for every terminal that would ever exist. Reading
a pane means stripping ANSI, keeping the word boundaries that cursor-move escapes
stand in for, and taking the last envelope rather than the first line. Three
outcomes are now distinguished where there were two: a false claim, an agent that
died without reporting, and silence that does not matter because the guard passed.

## Consequences

**Good.** Agents run only in disposable worktrees and `plan_launch` refuses to
point one at the main checkout. The gate is mechanical and testable without a server. Depth is
configurable and regenerates profiles. Three model families are reachable with
quota fallback. Worktrees on ext4 cut `git status` from 3.021 s to 0.146 s.
493 tests: 487 pass in WSL, 482 on Windows — the difference is shell tests that
skip where `bash` cannot reach the filesystem under test.

**Costs, accepted.**

- A vendored patch to `antigravity_cli.py`. `cao update` reverts it, so a test
  asserts the bridge is present in the live source.
- Depth over-spawn is possible between sweeps.
- A supervisor's read-only restriction is real on a hard-enforcement provider and
  prompt-level on a soft one; `spawner_restriction_warnings()` says which, rather
  than implying a guarantee it cannot make.
- Quota-detection patterns are **unverified** until a real limit is provoked;
  `budget.check` fails *open* rather than falsely cooling a healthy pool.

## Alternatives rejected

- **Herdr as the backend** — a terminal backend, not a cross-OS bridge; forwards
  `--cwd` untranslated; experimental upstream. See ADR 0007.
- **A custom CAO provider for DeepSeek** — `ProviderManager` uses static imports,
  so this means a second vendored patch.
- **A full ext4 clone instead of worktrees** — 8× faster again, at the cost of a
  push-back step that can fail halfway and lose work.
- **A CAO two-phase `plan`/`execute` workflow** — workflow steps run inside agent
  terminals, which would put planning in a subagent, the one thing the gate exists
  to prevent.
