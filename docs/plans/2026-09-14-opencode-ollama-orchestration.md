# OpenCode + local Ollama — corrected implementation plan

**Goal:** add OpenCode (keyless + local-Ollama first) to the AutoOS catalog on all
three platforms, reusing the existing Ollama stack. OpenHands, rescue-profile
changes, cross-repo skill changes, and live-server mutations are explicitly
deferred (see §5).

**Why this replaces the 2026-09-14 draft:** that draft is not implementable as
written. Summary verdict:

| # | Draft claim | What the repo actually shows | Verdict |
|---|---|---|---|
| 1 | Delete `oterm` from 3 catalogs + scripts/tests | `grep -Rin oterm catalog lib tests setup.ps1 setup.sh web` → zero hits | Phantom deletion. Drop it. |
| 2 | Add `rescue` profile as "emergency AI recovery environment" | No `rescue` key exists in any catalog; the approved design lives in `docs/plans/2026-09-11-installer-usb-and-rescue-profile.md` where `rescue` = disk/filesystem/hardware/Windows repair + both AI CLIs | Do not redefine `rescue` here. Coordinate with that plan instead of forking it. |
| 3 | Linux installer `https://dev.meta.ai/cli/install-opencode.sh` | No such vendor URL is established; OpenCode's own install is `https://opencode.ai/install`. Rule 2 requires the vendor's own URL. | Blocker. Phase 0 must verify the URL; never ship the Meta URL unverified. |
| 4 | Catalog `installer:` field with a raw `curl … \| bash` string | Schema has no `installer` field. `script` provider means `package` maps to a function (`install_opencode` / `Install-AutoOSOpencode`), exactly like `package: "ollama"` → `install_ollama`. | Blocker. Follow the existing `script`-provider pattern. |
| 5 | Config at `%APPDATA%\opencode\config.json` **and** `~/.config/opencode/opencode.json`; providers `opencode/free`, `meta` @ `https://api.meta.ai/v1`, model `muse-spark-1.3` | None of these paths/IDs/URLs are established in-repo; the `~/.config/opencode/opencode.json` single-path layout is the one to verify in Phase 0 | Blocker. Verify before coding; write to one verified path via read-modify-write. |
| 6 | Keys from `secrets\api_keys.conf`, injected into configs + live REST API | That path is Windows-only, not in `.gitignore` (which ignores `.env`, `*.vault.yml`, `inventory.yml`), and breaks the existing `prompt` → `answers` → `autoos.config.json` (gitignored) convention | Blocker under Hard Rule 1. Use optional `prompt`s with blank defaults; keyless must work. |
| 7 | `openhands` (`@openhands/agent-canvas`) in `rescue` | Rescue is offline-tolerant by design (see prior plan §B6); a Node + network + API-key canvas belongs in `workstation`/`ai-coding` at most, and its package/API surface is unverified | Defer. Phase 0 spike or drop. |
| 8 | Rewrite `skills/unattended-orchestration/*` in the sibling `agent-skills` repo + worktree at an absolute `c:\Users\…` path | Different repo (out of scope for AutoOS), plus an absolute username path in a tracked file (fails the §7 checklist) | Move to a separate plan owned by that repo; never use username paths. |
| 9 | `POST /api/profiles/{name}`, `/api/agent-profiles/{name}`, `/api/settings/mcp/{key}` against `http://127.0.0.1:8000` with a key file | Endpoints and key path unverified; live mutation is not `--dry-run`-able, not idempotent, has no backup/confirm (Hard Rule 3/5) | Manual follow-up only, after endpoint discovery. Not part of provisioning. |
| 10 | Verify by asserting on `%APPDATA%…\config.json` contents + live API + other-repo `pytest` | Test rules: assert on the *planned command* and pure functions, never system state; no test installs software; e2e is `--dry-run` only | Rewrite verification per §6. |

## 1. Phase 0 — discovery spike (do first, ~30 min, no catalog edits)

1. Confirm npm package + verify command: `npm view opencode-ai version` and the
   binary the package installs (`opencode --version`). If the package name
   differs, the catalog entry changes — do not guess.
2. Confirm the Linux/macOS installer URL from the vendor's own docs
   (expected `https://opencode.ai/install`, `curl -fsSL … | bash`). Record the
   exact URL actually documented; that URL — and only that URL — goes into
   `install_opencode`.
3. Confirm the real user-config path on all three OSes (expected single file
   under the user profile, e.g. `~/.config/opencode/opencode.json`; verify
   where it resolves on Windows rather than assuming `%APPDATA%`). Record the
   JSON shape for `provider` / `model` / `mcp` keys.
4. Confirm which models exist without credentials. Do **not** hardcode
   `opencode/free`, `muse-spark-1.3`, `qwen3:30b`, `hermes3:8b` as facts:
   check what `opencode` offers keyless and what `ollama pull` tags actually
   resolve to. Note: a 30B tag is a poor default for rescue-class machines.
5. Confirm OpenHands separately (`npm view @openhands/agent-canvas version`,
   real binary name, real `arch` limits). If unverified, it stays out of this
   plan entirely.

Exit criteria: §2–§3 contain only verified names, URLs, paths, and model IDs.
Anything still unverified is marked `UNVERIFIED — not shipping`.

## 2. Catalog changes (data only, all three files)

- Add **one** component, `opencode`, to the existing `ai-coding` category in
  `catalog/windows.json`, `catalog/linux.json`, `catalog/macos.json`.
- Windows entry uses the existing `npm` provider (same shape as `claude-code`):
  `provider: npm`, `package` = verified OpenCode npm package,
  `requires: ["nodejs"]`, `verify` = verified `--version` command,
  `postInstall` = `Set-AutoOSOpenCodeConfig`, `homepage` = vendor docs URL.
- Linux/macOS entries use the existing `script` provider (same shape as
  `ollama`): `package: "opencode"`, `requires` = downloader prerequisite
  (`download-tools` on Linux), `postInstall` = `setup_opencode_config`.
- `description` ≤ 70 chars (schema convention, menu width), e.g.
  `AI coding agent with local-model support` (38 chars).
- `profiles: ["workstation", "ai-coding"]` only. **Do not** add any profile to
  `rescue`: `rescue` does not exist yet and is owned by the USB/rescue plan.
  If that plan later wants a keyless AI CLI in rescue, it can opt in to
  `opencode` itself once keyless operation is proven.
- No `installer`, `source`, `prompt`, or model-list fields unless the schema
  already supports them. If a new API key prompt is genuinely needed, add it
  to each catalog's `prompts` map following the `context7_api_key` pattern
  (optional, blank default, `question` + `help`) — never a new secrets file.

## 3. Installer + config code (code follows the existing patterns)

### Windows (`lib/windows/AutoOS.Install.psm1`)

- Add `Install-AutoOSOpencode` and dispatch it from
  `Invoke-AutoOSScriptProvider` **only if** the catalog uses
  `provider: script` on Windows; with `provider: npm` no new installer
  function is needed (the `npm` branch already handles it).
- Add `Set-AutoOSOpenCodeConfig` and export it from `Export-ModuleMember`
  alongside the other `Set-*`/`Install-*` functions:
  - Honor `$script:DryRun` (log `would write …`, change nothing).
  - Back up any existing user config to `<file>.autoos-backup-<timestamp>`
    first (Hard Rule 5; this extension is already gitignored).
  - Read-modify-write the JSON (never wholesale overwrite — Hard Rule 4);
    preserve unknown user keys.
  - Write **only** the verified Ollama provider entry (expected base URL
    `http://127.0.0.1:11434/v1` with verified local tags). Reuse the models
    the existing `ollama_models` prompt already pulls (default today is
    `nomic-embed-text`); do not invent a second model list.
  - Keyed providers are configured **only** when the corresponding answer is
    non-empty; missing keys are not an error — the tool must work keyless
    against local Ollama.
  - All output through `Write-AutoOSLine` (never `Write-Host`), so
    `--no-color`/`--json` and the log file keep working.
  - Never log key material.

### Linux/macOS (`lib/linux/install.sh`)

- Mirror with `install_opencode` (vendor URL only, verified in Phase 0) and
  `setup_opencode_config`, following `install_ollama`/`setup_ollama_models`:
  `set -euo pipefail`, `local`s, quoted expansions, `AUTOOS_SUDO` for
  privilege (no bare `sudo`), `AUTOOS_DRY_RUN` early-return with `ui_muted`,
  `shellcheck` clean.
- Config step uses `python3` for JSON read-modify-write (the repo's allowed
  JSON tool), same backup + keyless-first semantics as Windows.

## 4. Explicit non-goals (moved out, not silently dropped)

- **`oterm` removal** — nothing to remove; no action.
- **`rescue` profile** — owned by
  `docs/plans/2026-09-11-installer-usb-and-rescue-profile.md`. This plan adds
  no profile and edits no rescue component.
- **OpenHands / Agent Canvas** — needs its own Phase-0 verification (package,
  binary, `arch` limits incl. Pi `arm64`, homepage, `verify`). Even then it
  targets `workstation`/`ai-coding`, never `rescue`.
- **Sibling-repo skill changes** (`AgentAdapters.psm1`, `cao/config.py`,
  `routing.py`, `handoff.config.json`) and any worktree experiment — separate
  plan in that repo, with relative paths only.
- **Live `http://127.0.0.1:8000` mutations** — manual follow-up after real
  endpoint discovery (`GET /api/...` first, backup before `POST`), never part
  of `setup.*` execution and never asserted in tests.

## 5. Verification (per AGENTS.md §5/§7, no system-state tests)

1. `bash tests/run-tests.sh` and `pwsh tests/run-tests.ps1` both pass
   (schema validation covers the new entries automatically; no new framework).
2. `shellcheck` clean on touched `.sh`; `Invoke-ScriptAnalyzer` clean on
   touched `.psm1` — a skip is reported as a skip, never as a pass.
3. `--dry-run` on each platform profile; read the plan output (opencode
   appears under `workstation`/`ai-coding`, absent from anything else).
4. Double-run: second run reports `skipped`, not `installed`.
5. Checklist: no secret/token/hostname/username path in any tracked file, no
   binary committed, no absolute local path, `README.md` touched only if a
   flag or entry point changed.
