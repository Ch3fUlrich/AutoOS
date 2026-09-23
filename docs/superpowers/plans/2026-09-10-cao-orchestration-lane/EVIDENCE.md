# CAO Orchestration Lane — Measured Evidence

**Date:** 2026-09-10
**Host:** Windows 11 + WSL2 Ubuntu, CAO 2.5.0 on `:9889`, tmux backend
**Spec:** [../../specs/2026-09-10-cao-orchestration-design.md](../../specs/2026-09-10-cao-orchestration-design.md)

Everything here was run, not reasoned about. Where something failed, the failure
is recorded rather than retried until it looked green.

---

## 1. Summary

| Claim | Verdict |
|---|---|
| 367 hermetic unit tests pass | **Yes** |
| Depth is configurable, generating correct profiles at 2/3/4/5 levels | **Yes** |
| Three model families reachable | **Yes** — Claude ok, Gemini ok, DeepSeek ok |
| A supervisor spawns a worker (L2 -> L3) | **Yes** |
| A worker does real work and returns a RESULT | **Yes** — file written, verified byte-exact |
| The hierarchy is cross-family | **Yes** — Claude Sonnet 5 supervising DeepSeek V4.1 Flash |
| Agents refuse to fake success when blocked | **Yes**, twice, unprompted |
| ext4 worktrees remove the filesystem tax | **Yes** — 2.929 s -> 0.224 s, executed on the real repo |

**The round trip works.** Getting there took four more defects (D17-D20), every
one of the same shape: something reports success while granting or doing nothing.

---

## 2. Filesystem placement — the 20× result

`git status --porcelain`, same repository, same WSL Ubuntu:

| Layout | Time | Relative |
|---|---|---|
| Repo on `/mnt/c` (9p) | **3.021 s** | 1× |
| ext4 worktree, `.git` still on `/mnt/c` | **0.146 s** | **20.7× faster** |
| Fully ext4 | **0.017 s** | 178× faster |

Reproduced independently when the E2E worktree was created: **0.154 s**.

An agent runs `git status`, file reads and test discovery constantly. This is the
single highest-leverage configuration choice on a Windows host, and it costs one
config field (`cao.worktreeRoot`).

---

## 3. Provider probes

`cao/probe.py`, cheapest model per pool, login shell:

| Pool | Verdict | Evidence |
|---|---|---|
| `anthropic` | **not_authenticated** | `Not logged in · Please run /login` |
| `antigravity` | ok | `PROBE_OK` |
| `deepseek` | ok | `PROBE_OK` |

DeepSeek was additionally verified against its own API: `deepseek-flash` returned
`DEEPSEEK_OK`, 63 tokens total, 18 of them reasoning tokens.

---

## 4. Defects found by measurement

Numbered continuing from the spec's audit (§9 lists 1–7).

### D8 — `/health` reports a provider "ok" that cannot do any work

`GET /health` returned `"claude":"ok"` for a Claude Code install that was **not
logged in**. Health checks binary presence, not the account. Every Claude worker
CAO spawned would have died on its first turn, and the hierarchy would have
silently degraded to Gemini-only with no error anywhere.

**Fixed:** `cao/probe.py` classifies `ok / not_authenticated / quota /
not_installed / failed`. Auth is checked *before* quota deliberately — recording
an unauthenticated pool as "cooling" would let it reopen on a timer and fail
again forever, when what is needed is a human.

**Still open for the operator:** run `claude auth` (or `claude` then `/login`)
inside WSL. This is an interactive OAuth flow and is not something an agent
should perform.

### D9 — `provider: antigravity` is not a valid ProviderType

The hand-written `agy_*.yaml` profiles from commit `69b229a` declare
`provider: antigravity`. The enum (`models/provider.py`) has `antigravity_cli`.
CAO does not reject the unknown value — it **silently falls back to `kiro_cli`**,
which is not installed, producing `400 Bad Request: Kiro engine 'v2' cannot start`.

**Fixed:** `PROVIDER_BY_POOL` uses enum values, and a test asserts every mapping
is a real member.

### D10 — `~/.cao/profiles/` is read by nothing

`setup-cao.sh` copied profiles to `~/.cao/profiles/`. CAO's profile store
resolves `<name>.md` under `LOCAL_AGENT_STORE_DIR`, and on this host that is
`/mnt/c/Users/<you>/.aws/cli-agent-orchestrator/agent-store/`. `cao profile list`
never showed anything placed in `~/.cao/profiles/`.

Copying into the store is also not enough — `cao install <file>.md` is the
registration step. Format is YAML frontmatter + the prompt as the markdown body.

**Fixed:** `cao/profiles.py` emits `.md`, and the documented flow is
generate → `cao install`.

### D11 — `assign` / `handoff` are not `allowedTools` entries

`cao profile validate` warns that `assign` "is not in CAO's recognized
vocabulary. It may be silently ignored". MCP access is granted **per server**, as
`@cao-mcp-server`. The real vocabulary, read off a live launch's `Blocked:` line:
`glob, google_web_search, list_directory, read_file, replace, run_shell_command,
search_file_content, web_fetch, write_file`.

Both the shipped profiles and my first generator listed bare MCP tool names, so
neither granted nor denied what it thought it did.

**Fixed:** `CAO_TOOL_VOCABULARY` plus a test that every generated entry is either
`@server` or a member.

### D12 — tool restrictions are advisory on Gemini

`ANTIGRAVITY_CLI` is in CAO's `SOFT_ENFORCEMENT_PROVIDERS`
(`terminal_service.py:211`), documented as "prompt-level text only (no native
blocking mechanism) — a restricted policy on these is advisory, not enforced".

This invalidated the spec's original depth mechanism, which relied on withholding
`assign` from a Gemini leaf's `allowedTools`. **A Gemini leaf told not to spawn
can still spawn.** `opencode_cli`, `claude_code`, `grok_cli`, `kiro_cli` and
`copilot_cli` enforce for real.

**Consequence — see §6.**

### D13 — the WSL `agy` is a shim to the Windows binary

`/home/<you>/.local/bin/agy` is four lines:

```python
#!/usr/bin/env python3
import sys, os
windows_agy = "/mnt/c/Users/<you>/AppData/Local/agy/bin/agy.exe"
os.execv(windows_agy, [windows_agy] + sys.argv[1:])
```

There is **no native Linux `agy`**. One-shot `agy -p` works (it reaches the
signed-in Windows binary), but CAO's interactive TUI session lands on
`Welcome to the Antigravity CLI. You are currently not signed in.` — the
Windows/Linux credential boundary predicted in spec §5.1, now measured.

**Consequence:** CAO cannot reliably drive Gemini on this host. The providers
with real Linux binaries are `claude` (needs login) and `opencode` (works).

### D14 — `cao launch` client timeout is not a failure

`cao launch` uses `MCP_REQUEST_TIMEOUT = 30` (a hard literal, not env-tunable).
Session creation on a TUI provider exceeds it. Twice the client reported
`Read timed out (read timeout=30)` while the server had **already created the
session** — confirmed by `cao session list`.

**Operational rule:** after a launch timeout, check `cao session list` before
retrying. Retrying blind creates duplicate sessions.

### D15 — `opencode` installs off-PATH and edits no rc file

The installer put the binary at `~/.opencode/bin/opencode`, which is not on
`PATH`, and wrote nothing to `.bashrc` or `.profile`. This is the same
`%PATH%` failure class the whole CAO integration began with.

**Fixed by symlink** into `~/.local/bin` (already on PATH), which is durable and
does not depend on rc-file sourcing.


### D16 — a broken Windows `cao` sits on PATH

A pip install of CAO exists on the Windows side and is on PATH. It cannot run:
CAO imports `fcntl`/`termios` at module scope and drives tmux, so every
invocation exits with an ImportError. Any Windows-side script or agent that calls
`cao ...` fails.

Upstream's error text is unusually good — it names WSL2 as the fix — so this is a
trap rather than a mystery. But the lane must never invoke `cao` from Windows:
every call goes through `wsl -d <distro> bash -lc 'cao …'`.

**Fixed:** `setup_cao.cao_runs_here()` refuses to treat a Windows `cao` as a
usable install, and `cao_invocation()` returns the right argv prefix for the host
it is running on.

---

## 5. The 3-level hierarchy — what was observed

Session `cao-e2e2`, both agents on `opencode_cli` / `deepseek/deepseek-v4-flash`,
worktree on ext4.

**Observed:**

```
0: cao_L2_supervisor-01f4* (1 panes)
1: cao_L3_worker-b4c5      (1 panes)
```

- L2 called real coordination tools: `cao-mcp-server_find_profiles`,
  `cao-mcp-server_list_siblings`.
- L3 was spawned, ran ~10 minutes, and built a todo list reading
  *"Write /tmp/cao-e2e-proof.txt … Reply with RESULT envelope"* — it adopted the
  envelope protocol from the generated system prompt with no other instruction.
- Cost: $0.02 supervisor, $0.01 worker; 18.0K and 12.4K tokens.

**Failed:** `handoff` returned MCP `-32001 Request timed out` on three attempts
(default, 600 s, 3600 s). `cao-mcp-server` then degraded — `find_profiles` and
`list_siblings`, which had been answering, began timing out, then `handoff`
returned `Not connected`, and finally the MCP tools vanished from the session
entirely (`Available tools: invalid, skill, todowrite`).

**The supervisor's own words**, verbatim:

> `RESULT e2e-proof status=needs_revision`
> The mandated delegation path (handoff → cao_L3_worker) is not executable in
> this environment. … I could not obtain any worker evidence, so I cannot confirm
> whether /tmp/cao-e2e-proof.txt was actually written … Layer 1 orchestrator
> decision required.

This is the designed behaviour under failure: it did not invent a result, it
reported structured evidence and escalated. The protocol held; the transport did
not.

**Not demonstrated:** a completed worker→supervisor RESULT round trip. The
previous walkthrough claimed the hierarchy worked on the strength of a supervisor
*booting*; this run got materially further — a real worker really ran — and still
did not close the loop. That remains unproven.

---

## 6. Correction the evidence forces

The spec's depth mechanism (§4) said: withhold `assign`/`handoff` from the leaf's
`allowedTools`. D11 and D12 make that unworkable as stated:

- Bare tool names are not vocabulary entries (D11).
- On Gemini, restrictions are advisory anyway (D12).

Withholding the **MCP server** instead is provider-independent — but CAO grants
MCP access per server, not per tool, so a leaf without `@cao-mcp-server` also
loses `send_message`. That forces `handoff`, which is precisely what timed out.

The honest position, and what the skill should say:

1. **A leaf must run on a hard-enforcement provider** — `opencode_cli`,
   `claude_code`, `grok_cli`, `kiro_cli`, `copilot_cli`. Config validation should
   reject a leaf assigned to a soft-enforcement pool, because on those the depth
   limit is a request, not a rule.
2. **Depth on a soft-enforcement provider cannot be enforced in-process.** It
   needs external policing: poll `/sessions` and terminate any terminal deeper
   than `maxDepth`.
3. **Prefer `assign` + `send_message` over `handoff`** for anything slow.
   Blocking `handoff` across a stdio MCP server did not survive a 10-minute
   worker here.

---

## 7. Reproducing

```bash
# hermetic suite (no providers needed)
python -m pytest skills/unattended-orchestration/tests/cao/ -q

# live provider smoke tests
CAO_LIVE=1 python -m pytest skills/unattended-orchestration/tests/cao/test_live_providers.py -v

# provider usability, not just presence
python -c "import sys; sys.path.insert(0,'skills/unattended-orchestration');
import subprocess; from cao.probe import probe_argv, classify
for p in ('anthropic','antigravity','deepseek'):
    r = subprocess.run(probe_argv(p, repo='/mnt/c/Users/<you>/Documents/Code/agent-skills', wsl_distro='Ubuntu'), capture_output=True, text=True)
    print(p, classify(r.stdout, r.stderr, r.returncode))"

# setup check
python infra/mcp-servers/cao-setup/setup_cao.py --check \
  --config skills/unattended-orchestration/handoff.config.example.json
```

---

## 8. The working round trip (2026-09-10, session `cao-e2e9`)

Configuration: L2 `claude_code` / `sonnet` at high effort, L3 `opencode_cli` /
`deepseek-direct/deepseek-flash`, worktree on ext4, `CAO_HOME_DIR=$HOME/.cao`.

**Observed, in order:**

1. Supervisor: `Task assigned to worker 30dba4e4. Waiting for its send_message callback before verifying with Read.`
2. Worker wrote the file and reported:
   `RESULT e2e-proof status=success` — *"Content verified by xxd: WORKER_DID_THIS — 15 bytes, no trailing newline. evidence: xxd output 574f...54484953"*
3. CAO delivered the callback to the supervisor's inbox.
4. Supervisor: `RESULT e2e-proof status=success`, then flagged its own limitation:
   *"no Read tool is available in this supervisor session … so I could not independently re-verify the file myself as instructed — accepted the worker's xxd byte-dump evidence in its stead."*

File on disk: `cao-e2e-proof.txt`, 15 bytes, content `WORKER_DID_THIS`. Elapsed ~90 s.

Two things worth keeping from that transcript. The worker volunteered a hex dump
rather than asserting completion; and the supervisor reported a gap in its own
tooling instead of quietly claiming a verification it had not performed. Neither
was requested. That is the behaviour the envelope protocol is for — and step 4 is
also what exposed D20.

### D17 — `cao launch` ignores a profile's `provider:`

`cao profile show` reported `provider: claude_code`, and the session API still
defaulted to `kiro_cli` (not installed) with `400 Bad Request`. The provider must
be passed explicitly: `cao launch --provider claude_code`.

### D18 — two CAO homes, and the profiles were in the wrong one

`CAO_HOME_DIR` was unset, so the CLI resolved to
`/mnt/c/Users/<you>/.aws/cli-agent-orchestrator/` while the running server used
`~/.cao`. `cao install` wrote to the first; the server read the second. The
profiles therefore did not exist as far as the server was concerned, so CAO took
its "no CAO profile found" path and passed `--agent cao_L2_supervisor` to Claude
Code's own agent store, which answered:

```
--agent 'cao_L2_supervisor' not found. Available agents: claude, claude-code-guide, ...
```

Two consequences beyond the failure itself. The walkthrough's claimed
`CAO_HOME_DIR="$HOME/.cao"` **ext4 FIFO isolation was not in effect** — the
default home is on the 9p filesystem. And every `cao` invocation must export
`CAO_HOME_DIR` or silently address a different installation.

### D19 — `allowedTools` names are provider-specific

Handing an `opencode_cli` worker the antigravity vocabulary
(`write_file`, `run_shell_command`) granted it **no filesystem tools at all**. It
reported that its only callable tools were `cao-mcp-server`, `skill` and
`todowrite`, probed thirteen alternatives, and refused to claim completion.

### D20 — naming tools grants nothing, not a subset

The same failure on `claude_code`: a supervisor given `("Read", "Glob", "Grep")`
reported *"no Read tool is available in this supervisor session"*.

Two providers, two vocabularies, both silently granting nothing. A restriction
that grants nothing is worse than no restriction — the agent can neither work nor
explain why. Every level now gets `*`, and `spawner_restriction_warnings()` states
plainly that "delegate, never implement" is a prompt rule rather than an enforced
one. A control you believe you have is worse than one you know you lack.

### Also fixed along the way

| Symptom | Cause | Fix |
|---|---|---|
| `Claude Code initialization timed out after 60s`, session deleted | CAO's default init budget; Claude Code loads MCP servers at startup | `cao config set provider_init_timeout 300` **in the server's home** |
| Claude never reaches idle in a fresh worktree | an unapproved folder prompts for trust and MCP approval | `trust_worktree.py <worktree> --mcpjson omnigraph` before launch |
| worker stuck on `Permission required — Access external directory /tmp` | OpenCode gates paths outside the workspace | write inside the worktree; `permission: {edit, bash: allow}` in `opencode.json` as belt |
| model shown as "DeepSeek V4 Pro" when V4.1 Flash was requested | OpenCode's registry maps its own ids | custom `deepseek-direct` provider hitting `api.deepseek.com/v1` and pinning `deepseek-flash` |

---

## 9. Corrections to sections 4 and 8 (2026-09-10, later)

Two of the defects above were diagnosed wrongly. The symptoms were real; the
explanations were not, and the fixes they justified were worse than the problem.

### D19/D20 were misdiagnosed — CAO has a universal tool vocabulary

I concluded that "naming tools grants nothing, not a subset" and gave every level
`allowedTools: ["*"]`, surrendering a real control. Reading
`utils/tool_mapping.py` — which the agents' own output pointed at — shows what is
actually happening:

> CAO defines a universal tool vocabulary (`execute_bash`, `fs_read`, `fs_write`,
> `fs_list`, `fs_*`, `web_fetch`, `@builtin`, `@cao-mcp-server`) that is
> translated to each provider's native tool names.

Provider-*native* names (`write_file`, `Read`) are not vocabulary entries, so they
map to nothing, `get_disallowed_tools` computes "block every native tool", and the
agent is left with MCP + `skill` + `todowrite`. That is indistinguishable from
"restrictions do not work" unless you read the mapping. `"*"` is a genuine escape
hatch (`if "*" in allowed: return []`), so the workaround happened to work — for
the wrong reason, and at the cost of the control.

**Corrected.** Profiles now speak CAO's vocabulary:

| Level | allowedTools | CAO denies |
|---|---|---|
| spawner | `@cao-mcp-server, fs_read, fs_list` | Agent, Bash, BashOutput, Edit, KillShell, Monitor, NotebookEdit, Task, WebFetch, WebSearch, Write |
| leaf | `@cao-mcp-server, execute_bash, fs_*, web_fetch` | *(nothing)* |

`Read`, `Glob` and `Grep` are **not** in the supervisor's deny list, so
"delegate, never implement" is now an enforced property on a hard-enforcement
provider rather than a request. Verified live: the supervisor used `Read` and
reported `monitor.py:1` correctly, and had no `Write`.

### D21 — an agent reported evidence that was confidently wrong

Final run, session `cao-final`. The worker created `verified.txt` correctly, then
reported:

> *"contents: exactly "TOOLS_CORRECT" (od -c shows: T O O L S _ C O R R E C T \n
> — single trailing newline, matching normal write behavior)."*

The file is **13 bytes with no trailing newline**. The work was right; the
byte-level evidence was fabricated. Worse, the supervisor **relayed it verbatim**
while holding `fs_read` and being able to check.

This is the failure the envelope protocol exists to catch, and it nearly passed
because the claim was specific, plausible and unprompted — the same qualities
that made the earlier `xxd` report look trustworthy.

**Corrected.** The generated supervisor prompt now says: re-verify every worker
claim you can check yourself; *relaying is not verifying*. A test asserts both the
instruction and that `fs_read` is actually granted, because telling an agent to
verify without giving it the tool is theatre.

**The general lesson, and it applies to this document too:** an agent's evidence
is a claim, not a measurement. Every number in this file that came from an agent
has been re-checked against the filesystem or the API.

---

## 10. Tier policy verified live (session `cao-tiers`)

Configuration: L2 `claude_code` / `opus` @ high effort, L3 `opencode_cli` /
`deepseek-direct/deepseek-flash`.

Supervisor, unprompted, in order:

> *"I'm running as Opus 5 (model ID claude-opus-5)."*

> *"I explicitly instructed the worker not to report byte counts or od dumps —
> that measurement is mine to make."*

> *"No Bash tool is available to me at this depth, so I can't run `wc -c`.
> Probing the trailing byte with a multiline regex instead."*

Worker reported `MODEL: deepseek-direct/deepseek-flash`. File written:
`tiers.txt`, 15 bytes, `OPUS_SUPERVISED`, verified independently.

Three things are demonstrated at once, and none were asked for in those words:

1. **The tier policy holds.** Layer 2 really is Opus 5, Layer 3 really is the
   pinned V4.1 Flash.
2. **The D21 fix generalised.** Told to re-verify rather than relay, the
   supervisor went further and removed the worker's opportunity to fabricate —
   it claimed the measurement as its own job.
3. **The tool restriction is real.** It wanted `wc -c`, found `Bash` genuinely
   denied, said so plainly, and worked within the limit with `Grep` rather than
   claiming a number it could not obtain. That is what an enforced restriction
   looks like from the inside, and it is the opposite of the earlier run where
   an agent invented an `od -c` dump.

---

## 11. D13 was misdiagnosed — agy is fixable (2026-09-11)

§4 claimed "CAO cannot reliably drive Gemini on this host" because the WSL `agy`
is a shim to `agy.exe` and "CAO's TUI session lands unauthenticated". The shim is
real; the conclusion drawn from it was not. Three measurements:

| Setup | cwd | agy TUI |
|---|---|---|
| Windows shim | `/mnt/c/...` | **signed in** — `<operator e-mail> (Google AI Pro)`, Antigravity CLI 1.2.0 |
| Windows shim | `/home/...` (ext4) | "You are currently not signed in" |
| Native Linux binary (fresh) | `/home/...` | "not signed in" — no Linux-side credentials yet |

So it was never interop, the TUI, or credentials. **A Windows process cannot
operate with its working directory on a Linux-only path.** The symptom presents
as an auth failure, which is why it was misread.

That collides head-on with §5: this lane puts worktrees on **ext4** for a 20x
filesystem win, and that is exactly the case a shimmed `agy` cannot serve.

### The fix

A native Linux `agy` exists — a Go binary, no Node, self-updating:

```bash
rm ~/.local/bin/agy
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy            # once, interactively, to sign in
```

Installed here: `agy` is now an ELF 64-bit executable (204 MB, v1.2.1) in place
of the four-line shim. The shim is kept at `/tmp/agy-shim.bak`.

**Still outstanding:** the native binary has no credentials of its own. Google
OAuth is interactive and not something an agent should perform, so `agy -p`
currently returns empty and `cao/probe.py` reports
`antigravity: not_authenticated` — which is the probe doing its job on a change
made minutes earlier, rather than reporting a stale OK.

`setup_cao.agy_flavour()` now distinguishes native / windows-shim / absent, and
`--check` warns with the exact fix when a shim is found, because the failure it
causes looks like something else entirely.

### Resolved (2026-09-11, later the same day)

The user signed in. Measured directly, in an ext4 cwd — the case the shim could
never serve:

```
$ cd ~/agy-ext4-test && agy --version && agy
Antigravity CLI 1.2.1
<operator e-mail> (Google AI Pro)
model: Gemini 3.8 Flash (High)
cwd: /home/<you>/agy-ext4-test
```

So the "still outstanding" paragraph above is closed: `antigravity` is a usable
pool inside CAO on this host, on the fast filesystem, and the earlier D13 verdict
("unusable") was wrong twice over — once in the diagnosis, once in the prognosis.

The trust prompt was the last gap. agy 1.2.1 asks **"Do you trust the contents of
this project?"**; `watchdog._AUTO_ANSWERS` matched only Claude Code's "files in
this folder", so a Gemini worker would have sat on its first prompt until CAO
deleted the terminal — the exact stall the watchdog exists to prevent. The
pattern now matches both measured wordings, and a test pins each.

---

## 12. Two orphaned capabilities, and a suite that would not start (2026-09-11)

### D22 — the anti-lying layer detected failure, never dishonesty

`cao verify` ran the phase's guard and acted on the verdict. It never looked at
what the agent had *said*. That leaves the more useful signal on the floor: a
phase that fails after an honest `status=blocked` is ordinary work, while a phase
that fails after `status=ok` means this agent's reports are unreliable on **every
phase it touched — including the ones whose guards passed**.

`verify.compare_claim()` now parses the terminal's RESULT envelope and compares it
with the measured verdict; `cao verify --phase X --terminal <id>` prints a
contradiction to stderr and appends `kind=contradiction` to `verify.ndjson`.

The deliberate non-rule: `error` (exit 97, guard could not run) is **never** a
contradiction. Absence of evidence cannot convict anyone, and a check that
manufactured accusations out of a broken worktree would be switched off within a
week. Pinned by `test_a_guard_that_could_not_run_never_accuses_anyone`.

Writing the function was not the fix. It sat tested and uncalled for one commit —
the same shape as every defect in section 8 — and only became real when `cmd_verify`
called it.

### D23 — `git status` was fine; `pytest` would not collect at all

```
$ python3 -m pytest skills/unattended-orchestration/tests/ -q
Failed: Marks cannot be applied to fixtures.
  .../libtmux/pytest_plugin.py:52
```

Zero tests ran. Nothing in this skill is wrong: pytest 9.1.1 auto-loads the
plugin `libtmux` 0.53.0 ships via entry point, and it dies at startup. libtmux is
a **CAO dependency**, so this hits precisely the hosts that can actually run this
lane — and the failure reads as "this skill is broken".

`skills/unattended-orchestration/pytest.ini` blocks it (`-p no:libtmux`). Nothing
here imports libtmux. It lives beside the skill, not at the repo root, so a copied
skill keeps working: the foreign-repo test runs pytest against the copy, and the
copy carries the file. Both facts are asserted, because a config file that fixes
only the repo it was written in is the portability bug this suite exists to catch.

Windows 417 passed / 6 skipped · WSL 417 passed / 6 skipped.

### D24 — two more capabilities that existed and were never reached

Auditing `cao/` for public callables with no caller found twelve; nine were CLI
verb handlers wired through `set_defaults(func=...)` (a false positive). The
other three were real, and two of them mattered:

**`worktree.hydrate()`** — a worktree holds tracked files only, so it has no
`.env`. `hydrate` copies it and pins the graph id, and nothing had called it
since provisioning moved into the launch shell. Every worktree this lane created
went out without `OMNIGRAPH_TOKEN`. Folded into `provision_script`, and measured
rather than asserted on as a string:

```
$ bash -c "$(provision_script repo wt --graph demo-graph)"
$ cat wt/.env
OMNIGRAPH_TOKEN=secret123
OMNIGRAPH_GRAPH_ID=demo-graph
$ # re-run (resume does): still one pin, token intact
```

The pin belongs in the *file*, not only in `cao launch --env`, because children a
supervisor spawns inherit the worktree rather than Layer 1's environment.

**`wire.render_result()`** — the function that defines the RESULT envelope.
Generated profiles said "Reply only with a RESULT envelope" and never showed its
shape, so what an agent was *taught* and what `wire.parse_result` *accepts* were
two independent definitions. That is not cosmetic now: `compare_claim` treats an
unparsable envelope as **no claim**, so a drifted format would silently switch
off the contradiction check rather than fail anything. The profile now renders its
example with `render_result`, and a test parses the example back out of the
generated prompt.

**`worktree.provision()`** is the remaining orphan and stays: it is the local-host
implementation, used by tools and tests, while the launch path needs the shell
form because provisioning happens in the distro. Both now route through
`provision_script` for the steps that must agree.

The five shell tests **skip on Windows** — `bash` there is WSL's and cannot see a
`C:/Users/...` temp directory. That is stated in the skip reason rather than
hidden, and they run for real in WSL, which is where this lane executes.

Windows 421 passed / 11 skipped · WSL 426 passed / 6 skipped (432 tests either way; the difference is the five shell tests).

### D25 — the quota patterns were guesses; now they are counted

This lane shipped with "quota-detection patterns are unverified until a real
limit is provoked" as a stated known limit. Provoking one on demand is not
practical, but the wordings do not have to be guessed: they are *in the shipped
binaries*. Counted 2026-09-11 with `strings` on Claude Code 2.1.236 (320 MB,
`~/.local/share/claude/versions/2.1.236`):

```
28  billing_error                     25  rate_limit_error
25  overloaded_error                  34  request timed out (both casings)
16  Usage limit reached               16  RESOURCE_EXHAUSTED
 7  quota exceeded                     5  credit balance too low
 4  spend limit reached                5  Too Many Requests (both casings)
```

and on the native `agy` Go binary: `RESOURCE_EXHAUSTED`,
`ERROR_CODE_RESOURCE_EXHAUSTED`.

Against the five patterns this lane shipped with, that is two defects:

1. **`billing_error`, `credit balance too low`, `spend limit reached` matched
   nothing.** The most consequential wall of the three was invisible, so the
   ladder never descended and the run kept dispatching into it.
2. **`overloaded_error` / 529 would have matched nothing either — correct by
   accident.** Adding the missing wordings naively (anything limit-shaped closes
   the pool) would have *introduced* the opposite defect: a 529 is seconds of
   congestion, and closing a pool for an hour over one declines capacity that was
   never gone. That directly contradicts this lane's own "never decline a pool".

So detection now classifies rather than matches: `exhausted` cools, `billing`
closes outright (waiting cannot help), `transient` does nothing. Transient is
checked first and wins, because these bodies carry prose and a stray "rate limit"
inside a 529 body would close a healthy pool.

Still honest about what this is **not**: these are the strings the vendor ships,
not a limit observed live. The failure mode it removes is a pattern list written
from memory; it does not prove the surrounding flow behaves correctly when a real
limit lands. `is_open` still fails OPEN.

---

## 13. The first end-to-end live run (2026-09-11, sessions `cao-agent-skills-smoke1*`)

Everything above this section was unit-tested and reviewed. The run below took
one trivial phase -- *create `LIVE_PROOF.md` containing `cao-lane-live-ok`*,
guard `grep -qx cao-lane-live-ok LIVE_PROOF.md` -- through the real lane against
a real `cao-server` on :9889. It found **six** defects in ninety minutes, five of
which no unit test would ever have found, because each one only exists where this
code meets something it does not own.

### What worked on the first try

* The **gate refused my own plan**: `assumedDefaults` used `because` instead of
  `why`. The first thing the lane did was stop me.
* **Hydration** landed: the worktree came up with `.env` carrying
  `OMNIGRAPH_GRAPH_ID=agent-skills`, and `.claude/settings.local.json` trusted.
* The **supervisor obeyed `use_model:`** on its very first live envelope --
  *"I've delegated to worker fc1cc684 (developer profile, pinned to
  gemini-3.8-flash-high)"*.
* The **supervisor propagated the discipline downward** unprompted, telling its
  leaf: *"Do NOT edit or weaken that command. Do not claim success unless you
  actually ran it and saw exit=0"*, and *"then I will independently Read the file
  and re-run the guard myself before reporting anything to Layer 1"*.

### D26 — "completed" means the pane exited, not that the work is done

CAO reported both terminals `completed`. `LIVE_PROOF.md` did not exist. The
supervisor's session had been killed mid-delegation by *"Remote Control
disconnected -- this session was ended or archived from another device or app
(code 4090)"*.

This is the single strongest argument for the rule this lane is built on: **Layer
1 runs the guard itself.** Every status CAO had was green.

### D27 — the contradiction check could never have fired

`compare_claim` fed `wire.parse_result` -- which matches **line 0** -- with a tmux
pane: 19,930 characters of ANSI. It returned "no claim" and said nothing. It would
have returned "no claim" for every terminal that will ever exist.

A check that cannot fire is worse than no check, because its silence reads as
evidence of honesty. `wire.find_result()` now strips ANSI and scans for the LAST
envelope in the capture.

### D28 — stripping ANSI welded the words together

The first stripper turned the supervisor's line into
`I'vedelegatedtoworkerfc1cc684`. A tmux capture is a *rendered* view: runs of
spaces arrive as cursor-positioning escapes (`ESC[42G`), so deleting them closes
the gaps -- and would have welded a real header into `RESULTp1status=ok`,
defeating the fix in D27 one layer down. Positioning escapes now become a single
space.

### D29 — an agent that dies silently looked identical to one that lied

With D27 fixed, the honest reading of the smoke run was *"guard failed, no claim
at all"*. That is not dishonesty -- it is an agent that was killed -- and the
operator was told nothing. `verify --terminal` now distinguishes three outcomes:
CONTRADICTION (claimed ok, guard disagrees), NO REPORT (guard failed, nothing
claimed), and silence when the work is done.

### D30 — looking at a phase spent its budget for fixing it

Three `cao verify` runs on one phase -- two of them pure inspection -- produced
*"NEEDS HUMAN: 3 failed attempts (cap 3)"*. `attempts_for` counted **failed
verdicts**, so reading the state consumed the rework allowance. `verify` is the
read-mostly operator surface; it now counts rework **spawns**, recorded where an
agent is actually launched.

(Wiring `record_rework` then failed silently -- imported, never called -- and the
next live rework proved it by reusing a session name. A test now asserts the
spawn records.)

### D31 — the rework path was unreachable: session names are unique server-side

```
Error: Failed to connect to cao-server: 400 Client Error: Bad Request
{"detail":"Session 'cao-agent-skills-smoke1' already exists"}
```

Every rework reused the failed attempt's session name, so **no rework could ever
launch** -- the lane's whole answer to "an agent did shit" was dead on arrival.
Attempts now get `-r<n>`, and the number is chosen from the names CAO actually
holds (`pick_attempt`), not from a local counter: measured, the ledger said "no
reworks yet" while the server already held `-r2`.

### D32 — `probe` knew the pool was dead and nothing listened

Two workers died on *"There's an issue with the selected model
(antigravity/gemini-3.8-flash-high). It may not exist or you may not have access
to it."*

Two separate faults:

1. **Ours.** The envelope carried `use_model: antigravity/gemini-3.8-flash-high`
   and the supervisor passed it verbatim as a model id. `agy models` lists
   `gemini-3.8-flash-high`; the slash was ours. Pool and model are now separate
   fields, and a test forbids the slash form.
2. **The pool was genuinely down** -- and `python -m cao probe` said so:
   `antigravity   failed   no answer in 90s`. Routing selected it anyway, because
   `check` (credentials exist) wrote to the budget ledger and `probe` (the pool
   WORKS) wrote to nothing. Probe now records both directions -- a failure closes
   the pool, a pass reinstates it, so one bad minute cannot remove a pool for the
   rest of a run.

### The loop, closed

With D32 fixed the ladder descended on its own:

```
$ python -m cao probe
antigravity   failed   no answer in 60s
$ python -m cao launch --phase smoke1 --dry-run
leaf model: anthropic/sonnet effort=medium   (was antigravity/gemini-3.8-flash-high)
$ python -m cao verify --phase smoke1 --rework
Session created: cao-agent-skills-smoke1-r4
[3] ARTIFACT APPEARED
$ cat LIVE_PROOF.md
cao-lane-live-ok
$ python -m cao verify --phase smoke1
exit_code: 0   verdict: PASS
lease released: the guard passed, so the phase is genuinely done.
```

plan -> launch -> agent fails -> guard catches it -> rework -> dead pool routed
around -> work done -> guard passes -> lease released. Four supervisor sessions,
four leaves, two providers, no human decision in the loop.

### D33 — a live run leaves state in the repo, and it was not ignored

`--state-dir` defaults to `output/cao`, inside the checkout. After the run it
held two untracked NDJSON ledgers containing verdicts, quota records and captured
agent output, and **`output/cao/leases/wire.lease` was already tracked** -- a
stale lock committed on 2026-09-10 that would have refused any future phase named
`wire`. Both paths are now ignored, the stale lease is gone, and two tests assert
it: one that nothing under them is tracked, one that `git check-ignore` actually
matches.

Windows 469 passed / 11 skipped · WSL 474 passed / 6 skipped (485 tests).

---

## 14. The probe was wrong in both directions (2026-09-11, after §13)

§13 ended by wiring `probe` to gate routing: a failing pool stops being
dispatched to. That made the probe's own correctness load-bearing, and it was
not correct. Four faults, found by chasing one question -- *why can agy not
serve a leaf?* -- to the end.

### D34 — agy will not answer without a terminal, and the probe never has one

```
$ agy -p 'Reply with exactly: PROBE_OK' --model gemini-3.8-flash-high   # in tmux
PROBE_OK
EXIT=0

$ bash -lc "agy -p '...'" 2>&1 | tail -1     # stdout is a pipe
error: interrupted
```

`subprocess.run(capture_output=True)` always gives a pipe, so **a healthy,
signed-in provider always looked broken** -- and once probe gated routing, that
false negative declined a working pool. The antigravity probe now runs inside a
throwaway tmux session, which is not a workaround but the honest test: CAO runs
every agent in a tmux pane.

### D35 — only proof may close a pool

Even with D34 fixed, closing on any non-`ok` verdict is wrong. A timeout, a
harness limitation, or an unrecognised banner is not evidence the pool is down,
and closing on it declines capacity -- against this lane's own standing rule.
`closes_pool()` now admits exactly two verdicts, `not_installed` and
`not_authenticated`; `quota` cools through the budget ledger instead (a closure
has no expiry and would outlive the wall); `failed` and the new `inconclusive`
are reported loudly and acted on not at all.

### D36 — the probe was grading its own echo

With the tmux probe in place, antigravity reported `ok` while the pane contained
nothing but the command line. A capture holds the **question** as well as the
answer, and the question is `Reply with exactly: PROBE_OK` -- so
`PROBE_TOKEN in stdout` was true however the model behaved. The probe could only
ever say ok.

Rephrasing the question so it cannot contain its own answer traded a false pass
for a false failure (neither tier answered the longer prompt within 120 s). The
fix that works in both directions is structural: the probe shell prints a
sentinel *after* the shell echoes the command, and only the slice after its last
occurrence is classified.

### D37 — the cheapest model was the one that does not answer

`_PROBE_MODELS["antigravity"]` was `gemini-3.8-flash-low`, chosen as "the
cheapest model that still proves the account works". Measured three times, it
returned nothing in 120 s; `gemini-3.8-flash-high` answered in ~22 s. Cheapest is
only the right probe if it replies.

### Live, after all four

```
pool          verdict             evidence
------------------------------------------------------------------------
anthropic     ok                  PROBE_OK
antigravity   ok                  PROBE_OK
deepseek      ok                  PROBE_OK
```

and the ladder puts `antigravity/gemini-3.8-flash-high` back at the leaf. The
sequence is worth keeping in view: the pool went dead -> healthy-but-unprobeable
-> falsely ok -> honestly failed -> honestly ok, and three of those five states
would have been reported to an operator as fact.

**Still open.** The live worker failure in §13 (*"There's an issue with the
selected model"* inside a CAO-spawned pane) is **not** explained by any of this.
agy answers that exact model from a plain tmux pane, in an ext4 cwd, with CAO's
own flags including the `-i` injection. Whatever differs, it is on the `assign`
path, and it is not the model id, the account, the filesystem or the terminal.
Recorded as unexplained rather than guessed at.

---

## 15. Production readiness: what another agent hits first (2026-09-11)

Three gaps found by asking "if a different agent picked this up tomorrow, where
does it lose an hour?"

### D38 — preflight said READY while nothing could run

```
$ python -m cao check
  anthropic      available
  antigravity    available
  deepseek       available
warnings (0)
$ echo $?
0
```

…with no `cao-server` running. Every verb that acts — `launch`, `sweep`,
`verify --terminal`, `resume` — needs it. An agent reads green, runs `launch`,
and gets a connection error it had no reason to expect. `check` now probes the
server, prints it either way, and exits **1** with the command that fixes it.
Unavailable *pools* still exit 0, because ladders route around those; an
unreachable *server* means nothing runs at all.

### D39 — the message shown at the worst moment named a flag that does not exist

`plan_launch`'s refusal — the text an operator sees precisely when they are
stuck — said:

> agree one with the user first (`python -m cao plan --init`)

There is no `--init`. Following the advice produces an argparse error on top of
the original problem. Fixed, and a test now extracts every `python -m cao …`
string from the lane's own code and SKILL.md and parses it with the real parser.
Verified non-vacuous: `plan --init` is rejected by that parser.

### D40 — worktrees accumulate, and only a human noticed

Cleaning up after §13 found three worktrees from earlier runs still registered,
holding nothing but scratch tokens (`OPUS_SUPERVISED`, `TOOLS_CORRECT`,
`LAUNCH_PATH_OK`) already recorded in §10. Every tracked diff in them was the
CRLF phantom of §14; no commits ahead of main, no stashes. Removed.

`cao sweep` now reports worktrees whose session is gone, with each one's dirty
state and the command to reclaim it — and **never removes one**. A worktree is
where an agent's uncommitted work lives, and "no session" can also mean the
server was restarted; deleting on that inference trades work for tidiness.
`is_dirty` counts an unreadable worktree as dirty for the same reason: unknown
must never read as safe to delete.

Live afterwards:

```
worktrees with no session: 1
  /home/<you>/cao-worktrees/agent-skills-demo9  (DIRTY - has uncommitted work)
  a DIRTY one holds work an agent never committed - look first.
```
