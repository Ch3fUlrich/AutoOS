# Local AI & Coding Stack

An optional, self-hosted LLM inference, UI, and SWE agent stack designed to complement your coding workflow. This environment provides local model execution via Ollama, API proxying and routing via LiteLLM, a chat interface via Open WebUI, and a browser-based agent coding environment via OpenHands.

## Components

- **OpenHands**: Browser-based Software Engineering (SWE) AI agent platform using a sandboxed runtime container for executing code safely.
- **Ollama**: Local LLM engine serving models.
- **Ollama Agent**: A sandboxed container configured to access the main Ollama API via `host.docker.internal`. Useful for running tasks locally.
- **LiteLLM**: Standardizes API calls across multiple providers (e.g., Perplexity, OpenAI, Anthropic). It's pre-configured with Perplexity models and serves as a unified proxy.
- **Open WebUI**: A feature-rich frontend for interacting with your models, connected to both Ollama and LiteLLM.

## Setup Instructions

1. **Copy the environment template:**
   ```bash
   cp .env.example .env
   ```

2. **Configure your `.env` file:**
   - **Workspace**: Create the directory specified in `WORKSPACE_BASE` (default `C:/Apps/coding_workspace`).
   - **Secrets**: Set a strong `WEBUI_SECRET_KEY` (e.g., using `openssl rand -hex 32`).
   - **API Keys**: Provide keys like `PERPLEXITY_API_KEY` for LiteLLM, and any required keys for OpenHands (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.). *Alternatively, you can point OpenHands to the local LiteLLM proxy to centralize key management.*
   - **Paths**: Adjust `APPS_ROOT` and `WORKSPACE_BASE` to match your local setup. 

3. **Start the stack:**
   ```bash
   docker compose up -d
   ```
   *(Note: The first run may take a few minutes as OpenHands downloads its runtime images).*

4. **Access the services:**
   - **OpenHands**: [http://localhost:3000](http://localhost:3000)
   - **Open WebUI**: [http://localhost:3131](http://localhost:3131)
   - **LiteLLM Proxy**: [http://localhost:4000](http://localhost:4000)
   - **Ollama API**: [http://localhost:11434](http://localhost:11434)

## Using the Ollama Agent

The `ollama-agent` container runs continuously (via `tail -f /dev/null`) and shares the models directory (`C:/Apps/ollama`) with the main `ollama` service. To run a model inside this isolated environment:

```bash
docker exec -it ollama-agent ollama run llama3.1:8b-instruct-q5_k_m
```

## Integration into AutoOS (Phase 2)

### Already covered by AutoOS
- Ollama: catalog component `ollama` (script install, `setup_ollama_models`) covers the local
  runtime that the `ollama` service here also provides.
  (sourced: ../../AutoOS/catalog/linux.json)
- OpenHands: catalog component `openhands` is only the Agent Canvas (`@openhands/agent-canvas`,
  npm) — the multi-agent UI, not the all-hands-ai OpenHands server the removed service ran.
  (sourced: ../../AutoOS/catalog/linux.json, ../../AutoOS/catalog/macos.json,
  ../../AutoOS/catalog/windows.json)
- LiteLLM / Open WebUI: no catalog component found for either.
  (sourced: ../../AutoOS/catalog/linux.json, ../../AutoOS/catalog/macos.json,
  ../../AutoOS/catalog/windows.json)

### Exists only here — do not lose in migration
- LiteLLM proxy service + `litellm_config.yml` (Perplexity `sonar*` model routes).
  (sourced: infra/local-ai/docker-compose.yml, infra/local-ai/litellm_config.yml)
- `perplexity-pipefunction.py` — Open WebUI manifold pipe for Perplexity Sonar.
  (sourced: infra/local-ai/perplexity-pipefunction.py)
- `open-webui` service — chat UI bridging Ollama and LiteLLM.
  (sourced: infra/local-ai/docker-compose.yml)
- `ollama-agent` service — second Ollama sidecar with no catalog equivalent.
  (sourced: infra/local-ai/docker-compose.yml)
