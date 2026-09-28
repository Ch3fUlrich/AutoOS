# The catalog

`catalog/windows.json`, `catalog/linux.json` and `catalog/macos.json` describe
**what** can be installed. `lib/` describes **how**.

The same folder holds six catalogs of other types: `images.json` (bootable
images for the [USB creator](usb-creator.md)), `engines.json` (USB write engines),
`ai-registry.json` (with `ai-registry.schema.json`), `ide-models.json` (IDE model
metadata), and `agent-harness.json` (agent harness configuration). `--check-catalog`
validates every file in `catalog/`, dispatched by its top-level shape; an
unrecognised shape fails.

Adding software must never require touching `setup.ps1` or `setup.sh`. If you
find yourself editing an entry point to add a package, the schema is missing
something — extend the schema instead.

## Software on offer

Run `--list` for the current set. The headline items:

| Area | Includes |
|---|---|
| Terminal | Windows Terminal, PowerShell 7, Oh My Posh, zsh + Powerlevel10k, Nerd Fonts |
| Coding & AI | Claude Code CLI, OpenCode CLI, Qoder CLI, Qoder, Claude autostart (reopens your sessions after a reboot), Herdr sessions (Linux-only, opt-in; snapshots and restores Herdr panes across a reboot instead — mutually exclusive with Claude autostart), Claude Desktop, Antigravity, Zed, VS Code, Docker, Herdr, Node.js, OmniRoute gateway, LiteLLM fallback router |
| Input | Handy — offline speech-to-text, so you can dictate prompts instead of typing them |
| MCP stack | Wires the complete MCP stack (Graphify, Serena, Playwright, Context7, Omnigraph) using AutoOS's vendored skills — no external clone needed. Registers Graphify with Claude Code, Antigravity and Qoder CLI, and approves Omnigraph per-repo — asking for your Omnigraph URL rather than hardcoding one, and naming what is still missing rather than pretending it is wired |
| Desktop (Windows) | Windhawk with the Explorer file-size and taskbar-clock mods, PowerToys |
| Remote | Tailscale, WireGuard, Parsec, OpenSSH |
| Science | Miniconda plus an isolated `suite2p` environment |

Adding software means adding a catalog entry — no code changes. See
[Adding software](#adding-software).

## A component

```jsonc
{
  "id": "claude-code",                   // stable, kebab-case, never reused
  "name": "Claude Code CLI",             // shown in the menu
  "description": "Anthropic's terminal coding agent",  // <= 70 chars
  "provider": "npm",                     // how it gets installed
  "package": "@anthropic-ai/claude-code",// provider-specific identifier
  "verify": "claude --version",          // proves it actually works afterwards
  "requires": ["nodejs"],                // other component ids
  "profiles": ["workstation", "light"],  // which profiles pre-tick it
  "arch": ["x64"],                       // omit to mean "any"
  "cask": true,                          // macOS only: brew install --cask
  "source": "msstore",                   // winget only: alternate source
  "postInstall": "Install-AutoOSPoshTheme", // function in lib/
  "prompt": "omnigraph_url",             // a question to ask before installing
  "notes": "One line shown under the plan entry.",
  "launcher": "none"                     // a service, not a command
}
```

### Fields

| Field | Required | Meaning |
|---|:---:|---|
| `id` | ✅ | Stable identity. Renaming one breaks saved state and `--only`. |
| `name` | ✅ | Menu label |
| `description` | ✅ | One line, 70 characters max — the schema test enforces it |
| `provider` | ✅ | `winget` `choco` `npm` `apt` `snap` `brew` `script` `custom` |
| `package` | ✅ | Provider-specific identifier |
| `verify` | | Command that must exit 0 after install ([why](state-and-undo.md#verification)) |
| `requires` | | Resolved transitively and topologically — never hand-order the catalog |
| `profiles` | | Which profiles pre-tick this; `[]` means "only if chosen by hand" |
| `arch` | | Hides the component on other architectures |
| `cask` | | macOS: use `brew install --cask` |
| `source` | | winget: e.g. `msstore` for a Store product id |
| `postInstall` | | Function name in `lib/` run after a successful install |
| `prompt` | | Key into the catalog's `prompts` object |
| `notes` | | Shown under the plan entry |
| `launcher` | | `none` when the component installs a background service and no command. Without it the post-run report hunts for an executable and tells the user "no launcher found yet", which for a service is a wrong answer rather than a blank one. |
| `tombstone` | | `true` — the component is retired: the id stays known and selecting it installs nothing ([why](#retiring-a-component-tombstone)) |
| `note` | | tombstone only — why it went away; it is printed in the skip line |
| `replaced_by` | | tombstone only — the ids that took its work on; a replayed selection expands to them ([why](#replaced_by-keeping-the-work)) |

### Retiring a component (tombstone)

An `id` is a contract: it lives in saved state files, in `--only` / `-Only` flags
and in whatever a user ticked on their last machine. Deleting the entry turns
every one of those into "Unknown component id", so a retired component is
**marked, not removed**:

```jsonc
{
  "id": "old-thing",
  "name": "Old Thing",
  "description": "Retired — wired by new-thing now.",
  "provider": "custom",
  "package": "old-thing",
  "tombstone": true,
  "note": "wired by new-thing now",
  "replaced_by": ["new-thing"]
}
```

What that changes, on both platforms:

- The id stays accepted everywhere it was accepted before — `--only`,
  `--from-state` / `-FromState` — and it stays *visible* in the menu, which is
  where a reader learns it went away; only the choosing left.
- Selecting it does nothing: the run reports `skipped: retired (<note>)`, asks no
  prompt, runs no post-install step, and never counts it as installed or as
  failed. An old state file replays clean instead of failing.
- Profiles never pre-tick it, and no menu can tick it: a retired row is drawn
  **locked** in the terminal menu — `[-]` / `[=]`, muted, ignored by space, `a`,
  `g`, `i` and the non-interactive fallback alike — and labelled
  `(retired: replaced by <ids>)`, so the row itself answers why choosing it does
  nothing. `--list` / `-ListComponents` still marks the row `(retired)`. A
  selection that does name a retired id is expanded before the plan (below), so
  no path can plan a tombstone without the ids that took its work.
- It is never *detected* as installed either: the `✓` on a retired id would
  answer for a product AutoOS no longer offers, and the cache that report comes
  from is the one the rest of the run reads.
- A retired entry keeps the `provider` it had, and retirement is asked **before**
  the provider is: a retired `manual` component resolves to a plan row reporting
  `skipped: retired`, where the provider guard would have failed the whole run
  over a row that installs nothing — and the plan no longer prints its stale
  vendor link as an "Action required" step.
- `postInstall`, `prompt`, `requires` and `verify` may all be absent — they only
  mean something for something that installs. No other entry may `require` a
  tombstone: that dependency could never be satisfied. The validator rejects it,
  and because a normal run never validates, **resolve refuses it too** — the
  dependent is announced in the plan (`Asks A Question requires retired old-thing
  - it cannot be installed`), asked nothing, and recorded as a failure at execute
  time, so the run exits non-zero instead of installing green without the thing it
  needed. Its own dependents are refused on the same grounds, transitively. A
  tombstone still drags nothing in when it is chosen.
- The browser UI sees the same facts: `--serve` / `-Serve` sends `tombstone`,
  `note` and `replaced_by` with every component, and the page draws a retired id
  **shown, disabled and labelled** `(retired)` rather than hiding it — the row is
  where a reader learns the product went away and what replaced it, which is the
  only reason the catalog still carries the id. It is never pre-ticked by a
  profile, never pulled in as a dependency, never asked about in the questions
  card, and offers neither a Configure button nor a one-click install. A browser
  selection reaches the installer as `--only` / `-Only`, so it is expanded there
  exactly like a replayed state file — the page shows the successors, it does not
  plan them on its own.
- `tombstone` must be the boolean `true` (a truthy string would silently retire a
  live entry), and `note` is rejected on an entry that installs something.

`note` is the retirement reason; `notes` is the ordinary plan-entry line above.
They are different fields on purpose: a live component can have `notes`, and a
tombstone's `note` is what makes its skip line readable.

### `replaced_by`: keeping the work

Marking an id retired solves the "old state file names a deleted component"
problem and leaves a worse one behind. A state file saved **before** the
retirement lists the retired id and none of the ids that inherited its work, and
those ids cannot be added to a file that was written years earlier. Replaying it
used to book the retirement and install nothing: the machine the user is
provisioning simply never gets that work done, and the run reports `skipped`,
which reads like success.

`replaced_by` is the catalog's answer — the ids that took the work on:

```jsonc
{ "id": "agent-skills", "tombstone": true, "replaced_by": ["agent-skill-links", "omnigraph-client"] }
```

Every selection the entry point produces is expanded before the plan, whichever
path made it — `--from-state` / `-FromState`, `--only` / `-Only`, a browser run
(whose payload the server passes in as `--only`), the interactive menu, and the
profile a `-Yes` run takes:

- One muted line announces it: `agent-skills is retired: replaced by
  agent-skill-links, omnigraph-client`. Nothing is substituted silently.
- The retired row **stays** in the plan and still reports `skipped: retired`, so
  what the reader replayed is what the report names, and the successors do the
  installing beside it.
- A successor the selection already lists is not added twice, so replaying a state
  file that a replay wrote is a no-op; `requires` are then resolved as usual.
- A successor this machine does not offer (another architecture, another profile
  of catalog) is left out, and the line names only the ids that really entered the
  plan — an announcement that promises more than the plan delivers would be the
  same defect with better manners.
- A successor that has itself been retired since is expanded in turn, so a second
  retirement of the same work still lands.
- A profile never pre-ticks a tombstone and no menu row can tick one, so a run
  that came from either names no retired id to expand. That is the second guard,
  not the only one: the expansion above runs unconditionally, so a retired id that
  reaches the selection by any route — including one added later — still brings
  its successors with it.

The validators on both platforms reject a `replaced_by` that is empty, that sits
on an entry which is not a tombstone, or that names an id which does not exist or
is itself retired — a successor that installs nothing would replay into another
`skipped` row and lose the work one step later, which is precisely the bug the
field closes.

## Providers

| Provider | Platform | Runs |
|---|---|---|
| `winget` | Windows | `winget install --id <package> --exact --silent` |
| `choco` | Windows | `choco install <package> -y` |
| `apt` | Linux | `apt-get install -y <package>` (space-separated names allowed) |
| `snap` | Linux | `snap install <package>` |
| `brew` | macOS | `brew install [--cask] <package>` — never under sudo |
| `npm` | all | `npm install -g <package>` |
| `script` | all | A named function in `lib/*/install.*` |
| `custom` | all | Nothing; the work happens entirely in `postInstall` |

## Platform availability

There is one catalog per platform, so whether a component exists anywhere else is
implied rather than stated. The browser UI computes it by reading **all three**
catalogs and labels only the exceptions:

| Available on | Label shown |
|---|---|
| all three | *(nothing — silence means everywhere)* |
| two | `not on Windows` / `not on macOS` / `not on Linux` |
| one | `Windows only` / `Linux only` / `macOS only` |

**The same product must carry the same `id` in every catalog it appears in.**
Filing Docker under `docker-desktop` on Windows and `docker` elsewhere made both
entries report themselves as single-platform. A test asserts that the core stack
— Claude Code, Git, Node.js, Docker, Tailscale, Handy, VS Code, Herdr,
agent-skills and the Nerd Font — resolves to all three platforms.

Names may still differ where the product genuinely differs (`Docker Engine` on
Linux, `Docker Desktop` elsewhere); only the id has to match.

When adding something, prefer covering all three. Where a platform genuinely has
no package, leave it out of that catalog and the label explains itself.

## Prompts

Questions are collected **once, before anything is installed**, so an install
never blocks halfway waiting for input.

```jsonc
"prompts": {
  "omnigraph_url": {
    "question": "Omnigraph remote server URL (blank = local Docker stack on :8080)",
    "default": "",
    "help": "Shown above the question, in muted text."
  }
}
```

A prompt can also be pre-answered from the environment, which is how the
[browser UI](web-ui.md) passes answers through to the same code path:

```bash
AUTOOS_ANSWER_OMNIGRAPH_URL=https://omnigraph.example.com ./setup.sh --yes
```

The variable name is `AUTOOS_ANSWER_` + the prompt key upper-cased, with `-`
replaced by `_`.

## Adding software

1. Add the entry to the right catalog.
2. `./setup.sh --check-catalog` (or `-CheckCatalog`) — the schema test covers
   every entry automatically, so a malformed one fails loudly.
3. `./setup.sh --only <your-id> --dry-run` and read the planned command.
4. Run it twice. The second run must report `skipped`.

No new test is needed for a catalog entry.

## Verifying winget ids

winget ids are easy to get subtly wrong (`tailscale.tailscale` vs
`Tailscale.Tailscale`). Check before committing:

```powershell
winget show --id <the.id> --exact --disable-interactivity
```

## The MCP stack

AutoOS provides first-class support for installing and configuring Model Context
Protocol (MCP) servers across **Claude Code** (`~/.claude.json` via CLI),
**Antigravity** (`~/.gemini/config/mcp_config.json` on Linux/macOS and
`%APPDATA%\Antigravity\mcp_config.json` on Windows) and **Qoder CLI**
(`~/.qoder/settings.json`, written through `qodercli mcp add-json` rather than by
hand).

Available standalone MCP components in the catalog:
- `mcp-serena`: Semantic code navigation & symbol search (LSP) via `serena-agent`.
- `mcp-graphify`: Codebase knowledge graph queries via `graphify.serve`.
- `mcp-playwright`: Headless browser automation via `@playwright/mcp`.
- `mcp-context7`: Real-time documentation lookups via `@upstash/context7-mcp`.
- `omnigraph-client`: the machine half of `omnigraph` on Linux and macOS — the
  env file (`~/.autoos-omnigraph.env`, mode 600) holding the server URL and the
  bearer token, the pinned bridge pre-installed into a private npm prefix, and
  the `omnigraph-mcp-autoos` wrapper a user-scope MCP entry calls. It skips with
  a hint when the `omnigraph_url` answer or the `omnigraph_token` key is missing.
  It is also where the MCP *wiring* that names `omnigraph` lives: it approves this
  checkout's project servers, warns about a user-scope `omnigraph` instead of
  writing one, writes Antigravity's `omnigraph` entry (that config has no project
  scope), and names the retired tree's user-scope `homelab` leftover without
  removing it — the operator deferred the homelab MCP switch (2026-09-28), so the
  entry stays until the server-side homelab MCP replaces it. Those four
  steps run even when the URL answer is blank — only the artifacts that carry the
  URL and token wait for it. See `docs/omnigraph.md`
  ("The `omnigraph-client` component").
- `agent-skill-links`: links this checkout's `.agents/skills` into every client's
  skills directory — the table in AGENTS.md section 8 is which directory each
  client reads, and `link_skill_dirs` is the one link rule they all go through.

The retired `agent-skills` component is a **tombstone**: the id stays known so an
old selection or state file still resolves, its step installs nothing and only
names the components that took its work — `agent-skill-links`, `omnigraph-client`
and the four `mcp-*` components, which each register their own server.

| Server | Scope | Configuration & Precedence |
|---|---|---|
| **graphify** | one **user** entry | Cwd-relative (`graphify-out/graph.json`), serving each repo its own graph. Configured in Claude Code, Antigravity and Qoder CLI. |
| **serena** | one **user** entry | Repo-agnostic symbol lookups via LSP. Path chosen at runtime. Configured in Claude Code, Antigravity and Qoder CLI. |
| **playwright** | one **user** entry | Headless browser execution for coding agents. Configured in Claude Code, Antigravity and Qoder CLI. |
| **context7** | one **user** entry | Real-time framework and library docs. Configured in Claude Code, Antigravity and Qoder CLI. |
| **omnigraph** | **project** only | The graph is chosen per repo by `OMNIGRAPH_GRAPH_ID`. A user-scope `omnigraph` silently overrides the per-repo one and answers from the wrong graph. |

Key wiring invariants AutoOS enforces:

- It never creates a user-scope `omnigraph` in Claude Code, and warns if it finds
  one, naming the `claude mcp remove` that undoes it.
- A tracked `.mcp.json` cannot approve itself, so AutoOS writes the project server
  into that repo's untracked `.claude/settings.local.json` (`enabledMcpjsonServers`).
  Without that, Claude Code skips the server silently.
- Antigravity configurations are always merged idempotently, backing up existing
  `mcp_config.json` files before editing without dropping other user-configured
  servers.
- Qoder registration goes through `qodercli mcp add-json … -s user`, for the same
  reason Claude Code's goes through its own CLI, and skips any server already
  registered so a second run reports skipped. It registers the four repo-agnostic
  servers only: `omnigraph` stays project-scoped, because Qoder would otherwise
  carry one machine-wide graph pin into every repository.

Claude Code registration goes through `claude mcp add`, never through editing
`~/.claude.json` directly: that file is tens of kilobytes of the user's own
session state, and rewriting it to change one key violates Rule 4 (never
overwrite a config wholesale).

### What AutoOS does not do

Omnigraph is a container talking to a graph server over a Docker network. AutoOS
does not build the image, start the stack or issue credentials. Its own check is
the client side, run by the tool that can actually reach the server —
`tools/check-omnigraph.py` names a missing token, a rejected token or a missing
graph (the healthchecks call it; `docs/omnigraph.md` documents it). No install
step guesses at readiness from across a machine it cannot see. These are still how
a mis-set-up server shows up at MCP start-up:

| Missing | How it fails at MCP start-up |
|---|---|
| `omnigraph-mcp:latest` image | `pull access denied` |
| the `mcp-server` Docker network | `fetch failed` |
| `OMNIGRAPH_TOKEN` | `missing bearer token` |

The token is **not** issued by AutoOS or looked up from anywhere. It is a shared
secret you choose: the server compose file takes it as
`OMNIGRAPH_SERVER_BEARER_TOKEN=${OMNIGRAPH_TOKEN}`, so the same value goes in
`infra/mcp-servers/.env.shared` and in the client environment. AutoOS never
invents one — this repository is public, and a plausible-looking secret in it is
a leak whether or not it happens to work.

Qoder is the one AI client AutoOS wires for MCP but **cannot** route through the
OmniRoute gateway. Its Custom Models accept a curated provider list only (Alibaba
Cloud Model Studio, DeepSeek, Z.ai, Kimi, MiniMax, Xiaomi MIMO) — there is no
arbitrary OpenAI-compatible base URL, so `:20128` is not addressable. Custom models
are added interactively (`/model` → Custom tab) and stored encrypted under
`~/.qoder/.models/<uid>/customs`, so nothing in `lib/` could write one even if the
endpoint existed. Qoder therefore runs on its own subscription auth; `Set-AutoOSQoderMcp`
/ `setup_qoder_mcp` wire its tools and deliberately stop there. A function named
`route_qoder_to_gateway` would be a lie.


## Installed status and vendor setup

Optional Windows `installedNames` entries match exact registered display names plus
version, bitness, release-channel and locale suffixes — `Notepad++ (64-bit x64)`,
`PowerToys (Preview) x64` and `Mozilla Firefox (x64 en-US)` all match their bare
name, while `Git Extensions` never matches `Git`. `installedAppx` contains exact
MSIX package names.

When neither the Uninstall registry nor the `verify` command's executable settles
it, detection falls back to per-user installs under `%LOCALAPPDATA%\Programs` and
then to winget's own record of installed package ids, read once per run with a
timeout. `%APPDATA%` is never consulted: configuration outlives the uninstall that
removed the program. Set `AUTOOS_SKIP_WINGET_LIST=1` to skip the winget fallback.

Linux detection searches `~/.local/bin`, `~/bin`, `~/.cargo/bin`, `/usr/local/bin`,
`/snap/bin` and both flatpak export directories in addition to `PATH`, because a
non-login or `sudo` shell reaches none of them.

The same detector serves terminal badges, browser badges and installer skipping.
A command in a private tool runtime is not proof of a normally installed application.
Unknown probes remain unknown, without a green check.

`manual` entries require `homepage` and `notes`. They produce an action-required
result and never run an unverified installer or report a successful installation.
Windows `psmodule` entries install a named PowerShell Gallery module at user scope;
`minimumVersion` is optional. Oh My Posh declares PSReadLine and Terminal-Icons as
ordinary dependencies, rather than hiding their installation in a startup profile.
