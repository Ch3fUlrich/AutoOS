# Workstation CI runner image (autoos:CI-WORKST-RUNNER): the official runner plus the tools
# the AutoOS/plangraph Linux jobs expect from ubuntu-latest but the runner image lacks.
# Versions are pinned and at least two weeks old when chosen (2026-10-16).
FROM ghcr.io/actions/actions-runner:2.338.0
USER root
ARG PWSH_VERSION=7.6.6
ARG NODE_VERSION=v24.21.0
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends iproute2 shellcheck yamllint jq unzip xz-utils make ca-certificates curl \
    libicu74 libssl3 \
 && rm -rf /var/lib/apt/lists/*
# PowerShell (official release tarball)
RUN curl -fsSL -o /tmp/pwsh.tgz "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell-${PWSH_VERSION}-linux-x64.tar.gz" \
 && mkdir -p /opt/microsoft/powershell/7 && tar -xzf /tmp/pwsh.tgz -C /opt/microsoft/powershell/7 \
 && chmod +x /opt/microsoft/powershell/7/pwsh && ln -sf /opt/microsoft/powershell/7/pwsh /usr/local/bin/pwsh && rm /tmp/pwsh.tgz
# Node.js LTS (official tarball, verified against the published SHASUMS256)
RUN cd /tmp && curl -fsSLO "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" \
 && curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/SHASUMS256.txt" | grep " node-${NODE_VERSION}-linux-x64.tar.xz\$" | sha256sum -c - \
 && tar -xJf "node-${NODE_VERSION}-linux-x64.tar.xz" -C /usr/local --strip-components=1 && rm "node-${NODE_VERSION}-linux-x64.tar.xz"
USER runner
