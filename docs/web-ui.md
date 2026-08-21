# Browser UI

For a machine with no keyboard attached — a Raspberry Pi in a cupboard, a
server you reach over Tailscale, a fresh box you are SSH'd into — the terminal
menu is awkward. `--serve` gives you the same thing in a browser.

## Short answer

**Yes — you can do the entire setup in the browser and install from there.**
Detection, profile, every checkbox, every question and the install itself all
happen on the page. Nothing has to be done in the terminal first except
starting the server.

## Starting it

```bash
./setup.sh --serve                    # only this machine can reach it
./setup.sh --serve --bind 0.0.0.0     # reachable from your LAN / tailnet
./setup.sh --serve --port 9000        # different port
```

```powershell
.\setup.ps1 -Serve
.\setup.ps1 -Serve -Bind 0.0.0.0
```

It prints a URL containing a one-time token:

```
  AutoOS browser UI
  ----------------------------------------------------------
  http://localhost:8777/?token=k3Jd9x...
  ----------------------------------------------------------
  The token changes every run. Ctrl-C to stop.
```

Open that URL. On a desktop it opens by itself; on a headless box, copy it.

## Using it

The page is organised into four tabs, with a persistent action bar pinned to the
bottom so the primary action and the running total are always visible.

| Tab | What you do |
|---|---|
| **Overview** | Read the detected system, pick a profile. The one matching your hardware is tagged `suggested`. |
| **Components** | Tick or untick anything, grouped by category with a per-group count. Filter, expand/collapse, select all/none. |
| **Install order** | See the resolved plan as numbered steps — what gets installed first and what waits on it. |
| **Run & log** | Answer any questions your selection needs, then watch the live, colour-coded log. |

Each component shows its **provider** (colour-coded: `winget`, `apt`, `brew`,
`npm`, `script`, `custom`), its exact package id, and its name as a link to the
project's own homepage — so you can check what something is before installing it.

### Dependencies are shown, not hidden

Some things cannot be installed on their own. Claude Code CLI needs Node.js;
Powerlevel10k needs Oh My Zsh, which needs Zsh and Git. The page makes that
explicit rather than silently expanding your selection:

- Every component lists **`needs …`** and **`needed by N (…)`** chips.
- Ticking something **auto-adds its dependencies**, drawn with a dashed purple
  border and an **`auto`** chip so you can tell them from your own choices.
- A dependency that something else needs is marked **`locked`** and cannot be
  unticked — hover it and the tooltip names what requires it. Untick the thing
  that needs it instead.
- The action bar always reads e.g. `3 to install · 2 pulled in as dependencies`.

The **Install order** tab turns the same information into the actual plan:

```
①  Step 1 · 4 components     Needs nothing else — installed first.
      curl & wget · Git · OpenSSH server · tmux
②  Step 2 · 2 components     Cannot start until step 1 has finished.
      Node.js LTS  (after curl & wget) · Tailscale  (after curl & wget)
③  Step 3 · 2 components     Cannot start until step 2 has finished.
      Claude Code CLI  (after Node.js LTS) · Herdr  (after Node.js LTS)
```

Steps are computed from the longest requirement chain, so step 1 is always
"needs nothing". Items pulled in automatically keep the dashed purple styling.

### Recommended flow

1. **Overview** — confirm the machine, choose a profile.
2. **Components** — adjust the ticks. Watch the dependency chips.
3. **Install order** — sanity-check what will happen and in what sequence.
4. **Run & log** — leave *Dry run* ticked, press **Install selected**, read the log.
5. Untick *Dry run*, press it again, confirm the dialog.

The confirmation dialog names how many components are about to be installed and
how many of those are automatic dependencies. Dry run changes nothing at all —
not one file.

## What happens under the hood

The page does not install anything itself. It calls a small local server which
runs **the same entry point you would have typed**:

```
setup.sh  --only <your,ticked,ids> --yes --no-color [--dry-run]
```

That matters: the browser path and the terminal path cannot drift apart, and
dependency resolution, the plan, idempotency checks and post-install steps are
all the ones you already tested from the CLI. Your answers reach it as
`AUTOOS_ANSWER_*` environment variables.

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | The page itself (no token needed — it is a static file) |
| `/api/state` | GET | Detected system + the catalog for this machine |
| `/api/install` | POST | Start a run — `{ids, answers, dryRun}` |
| `/api/log` | GET | Poll for new log lines and progress |

## Security

This endpoint installs software, so it is locked down by default.

- **Binds `127.0.0.1`** unless you pass `--bind`. A wider bind prints a warning.
- **Every API call needs the token** printed in the terminal. Without it you get
  `403`. The token is regenerated on every start.
- **`--dry-run` locks the whole session** to preview-only:
  ```bash
  ./setup.sh --serve --bind 0.0.0.0 --dry-run
  ```
  Now anyone with the URL can look at the plan but the server will refuse to
  install, no matter what the page sends. Useful for showing someone what a
  profile does.
- **One run at a time.** A second `POST /api/install` while one is running gets
  `409`.

Over a tailnet this is reasonable. On an untrusted network, prefer SSH port
forwarding and keep the default loopback bind:

```bash
ssh -L 8777:localhost:8777 you@the-box
# then run ./setup.sh --serve on the box and open the URL locally
```

## Limits worth knowing

- **Elevation is inherited, not requested.** The install runs as the user who
  started the server. On Windows, machine-wide packages are skipped unless you
  started `setup.ps1 -Serve` from an elevated terminal. The page shows
  `elevated: yes/no` in the detected-system panel — check it before a big run.
- **Non-loopback binding on Windows** needs an elevated shell, or a one-time URL
  reservation:
  ```powershell
  netsh http add urlacl url=http://+:8777/ user=$env:USERNAME
  ```
- **The page has no replay control.** Run state is still saved server-side, so
  after a browser run you can replay it from the CLI with
  `--from-state`. See [Replay, verification & undo](state-and-undo.md).
- **Closing the tab does not stop the install.** The server owns the run; reopen
  the URL and `/api/log` picks the output back up from the start.
- **`Ctrl-C` in the terminal stops the server**, and with it any run in progress.
