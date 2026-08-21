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

Two tabs, with a persistent action bar pinned to the bottom so the running total
and the primary action stay visible wherever you are.

| Tab | Contains |
|---|---|
| **Overview** | Everything you choose: detected system, profile, components, install order — each in its own collapsible card. |
| **Run & log** | Any questions your selection needs, then the live colour-coded log. |

Every big card (`Detected system`, `Profile`, `Components`, `Install order`) is a
collapsible section — click the header to fold it away once you are done with it.

### Theme

A three-way switch in the header: **Auto** (follows your OS), **Light**, **Dark**.
The choice is remembered per browser.

### Profile cards expand

Selecting a profile grows its card and shrinks the others, and the expanded card
shows **what that profile actually installs** — total components, how many are
dependencies, and a per-category breakdown with the component names — before you
scroll down to the detail.

### Components: list or grid

The components card has a **List / Grid** switch and, in grid mode, a **Columns**
selector (auto-fit, 2, 3 or 4). Both are remembered per browser. Grid is useful
on a wide screen; list gives each component more room for its dependency chips.

There is also a filter box, per-group counts, and expand/collapse for the
category groups.

Each component shows its **provider** (colour-coded: `winget`, `apt`, `brew`,
`npm`, `script`, `custom`), its exact package id, and its name as a link to the
project's own homepage.

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

The **Install order** card turns the same information into the actual plan:

```
①  Step 1 · 4 components     Needs nothing else — installed first.
      curl & wget · Git · OpenSSH server · tmux
②  Step 2 · 2 components     Cannot start until step 1 has finished.
      Node.js LTS  (after curl & wget) · Tailscale  (after curl & wget)
③  Step 3 · 2 components     Cannot start until step 2 has finished.
      Claude Code CLI  (after Node.js LTS) · Herdr  (after Node.js LTS)
```

Steps come from the longest requirement chain, so step 1 always "needs nothing".

### Recommended flow

1. **Overview** — confirm the machine, pick a profile, read its summary.
2. Adjust the ticks below. Watch the dependency chips and the install order.
3. **Run & log** — leave *Dry run* ticked, press **Install selected**, read the log.
4. Untick *Dry run*, press it again, confirm the dialog.

The confirmation dialog names how many components are about to be installed and
how many are automatic dependencies. Dry run changes nothing at all.

### Stopping the server

`Ctrl-C` in the terminal stops it and releases the port. On Linux and macOS
`SIGTERM` (a plain `kill`) works too.

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
