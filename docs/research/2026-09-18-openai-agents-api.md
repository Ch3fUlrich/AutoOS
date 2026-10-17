# OpenAI Agents API: survey and fit for the AutoOS + agent-skills hierarchy

Written 2026-09-18. Read-only research. Decision accepted in [ADR 0005](../decisions/0005-openai-agents-api-not-the-backbone.md). It replaces the second-hand §F of
`agent-skills/docs/superpowers/plans/2026-09-17-agent-orchestration-and-autoos-merge-SURVEY.md`.

**Labels.** **[S]** = stated in a primary source I fetched today. **[M]** = measured this session
(GitHub API output, local files, fetch status). **[I]** = my own inference. `[n]` points to the Sources list.
**Method.** OpenAI doc pages were downloaded as Markdown (`<page>.md`). WebFetch still gets
HTTP 403 on the openai.com announcement [M], so I read it through the built-in browser pane [M].

---

## TL;DR

- The **Agents API** is OpenAI's hosted **Codex harness**. It went into **public beta on 2026-09-10**.
  It gives you durable sessions, automatic compaction, subagents, MCP, and a sandbox that OpenAI
  hosts, you host, or that is left out. It charges no fee of its own: you pay for tokens, tools and
  containers [S 1,3,15].
- It runs **OpenAI models only**. There is no documented way to bring your own model. It is
  **not ZDR-eligible**, keeps data **only in the US**, and its subagents **share one model and one
  filesystem** [S 3,6,28; I].
- **Recommended option: (b).** Don't use it as the orchestration backbone. Add OpenAI as **one
  L3/reviewer pool through the `codex` CLI**, using CAO's existing `codex` provider type. Don't
  adopt the Agents SDK (c) for now.
- **Revisit** if any of these happen: it reaches GA; it gains bring-your-own-model; it gains ZDR;
  the Windows executor becomes stable; or a reboot-proof orchestrator becomes a hard requirement.

## 1. What it is

- **Managed runtime [S 2,3,4].** OpenAI runs the harness, which handles the model/tool loop,
  session state, compaction and recovery. Your *application server* submits work, receives events
  (by stream or webhook) and handles function tools. The announcement says the harness is the
  open-source Codex harness [S 1]. The `openai/codex` repo is Apache-2.0 [M 29].
- **Four core concepts [S 3].** An *Agent* is the model, instructions, tools and MCP servers. It
  can be saved and reused through `agent_id` [S 5]. An *Environment* is optional. A *Session* is a
  durable instance of the agent. *Events/items* are its inputs and outputs. A message sent while a
  turn is active **steers** that turn. A message sent when the session is idle starts a new turn
  [S 11]. Streams don't replay missed events: to recover, you re-read the saved items and turns
  [S 11].
- **Environment types [S 4,5].** There are three settings:
  - **`none`**: no shell, no files and no executor MCPs.
  - **`openai_hosted`**: a Linux sandbox with Python and Node. You can set packages, setup
    commands, files and a network policy (enabled/disabled/restricted). The sandbox is deleted
    after **1 h** with no activity or keep-alives, and that timeout can't be changed [S 8].
  - **`self_hosted`**: you run the executor, **`codex exec-server`**, on a laptop, container or
    partner sandbox. It connects outbound over WebSocket and runs shell commands, file I/O and
    local MCP servers when the harness asks. It is installed from `@openai/codex@alpha` [S 7].
    Partner sandboxes: Modal, Cloudflare, Vercel, Daytona, Blaxel, E2B, Runloop, DigitalOcean,
    OCI [S 7].
- **Multi-agent [S 6].** A coordinator creates, messages, waits for and interrupts subagents. The
  default limit is **6 concurrent subagents**, not counting the coordinator. Subagents **share the
  session's environment filesystem**: "Agents that edit the same files must coordinate their
  changes." They inherit MCP tools and web search, but they **can't use function tools**. The
  session-create schema has **no per-subagent model field** [M 28]. So subagents probably run on
  the session's model [I].
- **Handoffs and guardrails.** These are **not Agents API features**. Handoffs, agents-as-tools and
  input/output guardrails with human review belong to the **Agents SDK** [S 22,23]. None of the
  Agents API guide pages I downloaded mention guardrails or approvals [M]. Human-in-the-loop would
  have to go through function-tool round-trips or `requires_action` [I].
- **Tools [S 1,2,3,10].**
  - Function tools, remote MCP, web search, programmatic tool calling, tool search, skills/plugins
    and vaults for MCP credentials.
  - With an environment, you also get built-in Bash, apply-patch and stdio MCP servers.
  - MCP can connect from OpenAI's side or from inside your environment.
- **Observability [S 12].** Usage is recorded per turn and per subagent, but it is best-effort and
  is not a bill. Traces can be exported as OTLP JSON.

**How it relates to the other products**

| Product | Relationship | Tag |
|---|---|---|
| Responses API | The low-level model call. The Agents API executor needs `api.responses.write` for inference. Responses has its own **Multi-agent beta** for GPT-5.6 models | [S 2,7,24] |
| Agents SDK (Python/TS) | Open-source library. **You** run the harness in your app and get handoffs, guardrails, sessions and "sandbox agents" (beta; Unix-local, Docker or hosted) | [S 2,21,22] |
| Codex (CLI/cloud) | Uses the same harness. The Agents API's self-hosted executor is a Codex CLI subcommand | [S 1,7] |
| AgentKit / Agent Builder | AgentKit is the umbrella: Agent Builder, ChatKit, Connector Registry and Evals. **Agent Builder shuts down 2026-11-30**; ChatKit stays. Migration goes to the Agents SDK or ChatGPT Workspace Agents | [S 16,25,26; 27 search summary only] |
| Assistants API | **Already shut down 2026-08-26.** Replaced by Responses + Conversations | [S 15,16] |

## 2. Model support

- **Hosted Agents API: OpenAI models only [S+I].**
  - Every example in every Agents API page uses `gpt-6-astra` [M 3–13].
  - `model` is an unconstrained `str`, and there is no `base_url` or provider field [M 28].
  - Billing is "at the selected model's API rates", with inference on your OpenAI key [S 3,7].
  - No page mentions non-OpenAI models [M].
  - Anthropic, Google, DeepSeek, Ollama and OpenRouter therefore **can't be the agent's model**.
    They can only be reached as *tools*: a CLI the shell runs, or an MCP server [I].
- **Agents SDK: yes, through adapters [S 19,20].**
  - Available routes: `set_default_openai_client`, `ModelProvider`, per-agent `Agent.model`,
    `OpenAIChatCompletionsModel` with any OpenAI-compatible `base_url`, and `MultiProvider`
    prefixes `litellm/…` and `any-llm/…`.
  - LiteLLM and any-llm are supported on a "best-effort, beta basis" [S 20].
  - Caveats [S 20]:
    - Tracing fails without an OpenAI key unless you disable it or use a custom processor.
    - Hosted tools (web search, file search) and structured outputs are OpenAI-only.
    - Responses-only features (tool search, programmatic tool calling) are rejected on
      Chat Completions backends.
- **Codex CLI [S 30].**
  - Built-in local providers `ollama` and `lmstudio` (`--oss`).
  - Custom `model_providers.<id>` need `wire_api = "responses"`, the only supported value. So a
    backend that offers only Chat Completions can't be used directly.

| Required pool | Hosted Agents API | Agents SDK (library) | Codex CLI |
|---|---|---|---|
| Claude (Anthropic) | no [I] | LiteLLM, beta [S 20,32] | no [I] |
| Gemini | no [I] | LiteLLM, beta [S 20,32] | no [I] |
| DeepSeek | no [I] | LiteLLM or Chat-Completions client [S 20,32] | only if it offers a Responses API; not verified |
| Meta Muse Spark | no [I] | Chat-Completions client at `api.meta.ai/v1`, or LiteLLM `meta` [S 33,32] | no: Chat Completions only [S 30,33; I] |
| Local Ollama | no [I] | LiteLLM [S 32] | yes, `--oss` [S 30] |
| OpenRouter | no [I] | LiteLLM [S 32] | not verified |
| `claude`/`gemini`/`agy`/`opencode` **CLIs** | Only as shell commands inside a self-hosted environment. The brain is still GPT [S 7; I] | Only if you wrap them as function tools [I] | n/a |

## 3. Pricing, limits, data

**Platform fee: none.** "There are no additional fees for using the Agents API" [S 1]. You pay for
model tokens, OpenAI tools, and container time for hosted sandboxes [S 3,8].

| Model, per 1M tokens (standard, short context) | Input | Cached input | Cache write | Output | Tag |
|---|---|---|---|---|---|
| gpt-6-astra | $10.00 | $1.00 | $12.50 | $50.00 | [S 14] |
| gpt-5.6-sol (promo price until at least 2026-11-21) | $4.00 | $0.40 | $5.00 | $20.00 | [S 14,15] |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $12.00 | [S 14] |
| gpt-5.6-luna | $0.20 | $0.02 | $0.25 | $1.20 | [S 14] |

- **Tier modifiers [S 14,15].**
  - Long context (>272K): input ×2, output ×1.5. For example, Astra goes to $20 in and $75 out.
  - Batch and Flex: −50%. Fast mode: ×2.
  - The session schema accepts `service_tier` values `auto`, `default`, `flex`, `priority` and
    `fast` [M 28]. I haven't tested whether the harness actually honours `flex` [I].
- **Tools and containers [S 14,15].**
  - Web search: $10 per 1k calls, plus search-content tokens at the model rate.
  - Containers (Hosted Shell and Code Interpreter; hosted sandboxes use the same rates): $0.03 /
    $0.12 / $0.48 / $1.92 for 1 / 4 / 16 / 64 GB, **per 20 min**. Since 2026-06-02 this is
    **billed per minute with a 5-minute minimum**.
  - File-search call pricing applies to the Responses API only.
- **Rate limits.**
  - No limits specific to the Agents API are documented [M 18].
  - The usual org tiers apply. Tier 1 is capped at $100/month and Tier 5 at $200,000/month
    [S 18].
  - Harness model calls go through your key, so they probably count against normal model
    RPM/TPM [I].
- **Data [S 3,17].**
  - `/v1/agents`: not used for training; abuse-monitoring logs kept 30 days; application state
    kept **until deleted**.
  - It "does not support Zero Data Retention (ZDR)", even with a self-hosted sandbox.
  - Data residency: **US only**.

## 4. Fit against the 3-level hierarchy

| Requirement | Agents API (hosted) | Tag |
|---|---|---|
| L1 orchestrator (Claude Code, Opus/Fable) | **No.** The harness model must be OpenAI's | [I from §2] |
| L2 phase orchestrators (Claude Code) | **No**, for the same reason | [I] |
| L3 cheap/fast executors | Only OpenAI models, API-billed. Luna at $0.20/$1.20 (flex $0.10/$0.60) is cheap per token, but there's no subscription to amortise | [S 14; I] |
| Run gemini/claude/agy/opencode CLIs | Only indirectly: a self-hosted executor can shell out to them. Windows support for `codex exec-server` isn't documented, and the executor is an alpha build | [S 7; I] |
| DeepSeek / Muse Spark / Ollama / OpenRouter | Not as agent models; only as tools | [I] |
| Git worktree per session | No equivalent. One environment per session, and subagents share it. You would have to map one session to one worktree yourself | [S 6,7; I] |
| Guard-gated merges, resource locks | None built in | [M 3–13; I] |
| Resumable sessions | **Yes, and this is its strongest feature**: durable sessions, steering, saved turns/items, stream recovery | [S 3,11] |
| Survives host reboot | Session state does, because it's hosted. The hosted sandbox expires after 1 h idle. A self-hosted executor dies with the host, and killed commands aren't restarted | [S 8,9] |
| Cross-vendor usage-limit recovery | No. Single vendor, subject to OpenAI tier caps | [S 18; I] |
| Cost efficiency and speed | Good on Luna with flex/cached input. Subagent fan-out multiplies tokens, and usage figures are best-effort | [S 12,14] |

What the project already has covers the rest: worktree isolation, guard-gated `--no-ff` merges,
`resources` locks, the DONE-note protocol, `state.json` plus the NDJSON ledger, and
cross-vendor ladders in `cao/routing.py` [L1].

## 5. Options compared

| Option | What it takes | Gains | Costs / risks | Verdict |
|---|---|---|---|---|
| **(a) Backbone** | Re-host L1/L2 as Agents API sessions; L3 CLIs reached through a self-hosted executor | Managed durability, compaction, subagents, tracing | L1/L2 become GPT, which breaks the Claude-led design. Beta API, alpha executor. No ZDR. Shared-filesystem subagents. Pay-per-token only. Duplicates CAO/unattended-orchestration | **Reject** |
| **(b) One L3/reviewer pool** | Map an `openai` pool to CAO provider `codex` (already in `VALID_PROVIDERS`, missing from `PROVIDER_BY_POOL`) and add `family: "openai"` [M L2] | Real cross-family reviewer. Usage comes from a ChatGPT plan [S 31] or an API key. `codex --oss` can also run Ollama [S 30]. No new infrastructure | Another bill or plan quota (e.g. Plus: 10–100 local messages per 5 h on Sol [S 31]). Custom providers must speak the Responses API [S 30] | **Recommended** |
| **(c) Agents SDK + LiteLLM** | Python/TS library inside our orchestrator | MIT; runs locally; any model via LiteLLM or Chat Completions, including Muse Spark and DeepSeek; handoffs, guardrails, sandbox agents | Re-implements what Claude Code L1/L2 plus CAO already do. The adapters are only best-effort beta [S 20]. SDK is still 0.x (py v0.22.3, js v0.18.0 [M 29]). Sandbox agents are beta, with Unix-local documented for macOS/Linux only [S 21]. Drives model APIs, not CLI agents | **Not now**; see triggers |
| **(d) Don't use** | Nothing | Zero cost and zero surface area | No OpenAI-family reviewer | Acceptable fallback if there's no OpenAI account |

## 6. Recommendation and revisit triggers

**Take option (b). In effect that means option (d) for the Agents API itself.** Keep L1/L2 on
Claude Code and keep CAO/unattended-orchestration as the orchestration layer. Reach OpenAI the same
way as every other pool: through a CLI (`codex`) in its own worktree, with its own quota pool, and
with `family: openai` so the pairing rule can give you a reviewer that is neither Anthropic nor
Google. One idea is cheap enough to borrow: an SDK-style **output guardrail** that checks a DONE
note against the brief's artefact list (gap E5 in the earlier survey) [S 23; I].

Revisit when **any** of these happens:
1. The Agents API reaches **GA** and documents **bring-your-own-model** or a non-OpenAI endpoint.
   That would reopen (a) for L2 or L3.
2. It becomes **ZDR-eligible** or offers **non-US residency**. This matters if repo content is
   sensitive.
3. `codex exec-server` leaves **alpha** and documents **Windows** support. Then hosted durability
   over local worktrees becomes realistic.
4. **Per-subagent model and environment selection** ships. That would give worktree-like
   isolation and a cheap-L3 fan-out inside one session.
5. A **reboot-proof orchestrator** becomes a hard requirement, as in the 2026-09-05 incident. If
   so, compare it with the equivalent hosted runtime from the vendor that runs L1/L2 before
   choosing. I didn't verify that product here.
6. The SDK reaches **1.0** and its **LiteLLM adapter leaves beta**, *and* a needed pool has no
   usable CLI. Then reconsider (c) as a thin API-only L3 runner.

Record the decision as a `Decision` node so it doesn't get argued again.

## 7. Corrections to the earlier second-hand survey (§F)

| Earlier claim | Verdict | Correct statement |
|---|---|---|
| Announcement returned 403 | Confirmed for WebFetch [M] | The page is readable through the browser pane [M 1] |
| Managed Codex harness; "saves its progress"; saved session config, turns and items | **Confirmed** | [S 2] |
| Quoted features: "compaction, multi-agent orchestration, and an optional hosted sandbox" | **Misquoted** | The doc lists compaction, multi-agent orchestration, programmatic tool calling and MCP support. The sandbox can be hosted, self-hosted or absent [S 2,4] |
| **Handoffs** are a first-class Agents API primitive | **Wrong** | Handoffs are an Agents SDK feature. The API uses coordinator-to-subagent delegation, default 6 concurrent [S 6,22] |
| Input/output **guardrails** | **Wrong** for the API | They are SDK-only. No API guardrail or approval primitive is documented [S 23; M] |
| Tools: service tools, function handlers, MCP | **Confirmed, but incomplete** | Also web search, programmatic tool calling, tool search, skills/plugins, vaults and built-in shell/apply-patch [S 1,3,4] |
| SDK is MIT and costs $0 | **Confirmed** | But it is still pre-1.0 [M 29] |
| Agent Builder and Evals unavailable from 2026-11-30 | **Confirmed** | Evals goes read-only 2026-10-31. Prompt objects also shut down 2026-11-30. Assistants API already shut down 2026-08-26 [S 16] |
| Model prices for Luna/Terra/Sol/Astra | **Confirmed** | But Sol's price is promotional, and the survey missed cache-write pricing and the Flex/Batch/Fast tiers [S 14,15] |
| Web search $10 per 1k | **Confirmed** | [S 14] |
| File search $2.50 per 1k plus storage | **Misapplied** | That call pricing is Responses-API-only, and file search isn't a documented Agents API tool [S 14; M] |
| Code Interpreter "$0.03–$1.92 per session" | **Imprecise** | The rate is per 20 min per container by memory size, billed per minute with a 5-min minimum since 2026-06-02. Hosted sandboxes use the same rate [S 8,14,15] |
| Long context "~2×" | **Partly wrong** | Input ×2, output ×1.5 [S 14] |
| No mention of non-OpenAI models | **Confirmed** | Also confirmed by the schema: free-string `model`, no provider or `base_url` field [M 28] |
| "Strictly pay-per-token … the **most expensive** L3 tier" | **Partly wrong** | Pay-per-token is true for the API. But Luna at $0.20/$1.20 (flex half that) is not clearly the most expensive, and through the codex CLI a ChatGPT plan amortises usage [S 14,31] |
| "No way to drive `claude`/`agy`/`opencode` CLIs" | **Wrong as stated** | A self-hosted executor runs arbitrary shell commands on your machine, including CLIs and git. The underlying point still holds: the brain is GPT, and there are no worktree, merge or lock semantics [S 7; I] |
| Server-side durability survives a host reboot | **Only partly** | Session state survives. The hosted sandbox expires after 1 h idle. A self-hosted executor dies with the host [S 8,9] |
| "`providers/codex.py` adapter exists; `role_provider_routing.reviewer` routes there" | **Stale / misleading** | Both are under `skills/unifished-swarm-orchestration/`, which exists at HEAD but is **deleted in the working tree** (uncommitted, 2026-09-18). The adapter builds a **Chat Completions payload**, not a codex-CLI launch. The live path is CAO: `codex` is a valid provider, but `PROVIDER_BY_POOL` has no `openai` pool yet [M L2,L3] |
| *(Omitted)* | **Missing** | Public beta with the `OpenAI-Beta: agents=v1` header; no ZDR; US-only residency; state kept until deleted [S 3,13,17] |

## Sources

1. Introducing the Agents API (2026-09-10): https://openai.com/index/introducing-the-agents-api/. WebFetch 403; read through the browser pane.
2. Agents: compare runtimes: https://developers.openai.com/api/docs/guides/agents
3. Agents API overview: https://developers.openai.com/api/docs/guides/agents-api/overview
4. Architecture: https://developers.openai.com/api/docs/guides/agents-api/architecture
5. Configuring agents: https://developers.openai.com/api/docs/guides/agents-api/configuration
6. Multi-agent (Agents API): https://developers.openai.com/api/docs/guides/agents-api/multi-agent
7. Self-hosted sandboxes: https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted
8. OpenAI-hosted sandboxes: https://developers.openai.com/api/docs/guides/agents-api/environments/openai-hosted
9. Sandbox lifecycle: https://developers.openai.com/api/docs/guides/agents-api/environments/lifecycle
10. MCP connections: https://developers.openai.com/api/docs/guides/agents-api/tools/mcp
11. Run and continue sessions: https://developers.openai.com/api/docs/guides/agents-api/sessions
12. Observability and usage: https://developers.openai.com/api/docs/guides/agents-api/observability
13. Agents API quickstart: https://developers.openai.com/api/docs/guides/agents-api/quickstart
14. API pricing: https://developers.openai.com/api/docs/pricing
15. API changelog: https://developers.openai.com/api/docs/changelog
16. Deprecations: https://developers.openai.com/api/docs/deprecations
17. Data controls: https://developers.openai.com/api/docs/guides/your-data
18. Rate limits: https://developers.openai.com/api/docs/guides/rate-limits
19. Agents SDK models and providers: https://developers.openai.com/api/docs/guides/agents/models
20. Agents SDK (Python) models: https://openai.github.io/openai-agents-python/models/
21. SDK sandbox agents: https://developers.openai.com/api/docs/guides/agents/sandboxes
22. SDK orchestration and handoffs: https://developers.openai.com/api/docs/guides/agents/orchestration
23. SDK guardrails and human review: https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
24. Responses API multi-agent (beta): https://developers.openai.com/api/docs/guides/responses-multi-agent
25. Agent Builder: https://developers.openai.com/api/docs/guides/agent-builder
26. Migrate from Agent Builder: https://developers.openai.com/api/docs/guides/agent-builder/migrate-from-agent-builder
27. Introducing AgentKit: https://openai.com/index/introducing-agentkit/. Search-result summary only; not fetched.
28. openai-python `session_create_params.py`: https://github.com/openai/openai-python/blob/main/src/openai/types/beta/agents/session_create_params.py
29. GitHub API metadata (2026-09-18): https://github.com/openai/openai-agents-python (MIT, v0.22.3); https://github.com/openai/openai-agents-js (MIT, v0.18.0); https://github.com/openai/codex (Apache-2.0)
30. Codex config reference: https://developers.openai.com/codex/config-file/config-reference
31. Codex plans and limits: https://learn.chatgpt.com/docs/pricing
32. LiteLLM providers: https://docs.litellm.ai/docs/providers
33. Meta Model API overview: https://dev.meta.ai/docs/overview
- L1. Local: `agent-skills/docs/superpowers/plans/2026-09-17-agent-orchestration-and-autoos-merge-SURVEY.md` §F
- L2. Local: `agent-skills/skills/unattended-orchestration/cao/profiles.py` (`PROVIDER_BY_POOL`, `VALID_PROVIDERS`)
- L3. Local: `agent-skills/skills/unifished-swarm-orchestration/custom_orchestration/{providers/codex.py, agent_orchestration.config.yaml}` at HEAD; deleted in the working tree
