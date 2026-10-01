# opencode-DIRECT fallback ladder — 2026-10-01 (L0/operator item 2)

**Lane:** `ws-fallback` · run `ws-omniroute-20260930` (L1-beta)
**Branch:** `L1-backlog/ws-fallback-20261001` · **Base:** `origin/main` `88359146` (at branch time; main is a moving target — see §6)
**Purpose:** when the omniroute combos fail, a lane needs model refs it can spawn on **inside opencode**, without the gateway proxying. Recorded from live probes this session; nothing here is inferred.

---

## 1. Why a direct ladder exists

Implementations, reviews and research run **inside opencode**. The omniroute combos are served by the gateway on `:20128`; when that is down/gated all combos fail together. The opencode Zen free tier and the operator's personal BYOK keys do **not** go through `:20128` — opencode calls the vendor directly. So they are the honest last resort. **Losing the gateway also loses its combo policy, admission control and the 429 backoff** — a lane on this ladder is unmanaged.

## 2. Probes (tiny ack + one tool call where applicable)

Each probe spawned one tiny subagent on the ref; it ran `echo PROBE-<x>-OK` and reported the output.

| Ref | Result | Family | Evidence |
|---|---|---|---|
| `opencode/longcat-2.5-preview-free` | **OK** | Meituan (Longcat) | subagent `ses_f09849d36ffem1P7eNAJMjb1al` completed a real read-only review (file + git tool calls) |
| `opencode/space-bunny-free` | **OK** | Space Bunny | subagent `ses_f096fb1b3ffeo63OYaIPXd2pqR` ran the echo tool call |
| `opencode/mimo-v2.6-flash-free` | **OK** | Mimo (Xiaomi) | subagent `ses_f096fb1b2ffeE4UGQksx7jDwDS` ran the echo tool call |
| `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | **OK** | NVIDIA | subagent `ses_f096f887fffewNDgGblugLr5vR` ran the echo tool call |
| `openrouter/qwen/qwen3.8-27b:free` | **OK** | Qwen (Alibaba) | subagent `ses_f096f887effeRRR8JWer7z5RwM` ran the echo tool call |
| `meta/muse-spark-1.3` | **FAIL** | Meta | spawn error verbatim: `META_API_KEY is not set` |
| `litellm/t2-worker` | **FAIL** | (mapped) | spawn error verbatim: `/chat/completions: Invalid model name passed in model=t2-worker` |
| `ollama/qwen2.5-coder:7b` | **FAIL (opencode ref)** | Qwen (local) | spawn error verbatim: `ConnectionRefused: Unable to connect` |

### 2a. Two failures are config defects, not dead services

- **litellm** — the live server serves `tier1, tier1-paid, tier2, tier2-paid, tier3, tier3-paid, rag` (`GET http://127.0.0.1:4000/v1/models`). The opencode block declares `t2-worker`, which the server rejects → the correct ref is **`litellm/tier2`**.
- **ollama** — the daemon is **UP**: `GET http://127.0.0.1:11434/api/tags` → 11 models incl. `qwen2.5-coder:7b`, and a direct `POST /api/generate` answered `OK` in **54 s** (cold load). The opencode ref failed only because the per-user provider sets `baseURL: http://host.docker.internal:11434/v1`, which does **not resolve on the host** (it is a container-only name). Fix: `http://127.0.0.1:11434/v1`.
- **meta direct** is genuinely unavailable until `META_API_KEY` is set.

## 3. The ladder (first that works)

1. **omniroute combos** — the gateway default; combo policy + 429 backoff apply.
2. **openrouter — `:free` ONLY.** The operator has **NO openrouter credit**; every paid openrouter leg is denied. Probed OK: `openrouter/nvidia/nemotron-3-super-120b-a12b:free`, `openrouter/qwen/qwen3.8-27b:free`. Also available `:free`: `openrouter/cohere/north-mini-code:free`, `openrouter/poolside/laguna-s-2.1:free`. Personal key; direct.
3. **opencode Zen free** — `opencode/longcat-2.5-preview-free`, `opencode/space-bunny-free`, `opencode/mimo-v2.6-flash-free`, `opencode/muse-spark-free`, … (Zen free tier; direct).
4. **meta direct** — `meta/muse-spark-1.3` — **DOWN** (`META_API_KEY` unset). Paid + **trains on prompts**.
5. **litellm** — **`litellm/tier2`** (not the config's `t2-worker`).
6. **ollama (local, offline)** — `ollama/qwen2.5-coder:7b` (after the baseURL fix), plus `qwen3:30b`, `hermes3:8b`.

### Rules

- **In-opencode usage is not gateway proxying.** These refs are served by the opencode client directly; `:20128`, the combos, admission control and the 429 backoff are all bypassed.
- **public-only where the model trains.** `meta/muse-spark-1.3` (contributor tier, trains), `free_ai/*` (trains), and any `:free` id whose provider states training. Never send private/sensitive context.
- **Cost:** openrouter = `:free` only (no credit). meta = paid, sparingly. ollama = local/offline.

## 4. Record for the records doc — the 7 paid openrouter route legs

The registry `routes.*.legs` still carried **7 paid openrouter legs** before TORDER-OR
(measured from the operator's list; leg → route):

1. `openrouter/google/gemini-3.8-flash` → `gemini-3.8-flash`
2. `openrouter/meta/muse-spark-1.3-contributor` → `spark-1.3-contributor`
3. `openrouter/meta/muse-spark-1.3-contributor` → `t1-orchestrator-clean`
4. `openrouter/deepseek/deepseek-v4.1-flash` → `t2-orchestrator`
5. `openrouter/deepseek/deepseek-v4.1-flash` → `t2-worker-clean`
6. `openrouter/deepseek/deepseek-v4.1-flash` → `t2-worker`
7. `openrouter/openai/gpt-oss-120b` → `t2-worker`

**TORDER/alpha fixes them + the gate literal.** Verified present on the TORDER lane:
`c4c3654b` ("TORDER-OR openrouter NO credits free-only — drop 7 paid legs, keep
t1-clean declared+gated, provider stays available") on `L1-backlog/ws-tier-order-20261001`.
The gate literal (`policy` + `routes.*.unavailable_legs`) is the enforcement; do not
merge the combos lineage until TORDER lands and the gates are green.

## 5. Files changed

- this doc (new)
- `CHANGELOG.md` — one `## [Unreleased]` bullet
- lane-brief templates (run-local): `L1b-brief.md`, `L1b-nebius-wave-brief.md` — ladder refs added

No repo config was changed (ollama/meta/litellm fixes are host/per-user, flagged below,
not edited); therefore no test change is required.

## 6. Flags for the operator

- **Secret (no value printed).** The per-user `%USERPROFILE%\.config\opencode\opencode.json` carries a **plaintext `openrouter` `apiKey`** (AGENTS.md rule 1 / R-orch-27: name-only). It is not tracked, but any transcript that prints it is a rotation event. Recommend rotating and switching to `{env:OPENROUTER_API_KEY}`.
- **Host config fixes not applied here (per-user, operator-owned):** ollama `baseURL` → `http://127.0.0.1:11434/v1`; litellm model ref → `litellm/tier2`; set `META_API_KEY` if meta direct is wanted.
- **`origin/main` moved again** during this task: `eae75811` → `88359146` ("Take L1-backlog/reviewgate-2fam d0f70f1"). The merge-checklist refresh was measured at `eae75811`; re-check the conflict map against `88359146`.
