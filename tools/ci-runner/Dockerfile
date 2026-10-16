# Workstation CI runner image (autoos:CI-WORKST-RUNNER): the official runner plus the tools
# the AutoOS/plangraph Linux jobs expect from ubuntu-latest but the runner image lacks.
# Versions are pinned and at least two weeks old when chosen (2026-10-16).
# Image tag: workst-runner:2.338.0-4
FROM ghcr.io/actions/actions-runner:2.338.0
USER root
ARG PWSH_VERSION=7.6.6
ARG NODE_VERSION=v24.21.0
# fonts-dejavu-core/fonts-liberation are the Cyrillic-capable TTFs the lab PDF tests need;
# python3-venv/python3-dev/build-essential the python and C toolchain of the jobs.
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends iproute2 shellcheck yamllint jq unzip xz-utils make ca-certificates curl \
    libicu74 libssl3 fonts-dejavu-core fonts-liberation python3-venv python3-dev build-essential \
 && rm -rf /var/lib/apt/lists/*
# PowerShell (official release tarball)
RUN curl -fsSL -o /tmp/pwsh.tgz "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell-${PWSH_VERSION}-linux-x64.tar.gz" \
 && mkdir -p /opt/microsoft/powershell/7 && tar -xzf /tmp/pwsh.tgz -C /opt/microsoft/powershell/7 \
 && chmod +x /opt/microsoft/powershell/7/pwsh && ln -sf /opt/microsoft/powershell/7/pwsh /usr/local/bin/pwsh && rm /tmp/pwsh.tgz
# Node.js LTS (official tarball, verified against the published SHASUMS256)
RUN cd /tmp && curl -fsSLO "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" \
 && curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/SHASUMS256.txt" | grep " node-${NODE_VERSION}-linux-x64.tar.xz\$" | sha256sum -c - \
 && tar -xJf "node-${NODE_VERSION}-linux-x64.tar.xz" -C /usr/local --strip-components=1 && rm "node-${NODE_VERSION}-linux-x64.tar.xz"
# Temporal CLI v1.9.1 (release tarball, verified against the published sha256)
RUN cd /tmp \
 && curl -fsSL -o temporal_cli_1.9.1_linux_amd64.tar.gz "https://github.com/temporalio/cli/releases/download/v1.9.1/temporal_cli_1.9.1_linux_amd64.tar.gz" \
 && echo "09a0326a51db84d02735e53542b9ebd8c4758daf47482a9ab0abce15844e60d5  temporal_cli_1.9.1_linux_amd64.tar.gz" | sha256sum -c - \
 && tar -xzf temporal_cli_1.9.1_linux_amd64.tar.gz temporal \
 && install -m 0755 temporal /usr/local/bin/temporal \
 && rm -f temporal_cli_1.9.1_linux_amd64.tar.gz temporal
# sccache v0.18.0 (musl release tarball; the release's .sha256 asset holds just the hash,
# so build the '<hash>  <file>' line sha256sum expects)
RUN cd /tmp \
 && curl -fsSL -o sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz "https://github.com/mozilla/sccache/releases/download/v0.18.0/sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz" \
 && curl -fsSL -o sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz.sha256 "https://github.com/mozilla/sccache/releases/download/v0.18.0/sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz.sha256" \
 && printf '%s  %s\n' "$(tr -d '[:space:]' < sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz.sha256)" "sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz" | sha256sum -c - \
 && tar -xzf sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz \
 && install -m 0755 sccache-v0.18.0-x86_64-unknown-linux-musl/sccache /usr/local/bin/sccache \
 && rm -rf sccache-v0.18.0-x86_64-unknown-linux-musl sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz sccache-v0.18.0-x86_64-unknown-linux-musl.tar.gz.sha256
# Cache dirs created as root and chowned to runner: when a fresh named volume is mounted
# on one of these paths Docker copies the directory (owner included), so the runner user
# owns the cargo registry/git and the sccache cache from the very first job on.
RUN mkdir -p /home/runner/.cargo/registry /home/runner/.cargo/git /home/runner/.cache/sccache \
 && chown -R runner:runner /home/runner/.cargo /home/runner/.cache
USER runner
# Rust for the runner user: rustup, minimal profile, the stable toolchain - downloaded
# once and then shared through the ci-cargo-* volumes, compiled through sccache.
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
ENV PATH=/home/runner/.cargo/bin:$PATH
