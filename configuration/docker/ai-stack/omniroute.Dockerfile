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
FROM diegosouzapw/omniroute:3.8.50@sha256:085c57adf499a8aaa9f35ccde95c0df9c11bd9ecd18d6c9edbf3b68b8079ba9d AS base
USER root
RUN npm install -g @qoder-ai/qodercli@1.1.63 && npm cache clean --force
# Into a throw-away prefix, then moved: npm in /app would reconcile the whole
# package.json. bcryptjs has no dependencies, so the one package is the tree.
RUN npm install --prefix /tmp/autoos-cli-deps --ignore-scripts --no-audit --no-fund bcryptjs@3.0.3 && rm -rf /app/node_modules/bcryptjs && mv /tmp/autoos-cli-deps/node_modules/bcryptjs /app/node_modules/bcryptjs && rm -rf /tmp/autoos-cli-deps && npm cache clean --force
# The base's own user; compose.yml overrides it with the host uid:gid anyway.
USER node

# U2 (autoos3, D-626): Vertex "Requests ending with a model turn are not supported" (400).
# tools/apply-vertex-patch.py (reviewed, lane F1-vertex) strips a trailing role:"model"
# content at the 12 mergeConsecutiveSameRoleContents call sites in 6 compiled chunks.
# The runtime image has a read-only rootfs and no python, so the patch runs at BUILD time
# in a python stage over a copy of the chunks, which are copied back. The build FAILS
# unless the first run reports exactly 12 patched / 0 skipped / 0 errors and a second run
# 0 patched / 12 skipped (idempotent) - chunk names are build-specific, so a FROM bump
# that renames them breaks the build instead of shipping an unpatched gateway.
FROM python:3.12-slim-bookworm@sha256:7753c33391fc9f01d1984375bf375eb6686d52ba10db6043a86634a5ccf90dcf AS vertex-patch
COPY --from=base /app/.build/next/server/chunks /chunks
COPY --from=tools apply-vertex-patch.py /apply-vertex-patch.py
# set -e and no pipes (/bin/sh has no pipefail): every step must succeed, the patcher's
# exit code included, or the build stops.
RUN set -e; \
    python3 /apply-vertex-patch.py /chunks > /run1.txt || { cat /run1.txt; exit 1; }; cat /run1.txt; \
    grep -qx 'Done: 12 patched, 0 skipped, 0 errors' /run1.txt; \
    python3 /apply-vertex-patch.py /chunks > /run2.txt || { cat /run2.txt; exit 1; }; cat /run2.txt; \
    grep -qx 'Done: 0 patched, 12 skipped, 0 errors' /run2.txt; \
    rm -f /chunks/*.autoos-backup-*

FROM base
COPY --from=vertex-patch /chunks/ /app/.build/next/server/chunks/
