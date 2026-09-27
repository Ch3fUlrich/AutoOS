FROM python:3.12-slim

# graphify's own extractors need no compiler (tree-sitter ships prebuilt
# wheels), so this stays a single-stage slim image.
#
# PINNED deliberately. The AST cache is namespaced by version
# (graphify-out/cache/ast/v<version>/), so an unpinned rebuild silently upgrades
# graphify and invalidates every repo's cache at once — a cold re-extraction of
# every project, triggered by an unrelated image rebuild.  Bump this on purpose,
# not by accident.
#
# The `openai` extra is what lets semantic extraction of docs/papers/images run
# against an OpenAI-compatible endpoint.  Without it `--backend openai` fails at
# call time with "the 'openai' package is required for this backend", after the
# AST pass has already succeeded — so the graph looks built while every
# non-code file is silently missing from it.  Point it at the homelab LiteLLM:
#   OPENAI_BASE_URL=http://192.0.2.10:4000/v1
#   OPENAI_MODEL=deepseek-v4-flash
#   OPENAI_API_KEY=$LITELLM_MASTER_KEY
# which also satisfies the standing rule that LLM calls go through LiteLLM
# rather than a provider SDK.
# `mcp` (the MCP Python SDK) is an UNpinned transitive of graphifyy[mcp]. mcp 2.0.0
# split its type exports into a separate `mcp-types` package and dropped `AnyUrl`
# from `mcp.types`, which graphify 0.9.20 imports at server-build time
# (serve.py: `from mcp.types import AnyUrl`). An unconstrained install therefore
# pulls mcp 2.x and the server crashes on startup with
#   ImportError: cannot import name 'AnyUrl' from 'mcp.types'
# which reaches Claude Code as graphify "Failed to connect — Connection closed".
# Cap below 2.0 (1.29.0 is the tested-good 1.x). Revisit when bumping graphifyy.
RUN pip install --no-cache-dir "graphifyy[mcp,openai]==0.9.20" "mcp<2"

WORKDIR /repo

# graphify.serve defaults to stdio, so this container is normally run with
# `docker run -i` and the repo bind-mounted at /repo rather than as a
# long-running compose service.  (0.9.20 does also support
# `--transport http`, contrary to older notes here; stdio remains the default
# because one cwd-relative user-scope entry then serves whichever repo Claude
# Code was started in.)
ENTRYPOINT ["python", "-m", "graphify.serve"]
CMD ["graphify-out/graph.json"]
