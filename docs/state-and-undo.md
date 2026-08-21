# Replay, verification & undo

Three things that turn a one-shot installer into something you can rely on
twice: a record of what you chose, proof that what installed actually runs, and
a way back for the files AutoOS touched.

## Replay

Every real run writes a state file — by default `.autoos-state.json` in the
repo root (git-ignored).

```json
{
  "version": 1,
  "savedAt": "2026-08-21T10:53:44Z",
  "platform": "linux",
  "profile": "light",
  "selected": ["git", "tmux", "claude-code"],
  "answers": { "omnigraph_url": "https://omnigraph.example.com" },
  "results": { "installed": ["claude-code"], "skipped": ["git", "tmux"], "failed": [] }
}
```

Set up the next machine the same way:

```bash
./setup.sh --from-state .autoos-state.json
```

```powershell
.\setup.ps1 -FromState .autoos-state.json
```

Notes:

- It is **saved last**, so it records what actually happened, not what was planned.
- A **dry run saves nothing** — it would otherwise overwrite a real record with a hypothetical one.
- Replaying onto different hardware **drops what does not apply** and says so.
  A Pi replaying a workstation state quietly skips the x64-only entries rather
  than failing.
- Answers are replayed too, so you are not asked the same questions again.
- `--save-state <path>` puts it somewhere else — useful for keeping one per machine.

The state file records *choices*, not secrets. Check before syncing it anywhere
if you have added prompts of your own.

## Verification

A package manager reporting success is not proof the thing works. A binary can
land outside PATH; a shim can be written without its runtime. Components can
declare how to prove it:

```jsonc
{ "id": "claude-code", "verify": "claude --version" }
```

After a successful install AutoOS runs that command, having first re-read the
**persisted** PATH — a fresh install usually lands somewhere the current
process's PATH predates.

| Outcome | Meaning |
|---|---|
| `verified` | The command ran and exited 0 |
| `unverified` | Installed, but the command failed — usually needs a new terminal |
| `unchecked` | No `verify` in the catalog, or `--no-verify`, or a dry run |

Unverified components are counted separately in the summary. They are a
*warning*, not a failure: "installed but needs a new login" is a normal outcome
for anything that changes PATH.

Skip it with `--no-verify` / `-NoVerify`.

## Undo

```bash
./setup.sh --undo              # asks first
./setup.sh --undo --dry-run    # show what would be restored
./setup.sh --undo --yes        # no prompt
```

```powershell
.\setup.ps1 -Undo
```

### What it restores

Every file AutoOS modifies is copied to `<file>.autoos-backup-<timestamp>`
first. Undo finds those, picks the **newest per original**, and restores them:

- shell profiles (`.zshrc`, `Microsoft.PowerShell_profile.ps1`)
- any config a `postInstall` step edited
- on Windows, the **user PATH**, from `.autoos-path-backup-User-<timestamp>.txt`

### What it deliberately does not do

**It does not uninstall packages.** Guessing which of a package manager's
changes were "ours" is how an undo becomes a second incident. Removing software
is left to the tool that owns that record:

```bash
sudo apt-get remove <package>
brew uninstall <package>
```

```powershell
winget uninstall --id <the.id>
choco uninstall <package>
```

Both test suites assert this property directly — a grep over the undo path that
fails if an uninstall command ever appears in it.
