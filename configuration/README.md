# configuration/ — one place for every moving part

Everything AutoOS wires into apps lives here, so a machine can be re-created
from this folder plus `catalog/`. Keys never leave it: the real key file is
git-ignored, only the template is tracked.

| Path | What it is | Read by |
|---|---|---|
| `api-keys.yml` | **The key file.** Flat `provider: key`. Git-ignored. | `omniroute/apply.*`, `start-stack.*`, the browser UI (status only) |
| `api-keys.example.yml` | Tracked template — copy to `api-keys.yml` | you |
| `omniroute/combos.json` | Tier definitions (t1-orchestrator/t2-worker/t3-driver/t4-rag + `-clean`/`-free-only` variants) | `omniroute/apply.*` |
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
opencode serve --hostname 0.0.0.0 --port 4096   # then open http://<this-host>:4096
opencode pair                                    # QR + username/password for the phone
opencode pair --url https://<public-host>        # advertise an external URL instead
```

Unauthenticated requests get `401` — that is the server proving it is alive,
not an error. Pairing credentials print once in the terminal; keep them out
of tracked files. No config change is needed: the repo `opencode.jsonc`
routing (OmniRoute `:20128`) applies to the served sessions too.

## Autostart after reboot (opt-in)

```powershell
.\configuration\autostart\Register-AutoOSAutostart.ps1            # register the logon task
.\configuration\autostart\Register-AutoOSAutostart.ps1 -Unregister # remove it again
```

```bash
cp configuration/autostart/autoos-stack.service ~/.config/systemd/user/  # adjust ExecStart to your checkout
systemctl --user enable --now autoos-stack.service
```

Resumes the gateway, the `openhands-app` container, **and** `opencode serve` on :4096. AutoOS `--serve`
never autostarts (its token is random per run). Registration is idempotent:
re-running replaces the same task / unit. Health: `healthcheck.sh` /
`healthcheck.ps1` (log-only; `--fix` / `-Fix` resumes). Windows: prefer the
`.ps1` — Git Bash `curl` cannot reach IPv4-only loopback listeners on some
boxes and would report them down.

## Rules

- **Keys only in `api-keys.yml`** (and the fallback LiteLLM `.env`). No key
  ever appears in a tracked file, an argument on the command line, or the
  browser payload.
- **`apply` is idempotent**: re-running replaces combos and re-adds providers.
- Model refs in `combos.json` are validated against the live catalog when the
  client key allows it; unknown refs are skipped with a warning, never
  silently swapped for something else.
