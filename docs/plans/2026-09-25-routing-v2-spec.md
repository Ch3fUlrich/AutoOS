# Spec — routing v2, one model registry, orchestration skill v2

Status: **draft for operator sign-off** · 2026-09-25 · decisions D1–D20 come from the operator Q&A
of that day; research evidence is in §12. Supersedes the routing parts of
[model-routing-overhaul-DRAFT.md](model-routing-overhaul-DRAFT.md) and extends
[ADR 0006](../decisions/0006-launch-time-routing-resolver.md) (the launch-time resolver stays the
only place that picks a model).

## 1. Goal

Every agent task runs on the **cheapest model that reliably produces a verified result**, chosen by a
deterministic, explainable algorithm. Every model fact lives in **one file**. Agents talk through
**one terse protocol**. Every lesson learned becomes a **rule** that the tooling can serve.

Non-goals: a learned (ML) router; per-request model switching inside a running agent; a response
cache; adopting A2A before a real cross-vendor boundary exists.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| D1 | Implementer | Free/cheap models chosen by computed complexity. Inputs: time, speed, cost, usable context, reasoning effort. Claude only on escalation, Sonnet before Opus. |
| D2 | Review / close | By risk. High (security, secrets, user config, deletion, CI) → Sonnet closes + 2 cross-family API reviews. Otherwise → 1 cross-family API review. A reviewer's family is never the writer's. |
| D3 | Registry | One data file + the resolver, exposed as a CLI and an MCP tool; a generated markdown page for humans. |
| D4 | Time | Tie-break among options within 10% of the best cost + opt-in defer for tasks marked `deferrable`. Blocking work never waits. |
| D5 | Features | Measured by the resolver (git, Serena/Omnigraph, token counts over declared paths). The orchestrator supplies `kind`, `risk`, `deferrable` and confirms. |
| D6 | Knob | One mode: `cost-first` / `balanced` (default) / `quality-first` + manual override. |
| D7 | Decompose | Split S3/S4 only when planning cost + N cheap subtasks < one mid-tier run. Tightly coupled changes stay whole. |
| D8 | Usable context | 50% of advertised until a probe measures the model. Filter: `need × 1.3 ≤ usable`. |
| D9 | Tokens | Terse fixed-field messages, short skill text; code context is never compressed. |
| D10 | Lessons | Every bug hit while orchestrating becomes a deduplicated rule in the skill; later served by a script/MCP tool instead of loaded whole. |
| D11 | Single source | The registry is the only hand-edited model file; everything else is generated or reads it. CI fails on drift. |
| D12 | Skills | No copy-paste into briefs. A brief names the skills to use; every agent client discovers `.agents/skills/` natively. |
| D13 | Qoder / agy | Keep the CLIs **and** connect both accounts as OmniRoute OAuth providers (operator accepts the terms risk; sign-in is an operator step). |
| D14 | Order | Registry + resolver → skill v2 → messaging. |
| D15 | Gateway | Keep OmniRoute. Fix effort fidelity on our side (§7), not by bypassing it. |
| D16 | Handoff | Per-model caps (§8.3), checkpoint every wave, replaced by probe results. |
| D17 | Messaging | MCP Tasks lifecycle with A2A-compatible names; A2A adapter later. |
| D18 | Probe spend | Free legs only. Paid-only models keep the D8 default until measured another way. |
| D19 | RTK | OmniRoute RTK on tool output only after an A/B shows savings with no lost failure line. Off until then. |
| D20 | Evidence first | Nothing enters the skill rules, the MCP tools, the registry `policy` or the resolver without evidence: a test that pins it or a recorded measurement. A value still at `source: default` is shown as unmeasured in every `--explain` line; a rule without a `source` fails `skill-rules.py check`. |

## 3. The registry — `catalog/ai-registry.json`

### 3.1 Sections

| Section | One entry per | Fields (all required unless marked) |
|---|---|---|
| `providers` | upstream provider | `id`, `omniroute_id`, `litellm_env`, `litellm_prefix`, `api_base`?, `provider_data`?, `trains_on_prompts` (bool), `tier` (`free`/`paid`/`subscription`), `windows`? (§6.4) |
| `models` | upstream model | `id`, `family` (`anthropic`/`google`/`deepseek`/`meta`/`qwen`/`openai-oss`/`mistral`/`cohere`/…), `context_advertised`, `context_usable` (`{tokens, source: default\|probe}`), `output_max`, `reasoning` (bool), `effort_ladder` (ordered list, e.g. `["none","low","medium","high","xhigh"]`), `tool_calls` (`proven`/`unproven`/`broken`), `price_in`, `price_out`, `price_cache_read`?, `client_bound`? (e.g. `opencode` for Zen free legs), `direct`? (today's `llm-models.json` direct-provider block) |
| `routes` | combo / fallback group | `id`, `class` (`free`/`cheap`/`mid`/`frontier`), `strategy`, `legs` (ordered `provider/model` refs), `surfaces` (per surface: `omniroute`/`litellm`/`opencode`/`zed`/`openhands` → display name, context/output declared, effort default, OpenHands profile fields), `retired`? |
| `clients` | agent CLI | `id`, `binary`, `gateway_mode` (`omniroute-run`/`native`/`none`), `skills_dir`, `supports_effort`, `signin_check` |
| `policy` | — | modes and thresholds (§5.4), bucket table (§5.2), effort defaults (§5.5), handoff caps (§8.3), risk rules (§5.7), seed priors and latency seeds (§5.6), `verify_tokens` and `brief_tokens` per bucket (§5.3) |

Rules (all checked by `registry.py check`):
- every `legs` entry resolves to a `models` × `providers` pair; ids are unique across sections;
- `privacy=sensitive` routes contain only legs whose provider has `trains_on_prompts: false`;
- `api_base` holds only a public vendor endpoint; private hosts, IPs and usernames are rejected;
- no field carries a date or machine value that would make a render differ between machines or days.

**Measured values never write the registry.** Probes and `recalibrate` write the git-ignored overlay
`logs/routing/measured.json`, which the resolver merges at load time (overlay wins, the `source` field
says so). `registry.py promote` turns chosen overlay values into a registry diff for a reviewed
commit, so the registry stays the only hand-edited file and renders stay reproducible.

### 3.2 What it replaces (D11)

| Today (hand-edited) | After |
|---|---|
| `catalog/llm-models.json` | folded into `models` (+ `direct` block) — file removed |
| `catalog/providers.json` | folded into `providers` — file removed |
| `catalog/ide-models.json` | folded into `routes.surfaces` / `display_name` — file removed |
| `configuration/omniroute/combos.json` | **generated** (apply.sh/.ps1 keep reading it) |
| `configuration/litellm/config.yaml` model groups | **generated** |
| `opencode.jsonc` AUTOOS-MANAGED blocks, Zed lists | generated (existing `sync-ide-models.py` retargeted) |
| `configuration/openhands/tier-profiles.json` | generated |
| model tables in `docs/models.md` | generated section between markers; prose stays hand-written |
| `.agents/skills/unattended-orchestration/provider-windows.json` | folded into `providers.<id>.windows`; `provider_windows.py` reads the registry |

Migration is two-phase, with no deletion before equality is proven:
1. Add the registry plus a field-level mapping (old file · old field → registry path). `registry.py
   validate` proves every entry of the three old catalogs maps to a target, and `render` output equals
   today's generated files semantically (parsed, key order ignored).
2. Only then switch consumers, delete the old catalogs and make CI require the drift check. Rollback =
   revert that commit.

Consumers that can read JSON read the registry directly. A file is generated only when a third-party
tool dictates its format. Generated files start with a "generated from catalog/ai-registry.json — do
not edit" line where the format allows comments. `tools/registry.py check` (CI, both suites) fails
when any generated file differs from a fresh render.

### 3.3 Not in the registry

Keys (stay in git-ignored `configuration/api-keys.yml`), hostnames, and the **track record** (machine
data, §5.6) — the registry holds only seed priors.

## 4. Task card v2

```
kind        implement | debug | review | plan | bulk | research     (orchestrator)
risk        normal | high                                           (orchestrator; §5.7 may raise it)
paths       files/dirs the task may touch                           (orchestrator; feeds §5.1)
spec        exact | partial | vague                                 (orchestrator)
privacy     public | sensitive                                      (default public)
deferrable  bool, deadline?                                         (default false)
mode        cost-first | balanced | quality-first                   (default balanced)
override    route/client/effort pinned by the operator              (optional; logged)
```

An override is applied **after** the hard filters: it can pick any route that survived them, never one
that privacy, usable context or sign-in removed.

v1 cards stay valid: `complexity` maps to the bucket, `role` to `kind`, `ctx` to a minimum
`context_usable`. Unknown fields remain an error.

## 5. The algorithm (resolver v2)

Two functions:
- `measure(card, repo, clients) → features, client_state` does the I/O: git, Serena/Omnigraph, token
  counts, each client's sign-in check.
- `plan(card, features, client_state, registry, overlay, track_record, orchestrator_model, now) →
  route_plan` is pure (no I/O, no clock of its own). A missing input fails closed.

Output is JSON plus a one-line `reason` for every choice.

### 5.1 Features (measured, D5)

| Feature | How |
|---|---|
| `files` | count of files under `paths` the task changes (declared list, else git-tracked files in `paths`) |
| `modules` | distinct top-level directories among them |
| `fanout` | max reference count of symbols named in the brief (Serena `find_referencing_symbols`, else Omnigraph, else grep) |
| `lines` | estimated changed lines (declared, else 20 × files) |
| `tests` | a test file covers the touched path (name match or suite reference) |
| `need_tokens` | token count of files to read + brief + tool schemas of the chosen client |

### 5.2 Complexity bucket (initial weights, recalibrated from the track record)

| Feature | Points |
|---|---|
| files | 1 → 0 · 2 → 1 · 3–5 → 2 · 6–10 → 3 · >10 → 4 |
| modules | 1 → 0 · 2 → 1 · ≥3 → 2 |
| fanout | <5 → 0 · 5–20 → 1 · >20 → 2 |
| lines | <30 → 0 · 30–150 → 1 · 150–500 → 2 · >500 → 3 |
| spec | exact → 0 · partial → 1 · vague → 2 |
| tests | exist → 0 · missing → 1 |
| kind | implement, bulk, review, research → 0 · debug → 2 · plan → 3 |

Sum (0–17) → **S0** 0–1 · **S1** 2–3 · **S2** 4–6 · **S3** 7–9 · **S4** ≥10. Golden tests pin every
boundary.

### 5.3 Steps

1. **Filter** routes. Route-level filters remove the whole route: privacy (§3.1 rule — a leg that
   trains on prompts is never a fallback for a `privacy=sensitive` task; sensitive work falls through
   only to other clean legs); client installed **and** signed in; retired; mode budget cap.
   Per-leg filters skip a leg and let the combo fall through to the next one (operator 2026-09-26):
   `need_tokens × 1.3 ≤ context_usable`; `tool_calls = proven` for agentic kinds; a rate-limited
   unproven leg; `client_bound` legs only through their client. A route is removed only when no leg
   is usable. The expected serving leg is the first usable one. Filters are never relaxed later.
   **No route survives** → `route_plan.route = null`, state `input_required`, and a reason naming each
   filter that removed a candidate (sign in, narrow `paths`, split the task, or override).
2. **Bucket** (§5.2).
3. **Decompose** (D7), S3/S4 only, one level deep (subtasks are never decomposed again): the
   orchestrator proposes subtask cards; `plan` scores each. Split when
   `n × policy.brief_tokens × orchestrator price + Σ E[cost](subtask) < E[cost](whole)`; then return
   `decompose` with the subtask plans.
4. **Score** each remaining route `r` at effort `e`, in USD:
   `E[cost] = (C_attempt + C_verify) / p + λ_mode × T / p`
   - `C_attempt` = estimated tokens × price of the route's **expected serving leg** (its first leg that
     passed the filters; priority strategy), 0 for a free leg;
   - `C_verify` = `policy.verify_tokens[bucket]` × `orchestrator_model` price — this is what makes an
     unreliable free route expensive;
   - `T` = median minutes from the track record, else `policy.latency_seed[class]`;
   - `p` = §5.6.
5. **Pick** the lowest `E[cost]` among routes with `p ≥ θ_mode`; if none reaches θ, the route with the
   highest `p` (still inside the filters) and the reason says θ was missed.
6. **Time** (D4): among picks within 10% of the best, prefer a provider in its cheap window or with the
   most quota headroom; if `deferrable` and the best provider's cheap window starts before the
   deadline, return `defer_until`.
7. **Emit** `route_plan`: `route`, `class`, `client`, `effort`, `max_tokens`, `context_budget`,
   `reviewers`, `escalation` (next two steps), `reason`.

### 5.4 Modes

| Mode | θ (min success) | λ (USD per minute of wall-clock) |
|---|---|---|
| cost-first | 0.60 | 0 |
| balanced | 0.80 | 0.01 |
| quality-first | 0.95 | 0.05 |

Initial values in `policy`; `recalibrate` proposes new ones.

### 5.5 Effort (its own axis)

`effort(bucket, kind, class, ladder)` is total; first matching row wins:

| Condition | Effort |
|---|---|
| leg is not a reasoning model | none (field not sent) |
| kind = plan or debug | high |
| S4 and class = frontier | top rung of the ladder |
| S3 or S4 | high |
| S1 or S2 | medium |
| S0 or kind = bulk | low |

- The result is clamped to the nearest rung the leg's `effort_ladder` has; a rung the model lacks is
  never sent.
- Reasoning models: `max_tokens ≥ 48k` at any effort (measured: 16k is exhausted by reasoning alone),
  `≥ 64k` at high and above, capped by `output_max`.
- Briefs state scope and a stop condition; they never say "think deeply" (it inflates reasoning tokens
  without a correctness gain).

### 5.6 Track record

- Git-ignored `logs/routing/track-record.jsonl`, one line per finished run: route, class, served leg,
  bucket, effort, tokens in/out, cost, latency, gate result (§5.7), failure class.
- `p(r,bucket,e) = (α + passes) / (α + β + passes + failures)`, with `{α, β}` from
  `policy.seed_priors[class][bucket]`; observations are counted per route and fall back to the class.
- Seed priors from 2026-09-25 measurements: free/cheap routes pass S0–S1 (single file, exact spec) and
  fail S2+ (4/4 multi-file failures with fabricated reports or partial edits).
- `tools/registry.py recalibrate` refits the bucket weights and θ checks; it runs by hand or when a
  route's legs change, never silently.

### 5.7 Gate, escalation, review

- **One pass bar for every route:** NO-OP guard; diff stays inside `paths`; the report's claims match the
  diff; tests for the touched parts pass; lint/shellcheck/PSScriptAnalyzer clean on touched files.
- **Failure classes:** `logic` (wrong result, failing test) → effort +1 first; `capability`
  (fabricated report, no edit, broken tool calls, context overflow) → next route class.
- At most 2 escalation steps, then Sonnet; Opus only for S4 or after Sonnet fails.
- **Risk** is raised to `high` automatically when the diff touches secrets handling, user
  PATH/profile/config writers, deletion paths, CI workflows or security settings. The raise happens at
  the gate, after the run: the route is not recomputed, but the high-risk review set applies.
- Terminal state: after the last escalation step fails, the task becomes `failed` with the failure
  history; the orchestrator decides (split, rewrite the brief, or take it itself).
- **Review:** `normal` → 1 API review; `high` → 2 API reviews + Sonnet closes. Reviewers run at low
  effort, `max_tokens` 48k.

## 6. Interfaces

### 6.1 CLI

- `tools/autoos-agent.py route --card … [--explain]` prints the `route_plan`; `run` takes card v2.
- `tools/autoos-agent.py context [--transcript PATH]` prints the calling session's context fill:
  tokens = input + cache-read + cache-creation of the latest assistant usage record in the Claude Code
  transcript JSONL (other clients: their own session log, or `unknown`), the cap for the model (§8.3)
  and the percentage. Tested on fixture transcripts.
- `tools/registry.py check|validate|render|probe|recalibrate|promote`.

### 6.2 MCP (`autoos-agent` server)

`route(card)`, `list_agents()`, `context()`, `respond(task_id, text)` plus the existing
`spawn/status/result/cancel` moved onto the §9 lifecycle. `list_agents` returns a plain projection of
the registry (client, routes, usable + reason); the A2A Agent Card mapping (url, capabilities,
input/output modes, skills, auth) is specified when the adapter is built.

### 6.3 Human page

`docs/models.md` generated section: routes, classes, legs, usable context, effort ladder, prices,
windows.

### 6.4 Provider windows

`providers.<id>.windows`: list of `{days, utc_from, utc_to, price_factor}` (e.g. DeepSeek off-peak
half price, verified 2026-09-24 in ADR 0006). Time never overrides a hard filter.

## 7. Gateway (OmniRoute) configuration

- Effort ladders come from our registry and drive every client (opencode variants, Zed defaults,
  spawner `reasoning_effort`). OmniRoute passes effort through unchanged on the openrouter leg
  (measured); its built-in model table lacks Muse Spark 1.3 and DeepSeek v4.1 — send a data-only
  upstream PR.
- OmniRoute's native Gemini translator maps `high`/`xhigh`/`max` to one budget: routes that need more
  than `high` on Gemini put the openrouter leg first.
- Forward a stable session id so OpenRouter's sticky routing keeps prompt-cache hits across turns.
- Response cache stays off. Prompt-cache passthrough stays on. Compression off until D19's A/B.
- Qoder and Antigravity accounts added as OAuth providers (operator sign-in), then referenced as legs.

## 8. Orchestration skill v2

### 8.1 Rules (D10)

- The skill body is a numbered rule list grouped by topic (`spawn`, `brief`, `review`, `git`, `tests`,
  `gateway`, `handoff`). One rule = one line: `R-<topic>-<nn>: <imperative>. (why: <≤12 words>;
  source: <date or commit>)`.
- `tools/skill-rules.py check` (CI) fails on duplicate ids, near-duplicate text, rules over 200
  characters, or a missing source.
- Every bug hit while orchestrating becomes a rule in the same change that fixes it.
- A rule's `source` names the test or measurement that proves it (D20); an untested lesson waits in
  the DONE note until it has one.
- Phase 2: `skill-rules.py query --topic …` and an MCP tool return only matching rules; `SKILL.md`
  keeps the index.

### 8.2 Terse protocol (D9)

Briefs and reports are fixed-field, no prose:

```
BRIEF  id · goal · paths · spec · done-when · skills: <names> · effort · budget
REPORT id · status (completed|failed|input_required) · files · tests (cmd → result) · blockers · lessons
```

Bulky output (logs, diffs, test output) goes to files under the git-ignored run folder; messages carry
the path.

### 8.3 Orchestrator handoff (D16)

| Orchestrator model | Hand off at |
|---|---|
| Claude Opus, 1M window | 400k tokens (40%) |
| Muse Spark, 1M window | 300k (30%) |
| Gemini, 1M window | 200k (20%) |
| 200k-class | 150k (75%) |

- At half the cap: offload tool output to files, keep only status lines.
- A state file (goal, decisions + why, open questions, per-lane state, failure history) is rewritten
  every wave, so a handoff is possible at any moment.
- Fill level is read from the session transcript's latest usage record (`autoos-agent context`).
- The recall probe (§10) replaces a default when it lands.

### 8.4 Skills for every agent (D12)

The installer links `.agents/skills/` into each client's native skills directory (the way
`.claude/skills/` works today); briefs list skill names only. API-only reviewers get the named review
section loaded by the review script at run time.

## 9. Messaging (D17)

- Task states: `submitted`, `working`, `input_required`, `completed`, `failed`, `canceled`,
  `rejected` (A2A names).
- The `autoos-agent` MCP server implements the MCP Tasks lifecycle (tasks/get, tasks/result,
  tasks/cancel). If a client lacks Tasks support, `status/result/cancel` tools expose the same states.
- Ask-back: a worker writes a question file → the task enters `input_required` → the orchestrator
  answers with `respond(task_id, text)`.
- State names already match A2A, and `list_agents` is generated from the registry, so an A2A adapter is
  a thin layer later.
- Content (briefs, reports, diffs) stays in git-ignored files.

## 10. Probes (free legs only, D18)

| Probe | Measures | Writes |
|---|---|---|
| recall | multi-needle recall at 32k/128k/256k/500k | overlay `context_usable` (source `probe`) |
| effort | reasoning tokens + pass rate per rung, n ≥ 5 | overlay; `promote` proposes the `effort_ladder` |
| RTK A/B | tokens and gate result with RTK off/on on the same tasks | D19 decision |

Each probe logs its token use; a probe that would hit a paid leg refuses.

## 11. Acceptance

- `registry.py check` green in CI on both platforms; every former hand-edited file is generated or
  removed; the audit reports no drift.
- Resolver unit tests: every bucket boundary, every filter, decomposition, defer, clamp to the ladder,
  escalation classes; an explain line for every output.
- Committed fixture `tests/fixtures/routing-2026-09-25.json` (the recorded tasks, their features and
  outcomes; no hostnames or paths): `plan` with seed priors plus that fixture reproduces the measured
  outcomes — single-file exact tasks → free/cheap; multi-file → cheap-paid or decompose; no route that
  fabricated a report.
- Migration phase 1 gate (§3.2) passes before any old file is deleted.
- Skill: `skill-rules.py check` green; every lesson from `logs/handoff-sessions/` DONE notes present
  as a rule.
- CI: each client's `skills_dir` link exists and resolves to `.agents/skills/`. Live "client lists the
  skills" checks are an operator step, recorded in the DONE note.
- Idempotence: a second `render` changes nothing; a second install reports `skipped`.

## 12. Evidence

- Routing research 2026-09-25 (RouteLLM, FrugalGPT, AutoMix, Arch-Router, Copilot/Cursor Auto, Azure
  model router, OpenRouter auto): cascades with a cheap verifier beat a-priori classifiers when
  verification is cheap; per-model performance priors are the strongest routing signal; no system
  fuses model, effort and context.
- Effort: turning reasoning on matters more than the rung; "think deeply" prompts add reasoning tokens
  with no correctness gain.
- Context: MRCR v2 8-needle — Opus 4.6 91.9% @256k → 78.3% @1M; Opus 4.7 59.2% @256k → 32.2% @1M;
  Gemini 3.1 Pro 84.9% @128k → 26.3% @1M (secondary sources for the Opus figures). Claude Code
  auto-compacts at ~97% of 1M. Clearing old tool results: +29% task success, −84% tokens (Anthropic).
- OmniRoute (source + 7 live probes + 941 call logs): effort passed through on the openrouter leg;
  model table missing spark-1.3 / deepseek-v4.1; Gemini translator collapses high/xhigh/max; response
  cache and compression off; every logged request carried effort `high` (clients clamp).
- A2A v1.0.1 (Linux Foundation): no CLI coding agent speaks it natively; MCP 2025-11-25 Tasks covers
  the same lifecycle.
- Not gathered: community (Reddit/HN) reports on OmniRoute — search quota ran out.
- This spec was reviewed by deepseek-v4.1-flash and qwen3.8-27b (free) through the gateway; 18 distinct
  findings, all applied except one narrowed (public vendor `api_base` stays, private hosts rejected).
  The gemini-3.8-flash review failed: native free key cooling down (429) and the OpenRouter connection
  reported "credits exhausted" (402/401) — every paid openrouter leg is down until it is topped up.
