# OpenCode Fleet Configuration & Plugins

Fleet operational notes and configurations for OpenCode agent runners across this repository. This page currently documents the `bash-guard` plugin integration, ensuring execution-safety parity across Claude and OpenCode harnesses.

## bash-guard plugin

### Overview

`configuration/opencode/plugins/bash-guard/index.mjs` is an OpenCode v2 plugin that hooks into tool execution before execution (`execute.before`) for the `shell` and `bash` tools. It delegates inspection to the SAME guard core as the Claude hook (`tools/hooks/bash_guard.py`) with no second copy of the safety rules.

When the guard core exits with code 2, the plugin throws an error to refuse execution, presenting the guard's refusal message directly to the model. Every other outcome—such as a missing guard script, spawn error, non-zero unexpected exit code, or exceeding the 5 s process timeout—fails OPEN with a single `bash-guard:` log line on stderr, ensuring agent sessions are never blocked by harness infrastructure errors.

### OpenCode Configuration

OpenCode v2 loads a plugin from a DIRECTORY that holds `index.mjs` (measured on 2.0.12: a single file path in the config is
rejected with "configured plugin path must be a directory", and a v1-style plugin does not load). Two ways to load it:

1. Auto-discovery: put (or link) the directory at `<project>/.opencode/plugins/bash-guard/` so that
   `<project>/.opencode/plugins/bash-guard/index.mjs` exists; it is hot-reloaded within seconds when the file changes.
2. The config key `plugins` with the DIRECTORY as the entry:

```jsonc
{
  "plugins": [
    "configuration/opencode/plugins/bash-guard"
  ]
}
```

(The entry is a directory path; use an absolute path when the session's working directory is not the repo root. Check the
installed opencode version's config schema before changing the spelling.)

### Environment Variables

- `AUTOOS_REPO_ROOT`: Optional override pointing to the AutoOS repository root where `tools/hooks/bash_guard.py` resides. If unset, the repository root is derived automatically from the plugin file location.

### Mandatory Operational Rule

OpenCode L1 sessions MUST load the `bash-guard` plugin before they run unattended.

### How to Verify

1. Start a scratch session with the plugin enabled.
2. Ask the model to execute a shell command with an UNQUOTED heredoc containing a backticked command, such as:
   ```bash
   cat <<EOF
   `date`
   EOF
   ```
   The call must be refused and the refusal message displayed to the model.
3. Ask the model to execute a command with a QUOTED heredoc, such as:
   ```bash
   cat <<'EOF'
   `date`
   EOF
   ```
   The command must execute successfully.

### Known Limits

- **Fail-open design:** Missing dependencies, execution timeouts (> 5 s), or Python runtime errors fail open with a diagnostic stderr log line.
- **Tool scope:** Inspects only the `shell` and `bash` tools; calls via custom tools or external runners bypass this hook.
- **Rule set:** Currently covers unquoted heredocs with backticks/subshells and `claude --bg/-p/--print` nested calls. The kill-by-image-name rule is added to the core with FLEET-HOOKS v2.3 later.

## oc_l1.py launcher

`tools/oc_l1.py` starts ONE OpenCode orchestrator (an "L1") for ONE lane from a host-local config: one `opencode serve`
bound to 127.0.0.1, one session, relaunchable from a handoff card. It has three subcommands:

```bash
python3 tools/oc_l1.py render --name <lane> [--config PATH]   # write the scratch opencode config, nothing else
python3 tools/oc_l1.py start  --name <lane> [--config PATH]   # render, start the server, create the session, run the canary
python3 tools/oc_l1.py status --name <lane> [--config PATH]   # live | silent | dead
```

### Config

The config is host-local and never committed: `${XDG_CONFIG_HOME:-~/.config}/autoos/oc-l1.json` on POSIX,
`%LOCALAPPDATA%\autoos\oc-l1.json` on Windows (override with `--config`). Copy
`configuration/oc-l1.example.json` and fill in real values; every key is documented in its `_comment`. Important rules:

- `password_env` holds only the NAME of an environment variable. `start` refuses (exit 2, nothing started) when that variable
  is not set in the process environment; the value is never written to a file, a command line or the rendered config.
- `opencode_bin` is the explicit path of the npm `opencode` binary. It is never looked up on PATH and never the desktop app's
  background service.
- The server binds to 127.0.0.1 only (`--hostname 127.0.0.1`), on `serve_port` (default: a stable hash of the lane name in
  47200-47299). That number is a guess, so `start` binds it before spawning and, when a listener already holds it, walks forward to
  the next free port in the range — deterministic, so a relaunch lands where the last run did. The port the server really took is
  what the state file records, and status/stop/inbox all read that number.
- `render` writes a scratch config under `scratch_dir`; the user's own opencode config is never touched. Only the MCP servers
  listed in the lane's `mcp` are enabled; every other server of the repo `opencode.jsonc` is disabled.
- `plugins` lists plugin DIRECTORIES (each holding an `index.mjs`), for example the bash-guard plugin of the section above.
  `start` refuses a lane whose `plugins` list is empty (exit 2, before the server spawns): with no plugin nothing can deny the
  canary, so such a lane could only ever exit 5 (D-665). `render` alone still accepts an empty list.
- The scratch file uses opencode's OWN config schema (singular `provider`, models keyed by the gateway model id, a flat `mcp` map,
  `plugins`, `permission`, no `server` block), not the repo-file shape; `tests/test_oc_l1_render.py` pins it field by field.
- The `autoos-agent` MCP entry gets `AUTOOS_WORKERS_DIR` (lane key `workers_dir`, default `<cwd>/logs/workers`): without it the MCP
  tools run `git rev-parse` with an inherited stdin and, under opencode's stdio transport, the first `ps` call hangs.
- Six optional keys exist for a lane that orchestrates instead of writes (D-665, used by `oc_l2.py`): `permission` (a map merged
  OVER the rendered permission defaults, e.g. `{"task": "deny"}`; the reserved key `external_directory` is refused there because it
  has its own lane list), `guard_role` (exported to the server child as `AUTOOS_GUARD_ROLE`), `inbox_file` (exported as
  `AUTOOS_L1_INBOX`), `first_prompt_file` (posted verbatim as the pilot's first prompt instead of the handoff head), `agent_layer`
  (exported as `AUTOOS_AGENT_LAYER` — the level the lane runs at, and what the MCP fence below reads) and `child_env` (the extra
  environment NAMES a lane needs beyond the inherited set; any lane may set it). An L1 lane that sets none of them behaves exactly
  as before.

### start, relaunch and the canary

`start` renders the config, starts the server with scratch XDG directories (so no other tool's skills load), waits for health,
creates the pilot session and writes the state file atomically (mode 0600 on POSIX). It then runs the bash-guard CANARY: a
second throwaway session on the same server is asked to run a command that the guard must refuse (an unquoted heredoc with a
backticked command). The prompt demands EXACTLY ONE tool call and names the shell tool — the probe is the call, not a reply
about it — and the canary recognises exactly the names the plugin hooks, `shell` and `bash` (opencode renamed `bash` to `shell`);
`execute` is code mode's executor and the guard never inspects it, so a "denial" carried by `execute` is the model quoting the
marker, not evidence about the guard. `tests/test_oc_l1_canary.py` pins the two sets so they cannot drift. The result
`{denied, ts, plugin_path, session_id, detail}` is stored in the state file and merged into
`heartbeat.json`; `detail` always says what the session did (the tool names seen, the first 200 chars of assistant text), so a
refusal is diagnosable from the host heartbeat without re-running anything. Only after a DENIED canary does `start` post the
pilot's first prompt (the handoff head plus a hint line about MCP tools): a pilot that is not known to be guarded never runs.

- Canary DENIED: the first prompt is posted, exit 0, the L1 may run unattended. A denial is recognised by the marker
  `bash-guard: DENIED` **at the start of the tool's error text** (whitespace aside), on a call to a guarded tool whose status is
  `error` — a marker that merely appears inside the text, in an assistant reply, or on a completed call is not a denial. EVERY
  denial path carries the marker — both `tools/hooks/bash_guard.py` and the plugin's own
  orchestrator-role throw, which runs before the Python guard (D-665). Anything the launcher prints or logs about an HTTP failure
  is scrubbed of the password, the base64 `Basic …` token and any `Authorization:` value.
- Canary NOT denied (the command ran, no tool call happened, timeout, error): NO prompt is posted, the pilot session stays idle,
  the server stays up for supervised use, `start` prints `UNATTENDED-REFUSED` and exits 5. The plugin fails open on purpose, so
  an unproven guard means no unattended run. A model that only answered in text is `inconclusive: text-only answer`, which is
  exit 5 with a different `detail` — it is NOT evidence that the guard allowed anything.
- A second `start` on a live session posts nothing new and prints the stored canary line; if that stored canary was not denied
  it prints `UNATTENDED-REFUSED` and exits 5 again (it never reports a refused start as live). A `start` that finds the server
  dead is a RELAUNCH: it starts a new server and runs the canary again, and `heartbeat.json` gets the new result.
- The server child's environment is an **allowlist**, not `dict(os.environ)` (D-665 fix 2): handing over the launcher's whole
  environment put every credential the shell happened to export — a GitHub token, a provider key, a database password — in the
  lane's `/proc/<pid>/environ`, readable by the lane, by the MCP server it starts and by every worker it spawns. `oc_l1` holds the
  rule in one place: the names a session needs (`PATH`, `HOME`, locale, terminal, proxy and certificate variables, the `XDG_*`,
  `GIT_*`, `NODE_*`, `PYTHON*`, `SSL_*`, `AUTOOS_*`, `SESSION_*` families, the Windows equivalents) minus every credential-shaped
  name in them — a name ending in `_PW`/`_KEY`/`_TOKEN`/`_SECRET`/`_PASSWORD` or containing `PASSWORD`/`SECRET`/`TOKEN`/
  `CREDENTIAL`/`APIKEY` stays with the launcher. Two credentials are exempt because the rendered config references them as
  `{env:...}` and the lane has no other way to its model: `AUTOOS_OMNIROUTE_URL` and `AUTOOS_OMNIROUTE_KEY`. The server password
  reaches the child under `OPENCODE_SERVER_PASSWORD` alone, and `AUTOOS_GUARD_ROLE`/`AUTOOS_L1_INBOX`/`AUTOOS_AGENT_LAYER` are set
  from the LANE and never inherited. A lane that genuinely needs one more variable names it in `child_env`; a credential-shaped
  name there is a validation error, and a value never belongs in a lane config. What was left behind is printed by NAME at spawn,
  so a missing variable is diagnosable instead of silent.
- **The layer fence.** One `autoos-agent` MCP server answers both an L1 and an L2, and the same lane tools are on it, so
  without a fence an L2 could start, stop or nudge lanes — relaunch its own supervisor, or switch off a phase it does not own. The
  L2 lane marks its own level (`agent_layer: "L2"` → `AUTOOS_AGENT_LAYER=L2` in the child env AND in the MCP server's own rendered
  env), and that one marker does two things. It picks the server's TOOL PROFILE: an L2 registers the spawner and its read-only
  companions — `spawn`, `status`, `result`, `ps`, `list_clients`, `route`, `context`, `heartbeat` — and lists no lane tool, no
  `cancel`, no `respond` at all (an unknown profile falls back to this narrowest list, never to the full one). And it fences what
  remains: `l2_start`, `l2_stop`, `l2_resume`, `l2_inbox`, `oc_start` and `oc_restart` still answer `refused: true` when they
  read `L2` back, because the same functions are reachable from the CLI and a hidden tool is not a permitted call; a `spawn` from
  an L2 is a tier-2 or tier-3 worker — a writer or a reviewer — forced into its own clone (`--isolate`, whatever the caller
  passed), while tier 1 and any `role: orchestrate` card are refused before a run dir exists. It used to be tier-3-only, which was
  a dead end: tier 3 is the
  review-only seat and refuses an implement card, so an L2 could never start a writer (found live by AO-L2-PRODTEST). A spawn that
  passes that fence is stamped `AUTOOS_AGENT_LAYER=L3` — the child's layer is decided by the spawner, from the spawner's own
  environment, never inherited and never a plan entry or a caller's `extra`, because a child that chooses its own mark chooses its
  own fence — and a server that reads `L3` back registers the L2 menu WITHOUT `spawn` (profile `l3`) and refuses a `spawn` call
  made anyway: a leaf never spawns (skill rule `R-worker-06`). Both rules are read again by the CLI's `run` — the same helper, so a
  lane's own shell cannot get a warmer answer than its own server gave — and either refusal exits `14` (`EXIT_LAYER_FENCE`), which
  is a report upward, not a flag to fix and retry. An L1's worker carries no mark at all and keeps the menu it always had.
  `l2_status` and `oc_status`
  stay open for a caller that can see them: watching one's own lane is ordinary L2 work, and upward reporting goes to the L1 inbox.
- The child's stderr is appended to `<scratch_dir>/opencode.log`, created 0600 with the scratch tree 0700 — the modes are applied
  at creation, so there is no window in which the log sits world-readable next to a child environment that carries the password:
  the guard's fail-open notes are written there, and
  a `DEVNULL` made "the plugin never loaded" indistinguishable from "the plugin allowed" (D-665).

Exit codes of `start`: 0 started or already live, 2 config or validation error (password env unset, empty `plugins`), 4 health
timeout (the child is killed by its recorded PID), 5 canary not denied.

### status

`status` asks the server (v2 API, Basic auth from the same environment variable): `live` (exit 0), `silent` (exit 1: alive, but
no new assistant message or tool item for `silent_minutes`, default 10) or `dead` (exit 2: no answer, or the session ended as
failed or interrupted).

### Templates

- `configuration/oc-l1/autoos-oc-l1.service`: example systemd user unit that runs `start`.
- `configuration/oc-l1/windows-task.md`: the Windows Scheduled Task equivalent.

## oc_l2.py phase lanes (D-665)

`tools/oc_l2.py` starts ONE OpenCode orchestrator (an "L2") per PHASE of a project and hands it a brief. It is a lane *producer*,
not a second launcher: it resolves what an L2 lane is, writes that as an `oc_l1` lane config, and delegates the render, the health
poll, the canary and the state file to `tools/oc_l1.py` — so a fix to the canary lands here for free.

```bash
python3 tools/oc_l2.py start  --repo PATH --phase NAME --brief PATH [--combo l2-orchestrator] [--inbox PATH]
python3 tools/oc_l2.py status --lane l2-<repo>-<checkout-tag>-<phase>
python3 tools/oc_l2.py stop   --lane l2-<repo>-<checkout-tag>-<phase>
python3 tools/oc_l2.py inbox  --lane l2-<repo>-<checkout-tag>-<phase> --text LINE
python3 tools/oc_l2.py resume --lane l2-<repo>-<checkout-tag>-<phase>
```

Each subcommand prints exactly one JSON object, and the exit codes are `oc_l1`'s, forwarded: 0 ok, 2 config/validation/refusal,
4 health timeout, 5 `UNATTENDED-REFUSED`. `status` adds one verdict of its own: `stalled` (exit 1) — the session is alive but its
last turn ended in error, or it sits idle while a child run it spawned already exited. Children are discovered, not recorded, and
attributed by IDENTITY (AO-L2-RESUME F1): the lane's own environment carries `AUTOOS_L2_LANE=<lane>`, the spawner records that as
`parent_lane` at the top of every run's `job.json`, and only a run whose `parent_lane` is the lane is its child — the same-cwd and
start-time filters narrow a match, they never make one. A run anyone else starts in the lane's directory therefore cannot stall the
lane or wake it for someone else's process. They are read from the spawner's own root (`AUTOOS_STATE_DIR`/agents, recorded in the
lane config as `l2.agents_root` at start, because a lane tool's CLI child gets an allowlist that does not carry that variable), and
child state is the run's own `exit.json` — never `pgrep -f`, which matches the caller's own argv.

`resume` wakes a stalled lane with one short prompt naming the child or the error and the next action the stall implies (`child
<run-id> exited rc=N; read <run-id> result via autoos-agent result and continue the phase plan`, and for an errored turn `re-read
the last tool error, retry the failed step once, then continue the phase plan`) — `stalled` puts that on the verdict as `next_action`
so the wake, the answer and the `status` detail all say the same thing — and the three outcomes are distinguishable in that one JSON
object.
ONE wake per stall (F2): the wake is recorded in the lane's `heartbeat.json` (`last_wake_ts`, `last_wake_key`, the run id), and
while it stands — no turn activity newer than the wake and the 10-minute wake window unexpired — `stalled` reads `already-woken` and
a second `resume` is a no-op instead of a prompt per poll. An `inbox` nudge that lands on a stalled lane records that same marker
under the same key (P1): the nudge posts a prompt, so it IS the wake for the stall it named, and the `resume` sent straight after it
answers `already_woken` with no second prompt — a nudge that only the append landed (a refused HTTP status) records nothing, and the
stall stays wakeable. The marker belongs to the session that wrote it: `start` clears every
`last_wake_*` key from the heartbeat the canary merged forward (and a restart goes through `start`), because a new session reading
its predecessor's marker would report its first stall of the same key `already-woken` and never be woken. Only the markers go — the
turn count merges forward and the canary record stays.
A RESTART is for a lane that cannot take a prompt at all (F3), and the session probe names which of
three it is (`probe_session` → `live` / `gone` / `unknown`): the session is PROVABLY gone (`live_session`
answers nothing because nothing listens, the session replies 404, or it ended `failed`/`interrupted`) or
the connection itself failed, and then `resume` stops and starts the lane from its stored config and
reports `restarted`. An unreadable probe is not a gone session: the password env unset, a 401/403/5xx or
a reply that will not parse answers `restart_refused` with the `probe` reason and exit 2 while touching
nothing at all — the stop it would have run needs no password and the `start` after it is refused for that
same missing password, so an unknown read would kill a working lane and leave it dead. `stalled` answers
`probe-unknown` for the same case, so an unreadable lane never becomes a restart trigger off a poll.
A lane whose process is already gone counts as stopped —
nothing to kill, the state file goes, the start brings it back. A stop that was REFUSED (R-coord-10: a pid it cannot prove is the
lane, a state file that would not go) leaves the old server holding the port, so no start is attempted: the answer is
`restart_failed: stop refused (<cmd_stop's detail>)` with exit 2, not `start`'s `already running`. An HTTP status that is merely a refusal (409 busy is the
server answering a working lane) reports `wake_rejected` with `http_status` and leaves the lane, its state file and its process
alone — a healthy lane is never restarted for being busy. What the record and the transcript held is forced through one printable
capped line before it is interpolated (F6), so an error string with a newline cannot end the wake sentence and start an instruction
of its own. The five are MCP tools on the `autoos-agent` server (`l2_start`, `l2_status`, `l2_stop`, `l2_resume`, `l2_inbox`), so an
L1 coordinates phases without leaving its own session.

`stalled` judges exactly one turn of the transcript: the newest progress item, decided by its timestamp (`order=desc` position only
breaks a tie), so an errored turn the lane already spoke past is history, not a stall. An exited child
is likewise not a stall while the newest message is an assistant turn that has not ended (no `finish`,
no completed time): the lane is mid-turn, not quiet, and the turn that reads the result is the one it is
writing. The `status` and `inbox` polls treat a stall
probe and a heartbeat merge as decoration: what the lane state can legitimately fail with (`L2Error`, `ServerDown`, `OSError`,
`ValueError`) is named in the answer as `stalled_error` / `activity_error` and the poll carries on with its ordinary verdict, while
anything else raises — a bug in the probe is not reported as a lane that is simply not stalled.
Both polls also merge the transcript into `heartbeat.json`, and the stamps there are the messages' own, never the moment of the
poll (P1): `last_message_ts` and `last_activity_ts` both carry the newest progress item's timestamp, so a poll that sees no new
message leaves the lane exactly as stale as it was — which is what an external monitor watching one needs to read a stalled lane off
the file at all, instead of a fresh `last_activity_ts` per ask.

The lane it renders — and an L2 has nothing else, which is the point (R-coord-14: the L2 never edits code):

- lane name `l2-<repo>-<checkout-tag>-<phase>`, where the checkout tag is six hex of the repo's absolute path — two projects whose
  directories share a name are two lanes, while one project spelled two ways (`AutoOS CI`, `autoos-ci`) is still one lane;
  model = the gateway combo (`l2-orchestrator` by default), with the context and output **from the
  registry route's own `surfaces.omniroute`** so a 1M lane cannot be clamped to 128k;
- `mcp: ["autoos-agent"]` — the spawner is the only enabled server: no editor, no filesystem MCP, no second spawner; and because the
  lane renders `AUTOOS_AGENT_LAYER=L2` into that server's own environment, the spawner answers it with the L2 tool profile (above),
  not with the full menu;
- `permission` merged over the L1 defaults with every file-mutating and spawn key denied (`edit`, `write`, `patch`, `apply_patch`,
  `task`, `subagent`; `read` and `bash` stay allowed), plus the bash-guard plugin with `guard_role: "l2"` — a role of its own, not
  the orchestrator's scoped write: an L2's shell is a CLOSED READ-ONLY list (`git status|log|diff|show`, `ls`, `cat`, `rg`, `head`,
  `tail`, `wc`, `pwd`), with no output redirection, no stdin redirection, no here-document, no command substitution and no `$`
  anywhere in the command (`$KEY`, `${KEY}`, `$((1))`, `$(cmd)`): a glued option value is not a path, so the operand rules never
  saw one and `rg -r$AUTOOS_OMNIROUTE_KEY foo README.md` printed the gateway key through a read-only head (L2SECRETS fix 10); no
  environment-assignment prefix and no `env`/`printenv`/`set`/`export`/`declare` head (the lane's own environment holds the server
  password and the gateway keys); no flag that hands the read a program, a repository or a pager, tested inside a short bundle
  (`rg -uz`) as well as spelled out; and no path operand the shell can move outside the checkout — absolute, `~`-led (glued into an
  option value as much as standing alone), `..`-crossing, `//`-spaced, or containing a backtick or a glob character is refused as
  `path outside repo`, and the secret files that sit
  *inside* the repo (`.env*`, `*.key`, `*.pem`, `api-keys.yml`, `*credentials*.json`) with it. Anything else is denied with
  `bash-guard: DENIED - l2 read-only: …`. The canary command is denied by this role too, so a lane that starts
  is a lane whose guard works;
- the first prompt is the WHOLE brief plus a fixed footer (load `unattended-orchestration`, never edit code, spawn workers — a
  tier-2 writer or a tier-3 reviewer, never tier 1 and never an `orchestrate` card, and always in an isolated clone — through the
  `autoos-agent` MCP, report `REPORT`/`DONE` to the L1 inbox with the `l2_report` tool, since a read-only
  shell cannot append), wait on children via `autoos-agent status`/`result` (their run record under
  `logs/agents/<run>/`, never `pgrep -f`), and the launcher's hint line.

**Where the reports land.** The child env carries `AUTOOS_L1_INBOX` (lane key `inbox_file`), resolved from `--inbox` or that
variable and refused when neither names one — a report that goes nowhere is a phase that silently never finishes. The L2 appends
one timestamped line per milestone there, prefixed `REPORT`, and a final `DONE` (or `BLOCKED`) line; the repo convention is the L1
session's own inbox, `$AUTOOS_RUN_DIR/inbox/l1.md` (`tools/autoos_inbox.py:inbox_path("l1")`, whose reader is what polls it). Work
in the other direction is `inbox`: one record appended to the lane's own inbox — `$AUTOOS_RUN_DIR/inbox/<lane>.md` when a run dir
is set, else `<lane dir>/inbox.md`, the path the append reports — and the live session nudged with the same
`POST /api/session/{id}/prompt` the launcher uses for its first prompt. Only a session that proved itself guarded is woken: a lane
whose recorded canary never denied, or that was never prompted, is refused (`refused: true`, exit 2) while the line stays in the
inbox. A stalled-but-alive lane cleared its canary, so it is nudged, not refused. The append happens whether or not the nudge lands, and the answer says which.
A nudge that lands on a stalled lane is that stall's wake and records the `heartbeat.json` marker `resume` would have left, so the
`resume` after it is a no-op rather than a second prompt (F2, P1).

State lives under `$AUTOOS_OCL2_STATE_DIR` (default `<tmpdir>/autoos-oc-l2/`), one directory per lane with the generated config
(0600 — it names host paths), the scratch dirs, the composed prompt and the lane inbox. **Nothing is merged into
`~/.config/autoos/oc-l1.json`, so the `autoos-oc-l1` watcher never restarts an L2 lane**: recovery is `l2_start` again, which is
also why a `start` on a lane that is already live is refused rather than silently restarted.

`stop` kills the recorded PID's process group (the child is its own session leader, so everything `opencode serve` forked dies
with it) and removes the state file only after proving the PID gone — a zombie answers `kill(pid, 0)`, so death is read from
`/proc/<pid>/stat` (R-coord-10: an orphan `serve` holding the port would make the next start's health poll answer for the wrong
server). A PID whose argv holds no token that IS the lane's binary is never killed; the stop reports `stopped=false, orphan=true`
and keeps the state file.

A manual live check (real binary, real gateway, the host password env — the value never leaves the environment):

```bash
AUTOOS_OCL1_PW='<from the host secret store>' AUTOOS_OPENCODE_BIN="$(command -v opencode)" \
AUTOOS_L1_INBOX="$AUTOOS_RUN_DIR/inbox/l1.md" \
python3 tools/oc_l2.py start --repo /path/to/project --phase p1 --brief /path/to/brief.md
python3 tools/oc_l2.py status --lane l2-project-p1 && python3 tools/oc_l2.py stop --lane l2-project-p1
```

`start` answering `canary.denied=true` with `exit_code: 0` is the proof the lane may run unattended; `exit_code: 5` means the
guard did not deny, the brief was never posted, and the lane must be stopped and fixed, not supervised anyway.
