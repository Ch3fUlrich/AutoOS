# Brief — AutoOS / OpenHands L2 session (2026-09-17, evening)

You are an **interactive Opus session** spawned by the sibling-analysis-repo orchestrator (session
`dce9ba20`, worktree `sibling-analysis-repo-orchestrator`) at the operator's request:

> "ensure spawn an interactive opus subagent which can spawn subagent to work on [the
> AutoOS/OpenHands items]"

You may spawn subagents (Sonnet for mechanical steps, Opus for judgement; never more than two at
once). You own the items below end to end and report in a DONE note. Nothing here touches the
`sibling-analysis-repo` repository, its `master.h5` store or its Postgres databases — do not open them.

## Read first

1. `C:\Users\<you>\Documents\Code\agent-skills\docs\superpowers\plans\2026-09-17-agent-orchestration-and-autoos-merge-plan.md`
   and its `-SURVEY.md` sibling (the merge plan for the orchestration skills and AutoOS; phases,
   what exists where, what is duplicated).
2. `C:\Users\<you>\Documents\Code\sibling-analysis-repo\docs\superpowers\plans\2026-09-17-curation-round-2\W6-operator-additions-2026-09-17.md`
   **section 4** (the operator's AutoOS items, verbatim) — read only, do not edit that repo.
3. `C:\Users\<you>\Documents\Code\agent-skills\skills\unattended-orchestration\SKILL.md` and
   `deepseek_review.sh` in the same directory.
4. `C:\Users\<you>\Documents\Code\agent-skills\AGENTS.md` (repo rules; the memory layer there).

## Coordination — another session is committing in agent-skills

Another Claude session works in `C:\Users\<you>\Documents\Code\agent-skills` (its 17:35 commit
`3df910e` briefly broke the runner's `-Validate`; restored `d4e6fad`). Therefore:

- Run `git -C C:\Users\<you>\Documents\Code\agent-skills status --short` before anything. **Never
  edit, stage, stash or reset a file that is already dirty there.** Never commit in that checkout.
- Do your repo edits in your own worktree: `git -C <agent-skills> worktree add
  ..\agent-skills-autoos-l2 -b autoos-l2 <current HEAD>`; commit there with `-c
  core.hooksPath=/dev/null`; the operator merges. `Start-Process` splits spaced arguments — quote.
- The AutoOS repository is wherever the merge plan says it is (it also has a worktree in use by
  another session — same rule: your own worktree, your own branch).
- Host-level changes (installing Ollama, pulling/deleting models) are the operator's explicit
  request; record every command you run and its result in the DONE note.

## Items (operator's words in quotes)

1. **Ollama is unusable from AutoOS.** "the AutoOS/OpenHands ollama models are not usable" —
   measured cause: `ollama` is on no PATH, so the `ollama-qwen2.5-coder` profile cannot start.
   Find where Ollama is (or is not) installed on this host (`infra/local-ai/` in agent-skills
   documents the intended setup), make the profile runnable (install through the documented
   route, or point the profile at the real binary), and prove it with one completed request.
2. **Duplicate model profiles.** "remove the duplicate models" — de-duplicate the AutoOS/OpenHands
   profile list; the survey lists the duplicates. Keep one owner per model; a profile removed is
   named in the DONE note with the profile it collapses into.
3. **Model swap.** "swap ollama-qwen2.5-coder with Qwen3 Coder 14B Q4_K_M — delete the older model
   and download the new one." Pull the new model first, verify it answers, then remove the old one.
   Disk and VRAM: one RTX 3060 (12 GB); check free space before pulling.
4. **Evaluate Qwen3 Coder 35B-A3B via llama.cpp `--n-cpu-moe 24`.** Is it runnable here (RAM,
   VRAM, tokens/s on a real coding prompt)? Write the measurement and a recommendation; do not
   make it the default without the operator.
5. **`deepseek_review.sh` findings to fold into the skill** (measured today by the orchestrator):
   opencode's `--file` attaches only ~1,000 lines and the prompt file must live under
   `/tmp/cross-review` or opencode auto-rejects the read as an external directory. A chunked
   variant (900-line parts, same standing instruction, outputs concatenated) is at
   `C:\Users\<you>\AppData\Local\Temp\claude\C--Users-<you>-Documents-Code-sibling-analysis-repo\dce9ba20-c0aa-4c26-af68-51043b020c89\scratchpad\deepseek_chunked_review.sh`
   — copy it into the skill beside `deepseek_review.sh`, generalise the two hardcoded paths into
   arguments, and document both limits in `SKILL.md`.
6. **Runner defect to record** (skill obs-15 shape): `run_handoff_sessions.ps1 --resume <id>` of a
   stopped session "starts a copy" that may exit at once (three RB1 resumes tonight) or work (RB2,
   W1). Record it in the skill's known-defects section with the reproduction; fix only if the
   cause is obvious and the fix is small.
7. Merge-plan phases 0 and 2 (agent-skills side) as time allows, after 1–6.

## Rules

- Never print or copy secrets (`agent-skills/secrets/api_keys.conf` is read inside WSL only).
- If the auto-mode classifier refuses a command, record it verbatim under "Refusals" in the DONE
  note and hand the command to the operator; never work around a refusal.
- Sonnet subagents do the mechanical steps (profile edits, downloads, measurements); you judge.
- DONE note: `C:\Users\<you>\Documents\Code\agent-skills\docs\superpowers\plans\2026-09-17-autoos-l2-DONE.md`
  (in your worktree, committed on `autoos-l2`), sections: Done (per item, with evidence), Commands
  run on the host, Refusals, Remains, Decisions for the operator.
