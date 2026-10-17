# AGENT_ORCHESTRATION_RATIONALE.md

Purpose: explain the design decisions behind `SKILL.md`, `agent_orchestration.config.yaml`, and `ARCHITECTURE_CONTRACT.md`.

> This file answers **why**. `SKILL.md` states **what** the policy is and owns nothing numeric;
> `agent_orchestration.config.yaml` owns every threshold, weight, tier, and matrix. If a number
> appears here, it is illustrative — the config is authoritative.

## 1. Why a Three-Role Model

A single coding agent often mixes planning, implementation, and validation in one context. This increases the chance of:
- architecture drift
- incomplete implementation
- self-approval of weak code
- hidden assumptions surviving unchallenged

Separating roles reduces these failures:
- architect preserves system-level intent
- engineer focuses on scoped execution
- reviewer independently validates fit and quality

This structure is not about adding agents everywhere. It is about separating responsibilities so the implementation does not lose the big picture.

## 2. Why the Architecture Contract Exists

The main recurring failure mode in multi-agent coding is that implementation does not fit the larger system. Plans written informally in chat are too lossy.

The contract fixes that by turning architecture into an executable handoff artifact.

The contract:
- defines exact scope
- captures interfaces and invariants
- lists forbidden changes
- defines objective acceptance gates
- specifies checkpoint and recovery boundaries

This reduces ambiguity between planning and implementation.

## 3. Why Chat History Is Not Trusted

Long-running coding tasks exceed reliable conversational memory.
Agents lose details, compress incorrectly, or prioritize the latest turn over the most important constraint.

Durable artifacts are more reliable than context alone:
- `task.md`
- `BUG_REGISTER.md`
- `ARCHITECTURE_CONTRACT.md`
- checkpoint files

This design assumes conversation is useful but not authoritative.

## 4. Why Best-of-N Is Selective

Running multiple agents on the same task can improve outcomes when:
- requirements are ambiguous
- there are multiple valid implementations
- concurrency or security makes blind spots costly
- a previous attempt failed

But Best-of-N is expensive.
It multiplies token use and verification cost.

So it should behave like a special tool, not a default mode.
The framework therefore activates it only when risk justifies the additional cost.

## 5. Why the Trigger Signals Look the Way They Do

The chosen risk signals are not arbitrary. They reflect common sources of agent failure:
- many files touched increases integration risk
- public API changes increase ripple effects
- ambiguous acceptance causes divergence
- high-churn modules are less stable
- missing tests reduce objective feedback
- concurrency and security code have subtle failure modes
- prior review failure is direct evidence that a single pass was insufficient

These signals help reserve expensive orchestration for the tasks that benefit most from it.

## 8. Safe Locking Lifecycle
**Why does lock discipline matter?** In a multi-agent coding environment, two engineers assigned to different concurrent bugs might attempt to modify the exact same Python file simultaneously. The Lock Manager prevents write-collision by assigning temporary leased locks (`.agent-state/locks`) on critical files. If an agent tries to modify a locked file, it falls back to a deterministic abort or blocks, rather than causing a silent merge collision on disk.

## 9. Stale Worktree Lifecycle
**Why clean up deterministic worktrees safely?** A repository flooded with `agent/mock-engineer-001` git worktrees will eventually crash IDE indexers or clutter standard developer workflows. By implementing `git worktree remove` on task success and selectively pruning stale metadata during finalization (`finalize_task_state`), we ensure the project remains ephemeral and tidy. We never allow `remove --force` on the primary codebase tree—there are hard safety blockers to ensure orchestration errors cannot destroy the main workspace.

## 10. Subprocess Execution Hardening
**Why avoid shell=True?** Because any task config mapping an unvetted command (e.g. via dynamic inputs) into a Python `subprocess.run(..., shell=True)` is vulnerable to trivial arbitrary code execution via shell escaping (e.g. `&& rm -rf /`). Removing `shell=True` and preferring strict `shlex` argv-lists sanitizes local runtime behavior without complicating the Python execution layer.

## 6. Why Verification Is External

Agents are poor judges of their own correctness when incentives push them toward completion.
If the same agent writes code and decides whether it is done, quality drops.

The reviewer and verification commands are therefore independent gates.
The framework does not accept:
- "looks good"
- "tests should pass"
- "implementation complete"

It accepts only explicit evidence.

## 7. Why Token Budget Is Advisory, Not Hard Stop

A hard token ceiling can produce half-finished implementations that later require more repair than the original savings justified.

The framework therefore treats token budget as a supervision signal.
When token use becomes abnormal, the architect intervenes.
That intervention can improve quality by:
- reducing scope
- clarifying requirements
- supplying missing context
- downgrading only the mechanical part of work
- aborting malformed tasks early

This avoids false savings.

## 8. Why Checkpoints Are Mandatory

Long sessions fail.
Providers disconnect.
Tools crash.
Worktrees get interrupted.

Without checkpoints, the agent must reconstruct state from memory, which is unreliable.
Checkpoints make recovery deterministic.

The framework stores:
- where the agent worked
- what it changed
- what it finished
- what remains
- which contract version applies

This makes resume possible even after interruption.

## 9. Why Native Checkpointing Is Secondary

Provider-native checkpointing is useful, especially when it is robust.
Claude Code is strong here.

But native recovery should not be the only layer because:
- it is provider-specific
- it may not fully transfer across orchestrators
- external branch/worktree state is still needed for auditable continuation

So native checkpointing is treated as a bonus recovery layer.
Canonical recovery still relies on durable local artifacts and git state.

## 10. Why Branch/Worktree Isolation Matters

Parallel agents without isolation overwrite each other, create hidden merge conflicts, and contaminate verification.

Isolated branches/worktrees solve:
- file collision
- resume clarity
- selective rollback
- candidate comparison in Best-of-N

This is especially important when multiple engineers solve the same task independently.

## 11. Why Omnigraph Is Preferred Over Generic Memory MCP

Generic memory is useful for storing facts.
But orchestration across coding agents requires more than facts.
It requires shared operational state:
- task relationships
- dependency context
- graph queries
- branching and coordination semantics
- durable project-specific structure

Omnigraph matches this better than a generic persistent memory layer.
That is why it is preferred as the main shared graph/memory substrate for coding orchestration.

Generic memory MCP is still useful for lightweight persistence, but it is not the strongest primary coordination layer for this use case.

## 12. Why Large Skill Libraries Are Dangerous

A large unfiltered skill catalog can create:
- decision paralysis
- prompt bloat
- weak routing
- overlapping or conflicting instructions

The framework therefore uses small curated skill sets per role and lazy loading.
This improves consistency and reduces unnecessary context size.

The principle is:
- fewer available actions
- clearer role boundaries
- lower token overhead
- better repeatability

## 13. Why These MCP Servers Were Chosen

### Serena
Chosen for symbol-level retrieval and editing.
It reduces dependency on crude grep-style navigation.

### Graphify
Chosen for dependency and codebase understanding.
It helps the architect split work cleanly.

### Omnigraph
Chosen for shared state and coordination across agents.

### Superpowers
Chosen because workflow discipline improves output quality.
TDD and structured debugging reduce sloppy code.

### Context7
Chosen to reduce outdated or hallucinated API usage.

### Fetch
Chosen for lightweight external retrieval without overloading terminal-based ad hoc scraping.

### Playwright
Conditional because browser automation is useful only for UI and workflow verification.

### Sentry
Chosen as the default observability MCP. It is ideal for catching runtime errors, performing failure analysis, and early error detection in standard application logic. It remains the default because its scope is typically narrower and more action-oriented for a single repository or service.

### Datadog
Chosen as a conditional observability MCP. It becomes valuable only when system topology requires cross-service context—such as distributed systems and multi-server setups. Enabling it by default on single-node projects adds unnecessary noise and complexity.

### Observability MCP Access Rules
Observability MCP access must be tightly scoped because telemetry data is vast, often noisy, and can contain untrusted external input.
Exposing full production telemetry to an agent increases the risk of prompt injection and tool poisoning (e.g., an attacker embedding malicious instructions in an error trace). Therefore:
- Access is scoped strictly by role (Architect for triage, Engineer for debugging, Reviewer for regression-checking).
- Access is disabled by default for ordinary local feature work.

### Observability Configuration Strategy
Hosted SaaS and self-hosted environments require different assumptions. Hosted Datadog/Sentry uses standard tokens/API keys and default domains. Self-hosted deployments require overriding URLs (e.g., `SENTRY_HOST`), adjusting certificate validations (`NODE_EXTRA_CA_CERTS`), and potentially disabling advanced features not supported locally (`MCP_DISABLE_SKILLS`). Documenting these required variables rather than providing rigid deployment scripts keeps the framework markdown-first and provider-agnostic, allowing any local stack (e.g., Docker Compose) to plug into the runtime seamlessly.

### Sequential Thinking
Conditional because strong thinking-capable models often do not need it, while weaker models may benefit.

## 14. Why the Framework Is Provider-Agnostic

Provider capabilities change.
A workflow tied too tightly to one model or IDE will age poorly.

The framework therefore separates:
- orchestration logic
- durable artifacts
- recovery semantics
- verification contracts

from:
- provider-specific caching
- provider-specific checkpointing
- provider-specific agent setup

This keeps the workflow portable across Claude Code, Antigravity, Codex, OpenHands, DeepSeek-TUI, and custom orchestrators.

## 15. Why the Final Documents Are Minimal

The final framework is designed for agent consumption, not essay reading.
Too much prose harms execution.

So the operational documents emphasize:
- rules
- triggers
- required fields
- exact artifacts
- order of operations
- failure behavior

The rationale file exists separately so the operational files can stay compact.

## 16. When to Adapt the Framework

Adjust the framework when:
- project size is tiny and orchestration overhead is larger than implementation cost
- verification is too expensive relative to task value
- provider-native features outperform custom recovery in a stable environment
- language ecosystem requires different verifier stacks
- team governance requires different review thresholds

Keep unchanged unless there is clear evidence otherwise:
- contract-first handoff
- external verification
- checkpointed recovery
- role separation
- selective Best-of-N
- curated skill routing

## 17. Why the YAML Config Exists

The YAML config separates policy from runtime logic.

This matters because orchestration rules change more often than core control flow.
A declarative config makes it easier to:
- change thresholds without editing code
- add or remove MCP assignments safely
- adjust risk scoring
- tune Best-of-N triggers
- adapt to different providers and repositories

The YAML file acts as the operational policy layer.
It defines:
- role permissions
- MCP routing
- risk thresholds and weights
- token budget policy
- checkpointing requirements
- cache-control behavior
- provider capability flags

This keeps the orchestrator implementation smaller and more stable.

## 18. Why the Python Scaffold Exists

The Python scaffold is the execution layer that reads policy and enforces it.

It is intentionally minimal.
Its goal is not to be a full agent framework.
Its goal is to provide a clean starting point for:
- risk scoring
- checkpoint persistence
- worktree setup
- recovery behavior
- MCP routing
- budget supervision

The scaffold encodes the core control points that matter most for reliability:
- `RiskEngine` decides how much orchestration a task deserves
- `StateStore` persists checkpoints and locks
- `GitManager` isolates work in branches/worktrees
- `ContractManager` hashes the contract so resumed work can detect contract drift
- `MCPRouter` selects only the tools relevant for the role and task
- `BudgetEngine` keeps cost supervision separate from implementation logic

## 19. Why Policy and Runtime Are Split

A common orchestration failure is mixing configuration, agent policy, and execution logic into one large prompt or one large script.

That makes the system:
- harder to debug
- harder to port
- harder to test
- harder to adapt across providers

The split used here is deliberate:
- markdown files define human-readable and agent-readable operating rules
- YAML defines runtime policy values and routing
- Python enforces the mechanics of state, recovery, and selection

This improves maintainability and makes experimentation safer.

## 20. Why the Scaffold Is Incomplete by Design

The scaffold does not directly call model APIs, run subagents, or implement full provider adapters.

This is intentional.
Those parts vary heavily across:
- Claude Code
- Antigravity
- Codex
- OpenHands
- custom internal tools

The scaffold instead defines stable orchestration primitives that survive provider changes:
- contract hashing
- checkpoint writing
- worktree creation
- lock management
- risk routing
- MCP selection

This keeps the architecture reusable.

## 21. How YAML and Python Fit the Rest of the System

The full stack is now:

- `SKILL.md`: normative role behaviour and workflow rules (no numbers)
- `ARCHITECTURE_CONTRACT.md`: scoped task contract
- `AGENT_ORCHESTRATION_RATIONALE.md`: explanation of decisions
- `agent_orchestration.config.yaml`: **all** policy, thresholds, tiers, and matrices
- `orchestrator_scaffold.py`: runtime scaffold
- `README.md`: how to run the scaffold and add a provider adapter

Together these files separate:
- intent
- policy
- execution
- recovery
- explanation

That separation is one of the main reasons the framework should remain stable as models and providers change.

## 22. Why the YAML Uses MCP and Provider Capability Flags

Different providers expose different behaviors for:
- caching
- checkpointing
- model reasoning
- tool integration

Encoding these capabilities in YAML instead of hardcoding them in Python makes it easier to:
- switch providers
- compare setups
- run different environments
- test fallback behavior

This also supports mixed-provider orchestration, where the architect, engineer, and reviewer may use different model families.

## 23. Why the Scaffold Uses Contract Hashing

A resumed agent should not continue blindly if the architecture contract changed while it was interrupted.

Hashing the contract provides a simple guard:
- if the hash matches, resume safely
- if it changed, the agent must re-read the contract and potentially replan

This prevents stale continuation after architectural drift.

## 24. Why MCP Routing Is in the Runtime Layer

Not every task needs every MCP server.
Loading too many MCPs increases prompt mass and raises routing ambiguity.

Runtime MCP routing allows the orchestrator to:
- assign only relevant tools
- preserve leaner context
- reduce unnecessary decisions for workers
- keep behavior aligned with role and task type

This is one of the most important practical optimizations for large-codebase agent workflows.

## 25. Why Provider Adapters Are Separate

The provider adapter layer isolates orchestration logic from provider-specific execution details.

This is necessary because providers differ in:
- prompt format
- tool calling structure
- MCP integration style
- skill support
- native checkpointing
- cache control support
- browser and terminal capabilities

Without adapters, the orchestrator would accumulate provider-specific branching and become harder to maintain.

The adapter pattern solves this by making the orchestrator depend on one stable interface:
- build request
- invoke agent
- resume agent
- report capabilities

Each provider can then translate the framework's abstract request into its own runtime format.

## 26. Why a Registry Is Used

The registry gives the orchestrator one lookup point for all providers.

This has several advantages:
- easier provider switching
- simpler fallback logic
- cleaner testing
- easier support for mixed-provider role assignment

It also makes it straightforward to map:
- architect -> one provider
- engineer -> another provider
- reviewer -> a third provider

without changing orchestration logic.

## 27. Why Adapters Report Capabilities

Capability reporting lets orchestration policy react to provider differences instead of assuming all providers behave the same.

Examples:
- if native checkpointing exists, use it as a secondary recovery layer
- if explicit cache control exists, enable stable-prefix breakpoints
- if skills are supported, use curated skill routing
- if browser execution is supported, allow Playwright-driven workflows

This avoids hidden mismatches between policy and execution environment.

## 28. Why Adapters Are Stubbed First

The first version of the adapters is intentionally skeletal.

The goal is to define the stable contract before binding to real APIs.
This reduces early complexity and makes it easier to:
- test orchestration logic independently
- validate request shape
- compare providers conceptually
- add live API integrations later without redesigning the interface

The stubs are therefore a scaffolding decision, not an omission.

## 29. How the Adapter Layer Fits the Full System

The full architecture is now split into six layers:

1. role and workflow behavior
   - `SKILL.md`

2. scoped implementation contract
   - `ARCHITECTURE_CONTRACT.md`

3. compact orchestration rules
   - `SKILL.md` (normative) + `agent_orchestration.config.yaml` (numeric)

4. design explanation
   - `AGENT_ORCHESTRATION_RATIONALE.md`

5. runtime policy
   - `agent_orchestration.config.yaml`

6. runtime execution
   - `orchestrator_scaffold.py`
   - `providers/`

This structure keeps:
- agent instructions
- human rationale
- policy values
- orchestration mechanics
- provider integration

cleanly separated.

## 30. How the Python Layer Fits a Markdown-First Agent System

Most coding agents primarily read Markdown instructions.
That remains true here.

The Python layer does not replace the Markdown files.
Instead, it supports two optional use cases:

1. custom orchestration runtime
   - a user-managed Python process enforces policy, routing, checkpoints, and fallback

2. executable helper scripts inside a skill
   - the skill instructions can tell the agent to run scripts for deterministic operations

So the canonical rule system remains in Markdown:
- `SKILL.md`
- `ARCHITECTURE_CONTRACT.md`

The Python layer exists only to operationalize those rules when a custom orchestrator is desired.

## 31. Why the Integration Layer Exists

The integration layer connects orchestration policy to provider execution.

Without it, the orchestrator would need provider-specific logic in its core loop.
That would make the system harder to maintain and harder to extend.

The integration layer allows the orchestrator to:
- select provider by role
- assign fallback providers and models
- pass cache-control only where supported
- pass checkpoint hints only where supported
- keep MCP routing independent from provider details

This keeps orchestration logic stable while provider implementations evolve.

## 32. Why Role-Based Provider Routing Is Useful

Different providers excel at different things.
The framework therefore allows:
- architect on a planning-strong model/provider
- engineer on a fast, high-quality coding provider
- reviewer on a different model family to reduce shared blind spots

Role-based routing is one of the most practical ways to improve quality without using the most expensive model for every subtask.

## 33. Why Fallback Routing Is Explicit

Fallback behavior should be defined in policy, not improvised at runtime.

Explicit fallback routing:
- improves predictability
- makes failures easier to debug
- allows cost-aware backup chains
- avoids hidden provider switching

This is especially important when session continuation and checkpoint recovery matter.

## 34. Summary of Architecture Principles

To ensure clarity, the following principles strictly govern this framework's design:
- **Markdown remains canonical.** The `.md` files define the rules and behavior of the agents. They are the single source of truth.
- **Python is execution support, not policy.** The Python scaffold executes the rules but does not invent or overwrite them.
- **YAML exists to separate policy values from execution code.** Thresholds, routing, and capabilities belong in config, not hardcoded in Python.
- **The adapter layer isolates provider-specific execution details.** Provider quirks are managed behind the adapter interface, keeping the orchestrator generic.
- **The dry-run example is intended to validate orchestration flow, not model quality.** The local example uses stubbed providers to prove out routing, risk scoring, branching, and checkpointing logic without requiring real APIs.

## 35. Why Payload Shaping is Capability-Aware

Not all language models and orchestration platforms expose identical features for structured data. 

Some guarantee schema adherence natively (OpenAI Structured Outputs). Some support JSON modes but cannot enforce exact keys. Others rely strictly on prompts, and still others treat tool/function calling as an entirely separate channel from final textual output.

If the orchestrator assumes a single format, it either breaks local/smaller models or underutilizes advanced models. 

Therefore, the `ProviderExecutor` queries the provider's `capabilities()` before executing. It dynamically negotiates a response strategy (`tool_calling`, `strict_schema`, `json_mode`, `prompt_fallback`, or `plain_text`) to maximize reliability for the current task on the currently selected model. This allows the system to seamlessly mix GPT-4o (using native schemas) with a local Llama model (using prompt fallbacks) on the same orchestration run without changing the core workflow.