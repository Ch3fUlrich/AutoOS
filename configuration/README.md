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
training-data rules: [docs/models.md](../docs/models.md).

## Rules

- **Keys only in `api-keys.yml`** (and the fallback LiteLLM `.env`). No key
  ever appears in a tracked file, an argument on the command line, or the
  browser payload.
- **`apply` is idempotent**: re-running replaces combos and re-adds providers.
- Model refs in `combos.json` are validated against the live catalog when the
  client key allows it; unknown refs are skipped with a warning, never
  silently swapped for something else.
