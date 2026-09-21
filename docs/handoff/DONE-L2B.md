# DONE — L2B: router tier sync + capability tests

Branch `merge/router-sync-openhands`, cut from `main` 7cc809c in its own
worktree. **Not merged into main.**

## 1. Landed

| File | Change |
| --- | --- |
| `tools/sync-router-tiers.py` | new — mirrors `combos.json` tier legs into the LiteLLM fallback |
| `configuration/litellm/config.yaml` | tier2/tier3 wrapped in `# AUTOOS-MANAGED-START/END` markers and resynced |
| `tests/run-tests.ps1`, `tests/run-tests.sh` | drop the now-wrong "any cerebras leg is stale" assertion (see §2) |
| `docs/models.md` | note that `combos.json` is the source and the tool keeps config.yaml mirrored |
| `docs/handoff/DONE-L2B.md` | this file |

`tools/sync-router-tiers.py` reads `configuration/omniroute/combos.json`
(tier2 + tier3) and regenerates **only** the bytes between the markers in
`config.yaml`. Everything outside the markers — header prose, tier1,
tier1-paid, tier2-paid, tier3-paid, both settings blocks, every comment and all
whitespace — is left untouched (asserted in testing below; the outside-marker
line list is byte-identical before and after).

Markers are placed **inside** each tier's existing block on purpose: the
`# ── tierN` header comment and the blank line that separates tiers stay manual.
A `rpm:` cap (and its comment) on a leg that survives a resync is preserved;
`model`, `api_key` and `api_base` are regenerated, because those are exactly
what drifts.

Provider mapping used (`combos.json` ref → LiteLLM):

| OmniRoute provider | LiteLLM | key env / api_base |
| --- | --- | --- |
| `opencode-zen` | `openai` | `OPENCODE_ZEN_API_KEY` / `https://opencode.ai/zen/v1` |
| `cheaperinference` | `openai` | `CHEAPINFERENCE_API_KEY` / `https://api.cheaperinference.com/v1` |
| everything else | same prefix | native LiteLLM env var |

The cheaperinference host is not guessed: `open-sse/executors/cheaperinference.ts`
documents it as `api.cheaperinference.com`. `CHEAPINFERENCE_API_KEY` has no
entry in `configuration/litellm/.env.example` yet — **follow-up** (§5).

Flags: `--check` (exit 1 + unified diff on drift, changes nothing), default
`--write`, plus `--combos` / `--config` / `--quiet`. Exit 2 = unusable input
(marker missing, doubled, mismatched, or file unreadable). No secret file is
opened; only model names and env-var *names* are printed.

### Verified

- `--check` → exit 0 (`OK: tier2, tier3 match combos.json`); run twice → second
  run reports `Already in sync`, not `installed` (AGENTS.md §4).
- `diff` after two runs is identical (idempotent).
- `ruff check` + `ruff format --check` clean.
- YAML parses; leg counts `tier1=3, tier1-paid=2, tier2=10, tier2-paid=1, tier3=8, tier3-paid=2`
  match `combos.json` exactly.
- Failure paths exercised: missing `END` → exit 2 with the mismatched-marker
  message; missing file → exit 2.
- LF stayed LF after a write (no Windows CRLF translation).
- Both suites: the touched test cases pass — `litellm fallback config is
  internally consistent` in `run-tests.ps1` and its `run-tests.sh` twin.
  (The `litellm installer delegates to pipx when present` failure is
  pre-existing on `main` and unrelated to this track.)

## 2. Drift `--check` found (the reason this exists)

`config.yaml` had drifted from `combos.json` in **both** synced tiers:

- **tier2** was `mistral-small → groq → gemini` (3 legs). It should be
  `gemini-3.8-flash → groq → cerebras → sambanova → cheaperinference ×3 →
  openrouter → deepseek direct → zen` (10 legs). The 2-RPM Mistral leg led the
  tier, and 7 legs were missing entirely.
- **tier3** was `devstral-small → mistral-small → gemini-2.5-flash` (3 legs).
  It should be `mistral-code → groq qwen3.8 → cerebras qwen-3.8 →
  cheaperinference ×2 → mistral-small → deepseek → zen` (8 legs).
  `mistral/devstral-small-latest` and `gemini/gemini-2.5-flash` are no longer
  in any OmniRoute tier.

The resync in this branch closes both.

### The stale test assertion this exposed

Both suites asserted *no* `cerebras` leg may appear in `config.yaml`
(`run-tests.sh` `if "cerebras" in m …`, `run-tests.ps1`
`($models -match 'cerebras').Count -eq 0`). That was written when "Cerebras
killed no-card free" made cerebras stale — but `combos.json` re-admits it as a
credit/paid-capable leg (two legs, 2026-09-20), so the assertion was already
false for the *source of truth* while remaining true only because
`config.yaml` had not been resynced. Left alone, this branch would turn main
red the moment the mirror was corrected.

Fix: the stale check now covers only `llama-3.3-70b` (still genuinely
retired). Cerebras correctness is covered by
`tools/sync-router-tiers.py --check` against `combos.json`, which is the
stronger check — it compares the whole leg list, not one substring. Noted in
both files so the next reader sees why it was loosened.

Both `litellm fallback config is internally consistent` cases pass after the
change.

## 3. Capability: can `opencode run` nest subagents 2 levels?

**Answer: NO by default; YES when `experimental.subagent_depth` is raised — and
the raise is required.**

Default is 1, and `1` means *subagents may not launch subagents*. Evidence,
all from opencode v2.0.12 on this machine:

1. `opencode run --agent tier1-orchestrator` with a prompt to launch
   `tier2-worker`, which is to launch `tier3-reviewer`:
   - `subagent tier2-worker` ran and completed;
   - `tier2-worker` then refused: *"the current session is already a sub-agent
     and the system's sub-agent depth limit prevents nesting another one."*
   - Final output `RESULT=tier1[<that refusal>]`. **No `LEAF-OK`.**
2. The CLI binary carries the reason verbatim:
   `Maximum subagent nesting depth. Defaults to 1, which prevents subagents
   from launching subagents.` and
   `Subagent depth limit reached ({n}). Increase "experimental.subagent_depth"
   to allow nested subagents.`
   (`opencode debug agents` shows `tier1-orchestrator` = `all`,
   `tier2-worker`/`tier3-reviewer` = `subagent`; the `subagent` permissions in
   `opencode.jsonc` were already correct and were not the blocker.)
3. With `"experimental": { "subagent_depth": 2 }` (overlay applied via a
   temp-dir `OPENCODE_CONFIG`, repo config untouched) the same probe returned:

   ```text
   subagent tier2-worker → <subagent sessionID="…" state="completed">
                             tier2[LEAF-OK]
                           </subagent>
   RESULT=tier1[tier2[LEAF-OK]]
   ```

So a real tier1 → tier2 → tier3 chain is reachable, but **only after someone
sets the depth**; the current repo config does not, so the shipped 3-tier
ladder is 1 level deep in practice.

Cost/tokens: every probe returned `cost: 0` (OmniRoute combo pricing is not
reported back to opencode, as `configuration/openhands/config.toml` already
notes). Combo hop observed in the gateway: `meta/muse-spark-1.3-contributor`
(tier1 paid leg). Token counts reported by opencode per outer step:
`tier1` step `in=9042 out=314` with `subagent_depth=2`; `in=11978 out=228`
default. The `tier3` smoke probe answered `PROBE-OK`.

## 4. Capability: OpenHands on `:3000`

**Answers on `:3000`: YES.** `start-stack.ps1 -App openhands` reached
`Gateway OK`, the image `docker.openhands.dev/openhands/openhands:latest`
version `1.11.0` started, and `GET http://localhost:3000` → **200**.

**Loaded LLM configuration**: the deployment env path, not a saved profile.

- `LLM_MODEL=openai/tier1`, `LLM_BASE_URL=http://host.docker.internal:20128/v1`,
  `LLM_API_KEY` set (35 chars) — injected by `start-stack.ps1`.
- Proven end-to-end through OpenHands' *own* bundled LiteLLM (not a raw curl,
  which gives a misleading 401):
  `litellm.completion("openai/tier1", api_base=$LLM_BASE_URL, …)` →
  `OH-OK`, served by `meta/muse-spark-1.3-contributor`. Container → host `:20128`
  path is good.

**Caveat 1 (profile ladder is not actually exercisable today).** The persisted
profile `openhands/openai_tier1` (the one `docs/openhands-runbook.md` names) is
currently **broken**: `GET /api/v1/settings` returns **500**, and the log is
unambiguous:

```text
pydantic ValidationError for Settings
  AgentSettings schema_version 6 is newer than supported version 4
```

`~/.openhands/settings.json` was written by a newer OpenHands than this image
reads. Any core settings/profile route 500s until that file is removed or
migrated. So the *loaded* profile is the LLM env config above; the saved
profile was not loaded, and the 500 (not just an LLM-model mismatch) would stop
it from being loaded. **Follow-up** (§5).

**Caveat 2 (`-it` in a non-interactive shell).** `start-stack.ps1 -App openhands`
prints `cannot attach stdin to a TTY-enabled container because stdin is not a
terminal`, does not start the container, then prints the success line
`OpenHands UI: http://localhost:3000` and exits 0. There is no HTTP probe behind
that line. Detached (`docker run -d`), everything works. **Follow-up** (§5).

## 5. Follow-ups (out of this track's scope — do not read as done)

1. Wire `python3 tools/sync-router-tiers.py --check` into CI (`tests/` or the
   workflow) so future drift fails loudly.
2. Add `CHEAPINFERENCE_API_KEY` to `configuration/litellm/.env.example` (the
   regenerated cheaperinference legs reference it) and confirm the
   `openai/<model>` + `api_base` construction end-to-end with LiteLLM running.
3. Decide the subagent-depth contract: set
   `"experimental": { "subagent_depth": 2 }` in `opencode.jsonc` (with the note
   that the `subagent` permissions are the real depth control) or document that
   the 3-tier ladder is 1 level deep by design.
4. `start-stack.ps1|sh -App openhands`: replace `-it` with a TTY check +
   `docker run -d` fallback, and probe `:3000` before claiming success.
5. OpenHands profile 500: either delete/regenerate `~/.openhands/settings.json`
   (schema 6 → 4) or pin the image to the newer app version; then re-run a real
   saved-profile conversation so `openai_tier1` is proven, not just the env path.

Nothing in this branch was merged to `main`.
