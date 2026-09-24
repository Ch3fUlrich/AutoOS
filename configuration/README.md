# configuration/ — one place for every moving part

Everything AutoOS wires into apps lives here, so a machine can be re-created
from this folder plus `catalog/`. Keys never leave it: the real key file is
git-ignored, only the template is tracked.

| Path | What it is | Read by |
|---|---|---|
| `api-keys.yml` | **The key file.** Flat `provider: key`. Git-ignored. | `omniroute/apply.*`, `start-stack.*`, the browser UI (status only) |
| `api-keys.example.yml` | Tracked template — copy to `api-keys.yml` | you |
| `omniroute/combos.json` | Tier definitions (tier1/2/3 + `-clean` variants) | `omniroute/apply.*` |
| `omniroute/apply.sh` / `apply.ps1` | Registers providers + (re)creates combos | you, once after keys are in |
| `start-stack.sh` / `start-stack.ps1` | Starts OmniRoute if needed, then an app wired to it | you |
| `autostart/` | Opt-in resume after reboot (scheduled task / systemd unit + launcher) | you, once |
| `healthcheck.sh` / `healthcheck.ps1` | Probe `:20128` `:3000` `:4096` `:8777`; log-only, `--fix`/`-Fix` resumes | you |
| `litellm/` | The manual fallback router (off by default) | `setup.*` when LiteLLM is ticked |
| `openhands/config.toml` | OpenHands LLM profiles (dev path); docker path uses env | OpenHands |

## First run

```bash
cp configuration/api-keys.example.yml configuration/api-keys.yml   # fill in
./configuration/omniroute/apply.sh          # register keys + build combos
./configuration/start-stack.sh opencode     # gateway + app
```

```powershell
Copy-Item configuration\api-keys.example.yml configuration\api-keys.yml
.\configuration\omniroute\apply.ps1
.\configuration\start-stack.ps1 -App opencode
```

Key origins: [docs/api-keys.md](../docs/api-keys.md). Tier meanings and the
training-data rules: [docs/models.md](../docs/models.md). Phone reachability:
[docs/troubleshooting.md](../docs/troubleshooting.md#phone-over-tailscale-remote-uis).

## opencode serve (phone fallback web UI)

```bash
./configuration/autostart/run-opencode-serve.sh --detach   # 0.0.0.0:4096, background
./configuration/start-stack.sh opencode-serve              # same, no-op when up
```

opencode V2 serves its UI to anyone and guards `/api/*` with HTTP Basic auth:
user **`opencode`**, password from `OPENCODE_PASSWORD`. Without that variable
it invents a new password on every start, so the wrapper pins one in
`~/.config/autoos/opencode-serve.password` (mode 600, generated on first use,
never printed) and exports the `{env:...}` keys the global opencode config
references (read literally from `configuration/litellm/.env` and
`api-keys.yml`). A client on another machine connects with
`OPENCODE_PASSWORD=... opencode --server http://<this-host>:4096`.

## Autostart after reboot (opt-in)

```powershell
.\configuration\autostart\Register-AutoOSAutostart.ps1            # register the logon task
.\configuration\autostart\Register-AutoOSAutostart.ps1 -Unregister # remove it again
```

```bash
./configuration/autostart/register-autostart.sh --dry-run    # what it would write
./configuration/autostart/register-autostart.sh              # write, enable, start
./configuration/autostart/register-autostart.sh --takeover   # also replace hand-started copies
./configuration/autostart/register-autostart.sh --unregister # remove them again
```

Linux registers four systemd `--user` units, rendered with this checkout's
path and the PATH this machine needs (nvm's node, `~/.local/bin`), so re-run
it after moving the checkout or upgrading node:

| Unit | Runs | Listens |
|---|---|---|
| `autoos-omniroute` | `omniroute serve` (notify + watchdog) | `0.0.0.0:20128` |
| `autoos-litellm` | `configuration/litellm/start-litellm.sh --foreground` | `127.0.0.1:4000` |
| `autoos-opencode` | `configuration/autostart/run-opencode-serve.sh` | `0.0.0.0:4096` |
| `autoos-stack` | `Start-AutoOSStack.sh` (oneshot: OpenHands container, anything down) | - |

A service somebody started by hand keeps running: its unit is enabled (it
takes over at the next boot) but not started, unless `--takeover`. The
gateway keeps `REQUIRE_API_KEY=true` in `~/.omniroute/.env`; registration
appends it there (after a backup) only when the line is missing. The units
start at boot only with lingering on (`loginctl enable-linger`); the script
says when that needs sudo. By hand instead: copy a unit, fill in the paths,
then `systemctl --user enable --now autoos-stack.service`.

AutoOS `--serve` never autostarts (its token is random per run). Registration
is idempotent: re-running reports unchanged units as skipped. Health:
`healthcheck.sh` / `healthcheck.ps1` (log-only; `--fix` / `-Fix` resumes the
gateway, litellm, OpenHands and opencode serve). Windows: prefer the
`.ps1` - Git Bash `curl` cannot reach IPv4-only loopback listeners on some
boxes and would report them down.

## Rules

- **Keys only in `api-keys.yml`** (and the fallback LiteLLM `.env`). No key
  ever appears in a tracked file, an argument on the command line, or the
  browser payload.
- **`apply` is idempotent**: re-running replaces combos and re-adds providers.
- Model refs in `combos.json` are validated against the live catalog when the
  client key allows it; unknown refs are skipped with a warning, never
  silently swapped for something else.
