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

The page is one scroll, top to bottom:

| Section | What you do |
|---|---|
| **Detected system** | Read-only. Confirms you are on the machine you think you are. |
| **What to install** | Pick a profile, then tick or untick anything. `Select all` / `Select none` are there for the impatient. |
| **Questions** | Appears only when your selection needs it — e.g. the Omnigraph URL, the Herdr source. |
| **Run** | `Dry run` is **on by default**. Press `Install selected`. |
| **Log** | Live output, colour-coded, scrolling as it goes. A progress bar tracks components. |

The questions section is reactive: tick `agent-skills` and the Omnigraph URL
field appears; untick it and the field goes away. You answer everything
*before* the install starts, so it never stops halfway to ask.

### Recommended flow

1. Leave **Dry run** ticked and press **Install selected**.
2. Read the log. It shows every command that *would* run.
3. Untick **Dry run**, press it again, confirm the dialog.

The confirmation dialog names how many components are about to be installed.
Dry run changes nothing at all — not one file.

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
EOF
