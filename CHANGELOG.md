# Changelog

All notable changes to AutoOS are recorded here, newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed — one source for the gateway model list (`catalog/ide-models.json`)

- **The tier/model list was hand-kept in about eight places and had drifted**: the 1M
  tier was `1000000` in `opencode.jsonc`, `1048576` in the Zed writers, the OpenHands
  tier profiles and the installers, `128000` in `configuration/openhands/config.toml`,
  with three different output budgets. `catalog/ide-models.json` now owns ids, display
  names, windows and per-surface membership (opencode, Zed, OpenHands); leg order stays
  in `combos.json`. The 1M tier is `1000000` everywhere.
- **Both Zed writers and both OpenCode user-config writers read the catalog at run time**
  instead of carrying literals. Zed's litellm list gains `t4-rag`, the V1 OpenCode config
  gains the four combos it had missed, and every surface shows the same names (the old
  "no training" label on `t1-orchestrator-clean` was wrong: its only leg trains).
- **`tools/sync-ide-models.py`** regenerates the static copies — the `opencode.jsonc`
  model blocks (between `// AUTOOS-MANAGED` markers) and the token windows in the
  OpenHands tier spec and `config.toml` — and checks OpenHands membership both ways.
  `--check` exits 1 with a diff; both suites run it.
- **The OpenHands default LLM uses its own model's windows** (t1 from the catalog, or the
  keyless fallback's from `llm-models.json`) instead of one `1048576` for all three —
  the local 32k Ollama model was told it had a 1M window.

### Fixed — the OpenCode user config converges in one run

- **`setup_opencode_config` needed two runs**: the providers merge wrote `—` escapes
  where the agent harness wrote UTF-8, so the second run always "merged" again and took
  a fresh backup. The merge now writes only when the document changes, in UTF-8.

### Fixed — agy and the Antigravity app no longer read as available when they are not

- **A signed-out `agy` passed `list` as installed** and a headless run then waited 60 s on
  an OAuth prompt before failing. The spawner probes `agy models` (under a second either
  way): `list` shows `signed-out` with the reason, `run --client agy` refuses with exit 3,
  and the MCP `list_clients` carries `usable` + `reason`.
- **The Antigravity app on Linux counted as installed when it was skipped**: a blank
  `.deb` URL returned 0. It is `failed` now, with the download page as the next step.
- **agy's vendor installer edits shell profiles** (`agy install` appends a PATH line to
  `~/.zshrc`, `~/.zprofile`, `~/.profile`); `install_agy` backs them up first.
- **`--client gemini` exited 55 in any folder gemini had not been told to trust.** The
  spawner passes `--skip-trust` (this session only), so the approval mode is kept too.

### Fixed — tier orchestration on opencode v2 (measured live 2026-09-24)

- **Nested tiers could not run.** `opencode.jsonc` set a top-level `subagent_depth`,
  which opencode 2.0.16 drops as an unsupported legacy setting; the depth stayed 1 and
  t2-worker answered "Subagent depth limit reached (1)". It is `experimental.subagent_depth`
  now, and both suites gate it.
- **The read-only reviewer could write and commit.** Its `bash` rule matched nothing (v2
  calls it `shell`) and serena's write tools bypassed the `edit` deny. t3-reviewer now
  denies `serena_*` except a read-only list, the omnigraph write tools and browser code,
  and carries every `agent-harness.json` shell fence (no commit, push, checkout, reset).
  Both suites assert the fence shape and that no `bash` rule survives.
- **LiteLLM did not install on Ubuntu 24.04.** `pip install --user` fails under PEP 668
  and the step still reported success. The installer uses `uv tool install` (then pipx),
  returns failure when both fail, the catalog entry requires `uv`, and a present
  `litellm` is detected so a second run skips.
- **`apply.*` registered `REPLACE_WITH_*` placeholders as provider keys**, which then
  shadowed the real key as "already registered". Placeholders now count as no key.
- **`start-stack.sh openhands` never ran the tier-profile sync**: the repo root resolved
  one directory too high.

### Added — one spawner for every agent client

- `tools/autoos-agent.py --client opencode|claude|qwen|gemini|codex|agy|qoder`: one
  command for every agent CLI. qwen, gemini and codex go through `omniroute run`. claude,
  agy and qoder run on their own login, and `list` prints the matrix.
- **Task cards** (ADR 0006): without `--tier`, `--card role=…,privacy=…` resolves to a
  combo through one tested `select_combo`. `privacy=sensitive` with `ctx=1m` is refused
  unless `--allow-training`, and the qoder promo takes public work only.
- **Depth budget** for every client (`AUTOOS_AGENT_DEPTH`/`MAX_DEPTH`, exit 4 past it).
- **`--lean`**: no serena, playwright or context7 (1406 → 678 MB peak per opencode run).
  The overlay needs opencode 2.x's `disabled: true`; `enabled: false` is dropped.
- **`tools/autoos_agent_mcp.py`**: the spawner as an MCP server (spawn, status, result,
  cancel), registered for Claude Code, opencode, OpenHands and Zed.
- Catalog: `qwen-code`, `gemini-cli`, `codex` (npm). The `qoder` client uses the
  existing script-provider `qodercli` entry and its `Set-AutoOSQoderMcp` / `setup_qoder_mcp`.

### Added — one-command tier agents

- `tools/autoos-agent.py` spawns one tier agent with its own model (a bare
  `opencode run --agent` uses the default model), standalone (so the current key is
  used), with stdin closed (a piped stdin hangs `opencode run`). `--isolate` runs it in a
  private clone with writes outside denied; `--free` runs every tier on opencode's free
  model with no key; `--dry-run` prints the plan.

### Added — router: t2-orchestrator combo, opus curation, free-only mirrors

- **New `t2-orchestrator` combo** (200k, small-scope orchestration):
  `antigravity/claude-opus-4-6-thinking` (OAuth free, ack-probed 3.4s) →
  `cc/claude-opus-4-6` (subscription overflow) →
  `openrouter/deepseek/deepseek-v4.1-flash` (cheap smart tail). Wired through
  all four client surfaces (opencode, OpenHands profiles, Zed writers).
- **New pinned `opus-4-6` combo** (agy thinking → cc opus). `t1-orchestrator`
  stays spark-only by design; `t2-worker` gained the ack-probed
  `antigravity/gemini-3.7-flash-medium` leg beside the gemini head.
- **Free-only LiteLLM groups** (`t1/t2/t3-*-free-only`): hand-curated mirrors
  minus gateway-only legs, no fallbacks entries (zero spend fails loudly).
  A new suite test pins mirror-equality and the declared-drop set.
- **CLI routing automation**: `Install-AutoOSOmniRouteRouting` /
  `route_detected_clis_to_gateway` (omniroute postInstall) routes
  pre-installed Claude Code + Qwen Code at the gateway; `Set-AutoOSApiKeyEnv`
  exports `OMNIROUTE_API_KEY` / `QODER_PERSONAL_ACCESS_TOKEN` absent-only;
  `Install-AutoOSQoderCli` fixes the "qodercli not recognized" dashboard
  error (PATH append + PAT); `devin-cli` catalog entries (winget +
  vendor script). Environment writes broadcast `WM_SETTINGCHANGE` so new
  terminals see them without sign-out.

### Removed — router: credit combos, retired ids, Z.AI

- **`tier2-credit` / `tier3-credit` combos deleted** (breaker fast-skip +
  cooldowns demote exhausted balances automatically).
- **Retired `tier1/2/3`, `rag`, `*-paid`, `*-credit` ids deleted from the
  live gateway store**; both suites assert the exact combo list plus an
  explicit retired-ids regression test, and `apply.*` only ever creates.
- **Z.AI dropped**: key unfunded (429-insufficient-balance), connection
  removed, registry/example/docs rows reverted.

### Added — Qoder desktop, and Qoder's MCP servers

- **`qoder-desktop` joins the catalog** on all three platforms: `winget
  Alibaba.Qoder` on Windows and `manual` on Linux/macOS, where the vendor ships
  only `.deb`/`.rpm`/`.dmg` and no Homebrew cask exists (checked against the
  live cask API, not assumed). The entry carries no `verify`: the app puts no
  confirmed CLI on PATH, and a verify that fails after a successful install is
  worse than none.
- **Qoder's MCP servers are wired** by the `Set-AutoOSQoderMcp` /
  `setup_qoder_mcp` postInstall, attached to the existing `qodercli` component
  (whose installer is the `Install-AutoOSQoderCli` entry above). Registration
  goes through `qodercli mcp add-json` at user scope — never a hand-edit of
  `~/.qoder/settings.json`, for the same reason Claude Code's registration goes
  through `claude mcp add`. Pins resolve from `catalog/agent-harness.json` at
  runtime. `omnigraph` is deliberately absent: its graph is per-repository, so
  a machine-global entry would pin the wrong graph for every other repo.

### Fixed — Qoder detection, Qoder on Windows arm64, and the worktree memory pin

- **`detect.sh` had no `qodercli` case.** A `script`-provider component with no
  detection heuristic is never recognised as present, so an already-installed
  Qoder was reinstalled on every run instead of reported `skipped`
  (AGENTS.md §4). It now tests `has_bin qodercli`.
- **`catalog/windows.json` offered the Qoder CLI on arm64.** The vendor's own
  installation docs state Windows arm64 is not currently supported, so `arch`
  is `["x64"]` and the component is hidden there rather than shown and failing
  (AGENTS.md §3). The vendor manifest does ship linux/darwin arm64 builds, so
  those two catalogs are untouched.
- **Qoder is the one client AutoOS wires for tools but not for model routing**,
  and that is a vendor constraint, not an omission. Its Custom Models accept a
  curated provider list only (Alibaba Cloud Model Studio, DeepSeek, Z.ai, Kimi,
  MiniMax, Xiaomi MIMO) with no arbitrary OpenAI-compatible base URL, and
  `~/.qoder/.models/<uid>/customs` is encrypted — so `:20128` is not addressable
  and nothing in `lib/` could write a provider even if it were. Recorded in
  `docs/models.md` and `docs/catalog.md` so the next reader does not re-derive
  it, and so nobody names a function `route_qoder_to_gateway`.
- **`trust_worktree.py` pinned the wrong omnigraph graph.** It derived
  `OMNIGRAPH_GRAPH_ID` from the checkout folder's case (`AutoOS`), but the cluster
  graph is `autoos` — so every unattended lane wrote its memory to a graph that
  does not exist, silently, which is the wrong-graph failure `CLAUDE.md` opens
  with. It now reads the pin the repo already ships in `.mcp.json` and falls back
  to the folder name only when there is none. A worktree provisioned before this
  fix keeps its bad pin until the `.env` line is removed: the helper writes the key
  only when absent, it does not correct one.

### Changed — documentation restructure

- **README.md is a front page again** (391 → 130 lines): the setup commands,
  usage, ONE screenshot and a Documentation table. The manual moved into `docs/`:
  [architecture](docs/architecture.md) (why it works this way, the layout table),
  [getting started](docs/getting-started.md) (the terminal view),
  [the catalog](docs/catalog.md) (software on offer),
  [model routing](docs/models.md) (fresh OS → working agent stack) and a new
  [OpenHands Agent Canvas](docs/openhands.md) page. `docs/README.md` and the
  README table list every page; `tests/check-links.py` and the docs-index test
  keep them honest.

### Changed — routing: free-first with a fast-skip

- **The zen free contributor promo is the first leg again** in `tier1` /
  `spark-1.3-contributor`, backed by `providerBreaker.apikey.failureThreshold`
  12 → 2 (`apply.*` sets it on both platforms). A 403 is a permanent-class
  error, so at 12 the dead promo was retried on every request; at 2 the
  connection is skipped for `resetTimeoutMs` (30 s) after two failures.
  Measured: 5 spark requests → only 3 zen attempts, the skipped ones faster,
  all served by the paid contributor leg. The same threshold makes every
  failing free leg hop fast instead of being retried ~12 times.

### Fixed — clients silently falling back to defaults

- **`opencode.jsonc` was invalid JSON**: a hand-added OpenRouter provider block
  was missing its closing brace, so every client loaded defaults while the
  suites' text-based assertions stayed green. Repaired, and both suites now
  assert the file parses. The added provider is kept — it is the direct
  effort-ladder surface (`openrouter/muse-spark-1.3-contributor`).
- **A direct-provider tier takes its own key.** The OpenHands profile
  `openrouter-muse-spark-1.3-contributor` declares `"gateway": "openrouter"`;
  both installers and `tools/sync-openhands-profiles.py` resolve a key per
  gateway, so it receives the OpenRouter key and its own base URL, never the
  gateway client key. Both suites assert it.

### Added — OpenHands Agent Canvas

- **Vendored OpenHands profiles** in `openhands/`: 21 LLM profiles projected from
  `catalog/llm-models.json`, and 11 agent profiles forming a three-level hierarchy
  (orchestrator → sub-orchestrator → worker, each with a free OpenRouter variant, plus
  Claude Code and Gemini CLI over ACP). Both installers copy them into `~/.openhands`.
  `tests/check-vendored.py` and two suite cases fail on any drift from the catalog.
- **The Ollama address is chosen per host.** `OLLAMA_BASE_URL` wins when set. Otherwise
  `host.docker.internal` is used when Ollama answers there. Otherwise the catalog's
  `127.0.0.1` is kept. A containerised OpenHands and a host-run `agent-canvas` on native
  Linux both reach Ollama now.
- [docs/openhands.md](docs/openhands.md) on installing, configuring and starting `agent-canvas`.
- [ADR 0005](docs/decisions/0005-openai-agents-api-not-the-backbone.md) (accepted) and
  [the survey](docs/research/2026-09-18-openai-agents-api.md) behind it: the OpenAI Agents
  API is not the orchestration backbone, and OpenAI joins as one reviewer pool.

### Fixed — OpenHands setup

- **OpenHands settings 500 after agent-canvas 1.20 writes schema 6.** `agent-canvas`
  writes `agent_settings.schema_version: 6` plus an `enabled` key on every MCP server,
  while the `docker.openhands.dev/openhands/openhands:latest` image supports version 4
  and rejects `enabled` with `extra_forbidden` — every `/api` settings route 500s with
  `AgentSettings schema_version 6 is newer than supported version 4`. The old
  `start-stack` guard checked top-level `schema_version` (3 on broken files too) and
  never fired. `start-stack.ps1`/`.sh` now repair in place with a timestamped backup:
  clamp `agent_settings.schema_version` down to 4 (older payloads keep theirs, so the
  image's own migrations still run) and strip the `enabled` keys; only unparseable files
  move aside. Both installers also write schema 4 instead of 5 and strip inherited
  `enabled` keys. Suite pins the repair shape in both harnesses.

- **Windows OpenHands setup failed under `Set-StrictMode`.** The embedded Python was an
  expanding here-string, so every `$` in it was evaluated by PowerShell. It is now a literal
  here-string that reads the catalog from disk. One suite case parses it; another runs it
  against a temp home.
- Non-thinking models (Ollama, `deepseek-chat`) get explicit thinking opt-outs. They used to
  inherit `reasoning_effort: high`, which Ollama rejects.

### Added — installer / rescue USB

- **Your own image:** `--image custom-local --image-path <file>` or
  `--image custom-url --image-url <url> --image-sha256 <hex>`, with a required
  `--write-mode hybrid|raw`. See [usb-creator.md](docs/usb-creator.md#your-own-image).
- **The Windows read-back reads the stick itself**, not the file cache
  (`FILE_FLAG_NO_BUFFERING`), matching Linux's `dd iflag=direct`.
- **Verified mirrors** for Debian netinst and Fedora Workstation.
- **CI runs both suites with no route to the internet**, so a test can no longer
  install or download anything for real.

### Fixed — installer / rescue USB

- **Fedora Workstation never resolved**: its filename pattern, checksum filename,
  directory depth and bare-integer version folders no longer matched upstream, and its
  index went through a geo-redirector that could land on a broken mirror. It now resolves
  from `dl.fedoraproject.org` (new optional catalog field `leaf` for the deep ISO folder).
- **`--config` replays failed on Git Bash for Windows** with "Unknown profile" — the
  config and state loaders kept a trailing carriage return from native python3.

- **`--create-usb` / `-CreateUsb` builds a bootable stick end to end**: resolve the image from
  its catalog entry (never a pinned version), fetch the vendor's GPG-signed checksum manifest,
  fetch the image — from a catalog mirror when one is listed — and verify it, re-check the
  target device immediately before the first destructive step, write with the chosen engine,
  flush, then **read every file back and compare it with the image** before saying
  `Ready to boot`. A real write needs `--wipe-target-disk` / `-WipeTargetDisk`; `--dry-run`
  shows the plan and touches nothing. See [usb-creator.md](docs/usb-creator.md) and
  [ADR 0004](docs/decisions/0004-ventoy-and-wsl-not-rufus.md).
- **A terminal chooser** (image → kind → engine → device) when `--create-usb` is run
  interactively without every flag, and a *Create installer USB* entry in the profile menu.
  The confirmation names the device, its model, its size and that all data on it will be
  destroyed; a non-interactive run never consents on the user's behalf.
- **`rescue` and `local-ai` profiles**, Claude Code and `agy` as independent entries, an `ai`
  dispatcher with a local backend, offline `.deb` cache and pip wheelhouse for the stick.
- Catalog: `images.json` (version-free image entries with checksum/signature/key and an
  optional `mirrors` list), `engines.json` (five write engines).

### Fixed — `claude-autostart`

- **It recorded no sessions on Windows.** Discovery parsed process command lines,
  but `Win32_Process` exposes no working directory, so every session was dropped
  — measured 0 of 6 live sessions on a developer machine. Discovery now reads
  `~/.claude/projects/*/*.jsonl`, whose records carry `cwd` and `sessionId`
  directly. See [ADR 0001](docs/decisions/0001-discover-claude-sessions-from-transcripts.md).
- **`claude-sessions.ps1 hook-start` was dead code.** It built a session list in a
  local variable and then called a function that ignored the argument. The hook
  layer is removed entirely — `SessionEnd` deleted the very record restore
  depends on. See [ADR 0003](docs/decisions/0003-no-claude-code-lifecycle-hooks.md).
  AutoOS no longer writes to `~/.claude/settings.json` at all.
- **Restore produced processes no human could reach.** A hidden Scheduled Task
  spawning `claude.cmd`, and a bare `nohup claude &` on Linux, both start an
  interactive TUI with nowhere to draw. Restore now targets a real terminal host
  (herdr → tmux on Linux, Windows Terminal on Windows) and reports `skipped` with
  a reason when none is available. See
  [ADR 0002](docs/decisions/0002-restored-sessions-need-a-visible-terminal.md).
- **The snapshot timer stopped scheduling after the first missed window.**
  `OnBootSec=` + `OnUnitActiveSec=` is monotonic-only; it is now
  `OnCalendar=*:0/<interval>` with `Persistent=true`, the trap the upstream unit
  file documents. The Windows task's repetition was grafted onto another
  trigger's `Repetition` object and effectively ran daily; it is a one-shot
  trigger with a real repetition interval now.
- **The restore unit could not be diagnosed and was killed half-done.** Added
  `StandardOutput=journal`, `StandardError=journal` and `TimeoutStartSec=900`, and
  removed the `After=default.target` / `WantedBy=default.target` ordering cycle.
- **One session could be restored twice.** A session that left transcripts under
  two project slugs (a scratchpad directory beside the repo does this) produced
  two records with the same id, and two terminals fighting over one conversation.
- **The installer was not idempotent.** It re-registered the Scheduled Task and
  rewrote `settings.json` on every run, leaving a timestamped backup behind each
  time, and always reported success. A second run now reports `skipped`.
- **It claimed to work on macOS.** It was listed in `catalog/macos.json`, and
  `setup.sh` routes macOS through `lib/linux/install.sh` — which writes *systemd*
  units to a machine that has no systemd, after which detection reports it
  installed. Removed from the macOS catalog until a launchd implementation exists.
- `loginctl enable-linger` is announced before it runs, and when it cannot be
  done the exact command is printed rather than the failure being swallowed.
- `claude_autostart.enabled` is a real pause switch (keeps the supervisor and the
  snapshots, restores nothing). It had been in the shipped schema and the web card
  from the start with no code reading it.

### Fixed — web UI

- **Settings never reached the code that reads them.** The card wrote
  `resume_prompt_mode` / `interval_minutes` while the scripts read `resume_mode` /
  `snapshot_interval_mins`, and saving replaced the config object wholesale,
  discarding `fallback`, `fallback_cwd` and `fallback_name`. One schema now
  (`autoos.config.example.json` is its single home, read by both the PowerShell
  and the Python side), the save merges, and a test compares the keys.
- **The configuration form offered settings the platform never asks.** It
  hardcoded six fields, but `git_user_name`, `git_user_email`, `ollama_models` and
  `antigravity_url` are Linux-only prompts — on Windows those boxes wrote answers
  no installer reads. The form is rendered from the catalog's own `prompts` now.
- **The form was seeded from the example file.** With no `autoos.config.json`,
  `/api/config` returned `autoos.config.example.json`, putting `Your Name` and
  `you@example.com` in the identity fields where they looked answered and were
  saved as though they were real. The seed comes from the machine
  (`git config --global`); catalog defaults render as placeholders rather than
  values; and an empty field is saved as *unanswered* rather than as `""`, which
  would shadow the default (`herdr_source: ""` reaches the installer as a real
  answer and fails its source check).
- **Accessibility regression.** The card-chooser stylesheet replaced
  `@media(prefers-reduced-motion:reduce){.bar.indeterminate>div{animation:none}}`;
  restored, and the new transitions and `scrollIntoView` now honour it too.
- `GET /api/claude/sessions` ran a snapshot, mutating state on every page load.
  It is read-only; `POST /api/claude/snapshot` is the only writer.
- Every inline `onclick` is gone. They forced a value to be HTML-escaped into a
  JavaScript string context, which is the wrong escaper; the page uses one
  delegated listener instead.

### Fixed — elsewhere

- **The status screen showed a date two months in the future.** A bare
  `[datetime]::TryParse` reads an ISO-8601 round-trip string under the current
  culture, so `2026-09-11T12:04` came back as `2026-11-09` — next to session rows
  dated correctly.
- The post-run report told the user to "run claude" to use a background service,
  then, after the misleading `verify` was removed, that it had "no launcher found
  yet". A component can declare `"launcher": "none"` now, and the report says it
  runs in the background instead of hunting for an executable.

### Added

- `docs/decisions/` — architecture decision records, starting with the three
  above — and this changelog.
- **Configure and System sections in the browser UI.** Overview had grown to six
  cards covering three unrelated jobs: what this machine is, what to install, and
  how the installed things are configured. Overview is now only *choose what to
  install*; see the layout section below for where the rest went.
- `setup.ps1 -ClaudeSessions <action>` and `./setup.sh --claude-sessions <action>`
  — status, snapshot, restore or configure, from the entry point people already
  use, rendered through the shared UI layer so `--no-color`, non-TTY output and
  the run log keep working. The status screen leads with the three facts that
  decide whether sessions come back: the supervisor, the terminal host, and how
  long ago the snapshot was taken.
- `configure` presents each setting as a radio menu rather than a free-text
  prompt, since every one of them is a closed set and a typo used to be accepted
  silently and then ignored by the code that read it.
- Behavioural tests, in both suites, driven from a fixture transcript tree built
  at test time: discovery, the liveness window, the `max_sessions` cap,
  deduplication, anti-clobber, the restore plan, the `--rc` override, terminal-host
  refusal, the disabled switch, config merge-not-replace, and the schema
  agreements between the web UI, the catalogs and the shipped example.

### Changed

- **Dry run is off by default.** A run that installs nothing, from a button that
  says *Install selected*, is a surprise in the wrong direction; the confirmation
  already lists every package before anything happens.
- The Claude autostart card leads with state — service, last snapshot age, tracked
  count — rather than with settings selects, and opens only when the component is
  installed.
- Choosing a card in the chooser expands it: a preset that reveals a folded card
  has not really revealed it, since the controls it was chosen for stay hidden.
  The view presets carry a pressed state, so the active view is visible.

### Changed — browser UI layout

- **Navigation is a dropdown in the header**, not a strip that grew a tab each
  time the page gained a job. It is a real menu: arrow keys walk it, Escape
  closes it, a click elsewhere closes it, and the current section is checked.
  Panels are `role="region"` now — with no tablist left, `role="tabpanel"` was
  describing something that no longer existed.
- **The header carries the machine and nothing else about it.** The environment
  pill, the installed pill and the detected-system facts all described the
  machine; they are on the System tab, which is what that tab is for.
- **System and Installed are one section**, last in the menu: "what is this
  machine" and "what is already on it" are the same question asked twice.
- **The theme is one button** showing the theme it would switch to, instead of a
  three-way Auto/Light/Dark group.
- **The page has its own favicon**, inlined as a data URI. The local server has
  no asset route, so every page load was logging a 403 for `/favicon.ico`.
- **Profiles are a row of square chips plus one full-width detail.** Growing the
  selected card inside the same flex row capped how wide it could get and left
  the others stretched to its height. Each profile has an icon and its component
  count on the chip.
- **The component list is compact by default** — icon, name, one line — with
  **Details** adding the provider, package, platform and dependency chips.
  Already-installed components sort last within their category, and the grid
  fits more per row (290px → 215px minimum).
- **Components carry their real icon**, the application's own favicon fetched by
  the homepage domain the catalog already holds. Drawn over a monogram, so an
  offline or blocked request degrades to a letter rather than a broken image.
  Note the tradeoff: the icon service learns which domains are in the catalog.
- **⚡ Install on any component** installs just that one without touching the
  selection. Presses during a run are queued rather than racing it (the server
  rejects a second concurrent run), and progress shows in the header so it is
  visible from whichever section it was started from.
- The install order lists only what will actually be installed; already-present
  components made the plan look longer than the work.

### Added — elsewhere

- `.claude/skills/autoos-install/` — a skill that teaches an agent to drive this
  repository: the pipeline, the real flags on both entry points, how to answer
  prompts non-interactively, and how to add a component to the catalog.
- `setup.ps1 -Installed`, to match `setup.sh --installed`.
  `docs/getting-started.md` had documented the Windows flag for some time; it
  did not exist.
