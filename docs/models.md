<!-- AUTOOS-MANAGED-START models-doc -->
_Generated from `catalog/ai-registry.json` — do not edit by hand. Run `python3 tools/registry.py render models-doc --check` after a registry change; if it fails, run `python3 tools/registry.py render models-doc` and replace the text between the two `AUTOOS-MANAGED-START/END models-doc` markers below with its output._

| Route | Class | Context | Legs |
|---|---|---|---|
| `auto` | mid | 131,072 | (none) |
| `auto/cheap` | cheap | 131,072 | (none) |
| `auto/smart` | mid | 131,072 | (none) |
| `cheaperinference/glm-5.2` | cheap | 128k | ~~cheaperinference `glm-5.2`~~ (unavailable) |
| `cheaperinference/kimi-k3` | cheap | 128k | ~~cheaperinference `kimi-k3`~~ (unavailable) |
| `deepseek-v4.1-flash` | cheap | 128k | ~~deepseek `deepseek-flash`~~ (unavailable) → ~~opencode-zen `deepseek-v4.1-flash`~~ (unavailable) |
| `gemini-3.8-flash` | cheap | 128k | gemini `gemini-3.8-flash` → ~~openrouter `google/gemini-3.8-flash`~~ (unavailable) |
| `opus-4-6` | frontier | 200k | antigravity `claude-opus-4-6-thinking` → ~~cc `claude-opus-4-6`~~ (unavailable) |
| `samba/MiniMax-M3` | cheap | 128k | ~~samba `MiniMax-M3`~~ (unavailable) |
| `samba/gpt-oss-120b` | cheap | 128k | ~~samba `gpt-oss-120b`~~ (unavailable) |
| `spark-1.3-contributor` | cheap | 1M | meta_api `muse-spark-1.3-contributor` → ~~opencode-zen `muse-spark-1.3-contributor-free`~~ (unavailable) → ~~openrouter `meta/muse-spark-1.3-contributor`~~ (unavailable) |
| `t1-orchestrator` | cheap | 1M | meta_api `muse-spark-1.3-contributor` → ~~opencode-zen `muse-spark-1.3-contributor-free`~~ (unavailable) → ~~openrouter `meta/muse-spark-1.3-contributor`~~ (unavailable) → gemini `gemini-3.8-flash` |
| `t1-orchestrator-clean` | cheap | 1M | ~~openrouter `meta/muse-spark-1.3-contributor`~~ (unavailable) |
| `t1-orchestrator-free-only` | free | 1M | ~~opencode-zen `muse-spark-1.3-contributor-free`~~ (unavailable) → gemini `gemini-3.8-flash` |
| `t1-orchestrator-paid` | cheap | 1M | meta_api `muse-spark-1.3-contributor` |
| `t2-orchestrator` | frontier | 200k | antigravity `claude-opus-4-6-thinking` → ~~cc `claude-opus-4-6`~~ (unavailable) → ~~openrouter `deepseek/deepseek-v4.1-flash`~~ (unavailable) |
| `t2-worker` | mid | 128k | gemini `gemini-3.8-flash` → antigravity `gemini-3.7-flash-high` → meta_api `muse-spark-1.3-contributor` → ~~groq `openai/gpt-oss-120b`~~ (unavailable) → ~~cerebras `gpt-oss-120b`~~ (unavailable) → ~~sambanova `gpt-oss-120b`~~ (unavailable) → ~~openrouter `openai/gpt-oss-120b`~~ (unavailable) → ~~cheaperinference `deepseek-v4-flash`~~ (unavailable) → ~~cheaperinference `glm-4.5-air`~~ (unavailable) → ~~cheaperinference `kimi-k3`~~ (unavailable) → ~~openrouter `deepseek/deepseek-v4.1-flash`~~ (unavailable) → ~~deepseek `deepseek-flash`~~ (unavailable) → ~~opencode-zen `deepseek-v4.1-flash`~~ (unavailable) |
| `t2-worker-clean` | mid | 128k | ~~deepseek `deepseek-flash`~~ (unavailable) → ~~openrouter `deepseek/deepseek-v4.1-flash`~~ (unavailable) → ~~opencode-zen `deepseek-v4.1-flash`~~ (unavailable) → mistral `mistral-small-latest` |
| `t2-worker-free-only` | free | 128k | gemini `gemini-3.8-flash` → antigravity `gemini-3.7-flash-medium` → ~~groq `openai/gpt-oss-120b`~~ (unavailable) → ~~cerebras `gpt-oss-120b`~~ (unavailable) → ~~sambanova `gpt-oss-120b`~~ (unavailable) → free_ai `qwen7b` |
| `t2-worker-paid` | mid | 131,072 | (none) |
| `t3-driver` | cheap | 128k | mistral `mistral-code-latest` → meta_api `muse-spark-1.3-contributor` → ~~groq `qwen/qwen3.8-27b`~~ (unavailable) → ~~samba `gpt-oss-120b`~~ (unavailable) → ~~cheaperinference `glm-5.2`~~ (unavailable) → ~~deepseek `deepseek-flash`~~ (unavailable) → ~~cheaperinference `kimi-k3`~~ (unavailable) → ~~samba `MiniMax-M3`~~ (unavailable) → ~~cerebras `qwen-3.8-27b`~~ (unavailable) → ~~cheaperinference `glm-4.5-air`~~ (unavailable) → ~~cheaperinference `minimax-m2.7`~~ (unavailable) → mistral `mistral-small-latest` → ~~opencode-zen `deepseek-v4.1-flash`~~ (unavailable) |
| `t3-driver-clean` | cheap | 128k | ~~deepseek `deepseek-flash`~~ (unavailable) → mistral `mistral-small-latest` → ~~opencode-zen `deepseek-v4.1-flash`~~ (unavailable) |
| `t3-driver-free-only` | free | 128k | ~~groq `qwen/qwen3.8-27b`~~ (unavailable) → ~~cerebras `qwen-3.8-27b`~~ (unavailable) → free_ai `qwen7b` |
| `t3-driver-paid` | cheap | 131,072 | (none) |
| `t4-rag` | cheap | 128k | cohere `command-a-03-2025` → cohere `command-r-plus-08-2024` |
<!-- AUTOOS-MANAGED-END models-doc -->
