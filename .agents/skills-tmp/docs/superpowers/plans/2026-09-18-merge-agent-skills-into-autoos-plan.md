# Merge agent-skills into AutoOS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** AutoOS becomes the one uniform repository that sets up a machine *and* its agents. It
has a clean folder layout and proper skills. Any agent pointed at it finds the skill or file it
needs within a few reads, including a local 7B model with a 32k context. Every agent it spawns
works in one known place outside the repository. After that, agent-skills is archived.

**Architecture:**
- **Migrate agent-skills' current state, not its history.** The tracked files of agent-skills
  `main` at the moment of migration are the only input, and no history is carried over. Each
  file is moved to its place in a new target layout, merged, rewritten or dropped, following one
  mapping table (ADR 0006). It is not a verbatim dump.
- **Navigation for all agents.** On top of the layout comes a tiered navigation layer:
  1. `AGENTS.md`
  2. a generated `INDEX.md`
  3. one README per folder
  4. `SKILL.md` files
  5. `references/`

  Every tier has a size budget, a test enforces it, and a routing eval runs against a local
  model.
- **One `<repo>-worktrees/` folder beside each repository** (ADR 0007). Everything a spawned
  agent creates (its worktree, its scratch) lives there: outside the repo, so MCP servers index
  only the repo, and grouped, so the parent folder stays tidy.
- **Then the functional goals:** one orchestration skill, one model catalogue feeding CAO, and a
  profile-management tab.

**Tech Stack:** PowerShell 5.1/7 (plain-script tests, UTF-8 BOM, CRLF), POSIX bash (LF),
Python 3 (installers' embedded scripts, CAO, `tools/*.py`, pytest for imported suites), GitHub
Actions.

**Spec (read these first):**
- The operator's four original tasks (merge the orchestration skills; merge the canvas worktree;
  a three-level hierarchy reaching Claude, Gemini CLI, DeepSeek, Muse Spark, Ollama and
  OpenRouter; a profile tab in the GUI and TUI), plus the goal "remove the agent-skills
  repository".
- The operator's 2026-09-18 amendments:
  - migrate the current state only, into one uniform repository with a smart folder structure
    and proper skills;
  - agents navigate fast and use the routing-index skill properly;
  - the index and READMEs are updated for the full repository;
  - local and low-context agents spawned through OpenHands can navigate too;
  - spawned agents get a proper folder of their own, so they don't clutter anything;
  - ADR 0005 is accepted, and the OpenAI Agents API is not needed.
- `docs/superpowers/plans/2026-09-17-agent-orchestration-and-autoos-merge-plan.md` and its
  `-SURVEY.md` (this repo). Their phases 1, 3 and 4 are carried into Phases 6–8 below. Their
  decision ◆0 ("keep two repos") is overturned by the operator.
- `AutoOS/docs/decisions/0005-openai-agents-api-not-the-backbone.md` (**accepted**) and
  `AutoOS/docs/research/2026-09-18-openai-agents-api.md`.

## Global Constraints

- **AutoOS is PUBLIC; agent-skills is PRIVATE** (`gh repo view`, 2026-09-18). Nothing
  reaches AutoOS that AutoOS `AGENTS.md` rule 1 forbids: credentials, hostnames, IPs, usernames,
  e-mail addresses, user home paths. Nor private project names.
- AutoOS rule 2: never overwrite a user's PATH, shell profile or config wholesale. Read,
  append, write back, with a backup.
- AutoOS rule 3: everything is safe to run twice. A second run reports `skipped`, never
  `installed`.
- Every `.ps1`/`.psm1` has a UTF-8 BOM (`tests/run-tests.ps1` enforces it). `.gitattributes`:
  LF everywhere, CRLF for `ps1`/`psm1`/`psd1`.
- Tests install nothing. PowerShell and bash suites are plain scripts. Imported Python suites
  run under pytest in a dedicated CI job (◆ D5).
- Run the Linux suite in WSL as
  `wsl bash -lc "cd /mnt/c/Users/<you>/Documents/Code/AutoOS && bash tests/run-tests.sh"`.
  `tests/run-tests.sh --wsl` fails when started from Git Bash (path not translated).
- **Agent-facing size budgets** (their single home is `tools/check-agent-docs.py`, Task 3.1):

  | File | Budget |
  |---|---|
  | `AGENTS.md` | ≤ 80 lines |
  | `INDEX.md` | ≤ 6,000 bytes (≈ 1.5k tokens) |
  | Each folder `README.md` | ≤ 2,400 bytes |
  | Each `SKILL.md` body | ≤ 250 lines, detail in `references/` |
  | Skill `description` | ≤ 300 characters |

- Never push AutoOS's local branches `backup-local-main-july` / `integration-merge`: they predate
  the 2026-08-21 history rewrite.
- Never reboot the host. Never delete or move `infra/mcp-servers/data/` (the live store of all
  8 memory graphs), except in the operator runbook, Task 5.5.
- Commits end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Work on a branch per
  phase, never directly on `main`. Merge back when the phase's suites are green.

## Facts this plan rests on (inventory, 2026-09-18)

| Fact | Consequence |
|---|---|
| agent-skills: 293 tracked files, 2.58 MB tracked, but ~14 GB on disk (ignored graph data, 64,599 backups, legacy `mcp-servers/`, a stray `C:` folder) | The migration reads `git ls-files` of `main`, **never** the directory |
| Tracked files hold the public omnigraph domain, `*.vm` hostnames, `192.168.178.x`, Windows/WSL user paths, `/home/<user>`. 15 docs name private projects; `EVIDENCE.md` holds an e-mail address | Scrub or exclude in agent-skills before anything moves (Phase 1) |
| `SKILL.md` sizes: `unattended-orchestration` 914 lines, `mcp-servers-setup` 575, `structured-memory` 294, `swarm-orchestration` 268, `coding-principles` 190, `repository-index` 185 | A 7B/32k model cannot hold them. They get split into references (Task 3.6) |
| `repository-index` maps only agent-skills. Its §1 lists `infra/local-ai` and `webpage/` (both dropped), and it names a private host | Rewritten for the unified repo (Task 3.4) |
| The runner defaults `worktreeParent` to the repo's **parent folder** (`HandoffCore.psm1:1509-1510`), creating `<Code>/<repo>-<session>` per session. Keeping worktrees outside the repo is right, because MCP servers index a repo root. But ungrouped, they fill the parent folder (~20 `sibling-analysis-repo-*` in `Documents/Code`) | The default becomes `<parent>/<repo>-worktrees/` (Task 4.2); names stay `<repo>-<session>` for unique Serena project names |
| CAO's `worktreeRoot` is `$HOME/cao-worktrees` (`handoff.config.example.json:311`), on ext4 because WSL needs it. OpenHands workspaces are whatever the UI picks (`~/.openhands/workspaces.json`) | CAO: `$HOME/<repo>-worktrees/` (Task 4.3); OpenHands profiles carry the rule (Task 4.4) |
| agent-skills has no CI; 7 plain-pwsh suites; pytest suites in `skills/unattended-orchestration/tests`, swarm `custom_orchestration/tests`, `infra/mcp-servers/{scripts,omnigraph-setup,servers/homelab-mcp}` | AutoOS CI gains an `agents` job |
| AutoOS installers **clone the private repo**, read `…/agent-skills/secrets/api_keys.conf`, and link `~/.claude/skills/*`, `~/.gemini/config/skills/*`, `~/.openhands/skills` into it. They only link when the target is missing | Phase 5 rewires these and **repoints** existing links |
| Live host consumers of the agent-skills checkout: the omnigraph containers (compose working dir), scheduled task `\Omnigraph Sync`, `~/.gemini/config/mcp_config.json`, `~/.codewhale/mcp.json`, `~/.gemini/antigravity-cli/settings.json`, `~/.serena/serena_config.yml`, sibling repos' CLAUDE/AGENTS links | Operator runbook (Task 5.5) and a read-only finder (Task 5.6) |
| ADR numbers: AutoOS uses 0001–0005; agent-skills uses 0001–0008 | The new layout ADR is 0006, the worktrees-folder ADR 0007, and the migrated agent-skills ADRs become 0008–0015 |

## Decisions the operator takes (◆) — recommended option first

| ◆ | Question | Recommended | Alternative |
|---|---|---|---|
| D1 | The unified layout | The tree in Task 2.1 (ADR 0006) | Amend the tree before Task 2.2 starts |
| D2 | What is **not** migrated | `webpage/`, `EVIDENCE.md`, `infra/local-ai/` (superseded by AutoOS's `ollama`/`openhands` components), `handoff.config.json` (local runtime config), `docs/REMOTE-SYNC-TEST-PLAN.md`, `CHANGELOG.md` (AutoOS's changelog links the archive), and any prompt or starter that exists only for a private repo | Migrate them scrubbed |
| D3 | Private infrastructure values | **Placeholders** in `.example` files and compose (`${OMNIGRAPH_HOST}` …); real values in user-scope `~/.config/autoos/site.env` | A separate small private "site" repo |
| D4 | Secrets location | `~/.config/autoos/api_keys.conf` on every OS; legacy `…/agent-skills/secrets/api_keys.conf` read as a fallback, with a notice, until Phase 9 | `%APPDATA%\autoos\` on Windows |
| D5 | pytest in AutoOS CI | Yes, one `agents` job (Linux + Windows), dev-only dependency | Port Python tests to plain scripts |
| D6 | Where spawned agents work (ADR 0007) | `<parent>/<repo>-worktrees/`, holding `<repo>-<session>/` worktrees and `.sessions/<key>/` scratch. CAO in WSL: `$HOME/<repo>-worktrees/` (ext4). `AGENT_WORKTREES_PARENT` overrides the parent | Keep one sibling folder per session |
| D7 | herdr | Folded into the merged orchestration skill as one section | Separate skill |
| D8 | Swarm Python scaffold | Retire `orchestrator_scaffold.py` / `role_router.py`; keep `providers/*.py` and `verification_runner.py` | Keep everything |
| D9 | Profile tab | Store in `~/.autoos/profiles/<id>.json`; built-ins read-only (fork on edit, `derivedFrom`); exports never carry `answers` | — |
| D10 | Windows OpenHands PowerShell probe | Guard line at the top of the PowerShell profile (Task 0.5); remove once upstream PR OpenHands/software-agent-sdk#3913 ships | Nothing (accept the 5 s probe failures) |
| — | OpenAI Agents API | **Decided: not used** (ADR 0005 accepted). An OpenAI `codex` reviewer pool stays optional (Task 7.3) | — |

---

## Phase 0 — Stabilise both repositories

**Done on 2026-09-18.** Both `main`s are clean, every worktree branch is merged, and the merged
worktrees are removed.

| Task | Result |
|---|---|
| 0.1 Runner fix | agent-skills `1cbe026`: rebuilt from `73a617b~1` plus the leftover-commit block, which now runs before the guards. Runner.Smoke 19/19 |
| 0.2 Pending agent-skills work | `34b642a`: the `unifished-` typo rename undone, `compact-graphs.ps1`, the 2026-09-17 plan/survey/brief, and `api_keys.conf.example` with placeholder values |
| 0.3 Side branches | agent-skills `autoos-l2` merged (`ec22702`); `experiment/agent-canvas-orchestration` merged (`b75b284`), then its invented `agent-canvas` adapter removed (`176f59d`; the muse/openrouter CAO pools stay). AutoOS `autoos-l2` merged as superseded (`0b854ef`, no content change). All three worktrees and branches removed |
| 0.4 AutoOS | `feat/openhands-agent-canvas` fast-forwarded; `worktree-usb-improvements` merged (`993dbad`); ADR 0005 accepted (`1fdf5aa`). Windows suite 263/0, Linux (WSL) 299/0 |
| Suites on agent-skills `main` | AdapterContract 21/21, HandoffCore 91, Ledger 8, McpTopology 12, Archive 4, Portability 10, Runner.Smoke 19 (all 0 failed); pytest 501 passed / 13 skipped; swarm pytest 61 passed |

**Still open:**
- AutoOS worktree `.claude/worktrees/usb-improvements` belongs to a **live** Claude session
  (pid 59700). Its commits up to `a7658b4` are merged. Merge its later commits the same way,
  and remove the worktree only once that session has ended.
- Neither `main` is pushed. AutoOS main is 15 commits ahead of `origin/main`; agent-skills is 11
  ahead (including this plan). Pushing is the operator's call.

### Task 0.5: OpenHands' PowerShell probe on Windows (◆ D10)

Cause, measured 2026-09-18:
- OpenHands' `terminal/factory.py` probes `powershell -Command "Write-Host 'PowerShell Available'"`
  with a 5 s timeout and **without** `-NoProfile`.
- The user's profile takes 5.4–8.9 s in pwsh and 8.4–13.2 s in PowerShell 5.1.
- The probe runs with `AI_AGENT=openhands` set. A profile that returns at once when that
  variable is set starts in 0.5 s.

**Files:**
- Modify: `AutoOS/lib/windows/AutoOS.Install.psm1` (`Add-AutoOSProfileLine`,
  `Set-AutoOSOpenHandsConfig`)
- Test: `AutoOS/tests/run-tests.ps1`

**Interfaces:**
- Produces: `Add-AutoOSProfileLine -ProfilePath <string> -Line <string> -Marker <string> [-Prepend]`

- [ ] **Step 1: Write the failing test** (put it after the existing `Add-AutoOSProfileLine` cases)

```powershell
Test-Case 'Add-AutoOSProfileLine -Prepend puts the line first, exactly once' {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("autoos-prof-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $p = Join-Path $tmp 'Microsoft.PowerShell_profile.ps1'
    Set-Content -Path $p -Value 'Write-Host slow-profile' -Encoding utf8
    $line = "if (`$env:AI_AGENT -eq 'openhands') { return }"
    try {
        Initialize-AutoOSInstaller -DryRun:$false -Answers @{} -RepoRoot $Root
        Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line $line -Marker "AI_AGENT -eq 'openhands'" 6>$null
        Add-AutoOSProfileLine -Prepend -ProfilePath $p -Line $line -Marker "AI_AGENT -eq 'openhands'" 6>$null
        $lines = @(Get-Content -Path $p)
        Assert-Equal $lines[0] '# added by AutoOS'
        Assert-Equal $lines[1] $line
        Assert-Equal @($lines | Where-Object { $_ -like '*AI_AGENT*' }).Count 1
        Assert-Contains $lines 'Write-Host slow-profile'
    } finally { Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
```

- [ ] **Step 2: Run it and see it fail**

Run: `pwsh -NoProfile -File tests/run-tests.ps1 -Filter 'Prepend'`
Expected: FAIL (`A parameter cannot be found that matches parameter name 'Prepend'`).

- [ ] **Step 3: Implement.** In `Add-AutoOSProfileLine`, add `[switch]$Prepend` to `param(...)`
  and replace the final `Add-Content` line with:

```powershell
    if ($Prepend) {
        # A guard is useless at the end of a profile: everything slow has already run.
        $body = if (Test-Path $ProfilePath) { [IO.File]::ReadAllText($ProfilePath) } else { '' }
        [IO.File]::WriteAllText($ProfilePath, "# added by AutoOS`r`n$Line`r`n$body", [Text.UTF8Encoding]::new($true))
    } else {
        Add-Content -Path $ProfilePath -Value "`n# added by AutoOS`n$Line"
    }
```

  At the end of `Set-AutoOSOpenHandsConfig`, before the final `Write-AutoOSLine`:

```powershell
    # OpenHands probes PowerShell with a 5 s timeout and without -NoProfile
    # (OpenHands/software-agent-sdk#5133; fix pending in #3913). It sets AI_AGENT, so a
    # profile that returns at once for it keeps the probe fast. Existing profiles only.
    $docs = [Environment]::GetFolderPath('MyDocuments')
    foreach ($prof in @((Join-Path $docs 'PowerShell\Microsoft.PowerShell_profile.ps1'),
                        (Join-Path $docs 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1'))) {
        if (Test-Path $prof) {
            Add-AutoOSProfileLine -Prepend -ProfilePath $prof -Marker "AI_AGENT -eq 'openhands'" `
                -Line "if (`$env:AI_AGENT -eq 'openhands') { return }  # OpenHands' PowerShell probe times out after 5 s (software-agent-sdk#5133)"
        }
    }
```

- [ ] **Step 4: Run it and see it pass**, then run the full Windows suite. Expected: all pass,
  including `PSScriptAnalyzer is clean` and `every PowerShell file has a UTF-8 BOM`.
- [ ] **Step 5: Commit** `fix(openhands): keep OpenHands' PowerShell probe under its 5 s timeout with a profile guard`.

### Task 0.6: Drop the MCP `"timeout": 120.0` entries

OpenHands forwards this value to fastmcp, whose stdio transport ignores it. An open upstream PR
(OpenHands/software-agent-sdk#3254) would read it as **milliseconds**: a 0.12 s tool timeout.

**Files:**
- Modify: `AutoOS/lib/linux/install.sh`, `AutoOS/lib/windows/AutoOS.Install.psm1` (the
  `mcp_cfg[...]` blocks in the OpenHands setup scripts)
- Test: `AutoOS/tests/run-tests.sh`

- [ ] **Step 1: Failing test.** In the case
  `setup_openhands_config writes the resolved Ollama address into the profile and the keyless default`,
  add a fourth printed value `any("timeout" in v for v in s["agent_settings"]["mcp_config"].values())`
  and extend the expected string with ` False`. Run it; expected FAIL (prints `True`).
- [ ] **Step 2:** Delete every `"timeout": 120.0,` line in both installers' `mcp_cfg[...]`
  dicts.
- [ ] **Step 3:** Re-run; expected PASS. Run both full suites.
- [ ] **Step 4: Commit** `fix(openhands): stop writing MCP timeouts OpenHands ignores (and may soon read as ms)`.

### Task 0.7: Move the remaining Serena memories into Omnigraph

Since 2026-09-18, Serena's memory and onboarding tools are **off globally**
(`~/.serena/serena_config.yml`, verified by a live tool listing), and Omnigraph is the only memory
layer. Two sets of old Serena memory files are left on disk:
- AutoOS's `.serena/memories/` (6 files, git-ignored);
- the ended usb session's copy in `Code/AutoOS-worktrees/.sessions/usb-improvements/serena-memories/`
  (5 files).

- [ ] **Step 1:** Load the `structured-memory` skill and read its `references/operations.md`.
  Read the `autoos` graph's schema (`schema_get`) and take the head commit (`commits_list`).
- [ ] **Step 2:** For each memory file, sort every statement into one of four piles:
  - already in `AGENTS.md`/docs → drop;
  - a durable decision, rule or convention → an Omnigraph node of the matching type, with its
    source file as provenance;
  - a repo fact that belongs in docs → a line in the right README;
  - stale → drop.

  Where the two sets disagree, the newer file wins, but check it against the code first.
- [ ] **Step 3:** `load` in `merge` mode, then `commits_list` again: the head must have moved. List
  the nodes written in the DONE note.
- [ ] **Step 4:** Delete AutoOS's `.serena/memories/` and the archived copy only after Step 3
  verified. **Operator OK needed** for the deletion.

### Task 0.8: AutoOS enforces "one tool per job" for Serena on every machine

**Files:**
- Modify: `AutoOS/lib/linux/install.sh`, `AutoOS/lib/windows/AutoOS.Install.psm1` (where Serena
  is installed/registered: `install_mcp_serena` / its PowerShell twin)
- Create: `AutoOS/tools/check-serena-tools.py` (the start-up probe used on 2026-09-18)
- Test: both suites

- [ ] **Step 1: Failing test.** Given a temp `~/.serena/serena_config.yml` whose
  `excluded_tools` holds only `read_file`, a real (non-dry) run of the new
  `ensure_serena_exclusions` / `Set-AutoOSSerenaExclusions` must leave these, deduplicated and
  in this order:
  - the file tools `create_text_file`, `read_file`, `execute_shell_command`, `list_dir`,
    `search_for_pattern`, `find_file`, `replace_content`, `replace_in_files`;
  - the memory tools `onboarding`, `write_memory`, `read_memory`, `list_memories`,
    `edit_memory`, `rename_memory`, `delete_memory`.

  It keeps every other key byte-for-byte and writes a backup. A second run reports `skipped` and
  writes nothing. A missing config file is created with just that key.
- [ ] **Step 2:** Implement it as a read → merge → write-back of **only** `excluded_tools` (rule 2),
  using a line-based edit of that one YAML list so comments survive. Call it after Serena's
  registration.
- [ ] **Step 3:** Commit `tools/check-serena-tools.py`:
  - it starts `serena start-mcp-server --context claude-code` over stdio;
  - it runs MCP `initialize` + `tools/list`;
  - it exits 1 if any memory or onboarding tool is exposed or `find_symbol` is missing.

  It is **not** a CI test (it needs Serena and network for `uvx`). The installer runs it after
  `ensure_serena_exclusions` when Serena is present, and reports its result. `mcp-servers-setup`
  says to run it after every Serena upgrade.
- [ ] **Step 4:** Both suites green. **Commit** `feat(serena): memory and file tools excluded globally; Omnigraph is the only memory layer`.

### Task 0.9: Tool hygiene: one tool per job, pinned, current (**approved by the operator, 2026-09-18**)

Evidence: `docs/superpowers/plans/2026-09-18-tool-audit.md` (read-only audit, 2026-09-18; every
row marked measured or inferred). The operator approved groups 0.9a–0.9e. Task 0.10 (the
data-affecting upgrades) needs its own go at execution time.

**Rules for every sub-task:**
- **Before:** record the current versions/entries (`--version`, `npm ls -g --depth=0`,
  `uv tool list`, `docker ps -a`, a redacted dump of the config entry). Back up every file you
  edit as `<file>.bak-<date>-0.9x`.
- **After:**
  - restart the affected agent clients;
  - run `tools/check-serena-tools.py` (Task 0.8);
  - make one real call per MCP server you touched (for example omnigraph `health`, context7
    `resolve-library-id`, playwright `browser_navigate` to `about:blank`);
  - record before → after and the checks in "Where things stand".
- **Machine:** check free disk before pulls and builds (the orders' §5). Don't run updates while
  a batch run is active.
- **Never print a secret.** Redact values of keys named `*token*`, `*key*`, `*secret*`,
  `*password*` in every dump.

#### 0.9a: Security: the Omnigraph token out of `~/.claude.json`

Measured: the AutoOS local-scope `omnigraph` entry in `~/.claude.json` passes
`-e OMNIGRAPH_TOKEN=<literal, 64 chars>`. Both repos' `.mcp.json` files use `${OMNIGRAPH_TOKEN}`,
and `OMNIGRAPH_TOKEN` is set in the user environment.

- [ ] Back up `~/.claude.json`. In that entry's `args`, replace the literal with
  `OMNIGRAPH_TOKEN=${OMNIGRAPH_TOKEN}`, editing only that one string. Check the shape with a
  script that prints `env-reference` or `LITERAL (<n> chars)`, never the value.
- [ ] Restart Claude Code in AutoOS; omnigraph `health` answers for graph `autoos`.
- Note: the container still receives the value in its environment, which is inherent to
  `docker -e`. The fix removes it from the config file and from process command lines.

#### 0.9b: One omnigraph entry for AutoOS

Measured: AutoOS has a local-scope docker entry (image `omnigraph-mcp:latest` = 0.8.0), which is
the one in use, **and** a project `.mcp.json` npx entry (unpinned; `enabledMcpjsonServers: []`,
so dormant).

- [ ] Keep the **committed** `AutoOS/.mcp.json` entry as the one home, because every clone and
  every client that reads `.mcp.json` gets it:
  - pin it to `@modernrelay/omnigraph-mcp@0.8.0`;
  - keep `env` by reference;
  - commit (AutoOS branch; "one tool per job" in the message).
- [ ] Remove the local-scope entry (`claude mcp remove omnigraph --scope local` from the AutoOS
  folder), then approve the project server (`enabledMcpjsonServers: ["omnigraph"]`, the helper
  `Enable-AutoOSProjectMcpServer` / `enable_project_mcp_server` does this).
- [ ] Verify: one `omnigraph` in `claude mcp list` from AutoOS; `health` answers for graph
  `autoos`. If the npx route can't reach the server (the docker route used the
  `mcp-server_mcp-net` network), revert to the docker entry **in `.mcp.json`**, mirroring
  agent-skills. Record which, and why.

#### 0.9c: Pin what floats

- [ ] `@modernrelay/omnigraph-mcp@0.8.0` in agy (`~/.gemini/config/mcp_config.json`) and in the
  OpenHands config that the AutoOS installers write. The installers change **with a test**:
  the `mcp_cfg["omnigraph"]` args contain `@modernrelay/omnigraph-mcp@0.8.0`.
- [ ] `serena-agent==1.7.0` in agy, codewhale (`~/.codewhale/mcp.json`) and the installers'
  OpenHands `mcp_cfg["serena"]` (test likewise). Then run `uv tool uninstall serena-agent`
  (a stale 1.5.3).
- [ ] Pin `@playwright/mcp`, `@upstash/context7-mcp` and `graphifyy[mcp]` to the versions from
  0.9e, in the same places and the same way.
- [ ] Both AutoOS suites green. Commit `fix(mcp): pin every MCP server the installers write`.

#### 0.9d: Remove overlaps and dead weight

- [ ] **Memory:**
  - remove the `mem0` entry from `~/.codewhale/mcp.json`;
  - `uv tool uninstall mem0-mcp-selfhosted mem0-mcp-server`;
  - `npm rm -g @modelcontextprotocol/server-memory`;
  - `docker image rm` the mem0 and `mcp/memory` images, listing them first with
    `docker images --format '{{.Repository}}:{{.Tag}}' | grep -Ei 'mem0|mcp/memory'`.
- [ ] **Local LLM:**
  - `docker stop ollama-agent && docker rm ollama-agent`, after `docker inspect` confirms it
    mounts the same `C:/Apps/ollama` store (the models stay, since that folder is shared);
  - uninstall the legacy Windows Ollama 0.5.11 (Apps & Features → Ollama, or its
    `unins000.exe /SILENT`);
  - `docker image rm ghcr.io/ggml-org/llama.cpp:server-cuda`.
- [ ] **OpenHands:** remove the `ws-openhands-agent` service from
  `infra/local-ai/docker-compose.yml`, and correct `2026-09-17-autoos-l2-DONE.md:45`.
  agent-canvas is the only OpenHands.
- [ ] **Browser, Claude only:** `claude mcp remove playwright --scope user`. The built-in pane
  handles dev servers and Claude-in-Chrome handles logged-in browsing. Playwright stays, pinned,
  for agy, codewhale and OpenHands.
- [ ] **Stale or hard-wired paths:**
  - agy and codewhale launch superpowers from the leftover
    `agent-skills/mcp-servers/servers/superpowers/build`. Build the maintained source
    (`infra/mcp-servers/servers/superpowers`: `npm ci && npm run build`) and point both at its
    `build/index.js`; Task 5.5 later moves it to `third_party/superpowers-mcp`.
  - codewhale's graphify is wired to `research-repo/graphify-out/graph.json`; make it the cwd-relative
    `graphify-out/graph.json`.
- [ ] **Wired nowhere:**
  - `npm rm -g @modelcontextprotocol/server-filesystem @modelcontextprotocol/server-github @modelcontextprotocol/server-postgres @qwen-code/qwen-code`;
  - `uv tool uninstall mcp-server-fetch mcp-server-git mcp-server-time mcp-server-docker mcp-server-redis mcp-server-jupyter`;
  - `homelab-mcp` stays unwired until someone needs it (recorded, not removed).
- [ ] **Skills:** nothing is unlinked by usage count. `unattended-orchestration` showed zero uses
  only because it wasn't linked before 2026-09-18. The orchestration and review duplicates are
  resolved by merging in Phase 6.
- [ ] After this group, check that `docker ps` shows exactly one `ollama`, and no mem0 or
  `ws-openhands-agent`. Check that `npm ls -g --depth=0` and `uv tool list` lack the removed
  packages. Record the disk space freed (`docker system df` before/after).

#### 0.9e: Updates, now

| Tool | From → to | Command |
|---|---|---|
| agent-canvas (agent-server 1.48.0 → 1.49.1) | 1.19.0 → 1.20.0 | `npm i -g @openhands/agent-canvas@1.20.0`. It still lacks the PowerShell fix (PR #3913 open), so keep Task 0.5 |
| Playwright MCP | 0.0.80 → 0.0.81 | `npm i -g @playwright/mcp@0.0.81` |
| context7 MCP | 4.0.4 → 4.1.1 | `npm i -g @upstash/context7-mcp@4.1.1` |
| graphify | 0.9.30 → 0.9.63 | `uv tool install --force "graphifyy[mcp]==0.9.63"` |
| opencode (WSL) | 1.18.30 → 1.18.31 | `wsl -- opencode upgrade` |
| codex | 0.153.4 → 0.155.0 | `npm i -g @openai/codex@0.155.0` |
| Claude Code (WSL) | 2.1.236 → 2.1.276 | `wsl -- claude update` |
| gh, git, pwsh | 2.92 → 2.101; 2.55.0.windows.3 → .5; 7.5.8 → 7.6.6 | `winget upgrade GitHub.cli Git.Git Microsoft.PowerShell` |

- [ ] Update one row at a time, then verify it with `--version` plus the MCP call where one
  applies. For agent-canvas: start it (`agent-canvas --info`, then launch), check the UI loads,
  run one OpenHands conversation on a throwaway worktree, and stop it.
- [ ] A row that breaks gets rolled back to its "from" version, and the failure is recorded.

### Task 0.10: Planned upgrades (each needs the operator's go when it's run)

| Upgrade | Why it waits | Before starting | Go/no-go check |
|---|---|---|---|
| Omnigraph server v0.8.1 → v0.11.0, together with omnigraph-mcp → 0.10.x | Coordinated upgrade: v0.9/v0.10 graphs need an **offline storage upgrade**, and the v0.10 notes break the graph vocabulary | A snapshot of all 8 graphs (`snapshot`), a copy of `infra/mcp-servers/data/`, and a dry run on a copy | Every graph answers `health` and one query; `commits_list` heads unchanged. Roll back from the copy otherwise |
| uv 0.9.18 → 0.12.x (Windows), 0.9.6 → 0.12.x (WSL) | Breaking-changes sections in 0.10 (venv replacement) and 0.11 (TLS verifier) | `git grep -n "uv_build" -- '*.toml'` in every repo that pins upper bounds | Both AutoOS suites and the agent-skills pytest green |
| Ollama container 0.20.4 → 0.34.2 | 14 minor versions not reviewed; after 0.9d, only one container remains | Release notes read; the image tag pinned in the compose file | The 7B coder answers; tokens/s within 10% of the 41.9 measured on 2026-09-17 |
| CAO 2.5.0 → 2.5.1 | Only on GitHub, not PyPI | — | When PyPI has it: `uv tool upgrade cli-agent-orchestrator`, then `python -m cao check` |

---

## Phase 1 — Make agent-skills' current state publishable (agent-skills, branch `prep/public`)

Tasks 1.1–1.4 and 1.6 are independent (one worktree each, in `Documents/Code/agent-skills-worktrees/`).
Task 1.5 touches every PowerShell file and runs **last**.

### Task 1.1: A publication scanner

**Files:**
- Create: `scripts/public-scrub/scan.py`
- Create: `scripts/public-scrub/patterns.txt` (**private**: it is never migrated)
- Test: `scripts/public-scrub/test_scan.py`

**Interfaces:**
- Produces: `python scripts/public-scrub/scan.py [--patterns FILE] [--git-tree REV] [paths…]`.
  It exits 1 and prints `path:line: <pattern-name>` for each hit, and exits 0 when clean.
  `--git-tree REV` scans `git ls-tree -r REV` blobs instead of the working tree.

- [ ] **Step 1: Write `patterns.txt`** (one `name<TAB>regex` per line).
  - Put the literal private values here (the omnigraph domain, each `*.vm` host, the
    `192.168.178.` prefix, both user home paths, the e-mail address, each private project
    name).
  - Add generic rules: `rfc1918<TAB>\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+`,
    `win-home<TAB>[A-Za-z]:\\\\Users\\\\[^\\\\\s]+`, `unix-home<TAB>/home/[a-z][a-z0-9_-]+`,
    `key-shape<TAB>(sk-or-v1-[0-9a-f]{16,}|sk-ant-[A-Za-z0-9-]{16,}|LLM_[A-Za-z0-9]{12,})`.
- [ ] **Step 2: Write the failing test**

```python
import subprocess, sys, pathlib

SCAN = pathlib.Path(__file__).with_name("scan.py")

def run(tmp_path, text):
    (tmp_path / "patterns.txt").write_text("host\tsecret\\.example\\.lan\n", encoding="utf-8")
    (tmp_path / "doc.md").write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, str(SCAN), "--patterns", str(tmp_path / "patterns.txt"),
                           str(tmp_path / "doc.md")], capture_output=True, text=True)

def test_hit_reports_path_line_and_name(tmp_path):
    r = run(tmp_path, "ok\nsee secret.example.lan\n")
    assert r.returncode == 1
    assert "doc.md:2: host" in r.stdout

def test_clean_file_exits_zero(tmp_path):
    assert run(tmp_path, "nothing here\n").returncode == 0
```

- [ ] **Step 3: Run it and see it fail.**
  `python -m pytest scripts/public-scrub/test_scan.py -q`. Expected: FAIL (scan.py missing).
- [ ] **Step 4: Implement `scan.py`**

```python
"""Fail when a file holds anything AutoOS's public-repo rule forbids."""
import argparse, pathlib, re, subprocess, sys

def load(path):
    rules = []
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#"):
            name, rx = raw.split("\t", 1)
            rules.append((name, re.compile(rx)))
    return rules

def blobs(args):
    if args.git_tree:
        names = subprocess.run(["git", "ls-tree", "-r", "--name-only", args.git_tree],
                               capture_output=True, text=True, check=True).stdout.splitlines()
        for n in names:
            data = subprocess.run(["git", "show", f"{args.git_tree}:{n}"], capture_output=True).stdout
            yield n, data.decode("utf-8", "replace")
    else:
        for p in args.paths:
            for f in ([pathlib.Path(p)] if pathlib.Path(p).is_file() else pathlib.Path(p).rglob("*")):
                if f.is_file() and ".git" not in f.parts:
                    yield str(f), f.read_text(encoding="utf-8", errors="replace")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patterns", default=str(pathlib.Path(__file__).with_name("patterns.txt")))
    ap.add_argument("--git-tree")
    ap.add_argument("paths", nargs="*", default=["."])
    args = ap.parse_args()
    rules, hits = load(args.patterns), 0
    for name, text in blobs(args):
        for i, line in enumerate(text.splitlines(), 1):
            for rname, rx in rules:
                if rx.search(line):
                    print(f"{name}:{i}: {rname}"); hits += 1
    sys.exit(1 if hits else 0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 5:** Run the test; expected PASS. Then run the baseline:
  `python scripts/public-scrub/scan.py --git-tree HEAD > scrub-baseline.txt`. Expected: many
  hits. This list is the to-do for 1.2–1.4, and it is **not** committed.
- [ ] **Step 6: Commit** `feat(scrub): publication scanner for the AutoOS migration`.

### Task 1.2: Parameterise private infrastructure values (◆ D3)

**Files:**
- `infra/mcp-servers/{docker-compose*.yml,.env.*.example,README.md,omnigraph-setup/*,bin/graphify-mcp,servers/*/README.md,servers/graphify-mcp/Dockerfile}`
- `.mcp.json`
- `skills/mcp-servers-setup/SKILL.md`
- `skills/repository-index/SKILL.md`
- `skills/structured-memory/references/operations.md`
- every other file in `scrub-baseline.txt` under `infra/` or `skills/`

- [ ] **Step 1:** Replace every host, domain and LAN IP with an environment reference:
  - compose: `${OMNIGRAPH_HOST}`, `${OMNIGRAPH_MINIO_HOST}`, `${CODING_HOST}` …
  - `.example` files: `omnigraph.example.org` and `coding.example.internal` placeholders;
  - prose: a placeholder plus "set in `~/.config/autoos/site.env`".
- [ ] **Step 2:** Add `infra/mcp-servers/site.env.example`, listing every variable with its
  placeholder. Document the lookup order in `infra/mcp-servers/README.md`: environment, then
  `~/.config/autoos/site.env`, then compose defaults.
- [ ] **Step 3:** Run `docker compose -f infra/mcp-servers/docker-compose.yml --env-file infra/mcp-servers/site.env.example config -q`.
  Expected: exit 0. This renders the file without starting anything.
- [ ] **Step 4:** `scan.py infra skills .mcp.json` → no hits of the host/IP rules.
- [ ] **Step 5:** Run the infra pytest suites
  (`python -m pytest infra/mcp-servers/scripts infra/mcp-servers/omnigraph-setup infra/mcp-servers/servers/homelab-mcp/tests -q`).
  Expected: same pass count as before the change.
- [ ] **Step 6: Commit** `refactor(infra): site-specific hosts come from site.env, never from the tree`.

### Task 1.3: Remove user paths and point secrets at the user scope (◆ D4)

**Files:**
- `skills/unattended-orchestration/cao/config.py`
- `skills/unattended-orchestration/deepseek_review.sh:17`
- `skills/unattended-orchestration/deepseek_chunked_review.sh`
- `skills/unattended-orchestration/tests/cao/test_live_providers.py:22`
- `skills/unattended-orchestration/tests/cao/conftest.py`
- `secrets/README.md`

**Interfaces:**
- Produces: the secrets lookup order used everywhere:
  1. `$AUTOOS_SECRETS` (a file path);
  2. `~/.config/autoos/api_keys.conf`;
  3. the repo's `secrets/api_keys.conf` (legacy walk-up).

- [ ] **Step 1: Failing pytest** in `skills/unattended-orchestration/tests/cao/test_config.py`
  (`find_secrets(start) -> Path` is the resolver, at `cao/config.py:21`; `load_secrets(path)`
  only parses):

```python
def test_env_then_user_scope_then_repo(tmp_path, monkeypatch):
    from cao.config import find_secrets
    home = tmp_path / "home"; (home / ".config" / "autoos").mkdir(parents=True)
    repo = tmp_path / "repo"; (repo / ".git").mkdir(parents=True); (repo / "secrets").mkdir()
    (repo / "secrets" / "api_keys.conf").write_text("deepseek=repo\n")
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AUTOOS_SECRETS", raising=False)
    assert find_secrets(repo) == repo / "secrets" / "api_keys.conf"          # legacy only
    user = home / ".config" / "autoos" / "api_keys.conf"; user.write_text("deepseek=user\n")
    assert find_secrets(repo) == user                                         # user scope wins
    explicit = tmp_path / "x.conf"; explicit.write_text("deepseek=env\n")
    monkeypatch.setenv("AUTOOS_SECRETS", str(explicit))
    assert find_secrets(repo) == explicit                                     # env wins
```

  Also add an autouse fixture to `tests/cao/conftest.py` that points `HOME` and `USERPROFILE`
  at `tmp_path` and deletes `AUTOOS_SECRETS`. Otherwise the existing
  `test_find_secrets_walks_up_to_the_repo_root` / `…prefers_the_nearest_file` fail on any
  machine that already has `~/.config/autoos/api_keys.conf`.
- [ ] **Step 2:** Run
  `python -m pytest skills/unattended-orchestration/tests/cao/test_config.py -q`. Expected: the
  new test FAILs at the second assert; the others pass.
- [ ] **Step 3:** Implement at the top of `find_secrets`:

```python
    env = os.environ.get("AUTOOS_SECRETS")
    if env and Path(env).is_file():
        return Path(env)
    user = Path.home() / ".config" / "autoos" / "api_keys.conf"
    if user.is_file():
        return user
```

  (Add `import os` if it's missing; the walk-up below stays as the legacy fallback.)

  In both `deepseek_*review.sh` scripts, replace the default with
  `${DEEPSEEK_SECRETS:-${AUTOOS_SECRETS:-$HOME/.config/autoos/api_keys.conf}}`, falling back to
  the existing `--git-common-dir` path when that file is absent. In `test_live_providers.py`,
  replace the `/mnt/c/...` literal with `pathlib.Path(__file__).resolve().parents[4]` (the repo
  root: `tests/cao/` → skill → `skills/` → root).
- [ ] **Step 4:** Run `python -m pytest skills/unattended-orchestration/tests -q -p no:libtmux`.
  Expected: 502 passed, 13 skipped, or better.
- [ ] **Step 5: Commit** `refactor(secrets): user-scope api_keys.conf first; no user paths in the tree`.

### Task 1.4: Scrub or drop the private documents (◆ D2)

- [ ] **Step 1:** For each file in `scrub-baseline.txt` under `docs/`, `prompts/`, `starters/`,
  `webpage/`, root `*.md`, choose one:
  - (a) keep, scrubbed: project names become "a sibling analysis repo", paths and hosts become
    placeholders;
  - (b) list in `scripts/public-scrub/migrate-exclude.txt` (one git pathspec per line).

  The exclude list always contains `EVIDENCE.md`, `webpage/`, `handoff.config.json`,
  `docs/REMOTE-SYNC-TEST-PLAN.md`, `infra/local-ai/`, `CHANGELOG.md` and `scripts/public-scrub/`.
- [ ] **Step 2:** Verify:
  `git ls-files | grep -v -f <(sed 's/^/^/' scripts/public-scrub/migrate-exclude.txt) | xargs python scripts/public-scrub/scan.py`.
  Expected: exit 0.
- [ ] **Step 3: Commit** `docs: scrub or exclude private documents before the migration`.

### Task 1.5: BOM and line endings for every PowerShell file (last in Phase 1)

- [ ] **Step 1:**

```powershell
Get-ChildItem -Recurse -Include *.ps1,*.psm1,*.psd1 -File | Where-Object { $_.FullName -notmatch '\\\.git\\' } | ForEach-Object {
  $t = [IO.File]::ReadAllText($_.FullName) -replace "`r?`n", "`r`n"
  [IO.File]::WriteAllText($_.FullName, $t, [Text.UTF8Encoding]::new($true)) }
```

- [ ] **Step 2:** Add `*.psm1 text eol=crlf` and `*.psd1 text eol=crlf` to `.gitattributes`,
  then `git add --renormalize .`.
- [ ] **Step 3:** Run all 7 PowerShell suites **under both `pwsh` and `powershell` (5.1)**.
  Expected: all exit 0.
- [ ] **Step 4: Commit** `chore: UTF-8 BOM and CRLF for every PowerShell file (AutoOS rule)`.

### Task 1.6: Licences of vendored and adapted content

- [ ] **Step 1:** For `infra/mcp-servers/servers/superpowers` (MIT, erophames) and the 5
  adapted skills (`skills/SYNC.md` lists their sources), record licence and origin in
  `THIRD_PARTY.md`. Any source without a compatible licence goes to `migrate-exclude.txt`.
- [ ] **Step 2: Commit** `docs: record third-party origins and licences before publication`.

**Phase 1 exit criterion:** merge `prep/public` into agent-skills `main`. Then
`git ls-files` minus `migrate-exclude.txt`, piped through `scan.py`, exits 0, and all
agent-skills suites pass. **The migration input is exactly this file list at this commit.**

---

## Phase 2 — One uniform layout, and the migration into it (AutoOS, branch `feat/unify`, one lane)

### Task 2.1: ADR 0006 — the unified layout (◆ D1)

**Files:** Create `AutoOS/docs/decisions/0006-one-repository-layout.md` (status *proposed*). The
operator accepts it before Task 2.2.

The layout it fixes:

```
AutoOS/
├── AGENTS.md          tier 0 · ≤80 lines · hard rules, where agents work, "read INDEX.md next"
├── INDEX.md           tier 1 · ≤6 KB · generated folder map + "I want to… → go to" + skill list
├── README.md          for humans
├── CLAUDE.md          pointer to AGENTS.md + the Claude-only delta
├── GEMINI.md          pointer to AGENTS.md + the Gemini-only delta
├── setup.ps1, setup.sh   the two entry points
├── catalog/           DATA only: windows/linux/macos.json, images, engines, llm-models (+ schemas)
├── lib/               installer CODE: windows/*.psm1, linux/*.sh
├── web/               browser GUI
├── skills/            every agent skill: <name>/SKILL.md (+ references/, scripts/, tests/)
├── agents/            agent RUNTIME configuration (not instructions): openhands/ (LLM + agent profiles)
├── infra/             long-running services: mcp-servers/, remote-access/
├── remote/            provisioning OTHER machines: ansible/, ubuntu-autoinstall/
├── templates/         files copied to user scope: secrets/, site.env.example, shell/, terminal/
├── tools/             repo maintenance: gen-index.py, check-agent-docs.py, check-public.py, eval_routing.py, find-agent-skills-refs.*
├── tests/             suites + fixtures
├── docs/              guides (*.md), decisions/, plans/, specs/, research/, prompts/
└── third_party/       vendored code, never edited (superpowers-mcp/ …)
```

Rules the ADR states:
- One home per fact.
- A folder exists only if it has a README.
- Skills hold instructions. Code a skill needs lives inside that skill's folder (the unattended
  runner's portability test copies the folder).
- Runtime state never lives in the tree. Worktrees and agent scratch go to `<repo>-worktrees/`
  beside it (ADR 0007); everything else goes to user scope.

- [ ] **Step 1:** Write the ADR (context: two repos, one public; decision: the tree and rules
  above; consequences: the moves in 2.2/2.3). **Commit** `docs(adr): 0006 one repository layout (proposed)`.

### Task 2.2: Move AutoOS's own folders into the layout

| From | To |
|---|---|
| `openhands/` | `agents/openhands/` |
| `.claude/skills/autoos-install/` | `skills/autoos-install/` (`.claude/skills/autoos-install` becomes a one-line pointer file or a link) |
| `Windows/ansible/` | `remote/ansible/` |
| `Linux/ubuntu_autoinstall/` | `remote/ubuntu-autoinstall/` |
| `Windows/Terminal/`, `Windows/powershell/`, `Linux/bash/` | `templates/terminal/`, `templates/shell/powershell/`, `templates/shell/bash/` |
| `tests/check-vendored.py`, `tests/check-links.py` | stay (they are tests) |

- [ ] **Step 1: One move per commit.** `git mv`, then
  `git grep -n "<old path>"` → update every reference (installers, tests, CI, docs, catalog).
  Run both suites, `python tests/check-links.py .` and `python tests/check-vendored.py`.
  Commit as `refactor(layout): <from> -> <to>`.
- [ ] **Step 2:** For `openhands/` → `agents/openhands/`, the installers' template paths
  (`os.path.join(REPO_ROOT, "openhands", …)` in `install.sh`, `os.path.join(repo_root, 'openhands', …)`
  in `AutoOS.Install.psm1`) and both vendored-profile tests change together. The e2e cases
  (`setup_openhands_config writes…`, `the embedded OpenHands setup script writes…`) must pass
  unchanged.

### Task 2.3: Migrate agent-skills' current state by mapping

The input is the Phase 1 exit commit's file list. It is **not** a directory copy and **not**
history.

| agent-skills (tracked) | AutoOS |
|---|---|
| `skills/<name>/` (all 14) | `skills/<name>/` — Phase 6 consolidates the orchestration skills |
| `skills/SYNC.md` | `skills/SYNC.md` |
| `infra/mcp-servers/` (without `servers/superpowers/`) | `infra/mcp-servers/` |
| `infra/mcp-servers/servers/superpowers/` | `third_party/superpowers-mcp/` |
| `infra/remote-access/` | `infra/remote-access/` |
| `docs/decisions/0001–0008` | `docs/decisions/0008–0015` (titles and every link renumbered) |
| `docs/superpowers/plans/`, `docs/superpowers/specs/` | `docs/plans/`, `docs/specs/` |
| `docs/architecture.md`, `docs/agent-compatibility.md` | `docs/agents-architecture.md`, `docs/agent-compatibility.md` |
| `prompts/` (scrubbed survivors) | `docs/prompts/` |
| `starters/` (scrubbed survivors) | `templates/starters/` |
| `secrets/README.md`, `secrets/api_keys.conf.example` | `templates/secrets/` |
| `THIRD_PARTY.md` | merged into AutoOS `third_party/README.md` |
| `.mcp.json`, `.graphifyignore`, `.serena/project.yml` | merged into AutoOS's own |
| `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `CONTRIBUTING.md` | merged in Task 3.5 (content), not copied |
| everything in `migrate-exclude.txt` | not migrated |

- [ ] **Step 1: Write the migration script** `tools/migrate-agent-skills.py`. It is committed
  in AutoOS and deleted in Phase 9.
  - It reads `git -C <agent-skills> ls-files` at a given commit and drops the exclude list.
  - It copies each file through the mapping table above (a Python dict in the script, ordered
    so that the longest prefix wins).
  - It refuses to overwrite an existing AutoOS file, printing the conflict instead.
  - It prints `unmapped: <path>` for any file no rule covers, and exits 1 if there are any.
- [ ] **Step 2: Test it first**, with `tests/fixtures/migrate/` holding a tiny fake source repo
  (built by the test with `git init`, 4 files: one per mapping kind plus one unmapped). Expected:
  exit 1 and `unmapped: other/x.md`. After a mapping rule for `other/` is added in the test's own
  table, exit 0 and the files are at their targets. Add it to `tests/run-tests.sh` as case
  `migrate-agent-skills maps every tracked file or refuses`.
- [ ] **Step 3: Run it for real:**
  `python tools/migrate-agent-skills.py --source ../agent-skills --rev <phase-1-exit-sha>`.
  Expected: exit 0. Renumber the ADRs with a sed map (`0001→0008 … 0008→0015`) over every
  migrated `*.md`.
- [ ] **Step 4:** Run agent-skills' private scanner over the AutoOS tree:
  `python ../agent-skills/scripts/public-scrub/scan.py .`. Expected: exit 0.
- [ ] **Step 5: Commit** `feat: migrate agent-skills' current state (@<sha>) into the unified layout`.

### Task 2.4: Ignore rules and attributes

- [ ] **Step 1:** Append to AutoOS `.gitignore`: `/secrets/*`, `infra/mcp-servers/data/`,
  `cluster.yaml`, `seed/*.jsonl`, `homelab.yaml`, `/[A-Za-z]:/`, `/output/`, `/.cao-leases/`,
  `.graph-backup/`, `agent_keys.yaml`, `**/.agent-state/`. Then un-ignore
  `!.serena/project.yml`, `!**/.serena/project.yml` and `!.env.*.example`.
- [ ] **Step 2:** Run `git status --ignored` and confirm that no migrated file is ignored.
  **Commit** `chore: ignore rules for the migrated agent tree`.

### Task 2.5: CI for the unified tree (◆ D5)

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `tools/check-public.py` (generic rules only; the literal list stays private)
- Modify: `tests/run-tests.ps1` (PSScriptAnalyzer scope)

- [ ] **Step 1: `check-public.py`.** Copy `scan.py`'s engine with only the generic rules
  inline: RFC 1918 IPv4, Windows and Unix user-home paths, e-mail addresses (allow
  `noreply@anthropic.com` and `*@example.*`), and key shapes. Exempt `tests/fixtures/**` and
  `*.example*`. Add a failing case to `tests/run-tests.sh` first: a temp file holding
  `192.0.2.10` must make it exit 1.
- [ ] **Step 2:** Add an `agents` job to `ci.yml`, matrix `ubuntu-latest` + `windows-latest`:
  - `pip install pytest`;
  - `python -m pytest skills infra -q -p no:libtmux`;
  - `pwsh -File` on each `skills/unattended-orchestration/tests/*.Tests.ps1`;
  - on Windows, the same suites under `powershell`.
- [ ] **Step 3:** Extend the Linux job:
  - `shellcheck` over the migrated `*.sh`;
  - `check-links.py .`;
  - `python tools/check-public.py .`;
  - `python tools/check-agent-docs.py` (from Task 3.1).
- [ ] **Step 4:** Extend `PSScriptAnalyzer is clean` in `run-tests.ps1` to cover
  `skills/**/*.ps*1` and `infra/**/*.ps*1`. Fix what it reports, or add a documented exclusion.
- [ ] **Step 5:** Push the branch and see every job green. **Commit**
  `ci: run the migrated agent suites and a generic public-content check`.

---

## Phase 3 — The navigation layer (AutoOS, branch `feat/navigation`)

Why: an agent pointed at the repo must reach the right skill in **≤ 3 reads**:
`AGENTS.md` → `INDEX.md` → the one `SKILL.md` or `README.md` it names. That must hold for a
local 7B model whose whole context is 32k tokens, so every tier has a budget and a test.

| Tier | File | Budget | Owns |
|---|---|---|---|
| 0 | `AGENTS.md` | ≤ 80 lines | Hard rules, where agents work (ADR 0007), how to test, "read `INDEX.md` next" |
| 1 | `INDEX.md` | ≤ 6,000 bytes | Folder map and skill list (**generated**), "I want to… → go to" (hand-written, ≤ 25 rows) |
| 2 | `<folder>/README.md` | ≤ 2,400 bytes; first line `# <folder> — <purpose>` | What's here, the entry points, what is *not* here |
| 3 | `skills/<name>/SKILL.md` | description ≤ 300 chars; body ≤ 250 lines | When to use it, the workflow |
| 4 | `skills/<name>/references/*.md` | — (each starts with a one-line summary) | Detail read only while doing the thing |
| — | `skills/repository-index/SKILL.md` | tier-3 budget | The full router for capable agents: MCP servers, decision tree, skill tables (generated) |

### Task 3.1: The budget and coverage checker

**Files:**
- Create: `tools/check-agent-docs.py`
- Test: `tests/run-tests.sh` (new `describe "agent navigation"`)

**Interfaces:**
- Produces: `python tools/check-agent-docs.py [--root DIR]`. It exits 1 and prints one
  `<path>: <rule>` line per violation, and exits 0 when clean.
- Consumes: SKILL.md frontmatter keys `name`, `description`, `category`, where `category` is one
  of `always-on`, `task`, `orchestration`, `review`, `install`.

- [ ] **Step 1: Failing test**

```bash
if it "check-agent-docs flags an over-budget AGENTS.md and a skill missing from INDEX.md"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/skills/demo" "$tmp/lib"
    for i in $(seq 1 81); do echo "line $i"; done > "$tmp/AGENTS.md"
    printf '# INDEX\n' > "$tmp/INDEX.md"
    printf '# lib — code\n' > "$tmp/lib/README.md"; printf '# skills — skills\n' > "$tmp/skills/README.md"
    printf -- '---\nname: demo\ndescription: Use when testing.\ncategory: task\n---\nbody\n' > "$tmp/skills/demo/SKILL.md"
    out="$(python3 tools/check-agent-docs.py --root "$tmp" 2>&1)"; rc=$?
    rm -rf "$tmp"
    if [[ $rc -eq 1 && "$out" == *"AGENTS.md: over 80 lines"* && "$out" == *"skills/demo: not listed in INDEX.md"* ]]
    then pass; else fail "rc=$rc out=$out"; fi
fi
```

- [ ] **Step 2:** Run `bash tests/run-tests.sh --filter check-agent-docs`. Expected: FAIL (no such
  file).
- [ ] **Step 3: Implement**

```python
"""Budgets and routing coverage for the files agents read first, small models included.

The numbers below are the single home of the navigation budgets (ADR 0006).
"""
import argparse, pathlib, re, subprocess, sys

AGENTS_MAX_LINES = 80
INDEX_MAX_BYTES = 6000
README_MAX_BYTES = 2400
SKILL_BODY_MAX_LINES = 250
DESCRIPTION_MAX_CHARS = 300
CATEGORIES = {"always-on", "task", "orchestration", "review", "install"}

def frontmatter(text):
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t")):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
    return meta, (text[m.end():] if m else text)

def top_dirs(root):
    try:
        names = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                               text=True, check=True).stdout.splitlines()
        dirs = {n.split("/", 1)[0] for n in names if "/" in n}
    except (subprocess.CalledProcessError, FileNotFoundError):
        dirs = {p.name for p in root.iterdir() if p.is_dir()}
    return sorted(d for d in dirs if not d.startswith("."))

def check(root):
    errs = []
    agents = root / "AGENTS.md"
    if agents.is_file() and len(agents.read_text(encoding="utf-8").splitlines()) > AGENTS_MAX_LINES:
        errs.append(f"AGENTS.md: over {AGENTS_MAX_LINES} lines")
    index = root / "INDEX.md"
    index_text = index.read_text(encoding="utf-8") if index.is_file() else ""
    if not index_text:
        errs.append("INDEX.md: missing")
    elif len(index_text.encode("utf-8")) > INDEX_MAX_BYTES:
        errs.append(f"INDEX.md: over {INDEX_MAX_BYTES} bytes")
    for d in top_dirs(root):
        readme = root / d / "README.md"
        if not readme.is_file():
            errs.append(f"{d}/: no README.md"); continue
        text = readme.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > README_MAX_BYTES:
            errs.append(f"{d}/README.md: over {README_MAX_BYTES} bytes")
        if not re.match(rf"# {re.escape(d)} — \S", text):
            errs.append(f"{d}/README.md: first line must be '# {d} — <purpose>'")
    router = root / "skills" / "repository-index" / "SKILL.md"
    router_text = router.read_text(encoding="utf-8") if router.is_file() else ""
    for skill in sorted((root / "skills").glob("*/SKILL.md")):
        name = skill.parent.name
        meta, body = frontmatter(skill.read_text(encoding="utf-8"))
        if meta.get("name") != name:
            errs.append(f"skills/{name}: frontmatter name must be '{name}'")
        if not meta.get("description") or len(meta["description"]) > DESCRIPTION_MAX_CHARS:
            errs.append(f"skills/{name}: description missing or over {DESCRIPTION_MAX_CHARS} chars")
        if meta.get("category") not in CATEGORIES:
            errs.append(f"skills/{name}: category must be one of {sorted(CATEGORIES)}")
        if len(body.splitlines()) > SKILL_BODY_MAX_LINES:
            errs.append(f"skills/{name}: body over {SKILL_BODY_MAX_LINES} lines; move detail to references/")
        if f"`{name}`" not in index_text:
            errs.append(f"skills/{name}: not listed in INDEX.md")
        if router_text and f"`{name}`" not in router_text:
            errs.append(f"skills/{name}: not listed in skills/repository-index/SKILL.md")
    return errs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    errs = check(pathlib.Path(ap.parse_args().root))
    print("\n".join(errs))
    sys.exit(1 if errs else 0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4:** Run it; expected PASS. The repository itself doesn't pass yet; that case is
  added in Task 3.6, once it can be green, so CI never sits red mid-phase.
- [ ] **Step 5: Commit** `feat(tools): navigation budgets and skill-coverage checker`.

### Task 3.2: `INDEX.md` and its generator

**Files:**
- Create: `tools/gen-index.py`, `INDEX.md`
- Test: `tests/run-tests.sh`

**Interfaces:**
- Produces: `python tools/gen-index.py [--check]`. It rewrites the text between
  `<!-- gen:folders -->…<!-- /gen:folders -->` and `<!-- gen:skills -->…<!-- /gen:skills -->`
  in `INDEX.md`. With `--check` it exits 1 if the file is stale.
- Consumes: each folder README's first line (`# <folder> — <purpose>`) and SKILL.md
  frontmatter (`name`, `category`, `description`).

- [ ] **Step 1: Failing test**

```bash
if it "INDEX.md is up to date with the folders and skills (tools/gen-index.py --check)"; then
    if python3 tools/gen-index.py --check; then pass; else fail "run: python3 tools/gen-index.py"; fi
fi
```

- [ ] **Step 2:** Run it; expected FAIL (no generator).
- [ ] **Step 3: Implement**

```python
"""Regenerate the generated sections of INDEX.md; --check fails when they are stale."""
import pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

def meta_of(path):
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", path.read_text(encoding="utf-8"), re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1); out[k.strip()] = v.strip().strip('"')
    return out

def folders():
    rows = []
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")):
        readme = d / "README.md"
        if readme.is_file():
            first = readme.read_text(encoding="utf-8").splitlines()[0]
            rows.append(f"| `{d.name}/` | {first.split('—', 1)[-1].strip()} |")
    return "| Folder | What is in it |\n|---|---|\n" + "\n".join(rows)

def skills():
    order = ["always-on", "task", "orchestration", "review", "install"]
    found = [meta_of(s) | {"_dir": s.parent.name} for s in ROOT.glob("skills/*/SKILL.md")]
    found.sort(key=lambda m: (order.index(m.get("category", "task")) if m.get("category") in order else 9, m["_dir"]))
    rows = [f"| `{m.get('name', m['_dir'])}` | {m.get('category', '')} | {m.get('description', '').split('. ')[0][:140]} |"
            for m in found]
    return "| Skill (`skills/<name>/SKILL.md`) | Kind | Use when |\n|---|---|---|\n" + "\n".join(rows)

def render(text):
    for tag, body in (("folders", folders()), ("skills", skills())):
        text = re.sub(rf"(<!-- gen:{tag} -->\n).*?(\n<!-- /gen:{tag} -->)",
                      lambda m: m.group(1) + body + m.group(2), text, flags=re.S)
    return text

def main():
    index = ROOT / "INDEX.md"
    old = index.read_text(encoding="utf-8")
    new = render(old)
    if "--check" in sys.argv:
        sys.exit(0 if old == new else (print("INDEX.md is stale: run python tools/gen-index.py") or 1))
    index.write_text(new, encoding="utf-8", newline="\n")

if __name__ == "__main__":
    main()
```

  Seed `INDEX.md`:

```markdown
# INDEX — where everything is

Read this after `AGENTS.md`. Open only the file a row points at.

## I want to…

| …do this | Go to |
|---|---|
| Add or change software in the menu | `catalog/<os>.json` (data only) |
| Change how something installs | `lib/windows/*.psm1`, `lib/linux/*.sh` |
| Change an LLM, its price or its profile | `catalog/llm-models.json`, then `agents/openhands/` |
| Orchestrate agents (batch, CAO, herdr) | `skills/unattended-orchestration/SKILL.md` (becomes `skills/agent-orchestration/` in Phase 6) |
| Act as the L1 main orchestrator | `skills/unattended-orchestration/references/main-orchestrator.md` |
| Run or fix the MCP stack | `skills/mcp-servers-setup/SKILL.md`, `infra/mcp-servers/README.md` |
| Remember or recall a decision | `skills/structured-memory/SKILL.md` |
| Know why something is the way it is | `docs/decisions/` |
| Run the tests | `tests/README.md` |
| Find every skill and MCP server, with triggers | `skills/repository-index/SKILL.md` |

## Folders

<!-- gen:folders -->
<!-- /gen:folders -->

## Skills

<!-- gen:skills -->
<!-- /gen:skills -->
```

- [ ] **Step 4:** Run `python3 tools/gen-index.py`, then the test. Expected: PASS.
- [ ] **Step 5: Commit** `feat(nav): INDEX.md generated from folder READMEs and skill frontmatter`.

### Task 3.3: One README per top-level folder

- [ ] **Step 1:** For each folder that `check-agent-docs.py` reports as `no README.md` or over
  budget, write it:
  - first line `# <folder> — <purpose in ≤ 12 words>`;
  - then "Entry points" (≤ 5 bullets with paths);
  - then "Not here" (where people wrongly look).

  Folders: `catalog`, `lib`, `web`, `skills`, `agents`, `infra`, `remote`, `templates`,
  `tools`, `tests`, `docs`, `third_party`.
- [ ] **Step 2:** Run `python3 tools/gen-index.py && python3 tools/check-agent-docs.py`. Expected:
  no `README` violations. **Commit** `docs(nav): a README with a one-line purpose for every folder`.

### Task 3.4: Rewrite the routing-index skill for the whole repository

**Files:** Modify `skills/repository-index/SKILL.md`; extend `tools/gen-index.py`.

- [ ] **Step 1:** Replace its §1 "Repository map" with one line: "The map is `INDEX.md`
  (generated). This skill routes *capabilities*." That gives one home per fact.
- [ ] **Step 2:** Keep §2 (MCP server directory) and §6 (decision tree). Drop every
  host-specific note (private hosts, ports that only exist on one machine), and point at
  `skills/mcp-servers-setup` for wiring.
- [ ] **Step 3:** Replace §3–§5 (skill tables written by hand) with
  `<!-- gen:skills -->…<!-- /gen:skills -->`, filled by `gen-index.py`. Make `render()` run over
  both `INDEX.md` and this file, and make `--check` cover both.
- [ ] **Step 4:** Add frontmatter `category:` to every `SKILL.md`: `always-on` for
  coding-principles, structured-memory and repository-index; `task` for html-working-documents
  and homelab-access; `orchestration` for the orchestration skills; `review` for pr-approval-agent,
  qa-swarm, review-triage, no-mistakes and babysit-prs; `install` for autoos-install and
  mcp-servers-setup.
- [ ] **Step 5:** Run the generator, then both checks. **Commit** `docs(nav): repository-index routes capabilities for the unified repo; skill tables generated`.

### Task 3.5: Tier-0 instruction files

- [ ] **Step 1:** Rewrite `AGENTS.md` (≤ 80 lines) as:
  1. the 3 hard rules;
  2. "Next: read `INDEX.md`";
  3. where you work, per ADR 0007: your worktree plus
     `<repo>-worktrees/.sessions/<you>/`, never elsewhere;
  4. test commands;
  5. "Every fact has one home".

  Content from agent-skills' `AGENTS.md` that doesn't fit moves into the skill it belongs to.
  `CLAUDE.md` and `GEMINI.md` each become a pointer plus their tool-specific delta (≤ 20 lines).
- [ ] **Step 2:** Merge agent-skills' `README.md` and `CONTRIBUTING.md` into AutoOS `README.md`
  (a short "Agents and skills" section pointing at `INDEX.md`) and `docs/contributing.md`. The
  contributing rule: "adding a skill = folder + frontmatter + `gen-index.py`".
- [ ] **Step 3:** `check-agent-docs.py` → no `AGENTS.md` violation; check-links clean. **Commit**
  `docs(nav): AGENTS.md is tier 0; CLAUDE/GEMINI are deltas`.

### Task 3.6: Fit every skill into the budget

- [ ] **Step 1:** For each `skills/<name>: body over 250 lines` violation (expected:
  unattended-orchestration, mcp-servers-setup, structured-memory, swarm-orchestration until
  Phase 6), move whole sections into `skills/<name>/references/<topic>.md`.
  - Each reference starts with a one-line summary.
  - The SKILL.md keeps the trigger, the workflow steps, and one line per reference: "Read
    `references/<topic>.md` when <situation>".
  - No text is rewritten in the move; git's rename detection shows it as a move.
- [ ] **Step 2:** Add the repository-wide case to `tests/run-tests.sh`:

```bash
if it "the repository itself passes check-agent-docs"; then
    if python3 tools/check-agent-docs.py; then pass; else fail "see above"; fi
fi
```

  Run it, plus the unattended-orchestration suites (Portability copies the folder and must still
  pass). All green. **Commit**
  `docs(skills): SKILL.md bodies within budget; detail moved to references/`.

### Task 3.7: A routing eval with a small local model

**Files:**
- Create: `tools/eval_routing.py`
- Create: `tests/fixtures/routing-questions.json` (20 entries `{"q": "...", "expect": "<path prefix>"}`,
  one per "I want to…" row plus 10 skill triggers)
- Test: `tests/run-tests.sh` (scorer only; the model run is opt-in)

**Interfaces:**
- Produces: `python tools/eval_routing.py [--base-url URL] [--model M] [--min 0.9]`. The system
  prompt is **only** `AGENTS.md` + `INDEX.md`. It prints `PASS|FAIL <q> -> <answer>`, exits 1
  below `--min`, and exposes `score(answer: str, expect: str) -> bool`.

- [ ] **Step 1: Failing scorer test**

```bash
if it "eval_routing.score accepts the expected path anywhere in the answer, case-sensitively"; then
    out="$(python3 -c 'import sys; sys.path.insert(0,"tools"); import eval_routing as e; print(e.score("Open `catalog/linux.json`.", "catalog/"), e.score("see Catalog", "catalog/"))')"
    assert_eq "$out" "True False"
fi
```

- [ ] **Step 2: Implement**

```python
"""Can a small model route with only AGENTS.md + INDEX.md in context? Opt-in: needs a model."""
import argparse, json, os, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]

def score(answer, expect):
    return expect in answer

def ask(base, model, system, question, key):
    body = json.dumps({"model": model, "temperature": 0, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": question + "\nAnswer with the path to open, nothing else."}]}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", body,
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["choices"][0]["message"]["content"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"))
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--min", type=float, default=0.9)
    a = ap.parse_args()
    system = (ROOT / "AGENTS.md").read_text(encoding="utf-8") + "\n\n" + (ROOT / "INDEX.md").read_text(encoding="utf-8")
    qs = json.loads((ROOT / "tests/fixtures/routing-questions.json").read_text(encoding="utf-8"))
    ok = 0
    for q in qs:
        ans = ask(a.base_url, a.model, system, q["q"], os.environ.get("EVAL_API_KEY", "none"))
        hit = score(ans, q["expect"]); ok += hit
        print(("PASS" if hit else "FAIL"), q["q"], "->", ans.strip()[:120])
    print(f"{ok}/{len(qs)}")
    sys.exit(0 if ok / len(qs) >= a.min else 1)

if __name__ == "__main__":
    main()
```

- [ ] **Step 3:** The scorer test passes. Then run it for real against the host's Ollama
  (`python tools/eval_routing.py`, model `qwen2.5-coder:7b`, 32k context). **Gate: ≥ 18/20.**
  Also run it against one free OpenRouter model
  (`--base-url https://openrouter.ai/api/v1 --model <free id>`, with `EVAL_API_KEY` exported
  from the user-scope secrets file). Record both scores in `docs/research/<date>-routing-eval.md`.
  If a question fails, fix the `INDEX.md` row or README first line, not the question.
- [ ] **Step 4: Commit** `test(nav): routing eval - a 7B local model finds the right file from AGENTS.md + INDEX.md`.

**Phase 3 exit criterion:**
- `check-agent-docs.py` and `gen-index.py --check` are green in CI;
- the routing eval scored ≥ 18/20 on `qwen2.5-coder:7b`;
- `AGENTS.md` + `INDEX.md` together are ≤ 3k tokens.

---

## Phase 4 — One `<repo>-worktrees/` folder per repository (◆ D6, ADR 0007)

The layout. It sits **beside** the repository, never inside it:

```
<parent>/                              e.g. Documents/Code
├── <repo>/                            the main checkout: a spawned agent never edits it
└── <repo>-worktrees/                  everything spawned agents create for this repo
    ├── <repo>-<session>/              one git worktree per L2/L3 session: the only place that agent edits
    └── .sessions/<session-key>/       scratch/, logs/: never committed, removed by -Cleanup after the merge
```

Why this shape (operator, 2026-09-18):
- **Outside the repo**, because MCP servers (Serena's language servers, Graphify, ripgrep) index
  a repository root. Worktrees *inside* it would be indexed as duplicate code, and nested git
  checkouts confuse them.
- **Grouped**, so the parent folder isn't filled with `sibling-analysis-repo-*`-style siblings.
- **Worktree folders keep the `<repo>-<session>` name**, because Serena names a project after its
  folder. Unique folder names keep its project rows apart.
- **Exception, CAO in WSL:** worktrees must sit on the native ext4 filesystem
  (`handoff.config.example.json:293`), so the same shape lives at `$HOME/<repo>-worktrees/`
  inside WSL. `AGENT_WORKTREES_PARENT` overrides the parent folder anywhere, and an explicit
  `worktreeParent` in a run config still wins.

Rules for a spawned agent:
- It edits only its worktree.
- Temporary files go to `<repo>-worktrees/.sessions/<key>/scratch/`.
- It never writes into the main checkout, the user's home, or another session's folders.

### Task 4.1: ADR 0007

- [ ] Write `AutoOS/docs/decisions/0007-worktrees-beside-the-repo.md`: the tree, the rules, the
  "why" list above, and the CAO/WSL exception. **Commit** `docs(adr): 0007 one <repo>-worktrees folder beside each repository`.

### Task 4.2: The batch runner defaults into `<repo>-worktrees/`

**Files:**
- Modify: `skills/unattended-orchestration/HandoffCore.psm1`:
  - `Read-HandoffConfig`: the derived default at `$cfg["worktreeParent"] = (Split-Path $cfg["repo"] -Parent)`;
  - `Get-HandoffSessionVars`;
  - the default `briefTemplate` in `New-HandoffConfigScaffold`.
- Modify: `skills/unattended-orchestration/run_handoff_sessions.ps1` (create `scratchDir` before
  launch; `-Cleanup` removes it after a merge)
- Test: `skills/unattended-orchestration/tests/HandoffCore.Tests.ps1`

**Interfaces:**
- Produces: `Get-HandoffWorktreesDir -Repo <path>` returns
  `<AGENT_WORKTREES_PARENT or parent of repo>/<repo leaf>-worktrees`.
- Produces: session var `{{scratchDir}}` = `<worktreeParent>/.sessions/<key>`.
- An explicit `worktreeParent` in a config still wins, so existing configs keep working.

- [ ] **Step 1: Failing tests** (inside the existing `Read-HandoffConfig / validation` try block,
  where `$goodPath` and `$tmp` exist; `Assert-Equal` takes `(expected, actual)`)

```powershell
    It "defaults worktreeParent to <parent>/<repo>-worktrees: beside the repo, grouped, never inside it" {
        $saved = $env:AGENT_WORKTREES_PARENT; $env:AGENT_WORKTREES_PARENT = $null
        try {
            $c = Read-HandoffConfig $goodPath
            Assert-Equal (Join-Path (Split-Path $tmp -Parent) ((Split-Path $tmp -Leaf) + "-worktrees")) $c.worktreeParent
            $v = Get-HandoffSessionVars $c "A" $false
            Assert-True ($v["worktree"] -like "*-worktrees*$([IO.Path]::DirectorySeparatorChar)*-alpha") "worktree must sit inside <repo>-worktrees, got $($v['worktree'])"
        } finally { $env:AGENT_WORKTREES_PARENT = $saved }
    }
    It "honours AGENT_WORKTREES_PARENT (CAO/WSL keeps worktrees on ext4 under HOME)" {
        $saved = $env:AGENT_WORKTREES_PARENT; $env:AGENT_WORKTREES_PARENT = Join-Path $tmp "home"
        try {
            $c = Read-HandoffConfig $goodPath
            Assert-Equal (Join-Path (Join-Path $tmp "home") ((Split-Path $tmp -Leaf) + "-worktrees")) $c.worktreeParent
        } finally { $env:AGENT_WORKTREES_PARENT = $saved }
    }
    It "gives every session a scratchDir under <repo>-worktrees/.sessions/<key>" {
        $saved = $env:AGENT_WORKTREES_PARENT; $env:AGENT_WORKTREES_PARENT = $null
        try {
            $c = Read-HandoffConfig $goodPath
            $v = Get-HandoffSessionVars $c "A" $false
            Assert-Equal (Join-Path (Join-Path $c.worktreeParent ".sessions") "A") $v["scratchDir"]
        } finally { $env:AGENT_WORKTREES_PARENT = $saved }
    }
```

- [ ] **Step 2:** Run `pwsh -NoProfile -File skills/unattended-orchestration/tests/HandoffCore.Tests.ps1`.
  Expected: 3 FAIL.
- [ ] **Step 3: Implement**

```powershell
function Get-HandoffWorktreesDir([string]$Repo) {
    <#  ADR 0007: every worktree of a repo in ONE folder beside it, <parent>/<repo>-worktrees.
        Beside, never inside: MCP servers (Serena, Graphify) index a repository root and
        must not see worktrees. Grouped, so the parent folder is not buried in siblings.
        AGENT_WORKTREES_PARENT moves the parent (CAO in WSL needs ext4 under $HOME).  #>
    $parent = if ($env:AGENT_WORKTREES_PARENT) { $env:AGENT_WORKTREES_PARENT } else { Split-Path $Repo -Parent }
    return (Join-Path $parent ((Split-Path $Repo -Leaf) + "-worktrees"))
}
```

  - In `Read-HandoffConfig`, replace `$cfg["worktreeParent"] = (Split-Path $cfg["repo"] -Parent)`
    with `$cfg["worktreeParent"] = Get-HandoffWorktreesDir $cfg["repo"]`.
  - In `Get-HandoffSessionVars`, after the `$vars = @{…}` block, add
    `$vars["scratchDir"] = Join-Path (Join-Path $Config["worktreeParent"] ".sessions") $key`.
  - In the scaffold's default `briefTemplate`, after the worktree sentence, add
    `Temporary files go to {{scratchDir}}, never into the worktree or anywhere else.`
  - In `run_handoff_sessions.ps1` `Run-Session`, before the first launch, add
    `New-Item -ItemType Directory -Force -Path $vars.scratchDir | Out-Null`.
  - In `-Cleanup`, after `Remove-Worktree`, remove `$vars.scratchDir`.
  - Export `Get-HandoffWorktreesDir`.
- [ ] **Step 4:** Run all 7 suites. Expected: all pass. Runner.Smoke's `-Validate` output now
  shows the `-worktrees` parent.
- [ ] **Step 5:** In `handoff.config.example.json`, replace the
  `"worktreeParent": "C:\\Users\\you\\Code"` example with a comment line: "override only; default
  `<parent>/<repo>-worktrees`". **Commit**
  `feat(runner): worktrees and scratch live in <repo>-worktrees/ beside the repo`.

### Task 4.3: CAO uses the same shape (ext4 under `$HOME` in WSL)

**Files:**
- Modify: whatever reads `worktreeRoot` in `skills/unattended-orchestration/cao/`
  (`git grep -n worktreeRoot skills/unattended-orchestration/cao`)
- Modify: `handoff.config.example.json:311`
- Test: `tests/cao/test_config.py`

- [ ] **Step 1: Failing pytest.** With no `worktreeRoot` in the config and
  `AGENT_WORKTREES_PARENT` unset, the loaded root is `Path.home() / f"{repo.name}-worktrees"`.
  With `AGENT_WORKTREES_PARENT=<tmp>`, it is `<tmp>/<repo>-worktrees`. An explicit
  `worktreeRoot` still wins.
- [ ] **Step 2:** Implement the default. Replace the example's `"$HOME/cao-worktrees"` with
  `"$HOME/<repo>-worktrees"`, keeping the native-filesystem note.
- [ ] **Step 3:** pytest green. **Commit** `feat(cao): worktree root defaults to $HOME/<repo>-worktrees`.

### Task 4.4: OpenHands agents get the rule and the map

**Files:**
- Modify: `agents/openhands/agent-profiles/{orchestrator,orchestrator-free,suborchestrator,suborchestrator-free,worker,worker-free,worker-free-high}.json`
  (`system_message_suffix`, which is `null` today)
- Test: `tests/run-tests.sh`

- [ ] **Step 1: Failing test**

```bash
if it "every OpenHands-kind agent profile carries the navigation and worktree rule"; then
    if python3 - <<'PY'
import glob, json, sys
bad = []
for p in sorted(glob.glob("agents/openhands/agent-profiles/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    if d.get("agent_kind") != "openhands":
        continue
    s = d.get("system_message_suffix") or ""
    if not all(k in s for k in ("AGENTS.md", "INDEX.md", "-worktrees", ".sessions")) or len(s) > 400:
        bad.append(p)
print("\n".join(bad)); sys.exit(1 if bad else 0)
PY
    then pass; else fail "profiles above lack the rule or exceed 400 chars"; fi
fi
```

- [ ] **Step 2:** Run it; expected FAIL (7 profiles listed).
- [ ] **Step 3:** Set in each of the 7 profiles (≤ 400 chars, identical text):

```text
Start by reading AGENTS.md, then INDEX.md, in the repository root; open only the files they point you to. Edit files only inside your working directory. Put temporary files in <repo>-worktrees/.sessions/<task>/scratch/ (the folder beside the repository), never in the repository itself.
```

  The ACP profiles (`claude-*`, `agy-*`) get the same rule from `AGENTS.md`, which those CLIs
  read natively.
- [ ] **Step 4:** Test passes. The installers copy agent profiles verbatim, so
  `~/.openhands/agent-profiles` gets the rule on the next configure run. **Commit**
  `feat(openhands): agents read AGENTS.md + INDEX.md first and keep scratch beside the repo`.
- [ ] **Step 5 (measurement; the result goes in ADR 0007):** start one OpenHands conversation
  with `worker-free` on a throwaway worktree in `AutoOS-worktrees/`. Record:
  - its working directory;
  - where a sub-agent spawned by `suborchestrator` works;
  - whether any file appeared outside the worktree and `.sessions/`.

  If sub-agents share the parent's workspace (expected), note that the L2 profile must hand each
  L3 its own worktree path in the task text.

### Task 4.5: See where every worktree is

**Files:** Create `tools/worktree-report.ps1` and `tools/worktree-report.sh` (read-only); test in
both suites.

- [ ] **Step 1: Failing test.** A temp parent containing:
  - repo `R`;
  - `R-worktrees/R-x`, a worktree whose branch is merged;
  - `R-worktrees/R-y`, unmerged;
  - `R-worktrees/.sessions/z`, with no matching worktree;
  - `R-old`, a worktree **beside** `R` but outside `R-worktrees`.

  The report prints `merged R-worktrees/R-x`, `active R-worktrees/R-y`,
  `orphan R-worktrees/.sessions/z` and `outside R-old`, and exits 0.
- [ ] **Step 2: Implement.** For each given repo, run `git worktree list --porcelain` and
  `git branch --merged <base>`, classify each worktree by whether its path is under
  `<repo>-worktrees/`, then list `.sessions/*` folders without a worktree. It deletes nothing.
- [ ] **Step 3: Commit** `feat(tools): worktree report - merged, active, orphaned, outside <repo>-worktrees`.

## Phase 5 — Rewire every coupling to the AutoOS checkout

Tasks 5.1–5.4 share `lib/linux/install.sh` and `lib/windows/AutoOS.Install.psm1`: **one lane,
sequential.** Tasks 5.5 and 5.6 are separate.

### Task 5.1: Skills come from AutoOS, and stale links are repointed

**Files:**
- Modify: `lib/linux/install.sh` (`install_agent_skills`, the skill-link loop at `:1264-1285`)
- Modify: `lib/windows/AutoOS.Install.psm1` (`Install-AutoOSAgentSkills`, loop at `:940-962`)
- Modify: `catalog/{windows,linux,macos}.json` (entry `agent-skills`: rename to "Agent skills
  (bundled)", drop the clone, keep the id so saved states replay)
- Test: `tests/run-tests.sh`, `tests/run-tests.ps1`

**Interfaces:**
- Produces (bash): `link_skill_dir <src> <dst>` prints one of `linked`, `relinked`, `skipped`
  or `kept-real-dir`.
- Produces (PowerShell): `Set-AutoOSSkillLink -Source <dir> -Target <path>` returns the same
  four strings.

- [ ] **Step 1: Failing bash test**

```bash
if it "link_skill_dir repoints a link that targets the old checkout, never replaces a real dir"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/new/s" "$tmp/old/s" "$tmp/real"
    ln -s "$tmp/old/s" "$tmp/l1"
    r1="$(link_skill_dir "$tmp/new/s" "$tmp/l1")"
    r2="$(link_skill_dir "$tmp/new/s" "$tmp/l1")"
    r3="$(link_skill_dir "$tmp/new/s" "$tmp/real")"
    r4="$(link_skill_dir "$tmp/new/s" "$tmp/l2")"
    tgt="$(readlink "$tmp/l1")"; rm -rf "$tmp"
    assert_eq "$r1 $r2 $r3 $r4 ${tgt##*/new/}" "relinked skipped kept-real-dir linked s"
fi
```

- [ ] **Step 2:** Run it; expected FAIL (`link_skill_dir: command not found`).
- [ ] **Step 3: Implement** in `install.sh`:

```bash
# link_skill_dir <src> <dst>
# One skill directory linked into an agent's skills folder. A link that points
# anywhere else (the old agent-skills checkout) is repointed; a real directory
# is somebody's own skill and is never replaced.
link_skill_dir() {
    local src="$1" dst="$2"
    if [[ -L "$dst" ]]; then
        if [[ "$(readlink "$dst")" == "$src" ]]; then echo skipped; return 0; fi
        ln -snf "$src" "$dst" && echo relinked; return 0
    fi
    if [[ -e "$dst" ]]; then echo kept-real-dir; return 0; fi
    ln -s "$src" "$dst" 2>/dev/null || cp -r "$src" "$dst"
    echo linked
}
```

  Then, in `install_agent_skills`:
  - replace `clone_or_update …agent-skills.git "$dest"` with `local dest="$AUTOOS_ROOT"`;
  - replace both `if [[ ! -e … ]]; then ln -snf … fi` blocks with
    `link_skill_dir "$s_dir" "$agy_skills/$s_name" >/dev/null` and
    `link_skill_dir "$s_dir" "$claude_skills/$s_name" >/dev/null`.

  Also update the existing case `agent-skills links skills to Antigravity and Claude Code`: it
  now creates its fixtures under `AUTOOS_ROOT="$tmp/autoos"`.
- [ ] **Step 4:** Run it; expected PASS. Then the full Linux suite.
- [ ] **Step 5: PowerShell twin.** Write the matching failing test using
  `New-Item -ItemType Junction` for the "old" link. Implement `Set-AutoOSSkillLink`: a
  `ReparsePoint` whose `.Target` differs is removed with `cmd /c rmdir` (never
  `Remove-Item -Recurse`, which follows junctions) and recreated. Wire it into
  `Install-AutoOSAgentSkills` with `$dest = $script:RepoRoot`. Run it and the full Windows suite.
- [ ] **Step 6:** Point `setup_openhands_config` / `Set-AutoOSOpenHandsConfig`'s
  `~/.openhands/skills` link at `$AUTOOS_ROOT/skills`, using the same helper.
- [ ] **Step 7: Commit** `feat(skills): install agent skills from the AutoOS checkout; repoint links left at agent-skills`.

### Task 5.2: One secrets resolver (◆ D4)

**Files:**
- Modify: `lib/windows/AutoOS.Install.psm1` (the two `$secretsPath = Join-Path $HOME 'Documents\Code\agent-skills\…'` lines)
- Modify: `lib/linux/install.sh` (`setup_opencode_config`, `setup_openhands_config` `secrets_file=`)
- Test: both suites

**Interfaces:**
- Produces (PowerShell): `Get-AutoOSSecretsPath` returns the first existing of
  `$env:AUTOOS_SECRETS`, `~\.config\autoos\api_keys.conf`, and the two legacy agent-skills
  paths; `$null` when none exists.
- Produces (bash): `autoos_secrets_path`, with the same order.

- [ ] **Step 1: Failing tests** (bash shown; PowerShell mirrors it with `$env:USERPROFILE`):

```bash
if it "autoos_secrets_path prefers the user-scope file over the legacy agent-skills one"; then
    tmp="$(mktemp -d)"; mkdir -p "$tmp/.config/autoos" "$tmp/Documents/Code/agent-skills/secrets"
    touch "$tmp/.config/autoos/api_keys.conf" "$tmp/Documents/Code/agent-skills/secrets/api_keys.conf"
    got="$(SYS_HOME="$tmp"; unset AUTOOS_SECRETS; autoos_secrets_path)"
    rm -rf "$tmp"
    assert_eq "$got" "$tmp/.config/autoos/api_keys.conf"
fi
```

- [ ] **Step 2:** Run it; expected FAIL.
- [ ] **Step 3: Implement**

```bash
autoos_secrets_path() {
    local c
    for c in "${AUTOOS_SECRETS:-}" "$SYS_HOME/.config/autoos/api_keys.conf" \
             "$SYS_HOME/Documents/Code/agent-skills/secrets/api_keys.conf" \
             "$SYS_HOME/Documents/code/agent-skills/secrets/api_keys.conf"; do
        [[ -n "$c" && -f "$c" ]] && { printf '%s\n' "$c"; return 0; }
    done
    return 0
}
```

  Use it in both setup functions (`local secrets_file; secrets_file="$(autoos_secrets_path)"`).
  Print `ui_warn "reading legacy secrets from $secrets_file; move it to ~/.config/autoos/api_keys.conf"`
  when the result contains `/agent-skills/`.
- [ ] **Step 4:** Run the tests and both full suites; expected PASS.
- [ ] **Step 5:** README: in the agent-canvas section, name `~/.config/autoos/api_keys.conf`
  as the key file.
- [ ] **Step 6: Commit** `feat(secrets): ~/.config/autoos/api_keys.conf, legacy agent-skills path as fallback`.

### Task 5.3: Omnigraph readiness and MCP paths

- [ ] Point `omnigraph_readiness` (`install.sh:1177-1200`) and
  `Write-AutoOSOmnigraphReadiness -AgentSkillsDir` (`AutoOS.Install.psm1:830`) at
  `$AUTOOS_ROOT/infra/mcp-servers`, and rename the parameter `-InfraDir`. Update the tests that
  mention `agent-skills` (`run-tests.ps1:967`, `run-tests.sh:886,1401`).
- [ ] Point the `mcp-graphify` catalog `homepage` fields (`windows.json:526`, `linux.json:698`,
  `macos.json:401`) at the AutoOS path.
- [ ] Commit `refactor: omnigraph readiness and MCP docs point at infra/ in AutoOS`.

### Task 5.4: Docs

- [ ] Update `README.md`, `docs/catalog.md` and `docs/plans/2026-09-14-…` wherever they say
  "agent-skills repository". Run `python3 tools/gen-index.py`, `check-agent-docs.py` and
  `python tests/check-links.py .`; expected clean. Commit `docs: agent skills live in AutoOS`.

### Task 5.5: Host migration runbook (operator executes; the agent writes it)

**Files:** Create `docs/agents-migration.md`, with one numbered, reversible step each:
1. `docker compose -f <agent-skills>/infra/mcp-servers/docker-compose.yml stop` (server, minio,
   viewer). Then **copy** (don't move) `data/minio` and `cluster/` to
   `~/.local/share/autoos/omnigraph/` and verify the byte counts.
2. Set `OMNIGRAPH_DATA_DIR` in `~/.config/autoos/site.env` and start the stack from
   `AutoOS/infra/mcp-servers`. Check that all 8 graphs answer (`omnigraph` health and one query
   per graph).
3. Re-point scheduled task `\Omnigraph Sync` at `AutoOS\infra\mcp-servers\omnigraph-setup\omnigraph-sync-hidden.vbs`
   (`schtasks /Change /TN "\Omnigraph Sync" /TR …`).
4. Re-run `setup.ps1 -Only agent-skills,openhands`. Task 5.1's helper repoints
   `~/.claude/skills/*`, `~/.gemini/config/skills/*` and `~/.openhands/skills`.
5. Edit `~/.gemini/config/mcp_config.json`, `~/.codewhale/mcp.json` (superpowers path →
   `AutoOS/third_party/superpowers-mcp/build/index.js`, after `npm ci && npm run build` there),
   `~/.gemini/antigravity-cli/settings.json`, `~/.gemini/projects.json`,
   `~/.codewhale/config.toml` and `~/.serena/serena_config.yml`.
6. Move `secrets/api_keys.conf` to `~/.config/autoos/api_keys.conf`.
7. Run `tools/worktree-report` on each repo. For each `outside` worktree (for example the
   `sibling-analysis-repo-*` siblings), decide one of:
   - merged → `git worktree remove`;
   - still needed, and no live session in it → `git -C <repo> worktree move <path> <repo>-worktrees/<name>`,
     then re-activate Serena on the new path.

   A worktree a live session is using is never moved.
8. Run Task 5.6's finder; expected: no remaining references.

Each step lists its undo. Nothing is deleted in this runbook without the operator's go.

### Task 5.6: A read-only reference finder

**Files:** Create `tools/find-agent-skills-refs.ps1` and `tools/find-agent-skills-refs.sh`;
test in both suites.

- [ ] **Step 1: Failing test.** A temp HOME with `.claude/skills/x` linked to
  `<tmp>/Documents/Code/agent-skills/skills/x`, plus a `.gemini/config/mcp_config.json`
  containing the string `agent-skills`. The finder must print both paths and exit 1. With
  neither present, it prints nothing and exits 0.
- [ ] **Step 2: Implement.**
  - Scan the link targets under `~/.claude/skills`, `~/.gemini/config/skills` and
    `~/.openhands/skills`.
  - Grep (fixed string `agent-skills`) the files `~/.gemini/config/mcp_config.json`,
    `~/.gemini/antigravity-cli/settings.json`, `~/.gemini/projects.json`,
    `~/.codewhale/{mcp.json,config.toml}` and `~/.serena/serena_config.yml`.
  - On Windows also run `schtasks /Query /TN "\Omnigraph Sync" /V /FO LIST`.
  - Print `path: reason` for each hit.
- [ ] **Step 3:** Tests pass. **Commit** `feat(tools): find what still points at the agent-skills checkout`.

---

## Phases 6–8 — the functional goals (each gets its own detailed plan when it starts)

These are the operator's original tasks 1, 3 and 4. They build on one repository, one secrets
path, the navigation layer and the `<repo>-worktrees/` layout. Before a phase starts, write its bite-sized plan
with superpowers:writing-plans in `AutoOS/docs/plans/`. Every phase ends with
`gen-index.py --check`, `check-agent-docs.py` and the routing eval still green.

### Phase 6 — Proper skills: one orchestration skill (original task 1)

| # | Task | Acceptance |
|---|---|---|
| 6.1 | `skills/agent-orchestration/SKILL.md` replaces the SKILL.md of unattended-, swarm- and herdr-orchestration (◆ D7).<br>• Vocabulary L1/L2/L3, plus a glossary for Orchestrator/Session/Subagent, supervisor/worker and architect/engineer/reviewer.<br>• Batch lane, CAO lane and herdr each become one section plus a reference file.<br>• The runner code (`HandoffCore.psm1`, `run_handoff_sessions.ps1`, `cao/`) moves with its tests into `skills/agent-orchestration/`.<br>• `references/main-orchestrator.md` (the L1 standing orders, 2026-09-18) moves with it and stays the L1 entry point; the description keeps the L1 trigger, and `~/.claude/skills/agent-orchestration` replaces the `unattended-orchestration` junction.<br>• No numbers in prose: every value lives in `handoff.config.example.json` or `agent_orchestration.config.yaml` | Old skill folders are removed. Portability, Runner.Smoke and pytest are green from the new path. `INDEX.md` routes "orchestrate agents" to it |
| 6.2 | Add `orchestration.wave_caps` and `orchestration.forbidden_concurrent_git` to `agent_orchestration.config.yaml` (values from the 2026-07-30 plan `:964-975`), or delete the pointers | A test that **every** config key a SKILL.md names resolves to an existing key |
| 6.3 | Retire the scaffold (◆ D8) | pytest count drops only by the retired files' tests |
| 6.4 | One setup entry point `skills/agent-orchestration/setup.{ps1,sh}` wrapping `-Init`, `python -m cao check` and `setup_cao.py --check`. It becomes an AutoOS catalog component `agent-orchestration` with `requires: [agent-skills, python3]` | `setup.ps1 -Only agent-orchestration -DryRun` shows the three checks |
| 6.5 | Skill hygiene for the rest: each of the 5 adapted review skills and `homelab-access` gets a `category`, a "Use when…" description and a `references/` split where needed | `check-agent-docs.py` green |
| 6.6 | Write `infra/mcp-servers/cao-setup/README.md` (0 bytes today) | check-links clean |

### Phase 7 — One model catalogue; every provider reachable at L3 (original task 3)

| # | Task | Acceptance |
|---|---|---|
| 7.1 | `catalog/llm-models.json` gains `family`, `pool`, `tier` (`orchestrator`/`leaf`) and `subscription`; schema and drift test extended | Both suites green |
| 7.2 | `cao/catalog.py::load_model_catalog()` + `build_routing()` generate `cao.routing.<class>` ladders from it; hand-written ladders stay as an override | pytest: a ladder built from a fixture catalog |
| 7.3 | **Measure before adding** each pool, recording the flags in the adapter comments: `opencode run` for OpenRouter, Muse and Ollama (OpenCode is already configured by AutoOS for all three); DeepSeek via `codewhale` or `opencode`; the `muse → mcode` mapping from the canvas branch (verify or replace); optionally `codex exec` as an OpenAI reviewer pool (ADR 0005). One quota-wall fixture each | A pool is added to `PROVIDER_BY_POOL` only with a measured fixture |
| 7.4 | `FAMILY_ORDER = (anthropic, google, meta, deepseek, openai, oss)`; `openrouter` removed from it (a **pool**, not a family: its models carry their real family) | Pairing test: reviewer family ≠ implementer family |
| 7.5 | OpenHands as L3: measure whether an OpenHands **headless CLI** exists (the SDK/agent-server; not `agent-canvas`, which is a web-UI launcher). If it does, an `openhands` adapter using the vendored `worker*` profiles, one worktree per task (ADR 0007); if not, OpenHands stays the human-watched canvas | A measured `--help` in the adapter comment, or a recorded "no" |
| 7.6 | Cost ledger `<state>/cost.ndjson` priced from 7.1; `-Status` prints per-level totals | Ledger test with a fixture turn |
| 7.7 | Close observations 24, 25 and 27: re-read `dependsOn` per poll; `stop-lane-<name>`; check the DONE note's artefact list before the guards | HandoffCore tests, pass and fire for each |
| 7.8 | Phase identity for L2: a `phases` block in `handoff.config.json` and a phase id in every ledger event | Ledger test |
| 7.9 | Off-peak routing in code: the CAO ladders and the batch runner consult `provider_windows.preferred()`. A session may declare `"deferrable": true`, and the runner then starts it in its pool's cheap/quiet window (`notBefore`). Blocking sessions are never deferred | pytest with fixed UTC times; a HandoffCore test that a deferrable session waits and a blocking one does not |

### Phase 8 — Profile management tab, GUI and TUI (original task 4)

| # | Task | Acceptance |
|---|---|---|
| 8.1 | User profile store `~/.autoos/profiles/<id>.json`, overlaid on the catalog's built-ins at read time in `catalog.sh` and `AutoOS.Catalog.psm1`; built-ins read-only, fork on edit (◆ D9) | Unit tests on both platforms |
| 8.2 | `/api/profiles` CRUD + `export` + `import` in **both** `lib/linux/serve.py` and `AutoOS.Serve.psm1`, copying `/api/config`'s backup-and-merge discipline; exports never carry `answers` | Route-table parity test between the two servers |
| 8.3 | `web/index.html`: `"profiles"` in `TABS` (today `overview, configure, run, system`) with add, duplicate, rename, delete, Import (file input), Export (Blob download), and a catalog browser reusing the component cards and dependency badges to add or remove components | `tests/test-web-progress.js`-style DOM test |
| 8.4 | The same menu in both TUIs (`AutoOS.Ui.psm1`, `lib/linux/ui.sh`) | Scripted-input tests |
| 8.5 | Headless flags `--profile-new/--profile-import/--profile-export` (`-ProfileNew` …) | Dry-run tests |
| 8.6 | Later: a second sub-tab for LLM/agent profiles (`agents/openhands/`) | — |

---

## Phase 9 — Retire agent-skills

- [ ] **9.1** Task 5.6's finder reports nothing on every machine that used agent-skills.
- [ ] **9.2** `git log <phase-1-exit-sha>..main` in agent-skills is empty, or every later
  commit's content is migrated (scrubbed) through `tools/migrate-agent-skills.py` for those
  paths. Then delete that script.
- [ ] **9.3** Sibling repositories' `CLAUDE.md`/`AGENTS.md` links to `../agent-skills/...` are
  updated. Those are their own repos: one small PR each, the operator's call.
- [ ] **9.4** The agent-skills README becomes a pointer to AutoOS. The 83 bot PRs are closed.
  The GitHub repo is **archived**, not deleted (the operator does this; it is outward-facing).
- [ ] **9.5** Remove the local clone after 30 days, and only after a final check that
  `infra/mcp-servers/data/` was copied (Task 5.5 step 1).

---

## Sequencing and swarm safety

```
Phase 0 (done; 0.5, 0.6 open) ─► Phase 1 (1.1‖1.2‖1.3‖1.4‖1.6, then 1.5) ─► Phase 2 (one lane)
                                                                              │
                        Phase 3 (navigation) ‖ Phase 4 (worktrees folder; 4.2/4.3 share cao/runner → one lane)
                                                                              │
                        Phase 5: 5.1→5.2→5.3→5.4 (one lane: shared installers) ‖ 5.5 ‖ 5.6
                                                                              │
                        Phase 6 ──(shares the orchestration code)──► Phase 7      Phase 8 (independent)
                                                                              │
                                                                          Phase 9
```

- Worktrees live in `<repo>-worktrees/` beside the repo from the first lane on. Until Task 4.2
  lands, set `worktreeParent` to that folder explicitly in the run's config.
- Lanes that write the same files are one lane. In the runner's terms:
  - `resources: ["installers:write"]` for Phase 5;
  - `["orchestration:write"]` for 4.2, 4.3, 6 and 7;
  - `["nav:write"]` for Phase 3, because `INDEX.md` is regenerated by every phase that adds a
    folder or skill.
- Every lane ends with both AutoOS suites green: Windows, and Linux in WSL. From Phase 3 on,
  it also ends with `gen-index.py --check` and `check-agent-docs.py` green.
- L1 (the operator's session) owns decisions ◆ D1–D10. L2 sessions own a phase. L3 executors
  get one task each, with its **Files** and **Interfaces** blocks as the whole brief, plus
  `AGENTS.md` + `INDEX.md`.

## Where things stand (2026-09-18)

- **Done:** Phase 0 tasks 0.1–0.4. Both `main`s are clean and hold every former worktree
  branch. Earlier in the day on AutoOS:
  - the vendored OpenHands profiles;
  - the Windows OpenHands setup repaired;
  - the Ollama address resolved per host;
  - the uv-cache patch and the stale-profile prune removed;
  - the README agent-canvas section;
  - ADR 0005 accepted.
- **Done in agent-skills:** the L1 standing orders,
  `skills/unattended-orchestration/references/main-orchestrator.md` (`19ce05d`). The skill is now
  linked into `~/.claude/skills`, so sessions can load it.
- **Done (2026-09-18, operator request):**
  - Serena memory and onboarding tools excluded globally (backup `serena_config.yml.bak-20260918-memories`; `replace_in_files` also excluded; a live listing shows 15 tools, none of them memory or plain-text file tools);
  - `~/.claude/CLAUDE.md` gained a "one tool per job" table and no longer orders onboarding (backup `CLAUDE.md.bak-20260918`);
  - the `usb-improvements` worktree was removed after archiving its Serena memories and logs.
- **Done (2026-09-18):** off-peak rule for L1/L2 (`references/main-orchestrator.md` §4a) with `provider-windows.json` (DeepSeek window verified against its pricing page) and `provider_windows.py` (6 tests); tool audit saved as `2026-09-18-tool-audit.md`.
- **Approved (2026-09-18):** Task 0.9a–e (tool hygiene: token out of `~/.claude.json`, one omnigraph entry, pins, removals, updates). Task 0.10 (Omnigraph v0.11, uv 0.12, Ollama 0.34, CAO 2.5.1) waits for a go each.
- **Next:** 0.9a first, then the installer lane 0.5 → 0.6 → 0.8 → 0.9c, with 0.9b/d/e and 0.7 alongside, then Phase 1.

## Handoff — paste this to the L1 orchestrator

```markdown
## Handoff: L1 main orchestrator, merge agent-skills into AutoOS (v2, 2026-09-18)

You are the **L1 main orchestrator**. Load the `unattended-orchestration` skill and read
`references/main-orchestrator.md` first. Those are your standing orders:
- navigate with Serena (code symbols), Graphify (dependencies) and Omnigraph (the **only**
  memory; Serena's memory tools are off);
- spawn interactive Opus L2s per chunk, which drive mechanical L3s (Haiku, Sonnet, Gemini Flash,
  DeepSeek, Muse Spark);
- a different model family reviews and tests than the one that wrote;
- prefer pools in their off-peak window (`provider_windows.py`);
- filter logs with code before reading them;
- check CPU, RAM, disk and GPU before heavy steps;
- research-grade evidence.

**Plan:** `C:\Users\<you>\Documents\Code\agent-skills\docs\superpowers\plans\2026-09-18-merge-agent-skills-into-autoos-plan.md`.
Read Goal, Global Constraints, Decisions and "Where things stand". Open a task's detail only when
you assign it. Tool evidence: `2026-09-18-tool-audit.md`, in the same folder.

**State:** both `main`s are clean, with no worktrees. New worktrees go to
`Code\<repo>-worktrees\<repo>-<session>\`; set `worktreeParent` to that folder explicitly until
Task 4.2 lands.

**Order of work:**
1. Task 0.9a (Omnigraph token out of `~/.claude.json`): first, alone.
2. The installer lane, sequential because it's the same AutoOS `lib/` files:
   0.5 → 0.6 → 0.8 → 0.9c.
3. Alongside it: 0.9b, 0.9d, 0.9e (host tools; one heavy step at a time) and 0.7 (move Serena
   memories into Omnigraph).
4. Then Phase 1 (agent-skills: 1.1, 1.2, 1.3, 1.4 and 1.6 in parallel; 1.5 last).

**Approved:** the Task 0.9a–e host changes as written, with backups first and verification after.

**Stop and ask the operator before:**
- each upgrade in Task 0.10;
- any ◆ decision (the ADR 0006 layout gates Task 2.2);
- deleting the Serena memory files (0.7 step 4);
- pushing, PRs, or archiving anything;
- moving worktrees or data outside Task 0.9;
- running an installer for real;
- anything a refusal blocks.

**Done means:**
- every finished task has its commits on `main`;
- its suites are green: Windows + WSL for AutoOS; pytest + the pwsh suites for agent-skills;
- before → after is recorded in "Where things stand";
- a DONE note at `agent-skills/docs/superpowers/plans/<date>-merge-L1-DONE.md`: commits, what
  remains, decisions and why, refusals verbatim, and the pool/window choices you made.
```
