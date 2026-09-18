# Changelog

All notable changes to AutoOS are recorded here, newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
- README section on installing, configuring and starting `agent-canvas`.
- [ADR 0005](docs/decisions/0005-openai-agents-api-not-the-backbone.md) (proposed) and
  [the survey](docs/research/2026-09-18-openai-agents-api.md) behind it: the OpenAI Agents
  API is not the orchestration backbone, and OpenAI joins as one reviewer pool.

### Fixed — OpenHands setup

- **Windows OpenHands setup failed under `Set-StrictMode`.** The embedded Python was an
  expanding here-string, so every `$` in it was evaluated by PowerShell. It is now a literal
  here-string that reads the catalog from disk. One suite case parses it; another runs it
  against a temp home.
- Non-thinking models (Ollama, `deepseek-chat`) get explicit thinking opt-outs. They used to
  inherit `reasoning_effort: high`, which Ollama rejects.

### Added — installer / rescue USB

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
