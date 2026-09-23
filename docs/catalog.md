# The catalog

`catalog/windows.json`, `catalog/linux.json` and `catalog/macos.json` describe
**what** can be installed. `lib/` describes **how**.

The same folder holds three catalogs of other types: `images.json` (bootable
images for the [USB creator](usb-creator.md)), `engines.json` (USB write engines)
and `llm-models.json` (with `llm-models.schema.json`). `--check-catalog` validates
every file in `catalog/`, dispatched by its top-level shape; an unrecognised shape
fails.

Adding software must never require touching `setup.ps1` or `setup.sh`. If you
find yourself editing an entry point to add a package, the schema is missing
something — extend the schema instead.

## Software on offer

Run `--list` for the current set. The headline items:

| Area | Includes |
|---|---|
| Terminal | Windows Terminal, PowerShell 7, Oh My Posh, zsh + Powerlevel10k, Nerd Fonts |
| Coding & AI | Claude Code CLI, OpenCode CLI, Claude autostart (reopens your sessions after a reboot), Claude Desktop, Antigravity, Zed, VS Code, Docker, Herdr, Node.js, OmniRoute gateway, LiteLLM fallback router |
| Input | Handy — offline speech-to-text, so you can dictate prompts instead of typing them |
| MCP stack | Clones [agent-skills](https://github.com/Ch3fUlrich/agent-skills), registers Graphify with Claude Code and approves Omnigraph per-repo — asking for your Omnigraph URL rather than hardcoding one, and naming what is still missing rather than pretending it is wired |
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
Protocol (MCP) servers across both **Claude Code** (`~/.claude.json` via CLI) and
**Antigravity** (`~/.gemini/config/mcp_config.json` on Linux/macOS and
`%APPDATA%\Antigravity\mcp_config.json` on Windows).

Available standalone MCP components in the catalog:
- `mcp-serena`: Semantic code navigation & symbol search (LSP) via `serena-agent`.
- `mcp-graphify`: Codebase knowledge graph queries via `graphify.serve`.
- `mcp-playwright`: Headless browser automation via `@playwright/mcp`.
- `mcp-context7`: Real-time documentation lookups via `@upstash/context7-mcp`.

The `agent-skills` component wires the complete MCP stack above, along with
`omnigraph` project-scoped memory.

| Server | Scope | Configuration & Precedence |
|---|---|---|
| **graphify** | one **user** entry | Cwd-relative (`graphify-out/graph.json`), serving each repo its own graph. Configured in both Claude Code and Antigravity. |
| **serena** | one **user** entry | Repo-agnostic symbol lookups via LSP. Path chosen at runtime. Configured in both Claude Code and Antigravity. |
| **playwright** | one **user** entry | Headless browser execution for coding agents. Configured in both Claude Code and Antigravity. |
| **context7** | one **user** entry | Real-time framework and library docs. Configured in both Claude Code and Antigravity. |
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

Claude Code registration goes through `claude mcp add`, never through editing
`~/.claude.json` directly: that file is tens of kilobytes of the user's own
session state, and rewriting it to change one key violates Rule 4 (never
overwrite a config wholesale).

### What AutoOS does not do

Omnigraph is a container talking to a graph server over a Docker network. AutoOS
does not build the image, start the stack or issue credentials — it checks and
names whichever of these is missing:

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
