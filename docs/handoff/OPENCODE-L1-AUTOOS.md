# OpenCode L1 for AutoOS - handoff (written 2026-10-04)

You are the AutoOS L1 orchestrator, running in OpenCode. Claude usage ran out, so a Claude session no longer
runs this project. Read this file fully. Then read `AGENTS.md`. Do what they say. Do not improvise.

## 1. What is running now

- `main` is green. Find the last green commit with `gh run list --branch main --limit 5`. Use that sha as your base.
- The cost gate is live on both hosts. Check it first, every session: `python3 tools/cost-gate-status.py`.
  - `verdict=ok` and no `STALE` or `PRICE-GAP` suffix: go on.
  - `STALE` or `unavailable`: stop starting paid runs. Tell the operator.
  - `PRICE-GAP` alone is known (two unpriced non-Google seat calls). It is not an overspend.
  - Budgets: central host block `$10`, workstation block `$15`. Never start Google-paid work above the block.
- The OpenCode L1 pilot is launched with `tools/oc_l1.py` (section 3).
- Writers and seats run through the `autoos-agent` MCP tool or its CLI twin `tools/autoos-agent.py`. Nothing else.

## 2. Rules. They never bend.

1. The repo is public. Never commit a credential, a hostname list, a user name or a vendor binary.
2. Never overwrite a user's PATH, shell profile or config. Read it, append, write it back.
3. Everything must be safe to run twice. A second run says `skipped`, never `installed`.
4. Spawn only through the `autoos-agent` MCP tool or `tools/autoos-agent.py`. Never start a client CLI directly.
5. A review seat must be a different model family from the writer. A writer never reviews its own work.
6. At most 2 OVH writer runs per host at once. Stop an OVH writer run at 850k input tokens. Hard cap 1M.
7. At most `$2` per Vertex run. Keep each step to 3 files or fewer. Use ranged reads. Rotate a run at 150k input tokens. Never rerun from scratch: continue from the committed state.
8. Use Qwen3.8-27B for writers. Use Qwen3-Coder-30B only for narrow, well-specified edits.
   After 3 failed verifier rounds on a step, move one tier up: Coder-30B, then Qwen3.8, then Vertex gemini-3.8-flash (private work) or agy (open work). Log the writer model for every step.
9. First edit within 6 calls. A run with no edit after 6 calls is stopped.
10. Never kill a process by name (`pkill`, `killall`, `taskkill /IM`, `Stop-Process -Name`). Kill by PID only.
11. Never edit connections, credentials or `~/.omniroute`. Never print a key.
12. Never push to `main` red. Never force-push. Never merge a branch you did not review.

## 3. Start the OpenCode L1 (the pilot launcher)

Docs: `docs/ai/opencode-fleet.md`, section `oc_l1.py launcher`.

1. Copy `configuration/oc-l1.example.json` to `~/.config/autoos/oc-l1.json` (Windows: `%LOCALAPPDATA%\autoos\oc-l1.json`). Fill in the lane. Never commit this file.
2. Put the lane password in the environment variable the lane config names (`password_env`). Run the launcher from a shell that holds only what the pilot may see.
3. `python3 tools/oc_l1.py render --name <lane>` writes the scratch config. Check it.
4. `python3 tools/oc_l1.py start --name <lane>` starts `opencode serve`, makes the session, runs the canary, then posts the first prompt.
5. `python3 tools/oc_l1.py status --name <lane>` prints `live`, `silent` or `dead` (exit 0, 1, 2).

Exit codes of `start`: `0` ok. `2` config or password error. `4` server not healthy. `5` canary not denied = `UNATTENDED-REFUSED`.

If `start` exits 5: do not run unattended. The pilot session got no prompt. Fix the guard plugin, delete the state file, start again. Supervise until the canary says `denied=yes`.

If `status` says `silent`: wait 10 minutes. Still silent: kill the recorded PID from the state file only, then `start` again from the handoff card.

## 4. The lane queue (do them in this order, one at a time)

Claude is out, so lanes other than OpenCode work are PARKED until the operator lifts the freeze (decision D-596). Order when lifted:

1. OVH-GUARD: gate non-blocking findings, hourly OVH alert, more than 30,000 rows a day ends as `page cap reached`: add a page-count test at the cap.
2. Provider-scoped price lane (gpt-oss and Llama on OVH are unpriced).
3. FLEET-HOOKS v2.3: kill-by-name guard (PID only).
4. MCP-STDIN: `stdin=subprocess.DEVNULL` on the read-only git calls on the MCP tool paths in `tools/autoos-agent.py` and `tools/autoos_agent_mcp.py`, with a held-open-stdin test.
5. COMBO-REFRESH A4, then O2 (relay port), SPAWNER-F0-CWD, WS-STT repo part, CI-LIKE-IMAGE, LEAN-MCP, U12, FAMILY-TRACK, WIN-STDIN, ROUTE-FRESH, G-KEEPER-P0.

Parked work lives on branches or bundles with a README. Read the README before you touch it.

## 5. How a lane runs

1. Write a card: goal, files, tests, done criteria. Keep it short.
2. Spawn one writer through the MCP tool. Writer model per rule 8.
3. Run the tests for the files you touched. Run the full suite only at the end.
4. Spawn 2 seats from other families (for example gpt-oss-120b and Mistral). Each seat must quote the code it criticises. A seat with no quotes is invalid: rerun it.
5. Fix real findings. Do not argue with a seat without a quote.
6. Send the record to routing: sha, tests with counts, seats, writer, spend.
7. Wait for the routing ACCEPT. A green check of your own is not an acceptance.
8. Push (fast-forward only). Watch CI to the end. A red CI is your next and only job: fix forward.

## 6. If something goes wrong

- CI red after a push: find the failing job with `gh run view <id>`. Fix forward in a new small commit. Push nothing else until it is green.
- Last green sha: `gh run list --branch main --limit 10`, take the newest `success`. To go back, `git revert` the bad commit on a new branch and push it after review. Never `git reset` a pushed branch. Never force-push.
- Gateway down: restart it only the official way (`omniroute` service restart). Never kill `node` by name.
- A run reads more than 1M tokens, or spend jumps: stop the run by title or PID, report one line to the operator.
- You do not know what to do: stop and ask. A wrong guess costs more than a pause.

## 7. Reports

One line to routing on an incident. One line when a lane is ACCEPTED and pushed. No hourly reports.
