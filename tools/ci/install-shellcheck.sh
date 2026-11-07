#!/usr/bin/env bash
# Install the verified CI linter without changing the runner's system packages.
set -euo pipefail

SHELLCHECK_VERSION=0.11.0
SHELLCHECK_SHA256=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198

: "${RUNNER_TEMP:?RUNNER_TEMP must be set}"
: "${GITHUB_PATH:?GITHUB_PATH must be set}"
install_dir="$(mktemp -d "$RUNNER_TEMP/shellcheck.XXXXXX")"
archive="$install_dir/shellcheck.tar.xz"
bin_dir="$install_dir/bin"
curl -fsSL -o "$archive" "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.xz"
printf '%s  %s\n' "$SHELLCHECK_SHA256" "$archive" | sha256sum -c -
tar -xJf "$archive" -C "$install_dir"
mkdir -p "$bin_dir"
install -m 0755 "$install_dir/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "$bin_dir/shellcheck"
printf '%s\n' "$bin_dir" >> "$GITHUB_PATH"
export PATH="$bin_dir:$PATH"
shellcheck --version
