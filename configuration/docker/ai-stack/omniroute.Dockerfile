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
#
# A second layer adds bcryptjs for the reset-password CLI (`docker exec -i
# autoos-omniroute node bin/reset-password.mjs --password-stdin`): the base's
# standalone build prunes it from /app/node_modules, so the CLI dies with
# ERR_MODULE_NOT_FOUND. The version is the one /app/package-lock.json pins -
# bump it with the FROM digest.
FROM diegosouzapw/omniroute:3.8.50@sha256:085c57adf499a8aaa9f35ccde95c0df9c11bd9ecd18d6c9edbf3b68b8079ba9d
USER root
RUN npm install -g @qoder-ai/qodercli@1.1.63 && npm cache clean --force
# Into a throw-away prefix, then moved: npm in /app would reconcile the whole
# package.json. bcryptjs has no dependencies, so the one package is the tree.
RUN npm install --prefix /tmp/autoos-cli-deps --ignore-scripts --no-audit --no-fund bcryptjs@3.0.3 && rm -rf /app/node_modules/bcryptjs && mv /tmp/autoos-cli-deps/node_modules/bcryptjs /app/node_modules/bcryptjs && rm -rf /tmp/autoos-cli-deps && npm cache clean --force
# The base's own user; compose.yml overrides it with the host uid:gid anyway.
USER node
