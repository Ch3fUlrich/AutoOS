# The catalog

`catalog/windows.json`, `catalog/linux.json` and `catalog/macos.json` describe
**what** can be installed. `lib/` describes **how**.

Adding software must never require touching `setup.ps1` or `setup.sh`. If you
find yourself editing an entry point to add a package, the schema is missing
something — extend the schema instead.

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
  "notes": "One line shown under the plan entry."
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
