# OmniRoute gateway for the AutoOS docker AI stack (compose.yml, service omniroute).
#
# Why a local layer: the Qoder PAT login (dashboard -> Providers -> Qoder) is
# driven through the `qodercli` binary inside the gateway container, and the
# upstream image ships none - the login fails with `spawn qodercli ENOENT`.
# This adds exactly that binary and nothing else. No vendor binary is kept in
# git: npm fetches the package at build time (AGENTS.md rule 2). npm 12 in the
# base blocks install scripts by default, so nothing from the package runs at
# build either.
#
# The FROM digest and the qodercli version are bumped together, and with them
# the local tag in compose.yml (autoos/omniroute:<upstream>-autoos<n>; see
# docs/web-services.md, "Bumping an image"). The qodercli version is the one
# the host runs (`qodercli --version`).
FROM diegosouzapw/omniroute:3.8.50@sha256:085c57adf499a8aaa9f35ccde95c0df9c11bd9ecd18d6c9edbf3b68b8079ba9d
USER root
RUN npm install -g @qoder-ai/qodercli@1.1.63 && npm cache clean --force
# The base's own user; compose.yml overrides it with the host uid:gid anyway.
USER node
