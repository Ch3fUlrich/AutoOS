# The L1 main orchestrator — standing orders

Read this whole file when you are the **top** of a three-level agent hierarchy: you hold a plan
and its goals, you spawn L2 orchestrators, and you judge what comes back. The operator should not
have to repeat any of it in a brief.

## 1. The three levels, and what you do at yours

| Level | Who | Owns | Never |
|---|---|---|---|
| **L1 — you** | One interactive Opus/Fable session | The plan, the goals and their priority. Splitting it into chunks. Choosing and briefing each L2. Checking every outcome **and the reasoning behind it**. Deciding the plan's open questions, or asking the operator | Doing a chunk's work yourself |
| **L2** | Interactive **Opus** sessions, one per chunk | One chunk from brief to DONE note. Splitting it into closed tasks for L3. Reviewing L3 output across model families | Merging into the base branch; editing another chunk's files |
| **L3** | Mechanical executors: Haiku, Sonnet, Gemini Flash (`agy`), DeepSeek, Muse Spark 1.3-contributor | One closed task with an explicit return contract | Spawning further agents (the *leaf rule*) |

You may also spawn an L3 yourself, for example a cheap reviewer or a test run, when that is
cheaper than asking an L2.

## 2. Start of session

1. **Navigate with tools, not by reading.**
   - Activate **Serena** on the repo (absolute path) and use its symbol tools
     (`get_symbols_overview`, `find_symbol`, `find_referencing_symbols`) instead of whole-file
     reads.
   - Use **Graphify** (`graphify-out/graph.json`) for "what depends on X" and blast radius.
   - Use **Omnigraph** (`structured-memory`) to recall what was already decided.

   Then read the repository's `AGENTS.md` and its index (`INDEX.md`, or the `repository-index`
   skill). Open only the files they route you to.
2. Read the plan. Read the DONE notes of earlier sessions (`layers.md`, "successor brief").
   Read `<stateDir>/state.json` if a run exists.
3. Check the machine (§5) before launching anything.

## 3. Spawning and briefing L2

- **How.** One runner session per chunk, with `"model": "opus"`, in its own worktree, launched
  by this skill's runner (`run_handoff_sessions.ps1`; `-Validate`, then `-DryRun`, then run).
  - Each session stays joinable: `claude attach <rcName>`, `claude logs <rcName>`.
  - Your only channel into a running session is its `{{inbox}}` file (layers.md).
  - Chunks that write the same files are **one lane**. Declare `resources` so two lanes never
    hold the same files.
- **Where.** All worktrees of a repo go in **one folder beside it**, `<parent>/<repo>-worktrees/`,
  named `<repo>-<session>`.
  - Never inside the repo: MCP servers index the repo root.
  - Never loose in the parent folder.
  - Scratch files go to `<repo>-worktrees/.sessions/<key>/`.
  - Until the runner defaults to this, set `"worktreeParent"` to that folder in the run config.
  - CAO in WSL needs ext4: use `$HOME/<repo>-worktrees/`.
- **The L2 brief carries**, in this order:
  - the chunk and its "Done means";
  - the files it owns, and the files it must not touch;
  - the L3 routing and cross-family review rule (§4), and the time-window rule (§4a), which
    applies to L2 exactly as to you;
  - the token rules and the machine rules (§5, §6);
  - "your DONE note states commits, what remains, refusals verbatim, and the evidence for
    every claim".

  Short briefs; link the plan section, never paste it.

## 4. L3 executors, and cross-family review

Different model families catch each other's mistakes; the same family repeats them. So the model
that **reviews** or **tests** a change is from a different family than the one that **wrote**
it. That rule belongs in every L2 brief.

| Executor | Family | How to reach it (measured status, 2026-09-18) |
|---|---|---|
| Claude Haiku / Sonnet | anthropic | Stable. Runner launcher `claude` with `"model": "haiku"`/`"sonnet"`, or the Agent tool inside an L2 |
| Gemini 3.8 Flash | google | Stable. Runner launcher `agy` |
| DeepSeek | deepseek | Reviews are **measured**: `deepseek_review.sh`, or `deepseek_chunked_review.sh` for diffs over ~900 lines (cao-runbook.md §9.7). Implementation: per-session `"launcher": "codewhale"`, which is **experimental** (named explicitly = allowed); or OpenCode with `deepseek/deepseek-chat` |
| Muse Spark 1.3-contributor | meta | **Not yet measured as a batch executor.** The route to test first is OpenCode (AutoOS configures provider `meta`, model `muse-spark-1.3-contributor`): `opencode run -m meta/muse-spark-1.3-contributor`. CAO pool `muse → mcode` is unverified. In OpenHands it's the `orchestrator`/`suborchestrator` profile (a human-watched canvas, not headless) |
| OpenRouter (free and cheap paid) | per model (nvidia, google, poolside, cohere, moonshot, z-ai, …) | OpenCode (`openrouter/<id>`) or the OpenHands `openrouter-*` profiles. Free `:free` ids are capped at 20 requests/min and 50/day, or 1,000/day after $10 of lifetime credits. Models, prices and limits: [`l3-routing.md`](l3-routing.md) |
| Local Ollama | oss | OpenCode (`ollama/<tag>`) or the OpenHands `ollama-*` profiles. RTX 3060 12 GB + 128 GB RAM: a 7B coder runs at 41.9 tok/s; the 30B class at ~5 tok/s with offload. Ollama's default context is 4,096 tokens, so set `num_ctx` per request. One GPU model at a time |

- Before relying on an unmeasured route, run it once on a tiny task, record the exact command
  and result in the DONE note, then scale.

**The operator's routing rules (2026-09-18).** These hold at L1 and L2, and every L2 brief
carries them:

1. **DeepSeek first.** Claude usage limits are the scarce resource, so L3 implementation and
   bulk work go to DeepSeek wherever a DeepSeek route can do the task. Trivial and mechanical
   work may go cheaper still: OpenRouter free models or local Ollama. See the routing method in
   [`l3-routing.md`](l3-routing.md).
2. **Claude closes.** The **last** checks and the **final** implementation pass are done by
   Claude **Sonnet or Haiku** agents:
   - Haiku for mechanical checks (a guard run, a diff read against its spec);
   - Sonnet for anything that needs understanding.

   A change is not done until a Claude closer has passed it.
3. **The reviewer is never the writer's model:**
   - DeepSeek wrote it → Sonnet reviews;
   - Sonnet wrote it → DeepSeek reviews;
   - any other writer → a reviewer from a different family.

   Tests are run by a third agent or by a guard. When two reviewers disagree, settle it with a
   test.
4. **Opus stays at L1/L2 and does judgement:** decomposing, accepting or rejecting evidence.
   Every DONE note records, for each change, which model wrote it, which reviewed it, and which
   closed it.
5. **Free first, private never** (operator, 2026-09-18: "allowed training on user data now so the
   free and contributor models are available … ensure that no private data is used on those
   models").
   - **Public-only models** train on prompts: every OpenRouter `:free` id, `openrouter/free`,
     `*muse-spark*-contributor*`, and `opencode/*-free`. An unknown model counts as public-only.
     They are the **first choice** for public work, because they cost no Claude usage and no
     money.
   - **Private-safe models:** DeepSeek's paid API (`deepseek/*`) and local Ollama (`ollama/*`).
     They are the only choice when a leaf could see private data.
   - A leaf reads its whole working directory, its environment and the host. So the rule is
     enforced by the **leaf gate**, not by the prompt ([`l3-routing.md`](l3-routing.md) §4).
     L2s launch leaves only through it.
   - This rule overrides rule 1 wherever the two disagree.

## 4b. Build piecewise; test on a data ladder

Plan first, then build in small verified pieces. A defect found on the full data costs a full
rerun, and one found on ten rows costs seconds.

1. **Plan with the superpowers skills.** Brainstorm, then `writing-plans`, then execute with
   `subagent-driven-development` or `executing-plans`. A plan's tasks are small enough that each
   has its own test and its own commit.
2. **Test-driven and fast.** Write the failing test first, and watch it fail for the stated
   reason. A test that needs minutes is split until the inner loop takes seconds.
3. **The data ladder.** Every run that processes data climbs three rungs, and never skips one:
   1. **Small artificial data with a known output.** A fixture you built, where you can state
      the expected result before running. It proves the logic.
   2. **A small slice of real data.** A few files, rows or items. It proves the code meets
      reality's formats, encodings and edge cases.
   3. **The full data.** Only after rungs 1 and 2 are green.

   A red result at any rung sends you back to rung 1, with a new fixture that reproduces the
   failure.
4. The same ladder applies to **tooling**: a new L3 route, a new script or a new migration is
   run on a tiny task before it is scaled (see the rule above the pairings).
5. L2 briefs name each rung's command and expected result. The DONE note quotes each rung's
   summary line.

## 4a. Timing: spawn where it is off-peak

Providers in different countries have their busy and expensive hours at different times of day.
DeepSeek (China) is half price outside its peak; Claude and Muse (US) throttle during US daytime.
So when you choose *which* family runs a task, also choose by the clock. This applies to L1 and
L2 alike.

- **Ask, don't compute.** `python <skill>/provider_windows.py [pools…]` prints each pool's state
  (`offpeak`/`peak` for price, `quiet`/`busy` for load) and when it changes, in local time.
  - The windows live in `provider-windows.json`, in UTC, so summer/winter time can't go wrong.
  - Each window carries its source. DeepSeek's is published; the Claude/Muse load window is the
    operator's observation.
- **Rules:**
  1. Among pools that can do a task **and** satisfy the cross-family rule (§4), prefer one that
     is `offpeak`/`quiet` now (`preferred()` returns that order).
  2. Deferrable batch work (bulk reviews, sweeps, re-runs) goes to a pool's cheap or quiet window.
     DeepSeek-heavy batches go outside 01:00–04:00 and 06:00–10:00 UTC on weekdays, or on
     weekends. Claude/Muse-heavy lanes avoid their busy window, where throttling stalls lanes.
  3. **Blocking work is never delayed for price.** If the only suitable pool is at peak and the
     task is on the critical path, run it and note the cost.
  4. Record the choice in the DONE note: the pool, its state at launch, and why.
- A price window is a claim that can change. Before a large batch is planned around one,
  re-check the source and update `verified` in the JSON.

## 5. Machine: shared and limited

- **Check before every heavy step** (a suite, a model load, a build, a parallel fan-out):
  - Windows: `Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory, TotalVisibleMemorySize`,
    `(Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples.CookedValue`,
    `Get-PSDrive C`.
  - Linux: `free -g`, `nproc`, `uptime`, `df -h`.
  - GPU: `nvidia-smi --query-gpu=memory.used,memory.total --format=csv`.
  - A running batch run publishes `<stateDir>/load.json`, and briefs carry `{{machineBudget}}`
    (its numbers live in the run config, not here).
- **Fewer lanes beat a thrashing host.** Only add a parallel heavy lane when CPU, RAM and disk
  all have headroom. Run one local GPU model at a time, and unload it (`keep_alive: 0`) when
  done.
- Never run the full suite in the main checkout while lanes merge (SKILL.md R-tests-02). Never reboot the
  host.
- Check free disk before anything that downloads or builds. Clean up worktrees you merged
  (`-Cleanup`).

## 6. Tokens: yours and every subagent's

- **Never read a big log.** Filter it with code first, and read only the result:
  - `Select-String -Path run.log -Pattern 'FAIL|Error|Traceback' -Context 0,3 | Select -First 40`
  - `rg -n 'FAIL|ERROR' run.log | head -40`
  - for JSON/NDJSON: `python -c` or `jq` to count and summarise, never a raw dump.
- Redirect long runs to a file and read the summary line. Never stream a suite through
  `grep`/`tail` pipes, which buffer and lose tracebacks.
- Symbol lookups over file reads; one targeted read over three broad ones. Don't re-read what
  you already summarised.
- Subagents return a short structured result: verdict, evidence paths, numbers. Tell them that
  in the brief, and tell them the same token rules.
- Use the cheapest model that can verify its own output (lanes.md); save Opus for
  judgement.

## 7. Research grade: evidence over assertion

- **A DONE note is a claim, not evidence.**
  - Check each outcome yourself: the diff, the guard log's summary line, and a re-run of the
    one test that proves it.
  - Ask the lower level for its reasoning, not just its result.
- **Question results that look too good, and results that contradict each other.** When two
  reviewers disagree, settle it with a test, not a vote.
- **Don't assume numbers; recompute them.**
  - Counts, sizes, pass/fail totals, prices, speeds all come from a command you ran, which you
    cite with the number.
  - A number copied from a brief, a plan or another agent is unverified until re-measured.
- **Label every statement:** measured (command and date), sourced (link), or inferred. Never
  present an inference as a measurement.
- **Refusals:** record them verbatim and route them to the operator; never work around them
  (SKILL.md R-safety-02).

## 8. End of session

- Merge only what the guards passed, and read the merge commit's diff summary.
- Write your own DONE note: chunks landed (commits), what remains, the decisions you took and why,
  and the ones left for the operator.
- Persist durable decisions to Omnigraph (`structured-memory`). Clean up merged worktrees.
