# opencode serve for the AutoOS docker AI stack (compose.yml, service opencode).
#
# The upstream image is V2 (@opencode/cli, `opencode --version` -> v2.0.16;
# the legacy V1 npm package cannot read the V2 providers config) but is
# bare Alpine: no git (an agent could not commit), no bash, no node/npx and no
# uv/uvx (the MCP servers the rendered config starts). Add exactly those, plus
# curl for the healthcheck. Pinned by digest; bump both lines together.
FROM ghcr.io/anomalyco/opencode:2.0.19@sha256:998556e380e40c32f7be2a31a65fc75bc25858069135a492d3f8e5ac15ce83b7
RUN apk add --no-cache git bash nodejs npm uv python3 curl openssh-client-default less
