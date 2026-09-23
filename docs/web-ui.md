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

If the port is taken the server moves to the next free one — up to 20 along — and
says so above the URL. Read the port off the URL it prints rather than assuming
8777; the token is per-run either way.

The page and the server are tied together: it polls the server every couple of
seconds, and once the server goes it closes itself where the browser allows that
and otherwise clears itself and says so. A tab left open after `Ctrl-C` cannot go
on offering buttons that do nothing.

## Using it

Navigation is a dropdown in the header — one labelled button rather than a strip
that grew a tab every time the page gained a job. It is a real menu: arrow keys
walk it, Escape closes it, clicking elsewhere closes it. A persistent action bar
stays pinned to the bottom so the running total and the primary action are
visible wherever you are.

| Section | Contains |
|---|---|
| **Overview** | What to install: profile, components, install order. |
| **Configure** | Settings for the things AutoOS installs, and any questions your selection needs. Saved to `autoos.config.json`. |
| **Run & log** | The live colour-coded log. |
| **System** | What this machine is, and what is already on it. Read-only. |

Overview is only ever *choose what to install*. The rest of the header carries
the machine name and one button that toggles light and dark.

![Run & log: the pre-install questions and the live, colour-coded output](assets/webui-runlog.png)

### Card chooser

At the top of Overview sits the **Choose cards** toolbar: toggle any card off, or
pick a view — **All**, **Suggested** (profile and components) or **Catalog**
(components and install order). The active view is shown as a pressed button, and
your choice is remembered in `localStorage`. Choosing a card also expands it: a
preset that reveals a card you then have to unfold has not really revealed it.

Components with settings carry a **⚙ Configure** chip that switches to the
Configure tab, opens the right card and focuses the field.

### System

The detected machine, then everything already present on it — grouped by the
catalog's own categories and filterable by name, package or description. AutoOS
skips the latter on a run.

That list used to be a single comma-separated line inside the *Detected system*
card, where thirty-odd entries pushed every other detected fact off the screen.

### Components

The list is compact by default: the application's icon, its name and one line of
description. **Details** adds the provider, the exact package, the platform and
the dependency chips — the things you want when auditing a plan and never when
picking one. Within each category, what is already installed sorts last.

Each component that is not installed carries a **⚡ Install** button that installs
just that one without touching your selection. Presses while a run is in flight
are queued (the server runs one at a time), and progress appears in the header so
it is visible from whichever section you started it on.

Icons are the applications' own favicons, fetched by the homepage domain in the
catalog through DuckDuckGo's icon service. Two things follow: that service learns
which domains are in the catalog, and on an offline machine — which a freshly
provisioned box often is — nothing is fetched. Each icon is drawn on top of a
monogram, so a blocked, failed or offline request leaves a letter rather than a
broken image.

### Claude autostart

One card on the Configure tab, covering the `claude-autostart` component. It
leads with the three facts that decide whether your sessions actually come back:

| Shown | Means |
|---|---|
| **Service** | Whether the supervisor (systemd user units, or Scheduled Tasks) is installed |
| **Last snapshot** | How long ago the session list was recorded — anything older than a few minutes means the timer is not running |
| **Tracked sessions** | How many sessions would be reopened right now |

Below that, the sessions themselves (name, directory, branch, last active), a
**Capture snapshot now** button, and the settings:

| Setting | Effect |
|---|---|
| **Autostart** | On, or paused. Pausing keeps the service and the snapshots and restores nothing. |
| **Resume mode** | Resume in full, or from a summary. Only enforced where the terminal host supports typing into it (herdr, tmux) — see [ADR 0002](decisions/0002-restored-sessions-need-a-visible-terminal.md). |
| **When there is no snapshot** | Start one `claude --continue` session, or do nothing. |
| **Remote control** | Whether restored sessions get `--rc`. |
| **Snapshot every** | Interval for the timer. Baked into the unit file and the Scheduled Task, so changing it needs the installer re-run — the card says so after you save. |
| **Count as live for** | How far back "was running before the shutdown" reaches. |
| **Restore at most** | Cap, most recently active first. |

Saving merges into `autoos.config.json`; settings the card does not show
(`fallback_cwd`, `fallback_name`, `terminal_host`) are left alone.

The same state is available in the terminal, which is the better place to look
when the browser UI is not running:

```bash
bash lib/linux/claude-sessions.sh status
```

```powershell
pwsh lib\windows\claude-sessions.ps1 -Action status
```

### Prefilled answers

On a machine with no `autoos.config.json` yet, the configuration form is seeded
from the machine — your real `git config --global user.name` and `user.email`,
not the example file's `Your Name` / `you@example.com`. Catalog defaults appear
as greyed placeholders rather than as values, so a field you have not answered
looks unanswered, and an empty field is saved as *unanswered* rather than as an
empty string that would shadow the default.

### Dry run

**Dry run** is off by default and the confirmation lists every package before
anything happens. Tick it to print the plan without touching the machine.

### Theme

One button in the header. It starts on your OS preference and shows the theme it
would switch *to* — a sun while you are in dark, a moon while you are in light —
because that is the only thing a single toggle can say unambiguously. The choice
is remembered per browser.

### Profile cards expand into a plan

Selecting a profile grows its card and shrinks the others. The expanded card
shows **what that profile actually installs, and in what order**:

- a header line — total components, categories, and how many install steps;
- a process strip — `① 13 components → ② 8 components → ③ 5 components`;
- a **grid of category cards**, each listing its components with a numbered
  badge giving that component's install step.

Cards are ordered by when their work starts, and components within a card are
sorted by step, so reading top-left to bottom-right follows the install
sequence. Dependencies pulled in automatically are tinted purple.

The same numbers appear as a `step N` chip on every component in the list below
and as the numbered tiers in the Install order card — all three come from one
shared function, so they cannot drift apart.

### Components: list or grid

The components card has a **List / Grid** switch and, in grid mode, a **Columns**
selector (auto-fit, 2, 3 or 4). Both are remembered per browser. Grid is useful
on a wide screen; list gives each component more room for its dependency chips.

There is also a filter box, per-group counts, and expand/collapse for the
category groups.

Each component shows its **provider** (colour-coded: `winget`, `apt`, `brew`,
`npm`, `script`, `custom`), its exact package id, and its name as a link to the
project's own homepage.

Components that are not packaged for every platform carry a label —
`Linux only`, `not on Windows`, and so on. Silence means it is available on all
three. See [the catalog](catalog.md#platform-availability).

### Installed applications

AutoOS probes whether components from the catalog are already installed on the
host system:

- **Header pill**: A green `✓ N installed` pill in the header indicates how many catalog
  applications were detected on the machine, with a tooltip listing them.
- **Detected system**: Lists the detected installed application names prefixed by a green checkmark.
- **Component badges**: Applications already present display a green **`✓ installed`**
  badge next to their name.
- **Installed filter**: A **`✓ Installed (N)`** button in the toolbar filters the
  catalog list to display only the components already installed on the machine.
- **Profile summary**: Shows how many of the profile's components are already
  present on this computer (`✓ N already installed`).

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

### Copying the output

The log has a **Copy** button in its top-right corner that puts the whole run —
not just the visible part — on the clipboard, which is what you want when pasting
into an issue. It works while the card is collapsed, and it does not collapse the
card when clicked.

`localhost` is a secure context, so the clipboard API is available there. Over a
`--bind` LAN address on plain http it is not, and the button falls back to a
hidden textarea; if the browser refuses that too it selects the log and says so,
leaving `Ctrl-C` to finish the job.

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
| `/api/config` | GET, POST | Read / save configuration, answers, and autostart settings |
| `/api/claude/sessions` | GET | The recorded session list, when it was captured, and whether the supervisor is installed. Read-only. |
| `/api/claude/snapshot` | POST | Record the live sessions now |

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

## Installed apps and progress

Green checks identify detected applications in the component list, profile cards
and install order. The detector refreshes after a run. Checks do not mean an
application was installed by a preview, and unchecked/unknown detection is not
proof that an application is absent.

The overall bar counts **completed** components, including dependencies and skips.
The current-app bar uses a percentage supplied by the installer (including byte
counts during WinGet downloads). When no percentage is available it is
indeterminate and shows the phase and elapsed time. A stopped run keeps its
actual completed count; it never jumps to 100 percent simply because it exited.
