# OpenHands Agent Canvas

`agent-canvas` (npm `@openhands/agent-canvas`, catalog id `openhands`) is the
OpenHands web UI plus its agent-server, started locally through `uvx`. AutoOS
installs it and writes its configuration; you use it in the browser.

```bash
./setup.sh --only openhands -y        # installs Node.js too, then configures ~/.openhands
```

```powershell
.\setup.ps1 -Only openhands -Yes
```

## 1. Give it keys (optional)

The configure step reads, in this order, the environment (`MUSE_API_KEY`,
`DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `CONTEXT7_API_KEY`) and then
`~/Documents/Code/agent-skills/secrets/api_keys.conf` (`muse=…`, `deepseek=…`,
`openrouter=…`, `context7=…`). Keys are written only to your `~/.openhands`,
never to this repository. With no keys at all you still get a working setup on
the local Ollama model.

## 2. What the configure step writes

Re-running it is safe; `settings.json` is backed up first and merged, not
replaced.

| Path under `~/.openhands` | Contents |
|---|---|
| `settings.json` | The default LLM — the first of Muse Spark → DeepSeek → OpenRouter free → local Ollama you have a key for — plus the Serena, Graphify and Omnigraph MCP servers |
| `profiles/*.json` | One LLM profile per model in [`catalog/llm-models.json`](../catalog/llm-models.json) (DeepSeek, Muse Spark, OpenRouter models, Ollama) with context windows and prices, plus one per gateway tier (`omniroute-tier*`, `litellm-tier*`) from [`configuration/openhands/tier-profiles.json`](../configuration/openhands/tier-profiles.json) |
| `agent-profiles/*.json` | The agent hierarchy, copied from [`openhands/agent-profiles/`](../openhands/agent-profiles) |
| `skills` | A link to `agent-skills/skills`, when that checkout exists |

## 3. Start it and open the page

```bash
agent-canvas                  # http://localhost:8000
agent-canvas --port 3000      # another port
agent-canvas --info           # stack versions and ports
```

LLM settings are changed in the page's **Settings**, not through flags or
environment variables. The page's API key is generated and injected for you.
With `--public`, you must set `LOCAL_BACKEND_API_KEY` and paste it in the
browser. Don't use `--public` on a machine others can reach without a reason.

## 4. Pick an agent profile

The vendored agent profiles form a three-level hierarchy: the two upper levels
plan and delegate, and the lowest level carries out closed, mechanical tasks.

| Level | Profile | Model | Spawns sub-agents |
|---|---|---|---|
| L1 orchestrator | `orchestrator` / `orchestrator-free` | Muse Spark / Nemotron Ultra (OpenRouter free) | yes |
| L2 sub-orchestrator | `suborchestrator` / `suborchestrator-free` | Muse Spark / Nemotron Ultra | yes |
| L3 worker | `worker` / `worker-free` / `worker-free-high` | DeepSeek chat / Laguna / Nemotron Lightning | no |
| L3 via ACP | `claude-sonnet`, `claude-haiku`, `claude-opus`, `agy-gemini-3.8-flash` | Claude Code or Gemini CLI, which must be installed and signed in | — |

Pick the `-free` variants to run the whole tree on OpenRouter's free tier. Every
profile can switch model at run time (`enable_switch_llm_tool`).

## Things to know

- **Ollama's address is chosen at configure time.** `agent-canvas` runs the
  agent-server on the host, but a containerised OpenHands sees `127.0.0.1` as
  itself. So `OLLAMA_BASE_URL` wins when set. Otherwise
  `http://host.docker.internal:11434/v1` is used if Ollama answers there (Docker
  Desktop hosts, where both the host and containers can reach it). Otherwise it
  stays `http://127.0.0.1:11434/v1`, which is right for a native Linux host. For
  OpenHands in a container on native Linux, set
  `OLLAMA_BASE_URL=http://ollama:11434` (or your Ollama's address) and re-run
  the configure step.
- **`agent-canvas` has no headless mode.** It has no `--prompt`, `--model` or
  session flags, so it can't be driven as a CLI worker from a script. For
  unattended runs, use the orchestration skills in `agent-skills`, and use the
  canvas when a person is watching.
- Muse Spark and DeepSeek are paid per token. The `-free` profiles and Ollama
  cost nothing.

Vendored profile details and the thinking-flag opt-outs:
[`openhands/README.md`](../openhands/README.md).

Continuing this repository's work from inside the OpenHands container:
[OpenHands runbook](openhands-runbook.md).
