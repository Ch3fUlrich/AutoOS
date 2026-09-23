# Omnigraph MCP bridge (stdio) as a container — for hosts without Node.
# The bridge (@modernrelay/omnigraph-mcp) exposes the Omnigraph HTTP API to an
# MCP client over stdio. Run one `docker run -i --rm` per session (like graphify).
#
# Build:  docker build -t omnigraph-mcp:latest infra/mcp-servers/servers/omnigraph-mcp
# Run:    docker run -i --rm --network mcp-server_mcp-net \
#           -e OMNIGRAPH_BASE_URL=http://omnigraph-server:8080 \
#           -e OMNIGRAPH_GRAPH_ID=memory -e OMNIGRAPH_TOKEN=<token> omnigraph-mcp:latest
FROM node:20-slim
RUN npm install -g @modernrelay/omnigraph-mcp@0.8.0 \
    && npm cache clean --force
ENTRYPOINT ["omnigraph-mcp"]
