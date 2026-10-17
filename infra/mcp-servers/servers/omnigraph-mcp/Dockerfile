# Omnigraph MCP bridge (stdio) as a container — for hosts without Node.
# The bridge (@modernrelay/omnigraph-mcp) exposes the Omnigraph HTTP API to an
# MCP client over stdio. Run one `docker run -i --rm` per session (like graphify).
#
# Build:  docker build -t omnigraph-mcp:latest infra/mcp-servers/servers/omnigraph-mcp
# Run:    docker run -i --rm --network mcp-servers_default \
#           -e OMNIGRAPH_BASE_URL=http://omnigraph-server:8080 \
#           -e OMNIGRAPH_GRAPH_ID=memory -e OMNIGRAPH_TOKEN=<token> omnigraph-mcp:latest
FROM node:22-slim
# 0.13.0 = the bridge verified against omnigraph-server v0.13.0 (D-1038
# rehearsal 2026-10-10: 15 tools, same names, sends the omnigraph-http-api: 0.13
# header itself); it declares node >= 22.
RUN npm install -g @modernrelay/omnigraph-mcp@0.13.0 \
    && npm cache clean --force
ENTRYPOINT ["omnigraph-mcp"]
