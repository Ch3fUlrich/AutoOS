#!/usr/bin/env bash
# MCP Server Stack — Graphify Project Initialization Script (Linux)
# ============================================================================
# Builds Graphify knowledge graphs (graphify-out/graph.json) for one or more
# repositories, and optionally installs the git hooks that keep them fresh.
#
# Graphify is a project graph layer, not a Serena replacement: Serena still
# handles symbol-level navigation, Graphify handles graph-level queries
# across code and docs. See mcp-servers/README.md ("Graphify + Local Ollama
# — Known Gotchas") for the reasoning behind the ollama-backend defaults
# below, and mcp-servers/servers/graphify-mcp/README.md for the --docker path.
#
# Usage:
#   bash linux/init-graphify-projects.sh [OPTIONS]
#
# Options:
#   --path PATH        Repository to initialize (default: current directory)
#   --code-root DIR     Batch-initialize every git repo directly under DIR
#   --force              Rebuild even if graphify-out/graph.json exists
#                         (also bypasses graphify's semantic cache — a full
#                         LLM extraction pass, not just a merge, can take
#                         ~1h per repo on a local 8B-class model)
#   --no-hooks           Skip installing graphify's git hooks
#   --backend NAME       Extraction backend: ollama|openai|gemini|deepseek|...
#                         (default: ollama, local, no external API key)
#   --model NAME          Backend model override (default for ollama:
#                          hermes3:8b-ctx8k — see README gotchas for why not
#                          graphify's own qwen2.5-coder:7b default)
#   --docker              Run extraction via the graphify-mcp Docker image
#                         instead of `uv run` — no host uv/Python toolchain
#                         needed. Requires the image to already be built:
#                         docker build -t graphify-mcp:latest servers/graphify-mcp
#
# Examples:
#   bash linux/init-graphify-projects.sh
#   bash linux/init-graphify-projects.sh --code-root "$HOME/code"
#   bash linux/init-graphify-projects.sh --docker --no-hooks
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_SCRIPT="$SCRIPT_DIR/../patch-graphify-ollama-bugs.py"
GRAPHIFY_OUT_REL="graphify-out/graph.json"

# graphify's own ollama default (qwen2.5-coder:7b) is code-tuned, not
# structured-output-tuned, and needs a manual `ollama pull` first since it's
# not part of any base image. hermes3:8b is closer in size but noticeably
# more reliable at emitting valid JSON for graphify's extraction schema.
DEFAULT_OLLAMA_MODEL='hermes3:8b-ctx8k'

REPO_PATH="$(pwd)"
CODE_ROOT=""
FORCE=0
INSTALL_HOOKS=1
BACKEND="ollama"
MODEL=""
USE_DOCKER=0
DOCKER_IMAGE="graphify-mcp:latest"

while [ $# -gt 0 ]; do
    case "$1" in
        --path) REPO_PATH="$2"; shift 2 ;;
        --code-root) CODE_ROOT="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        --no-hooks) INSTALL_HOOKS=0; shift ;;
        --backend) BACKEND="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --docker) USE_DOCKER=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

ensure_ollama_model() {
    local model_name="$1"
    local have
    have=$(curl -sf --max-time 5 http://localhost:11434/api/tags | python3 -c \
        "import json,sys; print('\n'.join(m['name'] for m in json.load(sys.stdin).get('models', [])))" 2>/dev/null || true)
    if grep -qxF "$model_name" <<<"$have"; then
        return 0
    fi
    echo -e "  \033[33mPulling ollama model '$model_name' (first run only, several GB)...\033[0m"
    curl -sf --max-time 1800 -X POST http://localhost:11434/api/pull \
        -H 'Content-Type: application/json' \
        -d "{\"name\":\"$model_name\",\"stream\":false}" >/dev/null
    echo -e "  \033[32mv $model_name pulled\033[0m"
}

# Runs `uv run --with <extra> graphify <args...>` normally, or the
# equivalent inside the graphify-mcp Docker image with --docker. The image's
# entrypoint is `python -m graphify.serve`, so it's overridden for these
# one-shot CLI invocations.
run_graphify() {
    if [ "$USE_DOCKER" -eq 1 ]; then
        docker run --rm -v "$(pwd):/repo" -w /repo --entrypoint python "$DOCKER_IMAGE" -m graphify "$@"
    else
        local extra="graphifyy"
        if [ "$BACKEND" = "ollama" ]; then extra="graphifyy[ollama]"; fi
        uv run --with "$extra" graphify "$@"
    fi
}

invoke_graphify() {
    local repo_path="$1" repo_name="$2"
    local graph_path="$repo_path/$GRAPHIFY_OUT_REL"

    if [ -f "$graph_path" ] && [ "$FORCE" -eq 0 ]; then
        echo -e "  \033[90m- $repo_name : graph already exists (use --force to rebuild)\033[0m"
        echo "skipped"
        return 0
    fi

    echo -e "  \033[32m- $repo_name : building graph with backend '$BACKEND'\033[0m"
    pushd "$repo_path" >/dev/null

    # graphify's extraction pipeline has known bugs when a local model
    # returns malformed JSON for a chunk (str where a dict is expected, int
    # where a string ID is expected) — patch them defensively before every
    # run. Idempotent and cheap; safe even if graphify isn't installed in
    # the uv cache yet. Only relevant to the uv path — the Docker image is
    # built fresh from a pinned PyPI release each time, so it never
    # accumulates the stale-cache state this patches.
    if [ "$USE_DOCKER" -eq 0 ] && [ -f "$PATCH_SCRIPT" ]; then
        python3 "$PATCH_SCRIPT" >/dev/null || true
    fi

    # Ensure graphify respects .gitignore and ignores its own output.
    {
        [ -f .gitignore ] && cat .gitignore
        echo 'graphify-out/'
        echo 'GRAPH_*.html'
    } > .graphifyignore

    local extract_args=(extract . --backend "$BACKEND" --no-viz)
    if [ "$BACKEND" = "ollama" ]; then
        [ -n "$MODEL" ] || MODEL="$DEFAULT_OLLAMA_MODEL"
        # Local single-GPU ollama serves one request at a time — concurrency
        # > 1 just queues and adds contention. A ~6000 token-budget keeps
        # chunks small enough for reliable JSON from an 8B model. The
        # 5-minute client default timeout is too short: legitimate
        # generations get killed mid-flight and retried from scratch,
        # wasting far more time than a longer timeout costs.
        extract_args=(extract . --backend "$BACKEND" --model "$MODEL"
            --token-budget 6000 --max-concurrency 1 --api-timeout 1200 --no-viz)
    fi
    [ "$FORCE" -eq 1 ] && extract_args+=(--force)

    if ! run_graphify "${extract_args[@]}"; then
        popd >/dev/null
        echo "error"
        return 0
    fi

    echo -e "    \033[90mGenerating D3 collapsible tree HTML...\033[0m"
    if ! run_graphify tree --graph "$graph_path" --output "$repo_path/graphify-out/GRAPH_TREE.html"; then
        popd >/dev/null
        echo "error"
        return 0
    fi

    if [ "$INSTALL_HOOKS" -eq 1 ]; then
        if ! run_graphify hook install; then
            popd >/dev/null
            echo "error"
            return 0
        fi
    fi

    popd >/dev/null

    if [ ! -f "$graph_path" ]; then
        echo -e "  \033[31mX expected graph not found at $graph_path\033[0m" >&2
        echo "error"
        return 0
    fi

    echo -e "    \033[32mv graphify-out/graph.json ready\033[0m"
    echo "built"
}

echo -e "\033[36m======================================================================\033[0m"
echo -e "\033[36m  Graphify — Initialize Repository Graphs (Linux)\033[0m"
echo -e "\033[36m======================================================================\033[0m"

if [ "$USE_DOCKER" -eq 1 ]; then
    if ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
        echo -e "\033[31mX Docker image '$DOCKER_IMAGE' not found. Build it first:\033[0m" >&2
        echo "    docker build -t $DOCKER_IMAGE servers/graphify-mcp" >&2
        exit 1
    fi
fi

if [ "$BACKEND" = "ollama" ]; then
    if ! curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null; then
        echo -e "\033[31mX Ollama is not responding on :11434. Start it before building local graphs.\033[0m" >&2
        exit 1
    fi
    [ -n "$MODEL" ] || MODEL="$DEFAULT_OLLAMA_MODEL"
    ensure_ollama_model "$MODEL"
fi

TARGETS=()
if [ -n "$CODE_ROOT" ]; then
    [ -d "$CODE_ROOT" ] || { echo -e "\033[31mX CodeRoot not found: $CODE_ROOT\033[0m" >&2; exit 1; }
    for d in "$CODE_ROOT"/*/; do
        [ -d "${d}.git" ] && TARGETS+=("${d%/}")
    done
    echo "Batch mode: ${#TARGETS[@]} git repo(s) under $CODE_ROOT"
    echo ""
else
    RESOLVED="$(cd "$REPO_PATH" && pwd)"
    TARGETS=("$RESOLVED")
    echo "Single repo: $RESOLVED"
    echo ""
fi

BUILT=0
SKIPPED=0
ERRORS=0
for target in "${TARGETS[@]}"; do
    status=$(invoke_graphify "$target" "$(basename "$target")")
    case "$status" in
        built) BUILT=$((BUILT + 1)) ;;
        skipped) SKIPPED=$((SKIPPED + 1)) ;;
        error) ERRORS=$((ERRORS + 1)) ;;
    esac
done

echo ""
echo -e "\033[36m----------------------------------------------------------------------\033[0m"
echo -e "\033[36m  built=$BUILT  skipped=$SKIPPED  errors=$ERRORS\033[0m"
echo -e "\033[36m======================================================================\033[0m"

[ "$ERRORS" -eq 0 ]
