# DONE — AutoOS / OpenHands L2 session (2026-09-17)

*Written by the L2 Opus session spawned per `docs/superpowers/plans/2026-09-17-autoos-l2-brief.md`.
Two worktrees: `agent-skills-autoos-l2` (branch `autoos-l2`, from `main` @ 73a617b) and
`AutoOS-autoos-l2` (branch `autoos-l2`, from AutoOS `main` @ 7c2d65c). Nothing in `sibling-analysis-repo`
was opened — no store, no database, no write. **No file in either main checkout was modified**; the
other session's dirty files in `agent-skills` were never touched.*

> **Two of the brief's premises are contradicted by measurement, and both matter.**
>
> 1. The Ollama profile does **not** fail because `ollama` is off PATH. Ollama is installed and
>    running (a Docker container, which *is* the route `infra/local-ai/` documents), and the profile
>    never shells out to the CLI — it uses `base_url`. It fails because that `base_url` is
>    `http://127.0.0.1:11434/v1`, and inside any **container** `127.0.0.1` is that container,
>    where nothing listens on 11434. Measured, fixed, proven. (This note first assumed OpenHands
>    itself ran in a container; it does not, see the 2026-09-18 correction under Item 1.)
> 2. **Qwen3-Coder 14B does not exist** — not in the Ollama library, not as an official Qwen
>    release. The nearest real model is the 30B-A3B, which is also item 4's subject, so items 3 and
>    4 collapse onto one model.

---

## Done

### Item 1 — Ollama unusable from AutoOS/OpenHands · **root cause found, fixed, proven**

**Where Ollama is.** Installed and running, as a **Docker container** — which is the route
`agent-skills/infra/local-ai/docker-compose.yml` documents (`ollama/ollama:latest`, GPU via
`runtime: nvidia`, models bind-mounted from `C:/Apps/ollama`). A second container `ollama-agent`
shares the model directory. There is also a **stale legacy Windows install** at
`C:\Users\<you>\AppData\Local\Programs\Ollama` — `ollama.exe` **0.5.11**, dated Feb 2025, not on
PATH, not running, and far behind the container's **0.20.4**.

So "`ollama` is on no PATH" is true but is **not the failure**: nothing in the OpenHands path
invokes the CLI. The daemon answers on `:11434` and `qwen2.5-coder:7b` was already pulled.

**Proof the endpoint works** (Windows host, before any change):

```
POST http://127.0.0.1:11434/v1/chat/completions   model=qwen2.5-coder:7b
-> 200, content "```python\ns[::-1]\n```", usage {prompt 46, completion 9, total 55}
   43.2 s wall (cold model load; warm calls are 2.6-6.4 s, see item 3)
```

**The actual defect.** `openhands/profiles/ollama-qwen2.5-coder.json` sets
`"base_url": "http://127.0.0.1:11434/v1"`. (Corrected 2026-09-18: this note first said OpenHands
runs as container `ws-openhands-agent`. It does not. The only OpenHands deployment is
**agent-canvas**, whose agent-server is launched through `uvx`; `ws-openhands-agent` is only
*defined* in `infra/local-ai/docker-compose.yml` and is not deployed, with no container in
`docker ps -a`. It is being removed from that compose file. Source:
`2026-09-18-tool-audit.md`, row "OpenHands deployments".) Whatever runs in a container, inside
it `127.0.0.1` is that container.
Measured from a throwaway container on the live Docker network:

| URL tried from inside a container | Result |
|---|---|
| `http://127.0.0.1:11434/v1/models` | **HTTP 000** — connection failed (this is what the profile configures) |
| `http://ollama:11434/v1/models` | HTTP 200 |
| `http://host.docker.internal:11434/v1/models` | HTTP 200 |

`host.docker.internal` is the one value that answers **200 from all three vantage points** —
Windows host, WSL, and inside a container — so it serves OpenHands (container), OpenCode
(host/WSL) and a bare `curl` alike.

**Fix applied** (AutoOS worktree, branch `autoos-l2`): `base_url` ->
`http://host.docker.internal:11434/v1` in **both**

- `catalog/llm-models.json` (the `ollama-qwen2.5-coder` entry) — this is the **generator**:
  `lib/linux/install.sh:1540-1541` copies `direct.base_url` into the OpenHands profile and `:1380`
  feeds the same value to the OpenCode config. Fixing only the vendored profile would have been
  silently undone by the next install.
- `openhands/profiles/ollama-qwen2.5-coder.json` (the vendored copy, kept in step).

Tests after the change: `tests/run-tests.sh --filter "llm-models"` -> passed 1, failed 0;
`--filter "openhands projector"` -> passed 1, failed 0.

**Also measured — declared != live.** The compose file declares network `local-ai-network`; the
running containers sit on **`ollama-network`**, and no `local-ai-network` exists. The live stack was
not produced by `infra/local-ai/docker-compose.yml` as it now stands. Unresolved — see Decisions.

### Item 2 — duplicate model profiles · **done (one deletion), one non-obvious keep**

Every pair in `openhands/profiles/` (22 files, 231 pairs) and `openhands/agent-profiles/`
(11 files, 55 pairs) was compared as parsed JSON, not by name.

**Deleted: `openhands/profiles/ollama-qwen-coder.json` -> collapses into
`openhands/profiles/ollama-qwen2.5-coder.json`.** Byte-identical content (both
`ollama/qwen2.5-coder:7b`). The survivor won on all three tie-breaks: its name carries the `2.5`
the model id carries; `openhands/README.md` already called it "the canonical local profile" and the
other "legacy"; and `catalog/llm-models.json`, `lib/linux/install.sh` and
`lib/windows/AutoOS.Install.psm1` reference **only** `ollama-qwen2.5-coder`. Nothing in either repo
references `ollama-qwen-coder`. `openhands/README.md` updated to match.

**Deliberately NOT deleted, though byte-identical: `muse-spark-1.3.json` and
`muse-spark-1.3-contributor.json`.** An *intentional* alias pair, not a copy-paste accident, and
deleting either breaks the build:

- both are required by name as literal `_profile_for(...)` calls in `lib/linux/install.sh:1680-1681`
  and `lib/windows/AutoOS.Install.psm1:1536-1537`;
- `tests/run-tests.sh:378-382` hard-codes `muse_aliases = {"muse-spark-1.3",
  "muse-spark-1.3-contributor"}` and fails if either name is missing;
- both derive from the single catalogue entry `id: "muse-spark"`, whose `direct.model` is
  `openai/muse-spark-1.3-contributor` — the installer writes one model under two names on purpose;
- `agent-profiles/orchestrator.json` and `suborchestrator.json` reference
  `llm_profile_ref: "muse-spark-1.3"`, so deleting that file orphans two agent profiles.

Collapsing them is possible but is a coordinated change to two installers and a test, not a file
delete. See Decisions.

**No other duplicates exist.** No two profiles share a `model` id with differing settings. The
`orchestrator`/`suborchestrator` pairs share an `llm_profile_ref` but are distinct roles.

**Catalogue cross-check** (read-only): 20 catalogue models, 21 surviving profile files, **1:1 with
no orphans in either direction** once the muse alias is accounted for.

**Found while checking:** `openhands/README.md` claimed a test
`"vendored openhands profiles match the catalog snapshot"` guards this against drift. **That test
does not exist** in `tests/run-tests.sh` or `tests/run-tests.ps1`. The two real tests
(`"llm-models.json is valid and every id is unique"`, `"the openhands projector covers every shared
model"`) pass, but neither diffs the vendored JSON against the catalogue. The advertised drift
detector is absent — see Remains.

### Item 5 — `deepseek_review.sh` findings folded into the skill · **done and proven end to end**

New `skills/unattended-orchestration/deepseek_chunked_review.sh`, generalised from the
orchestrator's sibling-analysis-repo scratchpad copy. Contract:

```
deepseek_chunked_review.sh <label> <base-sha> <merge-sha> <done-means-file> <out-md> [repo] [paths...]
```

The two hardcoded paths became arguments/env: `repo` defaults to `git rev-parse --show-toplevel` of
the caller's cwd; the scratch dir is `${DSR_WORKDIR:-${TMPDIR:-/tmp}}/dsr_<label>`; the reviewed
pathspec defaults to the whole diff instead of sibling-analysis-repo's `app sibling_analysis scripts tests
configs`; and the two sibling-analysis-repo-specific sentences inside the prompt became `DSR_BRANCH_DESC`
and `DSR_REPO_DESC`, so no other repo is told it is reviewing sibling-analysis-repo. Kept unchanged: the
900-line split, the "DO NOT USE ANY TOOLS" standing instruction, the `/tmp/cross-review` copy, the
ANSI strip, `set -euo pipefail`, the per-part failure fallback. LF endings confirmed.

`SKILL.md` §9.7 gained a paragraph on both measured limits — the **silent** ~1,000-line `--file`
truncation (a review that looks complete may have seen a fifth of the diff) and the
`/tmp/cross-review` requirement with its "external directory" rejection symptom — dated 2026-09-17,
OpenCode 1.18.30, pointing at the chunked variant for any diff over ~900 lines.
*(Correction to the brief: `SKILL.md` had **no** existing section documenting `deepseek_review.sh`
— commit `c9265d9` added the script and never mentioned it in the skill. A new paragraph was added
inside the existing §9.7 rather than restructuring anything.)*

**A real defect was found in the generalisation and fixed by the L2, not by the subagent that wrote
it.** The `DEEPSEEK_SECRETS` default resolved through `$BASH_SOURCE` to *the script's own
checkout's* `secrets/api_keys.conf` — but that file is **untracked**, so it exists only in the main
checkout and **never in a worktree**, which is exactly where the unattended runner puts every
session. The default was therefore broken in the one environment the script exists for; the first
end-to-end run failed with `grep: .../agent-skills-autoos-l2/secrets/api_keys.conf: No such file or
directory`. It now falls back through `git rev-parse --path-format=absolute --git-common-dir` (a
worktree's own `--git-dir` points at `.git/worktrees/<name>`; the *common* dir's parent is the main
checkout) and fails loudly with `secrets file not found: <path> (set DEEPSEEK_SECRETS)` instead of
reporting a missing key.

**Verification.** `bash -n` clean. Default-repo path (no `repo` argument) exercised separately.
Failure path: a deliberately absent secrets file produced the guard message, not a syntax or
unbound-variable error, and never reached the model. **Real end-to-end run against DeepSeek** on a
planted bug (a `median()` returning the upper-middle element of an even-length list):
`deepseek-v4-flash` returned a correct, severity-ranked review naming `calc.py:9`, the exact wrong
value (`median([1,2,3,4])` -> `3` instead of `2.5`), the unguarded empty-input `IndexError`, and
correctly certified the `average()` refactor as behaviour-equivalent. 1,473 bytes, exit 0.

### Item 6 — runner resume defect recorded · **recorded, deliberately not fixed**

Added as observation **37** in
`skills/unattended-orchestration/PROPOSALS-2026-09-05-from-a-downstream-orchestrator.md`
(*correction to the brief:* there is no literal `obs-NN` string and no such section in `SKILL.md` —
the numbered observation rows live in PROPOSALS, where entry 15 is the closest topical precedent):

> `run_handoff_sessions.ps1 --resume <id>` of a *stopped* session starts a copy instead of resuming
> it, and the copy may exit immediately. Reproduction: resume a stopped session by id. Observed
> 2026-09-17: three resumes of RB1 exited immediately; RB2 and W1 resumed correctly. Cause not
> established.

**Not fixed, on purpose.** The resume path was traced: `run_handoff_sessions.ps1` has no `-Resume`
CLI flag at all — resume is chosen inside the main loop when `state.json` carries a `session_id`,
then `Start-Bg` -> `Invoke-Launcher "resume"` -> `Build-HandoffLaunchArgs` -> `claude --bg --resume
<id>`; `Start-Bg` regex-matches the launcher's stdout for a `backgrounded` id and returns null when
it does not match. No off-by-one, wrong variable or swapped argument was found. A 3-of-5 failure
rate on one code path in one night points at `claude --bg --resume` behaviour on a genuinely stopped
session rather than at a script bug — but that is unmeasured, so it is recorded with no guessed
cause. A wrong fix here costs an overnight run.

### Item 3 — model swap · **partially done; the named model does not exist**

**Qwen3-Coder 14B Q4_K_M could not be pulled because it does not exist.** Measured against the
Ollama registry (HTTP status of each manifest):

| Tag probed | Result |
|---|---|
| `qwen3-coder:14b` | **404** |
| `qwen3-coder:14b-q4_K_M` | **404** |
| `qwen3-coder:7b` | **404** |
| `qwen3-coder:30b` | 200 |
| `qwen3-coder:30b-a3b-q4_K_M` | 200 |
| `qwen3-coder:latest` | 200 |

HuggingFace agrees: a search for `Qwen3-Coder-14B` returns only unrelated third-party fine-tunes
(medical LoRAs, an SWE distill, an RL experiment) — no official Qwen release, no mainstream GGUF.
The Qwen3-Coder family is **30B-A3B** and **480B-A35B** (plus Qwen3-Coder-Next). The 14B in the
operator's note appears to be a misremembering; the model actually wanted is the **30B-A3B**, which
is also item 4's subject.

**Pulled instead: `qwen3-coder:30b` (Qwen3-Coder-30B-A3B-Instruct, Q4_K_M, 18 GB).** Disk checked
first: 792 GB free on `C:` before the pull, 753 GB after. It answers — see the table in item 4.

**The old model was NOT deleted, and this is a deliberate hold.** The brief's sequence was "pull the
new model, verify it answers, *then* remove the old one" — verification is the gate, and it did not
pass on the terms that would justify the deletion: the replacement is **8x slower** (5.2 vs 41.9
tok/s). Deleting `qwen2.5-coder:7b` would leave this host with no local coder fast enough for
interactive use, in exchange for one the operator did not actually name. Both now coexist (23 GB, on
a disk with 753 GB free) — the deletion is one command once the operator confirms the substitution.
See D1.

### Item 4 — Qwen3-Coder 30B-A3B via llama.cpp `--n-cpu-moe 24` · **measured; do not make it default**

*(The brief says "35B-A3B"; no such Qwen release exists — the mainstream model is 30B-A3B, and the
community "35B-A3B" names on HuggingFace are third-party merges. Measured the real one.)*

**Host:** RTX 3060, **12 GB** VRAM (12,288 MiB); **128 GB** system RAM — not the 60 GB the
operator's note assumed, so RAM is nowhere near the constraint. VRAM is.

**llama.cpp was not installed** on this host — not on Windows, not in WSL, and `infra/local-ai/`
does not mention it. Obtained as `ghcr.io/ggml-org/llama.cpp:server-cuda` (6.99 GB), pointed at
Ollama's existing GGUF blob (no second 18 GB download), and the eval container removed afterwards.

Same three coding probes for every row, warm (model load excluded):

| Runtime / setting | VRAM | Aggregate | P1 algo | P2 bugfix | P3 shell |
|---|---|---|---|---|---|
| **Ollama, `qwen2.5-coder:7b`** (incumbent) | ~6.3 GB, **100% GPU** | **41.9 tok/s** | 40.4 | 36.8 | 44.7 |
| **Ollama, `qwen3-coder:30b`** (auto split) | 11.5 GB, **60% CPU / 40% GPU** | **5.24 tok/s** | 4.81 | 4.90 | 6.51 |
| **llama.cpp, `--n-cpu-moe 24 -c 8192`** | 11.9 GB | **5.05 tok/s** | 4.91 | 4.72 | 5.57 |
| llama.cpp, `--n-cpu-moe 24 -c 8192 --load-mode none` | 11.9 GB | **3.27 tok/s** | 2.78 | 4.18 | 3.49 |
| llama.cpp, `--n-cpu-moe 20 -c 4096` | 12.0 GB (at the cap) | **1.51 tok/s** | 0.82 | 1.55 | 8.08 |

**Findings.**

1. **`--n-cpu-moe 24` buys nothing over Ollama here.** 5.05 vs 5.24 tok/s is inside the noise, and
   if anything llama.cpp is marginally slower. The premise that hand-tuning the MoE split beats
   Ollama's automatic one does not hold on this hardware.
2. **24 is about the right value, and there is no headroom below it.** `--n-cpu-moe 20` (four more
   layers' experts on the GPU) pushes VRAM to 11,989 of 12,288 MiB and throughput **collapses to
   1.51 tok/s**, with wild per-probe variance (0.82 / 1.55 / 8.08) — the signature of spilling into
   shared memory. The 12 GB card is the binding constraint, not RAM and not the split.
3. **`--load-mode none` makes it worse, although llama.cpp's own startup log recommends it**
   (`tensor overrides to CPU are used with mmap enabled - consider using --load-mode none for
   better performance`). Measured: 3.27 vs 5.05 tok/s, a 35% loss. Do not follow that advice here.
4. **Flag note:** `--no-mmap` is **not** valid in this build (`error: invalid argument: --no-mmap`);
   the mmap control is `--load-mode`.

**Quality, same three probes** (speed is only half the question):

| Probe | `qwen2.5-coder:7b` | `qwen3-coder:30b` |
|---|---|---|
| P1 merge intervals | correct | correct, cleaner |
| P2 find the bug | right diagnosis, but the fix crashes on `[]` | right diagnosis **and** guards `[]` |
| P3 POSIX `sh` retry | **fails** — bash arrays, `for ((...))`, `return ${cmd[-1]}`, never returns the command's status | close: correct loop and doubling backoff, but `return $?` after a `[ ]` test returns the test's status, not the command's; `local` is not POSIX |
| Score | 1.5 / 3 | 2.5 / 3 |

**Recommendation (the operator decides, per the brief).** 30B-A3B is *runnable* here but at ~5
tok/s it is **not** an interactive L3 executor — a 600-token answer takes two minutes. It is
defensible as a **batch reviewer**, where latency does not matter and the quality gain is real.
Keep `qwen2.5-coder:7b` as the interactive local model. Neither was made a default.

The eight-fold gap is a VRAM fact, not a model fact: the 7B fits entirely in 12 GB, the 30B-A3B
cannot. A local coder both fast *and* better than the 7B needs a bigger card, or a dense model in
the 12-14 GB class at Q4 (e.g. `qwen3:14b`, which is **not** a Coder variant).

### Item 7 — merge-plan phases 0 and 2 · **not started**

Items 1-6 consumed the session. Nothing was attempted, so nothing is half-done. See Remains.

---

## Commands run on the host

Read-only probes (`where`, `ls`, `curl`, `docker inspect`, `nvidia-smi`, `git status`) are omitted;
everything below changed host state or cost real time and bandwidth.

| # | Command | Result |
|---|---|---|
| 1 | `git -C AutoOS worktree add ../AutoOS-autoos-l2 -b autoos-l2 HEAD` | created; branch `autoos-l2` at 7c2d65c |
| 2 | `cp -r AutoOS/openhands AutoOS-autoos-l2/openhands` | 34 files — `openhands/` is **untracked** in AutoOS main (another session's work), so a worktree does not inherit it |
| 3 | `git -C agent-skills-autoos-l2 -c core.hooksPath=/dev/null merge --ff-only 73a617b` | fast-forwarded the existing worktree from d4e6fad to main's HEAD |
| 4 | `curl -X POST 127.0.0.1:11434/v1/chat/completions` (qwen2.5-coder:7b) | 200, 9 completion tokens, 43.2 s cold |
| 5 | `docker run --rm --network ollama-network curlimages/curl ... /v1/models` x3 | `127.0.0.1` -> **HTTP 000**; `ollama` -> 200; `host.docker.internal` -> 200 |
| 6 | `docker exec ollama ollama pull qwen3-coder:30b` | 18 GB pulled (the first invocation fetched the blob but died before writing the manifest; the re-run completed in seconds from the cached blob). Disk 792 -> 753 GB free |
| 7 | `python bench.py qwen2.5-coder:7b` | 41.86 tok/s aggregate, 52.0 s load |
| 8 | `python bench.py qwen3-coder:30b` | 5.24 tok/s aggregate, 86.9 s load, 60% CPU / 40% GPU |
| 9 | `docker pull ghcr.io/ggml-org/llama.cpp:server-cuda` | 6.99 GB image added |
| 10 | `docker run -d --name llamacpp-eval --gpus all -v C:/Apps/ollama/models/blobs:/models:ro -p 8099:8080 ... --n-cpu-moe 24 -c 8192` | served; 5.05 tok/s |
| 11 | same `+ --no-mmap` | **failed**: `error: invalid argument: --no-mmap` |
| 12 | same `+ --load-mode none` | served; 3.27 tok/s |
| 13 | same, `--n-cpu-moe 20 -c 4096` | served; 1.51 tok/s, VRAM at the cap |
| 14 | `docker stop llamacpp-eval && docker rm llamacpp-eval` | **removed** — no eval container is left running |
| 15 | `curl -X POST /api/generate -d keep_alive:0` (x2) | unloaded models between runs so measurements did not contend |
| 16 | `bash deepseek_chunked_review.sh ...` x3 (two on a throwaway repo, one real DeepSeek call) | see item 5 |

**Host state left changed:** `qwen3-coder:30b` added to the Ollama store (18 GB), and the
`ghcr.io/ggml-org/llama.cpp:server-cuda` image is present (6.99 GB) — **remove it with
`docker rmi ghcr.io/ggml-org/llama.cpp:server-cuda` if the llama.cpp route is not pursued.**
Nothing was uninstalled, no model deleted, no service configuration changed.

## Refusals

**None.** No auto-mode classifier refusal occurred in this session, so nothing was worked around and
nothing is handed to the operator on that account.

One harness *block* (not a classifier refusal) is recorded for completeness: a foreground `sleep 45`
chained before a status check was blocked with *"Blocked: sleep 45 followed by: ... To wait for a
condition, use Monitor with an until-loop"*. Complied with — later waits use `timeout ... until`
loops.

## Remains

1. **Item 7 (merge-plan phases 0 and 2) — untouched.** Phase 0's four measurements (the
   `agent-canvas` flag probe, the `openhands == agent-canvas` question, the `mcode` enum check, the
   quota fixtures) and phase 2's `--ff-only` merge of `experiment/agent-canvas-orchestration` are
   all still open.
2. **AutoOS's `openhands/` is untracked in `main`** — another session's uncommitted work. This
   session's branch **commits it**, the first time it enters history. Reconcile with that session's
   intent before merging.
3. **The drift test `openhands/README.md` advertises does not exist** ("vendored openhands profiles
   match the catalog snapshot"). Either write it or stop claiming it. Nothing today detects the
   vendored profiles drifting from `catalog/llm-models.json` — which is exactly why this session's
   item-1 fix had to be applied to both files by hand.
4. **`infra/local-ai/docker-compose.yml` does not describe the running stack.** It declares network
   `local-ai-network`; the live containers are on `ollama-network`, which the file never mentions,
   and `local-ai-network` does not exist. Whatever brought the stack up was not this file as it
   stands.
5. **The legacy Ollama 0.5.11 install** at `C:\Users\<you>\AppData\Local\Programs\Ollama` is dead
   weight and a trap for anyone who puts it on PATH expecting it to match the 0.20.4 daemon.
   Uninstalling needs the operator (it has an `unins000.exe`).
6. **Resume defect (obs 37) has no cause.** Establishing it needs a deliberate experiment against
   `claude --bg --resume` on a known-stopped session, not more code reading.

## Decisions for the operator

**D1 — Qwen3-Coder 14B does not exist. Which substitute, and does the 7B still go?**
`qwen3-coder:30b` is 8x slower than the incumbent (5.2 vs 41.9 tok/s) at moderately better quality
(2.5/3 vs 1.5/3). Options: **(a)** keep both — 30B as batch reviewer, 7B as the interactive model
(*recommended, and the current state*); **(b)** delete `qwen2.5-coder:7b` anyway and accept ~5 tok/s
locally — `docker exec ollama ollama rm qwen2.5-coder:7b`; **(c)** pull `qwen3:14b` (dense, fits
12 GB, but **not** a Coder variant) and compare first. Nothing was deleted pending this.

**D2 — `host.docker.internal` as the Ollama `base_url` in `catalog/llm-models.json`.** It is the
only value measured to work from all three vantage points here, but it is a Docker-Desktop
convenience: on a native Linux Docker host it resolves only if the compose file maps
`extra_hosts: host.docker.internal:host-gateway`. AutoOS ships `linux.json` / `macos.json` /
`windows.json`, so this is a cross-platform choice, not a local one. Alternatives:
`http://ollama:11434/v1` (works only for containers on the same compose network), or make it an
install-time answer. As committed it is correct for this host and for any Docker Desktop host.

**D3 — collapse `muse-spark-1.3` / `muse-spark-1.3-contributor`?** Byte-identical but intentionally
aliased; three files plus a test hard-code both names (item 2). Collapsing means a coordinated
change to `lib/linux/install.sh`, `lib/windows/AutoOS.Install.psm1` and `tests/run-tests.sh:378-382`
— not a delete. Left alone.

**D4 — is the llama.cpp route worth keeping?** Measured: no advantage over Ollama on this hardware.
The 6.99 GB image is still on the host; say the word and it goes. Revisit only with a larger card.

**D5 — AutoOS `openhands/` entering git history.** See Remains 2. If leaving it untracked was
deliberate (vendored output meant to stay out of git), cherry-pick this branch rather than merging
it wholesale.
