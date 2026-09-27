FROM python:3.12-slim

# `mcp` (the MCP Python SDK) must be capped below 2.0: mcp 2.0.0 split its type
# exports into a separate `mcp-types` package and dropped names servers import at
# build time. An unconstrained install is exactly what silently broke
# graphify-mcp — see servers/graphify-mcp/Dockerfile.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY homelab_mcp/ /app/homelab_mcp/

# The config is site data and is NEVER baked in. Mount it and point at it:
#   docker run -i --rm \
#     -v "$HOME/.config/homelab/homelab.yaml:/config/homelab.yaml:ro" \
#     -v "$HOME/.ssh:/root/.ssh:ro" \
#     -e HOMELAB_CONFIG=/config/homelab.yaml homelab-mcp:latest
ENV HOMELAB_CONFIG=/config/homelab.yaml
ENTRYPOINT ["python", "-m", "homelab_mcp.server"]
